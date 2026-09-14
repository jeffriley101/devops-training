"""H2B maintenance contract, exercised only against disposable databases."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app import models as m, team_continuity as domain, team_continuity_repair as repair
from app.contests import contest_week_schedule, ensure_contest_definitions, finalize_contest_week
from app.db import Base
from tests.team_factory import make_team

BOUNDARY = datetime(2026, 9, 14, 5, tzinfo=timezone.utc)
NOW = BOUNDARY + timedelta(hours=4)
OLD = BOUNDARY - timedelta(days=4)
DUE = datetime(2026, 9, 14, 17, 5, tzinfo=timezone.utc)
SOURCE, DEST = "band-camp-2026", "back-to-school-2026"
SECRET = "DO_NOT_EXPORT_private_notes_PIN"
ROSTERS = {1: [(15, 34)], 6: [(4, 25), (6, 21), (13, 31)],
           8: [(5, 28), (8, 4), (10, 5)], 9: [(9, 22), (12, 18), (14, 24), (16, 26)]}


def seed_database(url):
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        def configure(c, _):
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=OFF")  # Disposable fixture setup only.
        event.listen(engine, "connect", configure)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        c.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": repair.REVISION})
    with Session(engine, expire_on_commit=False) as s:
        for sid, key, start, end in ((1, SOURCE, date(2026, 7, 27), date(2026, 9, 13)),
                                    (2, DEST, date(2026, 9, 14), date(2026, 9, 27))):
            s.add(m.Season(id=sid, key=key, name=key, starts_on=start, ends_on=end,
                          timezone="America/Chicago", status="active", created_at=OLD, updated_at=OLD))
        for pid in (4, 5, 18, 21, 22, 24, 25, 26, 28, 31, 34, 99):
            s.add(m.WoodchuckProfile(id=pid, woodchuck_id=f"WC-H2B-{pid}", display_name=SECRET,
                  pin_hash=SECRET, instrument="Flute", level="Beginner", goal=SECRET))
        s.flush()
        for tid, name, emblem, creator in ((1, "Eureka", "emoji:cat", 4), (6, "Union", "letter:U", 26),
                                           (8, "The Teachers", "emoji:bee", 28), (9, "St. Louis", "emoji:dragon", 22)):
            s.add(m.TeamFamily(id=tid, created_at=OLD))
            s.flush()
            s.add(m.Team(id=tid, family_id=tid, season_id=1, display_name=name,
                         normalized_name=name.casefold(), emblem_key=emblem, creator_profile_id=creator, created_at=OLD))
        s.flush()
        for tid, roster in ROSTERS.items():
            for mid, pid in roster:
                s.add(m.TeamMembership(id=mid, season_id=1, team_id=tid, profile_id=pid,
                       started_at=OLD, selected_week_start=date(2026, 9, 7)))
        for mid, tid, pid in ((1, 1, 4), (2, 1, 5), (3, 6, 26), (7, 6, 5), (11, 6, 18)):
            s.add(m.TeamMembership(id=mid, season_id=1, team_id=tid, profile_id=pid,
                   started_at=OLD - timedelta(days=8), ended_at=OLD - timedelta(days=1), selected_week_start=date(2026, 8, 31)))
        for wid, sid, start in ((6, 1, date(2026, 8, 31)), (7, 1, date(2026, 9, 7)),
                                (8, 2, date(2026, 9, 14)), (9, 2, date(2026, 9, 21))):
            end, deadline, final = contest_week_schedule(start)
            s.add(m.ContestWeek(id=wid, season_id=sid, week_start=start, week_end=end,
                  verification_deadline_at=deadline, finalize_after=final,
                  status="finalized" if wid == 6 else "open", finalized_at=OLD if wid == 6 else None))
        s.flush()
        definitions = ensure_contest_definitions(s)
        contest = next(c for c in definitions if c.key == "team-weekly-practice")
        s.add_all([
            m.PracticeChart(id=53, profile_id=26, practice_date=date(2026, 9, 10), team_id=9,
                            include_contests=True, include_team_contests=True, minutes=40, instrument="Flute",
                            practice_details=[], note=SECRET, created_at=OLD),
            m.PracticeChart(id=54, profile_id=26, practice_date=date(2026, 9, 14), team_id=None,
                            include_team_contests=False, minutes=20, instrument="Flute", practice_details=[], note=SECRET),
            m.TeamReport(team_id=8, reporter_profile_id=4, category="other", status="dismissed", details=SECRET),
            m.TeamJoinRequest(team_id=8, profile_id=99, season_id=1, status="rejected"),
        ])
        for aid, pid in ((442, 4), (443, 26), (444, 26)):
            s.add(m.CampPointAward(id=aid, profile_id=pid, activity_type="care", points_awarded=1,
                                  occurred_at=BOUNDARY + timedelta(hours=1), duplicate_key=f"fixture:{aid}", team_id=None))
        historical = m.ContestResult(contest_week_id=6, contest_id=contest.id, subject_type="team", subject_key="9",
            team_id=9, display_name_snapshot="St. Louis", division="open", score=40, rank=1, medal="gold")
        s.add(historical); s.flush()
        s.add(m.TeamWeekMembershipSnapshot(contest_week_id=6, team_id=9, profile_id=26, snapshot_at=OLD))
        s.add(m.RewardGrant(contest_result_id=historical.id, profile_id=26, source_key=SECRET, reward_type="crown_win", amount=1))
        s.add(m.CrownAward(profile_id=26, source_key=SECRET, category_key="practice", earned_at=OLD))
        s.commit()
    # Explicit IDs in the incident fixture must not leave PostgreSQL sequences behind.
    if engine.dialect.name == "postgresql":
        with engine.begin() as c:
            for table in ("teams", "team_families", "team_memberships", "seasons", "contest_weeks", "woodchuck_profiles", "practice_charts", "camp_point_awards"):
                c.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT MAX(id) FROM {table}), true)"))
    return engine


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'h2b.db'}"
    engine = seed_database(url)
    yield url, engine
    engine.dispose()


def plan(db, now=NOW):
    return repair.generate_plan(db[0], SOURCE, DEST, now=now)


def apply(db, approved, now=NOW, **kwargs):
    values = dict(acknowledgments={ack: True for ack in repair.ACKS}, confirmation=repair.CONFIRMATION, now=now)
    values.update(kwargs)
    return repair.apply_repair(db[0], approved, approved["plan_sha256"], **values)


def full_snapshot(engine):
    """Test-only full-column evidence includes private fields, never exported."""
    with engine.connect() as c:
        return {table.name: [dict(r) for r in c.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
                for table in Base.metadata.sorted_tables}


def destination_counts(engine):
    with engine.connect() as c:
        return tuple(c.scalar(text(f"SELECT count(*) FROM {name} WHERE season_id=2"))
                     for name in ("teams", "team_memberships"))


def test_production_shape_plan_apply_verify_idempotency(db):
    original = full_snapshot(db[1])
    approved = plan(db)
    summary = approved["content"]["summary"]
    assert (summary["teams_to_create"], summary["memberships_to_create"], summary["conflict_count"]) == (4, 11, 0)
    assert summary["review_count"] == 0
    assert approved["content"]["boundary"] == BOUNDARY.isoformat()
    assert approved["content"]["selected_week_start"] == "2026-09-14"
    result = apply(db, approved)
    assert result["transaction_state"] == "committed"
    assert result["verification"]["new_team_count"] == 4
    assert result["verification"]["new_membership_count"] == 11
    assert destination_counts(db[1]) == (4, 11)
    after = full_snapshot(db[1])
    for name, rows in original.items():
        assert (rows == [r for r in after[name] if r["season_id"] == 1]
                if name in ("teams", "team_memberships") else rows == after[name]), name
    assert repair.verify_repair(db[0], approved, approved["plan_sha256"], now=NOW)["verification"]["passed"]
    with pytest.raises(repair.RepairError, match="stale_plan"):
        apply(db, approved)  # Original creates plan cannot authorize changed state.
    zero = plan(db)
    assert zero["content"]["summary"]["teams_to_create"] == zero["content"]["summary"]["memberships_to_create"] == 0
    assert apply(db, zero)["outcome"] == "no_op"
    assert apply(db, zero)["outcome"] == "no_op"
    assert full_snapshot(db[1]) == after


def test_plan_readonly_sha_and_privacy(db):
    original = Path(db[1].url.database).read_bytes()
    first = plan(db)
    second = plan(db, now=NOW + timedelta(minutes=1))
    assert first["metadata"]["generated_at"] != second["metadata"]["generated_at"]
    assert first["plan_sha256"] == second["plan_sha256"]
    assert repair.digest(json.loads(json.dumps(first["content"]))) == first["plan_sha256"]
    assert SECRET not in json.dumps(first)
    assert Path(db[1].url.database).read_bytes() == original


@pytest.mark.parametrize("change", ["team", "membership", "moderation", "source_member", "family", "profile", "season", "chart"])
def test_stale_plan_zero_writes(db, change):
    approved = plan(db)
    with Session(db[1]) as s:
        if change in ("team", "membership"):
            team = make_team(s, season_id=2, display_name="New Choice", normalized_name="new choice", emblem_key="letter:X", creator_profile_id=99)
            s.add(team); s.flush()
            if change == "membership":
                s.add(m.TeamMembership(team_id=team.id, profile_id=26, season_id=2, started_at=BOUNDARY, selected_week_start=date(2026, 9, 14)))
        elif change == "moderation":
            s.get(m.Team, 8).moderation_status = "hidden"
        elif change == "source_member":
            s.get(m.TeamMembership, 16).started_at = OLD - timedelta(hours=1)
        elif change == "family":
            family = m.TeamFamily(); s.add(family); s.flush()
            s.get(m.Team, 8).family_id = family.id
        elif change == "profile":
            s.get(m.WoodchuckProfile, 26).status = "deleted"
        elif change == "season":
            s.get(m.Season, 2).name = "Changed"
        else:
            s.get(m.PracticeChart, 54).minutes += 1
        s.commit()
    before = full_snapshot(db[1])
    with pytest.raises(repair.RepairError):
        apply(db, approved)
    assert full_snapshot(db[1]) == before


@pytest.mark.parametrize("kind", ["status", "timestamp", "result", "snapshot"])
def test_destination_freeze_refuses(db, kind):
    approved = plan(db)
    with Session(db[1]) as s:
        if kind == "status":
            s.get(m.ContestWeek, 8).status = "finalized"
        elif kind == "timestamp":
            s.get(m.ContestWeek, 8).finalized_at = NOW
        elif kind == "result":
            result = s.scalar(select(m.ContestResult))
            s.add(m.ContestResult(contest_week_id=8, contest_id=result.contest_id, subject_type="student", subject_key="26",
                  profile_id=26, display_name_snapshot=SECRET, division="open", score=1, rank=1, medal="gold"))
        else:
            s.add(m.TeamWeekMembershipSnapshot(contest_week_id=8, profile_id=26, team_id=None, snapshot_at=NOW))
        s.commit()
    before = full_snapshot(db[1])
    with pytest.raises(repair.RepairError):
        apply(db, approved)
    assert before == full_snapshot(db[1])


def test_stored_source_deadline_and_normal_finalization(db):
    approved = plan(db)
    with pytest.raises(repair.RepairError, match="preconditions_blocked"):
        apply(db, approved, now=DUE)
    assert destination_counts(db[1]) == (0, 0)
    with Session(db[1]) as s:
        finalize_contest_week(s, week_start=date(2026, 9, 7), now=DUE + timedelta(seconds=1)); s.commit()
        snapshots = list(s.scalars(select(m.TeamWeekMembershipSnapshot).where(m.TeamWeekMembershipSnapshot.contest_week_id == 7)))
        assert len(snapshots) == 11
        assert {row.team_id for row in snapshots} == {1, 6, 8, 9}
    assert destination_counts(db[1]) == (0, 0)
    refreshed = plan(db, now=DUE + timedelta(seconds=2))
    assert refreshed["content"]["summary"]["teams_to_create"] == 4
    assert apply(db, refreshed, now=DUE + timedelta(seconds=3))["verification"]["new_membership_count"] == 11


def test_deadline_crossing_before_commit_rolls_back(db):
    approved = plan(db)
    moments = iter([NOW, NOW, NOW, DUE])
    # build current, H1B now, build after, then final post-evidence guard.
    def times(now=None):
        return now if now is not None else next(moments)
    from unittest.mock import patch
    with patch.object(repair, "clock", times), pytest.raises(repair.RepairError):
        apply(db, approved, now=None)
    assert destination_counts(db[1]) == (0, 0)


@pytest.mark.parametrize("missing", repair.ACKS)
def test_ack_required_before_connection(db, missing, monkeypatch):
    approved = plan(db)
    ack = {key: True for key in repair.ACKS}; ack.pop(missing)
    monkeypatch.setattr(repair, "writer", lambda _: pytest.fail("unauthorized connection"))
    with pytest.raises(repair.RepairError):
        apply(db, approved, acknowledgments=ack)


@pytest.mark.parametrize("attack", ["hash", "missing_hash", "content", "rehashed_ids", "rehashed_seasons", "confirmation", "injected_reason"])
def test_plan_authorization_tampering(db, attack, capsys):
    approved = plan(db)
    args = dict(acknowledgments={k: True for k in repair.ACKS}, confirmation=repair.CONFIRMATION, now=NOW)
    expected = approved["plan_sha256"]
    if attack == "hash": expected = "0" * 64
    elif attack == "missing_hash": expected = None
    elif attack == "confirmation": args["confirmation"] = "yes"
    elif attack == "content": approved["content"]["actions"][0]["family_id"] = 99
    else:
        if attack == "rehashed_ids": approved["content"]["source"]["id"] = 99
        elif attack == "rehashed_seasons": approved["content"]["source"]["key"] = "absent"
        else: approved["content"]["preconditions"]["blockers"] = [SECRET]
        expected = approved["plan_sha256"] = repair.digest(approved["content"])
    before = full_snapshot(db[1])
    with pytest.raises(repair.RepairError) as exc:
        repair.apply_repair(db[0], approved, expected, **args)
    assert SECRET not in str(exc.value)
    assert full_snapshot(db[1]) == before


@pytest.mark.parametrize("failure", ["partial", "orm_history", "sql_history", "evidence"])
def test_failure_rolls_back_and_retry_works(db, monkeypatch, failure):
    approved = plan(db)
    original = domain.apply_team_continuity
    before = full_snapshot(db[1])
    def broken(session, **kwargs):
        result = original(session, **kwargs)
        assert session.scalar(text("SELECT count(*) FROM teams WHERE season_id=2")) == 4
        if failure == "orm_history":
            session.get(m.PracticeChart, 54).note = "BAD"; session.flush()
        elif failure == "sql_history":
            session.execute(text("UPDATE camp_point_awards SET team_id=1 WHERE id=442"))
        else:
            raise RuntimeError("forced partial repair")
        return result
    def failed_evidence(report):
        raise OSError("disk full")
    with monkeypatch.context() as patch:
        if failure != "evidence": patch.setattr(domain, "apply_team_continuity", broken)
        with pytest.raises((RuntimeError, repair.RepairError, OSError)):
            apply(db, approved, prepare=failed_evidence if failure == "evidence" else None)
    assert full_snapshot(db[1]) == before
    assert apply(db, approved)["verification"]["passed"]


@pytest.mark.parametrize("wrong", ["o5j6k7l8m9n0", "r8m9n0o1p2q3", "future_unapproved"])
def test_revision_guard(db, wrong):
    approved = plan(db)
    with db[1].begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num=:v"), {"v": wrong})
    for operation in (lambda: plan(db), lambda: apply(db, approved)):
        with pytest.raises(repair.RepairError, match="revision_not_approved"):
            operation()


def test_missing_family_index_guard(db):
    with db[1].begin() as c:
        c.execute(text("DROP INDEX ix_teams_family_id"))
    with pytest.raises(repair.RepairError, match="constraints_missing"):
        plan(db)


def test_pre_h1a_missing_family_fails_closed(tmp_path):
    url = f"sqlite:///{tmp_path / 'pre-h1a.db'}"
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        c.execute(text("INSERT INTO alembic_version VALUES ('o5j6k7l8m9n0')"))
        c.execute(text("CREATE TABLE teams (id INTEGER PRIMARY KEY)"))
    before = Path(engine.url.database).read_bytes()
    with pytest.raises(repair.RepairError, match="h1a_schema_required"):
        repair.generate_plan(url, SOURCE, DEST, now=NOW)
    assert Path(engine.url.database).read_bytes() == before
    engine.dispose()


@pytest.mark.parametrize("change", ["nonadjacent", "timezone", "overlap", "missing"])
def test_explicit_season_guards(db, change):
    if change == "missing":
        with pytest.raises(repair.RepairError):
            repair.generate_plan(db[0], "absent", DEST, now=NOW)
        return
    with Session(db[1]) as s:
        if change == "nonadjacent": s.get(m.Season, 2).starts_on += timedelta(days=1)
        elif change == "timezone": s.get(m.Season, 2).timezone = "UTC"
        else: s.add(m.Season(key="overlap", name="Overlap", starts_on=date(2026, 9, 14), ends_on=date(2026, 9, 27), status="planned"))
        s.commit()
    with pytest.raises(repair.RepairError): plan(db)


def cli_args(db, operation, output, approved_path=None, sha=None):
    args = [operation, "--database-url", db[0], "--output", str(output)]
    if operation == "plan":
        return args + ["--source-season", SOURCE, "--destination-season", DEST]
    args += ["--plan", str(approved_path), "--plan-sha256", sha]
    if operation == "apply":
        args += ["--apply", "--confirm", repair.CONFIRMATION] + ["--ack-" + a.replace("_", "-") for a in repair.ACKS]
    return args


def test_cli_plan_apply_verify_files(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(repair, "clock", lambda now=None: now or NOW)
    approved_path = tmp_path / "plan.json"
    original = Path(db[1].url.database).read_bytes()
    assert repair.main(cli_args(db, "plan", approved_path)) == 0
    assert Path(db[1].url.database).read_bytes() == original
    approved = repair.load_plan(approved_path)
    saved_plan = approved_path.read_bytes()
    assert repair.main(cli_args(db, "plan", approved_path)) == 1
    assert approved_path.read_bytes() == saved_plan
    apply_args = cli_args(db, "apply", tmp_path / "apply.json", approved_path, approved["plan_sha256"])
    assert repair.main(apply_args) == 0
    saved_apply = (tmp_path / "apply.json").read_bytes()
    assert repair.main(apply_args) == 1
    assert (tmp_path / "apply.json").read_bytes() == saved_apply
    before_verify = Path(db[1].url.database).read_bytes()
    verify_path = tmp_path / "verify.json"
    args = cli_args(db, "verify", verify_path, approved_path, approved["plan_sha256"])
    assert repair.main(args) == 0
    assert Path(db[1].url.database).read_bytes() == before_verify
    saved = verify_path.read_bytes()
    assert repair.main(args) == 1
    assert verify_path.read_bytes() == saved
    for name in ("plan.json", "apply.json", "verify.json"):
        assert ((tmp_path / name).stat().st_mode & 0o777) == 0o600
        assert SECRET not in (tmp_path / name).read_text()
    captured = capsys.readouterr()
    assert "PLAN SHA-256" in captured.out and SECRET not in captured.out + captured.err


@pytest.mark.parametrize("missing", ["--apply", "--plan-sha256", "--ack-backup-taken", "--ack-maintenance-mode", "--ack-writers-paused", "--ack-finalization-paused"])
def test_cli_missing_authorization_never_connects(tmp_path, monkeypatch, missing):
    fake = ("postgresql://u:SECRETPASSWORD@127.0.0.1/test", None)
    args = cli_args(fake, "apply", tmp_path / "out.json", tmp_path / "plan.json", "0" * 64)
    index = args.index(missing)
    del args[index:index + (2 if missing == "--plan-sha256" else 1)]
    monkeypatch.setattr(repair, "writer", lambda _: pytest.fail("unauthorized connection"))
    with pytest.raises(SystemExit) as exc: repair.main(args)
    assert exc.value.code == 2
    assert not (tmp_path / "out.json").exists()


def test_cli_defaults_driver_normalization_and_redaction(tmp_path, monkeypatch, capsys):
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg://"):
        url = repair.inventory.target_url(prefix + "u:SECRETPASSWORD@127.0.0.1/test?sslmode=require")
        assert url.drivername == "postgresql+psycopg"
        assert url.query["sslmode"] == "require"
        assert "SECRETPASSWORD" not in json.dumps(repair.inventory.target_metadata(url))
    with pytest.raises(SystemExit): repair.main([])
    def fail(*args, **kwargs): raise RuntimeError("postgresql://u:SECRETPASSWORD@host/db")
    monkeypatch.setattr(repair, "generate_plan", fail)
    assert repair.main(["plan", "--database-url", "postgresql://u:SECRETPASSWORD@host/db", "--source-season", SOURCE,
                        "--destination-season", DEST, "--output", str(tmp_path / "error.json")]) == 1
    captured = capsys.readouterr()
    assert "SECRETPASSWORD" not in captured.out + captured.err
    result = subprocess.run([".venv/bin/python", "-m", "app.team_continuity_repair", "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    for path in ("app/main.py", "app/teams.py", "app/contests.py", "app/seasons.py"):
        assert "team_continuity_repair" not in Path(path).read_text()


def test_private_engine_behavior_and_secret_not_exported(db):
    with Session(db[1]) as s:
        team = s.get(m.Team, 1)
        team.visibility = "private"; team.director_led = True; team.join_code = "PRIVATECODE1"
        s.add(m.ProfileCapability(profile_id=4, capability="band_director")); s.commit()
    approved = plan(db)
    assert "PRIVATECODE1" not in json.dumps(approved)
    assert apply(db, approved)["verification"]["passed"]
    with Session(db[1]) as s:
        successor = s.scalar(select(m.Team).where(m.Team.season_id == 2, m.Team.family_id == 1))
        assert successor.join_code and successor.join_code != "PRIVATECODE1"


def test_opening_week_correction_unchanged(db):
    from app.teams import active_membership, select_team
    apply(db, plan(db))
    with Session(db[1]) as s:
        student, dest = s.get(m.WoodchuckProfile, 26), s.get(m.Season, 2)
        initial = active_membership(s, profile_id=26, season_id=2)
        target = s.scalar(select(m.Team).where(m.Team.season_id == 2, m.Team.family_id == 8))
        assert initial is not None
        select_team(s, profile=student, season=dest, team=target, now=NOW); s.commit()
        old_team = s.get(m.Team, initial.team_id)
        with pytest.raises(ValueError): select_team(s, profile=student, season=dest, team=old_team, now=NOW)
        s.rollback()
        select_team(s, profile=student, season=dest, team=old_team, now=NOW + timedelta(days=7)); s.commit()


def test_final_evidence_failure_reports_committed(db, tmp_path, monkeypatch, capsys):
    from contextlib import contextmanager
    monkeypatch.setattr(repair, "clock", lambda now=None: now or NOW)
    approved = plan(db)
    plan_path = tmp_path / "plan.json"
    with repair.exclusive_report(plan_path) as save: save(approved)
    original = repair.exclusive_report
    @contextmanager
    def fail_final(path):
        with original(path) as save:
            def faulty(report):
                if report.get("transaction_state") == "committed": raise OSError("disk full")
                save(report)
            yield faulty
    monkeypatch.setattr(repair, "exclusive_report", fail_final)
    output = tmp_path / "apply.json"
    assert repair.main(cli_args(db, "apply", output, plan_path, approved["plan_sha256"])) == 1
    assert "DATABASE COMMITTED" in capsys.readouterr().err
    assert json.loads(output.read_text())["transaction_state"] == "prepared_not_committed"
    assert destination_counts(db[1]) == (4, 11)
    assert repair.verify_repair(db[0], approved, approved["plan_sha256"], now=NOW)["verification"]["passed"]
