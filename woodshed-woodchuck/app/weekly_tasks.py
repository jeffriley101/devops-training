"""Read-only Homework and reward-backed BOARD evidence; no weekly settlement."""
from datetime import datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import String, cast, select

from .contests import BONUS_REWARD_CATEGORY
from .instruments import canonical_instrument_key
from .models import PracticeChart, QuestCompletion, RewardGrant
from .weekly import CENTRAL, utc_instant, weekly_credit
from .weekly_models import WeeklyInstrumentPlan


def _utc(value):
    # Persisted timestamps are UTC; SQLite reflects them without an offset.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else utc_instant(value)


def _bounds(plan, profile_id):
    if plan.profile_id != profile_id:
        raise HTTPException(404, "Weekly plan unavailable.")
    end_date = plan.week_start + timedelta(days=7)
    return end_date, datetime.combine(end_date, time.min, CENTRAL).astimezone(timezone.utc)


def general_task_progress(session, profile_id, plan, *, now=None):
    """Jar evidence ends at source-week close; ordinary history credit is unchanged."""
    from .weekly_plans import _week_charts
    _, end = _bounds(plan, profile_id)
    instant = utc_instant(now)
    charts = _week_charts(session, profile_id, plan.week_start, min(instant, end),
                          exclusive=instant >= end)
    credit = weekly_credit(charts, week_start=plan.week_start, now=instant)
    return {
        "logging": {"progress": credit["logging_days"], "target": plan.logging_days_target,
                    "qualified": credit["logging_days"] >= plan.logging_days_target},
        "practice_minutes": {"progress": credit["practice_seconds"],
                             "target": plan.practice_minutes_target * 60, "unit": "seconds",
                             "qualified": credit["practice_seconds"] >= plan.practice_minutes_target * 60},
    }


def homework_assignment_progress(session, profile_id, plan, assignment, *, now=None):
    end_date, end = _bounds(plan, profile_id)
    if assignment.plan_id != plan.id:
        raise HTTPException(404, "Weekly assignment unavailable.")
    instant = utc_instant(now)
    started = _utc(assignment.started_at)
    charts = session.execute(select(
        PracticeChart.practice_date, PracticeChart.created_at, PracticeChart.instrument,
        PracticeChart.source, PracticeChart.minutes, PracticeChart.detected_playing_seconds,
    ).where(
        PracticeChart.profile_id == profile_id,
        PracticeChart.practice_date >= max(plan.week_start, started.astimezone(CENTRAL).date()),
        PracticeChart.practice_date < end_date,
        PracticeChart.created_at > started,
        PracticeChart.created_at < end,
        PracticeChart.created_at <= instant,
    )).all()
    instrument = canonical_instrument_key(assignment.instrument)
    matching = []
    for chart in charts:
        try:
            matches = canonical_instrument_key(chart.instrument) == instrument
        except ValueError:
            matches = False
        if matches:
            matching.append(chart)
    # This is the approved submission approximation, not measured playing time
    # after issuance. Keep BOOK and additive exact-second Pristine semantics.
    seconds = weekly_credit(matching, week_start=plan.week_start, now=instant)["practice_seconds"]
    target = plan.homework_minutes_target * 60
    return {"slot_no": assignment.slot_no, "instrument": assignment.instrument,
            "practice_seconds": seconds, "target_seconds": target, "qualified": seconds >= target}


def homework_progress(session, profile_id, plan, *, now=None):
    _bounds(plan, profile_id)
    rows = session.scalars(select(WeeklyInstrumentPlan).where(
        WeeklyInstrumentPlan.plan_id == plan.id).order_by(WeeklyInstrumentPlan.slot_no)).all()
    assignments = [homework_assignment_progress(session, profile_id, plan, row, now=now) for row in rows]
    return {"target_seconds": plan.homework_minutes_target * 60,
            "qualified": any(item["qualified"] for item in assignments), "assignments": assignments}


def board_progress(session, profile_id, plan, *, now=None):
    end_date, end = _bounds(plan, profile_id)
    instant = utc_instant(now)
    source = "bonus-challenge:" + cast(QuestCompletion.activity_date, String) + ":" + QuestCompletion.quest_id
    records = session.execute(select(QuestCompletion.activity_date, RewardGrant.category_key).join_from(QuestCompletion,
        RewardGrant,
        (RewardGrant.profile_id == QuestCompletion.profile_id)
        & (RewardGrant.source_key == source)
        & (RewardGrant.reward_type == "dandelion")
        & (RewardGrant.amount == 5)
        & RewardGrant.category_key.startswith(BONUS_REWARD_CATEGORY)
        & (RewardGrant.created_at == QuestCompletion.completed_at),
    ).where(
        QuestCompletion.profile_id == profile_id,
        QuestCompletion.reward_amount == 5,
        QuestCompletion.activity_date >= plan.week_start,
        QuestCompletion.activity_date < end_date,
        QuestCompletion.activity_date <= instant.astimezone(CENTRAL).date(),
        QuestCompletion.completed_at > _utc(plan.issued_at),
        QuestCompletion.completed_at < end,
        QuestCompletion.completed_at <= instant,
    )).all()
    dates = set()
    for activity_date, category in records:
        suffix = category.removeprefix(BONUS_REWARD_CATEGORY)
        try:
            canonical = canonical_instrument_key(suffix)
        except ValueError:
            continue
        if category == BONUS_REWARD_CATEGORY + canonical:
            dates.add(activity_date)
    completed = len(dates)
    return {"target": 2, "completed": min(2, completed), "qualified": completed >= 2}
