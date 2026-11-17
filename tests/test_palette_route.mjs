import assert from "node:assert/strict";
import test from "node:test";

// JEV ROUTING (docs/decisions/jev.md): what the palette sends and the rows an answer adds.
const { routeRequest, routeRows } = await import(new URL("../static/palette.js", import.meta.url).href);

const places = [
  { group: "Pages", label: "Teaching Inbox", href: "#inbox" },
  { group: "Grading", label: "Quiz 3", detail: "MATH 140 · Fall 2026 · Grading", href: "#grading/p3" },
  { group: "Grading", label: "Quiz 4", detail: "MATH 140 · Fall 2026 · Grading", href: "#grading/p4" },
  { group: "Students", label: "Example, Alice", detail: "MATH 140", run: () => {} },
  { group: "Scans", label: "Alice's makeup", detail: "Teaching Inbox", href: "#inbox/job/1" },
  { group: "Recent", label: "Quiz 3", href: "#grading/p3" },
  { group: "Actions", label: "Ask the assistant", run: () => {} },
];

test("students, scans and recent places are never sent", () => {
  const { groups, byId } = routeRequest(places);
  assert.deepEqual(Object.keys(groups).sort(), ["Actions", "Grading", "Pages"]);
  const sent = JSON.stringify(groups);
  assert.ok(!sent.includes("Alice"));
  assert.equal(byId.get(groups.Grading[0].id).href, "#grading/p3");
});

test("a confident guess leads, with its grading step in the link", () => {
  const { groups, byId } = routeRequest(places);
  const answer = { status: "ok", confident: true, kind: "Grading", picks: [{ id: groups.Grading[0].id, confidence: 0.93, tab: "review" }, { id: groups.Grading[1].id }] };
  const { lead, rows } = routeRows(answer, byId, "show quiz 3 flags", { run: () => {} });
  assert.equal(lead.length, 1);
  assert.equal(lead[0].group, "Best match");
  assert.equal(lead[0].href, "#grading/p3?tab=review");
  assert.equal(lead[0].label, "Quiz 3 · Review");
  assert.deepEqual(rows.map((row) => row.group), ["Did you mean", "Ask instead"]);
});

test("a weak guess follows the word matches; a question goes to the assistant unsent", () => {
  const { groups, byId } = routeRequest(places);
  const weak = routeRows({ status: "ok", confident: false, kind: "Grading", picks: [{ id: groups.Grading[1].id, tab: "overview" }] }, byId, "quiz four", { run: () => {} });
  assert.equal(weak.lead.length, 0);
  assert.equal(weak.rows[0].href, "#grading/p4");
  const question = routeRows({ status: "not_navigation" }, byId, "why did bob lose points", { run: () => {} });
  assert.deepEqual(question.rows.map((row) => row.label), ["Ask the assistant: “why did bob lose points”"]);
});

test("Jev switched off, failed or unreachable adds nothing", () => {
  const { byId } = routeRequest(places);
  for (const answer of [null, { status: "unavailable" }, { status: "failed" }, { status: "uncertain" }]) {
    assert.deepEqual(routeRows(answer, byId, "show quiz 3", { run: () => {} }), { lead: [], rows: [] });
  }
});
