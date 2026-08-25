(function (root) {
  "use strict";

  function parsePayload(response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      if (!response.ok) {
        throw new Error(payload.detail || "The Arcade could not start that game.");
      }
      return payload;
    });
  }

  function renderStatus(payload) {
    if (!payload || typeof payload !== "object") return;
    document.querySelectorAll("[data-arcade-balance]").forEach(function (output) {
      if (Number.isInteger(payload.balance)) output.textContent = String(payload.balance);
    });
    if (root.WWState && Number.isInteger(payload.balance)) {
      const state = root.WWState.getState();
      state.progress.credits = payload.balance;
      if (Number.isInteger(payload.state_revision)) {
        state.account.serverRevision = payload.state_revision;
      }
      root.WWState.saveState(state, { sync: false });
    }
    document.querySelectorAll("[data-arcade-economy-message]").forEach(function (message) {
      if (payload.reward_eligible === false) {
        message.textContent = "Daily prize plays complete — scores still count.";
      }
    });
  }

  function loadStatus(gameKey) {
    return fetch(`/arcade/plays/status/${gameKey}`, {
      credentials: "same-origin",
      cache: "no-store",
    }).then(parsePayload).then(function (payload) {
      renderStatus(payload);
      return payload;
    });
  }

  function startPlay(gameKey) {
    return fetch("/arcade/plays", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_key: gameKey }),
    }).then(parsePayload).then(function (payload) {
      renderStatus(payload);
      document.querySelectorAll("[data-arcade-economy-message]").forEach(function (message) {
        message.textContent = payload.reward_eligible === false
          ? "-1 🌼 · Daily prize plays complete — scores still count."
          : "-1 🌼";
      });
      return payload;
    });
  }

  function completePlay(playToken, score) {
    if (!playToken) return Promise.reject(new Error("Start a new game first."));
    return fetch(`/arcade/plays/${encodeURIComponent(playToken)}/complete`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score: Math.max(0, Math.round(score)) }),
    }).then(parsePayload).then(function (payload) {
      renderStatus(payload);
      document.querySelectorAll("[data-arcade-economy-message]").forEach(function (message) {
        if (payload.reward_eligible === false) {
          message.textContent = "Daily prize plays complete — scores still count.";
        } else if (payload.payout > 0) {
          message.textContent = `+${payload.payout} 🌼`;
        } else {
          message.textContent = "Run complete.";
        }
      });
      return payload;
    });
  }

  root.WoodshedArcadeEconomy = Object.freeze({
    completePlay,
    loadStatus,
    renderStatus,
    startPlay,
  });
}(typeof window !== "undefined" ? window : globalThis));
