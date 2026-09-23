"""Small rollout delta; synthetic SQLite records and no delivery services."""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import tester_enrollments as testers
from app.models import TesterEnrollment as Enrollment, WoodchuckProfile
from app.memberships import student_has_full_access
from test_tester_enrollments import tester_db, profile, count, CLAIMED, account_form, prepare_child_services


@pytest.mark.parametrize('value', [None, '', '0', 'false', 'no', 'off'],
                         ids=['unset', 'empty', '0', 'false', 'no', 'off'])
def test_registration_remains_open_for_unset_or_false_values(tester_db, monkeypatch, value):
    if value is None:
        monkeypatch.delenv('C001_REGISTRATION_DISABLED', raising=False)
    else:
        monkeypatch.setenv('C001_REGISTRATION_DISABLED', value)
    assert testers.c001_registration_open() is True
    client = TestClient(app)
    response = client.get('/prebeta/C001', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/guest'
    assert 'Create a C001 Pre-Beta account' in client.get('/guest').text


@pytest.mark.parametrize('value', ['true', '1', 'yes', 'on', 'unexpected'])
def test_disabled_entry_creates_no_claim_and_guest_stays_accountless(tester_db, monkeypatch, value):
    monkeypatch.setenv('C001_REGISTRATION_DISABLED', value)
    client = TestClient(app)
    response = client.get('/prebeta/C001')
    assert response.status_code == 503
    assert response.headers['content-type'].startswith('text/html')
    assert response.headers['cache-control'] == 'no-store'
    assert 'currently closed' in response.text
    guest = client.get('/guest')
    assert guest.status_code == 200
    assert 'Create a C001 Pre-Beta account' not in guest.text
    assert 'guest-confirm-logout' not in guest.text
    with tester_db() as session:
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 0
    request = SimpleNamespace(session={})
    with pytest.raises(ValueError):
        testers.establish_registration_context(request, testers.C001)
    assert request.session == {}
    monkeypatch.setenv('C001_REGISTRATION_DISABLED', 'false')
    assert client.get('/prebeta/C001', follow_redirects=False).status_code == 303
    assert 'Create a C001 Pre-Beta account' in client.get('/guest').text


def test_closure_preserves_established_claim_and_lifetime_access(tester_db, monkeypatch):
    client = TestClient(app)
    client.get('/prebeta/C001')
    monkeypatch.setenv('C001_REGISTRATION_DISABLED', 'true')
    assert client.post('/account/create', data=account_form()).status_code == 200
    with tester_db() as session:
        row = session.query(Enrollment).one()
        assert student_has_full_access(session, row.profile_id)
        assert count(session, Enrollment) == 1


def test_qr_contains_only_canonical_entry_without_claim_or_host_input(tester_db, monkeypatch):
    import app.main as main
    original = main.qr_data_uri
    values = []
    def capture(value):
        values.append(value)
        return original(value)
    monkeypatch.setattr(main, 'qr_data_uri', capture)
    client = TestClient(app)
    response = client.get('/prebeta/C001/display?token=private', headers={'host': 'untrusted.example'})
    assert response.status_code == 200
    open_sentence = 'Scan to explore Guest tools or begin C001 registration.'
    closed_sentence = 'Scan to explore Guest tools. New C001 registration is currently closed.'
    assert open_sentence in response.text
    assert closed_sentence not in response.text
    assert values == ['https://woodshed-woodchuck.onrender.com/prebeta/C001']
    assert 'untrusted.example' not in response.text and 'token=private' not in response.text
    assert 'svg' in base64.b64decode(response.context['entry_qr'].split(',')[1]).decode()
    assert '@media print' in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    monkeypatch.setenv('C001_REGISTRATION_DISABLED', 'true')
    closed = client.get('/prebeta/C001/display')
    assert closed_sentence in closed.text
    assert open_sentence not in closed.text


def test_recorded_age_conflict_is_html_and_guest_requires_explicit_logout(tester_db, monkeypatch):
    prepare_child_services(monkeypatch)
    with tester_db() as session:
        profile(session, 'ADULT', CLAIMED, age='adult')
        session.commit()
    client = TestClient(app)
    assert client.post('/account/login', data={'woodchuck_id': 'WC-TEST-ADULT', 'pin': '2468'}).status_code == 200
    page = client.get('/family/request')
    assert 'Open Guest tools (may ask you to clear this browser session)' in page.text
    data = {'csrf': page.context['csrf'], 'confirm_account': 'WC-TEST-ADULT', 'parent_email': 'parent@example.test'}
    response = client.post('/family/request', data=data)
    assert response.status_code == 409
    assert response.headers['content-type'].startswith('text/html')
    assert 'A recorded age cannot be changed here' in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    json_response = client.post('/family/request', data=data, headers={'accept': 'application/json'})
    assert json_response.status_code == 409
    assert 'recorded age' in json_response.json()['detail']
    assert 'guest-confirm-logout' in client.get('/guest').text
    assert client.get('/account/me').json()['authenticated'] is True


def test_family_error_privacy_status_and_data_semantics(tester_db):
    client = TestClient(app)
    for path, status in [('/family/activate/invalid', 409), ('/family/parent', 403)]:
        response = client.get(path)
        assert response.status_code == status
        assert response.headers['content-type'].startswith('text/html')
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['referrer-policy'] == 'no-referrer'
    response = client.get('/family/parent/data')
    assert response.status_code == 403
    assert 'detail' in response.json()
    assert client.post('/family/request', data={}).status_code == 403
