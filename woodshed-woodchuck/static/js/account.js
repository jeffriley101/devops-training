(function () {
  const stateApi = window.WWState;

  if (!stateApi) return;

  function validPin(pin) {
    return /^[0-9]{4}$/.test(pin);
  }

  async function responseMessage(response, fallback) {
    try {
      const payload = await response.json();

      if (typeof payload.detail === "string") {
        return payload.detail;
      }
    } catch (_error) {
      // Use fallback below.
    }

    return fallback;
  }

  function applyAccountProfile(state, profile) {
    state.account = {
      ...(state.account || {}),
      woodchuckId: profile.woodchuck_id,
      authenticated: true,
    };

    state.profile = {
      ...(state.profile || {}),
      woodchuckName: profile.display_name,
      instrument: profile.instrument,
      level: profile.level,
      goal: profile.goal,
      createdAt: profile.created_at,
    };

    return state;
  }

  async function uploadState(state) {
    const response = await fetch("/account/state", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state),
    });

    if (!response.ok) {
      throw new Error(
        await responseMessage(response, "The game could not be saved.")
      );
    }

    const payload = await response.json();
    const latestState = stateApi.getState();
    latestState.account = {
      ...(latestState.account || {}),
      serverRevision: Number.isInteger(payload.revision)
        ? payload.revision
        : latestState.account.serverRevision || 0,
      lastSyncedAt: payload.last_synced_at || new Date().toISOString(),
    };
    stateApi.saveState(latestState, { sync: false });
    return payload;
  }

  function prefillCreateForm(form) {
    const state = stateApi.getState();

    const nameEl = form.querySelector("#woodchuck-name");
    const instrumentEl = form.querySelector("#instrument");
    const levelEl = form.querySelector("#level");
    const goalEl = form.querySelector("#goal");

    if (nameEl && state.profile.woodchuckName) {
      nameEl.value = state.profile.woodchuckName;
    }

    if (instrumentEl && state.profile.instrument) {
      instrumentEl.value = state.profile.instrument;
    }

    if (levelEl && state.profile.level) {
      levelEl.value = state.profile.level;
    }

    if (goalEl && state.profile.goal) {
      goalEl.value = state.profile.goal;
    }
  }

  function wireCreateAccount() {
    const form = document.getElementById("account-create-form");

    if (!form) return;

    const errorEl = document.getElementById("setup-error");
    const successPanel = document.getElementById(
      "account-created-panel"
    );
    const accountIdEl = document.getElementById(
      "created-woodchuck-id"
    );
    const accountPinEl = document.getElementById("created-pin");
    const copyButton = document.getElementById(
      "copy-woodchuck-id"
    );
    const copyFeedback = document.getElementById(
      "copy-account-feedback"
    );

    prefillCreateForm(form);

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      errorEl.textContent = "";

      const formData = new FormData(form);
      const displayName = String(
        formData.get("display_name") || ""
      ).trim();
      const instrument = String(
        formData.get("instrument") || ""
      ).trim();
      const level = String(
        formData.get("level") || ""
      ).trim();
      const goal = String(
        formData.get("goal") || ""
      ).trim();
      const pin = String(formData.get("pin") || "").trim();

      if (!displayName) {
        errorEl.textContent = "Please name your Woodchuck.";
        return;
      }

      if (!instrument) {
        errorEl.textContent = "Please choose an instrument.";
        return;
      }

      if (!level) {
        errorEl.textContent = "Please choose a level.";
        return;
      }

      if (!goal) {
        errorEl.textContent = "Please choose a practice goal.";
        return;
      }

      if (!validPin(pin)) {
        errorEl.textContent =
          "Your PIN must contain exactly four digits.";
        return;
      }

      const submitButton = form.querySelector(
        "button[type='submit']"
      );

      submitButton.disabled = true;
      submitButton.textContent = "Creating Woodchuck...";

      try {
        formData.set(
          "initial_state",
          JSON.stringify(stateApi.getState())
        );
        const response = await fetch("/account/create", {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          throw new Error(
            await responseMessage(
              response,
              "The Woodchuck account could not be created."
            )
          );
        }

        const payload = await response.json();
        const profile = payload.profile;
        const state = payload.state;

        if (!state || typeof state !== "object") {
          throw new Error("The server did not return the new Woodshed.");
        }

        state.account = {
          ...(state.account || {}),
          serverRevision: Number.isInteger(payload.revision)
            ? payload.revision
            : 0,
        };
        stateApi.saveState(state, { sync: false });

        accountIdEl.textContent = profile.woodchuck_id;
        if (accountPinEl) {
          accountPinEl.textContent = payload.credentials.pin;
        }
        successPanel.hidden = false;
        form.hidden = true;

        successPanel.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      } catch (error) {
        errorEl.textContent =
          error.message || "The account could not be created.";

        submitButton.disabled = false;
        submitButton.textContent =
          "Create Persistent Woodchuck";
      }
    });

    if (copyButton) {
      copyButton.addEventListener("click", async function () {
        const accountId = accountIdEl.textContent.trim();

        if (!accountId) return;

        try {
          await navigator.clipboard.writeText(accountId);
          copyFeedback.textContent = "Woodchuck ID copied.";
        } catch (_error) {
          copyFeedback.textContent =
            `Write this down: ${accountId}`;
        }
      });
    }
  }

  function wireLogin() {
    const form = document.getElementById("account-login-form");

    if (!form) return;

    const errorEl = document.getElementById("login-error");

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      errorEl.textContent = "";

      const formData = new FormData(form);
      const woodchuckId = String(
        formData.get("woodchuck_id") || ""
      )
        .trim()
        .toUpperCase();
      const pin = String(formData.get("pin") || "").trim();

      if (!woodchuckId) {
        errorEl.textContent = "Enter your Woodchuck ID.";
        return;
      }

      if (!validPin(pin)) {
        errorEl.textContent =
          "Your PIN must contain exactly four digits.";
        return;
      }

      formData.set("woodchuck_id", woodchuckId);

      const submitButton = form.querySelector(
        "button[type='submit']"
      );

      submitButton.disabled = true;
      submitButton.textContent = "Restoring Woodshed...";

      try {
        const loginResponse = await fetch("/account/login", {
          method: "POST",
          body: formData,
        });

        if (!loginResponse.ok) {
          throw new Error(
            await responseMessage(
              loginResponse,
              "Woodchuck ID or PIN was not recognized."
            )
          );
        }

        const loginPayload = await loginResponse.json();
        const profile = loginPayload.profile;
        if (loginPayload.age_screen_required) {
          window.location.assign("/account/age");
          return;
        }

        const stateResponse = await fetch("/account/state", {cache: "no-store", credentials: "same-origin"});

        if (!stateResponse.ok) {
          throw new Error(
            await responseMessage(
              stateResponse,
              "The saved Woodshed could not be loaded."
            )
          );
        }

        const statePayload = await stateResponse.json();
        if (statePayload.state?.account?.woodchuckId &&
            statePayload.state.account.woodchuckId !== profile.woodchuck_id) {
          throw new Error("Sign-in changed. Reload before continuing.");
        }
        let restoredState;

        if (
          statePayload.state &&
          typeof statePayload.state === "object"
        ) {
          stateApi.saveState(statePayload.state, { sync: false });
          restoredState = stateApi.getState();
        } else {
          restoredState = stateApi.resetState();
        }

        applyAccountProfile(restoredState, profile);

        if (Number.isInteger(statePayload.revision)) {
          restoredState.account.serverRevision =
            statePayload.revision;
        }

        stateApi.saveState(restoredState, { sync: false });

        if (!statePayload.state) {
          await uploadState(restoredState);
        }

        window.location.assign("/home");
      } catch (error) {
        errorEl.textContent =
          error.message || "The Woodshed could not be restored.";

        submitButton.disabled = false;
        submitButton.textContent = "Sign In";
      }
    });
  }

  function wireProfileChange({ kind, endpoint, stateKey, payloadKey, triggerId }) {
    const openButton = document.getElementById(triggerId);
    const panel = document.getElementById(`change-${kind}-panel`);
    const form = document.getElementById(`change-${kind}-form`);
    const input = form && form.querySelector("input, select");
    const feedback = document.getElementById(`change-${kind}-feedback`);
    if (!openButton || !panel || !form || !input || !feedback) return;

    function close() {
      panel.hidden = true;
      panel.classList.add("hidden");
      openButton.setAttribute("aria-expanded", "false");
      openButton.focus();
    }
    openButton.addEventListener("click", function () {
      const currentValue = stateApi.getState().profile[stateKey] || "";
      if (input instanceof HTMLSelectElement && currentValue && !Array.from(input.options).some((option) => option.value === currentValue)) {
        input.prepend(new Option(`${currentValue} (current saved level)`, currentValue));
      }
      input.value = currentValue;
      feedback.textContent = "";
      feedback.classList.remove("error-text");
      panel.hidden = false;
      panel.classList.remove("hidden");
      openButton.setAttribute("aria-expanded", "true");
      input.focus();
    });
    panel.querySelectorAll("[data-close-profile-panel]").forEach((button) => {
      button.addEventListener("click", close);
    });
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const button = form.querySelector("button[type='submit']");
      feedback.classList.remove("error-text");
      feedback.textContent = "Saving…";
      button.disabled = true;
      try {
        const response = await fetch(endpoint, {
          method: "PATCH", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [payloadKey]: input.value.trim() }),
        });
        if (!response.ok) {
          throw new Error(await responseMessage(response, `The ${kind} could not be changed.`));
        }
        const payload = await response.json();
        const next = stateApi.getState();
        next.profile[stateKey] = payload[payloadKey];
        stateApi.saveState(next);
        feedback.textContent = `${kind === "name" ? "Name" : "Level"} changed successfully.`;
        window.WWSurfaces?.markSaved(panel);
        button.classList.add("is-confirmed-success");
        openButton.textContent = kind === "level"
          ? payload[payloadKey].charAt(0).toUpperCase()
          : payload[payloadKey];
        openButton.setAttribute(
          "aria-label",
          kind === "name"
            ? `Change Woodchuck name. Current name: ${payload[payloadKey]}`
            : `Level: ${payload[payloadKey]}. Change level.`
        );
        if (kind === "level") {
          openButton.title = `Level: ${payload[payloadKey]}. Change level.`;
        }
      } catch (error) {
        feedback.classList.add("error-text");
        feedback.textContent = error.message || `The ${kind} could not be changed.`;
      } finally {
        button.disabled = false;
      }
    });
  }

  wireCreateAccount();
  wireLogin();

  wireProfileChange({ kind: "name", endpoint: "/account/profile/name", stateKey: "woodchuckName", payloadKey: "display_name", triggerId: "woodchuck-name-value" });
  wireProfileChange({ kind: "level", endpoint: "/account/profile/level", stateKey: "level", payloadKey: "level", triggerId: "level-value" });
})();

(function () {
  const stateApi = window.WWState;

  if (!stateApi) return;

  let syncTimer = null;
  let syncInProgress = false;
  let pendingSync = false;

  function isPersistentAccount(state) {
    return Boolean(
      state &&
      state.account &&
      state.account.authenticated === true &&
      state.account.woodchuckId
    );
  }

  async function recoverFromConflict(localState, requestAccount) {
    const backupKey =
      `woodshedWoodchuckConflictBackup.${Date.now()}`;

    window.localStorage.setItem(
      backupKey,
      JSON.stringify(localState)
    );

    console.warn(
      "A stale Woodshed copy was backed up as:",
      backupKey
    );

    const response = await fetch("/account/state", {
      credentials: "same-origin",
      cache: "no-store",
    });

    if (!response.ok) {
      throw new Error(
        `Could not restore server state: ${response.status}`
      );
    }

    const payload = await response.json();

    const latest = stateApi.stateForResponse(requestAccount);
    if (!latest || payload.revision < latest.account.serverRevision) return;
    if (!payload.state || typeof payload.state !== "object") {
      throw new Error("The server did not return a saved Woodshed.");
    }
    if (payload.state.account?.woodchuckId !== latest.account.woodchuckId) return;

    stateApi.saveState(payload.state, { sync: false });

    const restoredState = stateApi.getState();

    restoredState.account = {
      ...(restoredState.account || {}),
      authenticated: true,
      serverRevision:
        Number.isInteger(payload.revision)
          ? payload.revision
          : restoredState.account.serverRevision || 0,
    };

    stateApi.saveState(restoredState, { sync: false });

    window.alert(
      "This Woodshed was updated in another browser. " +
      "The newest saved version will now load. " +
      "A backup of this browser's older copy was kept."
    );

    window.location.reload();
  }

  async function syncStateToServer() {
    const state = stateApi.getState();
    const requestAccount = stateApi.accountRequest();

    if (!isPersistentAccount(state)) return false;

    if (syncInProgress) {
      pendingSync = true;
      return false;
    }

    syncInProgress = true;
    pendingSync = false;

    try {
      const response = await fetch("/account/state", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "same-origin",
        body: JSON.stringify(state),
      });

      if (response.status === 401) {
        const latest = stateApi.stateForResponse(requestAccount);
        if (!latest) return false;
        latest.account.authenticated = false;
        stateApi.saveState(latest, { sync: false });
        return false;
      }

      if (response.status === 409) {
        pendingSync = false;
        if (!stateApi.stateForResponse(requestAccount)) return false;
        await recoverFromConflict(state, requestAccount);
        return false;
      }

      if (!response.ok) {
        throw new Error(`State sync failed with ${response.status}`);
      }

      const payload = await response.json();
      const latestState = stateApi.stateForResponse(requestAccount);
      if (!latestState) return false;
      const latestAccount =
        latestState.account &&
        typeof latestState.account === "object"
          ? latestState.account
          : {};

      if (!Number.isInteger(payload.revision) || payload.revision >= (latestAccount.serverRevision || 0)) {
        latestState.account = {
          ...latestAccount,
          serverRevision: Number.isInteger(payload.revision) ? payload.revision : latestAccount.serverRevision || 0,
          lastSyncedAt: payload.last_synced_at || new Date().toISOString(),
        };
        stateApi.applyEconomy(latestState, payload);
      }

      stateApi.saveState(latestState, { sync: false });
      return true;
    } catch (error) {
      console.warn("Woodshed account sync failed:", error);
      return false;
    } finally {
      syncInProgress = false;

      if (pendingSync) {
        pendingSync = false;
        scheduleSync(100);
      }
    }
  }

  function scheduleSync(delay = 500) {
    window.clearTimeout(syncTimer);

    syncTimer = window.setTimeout(function () {
      syncStateToServer();
    }, delay);
  }

  window.addEventListener("ww:state-saved", function (event) {
    const state =
      event.detail && event.detail.state
        ? event.detail.state
        : stateApi.getState();

    if (!isPersistentAccount(state)) return;

    scheduleSync();
  });

  window.WWAccountSync = {
    syncNow: syncStateToServer,
  };
})();
