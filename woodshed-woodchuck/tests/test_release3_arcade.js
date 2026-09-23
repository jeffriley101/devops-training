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
  assert.equal(outputs.price.textContent, 'Free');
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

test('static load replaces fallback; motion, visibility and errors restore it safely', () => {
  let visibility, intersection, motion;
  const media = {matches:false, addEventListener: (_event, fn) => {motion=fn;}};
  const img = {naturalWidth:256, classList:{add() {}}, getAttribute() {return this.src;},
    addEventListener(event,fn) {this[event]=fn;}};
  const doc = {hidden:false, createElement:()=>img, querySelectorAll:()=>[],
    addEventListener: (_event,fn) => {visibility=fn;}};
  const root = {matchMedia:()=>media,
    IntersectionObserver:class {constructor(fn) {intersection=fn;} observe() {}}};
  let active = false;
  const element = {classList:{toggle(_name, enabled) {active=enabled;}}, append() {}};
  vm.runInNewContext(fs.readFileSync('static/js/arcade-art.js','utf8'), {window:root, document:doc});
  root.WoodshedArcadeArt.wire(element,{static:'fallback.png',animated:'motion.webp'});
  assert.equal(img.src,'fallback.png'); assert.equal(img.loading,'lazy');
  assert.equal(active,false); assert.equal(img.hidden,false); // Layout box keeps lazy loading active.
  intersection([{isIntersecting:true}]);
  assert.equal(img.src,'fallback.png'); // Static must succeed before any animation.
  media.matches=true;
  img.load(); assert.equal(active,true); assert.equal(img.hidden,false);
  media.matches=false; motion(); assert.equal(img.src,'motion.webp');
  img.load(); assert.equal(active,true);
  media.matches=true; motion(); assert.equal(img.src,'fallback.png');
  img.load(); assert.equal(active,true);
  media.matches=false; motion(); img.load();
  doc.hidden=true; visibility(); assert.equal(img.src,'fallback.png');
  img.load(); assert.equal(active,true);
  doc.hidden=false; visibility(); img.error();
  assert.equal(active,false); assert.equal(img.src,'fallback.png');
  img.load(); assert.equal(active,true); // Animation failure still permits static art.
  img.error(); assert.equal(img.hidden,true); assert.equal(active,false);
  visibility(); assert.equal(active,false); // Static failure keeps the legacy fallback.
  assert.equal(Object.keys(root.WoodshedArcadeArt.assets).length,14);
  const untouched = {append() {throw Error('unconfigured artwork mutated fallback');}};
  for (const key of ["dressed-to-the-nines", "interval-basic-training", "history-mystery"]) {
    const asset = root.WoodshedArcadeArt.assets[key];
    assert.equal(asset.static, null); assert.equal(asset.tile, null);
    root.WoodshedArcadeArt.wire(untouched, asset);
  }
  root.WoodshedArcadeArt.wire(untouched, undefined);
});

test('initial static failure never hides the legacy fallback or starts animation', () => {
  const img = {classList:{add() {}}, getAttribute() {return this.src;},
    addEventListener(event,fn) {this[event]=fn;}};
  const root = {matchMedia:()=>({matches:false})};
  let active;
  vm.runInNewContext(fs.readFileSync('static/js/arcade-art.js','utf8'), {
    window:root, document:{createElement:()=>img, querySelectorAll:()=>[], addEventListener() {}}
  });
  root.WoodshedArcadeArt.wire({classList:{toggle(_name,enabled) {active=enabled;}}, append() {}},
    {static:'missing.png',animated:'motion.webp'});
  img.error();
  assert.equal(active,false); assert.equal(img.hidden,true);
  assert.equal(img.src,'missing.png');
});

test('configured tile stays static without visibility observer; existing cabinet image is untouched', () => {
  const img = {classList:{add() {}}, getAttribute() {return this.src;}, addEventListener() {}};
  let appended;
  const root = {matchMedia:()=>({matches:false})};
  const doc = {createElement:()=>img, querySelectorAll:()=>[], addEventListener() {}};
  vm.runInNewContext(fs.readFileSync('static/js/arcade-art.js','utf8'), {window:root, document:doc});
  root.WoodshedArcadeArt.assets.blue = {tile:'reviewed-blue.png', animated:'blue-motion.webp'};
  root.WoodshedArcadeArt.wire({
    classList:{toggle() {}},
    querySelector() {throw Error('must not reuse or overwrite legacy fallback image');},
    append(image) {appended=image;}
  }, root.WoodshedArcadeArt.assets.blue);
  assert.equal(appended, img);
  assert.equal(img.src, 'reviewed-blue.png');
  assert.equal(img.decoding, 'async');
});

test('approved manifest maps eleven distinct installed PNGs and excludes unapproved art', () => {
  const root = {};
  vm.runInNewContext(fs.readFileSync('static/js/arcade-art.js','utf8'), {
    window:root, document:{querySelectorAll:()=>[]}
  });
  const keys = ['plunge-burrow','blue','radio-tuner','scale-keyboard','thirds','wheel-of-woodchuck',
    'note-names','instrument-fingerings','rhythm-hear-pick','key-signatures','transposition'];
  const urls = [];
  for (const key of keys) {
    const asset = root.WoodshedArcadeArt.assets[key];
    assert.equal(asset.tile, `/static/img/arcade/${key}/tile.v1.png`);
    assert.equal(asset.static, `/static/img/arcade/${key}/static.v1.png`);
    assert.equal(asset.animated, null);
    for (const [slot, size, budget] of [['tile',128,50000], ['static',256,150000]]) {
      const png = fs.readFileSync(asset[slot].slice(1));
      assert.equal(png.subarray(1,4).toString(), 'PNG');
      assert.equal(png.readUInt32BE(16),size);
      assert.equal(png.readUInt32BE(20),size);
      assert.ok(png.length <= budget, `${key} ${slot} exceeds budget`);
    }
    urls.push(asset.tile);
  }
  assert.equal(new Set(urls).size,11);
  assert.equal(root.WoodshedArcadeArt.assets['rhythm-baseball'],undefined);
});
