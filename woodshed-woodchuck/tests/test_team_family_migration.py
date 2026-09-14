"""H1A migration preserves historical Teams and every incoming FK domain."""
from datetime import date, timedelta
from importlib import import_module
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, Table, create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.models import (CampPointAward, Contest, ContestResult, ContestWeek,
    DirectorTeamContest, DirectorTeamContestEntry, DirectorTeamContestResult,
    PracticeChart, Season, TeamJoinRequest, TeamMembership, TeamReport,
    TeamWeekMembershipSnapshot, WoodchuckProfile)
from tests.test_team_families import NOW, disposable_url

OLD = "r8m9n0o1p2q3"
NEW = "s9n0o1p2q3r4"
HISTORY = ["teams", "team_memberships", "team_join_requests", "team_reports",
           "team_week_membership_snapshots", "practice_charts", "camp_point_awards",
           "contest_results", "director_team_contest_entries", "director_team_contest_results"]


def seed(engine):
    with engine.begin() as c:
        c.execute(WoodchuckProfile.__table__.insert().values(id=1, woodchuck_id="WC-HISTORY",
            display_name="History", pin_hash="test", instrument="Flute", level="Beginner", goal="Practice"))
        for number in (1, 2):
            c.execute(Season.__table__.insert().values(id=number, key=f"history-{number}",
                name=f"Season {number}", starts_on=date(2026, 7, 27) + timedelta(weeks=number-1),
                status="active" if number == 2 else "closed"))
        legacy = Table("teams", MetaData(), autoload_with=c)
        for team_id, season_id, state, private in [(10, 1, "active", False), (20, 2, "active", False),
                                                   (30, 1, "hidden", False), (40, 1, "under_review", True)]:
            similar = team_id in (10, 20)
            c.execute(legacy.insert().values(id=team_id, season_id=season_id,
                display_name="Same Team" if similar else f"Team {team_id}",
                normalized_name="same team" if similar else f"team {team_id}",
                emblem_key="emoji:cat" if similar else f"letter:{'H' if team_id == 30 else 'P'}",
                creator_profile_id=None if team_id == 30 else 1, visibility="private" if private else "public",
                director_led=private, join_code="HISTORY1" if private else None,
                moderation_status=state, moderation_updated_at=NOW, created_at=NOW - timedelta(days=team_id)))
        c.execute(TeamMembership.__table__.insert().values(id=1, season_id=1, team_id=10,
            profile_id=1, selected_week_start=date(2026, 7, 27), started_at=NOW))
        c.execute(TeamJoinRequest.__table__.insert().values(id=1, season_id=1, team_id=40,
            profile_id=1, status="pending"))
        c.execute(TeamReport.__table__.insert().values(id=1, team_id=30, reporter_profile_id=1,
            category="other", details="Preserved moderation"))
        c.execute(ContestWeek.__table__.insert().values(id=1, season_id=1, week_start=date(2026, 7, 27),
            week_end=date(2026, 8, 3), verification_deadline_at=NOW + timedelta(days=7),
            finalize_after=NOW + timedelta(days=8), status="finalized", finalized_at=NOW + timedelta(days=9)))
        c.execute(TeamWeekMembershipSnapshot.__table__.insert().values(id=1, contest_week_id=1,
            profile_id=1, team_id=10, membership_id=1, snapshot_at=NOW))
        c.execute(PracticeChart.__table__.insert().values(id=1, profile_id=1, practice_date=NOW.date(),
            minutes=30, instrument="Flute", team_id=10))
        c.execute(CampPointAward.__table__.insert().values(id=1, profile_id=1, activity_type="test",
            points_awarded=5, occurred_at=NOW, duplicate_key="history", team_id=10))
        c.execute(Contest.__table__.insert().values(id=1, key="historical-team", name="Historical",
            metric_type="practice_minutes", subject_type="team"))
        c.execute(ContestResult.__table__.insert().values(id=1, contest_week_id=1, contest_id=1,
            division="open", subject_type="team", subject_key="10", team_id=10,
            display_name_snapshot="Same Team", score=30, rank=1, medal="gold"))
        c.execute(DirectorTeamContest.__table__.insert().values(id=1, season_id=1, owner_profile_id=1,
            title="Private history", metric="total_minutes", starts_at=NOW, ends_at=NOW + timedelta(days=1),
            finalizes_at=NOW + timedelta(days=2), status="finalized", finalized_at=NOW + timedelta(days=3)))
        c.execute(DirectorTeamContestEntry.__table__.insert().values(id=1, contest_id=1, team_id=40))
        c.execute(DirectorTeamContestResult.__table__.insert().values(id=1, contest_id=1, team_id=40,
            team_name_snapshot="Team 40", emblem_key_snapshot="letter:P", score=50, rank=1))


def snapshot(engine):
    with engine.connect() as c:
        return {name: [dict(row) for row in c.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings()]
                for name in HISTORY}


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_team_family_upgrade_history_constraints_and_guard(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    @event.listens_for(engine, "connect")
    def fk_on(connection, _):
        if backend == "sqlite":
            connection.execute("PRAGMA foreign_keys=ON")
    seed(engine)
    before = snapshot(engine)
    old_indexes = {i["name"] for i in inspect(engine).get_indexes("teams") if not i.get("duplicates_constraint")}
    old_uniques = {i["name"] for i in inspect(engine).get_unique_constraints("teams")}
    old_checks = {i["name"]: i["sqltext"] for i in inspect(engine).get_check_constraints("teams")}
    command.upgrade(config, NEW)
    after = snapshot(engine)
    assert len({row.pop("family_id") for row in after["teams"]}) == len(before["teams"])
    assert after == before
    with engine.connect() as c:
        assert c.scalar(text("SELECT COUNT(*) FROM team_families")) == 4
        assert c.scalar(text("SELECT COUNT(*) FROM teams WHERE family_id IS NULL")) == 0
        assert c.scalar(text("SELECT COUNT(*) FROM teams t JOIN team_families f ON t.family_id=f.id "
                            "WHERE t.created_at = f.created_at")) == 4
        ctx = MigrationContext.configure(c, opts={"include_object":
            lambda obj, name, type_, reflected, compare_to:
                name in {"teams", "team_families"} if type_ == "table" else True})
        assert compare_metadata(ctx, Base.metadata) == []
    inspector = inspect(engine)
    assert {i["name"] for i in inspector.get_indexes("teams") if not i.get("duplicates_constraint")} == old_indexes | {"ix_teams_family_id"}
    assert {i["name"] for i in inspector.get_unique_constraints("teams")} == old_uniques | {"uq_team_season_family"}
    assert {i["name"]: i["sqltext"] for i in inspector.get_check_constraints("teams")} == old_checks
    fk = next(f for f in inspector.get_foreign_keys("teams") if f["name"] == "fk_teams_family_id_team_families")
    assert fk["options"]["ondelete"] == "RESTRICT"
    assert fk["constrained_columns"] == ["family_id"] and fk["referred_table"] == "team_families"
    for sql in (
        "UPDATE teams SET family_id = NULL WHERE id = 10",
        "UPDATE teams SET family_id = 999999 WHERE id = 10",
        "UPDATE teams SET family_id = (SELECT family_id FROM teams WHERE id = 10) WHERE id = 30",
        "DELETE FROM team_families WHERE id = (SELECT family_id FROM teams WHERE id = 10)",
    ):
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(text(sql))
    command.downgrade(config, OLD)
    assert snapshot(engine) == before
    assert "team_families" not in inspect(engine).get_table_names()
    command.upgrade(config, NEW)
    with engine.begin() as c:
        c.execute(text("UPDATE teams SET family_id = (SELECT family_id FROM teams WHERE id = 10) WHERE id = 20"))
    continued = snapshot(engine)
    with pytest.raises(RuntimeError, match="destroy cross-season TeamFamily continuity"):
        command.downgrade(config, OLD)
    assert snapshot(engine) == continued
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == NEW
    engine.dispose()


def test_sqlite_fk_enabled_migration_connection_refuses_before_ddl(tmp_path, monkeypatch):
    url = disposable_url(tmp_path, "sqlite")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    migration = import_module("migrations.versions.s9n0o1p2q3r4_add_team_families")
    with engine.connect() as c:
        c.execute(text("PRAGMA foreign_keys=ON"))
        monkeypatch.setattr(migration.op, "get_bind", lambda: c)
        with pytest.raises(RuntimeError, match="dedicated SQLite"):
            migration.upgrade()
        assert "team_families" not in inspect(c).get_table_names()
        assert "family_id" not in {column["name"] for column in inspect(c).get_columns("teams")}
    engine.dispose()


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_failed_upgrade_rolls_back_schema_and_history(tmp_path, monkeypatch, backend):
    url = disposable_url(tmp_path, backend)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, OLD)
    engine = create_engine(url)
    seed(engine)
    before = snapshot(engine)
    migration = import_module("migrations.versions.s9n0o1p2q3r4_add_team_families")
    def fail_after_rebuild(connection):
        raise RuntimeError("simulated failure after constraint creation")
    monkeypatch.setattr(migration, "_check_sqlite_foreign_keys", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="simulated failure"), engine.begin() as c:
        with Operations.context(MigrationContext.configure(c)):
            migration.upgrade()
    assert snapshot(engine) == before
    assert "team_families" not in inspect(engine).get_table_names()
    assert "family_id" not in {column["name"] for column in inspect(engine).get_columns("teams")}
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == OLD
    engine.dispose()
