(function () {
  "use strict";
  let held = null, release = null, pending = false;
  function relinquish() { if (release) release(); release = null; held = null; }
  async function acquire(method) {
    if (held === method) return true;
    if (held || pending || !navigator.locks) return false;
    const binding = window.WWSessionBoundary?.binding();
    if (!binding) return false;
    pending = true;
    return new Promise(resolve => {
      navigator.locks.request(`woodshed:practice-method:${binding.accountId}`, {ifAvailable: true}, async lock => {
        pending = false;
        if (!lock || !window.WWSessionBoundary.matchesBinding(binding)) { resolve(false); return; }
        held = method;
        resolve(true);
        await new Promise(done => { release = done; });
      }).catch(() => { pending = false; resolve(false); });
    });
  }
  window.WWPracticeMethods = Object.freeze({acquire, release: relinquish,
    unavailable: "Another practice method is active. Stop it in its tab and try again. If this browser cannot coordinate practice tools, use a current browser on HTTPS."});
  window.addEventListener("pagehide", relinquish);
  window.addEventListener("ww:session-changed", relinquish);
})();
