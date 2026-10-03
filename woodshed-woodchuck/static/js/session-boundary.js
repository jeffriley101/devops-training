(function () {
  "use strict";
  // An origin-local change counter, not a visitor identifier. No value is sent
  // to the server. Keep it outside the discardable Guest preference namespace.
  const KEY = "woodshed:session-change:v1";
  const nativeFetch = window.fetch.bind(window);
  let accountId = document.body.dataset.accountId || "";
  let pageGeneration = document.body.dataset.pageGeneration || "";
  const localGuest = document.body.dataset.guest === "local";
  let stopped = false;
  let changing = false;
  let guestPageLeaving = false;
  let guestFormPending = false;
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
    clearDrafts();
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
    // Pure local tools can work without browser storage. This does not enable
    // authentication or account requests, which remain prohibited below.
    if (localGuest && epoch === null) return !stopped && !changing && !verifying;
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
  function removeAccountProductCaches() {
    // Exact product keys only; the session epoch and security cookies survive.
    for (const key of ["woodshedWoodchuckState.v1", "woodshed.plungeBurrow.bestScore", "woodshedWoodchuckMetronomeBpm"]) {
      try { window.localStorage.removeItem(key); } catch (_error) {}
    }
    try {
      for (const key of Object.keys(window.localStorage)) {
        if (key.startsWith("woodshedWoodchuckConflictBackup.")) window.localStorage.removeItem(key);
      }
    } catch (_error) {}
    clearDrafts();
    try {
      for (const key of Object.keys(window.sessionStorage)) {
        if (key.startsWith("woodshed:arcade-start:") ||
            ["woodshed:world-entry-arrival:v1", "woodshed:arcade-entry:v1"].includes(key)) {
          window.sessionStorage.removeItem(key);
        }
      }
    } catch (_error) {}
  }
  function clearAccountProductCaches() {
    check();
    removeAccountProductCaches();
  }
  function binding() {
    if (!isCurrent() || !accountId || !pageGeneration) return null;
    return {accountId, pageGeneration, epoch};
  }
  function matchesBinding(saved) {
    const current = binding();
    return Boolean(current && saved && saved.accountId === current.accountId &&
      saved.pageGeneration === current.pageGeneration && saved.epoch === current.epoch);
  }
  function checkGuestForm(form) {
    const url = new URL(form?.action || "", window.location.href);
    if (!localGuest || guestPageLeaving || document.hidden || epoch === null ||
        String(form?.method || "").toUpperCase() !== "POST" ||
        url.origin !== window.location.origin || url.search || url.hash ||
        !["/guest/discard", "/guest/secret-symbol"].includes(url.pathname) ||
        !["", "_self"].includes(form.target || "") || typeof form.submit !== "function") {
      throw new Error("Reload Guest tools before submitting registration context.");
    }
  }
  async function submitGuestForm(form) {
    check();
    checkGuestForm(form);
    if (!navigator.locks?.request) {
      throw new Error("Safe registration context changes require a secure browser with Web Locks support.");
    }
    if (guestFormPending) throw new Error("Registration context is already being submitted.");
    const requestedEpoch = epoch;
    guestFormPending = true;
    try {
      // Keep native cookie-changing Guest navigation ordered with account
      // transitions. The response cookie is applied before this page unloads.
      return await navigator.locks.request("woodshed-account-transition", function () {
        check(requestedEpoch);
        checkGuestForm(form);
        return new Promise((resolve, reject) => {
          const release = () => resolve();
          window.addEventListener("pagehide", release, {once: true});
          try { form.submit(); }
          catch (error) {
            window.removeEventListener("pagehide", release);
            reject(error);
          }
        });
      });
    } finally { guestFormPending = false; }
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
          pageGeneration = ""; // The next rendered account page supplies the new server marker.
          clearDrafts();
          if (payload?.authenticated === true || payload?.profile?.woodchuck_id) removeAccountProductCaches();
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
    if (event.key === KEY || event.key === null) {
      if (localGuest && epoch === null) stop();
      else isCurrent();
    }
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
  window.addEventListener("pagehide", function () {
    if (localGuest) guestPageLeaving = true;
  });
  async function startAccountPage() {
    if (!scripts) return true; // Local Guest pages make no preflight request.
    window.WWRecovery?.begin(() => window.location.reload());
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
        if (!response.ok) throw Object.assign(new Error("Session verification failed"), {status: response.status});
        const payload = await response.json();
        if (readEpoch() !== epoch || stopped || !payload?.authenticated ||
            payload.profile?.woodchuck_id !== accountId ||
            !document.body.dataset.pageGeneration ||
            payload.page_generation !== document.body.dataset.pageGeneration) {
          throw Object.assign(new Error("Rendered account session is no longer current"), {status: 401});
        }
        verifying = false;
        const shell = document.querySelector(".app-shell");
        if (shell) shell.hidden = false;
        // Load real consumers in their original order only after verification.
        // JSON bootstrap is outside the inert template, but no state consumer
        // ran before this gate. Opening the shell lets layout consumers measure it.
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
        window.WWRecovery?.clear();
        return true;
      });
    } catch (error) {
      if (!window.WWRecovery) { stop(); return false; }
      // Never unlock consumers after a failed preflight or script load. Network
      // failures do not revoke the session or claim that the user logged out.
      if (readEpoch() !== epoch || stopped) { stop(); return false; }
      verifying = true;
      const shell = document.querySelector(".app-shell");
      if (shell) shell.hidden = true;
      window.WWRecovery.failure(error, () => window.location.reload());
      return false;
    }
  }
  const ready = startAccountPage();
  window.WWSessionBoundary = Object.freeze({
    isCurrent, ready, accountId: () => accountId, epoch: () => epoch,
    binding, matchesBinding, clearAccountProductCaches, submitGuestForm,
  });
})();
