"""Opt-in loopback disposable PostgreSQL, using H1B's real lock race harness."""
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import models as m, teams, team_continuity_repair as repair
from app.contests import finalize_contest_week
from tests import test_season_team_activation as spec
from tests.test_team_families import disposable_url
from tests.test_team_continuity_postgres import ordered_race
from tests.team_factory import make_team


@pytest.fixture
def pg(tmp_path):
    url = disposable_url(tmp_path, "postgresql")
    engine = spec.seed(url)
    yield url, engine
    engine.dispose()


def test_canonical_and_history(pg):
    spec.test_canonical_preflight_boundary_apply_repeat(pg)


def test_source_finalization_after_activation(pg):
    spec.test_later_normal_source_finalization(pg)


def test_due_source_then_retry(pg):
    spec.test_due_source_refuses_then_finalizes(pg)


def test_rollback(pg, monkeypatch):
    spec.test_failure_rolls_back(pg, monkeypatch)


def test_preflight_database_readonly(pg, monkeypatch):
    original = repair.inventory.readonly_connection
    checked_count = []
    @contextmanager
    def checked(url):
        with original(url) as c:
            assert c.scalar(text("SHOW transaction_read_only")) == "on"
            assert c.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            checked_count.append(True)
            yield c
    monkeypatch.setattr(repair.inventory, "readonly_connection", checked)
    before = spec.base.full_snapshot(pg[1])
    assert spec.job.preflight(pg[0], now=spec.BOUNDARY - timedelta(hours=1))["status"] == "READY"
    assert len(checked_count) == 1 and spec.base.full_snapshot(pg[1]) == before
    with original(pg[0]) as c:
        with pytest.raises(DBAPIError) as exc:
            c.execute(text("UPDATE teams SET display_name='forbidden'"))
        assert exc.value.orig.sqlstate == "25006"


@pytest.mark.parametrize("competitor", ["activation", "creation", "join", "finalization"])
@pytest.mark.parametrize("activation_first", [True, False])
def test_serialized_workers(pg, monkeypatch, competitor, activation_first):
    target_id = None
    if competitor == "join":
        with Session(pg[1]) as s:
            target = make_team(s, season_id=2, display_name="Choice", normalized_name="choice",
                               emblem_key="letter:X", creator_profile_id=99)
            s.add(target); s.flush(); target_id = target.id; s.commit()
    now = spec.DUE + timedelta(seconds=1) if competitor == "finalization" else spec.BOUNDARY
    def activation():
        return spec.activate(pg, now)["status"]
    def other():
        if competitor == "activation":
            return activation()
        with Session(pg[1]) as s:
            if competitor == "finalization":
                finalize_contest_week(s, week_start=spec.date(2026, 9, 21), now=now)
                s.commit(); return "finalized"
            profile, season = s.get(m.WoodchuckProfile, 26), s.get(m.Season, 2)
            if competitor == "creation":
                try:
                    teams.create_and_join_team(s, profile=profile, season=season, name="Student Choice",
                                               emblem_key="letter:Y", now=now)
                    return "created"
                except ValueError:
                    s.rollback(); return "blocked"
            teams.select_team(s, profile=profile, season=season, team=s.get(m.Team, target_id), now=now)
            s.commit(); return "joined"
    outcomes = ordered_race(monkeypatch, activation, other) if activation_first else ordered_race(monkeypatch, other, activation)
    if competitor == "activation":
        assert outcomes == ("READY", "ALREADY_COMPLETE")
        assert spec.base.destination_counts(pg[1]) == (4, 11)
    elif competitor == "finalization":
        assert outcomes == (("NOT_READY", "finalized") if activation_first else ("finalized", "READY"))
    elif activation_first:
        assert outcomes[0] == "READY"
    else:
        assert outcomes[1] == "NOT_READY"
        assert spec.base.destination_counts(pg[1]) == (1, 1)
    with pg[1].connect() as c:
        assert c.execute(text("SELECT family_id FROM teams WHERE season_id=2 GROUP BY family_id HAVING count(*)>1")).first() is None
        assert c.execute(text("SELECT profile_id FROM team_memberships WHERE season_id=2 AND ended_at IS NULL GROUP BY profile_id HAVING count(*)>1")).first() is None


@pytest.mark.parametrize("kind", ["frozen", "result", "snapshot"])
def test_frozen_destination(pg, kind):
    spec.test_whole_transition_refused(pg, kind)
