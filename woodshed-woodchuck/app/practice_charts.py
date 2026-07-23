from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    PracticeChart,
    PracticeChartVerification,
    StudentVerifierConnection,
    TrustedVerifier,
    WoodchuckProfile,
)


MAX_PRACTICE_MINUTES = 1440
MAX_PRACTICE_DETAILS = 30
MAX_DETAIL_LENGTH = 50
MAX_DAILY_CREDITS = 75


@dataclass(frozen=True)
class CreatedPracticeChartRequest:
    chart: PracticeChart
    verification: PracticeChartVerification


def normalize_practice_details(
    practice_details: list[str] | tuple[str, ...] | None,
) -> list[str]:
    if practice_details is None:
        return []

    if not isinstance(practice_details, (list, tuple)):
        raise ValueError("Practice details must be a list.")

    normalized: list[str] = []

    for raw_detail in practice_details:
        if not isinstance(raw_detail, str):
            raise ValueError("Each practice detail must be text.")

        detail = raw_detail.strip()

        if not detail:
            continue

        if len(detail) > MAX_DETAIL_LENGTH:
            raise ValueError(
                "Each practice detail must be 50 characters or fewer."
            )

        if detail not in normalized:
            normalized.append(detail)

    if len(normalized) > MAX_PRACTICE_DETAILS:
        raise ValueError(
            "A P-Chart may contain no more than 30 practice details."
        )

    return normalized


def create_practice_chart_verification_request(
    session: Session,
    *,
    profile: WoodchuckProfile,
    verifier_id: int,
    practice_date: date,
    minutes: int,
    note: str = "",
    practice_details: list[str] | tuple[str, ...] | None = None,
    source: str = "p-book",
    credits_awarded: int = 0,
) -> CreatedPracticeChartRequest:
    if profile.id is None:
        raise ValueError("The student account must be saved first.")

    if type(practice_date) is not date:
        raise ValueError("A valid practice date is required.")

    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("Practice minutes must be a whole number.")

    if minutes < 1 or minutes > MAX_PRACTICE_MINUTES:
        raise ValueError(
            "Practice minutes must be between 1 and 1440."
        )

    if not isinstance(note, str):
        raise ValueError("The P-Chart note must be text.")

    normalized_note = note.strip()

    if len(normalized_note) > 180:
        raise ValueError(
            "The P-Chart note must be 180 characters or fewer."
        )

    if source != "p-book":
        raise ValueError("Unsupported P-Chart source.")

    if (
        isinstance(credits_awarded, bool)
        or not isinstance(credits_awarded, int)
        or credits_awarded < 0
        or credits_awarded > MAX_DAILY_CREDITS
    ):
        raise ValueError(
            "P-Chart credits must be between 0 and 75."
        )

    normalized_details = normalize_practice_details(
        practice_details
    )

    connection = session.scalar(
        select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id == profile.id,
            StudentVerifierConnection.verifier_id == verifier_id,
            StudentVerifierConnection.status == "accepted",
        )
    )

    if connection is None:
        raise ValueError(
            "That trusted verifier is not connected to this student."
        )

    instrument = profile.instrument.strip()

    if not instrument:
        raise ValueError(
            "The student must have an instrument before creating a P-Chart."
        )

    chart = PracticeChart(
        profile_id=profile.id,
        practice_date=practice_date,
        minutes=minutes,
        instrument=instrument,
        note=normalized_note or None,
        practice_details=normalized_details,
        source=source,
        credits_awarded=credits_awarded,
    )

    session.add(chart)
    session.flush()

    verification = PracticeChartVerification(
        practice_chart_id=chart.id,
        verifier_id=verifier_id,
        status="pending",
    )

    session.add(verification)

    try:
        session.commit()
        session.refresh(chart)
        session.refresh(verification)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError(
            "The P-Chart verification request could not be created."
        ) from error

    return CreatedPracticeChartRequest(
        chart=chart,
        verification=verification,
    )


def respond_to_practice_chart_verification(
    session: Session,
    *,
    verifier: TrustedVerifier,
    verification_id: int,
    decision: str,
    response_note: str = "",
) -> PracticeChartVerification:
    if verifier.id is None:
        raise ValueError("The verifier account must be saved first.")

    normalized_decision = decision.strip().lower()

    if normalized_decision not in {"approved", "rejected"}:
        raise ValueError(
            "The verification decision must be approved or rejected."
        )

    if not isinstance(response_note, str):
        raise ValueError("The verifier response note must be text.")

    normalized_note = response_note.strip()

    if len(normalized_note) > 300:
        raise ValueError(
            "The verifier response note must be "
            "300 characters or fewer."
        )

    verification = session.scalar(
        select(PracticeChartVerification).where(
            PracticeChartVerification.id == verification_id,
            PracticeChartVerification.verifier_id == verifier.id,
        )
    )

    if verification is None:
        raise LookupError("Verification request was not found.")

    if verification.status != "pending":
        raise ValueError(
            "That P-Chart verification request "
            "has already been answered."
        )

    chart = session.get(
        PracticeChart,
        verification.practice_chart_id,
    )

    if chart is None:
        raise LookupError("The requested P-Chart was not found.")

    connection = session.scalar(
        select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id == chart.profile_id,
            StudentVerifierConnection.verifier_id == verifier.id,
            StudentVerifierConnection.status == "accepted",
        )
    )

    if connection is None:
        raise ValueError(
            "This verifier is no longer connected to the student."
        )

    verification.status = normalized_decision
    verification.response_note = normalized_note or None
    verification.responded_at = datetime.now(timezone.utc)

    try:
        session.commit()
        session.refresh(verification)
    except IntegrityError as error:
        session.rollback()
        raise RuntimeError(
            "The P-Chart verification response could not be saved."
        ) from error

    return verification

