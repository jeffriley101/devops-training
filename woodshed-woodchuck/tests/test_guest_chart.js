const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {File} = require('node:buffer');

function browser({navigator = {}, deniedStorage = false, c001 = false, prebeta = c001, toolsReady = true} = {}) {
  const ids = new Map(), events = new Map();
  const downloads = [], shares = [], copies = [], requests = [], nativeForms = [], guardedForms = [], revoked = [];
  const blobs = new Map();
  const local = new Map([
    ['woodshed:guest:v1:preferences', '{"instrument":"Tuba","history":"old guest"}'],
    ['woodshed:guest:v1:history', 'old chart'],
    ['woodshed:session-change:v1', '4'],
    ['woodshedWoodchuckState.v1', 'account cache'],
  ]);
  const session = new Map([['woodshed:guest:v1:draft', 'old draft'], ['woodshed:p-book:verifier-draft:v1', 'account draft']]);
  let current = true;
  function element() {
    const listeners = new Map();
    return {
      value: '', textContent: '', hidden: false, disabled: false, checked: false, children: [], dataset: {},
      addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(fn); },
      async fire(type, event = {}) {
        event.preventDefault ||= function () { this.defaultPrevented = true; };
        for (const fn of listeners.get(type) || []) await fn(event);
        return event;
      },
      click() { return this.fire('click'); },
      classList: {add() {}, remove() {}},
      appendChild(child) { this.children.push(child); },
      remove() { this.removed = true; },
    };
  }
  for (const id of ['guest-setup-form', 'guest-setup-fields', 'guest-feedback', 'guest-tools', 'guest-discard',
    'guest-chart-form', 'guest-chart-fields', 'guest-chart-date', 'guest-chart-instrument', 'guest-chart-minutes',
    'guest-chart-feedback', 'guest-chart-preview', 'guest-chart-create', 'guest-chart-share', 'guest-chart-download', 'guest-chart-copy']) ids.set(id, element());
  const setup = ids.get('guest-setup-form');
  setup.elements = {};
  for (const [name, value] of [['instrument', 'Flute'], ['level', 'Beginner'], ['goal', 'Daily']]) {
    setup.elements[name] = element();
    setup.elements[name].options = [{value: ''}, {value}];
  }
  setup.reset = () => Object.values(setup.elements).forEach(field => { field.value = ''; });
  setup.reportValidity = () => true;
  const detail = Object.assign(element(), {value: 'Scales'});
  ids.get('guest-chart-instrument').options = [{value: ''}, {value: 'Flute'}, {value: 'Tuba'}];
  const chartForm = ids.get('guest-chart-form');
  chartForm.querySelectorAll = () => [detail];
  chartForm.reset = () => {
    for (const name of ['date', 'instrument', 'minutes']) ids.get(`guest-chart-${name}`).value = '';
    detail.checked = false;
  };
  const body = element();
  body.dataset = {guest: 'local', c001Context: String(c001), prebetaContext: String(prebeta)};
  const document = {
    body, readyState: 'loading',
    getElementById: id => ids.get(id) || null,
    querySelectorAll: () => [],
    createElement(tag) {
      const node = element();
      if (tag === 'a') node.click = () => downloads.push({filename: node.download, blob: blobs.get(node.href), node});
      if (tag === 'form') node.submit = () => nativeForms.push(node);
      return node;
    },
  };
  function storage(map) {
    return new Proxy({removeItem(key) { map.delete(key); }, setItem() { throw new Error('Product storage write'); }}, {
      ownKeys: () => [...map.keys()], getOwnPropertyDescriptor: () => ({enumerable: true, configurable: true}),
    });
  }
  const window = {
    WWSessionBoundary: {
      isCurrent: () => current,
      clearAccountProductCaches() {},
      async submitGuestForm(form) { guardedForms.push(form); form.submit(); },
    },
    WWGuestToolsReady: toolsReady, WWTuner: {}, WWGuestPlunge: {ready: true},
    addEventListener(type, fn) { if (!events.has(type)) events.set(type, []); events.get(type).push(fn); },
    dispatchEvent(event) { for (const fn of events.get(event.type) || []) fn(event); },
    setTimeout(fn) { fn(); },
    fetch(...args) { requests.push(args); throw new Error('Guest network request'); },
  };
  for (const [name, map] of [['localStorage', local], ['sessionStorage', session]]) {
    Object.defineProperty(window, name, {get() { if (deniedStorage) throw new Error('Storage denied'); return storage(map); }});
  }
  const browserNavigator = {
    clipboard: {async writeText(text) { copies.push(text); }},
    ...navigator,
  };
  if (navigator.share) browserNavigator.share = async payload => { shares.push(payload); return navigator.share(payload); };
  const BrowserURL = {
    createObjectURL(blob) { const url = `blob:guest-test-${blobs.size}`; blobs.set(url, blob); return url; },
    revokeObjectURL(url) { revoked.push(url); },
  };
  const context = vm.createContext({window, document, navigator: browserNavigator, URL: BrowserURL, Blob, File,
    CustomEvent: class {constructor(type, options = {}) { this.type = type; this.detail = options.detail; }},
    fetch: (...args) => window.fetch(...args), console});
  for (const file of ['guest.js', 'guest-chart.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js', file), 'utf8'), context);
  document.readyState = 'complete';
  window.dispatchEvent({type: 'DOMContentLoaded'});
  async function explore() {
    for (const field of Object.values(setup.elements)) field.value = field.options[1].value;
    await setup.fire('submit');
  }
  async function build() {
    await explore();
    ids.get('guest-chart-date').value = '2026-10-03';
    ids.get('guest-chart-minutes').value = '25';
    detail.checked = true;
    await ids.get('guest-chart-create').click();
  }
  return {ids, local, session, window, document, downloads, shares, copies, requests, nativeForms, guardedForms, revoked, detail, explore, build,
    loseSession() { current = false; window.dispatchEvent({type: 'ww:session-changed'}); },
    lifecycle(type, extra = {}) { window.dispatchEvent({type, ...extra}); }};
}

test('Guest chart creates no network, browser product storage or automatic output', async () => {
  const b = browser();
  assert.equal(b.ids.get('guest-tools').hidden, true);
  assert.equal(b.local.has('woodshed:guest:v1:preferences'), false);
  assert.equal(b.local.has('woodshed:guest:v1:history'), false);
  assert.equal(b.session.has('woodshed:guest:v1:draft'), false);
  await b.build();
  assert.equal(b.ids.get('guest-chart-preview').textContent,
    'Woodshed Woodchuck Guest P-Chart\n\nPractice date: 2026-10-03\nInstrument: Flute\nPractice minutes: 25\nPractice details: Scales\n');
  assert.deepEqual(b.requests, []);
  assert.deepEqual(b.copies, []);
  assert.deepEqual(b.shares, []);
  assert.deepEqual(b.downloads, []);
  assert.deepEqual([...b.local], [['woodshed:session-change:v1', '4'], ['woodshedWoodchuckState.v1', 'account cache']]);
  assert.deepEqual([...b.session], [['woodshed:p-book:verifier-draft:v1', 'account draft']]);
});

test('Explicit Copy and Download contain only this chart and release the local blob URL', async () => {
  const b = browser();
  await b.build();
  const chartText = b.ids.get('guest-chart-preview').textContent;
  await b.ids.get('guest-chart-copy').click();
  assert.deepEqual(b.copies, [chartText]);
  await b.ids.get('guest-chart-download').click();
  assert.equal(b.downloads.length, 1);
  assert.equal(await b.downloads[0].blob.text(), chartText);
  assert.equal(b.downloads[0].filename, 'woodshed-guest-p-chart-2026-10-03.txt');
  assert.equal(b.downloads[0].node.removed, true);
  assert.equal(b.revoked.length, 1);
  assert.deepEqual(b.requests, []);
});

test('Share uses native files or text, and unsupported Share falls back to a local download', async () => {
  const fileBrowser = browser({navigator: {canShare: () => true, share: async () => {}}});
  await fileBrowser.build();
  await fileBrowser.ids.get('guest-chart-share').click();
  assert.equal(fileBrowser.shares.length, 1);
  assert.equal(fileBrowser.shares[0].files[0].name, 'woodshed-guest-p-chart-2026-10-03.txt');
  assert.equal(await fileBrowser.shares[0].files[0].text(), fileBrowser.ids.get('guest-chart-preview').textContent);
  assert.deepEqual(fileBrowser.downloads, []);
  const textBrowser = browser({navigator: {canShare: payload => !payload.files, share: async () => {}}});
  await textBrowser.build();
  await textBrowser.ids.get('guest-chart-share').click();
  assert.equal(textBrowser.shares[0].text, textBrowser.ids.get('guest-chart-preview').textContent);
  const fallback = browser();
  await fallback.build();
  await fallback.ids.get('guest-chart-share').click();
  assert.equal(fallback.downloads.length, 1);
  assert.deepEqual(fallback.requests, []);
});

test('Cancelling native Share does not download or copy', async () => {
  const b = browser({navigator: {share: async () => { throw Object.assign(new Error('cancelled'), {name: 'AbortError'}); }}});
  await b.build();
  await b.ids.get('guest-chart-share').click();
  assert.equal(b.ids.get('guest-chart-feedback').textContent, 'Sharing cancelled.');
  assert.deepEqual(b.downloads, []);
  assert.deepEqual(b.copies, []);
});

test('Share completion after session change cannot restore chart state or export controls', async () => {
  let resolveShare;
  const b = browser({navigator: {share: () => new Promise(resolve => { resolveShare = resolve; })}});
  await b.build();
  const pending = b.ids.get('guest-chart-share').click();
  b.loseSession();
  resolveShare();
  await pending;
  assert.equal(b.ids.get('guest-chart-preview').textContent, '');
  assert.equal(b.ids.get('guest-chart-feedback').textContent, '');
  for (const name of ['share', 'download', 'copy']) assert.equal(b.ids.get(`guest-chart-${name}`).disabled, true);
  await b.ids.get('guest-chart-copy').click();
  assert.deepEqual(b.copies, []);
});

test('Discard, navigation and BFCache clear form and closure data', async () => {
  for (const end of ['discard', 'pagehide', 'pageshow']) {
    const b = browser();
    await b.build();
    if (end === 'discard') await b.ids.get('guest-discard').click();
    else b.lifecycle(end, {persisted: true});
    assert.equal(b.window.WWGuest.isCurrent(), false);
    assert.equal(b.ids.get('guest-tools').hidden, true);
    assert.equal(b.ids.get('guest-chart-date').value, '');
    assert.equal(b.ids.get('guest-chart-instrument').value, '');
    assert.equal(b.ids.get('guest-chart-minutes').value, '');
    assert.equal(b.detail.checked, false);
    assert.equal(b.ids.get('guest-chart-preview').textContent, '');
    await b.ids.get('guest-chart-copy').click();
    assert.deepEqual(b.copies, []);
  }
});

test('Fixed-choice validation rejects invalid date, duration, instrument and category', async () => {
  for (const invalid of ['date', 'minutes', 'instrument', 'detail']) {
    const b = browser();
    await b.explore();
    b.ids.get('guest-chart-date').value = '2026-10-03';
    b.ids.get('guest-chart-minutes').value = '25';
    if (invalid === 'date') b.ids.get('guest-chart-date').value = '2026-02-30';
    if (invalid === 'minutes') b.ids.get('guest-chart-minutes').value = '1.5';
    if (invalid === 'instrument') b.ids.get('guest-chart-instrument').value = 'private student name';
    if (invalid === 'detail') { b.detail.value = 'private free text'; b.detail.checked = true; }
    await b.ids.get('guest-chart-create').click();
    assert.equal(b.ids.get('guest-chart-preview').hidden, true);
    assert.equal(b.ids.get('guest-chart-copy').disabled, true);
    assert.deepEqual(b.requests, []);
  }
});

test('Memory tools work with storage denied, and incomplete tool loading keeps setup disabled', async () => {
  const b = browser({deniedStorage: true});
  await b.build();
  assert.equal(b.ids.get('guest-chart-preview').hidden, false);
  assert.equal(b.ids.get('guest-setup-fields').disabled, false);
  const incomplete = browser({toolsReady: false});
  await incomplete.build();
  assert.equal(incomplete.ids.get('guest-setup-fields').disabled, true);
  assert.equal(incomplete.ids.get('guest-tools').hidden, true);
});

test('C001 and C002 discard submit only an empty explicit native POST after local reset', async () => {
  for (const options of [{c001: true}, {prebeta: true}]) {
    const b = browser(options);
    await b.build();
    await b.ids.get('guest-discard').click();
    assert.equal(b.nativeForms.length, 1);
    assert.equal(b.guardedForms.length, 1);
    assert.equal(b.nativeForms[0], b.guardedForms[0]);
    assert.equal(b.nativeForms[0].method, 'post');
    assert.equal(b.nativeForms[0].action, '/guest/discard');
    assert.deepEqual(b.nativeForms[0].children, []);
    assert.equal(b.ids.get('guest-chart-preview').textContent, '');
    assert.deepEqual(b.requests, []);
  }
});

test('A rejected guarded C001 discard keeps product data cleared and never submits the form', async () => {
  const b = browser({c001: true});
  b.window.WWSessionBoundary.submitGuestForm = async () => { throw new Error('Safe transition unavailable'); };
  await b.build();
  await b.ids.get('guest-discard').click();
  assert.deepEqual(b.nativeForms, []);
  assert.equal(b.ids.get('guest-chart-preview').textContent, '');
  assert.equal(b.ids.get('guest-tools').hidden, true);
  assert.match(b.ids.get('guest-feedback').textContent, /Registration context could not be discarded/);
  assert.deepEqual(b.requests, []);
});
