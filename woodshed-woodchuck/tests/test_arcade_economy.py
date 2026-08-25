from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, arcade_routes, main
from app.arcade_rewards import (
    ARCADE_PAYOUT_THRESHOLDS,
    DAILY_REWARDED_PLAY_LIMIT,
    arcade_play_status,
    complete_arcade_play,
    payout_for_score,
    start_arcade_play,
)
from app.db import Base
from app.main import app
from app.models import ArcadeHighScore, ArcadePlaySession, WoodchuckProfile, WoodchuckState
from app.security import hash_pin


ROOT = Path(__file__).resolve().parents[1]
ECONOMY_JS = (ROOT / "static" / "js" / "arcade-economy.js").read_text(
    encoding="utf-8"
)
ARCADE_JS = (ROOT / "static" / "js" / "arcade.js").read_text(encoding="utf-8")
PLUNGE_JS = (ROOT / "static" / "js" / "plunge-burrow.js").read_text(
    encoding="utf-8"
)
WHEEL_JS = (ROOT / "static" / "js" / "wheel-of-woodchuck.js").read_text(
    encoding="utf-8"
)
SCALE_JS = (ROOT / "static" / "js" / "scale-keyboard.js").read_text(
    encoding="utf-8"
)

@pytest.fixture()
def economy_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(arcade_routes, "SessionLocal", factory)
    monkeypatch.setattr(main, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_player(factory, suffix: str, *, credits: int = 20):
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-ECO-{suffix}",
            display_name=f"Economy {suffix}",
            pin_hash=hash_pin("2468"),
            instrument="Flute",
            level="Beginner",
            goal="Practice",
            status="active",
        )
        session.add(profile)
        session.flush()
        session.add(WoodchuckState(
            profile_id=profile.id,
            state_json={"progress": {"credits": credits}},
            revision=0,
        ))
        session.commit()
        return profile


def signed_client(factory, suffix: str, *, credits: int = 20):
    profile = add_player(factory, suffix, credits=credits)
    client = TestClient(app)
    assert client.post(
        "/account/login",
        data={"woodchuck_id": profile.woodchuck_id, "pin": "2468"},
    ).status_code == 200
    # Login streak rewards are independent. Set the exact balance under test.
    with factory() as session:
        state = session.get(WoodchuckState, profile.id)
        state.state_json = {"progress": {"credits": credits}}
        session.commit()
    return client, profile


def balance(factory, profile_id: int) -> int:
    with factory() as session:
        state = session.get(WoodchuckState, profile_id)
        return int(state.state_json["progress"]["credits"])


def test_page_views_are_free_and_start_deducts_exactly_once(economy_database) -> None:
    client, profile = signed_client(economy_database, "START", credits=4)

    for path in (
        "/plunge-burrow",
        "/arcade/blue",
        "/arcade/radio-tuner",
        "/arcade/wheel-of-woodchuck",
        "/arcade/scale-keyboard",
    ):
        assert client.get(path).status_code == 200
    assert client.get("/arcade/plays/status/scale-keyboard").json()["balance"] == 4

    response = client.post("/arcade/plays", json={"game_key": "scale-keyboard"})
    assert response.status_code == 200
    assert response.json()["entry_cost"] == 1
    assert response.json()["balance"] == 3
    assert balance(economy_database, profile.id) == 3
    with economy_database() as session:
        plays = session.scalars(select(ArcadePlaySession)).all()
        assert len(plays) == 1
        assert plays[0].game_key == "scale-keyboard"
        assert plays[0].submitted_score is None


def test_insufficient_balance_rejects_start_without_negative_balance(
    economy_database,
) -> None:
    client, profile = signed_client(economy_database, "EMPTY", credits=0)
    response = client.post("/arcade/plays", json={"game_key": "blue"})
    assert response.status_code == 409
    assert "1 dandelion" in response.json()["detail"]
    assert balance(economy_database, profile.id) == 0
    with economy_database() as session:
        assert session.scalar(select(ArcadePlaySession.id)) is None


def test_client_cannot_choose_entry_cost_or_payout(economy_database) -> None:
    client, profile = signed_client(economy_database, "AUTHORITY", credits=3)
    rejected_start = client.post(
        "/arcade/plays", json={"game_key": "blue", "entry_cost": 0}
    )
    assert rejected_start.status_code == 422
    assert balance(economy_database, profile.id) == 3

    play = client.post("/arcade/plays", json={"game_key": "blue"}).json()
    rejected_result = client.post(
        f"/arcade/plays/{play['play_token']}/complete",
        json={"score": 250, "payout": 999},
    )
    assert rejected_result.status_code == 422
    assert balance(economy_database, profile.id) == 2
    accepted = client.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 250}
    )
    assert accepted.status_code == 200
    assert accepted.json()["payout"] == 5


@pytest.mark.parametrize("game_key", sorted(ARCADE_PAYOUT_THRESHOLDS))
def test_all_games_use_centralized_zero_one_two_three_five_tiers(game_key: str) -> None:
    thresholds = ARCADE_PAYOUT_THRESHOLDS[game_key]
    assert [payout for _score, payout in thresholds] == [1, 2, 3, 5]
    assert payout_for_score(game_key, thresholds[0][0] - 1) == 0
    for score, expected in thresholds:
        assert payout_for_score(game_key, score) == expected


def test_completion_pays_once_and_retry_is_idempotent(economy_database) -> None:
    client, profile = signed_client(economy_database, "RETRY", credits=5)
    play = client.post("/arcade/plays", json={"game_key": "blue"}).json()

    first = client.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 150}
    )
    retry = client.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 150}
    )

    assert first.status_code == 200
    assert first.json()["payout"] == 3
    assert first.json()["balance"] == 7
    assert retry.status_code == 200
    assert retry.json()["already_completed"] is True
    assert retry.json()["payout"] == 3
    assert balance(economy_database, profile.id) == 7
    with economy_database() as session:
        row = session.scalar(select(ArcadePlaySession))
        assert row.submitted_score == 150
        assert row.payout == 3
        assert row.reward_granted_at is not None


def test_conflicting_replay_and_another_profile_are_rejected(economy_database) -> None:
    owner, _profile = signed_client(economy_database, "OWNER", credits=4)
    stranger, _ = signed_client(economy_database, "OTHER", credits=4)
    play = owner.post("/arcade/plays", json={"game_key": "radio-tuner"}).json()

    assert stranger.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 300}
    ).status_code == 404
    assert owner.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 300}
    ).status_code == 200
    conflict = owner.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 301}
    )
    assert conflict.status_code == 409


def test_result_requires_a_valid_authenticated_play(economy_database) -> None:
    anonymous = TestClient(app)
    assert anonymous.post(
        "/arcade/plays/not-a-play/complete", json={"score": 10}
    ).status_code == 401
    client, _ = signed_client(economy_database, "MISSING")
    assert client.post(
        "/arcade/scores/blue", json={"score": 10}
    ).status_code == 422
    assert client.post(
        "/arcade/plays/not-a-play/complete", json={"score": 10}
    ).status_code == 404


def test_play_token_cannot_be_submitted_for_another_game(economy_database) -> None:
    client, _ = signed_client(economy_database, "WRONGGAME")
    play = client.post("/arcade/plays", json={"game_key": "blue"}).json()
    response = client.post(
        "/arcade/scores/radio-tuner",
        json={"score": 100, "play_token": play["play_token"]},
    )
    assert response.status_code == 409
    retry = client.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 30}
    )
    assert retry.status_code == 200


def test_lower_score_completes_and_pays_without_replacing_best(economy_database) -> None:
    client, profile = signed_client(economy_database, "LOWER", credits=3)
    with economy_database() as session:
        session.add(ArcadeHighScore(
            profile_id=profile.id, game_key="blue", best_score=250
        ))
        session.commit()
    play = client.post("/arcade/plays", json={"game_key": "blue"}).json()
    result = client.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 80}
    ).json()
    assert result["updated"] is False
    assert result["best_score"] == 250
    assert result["payout"] == 2
    assert result["balance"] == 4


def test_daily_cap_is_per_game_and_still_allows_paid_play(economy_database) -> None:
    client, profile = signed_client(economy_database, "CAP", credits=20)
    now = datetime.now(timezone.utc)
    with economy_database() as session:
        for index in range(DAILY_REWARDED_PLAY_LIMIT):
            session.add(ArcadePlaySession(
                profile_id=profile.id,
                game_key="blue",
                play_token=f"past-blue-{index}",
                started_at=now,
                completed_at=now,
                entry_cost=1,
                submitted_score=250,
                payout=5,
                reward_granted_at=now,
            ))
        session.commit()

    status = client.get("/arcade/plays/status/blue").json()
    assert status["reward_eligible"] is False
    assert status["completed_reward_plays"] == 10
    assert client.get("/arcade/plays/status/radio-tuner").json()["reward_eligible"] is True

    play = client.post("/arcade/plays", json={"game_key": "blue"}).json()
    assert play["balance"] == 19
    assert play["reward_eligible"] is False
    completed = client.post(
        f"/arcade/plays/{play['play_token']}/complete", json={"score": 250}
    ).json()
    assert completed["payout"] == 0
    assert completed["balance"] == 19


def test_chicago_calendar_boundary_resets_reward_count(economy_database) -> None:
    profile = add_player(economy_database, "BOUNDARY", credits=30)
    before_midnight = datetime(2026, 1, 15, 5, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 1, 15, 6, 1, tzinfo=timezone.utc)
    with economy_database() as session:
        for index in range(10):
            session.add(ArcadePlaySession(
                profile_id=profile.id,
                game_key="scale-keyboard",
                play_token=f"boundary-{index}",
                started_at=before_midnight,
                completed_at=before_midnight,
                entry_cost=1,
                submitted_score=800,
                payout=1,
                reward_granted_at=before_midnight,
            ))
        session.commit()

    with economy_database() as session:
        old_status = arcade_play_status(
            session,
            profile_id=profile.id,
            game_key="scale-keyboard",
            now=before_midnight,
        )
        new_status = arcade_play_status(
            session,
            profile_id=profile.id,
            game_key="scale-keyboard",
            now=after_midnight,
        )
    assert old_status["reward_eligible"] is False
    assert new_status["reward_eligible"] is True
    assert new_status["completed_reward_plays"] == 0


def test_service_start_and_complete_are_one_play_one_score(economy_database) -> None:
    profile = add_player(economy_database, "SERVICE", credits=2)
    with economy_database() as session:
        started = start_arcade_play(
            session, profile_id=profile.id, game_key="plunge-burrow"
        )
        session.commit()
        result = complete_arcade_play(
            session,
            profile_id=profile.id,
            play_token=started.play.play_token,
            score=25,
        )
        session.commit()
    assert result["payout"] == 2
    assert result["best_score"] == 25
    assert result["balance"] == 3


def test_all_five_clients_use_shared_start_and_completion_contract() -> None:
    assert 'startPlay(gameKey)' in ARCADE_JS
    assert 'completePlay(\n          activePlayToken, score' in ARCADE_JS
    assert 'startPlay("plunge-burrow")' in PLUNGE_JS
    assert "completePlay(activePlayToken, score)" in PLUNGE_JS
    assert 'startPlay("wheel-of-woodchuck")' in WHEEL_JS
    assert "completePlay(\n      activePlayToken" in WHEEL_JS
    assert 'startPlay("scale-keyboard")' in SCALE_JS
    assert "completePlay(activePlayToken, game.score)" in SCALE_JS
    assert 'body: JSON.stringify({ game_key: gameKey })' in ECONOMY_JS
    assert "encodeURIComponent(playToken)" in ECONOMY_JS
    assert "root.WWState.saveState(state, { sync: false })" in ECONOMY_JS


def test_arcade_economy_migration_extends_scores_and_creates_play_ledger() -> None:
    migration = (
        ROOT / "migrations" / "versions" /
        "c4d5e6f7a8b9_add_scale_keyboard_and_arcade_plays.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "c4d5e6f7a8b9"' in migration
    assert 'down_revision = "b3c4d5e6f7a8"' in migration
    assert '"arcade_play_sessions"' in migration
    assert "'scale-keyboard'" in migration
    assert 'op.drop_table("arcade_play_sessions")' in migration
