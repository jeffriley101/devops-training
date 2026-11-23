"""Manual refund confirmation against disposable databases and a simulated Stripe transport."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import event, select

from app import billing_providers as billing, billing_reconciliation, billing_recovery, memberships as m
from app import operator_checkout
from app.age_privacy import declare_age
from app.models import (BillingEventApplication, BillingPaymentEffect, BillingProviderEvent, CheckoutAttempt,
                        Membership, MembershipAuditEvent, ProviderSubscription)
from app.tester_enrollments import C001, enroll_tester
from tests.test_billing_hardening import attempt, deliver as deliver_stub, payment
from tests.test_billing_replacement import db, sqlite_db
from tests.test_memberships import ADMIN, NOW, client, grant, page_csrf
from tests.test_operator_checkout import approve, http
from tests.test_stripe_billing import adapter, signed


@pytest.fixture
def paid(db, http):
    approval_id, attempt_id, _ = approve(db, http)
    operator_checkout.issue(db, approval_id)
    with db() as session:
        reference = session.get(CheckoutAttempt, attempt_id).reference
    invoice = http.paid_event(reference)
    record, _ = billing.process_webhook(db, "stripe", *signed(invoice))
    assert record.status == "processed"
    with db() as session:
        return session.scalar(select(Membership.id)), record.id, invoice


def admin_browser():
    browser = client()
    csrf = page_csrf(browser, "/admin/login")
    assert browser.post("/admin/login", data={"csrf": csrf, "token": "test-site-admin"}).status_code == 200
    return browser


def refund_form(member_id, csrf):
    return {"action": "end_refunded_stripe", "membership_id": member_id, "csrf": csrf,
            "invoice_id": "in_first", "refund_id": "re_first", "full_refund_confirmed": "yes"}


def end_access(db, member_id, invoice_id="in_first", refund_id="re_first", actor=ADMIN):
    with db.begin() as session:
        m.end_refunded_stripe_membership(session, member_id, actor, invoice_id=invoice_id,
                                         refund_id=refund_id, confirmed=True)


def refunds(session):
    return session.scalars(select(MembershipAuditEvent).where(
        MembershipAuditEvent.action == "stripe_full_refund_access_ended")).all()


def test_admin_ends_paid_seats_and_replay_cannot_restore_access(db, http, paid, monkeypatch):
    member_id, event_id, invoice = paid
    comp_id = grant(db, kind="student", owner_id=3)
    with db.begin() as session:
        for student in (1, 2):
            declare_age(session, student, "adult")
            m.add_seat(session, member_id, student, ADMIN)
        enroll_tester(session, 2, C001, NOW)
        assert all(m.student_has_full_access(session, student) for student in (1, 2, 3))
        paid_through = session.get(Membership, member_id).access_until
        _, token = m.invite_student(session, member_id, "invitee@example.test", ADMIN)

    admin = admin_browser()
    page = admin.get(f"/admin/membership?membership_id={member_id}")
    form = refund_form(member_id, page.context["csrf"])
    assert 'name="refund_id"' in page.text
    assert 'name="refund_id"' not in admin.get(f"/admin/membership?membership_id={comp_id}").text
    calls_before = len(http.calls)
    result = admin.post("/admin/membership", data=form)
    assert result.status_code == 200 and "Paid access ended after full Stripe refund" in result.text
    assert "re_first" in result.text and "in_first" in result.text and 'name="refund_id"' not in result.text
    with db() as session:
        audit, = refunds(session)
        assert (audit.membership_id, audit.billing_event_id, audit.actor_type) == (member_id, event_id, "admin")
        assert audit.details == {"refund_id": "re_first", "invoice_id": "in_first", "subscription_id": "sub_woodshed"}
        assert audit.created_at and session.get(Membership, member_id).revoked_at
        revoked_at = session.get(Membership, member_id).revoked_at
        audit_ids = list(session.scalars(select(MembershipAuditEvent.id).order_by(MembershipAuditEvent.id)))
    monkeypatch.setattr(m, "clock", lambda: NOW + timedelta(hours=1))
    assert admin.post("/admin/membership", data=form).status_code == 200
    assert len(http.calls) == calls_before
    with db() as session:
        assert session.get(Membership, member_id).revoked_at == revoked_at
        assert list(session.scalars(select(MembershipAuditEvent.id).order_by(MembershipAuditEvent.id))) == audit_ids
    with pytest.raises(ValueError, match="not active"), db.begin() as session:
        m.claim_invitation(session, token, m.Actor("student", 4))

    original, fresh = billing.process_webhook(db, "stripe", *signed(invoice))
    assert not fresh and original.id == event_id and original.status == "processed"
    alias = deepcopy(invoice)
    alias["id"] = "evt_second_delivery"
    http.events[alias["id"]] = alias
    other, fresh = billing.process_webhook(db, "stripe", *signed(alias))
    assert fresh and other.error_code == "membership_ended"
    retried = billing_recovery.retry(db, other.id, ADMIN)
    # Stripe inspection timestamps use wall time; the paid period fixture is historical.
    monkeypatch.setattr(billing, "clock", lambda: datetime.now(timezone.utc))
    inspected = billing_reconciliation.inspect(db, "event", event_id, ADMIN)
    with db() as session:
        assert session.get(MembershipAuditEvent, retried).details["outcome"] == "blocked"
        assert session.get(MembershipAuditEvent, inspected).details["classification"] == "financial_decision_required"
        member = session.get(Membership, member_id)
        assert (member.status, member.revoked_at, member.access_until) == ("revoked", revoked_at, paid_through)
        assert not m.active_seats(session, member_id) and not m.student_has_full_access(session, 1)
        assert m.student_has_full_access(session, 2) and m.student_has_full_access(session, 3)
        assert m.membership_is_active(session.get(Membership, comp_id))
        effect, = session.scalars(select(BillingPaymentEffect))
        assert (effect.event_id, effect.payment_reference, effect.paid_through) == (event_id, "in_first", paid_through)
        assert session.get(BillingProviderEvent, event_id).status == "processed"
        facts = session.scalar(select(BillingEventApplication.facts).where(BillingEventApplication.event_id == event_id))
        assert facts["stripe_invoice"]["payment_intent_id"] == "pi_first"
        sub = session.get(ProviderSubscription, effect.subscription_id)
        assert sub.membership_id == member_id and sub.provider_status == "unknown" and sub.terminated_at is None
        assert not sub.cancel_at_period_end and len(refunds(session)) == 1
    owner_page = client("adult").get("/membership")
    assert "Paid access ended" in owner_page.text and "re_first" not in owner_page.text
    replacement_id = grant(db, kind="adult", owner_id=1)
    with db.begin() as session:
        m.add_seat(session, replacement_id, 1, ADMIN)
    end_access(db, member_id)
    assert not billing.process_webhook(db, "stripe", *signed(invoice))[1]
    with db() as session:
        assert m.student_has_full_access(session, 1) and m.active_seats(session, replacement_id)
        assert not m.membership_is_active(session.get(Membership, member_id))


def test_admin_csrf_confirmation_and_correct_invoice_are_required(db, paid):
    member_id, _, _ = paid
    comp_id = grant(db, kind="student", owner_id=5)
    owner = client("adult")
    form = refund_form(member_id, page_csrf(owner))
    assert client().post("/admin/membership", data=form).status_code == 403
    assert owner.post("/admin/membership", data=form).status_code == 403
    assert owner.post("/membership/actions", data=form).status_code == 409
    with pytest.raises(PermissionError):
        end_access(db, member_id, actor=m.Actor("adult", 1))
    admin = admin_browser()
    form["csrf"] = page_csrf(admin, "/admin/membership")
    assert admin.post("/admin/membership", data=form | {"csrf": "wrong"}).status_code == 403
    assert admin.post("/admin/membership", data=form, headers={"origin": "https://other.example"}).status_code == 403
    for change in ({"full_refund_confirmed": ""}, {"full_refund_confirmed": "partial"},
                   {"invoice_id": "pi_first"}, {"invoice_id": "in_unknown"}, {"refund_id": ""},
                   {"refund_id": "ch_first"}, {"refund_id": "re_" + "x" * 253},
                   {"membership_id": comp_id}, {"membership_id": 9999}, {"action": "revoke"}):
        assert admin.post("/admin/membership", data=form | change).status_code == 409
    with db() as session:
        assert not refunds(session)
        assert all(m.membership_is_active(session.get(Membership, mid)) for mid in (member_id, comp_id))


def test_cancellation_or_refund_notifications_leave_paid_period_intact(db, http, paid):
    member_id, _, _ = paid
    with db.begin() as session:
        m.add_seat(session, member_id, 1, ADMIN)
    for event_type in ("customer.subscription.updated", "charge.refunded", "refund.updated"):
        notice = {"id": "evt_notice", "object": "event", "type": event_type, "livemode": http.livemode}
        assert billing.process_webhook(db, "stripe", *signed(notice)) == (None, False)
    with db() as session:
        member = session.get(Membership, member_id)
        boundary = m.utc(member.access_until)
        assert member.status == "active" and member.revoked_at is None and not refunds(session)
        assert m.student_has_full_access(session, 1, at=boundary - timedelta(microseconds=1))
        assert not m.student_has_full_access(session, 1, at=boundary)


def test_refunded_older_invoice_cannot_end_newer_paid_coverage(db, http, paid):
    member_id, _, _ = paid
    with db() as session:
        reference = session.scalar(select(CheckoutAttempt.reference))
    renewal = http.paid_event(reference, event_id="evt_renewal", renewal=True)
    record, _ = billing.process_webhook(db, "stripe", *signed(renewal))
    assert record.status == "processed"
    with pytest.raises(ValueError, match="paid-through"):
        end_access(db, member_id)
    with db() as session:
        assert not refunds(session) and m.membership_is_active(session.get(Membership, member_id))
    end_access(db, member_id, "in_renewal", "re_renewal")
    with db() as session:
        assert refunds(session)[0].billing_event_id == record.id
        assert not m.membership_is_active(session.get(Membership, member_id))


def test_one_refund_reference_cannot_end_two_paid_memberships_even_concurrently(db, monkeypatch):
    targets = []
    for owner in (1, 2):
        row = attempt(db, actor=m.Actor("student", owner))
        invoice_id = f"in_owner_{owner}"
        record, _ = deliver_stub(db, monkeypatch, payment(row.reference, payment_reference=invoice_id,
            external_subscription_id=f"sub_owner{owner}", external_event_id=f"evt_owner{owner}"))
        assert record.status == "processed"
        with db() as session:
            subscription = session.get(ProviderSubscription, session.get(CheckoutAttempt, row.id).subscription_id)
            targets.append((subscription.membership_id, invoice_id))
    with pytest.raises(ValueError, match="No verified payment"):
        end_access(db, targets[0][0], targets[1][1], "re_shared")
    gate = Barrier(2)

    def submit(target):
        gate.wait(timeout=5)
        try:
            end_access(db, *target, "re_shared")
            return True
        except ValueError as error:
            assert "different membership or invoice" in str(error)
            return False

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(submit, targets))
    assert sorted(outcomes) == [False, True]
    winner, loser = targets[outcomes.index(True)], targets[outcomes.index(False)]
    end_access(db, *winner, "re_shared")
    with pytest.raises(ValueError, match="already ended"):
        end_access(db, *winner, "re_different")
    with pytest.raises(ValueError, match="different membership or invoice"):
        end_access(db, winner[0], loser[1], "re_shared")
    with db() as session:
        audit, = refunds(session)
        assert audit.membership_id == winner[0] and audit.details["invoice_id"] == winner[1]
        assert not m.active_seats(session, winner[0]) and len(m.active_seats(session, loser[0])) == 1
        assert m.student_has_full_access(session, outcomes.index(False) + 1)


def test_audit_write_failure_rolls_back_membership_and_seats(db, paid):
    member_id, _, _ = paid
    with db.begin() as session:
        m.add_seat(session, member_id, 1, ADMIN)

    def fail_refund_audit(mapper, connection, audit):
        if audit.action == "stripe_full_refund_access_ended":
            raise RuntimeError("simulated audit failure")

    event.listen(MembershipAuditEvent, "before_insert", fail_refund_audit)
    try:
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            end_access(db, member_id)
    finally:
        event.remove(MembershipAuditEvent, "before_insert", fail_refund_audit)
    with db() as session:
        assert not refunds(session) and m.student_has_full_access(session, 1)
        assert m.membership_is_active(session.get(Membership, member_id))
    end_access(db, member_id)
