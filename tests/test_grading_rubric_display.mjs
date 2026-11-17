import test from 'node:test';
import assert from 'node:assert/strict';
import {partCapsText, rubricDisplay, warningsMarkup} from '../static/grading-rubric-display.js';
import {groupName, groupLines} from '../static/grading-review-queue.js';

const h = {e: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')};

test('any rubric IDs use full rubric order, with stable numbers for selected subsets', () => {
  for (const ids of [
    Array.from({length: 12}, (_, i) => `R5_${i + 1}`),
    ['zeta', 'line-900', '2', '8b1d42', 'R5_11', 'part.b', 'R5_2', 'alpha'],
  ]) {
    const rules = {items: ids.map((id, i) => ({id, points: i % 3, description: `Description ${i + 1}`}))};
    const original = structuredClone(rules), display = rubricDisplay(rules);
    const selected = [ids.at(-1), ids[1]];
    const group = {kind: 'proposal', applied: selected, items: selected.map(id => rules.items.find(item => item.id === id))};
    assert.equal(display.list(selected), `2, ${ids.length}`);
    assert.equal(groupName(group, rules), `Items 2 + ${ids.length}`);
    const markup = groupLines(group, h, rules);
    assert.ok(markup.indexOf('Description 2') < markup.indexOf(`Description ${ids.length}`));
    assert.ok(markup.includes(`aria-label="Item ${ids.length}"`));
    assert.deepEqual(rules, original);
    assert.deepEqual(group.applied, selected);
    assert.equal(groupName({...group, applied: [ids.at(-1)]}, rules), `Item ${ids.length}`);
  }
});

test('numbering is scoped to the question and missing historical items are never guessed', () => {
  const first = rubricDisplay({items: [{id: 'shared'}, {id: 'another'}]});
  const second = rubricDisplay({items: [{id: 'another'}, {id: 'shared'}]});
  assert.equal(first.number('shared'), 1);
  assert.equal(second.number('shared'), 2);
  assert.equal(first.label('missing'), 'Unavailable rubric item');
  assert.equal(rubricDisplay(null).number('R5_11'), '?');
  assert.equal(groupName({kind: 'hidden'}, null), 'Proposal hidden until your own decision');
  assert.equal(groupName({kind: 'proposal', applied: []}, null), 'No rubric line proposed');
});

test('import notes name items by their label, and fold once the rubric is approved (grading-setup-10)', () => {
  const rules = {items: [{id: 'R7_1'}, {id: 'R7_2'}], warnings: ['Gradescope exports omit item groups.', 'R7_2: review the conditional rule.', 'Z9: not an item.']};
  const draft = warningsMarkup({rubric: {rules, approved_at: null}}, h);
  assert.equal((draft.match(/class="grading-warning"/g) || []).length, 3);
  assert.ok(draft.includes('Item 2: review the conditional rule.'));
  assert.ok(draft.includes('Z9: not an item.'));
  assert.ok(!draft.includes('R7_2'));
  const approved = warningsMarkup({rubric: {rules, approved_at: '2026-10-01T12:00:00Z'}}, h);
  assert.ok(approved.startsWith('<details class="grading-tools grading-import-notes" data-import-notes><summary>Import notes (3)</summary>'), approved);
  assert.ok(!approved.includes('grading-warning'));
  assert.ok(approved.includes('<p class="supporting-copy">Item 2: review the conditional rule.</p>'));
  assert.equal(warningsMarkup({rubric: {rules: {items: [], warnings: []}, approved_at: null}}, h), '');
  assert.deepEqual(rules.warnings, ['Gradescope exports omit item groups.', 'R7_2: review the conditional rule.', 'Z9: not an item.']);
});

test('a deducting rubric names each part\'s cap beside its point range (GRADING "PART CAPS")', () => {
  const groups = [{id: 'part-a-cap', floor: -0.5, ceiling: 0}, {id: 'part-b-cap', floor: -1.5, ceiling: 0}, {id: 'g1', floor: -2, ceiling: 0}];
  assert.equal(partCapsText({groups}), ' · Part A loses at most 0.5 · Part B at most 1.5');
  assert.equal(partCapsText({groups: [groups[2]]}), '');
  assert.equal(partCapsText({items: []}), '');
});
