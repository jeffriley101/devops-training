"""C24 operator schema approval preserves immutable plans and reporting rows."""
from copy import deepcopy

from alembic import command
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app import classroom_models as cm
from app import season_team_activation, team_continuity_repair
from app.operator_classroom_s3_contract import C24
from tests.test_c22_operational_compatibility import (
    all_rows, alter_sqlite_definition, calendar, config, cutover, install_revision,
    migrated_db, set_clock,
)
from tests.test_persistent_team_cutover import NOW, activate, plan, stage
from tests.test_persistent_team_migration import disposable_sqlite_configuration


def install_c24(db):
    install_revision(db, "c23class001")
    command.upgrade(config(), "c24class001")
    with Session(db[1]) as session:
        membership = session.scalar(select(cm.ClassroomStudentMembership))
        member_period = session.scalar(select(cm.ClassroomMembershipPeriod))
        room = session.get(cm.ClassroomClass, membership.class_id)
        entitlement = session.scalar(select(cm.ClassroomEntitlement))
        period = cm.ClassroomReportingPeriod(program_id=room.program_id, class_id=room.id,
            membership_id=membership.id, membership_period_id=member_period.id,
            profile_id=membership.profile_id, authorizer_profile_id=membership.profile_id,
            account_declared_at=NOW, entitlement_id=entitlement.id, class_activation_at=NOW,
            scope_version="classroom-boundary-v1", notice_version="classroom-boundary-notice-v1", starts_at=NOW)
        session.add(period)
        session.flush()
        session.add(cm.ClassroomS3AuditEvent(program_id=room.program_id, class_id=room.id,
            profile_id=membership.profile_id, actor_profile_id=membership.profile_id, period_id=period.id,
            action="reporting_granted", scope_version=period.scope_version, notice_version=period.notice_version))
        session.commit()


@pytest.mark.parametrize("original", ["p21team001", "c22class001", "c23class001"])
@pytest.mark.parametrize("state", ["unstaged", "staged", "activated"])
def test_historical_artifacts_keep_hash_boundary_and_coverage_after_c24(migrated_db, original, state):
    db = migrated_db
    install_revision(db, original)
    approved = plan(db)
    immutable = deepcopy(approved)
    if state != "unstaged":
        stage(db, approved)
    receipt = activate(db, approved) if state == "activated" else None
    immutable_receipt = deepcopy(receipt)
    before = all_rows(db[1])
    command.upgrade(config(), "c24class001")
    after_upgrade = all_rows(db[1])
    for name in before:
        assert after_upgrade[name] == before[name], name
    if state == "unstaged":
        with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
            stage(db, approved)
        assert plan(db)["content"]["revision"] == "c24class001"
        assert all_rows(db[1]) == after_upgrade
    else:
        if state == "staged":
            receipt = activate(db, approved)
        after = all_rows(db[1])
        assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]
        assert all_rows(db[1]) == after
        assert after["persistent_team_control"][0]["staged_plan"] == approved
        assert after["persistent_team_control"][0]["staged_plan_sha256"] == approved["plan_sha256"]
        assert set(receipt["verification"]["after"]) == set(cutover.TABLES)
    assert approved == immutable
    if immutable_receipt is not None:
        assert receipt == immutable_receipt


def test_c24_plan_stage_activate_verify_and_calendar_preserve_reporting(migrated_db):
    db = migrated_db
    install_c24(db)
    before = all_rows(db[1])
    approved = plan(db)
    assert approved["content"]["revision"] == "c24class001"
    assert all_rows(db[1]) == before
    stage(db, approved)
    receipt = activate(db, approved)
    assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]
    for name in C24:
        assert all_rows(db[1])[name] == before[name]
    snapshot = all_rows(db[1])
    calendar.provision(db[0], seasons=["halloween-2026"])
    assert all_rows(db[1]) == snapshot
    calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
    after = all_rows(db[1])
    for name in before.keys() - {"contest_weeks", "teams", "team_memberships", "persistent_team_control", "team_week_membership_snapshots"}:
        assert after[name] == before[name], name
    assert calendar.provision(db[0], apply=True, seasons=["halloween-2026"])["status"] == "ALREADY_COMPLETE"
    assert all_rows(db[1]) == after


def test_c24_does_not_reapprove_historical_repair_or_reverse_artifacts(migrated_db):
    db = migrated_db
    install_c24(db)
    before = all_rows(db[1])
    with db[1].connect() as c:
        for guard in (calendar.schema_guard, cutover._schema_guard):
            assert guard(c) == "c24class001"
        with pytest.raises(team_continuity_repair.RepairError, match="require d17contest001"):
            team_continuity_repair.schema_guard(c)
    assert season_team_activation.activate(db[0])["reason_codes"] == ["seasonal_team_activation_retired"]
    for older in ("p21team001", "c22class001", "c23class001"):
        with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
            cutover._plan_schema_guard({"revision": "c24class001"}, older)
    assert all_rows(db[1]) == before


def assert_refuses(db):
    before = all_rows(db[1])
    for operation in (lambda: plan(db),
            lambda: calendar.provision(db[0], seasons=["halloween-2026"]),
            lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])):
        with pytest.raises(cutover.inventory.InventoryError, match="classroom_schema_missing_or_changed"):
            operation()
    assert all_rows(db[1]) == before


def test_false_c24_stamp_refuses_before_writes(migrated_db):
    db = migrated_db
    install_revision(db, "c23class001")
    with db[1].begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num='c24class001'"))
    assert_refuses(db)


@pytest.mark.parametrize("damage", ["table", "column", "type", "check", "foreign_key", "index", "predicate", "trigger", "default", "extra_trigger"])
def test_malformed_c24_structure_refuses_both_operators(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c24class001")
    table = "classroom_reporting_periods"
    with db[1].begin() as c:
        assert calendar.schema_guard(c) == "c24class001"
        assert cutover._schema_guard(c) == "c24class001"
        if damage == "table":
            c.execute(text("DROP TABLE classroom_s3_audit_events"))
        elif damage == "column":
            c.execute(text(f'ALTER TABLE {table} RENAME COLUMN source_audit_id TO unapproved_source'))
        elif damage == "type":
            if c.dialect.name == "postgresql":
                c.execute(text(f"ALTER TABLE {table} ALTER COLUMN id TYPE BIGINT"))
            else:
                alter_sqlite_definition(c, table, "membership_id INTEGER", "membership_id BIGINT")
        elif damage == "check":
            key, check = next(iter(C24[table]["checks"].items()))
            if c.dialect.name == "postgresql":
                c.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT "{key}"'))
                c.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT "{key}" CHECK (true)'))
            else:
                alter_sqlite_definition(c, table, check, "1=1")
        elif damage == "foreign_key":
            if c.dialect.name == "postgresql":
                fk = inspect(c).get_foreign_keys(table)[0]
                c.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT "{fk["name"]}"'))
            else:
                alter_sqlite_definition(c, table, "ON DELETE RESTRICT", "ON DELETE NO ACTION")
        elif damage in {"index", "predicate"}:
            c.execute(text("DROP INDEX uq_classroom_reporting_open"))
            if damage == "predicate":
                c.execute(text(f"CREATE UNIQUE INDEX uq_classroom_reporting_open ON {table} (membership_id) WHERE 1=1"))
        elif damage == "trigger":
            if c.dialect.name == "postgresql":
                c.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER classroom_reporting_no_overlap"))
            else:
                c.execute(text("DROP TRIGGER classroom_reporting_no_overlap_insert"))
        elif damage == "default":
            if c.dialect.name == "postgresql":
                c.execute(text(f"ALTER TABLE {table} ALTER COLUMN scope_version SET DEFAULT 'unapproved'"))
            else:
                alter_sqlite_definition(c, table, "scope_version VARCHAR(80) NOT NULL", "scope_version VARCHAR(80) DEFAULT 'unapproved' NOT NULL")
        elif c.dialect.name == "postgresql":
            c.execute(text("CREATE TRIGGER unapproved_reporting_trigger BEFORE INSERT ON classroom_s3_audit_events FOR EACH ROW EXECUTE FUNCTION classroom_reporting_no_overlap()"))
        else:
            c.execute(text("CREATE TRIGGER unapproved_reporting_trigger AFTER INSERT ON classroom_s3_audit_events BEGIN SELECT 1; END"))
    assert_refuses(db)


@pytest.mark.parametrize("operation", ["stage", "activate", "calendar"])
def test_unexpected_reporting_mutation_rolls_back_whole_operator(migrated_db, monkeypatch, operation):
    db = migrated_db
    install_c24(db)
    approved = plan(db)
    if operation == "activate":
        stage(db, approved)
    set_clock(monkeypatch, NOW)
    target, action = (("contest_weeks", "INSERT") if operation == "calendar" else ("persistent_team_control", "UPDATE"))
    mutation = "UPDATE classroom_reporting_periods SET scope_version='unapproved-change'"
    with db[1].begin() as c:
        if c.dialect.name == "postgresql":
            c.execute(text(f"CREATE FUNCTION forbidden_s3_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {mutation}; RETURN NEW; END; $$"))
            c.execute(text(f"CREATE TRIGGER forbidden_s3_change AFTER {action} ON {target} FOR EACH ROW EXECUTE FUNCTION forbidden_s3_change()"))
        else:
            c.execute(text(f"CREATE TRIGGER forbidden_s3_change AFTER {action} ON {target} BEGIN {mutation}; END"))
    before = all_rows(db[1])
    with pytest.raises(cutover.inventory.InventoryError, match="unexpected_classroom_row_change"):
        if operation == "stage":
            stage(db, approved)
        elif operation == "activate":
            activate(db, approved)
        else:
            calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
    assert all_rows(db[1]) == before


@pytest.mark.parametrize("migrated_db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("damage", ["unvalidated", "deferrable", "function", "trigger_columns", "trigger_when", "fk_disabled"])
def test_postgres_c24_enforcement_contract(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c24class001")
    with db[1].begin() as c:
        if damage == "unvalidated":
            c.execute(text("ALTER TABLE classroom_reporting_periods DROP CONSTRAINT ck_classroom_reporting_authorizer"))
            c.execute(text("ALTER TABLE classroom_reporting_periods ADD CONSTRAINT ck_classroom_reporting_authorizer CHECK (authorizer_profile_id = profile_id) NOT VALID"))
        elif damage == "deferrable":
            fk = inspect(c).get_foreign_keys("classroom_reporting_periods")[0]
            c.execute(text(f'ALTER TABLE classroom_reporting_periods ALTER CONSTRAINT "{fk["name"]}" DEFERRABLE INITIALLY DEFERRED'))
        elif damage == "function":
            c.execute(text("ALTER FUNCTION classroom_reporting_no_overlap() SECURITY DEFINER"))
        elif damage == "fk_disabled":
            c.execute(text("ALTER TABLE classroom_s3_audit_events DISABLE TRIGGER ALL"))
        else:
            c.execute(text("DROP TRIGGER classroom_reporting_no_overlap ON classroom_reporting_periods"))
            event = "INSERT OR UPDATE OF starts_at" if damage == "trigger_columns" else "INSERT OR UPDATE"
            when = " WHEN (NEW.ended_at IS NOT NULL)" if damage == "trigger_when" else ""
            c.execute(text("CREATE TRIGGER classroom_reporting_no_overlap BEFORE " + event +
                " ON classroom_reporting_periods FOR EACH ROW" + when + " EXECUTE FUNCTION classroom_reporting_no_overlap()"))
    assert_refuses(db)
