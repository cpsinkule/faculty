import assert from "node:assert/strict";
import test from "node:test";
import { issueLabel } from "../static/response-card-label.js";

test("RESPONSE-CARDS issue titles replace only the supported placeholders", () => {
  const issue = { title: "{{exam.name}} · {{course.code}} · {{exam.room}} · {{constructor}} · {{student.name}}", assignment_title: "Exam 1", course_code: "MATH 140", room_name: "Blocker 101" };
  assert.equal(issueLabel(issue), "Exam 1 · MATH 140 · Blocker 101 · {{constructor}} · {{student.name}}");
  assert.equal(issueLabel({title: "{{exam.name}}"}), "{{exam.name}}");
});
