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
    supportive: ["Keep going—you can do this."],
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
      return { id: "fallback-quest", text: "Practice one scale slowly with good tone.", target_minutes: 15, reward_credits: 20 };
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
    if (state.daily && state.daily.dateKey === today) return state;

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
    if (["/home", "/quest", "/store"].includes(path) && !hasProfile(state)) {
      window.location.replace("/setup");
      return false;
    }
    const continueLink = document.querySelector("[data-requires-profile='true']");
    if (continueLink && !hasProfile(state)) {
      continueLink.setAttribute("aria-disabled", "true");
      continueLink.classList.add("disabled");
      continueLink.addEventListener("click", (event) => {
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
    const instrumentEl = document.getElementById("instrument");
    const levelEl = document.getElementById("level");
    const goalEl = document.getElementById("goal");

    if (state.profile.instrument) instrumentEl.value = state.profile.instrument;
    if (state.profile.level) levelEl.value = state.profile.level;
    if (state.profile.goal) goalEl.value = state.profile.goal;

    form.addEventListener("submit", function (event) {
      const instrument = instrumentEl.value.trim();
      const level = levelEl.value.trim();
      const goal = goalEl.value.trim();
      if (!instrument || !level || !goal) {
        event.preventDefault();
        errorEl.textContent = "Please choose an instrument, level, and goal.";
        return;
      }
      const next = stateApi.getState();
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
    const profileEl = document.getElementById("profile-value");
    const questSummaryEl = document.getElementById("home-quest-summary");
    const questStatusEl = document.getElementById("home-quest-status");
    if (!creditsEl || !streakEl || !profileEl) return;

    creditsEl.textContent = String(state.progress.credits ?? 0);
    streakEl.textContent = `${state.progress.streak ?? 0} days`;
    profileEl.textContent = hasProfile(state) ? `${state.profile.instrument} · ${state.profile.level}` : "Not set";

    if (questSummaryEl && questStatusEl && state.daily) {
      questSummaryEl.textContent = `${state.daily.questText} (${state.daily.loggedMinutes || 0}/${state.daily.targetMinutes} min)`;
      questStatusEl.textContent = state.daily.completed ? "Complete ✅" : "Incomplete";
    }
  }

  function updateStreak(progress, today) {
    if (progress.lastCompletedDate === today) return;
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayKey = stateApi.localDateKey(yesterday);

    if (progress.lastCompletedDate === yesterdayKey) progress.streak += 1;
    else progress.streak = 1;

    progress.lastCompletedDate = today;
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
    if (!form || !questTextEl || !questTargetEl || !questStatusEl) return;

    const today = stateApi.localDateKey();
    if (state.daily.dateKey !== today) {
      ensureTodayQuest(state);
      stateApi.saveState(state);
    }

    function renderQuestStatus(s) {
      questTextEl.textContent = s.daily.questText;
      questTargetEl.textContent = String(s.daily.targetMinutes);
      if (questProgressEl) questProgressEl.textContent = `${s.daily.loggedMinutes || 0}/${s.daily.targetMinutes} minutes logged`;
      questStatusEl.textContent = s.daily.completed ? "Complete ✅" : "Not completed";
      if (s.daily.completed) completeBtn.disabled = true;
    }

    renderQuestStatus(state);
    if (state.daily.completed) {
      feedbackEl.querySelector("p:last-child").textContent = pickMessage("already_done", today);
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
        feedbackEl.querySelector("p:last-child").textContent = `${pickMessage("reward", dateKey)} +${next.daily.rewardCredits} credits earned.`;
      } else {
        feedbackEl.querySelector("p:last-child").textContent = `${pickMessage("supportive", dateKey)} (${next.daily.loggedMinutes}/${next.daily.targetMinutes} minutes)`;
      }

      next.daily.encouragement = feedbackEl.querySelector("p:last-child").textContent;
      stateApi.saveState(next);
      renderQuestStatus(next);
      hydrateHome(next);
    });
  }

  const state = ensureTodayQuest(stateApi.getState());
  stateApi.saveState(state);
  if (!routeGuard(state)) return;
  wireSetupForm(state);
  hydrateHome(state);
  wireQuestForm(state);
})();
