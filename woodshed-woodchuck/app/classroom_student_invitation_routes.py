"""Minimal invitation interfaces. No enrollment or P-Chart access is granted."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from . import classroom, classroom_director as director, classroom_s2 as s2
from . import classroom_student_invitations as service
from .age_privacy import utc
from .classroom_models import ClassroomClass, ClassroomStudentInvitation
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .models import TrustedVerifier
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token

router = APIRouter(route_class=PrivateRoute)
STUDENT_PATH = '/classroom/student-invitations'


def _page(request, **context):
    return director.templates.TemplateResponse(request=request, name='classroom_student_invitations.html',
        context={'title': 'Classroom invitations', 'csrf': csrf_token(request),
                 'page_class': 'classroom-director-page', **context})


def _student(session, request):
    try:
        return s2.student_from_request(session, request)
    except classroom.ClassroomDenied as error:
        raise HTTPException(401, 'Student sign-in is required.') from error


async def _form(request, fields):
    director._require_enabled()
    if not same_origin_mutation(request):
        raise HTTPException(403, 'A same-origin request is required.')
    form = await request.form()
    if len(form.multi_items()) != len(form) or set(form) != {'csrf', *fields}:
        raise HTTPException(400, 'Unsupported fields.')
    check_csrf(request, form.get('csrf'))
    return form


def _summary(row):
    now = s2._now()
    status = ('Revoked' if row.revoked_at else 'Accepted' if service.accepted_current(row, now)
              else 'Expired' if row.accepted_at or now >= utc(row.expires_at) else 'Pending')
    return {'id': row.id, 'status': status, 'expires': utc(row.expires_at).strftime('%b %d, %Y %H:%M UTC'),
            'legacy_expiry': row.accepted_at is not None and row.deadline_accepted_at is None,
            'can_accept': status == 'Pending' and utc(row.issued_at) <= now,
            'can_revoke': row.revoked_at is None}


def _student_rows(request):
    # Each Program gets its own transaction, matching the existing lock discipline.
    with SessionLocal() as session:
        proof = _student(session, request)
        s2._student(session, proof)
        service.reporting._account_rule(session, proof.profile_id)
        ids = list(session.scalars(select(ClassroomStudentInvitation.id).where(
            ClassroomStudentInvitation.profile_id == proof.profile_id)
            .order_by(ClassroomStudentInvitation.id.desc()).limit(50)))
    rows = []
    for ident in ids:
        with SessionLocal() as session:
            try:
                row = service.student_invitation(session, student=_student(session, request), invitation_id=ident)
                room = session.get(ClassroomClass, row.class_id)
                issuer = session.get(TrustedVerifier, row.issued_by_verifier_id)
                rows.append({**_summary(row), 'class_name': room.display_name, 'director_name': issuer.display_name})
            except classroom.ClassroomDenied:
                continue
    return rows


@router.get(STUDENT_PATH)
def student_page(request: Request):
    director._require_enabled()
    try:
        rows = _student_rows(request)
    except classroom.ClassroomDenied as error:
        raise HTTPException(404, service.UNAVAILABLE) from error
    return _page(request, mode='student', invitations=rows,
                 message=request.session.pop('student_classroom_invitation_message', None))


@router.post(STUDENT_PATH + '/identity')
async def student_identity(request: Request):
    await _form(request, ())
    with SessionLocal() as session:
        try:
            code = service.prepare_identity(session, student=_student(session, request))
        except classroom.ClassroomDenied as error:
            raise HTTPException(404, service.UNAVAILABLE) from error
    try:
        rows = _student_rows(request)
    except classroom.ClassroomDenied as error:
        raise HTTPException(404, service.UNAVAILABLE) from error
    return _page(request, mode='student', invitations=rows, identity_code=code)


@router.post(STUDENT_PATH + '/{invitation_id}/accept')
async def student_accept(request: Request, invitation_id: int):
    await _form(request, ())
    with SessionLocal() as session:
        try:
            service.accept(session, student=_student(session, request), invitation_id=invitation_id)
            session.commit()
        except classroom.ClassroomDenied as error:
            session.rollback()
            raise HTTPException(404, service.UNAVAILABLE) from error
    request.session['student_classroom_invitation_message'] = 'Invitation accepted. Enrollment and P-Chart access have not changed.'
    return RedirectResponse(STUDENT_PATH, 303)


DIRECTOR_PATH = '/classroom/director/{program_id}/classes/{class_id}/invitations'


@router.get(DIRECTOR_PATH)
def director_page(request: Request, program_id: int, class_id: int):
    director._require_enabled()
    with SessionLocal() as session:
        director._verifier(request, session)
    return _page(request, mode='director', confirm=True)


@router.post(DIRECTOR_PATH)
async def director_manage(request: Request, program_id: int, class_id: int):
    director._require_enabled()
    form = await request.form()
    action = form.get('action')
    fields = {'view': ('action',), 'issue': ('action', 'identity_code'), 'revoke': ('action', 'invitation_id')}
    if action not in fields:
        raise HTTPException(400, 'Unsupported action.')
    with SessionLocal() as session:
        verifier, actor = await director._authenticate_form(request, session, fields=fields[action])
        message = None
        try:
            service.reporting._exact_ids(program_id, class_id)
            classroom._lock_program(session, program_id)
            issued = select(ClassroomStudentInvitation).where(
                ClassroomStudentInvitation.program_id == program_id, ClassroomStudentInvitation.class_id == class_id,
                ClassroomStudentInvitation.issued_by_verifier_id == verifier.id)
            can_issue = False
            try:
                service.director_assignment(session, actor=actor, program_id=program_id, class_id=class_id)
                can_issue = True
            except classroom.ClassroomDenied:
                # Former issuers may manage their own retained references only.
                # No Class/student/practice access follows from this ownership.
                if action == 'issue' or session.scalar(issued.limit(1)) is None:
                    raise
            classroom._actor(session, actor)  # Recheck credentials after lock waits.
            if action == 'issue':
                identity = form.get('identity_code')
                row = service.issue_for_identity(session, actor=actor, program_id=program_id, class_id=class_id,
                                                 identity=identity.strip() if isinstance(identity, str) else identity)
                message = f'Invitation #{row.id} created. Ask the student to open Classroom invitations to accept.'
            elif action == 'revoke':
                raw = form.get('invitation_id', '')
                if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or len(raw) > 10:
                    raise classroom.ClassroomDenied(service.UNAVAILABLE)
                row = session.scalar(select(ClassroomStudentInvitation).where(
                    ClassroomStudentInvitation.id == int(raw), ClassroomStudentInvitation.program_id == program_id,
                    ClassroomStudentInvitation.class_id == class_id,
                    ClassroomStudentInvitation.issued_by_verifier_id == verifier.id))
                if row is None:
                    raise classroom.ClassroomDenied(service.UNAVAILABLE)
                service.revoke(session, actor=actor, invitation_id=row.id)
                message = f'Invitation #{row.id} revoked.'
            # Reference/status only: no student lookup or newly disclosed identity.
            rows = list(session.scalars(issued
                .order_by(ClassroomStudentInvitation.id.desc()).limit(50)))
            room = session.get(ClassroomClass, class_id)
            result = _page(request, mode='director', class_name=room.display_name, message=message,
                           invitations=[_summary(row) for row in rows], can_issue=can_issue)
            session.commit()
            return result
        except classroom.ClassroomDenied as error:
            session.rollback()
            raise HTTPException(404, service.UNAVAILABLE) from error
