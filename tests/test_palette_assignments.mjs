import assert from "node:assert/strict";
import test from "node:test";

// ONE ENTRY PER ASSIGNMENT (2026-10-18, review P3): the palette listed an assignment up to three times (Assignments, Exams, Grading).
const { entityItems, routeRequest, routeRows } = await import(new URL("../static/palette.js", import.meta.url).href);

const sources = {
  catalog: { assignments: [{ id: "a1", title: "Test 1", code: "MATH 140", definition_id: "d" }, { id: "a2", title: "Quiz 4", code: "MATH 140", definition_id: "d" },
    { id: "a3", title: "Old quiz", code: "MATH 140", definition_id: "d" }, { id: "a4", title: "Spring final", code: "MATH 140", definition_id: "d" }], sections: [],
    lifecycle: [{ assignment_id: "a1", term_id: "t1", term_label: "Fall 2026", exam_event_id: "e1", active: true },
      { assignment_id: "a2", term_id: "t 1", term_label: "Fall 2026", exam_event_id: null, active: false }] },
  grading: { assignments: [{ id: "p1", title: "Test 1", code: "MATH 140", term: "Fall", year: 2026, source_assignment_id: "a1", term_id: "t1" },
    { id: "p4", title: "Spring final", code: "MATH 140", term: "Spring", year: 2026, source_assignment_id: "a4", term_id: "t0" }] },
};

test("each assignment is one entry, opening its assignment shell", () => {
  const items = entityItems(sources).filter((item) => ["Assignments", "Exams", "Grading"].includes(item.group));
  assert.deepEqual(items.map((item) => [item.group, item.label, item.href]), [
    ["Assignments", "Test 1", "#assignments/a1/t1"],
    ["Assignments", "Quiz 4", "#assignments/a2/t%201"],
    ["Assignments", "Spring final", "#assignments/a4/t0"],
    ["Assignments", "Old quiz", "#documents?assignment=a3&step=details"],
  ]);
  assert.equal(items[0].detail, "MATH 140 · Fall 2026");
  assert.equal(items[0].plan, "p1");
  assert.equal(items[1].plan, undefined);
  assert.equal(items[2].detail, "MATH 140 · Spring 2026", "another term's grading plan by its assignment and term");
  assert.equal(items[3].detail, "MATH 140 · Edit in Assignments", "no term: it is edited on Assignments");
  assert.match(items[0].keywords, /grading/, "\"test 1 grading\" still finds it");
});

test("an assignment with grading is routed as a grading place, and its step opens on its tab inside the shell", () => {
  const places = entityItems(sources);
  const { groups, byId } = routeRequest(places);
  assert.deepEqual(groups.Grading.map((item) => item.label), ["Test 1", "Spring final"]);
  assert.deepEqual(groups.Assignments.map((item) => item.label), ["Quiz 4", "Old quiz"]);
  assert.equal(groups.Exams, undefined);
  const id = groups.Grading[0].id;
  const step = routeRows({ status: "ok", confident: true, kind: "Grading", picks: [{ id, confidence: 0.9, tab: "export" }] }, byId, "certify test 1", false);
  assert.deepEqual([step.lead[0].label, step.lead[0].href, step.lead[0].group], ["Test 1 · Certify", "#grading/p1?tab=export", "Assignments".replace("Assignments", "Best match")]);
  const overview = routeRows({ status: "ok", confident: true, kind: "Grading", picks: [{ id, confidence: 0.9, tab: "overview" }] }, byId, "open test 1", false);
  assert.equal(overview.lead[0].href, "#assignments/a1/t1", "no step: the assignment's overview");
});
