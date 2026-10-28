"""Personal dashboard authority, shared calculations, and graph accuracy."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import main, memberships, personal_development as service, classroom_s2 as s2
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence
from app.models import (Membership, PracticeChart, PracticeChartVerification, TesterEnrollment as Enrollment,
                        WoodchuckProfile)
from app.student_practice_metrics import practice_insights, practice_totals, student_practice_snapshot, week_start
from app.practice_charts import PRACTICE_CATEGORIES
from app.practice_chart_routes import profile_practice_streak
from app.trusted_verifier_dashboard import private_student_metrics
from test_classroom_access import access_web, enroll, protected_rows
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, student
from test_classroom_director import web, login
from test_classroom_student_invitation_routes import invitation_web, issue, student_client
from test_student_rating_privacy import safe

TODAY = date(2026, 10, 28)
MONDAY = week_start(TODAY)
PAGE = '/personal-development'
DATA = PAGE + '/data'
RATING = PAGE + '/rating'
CHARTS = PAGE + '/charts'


@pytest.fixture
def dashboard_db(access_web, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 28, 12, tzinfo=tz)
    monkeypatch.setattr(service, 'datetime', Clock)
    with access_web() as session:
        first = session.scalar(select(PracticeChart).where(PracticeChart.profile_id == 1))
        first.practice_date = MONDAY - timedelta(days=7)
        for day, minutes, source, seconds, approved in [
            (MONDAY - timedelta(days=4), 60, 'manual', None, False),
            (MONDAY - timedelta(days=1), 0, 'pristine', 19, True),
            (MONDAY - timedelta(days=35), 10, 'manual', None, True),
            (MONDAY, 5, 'manual', None, False),
            (MONDAY, 0, 'pristine', 45, False),
            (TODAY, 7, 'manual', None, False),
        ]:
            chart = PracticeChart(profile_id=1, practice_date=day, minutes=minutes, source=source,
                detected_playing_seconds=seconds, instrument='Trumpet', note='PRIVATE CHART TEXT',
                include_contests=False, include_team_contests=False)
            session.add(chart); session.flush()
            if approved:
                session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=1,
                                                      status='approved', response_note='PRIVATE ADULT NOTE'))
        session.commit()
    return access_web


def full(factory, source='manual'):
    with factory() as session:
        member = memberships.create_complimentary_membership(session, memberships.Actor('student', 1),
                                                            memberships.Actor('admin'))
        member.source = source  # Synthetic saved membership; no payment/provider calls.
        session.commit()
        return member.id


@pytest.mark.parametrize('source', ['manual', 'stripe', 'paypal', 'classroom', 'tester', 'free', 'invitation'])
def test_entitlement_sources_and_no_denied_chart_queries(dashboard_db, source, monkeypatch):
    if source in {'manual', 'stripe', 'paypal'}:
        full(dashboard_db, source)
    elif source == 'classroom':
        enroll(dashboard_db)
    elif source == 'tester':
        with dashboard_db() as session:
            session.add(Enrollment(profile_id=1, cohort_key='C001', joined_at=s2._now()))
            session.commit()
    elif source == 'invitation':
        browser, _, ident, csrf = issue(dashboard_db)
        assert browser.post(f'/classroom/student-invitations/{ident}/accept',
                            data={'csrf': csrf}, follow_redirects=False).status_code == 303
    monkeypatch.delenv('CLASSROOM_DIRECTOR_ENABLED')
    browser = student_client()
    permitted = source not in {'free', 'invitation'}
    queries = []
    def capture(conn, cursor, statement, *args):
        queries.append(statement.lower())
    engine = dashboard_db.kw['bind']
    before = protected_rows(dashboard_db)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = browser.get(DATA)
        rating = browser.get(RATING)
        history = browser.get(CHARTS)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert response.status_code == rating.status_code == history.status_code == (200 if permitted else 403)
    assert response.headers['cache-control'] == 'no-store'
    assert protected_rows(dashboard_db) == before
    if not permitted:
        assert response.json()['detail']['code'] == 'entitlement_required'
        assert not any('practice_charts' in q or 'practice_chart_verifications' in q for q in queries)
    else:
        assert set(rating.json()) == {'rating', 'rating_week', 'rating_week_end'}
        assert rating.json()['rating'] == response.json()['rating']
    page = browser.get(PAGE)
    assert page.status_code == 200
    assert 'Personal Development Dashboard' in page.text
    assert f'data-enabled="{str(permitted).lower()}"' in page.text
    assert '<svg' not in page.text and 'PRIVATE CHART TEXT' not in page.text
    assert 'data-pdd-invitation' in page.text and 'Explore Full Access' in page.text
    assert '/personal-development' in browser.get('/p-book').text


def test_rating_graphs_insights_and_read_only_personal_history(dashboard_db):
    enroll(dashboard_db)
    browser = student_client()
    before = protected_rows(dashboard_db)
    data = browser.get(DATA).json()
    with dashboard_db() as session:
        charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1)).all()
        approved = set(session.scalars(select(PracticeChartVerification.practice_chart_id)))
        expected = student_practice_snapshot(charts, approved, today=TODAY)
        assert data['rating'] == expected['rating'] == private_student_metrics(
            session, profile_id=1, today=TODAY, include_team_context=False)['rating']
        assert data['insights'] == practice_insights(charts, approved, today=TODAY)
        assert data['lifetime_seconds'] == expected['lifetime']['total_seconds'] == 6004
        assert data['weekly_seconds'] == expected['weekly']['total_seconds'] == 765
    assert data['rating_week'] == '2026-10-19'
    assert data['rating_week_end'] == '2026-10-25'
    assert [d['date'] for d in data['weekly']] == [(MONDAY + timedelta(days=i)).isoformat() for i in range(7)]
    assert [d['seconds'] for d in data['weekly']] == [345, 0, 420, 0, 0, 0, 0]
    assert data['lifetime'][0] == {'date': '2026-09-20', 'seconds': 0}
    assert data['lifetime'][-1] == {'date': TODAY.isoformat(), 'seconds': 6004}
    assert {'date': '2026-10-18', 'seconds': 600} in data['lifetime']  # Flat gap, no fictitious practice.
    assert all(a['date'] < b['date'] and a['seconds'] <= b['seconds']
               for a, b in zip(data['lifetime'], data['lifetime'][1:]))
    assert 'trend' not in data and 'rating_week_values' not in data and 'rating_history' not in data
    assert protected_rows(dashboard_db) == before
    # Existing student/family and account projections retain their rating privacy.
    safe(browser.get('/family/practice/data').json()['metrics'])
    safe(browser.get('/account/me').json())


def test_cross_account_guest_adult_and_credential_boundaries(dashboard_db):
    full(dashboard_db)
    browser = student_client()
    expected = browser.get(DATA).json()
    assert browser.get(DATA + '?profile_id=2&woodchuck_id=WC-SECOND&week=2020-01-01').json() == expected
    for path in (PAGE, DATA, RATING, CHARTS):
        assert browser.get(path, headers={'X-Woodshed-Account': 'WC-SECOND'}).status_code == 401
        assert TestClient(main.app).get(path).status_code == 401
        assert login(2).get(path).status_code == 401
    assert student_client(True).get(DATA).status_code == 403
    assert all(v not in str(expected) for v in ('PRIVATE', 'OTHER STUDENT', 'verifier_id', 'profile_id'))
    assert browser.get(CHARTS + '?profile_id=2&woodchuck_id=WC-SECOND').json() == browser.get(CHARTS).json()
    assert all(v not in browser.get(CHARTS).text for v in ('PRIVATE', 'OTHER STUDENT', 'verifier_id', 'profile_id'))
    with dashboard_db() as session:
        session.get(WoodchuckProfile, 1).session_version += 1
        session.commit()
    assert browser.get(DATA).status_code == 401
    assert browser.get(CHARTS).status_code == 401


@pytest.mark.parametrize('age,consent,withdrawn,allowed', [
    ('adult', False, False, True), ('13to17', False, False, True),
    ('under13', False, False, False), ('under13', True, False, False),
    ('13to17', True, False, True), ('13to17', True, True, False),
    ('unknown', False, False, False),
])
def test_age_and_consent(dashboard_db, monkeypatch, age, consent, withdrawn, allowed):
    full(dashboard_db)
    browser = student_client()
    from app import child_authorization
    monkeypatch.setattr(child_authorization, 'under13_available', lambda: True)
    monkeypatch.setattr(child_authorization, 'notice_policy', lambda: ('synthetic', '', 'a' * 64))
    with dashboard_db() as session:
        rule = session.get(AccountPrivacy, 1)
        rule.age_band = age
        if consent:
            evidence = ConsentEvidence(profile_id=1, parent_email='parent@example.test',
                approved_at=s2._now(), confirmed_at=s2._now(), notice_version='synthetic', notice_sha256='a' * 64,
                withdrawn_at=s2._now() if withdrawn else None)
            session.add(evidence); session.flush(); rule.consent_id = evidence.id
        session.commit()
    for path in (PAGE, DATA, RATING, CHARTS):
        assert browser.get(path).status_code == (200 if allowed else 403)
    if age == 'under13' and consent:
        assert 'data-personal-development="book"' not in browser.get('/p-book').text
        assert browser.get('/practice-charts/insights').status_code == 200  # Existing child benefit preserved.


@pytest.mark.parametrize('loss', ['leave', 'hold', 'expiry', 'full_expiry', 's2_off'])
def test_entitlement_loss_is_rechecked(dashboard_db, monkeypatch, loss):
    member = enroll(dashboard_db)
    personal = full(dashboard_db) if loss == 'full_expiry' else None
    browser = student_client()
    assert browser.get(DATA).status_code == 200
    dashboard_db.clock[0] += timedelta(seconds=1)
    with dashboard_db() as session:
        if loss == 'hold':
            s2.suspend_member(session, actor=actor(session), program_id=1,
                              class_id=dashboard_db.ids[0], membership_id=member)
        elif loss in {'leave', 'full_expiry'}:
            s2.leave_class(session, student=student(session), program_id=1, class_id=dashboard_db.ids[0])
        elif loss == 's2_off':
            monkeypatch.delenv('CLASSROOM_S2_ENABLED')
        else:
            dashboard_db.clock[0] += timedelta(days=15)
        session.commit()
    if personal:
        assert browser.get(DATA).status_code == 200  # Personal source survives enrollment loss.
        with dashboard_db() as session:
            session.get(Membership, personal).access_until = s2._now()
            session.commit()
    assert browser.get(DATA).status_code == browser.get(RATING).status_code == browser.get(CHARTS).status_code == 403
    assert browser.get('/practice-charts').status_code == 200


def test_empty_and_year_boundary_snapshot(dashboard_db):
    with dashboard_db() as session:
        empty = service.snapshot(session, 9999, today=date(2027, 1, 4))
    assert empty['rating'] == empty['lifetime_seconds'] == empty['weekly_seconds'] == 0
    assert empty['lifetime'] == []
    assert empty['rating_week'] == '2026-12-28' and empty['rating_week_end'] == '2027-01-03'
    assert len(empty['weekly']) == 7 and all(day['seconds'] == 0 for day in empty['weekly'])
    assert empty['totals']['lifetime']['charts'] == empty['current_verified_streak'] == 0
    assert empty['first_practice'] is empty['last_practice'] is None
    assert len(empty['history']) == 1 and empty['history'][0]['week_start'] == '2027-01-04'
    assert len(empty['categories']) == 23 and empty['categorized_charts'] == 0


def test_expanded_totals_and_complete_weekly_history(dashboard_db):
    enroll(dashboard_db)
    browser = student_client()
    data = browser.get(DATA).json()
    assert data['totals']['lifetime'] == dict(total=6004 / 60, verified=27, pristine=64 / 60,
        total_seconds=6004, verified_seconds=1620, pristine_seconds=64, charts=7, days=6)
    assert data['totals']['weekly'] == dict(total=12.75, verified=0, pristine=.75,
        total_seconds=765, verified_seconds=0, pristine_seconds=45, charts=3, days=2)
    assert data['first_practice'] == '2026-09-21' and data['last_practice'] == TODAY.isoformat()
    assert len(data['history']) == 6
    assert [row['week_start'] for row in data['history']] == [
        (MONDAY - timedelta(weeks=i)).isoformat() for i in range(6)]
    with dashboard_db() as session:
        charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1)).all()
        approved = set(session.scalars(select(PracticeChartVerification.practice_chart_id)))
        for week in data['history']:
            rows = [c for c in charts if week['week_start'] <= c.practice_date.isoformat() <= week['week_end']]
            totals = practice_totals(rows, approved)
            assert all(week[key] == value for key, value in totals.items())
            assert len(week['daily']) == 7
            assert sum(day['seconds'] for day in week['daily']) == week['total_seconds']
    assert all(data['history'][i]['charts'] == data['history'][i]['total_seconds'] == 0 for i in (2, 3, 4))
    for key in ('total_seconds', 'verified_seconds', 'pristine_seconds', 'charts', 'days'):
        assert sum(week[key] for week in data['history']) == data['totals']['lifetime'][key]
    assert data['history'][0]['daily'] == data['weekly']


def test_category_counts_reuse_book_choices_without_inventing_time(dashboard_db):
    full(dashboard_db)
    with dashboard_db() as session:
        charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1).order_by(
            PracticeChart.practice_date, PracticeChart.id)).all()
        charts[0].practice_details = list(PRACTICE_CATEGORIES) + ['Scales', 'custom historical label']
        charts[-1].practice_details = ['Scales', 'Scales', 'Long Tones', '<script>OTHER STUDENT</script>']
        session.commit()
    browser = student_client()
    data = browser.get(DATA).json()
    assert len(data['categories']) == 23
    assert data['categorized_charts'] == 2
    assert [c['name'] for c in data['categories']] == list(PRACTICE_CATEGORIES)
    for category in data['categories']:
        assert category == {'name': category['name'],
            'weekly_charts': int(category['name'] in ('Scales', 'Long Tones')),
            'lifetime_charts': 1 + int(category['name'] in ('Scales', 'Long Tones'))}
    assert data['lifetime_seconds'] == 6004
    assert 'OTHER STUDENT' not in str(data) and 'custom historical label' not in str(data)
    import re
    choices = re.findall(r'name="practice-detail" value="([^"]+)"', browser.get('/p-book').text)
    assert choices == list(PRACTICE_CATEGORIES)


def test_supported_verified_streak_and_zero_minute_records(dashboard_db):
    full(dashboard_db)
    with dashboard_db() as session:
        for offset, source, minutes, responded in [(0, 'p-book', 1, True), (1, 'p-book', 2, True),
                (2, 'p-book', 3, True), (2, 'p-book', 4, True), (3, 'p-book', 5, False),
                (4, 'manual', 6, True), (5, 'p-book', 0, True)]:
            chart = PracticeChart(profile_id=1, practice_date=TODAY-timedelta(days=offset),
                                  minutes=minutes, source=source, instrument='Trumpet')
            session.add(chart); session.flush()
            session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=1,
                status='approved', responded_at=s2._now() if responded else None))
        session.commit()
        assert profile_practice_streak(session, 1, today=TODAY) == 3
        data = service.snapshot(session, 1, today=TODAY)
        assert data['current_verified_streak'] == 3
        assert data['totals']['lifetime']['charts'] == 14  # Includes the zero-minute record.
        assert data['totals']['lifetime']['days'] == 8  # Distinct positive days, not submissions.
        assert service.snapshot(session, 1, today=TODAY + timedelta(days=1))['current_verified_streak'] == 3
        assert service.snapshot(session, 1, today=TODAY + timedelta(days=2))['current_verified_streak'] == 0


def test_submission_pagination_timestamps_and_account_scope(dashboard_db):
    full(dashboard_db)
    stamp = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    with dashboard_db() as session:
        # Repeated submission timestamps require the stable ID tie-breaker.
        for i in range(27):
            session.add(PracticeChart(profile_id=1, practice_date=date(2026, 10, 31),
                minutes=i, source='p-book', instrument='Trumpet', created_at=stamp,
                note='PRIVATE NOTE', practice_details=['Scales']))
        session.commit()
        expected = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == 1).order_by(
            PracticeChart.created_at.desc(), PracticeChart.id.desc())).all()
        expected_minutes = [chart.minutes for chart in expected]
    browser = student_client()
    before = protected_rows(dashboard_db)
    first, second = [browser.get(CHARTS + f'?page={page}') for page in (1, 2)]
    assert first.headers['cache-control'] == 'no-store'
    assert first.headers['referrer-policy'] == 'no-referrer'
    assert first.json()['total'] == second.json()['total'] == 34
    assert len(first.json()['charts']) == 25 and len(second.json()['charts']) == 9
    rows = first.json()['charts'] + second.json()['charts']
    assert [r['seconds'] // 60 for r in rows] == expected_minutes
    old = [r for r in rows if r['practice_date'] == '2026-10-31']
    assert len(old) == 27 and all(r['submitted_at'] == stamp.isoformat() for r in old)
    assert all(r['categories'] == ['Scales'] and r['kind'] == 'Reported' for r in old)
    assert {r['kind'] for r in rows} == {'Reported', 'Verified', 'Pristine'}
    assert all(set(r) == {'practice_date', 'submitted_at', 'seconds', 'kind', 'categories'} for r in rows)
    assert browser.get(CHARTS + '?page=3').json()['charts'] == []
    for invalid in ('0', '-1', '1000001', 'hello'):
        assert browser.get(CHARTS + '?page=' + invalid).status_code == 422
    assert protected_rows(dashboard_db) == before


def test_history_uses_practice_dates_across_year_and_dst_boundaries(dashboard_db):
    with dashboard_db() as session:
        # Student 2's own chart is deliberately outside this synthetic history.
        for day, seconds, submitted in [
            (date(2026, 11, 1), 19, datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)),
            (date(2026, 12, 31), 45, datetime(2027, 1, 1, 6, 30, tzinfo=timezone.utc)),
            (date(2027, 1, 3), 1, datetime(2027, 1, 4, 6, 30, tzinfo=timezone.utc)),
        ]:
            session.add(PracticeChart(profile_id=1, practice_date=day, minutes=0, source='pristine',
                detected_playing_seconds=seconds, instrument='Trumpet', created_at=submitted))
        session.flush()
        data = service.snapshot(session, 1, today=date(2027, 1, 4))
    weeks = {row['week_start']: row for row in data['history']}
    assert weeks['2027-01-04']['total_seconds'] == 0
    assert weeks['2026-12-28']['total_seconds'] == weeks['2026-12-28']['pristine_seconds'] == 46
    assert weeks['2026-10-26']['daily'][-1] == {'date': '2026-11-01', 'seconds': 19}
    assert data['lifetime_seconds'] == 6004 + 19 + 45 + 1
