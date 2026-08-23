from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, store_routes
from app.db import Base
from app.main import app
from app.models import OwnedItemCopy, WoodchuckProfile, WoodchuckState
from app.security import hash_pin
from app.store_catalog import ALL_ITEMS, catalog_for_date, catalog_payload
from app.store_inventory import grant_owned_item


@pytest.fixture()
def store_database(monkeypatch: pytest.MonkeyPatch):
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


def create_student(factory, *, credits: int = 100) -> int:
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-STORE-TEST",
            display_name="Shopper",
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
            revision=3,
        ))
        session.commit()
        return profile.id


def signed_in_client(factory, *, credits: int = 100) -> tuple[TestClient, int]:
    profile_id = create_student(factory, credits=credits)
    client = TestClient(app)
    response = client.post(
        "/account/login",
        data={"woodchuck_id": "WC-STORE-TEST", "pin": "2468"},
    )
    assert response.status_code == 200
    return client, profile_id


def test_catalog_has_locked_shelves_items_and_prices() -> None:
    payload = catalog_payload(now=datetime(2026, 8, 13, 18, tzinfo=timezone.utc))
    gear = payload["shelves"]["gear"]
    buddies = payload["shelves"]["little_buddy"]

    assert [(item["item_key"], item["emoji"], item["price"]) for item in gear[:3]] == [
        ("candle", "🕯️", 25),
        ("fruit", "🍎", 50),
        ("ice-cream", "🍦", 75),
    ]
    assert [(item["item_key"], item["emoji"], item["price"]) for item in buddies[:3]] == [
        ("ladybug", "🐞", 25),
        ("caterpillar", "🐛", 50),
        ("snail", "🐌", 75),
    ]
    assert len(gear) == 5
    assert len(buddies) == 4
    assert (gear[3]["item_key"], gear[3]["emoji"], gear[3]["price"]) == (
        "ufo", "🛸", 1000
    )
    assert gear[-1]["rotating"] is True and gear[-1]["price"] == 100
    assert buddies[-1]["rotating"] is True and buddies[-1]["price"] == 100
    assert not any("instrument" in item.name.casefold() for item in ALL_ITEMS.values())


def test_ufo_purchase_uses_existing_balance_and_duplicate_copy_rules(store_database) -> None:
    client, profile_id = signed_in_client(store_database, credits=2500)
    first = client.post("/store/purchases", json={"item_key": "ufo"})
    second = client.post("/store/purchases", json={"item_key": "ufo"})
    insufficient = client.post("/store/purchases", json={"item_key": "ufo"})

    assert first.status_code == second.status_code == 201
    assert first.json()["item"]["purchase_price"] == 1000
    assert first.json()["item"]["id"] != second.json()["item"]["id"]
    assert second.json()["dandelion_balance"] == 501
    assert insufficient.status_code == 409
    with store_database() as session:
        copies = session.scalars(select(OwnedItemCopy).where(
            OwnedItemCopy.profile_id == profile_id,
            OwnedItemCopy.item_key == "ufo",
        )).all()
        assert len(copies) == 2
        assert session.get(WoodchuckState, profile_id).state_json["progress"]["credits"] == 501


def test_daily_rotation_is_deterministic_and_central_date_owned() -> None:
    first = catalog_for_date(date(2026, 8, 13))
    repeat = catalog_for_date(date(2026, 8, 13))
    next_day = catalog_for_date(date(2026, 8, 14))
    assert first == repeat
    assert first["gear"][-1] != next_day["gear"][-1]
    assert first["little_buddy"][-1] != next_day["little_buddy"][-1]

    before_midnight = catalog_payload(
        now=datetime(2026, 8, 14, 4, 59, tzinfo=timezone.utc)
    )
    after_midnight = catalog_payload(
        now=datetime(2026, 8, 14, 5, 1, tzinfo=timezone.utc)
    )
    assert before_midnight["activity_date"] == "2026-08-13"
    assert after_midnight["activity_date"] == "2026-08-14"
    assert before_midnight["timezone"] == "America/Chicago"


def test_purchase_deducts_authoritative_balance_and_creates_one_copy(store_database) -> None:
    client, profile_id = signed_in_client(store_database, credits=80)
    response = client.post("/store/purchases", json={"item_key": "candle"})
    assert response.status_code == 201
    assert response.json()["dandelion_balance"] == 56
    assert response.json()["item"]["purchase_price"] == 25
    assert response.json()["item"]["placement_x"] is None
    assert response.json()["item"]["placement_y"] is None

    with store_database() as session:
        state = session.get(WoodchuckState, profile_id)
        owned = session.scalar(select(OwnedItemCopy))
        assert state.state_json["progress"]["credits"] == 56
        assert state.revision == 5
        assert owned.item_key == "candle"
        assert owned.acquisition_source == "store"


def test_duplicate_purchases_create_distinct_owned_copy_rows(store_database) -> None:
    client, profile_id = signed_in_client(store_database, credits=75)
    first = client.post("/store/purchases", json={"item_key": "candle"})
    second = client.post("/store/purchases", json={"item_key": "candle"})
    assert first.status_code == second.status_code == 201
    assert first.json()["item"]["id"] != second.json()["item"]["id"]
    assert second.json()["dandelion_balance"] == 26

    with store_database() as session:
        copies = session.scalars(select(OwnedItemCopy).where(
            OwnedItemCopy.profile_id == profile_id,
            OwnedItemCopy.item_key == "candle",
        )).all()
        assert len(copies) == 2


def test_insufficient_balance_changes_nothing(store_database) -> None:
    client, profile_id = signed_in_client(store_database, credits=23)
    response = client.post("/store/purchases", json={"item_key": "candle"})
    assert response.status_code == 409
    with store_database() as session:
        assert session.get(WoodchuckState, profile_id).state_json["progress"]["credits"] == 24
        assert session.scalar(select(func.count()).select_from(OwnedItemCopy)) == 0


def test_owned_copy_failure_rolls_back_balance_atomically(store_database) -> None:
    client, profile_id = signed_in_client(store_database, credits=80)

    def reject_copy(*_args, **_kwargs):
        raise RuntimeError("simulated owned-copy failure")

    event.listen(OwnedItemCopy, "before_insert", reject_copy)
    try:
        failing_client = TestClient(app, raise_server_exceptions=False)
        failing_client.cookies.update(client.cookies)
        response = failing_client.post("/store/purchases", json={"item_key": "candle"})
        assert response.status_code == 500
    finally:
        event.remove(OwnedItemCopy, "before_insert", reject_copy)

    with store_database() as session:
        state = session.get(WoodchuckState, profile_id)
        assert state.state_json["progress"]["credits"] == 81
        assert state.revision == 4
        assert session.scalar(select(func.count()).select_from(OwnedItemCopy)) == 0


def test_inventory_lists_each_copy_and_only_the_signed_in_profile(store_database) -> None:
    client, profile_id = signed_in_client(store_database, credits=100)
    client.post("/store/purchases", json={"item_key": "candle"})
    client.post("/store/purchases", json={"item_key": "fruit"})
    with store_database() as session:
        other = WoodchuckProfile(
            woodchuck_id="WC-OTHER-STORE", display_name="Other",
            pin_hash=hash_pin("1357"), instrument="Tuba",
            level="Beginner", goal="Practice",
        )
        session.add(other); session.flush()
        session.add(OwnedItemCopy(
            profile_id=other.id, item_key="snail", acquisition_source="store",
            purchase_price=75, acquired_at=datetime.now(timezone.utc),
        ))
        session.commit()

    response = client.get("/store/inventory")
    assert response.status_code == 200
    assert [item["item_key"] for item in response.json()["items"]] == ["candle", "fruit"]
    assert all(item["id"] for item in response.json()["items"])
    assert profile_id


def test_authenticated_store_and_both_shelves_load_with_inventory(
    store_database,
) -> None:
    client, _profile_id = signed_in_client(store_database, credits=100)
    assert client.post("/store/purchases", json={"item_key": "candle"}).status_code == 201

    page = client.get("/store")
    catalog = client.get("/store/catalog")
    inventory = client.get("/store/inventory")

    assert page.status_code == catalog.status_code == inventory.status_code == 200
    shelves = catalog.json()["shelves"]
    assert len(shelves["gear"]) == 5
    assert len(shelves["little_buddy"]) == 4
    assert {item["item_key"] for item in shelves["gear"]} >= {
        "candle", "fruit", "ice-cream", "ufo",
    }
    assert {item["item_key"] for item in shelves["little_buddy"]} >= {
        "ladybug", "caterpillar", "snail",
    }
    assert [item["item_key"] for item in inventory.json()["items"]] == ["candle"]


def test_mum_free_grant_is_idempotent_and_never_deducts_balance(store_database) -> None:
    profile_id = create_student(store_database, credits=12)
    with store_database() as session:
        first, created = grant_owned_item(
            session, profile_id=profile_id, item_key="fruit",
            grant_key="mum-fruit:2026-08-10",
        )
        retry, retry_created = grant_owned_item(
            session, profile_id=profile_id, item_key="fruit",
            grant_key="mum-fruit:2026-08-10",
        )
        session.commit()
        assert created is True and retry_created is False
        assert first.id == retry.id

    with store_database() as session:
        state = session.get(WoodchuckState, profile_id)
        item = session.scalar(select(OwnedItemCopy))
        assert state.state_json["progress"]["credits"] == 12
        assert state.revision == 3
        assert item.acquisition_source == "mum"
        assert item.purchase_price is None


def test_prices_are_not_client_authority_and_auth_is_required(store_database) -> None:
    assert TestClient(app).get("/store/catalog").status_code == 200
    assert TestClient(app).get("/store/inventory").status_code == 401
    assert TestClient(app).post(
        "/store/purchases", json={"item_key": "candle"}
    ).status_code == 401

    client, _profile_id = signed_in_client(store_database, credits=100)
    response = client.post(
        "/store/purchases",
        json={"item_key": "candle", "price": 1},
    )
    assert response.status_code == 422
    valid = client.post("/store/purchases", json={"item_key": "candle"})
    assert valid.status_code == 201
    assert valid.json()["item"]["purchase_price"] == 25
