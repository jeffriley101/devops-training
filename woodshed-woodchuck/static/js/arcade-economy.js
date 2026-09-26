(function (root) {
  "use strict";

  const RETRY_DELAY_MS = 350;
  const FRIENDLY_NETWORK_MESSAGE = "Couldn't reach the Woodshed. Try again.";

  function safeEndpoint(endpoint) {
    const path = String(endpoint || "").split("?")[0];
    if (path === "/arcade/plays") return path;
    if (/^\/arcade\/plays\/[^/]+\/(complete|answer)$/.test(path)) {
      return path.replace(/\/plays\/[^/]+\//, "/plays/{play_token}/");
    }
    if (/^\/arcade\/(scores|plays\/status)\/[^/]+$/.test(path)) {
      return path.replace(/\/[^/]+$/, "/{game_key}");
    }
    return "<unmatched>";
  }

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
      root.console.warn("[woodshed-arcade-request]", {
        operation: details.operation,
        attempt: details.attempt,
        status: details.status,
        receivedResponse: details.receivedResponse,
      });
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
          endpoint: safeEndpoint(settings.endpoint),
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
          endpoint: safeEndpoint(settings.endpoint),
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
          endpoint: safeEndpoint(settings.endpoint),
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

  function renderStatus(payload, requestAccount) {
    if (!payload || typeof payload !== "object") return;
    let balance = payload.balance;
    if (root.WWState) {
      const state = requestAccount ? root.WWState.stateForResponse(requestAccount) : null;
      if (!state) return;
      root.WWState.applyEconomy(state, payload);
      root.WWState.saveState(state, { sync: false });
      balance = state.progress.credits;
    }
    document.querySelectorAll("[data-arcade-balance]").forEach(function (output) {
      if (Number.isInteger(balance)) output.textContent = String(balance);
    });
    document.querySelectorAll("[data-arcade-price]").forEach(function (output) {
      if (output.dataset.arcadePrice && output.dataset.arcadePrice !== payload.game_key) return;
      if (payload.free_reason !== undefined) output.textContent = payload.free_reason === "always_free"
        ? "Always free" : payload.free_reason === "full_access" ? "Free"
        : "100 Dandelions · 3 attempts";
    });
    document.querySelectorAll("[data-arcade-attempts]").forEach(function (output) {
      if (output.dataset.arcadeAttempts && output.dataset.arcadeAttempts !== payload.game_key) return;
      if (payload.free_reason === "always_free" || payload.free_reason === "full_access") output.textContent = "";
      else if (Number.isInteger(payload.attempts_remaining)) output.textContent = `${payload.attempts_remaining} purchased attempts remaining`;
    });
    document.querySelectorAll("[data-arcade-economy-message]").forEach(function (message) {
      if (payload.reward_eligible === false) {
        message.textContent = "Daily prize plays complete — scores still count.";
      }
    });
  }

  function loadStatus(gameKey) {
    const requestAccount = root.WWState?.accountRequest();
    return arcadeRequest({
      gameKey,
      operation: "status",
      endpoint: `/arcade/plays/status/${encodeURIComponent(gameKey)}`,
      fetchOptions: { credentials: "same-origin", cache: "no-store" },
    }).then(function (payload) {
      renderStatus(payload, requestAccount);
      return payload;
    });
  }

  function startPlay(gameKey) {
    const requestAccount = root.WWState?.accountRequest();
    const storageKey = `woodshed:arcade-start:${requestAccount?.identity || "account"}:${gameKey}`;
    let requestId = pendingStarts.get(storageKey);
    try { requestId = requestId || root.sessionStorage?.getItem(storageKey); } catch (_) {}
    requestId = requestId || (root.crypto?.randomUUID?.() || `start-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`);
    pendingStarts.set(storageKey, requestId);
    try { root.sessionStorage?.setItem(storageKey, requestId); } catch (_) {}
    return arcadeRequest({
      gameKey,
      operation: "start",
      endpoint: "/arcade/plays",
      fetchOptions: {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_key: gameKey, request_id: requestId }),
      },
    }).then(function (payload) {
      if (root.WWState && !root.WWState.stateForResponse(requestAccount)) return payload;
      pendingStarts.delete(storageKey);
      try { root.sessionStorage?.removeItem(storageKey); } catch (_) {}
      if (payload.already_completed || payload.attempt_closed) throw new Error("That attempt is no longer available. Select New Game for another attempt.");
      if (typeof payload.play_token === "string") {
        playTokens.set(payload.play_token, gameKey);
      }
      renderStatus(payload, requestAccount);
      document.querySelectorAll("[data-arcade-economy-message]").forEach(function (message) {
        message.textContent = payload.resumed ? "Resuming the same attempt. No extra charge."
          : payload.charged_now > 0 ? "100 Dandelions paid · 3 attempts included; this attempt has started."
          : "Attempt started. No charge.";
        if (payload.result_authority === "self_reported") message.textContent += " Practice play: no prizes or shared scores.";
        else if (payload.reward_eligible === false) message.textContent += " Daily prize plays complete — scores still count.";
      });
      return payload;
    });
  }

  function completePlay(playToken, score) {
    const requestAccount = root.WWState?.accountRequest();
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
      if (root.WWState && !root.WWState.stateForResponse(requestAccount)) return payload;
      playTokens.delete(playToken);
      renderStatus(payload, requestAccount);
      document.querySelectorAll("[data-arcade-economy-message]").forEach(function (message) {
        if (payload.result_authority === "self_reported") {
          message.textContent = "Practice run complete. No prizes or shared scores.";
        } else if (payload.reward_eligible === false) {
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

  function answerHistory(playToken, questionIndex, choice) {
    const requestAccount = root.WWState?.accountRequest();
    return arcadeRequest({
      gameKey: "history-mystery",
      operation: "history-answer",
      endpoint: `/arcade/plays/${encodeURIComponent(playToken)}/answer`,
      fetchOptions: {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_index: questionIndex, choice }),
      },
    }).then(function (payload) {
      renderStatus(payload, requestAccount);
      return payload;
    });
  }

  const playTokens = new Map();
  const pendingStarts = new Map();

  root.WoodshedArcadeEconomy = Object.freeze({
    ArcadeRequestError,
    arcadeRequest,
    answerHistory,
    completePlay,
    loadStatus,
    loadScores,
    renderStatus,
    startPlay,
  });
}(typeof window !== "undefined" ? window : globalThis));
