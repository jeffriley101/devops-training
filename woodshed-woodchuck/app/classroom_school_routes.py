"""One school agreement screen, plus its independently authenticated evidence screen."""
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select

from . import classroom as s1, classroom_s2 as s2, classroom_school_authorization as service
from .classroom_enrollment_routes import _adult, _number
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .models import TrustedVerifier
from .session_config import session_secret
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token, require_site_admin
from .verifier_routes import current_verifier
from .verifiers import validate_email

SCHOOL = '/classroom/school/{program_id}'
STAFF = '/admin/classroom/school/{program_id}'
router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / 'templates'))
VERIFY_FIELDS = {'year_token', 'approver_email', 'authority_kind', 'school_name', 'authority_description',
    'contact_name', 'contact_role', 'contact_address', 'contact_source', 'verification_method',
    'verification_notes', 'contacted_at', 'independently_confirmed'}


def _enabled(request):
    if not service.enabled():
        raise HTTPException(404, service.UNAVAILABLE)
    if request.query_params:
        raise HTTPException(400, 'Use the school agreement controls.')
    staff = request.url.path.startswith('/admin/')
    if staff:
        require_site_admin(request)
    return staff


def _signer():
    return URLSafeTimedSerializer(session_secret(), salt='classroom-school-form-v1')


def _page(request, *, program_id, staff, detail=None, message=None):
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        adult = {'id': verifier.id, 'name': verifier.display_name} if verifier else None
    if detail is not None:
        for row in detail['records']:
            row['token'] = _signer().dumps([program_id, row['id'], adult['id'], list(row['revision']),
                service.AGREEMENT_VERSION, service.AGREEMENT_DIGEST])
        detail['year_options'] = [{'year': year, 'token': _signer().dumps(
            [program_id, year, adult['id'], list(detail['revisions'][year])])} for year in detail['years']]
    response = templates.TemplateResponse(request=request, name='classroom_school.html', context={
        'program_id': program_id, 'staff': staff, 'director': adult, 'detail': detail,
        'csrf': csrf_token(request), 'message': message, 'agreement': service.AGREEMENT,
        'agreement_version': service.AGREEMENT_VERSION})
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@router.get(SCHOOL)
@router.get(STAFF)
def enter(request: Request, program_id: int):
    staff = _enabled(request)
    _number(str(program_id))
    return _page(request, program_id=program_id, staff=staff)


async def _form(request):
    if (not same_origin_mutation(request)
            or request.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/x-www-form-urlencoded'):
        raise HTTPException(403, 'Use the same-origin school agreement form.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413, 'The school agreement form is too large.')
    try:
        pairs = parse_qsl(body.decode('utf-8'), keep_blank_values=True, strict_parsing=True,
            errors='strict', max_num_fields=24)
    except (ValueError, UnicodeError) as error:
        raise HTTPException(400, 'Use the school agreement form.') from error
    form = dict(pairs)
    if len(form) != len(pairs):
        raise HTTPException(400, 'Duplicate fields are not supported.')
    check_csrf(request, form.get('csrf'))
    return form


def _token(form, *, program_id, actor_id, size):
    payload = _signer().loads(form['year_token' if size == 4 else 'token'], max_age=1800)
    if (not isinstance(payload, list) or len(payload) != size or payload[0] != program_id
            or payload[2] != actor_id or not isinstance(payload[3], list)):
        raise s1.ClassroomDenied(service.UNAVAILABLE)
    return payload


@router.post(SCHOOL)
@router.post(STAFF)
async def control(request: Request, program_id: int):
    staff = _enabled(request)
    _number(str(program_id))
    form = await _form(request)
    action = form.get('action')
    fields = {'view': set(), 'verify': VERIFY_FIELDS, 'approve': {'token', 'approve'},
        'revoke': {'token', 'confirm', 'reason'}}.get(action)
    if fields is None or set(form) != {'csrf', 'director', 'pin', 'action', *fields} or action == 'verify' and not staff:
        raise HTTPException(400, 'Choose an available school agreement action.')
    message = None
    with SessionLocal() as session:
        verifier, actor = _adult(request, session, form)
        admin = s2.site_admin_authentication(request) if staff else None
        try:
            if action == 'verify':
                token = _token(form, program_id=program_id, actor_id=verifier.id, size=4)
                approver = session.scalar(select(TrustedVerifier).where(
                    TrustedVerifier.email == validate_email(form['approver_email'])))
                if approver is None:
                    raise s1.ClassroomDenied(service.UNAVAILABLE)
                try:
                    contacted = datetime.fromisoformat(form['contacted_at'])
                except ValueError as error:
                    raise s1.ClassroomDenied('Record the contact date and time with its time zone.') from error
                service.verify_authority(session, admin=admin, reviewer=actor, program_id=program_id,
                    approver_id=approver.id, year_start=token[1], previous=tuple(token[3]),
                    independently_confirmed=form['independently_confirmed'] == 'yes', contacted_at=contacted,
                    **{key:form[key] for key in VERIFY_FIELDS - {'year_token', 'approver_email',
                        'independently_confirmed', 'contacted_at'}})
                message = 'Authority evidence recorded. The named director or administrator must accept the same Program agreement.'
            elif action in {'approve', 'revoke'}:
                token = _token(form, program_id=program_id, actor_id=verifier.id, size=6)
                if action == 'approve':
                    service.approve(session, actor=actor, program_id=program_id, record_id=token[1],
                        previous=tuple(token[3]), agreement_version=token[4], agreement_digest=token[5],
                        explicit=form['approve'] == 'yes')
                    message = 'School agreement recorded for the stated year. Legal review is pending; student choices and parent consent remain separate.'
                else:
                    if form['confirm'] != 'yes':
                        raise s1.ClassroomDenied('Confirm revocation.')
                    service.revoke(session, actor=actor, admin=admin, program_id=program_id, record_id=token[1],
                        previous=tuple(token[3]), reason=form['reason'])
                    message = 'School agreement revoked. Earlier evidence and independent student and parent permissions are retained.'
            detail = service.view(session, actor=actor, program_id=program_id, admin=admin)
            if action != 'view':
                session.commit()
        except (s1.ClassroomDenied, BadData, ValueError) as error:
            session.rollback()
            raise HTTPException(409, 'School agreement action was not saved. Reload the current Program and evidence.') from error
    return _page(request, program_id=program_id, staff=staff, detail=detail, message=message)
