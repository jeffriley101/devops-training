(function () {
  "use strict";
  const PREFIX = "woodshed:guest:v1:";
  const form = document.getElementById("guest-setup-form");
  const feedback = document.getElementById("guest-feedback");
  const tools = document.getElementById("guest-tools");
  const setupFields = document.getElementById("guest-setup-fields");
  const logout = document.getElementById("guest-confirm-logout");
  let preferences = null;
  let leaving = false;
  let ready = false;

  function clearObsoleteGuestStorage() {
    for (const name of ["localStorage", "sessionStorage"]) {
      try {
        const storage = window[name];
        for (const key of Object.keys(storage)) {
          if (key.startsWith(PREFIX)) storage.removeItem(key);
        }
      } catch (_error) {
        // The new tools use memory even when browser storage is unavailable.
      }
    }
  }
  clearObsoleteGuestStorage();

  if (logout) logout.addEventListener("click", async function () {
    logout.disabled = true;
    try {
      const response = await fetch("/account/logout", {method: "POST", credentials: "same-origin"});
      if (!response.ok || (await response.json()).authenticated !== false) throw new Error();
      window.WWSessionBoundary.clearAccountProductCaches();
      clearObsoleteGuestStorage();
      // A fresh GET rechecks the actual session before exposing Guest tools.
      window.location.assign("/guest");
    } catch (_error) {
      feedback.textContent = "Sign out could not be confirmed. Guest tools remain closed; your saved account data was kept. Try again.";
      logout.disabled = false;
    }
  });

  if (!form || !tools || !setupFields) return;
  const fields = ["instrument", "level", "goal"];
  const choices = Object.fromEntries(fields.map(field => [field,
    new Set(Array.from(form.elements[field].options).map(option => option.value).filter(Boolean))]));

  function fresh() {
    return !leaving && window.WWSessionBoundary?.isCurrent() === true;
  }
  function isCurrent() {
    return ready && preferences !== null && fresh() && !tools.hidden;
  }
  function reset(disable = false) {
    preferences = null;
    form.reset();
    tools.hidden = true;
    setupFields.disabled = disable || !ready;
    document.getElementById("shed-secret-form")?.reset();
    const secretPanel = document.getElementById("shed-secret-panel");
    if (secretPanel) {
      secretPanel.hidden = true;
      secretPanel.classList.add("hidden");
    }
    window.dispatchEvent(new CustomEvent("ww:guest-reset"));
    window.dispatchEvent(new CustomEvent("ww:guest-discarded"));
  }
  window.WWGuest = Object.freeze({isCurrent, reset});

  function explore(event) {
    event?.preventDefault();
    if (!ready || !fresh() || !form.reportValidity()) return;
    const selected = Object.fromEntries(fields.map(field => [field, form.elements[field].value]));
    if (!fields.every(field => choices[field].has(selected[field]))) return;
    preferences = selected;
    tools.hidden = false;
    feedback.textContent = "Guest tools are ready for this session. Reloading or leaving clears your activity.";
    window.dispatchEvent(new CustomEvent("ww:guest-setup", {detail: {instrument: selected.instrument}}));
  }
  form.addEventListener("submit", explore);
  form.addEventListener("change", function () {
    if (preferences !== null) explore();
  });
  document.getElementById("guest-discard").addEventListener("click", function () {
    if (!fresh()) return;
    reset();
    clearObsoleteGuestStorage();
    feedback.textContent = "Guest data discarded.";
    if (document.body.dataset.prebetaContext === "true" || document.body.dataset.c001Context === "true") {
      // Discard the signed source claim explicitly, without submitting tool data.
      const discard = document.createElement("form");
      discard.method = "post";
      discard.action = "/guest/discard";
      document.body.appendChild(discard);
      window.WWSessionBoundary.submitGuestForm(discard).catch(function () {
        feedback.textContent = "Registration context could not be discarded. Reload Guest tools and try again.";
      });
    }
  });
  for (const link of document.querySelectorAll("[data-guest-account-entry]")) {
    link.addEventListener("click", function (event) {
      if (!fresh()) event.preventDefault();
      else reset(true);
    });
  }
  window.addEventListener("pagehide", function () {
    leaving = true;
    reset(true);
  });
  window.addEventListener("ww:session-changed", function () {
    leaving = true;
    reset(true);
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) leaving = true;
    reset(leaving);
  });
  function start() {
    ready = Boolean(window.WWGuestToolsReady && window.WWTuner && window.WWGuestChart?.ready && window.WWGuestPlunge?.ready);
    reset();
    if (!ready) feedback.textContent = "Guest tools could not load. Reload to try again.";
  }
  if (document.readyState === "loading") window.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
