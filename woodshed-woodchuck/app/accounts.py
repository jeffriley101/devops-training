from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import WoodchuckProfile
from .instruments import normalize_supported_instrument
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
