from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "templates/plunge_burrow.html").read_text(encoding="utf-8")
BOARD = (ROOT / "templates/quest.html").read_text(encoding="utf-8")
BASE = (ROOT / "templates/base.html").read_text(encoding="utf-8")
GAME_JS = (ROOT / "static/js/plunge-burrow.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/styles.css").read_text(encoding="utf-8")


def run_game_core(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_game_route_navigation_and_page_only_script() -> None:
    response = TestClient(app).get("/plunge-burrow")
    assert response.status_code == 200
    assert "<title>Plunge Burrow · Woodshed Woodchuck</title>" in response.text
    assert 'href="/arcade">Back to Arcade</a>' in response.text
    assert 'href="/plunge-burrow"' in BOARD
    assert "/static/js/plunge-burrow.js?v=7" in TEMPLATE
    assert "/static/js/plunge-burrow.js" not in BASE
    route_guard = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    route_guard = route_guard[
        route_guard.index("function routeGuard"):
        route_guard.index("function hydrateHome")
    ]
    assert '"/plunge-burrow"' in route_guard
    assert '"/arcade"' in route_guard


def test_page_is_responsive_accessible_and_has_all_controls() -> None:
    assert 'id="plunge-canvas"' in TEMPLATE
    assert 'aria-label="Plunge Burrow game board.' in TEMPLATE
    assert 'aria-live="polite"' in TEMPLATE
    assert 'id="plunge-score"' in TEMPLATE
    assert 'id="plunge-best"' in TEMPLATE
    assert 'id="plunge-hearts"' in TEMPLATE
    for control in ("plunge-start", "plunge-pause", "plunge-restart"):
        assert f'id="{control}"' in TEMPLATE
    for direction in ("up", "down", "left", "right"):
        assert f'data-direction="{direction}"' in TEMPLATE
        assert f'aria-label="Move {direction}"' in TEMPLATE
    assert "min-width: 3.25rem" in CSS and "min-height: 3.25rem" in CSS
    assert "overflow-x" not in CSS[CSS.index("/* Plunge Burrow prototype */"):]
    assert "@media (prefers-reduced-motion: reduce)" in CSS


def test_plunge_uses_the_shared_arcade_soundtrack() -> None:
    assert "Soundtrack coming soon" not in TEMPLATE
    assert 'id="plunge-music-enabled"' not in TEMPLATE
    assert 'id="plunge-music-volume"' not in TEMPLATE
    assert '/static/js/arcade-soundtrack.js?v=3' in TEMPLATE
    assert 'data-arcade-soundtrack="plunge-burrow"' in TEMPLATE
    assert "SOUNDTRACK_URL" not in GAME_JS
    assert "startMusic" not in GAME_JS
    assert "Tone.Transport" not in GAME_JS
    assert "http://" not in GAME_JS + TEMPLATE
    assert "https://" not in GAME_JS + TEMPLATE


def test_initial_state_direction_growth_speed_pause_restart_and_best_score() -> None:
    result = run_game_core(
        r"""
const core = require('./static/js/plunge-burrow.js');
const store = { values: {}, getItem(k) { return this.values[k] ?? null; }, setItem(k, v) { this.values[k] = v; } };
const game = new core.PlungeBurrowGame({ random: () => 0.2, storage: store });
const initial = game.snapshot();
const reverseBlocked = game.setDirection('left') === false;
game.obstacles = [];
game.dandelion = { x: game.trail[0].x + 1, y: game.trail[0].y };
game.start(); game.tick();
const afterEat = game.snapshot();
game.pause(); const pausedHead = {...game.trail[0]}; game.tick();
const frozen = game.trail[0].x === pausedHead.x && game.trail[0].y === pausedHead.y;
game.resume();
let intervalAtFour = null; let intervalAtFive = null;
for (let i = 1; i < 80; i += 1) {
  game.trail = game.initialTrail(); game.direction = 'right'; game.queuedDirection = null;
  game.dandelion = { x: game.trail[0].x + 1, y: game.trail[0].y };
  game.tick();
  if (game.score === 4) intervalAtFour = game.interval;
  if (game.score === 5) intervalAtFive = game.interval;
}
const capped = game.interval === core.MIN_INTERVAL;
const bestBeforeGameOver = game.best;
game.hearts = 1; game.trail = [{x: 19, y: 10}, {x: 18, y: 10}]; game.direction = 'right'; game.tick();
const gameOver = game.status === 'gameover' && game.hearts === 0;
const bestSaved = game.best === game.score && Number(store.values[core.BEST_SCORE_KEY]) === game.score;
game.reset();
console.log(JSON.stringify({
  initialLength: initial.trail.length, initialHearts: initial.hearts,
  validInitial: new Set(initial.trail.map(c => `${c.x},${c.y}`)).size === initial.trail.length,
  reverseBlocked, scoreAfterEat: afterEat.score, lengthAfterEat: afterEat.trail.length,
  frozen, intervalAtFour, intervalAtFive,
  capped, bestBeforeGameOver, gameOver, bestSaved,
  restartScore: game.score, restartHearts: game.hearts, restartStatus: game.status
}));
"""
    )
    assert result == {
        "initialLength": 3,
        "initialHearts": 3,
        "validInitial": True,
        "reverseBlocked": True,
        "scoreAfterEat": 1,
        "lengthAfterEat": 4,
        "frozen": True,
        "intervalAtFour": 220,
        "intervalAtFive": 204,
        "capped": True,
        "bestBeforeGameOver": 0,
        "gameOver": True,
        "bestSaved": True,
        "restartScore": 0,
        "restartHearts": 3,
        "restartStatus": "ready",
    }


def test_each_collision_costs_exactly_one_heart_and_respawns_safely() -> None:
    result = run_game_core(
        r"""
const {PlungeBurrowGame} = require('./static/js/plunge-burrow.js');
function collide(type) {
  const game = new PlungeBurrowGame({random: () => 0.3}); game.obstacles = []; game.start();
  if (type === 'wall') game.trail = [{x:19,y:10},{x:18,y:10}];
  if (type === 'self') game.trail = [{x:10,y:10},{x:11,y:10},{x:11,y:11},{x:10,y:11},{x:9,y:11}];
  if (type === 'self') game.direction = 'down';
  if (type === 'rock' || type === 'root') game.obstacles = [{x:11,y:10,type}];
  game.dandelion = {x:0,y:0}; game.tick();
  const once = game.hearts; const safe = game.trail.every(c => !game.obstacles.some(o => o.x === c.x && o.y === c.y));
  return {once, safe, length: game.trail.length};
}
console.log(JSON.stringify({wall:collide('wall'),self:collide('self'),rock:collide('rock'),root:collide('root')}));
"""
    )
    for collision in result.values():
        assert collision == {"once": 2, "safe": True, "length": 3}


def test_spawn_obstacles_storage_and_audio_are_safe_and_local_only() -> None:
    result = run_game_core(
        r"""
const {PlungeBurrowGame} = require('./static/js/plunge-burrow.js');
const broken = {getItem(){throw Error('blocked')},setItem(){throw Error('blocked')}};
const game = new PlungeBurrowGame({random:()=>0.42,storage:broken});
const occupied = new Set([...game.trail,...game.obstacles].map(c=>`${c.x},${c.y}`));
console.log(JSON.stringify({best:game.best,dandelionEmpty:!occupied.has(`${game.dandelion.x},${game.dandelion.y}`),rocks:game.obstacles.some(o=>o.type==='rock'),roots:game.obstacles.some(o=>o.type==='root')}));
"""
    )
    assert result == {"best": 0, "dandelionEmpty": True, "rocks": True, "roots": True}
    assert "root.WoodshedAudio" in GAME_JS
    assert "try { if (root.WoodshedAudio)" in GAME_JS
    assert 'playEffect("dandelionEarned")' in GAME_JS
    assert 'playEffect("incorrectTrivia")' in GAME_JS
    reporter = GAME_JS[GAME_JS.index("function reportScoringEvent"):GAME_JS.index("function renderLeaderboard")]
    assert "await" not in reporter
    assert "supplemental and must never interrupt the game" in reporter
    assert 'playEffect("' not in GAME_JS[: GAME_JS.index("const game = new PlungeBurrowGame")]


def test_running_game_keeps_one_dandelion_after_normal_collection() -> None:
    result = run_game_core(
        r"""
const {PlungeBurrowGame} = require('./static/js/plunge-burrow.js');
const game = new PlungeBurrowGame({random: () => 0.31});
game.obstacles = []; game.portals = []; game.carrot = null; game.instrument = null;
game.dandelion = {x: game.trail[0].x + 1, y: game.trail[0].y};
game.start(); game.tick();
const dandelionOnTrail = game.dandelion && game.trail.some((cell) => cell.x === game.dandelion.x && cell.y === game.dandelion.y);
console.log(JSON.stringify({score: game.score, length: game.trail.length, dandelion: Boolean(game.dandelion), dandelionOnTrail}));
"""
    )
    assert result == {"score": 1, "length": 4, "dandelion": True, "dandelionOnTrail": False}


def test_full_grid_collection_releases_one_tail_cell_for_a_dandelion() -> None:
    result = run_game_core(
        r"""
const {PlungeBurrowGame} = require('./static/js/plunge-burrow.js');
const game = new PlungeBurrowGame({random: () => 0.25});
game.obstacles = []; game.portals = []; game.carrot = null; game.instrument = null;
game.trail = [{x: 10, y: 10}];
for (let y = 0; y < game.gridSize; y += 1) {
  for (let x = 0; x < game.gridSize; x += 1) {
    if ((x === 10 && y === 10) || (x === 11 && y === 10)) continue;
    game.trail.push({x, y});
  }
}
const tailBefore = {...game.trail[game.trail.length - 1]};
game.dandelion = {x: 11, y: 10}; game.direction = 'right'; game.pendingGrowth = 0;
game.start(); game.tick();
const dandelionOnTrail = game.trail.some((cell) => cell.x === game.dandelion.x && cell.y === game.dandelion.y);
console.log(JSON.stringify({
  status: game.status, score: game.score, trailLength: game.trail.length,
  dandelion: game.dandelion, tailBefore, dandelionOnTrail,
}));
"""
    )
    assert result["status"] == "running"
    assert result["score"] == 1
    assert result["trailLength"] == 399
    assert result["dandelion"] == result["tailBefore"]
    assert result["dandelionOnTrail"] is False


def test_collision_relocation_and_deterministic_running_ticks_keep_a_dandelion() -> None:
    result = run_game_core(
        r"""
const {PlungeBurrowGame} = require('./static/js/plunge-burrow.js');
const game = new PlungeBurrowGame({random: () => 0.41});
game.obstacles = []; game.portals = [];
game.dandelion = {x: 10, y: 10};
game.trail = [{x: 19, y: 10}, {x: 18, y: 10}]; game.direction = 'right';
game.start(); game.tick();
const afterCollision = Boolean(game.dandelion) && !game.trail.some((cell) => cell.x === game.dandelion.x && cell.y === game.dandelion.y);
let allRunningSnapshotsHaveDandelion = afterCollision;
for (let index = 0; index < 20; index += 1) {
  game.status = 'running'; game.trail = game.initialTrail(); game.direction = 'right';
  game.queuedDirection = null; game.pendingGrowth = 0; game.obstacles = []; game.portals = [];
  game.carrot = null; game.instrument = null;
  game.dandelion = {x: game.trail[0].x + 1, y: game.trail[0].y};
  game.tick();
  allRunningSnapshotsHaveDandelion = allRunningSnapshotsHaveDandelion && Boolean(game.dandelion);
}
console.log(JSON.stringify({afterCollision, allRunningSnapshotsHaveDandelion}));
"""
    )
    assert result == {"afterCollision": True, "allRunningSnapshotsHaveDandelion": True}


def test_lifecycle_inputs_and_single_animation_loop_are_present() -> None:
    assert "requestAnimationFrame(animate)" in GAME_JS
    assert "if (frameId === null" in GAME_JS
    assert 'document.addEventListener("visibilitychange"' in GAME_JS
    assert "document.hidden && game.pause()" in GAME_JS
    assert 'root.addEventListener("pagehide"' in GAME_JS
    assert 'document.addEventListener("keydown"' in GAME_JS
    assert "ArrowUp" in GAME_JS and 'w: "up"' in GAME_JS
    assert 'canvas.addEventListener("touchstart"' in GAME_JS
    assert 'canvas.addEventListener("touchend"' in GAME_JS
    assert "event.preventDefault()" in GAME_JS


def test_plunge_persists_only_score_events_without_account_reward_side_effects() -> None:
    combined = TEMPLATE + GAME_JS
    assert 'root.fetch("/xp/plunge-points"' in GAME_JS
    assert 'event_key: eventKey' in GAME_JS
    assert 'event_type: eventType' in GAME_JS
    assert 'points_scored: pointsScored' in GAME_JS
    assert "occurred_at" not in GAME_JS
    assert "activity_date" not in GAME_JS
    assert "XMLHttpRequest" not in combined
    assert "/account/state" not in combined
    assert "RewardGrant" not in combined
    assert "CampPoint" not in combined
    assert "crown" not in combined.casefold()
    assert "alembic" not in combined.casefold()


def test_visible_game_score_continues_past_daily_xp_cap() -> None:
    result = run_game_core(
        r"""
const {PlungeBurrowGame} = require('./static/js/plunge-burrow.js');
const game = new PlungeBurrowGame({random: () => 0.31});
game.obstacles = []; game.portals = []; game.start();
for (let index = 0; index < 12; index += 1) {
  game.trail = game.initialTrail();
  game.direction = 'right'; game.queuedDirection = null;
  game.dandelion = {x: game.trail[0].x + 1, y: game.trail[0].y};
  game.tick();
}
console.log(JSON.stringify({score: game.score, dandelions: game.dandelionsCollected}));
"""
    )
    assert result == {"score": 12, "dandelions": 12}
    assert 'reportScoringEvent("dandelion", 1)' in GAME_JS
    assert 'reportScoringEvent("carrot", 3)' in GAME_JS
    assert 'reportScoringEvent("instrument", 5)' in GAME_JS
    assert 'reportScoringEvent("band_complete", 20)' in GAME_JS
