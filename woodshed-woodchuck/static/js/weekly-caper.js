(function () {
  'use strict';
  const current = () => window.WWSessionBoundary?.isCurrent() === true;
  let generation = 0;
  async function get(url, options = {}) {
    if (!current()) throw new Error('Sign in again to continue.');
    const request = window.WWState.accountRequest(), epoch = generation;
    const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options});
    const data = await response.json();
    const state = window.WWState.stateForResponse(request);
    if (!state || epoch !== generation || !current()) throw new Error('This session changed.');
    if (!response.ok) throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || 'The request could not be saved. Refresh and try again.'), {data});
    return data;
  }
  const post = (url, body) => get(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const node = (tag, text) => {const el = document.createElement(tag); if (text != null) el.textContent = text; return el;};

  // Shared task presentation/controller. Mum supplies navigation; a future
  // Newsletter container can mount this without copying qualification rules.
  const caperLanes = [
    ['logging', 'Logging days'], ['practice_minutes', 'Practice Minutes'],
    ['homework', 'Homework'], ['board', 'Bonus Challenges'], ['thirds', 'Thirds'],
  ];
  const caperFoci = ['Build daily consistency', 'Improve tone and control',
    'Learn songs faster', 'Strengthen improvisation'];
  const jarLanes = new Set(['logging', 'practice_minutes', 'homework']);
  const button = (label, action) => {
    const el = node('button', label); el.type = 'button'; el.className = 'btn btn-primary';
    el.addEventListener('click', action); return el;
  };
  const instrumentLabel = key => window.WWInstruments?.definitions.find(item => item.key === key)?.label || key;
  function instrumentKey(label) {
    // Display matching only. The start route resolves the instrument and validates
    // its opaque start_context again; no key from this lookup is submitted.
    const normalized = String(label || '').trim().toLowerCase().replace(/[\s/_-]+/g, ' ');
    if (['piano', 'keyboard', 'piano keyboard'].includes(normalized)) return 'piano-keyboard';
    if (normalized === 'hand percussion') return 'percussion';
    return window.WWInstruments?.definitions.find(item =>
      item.key.replace(/-/g, ' ') === normalized || item.label.toLowerCase().replace(/[\s/_-]+/g, ' ') === normalized)?.key;
  }
  function minutesPair(seconds, target) {
    // Display formatting only; do not round up or compare these values to qualify.
    const minutes = Math.floor(seconds / 60), remainder = seconds % 60;
    return remainder ? `${window.WWPracticeDuration.seconds(seconds)} / ${window.WWPracticeDuration.seconds(target)}`
      : `${minutes} / ${target / 60} min`;
  }
  function renderCaperTasks(tasks, planId, onClaim, assignments = []) {
    const list = node('div'); list.className = 'weekly-caper-tasks';
    for (const [lane, label] of caperLanes) {
      const task = tasks[lane], card = node('section'); card.className = 'weekly-caper-task'; card.dataset.lane = lane;
      card.setAttribute('aria-label', label); card.append(node('h3', label));
      if (task.claimed) card.append(node('strong', 'Claimed'));
      else if (task.claimable) {
        const claim = button('CLAIM', () => onClaim(planId, lane));
        claim.setAttribute('aria-label', `Claim ${label}`); card.append(claim);
      } else if (lane === 'logging') card.append(node('p', `${task.progress} / ${task.target}`));
      else if (lane === 'practice_minutes') card.append(node('p', minutesPair(task.progress, task.target)));
      else if (lane === 'board') card.append(node('p', `${task.completed} / ${task.target}`));
      else if (lane === 'thirds') {
        card.append(node('p', `Best: ${task.authoritative_score ?? 'No completed round'}`),
          node('p', `Goal: ${task.target} in one round`));
      } else {
        if (!task.assignments.length) card.append(node('p', 'No Homework started.'));
        for (const item of task.assignments) card.append(node('p',
          `${instrumentLabel(item.instrument)}: ${minutesPair(item.practice_seconds, item.target_seconds)}`));
      }
      if (jarLanes.has(lane)) card.append(node('small', task.claimed ? '1 Pickled Carrot jar claimed' : 'Reward: 1 Pickled Carrot jar'));
      if (lane === 'homework') for (const assignment of assignments) {
        const details = node('details'), spec = assignment.homework;
        details.append(node('summary', `${instrumentLabel(assignment.instrument)} Homework`), node('p', spec.title));
        const instructions = node('ol');
        instructions.append(...spec.instructions.map(text => node('li', text)));
        details.append(instructions, node('p', `Suggested blocks: ${spec.suggested_blocks.count} × ${spec.suggested_blocks.minutes_each} min`));
        card.append(details);
      }
      list.append(card);
    }
    return list;
  }
  function caperArt(kind) {
    const wrap = node('span'); wrap.className = `weekly-caper-art weekly-caper-art-${kind}`;
    const image = node('img'); image.alt = ''; image.hidden = true;
    const placeholder = node('span', kind === 'clover' ? 'White Clover artwork pending approval' : 'Clue');
    placeholder.className = 'weekly-caper-art-placeholder';
    image.addEventListener('load', () => {image.hidden = false; placeholder.hidden = true;});
    image.addEventListener('error', () => {image.hidden = true; placeholder.hidden = false;});
    image.src = kind === 'clover' ? '/static/img/weekly/four-leaf-white-clover.png' : '/static/img/weekly/clue-token.png';
    image.width = kind === 'clover' ? 1024 : 512; image.height = image.width;
    wrap.append(image, placeholder); return wrap;
  }
  function createCaper({container, status, heading, onContinue}) {
    let active = false, epoch = 0, busy = false, account, data, pages = [], expanded = new Set();
    const accountId = document.body.dataset.accountId;
    const storageKey = `woodshed:clover-caper:request:${accountId}`;
    const valid = ticket => active && ticket === epoch && current() && !!window.WWState.stateForResponse(account);
    function key(required = false) {
      try {return localStorage.getItem(storageKey);}
      catch (_) {
        if (required) throw new Error('Local recovery storage is unavailable. Your saved Clover request has not been replaced.');
        return null;
      }
    }
    function saveKey(value) {
      // A failed persistence attempt must never be followed by a new Clover POST.
      if (!accountId) throw new Error('Sign in again to continue.');
      if (value) {
        localStorage.setItem(storageKey, value);
        if (localStorage.getItem(storageKey) !== value) throw new Error('Please allow local storage to save this Clover request.');
      } else {
        localStorage.removeItem(storageKey);
        if (localStorage.getItem(storageKey) !== null) throw new Error('Local recovery storage could not be cleared.');
      }
    }
    function setBusy(value) {
      busy = value; container.setAttribute('aria-busy', String(value));
      container.querySelectorAll('button,select').forEach(el => {el.disabled = value || el.dataset.unavailable === 'true';});
    }
    function focusHeading() {heading?.focus({preventScroll: true});}
    function rememberHistory() {
      container.querySelectorAll('details[data-source-plan]').forEach(el => {
        if (el.open) expanded.add(el.dataset.sourcePlan); else expanded.delete(el.dataset.sourcePlan);
      });
    }
    function renderClue(parent, source, ticket) {
      if (source.clue.claimed) parent.append(node('p', 'Clue claimed'));
      else if (source.clue.claimable) parent.append(button('CLAIM CLUE', () => {
        if (valid(ticket)) void mutate(`/weekly/plans/${source.plan_id}/clue/claim`, {}, 'Clue claimed.');
      }));
      else parent.append(node('p', 'Claim all five tasks to unlock your clue.'));
    }
    function claimTask(planId, lane, ticket) {
      if (!valid(ticket)) return;
      void mutate(`/weekly/plans/${planId}/tasks/${lane}/claim`, {},
        jarLanes.has(lane) ? 'Claimed: 1 Pickled Carrot jar.' : 'Task claimed.');
    }
    function render(ticket) {
      const {weekly, claims, profile} = data;
      container.replaceChildren();
      if (!weekly.plan) {
        container.append(node('p', 'Choose your Focus for this week.'),
          node('p', `Current Game Mode: ${profile.game_mode || 'Not selected'}`));
        const label = node('label', 'Weekly Focus'), select = node('select');
        select.id = 'weekly-caper-focus'; select.name = 'focus'; label.htmlFor = select.id;
        const prompt = node('option', 'Choose a Focus'); prompt.value = ''; select.append(prompt);
        for (const focus of caperFoci) {const option = node('option', focus); option.value = focus; select.append(option);}
        const start = button('Start This Week', () => {
          if (valid(ticket) && caperFoci.includes(select.value)) void mutate('/weekly/plan', {focus: select.value}, 'Weekly plan saved.');
        });
        const update = () => {
          start.dataset.unavailable = String(!profile.game_mode || !caperFoci.includes(select.value));
          start.disabled = busy || start.dataset.unavailable === 'true';
        };
        select.addEventListener('change', update); update();
        container.append(label, select, start);
        if (!profile.game_mode) container.append(node('p', 'Choose Game Mode using the SHED Game Mode control, then reopen Clover Caper.'));
      } else {
        const plan = weekly.plan, assignments = plan.instruments || [];
        container.append(node('p', `Week of ${plan.week_start}`), node('p', `${plan.focus} · Issued Game Mode: ${plan.issued_game_mode}`));
        const instrument = node('section'); instrument.className = 'weekly-caper-instrument';
        instrument.append(node('h3', 'This week’s instrument work'), node('p', `Current instrument: ${profile.instrument || 'Not selected'}`));
        if (assignments.some(item => item.instrument === instrumentKey(profile.instrument))) instrument.append(node('p', 'Your saved Homework is below.'));
        else if (assignments.length >= 2) instrument.append(node('p', 'Both weekly instrument slots are saved. You can still explore and practice other instruments.'));
        else if (weekly.start_context) instrument.append(button('Start this week’s work', () => {
          if (valid(ticket)) void mutate('/weekly/plan/instruments/current/start', {start_context: weekly.start_context},
            'Instrument work saved.', profile.instrument);
        }));
        else instrument.append(node('p', 'Choose a supported instrument using the SHED instrument control, then reopen Clover Caper.'));
        container.append(instrument, renderCaperTasks(weekly.tasks, plan.id, (id, lane) => claimTask(id, lane, ticket), assignments));
        renderClue(container, claims.current_plan, ticket);
      }
      const clues = node('section'); clues.className = 'weekly-caper-clues';
      clues.append(caperArt('clue'), node('p', `${claims.clue_progress} / 7 clues`),
        node('p', `${claims.white_clover_balance} Four-Leaf White Clovers`));
      try {
        const pendingKey = key(true);
        if (pendingKey) clues.append(button('Resume Clover claim', () => {if (valid(ticket)) void claimClover(pendingKey);}));
        else if (claims.clover_claimable) clues.append(button('CLAIM CLOVER', () => {if (valid(ticket)) void claimClover();}));
      } catch (error) { clues.append(node('p', error.message)); }
      container.append(clues);
      const history = pages.flatMap(page => page.historical_plans), cursor = pages.at(-1)?.next_before_plan_id;
      if (history.length || cursor != null) {
        const earlier = node('section'); earlier.className = 'weekly-caper-history'; earlier.append(node('h3', 'Earlier weeks'));
        for (const source of history) {
          const detail = node('details'); detail.dataset.sourcePlan = String(source.plan_id); detail.open = expanded.has(String(source.plan_id));
          detail.append(node('summary', `Week of ${source.week_start}`),
            renderCaperTasks(source.tasks, source.plan_id, (id, lane) => claimTask(id, lane, ticket)));
          renderClue(detail, source, ticket); earlier.append(detail);
        }
        if (cursor != null) earlier.append(button('Older weeks', async () => {
          if (!valid(ticket) || busy) return;
          rememberHistory(); setBusy(true);
          try {
            const page = await get(`/weekly/claims?before_plan_id=${cursor}`);
            if (!valid(ticket)) return;
            pages.push(page); render(ticket); status.textContent = 'Earlier weeks loaded.'; focusHeading();
          } catch (error) {if (valid(ticket)) status.textContent = error.message;}
          finally {if (valid(ticket)) setBusy(false);}
        }));
        container.append(earlier);
      }
    }
    async function refresh(message = '', recover = false) {
      if (!active) return;
      rememberHistory(); const ticket = ++epoch;
      const cursors = pages.slice(0, -1).map(page => page.next_before_plan_id);
      container.replaceChildren(node('p', 'Loading Clover Caper…')); setBusy(true); status.textContent = message;
      try {
        const [weekly, claims, me] = await Promise.all([get('/weekly/plan'), get('/weekly/claims'), get('/account/me')]);
        if (!valid(ticket)) return;
        if (!me.authenticated || me.profile?.woodchuck_id !== accountId) throw new Error('Sign in again to continue.');
        if ((weekly.plan?.id ?? null) !== (claims.current_plan?.plan_id ?? null)) throw new Error('The week changed. Refresh Clover Caper.');
        const restored = [claims];
        for (const cursor of cursors) {
          if (cursor != null) restored.push(await get(`/weekly/claims?before_plan_id=${cursor}`));
          if (!valid(ticket)) return;
        }
        pages = restored; data = {weekly, claims, profile: me.profile};
        render(ticket); setBusy(false); focusHeading();
        // Only a key saved by an earlier explicit Clover click permits recovery.
        if (recover) {
          try { const pendingKey = key(true); if (pendingKey) await claimClover(pendingKey); }
          catch (error) { if (valid(ticket)) status.textContent = error.message; }
        }
      } catch (error) {
        if (!valid(ticket)) return;
        container.replaceChildren(button('Refresh Clover Caper', () => {if (valid(ticket)) void refresh('', true);}));
        status.textContent = error.message; setBusy(false); focusHeading();
      }
    }
    async function mutate(url, body, message, expectedInstrument) {
      if (!active || busy || !current()) return;
      const ticket = epoch; setBusy(true); status.textContent = 'Saving…';
      try {
        if (expectedInstrument !== undefined) {
          const me = await get('/account/me');
          if (!valid(ticket)) return;
          if (me.profile?.instrument !== expectedInstrument) throw new Error('Your active instrument changed. Review it before starting.');
        }
        await post(url, body);
        if (valid(ticket)) await refresh(message);
      } catch (error) {
        // An error may follow a committed transaction. Reload durable state
        // before exposing any claim button again; failed reload offers GET only.
        if (valid(ticket)) await refresh(`${error.message} Checking saved state.`);
      }
    }
    function celebrate(receipt, savedKey) {
      const ticket = epoch;
      container.replaceChildren(); container.classList.add('weekly-caper-celebration');
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      container.dataset.reducedMotion = String(reduced);
      container.append(node('h3', 'Four-Leaf White Clover earned'));
      const continueButton = button('', async () => {
        if (!valid(ticket) || busy) return;
        setBusy(true);
        try {
          if (!navigator.locks?.request) throw new Error('Clover recovery coordination is unavailable. Reload in a supported browser.');
          const failure = await navigator.locks.request(storageKey, () => {
            // Clearing and claiming share one lock. A delayed confirmation may
            // only clear its own key in the still-current account and panel.
            try {
              if (!valid(ticket)) return;
              if (key(true) !== savedKey) throw new Error('The pending recovery request changed. Reopen Clover Caper to recover it.');
              saveKey(null);
            } catch (error) { return {error}; }
          });
          if (failure) throw failure.error;
          if (valid(ticket)) onContinue();
        } catch (error) {
          if (valid(ticket)) status.textContent = `The Clover is saved. ${error.message}`;
        } finally { if (valid(ticket)) setBusy(false); }
      });
      continueButton.className = 'weekly-caper-clover'; continueButton.setAttribute('aria-label', 'Continue to SHED');
      continueButton.append(caperArt('clover'), node('span', 'Continue to SHED'));
      container.append(continueButton, node('p', `${receipt.white_clover_balance} Four-Leaf White Clovers`));
      status.textContent = 'Four-Leaf White Clover earned.'; setBusy(false); continueButton.focus({preventScroll: true});
      if (!reduced) window.dispatchEvent(new CustomEvent('woodshed:celebrate'));
    }
    async function claimClover(expectedKey = null) {
      if (!active || busy || !current()) return;
      const ticket = epoch; setBusy(true); status.textContent = 'Saving Clover claim…';
      try {
        // Coordinate only recovery-key ownership; the native session boundary
        // remains the authentication authority. Never use a presentation fallback.
        if (!navigator.locks?.request) throw new Error('Clover recovery coordination is unavailable. Reload in a supported browser.');
        const failure = await navigator.locks.request(storageKey, async () => {
          // Handle failures before they escape the native lock callback. The
          // outer recovery path still owns feedback and the durable reread.
          try {
            if (!valid(ticket)) return;
            let savedKey = key(true);
            // NULL denotes a new explicit click. Resume/automatic recovery is
            // bound to the observed key and may never create or adopt an action.
            if (savedKey !== expectedKey)
              throw new Error('The pending recovery request changed. Review the refreshed Clover state before continuing.');
            if (expectedKey === null) { savedKey = crypto.randomUUID(); saveKey(savedKey); }
            if (!valid(ticket)) return;
            const result = await post('/weekly/clover/claim', {request_key: savedKey});
            if (!valid(ticket)) return;
            if (result.claimed !== true || result.reward?.type !== 'white_clover' || result.reward.amount !== 1)
              throw new Error('Refresh to recover the saved Clover result.');
            celebrate(result, savedKey);
          } catch (error) { return {error}; }
        });
        if (failure) throw failure.error;
      } catch (error) {
        if (valid(ticket)) await refresh(`${error.message} Checking saved state.`);
      }
    }
    return Object.freeze({
      open() {
        active = true; account = window.WWState.accountRequest(); pages = []; expanded = new Set();
        container.classList.remove('weekly-caper-celebration'); return refresh('', true);
      },
      close() {
        active = false; epoch++; busy = false; data = null; pages = [];
        container.replaceChildren(); container.classList.remove('weekly-caper-celebration'); status.textContent = '';
      },
    });
  }
  window.WWWeeklyCaper = Object.freeze({renderTasks: renderCaperTasks, create: createCaper});
  const caperOpen = document.getElementById('weekly-caper-open');
  let mumCaper = null;
  if (caperOpen) {
    const panel = document.getElementById('mum-panel'), care = document.getElementById('mum-care-content');
    const view = document.getElementById('weekly-caper-view'), heading = document.getElementById('mum-title');
    const close = document.getElementById('mum-close-button'), originalTitle = heading.textContent;
    mumCaper = createCaper({container: document.getElementById('weekly-caper-content'),
      status: document.getElementById('weekly-caper-status'), heading, onContinue: () => close.click()});
    const restore = () => {
      mumCaper.close(); care.hidden = false; view.hidden = true; heading.textContent = originalTitle;
      close.setAttribute('aria-label', 'Close Mum’s Corner'); caperOpen.setAttribute('aria-expanded', 'false');
    };
    caperOpen.addEventListener('click', () => {
      if (!current()) return;
      panel.dispatchEvent(new CustomEvent('woodshed:caper-opened'));
      care.hidden = true; view.hidden = false; heading.textContent = 'Clover Caper';
      close.setAttribute('aria-label', 'Close Clover Caper'); caperOpen.setAttribute('aria-expanded', 'true');
      heading.focus({preventScroll: true}); void mumCaper.open();
    });
    document.getElementById('weekly-caper-back').addEventListener('click', () => {restore(); caperOpen.focus();});
    document.getElementById('weekly-caper-close').addEventListener('click', () => close.click());
    panel.addEventListener('woodshed:mum-opened', restore);
    panel.addEventListener('woodshed:mum-closed', restore);
    window.addEventListener('pagehide', restore);
    window.addEventListener('ww:session-changed', () => { generation++; restore(); });
  }

})();
