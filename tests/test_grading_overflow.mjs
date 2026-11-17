// The finalization dialog mirrors the server's rule for display: a question's still-combinable lines may not add up to
// more than it is worth. Both sides are held to one table of cases; the server decides.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";

// The dialog module imports the page's math renderer, which needs a browser; only the pure rule is loaded here.
const source = readFileSync(new URL("../static/grading-rubric-quality.js", import.meta.url), "utf8").replace(/^import .*$/gm, "");
const {heaviestCombination, combinationReader} = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const cases = JSON.parse(readFileSync(new URL("./fixtures/grading/overflow_cases.json", import.meta.url)));

test("the most points that can still be combined matches the shared cases", () => {
  for (const c of cases) assert.deepEqual(heaviestCombination(c.rubric, c.entry, c.maximum), c.expected, c.name);
});

test("marking the alternatives is what resolves a question", () => {
  const c = cases[0], pairs = [];
  assert.equal(heaviestCombination(c.rubric, {...c.entry, pairs}, c.maximum).points, 7);
  pairs.push(["R8", "R10"]);  // Point-slope and slope-intercept form are two spellings of one answer.
  assert.equal(heaviestCombination(c.rubric, {...c.entry, pairs}, c.maximum), null);
});

test("a dialog reuses a combination result until its exclusions change", () => {
  const c = cases[0], selected = new Map([['q', new Set()]]);
  const q = {question_id: 'q', rubric: c.rubric, builtin: c.entry.builtin, max_points: c.maximum};
  const read = combinationReader(selected), first = read(q);
  assert.equal(first.points, 7);
  assert.strictEqual(read(q), first);
  selected.get('q').add('R10\u001fR8');
  assert.equal(read(q), null);
  selected.get('q').clear();
  assert.deepEqual(read(q), first);
  assert.notStrictEqual(read(q), first);
});
