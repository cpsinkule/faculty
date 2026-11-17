import {guideSteps, lateScan, shortDate, waitingScansHref, SCAN_CHECKS_LABEL} from "./grading-guide.js";

// Scan checks precede grading. Later arrivals are a notice, never a graph step; the link lists the assignment's waiting scans on
// Collect (I2, 2026-10-18).
export function inboxNoticeMarkup(plan, {e}) {
  const w = plan.workflow || {}, pending = w.awaiting_review || 0;
  if (!pending) return '';
  return `<aside class="grading-inbox-notice" data-scan-checks-notice><p><strong>Waiting for scan checks on ${e(pending)} ${pending === 1 ? 'scan' : 'scans'}.</strong> ${pending === 1 ? 'Its papers join' : 'Their papers join'} grading once the checks are finished.</p><a class="text-button" href="${e(waitingScansHref(plan, w))}">${SCAN_CHECKS_LABEL} →</a></aside>`;
}

// Papers whose version waits for the instructor (a makeup or DR-center paper has no seat to go by): no step of the graph shows them,
// so the assignment says how many and opens Assignment versions, where they are decided (2026-09-26: three Version C makeups waited
// there, unseen, while every step showed done).
export function versionNoticeMarkup(plan, {e}) {
  const count = plan.workflow?.version_decisions || 0;
  if (!count) return '';
  return `<aside class="grading-inbox-notice" data-version-decisions><p><strong>${e(count)} ${count === 1 ? 'paper needs' : 'papers need'} your version decision.</strong> ${count === 1 ? 'It is' : 'They are'} not graded until you confirm which version ${count === 1 ? 'it is' : 'each is'}.</p><a class="text-button" href="#grading/${e(plan.id)}?tab=rubrics&focus=versions">Decide versions →</a></aside>`;
}

// ASSIGNMENT-WORKSPACE: an empty review queue does not prove that the assignment is ready to certify.
export function reviewNext(plan, queue) {
  const w = plan.workflow || {}, entries = queue.questions || [];
  const finals = entries.reduce((n, row) => n + (row.approved || 0), 0);
  const setupTab = w.next_tab && !['jobs', 'review', 'export'].includes(w.next_tab) ? w.next_tab : '';
  const tab = w.version_decisions ? 'rubrics&focus=versions' : setupTab || (w.new_responses || w.pending || w.failed || w.blocked_responses
    || entries.some(row => row.not_graded || row.regrade || row.failed) ? 'jobs' : w.next_tab);
  if (tab && tab !== 'export' && tab !== 'review') return {
    title: tab === 'jobs' ? 'Grading is not finished' : 'Preparation is not finished',
    detail: w.pending ? 'Work is queued or running. It continues automatically.' : 'Finish the remaining work before certifying grades.',
    label: tab === 'jobs' ? 'Open grading progress' : 'Finish preparation', href: `#grading/${plan.id}?tab=${tab}`};
  const waiting = (queue.waiting || 0) + (queue.other_waiting || 0);
  if (waiting) return {title: 'Responses still need review', detail: `${waiting} responses need your review before certification.`,
    label: 'Review remaining responses', href: `#grading/${plan.id}?tab=review`};
  if (!finals) return {title: 'No final grades yet', detail: 'An empty review queue does not mean grading is complete.',
    label: 'Open grading progress', href: `#grading/${plan.id}?tab=jobs`};
  // THE CERTIFY STEP WAITS FOR A LATE SCAN (GRADING; requirement, 2026-10-11): everything here is reviewed and a linked scan still waits for its scan checks. Certification refuses until it is
  // through, so the line leads to that scan, in the step graph's own words; a current certification is not reopened by it.
  const late = lateScan(w, plan);
  if (late) return {title: 'No responses need review', detail: `${finals} final responses. ${late.waits}: ${late.then}.`, label: late.label, href: late.href};
  return {title: 'No responses need review', detail: `${finals} final responses. Certify checks every student again.`,
    label: 'Continue to Certify', href: `#grading/${plan.id}?tab=export`};
}

// Whether the next batch starts by itself (2026-09-28: grading batches start on their own once new work stays the same for two
// minutes). Said only where it matters: new work waiting, or a reason the worker is not starting it. The Grade step (grading-jobs.js), Home's
// "Grading did not start by itself" row and the assignment Overview's waiting note (assignment-workspace.js `gradingWait`) say it with the same words.
export function autoGradeLine(plan) {
  const auto = plan?.auto_grade, fresh = plan?.workflow?.new_responses || 0;
  if (!auto) return "";
  const paused = {cancelled: "the last batch was cancelled", reset: "a batch was reset since"}[auto.paused];
  if (!auto.enabled) return fresh ? "Automatic grading is off in Compute settings: start the batch yourself." : "";
  if (paused) return `Automatic grading is paused because ${paused}. Starting a batch yourself resumes it.`;
  if (auto.not_started) return `A batch could not start by itself: ${auto.not_started.reason}`;
  if (!fresh) return "";
  // The worker never adds to a running batch, and its settle clock starts over when that batch ends (or the worker restarts):
  // responses confirmed mid-batch wait for their own batch (requirement, 2026-10-02: they looked lost while the batch ran).
  const minutes = Math.round((auto.settle_seconds || 120) / 60);
  if (plan?.workflow?.pending) return `Not in the batch running now: ${fresh === 1 ? "it grades in its" : "they grade in their"} own batch, which starts by itself about ${minutes} minutes after that one finishes.`;
  return `Grading starts by itself about ${minutes} minutes after the last batch finished or the last of these became ready, whichever is later.`;
}

// The term gradebook's column for this assignment (gradebook/README.md, "Integration"): the plan's term and course-wide
// assignment, never the plan itself. The gradebook focuses that column, or offers to add one; it reads grading, never changes it.
export function gradebookLink(plan, {e}) {
  if (!plan?.term_id || !plan?.source_assignment_id) return '';
  return `<a data-grading-gradebook href="#gradebook/${e(encodeURIComponent(plan.term_id))}?assignment=${e(encodeURIComponent(plan.source_assignment_id))}">Gradebook</a>`;
}

// One assignment's next step: the graph's steps, the current one, a status sentence, its detail and where its button goes. Pure:
// derived from the plan and its jobs, nothing decided (shared by Scan records and Home).
// `waiting`: no paper of it has reached grading yet, so the button leads to the scans (Upload scans, or the scans still in their
// scan checks on Collect), not to an empty Grade step; or every grade is reviewed and a later scan is still in its scan checks, so
// it leads there, not to a certification that refuses. `open` is the current step's own page.
export function assignmentNext(plan, jobs) {
  const steps = guideSteps(plan, jobs);
  const current = steps.find(s => s.current);
  const waiting = Boolean(current?.waiting);
  const open = current?.destination || `#grading/${plan.id}?tab=export`;
  const href = waiting ? current.action.href : open;
  const label = waiting ? current.action.label : current?.label || "Open grading";
  // Every step done means a current certification (the sixth step's rule); its day is the certification's.
  const day = shortDate(plan.workflow?.certification?.created_at);
  const status = current ? `${current.failed ? "Needs attention" : "Next"}: ${current.title.charAt(0).toLowerCase() + current.title.slice(1)}` : "Grades certified";
  const detail = current?.detail || `${day ? `Certified ${day}. ` : ""}Changed grades or later papers reopen the last step.`;
  return {steps, current, status, detail, href, label, waiting, open};
}

