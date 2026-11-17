import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// canvas-hub.js imports ui.js (which imports icons.js), so it is loaded from its file.
const hub = await import(new URL("../static/canvas-hub.js", import.meta.url).href);
const source = await readFile(new URL("../static/canvas-hub.js", import.meta.url), "utf8");

const term = (fields) => ({ group: "g 1", term: { id: "t", code: "MATH 140", term_label: "Fall 2026" }, copy: null, push: null,
  sections: [{ id: "s1", number: 507, course: { course_id: "11", name: "507" } }, { id: "s2", number: 508, course: { course_id: "12", name: "508" } }],
  courses: [{ course_id: "11", pushed: false }, { course_id: "12", pushed: false }], next: "start_copy", ...fields });

test("the Canvas page's steps and its one next step come from the overview alone", () => {
  const unlinked = hub.canvasSteps(term({ next: "link_sections", sections: [{ number: 507, course: null }, { number: 508, course: { course_id: "12" } }], courses: [{ course_id: "12", pushed: false }] }));
  assert.deepEqual(unlinked.steps.map((s) => s.state), ["started", "todo", "todo", "todo"]);
  assert.equal(unlinked.next.href, "#course/g%201/section/507");
  const start = hub.canvasSteps(term({}));
  assert.equal(start.next.href, "#course/g%201/home/canvas");
  assert.equal(start.steps[0].detail, "2 of 2 sections linked");
  const first = hub.canvasSteps(term({ copy: { revision: 3 }, next: "push_first" }));
  assert.equal(first.next.href, "#course/g%201/home/canvas?push=1");
  assert.deepEqual(first.steps.map((s) => s.state), ["done", "done", "todo", "todo"]);
  const rest = hub.canvasSteps(term({ copy: { revision: 3 }, next: "push_rest", courses: [{ course_id: "11", pushed: true }, { course_id: "12", pushed: false }] }));
  assert.deepEqual(rest.steps.map((s) => s.state), ["done", "done", "done", "started"]);
  assert.equal(rest.steps[3].detail, "1 of 2 Canvas courses pushed");
  const done = hub.canvasSteps(term({ copy: { revision: 3 }, next: "up_to_date", courses: [{ course_id: "11", pushed: true }, { course_id: "12", pushed: true }] }));
  assert.equal(done.next, null);
});

test("the Canvas page writes no inline styles and only links", () => {
  assert.doesNotMatch(source, /style="/);
  assert.doesNotMatch(source, /"POST"/);
});
