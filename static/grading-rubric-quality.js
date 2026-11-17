import {rubricDisplay} from "./grading-rubric-display.js";
// Rubric finalization: mandatory before grading. A model proposes clarifications, exclusive pairs and suspected errors;
// the instructor edits and confirms. Everything confirmed is internal guidance for the AI graders: the rubric itself,
// the review screen and every export keep the original wording.
import {mathText, renderMath} from './grading-math.js';
import {plural, whenVisible} from './ui.js';
import {mountQuestionWorkspace} from './grading-question-workspace.js';
import {mountRubricConcerns, plainIds} from './grading-rubric-concerns.js';
import {changeLinks, copyNote, linkIndex, loadLinks, partners, KIND_LABEL} from './grading-rubric-links.js';
import {choicesSummary, editPlanChoices} from './grading-rubric-choices.js';
import {bindRulings, loadRulings, rulingsMarkup} from './grading-recorded-rulings.js';

const labels = {missing: 'Upload all versions first', needed: 'Rubric check required', pending: 'Queued', running: 'Checking rubrics', failed: 'Check failed', needs_review: 'Review and confirm', confirmed: 'Rubrics finalized'};
const RULES = {full_credit: 'Full-credit line', no_credit: 'No-credit line'};
const SEP = '\u001f';
// An exclusion pair's canonical key: its two line ids in order, joined.
const exclusionKey = pair => [...pair].sort().join(SEP);

// The most points a grader could still select at once, or null when that is within the question's value. When a question's
// lines add up to more than it is worth, some of them MUST be alternatives (requirement, 2026-09-21), so the finalization is
// not done while this finds anything. Mirrors `rubric_quality.heaviest_combination`; both are held to
// tests/fixtures/grading/overflow_cases.json, and the server decides. The search is exact (lines that all exclude each other give
// one line at most); should it reach SEARCH_LIMIT the answer is {undecided: true}, never "resolved".
export const SEARCH_LIMIT = 200000;
export function heaviestCombination(rubric, entry, maximum) {
  if (rubric.mode !== 'positive') return null;
  const key = (a, b) => [a, b].sort().join(SEP), apart = new Set(), ids = rubric.items.map(item => item.id);
  for (const rule of entry.builtin || []) for (const other of ids) if (other !== rule.item_id) apart.add(key(rule.item_id, other));
  for (const [a, b] of entry.pairs || []) apart.add(key(a, b));
  const single = new Set((rubric.groups || []).filter(group => group.selection === 'single').map(group => group.id));
  for (const item of rubric.items) {
    for (const other of item.excludes || []) apart.add(key(item.id, other));
    if (item.stackable === false) for (const other of ids) if (other !== item.id) apart.add(key(item.id, other));
    if (item.group && single.has(item.group)) for (const other of rubric.items) if (other.id !== item.id && other.group === item.group) apart.add(key(item.id, other.id));
  }
  const lines = rubric.items.filter(item => item.points > 0).sort((a, b) => b.points - a.points);
  const cliques = [], cliqueOf = [];  // Members in index order, which is heaviest first.
  lines.forEach((line, k) => {
    let home = cliques.findIndex(members => members.every(m => apart.has(key(line.id, lines[m].id))));
    if (home < 0) { home = cliques.length; cliques.push([]); }
    cliques[home].push(k);
    cliqueOf.push(home);
  });
  const used = cliques.map(() => 0);
  const bound = k => cliques.reduce((sum, members, n) => sum + (used[n] ? 0 : (lines[members.find(m => m >= k)]?.points ?? 0)), 0);
  let best = 0, chosen = [], calls = 0;
  const search = (k, total, taken) => {
    calls++;
    if (total > best) { best = total; chosen = [...taken]; }
    if (calls > SEARCH_LIMIT || k === lines.length || total + bound(k) <= Math.max(best, maximum + 1e-9)) return;
    if (taken.every(other => !apart.has(key(lines[k].id, other)))) {
      taken.push(lines[k].id); used[cliqueOf[k]]++;
      search(k + 1, total + lines[k].points, taken);
      used[cliqueOf[k]]--; taken.pop();
    }
    search(k + 1, total, taken);
  };
  search(0, 0, []);
  if (best > maximum + 1e-9) return {points: Math.round(best * 1e4) / 1e4, maximum, lines: ids.filter(id => chosen.includes(id))};
  return calls > SEARCH_LIMIT ? {points: null, maximum, lines: [], undecided: true} : null;
}

// A dialog's rubric rows are fixed. Typing a clarification changes no combination, so repeat the search only after its pairs change.
export function combinationReader(selected) {
  const cache = new Map();
  return q => {
    const pairs = [...selected.get(q.question_id)].sort(), key = JSON.stringify(pairs), previous = cache.get(q.question_id);
    if (previous?.key === key && previous.rubric === q.rubric && previous.builtin === q.builtin && previous.maximum === q.max_points) return previous.value;
    const value = heaviestCombination(q.rubric, {builtin: q.builtin || [], pairs: pairs.map(pair => pair.split(SEP))}, q.max_points);
    cache.set(q.question_id, {key, rubric: q.rubric, builtin: q.builtin, maximum: q.max_points, value});
    return value;
  };
}

export async function reviewRubricQuality(aid, h, refresh, active, question = '') {
  const base = `/api/grading/assignments/${aid}/rubric-quality`;
  // The plan's linked lines (LINKED VERSIONS, 2026-10-02) are read beside the check; without them the window works as before.
  // What recorded meetings said of this assessment's grading (GRADING "RULINGS SAID IN A RECORDING") is read beside them too.
  const [status, links, held] = await Promise.all([h.api(base), loadLinks(h, aid), loadRulings(h, aid)]);
  let index = linkIndex(links.sets);
  if (!active()) return;
  const check = status.check;
  if (!check?.current || check.status !== 'complete') { h.toast('Run the check on the current rubrics first.', true); return; }
  // A resolution of the listed concerns (2026-09-25) already starts from the window as the instructor left it, so every question
  // starts from its result; `before` is what each question it asked held until then.
  const resolution = check.result.resolution || null, failed = status.resolution_failed || null;
  // Each question starts from what stands for it (2026-09-25): the plan's current confirmation, else the entry its latest
  // confirmation still gives it, and only a question with neither from the reviewer's new proposal. A kept question never takes
  // the reviewer's new suggestions by default: adopting them changes its guidance and sends its automatic grades back to review.
  const standing = q => { const s = status.standing?.[q.question_id]; return s && s.rubric_id === q.rubric_id ? s.entry : null; };
  // A CHECK ASKS ONLY WHAT DOES NOT STAND (2026-10-05): a settled question was not asked; its entry IS its confirmed one.
  const settledIds = new Set(check.result.settled || []);
  // A CHECK ASKS ONLY WHAT CHANGED (2026-10-25): a carried question was not asked either; its entry is the one the last check gave it.
  const carriedIds = new Set(check.result.carried || []);
  const confirmedEntry = q => status.review ? status.review.policy.questions.find(row => row.question_id === q.question_id && row.rubric_id === q.rubric_id) || null : standing(q);
  // A check made one problem at a time with every version side by side (2026-09-25) lists each problem's versions together.
  const family = new Map((check.result.families || []).flatMap((members, index) => members.map(qid => [qid, index])));
  const questions = [...status.context.questions].sort((a, b) => (family.get(a.question_id) ?? 1e9) - (family.get(b.question_id) ?? 1e9)
    || a.exam_version.localeCompare(b.exam_version)
    || a.printed_number.localeCompare(b.printed_number, undefined, {numeric: true}) || a.title.localeCompare(b.title));
  const questionLabel = q => `${q.exam_version ? `Version ${q.exam_version} · ` : ''}${q.title}`;
  const entry = (source, q) => source.questions.find(row => row.question_id === q.question_id) || {pairs: [], reasons: [], clarifications: {}};
  const start = q => resolution ? entry(check.result, q) : confirmedEntry(q) || entry(check.result, q);
  // A kept question whose reviewer now suggests something else (under versions-v1 often a sibling version's change, for consistency)
  // is pointed out, never adopted by default: adopting it changes the question's guidance and sends its automatic grades back to you.
  const pairKey = rows => (rows || []).map(exclusionKey).sort().join('|');
  const noted = rows => JSON.stringify(Object.entries(rows || {}).filter(([, v]) => String(v || '').trim()).sort());
  const differs = (a, b) => pairKey(a?.pairs) !== pairKey(b?.pairs) || noted(a?.clarifications) !== noted(b?.clarifications);
  const suggestsChange = q => { const kept = confirmedEntry(q); return !resolution && Boolean(kept) && differs(kept, entry(check.result, q)); };
  const resolvedChange = q => Boolean(resolution?.before?.[q.question_id]) && differs(resolution.before[q.question_id], entry(check.result, q));
  const leavesConfirmed = q => { const kept = confirmedEntry(q); return Boolean(resolution && kept) && differs(kept, entry(check.result, q)); };
  // What resolving the concerns changed on one question, in words: clarified items, exclusions added and removed.
  const changeText = q => {
    const was = resolution.before[q.question_id], now = entry(check.result, q), display = rubricDisplay(q.rubric);
    const keys = rows => new Set((rows || []).map(exclusionKey));
    const [then, after] = [keys(was.pairs), keys(now.pairs)];
    const pairText = list => list.map(pair => pair.split(SEP).map(id => display.label(id)).join(' and ')).join('; ');
    const clarified = q.rubric.items.filter(item => (was.clarifications?.[item.id] || '') !== (now.clarifications?.[item.id] || '')).map(item => item.id);
    const added = [...after].filter(pair => !then.has(pair)), removed = [...then].filter(pair => !after.has(pair));
    return [clarified.length === 1 ? `the clarification of item ${display.list(clarified)}` : clarified.length ? `the clarifications of items ${display.list(clarified)}` : '', added.length ? `new exclusions: ${pairText(added)}` : '',
      removed.length ? `exclusions removed: ${pairText(removed)}` : ''].filter(Boolean).join(' · ');
  };
  const selected = new Map(questions.map(q => [q.question_id, new Set(start(q).pairs.map(exclusionKey))]));
  // Differences between the versions' rubrics: found when a problem's versions were checked side by side (2026-09-25), or by an
  // earlier whole-exam check. A check made one question at a time (2026-09-21 to 2026-09-25) compared no versions.
  const issues = check.result.consistency_issues, errors = check.result.suspected_errors || [];
  // BOOKKEEPING IS FIXED, NOT ASKED (requirement, 2026-10-24): findings Faculty fixed are listed apart, each with Undo.
  const fixes = check.fixes || {};
  let handled = new Set(Object.entries(fixes).filter(([, fix]) => fix.state === 'applied').map(([n]) => Number(n)));
  const retry = new Map((failed?.responses || []).map(row => [row.concern, row.response]));
  const resolvedRows = resolution?.resolved || [];
  // Each concern opens the rubric of the questions it names in a pop-up over this window (requirement, 2026-09-25: scrolling to
  // the question and back to answer a concern was a pain). It is drawn when opened, so it shows the clarifications and
  // exclusions as this window holds them, unsaved edits included; the lines the concern names are marked.
  const rubricLink = (ids, items) => ids.some(id => questions.some(q => q.question_id === id))
    ? `<button type="button" class="text-button quality-rubric-link" data-show-rubric="${h.e(ids.join(' '))}" data-rubric-items="${h.e((items || []).join(' '))}">${ids.length > 1 ? 'Show rubrics' : 'Show rubric'}</button>` : '';
  // The instructor's answer to the concerns above (2026-09-25): the reviewer revisits the problems they name and revises their
  // clarifications and exclusions from what this window holds now. Nothing is approved until the rubrics are confirmed below.
  // The Resolve block travels with the decision on screen (grading-rubric-concerns.js): the button, what it sends and how it works
  // together; a failed or cancelled resolution is said at the top of Decisions, where it is seen first (2026-10-06).
  const failedNote = failed && (errors.length || issues?.length) ? `<p class="grading-warning" role="status" data-resolve-failed>${failed.cancelled ? 'Resolving the concerns was cancelled.' : `Resolving the concerns failed: ${h.e(failed.error || 'the reviewer did not answer')}.`} This review is unchanged, and your answers are kept to try again.</p>` : '';
  const respond = !errors.length && !issues?.length ? '' : `<section class="quality-respond" data-quality-respond>
    <div class="grading-actions">${h.button('resolve-concerns', 'Resolve concerns')}</div><p class="supporting-copy" data-resolve-scope></p>
    <details class="compute-why"><summary>How feedback is resolved</summary><p>Resolve concerns sends all drafted feedback and your current clarifications and exclusions. The reviewer revisits the problems these concerns name, with all their versions, and also investigates concerns without feedback. Wording and points stay yours. Nothing is approved until you confirm. Open concerns never stop you confirming.</p></details></section>`;
  // A FINALIZATION THAT READS AT A GLANCE (requirement, 2026-10-06). A version's
  // state is a few words beside its letter; a version that waits on the instructor opens by itself (a decision never folds).
  // WHAT THE CHECK PROPOSES (requirement, 2026-10-06). A question with no
  // confirmation of its own starts from the reviewer's guidance: its row counts what was proposed, and each of its lines reads
  // "Proposed clarification" while it still says what the reviewer wrote.
  const proposed = q => !resolution && !settledIds.has(q.question_id) && !confirmedEntry(q);
  const proposalOf = (q, iid) => proposed(q) ? String(entry(check.result, q).clarifications?.[iid] || '').trim() : '';
  const proposalCount = qs => {
    const notes = qs.reduce((n, q) => n + Object.values(entry(check.result, q).clarifications || {}).filter(v => String(v || '').trim()).length, 0);
    const pairs = qs.reduce((n, q) => n + (entry(check.result, q).pairs || []).length, 0);
    return notes || pairs ? [notes ? plural(notes, 'clarification') : '', pairs ? plural(pairs, 'exclusion') : ''].filter(Boolean).join(' and ') : '';
  };
  const stateText = q => carriedIds.has(q.question_id) ? 'Unchanged since the last check, not asked again' : settledIds.has(q.question_id) ? 'Confirmed earlier, not asked again' : confirmedEntry(q) ? 'Confirmed earlier'
    : proposed(q) ? `Checked now: ${proposalCount([q]) ? `${proposalCount([q])} proposed` : 'no guidance proposed'}` : 'Checked now';
  const needsLook = q => suggestsChange(q) || leavesConfirmed(q) || resolvedChange(q);
  const listed = names => names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0] || '';
  const versionsOf = qs => `Version${qs.length > 1 ? 's' : ''} ${listed(qs.map(q => q.exam_version))}`;
  // One question's fold (its state, warnings, built-in rules) and, when it stands alone, its items. In a problem with linked versions
  // (LINKED VERSIONS, 2026-10-02) the fold is one row of the problem's versions, holding that version's state only; its lines are
  // drawn with their linked copies below.
  const questionFold = (q, open, alone) => {
    const display = rubricDisplay(q.rubric);
    const builtin = new Map((q.builtin || []).map(rule => [rule.item_id, rule.rule])), suggested = entry(check.result, q), kept = confirmedEntry(q);
    const head = alone ? `${h.e(questionLabel(q))} · ${q.rubric.items.length} items` : `<strong class="quality-version-letter">Version ${h.e(q.exam_version)}</strong>`;
    const state = kept ? `<span class="supporting-copy" data-kept>${h.e(stateText(q))}</span>` : `<span class="supporting-copy" data-checked>${h.e(stateText(q))}</span>`;
    return `<details class="quality-question compute-fold${alone ? '' : ' quality-version'}" data-quality-fold="${h.e(q.question_id)}" ${alone ? `data-question-panel="${h.e(q.question_id)}"` : ''} ${open || needsLook(q) ? 'open' : ''}><summary>${head}${alone ? ' · ' : ' '}${state}${suggestsChange(q) ? ' <span class="grading-chip grading-chip-attention" data-suggests>Reviewer suggests changes</span>' : ''}${resolvedChange(q) ? ' <span class="grading-chip grading-chip-attention" data-resolved-change>Changed by resolving</span>' : ''} <span class="grading-chip grading-chip-attention" data-overflow-chip hidden>Alternatives to decide</span></summary><div class="compute-fold-body"><p class="grading-warning" role="status" data-overflow hidden></p>${resolvedChange(q) ? `<p class="supporting-copy" data-resolution-change>Resolving the concerns changed ${h.e(changeText(q))}. What each item said before is shown under it.</p>` : ''}${leavesConfirmed(q) ? '<p class="grading-warning">This differs from what you confirmed earlier: confirming it sends this question’s automatic grades back to you.</p>'
        : kept && resolution ? '<p class="supporting-copy">Unchanged since you confirmed it: its automatic grades stay final unless you change it.</p>'
        : kept && settledIds.has(q.question_id) ? '<p class="supporting-copy" data-settled>Its guidance stays as you confirmed it and was the reference for the versions checked. Its automatic grades stay final unless you change it.</p>'
        : carriedIds.has(q.question_id) ? '<p class="supporting-copy" data-settled>Its rubric is unchanged since the last check, so it keeps the guidance you settled there and was the reference for the versions checked.</p>'
        : kept ? '<p class="supporting-copy">It starts from your confirmed guidance; its automatic grades stay final unless you change it. The model’s suggestions are shown under each line.</p>' : ''}${suggestsChange(q) ? `<p class="grading-warning">The reviewer now suggests different clarifications or exclusions for this question${(check.result.families || []).some(members => members.includes(q.question_id) && members.some(other => other !== q.question_id && !confirmedEntry(questions.find(x => x.question_id === other) || {question_id: other}))) ? ', probably to keep it consistent with another version that changed' : ''}. Adopting them sends its automatic grades back to you.</p><p>${h.button('adopt-suggestions', 'Use the reviewer’s suggestions for this question', `data-adopt="${h.e(q.question_id)}"`)}</p>` : ''}${suggested.summary ? `<p class="supporting-copy">${mathText(suggested.summary, h)}</p>` : ''}${builtin.size ? `<p class="quality-builtin"><strong>Built-in rules, always on:</strong> ${[...builtin].map(([id, rule]) => `${h.e(display.label(id))} is the ${h.e(RULES[rule].toLowerCase())} and excludes every other item`).join('; ')}.</p>` : ''}${alone ? q.rubric.items.map(item => itemMarkup(q, item)).join('') : ''}</div></details>`;
  };
  // One rubric line: its clarification and what it cannot be combined with. Closed, it reads as the line, its clarification (or
  // "No clarification") and its exclusions, so the guidance can be read without opening anything; opening it edits them. `mode`:
  // 'alone' (a question without linked versions), 'linked' (one version's copy in a block of linked lines, under `header`, the
  // block's first line: only what differs from it is repeated) or 'loose' (a line linked to nothing). `actions` go under the editor.
  const itemMarkup = (q, item, mode = 'alone', header = null, actions = '') => {
    const display = rubricDisplay(q.rubric);
    const builtin = new Map((q.builtin || []).map(rule => [rule.item_id, rule.rule])), suggested = entry(check.result, q), kept = confirmedEntry(q), saved = start(q);
    const reason = suggested.reasons?.find(row => row.item_id === item.id)?.reason, proposal = settledIds.has(q.question_id) ? '' : suggested.clarifications?.[item.id] || '';
    const earlier = resolution?.before?.[q.question_id], was = earlier ? earlier.clarifications?.[item.id] || '' : null;
    const others = q.rubric.items.filter(other => other.id !== item.id && !builtin.has(other.id));
    const version = q.exam_version ? `Version ${q.exam_version}` : '', line = `${display.label(item.id)} · ${item.points} points`;
    const own = header && (header.label !== line) ? line : '';
    const label = mode === 'linked' && version ? version : mode === 'loose' && version ? `${version} · ${line}` : line;
    const wording = !header || String(header.description) !== String(item.description);
    return `<details class="quality-item" data-quality-item="${h.e(item.id)}" data-quality-question="${h.e(q.question_id)}" data-item-label="${h.e(`${version ? `${version} · ` : ''}${display.label(item.id)}`)}"><summary><span class="quality-item-head"><strong>${h.e(label)}</strong>${own ? ` <span class="supporting-copy">${h.e(own)}</span>` : ''}</span>${wording ? `<span class="quality-item-wording">${mathText(item.description, h)}</span>` : ''}<span class="quality-item-guidance" data-item-guidance></span><small data-item-summary></small></summary><div class="quality-exclusions">
          ${q.comments?.[item.id] ? `<p class="quality-comment"><strong>Your comment for the reviewer:</strong> ${mathText(q.comments[item.id], h)}${(suggested.comment_fallback || []).includes(item.id) ? ' <em>The reviewer left this line without a clarification, so your comment is proposed as it stands.</em>' : ''}</p>` : ''}
          ${h.area('Clarification for the AI graders (optional)', `clarify${SEP}${q.question_id}${SEP}${item.id}`, resolution ? (saved.clarifications?.[item.id] ?? '') : kept ? (kept.clarifications?.[item.id] ?? '') : (saved.clarifications?.[item.id] ?? proposal), 'maxlength="2000" data-clarification')}
          ${was !== null ? (was !== proposal ? `<p class="supporting-copy" data-before>Before resolving the concerns: ${was ? mathText(was, h) : 'no clarification'}</p>` : '')
            : proposal ? `<p class="supporting-copy">Suggested by the model: ${mathText(proposal, h)}</p>` : ''}
          ${builtin.has(item.id) ? `<p class="supporting-copy">${h.e(RULES[builtin.get(item.id)])}: excludes every other item by rule. This cannot be edited.</p>`
            : `<div class="quality-pairs"><p class="quality-pairs-label">Never selected together with</p><p class="supporting-copy">Each tick pairs this line with one other line, both ways. Lines ticked here are not paired with each other.</p>${reason ? `<p class="supporting-copy">Model explanation: ${mathText(reason, h)}</p>` : ''}${others.length ? '<div class="quality-pair-choices" data-pair-choices></div>' : '<p>No other items to combine with.</p>'}</div>`}${actions}</div></details>`;
  };
  // A line's ticks are drawn the first time it is opened (requirement, 2026-10-06): each list repeats every other line's wording, so drawn up front they were 88% of a five-version quiz's window (108,000
  // of 123,000 elements, 2,100 of its 2,400 formulas). The exclusions themselves live in `selected`, so an unopened line loses nothing.
  const pairChoices = (q, item) => {
    const display = rubricDisplay(q.rubric), builtin = new Set((q.builtin || []).map(rule => rule.item_id));
    return q.rubric.items.filter(other => other.id !== item.id && !builtin.has(other.id)).map(other => `<label class="grading-check"><input type="checkbox" data-pair="${h.e(exclusionKey([item.id, other.id]))}" data-question="${h.e(q.question_id)}"><span><strong>${h.e(display.label(other.id))} · ${h.e(other.points)} points</strong> ${mathText(other.description, h)}</span></label>`).join('');
  };
  function drawPairChoices(row) {
    const host = row.querySelector('[data-pair-choices]');
    if (!host || host.childElementCount) return;
    const q = byId.get(row.dataset.qualityQuestion), item = q.rubric.items.find(other => other.id === row.dataset.qualityItem);
    host.innerHTML = pairChoices(q, item);
    host.querySelectorAll('[data-pair]').forEach(input => { input.checked = selected.get(input.dataset.question).has(input.dataset.pair); });
    renderMath(host);
  }
  // LINKED VERSIONS (requirement, 2026-10-02): a problem with two or more versions is ONE panel. Its versions' linked lines are drawn
  // together (one block per linked set), so a line is settled once in view of its copies; lines with no copy follow. Each line is still
  // its own version's field: copying a clarification or mirroring an exclusion to the linked versions is offered, never done unasked.
  const byId = new Map(questions.map(q => [q.question_id, q]));
  const problems = (check.result.families || []).map(members => members.filter(id => byId.has(id))).filter(members => members.length > 1);
  const problemOf = new Map(problems.flatMap(members => members.map(id => [id, members])));
  const panelId = qid => problemOf.has(qid) ? `problem:${problemOf.get(qid)[0]}` : qid;
  const lineKey = (qid, iid) => `${qid}${SEP}${iid}`;
  const lineLabel = (qid, iid) => { const q = byId.get(qid); return `Version ${q.exam_version} · ${rubricDisplay(q.rubric).label(iid)}`; };
  const setsOf = members => links.sets.map(set => ({...set, lines: set.lines.filter(line => members.includes(line.question_id))})).filter(set => set.lines.length > 1);
  // One block per linked set: the line once (its first version's label, points and wording), then one row per version with that
  // version's clarification and exclusions. Copying and unlinking sit inside the row being edited; changing what kind of link it is
  // stays a quiet action of the block.
  const linkBlock = (set, members) => {
    const origin = set.origin === 'instructor' ? 'linked by you' : set.origin === 'reviewer' ? 'linked by the reviewer' : set.origin === 'drafted' ? 'as drafted, not reviewed yet' : '';
    const first = byId.get(set.lines[0].question_id), firstItem = first.rubric.items.find(row => row.id === set.lines[0].item_id);
    const header = {label: `${rubricDisplay(first.rubric).label(firstItem.id)} · ${firstItem.points} points`, description: firstItem.description};
    const lead = h.e(lineKey(set.lines[0].question_id, set.lines[0].item_id));
    const kinds = `${set.kind === 'seed' ? `<button type="button" class="button" data-link-kind="same" data-link-line="${lead}">Confirm as the same on each version</button>` : set.kind !== 'same' ? `<button type="button" class="text-button" data-link-kind="same" data-link-line="${lead}">Mark as the same on each version</button>` : ''}${set.kind !== 'related' ? `<button type="button" class="text-button" data-link-kind="related" data-link-line="${lead}">Mark as related: decided per version</button>` : ''}`;
    return `<section class="quality-linked quality-linked-${h.e(set.kind)}" data-link-set="${h.e(set.lines.map(line => lineKey(line.question_id, line.item_id)).join(' '))}">
      <header class="quality-linked-head"><div><strong>${h.e(header.label)}</strong> <span class="supporting-copy">${h.e(KIND_LABEL[set.kind] || '')}${origin ? ` · ${h.e(origin)}` : ''}</span>${set.conflict ? ' <span class="grading-chip grading-chip-attention">Two lines of one version: unlink one</span>' : ''}</div><div class="quality-linked-kinds">${kinds}</div></header>
      <p class="quality-linked-wording">${mathText(header.description, h)}</p>${set.note ? `<p class="supporting-copy">${mathText(set.note, h)}</p>` : ''}
      ${set.lines.map(line => {
        const q = byId.get(line.question_id), item = q.rubric.items.find(row => row.id === line.item_id), copies = partners(index, q.question_id, item.id).filter(other => members.includes(other.question_id));
        const at = h.e(lineKey(q.question_id, item.id));
        return itemMarkup(q, item, 'linked', header, `<div class="quality-link-actions">${copies.length ? `<button type="button" class="button" data-copy-clarification="${at}">Copy this clarification to ${h.e(versionsOf(copies.map(other => byId.get(other.question_id))))}</button>` : ''}<button type="button" class="text-button" data-unlink-line="${at}">Unlink Version ${h.e(q.exam_version)}</button></div><p class="supporting-copy" role="status" aria-live="polite" data-copy-status></p>`);
      }).join('')}
    </section>`;
  };
  const problemPanel = members => {
    const qs = members.map(id => byId.get(id)), sets = setsOf(members);
    const linked = new Set(sets.flatMap(set => set.lines.map(line => lineKey(line.question_id, line.item_id))));
    const loose = qs.flatMap(q => q.rubric.items.filter(item => !linked.has(lineKey(q.question_id, item.id))).map(item => [q, item]));
    const targets = q => qs.filter(other => other.exam_version !== q.exam_version).flatMap(other => other.rubric.items.map(item => [lineKey(other.question_id, item.id), `${lineLabel(other.question_id, item.id)} · ${String(item.description).slice(0, 60)}`]));
    return `<section class="quality-problem" data-question-panel="${h.e(panelId(members[0]))}">
      <div class="quality-problem-head"><h4>${h.e(qs[0].title)} · ${h.e(versionsOf(qs))}</h4></div>
      <div class="quality-versions">${qs.map(q => questionFold(q, false, false)).join('')}</div>
      <div class="quality-lines-head"><h5>Rubric lines</h5><details class="compute-why quality-link-help"><summary>How linked lines work</summary><p>Lines that correspond across the versions are shown together, and each version keeps its own clarification. “Same” lines credit the same thing: a clarification can be copied to the other versions, then adjusted to their values. “Related” lines are the same step with a different outcome and are decided per version.</p><label class="grading-check"><input type="checkbox" data-mirror-pairs checked><span>When I tick an exclusion, tick it on the versions linked as the same</span></label></details></div>
      <p class="supporting-copy" role="status" aria-live="polite" data-mirror-status></p>
      ${sets.map(set => linkBlock(set, members)).join('')}
      ${loose.length ? `<section class="quality-unlinked"><h5>Lines not linked to another version · ${loose.length}</h5>${loose.map(([q, item]) => itemMarkup(q, item, 'loose', null, `<div class="quality-link-to">${h.select('Link to', `link-to${SEP}${q.question_id}${SEP}${item.id}`, [['', 'Choose a line of another version…'], ...targets(q)])}${h.select('As', `link-kind${SEP}${q.question_id}${SEP}${item.id}`, [['same', 'The same on each version'], ['related', 'Related: decided per version']])}<button type="button" class="button" data-link-line-to="${h.e(lineKey(q.question_id, item.id))}">Link</button></div>`)).join('')}</section>` : ''}
    </section>`;
  };
  const panelsMarkup = () => {
    const drawn = new Set();
    return questions.map((q, qi) => {
      if (drawn.has(panelId(q.question_id))) return '';
      drawn.add(panelId(q.question_id));
      return problemOf.has(q.question_id) ? problemPanel(problemOf.get(q.question_id)) : questionFold(q, qi === 0 && !settledIds.has(q.question_id), true);
    }).join('');
  };
  // What a check that asked only some questions left alone (A CHECK ASKS ONLY WHAT DOES NOT STAND), by whole versions where it can.
  const scopeText = (() => {
    if (!settledIds.size) return '';
    const versions = [...new Set(questions.map(q => q.exam_version))].filter(Boolean).sort(), of = v => questions.filter(q => q.exam_version === v);
    const settledVersions = versions.filter(v => of(v).every(q => settledIds.has(q.question_id))), checkedVersions = versions.filter(v => of(v).every(q => !settledIds.has(q.question_id)));
    const named = names => `Version${names.length > 1 ? 's' : ''} ${listed(names)}`;
    if (carriedIds.size) {
      const n = questions.length - settledIds.size;
      return `Only the ${n} rubric${n === 1 ? '' : 's'} that changed since the last check ${n === 1 ? 'was' : 'were'} checked. The others keep the guidance you settled there.`;
    }
    if (versions.length > 1 && checkedVersions.length && settledVersions.length + checkedVersions.length === versions.length)
      return `Only ${named(checkedVersions)} ${checkedVersions.length > 1 ? 'were' : 'was'} checked. ${named(settledVersions)} ${settledVersions.length > 1 ? 'keep' : 'keeps'} the guidance you confirmed.`;
    const n = questions.length - settledIds.size;
    return `Only the ${n} rubric${n === 1 ? '' : 's'} without a standing confirmation ${n === 1 ? 'was' : 'were'} checked. The others keep the guidance you confirmed.`;
  })();
  // What the check found and proposed (WHAT THE CHECK PROPOSES), by whole versions where it can.
  const foundNothing = !resolution && !errors.length && !issues?.length;
  const clearText = foundNothing ? `Nothing needs a decision: the reviewer found no errors${problems.length ? ' and no differences between the versions' : ''}.` : 'Nothing needs a decision. Approve the rubrics, or read their guidance under Rubric items first.';
  const proposalText = (() => {
    const asked = questions.filter(proposed);
    if (!asked.length) return '';
    const versions = [...new Set(questions.map(q => q.exam_version))].filter(Boolean).sort();
    const whole = versions.filter(v => questions.filter(q => q.exam_version === v).every(proposed));
    const whom = versions.length > 1 && asked.every(q => whole.includes(q.exam_version)) ? `Version${whole.length > 1 ? 's' : ''} ${listed(whole)}` : plural(asked.length, 'question');
    const count = proposalCount(asked);
    // THE WINDOW SHOWS ONLY THE DECISIONS (requirement, 2026-10-24): the proposed guidance is approved with the rubrics unless edited.
    return count ? `The reviewer proposes ${count} for ${whom}. They are approved with the rubrics; read or edit them under Rubric items.` : `The reviewer proposes no clarifications or exclusions for ${whom}.`;
  })();
  // The window says first what waits on the instructor (DESIGN-LANGUAGE principles 1, 2 and 13), each as one short marked line, or
  // that nothing does; draw() keeps it current while lines are edited.
  const form = h.dialog('Finalize the rubrics', 'Guidance for the AI graders only: the rubric’s wording, points and exports stay as they are.',
    `<section class="quality-lead" data-quality-lead>
      <ul class="review-checks quality-needs" data-lead-needs>
        <li data-overflow-item hidden><span data-overflow-summary></span> ${h.button('review-point-conflicts', 'Review point conflicts')}</li>
        <li data-lead-concerns hidden></li><li data-lead-suggests hidden></li><li data-lead-regrade hidden></li>
      </ul>
      <p class="supporting-copy" data-lead-fixed hidden></p>
      <p class="quality-lead-clear" data-lead-clear hidden>${h.e(clearText)}</p>
      ${proposalText ? `<p class="supporting-copy" data-lead-proposed>${h.e(proposalText)}</p>` : ''}
      ${scopeText ? `<p class="supporting-copy" data-lead-scope>${h.e(scopeText)}</p>` : ''}
    </section>
    ${rulingsMarkup(held, questions, h.e, SEP)}
    <details class="compute-why quality-check-summary"><summary>Reviewer’s summary</summary><p>${mathText(check.result.summary, h)}</p></details>
    <nav class="quality-review-tabs" aria-label="Rubric review views"><button type="button" class="button" data-quality-tab="decisions">Decisions</button><button type="button" class="button" data-quality-tab="items">Rubric items</button></nav>
    <div data-quality-decisions>${failedNote}<div data-quality-concerns></div>
    ${resolvedRows.length ? `<details class="compute-fold" data-quality-resolved><summary>Resolved concerns · ${resolvedRows.length}</summary><div class="compute-fold-body">${resolution.response ? `<p class="supporting-copy">Your response: ${h.e(resolution.response)}</p>` : ''}<ul>${resolvedRows.map(row => `<li>${mathText(plainIds(row.message, questions, row.question_ids || [row.question_id]), h)}<div class="quality-finding-meta">${rubricLink(row.question_ids || [row.question_id], row.item_ids || [])}</div>${row.comment ? `<small class="supporting-copy">Your comment: ${h.e(row.comment)}</small>` : ''}${row.without_comment ? '<small class="supporting-copy" data-without-comment><strong>Settled by the reviewer without a comment from you.</strong></small>' : ''}<small class="supporting-copy">${mathText(plainIds(row.how, questions, row.question_ids || [row.question_id]), h)}</small></li>`).join('')}</ul></div></details>` : ''}
    ${respond}
    </div><div data-quality-items>
    <div data-quality-workspace>${panelsMarkup()}</div></div>
    <div class="quality-rubric-popover" popover data-rubric-popover role="dialog" aria-label="Rubric"></div>
    ${h.check('I approve these rubrics and their guidance for grading', 'confirm-quality')}`, 'Confirm and approve the rubrics', async (values, _, live) => {
      if (!values.has('confirm-quality')) {
        form.querySelector('[name="confirm-quality"]').focus();
        throw new Error('Tick “I approve these rubrics and their guidance for grading” beside this button to confirm. Open concerns never stop you confirming.');
      }
      const open = unresolved();
      if (open.length) throw new Error(`${open.length} question${open.length === 1 ? '' : 's'} still allow more points than they are worth. Mark which of their lines are alternatives first.`);
      const answer = await h.api(`${base}/confirm`, 'POST', {version: status.version, check_id: check.id, input_sha256: status.input_sha256, confirm_review: true, questions: current()});
      const done = answer?.confirmation;
      if (done) h.toast(done.suspended ? `Confirmed. ${done.changed} question${done.changed === 1 ? '' : 's'} changed; ${done.suspended} automatic grade${done.suspended === 1 ? '' : 's'} of those went back to review. The others stay final.`
        : `Confirmed. ${done.kept} question${done.kept === 1 ? '' : 's'} kept as confirmed earlier; no automatic grade went back to review.`);
      if (live()) { h.close(); await refresh(); }
    });
  // A ruling is added to the clarification field of the line the instructor chooses, and recorded only by their Confirm.
  bindRulings(form, held, {h, aid, questions, sep: SEP, field: (qid, iid) => form.elements.namedItem(`clarify${SEP}${qid}${SEP}${iid}`)});
  // The window's clarifications and exclusions, every question once: what a confirmation records and where a resolution starts.
  const current = () => status.context.questions.map(q => ({question_id: q.question_id, rubric_id: q.rubric_id, pairs: [...selected.get(q.question_id)].map(pair => pair.split(SEP)),
    clarifications: Object.fromEntries(q.rubric.items.map(item => [item.id, String(form.elements.namedItem(`clarify${SEP}${q.question_id}${SEP}${item.id}`)?.value || '').trim()]).filter(([, note]) => note))}));
  const resolveRequest = crypto.randomUUID();  // One per window: sending the same response again returns the same resolution.
  const overflow = combinationReader(selected);
  const unresolved = () => questions.filter(q => overflow(q));
  const attention = q => Boolean(overflow(q)) || errors.some((e, n) => !handled.has(n) && e.question_id === q.question_id) || suggestsChange(q) || resolvedChange(q);
  // One entry per panel: a problem with linked versions is one entry naming its versions.
  const choices = () => {
    const seen = new Set();
    return questions.filter(q => !seen.has(panelId(q.question_id)) && seen.add(panelId(q.question_id))).map(q => {
      const members = problemOf.get(q.question_id)?.map(id => byId.get(id)) || [q];
      return {id: panelId(q.question_id), label: members.length > 1 ? `${q.title} · ${versionsOf(members)}` : questionLabel(q),
        detail: members.some(m => overflow(m)) ? 'Alternatives to decide' : `${members.reduce((n, m) => n + m.rubric.items.length, 0)} rubric items`,
        attention: members.some(attention)};
    });
  };
  // GRADING: a shared concern opens its owning question, keeping linked versions together and all decisions unchanged.
  const requested = questions.find(q => q.question_id === question);
  const panels = choices().length;  // A search and a filter only help in a long list.
  const navigator = mountQuestionWorkspace(form.querySelector('[data-quality-workspace]'), choices(), h, {key: `${aid}:finalization`, initial: panelId(requested?.question_id || questions[0]?.question_id), find: panels > 6});
  function showTab(tab) {
    form.querySelector('[data-quality-decisions]').hidden = tab !== 'decisions';
    form.querySelector('[data-quality-items]').hidden = tab !== 'items';
    form.querySelectorAll('[data-quality-tab]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.qualityTab === tab)));
    if (tab === 'decisions') concernDesk?.update();
  }
  const concernDesk = errors.length || issues?.length ? mountRubricConcerns(form.querySelector('[data-quality-concerns]'), check.result, questions, h, {
    retry, questionLabel, links, onRedraw: () => { form.querySelector('[data-quality-tab="decisions"]').textContent = `Decisions (${concernDesk?.count || 0})`; draw(); },
    handled, fixes,
    // Undo asks the server, which takes back what a key fix added; the decision then waits like any other.
    undoFix: async indexes => {
      const answer = await h.api(`${base}/fixes`, 'POST', {check_id: check.id, concerns: indexes});
      Object.assign(fixes, answer?.check?.fixes || {});
      handled = new Set([...handled].filter(n => !indexes.includes(n)));
      h.toast(indexes.some(n => fixes[n]?.added?.some(row => row.forms?.length)) ? 'Undone: the key no longer accepts what the fix added. Decide it under Decisions.' : 'Undone: decide it under Decisions.');
    },
    // AN EDIT ONLY THE INSTRUCTOR CAN MAKE (2026-10-29): the points change the instructor asked for, saved as their own rubric
    // edit; the changed rubrics are then checked again on their own.
    applyEdit: async indexes => {
      await h.api(`${base}/edits`, 'POST', {check_id: check.id, concerns: indexes});
      h.toast('Changed. Check the changed rubric again, then confirm.');
      h.close();
      await refresh?.();
    },
    guidance: qid => current().find(row => row.question_id === qid),
    setClarification: (qid, iid, value) => {
      const area = form.elements.namedItem(`clarify${SEP}${qid}${SEP}${iid}`);
      area.value = value; area.dispatchEvent(new Event('input'));
    },
    editQuestion: (qid, iid) => {
      showTab('items'); navigator.select(panelId(qid));
      const item = iid && form.querySelector(`[data-quality-question="${CSS.escape(qid)}"][data-quality-item="${CSS.escape(iid)}"]`);
      if (item) item.open = true;
    },
  }) : null;
  if (!concernDesk) form.querySelector('[data-quality-concerns]').innerHTML = '<p class="empty-state" data-no-decisions>Nothing needs a decision. The reviewer’s guidance is approved with the rubrics; read or edit it under Rubric items.</p>';
  form.querySelector('[data-quality-tab="decisions"]').textContent = `Decisions (${concernDesk?.count || 0})`;
  form.querySelector('[data-quality-tab="items"]').textContent = `Rubric items (${panels} question${panels === 1 ? '' : 's'})`;
  form.querySelectorAll('[data-quality-tab]').forEach(button => button.onclick = () => showTab(button.dataset.qualityTab));
  form.querySelector('[data-grading-action="review-point-conflicts"]').onclick = () => { showTab('items'); navigator.select(panelId(unresolved()[0]?.question_id)); };
  // THE WINDOW SHOWS ONLY THE DECISIONS (requirement, 2026-10-24): it opens on
  // Decisions, even when none is left, and the guidance waits under Rubric items, approved with the rubrics unless edited. A question
  // asked for by name (a flag's "Open in Rubric items") still opens on its rubric.
  showTab(requested ? 'items' : 'decisions');
  if (requested) form.querySelector(`[data-quality-fold="${CSS.escape(requested.question_id)}"]`).open = true;
  function draw() {
    navigator.update(choices());
    form.querySelectorAll('[data-pair]').forEach(input => { input.checked = selected.get(input.dataset.question).has(input.dataset.pair); });
    // A question whose lines can still be combined for more than it is worth has alternatives nobody has marked yet.
    for (const q of questions) {
      const fold = form.querySelector(`[data-quality-fold="${CSS.escape(q.question_id)}"]`), over = overflow(q), note = fold.querySelector('[data-overflow]');
      if (over && note.hidden) fold.open = true;  // a point conflict that appears is a decision: it never waits in a closed fold
      note.hidden = fold.querySelector('[data-overflow-chip]').hidden = !over;
      note.textContent = !over ? '' : over.undecided ? `Still to decide: this question's lines are too entangled to prove that no selection exceeds ${over.maximum} points. Mark more of its alternatives (items that can never be selected together) until the check can finish.`
        : `Still to decide: items ${rubricDisplay(q.rubric).list(over.lines)} can be selected together for ${over.points} of ${over.maximum} points, more than this question is worth, so some of them must be alternatives. Open those items and tick which cannot be combined.`;
    }
    const open = unresolved(), summary = form.querySelector('[data-overflow-summary]');
    form.querySelector('[data-overflow-item]').hidden = !open.length;
    summary.textContent = open.length ? `${open.length} question${open.length === 1 ? '' : 's'} still allow more points than they are worth: ${open.map(questionLabel).join('; ')}. The rubrics cannot be confirmed until their alternatives are marked.` : '';
    const lead = (name, text) => { const row = form.querySelector(`[data-lead-${name}]`); row.hidden = !text; row.textContent = text; return Boolean(text); };
    const concerns = concernDesk?.count || 0, suggesting = questions.filter(suggestsChange), changing = questions.filter(leavesConfirmed);
    const fixedCount = concernDesk?.fixed || 0, fixedLine = form.querySelector('[data-lead-fixed]');
    fixedLine.hidden = !fixedCount;
    fixedLine.textContent = fixedCount ? `Faculty fixed ${fixedCount} finding${fixedCount === 1 ? '' : 's'} with one right answer instead of asking: see Fixed for you under Decisions, where each can be undone.` : '';
    const waiting = [Boolean(open.length),
      lead('concerns', concerns ? `${concerns} reviewer concern${concerns === 1 ? '' : 's'} to decide under Decisions. Open concerns never stop you approving.` : ''),
      lead('suggests', suggesting.length ? `The reviewer suggests different guidance for ${listed(suggesting.map(questionLabel))}, which you confirmed earlier.` : ''),
      lead('regrade', changing.length ? `Approving changes ${listed(changing.map(questionLabel))}, which you confirmed earlier: their automatic grades go back to you.` : '')].some(Boolean);
    form.querySelector('[data-lead-needs]').hidden = !waiting;
    form.querySelector('[data-lead-clear]').hidden = waiting;
    form.querySelectorAll('[data-quality-item]').forEach(row => {
      const q = questions.find(q => q.question_id === row.dataset.qualityQuestion), rule = (q.builtin || []).find(rule => rule.item_id === row.dataset.qualityItem);
      const others = [...selected.get(row.dataset.qualityQuestion)].map(pair => pair.split(SEP)).filter(pair => pair.includes(row.dataset.qualityItem)).map(pair => pair.find(id => id !== row.dataset.qualityItem));
      row.querySelector('[data-item-summary]').textContent = rule ? `${RULES[rule.rule]}: excludes every other item` : others.length ? `Cannot combine with items ${rubricDisplay(q.rubric).list(others)}` : 'No exclusions';
      // The clarification itself shows on the closed line; drawn again only when it changed.
      const note = row.querySelector('[data-clarification]').value.trim(), shown = row.querySelector('[data-item-guidance]');
      if (shown.dataset.drawn !== note) {
        shown.dataset.drawn = note;
        shown.classList.toggle('is-empty', !note);
        shown.innerHTML = note ? `<b>${note === proposalOf(q, row.dataset.qualityItem) ? 'Proposed clarification' : 'Clarification'}</b> ${mathText(note, h)}` : 'No clarification';
        renderMath(shown);
      }
      // Copying an empty clarification would only clear the other versions' own.
      const copy = row.querySelector('[data-copy-clarification]');
      if (copy) copy.disabled = !note;
    });
    concernDesk?.update();
  }
  // Mirroring an exclusion (LINKED VERSIONS): a pair ticked on one version is ticked on each version where BOTH lines have a copy
  // linked as the same; never through a related, unverified or unlinked line, and never onto a built-in rule.
  function mirror(input) {
    const panel = input.closest('.quality-problem'), qid = input.dataset.question;
    if (!panel?.querySelector('[data-mirror-pairs]')?.checked) return;
    const [a, b] = input.dataset.pair.split(SEP), done = [];
    for (const other of problemOf.get(qid) || []) {
      if (other === qid) continue;
      const x = partners(index, qid, a).find(line => line.question_id === other), y = partners(index, qid, b).find(line => line.question_id === other);
      const q2 = byId.get(other);
      if (!x || !y || x.item_id === y.item_id || (q2.builtin || []).some(rule => [x.item_id, y.item_id].includes(rule.item_id))) continue;
      const pair = exclusionKey([x.item_id, y.item_id]);
      if (input.checked) selected.get(other).add(pair); else selected.get(other).delete(pair);
      done.push(`Version ${q2.exam_version}`);
    }
    panel.querySelector('[data-mirror-status]').textContent = done.length ? `${input.checked ? 'Also set' : 'Also cleared'} on ${done.join(', ')}.` : '';
  }
  // A link change is saved at once (POST .../rubric-links) and redraws only its problem's panel, keeping every unsaved clarification.
  async function relink(button, operations) {
    await h.run(button, async () => {
      // In place: the Decisions view reads the same object, so its copies follow the new links too.
      Object.assign(links, await changeLinks(h, aid, operations));
      index = linkIndex(links.sets);
      const qid = operations[0].line.question_id, members = problemOf.get(qid);
      const panel = form.querySelector(`[data-question-panel="${CSS.escape(panelId(qid))}"]`);
      if (!members || !panel) return;
      const fields = members.flatMap(id => byId.get(id).rubric.items.map(item => `clarify${SEP}${id}${SEP}${item.id}`));
      const values = new Map(fields.map(name => [name, form.elements.namedItem(name)?.value]));
      const opened = new Set([...panel.querySelectorAll('details[open]')].map(node => node.dataset.qualityFold || `${node.dataset.qualityQuestion}${SEP}${node.dataset.qualityItem}`));
      const mirrored = panel.querySelector('[data-mirror-pairs]')?.checked ?? true;
      const fresh = document.createElement('div');
      fresh.innerHTML = problemPanel(members);
      panel.replaceChildren(...fresh.firstElementChild.childNodes);
      for (const [name, value] of values) { const area = form.elements.namedItem(name); if (area && value !== undefined) area.value = value; }
      panel.querySelectorAll('details').forEach(node => { if (opened.has(node.dataset.qualityFold || `${node.dataset.qualityQuestion}${SEP}${node.dataset.qualityItem}`)) node.open = true; });
      panel.querySelector('[data-mirror-pairs]').checked = mirrored;
      bind(panel); draw(); renderMath(panel);
      h.toast('Links saved. They change no rubric, guidance or grade.');
    });
  }
  const lineOf = value => { const [question_id, item_id] = value.split(SEP); return {question_id, item_id}; };
  // Ticks are drawn when their line opens, so they are heard on the window itself; `toggle` does not bubble, hence the capture.
  form.addEventListener('toggle', event => { if (event.target.matches?.('.quality-item') && event.target.open) drawPairChoices(event.target); }, true);
  form.addEventListener('change', event => {
    const input = event.target.closest?.('[data-pair]');
    if (!input) return;
    const pairs = selected.get(input.dataset.question);
    if (input.checked) pairs.add(input.dataset.pair); else pairs.delete(input.dataset.pair);
    mirror(input);
    draw();
  });
  function bind(root) {
    root.querySelectorAll('[data-clarification]').forEach(area => area.addEventListener('input', draw));
    root.querySelectorAll('[data-adopt]').forEach(button => button.onclick = () => {
      const q = questions.find(x => x.question_id === button.dataset.adopt), s = entry(check.result, q);
      selected.set(q.question_id, new Set((s.pairs || []).map(exclusionKey)));
      for (const item of q.rubric.items) {
        const area = form.querySelector(`[name="${CSS.escape(`clarify${SEP}${q.question_id}${SEP}${item.id}`)}"]`);
        if (area) area.value = s.clarifications?.[item.id] || '';
      }
      button.disabled = true;
      draw();
    });
    // Copying a clarification is explicit and only along "same" links; each copy lands in its own version's field to adjust.
    root.querySelectorAll('[data-copy-clarification]').forEach(button => button.onclick = () => {
      const {question_id: qid, item_id: iid} = lineOf(button.dataset.copyClarification);
      const text = form.elements.namedItem(`clarify${SEP}${qid}${SEP}${iid}`).value;
      const targets = partners(index, qid, iid).filter(line => (problemOf.get(qid) || []).includes(line.question_id));
      for (const line of targets) {
        const area = form.elements.namedItem(`clarify${SEP}${line.question_id}${SEP}${line.item_id}`);
        if (area) { area.value = text; area.closest('details').open = true; }
      }
      button.closest('.quality-item').querySelector('[data-copy-status]').textContent = copyNote(targets.map(line => lineLabel(line.question_id, line.item_id)), text);
      draw();
    });
    root.querySelectorAll('[data-unlink-line]').forEach(button => button.onclick = () => relink(button, [{op: 'unlink', line: lineOf(button.dataset.unlinkLine)}]));
    root.querySelectorAll('[data-link-kind]').forEach(button => button.onclick = () => relink(button, [{op: 'kind', line: lineOf(button.dataset.linkLine), kind: button.dataset.linkKind}]));
    root.querySelectorAll('[data-link-line-to]').forEach(button => button.onclick = () => {
      const from = button.dataset.linkLineTo, to = form.elements.namedItem(`link-to${SEP}${from}`).value, kind = form.elements.namedItem(`link-kind${SEP}${from}`).value;
      if (!to) { h.toast('Choose the line of another version to link it to.', true); return; }
      void relink(button, [{op: 'link', line: lineOf(from), to: lineOf(to), kind}]);
    });
  }
  bind(form);
  form.querySelector('[data-grading-action="resolve-concerns"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
    // No comment is required: the reviewer settles every concern without one itself (v3).
    const responses = [...form.querySelectorAll('[data-concern-comment]')].map(area => ({concern: Number(area.dataset.concernComment), response: area.value.trim()})).filter(row => row.response);
    await h.api(`${base}/resolve`, 'POST', {check_id: check.id, input_sha256: status.input_sha256, request_id: resolveRequest, responses, questions: current()});
    if (!active()) return;
    h.close();
    h.toast('The reviewer is resolving the concerns. Open Review and confirm when it finishes.');
    await refresh();
  }));
  // The confirmation tick and any refusal sit beside the Confirm button (2026-09-25): at the foot of a long window they went unseen,
  // and "confirm them first" read as if the open concerns blocked confirming.
  const footer = form.querySelector('.dialog-footer'), tick = form.querySelector('[name="confirm-quality"]').closest('label');
  tick.classList.add('quality-confirm-tick');
  const approveTick = tick.querySelector('input'), approveLabel = document.createElement('span');
  approveLabel.textContent = 'I approve these rubrics and their guidance for grading.';
  approveTick.setAttribute('aria-label', 'I approve these rubrics and their guidance for grading');
  tick.replaceChildren(approveTick, approveLabel);
  footer.prepend(form.querySelector('.form-error'), tick);
  const approve = footer.querySelector('button[type="submit"]');
  if (approve) { approve.setAttribute('aria-label', 'Confirm and approve the rubrics'); approve.textContent = 'Approve rubrics'; }
  const popover = form.querySelector('[data-rubric-popover]');
  function showRubric(button) {
    const ids = button.dataset.showRubric.split(' '), named = new Set(button.dataset.rubricItems.split(' ').filter(Boolean));
    popover.innerHTML = `<header class="quality-rubric-popover-head"><h4>${ids.length > 1 ? 'Rubrics' : 'Rubric'}</h4><button type="button" class="button" data-rubric-close>Close</button></header>${ids.map(id => questions.find(q => q.question_id === id)).filter(Boolean).map(q => {
      const display = rubricDisplay(q.rubric), rules = new Map((q.builtin || []).map(rule => [rule.item_id, rule.rule]));
      const pairs = [...selected.get(q.question_id)].map(pair => pair.split(SEP));
      return `<section><h5>${h.e(questionLabel(q))} · ${h.e(q.max_points)} points</h5><ol class="quality-rubric-lines">${q.rubric.items.map(item => {
        const note = String(form.elements.namedItem(`clarify${SEP}${q.question_id}${SEP}${item.id}`)?.value || '').trim();
        const apart = pairs.filter(pair => pair.includes(item.id)).map(pair => pair.find(id => id !== item.id));
        return `<li class="${named.has(item.id) ? 'is-named' : ''}"><strong>${h.e(display.label(item.id))} · ${h.e(item.points)} points</strong>${named.has(item.id) ? ' <span class="grading-chip grading-chip-attention">Named in this concern</span>' : ''}<div>${mathText(item.description, h)}</div>`
          + (rules.has(item.id) ? `<small class="supporting-copy">${h.e(RULES[rules.get(item.id)])}: excludes every other item.</small>` : apart.length ? `<small class="supporting-copy">Cannot combine with items ${h.e(display.list(apart))}</small>` : '')
          + (q.comments?.[item.id] ? `<small class="supporting-copy">Your comment for the reviewer: ${mathText(q.comments[item.id], h)}</small>` : '')
          + (note ? `<small class="supporting-copy">Clarification: ${mathText(note, h)}</small>` : '') + '</li>';
      }).join('')}</ol></section>`;
    }).join('')}`;
    popover.querySelector('[data-rubric-close]').onclick = () => popover.hidePopover();
    renderMath(popover);
    popover.showPopover();
    popover.querySelector('[data-rubric-close]').focus();
  }
  form.addEventListener('click', event => { const button = event.target.closest('[data-show-rubric]'); if (button) showRubric(button); });
  draw(); renderMath(form);
}

// A CHECK ASKS ONLY WHAT DOES NOT STAND (requirement, 2026-10-05): when the server names the questions a check would ask
// (`to_check`), the button and Check details say which, by whole versions where they can. Null for a check of every rubric.
function checkScope(status) {
  const ids = new Set(status.to_check || []), questions = status.context?.questions || [];
  if (!ids.size || !questions.length) return null;
  const versions = [...new Set(questions.map(q => q.exam_version))].sort();
  const whole = versions.filter(v => v && questions.filter(q => q.exam_version === v).every(q => ids.has(q.question_id)));
  const asked = questions.filter(q => ids.has(q.question_id)), confirmed = versions.filter(v => v && !whole.includes(v));
  const list = names => names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0];
  const n = asked.length, them = n === 1 ? 'its' : 'their';
  // A CHECK ASKS ONLY WHAT CHANGED (2026-10-25): the others keep what the last check gave them, confirmed or not.
  if (status.carried) {
    return {label: `Check the ${n} changed rubric${n === 1 ? '' : 's'}`,
      note: `Only the ${n} rubric${n === 1 ? '' : 's'} changed since the last check ${n === 1 ? 'is' : 'are'} checked, each beside ${them} problem’s other versions. Every other rubric keeps the clarifications and exclusions you settled, and the concerns still open on them stay listed.`};
  }
  if (versions.length > 1 && asked.every(q => whole.includes(q.exam_version))) {
    return {label: `Check Version${whole.length > 1 ? 's' : ''} ${list(whole)}`,
      note: `Only Version${whole.length > 1 ? 's' : ''} ${list(whole)} ${whole.length > 1 ? 'are' : 'is'} checked, beside the confirmed Version${confirmed.length > 1 ? 's' : ''} ${list(confirmed)} as the reference. Those keep ${confirmed.length > 1 ? 'their' : 'its'} confirmed clarifications and exclusions.`};
  }
  return {label: `Check the ${n} changed rubric${n === 1 ? '' : 's'}`,
    note: `Only the ${n} rubric${n === 1 ? '' : 's'} without a standing confirmation ${n === 1 ? 'is' : 'are'} checked, each beside ${them} problem’s confirmed versions. Every other rubric keeps its confirmed clarifications and exclusions.`};
}

// `actionHost` (optional): the step header's slot. The panel's one primary (Review and confirm, or the check that has to run) is
// drawn there instead of in the panel while `promote()` says the header has nothing more pressing, so step 3 shows its next thing
// at the top, once (review fix GS-D1). Otherwise it stays in the panel as an ordinary button.
export function mountRubricQuality(host, plan, h, refresh, active, open = false, onStatus = null, {actionHost = null, promote = () => true, question = ''} = {}) {
  const base = `/api/grading/assignments/${plan.id}/rubric-quality`, live = () => active() && host.isConnected;
  const requests = new Map();
  let timer = null, choices = null;
  // RUBRIC CHOICES (2026-10-24): the instructor's quick answers for this assignment, read once and shown beside the check.
  h.api(`/api/grading/assignments/${plan.id}/rubric-choices`).then(read => { choices = read; if (live()) void update(); }).catch(() => {});
  const choicesLine = () => {
    if (!choices) return '';
    const said = choicesSummary(choices.options, choices.choices);
    return `<p class="supporting-copy" data-rubric-choices-line>${said ? `Your choices: ${h.e(said)}. The check applies them instead of asking.` : 'No quick choices for this assignment: the check asks about work, rounding, spelling, letters and notation, and the “all incorrect” line where they matter.'} <button type="button" class="text-button" data-grading-action="rubric-choices">${said ? 'Change choices' : 'Set choices'}</button></p>`;
  };
  async function update() {
    if (!live()) return;
    try {
      const status = await h.api(base);
      if (!live()) return;
      onStatus?.(status);
      const busy = ['pending', 'running'].includes(status.state), ready = ['needs_review', 'confirmed'].includes(status.state);
      // Once the finalization is confirmed and current, or a finished check waits for review, running the check again (a paid run
      // that changes nothing by itself) waits under Check details instead of beside the review.
      const finalized = status.state === 'confirmed', runnable = !busy && status.state !== 'missing' && !status.unconfirmed?.length;
      const scope = checkScope(status);
      const runButton = h.button('run-quality', status.state === 'failed' ? 'Retry rubric check' : scope ? scope.label : status.check?.current ? 'Run rubric check again' : 'Run rubric check');
      const clarified = status.review ? status.review.policy.questions.reduce((n, q) => n + Object.keys(q.clarifications || {}).length, 0) : 0;
      host.innerHTML = `<section class="grading-panel quality-status"><div class="grading-heading"><div><h3>Rubric finalization <span class="grading-chip grading-chip-${status.state === 'confirmed' ? 'done' : status.state === 'failed' ? 'failed' : 'todo'}">${h.e(labels[status.state])}</span></h3><p class="supporting-copy">Required before grading</p></div></div><p>${status.state === 'confirmed' ? h.e(`${clarified} clarification${clarified === 1 ? '' : 's'} and the item exclusions are confirmed. Clarifications never change the rubric’s wording or points.`)
        : status.check?.error && status.check.current ? h.e(status.check.error) : status.state === 'missing' ? h.e(`Still needed: ${status.missing.join(', ') || 'rubrics'}.`)
        : status.unconfirmed?.length && !busy && !ready ? `${h.e(`Confirm each rubric for review in Rubrics & Templates first (${status.unconfirmed.length} not confirmed: ${status.unconfirmed.join(', ')}).`)} <a href="#grading/${h.e(plan.id)}?tab=rubrics">Rubrics &amp; Templates</a>`
        : busy && status.check?.resolves ? h.e(`Resolving the concerns you commented on${status.check.progress ? `: ${status.check.progress.completed} of ${status.check.progress.total} problems answered` : ''}. You can keep working meanwhile.`)
        : busy ? h.e(`Checking the rubrics${status.check?.progress ? `: ${status.check.progress.completed} of ${status.check.progress.total} questions finished` : ''}. You can keep working while the check runs.`)
        : status.resolution_failed && status.state === 'needs_review' ? h.e(`${status.resolution_failed.cancelled ? 'Resolving the concerns was cancelled' : `Resolving the concerns failed: ${status.resolution_failed.error}`}. The review it answered is unchanged, with your comments kept: open it to try again or confirm.`)
        : status.check?.resolves && status.state === 'needs_review' ? 'The reviewer answered your comments. Review what it changed and which concerns are still open, then confirm.'
        : status.rerun_failed && status.state === 'needs_review' ? h.e(`${status.rerun_failed.cancelled ? 'The newer check was cancelled' : `The newer check failed: ${status.rerun_failed.error || 'the reviewer did not answer'}`}. This is the last finished check of these rubrics: review and confirm it.`)
        : status.state === 'needs_review' && status.unresolved?.length ? h.e(`${status.unresolved.length} question${status.unresolved.length === 1 ? '' : 's'} of your confirmed finalization still allow more points than they are worth, so some of their lines must be alternatives. Open the review and mark them; only those questions are held back meanwhile.`)
        : status.state === 'needs_review' ? 'Review the suggested clarifications, exclusions and suspected errors, edit them, then confirm. One confirmation approves every rubric.'
        : scope ? h.e(`${scope.note} Then review what the check suggests and approve the rubrics.`)
        : 'Run the check, review any errors and item combinations, then approve the rubrics.'}</p>${finalized ? '' : choicesLine()}<div class="grading-actions">${ready ? h.button('review-quality', finalized ? 'View or edit the finalization' : 'Review and confirm') : ''}${runnable && !ready ? runButton : ''}${busy ? h.button('cancel-quality', status.check?.resolves ? 'Cancel resolving' : 'Cancel this check') : ''}</div><details class="grading-tools" data-quality-details><summary>Check details</summary><p>${h.e(status.model || 'Choose a Rubric drafting and review model in Compute settings')}</p><p>Clarifications guide the graders. They do not change the original rubric wording or point values.</p>${runnable && ready ? `<p class="supporting-copy">${scope ? h.e(`${scope.note} A paid model run; its suggestions replace the ones waiting for your review.`) : finalized ? 'Running the check again has the reviewer model read every rubric once more (a paid model run); nothing changes until you review and confirm what it suggests.' : 'Running the check again has the reviewer model read every rubric once more (a paid model run); its suggestions replace the ones waiting for your review.'}</p><div class="grading-actions">${runButton}</div>` : ''}</details></section>`;
      // One primary: the review while something waits for it, else the check that has to run. A finalized plan's next step is Grade.
      // It moves to the step header when the page gives one and nothing there comes first; the panel then keeps no copy.
      actionHost?.replaceChildren();
      const main = finalized ? null : host.querySelector('.quality-status > .grading-actions > :is([data-grading-action="review-quality"], [data-grading-action="run-quality"])');
      if (main && actionHost && promote()) {
        main.classList.add('primary'); actionHost.append(main);
        // A review that waits keeps a quiet way in from the panel too: a check finishing below the fold left the panel with nothing
        // to open it (requirement, 2026-10-02). The header keeps the one primary.
        if (main.dataset.gradingAction === 'review-quality') host.querySelector('.quality-status > .grading-actions').insertAdjacentHTML('afterbegin', h.button('open-review', 'Open the review'));
      } else if (main && !actionHost) main.classList.add('primary');
      const find = selector => host.querySelector(selector) || actionHost?.querySelector(selector);
      const review = event => h.run(event.currentTarget, () => reviewRubricQuality(plan.id, h, refresh, live, question));
      find('[data-grading-action="review-quality"]')?.addEventListener('click', review);
      host.querySelector('[data-grading-action="open-review"]')?.addEventListener('click', review);
      find('[data-grading-action="run-quality"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
        if (!requests.has(status.input_sha256)) requests.set(status.input_sha256, crypto.randomUUID());
        await h.api(`${base}/check`, 'POST', {input_sha256: status.input_sha256, request_id: requests.get(status.input_sha256)});
        requests.delete(status.input_sha256);  // Asking again later is a new check, not a retry of this request.
        if (live()) await refresh();
      }));
      host.querySelector('[data-grading-action="rubric-choices"]')?.addEventListener('click', event => h.run(event.currentTarget, () =>
        editPlanChoices(plan.id, h, saved => { choices = saved; if (live()) void update(); })));
      host.querySelector('[data-grading-action="cancel-quality"]')?.addEventListener('click', event => h.run(event.currentTarget, async () => {
        await h.api(`${base}/cancel`, 'POST', {});
        if (live()) await refresh();
      }));
      if (ready && open && !document.querySelector('dialog[open]')) {
        open = false;
        await reviewRubricQuality(plan.id, h, refresh, live, question);
      }
      if (busy) timer = setTimeout(() => whenVisible(update), 3000);  // a hidden tab reads once it is shown again
    } catch (error) {
      if (live()) { actionHost?.replaceChildren(); host.innerHTML = `<p role="alert">${h.e(error.message)}</p>${h.button('retry-quality-load', 'Reload rubric finalization')}`; host.querySelector('button').onclick = update; }
    }
  }
  void update();
  return () => clearTimeout(timer);
}
