(function (root) {
  "use strict";

  const GAME_KEY = "history-mystery";
  const QUESTION_COUNT = 5;
  const CATEGORY_ORDER = Object.freeze([
    "WHO AM I?",
    "WHO CHANGED ME?",
    "BIG YEAR",
    "HISTORY MYSTERY",
    "FAMOUS FACE",
  ]);

  class HistoryMysteryGame {
    constructor(questions) {
      this.questions = Array.isArray(questions) ? questions : [];
      this.status = "ready";
      this.questionIndex = 0;
      this.score = 0;
      this.awaitingAdvance = false;
      this.submitted = false;
    }

    start() {
      if (this.questions.length !== QUESTION_COUNT) {
        throw new Error("History Mystery needs exactly five questions.");
      }
      this.status = "running";
      this.questionIndex = 0;
      this.score = 0;
      this.awaitingAdvance = false;
      this.submitted = false;
      return this.snapshot();
    }

    answer(choice) {
      if (this.status !== "running" || this.awaitingAdvance) {
        return { accepted: false, reason: "not-ready" };
      }
      const question = this.currentQuestion;
      const correct = String(choice) === String(question.answer);
      if (correct) this.score += 1;
      this.awaitingAdvance = true;
      const finished = this.questionIndex === QUESTION_COUNT - 1;
      if (finished) this.status = "ended";
      return {
        accepted: true,
        correct,
        answer: question.answer,
        fact: question.fact,
        finished,
        score: this.score,
      };
    }

    advance() {
      if (!this.awaitingAdvance || this.status !== "running") return false;
      this.questionIndex += 1;
      this.awaitingAdvance = false;
      return true;
    }

    markSubmitted() {
      if (this.submitted) return false;
      this.submitted = true;
      return true;
    }

    get currentQuestion() {
      return this.questions[this.questionIndex] || null;
    }

    snapshot() {
      return {
        status: this.status,
        questionIndex: this.questionIndex,
        questionNumber: this.status === "ready" ? 0 : this.questionIndex + 1,
        score: this.score,
        awaitingAdvance: this.awaitingAdvance,
        currentQuestion: this.currentQuestion,
        submitted: this.submitted,
      };
    }
  }

  root.HistoryMysteryGame = HistoryMysteryGame;
  root.HISTORY_MYSTERY_RULES = Object.freeze({
    gameKey: GAME_KEY,
    questionCount: QUESTION_COUNT,
    categoryOrder: CATEGORY_ORDER,
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      HistoryMysteryGame,
      HISTORY_MYSTERY_RULES: root.HISTORY_MYSTERY_RULES,
    };
  }

  if (typeof document === "undefined") return;
  const page = document.querySelector("[data-history-mystery-game]");
  if (!page) return;

  const questionData = document.getElementById("history-mystery-question-data");
  let questions = [];
  try { questions = JSON.parse(questionData.textContent || "[]"); } catch (_error) { questions = []; }
  const game = new HistoryMysteryGame(questions);
  const scoreOutput = document.getElementById("history-mystery-score");
  const bestOutput = document.getElementById("history-mystery-best");
  const progressOutput = document.getElementById("history-mystery-progress");
  const categoryOutput = document.getElementById("history-mystery-category");
  const promptOutput = document.getElementById("history-mystery-prompt");
  const answers = document.getElementById("history-mystery-answers");
  const startButton = document.getElementById("history-mystery-start");
  const message = document.getElementById("history-mystery-message");
  let activePlayToken = null;
  let finishPromise = null;
  let starting = false;
  let dailyPlayAvailable = true;
  let feedbackTimer = null;

  function render() {
    const state = game.snapshot();
    scoreOutput.textContent = String(state.score);
    progressOutput.textContent = String(Math.min(QUESTION_COUNT, state.questionNumber));
    answers.replaceChildren();
    if (state.status !== "ready" && state.currentQuestion) {
      categoryOutput.textContent = state.currentQuestion.category;
      promptOutput.textContent = state.currentQuestion.prompt;
      state.currentQuestion.choices.forEach(function (choice) {
        const button = document.createElement("button");
        button.className = "btn btn-secondary";
        button.type = "button";
        button.dataset.historyMysteryAnswer = choice;
        button.textContent = choice;
        button.disabled = state.status !== "running" || state.awaitingAdvance;
        answers.appendChild(button);
      });
    } else {
      categoryOutput.textContent = "Daily Quiz";
      promptOutput.textContent = "Five questions. One try today.";
    }
    startButton.disabled = starting || !dailyPlayAvailable || state.status === "running";
    if (state.status === "running") startButton.textContent = "Quiz Running";
    else if (!dailyPlayAvailable) startButton.textContent = "Played Today";
    else startButton.textContent = "New Game";
  }

  function loadScores() {
    return fetch(`/arcade/scores/${GAME_KEY}`, {
      credentials: "same-origin",
      cache: "no-store",
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.detail || "Score is unavailable.");
        bestOutput.textContent = String(payload.best_score || 0);
        return payload;
      });
    });
  }

  function finishGame() {
    if (finishPromise) return finishPromise;
    if (!game.markSubmitted()) return Promise.resolve(null);
    const token = activePlayToken;
    activePlayToken = null;
    dailyPlayAvailable = false;
    render();
    message.textContent = `Final score: ${game.score} / 5`;
    finishPromise = root.WoodshedArcadeEconomy.completePlay(token, game.score)
      .then(function (payload) {
        bestOutput.textContent = String(payload.best_score || 0);
        return payload;
      })
      .catch(function (error) {
        message.textContent = `${message.textContent} · ${error.message}`;
        throw error;
      });
    return finishPromise;
  }

  function answerQuestion(choice) {
    const result = game.answer(choice);
    if (!result.accepted) return;
    message.textContent = result.correct
      ? `Correct! ${result.fact}`
      : `The answer is ${result.answer}. ${result.fact}`;
    render();
    root.clearTimeout(feedbackTimer);
    feedbackTimer = root.setTimeout(function () {
      if (result.finished) {
        finishGame().catch(function () {});
      } else {
        game.advance();
        message.textContent = "Choose one answer.";
        render();
        const firstAnswer = answers.querySelector("button");
        if (firstAnswer) firstAnswer.focus();
      }
    }, 700);
  }

  function startGame() {
    if (starting || !dailyPlayAvailable || game.status === "running") return;
    starting = true;
    message.textContent = "Starting today's quiz…";
    render();
    root.WoodshedArcadeEconomy.startPlay(GAME_KEY).then(function (payload) {
      activePlayToken = payload.play_token;
      finishPromise = null;
      dailyPlayAvailable = false;
      game.start();
      message.textContent = "Choose one answer.";
      render();
      const firstAnswer = answers.querySelector("button");
      if (firstAnswer) firstAnswer.focus();
    }).catch(function (error) {
      message.textContent = error.message;
      if (error.message.includes("once each Central day")) dailyPlayAvailable = false;
    }).finally(function () {
      starting = false;
      render();
    });
  }

  answers.addEventListener("click", function (event) {
    const button = event.target.closest("[data-history-mystery-answer]");
    if (!button || button.disabled) return;
    answerQuestion(button.dataset.historyMysteryAnswer);
  });
  startButton.addEventListener("click", startGame);

  root.WoodshedArcadeEconomy.loadStatus(GAME_KEY).then(function (payload) {
    dailyPlayAvailable = payload.daily_play_available !== false;
    if (!dailyPlayAvailable) {
      message.textContent = "Today's quiz is complete. Come back after Central midnight.";
    }
    render();
  }).catch(function (error) {
    message.textContent = error.message;
  });
  loadScores().catch(function () {});
  render();
}(typeof window !== "undefined" ? window : globalThis));
