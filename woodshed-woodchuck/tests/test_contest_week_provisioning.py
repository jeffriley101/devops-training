"""Calendar-only writes and lifecycle integration on disposable databases."""
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app import contest_jobs, contest_week_provisioning as job, models as m
from app import season_team_activation as activation, team_continuity_repair as repair
from app.contests import aware_utc, contest_week_schedule, finalize_contest_week
from app.db import Base
from app.seasons import bootstrap_canonical_seasons
from tests import test_season_team_activation as lifecycle
from tests import test_team_continuity_repair as history
from tests.test_team_continuity_postgres import ordered_race
from tests.test_team_families import disposable_url

PAIR = dict(source=lifecycle.SOURCE, destination=lifecycle.DEST)


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request, tmp_path):
    url = disposable_url(tmp_path, request.param)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=OFF")  # Disposable fixture only.
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        c.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": repair.REVISION})
    with Session(engine) as s:
        bootstrap_canonical_seasons(s)
        s.commit()
    yield url, engine
    engine.dispose()


def add_week(db, key, start, **changes):
    end, deadline, finalize = contest_week_schedule(start)
    with Session(db[1]) as s:
        season = s.scalar(select(m.Season).where(m.Season.key == key))
        values = dict(season_id=season.id, week_start=start, week_end=end,
                      verification_deadline_at=deadline, finalize_after=finalize, status="open")
        values.update(changes)
        row = m.ContestWeek(**values)
        s.add(row)
        s.flush()
        wid = row.id
        s.commit()
        return wid


def test_readonly_plan_and_missing_apply_repeat(db, monkeypatch):
    original = repair.inventory.readonly_connection
    @contextmanager
    def checked(url):
        with original(url) as c:
            repair.inventory.verify_readonly(c)
            yield c
    monkeypatch.setattr(repair.inventory, "readonly_connection", checked)
    before = history.full_snapshot(db[1])
    raw = Path(db[1].url.database).read_bytes() if db[1].dialect.name == "sqlite" else None
    plan = job.provision(db[0], **PAIR)
    assert plan["missing"] == 2 and plan["created"] == 0
    assert plan["boundary"] == "2026-09-28T05:00:00+00:00"
    assert [w["week_start"] for w in plan["weeks"]] == ["2026-09-21", "2026-09-28"]
    assert history.full_snapshot(db[1]) == before
    if raw is not None:
        assert Path(db[1].url.database).read_bytes() == raw
    applied = job.provision(db[0], apply=True, **PAIR)
    assert applied["created"] == 2 and applied["missing"] == 0
    assert {w["action"] for w in applied["weeks"]} == {"created"}
    after = history.full_snapshot(db[1])
    for table in before:
        if table != "contest_weeks":
            assert before[table] == after[table], table
    repeat = job.provision(db[0], apply=True, **PAIR)
    assert repeat["status"] == "ALREADY_COMPLETE" and repeat["created"] == 0
    assert history.full_snapshot(db[1]) == after


def test_matching_frozen_history_and_nonstandard_deadlines_unchanged(db):
    deadline = lifecycle.DUE + timedelta(days=2)
    add_week(db, lifecycle.SOURCE, date(2026, 9, 21), verification_deadline_at=deadline,
             finalize_after=deadline + timedelta(hours=1), status="finalized", finalized_at=deadline + timedelta(days=1))
    before = history.full_snapshot(db[1])
    report = job.provision(db[0], apply=True, **PAIR)
    assert report["created"] == 1 and report["unchanged"] == 1
    assert report["weeks"][0]["stored_deadlines_differ"]
    after = history.full_snapshot(db[1])
    assert after["contest_weeks"][0] == before["contest_weeks"][0]


@pytest.mark.parametrize("change", ["wrong_owner", "overlap", "wrong_end", "inverted", "duplicate_other_season"])
def test_conflicts_refuse_whole_batch(db, change):
    start = date(2026, 9, 28)
    owner = lifecycle.DEST
    values = {}
    if change in {"wrong_owner", "duplicate_other_season"}:
        owner = "holiday-2026"
    if change == "overlap":
        start -= timedelta(days=7)
        values["week_end"] = date(2026, 10, 1)
    if change == "wrong_end":
        values["week_end"] = date(2026, 10, 6)
    if change == "inverted":
        values["week_end"] = start - timedelta(days=1)
    if change == "duplicate_other_season":
        add_week(db, lifecycle.DEST, start)
    add_week(db, owner, start, **values)
    before = history.full_snapshot(db[1])
    assert job.provision(db[0], **PAIR)["status"] == "BLOCKED"
    assert job.provision(db[0], apply=True, **PAIR)["status"] == "BLOCKED"
    assert history.full_snapshot(db[1]) == before


@pytest.mark.parametrize("change", ["missing", "dates", "timezone", "overlap"])
def test_season_prerequisites_atomic(db, change):
    with Session(db[1]) as s:
        row = s.scalar(select(m.Season).where(m.Season.key == lifecycle.DEST))
        if change == "missing": s.delete(row)
        if change == "dates": row.ends_on -= timedelta(days=7)
        if change == "timezone": row.timezone = "America/New_York"
        if change == "overlap":
            s.add(m.Season(key="overlap", name="Overlap", starts_on=date(2026, 9, 29), ends_on=date(2026, 10, 2), status="planned"))
        s.commit()
    before = history.full_snapshot(db[1])
    assert job.provision(db[0], apply=True, **PAIR)["status"] == "BLOCKED"
    assert history.full_snapshot(db[1]) == before


def test_all_season_weeks_dst_and_adjacent_seasons(db):
    keys = ["halloween-2026", "holiday-2026", "hibernaculum-2027", "spring-2027"]
    report = job.provision(db[0], apply=True, seasons=keys)
    assert report["created"] == 32
    with Session(db[1]) as s:
        weeks = {w.week_start: w for w in s.scalars(select(m.ContestWeek))}
        assert weeks[date(2026, 10, 26)].week_end == date(2026, 11, 2)
        assert aware_utc(weeks[date(2026, 10, 26)].verification_deadline_at).hour == 18
        assert aware_utc(weeks[date(2027, 3, 8)].verification_deadline_at).hour == 17
        assert aware_utc(weeks[date(2027, 3, 1)].verification_deadline_at).hour == 18
        ordered = sorted(weeks.values(), key=lambda w: w.week_start)
        assert all(a.week_end == b.week_start for a, b in zip(ordered, ordered[1:]))
    assert job.provision(db[0], apply=True, seasons=keys)["created"] == 0


@pytest.mark.parametrize("arguments", [{}, {"seasons": ["unknown"]}, {"seasons": ["band-camp-2027"]},
    {"source": "back-to-school-2026", "destination": "holiday-2026"}, {"seasons": ["halloween-2026"], **PAIR}])
def test_explicit_bounded_scope(db, arguments):
    before = history.full_snapshot(db[1])
    with pytest.raises(repair.RepairError): job.provision(db[0], apply=True, **arguments)
    assert history.full_snapshot(db[1]) == before


def test_bounded_transition_into_open_ended_season(db):
    report = job.provision(db[0], apply=True, source="beach-2027", destination="band-camp-2027")
    assert report["created"] == 2
    assert report["weeks"][-1]["week_end"] == "2027-07-12"


def test_concurrent_provisioners_replan_after_lock(db, monkeypatch):
    def attempt(): return job.provision(db[0], apply=True, **PAIR)["created"]
    assert ordered_race(monkeypatch, attempt, attempt) == (2, 0)
    assert len(history.full_snapshot(db[1])["contest_weeks"]) == 2


def test_failure_after_first_insert_rolls_back(db):
    before = history.full_snapshot(db[1])
    def fail(mapper, connection, target):
        raise RuntimeError("controlled failure after SQL insert")
    event.listen(m.ContestWeek, "after_insert", fail)
    try:
        with pytest.raises(RuntimeError): job.provision(db[0], apply=True, **PAIR)
    finally:
        event.remove(m.ContestWeek, "after_insert", fail)
    assert history.full_snapshot(db[1]) == before
    assert job.provision(db[0], apply=True, **PAIR)["created"] == 2


def test_stale_plan_conflict_revalidated(db):
    assert job.provision(db[0], **PAIR)["status"] == "READY"
    add_week(db, "holiday-2026", date(2026, 9, 28))
    before = history.full_snapshot(db[1])
    assert job.provision(db[0], apply=True, **PAIR)["status"] == "BLOCKED"
    assert history.full_snapshot(db[1]) == before


def test_cli_scope_opt_in_and_safe_errors(db, monkeypatch, capsys):
    command = ["provision_weeks", "--source-season", lifecycle.SOURCE, "--destination-season", lifecycle.DEST]
    monkeypatch.setenv("DATABASE_URL", db[0])
    assert contest_jobs.main(command) == 0
    assert not history.full_snapshot(db[1])["contest_weeks"]
    assert contest_jobs.main(command + ["--apply"]) == 0
    assert contest_jobs.main(command + ["--season", lifecycle.DEST]) == 1
    monkeypatch.delenv("DATABASE_URL")
    assert contest_jobs.main(command) == 1
    capsys.readouterr()
    def failed(*a, **kw): raise RuntimeError("postgresql://user:SECRET@host/db")
    monkeypatch.setattr(job, "provision", failed)
    assert contest_jobs.main(command) == 1
    assert "SECRET" not in capsys.readouterr().err


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("scoring_mode", ["legacy_minutes", "precise_seconds"])
def test_provision_preflight_activate_later_source_finalization(tmp_path, backend, scoring_mode):
    url = disposable_url(tmp_path, backend)
    engine = lifecycle.seed(url)
    try:
        with Session(engine) as s:
            s.get(m.Season, 1).name = "Back to School"
            s.get(m.Season, 2).name = "Halloween"
            s.get(m.ContestWeek, 6).practice_scoring_mode = scoring_mode
            if scoring_mode == "precise_seconds":
                s.scalar(select(m.ContestResult)).precise_score = 40.25
            # The activation fixture's older frozen weeks stay outside this
            # explicitly bounded transition and must remain byte-for-byte intact.
            s.delete(s.get(m.ContestWeek, 7))
            s.delete(s.get(m.ContestWeek, 8))
            s.commit()
        before = history.full_snapshot(engine)
        assert activation.preflight(url, **PAIR, now=lifecycle.BOUNDARY - timedelta(days=1))["status"] == "NOT_READY"
        assert job.provision(url, **PAIR)["missing"] == 2
        assert history.full_snapshot(engine) == before
        assert job.provision(url, apply=True, **PAIR)["created"] == 2
        after = history.full_snapshot(engine)
        for table in before:
            if table == "contest_weeks":
                assert all(row in after[table] for row in before[table])
            else:
                assert before[table] == after[table], table
        assert activation.preflight(url, **PAIR, now=lifecycle.BOUNDARY - timedelta(days=1))["status"] == "READY"
        assert activation.activate(url, **PAIR, now=lifecycle.BOUNDARY - timedelta(seconds=1))["status"] == "NOT_DUE"
        activated = activation.activate(url, **PAIR, now=lifecycle.BOUNDARY)
        assert (activated["teams_created"], activated["memberships_created"]) == (4, 11)
        activated_history = history.full_snapshot(engine)
        for table in before:
            if table not in {"teams", "team_memberships"}:
                assert activated_history[table] == after[table], table
        assert activation.activate(url, **PAIR, now=lifecycle.BOUNDARY)["status"] == "ALREADY_COMPLETE"
        assert history.full_snapshot(engine) == activated_history
        with Session(engine) as s:
            source = finalize_contest_week(s, week_start=date(2026, 9, 21), now=lifecycle.DUE + timedelta(seconds=1))
            s.commit()
            assert source.practice_scoring_mode == "precise_seconds"
            snapshots = list(s.scalars(select(m.TeamWeekMembershipSnapshot).where(m.TeamWeekMembershipSnapshot.contest_week_id == source.id)))
            assert len(snapshots) == 11 and {r.team_id for r in snapshots} == {1, 6, 8, 9}
            dest = s.scalar(select(m.ContestWeek).where(m.ContestWeek.week_start == date(2026, 9, 28)))
            assert dest.status == "open"
            assert not s.scalar(select(m.ContestResult.id).where(m.ContestResult.contest_week_id == dest.id))
        frozen = history.full_snapshot(engine)
        assert len(frozen["reward_grants"]) > len(before["reward_grants"])
        assert job.provision(url, apply=True, **PAIR)["created"] == 0
        assert history.full_snapshot(engine) == frozen
    finally:
        engine.dispose()


def test_postgres_calendar_lock_blocks_uncoordinated_cross_season_insert(db, monkeypatch):
    if db[1].dialect.name != "postgresql":
        pytest.skip("PostgreSQL table-lock contract")
    from sqlalchemy.exc import DBAPIError
    original = job.build_plan
    attempted = []
    def checked(session, **arguments):
        if not attempted:
            attempted.append(True)
            with Session(db[1]) as other:
                other.execute(text("SET LOCAL lock_timeout='100ms'"))
                season = other.scalar(select(m.Season).where(m.Season.key == "holiday-2026"))
                end, deadline, finalize = contest_week_schedule(date(2026, 9, 28))
                other.add(m.ContestWeek(season_id=season.id, week_start=date(2026, 9, 28),
                                       week_end=end, verification_deadline_at=deadline,
                                       finalize_after=finalize, status="open"))
                with pytest.raises(DBAPIError) as exc:
                    other.flush()
                assert exc.value.orig.sqlstate == "55P03"
                other.rollback()
        return original(session, **arguments)
    monkeypatch.setattr(job, "build_plan", checked)
    assert job.provision(db[0], apply=True, **PAIR)["created"] == 2
    assert attempted == [True]


def test_full_season_reports_misplaced_history(db):
    add_week(db, lifecycle.DEST, date(2026, 11, 2), status="finalized", finalized_at=lifecycle.DUE)
    before = history.full_snapshot(db[1])
    report = job.provision(db[0], apply=True, seasons=[lifecycle.DEST])
    assert report["status"] == "BLOCKED"
    assert "weeks_outside_canonical_season" in {c["reason"] for c in report["conflicts"]}
    assert history.full_snapshot(db[1]) == before


def test_inverted_midweek_row_is_not_ignored(db):
    add_week(db, lifecycle.DEST, date(2026, 9, 28), week_start=date(2026, 9, 29), week_end=date(2026, 9, 28))
    assert job.provision(db[0], apply=True, **PAIR)["status"] == "BLOCKED"
