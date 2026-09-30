"""Persisted-fixture proof of the documented map, not new analytics telemetry."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event, exists, select
from sqlalchemy.orm import Session

from app.arcade_challenges import ACTION_GRACE_SECONDS, GAMES, RUN_SECONDS
from app.db import Base
from app.economy import qualified_camp_point_clause
from app.models import (
    AnalyticsEvent, ArcadePlaySession, CampPointAward, DailyTriviaAttempt,
    PracticeChart, PracticeChartVerification, RewardGrant, TesterEnrollment as Enrollment,
    WoodchuckProfile,
)
from app.practice_duration import chart_seconds_sql, qualified_practice_clause
from app.xp import xp_sources


CENTRAL = ZoneInfo("America/Chicago")
JOINED = datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc)
SUBMITTED = datetime(2026, 9, 20, 5, 30, tzinfo=timezone.utc)  # Sep 20, 00:30 Central
AS_OF = datetime(2026, 10, 10, tzinfo=timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def qualified_submissions(session, profile_id, as_of):
    """Existing earning predicate plus documented membership/as-of boundaries."""
    approved_by = exists().where(
        PracticeChartVerification.practice_chart_id == PracticeChart.id,
        PracticeChartVerification.status == "approved",
        PracticeChartVerification.responded_at <= as_of,
    )
    return session.execute(select(PracticeChart.id, PracticeChart.created_at)
        .join(Enrollment, Enrollment.profile_id == PracticeChart.profile_id)
        .where(PracticeChart.profile_id == profile_id, Enrollment.cohort_key == "C001",
               PracticeChart.created_at > Enrollment.joined_at,
               PracticeChart.created_at <= as_of, chart_seconds_sql() > 0,
               qualified_practice_clause(), approved_by)
        .order_by(PracticeChart.created_at, PracticeChart.id)).all()


def activation(session, profile_id, as_of=AS_OF):
    candidates = qualified_submissions(session, profile_id, as_of)
    return candidates[0] if candidates else None


def return_days(session, profile_id, as_of=AS_OF):
    first = activation(session, profile_id, as_of)
    if first is None:
        return set()
    activation_day = utc(first.created_at).astimezone(CENTRAL).date()
    instants = [row.created_at for row in qualified_submissions(session, profile_id, as_of)]
    instants += list(session.scalars(select(CampPointAward.occurred_at).where(
        CampPointAward.profile_id == profile_id, CampPointAward.occurred_at <= as_of,
        CampPointAward.activity_type.in_(("hours", "care", "marching", "quest")),
        qualified_camp_point_clause())))
    instants += list(session.scalars(select(DailyTriviaAttempt.created_at).where(
        DailyTriviaAttempt.profile_id == profile_id, DailyTriviaAttempt.created_at <= as_of)))
    for play in session.scalars(select(ArcadePlaySession).where(
            ArcadePlaySession.profile_id == profile_id,
            ArcadePlaySession.completed_at <= as_of,
            ArcadePlaySession.authoritative_score.is_not(None))):
        if play.game_key == "history-mystery":
            instants.append(play.completed_at)
            continue
        state = play.challenge_state
        if not isinstance(state, dict):
            continue
        elapsed = state.get("last_elapsed")
        if (play.game_key in GAMES and state.get("version") == 1
                and type(state.get("index")) is int and state["index"] > 0
                and type(elapsed) in (int, float)
                and 0 < elapsed <= RUN_SECONDS + ACTION_GRACE_SECONDS and isfinite(elapsed)):
            last_action_at = utc(play.started_at) + timedelta(seconds=elapsed)
            completed_at = utc(play.completed_at)
            # Starting today's game can seal yesterday's expired unfinished run.
            # Require accepted interaction on the completion day, not a late seal alone.
            if (last_action_at <= completed_at
                    and last_action_at.astimezone(CENTRAL).date() == completed_at.astimezone(CENTRAL).date()):
                instants.append(play.completed_at)
    return {day for timestamp in instants
            if (day := utc(timestamp).astimezone(CENTRAL).date()) > activation_day}


@pytest.fixture
def measurement():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        profile = WoodchuckProfile(woodchuck_id="C001-MAP", display_name="Student",
            pin_hash="fixture-only", instrument="Flute", level="Beginner", goal="Practice")
        session.add(profile)
        session.flush()
        session.add(Enrollment(profile_id=profile.id, cohort_key="C001",
            joined_at=JOINED, source="DIRECTOR1"))
        session.commit()
        yield session, profile
    engine.dispose()


def chart(session, profile, *, timestamp=SUBMITTED, source="p-book", status="approved",
          responded=True, minutes=20, credits=0, key=None):
    row = PracticeChart(profile_id=profile.id, practice_date=timestamp.astimezone(CENTRAL).date(),
        created_at=utc(timestamp), source=source, minutes=minutes, instrument="Flute",
        detected_playing_seconds=minutes * 60 if source == "pristine" else None,
        credits_awarded=credits, submission_key=key)
    session.add(row)
    session.flush()
    if status:
        session.add(PracticeChartVerification(practice_chart_id=row.id, status=status,
            responded_at=utc(timestamp) + timedelta(hours=2) if responded else None))
    session.commit()
    return row


def test_activation_uses_independently_credited_first_post_join_submission(measurement):
    session, profile = measurement
    chart(session, profile, timestamp=JOINED - timedelta(seconds=1))
    chart(session, profile, timestamp=JOINED)
    chart(session, profile, status=None, credits=75)  # Old currency is not qualification.
    chart(session, profile, source="pristine")
    first = chart(session, profile, timestamp=SUBMITTED + timedelta(hours=1))
    chart(session, profile, timestamp=SUBMITTED + timedelta(hours=2))
    assert activation(session, profile.id).id == first.id
    assert utc(activation(session, profile.id).created_at) == SUBMITTED + timedelta(hours=1)
    # A zero-currency chart is still independently qualified chart/XP credit.
    assert first.credits_awarded == 0
    assert xp_sources(session, profile_id=profile.id)["p_charts"] == 4


@pytest.mark.parametrize("changes", [
    {"status": None}, {"status": "pending"}, {"status": "rejected"},
    {"responded": False}, {"source": "pristine"}, {"minutes": 0},
])
def test_unqualified_or_nonpositive_chart_never_activates(measurement, changes):
    session, profile = measurement
    chart(session, profile, **changes)
    assert activation(session, profile.id) is None
    assert return_days(session, profile.id) == set()


def test_approval_controls_snapshot_availability_not_submission_timestamp(measurement):
    session, profile = measurement
    first = chart(session, profile)
    assert activation(session, profile.id, SUBMITTED + timedelta(hours=1)) is None
    assert utc(activation(session, profile.id, SUBMITTED + timedelta(hours=3)).created_at) == SUBMITTED
    assert activation(session, profile.id, SUBMITTED + timedelta(hours=3)).id == first.id


@pytest.mark.parametrize("hours,qualifies", [(0, False), (.25, False), (.5, True), (5, True), (24, True)])
def test_return_uses_later_central_day_not_utc_day_or_elapsed_hours(measurement, hours, qualifies):
    session, profile = measurement
    # Activation 23:30 Central. Five hours later is tomorrow, without waiting 24h.
    activated = datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc)
    chart(session, profile, timestamp=activated)
    chart(session, profile, timestamp=activated + timedelta(hours=hours))
    assert bool(return_days(session, profile.id)) is qualifies


def test_login_page_entry_and_unqualified_saved_practice_do_not_count_return(measurement):
    session, profile = measurement
    chart(session, profile)
    later = SUBMITTED + timedelta(days=1)
    session.add(RewardGrant(profile_id=profile.id, source_key="login-only", reward_type="dandelion",
        category_key="login-streak", amount=1, created_at=later))
    session.add(AnalyticsEvent(profile_id=profile.id, event_type="arcade_entered",
        occurred_at=later, activity_date=later.astimezone(CENTRAL).date()))
    chart(session, profile, timestamp=later, status=None)
    assert return_days(session, profile.id) == set()


@pytest.mark.parametrize("game,completed,authority,state,qualifies", [
    ("thirds", False, None, {"version": 1, "index": 1, "last_elapsed": 29}, False),
    ("thirds", True, None, None, False),
    ("thirds", True, 0, {"version": 1, "index": 0, "last_elapsed": 0}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": 29}, True),
    ("thirds", True, 0, {"version": 1, "index": 1}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": None}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": True}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": -1}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": 33}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": float("inf")}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": float("nan")}, False),
    ("thirds", True, 0, {"version": 1, "index": 1, "last_elapsed": 31}, False),
    ("blue", True, None, None, False),
    ("history-mystery", True, 0, None, True),
])
def test_arcade_requires_supported_completed_meaningful_evidence(
        measurement, game, completed, authority, state, qualifies):
    session, profile = measurement
    chart(session, profile)
    later = SUBMITTED + timedelta(days=1)
    session.add(ArcadePlaySession(profile_id=profile.id, game_key=game, play_token="map-play",
        started_at=later - timedelta(seconds=30), completed_at=later if completed else None,
        authoritative_score=authority, submitted_score=authority if completed else None,
        challenge_state=state, daily_play_date=later.astimezone(CENTRAL).date()
        if game == "history-mystery" else None))
    session.commit()
    assert bool(return_days(session, profile.id)) is qualifies


@pytest.mark.parametrize("activity,points,key,qualifies", [
    ("care", 1, "board-self-report-v2:2026-09-21:care", True),
    ("hours", 1, "board-self-report-v2:2026-09-21:hours", True),
    ("marching", 1, "board-self-report-v2:2026-09-21:marching", True),
    ("quest", 2, "bonus-challenge:self-report-v2:2026-09-21", True),
    ("care", 1, "band-camp:2026-09-21:care", False),
    ("quest", 2, "bonus-challenge:2026-09-21:old-quest", False),
    ("placement", 3, "automatic-placement", False),
    ("contest-placement", 2, "automatic-contest-placement", False),
])
def test_board_uses_inc002_provenance_and_excludes_legacy_or_automatic_activity(
        measurement, activity, points, key, qualifies):
    session, profile = measurement
    chart(session, profile)
    session.add(CampPointAward(profile_id=profile.id, activity_type=activity,
        points_awarded=points, duplicate_key=key, occurred_at=SUBMITTED + timedelta(days=1)))
    session.commit()
    assert bool(return_days(session, profile.id)) is qualifies


@pytest.mark.parametrize("correct", [True, False])
def test_persisted_board_trivia_answer_is_meaningful_even_without_reward(measurement, correct):
    session, profile = measurement
    chart(session, profile)
    later = SUBMITTED + timedelta(days=1)
    session.add(DailyTriviaAttempt(profile_id=profile.id, activity_date=later.astimezone(CENTRAL).date(),
        selected_answer="fixture-answer", correct=correct, created_at=later))
    session.commit()
    assert return_days(session, profile.id) == {later.astimezone(CENTRAL).date()}


@pytest.mark.parametrize("offset,retained", [(0, False), (5, False), (6, True), (7, True), (8, True), (9, False)])
def test_retention_is_frozen_central_day_6_7_or_8(measurement, offset, retained):
    session, profile = measurement
    chart(session, profile)
    chart(session, profile, timestamp=SUBMITTED + timedelta(days=offset))
    day = SUBMITTED.astimezone(CENTRAL).date()
    assert any((value - day).days in {6, 7, 8} for value in return_days(session, profile.id)) is retained


def test_retention_uses_central_calendar_days_across_dst_fall_back(measurement):
    session, profile = measurement
    activated = datetime(2026, 11, 1, 0, 30, tzinfo=CENTRAL)
    later = datetime(2026, 11, 7, 0, 30, tzinfo=CENTRAL)
    chart(session, profile, timestamp=activated)
    chart(session, profile, timestamp=later)
    assert (utc(later) - utc(activated)).total_seconds() == 145 * 3600
    days = return_days(session, profile.id, datetime(2026, 11, 10, tzinfo=timezone.utc))
    assert {(day - activated.date()).days for day in days} == {6}


def test_read_only_selection_deduplicates_review_activity_and_retains_history(measurement):
    session, profile = measurement
    first = chart(session, profile, key="original")
    session.add(PracticeChartVerification(practice_chart_id=first.id, status="approved", responded_at=AS_OF))
    later = SUBMITTED + timedelta(days=7)
    chart(session, profile, timestamp=later, key="return")
    session.add(CampPointAward(profile_id=profile.id, activity_type="care", points_awarded=1,
        duplicate_key="board-self-report-v2:2026-09-27:care", occurred_at=later))
    profile.status = "deleted"  # Historical membership/evidence remains measurable.
    session.commit()
    before = session.execute(select(Enrollment.__table__)).all()
    statements = []
    def collect(connection, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(session.get_bind(), "before_cursor_execute", collect)
    try:
        for _ in range(3):
            assert activation(session, profile.id).id == first.id
            assert return_days(session, profile.id) == {later.astimezone(CENTRAL).date()}
        assert session.execute(select(Enrollment.__table__)).all() == before
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", collect)
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def prepare_arcade_player(session, profile):
    from app.age_privacy import declare_age
    from app.models import WoodchuckState

    declare_age(session, profile.id, "adult", at=JOINED)
    session.add(WoodchuckState(profile_id=profile.id,
        state_json={"progress": {"credits": 500}}, revision=0))
    session.commit()


def test_later_day_game_start_cannot_turn_prior_action_into_return(measurement):
    from app.arcade_rewards import action_arcade_play, start_arcade_play

    session, profile = measurement
    chart(session, profile)
    prepare_arcade_player(session, profile)
    started = SUBMITTED + timedelta(hours=1)
    first = start_arcade_play(session, profile_id=profile.id, game_key="thirds", now=started)
    session.commit()
    action_arcade_play(session, profile_id=profile.id, play_token=first.play.play_token,
        action_index=0, answer=first.play.challenge_state["question"]["answer"],
        now=started + timedelta(seconds=1))
    session.commit()
    assert first.play.completed_at is None
    assert return_days(session, profile.id) == set()

    later = SUBMITTED + timedelta(days=1)
    new = start_arcade_play(session, profile_id=profile.id, game_key="thirds", now=later)
    session.commit()
    # Starting today seals yesterday's expired game with today's server clock.
    # Its retained accepted-action clock still identifies yesterday's interaction.
    assert utc(first.play.completed_at) == later
    assert first.play.authoritative_score == 1
    assert first.play.challenge_state["index"] == 1
    assert first.play.challenge_state["last_elapsed"] == 1
    assert new.play.completed_at is None and new.play.challenge_state["index"] == 0
    assert return_days(session, profile.id) == set()


def test_later_day_accepted_game_action_and_completion_qualify_once(measurement):
    from app.arcade_rewards import action_arcade_play, complete_arcade_play, start_arcade_play

    session, profile = measurement
    chart(session, profile)
    prepare_arcade_player(session, profile)
    later = SUBMITTED + timedelta(days=1)
    opened = start_arcade_play(session, profile_id=profile.id, game_key="thirds", now=later)
    session.commit()
    assert return_days(session, profile.id) == set()
    action_arcade_play(session, profile_id=profile.id, play_token=opened.play.play_token,
        action_index=0, answer=opened.play.challenge_state["question"]["answer"],
        now=later + timedelta(seconds=1))
    session.commit()
    assert return_days(session, profile.id) == set()
    finished = complete_arcade_play(session, profile_id=profile.id,
        play_token=opened.play.play_token, score=1, now=later + timedelta(seconds=30))
    session.commit()
    assert finished["already_completed"] is False
    expected = {later.astimezone(CENTRAL).date()}
    assert return_days(session, profile.id) == expected
    retried = complete_arcade_play(session, profile_id=profile.id,
        play_token=opened.play.play_token, score=1, now=later + timedelta(days=1))
    session.commit()
    assert retried["already_completed"] is True
    assert utc(opened.play.completed_at) == later + timedelta(seconds=30)
    assert return_days(session, profile.id) == expected
