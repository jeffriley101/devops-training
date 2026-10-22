/* Manual, two-stage Newsletter. No wallet, claim or preference writer. */
(function () {
  'use strict';
  const foci = ['Build daily consistency', 'Improve tone and control', 'Learn songs faster', 'Strengthen improvisation'];
  const lanes = [['logging', 'Chart-logging days'], ['practice_minutes', 'Practice Minutes'],
    ['homework', 'Homework'], ['board', 'BOARD Bonus Challenges'], ['thirds', 'Thirds']];
  const node = (tag, text) => { const el = document.createElement(tag); if (text != null) el.textContent = text; return el; };
  const button = (text, action) => { const el = node('button', text); el.type = 'button'; el.className = 'btn btn-secondary'; el.addEventListener('click', action); return el; };
  const duration = value => `${Math.floor(value / 60)} min ${value % 60} sec`;
  const instrument = key => window.WWInstruments?.definitions.find(item => item.key === key)?.label || key || 'Not selected';
  function detail(title) {
    const el = node('details'); el.className = 'newsletter-details'; el.append(node('summary', title)); return el;
  }
  function hero(parent, stage) {
    const el = node('header'); el.className = 'newsletter-hero';
    const art = node('img'); art.src = '/static/img/characters/mum.png'; art.alt = 'Mum'; art.width = 96; art.height = 96;
    const portrait = node('div'); portrait.className = 'newsletter-portrait'; portrait.append(art);
    const words = node('div'); words.append(node('small', `A note from Mum · ${stage} of 2`),
      node('h3', stage === 1 ? 'Hello, musician!' : 'A little practice. A little adventure.'),
      node('p', stage === 1 ? 'Pull up a chair. Let’s find your tune for the week ahead.' : 'Here’s your musical mission. Take it a phrase at a time — I’m cheering you on.'));
    el.append(portrait, words); parent.append(el);
  }
  function meter(parent, label, value, target, text) {
    const line = node('p', text); const bar = node('progress');
    bar.max = Math.max(1, target); bar.value = Math.min(Math.max(0, value), bar.max);
    bar.setAttribute('aria-label', label); bar.setAttribute('aria-valuetext', text);
    parent.append(line, bar);
  }
  function homework(parent, plan) {
    if (!plan?.instruments.length) return;
    const work = node('section'); work.className = 'newsletter-homework'; work.append(node('h3', 'Your Homework'));
    for (const assignment of plan.instruments) {
      const section = node('div'), spec = assignment.homework;
      section.append(node('h4', `${instrument(assignment.instrument)} — saved Homework`));
      if (spec.status === 'saved') {
        const instructions = node('ol'); instructions.append(...spec.instructions.map(text => node('li', text)));
        section.append(node('strong', spec.title), instructions,
          node('small', `Try ${spec.suggested_blocks.count} little sessions of ${spec.suggested_blocks.minutes_each} minutes.`));
      } else section.append(node('p', 'Your saved work is still safe, but we can’t show its instructions here.'));
      work.append(section);
    }
    parent.append(work);
  }
  function tasks(parent, values) {
    if (!values) return;
    const grid = node('div'); grid.className = 'newsletter-goals';
    for (const [lane, label] of lanes) {
      const task = values[lane], card = node('section'), jar = ['logging', 'practice_minutes', 'homework'].includes(lane);
      card.dataset.lane = lane;
      card.append(node('h4', label), node('small', task.claimed ? (jar ? 'Jar claimed' : 'Completed & claimed') : task.qualified ? 'Ready in Clover Caper' : 'Keep going — one step at a time'));
      if (lane === 'logging') meter(card, label, task.progress, task.target, `${task.progress} / ${task.target} days`);
      if (lane === 'practice_minutes') meter(card, label, task.progress, task.target, `${duration(task.progress)} / ${duration(task.target)}`);
      if (lane === 'board') meter(card, label, task.completed, task.target, `${task.completed} / ${task.target} different days`);
      if (lane === 'thirds') meter(card, label, task.authoritative_score ?? 0, task.target, `Best round: ${task.authoritative_score ?? 'None yet'} / ${task.target} in one round`);
      if (lane === 'homework') {
        for (const item of task.assignments) meter(card, instrument(item.instrument), item.practice_seconds, item.target_seconds,
          `${instrument(item.instrument)}: ${duration(item.practice_seconds)} / ${duration(item.target_seconds)}`);
        if (!task.assignments.length) card.append(node('p', 'Start your instrument work below to see your goal.'));
      }
      if (jar) card.append(node('span', task.claimed ? '1 Pickled Carrot jar claimed' : '1 Pickled Carrot jar to earn'));
      grid.append(card);
    }
    parent.append(grid);
  }
  function receipt(parent, label, value) {
    if (value) parent.append(node('p', `${label}: ${value.amount} ${value.reward_type.replaceAll('_', ' ')} earned.`));
  }
  function create({container, status, heading, editProfile, returnToShed = () => {}, caperAvailable = () => false, exploreCaper = () => false}) {
    let active = false, epoch = 0, account, data, busy = false, stage = 1, expectedWeek = null;
    const valid = ticket => active && ticket === epoch && window.WWSessionBoundary?.isCurrent() === true && !!window.WWState.stateForResponse(account);
    function lock(value) { busy = value; container.setAttribute('aria-busy', String(value)); container.querySelectorAll('button,select').forEach(el => { el.disabled = value || el.dataset.unavailable === 'true'; }); }
    async function request(path, ticket, body) {
      if (!valid(ticket)) throw new Error('This session changed. Reopen Newsletter.');
      const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...(body === undefined ? {} : {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
      const payload = await response.json();
      if (path === '/weekly/newsletter' && expectedWeek && payload.context?.week_start !== expectedWeek) throw new Error('The edition changed. Reopen Newsletter manually.');
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
      container.replaceChildren(); heading.textContent = stage === 1 ? 'A note from Mum' : 'Your new week';
      container.dataset.stage = String(stage);
      hero(container, stage);
      if (!data.context.available) {
        container.append(node('p', 'Come back Monday at 13:30 Chicago time for your next note from Mum.')); return;
      }
      const current = data.current;
      if (stage === 1) {
        const recap = data.recap, section = node('section'); section.className = 'newsletter-recap';
        section.append(node('h3', 'Last week in music'),
          node('p', `${duration(recap.practice.practice_seconds)} of practice · ${recap.practice.logging_days} chart-logging days`));
        const achieved = Object.values(recap.receipts).some(Boolean) || recap.recognition.competitions.some(c => c.honors.length);
        if (achieved) { const cheer = node('p', 'Well done — you have something to celebrate!'); cheer.className = 'newsletter-achievement'; section.append(cheer); }
        if (!recap.plan) section.append(node('p', 'No plan last week? Let’s make a fresh start.'));
        section.append(node('p', recap.recognition.status === 'pending' ? 'Competition results are still being wrapped up. Check back later.' : recap.recognition.status === 'finalized' ? 'Your competition results are ready to read below.' : 'We can’t show the competition results here yet.'));
        const notes = detail('Last week’s goals, rewards & results');
        notes.append(node('p', `${data.context.recap_week_start} – ${data.context.recap_week_end}`));
        if (recap.plan) notes.append(node('p', `Last week’s Focus: ${recap.plan.focus}`));
        tasks(notes, recap.tasks); homework(notes, recap.plan);
        for (const [name, value] of Object.entries(recap.receipts)) receipt(notes, name.replaceAll('_', ' '), value);
        for (const competition of recap.recognition.competitions) {
          if (competition.status === 'pending') notes.append(node('p', 'Some competition results are still on their way.'));
          for (const honor of competition.honors) {
            notes.append(node('p', `${honor.contest_key.replaceAll('_', ' ')}: ${honor.medal || 'Recorded honor'} · rank ${honor.rank} · ${honor.subject_type}`));
            for (const reward of honor.rewards) receipt(notes, 'Competition reward', reward);
          }
        }
        notes.append(node('p', 'These totals follow the charts saved for last week; later submissions can change them.'),
          node('p', 'A saved Top 20, Practice Rating history and Director’s Choice report aren’t available here.'));
        section.append(notes); container.append(section, node('h3', 'Find your Focus for this week'));
      }
      const mission = node('section'); mission.className = 'newsletter-mission';
      mission.append(node('p', `Your Instrument: ${instrument(current.active_instrument)} · Game Mode: ${current.game_mode || 'Not selected'}`));
      if (current.plan) {
        mission.append(node('h3', `Your Focus: ${current.plan.focus}`));
        if (stage === 1) {
          mission.append(node('p', 'Your plan is already saved. Let’s see what’s on your music stand.'), action('View Your New Week', () => refresh('', 2)));
        } else mission.append(node('small', `This week’s goals were set for ${current.plan.issued_game_mode}. Changing your current choices won’t change this plan.`));
      } else {
        mission.append(node('p', 'No saved plan yet. Pick one Focus — we’ll build your week around it. Ordinary practice is always open.'));
        if (stage === 1) {
          const label = node('label', 'Weekly Focus'), select = node('select');
          select.id = 'newsletter-focus'; label.htmlFor = select.id;
          const empty = node('option', 'Choose a Focus'); empty.value = ''; select.append(empty);
          foci.forEach(focus => { const option = node('option', focus); option.value = focus; select.append(option); });
          const confirm = action('Confirm Focus and start this week', () => mutate('plan', select.value), true);
          select.addEventListener('change', () => { confirm.dataset.unavailable = String(!current.plan_actions_available || !current.game_mode || !foci.includes(select.value)); confirm.disabled = busy || confirm.dataset.unavailable === 'true'; });
          mission.append(label, select, node('small', 'This saves your Focus and Game Mode for the week. Starting instrument work is a separate choice.'), confirm);
          if (!current.game_mode) mission.append(node('p', 'Choose and save a Game Mode in your profile, then reopen this note.'));
        }
      }
      if (!current.plan_actions_available) mission.append(node('p', 'We can’t start weekly work here right now. Ordinary practice is still open.'));
      const profile = action('Review Instrument / Game Mode in profile', editProfile); profile.classList.add('newsletter-link'); mission.append(profile);
      container.append(mission);
      if (stage === 2 && current.plan) {
        homework(container, current.plan);
        const assignments = current.plan.instruments;
        if (!assignments.length) container.append(node('p', 'Ready for Homework? Start your instrument work to save your instructions.'));
        if (assignments.length >= 2) container.append(node('small', 'Both instrument slots are saved. You can still explore other instruments in ordinary practice.'));
        else if (!assignments.some(item => item.instrument === current.active_instrument))
          container.append(action(`Start work for ${instrument(current.active_instrument)}`, () => mutate('start'), !current.plan_actions_available || !current.start_context));
        container.append(node('h3', 'Five little adventures'), node('p', 'Logging, Practice Minutes and Homework each offer one jar — three Pickled Carrot jars to earn. Finish all five tasks for a clue in Clover Caper.'));
        tasks(container, current.tasks);
        const available = current.plan_actions_available && caperAvailable();
        const guidance = 'Clover Caper isn’t available here right now. Your week is saved, and ordinary practice is still open.';
        container.append(action('Explore Clover Caper', () => {
          if (!data.current.plan_actions_available || !caperAvailable() || exploreCaper() !== true) status.textContent = guidance;
        }, !available));
        if (!available) container.append(node('p', guidance));
      }
      const extras = detail('Week notes & saved rewards');
      extras.append(node('p', `Week of ${data.context.week_start} – ${data.context.week_end}`));
      receipt(extras, 'Monday bonus already earned', current.monday_bonus);
      if (current.plan) extras.append(node('p', `Goals: ${current.plan.targets.logging_days} chart-logging days; ${duration(current.plan.targets.practice_seconds)} practice; ${current.plan.targets.homework_minutes} min Homework; Thirds ${current.plan.targets.thirds} in one round.`));
      extras.append(node('p', 'Earned task rewards are collected in Clover Caper. Reading or closing this note doesn’t collect them.'), action('Refresh saved Newsletter', () => refresh('', stage)));
      if (stage === 2) extras.append(action('Back to Last Week', () => refresh('', 1)));
      container.append(extras);
      if (stage === 2) {
        const finale = node('footer'); finale.className = 'newsletter-finale';
        finale.append(node('p', 'Now go pick up your horn!'), action('Back to SHED', returnToShed)); container.append(finale);
      }
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
        if (valid(ticket)) await refresh('Your week is ready.', 2);
      } catch (error) {
        // The write might have committed. Only a read can resolve uncertainty;
        // a failed recovery read exposes no further mutation controls.
        if (valid(ticket)) await refresh(`${error.message} Checking saved state.`, kind === 'plan' ? 1 : 2);
      }
    }
    return Object.freeze({open(week = null) { expectedWeek = week; account = window.WWState.accountRequest(); active = true; stage = 1; return refresh(); },
      close() { active = false; epoch++; data = null; busy = false; container.replaceChildren(); status.textContent = ''; }});
  }
  window.WWNewsletter = Object.freeze({create});
  const opener = document.getElementById('weekly-newsletter-open');
  if (!opener) return;
  const panel = document.getElementById('mum-panel'), care = document.getElementById('mum-care-content');
  const view = document.getElementById('weekly-newsletter-view'), heading = document.getElementById('mum-title');
  const close = document.getElementById('mum-close-button'), originalTitle = heading.textContent;
  function caperDestination() {
    const button = document.getElementById('weekly-caper-open'), destination = document.getElementById('weekly-caper-view');
    return button && destination && button.isConnected && destination.isConnected && !button.disabled && window.WWSessionBoundary?.isCurrent() ? {button, destination} : null;
  }
  const controller = create({container: document.getElementById('weekly-newsletter-content'), status: document.getElementById('weekly-newsletter-status'), heading,
    editProfile() { close.click(); document.getElementById('instrument-object').click(); },
    returnToShed() { close.click(); },
    caperAvailable: () => !!caperDestination(),
    exploreCaper() {
      const target = caperDestination();
      if (!target) return false;
      target.button.click();
      if (target.destination.hidden || target.button.getAttribute('aria-expanded') !== 'true') return false;
      // Only retire Newsletter after the existing Caper handler opened its view.
      controller.close(); view.hidden = true; panel.classList.remove('newsletter-active'); opener.setAttribute('aria-expanded', 'false');
      return true;
    }});
  function restore() { panel.classList.remove('newsletter-active'); controller.close(); view.hidden = true; care.hidden = false; heading.textContent = originalTitle; opener.setAttribute('aria-expanded', 'false'); close.setAttribute('aria-label', 'Close Mum’s Corner'); }
  function show(week = null) {
    if (!window.WWSessionBoundary?.isCurrent()) return;
    panel.dispatchEvent(new CustomEvent('woodshed:newsletter-opened'));
    panel.classList.add('newsletter-active');
    care.hidden = true; view.hidden = false; opener.setAttribute('aria-expanded', 'true'); close.setAttribute('aria-label', 'Close Newsletter'); void controller.open(week);
  }
  opener.addEventListener('click', () => show());
  document.getElementById('weekly-newsletter-back').addEventListener('click', () => { restore(); opener.focus(); });
  document.getElementById('weekly-newsletter-close').addEventListener('click', () => close.click());
  panel.addEventListener('woodshed:mum-opened', restore); panel.addEventListener('woodshed:mum-closed', restore);
  window.WWNewsletter = Object.freeze({create, openAutomatic(week) {
    if (!window.WWSessionBoundary?.isCurrent() || window.WWSurfaces.current() || document.querySelector('dialog[open]')) return false;
    const mum = document.getElementById('mum-open-button');
    mum.focus({preventScroll: true}); mum.click();
    if (panel.hidden || !window.WWSessionBoundary.isCurrent()) return false;
    show(week);
    return true;
  }});
  window.addEventListener('pagehide', restore); window.addEventListener('ww:session-changed', restore);
})();
