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
    const profileEl = document.getElementById("profile-value");
    const questSummaryEl = document.getElementById("home-quest-summary");
    const questStatusEl = document.getElementById("home-quest-status");

    if (!creditsEl || !streakEl || !profileEl) return;

    creditsEl.textContent = String(state.progress.credits ?? 0);
    streakEl.textContent = `${state.progress.streak ?? 0} days`;
    if (woodchuckNameEl) woodchuckNameEl.textContent = state.profile.woodchuckName || "Not named yet";
    profileEl.textContent = hasProfile(state)
      ? `${state.profile.instrument} · ${state.profile.level}`
      : "Not set";

    if (questSummaryEl && questStatusEl && state.daily && state.daily.questText) {
      questSummaryEl.textContent = `${state.daily.questText} (${state.daily.loggedMinutes || 0}/${state.daily.targetMinutes} min)`;
      questStatusEl.textContent = state.daily.completed ? "Complete ✅" : "Incomplete";
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
            <small>${quest.target_minutes} minutes · ${quest.reward_credits} credits</small>
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
          `${pickMessage("reward", dateKey)} +${next.daily.rewardCredits} credits earned.`;
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
    return state;
  }

  function wireStore(state) {
    const creditsEl = document.getElementById("store-credits-value");
    const equippedHeadEl = document.getElementById("equipped-head-value");
    const equippedBodyEl = document.getElementById("equipped-body-value");
    const feedbackEl = document.getElementById("store-feedback");
    const itemsEl = document.getElementById("store-items");

    if (!creditsEl || !equippedHeadEl || !equippedBodyEl || !itemsEl) return;

    function renderStore(rawState) {
      const s = ensureInventoryShape(rawState);

      creditsEl.textContent = String(s.progress.credits ?? 0);
      equippedHeadEl.textContent = itemDisplayName(s.inventory.equipped.head);
      equippedBodyEl.textContent = itemDisplayName(s.inventory.equipped.body);

      itemsEl.innerHTML = STORE_ITEMS.map((item) => {
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
            <p>${item.price} credits · ${item.slot === "head" ? "Hat" : "Hoodie"}</p>
            <div class="store-item-actions">
              ${primaryAction}
            </div>
          </article>
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
          feedbackEl.textContent = `Not enough credits yet. ${item.name} costs ${item.price} credits.`;
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
    const errorEl = document.getElementById("p-book-error");
    const feedbackEl = document.getElementById("p-book-feedback");
    const entriesEl = document.getElementById("p-book-entries");
    const exportBtn = document.getElementById("export-p-chart-btn");
    const emailBtn = document.getElementById("email-p-chart-btn");
    const teacherEmailEl = document.getElementById("teacher-email");
    const parentEmailEl = document.getElementById("parent-email");

    const PAGE_CREDIT_REWARD = 5;

    function formatEntry(entry) {
      const noteText = entry.note ? ` — ${entry.note}` : "";
      return `${entry.dateKey} — ${entry.minutes} minutes${noteText}`;
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

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      errorEl.textContent = "";

      const dateKey = dateEl.value || stateApi.localDateKey();
      const minutes = Number(minutesEl.value);
      const note = noteEl.value.trim();

      if (!Number.isFinite(minutes) || minutes <= 0) {
        errorEl.textContent = "Enter a positive number of minutes.";
        return;
      }

      const next = stateApi.getState();

      next.practiceLog.unshift({
        dateKey,
        minutes,
        note,
        source: "p-book",
        creditsAwarded: PAGE_CREDIT_REWARD,
        loggedAt: new Date().toISOString(),
      });

      next.practiceLog = next.practiceLog.slice(0, 100);
      next.progress.credits = (next.progress.credits || 0) + PAGE_CREDIT_REWARD;

      stateApi.saveState(next);
      renderEntries(next);

      feedbackEl.textContent = `A new page was added to your P-Book. +${PAGE_CREDIT_REWARD} credits added to your bank.`;
      minutesEl.value = "";
      noteEl.value = "";
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
  wireStore(state);
  wirePBook(state);
})();
