from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.exc import SQLAlchemyError

from .account_routes import current_profile
from .arcade_scores import (
    MAX_ARCADE_SCORE,
    arcade_score_payload,
)
from .arcade_rewards import (
    DAILY_REWARDED_PLAY_LIMIT,
    ArcadeDailyLimitError,
    ArcadePlayConflictError,
    InsufficientArcadeBalanceError,
    arcade_play_status,
    complete_arcade_play,
    start_arcade_play,
)
from .db import SessionLocal


router = APIRouter(prefix="/arcade", tags=["arcade"])
logger = logging.getLogger(__name__)


def _unexpected_arcade_error(*, operation: str, game_key: str) -> HTTPException:
    """Log request attribution without logging a student or play token."""
    logger.exception("arcade_request_failed operation=%s game_key=%s", operation, game_key)
    return HTTPException(
        status_code=503,
        detail="The Arcade is temporarily unavailable. Please try again.",
    )


def _request_game_key(request: Request) -> str:
    value = request.headers.get("X-Woodshed-Arcade-Game", "unknown")
    return value[:30] if value else "unknown"


class ArcadeScoreSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: StrictInt = Field(ge=0, le=MAX_ARCADE_SCORE)
    play_token: str = Field(min_length=20, max_length=64)


class ArcadePlayStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_key: str = Field(min_length=1, max_length=30)


class ArcadePlayCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: StrictInt = Field(ge=0, le=MAX_ARCADE_SCORE)


@router.get("/plays/status/{game_key}")
def get_arcade_play_status(game_key: str, request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            return arcade_play_status(
                session, profile_id=profile.id, game_key=game_key
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SQLAlchemyError as error:
            raise _unexpected_arcade_error(
                operation="status", game_key=_request_game_key(request)
            ) from error


@router.post("/plays")
def create_arcade_play(submitted: ArcadePlayStart, request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            result = start_arcade_play(
                session, profile_id=profile.id, game_key=submitted.game_key
            )
            session.commit()
            return {
                "play_token": result.play.play_token,
                "game_key": result.play.game_key,
                "entry_cost": result.play.entry_cost,
                "balance": result.balance,
                "reward_eligible": result.reward_eligible,
                "completed_reward_plays": result.completed_reward_plays,
                "daily_reward_limit": DAILY_REWARDED_PLAY_LIMIT,
                "state_revision": result.state_revision,
                "resumed": result.resumed,
            }
        except (ArcadeDailyLimitError, InsufficientArcadeBalanceError) as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SQLAlchemyError as error:
            session.rollback()
            raise _unexpected_arcade_error(
                operation="start", game_key=_request_game_key(request)
            ) from error


@router.post("/plays/{play_token}/complete")
def finish_arcade_play(
    play_token: str,
    submitted: ArcadePlayCompletion,
    request: Request,
):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            payload = complete_arcade_play(
                session,
                profile_id=profile.id,
                play_token=play_token,
                score=submitted.score,
            )
            session.commit()
            return payload
        except ArcadePlayConflictError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SQLAlchemyError as error:
            session.rollback()
            raise _unexpected_arcade_error(
                operation="complete", game_key=_request_game_key(request)
            ) from error


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
        except SQLAlchemyError as error:
            raise _unexpected_arcade_error(
                operation="scores-read", game_key=_request_game_key(request)
            ) from error


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
            payload = complete_arcade_play(
                session,
                profile_id=profile.id,
                play_token=submitted.play_token,
                score=submitted.score,
            )
            if payload["game_key"] != game_key:
                raise ArcadePlayConflictError(
                    "That play belongs to a different Arcade game."
                )
            session.commit()
            return payload
        except ArcadePlayConflictError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SQLAlchemyError as error:
            session.rollback()
            raise _unexpected_arcade_error(
                operation="scores-complete", game_key=_request_game_key(request)
            ) from error
