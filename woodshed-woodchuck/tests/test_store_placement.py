from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, store_routes
from app.db import Base
from app.main import app
from app.models import OwnedItemCopy, WoodchuckProfile
from app.security import hash_pin
from app.store_inventory import DECORATION_HITBOX_NORMALIZED


@pytest.fixture()
def placement_database(monkeypatch: pytest.MonkeyPatch):
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


def create_profile(factory, suffix: str) -> int:
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-PLACE-{suffix}",
            display_name=f"Decorator {suffix}",
            pin_hash=hash_pin("2468"),
            instrument="Flute",
            level="Beginner",
            goal="Practice",
        )
        session.add(profile)
        session.commit()
        return profile.id


def add_copy(factory, profile_id: int, item_key: str = "candle", *, x=None, y=None) -> int:
    with factory() as session:
        owned = OwnedItemCopy(
            profile_id=profile_id,
            item_key=item_key,
            acquisition_source="store",
            purchase_price=25,
            placement_x=x,
            placement_y=y,
            acquired_at=datetime.now(timezone.utc),
        )
        session.add(owned)
        session.commit()
        return owned.id


def signed_in_client(factory, suffix: str = "ONE") -> tuple[TestClient, int]:
    profile_id = create_profile(factory, suffix)
    client = TestClient(app)
    response = client.post(
        "/account/login",
        data={"woodchuck_id": f"WC-PLACE-{suffix}", "pin": "2468"},
    )
    assert response.status_code == 200
    return client, profile_id


def test_owned_unplaced_item_is_listed_with_null_placement(placement_database) -> None:
    client, profile_id = signed_in_client(placement_database)
    copy_id = add_copy(placement_database, profile_id)
    response = client.get("/store/inventory")
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": copy_id,
            "item_key": "candle",
            "name": "Candle",
            "emoji": "🕯️",
            "shelf": "gear",
            "acquisition_source": "store",
            "purchase_price": 25,
            "placement_x": None,
            "placement_y": None,
            "acquired_at": response.json()["items"][0]["acquired_at"],
        }
    ]


def test_placing_and_moving_update_the_same_owned_copy(placement_database) -> None:
    client, profile_id = signed_in_client(placement_database)
    copy_id = add_copy(placement_database, profile_id)
    placed = client.put(
        f"/store/inventory/{copy_id}/placement", json={"x": 0.25, "y": 0.35}
    )
    moved = client.put(
        f"/store/inventory/{copy_id}/placement", json={"x": 0.65, "y": 0.75}
    )
    assert placed.status_code == moved.status_code == 200
    assert placed.json()["item"]["placement_x"] == 0.25
    assert moved.json()["item"]["placement_x"] == 0.65
    assert moved.json()["item"]["placement_y"] == 0.75
    with placement_database() as session:
        assert session.scalar(select(func.count()).select_from(OwnedItemCopy)) == 1
        owned = session.get(OwnedItemCopy, copy_id)
        assert (owned.placement_x, owned.placement_y) == (0.65, 0.75)


def test_removing_clears_placement_without_deleting_ownership(placement_database) -> None:
    client, profile_id = signed_in_client(placement_database)
    copy_id = add_copy(placement_database, profile_id, x=0.2, y=0.3)
    response = client.delete(f"/store/inventory/{copy_id}/placement")
    assert response.status_code == 200
    assert response.json()["item"]["placement_x"] is None
    assert response.json()["item"]["placement_y"] is None
    with placement_database() as session:
        owned = session.get(OwnedItemCopy, copy_id)
        assert owned is not None
        assert owned.item_key == "candle"
        assert owned.placement_x is owned.placement_y is None


def test_duplicate_owned_copies_place_independently(placement_database) -> None:
    client, profile_id = signed_in_client(placement_database)
    first_id = add_copy(placement_database, profile_id)
    second_id = add_copy(placement_database, profile_id)
    first = client.put(f"/store/inventory/{first_id}/placement", json={"x": 0.2, "y": 0.4})
    second = client.put(f"/store/inventory/{second_id}/placement", json={"x": 0.4, "y": 0.4})
    assert first.status_code == second.status_code == 200
    inventory = client.get("/store/inventory").json()["items"]
    assert [(item["id"], item["placement_x"]) for item in inventory] == [
        (first_id, 0.2), (second_id, 0.4)
    ]


def test_cannot_move_or_remove_another_profiles_copy(placement_database) -> None:
    client, _profile_id = signed_in_client(placement_database)
    other_id = create_profile(placement_database, "OTHER")
    other_copy_id = add_copy(placement_database, other_id, x=0.3, y=0.3)
    moved = client.put(
        f"/store/inventory/{other_copy_id}/placement", json={"x": 0.6, "y": 0.6}
    )
    removed = client.delete(f"/store/inventory/{other_copy_id}/placement")
    assert moved.status_code == removed.status_code == 404
    with placement_database() as session:
        other = session.get(OwnedItemCopy, other_copy_id)
        assert (other.placement_x, other.placement_y) == (0.3, 0.3)


@pytest.mark.parametrize("payload", [
    {"x": -0.01, "y": 0.5},
    {"x": 1.01, "y": 0.5},
    {"x": 0.5, "y": -0.01},
    {"x": 0.5, "y": 1.01},
])
def test_coordinates_outside_normalized_range_are_rejected(placement_database, payload) -> None:
    client, profile_id = signed_in_client(placement_database)
    copy_id = add_copy(placement_database, profile_id)
    response = client.put(f"/store/inventory/{copy_id}/placement", json=payload)
    assert response.status_code == 422
    with placement_database() as session:
        owned = session.get(OwnedItemCopy, copy_id)
        assert owned.placement_x is owned.placement_y is None


def test_decoration_overlap_is_rejected_and_prior_position_is_preserved(placement_database) -> None:
    client, profile_id = signed_in_client(placement_database)
    fixed_id = add_copy(placement_database, profile_id, x=0.4, y=0.4)
    moving_id = add_copy(placement_database, profile_id, x=0.8, y=0.8)
    response = client.put(
        f"/store/inventory/{moving_id}/placement",
        json={"x": 0.4 + DECORATION_HITBOX_NORMALIZED / 2, "y": 0.4},
    )
    assert response.status_code == 409
    assert "overlaps another decoration" in response.json()["detail"]
    with placement_database() as session:
        assert (session.get(OwnedItemCopy, fixed_id).placement_x, session.get(OwnedItemCopy, fixed_id).placement_y) == (0.4, 0.4)
        moving = session.get(OwnedItemCopy, moving_id)
        assert (moving.placement_x, moving.placement_y) == (0.8, 0.8)


def test_normal_shed_controls_are_not_collision_participants(placement_database) -> None:
    client, profile_id = signed_in_client(placement_database)
    copy_id = add_copy(placement_database, profile_id)
    response = client.put(
        f"/store/inventory/{copy_id}/placement", json={"x": 0.0, "y": 0.0}
    )
    assert response.status_code == 200
    assert response.json()["item"]["placement_x"] == 0.0


def test_placement_endpoints_require_authentication(placement_database) -> None:
    profile_id = create_profile(placement_database, "AUTH")
    copy_id = add_copy(placement_database, profile_id)
    client = TestClient(app)
    assert client.get("/store/inventory").status_code == 401
    assert client.put(
        f"/store/inventory/{copy_id}/placement", json={"x": 0.2, "y": 0.2}
    ).status_code == 401
    assert client.delete(f"/store/inventory/{copy_id}/placement").status_code == 401
