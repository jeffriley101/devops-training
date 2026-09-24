const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function recoveryHarness() {
  const timers = new Map(), listeners = {}, children = [];
  let id = 0;
  const element = () => ({dataset: {}, children: [], setAttribute() {},
    append(...nodes) { this.children.push(...nodes); },
    remove() { const index = children.indexOf(this); if (index >= 0) children.splice(index, 1); }});
  const document = {createElement: element, querySelectorAll: () => [],
    body: {append: node => children.push(node)}, addEventListener: (key, fn) => listeners[key] = fn};
  const window = {addEventListener: (key, fn) => listeners[key] = fn};
  const location = {href: 'https://woodshed.test/home', origin: 'https://woodshed.test', pathname: '/home'};
  const navigator = {onLine: true};
  vm.runInNewContext(fs.readFileSync('static/js/recovery.js', 'utf8'), {
    window, document, location, navigator, URL, TypeError, queueMicrotask,
    setTimeout: (fn, delay) => { timers.set(++id, {fn, delay}); return id; },
    clearTimeout: key => timers.delete(key),
  });
  return {api: window.WWRecovery, timers, listeners, children, navigator};
}

test('fast operations never flash; stale completions cannot erase a newer recovery', () => {
  const h = recoveryHarness();
  const first = h.api.begin();
  h.api.clear(first);
  assert.equal(h.timers.size, 0);
  assert.equal(h.children.length, 0);
  const second = h.api.begin();
  h.listeners.pageshow({persisted: false});
  h.api.clear(first);
  const timer = [...h.timers.values()][0];
  assert.equal(timer.delay, 700);
  timer.fn();
  assert.equal(h.children[0].dataset.recovery, 'loading');
  h.api.failure({status: 503}, null, first);
  assert.equal(h.children[0].dataset.recovery, 'loading');
  h.api.clear(second);
  assert.equal(h.children.length, 0);
});

test('recovery distinguishes HTTP, session, network and feature failures', () => {
  const h = recoveryHarness();
  for (const [error, expected] of [[{status: 401}, 'session'], [{status: 503}, 'server'],
    [new TypeError('network'), 'offline'], [new Error('feature'), 'feature']]) {
    assert.equal(h.api.failure(error, () => {}), expected);
    assert.equal(h.children[0].dataset.recovery, expected);
    if (expected === 'session') {
      assert.equal(h.children[0].children.some(node => node.textContent === 'Retry'), false);
      assert.equal(h.children[0].children.find(node => node.textContent === 'Sign in').href, '/login');
    }
  }
});

test('cancelled links start no loader; pagehide cancels navigation watchdog as well', async () => {
  const h = recoveryHarness();
  const link = {href: 'https://woodshed.test/store', dataset: {}};
  const event = {target: {closest: () => link}, button: 0};
  h.listeners.click(event); event.defaultPrevented = true;
  await Promise.resolve();
  assert.equal(h.timers.size, 0);
  h.listeners.click({...event, defaultPrevented: false});
  await Promise.resolve();
  assert.equal(h.timers.size, 2);
  h.listeners.pagehide();
  assert.equal(h.timers.size, 0);
});

test('a late soundtrack play cannot restart background audio; foreground requires input', async () => {
  const events = {}, timers = new Map();
  let cleanup, resolvePlay, audio, plays = 0;
  const document = {hidden: false, readyState: 'complete',
    querySelector: selector => selector === '[data-arcade-soundtrack]' ? {dataset: {arcadeSoundtrack: 'blue'}} : null,
    getElementById: () => null, addEventListener: (key, fn) => events[key] = fn};
  class Audio {
    constructor() { audio = this; this.paused = true; }
    play() { plays++; this.paused = false; return new Promise(resolve => { resolvePlay = resolve; }); }
    pause() { this.paused = true; }
    addEventListener() {}
  }
  const window = {WWLifecycle: {onBackground: fn => cleanup = fn}, addEventListener() {},
    setTimeout: fn => { timers.set(1, fn); return 1; }, clearTimeout: id => timers.delete(id)};
  vm.runInNewContext(fs.readFileSync('static/js/arcade-soundtrack.js', 'utf8'), {window, document, Audio});
  events.pointerdown({});
  document.hidden = true; cleanup();
  resolvePlay(); await Promise.resolve();
  assert.equal(audio.paused, true);
  document.hidden = false;
  events['woodshed:arcade-soundtrack-run-state']({detail: {gameKey: 'blue', active: false}});
  [...timers.values()].forEach(fn => fn());
  assert.equal(plays, 1);
  events.pointerdown({});
  assert.equal(plays, 2);
});
