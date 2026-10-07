"""Live Program invitation boundary with synthetic adults and captured delivery."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app import (
    classroom, classroom_invitation, classroom_invitation_routes, main,
    session_revocations, verifier_routes,
)
from app.age_privacy import declare_age
from app.classroom_models import (
    ClassroomAuditEvent, ClassroomClass, ClassroomProgram, ClassroomRoleGrant,
    ClassroomStudentMembership, ClassroomTeachingAssignment,
)
from app.db import Base
from app.email_service import DeliveryResult, EmailService, SMTPConfig
from app.models import (
    BillingAccount, Membership, MembershipSeat, Organization,
    StudentVerifierConnection, TrustedVerifier, WoodchuckProfile,
)
from app.security import hash_pin, verify_pin
from app.verifiers import create_trusted_verifier_invitation


@pytest.fixture
def onboarding_db(tmp_path, monkeypatch):
    """Every HTTP test gets an isolated file and leaves Classroom management off."""
    engine = create_engine(f"sqlite:///{tmp_path / 'classroom-onboarding.db'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(classroom_invitation_routes, "SessionLocal", factory)
    monkeypatch.setattr(session_revocations, "SessionLocal", factory)
    monkeypatch.setattr(verifier_routes, "SessionLocal", factory)
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "synthetic-site-admin")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.delenv("CLASSROOM_S1_ENABLED", raising=False)
    monkeypatch.delenv("CLASSROOM_S1_LOCAL_PROVISIONING", raising=False)
    monkeypatch.delenv("CLASSROOM_S1_PROVISIONER_IDS", raising=False)
    with factory() as session:
        session.add_all([
            Organization(id=11, name="Synthetic Music Program", organization_type="music"),
            Organization(id=12, name="Other Synthetic Program", organization_type="music"),
            TrustedVerifier(id=21, email="existing@example.test", display_name="Original Adult",
                            pin_hash=hash_pin("2468")),
            WoodchuckProfile(id=31, woodchuck_id="WC-LIVE-TEST", display_name="Synthetic Student",
                             pin_hash=hash_pin("1357"), instrument="Flute", level="Beginner",
                             goal="Practice"),
        ])
        session.flush()
        declare_age(session, 31, "adult")
        session.add(StudentVerifierConnection(profile_id=31, verifier_id=21,
                                               role="band_director", status="accepted"))
        session.commit()
    yield factory
    engine.dispose()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def authority_counts(factory):
    with factory() as session:
        return {model.__tablename__: count(session, model) for model in (
            TrustedVerifier, ClassroomProgram, ClassroomRoleGrant, ClassroomAuditEvent,
            ClassroomClass, ClassroomTeachingAssignment, ClassroomStudentMembership,
            StudentVerifierConnection, BillingAccount, Membership, MembershipSeat,
        )}


def site_admin_client():
    client = TestClient(main.app)
    page = client.get("/admin/login")
    assert page.status_code == 200
    response = client.post("/admin/login", data={
        "csrf": page.context["csrf"], "token": "synthetic-site-admin",
    }, follow_redirects=False)
    assert response.status_code == 303
    return client


def admin_csrf(client):
    page = client.get("/admin/classroom/invitations")
    assert page.status_code == 200
    assert page.headers.get("cache-control") == "no-store"
    return page.context["csrf"]


def issue(client, csrf, *, organization_id="11", email="  New.Adult@Example.Test  ",
          headers=None):
    return client.post("/admin/classroom/invitations", data={
        "csrf": csrf, "organization_id": organization_id, "email": email,
    }, headers=headers, follow_redirects=False)


def capture_delivery(monkeypatch, *, sent=True, code="sent"):
    delivered = []

    def send(self, *, recipient, acceptance_url):
        delivered.append((recipient, acceptance_url))
        return DeliveryResult(sent, code)

    monkeypatch.setattr(EmailService, "send_program_invitation", send)
    return delivered


def invitation_path(delivered):
    assert len(delivered) == 1
    recipient, url = delivered[0]
    parsed = urlsplit(url)
    assert parsed.scheme == "http" and parsed.netloc == "testserver"
    assert parsed.path.startswith("/classroom/invitations/")
    assert parsed.query == "" and parsed.fragment == ""
    return recipient, parsed.path


def accept_csrf(client, path):
    page = client.get(path)
    assert page.status_code == 200
    assert page.headers.get("cache-control") == "no-store"
    assert page.headers.get("referrer-policy") == "no-referrer"
    return page.context["csrf"]


def accept(client, path, csrf, pin="1357", display_name="Founding Adult", **other):
    return client.post(path, data={
        "csrf": csrf, "pin": pin, "display_name": display_name, **other,
    }, follow_redirects=False)


def altered_token(token, **changes):
    """Construct signed malformed claims to test the parser's semantic checks."""
    body, _ = token.rsplit(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload.update(changes)
    changed_body = classroom_invitation._encode(payload)
    signature = hmac.new(classroom_invitation._key(), changed_body.encode("ascii"),
                         hashlib.sha256).hexdigest()
    return f"{changed_body}.{signature}"


def test_purpose_version_expiry_and_signed_claims_are_validated(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "synthetic-onboarding-signing-secret")
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    token = classroom_invitation.create_program_invitation(
        11, "  Director@Example.Test ", now=now)
    claims = classroom_invitation.parse_program_invitation(token, now=now + timedelta(seconds=1))
    assert (claims.organization_id, claims.email) == (11, "director@example.test")
    assert claims.expires_at - claims.issued_at == timedelta(days=7)
    with pytest.raises(ValueError):
        classroom_invitation.parse_program_invitation(token, now=now + timedelta(days=7))
    for changed in ({"purpose": "student-director-invitation"}, {"v": 2},
                    {"organization_id": "11"}, {"email": "DIRECTOR@example.test"}):
        with pytest.raises(ValueError):
            classroom_invitation.parse_program_invitation(
                altered_token(token, **changed), now=now + timedelta(seconds=1))
    body, signature = token.rsplit(".", 1)
    for changed in ({"organization_id": 12}, {"email": "other@example.test"}):
        malformed_body = classroom_invitation._encode({
            **json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))),
            **changed,
        })
        with pytest.raises(ValueError):
            classroom_invitation.parse_program_invitation(
                f"{malformed_body}.{signature}", now=now + timedelta(seconds=1))


def test_program_invitation_email_has_private_link_and_no_student_claim(monkeypatch):
    config = SMTPConfig("smtp.example.test", 587, "synthetic-user", "synthetic-password",
                        "woodshed@example.test", "Woodshed", True)
    service = EmailService(config)
    messages = []
    monkeypatch.setattr(service, "send", lambda message: (
        messages.append(message) or DeliveryResult(True, "sent")))
    url = 'https://woodshed.example.test/classroom/invitations/signed?source=mail&next="quoted"'
    result = service.send_program_invitation(
        recipient="director@example.test", acceptance_url=url)
    assert result == DeliveryResult(True, "sent")
    assert len(messages) == 1
    message = messages[0]
    assert message["To"] == "director@example.test"
    assert "Music Program invitation" in str(message["Subject"])
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert plain.count(url) == 1
    assert html.count("href=") == 1
    assert 'source=mail&amp;next=&quot;quoted&quot;' in html
    assert url not in html
    assert "existing PIN" in plain and "existing PIN" in html
    assert "student" not in (str(message["Subject"]) + plain + html).lower()


def test_site_admin_issuance_requires_auth_csrf_and_origin(onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    visitor = TestClient(main.app)
    assert visitor.get("/admin/classroom/invitations").status_code == 403
    assert issue(visitor, "missing").status_code == 403

    admin = site_admin_client()
    csrf = admin_csrf(admin)
    assert issue(admin, "wrong").status_code == 403
    assert issue(admin, csrf, email="director@example.test").status_code == 303
    assert len(delivered) == 1
    assert issue(admin, csrf, email="director@example.test", headers={
        "Origin": "https://foreign.example.test",
    }).status_code == 403
    assert issue(admin, csrf, email="director@example.test", headers={
        "Origin": "",
    }).status_code == 403
    assert len(delivered) == 1
    counts = authority_counts(onboarding_db)
    assert counts["trusted_verifiers"] == 1
    assert counts["classroom_programs"] == counts["classroom_role_grants"] == 0
    assert counts["student_verifier_connections"] == 1


def test_issuance_rejects_unknown_provisioned_and_invalid_email(onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    admin = site_admin_client()
    csrf = admin_csrf(admin)
    assert issue(admin, csrf, organization_id="999").status_code in {400, 404, 409}
    assert issue(admin, csrf, email="invalid-address").status_code in {400, 422}
    with onboarding_db() as session:
        session.add(ClassroomProgram(organization_id=12, owner_verifier_id=21))
        session.commit()
    assert issue(admin, csrf, organization_id="12").status_code in {400, 409}
    assert delivered == []
    counts = authority_counts(onboarding_db)
    assert counts["trusted_verifiers"] == 1
    assert counts["classroom_programs"] == 1
    assert counts["classroom_role_grants"] == 0


def test_issuance_normalizes_email_and_creates_no_authority(onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    before = authority_counts(onboarding_db)
    admin = site_admin_client()
    response = issue(admin, admin_csrf(admin))
    assert response.status_code == 303
    recipient, path = invitation_path(delivered)
    assert recipient == "new.adult@example.test"
    assert authority_counts(onboarding_db) == before
    assert admin.get("/trusted-verifiers/me").json()["authenticated"] is False
    visitor = TestClient(main.app)
    accept_csrf(visitor, path)
    assert authority_counts(onboarding_db) == before


def test_delivery_failure_creates_no_authority_and_allows_retry(onboarding_db, monkeypatch):
    failed = capture_delivery(monkeypatch, sent=False, code="not_configured")
    before = authority_counts(onboarding_db)
    admin = site_admin_client()
    csrf = admin_csrf(admin)
    response = issue(admin, csrf)
    assert response.status_code in {200, 303, 503}
    assert len(failed) == 1
    assert authority_counts(onboarding_db) == before
    delivered = capture_delivery(monkeypatch)
    assert issue(admin, csrf).status_code == 303
    assert invitation_path(delivered)[0] == "new.adult@example.test"
    assert authority_counts(onboarding_db) == before


def test_invitation_token_rejects_tampering_and_cannot_select_another_target(onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    admin = site_admin_client()
    assert issue(admin, admin_csrf(admin)).status_code == 303
    _, path = invitation_path(delivered)
    visitor = TestClient(main.app)
    csrf = accept_csrf(visitor, path)
    tampered = path[:-1] + ("x" if path[-1] != "x" else "y")
    assert visitor.get(tampered).status_code in {400, 403, 404, 410}
    assert accept(visitor, tampered, csrf).status_code in {400, 403, 404, 410}
    before = authority_counts(onboarding_db)
    result = accept(visitor, path, csrf, organization_id="12", email="other@example.test")
    if result.status_code == 303:
        with onboarding_db() as session:
            program = session.get(ClassroomProgram, 11)
            adult = session.scalar(select(TrustedVerifier).where(
                TrustedVerifier.email == "new.adult@example.test"))
            assert program.owner_verifier_id == adult.id
            assert session.get(ClassroomProgram, 12) is None
            assert session.scalar(select(TrustedVerifier.id).where(
                TrustedVerifier.email == "other@example.test")) is None
    else:
        assert result.status_code in {400, 403, 409, 422}
        assert authority_counts(onboarding_db) == before


def test_acceptance_requires_csrf_and_same_origin(onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    admin = site_admin_client()
    assert issue(admin, admin_csrf(admin)).status_code == 303
    _, path = invitation_path(delivered)
    visitor = TestClient(main.app)
    csrf = accept_csrf(visitor, path)
    before = authority_counts(onboarding_db)
    assert accept(visitor, path, "wrong").status_code == 403
    assert visitor.post(path, data={
        "csrf": csrf, "pin": "1357", "display_name": "Founding Adult",
    }, headers={"Origin": "https://foreign.example.test"}, follow_redirects=False).status_code == 403
    assert visitor.post(path, data={
        "csrf": csrf, "pin": "1357", "display_name": "Founding Adult",
    }, headers={"Origin": ""}, follow_redirects=False).status_code == 403
    assert authority_counts(onboarding_db) == before


def test_acceptance_limits_normalized_invited_email_before_credential_proof(
        onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    admin = site_admin_client()
    assert issue(admin, admin_csrf(admin), email="  EXISTING@Example.Test ").status_code == 303
    _, path = invitation_path(delivered)
    visitor = TestClient(main.app)
    csrf = accept_csrf(visitor, path)
    calls = []

    def limited(request, kind, identifier):
        calls.append((kind, identifier))
        raise HTTPException(429, "Synthetic credential throttle")

    def credential_proof(*args, **kwargs):
        pytest.fail("Credential proof ran before the login limit")

    monkeypatch.setattr(classroom_invitation_routes, "enforce_login_limit", limited)
    monkeypatch.setattr(classroom_invitation_routes, "accept_program_invitation", credential_proof)
    response = accept(visitor, path, csrf, pin="wrong", display_name="Replacement")
    assert response.status_code == 429
    assert calls == [("verifier", "existing@example.test")]
    assert authority_counts(onboarding_db)["classroom_programs"] == 0


def test_new_adult_acceptance_is_exact_and_replay_refuses(onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    admin = site_admin_client()
    assert issue(admin, admin_csrf(admin)).status_code == 303
    _, path = invitation_path(delivered)
    visitor = TestClient(main.app)
    csrf = accept_csrf(visitor, path)
    before = authority_counts(onboarding_db)
    assert accept(visitor, path, csrf, pin="12345").status_code in {400, 409, 422}
    assert accept(visitor, path, csrf, display_name=" ").status_code in {400, 409, 422}
    assert authority_counts(onboarding_db) == before

    assert accept(visitor, path, csrf).status_code == 303
    with onboarding_db() as session:
        adult = session.scalar(select(TrustedVerifier).where(
            TrustedVerifier.email == "new.adult@example.test"))
        assert adult is not None and adult.display_name == "Founding Adult"
        assert verify_pin("1357", adult.pin_hash)
        program = session.get(ClassroomProgram, 11)
        assert program.owner_verifier_id == adult.id
        grants = list(session.scalars(select(ClassroomRoleGrant)))
        assert len(grants) == 1
        assert (grants[0].program_id, grants[0].verifier_id, grants[0].role) == (
            11, adult.id, "head_director")
        audit = list(session.scalars(select(ClassroomAuditEvent)))
        assert {row.action for row in audit} == {"program_provisioned", "role_granted"}
        assert all(row.actor_verifier_id == adult.id for row in audit)
        assert count(session, StudentVerifierConnection) == 1
        for model in (BillingAccount, Membership, MembershipSeat, ClassroomClass,
                      ClassroomTeachingAssignment, ClassroomStudentMembership):
            assert count(session, model) == 0
    assert accept(visitor, path, csrf).status_code in {400, 403, 404, 409, 410}
    with onboarding_db() as session:
        assert count(session, TrustedVerifier) == 2
        assert count(session, ClassroomProgram) == count(session, ClassroomRoleGrant) == 1


def test_other_valid_invitation_cannot_replace_founding_owner(onboarding_db):
    first = classroom_invitation.create_program_invitation(11, "first@example.test")
    second = classroom_invitation.create_program_invitation(11, "second@example.test")
    with onboarding_db() as session:
        program = classroom.accept_program_invitation(
            session, token=first, pin="1357", display_name="First Director",
        )
        session.commit()
        founding_owner_id = program.owner_verifier_id
    with onboarding_db() as session:
        with pytest.raises(classroom.ClassroomDenied, match="already provisioned"):
            classroom.accept_program_invitation(
                session, token=second, pin="2468", display_name="Second Director",
            )
        session.commit()
        assert session.get(ClassroomProgram, 11).owner_verifier_id == founding_owner_id
        assert session.scalar(select(TrustedVerifier.id).where(
            TrustedVerifier.email == "second@example.test")) is None
        assert count(session, ClassroomRoleGrant) == 1


def test_existing_adult_requires_old_pin_without_reset_or_relationship_change(onboarding_db, monkeypatch):
    delivered = capture_delivery(monkeypatch)
    admin = site_admin_client()
    assert issue(admin, admin_csrf(admin), email="  EXISTING@Example.Test  ").status_code == 303
    recipient, path = invitation_path(delivered)
    assert recipient == "existing@example.test"
    visitor = TestClient(main.app)
    csrf = accept_csrf(visitor, path)
    with onboarding_db() as session:
        old_hash = session.get(TrustedVerifier, 21).pin_hash
        old_relationship = session.scalar(select(StudentVerifierConnection.id))
    assert accept(visitor, path, csrf, pin="1111", display_name="Replacement").status_code in {
        400, 401, 403, 409,
    }
    assert authority_counts(onboarding_db)["classroom_programs"] == 0
    assert accept(visitor, path, csrf, pin="2468", display_name="Replacement").status_code == 303
    with onboarding_db() as session:
        adult = session.get(TrustedVerifier, 21)
        assert adult.pin_hash == old_hash and adult.display_name == "Original Adult"
        assert session.get(ClassroomProgram, 11).owner_verifier_id == 21
        assert count(session, TrustedVerifier) == 1
        assert session.scalar(select(StudentVerifierConnection.id)) == old_relationship
        assert count(session, StudentVerifierConnection) == 1
        assert count(session, ClassroomRoleGrant) == 1


def test_existing_free_verifier_login_and_student_invitation_still_work(onboarding_db):
    with onboarding_db() as session:
        created = create_trusted_verifier_invitation(
            session, profile=session.get(WoodchuckProfile, 31),
            email="free-director@example.test", role="verifier")
    visitor = TestClient(main.app)
    response = visitor.post(f"/trusted-verifiers/invitations/{created.token}/accept", data={
        "display_name": "Free Verifier", "pin": "1357",
    })
    assert response.status_code == 200
    assert response.json()["connection"]["role"] == "verifier"
    visitor.post("/trusted-verifiers/logout")
    response = visitor.post("/trusted-verifiers/login", data={
        "email": "existing@example.test", "pin": "2468",
    })
    assert response.status_code == 200
    assert response.json()["verifier"]["id"] == 21
    with onboarding_db() as session:
        assert count(session, ClassroomProgram) == 0
        assert count(session, ClassroomRoleGrant) == 0
        assert count(session, StudentVerifierConnection) == 2


def test_audit_failure_rolls_back_new_credential_and_authority(onboarding_db, monkeypatch):
    token = classroom_invitation.create_program_invitation(11, "rollback@example.test")
    original = classroom._audit

    def fail_after_audit(session, program_id, actor_id, action, **references):
        original(session, program_id, actor_id, action, **references)
        if action == "program_provisioned":
            session.flush()
            raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(classroom, "_audit", fail_after_audit)
    before = authority_counts(onboarding_db)
    with onboarding_db() as session:
        with pytest.raises(RuntimeError, match="synthetic audit failure"):
            classroom.accept_program_invitation(session, token=token,
                                                pin="1357", display_name="Rollback")
        session.commit()
    assert authority_counts(onboarding_db) == before


def test_role_insert_failure_rolls_back_new_credential_and_program(onboarding_db):
    token = classroom_invitation.create_program_invitation(11, "rollback@example.test")

    def fail_role_insert(mapper, connection, target):
        raise RuntimeError("synthetic role insert failure")

    event.listen(ClassroomRoleGrant, "before_insert", fail_role_insert)
    try:
        before = authority_counts(onboarding_db)
        with onboarding_db() as session:
            with pytest.raises(RuntimeError, match="synthetic role insert failure"):
                classroom.accept_program_invitation(session, token=token,
                                                    pin="1357", display_name="Rollback")
            session.commit()
        assert authority_counts(onboarding_db) == before
    finally:
        event.remove(ClassroomRoleGrant, "before_insert", fail_role_insert)
