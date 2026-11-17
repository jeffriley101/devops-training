"""Ordinary adult account deletion. The caller locks the verified adult and owns the transaction."""
from datetime import timezone
import hashlib
import hmac
import re
import secrets

from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.orm import load_only

from .age_models import AccountPrivacy
from .child_models import ConsentEvidence, DirectorPermission, PendingConsent
from .classroom_models import ClassroomProgram
from .kws_models import KWSVerification
from .membership_models import (
    BillingAccount, BillingEventApplication, BillingProviderEvent, CheckoutAttempt,
    Membership, MembershipSeat, MembershipSeatInvitation, ProviderSubscription,
)
from .models import StudentVerifierConnection, TrustedVerifier, TrustedVerifierInvitation, WoodchuckProfile
from .session_config import session_secret
from .session_revocations import RevokedBrowserSession
from .verifier_invitation_disposal import accepted_invitation, retired_invitation_token


def _invitation_lifetime():
    from .classroom_invitation import LIFETIME
    from .verifiers import INVITATION_LIFETIME

    return max(LIFETIME, INVITATION_LIFETIME)


class AdultDeletionHold(ValueError):
    """Accountable preflight hold. Its code is safe for the external Support case."""


def invitation_key(email):
    return hmac.new(session_secret().encode(),
                    ("adult-invitation-retirement:v1:" + email.strip().lower()).encode(),
                    hashlib.sha256).hexdigest()


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def adult_invitation_cutoff_complete(session, key, deleted_at, now):
    expiry = _utc(deleted_at) + _invitation_lifetime()
    if _utc(now) >= expiry:
        return True
    marker = session.get(RevokedBrowserSession, key, populate_existing=True)
    return bool(marker and _utc(deleted_at) <= _utc(marker.revoked_at) <= _utc(now)
                and _utc(marker.expires_at) >= expiry)


def invitation_is_current(session, email, issued_at):
    """Call after locking the current adult, if any. New authorizations have a later issue time."""
    marker = session.get(RevokedBrowserSession, invitation_key(email), populate_existing=True)
    if marker is None:
        return True
    return _utc(issued_at) > _utc(marker.revoked_at)


def active_verifier(session, verifier_id):
    return session.scalar(select(TrustedVerifier).where(
        TrustedVerifier.id == verifier_id, TrustedVerifier.pin_hash.not_like("deleted$%"),
    ).with_for_update(read=True, key_share=True).execution_options(populate_existing=True))


def _billing_unsettled(session, adult_id, now):
    # Billing mutations serialize on this row. FK creation by a different
    # transaction also waits on the adult locked by the caller.
    account = session.scalar(select(BillingAccount.id).where(
        BillingAccount.verifier_id == adult_id).with_for_update(key_share=True))
    if account is None:
        return False
    member_ids = select(Membership.id).where(Membership.billing_account_id == account)
    live_members = member_ids.where(Membership.status == "active", Membership.revoked_at.is_(None),
                                    or_(Membership.access_until.is_(None), Membership.access_until > now))
    attempts = select(CheckoutAttempt.reference).where(CheckoutAttempt.billing_account_id == account)
    subscriptions = select(ProviderSubscription.provider, ProviderSubscription.external_subscription_id).where(
        ProviderSubscription.membership_id.in_(member_ids))
    unresolved_event = select(BillingEventApplication.id).join(
        BillingProviderEvent, BillingProviderEvent.id == BillingEventApplication.event_id,
    ).where(BillingEventApplication.status.in_(("pending", "recoverable")), or_(
        BillingEventApplication.checkout_reference.in_(attempts),
        tuple_(BillingProviderEvent.provider, BillingEventApplication.external_subscription_id).in_(subscriptions),
    ))
    return any(session.scalar(query.limit(1)) is not None for query in (
        select(MembershipSeat.id).where(MembershipSeat.membership_id.in_(live_members),
                                        MembershipSeat.removed_at.is_(None)),
        select(MembershipSeatInvitation.id).where(MembershipSeatInvitation.membership_id.in_(live_members),
                                                  MembershipSeatInvitation.status == "pending",
                                                  MembershipSeatInvitation.expires_at > now),
        select(ProviderSubscription.id).where(ProviderSubscription.membership_id.in_(member_ids),
                                              ProviderSubscription.terminated_at.is_(None)),
        select(CheckoutAttempt.id).where(CheckoutAttempt.billing_account_id == account,
                                         CheckoutAttempt.status.in_(("pending", "recoverable"))),
        unresolved_event,
    ))


def _unbound_director(session, email, now):
    # A mailbox is insufficient evidence that this separate parental grant
    # belongs to the adult. Leave the grant and any guardian credentials intact.
    live_permission = select(DirectorPermission.id).join(
        ConsentEvidence, ConsentEvidence.id == DirectorPermission.consent_id,
    ).join(AccountPrivacy, AccountPrivacy.profile_id == DirectorPermission.profile_id
    ).join(WoodchuckProfile, WoodchuckProfile.id == DirectorPermission.profile_id).where(
        func.lower(func.trim(DirectorPermission.email)) == email, DirectorPermission.connection_id.is_(None),
        DirectorPermission.revoked_at.is_(None), ConsentEvidence.withdrawn_at.is_(None),
        AccountPrivacy.consent_id == DirectorPermission.consent_id, WoodchuckProfile.status == "active",
    )
    # An unexpired pending request can still create a new unbound permission
    # during activation. The existing family procedure can withdraw it or it expires.
    pending = select(PendingConsent.id).join(KWSVerification, KWSVerification.pending_id == PendingConsent.id).where(
        func.lower(func.trim(PendingConsent.director_email)) == email, PendingConsent.expires_at > now,
        KWSVerification.director_allowed.is_(True),
        KWSVerification.state.in_(("reserved", "accepted", "delivery_unknown", "verified")),
    )
    return session.scalar(live_permission.union_all(pending).limit(1)) is not None


def _connections(adult_id):
    return select(StudentVerifierConnection.id).where(StudentVerifierConnection.verifier_id == adult_id)


def _bound_credentials(adult_id):
    return (DirectorPermission.connection_id.in_(_connections(adult_id)),
            or_(DirectorPermission.link_hash.is_not(None), DirectorPermission.link_expires_at.is_not(None),
                DirectorPermission.session_hash.is_not(None), DirectorPermission.session_expires_at.is_not(None)))


def _spent_adult_invitations(adult_id):
    return (accepted_invitation(), TrustedVerifierInvitation.accepted_verifier_id == adult_id,
            TrustedVerifierInvitation.token_hash != retired_invitation_token())


def clear_adult_spent_invitations(session, adult_id):
    # The original actor, contact and clocks belong to the student's invitation history.
    session.execute(update(TrustedVerifierInvitation).where(*_spent_adult_invitations(adult_id)).values(
        token_hash=retired_invitation_token(), updated_at=TrustedVerifierInvitation.updated_at,
    ).execution_options(synchronize_session=False))


def adult_spent_invitations_complete(session, adult_id):
    pending = select(TrustedVerifierInvitation.id).where(*_spent_adult_invitations(adult_id)).limit(1)
    return session.scalar(pending) is None


def delete_adult_account(session, adult, *, now):
    if adult.pin_hash.startswith("deleted$"):
        raise AdultDeletionHold("adult_already_deleted")
    email = adult.email.strip().lower()
    if session.scalar(select(TrustedVerifier.id).where(
            TrustedVerifier.id != adult.id, func.lower(func.trim(TrustedVerifier.email)) == email).limit(1)):
        raise AdultDeletionHold("adult_mailbox_ambiguous")
    if session.scalar(select(ClassroomProgram.organization_id).where(
            ClassroomProgram.owner_verifier_id == adult.id).limit(1)) is not None:
        raise AdultDeletionHold("adult_program_transfer_required")
    if _unbound_director(session, email, now):
        raise AdultDeletionHold("adult_unbound_director_review_required")
    if _billing_unsettled(session, adult.id, now):
        raise AdultDeletionHold("adult_billing_reconciliation_required")

    # Retire unexpired stateless Program invitations for this mailbox only.
    key = invitation_key(email)
    marker = session.get(RevokedBrowserSession, key)
    if marker is None:
        marker = RevokedBrowserSession(token_hash=key)
        session.add(marker)
    marker.revoked_at = now
    marker.expires_at = now + _invitation_lifetime()
    clear_adult_spent_invitations(session, adult.id)

    # The ordinary invitation token survives a signing-key rotation. Remove its
    # bearer capability but preserve the issuing student's row and ability to reissue.
    for invitation in session.scalars(select(TrustedVerifierInvitation).where(
            func.lower(func.trim(TrustedVerifierInvitation.email)) == email,
            TrustedVerifierInvitation.status == "pending", TrustedVerifierInvitation.expires_at > now,
    ).options(load_only(TrustedVerifierInvitation.id)).with_for_update()):
        invitation.token_hash = secrets.token_hex(32)
        invitation.updated_at = now
    session.execute(update(DirectorPermission).where(*_bound_credentials(adult.id)).values(
        link_hash=None, link_expires_at=None, session_hash=None, session_expires_at=None,
    ).execution_options(synchronize_session=False))
    session.execute(update(StudentVerifierConnection).where(
        StudentVerifierConnection.verifier_id == adult.id,
        StudentVerifierConnection.status.in_(("pending", "accepted")),
    ).values(status="disconnected", updated_at=now).execution_options(synchronize_session=False))
    adult.email = f"adult-{adult.id}-{secrets.token_hex(12)}@deleted.invalid"
    adult.display_name = "Deleted adult"
    adult.pin_hash = "deleted$" + secrets.token_hex(32)
    adult.updated_at = now
    session.flush()


def adult_deletion_complete(session, adult):
    adult = session.get(TrustedVerifier, adult.id, populate_existing=True)
    if (adult is None or adult.display_name != "Deleted adult"
            or not re.fullmatch(r"deleted\$[0-9a-f]{64}", adult.pin_hash)
            or not re.fullmatch(rf"adult-{adult.id}-[0-9a-f]{{24}}@deleted\.invalid", adult.email)):
        return False
    live_connection = select(StudentVerifierConnection.id).where(
        StudentVerifierConnection.verifier_id == adult.id, StudentVerifierConnection.status.in_(("pending", "accepted")),
    )
    credentials = select(DirectorPermission.id).where(*_bound_credentials(adult.id))
    return session.scalar(live_connection.limit(1)) is None and session.scalar(credentials.limit(1)) is None
