/* Manual, two-stage Newsletter. No wallet, claim or preference writer. */
(function () {
  'use strict';
  const foci = ['Build daily consistency', 'Improve tone and control', 'Learn songs faster', 'Strengthen improvisation'];
  const lanes = [['logging', 'Practice Chart logging days'], ['practice_minutes', 'Practice Minutes'],
    ['homework', 'Homework'], ['board', 'BOARD Bonus Challenges'], ['thirds', 'Thirds']];
  const node = (tag, text) => { const el = document.createElement(tag); if (text != null) el.textContent = text; return el; };
  const button = (text, action) => { const el = node('button', text); el.type = 'button'; el.className = 'btn btn-secondary'; el.addEventListener('click', action); return el; };
  const duration = value => `${Math.floor(value / 60)} min ${value % 60} sec`;
  const instrument = key => window.WWInstruments?.definitions.find(item => item.key === key)?.label || key || 'Not selected';
  function savedPlan(parent, plan) {
    if (!plan) { parent.append(node('p', 'No saved plan for this week.')); return; }
    parent.append(node('p', `Saved Focus: ${plan.focus}`), node('p', `Issued Game Mode: ${plan.issued_game_mode}`),
      node('p', `Frozen goals: ${plan.targets.logging_days} logging days; ${duration(plan.targets.practice_seconds)} practice; ${plan.targets.homework_minutes} min Homework; Thirds ${plan.targets.thirds} in one round.`));
    for (const assignment of plan.instruments) {
      const section = node('section'), spec = assignment.homework;
      section.append(node('h4', `${instrument(assignment.instrument)} — saved Homework`));
      if (spec.status === 'saved') {
        const instructions = node('ol');
        instructions.append(...spec.instructions.map(text => node('li', text)));
        section.append(node('p', spec.title), instructions,
          node('p', `Suggested blocks: ${spec.suggested_blocks.count} × ${spec.suggested_blocks.minutes_each} min`));
      } else section.append(node('p', 'Saved Homework presentation unavailable.'));
      parent.append(section);
    }
  }
  function tasks(parent, values) {
    if (!values) return;
    for (const [lane, label] of lanes) {
      const task = values[lane], card = node('section');
      card.dataset.lane = lane;
      card.append(node('h4', label), node('p', task.claimed ? 'Claimed' : task.qualified ? 'Qualified — available to claim in Clover Caper' : 'Not yet qualified'));
      if (lane === 'logging') card.append(node('p', `${task.progress} / ${task.target} days`));
      if (lane === 'practice_minutes') card.append(node('p', `${duration(task.progress)} / ${duration(task.target)}`));
      if (lane === 'board') card.append(node('p', `${task.completed} / ${task.target} distinct days`));
      if (lane === 'thirds') card.append(node('p', `Best eligible round: ${task.authoritative_score ?? 'None'}; target ${task.target}`));
      if (lane === 'homework') for (const item of task.assignments)
        card.append(node('p', `${instrument(item.instrument)}: ${duration(item.practice_seconds)} / ${duration(item.target_seconds)}`));
      if (['logging', 'practice_minutes', 'homework'].includes(lane)) card.append(node('small', task.claimed ? '1 Pickled Carrot jar already claimed.' : 'Prospective reward: 1 Pickled Carrot jar, claimed separately in Clover Caper.'));
      parent.append(card);
    }
  }
  function receipt(parent, label, value) {
    if (value) parent.append(node('p', `${label}: ${value.amount} ${value.reward_type.replaceAll('_', ' ')} recorded.`));
  }
  function create({container, status, heading, editProfile}) {
    let active = false, epoch = 0, account, data, busy = false, stage = 1;
    const valid = ticket => active && ticket === epoch && window.WWSessionBoundary?.isCurrent() === true && !!window.WWState.stateForResponse(account);
    function lock(value) { busy = value; container.setAttribute('aria-busy', String(value)); container.querySelectorAll('button,select').forEach(el => { el.disabled = value || el.dataset.unavailable === 'true'; }); }
    async function request(path, ticket, body) {
      if (!valid(ticket)) throw new Error('This session changed. Reopen Newsletter.');
      const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...(body === undefined ? {} : {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
      const payload = await response.json();
      if (!valid(ticket)) throw new Error('This session changed. Reopen Newsletter.');
      if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || 'Unable to complete the request.');
      return payload;
    }
    function action(text, callback, unavailable = false) {
      const el = button(text, () => { if (!busy && valid(epoch)) void callback(); });
      el.dataset.unavailable = String(unavailable); el.disabled = unavailable; return el;
    }
    function render() {
      const ticket = epoch;
      container.replaceChildren(); heading.textContent = stage === 1 ? 'Newsletter — Last Week' : 'Newsletter — Your New Week';
      container.dataset.stage = String(stage);
      if (!data.context.available) {
        container.append(node('p', 'This week’s Newsletter is available Monday at 13:30 America/Chicago.'), node('p', `Publication: ${data.context.publication_at}`)); return;
      }
      const current = data.current;
      container.append(node('p', `Edition: ${data.context.week_start} – ${data.context.week_end}`));
      if (stage === 1) {
        const recap = data.recap, section = node('section');
        section.append(node('h3', `Last week: ${data.context.recap_week_start} – ${data.context.recap_week_end}`));
        savedPlan(section, recap.plan);
        section.append(node('p', `Recorded practice as of this read: ${duration(recap.practice.practice_seconds)}; ${recap.practice.logging_days} logging days. This is not frozen history.`));
        tasks(section, recap.tasks);
        for (const [name, value] of Object.entries(recap.receipts)) receipt(section, name.replaceAll('_', ' '), value);
        section.append(node('h3', 'Competition recognition'), node('p', `Results: ${recap.recognition.status}`));
        let achieved = Object.values(recap.receipts).some(Boolean);
        for (const competition of recap.recognition.competitions) {
          section.append(node('p', `Competition results: ${competition.status}`));
          for (const honor of competition.honors) {
            achieved = true;
            section.append(node('p', `${honor.contest_key}: ${honor.medal || 'Recorded honor'} · rank ${honor.rank} · ${honor.subject_type}`));
            for (const reward of honor.rewards) receipt(section, 'Recorded recognition reward', reward);
          }
        }
        if (achieved) { const cheer = node('p', 'Well done on your recorded achievements!'); cheer.className = 'newsletter-achievement'; section.prepend(cheer); }
        section.append(node('p', 'Frozen Top 20, Practice Rating history and Director’s Choice recap are unavailable.'));
        container.append(section, node('h3', 'This week’s choices'));
      } else container.append(node('h3', 'Your saved week'));
      container.append(node('p', `Current active Instrument: ${instrument(current.active_instrument)}`), node('p', `Current Game Mode: ${current.game_mode || 'Not selected'}`),
        action('Review Instrument / Game Mode in profile', editProfile));
      receipt(container, 'This week’s already earned Monday bonus', current.monday_bonus);
      if (!current.plan_actions_available) container.append(node('p', 'Weekly plan actions are unavailable. Ordinary practice remains available.'));
      if (current.plan) {
        savedPlan(container, current.plan);
        container.append(node('p', 'Changing your current Instrument or Game Mode does not rewrite this saved plan.'));
        if (stage === 1) container.append(action('View Your New Week', () => refresh('', 2)));
        else {
          tasks(container, current.tasks);
          const assignments = current.plan.instruments;
          if (!assignments.length) container.append(node('p', 'No instrument work started. Start explicitly to save Homework.'));
          if (assignments.length >= 2) container.append(node('p', 'Both instrument slots are saved. Ordinary instrument exploration remains available.'));
          else if (!assignments.some(item => item.instrument === current.active_instrument))
            container.append(action(`Start work for ${instrument(current.active_instrument)}`, () => mutate('start'), !current.plan_actions_available || !current.start_context));
          container.append(node('p', 'Task rewards are claimed separately in Clover Caper. Reading or closing Newsletter awards nothing.'), action('Back to Last Week', () => refresh('', 1)));
        }
      } else {
        container.append(node('p', 'No saved weekly plan. Ordinary practice is available.'));
        if (stage === 1) {
          const label = node('label', 'Weekly Focus'), select = node('select');
          select.id = 'newsletter-focus'; label.htmlFor = select.id;
          const empty = node('option', 'Choose a Focus'); empty.value = ''; select.append(empty);
          foci.forEach(focus => { const option = node('option', focus); option.value = focus; select.append(option); });
          const confirm = action('Confirm Focus and start this week', () => mutate('plan', select.value), true);
          select.addEventListener('change', () => { confirm.dataset.unavailable = String(!current.plan_actions_available || !current.game_mode || !foci.includes(select.value)); confirm.disabled = busy || confirm.dataset.unavailable === 'true'; });
          container.append(label, select, node('p', 'Confirmation saves this Focus and the current Game Mode for the week. Instrument work starts separately.'), confirm);
          if (!current.game_mode) container.append(node('p', 'Choose and save Game Mode in the existing profile control, then reopen Newsletter.'));
        }
      }
      container.append(action('Refresh saved Newsletter', () => refresh('', stage)));
      if (!valid(ticket)) container.replaceChildren();
    }
    async function refresh(message = '', nextStage = stage) {
      if (!active) return;
      const ticket = ++epoch; lock(true); container.replaceChildren(node('p', 'Loading Newsletter…')); status.textContent = message;
      try {
        const result = await request('/weekly/newsletter', ticket);
        if (!valid(ticket)) return;
        data = result; stage = nextStage; render(); heading.focus({preventScroll: true});
      } catch (error) {
        if (!valid(ticket)) return;
        container.replaceChildren(action('Retry Newsletter read', () => refresh('', nextStage))); status.textContent = error.message;
      } finally { if (valid(ticket)) lock(false); }
    }
    async function mutate(kind, focus) {
      if (busy || !valid(epoch) || !data.current?.plan_actions_available || (kind === 'plan' && !foci.includes(focus))) return;
      const ticket = epoch, displayed = data; lock(true); status.textContent = 'Checking your current choices…';
      try {
        const fresh = await request('/weekly/newsletter', ticket);
        if (!valid(ticket)) return;
        if (!fresh.context.available || !fresh.current?.plan_actions_available || fresh.context.week_start !== displayed.context.week_start ||
            fresh.current.active_instrument !== displayed.current.active_instrument || fresh.current.game_mode !== displayed.current.game_mode ||
            fresh.current.start_context !== displayed.current.start_context || fresh.current.plan?.id !== displayed.current.plan?.id)
          throw new Error('Your week or choices changed. Review the refreshed saved state before confirming.');
        status.textContent = 'Saving…';
        if (kind === 'plan') await request('/weekly/plan', ticket, {focus});
        else await request('/weekly/plan/instruments/current/start', ticket, {start_context: displayed.current.start_context});
        if (valid(ticket)) await refresh('Saved state loaded from the server.', 2);
      } catch (error) {
        // The write might have committed. Only a read can resolve uncertainty;
        // a failed recovery read exposes no further mutation controls.
        if (valid(ticket)) await refresh(`${error.message} Checking saved state.`, kind === 'plan' ? 1 : 2);
      }
    }
    return Object.freeze({open() { account = window.WWState.accountRequest(); active = true; stage = 1; return refresh(); },
      close() { active = false; epoch++; data = null; busy = false; container.replaceChildren(); status.textContent = ''; }});
  }
  window.WWNewsletter = Object.freeze({create});
  const opener = document.getElementById('weekly-newsletter-open');
  if (!opener) return;
  const panel = document.getElementById('mum-panel'), care = document.getElementById('mum-care-content');
  const view = document.getElementById('weekly-newsletter-view'), heading = document.getElementById('mum-title');
  const close = document.getElementById('mum-close-button'), originalTitle = heading.textContent;
  const controller = create({container: document.getElementById('weekly-newsletter-content'), status: document.getElementById('weekly-newsletter-status'), heading,
    editProfile() { close.click(); document.getElementById('instrument-object').click(); }});
  function restore() { controller.close(); view.hidden = true; care.hidden = false; heading.textContent = originalTitle; opener.setAttribute('aria-expanded', 'false'); close.setAttribute('aria-label', 'Close Mum’s Corner'); }
  opener.addEventListener('click', () => {
    if (!window.WWSessionBoundary?.isCurrent()) return;
    panel.dispatchEvent(new CustomEvent('woodshed:newsletter-opened'));
    care.hidden = true; view.hidden = false; opener.setAttribute('aria-expanded', 'true'); close.setAttribute('aria-label', 'Close Newsletter'); void controller.open();
  });
  document.getElementById('weekly-newsletter-back').addEventListener('click', () => { restore(); opener.focus(); });
  document.getElementById('weekly-newsletter-close').addEventListener('click', () => close.click());
  panel.addEventListener('woodshed:mum-opened', restore); panel.addEventListener('woodshed:mum-closed', restore);
  window.addEventListener('pagehide', restore); window.addEventListener('ww:session-changed', restore);
})();
