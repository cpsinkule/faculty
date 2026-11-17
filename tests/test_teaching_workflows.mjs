import assert from 'node:assert/strict';
import test from 'node:test';
import {canvasPostedText, canvasReceipts, canvasSentText, draftRequest} from '../static/teaching-review.js';
import {blueprintMarkup} from '../static/quiz-blueprint.js';
import {eventType, groupEvents} from '../static/calendar-feed.js';

test('TEACHING-WORKFLOWS: office hours use Your events but keep separate appointment identities', () => {
  const one = {source: 'office_hours', id: 'office-hours:a', date: '2026-10-03', time: '14:00', end_time: '14:30', title: 'Office hours'};
  assert.equal(eventType(one), 'custom');
  assert.equal(groupEvents([one, {...one, id: 'office-hours:b'}]).length, 2);
});

test('TEACHING-WORKFLOWS: lesson requests name their exact evidence and remain drafts', () => {
  const request = draftRequest({plan_id:'p1', title:'Quiz', certification:{id:'c1',created_at:'today'},read_at:'now', questions:
    [{title:'Q1',version:'A',question_revision_id:'q1',rubric_id:'r1',objectives:[],n:3,earned_pct:20,statement:'Compute',rubric_lines:[]}]}, 'lesson');
  assert.match(request, /certification c1/);
  assert.match(request, /20-minute review lesson/);
  assert.match(request, /do not change grades/);
  assert.match(request, /"n":3/);
  assert.equal(blueprintMarkup(null, null, String), '');
});

// EACH CANVAS SITUATION BY ITSELF (requirement, 2026-10-11): Return's line said "14 missing, changed or unconfirmed" in one number.
test('TEACHING-WORKFLOWS: Return says each Canvas situation with its own count', () => {
  const columns = [{without_receipt: 3, receipts: {accepted: 300, changed: 4, uncertain: 2}}, {without_receipt: 3, receipts: {accepted: 10, failed: 1, uncertain: 1}}];
  assert.deepEqual(canvasReceipts(columns), {accepted: 310, never: 6, changed: 4, uncertain: 3, failed: 1, open: 14});
  assert.equal(canvasSentText(columns), '310 accepted · 6 never sent · 4 changed since sent · 3 unconfirmed · 1 failed');
  assert.equal(canvasSentText([{without_receipt: 0, receipts: {accepted: 30}}]), '30 accepted');
  assert.equal(canvasSentText([{without_receipt: 30, receipts: {}}]), '30 never sent');
  assert.equal(canvasSentText([]), 'Nothing sent');
  assert.deepEqual(canvasReceipts(undefined), {accepted: 0, never: 0, changed: 0, uncertain: 0, failed: 0, open: 0});
});

// POSTED IN CANVAS (requirement, 2026-10-11): Return never said whether students can see the grades in Canvas.
test('TEACHING-WORKFLOWS: Return says whether students see the grades in Canvas, as of the gradebook\'s last read', () => {
  const when = stamp => `<${stamp.slice(0, 10)}>`;
  const column = (graded, hidden, read_at = '2026-10-11T11:00:00Z') => ({posting: {graded, hidden, courses: 2, manual: true, read_at, source: 'sync'}});
  assert.equal(canvasPostedText([], when), 'Not read from Canvas yet');
  assert.equal(canvasPostedText([{posting: null}], when), 'Not read from Canvas yet');
  assert.equal(canvasPostedText([column(0, 0)], when), 'No grades in Canvas · read <2026-10-11>');
  assert.equal(canvasPostedText([column(310, 0)], when), 'Students see all 310 grades · read <2026-10-11>');
  assert.equal(canvasPostedText([column(310, 14)], when), '14 of 310 grades hidden from students · read <2026-10-11>');
  assert.equal(canvasPostedText([column(310, 310)], when), 'All 310 grades are hidden from students · read <2026-10-11>');
  assert.equal(canvasPostedText([column(1, 0)], when), 'Students see the 1 grade · read <2026-10-11>');
  assert.equal(canvasPostedText([column(1, 1)], when), 'The 1 grade is hidden from students · read <2026-10-11>');
  // TEACHING-WORKFLOWS: combined counts retain the range of their actual read times.
  assert.equal(canvasPostedText([column(300, 10, '2026-10-09T11:00:00Z'), column(20, 4), {posting: null}], when),
    '14 of 320 grades hidden from students · read at different times: <2026-10-09> to <2026-10-11> · 1 column not read yet');
  assert.equal(canvasPostedText(undefined), 'Not read from Canvas yet');
});
