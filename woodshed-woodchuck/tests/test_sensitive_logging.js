const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/arcade-economy.js', 'utf8');

for (const failure of ['network', 'rejection', 'parse']) {
  test(`emitted Arcade ${failure} diagnostics exclude URL capabilities`, async () => {
    const emitted = [];
    const context = {
      document: {querySelectorAll: () => []},
      console: {warn: (...args) => emitted.push(args)},
      setTimeout: callback => callback(),
      fetch: async () => {
        if (failure === 'network') throw new Error('synthetic-network-secret');
        return {ok: failure === 'parse', status: failure === 'parse' ? 200 : 503,
          json: async () => {
            if (failure === 'parse') throw new Error('synthetic-parse-secret');
            return {detail: 'Temporarily unavailable'};
          }};
      },
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    let error;
    try { await context.WoodshedArcadeEconomy.completePlay('synthetic-play-secret', 5); }
    catch (caught) { error = caught; }
    assert.ok(error);
    assert.ok(emitted.length > 0);
    const text = JSON.stringify({emitted, error});
    assert.doesNotMatch(text, /synthetic-(play|network|parse)-secret/);
    assert.equal(error.endpoint, '/arcade/plays/{play_token}/complete');
    assert.equal(emitted[0][1].operation, 'complete');
    assert.equal(emitted[0][1].attempt, 1);
    assert.equal(emitted[0][1].status, failure === 'network' ? null : failure === 'parse' ? 200 : 503);
  });
}
