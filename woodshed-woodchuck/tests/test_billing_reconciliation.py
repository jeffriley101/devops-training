"""Authoritative test adapters; no provider credentials, transport or persistent DB."""
from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
from tests.test_memberships import db, NOW, client, grant, FakeProvider
from tests.test_billing_hardening import attempt, payment, counts, deliver
from tests.test_billing_replacement import pending, apply, history, replacement_payment
from tests.test_billing_recovery import admin_client
from app import billing_reconciliation as reconciliation, billing_recovery as recovery
from app import billing_providers as b, memberships as m
from app.billing_config import BillingConfig
from app.models import (MembershipAuditEvent, BillingProviderEvent, BillingEventApplication,
                        CheckoutAttempt, Membership, ProviderSubscription, BillingPaymentEffect)

CONFIG = BillingConfig(stripe_billing_enabled=True)  # Public buying stays off.


class EvidenceProvider(FakeProvider):
    """Injected only in tests. Callable hook simulates races and lookup failures."""
    def __init__(self, event, hook=None, **changes):
        super().__init__(event)
        self.event, self.hook, self.changes = event, hook, changes

    def inspect_evidence(self, request):
        if self.hook:
            self.hook(request)
        return b.ProviderEvidence(request=request, observed_at=b.clock(), state="found",
            external_subscription_id=self.event.external_subscription_id,
            checkout_reference=self.event.checkout_reference, provider_checkout_id=request.provider_checkout_id,
            event=self.event, plan_code="full_annual_49", amount_cents=4900, currency="USD", interval="year",
            **self.changes)


def install(monkeypatch, event, hook=None):
    provider = EvidenceProvider(event, hook)
    monkeypatch.setitem(b.PROVIDERS, "stripe", provider)
    return provider


def inspect(db, kind, id):
    return reconciliation.inspect(db, kind, id, m.Actor("admin"), config=CONFIG)


def result(db, id):
    with db() as s:
        return s.get(MembershipAuditEvent, id).details["classification"]


def audits(db):
    with db() as s:
        return list(s.scalars(select(MembershipAuditEvent).where(
            MembershipAuditEvent.action.like("billing_inspection_%")).order_by(MembershipAuditEvent.id)))


def test_checkout_evidence_ordinary_processing_idempotent_and_webhook_envelope(db, monkeypatch):
    row = attempt(db)
    event = payment(row.reference)
    install(monkeypatch, event)
    calls = []
    original = b.apply_verified_event
    def tracking(*args):
        calls.append(args[1])
        return original(*args)
    monkeypatch.setattr(b, "apply_verified_event", tracking)
    assert result(db, inspect(db, "checkout", row.id)) == "applied"
    assert result(db, inspect(db, "checkout", row.id)) == "in_sync"
    assert len(calls) == 1 and counts(db) == (1, 1, 1, 1)
    # Same adapter-verified financial facts may arrive in a different envelope.
    assert deliver(db, monkeypatch, event, body="webhook-body")[0].status == "processed"
    assert counts(db) == (1, 1, 1, 1)
    assert all(a.checkout_attempt_id == row.id for a in audits(db))
    with db() as s:
        assert m.student_has_full_access(s, 1)


def test_agreement_and_lifecycle_never_grants_payment(db, monkeypatch):
    row = attempt(db)
    event = payment(row.reference, paid_through=None, payment_reference=None)
    install(monkeypatch, event)
    assert result(db, inspect(db, "checkout", row.id)) == "correlation_needed"
    assert counts(db) == (0, 0, 0, 0)
    deliver(db, monkeypatch, payment(row.reference))
    event = replace(event, external_event_id="lifecycle", occurred_at=NOW + timedelta(seconds=1),
                    period_end=NOW + timedelta(days=730), provider_status="past_due")
    install(monkeypatch, event)
    assert result(db, inspect(db, "checkout", row.id)) == "applied"
    with db() as s:
        assert m.utc(s.scalar(select(Membership.access_until))) == NOW + timedelta(days=365)
        assert s.scalar(select(ProviderSubscription.provider_status)) == "past_due"


@pytest.mark.parametrize("state,expected", [("not_found", "object_missing"), ("unsupported", "unsupported"),
                                          ("unavailable", "provider_unavailable")])
def test_missing_unsupported_unavailable_are_audited(db, monkeypatch, state, expected):
    row = attempt(db)
    provider = install(monkeypatch, payment(row.reference))
    provider.inspect_evidence = lambda req: b.ProviderEvidence(req, NOW, state)
    assert result(db, inspect(db, "checkout", row.id)) == expected
    assert counts(db) == (0, 0, 0, 0)
    assert [a.action for a in audits(db)] == ["billing_inspection_requested", "billing_inspection_result"]


def test_provider_timeout_no_transaction_and_request_already_committed(db, monkeypatch):
    row = attempt(db)
    def lookup(req):
        assert audits(db)[0].action == "billing_inspection_requested"
        # A separate writer can lock the owner while provider lookup is running.
        with db() as s:
            m.lock_billing_account(s, row.billing_account_id)
            s.commit()
        raise TimeoutError("secret-auth-header payer@example.test")
    install(monkeypatch, payment(row.reference), lookup)
    assert result(db, inspect(db, "checkout", row.id)) == "provider_unavailable"
    assert "secret" not in str([a.details for a in audits(db)])
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize("change", [
    {"external_subscription_id": "other"}, {"checkout_reference": "different"},
    {"amount_cents": 3000}, {"plan_code": "friendship_annual_30"}, {"currency": "EUR"},
    {"interval": "month"}, {"amount_cents": True}, {"observed_at": NOW.replace(tzinfo=None)},
])
def test_conflicting_evidence_cannot_apply(db, monkeypatch, change):
    row = attempt(db)
    provider = install(monkeypatch, payment(row.reference))
    lookup = provider.inspect_evidence
    provider.inspect_evidence = lambda req: replace(lookup(req), **change)
    assert result(db, inspect(db, "checkout", row.id)) == "reconciliation_required"
    assert counts(db) == (0, 0, 0, 0)


def test_changed_existing_event_facts_cannot_be_overwritten(db, monkeypatch):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    install(monkeypatch, payment(row.reference, paid_through=NOW + timedelta(days=730)))
    assert result(db, inspect(db, "event", event_id)) == "reconciliation_required"
    with db() as s:
        assert s.scalar(select(BillingEventApplication)).facts["paid_through"] == (NOW + timedelta(days=365)).isoformat()


def test_unknown_event_can_receive_provider_corroborated_correlation(db, monkeypatch):
    row = attempt(db)
    original = pending(db, payment())
    apply(db, original)
    install(monkeypatch, payment(row.reference, external_event_id="authoritative-correlated"))
    assert result(db, inspect(db, "event", original)) == "applied"
    assert counts(db) == (1, 1, 1, 1)
    assert result(db, inspect(db, "event", original)) == "local_retry_available"
    # Original verified facts stay immutable; ordinary A3 can now retry them.
    recovery.retry(db, original, m.Actor("admin"))
    assert counts(db) == (1, 1, 1, 1)


def test_terminal_evidence_uses_normal_processing_and_financial_cases_block(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference))
    terminal = payment(row.reference, external_event_id="terminal", occurred_at=NOW + timedelta(seconds=1),
                       terminal=True, provider_status="canceled", paid_through=None, payment_reference=None)
    install(monkeypatch, terminal)
    assert result(db, inspect(db, "checkout", row.id)) == "applied"
    with db() as s:
        assert s.scalar(select(ProviderSubscription.terminated_at)) is not None
        assert m.student_has_full_access(s, 1)
    later = payment(row.reference, external_event_id="late", occurred_at=NOW + timedelta(seconds=2), payment_reference="late")
    install(monkeypatch, later)
    assert result(db, inspect(db, "checkout", row.id)) == "financial_decision_required"
    assert counts(db) == (1, 1, 1, 1)


def test_explicit_financial_issue_and_missing_correlation(db, monkeypatch):
    row = attempt(db)
    provider = install(monkeypatch, payment(row.reference))
    lookup = provider.inspect_evidence
    provider.inspect_evidence = lambda req: replace(lookup(req), financial_resolution_required=True)
    assert result(db, inspect(db, "checkout", row.id)) == "financial_decision_required"
    unknown = pending(db, payment())
    install(monkeypatch, payment())
    assert result(db, inspect(db, "event", unknown)) == "correlation_needed"


@pytest.mark.parametrize("kind", [None, "student", "adult"])
def test_unauthorized_and_seated_cannot_inspect(db, kind):
    row = attempt(db)
    grant(db, "student", owner_id=2)
    assert client(kind, 2).post(f"/admin/billing-recovery/checkout/{row.id}/inspect").status_code == 403
    assert audits(db) == []


def test_route_csrf_no_get_mutation_no_overrides_and_sanitized_output(db, monkeypatch):
    row = attempt(db)
    install(monkeypatch, payment(row.reference))
    monkeypatch.setenv("STRIPE_BILLING_ENABLED", "true")  # Injected adapter only.
    c, csrf = admin_client()
    url = f"/admin/billing-recovery/checkout/{row.id}/inspect"
    assert c.get(url).status_code == 405
    assert c.post(url, data={"csrf": "wrong"}).status_code == 403
    assert c.post(url, data={"csrf": csrf}, headers={"Origin": "https://outside.example"}).status_code == 403
    assert c.post(url, data={"csrf": csrf}, headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post(url, data={"csrf": csrf, "amount": "1"}).status_code == 400
    assert audits(db) == []
    response = c.post(url, data={"csrf": csrf})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Authoritative evidence was applied" in response.text
    for private in (row.reference, "payer1@example.test", "payment-1", "sub-1", "evt-1"):
        assert private not in response.text
    assert c.post("/admin/billing-recovery/checkout/999/inspect", data={"csrf": csrf}).status_code == 404


def test_live_adapters_and_flags_still_disabled(db):
    row = attempt(db)
    # Fixture uses the original disabled adapter for authoritative lookup.
    assert result(db, reconciliation.inspect(db, "checkout", row.id, m.Actor("admin"))) == "unsupported"
    assert not BillingConfig().public_subscriptions_enabled
    assert counts(db) == (0, 0, 0, 0)


def test_application_failure_retains_evidence_for_a3(db, monkeypatch):
    row = attempt(db)
    install(monkeypatch, payment(row.reference))
    original = b.apply_verified_event
    def fail(*args):
        original(*args)
        raise OperationalError("statement", {}, Exception("secret"))
    monkeypatch.setattr(b, "apply_verified_event", fail)
    assert result(db, inspect(db, "checkout", row.id)) == "still_recoverable"
    assert counts(db) == (0, 0, 0, 0)
    with db() as s:
        event_id = s.scalar(select(BillingProviderEvent.id))
        assert event_id is not None
    monkeypatch.setattr(b, "apply_verified_event", original)
    recovery.retry(db, event_id, m.Actor("admin"))
    assert counts(db) == (1, 1, 1, 1)


def test_result_audit_failure_preserves_requested_and_no_partial_access(db, monkeypatch):
    row = attempt(db)
    install(monkeypatch, payment(row.reference))
    original = reconciliation._audit
    def fail(session, kind, id, action, **details):
        if action == "billing_inspection_result":
            raise OperationalError("statement", {}, Exception("secret"))
        return original(session, kind, id, action, **details)
    monkeypatch.setattr(reconciliation, "_audit", fail)
    with pytest.raises(OperationalError):
        inspect(db, "checkout", row.id)
    assert audits(db)[0].action == "billing_inspection_requested"
    assert counts(db) == (0, 0, 0, 0)
    monkeypatch.setattr(reconciliation, "_audit", original)
    assert result(db, inspect(db, "checkout", row.id)) == "applied"


def test_two_reconciliations(db, monkeypatch):
    row = attempt(db)
    barrier = Barrier(2)
    install(monkeypatch, payment(row.reference), lambda req: barrier.wait(timeout=10))
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: inspect(db, "checkout", row.id), range(2)))
    assert all(result(db, id) in {"applied", "in_sync"} for id in ids)
    assert counts(db) == (1, 1, 1, 1)


@pytest.mark.parametrize("worker", ["retry", "event"])
def test_reconciliation_races_processing(db, monkeypatch, worker):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    barrier = Barrier(2)
    install(monkeypatch, payment(row.reference), lambda req: barrier.wait(timeout=10))
    def process():
        barrier.wait(timeout=10)
        return recovery.retry(db, event_id, m.Actor("admin")) if worker == "retry" else apply(db, event_id)
    with ThreadPoolExecutor(2) as pool:
        inspected = pool.submit(inspect, db, "event", event_id)
        processed = pool.submit(process)
        processed.result(timeout=15)
        assert result(db, inspected.result(timeout=15)) in {"applied", "in_sync"}
    assert counts(db) == (1, 1, 1, 1)


def test_stale_evidence_after_worker_completion(db, monkeypatch):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    install(monkeypatch, payment(row.reference), lambda req: apply(db, event_id))
    assert result(db, inspect(db, "checkout", row.id)) == "in_sync"
    assert counts(db) == (1, 1, 1, 1)


def test_reconciliation_races_replacement(db, monkeypatch):
    history(db, monkeypatch)
    row = attempt(db)
    old_id = pending(db, payment(external_event_id="old-unresolved", payment_reference="old-unresolved"))
    new_id = pending(db, replacement_payment(row))
    barrier = Barrier(2)
    install(monkeypatch, payment(external_event_id="late", payment_reference="late"), lambda req: barrier.wait(timeout=10))
    def process():
        barrier.wait(timeout=10)
        return apply(db, new_id)
    with ThreadPoolExecutor(2) as pool:
        inspected = pool.submit(inspect, db, "event", old_id)
        processed = pool.submit(process)
        assert processed.result(timeout=15) == "failed"
        assert result(db, inspected.result(timeout=15)) == "financial_decision_required"
    with db() as s:
        assert s.scalar(select(func.count()).select_from(Membership).where(Membership.status == "active")) == 0


def test_older_payment_never_reduces_newer_entitlement(db, monkeypatch):
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference, paid_through=NOW + timedelta(days=730)))
    install(monkeypatch, payment(row.reference, external_event_id="older", payment_reference="older"))
    assert result(db, inspect(db, "checkout", row.id)) == "applied"
    with db() as s:
        assert m.utc(s.scalar(select(Membership.access_until))) == NOW + timedelta(days=730)


def test_changed_lookup_correlation_requires_fresh_inspection(db, monkeypatch):
    row = attempt(db)
    def change(req):
        with db() as s:
            s.get(CheckoutAttempt, row.id).provider_checkout_id = "another-checkout"
            s.commit()
    install(monkeypatch, payment(row.reference), change)
    assert result(db, inspect(db, "checkout", row.id)) == "stale_context"
    assert counts(db) == (0, 0, 0, 0)


def test_verified_payment_seat_conflict_retained_and_normal_retry(db, monkeypatch):
    row = attempt(db)
    member_id = grant(db)
    with db() as s:
        seat = m.add_seat(s, member_id, 1, m.Actor("admin"))
        seat_id = seat.id
        s.commit()
    install(monkeypatch, payment(row.reference))
    assert result(db, inspect(db, "checkout", row.id)) == "still_recoverable"
    with db() as s:
        event_id = s.scalar(select(BillingProviderEvent.id))
        assert s.get(CheckoutAttempt, row.id).status == "recoverable"
        m.remove_seat(s, member_id, seat_id, m.Actor("admin"))
        s.commit()
    recovery.retry(db, event_id, m.Actor("admin"))
    assert counts(db) == (2, 1, 2, 1)


def test_late_initial_evidence_does_not_bypass_checkout_validity(db, monkeypatch):
    row = attempt(db)
    install(monkeypatch, payment(row.reference))
    monkeypatch.setattr(b, "clock", lambda: NOW + timedelta(seconds=601))
    assert result(db, inspect(db, "checkout", row.id)) == "still_recoverable"
    assert counts(db) == (0, 0, 0, 0)
    with db() as s:
        assert s.scalar(select(BillingEventApplication.error_code)) == "checkout_authorization_expired"


def test_launch_snapshot_survives_sale_close_without_price_override(db, monkeypatch):
    row = attempt(db, plan="friendship_annual_30")
    provider = install(monkeypatch, payment(row.reference))
    lookup = provider.inspect_evidence
    provider.inspect_evidence = lambda req: replace(lookup(req), plan_code="friendship_annual_30", amount_cents=3000)
    assert not CONFIG.launch_sale_enabled
    assert result(db, inspect(db, "checkout", row.id)) == "applied"
    with db() as s:
        assert s.scalar(select(ProviderSubscription.amount_cents)) == 3000


def test_adapter_disabled_even_if_provider_flag_enabled(db, monkeypatch):
    row = attempt(db)
    monkeypatch.setitem(b.PROVIDERS, "stripe", b.DisabledProvider("stripe"))
    assert result(db, inspect(db, "checkout", row.id)) == "unsupported"
    with pytest.raises(PermissionError):
        reconciliation.inspect(db, "checkout", row.id, m.Actor("adult", 1), config=CONFIG)
