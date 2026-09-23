"""Identity, authorization, read-only aggregation and isolated failure contracts."""
import base64
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from starlette.background import BackgroundTask
from starlette.responses import Response

from app import (
    account_routes, analytics, analytics_routes, arcade_routes, main,
    practice_chart_routes, session_revocations,
)
from app.db import Base
from app.age_privacy import declare_age
from app.models import (AnalyticsEvent, ArcadePlaySession, CampPointAward, Contest,
    ContestResult, ContestWeek, DailyTriviaAttempt, OwnedItemCopy, PracticeChart,
    QuestCompletion, RewardGrant, Season, TesterEnrollment as Enrollment, WoodchuckProfile, WoodchuckState)
from app.security import hash_pin

NOW = datetime(2026, 9, 15, 18, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'analytics.db'}", connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=OFF")  # Disposable fixture database only.
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in (
        main, account_routes, analytics_routes, arcade_routes,
        practice_chart_routes, session_revocations,
    ):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(analytics, "_retry_after", 0)
    monkeypatch.setattr(analytics, "_last_warning", float("-inf"))
    # Alembic's logging setup in migration tests disables existing loggers.
    monkeypatch.setattr(analytics.logger, "disabled", False)
    monkeypatch.setenv("WOODSHED_ANALYTICS_ENABLED", "1")
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "analytics-test-admin")
    monkeypatch.setattr(analytics, "utc_now", lambda: NOW)
    with factory() as session:
        pin_hash = hash_pin("2468")
        for number in range(1, 5):
            session.add(WoodchuckProfile(id=number, woodchuck_id=f"WC-AN-{number}",
                display_name=f"Private Name {number}", pin_hash=pin_hash,
                instrument="Flute", level="Beginner", goal="Practice",
                status="deleted" if number == 3 else "active",
                created_at=NOW - timedelta(days=60)))
        session.flush()
        for profile_id in (1, 2, 4):
            declare_age(
                session, profile_id, "adult",
                at=NOW - timedelta(days=60),
            )
        session.add(WoodchuckState(profile_id=1, state_json={"progress": {"credits": 10}}))
        session.commit()
    yield factory
    engine.dispose()


def client(session_values=None):
    result = TestClient(main.app)
    if session_values:
        cookie = TimestampSigner(main.SESSION_SECRET).sign(
            base64.b64encode(json.dumps(session_values).encode())).decode()
        result.cookies.set("session", cookie)
    return result


def student_client(profile_id=1, version=0):
    return client({"woodchuck_profile_id": profile_id, "woodchuck_session_version": version})


def admin_client():
    result = client()
    # Use the real existing site-admin login and CSRF flow.
    page = result.get("/admin/login")
    assert result.post("/admin/login", data={"csrf": page.context["csrf"],
        "token": "analytics-test-admin"}, follow_redirects=False).status_code == 303
    return result


def recorded(db):
    with db() as session:
        return session.scalars(select(AnalyticsEvent).order_by(AnalyticsEvent.id)).all()


@pytest.mark.parametrize("event_type", list(analytics.EVENT_LABELS))
def test_allowed_events_daily_deduplication_and_central_boundary(db, event_type):
    for instant in (datetime(2026, 9, 15, 4, 59, tzinfo=timezone.utc),
                    datetime(2026, 9, 15, 4, 59, tzinfo=timezone.utc),
                    datetime(2026, 9, 15, 5, tzinfo=timezone.utc)):
        analytics.record_event(db, profile_id=1, event_type=event_type, occurred_at=instant)
    events = recorded(db)
    assert [row.activity_date for row in events] == [date(2026, 9, 14), date(2026, 9, 15)]
    assert all(row.event_type == event_type and row.profile_id == 1 for row in events)


def test_concurrent_entries_remain_one_record(db, caplog):
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(analytics.record_event, db, profile_id=1,
            event_type="arcade_entered", occurred_at=NOW) for _ in range(8)]
        for future in futures:
            future.result()
    assert len(recorded(db)) == 1
    assert not [record for record in caplog.records if record.name == "app.analytics"]


@pytest.mark.parametrize("profile_id,event_type", [(1, "student_login"), (1, "anything"),
    (999, "arcade_entered"), (3, "arcade_entered"), (True, "arcade_entered")])
def test_disallowed_events_and_missing_deleted_identity_are_not_written(db, profile_id, event_type):
    analytics.record_event(db, profile_id=profile_id, event_type=event_type, occurred_at=NOW)
    assert recorded(db) == []


@pytest.mark.parametrize("path,event_type", [("/arcade", "arcade_entered"),
                                           ("/practice/pristine", "pristine_entered")])
def test_identity_and_event_are_server_derived(db, path, event_type):
    result = student_client()
    response = result.get(path + "?student_id=2&profile_id=2&event_type=anything")
    assert response.status_code == 200
    assert result.get(path).status_code == 200
    rows = recorded(db)
    assert [(row.profile_id, row.event_type) for row in rows] == [(1, event_type)]
    assert result.post("/analytics", json={"profile_id": 2, "event_type": "anything"}).status_code == 404
    assert result.post(path, json={"profile_id": 2}).status_code == 405


@pytest.mark.parametrize("identity", [None, {"trusted_verifier_id": 1},
    {"woodchuck_profile_id": 3, "woodchuck_session_version": 0},
    {"woodchuck_profile_id": 1, "woodchuck_session_version": 99}])
def test_unauthenticated_adult_deleted_and_stale_sessions_are_not_observed(db, identity):
    result = client(identity)
    assert result.get("/arcade").status_code == 200
    assert result.get("/practice/pristine").status_code == 200
    assert recorded(db) == []


def test_unsigned_cookie_cannot_manufacture_identity(db):
    result = client()
    result.cookies.set("session", base64.b64encode(json.dumps({
        "woodchuck_profile_id": 2, "woodchuck_session_version": 0}).encode()).decode())
    assert result.get("/arcade").status_code == 200
    assert recorded(db) == []


@pytest.mark.parametrize("identity", [None, {"woodchuck_profile_id": 1, "woodchuck_session_version": 0},
    {"trusted_verifier_id": 1}, {"contest_admin_token_fingerprint": "not-site-admin"},
    {"site_admin_fingerprint": "forged"}])
def test_admin_report_rejects_non_site_admin_before_querying(db, monkeypatch, identity):
    def must_not_query(*args, **kwargs):
        pytest.fail("Unauthorized report queried the database")
    monkeypatch.setattr(analytics_routes, "build_report", must_not_query)
    assert client(identity).get("/admin/analytics").status_code == 403


def test_admin_report_read_only_private_and_token_rotation(db, monkeypatch):
    result = admin_client()
    statements = []
    engine = db.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = result.get("/admin/analytics")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert statements and set(statements) == {"SELECT"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "No recorded activity in this window" in response.text
    assert "Private Name" not in response.text and "WC-AN-" not in response.text
    assert result.post("/admin/analytics").status_code == 405
    monkeypatch.setenv("SITE_ADMIN_TOKEN", "rotated")
    assert result.get("/admin/analytics").status_code == 403


def seed_activity(db):
    yesterday = NOW - timedelta(days=1)
    with db() as session:
        session.add_all([
            RewardGrant(profile_id=1, source_key="login-streak:2026-09-14", category_key="login-streak",
                        reward_type="dandelion", amount=1, created_at=yesterday),
            RewardGrant(profile_id=2, source_key="automatic", reward_type="crown", amount=1, created_at=NOW),
            RewardGrant(profile_id=4, source_key="only-automatic", reward_type="crown", amount=1, created_at=NOW),
            PracticeChart(profile_id=1, practice_date=date(2026, 8, 1), minutes=7, instrument="Flute",
                          source="pristine", detected_playing_seconds=59, created_at=NOW,
                          include_contests=False, include_team_contests=True),
            PracticeChart(profile_id=1, practice_date=NOW.date(), minutes=2, instrument="Flute",
                          source="p-book", created_at=NOW, include_contests=True, include_team_contests=False),
            PracticeChart(profile_id=3, practice_date=NOW.date(), minutes=999, instrument="Flute", created_at=NOW),
            PracticeChart(profile_id=2, practice_date=NOW.date(), minutes=999, instrument="Flute",
                          created_at=NOW - timedelta(days=31)),
            PracticeChart(profile_id=2, practice_date=NOW.date(), minutes=999, instrument="Flute",
                          created_at=NOW + timedelta(days=1)),
            ArcadePlaySession(profile_id=1, game_key="blue", play_token="secret-one",
                              started_at=yesterday, completed_at=NOW, submitted_score=10),
            ArcadePlaySession(profile_id=2, game_key="blue", play_token="secret-two", started_at=NOW - timedelta(hours=2)),
            QuestCompletion(profile_id=1, activity_date=NOW.date(), quest_id="quest", logged_minutes=10,
                            reward_amount=1, completed_at=NOW),
            DailyTriviaAttempt(profile_id=2, activity_date=NOW.date(), selected_answer="private answer",
                               correct=False, created_at=NOW),
            OwnedItemCopy(profile_id=2, item_key="hat", acquisition_source="store", purchase_price=2, acquired_at=NOW),
            OwnedItemCopy(profile_id=4, item_key="hat", acquisition_source="mum", acquired_at=NOW),
            CampPointAward(profile_id=2, activity_type="care", points_awarded=1,
                           occurred_at=yesterday, duplicate_key="care", created_at=NOW),
            CampPointAward(profile_id=4, activity_type="contest-placement", points_awarded=1,
                           occurred_at=NOW, duplicate_key="auto", created_at=NOW),
        ])
        season = Season(key="analytics", name="Analytics", starts_on=date(2026, 9, 1), status="active")
        contest = Contest(key="analytics", name="Analytics", metric_type="practice_minutes", subject_type="student")
        session.add_all([season, contest])
        session.flush()
        week = ContestWeek(season_id=season.id, week_start=date(2026, 9, 7), week_end=date(2026, 9, 14),
                           verification_deadline_at=NOW, finalize_after=NOW, status="finalized")
        session.add(week)
        session.flush()
        session.add(ContestResult(contest_week_id=week.id, contest_id=contest.id, division="open",
            subject_type="student", subject_key="4", profile_id=4, display_name_snapshot="Private Winner",
            score=100, rank=1, medal="gold", created_at=NOW))
        session.commit()
    for profile_id, event_type, instant in [(1, "arcade_entered", yesterday),
        (2, "arcade_entered", NOW), (1, "pristine_entered", NOW), (2, "pristine_entered", NOW)]:
        analytics.record_event(db, profile_id=profile_id, event_type=event_type, occurred_at=instant)



def test_admin_analytics_accepts_known_cohort_filters_and_rejects_unknown(db, monkeypatch):
    result = admin_client()
    seen = []

    def fake_report(_factory, *, cohort_key=None, **_kwargs):
        seen.append(cohort_key)
        return {
            "first_day": date(2026, 8, 17),
            "today": date(2026, 9, 15),
            "as_of": NOW.astimezone(analytics.CENTRAL),
            "cohort_key": cohort_key,
            "enrolled": 0 if cohort_key else None,
            "day1_active": 0,
            "returned_after_day1": 0,
            "events_available": True,
            "recording_enabled": True,
            "accounts": 0, "new_accounts": 0,
            "active_today": 0, "active_7": 0, "active_30": 0, "returning": 0,
            "daily": [],
            "features": [],
            "practice_charts": 0, "practicing_students": 0,
            "practice_duration": "0 seconds",
            "contest_charts": 0, "team_contest_charts": 0,
            "reward_count": 0, "medal_count": 0,
            "games": [],
            "arcade_without_start": 0,
            "pristine_without_save": 0,
            "students": [],
            "recent": [],
        }

    monkeypatch.setattr(analytics_routes, "build_report", fake_report)

    all_page = result.get("/admin/analytics")
    pilot_page = result.get("/admin/analytics?cohort=PILOT-D1")
    c001_page = result.get("/admin/analytics?cohort=C001")
    assert all_page.status_code == pilot_page.status_code == c001_page.status_code == 200
    assert seen == [None, "PILOT-D1", "C001"]
    assert "All accounts" in pilot_page.text
    assert "PILOT-D1" in pilot_page.text
    assert "C001" in pilot_page.text
    assert "Enrolled active testers" in pilot_page.text
    assert "Active on join day" in pilot_page.text
    assert "Returned after join day" in pilot_page.text
    assert "C001 new registration claims: <strong>Open</strong>" in c001_page.text
    monkeypatch.setenv("C001_REGISTRATION_DISABLED", "true")
    closed = result.get("/admin/analytics?cohort=C001")
    assert "C001 new registration claims: <strong>Closed</strong>" in closed.text
    assert '/prebeta/C001/display' in closed.text
    assert "Enrolled active testers" not in all_page.text

    bad = result.get("/admin/analytics?cohort=NOT-A-COHORT")
    assert bad.status_code == 400


def test_report_authoritative_sources_returning_duration_and_workflow_gaps(db):
    seed_activity(db)
    report = analytics.build_report(db, now=NOW)
    assert (report["accounts"], report["active_today"], report["active_7"], report["active_30"], report["returning"]) == (3, 2, 2, 2, 1)
    assert (report["practice_charts"], report["practicing_students"], report["practice_duration"]) == (2, 1, "2 minutes 59 seconds")
    assert (report["contest_charts"], report["team_contest_charts"]) == (1, 1)
    assert (report["reward_count"], report["medal_count"]) == (3, 1)
    assert report["pristine_without_save"] == 1
    assert report["arcade_without_start"] == 0
    assert report["games"] == [("blue", {"started": 2, "completed": 1, "open_over_hour": 1})]
    features = {label: (count, students) for label, count, students in report["features"]}
    for label in ("Store purchases", "Quests completed", "Trivia attempts", "Board activity claims"):
        assert features[label] == (1, 1)
    assert [row["id"] for row in report["students"]] == [1, 2, 4]
    assert report["students"][-1]["active_days"] == 0
    assert dict(report["daily"])[date(2026, 9, 14)] == 1
    response = admin_client().get("/admin/analytics")
    assert response.status_code == 200
    for secret in ("Private Name", "Private Winner", "secret-one", "private answer", "WC-AN-"):
        assert secret not in response.text



def test_cohort_report_filters_profiles_and_ignores_pre_join_activity(db):
    joined = NOW - timedelta(days=1)

    with db() as session:
        session.add_all([
            Enrollment(profile_id=1, cohort_key="PILOT-D1", joined_at=joined),
            Enrollment(profile_id=2, cohort_key="C001", joined_at=joined),
        ])
        session.add_all([
            # Profile 1: pre-join practice must not count.
            PracticeChart(
                profile_id=1, practice_date=NOW.date(), minutes=20,
                instrument="Flute", source="p-book",
                created_at=joined - timedelta(minutes=1),
            ),
            # Profile 1: post-join practice counts.
            PracticeChart(
                profile_id=1, practice_date=NOW.date(), minutes=5,
                instrument="Flute", source="p-book",
                created_at=joined + timedelta(minutes=1),
            ),
            # Profile 2 belongs to another cohort and must not count.
            PracticeChart(
                profile_id=2, practice_date=NOW.date(), minutes=9,
                instrument="Flute", source="p-book",
                created_at=NOW,
            ),
            RewardGrant(
                profile_id=1,
                source_key="pilot-login",
                category_key="login-streak",
                reward_type="dandelion",
                amount=1,
                created_at=joined + timedelta(minutes=2),
            ),
        ])
        session.commit()

    report = analytics.build_report(db, now=NOW, cohort_key="PILOT-D1")

    assert report["cohort_key"] == "PILOT-D1"
    assert report["enrolled"] == 1
    assert report["accounts"] == 1
    assert report["practice_charts"] == 1
    assert report["practicing_students"] == 1
    assert report["practice_duration"] == "5 minutes"
    assert report["active_30"] == 1
    assert report["day1_active"] == 1

    c001 = analytics.build_report(db, now=NOW, cohort_key="C001")
    assert c001["enrolled"] == 1
    assert c001["accounts"] == 1
    assert c001["practice_charts"] == 1



def test_cohort_report_filters_profiles_and_ignores_pre_join_activity(db):
    joined = NOW - timedelta(days=1)

    with db() as session:
        session.add_all([
            Enrollment(profile_id=1, cohort_key="PILOT-D1", joined_at=joined),
            Enrollment(profile_id=2, cohort_key="C001", joined_at=joined),
        ])
        session.add_all([
            # Profile 1: pre-join practice must not count.
            PracticeChart(
                profile_id=1, practice_date=NOW.date(), minutes=20,
                instrument="Flute", source="p-book",
                created_at=joined - timedelta(minutes=1),
            ),
            # Profile 1: post-join practice counts.
            PracticeChart(
                profile_id=1, practice_date=NOW.date(), minutes=5,
                instrument="Flute", source="p-book",
                created_at=joined + timedelta(minutes=1),
            ),
            # Profile 2 belongs to another cohort and must not count.
            PracticeChart(
                profile_id=2, practice_date=NOW.date(), minutes=9,
                instrument="Flute", source="p-book",
                created_at=NOW,
            ),
            RewardGrant(
                profile_id=1,
                source_key="pilot-login",
                category_key="login-streak",
                reward_type="dandelion",
                amount=1,
                created_at=joined + timedelta(minutes=2),
            ),
        ])
        session.commit()

    report = analytics.build_report(db, now=NOW, cohort_key="PILOT-D1")

    assert report["cohort_key"] == "PILOT-D1"
    assert report["enrolled"] == 1
    assert report["accounts"] == 1
    assert report["practice_charts"] == 1
    assert report["practicing_students"] == 1
    assert report["practice_duration"] == "5 minutes"
    assert report["active_30"] == 1
    assert report["day1_active"] == 1

    c001 = analytics.build_report(db, now=NOW, cohort_key="C001")
    assert c001["enrolled"] == 1
    assert c001["accounts"] == 1
    assert c001["practice_charts"] == 1


def test_report_central_day_window_and_dst(db):
    # Fall-back repeats the 1am hour; it still counts as one Central day.
    for instant in (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
                    datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)):
        analytics.record_event(db, profile_id=1, event_type="arcade_entered", occurred_at=instant)
    assert len(recorded(db)) == 1
    report = analytics.build_report(db, now=datetime(2026, 11, 2, 5, 59, tzinfo=timezone.utc))
    assert report["today"] == date(2026, 11, 1)
    assert report["active_today"] == 1 and report["returning"] == 0


def test_exact_30_and_7_day_boundaries_and_recent_limit(db):
    with db() as session:
        for number, (profile_id, instant) in enumerate([
            (4, datetime(2026, 8, 17, 4, 59, tzinfo=timezone.utc)),
            (1, datetime(2026, 8, 17, 5, tzinfo=timezone.utc)),
            (1, datetime(2026, 9, 9, 4, 59, tzinfo=timezone.utc)),
            (2, datetime(2026, 9, 9, 5, tzinfo=timezone.utc)),
            (4, NOW + timedelta(seconds=1)),
        ]):
            session.add(RewardGrant(profile_id=profile_id, source_key=f"boundary-{number}",
                category_key="login-streak", reward_type="dandelion", amount=1, created_at=instant))
        session.commit()
    report = analytics.build_report(db, now=NOW)
    assert report["first_day"] == date(2026, 8, 17)
    assert (report["active_30"], report["active_7"], report["active_today"], report["returning"]) == (2, 1, 0, 1)
    with db() as session:
        for index in range(55):
            session.add(ArcadePlaySession(profile_id=1, game_key="blue", play_token=f"limit-{index}",
                started_at=NOW - timedelta(seconds=index)))
        session.commit()
    assert len(analytics.build_report(db, now=NOW)["recent"]) == 50


@pytest.mark.parametrize("path", ["/arcade", "/practice/pristine"])
def test_missing_event_table_never_breaks_pages_or_primary_actions(db, path, caplog):
    AnalyticsEvent.__table__.drop(db.kw["bind"])
    result = student_client()
    for _ in range(3):
        assert result.get(path).status_code == 200
    # Login, reward, game debit/completion and saved practice still commit.
    assert result.post("/account/login", data={"woodchuck_id": "WC-AN-1", "pin": "2468"}).status_code == 200
    start = result.post("/arcade/plays", json={"game_key": "blue"})
    assert start.status_code == 200
    token = start.json()["play_token"]
    assert result.post(f"/arcade/plays/{token}/complete", json={"score": 10}).status_code == 200
    saved = result.post("/practice-charts/pristine", json={"detected_playing_seconds": 61,
        "submission_key": "analytics-failure", "include_contests": False, "include_team_contests": False})
    assert saved.status_code == 201
    with db() as session:
        assert session.scalar(select(PracticeChart.detected_playing_seconds)) == 61
        assert session.scalar(select(ArcadePlaySession.completed_at)) is not None
        assert session.scalar(select(RewardGrant.id)) is not None
    response = admin_client().get("/admin/analytics")
    assert response.status_code == 200
    assert "Entry observations are unavailable" in response.text
    messages = [record.message for record in caplog.records if record.name == "app.analytics"]
    assert len(messages) == 1
    assert "OperationalError" in messages[0]
    assert "WC-AN" not in messages[0] and "INSERT" not in messages[0]


def test_commit_failure_rolls_back_only_analytics_and_recovers(db, monkeypatch):
    def fail_commit(self):
        raise RuntimeError("private exception details")
    with monkeypatch.context() as patch:
        patch.setattr(db.class_, "commit", fail_commit)
        assert student_client().get("/arcade").status_code == 200
    assert recorded(db) == []
    with db() as session:
        assert session.get(WoodchuckState, 1).state_json == {"progress": {"credits": 10}}
    monkeypatch.setattr(analytics, "_retry_after", 0)
    assert student_client().get("/arcade").status_code == 200
    assert len(recorded(db)) == 1


def test_disabled_and_scheduling_failure_leave_responses_unchanged(db, monkeypatch):
    monkeypatch.setenv("WOODSHED_ANALYTICS_ENABLED", "0")
    assert student_client().get("/arcade").status_code == 200
    assert recorded(db) == []
    monkeypatch.setenv("WOODSHED_ANALYTICS_ENABLED", "1")
    def fail_schedule(*args, **kwargs):
        raise RuntimeError("background task construction failed")
    monkeypatch.setattr(analytics, "BackgroundTask", fail_schedule)
    assert student_client().get("/practice/pristine").status_code == 200
    assert recorded(db) == []


def test_existing_background_task_is_preserved(db):
    task = BackgroundTask(lambda: None)
    response = Response(background=task)
    assert analytics.observe_response(response, session_factory=db, profile_id=1,
        event_type="arcade_entered").background is task


def test_recording_runs_after_response_body(db, monkeypatch):
    messages = []
    def record(*args, **kwargs):
        assert messages[-1]["type"] == "http.response.body"
        messages.append({"type": "analytics"})
    monkeypatch.setattr(analytics, "record_event", record)
    response = analytics.observe_response(Response("normal page"), session_factory=db,
        profile_id=1, event_type="arcade_entered")
    assert messages == []
    async def send(message):
        messages.append(message)
    asyncio.run(response({"type": "http"}, None, send))
    assert [message["type"] for message in messages] == ["http.response.start", "http.response.body", "analytics"]


def test_real_render_failure_is_not_hidden_or_recorded(db, monkeypatch):
    def fail_render(*args, **kwargs):
        raise RuntimeError("core rendering failure")
    monkeypatch.setattr(main.templates, "TemplateResponse", fail_render)
    with pytest.raises(RuntimeError, match="core rendering failure"):
        student_client().get("/arcade")
    assert recorded(db) == []


def test_only_two_pages_are_instrumented_and_admin_does_not_create_activity(db):
    result = student_client()
    for path in ("/home", "/p-book", "/store", "/arcade/blue"):
        assert result.get(path).status_code == 200
    assert admin_client().get("/admin/analytics").status_code == 200
    assert recorded(db) == []
