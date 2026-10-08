"""C23 operator approval and immutable historical PTA artifacts on real DDL."""
from copy import deepcopy

from alembic import command
import pytest
from sqlalchemy import inspect, text

from app import season_team_activation, team_continuity_repair
from app.operator_classroom_s2_contract import C23
from tests.test_c22_operational_compatibility import (
    all_rows, alter_sqlite_definition, calendar, config, cutover, install_revision,
    migrated_db, set_clock,
)
from tests.test_persistent_team_cutover import NOW, activate, plan, stage
from tests.test_persistent_team_migration import disposable_sqlite_configuration


@pytest.mark.parametrize("original", ["p21team001", "c22class001"])
@pytest.mark.parametrize("state", ["unstaged", "staged", "activated"])
def test_historical_artifacts_keep_hash_boundary_and_coverage_after_c23(
        migrated_db, original, state):
    db = migrated_db
    install_revision(db, original)
    approved = plan(db)
    immutable = deepcopy(approved)
    if state != "unstaged":
        stage(db, approved)
    receipt = activate(db, approved) if state == "activated" else None
    immutable_receipt = deepcopy(receipt)
    before = all_rows(db[1])
    command.upgrade(config(), "c23class001")
    after_upgrade = all_rows(db[1])
    for name in before:
        assert after_upgrade[name] == before[name], name
    if state == "unstaged":
        with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
            stage(db, approved)
        assert plan(db)["content"]["revision"] == "c23class001"
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


def test_c23_does_not_reapprove_historical_repair_or_reverse_artifacts(migrated_db):
    db = migrated_db
    install_revision(db, "c23class001")
    before = all_rows(db[1])
    with db[1].connect() as connection:
        for guard in (calendar.schema_guard, cutover._schema_guard):
            assert guard(connection) == "c23class001"
        with pytest.raises(team_continuity_repair.RepairError, match="require d17contest001"):
            team_continuity_repair.schema_guard(connection)
    assert season_team_activation.activate(db[0])["reason_codes"] == ["seasonal_team_activation_retired"]
    for older in ("p21team001", "c22class001"):
        with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
            cutover._plan_schema_guard({"revision": "c23class001"}, older)
    assert all_rows(db[1]) == before


def test_false_c23_stamp_without_s2_structure_refuses_before_writes(migrated_db):
    db = migrated_db
    install_revision(db, "c22class001")
    with db[1].begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='c23class001'"))
    assert_c23_refuses(db)


def assert_c23_refuses(db, reason="classroom_schema_missing_or_changed"):
    before = all_rows(db[1])
    for operation in (lambda: plan(db),
                      lambda: calendar.provision(db[0], seasons=["halloween-2026"]),
                      lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])):
        with pytest.raises(cutover.inventory.InventoryError, match=reason):
            operation()
    assert all_rows(db[1]) == before


@pytest.mark.parametrize("damage", ["table", "column", "type", "check", "foreign_key", "index", "predicate", "trigger"])
def test_malformed_c23_structure_is_refused_by_both_operators(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    from app.operator_classroom_s2_contract import C23
    first = next(iter(C23))
    with db[1].begin() as connection:
        if damage == "table":
            connection.execute(text("DROP TABLE classroom_s2_audit_events"))
        elif damage == "column":
            connection.execute(text(f'ALTER TABLE "{first}" RENAME COLUMN created_at TO unapproved_created_at'))
        elif damage == "type":
            if connection.dialect.name == "postgresql":
                connection.execute(text(f'ALTER TABLE "{first}" ALTER COLUMN id TYPE BIGINT'))
            else:
                # Changing INTEGER PRIMARY KEY via writable_schema corrupts
                # SQLite's rowid layout; a non-PK column tests exact types safely.
                alter_sqlite_definition(connection, first, "class_limit INTEGER", "class_limit BIGINT")
        elif damage == "check":
            key, check = next(iter(C23[first]["checks"].items()))
            if connection.dialect.name == "postgresql":
                connection.execute(text(f'ALTER TABLE "{first}" DROP CONSTRAINT "{key}"'))
                connection.execute(text(f'ALTER TABLE "{first}" ADD CONSTRAINT "{key}" CHECK (true)'))
            else:
                alter_sqlite_definition(connection, first, check, "1=1")
        elif damage == "foreign_key":
            if connection.dialect.name == "postgresql":
                fk = inspect(connection).get_foreign_keys(first)[0]
                connection.execute(text(f'ALTER TABLE "{first}" DROP CONSTRAINT "{fk["name"]}"'))
            else:
                alter_sqlite_definition(connection, first, "ON DELETE RESTRICT", "ON DELETE NO ACTION")
        elif damage in {"index", "predicate"}:
            name, (columns, unique, predicate) = next((name, index)
                for spec in C23.values() for name, index in spec["indexes"].items() if index[2])
            table = next(table for table, spec in C23.items() if name in spec["indexes"])
            connection.execute(text(f'DROP INDEX "{name}"'))
            if damage == "predicate":
                column_list = ", ".join('"' + column + '"' for column in columns)
                connection.execute(text(f'CREATE UNIQUE INDEX "{name}" ON "{table}" ({column_list}) WHERE 1=1'))
        else:
            if connection.dialect.name == "postgresql":
                connection.execute(text("ALTER TABLE classroom_membership_periods DISABLE TRIGGER classroom_period_no_overlap"))
            else:
                connection.execute(text("DROP TRIGGER classroom_period_no_overlap_insert"))
    assert_c23_refuses(db)


@pytest.mark.parametrize("operation", ["stage", "activate", "calendar"])
def test_unexpected_s2_mutation_rolls_back_whole_operator_transaction(
        migrated_db, monkeypatch, operation):
    db = migrated_db
    install_revision(db, "c23class001")
    approved = plan(db)
    if operation == "activate":
        stage(db, approved)
    set_clock(monkeypatch, NOW)
    target, action = (("contest_weeks", "INSERT") if operation == "calendar"
                      else ("persistent_team_control", "UPDATE"))
    with db[1].begin() as connection:
        mutation = "UPDATE classroom_entitlements SET class_limit=class_limit+1"
        if connection.dialect.name == "postgresql":
            connection.execute(text(f"CREATE FUNCTION test_forbidden_s2_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {mutation}; RETURN NEW; END; $$"))
            connection.execute(text(f"CREATE TRIGGER test_forbidden_s2_change AFTER {action} ON {target} FOR EACH ROW EXECUTE FUNCTION test_forbidden_s2_change()"))
        else:
            connection.execute(text(f"CREATE TRIGGER test_forbidden_s2_change AFTER {action} ON {target} BEGIN {mutation}; END"))
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
@pytest.mark.parametrize("damage", ["unvalidated_check", "deferrable_fk", "function", "trigger_columns", "trigger_when"])
def test_postgres_s2_enforcement_state_is_part_of_exact_contract(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    with db[1].begin() as connection:
        if damage == "unvalidated_check":
            connection.execute(text("ALTER TABLE classroom_entitlements DROP CONSTRAINT ck_classroom_entitlement_dates"))
            connection.execute(text("ALTER TABLE classroom_entitlements ADD CONSTRAINT ck_classroom_entitlement_dates CHECK (ends_at > starts_at) NOT VALID"))
        elif damage == "deferrable_fk":
            fk = inspect(connection).get_foreign_keys("classroom_entitlements")[0]
            connection.execute(text(f'ALTER TABLE classroom_entitlements ALTER CONSTRAINT "{fk["name"]}" DEFERRABLE INITIALLY DEFERRED'))
        elif damage == "function":
            connection.execute(text("ALTER FUNCTION classroom_period_no_overlap() SECURITY DEFINER"))
        else:
            connection.execute(text("DROP TRIGGER classroom_period_no_overlap ON classroom_membership_periods"))
            event = "INSERT OR UPDATE OF starts_at" if damage == "trigger_columns" else "INSERT OR UPDATE"
            when = " WHEN (NEW.ended_at IS NOT NULL)" if damage == "trigger_when" else ""
            connection.execute(text("CREATE TRIGGER classroom_period_no_overlap BEFORE " + event +
                " ON classroom_membership_periods FOR EACH ROW" + when +
                " EXECUTE FUNCTION classroom_period_no_overlap()"))
    assert_c23_refuses(db)


@pytest.mark.parametrize("damage", ["timestamp_default", "activation_default", "id_default", "global_code_unique", "extra_period_trigger"])
def test_c23_rejects_unreviewed_defaults_indexes_and_triggers(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    with db[1].begin() as connection:
        assert calendar.schema_guard(connection) == "c23class001"
        assert cutover._schema_guard(connection) == "c23class001"
        if damage == "timestamp_default":
            if connection.dialect.name == "postgresql":
                connection.execute(text("ALTER TABLE classroom_entitlements ALTER COLUMN created_at SET DEFAULT '2099-01-01 00:00:00+00'::timestamptz"))
            else:
                alter_sqlite_definition(connection, "classroom_entitlements", "CURRENT_TIMESTAMP", "'2099-01-01 00:00:00'")
        elif damage == "activation_default":
            if connection.dialect.name == "postgresql":
                connection.execute(text("ALTER TABLE classroom_entitlements ALTER COLUMN status SET DEFAULT 'active'"))
            else:
                alter_sqlite_definition(connection, "classroom_entitlements", "status VARCHAR(10) NOT NULL", "status VARCHAR(10) DEFAULT 'active' NOT NULL")
        elif damage == "id_default":
            if connection.dialect.name == "postgresql":
                connection.execute(text("ALTER TABLE classroom_entry_codes ALTER COLUMN id SET DEFAULT 99"))
            else:
                alter_sqlite_definition(connection, "classroom_entry_codes", "id INTEGER NOT NULL", "id INTEGER DEFAULT 99 NOT NULL")
        elif damage == "global_code_unique":
            connection.execute(text("CREATE UNIQUE INDEX unreviewed_global_class_code ON classroom_entry_codes(digest)"))
        elif connection.dialect.name == "postgresql":
            connection.execute(text("CREATE TRIGGER unreviewed_period_trigger AFTER INSERT ON classroom_membership_periods FOR EACH ROW EXECUTE FUNCTION classroom_period_no_overlap()"))
        else:
            connection.execute(text("CREATE TRIGGER unreviewed_period_trigger AFTER INSERT ON classroom_membership_periods BEGIN SELECT 1; END"))
    assert_c23_refuses(db)


@pytest.mark.parametrize("damage", ["update_action", "duplicate_fk"])
def test_c23_foreign_key_options_and_count_remain_exact(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    with db[1].begin() as connection:
        assert calendar.schema_guard(connection) == "c23class001"
        assert cutover._schema_guard(connection) == "c23class001"
        if connection.dialect.name == "postgresql":
            if damage == "update_action":
                fk = next(f for f in inspect(connection).get_foreign_keys("classroom_entitlements")
                          if f["constrained_columns"] == ["program_id"])
                connection.execute(text(f'ALTER TABLE classroom_entitlements DROP CONSTRAINT "{fk["name"]}"'))
                connection.execute(text("ALTER TABLE classroom_entitlements ADD FOREIGN KEY (program_id) REFERENCES classroom_programs(organization_id) ON DELETE RESTRICT ON UPDATE CASCADE"))
            else:
                connection.execute(text("ALTER TABLE classroom_entitlements ADD CONSTRAINT unreviewed_duplicate_program FOREIGN KEY (program_id) REFERENCES classroom_programs(organization_id) ON DELETE RESTRICT"))
        elif damage == "update_action":
            alter_sqlite_definition(connection, "classroom_entitlements", "ON DELETE RESTRICT",
                                    "ON DELETE RESTRICT ON UPDATE CASCADE")
        else:
            original = connection.scalar(text("SELECT sql FROM sqlite_master WHERE name='classroom_entitlements'"))
            prefix, suffix = original.rsplit(")", 1)
            replacement = prefix + ", CONSTRAINT unreviewed_duplicate_program FOREIGN KEY (program_id) REFERENCES classroom_programs(organization_id) ON DELETE RESTRICT)" + suffix
            alter_sqlite_definition(connection, "classroom_entitlements", original, replacement)
    assert_c23_refuses(db)


@pytest.mark.parametrize("migrated_db", ["sqlite"], indirect=True)
def test_c23_rejects_sqlite_expression_index_hidden_by_reflection(migrated_db):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    with db[1].begin() as connection:
        assert calendar.schema_guard(connection) == "c23class001"
        assert cutover._schema_guard(connection) == "c23class001"
        connection.execute(text("CREATE UNIQUE INDEX unreviewed_code_expression ON classroom_entry_codes(substr(digest,1,1))"))
    assert_c23_refuses(db)


@pytest.mark.parametrize("migrated_db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("damage", ["detached", "owned_by_other_column"])
def test_c23_serial_default_requires_its_own_sequence(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    with db[1].begin() as connection:
        assert calendar.schema_guard(connection) == "c23class001"
        assert cutover._schema_guard(connection) == "c23class001"
        owner = "NONE" if damage == "detached" else "classroom_entry_codes.generation"
        connection.execute(text("ALTER SEQUENCE classroom_entry_codes_id_seq OWNED BY " + owner))
    assert_c23_refuses(db)


@pytest.mark.parametrize("table", list(C23))
def test_c23_refuses_unapproved_triggers_on_every_s2_table(migrated_db, table):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    with db[1].begin() as connection:
        for guard in (calendar.schema_guard, cutover._schema_guard):
            assert guard(connection) == "c23class001"
        if connection.dialect.name == "postgresql":
            mutation = "NEW.class_limit = 9999;" if table == "classroom_entitlements" else ""
            connection.execute(text("CREATE FUNCTION unreviewed_s2_write() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN " + mutation + " RETURN NEW; END; $$"))
            connection.execute(text(f'CREATE TRIGGER unreviewed_s2_write BEFORE INSERT ON "{table}" '
                'FOR EACH ROW EXECUTE FUNCTION unreviewed_s2_write()'))
        else:
            mutation = ("UPDATE classroom_entitlements SET class_limit = 9999 WHERE id = NEW.id"
                        if table == "classroom_entitlements" else "SELECT 1")
            connection.execute(text(f'CREATE TRIGGER unreviewed_s2_write AFTER INSERT ON "{table}" '
                f'BEGIN {mutation}; END'))
    assert_c23_refuses(db)


@pytest.mark.parametrize("migrated_db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("damage", ["disabled_child", "disabled_parent", "replica_child"])
def test_c23_requires_foreign_key_triggers_on_both_sides(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    with db[1].begin() as connection:
        for guard in (calendar.schema_guard, cutover._schema_guard):
            assert guard(connection) == "c23class001"
        if damage == "disabled_child":
            connection.execute(text("ALTER TABLE classroom_entry_codes DISABLE TRIGGER ALL"))
        else:
            side = "confrelid" if damage == "disabled_parent" else "conrelid"
            table, trigger = connection.execute(text(f"""
                SELECT r.relname, t.tgname FROM pg_trigger t
                JOIN pg_constraint c ON c.oid = t.tgconstraint
                JOIN pg_class r ON r.oid = t.tgrelid
                WHERE c.conrelid = 'classroom_entry_codes'::regclass
                  AND t.tgrelid = c.{side} AND t.tgisinternal
                ORDER BY t.oid LIMIT 1
            """)).one()
            action = "DISABLE TRIGGER" if damage == "disabled_parent" else "ENABLE REPLICA TRIGGER"
            connection.execute(text(f'ALTER TABLE "{table}" {action} "{trigger}"'))
    assert_c23_refuses(db)


@pytest.mark.parametrize("migrated_db", ["postgresql"], indirect=True)
def test_c23_refuses_session_that_bypasses_foreign_key_triggers(migrated_db):
    db = migrated_db
    command.upgrade(config(), "c23class001")
    before = all_rows(db[1])
    with db[1].begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        for guard in (calendar.schema_guard, cutover._schema_guard):
            with pytest.raises(cutover.inventory.InventoryError, match="s2_foreign_key_enforcement"):
                guard(connection)
    assert all_rows(db[1]) == before
