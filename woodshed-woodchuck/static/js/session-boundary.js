(function () {
  "use strict";
  // An origin-local change counter, not a visitor identifier. No value is sent
  // to the server. Keep it outside the discardable Guest preference namespace.
  const KEY = "woodshed:session-change:v1";
  const nativeFetch = window.fetch.bind(window);
  let accountId = document.body.dataset.accountId || "";
  const localGuest = document.body.dataset.guest === "local";
  let stopped = false;
  let changing = false;
  const scripts = document.getElementById("account-scripts");
  let verifying = Boolean(scripts);

  function readEpoch() {
    try {
      const value = window.localStorage.getItem(KEY) || "0";
      return /^\d+$/.test(value) ? value : "0";
    } catch (_error) { return null; }
  }
  let epoch = readEpoch();

  function stop() {
    if (stopped) return;
    stopped = true;
    window.dispatchEvent(new CustomEvent("ww:session-changed"));
    const shell = document.querySelector(".app-shell");
    if (shell) shell.hidden = true;
    const message = document.createElement("section");
    message.className = "card";
    message.setAttribute("role", "alert");
    message.textContent = "Sign-in changed in this browser. This tab has stopped to protect your saved work. ";
    const link = document.createElement("a");
    link.href = localGuest ? "/guest" : "/";
    link.textContent = "Reload to continue";
    message.appendChild(link);
    document.body.appendChild(message);
  }

  function isCurrent() {
    if (epoch === null || readEpoch() !== epoch) stop();
    return !stopped && !changing && !verifying;
  }
  function check(expected = epoch) {
    if (!isCurrent() || expected !== epoch) {
      throw new Error("Sign-in changed or is in progress. Reload before continuing.");
    }
  }
  function clearDrafts() {
    for (const key of ["woodshed:p-book:verifier-draft:v1", "woodshed:practice-timer-started-at"]) {
      try { window.sessionStorage.removeItem(key); } catch (_error) {}
    }
  }
  function guardedResponse(response, expected) {
    // Guard both the network await and the later body await used by real consumers.
    for (const method of ["json", "text"]) {
      const read = response[method].bind(response);
      response[method] = async function () {
        check(expected);
        const value = await read();
        check(expected);
        return value;
      };
    }
    return response;
  }

  async function authenticationFetch(input, init) {
    check();
    if (!navigator.locks?.request) {
      throw new Error("Safe sign-in changes require a secure browser with Web Locks support.");
    }
    const requestedEpoch = epoch;
    return navigator.locks.request("woodshed-account-transition", async function () {
      check(requestedEpoch);
      // Invalidate old tabs/responses BEFORE changing the shared session cookie.
      const next = String(BigInt(epoch) + 1n);
      window.localStorage.setItem(KEY, next);
      epoch = next;
      changing = true;
      window.dispatchEvent(new CustomEvent("ww:session-changed"));
      let response;
      try {
        response = await nativeFetch(input, init);
        const payload = response.ok ? await response.clone().json() : null;
        if (readEpoch() !== epoch || stopped) {
          stop();
          throw new Error("Sign-in changed. Reload before continuing.");
        }
        if (response.ok) {
          accountId = payload?.authenticated === false ? "" : payload?.profile?.woodchuck_id || accountId;
          clearDrafts();
        }
      } finally {
        // Also invalidate pages initialized while the request was pending. Do
        // this on failure/uncertainty too; a lost response may have changed the
        // cookie. The initiator adopts the completed epoch before reading body.
        changing = false;
        if (readEpoch() === epoch && !stopped) {
          try {
            const completed = String(BigInt(epoch) + 1n);
            window.localStorage.setItem(KEY, completed);
            epoch = completed;
          } catch (error) { stop(); throw error; }
        } else { stop(); }
      }
      check();
      return guardedResponse(response, epoch);
    });
  }

  window.fetch = async function (input, init = {}) {
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, window.location.href);
    const method = String(init.method || input.method || "GET").toUpperCase();
    if (url.origin !== window.location.origin || url.pathname.startsWith("/static/")) {
      return nativeFetch(input, init);
    }
    check();
    // Defense in depth; local Guest pages also have connect-src 'none'.
    if (localGuest) throw new Error("This tool is local. Sign in to use account features.");
    if (method === "POST" && ["/account/login", "/account/create", "/account/logout"].includes(url.pathname)) {
      return authenticationFetch(input, init);
    }
    const expected = epoch;
    const headers = new Headers(init.headers || input.headers);
    headers.set("X-Woodshed-Account", accountId || "-");
    const response = await nativeFetch(input, {...init, headers});
    check(expected);
    return guardedResponse(response, expected);
  };

  window.addEventListener("storage", function (event) {
    if (event.key === KEY || event.key === null) isCurrent();
    if (event.key === "woodshedWoodchuckState.v1" && accountId) {
      try {
        const state = JSON.parse(event.newValue);
        if (state?.account?.woodchuckId !== accountId || !state?.account?.authenticated) stop();
      } catch (_error) { stop(); }
    }
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) stop();
    else isCurrent();
  });
  async function startAccountPage() {
    if (!scripts) return true; // Local Guest pages make no preflight request.
    try {
      if (!accountId || !navigator.locks?.request) throw new Error("Session verification unavailable");
      // Share the transition lock: no login/logout can overtake this check or
      // its cookie response. A tab opened during logout waits, then fails shut.
      return await navigator.locks.request("woodshed-account-transition", {mode: "shared"}, async function () {
        if (readEpoch() !== epoch || stopped) throw new Error("Sign-in changed");
        const response = await nativeFetch("/account/me", {
          credentials: "same-origin", cache: "no-store",
          headers: {"X-Woodshed-Account": accountId},
        });
        const payload = response.ok ? await response.json() : null;
        if (readEpoch() !== epoch || stopped || !payload?.authenticated ||
            payload.profile?.woodchuck_id !== accountId ||
            !document.body.dataset.pageGeneration ||
            payload.page_generation !== document.body.dataset.pageGeneration) {
          throw new Error("Rendered account session is no longer current");
        }
        verifying = false;
        // Load real consumers in their original order only after verification.
        // Keep the shell hidden until every script is ready; JSON bootstrap is
        // outside the inert template but no state consumer ran before this gate.
        const loads = [];
        for (const original of scripts.content.querySelectorAll("script")) {
          check();
          const script = document.createElement("script");
          for (const attr of original.attributes) script.setAttribute(attr.name, attr.value);
          script.async = false;
          loads.push(new Promise((resolve, reject) => {
            script.onload = resolve;
            script.onerror = () => reject(new Error("Account script could not load"));
          }));
          document.body.appendChild(script);
        }
        await Promise.all(loads);
        check();
        const shell = document.querySelector(".app-shell");
        if (shell) shell.hidden = false;
        return true;
      });
    } catch (_error) { stop(); return false; }
  }
  const ready = startAccountPage();
  window.WWSessionBoundary = Object.freeze({
    isCurrent, ready, accountId: () => accountId, epoch: () => epoch,
  });
})();
