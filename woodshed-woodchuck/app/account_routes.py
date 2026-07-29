from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Form, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .accounts import (
    ProfileChangeCooldown,
    authenticate_woodchuck,
    create_woodchuck_profile,
    update_profile_instrument,
    update_profile_display_name,
    update_profile_level,
)
from .instruments import INSTRUMENTS_BY_LABEL
from .db import SessionLocal
from .models import WoodchuckProfile, WoodchuckState


router = APIRouter(prefix="/account", tags=["account"])

SESSION_PROFILE_ID = "woodchuck_profile_id"


class InstrumentUpdate(BaseModel):
    instrument: str


class DisplayNameUpdate(BaseModel):
    display_name: str


class LevelUpdate(BaseModel):
    level: str


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


@router.patch("/profile/instrument")
def change_profile_instrument(
    request: Request,
    submitted: InstrumentUpdate,
):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Student sign-in is required.",
            )
        try:
            updated = update_profile_instrument(
                session,
                profile=profile,
                instrument=submitted.instrument,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=500,
                detail="The instrument could not be changed.",
            ) from error

        definition = INSTRUMENTS_BY_LABEL[updated.instrument.casefold()]
        return {
            "updated": True,
            "instrument": updated.instrument,
            "instrument_definition": dict(definition),
        }


def _change_profile_value(request: Request, submitted: BaseModel, updater):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            updated = updater(session, profile=profile, **submitted.model_dump())
        except ProfileChangeCooldown as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail="The profile could not be changed.") from error
        field = next(iter(submitted.model_dump()))
        return {"updated": True, field: getattr(updated, field)}


@router.patch("/profile/name")
def change_profile_name(request: Request, submitted: DisplayNameUpdate):
    return _change_profile_value(request, submitted, update_profile_display_name)


@router.patch("/profile/level")
def change_profile_level(request: Request, submitted: LevelUpdate):
    return _change_profile_value(request, submitted, update_profile_level)



@router.get("/state")
def load_account_state(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Sign in is required.",
            )

        saved_state = session.get(WoodchuckState, profile.id)

        return {
            "authenticated": True,
            "state": saved_state.state_json if saved_state else None,
            "revision": saved_state.revision if saved_state else 0,
        }


@router.put("/state")
def save_account_state(
    request: Request,
    state: dict[str, object] = Body(...),
):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Sign in is required.",
            )

        synced_at = datetime.now(timezone.utc).isoformat()
        normalized_state = dict(state)

        existing_account = normalized_state.get("account")
        submitted_account = (
            dict(existing_account)
            if isinstance(existing_account, dict)
            else {}
        )
        submitted_revision = submitted_account.get(
            "serverRevision",
            0,
        )

        if not isinstance(submitted_revision, int):
            submitted_revision = 0

        saved_state = session.get(WoodchuckState, profile.id)
        current_revision = (
            saved_state.revision
            if saved_state is not None
            else 0
        )

        if submitted_revision != current_revision:
            return JSONResponse(
                status_code=409,
                content={
                    "saved": False,
                    "conflict": True,
                    "message": (
                        "This Woodshed was updated from another "
                        "browser or device."
                    ),
                    "server_revision": current_revision,
                },
            )

        next_revision = current_revision + 1

        existing_account = normalized_state.get("account")
        account = (
            dict(existing_account)
            if isinstance(existing_account, dict)
            else {}
        )
        account.update(
            {
                "woodchuckId": profile.woodchuck_id,
                "authenticated": True,
                "serverRevision": next_revision,
                "lastSyncedAt": synced_at,
            }
        )
        normalized_state["account"] = account

        existing_profile = normalized_state.get("profile")
        browser_profile = (
            dict(existing_profile)
            if isinstance(existing_profile, dict)
            else {}
        )
        browser_profile.update(
            {
                "woodchuckName": profile.display_name,
                "instrument": profile.instrument,
                "level": profile.level,
                "goal": profile.goal,
            }
        )
        normalized_state["profile"] = browser_profile

        if saved_state is None:
            saved_state = WoodchuckState(
                profile_id=profile.id,
                state_json=normalized_state,
                revision=next_revision,
            )
            session.add(saved_state)
        else:
            saved_state.state_json = normalized_state
            saved_state.revision = next_revision

        session.commit()

        return {
            "saved": True,
            "revision": next_revision,
            "last_synced_at": synced_at,
        }
