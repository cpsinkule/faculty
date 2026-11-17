import assert from "node:assert/strict";
import test from "node:test";

import { comingUp, currentPlans, daySentence, finishedMarkup, gradingAttention, healthLine, needsCount, needsYou, progressPlaceholder } from "../static/home.js";

// Home's Needs you list is a view of the server's attention cards and current scans (plus, once read, Grading's own
// counts). It names no student and decides nothing; these check which rows it makes and where they lead.
const stages = (current) => [
  {key: "scan", label: "Scan", title: "Check the scanned pages", state: "done", status: "Done", detail: "", page: "pages", urgent: false},
  {key: "students", label: "Students", title: "Find whose paper each is", state: current ? "started" : "done", status: "To do", detail: "Continue.", page: "students", urgent: true},
];
const scan = (id, listed_as, headline = "Quiz 2 · papers without labels") => ({id, listed_as, href: `#inbox/job/${id}`, created_at: "2026-09-26T04:32:29+00:00",
  pages: {done: 650, total: 650}, scan: {headline, state: listed_as === "running" ? "wait" : "todo", stages: stages(true), steps: []}});

test("scans that need the instructor lead to their stage page; the worker's scans are not listed", () => {
  const data = {attention: [{key: "inbox", href: "#inbox", derived: true, kind: "review", label: "Scans and submissions that need you", count: 2, scans: 1, submissions: 1}],
    current_scans: [scan("a", "needs_review"), scan("b", "running", "This scan is being inspected")]};
  const rows = needsYou(data);
  assert.deepEqual(rows.map((row) => row.key), ["scan:a", "inbox:submissions"]);
  assert.equal(rows[0].href, "#inbox/job/a/students");
  assert.equal(rows[0].verb, "Find students", "a scan row's button is a verb, by the stage");
  assert.equal(rows[0].detail, "Needs you: find whose paper each is", "scan-flow.js scanNext's words (the Needs you chip, 2026-10-18)");
  assert.deepEqual(rows[0].facts, {pages: 650, received: "2026-09-26T04:32:29+00:00"}, "scans with one headline are told apart, never by filename");
  assert.equal(rows[1].count, 1);
  assert.equal(rows[1].href, "#inbox?status=submissions");
  assert.equal(rows[1].detail, "1 submission waits for your review.", "no failure is mentioned when nothing failed");
});

test("the submissions row says how many wait and how many failed, each only when there are some", () => {
  const detail = (submissions, submissions_failed) => needsYou({attention: [{key: "inbox", href: "#inbox", derived: true, count: submissions,
    scans: 0, submissions, submissions_failed}], current_scans: []})[0];
  assert.equal(detail(3, 1).detail, "2 submissions wait for your review, 1 submission failed.");
  assert.equal(detail(3, 1).kind, "error");
  assert.equal(detail(2, 2).detail, "2 submissions failed.");
  assert.equal(detail(1, 0).kind, "review");
});

test("the hero counts the things that need the instructor, not the rows (review W2-HOME-F1/D2)", () => {
  // 8 scans need the instructor (5 listed, then "3 more"), 3 submissions, 14 flagged answers, and Test 1's two decisions: Review
  // grades (240 responses, one decision, as the assignment tabs and the Assignments badge count it, 2026-10-18) and the rubric check.
  const current = Array.from({length: 8}, (_, index) => scan(`s${index}`, "needs_review"));
  const data = {attention: [
    {key: "inbox", href: "#inbox", derived: true, count: 11, scans: 8, submissions: 3, submissions_failed: 0},
    {key: "cards_flagged", href: "#response-cards/issue/i1", kind: "review", label: "Flagged card answers", count: 14},
  ], current_scans: current};
  const grading = gradingAttention({assignments: [{id: "p1", title: "Test 1", workflow: {review_responses: 240, setup: {rubric_quality: {state: "needs_review"}}}}]});
  const rows = needsYou(data, grading);
  assert.equal(rows.length, 10);
  assert.equal(rows.find((row) => row.key === "scan:more").items, 3);
  assert.equal(rows.find((row) => row.key === "grading:p1:review").count, 240, "the row still says how many responses");
  assert.equal(needsCount(rows), 8 + 3 + 14 + 1 + 1);
  assert.equal(daySentence({needs: needsCount(rows)}), "27 things need you");
  assert.equal(needsCount(null), null);
});

test("a card counted in another card keeps its row and count but adds nothing to the hero (TEACHING-WORKFLOWS NEEDS ME COUNT)", () => {
  const rows = needsYou({attention: [
    {key: "support:wellbeing", href: "#follow-ups?filter=wellbeing", kind: "review", label: "Possible wellbeing concerns", count: 1, counted_in: "support:followup"},
    {key: "support:followup", href: "#follow-ups", kind: "review", label: "Student follow-up", count: 2},
  ], current_scans: []});
  assert.deepEqual(rows.map((row) => [row.key, row.count, row.items]), [["support:wellbeing", 1, 0], ["support:followup", 2, 2]]);
  assert.equal(needsCount(rows), 2);
});

test("scan buttons are verbs for the stage, and the worker's stage is watched", () => {
  const verb = (key, status = "To do") => needsYou({attention: [], current_scans: [{...scan("v", "needs_review"),
    scan: {headline: "Quiz", stages: [{key, label: key, title: "Do it", state: "started", status, urgent: status !== "Working", detail: ""}]}}]})[0].verb;
  assert.deepEqual(["scan", "assignment", "students", "sheets", "papers", "review"].map((key) => verb(key)),
    ["Check pages", "Choose assignment", "Find students", "Review sheets", "Check papers", "Finish review"]);
  assert.equal(verb("sheets", "Working"), "View progress");
});

test("Home speaks for the grading plans of current terms only, as the server's cards and the badge do (W2-HOME-F3)", () => {
  const data = {assignments: [{id: "now", title: "Test 1", workflow: {review_responses: 2}}, {id: "old", title: "Spring final", workflow: {review_responses: 9}}]};
  assert.deepEqual(gradingAttention(data, currentPlans({current_grading_plans: ["now"]})).map((row) => row.key), ["grading:now:review"]);
  assert.equal(currentPlans({}), null, "an older server keeps every plan");
  assert.equal(gradingAttention(data, null).length, 2);
});

test("more than five scans become one link to the Inbox, counted from the server's filing", () => {
  const current = Array.from({length: 7}, (_, index) => scan(`s${index}`, "needs_review"));
  const rows = needsYou({attention: [{key: "inbox", href: "#inbox", derived: true, count: 9, scans: 9, submissions: 0}], current_scans: current});
  assert.equal(rows.filter((row) => row.key.startsWith("scan:s")).length, 5);
  const more = rows.find((row) => row.key === "scan:more");
  assert.match(more.title, /^4 more scans need you$/);
  assert.equal(more.href, "#inbox");
});

test("Grading's own counts replace the server's light grading cards and sit after the Inbox rows", () => {
  const data = {attention: [
    {key: "inbox", href: "#inbox", derived: true, count: 1, scans: 0, submissions: 1},
    {key: "grading:p1:review", href: "#grading/p1?tab=review", kind: "review", label: "Review grades · Test 1", count: 3},
    {key: "exam_requests", href: "#exams", kind: "review", label: "Exam requests to review", count: 1, detail: "1 student request is open.", verb: "Review"},
  ], current_scans: []};
  assert.deepEqual(needsYou(data).map((row) => row.key), ["inbox:submissions", "grading:p1:review", "exam_requests"]);
  // An assignment's card is one decision, whatever it counts (the Assignments badge's rule); the Inbox's submissions count each.
  assert.deepEqual(needsYou(data).map((row) => row.items), [1, 1, 1]);
  const grading = gradingAttention({assignments: [{id: "p1", title: "Test 1", workflow: {review_responses: 4, version_decisions: 3, failed: 2,
    setup: {rubric_quality: {state: "needs_review"}}}}]});
  assert.deepEqual(grading.map((row) => [row.key, row.count, row.href]), [
    ["grading:p1:review", 4, "#grading/p1?tab=review"],
    ["grading:p1:versions", 3, "#grading/p1?tab=rubrics&focus=versions"],
    ["grading:p1:failed", 2, "#grading/p1?tab=review&open=failed"],
    ["grading:p1:finalization", null, "#grading/p1?tab=answer-key&open=quality"]]);
  // The assignment's needs-you words (2026-10-18), the same as the server's card it replaces, so the row does not change once
  // Grading is read.
  assert.deepEqual(grading.map((row) => row.title), ["Review grades · Test 1", "Decide paper versions · Test 1", "Inspect failed grading · Test 1",
    "Review the rubric check · Test 1"]);
  assert.deepEqual(grading.map((row) => row.items), [1, 1, 1, 1]);
  assert.deepEqual(needsYou(data, grading).map((row) => row.key),
    ["inbox:submissions", "grading:p1:review", "grading:p1:versions", "grading:p1:failed", "grading:p1:finalization", "exam_requests"]);
});

test("nothing to do is an empty list; an assignment with nothing waiting adds no rows", () => {
  assert.deepEqual(needsYou({attention: [], current_scans: []}), []);
  assert.deepEqual(gradingAttention({assignments: [{id: "p", title: "Quiz", workflow: {review_responses: 0, failed: 0}}]}), []);
});

test("Coming up groups the next three weeks and links each group to its calendar day and its exam", () => {
  const dr = (section, bookings) => ({id: `dr${section}`, source: "dr", title: "Test 2", date: "2026-10-22", end_date: "2026-10-22", time: "18:30", end_time: "20:58",
    definition_id: "d1", code: "MATH 140", sections: [section], booking_count: bookings, time_basis: "dr_center",
    href: "#accommodations?tab=exams&course=MATH%20140&exam=Test%202&when=all", exam_href: "#exams/assessment/a2/t1"});
  const rows = comingUp([dr(501, 3), dr(502, 5), {id: "x", source: "custom", title: "Office hours", date: "2026-09-26", end_date: "2026-09-26", time: "", all_day: true, sections: [], href: "#calendar"}], "2026-09-26");
  assert.deepEqual(rows.map((row) => [row.title, row.relative]), [["Office hours", "today"], ["Test 2", "in 26 days"]]);
  const test2 = rows[1];
  assert.equal(test2.facts, "DR exam · MATH 140 · 2 sections · 8 bookings · 6:30–8:58 PM DR-center time");
  assert.equal(test2.href, "#calendar?month=2026-10&view=month&date=2026-10-22");
  assert.deepEqual(test2.source, {label: "Exam", href: "#exams/assessment/a2/t1"});
  assert.equal(rows[0].source, null, "your own events have no other record");
});

test("the hero sentence says what needs the instructor, the grading step and what is next", () => {
  assert.equal(daySentence({needs: 0}), "Nothing needs you");
  assert.equal(daySentence({needs: 2, grading: {title: "Test 1", label: "Certify the grades"}, next: {title: "Test 2", type: "dr", when: "Thu, Oct 22", relative: "in 26 days"}}),
    "2 things need you · Test 1: Certify the grades · Next: Test 2 DR exams, Thu, Oct 22");
  assert.equal(daySentence({}), "");
});

test("workspace health states itself and never calls an incomplete backup verified", () => {
  const at = (value) => `at ${value}`;
  const base = {worker: {state: "active"}, totals: {running: 0, pending: 2, failed: 0},
    storage: {enabled: true, backup: {status: "complete", verified_at: "X"}, disks: [{label: "Working storage", state: "ready", free_bytes: 3.1 * 1024 ** 4}]}};
  assert.equal(healthLine(base, at), "Worker responding · 2 queued · Backup verified at X · Working storage 3.1 TB free");
  const partial = {...base, storage: {...base.storage, backup: {status: "interrupted", verified_at: null}}};
  assert.match(healthLine(partial, at), /Backup interrupted · not verified/);
  assert.match(healthLine({...base, worker: {state: "stalled"}}, at), /^Worker not responding/);
});

// A grading plan as GET /api/grading lists it: the setup's questions and templates under workflow.setup.
const template = {id: "t1", label: "Blank exam", page_count: 2};
const gradingQuestion = (number, {status = "confirmed", approved = true, rubric = true} = {}) => ({question_id: `q${number}`, question_key: `q${number}`,
  content: {title: `Question ${number}`}, rubric: rubric ? {approved_at: approved ? "2026-09-19" : null} : null, review_ready: true, has_answer_key: true,
  location_check: {status, label: "", context: {printed_number: String(number), template, templates: [template]}}});
const gradingPlan = (id, questions, workflow = {}, extra = {}) => ({id, title: `Quiz ${id}`, code: "MATH 140", term: "Fall", year: 2026,
  entry: {ready: true, cleared_scans: 0}, ...extra,
  workflow: {scans: [], awaiting_review: 0, version_decisions: 0, responses: {}, collection: {}, new_responses: 0, pending: 0, failed: 0,
    blocked_responses: 0, review_responses: 0, certification: {state: "none"},
    setup: {questions, templates: [template], exam_versions: {letters: [], graded: [], blanks: {}}, rubric_quality: {state: "confirmed", readiness: {}},
      template_setup: {confirmed: true, slots: [{confirmed: true}]}, preparation: {}}, ...workflow}});

test("papers waiting for a setup step need the instructor there; before any paper, nothing does (journey-5)", () => {
  const empty = gradingPlan("p", []);
  assert.deepEqual(gradingAttention({assignments: [empty], jobs: []}), [], "no paper yet: the rubrics wait for nobody");
  const waiting = gradingPlan("p", [], {}, {entry: {ready: true, cleared_scans: 1}});
  const rows = gradingAttention({assignments: [waiting], jobs: []});
  assert.deepEqual(rows.map((row) => [row.key, row.href, row.verb]), [["grading:p:setup", "#grading/p?tab=rubrics&open=upload", "Upload rubrics"]]);
  assert.equal(rows[0].title, "Finish grading preparation · Quiz p", "the assignment's needs-you words; the detail names the step");
  assert.equal(rows[0].items, 1);
  assert.equal(rows[0].detail, "Grading waits until you upload the rubrics of every version.");
  // Reviewed papers count as papers in, as new responses do.
  assert.equal(gradingAttention({assignments: [gradingPlan("p", [], {collection: {complete: 3}})], jobs: []})[0].key, "grading:p:setup");
});

test("a setup step the pipeline is working needs nobody yet; one waiting for the instructor says what to do (review fix)", () => {
  const setup = (changes) => ({...gradingPlan("x", []).workflow.setup, questions: [gradingQuestion(1)], ...changes});
  const keys = (changes, extra = {}) => gradingAttention({assignments: [gradingPlan("p", [gradingQuestion(1)], {new_responses: 4, setup: setup(changes)}, extra)], jobs: []});
  // The rubric check running, a rubric preparation queued: "In progress", nothing for the instructor (nor, before setup is done,
  // the Grade step's reason that grading did not start).
  assert.deepEqual(keys({rubric_quality: {state: "running", readiness: {}}}, {auto_grade: {enabled: false}}), []);
  assert.deepEqual(keys({preparation: {pending: 1}}), []);
  const needed = keys({rubric_quality: {state: "needed", readiness: {}}});
  assert.deepEqual(needed.map((row) => [row.key, row.verb, row.href]), [["grading:p:setup", "Finalize the rubrics", "#grading/p?tab=answer-key&open=quality"]]);
  assert.equal(needed[0].title, "Finish grading preparation · Quiz p");
  assert.equal(needed[0].detail, "Grading waits until you run the rubric check.");
  const keyless = keys({questions: [{...gradingQuestion(1), has_answer_key: false}], rubric_quality: {state: "needed", readiness: {}}});
  assert.deepEqual(keyless.map((row) => [row.key, row.detail]), [["grading:p:setup", "Grading waits until you check the answer key of every question."]]);
});

test("answer areas ready to confirm are one row per assignment, and its step says nothing twice", () => {
  const plan = gradingPlan("p", [gradingQuestion(1, {status: "needs_confirmation"}), gradingQuestion(2, {status: "needs_review"}), gradingQuestion(3, {status: "pending"})],
    {new_responses: 4});
  const rows = gradingAttention({assignments: [plan], jobs: []});
  assert.deepEqual(rows.map((row) => [row.key, row.count, row.href]), [["grading:p:areas", 2, "#grading/p?tab=locations"]]);
  assert.equal(rows[0].title, "Check answer areas and templates · Quiz p");
  assert.equal(rows[0].items, 1, "one decision, however many questions");
  assert.equal(rows[0].detail, "The suggested answer areas of 2 questions wait for your look.");
  // The key's step with a finished check: the rubric check row says it, not a second "Papers waiting" row.
  const key = gradingPlan("k", [gradingQuestion(1)], {new_responses: 4, setup: {...plan.workflow.setup, questions: [gradingQuestion(1)],
    rubric_quality: {state: "needs_review", readiness: {}}}});
  assert.deepEqual(gradingAttention({assignments: [key], jobs: []}).map((row) => row.key), ["grading:k:finalization"]);
});

test("grades ready to certify are the instructor's to certify; a current certification needs nothing (usability audit, 2026-10-04)", () => {
  const ready = (certification = {state: "none"}) => gradingPlan("p", [gradingQuestion(1)], {collection: {complete: 3}, responses: {approved: 3}, certification});
  const jobs = [{assignment_id: "p", status: "complete"}];
  const rows = gradingAttention({assignments: [ready()], jobs});
  assert.deepEqual(rows.map((row) => [row.key, row.verb, row.href, row.items]), [["grading:p:certify", "Certify", "#grading/p?tab=export", 1]]);
  assert.equal(rows[0].title, "Certify grades · Quiz p");
  assert.match(rows[0].detail, /nothing is published or sent/);
  assert.match(gradingAttention({assignments: [ready({state: "changed"})], jobs})[0].detail, /changed after you certified/);
  assert.deepEqual(gradingAttention({assignments: [ready({state: "current", created_at: "2026-10-01T00:00:00Z"})], jobs}), []);
  // READY TO CERTIFY MEANS NOTHING IS STILL COMING (2026-10-11): a linked scan still in its Inbox checks holds the row back, for a
  // changed certification too; with the scan through, it is back.
  const late = (certification, awaiting_review) => ({...ready(certification), workflow: {...ready(certification).workflow, awaiting_review}});
  assert.deepEqual(gradingAttention({assignments: [late({state: "none"}, 1)], jobs}), []);
  assert.deepEqual(gradingAttention({assignments: [late({state: "changed"}, 2)], jobs}), []);
  assert.deepEqual(gradingAttention({assignments: [late({state: "none"}, 0)], jobs}).map((row) => row.key), ["grading:p:certify"]);
  // Still reviewing: the review row says it, not a certify row.
  const reviewing = gradingPlan("p", [gradingQuestion(1)], {collection: {complete: 3}, responses: {approved: 2}, review_responses: 1});
  assert.deepEqual(gradingAttention({assignments: [reviewing], jobs}).map((row) => row.key), ["grading:p:review"]);
});

test("new responses that did not start grading by itself are named with the Grade step's reason (journey-5)", () => {
  const auto = (state) => gradingPlan("p", [gradingQuestion(1)], {new_responses: 12}, {auto_grade: {enabled: true, paused: null, not_started: null, settle_seconds: 120, ...state}});
  const keys = (plan) => gradingAttention({assignments: [plan], jobs: []}).map((row) => row.key);
  assert.deepEqual(keys(auto({})), [], "it starts by itself: nothing to do");
  const refused = gradingAttention({assignments: [auto({not_started: {reason: "Choose a grading model in Compute settings.", responses: 12}})], jobs: []});
  assert.deepEqual(refused.map((row) => [row.key, row.count, row.items, row.href, row.verb]),
    [["grading:p:auto", 12, 1, "#grading/p?tab=jobs", "Open Grade"]]);
  assert.equal(refused[0].title, "Grading did not start by itself · Quiz p");
  assert.equal(refused[0].detail, "A batch could not start by itself: Choose a grading model in Compute settings.");
  assert.match(gradingAttention({assignments: [auto({paused: "cancelled"})], jobs: []})[0].detail, /paused because the last batch was cancelled/);
  assert.match(gradingAttention({assignments: [auto({enabled: false})], jobs: []})[0].detail, /^Automatic grading is off/);
  assert.deepEqual(keys(gradingPlan("p", [gradingQuestion(1)], {new_responses: 0}, {auto_grade: {enabled: false}})), [], "nothing new: nothing to start");
  // A setup step comes first: its row leads there, and grading cannot start before it anyway.
  const setup = gradingPlan("p", [gradingQuestion(1), gradingQuestion(2, {rubric: false})], {new_responses: 12}, {auto_grade: {enabled: false}});
  assert.deepEqual(keys(setup), ["grading:p:setup"]);
});

test("finished scans are linked where the Inbox lists them: History, or Archived once their grades are certified (journey-3)", () => {
  const e = (value) => String(value);
  const hrefs = (html) => [...html.matchAll(/href="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(finishedMarkup({finished_recent: 0}, e), "");
  assert.deepEqual(hrefs(finishedMarkup({finished_recent: 2}, e)), ["#inbox?status=history"], "an older server: History, as before");
  const all = finishedMarkup({finished_recent: 7, finished_recent_archived: 7}, e);
  assert.match(all, /7 scans from the last 7 days are finished, with certified grades/);
  assert.deepEqual(hrefs(all), ["#inbox?status=archived"]);
  const both = finishedMarkup({finished_recent: 7, finished_recent_archived: 4}, e);
  assert.match(both, /From the last 7 days: 3 scans finished · .*History.* · 4 with certified grades · .*Archived/);
  assert.deepEqual(hrefs(both), ["#inbox?status=history", "#inbox?status=archived"]);
});

test("In progress keeps the place the overview says it will take while Grading is read (rest-shell-6, review fix)", () => {
  // No current plan: nothing can show, so no place is held (the panel used to appear, then leave when Grading answered).
  assert.equal(progressPlaceholder({current_grading_plans: [], attention: []}), "none");
  // One plan no card names is most likely one line ("Quiz 4: grading not started"); one with work waiting is a card.
  assert.equal(progressPlaceholder({current_grading_plans: ["p"], attention: []}), "line");
  assert.equal(progressPlaceholder({current_grading_plans: ["p"], attention: [{key: "grading:p:review", count: 3}]}), "cards");
  assert.equal(progressPlaceholder({current_grading_plans: ["p"], attention: [{key: "queue:frq_locations:failed:p", plan_id: "p"}]}), "cards");
  assert.equal(progressPlaceholder({current_grading_plans: ["p"], attention: [{key: "grading:other:review"}]}), "line");
  assert.equal(progressPlaceholder({current_grading_plans: ["p", "q"], attention: []}), "cards");
  assert.equal(progressPlaceholder({attention: []}), "cards", "an older server lists no plans");
});

test("Coming up leaves out a Term schedule's routine rows (SCHEDULING, 2026-10-05)", () => {
  const rows = comingUp([
    { id: "schedule:a:2026-10-06", source: "schedule", title: "Section 507", date: "2026-10-06", time: "08:00", code: "MATH 140" },
    { id: "exam:1", source: "exam", title: "Quiz 5", date: "2026-10-09", time: "08:00", code: "MATH 140" },
  ], "2026-10-05");
  assert.deepEqual(rows.map((row) => row.title), ["Quiz 5"]);
});

test("Assistant changes waiting: a row only while some wait, counted once, opening the drawer's Changes (ASSISTANT HOME CARD, 2026-10-09)", () => {
  assert.equal(needsYou({attention: [], current_scans: []}).some((row) => row.key === "assistant:changes"), false);
  const rows = needsYou({attention: [], current_scans: [], assistant_changes: 3});
  const row = rows.find((item) => item.key === "assistant:changes");
  assert.equal(row.title, "Assistant changes waiting");
  assert.equal(row.count, 3);
  assert.equal(row.detail, "3 changes the Assistant prepared wait for your Approve.");
  assert.equal(row.assistant, "changes");
  assert.equal(row.verb, "Review changes");
  assert.equal(needsCount(rows), 3);
});
