/* Brief read-only Pantry. The existing SHOP control owns activation. */
(function () {
  'use strict';
  const node = (tag, text) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    return el;
  };
  function render(container, data) {
    const jars = node('span'); jars.className = 'weekly-pantry-jars';
    for (const [title, actual, target, format] of [
      ['Chart-logging days', data.progress.logging_days, data.targets?.logging_days, String],
      ['Practice Minutes', data.progress.practice_seconds, data.targets?.practice_seconds, window.WWPracticeDuration.seconds],
    ]) {
      const jar = node('span'); jar.className = 'weekly-pantry-jar';
      const fill = node('span'); fill.className = 'weekly-pantry-fill'; fill.setAttribute('aria-hidden', 'true');
      const hasTarget = Number.isFinite(target) && target > 0;
      fill.style.height = hasTarget ? `${Math.max(0, Math.min(100, actual / target * 100))}%` : '0%';
      jar.append(fill, node('strong', title), node('span', `${format(actual)}${hasTarget ? ` / ${format(target)}` : ' · goal not set'}`));
      jars.append(jar);
    }
    const amount = node('strong', `${data.credits} Dandelions`);
    container.replaceChildren(amount, node('small', `Week of ${data.context.week_start}`), jars,
      node('span', 'Progress statistics, not spendable currency.'),
      node('span', `${data.wallet.pickled_carrot} Pickled Carrot jars available`),
      node('span', `${data.wallet.white_clover} White Clovers`));
    return amount;
  }
  window.WWWeeklyPantry = Object.freeze({render});
  const opener = document.getElementById('dandelion-object');
  const dialog = document.getElementById('weekly-pantry');
  if (!opener || opener.dataset.weeklyPantry !== 'true' || !dialog) return;
  const content = document.getElementById('weekly-pantry-content');
  const status = document.getElementById('weekly-pantry-status');
  const display = document.getElementById('weekly-pantry-close');
  let generation = 0, timers = [];
  const current = () => window.WWSessionBoundary?.isCurrent() === true;
  function clearTimers() {timers.forEach(clearTimeout); timers = [];}
  window.WWSurfaces.register(dialog, () => dialog.close());
  async function open() {
    if (!current()) return;
    if (dialog.open) {dialog.close(); return;}
    if (window.WWSurfaces.current()) return;
    const expected = ++generation, account = window.WWState.accountRequest();
    const valid = () => expected === generation && dialog.open && current() && !!window.WWState.stateForResponse(account);
    clearTimers(); status.textContent = 'Loading Pantry…'; content.replaceChildren();
    display.setAttribute('aria-label', 'Loading Pantry. Tap to dismiss.');
    dialog.showModal();
    try {
      const response = await fetch('/weekly/pantry', {credentials: 'same-origin', cache: 'no-store'});
      const data = await response.json();
      if (!valid()) return;
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || 'Pantry could not be loaded.');
      const amount = render(content, data);
      status.textContent = 'Tap to dismiss.';
      display.setAttribute('aria-label', `${content.textContent}. Tap to dismiss.`);
      timers.push(setTimeout(() => {if (valid()) amount.hidden = true;}, 1400));
      timers.push(setTimeout(() => {
        if (!valid()) return;
        dialog.close();
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches)
          window.dispatchEvent(new CustomEvent('woodshed:celebrate'));
      }, 4500));
    } catch (error) {
      if (!valid()) return;
      status.textContent = `${error.message} Tap to dismiss, then tap Dandelions to retry.`;
      display.setAttribute('aria-label', status.textContent);
      timers.push(setTimeout(() => {if (valid()) dialog.close();}, 4500));
    }
  }
  // One existing SHOP listener calls open(); no second listener on its hit area.
  window.WWWeeklyPantry = Object.freeze({render, open});
  display.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    // A native close event may be queued until after another explicit opening.
    if (dialog.open) return;
    generation++; clearTimers(); content.replaceChildren(); status.textContent = '';
    display.setAttribute('aria-label', 'Pantry. Tap to dismiss.');
  });
  for (const event of ['pagehide', 'ww:session-changed']) window.addEventListener(event, () => {
    generation++; clearTimers(); if (dialog.open) dialog.close(); content.replaceChildren(); status.textContent = '';
    display.setAttribute('aria-label', 'Pantry. Tap to dismiss.');
  });
})();
