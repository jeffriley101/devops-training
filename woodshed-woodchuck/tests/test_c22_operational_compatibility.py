"""Operational compatibility on real migrations and disposable synthetic data.

Positive fixtures always run Alembic to the installed revision. Stamp changes
below are exclusively negative fixtures. Classroom remains disabled throughout.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace

from alembic import command
import pytest
from sqlalchemy import MetaData, Table, event, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import contest_week_provisioning as calendar, models as m
from app import persistent_team_cutover as cutover, team_authority
from app.seasons import CANONICAL_SEASONS
from tests.test_classroom_s1_migration import (
    add_identity_and_personal_history, config,
)
from tests.test_persistent_team_cutover import (
    BOUNDARY, BOUNDARY_AT, MEMBERSHIP_IDS, NOW, TEAM_IDS, activate, plan, seed, stage,
)
from tests.test_persistent_team_migration import disposable_sqlite_configuration
from tests.test_team_families import disposable_url


REVISIONS = ("p21team001", "c22class001", "c23class001")
CLASSROOM_TABLES = {
    "classroom_programs", "classroom_role_grants", "classroom_classes",
    "classroom_teaching_assignments", "classroom_student_memberships",
    "classroom_membership_periods", "classroom_ownership_transfers",
    "classroom_audit_events",
}


def all_rows(engine):
    """Separate preservation evidence includes every installed application table."""
    with engine.connect() as connection:
        metadata = MetaData()
        # Batch reflection is fresh on every snapshot, including damaged DDL.
        # Per-table reflection repeatedly scans the growing PostgreSQL catalogs.
        metadata.reflect(bind=connection, resolve_fks=False,
                         only=lambda name, _metadata: name != "alembic_version")
        return {table.name: cutover.normalized([dict(row) for row in connection.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()])
                for table in metadata.tables.values()}


def populate_classroom(engine, identity=None):
    """Synthetic rows exercise all eight tables without enabling any public flow."""
    owner, organization, recipient = identity or add_identity_and_personal_history(engine)
    with Session(engine) as session:
        session.add(m.ClassroomProgram(organization_id=organization,
                                      owner_verifier_id=owner))
        session.flush()
        room = m.ClassroomClass(program_id=organization, display_name="Synthetic Band")
        role = m.ClassroomRoleGrant(program_id=organization, verifier_id=recipient,
                                   role="code_manager", granted_by_verifier_id=owner)
        transfer = m.ClassroomOwnershipTransfer(program_id=organization,
            initiator_verifier_id=owner, recipient_verifier_id=recipient, owner_version=1)
        session.add_all([room, role, transfer])
        session.flush()
        teaching = m.ClassroomTeachingAssignment(program_id=organization,
            class_id=room.id, verifier_id=recipient, granted_by_verifier_id=owner)
        membership = m.ClassroomStudentMembership(class_id=room.id, profile_id=24)
        session.add_all([teaching, membership])
        session.flush()
        session.add_all([
            m.ClassroomMembershipPeriod(membership_id=membership.id, starts_at=NOW,
                                        state="active"),
            m.ClassroomAuditEvent(program_id=organization, class_id=room.id,
                actor_verifier_id=owner, target_verifier_id=recipient,
                action="teacher_assigned", assignment_id=teaching.id),
        ])
        session.commit()
    evidence = all_rows(engine)
    assert {name for name in evidence if name.startswith("classroom_")} == CLASSROOM_TABLES
    assert all(evidence[name] for name in CLASSROOM_TABLES)


def canonical_seed_names(engine):
    # The reusable cutover seed intentionally uses keys as display names. This
    # fixture supplies canonical calendar names before taking any evidence.
    names = {definition.key: definition.name for definition in CANONICAL_SEASONS}
    with Session(engine) as session:
        for season in session.scalars(select(m.Season)):
            season.name = names[season.key]
        session.commit()


@pytest.fixture(params=["sqlite", "postgresql"])
def migrated_db(request, tmp_path, monkeypatch):
    """P21 starts with actual migration DDL, never current metadata create_all."""
    url = disposable_url(tmp_path, request.param)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("CLASSROOM_S1_ENABLED", raising=False)
    command.upgrade(config(), "p21team001")
    engine = seed(url, migrated=True)
    canonical_seed_names(engine)
    try:
        yield url, engine
    finally:
        engine.dispose()


def install_revision(db, revision):
    if revision in {"c22class001", "c23class001"}:
        command.upgrade(config(), "c22class001")
        populate_classroom(db[1])
        if revision == "c23class001":
            command.upgrade(config(), revision)
            populate_classroom_s2(db[1])
    with db[1].connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision



def populate_classroom_s2(engine):
    """All five S2 tables contain synthetic preservation evidence."""
    from app import classroom_models as cm
    from app.operator_classroom_s2_contract import C23
    with Session(engine) as session:
        program = session.scalar(select(m.ClassroomProgram))
        room = session.scalar(select(m.ClassroomClass))
        membership = session.scalar(select(m.ClassroomStudentMembership))
        entitlement = cm.ClassroomEntitlement(program_id=program.organization_id,
            source="institutional", status="active", starts_at=NOW,
            ends_at=NOW + timedelta(days=365), class_limit=10, teacher_limit=2,
            approved_by_admin="synthetic-local-admin", provenance="operator-preservation")
        code = cm.ClassroomEntryCode(program_id=program.organization_id, class_id=room.id,
            generation=1, digest="a" * 64, is_current=True,
            issued_by_verifier_id=program.owner_verifier_id)
        session.add_all([entitlement, code,
            cm.ClassroomClassState(program_id=program.organization_id, class_id=room.id,
                state="active", enrollment_open=True,
                changed_by_verifier_id=program.owner_verifier_id),
            cm.ClassroomMembershipHold(membership_id=membership.id, state="suspended",
                reason="conduct", held_at=NOW - timedelta(days=1), released_at=NOW,
                held_by_verifier_id=program.owner_verifier_id,
                released_by_verifier_id=program.owner_verifier_id)])
        session.flush()
        session.add(cm.ClassroomS2AuditEvent(program_id=program.organization_id,
            class_id=room.id, profile_id=membership.profile_id, membership_id=membership.id,
            actor_verifier_id=program.owner_verifier_id, action="student_reinstated",
            entitlement_id=entitlement.id, code_id=code.id))
        session.commit()
    rows = all_rows(engine)
    assert all(rows[name] for name in C23)


def set_clock(monkeypatch, at):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)
    monkeypatch.setattr(team_authority, "datetime", Clock)


@pytest.mark.parametrize("revision", REVISIONS)
@pytest.mark.parametrize("nonempty_roster", [False, True])
def test_migrated_plan_stage_activate_verify_preserves_full_data(
        migrated_db, monkeypatch, revision, nonempty_roster):
    db = migrated_db
    install_revision(db, revision)
    if nonempty_roster:
        with Session(db[1]) as session:
            session.get(m.ContestWeek, 2).season_id = 2
            session.commit()
    before = all_rows(db[1])
    raw = Path(db[1].url.database).read_bytes() if db[1].dialect.name == "sqlite" else None
    original_readonly = cutover.inventory.readonly_connection

    @contextmanager
    def enforced_readonly(url):
        with original_readonly(url) as connection:
            cutover.inventory.verify_readonly(connection)
            yield connection

    monkeypatch.setattr(cutover.inventory, "readonly_connection", enforced_readonly)
    approved = plan(db)
    assert approved["content"]["revision"] == revision
    if revision != "p21team001":
        assert approved["content"]["pta_contract_revision"] == "p21team001"
    assert cutover.digest(approved["content"]) == approved["plan_sha256"]
    assert all_rows(db[1]) == before
    if raw is not None:
        assert Path(db[1].url.database).read_bytes() == raw
    staged = stage(db, approved)
    assert staged["operation"] == "staged"
    after_stage = all_rows(db[1])
    for name in before:
        if name != "persistent_team_control":
            assert after_stage[name] == before[name], name
    with pytest.raises(cutover.CutoverError, match="activation_receipt"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], staged)
    receipt = activate(db, approved)
    after = all_rows(db[1])
    assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]
    assert all_rows(db[1]) == after
    allowed = {"teams", "team_memberships", "contest_weeks",
               "persistent_team_control", "team_week_membership_snapshots"}
    for name in before.keys() - allowed:
        assert after[name] == before[name], name
    assert {name for name in receipt["verification"]["after"]} == set(cutover.TABLES)
    assert not CLASSROOM_TABLES.intersection(receipt["verification"]["after"])
    assert len(after["teams"]) == len(before["teams"]) == 10
    assert len(after["team_memberships"]) == len(before["team_memberships"]) == 40
    assert not after["team_membership_transitions"]
    assert after["persistent_team_control"][0]["staged_plan"] == approved
    assert after["persistent_team_control"][0]["staged_plan_sha256"] == approved["plan_sha256"]
    frozen = [r for r in after["team_week_membership_snapshots"] if r["contest_week_id"] == 2]
    assert len(frozen) == (20 if nonempty_roster else 0)
    closing = next(w for w in after["contest_weeks"] if w["id"] == 2)
    assert closing["team_roster_frozen_at"] == cutover.normalized(BOUNDARY_AT)
    assert closing["team_membership_rules_version"] == cutover.LEGACY
    assert next(w for w in after["contest_weeks"] if w["id"] == 1) == before["contest_weeks"][0]
    assert next(w for w in after["contest_weeks"] if w["id"] == 3)["team_membership_rules_version"] == cutover.PERSISTENT
    with pytest.raises(cutover.CutoverError, match="disabled_staged_control"):
        activate(db, approved)
    assert all_rows(db[1]) == after
    with db[1].begin() as connection:
        connection.execute(text("UPDATE practice_charts SET note='later ordinary activity' WHERE id=1"))
    changed = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match="post_activation_state_changed"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)
    assert all_rows(db[1]) == changed


@pytest.mark.parametrize("revision", REVISIONS)
@pytest.mark.parametrize("active", [False, True])
def test_migrated_calendar_is_readonly_insert_only_and_retry_safe(
        migrated_db, monkeypatch, revision, active):
    db = migrated_db
    install_revision(db, revision)
    if active:
        approved = plan(db)
        stage(db, approved)
        activate(db, approved)
    set_clock(monkeypatch, BOUNDARY_AT if active else NOW)
    before = all_rows(db[1])
    arguments = {"seasons": ["halloween-2026"]}
    proposed = calendar.provision(db[0], **arguments)
    assert proposed["missing"] == 3
    assert proposed["created"] == 0
    assert any(w["stored_deadlines_differ"] for w in proposed["weeks"] if w["action"] == "unchanged")
    assert all_rows(db[1]) == before
    applied = calendar.provision(db[0], apply=True, **arguments)
    assert applied["created"] == 3
    after = all_rows(db[1])
    for name in before.keys() - {"contest_weeks"}:
        assert after[name] == before[name], name
    old_weeks = {w["id"]: w for w in before["contest_weeks"]}
    assert {w["id"]: w for w in after["contest_weeks"] if w["id"] in old_weeks} == old_weeks
    new_weeks = [w for w in after["contest_weeks"] if w["id"] not in old_weeks]
    assert {w["team_membership_rules_version"] for w in new_weeks} == {
        cutover.PERSISTENT if active else cutover.LEGACY}
    assert calendar.provision(db[0], apply=True, **arguments)["created"] == 0
    assert all_rows(db[1]) == after


@pytest.mark.parametrize("revision", REVISIONS)
def test_migrated_pta_cli_preserves_original_plan_and_receipt_files(
        migrated_db, monkeypatch, tmp_path, capsys, revision):
    db = migrated_db
    install_revision(db, revision)
    instant = {"at": NOW}
    original_clock = cutover._clock
    monkeypatch.setattr(cutover, "_clock", lambda now=None:
                        original_clock(instant["at"] if now is None else now))
    capsys.readouterr()
    before = all_rows(db[1])
    assert cutover.main([
        "plan", "--team-ids", *map(str, TEAM_IDS),
        "--membership-ids", *map(str, MEMBERSHIP_IDS),
        "--rules-from-week-start", BOUNDARY.isoformat(),
    ]) == 0
    plan_bytes = capsys.readouterr().out.encode()
    approved = json.loads(plan_bytes)
    approved_hash = approved["plan_sha256"]
    assert approved["content"]["revision"] == revision
    assert cutover.digest(approved["content"]) == approved_hash
    assert all_rows(db[1]) == before
    plan_file = tmp_path / "original-approved-plan.json"
    plan_file.write_bytes(plan_bytes)
    approval = ["--plan-file", str(plan_file), "--approved-sha256", approved_hash]
    acknowledgments = ["--" + ack.replace("_", "-") for ack in cutover.ACKS]
    assert cutover.main(["apply", *approval, *acknowledgments,
                         "--confirmation", cutover.CONFIRMATION]) == 0
    staged = json.loads(capsys.readouterr().out)
    assert staged["operation"] == "staged"
    assert staged["verification"]["approved_plan_sha256"] == approved_hash
    assert plan_file.read_bytes() == plan_bytes

    instant["at"] = BOUNDARY_AT
    assert cutover.main(["activate", *approval, *acknowledgments,
                         "--confirmation", cutover.ACTIVATION_CONFIRMATION]) == 0
    receipt_bytes = capsys.readouterr().out.encode()
    receipt = json.loads(receipt_bytes)
    assert receipt["operation"] == "activated"
    assert receipt["verification"]["approved_plan_sha256"] == approved_hash
    receipt_file = tmp_path / "original-activation-receipt.json"
    receipt_file.write_bytes(receipt_bytes)
    after = all_rows(db[1])
    assert cutover.main(["verify", *approval, "--receipt-file", str(receipt_file)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"]
    assert all_rows(db[1]) == after
    assert plan_file.read_bytes() == plan_bytes
    assert receipt_file.read_bytes() == receipt_bytes
    control = after["persistent_team_control"][0]
    assert control["staged_plan"] == approved
    assert control["staged_plan_sha256"] == approved_hash
    for name in CLASSROOM_TABLES.intersection(before):
        assert after[name] == before[name], name


def test_c22_due_boundary_denies_calendar_without_mutation(migrated_db, monkeypatch):
    from fastapi import HTTPException
    db = migrated_db
    install_revision(db, "c22class001")
    approved = plan(db)
    stage(db, approved)
    set_clock(monkeypatch, BOUNDARY_AT)
    before = all_rows(db[1])
    for apply in (False, True):
        with pytest.raises(HTTPException) as denied:
            calendar.provision(db[0], apply=apply, seasons=["halloween-2026"])
        assert denied.value.status_code == 503
        assert all_rows(db[1]) == before


@pytest.mark.parametrize("reason", ["hash", "target", "authority", "early", "expired", "activity", "ack", "confirmation"])
def test_c22_activation_refusals_do_not_write(migrated_db, reason):
    db = migrated_db
    install_revision(db, "c22class001")
    approved = plan(db)
    stage(db, approved)
    options = {}
    expected = {"hash": "hash", "target": "target_mismatch",
        "authority": "staged_authority_changed", "early": "boundary_not_reached",
        "expired": "staged_boundary_expired", "activity": "post_boundary_activity",
        "ack": "acknowledgments", "confirmation": "confirmation"}[reason]
    if reason == "hash":
        approved = deepcopy(approved)
        approved["content"]["team_ids"] = [10]
    elif reason == "target":
        approved = deepcopy(approved)
        approved["content"]["target"]["database"] += "_wrong"
        approved["plan_sha256"] = cutover.digest(approved["content"])
    elif reason == "authority":
        with db[1].begin() as connection:
            connection.execute(text("UPDATE teams SET moderation_status='hidden' WHERE id=10"))
    elif reason == "early":
        options["now"] = BOUNDARY_AT - timedelta(microseconds=1)
    elif reason == "expired":
        options["now"] = BOUNDARY_AT + timedelta(days=7)
    elif reason == "ack":
        options["acknowledgments"] = {}
    elif reason == "confirmation":
        options["confirmation"] = "yes"
    elif reason == "activity":
        with Session(db[1]) as session:
            session.add(m.CampPointAward(profile_id=24, activity_type="care",
                points_awarded=1, occurred_at=BOUNDARY_AT, duplicate_key="c22-conflict"))
            session.commit()
    before = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match=expected):
        activate(db, approved, **options)
    assert all_rows(db[1]) == before


@pytest.mark.parametrize("stamp", ["p20team001", "unknown", "multihead", "empty", "missing"])
def test_revision_evidence_must_be_exact_and_single(migrated_db, stamp):
    db = migrated_db
    with db[1].begin() as connection:
        if stamp == "missing":
            connection.execute(text("DROP TABLE alembic_version"))
        elif stamp == "empty":
            connection.execute(text("DELETE FROM alembic_version"))
        elif stamp == "multihead":
            connection.execute(text("INSERT INTO alembic_version VALUES ('c22class001')"))
        else:
            connection.execute(text("UPDATE alembic_version SET version_num=:stamp"), {"stamp": stamp})
    before = all_rows(db[1])
    for operator in (lambda: plan(db),
                     lambda: calendar.provision(db[0], seasons=["halloween-2026"]),
                     lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])):
        with pytest.raises(cutover.inventory.InventoryError,
                           match="revision_not_approved|calendar_schema_required"):
            operator()
    assert all_rows(db[1]) == before


def test_false_c22_stamp_without_extension_refuses_both_operators(migrated_db):
    db = migrated_db
    with db[1].begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='c22class001'"))
    before = all_rows(db[1])
    for operator in (lambda: plan(db),
                     lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])):
        with pytest.raises(cutover.inventory.InventoryError, match="classroom_schema_missing_or_changed"):
            operator()
    assert all_rows(db[1]) == before


def alter_sqlite_definition(connection, table, original, replacement):
    """Damage a synthetic schema without changing rows or installing new DDL."""
    sql = connection.scalar(text("SELECT sql FROM sqlite_master WHERE name=:name"), {"name": table})
    assert original in sql
    connection.execute(text("PRAGMA writable_schema=ON"))
    connection.execute(text("UPDATE sqlite_master SET sql=:sql WHERE name=:name"),
                       {"sql": sql.replace(original, replacement), "name": table})
    connection.execute(text("PRAGMA writable_schema=OFF"))
    version = connection.scalar(text("PRAGMA schema_version"))
    connection.execute(text(f"PRAGMA schema_version={version + 1}"))


@pytest.mark.parametrize("damage", ["table", "column", "check", "foreign_key", "index", "predicate", "unique"])
def test_incomplete_c22_structure_refuses_both_operators(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c22class001")
    with db[1].begin() as connection:
        if damage == "table":
            connection.execute(text("DROP TABLE classroom_audit_events"))
        elif damage == "column":
            connection.execute(text("ALTER TABLE classroom_programs RENAME COLUMN owner_version TO broken_version"))
        elif damage == "check":
            if connection.dialect.name == "sqlite":
                alter_sqlite_definition(connection, "classroom_programs", "owner_version >= 1", "owner_version >= 0")
            else:
                connection.execute(text("ALTER TABLE classroom_programs DROP CONSTRAINT ck_classroom_program_owner_version"))
                connection.execute(text("ALTER TABLE classroom_programs ADD CONSTRAINT ck_classroom_program_owner_version CHECK (owner_version >= 0)"))
        elif damage == "foreign_key":
            if connection.dialect.name == "sqlite":
                alter_sqlite_definition(connection, "classroom_programs", "ON DELETE RESTRICT", "ON DELETE NO ACTION")
            else:
                foreign_key = next(f for f in inspect(connection).get_foreign_keys("classroom_programs")
                                   if f["constrained_columns"] == ["owner_verifier_id"])
                connection.execute(text(f'ALTER TABLE classroom_programs DROP CONSTRAINT "{foreign_key["name"]}"'))
                connection.execute(text("ALTER TABLE classroom_programs ADD FOREIGN KEY (owner_verifier_id) REFERENCES trusted_verifiers(id) ON DELETE NO ACTION"))
        elif damage in {"index", "predicate"}:
            connection.execute(text("DROP INDEX uq_classroom_role_open"))
            if damage == "predicate":
                connection.execute(text("CREATE UNIQUE INDEX uq_classroom_role_open ON classroom_role_grants(program_id, verifier_id, role) WHERE ended_at IS NOT NULL"))
        elif damage == "unique":
            if connection.dialect.name == "sqlite":
                alter_sqlite_definition(connection, "classroom_student_memberships",
                                        "UNIQUE (class_id, profile_id)", "UNIQUE (class_id, profile_id, id)")
            else:
                connection.execute(text("ALTER TABLE classroom_student_memberships DROP CONSTRAINT uq_classroom_membership_class_student"))
    before = all_rows(db[1])
    for operator in (lambda: plan(db),
                     lambda: calendar.provision(db[0], seasons=["halloween-2026"]),
                     lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])):
        with pytest.raises(cutover.inventory.InventoryError, match="classroom_schema_missing_or_changed"):
            operator()
    assert all_rows(db[1]) == before


@pytest.mark.parametrize("damage", ["interval_trigger", "authority_index", "staging_check", "calendar_check", "singleton"])
def test_existing_safety_guards_remain_required_on_c22(migrated_db, damage):
    db = migrated_db
    install_revision(db, "c22class001")
    with db[1].begin() as connection:
        if damage == "interval_trigger":
            connection.execute(text("DROP TRIGGER persistent_team_membership_interval_insert"
                if connection.dialect.name == "sqlite" else
                "ALTER TABLE team_memberships DISABLE TRIGGER persistent_team_membership_interval_guard"))
        elif damage == "authority_index":
            connection.execute(text("DROP INDEX uq_team_operating_family"))
        elif damage in {"staging_check", "calendar_check"}:
            table, name, fragment = (
                ("persistent_team_control", "ck_persistent_team_control_staging", "staged_for IS NULL")
                if damage == "staging_check" else
                ("contest_weeks", "ck_contest_week_team_membership_rules", "'persistent_v1'"))
            if connection.dialect.name == "sqlite":
                alter_sqlite_definition(connection, table, fragment,
                    "staged_for IS NOT NULL" if damage == "staging_check" else "'unapproved_v1'")
            else:
                connection.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {name}"))
        else:
            connection.execute(text("DELETE FROM persistent_team_control"))
    before = all_rows(db[1])
    operator = (lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])) if damage in {
        "calendar_check", "singleton"} else lambda: plan(db)
    with pytest.raises(cutover.inventory.InventoryError):
        operator()
    assert all_rows(db[1]) == before


def test_c22_unexpected_protected_write_rolls_back_every_table(migrated_db):
    from tests.test_persistent_team_cutover import test_unexpected_protected_write_rolls_back_entire_cutover
    install_revision(migrated_db, "c22class001")
    before = all_rows(migrated_db[1])
    test_unexpected_protected_write_rolls_back_entire_cutover(migrated_db)
    after = all_rows(migrated_db[1])
    for name in before.keys() - {"persistent_team_control"}:
        assert after[name] == before[name], name
    assert after["persistent_team_control"][0]["activated_at"] is None


@pytest.mark.parametrize("operation", ["stage", "activate", "calendar"])
def test_c22_injected_classroom_change_rolls_back_complete_operator_transaction(
        migrated_db, monkeypatch, operation):
    db = migrated_db
    install_revision(db, "c22class001")
    approved = plan(db)
    if operation == "activate":
        stage(db, approved)
    set_clock(monkeypatch, NOW)
    target, action = (("contest_weeks", "INSERT") if operation == "calendar"
                      else ("persistent_team_control", "UPDATE"))
    with db[1].begin() as connection:
        mutation = "UPDATE classroom_programs SET owner_version=owner_version+1"
        if db[1].dialect.name == "postgresql":
            connection.execute(text(f"CREATE FUNCTION test_forbidden_classroom_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {mutation}; RETURN NEW; END; $$"))
            connection.execute(text(f"CREATE TRIGGER test_forbidden_classroom_change AFTER {action} ON {target} FOR EACH ROW EXECUTE FUNCTION test_forbidden_classroom_change()"))
        else:
            connection.execute(text(f"CREATE TRIGGER test_forbidden_classroom_change AFTER {action} ON {target} BEGIN {mutation}; END"))
    before = all_rows(db[1])
    with pytest.raises(cutover.inventory.InventoryError, match="unexpected_classroom_row_change"):
        if operation == "stage":
            stage(db, approved)
        elif operation == "activate":
            activate(db, approved)
        else:
            calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
    assert all_rows(db[1]) == before


@pytest.fixture(params=["postgresql"])
def c22_boundary_db(request, tmp_path, monkeypatch):
    """Reuse the reviewed HTTP/race fixture with genuinely migrated c22 DDL."""
    from tests import test_persistent_team_boundary_runtime as runtime

    def migrated_seed(url):
        monkeypatch.setenv("DATABASE_URL", url)
        command.upgrade(config(), "c22class001")
        engine = seed(url, migrated=True)
        canonical_seed_names(engine)
        # The reused runtime fixture establishes adult age declarations itself.
        # Add only independent Classroom adults here, so its reviewed identity
        # setup does not conflict with the general fixture's under-13 example.
        with Session(engine) as session:
            owner = m.TrustedVerifier(email="race-owner@example.invalid",
                                     display_name="Synthetic Owner", pin_hash="synthetic")
            recipient = m.TrustedVerifier(email="race-recipient@example.invalid",
                display_name="Synthetic Recipient", pin_hash="synthetic")
            session.add_all([owner, recipient])
            session.flush()
            organization = m.Organization(name="Synthetic Race Program",
                organization_type="school", created_by_verifier_id=owner.id)
            session.add(organization)
            session.flush()
            identity = owner.id, organization.id, recipient.id
            session.commit()
        populate_classroom(engine, identity)
        return engine

    monkeypatch.delenv("CLASSROOM_S1_ENABLED", raising=False)
    monkeypatch.setattr(runtime, "seed", migrated_seed)
    fixture = runtime.boundary_db.__wrapped__(request, tmp_path, monkeypatch)
    db = next(fixture)
    try:
        yield db
    finally:
        with pytest.raises(StopIteration):
            next(fixture)


@pytest.mark.parametrize("damage", ["unvalidated_check", "deferrable_foreign_key"])
def test_c22_postgres_constraint_enforcement_is_required(c22_boundary_db, damage):
    db = (c22_boundary_db.url, c22_boundary_db.engine)
    with db[1].begin() as connection:
        if damage == "unvalidated_check":
            connection.execute(text("ALTER TABLE classroom_programs DROP CONSTRAINT ck_classroom_program_owner_version"))
            connection.execute(text("ALTER TABLE classroom_programs ADD CONSTRAINT ck_classroom_program_owner_version CHECK (owner_version >= 1) NOT VALID"))
        else:
            foreign_key = next(f for f in inspect(connection).get_foreign_keys("classroom_programs")
                               if f["constrained_columns"] == ["owner_verifier_id"])
            connection.execute(text(f'ALTER TABLE classroom_programs ALTER CONSTRAINT "{foreign_key["name"]}" DEFERRABLE INITIALLY DEFERRED'))
    before = all_rows(db[1])
    for operator in (lambda: plan(db),
                     lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])):
        with pytest.raises(cutover.inventory.InventoryError, match="classroom_schema_missing_or_changed"):
            operator()
    assert all_rows(db[1]) == before


@pytest.mark.parametrize("first", ["writer", "activation"])
@pytest.mark.parametrize("kind", ["private_chart", "private_verification", "public_verification"])
def test_c22_ordered_authority_races_observe_real_postgres_waits(
        c22_boundary_db, monkeypatch, first, kind):
    from tests.test_persistent_team_round2 import test_practice_writers_and_activation_serialize_in_both_orders
    before = all_rows(c22_boundary_db.engine)
    test_practice_writers_and_activation_serialize_in_both_orders(
        c22_boundary_db, monkeypatch, first, kind)
    after = all_rows(c22_boundary_db.engine)
    for name in CLASSROOM_TABLES:
        assert after[name] == before[name], name


@pytest.mark.parametrize("first", ["calendar", "activation"])
def test_c22_calendar_activation_contention_rechecks_after_observed_wait(
        c22_boundary_db, monkeypatch, first):
    from tests.test_persistent_team_round2 import wait_for_postgres_lock
    db = (c22_boundary_db.url, c22_boundary_db.engine)
    # Calendar is admitted before Monday; activation's explicit operator clock
    # reaches the approved Monday while waiting. Both use the real singleton.
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    stage(db, approved)
    before = all_rows(db[1])
    held, release, other_started = Event(), Event(), Event()
    actors, pids = {}, {}

    def observe(connection, cursor, sql, parameters, context, many):
        actor = actors.get(get_ident())
        if actor:
            pids[actor] = connection.connection.driver_connection.info.backend_pid
            if held.is_set() and actor != first:
                other_started.set()

    def hold(connection, cursor, sql, parameters, context, many):
        actor = actors.get(get_ident())
        if (actor == first and not held.is_set()
                and "persistent_team_control" in sql and "FOR UPDATE" in sql):
            held.set()
            assert release.wait(10), "First operator was not released"

    def run(actor):
        actors[get_ident()] = actor
        if actor == "calendar":
            return calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
        try:
            return activate(db, approved)
        except cutover.CutoverError as error:
            return str(error)

    event.listen(Engine, "before_cursor_execute", observe)
    event.listen(Engine, "after_cursor_execute", hold)
    other = "activation" if first == "calendar" else "calendar"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(run, first)
            assert held.wait(10)
            other_future = pool.submit(run, other)
            assert other_started.wait(5)
            wait_for_postgres_lock(
                SimpleNamespace(factory=sessionmaker(db[1])), pids[other])
            assert not other_future.done()
            release.set()
            results = {first: first_future.result(timeout=20),
                       other: other_future.result(timeout=20)}
    finally:
        release.set()
        event.remove(Engine, "before_cursor_execute", observe)
        event.remove(Engine, "after_cursor_execute", hold)
    assert results["calendar"]["created"] == 3
    after = all_rows(db[1])
    if first == "calendar":
        assert "staged_authority_changed" in results["activation"]
        assert after["persistent_team_control"] == before["persistent_team_control"]
        assert after["teams"] == before["teams"]
        assert after["team_memberships"] == before["team_memberships"]
    else:
        assert results["activation"]["transaction_state"] == "committed"
    for name in CLASSROOM_TABLES:
        assert after[name] == before[name], name
    new_weeks = [w for w in after["contest_weeks"] if w["id"] not in {1, 2, 3}]
    assert {w["team_membership_rules_version"] for w in new_weeks} == {
        cutover.LEGACY if first == "calendar" else cutover.PERSISTENT}


def replace_empty_sqlite_integer_declaration(connection, table, column, declaration):
    """Rebuild one unused migrated table, preserving its other DDL and indexes."""
    import re

    # C22 migration tables are empty in these probes. No writable_schema edits,
    # disabled FK enforcement, metadata-created replacement, or copied DDL.
    for name in CLASSROOM_TABLES:
        assert connection.scalar(text(f'SELECT COUNT(*) FROM "{name}"')) == 0
    original = connection.scalar(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=:table"),
        {"table": table})
    indexes = list(connection.scalars(text(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:table "
        "AND sql IS NOT NULL ORDER BY name"), {"table": table}))
    # This is a single targeted edit of Alembic's original CREATE TABLE text.
    pattern = rf"(?m)^(\s*{re.escape(column)}\s+)INTEGER\b"
    changed, count = re.subn(pattern, lambda match: match[1] + declaration, original)
    assert count == 1
    connection.execute(text(f'DROP TABLE "{table}"'))
    connection.execute(text(changed))
    for statement in indexes:
        connection.execute(text(statement))
    assert list(connection.scalars(text(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:table "
        "AND sql IS NOT NULL ORDER BY name"), {"table": table})) == indexes
    assert not connection.execute(text("PRAGMA foreign_key_check")).all()


def assert_c22_integer_damage_refused(db, table, *, check_public_operations=False):
    """Independent exact error and full-data oracle for both operator guards."""
    url, engine = db
    before = all_rows(engine)
    expected = f"classroom_schema_missing_or_changed: {table}: columns"
    with engine.connect() as connection:
        for guard in (calendar.schema_guard, cutover._schema_guard):
            with pytest.raises(cutover.inventory.InventoryError) as error:
                guard(connection)
            assert str(error.value) == expected
    assert all_rows(engine) == before
    if check_public_operations:
        for operation in (
            lambda: plan(db),
            lambda: calendar.provision(url, seasons=["halloween-2026"]),
            lambda: calendar.provision(url, apply=True, seasons=["halloween-2026"]),
        ):
            with pytest.raises(cutover.inventory.InventoryError) as error:
                operation()
            assert str(error.value) == expected
            assert all_rows(engine) == before


@pytest.mark.parametrize("revision", ["p21team001", "c22class001"])
def test_actual_migrated_integer_type_controls(migrated_db, revision):
    if revision == "c22class001":
        command.upgrade(config(), revision)
    before = all_rows(migrated_db[1])
    with migrated_db[1].connect() as connection:
        assert calendar.schema_guard(connection) == revision
        assert cutover._schema_guard(connection) == revision
        if revision == "c22class001":
            for table, column in (("classroom_classes", "id"),
                                  ("classroom_classes", "program_id"),
                                  ("classroom_programs", "owner_version")):
                actual = next(c for c in inspect(connection).get_columns(table)
                              if c["name"] == column)
                assert str(actual["type"]).upper() == "INTEGER"
    assert all_rows(migrated_db[1]) == before


@pytest.mark.parametrize("table,column,declaration", [
    pytest.param("classroom_classes", "id", "SMALLINT", id="smallint-primary-key"),
    pytest.param("classroom_programs", "owner_version", "SMALLINT", id="smallint-counter"),
    pytest.param("classroom_classes", "id", "BIGINT", id="bigint-primary-key"),
    pytest.param("classroom_programs", "owner_version", "BIGINT", id="bigint-counter"),
    pytest.param("classroom_classes", "program_id", "SMALLINT", id="smallint-reference"),
    pytest.param("classroom_classes", "program_id", "BIGINT", id="bigint-reference"),
    pytest.param("classroom_programs", "owner_version", "NUMERIC(20, 0)", id="numeric-counter"),
    pytest.param("classroom_programs", "owner_version", "REAL", id="real-counter"),
])
def test_c22_changed_integer_types_refuse_both_guards(
        migrated_db, table, column, declaration):
    db = migrated_db
    command.upgrade(config(), "c22class001")
    # Positive behavior uses the actual migrated DDL, before any damage.
    if declaration == "SMALLINT" and column in {"id", "owner_version"}:
        owner, organization, _ = add_identity_and_personal_history(db[1])
        before_behavior = all_rows(db[1])
        with Session(db[1]) as session:
            version = 40000 if db[1].dialect.name == "postgresql" else 1
            program = m.ClassroomProgram(organization_id=organization,
                                         owner_verifier_id=owner, owner_version=version)
            session.add(program)
            session.flush()
            if column == "id":
                values = {"program_id": organization, "display_name": "Integer contract"}
                if db[1].dialect.name == "postgresql":
                    values["id"] = 40000
                room = m.ClassroomClass(**values)
                session.add(room)
                session.flush()
                assert isinstance(room.id, int) and room.id > 0
                if db[1].dialect.name == "postgresql":
                    assert room.id > 32767
            if db[1].dialect.name == "postgresql":
                assert program.owner_version > 32767
            session.rollback()
        # Values above SMALLINT's range are removed before narrowing the DDL.
        # SQLite's valid omitted-ID insert is likewise fully rolled back.
        assert all_rows(db[1]) == before_behavior
    before_ddl = all_rows(db[1])
    with db[1].begin() as connection:
        assert calendar.schema_guard(connection) == "c22class001"
        assert cutover._schema_guard(connection) == "c22class001"
        if connection.dialect.name == "sqlite":
            replace_empty_sqlite_integer_declaration(connection, table, column, declaration)
        else:
            connection.execute(text(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE {declaration}'))
    assert all_rows(db[1]) == before_ddl
    assert_c22_integer_damage_refused(db, table, check_public_operations=(
        declaration == "SMALLINT" and column in {"id", "owner_version"}))


@pytest.mark.parametrize("migrated_db", ["sqlite"], indirect=True)
@pytest.mark.parametrize("declaration,raw_type", [
    pytest.param("INT", "INT", id="int-primary-key"),
    pytest.param('"\u0131nteger"', "\u0131nteger", id="unicode-casefold-primary-key"),
])
def test_c22_sqlite_integer_type_normalization_cannot_hide_nonexact_primary_key(
        migrated_db, declaration, raw_type):
    db = migrated_db
    command.upgrade(config(), "c22class001")
    before = all_rows(db[1])
    with db[1].begin() as connection:
        replace_empty_sqlite_integer_declaration(connection, "classroom_classes", "id", declaration)
    assert all_rows(db[1]) == before
    with db[1].connect() as connection:
        # SQLAlchemy normalizes INT and Unicode case variants to INTEGER.
        # SQLite does not give either declaration INTEGER PRIMARY KEY semantics.
        reflected = next(c for c in inspect(connection).get_columns("classroom_classes")
                         if c["name"] == "id")
        assert str(reflected["type"]).upper() == "INTEGER"
        raw = next(r for r in connection.execute(text('PRAGMA table_info("classroom_classes")')).mappings()
                   if r["name"] == "id")
        assert raw["type"] == raw_type and raw["pk"] == 1
    assert_c22_integer_damage_refused(db, "classroom_classes", check_public_operations=True)
