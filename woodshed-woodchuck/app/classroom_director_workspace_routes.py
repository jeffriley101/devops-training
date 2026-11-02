"""PIN-confirmed director workspace; Connected Students remains a separate app."""
from pathlib import Path
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates

from . import classroom as s1, classroom_identity, classroom_management_routes as management
from . import classroom_management_views, classroom_director_workspace as service
from .classroom_enrollment_routes import _adult, _number
from .db import SessionLocal
from .membership_routes import PrivateRoute
from .site_admin import csrf_token
from .verifier_routes import current_verifier

PATH = '/classroom/dashboard'
CLASS = PATH + '/{program_id}/classes/{class_id}'
STUDENT = CLASS + '/students/{membership_id}'
router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent/'templates'))


def enabled():
    return management.enabled() and classroom_identity.enabled()


def _enabled(request):
    if not enabled():
        raise HTTPException(404, 'Director Dashboard unavailable.')
    if request.query_params:
        raise HTTPException(400, 'Use the dashboard controls.')


def _page(request, **context):
    with SessionLocal() as session:
        verifier = current_verifier(request, session)
        director = {'id': verifier.id, 'name': verifier.display_name} if verifier else None
    response = templates.TemplateResponse(request=request, name='classroom_director_workspace.html',
        context={'title': 'Classroom Director Dashboard', 'director': director, 'csrf': csrf_token(request), **context},
        status_code=context.get('status_code',200))
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@router.get(PATH)
@router.get(CLASS)
@router.get(STUDENT)
def enter(request: Request, program_id: int | None=None, class_id: int | None=None, membership_id: int | None=None):
    _enabled(request)
    for value in (program_id, class_id, membership_id):
        if value is not None: _number(str(value))
    return _page(request, student_page=membership_id is not None)


@router.post(PATH)
@router.post(CLASS)
@router.post(STUDENT)
async def open_workspace(request: Request, program_id: int | None=None, class_id: int | None=None, membership_id: int | None=None):
    _enabled(request)
    for value in (program_id, class_id, membership_id):
        if value is not None:_number(str(value))
    form = await management._form(request)
    optional_week = {'week'} if membership_id is not None and 'week' in form else set()
    management._exact(form, {'director','pin'} | (optional_week if membership_id is not None else {'before'}))
    selected_week = None
    if membership_id is not None and form.get('week'):
        try:
            selected_week = date.fromisoformat(form['week'])
        except (ValueError, TypeError):
            raise HTTPException(400, 'Choose a reporting week beginning Monday.')
        if selected_week.weekday() != 0:
            raise HTTPException(400, 'Choose a reporting week beginning Monday.')
    data = catalog = None
    with SessionLocal() as session:
        _, actor = _adult(request, session, form)
        try:
            if program_id is None:
                catalog = classroom_management_views.catalog(session, actor=actor, before=_number(form['before'],optional=True))
            elif membership_id is None:
                data = service.class_view(session, actor=actor, program_id=program_id, class_id=class_id,
                    before=_number(form['before'],optional=True))
            else:
                data = service.student_view(session, actor=actor, program_id=program_id, class_id=class_id,
                    membership_id=membership_id, selected_week=selected_week)
        except s1.ClassroomDenied:
            failed=True
        else:
            failed=False
    if failed:
        return _page(request, student_page=membership_id is not None, status_code=404,
            error='Class or student access is unavailable. Reopen the dashboard with your current account.')
    return _page(request, data=data, catalog=catalog, student_page=membership_id is not None)
