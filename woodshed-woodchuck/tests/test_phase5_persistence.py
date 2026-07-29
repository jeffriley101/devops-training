from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    CampPointAward,
    Contest,
    ContestResult,
    ContestWeek,
    CrownProgress,
    PracticeChart,
    RewardGrant,
    Season,
    StudentVerifierConnection,
    TrustedVerifier,
    WoodchuckProfile,
)


def test_camp_point_award_has_weekly_ledger_fields() -> None:
    assert set(CampPointAward.__table__.columns.keys()) == {
        "id", "profile_id", "activity_type", "points_awarded",
        "occurred_at", "duplicate_key", "created_at",
    }
from app.practice_chart_routes import PracticeChartCreate, chart_payload
from app.practice_charts import create_practice_chart_verification_request


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.fixture
def student(session: Session) -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id="WC-TEST",
        display_name="Student",
        pin_hash="not-a-real-hash",
        instrument="Saxophone",
        level="Beginner",
        goal="Practice",
    )
    session.add(profile)
    session.commit()
    return profile


def test_student_can_create_self_reported_chart(
    session: Session,
    student: WoodchuckProfile,
) -> None:
    created = create_practice_chart_verification_request(
        session,
        profile=student,
        verifier_id=None,
        practice_date=date(2026, 7, 28),
        minutes=30,
    )

    assert created.chart.id is not None
    assert created.verification is None
    assert session.query(PracticeChart).count() == 1
    assert chart_payload(created.chart, None, None)["verification"] is None

    omitted = PracticeChartCreate(
        practice_date=date(2026, 7, 28), minutes=30
    )
    explicit_null = PracticeChartCreate(
        verifier_id=None,
        practice_date=date(2026, 7, 28),
        minutes=30,
    )
    assert omitted.verifier_id is None
    assert explicit_null.verifier_id is None


def test_accepted_verifier_still_creates_pending_request(
    session: Session,
    student: WoodchuckProfile,
) -> None:
    verifier = TrustedVerifier(
        email="verifier@example.com",
        display_name="Verifier",
        pin_hash="not-a-real-hash",
    )
    session.add(verifier)
    session.flush()
    session.add(
        StudentVerifierConnection(
            profile_id=student.id,
            verifier_id=verifier.id,
            role="teacher",
            status="accepted",
        )
    )
    session.commit()

    created = create_practice_chart_verification_request(
        session,
        profile=student,
        verifier_id=verifier.id,
        practice_date=date(2026, 7, 28),
        minutes=30,
    )

    assert created.verification is not None
    assert created.verification.status == "pending"
    assert created.verification.verifier_id == verifier.id


@pytest.mark.parametrize("verifier_id", [999, 1000])
def test_invalid_or_disconnected_verifier_is_rejected(
    session: Session,
    student: WoodchuckProfile,
    verifier_id: int,
) -> None:
    if verifier_id == 1000:
        verifier = TrustedVerifier(
            id=verifier_id,
            email="disconnected@example.com",
            display_name="Disconnected",
            pin_hash="not-a-real-hash",
        )
        session.add(verifier)
        session.commit()

    with pytest.raises(ValueError, match="not connected"):
        create_practice_chart_verification_request(
            session,
            profile=student,
            verifier_id=verifier_id,
            practice_date=date(2026, 7, 28),
            minutes=30,
        )

    assert session.query(PracticeChart).count() == 0


def _assert_duplicate_rejected(session: Session, first: object, duplicate: object) -> None:
    session.add(first)
    session.commit()
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_contest_uniqueness_constraints(
    session: Session,
    student: WoodchuckProfile,
) -> None:
    season = Season(
        key="band-camp-2026",
        name="Band Camp 2026",
        starts_on=date(2026, 7, 27),
        status="active",
    )
    contest = Contest(
        key="camp-commitment",
        name="Camp Commitment",
        metric_type="practice_minutes",
        subject_type="student",
    )
    session.add_all([season, contest])
    session.commit()

    _assert_duplicate_rejected(
        session,
        ContestWeek(
            season_id=season.id,
            week_start=date(2026, 7, 27),
            week_end=date(2026, 8, 3),
            verification_deadline_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            finalize_after=datetime(2026, 8, 5, tzinfo=timezone.utc),
            status="open",
        ),
        ContestWeek(
            season_id=season.id,
            week_start=date(2026, 7, 27),
            week_end=date(2026, 8, 3),
            verification_deadline_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            finalize_after=datetime(2026, 8, 5, tzinfo=timezone.utc),
            status="open",
        ),
    )

    week = session.query(ContestWeek).one()
    _assert_duplicate_rejected(
        session,
        ContestResult(
            contest_week_id=week.id,
            contest_id=contest.id,
            division="open",
            subject_type="student",
            subject_key=str(student.id),
            profile_id=student.id,
            display_name_snapshot=student.display_name,
            score=120,
            rank=1,
            medal="gold",
        ),
        ContestResult(
            contest_week_id=week.id,
            contest_id=contest.id,
            division="open",
            subject_type="student",
            subject_key=str(student.id),
            profile_id=student.id,
            display_name_snapshot=student.display_name,
            score=120,
            rank=1,
            medal="gold",
        ),
    )
    _assert_duplicate_rejected(
        session,
        RewardGrant(
            profile_id=student.id,
            source_key="week:1:contest:1",
            reward_type="dandelion",
            amount=1,
        ),
        RewardGrant(
            profile_id=student.id,
            source_key="week:1:contest:1",
            reward_type="dandelion",
            amount=1,
        ),
    )
    _assert_duplicate_rejected(
        session,
        CrownProgress(profile_id=student.id, category_key="commitment"),
        CrownProgress(profile_id=student.id, category_key="commitment"),
    )


def test_season_and_contest_keys_are_unique(session: Session) -> None:
    _assert_duplicate_rejected(
        session,
        Season(
            key="band-camp-2026",
            name="Band Camp",
            starts_on=date(2026, 7, 27),
            status="planned",
        ),
        Season(
            key="band-camp-2026",
            name="Duplicate",
            starts_on=date(2026, 8, 3),
            status="planned",
        ),
    )
    _assert_duplicate_rejected(
        session,
        Contest(
            key="commitment",
            name="Commitment",
            metric_type="practice_minutes",
            subject_type="student",
        ),
        Contest(
            key="commitment",
            name="Duplicate",
            metric_type="points",
            subject_type="instrument",
        ),
    )


def test_contest_models_match_approved_foundation() -> None:
    expected_columns = {
        Season: {
            "id", "key", "name", "timezone", "starts_on", "ends_on",
            "status", "created_at", "updated_at",
        },
        Contest: {
            "id", "key", "name", "metric_type", "subject_type",
            "crown_category", "active", "created_at", "updated_at",
        },
        ContestWeek: {
            "id", "season_id", "week_start", "week_end",
            "verification_deadline_at", "finalize_after", "status",
            "finalized_at", "created_at", "updated_at",
        },
        ContestResult: {
            "id", "contest_week_id", "contest_id", "division",
            "subject_type", "subject_key", "profile_id", "instrument",
            "display_name_snapshot", "score", "rank", "medal", "created_at",
        },
        RewardGrant: {
            "id", "profile_id", "contest_result_id", "source_key",
            "reward_type", "category_key", "amount", "created_at",
        },
        CrownProgress: {
            "id", "profile_id", "category_key", "qualifying_wins",
            "crown_earned_at", "created_at", "updated_at",
        },
        PracticeChart: {
            "id", "profile_id", "practice_date", "minutes", "instrument",
            "note", "practice_details", "source", "submission_key",
            "credits_awarded", "created_at", "updated_at",
        },
    }

    for model, columns in expected_columns.items():
        assert set(model.__table__.columns.keys()) == columns
