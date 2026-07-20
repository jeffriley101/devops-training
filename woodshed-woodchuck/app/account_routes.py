from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy.orm import Session

from .accounts import authenticate_woodchuck, create_woodchuck_profile
from .db import SessionLocal
from .models import WoodchuckProfile


router = APIRouter(prefix="/account", tags=["account"])

SESSION_PROFILE_ID = "woodchuck_profile_id"


def profile_payload(profile: WoodchuckProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "woodchuck_id": profile.woodchuck_id,
        "display_name": profile.display_name,
        "instrument": profile.instrument,
        "level": profile.level,
        "goal": profile.goal,
    }


def current_profile(
    request: Request,
    session: Session,
) -> WoodchuckProfile | None:
    profile_id = request.session.get(SESSION_PROFILE_ID)

    if not isinstance(profile_id, int):
        return None

    profile = session.get(WoodchuckProfile, profile_id)

    if profile is None:
        request.session.pop(SESSION_PROFILE_ID, None)

    return profile


@router.post("/create")
def create_account(
    request: Request,
    display_name: str = Form(...),
    pin: str = Form(...),
    instrument: str = Form(...),
    level: str = Form(...),
    goal: str = Form(...),
):
    with SessionLocal() as session:
        try:
            profile = create_woodchuck_profile(
                session,
                display_name=display_name,
                pin=pin,
                instrument=instrument,
                level=level,
                goal=goal,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        request.session[SESSION_PROFILE_ID] = profile.id

        return {
            "authenticated": True,
            "profile": profile_payload(profile),
        }


@router.post("/login")
def login(
    request: Request,
    woodchuck_id: str = Form(...),
    pin: str = Form(...),
):
    with SessionLocal() as session:
        profile = authenticate_woodchuck(
            session,
            woodchuck_id=woodchuck_id,
            pin=pin,
        )

        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Woodchuck ID or PIN was not recognized.",
            )

        request.session[SESSION_PROFILE_ID] = profile.id

        return {
            "authenticated": True,
            "profile": profile_payload(profile),
        }


@router.get("/me")
def account_me(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            return {"authenticated": False, "profile": None}

        return {
            "authenticated": True,
            "profile": profile_payload(profile),
        }


@router.post("/logout")
def logout(request: Request):
    request.session.clear()

    return {"authenticated": False}
