// Pure helpers of static/response-cards.js: issue titles and the answers CSV (UX overhaul, 2026-09-26).
import test from "node:test";
import assert from "node:assert/strict";
import { answersCsv, answersRows, cellText, csvCell } from "../static/response-cards.js";
import { issueLabel, LABEL_VARIABLES } from "../static/response-card-label.js";

test("issue titles fill in what the issue fixes and keep student variables literal", () => {
  const issue = {title: "{{exam.name}} · {{ exam.room }} · {{course.code}} · Hello {{student.first_name}}", assignment_title: "Test 1", room_name: "HECC 105", course_code: "MATH 140"};
  assert.equal(issueLabel(issue), "Test 1 · HECC 105 · MATH 140 · Hello {{student.first_name}}");
  assert.equal(issueLabel({title: "{{assignment.name}} notice", assignment_title: ""}), "{{assignment.name}} notice");
  assert.equal(issueLabel({}), "Response card");
});

test("the supported title placeholders", () => {
  assert.deepEqual(Object.keys(LABEL_VARIABLES), ["exam.name", "assignment.name", "course.code", "exam.room"]);
});

test("CSV cells are spreadsheet-escaped and quoted", () => {
  assert.equal(csvCell("=SUM(A1)"), "'=SUM(A1)");
  assert.equal(csvCell("+1"), "'+1");
  assert.equal(csvCell("-2"), "'-2");
  assert.equal(csvCell("@x"), "'@x");
  assert.equal(csvCell('Say "hi", then'), '"Say ""hi"", then"');
  assert.equal(csvCell(true), "Yes");
  assert.equal(csvCell(["a", "b"]), "a; b");
  assert.equal(csvCell(null), "");
});

test("the answers CSV leaves out the typed UIN, verification and expected values", () => {
  const issue = {schema: {fields: [
    {id: "intro", type: "static", label: "Read this"},
    {id: "seat", type: "confirmation", label: "Your seat", verification: {expected: "A1", mode: "normalized"}},
    {id: "confirm_uin", type: "short_text", label: "Type your UIN", verification: {expected: "salt:abc", mode: "uin-digest"}},
  ]}, recipients: [{student_name: "Example, Alice", section_number: 507, email: "a@example.test", status: "needs_review",
    current_submission: {submitted_at: "2026-09-14T12:30:00Z", answers: {seat: "=B2", confirm_uin: "123456789"}, verification: {seat: {matched: false}, confirm_uin: {matched: true}}}},
    {student_name: "Example, Bob", section_number: 507, email: "", status: "issued"}]};
  const csv = answersCsv(issue);
  const lines = csv.trim().split("\r\n");
  assert.equal(lines[0], "Student,Section,Email,Response status,Submitted at,Answers differing from the card,Your seat");
  assert.equal(lines[1], '"Example, Alice",507,a@example.test,Needs review,2026-09-14T12:30:00Z,Your seat,\'=B2');
  assert.equal(lines[2], '"Example, Bob",507,,Not opened,,,');
  for (const secret of ["123456789", "salt:abc", "A1", "matched", "UIN"]) assert.ok(!csv.includes(secret), secret);
});

test("a course field checked against the student's UIN in another mode is left out too", () => {
  const issue = {schema: {fields: [
    {id: "sid", type: "short_text", label: "Student number", verification: {expected: "{{ student.uin }}", mode: "compact"}},
    {id: "sid2", type: "short_text", label: "ID again", verification: {expected: "{{student.uin}}", mode: "exact"}},
    {id: "room", type: "short_text", label: "Room", verification: {expected: "{{exam.room}}", mode: "compact"}},
  ]}, recipients: [{student_name: "Example, Alice", section_number: 507, email: "", status: "complete",
    current_submission: {submitted_at: "2026-09-14T12:30:00Z", answers: {sid: "012345678", sid2: "012345678", room: "HECC 105"}, verification: {}}}]};
  const csv = answersCsv(issue);
  assert.equal(csv.split("\r\n")[0], "Student,Section,Email,Response status,Submitted at,Answers differing from the card,Room");
  assert.ok(!csv.includes("012345678"));
  assert.ok(csv.includes("HECC 105"));
});

test("the viewer's table and the CSV are the same rows: the CSV escapes each for spreadsheets, the table shows each as text", () => {
  const issue = { schema: { fields: [{ id: "attend", label: "Will you attend?", type: "boolean" }, { id: "times", label: "Times", type: "checkboxes" }] },
    recipients: [{ student_name: "Example, Alice", section_number: 501, email: "a@tamu.edu", status: "submitted",
      current_submission: { submitted_at: "2026-10-01T10:00:00Z", answers: { attend: true, times: ["=9am", "10am"] }, verification: {} } }] };
  const rows = answersRows(issue);
  assert.equal(answersCsv(issue), rows.map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n");
  assert.deepEqual(rows[1].map(cellText).slice(-2), ["Yes", "=9am; 10am"]);
  assert.equal(csvCell(rows[1].at(-1)), "'=9am; 10am");
});
