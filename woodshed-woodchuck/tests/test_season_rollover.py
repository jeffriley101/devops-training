from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import contest_seasons
from app import contests as contest_routes
from app.account_routes import SESSION_PROFILE_ID
from app.contest_seasons import (
    SeasonRolloverError,
    rollover_season,
    season_status_payload,
)
from app.contests import (
    aware_utc,
    create_camp_point_award,
    current_contests_payload,
    ensure_band_camp_data,
    finalized_weeks_payload,
    hall_of_champions_payload,
)
from app.db import Base
from app.models import (
    CampPointAward,
    Contest,
    ContestResult,
    ContestWeek,
    CrownProgress,
    PracticeChart,
    PracticeChartVerification,
    RewardGrant,
    Season,
    StudentVerifierConnection,
    TrustedVerifier,
    WoodchuckProfile,
    WoodchuckState,
)


ROLLOVER_NOW = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)


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


def ready_source(session: Session) -> tuple[Season, ContestWeek, list[Contest]]:
    season, contests, week = ensure_band_camp_data(
        session, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
    )
    season.ends_on = date(2026, 8, 2)
    week.status = "finalized"
    week.finalized_at = ROLLOVER_NOW - timedelta(hours=1)
    session.commit()
    return season, week, contests


def perform_rollover(session: Session):
    return rollover_season(
        session,
        source_key="band-camp-2026",
        next_key="band-camp-2027",
        next_name="Band Camp 2027",
        next_starts_on=date(2026, 8, 3),
        next_ends_on=date(2026, 8, 16),
        now=ROLLOVER_NOW,
    )


def add_student(session: Session) -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id="WC-ROLLOVER",
        display_name="Rollover Woodchuck",
        pin_hash="preserved-hash",
        instrument="Tuba",
        level="Advanced",
        goal="Audition",
    )
    session.add(profile)
    session.flush()
    return profile


def test_cannot_close_before_season_end(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    ready_source(session)
    with pytest.raises(SeasonRolloverError, match="season_end_not_passed"):
        rollover_season(
            session,
            source_key="band-camp-2026",
            next_key="band-camp-2027",
            next_name="Band Camp 2027",
            next_starts_on=date(2026, 8, 3),
            next_ends_on=date(2026, 8, 9),
            now=datetime(2026, 8, 2, 18, tzinfo=timezone.utc),
        )


def test_cannot_close_with_unfinalized_or_due_open_week(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    season, week, _ = ready_source(session)
    week.status = "open"
    week.finalized_at = None
    week.verification_deadline_at = ROLLOVER_NOW - timedelta(hours=1)
    week.finalize_after = ROLLOVER_NOW - timedelta(minutes=30)
    session.commit()

    blockers = season_status_payload(session, now=ROLLOVER_NOW)["blocking_reasons"]
    assert blockers == ["unfinalized_contest_weeks", "due_weeks_remain_open"]
    with pytest.raises(SeasonRolloverError, match="due_weeks_remain_open"):
        perform_rollover(session)
    assert season.status == "active"


def test_successful_rollover_generates_complete_central_weeks_and_is_idempotent(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    source, _, _ = ready_source(session)

    created = perform_rollover(session)
    session.commit()
    repeated = perform_rollover(session)
    session.commit()

    assert created.created is True and created.weeks_created == 2
    assert repeated.created is False and repeated.weeks_created == 2
    assert source.status == "closed"
    next_season = session.scalar(select(Season).where(Season.key == "band-camp-2027"))
    assert next_season is not None
    assert (next_season.status, next_season.timezone) == ("active", "America/Chicago")
    weeks = session.scalars(select(ContestWeek).where(
        ContestWeek.season_id == next_season.id
    ).order_by(ContestWeek.week_start)).all()
    assert [(week.week_start, week.week_end, week.status) for week in weeks] == [
        (date(2026, 8, 3), date(2026, 8, 10), "open"),
        (date(2026, 8, 10), date(2026, 8, 17), "open"),
    ]
    assert aware_utc(weeks[0].verification_deadline_at) == datetime(
        2026, 8, 10, 17, tzinfo=timezone.utc
    )
    assert aware_utc(weeks[0].finalize_after) == datetime(
        2026, 8, 10, 17, 5, tzinfo=timezone.utc
    )
    assert session.scalar(select(func.count()).select_from(Season)) == 2
    assert session.scalar(select(func.count()).select_from(ContestWeek)) == 3
    assert {contest.name for contest in session.scalars(select(Contest))} == {
        "Top Five Minutes Leaders",
        "Weekly Practice Minutes by Instrument",
        "Weekly Band Camp Points",
    }


def test_duplicate_key_and_conflicting_dates_are_rejected_safely(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    source, _, _ = ready_source(session)
    session.add(Season(
        key="band-camp-existing",
        name="Existing",
        timezone="America/Chicago",
        starts_on=date(2026, 8, 10),
        ends_on=date(2026, 8, 23),
        status="planned",
    ))
    session.commit()

    with pytest.raises(SeasonRolloverError, match="key already exists"):
        rollover_season(
            session, source_key=source.key, next_key="band-camp-existing",
            next_name="Duplicate", next_starts_on=date(2026, 8, 24),
            next_ends_on=date(2026, 8, 30), now=ROLLOVER_NOW,
        )
    with pytest.raises(SeasonRolloverError, match="dates conflict"):
        rollover_season(
            session, source_key=source.key, next_key="band-camp-new",
            next_name="Overlap", next_starts_on=date(2026, 8, 17),
            next_ends_on=date(2026, 8, 30), now=ROLLOVER_NOW,
        )
    assert source.status == "active"
    assert session.scalar(select(Season).where(Season.key == "band-camp-new")) is None


def test_rollover_transaction_rolls_back_close_and_partial_weeks(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    source, _, _ = ready_source(session)
    real_schedule = contest_seasons.contest_week_schedule
    calls = 0

    def fail_second_week(week_start: date):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced week generation failure")
        return real_schedule(week_start)

    monkeypatch.setattr(contest_seasons, "contest_week_schedule", fail_second_week)
    with pytest.raises(RuntimeError, match="forced"):
        with factory() as transaction_session:
            with transaction_session.begin():
                perform_rollover(transaction_session)

    session.expire_all()
    assert source.status == "active"
    assert session.scalar(select(Season).where(Season.key == "band-camp-2027")) is None
    assert session.scalar(select(func.count()).select_from(ContestWeek)) == 1


def test_rollover_preserves_history_state_rewards_crown_and_activity_data(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    source, old_week, contests = ready_source(session)
    student = add_student(session)
    verifier = TrustedVerifier(
        email="preserved-verifier@example.com",
        display_name="Preserved Verifier",
        pin_hash="preserved-verifier-hash",
    )
    session.add(verifier)
    session.flush()
    connection = StudentVerifierConnection(
        profile_id=student.id,
        verifier_id=verifier.id,
        role="teacher",
        status="accepted",
        accepted_at=ROLLOVER_NOW - timedelta(days=10),
    )
    state_payload = {
        "bandCamp": {"totals": {"points": 41}},
        "progress": {"credits": 12, "level": 6, "streak": 9},
    }
    state = WoodchuckState(
        profile_id=student.id, state_json=deepcopy(state_payload), revision=8
    )
    chart = PracticeChart(
        profile_id=student.id, practice_date=date(2026, 8, 1), minutes=30,
        instrument="Tuba", practice_details=[], source="p-book", credits_awarded=4,
    )
    session.add_all([state, chart, connection])
    session.flush()
    verification = PracticeChartVerification(
        practice_chart_id=chart.id, verifier_id=None, status="approved"
    )
    points = next(item for item in contests if item.key == "weekly-points-leaders")
    result = ContestResult(
        contest_week_id=old_week.id, contest_id=points.id, division="open",
        subject_type="student", subject_key=str(student.id), profile_id=student.id,
        display_name_snapshot="Rollover Woodchuck", score=30, rank=1, medal="gold",
    )
    session.add_all([verification, result])
    session.flush()
    reward = RewardGrant(
        profile_id=student.id, contest_result_id=result.id,
        source_key="preserved-reward", reward_type="dandelion", amount=1,
    )
    crown = CrownProgress(
        profile_id=student.id, category_key="weekly-points-leaders",
        qualifying_wins=12, crown_earned_at=ROLLOVER_NOW - timedelta(days=30),
    )
    activity_crown = CrownProgress(
        profile_id=student.id, category_key="instrument-care",
        qualifying_wins=14, crown_earned_at=ROLLOVER_NOW - timedelta(days=20),
    )
    award = CampPointAward(
        profile_id=student.id, activity_type="care", points_awarded=1,
        occurred_at=datetime(2026, 8, 1, 18, tzinfo=timezone.utc),
        duplicate_key="preserved-camp-award",
    )
    session.add_all([reward, crown, activity_crown, award])
    session.commit()
    preserved_ids = {
        "chart": chart.id, "verification": verification.id, "result": result.id,
        "reward": reward.id, "crown": crown.id,
        "activity_crown": activity_crown.id, "award": award.id,
        "verifier": verifier.id, "connection": connection.id,
    }

    perform_rollover(session)
    session.commit()

    assert session.get(WoodchuckState, student.id).state_json == state_payload
    assert session.get(PracticeChart, preserved_ids["chart"]).minutes == 30
    assert session.get(PracticeChartVerification, preserved_ids["verification"]).status == "approved"
    assert session.get(ContestResult, preserved_ids["result"]).score == 30
    assert session.get(RewardGrant, preserved_ids["reward"]).source_key == "preserved-reward"
    saved_crown = session.get(CrownProgress, preserved_ids["crown"])
    assert saved_crown.qualifying_wins == 12 and saved_crown.crown_earned_at is not None
    saved_activity_crown = session.get(CrownProgress, preserved_ids["activity_crown"])
    assert saved_activity_crown.qualifying_wins == 14
    assert saved_activity_crown.crown_earned_at is not None
    assert session.get(CampPointAward, preserved_ids["award"]).points_awarded == 1
    assert session.get(TrustedVerifier, preserved_ids["verifier"]).email == "preserved-verifier@example.com"
    assert session.get(StudentVerifierConnection, preserved_ids["connection"]).status == "accepted"
    assert finalized_weeks_payload(session)["weeks"][0]["season"]["key"] == source.key
    assert hall_of_champions_payload(session)["students"][0]["medals"]["gold"] == 1


def test_new_season_standings_start_empty_and_new_awards_use_current_week(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    ready_source(session)
    perform_rollover(session)
    student = add_student(session)
    session.commit()

    empty = current_contests_payload(
        session,
        now=datetime(2026, 8, 4, 18, tzinfo=timezone.utc),
        current_profile_id=student.id,
    )
    assert empty["season"]["key"] == "band-camp-2027"
    assert all(not standings.get("open") for standings in empty["standings"].values())
    assert "verified" not in empty["standings"]["weekly-camp-points"]
    assert empty["standings"]["weekly-points-leaders"]["verified"] == []
    assert empty["standings"]["weekly-practice-by-instrument"]["verified"] == []

    create_camp_point_award(
        session, profile=student, activity_type="care",
        activity_date=date(2026, 8, 4),
        now=datetime(2026, 8, 4, 18, tzinfo=timezone.utc),
    )
    session.commit()
    updated = current_contests_payload(
        session,
        now=datetime(2026, 8, 4, 18, tzinfo=timezone.utc),
        current_profile_id=student.id,
    )
    assert updated["standings"]["weekly-camp-points"]["open"][0]["total_points"] == 1


def test_status_payload_is_privacy_safe(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    ready_source(session)
    payload = season_status_payload(session, now=ROLLOVER_NOW)

    assert payload["rollover_allowed"] is True
    assert payload["blocking_reasons"] == []
    assert payload["active_season"]["total_weeks"] == 1
    serialized = repr(payload).casefold()
    for private in (
        "profile_id", "account_id", "woodchuck_id", "pin", "email",
        "verifier", "private_note", "database_url",
    ):
        assert private not in serialized


def test_status_endpoint_requires_authentication_and_returns_safe_data(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    ready_source(session)
    student = add_student(session)
    session.commit()
    monkeypatch.setattr(contest_routes, "SessionLocal", factory)

    def request(profile_id: int | None = None) -> Request:
        scope: dict[str, object] = {"type": "http", "method": "GET", "path": "/"}
        scope["session"] = (
            {SESSION_PROFILE_ID: profile_id} if profile_id is not None else {}
        )
        return Request(scope)

    with pytest.raises(HTTPException) as unauthorized:
        contest_routes.contest_season_status(request())
    assert getattr(unauthorized.value, "status_code", None) == 401
    payload = contest_routes.contest_season_status(request(student.id))
    assert payload["active_season"]["key"] == "band-camp-2026"
    assert "profile_id" not in repr(payload).casefold()
