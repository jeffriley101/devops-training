"""Minimal S2 controls. No roster, reporting, game, or consumer billing surface."""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError

from . import classroom, classroom_s2 as service
from .db import SessionLocal
from .login_limits import enforce_login_limit
from .membership_routes import PrivateRoute
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token, require_site_admin
from .verifier_routes import current_verifier

router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _enabled():
    if not service.enabled():
        raise HTTPException(404, "Classroom controls unavailable.")


async def _form(request):
    _enabled()
    if not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.")
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    if len(form.multi_items()) != len(form):
        raise HTTPException(400, "Duplicate fields are not supported.")
    return form


def _integer(form, key):
    value = form.get(key, "")
    if not isinstance(value, str) or not value.isascii() or not 1 <= len(value) <= 10 or not value.isdecimal() or not 1 <= int(value) <= 2147483647:
        raise HTTPException(400, "A valid scoped identifier is required.")
    return int(value)


def _exact(form, allowed):
    if set(form) - set(allowed) - {"csrf"}:
        raise HTTPException(400, "Unsupported fields.")


def _page(request, *, mode, result=None):
    return templates.TemplateResponse(request=request, name="classroom_s2.html",
        context={"title": "Classroom controls", "csrf": csrf_token(request), "mode": mode, "result": result})


@router.get("/classroom/manage")
def controls(request: Request):
    _enabled()
    with SessionLocal() as session:
        if current_verifier(request, session) is None:
            raise HTTPException(401, "Adult sign-in is required.")
    return _page(request, mode="adult")


@router.get("/classroom/entry")
def entry(request: Request):
    _enabled()
    with SessionLocal() as session:
        try:
            service.student_from_request(session, request)
        except classroom.ClassroomDenied as error:
            raise HTTPException(401, str(error)) from error
    return _page(request, mode="student")


@router.get("/admin/classroom/entitlement")
def internal_entitlement(request: Request):
    _enabled()
    require_site_admin(request)
    return _page(request, mode="admin")


@router.post("/admin/classroom/entitlement")
async def change_entitlement(request: Request):
    require_site_admin(request)
    form = await _form(request)
    _exact(form, {"program_id", "starts_at", "ends_at", "class_limit", "teacher_limit", "provenance", "status"})
    try:
        starts_at, ends_at = (datetime.fromisoformat(str(form[k])) for k in ("starts_at", "ends_at"))
        limits = [int(str(form[k])) for k in ("class_limit", "teacher_limit")]
    except (KeyError, ValueError, TypeError) as error:
        raise HTTPException(400, "Enter a finite period with UTC offsets and integer allowances.") from error
    with SessionLocal() as session:
        try:
            row = service.configure_institutional(session, admin=service.site_admin_authentication(request),
                program_id=_integer(form, "program_id"), starts_at=starts_at, ends_at=ends_at,
                class_limit=limits[0], teacher_limit=limits[1], provenance=form.get("provenance"),
                status=form.get("status", "active"))
            session.commit()
            result = {"entitlement_id": row.id, "status": row.status}
        except (classroom.ClassroomDenied, IntegrityError) as error:
            session.rollback()
            raise HTTPException(409, "Institutional entitlement change refused.") from error
    return _page(request, mode="admin", result=result)


@router.post("/classroom/manage/{action}")
async def manage(request: Request, action: str):
    form = await _form(request)
    fields = {
        "trial": set(), "create": {"display_name"}, "archive": {"class_id"},
        "reactivate": {"class_id"}, "open": {"class_id"}, "close": {"class_id"},
        "code": {"class_id", "preferred"}, "suspend": {"class_id", "membership_id", "reason"},
        "remove": {"class_id", "membership_id", "reason"}, "reinstate": {"class_id", "membership_id"},
        "teach": {"class_id", "verifier_id"}, "end-teaching": {"assignment_id"},
    }
    if action not in fields:
        raise HTTPException(404, "Control unavailable.")
    _exact(form, fields[action] | {"program_id", "email", "pin"})
    email, pin = str(form.get("email", "")), str(form.get("pin", ""))
    enforce_login_limit(request, "verifier", email)
    program_id = _integer(form, "program_id")
    with SessionLocal() as session:
        try:
            actor = classroom.authenticate_adult(session, email=email, pin=pin)
            args = {"actor": actor, "program_id": program_id}
            if "class_id" in fields[action]:
                args["class_id"] = _integer(form, "class_id")
            if "membership_id" in fields[action]:
                args["membership_id"] = _integer(form, "membership_id")
            result = {"action": action, "program_id": program_id}
            if action == "trial":
                row = service.start_trial(session, **args)
                result.update(entitlement_id=row.id, ends_at=row.ends_at.isoformat())
            elif action == "create":
                row = service.create_class(session, **args, display_name=form.get("display_name"))
                result["class_id"] = row.id
            elif action in {"archive", "reactivate"}:
                service.set_class_state(session, **args, state="archived" if action == "archive" else "active")
            elif action in {"open", "close"}:
                service.set_enrollment(session, **args, open=action == "open")
            elif action == "code":
                row, visible = service.issue_code(session, **args, preferred=form.get("preferred") or None)
                result.update(class_id=row.class_id, generation=row.generation, code=visible)
            elif action in {"suspend", "remove"}:
                service.suspend_member(session, **args, reason=form.get("reason", "conduct"), remove=action == "remove")
            elif action == "reinstate":
                service.reinstate_member(session, **args)
            elif action == "teach":
                row = service.assign_teacher(session, **args, verifier_id=_integer(form, "verifier_id"))
                result["assignment_id"] = row.id
            else:
                service.end_teaching(session, **args, assignment_id=_integer(form, "assignment_id"))
            session.commit()
        except classroom.ClassroomDenied as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(400, "Valid adult credentials are required.") from error
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "Classroom change conflicted. Reload and retry.") from error
    return _page(request, mode="adult", result=result)


@router.post("/classroom/entry/{action}")
async def student_entry(request: Request, action: str):
    form = await _form(request)
    fields = {"join": {"code"}, "prepare": {"code"}, "finish": {"intent"}, "leave": {"class_id"}}
    if action not in fields:
        raise HTTPException(404, "Entry action unavailable.")
    _exact(form, fields[action] | {"program_id"})
    with SessionLocal() as session:
        try:
            student = service.student_from_request(session, request)
            enforce_login_limit(request, "student", str(student.profile_id))
            args = {"student": student, "program_id": _integer(form, "program_id")}
            if action == "prepare":
                intent = service.prepare_entry(session, **args, code=form.get("code"))
                result = {"intent": intent, "message": "Complete existing account authorization, then finish entry within 30 minutes. A current code can be entered again without repeating completed authorization."}
            else:
                if action == "leave":
                    row = service.leave_class(session, **args, class_id=_integer(form, "class_id"))
                elif action == "finish":
                    row = service.join_class(session, **args, intent=form.get("intent"))
                else:
                    row = service.join_class(session, **args, code=form.get("code"))
                result = {"action": action, "class_id": row.class_id, "membership_id": row.id}
            session.commit()
        except classroom.ClassroomDenied as error:
            session.rollback()
            raise HTTPException(409, str(error)) from error
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(409, "Class entry conflicted. Reload and retry.") from error
    return _page(request, mode="student", result=result)
