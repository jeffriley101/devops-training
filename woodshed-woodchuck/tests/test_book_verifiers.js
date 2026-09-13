const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/js/app.js', 'utf8');

class Element {
  constructor() { this.children = []; this.events = {}; this.textContent = ''; }
  get firstChild() { return this.children[0]; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, handler) { this.events[name] = handler; }
  reset() {}
}

test('BOOK lists only accepted Verifiers and defaults to no request', async () => {
  const select = new Element();
  const connections = ['verifier', 'band_director', 'parent', 'mentor'].map((role, i) =>
    ({role, status: 'accepted', verifier: {id: i + 1, display_name: role}}));
  connections.push({role: 'verifier', status: 'pending', verifier: {id: 99}});
  const source = app.slice(app.indexOf('    async function loadVerifierOptions()'), app.indexOf('    async function loadTeams()'));
  await vm.runInNewContext(source + '\nloadVerifierOptions()', {
    verifierSelectEl: select, restoredDraft: null, verifierRoleLabel: role => role,
    Option: function(text, value) { this.text = text; this.value = value; },
    fetch: async () => ({ok: true, json: async () => ({connections})}),
  });
  assert.deepEqual(select.children.map(option => option.value), ['', '1']);
  assert.equal(select.children[0].text, 'No verification request');
  assert.equal(select.disabled, false);
});

test('BOOK current team loads without a SHED navigation element', async () => {
  const current = new Element();
  const source = app.slice(app.indexOf('    async function loadTeams()'), app.indexOf('    async function createPersistentPracticeChart('));
  await vm.runInNewContext(source + '\nloadTeams()', {
    document: {getElementById: id => id === 'p-book-current-team' ? current : null,
               createElement: () => new Element()},
    fetch: async () => ({ok: true, json: async () => ({membership: {team: {name: 'Musicians', emblem: {value: 'M'}}}})}),
    renderTeamEmblem: (element, emblem) => { element.textContent = emblem.value; },
  });
  assert.equal(current.children[0].textContent, 'Current team: ');
  assert.equal(current.children[1].children[1].textContent, 'Musicians');
});

test('management separates relationships and submits both forms through invitation API', async () => {
  const source = fs.readFileSync('static/js/trusted-verifiers.js', 'utf8');
  const elements = {};
  for (const match of source.matchAll(/querySelector\(\s*"([^\"]+)"/g)) elements[match[1]] = new Element();
  const verifier = elements['#trusted-verifier-invite-form'];
  const director = new Element();
  verifier.fields = {role: 'verifier', email: 'v@example.test'};
  director.fields = {role: 'band_director', email: 'd@example.test'};
  const posted = [];
  const connections = ['verifier', 'band_director'].map(role => ({role, status: 'accepted', verifier: {display_name: role}}));
  vm.runInNewContext(source, {
    document: {querySelector: key => elements[key], querySelectorAll: () => [verifier, director], createElement: () => new Element()},
    window: {location: {search: '', origin: 'http://testserver'}}, URLSearchParams, URL,
    FormData: class { constructor(form) { this.fields = form.fields; } get(key) { return this.fields[key]; } },
    fetch: async (url, options) => {
      assert.equal(url, '/trusted-verifiers/invitations');
      if (options.method === 'POST') posted.push(options.body.fields);
      return {ok: true, json: async () => options.method === 'POST'
        ? {accept_url: 'http://testserver/accept/token'} : {connections, invitations: []}};
    },
  });
  await new Promise(setImmediate);
  const allText = element => element.textContent + element.children.map(allText).join(' ');
  assert.match(allText(elements['#trusted-verifier-connection-list']), /verifier/);
  assert.doesNotMatch(allText(elements['#trusted-verifier-connection-list']), /band_director/);
  assert.match(allText(elements['#band-director-connection-list']), /band_director/);
  for (const form of [verifier, director]) await form.events.submit({currentTarget: form, preventDefault() {}});
  assert.deepEqual(posted.map(row => row.role), ['verifier', 'band_director']);
});
