const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/practice-insights.js', 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function boot(enabled = true) {
  const elements = {};
  for (const key of ['locked', 'active', 'content', 'status']) {
    elements[key] = {hidden: key === 'locked' ? enabled : !enabled,
      children: [], textContent: '', replaceChildren() { this.children = []; },
      append(child) { this.children.push(child); }};
  }
  const events = {}, pending = [];
  const document = {hidden: false,
    getElementById: () => ({dataset: {enabled: String(enabled)},
      querySelector: selector => elements[selector.slice(15, -1)]}),
    createElement: () => ({textContent: ''}),
    addEventListener: (event, fn) => { events[event] = fn; }};
  const window = {addEventListener: (event, fn) => { events[event] = fn; }};
  vm.runInNewContext(fs.readFileSync("static/js/practice-duration.js", "utf8"), {window});
  vm.runInNewContext(source, {document, window,
    fetch: (url, options) => new Promise(resolve => pending.push({url, options, resolve}))});
  return {elements, events, pending, document};
}
const response = {ok: true, status: 200, json: async () => ({weeks: [{
  week_start: '2026-08-17', week_end: '2026-08-23', minutes: 15, days: 1,
  verified_minutes: 10, pristine_minutes: 0, seconds: 118, verified_seconds: 0, pristine_seconds: 118}], total_minutes: 118 / 60, average_weekly_minutes: 118 / 240, total_seconds: 118, average_weekly_seconds: 29.5})};

test('Open card does not prefetch Full data', () => {
  const ui = boot(false);
  assert.equal(ui.pending.length, 0);
  assert.equal(ui.elements.locked.hidden, false);
});

test('Full panel renders isolated aggregate via no-store request', async () => {
  const ui = boot();
  assert.equal(ui.pending[0].url, '/practice-charts/insights');
  assert.equal(ui.pending[0].options.cache, 'no-store');
  ui.pending[0].resolve(response);
  await settle();
  assert.equal(ui.elements.content.children.length, 2);
  assert.match(ui.elements.content.children[1].textContent, /Weekly average: 30s/);
  assert.match(ui.elements.content.children[0].textContent, /1m 58s/);
  assert.equal(ui.elements.locked.hidden, true);
});

for (const code of [401, 403]) test(`${code} clears data and returns to locked state`, async () => {
  const ui = boot();
  ui.pending[0].resolve(response);
  await settle();
  ui.events.pageshow({persisted: true});
  assert.equal(ui.elements.content.children.length, 0);
  ui.pending[1].resolve({status: code});
  await settle();
  assert.equal(ui.elements.active.hidden, true);
  assert.equal(ui.elements.locked.hidden, false);
  assert.equal(ui.elements.content.children.length, 0);
});

test('hidden-page responses cannot restore stale data; return rechecks access', async () => {
  const ui = boot();
  ui.document.hidden = true;
  ui.events.visibilitychange();
  ui.pending[0].resolve(response);
  await settle();
  assert.equal(ui.elements.content.children.length, 0);
  ui.document.hidden = false;
  ui.events.visibilitychange();
  assert.equal(ui.pending.length, 2);
  ui.pending[1].resolve({status: 403});
  await settle();
  assert.equal(ui.elements.locked.hidden, false);
});

test('failed request clears old content without touching raw practiceLog', async () => {
  const ui = boot();
  ui.pending[0].resolve({ok: false, status: 503});
  await settle();
  assert.equal(ui.elements.content.children.length, 0);
  assert.match(ui.elements.status.textContent, /temporarily unavailable/);
  assert.doesNotMatch(source, /practiceLog|localStorage|sessionStorage/);
});
