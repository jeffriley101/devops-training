"""Real c23→c24 DDL preserves history; reporting evidence cannot be erased."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from queue import Queue
from threading import Event
import time

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app import classroom_models as cm
from app.db import Base
from tests.test_classroom_s1 import adult_pin_hash
from tests.test_classroom_s2_migration import NOW, add_period, config, migrated_c22, trial
from tests.test_persistent_team_migration import disposable_sqlite_configuration

TABLES = {"classroom_reporting_periods", "classroom_s3_audit_events"}


def rows(engine):
    with engine.connect() as c:
        metadata = MetaData()
        return {name: list(c.execute(select(Table(name, metadata, autoload_with=c, resolve_fks=False))))
                for name in inspect(c).get_table_names() if name != "alembic_version"}


@pytest.fixture
def migrated_c23(migrated_c22):
    engine = migrated_c22
    command.upgrade(config(), "c23class001")
    add_period(engine)
    with Session(engine) as session:
        session.add(trial())
        session.commit()
    return engine


def reporting(**changes):
    values = dict(program_id=1, class_id=1, membership_id=1, membership_period_id=1,
        profile_id=1, authorizer_profile_id=1, account_declared_at=NOW,
        entitlement_id=1, class_activation_at=NOW, scope_version="classroom-boundary-v1",
        notice_version="classroom-boundary-notice-v1", starts_at=NOW)
    values.update(changes)
    return cm.ClassroomReportingPeriod(**values)


def add_reporting(engine, **changes):
    with Session(engine) as session:
        row = reporting(**changes)
        session.add(row)
        session.commit()
        return row.id


def test_populated_c23_preserved_empty_reporting_and_round_trip(migrated_c23):
    engine = migrated_c23
    before = rows(engine)
    command.upgrade(config(), "c24class001")
    after = rows(engine)
    assert {k: v for k, v in after.items() if k not in TABLES} == before
    assert all(after[name] == [] for name in TABLES)
    with engine.connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "c24class001"
    command.downgrade(config(), "c23class001")
    assert rows(engine) == before
    command.upgrade(config(), "c24class001")
    assert rows(engine) == after


@pytest.mark.parametrize("audit", [False, True])
def test_used_reporting_downgrade_refuses_without_mutation(migrated_c23, audit):
    engine = migrated_c23
    command.upgrade(config(), "c24class001")
    period_id = add_reporting(engine)
    if audit:
        with Session(engine) as session:
            session.add(cm.ClassroomS3AuditEvent(program_id=1, class_id=1, profile_id=1,
                period_id=period_id, actor_profile_id=1, action="reporting_granted",
                scope_version="classroom-boundary-v1", notice_version="classroom-boundary-notice-v1"))
            session.commit()
    before = rows(engine)
    with pytest.raises(RuntimeError, match="classroom_s3_reporting_history_in_use"):
        command.downgrade(config(), "c23class001")
    assert rows(engine) == before
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "c24class001"


def test_reporting_overlap_and_end_evidence_enforced(migrated_c23):
    engine = migrated_c23
    command.upgrade(config(), "c24class001")
    add_reporting(engine, ended_at=NOW + timedelta(days=2), ended_by_profile_id=1, ended_reason="withdrawn")
    with pytest.raises(IntegrityError, match="classroom_reporting_period_overlap"):
        add_reporting(engine, starts_at=NOW + timedelta(days=1), ended_at=NOW + timedelta(days=3),
                      ended_by_profile_id=1, ended_reason="withdrawn")
    open_id = add_reporting(engine, starts_at=NOW + timedelta(days=2))
    with pytest.raises(IntegrityError):
        add_reporting(engine, starts_at=NOW + timedelta(days=3))
    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            row = session.get(cm.ClassroomReportingPeriod, open_id)
            row.ended_at = NOW + timedelta(days=3)
            session.commit()
    assert len(rows(engine)["classroom_reporting_periods"]) == 2


def test_reporting_scope_and_actor_constraints(migrated_c23):
    engine = migrated_c23
    command.upgrade(config(), "c24class001")
    with pytest.raises(IntegrityError):
        add_reporting(engine, program_id=2)
    period_id = add_reporting(engine)
    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(cm.ClassroomS3AuditEvent(program_id=2, class_id=3, profile_id=1,
                period_id=period_id, actor_profile_id=1, action="reporting_granted",
                scope_version="classroom-boundary-v1", notice_version="classroom-boundary-notice-v1"))
            session.commit()


def test_postgres_closed_reporting_overlap_observes_real_wait(migrated_c23):
    engine = migrated_c23
    command.upgrade(config(), "c24class001")
    if engine.dialect.name == "sqlite":
        add_reporting(engine, ended_at=NOW + timedelta(days=2), ended_by_profile_id=1, ended_reason="withdrawn")
        with pytest.raises(IntegrityError, match="classroom_reporting_period_overlap"):
            add_reporting(engine, starts_at=NOW + timedelta(days=1))
        return
    ready, release, pids = Event(), Event(), Queue()
    def insert(label):
        with Session(engine) as session:
            pids.put((label, session.scalar(text("SELECT pg_backend_pid()"))))
            try:
                session.add(reporting(starts_at=NOW + timedelta(days=label),
                    ended_at=NOW + timedelta(days=label + 2), ended_by_profile_id=1, ended_reason="withdrawn"))
                session.flush()
                if label == 0:
                    ready.set()
                    assert release.wait(10)
                session.commit()
                return "created"
            except IntegrityError as error:
                session.rollback()
                assert "classroom_reporting_period_overlap" in str(error)
                return "overlap_refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(insert, 0)
        assert ready.wait(10)
        second = pool.submit(insert, 1)
        observed = dict(pids.get(timeout=10) for _ in range(2))
        try:
            deadline = time.monotonic() + 8
            with engine.connect() as observer:
                while time.monotonic() < deadline:
                    if observed[0] in observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": observed[1]}):
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("No actual PostgreSQL reporting overlap-trigger lock wait observed")
        finally:
            release.set()
        assert (first.result(timeout=15), second.result(timeout=15)) == ("created", "overlap_refused")


def test_one_additive_reporting_head():
    script = ScriptDirectory.from_config(config())
    assert script.get_heads() == ["c24class001"]
    assert script.get_revision("c24class001").down_revision == "c23class001"


def test_reporting_services_use_migrated_period_and_audit_guards(migrated_c23, monkeypatch, adult_pin_hash):
    from app import classroom, classroom_reporting as service, classroom_s2 as s2
    from app.age_models import AccountPrivacy
    from app.models import TrustedVerifier, WoodchuckProfile

    engine = migrated_c23
    command.upgrade(config(), "c24class001")
    for flag in ("CLASSROOM_S1_ENABLED", "CLASSROOM_S2_ENABLED", "CLASSROOM_S3_ENABLED"):
        monkeypatch.setenv(flag, "1")
    clock = [datetime.now(timezone.utc) + timedelta(hours=1)]
    monkeypatch.setattr(service, "_now", lambda: clock[0])
    monkeypatch.setattr(s2, "_now", lambda: clock[0])
    with Session(engine) as session:
        session.get(TrustedVerifier, 1).pin_hash = adult_pin_hash
        session.get(WoodchuckProfile, 1).pin_hash = adult_pin_hash
        session.add(AccountPrivacy(profile_id=1, age_band="13to17",
            declared_at=clock[0], public_from=clock[0]))
        entitlement = session.get(cm.ClassroomEntitlement, 1)
        entitlement.starts_at = entitlement.created_at = clock[0]
        entitlement.ends_at = clock[0] + timedelta(days=14)
        session.add(cm.ClassroomClassState(class_id=1, program_id=1, state="active",
            enrollment_open=True, changed_by_verifier_id=1, created_at=clock[0]))
        session.commit()

        def authenticated():
            return s2.authenticate_student(session, woodchuck_id="WC-S2-MIGRATION", pin="2468")

        def read_boundary():
            return service.authorize_reporting(session,
                actor=classroom.authenticate_adult(session, email="adult@example.invalid", pin="2468"),
                program_id=1, class_id=1, profile_id=1)

        scope = dict(program_id=1, class_id=1, scope_version=service.SCOPE_VERSION,
                     notice_version=service.NOTICE_VERSION)
        first = service.grant_reporting(session, student=authenticated(), **scope)
        first_id = first.id
        session.commit()
        assert read_boundary().period_id == first_id
        with pytest.raises(classroom.ClassroomDenied):
            service.grant_reporting(session, student=authenticated(), **scope)
        # The installed guards must permit an empty closed interval followed
        # by an explicit new interval at the very same timestamp.
        service.withdraw_reporting(session, student=authenticated(), program_id=1, class_id=1)
        second = service.reauthorize_reporting(session, student=authenticated(), **scope)
        second_id = second.id
        assert second_id != first_id
        assert read_boundary().period_id == second_id
        session.commit()
        clock[0] += timedelta(days=1)
        service.withdraw_reporting(session, student=authenticated(), program_id=1, class_id=1)
        session.rollback()
        assert read_boundary().period_id == second_id
        assert list(session.scalars(select(cm.ClassroomS3AuditEvent.action).order_by(
            cm.ClassroomS3AuditEvent.id))) == ["reporting_granted", "reporting_withdrawn", "reporting_reauthorized"]


@pytest.mark.parametrize("migrated_c22", ["postgresql"], indirect=True)
@pytest.mark.parametrize("parent", ["program", "class", "membership"])
def test_raw_reporting_update_refuses_parent_contention_without_waiting(migrated_c23, parent):
    """BEFORE UPDATE already owns its tuple; NOWAIT prevents an inverse wait."""
    engine = migrated_c23
    command.upgrade(config(), "c24class001")
    period_id = add_reporting(engine)
    models = {"program": cm.ClassroomProgram, "class": cm.ClassroomClass,
              "membership": cm.ClassroomStudentMembership}
    def uncoordinated_update():
        with Session(engine) as updater:
            try:
                updater.execute(text("UPDATE classroom_reporting_periods SET ended_at=:ended, "
                    "ended_by_profile_id=1, ended_reason='withdrawn' WHERE id=:id"),
                    {"ended": NOW + timedelta(days=1), "id": period_id})
                updater.commit()
                return "unexpected_success"
            except OperationalError as error:
                updater.rollback()
                return error.orig.sqlstate
    with Session(engine) as reader:
        reader.scalar(select(models[parent]).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(uncoordinated_update)
            # A blocking parent lock would not finish until this transaction
            # released its parent. 55P03 confirms immediate NOWAIT refusal.
            assert result.result(timeout=5) == "55P03"
        period = reader.scalar(select(cm.ClassroomReportingPeriod).where(
            cm.ClassroomReportingPeriod.id == period_id).with_for_update())
        assert period.ended_at is None
        reader.rollback()
