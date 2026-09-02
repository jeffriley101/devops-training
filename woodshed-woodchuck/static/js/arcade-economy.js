(function (root) {
  "use strict";

  const RETRY_DELAY_MS = 350;
  const FRIENDLY_NETWORK_MESSAGE = "Couldn't reach the Woodshed. Try again.";

  class ArcadeRequestError extends Error {
    constructor(message, details) {
      super(message);
      this.name = "ArcadeRequestError";
      Object.assign(this, details || {});
    }
  }

  function logRequestFailure(details) {
    // This is intentionally concise: it identifies the request that failed
    // without putting student, token, or session data in production logs.
    if (root.console && typeof root.console.warn === "function") {
      root.console.warn("[woodshed-arcade-request]", details);
    }
  }

  function wait(milliseconds) {
    return new Promise(function (resolve) { root.setTimeout(resolve, milliseconds); });
  }

  async function arcadeRequest(options) {
    const settings = options || {};
    const attempts = settings.retryNetworkOnce === false ? 1 : 2;
    const requestOptions = Object.assign({}, settings.fetchOptions || {});
    requestOptions.headers = Object.assign({}, requestOptions.headers || {}, {
      "X-Woodshed-Arcade-Game": settings.gameKey || "unknown",
    });
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      let response;
      try {
        response = await fetch(settings.endpoint, requestOptions);
      } catch (error) {
        const details = {
          gameKey: settings.gameKey,
          operation: settings.operation,
          endpoint: settings.endpoint,
          attempt,
          status: null,
          receivedResponse: false,
        };
        logRequestFailure(details);
        if (attempt < attempts) {
          await wait(RETRY_DELAY_MS);
          continue;
        }
        throw new ArcadeRequestError(FRIENDLY_NETWORK_MESSAGE, details);
      }

      let payload = {};
      let responseParseFailed = false;
      try { payload = await response.json(); } catch (_error) {
        responseParseFailed = true;
      }
      if (response.ok && responseParseFailed) {
        const details = {
          gameKey: settings.gameKey,
          operation: settings.operation,
          endpoint: settings.endpoint,
          attempt,
          status: response.status,
          receivedResponse: true,
          responseParseFailed: true,
        };
        logRequestFailure(details);
        if (attempt < attempts) {
          await wait(RETRY_DELAY_MS);
          continue;
        }
        throw new ArcadeRequestError(FRIENDLY_NETWORK_MESSAGE, details);
      }
      if (!response.ok) {
        const details = {
          gameKey: settings.gameKey,
          operation: settings.operation,
          endpoint: settings.endpoint,
          attempt,
          status: response.status,
          receivedResponse: true,
          serverCode: typeof payload.code === "string" ? payload.code : null,
        };
        logRequestFailure(details);
        throw new ArcadeRequestError(
          typeof payload.detail === "string" && payload.detail
            ? payload.detail
            : "The Arcade could not finish that request.",
          details
        );
      }
      return payload;
    }
    throw new ArcadeRequestError(FRIENDLY_NETWORK_MESSAGE);
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
    return arcadeRequest({
      gameKey,
      operation: "status",
      endpoint: `/arcade/plays/status/${encodeURIComponent(gameKey)}`,
      fetchOptions: { credentials: "same-origin", cache: "no-store" },
    }).then(function (payload) {
      renderStatus(payload);
      return payload;
    });
  }

  function startPlay(gameKey) {
    return arcadeRequest({
      gameKey,
      operation: "start",
      endpoint: "/arcade/plays",
      fetchOptions: {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_key: gameKey }),
      },
    }).then(function (payload) {
      if (typeof payload.play_token === "string") {
        playTokens.set(payload.play_token, gameKey);
      }
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
    const gameKey = playTokens.get(playToken) || "unknown";
    return arcadeRequest({
      gameKey,
      operation: "complete",
      endpoint: `/arcade/plays/${encodeURIComponent(playToken)}/complete`,
      fetchOptions: {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ score: Math.max(0, Math.round(score)) }),
      },
    }).then(function (payload) {
      playTokens.delete(playToken);
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

  function loadScores(gameKey) {
    return arcadeRequest({
      gameKey,
      operation: "leaderboard-read",
      endpoint: `/arcade/scores/${encodeURIComponent(gameKey)}`,
      fetchOptions: { credentials: "same-origin", cache: "no-store" },
    });
  }

  const playTokens = new Map();

  root.WoodshedArcadeEconomy = Object.freeze({
    ArcadeRequestError,
    arcadeRequest,
    completePlay,
    loadStatus,
    loadScores,
    renderStatus,
    startPlay,
  });
}(typeof window !== "undefined" ? window : globalThis));
