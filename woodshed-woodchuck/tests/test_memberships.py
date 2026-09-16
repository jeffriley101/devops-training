"""Membership isolation, persistence and disabled-provider contract tests."""
import base64
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from app import main, membership_routes as routes, memberships as m, feature_access
from app.db import Base
from app.models import (WoodchuckProfile, TrustedVerifier, StudentVerifierConnection,
    BillingAccount, Membership, MembershipSeat, MembershipSeatInvitation, MembershipAuditEvent,
    ProviderSubscription, BillingProviderEvent)
from app.billing_config import BillingConfig, PLANS, available_plans, new_subscription_plan
from app import billing_providers as providers
from app.email_service import EmailService

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)
ADMIN = m.Actor("admin")


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'membership.db'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=OFF")  # Disposable test DB only.
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    from app import session_revocations
    monkeypatch.setattr(session_revocations, "SessionLocal", factory)
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(m, "clock", lambda: NOW)
    monkeypatch.setattr(providers, "clock", lambda: NOW)
    for key in ("PUBLIC_SUBSCRIPTIONS_ENABLED", "PAYPAL_BILLING_ENABLED", "STRIPE_BILLING_ENABLED", "LAUNCH_SALE_ENABLED", "CHECKOUT_VALIDITY_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "test-site-admin")
    with factory() as session:
        session.add_all([WoodchuckProfile(id=i, woodchuck_id=f"WC-MEMBER{i}", display_name=f"Student {i}",
            pin_hash="not-shown", instrument="Trumpet", level="Beginner", goal="Practice") for i in range(1, 10)])
        session.add_all([TrustedVerifier(id=i, email=f"payer{i}@example.test", display_name=f"Adult {i}",
                                         pin_hash="secret") for i in (1, 2)])
        session.commit()
    yield factory
    engine.dispose()


def client(kind=None, id=1, both=False):
    result = TestClient(main.app)
    session = {}
    if kind == "student" or both:
        session.update(woodchuck_profile_id=id, woodchuck_session_version=0)
    if kind == "adult" or both:
        session["trusted_verifier_id"] = id
    value = TimestampSigner(main.SESSION_SECRET).sign(base64.b64encode(json.dumps(session).encode())).decode()
    result.cookies.set("session", value)
    return result


def page_csrf(client, path="/membership"):
    response = client.get(path)
    assert response.status_code == 200
    return response.context["csrf"]


def grant(db, kind="adult", owner_id=1, **kwargs):
    with db() as session:
        member = m.create_complimentary_membership(session, m.Actor(kind, owner_id), ADMIN, **kwargs)
        session.commit()
        return member.id


@pytest.mark.parametrize("kind", ["student", "adult"])
def test_billing_identity_reuse_and_exclusive_owner(db, kind):
    with db() as session:
        account = m.billing_account(session, m.Actor(kind, 1), create=True)
        assert m.billing_account(session, m.Actor(kind, 1), create=True).id == account.id
        session.commit()
        assert session.scalar(select(func.count()).select_from(BillingAccount)) == 1
        assert (account.profile_id, account.verifier_id) == ((1, None) if kind == "student" else (None, 1))
        session.add(BillingAccount(profile_id=2, verifier_id=2))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        session.add(BillingAccount())
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        session.add(BillingAccount(**({"profile_id": 1} if kind == "student" else {"verifier_id": 1})))
        with pytest.raises(IntegrityError):
            session.flush()


def test_derived_access_manual_expiry_revocation_and_auto_seat(db):
    with db() as session:
        assert not m.student_has_full_access(session, 1)
    member = grant(db, "student", access_until=NOW + timedelta(days=10))
    with db() as session:
        assert m.student_has_full_access(session, 1)
        assert not m.student_has_full_access(session, 1, NOW - timedelta(seconds=1))
        assert not m.student_has_full_access(session, 1, NOW + timedelta(days=10))
        assert m.active_seats(session, member)[0].slot_number == 1
        assert "tier" not in WoodchuckProfile.__table__.columns
        m.revoke_complimentary_membership(session, member, ADMIN)
        session.commit()
        assert not m.student_has_full_access(session, 1)
        assert len(m.active_seats(session, member)) == 0
        assert session.scalar(select(func.count()).select_from(MembershipSeat)) == 1
        actions = set(session.scalars(select(MembershipAuditEvent.action)))
        assert {"membership_created", "complimentary_full_granted", "seat_added", "seat_removed", "membership_revoked"} <= actions


def test_five_spots_remove_reassign_and_cross_membership_unique(db):
    member = grant(db)
    other = grant(db, owner_id=2)
    with db() as session:
        assert m.active_seats(session, member) == []
        for i in range(1, 6):
            m.add_seat(session, member, i, ADMIN)
        session.commit()
        with pytest.raises(ValueError, match="five"):
            m.add_seat(session, member, 6, ADMIN)
        session.rollback()
        with pytest.raises(ValueError, match="already"):
            m.add_seat(session, other, 1, ADMIN)
        session.rollback()
        seat = m.active_seats(session, member)[0]
        m.remove_seat(session, member, seat.id, ADMIN)
        m.add_seat(session, member, 6, ADMIN)
        m.add_seat(session, other, 1, ADMIN)
        session.commit()
        assert len(m.active_seats(session, member)) == 5
        assert len(m.active_seats(session, other)) == 1
        assert session.scalar(select(func.count()).select_from(MembershipSeat)) == 7


def test_expired_membership_seat_can_be_replaced(db):
    expired = grant(db, "student", access_until=NOW + timedelta(seconds=1))
    target = grant(db)
    with db() as session:
        m.add_seat(session, target, 1, ADMIN, at=NOW + timedelta(days=1))
        session.commit()
        assert not m.active_seats(session, expired)
        assert len(m.active_seats(session, target)) == 1


def test_concurrent_sixth_seat_is_rejected(db):
    member = grant(db)
    with db() as session:
        for i in range(1, 5):
            m.add_seat(session, member, i, ADMIN)
        session.commit()
    def add(i):
        with db() as session:
            try:
                m.add_seat(session, member, i, ADMIN)
                session.commit()
                return True
            except (ValueError, IntegrityError):
                session.rollback()
                return False
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(add, (5, 6)))
    assert sorted(results) == [False, True]


def test_connected_picker_and_server_reauthorization(db):
    member = grant(db)
    with db() as session:
        for i, role, status in ((1, "verifier", "accepted"), (2, "band_director", "accepted"),
                                (3, "verifier", "pending"), (4, "verifier", "rejected"), (5, "band_director", "disconnected")):
            session.add(StudentVerifierConnection(profile_id=i, verifier_id=1, role=role, status=status))
        session.commit()
        actor = m.Actor("adult", 1)
        assert [row["profile_id"] for row in m.connected_students(session, actor)] == [1, 2]
        m.add_seat(session, member, 1, actor)
        m.add_seat(session, member, 2, actor)
        session.commit()
        for id in (3, 4, 5, 6):
            with pytest.raises(LookupError):
                m.add_seat(session, member, id, actor)
            session.rollback()
        with pytest.raises(LookupError):
            m.add_seat(session, member, 8, m.Actor("adult", 2))
        session.rollback()
        with pytest.raises(LookupError):
            m.remove_seat(session, member, m.active_seats(session, member)[0].id, m.Actor("adult", 2))


def test_inactive_and_disconnected_students_are_removed_from_picker(db):
    with db() as session:
        session.add_all([StudentVerifierConnection(profile_id=i, verifier_id=1, role=role, status="accepted")
                         for i, role in ((1, "verifier"), (2, "band_director"))])
        session.commit()
        assert len(m.connected_students(session, m.Actor("adult", 1))) == 2
        session.get(WoodchuckProfile, 1).status = "deleted"
        session.scalar(select(StudentVerifierConnection).where(StudentVerifierConnection.profile_id == 2)).status = "disconnected"
        session.commit()
        assert m.connected_students(session, m.Actor("adult", 1)) == []


def test_picker_dedupes_overlapping_helper_results(db, monkeypatch):
    row = {"profile_id": 1, "display_name": "One"}
    monkeypatch.setattr(m, "accepted_active_verifier_students", lambda *a, **k: [row])
    monkeypatch.setattr(m, "band_director_students", lambda *a, **k: [row])
    with db() as session:
        assert m.connected_students(session, m.Actor("adult", 1)) == [row]


def test_invitations_hash_claim_once_without_email_identity(db):
    member = grant(db)
    with db() as session:
        invitation, token = m.invite_student(session, member, "different-email@example.test", m.Actor("adult", 1))
        assert invitation.token_hash != token and len(invitation.token_hash) == 64
        session.commit()
        seat = m.claim_invitation(session, token, m.Actor("student", 7))
        session.commit()
        assert seat.profile_id == 7 and m.student_has_full_access(session, 7)
        assert session.get(MembershipSeatInvitation, invitation.id).status == "accepted"
        with pytest.raises(ValueError):
            m.claim_invitation(session, token, m.Actor("student", 8))
        session.rollback()
        assert "invitation_claimed" in set(session.scalars(select(MembershipAuditEvent.action)))


@pytest.mark.parametrize("failure", ["expired", "cancelled", "revoked", "full", "already_seated", "adult", "forged"])
def test_invitation_rejections(db, failure):
    member = grant(db)
    with db() as session:
        invitation, token = m.invite_student(session, member, "invite@example.test", m.Actor("adult", 1))
        session.commit()
        at = NOW
        if failure == "expired":
            at += timedelta(days=8)
        elif failure == "cancelled":
            m.cancel_invitation(session, member, invitation.id, m.Actor("adult", 1))
        elif failure == "revoked":
            m.revoke_complimentary_membership(session, member, ADMIN)
        elif failure == "full":
            for i in range(1, 6):
                m.add_seat(session, member, i, ADMIN)
        elif failure == "already_seated":
            other = m.create_complimentary_membership(session, m.Actor("adult", 2), ADMIN)
            m.add_seat(session, other.id, 7, ADMIN)
        session.commit()
        with pytest.raises((ValueError, LookupError, PermissionError)):
            m.claim_invitation(session, "forged" if failure == "forged" else token,
                               m.Actor("adult" if failure == "adult" else "student", 7), at=at)


def test_invitation_capacity_bound(db):
    member = grant(db)
    with db() as session:
        for i in range(5):
            m.invite_student(session, member, f"invite{i}@example.test", m.Actor("adult", 1))
        with pytest.raises(ValueError):
            m.invite_student(session, member, "sixth@example.test", m.Actor("adult", 1))


def test_feature_policy(db, monkeypatch):
    monkeypatch.setattr(feature_access, "FEATURES", {
        "open": feature_access.Feature(True, "open"), "full": feature_access.Feature(True, "full"),
        "off": feature_access.Feature(False, "open")})
    grant(db, "student")
    with db() as session:
        for profile in (1, 2):
            assert feature_access.can_use_feature(session, profile, "open")
            assert not feature_access.can_use_feature(session, profile, "off")
            assert not feature_access.can_use_feature(session, profile, "unknown")
        assert feature_access.can_use_feature(session, 1, "full")
        assert not feature_access.can_use_feature(session, 2, "full")


def test_admin_gate_grant_remove_and_revoke(db):
    admin = client()
    assert admin.get("/admin/membership").status_code == 403
    csrf = page_csrf(admin, "/admin/login")
    assert admin.post("/admin/login", data={"csrf": csrf, "token": "wrong"}).status_code == 403
    assert admin.post("/admin/login", data={"csrf": csrf, "token": "test-site-admin"}).status_code == 200
    csrf = page_csrf(admin, "/admin/membership")
    response = admin.post("/admin/membership", data={"csrf": csrf, "action": "grant", "owner_kind": "student", "owner_id": 1})
    assert response.status_code == 200
    with db() as session:
        member = session.scalar(select(Membership))
        id = member.id
        assert m.student_has_full_access(session, 1)
    for profile in range(2, 6):
        assert admin.post("/admin/membership", data={"csrf": csrf, "action": "add", "membership_id": id, "profile_id": profile}).status_code == 200
    assert admin.post("/admin/membership", data={"csrf": csrf, "action": "add", "membership_id": id, "profile_id": 6}).status_code == 409
    assert admin.post("/admin/membership", data={"csrf": csrf, "action": "revoke", "membership_id": id}).status_code == 200
    with db() as session:
        assert not m.student_has_full_access(session, 1)


def test_site_admin_independent_of_contest_admin_and_token_rotation(db, monkeypatch):
    monkeypatch.setenv("CONTEST_ADMIN_TOKEN", "contest-only")
    admin = client()
    csrf = page_csrf(admin, "/admin/login")
    assert admin.get("/admin/membership", headers={"X-Contest-Admin-Token": "contest-only"}).status_code == 403
    assert admin.post("/admin/login", data={"csrf": csrf, "token": "contest-only"}).status_code == 403
    assert admin.post("/admin/login", data={"csrf": csrf, "token": "test-site-admin"}).status_code == 200
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "rotated")
    assert admin.get("/admin/membership").status_code == 403


def test_admin_search_and_owner_views_use_names(db):
    member = grant(db)
    with db() as session:
        m.add_seat(session, member, 2, ADMIN)
        session.commit()
    admin = client()
    csrf = page_csrf(admin, "/admin/login")
    admin.post("/admin/login", data={"csrf": csrf, "token": "test-site-admin"})
    assert "Site Admin — Memberships" in admin.get("/admin/membership").text
    assert 'class="ww-dashboard-header"' in admin.get("/admin/membership").text
    assert "Sign out of Site Admin" in admin.get("/admin/membership").text
    page = admin.get("/admin/membership?q=Adult&membership_id=" + str(member))
    assert page.status_code == 200
    assert "Adult 1" in page.text and "Inspect membership" in page.text
    assert "Remove student" in page.text
    assert "End Subscription" in page.text
    assert "Ending this subscription will immediately remove Full Access from every student on this membership." in page.text
    assert "End this subscription? All students on this membership will immediately lose Full Access." in page.text
    page = admin.get("/admin/membership?q=Student&membership_id=" + str(member))
    assert "Student 1" in page.text and "Add to selected membership" in page.text
    assert admin.get("/admin/membership?membership_id=999").status_code == 404


def test_owner_cannot_manage_other_membership_seats_or_invitations(db):
    first, second = grant(db), grant(db, owner_id=2)
    with db() as session:
        seat = m.add_seat(session, second, 3, ADMIN)
        invitation, _ = m.invite_student(session, second, "other@example.test", m.Actor("adult", 2))
        session.commit()
        seat_id, invite_id = seat.id, invitation.id
    owner = client("adult")
    csrf = page_csrf(owner)
    for action, key, value in (("remove", "seat_id", seat_id), ("cancel_invitation", "invitation_id", invite_id)):
        assert owner.post("/membership/actions", data={"csrf": csrf, "as_account": "adult", "membership_id": first,
            "action": action, key: value}).status_code == 404


def test_owner_routes_privacy_csrf_and_actor_selection(db):
    member = grant(db)
    with db() as session:
        m.add_seat(session, member, 1, ADMIN)
        session.commit()
    student = client("student")
    page = student.get("/membership")
    assert "Full Access" in page.text
    assert "payer1@" not in page.text and "Outstanding invitations" not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "Which account" in client(both=True).get("/membership").text
    attacker = client("adult", 2)
    csrf = page_csrf(attacker)
    assert attacker.post("/membership/actions", data={"csrf": csrf, "as_account": "adult", "action": "invite", "membership_id": member,
        "email": "x@example.test"}).status_code == 409
    assert attacker.post("/membership/actions", data={"action": "remove"}).status_code == 403
    assert attacker.get("/membership?as_account=student").status_code == 403


def test_student_owner_seat_is_labeled_and_cannot_be_removed(db):
    member = grant(db, kind="student")
    student = client("student")
    page = student.get("/membership")
    assert "✓ Full Access" in page.text
    assert "Your account has Full Access." in page.text
    assert "Members: 1 of 5" in page.text
    assert "Student 1 — You" in page.text
    assert '<h1>Membership</h1>' in page.text
    assert 'class="ww-dashboard-header"' in page.text
    assert "Complimentary Full membership · Active" in page.text
    assert 'name="woodchuck_id"' in page.text and "Woodchuck ID" in page.text
    assert "Invite student" not in page.text and "Outstanding invitations" not in page.text
    assert page.text.count("name=\"action\" value=\"remove\"") == 0
    csrf = page_csrf(student)
    with db() as session:
        seat = m.active_seats(session, member)[0]
    response = student.post("/membership/actions", data={"csrf": csrf, "as_account": "student",
        "membership_id": member, "action": "remove", "seat_id": seat.id})
    assert response.status_code == 409


def test_owner_assigns_existing_student_immediately_by_woodchuck_id(db, monkeypatch):
    member = grant(db, kind="student")
    student = client("student")
    monkeypatch.setattr(EmailService, "send_membership_invitation",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("email sent")))
    csrf = page_csrf(student)
    response = student.post("/membership/actions", data={"csrf": csrf, "as_account": "student",
        "membership_id": member, "action": "add", "woodchuck_id": " wc-member2 "})
    assert response.status_code == 200
    with db() as session:
        assert len(m.active_seats(session, member)) == 2
        assert m.student_has_full_access(session, 2)
        assert session.scalar(select(func.count()).select_from(MembershipSeatInvitation)) == 0
    page = student.get("/membership")
    assert "Members: 2 of 5" in page.text
    assert "Student 2" in page.text


def test_owner_assignment_rejects_unknown_duplicate_and_other_membership_student(db):
    member = grant(db, kind="student")
    other = grant(db, kind="adult", owner_id=2)
    with db() as session:
        m.add_seat(session, other, 3, ADMIN)
        session.commit()
    student = client("student")
    csrf = page_csrf(student)
    base = {"csrf": csrf, "as_account": "student", "membership_id": member, "action": "add"}
    assert student.post("/membership/actions", data={**base, "woodchuck_id": "missing"}).status_code == 404
    assert student.post("/membership/actions", data={**base, "woodchuck_id": "WC-MEMBER1"}).status_code == 409
    assert student.post("/membership/actions", data={**base, "woodchuck_id": "WC-MEMBER3"}).status_code == 409


def test_email_invitation_route_and_claim(db):
    member = grant(db)
    with db() as session:
        _, token = m.invite_student(session, member, "second@example.test", m.Actor("adult", 1))
        session.commit()
    student = client("student", 3)
    path = f"/membership/invitations/{token}"
    csrf = page_csrf(student, path)
    assert student.post(path, data={"csrf": csrf}).status_code == 200
    with db() as session:
        assert m.student_has_full_access(session, 3)


def test_config_pricing_and_disabled_checkout(db, monkeypatch):
    config = BillingConfig.from_environment()
    assert config == BillingConfig(False, False, False, False)
    assert [p.amount_cents for p in PLANS.values()] == [700, 4900, 3000]
    assert len(available_plans(config)) == 2
    with pytest.raises(ValueError):
        new_subscription_plan("friendship_annual_30", config)
    student = client("student")
    csrf = page_csrf(student)
    for provider in ("paypal", "stripe", "unknown"):
        assert student.post("/membership/checkout", data={"csrf": csrf, "provider": provider, "plan_code": "full_monthly_7"}).status_code == 503
        assert student.post(f"/membership/webhooks/{provider}", content=b"{}").status_code == 503
    assert student.post("/membership/checkout", data={"csrf": csrf, "provider": "stripe", "plan_code": "full_monthly_7", "amount": 1}).status_code == 400
    assert student.post("/membership/checkout", data={"csrf": csrf, "provider": "stripe", "plan_code": "friendship_annual_30"}).status_code == 400
    for provider in ("paypal", "stripe"):
        monkeypatch.setenv(f"{provider.upper()}_BILLING_ENABLED", "true")
        monkeypatch.setenv("PUBLIC_SUBSCRIPTIONS_ENABLED", "true")
        assert student.post("/membership/checkout", data={"csrf": csrf, "provider": provider, "plan_code": "full_annual_49"}).status_code == 503
        assert student.post(f"/membership/webhooks/{provider}", content=b"{}").status_code == 503
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Membership)) == 0


class FakeProvider:
    def __init__(self, event=None):
        self.event = event
        self.checkouts = []
    def create_checkout(self, **kwargs):
        self.checkouts.append(kwargs)
        return providers.CheckoutResult("https://billing.invalid/test-checkout", kwargs["idempotency_key"])
    def cancel_subscription(self, **kwargs):
        self.cancelled = kwargs
    def create_portal_session(self, **kwargs):
        return "https://billing.invalid/test-portal"
    def verify_and_parse_webhook(self, body, headers):
        if headers.get("x-test-signature") != "valid":
            raise ValueError("Bad test signature")
        return self.event


def test_fake_provider_server_pricing(db, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setitem(providers.PROVIDERS, "stripe", fake)
    url = providers.checkout(db, m.Actor("student", 1), "stripe", "full_annual_49",
        config=BillingConfig(True, False, True, False, 600))
    assert url.endswith("test-checkout")
    assert fake.checkouts[0]["plan"].amount_cents == 4900
    with db() as session:
        assert session.scalar(select(func.count()).select_from(Membership)) == 0


def test_provider_event_once_paid_through_and_immutable_launch_plan(db, monkeypatch):
    member = grant(db, "student")
    with db() as session:
        membership = session.get(Membership, member)
        membership.source, membership.plan_code = "stripe", "friendship_annual_30"
        membership.access_until = NOW + timedelta(days=365)
        session.add(ProviderSubscription(membership_id=member, provider="stripe", external_subscription_id="sub-a",
            plan_code="friendship_annual_30", amount_cents=3000, currency="USD", interval="year", provider_status="active"))
        session.commit()
    event = providers.SubscriptionEvent("evt-1", "sub-a", NOW, "cancelled", NOW, NOW + timedelta(days=365), True)
    fake = FakeProvider(event)
    monkeypatch.setitem(providers.PROVIDERS, "stripe", fake)
    config = BillingConfig(False, False, True, False)
    with db() as session:
        first, fresh = providers.process_webhook(db, "stripe", b"signed-body", {"x-test-signature": "valid"}, config=config)
        assert fresh and first.status == "processed"
        again, fresh = providers.process_webhook(db, "stripe", b"signed-body", {"x-test-signature": "valid"}, config=config)
        assert not fresh and again.id == first.id
        assert session.scalar(select(func.count()).select_from(BillingProviderEvent)) == 1
        assert session.scalar(select(func.count()).select_from(MembershipAuditEvent).where(MembershipAuditEvent.action == "provider_status_changed")) == 1
        assert m.student_has_full_access(session, 1)
        assert not m.student_has_full_access(session, 1, NOW + timedelta(days=365))
        sub = session.scalar(select(ProviderSubscription))
        assert (sub.plan_code, sub.amount_cents) == ("friendship_annual_30", 3000)
        # Lookup and webhook envelopes may differ while verified facts agree.
        same, fresh = providers.process_webhook(db, "stripe", b"different-body", {"x-test-signature": "valid"}, config=config)
        assert not fresh and same.id == first.id and same.payload_hash == first.payload_hash
        from dataclasses import replace
        fake.event = replace(event, provider_status="active")
        with pytest.raises(ValueError, match="reused"):
            providers.process_webhook(db, "stripe", b"different-body", {"x-test-signature": "valid"}, config=config)


def test_unauthenticated_membership_redirect_and_student_key_link(tmp_path, monkeypatch):
    response = TestClient(main.app).get("/membership", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    template = Path("templates/base.html").read_text()
    assert 'class="nav-pill{% if active_nav == \'membership\' %} active{% endif %}"' not in template
    assert 'href="/membership?as_account=student"' in Path("templates/store.html").read_text()


def test_terminated_subscription_cannot_reactivate_or_inherit_price(db, monkeypatch):
    member = grant(db, "student")
    with db() as session:
        membership = session.get(Membership, member)
        membership.source, membership.plan_code = "stripe", "friendship_annual_30"
        membership.access_until = NOW + timedelta(days=1)
        session.add(ProviderSubscription(membership_id=member, provider="stripe", external_subscription_id="old-sub",
            plan_code="friendship_annual_30", amount_cents=3000, currency="USD", interval="year", provider_status="active"))
        session.commit()
    fake = FakeProvider(providers.SubscriptionEvent("ended", "old-sub", NOW, "cancelled", NOW,
                        NOW + timedelta(days=1), True, True))
    monkeypatch.setitem(providers.PROVIDERS, "stripe", fake)
    config = BillingConfig(False, False, True, False)
    with db() as session:
        providers.process_webhook(db, "stripe", b"ended", {"x-test-signature": "valid"}, config=config)
        assert m.student_has_full_access(session, 1)
        assert not m.student_has_full_access(session, 1, NOW + timedelta(days=1))
        fake.event = providers.SubscriptionEvent("new-event", "old-sub", NOW + timedelta(days=2), "active",
                        NOW + timedelta(days=2), NOW + timedelta(days=367))
        event, _ = providers.process_webhook(db, "stripe", b"new-event", {"x-test-signature": "valid"}, config=config)
        assert event.status == "failed" and event.error_code == "subscription_terminated"
        assert not m.student_has_full_access(session, 1, NOW + timedelta(days=2))
        with pytest.raises(ValueError):
            new_subscription_plan("friendship_annual_30", config)


def test_concurrent_cross_membership_assignment(db):
    members = [grant(db, owner_id=owner) for owner in (1, 2)]
    def add(member):
        with db() as session:
            try:
                m.add_seat(session, member, 1, ADMIN)
                session.commit()
                return True
            except (ValueError, IntegrityError):
                session.rollback()
                return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(add, members)) == [False, True]
