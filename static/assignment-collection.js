// ASSIGNMENT-WORKSPACE / EXAMS: workspace-owned view of the exam's collection, with no copied teaching state.
import {patchChildren} from './dom-patch.js';
import {viewLink} from './file-viewer.js';
import {errorMessage, registerLeaveGuard, runFormAction} from './ui.js';

// The owner sends collection once, under reconciliation.students; join by both permanent IDs.
export function collectionStudents(exam) {
  const byId = new Map((exam.reconciliation?.students || []).map(person => [person.section_id + ':' + person.enrollment_id, person]));
  return (exam.participants || []).map(person => ({...person, collection: byId.get(person.section_id + ':' + person.enrollment_id) || {}}));
}

/**
 * COLLECT'S ACTION IS IN THE ROW (ASSIGNMENT-WORKSPACE, 2026-10-11), replaced by COMPACT COLLECT: Collect is a filtered list and one
 * selected student's detail, whose expectation form is always shown, so no row holds a decision open or folded. This is a pure
 * classification of the students Collect's lead counts as "without a scanned paper": still expected, with the status `no_scan`,
 * `partial` or `missing_pages`, once any paper is back. The contract names it and tests/test_assignment_workspace.mjs pins it; the
 * list does not call it (the first selection and "Needs you" use `collectionNeedsAttention`).
 * @param {any} person a participant with its `collection` (`collectionStudents`)
 */
export const decidesInRow = (person, back = true) => back && person.expectation === 'expected' && ['no_scan', 'partial', 'missing_pages'].includes(person.collection?.status);

/**
 * NOTHING SCANNED YET (ASSIGNMENT-WORKSPACE, 2026-10-11): whether any student's paper has come back. Until one has, nobody is
 * "without a scanned paper" one by one: the scans are still to upload, so the heading reads "No papers scanned yet" and
 * `collectionNeedsAttention` marks nobody for a missing paper. Pure.
 * @param {any[]} students `collectionStudents`
 */
export const papersBack = students => students.some(person => ['complete', 'partial', 'needs_review', 'missing_pages'].includes(person.collection?.status));

// THE ZEROS ARE SAID AT CERTIFY (ASSIGNMENT-WORKSPACE, requirement, 2026-11-05): no scanned paper is not a flag, so `no_scan` needs nobody here.
export const collectionNeedsAttention = (person, back) => Boolean(person.collection?.issues?.length)
  || (back && !['complete', 'absent', 'excused', 'no_scan'].includes(person.collection?.status));
export function filterCollection(students, {query = '', section = '', status = ''} = {}) {
  const back = papersBack(students), words = query.trim().toLocaleLowerCase();
  return students.filter(person => (!words || `${person.name} ${person.section_number}`.toLocaleLowerCase().includes(words))
    && (!section || person.section_id === section)
    && (!status || (status === 'attention' ? collectionNeedsAttention(person, back) : person.collection?.status === status)));
}

export function createCollectionReview({api, escape: e, toast}) {
  let host = null, eventId = '', active = () => false, generation = 0, exam = null, selected = '', readError = '';
  let filters = {query: '', section: '', status: ''};
  const drafts = new Map(); // Workspace-owned browser drafts, keyed by permanent membership; never teaching records.
  const dirty = () => active() && drafts.size > 0;
  registerLeaveGuard(() => dirty() ? 'Leave collection? The expectation and reason you changed are not saved.' : null);
  const enc = encodeURIComponent;
  const names = {no_scan: 'No scanned paper', partial: 'Incomplete paper', missing_pages: 'Missing pages', needs_review: 'Scan needs review', complete: 'Complete paper', absent: 'Absent', excused: 'Excused'};
  const keyOf = person => person.section_id + ':' + person.enrollment_id;
  const people = () => exam ? collectionStudents(exam) : [];
  function row(person) {
    const c = person.collection || {}, copies = c.copies || [];
    const jobs = [...new Set(copies.flatMap(copy => copy.job_ids || []))];
    const file = c.assembly_revision_id ? `<a class="link-arrow" ${viewLink('/api/documents/assemblies/' + enc(c.assembly_revision_id) + '/download', 'Scanned paper', e)}>Preview paper</a>` : '';
    const draft = drafts.get(keyOf(person)), expectation = draft?.expectation ?? person.expectation, reason = draft?.reason ?? person.reason ?? '';
    return `<article class="collection-student" data-patch-key="${e(keyOf(person))}"><div><h3>${e(person.name)}</h3><span>Section ${e(person.section_number)} · ${e(names[c.status] || c.status || 'Not checked')}</span>
      ${c.missing_pages?.length ? `<p>Missing pages: ${e(c.missing_pages.join(', '))}</p>` : ''}
      ${(c.issues || []).map(issue => `<p>${e(typeof issue === 'string' ? issue : issue.message || issue.kind)}</p>`).join('')}
      <div class="assignment-links">${file}${jobs.map((id, i) => `<a class="link-arrow" href="#inbox/job/${enc(id)}">Review scan${jobs.length > 1 ? ' ' + (i + 1) : ''}</a>`).join('')}</div></div>
      <form data-collection-student="${e(person.enrollment_id)}" data-section="${e(person.section_id)}" data-version="${draft?.version ?? person.version}">
      <label class="field">Expectation<select name="expectation">${['expected','absent','excused'].map(value => `<option value="${value}" ${value === expectation ? 'selected' : ''}>${value === 'expected' ? 'Paper expected' : names[value]}</option>`).join('')}</select></label>
      <label class="field">Reason<textarea name="reason" rows="3" maxlength="2000">${e(reason)}</textarea></label>
      <p class="form-error" role="alert" hidden></p><div class="assignment-links"><button class="button" type="submit" ${draft ? '' : 'disabled'}>Save expectation</button><button class="text-button" type="button" data-collection-discard ${draft ? '' : 'disabled'}>Discard changes and reload</button></div></form></article>`;
  }
  function list() {
    const students = people(), shown = filterCollection(students, filters), back = papersBack(students);
    patchChildren(host.querySelector('[data-collection-list]'), shown.map(person => `<li data-patch-key="${e(keyOf(person))}"><button type="button" class="collection-choice" data-collection-choice="${e(keyOf(person))}" aria-pressed="${keyOf(person) === selected}" aria-controls="collection-selected-detail"${collectionNeedsAttention(person, back) ? ' data-needs' : ''}>
      <strong>${e(person.name)}</strong><span>Section ${e(person.section_number)} · ${e(names[person.collection?.status] || 'Not checked')}</span>${drafts.has(keyOf(person)) ? '<small>Unsaved changes</small>' : ''}</button></li>`).join('') || '<li>No students match these filters.</li>');
    host.querySelector('[data-collection-count]').textContent = `${shown.length} of ${students.length} students shown${drafts.size ? ` · ${drafts.size} unsaved` : ''}`;
    host.querySelector('[data-collection-outside]').hidden = !selected || shown.some(person => keyOf(person) === selected);
    host.toggleAttribute('data-collection-dirty', drafts.size > 0);
  }
  function details() {
    const person = people().find(person => keyOf(person) === selected);
    host.querySelector('[data-collection-detail]').innerHTML = person ? row(person) : '<p>Select a student to review their paper and expectation.</p>';
  }
  function draw(replaceDetails = true) {
    const students = people(), back = papersBack(students);
    if (!host.querySelector('[data-collection-list]')) {
      const sections = [...new Map(students.map(person => [person.section_id, person.section_number])).entries()];
      host.innerHTML = `<div data-collection-read-error></div><h3 data-collection-heading></h3><p data-collection-caution></p>
        <div class="collection-filters"><div class="field"><label for="collection-query">Find a student</label><input id="collection-query" type="search" data-collection-filter="query" placeholder="Name or section"></div>
        <div class="field"><label for="collection-section">Section</label><select id="collection-section" data-collection-filter="section"><option value="">All sections</option>${sections.map(([id, number]) => `<option value="${e(id)}">Section ${e(number)}</option>`).join('')}</select></div>
        <div class="field"><label for="collection-status">Papers</label><select id="collection-status" data-collection-filter="status"><option value="">All students</option><option value="attention">Needs you</option>${Object.entries(names).map(([key, name]) => `<option value="${key}">${e(name)}</option>`).join('')}</select></div></div>
        <p class="supporting-copy" data-collection-count role="status"></p><div class="collection-workspace"><ul class="collection-list" data-collection-list aria-label="Students"></ul>
        <section class="collection-detail" id="collection-selected-detail" aria-label="Selected student"><p class="supporting-copy" data-collection-outside hidden>The selected student is outside these filters. Their details and edits stay open.</p><div data-collection-detail></div></section></div>`;
      host.querySelectorAll('[data-collection-filter]').forEach(node => { node.value = filters[node.dataset.collectionFilter]; });
    }
    host.querySelector('[data-collection-heading]').textContent = !back ? 'No papers scanned yet' : students.some(person => collectionNeedsAttention(person, back)) ? 'Students needing attention' : 'Students';
    host.querySelector('[data-collection-caution]').textContent = back
      ? 'Resolve only the flagged papers; the others continue automatically. A missing paper alone is not an absence or an excuse, and recording one changes no grade.'
      : 'Upload the scans first. A student you already know was absent or excused can be recorded now; recording one changes no grade.';
    host.querySelector('[data-collection-read-error]').innerHTML = readError ? '<p role="alert">Collection could not be read. Your displayed records and edits are kept.</p><button type="button" class="button" data-collection-retry>Try again</button>' : '';
    if (!selected && students.length) selected = keyOf(students.find(person => collectionNeedsAttention(person, back)) || students[0]);
    list();
    if (replaceDetails || !host.querySelector('.collection-student')) details();
  }
  async function show(node, id, isActive, onChanged) {
    if (eventId !== id) { exam = null; selected = ''; drafts.clear(); filters = {query: '', section: '', status: ''}; }
    host = node; eventId = id; active = isActive;
    const request = ++generation, here = node;
    const currentView = () => isActive() && host === here && eventId === id;
    try {
      const current = await api('/api/exams/' + enc(id));
      if (!currentView() || generation !== request) return;
      const first = !exam;
      exam = current; readError = '';
      if (first && people().some(person => collectionNeedsAttention(person, papersBack(people())))) filters.status = 'attention';
      draw(!drafts.has(selected) && !host.querySelector('[data-collection-detail]')?.contains(document.activeElement));
    } catch (cause) {
      if (!currentView() || generation !== request) return;
      readError = errorMessage(cause);
      if (exam) draw(false);
      else host.innerHTML = '<p role="alert">Collection could not be read.</p><button type="button" class="button" data-collection-retry>Try again</button>';
    }
    host.oninput = host.onchange = event => {
      const filter = event.target.dataset.collectionFilter;
      if (filter) { filters[filter] = event.target.value; list(); return; }
      const form = event.target.closest('form'); if (!form) return;
      const person = people().find(person => keyOf(person) === selected);
      const draft = {version: Number(form.dataset.version), expectation: form.elements.expectation.value, reason: form.elements.reason.value};
      if (draft.expectation === person.expectation && draft.reason === (person.reason || '')) drafts.delete(selected); else drafts.set(selected, draft);
      form.querySelectorAll('button').forEach(button => { button.disabled = !drafts.has(selected); });
      list();
    };
    host.onclick = event => {
      if (event.target.closest('[data-collection-retry]')) { void show(host, eventId, active, onChanged); return; }
      const choice = event.target.closest('[data-collection-choice]');
      if (choice) { selected = choice.dataset.collectionChoice; list(); details(); return; }
      if (!event.target.closest('[data-collection-discard]')) return;
      const form = event.target.closest('form'), key = selected, kept = drafts.get(key);
      void runFormAction(form, async () => {
        generation += 1;
        let current;
        try { current = await api('/api/exams/' + enc(id)); }
        catch (cause) { throw new Error(`This student could not be read again, so nothing was discarded. ${errorMessage(cause)}`); }
        if (!currentView()) return;
        generation += 1;
        exam = current;
        if (drafts.get(key) === kept) drafts.delete(key);
        draw(!drafts.has(selected));
        await onChanged();
      });
    };
    host.onsubmit = event => {
      const form = event.target.closest('[data-collection-student]'); if (!form) return;
      event.preventDefault();
      const key = selected, kept = drafts.get(key);
      void runFormAction(form, async () => {
        generation += 1;
        const saved = await api(`/api/exams/${enc(id)}/participants/${enc(form.dataset.section)}/${enc(form.dataset.collectionStudent)}`, 'PATCH',
          {version: Number(form.dataset.version), expectation: form.elements.expectation.value, reason: form.elements.reason.value});
        if (!currentView()) return;
        generation += 1;
        exam = saved;
        if (drafts.get(key) === kept) drafts.delete(key);
        else if (drafts.has(key)) drafts.get(key).version = people().find(person => keyOf(person) === key)?.version;
        toast('Expectation saved.'); draw(!drafts.has(selected)); await onChanged();
      });
    };
  }
  return {show};
}
