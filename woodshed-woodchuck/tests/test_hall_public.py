"""Public Hall boundary checks with synthetic historical results and real HTTP."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, contests, main
from app.db import Base
from app.age_privacy import declare_age
from app.models import (Contest, ContestResult, ContestWeek, DirectorTeamContest,
                        DirectorTeamContestResult, Season, Team, WoodchuckProfile)
from app.security import hash_pin
from tests.team_factory import make_team

NOW = datetime(2026, 8, 3, 18, tzinfo=timezone.utc)
SENTINEL = "synthetic-private-future-field"


@pytest.fixture
def hall_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app import session_revocations
    monkeypatch.setattr(session_revocations, "SessionLocal", factory)
    for module in (account_routes, contests, main):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as session:
        people = [WoodchuckProfile(
            woodchuck_id=f"WC-DTO-{number}", display_name=name, pin_hash=hash_pin("2468"),
            instrument="Flute", level="Beginner", goal="Practice",
        ) for number, name in enumerate(("Alpha", "Beta"), 1)]
        season = Season(key="hall-synthetic-2026", name="Synthetic Season",
                        timezone="America/Chicago", starts_on=date(2026, 7, 27),
                        status="active")
        session.add_all([season, *people]); session.flush()
        for person in people:
            declare_age(session, person.id, "adult", at=NOW-timedelta(days=30))
        week = ContestWeek(season_id=season.id, week_start=date(2026, 7, 27),
                           week_end=date(2026, 8, 3), verification_deadline_at=NOW,
                           finalize_after=NOW, finalized_at=NOW, status="finalized")
        team = make_team(session, season_id=season.id, display_name="Team Alpha",
                         normalized_name="team alpha", emblem_key="shield:gold",
                         creator_profile_id=people[0].id)
        session.add_all([week, team]); session.flush()
        for kind, subjects in (("student", people), ("team", [team]), ("instrument", [None])):
            contest = Contest(key=f"dto-{kind}", name=f"Synthetic {kind}",
                              metric_type="practice_minutes", subject_type=kind, active=True)
            session.add(contest); session.flush()
            for subject in subjects:
                session.add(ContestResult(
                    contest_week_id=week.id, contest_id=contest.id, division="open",
                    subject_type=kind, subject_key=str(subject.id) if subject else "flute",
                    profile_id=subject.id if kind == "student" else None,
                    team_id=team.id if kind == "team" else None,
                    instrument="Flute" if kind == "instrument" else None,
                    display_name_snapshot=subject.display_name if subject else "Flute",
                    score=42, rank=1, medal="gold",
                ))
        event = DirectorTeamContest(
            season_id=season.id, owner_profile_id=people[0].id,
            title="Synthetic Invitational", description="Private director notes",
            metric="total_minutes", starts_at=NOW-timedelta(days=1), ends_at=NOW,
            finalizes_at=NOW, finalized_at=NOW, status="finalized",
        )
        session.add(event); session.flush()
        session.add(DirectorTeamContestResult(
            contest_id=event.id, team_id=team.id, team_name_snapshot="Team Alpha",
            emblem_key_snapshot="shield:gold", score=42.5, rank=1,
            active_participant_count=2, eligible_roster_count=2,
        ))
        session.commit()
    yield factory
    engine.dispose()


def client_for(number):
    client = TestClient(main.app)
    assert client.post("/account/login", data={
        "woodchuck_id": f"WC-DTO-{number}", "pin": "2468",
    }).status_code == 200
    return client


def poison_mappings(value):
    """Model future private metadata at every dictionary level, not just `_` keys."""
    if isinstance(value, dict):
        for child in list(value.values()):
            poison_mappings(child)
        value["future_metadata"] = SENTINEL
        value["parent_email"] = "synthetic-parent@example.invalid"
        value["_internal"] = SENTINEL
    elif isinstance(value, list):
        for child in value:
            poison_mappings(child)


def test_http_hall_rejects_future_fields_from_rankings_and_cups(hall_db, monkeypatch):
    client = client_for(1)
    expected = client.get("/contests/hall-of-champions").json()
    assert all(expected[key] for key in ("students", "teams", "instruments", "director_team_contests"))
    rank = contests._rank_champions
    cups = contests._traveling_cup_state

    def poisoned_rank(rows):
        result = rank(rows)
        poison_mappings(result)
        for row in result:
            row["woodchuck_id"] = "WC-PRIVATE-LEAK"
            row["profile_id"] = 900001
            row["pin_hash"] = "synthetic-private-pin-hash"
        return result

    def poisoned_cups(*args, **kwargs):
        public, private = cups(*args, **kwargs)
        poison_mappings(public)
        return public, private

    monkeypatch.setattr(contests, "_rank_champions", poisoned_rank)
    monkeypatch.setattr(contests, "_traveling_cup_state", poisoned_cups)
    response = client.get("/contests/hall-of-champions?_include_internal=true&profile_id=900001")
    assert response.status_code == 200
    assert SENTINEL not in response.text
    assert response.json() == expected
    assert client_for(2).get("/contests/hall-of-champions").json() == expected
    for secret in ("WC-DTO-1", "WC-DTO-2", "WC-PRIVATE-LEAK", "900001", "pin_hash", "parent_email"):
        assert secret not in response.text
    assert TestClient(main.app).get("/contests/hall-of-champions?_include_internal=true").status_code == 401


def test_http_hall_projects_every_nested_mapping_without_mutating_internal_data(hall_db, monkeypatch):
    client = client_for(1)
    expected = client.get("/contests/hall-of-champions").json()
    project = contests.public_hall_payload
    captured = []

    def inject_before_public_boundary(payload):
        poison_mappings(payload)
        before = deepcopy(payload)
        public = project(payload)
        assert payload == before
        captured.append(payload)
        return public

    monkeypatch.setattr(contests, "public_hall_payload", inject_before_public_boundary)
    response = client.get("/contests/hall-of-champions?_include_internal=true")
    assert response.status_code == 200
    assert response.json() == expected
    assert SENTINEL not in response.text
    assert captured[0]["students"][0]["_profile_id"]
    assert captured[0]["teams"][0]["_owner_profile_id"]
    assert captured[0]["teams"][0]["_normalized_name"] == "team alpha"
    assert captured[0]["director_team_contests"][0]["winners"][0]["future_metadata"] == SENTINEL


def test_public_copies_preserve_internal_entitlements_and_share_no_containers(hall_db):
    with hall_db() as session:
        internal = contests.hall_of_champions_payload(session, _include_internal=True, now=NOW)
        before = deepcopy(internal)
        public = contests.public_hall_payload(internal)
        assert internal == before
        assert internal["_traveling_cup_entitlements"]["punxsutawney_profile_ids"] == {
            row["_profile_id"] for row in internal["students"]
        }
        assert all(row["rank"] == 1 for row in public["students"])
        assert [row["display_name"] for row in public["students"]] == ["Alpha", "Beta"]
        for row in internal["students"]:
            assert contests.traveling_cup_entitlements(session, profile_id=row["_profile_id"], now=NOW)["punxsutawney"]
        poison_mappings(public)
        public["students"][0]["divisions"].append("private-change")
        public["director_team_contests"][0]["winners"].clear()
        assert internal == before


def test_hall_preserves_pristine_division_and_historical_moderation(hall_db):
    client = client_for(1)
    with hall_db() as session:
        row = session.scalar(select(ContestResult).where(ContestResult.subject_type == "student"))
        row.division = "pristine"
        team = session.scalar(select(Team))
        team.moderation_status = "hidden"
        session.commit()
    payload = client.get("/contests/hall-of-champions").json()
    student = next(row for row in payload["students"] if row["display_name"] == "Alpha")
    assert student["divisions"] == ["pristine"]
    assert student["by_division"]["pristine"]["gold"] == 1
    assert student["by_division"]["open"]["total"] == 0
    assert student["by_division"]["verified"]["total"] == 0
    assert student["achievements"][0]["division"] == "pristine"
    assert payload["teams"] == []
    assert payload["traveling_cups"]["coterie"]["teams"] == []
    # The separate director event projection retains its existing masking.
    row = payload["director_team_contests"][0]["winners"][0]
    assert row["team_name"] == "Hidden Team"
    assert row["emblem_key"] == "shield:silver"
    with hall_db() as session:
        assert session.scalar(select(ContestResult).where(ContestResult.subject_type == "team")).display_name_snapshot == "Team Alpha"
        assert session.scalar(select(DirectorTeamContestResult)).score == 42.5
