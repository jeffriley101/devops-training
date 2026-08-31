from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, director_dashboard, main, teams
from app.contests import hall_of_champions_payload
from app.db import Base
from app.director_dashboard import (
    DirectorContestCreate,
    create_director_contest,
    dashboard_payload,
    finalize_director_contest,
)
from app.main import app
from app.models import (
    DirectorTeamContestResult,
    PracticeChart,
    PracticeChartVerification,
    ProfileCapability,
    Season,
    Team,
    TeamMembership,
    WoodchuckProfile,
)
from app.security import hash_pin
from app.teams import create_director_team


NOW = datetime(2026, 8, 25, 18, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class FixedDashboardDateTime(datetime):
    """Keep endpoint week selection aligned with deterministic fixture data."""

    @classmethod
    def now(cls, tz=None):
        return NOW.replace(tzinfo=None) if tz is None else NOW.astimezone(tz)


@pytest.fixture()
def dashboard_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    for module in (account_routes, director_dashboard, main, teams):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(director_dashboard, "datetime", FixedDashboardDateTime)
    with factory() as session:
        season = Season(
            key="back-to-school-2026", name="Back to School",
            timezone="America/Chicago", starts_on=date(2026, 8, 3), status="active",
        )
        session.add(season)
        for key, display in (
            ("DIRECTOR", "Director Person"),
            ("OTHER", "Other Director"),
            ("STUDENT", "Student Person"),
            ("MEMBER", "Member Person"),
        ):
            session.add(WoodchuckProfile(
                woodchuck_id=f"WC-{key}", display_name=display,
                pin_hash=hash_pin("2468"), instrument="Flute",
                level="Beginner", goal="Practice",
            ))
        session.flush()
        for key in ("DIRECTOR", "OTHER"):
            profile = session.scalar(select(WoodchuckProfile).where(
                WoodchuckProfile.woodchuck_id == f"WC-{key}"
            ))
            session.add(ProfileCapability(
                profile_id=profile.id, capability="band_director",
                granted_by="contest-admin",
            ))
        session.commit()
    yield factory


def client_for(woodchuck_id: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/account/login", data={
        "woodchuck_id": woodchuck_id, "pin": "2468",
    }).status_code == 200
    return client


def profile(session, key: str) -> WoodchuckProfile:
    return session.scalar(select(WoodchuckProfile).where(
        WoodchuckProfile.woodchuck_id == f"WC-{key}"
    ))


def add_owned_team(factory, owner_key: str, name: str, emblem: str) -> Team:
    with factory() as session:
        owner = profile(session, owner_key)
        season = session.scalar(select(Season))
        return create_director_team(
            session, profile=owner, season=season, name=name,
            emblem_key=emblem, now=NOW,
        )


def test_dashboard_route_authorization_six_cards_and_multi_team_selector(
    dashboard_database,
) -> None:
    unauthorized = client_for("WC-STUDENT")
    response = unauthorized.get("/director", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/home"
    assert unauthorized.get("/director/dashboard").status_code == 403

    first = add_owned_team(dashboard_database, "DIRECTOR", "North Band", "letter:N")
    second = add_owned_team(dashboard_database, "DIRECTOR", "South Band", "letter:S")
    director = client_for("WC-DIRECTOR")
    page = director.get("/director")
    assert page.status_code == 200
    assert page.text.count('class="director-metric-card"') == 6
    assert "Director Dashboard" in page.text and "My Woodshed" in page.text
    assert "leaderboard" not in page.text.casefold()
    payload = director.get(f"/director/dashboard?team_id={second.id}").json()
    assert payload["team"]["id"] == second.id
    assert {row["id"] for row in payload["teams"]} == {first.id, second.id}

    other_team = add_owned_team(dashboard_database, "OTHER", "Other School", "letter:O")
    denied = director.get(f"/director/dashboard?team_id={other_team.id}")
    assert denied.status_code == 404


def test_dashboard_aggregates_metrics_charts_and_pending_state_without_identity_leak(
    dashboard_database,
) -> None:
    team = add_owned_team(dashboard_database, "DIRECTOR", "Metrics Band", "emoji:eagle")
    with dashboard_database() as session:
        member = profile(session, "MEMBER")
        student = profile(session, "STUDENT")
        season = session.scalar(select(Season))
        session.add_all([
            TeamMembership(
                season_id=season.id, team_id=team.id, profile_id=member.id,
                selected_week_start=date(2026, 8, 24), started_at=NOW - timedelta(days=5),
            ),
            TeamMembership(
                season_id=season.id, team_id=team.id, profile_id=student.id,
                selected_week_start=date(2026, 8, 24), started_at=NOW - timedelta(days=5),
            ),
        ])
        session.flush()
        first = PracticeChart(
            profile_id=member.id, practice_date=date(2026, 8, 24), minutes=30,
            instrument="Flute", team_id=team.id, include_contests=True,
            include_team_contests=True, created_at=NOW - timedelta(days=1),
        )
        second = PracticeChart(
            profile_id=student.id, practice_date=date(2026, 8, 25), minutes=3,
            instrument="Trumpet", team_id=team.id, include_contests=True,
            include_team_contests=True, created_at=NOW,
        )
        session.add_all([first, second]); session.flush()
        session.add_all([
            PracticeChartVerification(practice_chart_id=first.id, status="approved"),
            PracticeChartVerification(practice_chart_id=second.id, status="pending"),
        ])
        session.commit()

    director = client_for("WC-DIRECTOR")
    payload = director.get(f"/director/dashboard?team_id={team.id}").json()
    assert payload["period"] == {
        "week_start": "2026-08-24",
        "week_end": "2026-08-31",
    }
    metrics = payload["metrics"]
    assert metrics["total_practice_minutes"] == 33
    assert metrics["average_minutes"] == 30
    assert metrics["participation"] == {"active": 1, "eligible": 2, "percent": 50}
    assert metrics["p_charts"] == {"submitted": 2, "verified": 1, "pending": 1}
    assert metrics["consistency"]["days"] == 1
    assert metrics["team_practice_rating"] > 0
    assert payload["charts"]["daily_practice"][0]["minutes"] == 30
    assert {row["instrument"] for row in payload["charts"]["by_instrument"]} == {
        "Flute", "Trumpet",
    }
    assert "Member Person" not in repr(payload["metrics"])
    assert "Student Person" not in repr(payload["charts"])


def _event_setup(factory, metric: str):
    first = add_owned_team(factory, "DIRECTOR", f"Alpha {metric}", "letter:A")
    second = add_owned_team(factory, "DIRECTOR", f"Bravo {metric}", "letter:B")
    start = datetime(2026, 8, 20, 14, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    with factory() as session:
        owner = profile(session, "DIRECTOR")
        member = profile(session, "MEMBER")
        student = profile(session, "STUDENT")
        season = session.scalar(select(Season))
        session.add_all([
            TeamMembership(
                season_id=season.id, team_id=first.id, profile_id=member.id,
                selected_week_start=date(2026, 8, 17), started_at=start - timedelta(days=1),
            ),
            TeamMembership(
                season_id=season.id, team_id=second.id, profile_id=student.id,
                selected_week_start=date(2026, 8, 17), started_at=start - timedelta(days=1),
            ),
        ])
        session.commit()
        submitted = DirectorContestCreate(
            title=f"{metric} Invitational", description="Exact window",
            starts_at=start, ends_at=end, finalizes_at=end,
            metric=metric, team_ids=[first.id, second.id],
        )
        contest = create_director_contest(
            session, profile=owner, submitted=submitted, now=start
        )
        session.add_all([
            PracticeChart(
                profile_id=member.id, practice_date=start.date(), minutes=99,
                instrument="Flute", team_id=first.id, include_contests=True,
                include_team_contests=True, created_at=start - timedelta(seconds=1),
            ),
            PracticeChart(
                profile_id=member.id, practice_date=start.date(), minutes=30,
                instrument="Flute", team_id=first.id, include_contests=True,
                include_team_contests=True, created_at=start,
            ),
            PracticeChart(
                profile_id=student.id, practice_date=start.date(), minutes=30,
                instrument="Trumpet", team_id=second.id, include_contests=True,
                include_team_contests=True, created_at=end - timedelta(seconds=1),
            ),
            PracticeChart(
                profile_id=student.id, practice_date=end.date(), minutes=99,
                instrument="Trumpet", team_id=second.id, include_contests=True,
                include_team_contests=True, created_at=end,
            ),
        ])
        session.commit()
        return contest.id, owner.id, first.id, second.id, end


@pytest.mark.parametrize(
    "metric,expected",
    [("total_minutes", 30.0), ("average_minutes", 30.0), ("team_practice_rating", 12.0)],
)
def test_director_contest_exact_window_metrics_ties_and_finalization(
    dashboard_database, metric: str, expected: float,
) -> None:
    contest_id, owner_id, _first_id, _second_id, end = _event_setup(
        dashboard_database, metric
    )
    with dashboard_database() as session:
        contest = session.get(director_dashboard.DirectorTeamContest, contest_id)
        owner = session.get(WoodchuckProfile, owner_id)
        finalized, created = finalize_director_contest(
            session, contest=contest, profile=owner, now=end
        )
        assert created is True and finalized.status == "finalized"
        rows = session.scalars(select(DirectorTeamContestResult).where(
            DirectorTeamContestResult.contest_id == contest_id
        ).order_by(DirectorTeamContestResult.team_name_snapshot)).all()
        assert [row.rank for row in rows] == [1, 1]
        assert [row.score for row in rows] == [expected, expected]
        assert all(row.active_participant_count == 1 for row in rows)
        # A retry and later practice cannot mutate the historical result.
        membership = session.scalar(select(TeamMembership).where(
            TeamMembership.team_id == rows[0].team_id
        ))
        session.add(PracticeChart(
            profile_id=membership.profile_id, practice_date=end.date(), minutes=300,
            instrument="Flute", team_id=rows[0].team_id,
            include_contests=True, include_team_contests=True,
            created_at=end + timedelta(hours=1),
        ))
        session.commit()
        _same, created_again = finalize_director_contest(
            session, contest=finalized, profile=owner, now=end + timedelta(days=1)
        )
        assert created_again is False
        assert [row.score for row in session.scalars(select(DirectorTeamContestResult).where(
            DirectorTeamContestResult.contest_id == contest_id
        ).order_by(DirectorTeamContestResult.team_name_snapshot)).all()] == [expected, expected]


def test_contest_authorization_and_hall_are_private_roster_safe(dashboard_database) -> None:
    contest_id, owner_id, first_id, _second_id, end = _event_setup(
        dashboard_database, "total_minutes"
    )
    with dashboard_database() as session:
        owner = session.get(WoodchuckProfile, owner_id)
        contest = session.get(director_dashboard.DirectorTeamContest, contest_id)
        finalize_director_contest(session, contest=contest, profile=owner, now=end)
        hall = hall_of_champions_payload(session)
        event = hall["director_team_contests"][0]
        assert event["title"] == "total_minutes Invitational"
        assert len(event["winners"]) == 2
        assert hall["teams"] == []
        assert "Director Person" not in repr(event)
        assert "Member Person" not in repr(event)

    student = client_for("WC-STUDENT")
    assert student.post(f"/director/contests/{contest_id}/finalize").status_code == 403
    other = client_for("WC-OTHER")
    assert other.post(f"/director/contests/{contest_id}/finalize").status_code == 404
    other_team = add_owned_team(dashboard_database, "OTHER", "Foreign Team", "letter:F")
    denied = client_for("WC-DIRECTOR").post("/director/contests", json={
        "title": "Unauthorized Event", "description": "",
        "starts_at": (NOW + timedelta(days=1)).isoformat(),
        "ends_at": (NOW + timedelta(days=2)).isoformat(),
        "finalizes_at": (NOW + timedelta(days=2)).isoformat(),
        "metric": "total_minutes", "team_ids": [first_id, other_team.id],
    })
    assert denied.status_code == 403


def test_director_dashboard_migration_follows_published_team_foundation() -> None:
    migration = (ROOT / "migrations" / "versions" /
                 "e6f7a8b9c0d1_add_director_dashboard_contests.py").read_text()
    assert 'revision = "e6f7a8b9c0d1"' in migration
    assert 'down_revision = "d5e6f7a8b9c0"' in migration
    assert '"director_team_contests"' in migration
    assert '"director_team_contest_entries"' in migration
    assert '"director_team_contest_results"' in migration
    assert 'batch.drop_constraint("uq_team_season_creator"' in migration
    assert '"uq_team_public_season_creator"' in migration
