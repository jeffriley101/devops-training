from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

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


class PracticeChartCreate(BaseModel):
    verifier_id: int | None = Field(default=None, gt=0)
    practice_date: date
    minutes: int
    note: str = ""
    practice_details: list[str] = Field(default_factory=list)
    source: str = "p-book"
    credits_awarded: int = 0


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
            "created": True,
            "chart": chart_payload(
                created.chart,
                created.verification,
                verifier,
            ),
        }
