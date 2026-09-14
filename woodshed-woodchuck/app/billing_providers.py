"""Durable authorization and explicit retry of adapter-verified payment facts.

Transport/verification runs outside transactions. Commit received facts before
application. Browser redirects never grant access. Live adapters remain disabled.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Protocol
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .billing_config import BillingConfig, Plan, new_subscription_plan
from .models import (BillingAccount, ProviderSubscription, BillingProviderEvent, Membership,
                     CheckoutAttempt, BillingEventApplication, BillingPaymentEffect)
from .memberships import Actor, audit, clock, utc, billing_account


class BillingUnavailable(ValueError):
    pass


class RecoverableApplication(ValueError):
    pass


@dataclass(frozen=True)
class SubscriptionEvent:
    external_event_id: str
    external_subscription_id: str
    occurred_at: datetime
    provider_status: str
    period_start: datetime
    period_end: datetime
    cancel_at_period_end: bool = False
    terminal: bool = False
    checkout_reference: str | None = None
    # Only verified payment may supply these; billing periods are not entitlement.
    paid_through: datetime | None = None
    payment_reference: str | None = None


@dataclass(frozen=True)
class CheckoutResult:
    url: str
    external_checkout_id: str


class BillingProvider(Protocol):
    def create_checkout(self, *, plan: Plan, idempotency_key: str, expires_at: datetime) -> CheckoutResult: ...
    def cancel_subscription(self, *, external_subscription_id: str) -> None: ...
    def create_portal_session(self, *, external_customer_id: str) -> str: ...
    def verify_and_parse_webhook(self, body: bytes, headers: dict) -> SubscriptionEvent: ...


class DisabledProvider:
    def __init__(self, name):
        self.name = name

    def _unavailable(self, **kwargs):
        raise BillingUnavailable(f"{self.name.title()} billing is not configured.")

    create_checkout = cancel_subscription = create_portal_session = _unavailable

    def verify_and_parse_webhook(self, body, headers):
        return self._unavailable()


PROVIDERS = {name: DisabledProvider(name) for name in ("paypal", "stripe")}


def enabled_provider(name, config):
    if name not in PROVIDERS or not config.provider_enabled(name):
        raise BillingUnavailable("Billing provider is unavailable.")
    return PROVIDERS[name]


def _lock(session, model, id):
    session.execute(update(model).where(model.id == id).values(id=model.id))
    return session.get(model, id, populate_existing=True)


def authorize_checkout(session, actor, provider, plan_code, *, config=None, reference=None, new_attempt=False):
    """Local only: duplicate submissions reuse a pending attempt; explicit new
    attempts remain possible. The caller commits before any provider transport.
    """
    config = config or BillingConfig.from_environment()
    plan = new_subscription_plan(plan_code, config) if not reference else None
    if not config.public_subscriptions_enabled:
        raise BillingUnavailable("Public purchasing is not available yet.")
    enabled_provider(provider, config)
    account = billing_account(session, actor, create=True)
    at = utc(clock())
    if session.scalar(select(Membership.id).where(Membership.billing_account_id == account.id, Membership.status == "active")):
        raise ValueError("Manage the existing membership before starting another subscription.")
    if session.scalar(select(CheckoutAttempt.id).where(CheckoutAttempt.billing_account_id == account.id,
                                                      CheckoutAttempt.status == "recoverable")):
        raise ValueError("A paid checkout needs processing review before another purchase.")
    if session.scalar(select(BillingEventApplication.id).join(
            CheckoutAttempt, CheckoutAttempt.reference == BillingEventApplication.checkout_reference).join(
            BillingProviderEvent, BillingProviderEvent.id == BillingEventApplication.event_id).where(
            CheckoutAttempt.billing_account_id == account.id,
            CheckoutAttempt.provider == BillingProviderEvent.provider,
            BillingEventApplication.status != "processed",
            BillingEventApplication.facts["paid_through"].as_string().is_not(None))):
        # The inbox commit can survive a crash before application updates the
        # attempt. Never initiate another purchase while that payment awaits work.
        raise ValueError("A paid checkout needs processing review before another purchase.")
    if reference:
        attempt = session.scalar(select(CheckoutAttempt).where(
            CheckoutAttempt.reference == reference, CheckoutAttempt.billing_account_id == account.id,
            CheckoutAttempt.provider == provider, CheckoutAttempt.plan_code == plan_code))
        if attempt is None:
            raise ValueError("Checkout attempt not found.")
        if attempt.status != "pending" or utc(attempt.expires_at) <= at:
            raise ValueError("This checkout attempt cannot be started again.")
        return attempt
    if not new_attempt:
        attempt = session.scalar(select(CheckoutAttempt).where(
            CheckoutAttempt.billing_account_id == account.id, CheckoutAttempt.provider == provider,
            CheckoutAttempt.plan_code == plan_code, CheckoutAttempt.status == "pending",
            CheckoutAttempt.expires_at > at).order_by(CheckoutAttempt.id.desc()))
        if attempt is not None:
            return attempt
    validity = config.checkout_validity_seconds
    if not isinstance(validity, int) or isinstance(validity, bool) or not 0 < validity < 1_000_000_000:
        raise BillingUnavailable("Checkout validity has not been configured.")
    attempt = CheckoutAttempt(reference=token_urlsafe(32), billing_account_id=account.id,
        provider=provider, plan_code=plan.code, amount_cents=plan.amount_cents,
        currency=plan.currency, interval=plan.interval, created_at=at, updated_at=at,
        expires_at=at + timedelta(seconds=validity), status="pending")
    session.add(attempt)
    session.flush()
    return attempt


def checkout(session_factory, actor, provider, plan_code, *, config=None, reference=None, new_attempt=False):
    config = config or BillingConfig.from_environment()
    with session_factory() as session:
        attempt = authorize_checkout(session, actor, provider, plan_code, config=config,
                                     reference=reference, new_attempt=new_attempt)
        attempt_id, opaque = attempt.id, attempt.reference
        plan = Plan(attempt.plan_code, "Full Access", attempt.amount_cents, attempt.interval, attempt.currency)
        expires = utc(attempt.expires_at)
        session.commit()
    # Unknown remote outcome leaves SAME reference retryable. Adapters must honor
    # the idempotency key and expiration. No DB transaction spans transport.
    result = enabled_provider(provider, config).create_checkout(plan=plan, idempotency_key=opaque, expires_at=expires)
    if not isinstance(result, CheckoutResult) or not result.external_checkout_id:
        raise ValueError("Invalid provider checkout response.")
    with session_factory() as session:
        attempt = _lock(session, CheckoutAttempt, attempt_id)
        if attempt.provider_checkout_id and attempt.provider_checkout_id != result.external_checkout_id:
            raise ValueError("Provider changed the checkout reference on retry.")
        attempt.provider_checkout_id = result.external_checkout_id
        session.commit()
    return result.url


def _facts(event):
    if type(event.cancel_at_period_end) is not bool or type(event.terminal) is not bool:
        raise ValueError("Invalid lifecycle flags.")
    for value in (event.external_event_id, event.external_subscription_id, event.provider_status):
        if not isinstance(value, str) or not value or len(value) > 255:
            raise ValueError("Invalid provider event identity/status.")
    if len(event.provider_status) > 50:
        raise ValueError("Invalid provider status.")
    for value in (event.occurred_at, event.period_start, event.period_end):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Provider timestamps must include a timezone.")
    if event.period_end <= event.period_start:
        raise ValueError("Invalid subscription period.")
    if event.checkout_reference is not None and (not isinstance(event.checkout_reference, str)
                                                or not 1 <= len(event.checkout_reference) <= 64):
        raise ValueError("Invalid checkout reference.")
    if (event.paid_through is None) != (event.payment_reference is None):
        raise ValueError("Confirmed entitlement requires a payment reference and paid-through date.")
    if event.paid_through is not None:
        if (not isinstance(event.paid_through, datetime) or event.paid_through.utcoffset() is None
                or event.paid_through <= event.period_start or not isinstance(event.payment_reference, str)
                or not 1 <= len(event.payment_reference) <= 255):
            raise ValueError("Invalid confirmed entitlement.")
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in asdict(event).items()}


def receive_verified_event(session, provider, event, *, payload_hash):
    """Persist adapter-verified facts; caller commits BEFORE apply/retry.
    Database unavailability must reject delivery so the provider can redeliver.
    """
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider.")
    if not isinstance(payload_hash, str) or not 1 <= len(payload_hash) <= 64:
        raise ValueError("Invalid payload hash.")
    facts = _facts(event)
    existing = session.scalar(select(BillingProviderEvent).where(
        BillingProviderEvent.provider == provider, BillingProviderEvent.external_event_id == event.external_event_id))
    if existing:
        application = session.scalar(select(BillingEventApplication).where(BillingEventApplication.event_id == existing.id))
        if existing.payload_hash != payload_hash or (application and application.facts != facts):
            raise ValueError("Provider event identity was reused with different content.")
        if application is None:
            raise ValueError("Legacy event requires explicit reconciliation.")
        return existing, False
    record = BillingProviderEvent(provider=provider, external_event_id=event.external_event_id,
                                  payload_hash=payload_hash, status="received", received_at=clock())
    session.add(record)
    session.flush()
    session.add(BillingEventApplication(event_id=record.id, external_subscription_id=event.external_subscription_id,
        checkout_reference=event.checkout_reference, facts=facts, status="pending"))
    session.flush()
    return record, True


def _event(application):
    facts = dict(application.facts)
    for key in ("occurred_at", "period_start", "period_end", "paid_through"):
        if facts.get(key) is not None:
            facts[key] = datetime.fromisoformat(facts[key])
    return SubscriptionEvent(**facts)


def _provision(session, record, application, event):
    if event.paid_through is None:
        raise RecoverableApplication("awaiting_paid_entitlement")
    attempt = session.scalar(select(CheckoutAttempt).where(
        CheckoutAttempt.reference == application.checkout_reference, CheckoutAttempt.provider == record.provider))
    if attempt is None:
        raise RecoverableApplication("unknown_checkout")
    attempt = _lock(session, CheckoutAttempt, attempt.id)
    # Payment received within authorization may retry after expiry. Late initial
    # evidence remains recoverable for review, not an indefinite sale reservation.
    if (utc(record.received_at) >= utc(attempt.expires_at) or event.occurred_at >= utc(attempt.expires_at)
            or event.occurred_at < utc(attempt.created_at) or attempt.status in {"expired", "failed"}):
        raise RecoverableApplication("checkout_authorization_expired")
    if attempt.subscription_id is not None:
        raise RecoverableApplication("checkout_already_consumed")
    if event.terminal:
        raise RecoverableApplication("initial_subscription_terminated")
    account = _lock(session, BillingAccount, attempt.billing_account_id)
    actor = Actor("student", account.profile_id) if account.profile_id else Actor("adult", account.verifier_id)
    if session.scalar(select(Membership.id).where(Membership.billing_account_id == account.id, Membership.status == "active")):
        raise RecoverableApplication("owner_membership_conflict")
    member = Membership(billing_account_id=account.id, status="active", source=record.provider,
        plan_code=attempt.plan_code, starts_at=event.period_start, access_until=event.paid_through, max_student_seats=5)
    session.add(member)
    session.flush()
    sub = ProviderSubscription(membership_id=member.id, provider=record.provider,
        external_subscription_id=event.external_subscription_id, plan_code=attempt.plan_code,
        amount_cents=attempt.amount_cents, currency=attempt.currency, interval=attempt.interval,
        provider_status=event.provider_status, cancel_at_period_end=event.cancel_at_period_end,
        current_period_start=event.period_start, current_period_end=event.period_end)
    session.add(sub)
    session.flush()
    if account.profile_id:
        from .memberships import _add_seat
        _add_seat(session, member, account.profile_id, actor, utc(clock()))
    audit(session, member, actor, "membership_created", source=record.provider, plan_code=attempt.plan_code,
          amount_cents=attempt.amount_cents)
    audit(session, member, actor, "provider_subscription_created", provider=record.provider)
    attempt.subscription_id, attempt.status = sub.id, "completed"
    return sub


def apply_verified_event(session, event_id):
    """Explicit retry, no transport. Savepoint rolls back partial application.
    Commit recoverable outcomes. Unexpected database errors may escape; the
    separately committed inbox survives and remains available for retry.
    """
    record = _lock(session, BillingProviderEvent, event_id)
    if record is None:
        raise ValueError("Event not found.")
    application = session.scalar(select(BillingEventApplication).where(BillingEventApplication.event_id == event_id))
    if application is None:
        raise ValueError("Legacy event requires explicit reconciliation.")
    if application.status == "processed":
        return record
    application.attempts += 1
    session.flush()
    event = _event(application)
    try:
        with session.begin_nested():
            attempt = session.scalar(select(CheckoutAttempt).where(
                CheckoutAttempt.reference == application.checkout_reference, CheckoutAttempt.provider == record.provider))
            sub = session.scalar(select(ProviderSubscription).where(
                ProviderSubscription.provider == record.provider,
                ProviderSubscription.external_subscription_id == event.external_subscription_id))
            if sub is None and attempt:
                # Only initial provisioning needs the identity lock. Taking it
                # for renewals would invert seat management's membership ->
                # student order. Recheck after waiting for another provisioner.
                owner = session.get(BillingAccount, attempt.billing_account_id)
                actor = Actor("student", owner.profile_id) if owner.profile_id else Actor("adult", owner.verifier_id)
                billing_account(session, actor, create=True)
                _lock(session, BillingAccount, attempt.billing_account_id)
                sub = session.scalar(select(ProviderSubscription).where(
                    ProviderSubscription.provider == record.provider,
                    ProviderSubscription.external_subscription_id == event.external_subscription_id))
                if sub is not None:
                    # Release the newly acquired identity lock via the savepoint
                    # before retrying the existing-subscription lock path.
                    raise RecoverableApplication("concurrent_provisioning_retry")
            if attempt:
                # SQLAlchemy may retain the object read before lock acquisition.
                # Completed correlation must be read from the committed row.
                session.refresh(attempt)
            if sub is None:
                sub = _provision(session, record, application, event)
            elif application.checkout_reference and attempt is None:
                raise RecoverableApplication("unknown_checkout")
            sub = _lock(session, ProviderSubscription, sub.id)
            member = _lock(session, Membership, sub.membership_id)
            if attempt and (attempt.subscription_id != sub.id or attempt.billing_account_id != member.billing_account_id):
                raise RecoverableApplication("subscription_correlation_conflict")
            if member.status != "active" or member.revoked_at:
                raise RecoverableApplication("membership_ended")
            if event.paid_through is not None:
                effect = session.scalar(select(BillingPaymentEffect).where(
                    BillingPaymentEffect.subscription_id == sub.id,
                    BillingPaymentEffect.payment_reference == event.payment_reference))
                if effect and utc(effect.paid_through) != event.paid_through:
                    raise RecoverableApplication("payment_reference_conflict")
                if effect is None:
                    if sub.terminated_at and event.occurred_at > utc(sub.terminated_at):
                        raise RecoverableApplication("payment_after_termination")
                    if member.access_until is None:
                        raise RecoverableApplication("missing_paid_through_baseline")
                    member.access_until = max(utc(member.access_until), event.paid_through)
                    session.add(BillingPaymentEffect(subscription_id=sub.id, payment_reference=event.payment_reference,
                        paid_through=event.paid_through, event_id=record.id))
                    audit(session, member, Actor("provider"), "payment_entitlement_confirmed", event_id=record.id)
            # Lifecycle ordering never suppresses older independent payments.
            # Equal-time conflicting status remains available for reconciliation.
            lifecycle_error = None
            if sub.last_event_at and utc(sub.last_event_at) == event.occurred_at:
                if (sub.provider_status, sub.cancel_at_period_end, bool(sub.terminated_at),
                    utc(sub.current_period_start), utc(sub.current_period_end)) != (
                    event.provider_status, event.cancel_at_period_end, event.terminal, event.period_start, event.period_end):
                    lifecycle_error = "equal_time_lifecycle_conflict"
            if sub.last_event_at is None or utc(sub.last_event_at) < event.occurred_at:
                if sub.terminated_at and not event.terminal:
                    raise RecoverableApplication("subscription_terminated")
                sub.provider_status = event.provider_status
                sub.current_period_start, sub.current_period_end = event.period_start, event.period_end
                sub.cancel_at_period_end, sub.last_event_at = event.cancel_at_period_end, event.occurred_at
                if event.terminal:
                    sub.terminated_at = event.occurred_at
                audit(session, member, Actor("provider"), "provider_status_changed", event_id=record.id, status=event.provider_status)
            application.status = "recoverable" if lifecycle_error else "processed"
            application.error_code = lifecycle_error
            record.status = "failed" if lifecycle_error else "processed"
            record.processed_at = None if lifecycle_error else clock()
            record.error_code = lifecycle_error
            session.flush()
    except (ValueError, IntegrityError) as error:
        application.status = "recoverable"
        application.error_code = str(error) if isinstance(error, RecoverableApplication) else "provisioning_conflict"
        record.status, record.error_code, record.processed_at = "failed", application.error_code, None
        if application.checkout_reference:
            attempt = session.scalar(select(CheckoutAttempt).where(
                CheckoutAttempt.reference == application.checkout_reference, CheckoutAttempt.provider == record.provider))
            if attempt:
                attempt = _lock(session, CheckoutAttempt, attempt.id)
            if attempt and attempt.status == "pending" and event.paid_through is not None:
                attempt.status = "recoverable"
    session.flush()
    return record


def process_webhook(session_factory, provider, body, headers, *, config=None):
    config = config or BillingConfig.from_environment()
    event = enabled_provider(provider, config).verify_and_parse_webhook(body, headers)
    with session_factory() as session:
        try:
            record, fresh = receive_verified_event(session, provider, event, payload_hash=sha256(body).hexdigest())
            event_id = record.id
            session.commit()
        except IntegrityError:
            session.rollback()
            record, fresh = receive_verified_event(session, provider, event, payload_hash=sha256(body).hexdigest())
            event_id = record.id
            session.commit()
    with session_factory() as session:
        record = apply_verified_event(session, event_id)
        session.commit()
        return record, fresh
