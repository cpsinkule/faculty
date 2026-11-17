// The assignment window's pure helpers (UX overhaul W2-DOCS, 2026-09-26).
import { test } from "node:test";
import assert from "node:assert/strict";
import { finishLaterWords, keyFromTitle, setUpGradingNotice, prepareAssignmentGrading } from "../static/assignment-wizard.js";

test("the assignment key follows the title as a slug", () => {
  assert.equal(keyFromTitle("Test 1"), "test-1");
  assert.equal(keyFromTitle("  Unit Test 5: Limits & Continuity  "), "unit-test-5-limits-continuity");
  assert.equal(keyFromTitle("Examen Final — Révision"), "examen-final-revision");
  assert.equal(keyFromTitle(""), "");
  assert.equal(keyFromTitle("!!!"), "");
  assert.equal(keyFromTitle("x".repeat(300)).length, 160);
});

// Set up grading now (requirement, 2026-10-02): one notice for the window, the Exams hub's Grading tile and the card's ⋯ menu. A plan
// with no blank template says how to get one (the assignment finder matches a scan without labels only against a blank).
test("Set up grading now says what is left once the plan exists", () => {
  const made = (templates, extra = {}) => ({ set_up: { created: true, templates }, ...extra });
  assert.deepEqual(setUpGradingNotice("Exam 2", made([{ label: "A", error: "x" }, { label: "Version B", error: "y" }])),
    ["Grading is set up for Exam 2. Versions A, B could not be prepared as blank templates: add them in Rubrics & Templates.", { tone: "warning" }]);
  assert.match(setUpGradingNotice("Exam 2", made([{ label: "Version A", error: "x" }]))[0], /Version A could not be prepared as a blank template: add it in/);
  assert.deepEqual(setUpGradingNotice("Exam 2", { set_up: { created: false, templates: [] }, templates: [] }), ["Grading was already set up for Exam 2.", { tone: "success" }]);
  assert.deepEqual(setUpGradingNotice("Exam 2", made([], { templates: [], document_versions: [] })),
    ["Grading is set up for Exam 2: add its rubrics now. It has no blank template yet: register its PDF in Assignments, then prepare its blank template in Rubrics & Templates.", { tone: "info" }]);
  assert.match(setUpGradingNotice("Exam 2", made([], { templates: [], document_versions: [{ id: "v" }] }))[0], /It has no blank template yet: add one in Rubrics & Templates\.$/);
  assert.deepEqual(setUpGradingNotice("Exam 2", made([{ label: "A", template_id: "t" }], { templates: [{ id: "t" }], document_versions: [{ id: "v" }] })),
    ["Grading is set up for Exam 2: add its rubrics now. Its papers join once their scans clear the Teaching Inbox.", { tone: "success" }]);
});

test("ASSIGNMENT-WORKSPACE: handoff retains a created plan and does not immediately retry its failed import", async () => {
  const calls = [];
  const plan = { id: "term-plan", set_up: { created: true, templates: [{ label: "A", error: "Conversion unavailable" }] } };
  const result = await prepareAssignmentGrading({ assignmentId: "paper", termId: "selected-term", sectionIds: ["section"], templates: true,
    api: async (...args) => { calls.push(args); return plan; } });
  assert.equal(result.plan, plan);
  assert.deepEqual(result.problems, ["A: Conversion unavailable"]);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].slice(0, 3), ["/api/grading/assignments", "POST", { assignment_id: "paper", term_id: "selected-term", section_ids: ["section"] }]);
});

test("ASSIGNMENT-WORKSPACE: missing imports continue independently and resume without duplicating completed work", async () => {
  const plan = { id: "term-plan" };
  const slots = [
    { document_id: "ready", template_id: "ready-template", can_import: true },
    { document_id: "failed", label: "A", can_import: true },
    { document_id: "failed", label: "A second slot", can_import: true },
    { document_id: "later", label: "B", can_import: true },
    { document_id: "stamps-only", can_import: false },
  ];
  const calls = [];
  let fail = true;
  const api = async (url, method, data) => {
    if (!method) return { slots };
    assert.equal(method, "POST");
    assert.equal(url, "/api/grading/assignments/term-plan/templates/prepare");
    calls.push(data.document_id);
    if (data.document_id === "failed" && fail) throw new Error("Conversion unavailable");
    slots.filter(slot => slot.document_id === data.document_id).forEach(slot => { slot.template_id = "new-template"; });
    return { id: "new-template" };
  };
  const first = await prepareAssignmentGrading({ api, plan, templates: true });
  assert.equal(first.plan, plan);
  assert.deepEqual(first.problems, ["A: Conversion unavailable"]);
  assert.deepEqual(calls, ["failed", "later"]);
  fail = false;
  const retry = await prepareAssignmentGrading({ api, plan, templates: true });
  assert.deepEqual(retry.problems, []);
  assert.deepEqual(calls, ["failed", "later", "failed"]);
});

test("ASSIGNMENT-WORKSPACE: setup and template-list failures are resumable and preserve existing plan", async () => {
  const api = async () => { throw new Error("Offline"); };
  assert.deepEqual(await prepareAssignmentGrading({ api }), { plan: null, problems: ["Offline"] });
  const plan = { id: "saved" };
  assert.deepEqual(await prepareAssignmentGrading({ api, plan, templates: true }), { plan, problems: ["Offline"] });
});

test("ASSIGNMENT-WORKSPACE: a reused plan imports the later paper through its existing template route", async () => {
  const calls = [];
  const api = async (url, method, data) => {
    calls.push([url, method, data]);
    if (url === "/api/grading/assignments") return { id: "existing", set_up: { created: false, templates: [] } };
    if (!method) return { slots: [{ document_id: "later", can_import: true }] };
    return { id: "template" };
  };
  const result = await prepareAssignmentGrading({ api, assignmentId: "paper", termId: "term", sectionIds: [], templates: true });
  assert.deepEqual(result.problems, []);
  assert.deepEqual(calls.map(call => call[0]), ["/api/grading/assignments", "/api/grading/assignments/existing/templates", "/api/grading/assignments/existing/templates/prepare"]);
});

// ASK FIRST (requirement, 2026-10-11): a version typed on Paper and not added is not saved with the assignment.
test("Finish later asks before leaving a typed version behind, then says what was not added", () => {
  assert.deepEqual(finishLaterWords("Quiz 4", []), { question: "", notice: "Quiz 4 is saved. Continue it from the Assignments list." });
  assert.deepEqual(finishLaterWords("Quiz 4", ["B"]), {
    question: "Finish later without adding Version B? What you typed for it is not saved. Quiz 4 itself is saved.",
    notice: "Quiz 4 is saved. Version B was not added. Continue it from the Assignments list." });
  assert.deepEqual(finishLaterWords("Quiz 4", ["A", "Version B"]), {
    question: "Finish later without adding Versions A, B? What you typed for them is not saved. Quiz 4 itself is saved.",
    notice: "Quiz 4 is saved. Versions A, B were not added. Continue it from the Assignments list." });
  // Files chosen and no label yet: the row is still named.
  assert.match(finishLaterWords("Quiz 4", [""]).question, /^Finish later without adding the new version\? What you typed for it /);
  assert.match(finishLaterWords("Quiz 4", ["A", ""]).notice, /^Quiz 4 is saved\. The new versions were not added\. /);
});
