"""Authenticated student consent only; forms cannot address another student or Class."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import classroom, classroom_s2 as s2, classroom_progress_routes as routes
from app import classroom_progress_authorization as service, main
from app.age_models import AccountPrivacy
from app.classroom_models import ClassroomProgressAuthorization as Authorization, ClassroomTeachingAssignment
from test_classroom_s1 import actor, adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, student, join
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web, student_client, issue


@pytest.fixture
def progress_web(invitation_web, monkeypatch):
    monkeypatch.setenv("CLASSROOM_S3_ENABLED", "1")
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    monkeypatch.setattr(routes, "SessionLocal", invitation_web)
    with invitation_web() as session:
        join(session, visible="FIRST2026")
        session.commit()
    return invitation_web


def url(factory, action="authorize", program=1, class_id=None):
    return f"{routes.PATH}/{program}/classes/{class_id or factory.ids[0]}/{action}"


def form(client):
    page = client.get(routes.PATH)
    assert page.status_code == 200, page.text
    return {"csrf": page.context["csrf"], "notice_token": page.context["classes"][0]["notice_token"], "opt_in": "yes"}


def test_explicit_form_withdrawal_and_account_class_isolation(progress_web):
    client = student_client()
    page = client.get(routes.PATH)
    assert service.LABEL in page.text and "checked" not in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "no-referrer"
    assert "Jazz Ensemble" not in page.text
    with progress_web() as session:
        assert session.scalar(select(Authorization)) is None
    data = form(client)
    assert client.post(url(progress_web), data={k: v for k, v in data.items() if k != "opt_in"}).status_code == 400
    assert client.post(url(progress_web), data=data | {"opt_in": "no"}).status_code == 409
    assert client.post(url(progress_web), data=data | {"profile_id": "2"}).status_code == 400
    assert client.post(url(progress_web), data=data | {"csrf": "bad"}).status_code == 403
    assert client.post(url(progress_web), data=data, headers={"Origin": "https://outside.invalid"}).status_code == 403
    assert client.post(url(progress_web, program=2, class_id=progress_web.ids[1]), data=data).status_code == 409
    other = student_client(True)
    other_page = other.get(routes.PATH)
    assert not other_page.context["classes"]
    assert other.post(url(progress_web), data=data | {"csrf": other_page.context["csrf"]}).status_code == 409
    assert client.post(url(progress_web), data=data).status_code == 200
    assert "Authorization saved." in client.get(routes.PATH).text
    assert not other.get(routes.PATH).context["classes"]
    # Someone else's own-profile withdrawal is a harmless no-op.
    assert other.post(url(progress_web, "withdraw"), data={"csrf": other_page.context["csrf"]}).status_code == 200
    with progress_web() as session:
        assert session.scalar(select(Authorization)).withdrawn_at is None
        progress_web.clock[0] += timedelta(seconds=2)
        s2.leave_class(session, student=student(session), program_id=1, class_id=progress_web.ids[0])
        session.commit()
    page = client.get(routes.PATH)
    assert "inactive" in page.text and "Withdraw authorization" in page.text
    assert "Save authorization" not in page.text
    assert client.post(url(progress_web, "withdraw"), data={"csrf": data["csrf"]}).status_code == 200
    with progress_web() as session:
        assert session.scalar(select(Authorization)).withdrawn_by_profile_id == 1


def test_invitation_without_enrollment_cannot_authorize(invitation_web, monkeypatch):
    monkeypatch.setenv("CLASSROOM_S3_ENABLED", "1")
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    monkeypatch.setattr(routes, "SessionLocal", invitation_web)
    client, _, ident, csrf = issue(invitation_web)
    assert client.post(f"/classroom/student-invitations/{ident}/accept", data={"csrf": csrf}).status_code == 200
    assert not client.get(routes.PATH).context["classes"]
    with invitation_web() as session:
        assignment = session.scalar(select(ClassroomTeachingAssignment.id).where(
            ClassroomTeachingAssignment.class_id == invitation_web.ids[0]))
        with pytest.raises(classroom.ClassroomDenied):
            service.accept(session, student=student(session), program_id=1, class_id=invitation_web.ids[0],
                opted_in=True, notice_version=service.NOTICE_VERSION, assignment_ids=(assignment,),
                previous=(None, None), source_revision=None)
        assert session.scalar(select(Authorization)) is None


def test_stale_form_cannot_authorize_replacement_director(progress_web):
    client = student_client(); data = form(client)
    with progress_web() as session:
        assignment = session.scalar(select(ClassroomTeachingAssignment).where(
            ClassroomTeachingAssignment.class_id == progress_web.ids[0]))
        s2.end_teaching(session, actor=actor(session), program_id=1, assignment_id=assignment.id)
        s2.assign_teacher(session, actor=actor(session), program_id=1, class_id=progress_web.ids[0], verifier_id=3)
        session.commit()
    assert client.post(url(progress_web), data=data).status_code == 409
    assert "Adult 3" in client.get(routes.PATH).text
    assert client.post(url(progress_web), data=form(client)).status_code == 200


def test_stale_form_cannot_reverse_withdrawal(progress_web):
    client = student_client(); original = form(client)
    assert client.post(url(progress_web), data=original).status_code == 200
    assert client.post(url(progress_web, "withdraw"), data={"csrf": original["csrf"]}).status_code == 200
    assert client.post(url(progress_web), data=original).status_code == 409
    with progress_web() as session:
        assert session.scalar(select(Authorization)).withdrawn_at is not None
    assert client.post(url(progress_web), data=form(client)).status_code == 200


@pytest.mark.parametrize("flag", ["CLASSROOM_S1_ENABLED", "CLASSROOM_S2_ENABLED", "CLASSROOM_S3_ENABLED",
                                 "CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED"])
def test_default_off_gates(progress_web, monkeypatch, flag):
    client = student_client(); data = form(client)
    monkeypatch.delenv(flag)
    assert not service.enabled()
    assert client.get(routes.PATH).status_code == 404
    assert client.post(url(progress_web), data=data).status_code == 404
    assert routes.PATH not in client.get("/home").text


@pytest.mark.parametrize("age", ["under13", "unknown"])
def test_restricted_accounts_cannot_opt_in(progress_web, age):
    client = student_client(); data = form(client)
    with progress_web() as session:
        session.get(AccountPrivacy, 1).age_band = age
        session.commit()
    assert client.post(url(progress_web), data=data).status_code == 409
    assert "Save authorization" not in client.get(routes.PATH).text


def test_guest_and_mismatched_account_are_denied(progress_web):
    assert TestClient(main.app).get(routes.PATH).status_code == 401
    client = student_client()
    assert client.get(routes.PATH, headers={"X-Woodshed-Account": "WC-SECOND"}).status_code == 401
