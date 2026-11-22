"""Website purchase retries use one owner reservation and one Stripe idempotency key."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from app import billing_providers as billing
from app.billing_config import SUBSCRIPTION_TERMS_VERSION
from app.memberships import Actor
from app.models import CheckoutAttempt
from tests.test_billing_replacement import db, sqlite_db
from tests.test_memberships import NOW
from tests.test_stripe_billing import adapter, configure, count


@pytest.fixture
def website(adapter, monkeypatch):
    provider, http = adapter
    configure(monkeypatch, http.mode)
    monkeypatch.setenv("PUBLIC_SUBSCRIPTION_REFUND_POLICY", "Fixture refund terms.")
    return provider, http


def test_malformed_checkout_window_disables_offer_and_purchase(db, website, monkeypatch):
    _, http = website
    monkeypatch.setenv("CHECKOUT_VALIDITY_SECONDS", "not-a-time")
    adult = Actor("adult", 1)
    with db() as session:
        assert billing.website_purchase_plans(session, adult) == []
    with pytest.raises(billing.BillingUnavailable):
        billing.website_checkout(db, adult, "full_monthly_7")
    assert not http.calls and count(db, CheckoutAttempt) == 0


def test_simultaneous_plans_open_only_one_checkout(db, website):
    _, http = website
    start = Barrier(2)

    def buy(plan):
        start.wait(timeout=10)
        try:
            return billing.website_checkout(db, Actor("adult", 1), plan, terms_version=SUBSCRIPTION_TERMS_VERSION)
        except ValueError as error:
            assert "already in progress" in str(error)
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(buy, ("full_monthly_7", "full_annual_49")))
    assert outcomes.count(http.checkout["url"]) == outcomes.count(None) == 1
    assert count(db, CheckoutAttempt) == 1
    assert sum(call[0].lower() == "post" for call in http.calls) == 1


def test_lost_checkout_response_retries_same_reservation(db, website, monkeypatch):
    provider, http = website
    original_create = provider.create_checkout

    def lose_response(**kwargs):
        original_create(**kwargs)
        raise billing.BillingUnavailable("Response lost")

    adult = Actor("adult", 1)
    with monkeypatch.context() as lost:
        lost.setattr(provider, "create_checkout", lose_response)
        with pytest.raises(billing.BillingUnavailable, match="Response lost"):
            billing.website_checkout(db, adult, "full_monthly_7", terms_version=SUBSCRIPTION_TERMS_VERSION)
    assert billing.website_checkout(db, adult, "full_monthly_7", terms_version=SUBSCRIPTION_TERMS_VERSION) == http.checkout["url"]
    creates = [call for call in http.calls if call[0].lower() == "post"]
    with db() as session:
        attempt = session.scalars(select(CheckoutAttempt)).one()
        assert len(creates) == 2
        assert all(call[3]["Idempotency-Key"] == attempt.reference for call in creates)
        assert creates[0][2] == creates[1][2]
        assert attempt.provider_checkout_id == http.checkout["id"]


def test_unpaid_expiry_must_match_owner_reference_before_replacement(db, website, monkeypatch):
    _, http = website
    adult = Actor("adult", 1)
    billing.website_checkout(db, adult, "full_monthly_7", terms_version=SUBSCRIPTION_TERMS_VERSION)
    with db() as session:
        reference = session.scalar(select(CheckoutAttempt.reference))
    monkeypatch.setattr(billing, "clock", lambda: NOW + timedelta(hours=2))
    http.checkout.update(status="expired", payment_status="unpaid", subscription=None,
                         invoice=None, client_reference_id="another-owner-reference")
    with pytest.raises(ValueError, match="needs confirmation"):
        billing.website_checkout(db, adult, "full_annual_49", terms_version=SUBSCRIPTION_TERMS_VERSION)
    assert count(db, CheckoutAttempt) == 1
    http.checkout["client_reference_id"] = reference
    billing.website_checkout(db, adult, "full_annual_49", terms_version=SUBSCRIPTION_TERMS_VERSION)
    reads = len([call for call in http.calls if call[1].startswith("/v1/checkout/sessions/")])
    assert reads == 2
    billing.website_checkout(db, adult, "full_annual_49", terms_version=SUBSCRIPTION_TERMS_VERSION)
    assert len([call for call in http.calls if call[1].startswith("/v1/checkout/sessions/")]) == reads
    assert count(db, CheckoutAttempt) == 2
