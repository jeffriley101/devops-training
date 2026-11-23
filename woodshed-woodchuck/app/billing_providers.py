"""Durable authorization and explicit retry of adapter-verified payment facts.

Transport/verification runs outside transactions. Commit received facts before
application. Browser redirects never grant access.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Protocol
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .billing_config import BillingConfig, Plan, PLANS, SUBSCRIPTION_TERMS_VERSION, new_subscription_plan
from .models import (BillingAccount, ProviderSubscription, BillingProviderEvent, Membership,
                     CheckoutAttempt, BillingEventApplication, BillingPaymentEffect, MembershipAuditEvent)
from .memberships import Actor, audit, clock, utc, billing_account, lock_billing_account, owned_membership
from .billing_replacement import purchase_decision, require_new_purchase


class BillingUnavailable(ValueError):
    pass


RENEWAL_STOPPED = "stripe_renewal_stop_confirmed"


class RecoverableApplication(ValueError):
    pass


@dataclass(frozen=True)
class SubscriptionEvent:
    external_event_id: str
    external_subscription_id: str
    # For confirmed entitlement, adapters set this to the authoritative payment
    # completion time. It is not webhook receipt or local processing time.
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
    # Optional adapter evidence, retained in the existing JSON inbox. Omitted
    # for older adapters/events so their durable normalized facts stay unchanged.
    stripe_invoice: dict | None = None
    stripe_termination: dict | None = None


@dataclass(frozen=True)
class CheckoutResult:
    url: str
    external_checkout_id: str


@dataclass(frozen=True)
class EvidenceRequest:
    """Server-loaded identifiers only; never browser-supplied financial facts."""
    provider: str
    external_event_id: str | None = None
    external_subscription_id: str | None = None
    checkout_reference: str | None = None
    provider_checkout_id: str | None = None


@dataclass(frozen=True)
class ProviderEvidence:
    """Authoritative lookup result. Adapters authenticate provider/account/environment.

    A supplied event must meet the same verification standard as a webhook.
    Lifecycle-only evidence has no payment reference or paid-through entitlement.
    Native object names, raw payloads and transport credentials stay in adapters.
    """
    request: EvidenceRequest
    observed_at: datetime
    state: str  # found, not_found, unavailable, unsupported
    external_subscription_id: str | None = None
    checkout_reference: str | None = None
    provider_checkout_id: str | None = None
    event: SubscriptionEvent | None = None
    plan_code: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    interval: str | None = None
    financial_resolution_required: bool = False


class BillingProvider(Protocol):
    def create_checkout(self, *, plan: Plan, idempotency_key: str, expires_at: datetime) -> CheckoutResult: ...
    def issued_checkout_url(self, checkout_id: str, reference: str, plan_code: str) -> str: ...
    def cancel_subscription(self, *, external_subscription_id: str, external_customer_id: str) -> datetime: ...
    def create_portal_session(self, *, external_customer_id: str) -> str: ...
    def verify_and_parse_webhook(self, body: bytes, headers: dict) -> SubscriptionEvent | None: ...
    def inspect_evidence(self, request: EvidenceRequest) -> ProviderEvidence: ...


class DisabledProvider:
    def __init__(self, name):
        self.name = name

    def _unavailable(self, **kwargs):
        raise BillingUnavailable(f"{self.name.title()} billing is not configured.")

    create_checkout = cancel_subscription = create_portal_session = issued_checkout_url = _unavailable

    def verify_and_parse_webhook(self, body, headers):
        return self._unavailable()

    def inspect_evidence(self, request):
        return self._unavailable()


PROVIDERS = {name: DisabledProvider(name) for name in ("paypal", "stripe")}


def enabled_provider(name, config):
    if name not in PROVIDERS or not config.provider_enabled(name):
        raise BillingUnavailable("Billing provider is unavailable.")
    provider = PROVIDERS[name]
    if name == "stripe" and type(provider) is DisabledProvider:
        from .stripe_billing import StripeProvider
        return StripeProvider.from_environment() or provider
    return provider


def _lock(session, model, id):
    session.execute(update(model).where(model.id == id).values(id=model.id))
    return session.get(model, id, populate_existing=True)


def expire_pending_checkouts(session, account_id, at):
    """Lazily materialize the inclusive expiry boundary while preserving history.

    The caller owns the BillingAccount lock. This state ends browser checkout
    authorization; verified payment timing is evaluated independently below.
    """
    at = utc(at)
    changed = []
    for attempt in session.scalars(select(CheckoutAttempt).where(
            CheckoutAttempt.billing_account_id == account_id, CheckoutAttempt.status == "pending")):
        if utc(attempt.expires_at) <= at:
            attempt.status, attempt.updated_at = "expired", at
            changed.append(attempt)
    if changed:
        session.flush()
    return changed


def payment_within_authorization(attempt, occurred_at):
    """Provider-verified payment time uses [created_at, expires_at)."""
    occurred_at = utc(occurred_at)
    return utc(attempt.created_at) <= occurred_at < utc(attempt.expires_at)


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
    expire_pending_checkouts(session, account.id, at)
    if reference:
        attempt = session.scalar(select(CheckoutAttempt).where(
            CheckoutAttempt.reference == reference, CheckoutAttempt.billing_account_id == account.id,
            CheckoutAttempt.provider == provider, CheckoutAttempt.plan_code == plan_code))
        return reuse_checkout_attempt(session, account, actor, attempt, at)
    if not new_attempt:
        attempt = session.scalar(select(CheckoutAttempt).where(
            CheckoutAttempt.billing_account_id == account.id, CheckoutAttempt.provider == provider,
            CheckoutAttempt.plan_code == plan_code, CheckoutAttempt.status == "pending",
            CheckoutAttempt.expires_at > at).order_by(CheckoutAttempt.id.desc()))
        if attempt is not None:
            return reuse_checkout_attempt(session, account, actor, attempt, at)
    return new_checkout_attempt(session, account, actor, provider, plan, config, at)


def reuse_checkout_attempt(session, account, actor, attempt, at):
    """Both entry points hold the owner's billing lock before checking eligibility."""
    if attempt is None or attempt.billing_account_id != account.id:
        raise ValueError("Checkout attempt not found.")
    if attempt.status != "pending" or utc(attempt.expires_at) <= at:
        raise ValueError("This checkout attempt cannot be started again.")
    require_new_purchase(session, account.id, at, actor, attempt_id=attempt.id)
    return attempt


def new_checkout_attempt(session, account, actor, provider, plan, config, at):
    """Snapshot a server-selected plan under the owner's billing lock."""
    require_new_purchase(session, account.id, at, actor)
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

    def authorize(session):
        return authorize_checkout(session, actor, provider, plan_code, config=config,
                                  reference=reference, new_attempt=new_attempt)

    return start_authorized_checkout(session_factory, config, authorize)


def _website_plans(config):
    from .stripe_billing import StripeSettings
    if not config.public_subscriptions_enabled:
        raise BillingUnavailable("Public purchasing is not available yet.")
    if (not config.stripe_billing_enabled or not config.public_subscription_refund_policy.strip()
            or config.checkout_validity_seconds is None):
        raise BillingUnavailable("Website purchasing is currently unavailable.")
    settings = StripeSettings.from_environment()
    if settings is None:
        raise BillingUnavailable("Website purchasing is currently unavailable.")
    return [PLANS[code] for code in settings.prices]


def _website_pending_attempt(session, account_id, at):
    from .operator_checkout import AUTHORIZATION
    rows = session.scalars(select(CheckoutAttempt).where(
        CheckoutAttempt.billing_account_id == account_id, CheckoutAttempt.status == "pending",
        CheckoutAttempt.expires_at > at).limit(2)).all()
    if not rows:
        return None
    attempt = rows[0]
    if len(rows) > 1 or attempt.provider != "stripe" or session.scalar(select(MembershipAuditEvent.id).where(
            MembershipAuditEvent.checkout_attempt_id == attempt.id, MembershipAuditEvent.action == AUTHORIZATION)):
        raise ValueError("An existing checkout needs to be resolved before starting another subscription.")
    return attempt


def website_purchase_plans(session, actor, *, config=None):
    """An offer is read-only. Checkout rechecks eligibility under the owner's lock."""
    if actor.kind != "adult":
        return []
    try:
        plans = _website_plans(config or BillingConfig.from_environment())
        account = billing_account(session, actor)
        if account is None:
            return plans
        at = utc(clock())
        attempt = _website_pending_attempt(session, account.id, at)
    except ValueError:
        return []
    if not purchase_decision(session, account.id, at, attempt_id=attempt.id if attempt else None).allowed:
        return []
    return [plan for plan in plans if attempt is None or plan.code == attempt.plan_code]


class _VerifyExpiry(Exception):
    """Release the owner lock before consulting Stripe about an expired local attempt."""


def record_checkout_consent(session, attempt, actor, version):
    """Both purchaser paths hold the owner's billing lock. Retries keep the first assent."""
    if version != SUBSCRIPTION_TERMS_VERSION:
        raise ValueError("Review the subscription terms and agree to automatic renewal to continue.")
    action = "subscription_terms_accepted"
    if not session.scalar(select(MembershipAuditEvent.id).where(
        MembershipAuditEvent.checkout_attempt_id == attempt.id, MembershipAuditEvent.action == action,
        MembershipAuditEvent.details["version"].as_string() == version,
    )):
        session.add(MembershipAuditEvent(checkout_attempt_id=attempt.id, actor_type=actor.kind,
            actor_id=actor.id, action=action, details={"version": version, "plan_code": attempt.plan_code,
            "amount_cents": attempt.amount_cents, "currency": attempt.currency, "interval": attempt.interval}))


def website_checkout(session_factory, actor, plan_code, *, terms_version=None, config=None, reference=None, new_attempt=False):
    from .stripe_billing import PLAN_ENV
    config = config or BillingConfig.from_environment()
    if plan_code not in PLAN_ENV:
        raise ValueError("This plan is not available for a new subscription.")
    if PLANS[plan_code] not in _website_plans(config):
        raise ValueError("This plan is not available for a new subscription.")
    if actor.kind != "adult":
        raise PermissionError("An adult verifier account is required.")
    checked = None

    def authorize(session):
        account = billing_account(session, actor, create=True)
        at = utc(clock())
        expire_pending_checkouts(session, account.id, at)
        attempt = _website_pending_attempt(session, account.id, at)
        if reference is not None and (not attempt or reference != attempt.reference):
            raise ValueError("Checkout attempt not found.")
        if attempt:
            if new_attempt or attempt.plan_code != plan_code:
                raise ValueError("A checkout is already in progress. Retry the same plan.")
            attempt = reuse_checkout_attempt(session, account, actor, attempt, at)
        else:
            attempt = new_checkout_attempt(session, account, actor, "stripe", PLANS[plan_code], config, at)

        record_checkout_consent(session, attempt, actor, terms_version)
        # An expired local clock alone cannot rule out a successful payment
        # whose webhook is still in transit. Remember confirmed unpaid expiries.
        action = "stripe_unpaid_checkout_expiry_confirmed"
        verified = select(MembershipAuditEvent.id).where(
            MembershipAuditEvent.checkout_attempt_id == CheckoutAttempt.id,
            MembershipAuditEvent.action == action).exists()
        expired = session.scalars(select(CheckoutAttempt).where(
                CheckoutAttempt.billing_account_id == account.id,
                CheckoutAttempt.status == "expired", ~verified)).all()
        identities = []
        for prior in expired:
            if prior.provider != "stripe" or not prior.provider_checkout_id:
                raise ValueError("Contact Support to confirm your previous checkout before purchasing again.")
            identities.append((prior.id, prior.provider_checkout_id, prior.reference, prior.plan_code))
        if identities and checked is None:
            raise _VerifyExpiry(identities)
        for identity in identities:
            if identity not in checked:
                raise BillingUnavailable("Your checkout changed during confirmation. Please try again.")
            session.add(MembershipAuditEvent(checkout_attempt_id=identity[0], action=action, actor_type="provider"))
        return attempt

    try:
        return start_authorized_checkout(session_factory, config, authorize)
    except _VerifyExpiry as error:
        identities, = error.args
        provider = enabled_provider("stripe", config)
        for _, checkout_id, checkout_reference, prior_plan in identities:
            if not provider.confirm_unpaid_expiry(checkout_id, checkout_reference, prior_plan):
                raise ValueError("Your previous checkout needs confirmation before another purchase.") from None
        checked = set(identities)
    return start_authorized_checkout(session_factory, config, authorize)


def start_authorized_checkout(session_factory, config, authorize):
    """Commit the authorized attempt before contacting its provider."""
    with session_factory() as session:
        attempt = authorize(session)
        provider = attempt.provider
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


def cancel_website_renewal(session_factory, actor, membership_id):
    with session_factory() as session:
        member = owned_membership(session, membership_id, actor)
        if member.source != "stripe":
            raise LookupError("Website subscription unavailable.")
        sub = session.scalar(select(ProviderSubscription).where(
            ProviderSubscription.membership_id == member.id, ProviderSubscription.provider == "stripe"))
        if sub is None or not sub.external_customer_id:
            raise BillingUnavailable("The Stripe subscription could not be matched.")
        subscription_id, customer_id = sub.external_subscription_id, sub.external_customer_id

    # The update sets a desired state. A retry after a lost response reads Stripe first.
    provider = enabled_provider("stripe", BillingConfig.from_environment())
    effective = provider.cancel_subscription(external_subscription_id=subscription_id,
                                             external_customer_id=customer_id)
    with session_factory.begin() as session:
        member = owned_membership(session, membership_id, actor, lock=True)
        details = {"effective_at": effective.isoformat(), "subscription_id": subscription_id}
        prior = session.scalar(select(MembershipAuditEvent.id).where(
            MembershipAuditEvent.membership_id == member.id, MembershipAuditEvent.action == RENEWAL_STOPPED,
            MembershipAuditEvent.details["effective_at"].as_string() == details["effective_at"]))
        if prior is None:
            audit(session, member, actor, RENEWAL_STOPPED, **details)
    return effective


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
    if event.stripe_termination is not None:
        binding = event.stripe_termination
        if (not isinstance(binding, dict) or not all(isinstance(binding.get(key), str) and binding[key]
                for key in ("account_id", "customer_id", "checkout_id", "plan_code"))
                or not event.terminal or event.provider_status != "canceled" or not event.checkout_reference
                or event.stripe_invoice is not None or event.paid_through is not None):
            raise ValueError("Invalid Stripe termination evidence.")
    if event.paid_through is not None:
        if (not isinstance(event.paid_through, datetime) or event.paid_through.utcoffset() is None
                or event.paid_through <= event.period_start or not isinstance(event.payment_reference, str)
                or not 1 <= len(event.payment_reference) <= 255):
            raise ValueError("Invalid confirmed entitlement.")
    facts = {key: value.isoformat() if isinstance(value, datetime) else value for key, value in asdict(event).items()}
    if event.stripe_invoice is None:
        facts.pop("stripe_invoice")
    if event.stripe_termination is None:
        facts.pop("stripe_termination")
    return facts


def receive_verified_event(session, provider, event, *, payload_hash):
    """Persist adapter-verified facts; caller commits BEFORE apply/retry.
    Database unavailability must reject delivery so the provider can redeliver.
    """
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider.")
    if not isinstance(payload_hash, str) or not 1 <= len(payload_hash) <= 64:
        raise ValueError("Invalid payload hash.")
    facts = _facts(event)
    _lock_event_accounts(session, provider, event.external_subscription_id, event.checkout_reference)
    existing = session.scalar(select(BillingProviderEvent).where(
        BillingProviderEvent.provider == provider, BillingProviderEvent.external_event_id == event.external_event_id))
    if existing:
        application = session.scalar(select(BillingEventApplication).where(BillingEventApplication.event_id == existing.id))
        # Verified lookup and webhook envelopes can differ for the same event.
        # Identity is bound to ALL normalized facts; retain the first receipt hash.
        if application and application.facts != facts:
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


def _lock_event_accounts(session, provider, external_subscription_id, reference):
    """Inbox ingestion and application serialize with purchase authorization.

    Resolve both correlations, locking account IDs in order if they disagree.
    Unknown events still enter the inbox and acquire their owner lock on retry.
    """
    ids = set(session.scalars(select(Membership.billing_account_id).join(ProviderSubscription).where(
        ProviderSubscription.provider == provider,
        ProviderSubscription.external_subscription_id == external_subscription_id)))
    if reference:
        ids.update(session.scalars(select(CheckoutAttempt.billing_account_id).where(
            CheckoutAttempt.provider == provider, CheckoutAttempt.reference == reference)))
    for account_id in sorted(ids):
        lock_billing_account(session, account_id)
    return ids


def _provision(session, record, application, event):
    if event.paid_through is None:
        raise RecoverableApplication("awaiting_paid_entitlement")
    attempt = session.scalar(select(CheckoutAttempt).where(
        CheckoutAttempt.reference == application.checkout_reference, CheckoutAttempt.provider == record.provider))
    if attempt is None:
        raise RecoverableApplication("unknown_checkout")
    attempt = _lock(session, CheckoutAttempt, attempt.id)
    _check_stripe_binding(record, event, attempt=attempt)
    # Adapter-verified payment occurrence controls authorization. Webhook receipt
    # and local processing may arrive later; an expired state preserves history
    # and prevents browser reuse without erasing a payment completed in time.
    if not payment_within_authorization(attempt, event.occurred_at) or attempt.status == "failed":
        raise RecoverableApplication("checkout_authorization_expired")
    if attempt.subscription_id is not None:
        raise RecoverableApplication("checkout_already_consumed")
    if event.terminal:
        raise RecoverableApplication("initial_subscription_terminated")
    account = session.get(BillingAccount, attempt.billing_account_id)
    actor = Actor("student", account.profile_id) if account.profile_id else Actor("adult", account.verifier_id)
    require_new_purchase(session, account.id, utc(clock()), actor, attempt_id=attempt.id,
                         applying_subscription=(record.provider, event.external_subscription_id))
    member = Membership(billing_account_id=account.id, status="active", source=record.provider,
        plan_code=attempt.plan_code, starts_at=event.period_start, access_until=event.paid_through, max_student_seats=5)
    session.add(member)
    session.flush()
    sub = ProviderSubscription(membership_id=member.id, provider=record.provider,
        external_subscription_id=event.external_subscription_id, plan_code=attempt.plan_code,
        external_customer_id=event.stripe_invoice["customer_id"] if event.stripe_invoice else None,
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
    if event.stripe_invoice:
        attempt.provider_checkout_id = event.stripe_invoice["checkout_id"]
    return sub


def _check_stripe_binding(record, event, *, attempt=None, sub=None):
    """Compare authenticated Stripe facts with local authorization under locks."""
    facts = event.stripe_invoice if event.stripe_invoice is not None else event.stripe_termination
    if facts is None:
        return
    price = sub or attempt
    if record.provider != "stripe" or price is None or facts.get("plan_code") != price.plan_code:
        raise RecoverableApplication("stripe_price_conflict")
    if event.stripe_invoice is not None and (facts.get("amount_cents"), facts.get("currency"), facts.get("interval")) != (
            price.amount_cents, price.currency, price.interval):
        raise RecoverableApplication("stripe_price_conflict")
    if event.stripe_termination is not None and (not attempt or attempt.provider_checkout_id != facts["checkout_id"]):
        raise RecoverableApplication("stripe_checkout_conflict")
    if attempt and attempt.provider_checkout_id not in (None, facts.get("checkout_id")):
        raise RecoverableApplication("stripe_checkout_conflict")
    if sub and sub.external_customer_id != facts.get("customer_id"):
        raise RecoverableApplication("stripe_customer_conflict")


def lock_verified_event(session, event_id):
    """Shared account -> event locking for automatic and admin application."""
    record = session.get(BillingProviderEvent, event_id)
    if record is None:
        raise ValueError("Event not found.")
    application = session.scalar(select(BillingEventApplication).where(BillingEventApplication.event_id == event_id))
    locked_accounts = _lock_event_accounts(session, record.provider, application.external_subscription_id,
        application.checkout_reference) if application else set()
    record = _lock(session, BillingProviderEvent, event_id)
    if application:
        session.refresh(application)
    return record, application, locked_accounts


def apply_verified_event(session, event_id):
    """Explicit retry; no transport. Commit recoverable outcomes."""
    return apply_locked_event(session, *lock_verified_event(session, event_id))


def apply_locked_event(session, record, application, locked_accounts):
    """Caller obtained lock_verified_event. Never acquire new account locks here.

    Savepoint rolls back partial application. Unexpected errors may escape; the
    separately committed inbox survives and remains available for retry.
    """
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
            if ((attempt and attempt.billing_account_id not in locked_accounts) or
                    (sub and session.get(Membership, sub.membership_id).billing_account_id not in locked_accounts)):
                # Correlation appeared after account discovery. Retry from the
                # outer transaction rather than acquiring account locks late.
                raise RecoverableApplication("subscription_correlation_retry")
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
            _check_stripe_binding(record, event, attempt=attempt, sub=sub)
            if attempt and (attempt.subscription_id != sub.id or attempt.billing_account_id != member.billing_account_id):
                raise RecoverableApplication("subscription_correlation_conflict")
            # A terminal-only receipt may complete the history of a retired or
            # refunded membership. It can never carry a payment or grant access.
            if (member.status != "active" or member.revoked_at) and event.stripe_termination is None:
                raise RecoverableApplication("membership_ended")
            if event.stripe_termination and sub.terminated_at and utc(sub.terminated_at) != event.occurred_at:
                raise RecoverableApplication("termination_date_conflict")
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
            if not event.stripe_invoice and sub.last_event_at and utc(sub.last_event_at) == event.occurred_at:
                if (sub.provider_status, sub.cancel_at_period_end, bool(sub.terminated_at),
                    utc(sub.current_period_start), utc(sub.current_period_end)) != (
                    event.provider_status, event.cancel_at_period_end, event.terminal, event.period_start, event.period_end):
                    lifecycle_error = "equal_time_lifecycle_conflict"
            # A paid invoice proves paid coverage, not current subscription status.
            # This minimal Stripe adapter does not manufacture lifecycle facts.
            if not event.stripe_invoice and (sub.last_event_at is None or utc(sub.last_event_at) < event.occurred_at):
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
    if event is None:
        return None, False  # Verified but outside this adapter's supported events.
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
