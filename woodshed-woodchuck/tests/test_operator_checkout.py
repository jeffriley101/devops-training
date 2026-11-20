"""One shell authorization across SQLite/PostgreSQL and both simulated Stripe modes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app import billing_providers as billing, billing_reconciliation, operator_checkout as operator
from app.age_privacy import declare_age
from app.memberships import Actor, utc
from app.models import (BillingAccount, BillingEventApplication, BillingPaymentEffect, BillingProviderEvent,
                        CheckoutAttempt, Membership, MembershipAuditEvent, ProviderSubscription, TrustedVerifier)
from tests.test_billing_replacement import db, sqlite_db
from tests.test_memberships import NOW, client, grant, page_csrf
from tests.test_stripe_billing import CONFIG, adapter, configure, count, signed


@pytest.fixture
def http(adapter, monkeypatch):
    _, transport = adapter
    configure(monkeypatch, transport.mode)
    monkeypatch.setenv("PUBLIC_SUBSCRIPTIONS_ENABLED", "false")
    return transport


def approve(db, http, **overrides):
    fields = dict(adult_id=1, plan_code="full_monthly_7", mode=http.mode, operator="op-17", approval="FIN-42")
    return operator.authorize(db, **(fields | overrides))


@pytest.mark.parametrize("plan,days", [("full_monthly_7", 31), ("full_annual_49", 365)])
def test_issue_paid_invoice_and_replay(db, http, plan, days):
    receipt = approve(db, http, plan_code=plan)
    audit_id, attempt_id, expires = receipt
    assert approve(db, http, plan_code=plan) == receipt
    assert expires == NOW + timedelta(hours=1) and not http.calls
    with db() as session:
        audit = session.get(MembershipAuditEvent, audit_id)
        attempt = session.get(CheckoutAttempt, attempt_id)
        ref = attempt.reference
        assert audit.action == operator.AUTHORIZATION and audit.actor_type == "operator"
        assert audit.checkout_attempt_id == attempt.id and audit.details == {
            "approval": "FIN-42", "operator": "op-17", "adult_verifier_id": 1, "plan_code": plan,
            "mode": http.mode, "stripe_account_id": http.account,
            "price_id": "price_month" if days == 31 else "price_year"}
        assert session.get(BillingAccount, attempt.billing_account_id).verifier_id == 1
    assert operator.issue(db, audit_id) == http.checkout["url"]
    assert count(db, Membership) == count(db, BillingPaymentEffect) == 0
    body, headers = signed(http.paid_event(ref, plan))
    with pytest.raises(ValueError, match="signature"):
        billing.process_webhook(db, "stripe", body, {})
    record, fresh = billing.process_webhook(db, "stripe", body, headers)
    assert fresh and record.status == "processed"
    assert not billing.process_webhook(db, "stripe", body, headers)[1]
    with db() as session:
        attempt = session.get(CheckoutAttempt, attempt_id)
        sub = session.get(ProviderSubscription, attempt.subscription_id)
        member = session.get(Membership, sub.membership_id)
        paid = session.scalars(select(BillingPaymentEffect)).one()
        facts = session.scalars(select(BillingEventApplication)).one().facts
        assert attempt.status == "completed" and attempt.provider_checkout_id == http.checkout["id"]
        assert (sub.external_customer_id, sub.external_subscription_id) == ("cus_woodshed", "sub_woodshed")
        assert member.source == "stripe" and member.billing_account_id == attempt.billing_account_id
        assert utc(member.access_until) == utc(paid.paid_through) == NOW + timedelta(days=days)
        assert (paid.subscription_id, paid.payment_reference, paid.event_id) == (sub.id, "in_first", record.id)
        assert facts["stripe_invoice"]["payment_intent_id"] == "pi_first"
    with pytest.raises(ValueError, match="cannot be started again"):
        operator.issue(db, audit_id)
    assert approve(db, http, plan_code=plan) == receipt and count(db, CheckoutAttempt) == 1


def test_public_checkout_stays_closed_even_with_approval(db, http):
    audit_id, attempt_id, _ = approve(db, http)
    with db.begin() as session:
        declare_age(session, 1, "adult")
        ref = session.get(CheckoutAttempt, attempt_id).reference
    for kind, owner_id in (("adult", 1), ("adult", 2), ("student", 1)):
        browser = client(kind, owner_id)
        form = {"csrf": page_csrf(browser), "provider": "stripe", "plan_code": "full_monthly_7"}
        for extra in ({}, {"checkout_reference": ref}, {"new_attempt": "true"}):
            reply = browser.post("/membership/checkout", data=form | extra, follow_redirects=False)
            assert reply.status_code == 503 and "Public purchasing" in reply.text
        assert browser.post("/membership/checkout", data=form | {"authorization_id": audit_id}).status_code == 400
    assert not http.calls and count(db, CheckoutAttempt) == count(db, MembershipAuditEvent) == 1


def test_authorization_binding_and_eligibility(db, http, monkeypatch):
    for invalid in ({"adult_id": 0}, {"adult_id": 3}, {"operator": ""}, {"approval": ""},
                    {"plan_code": "friendship_annual_30"}, {"mode": "other"}):
        with pytest.raises(ValueError):
            approve(db, http, **invalid)
    assert count(db, CheckoutAttempt) == count(db, MembershipAuditEvent) == 0
    audit_id, attempt_id, _ = approve(db, http)
    for changed in ({"adult_id": 2}, {"plan_code": "full_annual_49"}, {"approval": "FIN-43"},
                    {"operator": "someone-else"}):
        with pytest.raises(ValueError, match="different"):
            approve(db, http, **changed)
    with db.begin() as session:
        other = MembershipAuditEvent(checkout_attempt_id=attempt_id, actor_type="admin", action="billing_inspection_requested")
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(ValueError, match="not found"):
        operator.issue(db, other_id)
    for key, value in (("PUBLIC_SUBSCRIPTIONS_ENABLED", "true"), ("STRIPE_BILLING_ENABLED", "false"),
                       ("STRIPE_ACCOUNT_ID", "acct_other"), ("STRIPE_PRICE_FULL_MONTHLY_7", "price_other"),
                       ("STRIPE_MODE", "test" if http.livemode else "live")):
        with monkeypatch.context() as changed:
            changed.setenv(key, value)
            with pytest.raises(ValueError):
                operator.issue(db, audit_id)
            with pytest.raises(ValueError):
                approve(db, http)
    with db.begin() as session:
        session.get(TrustedVerifier, 1).pin_hash = "deleted$fixture"
    with pytest.raises(ValueError, match="Account not found"):
        operator.issue(db, audit_id)
    assert not http.calls and count(db, CheckoutAttempt) == 1


def test_existing_purchase_checks_apply_at_authorization_and_issue(db, http):
    grant(db, "adult", 2)
    with pytest.raises(ValueError, match="existing membership"):
        approve(db, http, adult_id=2)
    audit_id, _, _ = approve(db, http)
    grant(db, "adult", 1)
    with pytest.raises(ValueError, match="existing membership"):
        operator.issue(db, audit_id)
    assert not http.calls and count(db, CheckoutAttempt) == 1


def test_prior_checkout_is_not_adopted(db, http):
    # Represents an attempt predating the public gate closure.
    with db.begin() as session:
        billing.authorize_checkout(session, Actor("adult", 1), "stripe", "full_annual_49", config=CONFIG)
    with pytest.raises(ValueError, match="pending checkout"):
        approve(db, http)
    assert not http.calls and count(db, CheckoutAttempt) == 1 and count(db, MembershipAuditEvent) == 0


def test_unknown_provider_outcome_reuses_reference_and_expiry(db, http, adapter, monkeypatch):
    provider, _ = adapter
    monkeypatch.setenv("CHECKOUT_VALIDITY_SECONDS", "2700")
    audit_id, attempt_id, expires = approve(db, http)
    assert expires == NOW + timedelta(minutes=45)
    create = provider.create_checkout

    def lose_response(**params):
        create(**params)
        raise billing.BillingUnavailable("Simulated response loss")

    with monkeypatch.context() as lost:
        lost.setattr(provider, "create_checkout", lose_response)
        with pytest.raises(billing.BillingUnavailable):
            operator.issue(db, audit_id)
    assert operator.issue(db, audit_id) == http.checkout["url"]
    posts = [call for call in http.calls if call[0].lower() == "post"]
    with db() as session:
        attempt = session.get(CheckoutAttempt, attempt_id)
        assert len(posts) == 2 and posts[0][2:] == posts[1][2:]
        assert posts[0][3]["Idempotency-Key"] == attempt.reference
        assert posts[0][2]["expires_at"] == [str(int(expires.timestamp()))]
    transport_calls = len(http.calls)
    monkeypatch.setattr(billing, "clock", lambda: expires)
    with pytest.raises(ValueError, match="cannot be started again"):
        operator.issue(db, audit_id)
    assert approve(db, http) == (audit_id, attempt_id, expires)
    assert count(db, MembershipAuditEvent) == count(db, CheckoutAttempt) == 1
    assert len(http.calls) == transport_calls


@pytest.mark.parametrize("seconds_before_expiry", [1, 0])
def test_delayed_invoice_checks_original_payment_time(db, http, monkeypatch, seconds_before_expiry):
    audit_id, attempt_id, expires = approve(db, http)
    operator.issue(db, audit_id)
    with db() as session:
        reference = session.get(CheckoutAttempt, attempt_id).reference
    invoice = http.paid_event(reference)
    invoice["data"]["object"]["status_transitions"]["paid_at"] = int(expires.timestamp()) - seconds_before_expiry
    monkeypatch.setattr(billing, "clock", lambda: expires + timedelta(minutes=5))
    record, _ = billing.process_webhook(db, "stripe", *signed(invoice))
    if seconds_before_expiry:
        assert record.status == "processed" and count(db, BillingPaymentEffect) == 1
    else:
        assert record.error_code == "checkout_authorization_expired"
        assert count(db, BillingPaymentEffect) == count(db, Membership) == 0


def test_admin_recovery_and_later_signed_invoice_credit_adult_once(db, http, monkeypatch):
    audit_id, attempt_id, _ = approve(db, http)
    operator.issue(db, audit_id)
    with db() as session:
        reference = session.get(CheckoutAttempt, attempt_id).reference
    invoice = http.paid_event(reference)
    # Stripe inspection timestamps are wall time, whereas this fixture's payment is historical.
    monkeypatch.setattr(billing, "clock", lambda: datetime.now(timezone.utc))
    inspected = billing_reconciliation.inspect(db, "checkout", attempt_id, Actor("admin"))
    with db() as session:
        assert session.get(MembershipAuditEvent, inspected).details["classification"] == "applied"
    assert billing.process_webhook(db, "stripe", *signed(invoice))[0].status == "processed"
    assert count(db, Membership) == count(db, BillingPaymentEffect) == count(db, ProviderSubscription) == 1


def test_concurrent_issuance_uses_one_stripe_idempotency_key(db, http, adapter, monkeypatch):
    audit_id, attempt_id, _ = approve(db, http)
    provider, _ = adapter
    create = provider.create_checkout
    both_authorized = Barrier(2)

    def issue_together(**params):
        both_authorized.wait(timeout=10)
        return create(**params)

    monkeypatch.setattr(provider, "create_checkout", issue_together)
    with ThreadPoolExecutor(2) as pool:
        urls = list(pool.map(lambda _: operator.issue(db, audit_id), (1, 2)))
    assert urls == [http.checkout["url"]] * 2
    posts = [call for call in http.calls if call[0].lower() == "post"]
    with db() as session:
        attempt = session.get(CheckoutAttempt, attempt_id)
        assert len(posts) == 2 and posts[0][2:] == posts[1][2:]
        assert posts[0][3]["Idempotency-Key"] == attempt.reference
        assert attempt.provider_checkout_id == http.checkout["id"]
    assert count(db, CheckoutAttempt) == count(db, MembershipAuditEvent) == 1


def test_competing_operator_approvals_have_one_winner(db, http):
    start = Barrier(2)

    def attempt(adult_id):
        start.wait(timeout=10)
        try:
            return approve(db, http, adult_id=adult_id)[0]
        except ValueError as error:
            assert "different" in str(error)
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    assert outcomes.count(None) == 1
    assert count(db, CheckoutAttempt) == count(db, MembershipAuditEvent) == 1 and not http.calls


def test_audit_failure_rolls_back_attempt(db, http):
    def fail_audit(session, context, instances):
        if any(isinstance(row, MembershipAuditEvent) for row in session.new):
            raise SQLAlchemyError("Simulated audit failure")

    event.listen(db.class_, "before_flush", fail_audit)
    try:
        with pytest.raises(SQLAlchemyError):
            approve(db, http)
    finally:
        event.remove(db.class_, "before_flush", fail_audit)
    assert not http.calls and count(db, CheckoutAttempt) == count(db, BillingAccount) == 0


def test_operator_command(db, http, monkeypatch, capsys):
    monkeypatch.setattr(operator, "SessionLocal", db)
    args = ["authorize", "--adult-verifier-id", "1", "--plan", "full_monthly_7", "--mode", http.mode,
            "--operator", "op-17", "--approval", "FIN-42"]
    assert operator.main(args) == 0
    with db() as session:
        audit_id = session.scalar(select(MembershipAuditEvent.id))
    assert f"Authorization {audit_id}," in capsys.readouterr().out and not http.calls
    assert operator.main(["issue", "--authorization-id", str(audit_id)]) == 0
    assert capsys.readouterr().out.strip() == http.checkout["url"]
    assert operator.main(["issue", "--authorization-id", "0"]) == 1
    assert count(db, BillingProviderEvent) == count(db, Membership) == 0
