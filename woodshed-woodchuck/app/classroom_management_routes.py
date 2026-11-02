"""Director management UI. Every durable change delegates to accepted S1/S2."""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import classroom as s1, classroom_s2 as s2, classroom_director, classroom_enrollment
from . import classroom_management_views as views
from .classroom_enrollment_routes import _form as enrollment_form, _adult, _number, _qr
from .classroom_models import (ClassroomRoleGrant, ClassroomTeachingAssignment,
    ClassroomStudentMembership)
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .models import TrustedVerifier
from .site_admin import csrf_token
from .verifier_routes import current_verifier
from .verifiers import validate_email

PATH = '/classroom/programs'
router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / 'templates'))


def enabled():
    return classroom_director.enabled() and classroom_enrollment.enabled()


def _enabled():
    if not enabled():
        raise HTTPException(404, 'Classroom management unavailable.')


def _exact(form, fields):
    if set(form) != {'csrf', *fields}:
        raise HTTPException(400, 'Unsupported management fields.')


async def _form(request):
    _enabled()
    # Reuse the accepted bounded, same-origin, CSRF and duplicate-field parser.
    return await enrollment_form(request)


def _page(request, *, mode='home', status_code=200, **context):
    from .classroom_identity import enabled as private_identity_enabled
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        identity = {'id': verifier.id, 'name': verifier.display_name} if verifier else None
    response = templates.TemplateResponse(request=request, name='classroom_management.html',
        context={'title': 'Programs and Classes', 'csrf': csrf_token(request),
            'director': identity, 'mode': mode, 'workspace_enabled': private_identity_enabled(), **context}, status_code=status_code)
    response.headers['X-Frame-Options'] = 'DENY'
    return response


def _no_query(request):
    if request.query_params:
        raise HTTPException(400, 'Use the management controls.')


@router.get(PATH)
def home(request: Request):
    _enabled(); _no_query(request)
    return _page(request)


@router.post(PATH)
async def catalog(request: Request):
    form = await _form(request)
    _exact(form, {'director', 'pin', 'before'})
    with SessionLocal() as session:
        _, actor = _adult(request, session, form)
        result = views.catalog(session, actor=actor, before=_number(form['before'], optional=True))
    return _page(request, catalog=result)


PROGRAM = PATH + '/{program_id}'
CLASS = PROGRAM + '/classes/{class_id}'


@router.get(PROGRAM)
@router.get(CLASS)
def enter(request: Request, program_id: int, class_id: int | None = None):
    _enabled(); _no_query(request)
    _number(str(program_id))
    if class_id is not None:
        _number(str(class_id))
    return _page(request, mode='detail', detail=None)


PROGRAM_FIELDS = {'trial': set(), 'create-class': {'display_name'},
    'grant-role': {'target_email', 'role'}, 'revoke-role': {'grant_id'},
    'propose-transfer': {'target_email'}, 'cancel-transfer': {'transfer_id'},
    'accept-transfer': {'transfer_id'}, 'code': {'class_id', 'preferred'}}
CLASS_FIELDS = {'archive': set(), 'reactivate': set(), 'open': set(), 'close': set(),
    'assign-teacher': {'target_email'}, 'end-teaching': {'assignment_id'},
    'depart-teaching': {'assignment_id'}, 'code': {'preferred'},
    'suspend': {'membership_id'}, 'remove': {'membership_id'}, 'reinstate': {'membership_id'}}
LABELS = {'trial': 'Start the 14-day trial', 'create-class': 'Create Class',
    'grant-role': 'Grant Program role', 'revoke-role': 'Revoke Program role',
    'propose-transfer': 'Propose ownership transfer', 'cancel-transfer': 'Cancel ownership transfer',
    'accept-transfer': 'Accept ownership transfer', 'code': 'Issue or rotate Class code',
    'archive': 'Archive Class', 'reactivate': 'Reactivate Class', 'open': 'Open enrollment',
    'close': 'Close enrollment', 'assign-teacher': 'Assign teaching director',
    'end-teaching': 'End teaching assignment', 'depart-teaching': 'Leave teaching assignment',
    'suspend': 'Suspend membership', 'remove': 'Remove membership', 'reinstate': 'Reinstate membership'}
EFFECTS = {
    'trial': 'One ordinary trial per Program. It lasts 14 days and does not charge a card or renew automatically.',
    'create-class': 'Create one active, open Class within the existing allowance. No teacher, code or student is added automatically.',
    'grant-role': 'This grants only the selected Program responsibility. Teaching and student reporting remain separate.',
    'revoke-role': 'The selected role ends immediately. Other roles, ownership and teaching assignments remain independent.',
    'propose-transfer': 'Only the named adult can accept. A new proposal supersedes the previous pending proposal. Ownership changes only on acceptance.',
    'cancel-transfer': 'This pending proposal will no longer be acceptable.',
    'accept-transfer': 'You become the canonical Owner. Existing role grants and teaching assignments for both adults remain; review and revoke them separately if needed.',
    'code': 'Any current code is invalidated. The new code is shown once. Existing memberships and sharing permissions do not change.',
    'archive': 'Close enrollment, invalidate the code and stop Class learning access. Retain memberships, assignments, holds and history.',
    'reactivate': 'Restore the same Class within the current allowance. Enrollment starts closed, the old code remains invalid and holds remain in place.',
    'open': 'Allow new code joins and enrollment invitations while current access is valid. No existing permission is expanded.',
    'close': 'Block new code joins, new enrollment invitations and acceptance of outstanding invitations. Existing memberships remain.',
    'assign-teacher': 'Assign this adult to this exact Class, subject to the teaching allowance. No reporting or chart permission is created.',
    'end-teaching': 'End this Class assignment immediately. Access depending on that assignment ends; historical evidence is retained.',
    'depart-teaching': 'End your own assignment. You may lose access to this Class and Program immediately.',
    'suspend': 'End the active membership period and place a hold. Code joins and invitations cannot release it. History is retained.',
    'remove': 'End membership and retain a removal hold. History and the original account remain intact.',
    'reinstate': 'Explicitly release the hold and open a new membership period if current account and Class requirements pass. Reporting is not reauthorized.',
}


def _prepare(session, *, actor, program_id, class_id, action, form):
    current = views.scope(session, actor=actor, program_id=program_id, class_id=class_id)
    if _number(form['owner_version']) != current['program'].owner_version:
        raise s1.ClassroomDenied('Program ownership changed. Reopen the controls.')
    required = ('delegate' if action in {'grant-role', 'revoke-role', 'propose-transfer', 'cancel-transfer'}
        else 'trial' if action == 'trial' else 'codes' if action == 'code'
        else None if action in {'accept-transfer', 'depart-teaching'} else 'manage')
    if required and not current[required]:
        raise s1.ClassroomDenied('Management authority unavailable.')
    payload = {key: form[key] for key in (PROGRAM_FIELDS if class_id is None else CLASS_FIELDS)[action]}
    for key in {'grant_id', 'transfer_id', 'assignment_id', 'membership_id', 'class_id'} & payload.keys():
        payload[key] = _number(payload[key])
    target = current['metadata']['display_name']
    adult = None
    if 'target_email' in payload:
        try:
            email = validate_email(payload['target_email'])
        except ValueError as error:
            raise s1.ClassroomDenied('Existing adult unavailable.') from error
        adult = session.scalar(select(TrustedVerifier).where(TrustedVerifier.email == email))
        if adult is None:
            raise s1.ClassroomDenied('Existing adult unavailable.')
        payload['target_email'] = email
        target = f'{adult.display_name} · {adult.email}'
    if action == 'grant-role' and payload['role'] not in s1.ROLES:
        raise s1.ClassroomDenied('Program role unavailable.')
    if action == 'create-class':
        payload['display_name'] = s1._name(payload['display_name'], 150)
        target = payload['display_name']
    elif action == 'revoke-role':
        grant = session.scalar(select(ClassroomRoleGrant).where(ClassroomRoleGrant.id == payload['grant_id'],
            ClassroomRoleGrant.program_id == program_id, ClassroomRoleGrant.ended_at.is_(None)))
        if grant is None:
            raise s1.ClassroomDenied('Scoped role unavailable.')
        target = f"{views._adult(session, grant.verifier_id)['name']} · {grant.role.replace('_', ' ')}"
    elif action in {'cancel-transfer', 'accept-transfer'}:
        proposal = s1._proposal(session, current['program'], payload['transfer_id'])
        if action == 'accept-transfer' and proposal.recipient_verifier_id != current['actor_id']:
            raise s1.ClassroomDenied('Intended recipient required.')
        target = views._adult(session, proposal.recipient_verifier_id)['name']
    elif action in {'end-teaching', 'depart-teaching'}:
        assignment = session.scalar(select(ClassroomTeachingAssignment).where(
            ClassroomTeachingAssignment.id == payload['assignment_id'], ClassroomTeachingAssignment.program_id == program_id,
            ClassroomTeachingAssignment.class_id == class_id, ClassroomTeachingAssignment.ended_at.is_(None)))
        if assignment is None or action == 'depart-teaching' and assignment.verifier_id != current['actor_id']:
            raise s1.ClassroomDenied('Scoped teaching assignment unavailable.')
        target = views._adult(session, assignment.verifier_id)['name']
    elif action in {'suspend', 'remove', 'reinstate'}:
        anchor = session.scalar(select(ClassroomStudentMembership).where(
            ClassroomStudentMembership.id == payload['membership_id'], ClassroomStudentMembership.class_id == class_id))
        if anchor is None:
            raise s1.ClassroomDenied('Scoped membership unavailable.')
        target = f'Membership reference {anchor.id}'
    elif action == 'code':
        room_id = class_id if class_id is not None else payload['class_id']
        s1._class(session, program_id, room_id)
        if payload['preferred']:
            payload['preferred'] = s2.normalize_code(payload['preferred'])
        target = f'Class {room_id}'
    return payload, target, adult


def _apply(session, *, request, actor, program_id, class_id, action, payload, adult):
    args = {'actor': actor, 'program_id': program_id}
    if class_id is not None:
        args['class_id'] = class_id
    code_result = None
    selected = class_id
    if action == 'trial':
        s2.start_trial(session, **args)
    elif action == 'create-class':
        selected = s2.create_class(session, **args, display_name=payload['display_name']).id
    elif action == 'grant-role':
        s1.grant_role(session, **args, verifier_id=adult.id, role=payload['role'])
    elif action == 'revoke-role':
        s1.revoke_role(session, **args, grant_id=payload['grant_id'])
    elif action == 'propose-transfer':
        s1.propose_transfer(session, **args, recipient_id=adult.id)
    elif action == 'cancel-transfer':
        s1.cancel_transfer(session, **args, transfer_id=payload['transfer_id'])
    elif action == 'accept-transfer':
        s1.accept_transfer(session, **args, transfer_id=payload['transfer_id'])
    elif action == 'assign-teacher':
        s2.assign_teacher(session, **args, verifier_id=adult.id)
    elif action in {'end-teaching', 'depart-teaching'}:
        s2.end_teaching(session, actor=actor, program_id=program_id,
            assignment_id=payload['assignment_id'], reason='departed' if action == 'depart-teaching' else 'revoked')
    elif action in {'archive', 'reactivate'}:
        s2.set_class_state(session, **args, state='archived' if action == 'archive' else 'active')
    elif action in {'open', 'close'}:
        s2.set_enrollment(session, **args, open=action == 'open')
    elif action == 'code':
        room_id = class_id if class_id is not None else payload['class_id']
        row, visible = s2.issue_code(session, actor=actor, program_id=program_id, class_id=room_id,
            preferred=payload['preferred'] or None)
        code_result = {**_qr(request, program_id, visible), 'class_id': row.class_id, 'generation': row.generation}
    elif action in {'suspend', 'remove'}:
        s2.suspend_member(session, **args, membership_id=payload['membership_id'],
            reason='admin_removal' if action == 'remove' else 'conduct', remove=action == 'remove')
    elif action == 'reinstate':
        s2.reinstate_member(session, **args, membership_id=payload['membership_id'])
    return selected, code_result


@router.post(PROGRAM)
@router.post(CLASS)
async def control(request: Request, program_id: int, class_id: int | None = None):
    form = await _form(request)
    _number(str(program_id))
    if class_id is not None:
        _number(str(class_id))
    action = form.get('action')
    base = {'director', 'pin', 'action'}
    if action == 'view':
        _exact(form, base | {'before', 'section'})
        if form['section'] not in {'classes', 'roles', 'roster', 'teachers'}:
            raise HTTPException(400, 'Choose a management section.')
    else:
        fields = (PROGRAM_FIELDS if class_id is None else CLASS_FIELDS).get(action)
        if fields is None or form.get('stage') not in {'review', 'apply'}:
            raise HTTPException(400, 'Choose an available management action.')
        _exact(form, base | {'stage', 'owner_version', *fields} |
            ({'confirm_change'} if form['stage'] == 'apply' else set()))
        if form['stage'] == 'apply' and form['confirm_change'] != 'yes':
            raise HTTPException(400, 'Confirm this exact change.')
    result = confirmation = code_result = detail = None
    with SessionLocal() as session:
        _, actor = _adult(request, session, form)
        try:
            if action == 'view':
                detail = views.detail(session, actor=actor, program_id=program_id, class_id=class_id,
                    before=_number(form['before'], optional=True), section=form['section'])
            else:
                # Review and apply both acquire the same Program-first scope and
                # current credentials; apply always calls the accepted service.
                payload, target, adult = _prepare(session, actor=actor, program_id=program_id,
                    class_id=class_id, action=action, form=form)
                if form['stage'] == 'review':
                    confirmation = {'action': action, 'label': LABELS[action], 'effect': EFFECTS[action],
                        'target': target, 'payload': payload, 'owner_version': form['owner_version']}
                    detail = views.detail(session, actor=actor, program_id=program_id, class_id=class_id)
                else:
                    selected, code_result = _apply(session, request=request, actor=actor,
                        program_id=program_id, class_id=class_id, action=action, payload=payload, adult=adult)
                    departed_without_access = False
                    if action == 'depart-teaching':
                        try:
                            views.scope(session, actor=actor, program_id=program_id, class_id=selected)
                        except s1.ClassroomDenied:
                            departed_without_access = True
                    # Only deliberate loss of metadata authority after one's own
                    # departure may omit refresh. Integrity/read failures still
                    # abort the outer transaction rather than report success.
                    detail = None if departed_without_access else views.detail(
                        session, actor=actor, program_id=program_id, class_id=selected)
                    session.commit()
                    result = LABELS[action] + ' — completed. Current permissions apply immediately.'
        except (s1.ClassroomDenied, IntegrityError):
            session.rollback()
            failed = True
        else:
            failed = False
    if failed:
        return _page(request, mode='detail', detail=None, status_code=409,
            error='Management change or access refused. Check your current role, scope, ownership version, account requirements and Class allowance. Reopen the controls.')
    return _page(request, mode='detail', detail=detail, confirmation=confirmation,
        message=result, code_result=code_result)
