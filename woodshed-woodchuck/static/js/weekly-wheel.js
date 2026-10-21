(function () {
  'use strict';
  const labels = Object.freeze({activity: '14 Activity points', dandelion: '400 Dandelions',
    xp: '1,400 XP', spin_again: 'Spin Again', ufo: 'UFO emoji',
    dandelion_1000: '1,000 Dandelions', white_clover: 'Four-Leaf White Clover',
    pants_choice: 'Historical pants-choice prize'});
  const colors = ['#f4c95d', '#96c97b', '#92cbe0', '#e8b5d5', '#bea4dd', '#efad70', '#e9eee0'];
  const node = (tag, text) => { const el = document.createElement(tag); if (text != null) el.textContent = text; return el; };
  const button = (text, action) => {
    const el = node('button', text); el.type = 'button'; el.className = 'btn btn-primary';
    el.addEventListener('click', action); return el;
  };
  const recoveryDatabase = 'woodshed-prize-wheel-recovery';
  const recoveryStore = 'pending-actions';
  function validateAction(raw) {
    if (raw === null) return raw;
    if (typeof raw !== 'string') throw new Error('The saved Wheel action needs recovery.');
    const item = JSON.parse(raw), keys = Object.keys(item).sort().join(',');
    if (!(item.kind === 'fund' && keys === 'key,kind' && typeof item.key === 'string' && item.key.length > 0 && item.key.length <= 64) &&
        !(item.kind === 'rotation' && keys === 'kind,play,rotation' && typeof item.play === 'string' && /^[a-zA-Z0-9_-]+$/.test(item.play) &&
          Number.isSafeInteger(item.rotation) && item.rotation > 0))
      throw new Error('The saved Wheel action needs recovery. Do not clear browser storage.');
    return raw;
  }
  function openRecovery() {
    return new Promise((resolve, reject) => {
      let request, failed = false;
      const failure = () => { failed = true; reject(new Error('Wheel recovery storage is unavailable or blocked. Restore access and refresh.')); };
      try { request = indexedDB.open(recoveryDatabase, 1); } catch (_) { failure(); return; }
      request.onblocked = failure;
      request.onerror = failure;
      request.onupgradeneeded = () => {
        if (failed) { request.transaction.abort(); return; }
        try {
          if (!request.result.objectStoreNames.contains(recoveryStore)) request.result.createObjectStore(recoveryStore);
        } catch (_) { failure(); request.transaction.abort(); }
      };
      request.onsuccess = () => {
        const db = request.result;
        if (failed) { db.close(); return; }
        db.onversionchange = () => db.close();
        resolve(db);
      };
    });
  }
  // Only IndexedDB owns the pending action. Each transition reads and writes
  // atomically; no transaction spans fetch, lock acquisition or UI work.
  async function pendingAction(id, operation, expected, current) {
    const db = await openRecovery();
    return new Promise((resolve, reject) => {
      let tx, result, failure;
      try {
        tx = operation === 'read' ? db.transaction(recoveryStore, 'readonly') :
          db.transaction(recoveryStore, 'readwrite', {durability: 'strict'});
      } catch (_) { db.close(); reject(new Error('Wheel recovery storage could not start a transaction.')); return; }
      tx.oncomplete = () => { db.close(); resolve(result); };
      tx.onabort = () => { db.close(); reject(failure || new Error('Wheel recovery storage could not commit. Restore access and refresh.')); };
      tx.onerror = () => { failure ||= new Error('Wheel recovery storage failed. Restore access and refresh.'); };
      try {
        const store = tx.objectStore(recoveryStore), request = store.get(id);
        request.onsuccess = () => {
          try {
            if (!current()) throw new Error('This account or panel changed.');
            const raw = validateAction(request.result ?? null);
            if (operation === 'read') result = raw;
            else if (operation === 'create') {
              if (raw !== null) throw new Error('A saved action already exists. Refresh to recover it.');
              store.add(validateAction(expected), id); result = expected;
            } else {
              result = raw === expected;
              if (result) store.delete(id);
            }
          } catch (error) { failure = error; tx.abort(); }
        };
      } catch (error) { failure = error; tx.abort(); }
    });
  }
  function create({container, status, heading}) {
    const id = document.body.dataset.accountId, storageKey = `woodshed:prize-wheel:request:${id}`;
    let active = false, epoch = 0, account, busy = false, data, confirmed = null, animation;
    const valid = ticket => active && ticket === epoch && window.WWSessionBoundary?.isCurrent() === true &&
      !!window.WWState.stateForResponse(account) && document.body.dataset.accountId === id;
    const read = ticket => pendingAction(id, 'read', null, () => valid(ticket));
    async function write(raw, ticket) {
      await pendingAction(id, 'create', raw, () => valid(ticket));
      if (await read(ticket) !== raw) throw new Error('The saved action changed before confirmation. Refresh to recover it.');
    }
    const remove = (raw, ticket) => pendingAction(id, 'remove', raw, () => valid(ticket));
    function setBusy(value) {
      busy = value; container.setAttribute('aria-busy', String(value));
      container.querySelectorAll('button').forEach(el => { el.disabled = value || el.dataset.unavailable === 'true'; });
    }
    async function request(path, ticket, body) {
      if (!valid(ticket)) throw new Error('Sign in again to continue.');
      const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store',
        ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
      const result = await response.json();
      if (!valid(ticket)) throw new Error('This account or panel changed.');
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message || 'The Wheel request could not be confirmed.');
      return result;
    }
    async function locked(ticket, operation) {
      if (!navigator.locks?.request) throw new Error('Safe recovery requires browser coordination. Reload in a supported browser.');
      // Catch inside the native callback as well as outside it (Firefox).
      const failure = await navigator.locks.request(storageKey, async () => {
        try { if (valid(ticket)) await operation(); } catch (error) { return {error}; }
      });
      if (failure) throw failure.error;
    }
    function wheel(weights, outcome, animate) {
      const wrap = node('div'), disk = node('div'); wrap.className = 'weekly-prize-wheel-wrap';
      disk.className = 'weekly-prize-wheel'; disk.setAttribute('aria-hidden', 'true');
      let start = 0, target = 0;
      const wedges = weights.map((item, i) => {
        const end = start + item.weight / 100;
        if (item.outcome === outcome) target = (start + end) / 2 * 3.6;
        const wedge = `${colors[i % colors.length]} ${start}% ${end}%`; start = end; return wedge;
      });
      disk.style.background = `conic-gradient(${wedges.join(',')})`;
      disk.style.transform = `rotate(${-target}deg)`;
      disk.dataset.outcome = outcome || ''; disk.dataset.targetAngle = String(target);
      wrap.append(disk);
      if (animate && outcome !== 'pants_choice' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        animation = disk.animate([{transform: 'rotate(0deg)'}, {transform: `rotate(${1080 - target}deg)`}],
          {duration: 1000, easing: 'cubic-bezier(.12,.65,.15,1)'});
      }
      return wrap;
    }
    function render(ticket, raw, animate = false, storageFailed = false) {
      animation?.cancel(); animation = null; container.replaceChildren();
      if (!data) return;
      const play = data.play, outcome = play?.outcomes.at(-1);
      container.append(node('p', `${data.wallet.pickled_carrot} Pickled Carrot jars available`),
        node('p', `${data.cost} jars per funded play · ${data.remaining_plays} of ${data.daily_limit} funded plays remaining today`),
        node('p', `America/Chicago day: ${data.funded_day}`), wheel(data.weights, outcome, animate));
      const odds = node('ul'); odds.className = 'weekly-wheel-odds'; odds.setAttribute('aria-label', 'Prize probabilities');
      for (const item of data.weights) odds.append(node('li', `${item.weight / 100}% — ${labels[item.outcome] || item.outcome}`));
      container.append(odds);
      if (outcome) {
        const result = node('p', `Saved result: ${labels[outcome] || outcome}`); result.className = 'weekly-wheel-result';
        container.append(result);
        if (!play.complete) container.append(node('p', 'Your free continuation stays available after midnight and after the ninth funded play.'));
      }
      if (data.wallet.pants_choice) container.append(node('p', `${data.wallet.pants_choice} historical pants-choice entitlement(s). No new pants prizes.`));
      if (storageFailed) {
        container.append(node('p', 'Recovery storage is unavailable. Restore storage access, then refresh Wheel. No new purchase can start.'));
      } else {
        if (raw) {
          if (confirmed?.raw === raw) container.append(button('Continue', () => { if (valid(ticket)) void clear(raw); }));
          else container.append(button('Recover saved action', () => { if (valid(ticket)) void act('recover', raw); }));
        } else if (play && !play.complete) {
          const expected = {play: play.id, rotation: play.next_rotation};
          container.append(button('SPIN AGAIN — FREE', () => { if (valid(ticket)) void act('rotation', null, expected); }));
        } else {
          const pay = button(`Exchange ${data.cost} jars — PLAY`, () => {
            if (valid(ticket)) void act('fund', null, {play: play?.id || null});
          });
          pay.dataset.unavailable = String(data.wallet.pickled_carrot < data.cost || data.remaining_plays < 1);
          pay.disabled = pay.dataset.unavailable === 'true'; container.append(pay);
        }
      }
      container.append(button('Refresh Wheel', () => { if (valid(ticket)) void refresh(); }),
        node('p', `Server balance: ${data.credits} Dandelions. Rewards appear in your existing account, XP and inventory views when refreshed.`),
        button('Reload SHOP balances', () => { if (valid(ticket)) window.location.reload(); }));
      setBusy(false);
    }
    async function refresh(recover = false, message = '') {
      if (!active) return;
      const ticket = ++epoch; confirmed = null; setBusy(true); status.textContent = message || 'Loading Prize Wheel…';
      container.replaceChildren();
      try {
        data = await request('/weekly/wheel', ticket);
        if (!valid(ticket)) return;
        let raw;
        try { raw = await read(ticket); }
        catch (_) {
          if (valid(ticket)) {
            render(ticket, null, false, true);
            status.textContent = 'Unable to read recovery storage. Restore access and refresh. No new payment was sent.';
          }
          return;
        }
        if (!valid(ticket)) return;
        render(ticket, raw); status.textContent = message || 'Wheel loaded. Plays require an explicit action.';
        if (recover && raw) await act('recover', raw);
      } catch (_) {
        if (valid(ticket)) {
          status.textContent = 'Unable to read Wheel or recovery storage. Restore access and refresh. No new payment was sent.';
          container.append(button('Refresh Wheel', () => { if (valid(ticket)) void refresh(true); })); setBusy(false);
        }
      }
    }
    async function act(kind, expectedRaw, expectedPlay) {
      if (!active || busy) return;
      const ticket = epoch; setBusy(true); status.textContent = 'Checking saved action…';
      try {
        await locked(ticket, async () => {
          if (await read(ticket) !== expectedRaw) throw new Error('The pending action changed. Refresh to review it.');
          let raw = expectedRaw;
          if (kind !== 'recover') {
            const fresh = await request('/weekly/wheel', ticket);
            if (!valid(ticket)) return;
            if (await read(ticket) !== null || (fresh.play?.id || null) !== expectedPlay.play)
              throw new Error('The active play changed. Refresh before another action.');
            if (kind === 'rotation' && (fresh.play.complete || fresh.play.next_rotation !== expectedPlay.rotation))
              throw new Error('That rotation changed. Refresh the saved result.');
            if (kind === 'fund' && (fresh.play && !fresh.play.complete)) throw new Error('Continue the saved free rotation first.');
            raw = JSON.stringify(kind === 'fund' ? {kind: 'fund', key: crypto.randomUUID()} :
              {kind: 'rotation', play: expectedPlay.play, rotation: expectedPlay.rotation});
            await write(raw, ticket);
          }
          // Recovery is always bound to its original record, never a new action.
          if (!valid(ticket)) return;
          if (!raw || await read(ticket) !== raw) throw new Error('The pending action changed. Refresh to review the saved result.');
          const action = JSON.parse(raw);
          let result;
          if (action.kind === 'rotation') {
            const saved = await request(`/weekly/wheel/plays/${encodeURIComponent(action.play)}`, ticket);
            if (!valid(ticket)) return;
            if (await read(ticket) !== raw) throw new Error('The pending action changed. Reading the saved server result.');
            result = saved.play.outcomes.length >= action.rotation ? saved :
              await request(`/weekly/wheel/plays/${encodeURIComponent(action.play)}/rotations`, ticket, {rotation: action.rotation});
          } else result = await request('/weekly/wheel/plays', ticket, {request_key: action.key});
          if (!valid(ticket)) return;
          if (await read(ticket) !== raw) throw new Error('The pending action changed. Reading the saved server result.');
          data = result; confirmed = {raw, play: result.play.id}; render(ticket, raw, kind !== 'recover');
          status.textContent = `Confirmed: ${labels[result.play.outcomes.at(-1)] || result.play.outcomes.at(-1)}. No additional play starts automatically.`;
          heading?.focus({preventScroll: true});
        });
      } catch (error) {
        if (valid(ticket)) await refresh(false, `${error.message} Review the saved server result before continuing. If recovery storage is unavailable, restore access and refresh.`);
      } finally { if (valid(ticket)) setBusy(false); }
    }
    async function clear(raw) {
      if (busy || confirmed?.raw !== raw) return;
      const ticket = epoch, playId = confirmed.play; setBusy(true);
      try {
        await locked(ticket, async () => {
          if (confirmed?.raw !== raw || confirmed.play !== playId) return;
          if (!await remove(raw, ticket)) throw new Error('The pending action changed.');
          confirmed = null;
        });
        if (valid(ticket)) await refresh();
      } catch (_) {
        if (valid(ticket)) await refresh(false, 'Saved result retained. Recovery storage could not be cleared or the action changed. Restore storage access and refresh.');
      } finally { if (valid(ticket)) setBusy(false); }
    }
    return Object.freeze({open() { active = true; account = window.WWState.accountRequest(); return refresh(true); },
      close() { active = false; epoch++; busy = false; confirmed = null; data = null; animation?.cancel(); container.replaceChildren(); status.textContent = ''; }});
  }
  window.WWPrizeWheel = Object.freeze({create, labels});
  const opener = document.getElementById('weekly-wheel-open');
  if (!opener) return;
  const dialog = document.getElementById('shop-feature-dialog'), view = document.getElementById('weekly-wheel-view');
  const room = dialog.querySelector('[data-shop-panel-content="practice-room"]'), heading = document.getElementById('shop-dialog-title');
  const controller = create({container: document.getElementById('weekly-wheel-content'), status: document.getElementById('weekly-wheel-status'), heading});
  function restore() { controller.close(); view.hidden = true; room.hidden = false; opener.setAttribute('aria-expanded', 'false'); heading.textContent = 'Practice Rooms'; }
  opener.addEventListener('click', () => {
    if (window.WWSessionBoundary?.isCurrent() !== true) return;
    room.hidden = true; view.hidden = false; opener.setAttribute('aria-expanded', 'true');
    heading.textContent = 'Prize Wheel'; heading.focus({preventScroll: true}); void controller.open();
  });
  document.getElementById('weekly-wheel-back').addEventListener('click', () => {restore(); opener.focus({preventScroll: true});});
  dialog.addEventListener('close', () => { if (!view.hidden) restore(); });
  window.addEventListener('pagehide', () => { if (!view.hidden) restore(); });
  window.addEventListener('ww:session-changed', () => { if (!view.hidden) restore(); });
  window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => {
    view.getAnimations({subtree: true}).forEach(item => item.cancel());
  });
})();
