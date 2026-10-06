"""Real Stripe SDK + signed webhooks, entirely fake HTTP and disposable databases."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import stripe
from sqlalchemy import func, select

from tests.test_billing_replacement import db, sqlite_db
from tests.test_memberships import NOW, client, grant, page_csrf
from app import billing_providers as b, memberships as m, billing_reconciliation as reconciliation
from app.billing_config import BillingConfig, PLANS
from app.age_privacy import declare_age
from app.models import (Membership, MembershipSeat, ProviderSubscription, CheckoutAttempt,
                        BillingProviderEvent, BillingEventApplication, BillingPaymentEffect, MembershipAuditEvent)
from app.stripe_billing import API_VERSION, StripeProvider, StripeSettings

CONFIG = BillingConfig(public_subscriptions_enabled=True, stripe_billing_enabled=True)
SETTINGS = StripeSettings("sk_test_fake", "whsec_fake", "acct_woodshed", "price_month", "price_year",
                          "http://localhost:8000", mode="test")


class StripeHTTP(stripe.HTTPClient):
    """No sockets. Exercise SDK serialization and StripeObject conversion too."""
    name = "woodshed-test"

    def __init__(self, mode="test"):
        super().__init__()
        self.mode = mode
        self.livemode = mode == "live"
        self.calls = []
        self.account = "acct_woodshed"
        self.events = {}
        self.error = None
        self.prices = {price_id: {
            "id": price_id, "object": "price", "livemode": self.livemode, "type": "recurring",
            "billing_scheme": "per_unit", "unit_amount": PLANS[code].amount_cents, "currency": "usd",
            "recurring": {"interval": PLANS[code].interval, "interval_count": 1, "usage_type": "licensed"},
        } for code, price_id in SETTINGS.prices.items()}
        self.checkout = {"object": "checkout.session", "id": f"cs_{mode}_woodshed", "livemode": self.livemode,
                         "mode": "subscription", "url": f"https://checkout.stripe.com/c/pay/cs_{mode}_woodshed",
                         "subscription": "sub_woodshed", "customer": "cus_woodshed",
                         "payment_method_types": ["card"], "invoice": "in_first"}
        self.invoice = None
        self.intent = {"id": "pi_first", "object": "payment_intent", "livemode": self.livemode,
                       "status": "succeeded", "customer": "cus_woodshed", "currency": "usd",
                       "amount_received": 700, "latest_charge": {
                           "id": "ch_first", "object": "charge", "paid": True,
                           "payment_method_details": {"type": "card"}}}

    def request(self, method, url, headers, post_data=None, **kwargs):
        path = urlsplit(url).path
        params = parse_qs(post_data or urlsplit(url).query)
        self.calls.append((method, path, params, headers))
        assert headers["Stripe-Version"] == API_VERSION
        assert headers["Authorization"] == f"Bearer sk_{self.mode}_fake"
        if self.error:
            return json.dumps({"error": {"type": "invalid_request_error", "message": "private transport detail"}}), 403, {}
        if path == "/v1/account":
            result = {"id": self.account, "object": "account"}
        elif path.startswith("/v1/prices/"):
            result = self.prices[path.rsplit("/", 1)[1]]
        elif path == "/v1/checkout/sessions" and method.lower() == "post":
            self.checkout["client_reference_id"] = params["client_reference_id"][0]
            self.checkout["metadata"] = {"woodshed_plan_code": params["metadata[woodshed_plan_code]"][0]}
            result = self.checkout
        elif path == "/v1/checkout/sessions":
            assert params["subscription"] == ["sub_woodshed"]
            result = {"object": "list", "data": [self.checkout], "has_more": False}
        elif path.startswith("/v1/checkout/sessions/"):
            result = self.checkout
        elif path.startswith("/v1/events/"):
            result = self.events[path.rsplit("/", 1)[1]]
        elif path == "/v1/invoice_payments":
            result = {"object": "list", "has_more": False, "data": [{
                "object": "invoice_payment", "id": "inpay_first", "invoice": params["invoice"][0],
                "status": "paid", "amount_paid": self.intent["amount_received"],
                "payment": {"type": "payment_intent", "payment_intent": self.intent["id"]}}]}
        elif path.startswith("/v1/payment_intents/"):
            result = self.intent
        elif path.startswith("/v1/invoices/"):
            result = self.invoice
        else:
            raise AssertionError(f"Unexpected Stripe request: {method} {path}")
        return json.dumps(result), 200, {}

    def paid_event(self, reference, code="full_monthly_7", *, event_id="evt_first", renewal=False):
        plan = PLANS[code]
        start = NOW + timedelta(days=31 if renewal else 0)
        end = start + timedelta(days=31 if plan.interval == "month" else 365)
        self.checkout.update(client_reference_id=reference, metadata={"woodshed_plan_code": code})
        self.intent["amount_received"] = plan.amount_cents
        self.intent["id"] = "pi_renewal" if renewal else "pi_first"
        self.invoice = {
            "id": "in_renewal" if renewal else "in_first", "object": "invoice", "livemode": self.livemode,
            "status": "paid", "collection_method": "charge_automatically", "customer": "cus_woodshed",
            "billing_reason": "subscription_cycle" if renewal else "subscription_create",
            "currency": "usd", "amount_paid": plan.amount_cents, "amount_due": plan.amount_cents,
            "total": plan.amount_cents, "amount_remaining": 0, "automatic_tax": {"enabled": False},
            "status_transitions": {"paid_at": int(start.timestamp())},
            "parent": {"type": "subscription_details", "subscription_details": {
                "subscription": "sub_woodshed", "metadata": {
                    "woodshed_checkout_reference": reference, "woodshed_plan_code": code}}},
            "lines": {"has_more": False, "data": [{
                "id": "il_first", "object": "line_item", "quantity": 1, "amount": plan.amount_cents,
                "currency": "usd", "period": {"start": int(start.timestamp()), "end": int(end.timestamp())},
                "parent": {"subscription_item_details": {"subscription": "sub_woodshed", "proration": False}},
                "pricing": {"price_details": {"price": SETTINGS.prices[code]}},
            }]},
        }
        event = {"id": event_id, "object": "event", "type": "invoice.paid", "api_version": API_VERSION,
                 "livemode": self.livemode, "created": int(start.timestamp()), "data": {"object": deepcopy(self.invoice)}}
        self.events[event_id] = event
        return event


@pytest.fixture(params=["test", "live"])
def adapter(monkeypatch, request):
    settings = replace(SETTINGS, mode=request.param, secret_key=f"sk_{request.param}_fake",
                       base_url="https://woodshedwoodchuck.com" if request.param == "live" else SETTINGS.base_url)
    http = StripeHTTP(settings.mode)
    sdk = stripe.StripeClient(settings.secret_key, stripe_version=API_VERSION, http_client=http,
                             max_network_retries=0)
    provider = StripeProvider(settings, client=sdk)
    monkeypatch.setitem(b.PROVIDERS, "stripe", provider)
    return provider, http


def signed(event, *, at=None):
    body = json.dumps(event).encode()
    at = str(int(time.time()) if at is None else at)
    signature = hmac.new(SETTINGS.webhook_secret.encode(), at.encode() + b"." + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={at},v1={signature}"}


def authorize(db, code="full_monthly_7"):
    with db() as session:
        attempt = b.authorize_checkout(session, m.Actor("student", 1), "stripe", code, config=CONFIG)
        session.commit()
        return attempt


def deliver(db, event, *, config=CONFIG):
    return b.process_webhook(db, "stripe", *signed(event), config=config)


def count(db, model):
    with db() as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("code", list(SETTINGS.prices))
def test_checkout_hosted_card_quantity_one_and_idempotent(db, adapter, code):
    provider, http = adapter
    for _ in range(2):
        url = b.checkout(db, m.Actor("student", 1), "stripe", code, config=CONFIG)
        assert url.startswith("https://checkout.stripe.com/")
    posts = [row for row in http.calls if row[0].lower() == "post"]
    assert len(posts) == 2 and posts[0][2:] == posts[1][2:]
    params, headers = posts[0][2:]
    assert params["mode"] == ["subscription"]
    assert params["payment_method_types[0]"] == ["card"]
    assert params["wallet_options[link][display]"] == ["never"]
    assert params["line_items[0][quantity]"] == ["1"]
    assert params["line_items[0][price]"] == [SETTINGS.prices[code]]
    assert params["automatic_tax[enabled]"] == ["false"]
    assert params["allow_promotion_codes"] == ["false"]
    with db() as session:
        attempt = session.scalar(select(CheckoutAttempt))
        assert headers["Idempotency-Key"] == attempt.reference
        assert params["subscription_data[metadata][woodshed_checkout_reference]"] == [attempt.reference]
        assert params["expires_at"] == [str(int(m.utc(attempt.expires_at).timestamp()))]
        assert attempt.provider_checkout_id == f"cs_{http.mode}_woodshed"
    assert count(db, CheckoutAttempt) == 1 and count(db, Membership) == 0


@pytest.mark.parametrize("code", list(SETTINGS.prices))
def test_invoice_paid_activates_once_and_retains_correlation(db, adapter, code):
    provider, http = adapter
    attempt = authorize(db, code)
    event = http.paid_event(attempt.reference, code)
    first, fresh = deliver(db, event)
    assert fresh and first.status == "processed"
    replay, fresh = deliver(db, event)
    assert replay.id == first.id and not fresh
    assert count(db, Membership) == count(db, ProviderSubscription) == count(db, BillingPaymentEffect) == 1
    with db() as session:
        sub = session.scalar(select(ProviderSubscription))
        member = session.get(Membership, sub.membership_id)
        assert member.source == "stripe" and member.max_student_seats == 5
        assert member.plan_code == code and m.student_has_full_access(session, 1)
        assert sub.external_customer_id == "cus_woodshed" and sub.external_subscription_id == "sub_woodshed"
        assert sub.provider_status == "unknown" and sub.last_event_at is None
        assert session.get(CheckoutAttempt, attempt.id).provider_checkout_id == f"cs_{http.mode}_woodshed"
        facts = session.scalar(select(BillingEventApplication)).facts
        assert facts["stripe_invoice"]["price_id"] == SETTINGS.prices[code]
        assert facts["stripe_invoice"]["payment_intent_id"] == "pi_first"
        assert facts["payment_reference"] == "in_first"


def test_renewal_extends_and_distinct_event_same_invoice_deduplicates(db, adapter):
    _, http = adapter
    attempt = authorize(db)
    deliver(db, http.paid_event(attempt.reference))
    renewal = http.paid_event(attempt.reference, event_id="evt_renewal", renewal=True)
    assert deliver(db, renewal)[0].status == "processed"
    alias = deepcopy(renewal)
    alias["id"] = "evt_redelivery_other"
    http.events[alias["id"]] = alias
    assert deliver(db, alias)[0].status == "processed"
    assert count(db, BillingPaymentEffect) == 2 and count(db, Membership) == 1
    with db() as session:
        assert m.utc(session.scalar(select(Membership.access_until))) == NOW + timedelta(days=62)


@pytest.mark.parametrize("failure", ["signature", "tampered_body", "old_signature"])
def test_signature_verification_precedes_transport_and_inbox(db, adapter, failure):
    provider, http = adapter
    event = http.paid_event("reference")
    body, headers = signed(event, at=1 if failure == "old_signature" else None)
    if failure == "signature":
        headers["stripe-signature"] = "invalid"
    if failure == "tampered_body":
        body += b" "
    with pytest.raises(ValueError, match="signature"):
        b.process_webhook(db, "stripe", body, headers, config=CONFIG)
    assert not http.calls and count(db, BillingProviderEvent) == 0


@pytest.mark.parametrize("failure", ["price", "account", "event_mode", "invoice_mode", "price_mode",
                                     "checkout_mode", "intent_mode",
                                     "customer", "quantity", "proration", "amount", "noncard", "api_version"])
def test_wrong_evidence_rejected_without_entitlement(db, adapter, failure):
    _, http = adapter
    event = http.paid_event(authorize(db).reference)
    invoice = event["data"]["object"]
    line = invoice["lines"]["data"][0]
    if failure == "price":
        line["pricing"]["price_details"]["price"] = "price_wrong"
    elif failure == "account":
        http.account = "acct_other"
    elif failure == "event_mode":
        event["livemode"] = not http.livemode
    elif failure == "invoice_mode":
        invoice["livemode"] = not http.livemode
    elif failure == "price_mode":
        http.prices["price_month"]["livemode"] = not http.livemode
    elif failure == "checkout_mode":
        http.checkout["livemode"] = not http.livemode
    elif failure == "intent_mode":
        http.intent["livemode"] = not http.livemode
    elif failure == "customer":
        invoice["customer"] = "cus_other"
    elif failure == "quantity":
        line["quantity"] = 5
    elif failure == "proration":
        line["parent"]["subscription_item_details"]["proration"] = True
    elif failure == "amount":
        invoice["amount_paid"] = 0
    elif failure == "noncard":
        http.intent["latest_charge"]["payment_method_details"]["type"] = "us_bank_account"
    elif failure == "api_version":
        event["api_version"] = "2020-08-27"
    with pytest.raises(ValueError):
        deliver(db, event)
    assert count(db, BillingProviderEvent) == count(db, Membership) == 0


def test_valid_other_price_cannot_consume_local_authorization(db, adapter):
    _, http = adapter
    attempt = authorize(db, "full_annual_49")
    record, _ = deliver(db, http.paid_event(attempt.reference, "full_monthly_7"))
    assert record.status == "failed" and record.error_code == "stripe_price_conflict"
    assert count(db, Membership) == 0


def test_existing_checkout_id_cannot_be_replaced(db, adapter):
    _, http = adapter
    attempt = authorize(db)
    with db() as session:
        session.get(CheckoutAttempt, attempt.id).provider_checkout_id = f"cs_{http.mode}_other"
        session.commit()
    record, _ = deliver(db, http.paid_event(attempt.reference))
    assert record.error_code == "stripe_checkout_conflict" and count(db, Membership) == 0


def test_complimentary_membership_blocks_payment_without_mutation(db, adapter):
    _, http = adapter
    attempt = authorize(db)
    member_id = grant(db, kind="student")  # Grant arrives after checkout authorization.
    with db() as session:
        before = [(s.id, s.profile_id) for s in session.scalars(select(MembershipSeat))]
    record, _ = deliver(db, http.paid_event(attempt.reference))
    assert record.status == "failed"
    with db() as session:
        member = session.get(Membership, member_id)
        assert member.source == "manual" and member.status == "active" and member.access_until is None
        assert before == [(s.id, s.profile_id) for s in session.scalars(select(MembershipSeat))]
        assert m.student_has_full_access(session, 1)
    assert count(db, Membership) == 1 and count(db, ProviderSubscription) == count(db, BillingPaymentEffect) == 0


def test_prebeta_access_survives_paid_expiry(db, adapter, monkeypatch):
    from app.tester_enrollments import enroll_tester, C001
    _, http = adapter
    with db() as session:
        declare_age(session, 1, "adult")
        enroll_tester(session, 1, C001, NOW)
        session.commit()
    deliver(db, http.paid_event(authorize(db).reference))
    monkeypatch.setattr(m, "clock", lambda: NOW + timedelta(days=100))
    with db() as session:
        assert not m.membership_is_active(session.scalar(select(Membership)))
        assert m.student_has_full_access(session, 1)


@pytest.mark.parametrize("event_type", [
    "checkout.session.completed", "checkout.session.async_payment_succeeded",
    "invoice.payment_succeeded", "invoice.payment_failed", "payment_intent.succeeded",
    "customer.subscription.updated", "customer.subscription.deleted",
])
def test_only_invoice_paid_is_processed(db, adapter, event_type):
    _, http = adapter
    event = {"id": "evt_other", "object": "event", "type": event_type, "livemode": http.livemode}
    assert deliver(db, event) == (None, False)
    assert count(db, Membership) == count(db, BillingProviderEvent) == 0
    assert not http.calls


def test_disabled_default_mismatched_credentials_and_launch_plan(adapter, monkeypatch):
    provider, http = adapter
    for key in ("STRIPE_MODE", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_ACCOUNT_ID",
                "STRIPE_PRICE_FULL_MONTHLY_7", "STRIPE_PRICE_FULL_ANNUAL_49", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(b.PROVIDERS, "stripe", b.DisabledProvider("stripe"))
    assert type(b.enabled_provider("stripe", CONFIG)) is b.DisabledProvider
    configure(monkeypatch)
    assert type(b.enabled_provider("stripe", CONFIG)) is StripeProvider
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_never_allowed")
    assert type(b.enabled_provider("stripe", CONFIG)) is b.DisabledProvider
    with pytest.raises(ValueError):
        provider.create_checkout(plan=PLANS["friendship_annual_30"], idempotency_key="unused",
                                 expires_at=NOW + timedelta(hours=1))
    assert not http.calls


def configure(monkeypatch, mode="test"):
    for key, value in {"PUBLIC_SUBSCRIPTIONS_ENABLED": "true", "STRIPE_BILLING_ENABLED": "true",
                       "STRIPE_MODE": mode, "STRIPE_SECRET_KEY": f"sk_{mode}_fake",
                       "STRIPE_WEBHOOK_SECRET": SETTINGS.webhook_secret, "STRIPE_ACCOUNT_ID": SETTINGS.account_id,
                       "STRIPE_PRICE_FULL_MONTHLY_7": SETTINGS.monthly_price,
                       "STRIPE_PRICE_FULL_ANNUAL_49": SETTINGS.annual_price,
                       "PUBLIC_BASE_URL": "https://woodshedwoodchuck.com" if mode == "live" else SETTINGS.base_url}.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("mode,base_url,accepted", [
    ("test", "http://localhost", True),
    ("test", "http://127.0.0.1", True),
    ("test", "https://woodshedwoodchuck.com", True),
    ("test", "http://woodshedwoodchuck.com", False),
    ("test", "http://localhost.example.com", False),
    ("live", "http://localhost", False),
    ("live", "http://127.0.0.1", False),
    ("live", "http://woodshedwoodchuck.com", False),
    ("live", "https://woodshedwoodchuck.com", True),
])
def test_base_url_requires_https_in_live_mode(monkeypatch, mode, base_url, accepted):
    configure(monkeypatch, mode)
    monkeypatch.setenv("PUBLIC_BASE_URL", base_url)
    settings = StripeSettings.from_environment()
    assert (settings is not None) is accepted
    if accepted:
        assert settings.base_url == base_url


@pytest.mark.parametrize("mode", ["test", "live"])
@pytest.mark.parametrize("key_prefix", ["sk_test_", "rk_test_", "sk_live_", "rk_live_", "pk_live_", ""])
def test_configuration_requires_key_matching_explicit_mode(monkeypatch, mode, key_prefix):
    configure(monkeypatch, mode)
    monkeypatch.setenv("STRIPE_SECRET_KEY", key_prefix + "fake")
    monkeypatch.setitem(b.PROVIDERS, "stripe", b.DisabledProvider("stripe"))
    settings = StripeSettings.from_environment()
    provider = b.enabled_provider("stripe", CONFIG)
    if key_prefix in (f"sk_{mode}_", f"rk_{mode}_"):
        assert settings is not None and settings.mode == mode
        assert settings.livemode is (mode == "live")
        assert type(provider) is StripeProvider and provider.settings == settings
    else:
        assert settings is None and type(provider) is b.DisabledProvider


@pytest.mark.parametrize("mode", [None, "", "   ", "TEST", "LIVE", "sandbox", "production", "false", "0"])
@pytest.mark.parametrize("key_mode", ["test", "live"])
def test_missing_or_invalid_mode_fails_closed(monkeypatch, mode, key_mode):
    configure(monkeypatch, key_mode)
    if mode is None:
        monkeypatch.delenv("STRIPE_MODE")
    else:
        monkeypatch.setenv("STRIPE_MODE", mode)
    monkeypatch.setitem(b.PROVIDERS, "stripe", b.DisabledProvider("stripe"))
    assert StripeSettings.from_environment() is None
    assert type(b.enabled_provider("stripe", CONFIG)) is b.DisabledProvider


@pytest.mark.parametrize("source", ["price", "checkout"])
def test_checkout_creation_rejects_wrong_mode(adapter, source):
    provider, http = adapter
    obj = http.prices["price_month"] if source == "price" else http.checkout
    obj["livemode"] = not http.livemode
    with pytest.raises(ValueError):
        provider.create_checkout(plan=PLANS["full_monthly_7"], idempotency_key="reference",
                                 expires_at=NOW + timedelta(hours=1))
    if source == "price":
        assert all(row[0].lower() != "post" for row in http.calls)


@pytest.mark.parametrize("source", ["signed_event", "canonical_event", "invoice", "price", "checkout", "intent"])
@pytest.mark.parametrize("invalid_mode", [None, 0, 1, "false", "true"])
def test_evidence_requires_boolean_livemode(adapter, source, invalid_mode):
    provider, http = adapter
    event = deepcopy(http.paid_event("reference"))
    objects = {"signed_event": event, "canonical_event": http.events[event["id"]],
               "invoice": http.events[event["id"]]["data"]["object"],
               "price": http.prices["price_month"], "checkout": http.checkout, "intent": http.intent}
    objects[source]["livemode"] = invalid_mode
    with pytest.raises(ValueError):
        provider.verify_and_parse_webhook(*signed(event))


@pytest.mark.parametrize("path", ["webhook", "inspection"])
def test_canonical_event_wrong_mode_rejected(adapter, path):
    provider, http = adapter
    event = deepcopy(http.paid_event("reference"))
    http.events[event["id"]]["livemode"] = not http.livemode
    with pytest.raises(ValueError):
        if path == "webhook":
            provider.verify_and_parse_webhook(*signed(event))
        else:
            provider.inspect_evidence(b.EvidenceRequest("stripe", external_event_id=event["id"]))
    assert any(row[1] == "/v1/events/evt_first" for row in http.calls)


@pytest.mark.parametrize("path", ["create", "invoice", "inspection"])
@pytest.mark.parametrize("checkout_id", ["cs_test_other", "cs_live_other", "cs_other", "pi_other", None, "cs_live_" + "x" * 255])
def test_checkout_id_matches_configured_mode(adapter, path, checkout_id):
    provider, http = adapter
    event = http.paid_event("reference")
    http.checkout["id"] = checkout_id

    def validate():
        if path == "create":
            return provider.create_checkout(plan=PLANS["full_monthly_7"], idempotency_key="reference",
                                            expires_at=NOW + timedelta(hours=1)).external_checkout_id
        if path == "invoice":
            return provider.verify_and_parse_webhook(*signed(event)).stripe_invoice["checkout_id"]
        return provider.inspect_evidence(b.EvidenceRequest(
            "stripe", checkout_reference="reference", provider_checkout_id=checkout_id)).provider_checkout_id

    if checkout_id == f"cs_{http.mode}_other":
        assert validate() == checkout_id
    elif path == "inspection" and checkout_id is None:
        assert validate() is None  # No identifiers means unsupported inspection.
    else:
        with pytest.raises(ValueError):
            validate()


@pytest.mark.parametrize("has_invoice", [False, True])
def test_checkout_inspection_rejects_wrong_mode(adapter, has_invoice):
    provider, http = adapter
    http.paid_event("reference")
    http.checkout["livemode"] = not http.livemode
    if not has_invoice:
        http.checkout["invoice"] = None
    with pytest.raises(ValueError):
        provider.inspect_evidence(b.EvidenceRequest(
            "stripe", checkout_reference="reference", provider_checkout_id=http.checkout["id"]))


@pytest.mark.parametrize("flag", ["STRIPE_BILLING_ENABLED", "PUBLIC_SUBSCRIPTIONS_ENABLED"])
def test_disabled_flags_prevent_checkout_without_transport(db, adapter, monkeypatch, flag):
    _, http = adapter
    configure(monkeypatch, http.mode)
    monkeypatch.setenv(flag, "false")
    with pytest.raises(b.BillingUnavailable):
        b.checkout(db, m.Actor("student", 1), "stripe", "full_monthly_7")
    assert not http.calls and count(db, Membership) == 0


def test_disabled_stripe_flag_blocks_webhooks_and_inspection(adapter, monkeypatch):
    provider, http = adapter
    configure(monkeypatch, http.mode)
    monkeypatch.setenv("STRIPE_BILLING_ENABLED", "false")
    with pytest.raises(b.BillingUnavailable):
        b.enabled_provider("stripe", BillingConfig.from_environment())
    assert not http.calls


def test_billing_and_public_purchasing_default_to_disabled(db, adapter, monkeypatch):
    _, http = adapter
    configure(monkeypatch, http.mode)
    monkeypatch.delenv("STRIPE_BILLING_ENABLED")
    monkeypatch.delenv("PUBLIC_SUBSCRIPTIONS_ENABLED")
    config = BillingConfig.from_environment()
    assert not config.stripe_billing_enabled and not config.public_subscriptions_enabled
    assert API_VERSION == "2026-08-26.dahlia"
    with pytest.raises(b.BillingUnavailable):
        b.checkout(db, m.Actor("student", 1), "stripe", "full_monthly_7")
    body, headers = signed(http.paid_event("unauthorized"))
    assert client().post("/membership/webhooks/stripe", content=body, headers=headers).status_code == 503
    assert not http.calls
    assert count(db, CheckoutAttempt) == count(db, BillingProviderEvent) == count(db, Membership) == 0


def test_single_configured_price_only(db, monkeypatch):
    configure(monkeypatch)
    monkeypatch.delenv("STRIPE_PRICE_FULL_ANNUAL_49")
    settings = StripeSettings.from_environment()
    assert settings.prices == {"full_monthly_7": SETTINGS.monthly_price}
    with db() as session:
        declare_age(session, 1, "adult")
        session.commit()
    response = client("student").get("/membership")
    assert response.status_code == 200
    assert "Purchasing is not available yet." in response.text
    assert 'action="/membership/checkout"' not in response.text
    monkeypatch.delenv("STRIPE_PRICE_FULL_MONTHLY_7")
    assert StripeSettings.from_environment() is None


@pytest.mark.parametrize("annual_price", [None, "", "   "])
def test_monthly_checkout_without_annual_price(adapter, monkeypatch, annual_price):
    provider, http = adapter
    configure(monkeypatch, http.mode)
    if annual_price is None:
        monkeypatch.delenv("STRIPE_PRICE_FULL_ANNUAL_49")
    else:
        monkeypatch.setenv("STRIPE_PRICE_FULL_ANNUAL_49", annual_price)
    provider.settings = StripeSettings.from_environment()
    assert provider.settings is not None
    result = provider.create_checkout(plan=PLANS["full_monthly_7"], idempotency_key="monthly-only",
                                      expires_at=NOW + timedelta(hours=1))
    assert result.external_checkout_id == f"cs_{http.mode}_woodshed"
    posts = [row for row in http.calls if row[0].lower() == "post"]
    assert len(posts) == 1
    assert posts[0][2]["line_items[0][price]"] == [SETTINGS.monthly_price]
    assert posts[0][2]["payment_method_types[0]"] == ["card"]
    assert posts[0][2]["wallet_options[link][display]"] == ["never"]


def test_annual_checkout_requires_annual_price_before_transport(adapter, monkeypatch):
    provider, http = adapter
    configure(monkeypatch, http.mode)
    monkeypatch.delenv("STRIPE_PRICE_FULL_ANNUAL_49")
    provider.settings = StripeSettings.from_environment()
    with pytest.raises(ValueError):
        provider.create_checkout(plan=PLANS["full_annual_49"], idempotency_key="missing-annual",
                                 expires_at=NOW + timedelta(hours=1))
    assert http.calls == []


def test_routes_checkout_csrf_and_return_never_grants_access(db, adapter, monkeypatch):
    _, http = adapter
    configure(monkeypatch, http.mode)
    # The shared PostgreSQL fixture creates profiles without an age declaration.
    with db() as session:
        declare_age(session, 1, "adult")
        session.commit()
    c = client("student")
    csrf = page_csrf(c)
    assert "Purchasing is not available yet." in c.get("/membership").text
    data = {"as_account": "student", "provider": "stripe", "plan_code": "full_monthly_7"}
    assert c.post("/membership/checkout", data=data).status_code == 403
    response = c.post("/membership/checkout", data={**data, "csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("https://checkout.stripe.com/")
    for query in ("checkout=returned", "checkout=success&payment_status=paid&session_id=" + http.checkout["id"]):
        response = c.get("/membership?" + query)
        assert response.status_code == 200 and "Open Access" in response.text
        assert count(db, Membership) == count(db, BillingPaymentEffect) == 0
        with db() as session:
            assert not m.student_has_full_access(session, 1)


def test_transport_error_sanitized_and_retry_reference_preserved(db, adapter):
    _, http = adapter
    http.error = True
    with pytest.raises(b.BillingUnavailable) as error:
        b.checkout(db, m.Actor("student", 1), "stripe", "full_monthly_7", config=CONFIG)
    assert str(error.value) == "Stripe is temporarily unavailable."
    assert count(db, CheckoutAttempt) == 1 and count(db, Membership) == 0


def test_webhook_http_endpoint_signature_activation_and_ignored_types(db, adapter, monkeypatch):
    _, http = adapter
    configure(monkeypatch, http.mode)
    event = http.paid_event(authorize(db).reference)
    body, headers = signed(event)
    c = client()
    assert c.post("/membership/webhooks/stripe", content=body).status_code == 400
    assert count(db, Membership) == 0
    response = c.post("/membership/webhooks/stripe", content=body, headers=headers)
    assert response.status_code == 200 and response.json()["processed"] is True
    assert c.post("/membership/webhooks/stripe", content=body, headers=headers).json()["duplicate"] is True
    body, headers = signed({"id": "evt_other", "object": "event", "livemode": http.livemode,
                            "type": "checkout.session.completed"})
    response = c.post("/membership/webhooks/stripe", content=body, headers=headers)
    assert response.status_code == 200 and response.json()["ignored"] is True
    assert count(db, Membership) == count(db, BillingPaymentEffect) == 1


def test_webhooks_continue_when_new_purchases_disabled(db, adapter):
    _, http = adapter
    event = http.paid_event(authorize(db).reference)
    assert deliver(db, event, config=replace(CONFIG, public_subscriptions_enabled=False))[0].status == "processed"


def test_checkout_reconciliation_and_later_webhook_use_one_payment(db, adapter, monkeypatch):
    _, http = adapter
    b.checkout(db, m.Actor("student", 1), "stripe", "full_monthly_7", config=CONFIG)
    with db() as session:
        attempt = session.scalar(select(CheckoutAttempt))
    event = http.paid_event(attempt.reference)
    # Fixture billing clock is historical; inspection observations use wall time.
    monkeypatch.setattr(b, "clock", lambda: datetime.now(timezone.utc))
    result_id = reconciliation.inspect(db, "checkout", attempt.id, m.Actor("admin"), config=CONFIG)
    with db() as session:
        assert session.get(MembershipAuditEvent, result_id).details["classification"] == "applied"
    assert deliver(db, event)[0].status == "processed"
    assert count(db, Membership) == count(db, BillingPaymentEffect) == 1
