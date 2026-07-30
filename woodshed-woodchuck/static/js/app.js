(function () {
  const stateApi = window.WWState;
  if (!stateApi) return;

  function playSound(effectName) {
    if (window.WoodshedAudio) window.WoodshedAudio.play(effectName);
  }

  function playCampReward(includeTriviaChime) {
    if (window.WoodshedAudio) {
      window.WoodshedAudio.playCampReward(Boolean(includeTriviaChime));
    }
  }

  function playNewCrownIfConfirmed(payload) {
    if (payload && payload.crown_newly_earned === true) {
      playSound("crownEarned");
    }
  }

  function celebrateSuccess(origin) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const burst = document.createElement("div");
    burst.className = "success-confetti";
    burst.setAttribute("aria-hidden", "true");
    const colors = ["#f4d35e", "#ee964b", "#f95738", "#74c0fc", "#90be6d"];
    for (let index = 0; index < 24; index += 1) {
      const piece = document.createElement("span");
      piece.style.setProperty("--confetti-x", `${(index % 8) * 12 - 42}vw`);
      piece.style.setProperty("--confetti-delay", `${(index % 6) * 30}ms`);
      piece.style.backgroundColor = colors[index % colors.length];
      burst.appendChild(piece);
    }
    document.body.appendChild(burst);
    window.setTimeout(() => burst.remove(), 1400);
  }

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

    form.addEventListener("submit", async function (event) {
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
      stateApi.saveState(next, { sync: false });
    });
  }

  function hydrateHome(state) {
    const creditsEl = document.getElementById("credits-value");
    const streakEl = document.getElementById("streak-value");
    const woodchuckNameEl = document.getElementById("woodchuck-name-value");
    const instrumentObjectEl = document.getElementById("instrument-object");
    const levelEl = document.getElementById("level-value");
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
    const instrument = state.profile.instrument || "Instrument not set";

    if (woodchuckNameEl) {
      woodchuckNameEl.textContent =
        state.profile.woodchuckName || "Name your Woodchuck";
      woodchuckNameEl.setAttribute(
        "aria-label",
        `Change Woodchuck name. Current name: ${state.profile.woodchuckName || "not set"}`
      );
    }

    if (instrumentObjectEl) {
      if (window.WWInstruments) {
        window.WWInstruments.renderInstrument(instrumentObjectEl, instrument);
      } else {
        instrumentObjectEl.textContent = "♪";
        instrumentObjectEl.title = instrument;
        instrumentObjectEl.setAttribute("aria-label", instrument);
      }
      instrumentObjectEl.setAttribute(
        "aria-label",
        `Change instrument. Current instrument: ${instrument}`
      );
      instrumentObjectEl.title = "Change instrument";
    }

    if (levelEl) {
      const profileLevel = state.profile.level || "Level not set";
      levelEl.textContent = profileLevel === "Level not set"
        ? "—"
        : profileLevel.charAt(0).toUpperCase();
      levelEl.setAttribute(
        "aria-label",
        `Level: ${profileLevel}. Change level.`
      );
      levelEl.title = `Level: ${profileLevel}. Change level.`;
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
        window.location.pathname === "/store"
          ? `${dandelions} dandelions.`
          : `${dandelions} dandelions. Open the shop.`
      );
    }
  }

  function wireShedSecret() {
    const trigger = document.getElementById("shed-secret-button");
    const panel = document.getElementById("shed-secret-panel");
    const form = document.getElementById("shed-secret-form");
    const input = document.getElementById("shed-secret-passcode");
    const feedback = document.getElementById("shed-secret-feedback");
    if (!trigger || !panel || !form || !input || !feedback) return;
    const closeButtons = [
      document.getElementById("shed-secret-cancel"),
      document.getElementById("shed-secret-close"),
    ].filter(Boolean);
    function close() {
      panel.hidden = true;
      panel.classList.add("hidden");
      trigger.setAttribute("aria-expanded", "false");
      input.value = "";
      trigger.focus();
    }
    trigger.addEventListener("click", function () {
      panel.hidden = false;
      panel.classList.remove("hidden");
      trigger.setAttribute("aria-expanded", "true");
      feedback.textContent = "";
      input.focus();
    });
    closeButtons.forEach((button) => button.addEventListener("click", close));
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const submit = form.querySelector("button[type='submit']");
      submit.disabled = true;
      try {
        const response = await fetch("/account/daily-secret", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ passcode: input.value }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "The secret could not be checked.");
        playNewCrownIfConfirmed(payload);
        const next = stateApi.getState();
        if (Number.isInteger(payload.credits)) next.progress.credits = payload.credits;
        if (Number.isInteger(payload.revision)) next.account.serverRevision = payload.revision;
        stateApi.saveState(next, { sync: false });
        hydrateHome(next);
        feedback.textContent = payload.redeemed ? "+20 dandelions" : "Already found today. Come back tomorrow!";
        if (payload.redeemed) {
          celebrateSuccess(form);
          playSound("secretReward");
        }
      } catch (error) {
        feedback.textContent = error.message || "That passcode did not match. Try again.";
      } finally {
        submit.disabled = false;
      }
    });
  }

  async function refreshPracticeStreak() {
    const streakEl = document.getElementById("streak-value");
    if (!streakEl) return;
    try {
      const response = await fetch("/practice-charts/streak", {
        credentials: "same-origin", cache: "no-store",
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (!Number.isInteger(payload.streak) || payload.streak < 0) return;
      const next = stateApi.getState();
      next.progress.streak = payload.streak;
      stateApi.saveState(next, { sync: false });
      hydrateHome(next);
    } catch (_error) {
      // Retain the last known server-derived value while offline.
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
    Trumpet: "Build speed gradually with relaxed, even articulation.",
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
        completeBtn.classList.add("is-confirmed-success");
        completeBtn.disabled = true;
      } else if (completeBtn) {
        completeBtn.classList.remove("is-confirmed-success");
        completeBtn.disabled = false;
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
      chooseQuestBtn.addEventListener("click", function () {
        playSound("dialClick");
        renderQuestChoices();
      });
    }

    if (skipQuestBtn) {
      skipQuestBtn.addEventListener("click", function () {
        playSound("dialClick");
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

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      playSound("dialClick");
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

      const completedNow = next.daily.loggedMinutes >= next.daily.targetMinutes;
      if (completedNow) {
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
      if (completedNow && window.WWAccountSync) {
        stateApi.saveState(next, { sync: false });
        const confirmed = await window.WWAccountSync.syncNow();
        if (!confirmed) {
          errorEl.textContent = "Quest completion could not be saved. Please try again.";
          return;
        }
        playSound("dandelionEarned");
      } else {
        stateApi.saveState(next);
      }
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




  function wireTuner() {
    const openButton = document.getElementById("tuner-open-button");
    const closeButton = document.getElementById("tuner-close-button");
    const panel = document.getElementById("tuner-panel");

    if (!openButton || !panel) return;

    openButton.addEventListener("click", function () {
      panel.classList.remove("hidden");
      openButton.setAttribute("aria-expanded", "true");

      panel.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });

      if (closeButton) {
        closeButton.focus();
      }
    });

    if (closeButton) {
      closeButton.addEventListener("click", function () {
        panel.classList.add("hidden");
        openButton.setAttribute("aria-expanded", "false");
        openButton.focus();
      });
    }
  }


  function wireMum(state) {
    const openButton = document.getElementById("mum-open-button");
    const closeButton = document.getElementById("mum-close-button");
    const readyButton = document.getElementById("mum-ready-button");
    const panel = document.getElementById("mum-panel");
    const messageEl = document.getElementById("mum-message");
    const choiceButtons = document.querySelectorAll("[data-mum-choice]");

    if (!openButton || !panel || !messageEl) return;

    const name = state.profile.woodchuckName || "musician";

    const dailyGreetings = [
      `Sit for a moment, ${name}. Have you eaten and had some water?`,
      `Welcome back, ${name}. Check your shoulders and take one slow breath.`,
      "Band camp takes energy. Make sure the musician is cared for too.",
      "Before the next challenge: water, food, music, pencil, and instrument.",
      "You do not have to practice tired and uncomfortable. Sit down a minute.",
    ];

    const responses = {
      water:
        "Take a few steady sips. You do not need to finish the whole bottle at once.",
      snack:
        "Choose something that will last through rehearsal—not just a quick burst of sugar.",
      rest:
        "Set the instrument down safely. Relax your jaw, shoulders, hands, and back for a few minutes.",
      camp:
        "Camp check: instrument, music, pencil, water, sunscreen, hat, comfortable shoes, and anything your director requested.",
    };

    function closeMumPanel() {
      panel.classList.add("hidden");
      openButton.setAttribute("aria-expanded", "false");
      openButton.focus();
    }

    openButton.addEventListener("click", function () {
      const greeting =
        dailyGreetings[getDayIndex(new Date()) % dailyGreetings.length];

      messageEl.textContent = greeting;
      panel.classList.remove("hidden");
      openButton.setAttribute("aria-expanded", "true");

      panel.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    });

    choiceButtons.forEach((button) => {
      button.addEventListener("click", function () {
        const response = responses[button.dataset.mumChoice];

        if (response) {
          messageEl.textContent = response;
        }
      });
    });

    if (closeButton) {
      closeButton.addEventListener("click", closeMumPanel);
    }

    if (readyButton) {
      readyButton.addEventListener("click", function () {
        messageEl.textContent =
          "Good. Take what you need with you, and do not rush the first note.";

        window.setTimeout(closeMumPanel, 900);
      });
    }

    panel.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeMumPanel();
      }
    });
  }

  function wireMetronome() {
    const openButton = document.getElementById(
      "metronome-open-button"
    );
    const closeButton = document.getElementById(
      "metronome-close-button"
    );
    const panel = document.getElementById("metronome-panel");
    const startButton = document.getElementById(
      "metronome-start-button"
    );
    const tapButton = document.getElementById(
      "metronome-tap-button"
    );
    const slowerButton = document.getElementById(
      "metronome-slower-button"
    );
    const fasterButton = document.getElementById(
      "metronome-faster-button"
    );
    const rangeInput = document.getElementById(
      "metronome-bpm-range"
    );
    const numberInput = document.getElementById(
      "metronome-bpm-input"
    );
    const bpmReadout = document.getElementById(
      "metronome-bpm-readout"
    );
    const pulse = document.getElementById("metronome-pulse");
    const status = document.getElementById("metronome-status");

    if (
      !openButton ||
      !panel ||
      !startButton ||
      !rangeInput ||
      !numberInput
    ) {
      return;
    }

    const BPM_STORAGE_KEY = "woodshedWoodchuckMetronomeBpm";
    const AudioContextClass =
      window.AudioContext || window.webkitAudioContext;

    let bpm = 100;
    let audioContext = null;
    let schedulerTimer = null;
    let nextBeatTime = 0;
    let isRunning = false;
    let tapTimes = [];
    const visualTimers = new Set();

    function clampBpm(value) {
      const numericValue = Number(value);

      if (!Number.isFinite(numericValue)) {
        return bpm;
      }

      return Math.min(220, Math.max(40, Math.round(numericValue)));
    }

    function saveBpm() {
      try {
        window.localStorage.setItem(BPM_STORAGE_KEY, String(bpm));
      } catch (_error) {
        // The metronome still works if browser storage is unavailable.
      }
    }

    function loadBpm() {
      try {
        const saved = window.localStorage.getItem(BPM_STORAGE_KEY);

        if (saved !== null) {
          bpm = clampBpm(saved);
        }
      } catch (_error) {
        bpm = 100;
      }
    }

    function renderBpm() {
      rangeInput.value = String(bpm);
      numberInput.value = String(bpm);

      if (bpmReadout) {
        bpmReadout.textContent = String(bpm);
      }

      saveBpm();
    }

    function setBpm(value) {
      bpm = clampBpm(value);
      renderBpm();

      if (status && !isRunning) {
        status.textContent =
          `Stopped at ${bpm} BPM.`;
      }
    }

    function queueVisualUpdate(callback, delayMilliseconds) {
      const timer = window.setTimeout(function () {
        visualTimers.delete(timer);
        callback();
      }, delayMilliseconds);

      visualTimers.add(timer);
    }

    function clearVisualTimers() {
      visualTimers.forEach((timer) => window.clearTimeout(timer));
      visualTimers.clear();
    }

    function showBeat(scheduledTime) {
      if (!audioContext || !pulse) return;

      const delay = Math.max(
        0,
        (scheduledTime - audioContext.currentTime) * 1000
      );

      queueVisualUpdate(function () {
        if (!isRunning) return;

        pulse.classList.remove("is-active");

        void pulse.offsetWidth;

        pulse.classList.add("is-active");

        queueVisualUpdate(function () {
          pulse.classList.remove("is-active");
        }, 110);
      }, delay);
    }

    function playClick(scheduledTime) {
      if (!audioContext) return;

      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(850, scheduledTime);

      gain.gain.setValueAtTime(0.0001, scheduledTime);
      gain.gain.exponentialRampToValueAtTime(
        0.14,
        scheduledTime + 0.003
      );
      gain.gain.exponentialRampToValueAtTime(
        0.0001,
        scheduledTime + 0.055
      );

      oscillator.connect(gain);
      gain.connect(audioContext.destination);

      oscillator.start(scheduledTime);
      oscillator.stop(scheduledTime + 0.06);

      showBeat(scheduledTime);
    }

    function scheduler() {
      if (!audioContext || !isRunning) return;

      while (
        nextBeatTime <
        audioContext.currentTime + 0.1
      ) {
        playClick(nextBeatTime);

        nextBeatTime += 60 / bpm;
      }
    }

    async function startMetronome() {
      if (!AudioContextClass) {
        if (status) {
          status.textContent =
            "This browser does not support the metronome audio tool.";
        }
        return;
      }

      if (!audioContext) {
        audioContext = new AudioContextClass();
      }

      if (audioContext.state === "suspended") {
        await audioContext.resume();
      }

      isRunning = true;
      nextBeatTime = audioContext.currentTime + 0.05;

      scheduler();
      schedulerTimer = window.setInterval(scheduler, 25);

      startButton.textContent = "Stop";
      startButton.classList.add("metronome-stop-button");

      if (status) {
        status.textContent =
          `Playing at ${bpm} BPM.`;
      }
    }

    function stopMetronome() {
      isRunning = false;

      if (schedulerTimer !== null) {
        window.clearInterval(schedulerTimer);
        schedulerTimer = null;
      }

      clearVisualTimers();

      if (pulse) {
        pulse.classList.remove("is-active");
      }

      startButton.textContent = "Start";
      startButton.classList.remove("metronome-stop-button");

      if (status) {
        status.textContent =
          `Stopped at ${bpm} BPM.`;
      }
    }

    function toggleMetronome() {
      if (isRunning) {
        stopMetronome();
      } else {
        startMetronome().catch(function () {
          if (status) {
            status.textContent =
              "The browser could not start metronome audio.";
          }
        });
      }
    }

    function registerTap() {
      const now = performance.now();
      const lastTap = tapTimes[tapTimes.length - 1];

      if (lastTap && now - lastTap > 2000) {
        tapTimes = [];
      }

      tapTimes.push(now);
      tapTimes = tapTimes.slice(-6);

      if (tapTimes.length < 2) {
        if (status) {
          status.textContent = "Tap again to set the tempo.";
        }
        return;
      }

      const intervals = [];

      for (let index = 1; index < tapTimes.length; index += 1) {
        const interval = tapTimes[index] - tapTimes[index - 1];

        if (interval >= 250 && interval <= 1500) {
          intervals.push(interval);
        }
      }

      if (!intervals.length) {
        if (status) {
          status.textContent =
            "Keep tapping a steady beat between 40 and 220 BPM.";
        }
        return;
      }

      const averageInterval =
        intervals.reduce((total, interval) => total + interval, 0) /
        intervals.length;

      setBpm(60000 / averageInterval);

      if (status) {
        status.textContent = `Tap tempo set to ${bpm} BPM.`;
      }
    }

    loadBpm();
    renderBpm();

    openButton.addEventListener("click", function () {
      panel.classList.remove("hidden");
      openButton.setAttribute("aria-expanded", "true");

      panel.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    });

    if (closeButton) {
      closeButton.addEventListener("click", function () {
        stopMetronome();
        panel.classList.add("hidden");
        openButton.setAttribute("aria-expanded", "false");
        openButton.focus();
      });
    }

    startButton.addEventListener("click", toggleMetronome);

    if (tapButton) {
      tapButton.addEventListener("click", registerTap);
    }

    if (slowerButton) {
      slowerButton.addEventListener("click", function () {
        setBpm(bpm - 5);
      });
    }

    if (fasterButton) {
      fasterButton.addEventListener("click", function () {
        setBpm(bpm + 5);
      });
    }

    rangeInput.addEventListener("input", function () {
      setBpm(rangeInput.value);
    });

    numberInput.addEventListener("change", function () {
      setBpm(numberInput.value);
    });

    window.addEventListener("pagehide", stopMetronome);
  }

  const BAND_CAMP_MARCHING_CHALLENGES = [
    "Mark time for one minute while counting evenly.",
    "Practice eight steps forward while keeping your upper body still.",
    "Stand at attention with tall posture for thirty seconds.",
    "March sixteen counts while quietly singing your part.",
    "Practice a clean eight-count halt.",
    "Check that your toes, shoulders, and instrument face forward.",
    "March in place while clapping a steady four-beat pulse.",
  ];

  function wireBandCamp(state) {
    const playerNameEl = document.getElementById("board-player-name");
    if (!playerNameEl) return;

    const playerPointsEl = document.getElementById("board-player-points");
    const playerWeeklyPointsEl = document.getElementById(
      "board-player-weekly-points"
    );

    const hoursCheckbox = document.getElementById("camp-hours-checkbox");
    const hoursStatusEl = document.getElementById("camp-hours-status");
    const hoursActivity = document.getElementById("camp-hours-activity");
    const hoursSummaryEl = document.getElementById("camp-hours-summary");

    const careButton = document.getElementById("instrument-care-button");
    const careStatusEl = document.getElementById("instrument-care-status");
    const careActivity = document.getElementById("instrument-care-activity");
    const careSummaryEl = document.getElementById("instrument-care-summary");

    const triviaForm = document.getElementById("trivia-form");
    const triviaQuestionEl = document.getElementById("trivia-question");
    const triviaOptionsEl = document.getElementById("trivia-options");
    const triviaButton = document.getElementById("trivia-button");
    const triviaStatusEl = document.getElementById("trivia-status");
    const triviaActivity = document.getElementById("trivia-activity");
    const triviaSummaryEl = document.getElementById("trivia-summary");

    const marchingTextEl = document.getElementById(
      "marching-challenge-text"
    );
    const marchingButton = document.getElementById(
      "marching-challenge-button"
    );
    const marchingStatusEl = document.getElementById(
      "marching-challenge-status"
    );
    const marchingActivity = document.getElementById("marching-activity");
    const marchingSummaryEl = document.getElementById("marching-challenge-summary");

    const feedbackEl = document.getElementById("board-feedback");

    const today = stateApi.localDateKey();
    const dayIndex = getDayIndex(new Date());
    let trivia = null;
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
      }

      return current;
    }

    function playerName(current) {
      return current.profile.woodchuckName || "Your Woodchuck";
    }

    function hasAward(current, contestKey) {
      return current.bandCamp.daily.awarded.includes(contestKey);
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

      addDailyWinnerIfComplete(current);

      return true;
    }

    const campAwardsInFlight = new Set();
    const serverConfirmedAwards = new Set();
    let serverConfirmedTriviaAttempt = null;

    async function persistCampPoint(activityType) {
      if (campAwardsInFlight.has(activityType)) return null;
      campAwardsInFlight.add(activityType);
      try {
        const response = await fetch("/contests/camp-points/awards", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            activity_type: activityType,
            activity_date: today,
          }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Camp points could not be saved.");
        }
        playNewCrownIfConfirmed(payload);
        if (payload.award && payload.award.activity_type) {
          serverConfirmedAwards.add(payload.award.activity_type);
        }
        if (playerWeeklyPointsEl && Number.isInteger(payload.weekly_points)) {
          playerWeeklyPointsEl.textContent = String(payload.weekly_points);
        }
        if (playerPointsEl && Number.isInteger(payload.career_points)) {
          playerPointsEl.textContent = String(payload.career_points);
        }
        window.dispatchEvent(new CustomEvent("ww:camp-points-saved"));
        return payload;
      } finally {
        campAwardsInFlight.delete(activityType);
      }
    }

    function renderActivityDisclosure(details, summary, activityType) {
      if (!details || !summary) return;
      const complete = activityType === "trivia"
        ? serverConfirmedTriviaAttempt !== null
        : serverConfirmedAwards.has(activityType);
      const rewarded = activityType !== "trivia"
        || serverConfirmedTriviaAttempt?.correct === true;
      summary.innerHTML = complete && rewarded
        ? '+1 Camp Point · +1 dandelion <span class="confirmed-checkmark" aria-hidden="true">✓</span><span class="sr-only"> Completed</span>'
        : complete
          ? 'Attempt used <span class="confirmed-checkmark" aria-hidden="true">✓</span><span class="sr-only"> Completed; no reward earned</span>'
        : activityType === "trivia"
          ? "One attempt per day"
          : "Not completed today";

      if (complete && details.dataset.serverComplete !== "true") {
        details.open = false;
      } else if (!complete) {
        details.open = true;
      }
      details.dataset.serverComplete = String(complete);
    }

    async function loadPersistedCampAwards() {
      const response = await fetch(
        `/contests/camp-points/awards/${encodeURIComponent(today)}`,
        { credentials: "same-origin", cache: "no-store" }
      );
      if (!response.ok) return;
      const payload = await response.json();
      if (payload.trivia_question && Array.isArray(payload.trivia_question.choices)) {
        trivia = payload.trivia_question;
      }
      const awards = Array.isArray(payload.awards) ? payload.awards : [];
      const next = prepareCurrentDay(stateApi.getState());
      const triviaAttempt = payload.trivia_attempt;

      if (playerWeeklyPointsEl && Number.isInteger(payload.weekly_points)) {
        playerWeeklyPointsEl.textContent = String(payload.weekly_points);
      }
      if (playerPointsEl && Number.isInteger(payload.career_points)) {
        playerPointsEl.textContent = String(payload.career_points);
      }

      awards.forEach((award) => {
        const activityType = award && award.activity_type;
        if (!["hours", "care", "trivia", "marching"].includes(activityType)) return;
        serverConfirmedAwards.add(activityType);
        if (!next.bandCamp.daily.awarded.includes(activityType)) {
          next.bandCamp.daily.awarded.push(activityType);
        }
        if (activityType === "care") next.bandCamp.daily.careComplete = true;
        if (activityType === "trivia") {
          next.bandCamp.daily.triviaAttempted = true;
          next.bandCamp.daily.triviaCorrect = true;
        }
        if (activityType === "marching") next.bandCamp.daily.marchingComplete = true;
      });

      if (triviaAttempt && Object.hasOwn(triviaAttempt, "selected_answer_id")) {
        serverConfirmedTriviaAttempt = triviaAttempt;
        next.bandCamp.daily.triviaAttempted = true;
        next.bandCamp.daily.triviaCorrect = triviaAttempt.correct === true;
        next.bandCamp.daily.triviaSelectedAnswer = triviaAttempt.selected_answer_id;
      }

      stateApi.saveState(next, { sync: false });
      renderBoard(next);
    }

    function setButtonComplete(button, text) {
      if (!button) return;

      button.disabled = true;
      button.textContent = text;
      button.classList.add("is-confirmed-success");
    }

    function renderTriviaOptions(current) {
      if (!triviaOptionsEl) return;

      triviaOptionsEl.replaceChildren();
      if (!trivia) return;
      const daily = current.bandCamp.daily;
      const selectedAnswerId = serverConfirmedTriviaAttempt?.selected_answer_id
        || daily.triviaSelectedAnswer;

      trivia.choices.forEach((choice) => {
        const label = document.createElement("label");
        label.className = "trivia-option";

        const input = document.createElement("input");
        input.type = "radio";
        input.name = "trivia-answer";
        input.value = choice.id;
        input.checked = choice.id === selectedAnswerId;
        input.disabled = daily.triviaAttempted;

        label.classList.toggle("is-selected", input.checked);
        label.classList.toggle(
          "is-confirmed-success",
          input.checked && daily.triviaAttempted && daily.triviaCorrect
        );

        const text = document.createElement("span");
        text.textContent = choice.text;

        label.append(input, text);
        triviaOptionsEl.appendChild(label);
      });
    }

    function renderBoard(current) {
      const name = playerName(current);
      const points = current.bandCamp.totals.points;
      const daily = current.bandCamp.daily;

      renderActivityDisclosure(hoursActivity, hoursSummaryEl, "hours");
      renderActivityDisclosure(careActivity, careSummaryEl, "care");
      renderActivityDisclosure(triviaActivity, triviaSummaryEl, "trivia");
      renderActivityDisclosure(marchingActivity, marchingSummaryEl, "marching");

      playerNameEl.textContent = name;

      if (playerPointsEl) {
        playerPointsEl.textContent = String(points);
      }

      if (hoursCheckbox) {
        const hoursComplete = serverConfirmedAwards.has("hours");
        hoursCheckbox.checked = hoursComplete;
        hoursCheckbox.disabled = hoursComplete;
        const label = hoursCheckbox.closest("label");
        if (label) label.classList.toggle("is-confirmed-success", hoursComplete);
      }

      if (serverConfirmedAwards.has("hours")) {
        if (hoursStatusEl) {
          hoursStatusEl.textContent = "Completed today";
        }
      } else if (hoursStatusEl) {
        hoursStatusEl.textContent = "Not completed today";
      }

      if (serverConfirmedAwards.has("care")) {
        setButtonComplete(careButton, "Instrument ready ✓");
        if (careStatusEl) {
          careStatusEl.textContent = "Completed today";
        }
      } else if (careStatusEl) {
        careStatusEl.textContent = "Not completed today";
      }

      if (triviaQuestionEl && trivia) {
        triviaQuestionEl.textContent = trivia.question;
      }

      renderTriviaOptions(current);

      if (daily.triviaAttempted) {
        triviaButton.disabled = true;
        triviaButton.textContent = daily.triviaCorrect ? "Correct ✓" : "Attempt used";
        triviaButton.classList.toggle(
          "is-confirmed-success",
          serverConfirmedTriviaAttempt?.correct === true
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

      if (serverConfirmedAwards.has("marching")) {
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

    }

    let current = prepareCurrentDay(stateApi.getState());
    renderBoard(current);
    loadPersistedCampAwards().catch(() => {
      // Leave activities open when server completion cannot be confirmed.
    });

    if (triviaOptionsEl) {
      triviaOptionsEl.addEventListener("change", function () {
        triviaOptionsEl.querySelectorAll(".trivia-option").forEach((label) => {
          const input = label.querySelector('input[name="trivia-answer"]');
          label.classList.toggle("is-selected", Boolean(input && input.checked));
        });
      });
    }

    if (hoursCheckbox) {
      hoursCheckbox.addEventListener("change", async function () {
        if (!hoursCheckbox.checked) return;
        const next = prepareCurrentDay(stateApi.getState());
        if (serverConfirmedAwards.has("hours")) return;
        hoursCheckbox.disabled = true;

        try {
          const persistedAward = await persistCampPoint("hours");
          if (!persistedAward) {
            throw new Error("Band Camp Hours could not be saved.");
          }
          if (persistedAward.created === true) {
            awardContest(next, "hours");
            playCampReward(false);
          } else if (!hasAward(next, "hours")) {
            next.bandCamp.daily.awarded.push("hours");
          }
        } catch (error) {
          hoursCheckbox.checked = false;
          hoursCheckbox.disabled = false;
          hoursActivity.open = true;
          feedbackEl.textContent = error.message ||
            "Band Camp Hours could not be saved. Please try again.";
          return;
        }

        stateApi.saveState(next);
        feedbackEl.textContent =
          "Band Camp Hours completed. +1 Camp Point and +1 dandelion.";

        renderBoard(next);
        hydrateHome(next);
      });
    }

    if (careButton) {
      careButton.addEventListener("click", async function () {
        const next = prepareCurrentDay(stateApi.getState());

        if (next.bandCamp.daily.careComplete) return;

        try {
          const persistedAward = await persistCampPoint("care");
          if (persistedAward && persistedAward.created === true) {
            playCampReward(false);
          }
        } catch (error) {
          feedbackEl.textContent = error.message || "Camp points could not be saved.";
          return;
        }

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
      triviaForm.addEventListener("submit", async function (event) {
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

        let checkedAnswer;
        try {
          const response = await fetch("/contests/trivia/answer", {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ activity_date: today, selected_answer_id: selected.value }),
          });
          checkedAnswer = await response.json();
          if (!response.ok) throw new Error(checkedAnswer.detail || "Trivia could not be checked.");
        } catch (error) {
          feedbackEl.textContent = error.message || "Trivia could not be checked.";
          return;
        }
        const isCorrect = checkedAnswer.correct === true;
        playNewCrownIfConfirmed(checkedAnswer);

        serverConfirmedTriviaAttempt = {
          selected_answer_id: checkedAnswer.selected_answer_id,
          correct: isCorrect,
        };

        next.bandCamp.daily.triviaAttempted = true;
        next.bandCamp.daily.triviaCorrect = isCorrect;
        next.bandCamp.daily.triviaSelectedAnswer = checkedAnswer.selected_answer_id;

        if (isCorrect) {
          serverConfirmedAwards.add("trivia");
          if (playerWeeklyPointsEl && Number.isInteger(checkedAnswer.weekly_points)) {
            playerWeeklyPointsEl.textContent = String(checkedAnswer.weekly_points);
          }
          if (playerPointsEl && Number.isInteger(checkedAnswer.career_points)) {
            playerPointsEl.textContent = String(checkedAnswer.career_points);
          }
          if (checkedAnswer.award_created === true) {
            celebrateSuccess(triviaForm);
            awardContest(next, "trivia");
            playCampReward(true);
          } else {
            if (checkedAnswer.created === true) playSound("correctTrivia");
            if (!next.bandCamp.daily.awarded.includes("trivia")) {
              next.bandCamp.daily.awarded.push("trivia");
            }
          }
          window.dispatchEvent(new CustomEvent("ww:camp-points-saved"));
          feedbackEl.textContent =
            "Correct! +1 Camp Point and +1 dandelion.";
        } else {
          if (checkedAnswer.created === true) playSound("incorrectTrivia");
          feedbackEl.textContent =
            "Not quite. No reward was earned today—thanks for giving it a try.";
        }

        stateApi.saveState(next);
        renderBoard(next);
        hydrateHome(next);
      });
    }

    if (marchingButton) {
      marchingButton.addEventListener("click", async function () {
        const next = prepareCurrentDay(stateApi.getState());

        if (next.bandCamp.daily.marchingComplete || marchingButton.disabled) return;

        const readyText = marchingButton.textContent;
        marchingButton.disabled = true;
        marchingButton.textContent = "Saving challenge…";

        let persistedAward;
        try {
          persistedAward = await persistCampPoint("marching");
          if (!persistedAward) throw new Error("Marching challenge could not be saved.");
        } catch (error) {
          marchingButton.disabled = false;
          marchingButton.textContent = readyText;
          marchingActivity.open = true;
          feedbackEl.textContent = error.message || "Camp points could not be saved.";
          return;
        }

        next.bandCamp.daily.marchingComplete = true;
        if (persistedAward.created === true) {
          awardContest(next, "marching");
          playCampReward(false);
        } else if (!hasAward(next, "marching")) {
          next.bandCamp.daily.awarded.push("marching");
        }
        stateApi.saveState(next);

        feedbackEl.textContent =
          "Marching challenge completed. +1 Camp Point and +1 dandelion.";

        renderBoard(next);
        hydrateHome(next);
      });
    }
  }

  function wirePlungeBurrow() {
    const button = document.getElementById("plunge-burrow-button");
    const panel = document.getElementById("plunge-burrow-panel");
    const close = document.getElementById("plunge-burrow-close");
    if (!button || !panel || !close) return;
    button.addEventListener("click", function () {
      panel.hidden = false; panel.classList.remove("hidden");
      button.setAttribute("aria-expanded", "true"); close.focus();
    });
    close.addEventListener("click", function () {
      panel.hidden = true; panel.classList.add("hidden");
      button.setAttribute("aria-expanded", "false"); button.focus();
    });
  }

  function wireBandCampStandings() {
    const root = document.getElementById("band-camp-standings");
    if (!root) return;

    const loadingEl = document.getElementById(
      "contest-standings-loading"
    );
    const errorEl = document.getElementById("contest-standings-error");
    const errorMessageEl = document.getElementById(
      "contest-standings-error-message"
    );
    const retryButton = document.getElementById("contest-standings-retry");
    const weekRangeEl = document.getElementById("contest-week-range");
    const weekContextEl = document.getElementById("contest-week-context");
    const weekStatusEl = document.getElementById("contest-week-status");
    let selectedDivision = "open";
    let requestInFlight = false;
    let refreshQueued = false;
    const tabs = [
      document.getElementById("contest-open-tab"),
      document.getElementById("contest-verified-tab"),
    ].filter(Boolean);
    const panels = {
      open: document.getElementById("contest-open-panel"),
      verified: document.getElementById("contest-verified-panel"),
    };

    function selectDivision(division, focusTab) {
      selectedDivision = division;
      tabs.forEach((tab) => {
        const selected = tab.id === `contest-${division}-tab`;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focusTab) tab.focus();
      });

      Object.entries(panels).forEach(([key, panel]) => {
        if (!panel) return;
        const selected = key === division;
        panel.hidden = !selected;
        panel.classList.toggle("hidden", !selected);
      });
      if (weekContextEl) {
        weekContextEl.textContent = `${division === "open" ? "Open" : "Verified"} division`;
      }
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", function () {
        selectDivision(tab.id.includes("verified") ? "verified" : "open", false);
      });
      tab.addEventListener("keydown", function (event) {
        let nextIndex = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          nextIndex = (index + 1) % tabs.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          nextIndex = (index - 1 + tabs.length) % tabs.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = tabs.length - 1;
        }
        if (nextIndex === null) return;
        event.preventDefault();
        selectDivision(
          tabs[nextIndex].id.includes("verified") ? "verified" : "open",
          true
        );
      });
    });

    function formatContestDate(value) {
      if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return null;
      }
      const [year, month, day] = value.split("-").map(Number);
      const parsed = new Date(year, month - 1, day);
      if (Number.isNaN(parsed.getTime())) return null;
      return parsed.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    }

    function renderDivision(division, rows) {
      const list = document.getElementById(
        `contest-${division}-standings`
      );
      const emptyEl = document.getElementById(`contest-${division}-empty`);
      if (!list || !emptyEl) return;

      list.replaceChildren();
      const safeRows = Array.isArray(rows)
        ? rows.filter((row) => (
            row &&
            Number.isInteger(row.rank) && row.rank > 0 &&
            typeof row.instrument === "string" &&
            row.instrument.trim() &&
            Number.isInteger(row.total_minutes) && row.total_minutes >= 0
          ))
        : [];

      safeRows.forEach((row) => {
        const teamName = window.WWInstruments &&
          window.WWInstruments.teamLabel(row.instrument);
        const rankedRow = document.createElement("article");
        rankedRow.className = `contest-ranked-row contest-rank-${Math.min(row.rank, 4)}`;
        rankedRow.setAttribute("role", "listitem");
        rankedRow.setAttribute(
          "aria-label",
          `Rank ${row.rank}, ${teamName || row.instrument}, ${row.total_minutes} practice minutes`
        );
        const rank = document.createElement("span");
        rank.className = "contest-rank-badge";
        rank.textContent = String(row.rank);
        const subject = document.createElement("span");
        subject.className = "contest-ranked-subject";
        const definition = window.WWInstruments &&
          window.WWInstruments.getDefinition(row.instrument);
        const icon = definition && definition.fallback_symbol
          ? definition.fallback_symbol
          : "♪";
        subject.textContent = `${icon} ${teamName || row.instrument}`;
        const score = document.createElement("strong");
        score.className = "contest-ranked-score";
        score.textContent = `${row.total_minutes} min`;
        rankedRow.append(rank, subject, score);
        list.appendChild(rankedRow);
      });

      const isEmpty = safeRows.length === 0;
      emptyEl.classList.toggle("hidden", !isEmpty);
      list.classList.toggle("hidden", isEmpty);
    }

    function ordinal(rank) {
      const remainder100 = rank % 100;
      if (remainder100 >= 11 && remainder100 <= 13) return `${rank}th`;
      if (rank % 10 === 1) return `${rank}st`;
      if (rank % 10 === 2) return `${rank}nd`;
      if (rank % 10 === 3) return `${rank}rd`;
      return `${rank}th`;
    }

    function positionMessage(division, position, campPoints = false) {
      if (!position || position.has_score !== true) {
        if (campPoints) {
          return "No Camp points yet · Complete a Band Camp activity to join the board.";
        }
        return division === "verified"
          ? "No verified minutes yet · Approved P-Charts appear here."
          : "No Open minutes yet · Submit a P-Chart to join the board.";
      }
      if (!Number.isInteger(position.rank) || position.rank < 1) {
        return "Your position is unavailable.";
      }
      const score = campPoints ? position.total_points : position.total_minutes;
      const behind = campPoints
        ? position.points_behind_leader
        : position.minutes_behind_leader;
      const parts = [
        position.tied === true
          ? `Tied for ${ordinal(position.rank)}`
          : `Rank ${position.rank}`,
        campPoints
          ? `${score} Camp ${score === 1 ? "point" : "points"}`
          : `${score} min`,
      ];
      if (position.rank === 1) {
        parts.push("Leading the board");
      } else if (Number.isInteger(behind)) {
        parts.push(campPoints
          ? `${behind} Camp ${behind === 1 ? "point" : "points"} behind leader`
          : `${behind} min behind leader`);
      }
      if (position.in_top_five === false) parts.push("Outside Top Five");
      return parts.join(" · ");
    }

    function renderPointsDivision(division, rows, position, kind = "points") {
      const campPoints = kind === "camp-points";
      const list = document.getElementById(`contest-${division}-${kind}`);
      const emptyEl = document.getElementById(
        `contest-${division}-${kind}-empty`
      );
      const messageEl = document.getElementById(
        `contest-${division}-${campPoints ? "camp-position" : "position"}-message`
      );
      if (!list || !emptyEl) return;

      list.replaceChildren();
      const safeRows = Array.isArray(rows)
        ? rows.filter((row) => (
            row &&
            Number.isInteger(row.rank) &&
            row.rank > 0 &&
            typeof row.display_name === "string" &&
            row.display_name.trim() &&
            Number.isInteger(campPoints ? row.total_points : row.total_minutes) &&
            (campPoints ? row.total_points : row.total_minutes) >= 0 &&
            typeof row.is_current_user === "boolean"
          ))
        : [];

      safeRows.forEach((row) => {
        const scoreValue = campPoints ? row.total_points : row.total_minutes;
        const rankedRow = document.createElement("article");
        rankedRow.className = `contest-ranked-row contest-rank-${Math.min(row.rank, 4)}`;
        rankedRow.setAttribute("role", "listitem");
        if (row.is_current_user) {
          rankedRow.classList.add("contest-current-user-row");
        }
        const publicName = row.is_current_user
          ? `${row.display_name} (You)`
          : row.display_name;
        rankedRow.setAttribute(
          "aria-label",
          `Rank ${row.rank}, ${publicName}, ${scoreValue} ${campPoints ? "Camp points" : "practice minutes"}`
        );
        const rank = document.createElement("span");
        rank.className = "contest-rank-badge";
        rank.textContent = String(row.rank);
        const subject = document.createElement("span");
        subject.className = "contest-ranked-subject";
        subject.textContent = publicName;
        const score = document.createElement("strong");
        score.className = "contest-ranked-score";
        score.textContent = campPoints
          ? `${scoreValue} Camp ${scoreValue === 1 ? "point" : "points"}`
          : `${scoreValue} min`;
        rankedRow.append(rank, subject, score);
        list.appendChild(rankedRow);
      });

      const isEmpty = safeRows.length === 0;
      emptyEl.classList.toggle("hidden", !isEmpty);
      list.classList.toggle("hidden", isEmpty);
      if (messageEl) {
        messageEl.textContent = positionMessage(division, position, campPoints);
      }
    }

    function showError(message) {
      if (loadingEl) loadingEl.classList.add("hidden");
      if (errorMessageEl) errorMessageEl.textContent = message;
      if (errorEl) errorEl.classList.remove("hidden");
      if (weekStatusEl) {
        weekStatusEl.textContent = "Unavailable";
        weekStatusEl.classList.remove("hidden");
      }
      root.setAttribute("aria-busy", "false");
    }

    async function loadStandings() {
      if (requestInFlight) {
        refreshQueued = true;
        return;
      }
      requestInFlight = true;
      root.setAttribute("aria-busy", "true");
      if (errorEl) errorEl.classList.add("hidden");
      if (weekStatusEl) {
        weekStatusEl.textContent = loadingEl && !loadingEl.classList.contains("hidden")
          ? "Loading"
          : "Refreshing";
        weekStatusEl.classList.remove("hidden");
      }
      try {
        const response = await fetch("/contests/current", {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (response.status === 401) {
          showError("Sign in to view the current Band Camp standings.");
          return;
        }
        if (!response.ok) {
          showError("Band Camp standings are unavailable right now.");
          return;
        }

        const payload = await response.json();
        const week = payload && payload.current_week;
        const standings = payload && payload.standings;
        const practiceStandings = standings &&
          standings["weekly-practice-by-instrument"];
        const pointsStandings = standings &&
          standings["weekly-points-leaders"];
        const campPointsStandings = standings &&
          standings["weekly-camp-points"];
        if (
          !week ||
          !practiceStandings ||
          !pointsStandings ||
          !Array.isArray(practiceStandings.open) ||
          !Array.isArray(practiceStandings.verified) ||
          !Array.isArray(pointsStandings.open) ||
          !Array.isArray(pointsStandings.verified) ||
          !pointsStandings.current_user_position ||
          !pointsStandings.current_user_position.open ||
          !pointsStandings.current_user_position.verified ||
          !campPointsStandings ||
          !Array.isArray(campPointsStandings.open) ||
          !campPointsStandings.current_user_position ||
          !campPointsStandings.current_user_position.open
        ) {
          showError("Band Camp standings could not be read.");
          return;
        }

        const startText = formatContestDate(week.week_start);
        let endText = null;
        if (typeof week.week_end === "string") {
          const endDate = new Date(`${week.week_end}T12:00:00`);
          if (!Number.isNaN(endDate.getTime())) {
            endDate.setDate(endDate.getDate() - 1);
            endText = endDate.toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
              year: "numeric",
            });
          }
        }
        if (weekRangeEl && startText && endText) {
          weekRangeEl.textContent = `${startText} – ${endText}`;
        }
        renderDivision("open", practiceStandings.open);
        renderDivision("verified", practiceStandings.verified);
        renderPointsDivision(
          "open",
          pointsStandings.open,
          pointsStandings.current_user_position.open
        );
        renderPointsDivision(
          "verified",
          pointsStandings.verified,
          pointsStandings.current_user_position.verified
        );
        renderPointsDivision(
          "open",
          campPointsStandings.open,
          campPointsStandings.current_user_position.open,
          "camp-points"
        );
        selectDivision(selectedDivision, false);
        if (loadingEl) loadingEl.classList.add("hidden");
        if (errorEl) errorEl.classList.add("hidden");
        if (weekStatusEl) weekStatusEl.classList.add("hidden");
        root.setAttribute("aria-busy", "false");
      } catch (_error) {
        showError("Band Camp standings are unavailable right now.");
      } finally {
        requestInFlight = false;
        if (refreshQueued) {
          refreshQueued = false;
          loadStandings();
        }
      }
    }

    selectDivision("open", false);
    retryButton.addEventListener("click", loadStandings);
    window.addEventListener("ww:p-chart-saved", loadStandings);
    window.addEventListener("ww:camp-points-saved", loadStandings);
    loadStandings();
  }

  function wirePastWinners() {
    const root = document.getElementById("past-winners");
    if (!root) return;

    const loadingEl = document.getElementById("past-winners-loading");
    const authEl = document.getElementById("past-winners-auth");
    const emptyEl = document.getElementById("past-winners-empty");
    const errorEl = document.getElementById("past-winners-error");
    const contentEl = document.getElementById("past-winners-content");
    const retryButton = document.getElementById("past-winners-retry");
    const weekSelect = document.getElementById("past-winners-week");
    const stateEls = [loadingEl, authEl, emptyEl, errorEl, contentEl];
    const tabs = [
      document.getElementById("past-winners-open-tab"),
      document.getElementById("past-winners-verified-tab"),
    ].filter(Boolean);
    const panels = {
      open: document.getElementById("past-winners-open-panel"),
      verified: document.getElementById("past-winners-verified-panel"),
    };
    const medals = {
      1: { key: "gold", emoji: "🥇", label: "Gold medal" },
      2: { key: "silver", emoji: "🥈", label: "Silver medal" },
      3: { key: "bronze", emoji: "🥉", label: "Bronze medal" },
    };

    function showState(element) {
      stateEls.forEach((candidate) => {
        if (candidate) candidate.classList.toggle("hidden", candidate !== element);
      });
      root.setAttribute("aria-busy", String(element === loadingEl));
    }

    function formatDate(value) {
      if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return null;
      }
      const [year, month, day] = value.split("-").map(Number);
      const parsed = new Date(year, month - 1, day);
      if (Number.isNaN(parsed.getTime())) return null;
      return parsed.toLocaleDateString(undefined, {
        month: "short", day: "numeric", year: "numeric",
      });
    }

    function weekLabel(week) {
      const start = formatDate(week.week_start);
      const [year, month, day] = week.week_end.split("-").map(Number);
      const sunday = new Date(year, month - 1, day);
      sunday.setDate(sunday.getDate() - 1);
      const end = Number.isNaN(sunday.getTime()) ? null :
        sunday.toLocaleDateString(undefined, {
          month: "short", day: "numeric", year: "numeric",
        });
      return start && end ? `${start} – ${end}` : week.week_start;
    }

    function selectDivision(division, focusTab) {
      tabs.forEach((tab) => {
        const selected = tab.id === `past-winners-${division}-tab`;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focusTab) tab.focus();
      });
      Object.entries(panels).forEach(([key, panel]) => {
        if (!panel) return;
        const selected = key === division;
        panel.hidden = !selected;
        panel.classList.toggle("hidden", !selected);
      });
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", function () {
        selectDivision(tab.id.includes("verified") ? "verified" : "open", false);
      });
      tab.addEventListener("keydown", function (event) {
        let nextIndex = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          nextIndex = (index + 1) % tabs.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          nextIndex = (index - 1 + tabs.length) % tabs.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = tabs.length - 1;
        }
        if (nextIndex === null) return;
        event.preventDefault();
        selectDivision(
          tabs[nextIndex].id.includes("verified") ? "verified" : "open", true
        );
      });
    });

    function renderContest(division, contestKey, results) {
      const type = contestKey === "weekly-points-leaders"
        ? "points"
        : contestKey === "weekly-camp-points"
          ? "camp-points"
          : "instruments";
      const rowsEl = document.getElementById(`past-winners-${division}-${type}`);
      const noResultsEl = document.getElementById(
        `past-winners-${division}-${type}-empty`
      );
      if (!rowsEl || !noResultsEl) return;
      rowsEl.replaceChildren();
      const rows = results.filter((result) => {
        const medal = result && medals[result.rank];
        const contest = result && result.contest;
        const subject = result && (type === "instruments" ? result.instrument : result.display_name);
        return medal && result.medal === medal.key &&
          result.division === division && contest && contest.key === contestKey &&
          typeof subject === "string" && subject.trim() &&
          Number.isInteger(result.score) && result.score >= 0;
      });

      rows.forEach((result) => {
        const medal = medals[result.rank];
        const isStudent = type !== "instruments";
        const subject = isStudent ? result.display_name : result.instrument;
        const row = document.createElement("article");
        row.className = "medal-row";
        const icon = document.createElement("span");
        icon.className = "medal-row-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = medal.emoji;
        const subjectBlock = document.createElement("div");
        subjectBlock.className = "medal-row-subject";
        const name = document.createElement("strong");
        const teamName = !isStudent && window.WWInstruments
          ? window.WWInstruments.teamLabel(subject)
          : null;
        name.textContent = isStudent ? subject : `🎵 ${teamName || subject}`;
        const rank = document.createElement("small");
        rank.textContent = `${medal.label} · Rank ${result.rank}`;
        subjectBlock.append(name, rank);
        const score = document.createElement("span");
        score.className = "medal-row-score";
        score.textContent = type === "camp-points"
          ? `${result.score} Camp ${result.score === 1 ? "point" : "points"}`
          : `${result.score} min`;
        row.append(icon, subjectBlock, score);
        rowsEl.appendChild(row);
      });
      noResultsEl.classList.toggle("hidden", rows.length !== 0);
    }

    function renderResults(payload) {
      const results = payload && Array.isArray(payload.results) ? payload.results : [];
      ["open", "verified"].forEach((division) => {
        renderContest(division, "weekly-points-leaders", results);
        renderContest(division, "weekly-practice-by-instrument", results);
        renderContest(division, "weekly-camp-points", results);
      });
    }

    async function loadResults(weekStart) {
      showState(loadingEl);
      try {
        const response = await fetch(
          `/contests/weeks/${encodeURIComponent(weekStart)}/results`,
          { credentials: "same-origin", headers: { Accept: "application/json" } }
        );
        if (response.status === 401) return showState(authEl);
        if (!response.ok) return showState(errorEl);
        renderResults(await response.json());
        showState(contentEl);
      } catch (_error) {
        showState(errorEl);
      }
    }

    async function loadWeeks() {
      showState(loadingEl);
      try {
        const response = await fetch("/contests/weeks/finalized", {
          credentials: "same-origin", headers: { Accept: "application/json" },
        });
        if (response.status === 401) return showState(authEl);
        if (!response.ok) return showState(errorEl);
        const payload = await response.json();
        const weeks = payload && Array.isArray(payload.weeks)
          ? payload.weeks.filter((week) => week &&
              typeof week.week_start === "string" &&
              typeof week.week_end === "string" &&
              week.season && typeof week.season.name === "string")
          : [];
        if (!weeks.length) return showState(emptyEl);
        weekSelect.replaceChildren();
        weeks.forEach((week) => {
          const option = document.createElement("option");
          option.value = week.week_start;
          option.textContent = `${weekLabel(week)} · ${week.season.name}`;
          weekSelect.appendChild(option);
        });
        await loadResults(weeks[0].week_start);
      } catch (_error) {
        showState(errorEl);
      }
    }

    weekSelect.addEventListener("change", function () {
      loadResults(weekSelect.value);
    });
    retryButton.addEventListener("click", loadWeeks);
    selectDivision("open", false);
    loadWeeks();
  }

  function wireHallOfChampions() {
    const root = document.getElementById("hall-of-champions");
    if (!root) return;

    const loadingEl = document.getElementById("champions-loading");
    const authEl = document.getElementById("champions-auth");
    const emptyEl = document.getElementById("champions-empty");
    const errorEl = document.getElementById("champions-error");
    const contentEl = document.getElementById("champions-content");
    const retryButton = document.getElementById("champions-retry");
    const filterButtons = Array.from(
      root.querySelectorAll("[data-champions-division]")
    );
    const stateEls = [loadingEl, authEl, emptyEl, errorEl, contentEl];
    let champions = { students: [], instruments: [] };
    let division = "all";
    const expanded = { students: false, instruments: false };

    function showState(element) {
      stateEls.forEach((candidate) => {
        if (candidate) candidate.classList.toggle("hidden", candidate !== element);
      });
      root.setAttribute("aria-busy", String(element === loadingEl));
    }

    function countsFor(champion) {
      return division === "all"
        ? champion.medals
        : champion.by_division[division];
    }

    function championName(champion, type) {
      return type === "students"
        ? champion.display_name
        : (window.WWInstruments.teamLabel(champion.instrument_label) || champion.instrument_label);
    }

    function sortedChampions(type) {
      return champions[type]
        .filter((champion) => countsFor(champion).total > 0)
        .slice()
        .sort((left, right) => {
          const leftCounts = countsFor(left);
          const rightCounts = countsFor(right);
          return rightCounts.gold - leftCounts.gold ||
            rightCounts.silver - leftCounts.silver ||
            rightCounts.bronze - leftCounts.bronze ||
            championName(left, type).localeCompare(championName(right, type), undefined, {
              sensitivity: "base",
            });
        });
    }

    function medalStat(emoji, label, count) {
      const stat = document.createElement("span");
      const icon = document.createElement("span");
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = emoji;
      stat.append(icon, document.createTextNode(` ${label}: ${count}`));
      return stat;
    }

    function renderType(type) {
      const singular = type === "students" ? "student" : "instrument";
      const list = document.getElementById(`${singular}-champions-list`);
      const noResults = document.getElementById(`${singular}-champions-empty`);
      const showAll = document.getElementById(`${singular}-champions-show-all`);
      if (!list || !noResults || !showAll) return;
      list.replaceChildren();
      const ordered = sortedChampions(type);
      const visible = expanded[type] ? ordered : ordered.slice(0, 10);

      visible.forEach((champion) => {
        const counts = countsFor(champion);
        const card = document.createElement("article");
        card.className = "champion-card";
        const head = document.createElement("div");
        head.className = "champion-card-head";
        const name = document.createElement("strong");
        name.textContent = type === "students"
          ? champion.display_name
          : `${champion.instrument_icon} ${window.WWInstruments.teamLabel(champion.instrument_label) || champion.instrument_label}`;
        const total = document.createElement("span");
        total.className = "champion-podium-total";
        total.textContent = `${counts.total} podium${counts.total === 1 ? "" : "s"}`;
        head.append(name, total);

        const medalRow = document.createElement("div");
        medalRow.className = "champion-medals";
        medalRow.append(
          medalStat("🥇", "Gold", counts.gold),
          medalStat("🥈", "Silver", counts.silver),
          medalStat("🥉", "Bronze", counts.bronze)
        );
        card.append(head, medalRow);

        if (type === "students") {
          const crown = document.createElement("p");
          crown.className = "champion-crown";
          if (champion.crown.earned) {
            crown.classList.add("champion-crown-earned");
            crown.textContent = `👑 Permanent crown earned · ${champion.crown.qualifying_wins} qualifying wins`;
          } else {
            crown.textContent = `Crown progress: ${champion.crown.qualifying_wins} of ${champion.crown.target_wins} qualifying wins`;
          }
          card.appendChild(crown);
        }

        const represented = document.createElement("p");
        represented.className = "champion-divisions";
        represented.textContent = `Divisions represented: ${champion.divisions
          .map((value) => value === "open" ? "Open" : "Verified")
          .join(", ")}`;
        card.appendChild(represented);
        list.appendChild(card);
      });

      noResults.classList.toggle("hidden", ordered.length !== 0);
      showAll.classList.toggle("hidden", expanded[type] || ordered.length <= 10);
    }

    function render() {
      filterButtons.forEach((button) => {
        button.setAttribute(
          "aria-pressed",
          String(button.dataset.championsDivision === division)
        );
      });
      renderType("students");
      renderType("instruments");
    }

    function validCounts(value) {
      return value && ["gold", "silver", "bronze", "total"].every(
        (key) => Number.isInteger(value[key]) && value[key] >= 0
      );
    }

    function validChampion(champion, type) {
      const name = type === "students"
        ? champion && champion.display_name
        : champion && champion.instrument_label;
      return champion && typeof name === "string" && name.trim() &&
        validCounts(champion.medals) && champion.by_division &&
        validCounts(champion.by_division.open) &&
        validCounts(champion.by_division.verified) &&
        Array.isArray(champion.divisions) &&
        (type === "instruments" || (
          champion.crown &&
          Number.isInteger(champion.crown.qualifying_wins) &&
          champion.crown.qualifying_wins >= 0 &&
          champion.crown.target_wins === 10 &&
          typeof champion.crown.earned === "boolean"
        ));
    }

    async function loadChampions() {
      showState(loadingEl);
      try {
        const response = await fetch("/contests/hall-of-champions", {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (response.status === 401) return showState(authEl);
        if (!response.ok) return showState(errorEl);
        const payload = await response.json();
        champions = {
          students: payload && Array.isArray(payload.students)
            ? payload.students.filter((item) => validChampion(item, "students"))
            : [],
          instruments: payload && Array.isArray(payload.instruments)
            ? payload.instruments.filter((item) => validChampion(item, "instruments"))
            : [],
        };
        if (!champions.students.length && !champions.instruments.length) {
          return showState(emptyEl);
        }
        render();
        showState(contentEl);
      } catch (_error) {
        showState(errorEl);
      }
    }

    filterButtons.forEach((button) => {
      button.addEventListener("click", function () {
        division = button.dataset.championsDivision;
        expanded.students = false;
        expanded.instruments = false;
        render();
      });
    });
    ["students", "instruments"].forEach((type) => {
      const singular = type === "students" ? "student" : "instrument";
      document.getElementById(`${singular}-champions-show-all`).addEventListener(
        "click",
        function () {
          expanded[type] = true;
          renderType(type);
        }
      );
    });
    retryButton.addEventListener("click", loadChampions);
    loadChampions();
  }

  function wirePersonalCrownProgress() {
    const roots = Array.from(
      document.querySelectorAll(".personal-crown-progress")
    );
    if (!roots.length) return;

    function showError(message) {
      roots.forEach((root) => {
        root.querySelectorAll(".personal-crown-loading").forEach((element) => element.classList.add("hidden"));
        root.querySelectorAll(".personal-crown-content").forEach((element) => element.classList.add("hidden"));
        root.querySelectorAll(".personal-crown-error").forEach((element) => {
          element.textContent = message;
          element.classList.remove("hidden");
        });
        root.setAttribute("aria-busy", "false");
      });
    }

    function earnedDate(value) {
      if (typeof value !== "string") return null;
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return null;
      return parsed.toLocaleDateString(undefined, {
        month: "long", day: "numeric", year: "numeric",
      });
    }

    function render(progress) {
      roots.forEach((root) => {
        const status = root.querySelector(".personal-crown-status");
        const count = root.querySelector(".personal-crown-count strong");
        const meter = root.querySelector(".personal-crown-meter");
        const remaining = root.querySelector(".personal-crown-remaining");
        const date = root.querySelector(".personal-crown-earned-date");
        const categoryList = root.querySelector(".crown-category-list");
        const wins = progress.qualifying_wins;
        const target = progress.target_wins;

        if (count) count.textContent = `${wins} of ${target} wins`;
        if (meter) {
          meter.value = Math.min(wins, target);
          meter.textContent = `${wins} of ${target} qualifying wins`;
          meter.setAttribute("aria-label", `Crown progress: ${wins} of ${target} qualifying wins`);
        }
        if (progress.earned && status && remaining && date) {
          status.textContent = "👑 Permanent crown earned";
          status.classList.add("personal-crown-earned");
          remaining.textContent = "Permanent achievement — progress never resets.";
          const formattedDate = earnedDate(progress.earned_at);
          date.textContent = formattedDate ? `Earned ${formattedDate}` : "";
          date.classList.toggle("hidden", !formattedDate);
        } else if (status && remaining && date) {
          status.textContent = "Keep going toward your permanent crown.";
          status.classList.remove("personal-crown-earned");
          const winsRemaining = progress.remaining_wins;
          remaining.textContent = `${winsRemaining} qualifying ${winsRemaining === 1 ? "win" : "wins"} remain.`;
          date.textContent = "";
          date.classList.add("hidden");
        }
        if (categoryList && Array.isArray(progress.categories)) {
          categoryList.replaceChildren();
          progress.categories.forEach((category) => {
            const card = document.createElement("article");
            card.className = "crown-category-card";
            const name = document.createElement("strong");
            name.textContent = `${category.earned ? "👑 " : ""}${category.name}`;
            const value = document.createElement("span");
            value.textContent = `${category.progress} / ${category.target}`;
            const categoryEarnedDate = earnedDate(category.earned_at);
            const detail = document.createElement("small");
            detail.textContent = categoryEarnedDate ? `Earned ${categoryEarnedDate}` : "In progress";
            card.setAttribute(
              "aria-label",
              `${category.name}: ${category.progress} of ${category.target}${category.earned ? ", permanent crown earned" : ""}`
            );
            card.append(name, value, detail);
            categoryList.appendChild(card);
          });
        }
        root.querySelectorAll(".personal-crown-loading").forEach((element) => element.classList.add("hidden"));
        root.querySelectorAll(".personal-crown-error").forEach((element) => element.classList.add("hidden"));
        root.querySelectorAll(".personal-crown-content").forEach((element) => element.classList.remove("hidden"));
        root.setAttribute("aria-busy", "false");
      });
    }

    async function loadProgress() {
      try {
        const response = await fetch("/contests/crown-progress", {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        if (response.status === 401) {
          showError("Sign in to view your crown progress.");
          return;
        }
        if (!response.ok) {
          showError("Crown progress is unavailable right now.");
          return;
        }
        const payload = await response.json();
        if (!payload ||
            !Number.isInteger(payload.qualifying_wins) ||
            payload.qualifying_wins < 0 ||
            payload.target_wins !== 10 ||
            !Number.isInteger(payload.remaining_wins) ||
            payload.remaining_wins < 0 ||
            typeof payload.earned !== "boolean") {
          showError("Crown progress could not be read.");
          return;
        }
        if (!Array.isArray(payload.categories) || payload.categories.length !== 6 ||
            payload.categories.some((category) =>
              !category || typeof category.name !== "string" ||
              !Number.isInteger(category.progress) || category.progress < 0 ||
              category.target !== 10 || typeof category.earned !== "boolean"
            )) {
          showError("Crown categories could not be read.");
          return;
        }
        render(payload);
      } catch (_error) {
        showError("Crown progress is unavailable right now.");
      }
    }

    loadProgress();
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

  function wireShopPolish() {
    const dialog = document.getElementById("shop-feature-dialog");
    const closeButton = document.getElementById("shop-dialog-close");
    const title = document.getElementById("shop-dialog-title");
    const qrStatus = document.getElementById("shop-qr-status");
    const controls = Array.from(document.querySelectorAll("[data-shop-panel]"));
    if (!dialog || !closeButton || !title || !controls.length) return;
    const panels = Array.from(dialog.querySelectorAll("[data-shop-panel-content]"));
    const titles = {
      crown: "Crown Progress", goat: "The GOAT Tracker",
      "practice-definition": "Practice Definition", share: "Share Woodshed",
      clothing: "Clothing Shelf", gear: "Gear Shelf",
      "practice-room": "Practice Room", artist: "Artist",
    };
    let activator = null;

    async function copyPublicAddress(control) {
      const address = control.dataset.publicSiteUrl;
      if (!address || !qrStatus) return;
      try {
        if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(address);
        qrStatus.textContent = "Website address copied.";
      } catch (_error) {
        qrStatus.textContent = `The website address could not be copied. Use this link: ${address}`;
      }
    }

    controls.forEach((control) => {
      control.addEventListener("click", function () {
        const key = control.dataset.shopPanel;
        panels.forEach((panel) => { panel.hidden = panel.dataset.shopPanelContent !== key; });
        title.textContent = titles[key] || "Shop feature";
        activator = control;
        if (key === "share") {
          qrStatus.textContent = "";
          copyPublicAddress(control);
        }
        dialog.showModal();
        title.focus({ preventScroll: true });
      });
    });
    closeButton.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", function () {
      if (activator) activator.focus({ preventScroll: true });
    });
  }

  function wirePBook(state) {
    const form = document.getElementById("p-book-form");
    if (!form) return;

    const dateEl = document.getElementById("p-book-date");
    const minutesEl = document.getElementById("p-book-minutes");
    const noteEl = document.getElementById("p-book-note");
    const practiceDetailEls = Array.from(document.querySelectorAll("input[name='practice-detail']"));
    const timerDisplayEl = document.getElementById("practice-timer-display");
    const timerToggleBtn = document.getElementById("practice-timer-toggle-btn");
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
    const verifierSelectEl = document.getElementById("p-book-verifier");
    const includeContestsEl = document.getElementById("p-book-include-contests");
    const verifierHelpEl = document.getElementById("p-book-verifier-help");
    const submitBtn = form.querySelector("button[type='submit']");
    const openDialog = document.getElementById("open-chart-confirmation");
    const submitOpenBtn = document.getElementById("submit-open-chart");
    const chooseVerifierBtn = document.getElementById("choose-chart-verifier");
    const cancelOpenBtn = document.getElementById("cancel-open-chart");

    const weekPracticeEl = document.getElementById("p-book-week-practice");
    const careerPracticeEl = document.getElementById("p-book-career-practice");
    const practiceDaysEl = document.getElementById("p-book-practice-days");
    const pagesCountEl = document.getElementById("p-book-pages-count");

    const DANDELION_DAILY_CAP = 75;
    let submissionInFlight = false;
    let pendingSubmissionKey = null;
    let openSubmissionConfirmed = false;

    if (openDialog && submitOpenBtn && chooseVerifierBtn && cancelOpenBtn) {
      submitOpenBtn.addEventListener("click", function () {
        openSubmissionConfirmed = true;
        openDialog.close();
        form.requestSubmit();
      });
      chooseVerifierBtn.addEventListener("click", function () {
        openDialog.close();
        verifierSelectEl.focus();
      });
      cancelOpenBtn.addEventListener("click", function () {
        openDialog.close();
      });
    }

    function verifierRoleLabel(role) {
      return String(role || "")
        .replaceAll("_", " ")
        .replace(/\b\w/g, (character) =>
          character.toUpperCase()
        );
    }

    async function loadVerifierOptions() {
      if (!verifierSelectEl) return;

      verifierSelectEl.disabled = true;

      try {
        const response = await fetch(
          "/trusted-verifiers/invitations",
          {
            credentials: "same-origin",
          }
        );

        if (response.status === 401) {
          verifierSelectEl.replaceChildren(
            new Option(
              "Sign in to request verification",
              ""
            )
          );

          if (verifierHelpEl) {
            verifierHelpEl.textContent =
              "Sign in to your Woodchuck account to send a " +
              "P-Chart to a trusted verifier.";
          }

          return;
        }

        const payload = await response.json();

        if (!response.ok) {
          throw new Error(
            payload.detail ||
            "Trusted verifiers could not be loaded."
          );
        }

        const connections = Array.isArray(payload.connections)
          ? payload.connections.filter(
              (connection) =>
                connection.status === "accepted" &&
                connection.verifier
            )
          : [];

        verifierSelectEl.replaceChildren(
          new Option(
            "Do not request verification",
            ""
          )
        );

        connections.forEach((connection) => {
          const verifier = connection.verifier;
          const role = verifierRoleLabel(connection.role);

          verifierSelectEl.appendChild(
            new Option(
              `${verifier.display_name} — ${role}`,
              String(verifier.id)
            )
          );
        });

        verifierSelectEl.disabled = false;

        if (verifierHelpEl) {
          verifierHelpEl.textContent = connections.length
            ? (
                "Select a trusted verifier, or leave this Open."
              )
            : (
                "No trusted verifiers are connected yet. " +
                "This chart can still be saved as Open."
              );
        }
      } catch (error) {
        verifierSelectEl.replaceChildren(
          new Option(
            "Trusted verifiers unavailable",
            ""
          )
        );

        if (verifierHelpEl) {
          verifierHelpEl.textContent =
            error.message ||
            "Trusted verifiers could not be loaded.";
        }
      }
    }

    async function createPersistentPracticeChart({
      verifierId,
      dateKey,
      minutes,
      note,
      practiceDetails,
      creditsAwarded,
      submissionKey,
      includeContests,
    }) {
      const response = await fetch(
        "/practice-charts",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            verifier_id: verifierId,
            practice_date: dateKey,
            minutes,
            note,
            practice_details: practiceDetails,
            source: "p-book",
            credits_awarded: creditsAwarded,
            submission_key: submissionKey,
            include_contests: includeContests,
          }),
        }
      );

      const payload = await response.json();

      if (!response.ok) {
        throw new Error(
          payload.detail ||
          "The persistent P-Chart could not be created."
        );
      }

      return payload;
    }

    async function loadPersistentPracticeCharts() {
      try {
        const response = await fetch(
          "/practice-charts",
          {
            credentials: "same-origin",
            cache: "no-store",
          }
        );

        if (response.status === 401) {
          return;
        }

        const payload = await response.json();

        if (!response.ok) {
          throw new Error(
            payload.detail ||
            "Persistent P-Charts could not be loaded."
          );
        }

        const serverCharts = Array.isArray(payload.charts)
          ? payload.charts
          : [];

        const next = stateApi.getState();

        next.practiceLog = Array.isArray(next.practiceLog)
          ? next.practiceLog
          : [];

        const entriesByServerId = new Map(
          next.practiceLog
            .filter((entry) => entry && entry.serverChartId)
            .map((entry) => [
              String(entry.serverChartId),
              entry,
            ])
        );

        let changed = false;
        let verificationChanged = false;

        serverCharts.forEach((serverChart) => {
          const verification =
            serverChart.verification || {};

          const serverId = String(serverChart.id);
          const status = verification.status || "pending";
          const responseNote =
            verification.response_note || "";

          const verifierName =
            verification.verifier &&
            verification.verifier.display_name
              ? verification.verifier.display_name
              : "";

          const existing = entriesByServerId.get(serverId);

          if (existing) {
            if (existing.verificationStatus !== status) {
              existing.verificationStatus = status;
              verificationChanged = true;
              changed = true;
            }

            if (
              existing.verificationResponseNote !== responseNote
            ) {
              existing.verificationResponseNote = responseNote;
              changed = true;
            }

            if (existing.verifierName !== verifierName) {
              existing.verifierName = verifierName;
              changed = true;
            }

            return;
          }

          next.practiceLog.push({
            dateKey: serverChart.practice_date,
            minutes: serverChart.minutes,
            note: serverChart.note || "",
            practiceDetails: Array.isArray(
              serverChart.practice_details
            )
              ? serverChart.practice_details
              : [],
            source: serverChart.source || "p-book",
            creditsAwarded:
              Number(serverChart.credits_awarded) || 0,
            loggedAt:
              serverChart.created_at ||
              new Date().toISOString(),
            serverChartId: serverChart.id,
            verificationStatus: status,
            verificationResponseNote: responseNote,
            verifierId: verification.verifier_id || null,
            verifierName,
            includeContests: serverChart.include_contests !== false,
          });

          changed = true;
        });

        if (!changed) {
          return;
        }

        next.practiceLog.sort((left, right) =>
          String(right.loggedAt || "").localeCompare(
            String(left.loggedAt || "")
          )
        );

        next.practiceLog = next.practiceLog.slice(0, 100);

        stateApi.saveState(next, { sync: false });
        renderEntries(next);
        renderPBookSummary(next);

        if (verificationChanged && feedbackEl) {
          feedbackEl.classList.add("success-callout");
          feedbackEl.textContent =
            "Verification results were refreshed.";
        }
      } catch (error) {
        console.warn(
          "Could not refresh persistent P-Charts:",
          error
        );
      }
    }

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
    const PRACTICE_TIMER_LIMIT_SECONDS = 120 * 60;
    const PRACTICE_TIMER_STORAGE_KEY = "woodshedPracticeTimerStartedAt";

    function formatTimerSeconds(totalSeconds) {
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    function updatePracticeTimerDisplay() {
      if (!timerDisplayEl || !practiceTimerStartedAt) return;

      const elapsedSeconds = Math.min(
        PRACTICE_TIMER_LIMIT_SECONDS,
        Math.max(0, Math.floor((Date.now() - practiceTimerStartedAt) / 1000))
      );
      timerDisplayEl.textContent = formatTimerSeconds(elapsedSeconds);
      if (elapsedSeconds >= PRACTICE_TIMER_LIMIT_SECONDS) {
        stopPracticeTimerInterval();
        practiceTimerStartedAt = null;
        window.sessionStorage.removeItem(PRACTICE_TIMER_STORAGE_KEY);
        minutesEl.value = "120";
        if (timerFeedbackEl) timerFeedbackEl.textContent =
          "Timer stopped at 2 hours. You can adjust your minutes before submitting.";
        const toggle = document.getElementById("practice-timer-toggle-btn");
        if (toggle) {
          toggle.textContent = "Start Timer";
          toggle.classList.remove("btn-red");
          toggle.classList.add("btn-secondary");
        }
      }
    }

    function stopPracticeTimerInterval() {
      if (practiceTimerInterval) {
        window.clearInterval(practiceTimerInterval);
        practiceTimerInterval = null;
      }
    }

    function wirePracticeTimer() {
      if (!timerDisplayEl || !timerToggleBtn || !timerStartBtn || !timerStopBtn || !minutesEl) return;

      function renderTimerRunning(running) {
        timerToggleBtn.textContent = running ? "Stop Timer" : "Start Timer";
        timerToggleBtn.classList.toggle("btn-red", running);
        timerToggleBtn.classList.toggle("btn-secondary", !running);
      }

      timerStartBtn.addEventListener("click", function () {
        practiceTimerStartedAt = Date.now();
        window.sessionStorage.setItem(PRACTICE_TIMER_STORAGE_KEY, String(practiceTimerStartedAt));
        stopPracticeTimerInterval();
        timerDisplayEl.textContent = "00:00";
        practiceTimerInterval = window.setInterval(updatePracticeTimerDisplay, 1000);

        if (timerFeedbackEl) {
          timerFeedbackEl.textContent = "Timer started. Go make some music.";
        }
        renderTimerRunning(true);
      });

      timerStopBtn.addEventListener("click", function () {
        if (!practiceTimerStartedAt) {
          if (timerFeedbackEl) {
            timerFeedbackEl.textContent = "Start the timer first.";
          }
          return;
        }

        const elapsedSeconds = Math.min(PRACTICE_TIMER_LIMIT_SECONDS, Math.max(0, Math.floor((Date.now() - practiceTimerStartedAt) / 1000)));
        const elapsedMinutes = Math.max(1, Math.round(elapsedSeconds / 60));

        stopPracticeTimerInterval();
        timerDisplayEl.textContent = formatTimerSeconds(elapsedSeconds);
        practiceTimerStartedAt = null;
        window.sessionStorage.removeItem(PRACTICE_TIMER_STORAGE_KEY);
        renderTimerRunning(false);

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

      timerToggleBtn.addEventListener("click", function () {
        if (practiceTimerStartedAt) timerStopBtn.click();
        else timerStartBtn.click();
      });

      const restoredStart = Number(window.sessionStorage.getItem(PRACTICE_TIMER_STORAGE_KEY));
      if (Number.isFinite(restoredStart) && restoredStart > 0) {
        practiceTimerStartedAt = restoredStart;
        updatePracticeTimerDisplay();
        if (practiceTimerStartedAt) {
          practiceTimerInterval = window.setInterval(updatePracticeTimerDisplay, 1000);
          renderTimerRunning(true);
        }
      }
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
      const details = Array.isArray(entry.practiceDetails)
        ? entry.practiceDetails
        : [];
      const detailText = details.length
        ? ` — ${details.join(", ")}`
        : "";

      const verificationText =
        entry.verificationStatus === "pending"
          ? " — Verification pending"
          : entry.verificationStatus === "approved"
            ? " — Verified"
            : entry.verificationStatus === "rejected"
              ? " — Needs correction"
              : "";

      const verifierNoteText =
        entry.verificationResponseNote
          ? (
              ` — Verifier note: ` +
              entry.verificationResponseNote
            )
          : "";

      return (
        `${entry.dateKey} — ${entry.minutes} minutes` +
        `${detailText}${noteText}` +
        `${verificationText}${verifierNoteText}`
      );
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
      const practiceDays = new Set(entries.map((entry) => entry.dateKey).filter(Boolean)).size;
      const pagesCount = entries.length;

      if (practiceDaysEl) practiceDaysEl.textContent = String(practiceDays);
      if (pagesCountEl) pagesCountEl.textContent = String(pagesCount);
    }

    async function loadPracticeTotals() {
      if (!weekPracticeEl && !careerPracticeEl) return;
      try {
        const response = await fetch("/practice-charts/totals", {
          credentials: "same-origin", cache: "no-store",
        });
        if (!response.ok) return;
        const payload = await response.json();
        if (weekPracticeEl && typeof payload.this_week_display === "string") {
          weekPracticeEl.textContent = payload.this_week_display;
        }
        if (careerPracticeEl && typeof payload.career_display === "string") {
          careerPracticeEl.textContent = payload.career_display;
        }
      } catch (_error) {
        // Keep the compact zero-value placeholder while offline.
      }
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
    loadVerifierOptions();
    loadPersistentPracticeCharts();
    loadPracticeTotals();

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (submissionInFlight) return;
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

      if (
        !Number.isInteger(minutes) ||
        minutes < 1 ||
        minutes > 1440
      ) {
        errorEl.textContent =
          "Enter a whole number of minutes between 1 and 1440.";
        return;
      }

      const next = stateApi.getState();
      const dandelionsEarned = calculateDandelionsForPractice(
        minutes,
        practiceDetails,
        next.practiceLog || [],
        dateKey
      );

      const verifierId = verifierSelectEl
        ? Number(verifierSelectEl.value)
        : 0;

      const verifierName =
        verifierSelectEl &&
        verifierSelectEl.selectedOptions.length
          ? verifierSelectEl.selectedOptions[0].textContent
          : "";
      const includeContests = includeContestsEl ? includeContestsEl.checked : true;

      if (!verifierId && !openSubmissionConfirmed && openDialog) {
        if (!openDialog.open) openDialog.showModal();
        return;
      }

      submissionInFlight = true;
      if (submitBtn) {
        submitBtn.disabled = true;
      }

      try {
        pendingSubmissionKey = pendingSubmissionKey || (
          window.crypto && typeof window.crypto.randomUUID === "function"
            ? window.crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2)}`
        );
        const createdPayload = await createPersistentPracticeChart({
          verifierId: verifierId || null,
          dateKey,
          minutes,
          note,
          practiceDetails,
          creditsAwarded: dandelionsEarned,
          submissionKey: pendingSubmissionKey,
          includeContests,
        });
        const serverChart = createdPayload && createdPayload.chart;
        if (!serverChart || !Number.isInteger(serverChart.id)) {
          throw new Error("The saved P-Chart response could not be read.");
        }
        playNewCrownIfConfirmed(createdPayload);
        if (createdPayload.created === true) {
          celebrateSuccess(form);
          playSound("pChartSubmitted");
        }

        next.progress.credits =
          (next.progress.credits || 0) +
          dandelionsEarned;
        if (Number.isInteger(createdPayload.streak)) {
          next.progress.streak = createdPayload.streak;
        }

        stateApi.saveState(next);
        renderEntries(next);
        renderPBookSummary(next);
        await loadPersistentPracticeCharts();
        await loadPracticeTotals();

        feedbackEl.classList.add("success-callout");

        feedbackEl.textContent = verifierId
          ? (
              `A new page was added and sent to ` +
              `${verifierName}. Verification is pending. ` +
              `+${dandelionsEarned} dandelions added.`
            )
          : (
              `A new Open P-Chart was saved. ` +
              `+${dandelionsEarned} dandelions added.`
            );

        minutesEl.value = "";
        noteEl.value = "";

        practiceDetailEls.forEach((checkbox) => {
          checkbox.checked = false;
        });
        if (includeContestsEl) includeContestsEl.checked = true;

        pendingSubmissionKey = null;
        hydrateHome(next);
        window.dispatchEvent(new CustomEvent("ww:p-chart-saved"));
      } catch (error) {
        errorEl.textContent =
          error.message ||
          "The P-Chart could not be submitted.";
      } finally {
        openSubmissionConfirmed = false;
        submissionInFlight = false;
        if (submitBtn) {
          submitBtn.disabled = false;
        }
      }
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

        const mailtoQuery = params.toString().replace(/\+/g, "%20");
        const mailtoUrl = `mailto:${encodeURIComponent(teacherEmail)}?${mailtoQuery}`;
        window.location.href = mailtoUrl;
      });
    }
  }

  const state = ensureTodayQuest(stateApi.getState());

  if (!routeGuard(state)) return;

  wireSetupForm(state);
  hydrateHome(state);
  wireShedSecret();
  refreshPracticeStreak();
  wireMetronome();
  wireTuner();
  wireMum(state);
  wireQuestForm(state);
  wireBandCamp(state);
  wirePlungeBurrow();
  wireBandCampStandings();
  wirePastWinners();
  wireHallOfChampions();
  wirePersonalCrownProgress();
  wireStore(state);
  wireShopPolish();
  wirePBook(state);
})();
