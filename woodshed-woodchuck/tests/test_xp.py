from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, xp_routes
from app.db import Base
from app.main import app
from app.models import (
    CampPointAward,
    PlungePointAward,
    PracticeChart,
    PracticeChartVerification,
    WoodchuckProfile,
    WoodchuckState,
)
from app.security import hash_pin
from app.xp import level_payload, plunge_xp, xp_payload, xp_sources


@pytest.fixture()
def xp_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(xp_routes, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_profile(session, *, woodchuck_id: str = "WC-XP-TEST") -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id=woodchuck_id,
        display_name="XP Tester",
        pin_hash=hash_pin("2468"),
        instrument="Flute",
        level="Beginner",
        goal="Practice",
    )
    session.add(profile)
    session.flush()
    return profile


def add_chart(
    session,
    profile_id: int,
    *,
    minutes: int,
    key: str,
    source: str = "p-book",
) -> PracticeChart:
    chart = PracticeChart(
        profile_id=profile_id,
        practice_date=date(2025, 1, 6),
        minutes=minutes,
        instrument="Flute",
        practice_details=[],
        source=source,
        submission_key=key,
        include_contests=True,
        include_team_contests=True,
        credits_awarded=0,
    )
    session.add(chart)
    session.flush()
    return chart


def add_plunge_event(
    session,
    profile_id: int,
    *,
    key: str,
    points: int,
    occurred_at: datetime,
    event_type: str = "dandelion",
) -> None:
    session.add(PlungePointAward(
        profile_id=profile_id,
        event_key=key,
        event_type=event_type,
        points_scored=points,
        occurred_at=occurred_at,
    ))


def test_historical_minutes_and_valid_submitted_p_charts_use_canonical_rows(
    xp_database,
) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        add_chart(session, profile.id, minutes=90, key="old-positive")
        add_chart(session, profile.id, minutes=35, key="older-positive")
        add_chart(session, profile.id, minutes=0, key="zero-minute")
        add_chart(session, profile.id, minutes=-5, key="negative-minute")
        add_chart(
            session,
            profile.id,
            minutes=25,
            key="imported-non-p-book",
            source="quest",
        )
        session.commit()

        sources = xp_sources(session, profile_id=profile.id)

    assert sources["practice_minutes"] == 150
    assert sources["p_charts"] == 2


def test_rejected_verification_does_not_remove_p_chart_xp(xp_database) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        chart = add_chart(session, profile.id, minutes=20, key="rejected-chart")
        verification = PracticeChartVerification(
            practice_chart_id=chart.id,
            verifier_id=None,
            status="pending",
        )
        session.add(verification)
        session.commit()
        before = xp_payload(session, profile_id=profile.id)

        verification.status = "rejected"
        verification.responded_at = datetime(2025, 1, 7, tzinfo=timezone.utc)
        session.commit()
        after = xp_payload(session, profile_id=profile.id)

    assert before == after
    assert after["sources"]["p_charts"] == 1
    assert after["sources"]["practice_minutes"] == 20


def test_board_points_are_lifetime_ledger_points(xp_database) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        session.add_all([
            CampPointAward(
                profile_id=profile.id,
                activity_type="bonus-challenge",
                points_awarded=2,
                occurred_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                duplicate_key="bonus:2024-01-01",
            ),
            CampPointAward(
                profile_id=profile.id,
                activity_type="placement",
                points_awarded=7,
                occurred_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
                duplicate_key="placement:2026-08-03",
            ),
        ])
        session.commit()

        sources = xp_sources(session, profile_id=profile.id)

    assert sources["board_points"] == 9


def test_plunge_points_are_capped_at_ten_per_central_day(xp_database) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        add_plunge_event(
            session, profile.id, key="day-one-a", points=8,
            occurred_at=datetime(2026, 8, 13, 15, tzinfo=timezone.utc),
        )
        add_plunge_event(
            session, profile.id, key="day-one-b", points=9,
            occurred_at=datetime(2026, 8, 13, 20, tzinfo=timezone.utc),
        )
        add_plunge_event(
            session, profile.id, key="day-two", points=4,
            occurred_at=datetime(2026, 8, 14, 15, tzinfo=timezone.utc),
        )
        session.commit()

        result = plunge_xp(session, profile_id=profile.id)

    assert result == 14


def test_plunge_utc_events_on_either_side_of_central_midnight_use_separate_days(
    xp_database,
) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        # 04:59 UTC is 11:59 PM CDT; SQLite returns this timestamp timezone-naive.
        add_plunge_event(
            session, profile.id, key="before-midnight", points=20,
            occurred_at=datetime(2026, 8, 14, 4, 59),
        )
        # 05:01 UTC is 12:01 AM CDT on the next America/Chicago calendar day.
        add_plunge_event(
            session, profile.id, key="after-midnight", points=20,
            occurred_at=datetime(2026, 8, 14, 5, 1, tzinfo=timezone.utc),
        )
        session.commit()

        result = plunge_xp(session, profile_id=profile.id)

    assert result == 20


def test_client_account_state_cannot_change_xp(xp_database) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        session.add(WoodchuckState(
            profile_id=profile.id,
            state_json={
                "progress": {"xp_total": 999999, "level": 10},
                "practiceLog": [{"minutes": 50000}],
            },
            revision=1,
        ))
        session.commit()

        payload = xp_payload(session, profile_id=profile.id)

    assert payload["xp_total"] == 0
    assert payload["level"] == 1
    assert payload["sources"] == {
        "practice_minutes": 0,
        "board_points": 0,
        "p_charts": 0,
        "plunge_points": 0,
    }


@pytest.mark.parametrize(
    ("xp_total", "level", "current", "next_level"),
    [
        (0, 1, 0, 250),
        (249, 1, 0, 250),
        (250, 2, 250, 750),
        (749, 2, 250, 750),
        (750, 3, 750, 1500),
        (24999, 9, 18000, 25000),
        (25000, 10, 25000, None),
    ],
)
def test_level_threshold_boundaries(
    xp_total: int, level: int, current: int, next_level: int | None
) -> None:
    payload = level_payload(xp_total)
    assert payload["level"] == level
    assert payload["current_level_xp"] == current
    assert payload["next_level_xp"] == next_level


def test_level_ten_keeps_accumulating_lifetime_xp() -> None:
    payload = level_payload(100_000)
    assert payload == {
        "level": 10,
        "current_level_xp": 25000,
        "next_level_xp": None,
        "progress_percent": 100.0,
    }


def test_progress_percentage_is_within_current_level() -> None:
    assert level_payload(500)["progress_percent"] == 50.0


def test_xp_endpoint_requires_authentication_and_returns_calculated_payload(
    xp_database,
) -> None:
    client = TestClient(app)
    assert client.get("/xp").status_code == 401

    with xp_database() as session:
        profile = add_profile(session)
        add_chart(session, profile.id, minutes=24, key="endpoint-chart")
        session.commit()

    login = client.post(
        "/account/login",
        data={"woodchuck_id": "WC-XP-TEST", "pin": "2468"},
    )
    assert login.status_code == 200

    response = client.get("/xp")
    assert response.status_code == 200
    assert response.json() == {
        "level": 1,
        "current_level_xp": 0,
        "next_level_xp": 250,
        "progress_percent": 10.0,
        "xp_total": 25,
        "sources": {
            "practice_minutes": 24,
            "board_points": 0,
            "p_charts": 1,
            "plunge_points": 0,
        },
    }


def sign_in_xp_student(client: TestClient) -> None:
    response = client.post(
        "/account/login",
        data={"woodchuck_id": "WC-XP-TEST", "pin": "2468"},
    )
    assert response.status_code == 200


def test_plunge_write_endpoint_requires_authentication(xp_database) -> None:
    response = TestClient(app).post("/xp/plunge-points", json={
        "event_key": "anonymous-event",
        "event_type": "dandelion",
        "points_scored": 1,
    })
    assert response.status_code == 401


def test_authenticated_plunge_event_persists_with_server_owned_utc_timestamp(
    xp_database,
) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        profile_id = profile.id
        session.commit()
    client = TestClient(app)
    sign_in_xp_student(client)
    before = datetime.now(timezone.utc)

    response = client.post("/xp/plunge-points", json={
        "event_key": "server-time-event",
        "event_type": "carrot",
        "points_scored": 3,
    })
    after = datetime.now(timezone.utc)

    assert response.status_code == 200
    assert response.json() == {
        "created": True,
        "event_key": "server-time-event",
        "event_type": "carrot",
        "points_scored": 3,
    }
    with xp_database() as session:
        award = session.scalar(select(PlungePointAward).where(
            PlungePointAward.profile_id == profile_id
        ))
        assert award is not None
        occurred_at = award.occurred_at
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        assert before <= occurred_at <= after


def test_plunge_duplicate_retry_is_idempotent(xp_database) -> None:
    with xp_database() as session:
        add_profile(session)
        session.commit()
    client = TestClient(app)
    sign_in_xp_student(client)
    payload = {
        "event_key": "retry-event",
        "event_type": "instrument",
        "points_scored": 5,
    }

    first = client.post("/xp/plunge-points", json=payload)
    second = client.post("/xp/plunge-points", json=payload)

    assert first.status_code == 200 and first.json()["created"] is True
    assert second.status_code == 200 and second.json()["created"] is False
    with xp_database() as session:
        assert session.scalar(select(func.count()).select_from(PlungePointAward)) == 1


def test_plunge_duplicate_key_with_conflicting_payload_is_rejected(xp_database) -> None:
    with xp_database() as session:
        add_profile(session)
        session.commit()
    client = TestClient(app)
    sign_in_xp_student(client)
    first = client.post("/xp/plunge-points", json={
        "event_key": "conflict-event",
        "event_type": "dandelion",
        "points_scored": 1,
    })
    conflict = client.post("/xp/plunge-points", json={
        "event_key": "conflict-event",
        "event_type": "carrot",
        "points_scored": 3,
    })

    assert first.status_code == 200
    assert conflict.status_code == 409
    with xp_database() as session:
        award = session.scalar(select(PlungePointAward))
        assert award.event_type == "dandelion"
        assert award.points_scored == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"event_key": "bad-type", "event_type": "portal", "points_scored": 1},
        {"event_key": "bad-dandelion", "event_type": "dandelion", "points_scored": 2},
        {"event_key": "bad-carrot", "event_type": "carrot", "points_scored": 4},
        {"event_key": "bad-instrument", "event_type": "instrument", "points_scored": 20},
        {"event_key": "bad-band", "event_type": "band_complete", "points_scored": 5},
    ],
)
def test_plunge_rejects_invalid_event_types_and_point_amounts(
    xp_database, payload: dict[str, object]
) -> None:
    with xp_database() as session:
        add_profile(session)
        session.commit()
    client = TestClient(app)
    sign_in_xp_student(client)

    response = client.post("/xp/plunge-points", json=payload)

    assert response.status_code == 400
    with xp_database() as session:
        assert session.scalar(select(func.count()).select_from(PlungePointAward)) == 0


def test_plunge_request_cannot_supply_timestamp_or_activity_date(xp_database) -> None:
    with xp_database() as session:
        add_profile(session)
        session.commit()
    client = TestClient(app)
    sign_in_xp_student(client)
    payload = {
        "event_key": "client-time-event",
        "event_type": "dandelion",
        "points_scored": 1,
        "occurred_at": "2000-01-01T00:00:00Z",
        "activity_date": "2000-01-01",
    }

    response = client.post("/xp/plunge-points", json=payload)

    assert response.status_code == 422
    with xp_database() as session:
        assert session.scalar(select(func.count()).select_from(PlungePointAward)) == 0


def test_raw_plunge_score_exceeds_ten_while_daily_xp_remains_capped(
    xp_database,
) -> None:
    with xp_database() as session:
        profile = add_profile(session)
        timestamp = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
        add_plunge_event(
            session, profile.id, key="uncapped-instrument", points=5,
            event_type="instrument", occurred_at=timestamp,
        )
        add_plunge_event(
            session, profile.id, key="uncapped-band", points=20,
            event_type="band_complete", occurred_at=timestamp,
        )
        session.commit()

        raw_score = session.scalar(select(func.sum(PlungePointAward.points_scored)))
        capped_xp = plunge_xp(session, profile_id=profile.id)

    assert raw_score == 25
    assert capped_xp == 10
