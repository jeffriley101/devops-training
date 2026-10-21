"""Owned Newsletter GETs on fresh actual migrations; no UI or reward settlement.

Historical rows below are explicitly synthetic persisted history, not a claim
that a finalizer or browser journey was executed by these projection tests.
"""
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import models as m, weekly_newsletter as newsletter, weekly_plans as plans, weekly_routes
from app.age_privacy import declare_age
from app.feature_access import FEATURES, Feature
from app.main import app
from app.security import hash_pin
from app.weekly import CENTRAL, utc_instant
from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan, WeeklyTaskClaim
from tests.test_weekly2_foundation_migration import config, rows
from tests.test_weekly2_thirds import arcade_factory
from tests.test_weekly2_game_mode import assert_only_session_retirement

PATH = '/weekly/newsletter'
START = date(2026, 10, 19)
PREVIOUS = START - timedelta(days=7)
NOW = datetime(2026, 10, 19, 13, 30, tzinfo=CENTRAL)
FOCUS = 'Build daily consistency'


@pytest.fixture(params=['sqlite', 'postgresql'])
def db(request, tmp_path, monkeypatch):
    """Exact private harness root/socket required; never use application databases."""
    root = Path(os.environ['WW_NEWSLETTER_TEST_ROOT'])
    assert root.is_absolute() and root.resolve(strict=True) == root
    assert root.stat().st_uid == os.getuid() and root.stat().st_mode & 0o777 == 0o700
    assert tmp_path.is_relative_to(root)
    if request.param == 'sqlite':
        target = tmp_path / 'newsletter.db'
        assert not target.exists()
        url = f'sqlite:///{target}'
    else:
        parsed = make_url(os.environ['WW_WEEKLY2_TEST_POSTGRES_URL'])
        socket = Path(os.environ['WW_NEWSLETTER_TEST_SOCKET'])
        assert parsed.drivername == 'postgresql+psycopg' and not parsed.host
        assert parsed.database.startswith('ww_weekly2_')
        assert set(parsed.query) == {'host', 'port'} and parsed.query['host'] == str(socket)
        assert socket.resolve(strict=True) == socket and socket.stat().st_uid == os.getuid()
        assert socket.stat().st_mode & 0o777 == 0o700 and socket.parent == root.parent
        control = create_engine(parsed)
        schema = 'wl2_' + uuid4().hex
        with control.begin() as connection:
            assert connection.scalar(text('SELECT inet_server_addr()')) is None
            assert connection.scalar(text('SHOW listen_addresses')) == ''
            assert connection.scalar(text('SELECT current_database()')) == parsed.database
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        control.dispose()
        url = parsed.update_query_dict({'options': f'-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=20000'}).render_as_string(hide_password=False)
    monkeypatch.setenv('DATABASE_URL', url)
    engine = create_engine(url)
    if request.param == 'sqlite':
        @event.listens_for(engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
    yield engine
    engine.dispose()  # Retain files/schemas; no fixture deletion.


@pytest.fixture
def runtime(db, monkeypatch):
    command.upgrade(config(), 'head')
    with db.connect() as connection:
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == 'w25weekly004'
    factory = sessionmaker(db, expire_on_commit=False, autoflush=False)
    factory.engine = db
    factory.clock = [NOW]
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    for key in FEATURES:
        if key.startswith('weekly_'):
            monkeypatch.setitem(FEATURES, key, Feature(False, 'open'))
    monkeypatch.setitem(FEATURES, 'weekly_newsletter', Feature(True, 'open'))
    monkeypatch.setattr(weekly_routes, 'utc_instant', lambda value=None: utc_instant(value or factory.clock[0]))
    with factory.begin() as session:
        for pid in (1, 2):
            session.add(m.WoodchuckProfile(id=pid, woodchuck_id=f'WL2-NEWS-{pid}',
                display_name=f'PRIVATE IDENTITY {pid}', pin_hash=hash_pin('2468'),
                instrument='Flute', level='Advanced', goal='Legacy goal', game_mode=None))
        session.flush()
        for pid in (1, 2):
            declare_age(session, pid, 'adult')
            session.add(m.WoodchuckState(profile_id=pid, revision=7,
                state_json={'progress': {'credits': 250}, '_weekly_preferences': {'game_mode': 'Performance'}}))
    def client(pid=1):
        result = TestClient(app)
        assert result.post('/account/login', data={'woodchuck_id': f'WL2-NEWS-{pid}', 'pin': '2468'}).status_code == 200
        return result
    factory.client = client
    return factory


def assert_safe(value):
    forbidden = {'rating', 'delta', 'rating_value', 'rating_delta', 'rating_week_values',
                 'practice_rating', 'score', 'notes', 'note', 'profile_id', 'display_name',
                 'display_name_snapshot', 'recent_results', 'generation_snapshot', 'request_key'}
    if isinstance(value, dict):
        assert not forbidden.intersection(value), value
        for item in value.values():
            assert_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_safe(item)


def read_unchanged(factory, client, *, status=200):
    before = rows(factory.engine)
    statements = []
    def capture(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}:
            statements.append(statement)
    event.listen(factory.engine, 'before_cursor_execute', capture)
    try:
        response = client.get(PATH)
    finally:
        event.remove(factory.engine, 'before_cursor_execute', capture)
    assert response.status_code == status, response.text
    assert not statements, statements
    assert rows(factory.engine) == before
    assert 'no-store' in response.headers['cache-control']
    if status == 200:
        assert 'private' in response.headers['cache-control']
        assert_safe(response.json())
    return response.json()


def saved_plan(factory, monkeypatch, *, week=START, mode='Challenge', pid=1):
    # Use the sealed writer/generator to create real saved plans/assignments;
    # gates are enabled only during this synthetic setup.
    with monkeypatch.context() as local:
        local.setitem(FEATURES, 'weekly_plans', Feature(True, 'open'))
        with factory.begin() as session:
            profile = session.get(m.WoodchuckProfile, pid)
            profile.game_mode = mode
            session.flush()
            instant = datetime.combine(week, datetime.min.time(), CENTRAL) + timedelta(hours=8)
            plan = plans.issue_current_plan(session, profile, FOCUS, now=instant)
            plans.start_current_instrument(session, profile, plans.start_context(profile, plan), now=instant)
            return plan.id


def chart(pid, day, *, source='p-book', seconds=600, submitted=None):
    return m.PracticeChart(profile_id=pid, practice_date=day, minutes=seconds // 60,
        detected_playing_seconds=seconds if source == 'pristine' else None,
        source=source, instrument='Flute', note='PRIVATE CHART NOTE',
        include_contests=False, include_team_contests=False,
        created_at=utc_instant(submitted or datetime.combine(day, datetime.min.time(), CENTRAL) + timedelta(hours=10)))


def test_publication_calendar_boundaries_and_dst(record_property):
    cases = []
    for monday, offset in ((date(2026, 10, 19), '-05:00'), (date(2026, 3, 2), '-06:00'),
                           (date(2026, 3, 9), '-05:00'), (date(2026, 10, 26), '-05:00'),
                           (date(2026, 11, 2), '-06:00')):
        midnight = datetime.combine(monday, datetime.min.time(), CENTRAL)
        for minutes, seconds, available in ((0, 0, False), (809, 59, False), (810, 0, True)):
            value = midnight + timedelta(minutes=minutes, seconds=seconds)
            context = newsletter.publication_context(value)
            assert context['available'] is available and context['week_start'] == str(monday)
            assert context['recap_week_start'] == str(monday - timedelta(days=7))
            assert context['publication_at'].endswith(offset)
            assert newsletter.publication_context(value.astimezone(timezone.utc)) == context
            cases.append({'instant': value.isoformat(), **context})
        for days in range(1, 7):
            assert newsletter.publication_context(midnight + timedelta(days=days))['available']
        assert newsletter.publication_context(midnight + timedelta(days=6, hours=23, minutes=59, seconds=59))['available']
        next_week = newsletter.publication_context(midnight + timedelta(days=7))
        assert not next_week['available'] and next_week['week_start'] == str(monday + timedelta(days=7))
    with pytest.raises(ValueError):
        newsletter.publication_context(datetime(2026, 10, 19))
    record_property('boundary_examples', json.dumps(cases))


def test_default_gate_and_only_read_route():
    assert FEATURES['weekly_newsletter'] == Feature(False, 'open')
    routes = [route for route in app.routes if getattr(route, 'path', None) == PATH]
    assert len(routes) == 1 and routes[0].methods == {'GET'}


def test_gate_off_and_prepublication_do_not_read_weekly_storage(runtime, monkeypatch):
    with runtime.client() as client:
        before = rows(runtime.engine)
        seen = []
        def capture(connection, cursor, statement, parameters, context, many):
            if any(table in statement.lower() for table in ('weekly_plans', 'weekly_task_claims', 'weekly_wheel_plays', 'contest_results')):
                seen.append(statement)
        event.listen(runtime.engine, 'before_cursor_execute', capture)
        try:
            monkeypatch.setitem(FEATURES, 'weekly_newsletter', Feature(False, 'open'))
            assert client.get(PATH).status_code == 403
            monkeypatch.setitem(FEATURES, 'weekly_newsletter', Feature(True, 'open'))
            runtime.clock[0] = NOW - timedelta(seconds=1)
            result = client.get(PATH).json()
            assert result['context']['available'] is False
            assert result['recap'] is result['current'] is None
            assert not seen
        finally:
            event.remove(runtime.engine, 'before_cursor_execute', capture)
        assert rows(runtime.engine) == before
        read_unchanged(runtime, client)


def test_no_plan_morning_credit_late_credit_and_no_bonus(runtime, monkeypatch, record_property):
    with runtime.client() as client:
        with runtime.begin() as session:
            session.add_all([chart(1, START, seconds=300), chart(1, START, source='pristine', seconds=299),
                chart(1, START - timedelta(days=1), submitted=NOW - timedelta(hours=3)),
                chart(2, START, seconds=6000), chart(1, START, seconds=6000, submitted=NOW + timedelta(hours=1))])
        monkeypatch.setitem(FEATURES, 'weekly_monday_bonus', Feature(True, 'open'))
        result = read_unchanged(runtime, client)
        assert result['current']['plan'] is None and result['current']['tasks'] is None
        assert result['current']['game_mode'] is None  # Historical Level/JSON cannot choose it.
        assert result['current']['progress'] == {'practice_seconds': 599, 'practice_days': 1, 'logging_days': 1}
        assert result['recap']['practice']['practice_seconds'] == 600
        assert result['recap']['practice']['logging_days'] == 0
        assert result['current']['monday_bonus'] is None
        assert result['recap']['top_20']['status'] == 'unavailable'
        assert result['recap']['practice_rating_history']['status'] == 'unavailable'
        assert result['recap']['directors_choice']['status'] == 'unavailable'
        assert read_unchanged(runtime, client) == result
        record_property('no_plan_response', json.dumps(result))


@pytest.mark.parametrize('mode,minutes', [('Beginner', 30), ('Challenge', 60), ('Performance', 90)])
def test_saved_plan_homework_targets_remain_frozen(runtime, monkeypatch, mode, minutes, record_property):
    with runtime.client() as client:
        current = saved_plan(runtime, monkeypatch, mode=mode)
        previous = saved_plan(runtime, monkeypatch, week=PREVIOUS, mode=mode)
        with runtime.begin() as session:
            profile = session.get(m.WoodchuckProfile, 1)
            profile.game_mode = 'Beginner' if mode != 'Beginner' else 'Performance'
            profile.instrument = 'Clarinet'
            session.add(chart(1, START, seconds=300))
            assignment = session.get(WeeklyInstrumentPlan, (current, 1))
            assignment.homework_spec = {**assignment.homework_spec, 'rating': 99, 'notes': 'DO NOT EXPOSE'}
            session.get(WeeklyPlan, previous).recent_results = {'private_notes': 'DO NOT EXPOSE'}
        def forbidden(*args, **kwargs):
            raise AssertionError('Newsletter called a generator/writer')
        from app import weekly_homework, weekly_rewards, weekly_caper, weekly_wheel, contests, login_streaks
        for module, name in [(weekly_homework, 'homework_spec'), (plans, 'issue_current_plan'),
                             (plans, 'start_current_instrument'), (weekly_rewards, 'grant'),
                             (weekly_caper, 'claim_task'), (weekly_caper, 'claim_clue'),
                             (weekly_caper, 'claim_clover'), (contests, 'finalize_contest_week'),
                             (login_streaks, 'stage_monday_opening')]:
            monkeypatch.setattr(module, name, forbidden)
        result = read_unchanged(runtime, client)
        assert result['current']['plan']['id'] == current and result['recap']['plan']['id'] == previous
        assert result['current']['plan']['issued_game_mode'] == mode
        assert result['current']['plan']['targets']['practice_seconds'] == minutes * 60
        assert result['current']['active_instrument'] == 'clarinet'
        assert result['current']['plan_actions_available'] is False
        spec = result['current']['plan']['instruments'][0]['homework']
        assert spec['status'] == 'saved' and spec['instructions']
        assert 'DO NOT EXPOSE' not in json.dumps(result)
        assert result['current']['progress']['practice_seconds'] == 300
        record_property('saved_plan_response', json.dumps(result))


def test_previous_receipts_survive_spending_and_removed_evidence(runtime, monkeypatch, record_property):
    with runtime.client() as client:
        prior = saved_plan(runtime, monkeypatch, week=PREVIOUS)
        when = utc_instant(NOW - timedelta(days=1))
        with runtime.begin() as session:
            for number in range(1, 4):
                session.add(m.RewardGrant(profile_id=1, source_key=f'clover-caper:{PREVIOUS}:jar:{number}',
                    reward_type='pickled_carrot', amount=1, created_at=when))
            session.add(m.RewardGrant(profile_id=1, source_key='synthetic-spent-jars', reward_type='pickled_carrot', amount=-3, created_at=when))
            session.add_all([WeeklyTaskClaim(plan_id=prior, lane=lane, claimed_at=when) for lane in ('board', 'thirds')])
            for week in (PREVIOUS, START):
                session.add(m.RewardGrant(profile_id=1, source_key=f'weekly-monday:{week}', reward_type='dandelion', amount=75,
                    created_at=when if week == PREVIOUS else utc_instant(NOW)))
            session.add(m.RewardGrant(profile_id=1, source_key=f'clover-caper:{PREVIOUS}:clue', reward_type='caper_clue', amount=1, created_at=when))
            session.add(m.RewardGrant(profile_id=2, source_key=f'clover-caper:{PREVIOUS}:jar:1', reward_type='pickled_carrot', amount=1, created_at=when))
        result = read_unchanged(runtime, client)
        assert all(state['claimed'] and state['qualified'] for state in result['recap']['tasks'].values())
        assert result['recap']['tasks']['homework']['assignments'][0]['qualified'] is False
        assert result['recap']['clue']['claimed']
        assert [result['recap']['receipts'][lane]['amount'] for lane in ('logging', 'practice_minutes', 'homework')] == [1, 1, 1]
        assert result['current']['monday_bonus']['amount'] == 75
        assert read_unchanged(runtime, client) == result
        record_property('seeded_receipt_response', json.dumps(result))


def test_source_week_qualification_does_not_count_late_submission(runtime, monkeypatch):
    with runtime.client() as client:
        saved_plan(runtime, monkeypatch, week=PREVIOUS)
        with runtime.begin() as session:
            session.add(chart(1, START - timedelta(days=1), seconds=3600, submitted=NOW - timedelta(hours=2)))
        result = read_unchanged(runtime, client)
        assert result['recap']['practice']['practice_seconds'] == 3600  # Ordinary late credit retained.
        assert result['recap']['tasks']['practice_minutes']['progress'] == 0
        assert not result['recap']['tasks']['homework']['qualified']
        assert not result['recap']['tasks']['logging']['qualified']


def seed_contest(factory, *, persistent=False, status='finalized'):
    finalized = utc_instant(NOW - timedelta(hours=1))
    with factory.begin() as session:
        session.add(m.Season(id=1, key='news-synthetic', name='Synthetic season', starts_on=date(2026, 9, 1), status='closed'))
        session.add(m.TeamFamily(id=1))
        session.add(m.TrustedVerifier(id=1, email='newsletter@example.test', display_name='Synthetic adult', pin_hash=hash_pin('1357')))
        session.flush()
        from app.classroom_models import ClassroomProgram
        session.add(m.Organization(id=1, name='Synthetic classroom', organization_type='music', created_by_verifier_id=1))
        session.flush()
        session.add(ClassroomProgram(organization_id=1, owner_verifier_id=1))
        session.add(m.Team(id=1, season_id=1, family_id=1, display_name='PRIVATE TEAM', normalized_name='private team', emblem_key='synthetic'))
        session.add(m.Contest(id=1, key='news-own-practice', name='Synthetic practice', metric_type='practice_minutes', subject_type='student'))
        session.add(m.Contest(id=2, key='news-team-points', name='Synthetic Team', metric_type='points', subject_type='team'))
        week = m.ContestWeek(id=1, season_id=1, week_start=PREVIOUS, week_end=START,
            verification_deadline_at=finalized, finalize_after=finalized, status=status,
            finalized_at=finalized if status == 'finalized' else None,
            team_membership_rules_version='persistent_v1' if persistent else 'legacy_seasonal_v1',
            team_roster_frozen_at=finalized, finalizer_rules_version='contest_finalizer_persistent_v3' if persistent else 'contest_finalizer_v2',
            practice_scoring_mode='submitted_v2')
        session.add(week)
        session.flush()
        session.add(m.TeamWeekMembershipSnapshot(contest_week_id=1, profile_id=1, team_id=1, snapshot_at=finalized))
        for ident, pid, subject, contest in ((1, 1, 'student', 1), (2, 2, 'student', 1), (3, None, 'team', 2)):
            session.add(m.ContestResult(id=ident, contest_week_id=1, contest_id=contest, division='open', subject_type=subject,
                subject_key=str(pid) if pid else '1', profile_id=pid, team_id=1 if pid is None else None,
                display_name_snapshot='PRIVATE CHILD IDENTITY', score=99, rank=1, medal='gold', created_at=finalized))
        session.flush()
        session.add(m.RewardGrant(profile_id=1, contest_result_id=1, source_key='synthetic-contest-receipt',
                                 reward_type='dandelion', amount=50, created_at=finalized))


@pytest.mark.parametrize('persistent', [False, True], ids=['historical-v2', 'persistent-v3'])
def test_finalized_owned_recognition_uses_frozen_roster_not_live_ranking(runtime, persistent, record_property):
    with runtime.client() as client:
        seed_contest(runtime, persistent=persistent)
        result = read_unchanged(runtime, client)
        recognition = result['recap']['recognition']
        assert recognition['status'] == 'finalized'
        saved = recognition['competitions'][0]
        assert saved['rules_version'] == ('contest_finalizer_persistent_v3' if persistent else 'contest_finalizer_v2')
        assert [row['subject_type'] for row in saved['honors']] == ['student', 'team']
        assert saved['honors'][0]['rewards'][0]['amount'] == 50
        assert 'PRIVATE' not in json.dumps(result)
        assert result['recap']['top_20']['status'] == 'unavailable'
        with runtime.client(2) as other:
            other_result = read_unchanged(runtime, other)
            assert [row['subject_type'] for row in other_result['recap']['recognition']['competitions'][0]['honors']] == ['student']
        record_property('finalized_response', json.dumps(result))


def test_available_edition_pending_and_missing_results(runtime):
    with runtime.client() as client:
        seed_contest(runtime, status='pending')
        result = read_unchanged(runtime, client)
        assert result['context']['available']
        assert result['recap']['recognition']['status'] == 'pending'
        assert result['recap']['recognition']['competitions'][0]['honors'] == []
        with runtime.begin() as session:
            week = session.get(m.ContestWeek, 1)
            week.status = 'finalized'
            week.finalized_at = utc_instant(NOW + timedelta(days=1))
        assert read_unchanged(runtime, client)['recap']['recognition']['status'] == 'pending'
        with runtime.begin() as session:
            week = session.get(m.ContestWeek, 1)
            week.finalized_at = utc_instant(NOW)
            for grant in session.scalars(select(m.RewardGrant).where(m.RewardGrant.contest_result_id.is_not(None))):
                session.delete(grant)
            for row in session.scalars(select(m.ContestResult)):
                session.delete(row)
        result = read_unchanged(runtime, client)
        assert result['recap']['recognition']['competitions'][0]['reason'] == 'saved_results_missing'


def test_query_ownership_account_switch_and_revocation(runtime):
    with runtime.client() as client:
        before = rows(runtime.engine)
        for query in ('profile_id=2', 'plan_id=2', 'week=2026-10-12', 'before_plan_id=1'):
            assert client.get(PATH + '?' + query).status_code == 422
        assert client.get(PATH, headers={'X-Woodshed-Account': 'WL2-NEWS-2'}).status_code == 401
        assert rows(runtime.engine) == before
        old_cookie = client.cookies.get('session')
        assert client.post('/account/logout').status_code == 200
        with TestClient(app) as stale:
            stale.cookies.set('session', old_cookie)
            read_unchanged(runtime, stale, status=401)
        assert client.post('/account/login', data={'woodchuck_id': 'WL2-NEWS-2', 'pin': '2468'}).status_code == 200
        assert client.get(PATH, headers={'X-Woodshed-Account': 'WL2-NEWS-1'}).status_code == 401
        assert read_unchanged(runtime, client)['current']['plan'] is None
    with TestClient(app) as anonymous:
        read_unchanged(runtime, anonymous, status=401)


@pytest.mark.parametrize('change', ['version', 'inactive', 'age'])
def test_ineligible_and_stale_session_fail_closed(runtime, change):
    with runtime.client() as client:
        from app.age_models import AccountPrivacy
        with runtime.begin() as session:
            if change == 'version':
                session.get(m.WoodchuckProfile, 1).session_version += 1
            elif change == 'inactive':
                session.get(m.WoodchuckProfile, 1).status = 'deleted'
            else:
                session.get(AccountPrivacy, 1).age_band = 'under13'
        before = rows(runtime.engine)
        cookie = client.cookies.get('session')
        response = client.get(PATH)
        assert response.status_code in (401, 403)
        after = rows(runtime.engine)
        # Existing security middleware may retire this browser, never reward it.
        if change == 'age':
            assert after == before
        else:
            assert_only_session_retirement(before, after, cookie)


def test_child_consent_and_private_identity(arcade_factory, monkeypatch):
    from app import tester_enrollments as testers, child_authorization as consent
    from app.child_models import ConsentEvidence
    from tests.test_tester_enrollments import test_prebeta_under13_claim_survives_cross_device_activation_and_retry, CLAIMED
    monkeypatch.setattr(testers, 'clock', lambda: CLAIMED)
    test_prebeta_under13_claim_survives_cross_device_activation_and_retry(
        arcade_factory, monkeypatch, '/prebeta/C002', 'C002', 'DIRECTOR1')
    monkeypatch.setitem(FEATURES, 'weekly_newsletter', Feature(True, 'open'))
    monkeypatch.setattr(weekly_routes, 'utc_instant', lambda value=None: utc_instant(value or NOW))
    with arcade_factory() as session:
        profile = session.scalar(select(m.WoodchuckProfile))
        wid = profile.woodchuck_id
    with TestClient(app) as student:
        assert student.post('/account/login', data={'woodchuck_id': wid, 'pin': '2468'}).status_code == 200
        result = read_unchanged(arcade_factory, student)
        assert wid not in json.dumps(result)
        with arcade_factory.begin() as session:
            consent.withdraw_evidence(session, session.scalar(select(ConsentEvidence)))
        before = rows(arcade_factory.engine)
        cookie = student.cookies.get('session')
        assert student.get(PATH).status_code in (401, 403)
        after = rows(arcade_factory.engine)
        assert_only_session_retirement(before, after, cookie)


def test_dst_recap_uses_calendar_week_not_168_hours(runtime, monkeypatch):
    with runtime.client() as client:
        runtime.clock[0] = datetime(2026, 11, 1, 23, 30, tzinfo=CENTRAL)
        older = saved_plan(runtime, monkeypatch, week=date(2026, 10, 19))
        current = saved_plan(runtime, monkeypatch, week=date(2026, 10, 26))
        result = read_unchanged(runtime, client)
        assert result['current']['plan']['id'] == current
        assert result['recap']['plan']['id'] == older
        assert result['context']['recap_week_start'] == '2026-10-19'


def test_adult_role_context_cannot_select_student(runtime):
    from base64 import b64encode
    from itsdangerous import TimestampSigner
    from app.main import SESSION_SECRET
    # Negative role-only session fixtures, never positive authorization bypasses.
    for facts in ({'child_parent_consent': 900}, {'child_director_permission': 900},
                  {'trusted_verifier_id': 900}):
        with TestClient(app) as client:
            client.cookies.set('session', TimestampSigner(SESSION_SECRET).sign(
                b64encode(json.dumps(facts).encode())).decode())
            before = rows(runtime.engine)
            assert client.get(PATH).status_code == 401
            assert client.get(PATH, params={'profile_id': 1}).status_code == 422
            assert rows(runtime.engine) == before


def test_bounded_competition_response(runtime, monkeypatch):
    with runtime.client() as client:
        with runtime.begin() as session:
            for ident in range(1, newsletter.MAX_COMPETITIONS + 2):
                session.add(m.Season(id=ident, key=f'synthetic-{ident}', name='Synthetic competition',
                                     starts_on=PREVIOUS, status='active'))
                session.flush()
                session.add(m.ContestWeek(season_id=ident, week_start=PREVIOUS, week_end=START,
                    verification_deadline_at=utc_instant(NOW), finalize_after=utc_instant(NOW), status='pending'))
        result = read_unchanged(runtime, client)
        assert result['recap']['recognition'] == {
            'status': 'unavailable', 'reason': 'competition_limit', 'competitions': []}
