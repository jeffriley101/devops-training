from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, xp_routes
from app.db import Base
from app.main import app
from app.models import WoodchuckProfile
from app.security import hash_pin
from app.xp import plunge_best_payload, record_plunge_best_score


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
GAME_JS = (ROOT / "static/js/plunge-burrow.js").read_text(encoding="utf-8")
BOARD = (ROOT / "templates/quest.html").read_text(encoding="utf-8")


@pytest.fixture()
def best_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(xp_routes, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_profile(
    session,
    *,
    woodchuck_id: str,
    display_name: str,
    score: int = 0,
    status: str = "active",
) -> WoodchuckProfile:
    profile = WoodchuckProfile(
        woodchuck_id=woodchuck_id,
        display_name=display_name,
        pin_hash=hash_pin("2468"),
        instrument="Flute",
        level="Beginner",
        goal="Practice",
        status=status,
        plunge_best_score=score,
    )
    session.add(profile)
    session.flush()
    return profile


def sign_in(client: TestClient, woodchuck_id: str) -> None:
    response = client.post(
        "/account/login",
        data={"woodchuck_id": woodchuck_id, "pin": "2468"},
    )
    assert response.status_code == 200


def test_first_plunge_best_score_is_persisted(best_database) -> None:
    with best_database() as session:
        profile = add_profile(
            session, woodchuck_id="WC-BEST-FIRST", display_name="First"
        )
        session.commit()

        best_score, updated = record_plunge_best_score(
            session, profile_id=profile.id, score=37
        )
        session.commit()

    assert (best_score, updated) == (37, True)
    with best_database() as session:
        assert session.get(WoodchuckProfile, profile.id).plunge_best_score == 37


def test_lower_score_does_not_replace_personal_best(best_database) -> None:
    with best_database() as session:
        profile = add_profile(
            session, woodchuck_id="WC-BEST-LOWER", display_name="Lower", score=80
        )
        session.commit()

        best_score, updated = record_plunge_best_score(
            session, profile_id=profile.id, score=42
        )
        session.commit()

    assert (best_score, updated) == (80, False)


def test_higher_score_replaces_personal_best(best_database) -> None:
    with best_database() as session:
        profile = add_profile(
            session, woodchuck_id="WC-BEST-HIGHER", display_name="Higher", score=80
        )
        session.commit()

        best_score, updated = record_plunge_best_score(
            session, profile_id=profile.id, score=125
        )
        session.commit()

    assert (best_score, updated) == (125, True)


def test_duplicate_best_submission_is_safe_and_idempotent(best_database) -> None:
    with best_database() as session:
        add_profile(
            session, woodchuck_id="WC-BEST-RETRY", display_name="Retry"
        )
        session.commit()
    client = TestClient(app)
    sign_in(client, "WC-BEST-RETRY")

    first = client.post("/xp/plunge-best", json={"score": 64})
    retry = client.post("/xp/plunge-best", json={"score": 64})

    assert first.status_code == 200
    assert first.json()["updated"] is True
    assert retry.status_code == 200
    assert retry.json()["updated"] is False
    assert retry.json()["best_score"] == 64


def test_best_score_is_available_in_a_separate_device_session(best_database) -> None:
    with best_database() as session:
        add_profile(
            session, woodchuck_id="WC-BEST-DEVICE", display_name="Traveler"
        )
        session.commit()
    first_device = TestClient(app)
    second_device = TestClient(app)
    sign_in(first_device, "WC-BEST-DEVICE")
    sign_in(second_device, "WC-BEST-DEVICE")

    assert first_device.post("/xp/plunge-best", json={"score": 91}).status_code == 200
    response = second_device.get("/xp/plunge-best")

    assert response.status_code == 200
    assert response.json()["best_score"] == 91


def test_leaderboard_uses_olympic_ties_and_active_public_names_only(
    best_database,
) -> None:
    with best_database() as session:
        current = add_profile(
            session, woodchuck_id="WC-BEST-CURRENT", display_name="Current", score=10
        )
        add_profile(
            session, woodchuck_id="WC-BEST-LEADER", display_name="Leader", score=50
        )
        add_profile(
            session, woodchuck_id="WC-BEST-ALPHA", display_name="Alpha", score=40
        )
        add_profile(
            session, woodchuck_id="WC-BEST-ZULU", display_name="Zulu", score=40
        )
        add_profile(
            session,
            woodchuck_id="WC-BEST-DELETED",
            display_name="Deleted Secret",
            score=999,
            status="deleted",
        )
        session.commit()

        payload = plunge_best_payload(session, profile_id=current.id)

    assert payload["best_score"] == 10
    assert [
        (row["rank"], row["display_name"], row["score"])
        for row in payload["leaderboard"]
    ] == [
        (1, "Leader", 50),
        (2, "Alpha", 40),
        (2, "Zulu", 40),
        (4, "Current", 10),
    ]
    assert "Deleted Secret" not in str(payload)


def test_best_score_api_requires_authentication_and_exposes_no_identifiers(
    best_database,
) -> None:
    with best_database() as session:
        add_profile(
            session, woodchuck_id="WC-BEST-PRIVATE", display_name="Public Name", score=12
        )
        session.commit()
    anonymous = TestClient(app)
    assert anonymous.get("/xp/plunge-best").status_code == 401
    assert anonymous.post("/xp/plunge-best", json={"score": 12}).status_code == 401

    client = TestClient(app)
    sign_in(client, "WC-BEST-PRIVATE")
    payload = client.get("/xp/plunge-best").json()
    serialized = str(payload)
    assert set(payload) == {"best_score", "leaderboard"}
    assert "WC-BEST-PRIVATE" not in serialized
    assert "profile_id" not in serialized
    assert "pin" not in serialized.casefold()


def test_board_keeps_burrow_standings_outside_board_and_preserves_launcher() -> None:
    assert 'id="board-player-burrow-best"' not in BOARD
    assert 'id="board-burrow-leaderboard"' not in BOARD
    assert 'id="plunge-burrow-button"' in BOARD


def test_game_reports_its_best_without_using_local_storage_as_server_evidence() -> None:
    assert 'root.fetch("/xp/plunge-best"' in GAME_JS
    assert "WoodshedArcadeEconomy.completePlay(activePlayToken, score)" in GAME_JS
    gameover = GAME_JS[GAME_JS.index('if (event === "gameover")'):]
    assert "completePlay(detail.score)" in gameover
