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
      createdAt:
        state.profile && state.profile.createdAt
          ? state.profile.createdAt
          : new Date().toISOString(),
    };

    return state;
  }

  async function uploadState(state) {
    const response = await fetch("/account/state", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(state),
    });

    if (!response.ok) {
      throw new Error(
        await responseMessage(
          response,
          "The account was created, but the game could not be saved."
        )
      );
    }

    const payload = await response.json();

    state.account.lastSyncedAt =
      payload.last_synced_at || new Date().toISOString();

    stateApi.saveState(state);

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

      if (!instrument || !level || !goal) {
        errorEl.textContent =
          "Please choose an instrument, level, and goal.";
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

        const state = applyAccountProfile(
          stateApi.getState(),
          profile
        );

        stateApi.saveState(state);
        await uploadState(state);

        accountIdEl.textContent = profile.woodchuck_id;
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

        const stateResponse = await fetch("/account/state");

        if (!stateResponse.ok) {
          throw new Error(
            await responseMessage(
              stateResponse,
              "The saved Woodshed could not be loaded."
            )
          );
        }

        const statePayload = await stateResponse.json();
        let restoredState;

        if (
          statePayload.state &&
          typeof statePayload.state === "object"
        ) {
          stateApi.saveState(statePayload.state);
          restoredState = stateApi.getState();
        } else {
          restoredState = stateApi.resetState();
        }

        applyAccountProfile(restoredState, profile);
        stateApi.saveState(restoredState);

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

  wireCreateAccount();
  wireLogin();
})();
