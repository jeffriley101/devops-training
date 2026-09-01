(function (root) {
  "use strict";

  const GAME_KEY = "interval-basic-training";
  const GAME_SECONDS = 30;
  const MAX_MISTAKES = 2;
  const NOTE_DURATION_SECONDS = 0.35;
  const NOTE_DURATION_MS = 350;
  const NOTE_GAP_MS = 120;
  const SECOND_NOTE_DELAY_MS = NOTE_DURATION_MS + NOTE_GAP_MS;
  const SEQUENCE_DURATION_MS = SECOND_NOTE_DELAY_MS + NOTE_DURATION_MS;
  const SOUNDTRACK_RUN_STATE_EVENT = "woodshed:arcade-soundtrack-run-state";
  const INTERVAL_QUESTIONS = Object.freeze([
    Object.freeze({ label: "Unison", firstMidi: 60, secondMidi: 60, firstNote: "C4", secondNote: "C4" }),
    Object.freeze({ label: "2nd", firstMidi: 60, secondMidi: 62, firstNote: "C4", secondNote: "D4" }),
    Object.freeze({ label: "3rd", firstMidi: 60, secondMidi: 64, firstNote: "C4", secondNote: "E4" }),
    Object.freeze({ label: "4th", firstMidi: 60, secondMidi: 65, firstNote: "C4", secondNote: "F4" }),
    Object.freeze({ label: "5th", firstMidi: 60, secondMidi: 67, firstNote: "C4", secondNote: "G4" }),
    Object.freeze({ label: "6th", firstMidi: 60, secondMidi: 69, firstNote: "C4", secondNote: "A4" }),
    Object.freeze({ label: "7th", firstMidi: 60, secondMidi: 71, firstNote: "C4", secondNote: "B4" }),
    Object.freeze({ label: "Octave", firstMidi: 60, secondMidi: 72, firstNote: "C4", secondNote: "C5" }),
    Object.freeze({ label: "9th", firstMidi: 60, secondMidi: 74, firstNote: "C4", secondNote: "D5" }),
  ]);

  function midiToFrequency(midi) {
    const note = Number(midi);
    if (!Number.isFinite(note)) return null;
    return 440 * Math.pow(2, (note - 69) / 12);
  }

  class IntervalBasicTrainingGame {
    constructor(options) {
      const settings = options || {};
      this.questions = settings.questions || INTERVAL_QUESTIONS;
      this.random = settings.random || Math.random;
      this.status = "ready";
      this.score = 0;
      this.mistakes = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.currentQuestion = null;
      this.previousLabel = null;
      this.endReason = null;
      this.submitted = false;
    }

    chooseQuestion() {
      if (!this.questions.length) throw new Error("Interval Basic Training needs questions.");
      let choices = this.questions;
      if (choices.length > 1 && this.previousLabel) {
        choices = choices.filter((question) => question.label !== this.previousLabel);
      }
      const position = Math.min(
        choices.length - 1,
        Math.max(0, Math.floor(this.random() * choices.length))
      );
      this.currentQuestion = choices[position];
      this.previousLabel = this.currentQuestion.label;
      return this.currentQuestion;
    }

    start() {
      this.status = "running";
      this.score = 0;
      this.mistakes = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.currentQuestion = null;
      this.previousLabel = null;
      this.endReason = null;
      this.submitted = false;
      this.chooseQuestion();
      return this.snapshot();
    }

    answer(label) {
      if (this.status !== "running" || !this.currentQuestion) {
        return { accepted: false, reason: "not-running" };
      }
      const answeredQuestion = this.currentQuestion;
      const correct = label === answeredQuestion.label;
      if (correct) {
        this.score += 1;
      } else {
        this.mistakes += 1;
      }
      if (this.mistakes >= MAX_MISTAKES) {
        this.status = "ended";
        this.endReason = "two-mistakes";
      } else {
        this.chooseQuestion();
      }
      return {
        accepted: true,
        correct,
        score: this.score,
        mistakes: this.mistakes,
        ended: this.status === "ended",
        endReason: this.endReason,
      };
    }

    replay() {
      if (this.status !== "running" || !this.currentQuestion) {
        return { accepted: false, reason: "not-running" };
      }
      return { accepted: true, question: this.currentQuestion };
    }

    elapse(milliseconds) {
      if (this.status !== "running") return this.remainingMs;
      this.remainingMs = Math.max(0, this.remainingMs - Math.max(0, milliseconds));
      if (this.remainingMs === 0) {
        this.status = "ended";
        this.endReason = "time";
      }
      return this.remainingMs;
    }

    markSubmitted() {
      if (this.submitted) return false;
      this.submitted = true;
      return true;
    }

    snapshot() {
      return {
        status: this.status,
        score: this.score,
        mistakes: this.mistakes,
        remainingMs: this.remainingMs,
        currentQuestion: this.currentQuestion,
        endReason: this.endReason,
        submitted: this.submitted,
      };
    }
  }

  root.IntervalBasicTrainingGame = IntervalBasicTrainingGame;
  root.INTERVAL_BASIC_TRAINING_QUESTIONS = INTERVAL_QUESTIONS;
  root.INTERVAL_BASIC_TRAINING_RULES = Object.freeze({
    gameKey: GAME_KEY,
    gameSeconds: GAME_SECONDS,
    maxMistakes: MAX_MISTAKES,
    noteDurationSeconds: NOTE_DURATION_SECONDS,
    noteGapMs: NOTE_GAP_MS,
    secondNoteDelayMs: SECOND_NOTE_DELAY_MS,
    sequenceDurationMs: SEQUENCE_DURATION_MS,
    midiToFrequency,
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      IntervalBasicTrainingGame,
      INTERVAL_BASIC_TRAINING_QUESTIONS: INTERVAL_QUESTIONS,
      INTERVAL_BASIC_TRAINING_RULES: root.INTERVAL_BASIC_TRAINING_RULES,
      midiToFrequency,
    };
  }

  if (typeof document === "undefined") return;
  const page = document.querySelector("[data-interval-game]");
  if (!page) return;

  const game = new IntervalBasicTrainingGame();
  const scoreOutput = document.getElementById("interval-score");
  const bestOutput = document.getElementById("interval-best");
  const timeOutput = document.getElementById("interval-time");
  const mistakesOutput = document.getElementById("interval-mistakes");
  const answerButtons = Array.from(page.querySelectorAll("[data-interval-answer]"));
  const replayButton = document.getElementById("interval-replay");
  const startButton = document.getElementById("interval-start");
  const message = document.getElementById("interval-message");
  const leaderboard = page.querySelector("[data-arcade-leaderboard]");
  let timer = null;
  let lastTickAt = 0;
  let secondNoteTimer = null;
  let sequenceEndTimer = null;
  let audioGeneration = 0;
  let audioLocked = false;
  let answerLocked = false;
  let activePlayToken = null;
  let finishPromise = null;
  let starting = false;
  let saving = false;

  function setSoundtrackRunActive(active) {
    page.dispatchEvent(new root.CustomEvent(SOUNDTRACK_RUN_STATE_EVENT, {
      bubbles: true,
      detail: {
        gameKey: GAME_KEY,
        active: active === true,
        resumeDelayMs: active ? 0 : NOTE_DURATION_MS,
      },
    }));
  }

  function renderLeaderboard(payload) {
    bestOutput.textContent = String(payload.best_score || 0);
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

  function loadScores() {
    return fetch(`/arcade/scores/${GAME_KEY}`, {
      credentials: "same-origin",
      cache: "no-store",
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.detail || "Scores are unavailable.");
        renderLeaderboard(payload);
        return payload;
      });
    });
  }

  function cancelQuestionAudio() {
    audioGeneration += 1;
    if (secondNoteTimer !== null) root.clearTimeout(secondNoteTimer);
    if (sequenceEndTimer !== null) root.clearTimeout(sequenceEndTimer);
    secondNoteTimer = null;
    sequenceEndTimer = null;
    audioLocked = false;
  }

  function render() {
    const state = game.snapshot();
    const running = state.status === "running";
    scoreOutput.textContent = String(state.score);
    timeOutput.textContent = String(Math.ceil(state.remainingMs / 1000));
    mistakesOutput.textContent = `${state.mistakes} / ${MAX_MISTAKES}`;
    answerButtons.forEach(function (button) {
      button.disabled = !running || answerLocked || audioLocked;
    });
    replayButton.disabled = !running || answerLocked || audioLocked;
    startButton.disabled = running || starting || saving;
    startButton.textContent = running ? "Game Running" : "New Game";
  }

  function playCurrentInterval() {
    if (game.status !== "running" || !game.currentQuestion) return false;
    cancelQuestionAudio();
    const generation = audioGeneration;
    const question = game.currentQuestion;
    audioLocked = true;
    render();
    try {
      if (root.WoodshedAudio) {
        root.WoodshedAudio.playPianoPitch(
          midiToFrequency(question.firstMidi), NOTE_DURATION_SECONDS
        );
      }
    } catch (_error) { /* Audio remains supplemental to the state machine. */ }
    secondNoteTimer = root.setTimeout(function () {
      if (generation !== audioGeneration || game.status !== "running") return;
      try {
        if (root.WoodshedAudio) {
          root.WoodshedAudio.playPianoPitch(
            midiToFrequency(question.secondMidi), NOTE_DURATION_SECONDS
          );
        }
      } catch (_error) { /* Audio remains supplemental to the state machine. */ }
    }, SECOND_NOTE_DELAY_MS);
    sequenceEndTimer = root.setTimeout(function () {
      if (generation !== audioGeneration) return;
      audioLocked = false;
      secondNoteTimer = null;
      sequenceEndTimer = null;
      render();
    }, SEQUENCE_DURATION_MS);
    return true;
  }

  function finishGame() {
    if (finishPromise) return finishPromise;
    if (!game.markSubmitted()) return Promise.resolve(null);
    if (timer !== null) root.clearInterval(timer);
    timer = null;
    cancelQuestionAudio();
    setSoundtrackRunActive(false);
    answerLocked = false;
    saving = true;
    render();
    const endedByMistakes = game.endReason === "two-mistakes";
    message.textContent = endedByMistakes
      ? `Two wrong answers ended the run. Final score: ${game.score}. Saving…`
      : `Time! Final score: ${game.score}. Saving…`;
    const token = activePlayToken;
    activePlayToken = null;
    finishPromise = root.WoodshedArcadeEconomy.completePlay(token, game.score)
      .then(function (payload) {
        renderLeaderboard(payload);
        const saved = payload.updated
          ? `New personal best: ${payload.best_score}!`
          : `Score saved. Personal best: ${payload.best_score}.`;
        message.textContent = endedByMistakes
          ? `Two wrong answers ended the run. ${saved}`
          : saved;
        return payload;
      }).catch(function (error) {
        message.textContent = error.message || "That score could not be saved.";
        return null;
      }).finally(function () {
        saving = false;
        render();
      });
    return finishPromise;
  }

  function tick() {
    const now = performance.now();
    game.elapse(now - lastTickAt);
    lastTickAt = now;
    render();
    if (game.status === "ended") finishGame();
  }

  async function startGame() {
    if (starting || game.status === "running" || saving) return;
    starting = true;
    startButton.disabled = true;
    message.textContent = "Starting…";
    try {
      const play = await root.WoodshedArcadeEconomy.startPlay(GAME_KEY);
      activePlayToken = play.play_token;
    } catch (error) {
      message.textContent = error.message || "That game could not start.";
      starting = false;
      render();
      return;
    }
    setSoundtrackRunActive(true);
    try {
      if (root.WoodshedAudio) await root.WoodshedAudio.unlock();
    } catch (_error) { /* A muted/unavailable sound graph must not lose a paid run. */ }
    finishPromise = null;
    answerLocked = false;
    game.start();
    lastTickAt = performance.now();
    if (timer !== null) root.clearInterval(timer);
    timer = root.setInterval(tick, 100);
    message.textContent = "Listen, then choose the interval.";
    starting = false;
    render();
    playCurrentInterval();
  }

  answerButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      if (answerLocked || audioLocked) return;
      answerLocked = true;
      cancelQuestionAudio();
      const result = game.answer(button.dataset.intervalAnswer);
      if (!result.accepted) {
        answerLocked = false;
        render();
        return;
      }
      if (result.ended) {
        render();
        finishGame();
        return;
      }
      message.textContent = result.correct ? "Correct!" : "One mistake. Keep going.";
      render();
      root.setTimeout(function () {
        if (game.status !== "running") return;
        answerLocked = false;
        playCurrentInterval();
      }, 160);
    });
  });

  replayButton.addEventListener("click", function () {
    if (answerLocked || audioLocked) return;
    const result = game.replay();
    if (!result.accepted) return;
    message.textContent = "Replay.";
    playCurrentInterval();
  });
  startButton.addEventListener("click", function () { void startGame(); });

  root.WoodshedArcadeEconomy.loadStatus(GAME_KEY).catch(function (error) {
    message.textContent = error.message;
  });
  loadScores().catch(function () {});
  render();
}(typeof window !== "undefined" ? window : globalThis));
