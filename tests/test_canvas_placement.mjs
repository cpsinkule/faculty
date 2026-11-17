import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const placement = await import(new URL("../static/canvas-placement.js", import.meta.url).href);
const push = await import(new URL("../static/canvas-push.js", import.meta.url).href);

test("a placement posts the form's values with the proposal's revision and the link", () => {
  const body = placement.placementBody({ revision: 4 }, { name: " Quiz 3 ", points: " 10 ", group: "Quizzes", due_date: "2026-10-02", due_time: "",
    submission: "on_paper", published: true, module: "__new", new_module: " Quizzes " }, { assignmentId: "a1" }, "req-12345678");
  assert.deepEqual(body, { base_revision: 4, request_id: "req-12345678", assignment_id: "a1", module: { new_name: "Quizzes" },
    fields: { name: "Quiz 3", points: "10", group: "Quizzes", due_date: "2026-10-02", due_time: "23:59", submission: "on_paper", published: true } });
  const column = placement.placementBody({ revision: 1 }, { name: "iClicker", points: "10", module: "mod_1", due_date: "" }, { itemId: "col 1" }, "req-87654321");
  assert.deepEqual([column.item_id, column.module, column.fields.due_time, column.fields.published], ["col 1", { id: "mod_1" }, "", false]);
  assert.equal(placement.placementBody({ revision: 1 }, { name: "x", module: "" }, {}, "r-12345678").module, null);
  assert.equal(placement.placementBody({ revision: 1 }, { name: "x", group: "__new", new_group: " Quiz Category " }, {}, "r-12345678").fields.group, "Quiz Category");
});

test("the first push to a term's courses starts with one course, later ones with all", () => {
  const courses = [{ course_id: "1" }, { course_id: "2" }, { course_id: "3" }];
  assert.deepEqual(push.initialCourses(courses, []), ["1"]);
  assert.deepEqual(push.initialCourses(courses, ["1"]), ["1", "2", "3"]);
  assert.deepEqual(push.initialCourses(courses.slice(0, 1), []), ["1"]);
  assert.equal(push.opsSummary({ create_assignment: 2, create_item: 1 }), "2 assignments made · 1 item added");
  assert.equal(push.opsSummary({}), "Nothing to change");
});

test("the Canvas modules write no inline styles", async () => {
  for (const name of ["canvas-placement.js", "canvas-push.js"]) {
    const text = await readFile(new URL(`../static/${name}`, import.meta.url), "utf8");
    assert.doesNotMatch(text, /style="/, name);
  }
});

test("CANVAS: a pending conflict resolution replaces Push, so a stale preview is never pushed", () => {
  const ready = push.footerMarkup({ total: 3, pending: 0, direct: false });
  assert.match(ready, /data-cc-push/);
  assert.match(ready, /Push 3 changes/);
  assert.doesNotMatch(ready, /data-cc-resolve/);
  const term = push.footerMarkup({ total: 3, pending: 2, direct: false });
  assert.doesNotMatch(term, /data-cc-push/);
  assert.match(term, /Add 2 texts to the draft/);
  assert.match(term, /data-cc-resolve-preview>Save and preview again</);
  const direct = push.footerMarkup({ total: 0, pending: 1, direct: true });
  assert.doesNotMatch(direct, /data-cc-push/);
  assert.match(direct, /Add 1 text to the draft/);
  assert.match(direct, /data-cc-resolve-preview>Preview again</);
  assert.equal(push.resolutionKey("507", "page:hours", 1), "507|page:hours|1");
});

test("CANVAS: a row resolved in the draft is left as is in Canvas (no Replace, even under Replace all)", () => {
  const attention = [{ key: "page:a" }, { key: "page:b" }, { key: "page:c" }];
  assert.deepEqual(push.leaveResolved({ replace: ["page:a", "page:b"], remove: ["x"] }, attention, new Set(["page:a"])),
    { replace: ["page:b"], remove: ["x"] });
  assert.deepEqual(push.leaveResolved({ replace: "all" }, attention, new Set(["page:b"])), { replace: ["page:a", "page:c"] });
  assert.deepEqual(push.leaveResolved({ replace: "all" }, attention, new Set()), { replace: "all" });
  assert.deepEqual(push.leaveResolved(undefined, attention, new Set(["page:a"])), { replace: [] });
});

test("errors persist as danger toasts: no caller passes the unknown tone 'error'", async () => {
  const { toastSpec } = await import(new URL("../static/ui.js", import.meta.url).href);
  for (const options of [true, { tone: "danger" }]) {
    const spec = toastSpec("The push could not start.", options);
    assert.deepEqual([spec.tone, spec.persist, spec.timeout, spec.role], ["danger", true, 0, "alert"]);
  }
  const directory = new URL("../static/", import.meta.url);
  for (const name of (await readdir(directory)).filter((n) => n.endsWith(".js"))) {
    const text = await readFile(new URL(name, directory), "utf8");
    assert.doesNotMatch(text, /tone\s*:\s*["']error["']/, name);
  }
});
