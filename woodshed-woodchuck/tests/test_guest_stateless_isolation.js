// Synthetic browser contexts only; no server, account credentials or network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, '../static/js', name), 'utf8');
const STATE = 'woodshedWoodchuckState.v1';
const EPOCH = 'woodshed:session-change:v1';
const DRAFT = 'woodshed:p-book:verifier-draft:v1';
const TIMER = 'woodshed:practice-timer-started-at';

function events(target = {}) {
  const listeners = new Map();
  target.addEventListener = (name, fn, options = {}) => {
    if (!listeners.has(name)) listeners.set(name, []);
    listeners.get(name).push({fn, once: options.once === true});
  };
  target.removeEventListener = (name, fn) => {
    listeners.set(name, (listeners.get(name) || []).filter(item => item.fn !== fn));
  };
  target.dispatchEvent = event => {
    for (const item of [...(listeners.get(event.type) || [])]) {
      if (item.once) target.removeEventListener(event.type, item.fn);
      item.fn(event);
    }
  };
  return target;
}
function element() {
  return events({dataset: {}, value: '', textContent: '', hidden: false, disabled: false,
    classList: {add() {}, remove() {}, toggle() {}}, setAttribute() {}, focus() {},
    scrollIntoView() {}, appendChild() {}, append() {}, replaceChildren() {},
    querySelector: () => null, querySelectorAll: () => []});
}
function storage(map) {
  return new Proxy({getItem: key => map.get(key) ?? null,
    setItem: (key, value) => map.set(key, String(value)), removeItem: key => map.delete(key)}, {
    ownKeys: () => [...map.keys()], getOwnPropertyDescriptor: (target, key) =>
      Reflect.getOwnPropertyDescriptor(target, key) || {enumerable: true, configurable: true}
  });
}
function queuedLocks() {
  const waiting = [];
  let held = false;
  function next() {
    if (held || waiting.length === 0) return;
    held = true;
    const item = waiting.shift();
    Promise.resolve().then(item.fn).then(item.resolve, item.reject).finally(() => {
      held = false;
      next();
    });
  }
  return {request(name, options, callback) {
    assert.equal(name, 'woodshed-account-transition');
    return new Promise((resolve, reject) => {
      waiting.push({fn: callback || options, resolve, reject});
      next();
    });
  }};
}
function guestForm(action = '/guest/discard') {
  const form = Object.assign(element(), {action, method: 'post', target: '', submissions: 0});
  form.submit = () => {form.submissions++;};
  return form;
}
const nextTurn = () => new Promise(resolve => setImmediate(resolve));
async function browser({account = '', generation = '', guest = 'transition',
  shared = new Map(), session = new Map(), unavailable = false,
  locks = {request: async (_name, options, fn) => (fn || options)()}} = {}) {
  const ids = new Map(), requests = [], intervals = new Map();
  let timerNumber = 0, clock = 1000;
  const body = element(), shell = element();
  body.dataset = {accountId: account, pageGeneration: generation, guest};
  if (account && generation) ids.set('account-scripts', {content: {querySelectorAll: () => []}});
  const local = storage(shared);
  if (unavailable) {
    local.getItem = () => {throw new Error('Storage unavailable');};
    local.setItem = () => {throw new Error('Storage unavailable');};
  }
  const window = events({localStorage: local, sessionStorage: storage(session),
    location: {href: 'https://synthetic.invalid/guest', origin: 'https://synthetic.invalid', pathname: '/guest', assign() {}},
    fetch: async (url, init = {}) => {
      requests.push({url, init});
      return new Response(JSON.stringify(url === '/account/me'
        ? {authenticated: true, profile: {woodchuck_id: account}, page_generation: generation}
        : {charts: [], verifiers: [], teams: []}), {headers: {'Content-Type': 'application/json'}});
    }, setTimeout: () => ++timerNumber, clearTimeout() {},
    setInterval: fn => {intervals.set(++timerNumber, fn); return timerNumber;},
    clearInterval: id => intervals.delete(id), alert() {}});
  const document = events({body, hidden: false, getElementById: id => ids.get(id) || null,
    querySelector: selector => selector === '.app-shell' ? shell : null,
    querySelectorAll: () => [], createElement: element});
  const context = {window, document, navigator: {locks},
    URL, Headers, Response, console, performance: {now: () => clock},
    CustomEvent: class {constructor(type, init = {}) {this.type = type; this.detail = init.detail;}}};
  context.fetch = (...args) => window.fetch(...args);
  vm.createContext(context);
  const load = name => vm.runInContext(source(name), context);
  load('session-boundary.js');
  await window.WWSessionBoundary.ready;
  return {window, context, document, ids, shared, session, requests, intervals, load,
    setClock: value => {clock = value;}};
}
function accountState(id) {
  return {account: {woodchuckId: id, authenticated: true, serverRevision: 1},
    profile: {woodchuckName: id, instrument: 'Flute', level: 'Beginner', goal: 'Daily'},
    progress: {credits: 37}, practiceLog: [{note: 'Saved account history'}]};
}
function wirePBook(b) {
  for (const id of ['p-book-form', 'p-book-date', 'p-book-minutes', 'p-book-note', 'p-book-error',
    'p-book-feedback', 'p-book-verifier-manage', 'practice-timer-display', 'practice-timer-toggle-btn',
    'practice-timer-start-btn', 'practice-timer-stop-btn']) b.ids.set(id, element());
  b.ids.get('p-book-form').querySelector = () => element();
  b.context.stateApi = b.window.WWState;
  const app = source('app.js');
  vm.runInContext(app.slice(app.indexOf('  function wirePBook('), app.indexOf('  const state = ensureTodayQuest(')) +
    '\nwirePBook(stateApi.getState());', b.context);
}

test('anonymous welcome and setup state never reads prior account products or writes a scaffold', async () => {
  for (const shared of [new Map(), new Map([[STATE, JSON.stringify(accountState('WC-A'))]])]) {
    const before = [...shared];
    const b = await browser({shared}); b.load('state.js');
    const fresh = b.window.WWState.getState();
    assert.equal(fresh.account.woodchuckId, '');
    assert.equal(fresh.practiceLog.length, 0);
    fresh.practiceLog.push({note: 'Anonymous entry'});
    assert.equal(b.window.WWState.saveState(fresh), false);
    assert.deepEqual([...shared], before);
  }
});

test('verified server bootstrap restores legacy account state but rejects a conflicting embedded identity', async () => {
  for (const [state, expectedId] of [
    [{profile: {instrument: 'Flute'}, progress: {credits: 500}}, 'WC-A'],
    [accountState('WC-B'), '']
  ]) {
    const b = await browser({account: 'WC-A', generation: 'verified-A-session'});
    b.ids.set('account-state-bootstrap', {textContent: JSON.stringify({state, revision: 7})});
    b.load('state.js');
    const restored = b.window.WWState.getState();
    assert.equal(restored.account.woodchuckId, expectedId);
    assert.equal(restored.account.authenticated, Boolean(expectedId));
    if (expectedId) {
      assert.equal(restored.profile.instrument, 'Flute');
      assert.equal(restored.progress.credits, 500);
      assert.equal(restored.account.serverRevision, 7);
      assert.equal(JSON.parse(b.shared.get(STATE)).account.woodchuckId, 'WC-A');
    } else assert.equal(b.shared.has(STATE), false);
  }
  const anonymous = await browser();
  anonymous.ids.set('account-state-bootstrap', {textContent: JSON.stringify({state: accountState('WC-A'), revision: 7})});
  anonymous.load('state.js');
  assert.equal(anonymous.window.WWState.getState().account.woodchuckId, '');
  assert.equal(anonymous.shared.has(STATE), false);
});

test('confirmed account cache cleanup removes only the named products and preserves the security epoch', async () => {
  const shared = new Map([[EPOCH, '8'], [STATE, 'account data'],
    ['woodshedWoodchuckConflictBackup.1', 'old account data'], ['woodshed.plungeBurrow.bestScore', '999'],
    ['woodshedWoodchuckMetronomeBpm', '88'],
    ['woodshed.soundEffects.volume', '0.35'], ['unrelated', 'keep']]);
  const session = new Map([[DRAFT, 'draft'], [TIMER, '123'], ['woodshed:arcade-start:old:plunge', 'retry'],
    ['woodshed:world-entry-arrival:v1', 'arrival'], ['unrelated', 'keep']]);
  const b = await browser({account: 'WC-A', generation: 'A-session', shared, session});
  b.window.WWSessionBoundary.clearAccountProductCaches();
  assert.deepEqual([...shared], [[EPOCH, '8'], ['woodshed.soundEffects.volume', '0.35'], ['unrelated', 'keep']]);
  assert.deepEqual([...session], [['unrelated', 'keep']]);
  shared.set(STATE, 'new account data'); shared.set(EPOCH, '9');
  assert.throws(() => b.window.WWSessionBoundary.clearAccountProductCaches(), /Sign-in changed/);
  assert.equal(shared.get(STATE), 'new account data');
});

test('account change stops stale tab writes and clears its own drafts without removing the new account cache', async () => {
  const shared = new Map([[STATE, JSON.stringify(accountState('WC-A'))]]);
  const b = await browser({account: 'WC-A', generation: 'A-session', shared}); b.load('state.js');
  const old = b.window.WWState.getState();
  b.session.set(DRAFT, 'A draft'); b.session.set(TIMER, 'A timer');
  shared.set(STATE, JSON.stringify(accountState('WC-B'))); shared.set(EPOCH, '1');
  b.window.dispatchEvent({type: 'storage', key: EPOCH});
  assert.equal(b.session.has(DRAFT), false); assert.equal(b.session.has(TIMER), false);
  assert.equal(b.window.WWState.saveState(old), false);
  assert.equal(JSON.parse(shared.get(STATE)).account.woodchuckId, 'WC-B');
});

test('P-Book keeps a same-session draft but rejects other accounts and earlier sessions after tab reload', async () => {
  const origin = await browser({account: 'WC-A', generation: 'A-session'});
  const binding = origin.window.WWSessionBoundary.binding();
  for (const [account, generation, epoch, expected, storedBinding = binding] of [
    ['WC-A', 'A-session', '0', 'A private note'], ['WC-B', 'B-session', '0', ''],
    ['WC-A', 'A-new-session', '0', ''], ['WC-A', 'A-session', '2', ''],
    ['WC-A', 'A-session', '0', '', null]
  ]) {
    const shared = new Map([[EPOCH, epoch], [STATE, JSON.stringify(accountState(account))]]);
    const session = new Map([[DRAFT, JSON.stringify({binding: storedBinding, savedAt: Date.now(), minutes: '25', note: 'A private note'})],
      [TIMER, JSON.stringify({binding: storedBinding, startedAt: Date.now() - 60000})]]);
    const b = await browser({account, generation, shared, session}); b.load('state.js'); wirePBook(b);
    assert.equal(b.ids.get('p-book-note').value, expected);
    assert.equal(b.intervals.size, expected ? 1 : 0);
    assert.equal(b.session.has(TIMER), Boolean(expected));
  }
});

test('P-Book pagehide and delayed callbacks cannot recreate A draft or timer after another account signs in', async () => {
  const shared = new Map([[STATE, JSON.stringify(accountState('WC-A'))]]);
  const b = await browser({account: 'WC-A', generation: 'A-session', shared}); b.load('state.js'); wirePBook(b);
  b.ids.get('p-book-note').value = 'A private note';
  b.ids.get('p-book-verifier-manage').dispatchEvent({type: 'click'});
  b.ids.get('practice-timer-start-btn').dispatchEvent({type: 'click'});
  assert.equal(b.session.has(DRAFT), true); assert.equal(b.session.has(TIMER), true);
  shared.set(EPOCH, '1');
  b.window.dispatchEvent({type: 'pagehide'});
  b.ids.get('p-book-verifier-manage').dispatchEvent({type: 'click'});
  b.ids.get('practice-timer-start-btn').dispatchEvent({type: 'click'});
  b.window.dispatchEvent({type: 'pageshow', persisted: true});
  assert.equal(b.session.has(DRAFT), false); assert.equal(b.session.has(TIMER), false);
  assert.equal(b.intervals.size, 0);
});

test('Guest metronome stores nothing and resets both tempo and tap history on discard and navigation', async () => {
  for (const unavailable of [false, true]) {
    const shared = new Map([['woodshedWoodchuckMetronomeBpm', '88'], ['woodshed:guest:v1:metronome-bpm', '160']]);
    const before = [...shared];
    const b = await browser({guest: 'local', unavailable, shared});
    b.window.WWGuest = {isCurrent: () => true};
    for (const id of ['metronome-open-button', 'metronome-panel', 'metronome-start-button',
      'metronome-tap-button', 'metronome-bpm-range', 'metronome-bpm-input', 'metronome-status']) b.ids.set(id, element());
    b.load('app.js');
    assert.equal(b.window.WWGuestToolsReady, true);
    const bpm = b.ids.get('metronome-bpm-input');
    assert.equal(Number(bpm.value), 120);
    bpm.value = '132'; bpm.dispatchEvent({type: 'change'});
    b.setClock(1000); b.ids.get('metronome-tap-button').dispatchEvent({type: 'click'});
    b.setClock(1400); b.ids.get('metronome-tap-button').dispatchEvent({type: 'click'});
    assert.equal(Number(bpm.value), 150);
    b.window.dispatchEvent({type: 'ww:guest-discarded'});
    b.setClock(1600); b.ids.get('metronome-tap-button').dispatchEvent({type: 'click'});
    assert.equal(Number(bpm.value), 120);
    bpm.value = '132'; bpm.dispatchEvent({type: 'change'});
    b.window.dispatchEvent({type: 'pagehide'}); assert.equal(Number(bpm.value), 120);
    assert.deepEqual([...shared], before);
    await assert.rejects(b.window.fetch('/account/state'), /local/);
    assert.equal(b.requests.length, 0);
  }
});

test('discarded Guest controls cannot change tempo or start audio even while the security boundary remains current', async () => {
  const b = await browser({guest: 'local'});
  let active = true, audioStarts = 0, microphoneStarts = 0, finishPermission;
  b.window.WWGuest = {isCurrent: () => active};
  b.window.WWTuner = {detectPitch: () => null, median: () => null};
  b.window.AudioContext = class {
    constructor() {audioStarts++; this.state = 'running';}
    close() {this.state = 'closed'; return Promise.resolve();}
  };
  b.context.navigator.mediaDevices = {getUserMedia: () => {
    microphoneStarts++;
    return new Promise(resolve => {finishPermission = resolve;});
  }};
  for (const id of ['metronome-open-button', 'metronome-panel', 'metronome-start-button',
    'metronome-tap-button', 'metronome-faster-button', 'metronome-bpm-range', 'metronome-bpm-input',
    'metronome-status', 'tuner-open-button', 'tuner-close-button', 'tuner-panel', 'tuner-note',
    'tuner-diagnosis']) b.ids.set(id, element());
  b.ids.get('tuner-panel').hidden = true;
  b.load('app.js');
  for (const end of ['ww:guest-discarded', 'pagehide']) {
    active = false;
    b.window.dispatchEvent({type: end});
    assert.equal(b.window.WWSessionBoundary.isCurrent(), true);
    b.ids.get('metronome-faster-button').dispatchEvent({type: 'click'});
    b.ids.get('metronome-tap-button').dispatchEvent({type: 'click'});
    b.ids.get('metronome-start-button').dispatchEvent({type: 'click'});
    b.ids.get('tuner-open-button').dispatchEvent({type: 'click'});
    assert.equal(Number(b.ids.get('metronome-bpm-input').value), 120);
    assert.equal(audioStarts, 0); assert.equal(microphoneStarts, 0);
  }
  // A permission prompt begun during an active session also releases its late stream.
  active = true;
  b.ids.get('tuner-open-button').dispatchEvent({type: 'click'});
  assert.equal(microphoneStarts, 1);
  active = false;
  b.window.dispatchEvent({type: 'pagehide'});
  const track = {readyState: 'live', stop() {this.readyState = 'ended';}};
  finishPermission({getTracks: () => [track]});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(track.readyState, 'ended');
  assert.equal(b.ids.get('tuner-panel').hidden, true);
});

test('native Guest context form keeps account transitions queued until response navigation leaves the page', async () => {
  for (const action of ['/guest/discard', '/guest/secret-symbol']) {
    const locks = queuedLocks(), shared = new Map([[EPOCH, '8']]);
    const guest = await browser({guest: 'local', locks, shared});
    const login = await browser({locks, shared});
    const form = guestForm(action);
    const navigation = guest.window.WWSessionBoundary.submitGuestForm(form);
    await nextTurn();
    assert.equal(form.submissions, 1);
    assert.equal(shared.get(EPOCH), '8');
    const authentication = login.window.fetch('/account/login', {method: 'POST'});
    await nextTurn();
    assert.equal(login.requests.length, 0);
    guest.window.dispatchEvent({type: 'pagehide'});
    await navigation;
    await authentication;
    assert.equal(login.requests.length, 1);
    assert.equal(shared.get(EPOCH), '10');
    assert.equal(guest.requests.length, 0);
  }
});

test('Guest context forms allow only exact same-origin POST routes with available security controls', async () => {
  for (const changes of [{method: 'get'}, {action: '/practice-charts'},
    {action: 'https://elsewhere.invalid/guest/discard'}, {action: '/guest/discard?extra=1'},
    {action: '/guest/discard#extra'}, {target: '_blank'}]) {
    const b = await browser({guest: 'local'}), form = Object.assign(guestForm(), changes);
    await assert.rejects(b.window.WWSessionBoundary.submitGuestForm(form), /Reload Guest tools/);
    assert.equal(form.submissions, 0);
  }
  for (const options of [{guest: 'transition'}, {guest: 'local', unavailable: true},
    {guest: 'local', locks: undefined}]) {
    const b = await browser(options), form = guestForm();
    if ('locks' in options) b.context.navigator.locks = undefined;
    await assert.rejects(b.window.WWSessionBoundary.submitGuestForm(form), /Reload Guest tools|Web Locks/);
    assert.equal(form.submissions, 0);
  }
});

test('queued Guest form rechecks current session and form after acquiring the transition lock', async () => {
  for (const invalidate of ['epoch', 'form', 'pagehide']) {
    const locks = queuedLocks(), shared = new Map();
    const b = await browser({guest: 'local', locks, shared});
    let release;
    const blocker = locks.request('woodshed-account-transition', () => new Promise(resolve => {release = resolve;}));
    await nextTurn();
    const form = guestForm();
    const navigation = b.window.WWSessionBoundary.submitGuestForm(form);
    const rejected = assert.rejects(navigation, /Sign-in changed|Reload Guest tools/);
    if (invalidate === 'epoch') shared.set(EPOCH, '1');
    if (invalidate === 'form') form.action = '/account/create';
    if (invalidate === 'pagehide') b.window.dispatchEvent({type: 'pagehide'});
    release();
    await blocker;
    await rejected;
    assert.equal(form.submissions, 0);
  }
});
