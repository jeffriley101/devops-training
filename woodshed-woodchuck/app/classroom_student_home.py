"""Owned student home over existing enrollment, capability and Weekly Loop readers.

No roster, reporting, sharing, issuance, settlement or reward writer lives here.
Each Class is read in its own Program transaction, in the existing lock order.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from . import classroom as s1, classroom_s2 as s2, classroom_directors_choice as choice
from . import classroom_enrollment_routes as enrollment, classroom_enrollment_views as views
from . import classroom_capabilities as capabilities, weekly_plans, weekly_routes, weekly_tasks
from .age_privacy import eligible
from .account_routes import SESSION_PROFILE_ID
from .classroom_models import ClassroomClass, ClassroomStudentMembership
from .content import GOAL_OPTIONS
from .db import SessionLocal
from .feature_access import can_use_feature
from .membership_routes import PrivateRoute
from .session_revocations import NONCE, RevokedBrowserSession, digest
from .weekly import utc_instant
from .weekly_homework import weekly_instrument
from .weekly_models import WeeklyInstrumentPlan

router = APIRouter(route_class=PrivateRoute)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / 'templates'))


def _student(session, request, expected=None):
    proof = enrollment._student(request, session, expected)
    nonce = request.session.get(NONCE)
    if not isinstance(nonce, str) or session.get(RevokedBrowserSession, digest(nonce), populate_existing=True):
        raise HTTPException(401, 'Sign-in changed. Reopen Classroom.')
    return proof


def _profile(session, request, expected=None):
    try:
        return s2._student(session, _student(session, request, expected), require_eligibility=False)
    except s1.ClassroomDenied as error:
        raise HTTPException(401, 'Sign-in changed. Reopen Classroom.') from error


def _classes(request, account, before):
    with SessionLocal() as session:
        profile = _profile(session, request, account)
        query = select(ClassroomStudentMembership.id, ClassroomClass.program_id,
                       ClassroomStudentMembership.class_id).join(ClassroomClass,
            ClassroomClass.id == ClassroomStudentMembership.class_id).where(
                ClassroomStudentMembership.profile_id == profile.id)
        if before is not None:
            query = query.where(ClassroomStudentMembership.id < before)
        rows = session.execute(query.order_by(ClassroomStudentMembership.id.desc()).limit(views.PAGE_SIZE + 1)).all()
    result = []
    for _, program_id, class_id in rows[:views.PAGE_SIZE]:
        with SessionLocal() as session:
            try:
                proof = _student(session, request, account)
                item = views.student_membership(session, student=proof, program_id=program_id, class_id=class_id)
                # This Classroom-only capability cannot be rescued by personal
                # Full, another Class or another Program. Never serialize sources.
                item['membership']['access'] = capabilities.capability_decision(session, proof.profile_id,
                    'note-names', program_id=program_id, class_id=class_id).allowed
                if choice.enabled() and item['membership']['access']:
                    item['choice'] = choice.student_page(session, student=proof, program_id=program_id, class_id=class_id)
                _student(session, request, account)
            except s1.ClassroomDenied as error:
                raise HTTPException(409, 'Your Class status could not be verified. Reopen Classroom.') from error
            result.append(item)
    return result, rows[views.PAGE_SIZE - 1].id if len(rows) > views.PAGE_SIZE else None


def _homework(session, request, profile):
    """Narrow saved Homework projection; reuse canonical generation/progress elsewhere."""
    if not eligible(session, profile.id) or not can_use_feature(session, profile.id, 'weekly_plans'):
        return {'available': False}
    profile = weekly_routes._authorized(request, session)
    instant = utc_instant()
    plan = weekly_plans.load_current_plan(session, profile.id, now=instant)
    try:
        instrument = weekly_instrument(profile.instrument)
    except ValueError:
        instrument = None
    result = {'available': True, 'instrument': instrument, 'game_mode': profile.game_mode,
              'plan': None, 'assignments': [], 'start_context': None, 'can_start': False}
    if plan is not None:
        result['plan'] = {'week_start': plan.week_start.isoformat(), 'focus': plan.focus,
                         'game_mode': plan.issued_game_mode, 'target_minutes': plan.homework_minutes_target}
        rows = session.scalars(select(WeeklyInstrumentPlan).where(
            WeeklyInstrumentPlan.plan_id == plan.id).order_by(WeeklyInstrumentPlan.slot_no)).all()
        for row in rows:
            result['assignments'].append({'slot': row.slot_no, 'instrument': row.instrument,
                'homework': row.homework_spec,
                'progress': weekly_tasks.homework_assignment_progress(session, profile.id, plan, row, now=instant)})
        result['start_context'] = weekly_plans.start_context(profile, plan)
        result['can_start'] = bool(result['start_context'] and len(rows) < 2
                                  and all(row.instrument != instrument for row in rows))
    weekly_routes._authorized(request, session, refresh=True)
    return result


@router.get('/classroom')
def home(request: Request):
    enrollment._enabled()
    before = enrollment._cursor(request)
    with SessionLocal() as session:
        try:
            profile = _profile(session, request)
        except HTTPException as error:
            if (error.status_code != 401 or request.session.get(SESSION_PROFILE_ID) is not None
                    or request.headers.get('x-woodshed-account') is not None):
                raise
            profile = None
        if profile is None:
            account = None
        else:
            account = profile.woodchuck_id
    members, next_before = _classes(request, account, before) if account else ([], None)
    with SessionLocal() as session:
        if account:
            profile = _profile(session, request, account)
            ready = eligible(session, profile.id)
            homework = _homework(session, request, profile)
            _student(session, request, account)
        else:
            ready, homework = False, {'available': False}
    # Standalone rendering avoids the ordinary home renderer's opening rewards.
    return templates.TemplateResponse(request=request, name='classroom_student_home.html', context={
        'account': account, 'ready': ready, 'members': members, 'next_before': next_before,
        'homework': homework, 'foci': GOAL_OPTIONS, 'choice_enabled': choice.enabled()})
