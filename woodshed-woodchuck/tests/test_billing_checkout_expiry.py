"""A5 deterministic 60-minute checkout authorization and late-payment rules."""
from dataclasses import replace
from datetime import timedelta
from sqlalchemy import select, func
import pytest
from tests.test_memberships import db, NOW, FakeProvider, client, page_csrf, grant
from tests.test_billing_hardening import payment, deliver, counts
from app import billing_providers as b, billing_recovery as recovery, memberships as m
from app.billing_config import BillingConfig, DEFAULT_CHECKOUT_VALIDITY_SECONDS
from app.models import (CheckoutAttempt, BillingEventApplication, BillingProviderEvent,
                        ProviderSubscription, Membership, MembershipAuditEvent)


CONFIG = BillingConfig(True, False, True, True)


def authorize(db, plan="full_annual_49", **kwargs):
    with db() as session:
        row = b.authorize_checkout(session, m.Actor("student", 1), "stripe", plan,
                                   config=CONFIG, **kwargs)
        session.commit()
        return row


def test_default_and_server_override_authorization_windows(db):
    assert DEFAULT_CHECKOUT_VALIDITY_SECONDS == 3600
    assert BillingConfig().checkout_validity_seconds == 3600
    row = authorize(db)
    assert row.expires_at == NOW + timedelta(minutes=60)
    with db() as session:
        custom = b.authorize_checkout(session, m.Actor("adult", 1), "stripe", "full_monthly_7",
            config=replace(CONFIG, checkout_validity_seconds=900), new_attempt=True)
        session.commit()
        assert custom.expires_at == NOW + timedelta(minutes=15)


def test_environment_duration_override_is_server_controlled(monkeypatch):
    monkeypatch.setenv("CHECKOUT_VALIDITY_SECONDS", "2700")
    assert BillingConfig.from_environment().checkout_validity_seconds == 2700


def test_browser_cannot_change_checkout_duration(db, monkeypatch):
    monkeypatch.setitem(b.PROVIDERS, "stripe", FakeProvider())
    monkeypatch.setattr(BillingConfig, "from_environment", classmethod(lambda cls: CONFIG))
    user = client("student")
    csrf = page_csrf(user)
    response = user.post("/membership/checkout", data={"csrf": csrf, "provider": "stripe",
        "plan_code": "full_annual_49", "checkout_validity_seconds": "999999"})
    assert response.status_code == 400
    with db() as session:
        assert session.scalar(select(func.count()).select_from(CheckoutAttempt)) == 0


def test_exact_expiry_boundary_and_lazy_history_normalization(db, monkeypatch):
    row = authorize(db)
    monkeypatch.setattr(b, "clock", lambda: row.expires_at - timedelta(microseconds=1))
    assert authorize(db, reference=row.reference).id == row.id
    monkeypatch.setattr(b, "clock", lambda: row.expires_at)
    with pytest.raises(ValueError, match="cannot be started"):
        authorize(db, reference=row.reference)
    replacement = authorize(db)
    assert replacement.id != row.id and replacement.reference != row.reference
    assert replacement.expires_at == row.expires_at + timedelta(minutes=60)
    with db() as session:
        old = session.get(CheckoutAttempt, row.id)
        assert old.status == "expired" and old.subscription_id is None
        assert session.scalar(select(func.count()).select_from(CheckoutAttempt)) == 2
        assert session.scalar(select(func.count()).select_from(ProviderSubscription)) == 0


def test_expired_launch_attempt_cannot_resurrect_sale(db, monkeypatch):
    row = authorize(db, "friendship_annual_30")
    monkeypatch.setattr(b, "clock", lambda: row.expires_at)
    closed = replace(CONFIG, launch_sale_enabled=False)
    with db() as session:
        with pytest.raises(ValueError, match="cannot be started"):
            b.authorize_checkout(session, m.Actor("student", 1), "stripe", "friendship_annual_30",
                                 config=closed, reference=row.reference)
    with db() as session:
        with pytest.raises(ValueError, match="not available"):
            b.authorize_checkout(session, m.Actor("student", 1), "stripe", "friendship_annual_30",
                                 config=closed, new_attempt=True)
    with db() as session:
        new = b.authorize_checkout(session, m.Actor("student", 1), "stripe", "full_annual_49",
                                   config=closed)
        session.commit()
        assert new.reference != row.reference and new.amount_cents == 4900
        assert session.get(CheckoutAttempt, row.id).status == "expired"


def test_payment_occurring_before_expiry_can_arrive_and_apply_late(db, monkeypatch):
    row = authorize(db)
    event = payment(row.reference, occurred_at=row.expires_at - timedelta(microseconds=1))
    monkeypatch.setattr(b, "clock", lambda: row.expires_at + timedelta(days=1))
    record, _ = deliver(db, monkeypatch, event)
    assert record.status == "processed" and counts(db) == (1, 1, 1, 1)
    with db() as session:
        stored = session.get(CheckoutAttempt, row.id)
        assert stored.status == "completed"
        assert b.utc(stored.created_at) == row.created_at and b.utc(stored.expires_at) == row.expires_at
        assert m.student_has_full_access(session, 1)


def test_in_time_launch_payment_keeps_snapshot_when_received_after_sale_and_expiry(db, monkeypatch):
    row = authorize(db, "friendship_annual_30")
    event = payment(row.reference, occurred_at=row.expires_at - timedelta(seconds=1))
    monkeypatch.setattr(b, "clock", lambda: row.expires_at + timedelta(days=1))
    record, _ = deliver(db, monkeypatch, event)
    assert record.status == "processed"
    with db() as session:
        subscription = session.scalar(select(ProviderSubscription))
        assert (subscription.plan_code, subscription.amount_cents) == ("friendship_annual_30", 3000)


@pytest.mark.parametrize("offset", [timedelta(), timedelta(microseconds=1)])
def test_payment_occurring_at_or_after_expiry_never_auto_applies(db, monkeypatch, offset):
    row = authorize(db, new_attempt=True)
    event = payment(row.reference, external_event_id=f"late-{offset.microseconds}",
                    external_subscription_id=f"late-sub-{offset.microseconds}",
                    payment_reference=f"late-payment-{offset.microseconds}",
                    occurred_at=row.expires_at + offset)
    monkeypatch.setattr(b, "clock", lambda: row.expires_at + timedelta(minutes=1))
    record, _ = deliver(db, monkeypatch, event)
    assert record.error_code == "checkout_authorization_expired"
    assert counts(db) == (0, 0, 0, 0)
    with db() as session:
        assert session.scalar(select(func.count()).select_from(BillingEventApplication).where(
            BillingEventApplication.status == "recoverable")) == 1


def test_unresolved_late_payment_blocks_new_checkout_but_clean_expiry_does_not(db, monkeypatch):
    clean = authorize(db)
    monkeypatch.setattr(b, "clock", lambda: clean.expires_at)
    assert authorize(db).id != clean.id
    unresolved = authorize(db, new_attempt=True)
    event = payment(unresolved.reference, external_event_id="late-unresolved",
                    external_subscription_id="late-unresolved-sub", payment_reference="late-unresolved-payment",
                    occurred_at=unresolved.expires_at)
    monkeypatch.setattr(b, "clock", lambda: unresolved.expires_at + timedelta(seconds=1))
    deliver(db, monkeypatch, event)
    with db() as session:
        with pytest.raises(ValueError, match="processing review"):
            b.authorize_checkout(session, m.Actor("student", 1), "stripe", "full_annual_49",
                                 config=CONFIG, new_attempt=True)
        assert session.get(CheckoutAttempt, unresolved.id) is not None


def test_in_time_payment_local_failure_recovers_after_expiry(db, monkeypatch):
    row = authorize(db)
    other = grant(db)
    with db() as session:
        seat = m.add_seat(session, other, 1, m.Actor("admin"))
        session.commit()
        seat_id = seat.id
    event = payment(row.reference, occurred_at=row.expires_at - timedelta(seconds=1))
    monkeypatch.setattr(b, "clock", lambda: row.expires_at + timedelta(minutes=5))
    record, _ = deliver(db, monkeypatch, event)
    assert record.status == "failed" and record.error_code == "provisioning_conflict"
    with db() as session:
        m.remove_seat(session, other, seat_id, m.Actor("admin"))
        session.commit()
    recovery.retry(db, record.id, m.Actor("admin"))
    assert counts(db) == (2, 1, 2, 1)
    with db() as session:
        assert session.scalar(select(Membership.access_until).where(Membership.source == "stripe")) is not None
        assert session.scalar(select(func.count()).select_from(MembershipAuditEvent).where(
            MembershipAuditEvent.action == "payment_entitlement_confirmed")) == 1


def test_expiration_does_not_touch_manual_membership_or_provider_state(db, monkeypatch):
    row = authorize(db)
    manual = grant(db, "adult")
    with db() as session:
        member = session.get(Membership, manual)
        before = (member.status, member.access_until)
    monkeypatch.setattr(b, "clock", lambda: row.expires_at)
    # A different owner can materialize the checkout expiry without touching membership state.
    with db() as session:
        b.expire_pending_checkouts(session, row.billing_account_id, row.expires_at)
        session.commit()
        member = session.get(Membership, manual)
        assert (member.status, member.access_until) == before
        assert session.get(CheckoutAttempt, row.id).status == "expired"
        assert session.scalar(select(func.count()).select_from(BillingProviderEvent)) == 0
