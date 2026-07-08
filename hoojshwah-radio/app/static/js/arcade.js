const arcadeCard = document.querySelector("#khjw-arcade");
const arcadeStartButton = document.querySelector("#arcade-start");
const arcadeTapButton = document.querySelector("#arcade-tap");
const arcadeNeedle = document.querySelector("#arcade-needle");
const arcadeScore = document.querySelector("#arcade-score");
const arcadeTime = document.querySelector("#arcade-time");
const arcadeStatus = document.querySelector("#arcade-status");
const arcadeForm = document.querySelector("#arcade-form");
const arcadeInitials = document.querySelector("#arcade-initials");
const arcadeScores = document.querySelector("#arcade-scores");
const arcadeTimeSecretTrigger = document.querySelector("#arcade-time-secret-trigger");
const arcadeTimeSecretDialog = document.querySelector("#arcade-time-secret-dialog");
const arcadeTimeSecretForm = document.querySelector("#arcade-time-secret-form");
const arcadeTimeSecretPass = document.querySelector("#arcade-time-secret-pass");

let arcadeSeconds = 30;
const ARCADE_TICK_MS = 40;
const ARCADE_CENTER = 50;
const ARCADE_GOLD_ZONE = 8;
const ARCADE_MAX_SCORE = 50000;

let arcadeRunning = false;
let arcadeStartedAt = 0;
let arcadeTimer = null;
let arcadeCurrentScore = 0;
let arcadeNeedlePosition = 50;
let arcadeNeedleVelocity = 0.8;

function renderArcadeState() {
  if (arcadeNeedle) {
    arcadeNeedle.style.left = `${arcadeNeedlePosition}%`;
  }

  if (arcadeScore) {
    arcadeScore.textContent = arcadeCurrentScore;
  }

  if (arcadeTime) {
    const elapsed = Math.floor((Date.now() - arcadeStartedAt) / 1000);
    arcadeTime.textContent = Math.max(0, arcadeSeconds - elapsed);
  }
}

function stopArcade() {
  arcadeRunning = false;
  window.clearInterval(arcadeTimer);
  arcadeTimer = null;

  if (arcadeStartButton) {
    arcadeStartButton.disabled = false;
    arcadeStartButton.textContent = "Play Again";
  }

  if (arcadeTapButton) {
    arcadeTapButton.disabled = true;
  }

  if (arcadeForm) {
    arcadeForm.hidden = arcadeCurrentScore <= 0;
  }

  if (arcadeInitials && arcadeCurrentScore > 0) {
    arcadeInitials.value = "";
    arcadeInitials.focus();
  }

  if (arcadeStatus) {
    arcadeStatus.textContent = arcadeCurrentScore > 0
      ? `Final score: ${arcadeCurrentScore}. Add your initials.`
      : "No signal caught. Try again.";
  }
}

function tickArcade() {
  const elapsedMs = Date.now() - arcadeStartedAt;

  if (elapsedMs >= arcadeSeconds * 1000) {
    stopArcade();
    renderArcadeState();
    return;
  }

  arcadeNeedlePosition += arcadeNeedleVelocity;

  if (arcadeNeedlePosition >= 98 || arcadeNeedlePosition <= 2) {
    arcadeNeedleVelocity *= -1;
    arcadeNeedlePosition = Math.max(2, Math.min(98, arcadeNeedlePosition));
  }

  const secondsElapsed = elapsedMs / 1000;
  const speedBoost = 1 + secondsElapsed / 42;
  arcadeNeedleVelocity += arcadeNeedleVelocity > 0 ? 0.003 * speedBoost : -0.003 * speedBoost;

  renderArcadeState();
}

function startArcade() {
  if (!arcadeCard || arcadeRunning) {
    return;
  }

  arcadeRunning = true;
  arcadeStartedAt = Date.now();
  arcadeCurrentScore = 0;
  arcadeNeedlePosition = 50;
  arcadeNeedleVelocity = Math.random() > 0.5 ? 0.8 : -0.8;

  if (arcadeForm) {
    arcadeForm.hidden = true;
  }

  if (arcadeStartButton) {
    arcadeStartButton.disabled = true;
    arcadeStartButton.textContent = "Game Running";
  }

  if (arcadeTapButton) {
    arcadeTapButton.disabled = false;
    arcadeTapButton.focus();
  }

  if (arcadeStatus) {
    arcadeStatus.textContent = "Tap when the needle is in the gold zone.";
  }

  renderArcadeState();
  arcadeTimer = window.setInterval(tickArcade, ARCADE_TICK_MS);
}

function tuneArcadeSignal() {
  if (!arcadeRunning) {
    return;
  }

  const distance = Math.abs(arcadeNeedlePosition - ARCADE_CENTER);

  if (distance <= ARCADE_GOLD_ZONE) {
    const bonus = Math.round((ARCADE_GOLD_ZONE - distance) * 18);
    arcadeCurrentScore += 100 + bonus;

    if (arcadeStatus) {
      arcadeStatus.textContent = "Locked!";
    }
  } else if (distance <= ARCADE_GOLD_ZONE * 2) {
    arcadeCurrentScore += 25;

    if (arcadeStatus) {
      arcadeStatus.textContent = "Close. Static cleared a little.";
    }
  } else {
    arcadeCurrentScore = Math.max(0, arcadeCurrentScore - 20);

    if (arcadeStatus) {
      arcadeStatus.textContent = "Static.";
    }
  }

  arcadeCurrentScore = Math.min(arcadeCurrentScore, ARCADE_MAX_SCORE);
  renderArcadeState();
}

function cleanArcadeInitials(value) {
  return (value || "").toUpperCase().replace(/[^A-Z]/g, "").slice(0, 3);
}

async function loadArcadeScores() {
  if (!arcadeScores) {
    return;
  }

  try {
    const response = await fetch("/api/game-scores?game_name=signal", { cache: "no-store" });

    if (!response.ok) {
      throw new Error(`Score API returned ${response.status}`);
    }

    const data = await response.json();
    renderArcadeScores(data.scores || []);
  } catch (error) {
    console.error("Could not load arcade scores:", error);
    arcadeScores.innerHTML = "<li>Arcade board unavailable.</li>";
  }
}

function renderArcadeScores(scores) {
  if (!arcadeScores) {
    return;
  }

  arcadeScores.innerHTML = "";

  if (scores.length === 0) {
    const item = document.createElement("li");
    item.textContent = "No scores yet. Be the first three letters.";
    arcadeScores.appendChild(item);
    return;
  }

  scores.forEach((entry) => {
    const item = document.createElement("li");
    const row = document.createElement("span");
    const initials = document.createElement("span");
    const score = document.createElement("span");

    row.className = "score-row";
    initials.textContent = entry.initials;
    score.textContent = Number(entry.score || 0).toLocaleString();

    row.appendChild(initials);
    row.appendChild(score);
    item.appendChild(row);
    arcadeScores.appendChild(item);
  });
}

async function saveArcadeScore(initials) {
  const response = await fetch("/api/game-scores", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      game_name: "signal",
      initials,
      score: arcadeCurrentScore
    })
  });

  if (!response.ok) {
    throw new Error(`Score API returned ${response.status}`);
  }

  return response.json();
}

if (arcadeStartButton) {
  arcadeStartButton.addEventListener("click", startArcade);
}

if (arcadeTapButton) {
  arcadeTapButton.addEventListener("click", tuneArcadeSignal);
}

if (arcadeInitials) {
  arcadeInitials.addEventListener("input", () => {
    arcadeInitials.value = cleanArcadeInitials(arcadeInitials.value);
  });
}

if (arcadeForm && arcadeInitials) {
  arcadeForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const initials = cleanArcadeInitials(arcadeInitials.value);

    if (initials.length !== 3) {
      if (arcadeStatus) {
        arcadeStatus.textContent = "Use exactly three letters.";
      }
      return;
    }

    try {
      const result = await saveArcadeScore(initials);
      renderArcadeScores(result.scores || []);
      arcadeForm.hidden = true;

      if (arcadeStatus) {
        arcadeStatus.textContent = `${initials} is on the board.`;
      }
    } catch (error) {
      console.error("Could not save arcade score:", error);

      if (arcadeStatus) {
        arcadeStatus.textContent = "Could not save score. Try again.";
      }
    }
  });
}

loadArcadeScores();


function unlockArcadeTimeBoost() {
  arcadeSeconds = 35;

  if (arcadeTime) {
    arcadeTime.textContent = arcadeSeconds;
  }

  if (arcadeStatus) {
    arcadeStatus.textContent = "Secret tuner boost unlocked: 35 seconds.";
  }
}

if (arcadeTimeSecretTrigger && arcadeTimeSecretDialog && arcadeTimeSecretPass) {
  arcadeTimeSecretTrigger.addEventListener("click", () => {
    arcadeTimeSecretDialog.hidden = !arcadeTimeSecretDialog.hidden;

    if (!arcadeTimeSecretDialog.hidden) {
      arcadeTimeSecretPass.focus();
    }
  });
}

if (arcadeTimeSecretForm && arcadeTimeSecretPass) {
  arcadeTimeSecretForm.addEventListener("submit", (event) => {
    event.preventDefault();

    if (arcadeTimeSecretPass.value.trim().toLowerCase() === "god_mode") {
      unlockArcadeTimeBoost();
      arcadeTimeSecretPass.value = "";
      arcadeTimeSecretDialog.hidden = true;
    } else {
      arcadeTimeSecretPass.value = "";
    }
  });
}

window.addEventListener("khjw:arcade-boost", unlockArcadeTimeBoost);
