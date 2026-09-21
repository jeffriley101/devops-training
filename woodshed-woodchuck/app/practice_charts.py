from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .economy import lock_state
from .models import (
    PracticeChart,
    PracticeChartVerification,
    QuestCompletion,
    RewardGrant,
    StudentVerifierConnection,
    TrustedVerifier,
    WoodchuckProfile,
)


MAX_PRACTICE_MINUTES = 1440
MAX_DETECTED_PLAYING_SECONDS = MAX_PRACTICE_MINUTES * 60
MAX_PRACTICE_DETAILS = 30
MAX_DETAIL_LENGTH = 50
MAX_DAILY_CREDITS = 75


@dataclass(frozen=True)
class CreatedPracticeChartRequest:
    chart: PracticeChart
    verification: PracticeChartVerification | None
    created: bool = True


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
    verifier_id: int | None,
    practice_date: date,
    minutes: int,
    note: str = "",
    practice_details: list[str] | tuple[str, ...] | None = None,
    source: str = "p-book",
    credits_awarded: int = 0,
    submission_key: str | None = None,
    include_contests: bool = True,
    include_team_contests: bool = True,
    team_id: int | None = None,
    ordinary_email_preset_id: int | None = None,
    detected_playing_seconds: int | None = None,
    award_dandelions: bool = False,
) -> CreatedPracticeChartRequest:
    from .age_privacy import require_eligible, ordinary_director_connection
    from .child_authorization import protected_child, permitted_director
    require_eligible(session,profile.id)
    if type(practice_date) is not date:
        raise ValueError("A valid practice date is required.")

    if protected_child(session,profile.id):
        from .age_privacy import can_publish
        if not can_publish(session,profile.id):include_contests=False
        include_team_contests=False;team_id=None;ordinary_email_preset_id=None
        if verifier_id is not None and not permitted_director(session,profile.id,verifier_id,review=True):
            from .age_privacy import ordinary_director_connection
            from .age_models import AccountPrivacy
            from .contests import CENTRAL
            rule=session.get(AccountPrivacy,profile.id)
            if not ordinary_director_connection(session,profile.id,verifier_id) or practice_date<=rule.public_from.astimezone(CENTRAL).date():
                raise ValueError('A new director may review only eligible post-transition practice.')
    if profile.id is None:
        raise ValueError("The student account must be saved first.")

    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("Practice minutes must be a whole number.")

    if source not in {"p-book", "pristine"}:
        raise ValueError("Unsupported P-Chart source.")
    if source == "pristine":
        if verifier_id is not None:
            raise ValueError("Pristine P-Charts do not require a verifier.")
        if (
            isinstance(detected_playing_seconds, bool)
            or not isinstance(detected_playing_seconds, int)
            or detected_playing_seconds < 1
            or detected_playing_seconds > MAX_DETECTED_PLAYING_SECONDS
        ):
            raise ValueError(
                "Detected playing time must be between 1 and 86400 seconds."
            )
        if minutes != detected_playing_seconds // 60:
            raise ValueError("Pristine minutes must match detected playing time.")
        if credits_awarded != 0 or ordinary_email_preset_id is not None:
            raise ValueError("Pristine P-Charts cannot request delivery or credits.")
    else:
        if minutes < 1 or minutes > MAX_PRACTICE_MINUTES:
            raise ValueError(
                "Practice minutes must be between 1 and 1440."
            )
        if detected_playing_seconds is not None:
            raise ValueError("Only Pristine P-Charts store detected playing time.")

    if not isinstance(note, str):
        raise ValueError("The P-Chart note must be text.")

    normalized_note = note.strip()

    if len(normalized_note) > 180:
        raise ValueError(
            "The P-Chart note must be 180 characters or fewer."
        )

    if type(include_contests) is not bool:
        raise ValueError("Contest inclusion must be true or false.")
    if type(include_team_contests) is not bool:
        raise ValueError("Team contest inclusion must be true or false.")

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

    if verifier_id is not None:
        connection = session.scalar(
            select(StudentVerifierConnection).where(
                StudentVerifierConnection.profile_id == profile.id,
                StudentVerifierConnection.verifier_id == verifier_id,
                StudentVerifierConnection.status == "accepted",
                StudentVerifierConnection.role.in_(("verifier", "band_director")),
            )
        )

        if connection is None or (connection.role=='band_director' and not (permitted_director(session,profile.id,verifier_id,review=True) or ordinary_director_connection(session,profile.id,verifier_id))):
            raise ValueError('Choose an accepted, authorized chart reviewer.')

    state = lock_state(session, profile.id) if award_dandelions else None

    if submission_key is not None:
        if not isinstance(submission_key, str):
            raise ValueError("The P-Chart submission key must be text.")
        submission_key = submission_key.strip()
        if not submission_key or len(submission_key) > 64:
            raise ValueError(
                "The P-Chart submission key must be between 1 and 64 characters."
            )
        existing_chart = session.scalar(
            select(PracticeChart).where(
                PracticeChart.profile_id == profile.id,
                PracticeChart.submission_key == submission_key,
            )
        )
        if existing_chart is not None:
            if existing_chart.source != source:
                raise ValueError(
                    "That submission key belongs to a different P-Chart type."
                )
            existing_verification = session.scalar(
                select(PracticeChartVerification).where(
                    PracticeChartVerification.practice_chart_id
                    == existing_chart.id
                )
            )
            return CreatedPracticeChartRequest(
                chart=existing_chart,
                verification=existing_verification,
                created=False,
            )

    instrument = profile.instrument.strip()

    if not instrument:
        raise ValueError(
            "The student must have an instrument before creating a P-Chart."
        )

    if award_dandelions:
        # Match BOOK's existing formula/cap using persisted records, not its
        # truncated browser history or the submitted credits_awarded value.
        earned = session.scalar(select(func.coalesce(func.sum(PracticeChart.credits_awarded), 0)).where(
            PracticeChart.profile_id == profile.id, PracticeChart.practice_date == practice_date,
        )) or 0
        completion = session.scalar(select(QuestCompletion).where(
            QuestCompletion.profile_id == profile.id, QuestCompletion.activity_date == practice_date,
        ))
        # Only the legacy quest endpoint inserts a rewarded BOOK log entry.
        # The current Board Bonus Challenge has always awarded separately.
        if completion is not None and session.scalar(select(RewardGrant.id).where(
            RewardGrant.profile_id == profile.id,
            RewardGrant.reward_type == "dandelion",
            RewardGrant.source_key == f"bonus-challenge:{practice_date.isoformat()}:{completion.quest_id}",
        )) is not None:
            earned += completion.reward_amount
        credits_awarded = (min(minutes // 5 + len(normalized_details), max(0, MAX_DAILY_CREDITS - earned))
                           if source == "p-book" else 0)

    chart = PracticeChart(
        profile_id=profile.id,
        practice_date=practice_date,
        minutes=minutes,
        instrument=instrument,
        note=normalized_note or None,
        practice_details=normalized_details,
        source=source,
        detected_playing_seconds=detected_playing_seconds,
        credits_awarded=credits_awarded,
        submission_key=submission_key,
        include_contests=include_contests,
        include_team_contests=include_team_contests,
        team_id=team_id if include_team_contests else None,
        ordinary_email_preset_id=ordinary_email_preset_id,
    )

    session.add(chart)
    session.flush()

    if award_dandelions and credits_awarded:
        payload = deepcopy(state.state_json or {})
        progress = dict(payload.get("progress") or {})
        balance = progress.get("credits", 0)
        balance = balance if type(balance) is int else 0
        progress["credits"] = balance + credits_awarded
        payload["progress"] = progress
        state.state_json = payload
        state.revision += 1
        session.add(RewardGrant(profile_id=profile.id, source_key=f"practice-chart:{chart.id}",
                                reward_type="dandelion", category_key="practice", amount=credits_awarded))

    verification = None
    if verifier_id is not None:
        verification = PracticeChartVerification(
            practice_chart_id=chart.id,
            verifier_id=verifier_id,
            status="pending",
        )
        session.add(verification)

    try:
        session.commit()
        session.refresh(chart)
        if verification is not None:
            session.refresh(verification)
    except IntegrityError as error:
        session.rollback()
        if submission_key is not None:
            existing_chart = session.scalar(
                select(PracticeChart).where(
                    PracticeChart.profile_id == profile.id,
                    PracticeChart.submission_key == submission_key,
                )
            )
            if existing_chart is not None:
                if existing_chart.source != source:
                    raise ValueError(
                        "That submission key belongs to a different P-Chart type."
                    )
                existing_verification = session.scalar(
                    select(PracticeChartVerification).where(
                        PracticeChartVerification.practice_chart_id
                        == existing_chart.id
                    )
                )
                return CreatedPracticeChartRequest(
                    chart=existing_chart,
                    verification=existing_verification,
                    created=False,
                )
        raise RuntimeError(
            "The P-Chart verification request could not be created."
        ) from error

    return CreatedPracticeChartRequest(
        chart=chart,
        verification=verification,
    )


def create_pristine_practice_chart(
    session: Session,
    *,
    profile: WoodchuckProfile,
    detected_playing_seconds: int,
    submission_key: str,
    include_contests: bool = True,
    include_team_contests: bool = True,
    team_id: int | None = None,
    practice_date: date,
) -> CreatedPracticeChartRequest:
    """Persist one self-verified microphone-activity practice session."""
    return create_practice_chart_verification_request(
        session,
        profile=profile,
        verifier_id=None,
        practice_date=practice_date,
        minutes=detected_playing_seconds // 60,
        note="Pristine Practice",
        practice_details=[],
        source="pristine",
        credits_awarded=0,
        submission_key=submission_key,
        include_contests=include_contests,
        include_team_contests=include_team_contests,
        team_id=team_id,
        detected_playing_seconds=detected_playing_seconds,
    )


def respond_to_practice_chart_verification(
    session: Session,
    *,
    verifier: TrustedVerifier,
    verification_id: int,
    decision: str,
    response_note: str = "",
    request=None,
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

    from .age_privacy import require_eligible
    require_eligible(session,chart.profile_id)
    from .child_authorization import protected_child, director_authenticated
    from .age_privacy import director_chart_visible
    if protected_child(session,chart.profile_id) and not director_chart_visible(session,chart,verifier.id):
        if request is None:raise ValueError('Verify director access before reviewing private charts.')
        permission=director_authenticated(session,request,review=True)
        if permission.profile_id!=chart.profile_id:raise ValueError('Chart belongs to another student.')
        from .models import StudentVerifierConnection as Connection
        if session.get(Connection,permission.connection_id).verifier_id!=verifier.id:raise ValueError('Wrong chart reviewer.')
        verification=session.scalar(select(PracticeChartVerification).where(PracticeChartVerification.id==verification_id).with_for_update().execution_options(populate_existing=True))
        if verification.status!='pending':raise ValueError('This chart has already been answered.')
    connection = session.scalar(
        select(StudentVerifierConnection).where(
            StudentVerifierConnection.profile_id == chart.profile_id,
            StudentVerifierConnection.verifier_id == verifier.id,
            StudentVerifierConnection.status == "accepted",
            StudentVerifierConnection.role.in_(("verifier", "band_director")),
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
