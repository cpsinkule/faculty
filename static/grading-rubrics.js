import {partCapsText, rubricDisplay, warningsMarkup} from "./grading-rubric-display.js";
import {mountRubricQuality} from "./grading-rubric-quality.js";
import {uploadRubrics} from "./grading-upload.js";
import {importFromGradescope} from "./grading-gradescope-rubrics.js";
import {mountLocationChecks, wholeExamMarkup, mountWholeExam} from "./grading-locations.js";
import {inPrintedOrder, locationState, rubricState, rubricProgress, finalizationProgress, readyForReview, guideSteps, stepHeader, stepNext, preparationTasks, needsAssignmentPaper, assignmentPaperHref, versionTitle} from "./grading-guide.js";
import {templatesView} from "./grading-templates.js";
import {answerKeyDialog, keyMarkup} from "./grading-groups.js";
import {mountVersions} from "./grading-versions.js";
import {mathText, renderMath} from "./grading-math.js";
import {mountQuestionWorkspace} from "./grading-question-workspace.js";
import {confirmForReview, hasDraft, draftQuestions, saveAllDrafts, discardAllDrafts, mountRubricEdits, rubricEditMarkup, EDIT_HELP, finalizedDrafts, reopenDialog} from "./grading-rubric-edit.js";
import {disclosureMarkup, plural, whenVisible} from "./ui.js";
import {adaptable, adaptRubrics} from "./grading-rubric-adapt.js";
import {preparationHistory} from "./grading-jobs.js";
import {linkIndex, linkOf, loadLinks} from "./grading-rubric-links.js";

const chip = (state, h, extra = "") => `<span class="grading-chip grading-chip-${h.e(state.key)} ${extra}">${h.e(state.label)}</span>`;
// An approved rubric keeps the shared approved-badge class other views and checks rely on.
const chips = (q, h) => `${chip(locationState(q), h)}${chip(rubricState(q), h, q.rubric?.approved_at ? "grading-approved" : "")}`;

// What an approval actually covers: the interpreted values, not the raw file.
function rulesMarkup(q, h) {
  const rules = q.rubric.rules, display = rubricDisplay(q.rubric.rules);
  return `<p>${h.e(rules.mode === "negative" ? "Start at full credit and deduct" : "Start at zero and add credit")} · ${h.e(rules.floor)}–${h.e(rules.ceiling)} points${h.e(partCapsText(rules))}</p><ul class="grading-rule-list">${rules.items.map(i => `<li><strong>${h.e(rubricDisplay(q.rubric.rules).label(i.id))} · ${h.e(i.points)} point${Math.abs(i.points) === 1 ? "" : "s"}</strong><span>${mathText(i.description, h)}</span>${i.follow_through ? '<small>Allow follow-through; do not repeat the originating penalty.</small>' : ""}${i.requires.length ? `<small>Requires items ${h.e(display.list(i.requires))}</small>` : ""}${i.excludes.length ? `<small>Excludes items ${h.e(display.list(i.excludes))}</small>` : ""}${i.condition_handling === "structured" ? "<small>Condition enforced by instructor-confirmed structured rules.</small>" : ""}${i.exceptions && i.exceptions !== i.description ? `<small>${h.e(i.exceptions)}</small>` : ""}</li>`).join("")}</ul>${warningsMarkup(q, h)}`;
}

// The instructor's own correction of one line (a typo in the coordinator's rubric). The server writes a new immutable revision and,
// where nothing the finalization decided depends on the wording, carries the confirmed finalization to it.
async function correctLine(plan, q, itemId, h, refresh, questions = []) {
  const item = q.rubric.rules.items.find(i => i.id === itemId);
  // LINKED VERSIONS (requirement, 2026-10-02): the same line on the other versions may be corrected in the same window, each with its
  // own wording (prefilled with ITS current wording, never this one's) and each its own new revision. Nothing is ticked by default.
  const links = await loadLinks(h, plan.id), set = linkOf(linkIndex(links.sets), q.question_id, itemId);
  const others = set && !set.conflict ? set.lines.map(line => ({line, other: questions.find(row => row.question_id === line.question_id)}))
    .filter(({line, other}) => line.question_id !== q.question_id && other?.rubric?.rules.items.some(i => i.id === line.item_id)) : [];
  const linked = others.length ? `<fieldset class="grading-correct-linked"><legend>The same line on other versions</legend><p class="supporting-copy">Tick a version to correct its line too. Each keeps its own wording and values.</p>${others.map(({line, other}) => {
    const wording = other.rubric.rules.items.find(i => i.id === line.item_id).description, name = `${line.question_id}\u001f${line.item_id}`;
    return `<div class="grading-correct-linked-line"><label class="grading-check"><input type="checkbox" name="also-correct" value="${h.e(name)}"><span>${h.e(versionTitle(other))} · ${h.e(rubricDisplay(other.rubric.rules).label(line.item_id))}${set.kind === 'related' ? ' · related: decided per version' : set.kind === 'seed' ? ' · same item ID, not verified' : ''}</span></label>${h.area(`Corrected wording for ${versionTitle(other)}`, `also-wording\u001f${name}`, wording, 'rows="2"')}</div>`;
  }).join('')}</fieldset>` : '';
  h.dialog(`Correct ${rubricDisplay(q.rubric.rules).label(itemId).toLowerCase()}`, `${versionTitle(q)} · ${item.points} point${Math.abs(item.points) === 1 ? "" : "s"}`,
    `${h.area("Corrected wording", "description", item.description, "required")}<p class="supporting-copy">Saving creates a new revision of this rubric with this line corrected. Its points and every other line stay as they are, the uploaded wording stays on record, and your confirmed exclusions and the other lines’ clarifications are kept. Any clarification of this corrected line is removed because it described the old wording.</p><p class="supporting-copy">Responses of this question that were graded under the old wording need grading again. Faculty changes nothing on Gradescope: correct the line there as well.</p>${linked}`,
    "Save corrected line", async (values, _, active) => {
      const also = values.getAll("also-correct").map(name => {
        const [qid, iid] = name.split("\u001f"), other = questions.find(row => row.question_id === qid);
        return {other, iid, description: values.get(`also-wording\u001f${name}`)};
      });
      const result = await h.api(`/api/grading/assignments/${plan.id}/questions/${q.question_id}/rubric-lines`, "POST",
        {version: q.version, rubric_id: q.rubric_id, sha256: q.rubric.sha256, item_id: itemId, description: values.get("description")});
      // Each other version through its own route, one at a time; the first refusal stops and says which versions were corrected.
      const done = [];
      for (const {other, iid, description} of also) {
        try {
          await h.api(`/api/grading/assignments/${plan.id}/questions/${other.question_id}/rubric-lines`, "POST",
            {version: other.version, rubric_id: other.rubric_id, sha256: other.rubric.sha256, item_id: iid, description});
          done.push(other);
        } catch (error) {
          if (active()) { h.close(); await refresh(); }
          h.toast(`${versionTitle(q)}${done.length ? ` and ${done.map(versionTitle).join(', ')}` : ''} corrected; ${versionTitle(other)} was not, and nothing after it was sent: ${error.message}`, true);
          return;
        }
      }
      if (active()) {
        h.close(); await refresh();
        h.toast((result.correction.finalization_carried ? "Line corrected. Your confirmed rubric finalization was kept." : "Line corrected. Run the rubric check again before grading.")
          + (done.length ? ` Also corrected on ${done.map(other => `Version ${other.exam_version}`).join(', ')}.` : ''));
      }
    });
}

// On Rubrics & Templates a question is done when its rubric is CONFIRMED for the rubric reviewer, as the step's own count says; an
// uploaded rubric alone is not (a multi-version exam otherwise looked ready with rubrics nobody had checked). One vocabulary for the
// step (2026-09-26 overhaul): the question's chip and the question list say the same state; approval belongs to steps 2 and 3.
function reviewChoice(plan, q) {
  if (!q.rubric) return {label: 'Needs rubric', key: 'todo'};
  if (hasDraft(plan, q)) return {label: 'Unsaved changes', key: 'todo'};
  return readyForReview(plan, q) ? {label: 'Confirmed for review', key: 'done'} : {label: 'Not confirmed for review', key: 'todo'};
}
function rubricChoice(plan, q) {
  const state = reviewChoice(plan, q);
  return q.rubric ? {...state, label: `${q.rubric.rules.items.length} rubric items · ${state.label.toLowerCase()}`} : state;
}

// This assignment's grading runs, as the step graph counts them.
const planJobs = plan => (plan.activity?.jobs || []).filter(job => job.assignment_id === plan.id);

// Step 1's one sentence of state: what is left, in the order the step asks for it. (Without the assignment's PDF no step is drawn:
// `rubricView` shows the Add-paper panel instead, as the rubric uploads and the blank templates are matched to its versions.)
function rubricLead(progress, deciding) {
  if (progress.failed) return 'A rubric preparation failed. Inspect it before continuing.';
  if (progress.busy) return 'Rubrics are being prepared. You can keep working meanwhile.';
  if (!progress.uploadsReady) return progress.total ? `${progress.uploaded} of ${plural(progress.total, 'question')} have a rubric. Upload the rest, or draft them from the blank templates.`
    : 'Upload the rubric files, or draft the rubrics from each version’s blank template.';
  if (!progress.reviewReady) return `${progress.reviewConfirmed} of ${plural(progress.uploaded, 'rubric')} confirmed for review. Check each one below, then confirm it.`;
  if (!progress.templatesReady) return 'Every rubric is confirmed for review. Choose each version’s blank template, then continue.';
  if (deciding) return `${plural(deciding, 'paper')} ${deciding === 1 ? 'needs' : 'need'} your version decision before grading.`;
  return 'Every rubric and every version’s template is confirmed.';
}

// A question to open when the page is next drawn: a confirmed answer area moves on to the next question still needing one.
let reopen = null, focusShown = '';
// Per plan: whether the instructor opened or closed a finished step 1's "Change rubrics or templates", and on which address.
const toolsOpen = new Map();

function questionChoices(plan, questions, stage) {
  return questions.map(q => {
    const state = stage === 'locations' ? locationState(q) : stage === 'answer-key'
      ? {label: q.answer?.key?.length ? 'Answer key saved' : q.answer?.proposed_key?.length ? 'Key read from the rubric · confirm it'
          : readKey(q) ? 'Key read from the blank assignment · confirm it' : reading(q) ? 'Reading the key from the blank assignment' : 'Needs answer key', key: q.answer?.key?.length ? 'done' : 'todo'}
      : rubricChoice(plan, q);
    // Grouped by version (printed order keeps each version's questions together); the heading carries the version, the search too.
    return {id: q.question_id, label: q.exam_version ? q.content.title : versionTitle(q), group: q.exam_version ? `Version ${q.exam_version}` : '',
      unsaved: stage === 'rubrics' && Boolean(q.rubric) && hasDraft(plan, q), detail: `${q.content.max_points} points · ${state.label}`, attention: state.key !== 'done'};
  });
}

// Questions without a saved key: those with a key read from the rubric (Save N answer keys saves them as shown) and those with none.
// Where the rubric gives no key, its latest reading from the blank exam (2026-09-25): the key it read under the current rubric, or
// whether it is still being read.
const readKey = q => !q.answer?.proposed_key?.length && q.answer?.read_key?.current && q.answer.read_key.status === 'complete' && q.answer.read_key.key.length ? q.answer.read_key : null;
const reading = q => ['pending', 'running'].includes(q.answer?.read_key?.status);
const keyToSave = q => q.answer?.proposed_key?.length ? q.answer.proposed_key : readKey(q)?.key || [];
// GRADING MULTIPLE-CHOICE READER, AUTOMATIC CONFIRMATION (2026-10-08): a multiple-choice question is graded by choice by itself; it can
// be set to written work, and back, until it has grades.
const choiceMarkup = (q, h) => {
  const c = q.answer?.choice;
  if (!c) return '';
  const action = (name, label) => c.locked ? '' : `<div class="grading-actions">${h.button(name, label, `data-question="${h.e(q.question_id)}"`)}</div>`;
  if (c.written) {
    return `<div class="grading-key-choice" data-key-choice data-choice-written><p><span class="grading-chip">Multiple choice · graded as written work</span> You chose written work for this question.</p>${action('grade-by-choice', 'Grade by choice')}</div>`;
  }
  const g = c.grading;
  return `<div class="grading-key-choice" data-key-choice><p><span class="grading-chip">Multiple choice · graded by choice</span> Options ${h.e(g.options[0])}–${h.e(g.options[g.options.length - 1])}, correct ${h.e(g.correct)} (from the rubric).</p><p class="supporting-copy">With the multiple-choice reader on, a Cloud batch reads the marked option; an unsure read is graded as written work.</p>${action('grade-as-written', 'Grade as written work')}</div>`;
};
const proposedKeys = questions => {
  const open = questions.filter(q => q.rubric && !q.answer?.key?.length);
  return {ready: open.filter(q => keyToSave(q).length), unread: open.filter(q => !keyToSave(q).length && !reading(q)), reading: open.filter(reading)};
};


/**
 * Draws a setup step (Rubrics & Templates, Answer areas, Key & Rubric Finalization). Returns true when it scrolled the page on
 * purpose (a link naming a question, the templates, the versions or the preparations, or the next answer area to confirm), so a
 * refresh of the same step keeps the instructor's scroll position otherwise. `view.busy()` says the instructor is in a dialog,
 * typing or holding unsaved work: a background redraw waits for it.
 */
export function rubricView(host, plan, h, refresh, active, view = {}) {
  // A link's focus (a question, the templates, the versions) is shown once per address; an address without one starts afresh.
  if (!view.focus) focusShown = '';
  const firstShow = () => focusShown !== location.hash && Boolean(focusShown = location.hash);
  let scrolled = false;
  const scrollTo = node => { if (!node) return; node.scrollIntoView({block: 'start'}); scrolled = true; };
  const questions = inPrintedOrder(plan.questions), stage = view.locations ? 'locations' : view.stage || 'rubrics';
  // ASSIGNMENT-WORKSPACE "PREPARE STARTS WITH ITS PREREQUISITE": one action before dependent setup on each Prepare sub-tab.
  if (needsAssignmentPaper(plan)) {
    host.innerHTML = `<section class="grading-panel" data-add-paper-panel><h2>Add the assignment paper</h2>
      <p>Add a PDF in Create first. Rubrics and blank templates are matched to its paper versions.</p>
      <a class="button primary" data-grading-add-paper href="${h.e(assignmentPaperHref(plan))}">Add paper in Create</a>
      <details class="disclosure"><summary>After the paper is added</summary><p>Prepare the rubrics and blank templates, confirm answer areas, then check the answer keys and finalize the rubrics. Printing can proceed while grading preparation is unfinished.</p>
      ${questions.length ? `<p>${questions.length} existing question${questions.length === 1 ? '' : 's'} will remain available.</p>` : ''}</details></section>`;
    return false;
  }
  const next = reopen?.key === `${plan.id}:${stage}` && questions.some(q => q.question_id === reopen.id) ? reopen.id : '';
  reopen = null;
  const questionWorkspace = () => mountQuestionWorkspace(host.querySelector('[data-setup-questions]'), questionChoices(plan, questions, stage), h,
    {key: `${plan.id}:${stage}`, initial: next || (questions.some(q => q.question_id === view.focus) ? view.focus : ''), shortcuts: true});
  if (stage === 'answer-key') {
    // The panel says what the header cannot: one sentence, what is still being read, and keys nothing could be read for. Save N
    // answer keys itself is the step header's one primary while keys wait for it (review fixes GS-D1, GS-D5; grading-setup-14).
    const keys = proposedKeys(questions);
    const keyNotes = () => {
      const {ready, unread, reading: busy} = keys;
      if (!ready.length && !unread.length && !busy.length) return '<p class="supporting-copy">Every question has an answer key.</p>';
      return `<p class="supporting-copy">Check the key each question shows below; fix a wrong one with <strong>Edit this answer key</strong>.</p>${busy.length ? `<p class="supporting-copy" role="status" data-keys-reading>Reading ${busy.length === 1 ? 'the key' : `${busy.length} keys`} from the blank assignment: ${h.e(busy.map(versionTitle).join(', '))}…</p>` : ''}${unread.length ? `<div class="grading-warning" data-keys-unread><p>No key could be read from the rubric for ${h.e(unread.map(versionTitle).join(', '))}: read ${unread.length === 1 ? 'it' : 'them'} from the blank assignment for you to check, or write ${unread.length === 1 ? 'it' : 'them'} with Edit this answer key.</p><div class="grading-actions">${h.button('read-keys', `Read ${unread.length === 1 ? 'it' : `these ${unread.length}`} from the blank assignment`, 'data-read-keys')}</div></div>` : ''}`;
    };
    const confirmKeys = keys.ready.length ? `<button type="button" class="button primary" data-grading-action="confirm-all-keys" data-confirm-all-keys title="${h.e(`Saves the key each question shows (read from its rubric, or from the blank assignment) for the ${keys.ready.length === 1 ? 'question' : `${keys.ready.length} questions`} without a saved key.`)}">Save ${keys.ready.length} answer key${keys.ready.length === 1 ? '' : 's'}</button>` : '';
    const keySteps = guideSteps(plan, planJobs(plan)), {elsewhere} = stepNext(keySteps, 'key');
    host.innerHTML = `${stepHeader(keySteps, 'key', h, {lead: finalizationProgress(plan).detail,
        body: disclosureMarkup({label: 'How this works', key: 'grading:finalization-help', className: 'grading-step-help',
          body: '<p>The key is read from your rubric for you to check and never awards credit by itself. The rubric check suggests clarifications, exclusions and suspected errors; one confirmation approves every rubric. Clarifications are for the AI graders only: grades and exports keep your original rubric wording.</p>'}),
        actions: `${confirmKeys}<span class="grading-step-action-slot" data-quality-action-slot></span><a class="button${elsewhere ? '' : ' primary'}" data-finalization-next hidden href="#grading/${h.e(plan.id)}?tab=jobs">Continue to Grade →</a>`})}
      <div class="grading-setup-columns"><section class="grading-panel"><h3>Answer key</h3>${questions.length ? keyNotes() : ''}${questions.length ? `<div class="grading-actions">${h.button('answer-key', 'Edit the whole answer key')}</div>` : '<p>Upload the rubrics first.</p>'}</section><div data-rubric-quality-slot></div></div>
      <div data-setup-questions>${questions.map(q => `<section class="grading-panel grading-key-question" data-question-panel="${h.e(q.question_id)}"><header class="grading-heading"><div><span class="eyebrow">${h.e(q.content.max_points)} points</span><h3>${h.e(versionTitle(q))}</h3></div>${q.rubric ? h.button('edit-key', 'Edit this answer key', `data-question="${h.e(q.question_id)}"`) : ''}</header><div class="grading-key-summary"><h4>Accepted final answers</h4>${q.answer?.key?.length ? keyMarkup(q.answer.key, h) : q.answer?.proposed_key?.length ? `<div class="grading-key-proposed" data-key-proposed><p><span class="grading-chip grading-chip-todo">Read from the rubric · not saved yet</span></p>${keyMarkup(q.answer.proposed_key, h)}<div class="grading-actions">${h.button('save-proposed-key', 'Save this key', `data-question="${h.e(q.question_id)}"`)}</div></div>`
        : readKey(q) ? `<div class="grading-key-proposed" data-key-proposed data-key-read><p><span class="grading-chip grading-chip-todo">Read from the blank assignment by ${h.e(q.answer.read_key.model)} · not saved yet</span></p>${keyMarkup(q.answer.read_key.key, h)}${q.answer.read_key.basis.filter(Boolean).map(note => `<p class="supporting-copy">${mathText(note, h)}</p>`).join('')}<div class="grading-actions">${h.button('save-proposed-key', 'Save this key', `data-question="${h.e(q.question_id)}"`)}</div></div>`
        : reading(q) ? '<p class="supporting-copy" role="status">Reading this answer from the blank assignment…</p>'
        : `<p class="grading-warning">No answer could be read from this rubric’s full-credit line.${q.answer?.read_key?.current && q.answer.read_key.status === 'failed' ? ` Reading it from the blank assignment failed: ${h.e(q.answer.read_key.error)}` : q.answer?.read_key?.current && q.answer.read_key.unreadable ? ` The model could not read it from the blank assignment: ${h.e(q.answer.read_key.unreadable)}` : ''} Read it from the blank assignment above, or write the accepted answers with Edit this answer key.</p>`}${choiceMarkup(q, h)}${q.content.worked_solution ? `<details><summary>Worked solution</summary><div>${mathText(q.content.worked_solution, h)}</div></details>` : ''}</div>${q.rubric ? `<details class="grading-tools" data-rubric-lines="${h.e(q.question_id)}" open><summary>Rubric lines · correct a wording</summary><ul class="grading-rule-list">${q.rubric.rules.items.map(i => `<li><div class="grading-rubric-line-heading"><strong>${h.e(rubricDisplay(q.rubric.rules).label(i.id))} · <span class="grading-rubric-points">${h.e(i.points)} point${Math.abs(i.points) === 1 ? '' : 's'}</span></strong><button type="button" class="text-button grading-correct-line" data-grading-action="correct-line" data-question="${h.e(q.question_id)}" data-item="${h.e(i.id)}" aria-label="Correct the wording of ${h.e(rubricDisplay(q.rubric.rules).label(i.id).toLowerCase())}">Correct wording</button></div><span>${mathText(i.description, h)}</span></li>`).join('')}</ul></details>` : ''}</section>`).join('')}</div>`;
    if (questions.length) questionWorkspace();
    else host.querySelector('[data-setup-questions]').innerHTML = '<p class="empty-state">Upload rubrics before editing answer keys.</p>';
    host.querySelectorAll('[data-grading-action="correct-line"]').forEach(button => button.onclick = () => h.run(button, () => correctLine(plan, questions.find(q => q.question_id === button.dataset.question), button.dataset.item, h, refresh, questions)));
    host.querySelectorAll('[data-grading-action="edit-key"]').forEach(button => button.onclick = () => answerKeyDialog(plan, h, refresh, button.dataset.question));
    // Save the key read from the rubric exactly as the page shows it: for one question, or with Save N answer keys for every question
    // without a key (requirement, 2026-09-25). Only key text changes; the server checks each question's version.
    const saveProposed = list => h.api(`/api/grading/assignments/${plan.id}/answer-key`, 'POST', {version: plan.version,
      questions: list.map(q => ({question_id: q.question_id, version: q.version, answer_key: keyToSave(q)}))});
    // Written work, or back to grading by choice, keeps the key as it is.
    const setChoice = (q, value) => h.api(`/api/grading/assignments/${plan.id}/answer-key`, 'POST', {version: plan.version,
      questions: [{question_id: q.question_id, version: q.version, answer_key: q.answer?.key || [], choice_grading: value}]});
    for (const [action, value] of [['grade-by-choice', null], ['grade-as-written', {written: true}]]) {
      host.querySelectorAll(`[data-grading-action="${action}"]`).forEach(button => button.addEventListener('click', event => h.run(event.currentTarget, async () => {
        await setChoice(questions.find(item => item.question_id === button.dataset.question), value);
        if (active()) await refresh();
      })));
    }
    host.querySelectorAll('[data-grading-action="save-proposed-key"]').forEach(button => button.addEventListener('click', event => h.run(event.currentTarget, async () => {
      await saveProposed([questions.find(q => q.question_id === button.dataset.question)]);
      if (active()) await refresh();
    })));
    host.querySelector('[data-grading-action="read-keys"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
      await h.api(`/api/grading/assignments/${plan.id}/answer-keys/read`, 'POST', {request_id: crypto.randomUUID(), question_ids: proposedKeys(questions).unread.map(q => q.question_id)});
      if (active()) await refresh();
    }));
    // While keys are being read the page takes a quiet look every 5 s (in a hidden tab, once shown again): the plan alone, without
    // its activity, and nothing is drawn. Only a key that finished reading redraws the step, and never under the instructor: while a
    // dialog, typing, unsaved work or a typed question search is on screen, the reading line says so and offers Refresh instead
    // (grading-setup-1, bugs-frontend-2, missed:perf[0]). Each drawing starts at most one chain of looks.
    if (keys.reading.length) {
      const waiting = new Set(keys.reading.map(q => q.question_id));
      const held = () => Boolean(view.busy?.() || document.querySelector('dialog[open]') || host.querySelector('[name="question-search"]')?.value.trim());
      const look = () => whenVisible(async () => {
        if (!active() || !host.isConnected) return;
        let latest = null;
        try { latest = await h.api(`/api/grading/assignments/${plan.id}`); } catch { /* the next look reads again */ }
        if (!active() || !host.isConnected) return;
        const finished = latest && [...waiting].some(id => { const q = latest.questions.find(item => item.question_id === id); return !q || !reading(q); });
        if (!finished) { setTimeout(look, 5000); return; }
        if (!held()) { void refresh(); return; }
        const line = host.querySelector('[data-keys-reading]');
        if (!line) return;
        line.textContent = `${waiting.size === 1 ? 'The answer key' : 'The answer keys'} finished reading. `;
        const again = document.createElement('button');
        Object.assign(again, {type: 'button', className: 'text-button', textContent: 'Refresh', onclick: () => refresh()});
        again.dataset.keysRefresh = '';
        line.append(again);
      });
      setTimeout(look, 5000);
    }
    host.querySelector('[data-grading-action="confirm-all-keys"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
      await saveProposed(proposedKeys(questions).ready);
      if (active()) await refresh();
    }));
    host.querySelector('[data-grading-action="answer-key"]')?.addEventListener('click', () => answerKeyDialog(plan, h, refresh));
    if (view.open === 'answer-key' && questions.length) answerKeyDialog(plan, h, refresh);
    const finalization = quality => {
      const latest = {...plan, rubric_quality: quality || plan.rubric_quality}, progress = finalizationProgress(latest);
      host.querySelector('[data-step-lead]').textContent = progress.detail;
      host.querySelector('[data-finalization-next]').hidden = !progress.done;
      view.onPlan?.(latest);
    };
    finalization();
    // The check's own next thing (Run rubric check, Review and confirm) is the header's primary once no key waits to be saved.
    mountRubricQuality(host.querySelector('[data-rubric-quality-slot]'), plan, h, refresh, active, view.open === 'quality', finalization,
      {actionHost: host.querySelector('[data-quality-action-slot]'), promote: () => !host.querySelector('[data-confirm-all-keys]'), question: view.focus || ''});
    renderMath(host);
    return false;
  }
  const steps = guideSteps(plan, planJobs(plan)), progress = rubricProgress(plan), adapt = adaptable(plan);
  const located = questions.filter(q => locationState(q).key === 'done').length, deciding = plan.workflow?.version_decisions || 0;
  let navigator = null, primaryHost = null;
  // Answer areas shows the rubric read-only: every change to a rubric (its lines, interpretation, revisions, question settings) is
  // made on Rubrics & Templates, one place instead of three.
  const editElsewhere = q => `<p><a class="link-arrow" data-rubric-edit-link aria-label="Edit this rubric on Rubrics &amp; Templates" href="#grading/${h.e(plan.id)}?tab=rubrics&amp;focus=${encodeURIComponent(q.question_id)}">Edit this rubric on Rubrics &amp; Templates</a></p>`;
  const companion = q => !view.unified ? '' : `<section class="assignment-question-context"><h4>Question and solution</h4>${mathText(q.content.problem || q.content.statement || 'Add the question wording.', h)}${q.content.worked_solution ? `<details open><summary>Worked solution</summary>${mathText(q.content.worked_solution, h)}</details>` : '<p>No worked solution recorded.</p>'}<p><a class="link-arrow" href="#grading/${h.e(plan.id)}?tab=answer-key&amp;focus=${encodeURIComponent(q.question_id)}">Answer key and rubric finalization</a></p><div data-workspace-linked-versions="${h.e(q.question_id)}"></div></section>`;
  const questionPanels = `<div data-setup-questions>${questions.map(q => `<section class="grading-panel grading-question" data-question-panel="${h.e(q.question_id)}" data-rubric-question="${h.e(q.question_id)}"><header class="grading-heading"><div><span class="eyebrow">${h.e(q.content.max_points)} points${q.rubric ? ` · ${q.rubric.rules.items.length} rubric items` : ''}</span><h3>${h.e(versionTitle(q))}</h3><p class="grading-chips" data-question-chips>${view.locations ? chips(q, h) : chip(reviewChoice(plan, q), h)}</p></div></header>${companion(q)}<div data-location-slot></div>${q.rubric ? `<details class="grading-question-details" ${view.locations ? '' : 'open'}><summary>${view.locations ? 'Rubric as read from your file' : 'Rubric lines, points and your comments for the reviewer'} · revision ${h.e(q.rubric.revision)}</summary>${view.locations ? rulesMarkup(q, h) : rubricEditMarkup(plan, q, h)}${view.locations ? '' : `<div class="grading-actions">${h.button('edit', 'Edit interpretation', `data-id="${h.e(q.question_id)}"`)}${h.button('import', 'Import revision', `data-id="${h.e(q.question_id)}"`)}</div>`}<details><summary>Original uploaded file</summary><pre class="grading-source">${h.e(q.rubric.source_text)}</pre></details></details>${view.locations ? editElsewhere(q) : ''}` : view.locations ? `<p>This question has no rubric yet.</p>${editElsewhere(q)}` : `<p>Upload or import the rubric for this question.</p>${h.button('import', 'Import rubric', `data-id="${h.e(q.question_id)}"`)}`}${view.locations ? '' : `<details class="grading-tools"><summary>Question settings</summary><p class="supporting-copy">Changing the question requires a newly approved rubric.</p>${h.button('edit-question', 'Edit question / solution', `data-id="${h.e(q.question_id)}"`)}</details>`}</section>`).join('')}</div>`;
  if (view.locations) {
    const total = questions.length, done = total > 0 && located === total, {elsewhere} = stepNext(steps, 'locations');
    // One sentence. While no answer area can be read yet (the templates wait for step 1), say so and link back there (GS-D3).
    const blocked = total > 0 && questions.every(q => !q.location_check || ['waiting_template', 'waiting_templates'].includes(q.location_check.status));
    const lead = count => blocked ? 'Confirm the Assignment Templates on Rubrics & Templates first: the answer areas are read from them.'
      : `${count} of ${total} answer area${total === 1 ? '' : 's'} confirmed.`;
    // Confirm N answer areas and its summary sit once in this header (drawn by mountLocationChecks); Continue shows once every area is confirmed.
    host.innerHTML = `${stepHeader(steps, 'locations', h, {lead: total ? lead(located) : 'Upload the rubrics first: each question’s answer area is read from its blank template.',
        body: '<div data-location-all-summary-slot></div>',
        actions: `${blocked ? `<a class="button primary" data-location-templates href="#grading/${h.e(plan.id)}?tab=rubrics&amp;focus=templates">Choose templates →</a>` : ''}<span class="grading-step-action-slot" data-location-all-slot></span><a class="button${elsewhere ? '' : ' primary'}" data-areas-next ${done ? '' : 'hidden'} href="#grading/${h.e(plan.id)}?tab=answer-key">Continue to Key &amp; Rubric Finalization →</a>`})}
      ${wholeExamMarkup(plan)}${questionPanels}`;
    host.querySelector('[data-step-lead]').dataset.areaProgress = '';
  } else {
    // Step 1. ONE primary action in the header: the thing the step needs next. The template confirmation ("Continue to answer
    // areas") always lives in the header too, as that primary once it is the next thing, else beside it as a secondary.
    const unconfirmed = progress.uploaded > progress.reviewConfirmed;
    const settled = progress.done && !deciding, {elsewhere} = stepNext(steps, 'rubrics');
    const primary = progress.failed ? 'inspect' : !progress.uploadsReady ? 'upload' : unconfirmed ? 'review' : progress.done && deciding ? 'decide' : 'continue';
    const lacking = adapt ? adapt.slots.filter(slot => !plan.questions.some(q => q.assignment_version_id === slot.document_id && q.exam_version === slot.exam_version && q.rubric)) : [];
    const primaryButton = {
      inspect: `<button type="button" class="button primary" data-grading-action="inspect-preparation">${h.e(steps[0].action?.label || 'Inspect rubric preparation')}</button>`,
      upload: '<button type="button" class="button primary" data-grading-action="upload-rubrics">Upload rubrics</button>',
      review: '<button type="button" class="button primary" data-grading-action="confirm-all-review">Confirm all for rubric review</button>',
      decide: '<button type="button" class="button primary" data-grading-action="decide-versions">Decide versions</button>',
      continue: '',
    }[primary];
    const continueSlot = plan.template_setup ? '<span class="grading-step-action-slot" data-template-continue-slot></span>'
      : progress.done ? `<a class="button${elsewhere || primary !== 'continue' ? '' : ' primary'}" data-step-continue href="#grading/${h.e(plan.id)}?tab=locations">Continue to answer areas</a>` : '';
    const working = (plan.template_setup?.generations || []).some(g => ['pending', 'running'].includes(g.status));
    const rubricPanel = `<section class="grading-panel rubric-upload-first"><h3>Rubrics</h3><p>Upload Gradescope CSVs, or import the rubrics straight from Gradescope, to establish the problem numbers.</p><div class="grading-actions">${primary !== 'upload' ? h.button('upload-rubrics', 'Upload rubrics') : ''}${h.button('import-gradescope-rubrics', 'Import from Gradescope')}${adapt && lacking.length ? h.button('adapt-rubrics', `Adapt ${adapt.first.label}’s rubrics to other versions`) : ''}${unconfirmed && primary !== 'review' ? h.button('confirm-all-review', 'Confirm all for rubric review') : ''}</div><details class="grading-tools"><summary>Other setup options</summary>${progress.uploadsReady ? '' : '<p class="supporting-copy">No CSVs? Choose each version’s blank template under Assignment Templates, then draft the rubrics of every version at once there.</p>'}${adapt && !lacking.length ? h.button('adapt-rubrics', `Adapt ${adapt.first.label}’s rubrics to other versions`) : ''}${h.button('question', 'Add question manually')}<p class="supporting-copy">Rubric approval takes place in Key &amp; Rubric Finalization.</p></details><div data-preparation-slot></div></section>`;
    const columns = `<div class="grading-setup-columns"><div>${rubricPanel}<div data-versions-slot></div></div><div data-templates-slot id="grading-templates"></div></div>`;
    const slots = plan.template_setup?.slots || [];
    // A finished step keeps its tools one click away: a summary, then everything that would change it in one disclosure (open while
    // a draft or adaptation runs, or when a link asked for the templates or versions).
    // The instructor's own opening or closing holds across refreshes of the same address (review fix GS-3); a new address (a link
    // naming the templates or versions) starts from the rule again.
    const remembered = toolsOpen.get(plan.id);
    const opened = remembered?.hash === location.hash ? remembered.open
      : working || ['templates', 'versions'].includes(view.focus) || view.open === 'templates' || view.open === 'upload';
    const summary = `<div class="grading-setup-summary" data-setup-summary><div class="stat"><strong>${h.e(progress.reviewConfirmed.toLocaleString())}</strong><span>rubric${progress.reviewConfirmed === 1 ? '' : 's'} confirmed for review</span></div>${slots.length ? `<div class="stat"><strong>${h.e(slots.filter(s => s.confirmed).length.toLocaleString())}</strong><span>version template${slots.length === 1 ? '' : 's'} confirmed</span></div>` : ''}<div class="stat" data-paper-versions hidden><strong></strong><span>paper versions confirmed</span></div></div>`;
    host.innerHTML = `${stepHeader(steps, 'rubrics', h, {lead: rubricLead(progress, deciding),
        body: progress.uploaded ? `<p class="grading-drafts-bar" data-rubric-drafts role="status" hidden><span data-rubric-drafts-count></span><button type="button" class="button" data-grading-action="save-all-edits">Save all</button><button type="button" class="text-button" data-grading-action="discard-all-edits">Discard all</button></p>${disclosureMarkup({label: 'How edits and comments work', body: `<p>${h.e(EDIT_HELP)}</p>`, key: 'grading:rubric-edit-help', className: 'grading-step-help'})}` : '',
        actions: `${primaryButton}${continueSlot}`})}
      ${settled ? `${summary}<details class="disclosure grading-setup-tools" data-setup-tools ${opened ? 'open' : ''}><summary><span>Change rubrics or templates</span></summary><div class="disclosure-body">${columns}</div></details>` : columns}
      ${questionPanels}`;
    primaryHost = host.querySelector('[data-template-continue-slot]');
    if (primaryHost) primaryHost.dataset.tier = primary === 'continue' && !elsewhere ? 'primary' : 'secondary';
  }
  // Recorded from the instructor's click (a parsed open attribute also fires toggle, which is not their choice).
  const tasks = preparationTasks(plan);
  host.insertAdjacentHTML('afterbegin', `<section class="grading-preparation-checklist"><strong>${tasks.length ? 'Remaining preparation' : 'Preparation complete'}</strong>${tasks.length ? `<ol>${tasks.map(task => `<li><a href="${h.e(task.href)}">${h.e(task.label)}</a></li>`).join('')}</ol>` : '<p>Questions, templates, answer areas and rubrics are ready.</p>'}${view.ask && tasks.length ? '<button type="button" class="button" data-prepare-remaining>Prepare remaining setup</button><p class="supporting-copy">The Assistant prepares a review. Changes still require approval.</p>' : ''}</section>`);
  host.querySelector('[data-prepare-remaining]')?.addEventListener('click', event => {
    void h.run(event.currentTarget, () => view.ask(`Prepare the remaining setup for assignment ${plan.source_assignment_id} in term ${plan.term_id}, grading plan ${plan.id}. Use prepare_assessment for its existing exam when available; inspect current prerequisites, use the focused tools, and leave required changes for my approval. Do not certify, send or publish.`, {reviewOnly: true}));
  });
  const tools = host.querySelector('[data-setup-tools]');
  tools?.querySelector(':scope > summary').addEventListener('click', () => toolsOpen.set(plan.id, {hash: location.hash, open: !tools.open}));
  navigator = questions.length ? questionWorkspace() : null;
  if (view.unified && questions.length) void loadLinks(h, plan.id).then(links => {
    if (!active()) return;
    for (const q of questions) {
      const family = links.families.find(ids => ids.includes(q.question_id)) || [];
      const others = questions.filter(other => other.question_id !== q.question_id && family.includes(other.question_id));
      const slot = host.querySelector(`[data-workspace-linked-versions="${CSS.escape(q.question_id)}"]`);
      if (slot && others.length) slot.innerHTML = `<p>Linked versions: ${others.map(other => `<a href="#grading/${h.e(plan.id)}?tab=rubrics&amp;workspace=1&amp;focus=${encodeURIComponent(other.question_id)}">${h.e(versionTitle(other))}</a>`).join(' · ')}</p>`;
    }
  });
  // A link naming a question (Answer areas' "Edit this rubric on Rubrics & Templates") shows it once, not on every later refresh.
  if (questions.some(q => q.question_id === view.focus) && firstShow()) scrollTo(host.querySelector('.grading-question-work'));
  // The whole exam's areas at a glance; choosing one opens its question here.
  const selectQuestion = id => { navigator?.select(id); host.querySelector('.grading-question-work')?.scrollIntoView({block: 'start'}); };
  if (view.locations) mountWholeExam(host, plan, h, selectQuestion);
  const work = next && host.querySelector('.grading-question-work');
  if (work && work.getBoundingClientRect().top < 0) scrollTo(work);
  if (!questions.length) host.querySelector('[data-setup-questions]').innerHTML = '<p class="empty-state">No questions yet.</p>';
  const templatesHost = host.querySelector('[data-templates-slot]');
  if (templatesHost && plan.template_setup) void templatesView(templatesHost, plan, h, active, refresh, {actionHost: primaryHost, uploadsReady: progress.uploadsReady});
  if ((view.focus === 'templates' && firstShow()) || view.open === 'templates') scrollTo(templatesHost);
  const versionsHost = host.querySelector('[data-versions-slot]');
  const paperCount = host.querySelector('[data-paper-versions]');
  if (versionsHost) mountVersions(versionsHost, plan, h, refresh, active, confirmed => {
    if (!paperCount || !confirmed) return;
    paperCount.querySelector('strong').textContent = confirmed.toLocaleString();
    paperCount.hidden = false;
  });
  if (view.focus === 'versions' && firstShow()) scrollTo(versionsHost);  // "Decide versions" on the assignment's notice
  host.querySelector('[data-grading-action="decide-versions"]')?.addEventListener('click', () => versionsHost?.scrollIntoView({block: 'start'}));
  renderMath(host);
  // Rubric preparation history (worker readings of rubric sources) sits with the rubrics it prepares; a link naming it opens it.
  const preparationSlot = host.querySelector('[data-preparation-slot]');
  const history = preparationSlot ? preparationHistory(preparationSlot, plan, plan.activity?.preparations, h, refresh, active, {open: view.focus === 'preparations'}) : null;
  const showPreparations = () => {
    const tools = host.querySelector('[data-setup-tools]');
    if (tools && history && tools.contains(history)) tools.open = true;
    const details = history?.querySelector('[data-preparation-history]');
    if (details) details.open = true;
    history?.scrollIntoView({block: 'start'});
    history?.querySelector('summary')?.focus({preventScroll: true});
  };
  if (view.focus === 'preparations' && firstShow()) { showPreparations(); scrolled = Boolean(history); }
  host.querySelectorAll('[data-grading-action="inspect-preparation"]').forEach(button => button.addEventListener('click', showPreparations));
  host.querySelectorAll('[data-grading-action="upload-rubrics"]').forEach(button => button.addEventListener('click', () => uploadRubrics(plan, h, refresh, active)));
  host.querySelectorAll('[data-grading-action="import-gradescope-rubrics"]').forEach(button => button.addEventListener('click', () => importFromGradescope(plan, h, refresh, active)));
  host.querySelectorAll('[data-grading-action="adapt-rubrics"]').forEach(button => button.addEventListener('click', () => adaptRubrics(plan, h, refresh, active)));
  host.querySelector('[data-grading-action="question"]')?.addEventListener('click', () => view.editQuestion?.());
  if (view.open === 'upload') uploadRubrics(plan, h, refresh, active);
  // The question list and each question's chip follow typing: "Unsaved changes" appears and goes as the form does. While any
  // question holds unsaved changes the header says how many and offers Save all, which is then the step's one primary action.
  const draftsBar = host.querySelector('[data-rubric-drafts]');
  const saveAll = draftsBar?.querySelector('[data-grading-action="save-all-edits"]');
  const drawDrafts = () => {
    if (!draftsBar) return;
    const count = draftQuestions(plan).length;
    draftsBar.hidden = !count;
    draftsBar.querySelector('[data-rubric-drafts-count]').textContent = `${count} question${count === 1 ? ' has' : 's have'} unsaved changes`;
    saveAll.classList.toggle('primary', Boolean(count));
    const actions = host.querySelector('[data-step-actions]');
    if (count) actions?.querySelectorAll('.button.primary').forEach(button => { button.classList.remove('primary'); button.dataset.stepDemoted = ''; });
    else actions?.querySelectorAll('[data-step-demoted]').forEach(button => { button.classList.add('primary'); delete button.dataset.stepDemoted; });
  };
  const onDraft = () => {
    navigator?.update(questionChoices(plan, questions, stage));
    for (const q of questions) {
      const node = host.querySelector(`[data-rubric-question="${CSS.escape(q.question_id)}"] [data-question-chips]`);
      if (node) node.innerHTML = chip(reviewChoice(plan, q), h);
    }
    drawDrafts();
  };
  if (!view.locations) mountRubricEdits(host, plan, h, refresh, onDraft);
  const saveEverything = async () => {
    const {saved, refused, unsent} = await saveAllDrafts(host, plan, h);
    const done = saved.map(({q, lines}) => `${versionTitle(q)} (${lines ? `${lines} line${lines === 1 ? '' : 's'}` : 'comments'})`);
    // The refused question is shown once the step is drawn again with what was saved (or at once when nothing was).
    if (refused) reopen = {key: `${plan.id}:${stage}`, id: refused.q.question_id};
    if (saved.length) await refresh();
    else if (refused) { reopen = null; navigator?.select(refused.q.question_id); }
    if (refused) {
      h.toast(`${saved.length ? `Saved ${done.join(', ')}. ` : ''}${versionTitle(refused.q)} was not saved: ${refused.error.message}${unsent ? ` ${unsent} more question${unsent === 1 ? ' was' : 's were'} not sent.` : ''}`, true);
    } else if (saved.length) h.toast(`Saved ${done.join(', ')}. Confirm ${saved.length === 1 ? 'it' : 'them'} for review when ready.`);
  };
  // Drafts on finalized rubrics take those questions out of the finalization: one window names them before anything is sent.
  saveAll?.addEventListener('click', () => {
    const reopening = finalizedDrafts(host, plan);
    if (!reopening.length) { void h.run(saveAll, saveEverything); return; }
    reopenDialog(plan, reopening, h, async () => { h.close(); await h.run(saveAll, saveEverything); });
  });
  draftsBar?.querySelector('[data-grading-action="discard-all-edits"]')?.addEventListener('click', () => {
    const count = draftQuestions(plan).length;
    if (count && !window.confirm(`Discard the unsaved changes of ${count} question${count === 1 ? '' : 's'}?`)) return;
    discardAllDrafts(host);
    onDraft();
  });
  host.querySelector('[data-grading-action="confirm-all-review"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
    // A confirmation names the SAVED revisions and comments: edits still in a form would silently not be what the reviewer reads.
    const unsaved = questions.filter(q => hasDraft(plan, q));
    if (unsaved.length) throw new Error(`Save or discard your unsaved changes first (Save all is at the top of this step): ${unsaved.map(versionTitle).join(', ')}.`);
    await confirmForReview(plan, questions, h);
    await refresh();
    h.toast('Every rubric is confirmed for review.');
  }));
  host.querySelectorAll("[data-rubric-question] [data-grading-action][data-id]").forEach(button => button.onclick = () => {
    const q = plan.questions.find(q => q.question_id === button.dataset.id);
    if (button.dataset.gradingAction === "edit-question") { view.editQuestion?.(q); return; }
    const base = `/api/grading/assignments/${plan.id}/questions/${q.question_id}`;
    if (button.dataset.gradingAction === "edit") {
      const rules = structuredClone(q.rubric.rules);
      // Wording and points belong to the line editor, where the comments and the confirmation for review go with them; this dialog
      // edits how lines combine. Either way the question needs its confirmation for review again.
      const form = h.dialog("Edit rubric interpretation", "Follow-through, dependencies and structured rules. Change a line’s wording or points under Rubric lines on Rubrics & Templates.", `<p class="supporting-copy">Saving creates a new immutable revision of this rubric. Confirm it for rubric review again. A confirmed answer area is kept unless the rubric’s maximum changes.</p><div class="grading-item-editor">${rules.items.map((i, index) => `<fieldset><legend>${h.e(rubricDisplay(rules).label(i.id))} · ${h.e(i.points)} point${Math.abs(i.points) === 1 ? "" : "s"}</legend><p>${mathText(i.description, h)}</p>${h.check("Allow follow-through", `follow-${index}`, i.follow_through)}<details><summary>Dependencies and conditions</summary><p class="supporting-copy">Original rubric ID: ${h.e(i.id)}. These advanced rules use the original IDs shown in Structured rules below.</p>${h.field("Requires item IDs (comma separated)", `requires-${index}`, i.requires.join(", "))}${h.field("Cannot combine with item IDs (comma separated)", `excludes-${index}`, i.excludes.join(", "))}${i.exceptions ? `<p class="supporting-copy">Retained condition: ${h.e(i.exceptions)}</p>${h.check("The structured rules fully express this condition", `structured-${index}`, i.condition_handling === "structured")}<p class="supporting-copy">Confirm only after encoding every dependency, exclusion or group limit. This is saved in the new rubric revision and takes effect after approval.</p>` : ""}</details></fieldset>`).join("")}</div><details><summary>Groups, dependencies, exceptions and additional items</summary>${h.area("Structured rules (JSON)", "rules", JSON.stringify(rules, null, 2))}<p class="supporting-copy">Editing JSON replaces the simple item fields. All rules are validated before saving.</p></details>`, "Save rubric revision", async (values, _, active) => {
        const edited = JSON.parse(values.get("rules"));
        const saved = new Map(rules.items.map(item => [item.id, item]));
        const reworded = (edited.items || []).filter(item => saved.has(item.id) && (String(item.description) !== String(saved.get(item.id).description) || Number(item.points) !== Number(saved.get(item.id).points)));
        if (reworded.length) throw new Error(`Change wording and points under Rubric lines on Rubrics & Templates, not here: ${reworded.map(item => rubricDisplay(rules).label(item.id)).join(", ")}.`);
        if (values.get("rules") === JSON.stringify(rules, null, 2)) edited.items.forEach((item, i) => { item.follow_through = values.has(`follow-${i}`); item.repeat_penalty_for_same_originating_error = !item.follow_through;
          for (const key of ["requires", "excludes"]) item[key] = String(values.get(`${key}-${i}`)).split(",").map(id => id.trim()).filter(Boolean);
          delete item.condition_handling;
          if (values.has(`structured-${i}`)) item.condition_handling = "structured";
        });
        await h.api(`${base}/rubrics`, "POST", {version: q.version, source_text: q.rubric.source_text, source_artifact_id: q.rubric.source_artifact_id, rules: edited});
        if (active()) { h.close(); await refresh(); }
      });
      return;
    }
    let sourceArtifact = null;
    async function waitPreparation(job, form) {
      while (["pending", "running"].includes(job.status)) {
        if (!form.isConnected || !document.querySelector("#dialog").open) return null;
        form.querySelector("[data-source-status]").textContent = `Queued worker preparation · ${job.completed_parts || 0} parts read. You can close this window; the work is retained.`;
        await new Promise(resolve => setTimeout(resolve, 1500));
        job = await h.api(`/api/grading/preparations/${job.id}`);
      }
      if (job.status === "failed") throw new Error(job.error);
      return job.result;
    }
    const form = h.dialog("Import coordinator rubric", "The original text is retained exactly. The interpretation starts as a draft.", `${h.select("Format", "format", [["text", "Plain / copied rubric text"], ["csv", "CSV / Gradescope evaluations"], ["json", "Structured JSON"]])}${h.select("Scoring", "mode", [["negative", "Deduct from full credit"], ["positive", "Add credit from zero"]])}<p class="supporting-copy">Gradescope CSVs supply their scoring method and question total. Other formats use the scoring choice above.</p>${h.select("Interpretation", "compiler", [["rules", "Read explicit points and rules"], ["model", "Interpret complex wording with the local model"]])}${h.area("Original rubric text", "source_text", "", "required")}<label class="field">Or read a source file<input type="file" name="source_file" accept=".txt,.csv,.json,.pdf,.png,.jpg,.jpeg"></label>${h.button("read-source", "Read source file")}<p data-source-status role="status"></p>`, "Interpret rubric", async (values, currentForm, active) => {
      let prepared = null, preparationId = null;
      if (values.get("compiler") === "model") {
        const job = await h.api(`${base}/compile`, "POST", {source_text: values.get("source_text"), format: values.get("format"), mode: values.get("mode"), request_id: crypto.randomUUID()});
        preparationId = job.id;
        prepared = await waitPreparation(job, currentForm);
        if (!active() || !prepared) return;
      }
      await h.api(`${base}/rubrics`, "POST", {version: q.version, source_text: values.get("source_text"), format: values.get("format"), mode: values.get("mode"), source_artifact_id: sourceArtifact,
        ...(prepared ? {rules: prepared.rules, preparation_id: preparationId} : {})});
      if (active()) { h.close(); await refresh(); }
    });
    form.querySelector('[data-grading-action="read-source"]').onclick = event => h.run(event.currentTarget, async () => {
      const file = form.elements.source_file.files[0];
      if (!file) throw new Error("Choose a source file first.");
      const data = new FormData(); data.set("file", file); data.set("request_id", crypto.randomUUID());
      let result = await h.api(`/api/grading/assignments/${plan.id}/sources`, "POST", data);
      if (result.kind) result = await waitPreparation(result, form);
      if (!result) return;
      if (!form.isConnected) return;
      form.elements.source_text.value = result.source_text; sourceArtifact = result.source_artifact_id;
      form.elements.format.value = file.name.toLowerCase().endsWith(".json") ? "json" : file.name.toLowerCase().endsWith(".csv") ? "csv" : "text";
      const detected = result.rubric_source;
      if (detected?.mode) form.elements.mode.value = detected.mode;
      let notice = result.rubric_source_error || result.provenance?.note || "Original retained. Inspect the extracted text before interpreting it.";
      if (detected) {
        notice = `Gradescope rubric ${detected.question_number}: ${detected.item_count} items${detected.mode ? ` · ${detected.mode} scoring` : ""}${detected.max_points != null ? ` · ${detected.max_points} points` : ""}. Original retained.`;
        if (detected.max_points != null && detected.max_points !== q.content.max_points) notice += ` This question is set to ${q.content.max_points} points. Edit its maximum to ${detected.max_points} before importing.`;
        else notice += " Review full-credit alternatives, part totals and item combinations before approval.";
      }
      form.querySelector("[data-source-status]").textContent = notice;
    });
  });
  if (view.locations || view.unified) mountLocationChecks(host, plan, h, refresh, active, latest => {
    // Template reading finishes in the background: keep each row and the guide current.
    for (const q of latest.questions) {
      const node = host.querySelector(`[data-rubric-question="${CSS.escape(q.question_id)}"] [data-question-chips]`);
      if (node) node.innerHTML = chips(q, h);
    }
    navigator?.update(questionChoices(latest, inPrintedOrder(latest.questions), stage));
    const count = latest.questions.filter(q => locationState(q).key === 'done').length, total = latest.questions.length;
    if (host.querySelector('[data-area-progress]')) host.querySelector('[data-area-progress]').textContent = `${count} of ${total} answer area${total === 1 ? '' : 's'} confirmed.${count === total ? '' : ' Look through each one; the Confirm button above takes those whose printed points match.'}`;
    if (host.querySelector('[data-areas-next]')) host.querySelector('[data-areas-next]').hidden = !total || count !== total;
    mountWholeExam(host, latest, h, selectQuestion);
    view.onPlan?.(latest);
  }, (confirmed, latest) => {
    // Browser navigation only: after one confirmation the next question in printed order (wrapping), after confirming together the first
    // one, whose answer area still needs confirming.
    const done = new Set(confirmed.map(q => q.question_id)), order = inPrintedOrder(latest.questions);
    const at = confirmed.length === 1 ? order.findIndex(q => done.has(q.question_id)) : -1;
    const left = [...order.slice(at + 1), ...order.slice(0, Math.max(at, 0))].filter(q => !done.has(q.question_id) && locationState(q).key !== 'done');
    if (left.length) reopen = {key: `${plan.id}:${stage}`, id: left[0].question_id};
    h.toast(`${confirmed.length === 1 ? `${versionTitle(confirmed[0])}: answer area confirmed.` : `${confirmed.length} answer areas confirmed.`}${left.length ? ` ${left.length} still ${left.length === 1 ? 'needs' : 'need'} confirming; showing ${versionTitle(left[0])}.` : ''}`);
  });
  return scrolled;
}
