"""Local replacement decision. Caller holds the BillingAccount lock throughout.

No provider cancellation, grace period, seat carry-forward or history deletion.
The same decision guards checkout authorization and verified initial provisioning.
"""
from dataclasses import dataclass
from sqlalchemy import select, or_, and_
from .models import (Membership, ProviderSubscription, CheckoutAttempt,
                     BillingProviderEvent, BillingEventApplication, BillingPaymentEffect)
from .memberships import utc, audit


@dataclass(frozen=True)
class PurchaseDecision:
    state: str
    retire_ids: tuple[int, ...] = ()

    @property
    def allowed(self):
        return self.state in {"new_owner", "expired_history"}


def purchase_decision(session, account_id, at, *, attempt_id=None, applying_subscription=None):
    """Refresh under account serialization; never infer payment from status.

    applying_subscription is (provider, external ID), used only by the verified
    initial-payment path. Other events for that same new subscription will be
    applied idempotently after provisioning, not treated as a second purchase.
    """
    at = utc(at)
    members = list(session.scalars(select(Membership).where(Membership.billing_account_id == account_id)
                                  .execution_options(populate_existing=True)))
    subscriptions = list(session.scalars(select(ProviderSubscription).join(Membership).where(
        Membership.billing_account_id == account_id).execution_options(populate_existing=True)))
    attempts = list(session.scalars(select(CheckoutAttempt).where(CheckoutAttempt.billing_account_id == account_id)
                                   .execution_options(populate_existing=True)))
    correlations = [and_(BillingProviderEvent.provider == row.provider,
                         BillingEventApplication.checkout_reference == row.reference) for row in attempts]
    correlations += [and_(BillingProviderEvent.provider == row.provider,
                          BillingEventApplication.external_subscription_id == row.external_subscription_id)
                     for row in subscriptions]
    if correlations:
        events = session.execute(select(BillingEventApplication, BillingProviderEvent).join(
            BillingProviderEvent, BillingProviderEvent.id == BillingEventApplication.event_id).where(or_(*correlations)))
        for app, event in events:
            if applying_subscription == (event.provider, app.external_subscription_id):
                continue
            if app.status != "processed" or event.status != "processed" or event.processed_at is None:
                return PurchaseDecision("unresolved_payment")
    for row in attempts:
        if row.id != attempt_id and row.status == "recoverable":
            return PurchaseDecision("unresolved_payment")
    by_member = {row.membership_id: row for row in subscriptions}
    retire = []
    for member in members:
        if member.source == "manual":
            if member.status == "active":
                return PurchaseDecision("existing_membership")
            continue
        sub = by_member.get(member.id)
        if member.access_until is None or sub is None:
            return PurchaseDecision("unresolved_payment")
        # A recorded payment effect must agree with its materialized entitlement.
        if session.scalar(select(BillingPaymentEffect.id).where(
                BillingPaymentEffect.subscription_id == sub.id,
                BillingPaymentEffect.paid_through > member.access_until)):
            return PurchaseDecision("unresolved_payment")
        if utc(member.access_until) > at:
            return PurchaseDecision("currently_entitled")
        # Expiration is not proof that a recurring provider contract has ended.
        if sub.terminated_at is None or utc(sub.terminated_at) > at:
            return PurchaseDecision("provider_not_terminal")
        if member.status == "active":
            retire.append(member.id)
    # Replacement may reuse its own pending authorization, but cannot open a
    # second checkout while a previous replacement can still accept payment.
    if members and any(row.id != attempt_id and row.status == "pending" and utc(row.expires_at) > at
                       for row in attempts):
        return PurchaseDecision("pending_checkout")
    return PurchaseDecision("expired_history" if members else "new_owner", tuple(retire))


def require_new_purchase(session, account_id, at, actor, **kwargs):
    decision = purchase_decision(session, account_id, at, **kwargs)
    if not decision.allowed:
        messages = {
            "unresolved_payment": "A paid checkout needs processing review before another purchase.",
            "provider_not_terminal": "The previous provider subscription must be confirmed ended before replacement.",
            "pending_checkout": "A replacement checkout is already in progress. Retry that checkout.",
        }
        raise ValueError(messages.get(decision.state, "Manage the existing membership before starting another subscription."))
    for member_id in decision.retire_ids:
        member = session.get(Membership, member_id)
        member.status = "ended"
        audit(session, member, actor, "membership_ended", reason="paid_entitlement_exhausted")
    session.flush()
    return decision
