from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, store_routes
from app.contests import hall_of_champions_payload
from app.db import Base
from app.main import app
from app.models import (
    Contest,
    ContestResult,
    ContestWeek,
    CrownAward,
    CrownProgress,
    RewardGrant,
    Season,
    Team,
    TeamMembership,
    TravelingCupPlacement,
    WoodchuckProfile,
    WoodchuckState,
)
from app.security import hash_pin
from app.store_inventory import list_inventory_payloads


CENTRAL = ZoneInfo("America/Chicago")


@pytest.fixture()
def cup_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(store_routes, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_profile(session, suffix: str) -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id=f"WC-CUP-{suffix}",
        display_name=f"Cup {suffix}",
        pin_hash=hash_pin("2468"),
        instrument="Flute",
        level="Beginner",
        goal="Practice",
    )
    session.add(profile)
    session.flush()
    session.add(WoodchuckState(
        profile_id=profile.id,
        state_json={"progress": {"credits": 10}},
        revision=1,
    ))
    return profile


def add_medal(
    session,
    *,
    week: ContestWeek,
    suffix: str,
    medal: str,
    profile: WoodchuckProfile | None = None,
    team: Team | None = None,
) -> None:
    subject_type = "student" if profile is not None else "team"
    contest = Contest(
        key=f"cup-test-{subject_type}-{suffix}",
        name=f"Cup Test {suffix}",
        metric_type="practice_minutes",
        subject_type=subject_type,
        crown_category=None,
        active=True,
    )
    session.add(contest)
    session.flush()
    subject = profile if profile is not None else team
    assert subject is not None
    session.add(ContestResult(
        contest_week_id=week.id,
        contest_id=contest.id,
        division="open",
        subject_type=subject_type,
        subject_key=f"{subject.id}:{suffix}",
        profile_id=profile.id if profile is not None else None,
        team_id=team.id if team is not None else None,
        display_name_snapshot=(
            profile.display_name if profile is not None else team.display_name
        ),
        score=100,
        rank={"gold": 1, "silver": 2, "bronze": 3}[medal],
        medal=medal,
    ))


def build_cup_world(factory):
    now = datetime.now(timezone.utc)
    central_today = now.astimezone(CENTRAL).date()
    week_start = central_today - timedelta(days=central_today.weekday())
    with factory() as session:
        holder = add_profile(session, "HOLDER")
        challenger = add_profile(session, "CHALLENGER")
        member = add_profile(session, "MEMBER")
        runner_member = add_profile(session, "RUNNER")
        former_member = add_profile(session, "FORMER")
        outsider = add_profile(session, "OUTSIDER")
        owner = add_profile(session, "OWNER")
        runner_owner = add_profile(session, "RUNNER-OWNER")
        season = Season(
            key="band-camp-cup-inventory",
            name="Cup Inventory",
            timezone="America/Chicago",
            starts_on=week_start,
            ends_on=None,
            status="active",
        )
        session.add(season)
        session.flush()
        week = ContestWeek(
            season_id=season.id,
            week_start=week_start,
            week_end=week_start + timedelta(days=7),
            verification_deadline_at=now - timedelta(minutes=10),
            finalize_after=now - timedelta(minutes=5),
            status="finalized",
            finalized_at=now,
        )
        leader_team = Team(
            season_id=season.id,
            display_name="Cup Leaders",
            normalized_name="cup leaders",
            emblem_key="shield:gold",
            creator_profile_id=owner.id,
        )
        runner_team = Team(
            season_id=season.id,
            display_name="Cup Runners",
            normalized_name="cup runners",
            emblem_key="shield:red",
            creator_profile_id=runner_owner.id,
        )
        session.add_all([week, leader_team, runner_team])
        session.flush()
        session.add_all([
            TeamMembership(
                season_id=season.id,
                team_id=leader_team.id,
                profile_id=member.id,
                selected_week_start=week_start,
                started_at=now - timedelta(days=2),
            ),
            TeamMembership(
                season_id=season.id,
                team_id=runner_team.id,
                profile_id=runner_member.id,
                selected_week_start=week_start,
                started_at=now - timedelta(days=2),
            ),
            TeamMembership(
                season_id=season.id,
                team_id=leader_team.id,
                profile_id=former_member.id,
                selected_week_start=week_start,
                started_at=now - timedelta(days=3),
                ended_at=now - timedelta(days=1),
            ),
        ])
        add_medal(
            session, week=week, suffix="student-holder-gold", medal="gold",
            profile=holder,
        )
        add_medal(
            session, week=week, suffix="team-leader-gold", medal="gold",
            team=leader_team,
        )
        session.commit()
        return {
            "holder": holder.id,
            "challenger": challenger.id,
            "member": member.id,
            "runner_member": runner_member.id,
            "former_member": former_member.id,
            "outsider": outsider.id,
            "owner": owner.id,
            "leader_team": leader_team.id,
            "runner_team": runner_team.id,
            "week": week.id,
        }


def signed_client(woodchuck_id: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/account/login",
        data={"woodchuck_id": woodchuck_id, "pin": "2468"},
    )
    assert response.status_code == 200
    return client


def inventory_by_id(session, profile_id: int) -> dict[str, dict[str, object]]:
    return {
        str(item["id"]): item
        for item in list_inventory_payloads(session, profile_id=profile_id)
    }


def test_dynamic_holders_see_only_their_current_traveling_cups(cup_database) -> None:
    ids = build_cup_world(cup_database)
    with cup_database() as session:
        holder = inventory_by_id(session, ids["holder"])
        challenger = inventory_by_id(session, ids["challenger"])
        member = inventory_by_id(session, ids["member"])
        former = inventory_by_id(session, ids["former_member"])
        runner_member = inventory_by_id(session, ids["runner_member"])

        assert holder["punxsutawney-cup"] == {
            "id": "punxsutawney-cup",
            "item_key": "punxsutawney-cup",
            "name": "Punxsutawney Cup",
            "emoji": "🏆",
            "shelf": "earned",
            "acquisition_source": "traveling-cup",
            "purchase_price": None,
            "placement_x": None,
            "placement_y": None,
            "placement_size": "xlarge",
            "acquired_at": None,
        }
        assert "punxsutawney-cup" not in challenger
        assert member["coterie-cup"]["name"] == "Coterie Cup"
        assert member["coterie-cup"]["placement_size"] == "xlarge"
        assert "coterie-cup" not in former
        assert "coterie-cup" not in runner_member

        week = session.get(ContestWeek, ids["week"])
        challenger_profile = session.get(WoodchuckProfile, ids["challenger"])
        runner_team = session.get(Team, ids["runner_team"])
        add_medal(
            session, week=week, suffix="student-challenger-gold", medal="gold",
            profile=challenger_profile,
        )
        add_medal(
            session, week=week, suffix="team-runner-gold", medal="gold",
            team=runner_team,
        )
        session.commit()

        assert "punxsutawney-cup" in inventory_by_id(session, ids["challenger"])
        assert "punxsutawney-cup" in inventory_by_id(session, ids["holder"])
        assert "coterie-cup" in inventory_by_id(session, ids["member"])
        assert "coterie-cup" in inventory_by_id(session, ids["runner_member"])


def test_cup_placement_is_validated_and_prior_preference_returns_after_a_tie(
    cup_database,
) -> None:
    ids = build_cup_world(cup_database)
    holder_client = signed_client("WC-CUP-HOLDER")
    outsider_client = signed_client("WC-CUP-OUTSIDER")
    before = None
    with cup_database() as session:
        before = (
            session.scalar(select(func.count()).select_from(RewardGrant)),
            session.scalar(select(func.count()).select_from(CrownProgress)),
            session.scalar(select(func.count()).select_from(CrownAward)),
        )

    placed = holder_client.put(
        "/store/inventory/punxsutawney-cup/placement",
        json={"x": 0.2, "y": 0.3},
    )
    assert placed.status_code == 200
    assert placed.json()["item"]["placement_size"] == "xlarge"
    resized = holder_client.put(
        "/store/inventory/punxsutawney-cup/size", json={"size": "large"}
    )
    removed = holder_client.delete(
        "/store/inventory/punxsutawney-cup/placement"
    )
    assert resized.status_code == removed.status_code == 200
    assert resized.json()["item"]["placement_size"] == "large"
    assert removed.json()["item"]["placement_x"] is None
    replaced = holder_client.put(
        "/store/inventory/punxsutawney-cup/placement",
        json={"x": 0.4, "y": 0.6, "size": "xlarge"},
    )
    assert replaced.status_code == 200
    forged = outsider_client.put(
        "/store/inventory/punxsutawney-cup/placement",
        json={"x": 0.5, "y": 0.5, "size": "xlarge"},
    )
    assert forged.status_code == 404

    with cup_database() as session:
        week = session.get(ContestWeek, ids["week"])
        challenger = session.get(WoodchuckProfile, ids["challenger"])
        add_medal(
            session, week=week, suffix="challenger-gold-lead", medal="gold",
            profile=challenger,
        )
        add_medal(
            session, week=week, suffix="challenger-silver-lead", medal="silver",
            profile=challenger,
        )
        session.commit()
        assert "punxsutawney-cup" not in inventory_by_id(session, ids["holder"])
        preference = session.get(
            TravelingCupPlacement, (ids["holder"], "punxsutawney-cup")
        )
        assert (
            preference.placement_x,
            preference.placement_y,
            preference.placement_size,
        ) == (0.4, 0.6, "xlarge")

    denied_after_loss = holder_client.put(
        "/store/inventory/punxsutawney-cup/placement",
        json={"x": 0.8, "y": 0.8, "size": "xlarge"},
    )
    assert denied_after_loss.status_code == 404

    with cup_database() as session:
        week = session.get(ContestWeek, ids["week"])
        holder = session.get(WoodchuckProfile, ids["holder"])
        add_medal(
            session, week=week, suffix="holder-silver-retie", medal="silver",
            profile=holder,
        )
        session.commit()
        restored = inventory_by_id(session, ids["holder"])["punxsutawney-cup"]
        assert (restored["placement_x"], restored["placement_y"]) == (0.4, 0.6)
        assert restored["placement_size"] == "xlarge"
        assert before == (
            session.scalar(select(func.count()).select_from(RewardGrant)),
            session.scalar(select(func.count()).select_from(CrownProgress)),
            session.scalar(select(func.count()).select_from(CrownAward)),
        )


def test_coterie_cup_stays_a_team_entitlement_without_personal_history(
    cup_database,
) -> None:
    ids = build_cup_world(cup_database)
    former_client = signed_client("WC-CUP-FORMER")
    assert former_client.put(
        "/store/inventory/coterie-cup/placement",
        json={"x": 0.3, "y": 0.3},
    ).status_code == 404
    member_client = signed_client("WC-CUP-MEMBER")

    with cup_database() as session:
        before_student_results = session.scalar(
            select(func.count()).select_from(ContestResult).where(
                ContestResult.subject_type == "student",
                ContestResult.profile_id == ids["member"],
            )
        )
        hall = hall_of_champions_payload(session)
        assert [row["team_name"] for row in hall["traveling_cups"]["coterie"]["teams"]] == [
            "Cup Leaders"
        ]
        assert all(row["display_name"] != "Cup MEMBER" for row in hall["students"])
        before_grants = session.scalar(
            select(func.count()).select_from(RewardGrant)
        )

        placed = member_client.put(
            "/store/inventory/coterie-cup/placement",
            json={"x": 0.25, "y": 0.35},
        )
        assert placed.status_code == 200
        assert placed.json()["item"]["placement_size"] == "xlarge"
        assert session.scalar(
            select(func.count()).select_from(ContestResult).where(
                ContestResult.subject_type == "student",
                ContestResult.profile_id == ids["member"],
            )
        ) == before_student_results
        assert session.scalar(
            select(func.count()).select_from(RewardGrant)
        ) == before_grants
        assert session.scalar(select(func.count()).select_from(RewardGrant).where(
            RewardGrant.reward_type == "trophy"
        )) == 0


def test_traveling_cup_inventory_uses_existing_stickerbook_controls() -> None:
    javascript = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")
    wiring = javascript[
        javascript.index("  function wireShedDecorations() {"):
        javascript.index("  function wireShedSecret() {")
    ]
    assert '"traveling-cup": "Current traveling cup"' in wiring
    assert 'const PLACEMENT_SIZES = ["medium", "large", "xlarge"]' in wiring
    assert "data-decoration-size" in wiring
    assert "data-decoration-action" in wiring
