from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import contests as contest_module
from app.account_routes import SESSION_PROFILE_ID
from app.contests import (
    central_week_boundaries,
    current_contests,
    current_contests_payload,
    ensure_band_camp_data,
    weekly_practice_by_instrument,
)
from app.db import Base
from app.models import (
    Contest,
    ContestWeek,
    PracticeChart,
    PracticeChartVerification,
    Season,
    WoodchuckProfile,
)


NOW = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)


@pytest.fixture
def database() -> tuple[Session, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session, factory


def add_student(
    session: Session,
    *,
    woodchuck_id: str,
    instrument: str,
) -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id=woodchuck_id,
        display_name=f"Student {woodchuck_id}",
        pin_hash="hash",
        instrument=instrument,
        level="Beginner",
        goal="Practice",
    )
    session.add(profile)
    session.flush()
    return profile


def add_chart(
    session: Session,
    *,
    profile: WoodchuckProfile,
    practice_date: date,
    minutes: int,
    instrument: str | None = None,
    verification_status: str | None = None,
) -> PracticeChart:
    chart = PracticeChart(
        profile_id=profile.id,
        practice_date=practice_date,
        minutes=minutes,
        instrument=instrument or profile.instrument,
        practice_details=[],
        source="p-book",
        credits_awarded=0,
    )
    session.add(chart)
    session.flush()
    if verification_status is not None:
        session.add(
            PracticeChartVerification(
                practice_chart_id=chart.id,
                verifier_id=None,
                status=verification_status,
            )
        )
    session.flush()
    return chart


def request_with_session(profile_id: int | None = None) -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/contests/current",
        "headers": [],
        "query_string": b"",
        "session": {},
    }
    if profile_id is not None:
        scope["session"] = {SESSION_PROFILE_ID: profile_id}
    return Request(scope)  # type: ignore[arg-type]


def test_current_week_uses_central_monday_boundaries() -> None:
    start, end, deadline, finalize_after = central_week_boundaries(NOW)

    assert start == date(2026, 7, 27)
    assert end == date(2026, 8, 3)
    assert deadline == datetime(2026, 8, 3, 17, tzinfo=timezone.utc)
    assert finalize_after == datetime(2026, 8, 3, 17, 5, tzinfo=timezone.utc)


def test_band_camp_records_are_created_idempotently(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    first = ensure_band_camp_data(session, now=NOW)
    second = ensure_band_camp_data(session, now=NOW)

    assert first[0].id == second[0].id
    assert first[2].id == second[2].id
    assert session.scalar(select(func.count()).select_from(Season)) == 1
    assert session.scalar(select(func.count()).select_from(Contest)) == 2
    assert session.scalar(select(func.count()).select_from(ContestWeek)) == 1
    assert {contest.key for contest in second[1]} == {
        "weekly-points-leaders",
        "weekly-practice-by-instrument",
    }


def test_weekly_practice_divisions_and_boundaries(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, contest_week = ensure_band_camp_data(session, now=NOW)
    first = add_student(session, woodchuck_id="WC-ONE", instrument="Saxophone")
    second = add_student(session, woodchuck_id="WC-TWO", instrument="saxophone")
    third = add_student(session, woodchuck_id="WC-THREE", instrument="Trumpet")

    add_chart(
        session,
        profile=first,
        practice_date=date(2026, 7, 27),
        minutes=20,
    )
    add_chart(
        session,
        profile=first,
        practice_date=date(2026, 7, 28),
        minutes=30,
        verification_status="approved",
    )
    add_chart(
        session,
        profile=second,
        practice_date=date(2026, 8, 2),
        minutes=50,
        instrument="  SAXOPHONE ",
        verification_status="approved",
    )
    add_chart(
        session,
        profile=third,
        practice_date=date(2026, 7, 29),
        minutes=40,
        verification_status="pending",
    )
    add_chart(
        session,
        profile=third,
        practice_date=date(2026, 7, 30),
        minutes=10,
        verification_status="rejected",
    )
    add_chart(
        session,
        profile=third,
        practice_date=date(2026, 7, 26),
        minutes=500,
        verification_status="approved",
    )
    add_chart(
        session,
        profile=third,
        practice_date=date(2026, 8, 3),
        minutes=500,
        verification_status="approved",
    )
    session.commit()

    standings = weekly_practice_by_instrument(
        session,
        contest_week=contest_week,
    )

    assert standings["open"] == [
        {"rank": 1, "instrument": "Saxophone", "total_minutes": 100},
        {"rank": 2, "instrument": "Trumpet", "total_minutes": 50},
    ]
    assert standings["verified"] == [
        {"rank": 1, "instrument": "Saxophone", "total_minutes": 80},
    ]


def test_ties_use_olympic_ranking_and_instrument_sorting(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, contest_week = ensure_band_camp_data(session, now=NOW)
    for index, (instrument, minutes) in enumerate(
        (("Trumpet", 60), ("Clarinet", 60), ("Flute", 30)),
        start=1,
    ):
        profile = add_student(
            session,
            woodchuck_id=f"WC-TIE-{index}",
            instrument=instrument,
        )
        add_chart(
            session,
            profile=profile,
            practice_date=date(2026, 7, 30),
            minutes=minutes,
        )
    session.commit()

    assert weekly_practice_by_instrument(
        session,
        contest_week=contest_week,
    )["open"] == [
        {"rank": 1, "instrument": "Clarinet", "total_minutes": 60},
        {"rank": 1, "instrument": "Trumpet", "total_minutes": 60},
        {"rank": 3, "instrument": "Flute", "total_minutes": 30},
    ]


def test_endpoint_requires_authentication_and_exposes_no_private_data(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    profile = add_student(
        session,
        woodchuck_id="WC-PRIVATE",
        instrument="Saxophone",
    )
    add_chart(
        session,
        profile=profile,
        practice_date=date(2026, 7, 28),
        minutes=45,
        verification_status="approved",
    )
    session.commit()
    monkeypatch.setattr(contest_module, "SessionLocal", factory)

    with pytest.raises(HTTPException) as unauthorized:
        current_contests(request_with_session())
    assert unauthorized.value.status_code == 401

    payload = current_contests(request_with_session(profile.id))
    serialized = repr(payload).casefold()
    assert payload["standings"]["weekly-practice-by-instrument"]["open"]
    for private_field in (
        "email",
        "verifier",
        "pin_hash",
        "woodchuck_id",
        "display_name",
        "legal_name",
    ):
        assert private_field not in serialized

    direct_payload = current_contests_payload(session, now=NOW)
    assert direct_payload["season"] == {
        "key": "band-camp-2026",
        "name": "Band Camp",
        "timezone": "America/Chicago",
        "status": "active",
        "starts_on": "2026-07-27",
        "ends_on": None,
    }
