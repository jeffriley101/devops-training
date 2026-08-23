from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .account_routes import current_profile
from .arcade_scores import (
    MAX_ARCADE_SCORE,
    arcade_score_payload,
    record_arcade_high_score,
)
from .db import SessionLocal


router = APIRouter(prefix="/arcade", tags=["arcade"])


class ArcadeScoreSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: StrictInt = Field(ge=0, le=MAX_ARCADE_SCORE)


@router.get("/scores/{game_key}")
def get_arcade_scores(game_key: str, request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            return arcade_score_payload(
                session, profile_id=profile.id, game_key=game_key
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/scores/{game_key}")
def update_arcade_scores(
    game_key: str,
    submitted: ArcadeScoreSubmission,
    request: Request,
):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            _best_score, updated = record_arcade_high_score(
                session,
                profile_id=profile.id,
                game_key=game_key,
                score=submitted.score,
            )
            session.commit()
            return {
                **arcade_score_payload(
                    session, profile_id=profile.id, game_key=game_key
                ),
                "updated": updated,
            }
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(error)) from error
