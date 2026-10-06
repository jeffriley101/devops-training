"""Real PostgreSQL waits, authenticated actors, and transactional S1 evidence.

Opt in only to a disposable loopback classroom_s1_* database. Each test owns a
random schema. The observer verifies pg_blocking_pids before releasing a writer;
thread scheduling or SQLite cannot stand in for this locking evidence.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from queue import Queue
from threading import Barrier, Event
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import classroom as service
from app.classroom_models import (
    ClassroomAuditEvent, ClassroomClass, ClassroomOwnershipTransfer,
    ClassroomProgram, ClassroomRoleGrant,
)
from app.db import Base
from app.models import Organization, StudentVerifierConnection, TrustedVerifier, WoodchuckProfile
from app.security import hash_pin


@pytest.fixture
def classroom_pg(monkeypatch):
    raw = os.getenv("WW_CLASSROOM_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("Explicit disposable loopback Classroom PostgreSQL URL required")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"127.0.0.1", "::1"}
    assert (url.database or "").startswith("classroom_s1_")
    assert not set(url.query) & {"host", "hostaddr", "service", "options"}
    schema = "classroom_s1_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url.update_query_dict({
        "options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=15000",
    }))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("CLASSROOM_S1_ENABLED", "1")
    monkeypatch.setenv("CLASSROOM_S1_LOCAL_PROVISIONING", "1")
    monkeypatch.setenv("CLASSROOM_S1_PROVISIONER_IDS", "1,4")
    credential_hash = hash_pin("2468")
    with factory() as session:
        session.add_all(TrustedVerifier(email=f"adult-{n}@example.test",
            display_name=f"Synthetic Adult {n}", pin_hash=credential_hash) for n in range(1, 5))
        session.add(Organization(id=1, name="Synthetic Program", organization_type="music"))
        session.commit()
        actor = authenticate(session, 1)
        service.provision_program(session, actor=actor, organization_id=1, owner=actor)
        session.commit()
    factory.control_engine = control
    yield factory
    engine.dispose()
    with control.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    control.dispose()


def authenticate(session, verifier_id):
    return service.authenticate_adult(session, email=f"adult-{verifier_id}@example.test", pin="2468")


def ordered_wait(factory, first, second, *, rollback_first=False):
    """Hold the first completed mutation until PostgreSQL proves the next waits."""
    ready, release = Event(), Event()
    pids = Queue()

    def worker(label, function):
        with factory() as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            pids.put((label, pid))
            try:
                result = function(session)
                if label == "first":
                    ready.set()
                    assert release.wait(10), "observer did not release the first transaction"
                if label == "first" and rollback_first:
                    session.rollback()
                else:
                    session.commit()
                return "ok", result
            except service.ClassroomDenied as error:
                session.rollback()
                return "denied", str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(worker, "first", first)
        assert ready.wait(10), "first mutation did not reach its held transaction"
        b = pool.submit(worker, "second", second)
        observed = dict(pids.get(timeout=10) for _ in range(2))
        try:
            deadline = time.monotonic() + 8
            with factory.control_engine.connect() as observer:
                while time.monotonic() < deadline:
                    blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"),
                                               {"pid": observed["second"]})
                    if observed["first"] in blockers:
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("No actual PostgreSQL lock wait observed")
        finally:
            release.set()
        return a.result(timeout=15), b.result(timeout=15)


def propose(session, recipient):
    return service.propose_transfer(session, actor=authenticate(session, 1),
                                    program_id=1, recipient_id=recipient).id


def proposal(factory, recipient=2):
    with factory() as session:
        transfer_id = propose(session, recipient)
        session.commit()
        return transfer_id


def accept(session, transfer_id):
    return service.accept_transfer(session, actor=authenticate(session, 2),
                                   program_id=1, transfer_id=transfer_id).id


def successful_transfers(session):
    return list(session.scalars(select(ClassroomAuditEvent).where(
        ClassroomAuditEvent.action == "ownership_transferred")))


@pytest.mark.parametrize("supersede_first", [False, True])
def test_acceptance_and_new_proposal_recheck_after_observed_wait(classroom_pg, supersede_first):
    transfer_id = proposal(classroom_pg)
    accept_original = lambda session: accept(session, transfer_id)
    new_proposal = lambda session: propose(session, 3)
    first, second = (new_proposal, accept_original) if supersede_first else (accept_original, new_proposal)
    results = ordered_wait(classroom_pg, first, second)
    assert [result[0] for result in results] == ["ok", "denied"]
    with classroom_pg() as session:
        program = session.get(ClassroomProgram, 1)
        assert session.scalar(select(func.count()).select_from(ClassroomProgram)) == 1
        original = session.get(ClassroomOwnershipTransfer, transfer_id)
        successes = successful_transfers(session)
        if supersede_first:
            assert (program.owner_verifier_id, program.owner_version) == (1, 1)
            assert original.status == "superseded" and successes == []
            pending = session.scalar(select(ClassroomOwnershipTransfer).where(
                ClassroomOwnershipTransfer.status == "pending"))
            assert pending.recipient_verifier_id == 3
        else:
            assert (program.owner_verifier_id, program.owner_version) == (2, 2)
            assert original.status == "accepted" and len(successes) == 1
            assert (successes[0].actor_verifier_id, successes[0].old_owner_id,
                    successes[0].new_owner_id, successes[0].transfer_id) == (2, 1, 2, transfer_id)


@pytest.mark.parametrize("rollback_first", [False, True])
def test_duplicate_acceptance_and_rollback_keep_one_owner_and_audit(classroom_pg, rollback_first):
    transfer_id = proposal(classroom_pg)
    results = ordered_wait(classroom_pg, lambda s: accept(s, transfer_id),
                           lambda s: accept(s, transfer_id), rollback_first=rollback_first)
    assert results[0][0] == "ok"
    assert results[1][0] == ("ok" if rollback_first else "denied")
    with classroom_pg() as session:
        program = session.get(ClassroomProgram, 1)
        assert (program.owner_verifier_id, program.owner_version) == (2, 2)
        assert session.get(ClassroomOwnershipTransfer, transfer_id).status == "accepted"
        assert len(successful_transfers(session)) == 1


def test_revoked_admin_cannot_use_preloaded_authority_after_wait(classroom_pg):
    with classroom_pg() as session:
        grant = service.grant_role(session, actor=authenticate(session, 1), program_id=1,
                                  verifier_id=2, role="admin")
        grant_id = grant.id
        session.commit()

    def revoke(session):
        return service.revoke_role(session, actor=authenticate(session, 1),
                                   program_id=1, grant_id=grant_id).id

    def stale_create(session):
        actor = authenticate(session, 2)
        # The competing uncommitted revocation is invisible to this read.
        cached = session.get(ClassroomRoleGrant, grant_id)
        assert cached.ended_at is None
        assert "admin" in service._roles(session, 1, 2)
        return service.create_class(session, actor=actor, program_id=1,
                                    display_name="Must not exist").id

    assert [result[0] for result in ordered_wait(classroom_pg, revoke, stale_create)] == ["ok", "denied"]
    with classroom_pg() as session:
        assert session.get(ClassroomRoleGrant, grant_id).ended_at is not None
        assert session.scalar(select(func.count()).select_from(ClassroomClass)) == 0
        assert session.scalar(select(func.count()).select_from(ClassroomAuditEvent).where(
            ClassroomAuditEvent.action == "class_created")) == 0


def test_concurrent_onboarding_unique_email_cannot_claim_identity(classroom_pg, monkeypatch):
    barrier = Barrier(2)
    original = service.hash_pin

    def synchronized_hash(pin):
        # Both have passed the initial email lookup before either can insert.
        value = original(pin)
        barrier.wait(timeout=10)
        return value

    monkeypatch.setattr(service, "hash_pin", synchronized_hash)

    def worker(operator_id, pin):
        with classroom_pg() as session:
            actor = authenticate(session, operator_id)
            try:
                adult_id = service.onboard_adult(session, actor=actor,
                    email="  New-Adult@EXAMPLE.TEST ", display_name="Synthetic New Adult", pin=pin)
                session.commit()
                return "created", adult_id, pin
            except service.ClassroomDenied:
                session.rollback()
                return "conflict", None, pin

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in
                   [pool.submit(worker, 1, "1357"), pool.submit(worker, 4, "9753")]]
    assert sorted(result[0] for result in results) == ["conflict", "created"]
    winner = next(result for result in results if result[0] == "created")
    loser = next(result for result in results if result[0] == "conflict")
    with classroom_pg() as session:
        assert session.scalar(select(func.count()).select_from(TrustedVerifier)) == 5
        authenticated = service.authenticate_adult(session, email="new-adult@example.test", pin=winner[2])
        assert authenticated._verifier_id == winner[1]
        with pytest.raises(service.ClassroomDenied):
            service.authenticate_adult(session, email="new-adult@example.test", pin=loser[2])
        assert session.scalar(select(func.count()).select_from(StudentVerifierConnection)) == 0
        assert session.scalar(select(func.count()).select_from(WoodchuckProfile)) == 0
        assert session.scalar(select(func.count()).select_from(ClassroomProgram)) == 1
        assert session.scalar(select(func.count()).select_from(ClassroomRoleGrant)) == 1


def test_audit_failure_cannot_leave_committable_transfer(classroom_pg, monkeypatch):
    transfer_id = proposal(classroom_pg)
    original = service._audit

    def fail_success(session, program_id, actor_id, action, **references):
        original(session, program_id, actor_id, action, **references)
        if action == "ownership_transferred":
            session.flush()
            raise RuntimeError("synthetic audit boundary failure")

    monkeypatch.setattr(service, "_audit", fail_success)
    with classroom_pg() as session:
        with pytest.raises(RuntimeError, match="synthetic audit boundary failure"):
            accept(session, transfer_id)
        session.commit()  # An accidental outer commit must still preserve the old authority.
    with classroom_pg() as session:
        program = session.get(ClassroomProgram, 1)
        assert (program.owner_verifier_id, program.owner_version) == (1, 1)
        assert session.get(ClassroomOwnershipTransfer, transfer_id).status == "pending"
        assert successful_transfers(session) == []


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_snapshot_isolation_cannot_reuse_prelock_role_snapshot(classroom_pg, isolation):
    # A Program-row lock alone cannot refresh a previously captured snapshot of
    # another role row. S1 therefore admits only READ COMMITTED writers.
    with classroom_pg.kw["bind"].connect().execution_options(isolation_level=isolation) as connection:
        with classroom_pg(bind=connection) as session:
            actor = authenticate(session, 1)
            with pytest.raises(service.ClassroomDenied, match="READ COMMITTED"):
                service.create_class(session, actor=actor, program_id=1, display_name="Forbidden snapshot")
            session.commit()
    with classroom_pg() as session:
        assert session.scalar(select(func.count()).select_from(ClassroomClass)) == 0
        assert session.scalar(select(func.count()).select_from(ClassroomAuditEvent).where(
            ClassroomAuditEvent.action == "class_created")) == 0
