import assert from "node:assert/strict";
import test from "node:test";

// ACCOMMODATIONS "DR QUIZ TAKERS" (requirement, 2026-10-15): the one checkbox both the Ctrl-K student card and the Accommodations page draw.
const { takerLine, takerMarkup, saveTaker } = await import(new URL("../static/dr-quiz-taker.js", import.meta.url).href);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const unmarked = { on: false, revision: 0, set_at: null, set_by: null };
const state = (changes = {}) => ({ on: true, revision: 1, set_at: "2026-10-15T15:00:00+00:00", set_by: "workspace", ...changes });

test("the hint names who marked or unmarked the student and the campus day", () => {
  assert.equal(takerLine(unmarked), "");
  assert.equal(takerLine({ ...unmarked, on: true }), "", "no change recorded: no hint");
  assert.equal(takerLine(state()), "Marked by you on Oct 15");
  assert.equal(takerLine(state({ set_by: "assistant" })), "Marked by the Assistant on Oct 15");
  assert.equal(takerLine(state({ on: false, revision: 2 })), "Unmarked by you on Oct 15");
  assert.equal(takerLine(state({ on: false, revision: 2, set_by: "assistant" })), "Unmarked by the Assistant on Oct 15");
  // 10 pm in College Station is already the next day in UTC: the day shown is the campus one.
  assert.equal(takerLine(state({ set_at: "2026-10-16T03:00:00+00:00" })), "Marked by you on Oct 15");
  assert.equal(takerLine(state({ set_at: "2026-11-02T06:30:00+00:00" })), "Marked by you on Nov 2");
});

test("the checkbox is the ordinary checkbox field, ticked only when on, with the id escaped", () => {
  const off = takerMarkup("1000000001", unmarked, escape);
  assert.ok(off.startsWith('<label class="checkbox-field">'), off);
  assert.match(off, /<input type="checkbox" data-dr-quiz-taker="1000000001"\s*>DR quiz taker<\/label>/);
  assert.doesNotMatch(off, /checked/);
  assert.equal(off.trim().endsWith("</label>"), true, "no hint when nothing was ever recorded");

  const on = takerMarkup("1000000001", state(), escape);
  assert.match(on, /<input type="checkbox" data-dr-quiz-taker="1000000001"\s+checked\s*>DR quiz taker<\/label>/);
  assert.ok(on.includes("Marked by you on Oct 15"), on);
  assert.ok(on.indexOf("Marked by you") > on.indexOf("</label>"), "the hint follows the label");

  const cleared = takerMarkup("1000000001", state({ on: false, revision: 2 }), escape);
  assert.doesNotMatch(cleared, /checked/);
  assert.ok(cleared.includes("Unmarked by you on Oct 15"), cleared);

  const odd = takerMarkup('"><script>', unmarked, escape);
  assert.ok(odd.includes('data-dr-quiz-taker="&quot;&gt;&lt;script&gt;"'), odd);
  assert.doesNotMatch(odd, /<script>/);
});

test("saving posts the value with a fresh request ID and answers what the server said", async () => {
  const calls = [];
  const api = async (url, method, body) => {
    calls.push({ url, method, body });
    return { enrollment_id: "1000000001", term_id: "t1", dr_quiz_taker: state({ on: body.on }) };
  };
  const answer = await saveTaker(api, "1000000001", true);
  assert.deepEqual(answer, { enrollment_id: "1000000001", term_id: "t1", dr_quiz_taker: state() });
  await saveTaker(api, "1000000001", false);
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(call.url, "/api/accommodations/dr-quiz-takers/1000000001");
    assert.equal(call.method, "POST");
    assert.deepEqual(Object.keys(call.body).sort(), ["on", "request_id"]);
    assert.match(call.body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  }
  assert.deepEqual(calls.map((c) => c.body.on), [true, false], "it sets the value; it does not toggle");
  assert.notEqual(calls[0].body.request_id, calls[1].body.request_id);
});

test("a student without accommodations gets no box unless already marked, which keeps it with a hint", () => {
  assert.equal(takerMarkup("1000000001", unmarked, escape, false), "");
  assert.equal(takerMarkup("1000000001", undefined, escape, false), "");
  const kept = takerMarkup("1000000001", state(), escape, false);
  assert.match(kept, /checked\s*>DR quiz taker<\/label>/);
  assert.ok(kept.includes("No accommodations on record."), kept);
  assert.doesNotMatch(takerMarkup("1000000001", state(), escape, true), /No accommodations/);
});
