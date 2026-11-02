"""Enrollment screens over the reviewed S2 and Enrollment V2 services."""
import base64
import io
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import qrcode
import qrcode.image.svg
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import classroom as s1, classroom_s2 as s2, classroom_enrollment as service
from . import classroom_enrollment_views as views
from .age_privacy import sharing_allowed
from .classroom_enrollment_models import ClassroomEnrollmentInvitation as Invitation
from .classroom_models import ClassroomClass, ClassroomStudentMembership
from .db import SessionLocal
from .login_limits import enforce_login_limit
from .membership_routes import PrivateRoute
from .session_origin import same_origin_mutation
from .site_admin import check_csrf, csrf_token
from .verifier_routes import current_verifier

PATH = '/classroom/enrollment'
DIRECTOR = PATH + '/director'
router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / 'templates'))


def _enabled():
    if not service.enabled():
        raise HTTPException(404, 'Classroom enrollment unavailable.')


def _number(value, *, optional=False):
    if optional and value in (None, ''):
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 10:
        raise HTTPException(400, 'Choose a valid Music Program or invitation reference.')
    number = int(value)
    if not 1 <= number <= 2147483647:
        raise HTTPException(400, 'Choose a valid Music Program or invitation reference.')
    return number


async def _form(request):
    _enabled()
    if not same_origin_mutation(request):
        raise HTTPException(403, 'A same-origin request is required.')
    if request.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/x-www-form-urlencoded':
        raise HTTPException(415, 'Use the enrollment form.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            raise HTTPException(413, 'The enrollment form is too large.')
    try:
        pairs = parse_qsl(body.decode('utf-8'), keep_blank_values=True, strict_parsing=True,
                          errors='strict', max_num_fields=10)
    except (ValueError, UnicodeError) as error:
        raise HTTPException(400, 'Use the enrollment form.') from error
    form = dict(pairs)
    if len(form) != len(pairs):
        raise HTTPException(400, 'Duplicate fields are not supported.')
    check_csrf(request, form.get('csrf'))
    return form


def _exact(form, fields):
    if set(form) != {'csrf', *fields}:
        raise HTTPException(400, 'Unsupported enrollment fields.')


def _student(request, session, expected=None):
    try:
        proof = s2.student_from_request(session, request)
    except s1.ClassroomDenied as error:
        raise HTTPException(401, 'Student sign-in is required. Reopen this page.') from error
    if expected is not None and expected != session.get(s2.WoodchuckProfile, proof.profile_id).woodchuck_id:
        raise HTTPException(401, 'Sign-in changed. Reopen this page.')
    return proof


def _account(request):
    with SessionLocal() as session:
        try:
            proof = s2.student_from_request(session, request)
            profile = s2._student(session, proof, require_eligibility=False)
        except s1.ClassroomDenied:
            return None
        return {'id': profile.id, 'woodchuck_id': profile.woodchuck_id,
                'ready': s2.eligible(session, profile.id),
                'sharing': sharing_allowed(session, profile.id)}


def _page(request, *, mode='home', status_code=200, **context):
    # Called only after scoped service transactions have closed.
    return templates.TemplateResponse(request=request, name='classroom_enrollment.html',
        status_code=status_code, context={'title': 'Classroom enrollment', 'mode': mode,
        'csrf': csrf_token(request), 'account': None if mode.startswith('director') else _account(request),
        'message': request.session.pop('classroom_enrollment_message', None), **context})


def _cursor(request):
    pairs = list(request.query_params.multi_items())
    if len(pairs) != len(request.query_params) or set(request.query_params) - {'before'}:
        raise HTTPException(400, 'Unsupported enrollment query.')
    return _number(request.query_params.get('before'), optional=True)


def _memberships(request):
    with SessionLocal() as session:
        proof = _student(request, session)
        s2._student(session, proof, require_eligibility=False)
        rows = session.execute(select(ClassroomClass.program_id, ClassroomStudentMembership.class_id).join(
            ClassroomStudentMembership, ClassroomStudentMembership.class_id == ClassroomClass.id).where(
            ClassroomStudentMembership.profile_id == proof.profile_id).order_by(
            ClassroomStudentMembership.id.desc()).limit(views.PAGE_SIZE)).all()
    result = []
    for program_id, class_id in rows:
        with SessionLocal() as session:
            try:
                result.append(views.student_membership(session, student=_student(request, session),
                    program_id=program_id, class_id=class_id))
            except s1.ClassroomDenied as error:
                raise HTTPException(409, 'Your Class status could not be verified. Ask your director for help.') from error
    return result


@router.get(PATH)
def home(request: Request):
    _enabled()
    if request.query_params:
        raise HTTPException(400, 'Class codes belong in the form, not the address.')
    account = _account(request)
    return _page(request, account=account, members=_memberships(request) if account else [])


@router.post(PATH + '/review')
async def review(request: Request):
    form = await _form(request)
    _exact(form, {'account', 'program_id', 'code'})
    with SessionLocal() as session:
        proof = _student(request, session, form['account'])
        enforce_login_limit(request, 'student', form['account'])
        try:
            result = views.code_review(session, student=proof,
                program_id=_number(form['program_id']), code=form['code'])
        except s1.ClassroomDenied:
            result = None
    if result is None:
        return _page(request, error='Class entry is unavailable. Check the current Music Program number and Class code.',
                     members=[], status_code=409)
    request.session['classroom_enrollment_return'] = {'kind': 'code', 'account': result['account'],
        'program_id': result['program_id'], 'intent': result['intent']}
    return _page(request, mode='review', review=result)


@router.get(PATH + '/resume')
def resume(request: Request):
    _enabled()
    saved = request.session.get('classroom_enrollment_return')
    if not isinstance(saved, dict) or saved.get('kind') != 'code':
        return RedirectResponse(PATH, 303)
    with SessionLocal() as session:
        proof = _student(request, session, saved.get('account'))
        try:
            result = views.code_review(session, student=proof, program_id=saved.get('program_id'), intent=saved.get('intent'))
        except s1.ClassroomDenied:
            result = None
    if result is None:
        request.session.pop('classroom_enrollment_return', None)
        return _page(request, members=[], error='This entry expired or changed. Enter the current Class code again; completed account authorization stays intact.', status_code=409)
    return _page(request, mode='review', review=result)


@router.post(PATH + '/join')
async def join(request: Request):
    form = await _form(request)
    _exact(form, {'account', 'program_id', 'intent', 'confirm_join'})
    if form['confirm_join'] != 'yes':
        raise HTTPException(400, 'Confirm that you want to join this Class.')
    with SessionLocal() as session:
        proof = _student(request, session, form['account'])
        enforce_login_limit(request, 'student', form['account'])
        try:
            s2.join_class(session, student=proof, program_id=_number(form['program_id']), intent=form['intent'])
            session.commit()
        except (s1.ClassroomDenied, IntegrityError):
            session.rollback()
            failed = True
        else:
            failed = False
    if failed:
        return _page(request, members=[], error='Enrollment could not be confirmed. Complete account authorization, or ask your director to check current Class access.', status_code=409)
    request.session.pop('classroom_enrollment_return', None)
    request.session['classroom_enrollment_message'] = 'Class joined. Choose any charts to share separately; nothing was shared automatically.'
    return RedirectResponse(PATH, 303)


@router.get(PATH + '/invitations')
def inbox(request: Request):
    _enabled()
    before = _cursor(request)
    account = _account(request)
    if account is None:
        return _page(request, mode='home', members=[])
    if not account['ready']:
        # A fixed own-inbox return after the existing account/parent process.
        # This stores presentation context, never an enrollment authorization.
        request.session['classroom_enrollment_return'] = {'kind': 'invitations', 'account': account['woodchuck_id']}
    with SessionLocal() as session:
        proof = _student(request, session)
        s2._student(session, proof, require_eligibility=False)
        query = select(Invitation.id).where(Invitation.profile_id == proof.profile_id)
        if before is not None:
            query = query.where(Invitation.id < before)
        ids = list(session.scalars(query.order_by(Invitation.id.desc()).limit(views.PAGE_SIZE + 1)))
    offers = []
    for ident in ids[:views.PAGE_SIZE]:
        with SessionLocal() as session:
            try:
                offers.append(views.student_offer(session, student=_student(request, session), invitation_id=ident))
            except s1.ClassroomDenied as error:
                raise HTTPException(409, 'An invitation status could not be verified. Reopen your inbox or ask your director for help.') from error
    return _page(request, mode='inbox', offers=offers,
        next_before=ids[views.PAGE_SIZE - 1] if len(ids) > views.PAGE_SIZE else None)


@router.post(PATH + '/invitations/{invitation_id}/respond')
async def respond(request: Request, invitation_id: int):
    form = await _form(request)
    _exact(form, {'account', 'choice'})
    with SessionLocal() as session:
        proof = _student(request, session, form['account'])
        enforce_login_limit(request, 'student', form['account'])
        try:
            result = service.respond(session, student=proof, invitation_id=invitation_id, choice=form['choice'])
            session.commit()
        except (s1.ClassroomDenied, IntegrityError):
            session.rollback()
            result = None
    if result is None:
        request.session['classroom_enrollment_return'] = {'kind': 'invitations', 'account': form['account']}
        return _page(request, members=[], error='The invitation is unavailable. Check account authorization and current Class access, then reopen your inbox.', status_code=409)
    request.session.pop('classroom_enrollment_return', None)
    messages = {
        'accepted': 'Enrollment confirmed. Practice Charts and reporting permissions were not shared automatically.',
        'declined': 'Invitation declined. Your membership and sharing choices have not changed.',
        'deferred': 'Saved for later. The response deadline and your membership have not changed.',
        'expired': 'This invitation expired. Ask your director for a new invitation.',
        'revoked': 'This invitation was revoked. Your existing memberships have not changed.',
    }
    request.session['classroom_enrollment_message'] = messages[result.state]
    return RedirectResponse(PATH + '/invitations', 303)


def _adult(request, session, form):
    verifier = current_verifier(request, session)
    if verifier is None:
        raise HTTPException(401, 'Adult sign-in is required.')
    if form.get('director') != str(verifier.id):
        raise HTTPException(401, 'Sign-in changed. Reopen this page.')
    pin = form.get('pin', '')
    if len(pin) != 4 or not pin.isascii() or not pin.isdecimal():
        raise HTTPException(400, 'Enter your four-digit verifier PIN.')
    enforce_login_limit(request, 'verifier', verifier.email)
    try:
        actor = s1.authenticate_adult(session, email=verifier.email, pin=pin)
    except (s1.ClassroomDenied, ValueError) as error:
        raise HTTPException(401, 'Verifier PIN was not recognized.') from error
    if s1._actor(session, actor) != verifier.id:
        raise HTTPException(401, 'Adult sign-in is required.')
    return verifier, actor


@router.get(DIRECTOR)
def director_home(request: Request):
    _enabled()
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        identity = {'id': verifier.id, 'name': verifier.display_name} if verifier else None
    return _page(request, mode='director-home', director=identity)


@router.post(DIRECTOR)
async def director_classes(request: Request):
    form = await _form(request)
    _exact(form, {'director', 'pin'})
    with SessionLocal() as session:
        verifier, actor = _adult(request, session, form)
        classes = views.director_classes(session, actor=actor)
        identity = {'id': verifier.id, 'name': verifier.display_name}
    return _page(request, mode='director-home', director=identity, classes=classes)


CLASS_PATH = DIRECTOR + '/{program_id}/classes/{class_id}'


@router.get(CLASS_PATH)
def director_entry(request: Request, program_id: int, class_id: int):
    _enabled()
    _number(str(program_id)); _number(str(class_id))
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        identity = {'id': verifier.id, 'name': verifier.display_name} if verifier else None
    return _page(request, mode='director-class', director=identity, detail=None)


def _qr(request, program_id, code):
    origin = (os.getenv('PUBLIC_BASE_URL') or str(request.base_url)).rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise HTTPException(503, 'The Class QR link is unavailable.')
    url = origin + PATH + '#' + urlencode({'program': str(program_id), 'code': code})
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=4)
    buffer = io.BytesIO(); image.save(buffer)
    return {'url': url, 'code': code, 'image': 'data:image/svg+xml;base64,' + base64.b64encode(buffer.getvalue()).decode()}


@router.post(CLASS_PATH)
async def director_action(request: Request, program_id: int, class_id: int):
    form = await _form(request)
    _number(str(program_id)); _number(str(class_id))
    fields = {'view': {'before'}, 'issue': {'woodchuck_id'}, 'revoke': {'invitation_id'}, 'qr': {'code'}}
    action = form.get('action')
    if action not in fields:
        raise HTTPException(400, 'Unsupported enrollment action.')
    _exact(form, {'director', 'pin', 'action', *fields[action]})
    result = qr = error = None
    with SessionLocal() as session:
        verifier, actor = _adult(request, session, form)
        identity = {'id': verifier.id, 'name': verifier.display_name}
        try:
            detail = views.director_class(session, actor=actor, program_id=program_id, class_id=class_id,
                before=_number(form.get('before'), optional=True))
            if action == 'issue':
                service.issue_invitation(session, actor=actor, program_id=program_id, class_id=class_id,
                    woodchuck_id=form['woodchuck_id'])
                result = 'Enrollment invitation sent to the student’s own inbox. Nothing is shared by issuance.'
            elif action == 'revoke':
                ident = _number(form['invitation_id'])
                match = session.scalar(select(Invitation.id).where(Invitation.id == ident,
                    Invitation.program_id == program_id, Invitation.class_id == class_id,
                    Invitation.issued_by_verifier_id == verifier.id))
                if match is None:
                    raise s1.ClassroomDenied(service.UNAVAILABLE)
                service.revoke_invitation(session, actor=actor, invitation_id=ident)
                result = 'Invitation revoked. Existing memberships have not changed.'
            elif action == 'qr':
                if not detail['can_issue']:
                    raise s1.ClassroomDenied(service.UNAVAILABLE)
                row = s2._entry(session, program_id, code=form['code'])
                if row.class_id != class_id:
                    raise s1.ClassroomDenied(service.UNAVAILABLE)
                qr = _qr(request, program_id, s2.normalize_code(form['code']))
            detail = views.director_class(session, actor=actor, program_id=program_id, class_id=class_id,
                before=_number(form.get('before'), optional=True))
            session.commit()
        except (s1.ClassroomDenied, IntegrityError):
            session.rollback()
            detail = None
            result = qr = None
            error = 'Enrollment access is unavailable. Check the WC# and your current Class assignment, enrollment and access.'
    return _page(request, mode='director-class', director=identity, detail=detail,
        message=result, qr=qr, error=error, status_code=409 if error else 200)
