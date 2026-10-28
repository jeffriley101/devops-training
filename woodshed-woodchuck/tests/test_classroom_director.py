"""Director metadata navigation and default-denied student access."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import account_routes, classroom, classroom_director as routes, main, session_revocations, verifier_routes
from app.classroom_models import ClassroomTeachingAssignment
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import ready, s2_db


@pytest.fixture
def web(s2_db, monkeypatch):
    for module in (routes, account_routes, main, session_revocations, verifier_routes):
        monkeypatch.setattr(module, 'SessionLocal', s2_db)
    monkeypatch.setenv('PUBLIC_BASE_URL', 'http://testserver')
    monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
    with s2_db() as session:
        first, _, _ = ready(session, program_id=1, preferred='FIRST2026')
        classroom.assign_teacher(session, actor=actor(session), program_id=1,
                                 class_id=first.id, verifier_id=2)
        session.commit()
        second, _, _ = ready(session, program_id=2, preferred='SECOND2026')
        second.display_name = 'Jazz Ensemble'
        session.commit()
        ids = first.id, second.id
    s2_db.ids = ids
    return s2_db


def login(adult=1):
    client = TestClient(main.app)
    response = client.post('/trusted-verifiers/login', data={'email': f'adult{adult}@example.test', 'pin': '2468'})
    assert response.status_code == 200
    return client


def confirm(client, path, **values):
    page = client.get(path)
    assert page.status_code == 200
    return client.post(path, data={'csrf': page.context['csrf'], 'pin': '2468', **values})


def test_default_off_and_existing_dashboard(web, monkeypatch):
    client = login()
    monkeypatch.delenv('CLASSROOM_DIRECTOR_ENABLED')
    for path in ('/classroom/director', f'/classroom/director/1/classes/{web.ids[0]}'):
        assert client.get(path).status_code == 404
        assert client.post(path, data={}).status_code == 404
    assert '/classroom/director' not in client.get('/trusted-verifiers/dashboard').text
    assert '/classroom/director' not in client.get('/band-director/dashboard').text
    assert client.get('/band-director/dashboard').status_code == 200
    for flag in ('CLASSROOM_S1_ENABLED', 'CLASSROOM_S2_ENABLED'):
        monkeypatch.setenv('CLASSROOM_DIRECTOR_ENABLED', '1')
        with monkeypatch.context() as local:
            local.delenv(flag)
            assert client.get('/classroom/director').status_code == 404
            assert '/classroom/director' not in client.get('/band-director/dashboard').text


def test_metadata_scope_no_student_queries_or_output(web):
    client = login(2)
    assert '/classroom/director' in client.get('/band-director/dashboard').text
    statements = []
    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement.lower())
    event.listen(web.kw['bind'], 'before_cursor_execute', capture)
    try:
        home = confirm(client, '/classroom/director')
        assert home.status_code == 200 and 'Program 1' in home.text and 'Concert Band' in home.text
        assert 'Program 2' not in home.text and 'Jazz Ensemble' not in home.text
        assert 'Private Student' not in home.text
        assert home.headers['cache-control'] == 'no-store'
        assert home.headers['referrer-policy'] == 'no-referrer'
        detail = confirm(client, f'/classroom/director/1/classes/{web.ids[0]}')
        assert detail.status_code == 200 and 'Concert Band' in detail.text
        assert detail.context['dashboard']['students'] == []
        assert 'Full-roster submission percentage: unavailable.' in detail.text
        for text in ('Private Student', 'WC-SYNTHETIC'):
            assert text not in home.text and text not in detail.text
        assert confirm(client, f'/classroom/director/2/classes/{web.ids[1]}').status_code == 404
        assert confirm(client, f'/classroom/director/1/classes/{web.ids[1]}').status_code == 404
    finally:
        event.remove(web.kw['bind'], 'before_cursor_execute', capture)
    forbidden = ('woodchuck_profiles', 'practice_charts', 'classroom_student_memberships',
                 'classroom_membership_periods', 'reward_grants', 'arcade_play_sessions')
    assert not [sql for sql in statements if any(name in sql for name in forbidden)]


def test_login_csrf_origin_and_entitlement_are_required(web, monkeypatch):
    guest = TestClient(main.app)
    assert guest.get('/classroom/director', follow_redirects=False).status_code == 303
    assert guest.post('/classroom/director', data={'csrf': 'bad', 'pin': '2468'}).status_code in (401, 403)
    client = login()
    page = client.get('/classroom/director')
    data = {'csrf': page.context['csrf'], 'pin': '2468'}
    assert client.post('/classroom/director', data={**data, 'csrf': 'bad'}).status_code == 403
    assert client.post('/classroom/director', data=data, headers={'Origin':'https://outside.invalid'}).status_code == 403
    assert client.post('/classroom/director', data={**data, 'pin':'9999'}).status_code == 401
    assert client.post('/classroom/director', data={**data, 'profile_id':'1'}).status_code == 400
    original = routes.classroom_s2.entitlement_decision
    def restricted(session, program_id, **kwargs):
        result = original(session, program_id, **kwargs)
        return type(result)(False, 'expired') if program_id == 1 else result
    monkeypatch.setattr(routes.classroom_s2, 'entitlement_decision', restricted)
    home = client.post('/classroom/director', data=data)
    assert home.status_code == 200 and 'Program 1' not in home.text and 'Program 2' in home.text
    assert confirm(client, f'/classroom/director/1/classes/{web.ids[0]}').status_code == 404


def test_assignment_revocation_takes_effect_on_next_class_access(web):
    client = login(2)
    path = f'/classroom/director/1/classes/{web.ids[0]}'
    assert confirm(client, path).status_code == 200
    with web() as session:
        assignment = session.scalar(select(ClassroomTeachingAssignment).where(
            ClassroomTeachingAssignment.verifier_id == 2,
            ClassroomTeachingAssignment.class_id == web.ids[0]))
        classroom.end_teaching(session, actor=actor(session), program_id=1,
                               assignment_id=assignment.id)
        session.commit()
    assert confirm(client, path).status_code == 404
    assert 'Concert Band' not in confirm(client, '/classroom/director').text
