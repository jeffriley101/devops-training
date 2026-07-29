from __future__ import annotations

import hashlib
import hmac
import io
import os
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contest_jobs import (
    WeekCandidate,
    candidate_reason,
    latest_finalize_outcome,
    run_finalize_due_weeks,
)
from .contest_seasons import (
    SeasonRolloverError,
    rollover_season,
    season_status_payload,
)
from .contests import CENTRAL, finalize_contest_week, utc_iso
from .db import SessionLocal
from .models import ContestWeek, Season


router = APIRouter(prefix="/contests/admin", tags=["contest-admin"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)
ADMIN_SESSION_KEY = "contest_admin_token_fingerprint"


def _configured_token() -> str:
    token = os.getenv("CONTEST_ADMIN_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Contest administration is unavailable.")
    return token


def _fingerprint(token: str) -> str:
    session_secret = os.getenv(
        "SESSION_SECRET", "woodshed-local-development-secret"
    ).encode("utf-8")
    return hmac.new(
        session_secret,
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def require_contest_admin(request: Request) -> None:
    configured = _configured_token()
    expected = _fingerprint(configured)
    session_fingerprint = request.session.get(ADMIN_SESSION_KEY)
    if isinstance(session_fingerprint, str) and hmac.compare_digest(
        session_fingerprint, expected
    ):
        return
    supplied = request.headers.get("X-Contest-Admin-Token", "")
    if not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=403, detail="Invalid contest admin token.")
    request.session[ADMIN_SESSION_KEY] = expected


def _active_season(session: Session) -> Season | None:
    return session.scalar(
        select(Season).where(
            Season.status == "active",
            Season.key.like("band-camp-%"),
        ).order_by(Season.starts_on.desc())
    )


def _current_week(session: Session, season: Season | None, now: datetime) -> ContestWeek | None:
    if season is None:
        return None
    central_today = now.astimezone(CENTRAL).date()
    week = session.scalar(
        select(ContestWeek).where(
            ContestWeek.season_id == season.id,
            ContestWeek.week_start <= central_today,
            ContestWeek.week_end > central_today,
        ).order_by(ContestWeek.week_start.desc())
    )
    if week is not None:
        return week
    return session.scalar(
        select(ContestWeek).where(ContestWeek.season_id == season.id).order_by(
            ContestWeek.week_start.desc()
        )
    )


def _week_reason(week: ContestWeek | None, now: datetime) -> str | None:
    if week is None:
        return "no_contest_week"
    return candidate_reason(
        WeekCandidate(
            week_start=week.week_start,
            week_end=week.week_end,
            status=week.status,
            verification_deadline_at=week.verification_deadline_at,
            finalize_after=week.finalize_after,
        ),
        now,
    )


def admin_status(session: Session, *, now: datetime) -> dict[str, object]:
    readiness = season_status_payload(session, now=now)
    season = _active_season(session)
    week = _current_week(session, season, now)
    reason = _week_reason(week, now)
    return {
        **readiness,
        "current_week": None if week is None else {
            "week_start": week.week_start.isoformat(),
            "week_end": week.week_end.isoformat(),
            "status": week.status,
            "verification_deadline_at": utc_iso(week.verification_deadline_at),
            "finalize_after": utc_iso(week.finalize_after),
            "finalized_at": utc_iso(week.finalized_at),
        },
        "finalization_due": reason is None,
        "finalization_blocking_reason": reason,
        "latest_job_outcome": latest_finalize_outcome(),
    }


def _flash(request: Request, kind: str, message: str) -> None:
    request.session["contest_admin_flash"] = {"kind": kind, "message": message}


def _redirect() -> RedirectResponse:
    return RedirectResponse(url="/contests/admin", status_code=303)


@router.get("")
def contest_admin_page(request: Request):
    require_contest_admin(request)
    with SessionLocal() as session:
        status = admin_status(session, now=datetime.now(timezone.utc))
    flash = request.session.pop("contest_admin_flash", None)
    return templates.TemplateResponse(
        request=request,
        name="contest_admin.html",
        context={"status": status, "flash": flash},
    )


@router.post("/finalize-current")
def finalize_current_week(request: Request):
    require_contest_admin(request)
    now = datetime.now(timezone.utc)
    try:
        with SessionLocal() as session:
            with session.begin():
                season = _active_season(session)
                week = _current_week(session, season, now)
                reason = _week_reason(week, now)
                if week is None or reason is not None:
                    raise SeasonRolloverError(
                        f"Current week cannot be finalized: {reason or 'unavailable'}."
                    )
                finalize_contest_week(session, week_start=week.week_start, now=now)
        _flash(request, "success", "Current contest week finalized successfully.")
    except SeasonRolloverError as error:
        _flash(request, "error", str(error)[:240])
    except Exception:
        _flash(request, "error", "Current week finalization failed without partial changes.")
    return _redirect()


@router.post("/finalize-due")
def finalize_all_due(request: Request):
    require_contest_admin(request)
    exit_code, summary = run_finalize_due_weeks(
        session_factory=SessionLocal,
        stream=io.StringIO(),
    )
    if summary.due == 0:
        _flash(request, "info", "No contest weeks are currently due.")
    elif exit_code:
        _flash(
            request,
            "error",
            f"Finalized {summary.finalized}; {summary.failed} due week(s) failed.",
        )
    else:
        _flash(request, "success", f"Finalized {summary.finalized} due week(s).")
    return _redirect()


@router.post("/readiness")
def check_rollover_readiness(request: Request):
    require_contest_admin(request)
    with SessionLocal() as session:
        readiness = season_status_payload(session, now=datetime.now(timezone.utc))
    blockers = readiness["blocking_reasons"]
    if blockers:
        _flash(request, "info", "Rollover blocked: " + ", ".join(blockers) + ".")
    else:
        _flash(request, "success", "Season is ready for explicit rollover configuration.")
    return _redirect()


@router.post("/rollover")
def run_rollover(
    request: Request,
    source_key: str = Form(...),
    next_key: str = Form(...),
    next_name: str = Form(...),
    next_start: date = Form(...),
    next_end: date = Form(...),
    confirmation: str = Form(...),
):
    require_contest_admin(request)
    if confirmation.strip() != "ROLL OVER":
        _flash(request, "error", 'Rollover confirmation must exactly match “ROLL OVER”.')
        return _redirect()
    try:
        with SessionLocal() as session:
            with session.begin():
                result = rollover_season(
                    session,
                    source_key=source_key,
                    next_key=next_key,
                    next_name=next_name,
                    next_starts_on=next_start,
                    next_ends_on=next_end,
                    now=datetime.now(timezone.utc),
                )
        message = (
            f"Rollover complete: {result.next_key} with "
            f"{result.weeks_created} contest week(s)."
            if result.created
            else f"Rollover already complete for {result.next_key}; no changes made."
        )
        _flash(request, "success", message)
    except SeasonRolloverError as error:
        _flash(request, "error", f"Rollover blocked: {str(error)[:200]}")
    except Exception:
        _flash(request, "error", "Season rollover failed without changing contest history.")
    return _redirect()
