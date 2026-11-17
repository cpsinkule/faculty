import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { guideSteps, inPrintedOrder, locationState, workflowTab, workflowMarkup, pipelineMarkup, rubricProgress, finalizationProgress, workflowProgress, stepHeader, stepNext } from "../static/grading-guide.js";

const template = {id: "t1", label: "Blank exam", page_count: 12};
function question(number, {status = "needs_confirmation", chosen = true, approved = false, rubric = true, label = "", key = [{label: "", accept: ["6"]}]} = {}) {
  return {question_id: `id-${number}`, question_key: `q${number}`, content: {title: `Question ${number}`}, answer: {key},
    rubric: rubric ? {approved_at: approved ? "2026-09-19" : null} : null,
    location_check: {status, label, context: {printed_number: String(number), template: chosen ? template : null, templates: chosen ? [template] : [template, {...template, id: "t2"}]}}};
}
const ready = {print_jobs_attached: 4, print_jobs_available: 0, scans_awaiting_review: 0, reviewed_papers: 325};
// The rubric finalization is mandatory; fixtures start with it confirmed and the tests about it say otherwise.
const finalized = {enabled: true, state: 'confirmed'};
const plan = (questions, templates = [template], papers = ready) => ({id: "plan", event_id: "event", questions, templates, papers, rubric_quality: finalized});
const byKey = steps => Object.fromEntries(steps.map(step => [step.key, step]));
const h = {e: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")};

const withProgress = (changes = {}) => ({...plan([question(18, {status: "confirmed", approved: true})]), workflow: {
  scans: [], awaiting_review: 0, responses: {approved: 1}, collection: {complete: 1},
  new_responses: 0, pending: 0, failed: 0, blocked_responses: 0, review_responses: 0, export: {current: true},
  certification: {state: "current", created_at: "2026-09-26T15:00:00+00:00", gradebook: {title: "Test 1"}}, ...changes}});

test('saved processing and partial reviews have independent progress, including later arrivals', () => {
  const value = withProgress({responses: {approved: 2, manual: 1, auto_accepted: 1, grading_review: 2, pending: 2},
    pending: 2, review_responses: 2, new_responses: 1, blocked_responses: 1});
  let progress = workflowProgress(value);
  assert.equal(progress.grade.completed, 6);
  assert.equal(progress.grade.total, 9);
  assert.equal(progress.grade.percent, 66);
  assert.equal(progress.review.completed, 4);
  assert.equal(progress.review.percent, 44);
  // The server also accounts for active regrades of responses with older results.
  value.workflow.progress = {grade: {completed: 3, total: 10}, review: {completed: 4, total: 10}};
  progress = workflowProgress(value);
  assert.equal(progress.grade.percent, 30);
  assert.equal(progress.review.percent, 40);
  value.workflow.progress.grade = {completed: 999, total: 1000};
  assert.equal(workflowProgress(value).grade.percent, 99);
});

test('setup counts uploads, templates, keys and the finalization, and shares template page work across questions', () => {
  const value = plan([question(1, {approved: true, status: 'confirmed'}), question(2, {status: 'running', key: []})]);
  value.rubric_quality = {enabled: true, state: 'needed'};
  value.template_setup = {slots: [{template_id: 't1', confirmed: true}, {template_id: 't2', confirmed: false}]};
  value.questions[0].location_check.reading_progress = {template_id: 't1', completed: 2, total: 2};
  value.questions[1].location_check.reading_progress = {template_id: 't1', completed: 1, total: 2};
  let progress = workflowProgress(value);
  assert.equal(progress.template, undefined); // Templates belong to the first step.
  assert.equal(progress.rubrics.completed, 5); // Two uploads, both references, one confirmation.
  assert.equal(progress.rubrics.total, 6);
  assert.equal(progress.locations.total, 6); // Two shared pages, two checks, two confirmations.
  assert.equal(progress.locations.completed, 3);
  assert.equal(progress.locations.percent, 50);
  assert.equal(progress.key.completed, 2); // One key and one approval of two; the rubric check has not run.
  assert.equal(progress.key.total, 6);
  value.rubric_quality = {enabled: true, state: 'needs_review'};
  progress = workflowProgress(value);
  assert.equal(progress.key.completed, 3); // The check is done; its confirmation is still owed.
  assert.equal(progress.rubrics.completed, 5);
  const summary = {questions: [], workflow: {setup: value}};
  assert.deepEqual(workflowProgress(summary).locations, progress.locations);
});

test('Inbox arrivals stay outside the graph and do not reopen completed grading', () => {
  for (const changes of [
    {awaiting_review: 1}, {blocked_responses: 2},
    {scans: [{inspection_status: 'failed', inbox_ready: false}], awaiting_review: 1},
    {scans: [{inspection_status: 'running', inbox_ready: false}], collection: {complete: 3, partial: 1}},
  ]) {
    const value = withProgress(changes), steps = guideSteps(value);
    assert.equal(steps.length, 6);
    assert.equal(steps.some(s => s.key === 'papers'), false);
    assert.ok(steps.every(s => s.done));
    assert.equal(workflowProgress(value).papers, undefined);
    assert.equal(workflowProgress(value).grade.percent, 100);
    assert.equal(workflowProgress(value).grade.total, 1);
    assert.equal(workflowTab('overview', value, []), 'export');
    assert.doesNotMatch(pipelineMarkup(steps, h), /Scan review|tab=papers/);
  }
});

// DESIGN-LANGUAGE §4 (requirement, 2026-10-03): each step prints the word for its state under its label (or its progress
// fraction); there are still no separate badges, dots, rings or tick marks.
test('progress counts and state words stay in the step, without separate status badges', () => {
  const value = withProgress({responses: {approved: 1, grading_review: 3}, review_responses: 3});
  const markup = pipelineMarkup(guideSteps(value), h);
  assert.match(markup, /aria-label="Review progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="25"/);
  assert.match(markup, /25% · 1\/4/);
  assert.match(workflowMarkup(value, [], 'review', h), /aria-valuenow="25"/);
  assert.doesNotMatch(markup, /grading-pipeline-dot|grading-progress-ring|grading-pipeline-cube|grading-pipeline-connector/);
  assert.match(markup, /<span class="grading-pipeline-number" aria-hidden="true">1<\/span><strong>Rubrics &amp; Templates<\/strong><small class="grading-pipeline-status">Done<\/small>/);
  // A step with progress shows its fraction instead of a state word.
  assert.doesNotMatch(markup, /data-pipeline-step="review">(?:(?!<\/li>)[\s\S])*grading-pipeline-status/);
  for (const failed of [false, true]) {
    const grade = byKey(guideSteps(withProgress({failed: Number(failed)}))).grade;
    const html = pipelineMarkup([grade], h);
    assert.doesNotMatch(html, /role="progressbar"|grading-progress-ring/);
    assert.ok(html.includes(failed ? 'aria-label="Grade: Failed' : 'aria-label="Grade: Done'));
    assert.doesNotMatch(html, /[×✓]/);
    assert.match(html, failed ? /<small class="grading-pipeline-status">Failed<\/small>/ : /<small class="grading-pipeline-status">Done<\/small>/);
  }
  const empty = workflowProgress(plan([], []));
  assert.equal(empty.rubrics, null);
  assert.equal(empty.grade, null);
  assert.doesNotMatch(pipelineMarkup(guideSteps(plan([], [], {})), h), /NaN|Infinity|role="progressbar"/);
});

test('untouched steps stay neutral; queued work and review needs count as started even at zero percent', () => {
  const empty = {...plan([], []), rubric_quality: {state: 'needed'}};
  const steps = guideSteps(empty);
  assert.ok(steps.every(s => !s.started));
  assert.equal(steps[0].current, true); // Being the next task does not start it.
  const markup = pipelineMarkup(steps, h);
  assert.equal((markup.match(/class="is-idle"/g) || []).length, 6);
  assert.match(markup, /aria-label="Rubrics &amp; Templates: Not started/);
  assert.equal((markup.match(/<small class="grading-pipeline-status">Not started<\/small>/g) || []).length, 6);
  const waiting = withProgress({responses: {}, new_responses: 8, export: {current: false}, certification: {state: 'none'}});
  let state = byKey(guideSteps(waiting));
  assert.equal(state.grade.started, false);
  assert.equal(state.review.started, false);
  assert.equal(state.export.started, false);
  waiting.workflow.pending = 8;
  state = byKey(guideSteps(waiting));
  assert.equal(state.grade.progress.percent, 0);
  assert.equal(state.grade.started, true);
  assert.equal(state.review.started, false);
  waiting.workflow.review_responses = 8;
  waiting.workflow.certification = {state: 'changed', created_at: '2026-09-22', changed: ['new_work']};
  state = byKey(guideSteps(waiting));
  assert.equal(state.review.started, true);
  assert.equal(state.export.started, true); // A certification the grades moved away from is unfinished, not untouched.
  assert.equal(state.export.done, false);
  const reading = {...plan([question(1, {status: 'running', key: []})]), rubric_quality: {state: 'running'}};
  state = byKey(guideSteps(reading));
  assert.equal(state.locations.progress.percent, 0);
  assert.equal(state.locations.started, true);
  assert.equal(state.key.progress.percent, 0);
  assert.equal(state.key.started, true);
  assert.equal(byKey(guideSteps({...empty, preparation: {pending: 1}})).rubrics.started, true);
});

test("current papers and decisions complete all six steps, with destinations retained", () => {
  const steps = guideSteps(withProgress());
  assert.equal(steps.length, 6);
  assert.deepEqual(steps.map(s => s.label), ['Rubrics & Templates', 'Answer areas', 'Key & Rubric Finalization', 'Grade', 'Review', 'Certify']);
  assert.ok(steps.every(s => s.done && s.destination));
  assert.ok(steps.every(s => !s.current));
});

test("later arrivals, queued work and stale responses reopen grading despite an older completed batch", () => {
  for (const changes of [{new_responses: 1}, {pending: 1}, {responses: {stale: 1}}]) {
    const steps = byKey(guideSteps(withProgress(changes), [{status: "complete"}]));
    assert.equal(steps.grade.done, false);
    assert.equal(steps.review.done, false);
    assert.equal(steps.export.done, false);
    assert.notEqual(steps.grade.state, "failed");
  }
});

test("trial acceptance requires review and the last step requires a current certification", () => {
  const trial = byKey(guideSteps(withProgress({responses: {trial_accepted: 1}, review_responses: 1})));
  assert.equal(trial.grade.done, true);
  assert.equal(trial.review.done, false);
  assert.equal(trial.export.done, false);
  // A retained export no longer completes the step; only a certification does (requirement, 2026-09-26).
  for (const certification of [undefined, {state: "none"}]) {
    const pending = withProgress({export: {current: true}, certification});
    const steps = byKey(guideSteps(pending));
    assert.equal(steps.review.done, true);
    assert.equal(steps.export.done, false);
    assert.equal(steps.export.status, "Pending");
    assert.equal(steps.export.started, true, "a reviewed plan has begun its last step");
    assert.equal(steps.export.current, true);
    assert.equal(steps.export.action.label, "Certify grades");
    assert.equal(steps.export.action.href, "#grading/plan?tab=export");
    assert.equal(steps.export.title, "Certify");
    assert.match(steps.export.detail, /gradebook column you choose and emails nobody; with its Gradescope box ticked, Faculty then sends them to Gradescope and publishes them\./);
    assert.equal(workflowTab("overview", pending, []), "export");
    assert.equal(workflowProgress(pending).export.completed, 0);
  }
  const changed = byKey(guideSteps(withProgress({certification: {state: "changed", created_at: "2026-09-20T15:00:00+00:00", changed: ["grades"]}})));
  assert.equal(changed.export.done, false);
  assert.equal(changed.export.status, "Changed since certification");
  assert.equal(changed.export.action.label, "Certify again");
  assert.match(changed.export.detail, /^Grades changed since the certification of Sep 2\d\. The gradebook keeps the certified values until you certify again\.$/);
  const current = byKey(guideSteps(withProgress()));
  assert.equal(current.export.done, true);
  assert.equal(current.export.action, null);
  assert.match(current.export.detail, /^Certified Sep 2\d and recorded in the gradebook column Test 1\.$/);
  // Everything done still opens the last step.
  assert.equal(workflowTab("overview", withProgress(), []), "export");
  assert.equal(workflowProgress(withProgress()).export.percent, 100);
});

test("failed work is red; review and running work stay pending, with usable links", async () => {
  const failure = guideSteps(withProgress({failed: 1}));
  assert.equal(byKey(failure).grade.state, "failed");
  assert.equal(byKey(failure).grade.current, true);
  const location = withProgress();
  location.questions[0].location_check.status = "failed";
  assert.equal(byKey(guideSteps(location)).locations.state, "failed");
  assert.match(byKey(guideSteps(location)).locations.detail, /^1 answer-area check failed\. Open the answer areas/);
  location.questions[0].location_check.status = "needs_review";
  assert.notEqual(byKey(guideSteps(location)).locations.state, "failed");
  const preparing = withProgress();
  preparing.preparation = {pending: 0, failed: 1};
  assert.equal(byKey(guideSteps(preparing)).rubrics.state, "failed");
  preparing.preparation = {pending: 1, failed: 0};
  assert.equal(byKey(guideSteps(preparing)).rubrics.done, false);
  assert.notEqual(byKey(guideSteps(preparing)).rubrics.state, "failed");
  const markup = pipelineMarkup(failure, h);
  assert.match(markup, /class="is-failed" data-pipeline-step="grade"/);
  assert.match(markup, /aria-label="Grade: Failed/);
  assert.doesNotMatch(markup, /[×✓]/);
  assert.match(markup, /data-pipeline-step="grade">(?:(?!<\/li>)[\s\S])*<small class="grading-pipeline-status">Failed<\/small>/);
  assert.match(markup, /class="is-done" data-pipeline-step="rubrics"/);
  assert.equal((markup.match(/<li /g) || []).length, 6);
});

test("questions appear in printed order, not upload order", () => {
  assert.deepEqual(inPrintedOrder([question(20), question(18), question(9)]).map(q => q.question_key), ["q9", "q18", "q20"]);
});

test("an empty assessment starts with uploading rubrics", () => {
  const steps = byKey(guideSteps(plan([], [])));
  assert.equal(steps.rubrics.state, "current");
  assert.equal(steps.rubrics.action.label, "Upload rubrics");
  assert.equal(steps.grade.state, "waiting");
  assert.equal(steps.review.action, null);
});

test("uploaded rubrics without a chosen blank exam keep the first step open at its templates", () => {
  const steps = byKey(guideSteps(plan([question(20, {status: "needs_review", chosen: false}), question(18, {status: "needs_review", chosen: false})], [template, {...template, id: "t2"}])));
  assert.equal(steps.rubrics.state, "current");
  assert.equal(steps.rubrics.status, "Needs templates");
  assert.equal(steps.rubrics.action.label, "Review assignment templates");
  assert.match(steps.rubrics.action.href, /tab=rubrics&focus=templates$/);
  assert.equal(steps.template, undefined);
  assert.equal(locationState(question(18, {status: "needs_review", chosen: false})).label, "Choose the assignment template");
  // Nothing can be confirmed before a blank exam is chosen.
  assert.equal(steps.locations.state, "waiting");
});

test("the blank exam is read in the background once the first step is complete", () => {
  const reading = {status: "running", label: "Reading the template and checking printed question totals: 5 of 12 pages read…"};
  const steps = byKey(guideSteps(plan([question(18, reading), question(19, reading)])));
  assert.equal(steps.rubrics.state, "done");
  assert.match(steps.locations.detail, /5 of 12 pages read/);
  assert.equal(steps.locations.status, "In progress");
});

test("the next location to review follows printed order and grading opens per ready question", () => {
  const steps = byKey(guideSteps(plan([question(20), question(18, {status: "confirmed", approved: true}), question(19)])));
  assert.equal(steps.locations.state, "current");  // Rubrics and templates are in: the answer areas are the next task.
  assert.equal(steps.locations.action.label, "Review Question 19");
  assert.match(steps.locations.detail, /^1 of 3 confirmed/);
  assert.equal(steps.grade.state, "open");
  assert.match(steps.grade.detail, /1 of 3 questions are ready/);
});

test("finished grading opens review", () => {
  const gradable = [question(18, {status: "confirmed", approved: true})];
  const steps = byKey(guideSteps(plan(gradable), [{status: "review_ready"}]));
  assert.deepEqual([steps.locations.state, steps.rubrics.state, steps.key.state, steps.grade.state, steps.review.state], ["done", "done", "done", "done", "current"]);
  assert.equal(workflowTab("overview", plan(gradable), [{status: "review_ready"}]), "review");
});

test('scan records stay accessible as a utility page without a numbered workflow step', () => {
  const value = withProgress();
  assert.equal(workflowTab('papers', value, []), 'papers');
  const markup = workflowMarkup(value, [], 'papers', h);
  assert.doesNotMatch(markup, /aria-current="step"|grading-workflow-position|tab=papers/);
  assert.equal((markup.match(/data-pipeline-step=/g) || []).length, 6);
});

test("the answer key is checked before grading, and its text format round-trips", async () => {
  const steps = byKey(guideSteps(plan([question(18, {status: "confirmed", approved: true, key: []}), question(19, {status: "confirmed", approved: true})])));
  assert.equal(steps.key.state, "current");
  assert.match(steps.key.detail, /^1 of 2 questions have a key/);
  assert.match(steps.key.action.href, /tab=answer-key$/);
  const groups = await readFile(new URL("../static/grading-groups.js", import.meta.url), "utf8");
  const standalone = "const inPrintedOrder = v => v, versionTitle = q => q.content.title;\n" + groups.replace(/^import .*$/gm, "");
  const { keyText, parseKey, splitAnswers } = await import(`data:text/javascript;base64,${Buffer.from(standalone).toString("base64")}`);
  const key = [{label: "a", accept: ["3x2", "3 x 2"]}, {label: "b", accept: ["not possible"]}];
  assert.equal(keyText(key), "a: 3x2 | 3 x 2\nb: not possible");
  assert.deepEqual(parseKey(keyText(key)), key);
  // An unlabelled answer keeps its own "=" and ":" and is never mistaken for a label.
  assert.deepEqual(parseKey("C(x)=21.5x+8829 | y=21.5x+8829\n\n3:2"), [{label: "", accept: ["C(x)=21.5x+8829", "y=21.5x+8829"]}, {label: "", accept: ["3:2"]}]);
  // A "|" inside braces, brackets, parentheses, after a backslash or in \left| … \right| is part of the answer (2026-09-27: an augmented
  // matrix's column bar split a Quiz 2 key into fragments no answer could match).
  const matrix = "\\left[\\begin{array}{ccc|c}1&1&1&9000\\\\0.04&0.06&0.08&550\\end{array}\\right]";
  assert.deepEqual(splitAnswers(`x+y+z=9000 | ${matrix}`), ["x+y+z=9000", matrix]);
  assert.deepEqual(parseKey(keyText([{label: "M", accept: [matrix, "x=3"]}])), [{label: "M", accept: [matrix, "x=3"]}]);
  assert.deepEqual(splitAnswers("\\left|x-3\\right| = 2 | (|x|) | a \\| b"), ["\\left|x-3\\right| = 2", "(|x|)", "a \\| b"]);
  assert.deepEqual(splitAnswers("x \\rightarrow 3 | 4"), ["x \\rightarrow 3", "4"]);
});

test("an exam with versions shows each version's progress and says plainly what one lacks", () => {
  const versioned = (number, letter, options) => ({...question(number, options), question_id: `id-${number}-${letter}`, question_key: `q${number}${letter === "A" ? "" : `-${letter}`}`, exam_version: letter});
  const both = {letters: ["A", "B"], graded: ["A"], seated: ["A", "B"], blanks: {A: {template_id: "t1", label: "Version A blank"}}};
  const onlyA = {...plan([versioned(19, "A", {status: "confirmed", approved: true}), versioned(18, "A", {status: "confirmed", approved: true})]), exam_versions: both};
  const steps = byKey(guideSteps(onlyA));
  assert.equal(steps.rubrics.done, false);
  // The first step names what it needs once: the answer key belongs to step 3, which says so itself (create-9).
  assert.match(steps.rubrics.detail, /Version A: 2 rubrics, 2 located, 2 approved, 2 with a key\. Version B has no rubrics and blank assignment yet\./);
  assert.equal(steps.rubrics.detail.match(/blank assignment/g).length, 1);
  assert.match(steps.key.detail, /Version B has no answer key yet\./);
  // Questions imported before versions existed keep the first step open until the instructor says what they describe.
  const loose = byKey(guideSteps({...plan([question(18, {status: "confirmed", approved: true})]), exam_versions: {...both, graded: []}}));
  assert.match(loose.rubrics.detail, /1 question still needs an assignment version/);
  assert.equal(loose.rubrics.done, false);
  // Each version's questions stay together, in printed order.
  assert.deepEqual(inPrintedOrder([versioned(18, "B"), versioned(19, "A"), versioned(18, "A")]).map(q => q.question_key), ["q18", "q19", "q18-B"]);
  const complete = {...plan([versioned(18, "A", {status: "confirmed", approved: true}), versioned(18, "B", {status: "confirmed", approved: true})]),
    exam_versions: {...both, graded: ["A", "B"], blanks: {...both.blanks, B: {template_id: "t2", label: "Version B blank"}}}};
  assert.equal(byKey(guideSteps(complete)).rubrics.done, true);
});

test("workflow keeps old links and lets instructors revisit every stage", () => {
  const setup = plan([question(18)]);
  assert.equal(workflowTab("assignments", setup, []), "rubrics");
  assert.equal(workflowTab("overview", setup, []), "locations");
  assert.equal(workflowTab("overview", plan([question(18, {rubric: false})]), []), "rubrics");
  for (const tab of ["rubrics", "locations", "answer-key", "papers", "jobs", "review", "export", "settings"]) {
    assert.equal(workflowTab(tab, setup, []), tab);
  }
  const markup = workflowMarkup(setup, [], "review", h);
  assert.equal((markup.match(/aria-current="step"/g) || []).length, 1);
  assert.match(markup, /href="#grading\/plan\?tab=review" aria-current="step"/);
  assert.match(markup, /<strong>Rubrics &amp; Templates<\/strong>/);
  // Older links open the step that now holds what they named.
  for (const legacy of ['approve', 'templates']) assert.equal(workflowTab(legacy, setup, []), 'rubrics');
  assert.equal(workflowTab('rubric-quality', setup, []), 'answer-key');
  assert.doesNotMatch(markup, /disabled/);
});

test('the first step waits for each rubric to be confirmed for the rubric reviewer', () => {
  // The plan read carries the finalization's readiness; the compact workflow.setup carries review_ready per question (2026-09-24).
  const full = plan([question(18), question(19)]);
  full.rubric_quality = {...full.rubric_quality, readiness: {'id-18': {ready: true}, 'id-19': {ready: false}}};
  let progress = rubricProgress(full);
  assert.deepEqual([progress.reviewConfirmed, progress.reviewReady, progress.done, progress.status], [1, false, false, 'Needs review confirmation']);
  assert.match(progress.detail, /1 of 2 confirmed for rubric review/);
  const step = byKey(guideSteps(full)).rubrics;
  assert.equal(step.done, false);
  assert.deepEqual(step.action, {label: 'Confirm rubrics for review', href: '#grading/plan?tab=rubrics'});
  const index = plan([{...question(18), review_ready: true}, {...question(19), review_ready: false}]);
  assert.equal(rubricProgress(index).reviewConfirmed, 1);
  full.rubric_quality.readiness['id-19'].ready = true;
  assert.equal(rubricProgress(full).done, true);
  // Data from before the confirmation says nothing about it and holds nothing back.
  assert.equal(rubricProgress(plan([question(18)])).reviewReady, true);
});

test('the first step needs every upload and template; nothing is approved there', () => {
  const draft = plan([question(18)]);
  assert.equal(rubricProgress(draft).done, true); // An unapproved rubric does not hold the first step open.
  assert.equal(byKey(guideSteps(draft)).rubrics.done, true);
  const waiting = plan([question(18, {status: 'waiting_templates'})]);
  waiting.template_setup = {confirmed: false, slots: [{template_id: 't1'}]};
  let steps = guideSteps(waiting);
  assert.deepEqual(steps.map(s => s.key), ['rubrics', 'locations', 'key', 'grade', 'review', 'export']);
  assert.equal(steps[0].current, true);
  assert.equal(steps[0].status, 'Needs templates');
  assert.match(steps[0].destination, /tab=rubrics$/);
  assert.equal(workflowTab('overview', waiting, []), 'rubrics');
  waiting.template_setup.confirmed = true;
  assert.equal(byKey(guideSteps(waiting)).rubrics.done, true);
  assert.equal(byKey(guideSteps({...draft, questions: []})).rubrics.done, false);
});

test('the rubric finalization is mandatory: step three waits for the key, the check and its confirmation', () => {
  const base = plan([question(18, {status: 'confirmed', approved: true})]);
  assert.equal(byKey(guideSteps(base)).key.done, true);
  for (const state of ['missing', 'needed', 'pending', 'running', 'failed', 'needs_review', undefined]) {
    const value = {...base, rubric_quality: state ? {enabled: true, state} : undefined};
    const steps = guideSteps(value), step = steps[2];
    assert.equal(steps.length, 6);
    assert.equal(step.key, 'key');
    assert.equal(step.done, false);
    assert.equal(step.current, true);
    assert.equal(step.failed, state === 'failed');
    assert.match(step.destination, /tab=answer-key$/);
    assert.match(step.action.href, /tab=answer-key&open=quality$/);
    // Nothing is offered for grading before the rubrics are finalized, whatever was approved earlier.
    assert.equal(byKey(steps).grade.action, null);
    assert.equal(workflowTab('overview', value, []), 'answer-key');
  }
  assert.equal(byKey(guideSteps({...base, rubric_quality: {enabled: true, state: 'needs_review'}})).key.action.label, 'Review the rubric check');
  // A confirmation approves every rubric, so an unapproved one means the confirmation is for other revisions.
  const unapproved = plan([question(18, {status: 'confirmed'})]);
  assert.equal(finalizationProgress(unapproved).status, 'Needs approval');
  assert.equal(byKey(guideSteps(unapproved)).key.done, false);
  // The key comes first within the step.
  const keyless = {...plan([question(18, {status: 'confirmed', key: []})]), rubric_quality: {enabled: true, state: 'needed'}};
  assert.match(byKey(guideSteps(keyless)).key.action.href, /tab=answer-key$/);
  assert.equal(finalizationProgress(keyless).status, 'Needs answer key');
});


test('tile and page use identical workflow steps, labels and links with exact active tasks', () => {
  for (const state of ['confirmed', 'needs_review']) {
    const value = {...withProgress(), rubric_quality: {enabled: true, state}};
    const steps = guideSteps(value);
    const tile = pipelineMarkup(steps, h);
    const parts = markup => ({
      keys: [...markup.matchAll(/data-pipeline-step="([^"]+)"/g)].map(m => m[1]),
      links: [...markup.matchAll(/href="([^"]+)"/g)].map(m => m[1]),
      labels: [...markup.matchAll(/<\/svg><strong>([^<]+)<\/strong>/g)].map(m => m[1]),
    });
    assert.equal(new Set(steps.map(s => s.destination)).size, steps.length);
    assert.equal(steps[0].label, 'Rubrics & Templates');
    for (const step of steps) {
      const markup = workflowMarkup(value, [], step.tab, h);
      assert.deepEqual(parts(markup), parts(tile));
      assert.equal((markup.match(/aria-current="step"/g) || []).length, 1);
      assert.ok(markup.includes(`href="${step.destination}" aria-current="step"`));
      assert.equal(workflowTab(step.tab, value, []), step.tab);
    }
  }
});

test('papers waiting for a version decision keep the first step and grading unfinished and point to Decide versions', () => {
  // requirement, 2026-09-26: three Version C makeups waited for a version decision "but all of the stages still showed green".
  const value = withProgress({version_decisions: 3, decision_responses: 21});
  const steps = byKey(guideSteps(value, [{status: 'complete'}]));
  assert.equal(steps.rubrics.done, false);
  assert.equal(steps.rubrics.state, 'current');
  assert.equal(steps.rubrics.status, 'Needs your decision');
  assert.deepEqual(steps.rubrics.action, {label: 'Decide versions', href: '#grading/plan?tab=rubrics&focus=versions'});
  assert.match(steps.rubrics.detail, /3 papers need your version decision before grading\./);
  for (const key of ['grade', 'review', 'export']) assert.equal(steps[key].done, false, key);
  assert.match(steps.grade.detail, /3 papers need your version decision\./);
  assert.equal(steps.locations.done, true, 'Steps with nothing to do for them stay done.');
  assert.equal(steps.key.done, true);
  const progress = workflowProgress(value);
  assert.ok(progress.rubrics.percent < 100);
  assert.equal(workflowTab('overview', value, [{status: 'complete'}]), 'rubrics');
  // Once decided, everything is done again.
  assert.ok(guideSteps(withProgress({version_decisions: 0}), [{status: 'complete'}]).every(s => s.done));
});


// Setup steps 1-3 share one goal-first header (2026-09-26 overhaul): the stage's name, one sentence, ONE action on the right.
test('a setup step header names the stage, says one sentence and offers one action', () => {
  const open = guideSteps(plan([question(18, {status: 'needs_confirmation', approved: false})]));
  const header = stepHeader(open, 'locations', h);
  assert.match(header, /<h3>Answer areas<\/h3>/);
  assert.ok(header.includes(`<p data-step-lead>${h.e(byKey(open).locations.detail)}</p>`), 'the step detail is the default sentence');
  assert.equal((header.match(/class="button primary"/g) || []).length, 1);
  assert.match(header, /data-step-action href="#grading\/plan\?tab=locations&amp;focus=id-18">Review Question 18</);
  assert.doesNotMatch(header, /class="next-step"/, 'an unfinished step shows no callout');
  // A lead and the page's own actions replace the defaults; '' leaves the action area empty.
  const custom = stepHeader(open, 'rubrics', h, {lead: 'One sentence.', actions: '', body: '<p data-extra>more</p>'});
  assert.match(custom, /<p data-step-lead>One sentence\.<\/p><p data-extra>more<\/p>/);
  assert.doesNotMatch(custom, /button/);
  assert.equal(stepHeader(open, 'missing', h), '');
});

test('a finished step continues to the next stage, and points to the current step when that is elsewhere', () => {
  // Everything done up to review: the finished first step continues to Answer areas and names Review as the next step.
  const value = withProgress({review_responses: 2, responses: {approved: 1, grading_review: 2}});
  const steps = guideSteps(value, [{status: 'complete'}]);
  assert.equal(steps.find(s => s.current).key, 'review');
  const {following, current, elsewhere} = stepNext(steps, 'rubrics');
  assert.deepEqual([following.key, current.key, elsewhere], ['locations', 'review', true]);
  const header = stepHeader(steps, 'rubrics', h);
  // The callout's headline is the current step's own action and its state, never the stage's fixed goal (review fix GS-D2).
  assert.match(header, /^<aside class="next-step" aria-label="Next step"><p class="eyebrow">Next step · Review<\/p><strong class="next-step-title">Review grades<\/strong><p class="next-step-detail">2 responses to review · 1 reviewed by you · 0 finalized automatically\.<\/p>/);
  assert.match(header, /<a class="button primary" href="#grading\/plan\?tab=review">Open Review →<\/a><\/aside>/);
  // Under the callout the header's Continue is secondary: one cream button per region.
  assert.match(header, /<a class="button" data-step-continue href="#grading\/plan\?tab=locations">Continue to Answer areas →<\/a>/);
  assert.equal((header.match(/button primary/g) || []).length, 1);
  // The step right before the current one needs no callout: its Continue already says where to go.
  const grade = stepHeader(steps, 'grade', h);
  assert.doesNotMatch(grade, /next-step/);
  assert.match(grade, /<a class="button primary" data-step-continue href="#grading\/plan\?tab=review">Continue to Review →<\/a>/);
  // The step being worked on names no callout and continues nowhere.
  assert.deepEqual(stepNext(steps, 'review').elsewhere, false);
  // The graph's steps are unchanged by the header: six stages, same labels, order and destinations.
  assert.deepEqual(steps.map(s => [s.label, s.destination]), guideSteps(value, [{status: 'complete'}]).map(s => [s.label, s.destination]));
});

test('a Next step callout on a later finished step names what step 1 still needs, not its fixed goal', () => {
  // Every rubric uploaded and every area confirmed, but no rubric confirmed for review: step 1 is current again.
  const value = plan([question(18, {status: 'confirmed', approved: false}), question(19, {status: 'confirmed', approved: false})]);
  value.rubric_quality = {...value.rubric_quality, readiness: {'id-18': {ready: false}, 'id-19': {ready: false}}};
  const steps = guideSteps(value);
  const first = byKey(steps).rubrics;
  assert.equal(first.current, true);
  assert.equal(first.action.label, 'Confirm rubrics for review');
  const header = stepHeader(steps, 'locations', h);
  assert.match(header, /<strong class="next-step-title">Confirm rubrics for review<\/strong>/);
  assert.doesNotMatch(header, /Upload the rubrics and the assignment templates/);
  assert.ok(header.includes(`<p class="next-step-detail">${h.e(first.detail.split('. ')[0])}.`), 'the state sentence follows');
  assert.match(header, />Open Rubrics &amp; Templates →<\/a><\/aside>/);
});

test("an empty first step says how to begin in one sentence, and a plan without its paper starts with the PDF", () => {
  const empty = byKey(guideSteps(plan([], [])));
  assert.equal(empty.rubrics.detail, "Upload the rubric CSVs, or draft the rubrics from each version’s blank assignment.");
  assert.doesNotMatch(empty.rubrics.detail, /blank exam|scanner/);
  // No registered paper (no PDF version): rubrics and blank templates attach to its versions, so the PDF comes first (create-4).
  const paperless = byKey(guideSteps({...plan([], []), source_assignment_id: "as 1", document_versions: []}));
  assert.deepEqual(paperless.rubrics.action, {label: "Add the PDF", href: "#documents?assignment=as%201&step=version"});
  assert.match(paperless.rubrics.detail, /^Add the paper in Create first/);
  const registered = byKey(guideSteps({...plan([], []), source_assignment_id: "as 1", document_versions: [{id: "v1"}]}));
  assert.equal(registered.rubrics.action.label, "Upload rubrics");
  // The term's own projection (rubric_upload_documents, as on the step's page) decides when the plan has it.
  assert.equal(byKey(guideSteps({...plan([], []), rubric_upload_documents: [{id: "v1"}], document_versions: []})).rubrics.action.label, "Upload rubrics");
  assert.equal(byKey(guideSteps({...plan([], []), rubric_upload_documents: [], document_versions: [{id: "v1"}]})).rubrics.action.label, "Add the PDF");
});

test("answer areas are named one way in the step and its chips", () => {
  assert.equal(locationState(question(1, {status: "confirmed"})).label, "Answer area confirmed");
  assert.equal(locationState(question(1, {status: "needs_confirmation"})).label, "Answer area ready to confirm");
  assert.equal(locationState(question(1, {status: "needs_review"})).label, "Answer area needs your review");
  assert.equal(locationState(question(1, {status: "running"})).label, "Reading the blank template…");
  assert.equal(byKey(guideSteps(plan([question(1)]))).locations.title, "Confirm where each answer is");
});

// A drafted quiz with its setup done and nothing scanned yet (Quiz 4 on 2026-10-02 read "Grade" everywhere): the Grade step waits
// for papers and leads to the Inbox. Its name, its place and its done rule do not change.
test("a finished setup with no paper scanned waits for papers and leads to the Inbox", () => {
  const drafted = withProgress({responses: {}, collection: {no_scan: 339}, certification: {state: "none"}});
  let steps = byKey(guideSteps(drafted));
  assert.equal(steps.grade.label, "Grade");
  assert.equal(steps.grade.current, true);
  assert.equal(steps.grade.waiting, true);
  assert.equal(steps.grade.done, false);
  assert.equal(steps.grade.started, false);
  assert.equal(steps.grade.status, "Waiting for papers");
  assert.equal(steps.grade.title, "Upload the scans once it is given");
  assert.deepEqual(steps.grade.action, {label: "Upload scans", href: "#inbox?upload=1"});
  assert.equal(steps.review.done, false);
  assert.match(pipelineMarkup(Object.values(steps), h), /aria-label="Grade: Waiting for papers\./);
  // The finished steps' "Next step" callout names the scan, not the Grade step.
  const header = stepHeader(guideSteps(drafted), "rubrics", h);
  assert.match(header, /<strong class="next-step-title">Upload the scans once it is given<\/strong>/);
  assert.match(header, /<a class="button primary" href="#inbox\?upload=1">Upload scans →<\/a><\/aside>/);
  // A scan of it waiting for its scan checks: finish that scan.
  steps = byKey(guideSteps(withProgress({responses: {}, collection: {}, certification: {state: "none"}, scans: [{job_id: "job-1", inbox_ready: false}], awaiting_review: 1})));
  assert.equal(steps.grade.waiting, true);
  assert.equal(steps.grade.title, "Finish the scan checks");
  assert.deepEqual(steps.grade.action, {label: "Finish the scan checks", href: "#inbox/job/job-1"});
  // With the plan's assignment and term known, both open the assignment's Collect: its Upload dialog (I1), its waiting scans (I2).
  const own = {source_assignment_id: "a1", term_id: "t1"};
  assert.deepEqual(byKey(guideSteps({...drafted, ...own})).grade.action, {label: "Upload scans", href: "#assignments/a1/t1?area=collect&upload=1"});
  steps = byKey(guideSteps({...withProgress({responses: {}, collection: {}, certification: {state: "none"}, scans: [{job_id: "job-1", inbox_ready: false}], awaiting_review: 1}), ...own}));
  assert.deepEqual(steps.grade.action, {label: "Finish the scan checks", href: "#assignments/a1/t1?area=collect&scans=waiting"});
  // Anything in grading, ready for it or waiting on the instructor is not "waiting for papers".
  for (const changes of [{new_responses: 8}, {pending: 2}, {failed: 1}, {version_decisions: 1, decision_responses: 3}, {collection: {complete: 2}},
    {scans: [{job_id: "job-2", inbox_ready: true}]}, {responses: {grading_review: 1}, review_responses: 1}]) {
    const grade = byKey(guideSteps(withProgress({responses: {}, collection: {}, certification: {state: "none"}, ...changes}))).grade;
    assert.notEqual(grade.waiting, true, JSON.stringify(changes));
    assert.notEqual(grade.status, "Waiting for papers");
  }
});

test("questions an accepted draft confirmed for review are not asked again, and its toast says what follows", async () => {
  // ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): the acceptance's confirmations are ordinary readiness rows, so step 1
  // counts them and names no "Confirm rubrics for review"; a question edited since is asked alone.
  const value = plan([question(18, {key: []}), question(19, {key: []})]);
  const row = {ready: true, confirmed_at: "2026-10-11T15:00:00+00:00", by_finalization: false};
  value.rubric_quality = {enabled: true, state: "needed", readiness: {"id-18": row, "id-19": row}};
  assert.deepEqual([rubricProgress(value).reviewConfirmed, rubricProgress(value).reviewReady], [2, true]);
  assert.notEqual(byKey(guideSteps(value)).rubrics.action?.label, "Confirm rubrics for review");
  value.rubric_quality.readiness["id-19"] = {ready: false};
  assert.deepEqual([rubricProgress(value).reviewConfirmed, rubricProgress(value).reviewReady], [1, false]);
  assert.equal(byKey(guideSteps(value)).rubrics.action.label, "Confirm rubrics for review");
  // The toast adds the acceptance's own answer: nothing when it created no question, that the check started, or what it waits for
  // (where a key is still to be read, the sentence says it is read once its answer area is confirmed).
  const {afterAcceptance} = await import("../static/grading-rubric-drafts.js");
  const waits = "The rubric check starts by itself once the answer areas are confirmed and the answer keys, each read once its answer area is confirmed, are saved.";
  assert.equal(afterAcceptance({results: [{status: "replaced"}]}), "");
  assert.equal(afterAcceptance({results: [], review: ""}), " The rubric check started by itself.");
  assert.equal(afterAcceptance({results: [], keys_to_read: ["id-18"], review: waits}), ` ${waits}`);
  // Key & Rubric Finalization says the plan read's sentence instead of asking to run the check, only while such a plan waits: a
  // check that runs, waits for review or failed says its own state, and a plan without the sentence asks as it always did.
  const lead = changes => finalizationProgress({...value, ...changes}).detail;
  assert.equal(lead({}), "0 of 2 questions have a key. Run the rubric check: it is required before grading.");
  assert.equal(lead({rubric_review_waits: waits}), `0 of 2 questions have a key. ${waits}`);
  assert.equal(byKey(guideSteps({...value, rubric_review_waits: waits})).key.status, byKey(guideSteps(value)).key.status);
  for (const [state, said] of [["pending", "The rubric check is running. You can keep working meanwhile."], ["failed", "The rubric check failed. Run it again."],
    ["needs_review", "Review the suggested clarifications, exclusions and suspected errors, then confirm."], ["missing", "Upload the rubrics of every version before the rubric check."]]) {
    assert.equal(lead({rubric_review_waits: waits, rubric_quality: {...value.rubric_quality, state}}), `0 of 2 questions have a key. ${said}`, state);
  }
  for (const file of ["grading-rubric-drafts.js", "grading-rubric-versions.js"]) {
    const source = await readFile(new URL(`../static/${file}`, import.meta.url), "utf8");
    assert.match(source, /h\.toast\(`[^`]*\$\{afterAcceptance\(result\)\}`\)/, file);
    assert.match(source, /Creating a question confirms its rubric for the rubric review, so check it here/, file);
  }
});
