(function () {
  "use strict";
  const PREFIX = "woodshed:guest:v1:";
  const KEY = PREFIX + "preferences";
  const form = document.getElementById("guest-setup-form");
  const feedback = document.getElementById("guest-feedback");
  const tools = document.getElementById("guest-tools");
  const logout = document.getElementById("guest-confirm-logout");

  if (logout) logout.addEventListener("click", async function () {
    logout.disabled = true;
    try {
      const response = await fetch("/account/logout", {method: "POST", credentials: "same-origin"});
      if (!response.ok || (await response.json()).authenticated !== false) throw new Error();
      // A fresh GET rechecks the actual session before exposing Guest tools.
      window.location.assign("/guest");
    } catch (_error) {
      feedback.textContent = "Sign out could not be confirmed. Guest tools remain closed; your saved account data was kept. Try again.";
      logout.disabled = false;
    }
  });

  if (!form) return;
  const fields = ["instrument", "level", "goal"];
  function allowed(field, value) {
    return Array.from(form.elements[field].options).some(option => option.value === value && value !== "");
  }
  function restore() {
    let saved = {};
    try { saved = JSON.parse(window.localStorage.getItem(KEY)) || {}; } catch (_error) {}
    for (const field of fields) form.elements[field].value = allowed(field, saved[field]) ? saved[field] : "";
    tools.hidden = !fields.every(field => allowed(field, form.elements[field].value));
  }
  function save(event) {
    if (event) event.preventDefault();
    if (!window.WWSessionBoundary.isCurrent() || !form.reportValidity()) return;
    const preferences = Object.fromEntries(fields.map(field => [field, form.elements[field].value]));
    if (!fields.every(field => allowed(field, preferences[field]))) return;
    try {
      window.localStorage.setItem(KEY, JSON.stringify(preferences));
      tools.hidden = false;
      feedback.textContent = "Guest preferences saved on this browser only. Nothing transfers when you sign in.";
    } catch (_error) {
      feedback.textContent = "Browser storage is unavailable. Enable local storage to explore as Guest.";
    }
  }
  form.addEventListener("submit", save);
  form.addEventListener("change", function () { if (!tools.hidden) save(); });
  document.getElementById("guest-discard").addEventListener("click", function () {
    if (!window.WWSessionBoundary.isCurrent()) return;
    // Exact dedicated namespace only. Account saves, drafts and audio preferences stay intact.
    for (const storage of [window.localStorage, window.sessionStorage]) {
      for (const key of Object.keys(storage)) if (key.startsWith(PREFIX)) storage.removeItem(key);
    }
    window.dispatchEvent(new CustomEvent("ww:guest-discarded"));
    form.reset();
    tools.hidden = true;
    feedback.textContent = "Guest data discarded. Saved account data was not changed.";
  });
  window.addEventListener("storage", function (event) {
    if (event.key?.startsWith(PREFIX) || event.key === null) {
      window.dispatchEvent(new CustomEvent("ww:guest-discarded"));
      restore();
    }
  });
  restore();
})();
