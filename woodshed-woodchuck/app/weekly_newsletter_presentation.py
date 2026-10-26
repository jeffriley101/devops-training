"""Atomic at-most-once auto-opening; manual Newsletter reads never call here."""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select, update

from .age_models import AccountPrivacy
from .models import WoodchuckProfile
from .teams import has_band_director_capability
from .weekly_models import WeeklyNewsletterPresentation
from .weekly_newsletter import publication_context


def student_context(session, request, profile):
    """Additional automatic-presentation restrictions, not a new auth policy."""
    privacy = session.get(AccountPrivacy, profile.id, populate_existing=True)
    management_context = any(request.session.get(key) for key in (
        "trusted_verifier_id", "site_admin_fingerprint", "contest_admin_token_fingerprint",
        "child_parent_consent", "child_director_permission"))
    return bool(not management_context and privacy and privacy.age_band in ("under13", "13to17", "adult")
                and not has_band_director_capability(session, profile_id=profile.id))


def reserve(session, request, expected_week, *, authorize, clock):
    """Caller commits; every request serializes on the native profile-first fence."""
    profile = authorize(session)
    if not student_context(session, request, profile):
        raise HTTPException(403, "Automatic Newsletter is available only in student context.")
    pid = profile.id
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(WoodchuckProfile).where(WoodchuckProfile.id == pid).values(
            id=WoodchuckProfile.id, updated_at=WoodchuckProfile.updated_at))
    session.scalar(select(WoodchuckProfile).where(WoodchuckProfile.id == pid)
                   .with_for_update().execution_options(populate_existing=True))
    profile = authorize(session)
    if not student_context(session, request, profile):
        raise HTTPException(403, "Automatic Newsletter is available only in student context.")
    instant = clock()
    context = publication_context(instant)
    if not context["available"] or context["week_start"] != expected_week:
        return {"status": "unavailable", "context": context}
    identity = (pid, date.fromisoformat(context["week_start"]))
    if session.get(WeeklyNewsletterPresentation, identity) is not None:
        return {"status": "already_reserved", "context": context}
    session.add(WeeklyNewsletterPresentation(profile_id=pid, week_start=identity[1], reserved_at=instant))
    session.flush()
    authorize(session)
    if not student_context(session, request, profile):
        raise HTTPException(403, "Student context changed.")
    # A lock wait or transaction crossing Monday must not reserve the old edition.
    current = publication_context(clock())
    if not current["available"] or current["week_start"] != expected_week:
        session.rollback()
        return {"status": "unavailable", "context": current}
    return {"status": "reserved", "context": context}
