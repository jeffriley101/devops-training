"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const rootPath = path.resolve(__dirname, "..");
const coreSource = fs.readFileSync(path.join(rootPath, "static/js/plunge-burrow.js"), "utf8");
const adapterSource = fs.readFileSync(path.join(rootPath, "static/js/guest-plunge.js"), "utf8");
const template = fs.readFileSync(path.join(rootPath, "templates/_guest_plunge.html"), "utf8");
const {PlungeBurrowGame} = require("../static/js/plunge-burrow.js");

// Real pickup/collision mechanics work without any storage or account evidence.
let storageCalls = 0;
const noStorage = {getItem() { storageCalls++; throw new Error("Unexpected storage read"); },
  setItem() { storageCalls++; throw new Error("Unexpected storage write"); }};
const localGame = new PlungeBurrowGame({random: () => 0.41, storage: noStorage, trackBest: false});
localGame.obstacles = []; localGame.portals = [];
localGame.dandelion = {x: 11, y: 10};
localGame.start(); localGame.tick();
assert.equal(localGame.score, 1);
localGame.carrot = {x: 12, y: 10}; localGame.tick();
assert.equal(localGame.score, 4);
localGame.bandSet = ["Flute", "Clarinet", "Trumpet"];
localGame.instrument = {x: 13, y: 10, name: "Saxophone", icon: "🎷"}; localGame.tick();
assert.equal(localGame.score, 29);
assert.equal(localGame.bandSet.length, 0);
localGame.hearts = 1; localGame.handleCollision("wall");
assert.equal(localGame.status, "gameover");
assert.equal(localGame.best, 0);
assert.equal(localGame.readBest(), 0);
localGame.writeBest(999);
assert.equal(storageCalls, 0);
localGame.reset();
assert.equal(localGame.score, 0);
assert.equal(localGame.start(), true);

// The opt-out leaves the existing account best-score behavior intact.
let savedBest = "6";
const accountGame = new PlungeBurrowGame({storage: {getItem: () => savedBest, setItem: (_key, value) => { savedBest = value; }}});
accountGame.start(); accountGame.score = 12; accountGame.hearts = 1; accountGame.handleCollision("wall");
assert.equal(savedBest, "12");

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatch(type, extra = {}) {
    const event = {type, target: this, preventDefault() {}, ...extra};
    for (const listener of this.listeners.get(type) || []) listener(event);
  }
}
class Element extends Events {
  constructor(id, attributes = "") {
    super(); this.id = id; this.hidden = /\bhidden\b/.test(attributes);
    this.disabled = /\bdisabled\b/.test(attributes); this.textContent = "";
    this.attributes = new Map(); this.children = []; this.dataset = {};
    this.classList = {toggle() {}};
  }
  setAttribute(key, value) { this.attributes.set(key, value); }
  replaceChildren(...children) { this.children = children; }
  appendChild(child) { this.children.push(child); }
  focus() {}
  closest() { return null; }
  getBoundingClientRect() { return {width: 400}; }
  click() { if (!this.disabled) this.dispatch("click"); }
}
function mount() {
  const browser = new Events();
  const document = new Events();
  const elements = new Map();
  for (const match of template.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) elements.set(match[1], new Element(match[1], match[0]));
  const directions = ["up", "down", "left", "right"].map(value => {
    const button = new Element("direction-" + value, "disabled");
    button.dataset.guestPlungeDirection = value;
    return button;
  });
  const panel = elements.get("guest-plunge-panel");
  panel.querySelectorAll = () => directions;
  const drawing = new Proxy({}, {get(target, name) { return target[name] || (() => {}); }});
  elements.get("guest-plunge-canvas").getContext = () => drawing;
  document.body = {dataset: {guest: "local"}};
  document.hidden = false;
  document.getElementById = id => elements.get(id) || null;
  document.createElement = tag => new Element(tag);
  browser.document = document;
  browser.devicePixelRatio = 1;
  browser.current = true;
  browser.setup = false;
  browser.WWSessionBoundary = {isCurrent: () => browser.current};
  browser.WWGuest = {isCurrent: () => browser.current && browser.setup};
  let ioCalls = 0;
  for (const name of ["localStorage", "sessionStorage", "indexedDB", "caches", "WoodshedArcadeEconomy", "WWState", "WoodshedAudio"]) {
    Object.defineProperty(browser, name, {get() { ioCalls++; throw new Error(`Unexpected ${name}`); }});
  }
  browser.fetch = () => { ioCalls++; throw new Error("Unexpected request"); };
  browser.navigator = {sendBeacon() { ioCalls++; throw new Error("Unexpected beacon"); }};
  browser.XMLHttpRequest = function () { ioCalls++; throw new Error("Unexpected request"); };
  const frames = new Map();
  let frameId = 0;
  browser.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
  browser.cancelAnimationFrame = id => frames.delete(id);
  const sandbox = vm.createContext({window: browser});
  // An account-like canvas must not activate the legacy account DOM adapter.
  const originalLookup = document.getElementById;
  document.getElementById = () => { throw new Error("Guest core must not bind account UI"); };
  vm.runInContext(coreSource, sandbox);
  document.getElementById = originalLookup;
  let game;
  const RealGame = browser.PlungeBurrowCore.PlungeBurrowGame;
  browser.PlungeBurrowCore.PlungeBurrowGame = class extends RealGame {
    constructor(options) { super(options); game = this; }
  };
  for (const name of ["open", "close", "start", "pause", "replay"]) assert.equal(elements.get("guest-plunge-" + name).disabled, true);
  vm.runInContext(adapterSource, sandbox);
  return {browser, document, game, frames, elements, directions, ioCalls: () => ioCalls,
    button: name => elements.get("guest-plunge-" + name),
    frame(timestamp) {
      const next = frames.entries().next().value;
      assert.ok(next, "A live run should schedule animation");
      frames.delete(next[0]); next[1](timestamp);
    }};
}

const live = mount();
assert.equal(live.browser.WWGuestPlunge.ready, true);
assert.equal(live.game.storage, null);
assert.equal(live.game.trackBest, false);
assert.equal(live.button("open").disabled, true);
live.browser.setup = true;
live.browser.dispatch("ww:guest-setup");
live.button("open").click(); live.button("start").click();
assert.equal(live.game.status, "running");
assert.equal(live.frames.size, 1);
// Actual frame scheduling collects a pickup and renders its current score.
live.game.obstacles = []; live.game.portals = [];
live.game.dandelion = {x: 11, y: 10};
live.frame(0); live.frame(220);
assert.equal(live.button("score").textContent, "1");
live.button("pause").click();
assert.equal(live.game.status, "paused"); assert.equal(live.frames.size, 0);
live.button("pause").click();
assert.equal(live.game.status, "running"); assert.equal(live.frames.size, 1);
live.document.hidden = true; live.document.dispatch("visibilitychange");
assert.equal(live.game.status, "paused"); assert.equal(live.frames.size, 0);
live.document.hidden = false; live.button("pause").click();
// Client score tampering changes a local result without creating server authority.
live.game.score = 2147483647; live.game.hearts = 1; live.game.handleCollision("wall");
assert.equal(live.button("score").textContent, "2147483647");
assert.equal(live.game.best, 0); assert.equal(live.ioCalls(), 0);
const snapshot = live.browser.WWGuestPlunge.snapshot();
assert.equal("best" in snapshot, false);
snapshot.trail[0].x = -999;
assert.notEqual(live.game.trail[0].x, -999);
assert.throws(() => { snapshot.score = 10; }, TypeError);
live.button("replay").click();
assert.equal(live.game.status, "ready"); assert.equal(live.button("score").textContent, "0");
live.button("start").click(); assert.equal(live.game.status, "running");
live.button("close").click();
assert.equal(live.game.score, 0); assert.equal(live.frames.size, 0);
assert.equal(live.button("panel").hidden, true);

for (const type of ["ww:guest-reset", "ww:guest-discarded", "ww:session-changed", "pagehide", "pageshow"]) {
  live.button("open").click(); live.button("start").click();
  live.game.score = 42;
  live.browser.dispatch(type, {persisted: true});
  assert.equal(live.game.score, 0, `${type} must discard score`);
  assert.equal(live.game.status, "ready");
  assert.equal(live.button("panel").hidden, true);
  assert.equal(live.button("score").textContent, "0");
  assert.equal(live.frames.size, 0);
}

// A stale page cannot play even if someone manually reenables its buttons.
live.button("open").click(); live.button("start").click();
live.browser.current = false; live.browser.dispatch("ww:session-changed");
live.button("panel").hidden = false;
live.button("start").disabled = false; live.button("start").click();
assert.equal(live.game.status, "ready"); assert.equal(live.frames.size, 0);
live.directions[0].disabled = false; live.directions[0].click();
assert.equal(live.game.queuedDirection, null);
assert.equal(live.ioCalls(), 0);
console.log("Guest Plunge engine, controls, score tampering, replay, lifecycle, stale-page and zero-I/O checks passed.");

// Exercise the complete registered adapter with a changing ownership boundary.
const test = require('node:test');
function registered(saved = null) {
  const browser = new Events(), document = new Events(), elements = new Map();
  const html = fs.readFileSync(path.join(rootPath, 'templates/plunge_burrow.html'), 'utf8');
  for (const m of html.matchAll(/\bid="([^"]+)"/g)) elements.set(m[1], new Element(m[1]));
  const drawing = new Proxy({}, {get: (o,k) => o[k] || (() => {})});
  elements.get('plunge-canvas').getContext = () => drawing;
  for (const el of elements.values()) {
    el.classList = {add() {}, remove() {}, toggle() {}};
    el.append = (...items) => el.children.push(...items);
  }
  document.body = {dataset: {}}; document.hidden = false;
  document.getElementById = id => elements.get(id) || null;
  document.querySelectorAll = () => [];
  document.createElement = tag => {const e = new Element(tag); e.append = (...items) => e.children.push(...items);return e;};
  let owner = {accountId:'A', pageGeneration:'generation-A', epoch:'1'}, current = true;
  browser.WWSessionBoundary = {binding: () => ({...owner}), matchesBinding: saved => current &&
    ['accountId','pageGeneration','epoch'].every(key => saved?.[key] === owner[key])};
  const values = new Map(saved ? [[ 'woodshed.plungeBurrow.bestScore', saved ]] : []);
  let writes = 0, completions = 0;
  browser.localStorage = {getItem: k => values.get(k) ?? null, setItem: (k,v) => {writes++;values.set(k,v);}};
  const frames = new Map();let frame = 0;
  browser.requestAnimationFrame = fn => {frames.set(++frame,fn);return frame;};
  browser.cancelAnimationFrame = id => frames.delete(id);
  browser.setTimeout = fn => {fn();};browser.devicePixelRatio = 1; browser.document = document;
  browser.WoodshedArcadeEconomy = {startPlay: async () => ({play_token:'synthetic'}),
    completePlay: async () => {completions++;return {};}, loadStatus: async () => ({})};
  const sandbox = vm.createContext({window: browser});
  // Capture the real instance through its production start method.
  vm.runInContext(coreSource, sandbox);
  const start = browser.PlungeBurrowCore.PlungeBurrowGame.prototype.start;
  browser.PlungeBurrowCore.PlungeBurrowGame.prototype.start = function () {browser.testGame = this;return start.call(this);};
  return {browser, get game() {return browser.testGame;}, elements, frames, values,
    start: async () => {elements.get('plunge-start').click();await new Promise(resolve => setImmediate(resolve));},
    change: (key, value) => {owner = {...owner,[key]:value};}, invalidate: () => {current=false;},
    writes: () => writes, completions: () => completions};
}
test('registered Plunge cache requires exact account, generation and epoch ownership', async () => {
  for (const key of ['accountId','pageGeneration','epoch']) {
    const r = registered();await r.start();r.game.writeBest(7);
    assert.equal(r.writes(),1);
    const valid = r.values.get('woodshed.plungeBurrow.bestScore');
    assert.equal(JSON.parse(valid).binding.accountId,'A');
    r.change(key,'new-owner');
    r.game.writeBest(99);
    assert.equal(r.game.readBest(),0);
    assert.equal(r.writes(),1);
    const callback = [...r.frames.values()][0];r.frames.clear();callback(1000);
    assert.equal(r.game.status,'paused');assert.equal(r.frames.size,0);
    assert.equal(r.elements.get('plunge-start').disabled,true);
  }
  for (const saved of ['99',JSON.stringify({binding:{accountId:'B',pageGeneration:'generation-A',epoch:'1'},value:'99'})]) {
    const r=registered(saved);await r.start();assert.equal(r.game.best,0,'unowned cache is not imported');
  }
});
test('registered Plunge stops on session/page restoration and late collision cannot write or submit', async () => {
  for (const event of ['ww:session-changed','pagehide','pageshow']) {
    const r = registered();await r.start();r.game.score=17;
    r.browser.dispatch(event,{persisted:true});
    assert.equal(r.game.status,'paused');assert.equal(r.frames.size,0);
    // Even a queued callback that resumes core mechanics cannot write or submit.
    r.game.status='running';r.game.hearts=1;r.game.handleCollision('wall');
    r.game.writeBest(123);
    assert.equal(r.writes(),0);assert.equal(r.completions(),0);
    assert.equal(r.elements.get('plunge-restart').disabled,true);
  }
});
