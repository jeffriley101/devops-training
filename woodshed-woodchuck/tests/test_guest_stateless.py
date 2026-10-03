"""Guest source disposal and account authority; synthetic databases only."""
import base64
import json
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import TesterEnrollment as Enrollment, WoodchuckProfile, WoodchuckState
from app.c001_abuse import BROWSER_KEY
from test_guest_boundary import guest_db, counts


def session_data(client):
    cookie = client.cookies.get("session")
    return json.loads(base64.b64decode(cookie.split(".")[0])) if cookie else {}


def test_public_guest_has_only_local_adapters_and_safe_native_chart_form(guest_db):
    response = TestClient(app).get("/guest")
    assert response.status_code == 200
    assert "connect-src 'none'" in response.headers["content-security-policy"]

    class GuestHTML(HTMLParser):
        def __init__(self):
            super().__init__()
            self.chart = False
            self.fields = []
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "script" and "src" in attrs:
                self.scripts.append(attrs["src"])
            if tag == "form":
                self.chart = attrs.get("id") == "guest-chart-form"
                if self.chart:
                    assert attrs.get("method") == "dialog"
                    assert "action" not in attrs
            if self.chart and tag in {"input", "select", "textarea"}:
                self.fields.append(attrs)
                assert "name" not in attrs
                assert tag != "textarea"
                assert attrs.get("type", "select") in {"date", "number", "checkbox", "select"}

        def handle_endtag(self, tag):
            if tag == "form":
                self.chart = False

    rendered = GuestHTML()
    rendered.feed(response.text)
    assert len(rendered.fields) == 26  # date, instrument, minutes + 23 fixed details
    assert any("guest-chart.js" in src for src in rendered.scripts)
    assert any("guest-plunge.js" in src for src in rendered.scripts)
    assert not any(part in src for src in rendered.scripts for part in (
        "/state.js", "/account.js", "arcade-economy", "p-chart", "analytics", "blue"))


def test_discard_removes_only_fixed_source_with_no_database_mutation(guest_db):
    client = TestClient(app)
    before = counts(guest_db)
    assert client.get("/prebeta/C001?entry=director1").status_code == 200
    assert session_data(client) == {"tester_registration_context": {"cohort_key": "C001", "source": "DIRECTOR1"}}
    response = client.post("/guest/discard", headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert "tester_registration_context" not in session_data(client)
    assert counts(guest_db) == before


def test_discard_preserves_existing_rate_limiting_metadata(guest_db):
    client = TestClient(app)
    assert client.post("/guest/secret-symbol", data={"passcode": "C001"}).status_code == 200
    initial = session_data(client)
    assert len(initial[BROWSER_KEY]) == 32
    before = counts(guest_db)
    assert client.post("/guest/discard", headers={"Origin": "http://testserver"}).status_code == 200
    assert session_data(client) == {BROWSER_KEY: initial[BROWSER_KEY]}
    assert counts(guest_db) == before


def test_native_discard_accepts_null_origin_only_with_same_origin_fetch_metadata(guest_db):
    client = TestClient(app)
    client.get("/prebeta/C001")
    headers = {"Origin": "null", "Sec-Fetch-Site": "same-origin",
               "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    before = counts(guest_db)
    for change in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                   {"Sec-Fetch-Mode": "cors"}, {"Sec-Fetch-Dest": "empty"},
                   {"Origin": "https://other.example"}):
        assert client.post("/guest/discard", headers={**headers, **change}).status_code == 403
        assert session_data(client)["tester_registration_context"] == {"cohort_key": "C001"}
    assert client.post("/guest/discard", headers=headers).status_code == 200
    assert "tester_registration_context" not in session_data(client)
    assert counts(guest_db) == before


@pytest.mark.parametrize("origin,body,status", [
    (None, b"", 403), ("https://other.example", b"", 403),
    ("http://testserver", b"minutes=25", 400),
])
def test_discard_rejects_cross_origin_missing_origin_and_product_body(guest_db, origin, body, status):
    client = TestClient(app)
    client.get("/prebeta/C001?entry=director1")
    claim = session_data(client)
    before = counts(guest_db)
    headers = {"Origin": origin or ""}
    assert client.post("/guest/discard", content=body, headers=headers).status_code == status
    assert session_data(client) == claim
    assert counts(guest_db) == before


@pytest.mark.parametrize("active", ["account", "csrf"])
def test_discard_cannot_clear_other_active_session_state(guest_db, active):
    client = TestClient(app)
    if active == "account":
        assert client.post("/account/login", data={"woodchuck_id": "WC-GUEST-A", "pin": "2468"}).status_code == 200
    else:
        assert client.get("/family/notice").status_code == 200
    initial = session_data(client)
    before = counts(guest_db)
    assert client.post("/guest/discard", headers={"Origin": "http://testserver"}).status_code == 409
    assert session_data(client) == initial
    assert counts(guest_db) == before


def test_successful_existing_login_discards_source_failure_keeps_it(guest_db):
    client = TestClient(app)
    client.get("/prebeta/C001?entry=director1")
    claim = session_data(client)["tester_registration_context"]
    assert client.post("/account/login", data={"woodchuck_id": "WC-GUEST-A", "pin": "0000"}).status_code == 401
    assert session_data(client)["tester_registration_context"] == claim
    assert client.post("/account/login", data={"woodchuck_id": "WC-GUEST-A", "pin": "2468"}).status_code == 200
    assert "tester_registration_context" not in session_data(client)
    with guest_db() as session:
        assert list(session.scalars(select(Enrollment))) == []


def test_new_registration_consumes_source_once_and_starts_without_guest_activity(guest_db):
    client = TestClient(app)
    client.get("/prebeta/C001?entry=director1")
    response = client.post("/account/create", data={
        "age_band": "adult", "display_name": "New account", "pin": "2468",
        "instrument": "Flute", "level": "Beginner", "goal": "Practice every day",
        "initial_state": "{}",
    })
    assert response.status_code == 200, response.text
    assert "tester_registration_context" not in session_data(client)
    with guest_db() as session:
        enrollments = list(session.scalars(select(Enrollment)))
        assert len(enrollments) == 1
        enrollment = enrollments[0]
        assert (enrollment.cohort_key, enrollment.source) == ("C001", "DIRECTOR1")
        state = session.get(WoodchuckState, enrollment.profile_id).state_json
        assert not state.get("practiceLog")
        assert "guest" not in json.dumps(state).lower()
        assert session.get(WoodchuckProfile, enrollment.profile_id).plunge_best_score == 0
    assert client.post("/account/create", data={"age_band": "adult"}).status_code == 400
    with guest_db() as session:
        assert len(list(session.scalars(select(Enrollment)))) == 1


@pytest.mark.parametrize("age", ["under13", "unknown", ""])
def test_guest_source_cannot_override_registration_age_gate(guest_db, age):
    client = TestClient(app)
    client.get("/prebeta/C001?entry=director1")
    before = counts(guest_db)
    response = client.post("/account/create", data={"age_band": age, "initial_state": "{}"})
    assert response.status_code == 403
    assert counts(guest_db) == before
    assert session_data(client)["tester_registration_context"] == {"cohort_key": "C001", "source": "DIRECTOR1"}


@pytest.mark.parametrize('path', ['/guest/discard', '/guest/secret-symbol', '/account/logout',
                                  '/account/login', '/account/create', '/account/age',
                                  '/account/delete', '/account/daily-secret', '/family/request',
                                  '/family/activate/fake'])
@pytest.mark.parametrize('headers', [
    {'Origin': ''},
    {'Origin': 'http://testserver:9000', 'Sec-Fetch-Site': 'same-site'},
    {'Origin': 'https://foreign.example', 'Sec-Fetch-Site': 'cross-site'},
    {'Origin': 'http://testserver', 'Sec-Fetch-Site': 'same-site'},
    {'Origin': 'null', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty'},
])
def test_session_mutations_reject_foreign_or_missing_origin_before_any_write(guest_db, path, headers):
    from sqlalchemy import func
    from app.db import Base
    client = TestClient(app)
    client.get('/prebeta/C001')
    cookie = client.cookies.get('session')
    def all_counts():
        with guest_db() as session:
            return {t.name: session.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables}
    before = all_counts()
    response = client.post(path, data={'passcode': 'C001'}, headers=headers, follow_redirects=False)
    assert response.status_code == 403
    assert 'set-cookie' not in response.headers
    assert client.cookies.get('session') == cookie
    assert all_counts() == before  # Includes C001 attempt/identity metadata.


@pytest.mark.parametrize('headers', [
    {'Origin': 'http://testserver'},
    {'Origin': 'http://testserver', 'Sec-Fetch-Site': 'same-origin'},
    {'Origin': 'null', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'},
])
def test_first_party_symbol_discard_logout_share_one_policy(guest_db, headers):
    client = TestClient(app)
    assert client.post('/guest/secret-symbol', data={'passcode': 'C001'}, headers=headers).status_code == 200
    assert session_data(client)['tester_registration_context']['source'] == 'DIRECTOR1'
    assert client.post('/guest/discard', headers=headers).status_code == 200
    assert 'tester_registration_context' not in session_data(client)
    assert client.post('/account/logout', headers=headers).json() == {'authenticated': False}
    assert session_data(client) == {}


def test_absent_origin_cannot_mutate_session_or_initialize_abuse_identity(guest_db):
    client = TestClient(app)
    client.headers.pop('origin')  # Exercise truly absent browser evidence.
    for path in ['/guest/secret-symbol', '/guest/discard', '/account/logout']:
        response = client.post(path, data={'passcode': 'C001'}, follow_redirects=False)
        assert response.status_code == 403
        assert response.json()['detail'] == 'A same-origin request is required.'
        assert 'set-cookie' not in response.headers
        assert not client.cookies


def test_public_c001_link_is_explicit_navigation_exception(guest_db):
    client = TestClient(app)
    bad = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'no-cors', 'Sec-Fetch-Dest': 'image'}
    assert client.get('/prebeta/C001?entry=director1', headers=bad).status_code == 403
    assert session_data(client) == {}
    good = {**bad, 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
    assert client.get('/prebeta/C001?entry=director1', headers=good).status_code == 200
    assert session_data(client)['tester_registration_context'] == {'cohort_key': 'C001', 'source': 'DIRECTOR1'}
