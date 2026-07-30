(function (root) {
  "use strict";

  const GRID_SIZE = 20;
  const START_HEARTS = 3;
  const START_INTERVAL = 220;
  const SPEED_STEP = 16;
  const SPEED_MILESTONE = 5;
  const MIN_INTERVAL = 105;
  const BEST_SCORE_KEY = "woodshed.plungeBurrow.bestScore";
  const MUSIC_ENABLED_KEY = "woodshed.plungeBurrow.musicEnabled";
  const MUSIC_VOLUME_KEY = "woodshed.plungeBurrow.musicVolume";
  const SOUNDTRACK_URL = ""; // Configure a future approved local file here.
  const VECTORS = {
    up: { x: 0, y: -1 }, down: { x: 0, y: 1 },
    left: { x: -1, y: 0 }, right: { x: 1, y: 0 },
  };
  const OPPOSITE = { up: "down", down: "up", left: "right", right: "left" };

  function sameCell(a, b) { return a.x === b.x && a.y === b.y; }
  function cellKey(cell) { return `${cell.x},${cell.y}`; }

  class PlungeBurrowGame {
    constructor(options) {
      const config = options || {};
      this.random = config.random || Math.random;
      this.onChange = config.onChange || function () {};
      this.onEvent = config.onEvent || function () {};
      this.storage = config.storage || null;
      this.gridSize = config.gridSize || GRID_SIZE;
      this.best = this.readBest();
      this.reset();
    }

    initialTrail() {
      const middle = Math.floor(this.gridSize / 2);
      return [
        { x: middle, y: middle },
        { x: middle - 1, y: middle },
        { x: middle - 2, y: middle },
      ];
    }

    reset() {
      this.score = 0;
      this.hearts = START_HEARTS;
      this.interval = START_INTERVAL;
      this.direction = "right";
      this.queuedDirection = null;
      this.trail = this.initialTrail();
      this.obstacles = this.generateObstacles();
      this.dandelion = this.spawnDandelion();
      this.status = "ready";
      this.hitLocked = false;
      this.onChange(this.snapshot());
      return this.snapshot();
    }

    snapshot() {
      return {
        score: this.score, best: this.best, hearts: this.hearts,
        interval: this.interval, direction: this.direction,
        trail: this.trail.map((cell) => ({ ...cell })),
        obstacles: this.obstacles.map((item) => ({ ...item })),
        dandelion: this.dandelion ? { ...this.dandelion } : null,
        status: this.status,
      };
    }

    start() {
      if (this.status === "gameover") return false;
      this.status = "running";
      this.onEvent("start");
      this.onChange(this.snapshot());
      return true;
    }

    pause() {
      if (this.status !== "running") return false;
      this.status = "paused";
      this.onEvent("pause");
      this.onChange(this.snapshot());
      return true;
    }

    resume() {
      if (this.status !== "paused") return false;
      this.status = "running";
      this.onEvent("resume");
      this.onChange(this.snapshot());
      return true;
    }

    setDirection(next) {
      if (!VECTORS[next] || this.queuedDirection || OPPOSITE[this.direction] === next) return false;
      this.queuedDirection = next;
      return true;
    }

    tick() {
      if (this.status !== "running" || this.hitLocked) return false;
      if (this.queuedDirection) {
        this.direction = this.queuedDirection;
        this.queuedDirection = null;
      }
      const vector = VECTORS[this.direction];
      const head = this.trail[0];
      const next = { x: head.x + vector.x, y: head.y + vector.y };
      const ate = this.dandelion && sameCell(next, this.dandelion);
      const bodyToCheck = ate ? this.trail : this.trail.slice(0, -1);
      let collision = null;
      if (next.x < 0 || next.y < 0 || next.x >= this.gridSize || next.y >= this.gridSize) collision = "wall";
      else if (bodyToCheck.some((cell) => sameCell(cell, next))) collision = "self";
      else {
        const obstacle = this.obstacles.find((item) => sameCell(item, next));
        if (obstacle) collision = obstacle.type;
      }
      if (collision) return this.handleCollision(collision);

      this.trail.unshift(next);
      if (ate) {
        this.score += 1;
        this.interval = Math.max(
          MIN_INTERVAL,
          START_INTERVAL - Math.floor(this.score / SPEED_MILESTONE) * SPEED_STEP
        );
        this.dandelion = this.spawnDandelion();
        this.onEvent("dandelion", { score: this.score });
      } else {
        this.trail.pop();
      }
      this.onChange(this.snapshot());
      return true;
    }

    handleCollision(type) {
      if (this.hitLocked || this.status !== "running") return false;
      this.hitLocked = true;
      this.hearts = Math.max(0, this.hearts - 1);
      this.onEvent("hit", { type, hearts: this.hearts });
      if (this.hearts === 0) {
        this.status = "gameover";
        if (this.score > this.best) {
          this.best = this.score;
          this.writeBest(this.best);
        }
        this.onEvent("gameover", { score: this.score, best: this.best });
      } else {
        this.trail = this.initialTrail();
        this.direction = "right";
        this.queuedDirection = null;
        if (this.dandelion && this.trail.some((cell) => sameCell(cell, this.dandelion))) {
          this.dandelion = this.spawnDandelion();
        }
      }
      this.hitLocked = false;
      this.onChange(this.snapshot());
      return false;
    }

    occupiedSet(includeDandelion) {
      const occupied = new Set(this.trail.map(cellKey));
      this.obstacles.forEach((item) => occupied.add(cellKey(item)));
      if (includeDandelion && this.dandelion) occupied.add(cellKey(this.dandelion));
      return occupied;
    }

    randomEmptyCell(occupied) {
      const candidates = [];
      for (let y = 0; y < this.gridSize; y += 1) {
        for (let x = 0; x < this.gridSize; x += 1) {
          if (!occupied.has(`${x},${y}`)) candidates.push({ x, y });
        }
      }
      if (!candidates.length) return null;
      return candidates[Math.floor(this.random() * candidates.length) % candidates.length];
    }

    spawnDandelion() { return this.randomEmptyCell(this.occupiedSet(false)); }

    generateObstacles() {
      const obstacles = [];
      const occupied = new Set(this.trail.map(cellKey));
      const middle = Math.floor(this.gridSize / 2);
      for (let y = middle - 2; y <= middle + 2; y += 1) {
        for (let x = middle - 4; x <= middle + 4; x += 1) occupied.add(`${x},${y}`);
      }
      const addCell = (cell, type) => {
        const key = cellKey(cell);
        if (occupied.has(key)) return false;
        occupied.add(key); obstacles.push({ ...cell, type }); return true;
      };
      for (let index = 0; index < 8; index += 1) {
        const cell = this.randomEmptyCell(occupied);
        if (cell) addCell(cell, "rock");
      }
      for (let run = 0; run < 4; run += 1) {
        const start = this.randomEmptyCell(occupied);
        if (!start) continue;
        const horizontal = this.random() >= 0.5;
        for (let offset = 0; offset < 3; offset += 1) {
          const cell = { x: start.x + (horizontal ? offset : 0), y: start.y + (horizontal ? 0 : offset) };
          if (cell.x < this.gridSize && cell.y < this.gridSize) addCell(cell, "root");
        }
      }
      return obstacles;
    }

    readBest() {
      try {
        const value = Number(this.storage && this.storage.getItem(BEST_SCORE_KEY));
        return Number.isInteger(value) && value >= 0 ? value : 0;
      } catch (_error) { return 0; }
    }

    writeBest(value) {
      try { if (this.storage) this.storage.setItem(BEST_SCORE_KEY, String(value)); } catch (_error) {
        // A blocked or full localStorage must never stop the game.
      }
    }
  }

  const core = {
    PlungeBurrowGame, GRID_SIZE, START_HEARTS, START_INTERVAL,
    SPEED_STEP, SPEED_MILESTONE, MIN_INTERVAL, BEST_SCORE_KEY,
    MUSIC_ENABLED_KEY, MUSIC_VOLUME_KEY, SOUNDTRACK_URL,
  };
  root.PlungeBurrowCore = core;
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  if (!root.document) return;

  const document = root.document;
  const canvas = document.getElementById("plunge-canvas");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const scoreEl = document.getElementById("plunge-score");
  const bestEl = document.getElementById("plunge-best");
  const heartsEl = document.getElementById("plunge-hearts");
  const stateEl = document.getElementById("plunge-state");
  const liveEl = document.getElementById("plunge-live");
  const overlay = document.getElementById("plunge-overlay");
  const overlayTitle = document.getElementById("plunge-overlay-title");
  const overlayDetail = document.getElementById("plunge-overlay-detail");
  const startButton = document.getElementById("plunge-start");
  const pauseButton = document.getElementById("plunge-pause");
  const restartButton = document.getElementById("plunge-restart");
  const musicEnabled = document.getElementById("plunge-music-enabled");
  const musicVolume = document.getElementById("plunge-music-volume");
  const musicVolumeValue = document.getElementById("plunge-music-volume-value");
  let frameId = null;
  let lastFrame = 0;
  let accumulator = 0;
  let destroyed = false;
  let touchStart = null;
  let soundtrack = null;

  function announce(message) { liveEl.textContent = ""; root.setTimeout(() => { liveEl.textContent = message; }, 0); }
  function playEffect(name) {
    try { if (root.WoodshedAudio) root.WoodshedAudio.play(name); } catch (_error) { /* Supplemental only. */ }
  }
  function safeStorageGet(key, fallback) {
    try { const value = root.localStorage.getItem(key); return value === null ? fallback : value; } catch (_error) { return fallback; }
  }
  function safeStorageSet(key, value) {
    try { root.localStorage.setItem(key, String(value)); } catch (_error) { /* Preference remains page-local. */ }
  }
  function configureMusic() {
    if (!SOUNDTRACK_URL) return;
    soundtrack = new Audio(SOUNDTRACK_URL);
    soundtrack.loop = true;
    musicEnabled.disabled = false;
    musicVolume.disabled = false;
    musicEnabled.checked = safeStorageGet(MUSIC_ENABLED_KEY, "false") === "true";
    musicVolume.value = safeStorageGet(MUSIC_VOLUME_KEY, "30");
    soundtrack.volume = Number(musicVolume.value) / 100;
  }
  function startMusic() {
    if (!soundtrack || !musicEnabled.checked) return;
    const promise = soundtrack.play();
    if (promise && promise.catch) promise.catch(function () {});
  }
  function pauseMusic() { if (soundtrack) soundtrack.pause(); }
  function stopMusic() { if (soundtrack) { soundtrack.pause(); soundtrack.currentTime = 0; } }

  const game = new PlungeBurrowGame({
    storage: root.localStorage,
    onChange: renderState,
    onEvent: function (event, detail) {
      if (event === "dandelion") { playEffect("dandelionEarned"); announce(`Dandelion found. Score ${detail.score}.`); }
      if (event === "hit") {
        playEffect("incorrectTrivia");
        canvas.classList.add("is-hit");
        root.setTimeout(() => canvas.classList.remove("is-hit"), 260);
        announce(`${detail.type} collision. ${detail.hearts} hearts remaining.`);
      }
      if (event === "pause") announce("Game paused.");
      if (event === "resume") announce("Game resumed.");
      if (event === "gameover") { stopMusic(); announce(`Game over. Final score ${detail.score}.`); }
    },
  });

  function renderState(state) {
    if (!scoreEl) return;
    scoreEl.textContent = String(state.score);
    bestEl.textContent = String(state.best);
    heartsEl.textContent = Array.from({ length: state.hearts }, () => "♥").join(" ") || "None";
    heartsEl.setAttribute("aria-label", `${state.hearts} ${state.hearts === 1 ? "heart" : "hearts"}`);
    const labels = { ready: "Ready", running: "Playing", paused: "Paused", gameover: "Game Over" };
    stateEl.textContent = labels[state.status];
    pauseButton.disabled = state.status === "ready" || state.status === "gameover";
    pauseButton.textContent = state.status === "paused" ? "Resume" : "Pause";
    startButton.disabled = state.status === "running" || state.status === "paused" || state.status === "gameover";
    overlay.hidden = state.status === "running";
    if (state.status === "gameover") {
      overlayTitle.textContent = "Game Over";
      overlayDetail.textContent = `Final score: ${state.score}. Press Restart to burrow again.`;
    } else if (state.status === "paused") {
      overlayTitle.textContent = "Paused";
      overlayDetail.textContent = "Your burrow is waiting.";
    } else {
      overlayTitle.textContent = "Ready to burrow?";
      overlayDetail.textContent = "Choose a direction, then press Start.";
    }
    draw(state);
  }

  function resizeCanvas() {
    const size = Math.max(240, Math.floor(canvas.getBoundingClientRect().width));
    const ratio = Math.min(root.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(size * ratio);
    canvas.height = Math.floor(size * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    draw(game.snapshot());
  }

  function draw(state) {
    if (!context) return;
    const size = canvas.width / Math.min(root.devicePixelRatio || 1, 2);
    const cell = size / GRID_SIZE;
    context.clearRect(0, 0, size, size);
    context.fillStyle = "#5a351f"; context.fillRect(0, 0, size, size);
    context.strokeStyle = "rgba(255, 225, 171, 0.06)"; context.lineWidth = 1;
    for (let index = 1; index < GRID_SIZE; index += 1) {
      context.beginPath(); context.moveTo(index * cell, 0); context.lineTo(index * cell, size); context.stroke();
      context.beginPath(); context.moveTo(0, index * cell); context.lineTo(size, index * cell); context.stroke();
    }
    state.obstacles.forEach((item) => {
      const x = item.x * cell; const y = item.y * cell;
      if (item.type === "rock") {
        context.fillStyle = "#777066"; context.beginPath(); context.ellipse(x + cell / 2, y + cell / 2, cell * 0.38, cell * 0.3, -0.2, 0, Math.PI * 2); context.fill();
      } else {
        context.strokeStyle = "#382319"; context.lineWidth = Math.max(3, cell * 0.28); context.lineCap = "round";
        context.beginPath(); context.moveTo(x + cell * 0.15, y + cell * 0.2); context.lineTo(x + cell * 0.85, y + cell * 0.8); context.stroke();
      }
    });
    if (state.dandelion) {
      context.font = `${cell * 0.78}px system-ui`; context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText("🌼", (state.dandelion.x + 0.5) * cell, (state.dandelion.y + 0.52) * cell);
    }
    state.trail.slice().reverse().forEach((part, reverseIndex) => {
      const isHead = reverseIndex === state.trail.length - 1;
      const inset = cell * (isHead ? 0.08 : 0.17);
      context.fillStyle = isHead ? "#b66a35" : "#8b5635";
      context.beginPath(); context.roundRect(part.x * cell + inset, part.y * cell + inset, cell - inset * 2, cell - inset * 2, cell * 0.28); context.fill();
      if (isHead) {
        const vector = VECTORS[state.direction];
        context.fillStyle = "#f5e4c8"; context.beginPath();
        context.arc((part.x + 0.5 + vector.x * 0.18) * cell, (part.y + 0.5 + vector.y * 0.18) * cell, cell * 0.12, 0, Math.PI * 2); context.fill();
      }
    });
  }

  function animate(timestamp) {
    frameId = null;
    if (destroyed || game.status !== "running") return;
    if (!lastFrame) lastFrame = timestamp;
    accumulator += Math.min(timestamp - lastFrame, game.interval * 2);
    lastFrame = timestamp;
    while (accumulator >= game.interval && game.status === "running") {
      game.tick(); accumulator -= game.interval;
    }
    draw(game.snapshot());
    if (game.status === "running") frameId = root.requestAnimationFrame(animate);
  }
  function ensureLoop() {
    if (frameId === null && game.status === "running") {
      lastFrame = 0; accumulator = 0; frameId = root.requestAnimationFrame(animate);
    }
  }
  function cancelLoop() { if (frameId !== null) root.cancelAnimationFrame(frameId); frameId = null; lastFrame = 0; accumulator = 0; }
  function chooseDirection(direction) { game.setDirection(direction); canvas.focus({ preventScroll: true }); }

  startButton.addEventListener("click", function () { if (game.start()) { startMusic(); ensureLoop(); canvas.focus({ preventScroll: true }); } });
  pauseButton.addEventListener("click", function () {
    if (game.status === "running" && game.pause()) { cancelLoop(); pauseMusic(); }
    else if (game.resume()) { startMusic(); ensureLoop(); }
  });
  restartButton.addEventListener("click", function () { cancelLoop(); stopMusic(); game.reset(); announce("Game reset. Ready to start."); canvas.focus({ preventScroll: true }); });
  document.querySelectorAll("[data-direction]").forEach((button) => button.addEventListener("click", () => chooseDirection(button.dataset.direction)));

  const keyDirections = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", w: "up", W: "up", a: "left", A: "left", s: "down", S: "down", d: "right", D: "right" };
  document.addEventListener("keydown", function (event) {
    const direction = keyDirections[event.key];
    if (!direction) return;
    if (game.status === "running" || document.activeElement === canvas) event.preventDefault();
    chooseDirection(direction);
  });
  canvas.addEventListener("touchstart", function (event) {
    const touch = event.changedTouches[0]; touchStart = { x: touch.clientX, y: touch.clientY };
  }, { passive: true });
  canvas.addEventListener("touchend", function (event) {
    if (!touchStart) return;
    const touch = event.changedTouches[0]; const dx = touch.clientX - touchStart.x; const dy = touch.clientY - touchStart.y; touchStart = null;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 24) return;
    chooseDirection(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up"));
  }, { passive: true });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden && game.pause()) { cancelLoop(); pauseMusic(); }
  });
  root.addEventListener("resize", resizeCanvas);
  root.addEventListener("pagehide", function () { destroyed = true; cancelLoop(); stopMusic(); });
  musicEnabled.addEventListener("change", function () { safeStorageSet(MUSIC_ENABLED_KEY, musicEnabled.checked); if (!musicEnabled.checked) pauseMusic(); else if (game.status === "running") startMusic(); });
  musicVolume.addEventListener("input", function () { const value = Number(musicVolume.value); musicVolumeValue.textContent = `${value}%`; musicVolume.setAttribute("aria-valuetext", `${value} percent`); if (soundtrack) soundtrack.volume = value / 100; safeStorageSet(MUSIC_VOLUME_KEY, value); });

  configureMusic();
  resizeCanvas();
}(typeof window !== "undefined" ? window : globalThis));
