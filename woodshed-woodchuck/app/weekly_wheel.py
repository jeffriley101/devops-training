"""Explicit funded plays and one-rotation continuations; caller owns commit."""
import secrets
import sqlite3
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update

from .age_privacy import require_eligible
from .economy import economy_payload, lock_state
from .feature_access import require_feature
from .models import CampPointAward, RewardGrant, WoodchuckProfile, WoodchuckState
from .weekly import CENTRAL, utc_instant
from .weekly_models import WeeklyWheelPlay
from .weekly_rewards import grant, wallet as ledger_wallet

WHEEL_WEIGHTS = (("activity", 2950), ("dandelion", 2000), ("xp", 2000),
                 ("spin_again", 2000), ("ufo", 800), ("dandelion_1000", 200),
                 ("white_clover", 50))
COST = 3
DAILY_LIMIT = 9


def outcome_for_bucket(bucket):
    if type(bucket) is not int or not 0 <= bucket < 10000:
        raise ValueError("Wheel bucket must be an integer from 0 through 9999.")
    for outcome, weight in WHEEL_WEIGHTS:
        if bucket < weight:
            return outcome
        bucket -= weight
    raise AssertionError("Wheel weights must total 10000.")


def wallet(session, profile_id):
    # Preserve historical pants entitlements without generating new ones.
    return {**ledger_wallet(session, profile_id), "pants_choice": int(session.scalar(
        select(func.coalesce(func.sum(RewardGrant.amount), 0)).where(
            RewardGrant.profile_id == profile_id, RewardGrant.reward_type == "pants_choice")) or 0)}


def play_payload(session, play):
    return {"id": play.id, "funded_day": play.funded_day.isoformat(),
            "daily_ordinal": play.daily_ordinal, "outcomes": list(play.outcomes),
            "next_rotation": len(play.outcomes) + 1 if play.completed_at is None else None,
            "complete": play.completed_at is not None, "wallet": wallet(session, play.profile_id)}


def owned_play(session, profile_id, play_id):
    play = session.scalar(select(WeeklyWheelPlay).where(
        WeeklyWheelPlay.profile_id == profile_id, WeeklyWheelPlay.id == play_id))
    if play is None:
        raise HTTPException(404, "That Wheel play is unavailable.")
    return play


def status_payload(session, profile_id, *, play=None, now=None):
    day = utc_instant(now).astimezone(CENTRAL).date()
    count = session.scalar(select(func.count()).select_from(WeeklyWheelPlay).where(
        WeeklyWheelPlay.profile_id == profile_id, WeeklyWheelPlay.funded_day == day)) or 0
    if play is None:
        play = session.scalar(select(WeeklyWheelPlay).where(WeeklyWheelPlay.profile_id == profile_id)
            .order_by(WeeklyWheelPlay.completed_at.is_not(None),
                      WeeklyWheelPlay.funded_at.desc(), WeeklyWheelPlay.id.desc()).limit(1))
    state = session.get(WoodchuckState, profile_id)
    return {"play": play_payload(session, play) if play else None,
            "wallet": wallet(session, profile_id), "funded_day": day.isoformat(),
            "funded_plays": count, "remaining_plays": max(0, DAILY_LIMIT - count),
            "cost": COST, "daily_limit": DAILY_LIMIT,
            "weights": [{"outcome": key, "weight": weight} for key, weight in WHEEL_WEIGHTS],
            "credits": economy_payload(state)["credits"] if state else 0,
            "state_revision": state.revision if state else None}


def _lock_player(session, profile, authorize):
    # Match current profile -> state ordering, independently of the plan gate.
    profile_id, expected_version = profile.id, profile.session_version
    require_feature(session, profile_id, "weekly_wheel")
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(WoodchuckProfile).where(WoodchuckProfile.id == profile_id).values(
            id=WoodchuckProfile.id, updated_at=WoodchuckProfile.updated_at))
    profile = session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == profile_id)
        .with_for_update().execution_options(populate_existing=True))
    if profile is None or profile.status != "active" or profile.session_version != expected_version:
        raise HTTPException(401, "Student sign-in is required.")
    require_eligible(session, profile_id)
    require_feature(session, profile_id, "weekly_wheel")
    authorize(session)
    return profile


def _required_grant(session, profile_id, source, kind, amount, instant):
    if not grant(session, profile_id, source, kind, amount, now=instant):
        # Existing play retries return before this point. A missing/inconsistent
        # write must never be acknowledged as a newly settled rotation.
        raise HTTPException(409, "The saved Wheel evidence needs review.")


def _rotate(session, play, instant):
    outcome = outcome_for_bucket(secrets.randbelow(10000))
    play.outcomes = [*play.outcomes, outcome]
    if outcome == "spin_again":
        return
    source = f"weekly-wheel:{play.id}:{len(play.outcomes)}"
    amounts = {"activity": 14, "dandelion": 400, "xp": 1400, "ufo": 1,
               "dandelion_1000": 1000, "white_clover": 1}
    kind = "dandelion" if outcome == "dandelion_1000" else outcome
    _required_grant(session, play.profile_id, source, kind, amounts[outcome], instant)
    if kind == "dandelion":
        from .contests import _add_dandelion
        _add_dandelion(session, play.profile_id, amounts[outcome])
    elif kind == "activity":
        from .contests import _active_team_id_for_event
        session.add(CampPointAward(profile_id=play.profile_id, activity_type="wheel-prize",
            points_awarded=14, duplicate_key=source, occurred_at=instant, created_at=instant,
            team_id=_active_team_id_for_event(session, play.profile_id, instant)))
    play.completed_at = instant


def fund_play(session, profile, request_key, *, authorize, now=None):
    if type(request_key) is not str or not request_key.strip() or len(request_key) > 64:
        raise HTTPException(422, "A durable Wheel request key is required.")
    profile = _lock_player(session, profile, authorize)
    instant = utc_instant(now)  # Chicago day is derived after the lock wait.
    existing = session.scalar(select(WeeklyWheelPlay).where(
        WeeklyWheelPlay.profile_id == profile.id, WeeklyWheelPlay.request_key == request_key))
    if existing is not None:
        return existing
    pending = session.scalar(select(WeeklyWheelPlay).where(
        WeeklyWheelPlay.profile_id == profile.id, WeeklyWheelPlay.completed_at.is_(None)))
    if pending is not None:
        raise HTTPException(409, {"message": "Continue your saved free rotation first.",
                                  "play": play_payload(session, pending)})
    day = instant.astimezone(CENTRAL).date()
    count = session.scalar(select(func.count()).select_from(WeeklyWheelPlay).where(
        WeeklyWheelPlay.profile_id == profile.id, WeeklyWheelPlay.funded_day == day)) or 0
    if count >= DAILY_LIMIT:
        raise HTTPException(409, "Nine funded plays have already been used today.")
    if wallet(session, profile.id)["pickled_carrot"] < COST:
        raise HTTPException(409, "Three Pickled Carrot jars are needed for one play.")
    state = lock_state(session, profile.id)
    play = WeeklyWheelPlay(id=uuid4().hex, profile_id=profile.id, request_key=request_key,
        funded_day=day, funded_at=instant, daily_ordinal=count + 1, outcomes=[])
    session.add(play)
    _required_grant(session, profile.id, f"weekly-wheel:{play.id}:payment", "pickled_carrot", -COST, instant)
    _rotate(session, play, instant)
    state.revision += 1
    session.flush()
    return play


def continue_play(session, profile, play_id, rotation, *, authorize, now=None):
    if type(rotation) is not int or rotation < 1:
        raise HTTPException(422, "A valid rotation number is required.")
    profile = _lock_player(session, profile, authorize)
    play = owned_play(session, profile.id, play_id)
    if rotation <= len(play.outcomes):
        return play
    if (rotation != len(play.outcomes) + 1 or play.completed_at is not None
            or not play.outcomes or play.outcomes[-1] != "spin_again"):
        raise HTTPException(409, "That rotation is unavailable.")
    state = lock_state(session, profile.id)
    _rotate(session, play, utc_instant(now))
    state.revision += 1
    session.flush()
    return play


def is_wheel_unique_race(error, dialect):
    if dialect == "postgresql":
        return (getattr(error.orig, "sqlstate", None) == "23505" and
                getattr(getattr(error.orig, "diag", None), "constraint_name", None) in
                {"uq_weekly_wheel_request", "uq_weekly_wheel_day_slot", "uq_reward_grant_profile_source_type"})
    if dialect == "sqlite" and isinstance(error.orig, sqlite3.IntegrityError):
        return str(error.orig) in {
            "UNIQUE constraint failed: weekly_wheel_plays.profile_id, weekly_wheel_plays.request_key",
            "UNIQUE constraint failed: weekly_wheel_plays.profile_id, weekly_wheel_plays.funded_day, weekly_wheel_plays.daily_ordinal",
            "UNIQUE constraint failed: reward_grants.profile_id, reward_grants.source_key, reward_grants.reward_type",
        }
    return False
