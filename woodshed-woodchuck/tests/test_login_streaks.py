from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, store_routes
from app.db import Base
from app.login_streaks import (
    LOGIN_STREAK_CROWN_CATEGORY,
    apply_daily_login,
)
from app.main import app
from app.models import (
    CrownAward,
    LoginStreak,
    OwnedItemCopy,
    RewardGrant,
    RewardInventoryPlacement,
    WoodchuckProfile,
    WoodchuckState,
)
from app.security import hash_pin


@pytest.fixture()
def streak_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(store_routes, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_profile(factory, suffix: str = "ONE", *, credits: int = 10) -> int:
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-STREAK-{suffix}",
            display_name=f"Streak {suffix}",
            pin_hash=hash_pin("2468"),
            instrument="Flute",
            level="Beginner",
            goal="Practice",
        )
        session.add(profile)
        session.flush()
        session.add(WoodchuckState(
            profile_id=profile.id,
            state_json={"progress": {"credits": credits}},
            revision=0,
        ))
        session.commit()
        return profile.id


def apply_on(factory, profile_id: int, instant: datetime) -> dict[str, object]:
    with factory() as session:
        payload = apply_daily_login(
            session,
            profile_id=profile_id,
            now=instant,
        )
        session.commit()
        return payload


def test_first_login_route_creates_day_one_and_awards_one_dandelion(
    streak_database,
) -> None:
    profile_id = add_profile(streak_database)
    response = TestClient(app).post(
        "/account/login",
        data={"woodchuck_id": "WC-STREAK-ONE", "pin": "2468"},
    )

    assert response.status_code == 200
    payload = response.json()["login_streak"]
    assert payload["current_streak"] == 1
    assert payload["awarded_today"] is True
    assert payload["dandelions_awarded"] == 1
    assert payload["dandelion_balance"] == 11
    with streak_database() as session:
        assert session.get(LoginStreak, profile_id).current_days == 1
        assert session.get(WoodchuckState, profile_id).state_json["progress"]["credits"] == 11


def test_consecutive_same_day_and_missed_day_rules(streak_database) -> None:
    profile_id = add_profile(streak_database)
    first = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)

    day_one = apply_on(streak_database, profile_id, first)
    same_day = apply_on(streak_database, profile_id, first + timedelta(hours=4))
    day_two = apply_on(streak_database, profile_id, first + timedelta(days=1))
    reset = apply_on(streak_database, profile_id, first + timedelta(days=3))

    assert (day_one["current_streak"], day_one["dandelions_awarded"]) == (1, 1)
    assert same_day["current_streak"] == 1
    assert same_day["awarded_today"] is False
    assert same_day["dandelions_awarded"] == 0
    assert (day_two["current_streak"], day_two["dandelions_awarded"]) == (2, 2)
    assert (reset["current_streak"], reset["dandelions_awarded"]) == (1, 1)
    with streak_database() as session:
        grants = session.scalars(select(RewardGrant).where(
            RewardGrant.profile_id == profile_id,
            RewardGrant.category_key == "login-streak",
        )).all()
        assert [grant.amount for grant in grants] == [1, 2, 1]


def test_day_seven_and_fourteen_create_distinct_permanent_crowns(
    streak_database,
) -> None:
    profile_id = add_profile(streak_database, credits=0)
    first = datetime(2026, 7, 27, 17, tzinfo=timezone.utc)
    payloads = [
        apply_on(streak_database, profile_id, first + timedelta(days=offset))
        for offset in range(14)
    ]

    assert payloads[6]["current_streak"] == 7
    assert payloads[6]["crown_awarded"] is True
    assert payloads[13]["current_streak"] == 14
    assert payloads[13]["crown_awarded"] is True
    with streak_database() as session:
        awards = session.scalars(select(CrownAward).where(
            CrownAward.profile_id == profile_id,
            CrownAward.category_key == LOGIN_STREAK_CROWN_CATEGORY,
        ).order_by(CrownAward.id)).all()
        crown_grants = session.scalars(select(RewardGrant).where(
            RewardGrant.profile_id == profile_id,
            RewardGrant.reward_type == "crown_win",
            RewardGrant.category_key == LOGIN_STREAK_CROWN_CATEGORY,
        )).all()
        assert len(awards) == len(crown_grants) == 2
        assert awards[0].source_key != awards[1].source_key
        assert session.get(WoodchuckState, profile_id).state_json["progress"]["credits"] == 105


def test_crowns_survive_reset_and_daily_retries_are_idempotent(streak_database) -> None:
    profile_id = add_profile(streak_database, credits=0)
    first = datetime(2026, 8, 3, 17, tzinfo=timezone.utc)
    for offset in range(7):
        apply_on(streak_database, profile_id, first + timedelta(days=offset))

    retry = apply_on(streak_database, profile_id, first + timedelta(days=6, hours=3))
    reset = apply_on(streak_database, profile_id, first + timedelta(days=9))

    assert retry["awarded_today"] is False
    assert reset["current_streak"] == 1
    assert reset["crowns_earned"] == 1
    with streak_database() as session:
        assert session.scalar(select(func.count()).select_from(CrownAward).where(
            CrownAward.profile_id == profile_id,
            CrownAward.category_key == LOGIN_STREAK_CROWN_CATEGORY,
        )) == 1
        assert session.scalar(select(func.count()).select_from(RewardGrant).where(
            RewardGrant.profile_id == profile_id,
            RewardGrant.source_key == "login-streak:2026-08-09",
            RewardGrant.reward_type == "dandelion",
        )) == 1


def test_two_authenticated_devices_do_not_double_award(streak_database) -> None:
    profile_id = add_profile(streak_database, "DEVICES", credits=0)
    first = TestClient(app)
    second = TestClient(app)

    first_login = first.post(
        "/account/login",
        data={"woodchuck_id": "WC-STREAK-DEVICES", "pin": "2468"},
    )
    second_login = second.post(
        "/account/login",
        data={"woodchuck_id": "WC-STREAK-DEVICES", "pin": "2468"},
    )
    refresh = second.post("/account/login-streak")

    assert first_login.json()["login_streak"]["awarded_today"] is True
    assert second_login.json()["login_streak"]["awarded_today"] is False
    assert refresh.json()["awarded_today"] is False
    with streak_database() as session:
        assert session.get(WoodchuckState, profile_id).state_json["progress"]["credits"] == 1
        assert session.scalar(select(func.count()).select_from(RewardGrant).where(
            RewardGrant.profile_id == profile_id,
            RewardGrant.category_key == "login-streak",
        )) == 1


def test_america_chicago_midnight_is_the_calendar_boundary(streak_database) -> None:
    profile_id = add_profile(streak_database, credits=0)
    before_midnight = datetime(2026, 8, 24, 4, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 8, 24, 5, 1, tzinfo=timezone.utc)

    first = apply_on(streak_database, profile_id, before_midnight)
    second = apply_on(streak_database, profile_id, after_midnight)

    assert first["last_login_date"] == "2026-08-23"
    assert second["last_login_date"] == "2026-08-24"
    assert second["current_streak"] == 2
    assert second["dandelions_awarded"] == 2


def test_weekly_streak_crown_uses_stickerbook_and_shed_placement(
    streak_database,
) -> None:
    profile_id = add_profile(streak_database, "STICKER", credits=0)
    first = datetime(2026, 8, 3, 17, tzinfo=timezone.utc)
    for offset in range(7):
        apply_on(streak_database, profile_id, first + timedelta(days=offset))

    client = TestClient(app)
    assert client.post(
        "/account/login",
        data={"woodchuck_id": "WC-STREAK-STICKER", "pin": "2468"},
    ).status_code == 200
    inventory = client.get("/store/inventory").json()["items"]
    crown = next(item for item in inventory if item["item_key"] == "crown:weekly-login-streak")
    assert crown["name"] == "Weekly Streak Crown"
    assert crown["emoji"] == "👑"
    assert crown["placement_size"] == "small"

    placed = client.put(
        f"/store/inventory/{crown['id']}/placement",
        json={"x": 0.3, "y": 0.35, "size": "large"},
    )
    returned = client.delete(f"/store/inventory/{crown['id']}/placement")
    assert placed.status_code == returned.status_code == 200
    assert placed.json()["item"]["placement_size"] == "large"
    assert returned.json()["item"]["placement_x"] is None
    with streak_database() as session:
        assert session.scalar(select(func.count()).select_from(CrownAward).where(
            CrownAward.profile_id == profile_id,
        )) == 1
        assert session.scalar(select(func.count()).select_from(OwnedItemCopy)) == 0
        assert session.scalar(
            select(func.count()).select_from(RewardInventoryPlacement)
        ) == 1
        placement = session.scalar(select(RewardInventoryPlacement))
        assert placement.placement_x is placement.placement_y is None
        assert placement.placement_size == "large"


def test_login_streak_endpoint_requires_authentication() -> None:
    assert TestClient(app).post("/account/login-streak").status_code == 401
