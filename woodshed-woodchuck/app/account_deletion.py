from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, inspect, or_, select, update
from sqlalchemy.orm import Session

from .accounts import normalize_woodchuck_id, retired_identifier_hash
from .deletion_disposal import DeletionDataConflict, associated_disposal
from .child_models import ConsentEvidence, DirectorPermission, ParentAccess
from .models import (
    AnalyticsEvent,
    ContestResult,
    LoginStreak,
    PracticeChart,
    PracticeChartVerification,
    PracticeEmailPreset,
    Season,
    StudentOrganizationMembership,
    StudentVerifierConnection,
    Team,
    TeamJoinRequest,
    TeamMembership,
    TeamReport,
    TrustedVerifierInvitation,
    WoodchuckProfile,
    WoodchuckState,
)
from .security import verify_pin
from .verifiers import ACTIVE_CONNECTION_STATUSES
from .verifier_invitation_disposal import (
    REMOVED_INVITATION_EMAIL, clear_closed_student_invitations, closed_student_invitations_complete,
)
from .team_continuity import lock_team_seasons
from .team_authority import authority_write_time, persistent_enabled


DELETED_PUBLIC_NAME = "Deleted Woodchuck"
DELETED_STATE = {"account": {"authenticated": False, "deleted": True}}
REMOVED_PROFILE_VALUE = "[deleted]"
REMOVED_SCHOOL_NAME = "[removed]"
PRIVATE_CREDENTIALS = ("link_hash", "link_expires_at", "session_hash", "session_expires_at")
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
        normalize_woodchuck_id(woodchuck_id).encode("utf-8"), profile.woodchuck_id.encode("utf-8")
    )
    pin_matches = verify_pin(pin, profile.pin_hash)
    confirmation_matches = hmac.compare_digest(confirmation.encode("utf-8"), b"DELETE")
    if not (id_matches and pin_matches and confirmation_matches):
        _record_failure(profile, now)
        raise DeletionCredentialsError("Woodchuck ID, PIN, or confirmation did not match.")


def lock_account_for_deletion(
    session: Session, profile_id: int, *, now: datetime | None = None,
) -> tuple[WoodchuckProfile | None, datetime]:
    """Lock the account and its existing private authorization before confirmation."""
    # Deletion spans all seasons. Fence before membership/team writes so a
    # continuation cannot add a roster row behind the anonymization update.
    with session.no_autoflush:
        now = authority_write_time(session, at=now)
        lock_team_seasons(session, *session.scalars(select(Season.id).order_by(Season.id)))
        # Parent link issuance and consumption lock current consent before
        # writing. Hold those rows through cleanup so a link cannot survive it.
        consents = select(ConsentEvidence.id).where(
            ConsentEvidence.profile_id == profile_id
        ).order_by(ConsentEvidence.id)
        locked_consents = session.scalars(consents.with_for_update()).all()
        profile = session.scalar(select(WoodchuckProfile).where(
            WoodchuckProfile.id == profile_id
        ).with_for_update().execution_options(populate_existing=True))
        if locked_consents != session.scalars(consents).all():
            raise DeletionDataConflict("Account consent changed during deletion. Retry the request.")
    return profile, now


def ordinary_deletion_complete(session: Session, profile: WoodchuckProfile | None, *, current_scope=True) -> bool:
    """Check the minimum committed cleanup for one already restricted account."""
    if profile is None or profile.status != "deleted" or profile.display_name != DELETED_PUBLIC_NAME:
        return False
    if (profile.instrument, profile.level, profile.goal) != (REMOVED_PROFILE_VALUE,) * 3 or any(
        value is not None for value in (profile.first_choice_instrument, profile.game_mode,
                                        profile.display_name_changed_at, profile.level_changed_at)
    ):
        return False
    sid = profile.id
    if current_scope and inspect(session.connection()).has_table("classroom_roster_identities"):
        from .classroom_identity_models import ClassroomRosterIdentity
        remaining_name = select(ClassroomRosterIdentity.id).where(
            ClassroomRosterIdentity.profile_id == sid, ClassroomRosterIdentity.school_name != REMOVED_SCHOOL_NAME,
        )
        if session.scalar(remaining_name.limit(1)) is not None:
            return False
    state = session.get(WoodchuckState, sid, populate_existing=True)
    if state is not None and state.state_json != DELETED_STATE:
        return False

    charts = select(PracticeChart.id).where(PracticeChart.profile_id == sid)
    consents = select(ConsentEvidence.id).where(ConsentEvidence.profile_id == sid)
    residuals = (
        select(PracticeEmailPreset.id).where(PracticeEmailPreset.profile_id == sid),
        select(AnalyticsEvent.id).where(AnalyticsEvent.profile_id == sid),
        select(LoginStreak.profile_id).where(LoginStreak.profile_id == sid,
            or_(LoginStreak.current_days != 0, LoginStreak.last_login_date.is_not(None))),
        select(PracticeChart.id).where(PracticeChart.profile_id == sid, or_(
            PracticeChart.note.is_not(None), PracticeChart.ordinary_email_preset_id.is_not(None),
            PracticeChart.ordinary_email_error_code.is_not(None))),
        select(PracticeChartVerification.id).where(PracticeChartVerification.practice_chart_id.in_(charts), or_(
            PracticeChartVerification.response_note.is_not(None), PracticeChartVerification.last_email_error_code.is_not(None),
            PracticeChartVerification.status == "pending")),
        select(TrustedVerifierInvitation.id).where(TrustedVerifierInvitation.profile_id == sid, or_(
            TrustedVerifierInvitation.status == "pending", TrustedVerifierInvitation.last_email_error_code.is_not(None))),
        select(ParentAccess.consent_id).where(ParentAccess.consent_id.in_(consents),
            or_(*(getattr(ParentAccess, column).is_not(None) for column in PRIVATE_CREDENTIALS))),
        select(DirectorPermission.id).where(DirectorPermission.profile_id == sid,
            or_(*(getattr(DirectorPermission, column).is_not(None) for column in PRIVATE_CREDENTIALS))),
    )
    if current_scope and not closed_student_invitations_complete(session, sid):
        return False
    if current_scope:
        residuals += (
            select(ContestResult.id).where(ContestResult.profile_id == sid, ContestResult.subject_type == "student",
                                          ContestResult.display_name_snapshot != DELETED_PUBLIC_NAME),
            select(Team.id).where(Team.creator_profile_id == sid),
            select(TeamReport.id).where(TeamReport.reporter_profile_id == sid),
        )
    if any(session.scalar(statement.limit(1)) is not None for statement in residuals):
        return False
    details = select(PracticeChart.practice_details).where(PracticeChart.profile_id == sid)
    return all(value == [] for value in session.scalars(details))


def _clear_school_name(session, profile):
    if inspect(session.connection()).has_table("classroom_roster_identities"):
        from .classroom_identity_models import ClassroomRosterIdentity
        session.execute(update(ClassroomRosterIdentity).where(
            ClassroomRosterIdentity.profile_id == profile.id,
            ClassroomRosterIdentity.school_name != REMOVED_SCHOOL_NAME,
        ).values(school_name=REMOVED_SCHOOL_NAME))


def clear_student_ordinary_data(session: Session, profile: WoodchuckProfile, *, now: datetime):
    """Clean one locked subject. The caller authenticates the deletion and owns the transaction."""
    student_names = (ContestResult.profile_id == profile.id, ContestResult.subject_type == "student",
                     ContestResult.display_name_snapshot != DELETED_PUBLIC_NAME)
    if session.scalar(select(ContestResult.id).where(
        *student_names, ContestResult.subject_key != str(profile.id),
    ).limit(1)) is not None:
        raise DeletionDataConflict("A student contest result has inconsistent subject identifiers.")
    pending_invitations = session.scalars(select(TrustedVerifierInvitation).where(
        TrustedVerifierInvitation.profile_id == profile.id,
        TrustedVerifierInvitation.status == "pending",
    ).order_by(TrustedVerifierInvitation.id).with_for_update().execution_options(populate_existing=True)).all()
    if any(invite.accepted_at is not None or invite.accepted_verifier_id is not None
           for invite in pending_invitations):
        raise DeletionDataConflict("A pending verifier invitation has acceptance evidence.")
    presets = select(PracticeEmailPreset.id).where(PracticeEmailPreset.profile_id == profile.id)
    session.scalars(presets.order_by(PracticeEmailPreset.id).with_for_update()).all()
    if session.scalar(select(PracticeChart.id).where(
        PracticeChart.profile_id != profile.id,
        PracticeChart.ordinary_email_preset_id.in_(presets),
    ).limit(1)) is not None:
        # ON DELETE SET NULL would otherwise change a different student's chart.
        raise DeletionDataConflict("A practice email preset is linked to another account's chart.")
    if session.scalar(select(DirectorPermission.id).join(ConsentEvidence).where(
        or_(DirectorPermission.profile_id == profile.id, ConsentEvidence.profile_id == profile.id),
        DirectorPermission.profile_id != ConsentEvidence.profile_id,
    ).limit(1)) is not None:
        raise DeletionDataConflict("A director permission is linked to another account's consent.")

    _clear_school_name(session, profile)
    credentials = dict.fromkeys(PRIVATE_CREDENTIALS)
    consents = select(ConsentEvidence.id).where(ConsentEvidence.profile_id == profile.id)
    session.execute(update(ParentAccess).where(ParentAccess.consent_id.in_(consents)).values(**credentials))
    session.execute(update(DirectorPermission).where(DirectorPermission.profile_id == profile.id).values(**credentials))

    chart_ids = select(PracticeChart.id).where(PracticeChart.profile_id == profile.id)
    if session.scalar(select(PracticeChartVerification.id).where(
        PracticeChartVerification.practice_chart_id.in_(chart_ids),
        PracticeChartVerification.status == "pending", PracticeChartVerification.responded_at.is_not(None),
    ).limit(1)) is not None:
        raise DeletionDataConflict("A pending chart review has a recorded response.")
    session.execute(update(PracticeChart).where(
        PracticeChart.profile_id == profile.id
    ).values(
        note=None, practice_details=[], ordinary_email_preset_id=None, ordinary_email_error_code=None,
        updated_at=PracticeChart.updated_at,
    ))
    session.execute(update(PracticeChartVerification).where(
        PracticeChartVerification.practice_chart_id.in_(chart_ids)
    ).values(response_note=None, last_email_error_code=None, updated_at=PracticeChartVerification.updated_at))
    session.execute(update(PracticeChartVerification).where(
        PracticeChartVerification.practice_chart_id.in_(chart_ids),
        PracticeChartVerification.status == "pending",
    ).values(status="revoked", verifier_id=None, updated_at=now))
    session.execute(update(TrustedVerifierInvitation).where(
        TrustedVerifierInvitation.profile_id == profile.id,
        TrustedVerifierInvitation.status != "pending",
        TrustedVerifierInvitation.last_email_error_code.is_not(None),
    ).values(last_email_error_code=None, updated_at=TrustedVerifierInvitation.updated_at))
    for invitation in pending_invitations:
        invitation.email = REMOVED_INVITATION_EMAIL
        invitation.last_email_error_code = None
        invitation.status = "revoked"
        # The shared closed-invitation scrub retires the bearer in this transaction.
        if _utc(invitation.expires_at) > now:
            invitation.expires_at = now
        invitation.updated_at = now

    session.execute(delete(PracticeEmailPreset).where(PracticeEmailPreset.profile_id == profile.id))
    session.execute(delete(AnalyticsEvent).where(AnalyticsEvent.profile_id == profile.id))
    session.execute(update(LoginStreak).where(LoginStreak.profile_id == profile.id).values(
        current_days=0, last_login_date=None, updated_at=LoginStreak.updated_at,
    ))

    # Cleanup must keep the actual earlier clocks, including SQLAlchemy onupdate columns.
    state = session.get(WoodchuckState, profile.id, populate_existing=True)
    if state is not None and state.state_json != DELETED_STATE:
        session.execute(update(WoodchuckState).where(WoodchuckState.profile_id == profile.id).values(
            state_json=DELETED_STATE, revision=WoodchuckState.revision + 1, updated_at=WoodchuckState.updated_at,
        ))
    session.execute(update(WoodchuckProfile).where(WoodchuckProfile.id == profile.id).values(
        instrument=REMOVED_PROFILE_VALUE, level=REMOVED_PROFILE_VALUE, goal=REMOVED_PROFILE_VALUE,
        first_choice_instrument=None, game_mode=None, display_name_changed_at=None, level_changed_at=None,
        updated_at=WoodchuckProfile.updated_at,
    ))
    session.flush()
    clear_closed_student_invitations(session, profile.id)
    session.execute(update(ContestResult).where(*student_names, ContestResult.subject_key == str(profile.id))
                    .values(display_name_snapshot=DELETED_PUBLIC_NAME))
    session.execute(update(Team).where(Team.creator_profile_id == profile.id).values(creator_profile_id=None))
    session.execute(update(TeamReport).where(TeamReport.reporter_profile_id == profile.id).values(reporter_profile_id=None))


def anonymize_woodchuck_account(
    session: Session, *, profile: WoodchuckProfile, now: datetime
) -> dict[str, str] | None:
    """Disable one profile and clear ordinary account data within the caller's transaction."""
    profile, now = lock_account_for_deletion(session, profile.id, now=now)
    if profile is None:
        raise DeletionDataConflict("The account is unavailable.")
    if profile.status == "deleted":
        # Historical fulfillment uses its own approval and original deletion evidence.
        _clear_school_name(session, profile)
        return
    now = _utc(now)
    clear_student_ordinary_data(session, profile, now=now)
    persistent = persistent_enabled(session, now)
    from .c001_abuse import record_deletion
    record_deletion(session, profile.id)
    original_id = profile.woodchuck_id

    membership_scope = and_(
        TeamMembership.is_persistent.is_(True),
        TeamMembership.started_at <= now,
        or_(TeamMembership.ended_at.is_(None), TeamMembership.ended_at > now),
    ) if persistent else TeamMembership.ended_at.is_(None)
    session.execute(update(TeamMembership).where(
        TeamMembership.profile_id == profile.id,
        membership_scope,
    ).values(ended_at=now))
    if persistent:
        session.execute(update(TeamJoinRequest).where(
            TeamJoinRequest.profile_id == profile.id,
            TeamJoinRequest.is_persistent.is_(True),
            TeamJoinRequest.status == "pending",
        ).values(status="cancelled", resolved_at=now))
    session.execute(update(StudentVerifierConnection).where(
        StudentVerifierConnection.profile_id == profile.id,
        StudentVerifierConnection.status.in_(ACTIVE_CONNECTION_STATUSES),
    ).values(status="disconnected", updated_at=now))
    session.execute(update(StudentOrganizationMembership).where(
        StudentOrganizationMembership.profile_id == profile.id,
        StudentOrganizationMembership.status == "active",
    ).values(status="inactive"))

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
    if not ordinary_deletion_complete(session, profile):
        raise DeletionDataConflict("Ordinary account cleanup could not be verified.")
    return associated_disposal(session, profile.id)
