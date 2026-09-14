"""Explicit inspection of authoritative evidence; no financial override or transport under locks."""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from sqlalchemy import select, or_
from sqlalchemy.exc import SQLAlchemyError
from . import billing_providers as billing, billing_recovery as recovery
from .billing_config import BillingConfig
from .memberships import utc
from .models import (BillingProviderEvent, BillingEventApplication, CheckoutAttempt,
                     ProviderSubscription, Membership, MembershipAuditEvent, BillingPaymentEffect)


MESSAGES = {
    "in_sync": "Provider evidence agrees with recorded local work. No additional application was needed.",
    "local_retry_available": "The provider confirms existing verified work. Normal processing is available.",
    "evidence_available": "Authoritative evidence can enter normal billing processing.",
    "applied": "Authoritative evidence was applied through normal billing processing.",
    "correlation_needed": "Provider evidence lacks sufficient local correlation. No correlation was invented.",
    "reconciliation_required": "Evidence is incomplete or conflicts with durable records. No override was performed.",
    "financial_decision_required": "Provider or financial resolution is required. No financial action was taken.",
    "provider_unavailable": "Provider lookup is temporarily unavailable. Recorded work is retained.",
    "unsupported": "Authoritative inspection is not configured or supported by this provider adapter.",
    "object_missing": "The provider could not find the requested object. Local history was retained.",
    "stale_context": "Local correlation changed during inspection. Inspect again using current context.",
    "still_recoverable": "Inspection or processing remains unresolved. Review recorded work before retrying.",
}


@dataclass(frozen=True)
class Decision:
    classification: str
    process: bool = False


def _load(session, kind, target_id):
    record = app = attempt = sub = member = None
    if kind == "event":
        record = session.get(BillingProviderEvent, target_id)
        if record:
            app = session.scalar(select(BillingEventApplication).where(BillingEventApplication.event_id == record.id))
            sub, attempt, member = recovery.context(session, record, app)
    elif kind == "checkout":
        attempt = session.get(CheckoutAttempt, target_id)
        if attempt and attempt.subscription_id:
            sub = session.get(ProviderSubscription, attempt.subscription_id)
            member = session.get(Membership, sub.membership_id) if sub else None
    if record is None and attempt is None:
        raise LookupError("Billing inspection target not found.")
    request = billing.EvidenceRequest(
        provider=record.provider if record else attempt.provider,
        external_event_id=record.external_event_id if record else None,
        external_subscription_id=app.external_subscription_id if app else sub.external_subscription_id if sub else None,
        checkout_reference=app.checkout_reference if app else attempt.reference if attempt else None,
        provider_checkout_id=attempt.provider_checkout_id if attempt else None)
    return request, record, app, attempt, sub, member


def _audit(session, kind, target_id, action, **details):
    row = MembershipAuditEvent(action=action, actor_type="admin", details=details,
        billing_event_id=target_id if kind == "event" else None,
        checkout_attempt_id=target_id if kind == "checkout" else None)
    session.add(row)
    session.flush()
    return row


def _attach_context(session, row, kind, target_id):
    _, _, _, attempt, _, member = _load(session, kind, target_id)
    if attempt:
        row.checkout_attempt_id = attempt.id
    if member:
        row.membership_id = member.id


def _valid_evidence(request, evidence):
    if not isinstance(evidence, billing.ProviderEvidence) or evidence.request != request:
        return False
    if (not isinstance(evidence.observed_at, datetime) or evidence.observed_at.utcoffset() is None
            or evidence.observed_at > utc(billing.clock()) or type(evidence.financial_resolution_required) is not bool):
        return False
    if evidence.state not in {"found", "not_found", "unavailable", "unsupported"}:
        return False
    if evidence.state != "found":
        return evidence.event is None
    for name in ("external_subscription_id", "checkout_reference", "provider_checkout_id"):
        expected, actual = getattr(request, name), getattr(evidence, name)
        if expected is not None and expected != actual:
            return False
    if evidence.event:
        if not isinstance(evidence.event, billing.SubscriptionEvent):
            return False
        try:
            billing._facts(evidence.event)
        except (ValueError, TypeError, AttributeError):
            return False
        if (evidence.event.external_subscription_id != evidence.external_subscription_id
                or evidence.event.checkout_reference != evidence.checkout_reference):
            return False
    return True


def classify(session, request, evidence, target):
    """No writes: compare verified provider facts with fresh durable local state."""
    if not _valid_evidence(request, evidence):
        return Decision("reconciliation_required")
    if evidence.state != "found":
        return Decision({"not_found": "object_missing", "unavailable": "provider_unavailable",
                         "unsupported": "unsupported"}[evidence.state])
    if evidence.financial_resolution_required:
        return Decision("financial_decision_required")
    _, record, app, _, _, _ = target
    if record and session.scalar(select(BillingPaymentEffect.id).join(ProviderSubscription).join(Membership).where(
            BillingPaymentEffect.event_id == record.id,
            or_(Membership.access_until.is_(None), BillingPaymentEffect.paid_through > Membership.access_until))):
        return Decision("reconciliation_required")
    event = evidence.event
    if event is None:
        return Decision("correlation_needed")
    sub = session.scalar(select(ProviderSubscription).where(ProviderSubscription.provider == request.provider,
        ProviderSubscription.external_subscription_id == event.external_subscription_id))
    attempt = session.scalar(select(CheckoutAttempt).where(CheckoutAttempt.provider == request.provider,
        CheckoutAttempt.reference == evidence.checkout_reference)) if evidence.checkout_reference else None
    member = session.get(Membership, sub.membership_id) if sub else None
    if not sub and not attempt:
        return Decision("correlation_needed")
    if evidence.checkout_reference and not attempt:
        return Decision("correlation_needed")
    if sub and attempt and (attempt.subscription_id != sub.id or attempt.billing_account_id != member.billing_account_id):
        return Decision("reconciliation_required")
    # Known subscription/checkout price snapshots remain the price authority.
    if event.paid_through:
        price = sub or attempt
        if (type(evidence.amount_cents) is not int or
                (evidence.plan_code, evidence.amount_cents, evidence.currency, evidence.interval) !=
                (price.plan_code, price.amount_cents, price.currency, price.interval)):
            return Decision("reconciliation_required")
    if member and (member.status != "active" or member.revoked_at):
        return Decision("financial_decision_required" if event.paid_through else "reconciliation_required")
    if sub and event.paid_through:
        effect = session.scalar(select(BillingPaymentEffect).where(BillingPaymentEffect.subscription_id == sub.id,
            BillingPaymentEffect.payment_reference == event.payment_reference))
        if effect and (utc(effect.paid_through) != event.paid_through or member.access_until is None
                       or utc(effect.paid_through) > utc(member.access_until)):
            return Decision("reconciliation_required")
        if sub.terminated_at and event.occurred_at > utc(sub.terminated_at) and effect is None:
            return Decision("financial_decision_required")
    existing = session.scalar(select(BillingProviderEvent).where(BillingProviderEvent.provider == request.provider,
        BillingProviderEvent.external_event_id == event.external_event_id))
    if existing:
        application = session.scalar(select(BillingEventApplication).where(BillingEventApplication.event_id == existing.id))
        if application is None or application.facts != billing._facts(event):
            return Decision("reconciliation_required")
        decision = recovery.retryability(session, existing, application)
        if decision.state == "completed":
            if record and record.id != existing.id and (record.status != "processed" or app is None or app.status != "processed"):
                original = recovery.retryability(session, record, app)
                return Decision("local_retry_available" if original.retryable else "reconciliation_required")
            return Decision("in_sync")
        return Decision("local_retry_available", True) if decision.retryable else Decision("reconciliation_required")
    if not sub and not event.paid_through:
        return Decision("correlation_needed")
    return Decision("evidence_available", True)


def _summary(evidence):
    """Allowlisted evidence only, safe to retain and render without raw facts."""
    summary = {"observed_at": evidence.observed_at.isoformat(), "lookup_state": evidence.state}
    if evidence.event:
        event = evidence.event
        states = {"active", "pending", "past_due", "suspended", "canceled", "cancelled", "expired"}
        summary.update(lifecycle=event.provider_status if event.provider_status in states else "other",
            event_at=event.occurred_at.isoformat(), terminal=event.terminal,
            period_start=event.period_start.isoformat(), period_end=event.period_end.isoformat(),
            paid_through=event.paid_through.isoformat() if event.paid_through else None)
    return summary


def inspect(session_factory, kind, target_id, actor, *, config=None):
    if actor.kind != "admin":
        raise PermissionError("Site administrator required.")
    with session_factory() as session:
        request = _load(session, kind, target_id)[0]
        request_id = _audit(session, kind, target_id, "billing_inspection_requested").id
        session.commit()
    # No session/transaction is retained across this future network call.
    try:
        provider = billing.enabled_provider(request.provider, config or BillingConfig.from_environment())
        lookup = getattr(provider, "inspect_evidence", None)
        if lookup is None:
            raise billing.BillingUnavailable("Inspection unsupported.")
        evidence = lookup(request)
    except billing.BillingUnavailable:
        evidence = billing.ProviderEvidence(request, billing.clock(), "unsupported")
    except Exception:
        # Adapter failures must not dump transport exceptions/credentials in UI/audit.
        evidence = billing.ProviderEvidence(request, billing.clock(), "unavailable")
    processing_attempted = False
    try:
        with session_factory() as session:
            # Discover ALL evidence-correlated accounts before taking any event
            # locks. Re-read context after serialization; no remote call follows.
            valid = _valid_evidence(request, evidence)
            summary = _summary(evidence) if valid else {}
            subscription = evidence.external_subscription_id if valid and evidence.state == "found" else request.external_subscription_id
            reference = evidence.checkout_reference if valid and evidence.state == "found" else request.checkout_reference
            locked = billing._lock_event_accounts(session, request.provider, subscription, reference)
            session.expire_all()
            target = _load(session, kind, target_id)
            current = target[0]
            # Provisioning may add a subscription while lookup runs. Permit that
            # only if the new correlation agrees with returned authoritative data.
            changed = any(getattr(current, key) != getattr(request, key) and
                          getattr(current, key) != getattr(evidence, key, None)
                          for key in ("external_subscription_id", "checkout_reference", "provider_checkout_id", "provider"))
            decision = Decision("stale_context") if changed else classify(session, request, evidence, target)
            if decision.process:
                # A newly discovered account requires a fresh inspection, never
                # account locks acquired out of order after other local locks.
                attempt = session.scalar(select(CheckoutAttempt).where(CheckoutAttempt.provider == request.provider,
                    CheckoutAttempt.reference == reference)) if reference else None
                sub = session.scalar(select(ProviderSubscription).where(ProviderSubscription.provider == request.provider,
                    ProviderSubscription.external_subscription_id == subscription))
                owners = {attempt.billing_account_id} if attempt else set()
                if sub:
                    owners.add(session.get(Membership, sub.membership_id).billing_account_id)
                if not owners or not owners.issubset(locked):
                    decision = Decision("stale_context")
            if not decision.process:
                result = _audit(session, kind, target_id, "billing_inspection_result", request_id=request_id,
                    classification=decision.classification, processing=False, evidence=summary)
                _attach_context(session, result, kind, target_id)
                result_id = result.id
                session.commit()
                return result_id
            normalized = json.dumps(billing._facts(evidence.event), sort_keys=True, separators=(",", ":"))
            event, _ = billing.receive_verified_event(session, request.provider, evidence.event,
                                                       payload_hash=sha256(normalized.encode()).hexdigest())
            event_id = event.id
            _audit(session, kind, target_id, "billing_inspection_evidence", request_id=request_id,
                   classification=decision.classification, recorded_event_id=event_id, evidence=summary)
            session.commit()  # Verified evidence survives a later application failure.
        with session_factory() as session:
            processing_attempted = True
            event = billing.apply_verified_event(session, event_id)
            result = _audit(session, kind, target_id, "billing_inspection_result", request_id=request_id,
                classification="applied" if event.status == "processed" else "still_recoverable",
                processing=True, recorded_event_id=event_id, evidence=summary)
            # Both targets are legitimate once the provider event has been stored.
            if kind == "checkout":
                result.billing_event_id = event_id
            _attach_context(session, result, kind, target_id)
            result_id = result.id
            session.commit()
            return result_id
    except (SQLAlchemyError, ValueError, TypeError, KeyError):
        with session_factory() as session:
            result = _audit(session, kind, target_id, "billing_inspection_result", request_id=request_id,
                           classification="still_recoverable", processing=processing_attempted)
            result_id = result.id
            session.commit()
            return result_id


def recent_results(session):
    rows = session.scalars(select(MembershipAuditEvent).where(
        MembershipAuditEvent.action.in_(["billing_inspection_requested", "billing_inspection_result", "billing_inspection_evidence"]))
        .order_by(MembershipAuditEvent.id.desc()).limit(30))
    return [{"at": row.created_at, "event_id": row.billing_event_id, "checkout_id": row.checkout_attempt_id,
             "evidence": {key: row.details.get("evidence", {}).get(key) for key in
                          ("observed_at", "lookup_state", "event_at", "lifecycle", "terminal", "period_start", "period_end", "paid_through")},
             "message": "Provider inspection requested" if row.action == "billing_inspection_requested" else
                 MESSAGES.get(row.details.get("classification"), "Inspection evidence retained.")}
            for row in rows]
