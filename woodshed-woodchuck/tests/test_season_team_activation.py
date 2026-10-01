"""Boundary jobs use isolated fixtures only; full-row snapshots stay test-local."""
from datetime import date, timedelta
from pathlib import Path
import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import models as m, season_team_activation as job, team_continuity as domain
from app import team_continuity_repair as repair, contest_jobs, teams
from app.contests import finalize_contest_week
from tests import test_team_continuity_repair as base
from tests.team_factory import make_team

BOUNDARY = base.BOUNDARY + timedelta(days=14)
DUE = base.DUE + timedelta(days=14)
SOURCE, DEST = "back-to-school-2026", "halloween-2026"


def seed(url):
    engine = base.seed_database(url)
    with Session(engine) as s:
        s.get(m.Season, 2).key = DEST
        s.flush()
        for season in s.scalars(select(m.Season)):
            season.starts_on += timedelta(days=14)
            season.ends_on += timedelta(days=14)
        s.get(m.Season, 1).starts_on = date(2026, 9, 14)
        s.get(m.Season, 1).key = SOURCE
        s.get(m.Season, 2).key = DEST
        s.get(m.Season, 2).ends_on = date(2026, 11, 1)
        for w in s.scalars(select(m.ContestWeek).order_by(m.ContestWeek.week_start.desc())).all():
            for field in ("week_start", "week_end", "verification_deadline_at", "finalize_after"):
                setattr(w, field, getattr(w, field) + timedelta(days=14))
            s.flush()
        for member in s.scalars(select(m.TeamMembership)):
            member.started_at += timedelta(days=14)
            member.selected_week_start += timedelta(days=14)
            if member.ended_at:
                member.ended_at += timedelta(days=14)
        for chart in s.scalars(select(m.PracticeChart)):
            chart.practice_date += timedelta(days=14)
        s.commit()
    return engine


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'activation.db'}"
    engine = seed(url)
    yield url, engine
    engine.dispose()


def activate(db, now=BOUNDARY):
    return job.activate(db[0], source=SOURCE, destination=DEST, now=now)


def test_readonly_database_enforcement(db, monkeypatch):
    from contextlib import contextmanager
    original = repair.inventory.readonly_connection
    @contextmanager
    def checked(url):
        with original(url) as c:
            assert c.scalar(text("PRAGMA query_only")) == 1
            yield c
    monkeypatch.setattr(repair.inventory, "readonly_connection", checked)
    before = Path(db[1].url.database).read_bytes()
    job.preflight(db[0], now=BOUNDARY - timedelta(hours=1))
    assert before == Path(db[1].url.database).read_bytes()


def test_no_transition_and_no_runtime_import(db):
    assert job.preflight(db[0], now=BOUNDARY + timedelta(days=1))["status"] == "NO_TRANSITION"
    root = Path(__file__).resolve().parents[1]
    for path in ("app/main.py", "app/teams.py", "app/seasons.py"):
        assert "season_team_activation" not in (root / path).read_text()


def test_preflight_does_not_authorize_changed_state(db):
    assert job.preflight(db[0], now=BOUNDARY - timedelta(seconds=1))["status"] == "READY"
    with Session(db[1]) as s:
        s.get(m.Team, 6).moderation_status = "hidden"
        s.commit()
    before = base.full_snapshot(db[1])
    assert activate(db)["status"] == "NOT_READY"
    assert base.full_snapshot(db[1]) == before


def test_unprovisioned_destination_week_fails_closed(db):
    # Canonical seasons can exist ahead of their lazily created contest weeks.
    with Session(db[1]) as s:
        s.delete(s.get(m.ContestWeek, 8))
        s.commit()
    before = base.full_snapshot(db[1])
    report = job.preflight(db[0], now=BOUNDARY - timedelta(days=1))
    assert report["status"] == "NOT_READY"
    assert "destination_first_week_not_open" in report["reason_codes"]
    assert activate(db)["status"] == "NOT_READY"
    assert before == base.full_snapshot(db[1])


@pytest.mark.parametrize("now", [BOUNDARY - timedelta(days=1), BOUNDARY, BOUNDARY + timedelta(days=3)])
def test_seasonal_activation_is_retired_without_copying(db, now):
    before = base.full_snapshot(db[1])
    report = activate(db, now)
    assert report["status"] == "NOT_READY"
    assert report["reason_codes"] == ["seasonal_team_activation_retired"]
    assert base.full_snapshot(db[1]) == before


def test_retired_activation_never_connects_to_target(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Retired activation must not connect")
    monkeypatch.setattr(repair, "writer", forbidden)
    monkeypatch.setattr(repair.inventory, "target_url", forbidden)
    assert job.activate("unused")["reason_codes"] == ["seasonal_team_activation_retired"]
    assert job.main(["team_activate"]) == 1
    assert contest_jobs.main(["team_activate", "--database-url", "unused"]) == 1


def test_calendar_labels_do_not_invoke_team_cutover(db):
    root = Path(__file__).resolve().parents[1]
    for path in ("app/main.py", "app/teams.py", "app/seasons.py", "app/contest_seasons.py"):
        source = (root / path).read_text()
        assert "apply_team_continuity" not in source
        assert "persistent_team_cutover" not in source
