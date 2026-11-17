import assert from "node:assert/strict";
import test from "node:test";

import { GRADING_FILTER, gradingAttention, planHref, scanChecksHref } from "../static/home.js";
import { assignmentDecisionCards, assignmentDecisions, navEntry } from "../static/page-header.js";

// ONE COUNT OF WHAT NEEDS YOU (2026-10-18): the Assignments badge counts decisions, one per needs-you item, as the assignment tabs
// and the index count them; never the responses or questions inside them.
test("the Assignments badge counts one decision per assignment card, not its responses", () => {
  const cards = [
    { key: "grading:p1:review", href: "#grading/p1?tab=review", count: 240 },
    { key: "grading:p1:areas", href: "#grading/p1?tab=locations", count: 3 },
    { key: "exam_requests", href: "#exams/e1?tab=requests", count: 2 },
    { key: "queue:printing:failed", href: "#documents?tab=batches&status=attention", count: 1 },
    { key: "inbox", href: "#inbox", count: 9, derived: true },
    { key: "support:wellbeing", href: "#grading/p1", count: 1, counted_in: "x" },
    { key: "grading:p2:review", href: "#grading/p2?tab=review", count: 0 },
  ];
  assert.equal(assignmentDecisions(cards), 4);
  assert.equal(assignmentDecisions(null), 0);
  // The index lists exactly the cards the badge counts.
  assert.equal(assignmentDecisionCards(cards).length, 4);
  assert.deepEqual(assignmentDecisionCards(null), []);
  // A makeup version waiting to be approved opens the assignment's own address and is counted all the same (2026-11-05); another card there is not.
  const makeup = { key: "makeup:p1", href: "#assignments/a1/t1?area=create&makeup=1", count: 1, assignment_work: true };
  assert.deepEqual(assignmentDecisionCards([makeup, { key: "shared_stamps", href: "#assignments/a1/t1", count: 2 }]), [makeup]);
});

test("the rail highlights Assignments for every assignment page, Teaching review and a scan inside the shell (I4)", () => {
  for (const view of ["assignments", "documents", "exams", "exam-day", "grading", "grade-delivery", "teaching-review"]) {
    assert.equal(navEntry({ view, path: view }), "assignments", view);
  }
  assert.equal(navEntry({ view: "inbox", path: "inbox/job/j1/pages", inAssignment: true }), "assignments");
  assert.equal(navEntry({ view: "inbox", path: "inbox/job/j1/pages" }), "inbox", "a scan opened from the Inbox stays the Inbox's");
  assert.equal(navEntry({ view: "inbox", path: "inbox", inAssignment: true }), "inbox", "the Inbox list is always the Inbox's");
  assert.equal(navEntry({ view: "course", courseSub: "canvas", path: "course/x/home/canvas" }), "canvas");
  assert.equal(navEntry({ view: "course", path: "course/x/home" }), "current-courses");
  assert.equal(navEntry({ view: "office-hours", path: "office-hours" }), "follow-ups");
  assert.equal(navEntry({ view: "rooms", path: "rooms" }), "rooms");
});

test("Home opens an assignment in its shell when it knows the assignment, else the plan's own address", () => {
  assert.equal(planHref({ id: "p1", source_assignment_id: "a 1", term_id: "t1" }), "#assignments/a%201/t1");
  assert.equal(planHref({ id: "p1", source_assignment_id: "a1", term_id: "t1" }, "area=collect"), "#assignments/a1/t1?area=collect");
  assert.equal(planHref({ id: "p1" }), "#grading/p1");
  assert.equal(GRADING_FILTER, "#assignments?filter=grading", "the retired Grading index's place (I7)");
});

test("Finish the scan checks opens the assignment's waiting scans when they are all one assignment's (I2)", () => {
  const one = { waiting: [{ id: "p1", assignment_id: "a1", term_id: "t1" }], waiting_href: "#inbox/job/j1" };
  assert.equal(scanChecksHref(one), "#assignments/a1/t1?area=collect&scans=waiting");
  const two = { waiting: [{ id: "p1", assignment_id: "a1", term_id: "t1" }, { id: "p2", source_assignment_id: "a2", term_id: "t1" }], waiting_href: "#inbox" };
  assert.equal(scanChecksHref(two), "#inbox");
  assert.equal(scanChecksHref({ waiting: [{ id: "p1" }], waiting_href: "#inbox/job/j1" }), "#inbox/job/j1", "an older server keeps its link");
  assert.equal(scanChecksHref({}), "#inbox");
});

test("students with no scanned paper are no row on Home; Certify confirms their 0s (ASSIGNMENT-WORKSPACE THE ZEROS ARE SAID AT CERTIFY)", () => {
  const plan = (collection, certification = { state: "none" }) => ({ id: "p", title: "Quiz 5", source_assignment_id: "a", term_id: "t",
    workflow: { collection, certification, setup: { questions: [], templates: [], exam_versions: { letters: [], graded: [], blanks: {} },
      rubric_quality: { state: "confirmed", readiness: {} }, template_setup: { confirmed: true, slots: [] }, preparation: {} } } });
  // Only a missing-papers row is under test; a setup row of this bare plan is another rule's.
  const rows = (collection, certification) => gradingAttention({ assignments: [plan(collection, certification)], jobs: [] })
    .filter((row) => row.key.endsWith(":missing"));
  assert.deepEqual(rows({ no_scan: 4, partial: 2 }), []);
  assert.deepEqual(rows({ no_scan: 4, complete: 3 }), []);
  assert.deepEqual(rows({ no_scan: 26, complete: 3 }, { state: "changed" }), []);
});
