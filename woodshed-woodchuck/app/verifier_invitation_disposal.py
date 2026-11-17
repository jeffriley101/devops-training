"""Dispose of attributable invitation contacts and ordinary student delivery history."""

import hashlib
import hmac

from sqlalchemy import String, and_, cast, func, literal, or_, select, update

from .models import PracticeChart, PracticeChartVerification
from .models import TrustedVerifierInvitation as Invitation
from .session_config import session_secret


REMOVED_INVITATION_EMAIL = "deleted@invalid.local"


def retired_invitation_token():
    # The column is unique and non-null. This marker cannot match a SHA-256
    # bearer digest and contains only the primary key already on the same row.
    return literal("retired:") + cast(Invitation.id, String)


def accepted_invitation():
    return and_(Invitation.status == "accepted", Invitation.accepted_at.is_not(None),
                Invitation.accepted_verifier_id.is_not(None))


def _closed_invitation():
    unaccepted = and_(Invitation.status.in_(("expired", "revoked", "cancelled")),
                      Invitation.accepted_at.is_(None), Invitation.accepted_verifier_id.is_(None))
    return or_(accepted_invitation(), unaccepted)


def _remaining(profile_id):
    return and_(Invitation.profile_id == profile_id, _closed_invitation(),
                or_(Invitation.email != REMOVED_INVITATION_EMAIL,
                    Invitation.token_hash != retired_invitation_token()))


def clear_closed_student_invitations(session, profile_id):
    session.execute(update(Invitation).where(_remaining(profile_id)).values(
        email=REMOVED_INVITATION_EMAIL, token_hash=retired_invitation_token(),
        updated_at=Invitation.updated_at,
    ).execution_options(synchronize_session=False))


def closed_student_invitations_complete(session, profile_id):
    return session.scalar(select(Invitation.id).where(_remaining(profile_id)).limit(1)) is None


def invitation_key(email):
    return hmac.new(session_secret().encode(),
                    ("adult-invitation-retirement:v1:" + email.strip().lower()).encode(),
                    hashlib.sha256).hexdigest()


def _accepted_by_deletion(adult_id, deleted_at):
    return and_(accepted_invitation(), Invitation.accepted_verifier_id == adult_id,
                Invitation.role.in_(("verifier", "band_director")),
                Invitation.created_at <= Invitation.accepted_at,
                Invitation.accepted_at < Invitation.expires_at,
                Invitation.accepted_at <= deleted_at, Invitation.email != REMOVED_INVITATION_EMAIL)


def _verified_mailboxes(session, eligible, original_key):
    # The original receipt contains the keyed mailbox digest, including for a
    # previously deleted account whose current email is already a tombstone.
    addresses = session.scalars(select(Invitation.email).where(eligible).distinct())
    return [address for address in addresses if hmac.compare_digest(invitation_key(address), original_key)]


def clear_adult_invitation_contacts(session, adult_id, original_key, deleted_at):
    eligible = _accepted_by_deletion(adult_id, deleted_at)
    addresses = _verified_mailboxes(session, eligible, original_key)
    if addresses:
        session.execute(update(Invitation).where(eligible, Invitation.email.in_(addresses)).values(
            email=REMOVED_INVITATION_EMAIL, updated_at=Invitation.updated_at,
        ).execution_options(synchronize_session=False))


def adult_invitation_contacts_complete(session, adult_id, original_key, deleted_at):
    return not _verified_mailboxes(session, _accepted_by_deletion(adult_id, deleted_at), original_key)


def unverified_adult_invitation_contact_count(session, adult_id):
    # Call after scoped disposal. A changed signing secret, different mailbox,
    # or contradictory acceptance evidence requires review of the retained row.
    return session.scalar(select(func.count()).select_from(Invitation).where(
        Invitation.accepted_verifier_id == adult_id, Invitation.email != REMOVED_INVITATION_EMAIL,
    ))


def _student_delivery_changes(profile_id):
    chart_owner = PracticeChart.profile_id == profile_id
    chart_ids = select(PracticeChart.id).where(chart_owner)
    delivery = dict(last_email_attempt_at=None, last_email_sent_at=None, email_attempt_count=0)
    yield PracticeChart, chart_owner, dict(ordinary_email_attempted_at=None, ordinary_email_sent_at=None)
    yield PracticeChartVerification, PracticeChartVerification.practice_chart_id.in_(chart_ids), delivery
    yield Invitation, and_(Invitation.profile_id == profile_id, _closed_invitation()), delivery


def _has_delivery_data(model, fields):
    return or_(*(getattr(model, field).is_distinct_from(value) for field, value in fields.items()))


def clear_student_delivery_history(session, profile_id):
    for model, owner, fields in _student_delivery_changes(profile_id):
        session.execute(update(model).where(owner, _has_delivery_data(model, fields)).values(
            **fields, updated_at=model.updated_at,
        ).execution_options(synchronize_session=False))


def student_delivery_history_complete(session, profile_id):
    for model, owner, fields in _student_delivery_changes(profile_id):
        residuals = select(model.id).where(owner, _has_delivery_data(model, fields)).limit(1)
        if session.scalar(residuals) is not None:
            return False
    return True
