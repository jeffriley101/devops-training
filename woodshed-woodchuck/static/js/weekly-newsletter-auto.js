/* One server-reserved attempt. No clock, product cache, reward or manual-read writer. */
(function () {
  'use strict';
  function create({current, idle, read, reserve, open, reschedule = () => {}}) {
    let busy = false, stopped = false, attempted = false, epoch = 0;
    return Object.freeze({
      invalidate() { epoch++; },
      stop() { stopped = true; epoch++; },
      async attempt() {
        if (stopped || busy || attempted || !current() || !idle()) return;
        const ticket = epoch;
        const valid = () => !stopped && ticket === epoch && current() && idle();
        busy = true;
        try {
          const payload = await read();
          if (!valid() || payload.context?.available !== true) return;
          const week = payload.context.week_start;
          if (typeof week !== 'string' || typeof payload.context.publication_at !== 'string') return;
          // An unknown write outcome consumes this document's attempt, too.
          // Later entries ask the server, which never reserves the edition twice.
          attempted = true;
          const result = await reserve(week);
          if (!valid() || result.status !== 'reserved' || result.context?.available !== true ||
              result.context.week_start !== week) return;
          open(week);
        } catch (_) {
          // Fail closed. Manual Mum access and later eligible entries survive.
        } finally {
          busy = false;
          // Preserve an observed idle transition that arrived while the read
          // was in flight. Never retry a reservation or poll the clock.
          if (!stopped && !attempted && epoch !== ticket && current() && idle()) reschedule();
        }
      },
    });
  }
  window.WWNewsletterAuto = Object.freeze({create});
  if (location.pathname !== '/home' || !document.getElementById('weekly-newsletter-open')) return;
  const account = window.WWState.accountRequest();
  const current = () => window.WWSessionBoundary?.isCurrent() === true && !!window.WWState.stateForResponse(account);
  const idle = () => !document.hidden && !document.querySelector('.app-shell')?.hidden &&
    !window.WWSurfaces.current() && !document.querySelector('dialog[open]') &&
    !document.querySelector('.metronome-stop-button, #practice-timer-toggle-btn.btn-red') &&
    !document.body.classList.contains('world-entry-arrival') && !document.body.classList.contains('world-entry-flight') &&
    !document.body.classList.contains('stickerbook-open') &&
    !Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]')).some(el =>
      el.getClientRects().length && !el.closest('[hidden], .hidden'));
  async function request(path, body) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store',
      ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})});
    if (!response.ok) throw new Error('Newsletter unavailable');
    return response.json();
  }
  const controller = create({current, idle, read: () => request('/weekly/newsletter'),
    reserve: expected_week => request('/weekly/newsletter/presentation', {expected_week}),
    open: week => window.WWNewsletter.openAutomatic(week), reschedule: schedule});
  // Manual reading/closing must not initiate an automatic reservation. Defer
  // this document only; no durable manual-first or cross-device suppression.
  document.getElementById('weekly-newsletter-open').addEventListener('click', () => controller.stop());
  let ready = false, scheduled = false;
  function schedule() {
    if (!ready || scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; void controller.attempt(); });
  }
  // Surface transitions and safe resume events; no wall-clock publication timer.
  new MutationObserver(() => { controller.invalidate(); schedule(); }).observe(document.body,
    {subtree: true, attributes: true, attributeFilter: ['hidden', 'open']});
  document.addEventListener('pointerdown', () => controller.invalidate(), true);
  document.addEventListener('keydown', () => controller.invalidate(), true);
  document.addEventListener('visibilitychange', () => { controller.invalidate(); schedule(); });
  window.addEventListener('pagehide', () => controller.stop());
  window.addEventListener('ww:session-changed', () => controller.stop());
  Promise.all([window.WWSessionBoundary.ready, window.WWWorldEntry?.arrivalReady,
    window.WWShedEntry?.ready, window.WWGameModeEntry?.ready]).then(values => {
    if (values.some(value => value === false) || !current()) return;
    ready = true; schedule();
  }).catch(() => {});
})();
