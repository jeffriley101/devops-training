const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm'), fs = require('node:fs');
const window = {};
vm.runInNewContext(fs.readFileSync('static/js/book-interactions.js', 'utf8'), {window, document: {querySelector: () => null}});
const {aggregate, monday} = window.WWBookStats;
test('BOOK charts cover all authorized rows, exact seconds and overlapping chart dates', () => {
  const charts = Array.from({length: 151}, (_, index) => ({practice_date: '2026-11-09', duration_seconds: index === 150 ? 0 : 59}));
  charts.push({practice_date: '2026-11-08', duration_seconds: 1}, {practice_date: '2025-01-01', duration_seconds: 120});
  const before = JSON.stringify(charts), data = aggregate(charts, '2026-11-09');
  assert.equal(data.totalCharts, 153);
  assert.equal(data.totalDays, 3);
  assert.equal(data.totalSeconds, 150 * 59 + 121);
  assert.equal(data.week.length, 7);
  assert.equal(data.week[0].value, 8850);
  assert.equal(data.week[6].date, '2026-11-15');
  assert.equal(data.weeks.reduce((sum, row) => sum + row.value, 0), 153);
  assert.equal(data.career.at(-1).value, data.totalSeconds);
  assert.equal(JSON.stringify(charts), before);
});
test('ISO practice dates group Monday to Sunday across DST and years', () => {
  assert.equal(monday('2026-11-01'), '2026-10-26');
  assert.equal(monday('2026-03-08'), '2026-03-02');
  assert.equal(monday('2026-01-01'), '2025-12-29');
});
test('zero-time historical charts count as charts but not practice days', () => {
  const data = aggregate([{practice_date:'2026-11-09', duration_seconds:0}], '2026-11-09');
  assert.equal(data.totalDays, 0); assert.equal(data.totalCharts, 1); assert.equal(data.totalSeconds,0);
  assert.equal(aggregate([], '2026-11-09').career.length,0);
  assert.throws(() => aggregate([{practice_date:'2026-11-09',duration_seconds:null}], '2026-11-09'));
});
