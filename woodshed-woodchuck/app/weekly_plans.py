"""Issued account/week contracts. Reads project practice; nothing settles rewards."""
from datetime import date, datetime, time, timedelta, timezone
import sqlite3

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update

from .age_privacy import require_eligible
from .content import GOAL_OPTIONS
from .feature_access import require_feature
from .models import PracticeChart, WoodchuckProfile
from .weekly import CENTRAL, utc_instant, weekly_credit
from .weekly_models import WeeklyPlan


GENERATOR_VERSION = "weekly_plan_v1"
TARGETS = {
    "Beginner": (3, 30, 10, 3),
    "Challenge": (4, 60, 15, 7),
    "Performance": (5, 90, 20, 15),
}


def _week_start(instant):
    day = instant.astimezone(CENTRAL).date()
    return day - timedelta(days=day.weekday())


def _midnight(day):
    return datetime.combine(day, time.min, CENTRAL).astimezone(timezone.utc)


def _week_charts(session, profile_id, start, limit, *, exclusive=False):
    end = start + timedelta(days=7)
    # Select only credit inputs, never notes/free text or rating projections.
    return session.execute(select(
        PracticeChart.practice_date, PracticeChart.created_at, PracticeChart.source,
        PracticeChart.minutes, PracticeChart.detected_playing_seconds,
    ).where(
        PracticeChart.profile_id == profile_id,
        or_(and_(PracticeChart.practice_date >= start, PracticeChart.practice_date < end),
            and_(PracticeChart.created_at >= _midnight(start), PracticeChart.created_at < _midnight(end))),
        PracticeChart.created_at < limit if exclusive else PracticeChart.created_at <= limit,
    )).all()


def load_current_plan(session, profile_id, *, now=None):
    start = _week_start(utc_instant(now))
    return session.scalar(select(WeeklyPlan).where(
        WeeklyPlan.profile_id == profile_id, WeeklyPlan.week_start == start))


def current_general_progress(session, profile_id, *, now=None, week_start=None):
    instant = utc_instant(now)
    start = week_start if week_start is not None else _week_start(instant)
    if type(start) is not date or start.weekday() != 0:
        raise ValueError("Weekly progress must start on Monday.")
    return weekly_credit(_week_charts(session, profile_id, start, instant),
                         week_start=start, now=instant)


def recent_results_snapshot(session, profile_id, *, week_start):
    if type(week_start) is not date or week_start.weekday() != 0:
        raise ValueError("Weekly plans must start on Monday.")
    cutoff = _midnight(week_start)
    weeks = []
    for days in (14, 7):
        start = week_start - timedelta(days=days)
        credit = weekly_credit(_week_charts(session, profile_id, start, cutoff, exclusive=True),
                               week_start=start, now=cutoff, cutoff=cutoff)
        weeks.append({"week_start": start.isoformat(),
                      "practice_seconds": credit["practice_seconds"],
                      "logging_days": credit["logging_days"]})
    return cutoff, {"weeks": weeks}


def _lock_profile(session, profile):
    """Refresh authorization after the same profile-first fence used by issuance."""
    profile_id, expected_version = profile.id, profile.session_version
    require_feature(session, profile_id, "weekly_plans")
    if session.get_bind().dialect.name == "sqlite":
        # Acquire SQLite's writer fence before lookup, without changing history
        # or triggering the profile's Python-side updated_at default.
        session.execute(update(WoodchuckProfile).where(WoodchuckProfile.id == profile_id).values(
            id=WoodchuckProfile.id, updated_at=WoodchuckProfile.updated_at))
    profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == profile_id)
                             .with_for_update().execution_options(populate_existing=True))
    if profile is None or profile.status != "active" or profile.session_version != expected_version:
        raise HTTPException(401, "Student sign-in is required.")
    require_eligible(session, profile_id)
    require_feature(session, profile_id, "weekly_plans")
    return profile


def issue_current_plan(session, profile, focus, *, now=None):
    """Caller owns commit; profile-first locking serializes issuance with preference writes."""
    if not isinstance(focus, str) or focus not in GOAL_OPTIONS:
        raise HTTPException(422, "Choose a canonical weekly Focus.")
    profile = _lock_profile(session, profile)
    profile_id = profile.id
    instant = utc_instant(now)  # Derive the week after waiting for the writer.
    existing = load_current_plan(session, profile_id, now=instant)
    if existing is not None:
        if existing.focus != focus:
            raise HTTPException(409, "This week's Focus is already saved.")
        return existing
    mode = profile.game_mode
    if mode not in TARGETS:
        raise HTTPException(409, "Choose a Game Mode before issuing this week's plan.")
    start = _week_start(instant)
    cutoff, snapshot = recent_results_snapshot(session, profile_id, week_start=start)
    logging, practice, homework, thirds = TARGETS[mode]
    plan = WeeklyPlan(profile_id=profile_id, week_start=start, focus=focus,
        issued_game_mode=mode, generator_version=GENERATOR_VERSION, issued_at=instant,
        recent_results_cutoff=cutoff, recent_results=snapshot,
        logging_days_target=logging, practice_minutes_target=practice,
        homework_minutes_target=homework, thirds_target=thirds)
    session.add(plan)
    session.flush()
    return plan


def plan_payload(plan):
    """Explicit student projection: provenance stays on the server."""
    if plan is None:
        return None
    issued = plan.issued_at
    if issued.tzinfo is None:  # SQLite stores our UTC instants without an offset.
        issued = issued.replace(tzinfo=timezone.utc)
    return {"id": plan.id, "week_start": plan.week_start.isoformat(), "focus": plan.focus,
            "issued_game_mode": plan.issued_game_mode,
            "issued_at": issued.astimezone(timezone.utc).isoformat(),
            "targets": {"logging_days": plan.logging_days_target,
                        "practice_seconds": plan.practice_minutes_target * 60,
                        "homework_minutes": plan.homework_minutes_target,
                        "thirds": plan.thirds_target}}


def current_plan_payload(session, profile, plan, *, now=None):
    # Foundation projection only: no slot allocation, Homework or Caper imports.
    return {"plan": plan_payload(plan), "progress": current_general_progress(
        session, profile.id, now=now, week_start=plan.week_start if plan else None)}


def is_plan_unique_race(error, dialect):
    """Only the account/week UNIQUE can justify an issuance retry."""
    original = error.orig
    if dialect == "sqlite":
        return (getattr(original, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
                and str(original) == "UNIQUE constraint failed: weekly_plans.profile_id, weekly_plans.week_start")
    if dialect == "postgresql":
        return (getattr(original, "sqlstate", None) == "23505"
                and getattr(getattr(original, "diag", None), "constraint_name", None)
                == "uq_weekly_plan_profile_week")
    return False
