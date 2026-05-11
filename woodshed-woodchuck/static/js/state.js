(function () {
  const STORAGE_KEY = "woodshedWoodchuckState.v1";

  function localDateKey(date = new Date()) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function defaultState() {
    return {
      version: 2,
      profile: {
        instrument: "",
        level: "",
        goal: "",
        createdAt: null,
      },
      progress: {
        credits: 0,
        streak: 0,
        lastCompletedDate: null,
      },
      quest: {
        dateKey: localDateKey(),
        text: "",
        targetMinutes: 0,
        completed: false,
        rewardCredits: 0,
      },
      daily: {
        dateKey: localDateKey(),
        questId: "",
        questText: "",
        targetMinutes: 0,
        rewardCredits: 0,
        loggedMinutes: 0,
        completed: false,
        completedAt: null,
        encouragement: "",
      },
      inventory: {
        ownedItems: [],
        equipped: { head: null, body: null, feet: null },
      },
      practiceLog: [],
    };
  }

  function migrateProgress(progress = {}) {
    return {
      credits: typeof progress.credits === "number" ? progress.credits : 0,
      streak: typeof progress.streak === "number" ? progress.streak : 0,
      lastCompletedDate: progress.lastCompletedDate || progress.lastPracticeDate || null,
    };
  }

  function migrateToV2(parsed) {
    const base = defaultState();
    return {
      ...base,
      ...parsed,
      version: 2,
      profile: { ...base.profile, ...(parsed.profile || {}) },
      progress: migrateProgress(parsed.progress || {}),
      quest: { ...base.quest, ...(parsed.quest || {}) },
      daily: { ...base.daily, ...(parsed.daily || {}) },
      inventory: { ...base.inventory, ...(parsed.inventory || {}) },
      practiceLog: Array.isArray(parsed.practiceLog) ? parsed.practiceLog : [],
    };
  }

  function getState() {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      const fresh = defaultState();
      saveState(fresh);
      return fresh;
    }

    try {
      const parsed = JSON.parse(raw);
      if (!parsed.version || parsed.version === 1) {
        const migrated = migrateToV2(parsed);
        saveState(migrated);
        return migrated;
      }
      if (parsed.version !== 2) {
        const migrated = migrateToV2(parsed);
        saveState(migrated);
        return migrated;
      }
      return migrateToV2(parsed);
    } catch (_err) {
      const reset = defaultState();
      saveState(reset);
      return reset;
    }
  }

  function saveState(state) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function resetState() {
    const fresh = defaultState();
    saveState(fresh);
    return fresh;
  }

  window.WWState = {
    STORAGE_KEY,
    getState,
    saveState,
    resetState,
    localDateKey,
  };
})();
