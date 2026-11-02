/* Explicit Weekly Loop actions. Page reads never issue work or claim rewards. */
(function () {
  'use strict';
  const account = document.body.dataset.studentAccount;
  const privateArea = document.querySelector('[data-student-private]');
  const status = document.getElementById('homework-action-status');
  let busy = false;
  let departed = false;
  function clear() {
    departed = true;
    if (privateArea) privateArea.replaceChildren();
  }
  async function submit(event, path, body) {
    event.preventDefault();
    if (busy || departed || !account) return;
    busy = true;
    document.querySelectorAll('#student-homework button').forEach(button => { button.disabled = true; });
    status.textContent = 'Saving your choice…';
    try {
      const response = await fetch(path, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'x-woodshed-account': account},
        body: JSON.stringify(body)
      });
      if (departed) return;
      if (response.status === 401 || response.status === 403) {
        clear(); location.reload(); return;
      }
      if (!response.ok) {
        status.textContent = 'Your choice could not be saved. Refresh status before trying again.';
        return;
      }
      location.reload();
    } catch (_) {
      if (!departed) status.textContent = 'The connection was interrupted. Refresh status to check whether your choice was saved.';
    } finally {
      busy = false;
      // A failed/lost response may have committed: refresh before retrying.
    }
  }
  const focus = document.querySelector('[data-homework-focus]');
  if (focus) focus.addEventListener('submit', event => submit(event, '/weekly/plan', {focus: focus.elements.focus.value}));
  const start = document.querySelector('[data-homework-start]');
  if (start) start.addEventListener('submit', event => submit(event, '/weekly/plan/instruments/current/start', {start_context: start.elements.start_context.value}));
  window.addEventListener('pagehide', clear);
  window.addEventListener('pageshow', event => { if (event.persisted) { clear(); location.reload(); } });
  // Refresh permissions and frozen work after another tab changes the account.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) clear();
    else if (departed) location.reload();
  });
})();
