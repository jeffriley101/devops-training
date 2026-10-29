"""Calendar/PTA contracts on real migrations, with immutable historical evidence."""
from copy import deepcopy
from datetime import date

from alembic import command
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.orm import Session, registry

from app import models as m, season_team_activation, team_continuity_repair
from app.operator_weekly_contract import W25, PRESERVED_TABLES
from tests.test_c22_operational_compatibility import (
    all_rows, alter_sqlite_definition, calendar, canonical_seed_names, config,
    cutover, install_revision, set_clock,
)
from tests.test_c24_operational_compatibility import install_c24
from tests.test_persistent_team_cutover import NOW, activate, plan, seed, stage
from tests.test_persistent_team_migration import disposable_sqlite_configuration
from tests.test_team_families import disposable_url
from tests.pta_legacy_receipts import activate_legacy_fixture
from app import pta_receipt_schema as issuance


@pytest.fixture(params=["sqlite", "postgresql"])
def migrated_db(request, tmp_path, monkeypatch):
    url = disposable_url(tmp_path, request.param)
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(config(), "p21team001")
    bootstrap = create_engine(url)
    table = Table("woodchuck_profiles", MetaData(), autoload_with=bootstrap)
    mapping = registry()
    class LegacyProfile:
        pass
    mapping.map_imperatively(LegacyProfile, table)
    # Reuse the accepted authority seed against the actual p21 column set.
    # Current ORM preferences do not exist yet; no schema stamping/create_all.
    try:
        with monkeypatch.context() as patch:
            patch.setattr(m, "WoodchuckProfile", lambda **values:
                LegacyProfile(created_at=NOW, updated_at=NOW, **values))
            engine = seed(url, migrated=True)
    finally:
        mapping.dispose()
        bootstrap.dispose()
    canonical_seed_names(engine)
    try:
        yield url, engine
    finally:
        engine.dispose()


def install(db, revision):
    if revision in {"c24class001", "w25weekly005"}:
        install_c24(db)
        if revision == "w25weekly005":
            command.upgrade(config(), revision)
    else:
        install_revision(db, revision)


def populate_weekly(db):
    from app.weekly_models import WeeklyPlan, WeeklyInstrumentPlan, WeeklyWheelPlay
    from app.weekly_models import WeeklyTaskClaim, WeeklyNewsletterPresentation
    with Session(db[1]) as s:
        p = s.get(m.WoodchuckProfile, 24)
        p.game_mode, p.first_choice_instrument = "Challenge", "Flute"
        saved = WeeklyPlan(profile_id=p.id, week_start=date(2026, 9, 28),
            focus="Build daily consistency", issued_game_mode="Challenge",
            generator_version="synthetic-preservation", issued_at=NOW,
            recent_results_cutoff=NOW, recent_results={"preserve": True},
            logging_days_target=4, practice_minutes_target=60,
            homework_minutes_target=15, thirds_target=7)
        s.add(saved)
        s.flush()
        s.add_all([
            WeeklyInstrumentPlan(plan_id=saved.id, slot_no=1, instrument="Trumpet",
                started_at=NOW, homework_spec={"synthetic": "unchanged"}),
            WeeklyTaskClaim(plan_id=saved.id, lane="thirds", claimed_at=NOW),
            WeeklyWheelPlay(id="a" * 32, profile_id=p.id, request_key="b" * 32,
                funded_day=NOW.date(), funded_at=NOW, daily_ordinal=1,
                outcomes=["dandelions_400"], completed_at=NOW),
            WeeklyNewsletterPresentation(profile_id=p.id, week_start=saved.week_start, reserved_at=NOW),
            m.RewardGrant(profile_id=p.id, source_key="synthetic-earlier-earned", reward_type="pickled_carrot", amount=6),
            m.RewardGrant(profile_id=p.id, source_key="synthetic-earlier-spent", reward_type="pickled_carrot", amount=-3),
            m.RewardGrant(profile_id=p.id, source_key="synthetic-prize", reward_type="dandelion", amount=400),
            m.OwnedItemCopy(profile_id=p.id, item_key="ufo", acquisition_source="mum", acquisition_key="synthetic-copy"),
        ])
        state = s.get(m.WoodchuckState, p.id)
        state.state_json = {"dandelions": 475, "synthetic": "preserve"}
        s.commit()
    rows = all_rows(db[1])
    assert all(rows[name] for name in W25)
    assert sum(row["amount"] for row in rows["reward_grants"] if row["reward_type"] == "pickled_carrot") == 3


@pytest.mark.parametrize("revision", ["p21team001", "c22class001", "c23class001", "c24class001", "w25weekly005"])
def test_calendar_and_pta_full_cycle(migrated_db, monkeypatch, revision, record_property):
    db = migrated_db
    install(db, revision)
    if revision == "w25weekly005":
        populate_weekly(db)
    set_clock(monkeypatch, NOW)
    before = all_rows(db[1])
    approved = plan(db)
    calendar.provision(db[0], seasons=["halloween-2026"])
    assert all_rows(db[1]) == before
    assert approved["content"]["revision"] == revision
    stage(db, approved)
    command.upgrade(config(), issuance.REVISION)
    before = all_rows(db[1])
    receipt = activate(db, approved)
    after = all_rows(db[1])
    verified = cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)
    assert verified["passed"] and all_rows(db[1]) == after
    if revision == "w25weekly005":
        assert receipt["verification"]["evidence_contract"] == "pta-w25weekly005-v1"
        assert not verified["receipt_coverage"]["excluded_added_columns"]
        for name in PRESERVED_TABLES:
            assert after[name] == before[name], name
    calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
    final = all_rows(db[1])
    assert calendar.provision(db[0], apply=True, seasons=["halloween-2026"])["status"] == "ALREADY_COMPLETE"
    assert all_rows(db[1]) == final
    allowed = {"contest_weeks", "teams", "team_memberships", "persistent_team_control", "team_week_membership_snapshots", issuance.NAME}
    for name in before.keys() - allowed:
        assert final[name] == before[name], name
    record_property("operator_receipt", receipt)
    record_property("all_other_business_tables_unchanged", True)


@pytest.mark.parametrize("original", ["p21team001", "c22class001", "c23class001", "c24class001"])
@pytest.mark.parametrize("state", ["unstaged", "staged", "activated"])
def test_historical_approval_and_receipt(migrated_db, original, state, record_property):
    db = migrated_db
    install(db, original)
    approved = plan(db)
    original_plan = deepcopy(approved)
    if state != "unstaged":
        stage(db, approved)
    receipt = activate_legacy_fixture(db, approved) if state == "activated" else None
    original_receipt = deepcopy(receipt)
    command.upgrade(config(), issuance.REVISION)
    before = all_rows(db[1])
    if state == "unstaged":
        with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
            stage(db, approved)
        assert all_rows(db[1]) == before
        assert plan(db)["plan_sha256"] != approved["plan_sha256"]
        return
    if state == "staged":
        receipt = activate(db, approved)
        assert receipt["verification"]["evidence_contract"] == "pta-w25weekly005-v1"
    else:
        assert "evidence_contract" not in receipt["verification"]
    after = all_rows(db[1])
    if state == "activated":
        with pytest.raises(cutover.CutoverError, match="trusted_receipt_issuance_required"):
            cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)
        assert after[issuance.NAME] == []
    else:
        result = cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)
        assert result["passed"]
    assert all_rows(db[1]) == after
    assert approved == original_plan
    if original_receipt is not None:
        assert receipt == original_receipt
    assert after["persistent_team_control"][0]["staged_plan"] == original_plan
    assert after["persistent_team_control"][0]["staged_plan_sha256"] == approved["plan_sha256"]
    record_property("historical_provenance", "missing_refused" if state == "activated" else "new_issuance_verified")
    record_property("approval_and_old_receipt_unchanged", True)


def test_receipt_coverage_never_falls_back_or_ignores_old_changes(migrated_db):
    db = migrated_db
    approved = plan(db)
    stage(db, approved)
    receipt = activate_legacy_fixture(db, approved)
    command.upgrade(config(), issuance.REVISION)
    original = deepcopy((approved, receipt))
    with db[1].begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET game_mode='Challenge', first_choice_instrument='Flute' WHERE id=24"))
    # A digest that matches historical columns cannot establish issuance history.
    with pytest.raises(cutover.CutoverError, match="trusted_receipt_issuance_required"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)
    unknown = deepcopy(receipt)
    unknown["verification"]["evidence_contract"] = "invented-contract"
    with pytest.raises(cutover.CutoverError, match="trusted_receipt_issuance_required"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], unknown)
    with db[1].begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET display_name='changed' WHERE id=24"))
    before = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match="trusted_receipt_issuance_required"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)
    assert all_rows(db[1]) == before and (approved, receipt) == original


def test_new_receipt_covers_preferences_even_for_old_staged_plan(migrated_db):
    db = migrated_db
    approved = plan(db)
    stage(db, approved)
    command.upgrade(config(), issuance.REVISION)
    receipt = activate(db, approved)
    missing_marker = deepcopy(receipt)
    del missing_marker["verification"]["evidence_contract"]
    with pytest.raises(cutover.CutoverError, match="activation_receipt_issuance_mismatch"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], missing_marker)
    with db[1].begin() as c:
        c.execute(text("UPDATE woodchuck_profiles SET game_mode='Beginner' WHERE id=24"))
    before = all_rows(db[1])
    with pytest.raises(cutover.CutoverError, match="post_activation_state_changed"):
        cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)
    assert all_rows(db[1]) == before


def refuse_both(db):
    before = all_rows(db[1])
    for operation in (lambda: plan(db), lambda: calendar.provision(db[0], seasons=["halloween-2026"]),
                      lambda: calendar.provision(db[0], apply=True, seasons=["halloween-2026"])):
        with pytest.raises(cutover.inventory.InventoryError, match="schema_missing_or_changed|revision_not_approved"):
            operation()
    assert all_rows(db[1]) == before


def test_revision_and_transition_refusals(migrated_db):
    db = migrated_db
    install(db, "w25weekly005")
    for stamp in ("unknown", "p20team001", "w25weekly001", "w25weekly002", "w25weekly003", "w25weekly004"):
        with db[1].begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num=:stamp"), {"stamp": stamp})
        refuse_both(db)
    for older in ("p21team001", "c22class001", "c23class001", "c24class001"):
        with pytest.raises(cutover.CutoverError, match="plan_schema_revision_changed"):
            cutover._plan_schema_guard({"revision": "w25weekly005"}, older)


def test_false_weekly_stamp_refuses(migrated_db):
    db = migrated_db
    install(db, "c24class001")
    with db[1].begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num='w25weekly005'"))
    refuse_both(db)


@pytest.mark.parametrize("damage", ["table", "column", "type", "check", "unique", "foreign_key",
                                   "index", "profile_default", "profile_check", "s1", "s2", "s3"])
def test_schema_tampering_refuses(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "w25weekly005")
    with db[1].begin() as c:
        assert calendar.schema_guard(c) == "w25weekly005"
        assert cutover._schema_guard(c) == "w25weekly005"
        pg = c.dialect.name == "postgresql"
        if damage == "table":
            c.execute(text("DROP TABLE weekly_newsletter_presentations"))
        elif damage == "column":
            c.execute(text("ALTER TABLE weekly_plans RENAME COLUMN homework_minutes_target TO unapproved_target"))
        elif damage == "type":
            if pg:
                c.execute(text("ALTER TABLE weekly_plans ALTER COLUMN homework_minutes_target TYPE BIGINT"))
            else:
                alter_sqlite_definition(c, "weekly_plans", "homework_minutes_target INTEGER", "homework_minutes_target BIGINT")
        elif damage == "check":
            if pg:
                c.execute(text("ALTER TABLE weekly_wheel_plays DROP CONSTRAINT ck_weekly_wheel_daily_ordinal"))
            else:
                alter_sqlite_definition(c, "weekly_wheel_plays", "daily_ordinal BETWEEN 1 AND 9", "1=1")
        elif damage == "unique":
            if pg:
                c.execute(text("ALTER TABLE weekly_wheel_plays DROP CONSTRAINT uq_weekly_wheel_request"))
            else:
                alter_sqlite_definition(c, "weekly_wheel_plays", "UNIQUE (profile_id, request_key)", "UNIQUE (request_key)")
        elif damage == "foreign_key":
            if pg:
                key = inspect(c).get_foreign_keys("weekly_newsletter_presentations")[0]["name"]
                c.execute(text(f'ALTER TABLE weekly_newsletter_presentations DROP CONSTRAINT "{key}"'))
            else:
                alter_sqlite_definition(c, "weekly_newsletter_presentations", "ON DELETE CASCADE", "ON DELETE NO ACTION")
        elif damage == "index":
            c.execute(text("DROP INDEX ix_weekly_wheel_plays_profile_id"))
        elif damage == "profile_default":
            if pg:
                c.execute(text("ALTER TABLE woodchuck_profiles ALTER COLUMN game_mode SET DEFAULT 'Beginner'"))
            else:
                alter_sqlite_definition(c, "woodchuck_profiles", "game_mode VARCHAR(20)", "game_mode VARCHAR(20) DEFAULT 'Beginner'")
        elif damage == "profile_check":
            if pg:
                c.execute(text("ALTER TABLE woodchuck_profiles DROP CONSTRAINT ck_woodchuck_profile_game_mode"))
            else:
                alter_sqlite_definition(c, "woodchuck_profiles", "game_mode IS NULL OR game_mode IN ('Beginner', 'Challenge', 'Performance')", "1=1")
        elif damage == "s1":
            c.execute(text("ALTER TABLE classroom_programs RENAME COLUMN owner_version TO unapproved_version"))
        elif damage == "s2":
            c.execute(text("ALTER TABLE classroom_entitlements RENAME COLUMN class_limit TO unapproved_limit"))
        else:
            c.execute(text("ALTER TABLE classroom_reporting_periods RENAME COLUMN scope_version TO unapproved_version"))
    refuse_both(db)


@pytest.mark.parametrize("migrated_db", ["postgresql"], indirect=True)
@pytest.mark.parametrize("damage", ["not_valid", "deferred", "disabled_fk", "unexpected_trigger", "jsonb"])
def test_postgres_enforcement_refuses(migrated_db, damage):
    db = migrated_db
    command.upgrade(config(), "w25weekly005")
    with db[1].begin() as c:
        if damage == "not_valid":
            c.execute(text("ALTER TABLE weekly_wheel_plays DROP CONSTRAINT ck_weekly_wheel_daily_ordinal"))
            c.execute(text("ALTER TABLE weekly_wheel_plays ADD CONSTRAINT ck_weekly_wheel_daily_ordinal CHECK (daily_ordinal BETWEEN 1 AND 9) NOT VALID"))
        elif damage == "deferred":
            c.execute(text("ALTER TABLE weekly_wheel_plays DROP CONSTRAINT uq_weekly_wheel_request"))
            c.execute(text("ALTER TABLE weekly_wheel_plays ADD CONSTRAINT uq_weekly_wheel_request UNIQUE (profile_id,request_key) DEFERRABLE"))
        elif damage == "disabled_fk":
            c.execute(text("ALTER TABLE weekly_newsletter_presentations DISABLE TRIGGER ALL"))
        elif damage == "unexpected_trigger":
            c.execute(text("CREATE FUNCTION unapproved() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"))
            c.execute(text("CREATE TRIGGER unapproved BEFORE INSERT ON weekly_task_claims FOR EACH ROW EXECUTE FUNCTION unapproved()"))
        else:
            c.execute(text("ALTER TABLE weekly_plans ALTER COLUMN recent_results TYPE JSONB USING recent_results::jsonb"))
    refuse_both(db)


@pytest.mark.parametrize("operation,mutation", [
    ("stage", "UPDATE weekly_plans SET generator_version='unexpected'"),
    ("activate", "UPDATE woodchuck_profiles SET game_mode='Performance' WHERE id=24"),
    ("calendar", "UPDATE reward_grants SET amount=amount+1"),
    ("calendar", "UPDATE classroom_reporting_periods SET scope_version='unexpected'"),
])
def test_transaction_rollback_preserves_every_business_row(migrated_db, monkeypatch, operation, mutation):
    db = migrated_db
    install(db, "w25weekly005")
    command.upgrade(config(), issuance.REVISION)
    populate_weekly(db)
    set_clock(monkeypatch, NOW)
    approved = plan(db)
    if operation == "activate":
        stage(db, approved)
    target, action = ("contest_weeks", "INSERT") if operation == "calendar" else ("persistent_team_control", "UPDATE")
    with db[1].begin() as c:
        if c.dialect.name == "postgresql":
            c.execute(text(f"CREATE FUNCTION unexpected_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN {mutation}; RETURN NEW; END $$"))
            c.execute(text(f"CREATE TRIGGER unexpected_write AFTER {action} ON {target} FOR EACH ROW EXECUTE FUNCTION unexpected_write()"))
        else:
            c.execute(text(f"CREATE TRIGGER unexpected_write AFTER {action} ON {target} BEGIN {mutation}; END"))
    before = all_rows(db[1])
    with pytest.raises(cutover.inventory.InventoryError, match="unexpected_row_change|unexpected_classroom_row_change"):
        if operation == "stage":
            stage(db, approved)
        elif operation == "activate":
            activate(db, approved)
        else:
            calendar.provision(db[0], apply=True, seasons=["halloween-2026"])
    assert all_rows(db[1]) == before


def test_historical_continuity_repair_remains_refused(migrated_db):
    db = migrated_db
    command.upgrade(config(), "w25weekly005")
    before = all_rows(db[1])
    with db[1].connect() as c:
        with pytest.raises(team_continuity_repair.RepairError, match="require d17contest001"):
            team_continuity_repair.schema_guard(c)
    assert season_team_activation.activate(db[0])["reason_codes"] == ["seasonal_team_activation_retired"]
    assert all_rows(db[1]) == before
