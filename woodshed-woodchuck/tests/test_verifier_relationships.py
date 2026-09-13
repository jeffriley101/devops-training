"""Verifier and Band Director permissions are per accepted student relationship."""
from datetime import date
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main, practice_chart_routes
from app.models import StudentVerifierConnection, WoodchuckProfile, PracticeChartVerification
from app.email_service import DeliveryResult, EmailService
from app.verifiers import VERIFIER_ROLES, create_trusted_verifier_invitation, validate_role
from test_band_director_roster import roster_db, add_student, add_chart, signed_client


def test_verifier_management_uses_vertical_sections_and_scoped_feedback():
    template = Path("templates/trusted_verifiers.html").read_text()
    script = Path("static/js/trusted-verifiers.js").read_text()

    assert 'class="card trusted-verifier-relationships"' in template
    assert template.index('id="verifiers-heading"') < template.index('id="band-director-heading"')
    assert template.count('class="error-text relationship-form-error"') == 2
    assert 'form.querySelector(".relationship-form-error")' in script
    assert 'form.appendChild(successPanel)' in script
    assert 'reportError(button, error.message || "The change could not be completed.")' in script
    assert 'trusted-verifier-invite-error' not in template


def test_three_verifiers_and_one_band_director_have_independent_limits(roster_db):
    student = add_student(roster_db, "Capacity", connected=False)
    with roster_db() as session:
        from app.models import TrustedVerifier
        for verifier_id in (3, 4, 5, 6, 7):
            session.add(TrustedVerifier(id=verifier_id, email=f"adult{verifier_id}@example.com",
                                        display_name=f"Adult {verifier_id}", pin_hash="hash"))
        session.commit()
        profile = session.get(WoodchuckProfile, student)
        for verifier_id in (1, 2, 3):
            create_trusted_verifier_invitation(session, profile=profile,
            email=f"adult{verifier_id}@example.com", role="verifier")
        director = create_trusted_verifier_invitation(session, profile=profile,
            email="adult6@example.com", role="band_director")
        assert director.invitation.role == "band_director"
        with pytest.raises(ValueError, match="three trusted verifiers"):
            create_trusted_verifier_invitation(session, profile=profile,
                email="adult7@example.com", role="verifier")
        with pytest.raises(ValueError, match="only one Band Director"):
            create_trusted_verifier_invitation(session, profile=profile,
                email="adult7@example.com", role="band_director")

@pytest.mark.parametrize("role", ["parent", "mentor", "guardian", "private_teacher", "coach", "other_trusted_adult"])
def test_legacy_roles_are_not_new_choices(role):
    assert VERIFIER_ROLES == {"verifier", "band_director"}
    with pytest.raises(ValueError):
        validate_role(role)

@pytest.mark.parametrize("role", ["verifier", "band_director"])
def test_invite_accept_login_and_role_specific_dashboard(roster_db, role):
    student = add_student(roster_db, "Invitee", connected=False)
    with roster_db() as session:
        invitation = create_trusted_verifier_invitation(session,
            profile=session.get(WoodchuckProfile, student), email="adult@example.com", role=role)
        token = invitation.token
    client = TestClient(main.app)
    response = client.post(f"/trusted-verifiers/invitations/{token}/accept",
                           data={"display_name": "Adult", "pin": "1357"})
    assert response.status_code == 200
    assert response.json()["connection"]["role"] == role
    client.post("/trusted-verifiers/logout")
    assert client.post("/trusted-verifiers/login", data={"email": "adult@example.com", "pin": "1357"}).status_code == 200
    page = client.get("/trusted-verifiers/dashboard")
    assert (page.context["student"] is not None) == (role == "verifier")
    director = client.get("/band-director/dashboard")
    assert ("Invitee" in director.text) == (role == "band_director")
    if role == "verifier":
        assert {"rating", "trend", "lifetime", "season", "achievements"} <= page.context["student"].keys()

@pytest.mark.parametrize("role", ["band_director", "parent", "mentor", "unknown"])
def test_other_roles_cannot_select_verifier_snapshot(roster_db, role):
    student = add_student(roster_db, "Excluded", role=role)
    with roster_db() as session:
        connection = session.scalar(select(StudentVerifierConnection.id))
    client = signed_client()
    assert client.get("/trusted-verifiers/dashboard").context["student"] is None
    for endpoint in ("dashboard", "practice-charts"):
        assert client.get(f"/trusted-verifiers/{endpoint}?connection_id={connection}&role=verifier").status_code == 404
    assert client.get("/trusted-verifiers/me").json()["student_connections"] == []

@pytest.mark.parametrize("recipient_role", [None, "verifier", "band_director"])
def test_chart_recipient_enforced_on_post_and_notification(roster_db, monkeypatch, recipient_role):
    student = add_student(roster_db, "Student", role=recipient_role or "verifier")
    monkeypatch.setattr(practice_chart_routes, "SessionLocal", roster_db)
    monkeypatch.setattr(practice_chart_routes, "current_profile",
                        lambda request, session: session.get(WoodchuckProfile, student))
    deliveries = []
    def send(self, **kwargs):
        deliveries.append(kwargs)
        return DeliveryResult(True, "sent")
    monkeypatch.setattr(EmailService, "send_practice_chart", send)
    client = TestClient(main.app)
    response = client.post("/practice-charts", json={"minutes": 10, "practice_date": "2026-09-09",
                           "verifier_id": 1 if recipient_role else None})
    assert response.status_code == (400 if recipient_role == "band_director" else 201)
    with roster_db() as session:
        reviews = session.scalars(select(PracticeChartVerification)).all()
        assert len(reviews) == (1 if recipient_role == "verifier" else 0)
    assert len(deliveries) == (1 if recipient_role == "verifier" else 0)
    if recipient_role != "band_director":
        assert (response.json()["chart"]["verification"] is not None) == (recipient_role == "verifier")

@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_verifier_review_and_disconnect_revocation(roster_db, decision):
    student = add_student(roster_db, "Review", role="verifier")
    add_chart(roster_db, student, date(2026, 9, 9), 10, status="pending")
    client = signed_client()
    review = client.get("/trusted-verifiers/practice-charts").json()["pending_charts"][0]
    endpoint = f'/trusted-verifiers/practice-charts/{review["verification_id"]}/respond'
    assert signed_client("other@example.com").post(endpoint, json={"decision": decision}).status_code == 404
    assert client.post(endpoint, json={"decision": decision}).status_code == 200
    assert client.post(endpoint, json={"decision": decision}).status_code == 400
    with roster_db() as session:
        session.scalar(select(StudentVerifierConnection)).status = "disconnected"
        session.commit()
    assert client.get("/trusted-verifiers/dashboard").context["student"] is None
