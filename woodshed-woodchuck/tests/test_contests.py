from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app import contests as contest_module
from app import practice_chart_routes as practice_chart_routes_module
from app.account_routes import SESSION_PROFILE_ID
from app.contests import (
    central_week_boundaries,
    current_contests,
    current_contests_payload,
    ensure_band_camp_data,
    finalize_contest_week,
    contest_results_payload,
    finalized_weeks_payload,
    hall_of_champions_payload,
    crown_progress_payload,
    weekly_practice_by_instrument,
    weekly_student_points,
    weekly_camp_points,
    student_camp_point_totals,
    create_camp_point_award,
    CampPointAwardCreate,
)
from app.db import Base
from app.main import app
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
    Team,
    WoodchuckProfile,
    WoodchuckState,
)
from app.practice_chart_routes import PracticeChartCreate


NOW = datetime(2026, 7, 28, 15, tzinfo=timezone.utc)
FINAL_NOW = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)


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
    include_contests: bool = True,
    created_at: datetime | None = None,
) -> PracticeChart:
    chart = PracticeChart(
        profile_id=profile.id,
        practice_date=practice_date,
        minutes=minutes,
        instrument=instrument or profile.instrument,
        practice_details=[],
        source="p-book",
        credits_awarded=0,
        include_contests=include_contests,
        created_at=created_at,
    )
    session.add(chart)
    session.flush()
    if verification_status is not None:
        session.add(
            PracticeChartVerification(
                practice_chart_id=chart.id,
                verifier_id=None,
                status=verification_status,
                responded_at=(
                    datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
                    if verification_status in {"approved", "rejected"} else None
                ),
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
    assert session.scalar(select(func.count()).select_from(Contest)) == 7
    assert session.scalar(select(func.count()).select_from(ContestWeek)) == 1
    assert {contest.key for contest in second[1]} == {
        "weekly-points-leaders",
        "weekly-practice-by-instrument",
        "weekly-camp-points",
        "team-weekly-practice",
        "team-seasonal-points",
        "team-average-practice",
        "team-season-practice",
    }
    minutes = next(
        contest for contest in second[1] if contest.key == "weekly-points-leaders"
    )
    assert minutes.name == "Top Five Minutes Leaders"
    assert minutes.metric_type == "practice_minutes"


def test_camp_point_activities_persist_once_and_aggregate_separately(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, week = ensure_band_camp_data(session, now=NOW)
    student = add_student(session, woodchuck_id="WC-CAMP-A", instrument="Tuba")
    state_payload = {"bandCamp": {"totals": {"points": 27}}, "progress": {"credits": 9}}
    session.add(WoodchuckState(
        profile_id=student.id, state_json=deepcopy(state_payload), revision=3
    ))
    for activity in ("hours", "care", "trivia", "marching"):
        award, created = create_camp_point_award(
            session,
            profile=student,
            activity_type=activity,
            activity_date=date(2026, 7, 28),
            now=NOW,
        )
        assert created is True
        assert award.points_awarded == 1
    duplicate, created = create_camp_point_award(
        session,
        profile=student,
        activity_type="trivia",
        activity_date=date(2026, 7, 28),
        now=NOW,
    )
    session.commit()

    assert created is False
    assert duplicate.activity_type == "trivia"
    assert session.scalar(select(func.count()).select_from(CampPointAward)) == 4
    standings = weekly_camp_points(
        session, contest_week=week, current_profile_id=student.id
    )
    assert standings["open"] == [{
        "rank": 1,
        "display_name": "Student WC-CAMP-A",
        "total_points": 4,
        "is_current_user": True,
    }]
    assert standings["current_user_position"]["open"]["total_points"] == 4
    assert "verified" not in standings
    assert session.get(WoodchuckState, student.id).state_json == state_payload


def test_camp_points_do_not_include_p_charts_or_practice_minutes(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, week = ensure_band_camp_data(session, now=NOW)
    student = add_student(session, woodchuck_id="WC-CAMP-B", instrument="Flute")
    add_chart(
        session, profile=student, practice_date=date(2026, 7, 28), minutes=45
    )
    create_camp_point_award(
        session, profile=student, activity_type="care",
        activity_date=date(2026, 7, 28), now=NOW,
    )
    session.commit()

    payload = current_contests_payload(
        session, now=NOW, current_profile_id=student.id
    )["standings"]
    assert payload["weekly-points-leaders"]["open"][0]["total_minutes"] == 45
    assert payload["weekly-practice-by-instrument"]["open"][0]["total_minutes"] == 45
    assert payload["weekly-camp-points"]["open"][0]["total_points"] == 1


def test_student_camp_point_totals_use_persisted_central_week_and_exclude_future(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    season, _, week = ensure_band_camp_data(session, now=NOW)
    student = add_student(session, woodchuck_id="WC-CAMP-TOTALS", instrument="Flute")
    # NOW is Tuesday in America/Chicago. Preseason and future awards do not count.
    for key, occurred_at, points in (
        ("prior", datetime(2026, 7, 26, 20, tzinfo=timezone.utc), 5),
        ("monday", datetime(2026, 7, 27, 6, tzinfo=timezone.utc), 1),
        ("today", NOW - timedelta(minutes=1), 2),
        ("future", NOW + timedelta(minutes=1), 50),
    ):
        session.add(CampPointAward(
            profile_id=student.id, activity_type="hours", points_awarded=points,
            occurred_at=occurred_at, duplicate_key=f"totals-{key}",
        ))
    session.commit()

    assert student_camp_point_totals(
        session, profile_id=student.id, now=NOW,
        season=season, contest_week=week,
    ) == {"camp_points_this_week": 3, "camp_points_season": 3}


def test_board_week_and_season_camp_points_share_authoritative_ledger(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    season, contests, prior_week = ensure_band_camp_data(session, now=NOW)
    current_now = datetime(2026, 8, 5, 18, tzinfo=timezone.utc)
    _, _, current_week = ensure_band_camp_data(session, now=current_now)
    student = add_student(session, woodchuck_id="WC-17-PLUS-15", instrument="Flute")
    unrelated = add_student(session, woodchuck_id="WC-NOT-IN-TOTAL", instrument="Oboe")
    team = Team(
        season_id=season.id,
        display_name="Ledger Team",
        normalized_name="ledger team",
        emblem_key="letter:L",
        creator_profile_id=student.id,
    )
    session.add(team)
    session.flush()

    award_specs = [
        ("prior-activities", 12, datetime(2026, 7, 29, 18, tzinfo=timezone.utc), None),
        (
            f"contest:{prior_week.id}:weekly-points-leaders:open:camp-points",
            3,
            datetime(2026, 8, 3, 18, tzinfo=timezone.utc),
            team.id,
        ),
        ("band-camp:2026-08-03:activities", 8, datetime(2026, 8, 3, 19, tzinfo=timezone.utc), team.id),
        ("bonus-challenge:2026-08-03:first", 2, datetime(2026, 8, 3, 20, tzinfo=timezone.utc), None),
        ("bonus-challenge:2026-08-04:second", 2, datetime(2026, 8, 4, 20, tzinfo=timezone.utc), team.id),
        ("bonus-challenge:2026-08-05:third", 2, datetime(2026, 8, 5, 16, tzinfo=timezone.utc), None),
        (
            f"contest:{current_week.id}:weekly-points-leaders:open:camp-points",
            3,
            datetime(2026, 8, 5, 17, tzinfo=timezone.utc),
            team.id,
        ),
    ]
    for duplicate_key, points, occurred_at, team_id in award_specs:
        session.add(CampPointAward(
            profile_id=student.id,
            activity_type=(
                "contest-placement" if duplicate_key.startswith("contest:")
                else "bonus-challenge" if duplicate_key.startswith("bonus-challenge:")
                else "care"
            ),
            points_awarded=points,
            occurred_at=occurred_at,
            duplicate_key=duplicate_key,
            team_id=team_id,
        ))
    session.add(CampPointAward(
        profile_id=unrelated.id,
        activity_type="care",
        points_awarded=99,
        occurred_at=datetime(2026, 8, 5, 17, tzinfo=timezone.utc),
        duplicate_key="unrelated-current-week",
        team_id=team.id,
    ))
    points_contest = next(
        contest for contest in contests if contest.key == "weekly-points-leaders"
    )
    for division in ("open", "verified"):
        session.add(ContestResult(
            contest_week_id=current_week.id,
            contest_id=points_contest.id,
            division=division,
            subject_type="student",
            subject_key=str(student.id),
            profile_id=student.id,
            display_name_snapshot=student.display_name,
            score=42,
            rank=1,
            medal="gold",
        ))
    prior_week.status = "finalized"
    prior_week.finalized_at = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)
    session.commit()

    before_rows = [tuple(row) for row in session.execute(select(
        CampPointAward.id,
        CampPointAward.points_awarded,
        CampPointAward.occurred_at,
        CampPointAward.duplicate_key,
        CampPointAward.team_id,
    ).order_by(CampPointAward.id)).all()]
    totals = student_camp_point_totals(
        session, profile_id=student.id, now=current_now,
        season=season, contest_week=current_week,
    )
    assert totals == {"camp_points_this_week": 17, "camp_points_season": 32}
    assert totals["camp_points_season"] >= totals["camp_points_this_week"]

    payload = current_contests_payload(
        session, now=current_now, current_profile_id=student.id
    )
    assert payload["camp_points_this_week"] == 17
    assert payload["camp_points_season"] == 32
    student_position = payload["standings"]["weekly-camp-points"][
        "current_user_position"
    ]["open"]
    assert student_position["total_points"] == 17
    assert session.scalar(select(func.count()).select_from(CampPointAward).where(
        CampPointAward.profile_id == student.id,
        CampPointAward.duplicate_key.like(
            f"contest:{current_week.id}:%:camp-points"
        ),
    )) == 1
    assert current_contests_payload(
        session, now=current_now, current_profile_id=student.id
    )["camp_points_season"] == 32

    finalize_contest_week(
        session, week_start=prior_week.week_start, now=current_now
    )
    session.commit()
    assert student_camp_point_totals(
        session, profile_id=student.id, now=current_now,
        season=season, contest_week=current_week,
    ) == totals
    after_rows = [tuple(row) for row in session.execute(select(
        CampPointAward.id,
        CampPointAward.points_awarded,
        CampPointAward.occurred_at,
        CampPointAward.duplicate_key,
        CampPointAward.team_id,
    ).order_by(CampPointAward.id)).all()]
    assert after_rows == before_rows


def test_camp_point_award_endpoint_is_authenticated_idempotent_and_private(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    student = add_student(session, woodchuck_id="WC-CAMP-PRIVATE", instrument="Oboe")
    session.commit()
    monkeypatch.setattr(contest_module, "SessionLocal", factory)
    submitted = CampPointAwardCreate(
        activity_type="care",
        activity_date=datetime.now(contest_module.CENTRAL).date(),
    )

    with pytest.raises(HTTPException) as unauthorized:
        contest_module.award_camp_points(request_with_session(), submitted)
    assert unauthorized.value.status_code == 401
    first = contest_module.award_camp_points(request_with_session(student.id), submitted)
    repeated = contest_module.award_camp_points(request_with_session(student.id), submitted)
    persisted = contest_module.daily_camp_point_awards(
        submitted.activity_date, request_with_session(student.id)
    )

    assert first["created"] is True
    assert repeated["created"] is False
    assert first["award"]["points_awarded"] == 1
    assert persisted["activity_date"] == submitted.activity_date.isoformat()
    assert persisted["awards"] == [first["award"]]
    assert session.scalar(select(func.count()).select_from(CampPointAward)) == 1
    serialized = repr(first).casefold()
    for private in ("profile_id", "woodchuck_id", "wc-camp-private", "pin", "email", "verifier"):
        assert private not in serialized

    with pytest.raises(HTTPException) as unauthorized_read:
        contest_module.daily_camp_point_awards(
            submitted.activity_date, request_with_session()
        )
    assert unauthorized_read.value.status_code == 401


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
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return NOW if tz is not None else NOW.replace(tzinfo=None)

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
    monkeypatch.setattr(contest_module, "datetime", FixedDateTime)

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
        "legal_name",
    ):
        assert private_field not in serialized

    direct_payload = current_contests_payload(
        session,
        now=NOW,
        current_profile_id=profile.id,
    )
    assert direct_payload["season"] == {
        "key": "band-camp-2026",
        "name": "Band Camp",
        "timezone": "America/Chicago",
        "status": "active",
        "starts_on": "2026-07-27",
        "ends_on": None,
    }


def test_student_points_rankings_and_current_user_position(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, contest_week = ensure_band_camp_data(session, now=NOW)
    students = [
        add_student(
            session,
            woodchuck_id=f"WC-POINT-{index}",
            instrument="Saxophone",
        )
        for index in range(1, 8)
    ]
    students[0].display_name = "Alpha Chuck"
    students[1].display_name = "Bravo Chuck"
    students[2].display_name = "Charlie Chuck"
    students[3].display_name = "Delta Chuck"
    students[4].display_name = "Echo Chuck"
    students[5].display_name = "Foxtrot Chuck"
    students[6].display_name = "Golf Chuck"

    for index, profile in enumerate(students):
        point_count = 7 - index
        for chart_index in range(point_count):
            add_chart(
                session,
                profile=profile,
                practice_date=date(2026, 7, 28),
                minutes=10,
                verification_status=(
                    "approved" if chart_index < max(point_count - 1, 0) else None
                ),
            )
    session.commit()

    standings = weekly_student_points(
        session,
        contest_week=contest_week,
        current_profile_id=students[5].id,
    )

    assert len(standings["open"]) == 6
    assert standings["open"][-1] == {
        "rank": 6,
        "display_name": "Foxtrot Chuck",
        "total_minutes": 20,
        "is_current_user": True,
    }
    assert standings["current_user_position"]["open"] == {
        "rank": 6,
        "total_minutes": 20,
        "minutes_behind_leader": 50,
        "tied": False,
        "in_top_five": False,
        "has_score": True,
    }
    assert standings["current_user_position"]["verified"]["total_minutes"] == 10
    assert all(
        set(row) == {
            "rank", "display_name", "total_minutes", "is_current_user"
        }
        for division in ("open", "verified")
        for row in standings[division]
    )


def test_student_points_use_olympic_ties_and_separate_divisions(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, contest_week = ensure_band_camp_data(session, now=NOW)
    alpha = add_student(session, woodchuck_id="WC-ALPHA", instrument="Flute")
    beta = add_student(session, woodchuck_id="WC-BETA", instrument="Flute")
    gamma = add_student(session, woodchuck_id="WC-GAMMA", instrument="Flute")
    alpha.display_name = "Alpha"
    beta.display_name = "Beta"
    gamma.display_name = "Gamma"
    for profile, statuses in (
        (alpha, ("approved", "approved")),
        (beta, ("approved", None)),
        (gamma, (None,)),
    ):
        for status in statuses:
            add_chart(
                session,
                profile=profile,
                practice_date=date(2026, 7, 29),
                minutes=15,
                verification_status=status,
            )
    session.commit()

    standings = weekly_student_points(
        session,
        contest_week=contest_week,
        current_profile_id=beta.id,
    )

    assert [(row["rank"], row["display_name"], row["total_minutes"]) for row in standings["open"]] == [
        (1, "Alpha", 30),
        (1, "Beta", 30),
        (3, "Gamma", 15),
    ]
    assert [(row["display_name"], row["total_minutes"]) for row in standings["verified"]] == [
        ("Alpha", 30),
        ("Beta", 15),
    ]
    assert standings["current_user_position"]["open"]["tied"] is True
    assert standings["current_user_position"]["open"]["minutes_behind_leader"] == 0
    assert standings["current_user_position"]["verified"]["minutes_behind_leader"] == 15


def test_student_points_missing_score_and_name_use_safe_public_values(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, contest_week = ensure_band_camp_data(session, now=NOW)
    leader = add_student(session, woodchuck_id="WC-LEADER", instrument="Tuba")
    current = add_student(session, woodchuck_id="WC-CURRENT", instrument="Tuba")
    leader.display_name = "   "
    add_chart(
        session,
        profile=leader,
        practice_date=date(2026, 7, 30),
        minutes=20,
    )
    session.commit()

    standings = weekly_student_points(
        session,
        contest_week=contest_week,
        current_profile_id=current.id,
    )

    assert standings["open"][0]["display_name"] == "Woodchuck"
    assert standings["current_user_position"]["open"] == {
        "rank": None,
        "total_minutes": 0,
        "minutes_behind_leader": 20,
        "tied": False,
        "in_top_five": False,
        "has_score": False,
    }


def test_points_api_exposes_only_public_leaderboard_fields(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    profile = add_student(
        session,
        woodchuck_id="WC-SECRET-ID",
        instrument="Clarinet",
    )
    profile.display_name = "Public Chuck"
    add_chart(
        session,
        profile=profile,
        practice_date=date(2026, 7, 30),
        minutes=25,
        verification_status="approved",
    )
    session.commit()

    payload = current_contests_payload(
        session,
        now=NOW,
        current_profile_id=profile.id,
    )
    points = payload["standings"]["weekly-points-leaders"]
    assert points["open"][0] == {
        "rank": 1,
        "display_name": "Public Chuck",
        "total_minutes": 25,
        "is_current_user": True,
    }
    serialized = repr(points).casefold()
    for private_value in (
        "wc-secret-id",
        "pin_hash",
        "email",
        "verifier",
        "profile_id",
        "woodchuck_id",
    ):
        assert private_value not in serialized


def ready_week(session: Session) -> ContestWeek:
    _, _, week = ensure_band_camp_data(session, now=NOW)
    week.verification_deadline_at = FINAL_NOW - timedelta(minutes=10)
    week.finalize_after = FINAL_NOW - timedelta(minutes=5)
    session.commit()
    return week


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("week_end", date(2026, 8, 4), "has not ended"),
        ("verification_deadline_at", FINAL_NOW, "deadline has not passed"),
        ("finalize_after", FINAL_NOW, "time has not passed"),
    ],
)
def test_finalization_timing_gates(
    database: tuple[Session, sessionmaker[Session]],
    field: str,
    value: object,
    message: str,
) -> None:
    session, _ = database
    week = ready_week(session)
    setattr(week, field, value)
    session.commit()

    with pytest.raises(HTTPException, match=message) as blocked:
        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)

    assert blocked.value.status_code == 409
    assert week.status == "open"


def test_finalization_requires_timezone_aware_now(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    week = ready_week(session)
    with pytest.raises(ValueError, match="timezone-aware"):
        finalize_contest_week(
            session, week_start=week.week_start, now=FINAL_NOW.replace(tzinfo=None)
        )


def test_successful_finalization_medals_rewards_crown_and_idempotence(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    week = ready_week(session)
    alpha = add_student(session, woodchuck_id="WC-FINAL-A", instrument="Flute")
    beta = add_student(session, woodchuck_id="WC-FINAL-B", instrument="Clarinet")
    gamma = add_student(session, woodchuck_id="WC-FINAL-C", instrument="Oboe")
    delta = add_student(session, woodchuck_id="WC-FINAL-D", instrument="Bassoon")
    for profile, count in ((alpha, 4), (beta, 3), (gamma, 2), (delta, 1)):
        for _ in range(count):
            add_chart(
                session, profile=profile, practice_date=date(2026, 7, 29),
                minutes=5, verification_status="approved" if profile != beta else None,
                created_at=NOW,
            )
    session.commit()

    finalized = finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    first_results = [(r.id, r.rank, r.medal, r.score) for r in session.scalars(
        select(ContestResult).order_by(ContestResult.id)
    )]
    first_grants = session.scalar(select(func.count()).select_from(RewardGrant))

    finalize_contest_week(
        session, week_start=week.week_start, now=FINAL_NOW + timedelta(hours=1)
    )
    session.commit()

    assert finalized.status == "finalized"
    assert finalized.finalized_at == FINAL_NOW
    assert first_results == [(r.id, r.rank, r.medal, r.score) for r in session.scalars(
        select(ContestResult).order_by(ContestResult.id)
    )]
    assert session.scalar(select(func.count()).select_from(RewardGrant)) == first_grants
    point_results = session.scalars(select(ContestResult).join(Contest).where(
        Contest.key == "weekly-points-leaders", ContestResult.division == "open"
    ).order_by(ContestResult.rank)).all()
    assert [(r.rank, r.medal) for r in point_results] == [
        (1, "gold"), (2, "silver"), (3, "bronze")
    ]
    assert [result.score for result in point_results] == [20, 15, 10]
    assert not any(
        grant.profile_id in {beta.id, gamma.id}
        and grant.source_key.endswith("gold")
        for grant in session.scalars(select(RewardGrant))
    )
    crown = session.scalar(select(CrownProgress).where(CrownProgress.profile_id == alpha.id))
    assert crown is not None and crown.qualifying_wins == 2


def test_open_and_verified_wins_reuse_pending_crown_progress(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    fixture_session, factory = database
    fixture_session.close()
    with factory(autoflush=False) as session:
        week = ready_week(session)
        student = add_student(
            session, woodchuck_id="WC-DUAL-DIVISION", instrument="Flute"
        )
        session.add(WoodchuckState(
            profile_id=student.id,
            state_json={"progress": {"credits": 7}},
            revision=0,
        ))
        chart = add_chart(
            session,
            profile=student,
            practice_date=date(2026, 7, 29),
            minutes=42,
            verification_status="approved",
        )
        chart.created_at = NOW
        session.commit()

        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()

        progress = session.scalars(select(CrownProgress).where(
            CrownProgress.profile_id == student.id,
            CrownProgress.category_key == "weekly-points-leaders",
        )).all()
        assert len(progress) == 1
        assert progress[0].qualifying_wins == 2

        first_counts = {
            "results": session.scalar(select(func.count()).select_from(ContestResult)),
            "grants": session.scalar(select(func.count()).select_from(RewardGrant)),
            "camp_awards": session.scalar(
                select(func.count()).select_from(CampPointAward)
            ),
            "crown_progress": session.scalar(
                select(func.count()).select_from(CrownProgress)
            ),
        }
        first_balance = session.get(WoodchuckState, student.id).state_json[
            "progress"
        ]["credits"]
        assert first_balance > 7

        finalize_contest_week(
            session, week_start=week.week_start, now=FINAL_NOW + timedelta(hours=1)
        )
        session.commit()

        assert first_counts == {
            "results": session.scalar(select(func.count()).select_from(ContestResult)),
            "grants": session.scalar(select(func.count()).select_from(RewardGrant)),
            "camp_awards": session.scalar(
                select(func.count()).select_from(CampPointAward)
            ),
            "crown_progress": session.scalar(
                select(func.count()).select_from(CrownProgress)
            ),
        }
        assert (
            session.get(WoodchuckState, student.id).state_json["progress"]["credits"]
            == first_balance
        )


@pytest.mark.parametrize("existing_balance", [None, 7], ids=["pending", "persisted"])
def test_multiple_finalization_rewards_reuse_one_woodchuck_state(
    database: tuple[Session, sessionmaker[Session]],
    existing_balance: int | None,
) -> None:
    fixture_session, factory = database
    fixture_session.close()
    with factory(autoflush=False) as session:
        week = ready_week(session)
        student = add_student(
            session, woodchuck_id="WC-MULTI-REWARD", instrument="Flute"
        )
        unrelated = add_student(
            session, woodchuck_id="WC-UNRELATED-STATE", instrument="Oboe"
        )
        session.add(WoodchuckState(
            profile_id=unrelated.id,
            state_json={"progress": {"credits": 91}},
            revision=3,
        ))
        if existing_balance is not None:
            session.add(WoodchuckState(
                profile_id=student.id,
                state_json={"progress": {"credits": existing_balance}},
                revision=2,
            ))
        add_chart(
            session,
            profile=student,
            practice_date=date(2026, 7, 29),
            minutes=42,
            verification_status="approved",
            created_at=NOW,
        )
        session.commit()

        finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
        session.commit()

        matching_states = session.scalars(select(WoodchuckState).where(
            WoodchuckState.profile_id == student.id
        )).all()
        assert len(matching_states) == 1
        reward_total = session.scalar(select(func.sum(RewardGrant.amount)).where(
            RewardGrant.profile_id == student.id,
            RewardGrant.reward_type.in_(("dandelion", "participation_dandelion")),
        ))
        assert reward_total == 205
        assert matching_states[0].state_json["progress"]["credits"] == (
            (existing_balance or 0) + reward_total
        )
        assert session.get(WoodchuckState, unrelated.id).state_json == {
            "progress": {"credits": 91}
        }

        first_counts = {
            "results": session.scalar(select(func.count()).select_from(ContestResult)),
            "grants": session.scalar(select(func.count()).select_from(RewardGrant)),
            "camp_awards": session.scalar(
                select(func.count()).select_from(CampPointAward)
            ),
            "crown_progress": session.scalar(
                select(func.count()).select_from(CrownProgress)
            ),
            "states": session.scalar(select(func.count()).select_from(WoodchuckState)),
        }
        assert first_counts == {
            "results": 4,
            "grants": 7,
            "camp_awards": 4,
            "crown_progress": 1,
            "states": 2,
        }
        first_balances = {
            row.profile_id: row.state_json["progress"]["credits"]
            for row in session.scalars(select(WoodchuckState)).all()
        }

        finalize_contest_week(
            session, week_start=week.week_start, now=FINAL_NOW + timedelta(hours=1)
        )
        session.commit()

        assert first_counts == {
            "results": session.scalar(select(func.count()).select_from(ContestResult)),
            "grants": session.scalar(select(func.count()).select_from(RewardGrant)),
            "camp_awards": session.scalar(
                select(func.count()).select_from(CampPointAward)
            ),
            "crown_progress": session.scalar(
                select(func.count()).select_from(CrownProgress)
            ),
            "states": session.scalar(select(func.count()).select_from(WoodchuckState)),
        }
        assert first_balances == {
            row.profile_id: row.state_json["progress"]["credits"]
            for row in session.scalars(select(WoodchuckState)).all()
        }


def test_existing_finalized_student_scores_are_not_rewritten(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, contests, week = ensure_band_camp_data(session, now=NOW)
    student = add_student(session, woodchuck_id="WC-HIST-MIN", instrument="Flute")
    contest = next(item for item in contests if item.key == "weekly-points-leaders")
    week.status = "finalized"
    week.finalized_at = FINAL_NOW
    historical = ContestResult(
        contest_week_id=week.id,
        contest_id=contest.id,
        division="open",
        subject_type="student",
        subject_key=str(student.id),
        profile_id=student.id,
        display_name_snapshot="Historical Woodchuck",
        score=3,
        rank=1,
        medal="gold",
    )
    session.add(historical)
    session.commit()

    current_contests_payload(session, now=NOW, current_profile_id=student.id)
    session.refresh(historical)

    assert historical.score == 3
    assert historical.rank == 1
    assert historical.medal == "gold"
    assert contest_results_payload(session, week)["results"][0]["contest"] == {
        "key": "weekly-points-leaders",
        "name": "Top Five Minutes Leaders",
    }


def test_camp_points_finalize_once_with_medals_reward_and_crown(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    week = ready_week(session)
    gold = add_student(session, woodchuck_id="WC-CAMP-G", instrument="Flute")
    silver = add_student(session, woodchuck_id="WC-CAMP-S", instrument="Oboe")
    bronze = add_student(session, woodchuck_id="WC-CAMP-BR", instrument="Tuba")
    for profile, activities in (
        (gold, ("hours", "care", "trivia")),
        (silver, ("hours", "care")),
        (bronze, ("hours",)),
    ):
        for activity in activities:
            award, _created = create_camp_point_award(
                session, profile=profile, activity_type=activity,
                activity_date=date(2026, 7, 28), now=NOW,
            )
            award.created_at = NOW
    session.commit()

    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    finalize_contest_week(
        session, week_start=week.week_start, now=FINAL_NOW + timedelta(hours=1)
    )
    session.commit()

    rows = session.scalars(select(ContestResult).join(Contest).where(
        Contest.key == "weekly-camp-points"
    ).order_by(ContestResult.rank)).all()
    assert [(row.division, row.rank, row.medal, row.score) for row in rows] == [
        ("open", 1, "gold", 3),
        ("open", 2, "silver", 2),
        ("open", 3, "bronze", 1),
    ]
    grants = session.scalars(select(RewardGrant).where(
        RewardGrant.source_key.like("%:weekly-camp-points:%")
    )).all()
    assert {(grant.profile_id, grant.reward_type, grant.amount) for grant in grants} == {
        (gold.id, "dandelion", 50), (gold.id, "crown_win", 1),
        (silver.id, "dandelion", 25), (bronze.id, "dandelion", 15),
    }
    camp_crown = session.scalar(select(CrownProgress).where(
        CrownProgress.profile_id == gold.id,
        CrownProgress.category_key == "weekly-camp-points",
    ))
    assert camp_crown is not None and camp_crown.qualifying_wins == 1
    results = contest_results_payload(session, week)["results"]
    assert any(row["contest"]["key"] == "weekly-camp-points" for row in results)
    hall = hall_of_champions_payload(session)
    champion = next(
        item for item in hall["students"] if item["display_name"] == gold.display_name
    )
    assert champion["medals"]["gold"] == 1


def test_olympic_ties_and_open_verified_gold_pay_once(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    week = ready_week(session)
    students = [
        add_student(session, woodchuck_id=f"WC-TIED-{i}", instrument="Flute")
        for i in range(3)
    ]
    for profile, count in zip(students, (2, 2, 1)):
        for _ in range(count):
            add_chart(session, profile=profile, practice_date=date(2026, 7, 30),
                      minutes=5, verification_status="approved", created_at=NOW)
    session.commit()
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()

    rows = session.scalars(select(ContestResult).join(Contest).where(
        Contest.key == "weekly-points-leaders", ContestResult.division == "open"
    ).order_by(ContestResult.id)).all()
    assert [(r.rank, r.medal) for r in rows] == [(1, "gold"), (1, "gold"), (3, "bronze")]
    for student in students[:2]:
        gold_grants = session.scalars(select(RewardGrant).where(
            RewardGrant.profile_id == student.id,
            RewardGrant.source_key.like("%:weekly-points-leaders:%"),
        )).all()
        assert {grant.reward_type for grant in gold_grants} == {"dandelion", "crown_win"}
        assert len(gold_grants) == 4
        crown = session.scalar(select(CrownProgress).where(CrownProgress.profile_id == student.id))
        assert crown is not None and crown.qualifying_wins == 2


def test_tenth_win_sets_crown_once_and_progress_never_resets(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    week = ready_week(session)
    student = add_student(session, woodchuck_id="WC-CROWN-10", instrument="Tuba")
    contest = session.scalar(select(Contest).where(Contest.key == "weekly-points-leaders"))
    assert contest is not None
    session.add(CrownProgress(
        profile_id=student.id, category_key=contest.crown_category or contest.key,
        qualifying_wins=9,
    ))
    add_chart(
        session, profile=student, practice_date=date(2026, 7, 31),
        minutes=5, created_at=NOW,
    )
    session.commit()
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    progress = session.scalar(select(CrownProgress).where(CrownProgress.profile_id == student.id))
    assert progress is not None
    earned_at = progress.crown_earned_at
    assert progress.qualifying_wins == 10
    assert contest_module.aware_utc(earned_at) == FINAL_NOW
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW + timedelta(days=1))
    session.commit()
    assert progress.qualifying_wins == 10 and progress.crown_earned_at == earned_at


def test_instrument_participation_threshold_and_division_deduplication(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    week = ready_week(session)
    eligible = add_student(session, woodchuck_id="WC-PART-15", instrument="Flute")
    short = add_student(session, woodchuck_id="WC-PART-14", instrument="Flute")
    rival = add_student(session, woodchuck_id="WC-PART-R", instrument="Oboe")
    add_chart(session, profile=eligible, practice_date=date(2026, 8, 1), minutes=15,
              verification_status="approved", created_at=NOW)
    add_chart(session, profile=short, practice_date=date(2026, 8, 1), minutes=14,
              verification_status="approved", created_at=NOW)
    add_chart(session, profile=rival, practice_date=date(2026, 8, 1), minutes=10,
              verification_status="approved", created_at=NOW)
    session.commit()
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()

    participation = session.scalars(select(RewardGrant).where(
        RewardGrant.source_key.like("%:weekly-practice-by-instrument:%")
    )).all()
    assert [(grant.profile_id, grant.amount) for grant in participation] == [
        (eligible.id, 50), (eligible.id, 50),
    ]
    assert all(grant.reward_type == "dandelion" and grant.category_key is None for grant in participation)


def test_failure_rolls_back_every_finalization_change(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = database
    week = ready_week(session)
    student = add_student(session, woodchuck_id="WC-ROLLBACK", instrument="Flute")
    session.add(WoodchuckState(profile_id=student.id, state_json={"progress": {"credits": 7}}, revision=4))
    add_chart(
        session, profile=student, practice_date=date(2026, 8, 2),
        minutes=20, created_at=NOW,
    )
    session.commit()
    before_state = deepcopy(session.get(WoodchuckState, student.id).state_json)
    session.commit()

    def fail_reward(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced reward failure")

    monkeypatch.setattr(contest_module, "_add_dandelion", fail_reward)
    with pytest.raises(RuntimeError, match="forced"):
        with session.begin():
            finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)

    assert session.scalar(select(func.count()).select_from(ContestResult)) == 0
    assert session.scalar(select(func.count()).select_from(RewardGrant)) == 0
    assert session.scalar(select(func.count()).select_from(CrownProgress)) == 0
    assert session.get(ContestWeek, week.id).status == "open"
    assert session.get(WoodchuckState, student.id).state_json == before_state


def test_results_are_immutable_private_and_preserve_historical_data(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    week = ready_week(session)
    student = add_student(session, woodchuck_id="WC-SECRET-HIST", instrument="Flute")
    student.display_name = "Original Public Name"
    chart = add_chart(session, profile=student, practice_date=date(2026, 8, 2), minutes=20,
                      verification_status="approved", created_at=NOW)
    session.commit()
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()
    before = contest_results_payload(session, week)
    counts = {model: session.scalar(select(func.count()).select_from(model)) for model in (
        WoodchuckProfile, PracticeChart, PracticeChartVerification, Season, ContestWeek
    )}

    student.display_name = "Changed Later"
    chart.minutes = 999
    verification = session.scalar(select(PracticeChartVerification).where(
        PracticeChartVerification.practice_chart_id == chart.id
    ))
    assert verification is not None
    verification.status = "rejected"
    session.commit()
    after = contest_results_payload(session, week)

    assert after == before
    serialized = repr(after).casefold()
    assert "original public name" in serialized
    for private in ("profile_id", "account_id", "woodchuck_id", "wc-secret-hist", "email", "pin", "verifier"):
        assert private not in serialized
    assert {model: session.scalar(select(func.count()).select_from(model)) for model in counts} == counts


def test_manual_route_missing_invalid_token_and_results_authentication(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    week = ready_week(session)
    profile = add_student(session, woodchuck_id="WC-AUTH", instrument="Flute")
    session.commit()
    monkeypatch.setattr(contest_module, "SessionLocal", factory)
    request = request_with_session(profile.id)
    request.scope["method"] = "POST"

    monkeypatch.delenv("CONTEST_ADMIN_TOKEN", raising=False)
    with pytest.raises(HTTPException) as missing:
        contest_module.finalize_week_route(week.week_start, request)
    assert missing.value.status_code == 503

    monkeypatch.setenv("CONTEST_ADMIN_TOKEN", "correct-token")
    with pytest.raises(HTTPException) as invalid:
        contest_module.finalize_week_route(week.week_start, request)
    assert invalid.value.status_code == 403

    authorized_request = request_with_session(profile.id)
    authorized_request.scope["method"] = "POST"
    authorized_request.scope["headers"] = [
        (b"x-contest-admin-token", b"correct-token")
    ]
    monkeypatch.setattr(
        contest_module,
        "finalize_contest_week",
        lambda active_session, *, week_start, now: active_session.get(
            ContestWeek, week.id
        ),
    )
    authorized_payload = contest_module.finalize_week_route(
        week.week_start, authorized_request
    )
    assert authorized_payload["week"]["week_start"] == week.week_start.isoformat()

    with pytest.raises(HTTPException) as unauthorized:
        contest_module.contest_week_results(week.week_start, request_with_session())
    assert unauthorized.value.status_code == 401
    week.status = "finalized"
    week.finalized_at = FINAL_NOW
    session.commit()
    payload = contest_module.contest_week_results(week.week_start, request_with_session(profile.id))
    assert set(payload) == {"week", "results"}


def test_finalized_week_listing_is_newest_first_public_and_finalized_only(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    season, _, newest = ensure_band_camp_data(session, now=NOW)
    older = ContestWeek(
        season_id=season.id,
        week_start=date(2026, 7, 20),
        week_end=date(2026, 7, 27),
        verification_deadline_at=datetime(2026, 7, 27, 17, tzinfo=timezone.utc),
        finalize_after=datetime(2026, 7, 27, 17, 5, tzinfo=timezone.utc),
        status="finalized",
        finalized_at=datetime(2026, 7, 27, 18, tzinfo=timezone.utc),
    )
    open_week = ContestWeek(
        season_id=season.id,
        week_start=date(2026, 8, 3),
        week_end=date(2026, 8, 10),
        verification_deadline_at=datetime(2026, 8, 10, 17, tzinfo=timezone.utc),
        finalize_after=datetime(2026, 8, 10, 17, 5, tzinfo=timezone.utc),
        status="open",
    )
    newest.status = "finalized"
    newest.finalized_at = FINAL_NOW
    session.add_all([older, open_week])
    session.commit()

    payload = finalized_weeks_payload(session)

    assert [week["week_start"] for week in payload["weeks"]] == [
        "2026-07-27",
        "2026-07-20",
    ]
    assert payload["weeks"][0] == {
        "season": {"key": "band-camp-2026", "name": "Band Camp"},
        "week_start": "2026-07-27",
        "week_end": "2026-08-03",
        "finalized_at": "2026-08-03T18:00:00+00:00",
    }
    serialized = repr(payload).casefold()
    for private_field in (
        "profile_id", "account_id", "woodchuck_id", "legal_name",
        "email", "pin", "verifier", "season_id", "contest_week_id",
    ):
        assert private_field not in serialized


def test_finalized_week_listing_requires_authentication_and_can_be_empty(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    profile = add_student(session, woodchuck_id="WC-WEEKS-AUTH", instrument="Flute")
    session.commit()
    monkeypatch.setattr(contest_module, "SessionLocal", factory)

    with pytest.raises(HTTPException) as unauthorized:
        contest_module.finalized_contest_weeks(request_with_session())
    assert unauthorized.value.status_code == 401
    assert contest_module.finalized_contest_weeks(
        request_with_session(profile.id)
    ) == {"weeks": []}


def test_finalized_week_listing_static_route_requires_authentication() -> None:
    response = TestClient(app).get("/contests/weeks/finalized")

    assert response.status_code == 401
    assert response.json() == {"detail": "Student sign-in is required."}


def test_prior_finalized_band_camp_week_results_remain_browsable(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    profile = add_student(session, woodchuck_id="WC-PRIOR", instrument="Flute")
    season = Season(
        key="band-camp-2025", name="Band Camp 2025",
        timezone="America/Chicago", starts_on=date(2025, 7, 28), status="closed",
    )
    contest = Contest(
        key="historical-points", name="Historical Points",
        metric_type="points", subject_type="student",
    )
    session.add_all([season, contest])
    session.flush()
    week = ContestWeek(
        season_id=season.id, week_start=date(2025, 7, 28),
        week_end=date(2025, 8, 4),
        verification_deadline_at=datetime(2025, 8, 4, 17, tzinfo=timezone.utc),
        finalize_after=datetime(2025, 8, 4, 17, 5, tzinfo=timezone.utc),
        status="finalized", finalized_at=datetime(2025, 8, 4, 18, tzinfo=timezone.utc),
    )
    session.add(week)
    session.flush()
    session.add(ContestResult(
        contest_week_id=week.id, contest_id=contest.id, division="open",
        subject_type="student", subject_key=str(profile.id), profile_id=profile.id,
        display_name_snapshot="Prior Winner", score=3, rank=1, medal="gold",
    ))
    session.commit()
    monkeypatch.setattr(contest_module, "SessionLocal", factory)

    payload = contest_module.contest_week_results(
        week.week_start, request_with_session(profile.id)
    )
    assert payload["week"]["status"] == "finalized"
    assert payload["results"][0]["display_name"] == "Prior Winner"


def test_hall_aggregates_students_instruments_divisions_and_prior_seasons(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    current_season, contests, current_week = ensure_band_camp_data(session, now=NOW)
    points = next(c for c in contests if c.key == "weekly-points-leaders")
    instruments = next(c for c in contests if c.key == "weekly-practice-by-instrument")
    prior_season = Season(
        key="band-camp-2025", name="Band Camp 2025",
        timezone="America/Chicago", starts_on=date(2025, 7, 28), status="closed",
    )
    session.add(prior_season)
    session.flush()
    prior_week = ContestWeek(
        season_id=prior_season.id, week_start=date(2025, 7, 28),
        week_end=date(2025, 8, 4),
        verification_deadline_at=datetime(2025, 8, 4, 17, tzinfo=timezone.utc),
        finalize_after=datetime(2025, 8, 4, 17, 5, tzinfo=timezone.utc),
        status="finalized", finalized_at=datetime(2025, 8, 4, 18, tzinfo=timezone.utc),
    )
    current_week.status = "finalized"
    current_week.finalized_at = FINAL_NOW
    session.add(prior_week)
    students = [
        add_student(session, woodchuck_id=f"WC-HALL-{index}", instrument="Tuba")
        for index in range(1, 4)
    ]
    session.flush()

    def result(
        *, week: ContestWeek, contest: Contest, division: str, medal: str,
        rank: int, subject_key: str, snapshot: str,
        profile: WoodchuckProfile | None = None, instrument: str | None = None,
    ) -> ContestResult:
        return ContestResult(
            contest_week_id=week.id, contest_id=contest.id, division=division,
            subject_type="student" if profile else "instrument",
            subject_key=subject_key, profile_id=profile.id if profile else None,
            instrument=instrument, display_name_snapshot=snapshot,
            score=10, rank=rank, medal=medal,
        )

    session.add_all([
        result(week=prior_week, contest=points, division="open", medal="gold",
               rank=1, subject_key=str(students[0].id), snapshot="Old Name", profile=students[0]),
        result(week=current_week, contest=points, division="verified", medal="silver",
               rank=2, subject_key=str(students[0].id), snapshot="Shared Name", profile=students[0]),
        result(week=current_week, contest=points, division="open", medal="bronze",
               rank=3, subject_key=str(students[1].id), snapshot="Shared Name", profile=students[1]),
        result(week=prior_week, contest=points, division="open", medal="gold",
               rank=1, subject_key=str(students[2].id), snapshot="Alpha", profile=students[2]),
        result(week=current_week, contest=points, division="verified", medal="gold",
               rank=1, subject_key=str(students[2].id), snapshot="Alpha", profile=students[2]),
        result(week=prior_week, contest=instruments, division="open", medal="gold",
               rank=1, subject_key="flute", snapshot="Flute", instrument="Flute"),
        result(week=current_week, contest=instruments, division="verified", medal="silver",
               rank=2, subject_key="flute", snapshot="Flute", instrument="Flute"),
        result(week=current_week, contest=instruments, division="open", medal="bronze",
               rank=3, subject_key="saxophone", snapshot="Saxophone", instrument="Saxophone"),
    ])
    session.add_all([
        CrownProgress(
            profile_id=students[0].id,
            category_key=points.crown_category or points.key,
            qualifying_wins=7,
        ),
        CrownProgress(
            profile_id=students[2].id,
            category_key=points.crown_category or points.key,
            qualifying_wins=10,
            crown_earned_at=FINAL_NOW,
        ),
    ])
    session.commit()

    payload = hall_of_champions_payload(session)

    assert [student["display_name"] for student in payload["students"]] == [
        "Alpha", "Shared Name", "Shared Name"
    ]
    renamed = payload["students"][1]
    assert renamed["medals"] == {"gold": 1, "silver": 1, "bronze": 0, "total": 2}
    assert renamed["by_division"] == {
        "open": {"gold": 1, "silver": 0, "bronze": 0, "total": 1},
        "verified": {"gold": 0, "silver": 1, "bronze": 0, "total": 1},
    }
    assert renamed["divisions"] == ["open", "verified"]
    assert renamed["crown"] == {
        "qualifying_wins": 7, "target_wins": 10, "earned": False
    }
    assert payload["students"][0]["crown"] == {
        "qualifying_wins": 10, "target_wins": 10, "earned": True
    }
    assert payload["instruments"][0] == {
        "instrument_key": "flute",
        "instrument_label": "Flute",
        "instrument_icon": "🪈",
        "medals": {"gold": 1, "silver": 1, "bronze": 0, "total": 2},
        "by_division": {
            "open": {"gold": 1, "silver": 0, "bronze": 0, "total": 1},
            "verified": {"gold": 0, "silver": 1, "bronze": 0, "total": 1},
        },
        "divisions": ["open", "verified"],
    }
    assert current_season.key == "band-camp-2026"


def test_hall_uses_persisted_instrument_snapshot_not_current_profile_instrument(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, contests, week = ensure_band_camp_data(session, now=NOW)
    instrument_contest = next(
        contest for contest in contests
        if contest.key == "weekly-practice-by-instrument"
    )
    student = add_student(session, woodchuck_id="WC-HALL-INSTRUMENT", instrument="Tuba")
    week.status = "finalized"
    week.finalized_at = FINAL_NOW
    session.add(ContestResult(
        contest_week_id=week.id, contest_id=instrument_contest.id,
        division="open", subject_type="instrument", subject_key="clarinet",
        instrument="Clarinet", display_name_snapshot="Clarinet",
        score=30, rank=1, medal="gold",
    ))
    student.instrument = "Saxophone"
    session.commit()

    payload = hall_of_champions_payload(session)
    assert payload["instruments"][0]["instrument_label"] == "Clarinet"
    assert "Saxophone" not in repr(payload["instruments"])


def test_hall_empty_authentication_and_privacy(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    profile = add_student(session, woodchuck_id="WC-HALL-PRIVATE", instrument="Flute")
    session.commit()
    monkeypatch.setattr(contest_module, "SessionLocal", factory)

    assert hall_of_champions_payload(session) == {"students": [], "instruments": []}
    with pytest.raises(HTTPException) as unauthorized:
        contest_module.hall_of_champions(request_with_session())
    assert unauthorized.value.status_code == 401
    payload = contest_module.hall_of_champions(request_with_session(profile.id))
    serialized = repr(payload).casefold()
    for private_field in (
        "profile_id", "account_id", "woodchuck_id", "wc-hall-private",
        "legal_name", "email", "pin", "verifier", "note", "p-chart",
    ):
        assert private_field not in serialized


def test_hall_ranking_uses_gold_silver_bronze_then_public_name() -> None:
    champions = [
        {"display_name": "Zulu", "medals": {"gold": 1, "silver": 0, "bronze": 1}},
        {"display_name": "Bravo", "medals": {"gold": 1, "silver": 1, "bronze": 0}},
        {"display_name": "Alpha", "medals": {"gold": 1, "silver": 0, "bronze": 1}},
        {"display_name": "Gold", "medals": {"gold": 2, "silver": 0, "bronze": 0}},
    ]

    ordered = sorted(champions, key=contest_module._champion_sort_key)

    assert [champion["display_name"] for champion in ordered] == [
        "Gold", "Bravo", "Alpha", "Zulu"
    ]


def test_hall_static_route_requires_authentication() -> None:
    response = TestClient(app).get("/contests/hall-of-champions")

    assert response.status_code == 401


def test_crown_progress_defaults_to_zero_without_creating_a_row(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    ensure_band_camp_data(session, now=NOW)
    profile = add_student(session, woodchuck_id="WC-CROWN-ZERO", instrument="Flute")
    session.commit()

    payload = crown_progress_payload(session, profile_id=profile.id)

    assert payload == {
        "qualifying_wins": 0,
        "target_wins": 10,
        "remaining_wins": 10,
        "earned": False,
        "earned_at": None,
        "categories": [
            {"key": key, "name": name, "progress": 0, "target": 10, "earned": False, "earned_at": None}
            for key, name in contest_module.CROWN_CATEGORIES
        ],
    }
    assert session.scalar(select(func.count()).select_from(CrownProgress)) == 0


@pytest.mark.parametrize(
    ("wins", "earned_at", "remaining", "earned"),
    [
        (7, None, 3, False),
        (9, None, 1, False),
        (10, FINAL_NOW, 0, True),
        (12, FINAL_NOW, 0, True),
    ],
)
def test_crown_progress_partial_earned_and_above_target(
    database: tuple[Session, sessionmaker[Session]],
    wins: int,
    earned_at: datetime | None,
    remaining: int,
    earned: bool,
) -> None:
    session, _ = database
    _, contests, _ = ensure_band_camp_data(session, now=NOW)
    points = next(c for c in contests if c.key == "weekly-points-leaders")
    profile = add_student(
        session, woodchuck_id=f"WC-CROWN-{wins}", instrument="Flute"
    )
    progress = CrownProgress(
        profile_id=profile.id,
        category_key=points.crown_category or points.key,
        qualifying_wins=wins,
        crown_earned_at=earned_at,
    )
    session.add(progress)
    session.commit()
    original_earned_at = progress.crown_earned_at

    payload = crown_progress_payload(session, profile_id=profile.id)

    assert payload["qualifying_wins"] == wins
    assert payload["remaining_wins"] == remaining
    assert payload["earned"] is earned
    assert payload["earned_at"] == (
        "2026-08-03T18:00:00+00:00" if earned_at else None
    )
    session.refresh(progress)
    assert progress.qualifying_wins == wins
    assert (
        contest_module.aware_utc(progress.crown_earned_at)
        if progress.crown_earned_at else None
    ) == (
        contest_module.aware_utc(original_earned_at)
        if original_earned_at else None
    )


def test_nonqualifying_medals_and_participation_do_not_change_crown_progress(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, contests, week = ensure_band_camp_data(session, now=NOW)
    points = next(c for c in contests if c.key == "weekly-points-leaders")
    instruments = next(c for c in contests if c.key == "weekly-practice-by-instrument")
    profile = add_student(session, woodchuck_id="WC-NONQUALIFY", instrument="Flute")
    week.status = "finalized"
    week.finalized_at = FINAL_NOW
    session.add_all([
        ContestResult(
            contest_week_id=week.id, contest_id=points.id, division="open",
            subject_type="student", subject_key=str(profile.id), profile_id=profile.id,
            display_name_snapshot="Nonqualifier", score=2, rank=2, medal="silver",
        ),
        ContestResult(
            contest_week_id=week.id, contest_id=instruments.id, division="open",
            subject_type="instrument", subject_key="flute", instrument="Flute",
            display_name_snapshot="Flute", score=20, rank=3, medal="bronze",
        ),
    ])
    session.flush()
    session.add(RewardGrant(
        profile_id=profile.id, source_key="participation-only",
        reward_type="dandelion", amount=1,
    ))
    session.commit()

    assert crown_progress_payload(session, profile_id=profile.id)[
        "qualifying_wins"
    ] == 0
    assert session.scalar(select(func.count()).select_from(CrownProgress)) == 0


def test_crown_progress_endpoint_authentication_and_privacy(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    profile = add_student(session, woodchuck_id="WC-CROWN-PRIVATE", instrument="Flute")
    session.commit()
    monkeypatch.setattr(contest_module, "SessionLocal", factory)

    with pytest.raises(HTTPException) as unauthorized:
        contest_module.current_crown_progress(request_with_session())
    assert unauthorized.value.status_code == 401
    payload = contest_module.current_crown_progress(request_with_session(profile.id))
    assert set(payload) == {
        "qualifying_wins", "target_wins", "remaining_wins", "earned", "earned_at", "categories"
    }
    serialized = repr(payload).casefold()
    for private_field in (
        "profile_id", "account_id", "woodchuck_id", "wc-crown-private",
        "legal_name", "email", "pin", "verifier",
    ):
        assert private_field not in serialized


def test_open_p_chart_submission_is_idempotent_listed_and_updates_standings(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, factory = database
    profile = add_student(
        session, woodchuck_id="WC-PERSIST-OPEN", instrument="Tuba"
    )
    session.commit()
    monkeypatch.setattr(practice_chart_routes_module, "SessionLocal", factory)
    request = request_with_session(profile.id)
    submitted = PracticeChartCreate(
        verifier_id=None,
        practice_date=date(2026, 7, 29),
        minutes=35,
        note="Completed chart",
        practice_details=["Long tones"],
        credits_awarded=7,
        submission_key="submission-open-001",
    )

    first = practice_chart_routes_module.create_student_practice_chart(
        request, submitted
    )
    repeated = practice_chart_routes_module.create_student_practice_chart(
        request, submitted
    )

    assert first["created"] is True
    assert repeated["created"] is False
    assert first["chart"]["id"] == repeated["chart"]["id"]
    assert session.scalar(select(func.count()).select_from(PracticeChart)) == 1
    history = practice_chart_routes_module.list_student_practice_charts(request)
    assert len(history["charts"]) == 1
    assert history["charts"][0]["minutes"] == 35
    assert history["charts"][0]["instrument"] == "Tuba"
    assert history["charts"][0]["verification"] is None

    payload = current_contests_payload(
        session, now=NOW, current_profile_id=profile.id
    )
    points = payload["standings"]["weekly-points-leaders"]
    instruments = payload["standings"]["weekly-practice-by-instrument"]
    assert points["open"][0]["total_minutes"] == 35
    assert points["verified"] == []
    assert instruments["open"] == [
        {"rank": 1, "instrument": "Tuba", "total_minutes": 35}
    ]
    assert instruments["verified"] == []

    profile.instrument = "Flute"
    session.add(PracticeChartVerification(
        practice_chart_id=first["chart"]["id"], verifier_id=None,
        status="approved",
        responded_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
    ))
    session.commit()
    approved_payload = current_contests_payload(
        session, now=NOW, current_profile_id=profile.id
    )
    approved_points = approved_payload["standings"]["weekly-points-leaders"]
    approved_instruments = approved_payload[
        "standings"
    ]["weekly-practice-by-instrument"]
    assert approved_points["verified"][0]["total_minutes"] == 35
    assert approved_instruments["verified"] == [
        {"rank": 1, "instrument": "Tuba", "total_minutes": 35}
    ]


def test_contest_opt_out_stays_in_history_but_not_either_division(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    _, _, week = ensure_band_camp_data(session, now=NOW)
    profile = add_student(session, woodchuck_id="WC-OPTOUT", instrument="Clarinet")
    opted_in = add_chart(
        session, profile=profile, practice_date=week.week_start,
        minutes=20, verification_status="approved", include_contests=True,
    )
    opted_out = add_chart(
        session, profile=profile, practice_date=week.week_start,
        minutes=80, verification_status="approved", include_contests=False,
    )
    session.commit()
    payload = current_contests_payload(session, now=NOW, current_profile_id=profile.id)
    assert payload["standings"]["weekly-points-leaders"]["open"][0]["total_minutes"] == 20
    assert payload["standings"]["weekly-points-leaders"]["verified"][0]["total_minutes"] == 20
    assert session.get(PracticeChart, opted_out.id).minutes == 80
    assert session.get(PracticeChart, opted_in.id).include_contests is True


def test_activity_crowns_reconcile_upward_and_tenth_event_is_permanent(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    profile = add_student(session, woodchuck_id="WC-ACTIVITY-CROWN", instrument="Flute")
    for index in range(12):
        occurred = NOW + timedelta(days=index)
        session.add(CampPointAward(
            profile_id=profile.id, activity_type="trivia", points_awarded=1,
            occurred_at=occurred, duplicate_key=f"trivia-{index}",
        ))
    session.commit()
    payload = crown_progress_payload(session, profile_id=profile.id)
    trivia = next(item for item in payload["categories"] if item["key"] == "trivia")
    assert trivia["progress"] == 12 and trivia["earned"] is True
    progress = session.scalar(select(CrownProgress).where(
        CrownProgress.profile_id == profile.id,
        CrownProgress.category_key == "trivia",
    ))
    assert progress is not None and progress.qualifying_wins == 12
    earned_at = progress.crown_earned_at
    session.query(CampPointAward).filter(CampPointAward.profile_id == profile.id).delete()
    session.commit()
    crown_progress_payload(session, profile_id=profile.id)
    assert progress.qualifying_wins == 12 and progress.crown_earned_at == earned_at


def test_historical_hours_awards_still_feed_band_camp_hours_crown(
    database: tuple[Session, sessionmaker[Session]],
) -> None:
    session, _ = database
    profile = add_student(session, woodchuck_id="WC-HOURS-CROWN", instrument="Flute")
    for index in range(10):
        session.add(CampPointAward(
            profile_id=profile.id, activity_type="hours", points_awarded=1,
            occurred_at=NOW - timedelta(days=20 - index),
            duplicate_key=f"historical-hours-{index}",
        ))
    session.commit()

    payload = crown_progress_payload(session, profile_id=profile.id)
    hours = next(
        item for item in payload["categories"]
        if item["key"] == "band-camp-hours"
    )
    assert hours["name"] == "Band Camp Hours Crown"
    assert hours["progress"] == 10
    assert hours["earned"] is True
