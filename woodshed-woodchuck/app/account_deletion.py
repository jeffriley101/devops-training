from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .accounts import normalize_woodchuck_id, retired_identifier_hash
from .models import (
    ContestResult,
    PracticeChart,
    PracticeChartVerification,
    PracticeEmailPreset,
    StudentOrganizationMembership,
    StudentVerifierConnection,
    Team,
    TeamMembership,
    TeamReport,
    TrustedVerifierInvitation,
    WoodchuckProfile,
    WoodchuckState,
)
from .security import hash_invitation_token, verify_pin


DELETED_PUBLIC_NAME = "Deleted Woodchuck"
FAILURE_WINDOW = timedelta(minutes=15)
MAX_FAILURES = 5


class DeletionCredentialsError(ValueError):
    pass


class DeletionRateLimited(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _record_failure(profile: WoodchuckProfile, now: datetime) -> None:
    last = profile.deletion_last_failed_at
    if last is None or now - _utc(last) > FAILURE_WINDOW:
        profile.deletion_failed_attempts = 1
    else:
        profile.deletion_failed_attempts += 1
    profile.deletion_last_failed_at = now


def verify_deletion_confirmation(
    profile: WoodchuckProfile, *, woodchuck_id: str, pin: str,
    confirmation: str, now: datetime,
) -> None:
    now = _utc(now)
    last = profile.deletion_last_failed_at
    if (
        last is not None
        and now - _utc(last) <= FAILURE_WINDOW
        and profile.deletion_failed_attempts >= MAX_FAILURES
    ):
        raise DeletionRateLimited("Deletion confirmation is temporarily unavailable.")
    id_matches = hmac.compare_digest(
        normalize_woodchuck_id(woodchuck_id), profile.woodchuck_id
    )
    pin_matches = verify_pin(pin, profile.pin_hash)
    confirmation_matches = hmac.compare_digest(confirmation, "DELETE")
    if not (id_matches and pin_matches and confirmation_matches):
        _record_failure(profile, now)
        raise DeletionCredentialsError("Woodchuck ID, PIN, or confirmation did not match.")


def anonymize_woodchuck_account(
    session: Session, *, profile: WoodchuckProfile, now: datetime
) -> None:
    """Disable one profile while retaining immutable scoring/history sources."""
    if profile.status == "deleted":
        return
    now = _utc(now)
    original_id = profile.woodchuck_id

    session.execute(update(TeamMembership).where(
        TeamMembership.profile_id == profile.id,
        TeamMembership.ended_at.is_(None),
    ).values(ended_at=now))
    session.execute(update(Team).where(
        Team.creator_profile_id == profile.id
    ).values(creator_profile_id=None))
    session.execute(update(StudentVerifierConnection).where(
        StudentVerifierConnection.profile_id == profile.id
    ).values(status="disconnected", updated_at=now))
    session.execute(update(StudentOrganizationMembership).where(
        StudentOrganizationMembership.profile_id == profile.id
    ).values(status="inactive"))
    session.execute(update(TeamReport).where(
        TeamReport.reporter_profile_id == profile.id
    ).values(reporter_profile_id=None))

    chart_ids = select(PracticeChart.id).where(PracticeChart.profile_id == profile.id)
    session.execute(update(PracticeChart).where(
        PracticeChart.profile_id == profile.id
    ).values(
        note=None, practice_details=[], ordinary_email_preset_id=None,
    ))
    session.execute(update(PracticeChartVerification).where(
        PracticeChartVerification.practice_chart_id.in_(chart_ids)
    ).values(response_note=None))
    session.execute(update(PracticeChartVerification).where(
        PracticeChartVerification.practice_chart_id.in_(chart_ids),
        PracticeChartVerification.status == "pending",
    ).values(status="revoked", verifier_id=None, updated_at=now))
    session.execute(update(ContestResult).where(
        ContestResult.profile_id == profile.id,
        ContestResult.subject_type == "student",
    ).values(display_name_snapshot=DELETED_PUBLIC_NAME))

    for invitation in session.scalars(select(TrustedVerifierInvitation).where(
        TrustedVerifierInvitation.profile_id == profile.id
    )).all():
        invitation.email = "deleted@invalid.local"
        if invitation.status == "pending":
            invitation.status = "revoked"
            invitation.token_hash = hash_invitation_token(secrets.token_urlsafe(32))
            invitation.expires_at = now
        invitation.updated_at = now

    for preset in session.scalars(select(PracticeEmailPreset).where(
        PracticeEmailPreset.profile_id == profile.id
    )).all():
        session.delete(preset)

    state = session.get(WoodchuckState, profile.id)
    if state is not None:
        state.state_json = {"account": {"authenticated": False, "deleted": True}}
        state.revision += 1

    profile.retired_woodchuck_id_hash = retired_identifier_hash(original_id)
    profile.woodchuck_id = f"WD-{secrets.token_hex(6).upper()}"
    profile.display_name = DELETED_PUBLIC_NAME
    profile.pin_hash = f"deleted${secrets.token_hex(32)}"
    profile.status = "deleted"
    profile.deleted_at = now
    profile.session_version += 1
    profile.deletion_failed_attempts = 0
    profile.deletion_last_failed_at = None
    session.flush()
