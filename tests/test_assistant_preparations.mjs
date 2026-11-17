import assert from 'node:assert/strict';
import { groupRuns, preparationMarkup, preparationRunsMarkup, preparationTiming } from '../static/assistant-preparations.js';

const text = preparationMarkup({workflows: [], preparations: []}, {terms: [{id: 't', code: '<script>', term: 'Fall', year: 2026}]});
assert.ok(text.includes('&lt;script&gt;'));
assert.ok(text.includes('Once, now'));
assert.ok(text.includes('Daily at 5:00 AM Central'));
assert.ok(text.includes('data-preparation-reminder hidden'));
assert.ok(text.includes('required disabled'));
assert.equal(preparationTiming('readiness:exam').automatic, 'When the records change');
assert.equal(preparationTiming('readiness:exam').daily, false);
assert.equal(preparationTiming('reconciliation:term').daily, true);
const mixed = preparationMarkup({}, {exams: [{id: 'e', title: 'Exam'}], terms: [{id: 't'}]});
assert.ok(mixed.includes('When the records change'));
// "Once, now" is the default: a one-off run has no reminder setting (2026-10-09 review), so the field starts hidden.
assert.ok(mixed.includes('data-preparation-reminder hidden'));
assert.ok(!text.includes('<script>'));
const runs = preparationRunsMarkup({workflows: [{id: 'w', title: 'Rule', enabled: false, revision: 2}],
  preparations: [{id: 'r', kind: 'readiness', target: 'a', title: 'Quiz', state: 'ready', explanation: '<unsafe>', actions: [{pending: true}]}]});
assert.ok(runs.includes('Resume'));
assert.ok(runs.includes('Review changes'));
assert.ok(runs.includes('&lt;unsafe&gt;'));
assert.ok(runs.includes('data-prepare-again="r"'));
// A waiting next stage can be cancelled while its stage's cards stay (2026-10-09 review); a running one shows only Stop.
const staged = preparationRunsMarkup({preparations: [{id: 's', kind: 'readiness', target: 'a', title: 'Quiz', state: 'ready', next_stage: true, actions: []}]});
assert.ok(staged.includes('Cancel next stage') && staged.includes('data-stop-preparation="s"'));
const running = preparationRunsMarkup({preparations: [{id: 's', kind: 'readiness', target: 'a', title: 'Quiz', state: 'running', next_stage: true, actions: []}]});
assert.ok(!running.includes('Cancel next stage') && running.includes('Stop preparation'));
console.log('Assistant preparation markup passed');
// Saved groups runs by what they prepare (2026-10-09 review): the newest is shown in full with Prepare again; older drafts fold into
// "N earlier drafts" with state and date only, and carry no form of their own.
const grouped = preparationRunsMarkup({preparations: [
  {id: 'new', kind: 'readiness', target: 'a', title: 'Quiz', state: 'failed', created_at: '2026-10-09T10:00:00+00:00', actions: []},
  {id: 'other', kind: 'reconciliation', target: 't', title: 'Grades', state: 'ready', created_at: '2026-10-09T09:30:00+00:00', actions: []},
  {id: 'mid', kind: 'readiness', target: 'a', title: 'Quiz', state: 'stale', created_at: '2026-10-08T10:00:00+00:00', actions: []},
  {id: 'old', kind: 'readiness', target: 'a', title: 'Quiz', state: 'ready', created_at: '2026-10-07T10:00:00+00:00', actions: []}]});
assert.ok(grouped.includes('data-prepare-again="new"') && grouped.includes('data-prepare-again="other"'));
assert.ok(!grouped.includes('data-prepare-again="mid"') && !grouped.includes('data-prepare-again="old"'));
assert.ok(!grouped.includes('data-patch-key="preparation-mid"'));
assert.ok(grouped.includes('2 earlier drafts'));
assert.ok(grouped.includes('Draft withdrawn · Oct 8'));
assert.ok(grouped.includes('Ready to review · Oct 7'));
assert.equal((grouped.match(/earlier draft/g) || []).length, 1, 'a target with one run folds nothing');
assert.deepEqual(groupRuns([{id: 1, kind: 'k', target: 'a'}, {id: 2, kind: 'k', target: 'b'}, {id: 3, kind: 'k', target: 'a'}]).map(g => g.map(r => r.id)), [[1, 3], [2]]);
console.log('Assistant preparation grouping passed');
