(function (root) {
  "use strict";
  const document = root.document;
  if (document?.body?.dataset?.guest !== "local") return;
  const core = root.PlungeBurrowCore;
  const boundary = root.WWSessionBoundary;
  const panel = document.getElementById("guest-plunge-panel");
  const canvas = document.getElementById("guest-plunge-canvas");
  if (!core || !boundary || !panel || !canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return;
  const byId = name => document.getElementById(`guest-plunge-${name}`);
  const openButton = byId("open");
  const closeButton = byId("close");
  const startButton = byId("start");
  const pauseButton = byId("pause");
  const replayButton = byId("replay");
  const scoreEl = byId("score");
  const heartsEl = byId("hearts");
  const pickupsEl = byId("pickups");
  const stateEl = byId("state");
  const bandProgressEl = byId("band-progress");
  const bandListEl = byId("band-list");
  const bandCompleteEl = byId("band-complete");
  const overlay = byId("overlay");
  const overlayTitle = byId("overlay-title");
  const overlayDetail = byId("overlay-detail");
  const liveEl = byId("live");
  const directionButtons = Array.from(panel.querySelectorAll("[data-guest-plunge-direction]"));
  if ([openButton, closeButton, startButton, pauseButton, replayButton, scoreEl,
    heartsEl, pickupsEl, stateEl, bandProgressEl, bandListEl, bandCompleteEl,
    overlay, overlayTitle, overlayDetail, liveEl].some(element => !element)) return;

  let game;
  let frameId = null;
  let lastFrame = null;
  let accumulator = 0;
  let touchStart = null;
  let resetting = false;
  let canvasSize = 600;

  function current() {
    return boundary.isCurrent() && root.WWGuest?.isCurrent() === true;
  }
  function playable() { return current() && !panel.hidden && !document.hidden; }
  function announce(message) { liveEl.textContent = message; }
  function cancelLoop() {
    if (frameId !== null) root.cancelAnimationFrame(frameId);
    frameId = null;
    lastFrame = null;
    accumulator = 0;
  }

  function render(state) {
    scoreEl.textContent = String(state.score);
    heartsEl.textContent = "♥ ".repeat(state.hearts).trim() || "None";
    heartsEl.setAttribute("aria-label", `${state.hearts} ${state.hearts === 1 ? "heart" : "hearts"}`);
    pickupsEl.textContent = String(state.dandelionsCollected);
    stateEl.textContent = {ready: "Ready", running: "Playing", paused: "Paused", gameover: "Game Over"}[state.status];
    bandProgressEl.textContent = `${state.bandSet.length} / ${core.BAND_SET_TARGET}`;
    bandCompleteEl.hidden = state.bandSetFlashTicks <= 0;
    bandListEl.replaceChildren();
    for (const name of state.bandSet) {
      const item = document.createElement("li");
      const instrument = core.INSTRUMENTS.find(candidate => candidate.name === name);
      item.textContent = `${instrument?.icon || "♪"} ${name}`;
      bandListEl.appendChild(item);
    }
    if (!state.bandSet.length) {
      const item = document.createElement("li");
      item.className = "plunge-band-empty";
      item.textContent = "No instruments yet";
      bandListEl.appendChild(item);
    }
    const allowed = current();
    const active = allowed && !panel.hidden;
    openButton.disabled = !allowed;
    closeButton.disabled = !active;
    startButton.disabled = !active || state.status !== "ready";
    pauseButton.disabled = !active || !["running", "paused"].includes(state.status);
    pauseButton.textContent = state.status === "paused" ? "Resume" : "Pause";
    replayButton.disabled = !active;
    directionButtons.forEach(button => { button.disabled = !active || state.status === "gameover"; });
    canvas.classList.toggle("is-portal", state.portalFlashTicks > 0);
    overlay.hidden = state.status === "running";
    overlayTitle.textContent = state.status === "gameover" ? "Game Over"
      : state.status === "paused" ? "Paused" : "Ready to burrow?";
    overlayDetail.textContent = state.status === "gameover" ? `Final score: ${state.score}. Select Replay to start at zero.`
      : state.status === "paused" ? "Select Resume when you are ready."
      : "Choose a direction, then press Start.";
    draw(state);
  }

  function draw(state) {
    const cell = canvasSize / core.GRID_SIZE;
    context.clearRect(0, 0, canvasSize, canvasSize);
    context.fillStyle = "#5a351f";
    context.fillRect(0, 0, canvasSize, canvasSize);
    context.strokeStyle = "rgba(255, 225, 171, 0.08)";
    context.lineWidth = 1;
    for (let index = 1; index < core.GRID_SIZE; index++) {
      context.beginPath();
      context.moveTo(index * cell, 0); context.lineTo(index * cell, canvasSize);
      context.moveTo(0, index * cell); context.lineTo(canvasSize, index * cell);
      context.stroke();
    }
    for (const item of state.obstacles) {
      context.fillStyle = item.type === "rock" ? "#777066" : "#382319";
      context.beginPath();
      context.ellipse((item.x + 0.5) * cell, (item.y + 0.5) * cell,
        cell * 0.38, cell * 0.3, item.type === "rock" ? -0.2 : 0.7, 0, Math.PI * 2);
      context.fill();
    }
    for (const portal of state.portals) {
      context.fillStyle = portal.pairId === "A" ? "#241a35" : "#17343a";
      context.strokeStyle = portal.pairId === "A" ? "#e4b5ff" : "#9fe5df";
      context.lineWidth = Math.max(2, cell * 0.1);
      context.beginPath();
      context.ellipse((portal.x + 0.5) * cell, (portal.y + 0.5) * cell, cell * 0.39, cell * 0.31, 0, 0, Math.PI * 2);
      context.fill(); context.stroke();
      context.fillStyle = "#fff7df";
      context.font = `bold ${cell * 0.48}px system-ui`;
      context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText(portal.mark, (portal.x + 0.5) * cell, (portal.y + 0.5) * cell);
    }
    context.font = `${cell * 0.78}px system-ui`;
    context.textAlign = "center"; context.textBaseline = "middle";
    for (const [pickup, icon] of [[state.dandelion, "🌼"], [state.carrot, "🥕"], [state.instrument, state.instrument?.icon]]) {
      if (pickup) context.fillText(icon, (pickup.x + 0.5) * cell, (pickup.y + 0.52) * cell);
    }
    state.trail.slice().reverse().forEach((part, index) => {
      const head = index === state.trail.length - 1;
      const inset = cell * (head ? 0.08 : 0.17);
      context.fillStyle = head ? "#b66a35" : "#8b5635";
      context.beginPath();
      context.roundRect(part.x * cell + inset, part.y * cell + inset,
        cell - inset * 2, cell - inset * 2, cell * 0.28);
      context.fill();
      if (head) {
        const vector = {up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0]}[state.direction];
        context.fillStyle = "#f5e4c8";
        context.beginPath();
        context.arc((part.x + 0.5 + vector[0] * 0.18) * cell,
          (part.y + 0.5 + vector[1] * 0.18) * cell, cell * 0.12, 0, Math.PI * 2);
        context.fill();
      }
    });
  }

  function resize() {
    canvasSize = Math.max(240, Math.floor(canvas.getBoundingClientRect().width));
    const ratio = Math.min(root.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(canvasSize * ratio);
    canvas.height = Math.floor(canvasSize * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    draw(game.snapshot());
  }
  function animate(timestamp) {
    frameId = null;
    if (!playable() || game.status !== "running") { cancelLoop(); return; }
    if (lastFrame === null) lastFrame = timestamp;
    accumulator += Math.min(timestamp - lastFrame, game.interval * 2);
    lastFrame = timestamp;
    while (accumulator >= game.interval && game.status === "running") {
      const interval = game.interval;
      game.tick();
      accumulator -= interval;
    }
    if (game.status === "running") frameId = root.requestAnimationFrame(animate);
  }
  function startLoop() {
    if (frameId === null && playable() && game.status === "running") {
      lastFrame = null;
      accumulator = 0;
      frameId = root.requestAnimationFrame(animate);
    }
  }
  function reset(close = true) {
    if (resetting) return;
    resetting = true;
    try {
      cancelLoop();
      touchStart = null;
      if (close) panel.hidden = true;
      openButton.setAttribute("aria-expanded", String(!panel.hidden));
      if (game) game.reset();
      liveEl.textContent = "";
    } finally { resetting = false; }
  }
  game = new core.PlungeBurrowGame({
    storage: null,
    trackBest: false,
    onChange: render,
    onEvent(event, detail) {
      if (event === "gameover") { cancelLoop(); announce(`Game over. Final score ${detail.score}.`); }
      else if (event === "dandelion" || event === "carrot" || event === "instrument") announce(`Pickup collected. Score ${detail.score}.`);
      else if (event === "hit") announce(`Collision. ${detail.hearts} hearts remaining.`);
      else if (event === "portal") announce(`Travelled through portal ${detail.pairId}.`);
    },
  });

  openButton.addEventListener("click", () => {
    if (!current()) return;
    panel.hidden = false;
    openButton.setAttribute("aria-expanded", "true");
    render(game.snapshot());
    resize();
    startButton.focus({preventScroll: true});
  });
  closeButton.addEventListener("click", () => {
    reset();
    if (current()) openButton.focus({preventScroll: true});
  });
  startButton.addEventListener("click", () => {
    if (playable() && game.status === "ready" && game.start()) {
      startLoop(); canvas.focus({preventScroll: true});
    }
  });
  pauseButton.addEventListener("click", () => {
    if (!playable()) return;
    if (game.status === "running") { game.pause(); cancelLoop(); }
    else if (game.resume()) startLoop();
  });
  replayButton.addEventListener("click", () => {
    if (!playable()) return;
    reset(false);
    announce("Game reset. Score and pickups cleared. Press Start to play again.");
    startButton.focus({preventScroll: true});
  });
  function direction(value) {
    if (!playable() || !["ready", "running", "paused"].includes(game.status)) return;
    game.setDirection(value);
    canvas.focus({preventScroll: true});
  }
  directionButtons.forEach(button => button.addEventListener("click", () => direction(button.dataset.guestPlungeDirection)));
  const directions = {ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", w: "up", a: "left", s: "down", d: "right"};
  panel.addEventListener("keydown", event => {
    if (!playable() || event.target.closest?.("input,select,textarea")) return;
    if (event.key === "Escape") { reset(); openButton.focus({preventScroll: true}); return; }
    const value = directions[event.key] || directions[event.key.toLowerCase()];
    if (value) { event.preventDefault(); direction(value); }
  });
  canvas.addEventListener("touchstart", event => {
    if (!playable()) return;
    const touch = event.changedTouches[0];
    touchStart = {x: touch.clientX, y: touch.clientY};
  }, {passive: true});
  canvas.addEventListener("touchend", event => {
    if (!touchStart || !playable()) { touchStart = null; return; }
    const touch = event.changedTouches[0];
    const dx = touch.clientX - touchStart.x, dy = touch.clientY - touchStart.y;
    touchStart = null;
    if (Math.max(Math.abs(dx), Math.abs(dy)) >= 24) direction(Math.abs(dx) > Math.abs(dy)
      ? dx > 0 ? "right" : "left" : dy > 0 ? "down" : "up");
  }, {passive: true});
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { touchStart = null; game.pause(); cancelLoop(); }
  });
  root.addEventListener("resize", () => { if (!panel.hidden && current()) resize(); });
  for (const event of ["ww:guest-reset", "ww:guest-discarded", "ww:session-changed", "pagehide"]) {
    root.addEventListener(event, () => reset());
  }
  root.addEventListener("ww:guest-setup", () => reset());
  root.addEventListener("pageshow", event => { if (event.persisted) reset(); });
  root.WWGuestPlunge = Object.freeze({
    ready: true,
    snapshot() {
      const {best: _unusedBest, ...state} = game.snapshot();
      return Object.freeze(state);
    },
  });
})(typeof window !== "undefined" ? window : globalThis);
