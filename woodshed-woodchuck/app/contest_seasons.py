from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .contests import (
    CENTRAL,
    aware_utc,
    contest_week_schedule,
    contest_season_clause,
    ensure_contest_definitions,
)
from .models import ContestWeek, Season


SEASON_KEY_PATTERN = re.compile(
    r"^(?:band-camp|back-to-school)-[a-z0-9][a-z0-9-]*$"
)


class SeasonRolloverError(ValueError):
    pass


@dataclass(frozen=True)
class SeasonRolloverResult:
    source_key: str
    next_key: str
    weeks_created: int
    created: bool


def _source_blockers(
    session: Session, season: Season, *, now: datetime
) -> list[str]:
    blockers: list[str] = []
    central_today = now.astimezone(CENTRAL).date()
    if season.ends_on is None:
        blockers.append("season_end_date_missing")
    elif central_today <= season.ends_on:
        blockers.append("season_end_not_passed")

    weeks = session.scalars(
        select(ContestWeek).where(ContestWeek.season_id == season.id)
    ).all()
    if not weeks:
        blockers.append("no_contest_weeks")
    unfinalized = [week for week in weeks if week.status != "finalized"]
    if unfinalized:
        blockers.append("unfinalized_contest_weeks")
    if any(
        week.status == "open"
        and now.astimezone(timezone.utc) > aware_utc(week.verification_deadline_at)
        and now.astimezone(timezone.utc) > aware_utc(week.finalize_after)
        for week in unfinalized
    ):
        blockers.append("due_weeks_remain_open")
    return blockers


def _validate_next_dates(starts_on: date, ends_on: date) -> None:
    if starts_on.weekday() != 0:
        raise SeasonRolloverError("The next season must start on Monday.")
    if ends_on.weekday() != 6:
        raise SeasonRolloverError("The next season must end on Sunday.")
    if ends_on < starts_on or (ends_on - starts_on).days % 7 != 6:
        raise SeasonRolloverError("The next season must contain complete Monday-Sunday weeks.")


def rollover_season(
    session: Session,
    *,
    source_key: str,
    next_key: str,
    next_name: str,
    next_starts_on: date,
    next_ends_on: date,
    now: datetime,
) -> SeasonRolloverResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise SeasonRolloverError("The rollover time must be timezone-aware.")
    source = session.scalar(
        select(Season).where(Season.key == source_key).with_for_update()
    )
    if source is None:
        raise SeasonRolloverError("Source season was not found.")
    existing_next = session.scalar(select(Season).where(Season.key == next_key))
    normalized_name = " ".join(next_name.split())
    if source.status == "closed" and existing_next is not None:
        if (
            existing_next.name == normalized_name
            and existing_next.starts_on == next_starts_on
            and existing_next.ends_on == next_ends_on
            and existing_next.timezone == "America/Chicago"
        ):
            week_count = session.scalar(
                select(func.count()).select_from(ContestWeek).where(
                    ContestWeek.season_id == existing_next.id
                )
            ) or 0
            return SeasonRolloverResult(
                source_key=source.key,
                next_key=existing_next.key,
                weeks_created=int(week_count),
                created=False,
            )
        raise SeasonRolloverError("The existing next season configuration conflicts.")
    if source.status != "active":
        raise SeasonRolloverError("Source season is not active.")
    blockers = _source_blockers(session, source, now=now)
    if blockers:
        raise SeasonRolloverError("Rollover blocked: " + ", ".join(blockers))
    if not SEASON_KEY_PATTERN.fullmatch(next_key):
        raise SeasonRolloverError(
            "Next season key must use the band-camp-* or back-to-school-* format."
        )
    if next_key == source_key or existing_next is not None:
        raise SeasonRolloverError("Next season key already exists.")
    if not normalized_name:
        raise SeasonRolloverError("Next season name is required.")
    _validate_next_dates(next_starts_on, next_ends_on)
    if source.ends_on is None or next_starts_on <= source.ends_on:
        raise SeasonRolloverError("Next season dates overlap the source season.")

    conflict = session.scalar(
        select(Season).where(
            Season.key != source.key,
            Season.starts_on <= next_ends_on,
            (Season.ends_on.is_(None) | (Season.ends_on >= next_starts_on)),
        )
    )
    if conflict is not None:
        raise SeasonRolloverError("Next season dates conflict with an existing season.")

    ensure_contest_definitions(session)
    next_season = Season(
        key=next_key,
        name=normalized_name,
        timezone="America/Chicago",
        starts_on=next_starts_on,
        ends_on=next_ends_on,
        status="active",
    )
    session.add(next_season)
    session.flush()

    weeks_created = 0
    week_start = next_starts_on
    while week_start <= next_ends_on:
        week_end, verification_deadline_at, finalize_after = contest_week_schedule(
            week_start
        )
        session.add(ContestWeek(
            season_id=next_season.id,
            week_start=week_start,
            week_end=week_end,
            verification_deadline_at=verification_deadline_at,
            finalize_after=finalize_after,
            status="open",
        ))
        weeks_created += 1
        week_start = week_end

    source.status = "closed"
    session.flush()
    return SeasonRolloverResult(
        source_key=source.key,
        next_key=next_season.key,
        weeks_created=weeks_created,
        created=True,
    )


def season_status_payload(session: Session, *, now: datetime) -> dict[str, object]:
    active = session.scalar(
        select(Season).where(
            Season.status == "active",
            contest_season_clause(),
        ).order_by(Season.starts_on.desc())
    )
    if active is None:
        return {"active_season": None, "rollover_allowed": False,
                "blocking_reasons": ["no_active_season"]}
    weeks = session.scalars(
        select(ContestWeek).where(ContestWeek.season_id == active.id)
    ).all()
    blockers = _source_blockers(session, active, now=now)
    return {
        "active_season": {
            "key": active.key,
            "name": active.name,
            "timezone": active.timezone,
            "starts_on": active.starts_on.isoformat(),
            "ends_on": active.ends_on.isoformat() if active.ends_on else None,
            "status": active.status,
            "total_weeks": len(weeks),
            "open_weeks": sum(week.status == "open" for week in weeks),
            "finalized_weeks": sum(week.status == "finalized" for week in weeks),
        },
        "rollover_allowed": not blockers,
        "blocking_reasons": blockers,
    }
