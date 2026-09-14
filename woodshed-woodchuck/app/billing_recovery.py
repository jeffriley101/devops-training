"""Site-admin operations on durable verified work; never provider transport.

Retry requests commit before processing. Results commit with application effects;
database failures retain the request even if result recording is unavailable.
"""
from dataclasses import dataclass
from sqlalchemy import select, or_, exists
from sqlalchemy.exc import SQLAlchemyError
from . import billing_providers as processing
from .models import (BillingProviderEvent, BillingEventApplication, CheckoutAttempt,
                     ProviderSubscription, Membership, MembershipAuditEvent, BillingPaymentEffect)


RECONCILIATION = {
    "checkout_authorization_expired": "Payment evidence is outside checkout authorization. Reconciliation is required.",
    "checkout_already_consumed": "Checkout is already linked to a subscription. Reconciliation is required.",
    "initial_subscription_terminated": "Initial subscription is terminal. Reconciliation is required.",
    "subscription_correlation_conflict": "Subscription and checkout correlation disagree.",
    "payment_reference_conflict": "Recorded payment evidence conflicts with an existing payment effect.",
    "payment_after_termination": "Payment was recorded after termination. Reconciliation is required.",
    "missing_paid_through_baseline": "Confirmed entitlement baseline is missing.",
    "equal_time_lifecycle_conflict": "Provider lifecycle facts conflict at the same timestamp.",
    "subscription_terminated": "The historical subscription cannot be reactivated by local retry.",
    "membership_ended": "The membership has ended. Local retry cannot reactivate it.",
}
LOCAL_CODES = {None, "provisioning_conflict", "unknown_checkout", "awaiting_paid_entitlement",
               "subscription_correlation_retry", "concurrent_provisioning_retry"}
OUTCOMES = {
    "succeeded": "The recorded payment/event was applied successfully.",
    "still_recoverable": "Processing remains unresolved. The durable payment evidence has been retained.",
    "blocked": "Current state requires reconciliation; no processing override was performed.",
    "no_longer_retryable": "This work was already completed. No payment or entitlement was applied again.",
}


@dataclass(frozen=True)
class RecoveryDecision:
    retryable: bool
    state: str
    reason: str


def context(session, record, app):
    sub = attempt = member = None
    if app:
        sub = session.scalar(select(ProviderSubscription).where(ProviderSubscription.provider == record.provider,
            ProviderSubscription.external_subscription_id == app.external_subscription_id))
        attempt = session.scalar(select(CheckoutAttempt).where(CheckoutAttempt.provider == record.provider,
            CheckoutAttempt.reference == app.checkout_reference)) if app.checkout_reference else None
        member = session.get(Membership, sub.membership_id) if sub else None
    return sub, attempt, member


def retryability(session, record, app):
    """The queue and locked POST both use this domain decision."""
    inconsistent_effect = session.scalar(select(BillingPaymentEffect.id).join(ProviderSubscription).join(Membership).where(
        BillingPaymentEffect.event_id == record.id,
        or_(Membership.access_until.is_(None), BillingPaymentEffect.paid_through > Membership.access_until)))
    if inconsistent_effect:
        return RecoveryDecision(False, "reconciliation", "A recorded payment effect disagrees with local entitlement.")
    if record.status == "processed" and record.processed_at and (app is None or app.status == "processed"):
        return RecoveryDecision(False, "completed", "This event has already completed.")
    if app is None:
        return RecoveryDecision(False, "reconciliation", "No normalized event application exists. Reconciliation is required.")
    if app.status not in {"pending", "recoverable"} or record.status not in {"received", "failed"}:
        return RecoveryDecision(False, "reconciliation", "Event/application state is inconsistent.")
    code = app.error_code or record.error_code
    if code in RECONCILIATION or code not in LOCAL_CODES:
        return RecoveryDecision(False, "reconciliation", RECONCILIATION.get(code, "Unrecognized state requires reconciliation."))
    sub, attempt, member = context(session, record, app)
    if member and (member.status != "active" or member.revoked_at):
        return RecoveryDecision(False, "blocked", "The membership has ended; local retry cannot restore it.")
    if not sub:
        if not attempt:
            return RecoveryDecision(False, "reconciliation", "Awaiting checkout/subscription correlation. Local retry cannot invent it.")
        if not app.facts.get("paid_through") or not app.facts.get("payment_reference"):
            return RecoveryDecision(False, "reconciliation", "Awaiting verified payment evidence; lifecycle state cannot grant access.")
    reason = {
        "provisioning_conflict": "Local provisioning failed. Retry rechecks membership, ownership and student-seat constraints.",
        "subscription_correlation_retry": "Correlation changed during processing. Retry acquires fresh locks.",
        "concurrent_provisioning_retry": "Another worker changed provisioning state. Retry rechecks the recorded work.",
    }.get(code, "Recorded work has not completed. Retry the recorded facts through normal billing checks.")
    return RecoveryDecision(True, "retryable", reason)


def queue(session, before=None, limit=50):
    effect_mismatch = exists(select(BillingPaymentEffect.id).join(ProviderSubscription).join(Membership).where(
        BillingPaymentEffect.event_id == BillingProviderEvent.id,
        or_(Membership.access_until.is_(None), BillingPaymentEffect.paid_through > Membership.access_until)))
    query = select(BillingProviderEvent, BillingEventApplication).outerjoin(
        BillingEventApplication, BillingEventApplication.event_id == BillingProviderEvent.id).where(or_(
            BillingProviderEvent.status != "processed", BillingProviderEvent.processed_at.is_(None),
            BillingEventApplication.status != "processed", effect_mismatch))
    if before is not None:
        query = query.where(BillingProviderEvent.id < before)
    rows = session.execute(query.order_by(BillingProviderEvent.id.desc()).limit(limit + 1)).all()
    items = []
    for record, app in rows[:limit]:
        sub, attempt, member = context(session, record, app)
        audits = list(session.scalars(select(MembershipAuditEvent).where(
            MembershipAuditEvent.billing_event_id == record.id,
            MembershipAuditEvent.action.in_(["billing_retry_requested", "billing_retry_result"]))
            .order_by(MembershipAuditEvent.id.desc()).limit(6)))
        items.append({"event_id": record.id, "application_id": app.id if app else None,
            "provider": record.provider, "category": "Verified payment" if app and app.facts.get("paid_through") else "Lifecycle / correlation",
            "received_at": record.received_at, "state": app.status if app else record.status,
            "decision": retryability(session, record, app),
            "account_id": member.billing_account_id if member else attempt.billing_account_id if attempt else None,
            "membership_id": member.id if member else None, "subscription_id": sub.id if sub else None,
            "checkout_id": attempt.id if attempt else None,
            "history": [{"at": a.created_at, "requested": a.action == "billing_retry_requested",
                         "result": OUTCOMES.get(a.details.get("outcome"), "Request recorded; no outcome recorded yet.")}
                        for a in audits]})
    # Recovery attempts lacking any event are visible but cannot be executed.
    correlated = exists(select(BillingEventApplication.id).join(BillingProviderEvent).where(
        BillingEventApplication.checkout_reference == CheckoutAttempt.reference,
        BillingProviderEvent.provider == CheckoutAttempt.provider))
    orphans = session.execute(select(CheckoutAttempt.id, CheckoutAttempt.provider).where(
        CheckoutAttempt.status == "recoverable", ~correlated).order_by(CheckoutAttempt.id.desc()).limit(50)).all()
    recent = session.scalars(select(MembershipAuditEvent).where(
        MembershipAuditEvent.billing_event_id.is_not(None),
        MembershipAuditEvent.action.in_(["billing_retry_requested", "billing_retry_result"]))
        .order_by(MembershipAuditEvent.id.desc()).limit(20)).all()
    return {"items": items, "next_before": rows[limit - 1][0].id if len(rows) > limit else None,
            "orphan_checkouts": [{"id": id, "provider": provider} for id, provider in orphans],
            "recent_actions": [{"event_id": a.billing_event_id, "at": a.created_at,
                "message": "Retry requested" if a.action == "billing_retry_requested" else
                    OUTCOMES.get(a.details.get("outcome"), "Outcome unavailable.")} for a in recent]}


def _audit(session, event_id, action, **details):
    row = MembershipAuditEvent(billing_event_id=event_id, action=action,
                               actor_type="admin", details=details)
    session.add(row)
    session.flush()
    return row


def retry(session_factory, event_id, actor):
    if actor.kind != "admin":
        raise PermissionError("Site administrator required.")
    with session_factory() as session:
        if session.get(BillingProviderEvent, event_id) is None:
            raise LookupError("Billing event not found.")
        request_id = _audit(session, event_id, "billing_retry_requested").id
        session.commit()
    try:
        with session_factory() as session:
            # Same account -> event ordering as automatic processing. The audit
            # request transaction has closed before acquiring these locks.
            record, app, locked_accounts = processing.lock_verified_event(session, event_id)
            decision = retryability(session, record, app)
            if not decision.retryable:
                outcome = "no_longer_retryable" if decision.state == "completed" else "blocked"
            else:
                processing.apply_locked_event(session, record, app, locked_accounts)
                outcome = "succeeded" if app.status == "processed" and record.status == "processed" else "still_recoverable"
                if outcome != "succeeded" and not retryability(session, record, app).retryable:
                    outcome = "blocked"
            result = _audit(session, event_id, "billing_retry_result", request_id=request_id, outcome=outcome)
            _, _, member = context(session, record, app)
            if member:
                result.membership_id = member.id
            result_id = result.id
            session.commit()
            return result_id
    except (SQLAlchemyError, ValueError, TypeError, KeyError):
        # Never persist exception strings or payloads. If the database remains
        # unavailable, the committed request still proves the attempted action.
        with session_factory() as session:
            result = _audit(session, event_id, "billing_retry_result", request_id=request_id,
                            outcome="still_recoverable", reason="local_processing_failure")
            result_id = result.id
            session.commit()
            return result_id


def result_message(session, result_id):
    row = session.get(MembershipAuditEvent, result_id) if result_id else None
    if row and row.action == "billing_retry_result":
        return OUTCOMES.get(row.details.get("outcome"))
    return None
