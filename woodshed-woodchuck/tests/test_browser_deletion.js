const assert = require('node:assert/strict');
const {createHash, webcrypto} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = name => fs.readFileSync(path.join(__dirname, '../static/js', name), 'utf8');
const target = 'WC-DELETED';
const other = 'WC-KEEP';
const targetEntry = 'a'.repeat(64);
const otherEntry = 'b'.repeat(64);
const salt = '0'.repeat(32);
const digest = value => createHash('sha256').update(`${salt}:${value}`).digest('hex');
const receipt = JSON.stringify({salt, account: digest(target), entry: digest(targetEntry)});
const draft = 'woodshed:p-book:verifier-draft:v1';
const timer = 'woodshed:practice-timer-started-at';
const state = 'woodshedWoodchuckState.v1';
const marker = 'woodshed:account-deletion:v1';
const backup = id => `woodshedWoodchuckConflictBackup.${id}`;
const arcade = id => `woodshed:arcade-start:${JSON.stringify([id, true])}:history-mystery`;
const cache = id => JSON.stringify({account: {authenticated: true, woodchuckId: id}, practiceLog: ['synthetic']});
const bound = accountId => JSON.stringify({binding: {accountId}, note: 'synthetic note'});

function storage(values, emit = () => {}) {
  return new Proxy({
    getItem: key => values.get(key) ?? null,
    setItem(key, value) {
      const oldValue = values.get(key) ?? null;
      values.set(key, String(value));
      if (oldValue !== String(value)) emit({key, oldValue, newValue: String(value)});
    },
    removeItem(key) {
      const oldValue = values.get(key) ?? null;
      if (values.delete(key)) emit({key, oldValue, newValue: null});
    },
  }, {
    ownKeys: () => [...values.keys()],
    getOwnPropertyDescriptor: () => ({enumerable: true, configurable: true}),
  });
}

function browserCluster(initial) {
  const values = new Map(initial);
  const tabs = [];
  return {values, open({accountId = '', jsonReceipt, tabValues = new Map(), indexedDB, crypto = webcrypto} = {}) {
    const handlers = new Map();
    const shell = {hidden: false};
    const body = {dataset: {accountId}, appendChild() {}};
    const locks = [];
    const tab = {
      deliver(event) { for (const handler of handlers.get(event.type) || []) handler(event); },
      tabValues, shell, locks,
    };
    tabs.push(tab);
    const window = {
      localStorage: storage(values, event => {
        for (const otherTab of tabs) {
          if (otherTab !== tab) setImmediate(() => otherTab.deliver({type: 'storage', ...event}));
        }
      }),
      sessionStorage: storage(tabValues), indexedDB, crypto, fetch: async () => { throw new Error('Network not expected'); },
      addEventListener(type, handler) { handlers.set(type, [...(handlers.get(type) || []), handler]); },
      dispatchEvent: event => tab.deliver(event),
    };
    const context = vm.createContext({window, TextEncoder, queueMicrotask,
      navigator: {locks: {request: async (key, remove) => {locks.push(key); await remove();}}},
      document: {body, querySelector: () => shell,
        getElementById: name => name === 'deletion-browser-receipt' && jsonReceipt ? {textContent: jsonReceipt} : null,
        createElement: () => ({setAttribute() {}, appendChild() {}})},
      CustomEvent: class { constructor(type) { this.type = type; } },
    });
    vm.runInContext(source('session-boundary.js'), context);
    return Object.assign(tab, {window, load: name => vm.runInContext(source(name), context)});
  }};
}

function wheel(records) {
  const names = [];
  return {names, open(name) {
    names.push(name);
    const request = {};
    request.result = {
      objectStoreNames: { contains: (name) => name === 'pending-actions' },
      transaction(name) {
        assert.equal(name, 'pending-actions');
        const tx = {objectStore: () => ({
          getAllKeys: () => ({result: [...records.keys()]}),
          delete: key => ({result: records.delete(key)}),
        })};
        setImmediate(() => tx.oncomplete());
        return tx;
      },
      close() {},
    };
    setImmediate(() => request.onsuccess());
    return request;
  }};
}

async function until(done) {
  for (let tries = 0; tries < 100; tries += 1) {
    if (done()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail('Expected the browser cleanup to finish');
}

test('a deletion receipt removes only matching browser records and stops matching tabs', async () => {
  const score = 'woodshed.plungeBurrow.bestScore';
  const mode = 'woodshed:game-mode-entry:session:v1';
  const arrival = 'woodshed:world-entry-arrival:v1';
  const clover = id => `woodshed:clover-caper:request:${id}`;
  const daily = scope => `woodshed:streak-popup:daily:v1:${scope}`;
  const guest = 'woodshedWoodchuckMetronomeBpm';
  const cluster = browserCluster([
    [state, cache(target)], [backup(1), cache(' wc-deleted ')], [backup(2), cache(other)],
    [backup(3), 'malformed'], [score, bound(target)], [mode, JSON.stringify([target, 'generation'])],
    [clover(target), 'request'], [clover(other), 'other request'], [daily(targetEntry), 'day'],
    [daily(otherEntry), 'other day'], [guest, '88'],
  ]);
  const old = cluster.open({accountId: target, tabValues: new Map([
    [draft, bound(target)], [timer, bound(target)], [arcade(target), 'old action'], [arcade(other), 'other action'],
  ])});
  old.load('state.js');
  const otherTab = cluster.open({accountId: other, tabValues: new Map([[draft, bound(other)], [timer, bound(other)]])});
  const actions = new Map([[target, 'target action'], [other, 'other action']]);
  const indexedDB = wheel(actions);
  const success = cluster.open({jsonReceipt: receipt, indexedDB, tabValues: new Map([
    [draft, bound(target)], [timer, bound(other)], [arrival, JSON.stringify({account: targetEntry})],
  ])});

  await until(() => !actions.has(target) && old.tabValues.size === 1 && success.tabValues.size === 1);
  for (const key of [state, backup(1), score, mode, clover(target), daily(targetEntry), marker]) assert.equal(cluster.values.has(key), false);
  for (const key of [backup(2), backup(3), clover(other), daily(otherEntry), guest]) assert.equal(cluster.values.has(key), true);
  assert.equal(otherTab.tabValues.size, 2);
  assert.equal(otherTab.window.WWSessionBoundary.isCurrent(), true);
  assert.equal(success.tabValues.get(timer), bound(other));
  assert.equal(old.tabValues.get(arcade(other)), 'other action');
  assert.equal(old.shell.hidden, true);
  assert.equal(old.window.WWState.saveState(JSON.parse(cache(target))), false);
  assert.equal(cluster.values.has(state), false);
  assert.deepEqual([...actions.keys()], [other]);
  assert.deepEqual(success.locks, [`woodshed:prize-wheel:request:${target}`]);
  assert.deepEqual(indexedDB.names, ['woodshed-prize-wheel-recovery']);
});

test('cleanup preserves a shared slot replaced by another account while hashing', async () => {
  const cluster = browserCluster([[state, cache(target)], [backup(1), cache(target)]]);
  let release;
  const crypto = {subtle: {digest: (algorithm, value) => new Promise(resolve => {
    release = () => resolve(webcrypto.subtle.digest(algorithm, value));
  })}};
  cluster.open({jsonReceipt: receipt, crypto});
  await until(() => Boolean(release));
  cluster.values.set(state, cache(other));
  release();
  await until(() => !cluster.values.has(backup(1)));
  assert.equal(cluster.values.get(state), cache(other));
});

test('ordinary and malformed receipt pages leave account storage alone', async () => {
  const cluster = browserCluster([[state, cache(target)], [backup(1), cache(target)]]);
  cluster.open();
  cluster.open({jsonReceipt: '{"account":"untrusted"}'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(cluster.values.get(state), cache(target));
  assert.equal(cluster.values.get(backup(1)), cache(target));
  assert.equal(cluster.values.has(marker), false);
});
