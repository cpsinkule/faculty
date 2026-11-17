import assert from "node:assert/strict";
import test from "node:test";

import { spotSample, continueStep } from "../static/grading-review.js";
import { reviewReasons, decisionPrompt } from "../static/grading-review-layout.js";
import {reviewStart, nextReview, reviewSession, rememberReview} from "../static/grading-review-session.js";
import { jobBadge, batchOutcome, gradeLead, collectionNotice, failedLine, noPapers, choiceReads, jobDetailMarkup } from "../static/grading-jobs.js";
import { planFollower } from "../static/grading.js";
import { modelsLine, batchReadiness, batchPreview, regradeWarning } from "../static/grading-batch-layout.js";

// The grading run's derived wording (UX overhaul W2-GRADING-RUN part A): every sentence comes from saved counts, nothing is decided.
const e = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const h = {e, badge: status => `<span class="grading-badge grading-${e(status)}">${e(status)}</span>`};

test("a spot-check draws distinct responses from the pool, never more than asked or than exist", () => {
  let seed = 7;
  const random = {getRandomValues: array => { seed = (seed * 1103515245 + 12345) >>> 0; array[0] = seed; return array; }};
  const pool = Array.from({length: 40}, (_, i) => `r${i}`);
  const sample = spotSample(pool, 10, random);
  assert.equal(sample.length, 10);
  assert.equal(new Set(sample).size, 10);
  assert.ok(sample.every(id => pool.includes(id)));
  assert.deepEqual(pool.slice(0, 3), ["r0", "r1", "r2"], "the pool itself is not reordered");
  assert.deepEqual(spotSample(["a", "b"], 10, random).sort(), ["a", "b"]);
  assert.deepEqual(spotSample([], 10, random), []);
  assert.equal(spotSample(pool, 10).length, 10, "the default draws with crypto.getRandomValues");
});

const question = (id, n, version = "") => ({question_id: id, question_key: `q${n}`, exam_version: version, content: {title: `Question ${n}`}, rubric: {rules: {items: [{id: "R1"}, {id: "R2"}]}}});
const plan = {id: "p", questions: [question("a1", 1, "A"), question("a2", 2, "A"), question("b1", 1, "B")]};
const group = (id, size, applied = ["R1"]) => ({id, kind: "proposal", applied, size, items: []});

test("GRADING: resume revalidates exact identities, and Later never removes a response from the queue", () => {
  const member = (id, reason) => ({id, reason_kinds: [reason]});
  const queue = {waiting: 3, questions: [
    {question_id: "a1", groups: [{id: "g1", members: [member("r1", "rubric")]}, {id: "g2", members: [member("r2", "reading")]}]},
    {question_id: "b1", groups: [{id: "g1", members: [member("r3", "rubric")]}]},
  ]};
  const snapshot = structuredClone(queue), session = {later: new Set(["r1"]), reason: ""};
  assert.equal(nextReview(queue, {question: "a1", group: "g1"}, session).id, "r2");
  session.later.add("r2");
  assert.equal(nextReview(queue, {question: "a1", group: "g1"}, session), null, "never crosses a version/question implicitly");
  assert.deepEqual(queue, snapshot, "Later leaves counts and the server queue unchanged");
  const cursor = {question: "b1", group: "g1", response: "r3"};
  assert.deepEqual(reviewStart(plan, queue, {}, cursor), cursor);
  assert.equal(reviewStart(plan, queue, {}, {...cursor, response: "removed"}).response, "r1");
  assert.equal(reviewStart(plan, queue, {question: "a1"}, cursor).response, "r1");
  // Opening Review with nothing waiting starts nowhere: the workspace shows the next step instead.
  assert.equal(reviewStart(plan, {waiting: 0, questions: [{question_id: "a1", groups: []}]}, {}, cursor), null);
  assert.equal(reviewStart(plan, {waiting: 0, questions: []}, {}, null), null);
  assert.equal(nextReview(queue, {question: "a1", group: "g1"}, {reason: "reading", later: new Set()}).id, "r2");
  // A response the flag review is ruling on, or is about to by itself, is stepped past like one left for later (2026-10-11): it stays
  // in the queue and its counts, and a session from before `review` existed reads as before.
  const ruled = {later: new Set(), reason: "", review: new Set(["r1"])};
  assert.equal(nextReview(queue, {question: "a1", group: "g1"}, ruled).id, "r2");
  ruled.review.add("r2");
  assert.equal(nextReview(queue, {question: "a1", group: "g1"}, ruled), null);
  ruled.review.clear();
  assert.equal(nextReview(queue, {question: "a1", group: "g1"}, ruled).id, "r1");
  assert.deepEqual(queue, snapshot);
  assert.deepEqual([...reviewSession({id: "fresh-plan"}).review], []);
});

test("GRADING: session cursors are scoped to plan and batch and work without browser storage", () => {
  const p = {id: "session-test"}, cursor = {question: "a1", group: "g1", response: "r1"};
  rememberReview(p, {job: "j1"}, cursor);
  assert.deepEqual(reviewSession(p, {job: "j1"}).cursor, cursor);
  assert.equal(reviewSession(p, {job: "j2"}).cursor, null);
  assert.equal(reviewSession(p).cursor, null);
});

test("GRADING: decision shortcuts use exact rubric references and reveal nothing from blind work", () => {
  const value = {question: {rubric: {rules: {items: [{id: "R1"}, {id: "R10"}, {id: "other.part"}]}}}, result: {reasons: ["Check R10 and other.part."]}};
  assert.deepEqual(decisionPrompt(value).items, ["R10", "other.part"]);
  assert.match(decisionPrompt(value).prompt, /Items 2, 3/);
  assert.equal(decisionPrompt({...value, hidden: true}), null);
});

test("after a group: the question's next group, else the next question with work, else certification", () => {
  const queue = {questions: [{question_id: "a1", waiting: 3, groups: [group("g1", 0), group("g2", 3, ["R2"])]}, {question_id: "a2", waiting: 0, groups: []}, {question_id: "b1", waiting: 2, groups: []}]};
  const next = continueStep(plan, queue, {question: "a1", group: "g1"}, plan.questions[0].rubric.rules);
  assert.equal(next.title, "Group done.");
  assert.equal(next.label, "Next: Item 2");
  assert.equal(next.href, "#grading/p?tab=review&question=a1&group=g2");
  const onward = continueStep(plan, {questions: queue.questions.map(q => q.question_id === "a1" ? {...q, waiting: 0, groups: []} : q)}, {question: "a1", group: "g1"});
  assert.equal(onward.label, "Next question: Question 1 · Version B");
  assert.equal(onward.href, "#grading/p?tab=review&question=b1");
  // A batch's scope is kept on the way on.
  assert.equal(continueStep(plan, {questions: queue.questions.map(q => q.question_id === "a1" ? {...q, waiting: 0, groups: []} : q)}, {job: "j", question: "a1", group: "g1"}).href, "#grading/p?tab=review&job=j&question=b1");
  const done = continueStep(plan, {questions: []}, {question: "b1", group: "g"});
  assert.deepEqual([done.title, done.label, done.href], ["Everything is reviewed.", "Certification", "#grading/p?tab=export"]);
  const batch = continueStep(plan, {questions: [], other_waiting: 4}, {job: "j", question: "b1", group: "g"});
  assert.deepEqual([batch.title, batch.href], ["This batch is reviewed.", "#grading/p?tab=review"]);
});

test("why a response waits merges its reasons, the blocks and the finalization's reasons, each once, and never for a hidden proposal", () => {
  const value = {result: {reasons: ["Graders disagree", "  "], signals: {risk: {blocks: ["Graders disagree", "Little work shown"]}, rubric_quality: {reasons: ["Exclusion violated"]}}}};
  // The finalization's reasons, then Faculty's own blocks, then the graders' notes (grading-review-3).
  assert.deepEqual(reviewReasons(value), ["Exclusion violated", "Graders disagree", "Little work shown"]);
  assert.deepEqual(reviewReasons({...value, hidden: true}), []);
  assert.deepEqual(reviewReasons({result: {signals: {}}}), []);
});

test("why a response waits: Faculty's reasons first, reader notes last, a role-keyed copy kept once with its author", () => {
  // The shape of a real Exam 1 response (10 reasons, 2 repeated with a role key): 8 remain, and the three shown are Faculty's own.
  const value = {result: {reasons: [
    "Uncertain layout from line-4 to line-1: the arrow may join them.",
    "Uncertain reading of line-4: 3x or 3π.",
    "Confirm side margin for R2_4.",
    "Whether a lone 80,000 satisfies R2_2.",
    "primary: Confirm side margin for R2_4.",
    "primary: Whether a lone 80,000 satisfies R2_2.",
    "The grader found the rubric's wording unclear for this response.",
    "The handwriting is ambiguous in places.",
    "The grader was uncertain about a rubric decision.",
    "second: The second grader doubts R2_1.",
  ], signals: {risk: {blocks: ["The grader found the rubric's wording unclear for this response.", "primary: Confirm side margin for R2_4.",
    "primary: Whether a lone 80,000 satisfies R2_2.", "The handwriting is ambiguous in places."]}}}};
  assert.deepEqual(reviewReasons(value), [
    "The grader found the rubric's wording unclear for this response.",
    "The handwriting is ambiguous in places.",
    "The grader was uncertain about a rubric decision.",
    "primary: Confirm side margin for R2_4.",
    "primary: Whether a lone 80,000 satisfies R2_2.",
    "second: The second grader doubts R2_1.",
    "Uncertain layout from line-4 to line-1: the arrow may join them.",
    "Uncertain reading of line-4: 3x or 3π.",
  ]);
  // A block repeated by a grader's note keeps the block's place and shows who wrote it.
  assert.deepEqual(reviewReasons({result: {reasons: ["verifier: Little work shown"], signals: {risk: {blocks: ["Little work shown"]}}}}), ["verifier: Little work shown"]);
});

test("a finished batch with nothing waiting says Finished; the stored status is untouched", () => {
  const job = (status, progress) => ({status, progress: {total: 21, completed: 21, awaiting_review: 0, failed: 0, cancelled: 0, ...progress}});
  assert.match(jobBadge(job("review_ready", {}), h), />Finished</);
  assert.match(jobBadge(job("review_ready", {}), h), /data-status="review_ready"/);
  assert.match(jobBadge(job("review_ready", {awaiting_review: 2}), h), /grading-review_ready/);
  assert.match(jobBadge(job("review_ready", {failed: 1}), h), /grading-review_ready/);
  assert.match(jobBadge({status: "review_ready"}, h), /grading-review_ready/, "without progress nothing is claimed");
  assert.equal(batchOutcome(job("review_ready", {})), "21 graded · nothing waits for you");
  assert.equal(batchOutcome(job("complete", {awaiting_review: 1, failed: 2})), "21 graded · 1 waits for your review · 2 failed");
  assert.equal(batchOutcome(job("running", {completed: 5, total: 2400})), "5 of 2,400 responses graded so far");
});

test("the Grade step's lead comes from the plan's counts", () => {
  assert.equal(gradeLead({workflow: {new_responses: 0, scans: [{}]}}), "Nothing new to grade.");
  // A quiz drafted but not given yet: no scan and no response (journey-1).
  assert.equal(gradeLead({workflow: {new_responses: 0, scans: [], responses: {}}}), "No papers yet. Once it is given, upload its scans.");
  assert.equal(gradeLead({workflow: {new_responses: 0, scans: [], responses: {approved: 3}}}), "Nothing new to grade.");
  assert.ok(noPapers({workflow: {}}) && !noPapers({workflow: {scans: [{}]}}) && !noPapers({workflow: {responses: {pending: 1}}}) && !noPapers({workflow: {new_responses: 2}}));
  assert.equal(gradeLead({workflow: {new_responses: 1234}}), "1,234 new responses are ready to grade.");
  assert.equal(gradeLead({workflow: {new_responses: 3, regrade_responses: 3}}), "3 responses graded under an earlier rubric must be graded again.");
  assert.equal(gradeLead({workflow: {new_responses: 5, regrade_responses: 1}}), "5 new responses are ready to grade. 1 of them was graded under an earlier rubric and must be graded again.");
});

test("papers that cannot be graded are named only when counted, and never as absences", () => {
  const base = {id: "p", event_id: "ev", workflow: {scans: [{}], collection: {complete: 30, no_scan: 0, partial: 0, missing_pages: 0, needs_review: 0}, blocked_responses: 0, version_decisions: 0}};
  assert.equal(collectionNotice(base, h), "");
  // THE ZEROS ARE SAID AT CERTIFY (2026-11-05): no scanned paper is no notice here.
  assert.equal(collectionNotice({...base, workflow: {...base.workflow, collection: {...base.workflow.collection, no_scan: 2}}}, h), "");
  const two = collectionNotice({...base, workflow: {...base.workflow, collection: {...base.workflow.collection, missing_pages: 2}, blocked_responses: 46}}, h);
  assert.match(two, /2 papers are missing pages, so 46 responses cannot be graded\./);
  assert.match(two, /href="#exams\/ev\?tab=collection">Open Collection</);
  assert.match(two, /href="#grading\/p\?tab=papers"/);
  // With papers also waiting for a version decision the blocked count mixes both, so it is not attributed.
  const mixed = collectionNotice({...base, workflow: {...base.workflow, collection: {...base.workflow.collection, no_scan: 1, partial: 1, missing_pages: 2}, blocked_responses: 9, version_decisions: 1}}, h);
  assert.match(mixed, /2 papers are missing pages and 1 paper is incomplete\./);
  assert.doesNotMatch(mixed, /cannot be graded/);
  // Before any scan or response, a whole class "without a scanned paper" is no missing paper: nothing is said (journey-1).
  assert.equal(collectionNotice({...base, workflow: {...base.workflow, scans: [], responses: {}, collection: {...base.workflow.collection, no_scan: 339}}}, h), "");
});

test("failures wait for their batch's retry: the Grade step says how many", () => {
  assert.equal(failedLine({workflow: {failed: 0}}), "");
  assert.equal(failedLine({workflow: {failed: 2}}), "2 responses failed to grade and will not be graded again by themselves: retry them under Failed to grade in Review.");
  assert.equal(failedLine({workflow: {failed: 1}}), "1 response failed to grade and will not be graded again by itself: retry it under Failed to grade in Review.");
});

test("a batch with nothing eligible says why and the way on; grading again warns before Start", () => {
  const result = (extra = {}) => ({eligible: [], skipped: [], existing_count: 0, selection: "new", estimate: {estimated_cost: 0, basis: "", roles: {}}, held_questions: [], ...extra});
  assert.equal(batchReadiness(result({skipped: [{reason: "x"}]}), "local", "recognize").reason, "No eligible responses. Change your choices or check the blocked responses below.");
  assert.equal(batchReadiness(result({existing_count: 1234}), "local", "recognize").reason, "Every response was already graded (1,234). To grade them again, choose “All eligible, including regrading” under Responses.");
  assert.equal(batchReadiness(result(), "local", "recognize").reason, "No response of these questions is ready to grade yet.");
  assert.match(batchPreview(result({existing_count: 3}), "local", "recognize", h), /data-batch-regrade-all/);
  assert.doesNotMatch(batchPreview(result({skipped: [{reason: "x"}]}), "local", "recognize", h), /data-batch-regrade-all/);
  const member = (id, version) => ({response_id: id, response_version: version, source: {regions: []}});
  const again = result({selection: "all", eligible: [member("r1", 2), member("r2", 1), member(null, 0), member("r4", 0)]});
  assert.match(regradeWarning(again, "recognize", h), /data-regrade-warning/);
  assert.match(regradeWarning(again, "grade", h), /2 of these responses were graded before\./);
  assert.match(regradeWarning(again, "grade", h, "2026-09-29T15:00:00+00:00"), /These grades were certified on Sep 29\..*Certify again afterwards\./);
  assert.equal(regradeWarning(again, "rescore", h), "", "recalculating points replaces no decision");
  assert.equal(regradeWarning({...again, selection: "new"}, "recognize", h), "");
  assert.match(regradeWarning(result({selection: "all", eligible: [member("r1", 3)]}), "grade", h), /This response was graded before\.<\/strong><p>It is graded again\./);
  assert.match(regradeWarning(result({selection: "all", eligible: [member("r1", 3), member("r2", 1)]}), "grade", h), /These 2 responses were graded before\./);
  assert.match(batchPreview(again, "local", "recognize", h), /data-regrade-warning/);
});

// FRONTEND "The step graph follows the review" (2026-10-11): the plan is read again for the graph after the Review step's changes.
// Timers and reads are held by hand here, so each order of a change, a wait and an answer is exact.
function followed({active = () => true} = {}) {
  const timers = {due: [], setTimeout(run, ms) { const timer = {run, ms}; this.due.push(timer); return timer; },
    clearTimeout(timer) { this.due = this.due.filter(other => other !== timer); }};
  const reads = [], drawn = [];
  const read = () => new Promise((resolve, reject) => { reads.push({resolve, reject}); });
  const follower = planFollower(read, plan => drawn.push(plan), active, {wait: 1000, timers});
  // The wait is over: the one timer set runs. Settles what an answer started, too.
  const elapse = async () => { const [timer] = timers.due.splice(0, 1); timer?.run(); await new Promise(setImmediate); };
  return {follower, timers, reads, drawn, elapse, settle: () => new Promise(setImmediate)};
}

test("a change in Review is followed by one read of the plan, a second after the last one", async () => {
  const {follower, timers, reads, drawn, elapse, settle} = followed();
  follower.changed();
  assert.deepEqual([timers.due.length, timers.due[0].ms, reads.length], [1, 1000, 0], "nothing is read at once");
  // Three approvals within the second: one wait, counted from the last, and one read.
  follower.changed(); follower.changed();
  assert.equal(timers.due.length, 1);
  await elapse();
  assert.deepEqual([reads.length, timers.due.length, drawn.length], [1, 0, 0]);
  reads[0].resolve({id: "plan", workflow: 1});
  await settle();
  assert.deepEqual(drawn, [{id: "plan", workflow: 1}]);
  assert.deepEqual([reads.length, timers.due.length], [1, 0], "and nothing more until the next change");
});

test("a change during the plan's read is read for again, after it and never beside it, and the older answer is not drawn", async () => {
  const {follower, timers, reads, drawn, elapse, settle} = followed();
  follower.changed();
  await elapse();
  follower.changed();  // decided while the first read is under way
  await elapse();
  assert.deepEqual([reads.length, timers.due.length], [1, 1], "the second read waits for the first");
  reads[0].resolve({workflow: "before the second decision"});
  await settle();
  assert.deepEqual(drawn, []);
  await elapse();
  assert.equal(reads.length, 2);
  reads[1].resolve({workflow: "after it"});
  await settle();
  assert.deepEqual(drawn, [{workflow: "after it"}]);
});

test("a plan read that fails leaves the graph alone and the next change reads again; a page that was left reads nothing", async () => {
  const {follower, reads, drawn, elapse, settle} = followed();
  follower.changed();
  await elapse();
  reads[0].reject(new Error("Can’t reach your server."));
  await settle();
  assert.deepEqual(drawn, []);
  follower.changed();
  await elapse();
  reads[1].resolve({workflow: 2});
  await settle();
  assert.deepEqual(drawn, [{workflow: 2}]);

  let here = true;
  const left = followed({active: () => here});
  left.follower.changed();
  here = false;
  await left.elapse();
  assert.equal(left.reads.length, 0, "the wait ended after the page was left");
  left.follower.changed();
  assert.equal(left.timers.due.length, 0);
  // Left while the read was under way: its answer is not drawn.
  here = true;
  const away = followed({active: () => here});
  away.follower.changed();
  await away.elapse();
  here = false;
  away.reads[0].resolve({workflow: 3});
  await away.settle();
  assert.deepEqual(away.drawn, []);
  // stop() (a new render of the page, or leaving Grading) drops the wait.
  here = true;
  const stopped = followed();
  stopped.follower.changed();
  stopped.follower.stop();
  assert.equal(stopped.timers.due.length, 0);
});

test("a batch preview names its models on one line", () => {
  const line = modelsLine({primary: {endpoint: "Model A"}, second: {endpoint: "Model B"}, direct_reading: {endpoint: "Model A"}}, h, true);
  assert.match(line, /Models · primary: Model A · second: Model B</);
  assert.match(line, /data-grading-action="change-models"/);
  assert.equal(modelsLine({}, h), "");
});

// Part B (tasks 9-16): the Bubble sheets page, the Export step and the score summaries; all derived, nothing decided.
import { compactKey, secondLooks, scoreRows } from "../static/grading-bubble-key.js";
import { scoreLine, scoreTableMarkup, queueUrl, questionQueueUrl } from "../static/grading-review-queue.js";

test("the review desk reads its question's part of the queue, batch scope kept; the whole queue keeps its address", () => {
  const plan = {id: "p1"};
  assert.equal(queueUrl(plan), "/api/grading/assignments/p1/review-queue");
  assert.equal(queueUrl(plan, {job: "j1", question: "q1", group: "g1"}), "/api/grading/assignments/p1/review-queue?job_id=j1");
  assert.equal(questionQueueUrl(plan, {question: "q1", group: "g1"}), "/api/grading/assignments/p1/review-queue?question_id=q1");
  assert.equal(questionQueueUrl(plan, {job: "j1", question: "q1", group: "g1"}), "/api/grading/assignments/p1/review-queue?job_id=j1&question_id=q1");
});

test("the saved key reads in groups of five, with a dot for a question without an answer", () => {
  const version = {letter: "A", questions: [{number: 1, answer: "B"}, {number: 2, answer: "AC"}, {number: 4, answer: "D"}, {number: 6, answer: "E"}]};
  assert.deepEqual(compactKey(version), [{first: 1, letters: "B (AC) · D ·"}, {first: 6, letters: "E"}]);
  assert.deepEqual(compactKey({letter: "B", questions: []}), []);
});

test("worth a second look: only where at least five sheets mostly missed the saved answer and mostly marked one other letter", () => {
  const view = {key: {versions: [{letter: "A", questions: [{number: 1, answer: "B"}, {number: 2, answer: "C"}, {number: 3, answer: "D"}, {number: 4, answer: "A"}]}]},
    stats: {A: {1: {sheets: 10, full: 4, common: "C"}, 2: {sheets: 10, full: 8, common: "C"}, 3: {sheets: 4, full: 0, common: "A"}, 4: {sheets: 10, full: 2, common: "A"}}}};
  assert.deepEqual(secondLooks(view).map(l => [l.letter, l.number, l.common]), [["A", 1, "C"]]);
  assert.deepEqual(secondLooks({key: null}), []);
});

test("score rows: filter, a name search in any order, and an order; the list itself is not reordered", () => {
  const sheets = [{student: "Example, Bob", section: 501, status: "scored", earned: "8"}, {student: "Nguyen, Carol", section: 502, status: "scored", earned: "5"},
    {student: "", section: "", status: "no_key"}, {student: "Example, Alice", section: 501, status: "scored", earned: "9"}];
  assert.deepEqual(scoreRows(sheets, {search: "example 501"}).map(s => s.student), ["Example, Bob", "Example, Alice"]);
  assert.deepEqual(scoreRows(sheets, {sort: "low", filter: "scored"}).map(s => s.earned), ["5", "8", "9"]);
  assert.deepEqual(scoreRows(sheets, {sort: "high"}).map(s => s.earned ?? "-"), ["9", "8", "5", "-"]);
  assert.deepEqual(scoreRows(sheets, {sort: "name"}).map(s => s.student), ["Example, Alice", "Example, Bob", "Nguyen, Carol", ""]);
  assert.deepEqual(scoreRows(sheets, {filter: "unscored"}).length, 1);
  assert.equal(sheets[0].student, "Example, Bob");
});

test("a question's final grades in one line and one table row, from the server's read-only summary", () => {
  assert.equal(scoreLine(null, h), "");
  assert.match(scoreLine({count: 40, mean: 3.456, max: 6, full: 0.425, zero: 0.1}, h), /Final: mean 3\.46 of 6 · 43% full · 10% zero/);
  const table = scoreTableMarkup(plan, {questions: [{question_id: "a1", scores: {count: 2, mean: 4, max: 6, full: 0.5, zero: 0}}, {question_id: "b1", scores: null}]}, h);
  assert.match(table, /<th scope="row">Question 1 · Version A<\/th><td>2<\/td><td>4 of 6<\/td><td>50%<\/td><td>0%<\/td>/);
  assert.ok(!table.includes("Version B"));
  assert.match(scoreTableMarkup(plan, {questions: []}, h), /No question has a final grade yet/);
});

// The flag review in the review workspace takes room only when it has something to say (grading-review-2), one row at most.
import { flagReviewMarkup, byItselfText } from "../static/grading-flag-review.js";

test("GRADING FLAG REVIEW PROGRESS: real replies and active requests never imply an estimated total is complete", () => {
  const resolution = {id: "fr1", status: "running", model: "Reviewer", responses: 324, questions: 16,
    progress: {answered: 29, requests: 36, running: 7}};
  const draw = extra => flagReviewMarkup(plan, {resolution: {...resolution, ...extra}}, h);
  const active = draw({});
  assert.match(active, /29 replies received\. 7 requests in progress\. Additional checks may follow/);
  assert.match(active, /Grades update when the review finishes/);
  assert.match(active, /role="status"/);
  assert.doesNotMatch(active, /29 of 36|22\/22|has answered/);
  // A still-open tab can see an older server's counter too: even 22/22 must not claim completion.
  assert.match(draw({progress: {answered: 22, requests: 22}}), /22 replies received\. Processing the review/);
  assert.match(draw({status: "pending", progress: {answered: 0, requests: 0, running: 0}}), /0 replies received\. Queued to start/);
  const wait = {kind: "usage_limit", until: "2026-10-26T02:20:00+00:00"};
  const paused = draw({status: "pending", progress: {...resolution.progress, running: 0, wait}});
  assert.match(paused, /29 replies received\. Subscription usage limit reached; retry scheduled for/);
  assert.doesNotMatch(paused, /requests in progress|Additional checks may follow/);
  assert.match(draw({status: "pending", progress: {answered: 1, wait: {kind: "capacity"}}}), /1 reply received\. Model provider is busy/);
  assert.doesNotMatch(draw({progress: {wait: {kind: "retry", until: "invalid"}}}), /Invalid Date/);
});

test("GRADING FLAG REVIEW RESET: the old completed review says its original flags were restored", () => {
  const resolution = {id: "fr1", status: "complete", updated_at: "2026-10-25T20:00:00Z", model: "Reviewer",
    reset: {at: "2026-10-25T22:00:00Z", restored: 288, already_original: 36}};
  const html = flagReviewMarkup(plan, {resolution, preview: {responses: 324, questions: [], model: "Reviewer", chunk: 25}}, h);
  assert.match(html, /original flags restored/);
  assert.match(html, /288 grades restored; 36 were already unchanged/);
  assert.doesNotMatch(html, /0 of 0 flagged grades finalized/);
});

test("the flag review line: nothing, one closed line, or the offer; rulings in printed order", () => {
  const preview = (extra = {}) => ({questions: [], responses: 0, left_out: [], model: "Reviewer", chunk: 25, ...extra});
  assert.equal(flagReviewMarkup(plan, {resolution: null, preview: preview()}, h), "");
  assert.equal(flagReviewMarkup(plan, {resolution: null, preview: preview({left_out: [{why: "no line ticked", responses: 0}]})}, h), "");
  const left = flagReviewMarkup(plan, {resolution: null, preview: preview({left_out: [{why: "no line ticked", responses: 36}]})}, h);
  assert.match(left, /<details class="disclosure" data-flag-review-fold="left"><summary><span>Flag review · nothing it can rule on now<\/span>/);
  assert.match(left, /36 responses: no line ticked/);
  const offer = flagReviewMarkup(plan, {resolution: null, preview: preview({questions: [{}, {}], responses: 12})}, h);
  assert.match(offer, /Reviewer can rule on 12 flagged grades in 2 questions; anything it cannot settle stays with you\./);
  assert.match(offer, /<button type="button" class="button" data-flag-review-start>Send 12 flagged grades to Reviewer<\/button>/);
  assert.match(offer, /<summary><span>Details<\/span><\/summary>/);
  assert.doesNotMatch(offer, /grading-panel/, "one row in the workspace, not a panel");
  // A connection named by the model picker is shown by its model's name.
  const named = flagReviewMarkup(plan, {resolution: null, preview: preview({questions: [{}], responses: 3, model: "subscription-claude-opus-5-5"})}, h);
  assert.match(named, /data-flag-review-start>Send 3 flagged grades to Claude Opus 5\.5<\/button>/);
  assert.doesNotMatch(named, /subscription-claude/);
  assert.doesNotMatch(offer, /held grade/);
  const response = final => ({ref: "R1", outcome: "adopted", final, flags: [], ruling: {reason: "ok"}, score_before: 4});
  const finished = {id: "f", status: "complete", updated_at: "2026-10-02T15:00:00+00:00", model: "Reviewer", finalized: 2, totals: {responses: 3, adopted: 2, revised: 0, unreliable: 1},
    result: {questions: [{question_id: "b1", label: "Question 1 · Version B", responses: [response("finalized")]}, {question_id: "a2", label: "Question 2 · Version A", responses: [response("finalized")]},
      {question_id: "a1", label: "Question 1 · Version A", responses: [response("held")]}]}};
  const last = flagReviewMarkup(plan, {resolution: finished, preview: preview()}, h);
  assert.match(last, /<details class="disclosure" data-flag-review-last><summary><span>Last flag review · .* · 2 of 3 flagged grades finalized, 1 back in your queue<\/span>/);
  const order = [...last.matchAll(/data-flag-review-question="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(order, ["a1", "a2", "b1"], "printed order: version A's questions, then version B's");
  // Just finished, its grades still being scored: the line opens.
  const scoring = {...finished, result: {questions: [{question_id: "a1", label: "Question 1 · Version A", responses: [response("scoring")]}]}};
  assert.match(flagReviewMarkup(plan, {resolution: scoring, preview: preview()}, h), /<details class="disclosure" data-flag-review-last open><summary><span>Last flag review · .* finalized so far/);
  assert.match(flagReviewMarkup(plan, {resolution: {...finished, status: "running", progress: {answered: 1, requests: 2}, responses: 3, questions: 1}, preview: preview()}, h), /Reviewing 3 flagged grades in 1 question/);
});

// The review starts by itself after a batch (requirement, 2026-10-11): the offer says for which grades, or why it did not.
test("the flag review offer says that it starts by itself, for which grades, or why it did not", () => {
  const preview = {questions: [{}], responses: 12, left_out: [], model: "Reviewer", chunk: 25};
  const auto = (extra = {}) => ({enabled: true, waits: null, not_started: null, settle_seconds: 120, due: {responses: 12, response_ids: []}, ...extra});
  assert.equal(byItselfText(preview, undefined), "", "an answer from before the automatic review says nothing");
  assert.equal(byItselfText(preview, auto({enabled: false})), "");
  assert.equal(byItselfText(preview, auto({due: {responses: 0, response_ids: []}})), "", "these were sent once already: only the button");
  assert.equal(byItselfText(preview, auto()), " It starts by itself about two minutes after the batch; the button starts it now.");
  assert.equal(byItselfText(preview, auto({due: {responses: 5, response_ids: []}})), " It starts by itself about two minutes after the batch for the 5 just graded; the button sends all 12 now.");
  assert.equal(byItselfText(preview, auto({not_started: {reason: "No <model> reads images.", at: "t"}})), " It did not start by itself: No <model> reads images.");
  // Graded for participation (2026-11-11): the waiting grades are approved as they are, so no review is sent by itself.
  assert.match(byItselfText(preview, auto({waits: "participation", due: {responses: 0, response_ids: []}})), /graded for participation: Faculty approves these as they are/);
  const offer = flagReviewMarkup(plan, {resolution: null, preview, automatic: auto({not_started: {reason: "No <model> reads images.", at: "t"}})}, h);
  assert.match(offer, /stays with you\. It did not start by itself: No &lt;model&gt; reads images\.<\/span><button type="button" class="button" data-flag-review-start>Send 12 flagged grades to Reviewer<\/button>/);
  assert.match(flagReviewMarkup(plan, {resolution: null, preview, automatic: auto()}, h), /It starts by itself about two minutes after the batch; the button starts it now\.<\/span>/);
});

import { modelDisplayName } from "../static/grading-model-names.js";

test("connections are shown by their model's name", () => {
  assert.equal(modelDisplayName("subscription-claude-opus-5-5"), "Claude Opus 5.5");
  assert.equal(modelDisplayName("subscription-claude-sonnet-5"), "Claude Sonnet 5", "an id ending in a number is never cut short");
  assert.equal(modelDisplayName("subscription-claude-sonnet-5-2"), "Claude Sonnet 5", "the picker's -2 for a taken name");
  assert.equal(modelDisplayName("chatgpt-gpt-6-sol"), "GPT-6-Sol");
  assert.equal(modelDisplayName("Reviewer"), "Reviewer");
  assert.equal(modelDisplayName(null), "");
});

import { gradingMode, concernResponses } from "../static/grading-review-concerns.js";

test("a concern's responses are graded again the way the plan's latest batch graded, Cloud by default", () => {
  assert.equal(gradingMode({}), "cloud_standard");
  const job = (mode, created_at, extra = {}) => ({mode, created_at, operation: "grade", ...extra});
  assert.equal(gradingMode({activity: {jobs: [job("nebius", "2026-10-01", {tier: "cloud_standard"}), job("local", "2026-10-02")]}}), "local");
  assert.equal(gradingMode({activity: {jobs: [job("local", "2026-10-01"), job("nebius", "2026-10-02", {tier: "cloud_standard"})]}}), "cloud_standard");
  assert.equal(gradingMode({activity: {jobs: [job("nebius", "2026-10-02", {tier: "cloud_premium"})]}}), "cloud_standard", "a removed scheme is never started again");
  assert.equal(gradingMode({activity: {jobs: [job("local", "2026-10-01"), job("nebius", "2026-10-03", {tier: "cloud_standard", operation: "rescore"})]}}), "local");
  const entry = {groups: [{kind: "proposal", members: [{id: "a", reason_kinds: ["rubric", "grading"]}, {id: "b", reason_kinds: ["grading"]}]},
    {kind: "hidden", members: [{id: "c", reason_kinds: ["rubric"]}]}]};
  assert.deepEqual(concernResponses(entry), ["a"], "only those that raised a concern, never a blind proposal");
});

import { flagReadingsMarkup } from "../static/grading-review-layout.js";
import { siblingPreview } from "../static/grading-clarify.js";

test("a response the flag review left with the instructor shows the mark to look at and one choice per reading", () => {
  const question = {rubric: {rules: {items: [{id: "R2"}, {id: "R6"}, {id: "R10"}]}}};
  assert.equal(flagReadingsMarkup({question}, h), "");
  const note = {reason: "Look at the first cell of row 1: <4> or -14.", readings: [{reading: "reads 4", lines: ["R2", "R6"], score: 4, max_score: 5},
    {reading: "reads -14", lines: ["R2", "R6", "R10"], score: 3.5, max_score: 5}]};
  const html = flagReadingsMarkup({question, flag_review: note}, h);
  assert.match(html, /Look at the first cell of row 1: &lt;4&gt; or -14\./);
  assert.equal([...html.matchAll(/data-flag-reading="(\d)"/g)].map(m => m[1]).join(","), "0,1");
  assert.match(html, /reads -14<\/span> <strong>3\.5 \/ 5<\/strong>/);
  const only = flagReadingsMarkup({question, flag_review: {...note, readings: []}}, h);
  assert.doesNotMatch(only, /data-flag-reading="/);
});

test("a rule edited in review goes to the other versions as its added sentence only", () => {
  assert.deepEqual(siblingPreview("Keeps +18, +15.", "Keeps +18, +15. Own letters count."), {ok: true, text: "Adds “Own letters count.” after each ticked version’s own rule."});
  assert.match(siblingPreview("", "Accept 0.5.").text, /names values/);
  assert.equal(siblingPreview("Keeps +18, +15.", "Something else.").ok, false);
  assert.deepEqual(siblingPreview("Same.", "Same."), {ok: false, text: ""});
});

test("GRADING MULTIPLE-CHOICE READER HEALTH: one quiet line, a warning only when reads failed, nothing for other batches", () => {
  const h = {e: value => String(value).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c])};
  assert.equal(choiceReads({}, h), "");
  const quiet = choiceReads({choice_reads: {by_choice: 260, written: 35, reasons: {several: 25, unsure: 10}}}, h);
  assert.match(quiet, /Multiple choice: 260 read by choice · 35 graded as written work \(25 several marked, 10 unsure\)/);
  assert.doesNotMatch(quiet, /grading-warning/);
  const failed = choiceReads({choice_reads: {by_choice: 0, written: 5, reasons: {failed: 5}, failures: [{reason: "Authentication failed (HTTP 401).", responses: 5}]}}, h);
  assert.match(failed, /data-choice-failed/);
  assert.match(failed, /5 × Authentication failed \(HTTP 401\)\./);
  assert.doesNotMatch(failed, /wait for your review/);
});

test("GRADING THE CANARY WAS REMOVED: an old failed canary's card says how many responses it graded, from the detail the server sends", () => {
  const job = {id: "j", assignment_id: "a", status: "review_ready", mode: "nebius", compute: "cloud_standard", operation: "recognize", cost: 0, estimated_cost: null, runtime_seconds: 0, notice: "",
    progress: {total: 10, completed: 10, queued: 0, running: 0, failed: 0, cancelled: 0, awaiting_review: 0, stages: {}}, profile: {nebius_model: "m"}, tasks: [], reset: {resettable: false},
    canary: [{endpoint: "grader", status: "failed", agreement: null, baseline: null, detail: {compared: 3, responses: 10, reason: "too few"}}]};
  const markup = jobDetailMarkup(job, {...h, button: (name, label) => `<button data-job-action="${name}">${label}</button>`});
  assert.match(markup, /stopped before it could compare anything \(3 of 10 approved responses graded\)\. This says nothing about the model's grading\./);
  job.canary[0].detail = {};
  assert.match(jobDetailMarkup(job, {...h, button: () => ""}), /stopped before it could compare anything\. This says nothing/);
});
