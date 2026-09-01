from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import account_routes, arcade_routes, main
from app.db import Base
from app.main import app
from app.models import WoodchuckProfile, WoodchuckState
from app.security import hash_pin


ROOT = Path(__file__).resolve().parents[1]
ARCADE = (ROOT / "templates" / "arcade.html").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "scale_keyboard.html").read_text(encoding="utf-8")
GAME_JS = (ROOT / "static" / "js" / "scale-keyboard.js").read_text(encoding="utf-8")
DATA_JS = (ROOT / "static" / "js" / "scale-keyboard-data.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")


def run_scale_node(body: str) -> dict[str, object]:
    source = f"""
const assert = require("node:assert/strict");
const {{ ScaleKeyboardGame, SCALE_KEYBOARD_RULES, midiToFrequency }} =
  require("./static/js/scale-keyboard.js");
const {{ SCALE_KEYBOARD_SCALES }} = require("./static/js/scale-keyboard-data.js");
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


def test_black_keys_use_one_small_shared_rightward_offset() -> None:
    keyboard = CSS[CSS.index(".scale-piano {"):CSS.index(".scale-keyboard-actions {")]

    assert "--scale-black-key-shift: 0.22rem" in keyboard
    assert "translateX(calc(-50% + var(--scale-black-key-shift)))" in keyboard
    assert "translate(calc(-50% + var(--scale-black-key-shift)), 3px)" in keyboard
    assert "transform: translateX(-50%)" not in keyboard


@pytest.fixture()
def scale_database(monkeypatch: pytest.MonkeyPatch):
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


def signed_client(factory):
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id="WC-SCALE-PLAYER",
            display_name="Scale Player",
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
            state_json={"progress": {"credits": 5}},
            revision=0,
        ))
        session.commit()
    client = TestClient(app)
    assert client.post(
        "/account/login",
        data={"woodchuck_id": "WC-SCALE-PLAYER", "pin": "2468"},
    ).status_code == 200
    return client


def test_fifth_cabinet_and_authenticated_route(scale_database) -> None:
    assert ARCADE.count('class="arcade-cabinet ') == 8
    assert 'href="/arcade/scale-keyboard"' in ARCADE
    assert 'data-arcade-personal-best="scale-keyboard"' in ARCADE
    assert "1🌼 TO PLAY · WIN UP TO 5🌼" in ARCADE
    anonymous = TestClient(app).get("/arcade/scale-keyboard", follow_redirects=False)
    authenticated = signed_client(scale_database).get("/arcade/scale-keyboard")
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    assert authenticated.status_code == 200
    assert 'data-scale-keyboard' in authenticated.text


def test_scale_pool_has_correct_common_major_pitch_sequences() -> None:
    result = run_scale_node("""
assert.equal(SCALE_KEYBOARD_SCALES.length, 7);
const expected = [0, 2, 4, 5, 7, 9, 11, 12];
for (const scale of SCALE_KEYBOARD_SCALES) {
  assert.deepEqual(scale.notes.map((note) => note[0]), expected);
  assert.equal(scale.notes.length, 8);
  assert.match(scale.notes[0][1], /^[A-G]$/);
}
assert.deepEqual(
  SCALE_KEYBOARD_SCALES.map((scale) => scale.notes[0][1]).sort(),
  ["A", "B", "C", "D", "E", "F", "G"]
);
const spellings = Object.fromEntries(
  SCALE_KEYBOARD_SCALES.map((scale) => [scale.name, scale.notes.map((note) => note[1])])
);
assert.deepEqual(spellings["C Major"], ["C", "D", "E", "F", "G", "A", "B", "C"]);
assert.deepEqual(spellings["G Major"], ["G", "A", "B", "C", "D", "E", "F♯", "G"]);
assert.deepEqual(spellings["D Major"], ["D", "E", "F♯", "G", "A", "B", "C♯", "D"]);
assert.deepEqual(spellings["A Major"], ["A", "B", "C♯", "D", "E", "F♯", "G♯", "A"]);
assert.deepEqual(spellings["E Major"], ["E", "F♯", "G♯", "A", "B", "C♯", "D♯", "E"]);
assert.deepEqual(spellings["B Major"], ["B", "C♯", "D♯", "E", "F♯", "G♯", "A♯", "B"]);
assert.deepEqual(spellings["F Major"], ["F", "G", "A", "B♭", "C", "D", "E", "F"]);
console.log(JSON.stringify({ count: SCALE_KEYBOARD_SCALES.length, tonics:
  SCALE_KEYBOARD_SCALES.map((scale) => scale.notes[0][1]).sort() }));
""")
    assert result == {"count": 7, "tonics": ["A", "B", "C", "D", "E", "F", "G"]}


def test_displayed_midi_notes_map_to_concert_pitch_with_a4_440() -> None:
    result = run_scale_node("""
function close(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 0.001, `${actual} != ${expected}`);
}
close(midiToFrequency(60), 261.625565); // C4
close(midiToFrequency(64), 329.627557); // E4
close(midiToFrequency(69), 440);        // A4
close(midiToFrequency(70), 466.163762); // B-flat4 / A-sharp4
assert.equal(midiToFrequency("not-a-note"), null);
console.log(JSON.stringify({ a4: midiToFrequency(69) }));
""")
    assert result == {"a4": 440}


def test_thirty_second_run_correct_progress_wrong_penalty_and_completion() -> None:
    result = run_scale_node("""
const game = new ScaleKeyboardGame({ scales: [SCALE_KEYBOARD_SCALES[0]], random: () => 0 });
game.start();
assert.equal(game.remainingMs, 30000);
assert.equal(game.press(61).correct, false);
assert.equal(game.score, 0);
const expected = game.currentScale.notes.map((note) => game.currentScale.rootMidi + note[0]);
for (const midi of expected) assert.equal(game.press(midi).correct, true);
assert.equal(game.noteIndex, 8);
assert.equal(game.score, 1300);
game.elapse(30000);
assert.equal(game.status, "ended");
console.log(JSON.stringify({ score: game.score, status: game.status }));
""")
    assert result == {"score": 1300, "status": "ended"}


def test_wrong_note_penalty_floors_at_zero_and_does_not_advance() -> None:
    result = run_scale_node("""
const game = new ScaleKeyboardGame({ scales: [SCALE_KEYBOARD_SCALES[0]], random: () => 0 });
game.start();
game.press(61);
assert.equal(game.noteIndex, 0);
assert.equal(game.score, 0);
game.press(60);
game.press(61);
assert.equal(game.noteIndex, 1);
assert.equal(game.score, 50);
console.log(JSON.stringify({ score: game.score, progress: game.noteIndex }));
""")
    assert result == {"score": 50, "progress": 1}


def test_enharmonic_keys_compare_by_midi_and_remain_octave_sensitive() -> None:
    result = run_scale_node("""
const fMajor = SCALE_KEYBOARD_SCALES.find((scale) => scale.key === "f-major");
const fGame = new ScaleKeyboardGame({ scales: [fMajor], random: () => 0 });
fGame.start();
for (const midi of [53, 55, 57]) assert.equal(fGame.press(midi).correct, true);
assert.equal(fGame.expectedMidi(), 58); // B-flat3 and A-sharp3 are physical MIDI key 58.
assert.equal(fGame.press("58").correct, true); // DOM dataset values normalize to MIDI.

const bMajor = SCALE_KEYBOARD_SCALES.find((scale) => scale.key === "b-major");
const bGame = new ScaleKeyboardGame({ scales: [bMajor], random: () => 0 });
bGame.start();
for (const midi of [59, 61, 63, 64, 66, 68]) assert.equal(bGame.press(midi).correct, true);
assert.equal(bGame.expectedMidi(), 70); // A-sharp4 and B-flat4 are physical MIDI key 70.
assert.equal(bGame.press(70).correct, true);

const cMajor = SCALE_KEYBOARD_SCALES.find((scale) => scale.key === "c-major");
const octaveGame = new ScaleKeyboardGame({ scales: [cMajor], random: () => 0 });
octaveGame.start();
assert.equal(octaveGame.press(72).correct, false); // C5 is not the requested C4.
assert.equal(octaveGame.noteIndex, 0);
console.log(JSON.stringify({ bFlat: 58, aSharp: 70, octaveProgress: octaveGame.noteIndex }));
""")
    assert result == {"bFlat": 58, "aSharp": 70, "octaveProgress": 0}


def test_all_starter_scales_complete_from_their_midi_sequences() -> None:
    result = run_scale_node("""
for (const scale of SCALE_KEYBOARD_SCALES) {
  const game = new ScaleKeyboardGame({ scales: [scale], random: () => 0 });
  game.start();
  for (const [offset] of scale.notes) {
    const result = game.press(scale.rootMidi + offset);
    assert.equal(result.correct, true, `${scale.name} failed at MIDI ${scale.rootMidi + offset}`);
  }
  assert.equal(game.noteIndex, scale.notes.length);
  assert.equal(game.score, 1300);
}
console.log(JSON.stringify({ completed: SCALE_KEYBOARD_SCALES.length }));
""")
    assert result == {"completed": 7}


def test_multiple_scales_do_not_immediately_repeat_and_clock_is_run_wide() -> None:
    result = run_scale_node("""
const game = new ScaleKeyboardGame({ scales: SCALE_KEYBOARD_SCALES.slice(0, 2), random: () => 0 });
game.start();
const first = game.currentScale.key;
game.elapse(7250);
game.chooseScale();
assert.notEqual(game.currentScale.key, first);
assert.equal(game.remainingMs, 22750);
console.log(JSON.stringify({ first, second: game.currentScale.key, remaining: game.remainingMs }));
""")
    assert result["first"] != result["second"]
    assert result["remaining"] == 22750


def test_keyboard_markup_is_touch_friendly_and_feedback_uses_shared_audio() -> None:
    assert 'id="scale-keyboard-keys"' in TEMPLATE
    assert 'aria-label="Piano keyboard"' in TEMPLATE
    assert "Array.from({ length: 18 }" in GAME_JS
    assert 'className = "scale-piano-key is-white"' in GAME_JS
    assert 'className = "scale-piano-key is-black"' in GAME_JS
    assert 'root.WoodshedAudio.play(name)' in GAME_JS
    assert 'playFeedback("arcadeCheer")' in GAME_JS
    assert "root.WoodshedAudio.playPianoPitch(midiToFrequency(midi))" in GAME_JS
    assert 'playFeedback(result.correct ? "correctTrivia" : "incorrectTrivia")' not in GAME_JS
    assert GAME_JS.count("root.WoodshedAudio.playPianoPitch(midiToFrequency(midi))") == 1
    assert GAME_JS.index("if (!result.accepted) return") < GAME_JS.index(
        "root.WoodshedAudio.playPianoPitch(midiToFrequency(midi))"
    )
    assert "touch-action: manipulation" in CSS


def test_score_completion_uses_one_play_token_and_persistent_top_five() -> None:
    assert 'startPlay("scale-keyboard")' in GAME_JS
    assert "completePlay(activePlayToken, game.score)" in GAME_JS
    assert "if (finishPromise) return finishPromise" in GAME_JS
    assert 'fetch("/arcade/scores/scale-keyboard"' in GAME_JS
    assert 'data-arcade-leaderboard="scale-keyboard"' in TEMPLATE
    assert "arcade_high_scores" not in DATA_JS


def test_scale_personal_best_persists_through_play_api(scale_database) -> None:
    client = signed_client(scale_database)
    started = client.post("/arcade/plays", json={"game_key": "scale-keyboard"})
    assert started.status_code == 200
    completed = client.post(
        f"/arcade/plays/{started.json()['play_token']}/complete",
        json={"score": 1800},
    )
    assert completed.status_code == 200
    assert completed.json()["best_score"] == 1800
    assert completed.json()["leaderboard"] == [{
        "rank": 1,
        "display_name": "Scale Player",
        "score": 1800,
        "is_current_user": True,
    }]
    second_device = TestClient(app)
    assert second_device.post(
        "/account/login",
        data={"woodchuck_id": "WC-SCALE-PLAYER", "pin": "2468"},
    ).status_code == 200
    assert second_device.get("/arcade/scores/scale-keyboard").json()["best_score"] == 1800
