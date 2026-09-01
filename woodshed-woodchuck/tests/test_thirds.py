from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, arcade_routes, main
from app.arcade_rewards import ARCADE_PAYOUT_THRESHOLDS, DAILY_REWARDED_PLAY_LIMIT
from app.db import Base
from app.main import app
from app.models import ArcadeHighScore, ArcadePlaySession, WoodchuckProfile, WoodchuckState
from app.security import hash_pin


ROOT = Path(__file__).resolve().parents[1]
ARCADE = (ROOT / "templates" / "arcade.html").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "thirds.html").read_text(encoding="utf-8")
GAME_JS = (ROOT / "static" / "js" / "thirds.js").read_text(encoding="utf-8")
ARCADE_JS = (ROOT / "static" / "js" / "arcade.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")


def run_thirds_node(body: str) -> dict[str, object]:
    source = f"""
const assert = require("node:assert/strict");
const {{ ThirdsGame, THIRDS_CARDS, THIRDS_RULES, normalizeAnswer }} =
  require("./static/js/thirds.js");
{body}
"""
    result = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout or "{}")


@pytest.fixture()
def thirds_database(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(account_routes, "SessionLocal", factory)
    monkeypatch.setattr(arcade_routes, "SessionLocal", factory)
    monkeypatch.setattr(main, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def signed_client(factory, suffix: str, *, credits: int = 20):
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-THIRDS-{suffix}",
            display_name=f"Thirds {suffix}",
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
        profile_id = profile.id
        woodchuck_id = profile.woodchuck_id
    client = TestClient(app)
    assert client.post(
        "/account/login", data={"woodchuck_id": woodchuck_id, "pin": "2468"}
    ).status_code == 200
    # Login-streak awards are independent of the Arcade balance under test.
    with factory() as session:
        state = session.get(WoodchuckState, profile_id)
        state.state_json = {"progress": {"credits": credits}}
        session.commit()
    return client, profile_id


def play(client: TestClient, score: int):
    started = client.post("/arcade/plays", json={"game_key": "thirds"})
    assert started.status_code == 200
    completed = client.post(
        f"/arcade/plays/{started.json()['play_token']}/complete",
        json={"score": score},
    )
    assert completed.status_code == 200
    return started.json(), completed.json()


def test_sixth_cabinet_and_thirds_route_are_authenticated(thirds_database) -> None:
    assert ARCADE.count('class="arcade-cabinet ') == 8
    assert 'class="arcade-cabinet arcade-cabinet-thirds"' in ARCADE
    assert 'href="/arcade/thirds"' in ARCADE
    assert 'data-arcade-personal-best="thirds"' in ARCADE
    assert 'thirds: "/arcade/scores/thirds"' in ARCADE_JS
    anonymous = TestClient(app).get("/arcade/thirds", follow_redirects=False)
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    client, _profile_id = signed_client(thirds_database, "ROUTE")
    response = client.get("/arcade/thirds")
    assert response.status_code == 200
    assert 'data-thirds-game' in response.text


def test_initial_cards_are_exact_and_all_answers_are_natural_notes() -> None:
    result = run_thirds_node("""
assert.equal(THIRDS_RULES.gameSeconds, 30);
assert.deepEqual(THIRDS_CARDS.map(({ chord, answer }) => [chord, answer]), [
  ["C Major", "E"], ["D Minor", "F"], ["E Minor", "G"],
  ["F Major", "A"], ["G Major", "B"], ["A Minor", "C"],
  ["B Minor", "D"],
]);
for (const card of THIRDS_CARDS) assert.match(card.answer, /^[A-G]$/);
console.log(JSON.stringify({ count: THIRDS_CARDS.length, seconds: THIRDS_RULES.gameSeconds }));
""")
    assert result == {"count": 7, "seconds": 30}


def test_answers_trim_case_increment_only_when_correct_and_always_advance() -> None:
    result = run_thirds_node("""
const game = new ThirdsGame({ cards: THIRDS_CARDS.slice(0, 3), random: () => 0 });
game.start();
assert.equal(game.currentCard.chord, "C Major");
let answer = game.submit("  e  ");
assert.equal(answer.correct, true);
assert.equal(game.score, 1);
assert.notEqual(game.currentCard.chord, "C Major");
const secondChord = game.currentCard.chord;
answer = game.submit("a");
assert.equal(answer.correct, false);
assert.equal(game.score, 1);
assert.notEqual(game.currentCard.chord, secondChord);
assert.deepEqual(game.submit("H"), { accepted: false, reason: "invalid-answer" });
console.log(JSON.stringify({ score: game.score, current: game.currentCard.chord }));
""")
    assert result["score"] == 1


def test_thirty_second_clock_is_run_wide_and_input_markup_supports_enter() -> None:
    result = run_thirds_node("""
const game = new ThirdsGame({ random: () => 0 });
game.start();
game.submit("E");
game.elapse(12500);
game.submit(game.currentCard.answer);
assert.equal(game.remainingMs, 17500);
game.elapse(17500);
assert.equal(game.status, "ended");
assert.equal(game.submit("A").accepted, false);
console.log(JSON.stringify({ remaining: game.remainingMs, status: game.status }));
""")
    assert result == {"remaining": 0, "status": "ended"}
    assert 'id="thirds-answer-form"' in TEMPLATE
    assert 'pattern="[A-Ga-g]"' in TEMPLATE
    assert 'type="submit"' in TEMPLATE
    assert 'class="thirds-active-area"' in TEMPLATE


def test_thirds_uses_shared_play_session_once_and_persists_personal_best(
    thirds_database,
) -> None:
    client, profile_id = signed_client(thirds_database, "SAVE", credits=10)
    started, completed = play(client, 9)
    assert started["entry_cost"] == 1
    assert completed["payout"] == 3
    assert completed["best_score"] == 9
    retry = client.post(
        f"/arcade/plays/{started['play_token']}/complete", json={"score": 9}
    )
    assert retry.status_code == 200
    assert retry.json()["already_completed"] is True
    assert retry.json()["payout"] == 3
    with thirds_database() as session:
        plays = session.scalars(select(ArcadePlaySession).where(
            ArcadePlaySession.profile_id == profile_id
        )).all()
        score = session.scalar(select(ArcadeHighScore).where(
            ArcadeHighScore.profile_id == profile_id,
            ArcadeHighScore.game_key == "thirds",
        ))
        state = session.get(WoodchuckState, profile_id)
        assert len(plays) == 1
        assert score.best_score == 9
        assert state.state_json["progress"]["credits"] == 12
    second_device = TestClient(app)
    assert second_device.post(
        "/account/login", data={"woodchuck_id": "WC-THIRDS-SAVE", "pin": "2468"}
    ).status_code == 200
    assert second_device.get("/arcade/scores/thirds").json()["best_score"] == 9


def test_thirds_payout_tiers_and_per_game_daily_cap(thirds_database) -> None:
    assert ARCADE_PAYOUT_THRESHOLDS["thirds"] == (
        (3, 1), (6, 2), (9, 3), (12, 5)
    )
    client, profile_id = signed_client(thirds_database, "CAP", credits=30)
    for _index in range(DAILY_REWARDED_PLAY_LIMIT):
        _started, result = play(client, 3)
        assert result["payout"] == 1
    started, capped = play(client, 12)
    assert started["reward_eligible"] is False
    assert capped["payout"] == 0
    assert capped["best_score"] == 12
    with thirds_database() as session:
        state = session.get(WoodchuckState, profile_id)
        assert state.state_json["progress"]["credits"] == 29


def test_thirds_top_five_keeps_privacy_safe_names_and_olympic_ties(
    thirds_database,
) -> None:
    first, _ = signed_client(thirds_database, "ALPHA")
    second, _ = signed_client(thirds_database, "BETA")
    third, _ = signed_client(thirds_database, "GAMMA")
    play(first, 12)
    play(second, 12)
    play(third, 9)
    payload = first.get("/arcade/scores/thirds").json()
    assert [(row["rank"], row["score"]) for row in payload["leaderboard"]] == [
        (1, 12), (1, 12), (3, 9)
    ]
    assert all(set(row) == {"rank", "display_name", "score", "is_current_user"}
               for row in payload["leaderboard"])


def test_existing_arcade_frames_keep_distinct_outer_identities_and_dark_inner_panel() -> None:
    identities = {
        "plunge": "#a46a38",
        "blue": "#4298e4",
        "radio": "#eef2f5",
        "wheel": "#55a86d",
        "scale": "#fffdf4",
        "thirds": "#e55858",
    }
    for game, color in identities.items():
        block_start = CSS.index(f".arcade-cabinet-{game} {{")
        block = CSS[block_start:CSS.index("}", block_start)]
        assert color in block
    inner = CSS[CSS.index(".arcade-cabinet-link {"):]
    inner = inner[:inner.index("}")]
    assert "linear-gradient(180deg, #2a2d45, #131422)" in inner
    assert ARCADE.count("1🌼 TO PLAY · WIN UP TO 5🌼") == 8
    assert "arcade-cabinet-copy" not in ARCADE


def test_scale_cabinet_uses_cartoon_keyboard_necktie_structure() -> None:
    for class_name in (
        "keyboard-tie-person", "keyboard-tie-hair", "keyboard-tie-head",
        "keyboard-tie-shirt", "keyboard-necktie",
    ):
        assert class_name in ARCADE
        assert f".{class_name}" in CSS
    tie = CSS[CSS.rindex(".keyboard-necktie {"):]
    tie = tie[:tie.index("}")]
    assert "repeating-linear-gradient" in tie


def test_thirds_migration_extends_only_existing_arcade_game_constraints() -> None:
    migration = (ROOT / "migrations" / "versions" /
                 "f6a7b8c9d0e1_add_thirds_arcade_game.py").read_text(encoding="utf-8")
    assert 'revision = "f6a7b8c9d0e1"' in migration
    assert 'down_revision = "e6f7a8b9c0d1"' in migration
    assert migration.count("'thirds'") >= 4
    assert 'op.create_table' not in migration
    assert 'DELETE FROM arcade_play_sessions WHERE game_key = \'thirds\'' in migration


def test_thirds_client_finishes_once_through_existing_economy_contract() -> None:
    assert 'startPlay("thirds")' in GAME_JS
    assert "completePlay(token, game.score)" in GAME_JS
    assert "if (finishPromise) return finishPromise" in GAME_JS
    assert 'fetch("/arcade/scores/thirds"' in GAME_JS
    assert 'data-arcade-leaderboard="thirds"' in TEMPLATE
