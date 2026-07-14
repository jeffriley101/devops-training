(function () {
  const stateApi = window.WWState;
  if (!stateApi) return;

  function parseJsonFromId(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;

    try {
      return JSON.parse(el.textContent);
    } catch (_e) {
      return fallback;
    }
  }

  const questPool = parseJsonFromId("quest-pool-data", {});
  const saxVikingMessages = parseJsonFromId("sax-viking-messages-data", {
    reward: ["Great work today!"],
    supportive: ["Keep going — you can do this."],
    already_done: ["You already completed today's quest."],
  });

  function hasProfile(s) {
    return Boolean(s.profile.instrument && s.profile.level && s.profile.goal);
  }

  function getDayIndex(date = new Date()) {
    const start = new Date(date.getFullYear(), 0, 0);
    return Math.floor((date - start) / 86400000);
  }

  function selectQuestForToday(instrument, dateKey) {
    const quests = questPool[instrument] || [];

    if (!quests.length) {
      return {
        id: "fallback-quest",
        text: "Practice one scale slowly with good tone.",
        target_minutes: 15,
        reward_credits: 20,
      };
    }

    const idx = getDayIndex(new Date(`${dateKey}T00:00:00`)) % quests.length;
    return quests[idx];
  }

  function pickMessage(type, dateKey) {
    const list = saxVikingMessages[type] || [];
    if (!list.length) return "";

    const idx = getDayIndex(new Date(`${dateKey}T00:00:00`)) % list.length;
    return list[idx];
  }

  function ensureTodayQuest(state) {
    const today = stateApi.localDateKey();

    if (!hasProfile(state)) return state;
    if (state.daily && state.daily.dateKey === today && state.daily.questText) return state;

    const quest = selectQuestForToday(state.profile.instrument, today);

    state.daily = {
      dateKey: today,
      questId: quest.id,
      questText: quest.text,
      targetMinutes: quest.target_minutes,
      rewardCredits: quest.reward_credits,
      loggedMinutes: 0,
      completed: false,
      completedAt: null,
      encouragement: "",
    };

    state.quest = {
      dateKey: today,
      text: quest.text,
      targetMinutes: quest.target_minutes,
      completed: false,
      rewardCredits: quest.reward_credits,
    };

    return state;
  }

  function routeGuard(state) {
    const path = window.location.pathname;

    if (["/home", "/p-book", "/quest", "/store"].includes(path) && !hasProfile(state)) {
      window.location.replace("/setup");
      return false;
    }

    const continueLink = document.querySelector("[data-requires-profile='true']");
    if (continueLink && !hasProfile(state)) {
      continueLink.setAttribute("aria-disabled", "true");
      continueLink.classList.add("disabled");
      continueLink.addEventListener("click", function (event) {
        event.preventDefault();
        window.location.assign("/setup");
      });
    }

    return true;
  }

  function wireSetupForm(state) {
    const form = document.getElementById("setup-form");
    if (!form) return;

    const errorEl = document.getElementById("setup-error");
    const woodchuckNameEl = document.getElementById("woodchuck-name");
    const instrumentEl = document.getElementById("instrument");
    const levelEl = document.getElementById("level");
    const goalEl = document.getElementById("goal");

    if (state.profile.woodchuckName) woodchuckNameEl.value = state.profile.woodchuckName;
    if (state.profile.instrument) instrumentEl.value = state.profile.instrument;
    if (state.profile.level) levelEl.value = state.profile.level;
    if (state.profile.goal) goalEl.value = state.profile.goal;

    form.addEventListener("submit", function (event) {
      const woodchuckName = woodchuckNameEl.value.trim();
      const instrument = instrumentEl.value.trim();
      const level = levelEl.value.trim();
      const goal = goalEl.value.trim();

      if (!instrument || !level || !goal) {
        event.preventDefault();
        errorEl.textContent = "Please choose an instrument, level, and goal.";
        return;
      }

      const next = stateApi.getState();
      next.profile.woodchuckName = woodchuckName;
      next.profile.instrument = instrument;
      next.profile.level = level;
      next.profile.goal = goal;
      next.profile.createdAt = next.profile.createdAt || new Date().toISOString();

      ensureTodayQuest(next);
      stateApi.saveState(next);
    });
  }

  function hydrateHome(state) {
    const creditsEl = document.getElementById("credits-value");
    const streakEl = document.getElementById("streak-value");
    const woodchuckNameEl = document.getElementById("woodchuck-name-value");
    const instrumentObjectEl = document.getElementById("instrument-object");
    const levelEl = document.getElementById("level-value");
    const campLevelEl = document.getElementById("camp-level-value");
    const totalPChartsEl = document.getElementById("total-p-charts-value");
    const dandelionObjectEl = document.getElementById("dandelion-object");

    const practiceLog = Array.isArray(state.practiceLog)
      ? state.practiceLog
      : [];

    const totalPCharts = practiceLog.filter(
      (entry) => entry && entry.source === "p-book"
    ).length;

    const dandelions = state.progress.credits ?? 0;
    const streak = state.progress.streak ?? 0;
    const gameLevel = state.progress.level ?? 1;
    const instrument = state.profile.instrument || "Instrument not set";

    if (woodchuckNameEl) {
      woodchuckNameEl.textContent =
        state.profile.woodchuckName || "Name your Woodchuck";
    }

    if (campLevelEl) {
      campLevelEl.textContent = "Chuckling";
    }

    if (instrumentObjectEl) {
      instrumentObjectEl.title = instrument;
      instrumentObjectEl.setAttribute("aria-label", instrument);
    }

    if (levelEl) {
      levelEl.textContent = `#${gameLevel}`;
      levelEl.setAttribute("aria-label", `Level ${gameLevel}`);
    }

    if (streakEl) {
      streakEl.textContent = `Streak ${streak}`;
      streakEl.setAttribute(
        "aria-label",
        `${streak} day practice streak`
      );
    }

    if (totalPChartsEl) {
      totalPChartsEl.textContent = `P-Charts ${totalPCharts}`;
      totalPChartsEl.setAttribute(
        "aria-label",
        `${totalPCharts} total P-Charts`
      );
    }

    if (creditsEl) {
      creditsEl.textContent = String(dandelions);
    }

    if (dandelionObjectEl) {
      dandelionObjectEl.setAttribute(
        "aria-label",
        `${dandelions} dandelions. Open the shop.`
      );
    }
  }

  function updateStreak(progress, today) {
    if (progress.lastCompletedDate === today) return;

    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayKey = stateApi.localDateKey(yesterday);

    if (progress.lastCompletedDate === yesterdayKey) {
      progress.streak += 1;
    } else {
      progress.streak = 1;
    }

    progress.lastCompletedDate = today;
  }

  const INSTRUMENT_ADVICE = {
    Flute: "Use the head joint trick if you're having a hard time getting notes out.",
    Clarinet: "Cover the holes all the way to prevent squawking.",
    Saxophone: "Use full tone, but don't play loud.",
    Trumpet: "Keep getting faster... Then one day we will show you double-tonguing!",
    Trombone: "Use more air!",
    Tuba: "Big air. Let the room rumble.",
    Percussion: "Paradiddles are like tongue-twisters for drummers.",
  };

  function updateInstrumentAdvice(state) {
    const adviceEl = document.getElementById("instrument-advice");
    if (!adviceEl) return;

    const instrument = state.profile.instrument;
    const advice = INSTRUMENT_ADVICE[instrument] || "Choose a quest and keep moving.";
    adviceEl.textContent = `“${advice}”`;
  }

  function wireQuestForm(state) {
    const questTextEl = document.getElementById("quest-text");
    const questTargetEl = document.getElementById("quest-target");
    const questStatusEl = document.getElementById("quest-status");
    const questProgressEl = document.getElementById("quest-progress");
    const form = document.getElementById("practice-form");
    const minutesEl = document.getElementById("practice-minutes");
    const noteEl = document.getElementById("practice-note");
    const errorEl = document.getElementById("practice-error");
    const feedbackEl = document.getElementById("quest-feedback");
    const completeBtn = document.getElementById("complete-quest-btn");
    const chooseQuestBtn = document.getElementById("choose-quest-btn");
    const skipQuestBtn = document.getElementById("skip-quest-btn");
    const questChoicePanel = document.getElementById("quest-choice-panel");
    const questChoiceList = document.getElementById("quest-choice-list");

    if (!form || !questTextEl || !questTargetEl || !questStatusEl) return;

    const today = stateApi.localDateKey();

    function renderQuestStatus(s) {
      questTextEl.textContent = s.daily.questText;
      questTargetEl.textContent = String(s.daily.targetMinutes);
      questStatusEl.textContent = s.daily.completed ? "Complete ✅" : "Not completed";

      if (questProgressEl) {
        questProgressEl.textContent = `${s.daily.loggedMinutes || 0}/${s.daily.targetMinutes} minutes logged`;
      }

      if (completeBtn && s.daily.completed) {
        completeBtn.textContent = "Quest Complete";
      }
    }

    function activateQuest(next, quest) {
      const todayKey = stateApi.localDateKey();

      next.daily = {
        dateKey: todayKey,
        questId: quest.id,
        questText: quest.text,
        targetMinutes: quest.target_minutes,
        rewardCredits: quest.reward_credits,
        loggedMinutes: 0,
        completed: false,
        completedAt: null,
        encouragement: "",
      };

      next.quest = {
        dateKey: todayKey,
        text: quest.text,
        targetMinutes: quest.target_minutes,
        completed: false,
        rewardCredits: quest.reward_credits,
      };
    }

    function renderQuestChoices() {
      if (!questChoicePanel || !questChoiceList) return;

      const current = stateApi.getState();
      const quests = questPool[current.profile.instrument] || [];

      if (!quests.length) {
        questChoiceList.innerHTML = "<p>No alternate quests found for this instrument yet.</p>";
        questChoicePanel.classList.remove("hidden");
        return;
      }

      questChoiceList.innerHTML = quests
        .map((quest) => `
          <button class="quest-choice-card" type="button" data-quest-id="${quest.id}">
            <strong>${quest.text}</strong>
            <small>${quest.target_minutes} minutes · ${quest.reward_credits} dandelions</small>
          </button>
        `)
        .join("");

      questChoicePanel.classList.remove("hidden");
    }

    renderQuestStatus(state);
    updateInstrumentAdvice(state);

    if (state.daily.completed && feedbackEl) {
      feedbackEl.querySelector("p:last-child").textContent = pickMessage("already_done", today);
    }

    if (chooseQuestBtn) {
      chooseQuestBtn.addEventListener("click", renderQuestChoices);
    }

    if (skipQuestBtn) {
      skipQuestBtn.addEventListener("click", function () {
        const next = stateApi.getState();
        const quests = questPool[next.profile.instrument] || [];

        if (!quests.length) return;

        const currentIndex = quests.findIndex((quest) => quest.id === next.daily.questId);
        const nextQuest = quests[(currentIndex + 1) % quests.length];

        activateQuest(next, nextQuest);
        stateApi.saveState(next);
        renderQuestStatus(next);
        hydrateHome(next);
        updateInstrumentAdvice(next);

        if (feedbackEl) {
          feedbackEl.querySelector("p:last-child").textContent = "Quest skipped. Pick up momentum with this one instead.";
        }
      });
    }

    if (questChoiceList) {
      questChoiceList.addEventListener("click", function (event) {
        const button = event.target.closest("[data-quest-id]");
        if (!button) return;

        const next = stateApi.getState();
        const quests = questPool[next.profile.instrument] || [];
        const selectedQuest = quests.find((quest) => quest.id === button.dataset.questId);

        if (!selectedQuest) return;

        activateQuest(next, selectedQuest);
        stateApi.saveState(next);
        renderQuestStatus(next);
        hydrateHome(next);
        updateInstrumentAdvice(next);

        if (questChoicePanel) questChoicePanel.classList.add("hidden");
        if (feedbackEl) {
          feedbackEl.querySelector("p:last-child").textContent = "Quest selected. Keep moving.";
        }
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      errorEl.textContent = "";

      const next = stateApi.getState();
      ensureTodayQuest(next);

      const dateKey = stateApi.localDateKey();
      const minutes = Number(minutesEl.value);
      const note = noteEl.value.trim();

      if (!Number.isFinite(minutes) || minutes <= 0) {
        errorEl.textContent = "Enter a positive number of minutes.";
        return;
      }

      next.practiceLog.unshift({
        dateKey,
        minutes,
        note,
        questId: next.daily.questId,
        creditsAwarded: 0,
        loggedAt: new Date().toISOString(),
      });

      next.practiceLog = next.practiceLog.slice(0, 50);
      next.daily.loggedMinutes = (next.daily.loggedMinutes || 0) + minutes;

      if (next.daily.completed && next.daily.dateKey === dateKey) {
        feedbackEl.querySelector("p:last-child").textContent = pickMessage("already_done", dateKey);
        stateApi.saveState(next);
        renderQuestStatus(next);
        return;
      }

      if (next.daily.loggedMinutes >= next.daily.targetMinutes) {
        next.daily.completed = true;
        next.daily.completedAt = new Date().toISOString();
        next.progress.credits += next.daily.rewardCredits;
        updateStreak(next.progress, dateKey);

        const lastLog = next.practiceLog[0];
        lastLog.creditsAwarded = next.daily.rewardCredits;

        feedbackEl.querySelector("p:last-child").textContent =
          `${pickMessage("reward", dateKey)} +${next.daily.rewardCredits} dandelions earned.`;
      } else {
        feedbackEl.querySelector("p:last-child").textContent =
          `${pickMessage("supportive", dateKey)} (${next.daily.loggedMinutes}/${next.daily.targetMinutes} minutes)`;
      }

      next.daily.encouragement = feedbackEl.querySelector("p:last-child").textContent;
      stateApi.saveState(next);
      renderQuestStatus(next);
      hydrateHome(next);
      minutesEl.value = "";
      noteEl.value = "";
    });
  }

  const STORE_ITEMS = [
    { id: "hat-red", name: "Red Hat", slot: "head", price: 15 },
    { id: "hat-blue", name: "Blue Hat", slot: "head", price: 15 },
    { id: "hat-green", name: "Green Hat", slot: "head", price: 15 },
    { id: "hat-purple", name: "Purple Hat", slot: "head", price: 15 },
    { id: "hoodie-red", name: "Red Hoodie", slot: "body", price: 25 },
    { id: "hoodie-blue", name: "Blue Hoodie", slot: "body", price: 25 },
    { id: "hoodie-green", name: "Green Hoodie", slot: "body", price: 25 },
    { id: "hoodie-purple", name: "Purple Hoodie", slot: "body", price: 25 },
    { id: "water-bottle-red", name: "Red Water Bottle", slot: "waterBottle", price: 15 },
    { id: "water-bottle-blue", name: "Blue Water Bottle", slot: "waterBottle", price: 15 },
    { id: "water-bottle-green", name: "Green Water Bottle", slot: "waterBottle", price: 15 },
    { id: "water-bottle-purple", name: "Purple Water Bottle", slot: "waterBottle", price: 15 },
  ];

  function getStoreItem(itemId) {
    return STORE_ITEMS.find((item) => item.id === itemId);
  }

  function itemDisplayName(itemId) {
    const item = getStoreItem(itemId);
    return item ? item.name : "None";
  }

  function ensureInventoryShape(state) {
    state.inventory = state.inventory || {};
    state.inventory.ownedItems = Array.isArray(state.inventory.ownedItems)
      ? state.inventory.ownedItems
      : [];
    state.inventory.equipped = state.inventory.equipped || {};
    state.inventory.equipped.head = state.inventory.equipped.head || null;
    state.inventory.equipped.body = state.inventory.equipped.body || null;
    state.inventory.equipped.waterBottle = state.inventory.equipped.waterBottle || null;
    return state;
  }


  const BAND_CAMP_TRIVIA = [
    {
      question: "How many beats does a whole note receive in 4/4 time?",
      options: ["2", "3", "4"],
      answer: 2,
    },
    {
      question: "Which word means to gradually get louder?",
      options: ["Crescendo", "Diminuendo", "Fermata"],
      answer: 0,
    },
    {
      question: "What does a conductor’s upbeat usually help signal?",
      options: ["An entrance", "A break", "The end of rehearsal"],
      answer: 0,
    },
    {
      question: "What should most wind players use for a stronger tone?",
      options: ["Less air", "More air", "A tighter music stand"],
      answer: 1,
    },
    {
      question: "What does the marking piano mean?",
      options: ["Play softly", "Play quickly", "Stop playing"],
      answer: 0,
    },
    {
      question: "Which section usually includes trumpets and trombones?",
      options: ["Woodwinds", "Brass", "Percussion"],
      answer: 1,
    },
    {
      question: "What does a metronome help a musician maintain?",
      options: ["Tempo", "Instrument color", "Music-stand height"],
      answer: 0,
    },
  ];

  const BAND_CAMP_MARCHING_CHALLENGES = [
    "Mark time for one minute while counting evenly.",
    "Practice eight steps forward while keeping your upper body still.",
    "Stand at attention with tall posture for thirty seconds.",
    "March sixteen counts while quietly singing your part.",
    "Practice a clean eight-count halt.",
    "Check that your toes, shoulders, and instrument face forward.",
    "March in place while clapping a steady four-beat pulse.",
  ];

  const BAND_CAMP_CROWNS = {
    hours: "Camp Commitment Crown",
    care: "Instrument Care Crown",
    trivia: "Trivia Crown",
    marching: "Marching Challenge Crown",
  };

  function wireBandCamp(state) {
    const playerNameEl = document.getElementById("board-player-name");
    if (!playerNameEl) return;

    const playerPointsEl = document.getElementById("board-player-points");
    const leaderNameEl = document.getElementById("board-leader-name");
    const leaderPointsEl = document.getElementById("board-leader-points");

    const hoursForm = document.getElementById("camp-hours-form");
    const hoursInput = document.getElementById("camp-hours");
    const hoursButton = document.getElementById("camp-hours-button");
    const hoursStatusEl = document.getElementById("camp-hours-status");

    const careButton = document.getElementById("instrument-care-button");
    const careStatusEl = document.getElementById("instrument-care-status");

    const triviaForm = document.getElementById("trivia-form");
    const triviaQuestionEl = document.getElementById("trivia-question");
    const triviaOptionsEl = document.getElementById("trivia-options");
    const triviaButton = document.getElementById("trivia-button");
    const triviaStatusEl = document.getElementById("trivia-status");

    const marchingTextEl = document.getElementById(
      "marching-challenge-text"
    );
    const marchingButton = document.getElementById(
      "marching-challenge-button"
    );
    const marchingStatusEl = document.getElementById(
      "marching-challenge-status"
    );

    const crownHoursEl = document.getElementById("crown-hours");
    const crownCareEl = document.getElementById("crown-care");
    const crownTriviaEl = document.getElementById("crown-trivia");
    const crownMarchingEl = document.getElementById("crown-marching");

    const pastWinnersList = document.getElementById(
      "past-winners-list"
    );
    const championsList = document.getElementById("champions-list");
    const feedbackEl = document.getElementById("board-feedback");

    const today = stateApi.localDateKey();
    const dayIndex = getDayIndex(new Date());
    const trivia =
      BAND_CAMP_TRIVIA[dayIndex % BAND_CAMP_TRIVIA.length];
    const marchingChallenge =
      BAND_CAMP_MARCHING_CHALLENGES[
        dayIndex % BAND_CAMP_MARCHING_CHALLENGES.length
      ];

    function freshBandCampDay(dateKey) {
      return {
        dateKey,
        hours: null,
        careComplete: false,
        triviaAttempted: false,
        triviaCorrect: false,
        marchingComplete: false,
        awarded: [],
      };
    }

    function prepareCurrentDay(current) {
      if (current.bandCamp.daily.dateKey !== today) {
        current.bandCamp.daily = freshBandCampDay(today);
        stateApi.saveState(current);
      }

      return current;
    }

    function playerName(current) {
      return current.profile.woodchuckName || "Your Woodchuck";
    }

    function hasAward(current, contestKey) {
      return current.bandCamp.daily.awarded.includes(contestKey);
    }

    function addCrownIfEarned(current, contestKey) {
      const wins = current.bandCamp.totals.wins[contestKey] || 0;

      if (wins < 10) return;

      const alreadyEarned = current.bandCamp.champions.some(
        (entry) => entry.contest === contestKey
      );

      if (alreadyEarned) return;

      current.bandCamp.champions.unshift({
        contest: contestKey,
        crown: BAND_CAMP_CROWNS[contestKey],
        name: playerName(current),
        earnedAt: today,
      });
    }

    function addDailyWinnerIfComplete(current) {
      const requiredContests = [
        "hours",
        "care",
        "trivia",
        "marching",
      ];

      const completedEverything = requiredContests.every(
        (contestKey) => hasAward(current, contestKey)
      );

      if (!completedEverything) return;

      const alreadyRecorded = current.bandCamp.pastWinners.some(
        (entry) => entry.dateKey === today
      );

      if (alreadyRecorded) return;

      current.bandCamp.pastWinners.unshift({
        dateKey: today,
        name: playerName(current),
        points: requiredContests.length,
      });

      current.bandCamp.pastWinners =
        current.bandCamp.pastWinners.slice(0, 20);
    }

    function awardContest(current, contestKey) {
      if (hasAward(current, contestKey)) return false;

      current.bandCamp.daily.awarded.push(contestKey);
      current.bandCamp.totals.points += 1;
      current.bandCamp.totals.wins[contestKey] += 1;
      current.progress.credits += 1;

      addCrownIfEarned(current, contestKey);
      addDailyWinnerIfComplete(current);

      return true;
    }

    function setButtonComplete(button, text) {
      if (!button) return;

      button.disabled = true;
      button.textContent = text;
    }

    function renderNameList(listEl, entries, emptyText, formatter) {
      if (!listEl) return;

      listEl.replaceChildren();

      if (!entries.length) {
        const item = document.createElement("li");
        item.textContent = emptyText;
        listEl.appendChild(item);
        return;
      }

      entries.forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = formatter(entry);
        listEl.appendChild(item);
      });
    }

    function crownText(wins, crownEarned) {
      const progress = `${Math.min(wins, 10)}/10`;
      return crownEarned ? `${progress} 👑` : progress;
    }

    function renderTriviaOptions(current) {
      if (!triviaOptionsEl) return;

      triviaOptionsEl.replaceChildren();

      trivia.options.forEach((option, index) => {
        const label = document.createElement("label");
        label.className = "trivia-option";

        const input = document.createElement("input");
        input.type = "radio";
        input.name = "trivia-answer";
        input.value = String(index);
        input.disabled = current.bandCamp.daily.triviaAttempted;

        const text = document.createElement("span");
        text.textContent = option;

        label.append(input, text);
        triviaOptionsEl.appendChild(label);
      });
    }

    function renderBoard(current) {
      const name = playerName(current);
      const points = current.bandCamp.totals.points;
      const wins = current.bandCamp.totals.wins;
      const daily = current.bandCamp.daily;
      const champions = current.bandCamp.champions;

      playerNameEl.textContent = name;

      if (playerPointsEl) {
        playerPointsEl.textContent = String(points);
      }

      if (leaderNameEl) {
        leaderNameEl.textContent = points > 0 ? name : "No camper yet";
      }

      if (leaderPointsEl) {
        leaderPointsEl.textContent =
          points === 1 ? "1 point" : `${points} points`;
      }

      if (hoursInput) {
        hoursInput.value =
          daily.hours === null ? "" : String(daily.hours);
        hoursInput.disabled = hasAward(current, "hours");
      }

      if (hasAward(current, "hours")) {
        setButtonComplete(hoursButton, "Added to Board");
        if (hoursStatusEl) {
          hoursStatusEl.textContent =
            `${daily.hours} camp hours recorded today`;
        }
      } else if (hoursStatusEl) {
        hoursStatusEl.textContent = "Not completed today";
      }

      if (daily.careComplete) {
        setButtonComplete(careButton, "Instrument ready ✓");
        if (careStatusEl) {
          careStatusEl.textContent = "Completed today";
        }
      } else if (careStatusEl) {
        careStatusEl.textContent = "Not completed today";
      }

      if (triviaQuestionEl) {
        triviaQuestionEl.textContent = trivia.question;
      }

      renderTriviaOptions(current);

      if (daily.triviaAttempted) {
        setButtonComplete(
          triviaButton,
          daily.triviaCorrect ? "Correct ✓" : "Attempt used"
        );

        if (triviaStatusEl) {
          triviaStatusEl.textContent = daily.triviaCorrect
            ? "Correct answer—point earned"
            : "Try a new question tomorrow";
        }
      } else if (triviaStatusEl) {
        triviaStatusEl.textContent = "One attempt per day";
      }

      if (marchingTextEl) {
        marchingTextEl.textContent = marchingChallenge;
      }

      if (daily.marchingComplete) {
        setButtonComplete(
          marchingButton,
          "Challenge completed ✓"
        );

        if (marchingStatusEl) {
          marchingStatusEl.textContent = "Completed today";
        }
      } else if (marchingStatusEl) {
        marchingStatusEl.textContent = "Not completed today";
      }

      if (crownHoursEl) {
        crownHoursEl.textContent = crownText(
          wins.hours,
          champions.some((entry) => entry.contest === "hours")
        );
      }

      if (crownCareEl) {
        crownCareEl.textContent = crownText(
          wins.care,
          champions.some((entry) => entry.contest === "care")
        );
      }

      if (crownTriviaEl) {
        crownTriviaEl.textContent = crownText(
          wins.trivia,
          champions.some((entry) => entry.contest === "trivia")
        );
      }

      if (crownMarchingEl) {
        crownMarchingEl.textContent = crownText(
          wins.marching,
          champions.some((entry) => entry.contest === "marching")
        );
      }

      renderNameList(
        pastWinnersList,
        current.bandCamp.pastWinners,
        "No daily champion yet.",
        (entry) =>
          `${entry.name} — ${entry.dateKey} — ${entry.points} points`
      );

      renderNameList(
        championsList,
        champions,
        "No crowns earned yet.",
        (entry) =>
          `${entry.name} — ${entry.crown} — ${entry.earnedAt}`
      );
    }

    let current = prepareCurrentDay(stateApi.getState());
    renderBoard(current);

    if (hoursForm) {
      hoursForm.addEventListener("submit", function (event) {
        event.preventDefault();

        const next = prepareCurrentDay(stateApi.getState());
        const hours = Number(hoursInput.value);

        if (!Number.isFinite(hours) || hours <= 0) {
          feedbackEl.textContent =
            "Enter how many hours you spent at band camp.";
          return;
        }

        if (hasAward(next, "hours")) return;

        next.bandCamp.daily.hours = hours;
        awardContest(next, "hours");
        stateApi.saveState(next);

        feedbackEl.textContent =
          `${hours} camp hours added. +1 Camp Point and +1 dandelion.`;

        renderBoard(next);
        hydrateHome(next);
      });
    }

    if (careButton) {
      careButton.addEventListener("click", function () {
        const next = prepareCurrentDay(stateApi.getState());

        if (next.bandCamp.daily.careComplete) return;

        next.bandCamp.daily.careComplete = true;
        awardContest(next, "care");
        stateApi.saveState(next);

        feedbackEl.textContent =
          "Instrument care completed. +1 Camp Point and +1 dandelion.";

        renderBoard(next);
        hydrateHome(next);
      });
    }

    if (triviaForm) {
      triviaForm.addEventListener("submit", function (event) {
        event.preventDefault();

        const next = prepareCurrentDay(stateApi.getState());

        if (next.bandCamp.daily.triviaAttempted) return;

        const selected = triviaForm.querySelector(
          'input[name="trivia-answer"]:checked'
        );

        if (!selected) {
          feedbackEl.textContent =
            "Choose an answer before submitting trivia.";
          return;
        }

        const isCorrect = Number(selected.value) === trivia.answer;

        next.bandCamp.daily.triviaAttempted = true;
        next.bandCamp.daily.triviaCorrect = isCorrect;

        if (isCorrect) {
          awardContest(next, "trivia");
          feedbackEl.textContent =
            "Correct! +1 Camp Point and +1 dandelion.";
        } else {
          feedbackEl.textContent =
            `Not quite. The correct answer was “${trivia.options[trivia.answer]}.”`;
        }

        stateApi.saveState(next);
        renderBoard(next);
        hydrateHome(next);
      });
    }

    if (marchingButton) {
      marchingButton.addEventListener("click", function () {
        const next = prepareCurrentDay(stateApi.getState());

        if (next.bandCamp.daily.marchingComplete) return;

        next.bandCamp.daily.marchingComplete = true;
        awardContest(next, "marching");
        stateApi.saveState(next);

        feedbackEl.textContent =
          "Marching challenge completed. +1 Camp Point and +1 dandelion.";

        renderBoard(next);
        hydrateHome(next);
      });
    }
  }

  function wireStore(state) {
    const creditsEl = document.getElementById("store-credits-value");
    const equippedHeadEl = document.getElementById("equipped-head-value");
    const equippedBodyEl = document.getElementById("equipped-body-value");
    const equippedWaterBottleEl = document.getElementById("equipped-water-bottle-value");
    const feedbackEl = document.getElementById("store-feedback");
    const itemsEl = document.getElementById("store-items");

    if (!creditsEl || !equippedHeadEl || !equippedBodyEl || !itemsEl) return;

    function renderStore(rawState) {
      const s = ensureInventoryShape(rawState);

      creditsEl.textContent = String(s.progress.credits ?? 0);
      equippedHeadEl.textContent = itemDisplayName(s.inventory.equipped.head);
      equippedBodyEl.textContent = itemDisplayName(s.inventory.equipped.body);
      if (equippedWaterBottleEl) {
        equippedWaterBottleEl.textContent = itemDisplayName(s.inventory.equipped.waterBottle);
      }
      const shelves = [
        { title: "HATS", slot: "head" },
        { title: "HOODIES", slot: "body" },
        { title: "WATER BOTTLES", slot: "waterBottle" },
      ];

      itemsEl.innerHTML = shelves.map((shelf) => {
        const shelfItems = STORE_ITEMS.filter((item) => item.slot === shelf.slot);

        const itemButtons = shelfItems.map((item) => {
          const owned = s.inventory.ownedItems.includes(item.id);
          const equipped = s.inventory.equipped[item.slot] === item.id;
          const cardClasses = ["store-item-card"];
          if (owned) cardClasses.push("owned");
          if (equipped) cardClasses.push("equipped");

          const primaryAction = owned
            ? `<button class="btn btn-secondary" type="button" data-equip-item="${item.id}">${equipped ? "Equipped" : "Equip"}</button>`
            : `<button class="btn btn-primary" type="button" data-buy-item="${item.id}">Buy</button>`;

          return `
            <article class="${cardClasses.join(" ")}">
              <h3>${item.name}</h3>
              <p>${item.price} dandelions</p>
              <div class="store-item-actions">
                ${primaryAction}
              </div>
            </article>
          `;
        }).join("");

        return `
          <details class="shop-shelf">
            <summary>${shelf.title}</summary>
            <div class="shop-shelf-items">
              ${itemButtons}
            </div>
          </details>
        `;
      }).join("");
    }

    itemsEl.addEventListener("click", function (event) {
      const buyButton = event.target.closest("[data-buy-item]");
      const equipButton = event.target.closest("[data-equip-item]");
      const next = ensureInventoryShape(stateApi.getState());

      if (buyButton) {
        const item = getStoreItem(buyButton.dataset.buyItem);
        if (!item) return;

        if (next.inventory.ownedItems.includes(item.id)) {
          feedbackEl.textContent = "You already own that item.";
          return;
        }

        if ((next.progress.credits || 0) < item.price) {
          feedbackEl.textContent = `Not enough dandelions yet. ${item.name} costs ${item.price} dandelions.`;
          return;
        }

        next.progress.credits -= item.price;
        next.inventory.ownedItems.push(item.id);
        next.inventory.equipped[item.slot] = item.id;

        stateApi.saveState(next);
        renderStore(next);
        feedbackEl.textContent = `${item.name} purchased and equipped.`;
        return;
      }

      if (equipButton) {
        const item = getStoreItem(equipButton.dataset.equipItem);
        if (!item) return;

        if (!next.inventory.ownedItems.includes(item.id)) {
          feedbackEl.textContent = "Buy that item before equipping it.";
          return;
        }

        next.inventory.equipped[item.slot] = item.id;

        stateApi.saveState(next);
        renderStore(next);
        feedbackEl.textContent = `${item.name} equipped.`;
      }
    });

    renderStore(ensureInventoryShape(state));
  }

  function wirePBook(state) {
    const form = document.getElementById("p-book-form");
    if (!form) return;

    const dateEl = document.getElementById("p-book-date");
    const minutesEl = document.getElementById("p-book-minutes");
    const noteEl = document.getElementById("p-book-note");
    const practiceDetailEls = Array.from(document.querySelectorAll("input[name='practice-detail']"));
    const timerDisplayEl = document.getElementById("practice-timer-display");
    const timerStartBtn = document.getElementById("practice-timer-start-btn");
    const timerStopBtn = document.getElementById("practice-timer-stop-btn");
    const timerFeedbackEl = document.getElementById("practice-timer-feedback");
    const errorEl = document.getElementById("p-book-error");
    const feedbackEl = document.getElementById("p-book-feedback");
    const entriesEl = document.getElementById("p-book-entries");
    const exportBtn = document.getElementById("export-p-chart-btn");
    const emailBtn = document.getElementById("email-p-chart-btn");
    const teacherEmailEl = document.getElementById("teacher-email");
    const parentEmailEl = document.getElementById("parent-email");
    const teacherEmailOptionsEl = document.getElementById("teacher-email-options");
    const parentEmailOptionsEl = document.getElementById("parent-email-options");

    const totalMinutesEl = document.getElementById("p-book-total-minutes");
    const practiceDaysEl = document.getElementById("p-book-practice-days");
    const pagesCountEl = document.getElementById("p-book-pages-count");

    const DANDELION_DAILY_CAP = 75;

    function calculateDandelionsForPractice(minutes, practiceDetails, existingEntries, dateKey) {
      const minuteDandelions = Math.floor(minutes / 5);
      const detailDandelions = Array.isArray(practiceDetails) ? practiceDetails.length : 0;
      const possibleDandelions = minuteDandelions + detailDandelions;

      const earnedToday = existingEntries
        .filter((entry) => entry.dateKey === dateKey)
        .reduce((sum, entry) => sum + (Number(entry.creditsAwarded) || 0), 0);

      const remainingToday = Math.max(0, DANDELION_DAILY_CAP - earnedToday);
      return Math.min(possibleDandelions, remainingToday);
    }

    let practiceTimerStartedAt = null;
    let practiceTimerInterval = null;

    function formatTimerSeconds(totalSeconds) {
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    function updatePracticeTimerDisplay() {
      if (!timerDisplayEl || !practiceTimerStartedAt) return;

      const elapsedSeconds = Math.max(0, Math.floor((Date.now() - practiceTimerStartedAt) / 1000));
      timerDisplayEl.textContent = formatTimerSeconds(elapsedSeconds);
    }

    function stopPracticeTimerInterval() {
      if (practiceTimerInterval) {
        window.clearInterval(practiceTimerInterval);
        practiceTimerInterval = null;
      }
    }

    function wirePracticeTimer() {
      if (!timerDisplayEl || !timerStartBtn || !timerStopBtn || !minutesEl) return;

      timerStartBtn.addEventListener("click", function () {
        practiceTimerStartedAt = Date.now();
        stopPracticeTimerInterval();
        timerDisplayEl.textContent = "00:00";
        practiceTimerInterval = window.setInterval(updatePracticeTimerDisplay, 1000);

        if (timerFeedbackEl) {
          timerFeedbackEl.textContent = "Timer started. Go make some music.";
        }
      });

      timerStopBtn.addEventListener("click", function () {
        if (!practiceTimerStartedAt) {
          if (timerFeedbackEl) {
            timerFeedbackEl.textContent = "Start the timer first.";
          }
          return;
        }

        const elapsedSeconds = Math.max(0, Math.floor((Date.now() - practiceTimerStartedAt) / 1000));
        const elapsedMinutes = Math.max(1, Math.round(elapsedSeconds / 60));

        stopPracticeTimerInterval();
        timerDisplayEl.textContent = formatTimerSeconds(elapsedSeconds);
        practiceTimerStartedAt = null;

        const shouldFillMinutes = window.confirm(`Do you want to enter ${elapsedMinutes} practice minute${elapsedMinutes === 1 ? "" : "s"}?`);
        if (shouldFillMinutes) {
          minutesEl.value = String(elapsedMinutes);
          if (timerFeedbackEl) {
            timerFeedbackEl.textContent = `${elapsedMinutes} minute${elapsedMinutes === 1 ? "" : "s"} added. You can change it before submitting.`;
          }
        } else if (timerFeedbackEl) {
          timerFeedbackEl.textContent = "Timer stopped. Minutes were not added.";
        }
      });
    }

    function getRecentEmails(s, type) {
      const contacts = s.exportContacts || {};
      const emails = contacts[type] || [];
      return Array.isArray(emails) ? emails : [];
    }

    function isValidEmail(email) {
      return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);
    }

    function saveRecentEmail(state, type, email) {
      const cleanEmail = email.trim().toLowerCase();
      if (!cleanEmail || !isValidEmail(cleanEmail)) return;

      state.exportContacts = state.exportContacts || {};
      const current = getRecentEmails(state, type);
      const nextEmails = [cleanEmail, ...current.filter((item) => item !== cleanEmail)].slice(0, 5);
      state.exportContacts[type] = nextEmails;
    }

    function renderEmailOptions(s) {
      const teacherEmails = getRecentEmails(s, "teacherEmails");
      const parentEmails = getRecentEmails(s, "parentEmails");

      if (teacherEmailOptionsEl) {
        teacherEmailOptionsEl.innerHTML = teacherEmails
          .map((email) => `<option value="${email}"></option>`)
          .join("");
      }

      if (parentEmailOptionsEl) {
        parentEmailOptionsEl.innerHTML = parentEmails
          .map((email) => `<option value="${email}"></option>`)
          .join("");
      }
    }

    function formatEntry(entry) {
      const noteText = entry.note ? ` — ${entry.note}` : "";
      const details = Array.isArray(entry.practiceDetails) ? entry.practiceDetails : [];
      const detailText = details.length ? ` — ${details.join(", ")}` : "";

      return `${entry.dateKey} — ${entry.minutes} minutes${detailText}${noteText}`;
    }

    function renderEntries(s) {
      if (!entriesEl) return;

      const entries = Array.isArray(s.practiceLog) ? s.practiceLog.slice(0, 10) : [];

      if (!entries.length) {
        entriesEl.innerHTML = "<p>No practice pages logged yet.</p>";
        return;
      }

      entriesEl.innerHTML = entries
        .map((entry) => `<p>${formatEntry(entry)}</p>`)
        .join("");
    }

    function renderPBookSummary(s) {
      const entries = Array.isArray(s.practiceLog) ? s.practiceLog : [];
      const totalMinutes = entries.reduce((sum, entry) => sum + (Number(entry.minutes) || 0), 0);
      const practiceDays = new Set(entries.map((entry) => entry.dateKey).filter(Boolean)).size;
      const pagesCount = entries.length;

      if (totalMinutesEl) totalMinutesEl.textContent = String(totalMinutes);
      if (practiceDaysEl) practiceDaysEl.textContent = String(practiceDays);
      if (pagesCountEl) pagesCountEl.textContent = String(pagesCount);
    }

    function buildExportText(s) {
      const profileName = s.profile.woodchuckName || "Not named";
      const instrument = s.profile.instrument || "Not set";
      const entries = Array.isArray(s.practiceLog) ? s.practiceLog : [];
      const totalMinutes = entries.reduce((sum, entry) => sum + (Number(entry.minutes) || 0), 0);

      const lines = [
        "Woodshed Woodchuck Practice Chart",
        "",
        `Student/Woodchuck: ${profileName}`,
        `Instrument: ${instrument}`,
        `Total Minutes: ${totalMinutes}`,
        "",
        "Practice Entries:",
      ];

      if (!entries.length) {
        lines.push("No practice entries yet.");
      } else {
        entries.forEach((entry) => {
          lines.push(formatEntry(entry));
        });
      }

      return lines.join("\n");
    }

    dateEl.value = stateApi.localDateKey();
    renderEntries(state);
    renderPBookSummary(state);
    renderEmailOptions(state);
    wirePracticeTimer();

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      errorEl.textContent = "";
      feedbackEl.classList.remove("success-callout");

      const dateKey = dateEl.value || stateApi.localDateKey();
      const minutes = Number(minutesEl.value);
      const note = noteEl.value.trim();
      const practiceDetails = practiceDetailEls
        .filter((checkbox) => checkbox.checked)
        .map((checkbox) => checkbox.value);

      if (!Number.isFinite(minutes) || minutes <= 0) {
        errorEl.textContent = "Enter a positive number of minutes.";
        return;
      }

      const next = stateApi.getState();
      const dandelionsEarned = calculateDandelionsForPractice(
        minutes,
        practiceDetails,
        next.practiceLog || [],
        dateKey
      );

      next.practiceLog.unshift({
        dateKey,
        minutes,
        note,
        practiceDetails,
        source: "p-book",
        creditsAwarded: dandelionsEarned,
        loggedAt: new Date().toISOString(),
      });

      next.practiceLog = next.practiceLog.slice(0, 100);
      next.progress.credits = (next.progress.credits || 0) + dandelionsEarned;

      stateApi.saveState(next);
      renderEntries(next);
      renderPBookSummary(next);

      feedbackEl.classList.add("success-callout");
      feedbackEl.textContent = `A new page was added to your P-Book. +${dandelionsEarned} dandelions added to your bank.`;
      minutesEl.value = "";
      noteEl.value = "";
      practiceDetailEls.forEach((checkbox) => {
        checkbox.checked = false;
      });
      hydrateHome(next);
    });

    if (exportBtn) {
      exportBtn.addEventListener("click", async function () {
        const next = stateApi.getState();
        const exportText = buildExportText(next);

        try {
          await navigator.clipboard.writeText(exportText);
          feedbackEl.textContent = "P-Chart copied. You can paste it into a message or email for your band director.";
        } catch (_err) {
          feedbackEl.textContent = exportText;
        }
      });
    }

    if (emailBtn) {
      emailBtn.addEventListener("click", function () {
        const next = stateApi.getState();
        const exportText = buildExportText(next);
        const teacherEmail = teacherEmailEl ? teacherEmailEl.value.trim() : "";
        const parentEmail = parentEmailEl ? parentEmailEl.value.trim() : "";
        saveRecentEmail(next, "teacherEmails", teacherEmail);
        saveRecentEmail(next, "parentEmails", parentEmail);
        stateApi.saveState(next);
        renderEmailOptions(next);
        const subject = "Woodshed Woodchuck Practice Chart";

        const params = new URLSearchParams();
        if (parentEmail) params.set("cc", parentEmail);
        params.set("subject", subject);
        params.set("body", exportText);

        const mailtoUrl = `mailto:${encodeURIComponent(teacherEmail)}?${params.toString()}`;
        window.location.href = mailtoUrl;
      });
    }
  }

  const state = ensureTodayQuest(stateApi.getState());
  stateApi.saveState(state);

  if (!routeGuard(state)) return;

  wireSetupForm(state);
  hydrateHome(state);
  wireQuestForm(state);
  wireBandCamp(state);
  wireStore(state);
  wirePBook(state);
})();
