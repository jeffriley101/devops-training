"""Owned Newsletter reads. Publication is a clock boundary, not a settlement job.

Saved plans/receipts and finalized recognition have durable provenance. Practice
and qualification are explicitly current projections of retained source rows;
they are not invented frozen history. No writer is called from this module.
"""
from datetime import datetime, time, timedelta

from sqlalchemy import and_, or_, select

from .feature_access import can_use_feature
from .models import Contest, ContestResult, ContestWeek, RewardGrant, TeamWeekMembershipSnapshot
from .weekly import CENTRAL, utc_instant
from .weekly_caper import clue_state, task_states
from .weekly_models import WeeklyInstrumentPlan, WeeklyPlan
from .weekly_plans import (
    _week_start, current_general_progress, load_current_plan, plan_payload, start_context,
)
from .weekly_tasks import _utc

MAX_COMPETITIONS = 8
MAX_RECOGNITIONS = 64
TASK_FIELDS = {
    "logging": ("progress", "target", "qualified", "claimed", "claimed_at"),
    "practice_minutes": ("progress", "target", "unit", "qualified", "claimed", "claimed_at"),
    "homework": ("target_seconds", "qualified", "claimed", "claimed_at"),
    "board": ("target", "completed", "qualified", "claimed", "claimed_at"),
    "thirds": ("target", "authoritative_score", "completion_status", "qualified", "claimed", "claimed_at"),
}


def publication_context(now=None):
    """Reuse the accepted reference's named-zone week/release calculation."""
    instant = utc_instant(now)
    start = _week_start(instant)
    release = datetime.combine(start, time(13, 30), CENTRAL)
    return {
        "week_start": start.isoformat(), "week_end": (start + timedelta(days=6)).isoformat(),
        "recap_week_start": (start - timedelta(days=7)).isoformat(),
        "recap_week_end": (start - timedelta(days=1)).isoformat(),
        "publication_at": release.isoformat(), "available": instant >= release,
    }


def _fields(value, names):
    return {name: value[name] for name in names if name in value}


def _homework(spec):
    # Never forward arbitrary stored JSON, future private fields or generation
    # inputs. Unknown specifications remain stored but unavailable to this reader.
    if not isinstance(spec, dict) or spec.get("schema_version") != "weekly_homework_v1":
        return {"status": "unavailable", "reason": "unsupported_saved_specification"}
    names = ("schema_version", "recipe_id", "title", "presentation", "support")
    instructions, blocks = spec.get("instructions"), spec.get("suggested_blocks")
    if (not all(isinstance(spec.get(n), str) and len(spec[n]) <= 500 for n in names)
            or not isinstance(instructions, list) or len(instructions) > 12
            or not all(isinstance(step, str) and len(step) <= 1000 for step in instructions)
            or not isinstance(blocks, dict)
            or not all(type(blocks.get(n)) is int and 0 < blocks[n] <= 120
                       for n in ("count", "minutes_each"))):
        return {"status": "unavailable", "reason": "unsupported_saved_specification"}
    return {"status": "saved", **_fields(spec, names), "instructions": list(instructions),
            "suggested_blocks": _fields(blocks, ("count", "minutes_each"))}


def _saved_plan(session, plan):
    result = plan_payload(plan)
    if result is not None:
        assignments = session.scalars(select(WeeklyInstrumentPlan).where(
            WeeklyInstrumentPlan.plan_id == plan.id).order_by(WeeklyInstrumentPlan.slot_no)).all()
        result["instruments"] = [{"slot_no": item.slot_no, "instrument": item.instrument,
            "started_at": _utc(item.started_at).isoformat(), "homework": _homework(item.homework_spec)}
            for item in assignments]
    return result


def _tasks(session, profile_id, plan, instant):
    if plan is None:
        return None
    states = task_states(session, profile_id, plan, now=instant)
    result = {lane: _fields(states[lane], fields) for lane, fields in TASK_FIELDS.items()}
    result["homework"]["assignments"] = [_fields(item, (
        "slot_no", "instrument", "practice_seconds", "target_seconds", "qualified"))
        for item in states["homework"]["assignments"]]
    return result


def _receipt(row):
    return {"reward_type": row.reward_type, "amount": row.amount,
            "earned_at": _utc(row.created_at).isoformat()}


def _weekly_receipts(session, profile_id, week, instant):
    # Exact owned identities and amounts; no arbitrary category/metadata or
    # request-key mappings from other reward systems are exposed.
    sources = {"monday_bonus": (f"weekly-monday:{week}", "dandelion", 75),
               "clue": (f"clover-caper:{week}:clue", "caper_clue", 1)}
    sources.update({lane: (f"clover-caper:{week}:jar:{number}", "pickled_carrot", 1)
                    for lane, number in (("logging", 1), ("practice_minutes", 2), ("homework", 3))})
    result = {}
    for name, (source, kind, amount) in sources.items():
        row = session.scalar(select(RewardGrant).where(
            RewardGrant.profile_id == profile_id, RewardGrant.source_key == source,
            RewardGrant.reward_type == kind, RewardGrant.amount == amount,
            RewardGrant.created_at <= instant))
        result[name] = _receipt(row) if row is not None else None
    return result


def _recognition(session, profile_id, week_start, instant):
    """Personal/frozen-Team honors only: no names, live rankings or rating scores.

    Keep competitions separate rather than selecting the latest season or
    merging ordinary/persistent-Team histories. Historical rule labels are read,
    never treated as permission to reconstruct missing rows.
    """
    weeks = session.scalars(select(ContestWeek).where(
        ContestWeek.week_start == week_start).order_by(ContestWeek.id)
        .limit(MAX_COMPETITIONS + 1)).all()
    if len(weeks) > MAX_COMPETITIONS:
        return {"status": "unavailable", "reason": "competition_limit", "competitions": []}
    if not weeks:
        return {"status": "pending", "reason": "no_saved_contest_week", "competitions": []}
    competitions = []
    for week in weeks:
        item = {"contest_week_id": week.id, "status": "pending", "honors": []}
        competitions.append(item)
        if week.status != "finalized" or week.finalized_at is None or _utc(week.finalized_at) > instant:
            continue
        item.update(status="finalized", finalized_at=_utc(week.finalized_at).isoformat(),
                    rules_version=week.finalizer_rules_version)
        roster = select(TeamWeekMembershipSnapshot.team_id).where(
            TeamWeekMembershipSnapshot.contest_week_id == week.id,
            TeamWeekMembershipSnapshot.profile_id == profile_id,
            TeamWeekMembershipSnapshot.snapshot_at <= week.finalized_at)
        owned = or_(and_(ContestResult.subject_type == "student", ContestResult.profile_id == profile_id),
                    and_(ContestResult.subject_type == "team", ContestResult.team_id.in_(roster),
                         week.team_roster_frozen_at is not None))
        rows = session.execute(select(ContestResult, Contest).join(
            Contest, Contest.id == ContestResult.contest_id).where(
                ContestResult.contest_week_id == week.id, owned,
                ContestResult.created_at <= instant,
            ).order_by(ContestResult.id).limit(MAX_RECOGNITIONS + 1)).all()
        if len(rows) > MAX_RECOGNITIONS:
            item.update(status="unavailable", reason="recognition_limit")
            continue
        if not session.scalar(select(ContestResult.id).where(
                ContestResult.contest_week_id == week.id, ContestResult.created_at <= instant).limit(1)):
            item.update(status="unavailable", reason="saved_results_missing")
            continue
        for result, contest in rows:
            grants = session.scalars(select(RewardGrant).where(
                RewardGrant.profile_id == profile_id, RewardGrant.contest_result_id == result.id,
                RewardGrant.reward_type.in_(("dandelion", "crown_win")),
                RewardGrant.amount > 0, RewardGrant.created_at <= instant,
            ).order_by(RewardGrant.id).limit(17)).all()
            item["honors"].append({"contest_key": contest.key, "division": result.division,
                "subject_type": result.subject_type, "rank": result.rank, "medal": result.medal,
                "rewards_status": "recorded" if len(grants) <= 16 else "unavailable",
                "rewards": [_receipt(grant) for grant in grants] if len(grants) <= 16 else []})
    status = "pending" if any(c["status"] == "pending" for c in competitions) else (
        "unavailable" if any(c["status"] == "unavailable" for c in competitions) else "finalized")
    return {"status": status, "scope": "own_student_and_frozen_team_honors", "competitions": competitions}


def newsletter_payload(session, profile, *, now=None):
    instant = utc_instant(now)
    context = publication_context(instant)
    result = {"context": context, "recap": None, "current": None}
    if not context["available"]:
        return result
    start = _week_start(instant)
    previous = start - timedelta(days=7)
    plan = load_current_plan(session, profile.id, now=instant)
    # Select the calendar date, not UTC-minus-168-hours across a DST change.
    old = session.scalar(select(WeeklyPlan).where(
        WeeklyPlan.profile_id == profile.id, WeeklyPlan.week_start == previous))
    # Active instrument is canonicalized without persisting a preference.
    from .weekly_homework import weekly_instrument
    try:
        instrument = weekly_instrument(profile.instrument)
    except ValueError:
        instrument = None
    result["current"] = {
        "plan": _saved_plan(session, plan), "game_mode": profile.game_mode,
        "active_instrument": instrument, "start_context": start_context(profile, plan),
        "plan_actions_available": can_use_feature(session, profile.id, "weekly_plans"),
        "progress": current_general_progress(session, profile.id, now=instant),
        "tasks": _tasks(session, profile.id, plan, instant),
        "monday_bonus": _weekly_receipts(session, profile.id, start, instant)["monday_bonus"],
    }
    result["recap"] = {
        "plan": _saved_plan(session, old),
        "practice": {"status": "recorded", "provenance": "retained_charts_as_of_read_not_frozen",
                     **current_general_progress(session, profile.id, now=instant, week_start=previous)},
        "tasks": _tasks(session, profile.id, old, instant),
        "task_provenance": "source_week_qualification_and_durable_claims_as_of_read",
        "clue": _fields(clue_state(session, old), ("claimed", "claimed_at")) if old else None,
        "receipts": _weekly_receipts(session, profile.id, previous, instant),
        "recognition": _recognition(session, profile.id, previous, instant),
        "top_20": {"status": "unavailable", "reason": "no_frozen_top_20"},
        "practice_rating_history": {"status": "unavailable", "reason": "no_frozen_rating_history"},
        "directors_choice": {"status": "unavailable", "reason": "no_saved_directors_choice_contract"},
    }
    return result
