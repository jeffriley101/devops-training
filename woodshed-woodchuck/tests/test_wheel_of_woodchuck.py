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
WHEEL = (ROOT / "templates" / "wheel_of_woodchuck.html").read_text(
    encoding="utf-8"
)
WHEEL_JS = (ROOT / "static" / "js" / "wheel-of-woodchuck.js").read_text(
    encoding="utf-8"
)
TERMS_JS = (ROOT / "static" / "js" / "wheel-terms.js").read_text(
    encoding="utf-8"
)
AUDIO_JS = (ROOT / "static" / "js" / "audio.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")


def run_wheel_node(body: str) -> dict[str, object]:
    source = f"""
const assert = require("node:assert/strict");
const {{ WheelOfWoodchuckGame, WHEEL_OF_WOODCHUCK_RULES }} =
  require("./static/js/wheel-of-woodchuck.js");
const {{ WHEEL_OF_WOODCHUCK_TERMS }} = require("./static/js/wheel-terms.js");
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
def wheel_database(monkeypatch: pytest.MonkeyPatch):
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


def signed_wheel_client(factory, suffix: str, name: str = "Wheel Player"):
    with factory() as session:
        profile = WoodchuckProfile(
            woodchuck_id=f"WC-WHEEL-{suffix}",
            display_name=name,
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
            state_json={"progress": {"credits": 20}},
            revision=0,
        ))
        session.commit()
    client = TestClient(app)
    assert client.post(
        "/account/login",
        data={"woodchuck_id": profile.woodchuck_id, "pin": "2468"},
    ).status_code == 200
    return client, profile


def submit_paid_score(client: TestClient, game_key: str, score: int):
    started = client.post("/arcade/plays", json={"game_key": game_key})
    assert started.status_code == 200
    return client.post(
        f"/arcade/scores/{game_key}",
        json={"score": score, "play_token": started.json()["play_token"]},
    )


def test_fourth_cabinet_and_authenticated_game_route(wheel_database) -> None:
    assert 'class="arcade-cabinet arcade-cabinet-wheel"' in ARCADE
    assert 'href="/arcade/wheel-of-woodchuck"' in ARCADE
    assert "WHEEL OF WOODCHUCK" in ARCADE.upper()
    assert 'data-arcade-personal-best="wheel-of-woodchuck"' in ARCADE
    assert '<p>Time <strong id="wheel-time">45</strong></p>' in WHEEL
    assert '<p>Misses <strong id="wheel-misses">0/3</strong></p>' in WHEEL
    assert '/static/js/wheel-of-woodchuck.js?v=4' in WHEEL
    assert "const RUN_DURATION_MS = 45000" in WHEEL_JS

    anonymous = TestClient(app).get(
        "/arcade/wheel-of-woodchuck", follow_redirects=False
    )
    client, _profile = signed_wheel_client(wheel_database, "ROUTE")
    authenticated = client.get("/arcade/wheel-of-woodchuck")

    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/login"
    assert authenticated.status_code == 200
    assert "Wheel of Woodchuck" in authenticated.text
    assert 'data-wheel-of-woodchuck' in authenticated.text


def test_music_term_dataset_is_reusable_and_uses_plain_answers() -> None:
    result = run_wheel_node("""
assert.equal(WHEEL_OF_WOODCHUCK_TERMS.length, 31);
for (const term of WHEEL_OF_WOODCHUCK_TERMS) {
  assert.match(term.answer, /^[a-z]{5,}$/);
  assert.equal(typeof term.definition, "string");
  assert.ok(term.definition.length > 8);
}
console.log(JSON.stringify({ count: WHEEL_OF_WOODCHUCK_TERMS.length }));
""")
    assert result == {"count": 31}
    assert "-" not in "".join(
        line.split('answer: "', 1)[1].split('"', 1)[0]
        for line in TERMS_JS.splitlines()
        if 'answer: "' in line
    )


def test_spin_is_required_for_each_letter_and_used_letters_are_safe() -> None:
    result = run_wheel_node("""
const values = [0, 0.13, 0.13];
const game = new WheelOfWoodchuckGame({
  terms: [{ answer: "tempo", definition: "Speed" }],
  random: () => values.shift() ?? 0.13,
});
game.start();
assert.equal(game.remainingMs, 45000);
assert.equal(game.guessLetter("Z").reason, "spin-required");
game.startSpin(); game.completeSpin();
assert.equal(game.guessLetter("Z").misses, 1);
assert.equal(game.remainingMs, 45000);
assert.equal(game.guessLetter("Z").reason, "already-guessed");
assert.equal(game.misses, 1);
game.startSpin(); game.completeSpin();
game.guessLetter("T");
assert.equal(game.canGuessLetter, false);
assert.equal(game.guessLetter("E").reason, "spin-required");
console.log(JSON.stringify({ remaining: game.remainingMs, misses: game.misses }));
""")
    assert result == {"remaining": 45000, "misses": 1}


def test_numeric_spin_scores_each_repeated_letter_occurrence() -> None:
    result = run_wheel_node("""
const values = [0, 0.4];
const game = new WheelOfWoodchuckGame({
  terms: [{ answer: "fermata", definition: "Hold" }],
  random: () => values.shift() ?? 0,
});
game.start();
const spin = game.startSpin();
assert.equal(spin.letterValue, 350);
game.completeSpin();
const guess = game.guessLetter("A");
assert.equal(guess.occurrences, 2);
assert.equal(guess.gained, 700);
assert.equal(game.score, 700);
console.log(JSON.stringify({ score: game.score }));
""")
    assert result == {"score": 700}


def test_revealing_every_letter_awards_the_fixed_solve_bonus() -> None:
    result = run_wheel_node("""
let calls = 0;
const game = new WheelOfWoodchuckGame({
  terms: [{ answer: "tempo", definition: "Speed" }],
  random: () => calls++ === 0 ? 0 : 0.13,
});
game.start();
for (const letter of ["T", "E", "M", "P", "O"]) {
  game.startSpin(); game.completeSpin(); game.guessLetter(letter);
}
assert.equal(game.puzzleState, "solved");
assert.equal(game.score, 6000);
console.log(JSON.stringify({ score: game.score }));
""")
    assert result == {"score": 6000}


def test_three_times_segment_resolves_numeric_result_and_triples_letter_value() -> None:
    result = run_wheel_node("""
const values = [0, 0.8, 0.4];
const game = new WheelOfWoodchuckGame({
  terms: [{ answer: "tempo", definition: "Speed" }],
  random: () => values.shift() ?? 0,
});
game.start();
const spin = game.startSpin();
assert.equal(spin.segment, "3X");
assert.equal(spin.numericResult, 350);
assert.equal(spin.letterValue, 1050);
game.completeSpin();
assert.equal(game.guessLetter("O").gained, 1050);
assert.equal(game.score, 1050);
console.log(JSON.stringify(spin));
""")
    assert result == {"segment": "3X", "numericResult": 350, "letterValue": 1050}
    assert "3X bonus: spinning for the numeric value" in WHEEL_JS


def test_pointer_stays_fixed_and_wheel_content_counter_rotates_upright() -> None:
    spinner = WHEEL.index('id="wheel-spinner"')
    spinner_close = WHEEL.index("</div>", spinner)
    pointer = WHEEL.index('class="wheel-pointer"')
    assert spinner_close < pointer
    assert "function setWheelRotation(turns)" in WHEEL_JS
    assert '`${-turns}turn`' in WHEEL_JS
    assert "setWheelRotation(wheelTurns)" in WHEEL_JS
    assert "--wheel-content-counter-rotation: 0turn" in CSS
    assert CSS.count("rotate(var(--wheel-content-counter-rotation))") == 2
    assert ".wheel-pointer {" in CSS
    assert "WHEEL_SEGMENTS.indexOf(result.segment) / WHEEL_SEGMENTS.length" in WHEEL_JS
    assert "WHEEL_SEGMENTS.indexOf(result.numericResult) /" in WHEEL_JS


def test_active_game_area_keeps_locked_actions_together_and_leaderboard_below() -> None:
    active_start = WHEEL.index('id="wheel-active-area"')
    active_end = WHEEL.index("</section>", active_start)
    active = WHEEL[active_start:active_end]
    spin = active.index('id="wheel-spin"')
    spell = active.index('id="wheel-spell-open"')
    new_game = active.index('id="wheel-start"')

    assert spin < spell < new_game
    assert 'class="wheel-action-stack"' in active
    assert 'class="wheel-game-summary"' in active
    assert 'class="wheel-visual"' in active
    assert 'class="wheel-control-panel"' in active
    assert 'id="wheel-spell-form"' in active
    assert active_end < WHEEL.index('class="arcade-game-leaderboard"')
    assert 'grid-template-areas:\n    "wheel summary"\n    "wheel controls"\n    "letters letters"' in CSS
    assert '"summary summary"\n      "wheel controls"\n      "letters letters"' in CSS
    assert 'activeArea?.scrollIntoView({ block: "nearest" })' in WHEEL_JS


def test_exact_slice_order_and_misses_replace_spelling_attempts_and_time_penalty() -> None:
    slice_labels = ["MISS", "1000", "50", "350", "500", "100", "3X", "200"]
    spinner_start = WHEEL.index('id="wheel-spinner"')
    spinner_end = WHEEL.index("</div>", spinner_start)
    spinner = WHEEL[spinner_start:spinner_end]
    assert [spinner.index(f"<span>{label}</span>") for label in slice_labels] == sorted(
        spinner.index(f"<span>{label}</span>") for label in slice_labels
    )
    result = run_wheel_node("""
assert.deepEqual(WHEEL_OF_WOODCHUCK_RULES.wheelSegments,
  ["MISS", 1000, 50, 350, 500, 100, "3X", 200]);
const values = [0, 0.13, 0];
const game = new WheelOfWoodchuckGame({
  terms: [{ answer: "crescendo", definition: "Louder" }],
  random: () => values.shift() ?? 0,
});
game.start();
assert.equal(game.canGuessLetter, false);
game.startSpin(); game.completeSpin();
assert.equal(game.guessLetter("Z").misses, 1);
assert.equal(game.remainingMs, 45000);
assert.equal(game.spell("nope").misses, 2);
assert.equal(game.remainingMs, 45000);
const missSpin = game.startSpin();
assert.equal(missSpin.segment, "MISS");
const missResult = game.completeSpin();
assert.equal(missResult.misses, 3);
assert.equal(missResult.exhausted, true);
assert.equal(game.puzzleState, "failed");

const solved = new WheelOfWoodchuckGame({
  terms: [{ answer: "crescendo", definition: "Louder" }], random: () => 0,
});
solved.start();
assert.equal(solved.spell("  CrEsCeNdO  ").correct, true);
assert.equal(solved.score, 1000);
console.log(JSON.stringify({ misses: game.misses, remaining: game.remainingMs, score: solved.score }));
""")
    assert result == {"misses": 3, "remaining": 45000, "score": 1000}
    assert "WRONG_LETTER_PENALTY_MS" not in WHEEL_JS
    assert "spellingAttempts" not in WHEEL_JS


def test_three_misses_advance_to_a_new_term_and_reset_term_misses() -> None:
    result = run_wheel_node("""
const values = [0, 0.9];
const game = new WheelOfWoodchuckGame({
  terms: [
    { answer: "tempo", definition: "Speed" },
    { answer: "legato", definition: "Connected" },
  ],
  random: () => values.shift() ?? 0,
});
game.start();
const first = game.currentTerm.answer;
game.spell("wrong"); game.spell("wrong"); game.spell("wrong");
assert.equal(game.puzzleState, "failed");
assert.equal(game.status, "running");
assert.equal(game.score, 0);
game.loadNextTerm();
assert.notEqual(game.currentTerm.answer, first);
assert.equal(game.misses, 0);
assert.equal(game.remainingMs, 45000);
console.log(JSON.stringify({ misses: game.misses, remaining: game.remainingMs }));
""")
    assert result == {"misses": 0, "remaining": 45000}
    assert "if (result.exhausted)" in WHEEL_JS
    assert "advanceAfterPuzzle" in WHEEL_JS


def test_solve_bonus_next_term_and_timer_are_run_wide() -> None:
    result = run_wheel_node("""
const values = [0, 0.9];
const terms = [
  { answer: "tempo", definition: "Speed" },
  { answer: "legato", definition: "Connected" },
];
const game = new WheelOfWoodchuckGame({ terms, random: () => values.shift() ?? 0 });
game.start();
const first = game.currentTerm.answer;
game.elapse(7250);
game.spell(first);
assert.equal(game.score, 1000);
game.loadNextTerm();
assert.notEqual(game.currentTerm.answer, first);
assert.equal(game.remainingMs, 37750);
console.log(JSON.stringify({ next: game.currentTerm.answer, remaining: game.remainingMs }));
""")
    assert result["remaining"] == 37750


def test_cheer_uses_shared_audio_and_final_score_submission_is_guarded() -> None:
    assert 'root.WoodshedAudio.play("arcadeCheer")' in WHEEL_JS
    assert 'root.WoodshedAudio.unlock()' in WHEEL_JS
    assert '"arcadeCheer"' in AUDIO_JS
    assert "const crowd = new Tone.NoiseSynth" in AUDIO_JS
    assert "const crowdFilter = new Tone.Filter" in AUDIO_JS
    assert "function submitFinalScoreOnce()" in WHEEL_JS
    assert "if (finishPromise) return finishPromise" in WHEEL_JS
    assert "WoodshedArcadeEconomy.completePlay" in WHEEL_JS
    assert "activePlayToken" in WHEEL_JS
    assert 'data-arcade-soundtrack' not in WHEEL
    assert "/static/js/arcade-soundtrack.js" not in WHEEL


def test_wheel_scores_persist_only_higher_and_keep_ties_private_safe(
    wheel_database,
) -> None:
    current, _profile = signed_wheel_client(wheel_database, "CUR", "Current")
    alpha, _ = signed_wheel_client(wheel_database, "ALPHA", "Alpha")
    zulu, _ = signed_wheel_client(wheel_database, "ZULU", "Zulu")

    assert submit_paid_score(current, "wheel-of-woodchuck", 1200).json()["updated"] is True
    assert submit_paid_score(current, "wheel-of-woodchuck", 800).json()["updated"] is False
    assert submit_paid_score(alpha, "wheel-of-woodchuck", 1500).status_code == 200
    assert submit_paid_score(zulu, "wheel-of-woodchuck", 1500).status_code == 200

    payload = current.get("/arcade/scores/wheel-of-woodchuck").json()
    assert payload["best_score"] == 1200
    assert [(row["rank"], row["display_name"]) for row in payload["leaderboard"]] == [
        (1, "Alpha"),
        (1, "Zulu"),
        (3, "Current"),
    ]
    assert [row["is_current_user"] for row in payload["leaderboard"]] == [
        False,
        False,
        True,
    ]


def test_existing_blue_and_radio_game_keys_remain_available(wheel_database) -> None:
    client, _profile = signed_wheel_client(wheel_database, "OLDKEYS")
    for key in ("blue", "radio-tuner"):
        response = submit_paid_score(client, key, 25)
        assert response.status_code == 200
        assert response.json()["best_score"] == 25


def test_wheel_migration_extends_only_the_arcade_game_key_constraint() -> None:
    migration = (
        ROOT
        / "migrations"
        / "versions"
        / "b3c4d5e6f7a8_add_wheel_arcade_score.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "a2b3c4d5e6f7"' in migration
    assert migration.count('batch_alter_table("arcade_high_scores")') == 2
    assert "wheel-of-woodchuck" in migration
