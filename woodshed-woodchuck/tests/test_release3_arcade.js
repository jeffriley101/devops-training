const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function economy(fetch) {
  const outputs = { price: {dataset: {}, textContent: ''}, attempts: {dataset: {}, textContent: ''}, balance: {textContent: ''} };
  const stored = new Map(); let n = 0;
  const root = { console: {warn() {}}, setTimeout: fn => fn(),
    crypto: {randomUUID: () => `00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`},
    sessionStorage: {getItem: k => stored.get(k), setItem: (k,v) => stored.set(k,v), removeItem: k => stored.delete(k)} };
  const document = {querySelectorAll: selector => selector.includes('price') ? [outputs.price] : selector.includes('attempts') ? [outputs.attempts] : selector.includes('balance') ? [outputs.balance] : []};
  vm.runInNewContext(fs.readFileSync('static/js/arcade-economy.js','utf8'), {window: root, document, fetch});
  return {api: root.WoodshedArcadeEconomy, outputs, root, stored};
}
const ok = body => ({ok:true, status:200, json: async () => body});

test('lost response retries use one durable request ID; later deliberate starts use a new one', async () => {
  const ids = []; let calls = 0;
  const {api, stored} = economy(async (_url, options) => {
    ids.push(JSON.parse(options.body).request_id);
    if (++calls === 1) throw Error('lost response');
    return ok({play_token:'token', game_key:'thirds', charged_now:100, attempts_remaining:2});
  });
  await api.startPlay('thirds');
  assert.equal(ids[0], ids[1]); assert.equal(stored.size, 0);
  await api.startPlay('thirds'); assert.notEqual(ids[1], ids[2]);
});

test('status displays authoritative price, remaining attempts, balance, and free reasons', () => {
  const {api, outputs} = economy();
  api.renderStatus({game_key:'thirds', free_reason:null, attempts_remaining:2, balance:45});
  assert.equal(outputs.price.textContent, '100 Dandelions · 3 attempts');
  assert.equal(outputs.attempts.textContent, '2 purchased attempts remaining');
  assert.equal(outputs.balance.textContent, '45');
  api.renderStatus({game_key:'thirds', free_reason:'full_access', attempts_remaining:2});
  assert.equal(outputs.price.textContent, 'Free with Full Access');
  assert.equal(outputs.attempts.textContent, '');
  api.renderStatus({game_key:'thirds', free_reason:null, attempts_remaining:1});
  assert.equal(outputs.attempts.textContent, '1 purchased attempts remaining');
  api.renderStatus({game_key:'blue', free_reason:'always_free', attempts_remaining:0});
  assert.equal(outputs.price.textContent, 'Always free');
  assert.equal(outputs.attempts.textContent, '');
});

test('an already completed start retry never opens gameplay; shared scores remain server data', async () => {
  const {api} = economy(async url => url.includes('/scores/')
    ? ok({leaderboard:[{display_name:'Other',score:20},{display_name:'Self',score:10}]})
    : ok({already_completed:true, play_token:'completed-token'}));
  await assert.rejects(api.startPlay('thirds'), /no longer available/);
  assert.equal((await api.loadScores('thirds')).leaderboard.length, 2);
});

test('artwork defers animation and returns to static for reduced motion, hidden pages and failures', () => {
  let visibility, intersection, motion;
  const media = {matches:false, addEventListener: (_event, fn) => {motion=fn;}};
  const img = {classList:{add() {}}, getAttribute() {return this.src;}, addEventListener(_event,fn) {this.onError=fn;}};
  const doc = {hidden:false, querySelectorAll:()=>[], addEventListener: (_event,fn) => {visibility=fn;}};
  const root = {matchMedia:()=>media, IntersectionObserver:class {constructor(fn) {intersection=fn;} observe() {}}};
  vm.runInNewContext(fs.readFileSync('static/js/arcade-art.js','utf8'), {window:root, document:doc});
  root.WoodshedArcadeArt.wire({querySelector:()=>img,append() {}},{static:'fallback.png',animated:'motion.webp'});
  assert.equal(img.src,'fallback.png'); assert.equal(img.loading,'lazy');
  intersection([{isIntersecting:true}]); assert.equal(img.src,'motion.webp');
  media.matches=true; motion(); assert.equal(img.src,'fallback.png');
  media.matches=false; motion(); doc.hidden=true; visibility(); assert.equal(img.src,'fallback.png');
  doc.hidden=false; visibility(); img.onError(); assert.equal(img.src,'fallback.png');
  assert.equal(Object.keys(root.WoodshedArcadeArt.assets).length,14);
});
