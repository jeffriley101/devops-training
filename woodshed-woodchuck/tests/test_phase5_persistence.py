from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
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
    season = Season(key="band-camp-2026")
    contest = Contest(key="camp-commitment")
    session.add_all([season, contest])
    session.commit()

    _assert_duplicate_rejected(
        session,
        ContestWeek(season_id=season.id, week_start=date(2026, 7, 27)),
        ContestWeek(season_id=season.id, week_start=date(2026, 7, 27)),
    )

    week = session.query(ContestWeek).one()
    _assert_duplicate_rejected(
        session,
        ContestResult(
            contest_week_id=week.id,
            contest_id=contest.id,
            division="rookie",
            subject_key="saxophone",
        ),
        ContestResult(
            contest_week_id=week.id,
            contest_id=contest.id,
            division="rookie",
            subject_key="saxophone",
        ),
    )
    _assert_duplicate_rejected(
        session,
        RewardGrant(
            profile_id=student.id,
            source_key="week:1:contest:1",
            reward_type="dandelion",
        ),
        RewardGrant(
            profile_id=student.id,
            source_key="week:1:contest:1",
            reward_type="dandelion",
        ),
    )
    _assert_duplicate_rejected(
        session,
        CrownProgress(profile_id=student.id, category_key="commitment"),
        CrownProgress(profile_id=student.id, category_key="commitment"),
    )
