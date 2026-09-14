"""Phase A1 uses only explicit test durations, fake adapters and disposable DBs."""
from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
import pytest
from tests.test_memberships import db, NOW, FakeProvider, grant, client, page_csrf
from app import billing_providers as b, memberships as m
from app.billing_config import BillingConfig
from app.models import (CheckoutAttempt, BillingAccount, Membership, ProviderSubscription,
                        MembershipSeat, MembershipAuditEvent, BillingProviderEvent,
                        BillingEventApplication, BillingPaymentEffect)

CONFIG = BillingConfig(True, False, True, True, 600)  # Test only, no product duration.


def attempt(db, actor=m.Actor("student", 1), plan="full_annual_49", **kwargs):
    with db() as session:
        row = b.authorize_checkout(session, actor, "stripe", plan, config=CONFIG, **kwargs)
        session.commit()
        return row


def payment(ref=None, **kwargs):
    event = b.SubscriptionEvent("evt-1", "sub-1", NOW, "active", NOW, NOW + timedelta(days=365),
        checkout_reference=ref, paid_through=NOW + timedelta(days=365), payment_reference="payment-1")
    return replace(event, **kwargs)


def deliver(db, monkeypatch, event, body=None):
    monkeypatch.setitem(b.PROVIDERS, "stripe", FakeProvider(event))
    return b.process_webhook(db, "stripe", (body or event.external_event_id).encode(),
                            {"x-test-signature": "valid"}, config=replace(CONFIG, launch_sale_enabled=False))


def counts(db):
    with db() as s:
        return tuple(s.scalar(select(func.count()).select_from(model)) for model in
                     (Membership, ProviderSubscription, MembershipSeat, BillingPaymentEffect))


def test_attempt_snapshot_retry_new_and_ownership(db):
    row = attempt(db)
    assert len(row.reference) >= 40 and "student" not in row.reference
    assert (row.plan_code, row.amount_cents, row.currency, row.interval) == ("full_annual_49", 4900, "USD", "year")
    assert row.expires_at == NOW + timedelta(seconds=600)
    assert attempt(db).id == row.id
    assert attempt(db, reference=row.reference).id == row.id
    assert attempt(db, new_attempt=True).id != row.id
    with pytest.raises(ValueError, match="not found"):
        attempt(db, actor=m.Actor("adult", 1), reference=row.reference)
    assert counts(db) == (0, 0, 0, 0)


def test_concurrent_double_submit_reuses_attempt(db):
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: attempt(db).id, range(2)))
    assert ids[0] == ids[1]


@pytest.mark.parametrize("kind,seats", [("student", 1), ("adult", 0)])
def test_verified_initial_payment_and_replay(db, monkeypatch, kind, seats):
    row = attempt(db, actor=m.Actor(kind, 1))
    event = payment(row.reference)
    record, fresh = deliver(db, monkeypatch, event)
    assert fresh and record.status == "processed"
    assert counts(db) == (1, 1, seats, 1)
    again, fresh = deliver(db, monkeypatch, event)
    assert not fresh and again.id == record.id and counts(db) == (1, 1, seats, 1)
    with db() as s:
        sub = s.scalar(select(ProviderSubscription))
        assert s.get(CheckoutAttempt, row.id).subscription_id == sub.id
        assert (sub.amount_cents, sub.plan_code) == (4900, "full_annual_49")
        assert s.get(CheckoutAttempt, row.id).status == "completed"
        assert m.student_has_full_access(s, 1) == (kind == "student")


def test_sale_authorization_survives_sale_closure(db, monkeypatch):
    row = attempt(db, plan="friendship_annual_30")
    assert deliver(db, monkeypatch, payment(row.reference))[0].status == "processed"
    with db() as s:
        assert s.scalar(select(ProviderSubscription.amount_cents)) == 3000
        with pytest.raises(ValueError, match="not available"):
            b.authorize_checkout(s, m.Actor("adult", 1), "stripe", "friendship_annual_30",
                                 config=replace(CONFIG, launch_sale_enabled=False))


def test_expired_sale_preserved_for_review_without_access(db, monkeypatch):
    row = attempt(db, plan="friendship_annual_30")
    monkeypatch.setattr(b, "clock", lambda: NOW + timedelta(seconds=601))
    event = payment(row.reference, occurred_at=NOW + timedelta(seconds=601))
    record, _ = deliver(db, monkeypatch, event)
    assert record.status == "failed" and record.error_code == "checkout_authorization_expired"
    assert counts(db) == (0, 0, 0, 0)
    with db() as s:
        assert s.scalar(select(BillingEventApplication)).status == "recoverable"


@pytest.mark.parametrize("status", ["active", "pending", "past_due", "canceled"])
def test_status_is_not_initial_payment(db, monkeypatch, status):
    row = attempt(db)
    event = payment(row.reference, provider_status=status, paid_through=None, payment_reference=None)
    record, _ = deliver(db, monkeypatch, event)
    assert record.status == "failed" and counts(db) == (0, 0, 0, 0)
    assert record.error_code == "awaiting_paid_entitlement"


def test_paid_through_monotonic_and_effect_deduplication(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference))
    status = payment(row.reference, external_event_id="status", occurred_at=NOW + timedelta(seconds=10),
                     period_end=NOW + timedelta(days=800), paid_through=None, payment_reference=None, provider_status="past_due")
    deliver(db, monkeypatch, status)
    with db() as s:
        assert m.utc(s.scalar(select(Membership.access_until))) == NOW + timedelta(days=365)
    # A payment older than the latest lifecycle event still advances entitlement.
    renewal = payment(row.reference, external_event_id="renewal", occurred_at=NOW + timedelta(seconds=5),
                      paid_through=NOW + timedelta(days=730), payment_reference="payment-2")
    assert deliver(db, monkeypatch, renewal)[0].status == "processed"
    deliver(db, monkeypatch, replace(renewal, external_event_id="same-payment-other-event"))
    deliver(db, monkeypatch, payment(row.reference, external_event_id="old-payment", payment_reference="old"))
    with db() as s:
        assert m.utc(s.scalar(select(Membership.access_until))) == NOW + timedelta(days=730)
        assert s.scalar(select(ProviderSubscription.provider_status)) == "past_due"
        assert s.scalar(select(func.count()).select_from(BillingPaymentEffect)) == 3
        assert s.scalar(select(func.count()).select_from(MembershipAuditEvent).where(
            MembershipAuditEvent.action == "payment_entitlement_confirmed")) == 3


def test_equal_time_conflict_retained_but_payment_advances(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference))
    event = payment(row.reference, external_event_id="equal", provider_status="past_due",
                    paid_through=NOW + timedelta(days=730), payment_reference="payment-2")
    record, _ = deliver(db, monkeypatch, event)
    assert record.error_code == "equal_time_lifecycle_conflict"
    deliver(db, monkeypatch, event)
    with db() as s:
        assert m.utc(s.scalar(select(Membership.access_until))) == NOW + timedelta(days=730)
        assert s.scalar(select(func.count()).select_from(BillingPaymentEffect)) == 2
        assert s.scalar(select(ProviderSubscription.provider_status)) == "active"


def test_unknown_event_then_correlated_provision_then_retry(db, monkeypatch):
    row = attempt(db)
    unknown = payment()  # Verified payment arrives before checkout correlation.
    record, _ = deliver(db, monkeypatch, unknown)
    assert record.error_code == "unknown_checkout"
    assert counts(db) == (0, 0, 0, 0)
    deliver(db, monkeypatch, payment(row.reference, external_event_id="correlated"))
    retried, fresh = deliver(db, monkeypatch, unknown)
    assert retried.status == "processed" and not fresh
    assert counts(db) == (1, 1, 1, 1)


def test_successful_payment_local_seat_conflict_retries_after_resolution(db, monkeypatch):
    row = attempt(db)
    other = grant(db, "adult")
    with db() as s:
        seat = m.add_seat(s, other, 1, m.Actor("admin"))
        s.commit()
        seat_id = seat.id
    event = payment(row.reference)
    record, _ = deliver(db, monkeypatch, event)
    assert record.status == "failed" and record.processed_at is None
    assert counts(db) == (1, 0, 1, 0)
    with pytest.raises(ValueError, match="processing review"):
        attempt(db, new_attempt=True)
    with db() as s:
        assert s.get(CheckoutAttempt, row.id).status == "recoverable"
        assert s.scalar(select(BillingEventApplication)).facts["payment_reference"] == "payment-1"
        m.remove_seat(s, other, seat_id, m.Actor("admin"))
        s.commit()
    # Timely received payment retains its authorization during processing recovery.
    monkeypatch.setattr(b, "clock", lambda: NOW + timedelta(seconds=700))
    assert deliver(db, monkeypatch, event)[0].status == "processed"
    deliver(db, monkeypatch, event)
    assert counts(db) == (2, 1, 2, 1)  # Includes the preserved removed seat.


def test_unexpected_database_failure_keeps_committed_inbox(db, monkeypatch):
    row = attempt(db)
    original = b.apply_verified_event
    def fail(*args):
        raise OperationalError("test", {}, Exception("temporary failure"))
    monkeypatch.setattr(b, "apply_verified_event", fail)
    with pytest.raises(OperationalError):
        deliver(db, monkeypatch, payment(row.reference))
    with db() as s:
        assert s.scalar(select(BillingProviderEvent)).status == "received"
        assert s.scalar(select(BillingEventApplication)).status == "pending"
    with pytest.raises(ValueError, match="processing review"):
        attempt(db, new_attempt=True)
    monkeypatch.setattr(b, "apply_verified_event", original)
    assert deliver(db, monkeypatch, payment(row.reference))[0].status == "processed"
    assert counts(db) == (1, 1, 1, 1)


def test_checkout_route_no_csrf_transport_and_no_open_transaction(db, monkeypatch):
    class CheckingProvider(FakeProvider):
        def create_checkout(self, **kwargs):
            # Separate DB connection can see and write the committed attempt.
            with db() as s:
                row = s.scalar(select(CheckoutAttempt))
                assert row.reference == kwargs["idempotency_key"]
                row.updated_at = NOW
                s.commit()
            return super().create_checkout(**kwargs)
    fake = CheckingProvider()
    monkeypatch.setitem(b.PROVIDERS, "stripe", fake)
    monkeypatch.setattr(BillingConfig, "from_environment", classmethod(lambda cls: CONFIG))
    user = client("student")
    csrf = page_csrf(user)
    fields = {"csrf": csrf, "provider": "stripe", "plan_code": "full_monthly_7"}
    assert user.post("/membership/checkout", data=fields, follow_redirects=False).status_code == 303
    assert user.post("/membership/checkout", data=fields, follow_redirects=False).status_code == 303
    assert fake.checkouts[0]["idempotency_key"] == fake.checkouts[1]["idempotency_key"]
    assert csrf not in repr(fake.checkouts) and "billing_account_id" not in fake.checkouts[0]
    for key in ("amount", "currency", "billing_account_id", "paid_through"):
        assert user.post("/membership/checkout", data={**fields, key: "1"}).status_code == 400
    assert user.post("/membership/checkout", data={**fields, "csrf": "wrong"}).status_code == 403


def test_no_default_hold_or_new_sale_reservation(db):
    with db() as s:
        with pytest.raises(b.BillingUnavailable, match="validity"):
            b.authorize_checkout(s, m.Actor("student", 1), "stripe", "full_annual_49", config=replace(CONFIG, checkout_validity_seconds=None))
        s.rollback()
        assert s.scalar(select(func.count()).select_from(CheckoutAttempt)) == 0


def test_concurrent_same_payment_provisions_once(db, monkeypatch):
    row = attempt(db)
    event = payment(row.reference)
    monkeypatch.setitem(b.PROVIDERS, "stripe", FakeProvider(event))
    def process(_):
        return b.process_webhook(db, "stripe", b"same", {"x-test-signature": "valid"}, config=CONFIG)[0].status
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(process, range(2))) == ["processed", "processed"]
    assert counts(db) == (1, 1, 1, 1)


def test_distinct_events_same_payment_provision_once(db, monkeypatch):
    row = attempt(db)
    with db() as s:
        first, _ = b.receive_verified_event(s, "stripe", payment(row.reference), payload_hash="first")
        second, _ = b.receive_verified_event(s, "stripe", payment(row.reference, external_event_id="second"), payload_hash="second")
        ids = [first.id, second.id]
        s.commit()
    def process(id):
        with db() as s:
            status = b.apply_verified_event(s, id).status
            s.commit()
            return status
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(process, ids)) == ["processed", "processed"]
    assert counts(db) == (1, 1, 1, 1)


def test_second_subscription_cannot_consume_same_attempt(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference))
    event = payment(row.reference, external_event_id="second-sub", external_subscription_id="sub-2")
    record, _ = deliver(db, monkeypatch, event)
    assert record.error_code == "checkout_already_consumed"
    assert counts(db) == (1, 1, 1, 1)


def test_same_payment_reference_with_conflicting_facts_stays_recoverable(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference))
    conflicting = payment(row.reference, external_event_id="conflicting", paid_through=NOW + timedelta(days=800))
    assert deliver(db, monkeypatch, conflicting)[0].error_code == "payment_reference_conflict"
    with db() as s:
        assert m.utc(s.scalar(select(Membership.access_until))) == NOW + timedelta(days=365)


def test_partial_provisioning_constraint_failure_rolls_back_history(db, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    row = attempt(db)
    original = m._add_seat
    def fail(*args):
        original(*args)
        raise IntegrityError("test", {}, Exception("simulated constraint failure after seat creation"))
    monkeypatch.setattr(m, "_add_seat", fail)
    record, _ = deliver(db, monkeypatch, payment(row.reference))
    assert record.status == "failed"
    assert counts(db) == (0, 0, 0, 0)
    with db() as s:
        assert s.scalar(select(func.count()).select_from(MembershipAuditEvent)) == 0
        assert s.get(CheckoutAttempt, row.id).subscription_id is None
    monkeypatch.setattr(m, "_add_seat", original)
    assert deliver(db, monkeypatch, payment(row.reference))[0].status == "processed"
    assert counts(db) == (1, 1, 1, 1)


def test_explicit_reference_retry_after_sale_closes_and_new_attempt_after_expiry(db, monkeypatch):
    row = attempt(db, plan="friendship_annual_30")
    with db() as s:
        retry = b.authorize_checkout(s, m.Actor("student", 1), "stripe", "friendship_annual_30",
             config=replace(CONFIG, launch_sale_enabled=False), reference=row.reference)
        assert retry.id == row.id
    monkeypatch.setattr(b, "clock", lambda: NOW + timedelta(seconds=601))
    with pytest.raises(ValueError, match="cannot be started"):
        attempt(db, plan="friendship_annual_30", reference=row.reference)
    assert attempt(db).id != row.id


@pytest.mark.parametrize("value", [None, "", "yes", "-1", "0", "99999999999999999"])
def test_duration_environment_fails_closed_without_affecting_flags(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CHECKOUT_VALIDITY_SECONDS", raising=False)
    else:
        monkeypatch.setenv("CHECKOUT_VALIDITY_SECONDS", value)
    assert BillingConfig.from_environment().checkout_validity_seconds is None


def test_unknown_event_recovery_does_not_mutate_verifier_or_seat_privacy(db, monkeypatch):
    # Existing membership regression tests cover owner-only render/actions. This
    # route must return only processing acknowledgement, never payer/seat details.
    row = attempt(db, actor=m.Actor("adult", 1))
    monkeypatch.setitem(b.PROVIDERS, "stripe", FakeProvider(payment(row.reference)))
    monkeypatch.setattr(BillingConfig, "from_environment", classmethod(lambda cls: CONFIG))
    result = client().post("/membership/webhooks/stripe", content=b"signed", headers={"x-test-signature": "valid"})
    assert result.json() == {"received": True, "duplicate": False, "processed": True}
    assert client().post("/membership/webhooks/stripe", content=b"signed").status_code == 400


def test_remote_failure_retains_durable_retry_reference(db, monkeypatch):
    class UncertainProvider(FakeProvider):
        def create_checkout(self, **kwargs):
            self.checkouts.append(kwargs)
            if len(self.checkouts) == 1:
                raise RuntimeError("transport outcome unknown")
            return b.CheckoutResult("https://billing.invalid/retry", "remote-1")
    fake = UncertainProvider()
    monkeypatch.setitem(b.PROVIDERS, "stripe", fake)
    with pytest.raises(RuntimeError):
        b.checkout(db, m.Actor("student", 1), "stripe", "full_annual_49", config=CONFIG)
    with db() as s:
        row = s.scalar(select(CheckoutAttempt))
        assert row.status == "pending" and row.provider_checkout_id is None
        ref = row.reference
    b.checkout(db, m.Actor("student", 1), "stripe", "full_annual_49", config=CONFIG, reference=ref)
    assert fake.checkouts[0]["idempotency_key"] == fake.checkouts[1]["idempotency_key"]
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize("changes", [
    {"payment_reference": None}, {"paid_through": None}, {"payment_reference": ""},
    {"external_event_id": ""}, {"occurred_at": NOW.replace(tzinfo=None)},
    {"terminal": "false"}, {"period_end": NOW},
])
def test_invalid_normalized_facts_never_enter_inbox(db, changes):
    with db() as s:
        with pytest.raises(ValueError):
            b.receive_verified_event(s, "stripe", payment(**changes), payload_hash="test")
        assert s.scalar(select(func.count()).select_from(BillingProviderEvent)) == 0


def test_same_event_cannot_change_verified_facts(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference), body="same-body")
    with pytest.raises(ValueError, match="reused"):
        deliver(db, monkeypatch, payment(row.reference, paid_through=NOW + timedelta(days=800)), body="same-body")
    assert counts(db) == (1, 1, 1, 1)


def test_existing_subscription_does_not_lock_owner_before_membership(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference))
    def owner_lock(*args, **kwargs):
        pytest.fail("Renewals must not take the owner identity lock before membership/seat management")
    monkeypatch.setattr(b, "billing_account", owner_lock)
    renewal = payment(row.reference, external_event_id="renew", occurred_at=NOW + timedelta(seconds=1),
                      paid_through=NOW + timedelta(days=730), payment_reference="payment-2")
    assert deliver(db, monkeypatch, renewal)[0].status == "processed"


def test_retry_refreshes_preloaded_checkout_correlation(db, monkeypatch):
    row = attempt(db)
    with db() as retry_session:
        cached = retry_session.get(CheckoutAttempt, row.id)
        assert cached.subscription_id is None
        # Another worker completes provisioning while this session retains its
        # pre-lock checkout object. A row query alone does not refresh ORM state.
        deliver(db, monkeypatch, payment(row.reference))
        with db() as s:
            record, _ = b.receive_verified_event(s, "stripe", payment(row.reference, external_event_id="second-worker"),
                                                payload_hash="second-worker")
            event_id = record.id
            s.commit()
        assert b.apply_verified_event(retry_session, event_id).status == "processed"
        retry_session.commit()
    assert counts(db) == (1, 1, 1, 1)
