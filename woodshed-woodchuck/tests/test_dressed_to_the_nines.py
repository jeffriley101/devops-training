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
TEMPLATE = (ROOT / "templates" / "dressed_to_the_nines.html").read_text(
    encoding="utf-8"
)
GAME_JS = (ROOT / "static" / "js" / "dressed-to-the-nines.js").read_text(
    encoding="utf-8"
)
ARCADE_JS = (ROOT / "static" / "js" / "arcade.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")


def run_nines_node(body: str) -> dict[str, object]:
    source = f"""
const assert = require("node:assert/strict");
const {{ DressedToTheNinesGame, NINES_QUESTIONS, NINES_RULES, normalizeAnswer }} =
  require("./static/js/dressed-to-the-nines.js");
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
def nines_database(monkeypatch: pytest.MonkeyPatch):
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
            woodchuck_id=f"WC-NINES-{suffix}",
            display_name=f"Nines {suffix}",
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
    with factory() as session:
        state = session.get(WoodchuckState, profile_id)
        state.state_json = {"progress": {"credits": credits}}
        session.commit()
    return client, profile_id


def play(client: TestClient, score: int):
    started = client.post(
        "/arcade/plays", json={"game_key": "dressed-to-the-nines"}
    )
    assert started.status_code == 200
    completed = client.post(
        f"/arcade/plays/{started.json()['play_token']}/complete",
        json={"score": score},
    )
    assert completed.status_code == 200
    return started.json(), completed.json()


def test_seventh_cabinet_and_nines_route_are_authenticated(nines_database) -> None:
    assert ARCADE.count('class="arcade-cabinet ') == 9
    assert 'class="arcade-cabinet arcade-cabinet-nines"' in ARCADE
    assert 'href="/arcade/dressed-to-the-nines"' in ARCADE
    assert 'data-arcade-personal-best="dressed-to-the-nines"' in ARCADE
    assert '"dressed-to-the-nines": "/arcade/scores/dressed-to-the-nines"' in ARCADE_JS
    anonymous = TestClient(app).get(
        "/arcade/dressed-to-the-nines", follow_redirects=False
    )
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    client, _profile_id = signed_client(nines_database, "ROUTE")
    response = client.get("/arcade/dressed-to-the-nines")
    assert response.status_code == 200
    assert 'data-nines-game' in response.text
    assert response.text.count("data-nines-answer=") == 7


def test_nines_uses_sand_drop_and_shared_arcade_mute() -> None:
    soundtrack = (ROOT / "static" / "js" / "arcade-soundtrack.js").read_text(
        encoding="utf-8"
    )
    assert 'data-arcade-soundtrack="dressed-to-the-nines"' in TEMPLATE
    assert 'data-arcade-soundtrack-toggle' in TEMPLATE
    assert '/static/js/arcade-soundtrack.js?v=4' in TEMPLATE
    assert 'url: "/static/audio/arcade/sand-drop.mp3?v=1"' in soundtrack


def test_question_bank_is_exact_and_answers_are_natural_notes() -> None:
    result = run_nines_node("""
assert.equal(NINES_RULES.gameKey, "dressed-to-the-nines");
assert.equal(NINES_RULES.gameSeconds, 30);
assert.deepEqual(NINES_QUESTIONS.map(({ tonality, start, answer }) =>
  [tonality, start, answer]), [
  ["C Major", "C", "D"], ["D Minor", "D", "E"],
  ["F Major", "F", "G"], ["G Major", "G", "A"],
  ["A Minor", "A", "B"], ["Bb Major", "Bb", "C"],
  ["Eb Major", "Eb", "F"],
]);
for (const question of NINES_QUESTIONS) assert.match(question.answer, /^[A-G]$/);
console.log(JSON.stringify({ count: NINES_QUESTIONS.length, seconds: NINES_RULES.gameSeconds }));
""")
    assert result == {"count": 7, "seconds": 30}


def test_answers_score_only_when_correct_and_questions_advance_without_repeat() -> None:
    result = run_nines_node("""
const game = new DressedToTheNinesGame({ questions: NINES_QUESTIONS.slice(0, 3), random: () => 0 });
game.start();
assert.equal(game.currentQuestion.tonality, "C Major");
let answer = game.submit(" d ");
assert.equal(answer.correct, true);
assert.equal(game.score, 1);
assert.notEqual(game.currentQuestion.tonality, "C Major");
const secondTonality = game.currentQuestion.tonality;
answer = game.submit("a");
assert.equal(answer.correct, false);
assert.equal(game.score, 1);
assert.notEqual(game.currentQuestion.tonality, secondTonality);
assert.deepEqual(game.submit("Bb"), { accepted: false, reason: "invalid-answer" });
console.log(JSON.stringify({ score: game.score, current: game.currentQuestion.tonality }));
""")
    assert result["score"] == 1


def test_nines_uses_one_thirty_second_clock_and_touch_friendly_answer_grid() -> None:
    result = run_nines_node("""
const game = new DressedToTheNinesGame({ random: () => 0 });
game.start();
game.submit("D");
game.elapse(12000);
game.submit(game.currentQuestion.answer);
assert.equal(game.remainingMs, 18000);
game.elapse(18000);
assert.equal(game.status, "ended");
assert.equal(game.submit("A").accepted, false);
console.log(JSON.stringify({ remaining: game.remainingMs, status: game.status }));
""")
    assert result == {"remaining": 0, "status": "ended"}
    assert 'class="nines-answer-grid"' in TEMPLATE
    assert 'role="group"' in TEMPLATE
    assert "grid-template-columns: repeat(7, minmax(2.75rem, 1fr))" in CSS
    mobile = CSS[CSS.index("@media (max-width: 430px)"):]
    assert "grid-template-columns: repeat(4, minmax(3rem, 1fr))" in mobile
    answer_css = CSS[CSS.index(".nines-answer-grid button {"):]
    answer_css = answer_css[:answer_css.index("}")]
    assert "min-height: 3.25rem" in answer_css


def test_entry_cost_insufficient_balance_and_nines_payout_tiers(nines_database) -> None:
    assert ARCADE_PAYOUT_THRESHOLDS["dressed-to-the-nines"] == (
        (3, 1), (6, 2), (9, 3), (12, 5)
    )
    empty, empty_profile_id = signed_client(nines_database, "EMPTY", credits=0)
    rejected = empty.post(
        "/arcade/plays", json={"game_key": "dressed-to-the-nines"}
    )
    assert rejected.status_code == 409
    with nines_database() as session:
        assert session.scalar(select(ArcadePlaySession.id)) is None
        state = session.get(WoodchuckState, empty_profile_id)
        assert state.state_json["progress"]["credits"] == 0

    client, profile_id = signed_client(nines_database, "TIERS", credits=10)
    started, completed = play(client, 12)
    assert started["entry_cost"] == 1
    assert completed["payout"] == 5
    retry = client.post(
        f"/arcade/plays/{started['play_token']}/complete", json={"score": 12}
    )
    assert retry.status_code == 200
    assert retry.json()["already_completed"] is True
    with nines_database() as session:
        state = session.get(WoodchuckState, profile_id)
        assert state.state_json["progress"]["credits"] == 14


def test_personal_best_top_five_privacy_and_lower_score_behavior(nines_database) -> None:
    first, first_id = signed_client(nines_database, "ALPHA")
    second, _ = signed_client(nines_database, "BETA")
    third, _ = signed_client(nines_database, "GAMMA")
    play(first, 12)
    play(first, 3)
    play(second, 12)
    play(third, 9)
    payload = first.get("/arcade/scores/dressed-to-the-nines").json()
    assert payload["best_score"] == 12
    assert [(row["rank"], row["score"]) for row in payload["leaderboard"]] == [
        (1, 12), (1, 12), (3, 9)
    ]
    assert all(
        set(row) == {"rank", "display_name", "score", "is_current_user"}
        for row in payload["leaderboard"]
    )
    with nines_database() as session:
        score = session.scalar(select(ArcadeHighScore).where(
            ArcadeHighScore.profile_id == first_id,
            ArcadeHighScore.game_key == "dressed-to-the-nines",
        ))
        assert score.best_score == 12


def test_nines_daily_reward_cap_is_per_game(nines_database) -> None:
    client, _profile_id = signed_client(nines_database, "CAP", credits=30)
    for _index in range(DAILY_REWARDED_PLAY_LIMIT):
        _started, result = play(client, 3)
        assert result["payout"] == 1
    started, capped = play(client, 12)
    assert started["reward_eligible"] is False
    assert capped["payout"] == 0
    assert capped["best_score"] == 12
    assert client.get("/arcade/plays/status/thirds").json()["reward_eligible"] is True


def test_nines_uses_shared_client_contract_and_has_distinct_cabinet() -> None:
    assert "startPlay(GAME_KEY)" in GAME_JS
    assert "completePlay(token, game.score)" in GAME_JS
    assert "if (finishPromise) return finishPromise" in GAME_JS
    assert "fetch(`/arcade/scores/${GAME_KEY}`" in GAME_JS
    assert "if (answerLocked) return" in GAME_JS
    assert "!running || answerLocked" in GAME_JS
    assert 'data-arcade-leaderboard="dressed-to-the-nines"' in TEMPLATE
    cabinet_start = CSS.index(".arcade-cabinet-nines {")
    cabinet = CSS[cabinet_start:CSS.index("}", cabinet_start)]
    assert "#a876d4" in cabinet
    assert "arcade-nines-screen" in ARCADE


def test_nines_migration_extends_existing_arcade_constraints() -> None:
    migration = (
        ROOT / "migrations" / "versions" /
        "h8c9d0e1f2a3_add_dressed_to_the_nines_arcade_game.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "h8c9d0e1f2a3"' in migration
    assert 'down_revision = "g7b8c9d0e1f2"' in migration
    assert migration.count("'dressed-to-the-nines'") >= 4
    assert "op.create_table" not in migration
    assert "DELETE FROM arcade_play_sessions" in migration
    assert "DELETE FROM arcade_high_scores" in migration
