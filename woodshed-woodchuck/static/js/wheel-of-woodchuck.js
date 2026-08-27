(function (root) {
  "use strict";

  const RUN_DURATION_MS = 45000;
  const SOLVE_BONUS = 1000;
  const MAX_MISSES = 3;
  const NUMERIC_WHEEL_VALUES = Object.freeze([1000, 50, 350, 500, 100, 200]);
  const WHEEL_SEGMENTS = Object.freeze(["MISS", 1000, 50, 350, 500, 100, "3X", 200]);
  const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

  function normalizeAnswer(value) {
    return String(value == null ? "" : value).trim().toUpperCase();
  }

  function countOccurrences(answer, letter) {
    return Array.from(answer).filter(function (character) {
      return character === letter;
    }).length;
  }

  class WheelOfWoodchuckGame {
    constructor(options) {
      const settings = options || {};
      this.terms = settings.terms || [];
      this.random = settings.random || Math.random;
      this.resetRun();
    }

    resetRun() {
      this.status = "ready";
      this.remainingMs = RUN_DURATION_MS;
      this.score = 0;
      this.currentTerm = null;
      this.previousAnswer = null;
      this.guessedLetters = new Set();
      this.canGuessLetter = false;
      this.spinPending = false;
      this.letterValue = 0;
      this.spinResult = null;
      this.misses = 0;
      this.puzzleState = "idle";
    }

    start() {
      this.resetRun();
      this.status = "running";
      this.loadNextTerm();
      return this.snapshot();
    }

    choose(items) {
      if (!items.length) return null;
      const index = Math.min(items.length - 1, Math.floor(this.random() * items.length));
      return items[index];
    }

    loadNextTerm() {
      if (this.status !== "running" || this.remainingMs <= 0) return null;
      const available = this.terms.filter(function (term) {
        return normalizeAnswer(term.answer) !== this.previousAnswer;
      }, this);
      const pool = available.length ? available : this.terms;
      const selected = this.choose(pool);
      if (!selected) throw new Error("Wheel of Woodchuck needs at least one music term.");
      this.currentTerm = selected;
      this.previousAnswer = normalizeAnswer(selected.answer);
      this.guessedLetters = new Set();
      this.canGuessLetter = false;
      this.spinPending = false;
      this.letterValue = 0;
      this.spinResult = null;
      this.misses = 0;
      this.puzzleState = "active";
      return selected;
    }

    startSpin() {
      if (
        this.status !== "running" || this.puzzleState !== "active" ||
        this.spinPending || this.canGuessLetter
      ) return null;
      const segment = this.choose(WHEEL_SEGMENTS);
      const numericResult = segment === "3X" ? this.choose(NUMERIC_WHEEL_VALUES) :
        (typeof segment === "number" ? segment : null);
      const value = segment === "3X" ? numericResult * 3 : (numericResult || 0);
      this.spinPending = true;
      this.spinResult = { segment: segment, numericResult: numericResult, letterValue: value };
      return this.spinResult;
    }

    completeSpin() {
      if (!this.spinPending || this.status !== "running" || this.puzzleState !== "active") {
        return null;
      }
      this.spinPending = false;
      if (this.spinResult.segment === "MISS") {
        const miss = this.recordMiss();
        Object.assign(this.spinResult, miss);
        this.canGuessLetter = false;
        this.letterValue = 0;
        return this.spinResult;
      }
      this.canGuessLetter = true;
      this.letterValue = this.spinResult.letterValue;
      return this.spinResult;
    }

    recordMiss() {
      if (this.status !== "running" || this.puzzleState !== "active") {
        return { accepted: false, reason: "unavailable" };
      }
      this.misses = Math.min(MAX_MISSES, this.misses + 1);
      const exhausted = this.misses >= MAX_MISSES;
      if (exhausted) {
        this.puzzleState = "failed";
        this.canGuessLetter = false;
        this.spinPending = false;
      }
      return { accepted: true, misses: this.misses, exhausted: exhausted };
    }

    maskedAnswer() {
      if (!this.currentTerm) return "";
      const answer = normalizeAnswer(this.currentTerm.answer);
      if (this.puzzleState !== "active") return answer.split("").join(" ");
      return Array.from(answer).map(function (letter) {
        return this.guessedLetters.has(letter) ? letter : "_";
      }, this).join(" ");
    }

    allLettersRevealed() {
      if (!this.currentTerm) return false;
      return Array.from(new Set(normalizeAnswer(this.currentTerm.answer))).every(function (letter) {
        return this.guessedLetters.has(letter);
      }, this);
    }

    solveCurrentTerm() {
      if (this.puzzleState !== "active") return false;
      this.score += SOLVE_BONUS;
      this.puzzleState = "solved";
      this.canGuessLetter = false;
      this.spinPending = false;
      return true;
    }

    guessLetter(value) {
      const letter = normalizeAnswer(value);
      if (!/^[A-Z]$/.test(letter) || this.status !== "running" || this.puzzleState !== "active") {
        return { accepted: false, reason: "unavailable" };
      }
      if (this.guessedLetters.has(letter)) {
        return { accepted: false, reason: "already-guessed" };
      }
      if (!this.canGuessLetter || this.spinPending) {
        return { accepted: false, reason: "spin-required" };
      }

      this.guessedLetters.add(letter);
      this.canGuessLetter = false;
      const occurrences = countOccurrences(normalizeAnswer(this.currentTerm.answer), letter);
      if (!occurrences) {
        const miss = this.recordMiss();
        return Object.assign({ accepted: true, correct: false }, miss);
      }

      const gained = this.letterValue * occurrences;
      this.score += gained;
      const solved = this.allLettersRevealed();
      if (solved) this.solveCurrentTerm();
      return { accepted: true, correct: true, occurrences: occurrences, gained: gained, solved: solved };
    }

    spell(value) {
      if (this.status !== "running" || this.puzzleState !== "active") {
        return { accepted: false, reason: "unavailable" };
      }
      const correct = normalizeAnswer(value) === normalizeAnswer(this.currentTerm.answer);
      if (correct) {
        this.solveCurrentTerm();
        return { accepted: true, correct: true, solved: true };
      }
      const miss = this.recordMiss();
      return {
        accepted: true,
        correct: false,
        misses: miss.misses,
        exhausted: miss.exhausted,
      };
    }

    elapse(milliseconds) {
      if (this.status !== "running") return this.remainingMs;
      const elapsed = Number.isFinite(milliseconds) ? Math.max(0, milliseconds) : 0;
      this.remainingMs = Math.max(0, this.remainingMs - elapsed);
      if (this.remainingMs === 0) this.finish();
      return this.remainingMs;
    }

    finish() {
      if (this.status === "ended") return false;
      this.status = "ended";
      this.remainingMs = 0;
      this.canGuessLetter = false;
      this.spinPending = false;
      return true;
    }

    snapshot() {
      return {
        status: this.status,
        remainingMs: this.remainingMs,
        score: this.score,
        currentTerm: this.currentTerm,
        guessedLetters: Array.from(this.guessedLetters),
        canGuessLetter: this.canGuessLetter,
        letterValue: this.letterValue,
        misses: this.misses,
        puzzleState: this.puzzleState,
        maskedAnswer: this.maskedAnswer(),
      };
    }
  }

  root.WheelOfWoodchuckGame = WheelOfWoodchuckGame;
  root.WHEEL_OF_WOODCHUCK_RULES = Object.freeze({
    runDurationMs: RUN_DURATION_MS,
    solveBonus: SOLVE_BONUS,
    maxMisses: MAX_MISSES,
    numericWheelValues: NUMERIC_WHEEL_VALUES,
    wheelSegments: WHEEL_SEGMENTS,
    normalizeAnswer: normalizeAnswer,
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      WheelOfWoodchuckGame: WheelOfWoodchuckGame,
      WHEEL_OF_WOODCHUCK_RULES: root.WHEEL_OF_WOODCHUCK_RULES,
    };
  }

  if (typeof document === "undefined") return;
  const page = document.querySelector("[data-wheel-of-woodchuck]");
  if (!page) return;

  const activeArea = document.getElementById("wheel-active-area");
  const terms = root.WHEEL_OF_WOODCHUCK_TERMS || [];
  const game = new WheelOfWoodchuckGame({ terms: terms });
  const scoreOutput = document.getElementById("wheel-score");
  const bestOutput = document.getElementById("wheel-best");
  const timeOutput = document.getElementById("wheel-time");
  const missesOutput = document.getElementById("wheel-misses");
  const clue = document.getElementById("wheel-clue");
  const answer = document.getElementById("wheel-answer");
  const spinButton = document.getElementById("wheel-spin");
  const wheel = document.getElementById("wheel-spinner");
  const wheelResult = document.getElementById("wheel-spin-result");
  const letterGrid = document.getElementById("wheel-letters");
  const spellButton = document.getElementById("wheel-spell-open");
  const spellForm = document.getElementById("wheel-spell-form");
  const spellInput = document.getElementById("wheel-spell-input");
  const message = document.getElementById("wheel-message");
  const startButton = document.getElementById("wheel-start");
  const leaderboard = page.querySelector('[data-arcade-leaderboard="wheel-of-woodchuck"]');
  let timerId = null;
  let lastTickAt = 0;
  let puzzleTimerId = null;
  let spinTimerId = null;
  let wheelTurns = 0;
  let finishPromise = null;
  let activePlayToken = null;
  let starting = false;

  function setWheelRotation(turns) {
    wheel.style.setProperty("--wheel-content-counter-rotation", `${-turns}turn`);
    wheel.style.transform = `rotate(${turns}turn)`;
  }

  function setMessage(value) { message.textContent = value; }

  function renderLeaderboard(payload) {
    if (bestOutput) bestOutput.textContent = String(payload.best_score || 0);
    if (!leaderboard) return;
    leaderboard.innerHTML = "";
    const rows = Array.isArray(payload.leaderboard) ? payload.leaderboard : [];
    if (!rows.length) {
      const empty = document.createElement("li");
      empty.textContent = "No scores yet.";
      leaderboard.appendChild(empty);
      return;
    }
    rows.forEach(function (row) {
      const item = document.createElement("li");
      const name = `${row.display_name}${row.is_current_user ? " (You)" : ""}`;
      item.value = Number(row.rank);
      item.textContent = `${name} — ${row.score}`;
      if (row.is_current_user) item.classList.add("is-current-user");
      leaderboard.appendChild(item);
    });
  }

  async function loadScores() {
    try {
      const response = await fetch("/arcade/scores/wheel-of-woodchuck", { credentials: "same-origin" });
      if (response.ok) renderLeaderboard(await response.json());
    } catch (_error) {
      // The game remains playable if standings are temporarily unavailable.
    }
  }

  async function submitFinalScoreOnce() {
    if (finishPromise) return finishPromise;
    finishPromise = root.WoodshedArcadeEconomy.completePlay(
      activePlayToken, Math.max(0, Math.round(game.score))
    ).then(function (payload) {
      renderLeaderboard(payload);
      return payload;
    }).catch(function () {
      setMessage("That score could not be saved.");
      return null;
    });
    return finishPromise;
  }

  function render() {
    const state = game.snapshot();
    scoreOutput.textContent = String(state.score);
    timeOutput.textContent = String(Math.ceil(state.remainingMs / 1000));
    missesOutput.textContent = `${state.misses}/${MAX_MISSES}`;
    clue.textContent = state.currentTerm ? state.currentTerm.definition : "Press New Game to begin.";
    answer.textContent = state.maskedAnswer;
    const active = state.status === "running" && state.puzzleState === "active";
    spinButton.disabled = !active || state.spinPending || state.canGuessLetter;
    spellButton.disabled = !active;
    letterGrid.querySelectorAll("button").forEach(function (button) {
      const used = state.guessedLetters.includes(button.dataset.wheelLetter);
      button.disabled = !active || !state.canGuessLetter || used;
      button.classList.toggle("is-used", used);
    });
    startButton.textContent = state.status === "running" ? "Game Running" : "New Game";
    startButton.disabled = state.status === "running";
  }

  function finishRun() {
    if (timerId !== null) window.clearInterval(timerId);
    timerId = null;
    if (spinTimerId !== null) window.clearTimeout(spinTimerId);
    spinTimerId = null;
    if (puzzleTimerId !== null) window.clearTimeout(puzzleTimerId);
    puzzleTimerId = null;
    game.finish();
    spellForm.hidden = true;
    setMessage(`Time! Final score: ${game.score}`);
    render();
    submitFinalScoreOnce();
  }

  function tick() {
    const now = performance.now();
    game.elapse(now - lastTickAt);
    lastTickAt = now;
    render();
    if (game.status === "ended") finishRun();
  }

  function advanceAfterPuzzle(text, celebrate) {
    setMessage(text);
    spellForm.hidden = true;
    render();
    if (celebrate && root.WoodshedAudio) root.WoodshedAudio.play("arcadeCheer");
    puzzleTimerId = window.setTimeout(function () {
      puzzleTimerId = null;
      if (game.status !== "running") return;
      game.loadNextTerm();
      wheelResult.textContent = "Spin for a letter value";
      setMessage("New music term!");
      render();
    }, 700);
  }

  function handleLetter(letter) {
    const result = game.guessLetter(letter);
    if (!result.accepted) return;
    if (!result.correct) {
      if (result.exhausted) {
        advanceAfterPuzzle(`The term was ${normalizeAnswer(game.currentTerm.answer)}.`, false);
      } else {
        setMessage(`Not in this term. Miss ${result.misses}/${MAX_MISSES}.`);
      }
    } else if (result.solved) {
      advanceAfterPuzzle(`${normalizeAnswer(game.currentTerm.answer)} — ${game.currentTerm.definition}`, true);
    } else {
      setMessage(`${result.occurrences} ${letter}${result.occurrences === 1 ? "" : "s"}: +${result.gained}`);
    }
    render();
  }

  ALPHABET.split("").forEach(function (letter) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.wheelLetter = letter;
    button.textContent = letter;
    button.setAttribute("aria-label", `Guess ${letter}`);
    button.addEventListener("click", function () { handleLetter(letter); });
    letterGrid.appendChild(button);
  });

  spinButton.addEventListener("click", function () {
    const result = game.startSpin();
    if (!result) return;
    wheelTurns += 4 + WHEEL_SEGMENTS.indexOf(result.segment) / WHEEL_SEGMENTS.length;
    setWheelRotation(wheelTurns);
    wheelResult.textContent = result.segment === "3X"
      ? "3X! One more spin…"
      : "Spinning…";
    render();

    function finishSpin() {
      spinTimerId = null;
      const completed = game.completeSpin();
      if (completed.segment === "MISS") {
        wheelResult.textContent = "MISS";
        if (completed.exhausted) {
          advanceAfterPuzzle(`The term was ${normalizeAnswer(game.currentTerm.answer)}.`, false);
        } else {
          setMessage(`Miss ${completed.misses}/${MAX_MISSES}. Spin again.`);
          render();
        }
        return;
      }
      wheelResult.textContent = result.segment === "3X"
        ? `3X × ${result.numericResult} = ${result.letterValue} per letter`
        : `${result.letterValue} per letter`;
      setMessage("Choose one letter.");
      render();
    }

    spinTimerId = window.setTimeout(function () {
      if (result.segment !== "3X") {
        finishSpin();
        return;
      }
      wheelTurns += 3 + WHEEL_SEGMENTS.indexOf(result.numericResult) /
        WHEEL_SEGMENTS.length;
      setWheelRotation(wheelTurns);
      wheelResult.textContent = "3X bonus: spinning for the numeric value…";
      spinTimerId = window.setTimeout(finishSpin, 720);
    }, 720);
  });

  spellButton.addEventListener("click", function () {
    spellForm.hidden = false;
    spellInput.value = "";
    spellInput.focus();
  });

  spellForm.addEventListener("submit", function (event) {
    event.preventDefault();
    const result = game.spell(spellInput.value);
    if (!result.accepted) return;
    spellInput.value = "";
    if (result.correct) {
      advanceAfterPuzzle(`${normalizeAnswer(game.currentTerm.answer)} — ${game.currentTerm.definition}`, true);
    } else if (result.exhausted) {
      advanceAfterPuzzle(`The term was ${normalizeAnswer(game.currentTerm.answer)}.`, false);
    } else {
      setMessage(`Try again. Miss ${result.misses}/${MAX_MISSES}.`);
      render();
    }
  });

  startButton.addEventListener("click", async function () {
    if (game.status === "running" || starting) return;
    starting = true;
    startButton.disabled = true;
    setMessage("Starting…");
    try {
      const play = await root.WoodshedArcadeEconomy.startPlay("wheel-of-woodchuck");
      activePlayToken = play.play_token;
    } catch (error) {
      setMessage(error.message || "That game could not start.");
      startButton.disabled = false;
      starting = false;
      return;
    }
    starting = false;
    if (root.WoodshedAudio) root.WoodshedAudio.unlock();
    if (timerId !== null) window.clearInterval(timerId);
    if (puzzleTimerId !== null) window.clearTimeout(puzzleTimerId);
    if (spinTimerId !== null) window.clearTimeout(spinTimerId);
    finishPromise = null;
    spellForm.hidden = true;
    wheelResult.textContent = "Spin for a letter value";
    game.start();
    lastTickAt = performance.now();
    timerId = window.setInterval(tick, 100);
    setMessage("Spin, then choose one letter — or spell it now.");
    render();
    activeArea?.scrollIntoView({ block: "nearest" });
  });

  document.addEventListener("keydown", function (event) {
    if (event.target && event.target.closest("input")) return;
    if (/^[a-z]$/i.test(event.key)) handleLetter(event.key);
    if (event.code === "Space" && !spinButton.disabled) {
      event.preventDefault();
      spinButton.click();
    }
  });

  render();
  root.WoodshedArcadeEconomy.loadStatus("wheel-of-woodchuck").catch(function () {});
  loadScores();
}(typeof globalThis !== "undefined" ? globalThis : this));
