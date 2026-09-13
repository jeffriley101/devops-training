"""Transactional membership operations. Callers commit; failures roll back together.

Membership writes lock the membership row before counting seats. Student row
locks serialize cross-membership assignment on PostgreSQL; SQLite's first write
serializes writers. Partial unique slot/student indexes are the final backstop.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from .models import (BillingAccount, Membership, MembershipSeat, MembershipSeatInvitation,
                     MembershipAuditEvent, WoodchuckProfile, TrustedVerifier)
from .security import generate_invitation_token, hash_invitation_token
from .verifiers import accepted_active_verifier_students, band_director_students, normalize_email, validate_email


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def clock():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Actor:
    kind: str
    id: int | None = None


def audit(session, membership, actor, action, **details):
    session.add(MembershipAuditEvent(membership_id=membership.id, action=action,
                                    actor_type=actor.kind, actor_id=actor.id, details=details))


def billing_account(session, actor, *, create=False):
    if actor.kind not in {"student", "adult"} or actor.id is None:
        raise ValueError("An account owner is required.")
    model = WoodchuckProfile if actor.kind == "student" else TrustedVerifier
    owner = session.get(model, actor.id)
    if owner is None or (actor.kind == "student" and owner.status != "active"):
        raise ValueError("Account not found.")
    column = BillingAccount.profile_id if actor.kind == "student" else BillingAccount.verifier_id
    if create:
        # Acquires a write/row lock even before an account row exists.
        session.execute(update(model).where(model.id == actor.id).values(id=model.id, updated_at=model.updated_at))
    account = session.scalar(select(BillingAccount).where(column == actor.id))
    if account is None and create:
        account = BillingAccount(**{column.key: actor.id})
        session.add(account)
        session.flush()
    return account


def membership_is_active(membership, at=None):
    at = utc(at or clock())
    return bool(membership and membership.status == "active" and membership.revoked_at is None
                and utc(membership.starts_at) <= at
                and (membership.access_until is None or utc(membership.access_until) > at))


def student_has_full_access(session, profile_id, at=None):
    profile = session.get(WoodchuckProfile, profile_id)
    if profile is None or profile.status != "active":
        return False
    membership = session.scalar(select(Membership).join(MembershipSeat).where(
        MembershipSeat.profile_id == profile_id, MembershipSeat.removed_at.is_(None)))
    return membership_is_active(membership, at)


def owned_membership(session, membership_id, actor, *, lock=False):
    if lock:
        session.execute(update(Membership).where(Membership.id == membership_id).values(id=Membership.id, updated_at=Membership.updated_at))
    membership = session.get(Membership, membership_id, populate_existing=True)
    if membership is None:
        raise LookupError("Membership not found.")
    if actor.kind != "admin":
        account = billing_account(session, actor)
        if account is None or membership.billing_account_id != account.id:
            raise LookupError("Membership not found.")
    return membership


def active_seats(session, membership_id):
    return list(session.scalars(select(MembershipSeat).where(
        MembershipSeat.membership_id == membership_id, MembershipSeat.removed_at.is_(None)
    ).order_by(MembershipSeat.slot_number)))


def connected_students(session, actor):
    if actor.kind != "adult":
        return []
    rows = accepted_active_verifier_students(session, verifier_id=actor.id) + band_director_students(session, verifier_id=actor.id)
    return sorted({row["profile_id"]: {"profile_id": row["profile_id"], "display_name": row["display_name"]}
                   for row in rows}.values(), key=lambda row: (row["display_name"], row["profile_id"]))


def _add_seat(session, membership, profile_id, actor, at):
    if not membership_is_active(membership, at):
        raise ValueError("This membership is not active.")
    session.execute(update(WoodchuckProfile).where(WoodchuckProfile.id == profile_id).values(
        id=WoodchuckProfile.id, updated_at=WoodchuckProfile.updated_at))
    profile = session.get(WoodchuckProfile, profile_id, populate_existing=True)
    if profile is None or profile.status != "active":
        raise ValueError("Active student not found.")
    existing = session.scalar(select(MembershipSeat).where(
        MembershipSeat.profile_id == profile_id, MembershipSeat.removed_at.is_(None)))
    if existing:
        prior = session.get(Membership, existing.membership_id, populate_existing=True)
        if membership_is_active(prior, at):
            raise ValueError("This student already has an active Full seat.")
        # Retire an expired reservation, retaining its complete history.
        existing.removed_at = at
        audit(session, prior, actor, "seat_removed", seat_id=existing.id, reason="access_ended")
        session.flush()
    occupied = {seat.slot_number for seat in active_seats(session, membership.id)}
    slot = next((n for n in range(1, 6) if n not in occupied), None)
    if slot is None:
        raise ValueError("All five student spots are occupied.")
    seat = MembershipSeat(membership_id=membership.id, profile_id=profile_id, slot_number=slot, added_at=at)
    session.add(seat)
    session.flush()
    audit(session, membership, actor, "seat_added", seat_id=seat.id, profile_id=profile_id)
    return seat


def add_seat(session, membership_id, profile_id, actor, at=None):
    membership = owned_membership(session, membership_id, actor, lock=True)
    account = session.get(BillingAccount, membership.billing_account_id)
    allowed = actor.kind == "admin" or account.profile_id == profile_id
    if not allowed:
        allowed = profile_id in {row["profile_id"] for row in connected_students(session, actor)}
    if not allowed:
        raise LookupError("Connected student not found. Use a seat invitation instead.")
    return _add_seat(session, membership, profile_id, actor, utc(at or clock()))


def remove_seat(session, membership_id, seat_id, actor, at=None):
    membership = owned_membership(session, membership_id, actor, lock=True)
    seat = session.scalar(select(MembershipSeat).where(MembershipSeat.id == seat_id,
        MembershipSeat.membership_id == membership.id, MembershipSeat.removed_at.is_(None)))
    if seat is None:
        raise LookupError("Student spot not found.")
    account = session.get(BillingAccount, membership.billing_account_id)
    if actor.kind == "student" and account and account.profile_id == seat.profile_id:
        raise ValueError("The membership owner seat cannot be removed.")
    seat.removed_at = utc(at or clock())
    audit(session, membership, actor, "seat_removed", seat_id=seat.id, profile_id=seat.profile_id)
    session.flush()


def create_complimentary_membership(session, owner, actor, *, access_until=None, at=None):
    if actor.kind != "admin":
        raise PermissionError("Site administrator required.")
    at = utc(at or clock())
    if access_until is not None and utc(access_until) <= at:
        raise ValueError("Access must end after it starts.")
    account = billing_account(session, owner, create=True)
    existing = session.scalar(select(Membership).where(Membership.billing_account_id == account.id,
                                                       Membership.status == "active"))
    if existing:
        if membership_is_active(existing, at):
            raise ValueError("This account already owns an active membership.")
        existing.status = "ended"
        audit(session, existing, actor, "membership_ended")
        session.flush()
    membership = Membership(billing_account_id=account.id, source="manual", starts_at=at,
                            access_until=access_until, status="active", max_student_seats=5)
    session.add(membership)
    session.flush()
    audit(session, membership, actor, "membership_created", source="manual")
    audit(session, membership, actor, "complimentary_full_granted")
    if account.profile_id is not None:
        _add_seat(session, membership, account.profile_id, actor, at)
    return membership


def revoke_complimentary_membership(session, membership_id, actor, at=None):
    if actor.kind != "admin":
        raise PermissionError("Site administrator required.")
    membership = owned_membership(session, membership_id, actor, lock=True)
    if membership.source != "manual":
        raise ValueError("Only complimentary memberships can be revoked here.")
    if membership.status == "revoked":
        return
    at = utc(at or clock())
    membership.status, membership.revoked_at = "revoked", at
    for seat in active_seats(session, membership_id):
        seat.removed_at = at
        audit(session, membership, actor, "seat_removed", seat_id=seat.id, reason="membership_revoked")
    audit(session, membership, actor, "membership_revoked")
    session.flush()


def invite_student(session, membership_id, email, actor, at=None):
    membership = owned_membership(session, membership_id, actor, lock=True)
    at = utc(at or clock())
    if not membership_is_active(membership, at) or len(active_seats(session, membership_id)) >= 5:
        raise ValueError("An active membership with a free student spot is required.")
    email = normalize_email(email)
    validate_email(email)
    # Pending invitations reserve delivery opportunities, not active seats.
    # Bound outstanding invitations and always recheck capacity on claim.
    pending = list(session.scalars(select(MembershipSeatInvitation).where(
        MembershipSeatInvitation.membership_id == membership_id,
        MembershipSeatInvitation.status == "pending", MembershipSeatInvitation.expires_at > at)))
    if any(inv.email == email for inv in pending):
        raise ValueError("An invitation is already pending for that email.")
    if len(pending) + len(active_seats(session, membership_id)) >= 5:
        raise ValueError("Cancel an outstanding invitation before sending another.")
    token = generate_invitation_token()
    invitation = MembershipSeatInvitation(membership_id=membership_id, email=email,
        token_hash=hash_invitation_token(token), expires_at=at + timedelta(days=7), status="pending")
    session.add(invitation)
    session.flush()
    audit(session, membership, actor, "invitation_created", invitation_id=invitation.id)
    return invitation, token


def cancel_invitation(session, membership_id, invitation_id, actor):
    membership = owned_membership(session, membership_id, actor, lock=True)
    invitation = session.scalar(select(MembershipSeatInvitation).where(
        MembershipSeatInvitation.id == invitation_id, MembershipSeatInvitation.membership_id == membership_id,
        MembershipSeatInvitation.status == "pending"))
    if invitation is None:
        raise LookupError("Pending invitation not found.")
    invitation.status = "cancelled"
    audit(session, membership, actor, "invitation_cancelled", invitation_id=invitation.id)


def claim_invitation(session, token, actor, at=None):
    if actor.kind != "student":
        raise PermissionError("Student sign-in is required to claim a spot.")
    at = utc(at or clock())
    invitation = session.scalar(select(MembershipSeatInvitation).where(
        MembershipSeatInvitation.token_hash == hash_invitation_token(token)))
    if invitation is None:
        raise LookupError("Invitation not found.")
    # Token is the grant; it never confers ownership or analytics permissions.
    session.execute(update(Membership).where(Membership.id == invitation.membership_id).values(
        id=Membership.id, updated_at=Membership.updated_at))
    session.refresh(invitation)
    if invitation.status != "pending" or utc(invitation.expires_at) <= at:
        raise ValueError("This invitation has expired or is no longer available.")
    membership = session.get(Membership, invitation.membership_id, populate_existing=True)
    seat = _add_seat(session, membership, actor.id, actor, at)
    invitation.status, invitation.accepted_at, invitation.accepted_profile_id = "accepted", at, actor.id
    audit(session, membership, actor, "invitation_claimed", invitation_id=invitation.id, seat_id=seat.id)
    return seat
