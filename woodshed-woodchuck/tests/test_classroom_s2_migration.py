"""Actual SQLite/PostgreSQL c22 upgrades, inactive additions and safe rollback."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import os
from queue import Queue
from threading import Event
import time
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, MetaData, select, Table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models as m
from app.classroom_models import (
    ClassroomClassState, ClassroomEntitlement, ClassroomEntryCode,
    ClassroomMembershipHold, ClassroomS2AuditEvent,
)
from app.db import Base
from tests.test_classroom_s1_migration import config
from tests.test_persistent_team_migration import disposable_sqlite_configuration

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
S2_TABLES = {model.__tablename__ for model in (
    ClassroomClassState, ClassroomEntitlement, ClassroomEntryCode,
    ClassroomMembershipHold, ClassroomS2AuditEvent,
)}


def c23_metadata():
    """Compare the historical c23 schema independently of additive S3."""
    expected = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in {"classroom_reporting_periods", "classroom_s3_audit_events"}:
            table.to_metadata(expected)
    return expected


@pytest.fixture(params=["sqlite", "postgresql"])
def migrated_c22(request, tmp_path, monkeypatch):
    control = None
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'classroom-s2.db'}"
    else:
        raw = os.getenv("WW_CLASSROOM_TEST_POSTGRES_URL")
        if not raw:
            pytest.skip("Explicit disposable loopback Classroom PostgreSQL URL required")
        parsed = make_url(raw)
        assert parsed.get_backend_name() == "postgresql"
        assert parsed.host in {"127.0.0.1", "::1"}
        assert (parsed.database or "").startswith("classroom_s1_")
        assert not set(parsed.query) & {"host", "hostaddr", "service", "options"}
        schema = "classroom_s2_migration_" + uuid4().hex
        control = create_engine(parsed)
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = parsed.update_query_dict({
            "options": f"-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=15000",
        }).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    try:
        command.upgrade(config(), "c22class001")
        with Session(engine) as session:
            session.add(m.TrustedVerifier(id=1, email="adult@example.invalid", display_name="Synthetic Adult",
                                          pin_hash="synthetic-not-a-real-hash"))
            session.flush()
            session.add_all(m.Organization(id=i, name=f"Synthetic Program {i}", organization_type="music",
                                           created_by_verifier_id=1) for i in (1, 2))
            session.add(m.WoodchuckProfile(id=1, woodchuck_id="WC-S2-MIGRATION", display_name="Synthetic Student",
                pin_hash="synthetic-not-a-real-hash", instrument="Trumpet", level="Beginner", goal="Practice"))
            session.flush()
            session.add_all(m.ClassroomProgram(organization_id=i, owner_verifier_id=1) for i in (1, 2))
            session.flush()
            session.add_all(m.ClassroomClass(id=i, program_id=1 if i < 3 else 2,
                                            display_name=f"Synthetic Class {i}") for i in (1, 2, 3))
            session.flush()
            session.add(m.ClassroomStudentMembership(id=1, class_id=1, profile_id=1))
            session.add(m.ClassroomRoleGrant(program_id=1, verifier_id=1, role="head_director",
                                             granted_by_verifier_id=1, starts_at=NOW))
            session.add(m.ClassroomTeachingAssignment(program_id=1, class_id=1, verifier_id=1,
                                                       granted_by_verifier_id=1, starts_at=NOW))
            session.add(m.ClassroomAuditEvent(program_id=1, class_id=1, actor_verifier_id=1,
                                              action="class_created", occurred_at=NOW))
            session.commit()
        yield engine
    finally:
        engine.dispose()
        if control is not None:
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            control.dispose()


def snapshot(engine):
    with engine.connect() as connection:
        metadata = MetaData()
        return {name: list(connection.execute(select(Table(name, metadata, autoload_with=connection,
                        resolve_fks=False))).mappings())
                for name in inspect(connection).get_table_names()
                if name not in S2_TABLES | {"alembic_version"}}


def add_period(engine, *, start=NOW, end=None):
    with Session(engine) as session:
        row = m.ClassroomMembershipPeriod(membership_id=1, starts_at=start, ended_at=end,
                                           ended_reason="departed" if end else None, state="active")
        session.add(row)
        session.commit()


def trial(**changes):
    values = dict(program_id=1, source="trial", status="active", starts_at=NOW,
                  ends_at=NOW + timedelta(days=14), class_limit=10, teacher_limit=2,
                  approved_by_verifier_id=1, provenance="ordinary_trial")
    values.update(changes)
    return ClassroomEntitlement(**values)


def entry(**changes):
    values = dict(program_id=1, class_id=1, generation=1, digest="a" * 64,
                  is_current=True, issued_by_verifier_id=1, issued_at=NOW)
    values.update(changes)
    return ClassroomEntryCode(**values)


def test_populated_c22_preserved_s2_empty_and_reversible_until_used(migrated_c22):
    engine = migrated_c22
    before = snapshot(engine)
    command.upgrade(config(), "c23class001")
    assert snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c23class001"
        for name in S2_TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {name}")) == 0
        assert compare_metadata(MigrationContext.configure(connection), c23_metadata()) == []
        assert connection.scalar(text("SELECT count(*) FROM classroom_membership_periods")) == 0
    command.downgrade(config(), "c22class001")
    assert snapshot(engine) == before
    command.upgrade(config(), "c23class001")
    assert snapshot(engine) == before


@pytest.mark.parametrize("used", ["entitlement", "class_state", "entry_code", "hold", "audit", "period"])
def test_downgrade_refuses_authority_and_history_without_mutation(migrated_c22, used):
    engine = migrated_c22
    command.upgrade(config(), "c23class001")
    with Session(engine) as session:
        rows = {
            "entitlement": trial(),
            "class_state": ClassroomClassState(class_id=1, program_id=1, state="active",
                enrollment_open=True, changed_by_verifier_id=1),
            "entry_code": entry(),
            "hold": ClassroomMembershipHold(membership_id=1, state="suspended", reason="conduct",
                held_by_verifier_id=1, held_at=NOW),
            "audit": ClassroomS2AuditEvent(program_id=1, actor_verifier_id=1, action="trial_started"),
            "period": m.ClassroomMembershipPeriod(membership_id=1, starts_at=NOW, state="active"),
        }
        session.add(rows[used])
        session.commit()
    before = snapshot(engine)
    with pytest.raises(RuntimeError, match="classroom_s2_authority_or_history_in_use"):
        command.downgrade(config(), "c22class001")
    assert snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c23class001"
        assert S2_TABLES <= set(inspect(connection).get_table_names())


def test_existing_valid_period_is_preserved_and_overlap_insert_update_rejected(migrated_c22):
    engine = migrated_c22
    add_period(engine, end=NOW + timedelta(days=1))
    before = snapshot(engine)
    command.upgrade(config(), "c23class001")
    assert snapshot(engine) == before
    with pytest.raises(IntegrityError, match="classroom_membership_period_overlap"):
        add_period(engine, start=NOW + timedelta(hours=12), end=NOW + timedelta(days=2))
    add_period(engine, start=NOW + timedelta(days=1), end=NOW + timedelta(days=2))
    with pytest.raises(IntegrityError, match="classroom_membership_period_overlap"):
        with Session(engine) as session:
            latest = session.scalar(select(m.ClassroomMembershipPeriod).order_by(m.ClassroomMembershipPeriod.id.desc()))
            latest.starts_at = NOW + timedelta(hours=12)
            session.commit()
    add_period(engine, start=NOW + timedelta(days=2))
    with pytest.raises(IntegrityError):
        add_period(engine, start=NOW + timedelta(days=3))
    with Session(engine) as session:
        assert len(session.scalars(select(m.ClassroomMembershipPeriod)).all()) == 3


def test_database_guards_serialize_closed_period_overlap(migrated_c22):
    """Closed overlapping periods cannot evade the existing open-period index."""
    engine = migrated_c22
    command.upgrade(config(), "c23class001")
    if engine.dialect.name == "sqlite":
        add_period(engine, end=NOW + timedelta(days=2))
        with pytest.raises(IntegrityError, match="classroom_membership_period_overlap"):
            add_period(engine, start=NOW + timedelta(days=1), end=NOW + timedelta(days=3))
        return
    ready, release = Event(), Event()
    pids = Queue()

    def insert(label):
        with Session(engine) as session:
            pids.put((label, session.scalar(text("SELECT pg_backend_pid()"))))
            try:
                session.add(m.ClassroomMembershipPeriod(membership_id=1,
                    starts_at=NOW + timedelta(days=0 if label == "first" else 1),
                    ended_at=NOW + timedelta(days=2 if label == "first" else 3),
                    ended_reason="departed", state="active"))
                session.flush()
                if label == "first":
                    ready.set()
                    assert release.wait(10)
                session.commit()
                return "created"
            except IntegrityError as error:
                session.rollback()
                assert "classroom_membership_period_overlap" in str(error)
                return "overlap_refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(insert, "first")
        assert ready.wait(10)
        second = pool.submit(insert, "second")
        observed = dict(pids.get(timeout=10) for _ in range(2))
        try:
            deadline = time.monotonic() + 8
            with engine.connect() as observer:
                while time.monotonic() < deadline:
                    blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"),
                                               {"pid": observed["second"]})
                    if observed["first"] in blockers:
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("No actual PostgreSQL overlap-trigger lock wait observed")
        finally:
            release.set()
        assert (first.result(timeout=15), second.result(timeout=15)) == ("created", "overlap_refused")
    with Session(engine) as session:
        assert len(session.scalars(select(m.ClassroomMembershipPeriod)).all()) == 1


def test_preexisting_overlap_refuses_upgrade_and_preserves_c22(migrated_c22):
    engine = migrated_c22
    add_period(engine, end=NOW + timedelta(days=2))
    add_period(engine, start=NOW + timedelta(days=1), end=NOW + timedelta(days=3))
    before = snapshot(engine)
    with pytest.raises(RuntimeError, match="classroom_existing_period_overlap"):
        command.upgrade(config(), "c23class001")
    assert snapshot(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c22class001"
        assert not S2_TABLES & set(inspect(connection).get_table_names())


def test_trial_and_code_database_uniqueness_and_cross_program_reuse(migrated_c22):
    engine = migrated_c22
    command.upgrade(config(), "c23class001")
    with Session(engine) as session:
        session.add(trial(status="ended", ended_at=NOW + timedelta(days=1)))
        session.add(entry())
        session.commit()
    for row in (trial(), entry(class_id=2), entry(generation=2, digest="b" * 64)):
        with pytest.raises(IntegrityError):
            with Session(engine) as session:
                session.add(row)
                session.commit()
    with Session(engine) as session:
        session.add(entry(program_id=2, class_id=3))
        session.add(trial(program_id=2))
        session.commit()
    with Session(engine) as session:
        old = session.scalar(select(ClassroomEntryCode).where(ClassroomEntryCode.class_id == 1))
        old.is_current = False
        old.revoked_at = NOW + timedelta(seconds=1)
        old.revoked_by_verifier_id = 1
        session.flush()
        session.add(entry(generation=2))
        session.commit()
        assert len(session.scalars(select(ClassroomEntryCode)).all()) == 3


def test_revision_graph_has_one_additive_s2_head():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == ["c24class001"]
    assert script.get_revision("c23class001").down_revision == "c22class001"
