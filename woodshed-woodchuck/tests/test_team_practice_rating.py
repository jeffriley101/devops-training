from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.contests import team_leaderboards
from app.db import Base
from app.models import (
    ContestWeek,
    PracticeChart,
    Season,
    Team,
    TeamMembership,
    WoodchuckProfile,
)
from app.team_practice_rating import (
    ACTIVE_MINUTES_THRESHOLD,
    PARTICIPATION_BASE,
    PARTICIPATION_WEIGHT,
    TPR_MEMBER_MINUTES_CAP,
    TPR_NORMALIZATION,
    calculate_team_practice_rating,
)


def test_tpr_constants_and_realistic_simulations() -> None:
    assert ACTIVE_MINUTES_THRESHOLD == 5
    assert TPR_MEMBER_MINUTES_CAP == 120
    assert PARTICIPATION_BASE == 0.75
    assert PARTICIPATION_WEIGHT == 0.25
    assert TPR_NORMALIZATION == 2.5
    assert calculate_team_practice_rating([30] * 5, eligible_roster=5).rating == 26.8
    assert calculate_team_practice_rating([30] * 30, eligible_roster=30).rating == 65.7
    assert calculate_team_practice_rating([30] * 80, eligible_roster=80).rating == 107.3


def test_tpr_rejects_one_minute_gaming_and_softens_dormant_roster() -> None:
    one_minute = calculate_team_practice_rating([1] * 80, eligible_roster=80)
    assert one_minute.rating == 0
    active = calculate_team_practice_rating([30] * 10, eligible_roster=10)
    dormant = calculate_team_practice_rating([30] * 10, eligible_roster=80)
    assert active.rating > dormant.rating > 0
    assert dormant.participation_rate == 0.125


def test_tpr_caps_one_extreme_player_and_has_diminishing_team_size_returns() -> None:
    extreme = calculate_team_practice_rating([10_000], eligible_roster=80)
    moderate_band = calculate_team_practice_rating([30] * 30, eligible_roster=30)
    assert extreme.rating == 36.1
    assert moderate_band.rating > extreme.rating
    five = calculate_team_practice_rating([30] * 5, eligible_roster=5).rating
    twenty = calculate_team_practice_rating([30] * 20, eligible_roster=20).rating
    eighty = calculate_team_practice_rating([30] * 80, eligible_roster=80).rating
    assert twenty / five < 4
    assert eighty / twenty < 4


def test_tpr_board_is_server_authoritative_private_safe_and_olympic_ranked() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 4, 15, tzinfo=timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        season = Season(
            key="back-to-school-2026", name="Back to School",
            timezone="America/Chicago", starts_on=date(2026, 8, 3),
            status="active",
        )
        session.add(season); session.flush()
        week = ContestWeek(
            season_id=season.id, week_start=date(2026, 8, 3),
            week_end=date(2026, 8, 10), status="open",
            verification_deadline_at=datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
            finalize_after=datetime(2026, 8, 10, 13, tzinfo=timezone.utc),
        )
        session.add(week); session.flush()
        teams = []
        for index in range(3):
            owner = WoodchuckProfile(
                woodchuck_id=f"WC-TPR-{index}", display_name=f"Private Person {index}",
                pin_hash="hash", instrument="Flute", level="Beginner", goal="Practice",
            )
            session.add(owner); session.flush()
            team = Team(
                season_id=season.id, display_name=f"Band {index}",
                normalized_name=f"band {index}", emblem_key=f"letter:{chr(65 + index)}",
                creator_profile_id=owner.id,
                visibility="private" if index == 0 else "public",
                director_led=index == 0,
                join_code="PRIVATE1" if index == 0 else None,
            )
            session.add(team); session.flush(); teams.append((team, owner))
            session.add(TeamMembership(
                season_id=season.id, team_id=team.id, profile_id=owner.id,
                selected_week_start=week.week_start, started_at=now,
            ))
            session.add(PracticeChart(
                profile_id=owner.id, practice_date=date(2026, 8, 4), minutes=30,
                instrument="Flute", include_contests=True,
                include_team_contests=True, team_id=team.id, created_at=now,
            ))
        session.commit()
        rows = team_leaderboards(
            session, season=season, contest_week=week
        )["team-practice-rating"]["open"]
        assert [row["rank"] for row in rows] == [1, 1, 1]
        assert [row["emblem_key"] for row in rows] == ["letter:A", "letter:B", "letter:C"]
        private = next(row for row in rows if row["team_id"] == teams[0][0].id)
        assert all("captain_name" not in row and "captain_label" not in row for row in rows)
        assert "Private Person" not in repr(private)
