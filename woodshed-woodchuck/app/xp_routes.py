from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .account_routes import current_profile
from .db import SessionLocal
from .xp import (
    PlungeEventConflictError,
    record_plunge_point_award,
    xp_payload,
)


router = APIRouter(prefix="/xp", tags=["xp"])


class PlungePointSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1, max_length=100)
    event_type: str
    points_scored: StrictInt


@router.get("")
def get_xp(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return xp_payload(session, profile_id=profile.id)


@router.post("/plunge-points")
def create_plunge_points(request: Request, submitted: PlungePointSubmission):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            award, created = record_plunge_point_award(
                session,
                profile_id=profile.id,
                event_key=submitted.event_key,
                event_type=submitted.event_type,
                points_scored=submitted.points_scored,
            )
        except PlungeEventConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        session.commit()
        return {
            "created": created,
            "event_key": award.event_key,
            "event_type": award.event_type,
            "points_scored": award.points_scored,
        }
