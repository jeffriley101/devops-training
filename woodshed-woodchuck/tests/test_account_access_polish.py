from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, main as main_module
from app.db import Base
from app.main import app
from app.models import WoodchuckProfile, WoodchuckState
from app.security import hash_pin


ROOT = Path(__file__).resolve().parents[1]
OWN_ID = "WC-ACCESS-OWN"
OTHER_ID = "WC-ACCESS-OTHER"


@pytest.fixture
def access_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(main_module, "SessionLocal", factory)
    with factory() as session:
        for woodchuck_id, name in ((OWN_ID, "Current Student"), (OTHER_ID, "Other Student")):
            profile = WoodchuckProfile(
                woodchuck_id=woodchuck_id, display_name=name, pin_hash=hash_pin("1234"),
                instrument="Flute", level="Beginner", goal="Practice",
            )
            session.add(profile); session.flush()
            session.add(WoodchuckState(
                profile_id=profile.id,
                state_json={
                    "version": 4,
                    "account": {"woodchuckId": woodchuck_id, "authenticated": True},
                    "profile": {"woodchuckName": name},
                    "progress": {"credits": 0},
                },
                revision=0,
            ))
        session.commit()
    return factory


def sign_in(client: TestClient) -> None:
    response = client.post(
        "/account/login", data={"woodchuck_id": OWN_ID, "pin": "1234"}
    )
    assert response.status_code == 200


def test_account_control_is_rendered_once_in_shop_share_dialog() -> None:
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    home = (ROOT / "templates/home.html").read_text(encoding="utf-8")
    store = (ROOT / "templates/store.html").read_text(encoding="utf-8")
    css = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")

    assert 'class="account-access-control" href="/account/privacy"' not in base
    assert "shed-account-privacy-link" not in home
    share = store[store.index('data-shop-panel-content="share"'):store.index('data-shop-panel-content="clothing"')]
    assert 'class="authenticated-access-controls shop-share-account-controls"' in share
    assert 'class="account-access-control" href="/account/privacy"' in share
    assert 'id="authenticated-logout"' in share
    dialog_rule = css[css.index(".shop-dialog-scroll .shop-share-account-controls {"):css.index(".shop-dialog-scroll .shop-share-account-controls .authenticated-student-name {")]
    assert "position: static" in dialog_rule
    assert "bottom: auto" in dialog_rule


def test_authenticated_board_and_privacy_show_only_own_read_only_id(access_db) -> None:
    client = TestClient(app)
    sign_in(client)

    board = client.get("/quest").text
    assert "Woodchuck ID:" in board and OWN_ID in board
    assert OTHER_ID not in board
    assert "1234" not in board and "pin_hash" not in board
    assert board.count('id="copy-woodchuck-id"') == 1

    privacy = client.get("/account/privacy").text
    assert f"Your Woodchuck ID: <strong>{OWN_ID}</strong>" in privacy
    assert OTHER_ID not in privacy and "1234" not in privacy and "pin_hash" not in privacy
    assert 'id="delete-woodchuck-id" name="woodchuck_id"' in privacy
    assert 'id="delete-woodchuck-id" name="woodchuck_id" value=' not in privacy
    assert "enter it manually below" in privacy


def test_public_pages_expose_no_woodchuck_identifier(access_db) -> None:
    client = TestClient(app)
    board = client.get("/quest").text
    assert "Woodchuck ID:" not in board
    assert OWN_ID not in board and OTHER_ID not in board
    assert "account-access-control" not in board


def test_copy_handler_is_single_purpose_and_wired_once() -> None:
    script = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    function = script[script.index("function wireWoodchuckIdCopy"):script.index("function hydrateHome")]
    assert function.count('button.addEventListener("click"') == 1
    assert 'navigator.clipboard.writeText(woodchuckId)' in function
    assert 'button.dataset.woodchuckId || ""' in function
    assert 'button.dataset.copyWired = "true"' in function
    assert function.count("navigator.clipboard.writeText") == 1
    assert "pin" not in function.casefold()
    assert "console." not in function
    assert script.count("wireWoodchuckIdCopy();") == 1


def test_sign_in_logout_and_existing_manual_deletion_still_work(access_db) -> None:
    client = TestClient(app)
    sign_in(client)
    assert client.get("/account/me").json()["authenticated"] is True
    assert client.post("/account/logout").status_code == 200
    assert client.get("/account/me").json()["authenticated"] is False

    sign_in(client)
    response = client.post("/account/delete", data={
        "woodchuck_id": OWN_ID, "pin": "1234", "confirmation": "DELETE",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/account/me").json()["authenticated"] is False
    assert client.post("/account/login", data={
        "woodchuck_id": OWN_ID, "pin": "1234",
    }).status_code == 401
    assert OWN_ID not in client.get("/quest").text
