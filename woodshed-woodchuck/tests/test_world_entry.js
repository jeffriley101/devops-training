const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/world-entry.js', 'utf8');
process.env.TZ = 'America/Chicago';

function boot({motion = false, authenticated = true, account = 'opaque-account-a', storage = new Map(),
  clock = new Date(2026, 8, 14, 12).getTime(), width = 1200, height = 800,
  broken = false, storageBlocked = false, dialogBroken = false} = {}) {
  const nodes = [], events = {}, documentEvents = {}, timers = new Map(), navigations = [];
  let now = 0, next = 0;
  class Node {
    constructor(tagName = 'DIV') {
      this.tagName = tagName; this.dataset = {}; this.style = {overflow: ''};
      this.listeners = {}; this.children = []; this.attrs = {}; this.inert = false;
      this.complete = true; this.naturalWidth = broken ? 0 : 1824; this.naturalHeight = 1368;
      const classes = new Set();
      this.classList = {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)};
      this.animations = []; nodes.push(this);
    }
    append(node) { this.children.push(node); node.parent = this; }
    setAttribute(k, v) { this.attrs[k] = v; }
    getAttribute(k) { return this.attrs[k] ?? null; }
    hasAttribute(k) { return k in this.attrs; }
    addEventListener(k, fn) { this.listeners[k] = fn; }
    fire(k, event = {}) {
      const e = {preventDefault() {}, ...event}; this.listeners[k]?.(e);
      if (['click','pointerup'].includes(k) && this.parent) this.parent.fire(k, e);
    }
    focus() { document.activeElement = this; }
    showModal() { if (dialogBroken) throw Error('unsupported'); this.open = true; }
    close() { this.open = false; }
    remove() { this.removed = true; }
    closest(selector) { return selector === '.welcome-hero' ? hero : this.entry ? this : null; }
    getBoundingClientRect() { return {left: 20, top: 20, right: 1180, bottom: 795, width: 1160, height: 775}; }
    animate(frames, options) { const a = {frames, options, cancel() { this.cancelled = true; }}; this.animations.push(a); return a; }
  }
  const body = new Node('BODY'), shell = new Node(), hero = new Node(), original = new Node('IMG');
  body.append(shell); body.style.overflow = 'auto';
  body.dataset = {authenticated: String(authenticated), worldEntryAccount: account};
  original.src = '/static/img/woodshed-painting.png';
  const document = {body, activeElement: null,
    createElement: tag => new Node(tag.toUpperCase()),
    querySelector: selector => selector === '.welcome-hero-art' ? original : {},
    addEventListener: (k, fn) => { documentEvents[k] = fn; }};
  const dailyStorage = {
    getItem: k => { if (storageBlocked) throw Error('blocked'); return storage.get(k) ?? null; },
    setItem: (k, v) => { if (storageBlocked) throw Error('blocked'); storage.set(k, v); },
    removeItem: k => storage.delete(k),
  };
  const window = {innerWidth: width, innerHeight: height,
    matchMedia: () => ({matches: motion}),
    getComputedStyle: () => ({objectPosition: '48% 14%'}),
    location: {pathname: '/store', assign: href => navigations.push(href)},
    addEventListener: (k, fn) => { events[k] = fn; },
    setTimeout: (fn, ms) => { timers.set(++next, {fn, at: now + ms}); return next; },
    clearTimeout: id => timers.delete(id),
    localStorage: dailyStorage,
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  };
  class LocalDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock + now])); }
    static now() { return clock + now; }
  }
  vm.runInNewContext(source, {window, document, Image: Node, URL, Date: LocalDate});
  function advance(ms) {
    const end = now + ms;
    while (true) {
      const due = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at; timers.delete(due[0]); due[1].fn();
    }
    now = end;
  }
  function click(kind = 'arcade', override = {}) {
    const link = new Node('A'); link.entry = Boolean(kind);
    link.dataset.worldEntry = kind; link.href = kind === 'woodshed' ? 'https://example.test/home' : 'https://example.test/arcade';
    const e = {target: link, button: 0, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...override};
    documentEvents.click(e); return {link, e};
  }
  return {body, shell, nodes, events, advance, click, navigations, storage, original, document, timers,
    overlay: () => nodes.findLast(n => n.tagName === 'DIALOG' && !n.removed)};
}

test('only the marked authenticated entrance link flies to its original /home', () => {
  const ui = boot();
  assert.equal(ui.click(null).e.defaultPrevented, false);
  const {e} = ui.click('woodshed'); assert.equal(e.defaultPrevented, true);
  assert.match(ui.overlay().className, /woodshed/);
  const image = ui.overlay().children[0].children[0];
  assert.equal(image.src, '/static/img/woodshed-painting.png');
  assert.equal(image.animations[0].options.duration, 1080);
  ui.advance(1219); assert.equal(ui.navigations.length, 0);
  ui.advance(1); assert.deepEqual(ui.navigations, ['https://example.test/home']);
});
test('anonymous, modified, disabled and already-cancelled link clicks stay with original handlers', () => {
  assert.equal(boot({authenticated: false}).click('woodshed').e.defaultPrevented, false);
  for (const override of [{ctrlKey:true}, {metaKey:true}, {shiftKey:true}, {altKey:true}, {button:1}]) {
    const ui = boot(); assert.equal(ui.click('woodshed', override).e.defaultPrevented, false); assert.equal(ui.overlay(), undefined);
  }
  const ui = boot(); ui.click('woodshed', {defaultPrevented:true}); assert.equal(ui.overlay(), undefined);
});
for (const early of ['click', 'pointerup', 'Enter', ' ']) test(`Arcade ${early} continuation navigates once`, () => {
  const ui = boot(); ui.click(); const overlay = ui.overlay();
  assert.equal(overlay.children[0].children[0].src, '/static/img/arcade/arcade-entry-splash.png');
  assert.equal(ui.shell.inert, true);
  assert.equal(ui.document.activeElement, overlay);
  if (early === 'click') { overlay.fire('click'); overlay.fire('click'); }
  else if (early === 'pointerup') { overlay.fire('pointerup', {button:0,pointerType:'touch'}); overlay.fire('click'); }
  else if (early) { overlay.fire('keydown', {key: early}); overlay.fire('click'); }
  ui.click(); ui.advance(2000);
  assert.deepEqual(ui.navigations, ['https://example.test/arcade']);
  ui.advance(10000); assert.equal(ui.navigations.length, 1);
  assert.equal(ui.shell.inert, false); assert.equal(ui.body.style.overflow, 'auto');
});
test('same local day skips across reload/logout; separate accounts do not suppress each other', () => {
  const ui = boot(); ui.click(); ui.overlay().fire('click'); ui.advance(2000);
  assert.equal(boot({storage:ui.storage}).click().e.defaultPrevented, false);
  assert.equal(boot({storage:ui.storage,account:'opaque-account-b'}).click().e.defaultPrevented, true);
  boot({storage:ui.storage,authenticated:false});
  assert.equal(boot({storage:ui.storage}).click().e.defaultPrevented, false);
  assert.deepEqual([...ui.storage.values()], ['2026-09-14','2026-09-14']);
});
test('page left open across local midnight shows splash again without logout', () => {
  const ui = boot({clock:new Date(2026,8,14,23,59,50).getTime()});
  ui.click(); ui.overlay().fire('click'); ui.advance(5000);
  assert.equal(ui.click().e.defaultPrevented,false);
  ui.advance(5001);
  assert.equal(ui.click().e.defaultPrevented,true);
  assert.equal([...ui.storage.values()][0],'2026-09-15');
});
test('daily key uses browser local date, not UTC date', () => {
  const ui = boot({clock:new Date('2026-09-15T01:00:00Z').getTime()});
  ui.click(); assert.equal([...ui.storage.values()][0],'2026-09-14');
});
test('blocked localStorage does not block continuation or same-page daily checks', () => {
  const ui = boot({storageBlocked:true}); ui.click(); ui.overlay().fire('click'); ui.advance(6000);
  assert.equal(ui.navigations.length, 1); assert.equal(ui.click().e.defaultPrevented, false);
  ui.advance(24*60*60*1000); assert.equal(ui.click().e.defaultPrevented,true);
});
for (const motion of [false,true]) test(`failed cached image navigates even with reduced motion=${motion}`, () => {
  const ui = boot({broken:true,motion}); ui.click(); ui.advance(300);
  assert.equal(ui.navigations.length, 1);
});
test('late image error and image never loading both have bounded navigation', () => {
  const ui = boot(); ui.click(); ui.overlay().children[0].children[0].fire('error'); ui.advance(140);
  assert.equal(ui.navigations.length, 1);
  const slow = boot(); slow.original.complete = false; slow.click('woodshed'); slow.advance(140);
  assert.equal(slow.navigations.length, 1);
});
test('reduced motion woodshed uses 300ms crossfade, no zoom', () => {
  const ui = boot({motion:true}); ui.click('woodshed');
  assert.equal(ui.overlay().children[0].children[0].animations.length, 0);
  ui.advance(299); assert.equal(ui.navigations.length, 0);
  ui.advance(1); assert.equal(ui.navigations.length, 1);
});
test('unsupported animation/dialog path still navigates', () => {
  const ui = boot({dialogBroken:true}); ui.click(); ui.advance(140); assert.equal(ui.navigations.length,1);
});
test('pagehide/pageshow cancel timers, restore focus/inert/scroll and permit a new flight', () => {
  const ui = boot(); const {link} = ui.click('woodshed');
  ui.events.pagehide(); ui.events.pageshow(); ui.advance(6000);
  assert.equal(ui.navigations.length, 0); assert.equal(ui.shell.inert,false);
  assert.equal(ui.body.style.overflow,'auto'); assert.equal(ui.document.activeElement,link);
  ui.click('woodshed'); ui.advance(1220); assert.equal(ui.navigations.length,1);
});
test('retiring the session cancels the flight and prevents later entry overlays', () => {
  const ui = boot(); ui.click('woodshed');
  ui.events['ww:session-changed'](); ui.advance(6000);
  assert.equal(ui.navigations.length, 0);
  assert.equal(ui.shell.inert, false);
  assert.equal(ui.body.style.overflow, 'auto');
  assert.equal(ui.overlay(), undefined);
  assert.equal(ui.click('woodshed').e.defaultPrevented, false);
});
test('overlay traps Tab/Escape, and restores focus if user-directed navigation stalls', () => {
  const ui = boot(); const {link} = ui.click(); const overlay = ui.overlay();
  let prevented = false;
  overlay.fire('keydown', {key:'Tab', preventDefault() { prevented = true; }});
  assert.equal(prevented,true); assert.equal(ui.document.activeElement,overlay);
  overlay.fire('cancel'); ui.advance(10000); assert.equal(ui.navigations.length,0);
  overlay.fire('click'); ui.advance(140); assert.equal(ui.navigations.length,1);
  ui.advance(4000); assert.equal(ui.document.activeElement,link);
  assert.equal(ui.overlay(),undefined); assert.equal(ui.shell.inert,false);
});

for (const motion of [false,true]) test(`Arcade motion=${motion} waits indefinitely without any continuation timer`, () => {
  const ui = boot({motion}); ui.click(); const overlay = ui.overlay();
  assert.equal(ui.timers.size,0);
  assert.equal(overlay.children[1].textContent,'Tap anywhere to enter the Arcade');
  ui.advance(60*60*1000);
  assert.equal(ui.navigations.length,0); assert.equal(overlay.open,true);
  assert.equal(ui.shell.inert,true);
  overlay.fire('keydown',{key:'Enter'}); ui.advance(140); assert.equal(ui.navigations.length,1);
});
for (const surface of ['artwork','surround','instruction']) test(`entire ${surface} surface accepts bubbling pointer/click continuation`, () => {
  const ui = boot(); ui.click(); const overlay = ui.overlay();
  const target = surface === 'artwork' ? overlay.children[0].children[0] : surface === 'instruction' ? overlay.children[1] : overlay;
  target.fire('pointerup',{button:0,pointerType:'touch'}); target.fire('click');
  ui.advance(140); assert.equal(ui.navigations.length,1);
});
for (const [width,height] of [[1440,900],[390,844]]) test(`flight ${width}px uses one concurrent diagonal interpolation to the house`, () => {
  const ui = boot({width,height}); ui.click('woodshed');
  const stage = ui.overlay().children[0], art = stage.children[0];
  const {frames, options} = art.animations[0];
  assert.equal(frames.length,2); assert.equal(options.duration,1080);
  assert.equal(options.easing,'cubic-bezier(.7,0,.9,.35)');
  const parse = frame => frame.transform.match(/-?[\d.]+/g).map(Number);
  const [x0,y0,s0] = parse(frames[0]), [x1,y1,s1] = parse(frames[1]);
  assert.notEqual(x0,x1); assert.notEqual(y0,y1); assert.ok(s1>s0);
  assert.ok(Math.abs(x1+1824*.65*s1-width/2)<.001);
  assert.ok(Math.abs(y1+1368*.458*s1-height/2)<.001);
  assert.equal(stage.animations[0].options.easing,options.easing);
  assert.equal(stage.animations[0].options.duration,options.duration);
  const coverProgress = stage.animations[0].frames[1].offset;
  const cx = x0+(x1-x0)*coverProgress, cy = y0+(y1-y0)*coverProgress;
  const cs = s0+(s1-s0)*coverProgress;
  assert.ok(cx<=0 && cy<=0 && cx+1824*cs>=width && cy+1368*cs>=height,
    'the frame reaches the viewport only when artwork covers every edge');
  // Both axes and scale share exactly one progress value at every point.
  for (const p of [.01,.1,.5,.9]) {
    const x = x0+(x1-x0)*p, y = y0+(y1-y0)*p;
    assert.ok(Math.abs((x-x0)/(x1-x0)-(y-y0)/(y1-y0))<1e-10);
  }
});
