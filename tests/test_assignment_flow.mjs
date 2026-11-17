import assert from 'node:assert/strict';
import test from 'node:test';
import {reviewNext} from '../static/grading-overview.js';
import {lateScan, preparationTasks} from '../static/grading-guide.js';
import {onwardLink} from '../static/grading-review.js';
import {stageMarks} from '../static/assignment-workspace.js';
import {collectionStudents} from '../static/assignment-collection.js';

test('ASSIGNMENT-WORKSPACE: an empty queue never certifies unfinished work', () => {
  const plan = {id: 'p', workflow: {next_tab: 'export'}};
  assert.match(reviewNext(plan, {questions: []}).href, /tab=jobs$/);
  const final = {questions: [{approved: 2}]};
  assert.match(reviewNext(plan, final).href, /tab=export$/);
  assert.match(reviewNext(plan, {...final, waiting: 1}).href, /tab=review$/);
  assert.match(reviewNext(plan, {...final, other_waiting: 1}).href, /tab=review$/);
  for (const field of ['pending', 'new_responses', 'blocked_responses', 'failed'])
    assert.match(reviewNext({...plan, workflow: {...plan.workflow, [field]: 1}}, final).href, /tab=jobs$/);
  assert.match(reviewNext({...plan, workflow: {next_tab: 'answer-key'}}, final).href, /tab=answer-key$/);
  assert.match(reviewNext({...plan, workflow: {next_tab: 'locations', new_responses: 4}}, final).href, /tab=locations$/);
  assert.match(reviewNext(plan, {questions: [{approved: 2, failed: 1}]}).href, /tab=jobs$/);
});

// THE CERTIFY STEP WAITS FOR A LATE SCAN (requirement, 2026-10-11).
test('GRADING: with everything reviewed and a later scan still in the Inbox, the Review step leads to that scan', () => {
  const final = {questions: [{approved: 2}, {approved: 2}]};
  const scans = [{job_id: 'first', inbox_ready: true}, {job_id: 'late', inbox_ready: false}];
  const plan = (workflow) => ({id: 'p', workflow: {next_tab: 'export', ...workflow}});
  for (const certification of [undefined, {state: 'none'}, {state: 'changed'}]) {
    assert.deepEqual(reviewNext(plan({awaiting_review: 1, scans, certification}), final), {title: 'No responses need review', label: 'Finish the scan checks', href: '#inbox/job/late',
      detail: '4 final responses. A later scan of this assignment still waits for scan checks: certify once its papers are graded and reviewed.'});
  }
  // Several, or a summary that names no scan: said so, and the Inbox itself opens.
  const several = reviewNext(plan({awaiting_review: 2}), final);
  assert.equal(several.href, '#inbox');
  assert.match(several.detail, /^4 final responses\. 2 later scans of this assignment still wait for scan checks: certify once their papers/);
  // Nothing still coming, or already certified: certification is the way on, as before.
  const onward = {title: 'No responses need review', detail: '4 final responses. Certify checks every student again.', label: 'Continue to Certify', href: '#grading/p?tab=export'};
  assert.deepEqual(reviewNext(plan({awaiting_review: 0, scans: scans.slice(0, 1)}), final), onward);
  assert.deepEqual(reviewNext(plan({awaiting_review: 1, scans, certification: {state: 'current'}}), final), onward);
  // What comes before it still does: a response to review, unfinished grading, nothing final.
  assert.match(reviewNext(plan({awaiting_review: 1, scans}), {...final, waiting: 1}).href, /tab=review$/);
  assert.match(reviewNext(plan({awaiting_review: 1, scans, pending: 1}), final).href, /tab=jobs$/);
  assert.match(reviewNext(plan({awaiting_review: 1, scans}), {questions: []}).href, /tab=jobs$/);
});

test('ASSIGNMENT-WORKSPACE: local recording does not mark delivery complete', () => {
  const marks = stageMarks({lifecycle: {stages: [{key: 'recorded', state: 'done'}]}, workflow: {certification: {state: 'current'}}, needs_you: []});
  assert.equal(marks.return.state, '');
});

test('ASSIGNMENT-WORKSPACE: missing paper precedes downstream preparation', () => {
  const tasks = preparationTasks({id: 'p', source_assignment_id: 'a', term_id: 't', questions: [], document_versions: []});
  assert.deepEqual(tasks, [{label: 'Add the assignment paper in Create', href: '#documents?assignment=a&term=t&step=version'}]);
});

test('GRADING: checklist points to the exact missing question without changing it', () => {
  const plan = {id: 'p', questions: [{question_id: 'q', exam_version: 'A', location_check: {status: 'confirmed'}, has_answer_key: false,
    rubric: {approved_at: 'now'}}], template_setup: {slots: [{exam_version: 'A', confirmed: true}]}, rubric_quality: {state: 'confirmed'}};
  const before = JSON.stringify(plan), tasks = preparationTasks(plan);
  assert.equal(tasks.length, 1);
  assert.match(tasks[0].label, /Version A: check answer keys/);
  assert.match(tasks[0].href, /tab=answer-key&focus=q$/);
  assert.equal(JSON.stringify(plan), before);
});

test('EXAMS: collection joins both permanent student and section IDs, preserving correction versions', () => {
  const exam = {participants: [{section_id: 's1', enrollment_id: 'e', version: 4, expectation: 'expected'},
    {section_id: 's2', enrollment_id: 'e', version: 9, expectation: 'excused'}],
    reconciliation: {students: [{section_id: 's1', enrollment_id: 'e', status: 'needs_review', issues: ['Choose submission']},
      {section_id: 's2', enrollment_id: 'e', status: 'excused', issues: []}]}};
  const rows = collectionStudents(exam);
  assert.equal(rows[0].collection.status, 'needs_review');
  assert.equal(rows[1].collection.status, 'excused');
  assert.equal(rows[0].version, 4);
  assert.equal(rows[1].version, 9);
  assert.equal(exam.participants[0].collection, undefined);
});

// One reading of the rule (`lateScan`)
// for the step graph, the Review step's way on and this button.
test('GRADING: the button at the head of All responses leads to a late scan instead of a certification that refuses', () => {
  const h = {e: value => String(value).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;')};
  const scans = [{job_id: 'first', inbox_ready: true}, {job_id: 'late/1', inbox_ready: false}];
  const plan = (workflow) => ({id: 'p', workflow});
  const certify = '<a class="button" href="#grading/p?tab=export" data-review-onward="certify">Continue to Certify</a>';
  // Nothing coming, no workflow read, or already certified: the way on to certification, whatever is left to review.
  for (const workflow of [undefined, {}, {awaiting_review: 0, scans}, {awaiting_review: 1, scans, certification: {state: 'current'}}, {review_responses: 3}])
    assert.equal(onwardLink(plan(workflow), h), certify, JSON.stringify(workflow));
  // A later scan still in the Inbox: the scan, with the reason; also while responses here still wait for review.
  for (const workflow of [{awaiting_review: 1, scans}, {awaiting_review: 1, scans, certification: {state: 'changed'}}, {awaiting_review: 1, scans, review_responses: 3}])
    assert.equal(onwardLink(plan(workflow), h), '<a class="button" href="#inbox/job/late/1" title="A later scan of this assignment still waits for scan checks: '
      + 'certify once its papers are graded and reviewed." data-review-onward="scan">Finish the scan checks</a>', JSON.stringify(workflow));
  assert.match(onwardLink(plan({awaiting_review: 2}), h), /href="#inbox" title="2 later scans of this assignment still wait for scan checks: certify once their papers/);
  // The helper itself.
  assert.equal(lateScan(undefined), null);
  assert.equal(lateScan({awaiting_review: 1, certification: {state: 'current'}}), null);
  assert.deepEqual(lateScan({awaiting_review: 1, scans}), {count: 1, href: '#inbox/job/late/1', label: 'Finish the scan checks',
    waits: 'A later scan of this assignment still waits for scan checks', then: 'certify once its papers are graded and reviewed'});
});
