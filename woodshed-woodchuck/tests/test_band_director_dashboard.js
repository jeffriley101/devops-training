const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("static/js/band-director-dashboard.js", "utf8");

function harness() {
  const rows = [
    ["Zulu", 100, -12, 100, 2, 10, 1200, 100, 10, 2, 100, "Beta"],
    ["alpha", 9, 0, 9, 100, 2, 99, 2, 100, 10, 2, "Zulu"],
    ["Beta", 50, 8, 50, 10, 100, 500, 10, 2, 100, 10, "alpha"],
  ].map((values) => ({ cells: values.map((value) => ({ dataset: { sortValue: String(value) } })) }));
  const body = { rows, append(...sorted) { this.rows = sorted; } };
  const headers = Array.from({ length: 12 }, (_, i) => {
    const button = { dataset: { sort: i === 0 || i === 11 ? "text" : "number" },
      textContent: String(i), addEventListener(_, fn) { this.click = fn; } };
    return { button, state: "none", querySelector: () => button,
      getAttribute() { return this.state; }, setAttribute(_, value) { this.state = value; } };
  });
  const table = { tBodies: [body], querySelectorAll: () => headers };
  const feedback = {};
  vm.runInNewContext(source, { document: {
    querySelector: (selector) => selector === "[data-director-table]" ? table : feedback,
  } });
  return { headers, body, feedback };
}

for (let index = 0; index < 12; index++) {
  test(`column ${index}: ascending then descending with numeric delta for Trend`, () => {
    const h = harness();
    const values = () => Array.from(h.body.rows, (row) => row.cells[index].dataset.sortValue);
    const expected = values().sort(index === 0 || index === 11
      ? (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" })
      : (a, b) => Number(a) - Number(b));
    h.headers[index].button.click();
    assert.deepEqual(values(), expected);
    assert.equal(h.headers[index].state, "ascending");
    h.headers[index].button.click();
    assert.deepEqual(values(), expected.reverse());
    assert.equal(h.headers[index].state, "descending");
    assert.match(h.feedback.textContent, /descending/);
    h.headers[(index + 1) % 12].button.click();
    assert.equal(h.headers[index].state, "none");
  });
}
test("empty roster needs no table", () => {
  assert.doesNotThrow(() => vm.runInNewContext(source, { document: { querySelector: () => null } }));
});
