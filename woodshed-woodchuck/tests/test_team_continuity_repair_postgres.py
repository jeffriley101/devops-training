"""H2B wrapper tests: local disposable PostgreSQL only; no production fallback."""
from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import models as m, teams, team_continuity_repair as repair
from tests.test_team_families import disposable_url
from tests.test_team_continuity_postgres import ordered_race
from tests import test_team_continuity_repair as spec
from tests.team_factory import make_team


@pytest.fixture
def pg(tmp_path):
    url = disposable_url(tmp_path, "postgresql")
    engine = spec.seed_database(url)
    yield url, engine
    engine.dispose()


def test_production_shape_atomic_apply_verify_and_noop(pg):
    # Runs the exact 4/11, all-table full-row preservation, original-plan stale
    # rejection, VERIFY, and repeated zero-create APPLY assertions on PostgreSQL.
    spec.test_production_shape_plan_apply_verify_idempotency(pg)


@pytest.mark.parametrize("change", ["team", "membership", "moderation", "source_member", "family"])
def test_stale_plan_before_writes(pg, change):
    spec.test_stale_plan_zero_writes(pg, change)


@pytest.mark.parametrize("kind", ["status", "timestamp", "result", "snapshot"])
def test_destination_freeze(pg, kind):
    spec.test_destination_freeze_refuses(pg, kind)


@pytest.mark.parametrize("failure", ["partial", "orm_history", "sql_history", "evidence"])
def test_rollback_then_clean_retry(pg, monkeypatch, failure):
    spec.test_failure_rolls_back_and_retry_works(pg, monkeypatch, failure)


def test_due_source_then_existing_normal_finalizer(pg):
    spec.test_stored_source_deadline_and_normal_finalization(pg)


def test_postgres_readonly_plan_verify_and_plain_render_url(pg, monkeypatch):
    url = pg[0].replace("postgresql+psycopg://", "postgresql://")
    with repair.inventory.readonly_connection(url) as c:
        assert c.scalar(text("SHOW transaction_read_only")) == "on"
        assert c.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        with pytest.raises(DBAPIError) as exc:
            c.execute(text("UPDATE teams SET display_name='forbidden' WHERE id=1"))
        assert exc.value.orig.sqlstate == "25006"
    original = repair.inventory.readonly_connection
    snapshots = []
    @contextmanager
    def checked(value):
        with original(value) as c:
            assert c.scalar(text("SHOW transaction_read_only")) == "on"
            assert c.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            snapshots.append(True)
            yield c
    monkeypatch.setattr(repair.inventory, "readonly_connection", checked)
    before = spec.full_snapshot(pg[1])
    approved = repair.generate_plan(url, spec.SOURCE, spec.DEST, now=spec.NOW)
    later = repair.generate_plan(url, spec.SOURCE, spec.DEST, now=spec.NOW + timedelta(minutes=1))
    assert approved["plan_sha256"] == later["plan_sha256"]
    assert spec.full_snapshot(pg[1]) == before
    spec.apply((url, pg[1]), approved)
    before = spec.full_snapshot(pg[1])
    assert repair.verify_repair(url, approved, approved["plan_sha256"], now=spec.NOW)["verification"]["passed"]
    assert spec.full_snapshot(pg[1]) == before and len(snapshots) == 3


@pytest.mark.parametrize("competitor", ["apply", "creation", "join"])
@pytest.mark.parametrize("repair_first", [True, False])
def test_wrapper_enters_h1b_locks_both_orderings(pg, monkeypatch, competitor, repair_first):
    target_id = None
    if competitor == "join":
        with Session(pg[1]) as s:
            target = make_team(s, season_id=2, display_name="New Choice", normalized_name="new choice",
                               emblem_key="letter:X", creator_profile_id=99)
            s.add(target); s.flush(); target_id = target.id; s.commit()
    approved = spec.plan(pg)
    original_source = [row for row in spec.full_snapshot(pg[1])["team_memberships"] if row["season_id"] == 1]
    def attempt():
        try:
            return spec.apply(pg, approved)["outcome"]
        except repair.RepairError:
            return "blocked"
    def other():
        if competitor == "apply":
            return attempt()
        with Session(pg[1]) as s:
            profile, season = s.get(m.WoodchuckProfile, 26), s.get(m.Season, 2)
            if competitor == "creation":
                try:
                    teams.create_and_join_team(s, profile=profile, season=season, name="Student Choice",
                                               emblem_key="letter:Y", now=spec.NOW)
                    return "created"
                except ValueError:
                    s.rollback(); return "blocked"
            teams.select_team(s, profile=profile, season=season, team=s.get(m.Team, target_id), now=spec.NOW)
            s.commit(); return "joined"
    outcomes = (ordered_race(monkeypatch, attempt, other) if repair_first
                else ordered_race(monkeypatch, other, attempt))
    if competitor == "apply":
        assert outcomes == ("applied", "blocked")
        assert spec.destination_counts(pg[1]) == (4, 11)
    elif repair_first:
        assert outcomes[0] == "applied"
    else:
        assert outcomes[1] == "blocked"
        assert spec.destination_counts(pg[1]) == (1, 1)
    with pg[1].connect() as c:
        assert c.execute(text("SELECT family_id FROM teams WHERE season_id=2 GROUP BY family_id HAVING count(*)>1")).first() is None
        assert c.execute(text("SELECT profile_id FROM team_memberships WHERE season_id=2 AND ended_at IS NULL "
                              "GROUP BY profile_id HAVING count(*)>1")).first() is None
    assert [row for row in spec.full_snapshot(pg[1])["team_memberships"] if row["season_id"] == 1] == original_source


def test_opening_week_user_behavior(pg):
    spec.test_opening_week_correction_unchanged(pg)
