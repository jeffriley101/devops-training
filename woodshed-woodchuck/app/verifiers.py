from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    StudentVerifierConnection,
    TrustedVerifier,
    TrustedVerifierInvitation,
    WoodchuckProfile,
)
from .security import (
    generate_invitation_token,
    hash_invitation_token,
    hash_pin,
    is_valid_pin,
    verify_pin,
)


MAX_VERIFIERS_PER_STUDENT = 3
INVITATION_LIFETIME = timedelta(days=7)

VERIFIER_ROLES = frozenset(
    {
        "parent",
        "guardian",
        "band_director",
        "private_teacher",
        "coach",
        "other_trusted_adult",
    }
)

ACTIVE_CONNECTION_STATUSES = ("pending", "accepted")


@dataclass(frozen=True)
class CreatedInvitation:
    invitation: TrustedVerifierInvitation
    token: str


@dataclass(frozen=True)
class AcceptedInvitation:
    invitation: TrustedVerifierInvitation
    verifier: TrustedVerifier
    connection: StudentVerifierConnection


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_role(role: str) -> str:
    return (
        role.strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def validate_email(email: str) -> str:
    normalized = normalize_email(email)

    if not normalized or len(normalized) > 320:
        raise ValueError("A valid verifier email is required.")

    if any(character.isspace() for character in normalized):
        raise ValueError("A valid verifier email is required.")

    local_part, separator, domain = normalized.partition("@")

    if (
        separator != "@"
        or not local_part
        or not domain
        or "@" in domain
    ):
        raise ValueError("A valid verifier email is required.")

    return normalized


def validate_role(role: str) -> str:
    normalized = normalize_role(role)

    if normalized not in VERIFIER_ROLES:
        raise ValueError("Choose a valid trusted-verifier role.")

    return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def count_reserved_verifier_slots(
    session: Session,
    *,
    profile_id: int,
    now: datetime | None = None,
) -> int:
    now = now or _utc_now()

    connected_count = session.scalar(
        select(func.count())
        .select_from(StudentVerifierConnection)
        .where(
            StudentVerifierConnection.profile_id == profile_id,
            StudentVerifierConnection.status.in_(
                ACTIVE_CONNECTION_STATUSES
            ),
        )
    ) or 0

    pending_count = session.scalar(
        select(func.count())
        .select_from(TrustedVerifierInvitation)
        .where(
            TrustedVerifierInvitation.profile_id == profile_id,
            TrustedVerifierInvitation.status == "pending",
            TrustedVerifierInvitation.expires_at > now,
        )
    ) or 0

    return int(connected_count) + int(pending_count)


def create_trusted_verifier_invitation(
    session: Session,
    *,
    profile: WoodchuckProfile,
    email: str,
    role: str,
) -> CreatedInvitation:
    if profile.id is None:
        raise ValueError("The student account must be saved first.")

    normalized_email = validate_email(email)
    normalized_role = validate_role(role)
    now = _utc_now()

    existing_connection = session.scalar(
        select(StudentVerifierConnection)
        .join(
            TrustedVerifier,
            StudentVerifierConnection.verifier_id
            == TrustedVerifier.id,
        )
        .where(
            StudentVerifierConnection.profile_id == profile.id,
            TrustedVerifier.email == normalized_email,
            StudentVerifierConnection.status.in_(
                ACTIVE_CONNECTION_STATUSES
            ),
        )
    )

    if existing_connection is not None:
        raise ValueError(
            "That verifier is already connected to this student."
        )

    pending_invitation = session.scalar(
        select(TrustedVerifierInvitation).where(
            TrustedVerifierInvitation.profile_id == profile.id,
            TrustedVerifierInvitation.email == normalized_email,
            TrustedVerifierInvitation.status == "pending",
            TrustedVerifierInvitation.expires_at > now,
        )
    )

    if pending_invitation is not None:
        raise ValueError(
            "An active invitation already exists for that email."
        )

    reserved_slots = count_reserved_verifier_slots(
        session,
        profile_id=profile.id,
        now=now,
    )

    if reserved_slots >= MAX_VERIFIERS_PER_STUDENT:
        raise ValueError(
            "A student may have no more than three trusted verifiers."
        )

    token = generate_invitation_token()

    invitation = TrustedVerifierInvitation(
        profile_id=profile.id,
        email=normalized_email,
        role=normalized_role,
        token_hash=hash_invitation_token(token),
        status="pending",
        expires_at=now + INVITATION_LIFETIME,
    )

    session.add(invitation)

    try:
        session.commit()
        session.refresh(invitation)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError(
            "The trusted-verifier invitation could not be created."
        ) from error

    return CreatedInvitation(
        invitation=invitation,
        token=token,
    )


def reissue_trusted_verifier_invitation(
    session: Session,
    *,
    profile: WoodchuckProfile,
    invitation_id: int,
) -> CreatedInvitation:
    if profile.id is None:
        raise ValueError("The student account must be saved first.")

    invitation = session.scalar(
        select(TrustedVerifierInvitation).where(
            TrustedVerifierInvitation.id == invitation_id,
            TrustedVerifierInvitation.profile_id == profile.id,
        )
    )

    if invitation is None:
        raise LookupError("Invitation was not found.")

    if invitation.status != "pending":
        raise ValueError(
            "Only pending invitations can receive a new link."
        )

    profile = session.get(WoodchuckProfile, invitation.profile_id)
    if profile is None or profile.status != "active":
        raise ValueError("That trusted-verifier invitation is invalid.")

    token = generate_invitation_token()
    now = _utc_now()

    invitation.token_hash = hash_invitation_token(token)
    invitation.expires_at = now + INVITATION_LIFETIME

    try:
        session.commit()
        session.refresh(invitation)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError(
            "The invitation link could not be reissued."
        ) from error

    return CreatedInvitation(
        invitation=invitation,
        token=token,
    )


def accept_trusted_verifier_invitation(
    session: Session,
    *,
    token: str,
    display_name: str,
    pin: str,
) -> AcceptedInvitation:
    raw_token = token.strip()

    if not raw_token:
        raise ValueError("The invitation token is required.")

    invitation = session.scalar(
        select(TrustedVerifierInvitation).where(
            TrustedVerifierInvitation.token_hash
            == hash_invitation_token(raw_token)
        )
    )

    if invitation is None:
        raise ValueError("That trusted-verifier invitation is invalid.")

    if invitation.status != "pending":
        raise ValueError(
            "That trusted-verifier invitation has already been used."
        )

    profile = session.get(WoodchuckProfile, invitation.profile_id)
    if profile is None or profile.status != "active":
        raise ValueError("That trusted-verifier invitation is invalid.")

    now = _utc_now()

    if _as_utc(invitation.expires_at) <= now:
        invitation.status = "expired"
        session.commit()
        raise ValueError(
            "That trusted-verifier invitation has expired."
        )

    if not is_valid_pin(pin):
        raise ValueError("PIN must contain exactly four digits.")

    normalized_name = display_name.strip()

    verifier = session.scalar(
        select(TrustedVerifier).where(
            TrustedVerifier.email == invitation.email
        )
    )

    if verifier is None:
        if not normalized_name:
            raise ValueError("Verifier name is required.")

        verifier = TrustedVerifier(
            email=invitation.email,
            display_name=normalized_name,
            pin_hash=hash_pin(pin),
        )

        session.add(verifier)
        session.flush()
    elif not verify_pin(pin, verifier.pin_hash):
        raise ValueError(
            "A verifier account already exists for this email. "
            "Enter that account's existing PIN."
        )

    connection = session.scalar(
        select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id
            == invitation.profile_id,
            StudentVerifierConnection.verifier_id == verifier.id,
        )
    )

    if connection is None:
        connected_count = session.scalar(
            select(func.count())
            .select_from(StudentVerifierConnection)
            .where(
                StudentVerifierConnection.profile_id
                == invitation.profile_id,
                StudentVerifierConnection.status.in_(
                    ACTIVE_CONNECTION_STATUSES
                ),
            )
        ) or 0

        if connected_count >= MAX_VERIFIERS_PER_STUDENT:
            session.rollback()
            raise ValueError(
                "This student already has three trusted verifiers."
            )

        connection = StudentVerifierConnection(
            profile_id=invitation.profile_id,
            verifier_id=verifier.id,
            role=invitation.role,
            status="accepted",
            invited_at=invitation.created_at,
            accepted_at=now,
        )
        session.add(connection)
    else:
        connection.role = invitation.role
        connection.status = "accepted"
        connection.accepted_at = now

    invitation.status = "accepted"
    invitation.accepted_verifier_id = verifier.id
    invitation.accepted_at = now

    try:
        session.commit()
        session.refresh(invitation)
        session.refresh(verifier)
        session.refresh(connection)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError(
            "The trusted-verifier connection could not be completed."
        ) from error

    return AcceptedInvitation(
        invitation=invitation,
        verifier=verifier,
        connection=connection,
    )


def authenticate_trusted_verifier(
    session: Session,
    *,
    email: str,
    pin: str,
) -> TrustedVerifier | None:
    try:
        normalized_email = validate_email(email)
    except ValueError:
        return None

    verifier = session.scalar(
        select(TrustedVerifier).where(
            TrustedVerifier.email == normalized_email
        )
    )

    if verifier is None:
        return None

    if not verify_pin(pin, verifier.pin_hash):
        return None

    return verifier
