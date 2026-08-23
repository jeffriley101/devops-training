from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .account_routes import current_profile
from .db import SessionLocal
from .xp import (
    MAX_PLUNGE_BEST_SCORE,
    PlungeEventConflictError,
    plunge_best_payload,
    record_plunge_point_award,
    record_plunge_best_score,
    xp_payload,
)


router = APIRouter(prefix="/xp", tags=["xp"])


class PlungePointSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1, max_length=100)
    event_type: str
    points_scored: StrictInt


class PlungeBestSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: StrictInt = Field(ge=0, le=MAX_PLUNGE_BEST_SCORE)


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


@router.get("/plunge-best")
def get_plunge_best(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return plunge_best_payload(session, profile_id=profile.id)


@router.post("/plunge-best")
def update_plunge_best(request: Request, submitted: PlungeBestSubmission):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            best_score, updated = record_plunge_best_score(
                session,
                profile_id=profile.id,
                score=submitted.score,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        session.commit()
        return {
            **plunge_best_payload(session, profile_id=profile.id),
            "updated": updated,
        }
