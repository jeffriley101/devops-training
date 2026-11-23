"""Stripe contract finality and replacement using an in-process SDK transport."""
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app import billing_providers as billing, billing_recovery, memberships, operator_checkout, stripe_billing
from app.billing_replacement import purchase_decision
from app.models import (BillingPaymentEffect, BillingProviderEvent, CheckoutAttempt, Membership,
                        MembershipAuditEvent, ProviderSubscription)
from tests.test_billing_replacement import db, sqlite_db
from tests.test_memberships import ADMIN, NOW, client
from tests.test_operator_checkout import approve, http
from tests.test_stripe_billing import adapter, count, signed
from tests.test_stripe_refund_access import admin_browser, end_access
from tests.test_subscription_compliance import paid_membership
from tests.test_website_checkout import ANNUAL, MONTHLY, fields


def set_clock(monkeypatch, at):
    monkeypatch.setattr(billing, "clock", lambda: at)
    monkeypatch.setattr(memberships, "clock", lambda: at)
    provider_clock = Mock(wraps=datetime)
    provider_clock.now.return_value = at
    monkeypatch.setattr(stripe_billing, "datetime", provider_clock)


def post_event(browser, event):
    body, headers = signed(event)
    return browser.post("/membership/webhooks/stripe", content=body, headers=headers)


@pytest.mark.parametrize("paid_membership", [MONTHLY, ANNUAL], indirect=True)
def test_cancel_expire_receive_end_and_repurchase(db, http, paid_membership, monkeypatch):
    member_id, _ = paid_membership
    with db() as session:
        member = session.get(Membership, member_id)
        end = memberships.utc(member.access_until)
        plan = member.plan_code
        account_id = member.billing_account_id
    owner = client("adult")
    page = owner.get("/membership")
    confirmation = owner.post(f"/membership/{member_id}/cancel-renewal",
                              data={"csrf": page.context["csrf"], "as_account": "adult"})
    assert "Stripe confirmed cancellation effective" in confirmation.text
    with db() as session:
        assert session.scalars(select(ProviderSubscription)).one().terminated_at is None
        assert memberships.student_has_full_access(session, 1, at=end - timedelta(seconds=1))
    after = end + timedelta(hours=1)
    set_clock(monkeypatch, after)
    monkeypatch.setenv("PUBLIC_SUBSCRIPTIONS_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_SUBSCRIPTION_REFUND_POLICY", "fixture release gate")
    with db() as session:
        assert not memberships.student_has_full_access(session, 1, at=after)
        assert purchase_decision(session, account_id, after).state == "provider_not_terminal"
    http.subscription["canceled_at"] = int(NOW.timestamp())  # Original scheduling time.
    deletion = http.ended_event(end)
    assert post_event(owner, deletion).json()["processed"]
    assert post_event(owner, deletion).json()["duplicate"]
    with db() as session:
        sub = session.scalars(select(ProviderSubscription)).one()
        assert memberships.utc(sub.terminated_at) == end
        assert memberships.utc(session.get(Membership, member_id).access_until) == end
        assert purchase_decision(session, account_id, after).allowed
        assert not memberships.student_has_full_access(session, 1, at=after)
    assert count(db, BillingPaymentEffect) == 1

    old_sub, old_checkout = deepcopy(http.subscription), deepcopy(http.checkout)
    http.subscription.update(id="sub_replacement", status="active", ended_at=None, canceled_at=None)
    http.checkout.update(id=f"cs_{http.mode}_replacement", subscription="sub_replacement",
                         invoice="in_replacement", status="open", payment_status="unpaid")
    page = owner.get("/membership")
    assert "Continue to Stripe Checkout" in page.text
    response = owner.post("/membership/checkout", data=fields(page.context["csrf"], plan), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == http.checkout["url"]
    assert count(db, Membership) == count(db, BillingPaymentEffect) == 1
    with db() as session:
        new_attempt = session.scalars(select(CheckoutAttempt).where(CheckoutAttempt.status == "pending")).one()
    replacement = http.paid_event(new_attempt.reference, plan, purchased_at=after, event_id="evt_new_paid",
                                  invoice_id="in_replacement", intent_id="pi_replacement")
    assert post_event(owner, replacement).json()["processed"]
    assert post_event(owner, replacement).json()["duplicate"]
    with db() as session:
        new_sub = session.scalars(select(ProviderSubscription).where(
            ProviderSubscription.external_subscription_id == "sub_replacement")).one()
        new_id = new_sub.membership_id
        assert new_id != member_id and new_sub.terminated_at is None
        assert memberships.membership_is_active(session.get(Membership, new_id), after)
        assert session.get(Membership, member_id).status == "ended"
    page = owner.get("/membership")
    result = owner.post("/membership/actions", data={"csrf": page.context["csrf"], "as_account": "adult",
        "membership_id": new_id, "action": "add", "woodchuck_id": "WC-MEMBER1"})
    assert result.status_code == 200
    # Redelivery under another event ID must still affect only the original contract.
    with monkeypatch.context() as old:
        old.setattr(http, "subscription", old_sub)
        old.setattr(http, "checkout", old_checkout)
        late = http.ended_event(end, event_id="evt_late_old_end")
        assert post_event(owner, late).json()["processed"]
    with db() as session:
        assert memberships.membership_is_active(session.get(Membership, new_id), after)
        assert memberships.student_has_full_access(session, 1, at=after)
        assert session.scalars(select(ProviderSubscription).where(
            ProviderSubscription.membership_id == new_id)).one().terminated_at is None
    assert count(db, Membership) == count(db, BillingPaymentEffect) == 2


def test_terminal_confirmation_after_full_refund_preserves_revocation(db, http, paid_membership, monkeypatch):
    member_id, _ = paid_membership
    end_access(db, member_id)
    with db() as session:
        revoked = session.get(Membership, member_id).revoked_at
        assert revoked is not None
    at = NOW + timedelta(hours=1)
    set_clock(monkeypatch, at)
    assert post_event(client(), http.ended_event(at)).json()["processed"]
    with db() as session:
        member = session.get(Membership, member_id)
        assert member.revoked_at == revoked and not memberships.student_has_full_access(session, 1, at=at)
        assert memberships.utc(session.scalars(select(ProviderSubscription)).one().terminated_at) == at
    assert count(db, BillingPaymentEffect) == 1


def test_conflicting_terminal_dates_preserve_confirmed_end_and_payment(db, http, paid_membership, monkeypatch):
    member_id, _ = paid_membership
    first_end = NOW + timedelta(days=10)
    second_end = first_end + timedelta(days=1)
    set_clock(monkeypatch, second_end)
    browser = client()
    assert post_event(browser, http.ended_event(first_end)).json()["processed"]
    with db() as session:
        paid_through = session.get(Membership, member_id).access_until
        effect_id = session.scalars(select(BillingPaymentEffect.id)).one()

    conflict = http.ended_event(second_end, event_id="evt_conflicting_end")
    response = post_event(browser, conflict)
    assert response.status_code == 200 and response.json()["processed"] is False
    with db() as session:
        receipt = session.scalars(select(BillingProviderEvent).where(
            BillingProviderEvent.external_event_id == conflict["id"])).one()
        assert receipt.error_code == "termination_date_conflict"
        assert memberships.utc(session.scalars(select(ProviderSubscription)).one().terminated_at) == first_end
        assert session.get(Membership, member_id).access_until == paid_through
        effect = session.scalars(select(BillingPaymentEffect)).one()
        assert (effect.id, effect.payment_reference, effect.paid_through) == (effect_id, "in_first", paid_through)


@pytest.mark.parametrize("invalid", [
    "signature", "account", "customer", "mode", "reference", "missing_end", "customer_binding", "checkout_binding",
])
def test_terminal_requires_authenticated_matching_subscription(db, http, paid_membership, monkeypatch, invalid):
    member_id, _ = paid_membership
    end = NOW + timedelta(days=365)
    set_clock(monkeypatch, end)
    deletion = http.ended_event(end)
    canonical = http.events[deletion["id"]]["data"]["object"]
    if invalid == "account":
        http.account = "acct_other"
    elif invalid == "customer":
        canonical["customer"] = "cus_other"
    elif invalid == "mode":
        canonical["livemode"] = not http.livemode
    elif invalid == "reference":
        canonical["metadata"]["woodshed_checkout_reference"] = "different-reference"
    elif invalid == "missing_end":
        canonical.pop("ended_at")
    elif invalid == "customer_binding":
        # The two Stripe objects agree with each other but disagree with the original local invoice.
        http.checkout["customer"] = canonical["customer"] = "cus_other"
    elif invalid == "checkout_binding":
        http.checkout["id"] = f"cs_{http.mode}_other"
    before = len(http.calls)
    body, headers = signed(deletion)
    response = client().post("/membership/webhooks/stripe", content=body, headers={} if invalid == "signature" else headers)
    assert response.status_code == 400 or response.json().get("processed") is False
    if invalid == "signature":
        assert len(http.calls) == before
    with db() as session:
        assert session.scalars(select(ProviderSubscription)).one().terminated_at is None
        assert memberships.utc(session.get(Membership, member_id).access_until) == end
    assert count(db, BillingPaymentEffect) == 1


def test_deletion_before_initial_invoice_can_be_retried_without_provisioning(db, http, monkeypatch):
    approval, attempt_id, _ = approve(db, http)
    operator_checkout.issue(db, approval)
    with db() as session:
        reference = session.get(CheckoutAttempt, attempt_id).reference
    paid = http.paid_event(reference, MONTHLY)
    end = NOW + timedelta(days=31)
    set_clock(monkeypatch, end + timedelta(hours=1))
    deletion = http.ended_event(end)
    browser = client()
    first = post_event(browser, deletion)
    assert first.status_code == 200 and first.json()["processed"] is False
    assert count(db, Membership) == count(db, BillingPaymentEffect) == count(db, ProviderSubscription) == 0
    assert post_event(browser, paid).json()["processed"]
    with db() as session:
        terminal_id = session.scalars(select(BillingProviderEvent.id).where(
            BillingProviderEvent.external_event_id == deletion["id"])).one()
    billing_recovery.retry(db, terminal_id, ADMIN)
    assert post_event(browser, deletion).json()["duplicate"]
    with db() as session:
        assert session.get(BillingProviderEvent, terminal_id).status == "processed"
        assert memberships.utc(session.scalars(select(ProviderSubscription)).one().terminated_at) == end
    assert count(db, Membership) == count(db, BillingPaymentEffect) == 1


def test_operator_reconciles_missed_terminal_delivery(db, http, paid_membership, monkeypatch):
    member_id, _ = paid_membership
    with db() as session:
        attempt_id = session.scalars(select(CheckoutAttempt.id)).one()
        end = memberships.utc(session.get(Membership, member_id).access_until)
    # An early termination still leaves the nonrefunded paid entitlement intact.
    ended = end - timedelta(days=2)
    at = ended + timedelta(hours=1)
    set_clock(monkeypatch, at)
    admin = admin_browser()
    path = f"/admin/billing-recovery/checkout/{attempt_id}/inspect"
    page = admin.get(f"/admin/membership?membership_id={member_id}")
    assert path in page.text
    csrf = page.context["csrf"]
    before = len(http.calls)
    assert admin.post(path, data={"csrf": "bad"}).status_code == 403 and len(http.calls) == before
    assert admin.post(path, data={"csrf": csrf}).status_code == 200  # Active subscription.
    http.ended_event(ended)  # Snapshot available to Stripe reads, webhook never delivered.
    http.error = True
    assert admin.post(path, data={"csrf": csrf}).status_code == 200
    http.error = None
    with db() as session:
        assert session.scalars(select(ProviderSubscription)).one().terminated_at is None
        assert memberships.student_has_full_access(session, 1, at=at)
    for _ in range(2):
        assert admin.post(path, data={"csrf": csrf}).status_code == 200
    with db() as session:
        sub = session.scalars(select(ProviderSubscription)).one()
        member = session.get(Membership, member_id)
        assert memberships.utc(sub.terminated_at) == ended and memberships.utc(member.access_until) == end
        assert memberships.student_has_full_access(session, 1, at=end - timedelta(seconds=1))
        assert purchase_decision(session, member.billing_account_id, at).state == "currently_entitled"
        assert purchase_decision(session, member.billing_account_id, end).allowed
        facts = session.scalars(select(MembershipAuditEvent.details).where(
            MembershipAuditEvent.action == "billing_inspection_result").order_by(MembershipAuditEvent.id)).all()
        assert facts[1]["classification"] in {"unsupported", "provider_unavailable"}
        assert facts[1]["processing"] is False and facts[-1]["classification"] == "in_sync"
    assert count(db, BillingPaymentEffect) == 1
