"""Authenticated saved-plan foundation; no assignment or reward operations."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator
from sqlalchemy.exc import IntegrityError, OperationalError

from .account_routes import current_profile
from .db import SessionLocal
from .feature_access import require_feature
from .session_origin import same_origin_mutation
from .weekly import utc_instant
from . import weekly_plans as plans

router = APIRouter(prefix="/weekly", tags=["weekly"])


class PlanSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: StrictStr

    @field_validator("focus")
    @classmethod
    def canonical_focus(cls, value):
        from .content import GOAL_OPTIONS
        if value not in GOAL_OPTIONS:
            raise ValueError("Choose a canonical weekly Focus.")
        return value


def _authorized(request, session, *, refresh=False):
    profile = current_profile(request, session)
    if profile is not None and refresh:
        session.refresh(profile)
        profile = current_profile(request, session)
    if profile is None:
        raise HTTPException(401, "Student sign-in is required.")
    # Logout can revoke the browser nonce without waiting for a profile lock.
    # Recheck after issuance's lock wait as in the canonical Game Mode writer.
    from .session_revocations import NONCE, RevokedBrowserSession, digest
    nonce = request.session.get(NONCE)
    if not isinstance(nonce, str) or session.get(
        RevokedBrowserSession, digest(nonce), populate_existing=True
    ):
        request.scope["ww.suppress_session_cookie"] = True
        raise HTTPException(401, "Sign-in changed. Reload this page.")
    require_feature(session, profile.id, "weekly_plans")
    return profile


def _action(request, focus=None):
    if request.query_params:
        raise HTTPException(422, "Weekly plans do not accept query selectors.")
    for attempt in range(3):
        with SessionLocal() as session:
            try:
                profile = _authorized(request, session)
                if focus is None:
                    instant = utc_instant()
                    plan = plans.load_current_plan(session, profile.id, now=instant)
                else:
                    plan = plans.issue_current_plan(session, profile, focus)
                    instant = utc_instant()
                profile = _authorized(request, session, refresh=True)
                result = plans.current_plan_payload(session, profile, plan, now=instant)
                response = JSONResponse(result, headers={"Cache-Control": "no-store"})
                # GET only reads and closes its transaction. No implicit issuance,
                # state creation, grants, account mutation or revision increment.
                if focus is not None:
                    session.commit()
                return response
            except IntegrityError as error:
                session.rollback()
                if focus is None or not plans.is_plan_unique_race(error, session.get_bind().dialect.name):
                    raise
                if attempt == 2:
                    raise HTTPException(503, "Please retry this weekly plan request.",
                                        headers={"Retry-After": "1"}) from error
            except OperationalError as error:
                session.rollback()
                if session.get_bind().dialect.name != "sqlite" or not any(
                    text in str(error.orig).lower() for text in ("locked", "busy")
                ):
                    raise
                if attempt == 2:
                    raise HTTPException(503, "Please retry this weekly plan request.",
                                        headers={"Retry-After": "1"}) from error


@router.get("/plan")
def load_plan(request: Request):
    return _action(request)


@router.post("/plan")
def issue_plan(request: Request, submitted: PlanSubmission):
    if not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.",
                            headers={"Cache-Control": "no-store"})
    return _action(request, submitted.focus)
