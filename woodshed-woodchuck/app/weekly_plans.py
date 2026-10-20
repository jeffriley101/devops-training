"""Issued account/week contracts. Reads project practice; nothing settles rewards."""
from datetime import date, datetime, time, timedelta, timezone
import sqlite3
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update

from .age_privacy import require_eligible
from .content import GOAL_OPTIONS
from .feature_access import require_feature
from .models import PracticeChart, WoodchuckProfile
from .weekly import CENTRAL, utc_instant, weekly_credit
from .weekly_models import WeeklyInstrumentPlan, WeeklyPlan


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
    result = {"plan": plan_payload(plan), "progress": current_general_progress(
        session, profile.id, now=now, week_start=plan.week_start if plan else None)}
    if plan is not None:
        rows = session.scalars(select(WeeklyInstrumentPlan).where(
            WeeklyInstrumentPlan.plan_id == plan.id).order_by(WeeklyInstrumentPlan.slot_no)).all()
        # Preserve the B2a empty-assignment contract. Once started, assignments
        # come exclusively from saved rows; a read never invokes the catalog.
        if rows:
            result["plan"]["instruments"] = [instrument_payload(plan, row) for row in rows]
        result["start_context"] = start_context(profile, plan)
    return result


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


def start_context(profile, plan):
    """Optimistic precondition, not a credential. Mode/preference are deliberately absent."""
    if plan is None:
        return None
    from .weekly_homework import weekly_instrument
    try:
        instrument = weekly_instrument(profile.instrument)
    except ValueError:
        return None
    values = [profile.id, profile.woodchuck_id, plan.id, plan.week_start.isoformat(), instrument]
    return "weekly-start-v1:" + hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def instrument_payload(plan, assignment):
    started = assignment.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return {"slot_no": assignment.slot_no, "instrument": assignment.instrument,
            "started_at": started.astimezone(timezone.utc).isoformat(),
            "task_ref": f"weekly-plan:{plan.id}:slot:{assignment.slot_no}:homework",
            "homework_reward_ref": f"clover-caper:{plan.week_start.isoformat()}:jar:3",
            "homework": assignment.homework_spec,
            "completion_status": "verification-unavailable"}


def start_current_instrument(session, profile, context, *, now=None, authorize=None):
    """Caller commits the complete allocation. There is no completion/reward operation."""
    profile = _lock_profile(session, profile)
    # Current browser-nonce/account checks run after the profile lock wait,
    # before any allocation or generation. Caller rechecks again before commit.
    if authorize is not None:
        profile = authorize(session)
    instant = utc_instant(now)
    plan = load_current_plan(session, profile.id, now=instant)
    if plan is None:
        if context is not None:
            raise HTTPException(409, "The weekly plan changed. Refresh before starting work.")
        raise HTTPException(404, "Issue this week's plan before starting instrument work.")
    from .weekly_homework import homework_spec, weekly_instrument
    try:
        instrument = weekly_instrument(profile.instrument)
    except ValueError as error:
        raise HTTPException(409, "Choose a supported active instrument before starting work.") from error
    if context is None:
        raise HTTPException(422, "Echo the current start_context before starting work.")
    if context != start_context(profile, plan):
        raise HTTPException(409, "The active instrument or weekly plan changed. Refresh before starting work.")
    rows = session.scalars(select(WeeklyInstrumentPlan).where(
        WeeklyInstrumentPlan.plan_id == plan.id).order_by(WeeklyInstrumentPlan.slot_no).with_for_update()).all()
    for row in rows:
        if row.instrument == instrument:
            return instrument_payload(plan, row)
    if len(rows) >= 2:
        raise HTTPException(409, "Both weekly instrument slots are saved. You can still explore this instrument.")
    slot = next(number for number in (1, 2) if number not in {row.slot_no for row in rows})
    row = WeeklyInstrumentPlan(plan_id=plan.id, slot_no=slot, instrument=instrument,
                              started_at=instant, homework_spec=homework_spec(plan, instrument))
    session.add(row)
    session.flush()
    return instrument_payload(plan, row)


def is_instrument_unique_race(error, dialect):
    """Only this table's instrument UNIQUE or slot PK can justify allocation retry."""
    original = error.orig
    if dialect == "sqlite":
        return (getattr(original, "sqlite_errorcode", None) in
                (sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY)
                and str(original) in (
                    "UNIQUE constraint failed: weekly_instrument_plans.plan_id, weekly_instrument_plans.instrument",
                    "UNIQUE constraint failed: weekly_instrument_plans.plan_id, weekly_instrument_plans.slot_no"))
    if dialect == "postgresql":
        return (getattr(original, "sqlstate", None) == "23505" and
                getattr(getattr(original, "diag", None), "constraint_name", None) in
                ("uq_weekly_plan_instrument", "weekly_instrument_plans_pkey"))
    return False
