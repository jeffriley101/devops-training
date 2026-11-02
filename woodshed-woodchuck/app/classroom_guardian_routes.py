"""Native consent-bound parent controls; no director, school-email or PIN shortcut."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select

from . import classroom as s1, classroom_guardian_authorization as service
from .classroom_models import ClassroomClass, ClassroomStudentMembership, ClassroomTeachingAssignment
from .classroom_comprehensive_reporting import FIELD_LABELS
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .models import Organization, TrustedVerifier, WoodchuckProfile
from .session_config import session_secret
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token

PATH = '/classroom/parent'
router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / 'templates'))


def _enabled():
    if not service.enabled():
        raise HTTPException(404, service.UNAVAILABLE)


def _parent(session, request):
    try:
        return service.parent_from_request(session, request)
    except s1.ClassroomDenied as error:
        raise HTTPException(403, 'Open current parent access using your private emailed link.') from error


def _signer():
    return URLSafeTimedSerializer(session_secret(), salt='classroom-guardian-choice-v1')


def _rows(request):
    with SessionLocal() as session:
        proof = _parent(session, request)
        service._parent(session, proof)
        profile = session.get(WoodchuckProfile, proof.profile_id, populate_existing=True)
        identity = {'name': profile.display_name, 'woodchuck_id': profile.woodchuck_id}
        scopes = session.execute(select(ClassroomClass.program_id, ClassroomClass.id).join(
            ClassroomStudentMembership, ClassroomStudentMembership.class_id == ClassroomClass.id)
            .where(ClassroomStudentMembership.profile_id == proof.profile_id)
            .order_by(ClassroomClass.program_id, ClassroomClass.id)).all()
    rows = []
    for program_id, class_id in scopes:
        # Do not hold one Program while acquiring another Program's lock.
        with SessionLocal() as session:
            proof = _parent(session, request)
            choices = []
            director_ids = set()
            for purpose in ('participation', 'reporting'):
                choice = service.choice(session, parent=proof, program_id=program_id,
                    class_id=class_id, purpose=purpose)
                version, notice, digest, _ = service._notice(purpose)
                director_ids.update(choice['assignment_ids'])
                token = _signer().dumps([
                    proof.consent_id, proof.profile_id, service.token_binding(proof),
                    program_id, class_id, purpose, version, digest,
                    list(choice['revision']), choice['source_revision'],
                    list(choice['assignment_ids']), choice['participation_id'],
                ]) if choice['eligible'] else None
                public_choice = {key: choice[key] for key in ('eligible', 'authorized', 'saved')}
                choices.append({'purpose': purpose, 'choice': public_choice, 'token': token,
                    'notice': service.PARTICIPATION_NOTICE if purpose == 'participation' else service.REPORTING_NOTICE})
            directors = list(session.scalars(select(TrustedVerifier.display_name).join(
                ClassroomTeachingAssignment, ClassroomTeachingAssignment.verifier_id == TrustedVerifier.id)
                .where(ClassroomTeachingAssignment.id.in_(director_ids)).order_by(ClassroomTeachingAssignment.id)))
            rows.append({'program_id': program_id, 'class_id': class_id,
                'program_name': session.get(Organization, program_id).name,
                'class_name': session.get(ClassroomClass, class_id).display_name,
                'directors': directors, 'choices': choices})
    return identity, rows


@router.get(PATH)
def page(request: Request):
    _enabled()
    if request.query_params:
        raise HTTPException(400, 'Use the parent permission controls.')
    try:
        identity, rows = _rows(request)
    except s1.ClassroomDenied as error:
        raise HTTPException(403, service.UNAVAILABLE) from error
    response = templates.TemplateResponse(request=request, name='classroom_parent.html', context={
        'csrf': csrf_token(request), 'classes': rows, 'attestation': service.ATTESTATION,
        'fields': FIELD_LABELS, 'child': identity,
        'message': request.session.pop('classroom_parent_message', None)})
    response.headers['X-Frame-Options'] = 'DENY'
    return response


async def _form(request, fields):
    _enabled()
    if request.query_params or not same_origin_mutation(request):
        raise HTTPException(403, 'A same-origin request is required.')
    if request.headers.get('content-type', '').split(';', 1)[0] != 'application/x-www-form-urlencoded':
        raise HTTPException(415, 'Use the parent permission form.')
    data = await request.form()
    if (set(data) != {'csrf', *fields} or len(data.multi_items()) != len(data)
            or any(not isinstance(value, str) or len(value) > 2500 for value in data.values())):
        raise HTTPException(400, 'Reload and explicitly choose the current parent permission.')
    check_csrf(request, data.get('csrf'))
    return data


@router.post(PATH + '/{program_id}/classes/{class_id}/{purpose}/authorize')
async def authorize(request: Request, program_id: int, class_id: int, purpose: str):
    data = await _form(request, {'notice_token', 'guardian_attestation', 'opt_in'})
    with SessionLocal() as session:
        proof = _parent(session, request)
        try:
            token = _signer().loads(data['notice_token'], max_age=1800)
            if (not isinstance(token, list) or len(token) != 12
                    or token[:6] != [proof.consent_id, proof.profile_id, service.token_binding(proof),
                                    program_id, class_id, purpose]
                    or not isinstance(token[8], list) or not isinstance(token[10], list)):
                raise s1.ClassroomDenied(service.UNAVAILABLE)
            service.accept(session, parent=proof, program_id=program_id, class_id=class_id,
                purpose=purpose, notice_version=token[6], notice_digest=token[7],
                guardian_attested=data['guardian_attestation'] == 'yes', opted_in=data['opt_in'] == 'yes',
                previous=tuple(token[8]), source_revision=token[9], assignment_ids=tuple(token[10]),
                participation_id=token[11])
            session.commit()
        except (BadData, s1.ClassroomDenied) as error:
            session.rollback()
            raise HTTPException(409, 'Permission was not saved. Reload to review the current Class and directors.') from error
    request.session['classroom_parent_message'] = 'Parent permission saved. Other participation, student reporting and individual P-Chart choices remain separate.'
    return RedirectResponse(PATH, 303)


@router.post(PATH + '/{program_id}/classes/{class_id}/{purpose}/withdraw')
async def withdraw(request: Request, program_id: int, class_id: int, purpose: str):
    data = await _form(request, {'confirm'})
    if data['confirm'] != 'yes':
        raise HTTPException(400, 'Confirm withdrawal.')
    with SessionLocal() as session:
        try:
            service.withdraw(session, parent=_parent(session, request), program_id=program_id,
                class_id=class_id, purpose=purpose)
            session.commit()
        except s1.ClassroomDenied as error:
            session.rollback()
            raise HTTPException(404, service.UNAVAILABLE) from error
    request.session['classroom_parent_message'] = 'Permission withdrawn. Membership records, private practice and individual P-Chart choices are retained. Reporting also requires current participation permission.'
    return RedirectResponse(PATH, 303)
