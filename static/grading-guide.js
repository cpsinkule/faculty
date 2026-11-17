// One ordered workflow for an assessment: what is done, what is next, and why
// a later step is waiting. Pure functions of the plan; nothing is sent anywhere.
import {nextStepMarkup, plural as count} from './ui.js';

// Assignment versions (an exam's A on white and B on yellow behind one label sheet, or a quiz's versions): pure progress per version.
export const versionTitle = q => `${q.content.title}${q.exam_version ? ` · Version ${q.exam_version}` : ""}`;
const hasKey = q => q.has_answer_key ?? Boolean(q.answer?.key?.length);

// ASSIGNMENT-WORKSPACE: actionable read-only checklist; each link keeps its existing owning editor.
export const needsAssignmentPaper = plan => (Array.isArray(plan.rubric_upload_documents) || Array.isArray(plan.document_versions))
  && !(plan.rubric_upload_documents || plan.document_versions || []).length;
export const assignmentPaperHref = plan => `#documents?assignment=${encodeURIComponent(plan.source_assignment_id || '')}&term=${encodeURIComponent(plan.term_id || '')}&step=version`;
// I1/I2 (2026-10-18): scans are uploaded and finished inside the assignment's Collect, not in the bare Inbox. Both need the
// course-wide assignment and its term (`source_assignment_id`, `term_id` of the plan read); without them the Inbox stays the way.
export const collectHref = (plan, query) => plan?.source_assignment_id && plan?.term_id
  ? `#assignments/${encodeURIComponent(plan.source_assignment_id)}/${encodeURIComponent(plan.term_id)}?area=collect&${query}` : '';
export const uploadScansHref = plan => collectHref(plan, 'upload=1') || '#inbox?upload=1';
// The assignment's scans still in their scan checks, listed on Collect; without an assignment, the first waiting scan or the Inbox.
export function waitingScansHref(plan, workflow = plan?.workflow) {
  const own = collectHref(plan, 'scans=waiting');
  if (own) return own;
  const scan = (workflow?.scans || []).find(s => !s.inbox_ready && s.job_id);
  return scan ? `#inbox/job/${scan.job_id}` : '#inbox';
}
export const SCAN_CHECKS_LABEL = 'Finish the scan checks';
export function preparationTasks(plan) {
  plan = {...plan, ...plan.workflow?.setup};
  if (needsAssignmentPaper(plan)) return [{label: 'Add the assignment paper in Create', href: assignmentPaperHref(plan)}];
  const tasks = [], base = `#grading/${encodeURIComponent(plan.id)}?tab=`;
  const questions = plan.questions || [];
  const groups = new Map();
  questions.forEach(q => { const key = q.exam_version || 'Paper'; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(q); });
  if (!questions.length) tasks.push({label: 'Add questions and rubrics', href: base + 'rubrics&open=upload'});
  for (const slot of plan.template_setup?.slots || []) if (!slot.confirmed) tasks.push({label: `Confirm the blank template${slot.exam_version ? ' for Version ' + slot.exam_version : ''}`, href: base + 'rubrics&focus=templates'});
  for (const [version, rows] of groups) {
    const prefix = version === 'Paper' ? '' : `Version ${version}: `;
    for (const [label, test, tab] of [['add missing rubrics', q => !q.rubric, 'rubrics'],
      ['confirm answer areas', q => q.location_check?.status !== 'confirmed', 'locations'],
      ['check answer keys', q => !hasKey(q), 'answer-key']]) {
      const missing = rows.filter(test);
      if (missing.length) tasks.push({label: `${prefix}${label} (${missing.length})`, href: base + tab + '&focus=' + encodeURIComponent(missing[0].question_id)});
    }
  }
  if (questions.length && (plan.rubric_quality?.state !== 'confirmed' || questions.some(q => !q.rubric?.approved_at)))
    tasks.push({label: 'Review and finalize the rubrics', href: base + 'answer-key&open=quality'});
  return tasks;
}

// The tile and the page navigation share this identity, wording and destination.
// Six stages (requirement, 2026-09-20): everything uploaded comes first; the rubrics are finalized beside the answer key,
// once the answer areas have told the reviewer what each problem asks. The sixth is Certify (instructor,
// 2026-09-26); its key and tab stay `export`, so older links keep working.
const stages = {
  rubrics: {tab: 'rubrics', label: 'Rubrics & Templates'},
  locations: {tab: 'locations', label: 'Answer areas'},
  key: {tab: 'answer-key', label: 'Key & Rubric Finalization'},
  grade: {tab: 'jobs', label: 'Grade'},
  review: {tab: 'review', label: 'Review'},
  export: {tab: 'export', label: 'Certify'},
};

// One line per assignment version: what it has, and plainly what it still lacks.
export function versionProgress(plan) {
  const info = plan.exam_versions || {letters: [], graded: [], blanks: {}};
  if (info.letters.length < 2 && !info.graded.length) return [];
  return info.letters.map(letter => {
    const questions = plan.questions.filter(q => q.exam_version === letter), rubrics = questions.filter(q => q.rubric);
    const missing = [];
    if (!rubrics.length) missing.push("rubrics");
    if (!info.blanks[letter]) missing.push("a blank assignment");
    if (!rubrics.some(hasKey)) missing.push("an answer key");
    return {letter, questions: questions.length, rubrics: rubrics.length, blank: info.blanks[letter]?.label || "",
      located: rubrics.filter(q => q.location_check?.status === "confirmed").length, approved: rubrics.filter(q => q.rubric.approved_at).length,
      keyed: rubrics.filter(hasKey).length, missing};
  });
}

export const unversioned = plan => versionProgress(plan).length ? plan.questions.filter(q => !q.exam_version) : [];

const list = words => words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
export const versionLine = v => v.missing.length ? `Version ${v.letter} has no ${list(v.missing.map(m => m.replace(/^an? /, "")))} yet.`
  : `Version ${v.letter}: ${v.rubrics} rubrics, ${v.located} located, ${v.approved} approved, ${v.keyed} with a key.`;

const number = q => Number(q.location_check?.context?.printed_number || (q.question_key.match(/\d+/) || [Infinity])[0]);

// Printed order, not upload order; each exam version's questions stay together.
export const inPrintedOrder = questions => [...questions].sort((a, b) => (a.exam_version || "").localeCompare(b.exam_version || "") || number(a) - number(b) || a.question_key.localeCompare(b.question_key));

export function locationState(q) {
  const check = q.location_check, status = check?.status;
  if (!q.rubric) return {key: "waiting", label: "Waiting for its rubric"};
  if (status === "confirmed") return {key: "done", label: "Answer area confirmed"};
  if (["waiting_template", "waiting_templates"].includes(status)) return {key: "todo", label: "Needs Assignment Templates"};
  if (status === "pending" || status === "running") return {key: "busy", label: "Reading the blank template…"};
  if (!check?.context?.template && check?.context?.templates?.length > 1) return {key: "todo", label: "Choose the assignment template"};
  if (status === "needs_confirmation") return {key: "todo", label: "Answer area ready to confirm"};
  return {key: "attention", label: "Answer area needs your review"};
}

export const rubricState = q => !q.rubric ? {key: "todo", label: "No rubric yet"}
  : q.rubric.approved_at ? {key: "done", label: "Rubric approved"} : {key: "todo", label: "Approved when you finalize the rubrics"};

// Each rubric confirmed for the rubric reviewer, with the instructor's edits and comments (2026-09-24): the plan read carries the
// finalization's readiness (`rubric_quality.readiness`), the compact `workflow.setup.questions[]` (grading/progress.py
// `setup_summary`) each question's `review_ready`. A question neither reports on is not held back.
export const readyForReview = (plan, q) => (plan.rubric_quality?.readiness ? plan.rubric_quality.readiness[q.question_id]?.ready : q.review_ready) !== false;

// Step 1: every version's rubrics uploaded and every version's template confirmed. Nothing here is approved yet.
export function rubricProgress(plan) {
  plan = {...plan, ...plan.workflow?.setup};
  const total = plan.questions.length, uploaded = plan.questions.filter(q => q.rubric).length;
  const uploadsReady = total > 0 && uploaded === total && !unversioned(plan).length
    && versionProgress(plan).every(v => !v.missing.includes('rubrics'));
  const preparation = plan.preparation || {}, slots = plan.template_setup?.slots;
  const templatesReady = plan.template_setup ? Boolean(plan.template_setup.confirmed)
    : total > 0 && plan.templates?.length > 0 && plan.questions.every(q => q.location_check?.context?.template);
  const failed = Boolean(preparation.failed), busy = Boolean(preparation.pending);
  const reviewConfirmed = plan.questions.filter(q => q.rubric && readyForReview(plan, q)).length, reviewReady = reviewConfirmed === uploaded;
  const done = uploadsReady && templatesReady && reviewReady && !failed && !busy;
  const status = failed ? 'Failed' : busy ? 'In progress' : !uploadsReady ? 'Needs uploads' : !reviewReady ? 'Needs review confirmation' : !templatesReady ? 'Needs templates' : 'Done';
  const detail = `${uploaded} of ${total} questions have a rubric. ${reviewConfirmed} of ${uploaded} confirmed for rubric review.`
    + (slots ? ` ${slots.filter(s => s.confirmed).length} of ${slots.length} version templates confirmed.` : templatesReady ? ' Templates chosen.' : ' Choose the assignment templates.')
    + (preparation.pending || preparation.failed ? ` ${preparation.pending || 0} rubric preparations queued or running · ${preparation.failed || 0} failed.` : '');
  return {total, uploaded, uploadsReady, templatesReady, reviewConfirmed, reviewReady, failed, busy, done, status, detail};
}

// Step 3: the answer key checked, then the mandatory rubric finalization, whose one confirmation approves every rubric.
export function finalizationProgress(plan) {
  plan = {...plan, ...plan.workflow?.setup};
  const rubrics = plan.questions.filter(q => q.rubric), total = plan.questions.length;
  const keyed = rubrics.filter(hasKey).length, approved = rubrics.filter(q => q.rubric.approved_at).length;
  const keysReady = total > 0 && rubrics.length === total && keyed === total && versionProgress(plan).every(v => !v.missing.includes('an answer key'));
  const state = plan.rubric_quality?.state || 'needed';
  const failed = state === 'failed', busy = ['pending', 'running'].includes(state), confirmed = state === 'confirmed';
  const done = keysReady && confirmed && approved === total;
  const status = failed ? 'Failed' : busy ? 'In progress' : !keysReady ? 'Needs answer key' : state === 'needs_review' ? 'Needs review' : !confirmed ? 'Needs rubric check' : approved < total ? 'Needs approval' : 'Done';
  // ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): where the rubric check of accepted drafts starts by itself, the plan read
  // says what it still waits for (`rubric_review_waits`, only while it waits), and the step says that instead of asking to run it.
  const detail = `${keyed} of ${rubrics.length} questions have a key. `
    + (confirmed ? `Rubrics finalized; ${approved} of ${total} approved.` : failed ? 'The rubric check failed. Run it again.' : busy ? 'The rubric check is running. You can keep working meanwhile.'
      : state === 'needs_review' ? 'Review the suggested clarifications, exclusions and suspected errors, then confirm.'
      : state === 'missing' ? 'Upload the rubrics of every version before the rubric check.'
      : plan.rubric_review_waits || 'Run the rubric check: it is required before grading.');
  return {total, keyed, approved, keysReady, state, confirmed, failed, busy, done, status, detail};
}

// A certification's day, local and short ("Sep 26"); wall clock only, nothing converted for display beyond the browser's own.
export function shortDate(iso) {
  const when = new Date(iso || "");
  return Number.isNaN(when.getTime()) ? "" : when.toLocaleDateString(undefined, {month: "short", day: "numeric"});
}

function measured(completed, total, unit, explanation = '') {
  if (!Number.isFinite(total) || total <= 0) return null;
  total = Math.floor(total);
  completed = Math.min(total, Math.max(0, Math.floor(completed || 0)));
  return {completed, total, percent: Math.floor(100 * completed / total), unit, explanation};
}

// Count retained work, not elapsed time. Setup includes both processing and the
// instructor's confirmation; shared template pages count once across questions.
export function workflowProgress(plan) {
  plan = {...plan, ...plan.workflow?.setup};
  const questions = plan.questions, total = questions.length, rubric = rubricProgress(plan), final = finalizationProgress(plan);
  const preparation = plan.preparation || {};
  const missingVersions = versionProgress(plan).filter(v => v.missing.includes('rubrics')).length;
  const qualityDone = Number(['needs_review', 'confirmed'].includes(final.state)) + Number(final.confirmed);
  const slots = plan.template_setup?.slots;
  const templateDone = slots ? slots.filter(s => s.template_id || s.confirmed).length + slots.filter(s => s.confirmed).length : questions.filter(q => q.location_check?.context?.template).length;
  const readings = new Map();
  for (const q of questions) {
    const reading = q.location_check?.reading_progress;
    if (!reading?.template_id || !reading.total) continue;
    const previous = readings.get(reading.template_id);
    readings.set(reading.template_id, previous ? {total: Math.max(previous.total, reading.total), completed: Math.min(previous.completed, reading.completed)} : reading);
  }
  const pages = [...readings.values()];
  const checked = questions.filter(q => ['needs_review', 'needs_confirmation', 'confirmed'].includes(q.location_check?.status)).length;
  const confirmed = questions.filter(q => q.location_check?.status === 'confirmed').length;
  const w = plan.workflow, responses = w?.responses || {};
  const responseTotal = Object.values(responses).reduce((a, b) => a + b, 0);
  const expected = responseTotal + (w?.new_responses || 0) + (w?.decision_responses || 0);
  const finished = (responses.approved || 0) + (responses.manual || 0) + (responses.auto_accepted || 0);
  const graded = Math.max(0, responseTotal - Math.max((responses.pending || 0) + (responses.stale || 0), (w?.pending || 0) + (w?.failed || 0)));
  return {
    rubrics: measured(rubric.uploaded + templateDone,
      total + missingVersions + unversioned(plan).length + (slots ? 2 * slots.length : total) + (preparation.pending || 0) + (preparation.failed || 0) + (w?.version_decisions || 0),
      'upload tasks', 'Rubric uploads, then each version’s template prepared and confirmed. Missing versions, unfinished preparations and papers waiting for your version decision remain outstanding.'),
    locations: measured(pages.reduce((n, p) => n + p.completed, 0) + checked + confirmed,
      pages.reduce((n, p) => n + p.total, 0) + 2 * total, 'area tasks', 'Shared template pages inspected, question checks completed, and answer areas confirmed.'),
    key: measured(final.keyed + qualityDone + final.approved, total ? 2 * total + 2 : 0, 'finalization tasks', 'Answer keys checked, the rubric check run and confirmed, and every rubric approved by that confirmation.'),
    grade: measured(w?.progress?.grade?.completed ?? graded, w?.progress?.grade?.total ?? expected, 'responses processed', 'Current response work; new eligible, stale and actively regraded responses remain unfinished. Scans still in their scan checks are not counted yet. Historical batches do not add extra responses.'),
    review: measured(w?.progress?.review?.completed ?? finished, w?.progress?.review?.total ?? expected, 'final responses', 'Current grades reviewed by you or finalized automatically. Responses still awaiting processing or review remain unfinished.'),
    // Done only while a certification matches the current grades (`workflow.certification`, grading/progress.py).
    export: measured(w?.certification?.state === 'current' ? 1 : 0, finished ? 1 : 0, 'certification', 'A certification that matches the current grades.'),
  };
}

/**
 * THE CERTIFY STEP WAITS FOR A LATE SCAN (GRADING, requirement, 2026-10-11): the plan's linked scans still in their scan checks while
 * no certification is current. Certification refuses until they are through, so the step graph, the Review step's way on and the
 * button at the head of All responses lead to the scans instead: how many wait, where they are listed (the assignment's Collect,
 * I2; without the plan's assignment the first scan, or the Inbox), the button's words, and the two halves of the sentence said
 * of them. Null when none waits or the grades are certified (a current certification is not reopened by this). Pure.
 * @param {any} workflow the plan's `workflow`
 * @param {any} [plan] the plan, for its assignment and term
 */
export function lateScan(workflow, plan = null) {
  const count = workflow?.awaiting_review || 0;
  if (!count || workflow?.certification?.state === "current") return null;
  return {count, href: waitingScansHref(plan, workflow), label: SCAN_CHECKS_LABEL,
    waits: `${count === 1 ? "A later scan of this assignment still waits" : `${count} later scans of this assignment still wait`} for scan checks`,
    then: `certify once ${count === 1 ? "its" : "their"} papers are graded and reviewed`};
}

export function guideSteps(plan, jobs = []) {
  plan = {...plan, ...plan.workflow?.setup};
  const questions = inPrintedOrder(plan.questions), total = questions.length;
  const title = q => q.content.title;
  const rubrics = questions.filter(q => q.rubric);
  const reading = rubrics.filter(q => locationState(q).key === "busy");
  const confirmed = rubrics.filter(q => q.location_check?.status === "confirmed");
  const unconfirmed = rubrics.filter(q => q.location_check?.status !== "confirmed");
  const locationFailed = unconfirmed.filter(q => q.location_check?.status === "failed");
  const reviewable = unconfirmed.find(q => ["needs_confirmation", "needs_review", "failed"].includes(q.location_check?.status) && q.location_check.context.template);
  const rubric = rubricProgress(plan), final = finalizationProgress(plan);
  const gradable = final.confirmed ? questions.filter(q => q.rubric?.approved_at && q.location_check?.status === "confirmed") : [];
  const rubricsTab = q => `#grading/${plan.id}?tab=rubrics${q ? `&focus=${q.question_id}` : ""}`;
  const active = jobs.filter(j => ["pending", "running"].includes(j.status)).length;
  const hasTemplate = plan.templates.length > 0;
  // An exam with versions shows each version's progress, and says plainly what a version still lacks. The first step names only what
  // it needs (rubrics and a blank assignment): the answer key belongs to step 3, which says so itself.
  const versions = versionProgress(plan), loose = unversioned(plan);
  const lacking = word => versions.filter(v => v.missing.includes(word));
  const perVersion = versions.length ? ` ${versions.map(v => versionLine({...v, missing: v.missing.filter(m => m !== "an answer key")})).join(" ")}${loose.length ? ` ${count(loose.length, "question")} still ${loose.length === 1 ? "needs" : "need"} an assignment version.` : ""}` : "";
  // No paper registered for the assignment: rubrics and blank templates attach to its versions, so the PDF comes first (create-4).
  const documents = plan.rubric_upload_documents || plan.document_versions, noPaper = Array.isArray(documents) && !documents.length;
  const steps = [
    {key: 'rubrics', title: 'Upload the rubrics and the assignment templates', done: rubric.done, failed: rubric.failed, status: rubric.status, started: rubric.busy || hasTemplate,
      detail: (total ? rubric.detail : noPaper ? 'Add the paper in Create first: rubrics and blank templates attach to its versions.'
        : 'Upload the rubric CSVs, or draft the rubrics from each version’s blank assignment.') + perVersion,
      action: noPaper && !rubric.uploadsReady ? {label: 'Add the PDF', href: `#documents?assignment=${encodeURIComponent(plan.source_assignment_id || '')}&step=version`}
        : !rubric.uploadsReady ? {label: 'Upload rubrics', href: `${rubricsTab()}&open=upload`}
        : !rubric.reviewReady ? {label: 'Confirm rubrics for review', href: rubricsTab()}
        : {label: hasTemplate ? 'Review assignment templates' : 'Upload blank assignment', href: `${rubricsTab()}&focus=templates`}},
    {key: "locations", title: "Confirm where each answer is", done: total > 0 && confirmed.length === total,
      failed: locationFailed.length > 0, status: reading.length ? "In progress" : reviewable ? "Needs review" : "Pending", started: Boolean(reading.length || reviewable),
      detail: locationFailed.length ? `${count(locationFailed.length, "answer-area check")} failed. Open the answer areas to review or retry.` : reading.length ? `${reading[0].location_check.label} You can keep working meanwhile.`
        : `${confirmed.length} of ${rubrics.length} confirmed. Each suggested answer area needs one look from you.`,
      action: reviewable ? {label: `Review ${title(reviewable)}`, href: `#grading/${plan.id}?tab=locations&focus=${reviewable.question_id}`} : null},
    {key: "key", title: "Check the answer key and finalize the rubrics", done: final.done, failed: final.failed, status: final.status, started: final.busy || final.state === 'needs_review',
      detail: `${final.detail} The key is read from your rubric for you to check and never awards credit by itself. Clarifications are for the AI graders only: grades and exports keep your original rubric wording.${lacking("an answer key").length ? ` ${lacking("an answer key").map(v => `Version ${v.letter}`).join(" and ")} ${lacking("an answer key").length === 1 ? "has" : "have"} no answer key yet.` : ""}`,
      action: !rubrics.length ? null : !final.keysReady ? {label: "Answer key and solutions", href: `#grading/${plan.id}?tab=answer-key`}
        : {label: final.state === 'needs_review' ? 'Review the rubric check' : 'Finalize the rubrics', href: `#grading/${plan.id}?tab=answer-key&open=quality`}},
    {key: "grade", title: "Grade the responses", done: jobs.some(j => ["complete", "review_ready"].includes(j.status)), started: jobs.length > 0,
      detail: active ? `${active} grading run${active === 1 ? " is" : "s are"} in progress.`
        : gradable.length ? `${gradable.length} of ${total} questions are ready to grade.` : "Available once the answer areas are confirmed and the rubrics are finalized.",
      action: gradable.length ? {label: active ? "See progress" : "Grade responses", href: `#grading/${plan.id}?tab=jobs`} : null},
    {key: "review", title: "Review grades", done: false, started: jobs.some(j => ["complete", "review_ready"].includes(j.status)),
      detail: "Review the proposed grades. Grades reviewed by you and grades finalized automatically are counted separately.",
      action: jobs.some(j => ["complete", "review_ready"].includes(j.status)) ? {label: "Review grades", href: `#grading/${plan.id}?tab=review`} : null},
    {key: "export", title: "Certify", done: false, detail: "Check the grades, then certify them. Certifying records them in the gradebook column you choose and emails nobody; with its Gradescope box ticked, Faculty then sends them to Gradescope and publishes them.",
      action: {label: "Certify grades", href: `#grading/${plan.id}?tab=export`}},
  ];
  if (plan.preparation?.pending || plan.preparation?.failed) {
    steps[0].action = {label: 'Inspect rubric preparation', href: `#grading/${plan.id}?tab=rubrics&focus=preparations`};
  }
  const w = plan.workflow;
  if (w) {
    const responses = w.responses || {};
    const grade = steps.find(s => s.key === "grade"), review = steps.find(s => s.key === "review"), output = steps.find(s => s.key === "export");
    const responseCount = Object.values(responses).reduce((a, b) => a + b, 0);
    Object.assign(grade, {done: total > 0 && (w.configured_questions ?? gradable.length) === total && responseCount > 0 && !w.new_responses && !w.pending && !w.failed && !responses.pending && !responses.stale,
      failed: w.failed > 0, status: w.pending ? "In progress" : "Pending", started: Boolean(responseCount || w.pending || w.failed),
      detail: `${w.new_responses} new responses${w.regrade_responses ? ` (${w.regrade_responses} to grade again)` : ""} · ${w.pending} queued or running · ${w.failed} failed.`,
      action: {label: w.failed ? "Resolve grading errors" : w.pending ? "See grading progress" : "Grade new responses", href: `#grading/${plan.id}?tab=jobs`}});
    // Nothing of this assignment has reached grading yet (a drafted quiz not given, or its scan still in its scan checks): the next thing is
    // the scan, not an empty Grade step (2026-10-02 review: Quiz 4 read "Grade" everywhere and "339 students have no scanned paper").
    // The step keeps its name and its done rule; only what it asks for changes, and it is never counted as started.
    const scans = w.scans || [];
    const noPapers = responseCount === 0 && !w.new_responses && !w.pending && !w.failed && !w.version_decisions && !w.decision_responses
      && !w.collection?.complete && scans.every(s => !s.inbox_ready);
    if (noPapers) {
      const scan = scans.find(s => !s.inbox_ready && s.job_id);
      Object.assign(grade, {waiting: true, done: false, started: false, status: "Waiting for papers",
        title: scan ? SCAN_CHECKS_LABEL : "Upload the scans once it is given",
        detail: scan ? "A scan of this assignment waits for its scan checks. Its papers join grading once it clears them."
          : "No papers yet. Once it is given, upload its scans; its papers join grading once they clear the scan checks.",
        action: scan ? {label: SCAN_CHECKS_LABEL, href: waitingScansHref(plan, w)} : {label: "Upload scans", href: uploadScansHref(plan)}});
    }
    // Papers whose version waits for the instructor (a makeup paper still on the room chart, or one with no seat) are decided under
    // Assignment versions on the first step and are not graded until then: neither step is done while any wait (instructor,
    // 2026-09-26: "all of the stages still showed green").
    const deciding = w.version_decisions || 0;
    if (deciding) {
      const first = steps[0], settled = first.done, papers = `${count(deciding, "paper")} ${deciding === 1 ? "needs" : "need"} your version decision`;
      Object.assign(first, {done: false, started: true, status: settled ? "Needs your decision" : first.status, detail: `${first.detail} ${papers} before grading.`,
        action: settled ? {label: "Decide versions", href: `${rubricsTab()}&focus=versions`} : first.action});
      Object.assign(grade, {done: false, started: true, detail: `${grade.detail} ${papers}.`});
    }
    const finished = (responses.approved || 0) + (responses.manual || 0) + (responses.auto_accepted || 0);
    Object.assign(review, {done: grade.done && finished > 0 && !w.review_responses, status: w.review_responses ? "Needs review" : "Pending", started: Boolean(finished || w.review_responses),
      detail: `${w.review_responses} responses to review · ${(responses.approved || 0) + (responses.manual || 0)} reviewed by you · ${responses.auto_accepted || 0} finalized automatically.`,
      action: {label: "Review grades", href: `#grading/${plan.id}?tab=review`}});
    // The sixth step is done only while a CURRENT certification exists (requirement, 2026-09-26). A plan the server reports no certification for reads as none.
    const cert = w.certification || {state: "none"};
    output.done = review.done && cert.state === "current";
    output.started = review.done || cert.state !== "none";
    output.status = cert.state === "changed" ? "Changed since certification" : cert.state === "current" ? "Certified" : "Pending";
    const day = shortDate(cert.created_at), column = cert.gradebook?.title;
    output.detail = cert.state === "current" ? `Certified${day ? ` ${day}` : ""}${column ? ` and recorded in the gradebook column ${column}` : " and recorded in the term gradebook"}.`
      : cert.state === "changed" ? `Grades changed since the certification${day ? ` of ${day}` : ""}. The gradebook keeps the certified values until you certify again.`
      : output.detail;
    output.action = {label: cert.state === "changed" ? "Certify again" : "Certify grades", href: `#grading/${plan.id}?tab=export`};
    // THE CERTIFY STEP WAITS FOR A LATE SCAN (requirement, 2026-10-11): every grade is reviewed and a linked scan still waits for its scan checks.
    // Certification refuses then (`inbox_pending`), so the step asks for that scan, as the Grade step does while it waits for papers:
    // it keeps its name, place and done rule, and its action, the card's button and the "Next" line become the scan's.
    const late = review.done ? lateScan(w, plan) : null;
    if (late) {
      Object.assign(output, {waiting: true, status: "Waiting for a scan", title: late.label,
        detail: `${late.waits}. ${late.then.charAt(0).toUpperCase()}${late.then.slice(1)}.`, action: {label: late.label, href: late.href}});
    }
  }
  // Steps are offered whenever their own inputs exist: rubrics can be approved
  // while the blank exam is still being read. "Next" is the earliest of those.
  const next = (!steps[0].done ? steps[0] : null)
    || steps.find(step => step.failed) || steps.find(step => !step.done && step.action);
  const progress = workflowProgress(plan);
  // A destination or outstanding task count alone is not evidence that work began.
  return steps.map(step => ({...step, ...stages[step.key], started: Boolean(step.started || step.done || step.failed || progress[step.key]?.completed), progress: progress[step.key], destination: `#grading/${plan.id}?tab=${stages[step.key].tab}`, action: step.done ? null : step.action,
    current: step === next, state: step.failed ? "failed" : step.done ? "done" : step === next ? "current" : step.action ? "open" : "waiting"}));
}

// Where a step's page points next: the following stage, and the step to work on now when that is elsewhere (a finished step
// that is not the current one shows it as a callout; the header's Continue already names the following stage).
export function stepNext(steps, key) {
  const index = steps.findIndex(step => step.key === key), step = steps[index];
  const following = steps[index + 1] || null, current = steps.find(s => s.current) || null;
  const elsewhere = Boolean(step?.done && current && current.key !== step.key && current.key !== following?.key);
  return {step, following, current, elsewhere};
}

// The first sentences of a step's detail, up to about 160 characters (at least one sentence): the state, not the explanation.
function brief(text) {
  let out = '';
  for (const part of String(text || '').match(/.+?[.!?](?=\s|$)|.+$/g) || []) {
    const next = `${out} ${part.trim()}`.trim();
    if (out && next.length > 160) break;
    out = next;
  }
  return out;
}

// One goal-first header for a setup step's page (steps 1-3): the stage's name, one sentence of state, and ONE action on the right.
// While the step is unfinished the action is the step's own (or the page's `actions` markup, when the page performs it in place);
// once it is done it is "Continue to <next stage> →". A finished step that is not the current one also shows the current step as a
// "Next step" callout above the header, which only links. Everything here is derived from `guideSteps`; nothing is decided or sent.
// `steps` is guideSteps' output, `key` the step being viewed. Options: `lead` replaces the step's detail as the header sentence,
// `actions` replaces the default action markup ('' for none), `body` is extra markup under the lead (hints, disclosures).
// `leadMarkup`: the lead as markup the caller escaped itself (a sentence holding a text button), instead of `lead`'s text.
export function stepHeader(steps, key, h, {lead = null, leadMarkup = null, actions = null, body = ''} = {}) {
  const {step, following, current, elsewhere} = stepNext(steps, key);
  if (!step) return '';
  // The callout names the current step's own next thing (its action, e.g. "Confirm rubrics for review") and the state behind it (the
  // first sentences of its live detail), never the stage's fixed goal, which can name work already done (review fix GS-D2).
  // A step waiting for papers names its own title and action (Upload scans, or Finish the scan checks): it leads elsewhere.
  const goal = current?.action?.label?.replace(/\s*→\s*$/, '');
  const callout = elsewhere ? nextStepMarkup({eyebrow: `Next step · ${current.label}`, title: current.waiting ? current.title : goal || current.title, detail: brief(current.detail),
    label: current.waiting ? current.action.label : `Open ${current.label}`, href: current.action?.href || current.destination}) : '';
  // One primary per region: under a "Next step" callout the header's Continue is secondary.
  const action = actions ?? (!step.done && step.action ? `<a class="button primary" data-step-action href="${h.e(step.action.href)}">${h.e(step.action.label)}</a>`
    : step.done && following ? `<a class="button${elsewhere ? '' : ' primary'}" data-step-continue href="${h.e(following.destination)}">Continue to ${h.e(following.label)} →</a>` : '');
  return `${callout}<header class="grading-step-header" data-step-header="${h.e(step.key)}"><div class="grading-step-header-text"><h3>${h.e(step.label)}</h3><p data-step-lead>${leadMarkup ?? h.e(lead ?? step.detail)}</p>${body}</div><div class="grading-step-actions" data-step-actions>${action}</div></header>`;
}

export function workflowTab(requested, plan, jobs) {
  if (['assignments', 'approve', 'templates'].includes(requested)) return 'rubrics';
  if (requested === 'rubric-quality') return 'answer-key';
  const steps = guideSteps(plan, jobs);
  if (['settings', 'papers', 'bubble'].includes(requested) || steps.some(step => step.tab === requested)) return requested;
  return steps.find(step => step.current)?.tab || (steps.every(step => step.done) ? 'export' : 'rubrics');
}

const pipelineState = step => step.failed ? 'failed' : step.done ? 'done' : step.started ? 'pending' : 'idle';

// The step graph (DESIGN-LANGUAGE §4): numbered steps in ruled columns. Each step says its state in words under its label
// (Done, Failed, In progress, a waiting step's own status, or its progress fraction); colour only repeats it: done steps
// slate, the current step under a rust rule, failures brick, steps not started faint. data-size="compact" (a Home row, an
// Assignments card) draws the same list as a row of thin bars.
export function pipelineMarkup(steps, h, selectedKey = null, name = 'Assignment workflow steps') {
  return `<div class="grading-pipeline-scroll" role="region" aria-label="${h.e(name)}"><ol class="grading-pipeline">${steps.map((step, index) => {
    const state = pipelineState(step);
    const status = step.failed ? 'Failed' : step.done ? 'Done' : step.waiting ? step.status : state === 'idle' ? 'Not started' : step.status || 'In progress';
    const selected = selectedKey === null ? step.current : step.key === selectedKey;
    const progress = !step.failed && !step.done ? step.progress : null;
    const progressText = progress ? `${progress.percent}% · ${progress.completed} of ${progress.total} ${progress.unit}. ${progress.explanation}` : '';
    const caption = progress
      ? `<small class="grading-pipeline-fraction" role="progressbar" aria-label="${h.e(`${step.label} progress`)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.percent}" aria-valuetext="${h.e(progressText)}">${progress.percent}% · ${progress.completed}/${progress.total}</small>`
      : `<small class="grading-pipeline-status">${h.e(status)}</small>`;
    return `<li class="is-${state}" data-pipeline-step="${h.e(step.key)}"><a href="${h.e(step.destination)}" ${selected ? 'aria-current="step"' : ''} aria-label="${h.e(`${step.label}: ${status}. ${progressText} ${step.detail}`)}" title="${h.e(`${step.title} — ${status}\n${progressText}\n${step.detail}`)}"><span class="grading-pipeline-number" aria-hidden="true">${index + 1}</span><strong>${h.e(step.label)}</strong>${caption}</a></li>`;
  }).join('')}</ol></div>`;
}

export function workflowMarkup(plan, jobs, tab, h) {
  const steps = guideSteps(plan, jobs), index = steps.findIndex(step => step.tab === tab);
  return `<nav class="grading-tabs grading-workflow" aria-label="Grading workflow">${pipelineMarkup(steps, h, steps[index]?.key || '')}${index < 0 ? '' : `<p class="grading-workflow-position">Step ${index + 1} of ${steps.length} · <strong>${h.e(steps[index].label)}</strong></p>`}</nav>`;
}
