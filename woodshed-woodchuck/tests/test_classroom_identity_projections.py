"""Existing adult readers use WC# without expanding private-school identity scope."""
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import (account_routes, main, session_revocations, verifier_routes,
    classroom as s1, classroom_chart_sharing as sharing, classroom_dashboard)
from app.age_models import AccountPrivacy
from app.email_service import DeliveryResult, EmailService
from app.models import WoodchuckProfile, StudentVerifierConnection, PracticeChart, PracticeChartVerification
from app.verifiers import accepted_active_verifier_students, band_director_students
from test_classroom_s1 import adult_pin_hash, classroom_db, actor
from test_classroom_s2 import s2_db, NOW, student
from test_classroom_s3_reporting import report_db, setup, chart
from test_classroom_chart_sharing import configured


@pytest.fixture
def adult_web(classroom_db, monkeypatch):
    for module in (account_routes, main, session_revocations, verifier_routes):
        monkeypatch.setattr(module, 'SessionLocal', classroom_db)
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://testserver')
    with classroom_db() as session:
        session.get(WoodchuckProfile, 1).display_name = 'PRIVATE_PUBLIC_NICKNAME'
        session.commit()
    return classroom_db


@pytest.mark.parametrize('role', ['verifier', 'band_director'])
def test_existing_adult_roster_and_pending_chart_apis_hide_nickname(adult_web, role):
    with adult_web() as session:
        session.add(StudentVerifierConnection(profile_id=1, verifier_id=2, role=role, status='accepted'))
        record = PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=5, instrument='Trumpet')
        session.add(record); session.flush()
        session.add(PracticeChartVerification(practice_chart_id=record.id, verifier_id=2, status='pending'))
        session.commit()
    client = TestClient(main.app)
    assert client.post('/trusted-verifiers/login', data={'email': 'adult2@example.test', 'pin': '2468'}).status_code == 200
    for path in ['/trusted-verifiers/me', '/trusted-verifiers/practice-charts']:
        response = client.get(path)
        assert response.status_code == 200
        assert 'WC-SYNTHETIC' in response.text and 'PRIVATE_PUBLIC_NICKNAME' not in response.text
        assert 'school_name' not in response.text
    with adult_web() as session:
        # Private account nickname remains the account owner's own identity.
        assert account_routes.profile_payload(session.get(WoodchuckProfile, 1))['display_name'] == 'PRIVATE_PUBLIC_NICKNAME'


@pytest.mark.parametrize('role', ['verifier', 'band_director'])
def test_legacy_roster_uses_wc_order_without_school_lookup_or_nickname_column(adult_web, role):
    with adult_web() as session:
        profile = session.get(WoodchuckProfile, 1)
        profile.display_name = 'AAAA_PRIVATE_ALIAS'
        session.add(WoodchuckProfile(id=2, woodchuck_id='WC-AAA', display_name='ZZZZ_PRIVATE_ALIAS',
            pin_hash=profile.pin_hash, instrument='Flute', level='Beginner', goal='Practice'))
        session.flush()
        session.add(AccountPrivacy(profile_id=2, age_band='13to17', declared_at=NOW))
        session.add_all([StudentVerifierConnection(profile_id=ident, verifier_id=2, role=role, status='accepted') for ident in [1, 2]])
        session.commit()
    statements = []
    def capture(conn, cursor, statement, params, context, many): statements.append(statement.lower())
    engine = adult_web.kw['bind']
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        with adult_web() as session:
            reader = accepted_active_verifier_students if role == 'verifier' else band_director_students
            rows = reader(session, verifier_id=2)
            assert [row['display_name'] for row in rows] == ['WC-AAA', 'WC-SYNTHETIC']
            assert reader(session, verifier_id=3) == []
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert not any('classroom_roster_identities' in sql for sql in statements)


def test_invitation_delivery_uses_neutral_wc_without_altering_authentication(adult_web, monkeypatch):
    delivered = []
    def capture(self, **values):
        delivered.append(values)
        return DeliveryResult(True, 'captured')
    monkeypatch.setattr(EmailService, 'send_invitation', capture)
    client = TestClient(main.app)
    assert client.post('/account/login', data={'woodchuck_id': 'WC-SYNTHETIC', 'pin': '2468'}).status_code == 200
    response = client.post('/trusted-verifiers/invitations', data={'email': 'adult3@example.test', 'role': 'band_director'})
    assert response.status_code == 200
    assert delivered and delivered[0]['student_name'] == 'WC-SYNTHETIC'
    assert 'PRIVATE_PUBLIC_NICKNAME' not in json.dumps(delivered)
    unauthorized = TestClient(main.app)
    assert unauthorized.post('/account/login', data={'woodchuck_id': 'WC-SYNTHETIC', 'pin': '0000'}).status_code == 401


def test_classroom_shared_chart_and_v3_labels_do_not_expand_identity_authority(configured):
    factory, clock = configured
    with factory() as session:
        class_id, member_id, assignment_id = setup(session)
        session.get(WoodchuckProfile, 1).display_name = 'CLASSROOM_PRIVATE_NICKNAME'
        chart_id = chart(session, clock[0].date(), clock[0])
        sharing.set_sharing(session, student=student(session), chart_id=chart_id, assignment_ids=[assignment_id])
        records = sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id)
        assert records[0]['student'] == 'WC-SYNTHETIC'
        data = classroom_dashboard.dashboard(session, actor=actor(session, 2), program_id=1, class_id=class_id)
        assert data['students'][0]['name'] == 'WC-SYNTHETIC'
        assert 'CLASSROOM_PRIVATE_NICKNAME' not in json.dumps(data)
        with pytest.raises(s1.ClassroomDenied):
            sharing.shared_charts(session, actor=actor(session, 3), program_id=1, class_id=class_id)
        clock[0] += timedelta(minutes=1)
        s1.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignment_id)
        with pytest.raises(s1.ClassroomDenied):
            sharing.shared_charts(session, actor=actor(session, 2), program_id=1, class_id=class_id)
