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


def test_canonical_preflight_boundary_apply_repeat(db):
    before = base.full_snapshot(db[1])
    a = job.preflight(db[0], now=BOUNDARY - timedelta(days=1))
    b = job.preflight(db[0], now=BOUNDARY - timedelta(seconds=1))
    assert a == b
    assert a["status"] == "READY" and a["boundary"] == "2026-09-28T05:00:00+00:00"
    assert (a["source_team_count"], a["teams_to_create"], a["memberships_to_create"]) == (4, 4, 11)
    assert base.full_snapshot(db[1]) == before
    assert activate(db, BOUNDARY - timedelta(seconds=1))["status"] == "NOT_DUE"
    assert base.full_snapshot(db[1]) == before
    result = job.activate(db[0], now=BOUNDARY)
    assert (result["teams_created"], result["memberships_created"]) == (4, 11)
    assert result["verification"]["passed"]
    assert result["remaining_team_creates"] == result["remaining_membership_creates"] == 0
    after = base.full_snapshot(db[1])
    for table, rows in before.items():
        assert rows == ([r for r in after[table] if r["season_id"] == 1]
                        if table in ("teams", "team_memberships") else after[table]), table
    with Session(db[1]) as s:
        for member in s.scalars(select(m.TeamMembership).where(m.TeamMembership.season_id == 2)):
            old = next(r for r in before["team_memberships"] if r["profile_id"] == member.profile_id and r["ended_at"] is None)
            successor = s.get(m.Team, member.team_id)
            assert successor.id != old["team_id"]
            assert successor.family_id == s.get(m.Team, old["team_id"]).family_id
            assert repair.inventory.utc(member.started_at) == BOUNDARY
            assert member.selected_week_start == date(2026, 9, 28)
            assert teams.active_membership(s, profile_id=member.profile_id, season_id=2).team_id == successor.id
    repeat = activate(db)
    assert repeat["status"] == "ALREADY_COMPLETE"
    assert repeat["teams_created"] == repeat["memberships_created"] == 0
    assert base.full_snapshot(db[1]) == after


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


def test_midweek_and_dst(db):
    with Session(db[1]) as s:
        s.get(m.Season, 1).ends_on = date(2026, 9, 29)
        s.get(m.Season, 2).starts_on = date(2026, 9, 30)
        # Midweek synthetic source/destination weeks may share the containing Monday.
        s.get(m.ContestWeek, 7).week_end = date(2026, 9, 30)
        s.get(m.ContestWeek, 7).verification_deadline_at = BOUNDARY + timedelta(days=3)
        s.get(m.ContestWeek, 7).finalize_after = BOUNDARY + timedelta(days=3, minutes=5)
        s.commit()
    assert activate(db, BOUNDARY + timedelta(days=2))["memberships_created"] == 11
    with Session(db[1]) as s:
        assert {r.selected_week_start for r in s.scalars(select(m.TeamMembership).where(m.TeamMembership.season_id == 2))} == {date(2026, 9, 28)}
        winter = m.Season(starts_on=date(2026, 11, 2), timezone="America/Chicago")
        assert job.boundary(winter).hour == 6


def test_later_normal_source_finalization(db):
    activate(db)
    with Session(db[1]) as s:
        from app.age_privacy import declare_age
        for profile in s.scalars(select(m.WoodchuckProfile)).all():
            declare_age(s, profile.id, "adult", at=base.OLD)
        for chart in s.scalars(select(m.PracticeChart)).all():
            s.add(m.PracticeChartVerification(practice_chart_id=chart.id,
                status="approved", responded_at=DUE))
        finalize_contest_week(s, week_start=date(2026, 9, 21), now=DUE + timedelta(seconds=1))
        s.commit()
        snapshots = list(s.scalars(select(m.TeamWeekMembershipSnapshot).where(m.TeamWeekMembershipSnapshot.contest_week_id == 7)))
        assert len(snapshots) == 11
        assert {r.team_id for r in snapshots} == {1, 6, 8, 9}
        results = list(s.scalars(select(m.ContestResult).where(m.ContestResult.contest_week_id == 7)))
        assert results and all(r.team_id in {None, 1, 6, 8, 9} for r in results)
        assert s.scalar(select(m.RewardGrant.id).where(m.RewardGrant.contest_result_id.in_([r.id for r in results])))
        assert not s.scalar(select(m.ContestResult.id).where(m.ContestResult.contest_week_id == 8))
    assert base.destination_counts(db[1]) == (4, 11)


def test_due_source_refuses_then_finalizes(db):
    before = base.full_snapshot(db[1])
    response = activate(db, DUE)
    assert response["status"] == "NOT_READY"
    assert any(r.startswith("source_due_unfinalized") for r in response["reason_codes"])
    assert before == base.full_snapshot(db[1])
    with Session(db[1]) as s:
        finalize_contest_week(s, week_start=date(2026, 9, 21), now=DUE + timedelta(seconds=1)); s.commit()
    assert activate(db, DUE + timedelta(seconds=2))["teams_created"] == 4


@pytest.mark.parametrize("change", ["name", "emblem", "creator", "history", "successor", "hidden", "under_review", "overlap", "private", "frozen", "result", "snapshot"])
def test_whole_transition_refused(db, change):
    with Session(db[1]) as s:
        if change in {"name", "emblem", "creator", "history", "successor"}:
            source = s.get(m.Team, 1)
            target = make_team(s, season_id=2, display_name="Choice", normalized_name="choice", emblem_key="letter:X", creator_profile_id=99)
            if change == "name": target.normalized_name = source.normalized_name
            if change == "emblem": target.emblem_key = source.emblem_key
            if change == "creator": target.creator_profile_id = source.creator_profile_id
            if change == "successor": target.family_id = source.family_id
            s.add(target); s.flush()
            if change == "history":
                s.add(m.TeamMembership(season_id=2, team_id=target.id, profile_id=26, started_at=BOUNDARY,
                                      ended_at=BOUNDARY + timedelta(minutes=1), selected_week_start=BOUNDARY.date()))
        elif change in {"hidden", "under_review"}: s.get(m.Team, 1).moderation_status = change
        elif change == "private":
            t = s.get(m.Team, 1); t.visibility = "private"; t.director_led = True; t.join_code = "PRIVATE"
        elif change == "overlap":
            s.add(m.TeamMembership(season_id=1, team_id=6, profile_id=34, started_at=BOUNDARY - timedelta(days=1), ended_at=BOUNDARY + timedelta(days=1), selected_week_start=BOUNDARY.date()))
        elif change == "frozen": s.get(m.ContestWeek, 8).finalized_at = BOUNDARY
        elif change == "snapshot": s.add(m.TeamWeekMembershipSnapshot(contest_week_id=8, team_id=1, profile_id=34, snapshot_at=BOUNDARY))
        elif change == "result":
            old = s.scalar(select(m.ContestResult))
            s.add(m.ContestResult(contest_week_id=8, contest_id=old.contest_id, subject_type="team", subject_key="1", team_id=1, display_name_snapshot="Test", division="open", score=1, rank=1, medal="gold"))
        s.commit()
    before = base.full_snapshot(db[1])
    assert job.preflight(db[0], source=SOURCE, destination=DEST, now=BOUNDARY - timedelta(seconds=1))["status"] == "NOT_READY"
    assert activate(db)["status"] == "NOT_READY"
    assert before == base.full_snapshot(db[1])


@pytest.mark.parametrize("change", ["gap", "timezone", "overlap", "planned"])
def test_invalid_seasons(db, change):
    with Session(db[1]) as s:
        if change == "gap": s.get(m.Season, 1).ends_on -= timedelta(days=1)
        if change == "timezone": s.get(m.Season, 2).timezone = "America/New_York"
        if change == "planned": s.get(m.Season, 2).status = "planned"
        if change == "overlap": s.add(m.Season(key="ambiguous", name="ambiguous", starts_on=date(2026, 9, 27), ends_on=date(2026, 10, 1), status="active"))
        s.commit()
    before = base.full_snapshot(db[1])
    with pytest.raises(repair.RepairError): activate(db)
    assert before == base.full_snapshot(db[1])


def test_failure_rolls_back(db, monkeypatch):
    original = domain.apply_team_continuity
    def broken(*a, **kw):
        original(*a, **kw)
        raise RuntimeError("controlled failure after inserts")
    before = base.full_snapshot(db[1])
    monkeypatch.setattr(domain, "apply_team_continuity", broken)
    with pytest.raises(RuntimeError): activate(db)
    assert before == base.full_snapshot(db[1])
    monkeypatch.setattr(domain, "apply_team_continuity", original)
    assert activate(db)["teams_created"] == 4


def test_cli_explicit_job_and_safe_errors(db, monkeypatch, capsys):
    monkeypatch.setattr(repair, "clock", lambda now=None: BOUNDARY)
    assert contest_jobs.main(["team_preflight", "--database-url", db[0], "--source-season", SOURCE, "--destination-season", DEST]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "READY"
    assert base.destination_counts(db[1]) == (0, 0)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert job.main(["team_activate"]) == 1
    assert "password" not in capsys.readouterr().err


def test_no_transition_and_no_runtime_import(db):
    assert job.preflight(db[0], now=BOUNDARY + timedelta(days=1))["status"] == "NO_TRANSITION"
    root = Path(__file__).resolve().parents[1]
    for path in ("app/main.py", "app/teams.py", "app/seasons.py"):
        assert "season_team_activation" not in (root / path).read_text()


def test_opening_week_correction_after_scheduled_activation(db):
    activate(db)
    with Session(db[1]) as s:
        member = s.scalar(select(m.TeamMembership).where(m.TeamMembership.season_id == 2))
        original = s.get(m.Team, member.team_id)
        other = s.scalar(select(m.Team).where(m.Team.season_id == 2, m.Team.id != original.id))
        args = dict(profile=s.get(m.WoodchuckProfile, member.profile_id), season=s.get(m.Season, 2))
        payload = teams.selection_payload(s, profile=args["profile"], now=BOUNDARY)
        assert payload["season"]["key"] == DEST
        assert payload["membership"]["team"]["id"] == original.id
        assert payload["membership"]["correction_available"] is True
        assert "family_id" not in payload["membership"]["team"]
        teams.select_team(s, team=other, now=BOUNDARY + timedelta(hours=1), **args)
        s.commit()
        with pytest.raises(ValueError, match="locked"):
            teams.select_team(s, team=original, now=BOUNDARY + timedelta(hours=2), **args)
        s.rollback()
        teams.select_team(s, team=original, now=BOUNDARY + timedelta(days=7, hours=1), **args)
        s.commit()


def test_clock_crosses_deadline_during_apply_rolls_back(db, monkeypatch):
    moment = [DUE - timedelta(seconds=1)]
    monkeypatch.setattr(repair, "clock", lambda now=None: moment[0])
    original = domain.apply_team_continuity
    def crosses(*args, **kwargs):
        result = original(*args, **kwargs)
        moment[0] = DUE
        return result
    monkeypatch.setattr(domain, "apply_team_continuity", crosses)
    before = base.full_snapshot(db[1])
    with pytest.raises(repair.RepairError, match="source_due_unfinalized"):
        job.activate(db[0], source=SOURCE, destination=DEST)
    assert base.full_snapshot(db[1]) == before


def test_preflight_does_not_authorize_changed_state(db):
    assert job.preflight(db[0], now=BOUNDARY - timedelta(seconds=1))["status"] == "READY"
    with Session(db[1]) as s:
        s.get(m.Team, 6).moderation_status = "hidden"
        s.commit()
    before = base.full_snapshot(db[1])
    assert activate(db)["status"] == "NOT_READY"
    assert base.full_snapshot(db[1]) == before


def test_schema_guard_and_sanitized_cli_error(db, monkeypatch, capsys):
    with db[1].begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num='r8m9n0o1p2q3'"))
    before = base.full_snapshot(db[1])
    assert job.main(["team_activate", "--database-url", db[0], "--source-season", SOURCE,
                     "--destination-season", DEST]) == 1
    assert base.full_snapshot(db[1]) == before
    capsys.readouterr()
    def failed(*args, **kwargs):
        raise RuntimeError("postgresql://user:SECRET@host/db")
    monkeypatch.setattr(job, "activate", failed)
    assert job.main(["team_activate", "--database-url", db[0]]) == 1
    assert "SECRET" not in capsys.readouterr().err


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
