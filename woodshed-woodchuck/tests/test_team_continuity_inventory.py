"""H2A uses actual disposable schemas and read-only connections, never production."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, Table, create_engine, event, select, text
from sqlalchemy.exc import DBAPIError

from app import models
from app.db import Base
from app import team_continuity_inventory as inv
from tests.test_team_families import disposable_url

NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
OLD = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
SECRET = "SHOULD_NOT_APPEAR_credential_notes_codes"


def seed(engine):
    with engine.begin() as c:
        for pid in range(1, 21):
            c.execute(models.WoodchuckProfile.__table__.insert().values(
                id=pid, woodchuck_id=f"WC-INV-{pid}", display_name=SECRET, pin_hash=SECRET,
                instrument="Flute", level="Beginner", goal=SECRET))
        for sid, key, start, end in ((1, inv.KEYS[0], inv.SOURCE_START, inv.SOURCE_END),
                                    (2, inv.KEYS[1], inv.DEST_START, inv.DEST_END)):
            c.execute(models.Season.__table__.insert().values(id=sid, key=key, name=key,
                starts_on=start, ends_on=end, timezone="America/Chicago", status="active"))
        data = []
        for tid in range(1, 13):
            data.append(dict(id=tid, season_id=1, display_name=f"Team {tid}",
                normalized_name=f"team {tid}", emblem_key=f"letter:{chr(64+tid)}", creator_profile_id=tid,
                moderation_status="hidden" if tid == 3 else "under_review" if tid == 4 else "active",
                visibility="private" if tid in (5, 6) else "public", director_led=tid in (5, 6),
                join_code=SECRET + str(tid) if tid in (5, 6) else None))
        # Private join codes have a 16-char bound on PostgreSQL.
        data[4]["join_code"], data[5]["join_code"] = "SECRET_CODE_5", "SECRET_CODE_6"
        data += [
            dict(id=22, season_id=2, display_name="Team 2", normalized_name="team 2", emblem_key="letter:B", creator_profile_id=2),
            dict(id=23, season_id=2, display_name="Collision", normalized_name="team 10", emblem_key="letter:X", creator_profile_id=16),
            dict(id=24, season_id=2, display_name="Other", normalized_name="other", emblem_key="letter:K", creator_profile_id=17),
            dict(id=25, season_id=2, display_name="New choice", normalized_name="new choice", emblem_key="letter:Y", creator_profile_id=12),
        ]
        for row in data:
            c.execute(models.TeamFamily.__table__.insert().values(id=row["id"], created_at=OLD))
            c.execute(models.Team.__table__.insert().values(**row, family_id=row["id"], created_at=OLD))
        mid = 0
        def membership(tid, pid, start=OLD, end=None, season=1):
            nonlocal mid
            mid += 1
            c.execute(models.TeamMembership.__table__.insert().values(id=mid, team_id=tid, profile_id=pid,
                season_id=season, started_at=start, ended_at=end,
                selected_week_start=date(2026, 9, 7) if season == 1 else inv.DEST_START))
        for tid in (1, 2, 3, 4, 8, 9, 10, 11, 12):
            membership(tid, tid, end=inv.BOUNDARY if tid == 8 else None)
        membership(9, 9, start=OLD - timedelta(hours=1), end=inv.BOUNDARY + timedelta(hours=1))
        membership(1, 13, end=OLD + timedelta(hours=1))
        membership(2, 14)
        membership(22, 2, start=inv.BOUNDARY, season=2)
        membership(22, 14, start=inv.BOUNDARY, end=inv.BOUNDARY + timedelta(hours=1), season=2)
        c.execute(models.ProfileCapability.__table__.insert().values(profile_id=5, capability="band_director"))
        c.execute(models.TeamJoinRequest.__table__.insert().values(id=1, team_id=5, profile_id=15, season_id=1, status="pending"))
        c.execute(models.TeamReport.__table__.insert().values(id=1, team_id=3, reporter_profile_id=2,
            status="unresolved", category="other", details=SECRET))
        for chart_id, pdate, team in ((1, inv.DEST_START, None), (2, inv.DEST_START, 2), (3, inv.SOURCE_END, 2)):
            c.execute(models.PracticeChart.__table__.insert().values(id=chart_id, profile_id=2,
                practice_date=pdate, minutes=10, instrument="Flute", team_id=team, note=SECRET,
                practice_details=[SECRET], created_at=NOW))
        for aid, team in ((1, None), (2, 2), (3, 22)):
            c.execute(models.CampPointAward.__table__.insert().values(id=aid, profile_id=2, activity_type="test",
                points_awarded=1, occurred_at=NOW, team_id=team, duplicate_key=f"inventory-{aid}"))
        for wid, sid, start in ((1, 1, date(2026, 9, 7)), (2, 2, inv.DEST_START)):
            c.execute(models.ContestWeek.__table__.insert().values(id=wid, season_id=sid, week_start=start,
                week_end=start+timedelta(days=7), verification_deadline_at=NOW, finalize_after=NOW, status="open"))
        c.execute(models.Contest.__table__.insert().values(id=1, key="test-contest", name="Test",
            metric_type="practice_minutes", subject_type="team"))
        c.execute(models.DirectorTeamContest.__table__.insert().values(id=1, season_id=1,
            owner_profile_id=5, title=SECRET, description=SECRET, metric="total_minutes",
            starts_at=OLD, ends_at=OLD+timedelta(hours=1), finalizes_at=OLD+timedelta(hours=2), status="finalized", finalized_at=OLD+timedelta(hours=2)))
        c.execute(models.DirectorTeamContestEntry.__table__.insert().values(id=1, contest_id=1, team_id=5))
        c.execute(models.DirectorTeamContestResult.__table__.insert().values(id=1, contest_id=1, team_id=5,
            team_name_snapshot=SECRET, emblem_key_snapshot="letter:E", score=1, rank=1))


def make_database(url, mode):
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def disposable_performance(c, _):
            c.execute("PRAGMA synchronous=OFF")
    Base.metadata.create_all(engine)
    seed(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        if mode == "pre":
            # Model the pre-family schema, before public name claims existed.
            models.TeamNameClaim.__table__.drop(c)
            from importlib import import_module
            migration = import_module("migrations.versions.s9n0o1p2q3r4_add_team_families")
            with Operations.context(MigrationContext.configure(c)):
                migration.downgrade()
        c.execute(text("INSERT INTO alembic_version VALUES (:revision)"),
                  {"revision": "r8m9n0o1p2q3" if mode == "pre" else "s9n0o1p2q3r4"})
    engine.dispose()
    return url


@pytest.fixture(params=["pre", "post"])
def database(tmp_path, request):
    return make_database(f"sqlite:///{tmp_path / 'inventory.db'}", request.param), request.param


def report(database):
    return inv.inventory(database[0], now=NOW, commit="test-commit")


def test_schema_compatibility_and_unchanged_database(database):
    url, mode = database
    path = Path(inv.target_url(url).database)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = report(database)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert result["schema"]["schema_mode"] == ("pre_team_family" if mode == "pre" else "team_family_available")
    assert result["schema"]["tables"]["team_families"]["present"] == (mode == "post")
    assert ("family_id" in result["source_teams"][0]) == (mode == "post")
    assert result["summary_counts"]["inventory_complete"] is True
    assert result["metadata"]["boundary_utc"] == "2026-09-14T05:00:00+00:00"
    assert not result["seasons"]["validation_reasons"]


def test_actual_pre_h1a_migration_schema(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'actual_pre_h1a.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "r8m9n0o1p2q3")
    result = inv.inventory(url, now=NOW)
    assert result["schema"]["schema_mode"] == "pre_team_family"
    assert "family_id" not in result["schema"]["tables"]["teams"]["columns"]


def test_boundary_and_destination_history(database):
    r = report(database)
    assert r["summary_counts"]["active_boundary_memberships"] == 10
    assert r["summary_counts"]["students_with_destination_membership_history"] == 2
    exact = next(m for m in r["member_classifications"] if m["profile_id"] == 8)
    assert exact["classification"] == "REVIEW" and "exact_boundary_ending" in exact["reasons"]
    assert all("overlapping_source_memberships" in m["reasons"]
               for m in r["member_classifications"] if m["profile_id"] == 9)
    for pid in (2, 14):
        action = next(m for m in r["member_classifications"] if m["profile_id"] == pid)
        assert action["classification"] == "CONFLICT"
        assert "destination_membership_history" in action["reasons"]
        activity = next(a for a in r["destination_activity"] if a["profile_id"] == pid)
        assert len(activity["memberships"]) == 1
        assert (activity["memberships"][0]["ended_at"] is not None) == (pid == 14)
    old = next(m for m in r["member_classifications"] if m["profile_id"] == 13)
    assert old["classification"] == "INFORMATIONAL"


def test_collisions_and_candidates_are_diagnostics(database):
    r = report(database)
    manual = next(c for c in r["collisions"] if c["source_team_id"] == 2)
    assert manual["classification"] == "REVIEW"
    assert manual["label"] == "POSSIBLE MANUAL SUCCESSOR — REVIEW REQUIRED"
    for tid, reason in ((10, "name_collision"), (11, "emblem_collision"), (12, "public_creator_collision")):
        assert any(c["source_team_id"] == tid and reason in c["reasons"] and c["classification"] == "CONFLICT"
                   for c in r["collisions"])
    candidate = next(t for t in r["team_classifications"] if t["team_id"] == 1)
    assert candidate["classification"] == "SAFE_CANDIDATE"
    assert r["summary_counts"]["repair_authorized"] is False


def test_moderation_private_requests_and_redaction(database):
    r = report(database)
    assert r["moderation"]["teams"] == [3, 4]
    assert all(m["classification"] == "REVIEW" and "source_not_active" in m["reasons"]
               for m in r["member_classifications"] if m["team_id"] in (3, 4))
    assert r["pending_requests"][0]["team_id"] == 5
    assert r["pending_requests"][0]["classification"] == "REVIEW"
    invalid = next(t for t in r["team_classifications"] if t["team_id"] == 6)
    assert "private_owner_ineligible_or_unknown" in invalid["reasons"]
    assert next(p for p in r["private_director_teams"] if p["team_id"] == 5)["band_director_capability"] is True
    assert all(t["join_code_present"] for t in r["source_teams"] if t["visibility"] == "private")
    encoded = json.dumps(r)
    for sensitive in (SECRET, "SECRET_CODE_5", "SECRET_CODE_6"):
        assert sensitive not in encoded
    assert "details" not in r["moderation"]["reports"][0]


def test_attribution_inventory(database):
    r = report(database)
    charts = {c["id"]: c for c in r["chart_attribution"]}
    assert "null_team_attribution" in charts[1]["reasons"]
    assert "cross_season_team_attribution" in charts[2]["reasons"]
    assert "late_submitted_source_chart" in charts[3]["reasons"]
    awards = {a["id"]: a for a in r["award_attribution"]}
    assert "null_team_attribution" in awards[1]["reasons"]
    assert "cross_season_team_attribution" in awards[2]["reasons"]
    assert awards[3]["reasons"] == []
    assert charts[1]["include_team_contests"] is True


def test_director_and_frozen_complications(database):
    engine = create_engine(database[0])
    with engine.begin() as c:
        c.execute(models.DirectorTeamContest.__table__.update().values(ends_at=inv.BOUNDARY, finalizes_at=NOW))
        c.execute(models.ContestWeek.__table__.update().where(models.ContestWeek.id == 2).values(status="finalized", finalized_at=NOW))
        c.execute(models.ContestResult.__table__.insert().values(id=1, contest_week_id=2, contest_id=1,
            subject_type="team", subject_key="22", team_id=22, display_name_snapshot=SECRET,
            division="open", score=1, rank=1, medal="gold"))
        c.execute(models.TeamWeekMembershipSnapshot.__table__.insert().values(id=1, contest_week_id=2,
            team_id=22, profile_id=2, snapshot_at=NOW))
        c.execute(models.RewardGrant.__table__.insert().values(contest_result_id=1, profile_id=2,
            source_key=SECRET, reward_type="crown_win", amount=1))
        c.execute(models.CrownAward.__table__.insert().values(profile_id=2, source_key=SECRET,
            category_key="practice", earned_at=NOW))
        c.execute(models.CrownAward.__table__.insert().values(profile_id=3, source_key=SECRET,
            category_key="practice", earned_at=NOW))
    engine.dispose()
    r = report(database)
    assert r["director_contests"][0]["touches_boundary"] is True
    assert r["summary_counts"]["destination_finalized_weeks"] == 1
    assert r["summary_counts"]["destination_frozen_artifacts"] >= 3
    assert r["weeks"][1]["artifact_counts"] == {"contest_results": 1, "team_week_membership_snapshots": 1}
    assert all("destination_frozen" in t["reasons"] for t in r["team_classifications"])
    assert all("director_contest_review" in t["reasons"] for t in r["team_classifications"])
    assert any(d["table"] == "crown_awards" and d["destination"] for d in r["frozen_dependencies"])
    assert not any(d["table"] == "crown_awards" and d["profile_id"] == 3 for d in r["frozen_dependencies"])
    assert SECRET not in json.dumps(r)


def test_same_family_mismatch_post_schema(database):
    if database[1] == "pre":
        pytest.skip("family reference is intentionally absent")
    engine = create_engine(database[0])
    with engine.begin() as c:
        c.execute(models.Team.__table__.update().where(models.Team.id == 22).values(family_id=2, display_name="Contradiction"))
    engine.dispose()
    r = report(database)
    c = next(c for c in r["collisions"] if c["source_team_id"] == 2)
    assert "same_family_successor_mismatch" in c["reasons"] and c["classification"] == "CONFLICT"


def test_optional_absence_is_unknown_and_required_absence_fails(database):
    engine = create_engine(database[0])
    with engine.begin() as c:
        c.execute(text("DROP TABLE team_reports"))
    r = report(database)
    assert r["summary_counts"]["inventory_complete"] is False
    assert "team_reports" in r["schema"]["unavailable_sections"]
    assert r["source_teams"][0]["counts"]["unresolved_reports"] is None
    assert all(t["classification"] != "SAFE_CANDIDATE" for t in r["team_classifications"])
    with engine.begin() as c:
        c.execute(text("DROP TABLE team_memberships"))
    engine.dispose()
    with pytest.raises(inv.InventoryError, match="Required pre-H1A schema missing"):
        report(database)


def test_json_determinism_summary_cli_and_exclusive_output(database, tmp_path, capsys):
    first = report(database)
    assert first == report(database)
    summary = inv.human_summary(first)
    for label in ("Band Camp Teams: 12", "Back-to-School Teams: 4", "SAFE_CANDIDATE", "REVIEW", "CONFLICT",
                  "Pending source requests: 1", "Source private/director Teams: 2", "NO REPAIR WAS PERFORMED."):
        assert label in summary
    output = tmp_path / "result.json"
    assert inv.main(["--database-url", database[0], "--output", str(output)]) == 0
    assert json.loads(output.read_text())["schema"]["schema_mode"] == first["schema"]["schema_mode"]
    before = output.read_bytes()
    assert inv.main(["--database-url", database[0], "--output", str(output)]) == 1
    assert output.read_bytes() == before
    assert (output.stat().st_mode & 0o777) == 0o600
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_sqlite_actually_rejects_write(database):
    with inv.readonly_connection(database[0]) as c:
        assert c.scalar(text("PRAGMA query_only")) == 1
        with pytest.raises(DBAPIError):
            c.execute(text("UPDATE teams SET display_name = 'forbidden' WHERE id=1"))


@pytest.mark.parametrize("flag", ["--apply", "--repair", "--write"])
def test_cli_has_no_write_option_and_hides_unknown_credentials(flag, capsys):
    with pytest.raises(SystemExit) as e:
        inv.main([flag, "postgresql://owner:SECRETPASS@localhost/db"])
    assert e.value.code == 2
    captured = capsys.readouterr()
    assert "SECRETPASS" not in captured.out + captured.err


def test_target_and_driver_failure_never_print_password(monkeypatch, capsys):
    def fail(*a, **k):
        raise RuntimeError("postgresql://owner:SECRETPASS@localhost/db")
    monkeypatch.setattr(inv, "inventory", fail)
    assert inv.main(["--database-url", "postgresql://owner:SECRETPASS@localhost/db"]) == 1
    captured = capsys.readouterr()
    assert "SECRETPASS" not in captured.out + captured.err
    assert "localhost" in captured.err
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert inv.main([]) == 1


def test_help_import_does_not_load_app_models_or_offer_repair():
    import subprocess
    result = subprocess.run([".venv/bin/python", "-c",
        "import sys; import app.team_continuity_inventory; "
        "assert 'app.db' not in sys.modules; assert 'app.models' not in sys.modules; "
        "assert 'app.team_continuity' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([".venv/bin/python", "-m", "app.team_continuity_inventory", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert "--apply" not in result.stdout and "--repair" not in result.stdout


@pytest.mark.parametrize("mode", ["pre", "post"])
def test_disposable_postgres_readonly_and_compatibility(tmp_path, mode):
    url = make_database(disposable_url(tmp_path, "postgresql"), mode)
    with inv.readonly_connection(url) as c:
        assert c.scalar(text("SHOW transaction_read_only")) == "on"
        assert c.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        before = c.scalar(text("SELECT display_name FROM teams WHERE id=1"))
        with pytest.raises(DBAPIError) as exc:
            c.execute(text("UPDATE teams SET display_name = 'forbidden' WHERE id=1"))
        assert exc.value.orig.sqlstate == "25006"
    result = inv.inventory(url, now=NOW)
    assert result["schema"]["schema_mode"] == ("pre_team_family" if mode == "pre" else "team_family_available")
    assert result["source_teams"][0]["display_name"] == before
    assert result["metadata"]["read_only"] is True


def test_inventory_rejects_readwrite_connection(database):
    engine = create_engine(database[0])
    with engine.connect() as c:
        with pytest.raises(inv.InventoryError, match="read-only snapshot"):
            inv.build_report(c)
    engine.dispose()


def test_optional_column_absence_does_not_guess_late_charts(database):
    engine = create_engine(database[0])
    with engine.begin() as c:
        c.execute(text("ALTER TABLE practice_charts DROP COLUMN created_at"))
    engine.dispose()
    r = report(database)
    assert "created_at" in r["schema"]["tables"]["practice_charts"]["unavailable_columns"]
    assert r["summary_counts"]["inventory_complete"] is False
    assert all(not c["late_submitted_source_chart"] for c in r["chart_attribution"])


def test_inconsistent_membership_season_blocks_candidates(database):
    engine = create_engine(database[0])
    with engine.begin() as c:
        c.execute(models.TeamMembership.__table__.update().where(models.TeamMembership.profile_id == 13).values(season_id=2))
    engine.dispose()
    r = report(database)
    assert r["integrity_issues"][0]["reason"] == "season_team_reference_mismatch"
    assert all(t["classification"] == "CONFLICT" for t in r["team_classifications"])


def test_sqlite_missing_file_or_mode_override_fails_closed(tmp_path):
    path = tmp_path / "must_not_create.db"
    with pytest.raises(FileNotFoundError):
        with inv.readonly_connection(f"sqlite:///{path}"):
            pytest.fail("missing database was opened")
    assert not path.exists()
    with pytest.raises(inv.InventoryError):
        with inv.readonly_connection(f"sqlite:///{path}?mode=rw"):
            pytest.fail("read/write mode was accepted")


def test_optional_timezone_is_unavailable_instead_of_assumed(database):
    engine = create_engine(database[0])
    with engine.begin() as c:
        c.execute(text("ALTER TABLE seasons DROP COLUMN timezone"))
    engine.dispose()
    r = report(database)
    assert "timezone" in r["schema"]["tables"]["seasons"]["unavailable_columns"]
    assert r["summary_counts"]["inventory_complete"] is False
    assert "source_calendar_mismatch" in r["seasons"]["validation_reasons"]
    assert not any(t["classification"] == "SAFE_CANDIDATE" for t in r["team_classifications"])
