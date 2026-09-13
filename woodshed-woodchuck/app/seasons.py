"""Durable season authority and the single bootstrap calendar.

Season ends are inclusive; ContestWeek ends remain exclusive. ``active`` means
enabled for use, not 'today': adjacent enabled seasons can be provisioned ahead
of time. Planned/closed seasons are never current. Closing an expired season is
an explicit operation subject to the existing finalization guards.
"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from .models import Season


@dataclass(frozen=True)
class SeasonDefinition:
    key: str
    name: str
    starts_on: date
    ends_on: date | None


CANONICAL_SEASONS = (
    # One-time 2026 launch/testing transition: retain accumulated Band Camp
    # history through Sep 13. Future cycles must NOT inherit this extended span.
    SeasonDefinition("band-camp-2026", "Band Camp", date(2026, 7, 27), date(2026, 9, 13)),
    SeasonDefinition("back-to-school-2026", "Back to School", date(2026, 9, 14), date(2026, 9, 27)),
    SeasonDefinition("halloween-2026", "Halloween", date(2026, 9, 28), date(2026, 11, 1)),
    SeasonDefinition("holiday-2026", "Holiday", date(2026, 11, 2), date(2027, 1, 10)),
    SeasonDefinition("hibernaculum-2027", "Hibernaculum", date(2027, 1, 11), date(2027, 3, 7)),
    SeasonDefinition("spring-2027", "Spring", date(2027, 3, 8), date(2027, 5, 9)),
    SeasonDefinition("beach-2027", "Beach", date(2027, 5, 10), date(2027, 7, 4)),
    # Only this start has been approved. Do not invent its end or seed infinite weeks.
    SeasonDefinition("band-camp-2027", "Band Camp", date(2027, 7, 5), None),
)


class SeasonConfigurationError(ValueError):
    pass


def season_covering_date(session, target_date: date) -> Season | None:
    """Read-only runtime lookup; dates/names come exclusively from durable rows."""
    rows = session.scalars(select(Season).where(
        Season.status == "active", Season.starts_on <= target_date,
        (Season.ends_on.is_(None) | (Season.ends_on >= target_date)),
    ).order_by(Season.starts_on.desc(), Season.id)).all()
    if len(rows) > 1:
        raise SeasonConfigurationError("Overlapping active seasons cover the requested date.")
    return rows[0] if rows else None


def canonical_definition_for_date(target_date: date) -> SeasonDefinition | None:
    return next((item for item in CANONICAL_SEASONS if item.starts_on <= target_date
                 and (item.ends_on is None or target_date <= item.ends_on)), None)


def validate_definition(row: Season, definition: SeasonDefinition) -> None:
    if (row.name, row.starts_on, row.ends_on, row.timezone) != (
        definition.name, definition.starts_on, definition.ends_on, "America/Chicago",
    ):
        raise SeasonConfigurationError(f"Season {row.key} conflicts with the canonical calendar; explicit repair required.")


def create_missing_season(session, definition: SeasonDefinition) -> Season:
    """Safe bootstrap only: existing dates, statuses, teams and weeks never change."""
    existing = session.scalar(select(Season).where(Season.key == definition.key))
    if existing is not None:
        validate_definition(existing, definition)
        return existing
    query = select(Season).where(Season.ends_on.is_(None) | (Season.ends_on >= definition.starts_on))
    if definition.ends_on is not None:
        query = query.where(Season.starts_on <= definition.ends_on)
    if session.scalar(query) is not None:
        raise SeasonConfigurationError(f"Cannot create {definition.key}: dates overlap an existing season.")
    row = Season(key=definition.key, name=definition.name, starts_on=definition.starts_on,
                 ends_on=definition.ends_on, timezone="America/Chicago", status="active")
    session.add(row)
    session.flush()
    return row


def bootstrap_canonical_seasons(session) -> None:
    """Caller owns the transaction; conflicts must roll the transaction back."""
    for definition in CANONICAL_SEASONS:
        create_missing_season(session, definition)
