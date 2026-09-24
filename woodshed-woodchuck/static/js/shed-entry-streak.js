/* Daily presentation marker only. XP/streak values always come from the server. */
(function () {
  'use strict';
  const account = document.body.dataset.worldEntryAccount;
  const control = document.getElementById('xp-level-control');
  const panel = document.getElementById('xp-panel');
  if (location.pathname !== '/home' || document.body.dataset.authenticated !== 'true' || !account || !control || !panel) return;
  const key = `woodshed:streak-popup:daily:v1:${account}`;
  const localDay = () => {
    const date = new Date();
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  };
  const current = () => !window.WWSessionBoundary || window.WWSessionBoundary.isCurrent();
  let ready = false;
  function shown() {
    if (!current()) return;
    try { window.localStorage.setItem(key, localDay()); } catch (_) { /* Manual access still works. */ }
  }
  // Manual L3 and automatic entry use the identical component and count as seen.
  document.addEventListener('woodshed:xp-opened', shown);
  function openIfDue() {
    if (!ready || document.hidden || !current() || document.querySelector('.app-shell')?.hidden ||
        document.body.classList.contains('world-entry-arrival') || document.querySelector('.world-entry-overlay[open]') ||
        document.body.classList.contains('stickerbook-open') || window.WWSurfaces?.current()) return;
    const day = localDay();
    try {
      if (window.localStorage.getItem(key) === day) return;
      // Reserve the presentation before opening. If storage is unavailable,
      // leave L3 available rather than repeatedly surprising users on navigation.
      window.localStorage.setItem(key, day);
    } catch (_) { return; }
    control.focus({preventScroll: true});
    control.click();
    if (panel.hidden) {
      try { window.localStorage.removeItem(key); } catch (_) {}
    }
  }
  function attempt() {
    if (!ready || !current()) return;
    if (navigator.locks?.request) {
      void navigator.locks.request(`woodshed-streak-presentation:${account}`, openIfDue).catch(() => {});
    } else openIfDue();
  }
  Promise.all([window.WWSessionBoundary?.ready, window.WWWorldEntry?.arrivalReady]).then(([verified]) => {
    if (verified === false || !current()) return;
    ready = true;
    attempt();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) attempt(); });
})();
