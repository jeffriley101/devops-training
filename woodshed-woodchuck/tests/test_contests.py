from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

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
    finalize_contest_week,
    contest_results_payload,
    weekly_practice_by_instrument,
    weekly_student_points,
)
from app.db import Base
from app.models import (
    Contest,
    ContestResult,
    ContestWeek,
    CrownProgress,
    PracticeChart,
    PracticeChartVerification,
    RewardGrant,
    Season,
    WoodchuckProfile,
    WoodchuckState,
)


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
        "total_points": 2,
        "is_current_user": True,
    }
    assert standings["current_user_position"]["open"] == {
        "rank": 6,
        "total_points": 2,
        "points_behind_leader": 5,
        "tied": False,
        "in_top_five": False,
        "has_score": True,
    }
    assert standings["current_user_position"]["verified"]["total_points"] == 1
    assert all(
        set(row) == {
            "rank", "display_name", "total_points", "is_current_user"
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

    assert [(row["rank"], row["display_name"], row["total_points"]) for row in standings["open"]] == [
        (1, "Alpha", 2),
        (1, "Beta", 2),
        (3, "Gamma", 1),
    ]
    assert [(row["display_name"], row["total_points"]) for row in standings["verified"]] == [
        ("Alpha", 2),
        ("Beta", 1),
    ]
    assert standings["current_user_position"]["open"]["tied"] is True
    assert standings["current_user_position"]["open"]["points_behind_leader"] == 0
    assert standings["current_user_position"]["verified"]["points_behind_leader"] == 1


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
        "total_points": 0,
        "points_behind_leader": 1,
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
        "total_points": 1,
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
    assert not any(
        grant.profile_id in {beta.id, gamma.id}
        and grant.source_key.endswith("gold")
        for grant in session.scalars(select(RewardGrant))
    )
    crown = session.scalar(select(CrownProgress).where(CrownProgress.profile_id == alpha.id))
    assert crown is not None and crown.qualifying_wins == 1


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
                      minutes=5, verification_status="approved")
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
            RewardGrant.source_key.like("%:gold"),
        )).all()
        assert {grant.reward_type for grant in gold_grants} == {"dandelion", "crown_win"}
        assert len(gold_grants) == 2
        crown = session.scalar(select(CrownProgress).where(CrownProgress.profile_id == student.id))
        assert crown is not None and crown.qualifying_wins == 1


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
    add_chart(session, profile=student, practice_date=date(2026, 7, 31), minutes=5)
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
              verification_status="approved")
    add_chart(session, profile=short, practice_date=date(2026, 8, 1), minutes=14,
              verification_status="approved")
    add_chart(session, profile=rival, practice_date=date(2026, 8, 1), minutes=10,
              verification_status="approved")
    session.commit()
    finalize_contest_week(session, week_start=week.week_start, now=FINAL_NOW)
    session.commit()

    participation = session.scalars(select(RewardGrant).where(
        RewardGrant.source_key.like("%:participant:%")
    )).all()
    assert [(grant.profile_id, grant.amount) for grant in participation] == [(eligible.id, 1)]
    assert all(grant.reward_type == "dandelion" and grant.category_key is None for grant in participation)


def test_failure_rolls_back_every_finalization_change(
    database: tuple[Session, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = database
    week = ready_week(session)
    student = add_student(session, woodchuck_id="WC-ROLLBACK", instrument="Flute")
    session.add(WoodchuckState(profile_id=student.id, state_json={"progress": {"credits": 7}}, revision=4))
    add_chart(session, profile=student, practice_date=date(2026, 8, 2), minutes=20)
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
                      verification_status="approved")
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
    payload = contest_module.contest_week_results(week.week_start, request_with_session(profile.id))
    assert set(payload) == {"week", "results"}
