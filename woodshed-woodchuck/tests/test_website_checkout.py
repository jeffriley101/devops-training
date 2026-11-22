"""Browser purchase restrictions, using disposable SQLite and the fake Stripe HTTP transport."""
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app import billing_providers, memberships, operator_checkout
from app.age_models import AccountPrivacy
from app.billing_config import SUBSCRIPTION_TERMS_VERSION
from app.models import BillingAccount, CheckoutAttempt, Membership, TrustedVerifier
from tests.test_memberships import client, db, grant, page_csrf
from tests.test_stripe_billing import adapter, configure, count

MONTHLY = "full_monthly_7"
ANNUAL = "full_annual_49"
POLICY = "Refund conditions from the test fixture."
# The shipped MainActivity appends this literal to the system WebView UA.
PLAY_WEBVIEW = ("Mozilla/5.0 (Linux; Android 14; Pixel Build/UP1A; wv) AppleWebKit/537.36 "
                "Version/4.0 Chrome/130.0 Mobile Safari/537.36 WoodshedAndroid/0.1.0-r4b")


@pytest.fixture
def stripe_http(db, adapter, monkeypatch):
    _, http = adapter
    configure(monkeypatch, http.mode)
    monkeypatch.setenv("PUBLIC_SUBSCRIPTION_REFUND_POLICY", POLICY)
    return http


def fields(csrf, plan=MONTHLY):
    return {"csrf": csrf, "as_account": "adult", "plan_code": plan, "provider": "stripe",
            "terms_version": SUBSCRIPTION_TERMS_VERSION}


def checkout(browser, data):
    return browser.post("/membership/checkout", data=data, follow_redirects=False)


def assert_no_offer(page):
    for text in ("Continue to Stripe Checkout", "/membership/checkout", "checkout.stripe.com", "$7/month", "$49/year", POLICY):
        assert text not in page.text


def test_play_webview_excludes_offer_and_rejects_direct_post(db, stripe_http):
    browser = client("adult")
    browser.headers["User-Agent"] = PLAY_WEBVIEW
    page = browser.get("/membership")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert_no_offer(page)
    response = checkout(browser, fields(page.context["csrf"]))
    assert response.status_code == 403
    assert "location" not in response.headers and "stripe" not in response.text.lower()
    assert not stripe_http.calls and count(db, CheckoutAttempt) == 0
    grant(db, "adult", 1)
    management = browser.get("/membership")
    assert "Manage membership" in management.text and "/membership/actions" in management.text
    assert_no_offer(management)


@pytest.mark.parametrize("flag,value", [
    ("PUBLIC_SUBSCRIPTIONS_ENABLED", "false"),
    ("STRIPE_BILLING_ENABLED", "false"),
    ("PUBLIC_SUBSCRIPTION_REFUND_POLICY", "   "),
])
def test_disabled_sale_hides_prices_and_prevents_authorization(db, stripe_http, monkeypatch, flag, value):
    monkeypatch.setenv(flag, value)
    browser = client("adult")
    page = browser.get("/membership")
    assert page.status_code == 200
    assert_no_offer(page)
    assert checkout(browser, fields(page.context["csrf"])).status_code == 503
    assert not stripe_http.calls and count(db, CheckoutAttempt) == 0


@pytest.mark.parametrize("identity", ["adult_student", "teen", "unscreened", "signed_out", "deleted", "ambiguous"])
def test_unauthorized_identity_rejected_before_checkout(db, stripe_http, identity):
    student = identity in {"adult_student", "teen", "unscreened"}
    if identity == "signed_out":
        browser = client()
    else:
        browser = client("student" if student else "adult", both=identity == "ambiguous")
    csrf = page_csrf(browser, "/membership/invitations/unused")
    with db.begin() as session:
        if identity == "teen":
            session.get(AccountPrivacy, 1).age_band = "13to17"
        elif identity == "unscreened":
            session.delete(session.get(AccountPrivacy, 1))
        elif identity == "deleted":
            session.get(TrustedVerifier, 1).pin_hash = "deleted$fixture"
    page = browser.get("/membership", follow_redirects=False)
    assert_no_offer(page)
    data = fields(csrf)
    if student:
        assert 400 <= checkout(browser, data).status_code < 500  # Claiming an unsigned-in adult role.
        data["as_account"] = "student"
    if identity == "ambiguous":
        del data["as_account"]
    assert 400 <= checkout(browser, data).status_code < 500
    assert not stripe_http.calls and count(db, CheckoutAttempt) == count(db, BillingAccount) == 0


def test_disabled_or_manipulated_plan_and_owner_are_rejected(db, stripe_http, monkeypatch):
    monkeypatch.setenv("LAUNCH_SALE_ENABLED", "true")
    browser = client("adult")
    data = fields(page_csrf(browser))
    for change in ({"plan_code": "friendship_annual_30"}, {"plan_code": "unlisted"},
                   {"amount": "1"}, {"price_id": "price_override"}, {"billing_account_id": "2"}):
        assert checkout(browser, data | change).status_code == 400
    assert checkout(browser, data | {"provider": "paypal"}).status_code == 503
    conflicting = urlencode([*data.items(), ("plan_code", ANNUAL)])
    assert browser.post("/membership/checkout", content=conflicting,
                        headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 400
    monkeypatch.delenv("STRIPE_PRICE_FULL_ANNUAL_49")
    assert checkout(browser, data | {"plan_code": ANNUAL}).status_code == 400
    assert not stripe_http.calls and count(db, CheckoutAttempt) == 0


def test_public_retry_is_scoped_to_one_plan_and_owner(db, stripe_http):
    browser = client("adult")
    data = fields(page_csrf(browser))
    assert checkout(browser, data).status_code == 303
    with db() as session:
        reference = session.scalars(select(CheckoutAttempt.reference)).one()
    assert checkout(browser, data | {"checkout_reference": reference}).status_code == 303
    calls = len(stripe_http.calls)
    other = client("adult", 2)
    assert checkout(other, fields(page_csrf(other)) | {"checkout_reference": reference}).status_code == 400
    assert checkout(browser, data | {"plan_code": ANNUAL}).status_code == 400
    assert checkout(browser, data | {"new_attempt": "true"}).status_code == 400
    assert len(stripe_http.calls) == calls and count(db, CheckoutAttempt) == 1
    keys = [call[3]["Idempotency-Key"] for call in stripe_http.calls if call[0].lower() == "post"]
    assert keys == [reference, reference]


def test_public_checkout_cannot_issue_private_pending_authorization(db, stripe_http, monkeypatch):
    monkeypatch.setenv("PUBLIC_SUBSCRIPTIONS_ENABLED", "false")
    _, attempt_id, _ = operator_checkout.authorize(db, adult_id=1, plan_code=MONTHLY,
                                                  mode=stripe_http.mode, operator="test-op", approval="test-approval")
    with db() as session:
        reference = session.get(CheckoutAttempt, attempt_id).reference
    monkeypatch.setenv("PUBLIC_SUBSCRIPTIONS_ENABLED", "true")
    browser = client("adult")
    page = browser.get("/membership")
    assert_no_offer(page)
    data = fields(page.context["csrf"])
    for extra in ({}, {"checkout_reference": reference}, {"plan_code": ANNUAL}):
        assert checkout(browser, data | extra).status_code == 400
    assert not stripe_http.calls and count(db, CheckoutAttempt) == 1


@pytest.mark.parametrize("outcome", ["paid", "unpaid_expired", "unknown"])
def test_new_purchase_after_local_expiry_requires_confirmed_unpaid_session(db, stripe_http, monkeypatch, outcome):
    browser = client("adult")
    data = fields(page_csrf(browser))
    if outcome == "unknown":
        stripe_http.error = True
    initial = checkout(browser, data)
    assert initial.status_code == (503 if outcome == "unknown" else 303)
    with db() as session:
        attempt = session.scalars(select(CheckoutAttempt)).one()
    monkeypatch.setattr(billing_providers, "clock", lambda: memberships.utc(attempt.expires_at) + timedelta(seconds=1))
    stripe_http.error = False
    if outcome == "paid":
        stripe_http.checkout.update(status="complete", payment_status="paid")
    elif outcome == "unpaid_expired":
        stripe_http.checkout.update(status="expired", payment_status="unpaid", subscription=None, invoice=None)
        original_request = stripe_http.request

        def new_session(method, url, headers, post_data=None, **kwargs):
            if method.lower() == "post":
                stripe_http.checkout.update(id=f"cs_{stripe_http.mode}_second")
            return original_request(method, url, headers, post_data, **kwargs)

        monkeypatch.setattr(stripe_http, "request", new_session)
    response = checkout(browser, data)
    if outcome == "unpaid_expired":
        assert response.status_code == 303 and count(db, CheckoutAttempt) == 2
        keys = [call[3]["Idempotency-Key"] for call in stripe_http.calls if call[0].lower() == "post"]
        assert len(set(keys)) == 2
    else:
        assert response.status_code == 400 and count(db, CheckoutAttempt) == 1
        assert sum(call[0].lower() == "post" for call in stripe_http.calls) == (1 if outcome == "paid" else 0)
    assert count(db, Membership) == 0
