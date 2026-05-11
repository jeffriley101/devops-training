(function () {
  const STORAGE_KEY = "woodshedWoodchuckState.v1";

  function todayKey() {
    return new Date().toISOString().slice(0, 10);
  }

  function defaultState() {
    return {
      version: 1,
      profile: {
        instrument: "",
        level: "",
        goal: "",
        createdAt: null,
      },
      progress: {
        credits: 0,
        streak: 0,
        lastPracticeDate: null,
      },
      quest: {
        dateKey: todayKey(),
        text: "",
        targetMinutes: 0,
        completed: false,
        rewardCredits: 0,
      },
      inventory: {
        ownedItems: [],
        equipped: { head: null, body: null, feet: null },
      },
      practiceLog: [],
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
      if (parsed.version !== 1) {
        const migrated = defaultState();
        saveState(migrated);
        return migrated;
      }
      return { ...defaultState(), ...parsed };
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
  };
})();
