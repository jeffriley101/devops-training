"""Execute the real shared controller: per-game mapping and repeat policy."""

import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
TRACKS = [
    ("scale-keyboard", "gerry-4.wav?v=1", True, False),
    ("thirds", "gerry-4.mp3?v=1", False, False),
    ("dressed-to-the-nines", "sand-drop.mp3?v=2", True, False),
    ("wheel-of-woodchuck", "mudslide.mp3?v=2", True, False),
    ("interval-basic-training", "black-hole-rappelling.mp3?v=2", True, False),
    ("history-mystery", "thunderpants.mp3?v=2", True, False),
    ("blue", "gerry-3.mp3?v=1", True, False),
    ("plunge-burrow", "jeremy-9.mp3?v=1", False, True),
    ("radio-tuner", "trouble.mp3?v=1", False, True),
]


@pytest.mark.parametrize("game,filename,loop,restart", TRACKS)
def test_game_mapping_and_end_of_track_behavior(game, filename, loop, restart):
    source = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const [game, filename, loop, restart] = JSON.parse(process.argv[1]);
const listeners = {};
const audioListeners = {};
const timers = new Map();
let timerId = 0;
let audio;
let plays = 0;
const document = {
  querySelector(selector) {
    return selector === "[data-arcade-soundtrack]"
      ? {dataset: {arcadeSoundtrack: game}} : null;
  },
  getElementById() { return null; },
  addEventListener(type, callback) { listeners[type] = callback; },
};
const window = {
  addEventListener() {},
  setTimeout(callback, delay) { timers.set(++timerId, {callback, delay}); return timerId; },
  clearTimeout(id) { timers.delete(id); },
  WoodshedAudio: {isEnabled: () => true, getVolume: () => 0.4},
};
class Audio {
  constructor(url) { this.src = url; this.paused = true; this.ended = false; audio = this; }
  addEventListener(type, callback) { audioListeners[type] = callback; }
  play() { plays++; this.paused = false; this.ended = false; return Promise.resolve(); }
  pause() { this.paused = true; }
}
vm.runInNewContext(fs.readFileSync("static/js/arcade-soundtrack.js", "utf8"), {
  document, window, Audio,
});
assert.equal(audio.src, "/static/audio/arcade/" + filename);
assert.equal(audio.loop, loop);
assert.equal(audio.preload, "auto");
assert.equal(Boolean(audioListeners.ended), restart);
listeners.DOMContentLoaded();
assert.equal(plays, 0, "no autoplay before user gesture");
listeners.pointerdown({});
assert.equal(plays, 1);
audio.paused = true;
audio.ended = true;
if (audioListeners.ended) audioListeners.ended();
assert.equal(timers.size, restart ? 1 : 0, "no hidden delayed loop for Thirds");
if (restart) {
  const [{callback, delay}] = timers.values();
  timers.clear();
  assert.equal(delay, 6000, "existing delayed replay is preserved");
  callback();
  assert.equal(plays, 2);
}
if (game === "thirds") {
  listeners.pointerdown({});
  listeners.keydown({});
  for (const active of [true, false]) {
    listeners["woodshed:arcade-soundtrack-run-state"]({detail: {gameKey: game, active}});
  }
  for (const {callback} of timers.values()) callback();
  assert.equal(plays, 1, "gestures/game resume must not replay an exhausted Thirds track");
}
'''
    result = subprocess.run(
        ["node", "-e", source, json.dumps([game, filename, loop, restart])],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("game,filename,loop,restart", TRACKS[:6])
def test_affected_audio_is_served_at_exact_url_with_audio_content_type(
    game, filename, loop, restart,
):
    # Static requests do not require login or touch application database state.
    response = TestClient(app).get("/static/audio/arcade/" + filename)
    assert response.status_code == 200
    assert response.headers["content-type"] in {"audio/mpeg", "audio/wav", "audio/x-wav"}
    assert response.content == (ROOT / "static/audio/arcade" / filename.split("?")[0]).read_bytes()
