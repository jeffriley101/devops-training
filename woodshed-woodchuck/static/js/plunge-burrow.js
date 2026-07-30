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
  const MAX_HEARTS = 3;
  const CARROT_MILESTONE = 5;
  const INSTRUMENT_START_SCORE = 3;
  const INSTRUMENT_RESPAWN_GAP = 4;
  const BAND_SET_TARGET = 4;
  const INSTRUMENTS = [
    { name: "Flute", icon: "🪈" },
    { name: "Clarinet", icon: "♬" },
    { name: "Saxophone", icon: "🎷" },
    { name: "Trumpet", icon: "🎺" },
    { name: "Trombone", icon: "🎺" },
    { name: "Horn", icon: "📯" },
    { name: "Tuba", icon: "♩" },
    { name: "Percussion", icon: "🥁" },
  ];
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
      this.dandelionsCollected = 0;
      this.interval = START_INTERVAL;
      this.direction = "right";
      this.queuedDirection = null;
      this.pendingGrowth = 0;
      this.portalCooldown = null;
      this.portalFlashTicks = 0;
      this.bandSetFlashTicks = 0;
      this.bandSet = [];
      this.lastInstrumentType = null;
      this.instrumentSpawnScore = INSTRUMENT_START_SCORE;
      this.trail = this.initialTrail();
      this.obstacles = this.generateObstacles();
      this.portals = this.generatePortals();
      this.dandelion = this.spawnDandelion();
      this.carrot = null;
      this.instrument = null;
      this.status = "ready";
      this.hitLocked = false;
      this.onChange(this.snapshot());
      return this.snapshot();
    }

    snapshot() {
      return {
        score: this.score, best: this.best, hearts: this.hearts,
        dandelionsCollected: this.dandelionsCollected,
        interval: this.interval, direction: this.direction,
        pendingGrowth: this.pendingGrowth,
        portalFlashTicks: this.portalFlashTicks,
        bandSetFlashTicks: this.bandSetFlashTicks,
        trail: this.trail.map((cell) => ({ ...cell })),
        obstacles: this.obstacles.map((item) => ({ ...item })),
        portals: this.portals.map((item) => ({ ...item })),
        dandelion: this.dandelion ? { ...this.dandelion } : null,
        carrot: this.carrot ? { ...this.carrot } : null,
        instrument: this.instrument ? { ...this.instrument } : null,
        bandSet: this.bandSet.slice(),
        status: this.status,
      };
    }

    emit(event, detail) {
      try { this.onEvent(event, detail); } catch (_error) {
        // Visual and audio feedback must never control game state.
      }
    }

    start() {
      if (this.status === "gameover") return false;
      this.status = "running";
      this.emit("start");
      this.onChange(this.snapshot());
      return true;
    }

    pause() {
      if (this.status !== "running") return false;
      this.status = "paused";
      this.emit("pause");
      this.onChange(this.snapshot());
      return true;
    }

    resume() {
      if (this.status !== "paused") return false;
      this.status = "running";
      this.emit("resume");
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
      if (this.portalFlashTicks > 0) this.portalFlashTicks -= 1;
      if (this.bandSetFlashTicks > 0) this.bandSetFlashTicks -= 1;
      if (this.queuedDirection) {
        this.direction = this.queuedDirection;
        this.queuedDirection = null;
      }
      const vector = VECTORS[this.direction];
      const head = this.trail[0];
      const entered = { x: head.x + vector.x, y: head.y + vector.y };
      let next = entered;
      const entryPortal = this.portalAt(entered);
      let usedPortal = null;
      if (entryPortal && cellKey(entryPortal) !== this.portalCooldown) {
        const partner = this.portals.find((portal) => portal.pairId === entryPortal.pairId && !sameCell(portal, entryPortal));
        if (partner) { next = { x: partner.x, y: partner.y }; usedPortal = { entry: entryPortal, exit: partner }; }
      }
      const pickupGrowth = this.pickupGrowthAt(next);
      const bodyToCheck = this.pendingGrowth + pickupGrowth > 0 ? this.trail : this.trail.slice(0, -1);
      let collision = null;
      if (entered.x < 0 || entered.y < 0 || entered.x >= this.gridSize || entered.y >= this.gridSize) collision = "wall";
      else if (bodyToCheck.some((cell) => sameCell(cell, next))) collision = "self";
      else {
        const obstacle = this.obstacles.find((item) => sameCell(item, next));
        if (obstacle) collision = obstacle.type;
      }
      if (collision) return this.handleCollision(collision);

      this.trail.unshift(next);
      if (usedPortal) {
        this.portalCooldown = cellKey(usedPortal.exit);
        this.portalFlashTicks = 2;
        this.emit("portal", { pairId: usedPortal.entry.pairId, entry: { ...usedPortal.entry }, exit: { ...usedPortal.exit } });
      } else if (this.portalCooldown && cellKey(next) !== this.portalCooldown) {
        this.portalCooldown = null;
      }
      this.collectAt(next);
      if (this.pendingGrowth > 0) {
        this.pendingGrowth -= 1;
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
      this.emit("hit", { type, hearts: this.hearts });
      if (this.hearts === 0) {
        this.status = "gameover";
        if (this.score > this.best) {
          this.best = this.score;
          this.writeBest(this.best);
        }
        this.emit("gameover", { score: this.score, best: this.best });
      } else {
        this.trail = this.initialTrail();
        this.direction = "right";
        this.queuedDirection = null;
        this.pendingGrowth = 0;
        this.portalCooldown = null;
        this.relocatePickupsFromTrail();
      }
      if (this.status === "gameover") this.clearRunCollections();
      this.hitLocked = false;
      this.onChange(this.snapshot());
      return false;
    }

    occupiedSet(options) {
      const include = options || {};
      const occupied = new Set(this.trail.map(cellKey));
      this.obstacles.forEach((item) => occupied.add(cellKey(item)));
      if (include.portals !== false && this.portals) this.portals.forEach((item) => occupied.add(cellKey(item)));
      if (include.dandelion !== false && this.dandelion) occupied.add(cellKey(this.dandelion));
      if (include.carrot !== false && this.carrot) occupied.add(cellKey(this.carrot));
      if (include.instrument !== false && this.instrument) occupied.add(cellKey(this.instrument));
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

    spawnDandelion() { return this.randomEmptyCell(this.occupiedSet({ dandelion: false })); }

    spawnCarrot() {
      if (this.carrot) return this.carrot;
      this.carrot = this.randomEmptyCell(this.occupiedSet({ carrot: false }));
      return this.carrot;
    }

    chooseInstrumentType() {
      let index = Math.floor(this.random() * INSTRUMENTS.length) % INSTRUMENTS.length;
      if (INSTRUMENTS.length > 1 && INSTRUMENTS[index].name === this.lastInstrumentType) index = (index + 1) % INSTRUMENTS.length;
      return INSTRUMENTS[index];
    }

    spawnInstrument() {
      if (this.instrument) return this.instrument;
      const cell = this.randomEmptyCell(this.occupiedSet({ instrument: false }));
      if (!cell) return null;
      const type = this.chooseInstrumentType();
      this.instrument = { ...cell, name: type.name, icon: type.icon };
      this.lastInstrumentType = type.name;
      return this.instrument;
    }

    maybeSpawnInstrument() {
      if (!this.instrument && this.score >= this.instrumentSpawnScore) this.spawnInstrument();
    }

    pickupGrowthAt(cell) {
      if (this.dandelion && sameCell(cell, this.dandelion)) return 1;
      if (this.carrot && sameCell(cell, this.carrot)) return 2;
      return 0;
    }

    collectAt(cell) {
      if (this.dandelion && sameCell(cell, this.dandelion)) {
        this.score += 1;
        this.dandelionsCollected += 1;
        this.pendingGrowth += 1;
        this.interval = Math.max(MIN_INTERVAL, START_INTERVAL - Math.floor(this.dandelionsCollected / SPEED_MILESTONE) * SPEED_STEP);
        this.dandelion = null;
        if (this.dandelionsCollected % CARROT_MILESTONE === 0 && !this.carrot) this.spawnCarrot();
        this.dandelion = this.spawnDandelion();
        this.maybeSpawnInstrument();
        this.emit("dandelion", { score: this.score, count: this.dandelionsCollected });
        return;
      }
      if (this.carrot && sameCell(cell, this.carrot)) {
        const restored = this.hearts < MAX_HEARTS;
        this.score += 3;
        this.pendingGrowth += 2;
        if (restored) this.hearts += 1;
        this.carrot = null;
        this.maybeSpawnInstrument();
        this.emit("carrot", { score: this.score, growth: 2, heartRestored: restored, hearts: this.hearts });
        return;
      }
      if (this.instrument && sameCell(cell, this.instrument)) {
        const collected = this.instrument;
        const duplicate = this.bandSet.includes(collected.name);
        this.score += 5;
        if (!duplicate) this.bandSet.push(collected.name);
        let completed = false;
        if (this.bandSet.length === BAND_SET_TARGET) {
          this.score += 20;
          completed = true;
          this.bandSet = [];
          this.bandSetFlashTicks = 6;
        }
        this.instrument = null;
        this.instrumentSpawnScore = this.score + INSTRUMENT_RESPAWN_GAP;
        this.emit("instrument", { name: collected.name, duplicate, completed, score: this.score });
      }
    }

    portalAt(cell) { return this.portals.find((portal) => sameCell(portal, cell)) || null; }

    clearRunCollections() {
      this.dandelionsCollected = 0;
      this.carrot = null;
      this.instrument = null;
      this.bandSet = [];
      this.lastInstrumentType = null;
      this.instrumentSpawnScore = INSTRUMENT_START_SCORE;
      this.pendingGrowth = 0;
      this.portalCooldown = null;
      this.portalFlashTicks = 0;
      this.bandSetFlashTicks = 0;
    }

    relocatePickupsFromTrail() {
      const onTrail = (item) => item && this.trail.some((cell) => sameCell(cell, item));
      if (onTrail(this.dandelion)) { this.dandelion = null; this.dandelion = this.spawnDandelion(); }
      if (onTrail(this.carrot)) { this.carrot = null; this.spawnCarrot(); }
      if (onTrail(this.instrument)) { this.instrument = null; this.spawnInstrument(); }
    }

    generateObstacles() {
      const obstacles = [];
      const occupied = new Set(this.trail.map(cellKey));
      const middle = Math.floor(this.gridSize / 2);
      for (let y = middle - 2; y <= middle + 2; y += 1) {
        for (let x = middle - 4; x <= middle + 4; x += 1) occupied.add(`${x},${y}`);
      }
      const addCell = (cell, type, formation) => {
        const key = cellKey(cell);
        if (occupied.has(key)) return false;
        occupied.add(key); obstacles.push({ ...cell, type, ...(formation === undefined ? {} : { formation }) }); return true;
      };
      for (let index = 0; index < 6; index += 1) {
        const cell = this.randomEmptyCell(occupied);
        if (cell) addCell(cell, "rock");
      }
      for (let run = 0; run < 3; run += 1) {
        const start = this.randomEmptyCell(occupied);
        if (!start) continue;
        const horizontal = this.random() >= 0.5;
        for (let offset = 0; offset < 3; offset += 1) {
          const cell = { x: start.x + (horizontal ? offset : 0), y: start.y + (horizontal ? 0 : offset) };
          if (cell.x < this.gridSize && cell.y < this.gridSize) addCell(cell, "root", run);
        }
      }
      return obstacles;
    }

    generatePortals() {
      const portals = [];
      const occupied = new Set(this.trail.map(cellKey));
      this.obstacles.forEach((item) => occupied.add(cellKey(item)));
      const middle = Math.floor(this.gridSize / 2);
      const safeCandidate = (cell) => {
        if (Math.abs(cell.x - middle) <= 4 && Math.abs(cell.y - middle) <= 2) return false;
        return Object.values(VECTORS).every((vector) => {
          const neighbor = { x: cell.x + vector.x, y: cell.y + vector.y };
          return neighbor.x >= 0 && neighbor.y >= 0 && neighbor.x < this.gridSize && neighbor.y < this.gridSize && !occupied.has(cellKey(neighbor));
        });
      };
      const choose = (awayFrom) => {
        const candidates = [];
        for (let y = 1; y < this.gridSize - 1; y += 1) {
          for (let x = 1; x < this.gridSize - 1; x += 1) {
            const cell = { x, y };
            const separated = !awayFrom || Math.abs(x - awayFrom.x) + Math.abs(y - awayFrom.y) > 3;
            if (!occupied.has(cellKey(cell)) && separated && safeCandidate(cell)) candidates.push(cell);
          }
        }
        if (!candidates.length) return null;
        return candidates[Math.floor(this.random() * candidates.length) % candidates.length];
      };
      ["A", "B"].forEach((pairId) => {
        const first = choose(null);
        if (!first) return;
        occupied.add(cellKey(first));
        const second = choose(first);
        if (!second) { occupied.delete(cellKey(first)); return; }
        occupied.add(cellKey(second));
        portals.push({ ...first, pairId, mark: pairId }, { ...second, pairId, mark: pairId });
      });
      return portals;
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
    MUSIC_ENABLED_KEY, MUSIC_VOLUME_KEY, SOUNDTRACK_URL, MAX_HEARTS,
    CARROT_MILESTONE, INSTRUMENT_START_SCORE, INSTRUMENT_RESPAWN_GAP,
    BAND_SET_TARGET, INSTRUMENTS,
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
  const dandelionsEl = document.getElementById("plunge-dandelions");
  const stateEl = document.getElementById("plunge-state");
  const bandProgressEl = document.getElementById("plunge-band-progress");
  const bandListEl = document.getElementById("plunge-band-list");
  const bandCompleteEl = document.getElementById("plunge-band-complete");
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
      if (event === "portal") {
        playEffect("burrowPortal");
        announce(`Entered portal ${detail.pairId} and emerged from its matching tunnel. Direction preserved.`);
      }
      if (event === "carrot") {
        playEffect("carrotCollected");
        announce(`Carrot collected. Plus 3 score and two trail segments.${detail.heartRestored ? ` One heart restored; ${detail.hearts} hearts now.` : " Hearts already full."} Score ${detail.score}.`);
      }
      if (event === "instrument") {
        if (detail.completed) {
          playEffect("bandSetCompleted");
          announce(`${detail.name} collected for 5 points. Band Set Complete for 20 bonus points. A fresh set has begun. Score ${detail.score}.`);
        } else {
          playEffect("instrumentCollected");
          announce(`${detail.name} collected for 5 points.${detail.duplicate ? " Duplicate instrument; Band Set progress unchanged." : " Added to the Band Set."} Score ${detail.score}.`);
        }
      }
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
    dandelionsEl.textContent = String(state.dandelionsCollected);
    canvas.classList.toggle("is-portal", state.portalFlashTicks > 0);
    bandCompleteEl.hidden = state.bandSetFlashTicks <= 0;
    bandProgressEl.textContent = `${state.bandSet.length} / ${BAND_SET_TARGET}`;
    bandListEl.replaceChildren();
    if (!state.bandSet.length) {
      const empty = document.createElement("li"); empty.className = "plunge-band-empty"; empty.textContent = "No instruments yet"; bandListEl.appendChild(empty);
    } else {
      state.bandSet.forEach((name) => {
        const item = document.createElement("li");
        const instrument = INSTRUMENTS.find((candidate) => candidate.name === name);
        item.textContent = `${instrument ? instrument.icon : "♪"} ${name}`;
        bandListEl.appendChild(item);
      });
    }
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
    state.portals.forEach((portal) => {
      const centerX = (portal.x + 0.5) * cell; const centerY = (portal.y + 0.5) * cell;
      context.fillStyle = portal.pairId === "A" ? "#241a35" : "#17343a";
      context.strokeStyle = portal.pairId === "A" ? "#e4b5ff" : "#9fe5df";
      context.lineWidth = Math.max(2, cell * 0.1);
      context.beginPath(); context.ellipse(centerX, centerY, cell * 0.39, cell * 0.31, 0, 0, Math.PI * 2); context.fill(); context.stroke();
      context.fillStyle = "#fff7df"; context.font = `bold ${cell * 0.48}px system-ui`; context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText(portal.mark, centerX, centerY);
    });
    if (state.dandelion) {
      context.font = `${cell * 0.78}px system-ui`; context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText("🌼", (state.dandelion.x + 0.5) * cell, (state.dandelion.y + 0.52) * cell);
    }
    if (state.carrot) {
      context.font = `${cell * 0.78}px system-ui`; context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText("🥕", (state.carrot.x + 0.5) * cell, (state.carrot.y + 0.52) * cell);
    }
    if (state.instrument) {
      context.font = `${cell * 0.76}px system-ui`; context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText(state.instrument.icon, (state.instrument.x + 0.5) * cell, (state.instrument.y + 0.52) * cell);
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
  restartButton.addEventListener("click", function () { cancelLoop(); stopMusic(); game.reset(); announce("Game reset. Score, pickups, portals, and Band Set cleared. Ready to start."); canvas.focus({ preventScroll: true }); });
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
