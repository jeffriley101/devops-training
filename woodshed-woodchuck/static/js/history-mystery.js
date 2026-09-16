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

  const game = new HistoryMysteryGame([]);
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
  let playAccount = null;
  let pendingAnswer = null;

  function sameAccount(account) {
    return !root.WWState || !!root.WWState.stateForResponse(account);
  }

  function applySnapshot(snapshot) {
    game.questionIndex = snapshot.question_index;
    game.questions[snapshot.question_index] = snapshot.question;
    game.score = snapshot.score;
    game.status = snapshot.finished ? "ended" : "running";
    game.awaitingAdvance = false;
  }

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
    const requestAccount = root.WWState?.accountRequest();
    return fetch(`/arcade/scores/${GAME_KEY}`, {
      credentials: "same-origin",
      cache: "no-store",
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.detail || "Score is unavailable.");
        if (!sameAccount(requestAccount)) return payload;
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
        if (!sameAccount(playAccount)) return payload;
        bestOutput.textContent = String(payload.best_score || 0);
        return payload;
      })
      .catch(function (error) {
        if (!sameAccount(playAccount)) throw error;
        message.textContent = `${message.textContent} · ${error.message}`;
        throw error;
      });
    return finishPromise;
  }

  async function answerQuestion(choice) {
    if (game.status !== "running" || game.awaitingAdvance || !sameAccount(playAccount)) return;
    // A lost response retries the same committed choice, never a second guess.
    if (!pendingAnswer) pendingAnswer = { index: game.questionIndex, choice };
    game.awaitingAdvance = true;
    message.textContent = "Checking your answer…";
    render();
    try {
      const payload = await root.WoodshedArcadeEconomy.answerHistory(
        activePlayToken, pendingAnswer.index, pendingAnswer.choice
      );
      if (!sameAccount(playAccount)) return;
      pendingAnswer = null;
      const result = payload.answer_result;
      game.score = payload.history.score;
      message.textContent = result.correct
        ? `Correct! ${result.fact}`
        : `The answer is ${result.answer}. ${result.fact}`;
      render();
      root.clearTimeout(feedbackTimer);
      feedbackTimer = root.setTimeout(function () {
        if (!sameAccount(playAccount)) return;
        applySnapshot(payload.history);
        if (payload.history.finished) finishGame().catch(function () {});
        else message.textContent = "Choose one answer.";
        render();
        const firstAnswer = answers.querySelector("button");
        if (firstAnswer) firstAnswer.focus();
      }, 700);
    } catch (error) {
      if (!sameAccount(playAccount)) return;
      game.awaitingAdvance = false;
      if (error.message.includes("daily quiz has expired")) {
        game.status = "ended";
        dailyPlayAvailable = false;
        message.textContent = "That daily quiz has expired. Reload this page to start today's quiz.";
      } else {
        message.textContent = `${error.message} Tap an answer to retry saving your original choice.`;
      }
      render();
    }
  }

  function startGame() {
    if (starting || !dailyPlayAvailable || game.status === "running") return;
    starting = true;
    message.textContent = "Starting today's quiz…";
    render();
    const requestAccount = root.WWState?.accountRequest();
    root.WoodshedArcadeEconomy.startPlay(GAME_KEY).then(function (payload) {
      if (!sameAccount(requestAccount)) return;
      playAccount = requestAccount;
      activePlayToken = payload.play_token;
      finishPromise = null;
      pendingAnswer = null;
      dailyPlayAvailable = false;
      game.submitted = false;
      applySnapshot(payload.history);
      message.textContent = "Choose one answer.";
      render();
      const firstAnswer = answers.querySelector("button");
      if (firstAnswer) firstAnswer.focus();
    }).catch(function (error) {
      if (!sameAccount(requestAccount)) return;
      message.textContent = error.message;
      if (error.message.includes("once each Central day")) dailyPlayAvailable = false;
    }).finally(function () {
      if (!sameAccount(requestAccount)) return;
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

  const statusAccount = root.WWState?.accountRequest();
  root.WoodshedArcadeEconomy.loadStatus(GAME_KEY).then(function (payload) {
    if (!sameAccount(statusAccount)) return;
    dailyPlayAvailable = payload.daily_play_available !== false || payload.daily_play_resumable === true;
    if (payload.daily_play_resumable) message.textContent = "Resume today's quiz with New Game. No extra dandelion.";
    if (!dailyPlayAvailable) {
      message.textContent = "Today's quiz is complete. Come back after Central midnight.";
    }
    render();
  }).catch(function (error) {
    if (!sameAccount(statusAccount)) return;
    message.textContent = error.message;
  });
  loadScores().catch(function () {});
  render();
}(typeof window !== "undefined" ? window : globalThis));
