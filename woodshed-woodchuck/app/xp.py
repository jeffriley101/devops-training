from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import CampPointAward, PlungePointAward, PracticeChart


CENTRAL = ZoneInfo("America/Chicago")
LEVEL_THRESHOLDS = (0, 250, 750, 1500, 3000, 5000, 8000, 12000, 18000, 25000)
PLUNGE_DAILY_XP_CAP = 10
PLUNGE_EVENT_POINTS = {
    "dandelion": 1,
    "carrot": 3,
    "instrument": 5,
    "band_complete": 20,
}


class PlungeEventConflictError(ValueError):
    pass


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def record_plunge_point_award(
    session: Session,
    *,
    profile_id: int,
    event_key: str,
    event_type: str,
    points_scored: int,
    now: datetime | None = None,
) -> tuple[PlungePointAward, bool]:
    normalized_key = event_key.strip()
    if not normalized_key or len(normalized_key) > 100:
        raise ValueError("A valid Plunge event key is required.")
    expected_points = PLUNGE_EVENT_POINTS.get(event_type)
    if expected_points is None:
        raise ValueError("Unsupported Plunge scoring event.")
    if type(points_scored) is not int or points_scored != expected_points:
        raise ValueError("Plunge points do not match the scoring event.")

    existing = session.scalar(select(PlungePointAward).where(
        PlungePointAward.profile_id == profile_id,
        PlungePointAward.event_key == normalized_key,
    ))
    if existing is not None:
        if (
            existing.event_type != event_type
            or existing.points_scored != points_scored
        ):
            raise PlungeEventConflictError(
                "That Plunge event key was already used for different scoring data."
            )
        return existing, False

    occurred_at = now or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("The server award time must be timezone-aware.")
    award = PlungePointAward(
        profile_id=profile_id,
        event_key=normalized_key,
        event_type=event_type,
        points_scored=points_scored,
        occurred_at=occurred_at.astimezone(timezone.utc),
    )
    session.add(award)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(PlungePointAward).where(
            PlungePointAward.profile_id == profile_id,
            PlungePointAward.event_key == normalized_key,
        ))
        if existing is None:
            raise
        if (
            existing.event_type != event_type
            or existing.points_scored != points_scored
        ):
            raise PlungeEventConflictError(
                "That Plunge event key was already used for different scoring data."
            )
        return existing, False
    return award, True


def plunge_xp(session: Session, *, profile_id: int) -> int:
    daily_points: dict[object, int] = defaultdict(int)
    events = session.execute(
        select(PlungePointAward.occurred_at, PlungePointAward.points_scored).where(
            PlungePointAward.profile_id == profile_id
        )
    ).all()
    for occurred_at, points_scored in events:
        local_date = _aware_utc(occurred_at).astimezone(CENTRAL).date()
        daily_points[local_date] += int(points_scored)
    return sum(min(PLUNGE_DAILY_XP_CAP, points) for points in daily_points.values())


def xp_sources(session: Session, *, profile_id: int) -> dict[str, int]:
    practice_minutes = session.scalar(
        select(func.coalesce(func.sum(PracticeChart.minutes), 0)).where(
            PracticeChart.profile_id == profile_id,
            PracticeChart.minutes > 0,
        )
    ) or 0
    board_points = session.scalar(
        select(func.coalesce(func.sum(CampPointAward.points_awarded), 0)).where(
            CampPointAward.profile_id == profile_id
        )
    ) or 0
    p_charts = session.scalar(
        select(func.count()).select_from(PracticeChart).where(
            PracticeChart.profile_id == profile_id
        )
    ) or 0
    return {
        "practice_minutes": int(practice_minutes),
        "board_points": int(board_points),
        "p_charts": int(p_charts),
        "plunge_points": plunge_xp(session, profile_id=profile_id),
    }


def level_payload(xp_total: int) -> dict[str, int | float | None]:
    if xp_total < 0:
        raise ValueError("XP cannot be negative.")
    level = min(bisect_right(LEVEL_THRESHOLDS, xp_total), len(LEVEL_THRESHOLDS))
    current_level_xp = LEVEL_THRESHOLDS[level - 1]
    if level == len(LEVEL_THRESHOLDS):
        return {
            "level": level,
            "current_level_xp": current_level_xp,
            "next_level_xp": None,
            "progress_percent": 100.0,
        }
    next_level_xp = LEVEL_THRESHOLDS[level]
    progress_percent = (
        (xp_total - current_level_xp) / (next_level_xp - current_level_xp)
    ) * 100
    return {
        "level": level,
        "current_level_xp": current_level_xp,
        "next_level_xp": next_level_xp,
        "progress_percent": round(progress_percent, 2),
    }


def xp_payload(session: Session, *, profile_id: int) -> dict[str, object]:
    sources = xp_sources(session, profile_id=profile_id)
    xp_total = sum(sources.values())
    return {
        **level_payload(xp_total),
        "xp_total": xp_total,
        "sources": sources,
    }
