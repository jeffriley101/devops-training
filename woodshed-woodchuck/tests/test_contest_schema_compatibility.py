"""Calendar p20 approval and unchanged legacy repair guards on disposable DBs."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import contest_week_provisioning as provisioning, models as m
from app import season_team_activation as activation, team_continuity_repair as repair
from app.contests import contest_week_schedule
from app.seasons import bootstrap_canonical_seasons
from tests import test_season_team_activation as lifecycle
from tests import test_team_continuity_repair as history
from tests.test_contest_week_provisioning import db, PAIR  # noqa: F401
from tests.test_team_families import disposable_url

OLD = "f19arcade001"
NEW = "p20team001"


def snapshot(engine):
    metadata = MetaData()
    metadata.reflect(engine)
    with engine.connect() as connection:
        return {table.name: list(connection.execute(select(table).order_by(*table.primary_key.columns)))
                for table in metadata.sorted_tables}


def assert_refused_without_writes(url, engine, reason):
    before = snapshot(engine)
    statements = []
    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement.strip().split()[0].upper())
    event.listen(Engine, "before_cursor_execute", capture)
    try:
        for apply in (False, True):
            with pytest.raises(repair.inventory.InventoryError, match=reason):
                provisioning.provision(url, apply=apply, **PAIR)
        assert activation.activate(url)["reason_codes"] == ["seasonal_team_activation_retired"]
    finally:
        event.remove(Engine, "before_cursor_execute", capture)
    assert not {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}.intersection(statements)
    assert snapshot(engine) == before


@pytest.mark.parametrize("state", ["old", "unknown", "unsupported", "empty", "multiple",
    "practice_scoring_mode", "finalizer_rules_version", "rules_column", "control_table", "control_row", "rules_check", "control_check", "boundary"])
def test_schema_refusals_before_writes(db, state):
    with db[1].begin() as connection:
        if state in {"old", "unknown", "unsupported"}:
            version = {"old": OLD, "unknown": "future_unapproved", "unsupported": "d17contest001"}[state]
            connection.execute(text("UPDATE alembic_version SET version_num=:v"), {"v": version})
        elif state == "empty":
            connection.execute(text("DELETE FROM alembic_version"))
        elif state == "multiple":
            connection.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": OLD})
        elif state in {"rules_check", "control_check"}:
            from alembic.operations import Operations
            from alembic.migration import MigrationContext
            op = Operations(MigrationContext.configure(connection))
            table, name, sql = (("contest_weeks", "ck_contest_week_team_membership_rules",
                "team_membership_rules_version IN ('legacy_seasonal_v1', 'persistent_v1') OR 1=1")
                if state == "rules_check" else ("persistent_team_control", "ck_persistent_team_control_singleton", "id=1 OR 1=1"))
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(name, type_="check")
                batch.create_check_constraint(name, sql)
        elif state == "boundary":
            connection.execute(text("UPDATE persistent_team_control SET activated_at=:at, rules_from_week_start=:start"),
                {"at": datetime(2026, 10, 1, 18, tzinfo=timezone.utc), "start": date(2026, 10, 6)})
        elif state == "control_table":
            connection.execute(text("DROP TABLE persistent_team_control"))
        elif state == "control_row":
            connection.execute(text("DELETE FROM persistent_team_control"))
        elif state == "rules_column":
            connection.execute(text("ALTER TABLE contest_weeks RENAME COLUMN team_membership_rules_version TO unapproved_rules"))
        else:
            connection.execute(text(f"ALTER TABLE contest_weeks DROP COLUMN {state}"))
    reason = ("required_calendar_schema_incomplete" if state in {
        "practice_scoring_mode", "finalizer_rules_version", "rules_column", "control_table"}
        else {"control_row": "calendar_authority_singleton_missing",
              "rules_check": "calendar_rules_constraint_missing_or_changed",
              "control_check": "calendar_authority_constraint_missing_or_changed",
              "boundary": "calendar_authority_week_boundary_invalid"}.get(state, "revision_not_approved"))
    assert_refused_without_writes(*db, reason)


def test_historical_repair_guard_is_not_reapproved_by_calendar_guard(db):
    before = snapshot(db[1])
    with db[1].connect() as connection:
        provisioning.schema_guard(connection)
        with pytest.raises(repair.RepairError, match="require d17contest001"):
            repair.schema_guard(connection)
    assert activation.activate(db[0])["reason_codes"] == ["seasonal_team_activation_retired"]
    assert snapshot(db[1]) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_real_base_upgrade_then_calendar_plan_apply_and_prospective_rules(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            bootstrap_canonical_seasons(session)
            session.add(m.WoodchuckProfile(id=1, woodchuck_id="WC-COMPAT", display_name="Compatibility",
                pin_hash="test", instrument="Flute", level="Beginner", goal="Practice"))
            session.commit()
        metadata = MetaData()
        metadata.reflect(engine)
        end, deadline, finalize = contest_week_schedule(date(2026, 9, 14))
        with engine.begin() as connection:
            source_id = connection.scalar(select(metadata.tables["seasons"].c.id).where(
                metadata.tables["seasons"].c.key == lifecycle.SOURCE))
            connection.execute(metadata.tables["team_families"].insert().values(id=1, created_at=lifecycle.BOUNDARY))
            connection.execute(metadata.tables["teams"].insert().values(id=10, family_id=1,
                season_id=source_id, display_name="Compatibility", normalized_name="compatibility",
                emblem_key="letter:C", creator_profile_id=1, created_at=lifecycle.BOUNDARY))
            connection.execute(metadata.tables["team_memberships"].insert().values(id=20,
                season_id=source_id, team_id=10, profile_id=1,
                started_at=lifecycle.BOUNDARY - timedelta(days=7), selected_week_start=date(2026, 9, 21), created_at=lifecycle.BOUNDARY))
            connection.execute(metadata.tables["contest_weeks"].insert().values(
                season_id=source_id, week_start=date(2026, 9, 14), week_end=end,
                verification_deadline_at=deadline + timedelta(hours=1),
                finalize_after=finalize + timedelta(hours=1), status="finalized", finalized_at=finalize + timedelta(days=1),
                practice_scoring_mode="legacy_minutes", created_at=lifecycle.BOUNDARY, updated_at=lifecycle.BOUNDARY))
        assert_refused_without_writes(url, engine, "require p20team001")
        before = snapshot(engine)
        command.upgrade(config, NEW)
        migrated = snapshot(engine)
        for table, rows in before.items():
            if table != "alembic_version":
                assert len(migrated[table]) == len(rows)
                for prior, current in zip(rows, migrated[table]):
                    assert tuple(current._mapping[key] for key in prior._mapping) == tuple(prior)
        with Session(engine) as session:
            week = session.scalar(select(m.ContestWeek))
            assert week.practice_scoring_mode == "legacy_minutes"
            assert week.finalizer_rules_version is None
            assert week.team_membership_rules_version == "legacy_seasonal_v1"
        assert provisioning.provision(url, **PAIR)["missing"] == 2
        assert snapshot(engine) == migrated
        assert provisioning.provision(url, apply=True, **PAIR)["created"] == 2
        with engine.connect() as connection, pytest.raises(repair.RepairError, match="require d17contest001"):
            repair.schema_guard(connection)
        assert activation.activate(url)["reason_codes"] == ["seasonal_team_activation_retired"]
        after = snapshot(engine)
        assert provisioning.provision(url, apply=True, **PAIR)["status"] == "ALREADY_COMPLETE"
        assert snapshot(engine) == after
        with Session(engine) as session:
            current = session.scalar(select(m.ContestWeek).where(m.ContestWeek.week_start == date(2026, 9, 28)))
            prior_week = dict(session.execute(select(m.ContestWeek.__table__).where(m.ContestWeek.id == current.id)).mappings().one())
            control = session.get(m.PersistentTeamControl, 1)
            control.activated_at = datetime(2026, 10, 1, 18, tzinfo=timezone.utc)
            control.rules_from_week_start = date(2026, 10, 5)
            session.commit()
        assert provisioning.provision(url, apply=True, seasons=[lifecycle.DEST])["created"] == 4
        with Session(engine) as session:
            assert dict(session.execute(select(m.ContestWeek.__table__).where(m.ContestWeek.week_start == date(2026, 9, 28))).mappings().one()) == prior_week
            future = session.scalars(select(m.ContestWeek).where(m.ContestWeek.week_start >= date(2026, 10, 5))).all()
            assert len(future) == 4 and {w.team_membership_rules_version for w in future} == {"persistent_v1"}
            assert session.scalar(select(m.Team.id)) == 10
            assert session.scalar(select(m.TeamMembership.id)) == 20
        assert provisioning.provision(url, apply=True, seasons=[lifecycle.DEST])["status"] == "ALREADY_COMPLETE"
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", ["precise_score", "practice_scoring_mode", "finalizer_rules_version"])
def test_precision_history_is_verified_and_invalidates_approved_plan(tmp_path, field):
    url = f"sqlite:///{tmp_path / 'history.db'}"
    engine = history.seed_database(url)
    try:
        history.apply((url, engine), history.plan((url, engine)))
        approved = history.plan((url, engine))
        table = "contest_weeks" if field in {"practice_scoring_mode", "finalizer_rules_version"} else "contest_results"
        with engine.begin() as connection:
            assert field in repair.Snapshot(connection).rows(table)[0]
            connection.execute(text(f"UPDATE {table} SET {field}=:value"),
                               {"value": "precise_seconds" if field == "practice_scoring_mode"
                                else "contest_finalizer_v1" if field == "finalizer_rules_version"
                                else 40.25})
        current = history.plan((url, engine))
        with pytest.raises(repair.RepairError, match="verification_history_or_preconditions_changed"):
            repair.verify_content(approved["content"], current["content"])
        before = history.full_snapshot(engine)
        with pytest.raises(repair.RepairError, match="stale_plan"):
            history.apply((url, engine), approved)
        assert history.full_snapshot(engine) == before
    finally:
        engine.dispose()
