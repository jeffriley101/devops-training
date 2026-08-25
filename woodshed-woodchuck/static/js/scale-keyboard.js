(function (root) {
  "use strict";

  const GAME_SECONDS = 30;
  const CORRECT_NOTE_POINTS = 100;
  const SCALE_BONUS = 500;
  const WRONG_NOTE_PENALTY = 50;
  const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);

  function midiToFrequency(midi) {
    const note = Number(midi);
    if (!Number.isFinite(note)) return null;
    return 440 * Math.pow(2, (note - 69) / 12);
  }

  class ScaleKeyboardGame {
    constructor(options) {
      const settings = options || {};
      this.scales = settings.scales || [];
      this.random = settings.random || Math.random;
      this.status = "ready";
      this.score = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.currentScale = null;
      this.previousScaleKey = null;
      this.noteIndex = 0;
    }

    chooseScale() {
      if (!this.scales.length) throw new Error("Scale Keyboard needs scale data.");
      let choices = this.scales;
      if (this.scales.length > 1 && this.previousScaleKey) {
        choices = this.scales.filter((scale) => scale.key !== this.previousScaleKey);
      }
      const index = Math.min(choices.length - 1, Math.floor(this.random() * choices.length));
      this.currentScale = choices[Math.max(0, index)];
      this.previousScaleKey = this.currentScale.key;
      this.noteIndex = 0;
      return this.currentScale;
    }

    start() {
      this.status = "running";
      this.score = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.previousScaleKey = null;
      this.chooseScale();
      return this.snapshot();
    }

    expectedMidi() {
      if (!this.currentScale) return null;
      return this.currentScale.rootMidi + this.currentScale.notes[this.noteIndex][0];
    }

    press(midi) {
      if (this.status !== "running") return { accepted: false, reason: "not-running" };
      if (midi !== this.expectedMidi()) {
        this.score = Math.max(0, this.score - WRONG_NOTE_PENALTY);
        return { accepted: true, correct: false, score: this.score };
      }
      this.score += CORRECT_NOTE_POINTS;
      this.noteIndex += 1;
      const completed = this.noteIndex >= this.currentScale.notes.length;
      if (completed) this.score += SCALE_BONUS;
      return {
        accepted: true,
        correct: true,
        completed,
        score: this.score,
      };
    }

    elapse(milliseconds) {
      if (this.status !== "running") return this.remainingMs;
      this.remainingMs = Math.max(0, this.remainingMs - Math.max(0, milliseconds));
      if (this.remainingMs === 0) this.status = "ended";
      return this.remainingMs;
    }

    snapshot() {
      return {
        status: this.status,
        score: this.score,
        remainingMs: this.remainingMs,
        currentScale: this.currentScale,
        noteIndex: this.noteIndex,
      };
    }
  }

  root.ScaleKeyboardGame = ScaleKeyboardGame;
  root.SCALE_KEYBOARD_RULES = Object.freeze({
    gameSeconds: GAME_SECONDS,
    correctNotePoints: CORRECT_NOTE_POINTS,
    scaleBonus: SCALE_BONUS,
    wrongNotePenalty: WRONG_NOTE_PENALTY,
    midiToFrequency: midiToFrequency,
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      ScaleKeyboardGame,
      SCALE_KEYBOARD_RULES: root.SCALE_KEYBOARD_RULES,
      midiToFrequency,
    };
  }

  if (typeof document === "undefined") return;
  const page = document.querySelector("[data-scale-keyboard]");
  if (!page) return;

  const scales = root.SCALE_KEYBOARD_SCALES || [];
  const game = new ScaleKeyboardGame({ scales });
  const keyboard = document.getElementById("scale-keyboard-keys");
  const prompt = document.getElementById("scale-keyboard-prompt");
  const progress = document.getElementById("scale-keyboard-progress");
  const scoreOutput = document.getElementById("scale-keyboard-score");
  const bestOutput = document.getElementById("scale-keyboard-best");
  const timeOutput = document.getElementById("scale-keyboard-time");
  const message = document.getElementById("scale-keyboard-message");
  const startButton = document.getElementById("scale-keyboard-start");
  const leaderboard = page.querySelector("[data-arcade-leaderboard]");
  let timer = null;
  let lastTickAt = 0;
  let nextScaleTimer = null;
  let activePlayToken = null;
  let finishPromise = null;
  let betweenScales = false;

  function playFeedback(name) {
    try { if (root.WoodshedAudio) root.WoodshedAudio.play(name); } catch (_error) { /* Supplemental only. */ }
  }

  function renderLeaderboard(payload) {
    if (bestOutput) bestOutput.textContent = String(payload.best_score || 0);
    leaderboard.replaceChildren();
    const rows = Array.isArray(payload.leaderboard) ? payload.leaderboard : [];
    if (!rows.length) {
      const empty = document.createElement("li");
      empty.textContent = "No scores yet.";
      leaderboard.appendChild(empty);
      return;
    }
    rows.forEach(function (row) {
      const item = document.createElement("li");
      item.value = Number(row.rank);
      item.textContent = `${row.display_name}${row.is_current_user ? " (You)" : ""} — ${row.score}`;
      leaderboard.appendChild(item);
    });
  }

  function renderKeyboard() {
    keyboard.replaceChildren();
    if (!game.currentScale) return;
    const firstMidi = game.currentScale.rootMidi;
    const pitches = Array.from({ length: 18 }, (_value, index) => firstMidi + index);
    const whitePitches = pitches.filter((midi) => !BLACK_PITCH_CLASSES.has(midi % 12));
    whitePitches.forEach(function (midi) {
      const key = document.createElement("button");
      key.type = "button";
      key.className = "scale-piano-key is-white";
      key.dataset.scaleMidi = String(midi);
      key.setAttribute("aria-label", `Piano key ${midi}`);
      key.addEventListener("click", function () { pressKey(midi, key); });
      keyboard.appendChild(key);
    });
    pitches.filter((midi) => BLACK_PITCH_CLASSES.has(midi % 12)).forEach(function (midi) {
      const whitesBefore = pitches.filter((pitch) => pitch < midi && !BLACK_PITCH_CLASSES.has(pitch % 12)).length;
      const key = document.createElement("button");
      key.type = "button";
      key.className = "scale-piano-key is-black";
      key.dataset.scaleMidi = String(midi);
      key.style.left = `${((whitesBefore - 0.32) / whitePitches.length) * 100}%`;
      key.setAttribute("aria-label", `Black piano key ${midi}`);
      key.addEventListener("click", function () { pressKey(midi, key); });
      keyboard.appendChild(key);
    });
  }

  function render() {
    const state = game.snapshot();
    scoreOutput.textContent = String(state.score);
    timeOutput.textContent = String(Math.ceil(state.remainingMs / 1000));
    startButton.disabled = state.status === "running";
    startButton.textContent = state.status === "running" ? "Game Running" : "New Game";
    if (state.currentScale) {
      prompt.textContent = `PLAY: ${state.currentScale.name.toUpperCase()}`;
      progress.textContent = state.currentScale.notes.map(function (note, index) {
        return index < state.noteIndex ? note[1] : "_";
      }).join(" ");
    }
    keyboard.querySelectorAll("button").forEach(function (key) {
      key.disabled = state.status !== "running" || betweenScales;
    });
  }

  function loadNextScale() {
    betweenScales = false;
    game.chooseScale();
    renderKeyboard();
    message.textContent = "Next scale!";
    render();
  }

  function pressKey(midi, key) {
    if (betweenScales) return;
    const result = game.press(midi);
    if (!result.accepted) return;
    try {
      if (root.WoodshedAudio) {
        root.WoodshedAudio.playPianoPitch(midiToFrequency(midi));
      }
    } catch (_error) { /* Pitch playback is supplemental to scoring. */ }
    key.classList.remove("is-correct", "is-wrong");
    key.classList.add(result.correct ? "is-correct" : "is-wrong");
    root.setTimeout(function () { key.classList.remove("is-correct", "is-wrong"); }, 180);
    playFeedback(result.correct ? "correctTrivia" : "incorrectTrivia");
    if (result.completed) {
      betweenScales = true;
      playFeedback("arcadeCheer");
      message.textContent = `${game.currentScale.name} complete! +500`;
      keyboard.querySelectorAll("button").forEach(function (button) { button.disabled = true; });
      nextScaleTimer = root.setTimeout(loadNextScale, 500);
    } else {
      message.textContent = result.correct ? "+100" : "Try the next note. -50";
    }
    render();
  }

  function finishRun() {
    if (finishPromise) return finishPromise;
    if (timer !== null) root.clearInterval(timer);
    timer = null;
    if (nextScaleTimer !== null) root.clearTimeout(nextScaleTimer);
    nextScaleTimer = null;
    game.status = "ended";
    render();
    message.textContent = `Time! Final score: ${game.score}. Saving…`;
    finishPromise = root.WoodshedArcadeEconomy.completePlay(activePlayToken, game.score)
      .then(function (payload) {
        renderLeaderboard(payload);
        message.textContent = payload.updated
          ? `New personal best: ${payload.best_score}!`
          : `Score saved. Personal best: ${payload.best_score}.`;
        return payload;
      }).catch(function (error) {
        message.textContent = error.message || "That score could not be saved.";
        return null;
      });
    return finishPromise;
  }

  function tick() {
    const now = performance.now();
    game.elapse(now - lastTickAt);
    lastTickAt = now;
    render();
    if (game.status === "ended") finishRun();
  }

  async function startGame() {
    if (game.status === "running") return;
    startButton.disabled = true;
    message.textContent = "Starting…";
    try {
      const play = await root.WoodshedArcadeEconomy.startPlay("scale-keyboard");
      activePlayToken = play.play_token;
    } catch (error) {
      message.textContent = error.message || "That game could not start.";
      startButton.disabled = false;
      return;
    }
    if (root.WoodshedAudio) root.WoodshedAudio.unlock();
    finishPromise = null;
    betweenScales = false;
    game.start();
    renderKeyboard();
    lastTickAt = performance.now();
    timer = root.setInterval(tick, 100);
    message.textContent = "Play the scale upward.";
    render();
  }

  startButton.addEventListener("click", function () { void startGame(); });
  render();
  root.WoodshedArcadeEconomy.loadStatus("scale-keyboard").catch(function () {});
  fetch("/arcade/scores/scale-keyboard", { credentials: "same-origin", cache: "no-store" })
    .then(function (response) { return response.ok ? response.json() : null; })
    .then(function (payload) { if (payload) renderLeaderboard(payload); })
    .catch(function () {});
}(typeof globalThis !== "undefined" ? globalThis : this));
