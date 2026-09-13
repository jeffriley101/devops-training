"""Provider adapter contract. Production adapters deliberately have no transport.

Checkout completion/provisioning will be added with real signed provider events.
Browser redirects never grant access. Tests inject an adapter; there is no fake
provider registered in the application.
"""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Protocol
from sqlalchemy import select, update
from .billing_config import BillingConfig, Plan, new_subscription_plan
from .models import ProviderSubscription, BillingProviderEvent, Membership
from .memberships import Actor, audit, clock, utc, billing_account


class BillingUnavailable(ValueError):
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


class BillingProvider(Protocol):
    def create_checkout(self, *, billing_account_id: int, plan: Plan, idempotency_key: str) -> str: ...
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


def provision_initial_subscription(session, actor, provider, plan_code, event,
                                   *, config=None, payload_hash="initial"):
    """Atomically establish a verified first paid subscription.

    The caller supplies an event only after a real adapter has verified it. This
    operation performs no network work and is also usable with the test adapter.
    """
    config = config or BillingConfig.from_environment()
    if provider not in {"paypal", "stripe"}:
        raise BillingUnavailable("Billing provider is unavailable.")
    plan = new_subscription_plan(plan_code, config)
    if event.external_subscription_id is None or event.terminal:
        raise ValueError("A live subscription event is required.")
    if event.period_end <= event.period_start:
        raise ValueError("Invalid subscription period.")
    existing_event = session.scalar(select(BillingProviderEvent).where(
        BillingProviderEvent.provider == provider,
        BillingProviderEvent.external_event_id == event.external_event_id,
    ))
    if existing_event is not None:
        if existing_event.payload_hash != payload_hash:
            raise ValueError("Provider event identity was reused with different content.")
        return existing_event, None
    existing_subscription = session.scalar(select(ProviderSubscription).where(
        ProviderSubscription.provider == provider,
        ProviderSubscription.external_subscription_id == event.external_subscription_id,
    ))
    if existing_subscription is not None:
        raise ValueError("This provider subscription is already linked.")
    account = billing_account(session, actor, create=True)
    active = session.scalar(select(Membership).where(
        Membership.billing_account_id == account.id, Membership.status == "active"))
    if active is not None:
        raise ValueError("This account already owns an active membership.")
    membership = Membership(billing_account_id=account.id, status="active", source=provider,
        plan_code=plan.code, starts_at=event.period_start, access_until=event.period_end,
        max_student_seats=5)
    session.add(membership)
    session.flush()
    subscription = ProviderSubscription(membership_id=membership.id, provider=provider,
        external_customer_id=None, external_subscription_id=event.external_subscription_id,
        plan_code=plan.code, amount_cents=plan.amount_cents, currency=plan.currency,
        interval=plan.interval, provider_status=event.provider_status,
        current_period_start=event.period_start, current_period_end=event.period_end,
        cancel_at_period_end=event.cancel_at_period_end, last_event_at=event.occurred_at)
    session.add(subscription)
    session.flush()
    audit(session, membership, actor, "membership_created", source=provider, plan_code=plan.code,
          amount_cents=plan.amount_cents)
    audit(session, membership, actor, "provider_subscription_created", provider=provider)
    if account.profile_id is not None:
        from .memberships import _add_seat
        _add_seat(session, membership, account.profile_id, actor, utc(event.period_start))
    record = BillingProviderEvent(provider=provider, external_event_id=event.external_event_id,
        payload_hash=payload_hash, status="processed", processed_at=clock())
    session.add(record)
    session.flush()
    audit(session, membership, actor, "provider_status_changed", event_id=record.id,
          status=event.provider_status)
    return record, membership


def enabled_provider(name, config):
    if name not in PROVIDERS or not config.provider_enabled(name):
        raise BillingUnavailable("Billing provider is unavailable.")
    return PROVIDERS[name]


def checkout(session, actor, provider, plan_code, *, idempotency_key, config=None):
    config = config or BillingConfig.from_environment()
    plan = new_subscription_plan(plan_code, config)
    if not config.public_subscriptions_enabled:
        raise BillingUnavailable("Public purchasing is not available yet.")
    adapter = enabled_provider(provider, config)
    account = billing_account(session, actor, create=True)
    if session.scalar(select(Membership.id).where(Membership.billing_account_id == account.id,
                                                Membership.status == "active")):
        raise ValueError("Manage the existing membership before starting another subscription.")
    return adapter.create_checkout(billing_account_id=account.id, plan=plan,
                                   idempotency_key=idempotency_key)


def process_webhook(session, provider, body, headers, *, config=None):
    config = config or BillingConfig.from_environment()
    adapter = enabled_provider(provider, config)
    event = adapter.verify_and_parse_webhook(body, headers)
    if not event.external_event_id or len(event.external_event_id) > 255 or not event.external_subscription_id:
        raise ValueError("Invalid provider event identity.")
    if any(value.tzinfo is None for value in (event.occurred_at, event.period_start, event.period_end)):
        raise ValueError("Provider timestamps must include a timezone.")
    if event.period_end <= event.period_start:
        raise ValueError("Invalid subscription period.")
    digest = sha256(body).hexdigest()
    # Serialize on the subscription before checking event IDs. Unique provider
    # event identity also protects unrelated/unknown subscription deliveries.
    session.execute(update(ProviderSubscription).where(
        ProviderSubscription.provider == provider,
        ProviderSubscription.external_subscription_id == event.external_subscription_id,
    ).values(id=ProviderSubscription.id, updated_at=ProviderSubscription.updated_at))
    subscription = session.scalar(select(ProviderSubscription).where(
        ProviderSubscription.provider == provider,
        ProviderSubscription.external_subscription_id == event.external_subscription_id,
    ).with_for_update())
    existing = session.scalar(select(BillingProviderEvent).where(
        BillingProviderEvent.provider == provider, BillingProviderEvent.external_event_id == event.external_event_id))
    if existing:
        if existing.payload_hash != digest:
            raise ValueError("Provider event identity was reused with different content.")
        return existing, False
    record = BillingProviderEvent(provider=provider, external_event_id=event.external_event_id,
                                  payload_hash=digest, status="received")
    session.add(record)
    session.flush()
    if subscription is None:
        record.status, record.error_code = "ignored", "unknown_subscription"
    elif subscription.terminated_at is not None:
        record.status, record.error_code = "ignored", "subscription_terminated"
    elif subscription.last_event_at and utc(subscription.last_event_at) >= utc(event.occurred_at):
        record.status = "ignored"
    else:
        session.execute(update(Membership).where(Membership.id == subscription.membership_id).values(
            id=Membership.id, updated_at=Membership.updated_at))
        membership = session.get(Membership, subscription.membership_id, populate_existing=True)
        if membership.status != "active" or membership.revoked_at is not None:
            record.status, record.error_code = "ignored", "membership_ended"
        else:
            subscription.provider_status = event.provider_status
            subscription.current_period_start, subscription.current_period_end = event.period_start, event.period_end
            subscription.cancel_at_period_end = event.cancel_at_period_end
            subscription.last_event_at = event.occurred_at
            if event.terminal:
                subscription.terminated_at = event.occurred_at
            membership.access_until = event.period_end
            # Cancellation keeps paid-through access. A terminal subscription
            # cannot be renewed by this path, or inherit its launch plan anew.
            if event.terminal and utc(event.period_end) <= clock():
                membership.status = "ended"
            audit(session, membership, Actor("provider"), "provider_status_changed",
                  event_id=record.id, status=event.provider_status)
            record.status = "processed"
    record.processed_at = clock()
    session.flush()
    return record, True
