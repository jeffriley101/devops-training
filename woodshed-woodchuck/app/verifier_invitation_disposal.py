"""Dispose of contact and bearer data on the requesting student's closed invitations."""

from sqlalchemy import String, and_, cast, literal, or_, select, update

from .models import TrustedVerifierInvitation as Invitation


REMOVED_INVITATION_EMAIL = "deleted@invalid.local"


def retired_invitation_token():
    # The column is unique and non-null. This marker cannot match a SHA-256
    # bearer digest and contains only the primary key already on the same row.
    return literal("retired:") + cast(Invitation.id, String)


def accepted_invitation():
    return and_(Invitation.status == "accepted", Invitation.accepted_at.is_not(None),
                Invitation.accepted_verifier_id.is_not(None))


def _remaining(profile_id):
    unaccepted = and_(Invitation.status.in_(("expired", "revoked", "cancelled")),
                      Invitation.accepted_at.is_(None), Invitation.accepted_verifier_id.is_(None))
    return and_(Invitation.profile_id == profile_id, or_(accepted_invitation(), unaccepted),
                or_(Invitation.email != REMOVED_INVITATION_EMAIL,
                    Invitation.token_hash != retired_invitation_token()))


def clear_closed_student_invitations(session, profile_id):
    session.execute(update(Invitation).where(_remaining(profile_id)).values(
        email=REMOVED_INVITATION_EMAIL, token_hash=retired_invitation_token(),
        updated_at=Invitation.updated_at,
    ).execution_options(synchronize_session=False))


def closed_student_invitations_complete(session, profile_id):
    return session.scalar(select(Invitation.id).where(_remaining(profile_id)).limit(1)) is None
