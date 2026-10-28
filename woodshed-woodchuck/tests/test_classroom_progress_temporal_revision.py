"""Scheduled source history must survive fallback, without a boundary-time job."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
import hashlib
import json
from queue import Queue
import time

import pytest
from sqlalchemy import func, select, text

from app import classroom, classroom_s2 as s2, classroom_reporting as boundary
from app import classroom_progress_authorization as service
from app.age_privacy import utc
from app.classroom_models import (
    ClassroomAuditEvent, ClassroomS2AuditEvent, ClassroomEntitlement,
    ClassroomProgressAuthorization as Authorization,
)
from app.models import Organization
from test_classroom_s1 import adult_pin_hash, classroom_db
from test_classroom_s2 import s2_db, actor, student, institutional
from test_classroom_s1_postgres import classroom_pg
from test_classroom_s2_postgres import s2_pg
from test_classroom_s3_postgres import s3_pg, setup_reporting
from test_classroom_progress_authorization import progress_db, read, withdraw
from test_classroom_progress_source_revision import reviewed_options
from test_classroom_director import web
from test_classroom_student_invitation_routes import invitation_web, student_client
from test_classroom_progress_routes import progress_web, form, url


TICK = timedelta(microseconds=1)


def submit(session, reviewed):
    return service.accept(session, student=student(session), **reviewed)


def schedule(factory, *, capacity=10, offset=0):
    now = factory.clock[0]
    start = now + timedelta(minutes=5, microseconds=123)
    end = now + timedelta(minutes=10, microseconds=456)
    zone = timezone(timedelta(minutes=offset))
    with factory() as session:
        institutional(session, status="ended")
        trial = s2.start_trial(session, actor=actor(session), program_id=1)
        package = institutional(session, class_limit=capacity,
            starts_at=start.astimezone(zone), ends_at=end.astimezone(zone))
        ids = trial.id, package.id
        session.commit()
    return start, end, ids


@pytest.mark.parametrize("capacity", [0, 10])
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("observe_interruption", [False, True])
def test_scheduled_cycle_never_restores_form_or_consent(progress_db, capacity, saved, observe_interruption):
    factory, refs = progress_db, progress_db.refs
    start, end, (trial_id, package_id) = schedule(factory, capacity=capacity)
    with factory() as session:
        reviewed = reviewed_options(session, refs)
        original = submit(session, reviewed) if saved else None
        evidence = (original.id, original.source_revision, utc(original.accepted_at)) if saved else None
        session.commit()
    if observe_interruption:
        factory.clock[0] = start + TICK
        with factory() as session:
            decision = s2.entitlement_decision(session, 1)
            assert decision.entitlement_id == package_id and decision.allowed == bool(capacity)
            with pytest.raises(classroom.ClassroomDenied):
                submit(session, reviewed)
            with pytest.raises(classroom.ClassroomDenied):
                read(session, refs)
    # This must also work when no application request observed the entire interval.
    factory.clock[0] = end + TICK
    with factory() as session:
        assert s2.entitlement_decision(session, 1).entitlement_id == trial_id
        with pytest.raises(classroom.ClassroomDenied):
            submit(session, reviewed)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        assert session.scalar(select(func.count()).select_from(Authorization)) == int(saved)
        if saved:
            retained = session.get(Authorization, evidence[0])
            assert (retained.id, retained.source_revision, utc(retained.accepted_at)) == evidence
            assert retained.withdrawn_at is None
        fresh = reviewed_options(session, refs)
        assert fresh["source_revision"] != reviewed["source_revision"]
        row = submit(session, fresh)
        assert read(session, refs).allowed
        assert utc(row.accepted_at) == factory.clock[0]
        if saved:
            assert row.id != evidence[0] and retained.withdrawal_reason == "replaced"
        withdraw(session, refs)
        for previous in (reviewed, fresh):
            with pytest.raises(classroom.ClassroomDenied):
                submit(session, previous)
        session.commit()


def test_http_reauthorization_stays_stale_after_unobserved_scheduled_loss(progress_web):
    factory = progress_web
    now = factory.clock[0]
    with factory() as session:
        assert s2.entitlement_decision(session, 1).source == "trial"
        institutional(session, starts_at=now + timedelta(minutes=5),
            ends_at=now + timedelta(minutes=10), class_limit=0)
        session.commit()
    client = student_client()
    original = form(client)
    assert client.post(url(factory), data=original, follow_redirects=False).status_code == 303
    assert client.post(url(factory, "withdraw"), data={"csrf": original["csrf"]},
                       follow_redirects=False).status_code == 303
    delayed = form(client)
    factory.clock[0] = now + timedelta(minutes=11)  # No request during the outage.
    assert client.post(url(factory), data=delayed, follow_redirects=False).status_code == 409
    with factory() as session:
        rows = list(session.scalars(select(Authorization)))
        assert len(rows) == 1 and rows[0].withdrawn_at is not None
    assert client.post(url(factory), data=form(client), follow_redirects=False).status_code == 303
    with factory() as session:
        rows = list(session.scalars(select(Authorization).order_by(Authorization.id)))
        assert len(rows) == 2 and rows[0].withdrawn_at is not None and rows[1].withdrawn_at is None


@pytest.mark.parametrize("offset", [-360, 345])
def test_exact_boundaries_and_utc_precision(progress_db, offset):
    factory, refs = progress_db, progress_db.refs
    start, end, (trial_id, package_id) = schedule(factory, offset=offset)
    with factory() as session:
        initial = reviewed_options(session, refs)
        package = session.get(ClassroomEntitlement, package_id)
        assert utc(package.starts_at) == start and utc(package.ends_at) == end
    previous = initial
    previous_id = trial_id
    previous_revision = initial["source_revision"]
    revisions = {previous_revision}
    for instant, selected_id in (
        (start - TICK, trial_id), (start, package_id), (start + TICK, package_id),
        (end - TICK, package_id), (end, trial_id), (end + TICK, trial_id),
    ):
        factory.clock[0] = instant
        with factory() as session:
            assert s2.entitlement_decision(session, 1).entitlement_id == selected_id
            current = reviewed_options(session, refs)
            if selected_id != previous_id:
                assert current["source_revision"] not in revisions
                with pytest.raises(classroom.ClassroomDenied):
                    submit(session, previous)
                revisions.add(current["source_revision"])
            else:
                assert current["source_revision"] == previous_revision
            assert submit(session, current).source_revision == current["source_revision"]
            assert read(session, refs).allowed
            session.commit()
            previous, previous_id, previous_revision = current, selected_id, current["source_revision"]
    assert len(revisions) == 3


def test_insufficient_capacity_exact_boundaries(progress_db):
    factory, refs = progress_db, progress_db.refs
    start, end, _ = schedule(factory, capacity=0)
    with factory() as session:
        reviewed = reviewed_options(session, refs)
        submit(session, reviewed)
        session.commit()
    for instant, eligible in ((start - TICK, True), (start, False),
                              (start + TICK, False), (end - TICK, False), (end, True), (end + TICK, True)):
        factory.clock[0] = instant
        with factory() as session:
            assert s2.entitlement_decision(session, 1).allowed == eligible
            choice = service.choice(session, student=student(session), program_id=1, class_id=refs.class_id)
            assert choice.eligible == eligible
            assert choice.authorized == (instant < start)
            if instant >= start:
                with pytest.raises(classroom.ClassroomDenied):
                    submit(session, reviewed)
    with factory() as session:
        assert submit(session, reviewed_options(session, refs)) and read(session, refs).allowed


def test_uninterrupted_and_cancelled_schedules_do_not_expire_forms(progress_db):
    factory, refs = progress_db, progress_db.refs
    start, end, _ = schedule(factory)
    with factory() as session:
        # Cancel before activation; the change itself requires a fresh form,
        # but crossing its now-ineffective dates must not invalidate that form.
        institutional(session, status="ended")
        reviewed = reviewed_options(session, refs)
        ident = submit(session, reviewed).id
        session.commit()
    for instant in (start - TICK, start, end, end + timedelta(days=1)):
        factory.clock[0] = instant
        with factory() as session:
            current = reviewed_options(session, refs)
            assert current["source_revision"] == reviewed["source_revision"]
            assert submit(session, reviewed).id == ident and read(session, refs).allowed


def add_retained_package(session, start, end):
    # Synthetic retained schedules exercise the existing descending-ID priority
    # for overlapping rows. The operator currently replaces active packages;
    # this fixture does not add a new scheduling/writing capability.
    row = ClassroomEntitlement(program_id=1, source="institutional", status="active",
        starts_at=start, ends_at=end, class_limit=10, teacher_limit=2,
        approved_by_admin="synthetic-retained-schedule", provenance="synthetic-schedule",
        created_at=start - timedelta(days=1), updated_at=start - timedelta(days=1))
    session.add(row)
    session.flush()
    return row.id


def test_overlaps_precedence_and_multiple_unobserved_transitions(progress_db):
    factory, refs = progress_db, progress_db.refs
    now = factory.clock[0]
    start, end, (trial_id, outer_id) = schedule(factory)
    with factory() as session:
        inner_id = add_retained_package(session, now + timedelta(minutes=6), now + timedelta(minutes=7))
        later_id = add_retained_package(session, now + timedelta(minutes=8), now + timedelta(minutes=9))
        session.commit()
    reviewed_forms = []
    for instant, selected_id in (
        (now, trial_id), (start, outer_id), (now + timedelta(minutes=6), inner_id),
        (now + timedelta(minutes=7), outer_id), (now + timedelta(minutes=8), later_id),
        (now + timedelta(minutes=9), outer_id), (end, trial_id),
    ):
        factory.clock[0] = instant
        with factory() as session:
            assert s2.entitlement_decision(session, 1).entitlement_id == selected_id
            current = reviewed_options(session, refs)
            assert current["source_revision"] not in {r["source_revision"] for r in reviewed_forms}
            for old in reviewed_forms:
                with pytest.raises(classroom.ClassroomDenied):
                    submit(session, old)
            reviewed_forms.append(current)
    with factory() as session:
        assert submit(session, reviewed_forms[-1]) and read(session, refs).allowed


def test_unobserved_overlapping_schedules_cannot_restore_trial_consent(progress_db):
    factory, refs = progress_db, progress_db.refs
    now = factory.clock[0]
    _, end, _ = schedule(factory)
    with factory() as session:
        add_retained_package(session, now + timedelta(minutes=6), now + timedelta(minutes=7))
        add_retained_package(session, now + timedelta(minutes=8), now + timedelta(minutes=9))
        session.commit()
    with factory() as session:
        reviewed = reviewed_options(session, refs)
        ident = submit(session, reviewed).id
        session.commit()
    factory.clock[0] = end  # Skip every intervening start/end without a read or write.
    with factory() as session:
        with pytest.raises(classroom.ClassroomDenied):
            submit(session, reviewed)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        assert session.get(Authorization, ident).withdrawn_at is None
        assert submit(session, reviewed_options(session, refs)).id != ident
        assert read(session, refs).allowed


def test_fully_shadowed_boundaries_do_not_invalidate(progress_db):
    factory, refs = progress_db, progress_db.refs
    now = factory.clock[0]
    _, _, _ = schedule(factory)
    with factory() as session:
        # Higher-ID package covers both endpoints of the earlier package.
        selected_id = add_retained_package(session, now, now + timedelta(minutes=20))
        session.commit()
    with factory() as session:
        reviewed = reviewed_options(session, refs)
        ident = submit(session, reviewed).id
        session.commit()
    for minutes in (4, 6, 11, 19):
        factory.clock[0] = now + timedelta(minutes=minutes)
        with factory() as session:
            assert s2.entitlement_decision(session, 1).entitlement_id == selected_id
            assert reviewed_options(session, refs)["source_revision"] == reviewed["source_revision"]
            assert submit(session, reviewed).id == ident and read(session, refs).allowed


def test_repeated_operator_schedules_keep_all_prior_forms_stale(progress_db):
    factory, refs = progress_db, progress_db.refs
    _, end, _ = schedule(factory, capacity=0)
    with factory() as session:
        old_forms = [reviewed_options(session, refs)]
    for _ in range(3):
        factory.clock[0] = end + TICK
        with factory() as session:
            for old in old_forms:
                with pytest.raises(classroom.ClassroomDenied):
                    submit(session, old)
            current = reviewed_options(session, refs)
            row = submit(session, current)
            assert read(session, refs).allowed
            session.commit()
        with factory() as session:
            start, end = factory.clock[0] + timedelta(minutes=1), factory.clock[0] + timedelta(minutes=2)
            institutional(session, starts_at=start, ends_at=end, class_limit=0)
            with pytest.raises(classroom.ClassroomDenied):
                read(session, refs)
            reviewed = reviewed_options(session, refs)
            assert reviewed["source_revision"] != current["source_revision"]
            old_forms.extend((current, reviewed))
            assert session.get(Authorization, row.id).withdrawn_at is None
            session.commit()


def test_other_program_schedules_do_not_invalidate(progress_db):
    factory, refs = progress_db, progress_db.refs
    with factory() as session:
        if session.get(Organization, 2) is None:
            session.add(Organization(id=2, name="Other Program", organization_type="music"))
            session.flush()
            proof = actor(session)
            classroom.provision_program(session, actor=proof, organization_id=2, owner=proof)
            session.commit()
    now = factory.clock[0]
    with factory() as session:
        reviewed = reviewed_options(session, refs)
        ident = submit(session, reviewed).id
        session.commit()
    with factory() as session:
        institutional(session, program_id=2, starts_at=now + timedelta(minutes=1),
            ends_at=now + timedelta(minutes=2), class_limit=0)
        session.commit()
    for minutes in (1, 3):
        factory.clock[0] = now + timedelta(minutes=minutes)
        with factory() as session:
            assert reviewed_options(session, refs)["source_revision"] == reviewed["source_revision"]
            assert submit(session, reviewed).id == ident and read(session, refs).allowed


def test_previous_revision_format_requires_fresh_acceptance(progress_db):
    factory, refs = progress_db, progress_db.refs
    with factory() as session:
        reviewed = reviewed_options(session, refs)
        row = submit(session, reviewed)
        context = boundary._context(session, 1, refs.class_id, 1, student=student(session))
        anchor, member, entitlement, rule, activation, _ = context
        # Frozen v1 snapshot for this minimal fixture: its last S1/S2 events are
        # teacher_assigned/student_joined. No future field is silently backfilled.
        old = ("classroom-progress-source-v1", 1, refs.class_id, 1,
            session.scalar(select(func.max(ClassroomAuditEvent.id)).where(ClassroomAuditEvent.program_id == 1)),
            session.scalar(select(func.max(ClassroomS2AuditEvent.id)).where(ClassroomS2AuditEvent.program_id == 1)),
            anchor.id, member.id, entitlement.id, utc(rule.declared_at).isoformat(),
            activation.isoformat(), service.NOTICE_DIGEST)
        legacy = hashlib.sha256(json.dumps(old, separators=(",", ":")).encode()).hexdigest()
        row.source_revision = legacy
        ident, timestamp = row.id, utc(row.accepted_at)
        session.commit()
    with factory() as session:
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        with pytest.raises(classroom.ClassroomDenied):
            submit(session, reviewed | {"source_revision": legacy})
        retained = session.get(Authorization, ident)
        assert retained.source_revision == legacy and utc(retained.accepted_at) == timestamp
        assert retained.withdrawn_at is None
        renewed = submit(session, reviewed_options(session, refs))
        assert renewed.id != ident and renewed.source_revision != legacy and read(session, refs).allowed
        assert retained.withdrawal_reason == "replaced" and retained.source_revision == legacy


@pytest.mark.parametrize("phase", ["activation", "expiration"])
@pytest.mark.parametrize("accept_first", [False, True])
def test_postgres_acceptance_wait_crosses_exact_scheduled_boundary(s3_pg, monkeypatch, phase, accept_first):
    monkeypatch.setenv("CLASSROOM_PROGRESS_AUTHORIZATION_ENABLED", "1")
    factory = s3_pg
    factory.refs = refs = setup_reporting(factory, granted=False)
    factory.clock = factory.reporting_clock
    start, end, _ = schedule(factory)
    instant = start if phase == "activation" else end
    factory.clock[0] = instant - TICK
    with factory() as session:
        reviewed = reviewed_options(session, refs)
        assert session.scalar(select(Authorization)) is None
    pids = Queue()

    def waiting_acceptance():
        with factory() as session:
            pids.put(session.scalar(text("SELECT pg_backend_pid()")))
            try:
                ident = submit(session, reviewed).id
                session.commit()
                return "accepted", ident
            except classroom.ClassroomDenied:
                session.rollback()
                return "denied", None

    with factory() as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder_pid = holder.scalar(text("SELECT pg_backend_pid()"))
        if accept_first:
            submit(holder, reviewed)  # Valid before boundary; deliberately not committed yet.
        else:
            classroom._lock_program(holder, 1)  # No authorization row exists.
        pending = pool.submit(waiting_acceptance)
        waiting_pid = pids.get(timeout=10)
        try:
            deadline = time.monotonic() + 8
            with factory.control_engine.connect() as observer:
                while time.monotonic() < deadline:
                    if holder_pid in observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting_pid}):
                        break
                    time.sleep(.01)
                else:
                    pytest.fail("Acceptance did not wait on the actual PostgreSQL Program lock")
            # Time changes only after PostgreSQL proves the acceptance is waiting.
            factory.clock[0] = instant
        finally:
            holder.commit()
        assert pending.result(timeout=15) == ("denied", None)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Authorization)) == int(accept_first)
        with pytest.raises(classroom.ClassroomDenied):
            read(session, refs)
        current = reviewed_options(session, refs)
        assert current["source_revision"] != reviewed["source_revision"]
        assert submit(session, current) and read(session, refs).allowed
