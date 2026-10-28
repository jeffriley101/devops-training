"""Opt-in Classroom director metadata views; no student reporting."""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import classroom, classroom_s2
from .db import SessionLocal
from .login_limits import enforce_login_limit
from .membership_routes import PrivateRoute
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token
from .verifier_routes import current_verifier

router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def enabled():
    """A new restriction on top of the existing S1 and S2 dependencies."""
    return os.getenv("CLASSROOM_DIRECTOR_ENABLED") == "1" and classroom_s2.enabled()


def _require_enabled():
    if not enabled():
        raise HTTPException(404, "Classroom unavailable.")


def _render(request, verifier, *, program=None, selected_class=None, programs=None, shared_charts=None):
    return templates.TemplateResponse(request=request, name="classroom_director.html", context={
        "title": "Classroom", "verifier_name": verifier.display_name,
        "csrf": csrf_token(request), "program": program, "selected_class": selected_class,
        "programs": programs, "page_class": "classroom-director-page",
        "shared_charts": shared_charts,
    })


def _verifier(request, session):
    verifier = current_verifier(request, session)
    if verifier is None:
        raise HTTPException(401, "Adult sign-in is required.")
    return verifier


def _visible_programs(session, actor, *, program_id=None, class_id=None):
    result = classroom.my_classes(session, actor=actor, program_id=program_id, class_id=class_id)
    return [program for program in result.get("programs", ())
            if classroom_s2.entitlement_decision(session, program["program_id"]).allowed]


async def _authenticate_form(request, session, *, fields=()):
    if not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.")
    form = await request.form()
    if len(form.multi_items()) != len(form) or set(form) != {"csrf", "pin", *fields}:
        raise HTTPException(400, "Unsupported fields.")
    check_csrf(request, form.get("csrf"))
    verifier = _verifier(request, session)
    pin = form.get("pin")
    if not isinstance(pin, str) or len(pin) != 4:
        raise HTTPException(400, "Enter your four-digit PIN.")
    enforce_login_limit(request, "verifier", verifier.email)
    try:
        actor = classroom.authenticate_adult(session, email=verifier.email, pin=pin)
    except (classroom.ClassroomDenied, ValueError) as error:
        raise HTTPException(401, "Verifier PIN was not recognized.") from error
    if classroom._actor(session, actor) != verifier.id:
        raise HTTPException(401, "Adult sign-in is required.")
    return verifier, actor


@router.get("/classroom/director")
def home(request: Request):
    _require_enabled()
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        if verifier is None:
            return RedirectResponse("/trusted-verifiers/login", status_code=303)
        return _render(request, verifier)


@router.post("/classroom/director")
async def home_access(request: Request):
    _require_enabled()
    with SessionLocal() as session:
        verifier, actor = await _authenticate_form(request, session)
        try:
            programs = _visible_programs(session, actor)
        except classroom.ClassroomDenied as error:
            raise HTTPException(404, "Classroom unavailable.") from error
        return _render(request, verifier, programs=programs)


@router.get("/classroom/director/{program_id}/classes/{class_id}")
def class_entry(request: Request, program_id: int, class_id: int):
    _require_enabled()
    if program_id <= 0 or class_id <= 0:
        raise HTTPException(404, "Classroom unavailable.")
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        if verifier is None:
            return RedirectResponse("/trusted-verifiers/login", status_code=303)
        # Credentials and exact Program/Class authority are checked on POST.
        return _render(request, verifier, selected_class={"confirm": True})


@router.post("/classroom/director/{program_id}/classes/{class_id}")
async def class_access(request: Request, program_id: int, class_id: int):
    _require_enabled()
    if program_id <= 0 or class_id <= 0:
        raise HTTPException(404, "Classroom unavailable.")
    with SessionLocal() as session:
        verifier, actor = await _authenticate_form(request, session)
        try:
            programs = _visible_programs(session, actor, program_id=program_id, class_id=class_id)
        except classroom.ClassroomDenied as error:
            raise HTTPException(404, "Classroom unavailable.") from error
        if len(programs) != 1 or len(programs[0]["classes"]) != 1:
            raise HTTPException(404, "Classroom unavailable.")
        from . import classroom_chart_sharing
        try:
            shared = classroom_chart_sharing.shared_charts(session, actor=actor, program_id=program_id,
                                                          class_id=class_id)
        except classroom.ClassroomDenied:
            shared = []
        return _render(request, verifier, program=programs[0], selected_class=programs[0]["classes"][0],
                       shared_charts=shared)
