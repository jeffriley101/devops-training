"""Site Admin can retry recorded work but cannot fabricate or override payment."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
import pytest
from tests.test_memberships import db, client, page_csrf, grant, NOW
from tests.test_billing_hardening import attempt, payment, deliver, counts
from tests.test_billing_replacement import pending, apply, history, replacement_payment
from app import billing_recovery as recovery, billing_providers as b, memberships as m
from app.models import (BillingProviderEvent, BillingEventApplication, MembershipAuditEvent,
                        Membership, ProviderSubscription, CheckoutAttempt, MembershipSeat)
from app.models import BillingPaymentEffect


def admin_client():
    c = client()
    csrf = page_csrf(c, "/admin/login")
    assert c.post("/admin/login", data={"csrf": csrf, "token": "test-site-admin"}).status_code == 200
    return c, page_csrf(c, "/admin/billing-recovery")


def audit_results(db, event_id):
    with db() as s:
        return list(s.scalars(select(MembershipAuditEvent).where(MembershipAuditEvent.billing_event_id == event_id)
                             .order_by(MembershipAuditEvent.id)))


def test_queue_sanitized_context_and_success_exclusion(db, monkeypatch):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    with db() as s:
        app = s.scalar(select(BillingEventApplication))
        app.facts = {**app.facts, "extra_secret": "DO-NOT-EXPOSE"}
        s.commit()
    c, _ = admin_client()
    response = c.get("/admin/billing-recovery")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "Billing Recovery" in response.text and "Retry recorded event" in response.text
    assert "DO-NOT-EXPOSE" not in response.text and "payer1@" not in response.text
    assert row.reference not in response.text and "external_event_id" not in response.text
    with db() as s:
        app = s.scalar(select(BillingEventApplication))
        app.facts = {k: v for k, v in app.facts.items() if k != "extra_secret"}
        s.commit()
    assert apply(db, event_id) == "processed"
    assert "No billing events require attention" in c.get("/admin/billing-recovery").text


@pytest.mark.parametrize("kind", [None, "student", "adult"])
def test_recovery_denies_non_admin_and_seated_students(db, kind):
    grant(db, "student")
    c = client(kind)
    assert c.get("/admin/billing-recovery").status_code == 403
    assert c.post("/admin/billing-recovery/1/retry", data={"csrf": "anything"}).status_code == 403


def test_retry_csrf_get_and_overrides(db):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    c, csrf = admin_client()
    url = f"/admin/billing-recovery/{event_id}/retry"
    assert c.get(url).status_code == 405
    assert c.post(url, data={"csrf": "bad"}).status_code == 403
    assert c.post(url, data={"csrf": csrf}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(url, data={"csrf": csrf}, headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post(url, data={"csrf": csrf, "paid_through": "2099-01-01"}).status_code == 400
    assert not audit_results(db, event_id)
    assert counts(db) == (0, 0, 0, 0)


def test_retry_uses_normal_processing_and_audits_success_and_stale(db, monkeypatch):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    original, calls = b.apply_locked_event, []
    def tracked(session, record, app, locked_accounts):
        calls.append(record.id)
        return original(session, record, app, locked_accounts)
    monkeypatch.setattr(b, "apply_locked_event", tracked)
    c, csrf = admin_client()
    url = f"/admin/billing-recovery/{event_id}/retry"
    result = c.post(url, data={"csrf": csrf}, headers={"Origin": "http://testserver"})
    assert result.status_code == 200 and "applied successfully" in result.text
    assert c.post(url, data={"csrf": csrf}).status_code == 200
    assert calls == [event_id] and counts(db) == (1, 1, 1, 1)
    audits = audit_results(db, event_id)
    assert [a.action for a in audits] == ["billing_retry_requested", "billing_retry_result"] * 2
    assert [a.details.get("outcome") for a in audits[1::2]] == ["succeeded", "no_longer_retryable"]
    assert all(a.actor_type == "admin" and a.created_at for a in audits)
    assert all(set(a.details) <= {"request_id", "outcome", "reason"} for a in audits)
    assert audits[0].membership_id is None and audits[1].membership_id is not None
    assert "Billing Recovery" in c.get("/admin/membership").text


def test_unknown_correlation_blocked_then_available(db, monkeypatch):
    row = attempt(db)
    unknown = pending(db, payment(external_event_id="unknown"))
    result_id = recovery.retry(db, unknown, m.Actor("admin"))
    with db() as s:
        assert s.get(MembershipAuditEvent, result_id).details["outcome"] == "blocked"
        assert s.get(BillingEventApplication, 1).attempts == 0
    deliver(db, monkeypatch, payment(row.reference, external_event_id="correlated"))
    recovery.retry(db, unknown, m.Actor("admin"))
    assert audit_results(db, unknown)[-1].details["outcome"] == "succeeded"
    assert counts(db) == (1, 1, 1, 1)


def test_retry_seat_conflict_remains_recoverable(db):
    row = attempt(db)
    other = grant(db)
    with db() as s:
        m.add_seat(s, other, 1, m.Actor("admin"))
        s.commit()
    event_id = pending(db, payment(row.reference))
    recovery.retry(db, event_id, m.Actor("admin"))
    assert audit_results(db, event_id)[-1].details["outcome"] == "still_recoverable"
    with db() as s:
        assert s.get(CheckoutAttempt, row.id).status == "recoverable"
        assert s.get(BillingProviderEvent, event_id).processed_at is None
    assert counts(db) == (1, 0, 1, 0)


def test_expired_authorization_cannot_be_forced(db, monkeypatch):
    row = attempt(db)
    monkeypatch.setattr(b, "clock", lambda: NOW + timedelta(days=1))
    event_id = pending(db, payment(row.reference, occurred_at=NOW + timedelta(days=1)))
    recovery.retry(db, event_id, m.Actor("admin"))
    assert audit_results(db, event_id)[-1].details["outcome"] == "blocked"
    c, csrf = admin_client()
    assert "Retry recorded event" not in c.get("/admin/billing-recovery").text
    c.post(f"/admin/billing-recovery/{event_id}/retry", data={"csrf": csrf})
    assert audit_results(db, event_id)[-1].details["outcome"] == "blocked"
    assert counts(db) == (0, 0, 0, 0)


def test_retry_failure_retains_request_and_can_reenter(db, monkeypatch):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    original = b.apply_locked_event
    def failed(session, record, app, locked_accounts):
        original(session, record, app, locked_accounts)
        raise OperationalError("DO NOT EXPOSE", {}, Exception("SECRET"))
    with monkeypatch.context() as patch:
        patch.setattr(b, "apply_locked_event", failed)
        recovery.retry(db, event_id, m.Actor("admin"))
    assert counts(db) == (0, 0, 0, 0)
    audits = audit_results(db, event_id)
    assert len(audits) == 2 and audits[-1].details["outcome"] == "still_recoverable"
    assert "SECRET" not in str([a.details for a in audits])
    recovery.retry(db, event_id, m.Actor("admin"))
    assert counts(db) == (1, 1, 1, 1)


def test_two_admin_retries_are_idempotent(db):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: recovery.retry(db, event_id, m.Actor("admin")), range(2)))
    assert counts(db) == (1, 1, 1, 1)
    assert sorted(a.details["outcome"] for a in audit_results(db, event_id) if a.action == "billing_retry_result") == ["no_longer_retryable", "succeeded"]


def test_admin_retry_races_normal_processing(db):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    with ThreadPoolExecutor(2) as pool:
        admin = pool.submit(recovery.retry, db, event_id, m.Actor("admin"))
        auto = pool.submit(apply, db, event_id)
        admin.result(timeout=15)
        assert auto.result(timeout=15) == "processed"
    assert counts(db) == (1, 1, 1, 1)
    assert len(audit_results(db, event_id)) == 2


def test_admin_retry_races_replacement(db, monkeypatch):
    history(db, monkeypatch)
    row = attempt(db)
    old = pending(db, payment(external_event_id="old-recovery", payment_reference="old-recovery"))
    new = pending(db, replacement_payment(row))
    with ThreadPoolExecutor(2) as pool:
        admin = pool.submit(recovery.retry, db, old, m.Actor("admin"))
        auto = pool.submit(apply, db, new)
        admin.result(timeout=15)
        assert auto.result(timeout=15) == "failed"
    with db() as s:
        assert s.scalar(select(func.count()).select_from(Membership).where(Membership.status == "active")) == 0
    assert audit_results(db, old)[-1].details["outcome"] == "blocked"


def test_orphan_recoverable_checkout_visible_without_retry(db):
    row = attempt(db)
    with db() as s:
        s.get(CheckoutAttempt, row.id).status = "recoverable"
        s.commit()
    c, _ = admin_client()
    page = c.get("/admin/billing-recovery")
    assert "Recovery state has no correlated event" in page.text
    assert "Retry recorded event" not in page.text


def test_payment_effect_mismatch_is_reconciliation_only(db, monkeypatch):
    row = attempt(db)
    event, _ = deliver(db, monkeypatch, payment(row.reference))
    with db() as s:
        effect = s.scalar(select(BillingPaymentEffect))
        effect.paid_through = NOW + timedelta(days=730)
        s.commit()
    c, csrf = admin_client()
    page = c.get("/admin/billing-recovery")
    assert "recorded payment effect disagrees" in page.text and "Retry recorded event" not in page.text
    c.post(f"/admin/billing-recovery/{event.id}/retry", data={"csrf": csrf})
    assert audit_results(db, event.id)[-1].details["outcome"] == "blocked"


def test_non_admin_service_denied_and_missing_event_rejected(db):
    with pytest.raises(PermissionError):
        recovery.retry(db, 1, m.Actor("student", 1))
    c, csrf = admin_client()
    assert c.post("/admin/billing-recovery/999/retry", data={"csrf": csrf}).status_code == 404


def test_queue_paginates_without_mutation(db):
    for index in range(4):
        pending(db, payment(external_event_id=f"unknown-{index}"))
    with db() as s:
        first = recovery.queue(s, limit=2)
        second = recovery.queue(s, before=first["next_before"], limit=2)
        assert {r["event_id"] for r in first["items"]}.isdisjoint({r["event_id"] for r in second["items"]})
        assert len(first["items"]) == len(second["items"]) == 2
        assert s.scalar(select(func.count()).select_from(MembershipAuditEvent)) == 0


def test_successful_legacy_event_not_recovery_work(db):
    with db() as s:
        event = BillingProviderEvent(provider="stripe", external_event_id="legacy-success", received_at=NOW,
            processed_at=NOW, status="processed", payload_hash="legacy")
        s.add(event)
        s.commit()
        event_id = event.id
        assert recovery.queue(s)["items"] == []
    recovery.retry(db, event_id, m.Actor("admin"))
    assert audit_results(db, event_id)[-1].details["outcome"] == "no_longer_retryable"


def test_result_audit_failure_retains_request_and_rolls_back_entitlement(db, monkeypatch):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    original = recovery._audit
    def fail_result(session, id, action, **details):
        if action == "billing_retry_result":
            raise OperationalError("test outage", {}, Exception("not logged"))
        return original(session, id, action, **details)
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "_audit", fail_result)
        with pytest.raises(OperationalError):
            recovery.retry(db, event_id, m.Actor("admin"))
    assert counts(db) == (0, 0, 0, 0)
    assert [a.action for a in audit_results(db, event_id)] == ["billing_retry_requested"]
    recovery.retry(db, event_id, m.Actor("admin"))
    assert counts(db) == (1, 1, 1, 1)
