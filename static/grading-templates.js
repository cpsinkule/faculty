// Version references are prepared here; only Continue acknowledges the warning
// and releases automatic reading. Draft choices survive uploads and failures.
// Without Gradescope rubrics, one button drafts every version's rubrics at once from the chosen blank templates, whatever the assignment.
import {runFormAction, whenVisible} from './ui.js';
import {draftMarkup, reviewDrafts} from './grading-rubric-drafts.js';
import {allDraftMarkup, draftAllDialog, reviewVersionDrafts} from './grading-rubric-versions.js';
import { viewLink } from './file-viewer.js';
// `actionHost` (the step header's action slot) holds the one "Continue to answer areas" button; `dataset.tier` there says whether it
// is the header's primary. Without one the button stays at the foot of this panel.
export async function templatesView(host, plan, h, active, refresh, {actionHost = null, uploadsReady = false} = {}) {
  const base = `/api/grading/assignments/${plan.id}/templates`, drafting = `/api/grading/assignments/${plan.id}/rubric-drafts`;
  let state = plan.template_setup, busy = false, timer = null, preparing = '';
  const choices = new Map(), attempted = new Set(), errors = new Map(), requests = new Map(), drafts = new Map(), draftRequests = new Map();
  const valid = () => active() && host.isConnected;
  const selected = slot => choices.get(slot.key) ?? slot.template_id ?? '';
  const generation = slot => (state.generations || []).find(g => g.key === slot.key);
  const together = () => (state.generations || []).filter(g => g.kind === 'versions').sort((a, b) => a.created_at.localeCompare(b.created_at)).at(-1);
  const working = () => (state.generations || []).some(g => ['pending', 'running'].includes(g.status));
  // Every version keeps the template it was confirmed with: continuing needs no new confirmation (and writes no new receipt).
  const unchanged = () => Boolean(state.confirmed) && state.slots.every(slot => selected(slot) === slot.template_id);
  const continueNode = () => (actionHost?.isConnected ? actionHost : host).querySelector('[data-template-continue]');
  // Not usable yet: a secondary copy in the step header is left out (a grey button beside the primary said nothing, review fix GS-D9);
  // as the header's primary it stays, disabled, and its title says what it waits for.
  const waitingFor = () => state.blockers?.length ? state.blockers.join(' ') : state.slots.some(s => !selected(s)) ? 'Choose a blank template for every version first.' : '';
  const continueButton = tier => {
    const blocked = !state.ready || state.slots.some(s => !selected(s));
    if (blocked && actionHost && tier !== 'primary') return '';
    return `<button class="button${tier ? ` ${tier}` : ''}" type="button" data-template-continue ${busy || blocked ? 'disabled' : ''}${blocked && waitingFor() ? ` title="${h.e(waitingFor())}"` : ''}>Continue to answer areas</button>`;
  };
  // A template whose name says another version is probably in the wrong slot: said, never refused.
  const otherVersion = slot => {
    const chosen = slot.options.find(t => t.id === selected(slot)), named = chosen?.label.match(/\bVersion\s+([A-Z])\b/i)?.[1]?.toUpperCase();
    return slot.exam_version && named && named !== slot.exam_version ? `This template’s name says Version ${named}, but this slot is ${slot.label}. Check it is the right blank.` : '';
  };
  // The empty choice says what is happening: the saved PDF is being prepared (or waits its turn) only while preparation runs; a
  // failed one shows its warning and Retry below instead (grading-setup-18).
  const emptyChoice = slot => slot.can_import && preparing && !errors.has(slot.document_id) && (preparing === slot.document_id || !attempted.has(slot.document_id))
    ? 'Preparing assignment PDF…' : slot.can_import ? 'Choose a blank template' : 'Upload or choose a template';
  // Without a version slot (no registered paper) the draft block says what comes first; a blocker about rubrics would not.
  function draw() {
    if (!valid()) return;
    host.innerHTML = `<section class="grading-panel grading-template-step"><h3>Assignment Templates</h3><p>Choose one blank copy of the assignment per version. A blank scan from the same scanner as the student papers gives the best alignment.</p><div data-draft-all></div>
      <div class="grading-template-slots">${state.slots.map((slot, index) => `<section class="grading-template-slot" data-template-slot="${index}"><header><h4>${h.e(slot.label)}</h4><span class="supporting-copy">${h.e(slot.questions)} question${slot.questions === 1 ? '' : 's'}</span></header><div class="grading-template-choice">${h.select(`Template for ${slot.label}`, `template-${index}`, [['', emptyChoice(slot)], ...slot.options.map(t => [t.id, `${t.label} · ${t.page_count} pages${t.source_version_id ? ' · Digital PDF' : ''}`])], selected(slot))}
        ${selected(slot) ? `<a ${viewLink(`/api/assignment-templates/${selected(slot)}/pdf`, "Template", h.e)}>Preview template</a>` : ''}</div>
        ${otherVersion(slot) ? `<p class="grading-warning" data-template-mismatch>${h.e(otherVersion(slot))}</p>` : ''}
        ${errors.has(slot.document_id) ? `<p class="grading-warning" role="alert">${h.e(errors.get(slot.document_id))}</p>${h.button('retry-template', 'Retry preparation', `data-document="${h.e(slot.document_id)}"`)}` : ''}
        <div data-draft-slot="${index}"></div>
        <details class="grading-template-upload" ${!selected(slot) || drafts.has(slot.key) ? 'open' : ''}><summary>${selected(slot) ? 'Replace this template' : 'Upload a blank template'}</summary><form data-template-upload="${index}"><label class="field">Upload for ${h.e(slot.label)}<input type="file" name="files" accept=".pdf,.png,.jpg,.jpeg,.docx,.tex,.latex" multiple required ${busy || !state.can_choose ? 'disabled' : ''}></label><details><summary>LaTeX entry point</summary>${h.field('Main source filename', 'entrypoint', '', 'text', 'placeholder="main.tex"')}</details><button class="button" type="submit" ${busy || !state.can_choose ? 'disabled' : ''}>Upload template</button><p class="form-error" role="alert" hidden></p></form></details></section>`).join('')}</div>
      ${state.slots.length ? state.blockers.map(text => `<p class="grading-warning">${h.e(text)}</p>`).join('') : ''}<p role="status" data-template-status>${busy ? 'Preparing template pages…' : unchanged() ? 'These templates are confirmed.' : state.blockers?.length ? '' : 'Template checks start after you confirm and continue.'}</p>${actionHost ? '' : `<div class="grading-actions">${continueButton('primary')}</div>`}</section>`;
    if (actionHost?.isConnected) actionHost.innerHTML = continueButton(actionHost.dataset.tier === 'primary' ? 'primary' : '');
    for (const [index, slot] of state.slots.entries()) {
      host.querySelector(`[name="template-${index}"]`).onchange = event => { choices.set(slot.key, event.target.value); draw(); };
    }
    if (continueNode()) continueNode().onclick = continueToAreas;
    host.querySelectorAll('[data-template-upload]').forEach(form => {
      const slot = state.slots[Number(form.dataset.templateUpload)];
      const draft = drafts.get(slot.key);
      if (draft) {
        const transfer = new DataTransfer();
        draft.files.forEach(file => transfer.items.add(file));
        form.elements.files.files = transfer.files;
        form.elements.entrypoint.value = draft.entrypoint;
      }
      form.addEventListener('change', () => drafts.set(slot.key, {files: [...form.elements.files.files], entrypoint: form.elements.entrypoint.value}));
      form.onsubmit = event => {
        event.preventDefault();
        if (busy) return;
        void runFormAction(form, async () => {
          const files = [...form.elements.files.files];
          if (!files.length) throw new Error('Choose the template files for this version.');
          const signature = JSON.stringify([slot.key, files.map(f => [f.name, f.size, f.lastModified]), form.elements.entrypoint.value]);
          if (!requests.has(signature)) {
            let number = 1, label;
            do { label = `${slot.label.slice(0, 120)} · uploaded template ${number++}`; } while (slot.options.some(t => t.label === label));
            requests.set(signature, {id: crypto.randomUUID(), label});
          }
          const body = new FormData();
          body.set('assignment_id', plan.source_assignment_id);
          body.set('request_id', requests.get(signature).id);
          body.set('label', requests.get(signature).label);
          body.set('entrypoint', form.elements.entrypoint.value);
          for (const file of files) body.append('files', file);
          busy = true;
          if (continueNode()) continueNode().disabled = true;
          try {
            const result = await h.api('/api/assignment-templates', 'POST', body);
            state = await h.api(base);
            const current = state.slots.find(s => s.key === slot.key);
            if (!current?.options.some(t => t.id === result.id)) throw new Error(`The uploaded template’s ${result.page_count} pages do not match ${slot.label}. Upload the template matching the printed assignment.`);
            choices.set(slot.key, result.id);
            drafts.delete(slot.key);
          } finally {
            busy = false;
            if (valid() && continueNode()) continueNode().disabled = !state.ready || state.slots.some(s => !selected(s));
          }
          if (form.isConnected && valid()) draw();
        });
      };
    });
    host.querySelectorAll('[data-grading-action="retry-template"]').forEach(button => button.onclick = () => {
      attempted.delete(button.dataset.document); void prepare();
    });
    drawDrafts();
  }
  // Only the draft blocks follow a running draft, so an open list or a chosen file is never redrawn under the instructor.
  function drawDrafts() {
    if (!valid()) return;
    const all = host.querySelector('[data-draft-all]'), latest = together();
    if (all) {
      // Once every question has a rubric the AI draft is an alternative, not the next thing: folded unless a draft is under way.
      const folded = uploadsReady && !['pending', 'running'].includes(latest?.status);
      const wasOpen = all.querySelector('details')?.open;
      all.innerHTML = folded ? `<details class="grading-tools grading-draft-fold" ${wasOpen ? 'open' : ''}><summary>Draft rubrics with AI</summary>${allDraftMarkup(state, latest, h, busy)}</details>` : allDraftMarkup(state, latest, h, busy);
      all.querySelector('[data-grading-action="draft-all"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
        // RUBRIC CHOICES (2026-10-24): asked with the draft; a plan whose choices cannot be read is drafted as before.
        const choices = await h.api(`/api/grading/assignments/${plan.id}/rubric-choices`).catch(() => null);
        if (!valid()) return;
        draftAllDialog(state, selected, latest, h, async (mode, templates, rubricChoices) => {
          if (!valid()) throw new Error('This assignment is no longer open. Reopen it to generate rubrics.');
          const key = JSON.stringify([templates, mode, latest?.id || '', rubricChoices]);
          if (!draftRequests.has(key)) draftRequests.set(key, crypto.randomUUID());
          await h.api(drafting, 'POST', {all_versions: true, templates, mode, request_id: draftRequests.get(key), ...(rubricChoices ? {rubric_choices: rubricChoices} : {})});
          void poll();
        }, choices);
      }));
      all.querySelector('[data-grading-action="cancel-all-drafts"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
        await h.api(`${drafting}/${latest.id}/cancel`, 'POST', {});
        await poll();
      }));
      all.querySelector('[data-grading-action="review-all-drafts"]')?.addEventListener('click', event => h.run(event.currentTarget, () =>
        reviewVersionDrafts({...plan, template_setup: state}, latest.id, h, refresh, valid)));
    }
    for (const [index, slot] of state.slots.entries()) {
      const target = host.querySelector(`[data-draft-slot="${index}"]`);
      if (!target) continue;
      target.innerHTML = draftMarkup(generation(slot), selected(slot), h);
      const id = generation(slot)?.id;
      target.querySelector('[data-grading-action="cancel-drafts"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
        await h.api(`${drafting}/${id}/cancel`, 'POST', {});
        await poll();
      }));
      target.querySelector('[data-grading-action="review-drafts"]')?.addEventListener('click', event => h.run(event.currentTarget, () =>
        reviewDrafts({...plan, template_setup: state}, id, h, refresh, valid)));
    }
  }
  async function poll() {
    clearTimeout(timer);
    if (!valid()) return;
    try {
      const latest = await h.api(base);
      if (!valid()) return;
      state = {...state, generations: latest.generations};
      drawDrafts();
    } catch (error) { h.toast(error.message, true); }
    if (valid() && working()) timer = setTimeout(() => whenVisible(poll), 3000);  // a hidden tab reads once it is shown again
  }
  async function prepare() {
    if (busy || !valid() || !state.can_choose) return;
    const slot = state.slots.find(s => s.can_import && !selected(s) && !attempted.has(s.document_id));
    if (!slot) return;
    attempted.add(slot.document_id); errors.delete(slot.document_id); busy = true; preparing = slot.document_id; draw();
    try {
      await h.api(`${base}/prepare`, 'POST', {document_id: slot.document_id});
      if (!valid()) return;
      state = await h.api(base);
    } catch (error) { errors.set(slot.document_id, error.message); }
    finally { busy = false; preparing = ''; draw(); }
    if (valid()) void prepare();
  }
  function continueToAreas() {
    if (busy) return;
    if (unchanged()) { location.hash = `#grading/${plan.id}?tab=locations`; return; }
    const selections = state.slots.map(slot => ({key: slot.key, template_id: selected(slot)}));
    const resetting = state.slots.some(slot => slot.region_templates.some(template => template && selected(slot) !== template));
    const before = state.slots.some(slot => slot.confirmed);
    h.dialog(before ? 'Confirm changed templates' : 'Confirm assignment templates', 'Template quality affects alignment and cropped answer areas.',
      `<p class="grading-warning">${h.e(state.warning)}</p>${resetting ? h.check('Reset the existing answer areas for the changed templates; I will confirm new areas', 'reset_regions') : ''}`,
      'Confirm and continue', async (values, _, live) => {
        await h.api(`${base}/confirm`, 'POST', {input_sha256: state.input_sha256, selections, acknowledge_warning: true, reset_regions: values.has('reset_regions')});
        if (live()) { h.close(); location.hash = `#grading/${plan.id}?tab=locations`; }
      });
  }
  draw();
  void prepare();
  if (working()) timer = setTimeout(() => whenVisible(poll), 3000);
}
