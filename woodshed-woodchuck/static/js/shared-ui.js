/* Named web operations shared by buttons, keyboard, history and future shells. */
(function () {
  "use strict";
  const cleanups = new Set();
  let generation = 0;
  function background() {
    generation++;
    cleanups.forEach(fn => { try { Promise.resolve(fn()).catch(() => {}); } catch (_) {} });
    document.querySelectorAll('audio,video').forEach(media => media.pause());
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) background(); });
  window.addEventListener('pagehide', background);
  window.addEventListener('ww:session-changed', background);
  window.WWLifecycle = Object.freeze({
    onBackground(fn) { cleanups.add(fn); return () => cleanups.delete(fn); },
    generation: () => generation,
    isForeground: () => !document.hidden,
  });

  const surfaces = new Map();
  let stack = [], invoker = null, bypass = false, historyPending = false;
  const visible = node => node.isConnected && !node.hidden && !node.classList.contains('hidden') &&
    (node.tagName !== 'DIALOG' || node.open);
  const fields = node => JSON.stringify(Array.from(node.querySelectorAll('input,select,textarea'))
    .map(el => [el.name || el.id, el.value, el.checked]));
  function allowed(entry) {
    if (entry.node.querySelector('form[aria-busy="true"]') || entry.node.dataset.busy === 'true' ||
        entry.node.querySelector('button[type="submit"]:disabled') ||
        (entry.node.id === 'shop-purchase-confirmation' && document.getElementById('shop-purchase-confirm')?.disabled)) return false;
    return !entry.node.querySelector('form') || entry.initial === fields(entry.node) || window.confirm('Discard unsaved changes?');
  }
  function dismissCurrent(fromHistory = false, approved = false) {
    const entry = stack.at(-1);
    if (!entry) return false;
    if (!approved && !allowed(entry)) return true;
    if (fromHistory) entry.marker = false;
    bypass = true;
    try { entry.close(); } finally { bypass = false; }
    sync();
    return true;
  }
  const roomPage = document.body.classList.contains('artwork-room-page');
  function roomInert(active) {
    if (!roomPage) return;
    document.querySelectorAll('.artwork-scene, .main-nav, #shed-secret-button, #shed-decorate-panel').forEach(node => {
      node.inert = active;
    });
  }
  function sync() {
    for (const entry of [...stack].reverse()) {
      if (!visible(entry.node)) {
        stack = stack.filter(item => item !== entry);
        roomInert(false);
        if (entry.opener?.isConnected) entry.opener.focus({preventScroll: true});
        if (entry.marker && history.state?.wwSurface === entry.node.id) {
          historyPending = true; history.back();
        }
        entry.marker = false;
      }
    }
    for (const entry of surfaces.values()) {
      if (!entry.node.isConnected) { surfaces.delete(entry.node); continue; }
      if (visible(entry.node) && !stack.includes(entry)) {
        entry.opener = invoker || document.activeElement;
        invoker = null;
        entry.initial = fields(entry.node);
        stack.push(entry);
        if (!historyPending) {
          history.pushState({...history.state, wwSurface: entry.node.id}, '', location.href);
          entry.marker = true;
        }
        entry.node.tabIndex = -1;
        const focus = Array.from(entry.node.querySelectorAll('[autofocus],input:not([type="hidden"]),select,textarea,button,a[href]'))
          .find(el => !el.disabled && el.getClientRects().length) || entry.node;
        focus.focus({preventScroll: true});
        if (entry.node.tagName !== 'DIALOG' && entry.node.getAttribute('aria-modal') !== 'true')
          entry.node.scrollIntoView({block: 'nearest', behavior: 'instant'});
      }
    }
    roomInert(stack.length > 0);
  }
  function register(node, close) {
    if (!node || surfaces.has(node)) return;
    if (roomPage && node.tagName !== 'DIALOG' && node.id !== 'digital-rain') {
      node.setAttribute('data-room-panel', '');
      node.setAttribute('role', 'dialog');
      node.setAttribute('aria-modal', 'true');
    }
    const entry = {node, close, marker: false};
    surfaces.set(node, entry);
    node.addEventListener('cancel', event => { event.preventDefault(); dismissCurrent(); });
  }
  document.addEventListener('click', event => {
    if (bypass) return;
    invoker = event.target.closest('button,a,[role="button"]') || document.activeElement;
    const top = stack.at(-1);
    if (!top) return;
    if (event.target === top.node && top.node.tagName === 'DIALOG') {
      const rect = top.node.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
        event.preventDefault(); event.stopImmediatePropagation(); dismissCurrent(); return;
      }
    }
    const closing = event.target.closest('[data-close-profile-panel], [data-surface-close], button[id$="-close"], button[id$="-close-button"], button[id$="-cancel"], button[id$="-cancel-button"], .digital-rain-stop');
    const link = event.target.closest('a[href]');
    const leaving = link && new URL(link.href, location.href).pathname !== location.pathname;
    if (((closing && top.node.contains(closing)) || invoker === top.opener || leaving) && !allowed(top)) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('keydown', event => {
    if (document.querySelector('.world-entry-overlay[open]')) return;
    const top = stack.at(-1)?.node;
    if (event.key === 'Tab' && top?.getAttribute('aria-modal') === 'true' && top.tagName !== 'DIALOG') {
      const controls = Array.from(top.querySelectorAll('button,input,select,textarea,a[href],[tabindex="0"]'))
        .filter(el => !el.disabled && el.getClientRects().length);
      const first = controls[0] || top, last = controls.at(-1) || top;
      if (event.shiftKey && (document.activeElement === first || !top.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !top.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
    if (event.key === 'Escape' && stack.length) {
      event.preventDefault(); event.stopImmediatePropagation(); dismissCurrent();
    }
  }, true);
  window.addEventListener('popstate', () => {
    if (historyPending) {
      historyPending = false;
      const pending = stack.at(-1);
      if (pending && !pending.marker) {
        history.pushState({...history.state, wwSurface: pending.node.id}, '', location.href);
        pending.marker = true;
      }
      return;
    }
    const top = stack.at(-1);
    if (!top) return;
    if (!allowed(top)) {
      history.pushState({...history.state, wwSurface: top.node.id}, '', location.href);
      return;
    }
    dismissCurrent(true, true);
  });
  function discover() {
    const pairs = {
      'xp-panel': 'xp-panel-close',
      'shed-secret-panel': 'shed-secret-close', 'metronome-panel': 'metronome-close-button',
      'tuner-panel': 'tuner-close-button', 'mum-panel': 'mum-close-button',
      'shop-feature-dialog': 'shop-dialog-close', 'shop-purchase-confirmation': 'shop-purchase-cancel',
      'change-name-panel': '[data-close-profile-panel]', 'change-level-panel': '[data-close-profile-panel]',
      'your-woodchuck': '[data-surface-close]',
      'p-book-missing-selection': 'p-book-missing-back',
      'p-book-final-confirmation': 'p-book-confirm-back',
      'shed-team-panel': 'shed-team-button', 'sound-effects-panel': 'sound-effects-button',
    };
    for (const [id, control] of Object.entries(pairs)) {
      const node = document.getElementById(id);
      register(node, () => (control.startsWith('[') ? node.querySelector(control) : document.getElementById(control))?.click());
    }
    const rain = document.querySelector('.digital-rain-overlay');
    if (rain) { rain.id ||= 'digital-rain'; register(rain, () => rain.querySelector('.digital-rain-stop').click()); }
    sync();
  }
  const observer = new MutationObserver(discover);
  observer.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'class', 'open']});
  discover();
  window.WWSurfaces = Object.freeze({register, dismissCurrent,
    markSaved(node) { const entry = surfaces.get(node); if (entry) entry.initial = fields(node); },
    current: () => stack.at(-1)?.node || null,
  });
  window.WWNavigation = Object.freeze({dismissCurrent: () => dismissCurrent()});
})();
