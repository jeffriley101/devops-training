"""Same-origin, PIN-confirmed director forms and owned student assignment views."""
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from . import classroom as s1, classroom_directors_choice as service
from . import classroom_enrollment_routes as enrollment, classroom_enrollment_views as views
from .content import GOAL_OPTIONS
from .db import SessionLocal
from .instruments import INSTRUMENT_DEFINITIONS
from .membership_routes import PrivateRoute
from .session_revocations import NONCE, RevokedBrowserSession, digest
from .site_admin import csrf_token
from .verifier_routes import current_verifier

PATH = '/classroom/directors-choice'
CLASS = PATH + '/{program_id}/classes/{class_id}'
STUDENT = PATH + '/student/{program_id}/classes/{class_id}'
router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / 'templates'))


def _enabled(request, *, student=False):
    if not service.enabled():
        raise HTTPException(404, 'Director’s Choice unavailable.')
    pairs = list(request.query_params.multi_items())
    if len(pairs) != len(request.query_params) or set(request.query_params) - ({'week', 'before'} if student else set()):
        raise HTTPException(400, 'Use the assignment controls.')


def _nonce(request, session):
    nonce = request.session.get(NONCE)
    if not isinstance(nonce, str) or session.get(RevokedBrowserSession, digest(nonce), populate_existing=True):
        raise HTTPException(401, 'Sign-in changed. Reopen Classroom.')


def _page(request, **context):
    with SessionLocal() as session:
        adult = current_verifier(request, session)
        director = {'id': adult.id, 'name': adult.display_name} if adult else None
    response = templates.TemplateResponse(request=request, name='classroom_directors_choice.html',
        status_code=context.pop('status_code', 200), context={'director': director, 'csrf': csrf_token(request),
        'current_week': service.week().isoformat(), 'request_key': uuid4().hex, 'foci': GOAL_OPTIONS,
        'instruments': INSTRUMENT_DEFINITIONS, 'modes': ('Beginner', 'Challenge', 'Performance'), **context})
    response.headers.update({'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY'})
    return response


@router.get(PATH)
@router.get(CLASS)
def enter(request: Request, program_id: int | None = None, class_id: int | None = None):
    _enabled(request)
    for value in (program_id, class_id):
        if value is not None:
            enrollment._number(str(value))
    return _page(request, student=False, class_entry=class_id is not None)


@router.post(PATH)
async def classes(request: Request):
    _enabled(request)
    form = await enrollment._form(request, max_bytes=16384)
    enrollment._exact(form, {'director', 'pin'})
    with SessionLocal() as session:
        _, proof = enrollment._adult(request, session, form)
        _nonce(request, session)
        hints = views.director_classes(session, actor=proof)
    available = []
    for hint in hints:
        with SessionLocal() as session:
            try:
                proof = s1.authenticate_adult(session, email=current_verifier(request, session).email, pin=form['pin'])
                names = service.director_class(session, actor=proof, program_id=hint['program_id'], class_id=hint['class_id'])
                s1._actor(session, proof)
                _nonce(request, session)
            except s1.ClassroomDenied:
                continue
            available.append(names)
    return _page(request, student=False, classes=available)


@router.post(CLASS)
async def manage(request: Request, program_id: int, class_id: int):
    _enabled(request)
    enrollment._number(str(program_id))
    enrollment._number(str(class_id))
    form = await enrollment._form(request, max_bytes=16384)
    action = form.get('action')
    fields = {'director', 'pin', 'action'}
    if action == 'view':
        fields |= {'week', 'before'}
    elif action == 'history':
        fields |= {'assignment', 'before'}
    elif action == 'cancel':
        fields |= {'assignment', 'version'}
    elif action in {'create', 'edit'}:
        fields |= {'week', 'request_key'} if action == 'create' else {'assignment', 'version'}
        fields |= {'kind', 'title', 'instructions'} if form.get('kind') == 'custom' else {'kind', 'instrument', 'focus', 'mode'}
    else:
        raise HTTPException(400, 'Choose an assignment action.')
    enrollment._exact(form, fields)
    with SessionLocal() as session:
        _, proof = enrollment._adult(request, session, form)
        _nonce(request, session)
        args = {'actor': proof, 'program_id': program_id, 'class_id': class_id}
        try:
            history = None
            if action in {'create', 'edit', 'cancel'}:
                data = {key: form[key] for key in ('kind', 'title', 'instructions', 'instrument', 'focus', 'mode') if key in form}
                if action == 'create':
                    saved = service.create(session, **args, week_start=form['week'], request_key=form['request_key'], fields=data)
                else:
                    saved = service.change(session, **args, assignment_id=enrollment._number(form['assignment']),
                        expected_version=enrollment._number(form['version']), cancel=action == 'cancel',
                        fields=None if action == 'cancel' else data)
                selected = saved['week']
                before = None
            elif action == 'history':
                history = service.history(session, **args, assignment_id=enrollment._number(form['assignment']),
                    before=enrollment._number(form['before'], optional=True))
                selected, before = history['week'], None
            else:
                selected, before = form['week'], enrollment._number(form['before'], optional=True)
            detail = service.director_page(session, **args, week_start=selected, before=before)
            s1._actor(session, proof)
            _nonce(request, session)
            session.commit()
        except (s1.ClassroomDenied, IntegrityError) as error:
            session.rollback()
            message = str(error) if isinstance(error, s1.ClassroomDenied) else 'The assignment changed. Reopen the Class.'
            return _page(request, student=False, class_entry=True, error=message, status_code=409)
    return _page(request, student=False, class_entry=True, detail=detail, history=history,
        message='Assignment saved.' if action in {'create', 'edit', 'cancel'} else None)


@router.get(STUDENT)
def student_page(request: Request, program_id: int, class_id: int):
    _enabled(request, student=True)
    enrollment._number(str(program_id))
    enrollment._number(str(class_id))
    from .classroom_student_home import _student
    with SessionLocal() as session:
        proof = _student(session, request)
        try:
            detail = service.student_page(session, student=proof, program_id=program_id, class_id=class_id,
                week_start=request.query_params.get('week'), before=enrollment._number(request.query_params.get('before'), optional=True))
            _student(session, request)
        except s1.ClassroomDenied as error:
            raise HTTPException(403, service.UNAVAILABLE) from error
    return _page(request, student=True, detail=detail)
