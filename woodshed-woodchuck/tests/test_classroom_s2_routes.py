"""Real signed-session, origin, CSRF and authority boundaries for minimal S2 UI."""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import account_routes, classroom_s2_routes as routes, main, session_revocations, verifier_routes
from app.age_models import AccountPrivacy
from app.classroom_models import ClassroomEntitlement, ClassroomStudentMembership
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import NOW, ready, s2_db


@pytest.fixture
def web(s2_db, monkeypatch):
    for module in (routes, account_routes, session_revocations, verifier_routes, main):
        monkeypatch.setattr(module, "SessionLocal", s2_db)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    return s2_db


def adult_client():
    client = TestClient(main.app)
    assert client.post("/trusted-verifiers/login", data={"email": "adult1@example.test", "pin": "2468"}).status_code == 200
    return client


def student_client():
    client = TestClient(main.app)
    assert client.post("/account/login", data={"woodchuck_id": "WC-SYNTHETIC", "pin": "2468"}).status_code == 200
    return client


def test_trial_form_requires_origin_csrf_credentials_and_refuses_extra_fields(web):
    client = adult_client()
    page = client.get("/classroom/manage")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    data = {"csrf": page.context["csrf"], "program_id": "1", "email": "adult1@example.test", "pin": "2468"}
    assert client.post("/classroom/manage/trial", data={**data, "csrf": "bad"}).status_code == 403
    assert client.post("/classroom/manage/trial", data=data, headers={"Origin": "https://outside.example"}).status_code == 403
    assert client.post("/classroom/manage/trial", data=data, headers={"Origin": ""}).status_code == 403
    assert client.post("/classroom/manage/trial", data={**data, "actor_id": "1"}).status_code == 400
    assert client.post("/classroom/manage/trial", data={**data, "pin": "9999"}).status_code == 409
    assert client.post("/classroom/manage/trial", data={**data, "email": "invalid"}).status_code == 400
    assert client.post("/classroom/manage/trial", data={**data, "program_id": "1" * 5000}).status_code == 400
    response = client.post("/classroom/manage/trial", data=data)
    assert response.status_code == 200 and "entitlement_id" in response.context["result"]
    assert client.post("/classroom/manage/trial", data=data).status_code == 409
    with web() as session:
        assert len(list(session.scalars(select(ClassroomEntitlement)))) == 1


def test_student_auto_join_is_private_and_requires_own_session(web):
    with web() as session:
        row, _, visible = ready(session)
        class_id = row.id
        session.commit()
    client = student_client()
    page = client.get("/classroom/entry")
    assert page.status_code == 200
    data = {"csrf": page.context["csrf"], "program_id": "1", "code": visible}
    response = client.post("/classroom/entry/join", data=data)
    assert response.status_code == 200
    assert response.context["result"]["class_id"] == class_id
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert client.post("/classroom/entry/join", data=data, headers={"x-woodshed-account": "WC-OTHER"}).status_code == 409
    assert client.post("/classroom/entry/join", data={**data, "profile_id": "2"}).status_code == 400
    assert TestClient(main.app).get("/classroom/entry").status_code == 401
    with web() as session:
        assert len(list(session.scalars(select(ClassroomStudentMembership)))) == 1


@pytest.mark.parametrize("action", ["prepare", "join", "finish"])
def test_invalid_entry_response_does_not_disclose_program_state(web, action):
    with web() as session:
        ready(session)
        session.commit()
    client = student_client()
    page = client.get("/classroom/entry")
    entry = {"intent": "invalid-intent"} if action == "finish" else {"code": "INVALID9"}
    for program_id in (1, 2, 999):  # entitled, no entitlement, nonexistent
        response = client.post(f"/classroom/entry/{action}", data={
            "csrf": page.context["csrf"], "program_id": str(program_id), **entry})
        assert response.status_code == 409
        assert response.json() == {"detail": routes.service.INVALID_CODE}
    with web() as session:
        assert list(session.scalars(select(ClassroomStudentMembership))) == []


def test_pending_registered_account_can_prepare_but_not_join(web):
    with web() as session:
        ready(session)
        session.get(AccountPrivacy, 1).age_band = "unknown"
        session.commit()
    client = student_client()
    page = client.get("/classroom/entry")
    data = {"csrf": page.context["csrf"], "program_id": "1", "code": "BAND2026"}
    result = client.post("/classroom/entry/prepare", data=data)
    assert result.status_code == 200 and result.context["result"]["intent"]
    refusal = client.post("/classroom/entry/join", data=data)
    assert refusal.status_code == 409
    assert refusal.json() == {"detail": "Current account and parent authorization are required."}
    with web() as session:
        assert list(session.scalars(select(ClassroomStudentMembership))) == []


def test_internal_entitlement_requires_site_admin_not_adult(web):
    adult = adult_client()
    assert adult.get("/admin/classroom/entitlement").status_code == 403
    client = TestClient(main.app)
    page = client.get("/admin/login")
    assert client.post("/admin/login", data={"csrf": page.context["csrf"], "token": "synthetic-classroom-s2-admin"}, follow_redirects=False).status_code == 303
    page = client.get("/admin/classroom/entitlement")
    data = {"csrf": page.context["csrf"], "program_id": "1", "starts_at": NOW.isoformat(),
            "ends_at": (NOW + timedelta(days=60)).isoformat(), "class_limit": "4", "teacher_limit": "2",
            "provenance": "CASE_SYNTHETIC", "status": "active"}
    assert client.post("/admin/classroom/entitlement", data=data).status_code == 200
    with web() as session:
        row = session.scalar(select(ClassroomEntitlement))
        assert row.source == "institutional" and row.approved_by_admin and row.approved_by_verifier_id is None


def test_disabled_routes_and_rate_limit_before_mutation(web, monkeypatch):
    client = adult_client()
    page = client.get("/classroom/manage")
    def deny(*args):
        raise HTTPException(429, "Synthetic limit")
    monkeypatch.setattr(routes, "enforce_login_limit", deny)
    assert client.post("/classroom/manage/trial", data={"csrf": page.context["csrf"], "program_id": "1", "email": "adult1@example.test", "pin": "2468"}).status_code == 429
    with web() as session:
        assert list(session.scalars(select(ClassroomEntitlement))) == []
    monkeypatch.delenv("CLASSROOM_S2_ENABLED")
    assert client.get("/classroom/manage").status_code == 404
    assert client.get("/classroom/entry").status_code == 404
