// Presentation only: an item's position in this question's complete rubric is
// its display number. Never infer it from the ID or renumber a selected subset.
// Callers retain original IDs in form values, requests, grouping and audit data.
export function rubricDisplay(rules) {
  const positions = new Map((rules?.items || []).map((item, index) => [item.id, index + 1]));
  const number = id => positions.get(id) ?? '?';
  const label = id => positions.has(id) ? `Item ${number(id)}` : 'Unavailable rubric item';
  const ordered = (items, id = item => item.id) => [...items].sort((a, b) =>
    (positions.get(id(a)) ?? Infinity) - (positions.get(id(b)) ?? Infinity));
  const list = (ids, separator = ', ') => ordered(ids, id => id).map(number).join(separator);
  return {number, label, ordered, list};
}

// GRADING "PART CAPS" (requirement, 2026-10-15): a deducting rubric's lettered parts each lose at most their worth, so a part never
// takes points another part earned. " · Part A loses at most 0.5 · Part B at most 1.5", or "" for a rubric without caps.
export function partCapsText(rules) {
  const caps = (rules?.groups || []).map(g => [/^part-([a-h])-cap$/.exec(g.id || ''), g]).filter(([match]) => match);
  return caps.map(([match, g], index) => ` · Part ${match[1].toUpperCase()} ${index ? 'at most' : 'loses at most'} ${-g.floor}`).join('');
}

// A rubric's import notes, worded for the instructor (grading-setup-10): a note that starts with one of this rubric's item IDs
// ("R7: review the conditional rule…") names the line by its display label instead, as the line above it does. Until the rubric
// is approved each note is a warning to act on; once approved they are provenance, folded under one disclosure.
export function warningsMarkup(q, h) {
  const rules = q?.rubric?.rules, list = rules?.warnings || [];
  if (!list.length) return '';
  const display = rubricDisplay(rules), known = new Set((rules.items || []).map(item => item.id));
  const worded = list.map(text => String(text).replace(/^([A-Za-z0-9_.-]+):\s/, (whole, id) => known.has(id) ? `${display.label(id)}: ` : whole));
  if (!q.rubric.approved_at) return worded.map(text => `<p class="grading-warning">${h.e(text)}</p>`).join('');
  return `<details class="grading-tools grading-import-notes" data-import-notes><summary>Import notes (${worded.length})</summary>${worded.map(text => `<p class="supporting-copy">${h.e(text)}</p>`).join('')}</details>`;
}
