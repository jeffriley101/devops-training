const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const window = {};
vm.runInNewContext(fs.readFileSync('static/js/practice-duration.js', 'utf8'), {window});
const duration = window.WWPracticeDuration;

test('seconds display and legacy fallback distinguish missing from zero', () => {
  for (const [seconds, expected] of [[0, '0m'], [1, '1s'], [59, '59s'], [60, '1m'], [119, '1m 59s'], [118, '1m 58s'], [3661, '1h 1m 1s']]) {
    assert.equal(duration.seconds(seconds), expected);
    assert.equal(duration.entrySeconds({pristine: true, detectedPlayingSeconds: seconds, minutes: 20}), seconds);
  }
  assert.equal(duration.entrySeconds({pristine: true, detectedPlayingSeconds: null, minutes: 2}), 120);
  assert.equal(duration.entrySeconds({minutes: 2, detectedPlayingSeconds: 59}), 120);
});

const app = fs.readFileSync('static/js/app.js', 'utf8');
const exportFunction = app.slice(app.indexOf('    function buildExportText('), app.indexOf('\n    }', app.indexOf('    function buildExportText(')) + 6);

test('Book export totals complete saved dataset separately from local legacy data', () => {
  const context = {window, authoritativeCharts: [59, 59].map(seconds => ({
    practice_date: '2026-09-07', duration_seconds: seconds, source: 'pristine', practice_details: []
  }))};
  vm.createContext(context);
  vm.runInContext(exportFunction, context);
  const state = {profile: {}, practiceLog: [{minutes: 99}, {minutes: 999, serverChartId: 1}]};
  const exported = context.buildExportText(state);
  assert.match(exported, /Saved charts — credited duration: 1m 58s \(118 seconds\)/);
  assert.match(exported, /Browser-local legacy entries — separate from saved totals: 1h 39m \(5940 seconds\)/);
  assert.doesNotMatch(exported, /999/);
  context.authoritativeCharts.push({practice_date: '2026-09-07', duration_seconds: 120, source: 'p-book',
    practice_details: ['Scales'], note: 'Same practice also credited', verification: {status: 'approved'}});
  assert.match(context.buildExportText(state), /credited duration: 3m 58s \(238 seconds\)/);
  assert.match(context.buildExportText(state), /Scales — Same practice also credited — Verification approved/);
  context.authoritativeCharts = null;
  assert.match(context.buildExportText(state), /Saved chart totals unavailable/);
});

test('Book positive days and chart count use saved charts even beyond local history cap', () => {
  const start = app.indexOf('    function renderPBookSummary(');
  const context = {authoritativeCharts: Array.from({length: 120}, (_, i) => ({duration_seconds: i ? 1 : 0,
      practice_date: i ? '2026-09-08' : '2026-09-07'})),
    practiceDaysEl: {}, pagesCountEl: {}};
  vm.createContext(context);
  vm.runInContext(app.slice(start, app.indexOf('\n    }', start) + 6), context);
  context.renderPBookSummary({practiceLog: [{minutes: 99, dateKey: '2026-09-09'}]});
  assert.equal(context.practiceDaysEl.textContent, '1');
  assert.equal(context.pagesCountEl.textContent, '120');
});
