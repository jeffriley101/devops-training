"""Explicit authority promotion against disposable SQLite/PostgreSQL only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models as m, persistent_team_cutover as cutover
from app.db import Base
from tests.test_team_families import disposable_url

NOW = datetime(2026, 10, 1, 15, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=18)
TEAM_IDS = list(range(10, 15))
MEMBERSHIP_IDS = list(range(100, 120))
BOUNDARY = date(2026, 10, 5)


def seed(url):
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=OFF")  # Disposable fixtures only.
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)"))
        c.execute(text("INSERT INTO alembic_version VALUES ('p20team001')"))
    with Session(engine) as s:
        for sid, key, start, end in ((1, "band-camp-2026", date(2026, 7, 27), date(2026, 9, 13)),
                                    (2, "back-to-school-2026", date(2026, 9, 14), date(2026, 9, 27)),
                                    (3, "halloween-2026", date(2026, 9, 28), date(2026, 11, 1))):
            s.add(m.Season(id=sid, key=key, name=key, status="active", starts_on=start, ends_on=end))
        for pid in list(range(1, 20)) + [24]:
            s.add(m.WoodchuckProfile(id=pid, woodchuck_id=f"WC-PERSIST-{pid}", display_name="synthetic",
                pin_hash="SENSITIVE_PIN_HASH", instrument="Trumpet", level="Beginner", goal="Practice"))
        s.flush()
        names = ("Eureka", "Union", "The Teachers", "St. Louis", "Mr. Pickles Minions")
        for i, name in enumerate(names):
            family = m.TeamFamily(id=i + 1); s.add(family); s.flush()
            for tid, sid in ((i + 1, 1), (i + 10, 2)):
                s.add(m.Team(id=tid, family_id=family.id, season_id=sid, display_name=name,
                            normalized_name=name.casefold(), emblem_key=f"letter:{chr(65+i)}",
                            creator_profile_id=i + 1))
            s.add(m.TeamNameClaim(normalized_name=name.casefold(), family_id=family.id))
        s.flush()
        for i, pid in enumerate(list(range(1, 20)) + [24]):
            tid = 12 if pid == 24 else 10 + i % 5
            s.add(m.TeamMembership(id=100+i, profile_id=pid, team_id=tid, season_id=2,
                    started_at=OLD, selected_week_start=date(2026, 9, 14)))
            if pid != 24:
                s.add(m.TeamMembership(id=i+1, profile_id=pid, team_id=tid-9, season_id=1,
                        started_at=OLD-timedelta(days=30), selected_week_start=date(2026, 8, 10)))
        # The genuine St. Louis -> The Teachers change is immutable evidence.
        s.add(m.TeamMembership(id=90, profile_id=24, team_id=13, season_id=2,
                started_at=OLD-timedelta(days=1), ended_at=OLD, selected_week_start=date(2026, 9, 7)))
        for wid, sid, start, status in ((1, 2, date(2026, 9, 21), "finalized"),
                                        (2, 3, date(2026, 9, 28), "open"),
                                        (3, 3, date(2026, 10, 5), "open")):
            end = start + timedelta(days=7)
            due = datetime.combine(end, datetime.min.time(), timezone.utc) + timedelta(hours=17)
            s.add(m.ContestWeek(id=wid, season_id=sid, week_start=start, week_end=end, status=status,
                verification_deadline_at=due, finalize_after=due,
                finalized_at=OLD if status == "finalized" else None))
        s.add(m.PersistentTeamControl(id=1))
        s.add(m.TeamReport(team_id=10, reporter_profile_id=1, category="other", details="WHY CAN'T I LEAVE"))
        s.add(m.Contest(id=1, key="team-weekly-practice", name="Team Practice",
                        metric_type="practice_minutes", subject_type="team"))
        s.flush()
        s.add(m.ContestResult(id=1, contest_week_id=1, contest_id=1, subject_type="team", subject_key="13",
                team_id=13, display_name_snapshot="St. Louis", division="open", score=12, rank=1, medal="gold"))
        s.add(m.TeamWeekMembershipSnapshot(id=1, contest_week_id=1, profile_id=24, team_id=13,
                                           membership_id=90, snapshot_at=OLD-timedelta(hours=1)))
        s.add(m.PracticeChart(id=1, profile_id=24, practice_date=date(2026, 9, 15), team_id=13,
                minutes=12, instrument="Trumpet", practice_details=[], note="SENSITIVE_NOTE", created_at=OLD))
        s.add(m.PracticeChart(id=2, profile_id=24, practice_date=date(2026, 9, 29), team_id=None,
                minutes=8, instrument="Trumpet", practice_details=[], created_at=NOW-timedelta(days=1)))
        # Exactly nine NULL examples of the boundary defect remain unchanged.
        for i, activity in enumerate(["care"] + ["hours"]*2 + ["marching"] + ["trivia"]*5):
            s.add(m.CampPointAward(profile_id=24, team_id=None, activity_type=activity,
                    points_awarded=1, occurred_at=NOW-timedelta(days=1), duplicate_key=f"null:{i}"))
        s.commit()
    if engine.dialect.name == "postgresql":
        with engine.begin() as c:
            for name in ("seasons", "woodchuck_profiles", "team_families", "teams", "team_memberships",
                         "contest_weeks", "contests", "contest_results", "team_week_membership_snapshots", "practice_charts"):
                c.execute(text(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), (SELECT MAX(id) FROM {name}), true)"))
    return engine


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request, tmp_path):
    url = disposable_url(tmp_path, request.param)
    engine = seed(url)
    yield url, engine
    engine.dispose()


def plan(db, **kwargs):
    return cutover.generate_plan(db[0], TEAM_IDS, MEMBERSHIP_IDS, BOUNDARY, now=NOW, **kwargs)


def apply(db, approved, **kwargs):
    options = dict(acknowledgments={ack: True for ack in cutover.ACKS},
                   confirmation=cutover.CONFIRMATION, now=NOW)
    options.update(kwargs)
    return cutover.apply_cutover(db[0], approved, approved["plan_sha256"], **options)


def snapshot(engine):
    with engine.connect() as c:
        return cutover._snapshot(c, cutover._tables(c))


def test_five_existing_teams_twenty_memberships_promoted_no_copies(db):
    before = snapshot(db[1])
    approved = plan(db)
    receipt = apply(db, approved)
    after = snapshot(db[1])
    assert receipt["verification"]["passed"]
    assert cutover.verify_cutover(db[0], approved, approved["plan_sha256"], receipt)["passed"]
    assert {t["id"] for t in after["teams"] if t["is_operating"]} == set(TEAM_IDS)
    assert {m["id"] for m in after["team_memberships"] if m["is_persistent"]} == set(MEMBERSHIP_IDS)
    assert len(after["teams"]) == len(before["teams"]) == 10
    assert len(after["team_memberships"]) == len(before["team_memberships"]) == 40
    assert not [t for t in after["teams"] if t["season_id"] == 3]
    assert not after["team_membership_transitions"]
    for name in ("practice_charts", "camp_point_awards", "team_reports", "team_week_membership_snapshots", "contest_results"):
        assert after[name] == before[name]
    assert sum(a["team_id"] is None for a in after["camp_point_awards"]) == 9
    assert next(m for m in after["team_memberships"] if m["id"] == 90) == next(m for m in before["team_memberships"] if m["id"] == 90)
    assert next(w for w in after["contest_weeks"] if w["id"] == 2) == next(w for w in before["contest_weeks"] if w["id"] == 2)
    assert next(w for w in after["contest_weeks"] if w["id"] == 3)["team_membership_rules_version"] == "persistent_v1"


def test_plan_readonly_stable_hash_and_no_private_values(db):
    before = snapshot(db[1])
    approved = plan(db)
    later = cutover.generate_plan(db[0], TEAM_IDS, MEMBERSHIP_IDS, BOUNDARY, now=NOW+timedelta(minutes=1))
    assert approved["plan_sha256"] == later["plan_sha256"]
    assert snapshot(db[1]) == before
    assert "SENSITIVE" not in json.dumps(approved)
    assert len(approved["content"]["legacy_unended_membership_ids"]) == 19
    with cutover.inventory.readonly_connection(db[0]) as c:
        with pytest.raises(Exception):
            c.execute(text("UPDATE teams SET display_name='forbidden' WHERE id=10"))


@pytest.mark.parametrize("reason", ["team", "membership", "report", "null_award", "profile"])
def test_stale_approved_plan_refused_without_writes(db, reason):
    approved = plan(db)
    with db[1].begin() as c:
        query = {
            "team": "UPDATE teams SET display_name='Changed' WHERE id=10",
            "membership": "UPDATE team_memberships SET selected_week_start='2026-09-21' WHERE id=100",
            "report": "UPDATE team_reports SET details='Reviewed'",
            "null_award": "UPDATE camp_point_awards SET points_awarded=2 WHERE id=1",
            "profile": "UPDATE woodchuck_profiles SET display_name='Changed' WHERE id=1",
        }[reason]
        c.execute(text(query))
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="stale_plan"):
        apply(db, approved)
    assert snapshot(db[1]) == before


def test_explicit_hash_and_acknowledgments_required(db):
    approved = plan(db)
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="confirmation"):
        apply(db, approved, confirmation="yes")
    with pytest.raises(cutover.CutoverError, match="acknowledgments"):
        apply(db, approved, acknowledgments={})
    changed = deepcopy(approved); changed["content"]["team_ids"] = [10]
    with pytest.raises(cutover.CutoverError, match="hash"):
        apply(db, changed)
    assert snapshot(db[1]) == before


@pytest.mark.parametrize("ids", [TEAM_IDS+[1], TEAM_IDS+[10]])
def test_ambiguous_or_incomplete_team_authority_refused(db, ids):
    with pytest.raises(cutover.CutoverError):
        cutover.generate_plan(db[0], ids, MEMBERSHIP_IDS, BOUNDARY, now=NOW)


def test_incomplete_membership_authority_refused(db):
    with pytest.raises(cutover.CutoverError, match="exact_unended"):
        cutover.generate_plan(db[0], TEAM_IDS, MEMBERSHIP_IDS[:-1], BOUNDARY, now=NOW)


def test_historical_only_family_remains_without_operating_authority(db):
    with Session(db[1]) as s:
        family = m.TeamFamily(id=99); s.add(family); s.flush()
        s.add(m.Team(id=99, family_id=99, season_id=1, display_name="Retired Team",
                    normalized_name="retired team", emblem_key="letter:Z"))
        s.add(m.TeamNameClaim(normalized_name="retired team", family_id=99)); s.commit()
    apply(db, plan(db))
    with Session(db[1]) as s:
        assert not s.get(m.Team, 99).is_operating


def test_cutover_refuses_already_consumed_legacy_switch(db):
    at = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)
    with Session(db[1]) as s:
        original = s.get(m.TeamMembership, 119)
        original.ended_at = at
        s.flush()
        s.add(m.TeamMembership(id=200, season_id=2, profile_id=24, team_id=10,
                started_at=at, selected_week_start=date(2026, 9, 28)))
        s.commit()
    before = snapshot(db[1])
    ids = [mid for mid in MEMBERSHIP_IDS if mid != 119] + [200]
    with pytest.raises(cutover.CutoverError, match="not_carried|legacy_current_week"):
        cutover.generate_plan(db[0], TEAM_IDS, ids, BOUNDARY, now=NOW)
    assert snapshot(db[1]) == before


def test_cutover_refuses_prior_leave_for_currently_teamless_student(db):
    with Session(db[1]) as s:
        s.get(m.TeamMembership, 119).ended_at = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)
        s.commit()
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="legacy_current_week"):
        cutover.generate_plan(db[0], TEAM_IDS, MEMBERSHIP_IDS[:-1], BOUNDARY, now=NOW)
    assert snapshot(db[1]) == before


def test_cutover_refuses_other_profiles_legacy_current_week_choice(db):
    # The guard must inspect all evidence, not just approved member profiles.
    with Session(db[1]) as s:
        s.add(m.WoodchuckProfile(id=99, woodchuck_id="WC-PERSIST-99", display_name="synthetic",
                pin_hash="synthetic", instrument="Trumpet", level="Beginner", goal="Practice"))
        s.flush()
        s.add(m.TeamMembership(id=200, season_id=1, profile_id=99, team_id=1,
                started_at=datetime(2026, 9, 29, 15, tzinfo=timezone.utc),
                ended_at=datetime(2026, 9, 30, 15, tzinfo=timezone.utc),
                selected_week_start=date(2026, 9, 28)))
        s.commit()
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="legacy_current_week"):
        plan(db)
    assert snapshot(db[1]) == before


def test_cutover_apply_rechecks_no_new_legacy_choice(db):
    approved = plan(db)
    with Session(db[1]) as s:
        s.get(m.TeamMembership, 119).ended_at = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)
        s.commit()
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError):
        apply(db, approved)
    assert snapshot(db[1]) == before


@pytest.mark.parametrize("boundary", [date(2026, 9, 28), date(2026, 10, 6)])
def test_rules_boundary_must_be_future_clean_monday(db, boundary):
    with pytest.raises(cutover.CutoverError):
        cutover.generate_plan(db[0], TEAM_IDS, MEMBERSHIP_IDS, boundary, now=NOW)


def test_pending_requests_require_explicit_ids(db):
    with Session(db[1]) as s:
        s.add(m.TeamJoinRequest(id=1, season_id=2, team_id=10, profile_id=24)); s.commit()
    with pytest.raises(cutover.CutoverError, match="every_pending"):
        plan(db)
    approved = plan(db, join_request_ids=[1]); apply(db, approved)
    with Session(db[1]) as s:
        assert s.get(m.TeamJoinRequest, 1).is_persistent


def test_one_unended_persistent_membership_database_enforced(db):
    apply(db, plan(db))
    with Session(db[1]) as s:
        s.add(m.TeamMembership(profile_id=24, team_id=10, season_id=None, is_persistent=True,
                              started_at=NOW, selected_week_start=date(2026, 9, 28)))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()


def test_finite_effective_interval_overlap_database_enforced(db):
    apply(db, plan(db))
    with Session(db[1]) as s:
        s.get(m.TeamMembership, 119).ended_at = NOW + timedelta(days=2)
        s.commit()
        s.add(m.TeamMembership(profile_id=24, team_id=10, is_persistent=True,
                              started_at=NOW, ended_at=NOW+timedelta(days=1),
                              selected_week_start=date(2026, 9, 28)))
        with pytest.raises(IntegrityError, match="interval_overlap"):
            s.flush()
        s.rollback()


def test_empty_interval_preserved_and_noncompeting(db):
    apply(db, plan(db))
    with Session(db[1]) as s:
        s.add(m.TeamMembership(profile_id=24, team_id=10, is_persistent=True,
                              started_at=NOW, ended_at=NOW,
                              selected_week_start=date(2026, 9, 28)))
        s.commit()


def test_submillisecond_overlap_is_rejected(db):
    apply(db, plan(db))
    with Session(db[1]) as s:
        s.get(m.TeamMembership, 119).ended_at = NOW+timedelta(microseconds=2)
        s.commit()
        s.add(m.TeamMembership(profile_id=24, team_id=10, is_persistent=True,
                started_at=NOW+timedelta(microseconds=1), ended_at=NOW+timedelta(microseconds=3),
                selected_week_start=date(2026, 9, 28)))
        with pytest.raises(IntegrityError, match="interval_overlap"):
            s.flush()
        s.rollback()


def test_negative_interval_rejected(db):
    apply(db, plan(db))
    with Session(db[1]) as s:
        s.get(m.TeamMembership, 119).ended_at = OLD-timedelta(seconds=1)
        with pytest.raises(IntegrityError, match="negative_interval"):
            s.flush()
        s.rollback()


def test_concurrent_finite_intervals_cannot_overlap(db):
    if db[1].dialect.name != "postgresql":
        pytest.skip("Concurrent database interval guard requires PostgreSQL")
    apply(db, plan(db))
    with db[1].begin() as c:
        c.execute(text("UPDATE team_memberships SET ended_at=:now WHERE id=119"), {"now": NOW})
    barrier = Barrier(2)
    def worker(tid):
        with Session(db[1]) as s:
            s.add(m.TeamMembership(profile_id=24, team_id=tid, is_persistent=True,
                                  started_at=NOW, ended_at=NOW+timedelta(days=1),
                                  selected_week_start=date(2026, 9, 28)))
            barrier.wait(timeout=10)
            try:
                s.commit(); return "committed"
            except IntegrityError:
                s.rollback(); return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [10, 11]))
    assert sorted(results) == ["committed", "refused"]


@pytest.mark.parametrize("tamper", ["predicate", "trigger"])
def test_schema_tamper_preflight_refused(db, tamper):
    with db[1].begin() as c:
        if tamper == "predicate":
            c.execute(text("DROP INDEX uq_team_membership_persistent_active_profile"))
            c.execute(text("CREATE UNIQUE INDEX uq_team_membership_persistent_active_profile ON team_memberships (profile_id) WHERE is_persistent=true AND ended_at IS NOT NULL"))
        elif db[1].dialect.name == "postgresql":
            c.execute(text("ALTER TABLE team_memberships DISABLE TRIGGER persistent_team_membership_interval_guard"))
        else:
            c.execute(text("DROP TRIGGER persistent_team_membership_interval_insert"))
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="constraints|guard"):
        plan(db)
    assert snapshot(db[1]) == before


def test_unexpected_protected_write_rolls_back_entire_cutover(db):
    with db[1].begin() as c:
        if db[1].dialect.name == "postgresql":
            c.execute(text("""CREATE FUNCTION test_forbidden_change() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN UPDATE camp_point_awards SET team_id=10 WHERE id=1; RETURN NEW; END; $$"""))
            c.execute(text("CREATE TRIGGER test_forbidden_change AFTER UPDATE ON teams FOR EACH ROW EXECUTE FUNCTION test_forbidden_change()"))
        else:
            c.execute(text("CREATE TRIGGER test_forbidden_change AFTER UPDATE ON teams BEGIN UPDATE camp_point_awards SET team_id=10 WHERE id=1; END"))
    approved = plan(db)
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="unexpected_row_change"):
        apply(db, approved)
    assert snapshot(db[1]) == before


def test_concurrent_cutover_serializes_and_second_refuses(db):
    if db[1].dialect.name != "postgresql":
        pytest.skip("Concurrent cutover fencing requires PostgreSQL")
    approved = plan(db)
    barrier = Barrier(2)
    def worker(_):
        barrier.wait(timeout=10)
        try:
            apply(db, approved); return "committed"
        except cutover.CutoverError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [1, 2]))
    assert sorted(results) == ["committed", "refused"]


def test_one_operating_team_per_family_database_enforced(db):
    apply(db, plan(db))
    with db[1].begin() as c:
        with pytest.raises(IntegrityError):
            c.execute(text("UPDATE teams SET is_operating=true WHERE id=1"))


def test_origin_season_deletion_restricted(db):
    apply(db, plan(db))
    with db[1].begin() as c:
        with pytest.raises(IntegrityError):
            c.execute(text("DELETE FROM seasons WHERE id=2"))


def test_reapply_fails_closed(db):
    approved = plan(db); apply(db, approved)
    before = snapshot(db[1])
    with pytest.raises(cutover.CutoverError, match="disabled_singleton"):
        apply(db, approved)
    assert snapshot(db[1]) == before


def test_tool_not_imported_by_normal_runtime():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app"
    assert all("persistent_team_cutover" not in (root / name).read_text() for name in (
        "season_team_activation.py", "season_maintenance.py", "contest_week_provisioning.py", "main.py"))
