(function () {
  const STORAGE_KEY = "woodshedWoodchuckState.v1";

  function localDateKey(date = new Date()) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function defaultBandCampState() {
    return {
      seasonId: "band-camp-2026",
      daily: {
        dateKey: localDateKey(),
        hours: null,
        careComplete: false,
        triviaAttempted: false,
        triviaCorrect: false,
        marchingComplete: false,
        awarded: [],
      },
      totals: {
        points: 0,
        wins: {
          hours: 0,
          care: 0,
          trivia: 0,
          marching: 0,
        },
      },
      pastWinners: [],
      champions: [],
    };
  }

  function defaultState() {
    return {
      version: 4,
      account: {
        woodchuckId: "",
        authenticated: false,
        lastSyncedAt: null,
      },
      profile: {
        woodchuckName: "",
        instrument: "",
        level: "",
        goal: "",
        createdAt: null,
      },
      progress: {
        credits: 0,
        level: 1,
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
      bandCamp: defaultBandCampState(),
    };
  }

  function migrateAccount(account = {}) {
    return {
      woodchuckId:
        typeof account.woodchuckId === "string"
          ? account.woodchuckId
          : "",
      authenticated: account.authenticated === true,
      lastSyncedAt: account.lastSyncedAt || null,
    };
  }

  function migrateProgress(progress = {}) {
    return {
      credits: typeof progress.credits === "number" ? progress.credits : 0,
      level: typeof progress.level === "number" ? progress.level : 1,
      streak: typeof progress.streak === "number" ? progress.streak : 0,
      lastCompletedDate:
        progress.lastCompletedDate ||
        progress.lastPracticeDate ||
        null,
    };
  }

  function migrateBandCamp(bandCamp = {}) {
    const base = defaultBandCampState();
    const daily = bandCamp.daily || {};
    const totals = bandCamp.totals || {};
    const wins = totals.wins || {};

    return {
      ...base,
      ...bandCamp,
      daily: {
        ...base.daily,
        ...daily,
        awarded: Array.isArray(daily.awarded) ? daily.awarded : [],
      },
      totals: {
        ...base.totals,
        ...totals,
        points: typeof totals.points === "number" ? totals.points : 0,
        wins: {
          hours: typeof wins.hours === "number" ? wins.hours : 0,
          care: typeof wins.care === "number" ? wins.care : 0,
          trivia: typeof wins.trivia === "number" ? wins.trivia : 0,
          marching:
            typeof wins.marching === "number" ? wins.marching : 0,
        },
      },
      pastWinners: Array.isArray(bandCamp.pastWinners)
        ? bandCamp.pastWinners
        : [],
      champions: Array.isArray(bandCamp.champions)
        ? bandCamp.champions
        : [],
    };
  }

  function migrateToV4(parsed = {}) {
    const base = defaultState();

    return {
      ...base,
      ...parsed,
      version: 4,
      account: migrateAccount(parsed.account || {}),
      profile: { ...base.profile, ...(parsed.profile || {}) },
      progress: migrateProgress(parsed.progress || {}),
      quest: { ...base.quest, ...(parsed.quest || {}) },
      daily: { ...base.daily, ...(parsed.daily || {}) },
      inventory: { ...base.inventory, ...(parsed.inventory || {}) },
      practiceLog: Array.isArray(parsed.practiceLog)
        ? parsed.practiceLog
        : [],
      bandCamp: migrateBandCamp(parsed.bandCamp || {}),
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
      const migrated = migrateToV4(parsed);
      saveState(migrated);
      return migrated;
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
