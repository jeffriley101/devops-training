"""Authenticated saved plans, assignments and explicit Caper claims."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from sqlalchemy.exc import IntegrityError, OperationalError

from .account_routes import current_profile
from .db import SessionLocal
from .feature_access import require_feature
from .session_origin import same_origin_mutation
from .weekly import utc_instant
from . import weekly_plans as plans

router = APIRouter(prefix="/weekly", tags=["weekly"])


@router.get("/newsletter")
def load_newsletter(request: Request):
    """An owned read projection, never an opening/reward or publication writer."""
    cache = {"Cache-Control": "private, no-store"}
    try:
        if request.query_params:
            raise HTTPException(422, "Newsletter does not accept selectors.")
        with SessionLocal() as session:
            profile = _authorized(request, session, feature="weekly_newsletter")
            from .weekly_newsletter import newsletter_payload
            with session.no_autoflush:
                result = newsletter_payload(session, profile, now=utc_instant())
            _authorized(request, session, refresh=True, feature="weekly_newsletter")
            return JSONResponse(result, headers=cache)
    except HTTPException as error:
        error.headers = {**(error.headers or {}), **cache}
        raise


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


def _authorized(request, session, *, refresh=False, feature="weekly_plans"):
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
    require_feature(session, profile.id, feature)
    return profile


def _action(request, focus=None, *, start=False, context=None):
    if request.query_params:
        raise HTTPException(422, "Weekly plans do not accept query selectors.")
    for attempt in range(3):
        with SessionLocal() as session:
            try:
                profile = _authorized(request, session)
                if start:
                    result = {"assignment": plans.start_current_instrument(
                        session, profile, context,
                        authorize=lambda locked: _authorized(request, locked, refresh=True))}
                elif focus is None:
                    instant = utc_instant()
                    plan = plans.load_current_plan(session, profile.id, now=instant)
                else:
                    plan = plans.issue_current_plan(session, profile, focus)
                    instant = utc_instant()
                profile = _authorized(request, session, refresh=True)
                if not start:
                    result = plans.current_plan_payload(session, profile, plan, now=instant)
                response = JSONResponse(result, headers={"Cache-Control": "no-store"})
                # GET only reads and closes its transaction. No implicit issuance,
                # state creation, grants, account mutation or revision increment.
                if start or focus is not None:
                    session.commit()
                return response
            except IntegrityError as error:
                session.rollback()
                recognized = (plans.is_instrument_unique_race if start else plans.is_plan_unique_race)
                if (not start and focus is None) or not recognized(error, session.get_bind().dialect.name):
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


class InstrumentStartSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_context: StrictStr | None = Field(default=None, max_length=100)


@router.post("/plan/instruments/current/start")
def start_instrument(request: Request, submitted: InstrumentStartSubmission):
    if not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.",
                            headers={"Cache-Control": "no-store"})
    return _action(request, start=True, context=submitted.start_context)


class ClaimSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CloverClaimSubmission(ClaimSubmission):
    request_key: StrictStr = Field(min_length=1, max_length=64)


def _claims_action(request, operation, *, write=False):
    if write and not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.",
                            headers={"Cache-Control": "no-store"})
    allowed = set() if write else {"before_plan_id"}
    if set(request.query_params) - allowed:
        raise HTTPException(422, "Only the historical claims cursor is accepted.")
    for attempt in range(3):
        with SessionLocal() as session:
            try:
                profile = _authorized(request, session)
                # Authorize/gate before importing or accessing claim storage.
                from . import weekly_caper as caper
                result = operation(session, profile,
                    lambda locked: _authorized(request, locked, refresh=True))
                _authorized(request, session, refresh=True)
                response = JSONResponse(result, headers={"Cache-Control": "no-store"})
                if write:
                    session.commit()
                return response
            except IntegrityError as error:
                session.rollback()
                from .weekly_caper import is_claim_unique_race
                if not write or not is_claim_unique_race(error, session.get_bind().dialect.name):
                    raise
                if attempt == 2:
                    raise HTTPException(503, "Please retry this saved claim with the same request key.",
                                        headers={"Retry-After": "1"}) from error
            except OperationalError as error:
                session.rollback()
                if session.get_bind().dialect.name != "sqlite" or not any(
                    text in str(error.orig).lower() for text in ("locked", "busy")
                ):
                    raise
                if attempt == 2:
                    raise HTTPException(503, "Please retry this saved claim with the same request key.",
                                        headers={"Retry-After": "1"}) from error


@router.get("/claims")
def load_claims(request: Request, before_plan_id: int | None = Query(None, ge=1)):
    from .weekly_caper import claims_payload
    return _claims_action(request, lambda session, profile, authorize:
        claims_payload(session, profile.id, now=utc_instant(), before_plan_id=before_plan_id))


@router.post("/plans/{plan_id}/tasks/{lane}/claim")
def claim_weekly_task(plan_id: int, lane: str, request: Request, submitted: ClaimSubmission):
    from .weekly_caper import claim_task
    return _claims_action(request, lambda session, profile, authorize:
        claim_task(session, profile, plan_id, lane, authorize=authorize), write=True)


@router.post("/plans/{plan_id}/clue/claim")
def claim_weekly_clue(plan_id: int, request: Request, submitted: ClaimSubmission):
    from .weekly_caper import claim_clue
    return _claims_action(request, lambda session, profile, authorize:
        claim_clue(session, profile, plan_id, authorize=authorize), write=True)


@router.post("/clover/claim")
def claim_weekly_clover(request: Request, submitted: CloverClaimSubmission):
    from .weekly_caper import claim_clover
    return _claims_action(request, lambda session, profile, authorize:
        claim_clover(session, profile, submitted.request_key, authorize=authorize), write=True)


@router.get("/pantry")
def load_pantry(request: Request):
    """Owner-scoped statistics and signed ledger balances; never settlement."""
    if request.query_params:
        raise HTTPException(422, "Pantry does not accept account selectors.")
    with SessionLocal() as session:
        profile = _authorized(request, session, feature="weekly_pantry")
        from .feature_access import can_use_feature
        from .models import WoodchuckState
        from .weekly_rewards import wallet
        from .economy import economy_payload
        instant = utc_instant()
        start = plans._week_start(instant)
        targets = None
        # Independent gates: an enabled Pantry can show progress without querying
        # saved-plan storage when plan issuance is unavailable.
        if can_use_feature(session, profile.id, "weekly_plans"):
            plan = plans.load_current_plan(session, profile.id, now=instant)
            if plan is not None:
                targets = {"logging_days": plan.logging_days_target,
                           "practice_seconds": plan.practice_minutes_target * 60}
        state = session.get(WoodchuckState, profile.id)
        result = {"context": {"week_start": start.isoformat()},
                  "progress": plans.current_general_progress(session, profile.id, now=instant),
                  "targets": targets, "wallet": wallet(session, profile.id),
                  "credits": economy_payload(state)["credits"] if state else 0}
        _authorized(request, session, refresh=True, feature="weekly_pantry")
        # No commit, state hydration, claim, grant, finalization or repair.
        return JSONResponse(result, headers={"Cache-Control": "no-store"})


class WheelStartSubmission(ClaimSubmission):
    request_key: StrictStr = Field(min_length=1, max_length=64)

    @field_validator("request_key")
    @classmethod
    def nonblank_key(cls, value):
        if not value.strip():
            raise ValueError("A durable Wheel request key is required.")
        return value


class WheelRotationSubmission(ClaimSubmission):
    rotation: int = Field(strict=True, ge=1, le=2147483647)


def _wheel_action(request, *, request_key=None, play_id=None, rotation=None):
    write = request_key is not None or rotation is not None
    if write and not same_origin_mutation(request):
        raise HTTPException(403, "A same-origin request is required.",
                            headers={"Cache-Control": "no-store"})
    if request.query_params:
        raise HTTPException(422, "Wheel requests do not accept query selectors.")
    for attempt in range(3):
        with SessionLocal() as session:
            try:
                profile = _authorized(request, session, feature="weekly_wheel")
                from . import weekly_wheel as wheel
                authorize = lambda locked: _authorized(request, locked, refresh=True, feature="weekly_wheel")
                play = None
                if request_key is not None:
                    play = wheel.fund_play(session, profile, request_key, authorize=authorize)
                elif rotation is not None:
                    play = wheel.continue_play(session, profile, play_id, rotation, authorize=authorize)
                elif play_id is not None:
                    play = wheel.owned_play(session, profile.id, play_id)
                authorize(session)
                result = wheel.status_payload(session, profile.id, play=play)
                response = JSONResponse(result, headers={"Cache-Control": "no-store"})
                if write:
                    authorize(session)
                    session.commit()
                return response
            except IntegrityError as error:
                session.rollback()
                from .weekly_wheel import is_wheel_unique_race
                if not write or not is_wheel_unique_race(error, session.get_bind().dialect.name):
                    raise
                if attempt == 2:
                    raise HTTPException(503, "Retry the same saved Wheel action.",
                                        headers={"Retry-After": "1"}) from error
            except OperationalError as error:
                session.rollback()
                if not write or session.get_bind().dialect.name != "sqlite" or not any(
                    message in str(error.orig).lower() for message in ("locked", "busy")
                ):
                    raise
                if attempt == 2:
                    raise HTTPException(503, "Retry the same saved Wheel action.",
                                        headers={"Retry-After": "1"}) from error


@router.get("/wheel")
def load_wheel(request: Request):
    return _wheel_action(request)


@router.get("/wheel/plays/{play_id}")
def recover_wheel(request: Request, play_id: str):
    return _wheel_action(request, play_id=play_id)


@router.post("/wheel/plays")
def fund_wheel(request: Request, submitted: WheelStartSubmission):
    return _wheel_action(request, request_key=submitted.request_key)


@router.post("/wheel/plays/{play_id}/rotations")
def rotate_wheel(request: Request, play_id: str, submitted: WheelRotationSubmission):
    return _wheel_action(request, play_id=play_id, rotation=submitted.rotation)
