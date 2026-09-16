const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function pending() {
  let deliver, reject;
  return {promise: new Promise((resolve, fail) => {deliver = resolve; reject = fail;}),
    deliver: value => deliver(value), reject: error => reject(error)};
}
function harness({delayStatus = false} = {}) {
  const elements = new Map(), storage = new Map(), timers = [];
  const element = () => ({textContent: '', dataset: {}, children: [], handlers: {},
    replaceChildren() {this.children = [];}, appendChild(child) {this.children.push(child);},
    querySelector() {return this.children[0];}, focus() {},
    addEventListener(event, handler) {this.handlers[event] = handler;}});
  const economyMessage = element();
  const status = pending();
  const context = {
    document: {
      querySelector: () => ({}),
      querySelectorAll: selector => selector === "[data-arcade-economy-message]" ? [economyMessage] : [],
      getElementById: id => {
        if (!id.startsWith('history-mystery-')) return null;
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      }, createElement: element,
    },
    CustomEvent: class {},
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
    dispatchEvent() {}, sessionStorage: {removeItem() {}},
    setTimeout: callback => timers.push(callback), clearTimeout() {},
    console: {warn() {}},
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/js/state.js', 'utf8'), context);
  const api = context.WWState;
  function setAccount(id, credits) {
    const state = api.getState();
    state.account = {woodchuckId: id, authenticated: !!id, serverRevision: 100};
    state.progress.credits = credits;
    api.saveState(state, {sync: false});
  }
  setAccount('WC-A', 20);
  const start = pending(), answer = pending(), complete = pending();
  context.fetch = async url => delayStatus && url.includes("/status/") ? status.promise : ({ok: true, status: 200, json: async () => {
    if (url.endsWith('/complete')) return complete.promise;
    if (url.endsWith('/answer')) return answer.promise;
    if (url === '/arcade/plays') return start.promise;
    if (url.includes('/status/')) return {daily_play_available: true};
    return {best_score: 0};
  }});
  vm.runInContext(fs.readFileSync('static/js/arcade-economy.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('static/js/history-mystery.js', 'utf8'), context);
  const snapshot = {question_index: 0, score: 0, finished: false,
    question: {id: 'q1', category: 'Daily Quiz', prompt: 'Question', choices: ['A', 'B']}};
  return {api, context, setAccount, start, answer, complete, status, snapshot, elements, timers, economyMessage};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

for (const switchTo of ['WC-B', 'logout-and-return']) {
  for (const operation of ['start', 'answer']) {
    test(`History ${operation} response cannot follow ${switchTo}`, async () => {
      const h = harness();
      await settle();
      h.elements.get('history-mystery-start').handlers.click();
      if (operation === 'answer') {
        h.start.deliver({play_token: 'synthetic-play-token', history: h.snapshot,
          balance: 19, state_revision: 101});
        await settle();
        const button = h.elements.get('history-mystery-answers').children[0];
        h.elements.get('history-mystery-answers').handlers.click({target: {closest: () => button}});
      }
      if (switchTo === 'logout-and-return') h.setAccount('', 0);
      h.setAccount(switchTo === 'WC-B' ? 'WC-B' : 'WC-A', 70);
      const before = h.elements.get('history-mystery-score').textContent;
      if (operation === 'start') h.start.deliver({play_token: 'synthetic-play-token',
        history: {...h.snapshot, score: 4}, balance: 19, state_revision: 101});
      else h.answer.deliver({history: {...h.snapshot, score: 5, finished: true},
        answer_result: {correct: true, answer: 'A', fact: 'Fact'}, balance: 24, state_revision: 110});
      await settle();
      for (const callback of h.timers) callback();
      await settle();
      assert.equal(h.api.getState().progress.credits, 70);
      assert.equal(h.elements.get('history-mystery-score').textContent, before);
    });
  }
}

for (const expired of [false, true]) {
  test(expired ? 'expired quiz gives a reload instruction' : 'manual retry keeps the original answer after response loss', async () => {
    const h = harness();
    await settle();
    h.elements.get('history-mystery-start').handlers.click();
    h.start.deliver({play_token: 'synthetic-play-token', history: h.snapshot,
      balance: 19, state_revision: 101});
    await settle();
    const sent = [];
    h.context.WoodshedArcadeEconomy = {...h.context.WoodshedArcadeEconomy,
      answerHistory: async (_token, index, choice) => {
        sent.push({index, choice});
        if (sent.length === 1) throw new Error(expired ? 'That daily quiz has expired.' : 'Connection lost');
        return {history: {...h.snapshot, question_index: 1}, answer_result: {correct: false, answer: 'B', fact: 'Fact'}};
      }};
    const click = index => {
      const button = h.elements.get('history-mystery-answers').children[index];
      h.elements.get('history-mystery-answers').handlers.click({target: {closest: () => button}});
    };
    click(0);
    await settle();
    if (expired) {
      assert.match(h.elements.get('history-mystery-message').textContent, /Reload this page/);
      assert.ok(h.elements.get('history-mystery-answers').children.every(button => button.disabled));
    } else {
      click(1);
      await settle();
      assert.deepEqual(sent, [{index: 0, choice: 'A'}, {index: 0, choice: 'A'}]);
    }
  });
}

for (const switchTo of ['WC-B', 'logout-and-return']) {
  for (const operation of ['startPlay', 'completePlay']) {
    test(`Arcade ${operation} cannot display another account's reward`, async () => {
      const h = harness();
      await settle();
      const response = operation === 'startPlay' ? h.start : h.complete;
      const result = h.context.WoodshedArcadeEconomy[operation]('synthetic-token-or-game', 5);
      if (switchTo === 'logout-and-return') h.setAccount('', 0);
      h.setAccount(switchTo === 'WC-B' ? 'WC-B' : 'WC-A', 70);
      h.economyMessage.textContent = 'Current account message';
      response.deliver({play_token: 'synthetic-play-token', payout: 5, balance: 24, state_revision: 110});
      await result;
      assert.equal(h.economyMessage.textContent, 'Current account message');
      assert.equal(h.api.getState().progress.credits, 70);
    });
  }
}

for (const operation of ['start', 'finish', 'status']) {
  test(`History delayed ${operation} error cannot change the new account page`, async () => {
    const h = harness({delayStatus: operation === 'status'});
    const failure = pending();
    await settle();
    if (operation === 'start') {
      h.context.WoodshedArcadeEconomy = {...h.context.WoodshedArcadeEconomy, startPlay: () => failure.promise};
      h.elements.get('history-mystery-start').handlers.click();
    } else if (operation === 'finish') {
      h.context.WoodshedArcadeEconomy = {...h.context.WoodshedArcadeEconomy, completePlay: () => failure.promise};
      h.elements.get('history-mystery-start').handlers.click();
      h.start.deliver({play_token: 'synthetic-play-token', history: h.snapshot});
      await settle();
      const button = h.elements.get('history-mystery-answers').children[0];
      h.elements.get('history-mystery-answers').handlers.click({target: {closest: () => button}});
      h.answer.deliver({history: {...h.snapshot, finished: true, score: 5},
        answer_result: {correct: true, answer: 'A', fact: 'Fact'}});
      await settle();
      for (const callback of h.timers) callback();
      await settle();
    }
    h.setAccount('WC-B', 70);
    const message = h.elements.get('history-mystery-message');
    message.textContent = 'Current account message';
    h.elements.get('history-mystery-start').textContent = 'Current account button';
    if (operation === 'status') {
      h.status.deliver({ok: false, status: 409, json: async () => ({detail: 'Old account error'})});
    } else failure.reject(new Error('Old account error'));
    await settle();
    assert.equal(message.textContent, 'Current account message');
    assert.equal(h.elements.get('history-mystery-start').textContent, 'Current account button');
  });
}
