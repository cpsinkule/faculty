// The settings screen mirrors the server's reasoning rules for display; both are held to one table of cases.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import {reasoningChoices, reasoningDefault, reasoningMarkup, REASONING_LABELS, EFFORTS} from "../static/grading-settings-layout.js";

const cases = JSON.parse(readFileSync(new URL("./fixtures/grading/reasoning_cases.json", import.meta.url)));
const h = {e: value => String(value), select: (label, name, choices, current) => `<select name="${name}" aria-label="${label}">${choices.map(([value, title]) => `<option value="${value}"${value === current ? " selected" : ""}>${title}</option>`).join("")}</select>`};

test("choices and defaults match the shared cases", () => {
  for (const c of cases) {
    const endpoint = {reasoning: c.reasoning, reasoning_options: c.options};
    assert.deepEqual(reasoningChoices(endpoint), c.choices, c.name);
    assert.equal(reasoningDefault(endpoint), c.default, c.name);
  }
  assert.deepEqual(Object.keys(REASONING_LABELS).sort(), ["off", "on", ...EFFORTS].sort());
});

test("one possible configuration is stated, several are a choice, and a saved choice the model no longer offers falls back", () => {
  const of = name => cases.find(c => c.name === name), endpoint = c => ({reasoning: c.reasoning, reasoning_options: c.options});
  assert.match(reasoningMarkup(endpoint(of("never thinks")), "tier-a-reader", undefined, h), /Never thinks/);
  assert.match(reasoningMarkup(endpoint(of("must think, no effort levels")), "tier-a-reader", undefined, h), /Always thinks/);
  assert.match(reasoningMarkup(endpoint(of("nothing known")), "tier-a-reader", undefined, h), /follows this connection/);
  assert.equal(reasoningMarkup(undefined, "tier-a-reader", undefined, h), "");
  const must = reasoningMarkup(endpoint(of("must think, unmarked thinks least")), "tier-a-verifier", undefined, h);
  assert.match(must, /name="reasoning-tier-a-verifier"/);
  assert.match(must, /<option value="low" selected>Low effort \(least\)<\/option><option value="high">High effort<\/option><option value="max">Maximum effort<\/option>/);
  assert.doesNotMatch(must, /No reasoning/);
  assert.match(reasoningMarkup(endpoint(of("must think, unmarked thinks least")), "tier-a-verifier", "high", h), /value="high" selected/);
  assert.match(reasoningMarkup(endpoint(of("must think, unmarked thinks least")), "tier-a-verifier", "off", h), /value="low" selected/);
});
