(function () {
  'use strict';
  let timer, navigationTimer, panel, operation = 0;
  const labels = {loading: 'Loading / connecting…', offline: 'Offline / no connection.',
    server: 'Server unavailable. Please try again.', session: 'Session expired. Sign in again.',
    feature: 'This feature could not load. Please try again.'};
  function clear(expected) {
    if (expected !== undefined && expected !== operation) return;
    operation++; clearTimeout(timer); clearTimeout(navigationTimer); panel?.remove(); panel = null;
  }
  function show(kind, retry, detail, expected) {
    if (expected !== undefined && expected !== operation) return;
    clearTimeout(timer); panel?.remove();
    panel = document.createElement('section'); panel.className = 'shared-recovery';
    panel.dataset.recovery = kind; panel.setAttribute('role', kind === 'loading' ? 'status' : 'alert');
    const text = document.createElement('p'); text.textContent = detail || labels[kind]; panel.append(text);
    if (retry) { const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Retry'; button.onclick = retry; panel.append(button); }
    if (kind === 'session') { const link = document.createElement('a'); link.href = '/login'; link.textContent = 'Sign in'; panel.append(link); }
    if (kind !== 'loading' && kind !== 'session') {
      const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.textContent = 'Dismiss';
      dismiss.onclick = () => clear(); panel.append(dismiss);
    }
    (Array.from(document.querySelectorAll('dialog[open]')).at(-1) || document.body).append(panel);
  }
  function begin(retry) { clear(); const expected = operation; timer = setTimeout(() => show('loading', retry, null, expected), 700); return expected; }
  function failure(error, retry, expected) {
    if (expected !== undefined && expected !== operation) return;
    const kind = navigator.onLine === false ? 'offline' : error?.status === 401 ? 'session' :
      error?.status >= 500 ? 'server' : error instanceof TypeError ? 'offline' : 'feature';
    clearTimeout(navigationTimer);
    show(kind, kind === 'session' ? null : retry, null, expected);
    return kind;
  }
  window.WWRecovery = Object.freeze({begin, clear, show, failure});
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.download || link.dataset.worldEntry) return;
    const url = new URL(link.href, location.href);
    // Later feature handlers may cancel navigation to protect an unsaved draft.
    queueMicrotask(() => {
      if (event.defaultPrevented || url.origin !== location.origin || url.pathname === location.pathname) return;
      const expected = begin();
      navigationTimer = setTimeout(() => show(navigator.onLine === false ? 'offline' : 'server',
        () => location.assign(url.href), null, expected), 12000);
    });
  });
  // The first pageshow may arrive while account preflight is still pending.
  window.addEventListener('pageshow', event => { if (event.persisted) clear(); });
  window.addEventListener('pagehide', () => clear());
  window.addEventListener('ww:session-changed', () => clear());
})();
