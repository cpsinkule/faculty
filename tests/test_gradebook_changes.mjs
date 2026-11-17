import assert from "node:assert/strict";
import test from "node:test";

import { entrySummary, foundText, originLabel, valueWords } from "../static/gradebook-changes.js";
import { actorText } from "../static/gradebook.js";

// The Changes page's words (gradebook/README.md "Changes"): what made an entry and the one line under it. The entries are the
// server's; names and titles come from the View on screen.
const titles = { i1: "Test 1", i2: "Quiz 2", i3: "Quiz 3" };
const names = { "s1:e1": "Example, Alice" };
const titleOf = (id) => titles[id] || "";
const nameOf = (row) => names[row] || "";
const request = (origin, extra = {}) => ({ id: "r", type: "request", kind: "scores", origin, summary: {}, items: [],
  cells: { here: 0, added: 0, changed: 0, cleared: 0, elsewhere: 0 }, detail: null, setup: null, ...extra });

test("a value reads as points, Excused, Missing or a dash", () => {
  assert.deepEqual([valueWords({ status: "scored", points: "8.50" }), valueWords({ status: "excused" }), valueWords({ status: "missing" }),
    valueWords({ status: "cleared" }), valueWords(null)], ["8.5", "Excused", "Missing", "—", "—"]);
});

test("each entry names what made it, an import its file, Canvas or iClicker", () => {
  const labels = [
    request("typed"), request("absence", { summary: { source: "absence", action: "fill" } }),
    request("absence", { summary: { source: "absence", action: "put_back" } }), request("file", { summary: { filename: "grades.csv" } }), request("file"),
    request("canvas", { summary: { source: "canvas" } }), request("iclicker", { summary: { source: "iclicker" } }),
    request("pull", { summary: { item_id: "i1", parts: ["frq", "bubble"] } }), request("pull", { summary: { item_id: "i1", certification_id: "c" } }),
    request("certification", { summary: { item_id: "i1", certification_id: "c" } }), { type: "setup", changes: [] },
  ].map((entry) => originLabel(entry, titleOf));
  assert.deepEqual(labels, ["Typed in Faculty", "Full iClicker credit for an excused absence", "iClicker credit an absence gave, taken back",
    "Imported grades.csv", "Imported a file", "Imported from Canvas",
    "Imported from iClicker", "Pulled Test 1 from grading (written work, bubble sheets)", "Pulled Test 1 from its certification",
    "Recorded Test 1 from its certification", "Setup changed"]);
});

test("the line under it names a few cells, else the columns and counts", () => {
  const cell = { item_id: "i1", row: "s1:e1", before: { status: "scored", points: "80" }, after: { status: "scored", points: "85" } };
  assert.equal(entrySummary(request("typed", { cells: { here: 1, changed: 1 }, detail: [cell] }), { nameOf, titleOf }), "Example, Alice · Test 1: 80 → 85");
  assert.equal(entrySummary(request("typed", { cells: { here: 1, added: 1 }, detail: [{ ...cell, row: "gone", before: null }] }), { nameOf, titleOf }),
    "A student · Test 1: — → 85");
  assert.equal(entrySummary(request("canvas", { cells: { here: 40, added: 30, changed: 8, cleared: 2 }, items: ["i1", "i2", "i3"] }), { nameOf, titleOf }),
    "Test 1, Quiz 2 and 1 more: 30 added, 8 changed, 2 cleared");
  assert.equal(entrySummary(request("file", { setup: { revision: 3, changes: ["Added column “Quiz 2”", "Added column “Quiz 3”"] } }), { nameOf, titleOf }),
    "Added column “Quiz 2” and 1 more change");
  assert.equal(entrySummary(request("typed", { cells: { here: 0, elsewhere: 3 } }), { nameOf, titleOf }), "No scores in this section (3 scores in other sections)");
  assert.equal(entrySummary(request("pull"), { nameOf, titleOf }), "No scores written");
  assert.equal(entrySummary({ type: "setup", changes: ["Removed column “Lab”"] }), "Removed column “Lab”");
});

test("what a pull or an import found besides what it wrote", () => {
  assert.equal(foundText(request("pull", { summary: { counts: { add: 3, same: 2, kept: 1, waiting: 4, none: 0 } } })),
    "2 already held that value, 1 kept (typed or cleared in Faculty), 4 waiting for grading");
  assert.equal(foundText(request("typed", { summary: { cells: 1, counts: { scored: 1 } } })), "");
  assert.equal(foundText({ type: "setup" }), "");
});

test("the assistant's own entries say so (request_actor.py); the instructor's keep their words", () => {
  assert.deepEqual([actorText("assistant"), actorText("workspace"), actorText(null)], ["the assistant", "workspace", ""]);
  // A late paper Faculty certified by itself (GRADING "LATE PAPERS CERTIFY THEMSELVES") is Faculty's own, said in words.
  assert.equal(actorText("automatic"), "Faculty, by itself");
  assert.equal(originLabel(request("certification", { actor: "automatic", summary: { item_id: "i1" } }), titleOf), "Recorded Test 1 from a certification Faculty made by itself");
  assert.equal(originLabel(request("certification", { actor: "workspace", summary: { item_id: "i1" } }), titleOf), "Recorded Test 1 from its certification");
  assert.equal(originLabel(request("typed", { actor: "assistant" })), "Entered by the assistant");
  assert.equal(originLabel({ type: "setup", actor: "assistant", changes: [] }), "Setup changed · by the assistant");
  assert.equal(originLabel(request("pull", { actor: "assistant", summary: { item_id: "i1" } }), titleOf), "Pulled Test 1 from grading · by the assistant");
  assert.equal(originLabel(request("typed", { actor: "workspace" })), "Typed in Faculty");
});

test("the nightly WebAssign import's requests say so (gradebook/webassign_nightly.py)", () => {
  assert.equal(originLabel({ type: "request", origin: "canvas", actor: "workspace", summary: { source: "canvas", automatic: "webassign" } }),
    "Copied from Canvas by the nightly WebAssign import");
  assert.equal(originLabel({ type: "request", origin: "canvas", actor: "workspace", summary: { source: "canvas", skipped: ["Graphing Practice"] } }),
    "Left out of Sync with Canvas: “Graphing Practice”");
  assert.equal(originLabel({ type: "request", origin: "canvas", actor: "workspace", summary: { source: "canvas" } }), "Imported from Canvas");
  // SYNC WITH CANVAS: a sync names what it sent and what it left held.
  assert.equal(originLabel({ type: "request", origin: "canvas", actor: "workspace", summary: { source: "canvas", sync: { sending: 12, held: 1 } } }),
    "Synced with Canvas · 12 grades sent · 1 grade held");
  assert.equal(originLabel({ type: "request", origin: "canvas", actor: "workspace", summary: { source: "canvas", automatic: "canvas-sync", sync: { sending: 2, held: 0 } } }),
    "Synced with Canvas by the morning sync · 2 grades sent");
  assert.equal(originLabel({ type: "request", origin: "iclicker", actor: "workspace", summary: { source: "iclicker", automatic: "iclicker" } }),
    "Imported from iClicker after class");
  // Its due dates that followed Canvas (webassign_nightly.FOLLOWED): one column, several.
  assert.equal(originLabel({ type: "setup", actor: "workspace", automatic: "webassign", changes: ["“HW 1”: due Sep 4 → Sep 8"] }),
    "Due date updated from Canvas overnight");
  assert.equal(originLabel({ type: "setup", actor: "workspace", automatic: "webassign", changes: ["a", "b"] }),
    "Due dates updated from Canvas overnight");
});
