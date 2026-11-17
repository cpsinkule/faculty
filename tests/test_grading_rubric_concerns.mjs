import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../static/grading-rubric-concerns.js', import.meta.url), 'utf8').replace(/^import .*$/gm, '');
const {concernGroups, fixedGroups} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const questions = [{question_id: 'a', exam_version: 'A'}, {question_id: 'b', exam_version: 'B'}, {question_id: 'other', exam_version: 'B'}];
const decision = {title: 'Does this count?', decision: 'Choose how to apply this criterion.', group: 'same-policy',
  choices: [{label: 'Accept', feedback: 'Accept this case.', consequence: ''}, {label: 'Deduct', feedback: 'Apply the deduction.', consequence: '−1'}]};
const finding = (id, extra = {}) => ({question_id: id, item_ids: [id + '-line'], message: `Evidence for ${id}.`, ...decision, ...extra});
const result = (rows, extra = {}) => ({families: [['a', 'b']], suspected_errors: rows, ...extra});

test('GRADING: explicit matching decisions on parallel versions keep every original concern number and item ID', () => {
  const groups = concernGroups(result([finding('a'), finding('b')]), questions);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].rows.map(row => [row.index, row.ids, row.issue.item_ids]), [[0, ['a'], ['a-line']], [1, ['b'], ['b-line']]]);
});

test('GRADING: different problems, consequences, comments or decisions never merge', () => {
  for (const other of [finding('other'), finding('b', {decision: 'A different decision'}), finding('b', {comment: 'A different policy'}),
    finding('b', {choices: decision.choices.map(choice => ({...choice, consequence: 'Different'}))}), finding('a')]) {
    assert.equal(concernGroups(result([finding('a'), other]), questions).length, 2);
  }
  assert.equal(concernGroups(result([finding('a'), finding('b')]), questions, new Map([[1, 'An unsent different draft']])).length, 2);
});

test('GRADING: legacy findings and findings without supported choices remain usable without invented alternatives', () => {
  const legacy = id => ({question_id: id, item_ids: [], message: 'Instructor decision needed.'});
  const groups = concernGroups(result([legacy('a'), legacy('b')]), questions);
  assert.equal(groups.length, 2);
  assert.ok(groups.every(group => group.rows[0].issue.choices === undefined));
  const open = concernGroups(result([finding('a', {choices: []}), finding('b', {choices: []})]), questions);
  assert.equal(open.length, 1);
  assert.deepEqual(open[0].rows[0].issue.choices, []);
});

test('GRADING: consistency findings retain server numbering after suspected errors', () => {
  const groups = concernGroups(result([finding('a')], {consistency_issues: [{question_ids: ['a', 'b'], message: 'Different points'}]}), questions);
  assert.deepEqual(groups.map(group => group.rows[0].index), [0, 1]);
  assert.equal(groups[1].rows[0].kind, 'consistency');
});

// plainIds needs the display labels: the module is read with grading-rubric-display.js in front of it.
const display = readFileSync(new URL('../static/grading-rubric-display.js', import.meta.url), 'utf8');
const {plainIds} = await import(`data:text/javascript;base64,${Buffer.from(display + source).toString('base64')}`);

test('GRADING: a concern names rubric lines by their display label, only for the questions it names (grading-setup-17)', () => {
  const rubric = {items: Array.from({length: 12}, (_, i) => ({id: `R7_${i + 1}`}))};
  const named = [{question_id: 'a', rubric}, {question_id: 'other', rubric: {items: [{id: 'R9_1'}]}}];
  assert.equal(plainIds('May R7_10 (0.5) be added to R7_11 (2.5)? R7_1 stays.', named, ['a']), 'May Item 10 (0.5) be added to Item 11 (2.5)? Item 1 stays.');
  assert.equal(plainIds('R9_1 and R7_10', named, ['other']), 'Item 1 and R7_10');  // Another question's IDs stay as written.
  assert.equal(plainIds('R7_100 xR7_1 R7_1x', named, ['a']), 'R7_100 xR7_1 R7_1x');  // Whole words only.
  // An ID without both a letter and a digit is a prose word or a number: never relabelled.
  assert.equal(plainIds('a value of 2 for R1.', [{question_id: 'w', rubric: {items: [{id: 'a'}, {id: '2'}, {id: 'R1'}]}}], ['w']), 'a value of 2 for Item 3.');
  assert.equal(plainIds('', named, ['a']), '');
  assert.equal(plainIds('R7_1', named, []), 'R7_1');
  assert.equal(plainIds('R7_1', named, ['missing']), 'R7_1');
});

// LINKED VERSIONS (requirement, 2026-10-02): the server's `shared` key makes one decision of a finding the reviewer named once for
// several versions, whatever its prose; the instructor may take a version out of it, and every concern number is kept.
test('LINKED VERSIONS: rows of one shared finding are one decision, and a version decided separately stands alone', () => {
  const shared = (id, extra = {}) => ({question_id: id, item_ids: [id + '-line'], message: 'Which values count?', shared: 'p:0', title: 'Which values count?', decision: 'Count the key value?', ...extra});
  const three = [{question_id: 'a', exam_version: 'A'}, {question_id: 'b', exam_version: 'B'}, {question_id: 'c', exam_version: 'C'}];
  const rows = [shared('a'), shared('b'), shared('c')];
  const one = concernGroups({families: [['a', 'b', 'c']], suspected_errors: rows}, three);
  assert.equal(one.length, 1);
  assert.deepEqual(one[0].rows.map(row => row.index), [0, 1, 2]);
  const apart = concernGroups({families: [['a', 'b', 'c']], suspected_errors: rows}, three, new Map(), new Set([2]));
  assert.deepEqual(apart.map(group => group.rows.map(row => row.index)), [[0, 1], [2]]);
  // A draft or a different decision after a resolution keeps rows of one key apart, as before.
  assert.equal(concernGroups({families: [['a', 'b', 'c']], suspected_errors: rows}, three, new Map([[1, 'Only for B']])).length, 2);
  assert.equal(concernGroups({families: [['a', 'b', 'c']], suspected_errors: [shared('a'), shared('b', {decision: 'Now another question'})]}, three).length, 2);
  // Different keys never merge, even with the same prose.
  assert.equal(concernGroups({families: [['a', 'b', 'c']], suspected_errors: [shared('a'), shared('b', {shared: 'p:1'})]}, three).length, 2);
});

test('GRADING "BOOKKEEPING IS FIXED, NOT ASKED": a fixed finding is no decision, keeps its number, and is listed once for all its versions', () => {
  const fixed = {fix: {kind: 'guidance', note: 'Carried with each version’s values.'}};
  const rows = [finding('a', {shared: 'p:0', ...fixed}), finding('b', {shared: 'p:0', ...fixed}), finding('a', {title: 'Another', group: ''})];
  const handled = new Set([0, 1]);
  const groups = concernGroups(result(rows), questions, new Map(), new Set(), handled);
  assert.deepEqual(groups.map(group => group.rows.map(row => row.index)), [[2]]);
  assert.deepEqual(fixedGroups(result(rows), handled).map(group => group.indexes), [[0, 1]]);
  // Undone, it is a decision again under its own numbers.
  assert.deepEqual(concernGroups(result(rows), questions, new Map(), new Set(), new Set()).map(group => group.rows.map(row => row.index)), [[0, 1], [2]]);
});

// ONE QUESTION ONCE (requirement, 2026-10-29: Exam 2's one check asked whether a pair written without parentheses counts on three
// problems, and the letters question on three more): decisions the reviewer gave one `topic` are one decision across problems.
test('ONE QUESTION ONCE: one topic on several problems is one decision with a part per problem; a problem decided apart stands alone', () => {
  const many = [{question_id: 'p1a', exam_version: 'A', title: 'Question 18'}, {question_id: 'p1b', exam_version: 'B', title: 'Question 18'},
    {question_id: 'p2a', exam_version: 'A', title: 'Question 21'}, {question_id: 'p3a', exam_version: 'A', title: 'Question 23'}];
  const asked = (id, extra) => ({question_id: id, item_ids: [], message: `On ${id}.`, title: `Pairs on ${id.slice(0, 2)}?`, decision: `Decide ${id.slice(0, 2)}.`, ...extra});
  const rows = [asked('p1a', {shared: 's1', topic: 'ordered pairs and tuples'}), asked('p1b', {shared: 's1', topic: 'ordered pairs and tuples'}),
    asked('p2a', {topic: 'ordered pairs and tuples'}), asked('p3a', {topic: 'letters and notation'}), asked('p2a', {title: 'Unrelated', decision: 'Other.'})];
  const families = {families: [['p1a', 'p1b'], ['p2a'], ['p3a']]};
  const groups = concernGroups({...families, suspected_errors: rows}, many);
  assert.equal(groups.length, 3);
  assert.deepEqual(groups[0].rows.map(row => row.index), [0, 1, 2]);
  assert.deepEqual(groups[0].parts.map(part => part.rows.map(row => row.index)), [[0, 1], [2]]);
  assert.equal(groups[1].parts, undefined);  // a topic asked once is an ordinary decision
  // Decided apart, a problem leaves the shared decision; a differing unsent draft keeps them apart too.
  assert.equal(concernGroups({...families, suspected_errors: rows}, many, new Map(), new Set(['apart:2'])).length, 4);
  assert.equal(concernGroups({...families, suspected_errors: rows}, many, new Map([[2, 'Something else']])).length, 4);
  // A fixed finding never joins: it is no decision.
  assert.equal(concernGroups({...families, suspected_errors: rows}, many, new Map(), new Set(), new Set([2])).length, 3);
  assert.deepEqual(concernGroups({...families, suspected_errors: rows}, many, new Map(), new Set(), new Set([2]))[0].parts, undefined);
});
