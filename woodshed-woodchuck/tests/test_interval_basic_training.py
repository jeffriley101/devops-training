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
TEMPLATE = (ROOT / "templates" / "interval_basic_training.html").read_text(
    encoding="utf-8"
)
GAME_JS = (ROOT / "static" / "js" / "interval-basic-training.js").read_text(
    encoding="utf-8"
)
AUDIO_JS = (ROOT / "static" / "js" / "audio.js").read_text(encoding="utf-8")
ARCADE_JS = (ROOT / "static" / "js" / "arcade.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")


def run_interval_node(body: str) -> dict[str, object]:
    source = f"""
const assert = require("node:assert/strict");
const {{ IntervalBasicTrainingGame, INTERVAL_BASIC_TRAINING_QUESTIONS,
  INTERVAL_BASIC_TRAINING_RULES, midiToFrequency }} =
  require("./static/js/interval-basic-training.js");
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
def interval_database(monkeypatch: pytest.MonkeyPatch):
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
            woodchuck_id=f"WC-INTERVAL-{suffix}",
            display_name=f"Interval {suffix}",
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
        "/arcade/plays", json={"game_key": "interval-basic-training"}
    )
    assert started.status_code == 200
    completed = client.post(
        f"/arcade/plays/{started.json()['play_token']}/complete",
        json={"score": score},
    )
    assert completed.status_code == 200
    return started.json(), completed.json()


def test_eighth_cabinet_stable_key_and_authenticated_route(interval_database) -> None:
    assert ARCADE.count('class="arcade-cabinet ') == 9
    assert 'class="arcade-cabinet arcade-cabinet-intervals"' in ARCADE
    assert 'href="/arcade/interval-basic-training"' in ARCADE
    assert 'data-arcade-personal-best="interval-basic-training"' in ARCADE
    assert '"interval-basic-training": "/arcade/scores/interval-basic-training"' in ARCADE_JS
    anonymous = TestClient(app).get(
        "/arcade/interval-basic-training", follow_redirects=False
    )
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    client, _profile_id = signed_client(interval_database, "ROUTE")
    response = client.get("/arcade/interval-basic-training")
    assert response.status_code == 200
    assert 'data-interval-game' in response.text
    assert response.text.count("data-interval-answer=") == 9


def test_interval_soundtrack_is_idle_only_and_uses_shared_mute_state() -> None:
    soundtrack = (ROOT / "static" / "js" / "arcade-soundtrack.js").read_text(
        encoding="utf-8"
    )
    assert 'data-arcade-soundtrack="interval-basic-training"' in TEMPLATE
    assert 'data-arcade-soundtrack-toggle' in TEMPLATE
    assert '/static/js/arcade-soundtrack.js?v=4' in TEMPLATE
    assert '/static/js/interval-basic-training.js?v=2' in TEMPLATE
    assert 'url: "/static/audio/arcade/black-hole-rappelling.mp3?v=1"' in soundtrack
    assert 'document.addEventListener("woodshed:arcade-soundtrack-run-state"' in soundtrack
    assert "if (stopped || runActive || !applyPreferences()) return" in soundtrack
    assert "if (!enabled || runActive) audio.pause()" in soundtrack
    assert "setSoundtrackRunActive(true)" in GAME_JS
    assert "setSoundtrackRunActive(false)" in GAME_JS
    assert "root.WoodshedAudio.playPianoPitch(" in GAME_JS


def test_interval_soundtrack_controller_pauses_resumes_and_honors_mute() -> None:
    source = r'''
const assert = require("node:assert/strict");
const listeners = new Map();
const toggle = { textContent: "", setAttribute() {}, addEventListener() {} };
const soundtrackRoot = { dataset: { arcadeSoundtrack: "interval-basic-training" } };
global.document = {
  querySelector(selector) {
    if (selector === "[data-arcade-soundtrack]") return soundtrackRoot;
    if (selector === "[data-arcade-soundtrack-toggle]") return toggle;
    return null;
  },
  getElementById() { return null; },
  addEventListener(type, callback) {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(callback);
  },
};
global.window = global;
global.window.addEventListener = function () {};
global.window.setTimeout = function (callback) { callback(); return 1; };
global.window.clearTimeout = function () {};
let enabled = true;
global.window.WoodshedAudio = {
  isEnabled() { return enabled; }, getVolume() { return 0.4; },
  setEnabled(value) { enabled = value; },
};
let createdAudio;
global.Audio = class {
  constructor(url) {
    this.url = url; this.paused = true; this.ended = false; this.plays = 0;
    this.pauses = 0; createdAudio = this;
  }
  addEventListener() {}
  play() { this.paused = false; this.plays += 1; return Promise.resolve(); }
  pause() { this.paused = true; this.pauses += 1; }
};
require("./static/js/arcade-soundtrack.js");
function emit(type, event = {}) {
  for (const callback of listeners.get(type) || []) callback(event);
}
emit("DOMContentLoaded");
assert.equal(createdAudio.plays, 1);
emit("woodshed:arcade-soundtrack-run-state", {
  detail: { gameKey: "interval-basic-training", active: true },
});
assert.equal(createdAudio.paused, true);
emit("pointerdown");
assert.equal(createdAudio.plays, 1);
emit("woodshed:arcade-soundtrack-run-state", {
  detail: { gameKey: "interval-basic-training", active: false, resumeDelayMs: 350 },
});
assert.equal(createdAudio.plays, 2);
enabled = false;
emit("woodshed:arcade-soundtrack-run-state", {
  detail: { gameKey: "interval-basic-training", active: true },
});
emit("woodshed:arcade-soundtrack-run-state", {
  detail: { gameKey: "interval-basic-training", active: false },
});
assert.equal(createdAudio.plays, 2);
console.log(JSON.stringify({ plays: createdAudio.plays, pauses: createdAudio.pauses }));
'''
    result = subprocess.run(
        ["node", "-e", source], cwd=ROOT, check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {"plays": 2, "pauses": 3}


def test_exact_interval_table_all_starts_are_c4_and_duration_is_30_seconds() -> None:
    result = run_interval_node("""
assert.equal(INTERVAL_BASIC_TRAINING_RULES.gameKey, "interval-basic-training");
assert.equal(INTERVAL_BASIC_TRAINING_RULES.gameSeconds, 30);
assert.deepEqual(INTERVAL_BASIC_TRAINING_QUESTIONS.map((question) =>
  [question.label, question.firstNote, question.secondNote,
   question.firstMidi, question.secondMidi]), [
  ["Unison", "C4", "C4", 60, 60], ["2nd", "C4", "D4", 60, 62],
  ["3rd", "C4", "E4", 60, 64], ["4th", "C4", "F4", 60, 65],
  ["5th", "C4", "G4", 60, 67], ["6th", "C4", "A4", 60, 69],
  ["7th", "C4", "B4", 60, 71], ["Octave", "C4", "C5", 60, 72],
  ["9th", "C4", "D5", 60, 74],
]);
for (const question of INTERVAL_BASIC_TRAINING_QUESTIONS) {
  assert.equal(question.firstNote, "C4");
  assert.equal(question.firstMidi, 60);
}
console.log(JSON.stringify({ count: INTERVAL_BASIC_TRAINING_QUESTIONS.length }));
""")
    assert result == {"count": 9}


def test_answer_grid_has_exact_order_and_remains_three_by_three_on_mobile(
    interval_database,
) -> None:
    client, _profile_id = signed_client(interval_database, "GRID")
    rendered = client.get("/arcade/interval-basic-training").text
    labels = ["Unison", "2nd", "3rd", "4th", "5th", "6th", "7th", "Octave", "9th"]
    positions = [rendered.index(f'data-interval-answer="{label}"') for label in labels]
    assert positions == sorted(positions)
    grid = CSS[CSS.index(".interval-answer-grid {"):]
    grid = grid[:grid.index("}")]
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in grid
    mobile = CSS[CSS.index("@media (max-width: 430px)"):]
    mobile = mobile[:mobile.index("@media (max-width: 640px)")]
    assert ".interval-answer-grid" not in mobile
    button = CSS[CSS.index(".interval-answer-grid button {"):]
    button = button[:button.index("}")]
    assert "min-height: 3.5rem" in button


def test_correct_first_wrong_and_second_wrong_follow_two_strike_rule() -> None:
    result = run_interval_node("""
const game = new IntervalBasicTrainingGame({
  questions: INTERVAL_BASIC_TRAINING_QUESTIONS.slice(0, 3), random: () => 0,
});
game.start();
let answer = game.answer("Unison");
assert.equal(answer.correct, true);
assert.equal(game.score, 1);
assert.equal(game.mistakes, 0);
const afterCorrect = game.currentQuestion.label;
answer = game.answer("9th");
assert.equal(answer.correct, false);
assert.equal(game.score, 1);
assert.equal(game.mistakes, 1);
assert.equal(game.status, "running");
assert.notEqual(game.currentQuestion.label, afterCorrect);
const remainingBeforeSecondMiss = game.remainingMs;
answer = game.answer("9th");
assert.equal(answer.correct, false);
assert.equal(game.mistakes, 2);
assert.equal(answer.ended, true);
assert.equal(game.status, "ended");
assert.equal(game.endReason, "two-mistakes");
assert.equal(game.remainingMs, remainingBeforeSecondMiss);
assert.equal(game.answer("Unison").accepted, false);
console.log(JSON.stringify({ score: game.score, mistakes: game.mistakes,
  reason: game.endReason, remaining: game.remainingMs }));
""")
    assert result == {
        "score": 1,
        "mistakes": 2,
        "reason": "two-mistakes",
        "remaining": 30000,
    }


def test_replay_changes_no_state_and_does_not_pause_or_reset_timer() -> None:
    result = run_interval_node("""
const game = new IntervalBasicTrainingGame({ random: () => 0 });
game.start();
game.answer("9th");
game.elapse(4200);
const before = game.snapshot();
const replayed = game.replay();
const after = game.snapshot();
assert.equal(replayed.accepted, true);
assert.equal(replayed.question, before.currentQuestion);
assert.equal(after.currentQuestion, before.currentQuestion);
assert.equal(after.score, before.score);
assert.equal(after.mistakes, before.mistakes);
assert.equal(after.remainingMs, before.remainingMs);
console.log(JSON.stringify({ score: after.score, mistakes: after.mistakes,
  remaining: after.remainingMs }));
""")
    assert result == {"score": 0, "mistakes": 1, "remaining": 25800}


@pytest.mark.parametrize("wrong_answers", [0, 1])
def test_timer_expiration_ends_with_zero_or_one_mistake(wrong_answers: int) -> None:
    result = run_interval_node(f"""
const game = new IntervalBasicTrainingGame({{ random: () => 0 }});
game.start();
if ({wrong_answers}) game.answer("9th");
game.elapse(30000);
assert.equal(game.status, "ended");
assert.equal(game.endReason, "time");
assert.equal(game.mistakes, {wrong_answers});
assert.equal(game.answer("Unison").accepted, false);
assert.equal(game.markSubmitted(), true);
assert.equal(game.markSubmitted(), false);
console.log(JSON.stringify({{ reason: game.endReason, mistakes: game.mistakes }}));
""")
    assert result == {"reason": "time", "mistakes": wrong_answers}


def test_audio_uses_shared_piano_helper_sequential_timing_and_cancellable_locks() -> None:
    result = run_interval_node("""
assert.ok(Math.abs(midiToFrequency(60) - 261.625565) < 0.001);
assert.ok(Math.abs(midiToFrequency(69) - 440) < 0.001);
assert.equal(INTERVAL_BASIC_TRAINING_RULES.noteDurationSeconds, 0.35);
assert.equal(INTERVAL_BASIC_TRAINING_RULES.noteGapMs, 120);
assert.equal(INTERVAL_BASIC_TRAINING_RULES.secondNoteDelayMs, 470);
assert.equal(INTERVAL_BASIC_TRAINING_RULES.sequenceDurationMs, 820);
console.log(JSON.stringify({ timing: [470, 820] }));
""")
    assert result == {"timing": [470, 820]}
    assert "root.WoodshedAudio.playPianoPitch(" in GAME_JS
    assert "root.WoodshedAudio.unlock()" in GAME_JS
    assert "cancelQuestionAudio()" in GAME_JS
    assert "if (answerLocked || audioLocked) return" in GAME_JS
    assert "replayButton.disabled = !running || answerLocked || audioLocked" in GAME_JS
    assert "function playPianoPitch(frequency, duration)" in AUDIO_JS
    assert "current.piano.triggerAttackRelease(pitch, noteDuration)" in AUDIO_JS


def test_client_second_miss_and_timer_share_one_completion_guard() -> None:
    assert "if (finishPromise) return finishPromise" in GAME_JS
    assert "if (!game.markSubmitted()) return Promise.resolve(null)" in GAME_JS
    assert "if (result.ended)" in GAME_JS
    assert "finishGame();" in GAME_JS
    assert "if (game.status === \"ended\") finishGame()" in GAME_JS
    assert "completePlay(token, game.score)" in GAME_JS
    assert "Two wrong answers ended the run." in GAME_JS


def test_interval_economy_cost_payout_idempotency_and_insufficient_balance(
    interval_database,
) -> None:
    assert ARCADE_PAYOUT_THRESHOLDS["interval-basic-training"] == (
        (3, 1), (6, 2), (9, 3), (12, 5)
    )
    empty, empty_profile_id = signed_client(interval_database, "EMPTY", credits=0)
    rejected = empty.post(
        "/arcade/plays", json={"game_key": "interval-basic-training"}
    )
    assert rejected.status_code == 409
    with interval_database() as session:
        assert session.scalar(select(ArcadePlaySession.id)) is None
        assert session.get(WoodchuckState, empty_profile_id).state_json["progress"]["credits"] == 0

    client, profile_id = signed_client(interval_database, "PAYOUT", credits=10)
    started, completed = play(client, 12)
    assert started["entry_cost"] == 1
    assert completed["payout"] == 5
    retry = client.post(
        f"/arcade/plays/{started['play_token']}/complete", json={"score": 12}
    )
    assert retry.status_code == 200
    assert retry.json()["already_completed"] is True
    with interval_database() as session:
        state = session.get(WoodchuckState, profile_id)
        assert state.state_json["progress"]["credits"] == 14


def test_interval_daily_cap_is_independent(interval_database) -> None:
    client, _profile_id = signed_client(interval_database, "CAP", credits=30)
    for _index in range(DAILY_REWARDED_PLAY_LIMIT):
        _started, result = play(client, 3)
        assert result["payout"] == 1
    started, capped = play(client, 12)
    assert started["reward_eligible"] is False
    assert capped["payout"] == 0
    assert capped["best_score"] == 12
    assert client.get("/arcade/plays/status/dressed-to-the-nines").json()[
        "reward_eligible"
    ] is True


def test_independent_personal_best_and_privacy_safe_top_five(interval_database) -> None:
    first, first_id = signed_client(interval_database, "ALPHA")
    second, _ = signed_client(interval_database, "BETA")
    third, _ = signed_client(interval_database, "GAMMA")
    play(first, 12)
    play(first, 3)
    play(second, 12)
    play(third, 9)
    payload = first.get("/arcade/scores/interval-basic-training").json()
    assert payload["best_score"] == 12
    assert [(row["rank"], row["score"]) for row in payload["leaderboard"]] == [
        (1, 12), (1, 12), (3, 9)
    ]
    assert all(
        set(row) == {"rank", "display_name", "score", "is_current_user"}
        for row in payload["leaderboard"]
    )
    with interval_database() as session:
        interval_score = session.scalar(select(ArcadeHighScore).where(
            ArcadeHighScore.profile_id == first_id,
            ArcadeHighScore.game_key == "interval-basic-training",
        ))
        nines_score = session.scalar(select(ArcadeHighScore).where(
            ArcadeHighScore.profile_id == first_id,
            ArcadeHighScore.game_key == "dressed-to-the-nines",
        ))
        assert interval_score.best_score == 12
        assert nines_score is None


def test_interval_migration_extends_existing_arcade_constraints() -> None:
    migration = (
        ROOT / "migrations" / "versions" /
        "i9d0e1f2a3b4_add_interval_basic_training_arcade_game.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "i9d0e1f2a3b4"' in migration
    assert 'down_revision = "h8c9d0e1f2a3"' in migration
    assert migration.count("'interval-basic-training'") >= 4
    assert "op.create_table" not in migration
    assert "DELETE FROM arcade_play_sessions" in migration
    assert "DELETE FROM arcade_high_scores" in migration
