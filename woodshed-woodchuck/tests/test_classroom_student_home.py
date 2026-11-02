"""Student-owned Classes and real Weekly Loop Homework on migrated SQLite/PG.

Positive consent uses the existing signed synthetic KWS + native parent flow.
Homework starts through the actual Weekly Loop HTTP writers.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import re
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text

from app import classroom_student_home as home, classroom_s2 as s2, classroom_guardian_authorization as guardian
from app import classroom_school_authorization as school, classroom_chart_consent as chart_consent
from app import weekly_plans as plans, weekly_routes as weekly, child_authorization
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence
from app.classroom_models import ClassroomMembershipPeriod, ClassroomStudentMembership
from app.feature_access import FEATURES, Feature
from app.db import Base
from app.main import app
from app.models import PracticeChart, WoodchuckProfile
from app.weekly import utc_instant
from app.weekly_homework import homework_spec
from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan
from test_classroom_guardian_authorization import (
    guardian_data, migrated_db, captured, adult_pin_hash, actor, student,
    parent_accept, parent_proof, fully_authorize, read,
)
from test_classroom_s2 import institutional, create, code

PATH = '/classroom'
FOCI = ('Build daily consistency', 'Improve tone and control', 'Learn songs faster', 'Strengthen improvisation')


def rows(engine):
    # The fixture migrates the complete accepted schema. Use the actual model
    # tables directly to compare every persisted value without reflection noise.
    with engine.connect() as connection:
        return {table.name: list(connection.execute(select(table).order_by(*table.primary_key)).mappings())
                for table in Base.metadata.sorted_tables}


@pytest.fixture
def student_home(guardian_data, monkeypatch):
    refs = guardian_data
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(True, 'open'))
    for module in (home, plans, weekly):
        monkeypatch.setattr(module, 'utc_instant', lambda value=None: utc_instant(value or refs.clock[0]))
    with refs.factory() as session:
        session.get(WoodchuckProfile, refs.profile_id).game_mode = 'Challenge'
        session.commit()
    return refs


def page(refs, **kwargs):
    response = refs.child.get(PATH, **kwargs)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    return response.text


def issue(refs, focus=FOCI[0]):
    response = refs.child.post('/weekly/plan', json={'focus': focus}, headers={
        'Origin': 'https://testserver', 'x-woodshed-account': refs.woodchuck_id})
    assert response.status_code == 200, response.text
    return response.json()


def start(refs):
    context = refs.child.get('/weekly/plan').json()['start_context']
    response = refs.child.post('/weekly/plan/instruments/current/start', json={'start_context': context},
        headers={'Origin': 'https://testserver', 'x-woodshed-account': refs.woodchuck_id})
    assert response.status_code == 200, response.text
    return response.json()['assignment']


def allowed_class(refs):
    with refs.factory() as session:
        parent_accept(session, refs, 'participation'); session.commit()


def class_fragment(body, class_id):
    return re.search(r'<article class="student-class" data-class-id="'+str(class_id)+r'".*?</article>', body, re.S)[0]


def test_get_is_owned_read_only_and_never_issues_shares_reports_or_rewards(student_home):
    refs = student_home
    before = rows(refs.factory.engine)
    body = page(refs)
    assert 'Guardian synthetic Class' in body and 'data-membership-status>Member' in body
    assert 'Learning access currently unavailable' in body  # Membership alone is insufficient.
    assert 'No Focus or Homework has been saved' in body
    assert '0 /' not in body and 'qualification met' not in body
    assert 'data-homework-focus' in body and 'data-homework-start' not in body
    for secret in ('saved A', 'saved B', 'parent-a@example.test', 'WC-AGE-B', 'recent_results', 'homework_reward_ref'):
        assert secret not in body
    assert rows(refs.factory.engine) == before
    assert not school.LEGALLY_APPROVED_AGREEMENTS and not chart_consent.LEGALLY_APPROVED_POLICIES


def test_explicit_focus_start_saved_specs_and_actual_submission_evidence(student_home, monkeypatch):
    refs = student_home
    saved = issue(refs, FOCI[1]); before = rows(refs.factory.engine)
    body = page(refs)
    assert 'Instrument Homework has not been started' in body and 'data-homework-start' in body
    assert rows(refs.factory.engine) == before
    assignment = start(refs)
    with refs.factory() as session:
        plan = session.get(WeeklyPlan, saved['plan']['id'])
        assert assignment['homework'] == homework_spec(plan, 'Flute')
        # Wrong instrument, old submission, future submission and other-owner
        # charts must never become this student's matching Homework activity.
        for instrument, submitted, owner in [
            ('Flute', refs.clock[0]-timedelta(seconds=1), refs.profile_id),
            ('Tuba', refs.clock[0]+timedelta(seconds=1), refs.profile_id),
            ('Flute', refs.clock[0]+timedelta(days=1), refs.profile_id),
            ('Flute', refs.clock[0]+timedelta(seconds=1), refs.profile_id+1),
        ]:
            session.add(PracticeChart(profile_id=owner, practice_date=refs.clock[0].date(),
                created_at=submitted, source='p-book', minutes=900, instrument=instrument,
                note='PRIVATE HOMEWORK NOTE', practice_details=['PRIVATE DETAIL']))
        session.commit()
    refs.clock[0] += timedelta(seconds=2)
    body = page(refs)
    assert '0 / 15 minutes of matching submitted practice' in body
    assert 'Weekly Loop qualification not yet met' in body
    with refs.factory() as session:
        session.add(PracticeChart(profile_id=refs.profile_id, practice_date=refs.clock[0].date(),
            created_at=refs.clock[0], source='pristine', minutes=1, detected_playing_seconds=901,
            instrument='Flute', note='PRIVATE HOMEWORK NOTE', practice_details=['PRIVATE DETAIL']))
        session.commit()
    # Read only saved work: changing current preferences cannot regenerate it.
    with refs.factory() as session:
        profile = session.get(WoodchuckProfile, refs.profile_id)
        profile.game_mode = 'Performance'; profile.instrument = 'Tuba'; session.commit()
    def forbidden(*args, **kwargs): raise AssertionError('A page read must not regenerate saved Homework')
    monkeypatch.setattr('app.weekly_homework.homework_spec', forbidden)
    before = rows(refs.factory.engine); body = page(refs)
    assert '15.0167 / 15 minutes of matching submitted practice' in body
    assert 'Weekly Loop qualification met' in body and 'data-issued-mode>Challenge' in body
    assert assignment['homework']['title'] in body and 'PRIVATE HOMEWORK NOTE' not in body and 'PRIVATE DETAIL' not in body
    assert 'homework_reward_ref' not in body and rows(refs.factory.engine) == before


@pytest.mark.parametrize('mode', ['Beginner', 'Challenge', 'Performance'])
def test_all_existing_focus_mode_instrument_recipes_and_two_slot_limit(student_home, mode):
    refs = student_home
    for focus in FOCI:
        with refs.factory() as session:
            profile = session.get(WoodchuckProfile, refs.profile_id)
            profile.game_mode = mode; profile.instrument = 'Flute'; session.commit()
        issue(refs, focus); first = start(refs)
        with refs.factory() as session:
            session.get(WoodchuckProfile, refs.profile_id).instrument = 'Clarinet'; session.commit()
        second = start(refs)
        with refs.factory() as session:
            session.get(WoodchuckProfile, refs.profile_id).instrument = 'Tuba'; session.commit()
        before = rows(refs.factory.engine); body = page(refs)
        assert focus in body and f'data-issued-mode>{mode}' in body
        assert first['homework']['title'] in body and second['homework']['title'] in body
        assert 'data-homework-start' not in body and 'Both weekly instrument slots are saved' in body
        assert rows(refs.factory.engine) == before
        refs.clock[0] += timedelta(weeks=1)
    assert 'No Focus or Homework has been saved' in page(refs)


@pytest.mark.parametrize('loss', ['participation', 'entitlement', 'leave', 'suspend', 'archive'])
def test_current_class_authority_loss_preserves_membership_and_independent_homework(student_home, loss):
    refs = student_home; allowed_class(refs); issue(refs); start(refs)
    assert 'Learning access available' in class_fragment(page(refs), refs.class_id)
    refs.clock[0] += timedelta(seconds=2)  # A real membership period must have positive duration.
    with refs.factory() as session:
        if loss == 'participation':
            guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
                class_id=refs.class_id, purpose='participation')
        elif loss == 'entitlement':
            institutional(session, status='ended', starts_at=refs.clock[0], ends_at=refs.clock[0]+timedelta(days=1))
        elif loss == 'leave':
            s2.leave_class(session, student=student(session), program_id=1, class_id=refs.class_id)
        elif loss == 'suspend':
            s2.suspend_member(session, actor=actor(session), program_id=1, class_id=refs.class_id,
                membership_id=refs.membership_id)
        else:
            s2.set_class_state(session, actor=actor(session), program_id=1, class_id=refs.class_id, state='archived')
        session.commit()
    body = page(refs); fragment = class_fragment(body, refs.class_id)
    assert 'Learning access currently unavailable' in fragment and 'Open learning tools' not in fragment
    assert {'leave':'Not enrolled', 'suspend':'Suspended'}.get(loss,'Member') in fragment
    assert 'data-homework-slot="1"' in body  # Existing Open Weekly Loop is independent.


def test_cross_class_cross_program_and_other_account_are_never_authority_substitutes(student_home, monkeypatch):
    refs = student_home; allowed_class(refs)
    with refs.factory() as session:
        sibling = create(session, name='Same Program joined Class'); code(session, sibling.id, 'MORE2026'); session.commit()
        s2.join_class(session, student=student(session), program_id=1, code='MORE2026'); session.commit()
        institutional(session, program_id=2, starts_at=refs.clock[0], ends_at=refs.clock[0]+timedelta(days=365)); session.commit()
        extra = create(session, program_id=2, name='Other Program joined Class'); code(session, extra.id, 'OTHER2026', program_id=2); session.commit()
        s2.join_class(session, student=student(session), program_id=2, code='OTHER2026'); session.commit()
        hidden = create(session, program_id=2, name='UNJOINED SECRET CLASS'); session.commit()
        sibling_id, extra_id, hidden_id = sibling.id, extra.id, hidden.id
    body = page(refs)
    assert 'Learning access available' in class_fragment(body, refs.class_id)
    assert 'Learning access currently unavailable' in class_fragment(body, sibling_id)
    assert 'Learning access currently unavailable' in class_fragment(body, extra_id)
    assert 'UNJOINED SECRET CLASS' not in body
    for selector in ('profile_id=2', f'class_id={hidden_id}', 'program_id=2', 'before=0', 'before=1&before=2', 'before=wat'):
        response = refs.child.get(PATH+'?'+selector)
        assert response.status_code == 400 and 'UNJOINED SECRET CLASS' not in response.text
    assert refs.child.get(PATH, headers={'x-woodshed-account':'WC-AGE-B'}).status_code == 401
    other = TestClient(app)
    assert other.post('/account/login', data={'woodchuck_id':'WC-AGE-B','pin':'2468'}, headers={'Origin':'https://testserver'}).status_code == 200
    assert 'Guardian synthetic Class' not in other.get(PATH).text
    adult = TestClient(app)
    assert adult.post('/trusted-verifiers/login', data={'email':'adult2@example.test','pin':'2468'}, headers={'Origin':'https://testserver'}).status_code == 200
    assert 'Guardian synthetic Class' not in adult.get(PATH).text
    monkeypatch.setattr(home.views, 'PAGE_SIZE', 1)
    cursor = ''
    names = []
    for _ in range(3):
        body = refs.child.get(PATH+cursor).text
        names.extend(re.findall(r'<h3>(.*?)</h3>', body))
        match = re.search(r'/classroom\?before=([0-9]+)', body)
        cursor = '?before='+match[1] if match else ''
    assert names == ['Other Program joined Class', 'Same Program joined Class', 'Guardian synthetic Class']


@pytest.mark.parametrize('restriction', ['unknown-age', 'unsupported-instrument', 'no-game-mode'])
def test_missing_requirements_are_unavailable_without_fabricated_homework(student_home, restriction):
    refs = student_home
    with refs.factory() as session:
        profile = session.get(WoodchuckProfile, refs.profile_id)
        if restriction == 'unknown-age':
            rule = session.get(AccountPrivacy, refs.profile_id)
            rule.age_band = 'unknown'; rule.public_from = None; rule.consent_id = None
        elif restriction == 'unsupported-instrument': profile.instrument = 'Unknown legacy instrument'
        else: profile.game_mode = None
        session.commit()
    before = rows(refs.factory.engine); body = page(refs)
    assert 'data-homework-start' not in body and 'data-homework-slot' not in body
    if restriction == 'unknown-age':
        assert 'Weekly Homework is currently unavailable' in body and 'data-homework-focus' not in body
    elif restriction == 'no-game-mode': assert 'Choose a Game Mode' in body and 'data-homework-focus' not in body
    else:
        assert 'Choose a supported instrument' in body
        issue(refs)
        body = page(refs)
        assert 'data-homework-start' not in body and 'Choose a supported instrument' in body
        before = rows(refs.factory.engine)
    assert rows(refs.factory.engine) == before


@pytest.mark.parametrize('guardian_data', [{'under13':True}, {'under13':False}], indirect=True)
def test_age_parent_and_feature_requirements_preserve_separate_permissions(student_home, monkeypatch):
    refs = student_home
    allowed_class(refs); issue(refs); start(refs)
    assert 'data-homework-slot="1"' in page(refs)
    with refs.factory() as session:
        assert session.scalar(select(ClassroomMembershipPeriod)).ended_at is None
        with pytest.raises(s2.ClassroomDenied): read(session, refs)
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(False, 'open'))
    assert 'Weekly Homework is currently unavailable' in page(refs)
    monkeypatch.setitem(FEATURES, 'weekly_plans', Feature(True, 'open'))
    with refs.factory() as session:
        rule = session.get(AccountPrivacy, refs.profile_id)
        child_authorization.withdraw_evidence(session, session.get(ConsentEvidence, rule.consent_id)); session.commit()
    response = refs.child.get(PATH)
    assert 'data-homework-slot' not in response.text and 'data-homework-start' not in response.text
    assert refs.child.get('/weekly/plan').status_code in (401,403)


def test_revoked_browser_stale_context_and_unapproved_actions_remain_closed(student_home, monkeypatch):
    refs = student_home; saved = issue(refs)
    before = rows(refs.factory.engine)
    for path, data in [('/weekly/plan', {'focus':FOCI[0], 'profile_id':2}),
                       ('/weekly/plan/instruments/current/start', {'start_context':saved['start_context'],'completion':True})]:
        assert refs.child.post(path, json=data, headers={'Origin':'https://testserver'}).status_code == 422
        assert refs.child.post(path, json={k:v for k,v in data.items() if k not in ('profile_id','completion')},
            headers={'Origin':'https://foreign.invalid'}).status_code == 403
    refs.clock[0] += timedelta(weeks=1)
    assert refs.child.post('/weekly/plan/instruments/current/start', json={'start_context':saved['start_context']},
        headers={'Origin':'https://testserver'}).status_code == 409
    assert rows(refs.factory.engine) == before
    cookie = refs.child.cookies.get('session')
    assert refs.child.post('/account/logout').status_code == 200
    replay = TestClient(app); replay.cookies.set('session',cookie)
    assert 'Guardian synthetic Class' not in replay.get(PATH).text
    monkeypatch.delenv('CLASSROOM_ENROLLMENT_V2_ENABLED')
    response = replay.get(PATH)
    assert response.status_code == 404 and response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('rollback', [False, True])
def test_pg_home_waits_for_parent_withdrawal_and_observes_commit(student_home, rollback):
    refs = student_home; allowed_class(refs)
    ready, release = Event(), Event(); pids = []
    def withdraw():
        with refs.factory() as session:
            pid = session.scalar(text('select pg_backend_pid()')); pids.append(pid)
            guardian.withdraw(session, parent=parent_proof(session, refs), program_id=1,
                class_id=refs.class_id, purpose='participation')
            ready.set(); assert release.wait(15)
            session.rollback() if rollback else session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(withdraw); assert ready.wait(10)
        second = pool.submit(page, refs)
        try:
            with refs.factory.control_engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                deadline = time.monotonic()+10
                while time.monotonic()<deadline:
                    if observer.scalar(text('select exists(select 1 from pg_stat_activity where :pid = any(pg_blocking_pids(pid)))'), {'pid':pids[0]}): break
                    time.sleep(.02)
                else: pytest.fail('No actual PostgreSQL reader wait observed')
        finally: release.set()
        first.result(timeout=15); body = second.result(timeout=15)
    fragment = class_fragment(body, refs.class_id)
    assert ('Learning access available' if rollback else 'Learning access currently unavailable') in fragment


@pytest.mark.parametrize('migrated_db', ['postgresql'], indirect=True)
@pytest.mark.parametrize('change', ['native-withdrawal', 'credentials', 'nonce'])
def test_pg_native_authority_rechecked_after_profile_wait(student_home, change):
    refs = student_home; issue(refs); start(refs)
    ready, release = Event(), Event(); pids = []
    cookie = refs.child.cookies.get('session')
    def invalidate():
        with refs.factory() as session:
            # Follow the native consent -> profile order for the actual evidence
            # mutation. Other cases hold only the existing profile writer fence.
            if change == 'native-withdrawal':
                evidence = session.scalar(select(ConsentEvidence).where(ConsentEvidence.profile_id == refs.profile_id).with_for_update())
            profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == refs.profile_id).with_for_update())
            pids.append(session.scalar(text('select pg_backend_pid()')))
            if change == 'native-withdrawal': child_authorization.withdraw_evidence(session, evidence)
            elif change == 'credentials': profile.session_version += 1; session.flush()
            ready.set(); assert release.wait(15); session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invalidate); assert ready.wait(10)
        second = pool.submit(refs.child.get, PATH)
        try:
            with refs.factory.control_engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                deadline = time.monotonic()+10
                while time.monotonic()<deadline:
                    if observer.scalar(text('select exists(select 1 from pg_stat_activity where :pid = any(pg_blocking_pids(pid)))'), {'pid':pids[0]}): break
                    time.sleep(.02)
                else: pytest.fail('No actual PostgreSQL native-authority wait observed')
            if change == 'nonce':
                from itsdangerous import TimestampSigner
                from base64 import b64decode
                import json
                from app.main import SESSION_SECRET
                from app.session_revocations import NONCE, retire
                payload = json.loads(b64decode(TimestampSigner(SESSION_SECRET).unsign(cookie)))
                retire(payload[NONCE])
        finally: release.set()
        first.result(timeout=15); response = second.result(timeout=15)
    assert response.status_code == 401
    assert 'Guardian synthetic Class' not in response.text and 'data-homework-slot' not in response.text
    assert response.headers['cache-control'] == 'no-store'
