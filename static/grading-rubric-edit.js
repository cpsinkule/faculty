import {partCapsText, rubricDisplay, warningsMarkup} from "./grading-rubric-display.js";
import {mathParts, mathText, renderMath} from "./grading-math.js";
import {plural, registerLeaveGuard} from "./ui.js";
import {versionTitle} from "./grading-guide.js";
import {linkIndex, linkOf, loadLinks, LINK_SEP, passedOn, namesValues} from "./grading-rubric-links.js";

// Rubric lines prepared before the rubric reviewer reads them (requirement, 2026-09-24). Wording and points saved here are a new rubric revision (the graders
// read it, scoring uses it); a comment is guidance the reviewer works into that line's clarification. Confirming for review names the
// exact revision and comments the instructor saw; any later change asks for the confirmation again.

const readiness = (plan, q) => plan.rubric_quality?.readiness?.[q.question_id] || {comments: {}, ready: false};

// Unsaved wording, points and comments, kept in this page's memory while the step re-renders: saving or confirming ONE question
// refreshes the whole step, and the other questions' typing must survive it. Browser state only, never saved. A draft belongs to
// the exact revision it was typed on and is never put on a newer one.
const drafts = new Map();
const draftKey = (plan, q) => `${plan.id}:${q.question_id}`;
export const hasDraft = (plan, q) => drafts.get(draftKey(plan, q))?.rubricId === q.rubric_id;
/** The questions of this plan holding unsaved wording, points or comments on their current revision. */
export const draftQuestions = plan => plan.questions.filter(q => q.rubric && hasDraft(plan, q));

// Each mounted form's own reading of its changes and its own discard, for Save all and Discard all.
const controllers = new WeakMap();

// A save replaces this revision's draft after the response arrives. Keep its fields fixed while that request is pending.
async function saveForms(forms, operation) {
  if (forms.some(form => form.dataset.rubricSaving === 'true')) throw new Error('A rubric is already being saved.');
  const controls = forms.flatMap(form => [...form.querySelectorAll('input, select, textarea, button')]).filter(control => !control.disabled);
  forms.forEach(form => { form.dataset.rubricSaving = 'true'; });
  controls.forEach(control => { control.disabled = true; });
  try { return await operation(); }
  finally {
    forms.forEach(form => { delete form.dataset.rubricSaving; });
    controls.forEach(control => { control.disabled = false; });
  }
}

// One leave guard for the editor: while the step's forms are on screen and hold unsaved changes, leaving the page (another step,
// another page, a reload or closing the tab) asks first. The drafts survive moving within this tab, never a reload.
let editing = null;
let guarded = false;
function guard() {
  // Only while the editor is on screen: the shell hides the grading section instead of emptying it, so its forms (and this
  // tab's drafts) outlive leaving #grading, and a guard that fired then would ask on every later navigation and reload.
  if (!editing?.host.isConnected || editing.host.closest('[hidden]') || !editing.host.querySelector('form[data-rubric-edit]')) return null;
  const count = draftQuestions(editing.plan).length;
  return count ? `${count} question${count === 1 ? ' has' : 's have'} unsaved rubric edits. They stay in this tab until you reload or close it. Leave anyway?` : null;
}

export function reviewStatus(prep) {
  if (!prep) return 'Upload this question’s rubric first.';
  if (prep.ready && prep.by_finalization) return 'Covered by your confirmed rubric finalization. Edit or comment to send it to the reviewer again.';
  if (prep.ready) return `Confirmed for rubric review${prep.confirmed_at ? ` on ${new Date(prep.confirmed_at).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'})}` : ''}.`;
  return 'Not confirmed for rubric review yet. Save your edits and comments, then confirm.';
}

// A finalized rubric: its question's confirmed finalization still stands for this revision. Saving a line or a comment takes it
// out of that finalization (the server carries one only where it stood), so the save asks first and says what it undoes
// (grading-setup-3). A question already out of the finalization whose revision is approved still asks before changed lines,
// without the finalization's claims: a new revision needs its review, check and approval again. A comment on it asks nothing.
const stands = (plan, q) => plan.rubric_quality?.standing?.[q.question_id]?.rubric_id === q.rubric_id || Boolean(readiness(plan, q).by_finalization);
const reopens = (plan, q, lines) => stands(plan, q) || (lines > 0 && Boolean(q.rubric?.approved_at));

/**
 * Ask before saving edits that reopen finalized or approved rubrics. `list` is [{q, lines}] (lines: changed lines, 0 for comments
 * only); `operation` runs the save once the instructor chooses to go on. Closing the window saves nothing.
 */
export function reopenDialog(plan, list, h, operation) {
  const one = list.length === 1, lines = list.reduce((sum, row) => sum + row.lines, 0);
  const finalized = list.filter(row => stands(plan, row.q)), approved = list.filter(row => !stands(plan, row.q));
  const certified = plan.workflow?.certification?.state === 'current' ? ' The certified grades will show as changed until you certify again.' : '';
  // Correct wording keeps a finalization only where one stands: the link is offered for a changed line on a finalized question.
  const typo = finalized.find(row => row.lines)?.q;
  const names = rows => rows.map(row => versionTitle(row.q)).join(', ');
  const paragraphs = one ? [finalized.length
    ? (lines
      ? `Saving ${plural(lines, 'changed line')} makes a new revision of this rubric. This question stops grading until you confirm it for review, run the rubric check and approve it again. Responses graded under the earlier revision are graded again after that.${certified}`
      : 'Your rubric finalization covers this question. Saving these comments takes it out of the finalization: this question stops grading until you confirm it for rubric review, run the rubric check and approve it again. The other questions keep grading.')
    : `This rubric is approved. Saving ${plural(lines, 'changed line')} makes a new revision, which needs confirming for review, the rubric check and approval again. Responses graded under the approved revision are graded again after that.${certified}`]
    : [...(finalized.length ? [`Your rubric finalization covers ${finalized.length} of the questions you are saving: ${names(finalized)}. Saving takes each of them out of the finalization: it stops grading until you confirm it for rubric review, run the rubric check and approve it again. The other questions keep grading.`] : []),
      ...(approved.length ? [`${names(approved)} ${approved.length === 1 ? 'is approved: it needs' : 'are approved: they need'} confirming for review, the rubric check and approval again after this save.`] : []),
      ...(lines ? [`Changed lines make a new revision of their rubric, and responses graded under the earlier revision are graded again after that.${certified}`] : [])];
  const link = typo ? `<p class="supporting-copy">To fix only a typo in one line and keep your finalization, use <a data-correct-wording href="#grading/${h.e(plan.id)}?tab=answer-key&amp;focus=${h.e(encodeURIComponent(typo.question_id))}">Correct wording on Key &amp; Rubric Finalization</a>.</p>` : '';
  const subtitle = one ? versionTitle(list[0].q) : [finalized.length ? plural(finalized.length, 'finalized question') : '', approved.length ? plural(approved.length, 'approved question') : ''].filter(Boolean).join(' and ');
  const action = finalized.length ? (one ? 'Save and reopen this question' : 'Save and reopen these questions') : (one ? 'Save the new revision' : 'Save the new revisions');
  const form = h.dialog(finalized.length ? 'Change a finalized rubric?' : 'Change an approved rubric?', subtitle,
    `${paragraphs.map(text => `<p>${h.e(text)}</p>`).join('')}${link}`, action,
    async (values, current, active) => { if (active()) await operation(); });
  // The link leaves this step: the window goes with it (the drafts stay in this tab, and leaving asks first while they exist).
  form.querySelector('[data-correct-wording]')?.addEventListener('click', () => h.close());
  return form;
}

/** The forms holding unsaved changes that reopen a finalized rubric (or change lines of an approved one), as `reopenDialog` lists them. */
export function finalizedDrafts(host, plan) {
  return [...host.querySelectorAll('form[data-rubric-edit]')].map(form => controllers.get(form)).filter(c => c?.dirty())
    .map(c => { let lines = 1; try { lines = c.changes().lines.length; } catch { /* unreadable points: a line change */ } return {q: c.q, lines}; })
    .filter(row => reopens(plan, row.q, row.lines));
}

// The one explanation of the line editor, shown once in the step header instead of above every question's lines.
export const EDIT_HELP = 'Edit a line’s wording or points, and comment on any line for the rubric reviewer. Your wording and points become this rubric: the graders read them and scores use them. Your comments guide the reviewer, who works them into its clarifications for the graders. Faculty changes nothing on Gradescope.';

// The wording's mathematics as the graders will read it, shown only when the wording has some.
const hasMath = text => mathParts(text).some(part => 'math' in part);

// Where the rubric stands in the finalization: a quiet note beside the scoring (step 1's status is the confirmation for review).
const approvalNote = (q, h) => q.rubric.approved_at ? '<span class="grading-approval-note grading-approved">Rubric approved</span>'
  : `<span class="grading-approval-note">${h.e('Approved when you finalize the rubrics')}</span>`;

// Compact: one row per line (label, a narrow points box, the wording growing with its text), the maths preview only where the
// wording has maths, and a one-row comment below. Field names and accessible names are the ones the saved edits and tests use.
export function rubricEditMarkup(plan, q, h) {
  const rules = q.rubric.rules, display = rubricDisplay(rules), prep = readiness(plan, q);
  const notes = i => `${i.follow_through ? '<small>Allow follow-through; do not repeat the originating penalty.</small>' : ''}${i.requires.length ? `<small>Requires items ${h.e(display.list(i.requires))}</small>` : ''}${i.excludes.length ? `<small>Excludes items ${h.e(display.list(i.excludes))}</small>` : ''}${i.exceptions && i.exceptions !== i.description ? `<small>${h.e(i.exceptions)}</small>` : ''}`;
  const line = i => {
    const label = display.label(i.id), comment = prep.comments?.[i.id] || '';
    return `<li data-line="${h.e(i.id)}"><strong class="grading-rubric-line-label">${h.e(label)}</strong>
      <label class="field grading-rubric-line-points"><span class="sr-only">${h.e(label)} points</span><input name="${h.e(`points:${i.id}`)}" aria-label="${h.e(`${label} points`)}" type="number" step="0.01" required value="${h.e(i.points)}"></label>
      <div class="grading-rubric-line-body"><label class="field grading-rubric-line-wording"><span class="sr-only">${h.e(label)} wording</span><textarea name="${h.e(`description:${i.id}`)}" aria-label="${h.e(`${label} wording`)}" rows="1" required data-autogrow>${h.e(i.description)}</textarea></label>
      <div class="grading-rubric-line-preview" aria-hidden="true" ${hasMath(i.description) ? '' : 'hidden'}>${mathText(i.description, h)}</div>
      <label class="field grading-rubric-line-comment"><span class="field-label" aria-hidden="true">Comment for the reviewer</span><textarea name="${h.e(`comment:${i.id}`)}" aria-label="${h.e(`Comment on ${label.toLowerCase()} for the rubric reviewer (optional)`)}" rows="1" maxlength="2000" data-autogrow>${h.e(comment)}</textarea></label><div class="grading-rubric-also" data-also-comment="${h.e(i.id)}" hidden></div>${notes(i)}</div></li>`;
  };
  return `<form class="grading-rubric-edit" data-rubric-edit="${h.e(q.question_id)}" novalidate>
    <p class="grading-rubric-edit-meta">${h.e(rules.mode === 'negative' ? 'Start at full credit and deduct' : 'Start at zero and add credit')} · ${h.e(rules.floor)}–${h.e(rules.ceiling)} points${h.e(partCapsText(rules))} · ${approvalNote(q, h)}</p>
    <ol class="grading-rubric-edit-lines">${rules.items.map(line).join('')}</ol>
    ${warningsMarkup(q, h)}
    <div class="grading-actions">${h.button('save-rubric-edit', 'Save changes', `data-question="${h.e(q.question_id)}"`)}${h.button('discard-rubric-edit', 'Discard changes', `data-question="${h.e(q.question_id)}" hidden`)}${h.button('confirm-rubric-review', prep.ready ? 'Confirmed for review' : 'Confirm for rubric review', `data-question="${h.e(q.question_id)}" ${prep.ready ? 'disabled' : ''}`)}${h.button('confirm-problem-review', 'Confirm every version of this problem', `data-question="${h.e(q.question_id)}" hidden`)}</div>
    <p class="supporting-copy" data-rubric-edit-status role="status">${h.e(reviewStatus(prep))}</p></form>`;
}

// Text boxes grow with what is typed, from one row. Browsers with CSS field-sizing do it themselves.
const sizesItself = () => globalThis.CSS?.supports?.('field-sizing', 'content');
export function autogrow(field) {
  if (sizesItself() || !field.getClientRects().length) return;  // A hidden question is sized when it is typed in.
  field.style.height = 'auto';
  field.style.height = `${field.scrollHeight + 2}px`;
}

// What changed on the form, against the question as the page drew it.
function changes(form, q, prep) {
  const lines = [], comments = {};
  for (const item of q.rubric.rules.items) {
    const wording = String(form.elements.namedItem(`description:${item.id}`).value).trim();
    const points = Number(form.elements.namedItem(`points:${item.id}`).value);
    const line = {item_id: item.id};
    if (wording !== String(item.description).trim()) line.description = wording;
    if (!Number.isFinite(points)) throw new Error(`Give ${rubricDisplay(q.rubric.rules).label(item.id).toLowerCase()} a number of points.`);
    if (points !== Number(item.points)) line.points = points;
    if (Object.keys(line).length > 1) lines.push(line);
    const comment = String(form.elements.namedItem(`comment:${item.id}`).value).trim();
    if (comment !== (prep.comments?.[item.id] || '')) comments[item.id] = comment;
  }
  return {lines, comments};
}

export function confirmForReview(plan, questions, h) {
  const asked = {};
  for (const q of questions) {
    const prep = readiness(plan, q);
    if (q.rubric && !prep.ready) asked[q.question_id] = {rubric_id: prep.rubric_id, comments_sha256: prep.comments_sha256};
  }
  if (!Object.keys(asked).length) throw new Error('Every rubric is already confirmed for review.');
  return h.api(`/api/grading/assignments/${plan.id}/rubric-review-ready`, 'POST', {questions: asked});
}

// LINKED VERSIONS (requirement, 2026-10-02): a comment written once may go to the same line of the other versions. Since 2026-10-03
// only what was ADDED goes, after that line's own comment (`passedOn`): copying the whole comment had put Version A's numbers on B, C
// and D. A rewrite goes nowhere. The lines a changed comment is also put on, from the ticked boxes under it: [{q, item_id, comment}],
// only where that line's comment would change.
function alsoComments(form, prep, plan) {
  const found = [];
  form.querySelectorAll('[data-also-comment]').forEach(slot => {
    const iid = slot.dataset.alsoComment, comment = String(form.elements.namedItem(`comment:${iid}`).value).trim();
    if (slot.hidden || comment === (prep.comments?.[iid] || '')) return;
    slot.querySelectorAll('[data-also-line]:checked').forEach(box => {
      const [qid, item] = box.dataset.alsoLine.split(LINK_SEP), other = plan.questions.find(row => row.question_id === qid);
      const theirs = other ? readiness(plan, other).comments?.[item] || '' : '';
      const passed = passedOn(prep.comments?.[iid] || '', comment, theirs);
      if (other?.rubric && passed.kind === 'add' && passed.text !== theirs) found.push({q: other, item_id: item, comment: passed.text});
    });
  });
  return found;
}

// Under the ticked boxes, what each version will get: the added sentence, a warning when it names values (probably this version's),
// or that a rewrite is not passed on.
function alsoPreview(slot, before, comment, h) {
  const passed = passedOn(before, comment, '');
  const note = slot.querySelector('[data-also-preview]');
  if (!note) return;
  note.innerHTML = passed.kind === 'rewrite' ? 'You rewrote this comment, so nothing is passed on: edit the other versions’ comments yourself.'
    : `Adds “${h.e(passed.added)}” after each ticked version’s own comment.${namesValues(passed.added) ? ' <strong>It names values: check they hold on each version.</strong>' : ''}`;
  slot.querySelectorAll('[data-also-line]').forEach(box => { box.disabled = passed.kind === 'rewrite'; });
}

// One rubric-edits request: the question's own version, rubric revision and checksum travel with every save.
const postEdit = (h, plan, q, lines, comments) => h.api(`/api/grading/assignments/${plan.id}/questions/${q.question_id}/rubric-edits`, 'POST',
  {version: q.version, rubric_id: q.rubric_id, sha256: q.rubric.sha256, lines, comments});

// Each linked question's comments through its own rubric-edits route (comments only: no new revision), one question at a time.
async function postAlso(plan, h, list) {
  const byQuestion = new Map();
  for (const row of list) byQuestion.set(row.q.question_id, {q: row.q, comments: {...(byQuestion.get(row.q.question_id)?.comments || {}), [row.item_id]: row.comment}});
  for (const {q, comments} of byQuestion.values()) {
    await postEdit(h, plan, q, [], comments);
  }
  return byQuestion.size;
}

// Save every question's unsaved changes, one after the other, each to the ordinary per-question endpoint with its own question
// version, rubric revision and checksum. The first refusal stops the run: nothing after it is sent, and its changes stay in its form.
export async function saveAllDrafts(host, plan, h) {
  const pending = [...host.querySelectorAll('form[data-rubric-edit]')].map(form => controllers.get(form)).filter(c => c?.dirty());
  return saveForms(pending.map(c => c.form), async () => {
    const saved = [];
    for (const c of pending) {
      try {
        const {lines, comments} = c.changes();
        if (!lines.length && !Object.keys(comments).length) { drafts.delete(c.key); continue; }
        const also = c.also();
        const result = await postEdit(h, plan, c.q, lines, comments);
        drafts.delete(c.key);
        await postAlso(plan, h, also);
        saved.push({q: c.q, lines: result.edit.lines_changed || 0});
      } catch (error) {
        return {saved, refused: {q: c.q, error}, unsent: pending.length - saved.length - 1};
      }
    }
    return {saved, refused: null, unsent: 0};
  });
}

/** Put every form back to its saved revision and comments. */
export function discardAllDrafts(host) {
  host.querySelectorAll('form[data-rubric-edit]').forEach(form => controllers.get(form)?.discard());
}

export function mountRubricEdits(host, plan, h, refresh, onDraft = () => {}) {
  editing = {host, plan};
  if (!guarded) { guarded = true; registerLeaveGuard(guard); }
  host.querySelectorAll('form[data-rubric-edit]').forEach(form => {
    const q = plan.questions.find(item => item.question_id === form.dataset.rubricEdit);
    const prep = readiness(plan, q), status = form.querySelector('[data-rubric-edit-status]'), key = draftKey(plan, q);
    const save = form.querySelector('[data-grading-action="save-rubric-edit"]'), confirm = form.querySelector('[data-grading-action="confirm-rubric-review"]');
    const discard = form.querySelector('[data-grading-action="discard-rubric-edit"]');
    const dirty = () => { try { const c = changes(form, q, prep); return c.lines.length || Object.keys(c.comments).length; } catch { return true; } };
    const fields = () => q.rubric.rules.items.flatMap(item => [`description:${item.id}`, `points:${item.id}`, `comment:${item.id}`]).map(name => form.elements.namedItem(name));
    const preview = field => {  // The wording's mathematics, as the graders will read it; nothing to show without maths.
      const node = field.closest('li')?.querySelector('.grading-rubric-line-preview');
      if (!node) return;
      node.hidden = !hasMath(field.value);
      node.innerHTML = mathText(field.value, h); renderMath(node);
    };
    // Unsaved edits are not what a confirmation would name: saving comes first. Save changes waits for a change to save
    // (grading-setup-19), and stays disabled while its own request runs.
    let saving = false;
    const update = (note = '') => {
      const pending = dirty();
      if (pending) drafts.set(key, {rubricId: q.rubric_id, values: Object.fromEntries(fields().filter(field => field.value !== field.defaultValue).map(field => [field.name, field.value]))});
      else drafts.delete(key);
      confirm.disabled = Boolean(pending) || prep.ready;
      save.disabled = saving || !pending;
      save.title = pending ? '' : 'Edit a line’s wording, points or comment first.';
      discard.hidden = !pending;
      status.textContent = note || (pending ? 'Unsaved changes. Save them before confirming this rubric for review.' : reviewStatus(prep));
    };
    const draft = drafts.get(key);
    let note = '';
    if (draft?.rubricId === q.rubric_id) {
      for (const [name, value] of Object.entries(draft.values)) {
        const field = form.elements.namedItem(name);
        if (!field) continue;
        field.value = value;
        if (name.startsWith('description:')) preview(field);
      }
    } else if (draft) {
      drafts.delete(key);
      note = 'This rubric changed since you last typed here, so your unsaved changes to the earlier revision were not kept.';
    }
    update(note);
    requestAnimationFrame(() => form.querySelectorAll('textarea[data-autogrow]').forEach(autogrow));
    form.addEventListener('focusin', event => { if (event.target.matches?.('textarea[data-autogrow]')) autogrow(event.target); });
    form.addEventListener('input', event => {
      if ((event.target.name || '').startsWith('description:')) preview(event.target);
      if (event.target.matches?.('textarea[data-autogrow]')) autogrow(event.target);
      update();
      onDraft();
    });
    const reset = () => {
      form.reset();  // The fields' defaults are the saved revision and comments.
      fields().filter(field => field.name.startsWith('description:')).forEach(preview);
      form.querySelectorAll('textarea[data-autogrow]').forEach(autogrow);
      update();
    };
    discard.onclick = () => { reset(); onDraft(); };
    controllers.set(form, {form, q, key, dirty, changes: () => changes(form, q, prep), discard: reset, also: () => alsoComments(form, prep, plan)});
    const send = () => saveForms([form], async () => {
      const {lines, comments} = changes(form, q, prep);
      if (!lines.length && !Object.keys(comments).length) throw new Error('Nothing changed.');
      saving = true;
      try {
        const also = alsoComments(form, prep, plan);
        const result = await postEdit(h, plan, q, lines, comments);
        drafts.delete(key);
        result.also = await postAlso(plan, h, also);
        return result;
      } finally { saving = false; }
    });
    const saved = async result => {
      await refresh();
      h.toast(result.edit.lines_changed ? `Saved ${result.edit.lines_changed} changed line${result.edit.lines_changed === 1 ? '' : 's'} as a new rubric revision. Confirm it for review when ready.`
        : `Comments saved${result.also ? ` here and on ${result.also} other version${result.also === 1 ? '' : 's'}` : ''}. Confirm ${result.also ? 'these rubrics' : 'this rubric'} for review when ready.`);
    };
    save.onclick = event => {
      const button = event.currentTarget;
      let lines;
      try { lines = changes(form, q, prep).lines.length; } catch (error) { h.toast(error.message, true); return; }
      // Comments put on other versions take those questions out of a standing finalization too: the window names them.
      const others = [...new Map(alsoComments(form, prep, plan).map(row => [row.q.question_id, row.q])).values()].filter(other => reopens(plan, other, 0));
      if (!reopens(plan, q, lines) && !others.length) { void h.run(button, async () => saved(await send())); return; }
      reopenDialog(plan, [...(reopens(plan, q, lines) ? [{q, lines}] : []), ...others.map(other => ({q: other, lines: 0}))], h, async () => {
        const result = await send();
        h.close();
        await saved(result);
      });
    };
    confirm.onclick = event => h.run(event.currentTarget, async () => {
      if (dirty()) throw new Error('Save your changes first.');
      await h.api(`/api/grading/assignments/${plan.id}/rubric-review-ready`, 'POST', {questions: {[q.question_id]: {rubric_id: prep.rubric_id, comments_sha256: prep.comments_sha256}}});
      await refresh();
      h.toast('Confirmed for rubric review.');
    });
  });
  onDraft();  // Restoring a draft can find it already saved elsewhere.
  void linkVersions(host, plan, h, refresh);
}

// LINKED VERSIONS (requirement, 2026-10-02): once the plan's links are read, each linked line offers its comment to its copies on the
// other versions ("same" ticked; related or unverified ones left for the instructor to tick), and a problem with versions can be
// confirmed for review in one go. Without links the step is exactly as before.
async function linkVersions(host, plan, h, refresh) {
  const links = await loadLinks(h, plan.id);
  if (!host.isConnected || (!links.sets.length && !links.families.some(family => family.length > 1))) return;
  const index = linkIndex(links.sets), byId = new Map(plan.questions.map(q => [q.question_id, q]));
  host.querySelectorAll('form[data-rubric-edit]').forEach(form => {
    const q = byId.get(form.dataset.rubricEdit), prep = readiness(plan, q);
    if (!q?.rubric) return;
    const slots = [...form.querySelectorAll('[data-also-comment]')];
    for (const slot of slots) {
      const iid = slot.dataset.alsoComment, set = linkOf(index, q.question_id, iid);
      const others = set && !set.conflict ? set.lines.filter(line => line.question_id !== q.question_id && byId.get(line.question_id)?.rubric) : [];
      if (!others.length) { slot.remove(); continue; }
      const label = (other, item) => `${versionTitle(other)} · ${rubricDisplay(other.rubric.rules).label(item)}`;
      slot.innerHTML = `<fieldset class="grading-rubric-also-list"><legend>Also put this comment on</legend>${others.map(line => {
        const other = byId.get(line.question_id), current = readiness(plan, other).comments?.[line.item_id];
        return `<label class="grading-check"><input type="checkbox" data-also-line="${h.e(`${line.question_id}${LINK_SEP}${line.item_id}`)}" ${set.kind === 'same' ? 'checked' : ''}><span>${h.e(label(other, line.item_id))}${set.kind === 'related' ? ' · related: check it fits that version' : set.kind === 'seed' ? ' · same item ID, not verified' : ''}${current ? ' · keeps its own comment' : ''}</span></label>`;
      }).join('')}<small class="supporting-copy" data-also-preview></small></fieldset>`;
    }
    // Offered only while the line's comment differs from the saved one: there is nothing to pass on otherwise.
    const toggle = () => form.querySelectorAll('[data-also-comment]').forEach(slot => {
      const before = prep.comments?.[slot.dataset.alsoComment] || '', comment = String(form.elements.namedItem(`comment:${slot.dataset.alsoComment}`).value).trim();
      slot.hidden = comment === before;
      if (!slot.hidden) alsoPreview(slot, before, comment, h);
    });
    form.addEventListener('input', toggle);
    toggle();
    const family = links.families.find(members => members.includes(q.question_id))?.map(id => byId.get(id)).filter(other => other?.rubric) || [];
    const button = form.querySelector('[data-grading-action="confirm-problem-review"]');
    if (family.length < 2 || !button || family.every(other => readiness(plan, other).ready)) return;
    button.hidden = false;
    button.textContent = `Confirm Versions ${family.map(other => other.exam_version).join(', ')} for rubric review`;
    button.onclick = event => h.run(event.currentTarget, async () => {
      const waiting = family.filter(other => [...host.querySelectorAll('form[data-rubric-edit]')].some(node => node.dataset.rubricEdit === other.question_id && controllers.get(node)?.dirty()));
      if (waiting.length) throw new Error(`Save your changes to ${waiting.map(versionTitle).join(', ')} first.`);
      await confirmForReview(plan, family, h);
      await refresh();
      h.toast(`Confirmed every version of ${q.content.title} for rubric review.`);
    });
  });
}
