"""Personal website billing using disposable records and Stripe's SDK with a fake transport."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app import billing_providers as billing, memberships, operator_checkout
from app.age_models import AccountPrivacy
from app.age_privacy import declare_age
from app.billing_config import SUBSCRIPTION_TERMS_VERSION
from app.models import (BillingEventApplication, BillingPaymentEffect, CheckoutAttempt, Membership,
                        MembershipAuditEvent, PracticeChart, QuestCompletion, RewardGrant)
from app.practice_duration import chart_seconds
from tests.test_billing_replacement import db, sqlite_db
from tests.test_memberships import ADMIN, NOW, client
from tests.test_operator_checkout import approve, http
from tests.test_stripe_billing import adapter, count, signed
from tests.test_website_checkout import ANNUAL, MONTHLY, PLAY_WEBVIEW, fields


def assert_terms_before_checkout(page):
    form = page.text.split('class="card membership-purchase"', 1)[1].split("</form>", 1)[0]
    before, assent = form.split('name="terms_version"', 1)
    for term in ("renews automatically", "Cancel automatic renewal", "no voluntary satisfaction refunds",
                 "Woodshed Practice Promise", "initial annual purchase only", "within 14 days", "within 30 days",
                 "3 distinct Practice Charts", "20 total minutes of instrumental practice", "1 Bonus Challenge",
                 "never resets at annual renewal", "free account before purchase does not disqualify",
                 "current paid monthly or annual term anytime", "No practice or explanation",
                 "Earlier terms are not automatically refundable", "Refunds required by law"):
        assert term in before
    assert "<details" not in before and 'type="checkbox"' in before
    assert "checked" not in assent.split(">", 1)[0] and "required" in assent.split(">", 1)[0]
    assert "trial" not in form.lower() and "satisfaction guaranteed" not in form.lower()


def test_public_disclosure_and_assent_receipt(db, http, monkeypatch):
    monkeypatch.setenv("PUBLIC_SUBSCRIPTIONS_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_SUBSCRIPTION_REFUND_POLICY", "Obsolete wording")
    owner = client("adult")
    page = owner.get("/membership")
    assert_terms_before_checkout(page)
    assert "$7/month" in page.text and "$49/year" in page.text and "Obsolete wording" not in page.text
    form = fields(page.context["csrf"], ANNUAL)
    for version in ("", "previous-policy"):
        assert owner.post("/membership/checkout", data=form | {"terms_version": version}).status_code == 400
    assert not http.calls and count(db, CheckoutAttempt) == 0
    for _ in range(2):
        assert owner.post("/membership/checkout", data=form, follow_redirects=False).status_code == 303
    with db() as session:
        receipt = session.scalars(select(MembershipAuditEvent).where(
            MembershipAuditEvent.action == "subscription_terms_accepted")).one()
        attempt = session.get(CheckoutAttempt, receipt.checkout_attempt_id)
        assert receipt.actor_type == "adult" and receipt.actor_id == 1 and receipt.created_at
        assert receipt.details == {"version": SUBSCRIPTION_TERMS_VERSION, "plan_code": ANNUAL,
                                   "interval": "year", "amount_cents": 4900, "currency": "USD"}
        assert attempt.provider_checkout_id == http.checkout["id"]
    checkout_params = next(params for method, path, params, _ in http.calls
                           if method.lower() == "post" and path == "/v1/checkout/sessions")
    assert not any("trial" in key for key in checkout_params)


def test_private_review_requires_issued_session_bound_adult_and_consent(db, http):
    approval, _, _ = approve(db, http, plan_code=ANNUAL)
    path = f"/membership/private/{approval}"
    owner = client("adult")
    assert owner.get(path).status_code == 409 and not http.calls
    assert urlsplit(operator_checkout.issue(db, approval)).path == path
    calls_after_issue = len(http.calls)
    page = owner.get(path)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert_terms_before_checkout(page)
    assert "$49/year" in page.text and "$7/month" not in page.text and "checkout.stripe.com" not in page.text
    form = fields(page.context["csrf"], ANNUAL)
    assert client("adult", 2).get(path).status_code == 404
    signed_out = client().get(path)
    assert "/trusted-verifiers/login" in signed_out.text and "terms_version" not in signed_out.text
    assert "/trusted-verifiers/login" in client("student").get(path).text
    other = client("adult", 2)
    other_csrf = other.get("/membership").context["csrf"]
    assert other.post(path, data=form | {"csrf": other_csrf}).status_code == 404
    assert owner.get(path, headers={"User-Agent": PLAY_WEBVIEW}).status_code == 403
    assert owner.post(path, data=form, headers={"User-Agent": PLAY_WEBVIEW}).status_code == 403
    assert owner.post(path, data=form | {"csrf": "invalid"}).status_code == 403
    assert owner.post(path, data=form | {"terms_version": ""}).status_code == 400
    assert owner.post(path, data=form | {"plan_code": MONTHLY}).status_code == 400
    assert len(http.calls) == calls_after_issue
    result = owner.post(path, data=form, follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"] == http.checkout["url"]
    assert all(method.lower() == "get" for method, *_ in http.calls[calls_after_issue:])
    returned = owner.get("/membership?checkout=returned")
    assert "Refresh membership" in returned.text and "help stopping renewal" in returned.text
    assert count(db, Membership) == 0
    http.checkout["status"] = "expired"
    assert owner.post(path, data=form, follow_redirects=False).status_code == 400


@pytest.fixture
def paid_membership(db, http, request):
    plan = getattr(request, "param", ANNUAL)
    approval, attempt_id, _ = approve(db, http, plan_code=plan)
    operator_checkout.issue(db, approval)
    with db() as session:
        reference = session.get(CheckoutAttempt, attempt_id).reference
    record, _ = billing.process_webhook(db, "stripe", *signed(http.paid_event(reference, plan)))
    assert record.status == "processed"
    with db.begin() as session:
        member = session.scalars(select(Membership)).one()
        memberships.add_seat(session, member.id, 1, ADMIN)
        return member.id, reference


def test_paid_owner_stops_renewal_with_sales_closed(db, http, paid_membership, monkeypatch):
    member_id, _ = paid_membership
    path = f"/membership/{member_id}/cancel-renewal"
    owner = client("adult")
    page = owner.get("/membership")
    form = {"csrf": page.context["csrf"], "as_account": "adult"}
    assert path in page.text and "Request a refund" in page.text
    assert path not in owner.get("/membership", headers={"User-Agent": PLAY_WEBVIEW}).text
    assert owner.post(path, data=form, headers={"User-Agent": PLAY_WEBVIEW}).status_code == 403
    assert owner.post(path, data=form | {"csrf": "invalid"}).status_code == 403
    with db.begin() as session:
        declare_age(session, 1, "adult")
    for kind, owner_id in (("adult", 2), ("student", 1)):
        other = client(kind, owner_id)
        another = other.get("/membership")
        assert another.status_code == 200, (kind, another.url.path, another.text[:200])
        assert path not in another.text
        assert other.post(path, data={"csrf": another.context["csrf"], "as_account": kind}).status_code == 404
    assert not any("/v1/subscriptions/" in row[1] for row in http.calls)
    for _ in range(2):
        result = owner.post(path, data=form)
        assert "Stripe confirmed cancellation effective" in result.text and "Reconfirm cancellation" in result.text
        assert "Paid access active" in result.text
    assert "Stripe confirmed cancellation effective" in owner.get("/membership").text
    updates = [row for row in http.calls if row[0].lower() == "post" and "/v1/subscriptions/" in row[1]]
    assert len(updates) == 1 and http.subscription["cancel_at_period_end"] is True
    with db() as session:
        receipt = session.scalars(select(MembershipAuditEvent).where(
            MembershipAuditEvent.action == billing.RENEWAL_STOPPED)).one()
        member = session.get(Membership, member_id)
        end = memberships.utc(member.access_until)
        assert receipt.membership_id == member_id and datetime.fromisoformat(receipt.details["effective_at"]) == end
        assert member.revoked_at is None and member.status == "active"
        assert memberships.student_has_full_access(session, 1, at=end - timedelta(seconds=1))
        assert not memberships.student_has_full_access(session, 1, at=end)
    monkeypatch.setattr(memberships, "clock", lambda: end)
    expired = owner.get("/membership")
    assert "Paid access inactive" in expired.text and "Stripe confirmed cancellation effective" in expired.text


def test_unconfirmed_cancellation_is_retryable(db, http, paid_membership, monkeypatch):
    member_id, _ = paid_membership
    owner = client("adult")
    path = f"/membership/{member_id}/cancel-renewal"
    form = {"csrf": owner.get("/membership").context["csrf"], "as_account": "adult"}
    with monkeypatch.context() as mismatch:
        mismatch.setitem(http.subscription, "customer", "cus_unrelated")
        assert "could not confirm cancellation" in owner.post(path, data=form).text
    assert http.subscription["cancel_at_period_end"] is False
    original = http.request

    def lose_update_response(method, url, headers, post_data=None, **kwargs):
        response = original(method, url, headers, post_data, **kwargs)
        if method.lower() == "post" and "/v1/subscriptions/" in url:
            raise billing.BillingUnavailable("Simulated lost response")
        return response

    with monkeypatch.context() as lost:
        lost.setattr(http, "request", lose_update_response)
        assert "could not confirm cancellation" in owner.post(path, data=form).text
    assert http.subscription["cancel_at_period_end"] is True
    with db() as session:
        assert not session.scalar(select(MembershipAuditEvent.id).where(MembershipAuditEvent.action == billing.RENEWAL_STOPPED))
    assert "Stripe confirmed cancellation effective" in owner.post(path, data=form).text


@pytest.mark.parametrize("state,days,updates", [
    ("already_scheduled", 365, 0), ("earlier_date", 12, 0),
    ("later_date", 365, 1), ("ended", 3, 0),
])
def test_stripe_confirms_effective_end_for_existing_schedules(adapter, state, days, updates):
    provider, http = adapter
    http.paid_event("annual-attempt", ANNUAL)
    if state == "already_scheduled":
        http.subscription["cancel_at_period_end"] = True
    elif state == "ended":
        http.subscription.update(status="canceled", ended_at=int((NOW + timedelta(days=3)).timestamp()),
                                 canceled_at=int(NOW.timestamp()))
    else:
        offset = 12 if state == "earlier_date" else 400
        http.subscription["cancel_at"] = int((NOW + timedelta(days=offset)).timestamp())
    effective = provider.cancel_subscription(external_subscription_id="sub_woodshed", external_customer_id="cus_woodshed")
    assert effective == NOW + timedelta(days=days)
    assert sum(method.lower() == "post" for method, *_ in http.calls) == updates


def test_concurrent_confirmations_record_one_receipt(db, http, paid_membership):
    member_id, _ = paid_membership
    start = Barrier(2)

    def cancel():
        start.wait(timeout=10)
        return billing.cancel_website_renewal(db, memberships.Actor("adult", 1), member_id)

    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(cancel) for _ in range(2)]
        assert results[0].result() == results[1].result()
    with db() as session:
        assert len(session.scalars(select(MembershipAuditEvent).where(
            MembershipAuditEvent.action == billing.RENEWAL_STOPPED)).all()) == 1


def test_annual_practice_records_can_be_reviewed_after_renewal(db, http, paid_membership):
    member_id, reference = paid_membership
    renewal = http.paid_event(reference, ANNUAL, renewal=True, event_id="evt_annual_renewal")
    assert billing.process_webhook(db, "stripe", *signed(renewal))[0].status == "processed"
    with db.begin() as session:
        for days, minutes, seconds in [(-2, 25, None), (2, 7, None), (5, 9, None),
                                      (9, 4, 240), (31, 8, None), (366, 10, None)]:
            when = NOW + timedelta(days=days, hours=12)
            session.add(PracticeChart(profile_id=1, instrument="Trumpet", created_at=when, practice_date=when.date(),
                minutes=minutes, source="pristine" if seconds else "p-book", detected_playing_seconds=seconds))
        completed = NOW + timedelta(days=8, hours=12)
        session.add(QuestCompletion(profile_id=1, quest_id="brass", activity_date=completed.date(),
                                    logged_minutes=5, reward_amount=5, completed_at=completed))
        session.add(RewardGrant(profile_id=1, reward_type="dandelion", amount=5, created_at=completed,
            source_key=f"bonus-challenge:{completed.date()}:brass", category_key="board-bonus-v1:trumpet"))

    with db() as session:
        original = session.scalar(select(BillingEventApplication.facts)
            .join(BillingPaymentEffect, BillingPaymentEffect.event_id == BillingEventApplication.event_id)
            .where(BillingPaymentEffect.payment_reference == "in_first"))
        start = datetime.fromisoformat(original["occurred_at"])
        end = start + timedelta(days=30)
        seat, = memberships.active_seats(session, member_id)
        profile_id = seat.profile_id
        charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == profile_id,
            PracticeChart.created_at >= start, PracticeChart.created_at < end)).all()
        assert len({chart.id for chart in charts}) == 3 and sum(map(chart_seconds, charts)) == 1200
        bonus = session.scalars(select(QuestCompletion).where(QuestCompletion.profile_id == profile_id,
            QuestCompletion.completed_at >= start, QuestCompletion.completed_at < end)).one()
        reward = session.scalars(select(RewardGrant).where(RewardGrant.profile_id == profile_id,
            RewardGrant.source_key == f"bonus-challenge:{bonus.activity_date}:{bonus.quest_id}")).one()
        assert reward.created_at == bonus.completed_at


@pytest.mark.parametrize("paid_membership", [MONTHLY, ANNUAL], indirect=True)
def test_original_payment_and_under13_current_term_manual_refund(db, http, paid_membership):
    member_id, reference = paid_membership
    with db() as session:
        plan = session.get(Membership, member_id).plan_code
    renewal = http.paid_event(reference, plan, renewal=True, event_id="evt_renewal")
    record, _ = billing.process_webhook(db, "stripe", *signed(renewal))
    assert record.status == "processed"
    with db.begin() as session:
        age = session.get(AccountPrivacy, 1)
        if age is None:
            age = AccountPrivacy(profile_id=1, declared_at=NOW)
            session.add(age)
        age.age_band = "under13"
    buyer = client(both=True)
    page = buyer.get("/membership?as_account=adult")
    assert "Private member" in page.text and "Student 1" not in page.text
    assert buyer.get("/membership?as_account=student").status_code == 403
    result = buyer.post(f"/membership/{member_id}/cancel-renewal",
                        data={"csrf": page.context["csrf"], "as_account": "adult"})
    assert "Stripe confirmed cancellation effective" in result.text
    with db.begin() as session:
        records = session.execute(select(BillingPaymentEffect.payment_reference, BillingEventApplication.facts)
            .join(BillingEventApplication, BillingEventApplication.event_id == BillingPaymentEffect.event_id)
            .order_by(BillingPaymentEffect.paid_through)).all()
        assert [row.payment_reference for row in records] == ["in_first", "in_renewal"]
        initial = datetime.fromisoformat(records[0].facts["occurred_at"])
        current = datetime.fromisoformat(records[1].facts["period_start"])
        assert initial == NOW and current == initial + timedelta(days=365 if plan == ANNUAL else 31)
        assert memberships.utc(session.get(Membership, member_id).starts_at) == initial
        assert initial + timedelta(days=30) < current < datetime.fromisoformat(records[1].facts["period_end"])
        assert session.scalar(select(PracticeChart.id)) is None and session.scalar(select(QuestCompletion.id)) is None
        # Support has independently confirmed the purchasing guardian and full current-invoice refund.
        # Its existing access action accepts billing references alone, even at annual renewal.
        memberships.end_refunded_stripe_membership(session, member_id, ADMIN, invoice_id="in_renewal",
                                                   refund_id="re_current", confirmed=True)
        receipt = session.scalars(select(MembershipAuditEvent).where(
            MembershipAuditEvent.action == memberships.STRIPE_FULL_REFUND_ACCESS_ENDED)).one()
        assert receipt.details["invoice_id"] == "in_renewal" and receipt.billing_event_id == record.id
