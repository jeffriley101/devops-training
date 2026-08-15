from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Form, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .accounts import (
    ProfileChangeCooldown,
    authenticate_woodchuck,
    create_woodchuck_profile,
    update_profile_instrument,
    update_profile_display_name,
    update_profile_level,
)
from .instruments import INSTRUMENTS_BY_LABEL, shed_artwork_url
from .content import LEVEL_OPTIONS
from .db import SessionLocal
from .models import RewardGrant, WoodchuckProfile, WoodchuckState
from .account_deletion import (
    DeletionCredentialsError,
    DeletionRateLimited,
    anonymize_woodchuck_account,
    verify_deletion_confirmation,
)


router = APIRouter(prefix="/account", tags=["account"])

SESSION_PROFILE_ID = "woodchuck_profile_id"
SESSION_PROFILE_VERSION = "woodchuck_session_version"
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


class InstrumentUpdate(BaseModel):
    instrument: str


class DisplayNameUpdate(BaseModel):
    display_name: str


class LevelUpdate(BaseModel):
    level: str


class DailySecretSubmission(BaseModel):
    passcode: str


SECRET_REWARD_TIMEZONE = ZoneInfo("America/Chicago")
SECRET_REWARD_AMOUNT = 20


def profile_payload(profile: WoodchuckProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "woodchuck_id": profile.woodchuck_id,
        "display_name": profile.display_name,
        "instrument": profile.instrument,
        "level": profile.level,
        "goal": profile.goal,
        # This is the authoritative server-side account/profile creation time.
        # Keep the complete timestamp intact so clients never need to infer it.
        "created_at": profile.created_at.isoformat(),
    }


def current_profile(
    request: Request,
    session: Session,
) -> WoodchuckProfile | None:
    profile_id = request.session.get(SESSION_PROFILE_ID)

    if not isinstance(profile_id, int):
        return None

    profile = session.get(WoodchuckProfile, profile_id)

    session_version = request.session.get(SESSION_PROFILE_VERSION, 0)
    if (
        profile is None
        or profile.status != "active"
        or not isinstance(session_version, int)
        or session_version != profile.session_version
    ):
        request.session.pop(SESSION_PROFILE_ID, None)
        request.session.pop(SESSION_PROFILE_VERSION, None)
        return None

    return profile


@router.post("/create")
def create_account(
    request: Request,
    display_name: str | None = Form(None),
    pin: str | None = Form(None),
    instrument: str | None = Form(None),
    level: str | None = Form(None),
    goal: str | None = Form(None),
    initial_state: str | None = Form(None),
):
    with SessionLocal() as session:
        existing_profile = current_profile(request, session)
        if existing_profile is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A Woodchuck is already signed in. Use its existing "
                    "Woodchuck ID and PIN instead of creating another account."
                ),
            )
        if not isinstance(display_name, str) or not display_name.strip():
            raise HTTPException(status_code=400, detail="Please name your Woodchuck.")
        if not isinstance(instrument, str) or not instrument.strip():
            raise HTTPException(status_code=400, detail="Please choose an instrument.")
        instrument_key = " ".join(instrument.split()).casefold()
        if instrument_key not in INSTRUMENTS_BY_LABEL:
            raise HTTPException(status_code=400, detail="Please choose a supported instrument.")
        if not isinstance(level, str) or not level.strip():
            raise HTTPException(status_code=400, detail="Please choose a level.")
        if level.strip() not in LEVEL_OPTIONS:
            raise HTTPException(status_code=400, detail="Please choose a supported level.")
        if not isinstance(goal, str) or not goal.strip():
            raise HTTPException(status_code=400, detail="Please choose a practice goal.")
        if not isinstance(pin, str) or len(pin) != 4 or not pin.isascii() or not pin.isdigit():
            raise HTTPException(status_code=400, detail="Your PIN must contain exactly four digits.")
        if not isinstance(initial_state, str) or not initial_state.strip():
            raise HTTPException(status_code=400, detail="The initial Woodshed state is required.")
        try:
            try:
                submitted_state = json.loads(initial_state)
            except json.JSONDecodeError as exc:
                raise ValueError("The initial Woodshed state is malformed.") from exc
            if not isinstance(submitted_state, dict):
                raise ValueError("The initial Woodshed state must be an object.")
            profile = create_woodchuck_profile(
                session,
                display_name=display_name,
                pin=pin,
                instrument=instrument,
                level=level,
                goal=goal,
                commit=False,
            )
            authoritative_state = deepcopy(submitted_state)
            account = dict(authoritative_state.get("account") or {})
            account.update({
                "woodchuckId": profile.woodchuck_id,
                "authenticated": True,
                "serverRevision": 0,
                "lastSyncedAt": None,
            })
            authoritative_state["account"] = account
            browser_profile = dict(authoritative_state.get("profile") or {})
            browser_profile.update({
                "woodchuckName": profile.display_name,
                "instrument": profile.instrument,
                "level": profile.level,
                "goal": profile.goal,
                "createdAt": profile.created_at.isoformat(),
            })
            authoritative_state["profile"] = browser_profile
            session.add(WoodchuckState(
                profile_id=profile.id,
                state_json=authoritative_state,
                revision=0,
            ))
            session.commit()
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            session.rollback()
            raise

        request.session[SESSION_PROFILE_ID] = profile.id
        request.session[SESSION_PROFILE_VERSION] = profile.session_version

        return {
            "authenticated": True,
            "profile": profile_payload(profile),
            "credentials": {
                "woodchuck_id": profile.woodchuck_id,
                "pin": pin,
            },
            "state": authoritative_state,
            "revision": 0,
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
        request.session[SESSION_PROFILE_VERSION] = profile.session_version

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


@router.get("/privacy")
def account_privacy_page(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            return RedirectResponse(url="/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="account_privacy.html",
            context={"profile": profile},
        )


@router.post("/delete")
def delete_account(
    request: Request,
    woodchuck_id: str = Form(...),
    pin: str = Form(...),
    confirmation: str = Form(...),
):
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            verify_deletion_confirmation(
                profile, woodchuck_id=woodchuck_id, pin=pin,
                confirmation=confirmation, now=now,
            )
        except DeletionRateLimited as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except DeletionCredentialsError as error:
            session.commit()
            raise HTTPException(
                status_code=400,
                detail="Woodchuck ID, PIN, or confirmation did not match.",
            ) from error
        anonymize_woodchuck_account(session, profile=profile, now=now)
        session.commit()
    request.session.clear()
    return RedirectResponse(url="/?account_deleted=1", status_code=303)


@router.post("/daily-secret")
def redeem_daily_secret(request: Request, submitted: DailySecretSubmission):
    if submitted.passcode.strip().casefold() != "union":
        raise HTTPException(status_code=400, detail="That passcode did not match. Try again.")
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        activity_date = datetime.now(timezone.utc).astimezone(SECRET_REWARD_TIMEZONE).date()
        source_key = f"daily-secret:{activity_date.isoformat()}"
        existing = session.scalar(select(RewardGrant).where(
            RewardGrant.profile_id == profile.id,
            RewardGrant.source_key == source_key,
            RewardGrant.reward_type == "dandelion",
        ))
        state = session.get(WoodchuckState, profile.id)
        if existing is not None:
            credits = ((state.state_json or {}).get("progress") or {}).get("credits", 0) if state else 0
            return {"redeemed": False, "amount": 0, "credits": credits, "revision": state.revision if state else 0}
        if state is None:
            state = WoodchuckState(profile_id=profile.id, state_json={}, revision=0)
            session.add(state)
        payload = deepcopy(state.state_json or {})
        progress = dict(payload.get("progress") or {})
        credits = progress.get("credits", 0)
        credits = credits if isinstance(credits, int) and not isinstance(credits, bool) else 0
        progress["credits"] = credits + SECRET_REWARD_AMOUNT
        payload["progress"] = progress
        state.state_json = payload
        state.revision += 1
        session.add(RewardGrant(
            profile_id=profile.id, contest_result_id=None,
            source_key=source_key, reward_type="dandelion",
            category_key=None, amount=SECRET_REWARD_AMOUNT,
        ))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            current = session.get(WoodchuckState, profile.id)
            current_credits = ((current.state_json or {}).get("progress") or {}).get("credits", 0) if current else 0
            return {"redeemed": False, "amount": 0, "credits": current_credits, "revision": current.revision if current else 0}
        return {"redeemed": True, "amount": SECRET_REWARD_AMOUNT, "credits": progress["credits"], "revision": state.revision}


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
            "shed_artwork_url": shed_artwork_url(updated.instrument),
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
