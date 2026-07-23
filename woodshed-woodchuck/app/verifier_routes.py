from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .account_routes import current_profile
from .db import SessionLocal
from .models import (
    StudentVerifierConnection,
    TrustedVerifier,
    TrustedVerifierInvitation,
    WoodchuckProfile,
)
from .verifiers import (
    VERIFIER_ROLES,
    accept_trusted_verifier_invitation,
    authenticate_trusted_verifier,
    count_reserved_verifier_slots,
    create_trusted_verifier_invitation,
)


router = APIRouter(
    prefix="/trusted-verifiers",
    tags=["trusted-verifiers"],
)

SESSION_VERIFIER_ID = "trusted_verifier_id"


def verifier_payload(
    verifier: TrustedVerifier,
) -> dict[str, object]:
    return {
        "id": verifier.id,
        "email": verifier.email,
        "display_name": verifier.display_name,
    }


def invitation_payload(
    invitation: TrustedVerifierInvitation,
) -> dict[str, object]:
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "status": invitation.status,
        "created_at": invitation.created_at.isoformat(),
        "expires_at": invitation.expires_at.isoformat(),
        "accepted_at": (
            invitation.accepted_at.isoformat()
            if invitation.accepted_at is not None
            else None
        ),
    }


def current_verifier(
    request: Request,
    session: Session,
) -> TrustedVerifier | None:
    verifier_id = request.session.get(SESSION_VERIFIER_ID)

    if not isinstance(verifier_id, int):
        return None

    verifier = session.get(TrustedVerifier, verifier_id)

    if verifier is None:
        request.session.pop(SESSION_VERIFIER_ID, None)

    return verifier


@router.get("/roles")
def list_verifier_roles():
    return {
        "roles": sorted(VERIFIER_ROLES),
    }


@router.get("/invitations")
def list_student_verifiers(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Student sign-in is required.",
            )

        invitations = session.scalars(
            select(TrustedVerifierInvitation)
            .where(
                TrustedVerifierInvitation.profile_id == profile.id
            )
            .order_by(
                TrustedVerifierInvitation.created_at.desc()
            )
        ).all()

        connection_rows = session.execute(
            select(
                StudentVerifierConnection,
                TrustedVerifier,
            )
            .join(
                TrustedVerifier,
                StudentVerifierConnection.verifier_id
                == TrustedVerifier.id,
            )
            .where(
                StudentVerifierConnection.profile_id == profile.id
            )
            .order_by(
                StudentVerifierConnection.invited_at.desc()
            )
        ).all()

        connections = [
            {
                "id": connection.id,
                "role": connection.role,
                "status": connection.status,
                "accepted_at": (
                    connection.accepted_at.isoformat()
                    if connection.accepted_at is not None
                    else None
                ),
                "verifier": verifier_payload(verifier),
            }
            for connection, verifier in connection_rows
        ]

        return {
            "maximum_verifiers": 3,
            "reserved_slots": count_reserved_verifier_slots(
                session,
                profile_id=profile.id,
            ),
            "invitations": [
                invitation_payload(invitation)
                for invitation in invitations
            ],
            "connections": connections,
        }


@router.post("/invitations")
def create_student_invitation(
    request: Request,
    email: str = Form(...),
    role: str = Form(...),
):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Student sign-in is required.",
            )

        try:
            created = create_trusted_verifier_invitation(
                session,
                profile=profile,
                email=email,
                role=role,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error

        token = created.token

        return {
            "created": True,
            "invitation": invitation_payload(
                created.invitation
            ),
            # Returned until outbound email delivery is implemented.
            "invitation_token": token,
            "accept_path": (
                f"/trusted-verifiers/accept/{token}"
            ),
        }


@router.post("/invitations/{token}/accept")
def accept_invitation(
    request: Request,
    token: str,
    display_name: str = Form(...),
    pin: str = Form(...),
):
    with SessionLocal() as session:
        try:
            accepted = accept_trusted_verifier_invitation(
                session,
                token=token,
                display_name=display_name,
                pin=pin,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error

        request.session[SESSION_VERIFIER_ID] = (
            accepted.verifier.id
        )

        return {
            "authenticated": True,
            "invitation": invitation_payload(
                accepted.invitation
            ),
            "connection": {
                "id": accepted.connection.id,
                "role": accepted.connection.role,
                "status": accepted.connection.status,
            },
            "verifier": verifier_payload(
                accepted.verifier
            ),
        }


@router.post("/login")
def verifier_login(
    request: Request,
    email: str = Form(...),
    pin: str = Form(...),
):
    with SessionLocal() as session:
        verifier = authenticate_trusted_verifier(
            session,
            email=email,
            pin=pin,
        )

        if verifier is None:
            raise HTTPException(
                status_code=401,
                detail="Verifier email or PIN was not recognized.",
            )

        request.session[SESSION_VERIFIER_ID] = verifier.id

        return {
            "authenticated": True,
            "verifier": verifier_payload(verifier),
        }


@router.get("/me")
def verifier_me(request: Request):
    with SessionLocal() as session:
        verifier = current_verifier(request, session)

        if verifier is None:
            return {
                "authenticated": False,
                "verifier": None,
            }

        connection_rows = session.execute(
            select(
                StudentVerifierConnection,
                WoodchuckProfile,
            )
            .join(
                WoodchuckProfile,
                StudentVerifierConnection.profile_id
                == WoodchuckProfile.id,
            )
            .where(
                StudentVerifierConnection.verifier_id
                == verifier.id,
                StudentVerifierConnection.status == "accepted",
            )
            .order_by(
                StudentVerifierConnection.accepted_at.desc()
            )
        ).all()

        return {
            "authenticated": True,
            "verifier": verifier_payload(verifier),
            "student_connections": [
                {
                    "id": connection.id,
                    "role": connection.role,
                    "status": connection.status,
                    "accepted_at": (
                        connection.accepted_at.isoformat()
                        if connection.accepted_at is not None
                        else None
                    ),
                    "student": {
                        "woodchuck_id": profile.woodchuck_id,
                        "display_name": profile.display_name,
                        "instrument": profile.instrument,
                        "level": profile.level,
                        "goal": profile.goal,
                    },
                }
                for connection, profile in connection_rows
            ],
        }


@router.post("/logout")
def verifier_logout(request: Request):
    request.session.pop(SESSION_VERIFIER_ID, None)

    return {
        "authenticated": False,
        "verifier": None,
    }
