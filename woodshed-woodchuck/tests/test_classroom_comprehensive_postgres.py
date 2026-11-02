"""Independent comprehensive-reader privacy checks on disposable PostgreSQL.

The observed-wait cases use PostgreSQL's own pg_blocking_pids evidence.  They
exercise the actual report entrypoint rather than a copied authorization model.
"""
from datetime import datetime, time, timedelta, timezone
import json

import pytest
from sqlalchemy import event, select

from app import classroom as s1, classroom_s2 as s2, classroom_reporting as boundary
from app import classroom_progress_authorization as progress
from app.age_models import AccountPrivacy
from app.age_privacy import utc
from app.child_models import ConsentEvidence
from app.classroom_models import ClassroomProgressAuthorization, ClassroomTeachingAssignment
from app.models import PracticeChart, PracticeChartVerification, WoodchuckProfile
from app.practice_charts import PRACTICE_CATEGORIES
from app.student_practice_metrics import CENTRAL, week_start
from app.weekly_models import WeeklyInstrumentPlan, WeeklyPlan
from test_classroom_s1_postgres import classroom_pg, ordered_wait
from test_classroom_s2 import NOW, actor, student, create, code, join, institutional
from test_classroom_s2_postgres import s2_pg
from test_classroom_s3_postgres import s3_pg, setup_reporting
from test_classroom_progress_authorization import accept


CORE_LABELS = {
    'Weekly practice minutes', 'Lifetime practice minutes', 'Weekly practice days',
    'Weekly Practice Charts', 'Lifetime Practice Charts', 'Weekly Verified minutes',
    'Weekly Pristine minutes', 'Lifetime Verified minutes', 'Lifetime Pristine minutes',
    'Last P-Chart submission', 'Lifetime practice graph', 'Weekly practice graph',
    'Student Practice Rating', 'Rating trend', 'Current practice streak',
    'Weekly Focus', 'Homework participation', 'Homework completion',
}
EXPECTED_LABELS = CORE_LABELS | set(PRACTICE_CATEGORIES)


def service():
    from app import classroom_comprehensive_reporting
    return classroom_comprehensive_reporting


def midnight(day):
    return datetime.combine(day, time.min, CENTRAL).astimezone(timezone.utc)


@pytest.fixture
def comprehensive_pg(s3_pg, monkeypatch):
    monkeypatch.setenv('CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED', '1')
    monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
    refs = setup_reporting(s3_pg, granted=False)
    refs.factory, refs.clock = s3_pg, s3_pg.reporting_clock
    with s3_pg() as session:
        accept(session, refs)
        session.commit()
    refs.clock[0] = NOW + timedelta(days=50)
    refs.valid_day = refs.clock[0].astimezone(CENTRAL).date() - timedelta(days=2)
    refs.selected_week = week_start(refs.valid_day)
    with s3_pg() as session:
        profile = session.get(WoodchuckProfile, 1)
        profile.display_name = 'UNAPPROVED_PUBLIC_NICKNAME_SENTINEL'
        peer = WoodchuckProfile(id=2, woodchuck_id='WC-OTHER-REPORT',
            display_name='OTHER_STUDENT_SENTINEL', pin_hash=profile.pin_hash,
            instrument='Flute', level='Beginner', goal='Practice')
        session.add(peer); session.flush()
        session.add(AccountPrivacy(profile_id=2, age_band='adult', declared_at=NOW, public_from=NOW))
        submitted = midnight(refs.valid_day) + timedelta(hours=18)
        valid = PracticeChart(profile_id=1, practice_date=refs.valid_day, minutes=30,
            instrument='Trumpet', practice_details=list(PRACTICE_CATEGORIES),
            note='PRIVATE_NOTE_NEVER_READ_SENTINEL', created_at=submitted)
        pristine = PracticeChart(profile_id=1, practice_date=refs.valid_day, minutes=10,
            instrument='Trumpet', source='pristine', detected_playing_seconds=600,
            note='PRIVATE_PRISTINE_NOTE_SENTINEL', created_at=submitted)
        zero = PracticeChart(profile_id=1, practice_date=refs.valid_day, minutes=0,
            instrument='Trumpet', created_at=submitted + timedelta(seconds=1))
        forbidden = [
            # Each independently violates one retained historical condition.
            PracticeChart(profile_id=1, practice_date=NOW.astimezone(CENTRAL).date() - timedelta(days=1),
                minutes=999, instrument='Flute', created_at=submitted),
            PracticeChart(profile_id=1, practice_date=refs.valid_day, minutes=999,
                instrument='Flute', created_at=NOW - timedelta(seconds=1)),
            PracticeChart(profile_id=1, practice_date=refs.valid_day, minutes=999,
                instrument='Flute', created_at=refs.clock[0] + timedelta(seconds=1)),
            PracticeChart(profile_id=1, practice_date=NOW.astimezone(CENTRAL).date(),
                minutes=999, instrument='Flute', created_at=submitted),
            PracticeChart(profile_id=1, practice_date=refs.clock[0].astimezone(CENTRAL).date(),
                minutes=999, instrument='Flute', created_at=refs.clock[0]),
            PracticeChart(profile_id=2, practice_date=refs.valid_day, minutes=999,
                instrument='Flute', created_at=submitted),
        ]
        session.add_all([valid, pristine, zero, *forbidden]); session.flush()
        session.add(PracticeChartVerification(practice_chart_id=valid.id, verifier_id=2,
            response_note='PRIVATE_VERIFIER_SENTINEL', status='approved', responded_at=submitted))
        refs.valid_id = valid.id
        session.commit()
    return refs


def report(session, refs, **changes):
    arguments = dict(actor=actor(session, 2), program_id=1, class_id=refs.class_id,
        profile_id=1, selected_week=refs.selected_week)
    return service().report(session, **(arguments | changes))


def by_label(data):
    fields = data['values']['fields']
    return {label: fields[key] for key, label in service().FIELD_LABELS}


def test_pg_all_41_fields_exact_projection_without_private_notes_or_other_activity(comprehensive_pg):
    refs = comprehensive_pg
    statements = []
    def observe(_connection, _cursor, statement, *_args):
        statements.append(statement)
    engine = refs.factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with refs.factory() as session:
            result = report(session, refs)
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert len(service().FIELD_KEYS) == len(set(service().FIELD_KEYS)) == 41
    assert {label for _, label in service().FIELD_LABELS} == EXPECTED_LABELS
    assert set(result['values']['fields']) == set(service().FIELD_KEYS)
    assert result['source'] == 'separate_reporting_authorization'
    assert result['field_contract'] == 'classroom-reporting-41-v1'
    fields = by_label(result)
    for name in ('Weekly practice minutes', 'Lifetime practice minutes'):
        assert fields[name] == 40
    assert fields['Weekly practice days'] == 1
    assert fields['Weekly Practice Charts'] == fields['Lifetime Practice Charts'] == 3
    assert fields['Weekly Verified minutes'] == fields['Lifetime Verified minutes'] == 30
    assert fields['Weekly Pristine minutes'] == fields['Lifetime Pristine minutes'] == 10
    for name in PRACTICE_CATEGORIES:
        assert fields[name] == 1
    encoded = json.dumps(result)
    for sentinel in ('PRIVATE_NOTE', 'PRIVATE_PRISTINE', 'PRIVATE_VERIFIER',
                     'UNAPPROVED_PUBLIC_NICKNAME', 'OTHER_STUDENT', 'Flute'):
        assert sentinel not in encoded
    chart_queries = [sql.lower() for sql in statements if 'from practice_charts' in sql.lower()]
    assert chart_queries, 'actual comprehensive PracticeChart reader was not exercised'
    for sql in chart_queries:
        columns = sql.split('from practice_charts', 1)[0]
        for prohibited in ('practice_charts.note', 'practice_charts.team_id',
                           'practice_charts.ordinary_email', 'practice_charts.submission_key'):
            assert prohibited not in columns


@pytest.mark.parametrize('scope', ['no_authorization', 'owner_only', 'class', 'program', 'fields'])
def test_pg_denials_precede_practice_sql(comprehensive_pg, scope):
    refs = comprehensive_pg
    with refs.factory() as session:
        if scope == 'no_authorization':
            progress.withdraw(session, student=student(session), program_id=1, class_id=refs.class_id)
            session.commit()
        statements = []
        def observe(_conn, _cursor, sql, *_args): statements.append(sql.lower())
        engine = refs.factory.kw['bind']
        event.listen(engine, 'before_cursor_execute', observe)
        try:
            changes = {'owner_only': {'actor': actor(session)}, 'class': {'class_id': 99999},
                'program': {'program_id': 99999}, 'fields': {'fields': ('private_notes',)}}.get(scope, {})
            with pytest.raises(s1.ClassroomDenied): report(session, refs, **changes)
        finally:
            event.remove(engine, 'before_cursor_execute', observe)
        assert not any('from practice_charts' in sql for sql in statements)


@pytest.mark.parametrize('restriction', ['under13', 'unknown', 'consent_link', 'inactive'])
def test_pg_saved_authorization_never_bypasses_account_and_parent_restrictions(comprehensive_pg, restriction):
    refs = comprehensive_pg
    with refs.factory() as session:
        if restriction == 'inactive':
            session.get(WoodchuckProfile, 1).status = 'deleted'
        elif restriction == 'consent_link':
            evidence = ConsentEvidence(profile_id=1, notice_version='synthetic', notice_sha256='0' * 64,
                approved_at=NOW, confirmed_at=NOW)
            session.add(evidence); session.flush()
            session.get(AccountPrivacy, 1).consent_id = evidence.id
        else:
            session.get(AccountPrivacy, 1).age_band = restriction
        session.commit()
        with pytest.raises(s1.ClassroomDenied): report(session, refs)


@pytest.mark.parametrize('withdraw_first', [False, True])
def test_pg_reporting_withdrawal_and_reader_linearize_at_observed_wait(comprehensive_pg, withdraw_first):
    refs = comprehensive_pg
    def revoke(session):
        return progress.withdraw(session, student=student(session),
            program_id=1, class_id=refs.class_id).id
    def read(session):
        cached = session.scalar(select(ClassroomProgressAuthorization))
        assert cached.withdrawn_at is None
        return by_label(report(session, refs))['Lifetime practice minutes']
    operations = (revoke, read) if withdraw_first else (read, revoke)
    outcomes = ordered_wait(refs.factory, *operations)
    assert tuple(row[0] for row in outcomes) == (('ok', 'denied') if withdraw_first else ('ok', 'ok'))
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): report(session, refs)
        assert session.scalar(select(ClassroomProgressAuthorization)).withdrawn_at is not None
        assert session.get(PracticeChart, refs.valid_id).minutes == 30


def test_pg_rolled_back_withdrawal_preserves_actual_reader(comprehensive_pg):
    refs = comprehensive_pg
    outcomes = ordered_wait(refs.factory,
        lambda s: progress.withdraw(s, student=student(s), program_id=1, class_id=refs.class_id).id,
        lambda s: by_label(report(s, refs))['Lifetime practice minutes'], rollback_first=True)
    assert tuple(row[0] for row in outcomes) == ('ok', 'ok')
    assert outcomes[1][1] == 40


@pytest.mark.parametrize('writer_first', [False, True])
def test_pg_teacher_revocation_rechecks_cached_assignment_after_observed_wait(comprehensive_pg, writer_first):
    refs = comprehensive_pg
    def revoke(session):
        return s2.end_teaching(session, actor=actor(session), program_id=1,
            assignment_id=refs.assignment_id).id
    def read(session):
        cached = session.get(ClassroomTeachingAssignment, refs.assignment_id)
        assert cached.ended_at is None
        return by_label(report(session, refs))['Lifetime practice minutes']
    operations = (revoke, read) if writer_first else (read, revoke)
    outcomes = ordered_wait(refs.factory, *operations)
    assert tuple(row[0] for row in outcomes) == (('ok', 'denied') if writer_first else ('ok', 'ok'))
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): report(session, refs)


def test_pg_exact_other_class_membership_is_not_reporting_permission(comprehensive_pg):
    refs = comprehensive_pg
    with refs.factory() as session:
        other = create(session, name='Other synthetic Class')
        code(session, other.id, preferred='OTHER2026')
        s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=other.id, verifier_id=2)
        join(session, 'OTHER2026')
        session.commit()
        with pytest.raises(s1.ClassroomDenied): report(session, refs, class_id=other.id)
        # Changing another Class can affect retained capacity/source validity;
        # it must never automatically create the missing exact-Class consent.
        assert session.scalar(select(ClassroomProgressAuthorization).where(
            ClassroomProgressAuthorization.class_id == other.id)) is None


def test_pg_preauthorization_week_is_unavailable_not_zero(comprehensive_pg):
    refs = comprehensive_pg
    with refs.factory() as session:
        result = by_label(report(session, refs,
            selected_week=week_start(NOW.astimezone(CENTRAL).date()) - timedelta(days=14)))
    for label in ('Weekly practice minutes', 'Weekly practice days', 'Weekly Practice Charts',
                  'Weekly Verified minutes', 'Weekly Pristine minutes', 'Weekly practice graph'):
        assert result[label] is None
    for name in PRACTICE_CATEGORIES:
        assert result[name] is None


@pytest.mark.parametrize('evidence', ['eligible', 'old_plan', 'old_assignment', 'no_assignment'])
def test_pg_homework_uses_eligible_issuance_and_filtered_practice_only(comprehensive_pg, evidence):
    refs = comprehensive_pg
    issued = midnight(refs.selected_week) + timedelta(hours=1)
    if evidence == 'old_plan':
        issued = NOW - timedelta(seconds=1)
    started = midnight(refs.valid_day) + timedelta(hours=1)
    if evidence == 'old_assignment':
        started = NOW - timedelta(seconds=1)
    with refs.factory() as session:
        plan = WeeklyPlan(profile_id=1, week_start=refs.selected_week,
            focus='Improve tone and control', issued_game_mode='Beginner',
            generator_version='synthetic-reporting-test', issued_at=issued,
            recent_results_cutoff=issued, recent_results={}, logging_days_target=3,
            practice_minutes_target=30, homework_minutes_target=10, thirds_target=3)
        session.add(plan); session.flush()
        if evidence != 'no_assignment':
            session.add(WeeklyInstrumentPlan(plan_id=plan.id, slot_no=1,
                instrument='Trumpet', started_at=started, homework_spec={}))
        session.commit()
        fields = by_label(report(session, refs))
    if evidence == 'old_plan':
        assert fields['Weekly Focus'] is None
        assert fields['Homework participation'] is None
        assert fields['Homework completion'] is None
    else:
        assert fields['Weekly Focus'] == 'Improve tone and control'
        if evidence == 'eligible':
            assert fields['Homework participation'] is True
            assert fields['Homework completion'] is True
        elif evidence == 'old_assignment':
            assert fields['Homework participation'] is None
            assert fields['Homework completion'] is None
        else:
            assert fields['Homework participation'] is False
            assert fields['Homework completion'] is None


def test_pg_current_homework_does_not_claim_failure_when_today_is_unobservable(comprehensive_pg):
    refs = comprehensive_pg
    current = week_start(refs.clock[0].astimezone(CENTRAL).date())
    issued = midnight(current) + timedelta(minutes=1)
    with refs.factory() as session:
        plan = WeeklyPlan(profile_id=1, week_start=current,
            focus='Improve tone and control', issued_game_mode='Beginner',
            generator_version='synthetic-reporting-test', issued_at=issued,
            recent_results_cutoff=issued, recent_results={}, logging_days_target=3,
            practice_minutes_target=30, homework_minutes_target=10, thirds_target=3)
        session.add(plan); session.flush()
        session.add(WeeklyInstrumentPlan(plan_id=plan.id, slot_no=1,
            instrument='Flute', started_at=issued + timedelta(minutes=1), homework_spec={}))
        session.commit()
        fields = by_label(report(session, refs, selected_week=current))
        # The fixture contains enough current-day Flute practice to qualify,
        # but full-day history protection correctly excludes that evidence.
        # Neither the private evidence nor an incorrect failure may be shown.
        assert fields['Homework participation'] is True
        assert fields['Homework completion'] is None


def test_pg_reauthorization_does_not_backfill_previous_private_period(comprehensive_pg):
    refs = comprehensive_pg
    with refs.factory() as session:
        assert by_label(report(session, refs))['Lifetime practice minutes'] == 40
        progress.withdraw(session, student=student(session), program_id=1, class_id=refs.class_id)
        session.commit()
        refs.clock[0] += timedelta(seconds=1)
        accept(session, refs)
        session.commit()
        result = by_label(report(session, refs))
        # A new consent starts a new interval. The data remains preserved, but
        # cannot be reincorporated merely because the director is the same.
        assert result['Lifetime practice minutes'] in (None, 0)
        assert result['Last P-Chart submission'] is None
        assert session.get(PracticeChart, refs.valid_id).minutes == 30


@pytest.mark.parametrize('isolation', ['REPEATABLE READ', 'SERIALIZABLE'])
def test_pg_reporting_never_uses_snapshot_isolation(comprehensive_pg, isolation):
    refs = comprehensive_pg
    with refs.factory.kw['bind'].connect().execution_options(isolation_level=isolation) as connection:
        with refs.factory(bind=connection) as session:
            with pytest.raises(s1.ClassroomDenied): report(session, refs)


@pytest.mark.parametrize('loss', ['suspend', 'leave', 'archive', 'entitlement', 'new_teacher'])
@pytest.mark.parametrize('writer_first', [False, True])
def test_pg_actual_measurement_reader_rechecks_lifecycle_after_wait(comprehensive_pg, loss, writer_first):
    refs = comprehensive_pg
    def revoke(session):
        if loss == 'suspend':
            return s2.suspend_member(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, membership_id=refs.membership_id).id
        if loss == 'leave':
            return s2.leave_class(session, student=student(session), program_id=1,
                class_id=refs.class_id).id
        if loss == 'archive':
            return s2.set_class_state(session, actor=actor(session), program_id=1,
                class_id=refs.class_id, state='archived').class_id
        if loss == 'entitlement':
            return institutional(session, status='ended').id
        return s2.assign_teacher(session, actor=actor(session), program_id=1,
            class_id=refs.class_id, verifier_id=3).id
    def read(session):
        cached = session.scalar(select(ClassroomProgressAuthorization))
        assert cached.withdrawn_at is None
        return by_label(report(session, refs))['Lifetime practice minutes']
    outcomes = ordered_wait(refs.factory, *((revoke, read) if writer_first else (read, revoke)))
    assert tuple(row[0] for row in outcomes) == (('ok', 'denied') if writer_first else ('ok', 'ok'))
    with refs.factory() as session:
        with pytest.raises(s1.ClassroomDenied): report(session, refs)
        assert session.get(PracticeChart, refs.valid_id).minutes == 30


@pytest.mark.parametrize('qualification', ['canonical', 'wrong_instrument', 'before_assignment', 'at_week_end'])
def test_pg_homework_matches_existing_qualification_without_unbounded_reader(comprehensive_pg, qualification):
    refs = comprehensive_pg
    issued = midnight(refs.selected_week) + timedelta(minutes=1)
    started = midnight(refs.valid_day) + timedelta(hours=1)
    with refs.factory() as session:
        plan = WeeklyPlan(profile_id=1, week_start=refs.selected_week,
            focus='Improve tone and control', issued_game_mode='Beginner',
            generator_version='synthetic-reporting-test', issued_at=issued,
            recent_results_cutoff=issued, recent_results={}, logging_days_target=3,
            practice_minutes_target=30, homework_minutes_target=10, thirds_target=3)
        session.add(plan); session.flush()
        assignment = WeeklyInstrumentPlan(plan_id=plan.id, slot_no=1,
            instrument='trumpet' if qualification == 'canonical' else 'Flute',
            started_at=started, homework_spec={})
        session.add(assignment)
        if qualification in {'before_assignment', 'at_week_end'}:
            submitted = (started if qualification == 'before_assignment'
                else midnight(refs.selected_week + timedelta(days=7)))
            session.add(PracticeChart(profile_id=1, practice_date=refs.valid_day, minutes=99,
                source='p-book', instrument='Flute', created_at=submitted))
        session.commit()
        # Close the week so a supported failure can be observed. Future practice
        # in the fixture stays excluded by the independent instrument filter.
        refs.clock[0] = midnight(refs.selected_week + timedelta(days=8))
        from app.weekly_tasks import homework_progress
        expected = homework_progress(session, 1, plan, now=refs.clock[0])['qualified']
        actual = by_label(report(session, refs))['Homework completion']
        assert actual is expected is (qualification == 'canonical')
