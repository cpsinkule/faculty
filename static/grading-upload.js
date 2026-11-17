import {choicesFold} from './grading-rubric-choices.js';
import {mountNotFinal} from './grading-recorded-rulings.js';
// Rubrics arrive per known exam version; originals and per-file retry IDs are retained.
export function uploadRubrics(plan, h, refresh, active) {
  const documents = plan.rubric_upload_documents || plan.document_versions;
  if (!documents.length) { h.toast("Add this assignment’s PDF in Create first.", true); return; }
  const letters = plan.exam_versions?.letters || [];
  const slots = letters.length ? letters.map(letter => ({letter, title: `Version ${letter}`, document: documents.length === 1 ? documents[0].id : ''}))
    : documents.map(d => ({letter: '', title: d.label, document: d.id}));
  let entries = [], changed = false;
  const slotMarkup = (slot, i) => {
    const existing = plan.questions.filter(q => q.rubric && (slot.letter ? q.exam_version === slot.letter : q.assignment_version_id === slot.document)).length;
    return `<section class="rubric-upload-version"><h3>${h.e(slot.title)}</h3><p class="supporting-copy">${existing} question rubric${existing === 1 ? '' : 's'} already uploaded</p>${!slot.document ? h.select(`Printed document for ${slot.title}`, `document-${i}`, [['', 'Choose the printed document'], ...documents.map(d => [d.id, d.label])]) : ''}<label class="field">${slots.length === 1 ? 'Gradescope rubric CSVs' : h.e(`Rubric CSVs for ${slot.title}`)}<input type="file" name="${slots.length === 1 ? 'rubric_files' : `rubric_files-${i}`}" data-rubric-slot="${i}" accept=".csv,text/csv" multiple></label></section>`;
  };
  // A version no seating chart handed out (2026-09-24: a makeup Version C) has no slot until its rubrics arrive: it is added here.
  const nextLetter = () => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').find(letter => !slots.some(slot => slot.letter === letter));
  const form = h.dialog("Upload rubrics", "Add each version’s Gradescope question CSVs in its own upload spot. Existing imports are kept.",
    `<div class="rubric-upload-versions">${slots.map(slotMarkup).join('')}</div>${letters.length ? `<p data-add-version-row>${h.button('add-version', `Add Version ${nextLetter()}`)} <small class="supporting-copy">for a makeup or another version no seating chart handed out</small></p>` : ''}
    <p class="supporting-copy">The rubrics are checked, clarified and approved later, in Key &amp; Rubric Finalization, once the answer areas are confirmed.</p>
    <div data-rubric-choices-slot></div>
    <p class="supporting-copy">Select all question CSVs for each version. Filenames identify the printed question numbers. Blank-exam locations and rubric point values still need your confirmation.</p><ul data-upload-results aria-live="polite"></ul>`,
    'Upload and continue', async (values, current, live) => {
      if (!entries.length) {
        for (const input of current.querySelectorAll('[data-rubric-slot]')) {
          const i = Number(input.dataset.rubricSlot), slot = slots[i], version = slot.document || values.get(`document-${i}`);
          if (input.files.length && !version) throw new Error(`Choose the printed document for ${slot.title}.`);
          entries.push(...[...input.files].map(file => ({file, version, letter: slot.letter, title: slot.title, requestId: crypto.randomUUID(), status: 'Waiting'})));
        }
      }
      if (!entries.length || entries.length > 100) throw new Error('Choose up to 100 Gradescope CSV files across the versions.');
      await choices.save();
      const controls = [...current.querySelectorAll('input, select')];
      controls.forEach(control => { control.disabled = true; });
      let failures = 0;
      try {
        for (const entry of entries) {
          if (!live()) break;
          if (entry.done) continue;
          entry.status = 'Uploading…'; draw();
          const body = new FormData();
          body.set('file', entry.file); body.set('request_id', entry.requestId); body.set('assignment_version_id', entry.version);
          if (entry.letter) body.set('exam_version', entry.letter);
          try {
            const result = await h.api(`/api/grading/assignments/${plan.id}/rubric-uploads`, 'POST', body);
            entry.done = true; changed = true;
            entry.status = result.status === 'already_imported' ? 'Already imported; existing work kept' : result.printed_number ? `Question ${result.printed_number} · ${result.max_points} points · imported` : 'Imported · printed number needs review';
          } catch (error) { entry.status = error.message; failures++; }
          if (live()) draw();
        }
        if (!live()) { if (changed && active()) await refresh(); return; }
        if (failures) throw new Error(`${entries.filter(e => e.done).length} imported; ${failures} failed. Retry continues unfinished files. Successful imports are retained.`);
        if (live()) h.close();
      } finally { controls.forEach(control => { control.disabled = false; }); }
    });
  const choices = choicesFold(form.querySelector('[data-rubric-choices-slot]'), plan.id, h);
  mountNotFinal(form, h, plan.id);   // RECORDINGS "A RULING": a meeting's words that this rubric is not final
  function draw() {
    form.querySelector('[data-upload-results]').replaceChildren(...entries.map(entry => {
      const row = document.createElement('li'); row.textContent = `${entry.title} · ${entry.file.name}: ${entry.status}`; return row;
    }));
  }
  form.querySelectorAll('[data-rubric-slot], select').forEach(input => input.addEventListener('change', () => { entries = []; draw(); }));
  form.querySelector('[data-grading-action="add-version"]')?.addEventListener('click', event => {
    const letter = nextLetter();
    if (!letter) return;
    slots.push({letter, title: `Version ${letter}`, document: documents.length === 1 ? documents[0].id : ''});
    const holder = document.createElement('div');
    holder.innerHTML = slotMarkup(slots[slots.length - 1], slots.length - 1);
    const added = holder.firstElementChild;
    form.querySelector('.rubric-upload-versions').append(added);
    added.querySelectorAll('[data-rubric-slot], select').forEach(input => input.addEventListener('change', () => { entries = []; draw(); }));
    const next = nextLetter();
    if (next) event.currentTarget.textContent = `Add Version ${next}`; else event.currentTarget.closest('[data-add-version-row]').remove();
    added.querySelector('input[type=file]').focus();
  });
  document.querySelector('#dialog').addEventListener('close', () => { if (changed && active()) void refresh(); }, {once: true});
}
