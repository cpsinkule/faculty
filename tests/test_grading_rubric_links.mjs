import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../static/grading-rubric-links.js', import.meta.url), 'utf8');
const {linkIndex, linkOf, partners, namesValues, copyNote, passedOn} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const line = (question_id, item_id) => ({question_id, item_id});
const sets = [{kind: 'same', origin: 'reviewer', lines: [line('a', 'R1'), line('b', 'R1'), line('c', 'R2')]},
  {kind: 'related', origin: 'reviewer', note: 'Defined on C.', lines: [line('a', 'R2'), line('b', 'R2'), line('c', 'R3')]},
  {kind: 'seed', origin: 'seed', lines: [line('a', 'R3'), line('b', 'R3')]},
  {kind: 'same', origin: 'instructor', conflict: true, lines: [line('a', 'R4'), line('b', 'R4'), line('b', 'R5')]}];

test('LINKED VERSIONS: an edit is offered only along "same" links, never related, unverified or conflicting ones', () => {
  const index = linkIndex(sets);
  assert.deepEqual(partners(index, 'a', 'R1'), [line('b', 'R1'), line('c', 'R2')]);
  assert.deepEqual(partners(index, 'c', 'R3'), []);  // the matrix case: same step, another outcome on each version
  assert.deepEqual(partners(index, 'a', 'R3'), []);
  assert.deepEqual(partners(index, 'a', 'R3', ['same', 'seed']), [line('b', 'R3')]);
  assert.deepEqual(partners(index, 'a', 'R4'), []);
  assert.equal(linkOf(index, 'c', 'R3').note, 'Defined on C.');
  assert.equal(linkOf(index, 'z', 'R1'), null);
});

test('LINKED VERSIONS: a copied clarification that names values asks for a check of each version', () => {
  assert.equal(namesValues('Accept an abbreviation of the word "undefined".'), false);
  for (const text of ['Accept y = -5.', 'Accept $$\\frac{1}{2}$$', 'the result \\(AB\\)']) assert.equal(namesValues(text), true);
  assert.equal(copyNote([], 'x'), '');
  assert.equal(copyNote(['Version B · Item 2'], 'Accept DNE.'), 'Copied to Version B · Item 2.');
  assert.match(copyNote(['Version B · Item 2', 'Version C · Item 3'], 'Accept 2×3.'), /names values: check/);
});

test('LINKED VERSIONS: only the sentence the instructor added travels to another version, after its own text', () => {
  const a = 'Apply when the bottom row keeps +18, +15.', b = 'Apply when the bottom row keeps +120, +150.';
  const added = `${a} The student's own letters count.`;
  assert.deepEqual(passedOn(a, added, b), {kind: 'add', added: "The student's own letters count.", text: `${b} The student's own letters count.`});
  assert.equal(passedOn(a, `Note first. ${a}`, b).text, `Note first. ${b}`);
  assert.equal(passedOn('', 'Accept DNE.', b).text, `${b} Accept DNE.`);
  assert.equal(passedOn('', 'Accept DNE.', '').text, 'Accept DNE.');
  assert.equal(passedOn('No full stop', 'No full stop and more', 'Theirs').text, 'Theirs and more');
  assert.equal(passedOn('Old', 'Old More', 'Theirs').text, 'Theirs. More');
  // A rewrite never replaces another version's own wording or numbers.
  assert.deepEqual(passedOn(a, 'Apply when the objective row is wrong.', b), {kind: 'rewrite', added: '', text: b});
  assert.equal(passedOn(a, a, b).kind, 'none');
});
