from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from secrets import token_urlsafe
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .arcade_scores import MAX_ARCADE_SCORE, arcade_score_payload, record_arcade_high_score
from .models import ArcadePlaySession, WoodchuckProfile, WoodchuckState
from .xp import plunge_best_payload, record_plunge_best_score


ARCADE_TIMEZONE = ZoneInfo("America/Chicago")
ARCADE_ENTRY_COST = 1
DAILY_REWARDED_PLAY_LIMIT = 10
ARCADE_PLAY_GAME_KEYS = frozenset({
    "plunge-burrow",
    "blue",
    "radio-tuner",
    "wheel-of-woodchuck",
    "scale-keyboard",
    "thirds",
    "dressed-to-the-nines",
    "interval-basic-training",
})

# Conservative first-pass tiers based on each game's existing duration and
# scoring constants. Values are intentionally centralized for future tuning.
ARCADE_PAYOUT_THRESHOLDS: dict[str, tuple[tuple[int, int], ...]] = {
    "plunge-burrow": ((10, 1), (25, 2), (50, 3), (100, 5)),
    "blue": ((30, 1), (80, 2), (150, 3), (250, 5)),
    "radio-tuner": ((100, 1), (300, 2), (700, 3), (1500, 5)),
    "wheel-of-woodchuck": ((1000, 1), (2500, 2), (5000, 3), (9000, 5)),
    "scale-keyboard": ((800, 1), (1800, 2), (3000, 3), (5000, 5)),
    "thirds": ((3, 1), (6, 2), (9, 3), (12, 5)),
    "dressed-to-the-nines": ((3, 1), (6, 2), (9, 3), (12, 5)),
    "interval-basic-training": ((3, 1), (6, 2), (9, 3), (12, 5)),
}


class InsufficientArcadeBalanceError(ValueError):
    pass


class ArcadePlayConflictError(ValueError):
    pass


@dataclass(frozen=True)
class ArcadePlayStartResult:
    play: ArcadePlaySession
    balance: int
    reward_eligible: bool
    completed_reward_plays: int
    state_revision: int


def validate_arcade_play_game_key(game_key: str) -> str:
    if game_key not in ARCADE_PLAY_GAME_KEYS:
        raise ValueError("That Arcade game is unavailable.")
    return game_key


def payout_for_score(game_key: str, score: int) -> int:
    key = validate_arcade_play_game_key(game_key)
    if type(score) is not int or not 0 <= score <= MAX_ARCADE_SCORE:
        raise ValueError("A valid Arcade score is required.")
    payout = 0
    for minimum_score, tier_payout in ARCADE_PAYOUT_THRESHOLDS[key]:
        if score < minimum_score:
            break
        payout = tier_payout
    return payout


def _utc_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _central_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    local_day = now.astimezone(ARCADE_TIMEZONE).date()
    start = datetime.combine(local_day, time.min, tzinfo=ARCADE_TIMEZONE)
    end = datetime.combine(date.fromordinal(local_day.toordinal() + 1), time.min, tzinfo=ARCADE_TIMEZONE)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _state_for_update(session: Session, profile_id: int) -> WoodchuckState:
    state = session.scalar(
        select(WoodchuckState)
        .where(WoodchuckState.profile_id == profile_id)
        .with_for_update()
    )
    if state is None:
        state = WoodchuckState(profile_id=profile_id, state_json={}, revision=0)
        session.add(state)
        session.flush()
    return state


def _balance(state: WoodchuckState) -> int:
    value = ((state.state_json or {}).get("progress") or {}).get("credits", 0)
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, value)


def _set_balance(state: WoodchuckState, value: int) -> None:
    payload = deepcopy(state.state_json or {})
    progress = dict(payload.get("progress") or {})
    progress["credits"] = max(0, value)
    payload["progress"] = progress
    state.state_json = payload
    state.revision += 1


def _completed_plays_today(
    session: Session,
    *,
    profile_id: int,
    game_key: str,
    now: datetime,
    exclude_play_id: int | None = None,
) -> int:
    start, end = _central_day_bounds(now)
    statement = select(func.count(ArcadePlaySession.id)).where(
        ArcadePlaySession.profile_id == profile_id,
        ArcadePlaySession.game_key == game_key,
        ArcadePlaySession.completed_at >= start,
        ArcadePlaySession.completed_at < end,
    )
    if exclude_play_id is not None:
        statement = statement.where(ArcadePlaySession.id != exclude_play_id)
    return int(session.scalar(statement) or 0)


def arcade_play_status(
    session: Session,
    *,
    profile_id: int,
    game_key: str,
    now: datetime | None = None,
) -> dict[str, object]:
    key = validate_arcade_play_game_key(game_key)
    timestamp = _utc_now(now)
    profile_exists = session.scalar(select(WoodchuckProfile.id).where(
        WoodchuckProfile.id == profile_id,
        WoodchuckProfile.status == "active",
    ))
    if profile_exists is None:
        raise ValueError("The signed-in Woodchuck profile is unavailable.")
    state = session.get(WoodchuckState, profile_id)
    completed = _completed_plays_today(
        session, profile_id=profile_id, game_key=key, now=timestamp
    )
    return {
        "game_key": key,
        "balance": _balance(state) if state is not None else 0,
        "state_revision": state.revision if state is not None else 0,
        "entry_cost": ARCADE_ENTRY_COST,
        "completed_reward_plays": completed,
        "daily_reward_limit": DAILY_REWARDED_PLAY_LIMIT,
        "reward_eligible": completed < DAILY_REWARDED_PLAY_LIMIT,
    }


def start_arcade_play(
    session: Session,
    *,
    profile_id: int,
    game_key: str,
    now: datetime | None = None,
) -> ArcadePlayStartResult:
    key = validate_arcade_play_game_key(game_key)
    timestamp = _utc_now(now)
    profile_exists = session.scalar(
        select(WoodchuckProfile.id).where(
            WoodchuckProfile.id == profile_id,
            WoodchuckProfile.status == "active",
        ).with_for_update()
    )
    if profile_exists is None:
        raise ValueError("The signed-in Woodchuck profile is unavailable.")

    state = _state_for_update(session, profile_id)
    current_balance = _balance(state)
    if current_balance < ARCADE_ENTRY_COST:
        raise InsufficientArcadeBalanceError(
            "You need 1 dandelion to start a new Arcade game."
        )
    new_balance = current_balance - ARCADE_ENTRY_COST
    _set_balance(state, new_balance)
    completed = _completed_plays_today(
        session, profile_id=profile_id, game_key=key, now=timestamp
    )
    play = ArcadePlaySession(
        profile_id=profile_id,
        game_key=key,
        play_token=token_urlsafe(32),
        started_at=timestamp,
        entry_cost=ARCADE_ENTRY_COST,
    )
    session.add(play)
    session.flush()
    return ArcadePlayStartResult(
        play=play,
        balance=new_balance,
        reward_eligible=completed < DAILY_REWARDED_PLAY_LIMIT,
        completed_reward_plays=completed,
        state_revision=state.revision,
    )


def _score_payload(
    session: Session, *, profile_id: int, game_key: str
) -> dict[str, object]:
    if game_key == "plunge-burrow":
        return plunge_best_payload(session, profile_id=profile_id)
    return arcade_score_payload(session, profile_id=profile_id, game_key=game_key)


def complete_arcade_play(
    session: Session,
    *,
    profile_id: int,
    play_token: str,
    score: int,
    now: datetime | None = None,
) -> dict[str, object]:
    if type(score) is not int or not 0 <= score <= MAX_ARCADE_SCORE:
        raise ValueError("A valid Arcade score is required.")
    timestamp = _utc_now(now)
    profile_exists = session.scalar(
        select(WoodchuckProfile.id).where(
            WoodchuckProfile.id == profile_id,
            WoodchuckProfile.status == "active",
        ).with_for_update()
    )
    if profile_exists is None:
        raise ValueError("The signed-in Woodchuck profile is unavailable.")
    play = session.scalar(
        select(ArcadePlaySession)
        .where(ArcadePlaySession.play_token == play_token)
        .with_for_update()
    )
    if play is None or play.profile_id != profile_id:
        raise ValueError("That Arcade play is unavailable.")
    if play.completed_at is not None:
        if play.submitted_score != score:
            raise ArcadePlayConflictError(
                "That Arcade play was already completed with a different score."
            )
        state = session.get(WoodchuckState, profile_id)
        return {
            **_score_payload(session, profile_id=profile_id, game_key=play.game_key),
            "game_key": play.game_key,
            "play_token": play.play_token,
            "score": int(play.submitted_score),
            "payout": int(play.payout or 0),
            "balance": _balance(state) if state is not None else 0,
            "state_revision": state.revision if state is not None else 0,
            "already_completed": True,
        }

    completed_before = _completed_plays_today(
        session,
        profile_id=profile_id,
        game_key=play.game_key,
        now=timestamp,
        exclude_play_id=play.id,
    )
    reward_eligible = completed_before < DAILY_REWARDED_PLAY_LIMIT
    payout = payout_for_score(play.game_key, score) if reward_eligible else 0
    state = _state_for_update(session, profile_id)
    new_balance = _balance(state) + payout
    if payout:
        _set_balance(state, new_balance)

    if play.game_key == "plunge-burrow":
        _best, updated = record_plunge_best_score(
            session, profile_id=profile_id, score=score
        )
    else:
        _best, updated = record_arcade_high_score(
            session,
            profile_id=profile_id,
            game_key=play.game_key,
            score=score,
        )
    play.completed_at = timestamp
    play.submitted_score = score
    play.payout = payout
    play.reward_granted_at = timestamp if payout else None
    session.flush()
    return {
        **_score_payload(session, profile_id=profile_id, game_key=play.game_key),
        "game_key": play.game_key,
        "play_token": play.play_token,
        "score": score,
        "payout": payout,
        "balance": new_balance,
        "state_revision": state.revision,
        "reward_eligible": reward_eligible,
        "daily_reward_limit": DAILY_REWARDED_PLAY_LIMIT,
        "already_completed": False,
        "updated": updated,
    }
