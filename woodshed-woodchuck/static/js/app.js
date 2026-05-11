(function () {
  const stateApi = window.WWState;
  if (!stateApi) return;

  const state = stateApi.getState();

  function hasProfile(s) {
    return Boolean(s.profile.instrument && s.profile.level && s.profile.goal);
  }

  function routeGuard() {
    const path = window.location.pathname;
    if (["/home", "/quest", "/store"].includes(path) && !hasProfile(state)) {
      window.location.replace("/setup");
      return;
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
  }

  function wireSetupForm() {
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
      stateApi.saveState(next);
    });
  }

  function hydrateHome() {
    const creditsEl = document.getElementById("credits-value");
    const streakEl = document.getElementById("streak-value");
    const profileEl = document.getElementById("profile-value");
    if (!creditsEl || !streakEl || !profileEl) return;

    creditsEl.textContent = String(state.progress.credits ?? 0);
    streakEl.textContent = `${state.progress.streak ?? 0} days`;

    if (hasProfile(state)) {
      profileEl.textContent = `${state.profile.instrument} · ${state.profile.level}`;
    } else {
      profileEl.textContent = "Not set";
    }
  }

  routeGuard();
  wireSetupForm();
  hydrateHome();
})();
