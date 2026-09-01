(function (root) {
  "use strict";

  const GAME_KEY = "dressed-to-the-nines";
  const GAME_SECONDS = 30;
  const NINES_QUESTIONS = Object.freeze([
    Object.freeze({ tonality: "C Major", start: "C", answer: "D" }),
    Object.freeze({ tonality: "D Minor", start: "D", answer: "E" }),
    Object.freeze({ tonality: "F Major", start: "F", answer: "G" }),
    Object.freeze({ tonality: "G Major", start: "G", answer: "A" }),
    Object.freeze({ tonality: "A Minor", start: "A", answer: "B" }),
    Object.freeze({ tonality: "Bb Major", start: "Bb", answer: "C" }),
    Object.freeze({ tonality: "Eb Major", start: "Eb", answer: "F" }),
  ]);

  function normalizeAnswer(value) {
    return String(value == null ? "" : value).trim().toUpperCase();
  }

  class DressedToTheNinesGame {
    constructor(options) {
      const settings = options || {};
      this.questions = settings.questions || NINES_QUESTIONS;
      this.random = settings.random || Math.random;
      this.status = "ready";
      this.score = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.currentQuestion = null;
      this.previousTonality = null;
    }

    chooseQuestion() {
      if (!this.questions.length) throw new Error("Dressed to the Nines needs questions.");
      let choices = this.questions;
      if (choices.length > 1 && this.previousTonality) {
        choices = choices.filter((question) => question.tonality !== this.previousTonality);
      }
      const position = Math.min(
        choices.length - 1,
        Math.max(0, Math.floor(this.random() * choices.length))
      );
      this.currentQuestion = choices[position];
      this.previousTonality = this.currentQuestion.tonality;
      return this.currentQuestion;
    }

    start() {
      this.status = "running";
      this.score = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.currentQuestion = null;
      this.previousTonality = null;
      this.chooseQuestion();
      return this.snapshot();
    }

    submit(value) {
      if (this.status !== "running" || !this.currentQuestion) {
        return { accepted: false, reason: "not-running" };
      }
      const answer = normalizeAnswer(value);
      if (!/^[A-G]$/.test(answer)) {
        return { accepted: false, reason: "invalid-answer" };
      }
      const answeredQuestion = this.currentQuestion;
      const correct = answer === answeredQuestion.answer;
      if (correct) this.score += 1;
      this.chooseQuestion();
      return {
        accepted: true,
        correct,
        answer,
        tonality: answeredQuestion.tonality,
        score: this.score,
        nextTonality: this.currentQuestion.tonality,
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
        currentQuestion: this.currentQuestion,
      };
    }
  }

  root.DressedToTheNinesGame = DressedToTheNinesGame;
  root.NINES_QUESTIONS = NINES_QUESTIONS;
  root.NINES_RULES = Object.freeze({
    gameKey: GAME_KEY,
    gameSeconds: GAME_SECONDS,
    normalizeAnswer,
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      DressedToTheNinesGame,
      NINES_QUESTIONS,
      NINES_RULES: root.NINES_RULES,
      normalizeAnswer,
    };
  }

  if (typeof document === "undefined") return;
  const page = document.querySelector("[data-nines-game]");
  if (!page) return;

  const game = new DressedToTheNinesGame();
  const scoreOutput = document.getElementById("nines-score");
  const bestOutput = document.getElementById("nines-best");
  const timeOutput = document.getElementById("nines-time");
  const tonalityOutput = document.getElementById("nines-tonality");
  const questionOutput = document.getElementById("nines-question");
  const answerButtons = Array.from(page.querySelectorAll("[data-nines-answer]"));
  const startButton = document.getElementById("nines-start");
  const message = document.getElementById("nines-message");
  const leaderboard = page.querySelector("[data-arcade-leaderboard]");
  let timer = null;
  let lastTickAt = 0;
  let activePlayToken = null;
  let finishPromise = null;
  let starting = false;
  let answerLocked = false;

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

  function render() {
    const state = game.snapshot();
    scoreOutput.textContent = String(state.score);
    timeOutput.textContent = String(Math.ceil(state.remainingMs / 1000));
    tonalityOutput.textContent = state.currentQuestion
      ? state.currentQuestion.tonality.toUpperCase()
      : "READY";
    questionOutput.textContent = state.currentQuestion
      ? `Ninth above ${state.currentQuestion.start}?`
      : "Choose New Game";
    const running = state.status === "running";
    answerButtons.forEach(function (button) {
      button.disabled = !running || answerLocked;
    });
    startButton.disabled = running || starting;
    startButton.textContent = running ? "Game Running" : "New Game";
  }

  function finishGame() {
    if (finishPromise) return finishPromise;
    window.clearInterval(timer);
    timer = null;
    game.status = "ended";
    render();
    message.textContent = `Final score: ${game.score}`;
    const token = activePlayToken;
    activePlayToken = null;
    finishPromise = root.WoodshedArcadeEconomy.completePlay(token, game.score)
      .then(function (payload) {
        renderLeaderboard(payload);
        return payload;
      })
      .catch(function (error) {
        message.textContent = `${message.textContent} · ${error.message}`;
        throw error;
      });
    return finishPromise;
  }

  function tick() {
    const now = performance.now();
    game.elapse(now - lastTickAt);
    lastTickAt = now;
    render();
    if (game.status === "ended") finishGame().catch(function () {});
  }

  function startGame() {
    if (starting || game.status === "running") return;
    starting = true;
    startButton.disabled = true;
    message.textContent = "Starting…";
    root.WoodshedArcadeEconomy.startPlay(GAME_KEY).then(function (payload) {
      activePlayToken = payload.play_token;
      finishPromise = null;
      answerLocked = false;
      game.start();
      message.textContent = "Choose the ninth.";
      lastTickAt = performance.now();
      window.clearInterval(timer);
      timer = window.setInterval(tick, 100);
      render();
      answerButtons[0].focus();
    }).catch(function (error) {
      message.textContent = error.message;
    }).finally(function () {
      starting = false;
      render();
    });
  }

  answerButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      if (answerLocked) return;
      answerLocked = true;
      const result = game.submit(button.dataset.ninesAnswer);
      if (!result.accepted) {
        answerLocked = false;
        return;
      }
      message.textContent = result.correct ? "Correct!" : "Next question.";
      render();
      window.setTimeout(function () {
        answerLocked = false;
        render();
      }, 160);
    });
  });
  startButton.addEventListener("click", startGame);

  root.WoodshedArcadeEconomy.loadStatus(GAME_KEY).catch(function (error) {
    message.textContent = error.message;
  });
  loadScores().catch(function () {});
  render();
}(typeof window !== "undefined" ? window : globalThis));
