"""Purchase-only first-party policy; never infer CSRF safety from JSON/CORS."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, main, session_revocations, store_routes
from app.age_privacy import declare_age
from app.db import Base
from app.models import OwnedItemCopy, RewardGrant, WoodchuckProfile, WoodchuckState
from app.security import hash_pin


@pytest.fixture
def purchase_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    for module in (main, account_routes, session_revocations, store_routes):
        monkeypatch.setattr(module, "SessionLocal", factory)
    pin_hash = hash_pin("2468")
    with factory() as session:
        for label in ("A", "B"):
            profile = WoodchuckProfile(woodchuck_id=f"WC-ORIGIN-{label}",
                display_name=f"Synthetic {label}", pin_hash=pin_hash,
                instrument="Flute", level="Beginner", goal="Practice")
            session.add(profile)
            session.flush()
            declare_age(session, profile.id, "adult")
            session.add(WoodchuckState(profile_id=profile.id, revision=3,
                state_json={"progress": {"credits": 100}}))
        session.commit()
    yield factory
    engine.dispose()


def snapshot(factory):
    with factory() as session:
        return deepcopy({model.__tablename__: [dict(row) for row in
            session.execute(select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]
            for model in (WoodchuckState, OwnedItemCopy, RewardGrant)})


def signed_client(base_url="http://127.0.0.1:8000"):
    client = TestClient(main.app, base_url=base_url)
    response = client.post("/account/login",
        data={"woodchuck_id": "WC-ORIGIN-A", "pin": "2468"})
    assert response.status_code == 200, response.text
    return client


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8000", "https://woodshed.example.test"])
def test_same_origin_purchase_debits_once_and_only_authenticated_owner(purchase_db, base_url):
    with signed_client(base_url) as client:
        before = snapshot(purchase_db)
        response = client.post("/store/purchases", json={"item_key": "candle"})
        assert response.status_code == 201, response.text
        after = snapshot(purchase_db)
        assert len(after["owned_item_copies"]) == 1
        item = after["owned_item_copies"][0]
        assert item["profile_id"] == 1 and item["purchase_price"] == 25
        a_before, b_before = before["woodchuck_states"]
        a_after, b_after = after["woodchuck_states"]
        assert a_after["state_json"]["progress"]["credits"] == a_before["state_json"]["progress"]["credits"] - 25
        assert a_after["revision"] == a_before["revision"] + 1
        assert b_after == b_before
        assert after["reward_grants"] == before["reward_grants"]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://foreign.example.test"},
    {"Origin": "http://127.0.0.1:8001", "Sec-Fetch-Site": "same-site"},
    {"Origin": "http://127.0.0.1:8000.evil.test"},
    {"Origin": "invalid"},
    {"Origin": ""},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "cross-site"},
])
def test_rejected_origins_and_retries_never_debit_or_grant(purchase_db, headers):
    with signed_client() as client:
        before = snapshot(purchase_db)
        for _ in range(2):
            response = client.post("/store/purchases", json={"item_key": "candle"}, headers=headers)
            assert response.status_code == 403, response.text
            assert response.headers["cache-control"] == "no-store"
            assert snapshot(purchase_db) == before


def test_absent_origin_is_rejected_before_opening_purchase_session(purchase_db, monkeypatch):
    with signed_client() as client:
        client.headers.pop("Origin")
        before = snapshot(purchase_db)
        def unexpected_session():
            pytest.fail("Origin rejection must precede the purchase database session")
        monkeypatch.setattr(store_routes, "SessionLocal", unexpected_session)
        response = client.post("/store/purchases", json={"item_key": "candle"})
        assert "origin" not in response.request.headers
        assert response.status_code == 403
        assert snapshot(purchase_db) == before


def test_no_content_type_foreign_json_body_is_rejected(purchase_db):
    with signed_client() as client:
        before = snapshot(purchase_db)
        response = client.post("/store/purchases", content=b'{"item_key":"candle"}',
            headers={"Origin": "http://127.0.0.1:8001", "Sec-Fetch-Site": "same-site"})
        assert "content-type" not in response.request.headers
        assert response.status_code == 403
        assert snapshot(purchase_db) == before


def test_opaque_origin_uses_existing_same_origin_document_policy(purchase_db):
    with signed_client() as client:
        response = client.post("/store/purchases", json={"item_key": "candle"},
            headers={"Origin": "null", "Sec-Fetch-Site": "same-origin",
                     "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
        assert response.status_code == 201


def test_retry_after_rolled_back_purchase_debits_only_successful_attempt(purchase_db):
    with signed_client() as authenticated:
        with TestClient(main.app, base_url="http://127.0.0.1:8000",
                        raise_server_exceptions=False) as client:
            client.cookies.update(authenticated.cookies)
            before = snapshot(purchase_db)
            def reject_copy(*_args, **_kwargs):
                raise RuntimeError("synthetic owned-copy insertion failure")
            event.listen(OwnedItemCopy, "before_insert", reject_copy)
            try:
                assert client.post("/store/purchases", json={"item_key": "candle"}).status_code == 500
            finally:
                event.remove(OwnedItemCopy, "before_insert", reject_copy)
            assert snapshot(purchase_db) == before
            response = client.post("/store/purchases", json={"item_key": "candle"})
            assert response.status_code == 201
            after = snapshot(purchase_db)
            assert len(after["owned_item_copies"]) == 1
            assert after["woodchuck_states"][0]["state_json"]["progress"]["credits"] == before["woodchuck_states"][0]["state_json"]["progress"]["credits"] - 25
            assert after["woodchuck_states"][0]["revision"] == before["woodchuck_states"][0]["revision"] + 1
            assert after["woodchuck_states"][1] == before["woodchuck_states"][1]


def test_wrong_account_header_and_forged_owner_cannot_charge(purchase_db):
    with signed_client() as client:
        before = snapshot(purchase_db)
        response = client.post("/store/purchases", json={"item_key": "candle"},
            headers={"X-Woodshed-Account": "WC-ORIGIN-B"})
        assert response.status_code == 401
        response = client.post("/store/purchases", json={"item_key": "candle", "profile_id": 2})
        assert response.status_code == 422
        assert snapshot(purchase_db) == before


def test_stale_session_and_logout_cannot_purchase(purchase_db):
    with signed_client() as client:
        with purchase_db() as session:
            session.get(WoodchuckProfile, 1).session_version += 1
            session.commit()
        before = snapshot(purchase_db)
        assert client.post("/store/purchases", json={"item_key": "candle"}).status_code == 401
        assert snapshot(purchase_db) == before
    with signed_client() as client:
        assert client.post("/account/logout").status_code == 200
        before = snapshot(purchase_db)
        assert client.post("/store/purchases", json={"item_key": "candle"}).status_code == 401
        assert snapshot(purchase_db) == before


def test_guest_cannot_purchase_and_account_origin_policy_remains(purchase_db):
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as guest:
        assert guest.get("/guest").status_code == 200
        before = snapshot(purchase_db)
        assert guest.post("/store/purchases", json={"item_key": "candle"}).status_code == 401
        assert snapshot(purchase_db) == before
    with signed_client() as client:
        before = snapshot(purchase_db)
        assert client.post("/account/logout", headers={"Origin": "https://foreign.example.test"}).status_code == 403
        assert client.get("/account/me").json()["authenticated"] is True
        assert snapshot(purchase_db) == before
