"""V3 field contract: current individual chart grants, never roster authority."""
from datetime import datetime, time, timedelta, timezone
import json

import pytest
from sqlalchemy import event, select

from app import classroom_dashboard, classroom_chart_sharing as sharing, classroom_s2 as s2
from app.age_models import AccountPrivacy
from app.child_models import ConsentEvidence
from app.classroom_models import ClassroomTeachingAssignment, ClassroomReportingPeriod
from app.models import PracticeChart, PracticeChartVerification
from app.practice_charts import PRACTICE_CATEGORIES
from app.student_practice_metrics import CENTRAL, practice_totals, week_start
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, student, join
from test_classroom_director import web, login, confirm
from test_classroom_student_invitation_routes import invitation_web
from test_classroom_invited_chart_sharing import personal_snapshot


@pytest.fixture
def v3_web(invitation_web, monkeypatch):
    factory = invitation_web
    monkeypatch.delenv('CLASSROOM_S3_ENABLED', raising=False)
    monday = week_start(factory.clock[0].astimezone(CENTRAL).date())
    factory.monday = monday
    submitted = datetime.combine(monday, time(0, 30), CENTRAL).astimezone(timezone.utc)
    with factory() as session:
        member = join(session, visible='FIRST2026')
        factory.member_id = member.id
        assignment = session.scalar(select(ClassroomTeachingAssignment).where(
            ClassroomTeachingAssignment.class_id == factory.ids[0], ClassroomTeachingAssignment.verifier_id == 2))
        factory.assignment_id = assignment.id
        chart_ids = []
        specs = [(monday, 10, 'p-book', None, ['Scales', 'Scales'], True),
                 (monday - timedelta(days=1), 20, 'p-book', None, ['Scales'], True),
                 (monday, 1, 'pristine', 75, [], True),
                 (monday + timedelta(days=1), 0, 'p-book', None, list(PRACTICE_CATEGORIES) + ['Custom private activity'], False)]
        for index, (day, minutes, source, seconds, categories, approved) in enumerate(specs):
            chart = PracticeChart(profile_id=1, practice_date=day, minutes=minutes, source=source,
                detected_playing_seconds=seconds, practice_details=categories, instrument='Trumpet',
                note=f'Shared synthetic chart {index}', created_at=submitted + timedelta(minutes=index))
            session.add(chart); session.flush()
            chart_ids.append(chart.id)
            if approved:
                session.add(PracticeChartVerification(practice_chart_id=chart.id, verifier_id=1,
                    status='approved', response_note='NEVER DISCLOSE VERIFIER RESPONSE'))
            sharing.set_sharing(session, student=student(session), chart_id=chart.id, assignment_ids=[assignment.id])
        # Duplicate approvals must not double count the same chart.
        session.add(PracticeChartVerification(practice_chart_id=chart_ids[0], verifier_id=3, status='approved'))
        for profile_id in (1, 2):
            session.add(PracticeChart(profile_id=profile_id, practice_date=monday, minutes=999,
                instrument='Flute', note='UNSHARED CHART MUST NOT APPEAR', created_at=submitted))
        session.commit()
    factory.chart_ids = chart_ids
    return factory


def url(factory):
    return f'/classroom/director/1/classes/{factory.ids[0]}'


def payload(factory, director=None):
    response = confirm(director or login(2), url(factory))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.context['dashboard']


def test_shared_totals_categories_graphs_and_submission_boundaries(v3_web):
    before = personal_snapshot(v3_web)
    data = payload(v3_web)
    assert data['scope'] == 'explicitly_shared_charts'
    assert len(data['students']) == 1
    row = data['students'][0]
    assert row['lifetime'] == {'total': 31.25, 'verified': 30, 'pristine': 1.25,
        'total_seconds': 1875, 'verified_seconds': 1800, 'pristine_seconds': 75, 'charts': 4, 'days': 2}
    current = row['weekly'][v3_web.monday.isoformat()]
    assert (current['total_seconds'], current['verified_seconds'], current['pristine_seconds']) == (675, 600, 75)
    assert (current['charts'], current['days'], current['submissions']) == (3, 1, 3)
    assert current['daily_seconds'] == [675, 0, 0, 0, 0, 0, 0]
    assert data['categories'] == list(PRACTICE_CATEGORIES) and len(data['categories']) == 23
    scales = data['categories'].index('Scales')
    assert current['categories'][scales] == 2 and row['categories'][scales] == 3
    assert all(value == 1 for i, value in enumerate(current['categories']) if i != scales)
    assert [point['cumulative_seconds'] for point in row['history']] == [1200, 1875, 1875]
    assert row['last_submission'] == row['charts'][0]['submitted_at']
    # All existing authoritative totals still apply; no personal rating is computed.
    with v3_web() as session:
        charts = session.scalars(select(PracticeChart).where(PracticeChart.id.in_(v3_web.chart_ids))).all()
        assert row['lifetime'] == practice_totals(charts, set(v3_web.chart_ids[:3]))
        assert session.scalar(select(ClassroomReportingPeriod.id)) is None
    encoded = json.dumps(data)
    for secret in ('999', 'UNSHARED', 'VERIFIER RESPONSE', 'Another private student', 'WC-SYNTHETIC', 'rating', 'profile_id'):
        assert secret not in encoded
    assert personal_snapshot(v3_web) == before


def test_no_authority_expansion_from_metadata_or_request_parameters(v3_web):
    director = login(2)
    assert 'Shared synthetic chart' not in director.get(url(v3_web)).text
    for params in ({'profile_id': '2'}, {'class_id': str(v3_web.ids[1])}, {'fields': 'rating'}, {'week': '2020-01-01'}):
        assert confirm(director, url(v3_web), **params).status_code == 400
    assert confirm(login(3), url(v3_web)).status_code == 404
    assert payload(v3_web, login(1)) is None  # subscriber/admin is not the assigned recipient
    assert confirm(director, f'/classroom/director/2/classes/{v3_web.ids[0]}').status_code == 404
    assert confirm(director, f'/classroom/director/1/classes/{v3_web.ids[1]}').status_code == 404


@pytest.mark.parametrize('restriction', ['under13', 'unknown', 'consent', 'withdraw', 'suspend', 'leave', 'expired'])
def test_current_restrictions_remove_all_rows_and_totals(v3_web, restriction):
    assert payload(v3_web)['students']
    v3_web.clock[0] += timedelta(minutes=1)
    with v3_web() as session:
        if restriction in ('under13', 'unknown'):
            session.get(AccountPrivacy, 1).age_band = restriction
        elif restriction == 'consent':
            evidence = ConsentEvidence(profile_id=1, parent_email='synthetic@example.test',
                approved_at=s2._now(), confirmed_at=s2._now(), notice_version='synthetic', notice_sha256='a' * 64)
            session.add(evidence); session.flush()
            session.get(AccountPrivacy, 1).consent_id = evidence.id
        elif restriction == 'withdraw':
            for chart_id in v3_web.chart_ids:
                sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[])
        elif restriction == 'suspend':
            s2.suspend_member(session, actor=actor(session), program_id=1, class_id=v3_web.ids[0], membership_id=v3_web.member_id)
        elif restriction == 'leave':
            s2.leave_class(session, student=student(session), program_id=1, class_id=v3_web.ids[0])
        else:
            v3_web.clock[0] += timedelta(days=90)
        session.commit()
    response = confirm(login(2), url(v3_web))
    assert response.status_code == 404 or response.context['dashboard']['students'] == []
    assert 'Shared synthetic chart' not in response.text


def test_revoked_rows_do_not_query_chart_contents_or_verification(v3_web):
    with v3_web() as session:
        for chart_id in v3_web.chart_ids:
            sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[])
        session.commit()
    statements = []
    def capture(conn, cursor, sql, *args):
        statements.append(sql.lower())
    engine = v3_web.kw['bind']
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        assert payload(v3_web)['students'] == []
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not any('practice_charts' in sql or 'practice_chart_verifications' in sql for sql in statements)


def test_same_name_students_remain_distinct_and_notes_are_escaped(v3_web):
    from app.models import WoodchuckProfile
    with v3_web() as session:
        other = s2.authenticate_student(session, woodchuck_id='WC-SECOND', pin='2468')
        s2.join_class(session, student=other, program_id=1, code='FIRST2026')
        session.get(WoodchuckProfile, 2).display_name = 'Private Student'
        chart = PracticeChart(profile_id=2, practice_date=v3_web.monday, minutes=7, instrument='Flute',
            note='<script>window.PRIVATE_XSS=true</script>', created_at=v3_web.clock[0])
        session.add(chart); session.flush()
        sharing.set_sharing(session, student=other, chart_id=chart.id, assignment_ids=[v3_web.assignment_id])
        session.commit()
    response = confirm(login(2), url(v3_web))
    rows = response.context['dashboard']['students']
    assert len(rows) == 2 and rows[0]['key'] != rows[1]['key']
    assert sorted(row['lifetime']['total'] for row in rows) == [7, 31.25]
    assert '<script>window.PRIVATE_XSS' not in response.text


def test_invitation_alone_is_not_data_and_accepted_shares_survive_day_seven(invitation_web):
    from app import classroom_student_invitations as invitations
    from test_classroom_student_invitation_routes import issue, routes
    client, director, ident, csrf = issue(invitation_web)
    assert payload(invitation_web, director)['students'] == []
    assert client.post(f'{routes.STUDENT_PATH}/{ident}/accept', data={'csrf': csrf}).status_code == 200
    assert payload(invitation_web, director)['students'] == []
    with invitation_web() as session:
        chart = PracticeChart(profile_id=1, practice_date=invitation_web.clock[0].date(),
            minutes=9, instrument='Trumpet', note='One explicitly invited chart', created_at=invitation_web.clock[0])
        session.add(chart); session.flush()
        assignment = sharing.choices(session, student(session))[0]['assignment_id']
        sharing.set_sharing(session, student=student(session), chart_id=chart.id, assignment_ids=[assignment])
        session.commit()
    invitation_web.clock[0] += timedelta(days=8)
    row = payload(invitation_web, director)['students'][0]
    assert row['lifetime']['charts'] == 1 and row['lifetime']['total'] == 9
    with invitation_web() as session:
        invitations.revoke(session, actor=actor(session, 2), invitation_id=ident)
        session.commit()
    assert payload(invitation_web, director)['students'] == []


def test_comprehensive_boundary_grants_never_expand_the_shared_chart_set(v3_web, monkeypatch):
    from app import classroom_reporting as reporting
    monkeypatch.setenv('CLASSROOM_S3_ENABLED', '1')
    original = payload(v3_web)['students']
    with v3_web() as session:
        reporting.grant_reporting(session, student=student(session), program_id=1, class_id=v3_web.ids[0],
            scope_version=reporting.SCOPE_VERSION, notice_version=reporting.NOTICE_VERSION)
        session.commit()
    assert payload(v3_web)['students'] == original
    with v3_web() as session:
        reporting.withdraw_reporting(session, student=student(session), program_id=1, class_id=v3_web.ids[0])
        session.commit()
    # A separately selected historical chart remains an individual grant; the
    # boundary-only reporting interval neither imports nor rescues any chart.
    assert payload(v3_web)['students'] == original
