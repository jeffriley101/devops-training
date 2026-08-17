from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, store_routes
from app.db import Base
from app.main import app
from app.models import (
    CrownAward,
    CrownProgress,
    OwnedItemCopy,
    RewardInventoryPlacement,
    WoodchuckProfile,
    WoodchuckState,
)
from app.security import hash_pin
from app.store_inventory import claim_weekly_mum_snack


@pytest.fixture()
def bridge_database(monkeypatch: pytest.MonkeyPatch):
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


def add_profile(factory, suffix: str, *, credits: int = 50) -> int:
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-BRIDGE-{suffix}",
            display_name=f"Bridge {suffix}",
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
            revision=1,
        ))
        session.commit()
        return profile.id


def signed_client(factory, suffix: str = "ONE") -> tuple[TestClient, int]:
    profile_id = add_profile(factory, suffix)
    client = TestClient(app)
    response = client.post(
        "/account/login",
        data={"woodchuck_id": f"WC-BRIDGE-{suffix}", "pin": "2468"},
    )
    assert response.status_code == 200
    return client, profile_id


def earn_crown(factory, profile_id: int, category_key: str = "trivia") -> int:
    with factory() as session:
        ordinal = session.scalar(select(func.count()).select_from(CrownAward).where(
            CrownAward.profile_id == profile_id,
            CrownAward.category_key == category_key,
        )) or 0
        award = CrownAward(
            profile_id=profile_id,
            category_key=category_key,
            source_key=f"test-crown:{category_key}:{ordinal + 1}",
            earned_at=datetime(2026, 8, 10, 18, tzinfo=timezone.utc),
        )
        session.add(award)
        session.flush()
        award_id = award.id
        session.commit()
        return award_id


def test_earned_crowns_are_virtual_inventory_without_store_copies(bridge_database) -> None:
    client, profile_id = signed_client(bridge_database)
    trivia_id = earn_crown(bridge_database, profile_id, "trivia")
    team_id = earn_crown(bridge_database, profile_id, "team-crown")

    response = client.get("/store/inventory")
    assert response.status_code == 200
    crowns = [item for item in response.json()["items"] if item["acquisition_source"] == "crown"]
    assert [(item["id"], item["name"], item["emoji"]) for item in crowns] == [
        (f"crown:{trivia_id}", "Trivia Crown", "👑"),
        (f"crown:{team_id}", "Team Crown", "👑"),
    ]
    with bridge_database() as session:
        assert session.scalar(select(func.count()).select_from(OwnedItemCopy)) == 0


def test_crown_placement_and_return_never_change_crown_ownership(bridge_database) -> None:
    client, profile_id = signed_client(bridge_database)
    award_id = earn_crown(bridge_database, profile_id)

    placed = client.put(
        f"/store/inventory/crown:{award_id}/placement", json={"x": 0.2, "y": 0.3}
    )
    returned = client.delete(f"/store/inventory/crown:{award_id}/placement")
    assert placed.status_code == returned.status_code == 200
    assert placed.json()["item"]["placement_x"] == 0.2
    assert returned.json()["item"]["placement_x"] is None
    with bridge_database() as session:
        crown = session.get(CrownAward, award_id)
        assert crown is not None and crown.earned_at is not None
        assert session.scalar(select(func.count()).select_from(RewardInventoryPlacement)) == 0


def test_same_category_crowns_are_independent_inventory_copies(bridge_database) -> None:
    client, profile_id = signed_client(bridge_database)
    first_id = earn_crown(bridge_database, profile_id, "trivia")
    second_id = earn_crown(bridge_database, profile_id, "trivia")

    crowns = [
        item for item in client.get("/store/inventory").json()["items"]
        if item["item_key"] == "crown:trivia"
    ]
    assert [item["id"] for item in crowns] == [
        f"crown:{first_id}",
        f"crown:{second_id}",
    ]
    assert client.put(
        f"/store/inventory/crown:{first_id}/placement",
        json={"x": 0.2, "y": 0.3},
    ).status_code == 200
    assert client.put(
        f"/store/inventory/crown:{second_id}/placement",
        json={"x": 0.5, "y": 0.6},
    ).status_code == 200
    returned = client.delete(f"/store/inventory/crown:{first_id}/placement")
    assert returned.status_code == 200

    inventory = {
        item["id"]: item for item in client.get("/store/inventory").json()["items"]
        if item["acquisition_source"] == "crown"
    }
    assert inventory[f"crown:{first_id}"]["placement_x"] is None
    assert inventory[f"crown:{second_id}"]["placement_x"] == 0.5
    with bridge_database() as session:
        assert session.scalar(select(func.count()).select_from(CrownAward)) == 2
        assert session.scalar(
            select(func.count()).select_from(RewardInventoryPlacement)
        ) == 1
        assert session.scalar(select(func.count()).select_from(OwnedItemCopy)) == 0


def test_crown_and_owned_copy_share_collision_protection(bridge_database) -> None:
    client, profile_id = signed_client(bridge_database)
    award_id = earn_crown(bridge_database, profile_id)
    purchase = client.post("/store/purchases", json={"item_key": "candle"})
    assert purchase.status_code == 201
    copy_id = purchase.json()["item"]["id"]
    assert client.put(
        f"/store/inventory/{copy_id}/placement", json={"x": 0.4, "y": 0.4}
    ).status_code == 200

    collision = client.put(
        f"/store/inventory/crown:{award_id}/placement", json={"x": 0.45, "y": 0.4}
    )
    assert collision.status_code == 409
    assert "overlaps another decoration" in collision.json()["detail"]


def test_crown_and_snack_cannot_be_placed_by_another_profile(bridge_database) -> None:
    client, profile_id = signed_client(bridge_database)
    award_id = earn_crown(bridge_database, profile_id)
    with bridge_database() as session:
        claim_weekly_mum_snack(
            session,
            profile_id=profile_id,
            item_key="mum-apple",
            now=datetime(2026, 8, 10, 18, tzinfo=timezone.utc),
        )
        snack = session.scalar(select(OwnedItemCopy))
        session.commit()

    other_client, _ = signed_client(bridge_database, "TWO")
    assert other_client.put(
        f"/store/inventory/crown:{award_id}/placement", json={"x": 0.2, "y": 0.2}
    ).status_code == 404
    assert other_client.put(
        f"/store/inventory/{snack.id}/placement", json={"x": 0.4, "y": 0.4}
    ).status_code == 404


def test_mum_snack_is_free_weekly_and_idempotent_in_central_time(bridge_database) -> None:
    profile_id = add_profile(bridge_database, "SNACK", credits=12)
    with bridge_database() as session:
        first, created, first_week = claim_weekly_mum_snack(
            session,
            profile_id=profile_id,
            item_key="mum-apple",
            now=datetime(2026, 8, 10, 5, tzinfo=timezone.utc),
        )
        retry, retry_created, retry_week = claim_weekly_mum_snack(
            session,
            profile_id=profile_id,
            item_key="mum-banana",
            now=datetime(2026, 8, 16, 23, tzinfo=timezone.utc),
        )
        next_week, next_created, later_week = claim_weekly_mum_snack(
            session,
            profile_id=profile_id,
            item_key="mum-banana",
            now=datetime(2026, 8, 17, 5, tzinfo=timezone.utc),
        )
        session.commit()
        assert created is True and retry_created is False and next_created is True
        assert first.id == retry.id
        assert first.item_key == "mum-apple"
        assert next_week.item_key == "mum-banana"
        assert first_week == retry_week
        assert later_week > first_week

    with bridge_database() as session:
        assert session.get(WoodchuckState, profile_id).state_json["progress"]["credits"] == 12
        snacks = session.scalars(select(OwnedItemCopy).order_by(OwnedItemCopy.id)).all()
        assert [item.item_key for item in snacks] == ["mum-apple", "mum-banana"]
        assert all(item.purchase_price is None and item.acquisition_source == "mum" for item in snacks)


def test_snack_endpoint_accepts_only_approved_choices_and_merges_inventory(bridge_database) -> None:
    client, profile_id = signed_client(bridge_database)
    earn_crown(bridge_database, profile_id)
    purchase = client.post("/store/purchases", json={"item_key": "candle"})
    assert purchase.status_code == 201
    invalid = client.post("/store/mum/snacks", json={"item_key": "candle"})
    client_authority = client.post(
        "/store/mum/snacks",
        json={
            "item_key": "mum-apple",
            "profile_id": profile_id,
            "week_start": "2026-08-10",
            "price": 0,
            "quantity": 2,
        },
    )
    claimed = client.post("/store/mum/snacks", json={"item_key": "mum-cookie"})
    retry = client.post("/store/mum/snacks", json={"item_key": "mum-cookie"})
    assert invalid.status_code == 400
    assert client_authority.status_code == 422
    assert claimed.status_code == retry.status_code == 200
    assert claimed.json()["created"] is True
    assert retry.json()["created"] is False

    items = client.get("/store/inventory").json()["items"]
    assert {item["acquisition_source"] for item in items} == {"crown", "mum", "store"}
    assert {item["item_key"] for item in items} == {"candle", "crown:trivia", "mum-cookie"}
    assert TestClient(app).post("/store/mum/snacks", json={"item_key": "mum-apple"}).status_code == 401



def test_mum_snack_chooser_lists_only_the_approved_foods() -> None:
    home = (Path(__file__).resolve().parents[1] / "templates" / "home.html").read_text()
    assert 'id="mum-snack-chooser"' in home
    assert [
        key for key in (
            "mum-apple",
            "mum-banana",
            "mum-cookie",
            "mum-pretzel",
            "mum-strawberry",
            "mum-cheese",
            "mum-watermelon",
            "mum-popcorn",
        ) if f'data-mum-snack-item="{key}"' in home
    ] == [
        "mum-apple", "mum-banana", "mum-cookie", "mum-pretzel",
        "mum-strawberry", "mum-cheese", "mum-watermelon", "mum-popcorn",
    ]


def test_shed_inventory_wiring_supports_virtual_crown_ids_and_snack_refresh() -> None:
    javascript = (Path(__file__).resolve().parents[1] / "static" / "js" / "app.js").read_text()
    start = javascript.index("  function wireShedDecorations() {")
    end = javascript.index("  function wireShedSecret() {", start)
    decorations = javascript[start:end]
    assert "const itemId = (item) => String(item.id);" in decorations
    assert "woodshed:inventory-changed" in decorations
    mum_start = javascript.index("  function wireMum(state) {")
    mum_end = javascript.index("  function wireMetronome() {", mum_start)
    mum = javascript[mum_start:mum_end]
    assert 'fetch("/store/mum/snacks"' in mum
    assert "item_key: button.dataset.mumSnackItem" in mum
    assert "payload.created" in mum
