(function () {
  "use strict";

  const SCORE_ENDPOINTS = {
    "plunge-burrow": "/xp/plunge-best",
    blue: "/arcade/scores/blue",
    "radio-tuner": "/arcade/scores/radio-tuner",
    "wheel-of-woodchuck": "/arcade/scores/wheel-of-woodchuck",
    "scale-keyboard": "/arcade/scores/scale-keyboard",
  };
  const BLUE_GAME_SECONDS = 20;
  const RADIO_GAME_SECONDS = 30;
  const BLUE_RED_STAGE_BONUS_SECONDS = 10;
  const BLUE_WORLD_WIDTH = 3200;
  const BLUE_GROUND_Y = 315;
  const BLUE_FINAL_HOLE = Object.freeze({ left: 2360, right: 2450 });
  const RADIO_TICK_MS = 40;
  const RADIO_CENTER = 50;
  const RADIO_GOLD_ZONE = 8;
  const RADIO_MAX_SCORE = 50000;

  function renderLeaderboard(list, rows) {
    list.replaceChildren();
    if (!Array.isArray(rows) || !rows.length) {
      const empty = document.createElement("li");
      empty.textContent = "No scores yet";
      list.append(empty);
      return;
    }
    rows.slice(0, 5).forEach((row) => {
      const item = document.createElement("li");
      const name = typeof row.display_name === "string" ? row.display_name : "Woodchuck";
      item.value = Number(row.rank);
      item.textContent = `${name} — ${row.score}`;
      if (row.is_current_user) item.classList.add("is-current-user");
      list.append(item);
    });
  }

  function renderPersonalBest(gameKey, bestScore) {
    document.querySelectorAll(`[data-arcade-personal-best="${gameKey}"]`).forEach((output) => {
      output.textContent = String(bestScore || 0);
    });
  }

  async function loadScores(gameKey) {
    const endpoint = SCORE_ENDPOINTS[gameKey];
    if (!endpoint) throw new Error("That game is unavailable.");
    const response = await fetch(endpoint, {
      credentials: "same-origin",
      cache: "no-store",
    });
    let payload = {};
    try { payload = await response.json(); } catch (_error) { payload = {}; }
    if (!response.ok) throw new Error(payload.detail || "");
    return payload;
  }

  function wireArcadeRoom() {
    const lists = Array.from(document.querySelectorAll("[data-arcade-leaderboard]"));
    if (!lists.length || document.querySelector("[data-arcade-game]")) return;
    lists.forEach(async (list) => {
      try {
        const payload = await loadScores(list.dataset.arcadeLeaderboard);
        renderLeaderboard(list, payload.leaderboard);
        renderPersonalBest(list.dataset.arcadeLeaderboard, payload.best_score);
      } catch (_error) {
        renderLeaderboard(list, []);
      }
    });
  }

  function wireArcadeGame() {
    const root = document.querySelector("[data-arcade-game]");
    if (!root || root.dataset.arcadeWired === "true") return;
    root.dataset.arcadeWired = "true";

    const gameKey = root.dataset.arcadeGame;
    const scoreEl = document.getElementById("arcade-game-score");
    const bestEl = document.getElementById("arcade-game-best");
    const timeEl = document.getElementById("arcade-game-time");
    const startButton = document.getElementById("arcade-game-start");
    const message = document.getElementById("arcade-game-message");
    const leaderboard = root.querySelector("[data-arcade-leaderboard]");
    if (!scoreEl || !bestEl || !timeEl || !startButton || !message || !leaderboard) return;

    let score = 0;
    let timer = null;
    let running = false;
    let starting = false;
    let endTime = 0;
    let activePlayToken = null;

    const blueField = document.getElementById("blue-game-field");
    const blueCanvas = document.getElementById("blue-game-canvas");
    const blueContext = blueCanvas ? blueCanvas.getContext("2d") : null;
    const blueButtons = Array.from(document.querySelectorAll("[data-blue-action]"));
    let blueFrame = null;
    let bluePreviousTime = 0;
    let blueCameraX = 0;
    let blueStage = "blue";
    let blueRedStageBonusApplied = false;
    let blueRedBookRedirectScheduled = false;
    let blueFinalHoleFallInProgress = false;
    const blueInput = { left: false, right: false, jumpQueued: false };
    const bluePlayer = {
      x: 60, y: 80, width: 34, height: 46,
      velocityX: 0, velocityY: 0, onGround: false, checkpointX: 60,
    };
    const bluePlatforms = [
      { x: 0, y: BLUE_GROUND_Y, width: 520, height: 45 },
      { x: 610, y: BLUE_GROUND_Y, width: 520, height: 45 },
      { x: 1220, y: BLUE_GROUND_Y, width: 600, height: 45 },
      { x: 1910, y: BLUE_GROUND_Y, width: 450, height: 45 },
      { x: 2450, y: BLUE_GROUND_Y, width: 750, height: 45 },
      { x: 330, y: 238, width: 150, height: 18 },
      { x: 745, y: 225, width: 170, height: 18 },
      { x: 1035, y: 190, width: 120, height: 18 },
      { x: 1350, y: 235, width: 170, height: 18 },
      { x: 1690, y: 195, width: 130, height: 18 },
      { x: 2050, y: 230, width: 145, height: 18 },
      { x: 2320, y: 185, width: 120, height: 18 },
      { x: 2670, y: 225, width: 165, height: 18 },
      { x: 2990, y: 178, width: 130, height: 18 },
    ];
    const blueCollectibleSeeds = [
      [190, 275], [385, 200], [660, 275], [805, 185], [1070, 150],
      [1270, 275], [1410, 195], [1600, 275], [1730, 155], [1970, 275],
      [2090, 190], [2370, 145], [2510, 275], [2730, 185], [2920, 275],
      [3040, 138], [3160, 275],
    ];
    let blueCollectibles = [];

    const radioNeedle = document.getElementById("radio-game-needle");
    const radioTapButton = document.getElementById("radio-game-tap");
    let radioStartedAt = 0;
    let radioNeedlePosition = 50;
    let radioNeedleVelocity = 0.8;

    function updateScore(points) {
      score += points;
      scoreEl.textContent = String(score);
    }

    function setControlsEnabled(enabled) {
      blueButtons.forEach((button) => { button.disabled = !enabled; });
      if (radioTapButton) radioTapButton.disabled = !enabled;
      if (blueField) blueField.classList.toggle("is-playing", enabled);
    }

    function resetBlueStage() {
      bluePlayer.x = 60;
      bluePlayer.y = 80;
      bluePlayer.velocityX = 0;
      bluePlayer.velocityY = 0;
      bluePlayer.onGround = false;
      bluePlayer.checkpointX = 60;
      blueCameraX = 0;
      blueFinalHoleFallInProgress = false;
      blueInput.left = false;
      blueInput.right = false;
      blueInput.jumpQueued = false;
      blueCollectibles = blueCollectibleSeeds.map(([x, y]) => ({ x, y, collected: false }));
    }

    function resetBlueGame() {
      blueStage = "blue";
      blueRedStageBonusApplied = false;
      blueRedBookRedirectScheduled = false;
      if (blueField) blueField.dataset.blueStage = blueStage;
      resetBlueStage();
    }

    function rectanglesOverlap(a, b) {
      return a.x < b.x + b.width && a.x + a.width > b.x &&
        a.y < b.y + b.height && a.y + a.height > b.y;
    }

    function playBluePickupSound() {
      try {
        if (window.WoodshedAudio && typeof window.WoodshedAudio.play === "function") {
          window.WoodshedAudio.play("arcadePickup");
        }
      } catch (_error) {
        // A pickup must never interrupt game play when browser audio is unavailable.
      }
    }

    function playerFellThroughBlueFinalHole() {
      const center = bluePlayer.x + bluePlayer.width / 2;
      return center >= BLUE_FINAL_HOLE.left && center <= BLUE_FINAL_HOLE.right;
    }

    function trackBlueFinalHoleFall() {
      if (
        blueStage === "blue" && bluePlayer.velocityY > 0 &&
        bluePlayer.y >= BLUE_GROUND_Y && playerFellThroughBlueFinalHole()
      ) {
        blueFinalHoleFallInProgress = true;
      }
    }

    function enterBlueRedStage() {
      if (blueStage === "red" || !blueFinalHoleFallInProgress) return false;
      blueStage = "red";
      if (blueField) blueField.dataset.blueStage = blueStage;
      resetBlueStage();
      grantBlueRedStageBonus();
      message.textContent = "Red stage! +10 seconds.";
      return true;
    }

    function updateBlueGame(deltaSeconds) {
      const oldBottom = bluePlayer.y + bluePlayer.height;
      const direction = Number(blueInput.right) - Number(blueInput.left);
      bluePlayer.velocityX = direction * 250;
      if (blueInput.jumpQueued && bluePlayer.onGround) {
        bluePlayer.velocityY = -535;
        bluePlayer.onGround = false;
      }
      blueInput.jumpQueued = false;
      bluePlayer.velocityY += 1350 * deltaSeconds;
      bluePlayer.x += bluePlayer.velocityX * deltaSeconds;
      bluePlayer.x = Math.max(0, Math.min(BLUE_WORLD_WIDTH - bluePlayer.width, bluePlayer.x));
      bluePlayer.y += bluePlayer.velocityY * deltaSeconds;
      bluePlayer.onGround = false;

      if (bluePlayer.velocityY >= 0) {
        for (const platform of bluePlatforms) {
          const newBottom = bluePlayer.y + bluePlayer.height;
          const crossesTop = oldBottom <= platform.y && newBottom >= platform.y;
          const overlapsX = bluePlayer.x + bluePlayer.width > platform.x &&
            bluePlayer.x < platform.x + platform.width;
          if (crossesTop && overlapsX) {
            bluePlayer.y = platform.y - bluePlayer.height;
            bluePlayer.velocityY = 0;
            bluePlayer.onGround = true;
            if (platform.y === BLUE_GROUND_Y) bluePlayer.checkpointX = bluePlayer.x;
            break;
          }
        }
      }

      trackBlueFinalHoleFall();

      if (bluePlayer.y > (blueCanvas ? blueCanvas.height : 360) + 80) {
        if (blueStage === "blue" && enterBlueRedStage()) return;
        bluePlayer.x = Math.max(0, bluePlayer.checkpointX - 35);
        bluePlayer.y = 80;
        bluePlayer.velocityX = 0;
        bluePlayer.velocityY = 0;
      }

      for (const collectible of blueCollectibles) {
        if (collectible.collected) continue;
        const hitbox = { x: collectible.x - 12, y: collectible.y - 12, width: 24, height: 24 };
        if (rectanglesOverlap(bluePlayer, hitbox)) {
          collectible.collected = true;
          updateScore(10);
          playBluePickupSound();
        }
      }

      if (blueCanvas) {
        blueCameraX = Math.max(
          0,
          Math.min(BLUE_WORLD_WIDTH - blueCanvas.width, bluePlayer.x - blueCanvas.width * 0.34),
        );
      }
    }

    function drawBlueGame() {
      if (!blueCanvas || !blueContext) return;
      const width = blueCanvas.width;
      const height = blueCanvas.height;
      const redStage = blueStage === "red";
      const palette = redStage
        ? { sky: "#ffd0ce", middle: "#d95a54", ground: "#8d2026", platform: "#5b1820", trim: "#ff8178", player: "#c5262f", outline: "#7b1420" }
        : { sky: "#bcecff", middle: "#5aa9df", ground: "#2467a3", platform: "#143b67", trim: "#5dd5e8", player: "#075ed1", outline: "#073b83" };
      const gradient = blueContext.createLinearGradient(0, 0, 0, height);
      gradient.addColorStop(0, palette.sky);
      gradient.addColorStop(0.72, palette.middle);
      gradient.addColorStop(1, palette.ground);
      blueContext.fillStyle = gradient;
      blueContext.fillRect(0, 0, width, height);

      blueContext.fillStyle = "rgba(255, 255, 255, 0.6)";
      for (let x = 90; x < BLUE_WORLD_WIDTH; x += 390) {
        const screenX = x - blueCameraX;
        blueContext.beginPath();
        blueContext.arc(screenX, 65, 24, 0, Math.PI * 2);
        blueContext.arc(screenX + 26, 70, 31, 0, Math.PI * 2);
        blueContext.arc(screenX + 56, 65, 22, 0, Math.PI * 2);
        blueContext.fill();
      }

      blueContext.fillStyle = palette.platform;
      for (const platform of bluePlatforms) {
        blueContext.fillRect(platform.x - blueCameraX, platform.y, platform.width, platform.height);
        blueContext.fillStyle = palette.trim;
        blueContext.fillRect(platform.x - blueCameraX, platform.y, platform.width, 7);
        blueContext.fillStyle = palette.platform;
      }

      for (const collectible of blueCollectibles) {
        if (collectible.collected) continue;
        const x = collectible.x - blueCameraX;
        blueContext.fillStyle = "#e8fbff";
        blueContext.beginPath();
        blueContext.arc(x, collectible.y, 11, 0, Math.PI * 2);
        blueContext.fill();
        blueContext.strokeStyle = redStage ? "#d3343c" : "#0571e3";
        blueContext.lineWidth = 5;
        blueContext.stroke();
      }

      const playerX = bluePlayer.x - blueCameraX;
      blueContext.fillStyle = palette.player;
      blueContext.fillRect(playerX, bluePlayer.y, bluePlayer.width, bluePlayer.height);
      blueContext.fillStyle = "#e9fbff";
      blueContext.fillRect(playerX + 8, bluePlayer.y + 9, 6, 7);
      blueContext.fillRect(playerX + 22, bluePlayer.y + 9, 6, 7);
      blueContext.fillStyle = palette.outline;
      blueContext.fillRect(playerX + 8, bluePlayer.y + 31, 20, 5);
    }

    function blueLoop(timestamp) {
      if (!running || gameKey !== "blue") return;
      const delta = bluePreviousTime ? Math.min((timestamp - bluePreviousTime) / 1000, 0.034) : 0;
      bluePreviousTime = timestamp;
      updateBlueGame(delta);
      drawBlueGame();
      blueFrame = window.requestAnimationFrame(blueLoop);
    }

    // The Red-stage transition will call this once when that stage is added.
    // It extends the active timer instead of replacing the time remaining.
    function grantBlueRedStageBonus() {
      if (!running || gameKey !== "blue" || blueRedStageBonusApplied) return false;
      blueRedStageBonusApplied = true;
      endTime += BLUE_RED_STAGE_BONUS_SECONDS * 1000;
      updateTimer();
      return true;
    }

    function setBlueAction(action, pressed) {
      if (action === "jump") {
        if (pressed) blueInput.jumpQueued = true;
        return;
      }
      if (action === "left" || action === "right") blueInput[action] = pressed;
    }

    blueButtons.forEach((button) => {
      const action = button.dataset.blueAction;
      button.addEventListener("pointerdown", (event) => {
        if (!running) return;
        event.preventDefault();
        button.setPointerCapture(event.pointerId);
        setBlueAction(action, true);
      });
      ["pointerup", "pointercancel", "lostpointercapture"].forEach((eventName) => {
        button.addEventListener(eventName, () => setBlueAction(action, false));
      });
    });

    const blueKeys = {
      ArrowLeft: "left", KeyA: "left", ArrowRight: "right", KeyD: "right",
      ArrowUp: "jump", KeyW: "jump", Space: "jump",
    };
    document.addEventListener("keydown", (event) => {
      const action = blueKeys[event.code];
      if (!running || gameKey !== "blue" || !action) return;
      event.preventDefault();
      if (action !== "jump" || !event.repeat) setBlueAction(action, true);
    });
    document.addEventListener("keyup", (event) => {
      const action = blueKeys[event.code];
      if (!action || gameKey !== "blue") return;
      event.preventDefault();
      setBlueAction(action, false);
    });

    function renderRadioState() {
      if (radioNeedle) radioNeedle.style.left = `${radioNeedlePosition}%`;
      if (gameKey === "radio-tuner" && running) {
        const elapsed = Math.floor((Date.now() - radioStartedAt) / 1000);
        timeEl.textContent = String(Math.max(0, RADIO_GAME_SECONDS - elapsed));
      }
    }

    function tickRadioGame() {
      const elapsedMs = Date.now() - radioStartedAt;
      if (elapsedMs >= RADIO_GAME_SECONDS * 1000) {
        void finishGame();
        renderRadioState();
        return;
      }

      radioNeedlePosition += radioNeedleVelocity;
      if (radioNeedlePosition >= 98 || radioNeedlePosition <= 2) {
        radioNeedleVelocity *= -1;
        radioNeedlePosition = Math.max(2, Math.min(98, radioNeedlePosition));
      }

      const secondsElapsed = elapsedMs / 1000;
      const speedBoost = 1 + secondsElapsed / 42;
      radioNeedleVelocity += radioNeedleVelocity > 0
        ? 0.003 * speedBoost
        : -0.003 * speedBoost;
      renderRadioState();
    }

    function tuneRadioSignal() {
      if (!running || gameKey !== "radio-tuner") return;
      const distance = Math.abs(radioNeedlePosition - RADIO_CENTER);

      if (distance <= RADIO_GOLD_ZONE) {
        const bonus = Math.round((RADIO_GOLD_ZONE - distance) * 18);
        score += 100 + bonus;
        message.textContent = "Locked!";
      } else if (distance <= RADIO_GOLD_ZONE * 2) {
        score += 25;
        message.textContent = "Close. Static cleared a little.";
      } else {
        score = Math.max(0, score - 20);
        message.textContent = "Static.";
      }

      score = Math.min(score, RADIO_MAX_SCORE);
      scoreEl.textContent = String(score);
      renderRadioState();
    }

    if (radioTapButton) radioTapButton.addEventListener("click", tuneRadioSignal);

    async function finishGame() {
      if (!running) return;
      running = false;
      if (timer) window.clearInterval(timer);
      timer = null;
      if (blueFrame) window.cancelAnimationFrame(blueFrame);
      blueFrame = null;
      setControlsEnabled(false);
      startButton.disabled = false;
      startButton.textContent = "Play Again";
      timeEl.textContent = "0";
      message.textContent = `Time! You scored ${score}. Saving…`;
      if (gameKey === "blue") {
        window.dispatchEvent(new CustomEvent("woodshed:celebrate", {
          detail: { source: "arcade-blue" },
        }));
      }
      try {
        const payload = await window.WoodshedArcadeEconomy.completePlay(
          activePlayToken, score
        );
        bestEl.textContent = String(payload.best_score);
        renderLeaderboard(leaderboard, payload.leaderboard);
        message.textContent = payload.updated
          ? `New personal best: ${payload.best_score}!`
          : `Score saved. Personal best: ${payload.best_score}.`;
      } catch (error) {
        message.textContent = error.message || "Your score could not be saved.";
      } finally {
        if (gameKey === "blue" && blueStage === "red" && !blueRedBookRedirectScheduled) {
          blueRedBookRedirectScheduled = true;
          window.setTimeout(function () { window.location.assign("/p-book"); }, 700);
        }
      }
    }

    function updateTimer() {
      if (!running) return;
      const remaining = Math.max(0, endTime - performance.now());
      timeEl.textContent = String(Math.ceil(remaining / 1000));
      if (remaining <= 0) void finishGame();
    }

    async function startGame() {
      if (running || starting) return;
      starting = true;
      startButton.disabled = true;
      message.textContent = "Starting…";
      try {
        const play = await window.WoodshedArcadeEconomy.startPlay(gameKey);
        activePlayToken = play.play_token;
      } catch (error) {
        message.textContent = error.message || "That game could not start.";
        startButton.disabled = false;
        starting = false;
        return;
      }
      starting = false;
      running = true;
      score = 0;
      scoreEl.textContent = "0";
      timeEl.textContent = String(gameKey === "blue" ? BLUE_GAME_SECONDS : RADIO_GAME_SECONDS);
      message.textContent = "Go!";
      startButton.disabled = true;
      setControlsEnabled(true);
      if (gameKey === "blue") {
        endTime = performance.now() + BLUE_GAME_SECONDS * 1000;
        resetBlueGame();
        bluePreviousTime = 0;
        blueFrame = window.requestAnimationFrame(blueLoop);
        timer = window.setInterval(updateTimer, 100);
      } else {
        radioStartedAt = Date.now();
        radioNeedlePosition = 50;
        radioNeedleVelocity = Math.random() > 0.5 ? 0.8 : -0.8;
        startButton.textContent = "Game Running";
        if (radioTapButton) radioTapButton.focus();
        message.textContent = "Tap when the needle is in the gold zone.";
        renderRadioState();
        timer = window.setInterval(tickRadioGame, RADIO_TICK_MS);
      }
    }

    startButton.addEventListener("click", function () { void startGame(); });
    window.addEventListener("pagehide", () => {
      running = false;
      if (timer) window.clearInterval(timer);
      timer = null;
      if (blueFrame) window.cancelAnimationFrame(blueFrame);
      blueFrame = null;
    });

    resetBlueGame();
    drawBlueGame();
    renderRadioState();
    if (window.WoodshedArcadeEconomy) {
      window.WoodshedArcadeEconomy.loadStatus(gameKey).catch(function () {});
    }
    loadScores(gameKey).then((payload) => {
      bestEl.textContent = String(payload.best_score);
      renderLeaderboard(leaderboard, payload.leaderboard);
    }).catch(() => {
      renderLeaderboard(leaderboard, []);
    });
  }

  wireArcadeRoom();
  wireArcadeGame();
})();
