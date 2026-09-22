"""Small, best-effort entry observations and read-only pre-beta reporting.

Only internal IDs, two fixed event names and dates are collected. No metadata.
The caller supplies a trusted profile ID and a session factory, never its active
transaction. A response background task runs only after rendering has succeeded.
"""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
import logging
import os
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from starlette.background import BackgroundTask

from .models import (
    AnalyticsEvent, ArcadePlaySession, CampPointAward, ContestResult,
    DailyTriviaAttempt, OwnedItemCopy, PracticeChart, QuestCompletion,
    RewardGrant, SEASON_TIMEZONE, TesterEnrollment, WoodchuckProfile, utc_now,
)
from .practice_duration import chart_seconds_sql, format_seconds


CENTRAL = ZoneInfo(SEASON_TIMEZONE)
EVENT_LABELS = {
    "arcade_entered": "Arcade entry days",
    "pristine_entered": "Pristine Practice entry days",
}
logger = logging.getLogger(__name__)
_failure_lock = Lock()
_retry_after = 0.0
_last_warning = float("-inf")


def _warn(operation, error):
    """At most one warning per five minutes per worker; no SQL or identifiers."""
    global _last_warning
    with _failure_lock:
        now = monotonic()
        if now - _last_warning >= 300:
            _last_warning = now
            logger.warning("Analytics %s unavailable (%s); observations may be incomplete",
                           operation, type(error).__name__)


def as_utc(value):
    # SQLite returns naive values for the project's UTC DateTime columns.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def record_event(session_factory, *, profile_id, event_type, occurred_at):
    """Private server hook. Never accept a request, browser payload or live Session."""
    global _retry_after
    try:
        if (os.getenv("WOODSHED_ANALYTICS_ENABLED", "1") == "0"
                or monotonic() < _retry_after
                or event_type not in EVENT_LABELS
                or type(profile_id) is not int):
            return
        instant = as_utc(occurred_at)
        activity_date = instant.astimezone(CENTRAL).date()
        with session_factory() as session:
            from .age_privacy import eligible, can_publish
            if not eligible(session,profile_id) or not can_publish(session,profile_id,at=instant):return
            if session.scalar(select(WoodchuckProfile.id).where(
                WoodchuckProfile.id == profile_id, WoodchuckProfile.status == "active",
            )) is None:
                return
            duplicate = select(AnalyticsEvent.id).where(
                AnalyticsEvent.profile_id == profile_id,
                AnalyticsEvent.event_type == event_type,
                AnalyticsEvent.activity_date == activity_date,
            )
            if session.scalar(duplicate) is not None:
                return
            insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
            session.execute(insert(AnalyticsEvent).values(
                profile_id=profile_id, event_type=event_type,
                occurred_at=instant, activity_date=activity_date,
            ).on_conflict_do_nothing(index_elements=["profile_id", "event_type", "activity_date"]))
            session.commit()
    except Exception as error:
        # This session owns only analytics work; closing it rolls back only that work.
        _retry_after = monotonic() + 300
        _warn("write", error)


def observe_response(response, *, session_factory, profile_id, event_type):
    """Attach only to the two opted-in page renders, after their sessions close."""
    try:
        if (profile_id is not None and event_type in EVENT_LABELS
                and response.status_code == 200 and response.background is None
                and os.getenv("WOODSHED_ANALYTICS_ENABLED", "1") != "0"):
            response.background = BackgroundTask(
                record_event, session_factory, profile_id=profile_id,
                event_type=event_type, occurred_at=utc_now(),
            )
    except Exception as error:
        _warn("schedule", error)
    return response


def build_report(session_factory, *, now=None, cohort_key=None):
    """30 Central calendar days including today, up to the supplied instant.

    Read only selected fields. Automated rewards/results are context, never
    activity. Saved charts are measured by submission time, not practice_date.
    """
    now = as_utc(now or utc_now())
    today = now.astimezone(CENTRAL).date()
    first_day = today - timedelta(days=29)
    start = datetime.combine(first_day, time.min, CENTRAL).astimezone(timezone.utc)

    cohort_members = None
    if cohort_key is not None:
        from .tester_enrollments import normalize_cohort_key
        cohort_key = normalize_cohort_key(cohort_key)
        with session_factory() as session:
            cohort_members = {
                profile_id: as_utc(joined_at)
                for profile_id, joined_at in session.execute(
                    select(TesterEnrollment.profile_id, TesterEnrollment.joined_at)
                    .join(WoodchuckProfile, WoodchuckProfile.id == TesterEnrollment.profile_id)
                    .where(
                        TesterEnrollment.cohort_key == cohort_key,
                        WoodchuckProfile.status == "active",
                    )
                ).all()
            }

    def included(profile_id, timestamp=None):
        if cohort_members is None:
            return True
        joined_at = cohort_members.get(profile_id)
        if joined_at is None:
            return False
        return timestamp is None or as_utc(timestamp) >= joined_at

    activity = []
    counts = Counter()
    people = defaultdict(set)

    def add(profile_id, timestamp, label):
        instant = as_utc(timestamp)
        activity.append((profile_id, instant, label))
        counts[label] += 1
        people[label].add(profile_id)

    # Isolate this read too: an unapplied migration must not poison PostgreSQL's
    # transaction for the authoritative tables below.
    events_available = True
    try:
        with session_factory() as session:
            events = session.execute(select(
                AnalyticsEvent.profile_id, AnalyticsEvent.occurred_at, AnalyticsEvent.event_type,
            ).join(WoodchuckProfile, WoodchuckProfile.id == AnalyticsEvent.profile_id).where(
                WoodchuckProfile.status == "active",
                AnalyticsEvent.occurred_at >= start, AnalyticsEvent.occurred_at <= now,
            )).all()
    except SQLAlchemyError as error:
        events_available = False
        events = []
        _warn("report", error)
    for profile_id, timestamp, event_type in events:
        if included(profile_id, timestamp):
            add(profile_id, timestamp, EVENT_LABELS[event_type])

    with session_factory() as session:
        def rows(model, timestamp, *extra, conditions=()):
            result = session.execute(select(model.profile_id, timestamp, *extra).join(
                WoodchuckProfile, WoodchuckProfile.id == model.profile_id,
            ).where(WoodchuckProfile.status == "active", timestamp >= start,
                    timestamp <= now, *conditions)).all()
            return [row for row in result if included(row[0], row[1])]

        accounts = session.execute(select(WoodchuckProfile.id, WoodchuckProfile.created_at).where(
            WoodchuckProfile.status == "active",
        )).all()
        if cohort_members is not None:
            accounts = [row for row in accounts if row[0] in cohort_members]
        # Daily dandelion grants already form a durable daily sign-in ledger.
        for profile_id, timestamp in rows(RewardGrant, RewardGrant.created_at, conditions=(
            RewardGrant.category_key == "login-streak", RewardGrant.reward_type == "dandelion",
        )):
            add(profile_id, timestamp, "Daily sign-in activity")

        charts = rows(PracticeChart, PracticeChart.created_at, PracticeChart.source,
                      chart_seconds_sql(), PracticeChart.include_contests,
                      PracticeChart.include_team_contests)
        for profile_id, timestamp, source, *_ in charts:
            add(profile_id, timestamp, "Pristine charts saved" if source == "pristine" else "Other practice charts saved")

        plays = rows(ArcadePlaySession, ArcadePlaySession.started_at,
                     ArcadePlaySession.completed_at, ArcadePlaySession.game_key)
        for profile_id, timestamp, *_ in plays:
            add(profile_id, timestamp, "Games started")
        for profile_id, timestamp in rows(ArcadePlaySession, ArcadePlaySession.completed_at):
            add(profile_id, timestamp, "Games completed")
        for model, timestamp, label, conditions in (
            (QuestCompletion, QuestCompletion.completed_at, "Quests completed", ()),
            (DailyTriviaAttempt, DailyTriviaAttempt.created_at, "Trivia attempts", ()),
            (OwnedItemCopy, OwnedItemCopy.acquired_at, "Store purchases", (OwnedItemCopy.acquisition_source == "store",)),
            (CampPointAward, CampPointAward.created_at, "Board activity claims",
             (CampPointAward.activity_type.in_(("hours", "care", "marching")),)),
        ):
            for profile_id, timestamp in rows(model, timestamp, conditions=conditions):
                add(profile_id, timestamp, label)

        def context_count(model, *conditions):
            result = session.execute(select(model.profile_id, model.created_at).join(
                WoodchuckProfile, WoodchuckProfile.id == model.profile_id,
            ).where(WoodchuckProfile.status == "active", model.created_at >= start,
                    model.created_at <= now, *conditions)).all()
            return sum(1 for profile_id, timestamp in result if included(profile_id, timestamp))

        reward_count = context_count(RewardGrant)
        medal_count = context_count(ContestResult, ContestResult.subject_type == "student")

    by_day = defaultdict(set)
    by_profile = defaultdict(set)
    areas = defaultdict(set)
    last_seen = {}
    observed_days = defaultdict(set)
    for profile_id, timestamp, label in activity:
        day = timestamp.astimezone(CENTRAL).date()
        by_day[day].add(profile_id)
        by_profile[profile_id].add(day)
        areas[profile_id].add(label)
        observed_days[label].add((profile_id, day))
        last_seen[profile_id] = max(last_seen.get(profile_id, timestamp), timestamp)

    games = defaultdict(lambda: {"started": 0, "completed": 0, "open_over_hour": 0})
    for _, timestamp, completed_at, game_key in plays:
        games[game_key]["started"] += 1
        games[game_key]["completed"] += int(completed_at is not None and as_utc(completed_at) <= now)
        games[game_key]["open_over_hour"] += int(completed_at is None and as_utc(timestamp) <= now - timedelta(hours=1))
    practice_by_profile = Counter()
    seconds_by_profile = Counter()
    for profile_id, _, _, seconds, *_ in charts:
        practice_by_profile[profile_id] += 1
        seconds_by_profile[profile_id] += seconds

    days = [first_day + timedelta(days=index) for index in range(30)]

    day1_active = 0
    returned_after_day1 = 0
    if cohort_members is not None:
        for profile_id, joined_at in cohort_members.items():
            joined_day = joined_at.astimezone(CENTRAL).date()
            active_days = by_profile.get(profile_id, set())
            day1_active += int(joined_day in active_days)
            returned_after_day1 += int(any(day > joined_day for day in active_days))

    return {
        "today": today, "first_day": first_day, "as_of": now.astimezone(CENTRAL),
        "cohort_key": cohort_key,
        "enrolled": len(cohort_members) if cohort_members is not None else None,
        "day1_active": day1_active,
        "returned_after_day1": returned_after_day1,
        "events_available": events_available,
        "recording_enabled": os.getenv("WOODSHED_ANALYTICS_ENABLED", "1") != "0",
        "accounts": len(accounts),
        "new_accounts": sum(start <= as_utc(created) <= now for _, created in accounts),
        "active_today": len(by_day[today]),
        "active_7": len(set().union(*(by_day[day] for day in days[-7:]))),
        "active_30": len(by_profile),
        "returning": sum(len(dates) > 1 for dates in by_profile.values()),
        "daily": [(day, len(by_day[day])) for day in reversed(days)],
        "features": [(label, counts[label], len(people[label])) for label in (
            "Daily sign-in activity", *EVENT_LABELS.values(), "Pristine charts saved",
            "Other practice charts saved", "Games started", "Games completed",
            "Quests completed", "Trivia attempts", "Board activity claims", "Store purchases",
        )],
        "practice_charts": len(charts), "practicing_students": len(practice_by_profile),
        "practice_duration": format_seconds(sum(seconds_by_profile.values())),
        "contest_charts": sum(bool(row[4]) for row in charts),
        "team_contest_charts": sum(bool(row[5]) for row in charts),
        "reward_count": reward_count, "medal_count": medal_count,
        "games": sorted(games.items()),
        "arcade_without_start": len(observed_days[EVENT_LABELS["arcade_entered"]] - observed_days["Games started"]),
        "pristine_without_save": len(observed_days[EVENT_LABELS["pristine_entered"]] - observed_days["Pristine charts saved"]),
        "students": [{
            "id": profile_id,
            "last_seen": last_seen[profile_id].astimezone(CENTRAL) if profile_id in last_seen else None,
            "active_days": len(by_profile.get(profile_id, ())),
            "practice_charts": practice_by_profile[profile_id],
            "practice_duration": format_seconds(seconds_by_profile[profile_id]),
            "areas": sorted(areas[profile_id]),
        } for profile_id, _ in sorted(accounts)],
        "recent": [(timestamp.astimezone(CENTRAL), profile_id, label)
                   for profile_id, timestamp, label in sorted(activity, key=lambda row: row[1], reverse=True)[:50]],
    }
