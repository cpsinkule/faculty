// LINKED VERSIONS (GRADING, requirement, 2026-10-02): which rubric lines of one problem correspond across its versions, read from
// GET .../rubric-links. "same" lines carry guidance from one version to another (written for each version's values), "related"
// lines are the same step with a different outcome on each version and are decided per version, "seed" lines share an item ID
// because their rubrics were drafted together and are not verified yet. Links never change a rubric, guidance or a score: they
// only say where one edit may be offered to the other versions. Copying and mirroring are explicit, never automatic.
export const LINK_SEP = '\u001f';
export const KIND_LABEL = {same: 'Same on each linked version', related: 'Same step, different outcome: decided per version', seed: 'Linked by a shared item ID · not verified yet'};
const key = (qid, iid) => `${qid}${LINK_SEP}${iid}`;

// {families: [[qid]], sets: [...]}; an unavailable read gives no links, never an error: the window works as before without them.
export async function loadLinks(h, aid) {
  try {
    const found = await h.api(`/api/grading/assignments/${aid}/rubric-links`);
    return {families: found.families || [], sets: found.sets || []};
  } catch {
    return {families: [], sets: []};
  }
}

export function linkIndex(sets) {
  const index = new Map();
  (sets || []).forEach((set, n) => set.lines.forEach(line => index.set(key(line.question_id, line.item_id), {...set, index: n})));
  return index;
}

export const linkOf = (index, qid, iid) => index.get(key(qid, iid)) || null;

// The other lines one edit may be offered to: only "same" links (a related or unverified one is decided per version), never a set
// that holds two lines of one question.
export function partners(index, qid, iid, kinds = ['same']) {
  const set = linkOf(index, qid, iid);
  if (!set || set.conflict || !kinds.includes(set.kind)) return [];
  return set.lines.filter(line => !(line.question_id === qid && line.item_id === iid));
}

// A clarification that names values (digits or mathematics) is probably written for its own version's numbers.
export const namesValues = text => /\d|\$\$|\\\(|\\\[/.test(String(text || ''));

export function copyNote(targets, text) {
  if (!targets.length) return '';
  return `Copied to ${targets.join(', ')}.${namesValues(text) ? ' This text names values: check that each version’s own values are right.' : ''}`;
}

// What one edit of a line's guidance (a comment or a clarification) passes on to the same line of another version (instructor,
// 2026-10-03: adding one sentence to Version A's comment had replaced Versions B, C and D's comments with A's, numbers and all).
// Only what was ADDED travels: an edit that keeps the saved text as its start (or its end) passes the new part on, joined to the
// other line's own text; an edit of a line that had none passes it whole. A rewrite or a deletion travels nowhere: each version
// keeps its own text and is edited by hand. {kind: "add"|"rewrite"|"none", added, text}: `text` is the other line's text after it.
export function passedOn(before, after, theirs) {
  const old = String(before || '').trim(), now = String(after || '').trim(), own = String(theirs || '').trim();
  const join = (first, second) => [first, second].filter(Boolean).join(first && !/[.!?:;]$/.test(first) && /^[A-Z0-9$\\(]/.test(second) ? '. ' : ' ');
  if (now === old) return {kind: 'none', added: '', text: own};
  if (!old || now.startsWith(old)) { const added = now.slice(old.length).trim(); return {kind: 'add', added, text: join(own, added)}; }
  if (now.endsWith(old)) { const added = now.slice(0, now.length - old.length).trim(); return {kind: 'add', added, text: join(added, own)}; }
  return {kind: 'rewrite', added: '', text: own};
}

export async function changeLinks(h, aid, operations) {
  const found = await h.api(`/api/grading/assignments/${aid}/rubric-links`, 'POST', {operations});
  return {families: found.families || [], sets: found.sets || []};
}
