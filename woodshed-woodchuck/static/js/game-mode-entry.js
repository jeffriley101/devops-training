/* A per-login presentation reservation only. The server column is mode authority. */
(function () {
  'use strict';
  const dialog = document.getElementById('game-mode-entry');
  const stateApi = window.WWState;
  if (!dialog || !stateApi || !window.WWGameMode) return;
  const form = document.getElementById('game-mode-entry-form');
  const input = document.getElementById('game-mode-entry-select');
  const feedback = document.getElementById('game-mode-entry-feedback');
  const close = dialog.querySelector('[data-surface-close]');
  const modes = Array.from(input.options, option => option.value).filter(Boolean);
  const key = 'woodshed:game-mode-entry:session:v1';
  const initialize = window.WWGameMode.wire({form, panel: dialog, input,
    display: document.getElementById('game-mode-entry-current'), opener: null, feedback});
  window.WWSurfaces.register(dialog, () => dialog.close());
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    if (!window.WWSessionBoundary.isCurrent()) return;
    (document.getElementById('instrument-object') ||
      document.querySelector('#account-created-panel a[href="/home"]'))?.focus({preventScroll: true});
  });
  form.addEventListener('ww:game-mode-saved', () => { close.textContent = 'Continue'; });
  let finishEntry;
  const ready = new Promise(resolve => { finishEntry = resolve; });
  dialog.addEventListener('close', () => finishEntry(true));
  let pending = null, scheduled = false, attempting = false;
  const presented = new Set();
  const current = request => Boolean(stateApi.stateForResponse(request));

  async function attempt() {
    if (!pending || !current(pending.request)) return;
    if (stateApi.getState().profile.game_mode !== null) { pending = null; finishEntry(true); return; }
    if (document.hidden || document.querySelector('.app-shell')?.hidden ||
        window.WWSurfaces.current() || document.querySelector('dialog[open]') ||
        document.body.classList.contains('world-entry-arrival') ||
        document.body.classList.contains('stickerbook-open') ||
        Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]')).some(node =>
          node !== dialog && node.getClientRects().length && !node.closest('[hidden], .hidden'))) return;
    if (attempting) return;
    const entry = pending;
    function present() {
      if (pending !== entry || !current(entry.request)) return;
      if (document.hidden || window.WWSurfaces.current() || document.querySelector('dialog[open]')) return;
      pending = null;
      if (!entry.choice && presented.has(entry.marker)) { finishEntry(true); return; }
      try {
        if (!entry.choice && localStorage.getItem(key) === entry.marker) { finishEntry(true); return; }
        localStorage.setItem(key, entry.marker);
      } catch (_) {
        // Presentation caches are optional. Native account/session guards still
        // authorize every read/save; only cross-tab suppression is degraded.
      }
      presented.add(entry.marker);
      initialize();
      close.textContent = 'Choose later';
      if (entry.choice) input.value = entry.choice; // Only this explicit setup submission.
      dialog.showModal();
      if (entry.choice) form.requestSubmit();
    }
    attempting = true;
    try {
      if (navigator.locks?.request) {
        try { await navigator.locks.request('woodshed-game-mode-presentation', present); }
        catch (_) { present(); } // Presentation coordination is not authorization.
      } else { present(); }
    } finally { attempting = false; }

  }

  function schedule() {
    if (scheduled || !pending) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; void attempt().catch(() => { pending = null; }); });
  }
  new MutationObserver(schedule).observe(document.body, {subtree: true, childList: true,
    attributes: true, attributeFilter: ['hidden', 'class', 'open']});
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('ww:session-changed', () => {
    pending = null;
    presented.clear();
    if (dialog.open) dialog.close();
    feedback.textContent = '';
    form.reset();
  });

  async function start({choice = null, created = false} = {}) {
    try {
      await window.WWSessionBoundary.ready;
      if (!window.WWSessionBoundary.isCurrent()) {
        if (created) throw new Error('Reload or sign in to the account you already created, then choose Game Mode in your profile.');
        return;
      }
      const request = stateApi.accountRequest();
      const account = stateApi.getState().account;
      if (!account.authenticated || !account.woodchuckId) return;
      const response = await fetch('/account/me', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('Your account could not be confirmed. Choose Game Mode later in your profile.');
      const profile = await response.json();
      if (!current(request)) return;
      if (!profile.authenticated || profile.profile?.woodchuck_id !== account.woodchuckId ||
          !profile.page_generation || !Object.hasOwn(profile.profile, 'game_mode') ||
          (profile.profile.game_mode !== null && !modes.includes(profile.profile.game_mode))) {
        throw new Error('Reload before choosing Game Mode.');
      }
      if (!created && profile.page_generation !== document.body.dataset.pageGeneration) return;
      const stateResponse = await fetch('/account/state', {credentials: 'same-origin', cache: 'no-store'});
      if (!stateResponse.ok) throw new Error('Your saved Woodshed could not be confirmed. Choose Game Mode later in your profile.');
      const saved = await stateResponse.json();
      const next = stateApi.stateForResponse(request);
      if (!next) return;
      if (!Number.isInteger(saved.revision) || saved.revision < (next.account.serverRevision || 0) ||
          (saved.state?.account?.woodchuckId && saved.state.account.woodchuckId !== account.woodchuckId)) {
        throw new Error('Reload to confirm your saved Woodshed.');
      }
      const preferences = saved.state?._weekly_preferences;
      const mode = saved.state ? preferences?.game_mode : profile.profile.game_mode;
      if (mode !== null && !modes.includes(mode)) throw new Error('Reload to confirm Game Mode.');
      if (!stateApi.setServerGameMode(next, mode, saved.revision)) return;
      // This is a mode projection, not full state hydration. Keep the local
      // revision with its content. A newer server copy must go through the
      // existing 409 backup/recovery path before its revision can be adopted.
      // In particular, retain intervening local edits until that recovery.
      stateApi.saveState(next, {sync: false});
      await window.WWShedEntry?.ready;
      if (!current(request)) return;
      if (mode !== null) { finishEntry(true); return; }
      if (choice !== null && !modes.includes(choice)) throw new Error('Choose a supported Game Mode.');
      pending = {request, choice, marker: JSON.stringify([account.woodchuckId, profile.page_generation])};
      await window.WWWorldEntry?.arrivalReady;
      schedule();
    } catch (error) {
      finishEntry(false);
      // A failed request is never interpreted as a missing selection or logout.
      if (created && window.WWSessionBoundary.isCurrent()) {
        const notice = document.getElementById('copy-account-feedback');
        if (notice) notice.textContent = error.message;
      }
    }
  }
  window.WWGameModeEntry = Object.freeze({start, ready});
  if (dialog.dataset.auto === 'true') void start();
})();
