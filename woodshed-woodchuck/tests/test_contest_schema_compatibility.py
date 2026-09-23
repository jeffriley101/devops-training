"""Explicit revision approval and real migration coverage on disposable databases."""
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import contest_week_provisioning as provisioning, models as m
from app import season_team_activation as activation, team_continuity_repair as repair
from app.contests import contest_week_schedule
from app.seasons import bootstrap_canonical_seasons
from tests import test_season_team_activation as lifecycle
from tests import test_team_continuity_repair as history
from tests.test_contest_week_provisioning import db, PAIR  # noqa: F401
from tests.test_team_families import disposable_url
from tests.team_factory import make_team

OLD = "c15arcade001"
NEW = "d16team001"


def snapshot(engine):
    metadata = MetaData()
    metadata.reflect(engine)
    with engine.connect() as connection:
        return {table.name: list(connection.execute(select(table).order_by(*table.primary_key.columns)))
                for table in metadata.sorted_tables}


def operations(url):
    return (
        lambda: provisioning.provision(url, **PAIR),
        lambda: provisioning.provision(url, apply=True, **PAIR),
        lambda: activation.preflight(url, **PAIR, now=lifecycle.BOUNDARY),
        lambda: activation.activate(url, **PAIR, now=lifecycle.BOUNDARY),
    )


def assert_refused_without_writes(url, engine, reason):
    before = snapshot(engine)
    statements = []
    def capture(connection, cursor, statement, parameters, context, many):
        statements.append(statement.strip().split()[0].upper())
    event.listen(Engine, "before_cursor_execute", capture)
    try:
        for operation in operations(url):
            with pytest.raises(repair.RepairError, match=reason):
                operation()
    finally:
        event.remove(Engine, "before_cursor_execute", capture)
    assert not {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}.intersection(statements)
    assert snapshot(engine) == before


@pytest.mark.parametrize("state", ["old", "unknown", "unsupported", "empty", "multiple",
                                  "precise_score", "practice_scoring_mode", "family_index", "name_claims"])
def test_schema_refusals_before_writes(db, state):
    with db[1].begin() as connection:
        if state in {"old", "unknown", "unsupported"}:
            version = {"old": OLD, "unknown": "future_unapproved", "unsupported": "r8m9n0o1p2q3"}[state]
            connection.execute(text("UPDATE alembic_version SET version_num=:v"), {"v": version})
        elif state == "empty":
            connection.execute(text("DELETE FROM alembic_version"))
        elif state == "multiple":
            connection.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": OLD})
        elif state == "family_index":
            connection.execute(text("DROP INDEX ix_teams_family_id"))
        elif state == "name_claims":
            connection.execute(text("DROP TABLE team_name_claims"))
        else:
            table = "contest_results" if state == "precise_score" else "contest_weeks"
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {state}"))
    reason = ("required_schema_incomplete" if state in {"precise_score", "practice_scoring_mode", "name_claims"}
              else "team_family_constraints_missing" if state == "family_index" else "revision_not_approved")
    assert_refused_without_writes(*db, reason)


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_real_previous_schema_requires_upgrade_then_plan_apply_repeat(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            bootstrap_canonical_seasons(session)
            source = session.scalar(select(m.Season).where(m.Season.key == lifecycle.SOURCE))
            profile = m.WoodchuckProfile(woodchuck_id="WC-COMPAT", display_name="Compatibility",
                pin_hash="test", instrument="Flute", level="Beginner", goal="Practice")
            session.add(profile)
            session.flush()
            team = make_team(session, season_id=source.id, display_name="Compatibility",
                normalized_name="compatibility", emblem_key="letter:C", creator_profile_id=profile.id)
            session.add(team)
            session.flush()
            session.add(m.TeamMembership(season_id=source.id, team_id=team.id, profile_id=profile.id,
                started_at=lifecycle.BOUNDARY - timedelta(days=7), selected_week_start=date(2026, 9, 21)))
            session.commit()
        # The current continuity code requires the claim table, even if empty.
        for model in (m.TeamNameClaim,):
            with engine.connect() as connection, pytest.raises(DBAPIError):
                connection.execute(select(model))
        assert_refused_without_writes(url, engine, "require d16team001; upgrade older schemas")
        end, deadline, finalize = contest_week_schedule(date(2026, 9, 14))
        with engine.begin() as connection:
            source_id = connection.scalar(select(m.Season.id).where(m.Season.key == lifecycle.SOURCE))
            connection.execute(m.ContestWeek.__table__.insert().values(
                season_id=source_id, week_start=date(2026, 9, 14), week_end=end,
                verification_deadline_at=deadline + timedelta(hours=1),
                finalize_after=finalize + timedelta(hours=1), status="finalized", finalized_at=finalize + timedelta(days=1),
                practice_scoring_mode="legacy_minutes"))
        before = snapshot(engine)
        command.upgrade(config, NEW)
        migrated = snapshot(engine)
        for table, rows in before.items():
            if table != "alembic_version":
                assert migrated[table] == rows
        with Session(engine) as session:
            week = session.scalar(select(m.ContestWeek))
            assert week.practice_scoring_mode == "legacy_minutes"
            assert week.verification_deadline_at == before["contest_weeks"][0]._mapping["verification_deadline_at"]
        assert provisioning.provision(url, **PAIR)["missing"] == 2
        assert snapshot(engine) == migrated
        assert provisioning.provision(url, apply=True, **PAIR)["created"] == 2
        assert activation.preflight(url, **PAIR, now=lifecycle.BOUNDARY)["status"] == "READY"
        activated = activation.activate(url, **PAIR, now=lifecycle.BOUNDARY)
        assert activated["verification"]["passed"]
        assert (activated["teams_created"], activated["memberships_created"]) == (1, 1)
        after = snapshot(engine)
        assert provisioning.provision(url, apply=True, **PAIR)["status"] == "ALREADY_COMPLETE"
        assert provisioning.provision(url, **PAIR)["missing"] == 0
        assert activation.activate(url, **PAIR, now=lifecycle.BOUNDARY)["status"] == "ALREADY_COMPLETE"
        assert snapshot(engine) == after
        assert all(row in after["contest_weeks"] for row in migrated["contest_weeks"])
    finally:
        engine.dispose()


@pytest.mark.parametrize("field", ["precise_score", "practice_scoring_mode"])
def test_precision_history_is_verified_and_invalidates_approved_plan(tmp_path, field):
    url = f"sqlite:///{tmp_path / 'history.db'}"
    engine = history.seed_database(url)
    try:
        history.apply((url, engine), history.plan((url, engine)))
        approved = history.plan((url, engine))
        table = "contest_weeks" if field == "practice_scoring_mode" else "contest_results"
        with engine.begin() as connection:
            assert field in repair.Snapshot(connection).rows(table)[0]
            connection.execute(text(f"UPDATE {table} SET {field}=:value"),
                               {"value": "precise_seconds" if field == "practice_scoring_mode" else 40.25})
        current = history.plan((url, engine))
        with pytest.raises(repair.RepairError, match="verification_history_or_preconditions_changed"):
            repair.verify_content(approved["content"], current["content"])
        before = history.full_snapshot(engine)
        with pytest.raises(repair.RepairError, match="stale_plan"):
            history.apply((url, engine), approved)
        assert history.full_snapshot(engine) == before
    finally:
        engine.dispose()
