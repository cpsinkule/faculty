import assert from 'node:assert/strict';
import test from 'node:test';
import {withCoordinator, shortName, someone, stateLabel, recordPath, observedMessage, replyTarget, joinChoices, waitingOn, supportMatches} from '../static/student-support.js';
import {nextDeskAction} from '../static/followup-desk.js';

// A COORDINATOR'S FOLLOW-UP (TEACHING-WORKFLOWS): the page names the coordinator where it names a student, reads their kept mail
// from the term's record, and offers nothing that is a student's alone.
const LEE = {enrollment_id: 'coordinator:term-1', coordinator: true, name: 'Dr. Jordan Lee', email: 'jlee@tamu.edu', term_id: 'term-1',
  code: 'MATH 140', term_label: 'Fall 2026', sections: []};
const item = {kind: 'followup', enrollment_id: 'coordinator:term-1', term_id: 'term-1', source_key: 'gmail:abc1', title: 'Final exam proctors',
  state: 'needs_reply', attention: true, observed: ['2026-11-15T15:00:00Z', 'm1'], student: LEE};
const alice = {kind: 'followup', enrollment_id: '9000000306', term_id: 'term-1', source_key: 'gmail:abc2', title: 'Absence', state: 'waiting',
  attention: false, student: {name: 'Example, Alice', code: 'MATH 140', sections: [507]}};

test('A COORDINATOR’S FOLLOW-UP: the page names the coordinator, a student as before', () => {
  assert.equal(withCoordinator(item), true);
  assert.equal(withCoordinator(alice), false);
  assert.equal(withCoordinator({student: {name: 'Student in a past term'}}), false);
  assert.deepEqual([shortName(item), someone(item)], ['Dr. Jordan Lee', 'the coordinator']);
  assert.deepEqual([shortName(alice), someone(alice)], ['Alice', 'the student']);
  assert.equal(stateLabel({...item, state: 'waiting'}), 'Waiting on coordinator');
  assert.equal(stateLabel({...item, state: 'todo'}), 'To do');
  assert.equal(stateLabel(alice), 'Waiting on student');
  assert.equal(waitingOn({...item, state: 'waiting', next: 'Their answer about the room'}).who, 'Waiting on Dr. Jordan Lee');
  assert.equal(waitingOn(alice).who, 'Waiting on Alice');
  assert.equal(nextDeskAction({...item, state: 'waiting', attention: false}).title, 'Waiting on the coordinator');
  assert.equal(nextDeskAction(alice).title, 'Waiting on the student');
  assert.equal(supportMatches(item, 'active', 'jordan'), true);
});

test('A COORDINATOR’S FOLLOW-UP: its messages are read from the term’s coordinator record', () => {
  assert.equal(recordPath(item), '/api/communications/coordinators/term-1');
  assert.equal(recordPath(alice), '/api/communications/students/9000000306');
  const entries = [
    {message_id: 'm1', author_role: 'other', roles: ['author'], channel: 'gmail', subject: 'Final exam proctors', context: {thread_id: 'abc1'}},
    {message_id: 'm0', author_role: 'student', roles: ['cc'], channel: 'gmail', subject: 'Exam conflict', context: {thread_id: 'abc0'}}];
  assert.equal(observedMessage(item, entries).message_id, 'm1');
  assert.deepEqual(replyTarget(item, entries), {replyThreadId: 'abc1', subject: 'Re: Final exam proctors'});
  // A student's follow-up never takes a message that someone who is no student wrote, and the coordinator's never a student's.
  assert.equal(observedMessage({...alice, observed: ['', 'm1']}, entries), null);
  assert.equal(observedMessage({...item, observed: ['', 'm0']}, entries), null);
});

test('A COORDINATOR’S FOLLOW-UP: joins stay a student’s conversations alone', () => {
  const other = {...item, source_key: 'gmail:abc9', title: 'Grades due'};
  assert.deepEqual(joinChoices(item, [item, other]), []);
  const second = {...alice, source_key: 'gmail:abc3'};
  assert.equal(joinChoices(alice, [alice, second, item]).length, 1);
});
