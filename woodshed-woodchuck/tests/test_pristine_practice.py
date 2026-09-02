from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, main, practice_chart_routes
from app.contests import (
    ensure_band_camp_data,
    finalize_contest_week,
    team_leaderboards,
    weekly_student_points,
)
from app.db import Base
from app.main import app
from app.models import (
    PracticeChart,
    PracticeChartVerification,
    Contest,
    ContestResult,
    Season,
    Team,
    TeamMembership,
    WoodchuckProfile,
    WoodchuckState,
)
from app.security import hash_pin
from app.team_practice_rating import calculate_team_practice_rating
from app.teams import create_and_join_team, select_team


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)
FINAL_NOW = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)


@pytest.fixture()
def pristine_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    for module in (account_routes, main, practice_chart_routes):
        monkeypatch.setattr(module, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_profile(session: Session, key: str, instrument: str = "Flute") -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id=f"WC-PRISTINE-{key}",
        display_name=f"Pristine {key}",
        pin_hash=hash_pin("2468"),
        instrument=instrument,
        level="Beginner",
        goal="Practice",
    )
    session.add(profile)
    session.flush()
    session.add(WoodchuckState(
        profile_id=profile.id,
        state_json={"progress": {"credits": 0}},
        revision=0,
    ))
    return profile


def test_pristine_route_ui_and_microphone_privacy(pristine_database) -> None:
    store = (ROOT / "templates" / "store.html").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "pristine_practice.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "pristine-practice.js").read_text(encoding="utf-8")
    detector = (ROOT / "static" / "js" / "pristine-detector.js").read_text(encoding="utf-8")

    assert 'href="/practice/pristine" aria-label="Open Pristine Practice"' in store
    assert "Pristine P-Chart — Coming Soon" not in store
    assert "Pristine Practice" in template
    assert "Audio is\n    never saved or uploaded." in template
    assert "data-pristine-time" in template
    assert "data-pristine-done" in template
    assert "Save &amp; Finish" in template
    assert "Leave Without Saving" in template
    assert "Leaving this screen will not save your practice." in template
    assert "data-pristine-retry" in template
    assert "getUserMedia" in script and "createAnalyser" in script
    assert "getFloatTimeDomainData" in script
    assert "PristinePracticeDetector.createDetector" in script
    assert "/static/js/pristine-timer.js?v=2" in template
    assert "/static/js/pristine-detector.js?v=1" in template
    assert "/static/js/pristine-practice.js?v=3" in template
    assert "START_CONFIRMATION_MS = 180" in detector
    assert "strongTransientThreshold" in detector
    assert "adaptIdleFloor" in detector
    assert "MediaRecorder" not in script
    assert "FormData" not in script
    assert 'fetch("/practice-charts/pristine"' in script
    assert "No playing was detected yet." in script
    assert "if (currentState.playingSeconds < 1)" in script
    assert "function hasUnsavedPractice()" in script
    assert "timer.snapshot().playingSeconds >= 1" in script
    assert 'window.addEventListener("beforeunload"' in script
    assert "event.returnValue = \"\"" in script
    assert "if (approved) leaveApproved = true" in script
    assert "window.location.assign(destination || \"/store\")" in script

    with TestClient(app) as client:
        response = client.get("/practice/pristine")
    assert response.status_code == 200
    assert "sign in" in response.text.casefold()


def test_pristine_api_persists_exact_time_without_verifier_and_snapshots_team(
    pristine_database,
) -> None:
    factory = pristine_database
    with factory() as session:
        profile = add_profile(session, "API")
        season = Season(
            key="back-to-school-2026",
            name="Back to School",
            starts_on=date(2026, 8, 24),
            status="active",
        )
        session.add(season)
        session.flush()
        team = Team(
            season_id=season.id,
            display_name="Pristine Team",
            normalized_name="pristine team",
            emblem_key="emoji:eagle",
            creator_profile_id=profile.id,
        )
        session.add(team)
        session.flush()
        session.add(TeamMembership(
            season_id=season.id,
            team_id=team.id,
            profile_id=profile.id,
            selected_week_start=date(2026, 8, 31),
            started_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        ))
        session.commit()
        profile_id, team_id = profile.id, team.id

    client = TestClient(app)
    login = client.post(
        "/account/login",
        data={"woodchuck_id": "WC-PRISTINE-API", "pin": "2468"},
    )
    assert login.status_code == 200
    submitted = {
        "detected_playing_seconds": 754,
        "submission_key": "pristine-api-session",
        "include_contests": True,
        "include_team_contests": True,
    }
    first = client.post("/practice-charts/pristine", json=submitted)
    repeated = client.post("/practice-charts/pristine", json=submitted)
    assert first.status_code == 201 and repeated.status_code == 201
    assert first.json()["created"] is True
    assert repeated.json()["created"] is False
    chart_payload = first.json()["chart"]
    assert chart_payload["minutes"] == 12
    assert chart_payload["detected_playing_seconds"] == 754
    assert chart_payload["source"] == "pristine"
    assert chart_payload["pristine"] is True
    assert chart_payload["verification"] is None
    assert chart_payload["team_id"] == team_id

    with factory() as session:
        charts = session.scalars(select(PracticeChart).where(
            PracticeChart.profile_id == profile_id
        )).all()
        assert len(charts) == 1
        assert charts[0].team_id == team_id
        assert session.scalar(select(PracticeChartVerification)) is None

    unauthenticated = TestClient(app).post(
        "/practice-charts/pristine", json=submitted
    )
    assert unauthenticated.status_code == 401


def test_pristine_requires_detected_playing_but_accepts_exact_subminute_time(
    pristine_database,
) -> None:
    factory = pristine_database
    with factory() as session:
        profile = add_profile(session, "ZERO")
        ordinary = PracticeChart(
            profile_id=profile.id,
            practice_date=date(2026, 8, 31),
            minutes=10,
            instrument=profile.instrument,
            practice_details=[],
            source="p-book",
            credits_awarded=0,
        )
        session.add(ordinary)
        session.commit()
        profile_id = profile.id

    client = TestClient(app)
    assert client.post(
        "/account/login",
        data={"woodchuck_id": "WC-PRISTINE-ZERO", "pin": "2468"},
    ).status_code == 200
    rejected = client.post("/practice-charts/pristine", json={
        "detected_playing_seconds": 0,
        "submission_key": "pristine-zero-session",
    })
    assert rejected.status_code == 422

    one_second = client.post("/practice-charts/pristine", json={
        "detected_playing_seconds": 1,
        "submission_key": "pristine-one-second-session",
    })
    fifty_nine_seconds = client.post("/practice-charts/pristine", json={
        "detected_playing_seconds": 59,
        "submission_key": "pristine-fifty-nine-second-session",
    })
    assert one_second.status_code == 201
    assert fifty_nine_seconds.status_code == 201
    assert one_second.json()["chart"]["minutes"] == 0
    assert one_second.json()["chart"]["detected_playing_seconds"] == 1
    assert fifty_nine_seconds.json()["chart"]["minutes"] == 0
    assert fifty_nine_seconds.json()["chart"]["detected_playing_seconds"] == 59

    history = client.get("/practice-charts").json()["charts"]
    ordinary_payload = next(row for row in history if row["source"] == "p-book")
    pristine_payloads = [row for row in history if row["source"] == "pristine"]
    assert ordinary_payload["pristine"] is False
    assert ordinary_payload["detected_playing_seconds"] is None
    assert len(pristine_payloads) == 2
    assert all(row["pristine"] is True for row in pristine_payloads)
    assert sorted(row["detected_playing_seconds"] for row in pristine_payloads) == [1, 59]

    with factory() as session:
        charts = session.scalars(select(PracticeChart).where(
            PracticeChart.profile_id == profile_id
        )).all()
        assert len(charts) == 3
        assert all(chart.detected_playing_seconds != 0 for chart in charts)
        assert "pristine-zero-session" not in {
            chart.submission_key for chart in charts
        }


def test_pristine_populates_open_pristine_team_average_and_tpr_only() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        season, _contests, week = ensure_band_camp_data(session, now=NOW)
        first = add_profile(session, "BOARD-A")
        second = add_profile(session, "BOARD-B", "Trumpet")
        session.commit()
        team, _ = create_and_join_team(
            session,
            profile=first,
            season=season,
            name="Pristine Players",
            emblem_key="letter:P",
            now=NOW,
        )
        select_team(session, profile=second, season=season, team=team, now=NOW)
        verified = PracticeChart(
            profile_id=first.id,
            practice_date=week.week_start,
            minutes=10,
            instrument=first.instrument,
            practice_details=[],
            source="p-book",
            credits_awarded=0,
            include_contests=True,
            include_team_contests=True,
            team_id=team.id,
        )
        pristine = PracticeChart(
            profile_id=first.id,
            practice_date=week.week_start,
            minutes=30,
            instrument=first.instrument,
            practice_details=[],
            source="pristine",
            detected_playing_seconds=1800,
            credits_awarded=0,
            include_contests=True,
            include_team_contests=True,
            team_id=team.id,
        )
        short_pristine = PracticeChart(
            profile_id=second.id,
            practice_date=week.week_start,
            minutes=4,
            instrument=second.instrument,
            practice_details=[],
            source="pristine",
            detected_playing_seconds=240,
            credits_awarded=0,
            include_contests=True,
            include_team_contests=True,
            team_id=team.id,
        )
        session.add_all([verified, pristine, short_pristine])
        session.flush()
        session.add(PracticeChartVerification(
            practice_chart_id=verified.id,
            status="approved",
            responded_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        ))
        session.commit()

        students = weekly_student_points(
            session,
            contest_week=week,
            current_profile_id=first.id,
        )
        assert students["open"][0]["total_minutes"] == 40
        assert students["verified"][0]["total_minutes"] == 10
        assert students["pristine"][0]["total_minutes"] == 30
        assert all(row["display_name"] != second.display_name for row in students["verified"])

        boards = team_leaderboards(session, season=season, contest_week=week)
        assert boards["team-weekly-practice"]["open"][0]["score"] == 44
        assert boards["team-weekly-practice"]["verified"][0]["score"] == 10
        assert boards["team-weekly-practice"]["pristine"][0]["score"] == 34
        assert boards["team-weekly-average-practice"]["pristine"][0]["score"] == 30
        assert boards["team-weekly-average-practice"]["pristine"][0]["active_member_count"] == 1
        assert boards["team-practice-rating"]["pristine"][0]["score"] == (
            calculate_team_practice_rating([30], eligible_roster=2).rating
        )
    engine.dispose()


def test_pristine_finalization_matches_live_open_and_pristine_divisions() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        _season, _contests, week = ensure_band_camp_data(session, now=NOW)
        profile = add_profile(session, "FINAL")
        session.add(PracticeChart(
            profile_id=profile.id,
            practice_date=week.week_start,
            minutes=10,
            instrument=profile.instrument,
            practice_details=[],
            source="pristine",
            detected_playing_seconds=600,
            credits_awarded=0,
            include_contests=True,
            created_at=NOW,
        ))
        session.commit()

        finalize_contest_week(
            session,
            week_start=week.week_start,
            now=FINAL_NOW,
        )
        session.commit()
        divisions = set(session.scalars(
            select(ContestResult.division)
            .join(Contest, Contest.id == ContestResult.contest_id)
            .where(
                Contest.key == "weekly-points-leaders",
                ContestResult.profile_id == profile.id,
            )
        ).all())
        assert divisions == {"open", "pristine"}
    engine.dispose()


def test_pristine_timer_state_machine_grace_pause_resume_done_and_safety() -> None:
    script = r"""
const assert = require('node:assert/strict');
const api = require('./static/js/pristine-timer.js');
const timer = api.createTimer({maxSampleGapMs: Infinity});
assert.equal(timer.sample(0, true).playingSeconds, 0);
assert.equal(timer.sample(5000, true).playingSeconds, 5);
assert.equal(timer.sample(6000, false).playingSeconds, 6);
let state = timer.sample(13000, false);
assert.equal(state.status, 'listening');
assert.equal(state.paused, false);
assert.equal(state.playingSeconds, 13);
state = timer.sample(13999, false);
assert.equal(state.status, 'listening');
assert.equal(state.playingSeconds, 13);
state = timer.sample(14000, false);
assert.equal(state.status, 'paused');
assert.equal(state.playingSeconds, 14);
assert.equal(timer.sample(20000, true).status, 'playing');
assert.equal(timer.sample(23000, true).playingSeconds, 17);
assert.equal(timer.pause(24000).playingSeconds, 18);
assert.equal(timer.sample(30000, true).playingSeconds, 18);
timer.resume(31000);
assert.equal(timer.sample(32000, true).playingSeconds, 19);
const done = timer.finish(33000);
assert.equal(done.status, 'done');
assert.equal(done.playingSeconds, 20);
assert.equal(timer.sample(50000, true).playingSeconds, 20);

const beforePlaying = api.createTimer({maxSampleGapMs: Infinity});
beforePlaying.sample(0, false);
state = beforePlaying.sample(8000, false);
assert.equal(state.playingSeconds, 0);
assert.equal(state.status, 'listening');

const returnedInTime = api.createTimer({maxSampleGapMs: Infinity});
returnedInTime.sample(0, true);
returnedInTime.sample(1000, false);
state = returnedInTime.sample(7999, true);
assert.equal(state.status, 'playing');
assert.equal(state.playingSeconds, 7);

const safety = api.createTimer({safetyPauseMs: 60000, maxSampleGapMs: Infinity});
safety.sample(0, true);
state = safety.sample(60000, true);
assert.equal(state.status, 'safety-paused');
assert.equal(state.playingSeconds, 60);
assert.equal(safety.sample(90000, true).playingSeconds, 60);
safety.resume(90000);
assert.equal(safety.sample(100000, true).playingSeconds, 70);
assert.equal(api.SILENCE_GRACE_MS, 8000);
assert.equal(api.SAFETY_PAUSE_MS, 3600000);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_pristine_detector_uses_hysteresis_confirmation_and_idle_adaptation() -> None:
    script = r"""
const assert = require('node:assert/strict');
const api = require('./static/js/pristine-detector.js');
const detector = api.createDetector({smoothingAlpha: 1, initialNoiseFloor: 0.008});
detector.sample(0.008, 0, {calibrating: true});
let state = detector.sample(0.012, 100, {canContinue: false});
assert.equal(state.detected, false);
assert.ok(state.startThreshold > state.continueThreshold);
assert.ok(state.continueThreshold > state.noiseFloor);

// A weak bump is below the restart threshold, and strong sustained sound
// needs the short confirmation window before it starts the timer.
assert.equal(detector.sample(0.020, 200, {canContinue: false}).detected, false);
assert.equal(detector.sample(0.030, 300, {canContinue: false}).detected, false);
assert.equal(detector.sample(0.030, 470, {canContinue: false}).detected, false);
state = detector.sample(0.030, 480, {canContinue: false});
assert.equal(state.detected, true);

// Once a real session is playing, a quieter sustained note may continue it.
state = detector.sample(0.015, 500, {canContinue: true});
assert.equal(state.detected, true);

// After the silence grace has paused the timer, that same weak noise cannot
// restart it. A clearly loud percussion-like transient can.
assert.equal(detector.sample(0.015, 600, {canContinue: false}).detected, false);
state = detector.sample(0.065, 700, {canContinue: false});
assert.equal(state.detected, true);
assert.equal(state.strongTransient, true);

const idle = api.createDetector({smoothingAlpha: 1, initialNoiseFloor: 0.005});
const startingFloor = idle.snapshot(false, false).noiseFloor;
for (let index = 0; index < 180; index += 1) {
  idle.sample(0.008, index * 20, {canContinue: false});
}
const adaptedFloor = idle.snapshot(false, false).noiseFloor;
assert.ok(adaptedFloor > startingFloor);
idle.sample(0.050, 4000, {canContinue: true});
assert.equal(idle.snapshot(false, false).noiseFloor, adaptedFloor);
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_pristine_migration_follows_current_head_and_is_additive() -> None:
    migration = (
        ROOT
        / "migrations"
        / "versions"
        / "g7b8c9d0e1f2_add_pristine_practice.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "f6a7b8c9d0e1"' in migration
    assert 'sa.Column("detected_playing_seconds", sa.Integer(), nullable=True)' in migration
    assert "ck_practice_chart_detected_seconds_positive" in migration
    assert "detected_playing_seconds IS NULL OR detected_playing_seconds > 0" in migration
    assert "division IN ('open', 'verified', 'pristine')" in migration
    assert "DELETE FROM practice_charts" not in migration
