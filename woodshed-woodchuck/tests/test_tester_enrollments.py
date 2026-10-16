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


@pytest.fixture(params=["sqlite", "postgresql"])
def tester_db(monkeypatch, request, tmp_path):
    monkeypatch.delenv("C001_REGISTRATION_DISABLED", raising=False)
    from tests.test_team_families import disposable_url
    engine = (create_engine("sqlite://", poolclass=StaticPool,
                            connect_args={"check_same_thread": False})
              if request.param == "sqlite" else create_engine(disposable_url(tmp_path, "postgresql")))
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


def test_c002_direct_entry_is_director_attributed_guest_context_only(tester_db):
    client = TestClient(app)
    with tester_db() as session:
        before = count(session, Enrollment), count(session, WoodchuckProfile)
    response = client.get("/prebeta/C002?source=FORGED")
    assert response.status_code == 200
    assert testers.registration_context(response.context["request"]) == testers.C002
    assert testers.registration_source(response.context["request"]) == testers.DIRECTOR1
    assert 'data-prebeta-context="true"' in response.text
    assert 'data-prebeta-cohort="C002"' in response.text
    assert 'data-c001-context="false"' in response.text
    assert "Create a C002 Pre-Beta account" in response.text
    setup = client.get("/setup?age=under13")
    assert "C002 Pre-Beta registration" in setup.text
    assert "Your C002 claim will stay with the parent request across devices." in setup.text
    with tester_db() as session:
        assert (count(session, Enrollment), count(session, WoodchuckProfile)) == before


def test_c002_secret_symbol_is_director_attributed_and_creates_no_persistent_rows(tester_db):
    client = TestClient(app)
    with tester_db() as session:
        before = count(session, Enrollment), count(session, WoodchuckProfile)
    for _ in range(2):
        response = client.post("/guest/secret-symbol", data={"passcode": " c002 "})
        assert response.status_code == 200
        assert "C002 Pre-Beta recognized." in response.text
        assert testers.registration_context(response.context["request"]) == testers.C002
        assert testers.registration_source(response.context["request"]) == testers.DIRECTOR1
    with tester_db() as session:
        assert (count(session, Enrollment), count(session, WoodchuckProfile)) == before


@pytest.mark.parametrize("entry,source", [
    ("/prebeta/C001", None),
    ("/prebeta/C001?entry=secret-symbol", "DIRECTOR1"),
    ("/prebeta/C001?entry=director1", "DIRECTOR1"),
])
@pytest.mark.parametrize("age", ["adult", "13to17"])
def test_c001_13plus_creation_is_atomic_full_and_does_not_touch_memberships(tester_db, entry, source, age):
    client = TestClient(app)
    client.get(entry)
    created = client.post("/account/create", data=account_form(age))
    assert created.status_code == 200, created.text
    profile_id = created.json()["profile"]["id"]
    with tester_db() as session:
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.profile_id == profile_id,
            Enrollment.cohort_key == testers.C001,
        ))
        assert testers.utc(enrollment.joined_at) == CLAIMED
        assert enrollment.source == source
        assert student_has_full_access(session, profile_id)
        assert student_has_full_access(session, profile_id, at=CLAIMED + timedelta(days=3650))
        assert count(session, Enrollment) == 1
        assert count(session, BillingAccount) == count(session, Membership) == 0
        assert count(session, MembershipSeat) == count(session, ProviderSubscription) == 0
    duplicate = client.post("/account/create", data=account_form())
    assert duplicate.status_code == 400
    with tester_db() as session:
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 1

    from app.analytics import build_report
    assert build_report(tester_db, cohort_key=testers.C001)["enrolled"] == 1

    ordinary = TestClient(app)
    plain = ordinary.post("/account/create", data={**account_form(), "display_name": "Ordinary"})
    assert plain.status_code == 200, plain.text
    with tester_db() as session:
        assert count(session, WoodchuckProfile) == 2
        assert count(session, Enrollment) == 1


def test_c002_context_survives_guest_registration_with_lifetime_access_and_distinct_analytics(tester_db):
    client = TestClient(app)
    entry = client.get("/prebeta/C002?source=FORGED")
    assert entry.status_code == 200
    for path in ("/guest", "/setup", "/guest", "/prebeta/C002", "/prebeta/C002?entry=unknown"):
        page = client.get(path)
        assert page.status_code == 200
        assert testers.registration_context(page.context["request"]) == testers.C002
        assert testers.registration_source(page.context["request"]) == testers.DIRECTOR1

    failed = client.post("/account/create", data={**account_form("13to17"), "initial_state": "{"})
    assert failed.status_code == 400
    assert testers.registration_context(client.get("/guest").context["request"]) == testers.C002

    created = client.post("/account/create", data=account_form("13to17"))
    assert created.status_code == 200, created.text
    profile_id = created.json()["profile"]["id"]
    with tester_db() as session:
        enrollment = session.scalar(select(Enrollment).where(
            Enrollment.profile_id == profile_id,
            Enrollment.cohort_key == testers.C002,
        ))
        assert enrollment.source == testers.DIRECTOR1
        assert testers.utc(enrollment.joined_at) == CLAIMED
        assert student_has_full_access(session, profile_id)
        assert student_has_full_access(session, profile_id, at=CLAIMED + timedelta(days=3650))
        assert count(session, Enrollment) == 1
        assert count(session, BillingAccount) == count(session, Membership) == 0
        assert count(session, MembershipSeat) == count(session, ProviderSubscription) == 0

    assert client.post("/account/create", data=account_form()).status_code == 400
    with tester_db() as session:
        assert count(session, Enrollment) == count(session, WoodchuckProfile) == 1

    from app.analytics import build_report
    report = build_report(tester_db, cohort_key=testers.C002)
    assert report["cohort_key"] == testers.C002
    assert report["enrolled"] == 1
    assert [student["source"] for student in report["students"]] == [testers.DIRECTOR1]
    assert build_report(tester_db, cohort_key=testers.C001)["enrolled"] == 0
    assert build_report(tester_db, cohort_key=testers.PILOT_D1)["enrolled"] == 0


def test_c002_does_not_use_c001_activation_control_or_cap(tester_db, monkeypatch):
    from app import c001_abuse

    monkeypatch.setattr(c001_abuse, "authorize_activation", lambda *args, **kwargs: pytest.fail("C002 used C001 activation control"))
    monkeypatch.setenv("C001_ACTIVATION_ENABLED", "false")
    client = TestClient(app)
    assert client.get("/prebeta/C002").status_code == 200
    assert client.post("/account/create", data=account_form("adult")).status_code == 200


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


@pytest.mark.parametrize("entry,cohort,source", [
    ("/prebeta/C001", "C001", None),
    ("/prebeta/C001?entry=secret-symbol", "C001", "DIRECTOR1"),
    ("/prebeta/C001?entry=director1", "C001", "DIRECTOR1"),
    ("/prebeta/C002", "C002", "DIRECTOR1"),
])
def test_prebeta_under13_claim_survives_cross_device_activation_and_retry(tester_db, monkeypatch, entry, cohort, source):
    prepare_child_services(monkeypatch)
    student = TestClient(app)
    student.get(entry)
    assert student.post("/account/create", data=account_form("under13")).status_code == 403
    page = student.get("/family/request")
    assert f"preserve the new account's {cohort} claim" in page.text
    forged = student.post("/family/request", data={
        "csrf": page.context["csrf"],
        "confirm_account": "new",
        "parent_email": "parent@example.test",
        "cohort_key": "FORGED",
        "cohort_source": "FORGED",
        "source": "FORGED",
    })
    assert forged.status_code == 400
    with tester_db() as session:
        assert count(session, PendingConsent) == count(session, Enrollment) == 0
    response = student.post("/family/request", data={
        "csrf": page.context["csrf"],
        "confirm_account": "new",
        "parent_email": "parent@example.test",
    })
    assert response.status_code == 200, response.text
    with tester_db() as session:
        pending = session.scalar(select(PendingConsent))
        assert pending.profile_id is None
        assert pending.cohort_key == cohort
        assert pending.cohort_source == source
        assert testers.utc(pending.cohort_claimed_at) == CLAIMED
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 0

        activation_token = generate_invitation_token()
        pending.activation_hash = hash_invitation_token(activation_token)
        session.commit()
        with pytest.raises(ValueError):
            consent.activate(session, activation_token, profile=None, fields={})
        session.rollback()
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 0
        pending.approved_at = CLAIMED + timedelta(hours=1)
        pending.confirmed_at = CLAIMED + timedelta(hours=1)
        session.commit()
        # Approval still has not created the authorized persistent account.
        assert count(session, WoodchuckProfile) == count(session, Enrollment) == 0

    verification = SimpleNamespace(
        notice_sha256=consent.NOTICE_SHA256,
        director_allowed=False,
        state="verified",
        activated_consent_id=None,
    )
    from app import kws_verification
    monkeypatch.setattr(kws_verification, "verified_for_activation", lambda session, row: verification)
    activated_at = CLAIMED + timedelta(hours=2)
    monkeypatch.setattr(consent, "clock", lambda: activated_at)
    monkeypatch.setattr(testers, "clock", lambda: consent.clock())

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
        assert enrollment.cohort_key == cohort
        assert enrollment.source == source
        assert testers.utc(enrollment.joined_at) == activated_at
        assert testers.utc(enrollment.joined_at) != CLAIMED
        pending = parent_device_session.scalar(select(PendingConsent))
        assert testers.utc(pending.cohort_claimed_at) == CLAIMED
        assert pending.cohort_source == source
        assert evidence.profile_id == profile_id and permission is None
        assert eligible(parent_device_session, profile_id)
        assert student_has_full_access(parent_device_session, profile_id)

    monkeypatch.setattr(consent, "clock", lambda: activated_at + timedelta(hours=1))
    with tester_db() as retry:
        with pytest.raises(ValueError):
            consent.activate(retry, activation_token, profile=None, fields={})
        retry.rollback()
        assert count(retry, WoodchuckProfile) == count(retry, Enrollment) == 1
        enrollment = retry.scalar(select(Enrollment))
        assert testers.utc(enrollment.joined_at) == activated_at
        assert enrollment.source == source
        assert testers.utc(retry.scalar(select(PendingConsent)).cohort_claimed_at) == CLAIMED


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


def test_secret_entry_context_is_idempotent_and_creates_no_persistent_rows(tester_db):
    client = TestClient(app)
    def counts():
        with tester_db() as session:
            return {table.name: session.scalar(select(func.count()).select_from(table))
                    for table in Base.metadata.sorted_tables if not table.name.startswith('c001_')}
    before = counts()
    assert 'Create a C001 Pre-Beta account' not in client.get('/guest').text
    for _ in range(2):
        guest = client.get('/prebeta/C001?entry=secret-symbol')
        assert guest.status_code == 200
        assert guest.context['request'].session == {
            testers.SESSION_REGISTRATION_CONTEXT: {'cohort_key': 'C001', 'source': 'DIRECTOR1'}}
        assert 'C001 Pre-Beta recognized.' in guest.text
        assert 'data-guest="local"' in guest.text
        assert "connect-src 'none'" in guest.headers['content-security-policy']
        assert counts() == before
    # A later QR visit must not erase the known classroom attribution.
    again = client.get('/prebeta/C001')
    assert testers.registration_source(again.context['request']) == 'DIRECTOR1'


def test_secret_entry_existing_account_is_informational_only(tester_db):
    client = TestClient(app)
    created = client.post('/account/create', data=account_form())
    assert created.status_code == 200
    with tester_db() as session:
        before = {table.name: session.scalar(select(func.count()).select_from(table))
                  for table in Base.metadata.sorted_tables if not table.name.startswith('c001_')}
    for _ in range(2):
        response = client.post('/account/daily-secret', json={'passcode': ' c001 '})
        assert response.status_code == 200
        assert 'Your account has not changed' in response.json()['message']
    with tester_db() as session:
        assert not student_has_full_access(session, created.json()['profile']['id'])
        assert before == {table.name: session.scalar(select(func.count()).select_from(table))
                          for table in Base.metadata.sorted_tables if not table.name.startswith('c001_')}
    assert testers.registration_context(client.get('/guest').context['request']) is None


def test_invalid_secret_and_forged_attribution_do_not_create_claim(tester_db):
    client = TestClient(app)
    bad = client.post('/account/daily-secret', json={'passcode': 'C003'})
    assert bad.status_code == 400
    assert bad.json()['detail'] == 'That passcode did not match. Try again.'
    guest = client.get('/guest?source=DIRECTOR1&cohort=C001')
    assert testers.registration_context(guest.context['request']) is None
    created = client.post('/account/create', data={**account_form(), 'source': 'DIRECTOR1', 'cohort_key': 'C001'})
    assert created.status_code == 200
    with tester_db() as session:
        assert count(session, Enrollment) == 0
        assert not student_has_full_access(session, created.json()['profile']['id'])


def test_secret_entry_respects_closure_and_keeps_established_claim(tester_db, monkeypatch):
    client = TestClient(app)
    client.get('/prebeta/C001?entry=secret-symbol')
    monkeypatch.setenv('C001_REGISTRATION_DISABLED', 'true')
    fresh = TestClient(app)
    assert fresh.get('/prebeta/C001?entry=secret-symbol').status_code == 503
    assert testers.registration_context(fresh.get('/guest').context['request']) is None
    assert client.get('/prebeta/C001?entry=secret-symbol').status_code == 200
    created = client.post('/account/create', data=account_form())
    assert created.status_code == 200
    with tester_db() as session:
        assert session.scalar(select(Enrollment)).source == 'DIRECTOR1'


def test_existing_non_c001_secret_still_rewards_once(tester_db):
    from app.models import RewardGrant
    client = TestClient(app)
    assert client.post('/account/create', data=account_form()).status_code == 200
    first = client.post('/account/daily-secret', json={'passcode': ' UnIoN '})
    repeat = client.post('/account/daily-secret', json={'passcode': 'union'})
    assert first.status_code == repeat.status_code == 200
    assert first.json()['redeemed'] is True and first.json()['amount'] == 20
    assert repeat.json()['redeemed'] is False and repeat.json()['amount'] == 0
    with tester_db() as session:
        assert count(session, Enrollment) == 0
        grants = list(session.scalars(select(RewardGrant).where(RewardGrant.source_key.like('daily-secret:%'))))
        assert len(grants) == 1 and grants[0].amount == 20


def test_classroom_registration_has_no_125_tester_cap(tester_db):
    with tester_db() as session:
        for index in range(125):
            row = WoodchuckProfile(woodchuck_id=f'WC-COHORT-{index}', display_name='Existing tester',
                pin_hash='synthetic', instrument='Flute', level='Beginner', goal='Practice')
            session.add(row)
            session.flush()
            session.add(Enrollment(profile_id=row.id, cohort_key='C001', joined_at=CLAIMED))
        session.commit()
    client = TestClient(app)
    assert client.get('/prebeta/C001?entry=secret-symbol').status_code == 200
    created = client.post('/account/create', data=account_form('13to17'))
    assert created.status_code == 200
    with tester_db() as session:
        assert count(session, Enrollment) == 126
        assert student_has_full_access(session, created.json()['profile']['id'])


@pytest.mark.parametrize("entry", [
    "/prebeta/C001?source=DIRECTOR1",
    "/prebeta/C001?source=FORGED",
    "/prebeta/C001?entry=DIRECTOR1",
    "/prebeta/C001?entry=unknown&source=DIRECTOR1",
])
def test_c001_unknown_markers_and_query_or_form_sources_cannot_forge_source(tester_db, entry):
    client = TestClient(app)
    page = client.get(entry)
    assert testers.registration_context(page.context["request"]) == testers.C001
    assert testers.registration_source(page.context["request"]) is None
    created = client.post("/account/create", data={
        **account_form("13to17"), "source": "DIRECTOR1", "cohort_source": "FORGED",
        "cohort_key": "FORGED", "entry": "director1",
    })
    assert created.status_code == 200, created.text
    with tester_db() as session:
        row = session.scalar(select(Enrollment))
        assert row.cohort_key == testers.C001
        assert row.source is None
        assert testers.utc(row.joined_at) == CLAIMED


def test_director_context_survives_guest_setup_refresh_and_bare_navigation(tester_db):
    client = TestClient(app)
    entry = client.get("/prebeta/C001?entry=director1&source=FORGED")
    assert entry.status_code == 200
    assert testers.registration_source(entry.context["request"]) == testers.DIRECTOR1
    assert "data-guest=\"local\"" in entry.text
    with tester_db() as session:
        assert count(session, Enrollment) == count(session, WoodchuckProfile) == 0
    for path in ("/guest", "/setup", "/guest", "/prebeta/C001", "/prebeta/C001?source=FORGED"):
        page = client.get(path)
        assert page.status_code == 200
        assert testers.registration_context(page.context["request"]) == testers.C001
        assert testers.registration_source(page.context["request"]) == testers.DIRECTOR1

    failed = client.post("/account/create", data={**account_form(), "initial_state": "{"})
    assert failed.status_code == 400
    assert testers.registration_source(client.get("/guest").context["request"]) == testers.DIRECTOR1
    created = client.post("/account/create", data={**account_form(), "source": "FORGED"})
    assert created.status_code == 200, created.text
    # Once persisted, later route visits cannot change the enrollment attribution.
    client.get("/prebeta/C001?source=FORGED")
    with tester_db() as session:
        row = session.scalar(select(Enrollment))
        assert row.source == testers.DIRECTOR1
        assert testers.utc(row.joined_at) == CLAIMED
        assert count(session, Enrollment) == 1


def test_unsigned_browser_context_cannot_create_tester_enrollment(tester_db):
    from base64 import b64encode

    original = TestClient(app)
    original.get("/prebeta/C001?entry=director1")
    signed_cookie = original.cookies.get("session")
    _, timestamp, signature = signed_cookie.split(".")
    forged_payload = b64encode(json.dumps({testers.SESSION_REGISTRATION_CONTEXT: {
        "cohort_key": "C001", "source": "DIRECTOR1",
    }, "untrusted": True}).encode()).decode()
    attacker = TestClient(app)
    attacker.cookies.set("session", ".".join((forged_payload, timestamp, signature)))
    assert testers.registration_context(attacker.get("/guest").context["request"]) is None
    created = attacker.post("/account/create", data={**account_form(), "source": "DIRECTOR1"})
    assert created.status_code == 200, created.text
    with tester_db() as session:
        assert count(session, Enrollment) == 0


def test_duplicate_enrollment_and_deleted_profile_preserve_historical_provenance(tester_db):
    from app.account_deletion import anonymize_woodchuck_account

    with tester_db() as session:
        original_rows = []
        for suffix, source in (("DIRECTOR", testers.DIRECTOR1), ("NULL", None)):
            student = profile(session, suffix, CLAIMED - timedelta(days=5), age="13to17")
            row = testers.enroll_tester(session, student.id, testers.C001,
                                        CLAIMED - timedelta(days=4), source=source)
            original_rows.append((row.id, student.id, testers.utc(row.joined_at), row.source))
        session.commit()
        for row_id, student_id, joined_at, source in original_rows:
            duplicate = testers.enroll_tester(session, student_id, testers.C001, CLAIMED,
                                               source=None if source else testers.DIRECTOR1)
            assert duplicate.id == row_id
            assert testers.utc(duplicate.joined_at) == joined_at
            assert duplicate.source == source
            anonymize_woodchuck_account(session,
                profile=session.get(WoodchuckProfile, student_id), now=CLAIMED)
        session.commit()
        session.expire_all()
        after = list(session.scalars(select(Enrollment).order_by(Enrollment.id)))
        assert [(row.id, row.profile_id, testers.utc(row.joined_at), row.source)
                for row in after] == original_rows
        assert all(session.get(WoodchuckProfile, row.profile_id).status == "deleted" for row in after)
        assert all(not student_has_full_access(session, row.profile_id) for row in after)


def test_c001_reauthorization_preserves_original_joined_at_and_source(tester_db, monkeypatch):
    from app import kws_verification

    prepare_child_services(monkeypatch)
    original_join = CLAIMED - timedelta(days=4)
    with tester_db() as session:
        student = profile(session, "REAUTH", original_join)
        rule = declare_age(session, student.id, "under13")
        evidence = ConsentEvidence(profile_id=student.id, parent_email="parent@example.test",
            notice_version=consent.NOTICE_VERSION, notice_sha256=consent.NOTICE_SHA256,
            approved_at=original_join, confirmed_at=original_join)
        session.add(evidence)
        session.flush()
        rule.consent_id = evidence.id
        session.flush()
        enrollment = testers.enroll_tester(session, student.id, testers.C001,
                                           original_join, source=testers.DIRECTOR1)
        session.commit()
        consent.withdraw_evidence(session, evidence)
        session.commit()
        assert not eligible(session, student.id)
        before = enrollment.id, testers.utc(enrollment.joined_at), enrollment.source
        token = generate_invitation_token()
        pending = consent.request_consent(session, profile=student, parent_email="parent@example.test")
        assert pending.cohort_key is None and pending.cohort_source is None
        pending.activation_hash = hash_invitation_token(token)
        pending.approved_at = pending.confirmed_at = CLAIMED
        session.commit()
        student_id = student.id

    verification = SimpleNamespace(notice_sha256=consent.NOTICE_SHA256, director_allowed=False,
        state="verified", activated_consent_id=None)
    monkeypatch.setattr(kws_verification, "verified_for_activation", lambda session, row: verification)
    monkeypatch.setattr(consent, "clock", lambda: CLAIMED + timedelta(hours=1))
    with tester_db() as session:
        student = session.get(WoodchuckProfile, student_id)
        activated, _, _ = consent.activate(session, token, profile=student, fields={})
        session.commit()
        session.expire_all()
        row = session.scalar(select(Enrollment).where(Enrollment.profile_id == student_id))
        assert activated.id == student_id
        assert (row.id, testers.utc(row.joined_at), row.source) == before
        assert count(session, Enrollment) == count(session, WoodchuckProfile) == 1
        assert eligible(session, student_id)
        assert student_has_full_access(session, student_id)
