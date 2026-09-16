const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function stateApi() {
  const context = { document: { getElementById: () => null }, window: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/state.js'), 'utf8'), context);
  return context.window.WWState;
}

test('server earning/purchase snapshots update balance and sync revision together', () => {
  const api = stateApi();
  const state = { account: { serverRevision: 4 }, progress: { credits: 10 }, preference: 'keep' };
  api.applyEconomy(state, { credits: 16, state_revision: 5 });
  assert.equal(state.progress.credits, 16);
  assert.equal(state.account.serverRevision, 5);
  api.applyEconomy(state, { dandelion_balance: 6, state_revision: 6 });
  assert.equal(state.progress.credits, 6);
  assert.equal(state.account.serverRevision, 6);
  assert.equal(state.preference, 'keep');
});

test('delayed reward/sync responses cannot replace a newer purchase snapshot', () => {
  const api = stateApi();
  const state = { account: { serverRevision: 8 }, progress: { credits: 5 } };
  api.applyEconomy(state, { credits: 100, state_revision: 7 });
  api.applyEconomy(state, { credits: 100, revision: 6 });
  api.applyEconomy(state, { credits: 999999 });
  assert.equal(state.progress.credits, 5);
  assert.equal(state.account.serverRevision, 8);
  api.applyEconomy(state, { credits: 5, revision: 9 });
  assert.equal(state.account.serverRevision, 9);
});

function browserHarness() {
  const storage = new Map();
  const events = [];
  const context = {
    document: { getElementById: () => null, querySelectorAll: () => [] },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    window: {
      localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
      dispatchEvent: event => events.push(event),
      sessionStorage: { removeItem() {} },
      setTimeout() {},
    },
  };
  vm.createContext(context);
  const sourceRoot = path.join(__dirname, '../static/js');
  vm.runInContext(fs.readFileSync(path.join(sourceRoot, 'state.js'), 'utf8'), context);
  const api = context.window.WWState;
  const initial = api.getState();
  initial.account = {woodchuckId: 'WC-A', authenticated: true, serverRevision: 10};
  initial.progress.credits = 20;
  api.saveState(initial, {sync: false});
  return { api, context, events, sourceRoot };
}

function pendingResponse() {
  let deliver;
  const promise = new Promise(resolve => { deliver = resolve; });
  return {promise, deliver};
}

function boardHandlers(browser) {
  const { api, context, sourceRoot } = browser;
  const responses = {care: pendingResponse(), marching: pendingResponse()};
  const handlers = {};
  Object.assign(context, {
    stateApi: api, hoursCheckbox: null, triviaForm: null,
    careButton: {addEventListener: (_event, handler) => { handlers.care = handler; }},
    marchingButton: {textContent: 'Ready', disabled: false,
      addEventListener: (_event, handler) => { handlers.marching = handler; }},
    feedbackEl: {classList: {remove() {}, add() {}}}, marchingActivity: {},
    prepareCurrentDay: state => state,
    persistCampPoint: kind => responses[kind].promise,
    awardContest: (state, kind) => {
      if (!state.bandCamp.daily.awarded.includes(kind)) state.bandCamp.daily.awarded.push(kind);
    },
    hasAward: (state, kind) => state.bandCamp.daily.awarded.includes(kind),
    playCampReward() {}, playSound() {}, renderBoard() {}, hydrateHome() {},
  });
  const source = fs.readFileSync(path.join(sourceRoot, 'app.js'), 'utf8');
  const start = source.indexOf('    if (careButton) {');
  const end = source.indexOf('\n  function ', start);
  // Execute the real care/marching registration blocks with isolated DOM/network edges.
  vm.runInContext(source.slice(start, end).replace(/\n  }\s*$/, ''), context);
  return { handlers, responses };
}

function bookHandler(browser) {
  const {api, context, sourceRoot} = browser;
  const response = pendingResponse();
  let submit;
  Object.assign(context, {
    stateApi: api, submissionInFlight: false, confirmationApproved: true,
    errorEl: {textContent: ''}, feedbackEl: {classList: {remove() {}, add() {}}},
    form: {addEventListener: (_event, handler) => {submit = handler;}},
    dateEl: {value: '2026-09-15'}, minutesEl: {value: '10'}, noteEl: {value: 'Practice'},
    practiceDetailEls: [], verifierSelectEl: null, includeContestsEl: null,
    includeTeamEl: null, currentTeam: null, finalDialog: null, submitBtn: null,
    pendingSubmissionKey: 'test-chart', calculateDandelionsForPractice: () => 2,
    createPersistentPracticeChart: () => response.promise,
    playNewCrownIfConfirmed() {}, playNewMedalIfConfirmed() {}, celebrateSuccess() {}, playSound() {},
    renderEntries() {}, renderPBookSummary() {}, loadPersistentPracticeCharts: async () => {},
    loadPracticeTotals: async () => {}, updateSubmitGlow() {}, hydrateHome() {},
  });
  const source = fs.readFileSync(path.join(sourceRoot, 'app.js'), 'utf8');
  const start = source.indexOf('    form.addEventListener("submit", async function (event)', source.indexOf('  function wirePBook'));
  const end = source.indexOf('\n  }\n\n  const state =', start);
  vm.runInContext(source.slice(start, end), context);
  return { submit: () => submit({preventDefault() {}}), response };
}

test('actual Board saves: newer response first preserves its funds and intervening edits', async () => {
  const browser = browserHarness();
  const {handlers, responses} = boardHandlers(browser);
  const older = handlers.care();
  const newer = handlers.marching();
  responses.marching.deliver({created: true, credits: 22, state_revision: 12});
  await newer;
  const edited = browser.api.getState();
  edited.profile.goal = 'Changed while care was pending';
  browser.api.saveState(edited);
  responses.care.deliver({created: true, credits: 21, state_revision: 11});
  await older;
  const saved = browser.api.getState();
  assert.equal(saved.progress.credits, 22);
  assert.equal(saved.account.serverRevision, 12);
  assert.equal(saved.profile.goal, 'Changed while care was pending');
  assert.equal(saved.bandCamp.daily.marchingComplete, true);
  assert.equal(saved.bandCamp.daily.careComplete, true);
  assert.equal(browser.context.feedbackEl.textContent.includes('could not'), false);
  assert.equal(browser.events.filter(event => event.type === 'ww:state-saved').length, 3);
});

test('actual BOOK save cannot overwrite a newer Board response or user changes', async () => {
  const browser = browserHarness();
  const book = bookHandler(browser);
  const oldBook = book.submit();
  const {handlers, responses} = boardHandlers(browser);
  const board = handlers.care();
  responses.care.deliver({created: true, credits: 23, state_revision: 12});
  await board;
  const edited = browser.api.getState();
  edited.profile.goal = 'Keep this goal';
  edited.progress.streak = 8;
  browser.api.saveState(edited);
  book.response.deliver({created: false, chart: {id: 1, credits_awarded: 2}, credits: 22, state_revision: 11, streak: 3});
  await oldBook;
  const saved = browser.api.getState();
  assert.equal(browser.context.errorEl.textContent, '');
  assert.equal(saved.progress.credits, 23);
  assert.equal(saved.account.serverRevision, 12);
  assert.equal(saved.progress.streak, 8);
  assert.equal(saved.profile.goal, 'Keep this goal');
  assert.equal(saved.bandCamp.daily.careComplete, true);
});

for (const kind of ['logout', 'switch', 'logout-and-return']) {
  for (const handler of ['Board', 'BOOK']) {
    test(`${handler} pending response cannot cross ${kind}`, async () => {
      const browser = browserHarness();
      let outstanding, deliver;
      if (handler === 'Board') {
        const board = boardHandlers(browser);
        outstanding = board.handlers.care();
        deliver = board.responses.care.deliver;
      } else {
        const book = bookHandler(browser);
        outstanding = book.submit();
        deliver = book.response.deliver;
      }
      let changed = browser.api.getState();
      changed.account.authenticated = false;
      browser.api.saveState(changed, {sync: false});
      if (kind !== 'logout') {
        changed = browser.api.getState();
        changed.account.authenticated = true;
        changed.account.woodchuckId = kind === 'switch' ? 'WC-B' : 'WC-A';
        changed.account.serverRevision = 1;
        changed.progress.credits = 7;
        changed.profile.goal = 'New session goal';
        browser.api.saveState(changed, {sync: false});
      }
      const expected = JSON.stringify(browser.api.getState());
      deliver({created: false, chart: {id: 1, credits_awarded: 2}, credits: 99, state_revision: 99});
      await outstanding;
      assert.equal(JSON.stringify(browser.api.getState()), expected);
    });
  }
}

function syncHandler(browser, fetch) {
  browser.context.fetch = fetch;
  browser.context.console = {warn() {}};
  browser.context.window.addEventListener = () => {};
  browser.context.window.clearTimeout = () => {};
  browser.context.window.alert = () => {};
  browser.context.window.location = {reload() {}};
  const source = fs.readFileSync(path.join(browser.sourceRoot, 'account.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('\n(function () {') + 1), browser.context);
  return browser.context.window.WWAccountSync.syncNow;
}

function response(payload, status = 200) {
  return {ok: status === 200, status, json: async () => payload};
}

test('actual account sync response preserves a newer saved economy and user edit', async () => {
  const browser = browserHarness();
  const pending = pendingResponse();
  const sync = syncHandler(browser, () => pending.promise);
  const outstanding = sync();
  const newer = browser.api.getState();
  newer.progress.credits = 30;
  newer.account.serverRevision = 12;
  newer.profile.goal = 'Newest goal';
  browser.api.saveState(newer, {sync: false});
  pending.deliver(response({credits: 21, revision: 11}));
  await outstanding;
  assert.equal(browser.api.getState().progress.credits, 30);
  assert.equal(browser.api.getState().account.serverRevision, 12);
  assert.equal(browser.api.getState().profile.goal, 'Newest goal');
});

for (const status of [200, 401, 409]) {
  test(`actual account sync ${status} cannot change a switched account`, async () => {
    const browser = browserHarness();
    const pending = pendingResponse();
    const sync = syncHandler(browser, () => pending.promise);
    const outstanding = sync();
    const other = browser.api.getState();
    other.account.woodchuckId = 'WC-B';
    other.account.serverRevision = 1;
    other.progress.credits = 7;
    browser.api.saveState(other, {sync: false});
    const expected = JSON.stringify(browser.api.getState());
    pending.deliver(response({credits: 999, revision: 99}, status));
    await outstanding;
    assert.equal(JSON.stringify(browser.api.getState()), expected);
  });
}

test('actual conflict recovery GET cannot restore the previous account after logout', async () => {
  const browser = browserHarness();
  const pending = pendingResponse();
  const oldState = browser.api.getState();
  let calls = 0;
  const sync = syncHandler(browser, () => ++calls === 1 ? Promise.resolve(response({}, 409)) : pending.promise);
  const outstanding = sync();
  await new Promise(setImmediate);
  assert.equal(calls, 2);
  browser.api.resetState();
  const expected = JSON.stringify(browser.api.getState());
  pending.deliver(response({state: oldState, revision: 11}));
  await outstanding;
  assert.equal(JSON.stringify(browser.api.getState()), expected);
});

test('actual Arcade status saves keep newest balance and ignore switched-account responses', async () => {
  const browser = browserHarness();
  const pending = [pendingResponse(), pendingResponse(), pendingResponse()];
  let calls = 0;
  browser.context.fetch = () => pending[calls++].promise;
  vm.runInContext(fs.readFileSync(path.join(browser.sourceRoot, 'arcade-economy.js'), 'utf8'), browser.context);
  const economy = browser.context.window.WoodshedArcadeEconomy;
  const old = economy.loadStatus('blue');
  const newer = economy.loadStatus('blue');
  pending[1].deliver(response({balance: 25, state_revision: 12}));
  await newer;
  pending[0].deliver(response({balance: 20, state_revision: 10}));
  await old;
  assert.equal(browser.api.getState().progress.credits, 25);
  assert.equal(browser.api.getState().account.serverRevision, 12);
  const beforeSwitch = economy.loadStatus('blue');
  const other = browser.api.getState();
  other.account.woodchuckId = 'WC-B';
  other.account.serverRevision = 1;
  other.progress.credits = 7;
  browser.api.saveState(other, {sync: false});
  pending[2].deliver(response({balance: 99, state_revision: 99}));
  await beforeSwitch;
  assert.equal(browser.api.getState().progress.credits, 7);
  assert.equal(browser.api.getState().account.serverRevision, 1);
});
