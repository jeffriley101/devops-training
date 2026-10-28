"""Own student reporting choice only; deliberately no director data endpoint."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select, union

from . import classroom, classroom_s2 as s2, classroom_progress_authorization as service
from .classroom_director import templates
from .classroom_models import (
    ClassroomClass, ClassroomStudentMembership, ClassroomTeachingAssignment,
    ClassroomProgressAuthorization as Authorization,
)
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .models import TrustedVerifier
from .session_config import session_secret
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token

router = APIRouter(route_class=PrivateRoute)
PATH = "/classroom/reporting"


def _enabled():
    if not service.enabled():
        raise HTTPException(404, service.UNAVAILABLE)


def _student(session, request):
    try:
        return s2.student_from_request(session, request)
    except classroom.ClassroomDenied as error:
        raise HTTPException(401, "Student sign-in is required.") from error


def _signer():
    return URLSafeTimedSerializer(session_secret(), salt="classroom-progress-notice-v1")


def _rows(request):
    with SessionLocal() as session:
        proof = _student(session, request)
        s2._student(session, proof, require_eligibility=False)
        # Own retained enrollment/authorization references, never invitations or a roster.
        scopes = session.execute(union(
            select(ClassroomClass.program_id, ClassroomClass.id).join(
                ClassroomStudentMembership, ClassroomStudentMembership.class_id == ClassroomClass.id)
                .where(ClassroomStudentMembership.profile_id == proof.profile_id),
            select(Authorization.program_id, Authorization.class_id)
                .where(Authorization.profile_id == proof.profile_id))).all()
    rows = []
    for program_id, class_id in sorted(scopes):
        # Match Program-first lock discipline without spanning Programs in one transaction.
        with SessionLocal() as session:
            proof = _student(session, request)
            choice = service.choice(session, student=proof, program_id=program_id, class_id=class_id)
            if not choice.eligible and not choice.saved:
                continue
            names = list(session.scalars(select(TrustedVerifier.display_name).join(
                ClassroomTeachingAssignment, ClassroomTeachingAssignment.verifier_id == TrustedVerifier.id)
                .where(ClassroomTeachingAssignment.id.in_(choice.assignment_ids))
                .order_by(ClassroomTeachingAssignment.id))) if choice.eligible else []
            token = _signer().dumps([proof.profile_id, proof.session_version, program_id, class_id,
                service.NOTICE_VERSION, list(choice.assignment_ids), list(choice.revision),
                choice.source_revision]) if choice.eligible else None
            rows.append(dict(program_id=program_id, class_id=class_id,
                name=session.get(ClassroomClass, class_id).display_name,
                choice=choice, directors=names, notice_token=token))
    return rows


@router.get(PATH)
def page(request: Request):
    _enabled()
    try:
        rows = _rows(request)
    except classroom.ClassroomDenied as error:
        raise HTTPException(404, service.UNAVAILABLE) from error
    return templates.TemplateResponse(request=request, name="classroom_reporting.html", context={
        "title": "Classroom reporting", "page_class": "classroom-director-page",
        "csrf": csrf_token(request), "classes": rows, "label": service.LABEL, "notice": service.NOTICE,
        "message": request.session.pop("classroom_reporting_message", None)})


async def _form(request, fields):
    _enabled()
    if not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.")
    form = await request.form()
    if (len(form.multi_items()) != len(form) or set(form) != {"csrf", *fields}
            or any(not isinstance(value, str) or len(value) > 2000 for value in form.values())):
        raise HTTPException(400, "Reload the page and explicitly select your reporting choice.")
    check_csrf(request, form.get("csrf"))
    return form


@router.post(PATH + "/{program_id}/classes/{class_id}/authorize")
async def authorize(request: Request, program_id: int, class_id: int):
    form = await _form(request, ("opt_in", "notice_token"))
    with SessionLocal() as session:
        proof = _student(session, request)
        try:
            presented = _signer().loads(form["notice_token"], max_age=1800)
            if (not isinstance(presented, list) or len(presented) != 8 or presented[:5] != [
                    proof.profile_id, proof.session_version, program_id, class_id, service.NOTICE_VERSION]
                    or not isinstance(presented[5], list) or not isinstance(presented[6], list)
                    or not isinstance(presented[7], str)):
                raise classroom.ClassroomDenied(service.UNAVAILABLE)
            service.accept(session, student=proof, program_id=program_id, class_id=class_id,
                notice_version=presented[4], assignment_ids=tuple(presented[5]),
                previous=tuple(presented[6]), source_revision=presented[7], opted_in=form["opt_in"] == "yes")
            session.commit()
        except (BadData, classroom.ClassroomDenied) as error:
            session.rollback()
            raise HTTPException(409, "Authorization was not saved. Reload to review your current Class and directors.") from error
    request.session["classroom_reporting_message"] = "Authorization saved. Comprehensive reporting is not available yet."
    return RedirectResponse(PATH, 303)


@router.post(PATH + "/{program_id}/classes/{class_id}/withdraw")
async def withdraw(request: Request, program_id: int, class_id: int):
    await _form(request, ())
    with SessionLocal() as session:
        try:
            service.withdraw(session, student=_student(session, request), program_id=program_id, class_id=class_id)
            session.commit()
        except classroom.ClassroomDenied as error:
            session.rollback()
            raise HTTPException(404, service.UNAVAILABLE) from error
    request.session["classroom_reporting_message"] = "Authorization withdrawn. Your records, learning benefits and individual P-Chart choices are unchanged."
    return RedirectResponse(PATH, 303)
