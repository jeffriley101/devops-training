"""Guarded loopback PostgreSQL evidence for live Program acceptance.

Opt in only with a disposable classroom_s1_* database. Each test creates its
own schema; the race observer verifies an actual PostgreSQL lock wait.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from queue import Queue
from threading import Event
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import classroom, classroom_invitation
from app.classroom_invitation import create_program_invitation
from app.classroom_models import ClassroomAuditEvent, ClassroomProgram, ClassroomRoleGrant
from app.db import Base
from app.models import Organization, StudentVerifierConnection, TrustedVerifier
from app.security import hash_pin


@pytest.fixture(scope="module")
def live_pg_schema():
    raw = os.getenv("WW_CLASSROOM_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("Explicit disposable loopback Classroom PostgreSQL URL required")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"127.0.0.1", "::1"}
    assert (url.database or "").startswith("classroom_s1_")
    assert not set(url.query) & {"host", "hostaddr", "service", "options"}

    schema = "classroom_s1_live_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url.update_query_dict({
        "options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=15000",
    }))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    factory.control_engine = control
    yield factory
    engine.dispose()
    with control.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    control.dispose()


@pytest.fixture
def live_pg(live_pg_schema, monkeypatch):
    # DDL is expensive on small local disks; a dedicated schema is reused only
    # within this module and authority rows are cleared before each case.
    factory = live_pg_schema
    monkeypatch.setenv("SESSION_SECRET", "classroom-s1-live-local-test-secret-only")
    monkeypatch.delenv("CLASSROOM_S1_ENABLED", raising=False)
    with factory() as session:
        for model in (ClassroomAuditEvent, ClassroomRoleGrant, ClassroomProgram,
                      StudentVerifierConnection, TrustedVerifier, Organization):
            session.execute(delete(model))
        session.add(Organization(id=1, name="Synthetic Music Program", organization_type="music"))
        session.commit()
    return factory


def _count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def _observed_concurrent_acceptance(factory, token, *, rollback_first=False):
    ready, release = Event(), Event()
    pids = Queue()

    def worker(label):
        with factory() as session:
            pids.put((label, session.scalar(text("SELECT pg_backend_pid()"))))
            try:
                program = classroom.accept_program_invitation(
                    session, token=token, pin="2468", display_name="Invited Director",
                )
                if label == "first":
                    ready.set()
                    assert release.wait(10), "observer did not release the first transaction"
                if label == "first" and rollback_first:
                    session.rollback()
                    return "rolled_back", program.organization_id
                session.commit()
                return "ok", program.organization_id
            except classroom.ClassroomDenied as error:
                session.rollback()
                return "denied", str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, "first")
        assert ready.wait(10), "first acceptance did not reach its held transaction"
        second = pool.submit(worker, "second")
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
                    pytest.fail("No actual PostgreSQL Organization lock wait observed")
        finally:
            release.set()
        return first.result(timeout=15), second.result(timeout=15)


@pytest.mark.parametrize("rollback_first", [False, True])
def test_concurrent_new_adult_acceptance_has_one_canonical_owner(live_pg, rollback_first):
    token = create_program_invitation(1, "director@example.test")
    results = _observed_concurrent_acceptance(live_pg, token, rollback_first=rollback_first)
    assert [result[0] for result in results] == (
        ["rolled_back", "ok"] if rollback_first else ["ok", "denied"]
    )

    with live_pg() as session:
        adult = session.scalar(select(TrustedVerifier))
        program = session.get(ClassroomProgram, 1)
        grant = session.scalar(select(ClassroomRoleGrant))
        audits = list(session.scalars(select(ClassroomAuditEvent).order_by(ClassroomAuditEvent.id)))
        assert adult.email == "director@example.test"
        assert _count(session, TrustedVerifier) == 1
        assert _count(session, ClassroomProgram) == 1
        assert _count(session, ClassroomRoleGrant) == 1
        assert _count(session, StudentVerifierConnection) == 0
        assert program.owner_verifier_id == adult.id
        assert (grant.program_id, grant.verifier_id, grant.role,
                grant.granted_by_verifier_id) == (1, adult.id, "head_director", adult.id)
        assert [(row.action, row.actor_verifier_id, row.target_verifier_id)
                for row in audits] == [
                    ("program_provisioned", adult.id, adult.id),
                    ("role_granted", adult.id, adult.id),
                ]
        with pytest.raises(classroom.ClassroomDenied, match="already provisioned"):
            classroom.accept_program_invitation(session, token=token, pin="2468")


@pytest.mark.parametrize("failure", ["role", "audit"])
def test_failed_role_or_audit_rolls_back_new_adult_and_program(live_pg, monkeypatch, failure):
    token = create_program_invitation(1, "director@example.test")
    with live_pg() as session:
        if failure == "role":
            @event.listens_for(session, "before_flush")
            def fail_role(_session, _context, _instances):
                if any(isinstance(row, ClassroomRoleGrant) for row in _session.new):
                    raise RuntimeError("local role insert failure")
        else:
            original_audit = classroom._audit

            def fail_audit(session, program_id, actor_id, action, **references):
                original_audit(session, program_id, actor_id, action, **references)
                if action == "role_granted":
                    session.flush()
                    raise RuntimeError("local audit insert failure")

            monkeypatch.setattr(classroom, "_audit", fail_audit)
        with pytest.raises(RuntimeError, match=f"local {failure} insert failure"):
            classroom.accept_program_invitation(
                session, token=token, pin="2468", display_name="Invited Director",
            )
        session.commit()  # The caller cannot commit partial authority after failure.

    with live_pg() as session:
        for model in (TrustedVerifier, ClassroomProgram, ClassroomRoleGrant, ClassroomAuditEvent):
            assert _count(session, model) == 0


def test_existing_adult_requires_unchanged_existing_pin(live_pg):
    original_hash = hash_pin("1357")
    with live_pg() as session:
        session.add(TrustedVerifier(email="director@example.test", display_name="Existing Director",
                                    pin_hash=original_hash))
        session.commit()

    token = create_program_invitation(1, "director@example.test")
    with live_pg() as session:
        with pytest.raises(classroom.ClassroomDenied, match="credentials"):
            classroom.accept_program_invitation(
                session, token=token, pin="2468", display_name="Changed Identity",
            )
        session.commit()

    with live_pg() as session:
        assert _count(session, ClassroomProgram) == 0
        program = classroom.accept_program_invitation(
            session, token=token, pin="1357", display_name="Changed Identity",
        )
        session.commit()
        adult = session.scalar(select(TrustedVerifier))
        assert program.owner_verifier_id == adult.id
        assert (adult.display_name, adult.pin_hash) == ("Existing Director", original_hash)


def test_expiry_is_rechecked_after_organization_lock(live_pg, monkeypatch):
    issued = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    token = create_program_invitation(1, "director@example.test", now=issued)
    original_parse = classroom_invitation.parse_program_invitation
    moments = [issued + classroom_invitation.LIFETIME - timedelta(seconds=1),
               issued + classroom_invitation.LIFETIME]

    def advancing_server_time(value):
        return original_parse(value, now=moments.pop(0))

    monkeypatch.setattr(classroom_invitation, "parse_program_invitation", advancing_server_time)
    with live_pg() as session:
        with pytest.raises(classroom.ClassroomDenied, match="invitation unavailable"):
            classroom.accept_program_invitation(
                session, token=token, pin="2468", display_name="Invited Director",
            )
        session.commit()
    assert moments == []
    with live_pg() as session:
        assert _count(session, TrustedVerifier) == 0
        assert _count(session, ClassroomProgram) == 0


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_live_acceptance_requires_read_committed(live_pg, isolation):
    token = create_program_invitation(1, "director@example.test")
    with live_pg.kw["bind"].connect().execution_options(isolation_level=isolation) as connection:
        with live_pg(bind=connection) as session:
            with pytest.raises(classroom.ClassroomDenied, match="READ COMMITTED"):
                classroom.accept_program_invitation(
                    session, token=token, pin="2468", display_name="Invited Director",
                )
            session.commit()
    with live_pg() as session:
        assert _count(session, TrustedVerifier) == 0
        assert _count(session, ClassroomProgram) == 0
