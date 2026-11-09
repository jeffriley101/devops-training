"""S4 synthetic authority, cookie replay and complete database noninterference."""
from datetime import timedelta
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import classroom_guest as guest, classroom_s2 as s2, main, login_limits
from app.db import Base
from app.classroom_models import ClassroomClassState, ClassroomEntryCode, ClassroomEntitlement
from test_classroom_s1 import adult_pin_hash, classroom_db, provision
from test_classroom_s2 import ready, NOW


@pytest.fixture
def s4_web(classroom_db, monkeypatch):
    for name, module in tuple(sys.modules.items()):
        if name.startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', classroom_db)
    for key, value in {'CLASSROOM_S2_ENABLED': '1', 'CLASSROOM_GUEST_ENABLED': '1',
                       'LOGIN_RATE_LIMIT_MODE': 'memory', 'LOGIN_RATE_LIMIT_REQUIRED': 'false',
                       'PUBLIC_BASE_URL': 'http://testserver'}.items():
        monkeypatch.setenv(key, value)
    clock = [NOW.timestamp()]
    monkeypatch.setattr(guest, 'now', lambda: clock[0])
    monkeypatch.setattr(s2, '_now', lambda: NOW + timedelta(seconds=clock[0] - NOW.timestamp()))
    monkeypatch.setattr(guest, '_memory', guest.MemoryContexts())
    login_limits._backend.cache_clear()
    with classroom_db() as session:
        provision(session); provision(session, 2)
        session.commit()
        klass, code, visible = ready(session)
        session.commit()
        other, other_code, _ = ready(session, program_id=2, preferred='OTHER2026')
        session.commit()
    yield SimpleNamespace(factory=classroom_db, clock=clock, klass=klass.id, code=code.id,
                          other=other.id, other_code=other_code.id)
    login_limits._backend.cache_clear()


def fingerprints(factory):
    with factory() as session:
        return {table.name: hashlib.sha256(json.dumps(sorted(
            json.dumps(dict(row._mapping), sort_keys=True, default=str)
            for row in session.execute(select(table))), separators=(',', ':')).encode()).hexdigest()
            for table in Base.metadata.sorted_tables}


def enter(client, **changes):
    return client.post('/guest/classroom/admit', json={'program': 1, 'code': 'BAND2026', **changes})


def check(client, binding, action='check'):
    return client.post('/guest/classroom/' + action, json={'binding': binding})


def test_admission_revalidation_and_leave_have_no_sql_changes(s4_web):
    before = fingerprints(s4_web.factory)
    client = TestClient(main.app)
    page = client.get('/guest/classroom')
    assert page.status_code == 200
    assert 'connect-src http://testserver/guest/classroom/admit http://testserver/guest/classroom/check http://testserver/guest/classroom/leave;' in page.headers['content-security-policy']
    assert 'form-action \'none\'' in page.headers['content-security-policy']
    response = enter(client)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {'active', 'binding', 'expires_in'} and payload['expires_in'] == 900
    assert check(client, payload['binding']).status_code == 200
    assert check(client, payload['binding'], 'leave').json() == {'active': False}
    assert check(client, payload['binding']).status_code == 403
    assert fingerprints(s4_web.factory) == before


@pytest.mark.parametrize('changes', [
    {'program': 2}, {'program': 999}, {'program': True}, {'program': '1'}, {'program': -1},
    {'code': 'MISSING1'}, {'code': 'ＢＡＮＤ2026'}, {'code': 'BAND2026\n'}, {'code': None},
    {'code': 'x'*65}, {'class': 999}, {'profile_id': 1},
])
def test_invalid_admission_is_uniform_and_nonmutating(s4_web, changes):
    before = fingerprints(s4_web.factory)
    response = enter(TestClient(main.app), **changes)
    assert response.status_code == 403
    assert response.json() == {'detail': guest.UNAVAILABLE}
    assert 'set-cookie' not in response.headers
    assert fingerprints(s4_web.factory) == before


@pytest.mark.parametrize('change', ['closed', 'entry_closed', 'rotated', 'revoked', 'future_code',
                                    'expired', 'ended', 'capacity', 'gate_off'])
def test_current_class_authority_is_required_on_each_check(s4_web, monkeypatch, change):
    client = TestClient(main.app); binding = enter(client).json()['binding']
    s4_web.clock[0] += 2
    with s4_web.factory() as session:
        state = session.get(ClassroomClassState, s4_web.klass)
        code = session.get(ClassroomEntryCode, s4_web.code)
        package = session.scalar(select(ClassroomEntitlement).where(ClassroomEntitlement.program_id == 1))
        if change == 'closed': state.state = 'archived'; state.enrollment_open = False
        elif change == 'entry_closed': state.enrollment_open = False
        elif change in {'rotated', 'revoked'}:
            code.is_current = False; code.revoked_at = s2._now(); code.revoked_by_verifier_id = 1
        elif change == 'future_code': code.issued_at = s2._now() + timedelta(seconds=1)
        elif change == 'expired': package.ends_at = s2._now()
        elif change == 'ended': package.status = 'ended'; package.ended_at = s2._now()
        elif change == 'capacity': package.class_limit = 0
        else: monkeypatch.delenv('CLASSROOM_GUEST_ENABLED')
        session.commit()
    before = fingerprints(s4_web.factory)
    assert check(client, binding).status_code == (404 if change == 'gate_off' else 403)
    assert fingerprints(s4_web.factory) == before


def test_renewal_before_expiry_and_rejection_at_expiry(s4_web):
    client = TestClient(main.app); binding = enter(client).json()['binding']
    s4_web.clock[0] += 899
    assert check(client, binding).status_code == 200
    s4_web.clock[0] += 900
    assert check(client, binding).status_code == 403
    assert check(client, binding).status_code == 403


def test_logout_and_cookie_replay_cannot_resurrect_context(s4_web):
    client = TestClient(main.app); binding = enter(client).json()['binding']
    old = client.cookies.get('session')
    assert check(client, binding, 'leave').status_code == 200
    replay = TestClient(main.app); replay.cookies.set('session', old)
    assert check(replay, binding).status_code == 403


def test_other_class_entry_invalidates_old_tab_and_scope_cannot_be_substituted(s4_web):
    client = TestClient(main.app); first = enter(client).json()['binding']
    old = client.cookies.get('session')
    second = enter(client, program=2, code='OTHER2026').json()['binding']
    assert check(client, first).status_code == 403
    assert check(client, second).status_code == 200
    replay = TestClient(main.app); replay.cookies.set('session', old)
    assert check(replay, first).status_code == 403
    assert client.post('/guest/classroom/check', json={'binding': second, 'program': 1}).status_code == 403


@pytest.mark.parametrize('action', ['login', 'logout', 'adult'])
def test_native_account_transitions_retire_guest_context(s4_web, action):
    client = TestClient(main.app); binding = enter(client).json()['binding']
    cookie = client.cookies.get('session')
    if action == 'login':
        result = client.post('/account/login', data={'woodchuck_id': 'WC-SYNTHETIC', 'pin': '2468'})
    elif action == 'adult':
        result = client.post('/trusted-verifiers/login', data={'email': 'adult1@example.test', 'pin': '2468'})
    else:
        result = client.post('/account/logout')
    assert result.status_code == 200, result.text
    assert check(client, binding).status_code == 403
    replay = TestClient(main.app); replay.cookies.set('session', cookie)
    assert check(replay, binding).status_code == 403
    if action != 'logout': assert enter(client).status_code == 403


@pytest.mark.parametrize('failure', ['off', 'backend', 'production_memory'])
def test_missing_transient_protection_fails_closed_but_general_guest_works(s4_web, monkeypatch, failure):
    if failure == 'off': monkeypatch.setenv('LOGIN_RATE_LIMIT_MODE', 'off')
    elif failure == 'production_memory': monkeypatch.setattr(login_limits, 'is_production', lambda: True)
    else:
        def fail(): raise RuntimeError('SYNTHETIC_SECRET_MUST_NOT_ESCAPE')
        monkeypatch.setattr(guest, 'backend', fail)
    client = TestClient(main.app)
    response = enter(client)
    assert response.status_code == 503 and 'SYNTHETIC_SECRET' not in response.text
    page = client.get('/guest')
    assert page.status_code == 200 and "connect-src 'none'" in page.headers['content-security-policy']


def test_enumeration_limit_survives_cookie_clearing(s4_web):
    before = fingerprints(s4_web.factory)
    for _ in range(100):
        assert enter(TestClient(main.app), code='INVALID1').status_code == 403
    response = enter(TestClient(main.app))
    assert response.status_code == 429 and int(response.headers['retry-after']) > 0
    assert fingerprints(s4_web.factory) == before


@pytest.mark.parametrize('kind', ['logout', 'expiry'])
def test_revalidation_after_authority_wait_never_recreates_context(s4_web, monkeypatch, kind):
    client = TestClient(main.app); binding = enter(client).json()['binding']
    original = guest.code_authority
    def waited(*args, **kwargs):
        row = original(*args, **kwargs)
        if kind == 'logout': guest._memory.rows.clear()
        else: s4_web.clock[0] += 900
        return row
    monkeypatch.setattr(guest, 'code_authority', waited)
    assert check(client, binding).status_code == 403
    monkeypatch.setattr(guest, 'code_authority', original)
    assert check(client, binding).status_code == 403


@pytest.mark.parametrize('headers,body', [
    ({'Origin': 'https://outside.example'}, '{}'), ({'Origin': ''}, '{}'),
    ({'Origin': 'null', 'Sec-Fetch-Site': 'same-origin'}, '{}'),
    ({'Sec-Fetch-Site': 'cross-site'}, '{}'), ({'Content-Type': 'text/plain'}, '{}'),
    ({}, 'x'*513), ({}, 'invalid-json'), ({}, '[]'),
])
def test_entry_requires_bounded_same_origin_json(s4_web, headers, body):
    response = TestClient(main.app).post('/guest/classroom/admit', content=body,
        headers={'Content-Type': 'application/json', **headers})
    assert response.status_code == 403


def test_gate_off_no_s4_page_or_endpoints_and_no_guest_privilege(s4_web, monkeypatch):
    client = TestClient(main.app); binding = enter(client).json()['binding']
    for flag in ('CLASSROOM_GUEST_ENABLED', 'CLASSROOM_S2_ENABLED', 'CLASSROOM_S1_ENABLED'):
        with monkeypatch.context() as patch:
            patch.delenv(flag)
            assert client.get('/guest/classroom').status_code == 404
            assert check(client, binding).status_code == 404
            page = client.get('/guest')
            assert 'Enter Guest Classroom with a Class code' not in page.text
            assert "connect-src 'none'" in page.headers['content-security-policy']
    with s4_web.factory() as session:
        with pytest.raises(s2.ClassroomDenied):
            s2.student_from_request(session, SimpleNamespace(session={guest.KEY: 'x'}, headers={}))


def test_gate_shutdown_still_allows_irrevocable_guest_exit(s4_web, monkeypatch):
    client = TestClient(main.app); binding = enter(client).json()['binding']
    cookie = client.cookies.get('session')
    monkeypatch.delenv('CLASSROOM_GUEST_ENABLED')
    assert check(client, binding).status_code == 404
    assert check(client, binding, 'leave').status_code == 200
    monkeypatch.setenv('CLASSROOM_GUEST_ENABLED', '1')
    replay = TestClient(main.app); replay.cookies.set('session', cookie)
    assert check(replay, binding).status_code == 403


def test_guest_cannot_submit_practice_or_access_student_or_adult_services(s4_web, monkeypatch):
    for flag in ('CLASSROOM_ENROLLMENT_V2_ENABLED', 'CLASSROOM_DIRECTORS_CHOICE_ENABLED',
                 'CLASSROOM_DIRECTOR_ENABLED', 'CLASSROOM_S3_ENABLED'):
        monkeypatch.setenv(flag, '1')
    client = TestClient(main.app); enter(client)
    before = fingerprints(s4_web.factory)
    for path in ('/account/me', '/classroom', '/classroom/entry', '/classroom/director',
                 '/classroom/directors-choice/student/1/classes/' + str(s4_web.klass)):
        result = client.get(path, follow_redirects=False)
        if path == '/account/me':
            assert result.status_code == 200 and result.json()['authenticated'] is False
        elif path == '/classroom':
            assert result.status_code == 200 and 'Sign in to your student account' in result.text
            assert 'data-student-private' not in result.text
        else:
            assert result.status_code in {401, 403, 303}, (path, result.status_code)
        assert all(secret not in result.text for secret in ('WC-SYNTHETIC', 'Private Student', 'adult1@example.test'))
    result = client.post('/practice-charts/pristine', json={'detected_playing_seconds': 60, 'submission_key': 'guest-no-write'})
    assert result.status_code == 401, result.text
    assert fingerprints(s4_web.factory) == before
