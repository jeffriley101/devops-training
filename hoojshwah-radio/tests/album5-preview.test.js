const test = require("node:test");
const assert = require("node:assert/strict");
const { PreviewState } = require("../app/static/js/album5-preview.js");

const tracks = Array.from({ length: 11 }, (_, index) => ({
  id: `album5-preview-${String(index + 1).padStart(2, "0")}`,
}));

test("queues without interrupting the current normal track", () => {
  const state = new PreviewState(tracks);
  assert.equal(state.queue(), true);
  assert.equal(state.queued, true);
  assert.equal(state.active, false);
  assert.equal(state.current(), null);
});

test("plays all eleven preview tracks in exact order without normal insertions", () => {
  const state = new PreviewState(tracks);
  state.queue();

  const visited = [state.begin().id];
  assert.equal(state.queue(), false);
  assert.equal(state.current().id, tracks[0].id);
  let transition;
  do {
    transition = state.next();
    if (transition.track) visited.push(transition.track.id);
  } while (transition.action === "track");

  assert.deepEqual(visited, tracks.map((track) => track.id));
  assert.equal(transition.action, "return-normal");
  assert.equal(state.active, false);
});

test("early exit clears queued and active preview state", () => {
  const queued = new PreviewState(tracks);
  queued.queue();
  assert.equal(queued.exit(), true);
  assert.equal(queued.queued, false);

  const active = new PreviewState(tracks);
  active.queue();
  active.begin();
  active.next();
  assert.equal(active.exit(), true);
  assert.equal(active.active, false);
  assert.equal(active.index, -1);
});
