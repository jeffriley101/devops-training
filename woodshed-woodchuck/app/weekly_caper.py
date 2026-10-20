"""Explicit owner-scoped Caper claims; evidence reads never settle rewards."""
import hashlib
import sqlite3

from fastapi import HTTPException
from sqlalchemy import String, cast, select

from .feature_access import require_feature
from .models import RewardGrant
from .weekly import utc_instant
from .weekly_models import WeeklyPlan, WeeklyTaskClaim
from .weekly_plans import _lock_profile, _week_start, load_current_plan, thirds_progress
from .weekly_rewards import grant, wallet
from .weekly_tasks import _utc, board_progress, general_task_progress, homework_progress

LANES = ("logging", "practice_minutes", "homework", "board", "thirds")
JAR_LANES = {"logging": 1, "practice_minutes": 2, "homework": 3}
HISTORY_PAGE_SIZE = 12


def _source(plan, suffix):
    return f"clover-caper:{plan.week_start.isoformat()}:{suffix}"


def _receipt(session, profile_id, source, kind):
    return session.scalar(select(RewardGrant).where(
        RewardGrant.profile_id == profile_id, RewardGrant.source_key == source,
        RewardGrant.reward_type == kind, RewardGrant.amount == 1))


def _claim_plan(session, profile, plan_id, *, now=None, authorize=None):
    # SQLite writer fence / PostgreSQL profile FOR UPDATE precedes plan/receipt
    # decisions. Refresh identity, session version, eligibility and feature gate.
    profile = _lock_profile(session, profile)
    if authorize is not None:
        authorize(session)
    instant = utc_instant(now)
    plan = session.scalar(select(WeeklyPlan).where(
        WeeklyPlan.id == plan_id, WeeklyPlan.profile_id == profile.id,
        WeeklyPlan.week_start <= _week_start(instant), WeeklyPlan.issued_at <= instant,
    ).with_for_update().execution_options(populate_existing=True))
    if plan is None:
        raise HTTPException(404, "Weekly plan unavailable.")
    return plan, instant


def _lane_receipt(session, plan, lane):
    if lane in JAR_LANES:
        return _receipt(session, plan.profile_id, _source(plan, f"jar:{JAR_LANES[lane]}"), "pickled_carrot")
    return session.get(WeeklyTaskClaim, (plan.id, lane))


def _claimed_at(receipt):
    if receipt is None:
        return None
    value = receipt.created_at if isinstance(receipt, RewardGrant) else receipt.claimed_at
    return _utc(value).isoformat()


def task_states(session, profile_id, plan, *, now=None):
    # These helpers enforce ownership and use the source week's evidence window.
    result = general_task_progress(session, profile_id, plan, now=now)
    result.update(homework=homework_progress(session, profile_id, plan, now=now),
                  board=board_progress(session, profile_id, plan, now=now),
                  thirds=thirds_progress(session, profile_id, plan, now=now))
    for lane, progress in result.items():
        receipt = _lane_receipt(session, plan, lane)
        claimed = receipt is not None
        # A durable claim records previously proven qualification even if the
        # student later deletes a chart. Never revoke or recreate its reward.
        progress.update(qualified=bool(progress["qualified"] or claimed),
                        claimed=claimed, claimable=bool(progress["qualified"] and not claimed),
                        claimed_at=_claimed_at(receipt))
    return result


def clue_state(session, plan):
    receipt = _receipt(session, plan.profile_id, _source(plan, "clue"), "caper_clue")
    all_claimed = all(_lane_receipt(session, plan, lane) is not None for lane in LANES)
    return {"claimed": receipt is not None, "claimable": all_claimed and receipt is None,
            "claimed_at": _claimed_at(receipt)}


def _grant_receipt(session, plan, suffix, kind, instant):
    grant(session, plan.profile_id, _source(plan, suffix), kind, now=instant)
    session.flush()
    receipt = _receipt(session, plan.profile_id, _source(plan, suffix), kind)
    if receipt is None:
        raise HTTPException(409, "This saved reward is unavailable.")
    return receipt


def claim_task(session, profile, plan_id, lane, *, now=None, authorize=None):
    if lane not in LANES:
        raise HTTPException(422, "Choose one of the five weekly tasks.")
    plan, instant = _claim_plan(session, profile, plan_id, now=now, authorize=authorize)
    state = task_states(session, profile.id, plan, now=instant)[lane]
    if not state["claimed"]:
        if not state["qualified"]:
            raise HTTPException(409, "This weekly task has not qualified.")
        if lane in JAR_LANES:
            receipt = _grant_receipt(session, plan, f"jar:{JAR_LANES[lane]}", "pickled_carrot", instant)
        else:
            receipt = WeeklyTaskClaim(plan_id=plan.id, lane=lane, claimed_at=instant)
            session.add(receipt)
            session.flush()
        state.update(claimed=True, claimable=False, claimed_at=_claimed_at(receipt))
    # Jar/Clover balances are the protected RewardGrant ledger itself. There is
    # no second wallet column to synchronize, nor an XP/Dandelion state write.
    return {"lane": lane, **state,
            "reward": {"type": "pickled_carrot", "amount": 1} if lane in JAR_LANES else None}


def clover_state(session, profile_id):
    balances = wallet(session, profile_id)
    clues = balances["caper_clue"]
    sources = set(session.scalars(select(RewardGrant.source_key).where(
        RewardGrant.profile_id == profile_id, RewardGrant.reward_type == "white_clover",
        RewardGrant.amount == 1, RewardGrant.source_key.startswith("clover-caper:clues:"))))
    next_threshold = 7
    while f"clover-caper:clues:{next_threshold}" in sources:
        next_threshold += 7
    return {"clue_count": clues, "clue_progress": min(7, max(0, clues - next_threshold + 7)),
            "clover_claimable": clues >= next_threshold, "next_clover_threshold": next_threshold,
            "white_clover_balance": balances["white_clover"]}


def claim_clue(session, profile, plan_id, *, now=None, authorize=None):
    plan, instant = _claim_plan(session, profile, plan_id, now=now, authorize=authorize)
    state = clue_state(session, plan)
    if not state["claimed"]:
        if not state["claimable"]:
            raise HTTPException(409, "Claim all five weekly tasks before claiming the clue.")
        receipt = _grant_receipt(session, plan, "clue", "caper_clue", instant)
        state.update(claimed=True, claimable=False, claimed_at=_claimed_at(receipt))
    # Explicitly no Clover grant here, including on clues seven and fourteen.
    return {"clue": state, **clover_state(session, profile.id)}


def claim_clover(session, profile, request_key, *, now=None, authorize=None):
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 64:
        raise HTTPException(422, "A durable Clover request key is required.")
    profile = _lock_profile(session, profile)
    if authorize is not None:
        authorize(session)
    instant = utc_instant(now)
    # A retry key selects a saved result, never a threshold or reward amount.
    # Store only its digest on the existing grant; no pending-entitlement table.
    category = "caper-claim:" + hashlib.sha256(request_key.encode()).hexdigest()
    receipt = session.scalar(select(RewardGrant).where(
        RewardGrant.profile_id == profile.id, RewardGrant.reward_type == "white_clover",
        RewardGrant.amount == 1, RewardGrant.source_key.startswith("clover-caper:clues:"),
        RewardGrant.category_key == category))
    if receipt is None:
        state = clover_state(session, profile.id)
        if not state["clover_claimable"]:
            raise HTTPException(409, "No earned Clover is waiting to be claimed.")
        source = f"clover-caper:clues:{state['next_clover_threshold']}"
        if not grant(session, profile.id, source, "white_clover", now=instant):
            raise HTTPException(409, "Refresh the saved Clover claim.")
        receipt = next(row for row in session.new if isinstance(row, RewardGrant)
                       and row.profile_id == profile.id and row.source_key == source
                       and row.reward_type == "white_clover")
        receipt.category_key = category
        session.flush()
    return {"claimed": True, "claimed_at": _claimed_at(receipt),
            "reward": {"type": "white_clover", "amount": 1}, **clover_state(session, profile.id)}


def _plan_claims(session, profile_id, plan, instant):
    return {"plan_id": plan.id, "week_start": plan.week_start.isoformat(),
            "tasks": task_states(session, profile_id, plan, now=instant),
            "clue": clue_state(session, plan)}


def claims_payload(session, profile_id, *, now=None, before_plan_id=None):
    require_feature(session, profile_id, "weekly_plans")
    instant = utc_instant(now)
    current = load_current_plan(session, profile_id, now=instant)
    # Keyset pagination keeps old entitlements reachable without an unlimited
    # history response. Cursor IDs select a page only; ownership is always SQL-scoped.
    clue_source = "clover-caper:" + cast(WeeklyPlan.week_start, String) + ":clue"
    already_claimed = select(RewardGrant.id).where(
        RewardGrant.profile_id == profile_id, RewardGrant.source_key == clue_source,
        RewardGrant.reward_type == "caper_clue", RewardGrant.amount == 1).exists()
    query = select(WeeklyPlan).where(WeeklyPlan.profile_id == profile_id,
        WeeklyPlan.week_start < _week_start(instant), WeeklyPlan.issued_at <= instant, ~already_claimed)
    if before_plan_id is not None:
        query = query.where(WeeklyPlan.id < before_plan_id)
    rows = session.scalars(query.order_by(WeeklyPlan.id.desc()).limit(HISTORY_PAGE_SIZE + 1)).all()
    page = rows[:HISTORY_PAGE_SIZE]
    return {"current_plan": _plan_claims(session, profile_id, current, instant) if current else None,
            "historical_plans": [_plan_claims(session, profile_id, plan, instant) for plan in page],
            "next_before_plan_id": page[-1].id if len(rows) > HISTORY_PAGE_SIZE else None,
            **clover_state(session, profile_id)}


def is_claim_unique_race(error, dialect):
    """Retry only the two claim/grant identities, never arbitrary integrity errors."""
    if dialect == "postgresql":
        return (getattr(error.orig, "sqlstate", None) == "23505" and
                getattr(getattr(error.orig, "diag", None), "constraint_name", None) in
                {"pk_weekly_task_claim", "uq_reward_grant_profile_source_type"})
    if dialect == "sqlite" and isinstance(error.orig, sqlite3.IntegrityError):
        return str(error.orig) in {
            "UNIQUE constraint failed: weekly_task_claims.plan_id, weekly_task_claims.lane",
            "UNIQUE constraint failed: reward_grants.profile_id, reward_grants.source_key, reward_grants.reward_type",
        }
    return False
