from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import WoodchuckProfile
from .instruments import normalize_supported_instrument
from .content import LEVEL_OPTIONS
from .security import generate_woodchuck_id, hash_pin, is_valid_pin, verify_pin


def normalize_woodchuck_id(woodchuck_id: str) -> str:
    return woodchuck_id.strip().upper()


def create_woodchuck_profile(
    session: Session,
    *,
    display_name: str,
    pin: str,
    instrument: str,
    level: str,
    goal: str,
) -> WoodchuckProfile:
    display_name = display_name.strip()
    instrument = normalize_supported_instrument(instrument)
    level = level.strip()
    goal = goal.strip()

    if not display_name:
        raise ValueError("Woodchuck name is required.")

    if not instrument or not level or not goal:
        raise ValueError("Instrument, level, and goal are required.")

    if not is_valid_pin(pin):
        raise ValueError("PIN must contain exactly four digits.")

    for _ in range(10):
        profile = WoodchuckProfile(
            woodchuck_id=generate_woodchuck_id(),
            display_name=display_name,
            pin_hash=hash_pin(pin),
            instrument=instrument,
            level=level,
            goal=goal,
        )

        session.add(profile)

        try:
            session.commit()
            session.refresh(profile)
            return profile
        except IntegrityError:
            # An ID collision is extremely unlikely, but generate another
            # identifier rather than failing the account creation.
            session.rollback()

    raise RuntimeError("Unable to generate a unique Woodchuck ID.")


def update_profile_instrument(
    session: Session,
    *,
    profile: WoodchuckProfile,
    instrument: str,
) -> WoodchuckProfile:
    normalized_instrument = normalize_supported_instrument(instrument)
    profile.instrument = normalized_instrument

    try:
        session.commit()
        session.refresh(profile)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError("The instrument could not be changed.") from error

    return profile


class ProfileChangeCooldown(ValueError):
    def __init__(self, field: str, remaining: timedelta):
        seconds = max(1, int(remaining.total_seconds() + 0.999))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = (remainder + 59) // 60
        parts = []
        if days:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes and not days:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        super().__init__(
            f"{field} can be changed again in {' '.join(parts) or 'less than a minute'}."
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _update_profile_field(
    session: Session, *, profile: WoodchuckProfile, field: str, value: str,
    timestamp_field: str, cooldown: timedelta, now: datetime | None = None,
) -> WoodchuckProfile:
    now = _utc(now or datetime.now(timezone.utc))
    locked = session.scalar(
        select(WoodchuckProfile).where(WoodchuckProfile.id == profile.id).with_for_update()
    )
    if locked is None:
        raise RuntimeError("The profile could not be changed.")
    if getattr(locked, field) == value:
        return locked
    changed_at = getattr(locked, timestamp_field)
    if changed_at is not None:
        remaining = cooldown - (now - _utc(changed_at))
        if remaining.total_seconds() > 0:
            raise ProfileChangeCooldown(
                "Woodchuck name" if field == "display_name" else "Level", remaining
            )
    setattr(locked, field, value)
    setattr(locked, timestamp_field, now)
    try:
        session.commit()
        session.refresh(locked)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError("The profile could not be changed.") from error
    return locked


def update_profile_display_name(
    session: Session, *, profile: WoodchuckProfile, display_name: str,
    now: datetime | None = None,
) -> WoodchuckProfile:
    value = " ".join(display_name.split())
    if not value:
        raise ValueError("Woodchuck name is required.")
    if len(value) > 50:
        raise ValueError("Woodchuck name must be 50 characters or fewer.")
    return _update_profile_field(
        session, profile=profile, field="display_name", value=value,
        timestamp_field="display_name_changed_at", cooldown=timedelta(hours=24), now=now,
    )


def update_profile_level(
    session: Session, *, profile: WoodchuckProfile, level: str,
    now: datetime | None = None,
) -> WoodchuckProfile:
    value = level.strip()
    if value not in LEVEL_OPTIONS:
        raise ValueError("Choose a supported level.")
    return _update_profile_field(
        session, profile=profile, field="level", value=value,
        timestamp_field="level_changed_at", cooldown=timedelta(days=30), now=now,
    )


def authenticate_woodchuck(
    session: Session,
    *,
    woodchuck_id: str,
    pin: str,
) -> WoodchuckProfile | None:
    normalized_id = normalize_woodchuck_id(woodchuck_id)

    profile = session.scalar(
        select(WoodchuckProfile).where(
            WoodchuckProfile.woodchuck_id == normalized_id
        )
    )

    if profile is None:
        return None

    if not verify_pin(pin, profile.pin_hash):
        return None

    return profile
