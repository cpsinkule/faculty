// GRADING / FRONTEND: a view of the term plan's retained rubric check. Choices draft feedback; none approves a rubric.
import {mathText, renderMath} from './grading-math.js';
import {rubricDisplay} from './grading-rubric-display.js';
import {copyNote, linkIndex, linkOf, partners, KIND_LABEL} from './grading-rubric-links.js';

// A concern's prose quotes the rubric's own item IDs ("may R7_10 (0.5) be added to R7_11 (2.5)?"). Shown, each becomes its line's
// display label ("Item 10"), as the affected lines beside it are named (grading-setup-17). Only the items of the questions the
// concern names, longest ID first and whole words only; an ID needs a letter and a digit, so prose words and bare numbers stay as
// written. Display only: the stored concern and everything sent back to the reviewer keep the IDs.
export function plainIds(text, questions, ids) {
  if (!text) return text;
  const labels = new Map();
  for (const id of ids || []) {
    const q = questions.find(item => item.question_id === id), items = q?.rubric?.items || [];
    const display = rubricDisplay(q?.rubric);
    for (const item of items) if (/[A-Za-z]/.test(item.id) && /\d/.test(item.id) && !labels.has(item.id)) labels.set(item.id, display.label(item.id));
  }
  if (!labels.size) return text;
  const escape = id => id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`(?<![A-Za-z0-9_.-])(?:${[...labels.keys()].sort((a, b) => b.length - a.length).map(escape).join('|')})(?![A-Za-z0-9_-])`, 'g');
  return String(text).replace(pattern, id => labels.get(id));
}

// LINKED VERSIONS (requirement, 2026-10-02): the reviewer names a finding ONCE for every version it arises on, and the server keeps it
// as one row per version with one `shared` key, so those rows are one decision. A row the instructor chose to decide separately
// (`split`, by its concern number) stands alone. Rows of one key whose drafts, comments or presentation came to differ (after a
// resolution) stay apart, as before. Without a key, only a model's identical `group` presentation within one problem combines.
// BOOKKEEPING IS FIXED, NOT ASKED (requirement, 2026-10-24): a finding Faculty fixed (`handled`, by concern number) is no decision; it
// is listed apart under "Fixed for you" (`fixedGroups`), and Undo makes it a decision again. Concern numbers never change.
export function concernGroups(result, questions, retry = new Map(), split = new Set(), handled = new Set()) {
  const family = new Map((result.families || []).flatMap((members, n) => members.map(id => [id, n])));
  const rows = [...(result.suspected_errors || []).map(issue => ({issue, kind: 'suspected_error', ids: [issue.question_id]})),
    ...(result.consistency_issues || []).map(issue => ({issue, kind: 'consistency', ids: issue.question_ids || []}))];
  const groups = [], byKey = new Map();
  rows.forEach((row, index) => {
    if (handled.has(index)) return;
    const {issue, ids, kind} = row;
    // Only a model's explicit shared-decision key, within a known problem family, can combine findings. Conflicting choices or
    // comments, different problems and two findings on the same version remain separate. Original concern numbers never change.
    const families = [...new Set(ids.map(id => family.get(id)))];
    const key = split.has(index) ? ''
      : issue.shared ? JSON.stringify(['shared', kind, issue.shared, issue.title || '', issue.decision || '', issue.choices || [], retry.get(index) || '', issue.comment || ''])
      : issue.group && families.length === 1 && families[0] !== undefined
      ? JSON.stringify([families[0], kind, issue.group, issue.title || '', issue.decision || '', issue.choices || [], retry.get(index) || '', issue.comment || '']) : '';
    const candidate = key ? byKey.get(key) : null;
    const versions = ids.map(id => questions.find(q => q.question_id === id)?.exam_version);
    const group = candidate && !candidate.rows.some(other => other.ids.some(id => ids.includes(id)
      || versions.includes(questions.find(q => q.question_id === id)?.exam_version))) ? candidate : null;
    const record = {...row, index};
    if (group) group.rows.push(record);
    else {
      const next = {rows: [record]};
      groups.push(next);
      if (key) byKey.set(key, next);
    }
  });
  // ONE QUESTION ONCE (requirement, 2026-10-29): decisions the reviewer gave one `topic` are one decision across the
  // problems, each problem a `part`, one answer reaching every row. A problem the instructor chose to decide separately
  // (`split` holds "apart:<row>") stands alone, as does a row decided alone or one whose drafted answer differs.
  const merged = [], byTopic = new Map();
  for (const group of groups) {
    const first = group.rows[0], topic = first.issue.topic || '';
    const apart = group.rows.some(row => split.has(row.index) || split.has(`apart:${row.index}`));
    const other = topic && !apart ? byTopic.get(topic) : null;
    if (other && (retry.get(other.rows[0].index) || '') === (retry.get(first.index) || '') && !other.rows.some(row => group.rows.some(mine => mine.ids.some(id => row.ids.includes(id))))) {
      other.parts = other.parts || [{rows: [...other.rows]}];
      other.parts.push({rows: [...group.rows]});
      other.rows.push(...group.rows);
      continue;
    }
    merged.push(group);
    if (topic && !apart) byTopic.set(topic, group);
  }
  return merged;
}

// The fixed findings, one entry per finding as the reviewer named it (a shared one once for all its versions), in their order.
export function fixedGroups(result, handled) {
  const groups = [], byKey = new Map();
  (result.suspected_errors || []).forEach((issue, index) => {
    if (!handled.has(index)) return;
    const key = issue.shared || `row:${index}`;
    if (!byKey.has(key)) { const group = {issue, indexes: []}; byKey.set(key, group); groups.push(group); }
    byKey.get(key).indexes.push(index);
  });
  return groups;
}

// `readOnly` (2026-10-11, the quiz draft's page: the same check, made while the quiz is drafted, before there is a grading plan):
// the affected lines show their clarification and exclusions and are not edited here, there is no rubric pop-up, and the one
// action under them is the host's own (`editLabel`, `editQuestion`). The component reads no plan either way: a check's result,
// the questions it names, the guidance as held and the links are all it is given.
export function mountRubricConcerns(host, result, questions, h, options) {
  const {retry, guidance, setClarification, editQuestion, questionLabel, links = {sets: []}, split = new Set(), initial = 0, readOnly = false, editLabel = 'Open in Rubric items',
    handled = new Set(), fixes = {}, undoFix = null, pendingKeys = false, applyEdit = null} = options;
  const groups = concernGroups(result, questions, retry, split, handled), fixed = fixedGroups(result, handled);
  const handle = {};  // what the caller holds: kept current when Undo draws the decisions again
  const all = new Map(questions.map(q => [q.question_id, q]));
  let index = linkIndex(links.sets);  // Read again on each draw: the Rubric items view may change the links meanwhile.
  const family = new Map((result.families || []).flatMap(members => members.map(id => [id, members])));
  const idsOf = group => [...new Set(group.rows.flatMap(row => row.ids))].filter(id => all.has(id));
  const plain = (text, ids) => plainIds(text, questions, ids);
  // A finding the reviewer gave no title is named by its kind; the question it is about is the line under it (2026-10-06: every
  // untitled one read "Concern about Question 18").
  const KIND = {consistency: 'Consistency across versions', suspected_error: 'Possible error in the rubric'};
  const titled = group => Boolean(group.rows[0].issue.title);
  const title = group => plain(group.rows[0].issue.title, idsOf(group)) || KIND[group.rows[0].kind];
  const version = id => all.get(id)?.exam_version || '';
  const listed = names => names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0] || '';
  const versionsText = names => `Version${names.length > 1 ? 's' : ''} ${listed(names)}`;
  const scope = group => {
    if (group.parts) return group.parts.map(part => scope(part)).join('; ');
    const rows = idsOf(group).map(id => all.get(id)), titles = [...new Set(rows.map(q => q.title))];
    const versions = [...new Set(rows.map(q => q.exam_version).filter(Boolean))];
    return titles.length === 1 ? `${titles[0]}${versions.length ? ` · ${versionsText(versions)}` : ''}` : rows.map(questionLabel).join('; ');
  };
  // A decision shared across versions says where it applies, and which of the problem's versions it leaves alone (LINKED VERSIONS).
  const applies = group => {
    const ids = idsOf(group);
    if (group.parts) return `Asked on ${group.parts.map(part => scope(part)).join('; ')}: one answer reaches each, and the reviewer adapts it to each problem’s own lines.`;
    if (!group.rows[0].issue.shared && group.rows.length < 2) return '';
    const named = ids.map(version).filter(Boolean), others = (family.get(ids[0]) || []).filter(id => !ids.includes(id) && all.has(id)).map(version).filter(Boolean);
    return named.length ? `Applies to ${versionsText(named)}: the reviewer adapts your answer to each.${others.length ? ` Not raised on ${versionsText(others)}.` : ''}` : '';
  };
  let selected = Math.max(0, Math.min(groups.length - 1, initial));
  const feedback = group => retry.get(group.rows[0].index) || '';
  // What a fix did, in words: the forms a key fix added, else the reviewer's note.
  const fixText = group => {
    const added = group.indexes.flatMap(n => (fixes[n]?.added || []).map(row => ({row, q: all.get(result.suspected_errors[n].question_id)})));
    const keys = added.filter(({row}) => row.forms?.length).map(({row, q}) => `${q?.exam_version ? `Version ${q.exam_version}’s` : 'The'} key${row.part ? ` (part ${row.part})` : ''} now also accepts ${row.forms.join('; ')}`);
    // A drafted quiz's keys are the draft's until it is approved (2026-10-29): its key fixes are made then, and say so.
    const later = !pendingKeys ? [] : group.indexes.flatMap(n => {
      const issue = result.suspected_errors[n], q = all.get(issue.question_id);
      return issue.fix?.kind === 'key' ? (issue.fix.keys || []).filter(row => row.add?.length).map(row => `${q?.exam_version ? `Version ${q.exam_version}’s` : 'The'} key${row.part ? ` (part ${row.part})` : ''} will also accept ${row.add.join('; ')} once the quiz is approved`) : [];
    });
    return [pendingKeys && later.length ? '' : group.issue.fix?.note || '', ...keys, ...later].filter(Boolean).join('. ');
  };
  // AN EDIT ONLY THE INSTRUCTOR CAN MAKE (2026-10-29): the points change a decision proposes, each line once, as it reads now.
  const editLines = group => {
    const seen = new Set();
    return group.rows.flatMap(row => row.issue.edit || []).filter(line => all.has(line.question_id) && !seen.has(`${line.question_id}\u001f${line.item_id}`) && seen.add(`${line.question_id}\u001f${line.item_id}`))
      .map(line => {
        const q = all.get(line.question_id), item = q.rubric.items.find(row => row.id === line.item_id);
        return item ? {...line, label: `${q.exam_version ? `Version ${q.exam_version} · ` : ''}${q.title} · ${rubricDisplay(q.rubric).label(item.id)}`, from: item.points} : null;
      }).filter(Boolean);
  };
  const fixedMarkup = fixed.length ? `<details class="compute-fold quality-fixed" data-quality-fixed ${groups.length ? '' : 'open'}><summary>Fixed for you · ${fixed.length}</summary><div class="compute-fold-body">
    <p class="supporting-copy">Each had one right answer, from your rubric, comments, choices or earlier answers, so it was not asked. Undo makes it a decision again${undoFix ? ' and takes back what it added to a key' : ''}.</p>
    <ul class="quality-fixed-list">${fixed.map((group, n) => `<li data-fixed-row="${n}"><strong>${h.e(plain(group.issue.title, [group.issue.question_id]) || 'A finding with one right answer')}</strong>
      <small class="supporting-copy">${h.e(scope({rows: [{ids: group.indexes.map(i => result.suspected_errors[i].question_id)}]}))}</small>
      ${fixText(group) ? `<p>${mathText(plain(fixText(group), group.indexes.map(i => result.suspected_errors[i].question_id)), h)}</p>` : ''}
      <button type="button" class="text-button" data-undo-fix="${group.indexes.join(' ')}">${undoFix ? 'Undo' : 'Ask me instead'}</button></li>`).join('')}</ul></div></details>` : '';
  if (!groups.length) {
    // Nothing left to decide: the fixed findings alone, and the Resolve block out of sight (it would send nothing to decide).
    host.innerHTML = `<p class="empty-state" data-no-decisions>Nothing needs a decision.</p>${fixedMarkup}`;
    const respondBlock = host.closest('form')?.querySelector('[data-quality-respond]');
    if (respondBlock) respondBlock.hidden = true;
    bindUndo();
    return Object.assign(handle, {update() {}, count: 0, fixed: fixed.length, choose() {}});
  }
  host.innerHTML = `<div class="quality-decision-layout">
    <aside class="quality-decision-rail"><h4>Decisions</h4>${h.select('Decision to review', 'quality-decision-select', groups.map((group, n) => [String(n), title(group)]))}
      <nav aria-label="Rubric decisions">${groups.map((group, n) => `<button type="button" class="quality-decision-link" data-decision-choice="${n}"><strong>${h.e(title(group))}</strong><small>${h.e(scope(group))}</small><small data-decision-status="${n}"></small></button>`).join('')}</nav>
    </aside>
    <div class="quality-decision-work"><div class="quality-decision-pager"><span class="supporting-copy" data-decision-position></span><div>${h.button('previous-decision', '← Previous decision')}${h.button('next-decision', 'Next decision →')}</div></div>
      ${groups.map((group, n) => {
        const issue = group.rows[0].issue, choices = issue.choices || [], ids = idsOf(group), where = applies(group);
        const notes = group.rows.filter(row => row.issue.version_note);
        return `<section class="quality-decision" data-decision-panel="${n}" ${n !== selected ? 'hidden' : ''}>
          <p class="supporting-copy">${group.rows[0].kind === 'consistency' && titled(group) ? 'Consistency across versions · ' : ''}${h.e(scope(group))}</p>
          <h3>${h.e(title(group))}</h3>${where ? `<p class="supporting-copy" data-decision-applies>${h.e(where)}</p>` : ''}
          ${notes.length ? `<ul class="quality-version-notes" data-version-notes>${notes.map(row => `<li><strong>Version ${h.e(version(row.ids[0]))}:</strong> ${mathText(plain(row.issue.version_note, row.ids), h)}</li>`).join('')}</ul>` : ''}
          <div class="quality-decision-focus"><div class="quality-decision-policy">
            ${issue.decision ? `<p class="quality-decision-prompt">${mathText(plain(issue.decision, ids), h)}</p>` : `<p class="quality-decision-prompt">${mathText(plain(issue.message, ids), h)}</p>`}
            ${choices.length ? `<fieldset class="quality-policy-choices"><legend>Your answer</legend>${choices.map((choice, c) => `<label class="quality-policy-choice"><input type="radio" name="decision-policy-${n}" value="${c}" data-policy-choice="${n}" aria-label="${h.e(plain(choice.label, ids))}" ${choice.consequence ? `aria-describedby="policy-consequence-${n}-${c}"` : ''}><span>${mathText(plain(choice.label, ids), h)}</span>${choice.consequence ? `<small id="policy-consequence-${n}-${c}">${mathText(plain(choice.consequence, ids), h)}</small>` : ''}</label>`).join('')}<label class="quality-policy-choice"><input type="radio" name="decision-policy-${n}" value="custom" data-policy-choice="${n}"><span>Write my own feedback</span></label></fieldset>`
              : '<p class="supporting-copy" data-no-policy-choices>No preset answers. Write your policy below, or leave it empty and the reviewer settles it.</p>'}
            <div class="quality-feedback-box" data-feedback-box="${n}" ${choices.length && !feedback(group) ? 'hidden' : ''}>${h.area('Feedback to the reviewer', `decision-feedback-${n}`, feedback(group), `rows="4" maxlength="4000" data-decision-feedback="${n}"`)}
            <p class="supporting-copy" role="status" aria-live="polite" data-feedback-state="${n}"></p></div>
            ${applyEdit && editLines(group).length ? `<div class="quality-edit-offer" data-edit-offer="${n}"><p><strong>The change you asked for:</strong> ${h.e(editLines(group).map(line => `${line.label}: ${line.from} → ${line.points} points`).join('; '))}.</p>
              <div class="grading-actions"><button type="button" class="button" data-apply-edit="${group.rows.filter(row => row.issue.edit?.length).map(row => row.index).join(' ')}">Approve this change</button></div></div>` : ''}
            ${group.parts ? `<details class="compute-why" data-topic-parts><summary>How it is asked on each problem</summary>${group.parts.map(part => `<p><strong>${h.e(scope(part))}:</strong> ${mathText(plain(part.rows[0].issue.decision || part.rows[0].issue.message, idsOf(part)), h)}</p>`).join('')}
              <p class="supporting-copy">A problem needs a different answer?</p><div class="grading-actions">${group.parts.map(part => `<button type="button" class="button" data-split-part="${part.rows.map(row => row.index).join(' ')}">Decide ${h.e(scope(part))} separately</button>`).join('')}</div></details>` : ''}
            <div data-decision-actions></div>
            ${!group.parts && group.rows.length > 1 && new Set(group.rows.map(row => version(row.ids[0]))).size === group.rows.length ? `<details class="compute-why" data-split-versions><summary>A version needs a different answer?</summary><p class="supporting-copy">Take one version out of this decision to give it its own feedback. Nothing is sent until you resolve the concerns.</p><div class="grading-actions">${group.rows.map(row => `<button type="button" class="button" data-split-row="${row.index}">Decide Version ${h.e(version(row.ids[0]))} separately</button>`).join('')}</div></details>` : ''}
            ${group.rows.map(row => `<textarea name="concern-comment-${row.index}" data-concern-comment="${row.index}" hidden>${h.e(feedback(group))}</textarea>`).join('')}
            ${issue.note ? `<p class="supporting-copy">Still open: ${mathText(plain(issue.note, ids), h)}</p>` : ''}
            ${group.rows.some(row => row.issue.comment) ? `<details class="compute-why"><summary>Your earlier feedback</summary>${group.rows.filter(row => row.issue.comment).map(row => `<p>${h.e(row.ids.map(id => questionLabel(all.get(id))).join('; '))}: ${mathText(row.issue.comment, h)}</p>`).join('')}</details>` : ''}
            <details class="compute-why" data-decision-explanation><summary>Full reviewer explanation</summary>${(group.rows[0].issue.shared ? group.rows.slice(0, 1) : group.rows).map(row => `<div><p class="supporting-copy">${h.e((group.rows[0].issue.shared ? ids : row.ids).map(id => all.has(id) ? questionLabel(all.get(id)) : '').join('; '))}</p><p>${mathText(plain(row.issue.message, row.ids), h)}</p>${row.issue.note ? `<p>Still open: ${mathText(plain(row.issue.note, row.ids), h)}</p>` : ''}</div>`).join('')}</details>
          </div><aside class="quality-decision-reference" aria-label="Affected rubric lines" data-decision-reference="${n}"></aside></div>
        </section>`;
      }).join('')}
    </div></div>${fixedMarkup}`;
  const areaFor = n => host.querySelector(`[data-decision-feedback="${n}"]`);
  const menu = host.querySelector('[name="quality-decision-select"]');
  // The Resolve block (its button, what it sends, how it works) sits beside the decision on screen; it sends every decision at once.
  const respond = host.closest('form').querySelector('[data-quality-respond]');
  const currentGroup = () => groups[selected];
  // The lines a decision names on one question: a suspected error's own items, a consistency finding's items on that version.
  const namedOn = (group, qid) => new Set(group.rows.filter(row => row.ids.includes(qid))
    .flatMap(row => row.kind === 'consistency' ? row.issue.lines?.[qid] || [] : row.issue.item_ids || []));
  const lineName = line => `Version ${version(line.question_id)} · ${rubricDisplay(all.get(line.question_id).rubric).label(line.item_id)}`;

  function update() {
    let answered = 0;
    groups.forEach((group, n) => {
      const hasDraft = Boolean(areaFor(n).value.trim());
      if (hasDraft) answered += 1;
      host.querySelector(`[data-decision-status="${n}"]`).textContent = hasDraft ? 'Answered' : group.rows.some(row => row.issue.note) ? 'Still open' : '';
      host.querySelector(`[data-feedback-state="${n}"]`).textContent = hasDraft ? 'Drafted, not sent yet: it goes to the reviewer when you resolve the concerns.' : '';
    });
    const told = respond?.querySelector('[data-resolve-scope]');
    if (told) told.textContent = `Sends all ${groups.length} decision${groups.length === 1 ? '' : 's'} to the reviewer${answered ? `, with your answer${answered === 1 ? '' : 's'} on ${answered}` : ''}; it settles ${answered ? 'the others' : 'them'} itself. Nothing is approved until you approve.`;
    const reference = host.querySelector(`[data-decision-reference="${selected}"]`);
    if (!reference) return;
    reference.querySelectorAll('[data-reference-row]').forEach(row => {
      const area = row.querySelector('[data-reference-note]');  // none on a read-only line
      const now = guidance(row.dataset.referenceRow), note = now?.clarifications?.[row.dataset.referenceItem] || '';
      if (area && document.activeElement !== area) area.value = note;
      // The clarification shows on the closed line, as in Rubric items; drawn again only when it changed.
      const shown = row.querySelector('[data-reference-guidance]');
      if (shown.dataset.drawn !== note) {
        shown.dataset.drawn = note;
        shown.classList.toggle('is-empty', !note);
        shown.innerHTML = note ? `<b>Clarification</b> ${mathText(note, h)}` : 'No clarification';
        renderMath(shown);
      }
      const copy = row.querySelector('[data-copy-reference]');
      if (copy) copy.disabled = !note;  // copying nothing would only clear the other versions' own
    });
    reference.querySelectorAll('[data-reference-exclusions]').forEach(node => {
      const q = all.get(node.dataset.referenceQuestion), now = guidance(q.question_id), id = node.dataset.referenceExclusions, rule = q.builtin?.find(row => row.item_id === id);
      const apart = now.pairs.filter(pair => pair.includes(id)).map(pair => pair.find(other => other !== id));
      node.textContent = rule ? `${rule.rule === 'full_credit' ? 'Full-credit' : 'No-credit'} line: excludes every other item`
        : apart.length ? `Cannot combine with items ${rubricDisplay(q.rubric).list(apart)}` : 'No exclusions';
    });
  }
  // Every version a decision names, at once (LINKED VERSIONS, 2026-10-02: one version at a time behind tabs meant editing the same
  // line once per version). Lines linked across the versions are named once, then one row per version with its clarification and
  // exclusions, as in Rubric items (2026-10-06); opening a row edits that version's own field in the policy form.
  function drawReference() {
    const group = currentGroup(), ids = idsOf(group), reference = host.querySelector(`[data-decision-reference="${selected}"]`);
    if (!group || !reference) return;
    index = linkIndex(links.sets);
    if (!ids.length) { reference.innerHTML = '<p class="empty-state">This concern names no specific rubric lines. Review the complete rubric under Rubric items.</p>'; return; }
    const named = new Set(ids.flatMap(id => [...namedOn(group, id)]));
    const lines = ids.flatMap(id => all.get(id).rubric.items.filter(item => namedOn(group, id).has(item.id)).map(item => ({question_id: id, item_id: item.id})));
    const blocks = [], blockOf = new Map();
    for (const line of lines) {
      const set = linkOf(index, line.question_id, line.item_id), key = set && !set.conflict ? `set${set.index}` : `${line.question_id}\u001f${line.item_id}`;
      if (!blockOf.has(key)) { const block = {set: key.startsWith('set') ? set : null, lines: []}; blockOf.set(key, block); blocks.push(block); }
      blockOf.get(key).lines.push(line);
    }
    const whole = ids.filter(id => !namedOn(group, id).size);
    const row = (line, head) => {
      const q = all.get(line.question_id), item = q.rubric.items.find(row => row.id === line.item_id), display = rubricDisplay(q.rubric);
      const copies = partners(index, q.question_id, item.id).filter(other => all.has(other.question_id));
      const own = `${display.label(item.id)} · ${item.points} points`, differs = String(head.description) !== String(item.description);
      const label = version(q.question_id) ? `Version ${version(q.question_id)}` : own;
      const closed = `<span class="quality-reference-row-label"><strong>${h.e(label)}</strong>${label !== own && head.label !== own ? ` <span class="supporting-copy">${h.e(own)}</span>` : ''}</span>${differs ? `<span class="quality-reference-wording">${mathText(item.description, h)}</span>` : ''}<span class="quality-item-guidance" data-reference-guidance></span><small class="supporting-copy" data-reference-exclusions="${h.e(item.id)}" data-reference-question="${h.e(q.question_id)}"></small>`;
      // Read only: the line as it stands, nothing to open (the guidance changes through the answers, or later in Rubric items).
      if (readOnly) return `<div class="quality-reference-guidance" data-reference-row="${h.e(q.question_id)}" data-reference-item="${h.e(item.id)}">${closed}</div>`;
      return `<details class="quality-reference-guidance" data-reference-row="${h.e(q.question_id)}" data-reference-item="${h.e(item.id)}"><summary>${closed}</summary>
        <div class="quality-reference-edit">${q.comments?.[item.id] ? `<p class="supporting-copy"><strong>Your comment:</strong> ${mathText(q.comments[item.id], h)}</p>` : ''}
        ${h.area('Clarification for the AI graders (optional)', `reference-note-${selected}-${q.question_id}-${item.id}`, guidance(q.question_id).clarifications?.[item.id] || '', `maxlength="2000" rows="3" data-reference-note="${h.e(item.id)}" data-reference-question="${h.e(q.question_id)}"`)}
        ${copies.length ? `<div class="grading-actions"><button type="button" class="button" data-copy-reference="${h.e(q.question_id)}" data-copy-item="${h.e(item.id)}">Copy this clarification to ${h.e(versionsText(copies.map(other => version(other.question_id))))}</button></div><p class="supporting-copy" role="status" aria-live="polite" data-copy-status></p>` : ''}</div></details>`;
    };
    reference.innerHTML = `<p class="quality-reference-label">Affected rubric lines</p>${blocks.map(block => {
      const first = all.get(block.lines[0].question_id), item = first.rubric.items.find(row => row.id === block.lines[0].item_id);
      const head = {label: `${rubricDisplay(first.rubric).label(item.id)} · ${item.points} points`, description: item.description};
      // A linked line's copies on versions this decision does not name are named, never drawn.
      const elsewhere = block.set ? block.set.lines.filter(line => all.has(line.question_id) && !block.lines.some(own => own.question_id === line.question_id && own.item_id === line.item_id)) : [];
      const link = block.set ? `${KIND_LABEL[block.set.kind] || ''}${elsewhere.length ? `; also ${elsewhere.map(lineName).join(', ')}` : ''}` : '';
      return `<section class="quality-reference-line"><p class="quality-reference-head"><strong>${h.e(head.label)}</strong>${link ? ` <span class="supporting-copy" data-reference-link>${h.e(link)}</span>` : ''}</p><div class="quality-reference-wording">${mathText(item.description, h)}</div>${block.lines.map(line => row(line, head)).join('')}</section>`;
    }).join('')}${whole.length ? `<p class="supporting-copy" data-reference-whole>It concerns the whole rubric of ${h.e(scope({rows: [{ids: whole}]}))}.</p>` : ''}
      <div class="grading-actions">${readOnly ? '' : `<button type="button" class="text-button quality-rubric-link" data-show-rubric="${h.e(ids.join(' '))}" data-rubric-items="${h.e([...named].join(' '))}">${ids.length > 1 ? 'Show rubrics' : 'Show rubric'}</button>`}<button type="button" class="text-button" data-edit-quality-question="${h.e(ids[0])}" data-edit-item="${h.e([...namedOn(group, ids[0])][0] || '')}">${h.e(editLabel)}</button></div>`;
    reference.querySelectorAll('[data-reference-note]').forEach(area => area.addEventListener('input', () => setClarification(area.dataset.referenceQuestion, area.dataset.referenceNote, area.value)));
    reference.querySelectorAll('[data-edit-quality-question]').forEach(button => button.onclick = () => editQuestion(button.dataset.editQualityQuestion, button.dataset.editItem || undefined));
    // Copying is explicit and only along "same" links: the text lands in each linked version's own field, where it can be edited.
    reference.querySelectorAll('[data-copy-reference]').forEach(button => button.onclick = () => {
      const qid = button.dataset.copyReference, iid = button.dataset.copyItem, text = guidance(qid)?.clarifications?.[iid] || '';
      const targets = partners(index, qid, iid).filter(line => all.has(line.question_id));
      targets.forEach(line => setClarification(line.question_id, line.item_id, text));
      button.closest('details').querySelector('[data-copy-status]').textContent = copyNote(targets.map(lineName), text);
      update();
    });
    update(); renderMath(reference);
  }
  function choose(n) {
    selected = Math.max(0, Math.min(groups.length - 1, n));
    menu.value = String(selected);
    host.querySelectorAll('[data-decision-panel]').forEach(panel => { panel.hidden = Number(panel.dataset.decisionPanel) !== selected; });
    host.querySelectorAll('[data-decision-choice]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.decisionChoice) === selected)));
    host.querySelector('[data-decision-position]').textContent = `Decision ${selected + 1} of ${groups.length}`;
    host.querySelector('[data-grading-action="previous-decision"]').disabled = selected === 0;
    host.querySelector('[data-grading-action="next-decision"]').disabled = selected === groups.length - 1;
    if (respond) host.querySelector(`[data-decision-panel="${selected}"] [data-decision-actions]`).append(respond);
    drawReference();
  }
  host.querySelectorAll('[data-decision-choice]').forEach(button => button.onclick = () => choose(Number(button.dataset.decisionChoice)));
  menu.addEventListener('change', () => choose(Number(menu.value)));
  host.querySelector('[data-grading-action="previous-decision"]').onclick = () => choose(selected - 1);
  host.querySelector('[data-grading-action="next-decision"]').onclick = () => choose(selected + 1);
  groups.forEach((group, n) => {
    const area = areaFor(n);
    area.addEventListener('input', () => {
      group.rows.forEach(row => { host.querySelector(`[data-concern-comment="${row.index}"]`).value = area.value; });
      const choice = host.querySelector(`[name="decision-policy-${n}"]:checked`);
      if (choice && choice.value !== 'custom' && area.value !== group.rows[0].issue.choices[Number(choice.value)].feedback) {
        host.querySelector(`[name="decision-policy-${n}"][value="custom"]`).checked = true;
      }
      update();
    });
  });
  host.querySelectorAll('[data-policy-choice]').forEach(input => input.addEventListener('change', () => {
    const n = Number(input.dataset.policyChoice), area = areaFor(n);
    host.querySelector(`[data-feedback-box="${n}"]`).hidden = false;  // the answer's words, to edit before they are sent
    if (input.value !== 'custom') {
      area.value = groups[n].rows[0].issue.choices[Number(input.value)].feedback;
      area.dispatchEvent(new Event('input'));
    } else area.focus();
  }));
  // Deciding a version separately draws the decisions again with that row on its own; every draft is kept (as each row's draft),
  // and the Resolve button is put back where it came from first so it survives the redraw.
  host.querySelectorAll('[data-split-row]').forEach(button => button.onclick = () => {
    groups.forEach((group, n) => group.rows.forEach(row => {
      const text = areaFor(n).value;
      if (text.trim()) retry.set(row.index, text); else retry.delete(row.index);
    }));
    const row = Number(button.dataset.splitRow);
    split.add(row);
    if (respond && host.contains(respond)) host.after(respond);
    const fresh = mountRubricConcerns(host, result, questions, h, {...options, split, initial: selected});
    Object.assign(api, fresh);
    const alone = concernGroups(result, questions, retry, split, handled).findIndex(group => group.rows.length === 1 && group.rows[0].index === row);
    if (alone >= 0) fresh.choose(alone);
    options.onRedraw?.();
  });
  // Deciding a problem separately (ONE QUESTION ONCE): its rows leave the shared decision, every draft kept, as for a version.
  host.querySelectorAll('[data-split-part]').forEach(button => button.onclick = () => {
    groups.forEach((group, n) => group.rows.forEach(row => {
      const text = areaFor(n).value;
      if (text.trim()) retry.set(row.index, text); else retry.delete(row.index);
    }));
    const rows = button.dataset.splitPart.split(' ').map(Number);
    rows.forEach(row => split.add(`apart:${row}`));
    if (respond && host.contains(respond)) host.after(respond);
    const fresh = mountRubricConcerns(host, result, questions, h, {...options, split, initial: selected});
    Object.assign(api, fresh);
    const alone = concernGroups(result, questions, retry, split, handled).findIndex(group => group.rows.some(row => rows.includes(row.index)));
    if (alone >= 0) fresh.choose(alone);
    options.onRedraw?.();
  });
  host.querySelectorAll('[data-apply-edit]').forEach(button => button.onclick = async () => {
    button.disabled = true;
    try {
      await applyEdit(button.dataset.applyEdit.split(' ').map(Number), editLines(groups[Number(button.closest('[data-edit-offer]').dataset.editOffer)]));
    } catch (error) {
      button.disabled = false;
      h.toast(error.message, true);
    }
  });
  choose(selected);
  bindUndo();
  const api = Object.assign(handle, {update, count: groups.length, fixed: fixed.length, choose});
  return api;

  // Undo a fix: the host's own step first (the plan's window asks the server, which takes back what a key fix added), then the
  // decisions are drawn again with it among them; every draft is kept, as when a version is decided separately.
  function bindUndo() {
    host.querySelectorAll('[data-undo-fix]').forEach(button => button.onclick = async () => {
      const indexes = button.dataset.undoFix.split(' ').map(Number);
      button.disabled = true;
      try {
        if (undoFix) await undoFix(indexes);
      } catch (error) {
        button.disabled = false;
        h.toast(error.message, true);
        return;
      }
      host.querySelectorAll('[data-concern-comment]').forEach(area => {
        if (area.value.trim()) retry.set(Number(area.dataset.concernComment), area.value); else retry.delete(Number(area.dataset.concernComment));
      });
      const respondBlock = host.closest('form')?.querySelector('[data-quality-respond]');
      if (respondBlock) { respondBlock.hidden = false; if (host.contains(respondBlock)) host.after(respondBlock); }
      const rest = new Set([...handled].filter(n => !indexes.includes(n)));
      const fresh = mountRubricConcerns(host, result, questions, h, {...options, handled: rest});
      Object.assign(handle, fresh);
      const shown = concernGroups(result, questions, retry, split, rest).findIndex(group => group.rows.some(row => indexes.includes(row.index)));
      if (shown >= 0) fresh.choose(shown);
      options.onFixUndone?.(fresh, rest);
      options.onRedraw?.();
    });
  }
}
