"""Seconds survive aggregation, eligibility, live ranking and new snapshots."""
import csv
from datetime import date, timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text

from app.practice_duration import chart_seconds, chart_seconds_sql, format_seconds
from app.practice_chart_routes import practice_totals_payload, profile_practice_streak
from app.practice_charts import create_pristine_practice_chart, create_practice_chart_verification_request
from app.student_practice_metrics import practice_totals, practice_insights
from app.band_director_dashboard import dashboard_metrics, dashboard_csv
from app.models import (PracticeChart, ContestResult, Contest, RewardGrant, CrownProgress,
                        CrownAward, TeamWeekMembershipSnapshot, WoodchuckState)
from app.contests import (ensure_band_camp_data, weekly_student_points, weekly_practice_by_instrument,
                          team_leaderboards, finalize_contest_week)
from app.teams import create_and_join_team, select_team
from app.xp import xp_sources
from test_pristine_practice import pristine_database, add_profile, NOW, FINAL_NOW
from test_band_director_roster import roster_db, add_student, signed_client


@pytest.mark.parametrize("source,seconds,minutes,expected", [
    *[("pristine", seconds, 7, seconds) for seconds in (0, 1, 59, 60, 119)],
    ("pristine", None, 7, 420), ("p-book", None, 7, 420),
    ("legacy", None, 7, 420), ("p-book", 59, 7, 420),
    ("pristine", -1, 7, 420), ("pristine", 86401, 7, 420),
])
def test_authoritative_duration_and_legacy_sql(source, seconds, minutes, expected):
    chart = SimpleNamespace(source=source, detected_playing_seconds=seconds, minutes=minutes)
    assert chart_seconds(chart) == expected
    # Minimal historical read shape also exercises missing/zero seconds, which
    # the current submission constraints intentionally do not accept.
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE practice_charts (source TEXT, minutes INTEGER, detected_playing_seconds INTEGER)"))
        connection.execute(text("INSERT INTO practice_charts VALUES (:source, :minutes, :seconds)"),
                           dict(source=source, minutes=minutes, seconds=seconds))
        assert connection.scalar(select(chart_seconds_sql())) == expected
    engine.dispose()


def test_totals_book_credit_streak_insights_and_xp(pristine_database):
    with pristine_database() as session:
        profile = add_profile(session, "SECONDS")
        other = add_profile(session, "OTHER")
        session.commit()
        day = date(2026, 9, 7)
        # Historical private reports remain readable; only qualified records earn.
        for index, seconds in enumerate((1, 59, 59, 60, 119)):
            session.add(PracticeChart(profile_id=profile.id, instrument="Flute",
                source="pristine", detected_playing_seconds=seconds, minutes=seconds // 60,
                practice_date=day + timedelta(days=index), include_contests=False))
        session.add(PracticeChart(profile_id=profile.id, instrument="Flute", practice_date=day, minutes=5))
        session.add(PracticeChart(profile_id=other.id, instrument="Flute", practice_date=day, minutes=999))
        session.commit()
        totals = practice_totals_payload(session, profile.id, today=day)
        assert totals["this_week_seconds"] == totals["career_seconds"] == 598
        assert totals["this_week_display"] == "9 minutes 58 seconds"
        assert totals["career_minutes"] == 598 / 60
        assert profile_practice_streak(session, profile.id, today=day + timedelta(days=4)) == 0
        charts = session.scalars(select(PracticeChart).where(PracticeChart.profile_id == profile.id)).all()
        assert len(charts) == 6  # Each save creates a Book entry.
        assert practice_totals(charts, set())["days"] == 5
        insight = practice_insights(charts, set(), today=day + timedelta(days=7))
        assert insight["total_seconds"] == 598
        assert insight["weeks"][-1]["pristine_seconds"] == 298
        assert insight["weeks"][-1]["days"] == 5
        assert xp_sources(session, profile_id=profile.id)["practice_minutes"] == 0
        assert sum(chart.credits_awarded for chart in charts) == 0
    assert format_seconds(118) == "1 minute 58 seconds"
    assert format_seconds(3601) == "1 hour 1 second"


def test_director_report_export_and_rendered_seconds(roster_db):
    student = add_student(roster_db, "Seconds")
    add_student(roster_db, "Hidden", verifier_id=2)
    with roster_db() as session:
        for seconds in (59, 59):
            session.add(PracticeChart(profile_id=student, practice_date=date(2026, 9, 7),
                minutes=0, instrument="Trumpet", source="pristine", detected_playing_seconds=seconds,
                include_contests=False))
        session.commit()
        data = dashboard_metrics(session, verifier_id=1, today=date(2026, 9, 9))
        row = data["students"][0]
        assert row["weekly"]["total_seconds"] == row["lifetime"]["total_seconds"] == 118
        assert row["weekly"]["days"] == 1
        exported = list(csv.DictReader(StringIO(dashboard_csv(data))))
        assert len(exported) == 1
        assert exported[0]["Practice Seconds"] == exported[0]["Career Practice Seconds"] == "118"
        assert exported[0]["Pristine Seconds"] == "118"
        assert exported[0]["Verified Seconds"] == "0"
    response = signed_client().get("/band-director/dashboard?week=2026-09-07")
    assert "1 minute 58 seconds" in response.text
    assert "Hidden" not in response.text


@pytest.mark.parametrize("base_seconds", [0, 240])
def test_precise_live_ties_finalization_and_immutable_rewards(pristine_database, base_seconds):
    with pristine_database() as session:
        season, _, week = ensure_band_camp_data(session, now=NOW)
        profiles = [add_profile(session, key, instrument) for key, instrument in
                    (("A", "Flute"), ("B", "Trumpet"), ("C", "Clarinet"))]
        session.commit()
        for profile, durations in zip(profiles, [(base_seconds + 59, 59), (base_seconds + 118,), (base_seconds + 119,)]):
            team, _ = create_and_join_team(session, profile=profile, season=season,
                name=f"Seconds {profile.id}", emblem_key=f"letter:{chr(64 + profile.id)}", now=NOW)
            for seconds in durations:
                session.add(PracticeChart(profile_id=profile.id, practice_date=week.week_start,
                    minutes=seconds // 60, detected_playing_seconds=seconds, source="pristine",
                    instrument=profile.instrument, created_at=NOW, team_id=team.id))
        session.commit()
        data = weekly_student_points(session, contest_week=week, current_profile_id=profiles[0].id)
        assert data["open"] == data["verified"] == data["pristine"] == []
        assert weekly_practice_by_instrument(session, contest_week=week)["open"] == []
        boards = team_leaderboards(session, season=season, contest_week=week)
        for key in ("team-weekly-practice", "team-weekly-average-practice", "team-lifetime-practice"):
            assert boards[key]["open"] == []
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()
        assert list(session.scalars(select(ContestResult))) == []
        assert list(session.scalars(select(RewardGrant))) == []
        assert list(session.scalars(select(CrownAward))) == []
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW + timedelta(days=1))
        assert list(session.scalars(select(ContestResult))) == []


def test_tpr_keeps_fractional_input_and_existing_threshold_and_cap():
    from app.team_practice_rating import calculate_team_practice_rating
    assert calculate_team_practice_rating([299 / 60], eligible_roster=1).active_participants == 0
    rating = calculate_team_practice_rating([359 / 60], eligible_roster=1)
    assert rating.average_minutes == 359 / 60
    assert rating.rating == 2.4
    assert calculate_team_practice_rating([121], eligible_roster=1).rating == 48


def test_additive_migration_preserves_old_snapshots():
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration = importlib.import_module("migrations.versions.t0p1q2r3s4t5_add_precise_contest_score")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE contest_weeks (id INTEGER PRIMARY KEY, status TEXT)"))
        connection.execute(text("INSERT INTO contest_weeks VALUES (1, 'finalized'), (2, 'open'), (3, 'pending')"))
        connection.execute(text("CREATE TABLE contest_results (id INTEGER PRIMARY KEY, score INTEGER, rank INTEGER, medal TEXT)"))
        connection.execute(text("INSERT INTO contest_results VALUES (1, 0, 1, 'gold'), (2, 12, 2, 'silver')"))
        before = list(connection.execute(text("SELECT * FROM contest_results")))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert list(connection.execute(text("SELECT id, score, rank, medal FROM contest_results"))) == before
        assert list(connection.scalars(text("SELECT precise_score FROM contest_results"))) == [None, None]
        assert list(connection.scalars(text("SELECT practice_scoring_mode FROM contest_weeks ORDER BY id"))) == ["legacy_minutes", None, None]
        # Old finalizers after migration cannot acquire a legacy attestation.
        connection.execute(text("UPDATE contest_weeks SET status = 'finalized' WHERE id = 2"))
        assert connection.scalar(text("SELECT practice_scoring_mode FROM contest_weeks WHERE id = 2")) is None
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert list(connection.execute(text("SELECT * FROM contest_results"))) == before
    engine.dispose()
