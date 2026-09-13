from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main, trusted_verifier_dashboard
from app.models import PracticeChartVerification, StudentVerifierConnection, WoodchuckProfile
from app.practice_charts import create_practice_chart_verification_request
from app.verifiers import VERIFIER_ROLES, VERIFIER_ROLE_LABELS, create_trusted_verifier_invitation, validate_role
from test_band_director_roster import roster_db, add_student, add_chart, signed_client

LEGACY = ("guardian", "private_teacher", "coach", "other_trusted_adult")
TODAY = date(2026, 9, 9)


def test_canonical_roles():
    assert VERIFIER_ROLES == {"parent", "band_director", "mentor"}
    assert TestClient(main.app).get("/trusted-verifiers/roles").json()["roles"] == sorted(VERIFIER_ROLES)
    for role in VERIFIER_ROLES:
        assert validate_role(role) == role
    source = main.templates.env.loader.get_source(main.templates.env, "trusted_verifiers.html")[0]
    selector = source[source.index("<select"):source.index("</select>") + len("</select>")]
    rendered = main.templates.env.from_string(selector).render(verifier_role_labels=VERIFIER_ROLE_LABELS)
    for role, label in VERIFIER_ROLE_LABELS.items():
        assert f'<option value="{role}">{label}</option>' in rendered
    for role in LEGACY:
        assert role not in rendered


@pytest.mark.parametrize("role", LEGACY)
def test_legacy_cannot_create_invitation(roster_db, role):
    student = add_student(roster_db, "Invitee", connected=False)
    with roster_db() as session:
        with pytest.raises(ValueError, match="valid trusted-verifier role"):
            create_trusted_verifier_invitation(session, profile=session.get(WoodchuckProfile, student),
                                               email="mentor@example.com", role=role)


def test_mentor_invitation_acceptance_login(roster_db):
    student = add_student(roster_db, "Invitee", connected=False)
    with roster_db() as session:
        invitation = create_trusted_verifier_invitation(
            session, profile=session.get(WoodchuckProfile, student),
            email="mentor@example.com", role="mentor")
        token = invitation.token
    client = TestClient(main.app)
    response = client.post(f"/trusted-verifiers/invitations/{token}/accept",
                           data={"display_name": "Mentor", "pin": "1357"})
    assert response.status_code == 200
    assert response.json()["connection"]["role"] == "mentor"
    client.post("/trusted-verifiers/logout")
    assert client.post("/trusted-verifiers/login", data={
        "email": "mentor@example.com", "pin": "1357"}).status_code == 200
    assert client.get("/trusted-verifiers/dashboard").context["student"]["display_name"] == "Invitee"
    assert "goal" not in client.get("/trusted-verifiers/me").json()["student_connections"][0]["student"]


@pytest.mark.parametrize("role", ("mentor", *LEGACY, "unknown"))
def test_limited_payload_never_builds_parent_analytics(roster_db, monkeypatch, role):
    student = add_student(roster_db, "Mentored", role=role)
    for day, minutes in [(7, 10), (8, 20), (9, 0), (1, 9999)]:
        add_chart(roster_db, student, date(2026, 9, day), minutes)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 9, 18, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(trusted_verifier_dashboard, "datetime", FixedDatetime)
    def forbidden(*args, **kwargs):
        raise AssertionError("Mentor must not build full analytics")
    for name in ("student_practice_snapshot", "current_roster_period", "recent_achievements"):
        monkeypatch.setattr(trusted_verifier_dashboard, name, forbidden)
    page = signed_client().get("/trusted-verifiers/dashboard?role=parent&week=2026-09-01")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    payload = page.context["student"]
    assert set(payload) == {"connection_id", "role", "display_name", "instrument", "level",
                            "weekly", "practice_streak"}
    assert payload["weekly"] == {"total": 30, "days": 2}
    assert payload["practice_streak"] == 2
    for label in ("Practice Rating", "verifier-trend", "Current season", "Lifetime", "All Time",
                  "Recent achievements", "9999", "P-Charts this week", "Practice quality"):
        assert label not in page.text
    for label in ("Mentored", "Trumpet", "Beginner", "Practice days this week", "Practice streak"):
        assert label in page.text
    assert 'href="/band-director/dashboard"' not in page.text
    assert signed_client().get("/band-director/dashboard.csv").text.count("\n") == 1


def test_mixed_role_switching_and_revocation(roster_db):
    ids = [add_student(roster_db, name, role=role, verifier_id=verifier)
           for name, role, verifier in [("Parent child", "parent", 1), ("Mentor child", "mentor", 1),
                                        ("Other mentor child", "mentor", 1), ("Private", "parent", 2)]]
    with roster_db() as session:
        connections = [session.scalar(select(StudentVerifierConnection.id).where(
            StudentVerifierConnection.profile_id == student)) for student in ids]
    client = signed_client()
    for index in (0, 1, 2, 0, 1):
        page = client.get(f"/trusted-verifiers/dashboard?connection_id={connections[index]}")
        assert page.status_code == 200
        assert ("rating" in page.context["student"]) == (index == 0)
        assert 'id="verifier-student-selector"' in page.text
        assert "Private" not in page.text
    assert client.get(f"/trusted-verifiers/dashboard?connection_id={connections[3]}").status_code == 404
    with roster_db() as session:
        session.get(StudentVerifierConnection, connections[1]).status = "disconnected"
        session.get(WoodchuckProfile, ids[2]).status = "deleted"
        session.commit()
    for connection_id in connections[1:3]:
        for endpoint in ("dashboard", "practice-charts"):
            assert client.get(f"/trusted-verifiers/{endpoint}?connection_id={connection_id}").status_code == 404


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_mentor_review_uses_existing_workflow(roster_db, decision):
    student = add_student(roster_db, "Review", role="mentor")
    unrelated = add_student(roster_db, "Unrelated", verifier_id=2)
    with roster_db() as session:
        created = create_practice_chart_verification_request(
            session, profile=session.get(WoodchuckProfile, student), verifier_id=1,
            practice_date=date.today(), minutes=15)
        review_id = created.verification.id
        with pytest.raises(ValueError):
            create_practice_chart_verification_request(
                session, profile=session.get(WoodchuckProfile, unrelated), verifier_id=1,
                practice_date=date.today(), minutes=15)
    client = signed_client()
    assert client.get("/trusted-verifiers/practice-charts").json()["pending_charts"][0]["verification_id"] == review_id
    endpoint = f"/trusted-verifiers/practice-charts/{review_id}/respond"
    assert signed_client("other@example.com").post(endpoint, json={"decision": decision}).status_code == 404
    assert client.post(endpoint, json={"decision": decision}).status_code == 200
    assert client.post(endpoint, json={"decision": decision}).status_code == 400


@pytest.mark.parametrize("status", ["pending", "rejected", "disconnected"])
def test_mentor_review_requires_still_accepted_connection(roster_db, status):
    student = add_student(roster_db, "Review", role="mentor")
    add_chart(roster_db, student, TODAY, 10, status="pending")
    with roster_db() as session:
        review_id = session.scalar(select(PracticeChartVerification.id))
        session.scalar(select(StudentVerifierConnection)).status = status
        session.commit()
    client = signed_client()
    assert client.get("/trusted-verifiers/practice-charts").json()["pending_charts"] == []
    assert client.post(f"/trusted-verifiers/practice-charts/{review_id}/respond",
                       json={"decision": "approved"}).status_code == 400
    with roster_db() as session:
        assert session.get(PracticeChartVerification, review_id).status == "pending"
