"""Small, durable tester-enrollment and registration-context services."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .db import SessionLocal
from .models import TesterEnrollment, WoodchuckProfile


PILOT_D1 = "PILOT-D1"
C001 = "C001"
LIFETIME_TESTER_COHORTS = frozenset({PILOT_D1, C001})
SESSION_REGISTRATION_CONTEXT = "tester_registration_context"
PILOT_D1_DATE = date(2026, 9, 16)
CENTRAL = ZoneInfo("America/Chicago")
_COHORT_KEY = re.compile(r"[A-Z0-9][A-Z0-9-]{0,39}")


def clock() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def normalize_cohort_key(value: str) -> str:
    key = str(value).strip().upper()
    if not _COHORT_KEY.fullmatch(key):
        raise ValueError("Invalid tester cohort key.")
    return key


def enroll_tester(session, profile_id: int, cohort_key: str, joined_at: datetime) -> TesterEnrollment:
    """Idempotently record one authorized profile/cohort enrollment.

    This service deliberately does not create billing, membership, or seat rows.
    Deleted profiles may retain historical enrollment, but access checks still
    require an active, age-eligible profile.
    """
    key = normalize_cohort_key(cohort_key)
    profile = session.scalar(
        select(WoodchuckProfile)
        .where(WoodchuckProfile.id == profile_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if profile is None:
        raise ValueError("Tester profile not found.")
    existing = session.scalar(select(TesterEnrollment).where(
        TesterEnrollment.profile_id == profile_id,
        TesterEnrollment.cohort_key == key,
    ))
    if existing is not None:
        return existing
    row = TesterEnrollment(
        profile_id=profile_id,
        cohort_key=key,
        joined_at=utc(joined_at),
    )
    session.add(row)
    session.flush()
    return row


def tester_has_lifetime_access(session, profile_id: int, *, at: datetime | None = None) -> bool:
    """Return tester access only after the ordinary child/age gate is open."""
    from .age_privacy import eligible

    profile = session.get(WoodchuckProfile, profile_id)
    if profile is None or profile.status != "active" or not eligible(session, profile_id):
        return False
    instant = utc(at or clock())
    return session.scalar(select(TesterEnrollment.id).where(
        TesterEnrollment.profile_id == profile_id,
        TesterEnrollment.cohort_key.in_(LIFETIME_TESTER_COHORTS),
        TesterEnrollment.joined_at <= instant,
    ).limit(1)) is not None


def establish_registration_context(request, cohort_key: str) -> None:
    key = normalize_cohort_key(cohort_key)
    if key != C001:
        raise ValueError("This public tester entry is unavailable.")
    existing = registration_context(request)
    if existing == key:
        return
    request.session[SESSION_REGISTRATION_CONTEXT] = {"cohort_key": key}


def registration_context(request) -> str | None:
    """Read only signed server session context; visiting Guest is not a claim."""
    value = request.session.get(SESSION_REGISTRATION_CONTEXT)
    if not isinstance(value, dict) or set(value) != {"cohort_key"}:
        return None
    if value.get("cohort_key") != C001:
        return None
    return C001


def clear_registration_context(request) -> None:
    request.session.pop(SESSION_REGISTRATION_CONTEXT, None)


def pilot_d1_window() -> tuple[datetime, datetime]:
    start = datetime.combine(PILOT_D1_DATE, time.min, tzinfo=CENTRAL)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def pilot_d1_candidates(session) -> list[WoodchuckProfile]:
    start, end = pilot_d1_window()
    return list(session.scalars(select(WoodchuckProfile).where(
        WoodchuckProfile.created_at >= start,
        WoodchuckProfile.created_at < end,
    ).order_by(WoodchuckProfile.id)))


def backfill_pilot_d1(session) -> int:
    created = 0
    for profile in pilot_d1_candidates(session):
        existing = session.scalar(select(TesterEnrollment.id).where(
            TesterEnrollment.profile_id == profile.id,
            TesterEnrollment.cohort_key == PILOT_D1,
        ))
        if existing is None:
            enroll_tester(session, profile.id, PILOT_D1, profile.created_at)
            created += 1
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Woodshed tester-enrollment operations")
    parser.add_argument("operation", choices=("backfill-pilot-d1",))
    parser.add_argument("--apply", action="store_true", help="commit the idempotent backfill")
    args = parser.parse_args()
    with SessionLocal() as session:
        if not args.apply:
            print(f"Pilot D1 candidates: {len(pilot_d1_candidates(session))}; no changes made (add --apply).")
            return
        created = backfill_pilot_d1(session)
        session.commit()
        print(f"Pilot D1 enrollments created: {created}.")


if __name__ == "__main__":
    main()
