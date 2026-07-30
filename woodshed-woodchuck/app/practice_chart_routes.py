from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import distinct, func, select

from .account_routes import current_profile
from .db import SessionLocal
from .models import (
    PracticeChart,
    PracticeChartVerification,
    TrustedVerifier,
)
from .practice_charts import (
    create_practice_chart_verification_request,
)


router = APIRouter(
    prefix="/practice-charts",
    tags=["practice-charts"],
)

CENTRAL = ZoneInfo("America/Chicago")


def practice_streak(practice_dates: list[date], today: date) -> int:
    days = sorted(set(practice_dates), reverse=True)
    if not days or days[0] < today - timedelta(days=1):
        return 0
    streak = 1
    for newer, older in zip(days, days[1:]):
        if newer - older != timedelta(days=1):
            break
        streak += 1
    return streak


def profile_practice_streak(session, profile_id: int, today: date | None = None) -> int:
    dates = list(session.scalars(
        select(distinct(PracticeChart.practice_date)).where(
            PracticeChart.profile_id == profile_id,
            PracticeChart.minutes > 0,
        )
    ))
    return practice_streak(dates, today or datetime.now(CENTRAL).date())


def format_practice_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, remaining = divmod(minutes, 60)
    hour_text = f"{hours} hour{'s' if hours != 1 else ''}"
    return hour_text if remaining == 0 else f"{hour_text} {remaining} minutes"


def practice_totals_payload(session, profile_id: int, today: date | None = None) -> dict[str, object]:
    today = today or datetime.now(CENTRAL).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)
    career = session.scalar(select(func.coalesce(func.sum(PracticeChart.minutes), 0)).where(
        PracticeChart.profile_id == profile_id, PracticeChart.minutes > 0,
    )) or 0
    weekly = session.scalar(select(func.coalesce(func.sum(PracticeChart.minutes), 0)).where(
        PracticeChart.profile_id == profile_id, PracticeChart.minutes > 0,
        PracticeChart.practice_date >= week_start,
        PracticeChart.practice_date < week_end,
    )) or 0
    return {
        "week_start": week_start.isoformat(), "week_end": (week_end - timedelta(days=1)).isoformat(),
        "this_week_minutes": int(weekly), "this_week_display": format_practice_minutes(int(weekly)),
        "career_minutes": int(career), "career_display": format_practice_minutes(int(career)),
    }


class PracticeChartCreate(BaseModel):
    verifier_id: int | None = Field(default=None, gt=0)
    practice_date: date
    minutes: int
    note: str = ""
    practice_details: list[str] = Field(default_factory=list)
    source: str = "p-book"
    credits_awarded: int = 0
    submission_key: str | None = Field(default=None, min_length=1, max_length=64)
    include_contests: StrictBool = True


def verification_payload(
    verification: PracticeChartVerification,
    verifier: TrustedVerifier | None,
) -> dict[str, object]:
    return {
        "id": verification.id,
        "verifier_id": verification.verifier_id,
        "verifier": (
            {
                "id": verifier.id,
                "display_name": verifier.display_name,
                "email": verifier.email,
            }
            if verifier is not None
            else None
        ),
        "status": verification.status,
        "response_note": verification.response_note,
        "requested_at": verification.requested_at.isoformat(),
        "responded_at": (
            verification.responded_at.isoformat()
            if verification.responded_at is not None
            else None
        ),
    }


def chart_payload(
    chart: PracticeChart,
    verification: PracticeChartVerification | None,
    verifier: TrustedVerifier | None,
) -> dict[str, object]:
    return {
        "id": chart.id,
        "practice_date": chart.practice_date.isoformat(),
        "minutes": chart.minutes,
        "instrument": chart.instrument,
        "note": chart.note or "",
        "practice_details": chart.practice_details,
        "source": chart.source,
        "credits_awarded": chart.credits_awarded,
        "include_contests": chart.include_contests,
        "created_at": chart.created_at.isoformat(),
        "verification": (
            verification_payload(verification, verifier)
            if verification is not None
            else None
        ),
    }


@router.get("")
def list_student_practice_charts(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Student sign-in is required.",
            )

        rows = session.execute(
            select(
                PracticeChart,
                PracticeChartVerification,
                TrustedVerifier,
            )
            .outerjoin(
                PracticeChartVerification,
                PracticeChartVerification.practice_chart_id
                == PracticeChart.id,
            )
            .outerjoin(
                TrustedVerifier,
                TrustedVerifier.id
                == PracticeChartVerification.verifier_id,
            )
            .where(
                PracticeChart.profile_id == profile.id,
            )
            .order_by(
                PracticeChart.practice_date.desc(),
                PracticeChart.id.desc(),
            )
        ).all()

        return {
            "charts": [
                chart_payload(
                    chart,
                    verification,
                    verifier,
                )
                for chart, verification, verifier in rows
            ]
        }


@router.get("/streak")
def student_practice_streak(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return {
            "streak": profile_practice_streak(session, profile.id),
            "qualifying_day": "A calendar day with at least one persisted P-Chart containing practice minutes.",
        }


@router.get("/totals")
def student_practice_totals(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return practice_totals_payload(session, profile.id)


@router.post("", status_code=201)
def create_student_practice_chart(
    request: Request,
    submitted: PracticeChartCreate,
):
    with SessionLocal() as session:
        profile = current_profile(request, session)

        if profile is None:
            raise HTTPException(
                status_code=401,
                detail="Student sign-in is required.",
            )

        try:
            created = create_practice_chart_verification_request(
                session,
                profile=profile,
                verifier_id=submitted.verifier_id,
                practice_date=submitted.practice_date,
                minutes=submitted.minutes,
                note=submitted.note,
                practice_details=submitted.practice_details,
                source=submitted.source,
                credits_awarded=submitted.credits_awarded,
                submission_key=submitted.submission_key,
                include_contests=submitted.include_contests,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail=str(error),
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=500,
                detail=(
                    "The P-Chart verification request "
                    "could not be created."
                ),
            ) from error

        verifier = (
            session.get(
                TrustedVerifier,
                created.verification.verifier_id,
            )
            if created.verification is not None
            and created.verification.verifier_id is not None
            else None
        )

        return {
            "created": created.created,
            "streak": profile_practice_streak(session, profile.id),
            "chart": chart_payload(
                created.chart,
                created.verification,
                verifier,
            ),
        }
