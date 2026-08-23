from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    CrownAward,
    LoginStreak,
    RewardGrant,
    WoodchuckProfile,
    WoodchuckState,
)


LOGIN_STREAK_TIMEZONE = ZoneInfo("America/Chicago")
LOGIN_STREAK_CROWN_CATEGORY = "weekly-login-streak"
LOGIN_STREAK_CROWN_INTERVAL = 7


def central_login_date(now: datetime | None = None) -> date:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(LOGIN_STREAK_TIMEZONE).date()


def _pending_row(session: Session, model: type, **values: object):
    return next((
        row
        for row in session.new
        if isinstance(row, model)
        and all(getattr(row, field) == value for field, value in values.items())
    ), None)


def _state_for_update(session: Session, profile_id: int) -> WoodchuckState:
    state = _pending_row(session, WoodchuckState, profile_id=profile_id)
    if state is None:
        state = session.scalar(
            select(WoodchuckState)
            .where(WoodchuckState.profile_id == profile_id)
            .with_for_update()
        )
    if state is None:
        state = WoodchuckState(profile_id=profile_id, state_json={}, revision=0)
        session.add(state)
    return state


def _credits(state: WoodchuckState) -> int:
    value = ((state.state_json or {}).get("progress") or {}).get("credits", 0)
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return max(0, value)


def _reward_grant(
    session: Session,
    *,
    profile_id: int,
    source_key: str,
    reward_type: str,
) -> RewardGrant | None:
    pending = _pending_row(
        session,
        RewardGrant,
        profile_id=profile_id,
        source_key=source_key,
        reward_type=reward_type,
    )
    if pending is not None:
        return pending
    return session.scalar(select(RewardGrant).where(
        RewardGrant.profile_id == profile_id,
        RewardGrant.source_key == source_key,
        RewardGrant.reward_type == reward_type,
    ))


def _award_weekly_crown(
    session: Session,
    *,
    profile_id: int,
    activity_date: date,
    earned_at: datetime,
) -> tuple[CrownAward, bool]:
    source_key = f"login-streak-crown:{activity_date.isoformat()}"
    grant = _reward_grant(
        session,
        profile_id=profile_id,
        source_key=source_key,
        reward_type="crown_win",
    )
    created = grant is None
    if grant is None:
        session.add(RewardGrant(
            profile_id=profile_id,
            contest_result_id=None,
            source_key=source_key,
            reward_type="crown_win",
            category_key=LOGIN_STREAK_CROWN_CATEGORY,
            amount=1,
        ))

    award = _pending_row(
        session,
        CrownAward,
        profile_id=profile_id,
        source_key=source_key,
    )
    if award is None:
        award = session.scalar(select(CrownAward).where(
            CrownAward.profile_id == profile_id,
            CrownAward.source_key == source_key,
        ))
    if award is None:
        award = CrownAward(
            profile_id=profile_id,
            category_key=LOGIN_STREAK_CROWN_CATEGORY,
            source_key=source_key,
            earned_at=earned_at,
        )
        session.add(award)
    return award, created


def _crown_count(session: Session, profile_id: int) -> int:
    persisted = session.scalar(
        select(func.count())
        .select_from(CrownAward)
        .where(
            CrownAward.profile_id == profile_id,
            CrownAward.category_key == LOGIN_STREAK_CROWN_CATEGORY,
        )
    ) or 0
    pending = sum(
        1
        for row in session.new
        if isinstance(row, CrownAward)
        and row.profile_id == profile_id
        and row.category_key == LOGIN_STREAK_CROWN_CATEGORY
    )
    return persisted + pending


def _payload(
    session: Session,
    *,
    streak: LoginStreak,
    state: WoodchuckState,
    awarded_today: bool,
    dandelions_awarded: int,
    crown_awarded: bool,
) -> dict[str, object]:
    crown_progress = streak.current_days % LOGIN_STREAK_CROWN_INTERVAL
    return {
        "current_streak": streak.current_days,
        "last_login_date": (
            streak.last_login_date.isoformat() if streak.last_login_date else None
        ),
        "awarded_today": awarded_today,
        "dandelions_awarded": dandelions_awarded,
        "dandelion_balance": _credits(state),
        "state_revision": state.revision,
        "crown_awarded": crown_awarded,
        "crowns_earned": _crown_count(session, streak.profile_id),
        "crown_progress": crown_progress,
        "days_to_next_crown": LOGIN_STREAK_CROWN_INTERVAL - crown_progress,
        "crown_interval": LOGIN_STREAK_CROWN_INTERVAL,
    }


def apply_daily_login(
    session: Session,
    *,
    profile_id: int,
    now: datetime | None = None,
) -> dict[str, object]:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    else:
        instant = instant.astimezone(timezone.utc)
    activity_date = central_login_date(instant)

    locked_profile_id = session.scalar(
        select(WoodchuckProfile.id)
        .where(
            WoodchuckProfile.id == profile_id,
            WoodchuckProfile.status == "active",
        )
        .with_for_update()
    )
    if locked_profile_id is None:
        raise ValueError("That Woodchuck profile is unavailable.")

    streak = _pending_row(session, LoginStreak, profile_id=profile_id)
    if streak is None:
        streak = session.scalar(
            select(LoginStreak)
            .where(LoginStreak.profile_id == profile_id)
            .with_for_update()
        )
    if streak is None:
        streak = LoginStreak(profile_id=profile_id, current_days=0)
        session.add(streak)

    state = _state_for_update(session, profile_id)
    if streak.last_login_date == activity_date:
        return _payload(
            session,
            streak=streak,
            state=state,
            awarded_today=False,
            dandelions_awarded=0,
            crown_awarded=False,
        )

    source_key = f"login-streak:{activity_date.isoformat()}"
    existing_grant = _reward_grant(
        session,
        profile_id=profile_id,
        source_key=source_key,
        reward_type="dandelion",
    )
    if existing_grant is not None:
        streak.current_days = max(1, existing_grant.amount)
        streak.last_login_date = activity_date
        if streak.current_days % LOGIN_STREAK_CROWN_INTERVAL == 0:
            _award_weekly_crown(
                session,
                profile_id=profile_id,
                activity_date=activity_date,
                earned_at=instant,
            )
        return _payload(
            session,
            streak=streak,
            state=state,
            awarded_today=False,
            dandelions_awarded=0,
            crown_awarded=False,
        )

    if streak.last_login_date == activity_date - timedelta(days=1):
        next_days = streak.current_days + 1
    else:
        next_days = 1
    streak.current_days = next_days
    streak.last_login_date = activity_date

    payload = deepcopy(state.state_json or {})
    progress = dict(payload.get("progress") or {})
    progress["credits"] = _credits(state) + next_days
    payload["progress"] = progress
    state.state_json = payload
    state.revision += 1
    session.add(RewardGrant(
        profile_id=profile_id,
        contest_result_id=None,
        source_key=source_key,
        reward_type="dandelion",
        category_key="login-streak",
        amount=next_days,
    ))

    crown_awarded = False
    if next_days % LOGIN_STREAK_CROWN_INTERVAL == 0:
        _award, crown_awarded = _award_weekly_crown(
            session,
            profile_id=profile_id,
            activity_date=activity_date,
            earned_at=instant,
        )

    return _payload(
        session,
        streak=streak,
        state=state,
        awarded_today=True,
        dandelions_awarded=next_days,
        crown_awarded=crown_awarded,
    )
