const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness(mode = 'Challenge', stored = null) {
  const listeners = {}, ids = new Map(), storage = new Map(), voices = [], intervals = new Map();
  let timer = 0, context, volume = .2, background;
  const element = () => ({value: '', textContent: '', hidden: false, listeners: {}, offsetWidth: 1,
    classList: {add(){}, remove(){}}, setAttribute(){}, scrollIntoView(){}, focus(){},
    addEventListener(key, fn) {this.listeners[key] = fn;}});
  for (const id of ['open-button','close-button','panel','start-button','tap-button','slower-button',
    'faster-button','bpm-range','bpm-input','bpm-readout','pulse','status','mode-controls',
    'subdivision','meter','sound','volume']) ids.set('metronome-' + id, element());
  ids.set('woodchuck-appearance-data', {textContent: JSON.stringify({weekly_modes_enabled: true, game_mode: mode})});
  const document = {body: {dataset: {accountId: 'WC-TOOLS'}}, hidden: false,
    getElementById: id => ids.get(id), addEventListener(){}};
  if (stored !== null) storage.set('woodshed:practice-tools:v1:WC-TOOLS', stored);
  class AudioContext {
    constructor() {context = this; this.currentTime = 0; this.state = 'running'; this.destination = {};}
    resume() {this.state = 'running'; return Promise.resolve();}
    suspend() {this.state = 'suspended'; return Promise.resolve();}
    createOscillator() {
      const voice = {type: '', frequency: {setValueAtTime(value) {voice.frequencyValue = value;}},
        connect(){}, disconnect(){}, start(time) {voice.time = time; voices.push(voice);}, stop(){}};
      return voice;
    }
    createGain() {
      const gain = {connect(){}, disconnect(){}, gain: {setValueAtTime(){},
        exponentialRampToValueAtTime(value, time) {if (value > .0001) gain.peak = value; gain.end = time;}}};
      const original = gain.connect;
      gain.connect = (...args) => {original(...args); gain.voice = null;};
      // createGain follows createOscillator; retain its audible envelope.
      const oldPush = voices.push;
      voices.push = function(voice) {voice.envelope = gain; voices.push = oldPush; return oldPush.call(this, voice);};
      return gain;
    }
  }
  const window = {document, localStorage: {getItem: key => storage.get(key) || null, setItem: (k,v) => storage.set(k,v)},
    AudioContext, WoodshedAudio: {isEnabled: () => true, getVolume: () => volume, setVolume: v => volume = v},
    WWLifecycle: {onBackground: fn => background = fn},
    addEventListener(key, fn) {(listeners[key] ||= []).push(fn);},
    dispatchEvent(event) {for (const fn of listeners[event.type] || []) fn(event);},
    setTimeout: () => ++timer, clearTimeout(){},
    setInterval(fn) {intervals.set(++timer, fn); return timer;}, clearInterval: id => intervals.delete(id)};
  window.WWState = {getState: () => ({account:{authenticated:true,woodchuckId:'WC-TOOLS'},profile:{game_mode:mode}})};
  window.WWSessionBoundary = {isCurrent:()=>true,accountId:()=> 'WC-TOOLS'};
  const sandbox = {window, document, localGuest: false, toolIsCurrent: () => true, performance: {now: () => 0},
    CustomEvent: class {constructor(type, options = {}) {this.type=type; this.detail=options.detail;}}};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync('static/js/practice-tools.js','utf8'), sandbox);
  const app = fs.readFileSync('static/js/app.js','utf8');
  vm.runInContext(app.slice(app.indexOf('  function wireMetronome() {'), app.indexOf('  const BACK_TO_SCHOOL_READINESS_CHALLENGES')) + '\nwireMetronome();', sandbox);
  return {setMode: value => {mode=value;window.dispatchEvent({type:'ww:game-mode-projected'});}, window, ids, voices, intervals, storage, context: () => context, volume: () => volume, background: () => background()};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('mode presets retain measured note/center and exact legacy tuner boundaries', () => {
  const sandbox = {};
  vm.runInNewContext(fs.readFileSync('static/js/tuner.js','utf8'), sandbox);
  const tuner = sandbox.WWTuner;
  assert.equal(tuner.classifyCents(4), 'SHARP');
  assert.equal(tuner.classifyCents(4, {targetWindow: 5}), 'PRISTINE');
  assert.equal(tuner.classifyCents(5, {targetWindow: 5}), 'SHARP');
  assert.equal(tuner.frequencyToNote(440).name, 'A4');
  assert.equal(tuner.frequencyToNote(440 * 2 ** (4/1200)).name, 'A4');
  assert.equal(tuner.frequencyToNote(466.1637615).name, 'A♯4');
});

test('real metronome scheduler uses subdivisions, meter accent and existing clock', async () => {
  const h = harness();
  h.ids.get('metronome-subdivision').value = '3';
  h.ids.get('metronome-meter').value = '3';
  h.ids.get('metronome-sound').value = 'Clave';
  h.ids.get('metronome-subdivision').listeners.change();
  h.ids.get('metronome-start-button').listeners.click(); await flush();
  assert.equal(h.intervals.size, 1);
  h.context().currentTime = 1.5;
  [...h.intervals.values()][0]();
  assert.equal(h.voices[0].frequencyValue, 2500);
  assert.ok(Math.abs(h.voices[1].time - h.voices[0].time - 1/6) < 1e-9);
  assert.ok(h.voices[0].envelope.peak > h.voices[3].envelope.peak);
  assert.ok(h.voices[3].envelope.peak > h.voices[1].envelope.peak);
  assert.equal(h.voices[9].envelope.peak, h.voices[0].envelope.peak);
  h.background();
  assert.equal(h.intervals.size, 0);
  assert.equal(h.context().state, 'suspended');
});

test('switching modes restores mode settings and never changes volume', async () => {
  const h = harness();
  h.ids.get('metronome-subdivision').value = '2';
  h.ids.get('metronome-sound').value = 'Snap';
  h.ids.get('metronome-sound').listeners.change();
  h.ids.get('metronome-start-button').listeners.click(); await flush();
  assert.equal(h.voices[0].type, 'triangle');
  assert.equal(h.voices[0].frequencyValue, 1600);
  h.setMode('Beginner');
  assert.equal(h.intervals.size, 0);
  assert.equal(h.volume(), .2);
  assert.equal(h.ids.get('metronome-mode-controls').hidden, true);
  h.setMode('Challenge');
  assert.equal(h.ids.get('metronome-subdivision').value, '2');
  assert.equal(h.ids.get('metronome-sound').value, 'Snap');
  h.ids.get('metronome-volume').value = '.1';
  h.ids.get('metronome-volume').listeners.input();
  assert.equal(h.volume(), .1);
});

test('unrelated malformed legacy caches cannot set mode or disable controls', () => {
  for (const stored of ['"old value"', '42', '[]', '{"Challenge":null}']) {
    const h = harness('Challenge', stored);
    assert.doesNotThrow(() => h.window.WWPracticeTools.saveMetronome({subdivision: 2}));
    assert.equal(h.window.WWPracticeTools.currentMetronome().subdivision, 2);
  }
});


test('NULL and forged mode events do not infer a selection or write a new cache', async () => {
  const h=harness(null);
  assert.equal(h.window.WWPracticeTools.getMode(), null);
  assert.equal(h.ids.get('metronome-mode-controls').hidden,true);
  h.window.dispatchEvent({type:'woodshed:mode',detail:{mode:'Performance'}});
  assert.equal(h.window.WWPracticeTools.getMode(),null);
  assert.equal(h.window.WWPracticeTools.currentTuner().targetWindow,2);
  h.window.WWPracticeTools.saveMetronome({subdivision:3});
  assert.deepEqual([...h.storage], [['woodshedWoodchuckMetronomeBpm','120']]);
  h.ids.get('metronome-start-button').listeners.click(); await flush();
  assert.equal(h.intervals.size,1);
  assert.equal(h.voices[0].frequencyValue,850);
  assert.equal(h.window.WWPracticeTools.getMode(),null);
  h.background();assert.equal(h.intervals.size,0);
});

test('all modes use the exact approved tuning presets and both advanced modes match', () => {
  const h=harness();
  for(const [mode,expected] of [[null,[2,5,2]],['Beginner',[5,7,3]],['Challenge',[2,5,2]],['Performance',[2,5,2]]]) {
    h.setMode(mode);
    const x=h.window.WWPracticeTools.currentTuner();
    assert.deepEqual([x.targetWindow,x.history,x.stableFrames],expected);
    assert.equal(h.window.WWPracticeTools.advanced(),['Challenge','Performance'].includes(mode));
  }
});

test('existing pitch detector preserves measured pitch, distinct note identity, and silence', () => {
  const s={};vm.runInNewContext(fs.readFileSync('static/js/tuner.js','utf8'),s);
  for(const hz of [440,466.1637615]) {
    const samples=new Float32Array(4096);
    for(let i=0;i<samples.length;i++) samples[i]=.5*Math.sin(2*Math.PI*hz*i/48000);
    const measured=s.WWTuner.detectPitch(samples,48000);
    assert.ok(Math.abs(measured-hz)<.5);
    assert.equal(s.WWTuner.frequencyToNote(measured).name,hz===440?'A4':'A♯4');
  }
  assert.equal(s.WWTuner.detectPitch(new Float32Array(4096),48000),null);
  for(const [cents,want] of [[-21,'VERY FLAT'],[-20,'FLAT'],[-2,'FLAT'],[-1.999,'PRISTINE'],[0,'PRISTINE'],[1.999,'PRISTINE'],[2,'SHARP'],[20,'SHARP'],[21,'VERY SHARP']]) assert.equal(s.WWTuner.classifyCents(cents),want);
});
