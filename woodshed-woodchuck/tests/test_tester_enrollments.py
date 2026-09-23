"""Release 2 Pilot D1/C001 enrollment, access, and child-flow tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db import Base
from app import child_authorization as consent
from app.age_models import AccountPrivacy
from app.age_privacy import declare_age, eligible
from app.child_models import ConsentEvidence, PendingConsent
from app.membership_models import BillingAccount, Membership, MembershipSeat, ProviderSubscription
from app.models import TesterEnrollment as Enrollment, WoodchuckProfile
from app.security import generate_invitation_token, hash_invitation_token, hash_pin
from app import tester_enrollments as testers
from app.memberships import student_has_full_access


CLAIMED = datetime(2026, 9, 20, 15, 30, 12, 345678, tzinfo=timezone.utc)


@pytest.fixture
def tester_db(monkeypatch):
    monkeypatch.delenv("C001_REGISTRATION_DISABLED", raising=False)
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(module, "SessionLocal"):
            monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(testers, "clock", lambda: CLAIMED)
    yield factory
    engine.dispose()


def profile(session, suffix, created_at, *, age=None, status="active"):
    row = WoodchuckProfile(
        woodchuck_id=f"WC-TEST-{suffix}",
        display_name=f"Tester {suffix}",
        pin_hash=hash_pin("2468"),
        instrument="Flute",
        level="Beginner",
        goal="Practice every day",
        created_at=created_at,
        status=status,
    )
    session.add(row)
    session.flush()
    if age:
        declare_age(session, row.id, age, at=created_at)
    return row


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def account_form(age="adult"):
    return {
        "age_band": age,
        "display_name": "Cohort Student",
        "pin": "2468",
        "instrument": "Flute",
        "level": "Beginner",
        "goal": "Practice every day",
        "initial_state": json.dumps({"progress": {"credits": 99}}),
    }


def test_pilot_d1_backfill_is_central_day_idempotent_and_keeps_original_join_time(tester_db):
    # September 16 in Chicago is 05:00 UTC through (but not including) Sep 17 05:00 UTC.
    qualifying = [
        datetime(2026, 9, 16, 5, tzinfo=timezone.utc),
        datetime(2026, 9, 17, 4, 59, 59, tzinfo=timezone.utc),
    ]
    with tester_db() as session:
        rows = [profile(session, str(i), stamp, age="adult") for i, stamp in enumerate(qualifying)]
        profile(session, "BEFORE", qualifying[0] - timedelta(microseconds=1), age="adult")
        profile(session, "AFTER", qualifying[1] + timedelta(seconds=1), age="adult")
        session.commit()
        assert testers.backfill_pilot_d1(session) == 2
        session.commit()
        assert testers.backfill_pilot_d1(session) == 0
        session.commit()
        enrollments = list(session.scalars(select(Enrollment).order_by(Enrollment.profile_id)))
        assert [(row.profile_id, testers.utc(row.joined_at)) for row in enrollments] == [
            (rows[0].id, qualifying[0]),
            (rows[1].id, qualifying[1]),
        ]
        assert all(student_has_full_access(session, row.id) for row in rows)
        assert count(session, BillingAccount) == count(session, Membership) == 0
        assert count(session, MembershipSeat) == count(session, ProviderSubscription) == 0


def test_tester_entitlement_is_lifetime_but_never_bypasses_age_or_inactive_status(tester_db):
    with tester_db() as session:
        unknown = profile(session, "UNKNOWN", CLAIMED - timedelta(days=4))
        adult = profile(session, "ADULT", CLAIMED - timedelta(days=4), age="adult")
        deleted = profile(session, "DELETED", CLAIMED - timedelta(days=4), age="adult")
        deleted.status = "deleted"
        for row in (unknown, adult, deleted):
            testers.enroll_tester(session, row.id, testers.PILOT_D1, row.created_at)
            # Repeating the generic internal operation returns the same row.
            assert testers.enroll_tester(session, row.id, testers.PILOT_D1, row.created_at).profile_id == row.id
        session.commit()
        assert not student_has_full_access(session, unknown.id)
        assert student_has_full_access(session, adult.id)
        assert student_has_full_access(session, adult.id, at=CLAIMED + timedelta(days=3650))
        assert not student_has_full_access(session, deleted.id)
        assert count(session, Enrollment) == 3


def test_c001_guest_context_only_comes_from_deliberate_route_and_creates_nothing(tester_db):
    client = TestClient(app)
    with tester_db() as session:
        before = count(session, Enrollment), count(session, WoodchuckProfile)
    forged = client.get("/guest?c001=true", headers={"X-C001": "true"})
    assert "official C001 entry link" in forged.text
    assert "Create a C001 Pre-Beta account" not in forged.text
    response = client.get("/prebeta/C001", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/guest"
    guest = client.get("/guest")
    assert guest.status_code == 200
    assert "Create a C001 Pre-Beta account" in guest.text
    assert 'id="guest-setup-form"' in guest.text
    assert "account-create-form" not in guest.text
    assert "C001 Pre-Beta registration" in client.get("/setup").text
    with tester_db() as session:
        assert (count(session, Enrollment), count(session, WoodchuckProfile)) == before


def test_c001_13plus_creation_is_atomic_full_and_does_not_touch_memberships(tester_db):
    client = TestClient(app)
    client.get("/prebeta/C001")
    created = client.post("/account/create", data=account_form())
    assert created.status_code == 200, created.text
    profile_id = created.json()["profile"]["id"]
    with tester_db() as session:
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.profile_id == profile_id,
            Enrollment.cohort_key == testers.C001,
        ))
        assert testers.utc(enrollment.joined_at) == CLAIMED
        assert student_has_full_access(session, profile_id)
        assert count(session, Enrollment) == 1
        assert count(session, BillingAccount) == count(session, Membership) == 0
        assert count(session, MembershipSeat) == count(session, ProviderSubscription) == 0
    duplicate = client.post("/account/create", data=account_form())
    assert duplicate.status_code == 400
    with tester_db() as session:
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 1

    ordinary = TestClient(app)
    plain = ordinary.post("/account/create", data={**account_form(), "display_name": "Ordinary"})
    assert plain.status_code == 200, plain.text
    with tester_db() as session:
        assert count(session, WoodchuckProfile) == 2
        assert count(session, Enrollment) == 1


def test_c001_account_and_enrollment_roll_back_together_on_invalid_state(tester_db):
    client = TestClient(app)
    client.get("/prebeta/C001")
    broken = client.post("/account/create", data={**account_form(), "initial_state": "{"})
    assert broken.status_code == 400
    with tester_db() as session:
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 0
        assert count(session, AccountPrivacy) == 0
    # The signed claim remains available after rollback so a corrected retry works.
    repaired = client.post("/account/create", data=account_form())
    assert repaired.status_code == 200, repaired.text
    with tester_db() as session:
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 1


def test_existing_signed_in_profile_cannot_publicly_join_c001(tester_db):
    with tester_db() as session:
        row = profile(session, "EXISTING", CLAIMED - timedelta(days=2), age="adult")
        session.commit()
        profile_id = row.id
    client = TestClient(app)
    assert client.post("/account/login", data={"woodchuck_id": "WC-TEST-EXISTING", "pin": "2468"}).status_code == 200
    client.get("/prebeta/C001")
    guest = client.get("/guest")
    assert "guest-confirm-logout" in guest.text
    assert "Create a C001 Pre-Beta account" not in guest.text
    with tester_db() as session:
        assert session.scalar(select(Enrollment).where(Enrollment.profile_id == profile_id)) is None


def prepare_child_services(monkeypatch):
    monkeypatch.setattr(consent, "clock", lambda: CLAIMED)
    monkeypatch.setattr(consent, "under13_available", lambda: True)
    monkeypatch.setattr(consent, "require_under13_review", lambda: None)
    monkeypatch.setattr(
        consent,
        "notice_policy",
        lambda environment=None: (consent.NOTICE_VERSION, consent.NOTICE, consent.NOTICE_SHA256),
    )
    monkeypatch.setattr(consent, "send_copy", lambda *args, **kwargs: None)


def test_c001_under13_claim_survives_cross_device_activation_and_retry(tester_db, monkeypatch):
    prepare_child_services(monkeypatch)
    student = TestClient(app)
    student.get("/prebeta/C001")
    page = student.get("/family/request")
    assert "preserve the new account's C001 claim" in page.text
    response = student.post("/family/request", data={
        "csrf": page.context["csrf"],
        "confirm_account": "new",
        "parent_email": "parent@example.test",
    })
    assert response.status_code == 200, response.text
    with tester_db() as session:
        pending = session.scalar(select(PendingConsent))
        assert pending.profile_id is None
        assert pending.cohort_key == testers.C001
        assert testers.utc(pending.cohort_claimed_at) == CLAIMED
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 0

        activation_token = generate_invitation_token()
        pending.activation_hash = hash_invitation_token(activation_token)
        pending.approved_at = CLAIMED + timedelta(hours=1)
        pending.confirmed_at = CLAIMED + timedelta(hours=1)
        session.commit()

    verification = SimpleNamespace(
        notice_sha256=consent.NOTICE_SHA256,
        director_allowed=False,
        state="verified",
        activated_consent_id=None,
    )
    from app import kws_verification
    monkeypatch.setattr(kws_verification, "verified_for_activation", lambda session, row: verification)

    # Closure stops new claims, but must not strand an established parent flow.
    monkeypatch.setenv("C001_REGISTRATION_DISABLED", "true")
    # This is a fresh database session with no original browser/session context.
    with tester_db() as parent_device_session:
        created, evidence, permission = consent.activate(
            parent_device_session,
            activation_token,
            profile=None,
            fields={
                "display_name": "Young Tester",
                "pin": "2468",
                "instrument": "Flute",
                "level": "Beginner",
                "goal": "Practice every day",
            },
        )
        parent_device_session.commit()
        profile_id = created.id
        enrollment = parent_device_session.scalar(select(Enrollment).where(
            Enrollment.profile_id == profile_id,
        ))
        assert enrollment.cohort_key == testers.C001
        assert testers.utc(enrollment.joined_at) == CLAIMED
        assert evidence.profile_id == profile_id and permission is None
        assert eligible(parent_device_session, profile_id)
        assert student_has_full_access(parent_device_session, profile_id)

    with tester_db() as retry:
        with pytest.raises(ValueError):
            consent.activate(retry, activation_token, profile=None, fields={})
        retry.rollback()
        assert count(retry, WoodchuckProfile) == count(retry, Enrollment) == 1


def test_failed_expired_or_withdrawn_child_claim_never_creates_enrollment(tester_db, monkeypatch):
    prepare_child_services(monkeypatch)
    with tester_db() as session:
        token = generate_invitation_token()
        row = PendingConsent(
            profile_id=None,
            parent_email="parent@example.test",
            director_email="",
            director_name="",
            review_allowed=False,
            approve_hash=hash_invitation_token(generate_invitation_token()),
            activation_hash=hash_invitation_token(token),
            created_at=CLAIMED - timedelta(days=3),
            expires_at=CLAIMED - timedelta(seconds=1),
            approved_at=CLAIMED - timedelta(days=2),
            confirmed_at=CLAIMED - timedelta(days=2),
            notice_version=consent.NOTICE_VERSION,
            cohort_key=testers.C001,
            cohort_claimed_at=CLAIMED - timedelta(days=3),
        )
        session.add(row)
        session.commit()
        with pytest.raises(ValueError, match="expired"):
            consent.activate(session, token, profile=None, fields={})
        session.rollback()
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 0


def test_membership_and_tester_access_coexist_without_spreading_tester_benefit(tester_db):
    with tester_db() as session:
        tester = profile(session, "COEXIST", CLAIMED - timedelta(days=3), age="adult")
        other = profile(session, "OTHER", CLAIMED - timedelta(days=3), age="adult")
        testers.enroll_tester(session, tester.id, testers.C001, CLAIMED - timedelta(days=2))
        billing = BillingAccount(profile_id=other.id)
        session.add(billing)
        session.flush()
        membership = Membership(billing_account_id=billing.id, source="manual", status="active")
        session.add(membership)
        session.flush()
        session.add(MembershipSeat(membership_id=membership.id, profile_id=tester.id, slot_number=1))
        session.commit()
        assert student_has_full_access(session, tester.id)
        assert not student_has_full_access(session, other.id)
        assert count(session, Enrollment) == 1
        assert count(session, MembershipSeat) == 1
