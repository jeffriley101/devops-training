"""A2 regression/races on SQLite and opt-in disposable PostgreSQL.

WW_BILLING_TEST_POSTGRES_URL must explicitly target a local ww_billing_a2_test
database. Each test uses a new schema; no production/default DATABASE_URL is read.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from tests.test_memberships import db as sqlite_db, NOW, FakeProvider, grant
from tests.test_billing_hardening import attempt, payment, deliver, CONFIG
from tests.test_billing_hardening import (
    test_concurrent_double_submit_reuses_attempt,
    test_concurrent_same_payment_provisions_once,
    test_distinct_events_same_payment_provision_once,
)
from app import billing_providers as b, memberships as m, membership_routes as routes
from app.billing_replacement import purchase_decision
from app.db import Base
from app.models import (WoodchuckProfile, TrustedVerifier, BillingAccount, Membership,
                        MembershipSeat, ProviderSubscription, CheckoutAttempt, MembershipAuditEvent,
                        BillingProviderEvent, BillingEventApplication, BillingPaymentEffect)


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request, sqlite_db, monkeypatch):
    if request.param == "sqlite":
        yield sqlite_db
        return
    raw = os.getenv("WW_BILLING_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("No explicit disposable PostgreSQL test database configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"} and url.database == "ww_billing_a2_test"
    schema = "billing_" + uuid4().hex
    bootstrap = create_engine(url)
    with bootstrap.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    bootstrap.dispose()
    engine = create_engine(url, isolation_level="READ COMMITTED", connect_args={
        "options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=15000"})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(routes, "SessionLocal", factory)
    with factory() as s:
        s.add_all([WoodchuckProfile(id=i, woodchuck_id=f"WC-MEMBER{i}", display_name=f"Student {i}",
            pin_hash="test", instrument="Trumpet", level="Beginner", goal="Practice") for i in range(1, 10)])
        s.add_all([TrustedVerifier(id=i, email=f"adult{i}@example.test", display_name=f"Adult {i}",
                                  pin_hash="test") for i in (1, 2)])
        s.commit()
    yield factory
    engine.dispose()  # Keep disposable schemas available for failure inspection.


LATER = NOW + timedelta(days=366)


def history(db, monkeypatch, kind="student", terminal=True):
    row = attempt(db, actor=m.Actor(kind, 1))
    original = payment(row.reference)
    deliver(db, monkeypatch, original)
    with db() as s:
        sub = s.scalar(select(ProviderSubscription))
        member_id, sub_id = sub.membership_id, sub.id
        m.add_seat(s, member_id, 2, m.Actor("admin"))
        s.commit()
    if terminal:
        deliver(db, monkeypatch, payment(row.reference, external_event_id="terminal",
            occurred_at=NOW + timedelta(seconds=1), provider_status="canceled", terminal=True,
            paid_through=None, payment_reference=None))
    monkeypatch.setattr(b, "clock", lambda: LATER)
    monkeypatch.setattr(m, "clock", lambda: LATER)
    return member_id, sub_id, original


def replacement_payment(row, **kwargs):
    return payment(row.reference, external_event_id="new-paid", external_subscription_id="sub-new",
                   occurred_at=LATER, period_start=LATER, period_end=LATER + timedelta(days=365),
                   paid_through=LATER + timedelta(days=365), payment_reference="new-payment", **kwargs)


def pending(db, event):
    with db() as s:
        row, _ = b.receive_verified_event(s, "stripe", event, payload_hash=event.external_event_id)
        s.commit()
        return row.id


def apply(db, event_id):
    with db() as s:
        row = b.apply_verified_event(s, event_id)
        s.commit()
        return row.status


def active_count(db):
    with db() as s:
        return s.scalar(select(func.count()).select_from(Membership).where(Membership.status == "active"))


def wait_for_pg_lock(db, pid):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        with db() as s:
            if s.scalar(text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}):
                return
        sleep(0.02)
    pytest.fail("Expected real PostgreSQL row-lock contention was not observed")


@pytest.mark.parametrize("kind,expected", [("student", [1]), ("adult", [])])
def test_replacement_preserves_history_and_owner_seats(db, monkeypatch, kind, expected):
    old_id, old_sub, original = history(db, monkeypatch, kind)
    with db() as s:
        before = {model: s.scalar(select(func.count()).select_from(model)) for model in
                  (MembershipSeat, BillingProviderEvent, BillingPaymentEffect, MembershipAuditEvent)}
        assert not m.student_has_full_access(s, 1)
    row = attempt(db, actor=m.Actor(kind, 1))
    assert attempt(db, actor=m.Actor(kind, 1)).id == row.id
    event = replacement_payment(row)
    assert deliver(db, monkeypatch, event)[0].status == "processed"
    assert deliver(db, monkeypatch, event)[0].status == "processed"
    with db() as s:
        assert s.get(Membership, old_id).status == "ended"
        assert s.get(ProviderSubscription, old_sub).external_subscription_id == "sub-1"
        new = s.scalar(select(ProviderSubscription).where(ProviderSubscription.external_subscription_id == "sub-new"))
        assert new.id != old_sub and new.membership_id != old_id
        assert [seat.profile_id for seat in m.active_seats(s, new.membership_id)] == expected
        assert m.student_has_full_access(s, 1) == (kind == "student")
        assert not m.student_has_full_access(s, 2)  # No discretionary carry-forward.
        assert s.scalar(select(func.count()).select_from(Membership)) == 2
        assert s.scalar(select(func.count()).select_from(ProviderSubscription)) == 2
        for model, count in before.items():
            assert s.scalar(select(func.count()).select_from(model)) >= count
        assert s.scalar(select(func.count()).select_from(MembershipAuditEvent).where(
            MembershipAuditEvent.membership_id == old_id, MembershipAuditEvent.action == "membership_ended")) == 1


def test_currently_entitled_cannot_replace(db, monkeypatch):
    history(db, monkeypatch)
    monkeypatch.setattr(b, "clock", lambda: NOW + timedelta(days=100))
    with pytest.raises(ValueError, match="existing membership"):
        attempt(db)


def test_expiry_without_verified_terminal_contract_blocks(db, monkeypatch):
    old, _, _ = history(db, monkeypatch, terminal=False)
    with pytest.raises(ValueError, match="confirmed ended"):
        attempt(db)
    with db() as s:
        assert s.get(Membership, old).status == "active"


@pytest.mark.parametrize("status", ["pending", "recoverable"])
def test_unresolved_old_payment_without_checkout_reference_blocks(db, monkeypatch, status):
    old, _, _ = history(db, monkeypatch)
    event_id = pending(db, payment(external_event_id="late-payment", paid_through=LATER + timedelta(days=30),
                                   payment_reference="late-payment"))
    with db() as s:
        s.scalar(select(BillingEventApplication).where(BillingEventApplication.event_id == event_id)).status = status
        s.commit()
    with pytest.raises(ValueError, match="processing review"):
        attempt(db)
    with db() as s:
        assert s.get(Membership, old).status == "active"


def test_unapplied_payment_effect_blocks(db, monkeypatch):
    old, sub, _ = history(db, monkeypatch)
    with db() as s:
        s.add(BillingPaymentEffect(subscription_id=sub, event_id=s.scalar(select(BillingProviderEvent.id)),
            payment_reference="unmaterialized", paid_through=LATER + timedelta(days=30)))
        s.commit()
    with pytest.raises(ValueError, match="processing review"):
        attempt(db)
    with db() as s:
        assert s.get(Membership, old).status == "active"


def test_pending_replacement_reused_but_second_checkout_rejected(db, monkeypatch):
    history(db, monkeypatch)
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda _: attempt(db).id, range(2)))
    assert ids[0] == ids[1]
    with pytest.raises(ValueError, match="already in progress"):
        attempt(db, new_attempt=True)


def test_two_workers_provision_replacement_once(db, monkeypatch):
    history(db, monkeypatch)
    row = attempt(db)
    event_id = pending(db, replacement_payment(row))
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(lambda _: apply(db, event_id), range(2))) == ["processed", "processed"]
    assert active_count(db) == 1
    with db() as s:
        assert s.scalar(select(func.count()).select_from(ProviderSubscription)) == 2
        sub = s.get(ProviderSubscription, s.get(CheckoutAttempt, row.id).subscription_id)
        assert len(m.active_seats(s, sub.membership_id)) == 1


def test_old_events_never_modify_replacement(db, monkeypatch):
    old, _, original = history(db, monkeypatch)
    row = attempt(db)
    deliver(db, monkeypatch, replacement_payment(row))
    # Original event replay is a no-op; new stale events remain old-correlated.
    assert deliver(db, monkeypatch, original)[0].status == "processed"
    for evt in (payment(external_event_id="old-stale", paid_through=None, payment_reference=None),
                payment(external_event_id="old-new-payment", payment_reference="late", paid_through=LATER + timedelta(days=700))):
        result, _ = deliver(db, monkeypatch, evt)
        assert result.error_code == "membership_ended" and result.processed_at is None
    with db() as s:
        current = s.scalar(select(Membership).where(Membership.status == "active"))
        assert m.utc(current.access_until) == LATER + timedelta(days=365)
        assert s.get(Membership, old).status == "ended"


def test_owner_seated_elsewhere_after_checkout_is_recoverable(db, monkeypatch):
    history(db, monkeypatch)
    row = attempt(db)
    other = grant(db)
    with db() as s:
        m.add_seat(s, other, 1, m.Actor("admin"))
        s.commit()
    result, _ = deliver(db, monkeypatch, replacement_payment(row))
    assert result.status == "failed" and result.processed_at is None
    assert active_count(db) == 1
    with db() as s:
        assert s.get(CheckoutAttempt, row.id).status == "recoverable"
        assert s.scalar(select(func.count()).select_from(ProviderSubscription)) == 1


def test_payment_arrives_after_authorization_blocks_provisioning(db, monkeypatch):
    history(db, monkeypatch)
    row = attempt(db)
    pending(db, payment(external_event_id="old-unresolved", payment_reference="old-money",
                        paid_through=LATER + timedelta(days=30)))
    result, _ = deliver(db, monkeypatch, replacement_payment(row))
    assert result.status == "failed" and result.processed_at is None
    assert active_count(db) == 0


def test_recovery_extension_serializes_with_checkout(db, monkeypatch):
    old, _, _ = history(db, monkeypatch)
    event_id = pending(db, payment(external_event_id="recover", payment_reference="recover",
                                   paid_through=LATER + timedelta(days=30)))
    held, release, submitted = Event(), Event(), Event()
    waiting = []
    def recovery():
        with db() as s:
            assert b.apply_verified_event(s, event_id).status == "processed"
            held.set()
            assert release.wait(10)
            s.commit()
    def checkout():
        with db() as s:
            if s.get_bind().dialect.name == "postgresql":
                waiting.append(s.scalar(text("SELECT pg_backend_pid()")))
            submitted.set()
            with pytest.raises(ValueError):
                b.authorize_checkout(s, m.Actor("student", 1), "stripe", "full_annual_49", config=CONFIG)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(recovery)
        assert held.wait(10)
        second = pool.submit(checkout)
        assert submitted.wait(10)
        try:
            if waiting:
                wait_for_pg_lock(db, waiting[0])
        finally:
            release.set()
        first.result(timeout=15)
        second.result(timeout=15)
    with db() as s:
        assert s.get(Membership, old).status == "active"
        assert m.utc(s.get(Membership, old).access_until) == LATER + timedelta(days=30)
        assert s.scalar(select(func.count()).select_from(CheckoutAttempt)) == 1


def test_recovery_and_replacement_provisioning_do_not_overlap(db, monkeypatch):
    history(db, monkeypatch)
    row = attempt(db)
    old_event = pending(db, payment(external_event_id="old-recovery", payment_reference="old-recovery",
                                    paid_through=LATER + timedelta(days=30)))
    new_event = pending(db, replacement_payment(row))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda event_id: apply(db, event_id), [old_event, new_event]))
    assert results == ["failed", "failed"]  # Retained for explicit resolution.
    assert active_count(db) == 0


def test_manual_membership_still_blocks_checkout(db):
    grant(db, kind="student")
    with pytest.raises(ValueError, match="existing membership"):
        attempt(db)


def test_newly_discovered_correlation_requires_fresh_lock_transaction(db, monkeypatch):
    row = attempt(db)
    event_id = pending(db, payment(row.reference))
    # Simulate correlation becoming visible only after the discovery snapshot.
    with monkeypatch.context() as patch:
        patch.setattr(b, "_lock_event_accounts", lambda *args: set())
        assert apply(db, event_id) == "failed"
    with db() as s:
        assert s.get(BillingProviderEvent, event_id).error_code == "subscription_correlation_retry"
        assert s.scalar(select(func.count()).select_from(Membership)) == 0
    assert apply(db, event_id) == "processed"
    assert active_count(db) == 1


def test_postgres_seat_management_and_payment_lock_order(db, monkeypatch):
    with db() as s:
        if s.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL row locks only")
    row = attempt(db)
    deliver(db, monkeypatch, payment(row.reference))
    event_id = pending(db, payment(row.reference, external_event_id="renew", payment_reference="renew",
        occurred_at=NOW + timedelta(seconds=1), paid_through=NOW + timedelta(days=730)))
    waiting, started = [], Event()
    def renewal():
        with db() as s:
            waiting.append(s.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            result = b.apply_verified_event(s, event_id).status
            s.commit()
            return result
    with db() as s, ThreadPoolExecutor(1) as pool:
        member_id = s.scalar(select(Membership.id))
        member = m.owned_membership(s, member_id, m.Actor("admin"), lock=True)
        future = pool.submit(renewal)
        assert started.wait(5)
        try:
            wait_for_pg_lock(db, waiting[0])
            # Payment owns the account lock while blocked on this membership.
            # Seat management must still acquire its student/FK locks and finish.
            m._add_seat(s, member, 2, m.Actor("admin"), NOW)
            s.commit()
        finally:
            s.rollback()
        assert future.result(timeout=10) == "processed"
