"""Stripe Checkout, paid invoice and final subscription lifecycle evidence.

Checkout creates its Customer. Existing columns retain cs_/cus_/sub_/in_ IDs;
the existing verified-facts JSON retains Price and PaymentIntent correlation.
Only paid subscription invoices grant access. Final cancellation is recorded separately.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import os
from urllib.parse import urlsplit

import stripe

from .billing_config import PLANS
from .billing_providers import (
    BillingUnavailable, CheckoutResult, DisabledProvider, ProviderEvidence, SubscriptionEvent,
)

API_VERSION = "2026-08-26.dahlia"  # stripe 15.6.1; webhook endpoint must match.
PLAN_ENV = {
    "full_monthly_7": "STRIPE_PRICE_FULL_MONTHLY_7",
    "full_annual_49": "STRIPE_PRICE_FULL_ANNUAL_49",
}


def require(condition):
    if not condition:
        raise ValueError("Stripe evidence does not match the supported purchase.")


def identifier(value, prefix):
    require(isinstance(value, str) and value.startswith(prefix) and len(value) <= 255)
    return value


def timestamp(value):
    require(type(value) is int and value > 0)
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError):
        raise ValueError("Invalid Stripe timestamp.") from None


def hosted_url(checkout):
    url = urlsplit(checkout.get("url") or "")
    require(url.scheme == "https" and url.netloc == "checkout.stripe.com")
    return checkout["url"]


@dataclass(frozen=True)
class StripeSettings:
    secret_key: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    account_id: str
    monthly_price: str
    annual_price: str
    base_url: str
    mode: str

    @classmethod
    def from_environment(cls):
        values = [os.getenv(key, "").strip() for key in (
            "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_ACCOUNT_ID",
            *PLAN_ENV.values(), "PUBLIC_BASE_URL")]
        key, signing, account, monthly, annual, base = values
        mode = os.getenv("STRIPE_MODE", "").strip()
        if (mode not in ("test", "live") or not all((key, signing, account, base))
                or not (monthly or annual)):
            return None
        try:
            origin = urlsplit(base)
        except ValueError:
            return None
        if (not key.startswith((f"sk_{mode}_", f"rk_{mode}_")) or not signing.startswith("whsec_")
                or not account.startswith("acct_")
                or any(value and not value.startswith("price_") for value in (monthly, annual))
                or monthly == annual
                or origin.username or origin.password or origin.query or origin.fragment
                or origin.path not in ("", "/") or not origin.hostname
                or not (origin.scheme == "https" or
                        mode == "test" and origin.scheme == "http"
                        and origin.hostname in ("localhost", "127.0.0.1"))):
            return None
        return cls(key, signing, account, monthly, annual, base.rstrip("/"), mode)

    @property
    def livemode(self):
        return self.mode == "live"

    @property
    def checkout_id_prefix(self):
        return f"cs_{self.mode}_"

    @property
    def prices(self):
        return {code: value for code, value in zip(PLAN_ENV, (self.monthly_price, self.annual_price)) if value}


class StripeProvider(DisabledProvider):
    def __init__(self, settings, *, client=None):
        super().__init__("stripe")
        self.settings = settings
        self.client = client or stripe.StripeClient(
            settings.secret_key, stripe_version=API_VERSION, max_network_retries=2,
            http_client=stripe.RequestsClient(timeout=15))

    @classmethod
    def from_environment(cls):
        settings = StripeSettings.from_environment()
        return cls(settings) if settings else None

    def _call(self, method, *args, **kwargs):
        try:
            result = method(*args, **kwargs)
            return result.to_dict() if isinstance(result, stripe.StripeObject) else result
        except stripe.StripeError:
            # Never expose transport details, request bodies or credentials.
            raise BillingUnavailable("Stripe is temporarily unavailable.") from None

    def _account(self):
        account = self._call(self.client.v1.accounts.retrieve_current)
        require(account.get("id") == self.settings.account_id)

    def _price(self, price_id):
        codes = [code for code, value in self.settings.prices.items() if value == price_id]
        require(len(codes) == 1)
        plan = PLANS[codes[0]]
        price = self._call(self.client.v1.prices.retrieve, price_id)
        recurring = price.get("recurring") or {}
        require(price.get("id") == price_id and price.get("livemode") is self.settings.livemode
                and price.get("type") == "recurring" and price.get("billing_scheme") == "per_unit"
                and price.get("unit_amount") == plan.amount_cents and price.get("currency") == "usd"
                and recurring.get("interval") == plan.interval and recurring.get("interval_count") == 1
                and recurring.get("usage_type") == "licensed")
        return plan

    def create_checkout(self, *, plan, idempotency_key, expires_at):
        require(plan.code in self.settings.prices)
        self._account()
        expected = self._price(self.settings.prices[plan.code])
        require((plan.amount_cents, plan.currency, plan.interval) ==
                (expected.amount_cents, expected.currency, expected.interval))
        metadata = {"woodshed_checkout_reference": idempotency_key, "woodshed_plan_code": plan.code}
        result = self._call(self.client.v1.checkout.sessions.create, params={
            "mode": "subscription", "payment_method_types": ["card"],
            "wallet_options": {"link": {"display": "never"}},
            "line_items": [{"price": self.settings.prices[plan.code], "quantity": 1}],
            "client_reference_id": idempotency_key, "metadata": metadata,
            "subscription_data": {"metadata": metadata},
            "automatic_tax": {"enabled": False}, "allow_promotion_codes": False,
            "expires_at": int(expires_at.timestamp()),
            "success_url": self.settings.base_url + "/membership?checkout=returned",
            "cancel_url": self.settings.base_url + "/membership",
        }, options={"idempotency_key": idempotency_key})
        require(result.get("livemode") is self.settings.livemode)
        return CheckoutResult(hosted_url(result), identifier(result.get("id"), self.settings.checkout_id_prefix))

    def issued_checkout_url(self, checkout_id, reference, plan_code):
        """Open only the session the private operator already issued."""
        self._account()
        checkout = self._call(self.client.v1.checkout.sessions.retrieve,
                              identifier(checkout_id, self.settings.checkout_id_prefix))
        metadata = checkout.get("metadata") or {}
        require(checkout.get("object") == "checkout.session" and checkout.get("id") == checkout_id
                and checkout.get("livemode") is self.settings.livemode and checkout.get("mode") == "subscription"
                and checkout.get("client_reference_id") == reference and metadata.get("woodshed_plan_code") == plan_code
                and checkout.get("status") == "open" and checkout.get("payment_status") == "unpaid")
        return hosted_url(checkout)

    def cancel_subscription(self, *, external_subscription_id, external_customer_id):
        subscription_id = identifier(external_subscription_id, "sub_")
        customer_id = identifier(external_customer_id, "cus_")
        self._account()

        def renewal_stop_date(subscription):
            require(subscription.get("object") == "subscription" and subscription.get("id") == subscription_id
                    and subscription.get("customer") == customer_id and subscription.get("livemode") is self.settings.livemode)
            if subscription.get("status") == "canceled":
                return timestamp(subscription.get("ended_at"))
            items = subscription.get("items") or {}
            require(items.get("has_more") is False and len(items.get("data", [])) == 1)
            end = timestamp(items["data"][0].get("current_period_end"))
            if subscription.get("cancel_at") is not None:
                scheduled = timestamp(subscription["cancel_at"])
                return scheduled if scheduled <= end else None
            return end if subscription.get("cancel_at_period_end") is True else None

        subscriptions = self.client.v1.subscriptions
        effective = renewal_stop_date(self._call(subscriptions.retrieve, subscription_id))
        if effective is None:
            effective = renewal_stop_date(self._call(subscriptions.update, subscription_id,
                params={"cancel_at_period_end": True, "proration_behavior": "none"}))
        require(effective is not None)
        return effective

    def confirm_unpaid_expiry(self, checkout_id, reference, plan_code):
        """Only a definitively expired, unused session can precede a replacement."""
        self._account()
        checkout = self._call(self.client.v1.checkout.sessions.retrieve,
                              identifier(checkout_id, self.settings.checkout_id_prefix))
        metadata = checkout.get("metadata") or {}
        return (checkout.get("object") == "checkout.session" and checkout.get("id") == checkout_id
                and checkout.get("livemode") is self.settings.livemode and checkout.get("mode") == "subscription"
                and checkout.get("client_reference_id") == reference and metadata.get("woodshed_plan_code") == plan_code
                and checkout.get("status") == "expired" and checkout.get("payment_status") == "unpaid"
                and checkout.get("subscription") is None and checkout.get("invoice") is None)

    def _event(self, event):
        require(event.get("livemode") is self.settings.livemode and not event.get("account")
                and event.get("api_version") == API_VERSION)
        if event.get("type") == "customer.subscription.deleted":
            data = event.get("data")
            require(isinstance(data, dict))
            return self._termination(data.get("object"), identifier(event.get("id"), "evt_"),
                                     timestamp(event.get("created")))
        if event.get("type") != "invoice.paid":
            return None
        return self._invoice(event["data"]["object"], identifier(event.get("id"), "evt_"))

    def _retrieve_event(self, event_id, expected_type=None):
        canonical = self._call(self.client.v1.events.retrieve, identifier(event_id, "evt_"))
        require(canonical.get("id") == event_id and (expected_type is None or canonical.get("type") == expected_type))
        return self._event(canonical)

    def verify_and_parse_webhook(self, body, headers):
        try:
            event = stripe.Webhook.construct_event(
                body, headers.get("stripe-signature", ""), self.settings.webhook_secret)
        except (ValueError, stripe.SignatureVerificationError):
            raise ValueError("Invalid Stripe webhook signature or payload.") from None
        event = event.to_dict()
        require(event.get("livemode") is self.settings.livemode and not event.get("account"))
        if event.get("type") not in ("invoice.paid", "customer.subscription.deleted"):
            return None
        self._account()
        # Direct-account webhooks lack an account ID. Retrieval with the verified
        # account's configured key binds the event to that account (no Connect support).
        return self._retrieve_event(event.get("id"), event["type"])

    def _checkout_id(self, subscription, customer, reference, plan_code):
        sessions = self._call(self.client.v1.checkout.sessions.list, params={"subscription": subscription, "limit": 2})
        data = sessions.get("data")
        require(sessions.get("has_more") is False and isinstance(data, list) and len(data) == 1)
        checkout = data[0]
        require(isinstance(checkout, dict) and checkout.get("object") == "checkout.session"
                and checkout.get("livemode") is self.settings.livemode and checkout.get("mode") == "subscription"
                and checkout.get("subscription") == subscription and checkout.get("customer") == customer
                and checkout.get("client_reference_id") == reference and isinstance(checkout.get("metadata"), dict)
                and checkout["metadata"].get("woodshed_plan_code") == plan_code
                and checkout.get("payment_method_types") == ["card"])
        return identifier(checkout.get("id"), self.settings.checkout_id_prefix)

    def _termination(self, subscription, event_id, observed_at):
        """An ended subscription supplies lifecycle evidence only, including on replay."""
        require(isinstance(subscription, dict) and subscription.get("object") == "subscription"
                and subscription.get("livemode") is self.settings.livemode and subscription.get("status") == "canceled")
        sub_id = identifier(subscription.get("id"), "sub_")
        customer = identifier(subscription.get("customer"), "cus_")
        # A period-end request can set canceled_at while the subscription is
        # still active. ended_at dates the completed cancellation.
        ended_at = timestamp(subscription.get("ended_at"))
        require(ended_at <= observed_at)
        items = subscription.get("items")
        require(isinstance(items, dict) and items.get("has_more") is False and isinstance(items.get("data"), list)
                and len(items["data"]) == 1 and isinstance(items["data"][0], dict))
        item = items["data"][0]
        start, end = timestamp(item.get("current_period_start")), timestamp(item.get("current_period_end"))
        require(start < end and start <= ended_at)
        metadata = subscription.get("metadata")
        require(isinstance(metadata, dict))
        reference = metadata.get("woodshed_checkout_reference")
        code = metadata.get("woodshed_plan_code")
        require(isinstance(reference, str) and 1 <= len(reference) <= 64 and isinstance(code, str) and code in PLAN_ENV)
        checkout_id = self._checkout_id(sub_id, customer, reference, code)
        return SubscriptionEvent(external_event_id=event_id, external_subscription_id=sub_id,
            occurred_at=ended_at, provider_status="canceled", period_start=start, period_end=end,
            terminal=True, checkout_reference=reference, stripe_termination={
                "account_id": self.settings.account_id, "customer_id": customer, "checkout_id": checkout_id, "plan_code": code})

    def _invoice(self, invoice, event_id):
        """Normalize immutable paid-invoice facts, never today's subscription state."""
        require(invoice.get("object") == "invoice" and invoice.get("livemode") is self.settings.livemode
                and invoice.get("status") == "paid" and invoice.get("collection_method") == "charge_automatically"
                and invoice.get("billing_reason") in ("subscription_create", "subscription_cycle")
                and not (invoice.get("automatic_tax") or {}).get("enabled")
                and not invoice.get("discounts") and not invoice.get("total_taxes")
                and not invoice.get("total_discount_amounts") and not invoice.get("starting_balance")
                and not invoice.get("pre_payment_credit_notes_amount")
                and not invoice.get("on_behalf_of") and not invoice.get("transfer_data"))
        invoice_id = identifier(invoice.get("id"), "in_")
        customer = identifier(invoice.get("customer"), "cus_")
        parent = invoice.get("parent") or {}
        details = parent.get("subscription_details") or {}
        require(parent.get("type") == "subscription_details")
        subscription = identifier(details.get("subscription"), "sub_")
        metadata = details.get("metadata") or {}
        reference = metadata.get("woodshed_checkout_reference")
        require(isinstance(reference, str) and 1 <= len(reference) <= 64)
        lines = invoice.get("lines") or {}
        require(lines.get("has_more") is False and len(lines.get("data", [])) == 1)
        line = lines["data"][0]
        item = (line.get("parent") or {}).get("subscription_item_details") or {}
        require(item.get("subscription") == subscription and item.get("proration") is False
                and line.get("quantity") == 1)
        price_id = identifier(((line.get("pricing") or {}).get("price_details") or {}).get("price"), "price_")
        plan = self._price(price_id)
        require(metadata.get("woodshed_plan_code") == plan.code
                and invoice.get("currency") == "usd" and line.get("currency") == "usd"
                and line.get("amount") == plan.amount_cents
                and all(invoice.get(key) == plan.amount_cents for key in ("amount_paid", "amount_due", "total"))
                and invoice.get("amount_remaining") == 0)
        checkout_id = self._checkout_id(subscription, customer, reference, plan.code)
        # invoice.paid also covers out-of-band settlement. Require an actual,
        # fully successful card PaymentIntent linked through InvoicePayment.
        payments = self._call(self.client.v1.invoice_payments.list,
                              params={"invoice": invoice_id, "status": "paid", "limit": 2})
        require(not payments.get("has_more") and len(payments.get("data", [])) == 1)
        paid = payments["data"][0]
        payment = paid.get("payment") or {}
        require(paid.get("invoice") == invoice_id and paid.get("status") == "paid"
                and paid.get("amount_paid") == plan.amount_cents and payment.get("type") == "payment_intent")
        payment_id = identifier(payment.get("payment_intent"), "pi_")
        intent = self._call(self.client.v1.payment_intents.retrieve, payment_id,
                            params={"expand": ["latest_charge"]})
        charge = intent.get("latest_charge") or {}
        require(intent.get("livemode") is self.settings.livemode and intent.get("status") == "succeeded"
                and intent.get("customer") == customer and intent.get("currency") == "usd"
                and intent.get("amount_received") == plan.amount_cents
                and isinstance(charge, dict) and charge.get("paid") is True
                and (charge.get("payment_method_details") or {}).get("type") == "card")
        period = line.get("period") or {}
        start, end = timestamp(period.get("start")), timestamp(period.get("end"))
        require(end > start)
        return SubscriptionEvent(
            external_event_id=event_id, external_subscription_id=subscription,
            occurred_at=timestamp((invoice.get("status_transitions") or {}).get("paid_at")),
            provider_status="unknown", period_start=start, period_end=end,
            checkout_reference=reference, paid_through=end, payment_reference=invoice_id,
            stripe_invoice={"account_id": self.settings.account_id, "customer_id": customer,
                "checkout_id": checkout_id, "price_id": price_id, "payment_intent_id": payment_id,
                "plan_code": plan.code, "amount_cents": plan.amount_cents,
                "currency": plan.currency, "interval": plan.interval})

    def inspect_evidence(self, request):
        self._account()
        require(request.provider == "stripe")
        if request.external_event_id and request.external_event_id.startswith("evt_"):
            return self._evidence(request, self._retrieve_event(request.external_event_id))
        if request.external_subscription_id:
            sub_id = identifier(request.external_subscription_id, "sub_")
            lookup_id = "stripe_subscription_ended_" + sha256(sub_id.encode()).hexdigest()
            if request.external_event_id in (None, lookup_id):
                sub = self._call(self.client.v1.subscriptions.retrieve, sub_id)
                require(sub.get("object") == "subscription" and sub.get("id") == sub_id
                        and sub.get("livemode") is self.settings.livemode)
                if sub.get("status") == "canceled":
                    return self._evidence(request, self._termination(sub, lookup_id, datetime.now(timezone.utc)))
                if request.external_event_id == lookup_id:
                    return self._evidence(request, None)
        if request.provider_checkout_id:
            checkout_id = identifier(request.provider_checkout_id, self.settings.checkout_id_prefix)
            checkout = self._call(self.client.v1.checkout.sessions.retrieve, checkout_id)
            require(checkout.get("livemode") is self.settings.livemode
                    and checkout.get("client_reference_id") == request.checkout_reference)
            if not checkout.get("invoice"):
                return ProviderEvidence(request, datetime.now(timezone.utc), "found",
                    checkout_reference=request.checkout_reference, provider_checkout_id=request.provider_checkout_id)
            invoice = self._call(self.client.v1.invoices.retrieve, identifier(checkout["invoice"], "in_"))
            # Stable synthetic receipt for operator recovery of an undelivered
            # first invoice. Invoice ID deduplication also covers later webhooks.
            return self._evidence(request, self._invoice(invoice, "stripe_invoice_paid_" + invoice["id"]))
        return self._evidence(request, None)

    def _evidence(self, request, event):
        if event is None:
            return ProviderEvidence(request, datetime.now(timezone.utc), "unsupported")
        facts = event.stripe_invoice or event.stripe_termination
        return ProviderEvidence(request, datetime.now(timezone.utc), "found",
            external_subscription_id=event.external_subscription_id, checkout_reference=event.checkout_reference,
            provider_checkout_id=facts["checkout_id"], event=event,
            plan_code=facts["plan_code"], amount_cents=facts.get("amount_cents"),
            currency=facts.get("currency"), interval=facts.get("interval"))
