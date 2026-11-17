import assert from "node:assert/strict";
import test from "node:test";

import { guideSteps } from "../static/grading-guide.js";
import { assignmentNext, autoGradeLine, inboxNoticeMarkup } from "../static/grading-overview.js";

// assignmentNext is the derivation of an assignment's next step (Home reuses it): the same steps,
// status sentence, detail and destination the card shows, with nothing decided.
const template = {id: "t1", label: "Blank exam", page_count: 12};
const question = (number, {status = "needs_confirmation", approved = false, rubric = true} = {}) => ({question_id: `id-${number}`, question_key: `q${number}`,
  content: {title: `Question ${number}`}, answer: {key: [{label: "", accept: ["6"]}]}, rubric: rubric ? {approved_at: approved ? "2026-09-19" : null} : null,
  location_check: {status, label: "", context: {printed_number: String(number), template, templates: [template]}}});
const plan = (id, questions, workflow = {}) => ({id, title: `Quiz ${id}`, code: "MATH 140", term: "Fall", year: 2026, questions, templates: [template],
  papers: {print_jobs_attached: 1, print_jobs_available: 0, scans_awaiting_review: 0, reviewed_papers: 3}, rubric_quality: {enabled: true, state: "confirmed"},
  template_setup: {confirmed: true, slots: [{confirmed: true}]},
  workflow: {scans: [], awaiting_review: 0, version_decisions: 0, responses: {}, collection: {}, new_responses: 0, pending: 0, failed: 0,
    blocked_responses: 0, review_responses: 0, export: {current: true}, certification: {state: "current", created_at: "2026-09-26T15:00:00+00:00"}, ...workflow}});
const h = {e: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;"), button: () => ""};

test("the next step names the current step, its detail and its page", () => {
  const p = plan("p1", [question(1, {rubric: false})]);
  const next = assignmentNext(p, []);
  const steps = guideSteps(p, []);
  assert.deepEqual(next.steps.map(s => s.key), steps.map(s => s.key));
  assert.equal(next.current.key, "rubrics");
  assert.equal(next.status, `Next: ${next.current.title.charAt(0).toLowerCase()}${next.current.title.slice(1)}`);
  assert.equal(next.detail, next.current.detail);
  assert.equal(next.href, next.current.destination);
  assert.equal(next.label, next.current.label);
});

test("a failed step says it needs attention", () => {
  const p = plan("p2", [question(2), question(3)], {failed: 2});
  const next = assignmentNext(p, [{assignment_id: "p2", status: "failed"}]);
  const failed = next.steps.find(s => s.failed);
  if (failed && failed === next.current) assert.match(next.status, /^Needs attention: /);
  else assert.match(next.status, /^(Next|Needs attention): /);
});

test("with nothing current the card opens the last step and says the grades are certified", () => {
  const next = assignmentNext(plan("p3", []), []);
  if (!next.current) {
    assert.equal(next.status, "Grades certified");
    assert.equal(next.href, "#grading/p3?tab=export");
    assert.equal(next.label, "Open grading");
    assert.match(next.detail, /^Certified Sep 2\d\. Changed grades or later papers reopen the last step\.$/);
  }
  // Every step done: the certification is current, and the card says so with its day.
  const done = assignmentNext(plan("p5", [question(5, {status: "confirmed", approved: true})], {responses: {approved: 3}, collection: {complete: 3}}), [{assignment_id: "p5", status: "complete"}]);
  assert.equal(done.current, undefined);
  assert.equal(done.status, "Grades certified");
  assert.match(done.detail, /^Certified Sep 2\d\. /);
  // Not yet certified: the last step is next, by its own name.
  const pending = assignmentNext(plan("p6", [question(6, {status: "confirmed", approved: true})], {responses: {approved: 3}, collection: {complete: 3}, certification: {state: "none"}}), [{assignment_id: "p6", status: "complete"}]);
  assert.equal(pending.current.key, "export");
  assert.equal(pending.status, "Next: certify");
  assert.equal(pending.label, "Certify");
});

// THE CERTIFY STEP WAITS FOR A LATE SCAN (requirement, 2026-10-11).
test("every grade reviewed and a later scan still in the Inbox: the card asks for that scan, not for a certification that refuses", () => {
  const reviewed = (workflow) => plan("p11", [question(11, {status: "confirmed", approved: true})], {responses: {approved: 3}, collection: {complete: 3}, ...workflow});
  const jobs = [{assignment_id: "p11", status: "complete"}];
  const scans = [{job_id: "first", inbox_ready: true}, {job_id: "late", inbox_ready: false}];
  for (const state of ["none", "changed"]) {
    const next = assignmentNext(reviewed({certification: {state}, awaiting_review: 1, scans}), jobs);
    assert.equal(next.current.key, "export", state);
    assert.equal(next.waiting, true, state);
    assert.equal(next.status, "Next: finish the scan checks", state);
    assert.equal(next.label, "Finish the scan checks", state);
    assert.equal(next.href, "#inbox/job/late", state);
    assert.equal(next.detail, "A later scan of this assignment still waits for scan checks. Certify once its papers are graded and reviewed.", state);
    // The card's title still opens the assignment at its step, and the step keeps its name, its place and its done rule.
    assert.equal(next.open, "#grading/p11?tab=export", state);
    assert.deepEqual([next.current.label, next.current.done, next.current.status, next.steps.at(-1).key], ["Certify", false, "Waiting for a scan", "export"], state);
  }
  // With its assignment and term known, the scans waiting are listed on the assignment's Collect (I2).
  const linked = assignmentNext({...reviewed({certification: {state: "none"}, awaiting_review: 1, scans}), source_assignment_id: "a 1", term_id: "t1"}, jobs);
  assert.equal(linked.href, "#assignments/a%201/t1?area=collect&scans=waiting");
  // Several, or a summary that names no scan: said so, and the Inbox itself opens.
  const several = assignmentNext(reviewed({certification: {state: "none"}, awaiting_review: 2, scans: []}), jobs);
  assert.equal(several.href, "#inbox");
  assert.match(several.detail, /^2 later scans of this assignment still wait for scan checks\. Certify once their papers/);
  // Nothing still coming: certifying is next, as before. A current certification is not reopened by a late scan here.
  assert.equal(assignmentNext(reviewed({certification: {state: "none"}, awaiting_review: 0, scans: scans.slice(0, 1)}), jobs).status, "Next: certify");
  assert.equal(assignmentNext(reviewed({awaiting_review: 1, scans}), jobs).status, "Grades certified");
  // Still reviewing with a scan in the Inbox: the review comes first, by its own name.
  const reviewing = assignmentNext(reviewed({certification: {state: "none"}, awaiting_review: 1, scans, responses: {approved: 2, proposed: 1}, review_responses: 1}), jobs);
  assert.deepEqual([reviewing.current.key, reviewing.waiting], ["review", false]);
});

// A finished setup with no paper scanned yet (a drafted quiz not given): the card leads to the scan, not to an empty Grade step.
test("an assignment waiting for papers sends the card to the Inbox, by the scan's own state", () => {
  const ready = [question(7, {status: "confirmed", approved: true})];
  const none = assignmentNext(plan("p7", ready, {certification: {state: "none"}}), []);
  assert.equal(none.waiting, true);
  assert.equal(none.current.key, "grade");
  assert.equal(none.status, "Next: upload the scans once it is given");
  assert.equal(none.href, "#inbox?upload=1");
  assert.equal(none.label, "Upload scans");
  assert.match(none.detail, /^No papers yet\./);
  // Only the button leads to the scans: the assignment still opens at its current step.
  assert.equal(none.open, "#grading/p7?tab=jobs");
  // With its assignment and term known, Upload scans opens Collect's own upload (I1).
  assert.equal(assignmentNext({...plan("p7", ready, {certification: {state: "none"}}), source_assignment_id: "a7", term_id: "t7"}, []).href,
    "#assignments/a7/t7?area=collect&upload=1");
  // A scan of it still in the Inbox: finish that scan.
  const inbox = assignmentNext(plan("p8", ready, {certification: {state: "none"}, scans: [{job_id: "job-8", inbox_ready: false}], awaiting_review: 1}), []);
  assert.equal(inbox.waiting, true);
  assert.equal(inbox.status, "Next: finish the scan checks");
  assert.equal(inbox.href, "#inbox/job/job-8");
  assert.equal(inbox.label, "Finish the scan checks");
  assert.equal(inbox.open, "#grading/p8?tab=jobs");
  // Papers in grading (or new ones ready) are graded as before.
  const papers = assignmentNext(plan("p9", ready, {certification: {state: "none"}, new_responses: 4, scans: [{job_id: "job-9", inbox_ready: true}]}), []);
  assert.equal(papers.waiting, false);
  assert.equal(papers.href, "#grading/p9?tab=jobs");
  assert.equal(papers.label, "Grade");
});

// I2: the scan-checks notice above a step lists the assignment's waiting scans on Collect, in the canonical words.
test("the scan-checks notice links Collect's waiting scans", () => {
  const p = {...plan("p20", [], {awaiting_review: 2, scans: [{job_id: "j20", inbox_ready: false}]}), source_assignment_id: "a20", term_id: "t20"};
  const notice = inboxNoticeMarkup(p, h);
  assert.match(notice, /Waiting for scan checks on 2 scans\./);
  assert.ok(notice.includes('href="#assignments/a20/t20?area=collect&amp;scans=waiting">Finish the scan checks →</a>'));
  assert.ok(inboxNoticeMarkup({...p, source_assignment_id: ""}, h).includes('href="#inbox/job/j20"'));
  assert.equal(inboxNoticeMarkup(plan("p21", []), h), "");
});

// journey-5: whether grading starts by itself, in the Grade step's own words (autoGradeLine); nothing when there is nothing to say.
test("autoGradeLine says whether grading starts by itself", async () => {
  const ready = [question(11, {status: "confirmed", approved: true})];
  const auto = state => ({auto_grade: {enabled: true, paused: null, not_started: null, settle_seconds: 120, ...state}});
  const fresh = {...plan("p11", ready, {certification: {state: "none"}, new_responses: 4, scans: [{job_id: "j", inbox_ready: true}]}), ...auto({})};
  assert.equal(autoGradeLine(fresh), "Grading starts by itself about 2 minutes after the last batch finished or the last of these became ready, whichever is later.");
  const midBatch = {...fresh, workflow: {...fresh.workflow, pending: 30}};
  assert.equal(autoGradeLine(midBatch), "Not in the batch running now: they grade in their own batch, which starts by itself about 2 minutes after that one finishes.");
  assert.equal(autoGradeLine({...fresh, ...auto({enabled: false})}), "Automatic grading is off in Compute settings: start the batch yourself.");
  assert.match(autoGradeLine({...fresh, ...auto({paused: "cancelled"})}), /^Automatic grading is paused because the last batch was cancelled\./);
  const idle = {...plan("p12", ready, {certification: {state: "none"}, scans: [{job_id: "j", inbox_ready: true}], collection: {complete: 3}, responses: {approved: 3}}), ...auto({})};
  assert.equal(autoGradeLine(idle), "");
});
