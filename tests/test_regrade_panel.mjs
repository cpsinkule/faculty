// Return › Regrade requests (static/grading-regrades.js; REGRADES 2026-10-07): the pure helpers. Where the grade is compares like with
// like (the question on Faculty and Gradescope, the total in the gradebook and Canvas) and names what differs; Apply's steps read as
// plain words; the list puts open requests first with each student named and folds the rest; a run's stopped step offers its way on.
import assert from "node:assert/strict";
import { whereSummary, stepWords, listMarkup, runMarkup, who, suggestionMarkup, sheetMarkup, letters } from "../static/grading-regrades.js";

const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
const h = {e};

const agree = {faculty: {points: "4", of: "6"}, gradescope: {points: "4", state: "sent"}, certified: {earned: "72"}, gradebook: {value: "72", source: "grading"},
  canvas: {value: "72"}};
assert.deepEqual(whereSummary(agree), {parts: [["Faculty", "4 / 6"], ["Gradescope", "4"], ["Gradebook", "72"], ["Canvas", "72"]], differs: []});
// A grade changed in Faculty: Gradescope differs until Apply sends it; the gradebook keeps the certified total until then.
assert.deepEqual(whereSummary({...agree, faculty: {points: "6", of: "6"}, gradescope: {points: "4", state: "changed"}}).differs, ["Gradescope"]);
assert.deepEqual(whereSummary({...agree, canvas: {value: "70"}}).differs, ["Canvas"]);
// A value typed in the gradebook is the instructor's: not called different from the certified total.
assert.deepEqual(whereSummary({...agree, gradebook: {value: "75", source: "instructor"}}).differs, ["Canvas"]);
assert.deepEqual(whereSummary(null), {parts: [], differs: []});
assert.deepEqual(whereSummary({faculty: null, gradescope: {points: null, state: "not_final"}}).parts, [["Faculty", "no final grade"], ["Gradescope", "–"]]);

assert.equal(stepWords({step: "gradescope", from: "4", to: "6", published: ["Exam"]}), "This grade on Gradescope: 4 → 6 · published: the student sees it at once");
assert.equal(stepWords({step: "reply"}), "Your reply, once Gradescope shows the grade · it closes the request");
assert.equal(stepWords({step: "gradebook", from: "72", to: "74"}), "Gradebook, certifying just this change: 72 → 74");
assert.equal(stepWords({step: "gradebook", from: "72", to: null, typed: true, replace: false}), "Gradebook, certifying just this change: 72 → the certified value · your typed value stays");
assert.equal(stepWords({step: "gradebook", problem: "Other grades changed"}), "The gradebook waits for Certify again");
assert.equal(stepWords({step: "canvas", from: "72", to: "74", course: "MATH-140-508"}), "Canvas (MATH-140-508): 72 → 74");

assert.equal(who({student: {name: "Example, Alice", section: 508}}), "Example, Alice · 508");
assert.equal(who({}), "Student not linked");

const open = {id: "m1", created_at: "2026-10-06T22:13:40Z", state: "open", question: "Question 20", exam_version: "A", student: {name: "Example, <Alice>", section: 508}, faculty: {points: "4", of: "6"}};
const done = {...open, id: "m2", state: "answered"};
const list = listMarkup([done, open], "m1", h);
assert.ok(list.indexOf('data-regrade-open="m1"') < list.indexOf('data-regrade-open="m2"'), "open requests come first");
assert.match(list, /data-regrade-open="m1" aria-current="true"/);
assert.match(list, /Example, &lt;Alice&gt; · 508/);  // names are text
assert.match(list, /Answered or handled · 1/);
assert.match(listMarkup([], "", h), /No regrade requests/);

const stopped = {id: "r1", finished: false, waiting: false, stopped: true, steps: [
  {step: "gradescope", status: "done", from: "4", to: "6", detail: ""},
  {step: "reply", status: "stopped", detail: "Gradescope shows 4 for the question, not 6."},
  {step: "gradebook", status: "pending", detail: ""}]};
const markup = runMarkup(stopped, h);
assert.match(markup, /Apply · Stopped/);
assert.match(markup, /data-regrade-retry="reply"/);
assert.match(markup, /Gradescope shows 4 for the question, not 6\./);
assert.match(markup, /data-regrade-cancel/);
assert.doesNotMatch(markup, /data-regrade-continue/);  // a stopped step is retried, not continued past
const linked = runMarkup({...stopped, steps: [{step: "gradebook", status: "stopped", detail: "Other grades changed.", href: "#grading/p?tab=export", action: "Certify again…"}]}, h);
assert.match(linked, /href="#grading\/p\?tab=export">Certify again…/);
assert.doesNotMatch(linked, /data-regrade-retry/);
assert.match(runMarkup({...stopped, finished: true, stopped: false, steps: []}, h), /Apply · Finished/);
assert.equal(runMarkup(null, h), "");
// ONE SUGGESTION (REGRADES "FOLLOW-UP"): Follow-up's decision on Return, or that it is preparing one; nothing without one.
const change = suggestionMarkup({state: "ready", href: "#follow-ups", action: {decision: "change", from: "4", to: "6", of: "6", reason: "The setup is <right>.",
  steps: [{step: "grade", added: ["R3_1"], removed: ["R3_2"]}], reply: "Hi."}}, h);
assert.match(change, /Follow-up suggests: Change the grade to 6 \/ 6/);
assert.match(change, /The setup is &lt;right&gt;\./);
assert.match(change, /Rubric lines \+R3_1, −R3_2/);
assert.match(change, /data-outward data-regrade-approve>Approve the suggestion/);
assert.match(suggestionMarkup({state: "ready", action: {decision: "keep", from: "4", of: "6", steps: []}}, h), /Keep the grade \(4 \/ 6\)/);
assert.match(suggestionMarkup({state: "preparing", action: null}, h), /Follow-up is preparing a suggestion/);
assert.equal(suggestionMarkup({state: "none", action: null}, h), "");
assert.equal(suggestionMarkup(null, h), "");
console.log("regrade panel helpers ok");

// A bubble-sheet question (REGRADES "BUBBLE SHEETS", 2026-10-28): Gradescope is known only where Faculty corrected it, so it differs
// only when it shows another score; the panel shows the question's row of the scan with what Faculty read and the key.
const mcWhere = {kind: "mc", faculty: {points: "4", of: "4"}, gradescope: null, certified: {earned: "54"}, gradebook: {value: "54", source: "grading"}};
assert.deepEqual(whereSummary(mcWhere).differs, []);
assert.deepEqual(whereSummary({...mcWhere, gradescope: {points: "0", state: "failed"}}).differs, ["Gradescope"]);
assert.deepEqual(whereSummary({...mcWhere, gradescope: {points: "4", state: "accepted"}}).differs, []);
assert.equal(letters("BD"), "B and D");
assert.equal(letters(""), "a blank");
const sheet = sheetMarkup({sheet_id: "s1", number: 3, version: "B", marked: "", answer: "D", scoring: "exact", points: "4", earned: "0",
  sheet_earned: "50", sheet_possible: "60", picture: "/api/bubble-sheets/sheets/s1/parts/q3"}, h);
assert.match(sheet, /<img class="regrade-sheet-row" src="\/api\/bubble-sheets\/sheets\/s1\/parts\/q3"/);
assert.match(sheet, /Faculty read <strong>a blank<\/strong>/);
assert.match(sheet, /your key <strong>D<\/strong> \(exact\)/);
assert.match(sheetMarkup({earned: null, reason: "Version C has no answers in the key yet."}, h), /Version C has no answers/);
const mcSuggestion = suggestionMarkup({state: "ready", href: "#follow-ups", action: {decision: "change", answer: "D", number: 3, to: "4", of: "4", reason: "D is filled."}}, h);
assert.match(mcSuggestion, /Follow-up suggests: Read D for question 3: 4 \/ 4/);
