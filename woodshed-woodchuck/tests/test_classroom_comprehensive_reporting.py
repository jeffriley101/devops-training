"""SQLite product semantics for the approved individual 41-field report."""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app import classroom_comprehensive_reporting as service
from app import classroom_reporting as boundary, classroom_s2 as s2
from app import classroom_progress_authorization as progress
from app.models import PracticeChart, PracticeChartVerification
from app.practice_charts import PRACTICE_CATEGORIES
from app.student_practice_metrics import CENTRAL, week_start, student_practice_rating
from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, NOW, student
from test_classroom_s3_postgres import setup_reporting
from test_classroom_progress_authorization import accept


def midnight(day):
    return datetime.combine(day, time.min, CENTRAL).astimezone(timezone.utc)


@pytest.fixture
def report_db(s2_db, monkeypatch):
    monkeypatch.setenv('CLASSROOM_S3_ENABLED', '1')
    monkeypatch.setenv('CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED', '1')
    clock = [NOW]
    monkeypatch.setattr(s2, '_now', lambda: clock[0])
    monkeypatch.setattr(boundary, '_now', lambda: clock[0])
    refs = setup_reporting(s2_db, granted=False)
    refs.factory, refs.clock = s2_db, clock
    with s2_db() as session:
        accept(session, refs); session.commit()
    clock[0] += timedelta(days=57)
    refs.week = week_start(clock[0].astimezone(CENTRAL).date()) - timedelta(days=7)
    with s2_db() as session:
        # Five distinct positive dates, one Verified chart and one Pristine.
        for offset in range(5):
            chart = PracticeChart(profile_id=1, practice_date=refs.week + timedelta(days=offset),
                minutes=20, instrument='Trumpet', created_at=midnight(refs.week + timedelta(days=offset))
                    + timedelta(hours=18), practice_details=list(PRACTICE_CATEGORIES),
                note='PRIVATE_COMPREHENSIVE_NOTE')
            session.add(chart); session.flush()
            if offset == 0:
                session.add(PracticeChartVerification(practice_chart_id=chart.id,
                    verifier_id=1, status='approved', responded_at=chart.created_at))
        session.add(PracticeChart(profile_id=1, practice_date=refs.week, minutes=0,
            instrument='Trumpet', source='pristine', detected_playing_seconds=1200,
            created_at=midnight(refs.week) + timedelta(hours=19)))
        plan = WeeklyPlan(profile_id=1, week_start=refs.week, focus='Improve tone and control',
            issued_game_mode='Beginner', generator_version='weekly_plan_v1',
            issued_at=midnight(refs.week), recent_results_cutoff=midnight(refs.week),
            recent_results={}, logging_days_target=3, practice_minutes_target=30,
            homework_minutes_target=10, thirds_target=3)
        session.add(plan); session.flush()
        session.add(WeeklyInstrumentPlan(plan_id=plan.id, slot_no=1, instrument='Trumpet',
            started_at=midnight(refs.week) + timedelta(hours=1), homework_spec={}))
        session.commit()
    return refs


def read(session, refs, **changes):
    return service.report(session, **(dict(actor=actor(session, 2), program_id=1,
        class_id=refs.class_id, profile_id=1, selected_week=refs.week) | changes))


def test_all_41_fields_have_reviewed_individual_semantics(report_db):
    refs = report_db
    with refs.factory() as session:
        data = read(session, refs)
    values = data['values']['fields']
    assert len(values) == 41 and set(values) == set(service.FIELD_KEYS)
    assert len([key for key in values if key.startswith('category_')]) == 23
    assert all(value is True for key, value in values.items() if key.startswith('category_'))
    for prefix in ('weekly', 'lifetime'):
        assert values[f'{prefix}_practice_minutes'] == 120
        assert values[f'{prefix}_practice_charts'] == 6
        assert values[f'{prefix}_verified_minutes'] == 20
        assert values[f'{prefix}_pristine_minutes'] == 20
    assert values['weekly_practice_days'] == 5
    assert values['student_practice_rating'] == 104
    assert values['rating_trend']['delta'] == 104
    assert values['rating_trend']['direction'] == 'up'
    assert values['last_p_chart_submission'] == (midnight(refs.week + timedelta(days=4))
        + timedelta(hours=18)).astimezone(CENTRAL).isoformat()
    assert sum(point['minutes'] for point in values['weekly_practice_graph']) == 120
    assert values['lifetime_practice_graph'][-1]['minutes'] == 120
    assert values['weekly_focus'] == 'Improve tone and control'
    assert values['homework_participation'] is True and values['homework_completion'] is True
    assert values['current_practice_streak'] is None
    assert 'current day' in data['message']


def test_preauthorization_or_partial_week_never_becomes_zero(report_db):
    refs = report_db
    with refs.factory() as session:
        values = read(session, refs, selected_week=week_start(NOW.astimezone(CENTRAL).date()))['values']['fields']
    assert values['weekly_practice_minutes'] is None
    assert values['weekly_practice_days'] is None
    assert values['weekly_practice_graph'] is None
    assert values['weekly_focus'] is None
    assert all(value is None for key, value in values.items() if key.startswith('category_'))
    assert values['lifetime_practice_minutes'] == 120


@pytest.mark.parametrize('days,expected_rating,expected_trend', [(3, None, None), (19, 0, None)])
def test_rating_requires_complete_authorized_weeks(report_db, days, expected_rating, expected_trend):
    refs = report_db
    refs.clock[0] = NOW + timedelta(days=days)
    with refs.factory() as session:
        values = read(session, refs, selected_week=None)['values']['fields']
    assert values['student_practice_rating'] == expected_rating
    assert values['rating_trend'] == expected_trend


def test_no_plan_is_unavailable_not_homework_failure(report_db):
    refs = report_db
    with refs.factory() as session:
        values = read(session, refs, selected_week=refs.week-timedelta(days=7))['values']['fields']
    assert values['weekly_practice_minutes'] == 0  # Authorized, fully observed empty week.
    assert values['weekly_focus'] is None
    assert values['homework_participation'] is None
    assert values['homework_completion'] is None


def test_authorization_rechecked_and_no_notes_or_unapproved_telemetry_read(report_db):
    refs = report_db
    statements = []
    engine = refs.factory.kw['bind']
    def capture(conn, cursor, sql, params, ctx, many): statements.append(sql.lower())
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with refs.factory() as session:
            data = read(session, refs)
            progress.withdraw(session, student=student(session), program_id=1, class_id=refs.class_id)
            session.commit()
            statements.clear()
            with pytest.raises(s2.s1.ClassroomDenied): read(session, refs)
        assert not any('practice_charts' in sql or 'weekly_plans' in sql for sql in statements)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    import json
    assert 'PRIVATE_COMPREHENSIVE_NOTE' not in json.dumps(data)
    assert not {'game_mode','login_streak','program_rating','notes','exports'} & data['values']['fields'].keys()


@pytest.mark.parametrize('fields', [('notes',), ('program_rating',), ('export',), [], None])
def test_unapproved_projection_denied_before_private_queries(report_db, fields):
    refs = report_db
    with refs.factory() as session:
        with pytest.raises(s2.s1.ClassroomDenied): read(session, refs, fields=fields)


def test_versioned_contract_cannot_grow_with_book_activity_menu(monkeypatch):
    import importlib
    from app import practice_charts
    expected = service.FIELD_LABELS
    monkeypatch.setattr(practice_charts, 'PRACTICE_CATEGORIES',
        practice_charts.PRACTICE_CATEGORIES + ('UNAPPROVED_NEW_CATEGORY',))
    importlib.reload(service)
    assert service.FIELD_LABELS == expected
    assert len(service.CATEGORY_FIELDS) == 23
    assert 'UNAPPROVED_NEW_CATEGORY' not in dict(service.FIELD_LABELS).values()


def test_category_activity_is_one_credit_and_never_custom_free_text(report_db):
    refs = report_db
    with refs.factory() as session:
        charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1)).all()
        for chart in charts:
            chart.practice_details = ['Scales', 'Scales', 'UNAPPROVED_CUSTOM_DETAIL']
        session.commit()
        fields = read(session, refs)['values']['fields']
    categories = {key: fields[key] for key, _ in service.CATEGORY_FIELDS}
    assert categories['category_scales'] is True
    assert sum(categories.values()) == 1
    assert 'UNAPPROVED_CUSTOM_DETAIL' not in str(fields)


def test_changing_practice_week_does_not_change_last_completed_rating(report_db):
    refs = report_db
    with refs.factory() as session:
        latest = read(session, refs)['values']
        historical = read(session, refs, selected_week=refs.week - timedelta(weeks=1))['values']
    assert historical['fields']['weekly_practice_minutes'] == 0
    assert historical['rating_week'] == latest['rating_week'] == refs.week.isoformat()
    assert historical['fields']['student_practice_rating'] == latest['fields']['student_practice_rating'] == 104
    assert historical['fields']['rating_trend'] == latest['fields']['rating_trend']


@pytest.mark.parametrize('day,hours', [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
@pytest.mark.parametrize('start_offset,end_offset,visible', [
    (timedelta(), timedelta(), True),
    (timedelta(microseconds=1), timedelta(), False),
    (timedelta(), -timedelta(microseconds=1), False),
])
def test_occurrence_requires_entire_central_day_across_dst(report_db, day, hours,
                                                          start_offset, end_offset, visible):
    refs = report_db
    start, end = midnight(day), midnight(day + timedelta(days=1))
    assert (end - start).total_seconds() == hours * 3600
    with refs.factory() as session:
        chart = PracticeChart(profile_id=1, practice_date=day, minutes=7,
            instrument='Trumpet', created_at=start + timedelta(hours=12))
        session.add(chart); session.flush()
        decision = SimpleNamespace(starts_at=start + start_offset, ends_at=end + end_offset)
        rows = service._charts(session, 1, decision)
        assert (chart.id in {row.id for row in rows}) is visible


@pytest.mark.parametrize('offset,visible', [
    (-timedelta(microseconds=1), False), (timedelta(), True),
    (timedelta(microseconds=1), True),
])
def test_submission_lower_bound_is_independent_of_practice_date(report_db, offset, visible):
    refs = report_db
    with refs.factory() as session:
        authorization = progress.authorize_boundary(session, actor=actor(session, 2),
            program_id=1, class_id=refs.class_id, profile_id=1)
        chart = PracticeChart(profile_id=1, practice_date=refs.week, minutes=7,
            instrument='Trumpet', created_at=authorization.starts_at + offset)
        session.add(chart); session.flush()
        assert (chart.id in {row.id for row in service._charts(session, 1, authorization)}) is visible


@pytest.mark.parametrize('week', [datetime(2026, 1, 5), date(2026, 1, 6), '2026-01-05'])
def test_invalid_week_denied_before_measurement_sql(report_db, week):
    refs = report_db
    statements = []
    engine = refs.factory.kw['bind']
    def observe(_connection, _cursor, sql, *_args): statements.append(sql.lower())
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with refs.factory() as session:
            with pytest.raises(s2.s1.ClassroomDenied): read(session, refs, selected_week=week)
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert not any('from practice_charts' in sql or 'from weekly_plans' in sql for sql in statements)
