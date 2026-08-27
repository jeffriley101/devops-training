(function (root) {
  "use strict";

  const GAME_SECONDS = 30;
  const THIRDS_CARDS = Object.freeze([
    Object.freeze({ chord: "C Major", answer: "E" }),
    Object.freeze({ chord: "D Minor", answer: "F" }),
    Object.freeze({ chord: "E Minor", answer: "G" }),
    Object.freeze({ chord: "F Major", answer: "A" }),
    Object.freeze({ chord: "G Major", answer: "B" }),
    Object.freeze({ chord: "A Minor", answer: "C" }),
    Object.freeze({ chord: "B Minor", answer: "D" }),
  ]);

  function normalizeAnswer(value) {
    return String(value == null ? "" : value).trim().toUpperCase();
  }

  class ThirdsGame {
    constructor(options) {
      const settings = options || {};
      this.cards = settings.cards || THIRDS_CARDS;
      this.random = settings.random || Math.random;
      this.status = "ready";
      this.score = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.currentCard = null;
      this.previousChord = null;
    }

    chooseCard() {
      if (!this.cards.length) throw new Error("Thirds needs chord cards.");
      let choices = this.cards;
      if (choices.length > 1 && this.previousChord) {
        choices = choices.filter((card) => card.chord !== this.previousChord);
      }
      const position = Math.min(
        choices.length - 1,
        Math.max(0, Math.floor(this.random() * choices.length))
      );
      this.currentCard = choices[position];
      this.previousChord = this.currentCard.chord;
      return this.currentCard;
    }

    start() {
      this.status = "running";
      this.score = 0;
      this.remainingMs = GAME_SECONDS * 1000;
      this.currentCard = null;
      this.previousChord = null;
      this.chooseCard();
      return this.snapshot();
    }

    submit(value) {
      if (this.status !== "running" || !this.currentCard) {
        return { accepted: false, reason: "not-running" };
      }
      const answer = normalizeAnswer(value);
      if (!/^[A-G]$/.test(answer)) {
        return { accepted: false, reason: "invalid-answer" };
      }
      const answeredCard = this.currentCard;
      const correct = answer === answeredCard.answer;
      if (correct) this.score += 1;
      this.chooseCard();
      return {
        accepted: true,
        correct,
        answer,
        chord: answeredCard.chord,
        score: this.score,
        nextChord: this.currentCard.chord,
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
        currentCard: this.currentCard,
      };
    }
  }

  root.ThirdsGame = ThirdsGame;
  root.THIRDS_CARDS = THIRDS_CARDS;
  root.THIRDS_RULES = Object.freeze({ gameSeconds: GAME_SECONDS, normalizeAnswer });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { ThirdsGame, THIRDS_CARDS, THIRDS_RULES: root.THIRDS_RULES, normalizeAnswer };
  }

  if (typeof document === "undefined") return;
  const page = document.querySelector("[data-thirds-game]");
  if (!page) return;

  const game = new ThirdsGame();
  const scoreOutput = document.getElementById("thirds-score");
  const bestOutput = document.getElementById("thirds-best");
  const timeOutput = document.getElementById("thirds-time");
  const chordOutput = document.getElementById("thirds-chord");
  const form = document.getElementById("thirds-answer-form");
  const input = document.getElementById("thirds-answer");
  const submitButton = document.getElementById("thirds-submit");
  const startButton = document.getElementById("thirds-start");
  const message = document.getElementById("thirds-message");
  const leaderboard = page.querySelector("[data-arcade-leaderboard]");
  let timer = null;
  let lastTickAt = 0;
  let activePlayToken = null;
  let finishPromise = null;
  let starting = false;

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
    return fetch("/arcade/scores/thirds", {
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
    chordOutput.textContent = state.currentCard ? state.currentCard.chord.toUpperCase() : "READY";
    const running = state.status === "running";
    input.disabled = !running;
    submitButton.disabled = !running;
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
    root.WoodshedArcadeEconomy.startPlay("thirds").then(function (payload) {
      activePlayToken = payload.play_token;
      finishPromise = null;
      game.start();
      input.value = "";
      message.textContent = "Type the third.";
      lastTickAt = performance.now();
      window.clearInterval(timer);
      timer = window.setInterval(tick, 100);
      render();
      input.focus();
    }).catch(function (error) {
      message.textContent = error.message;
    }).finally(function () {
      starting = false;
      render();
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const result = game.submit(input.value);
    if (!result.accepted) {
      message.textContent = result.reason === "invalid-answer" ? "Type one note from A to G." : "Start a new game first.";
      input.focus();
      return;
    }
    message.textContent = result.correct ? "Correct!" : "Next chord.";
    input.value = "";
    render();
    input.focus();
  });
  startButton.addEventListener("click", startGame);

  root.WoodshedArcadeEconomy.loadStatus("thirds").catch(function (error) {
    message.textContent = error.message;
  });
  loadScores().catch(function () {});
  render();
}(typeof window !== "undefined" ? window : globalThis));
