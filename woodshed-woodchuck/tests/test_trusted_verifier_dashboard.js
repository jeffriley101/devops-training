const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/trusted-verifier-dashboard.js', 'utf8');

class Element {
  constructor() { this.children = []; this.events = {}; this.dataset = {}; this.value = ''; this.classList = {add() {}}; }
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  get firstChild() { return this.children[0]; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  addEventListener(event, callback) { this.events[event] = callback; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness({connected = true, multiple = true, denied = false, pendingCount = 1} = {}) {
  const nodes = Object.fromEntries(['verifier-student-list', 'verifier-practice-chart-list',
    'verifier-dashboard-error', 'verifier-practice-chart-feedback', 'trusted-verifier-logout-button',
    'verifier-review-notice', 'verifier-reviews']
    .map(id => [id, new Element()]));
  nodes['verifier-student-list'].dataset.connectionId = connected ? '17' : '';
  const noticeLink = new Element();
  nodes['verifier-review-notice'].querySelector = () => noticeLink;
  const select = new Element(), button = new Element(), form = new Element();
  form.querySelector = tag => tag === 'select' ? select : button;
  form.requestSubmit = () => { form.submitted = true; };
  select.id = 'verifier-connection';
  select.form = form;
  if (multiple) nodes['verifier-student-selector'] = form;
  const calls = [], navigations = [];
  let pending = true;
  const item = {verification_id: 42, student: {display_name: '<Child>'},
    chart: {practice_date: '2026-09-09', minutes: 20, instrument: 'Flute', practice_details: [], note: 'Practice'}};
  const fetch = async (url, options) => {
    calls.push({url, options});
    if (options?.method === 'POST') { pending = false; return {ok: true, status: 200, json: async () => ({})}; }
    if (url.includes('/dashboard')) return {ok: true, text: async () => 'updated'};
    return {ok: !denied, status: denied ? 404 : 200,
      json: async () => denied ? {detail: 'Connected student not found.'} : {pending_charts: pending ? Array(pendingCount).fill(item) : []}};
  };
  vm.runInNewContext(source, {document: {
    querySelector: selector => nodes[selector.slice(1)] || null,
    createElement: () => new Element(),
  }, fetch, window: {location: {href: '/trusted-verifiers/dashboard?connection_id=17',
    assign: url => navigations.push(url)}}, DOMParser: class {
    parseFromString() { return {querySelector: () => ({childNodes: ['updated snapshot']})}; }
  }});
  return {nodes, calls, navigations, select, button, form, noticeLink};
}

test('delegated selector directly submits authorized connection', async () => {
  const h = harness();
  h.nodes['verifier-student-list'].events.change({target: h.select});
  assert.equal(h.form.submitted, true);
  await tick();
  assert.equal(h.calls[0].url, '/trusted-verifiers/practice-charts?connection_id=17');
  assert.ok(h.calls.every(call => !call.url.includes('/me')));
});

for (const [index, decision] of [[0, 'approved'], [1, 'rejected']]) {
  test(`${decision}: unchanged response endpoint, disable buttons, refresh selected snapshot and queue`, async () => {
    const h = harness();
    await tick();
    const card = h.nodes['verifier-practice-chart-list'].children[0];
    const row = card.children.at(-1), note = card.children.at(-2);
    note.value = '  Encouragement  ';
    row.children[index].events.click();
    assert.ok(row.children.every(button => button.disabled));
    await tick();
    const post = h.calls.find(call => call.options?.method === 'POST');
    assert.equal(post.url, '/trusted-verifiers/practice-charts/42/respond');
    assert.deepEqual(JSON.parse(post.options.body), {decision, response_note: 'Encouragement'});
    assert.deepEqual(h.nodes['verifier-student-list'].children, ['updated snapshot']);
    assert.match(h.nodes['verifier-practice-chart-list'].children[0].textContent, /No P-Charts/);
    assert.equal(h.calls.filter(call => call.options?.method === 'POST').length, 1);
    assert.equal(h.nodes['verifier-review-notice'].hidden, true);
    assert.equal(h.nodes['verifier-reviews'].dataset.pending, 'false');
    // The identity selector is replaced with the snapshot, but delegation survives.
    const replacementForm = {requestSubmit() {this.submitted = true;}};
    h.nodes['verifier-student-list'].events.change({target: {id: 'verifier-connection', form: replacementForm}});
    assert.equal(replacementForm.submitted, true);
  });
}

test('no connected student makes no unscoped review request', async () => {
  const h = harness({connected: false, multiple: false});
  await tick();
  assert.equal(h.calls.length, 0);
  assert.match(h.nodes['verifier-practice-chart-list'].children[0].textContent, /No P-Charts/);
});

test('revoked selection clears stale student snapshot and review cards', async () => {
  const h = harness({denied: true});
  await tick();
  assert.equal(h.nodes['verifier-student-list'].children.length, 0);
  assert.equal(h.nodes['verifier-practice-chart-list'].children.length, 0);
  assert.match(h.nodes['verifier-dashboard-error'].textContent, /Connected student not found/);
  assert.equal(h.nodes['verifier-review-notice'].hidden, true);
});

for (const count of [0, 1, 3]) {
  test(`authorized pending count ${count} controls calm review notice`, async () => {
    const h = harness({pendingCount: count});
    await tick();
    assert.equal(h.nodes['verifier-review-notice'].hidden, count === 0);
    assert.equal(h.nodes['verifier-reviews'].dataset.pending, String(count > 0));
    if (count) assert.equal(h.noticeLink.textContent,
      count === 1 ? '1 P-Chart needs your review' : '3 P-Charts need your review');
  });
}
