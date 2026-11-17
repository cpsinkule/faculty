import assert from 'node:assert/strict';
import test from 'node:test';
import {supportMatches, triageMarkup, triageStatusMarkup} from '../static/student-support.js';
import {verificationMarkup} from '../static/assistant-workspace.js';
import {esc} from '../static/assistant.js';

test('JEV: FYI remains inspectable and request shortcuts never execute writes', () => {
  const item = {state: 'fyi', attention: false, title: 'Thanks', student: {name: 'Alice'}};
  assert.equal(supportMatches(item, 'active', ''), false);
  assert.equal(supportMatches(item, 'fyi', ''), true);
  assert.equal(supportMatches(item, 'all', ''), true);
  const html = triageMarkup({status: 'complete', label: '<img src=x>', requests: ['absence', 'makeup', 'office_hours', 'grading', 'materials', 'malicious'], mail: {}}, 0, esc);
  assert.ok(html.includes('Draft absence record'));
  assert.ok(html.includes('Open makeups'));
  assert.ok(html.includes('Schedule office hours'));
  assert.ok(!html.includes('malicious'));
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('/api/'));
});

test('JEV: failures, unsupported claims and partial checks remain explicit and escaped', () => {
  const html = verificationMarkup({status: 'review', detail: 'Review sources', partial: true,
    checks: [{paragraph: 1, verdict: 'contradicted', text: '<script>claim</script>'}], sources: []});
  assert.ok(html.includes('Contradicted by a source'));
  assert.ok(html.includes('covered only part'));
  assert.ok(!html.includes('<script>'));
  assert.ok(verificationMarkup({status: 'unverified', detail: 'Check unavailable'}).includes('Check unavailable'));
  assert.ok(triageMarkup({status: 'uncertain', error: 'Not resent'}, 0, esc).includes('Retry conversation analysis'));
});

test('JEV HOST FAILURES: one paused line and one Retry all failed, escaped; nothing when triage is well', () => {
  const paused = 'Student triage paused: OpenAI Decisions unavailable since 10:42 AM. Retrying every 5 minutes.';
  const items = [{triage: {status: 'failed'}}, {triage: {status: 'uncertain'}}, {triage: {status: 'pending'}}, {triage: {status: 'complete'}}, {}];
  const html = triageStatusMarkup({jev: {triage_paused: paused}, items}, esc);
  assert.ok(html.includes(paused));
  assert.ok(html.includes('2 conversation analyses failed.'));
  assert.ok(html.includes('data-support-retry-failed'));
  assert.equal(triageStatusMarkup({jev: {}, items: [{triage: {status: 'complete'}}]}, esc), '');
  assert.ok(!triageStatusMarkup({jev: {triage_paused: '<b>x</b>'}, items: []}, esc).includes('<b>'));
  assert.ok(!triageStatusMarkup({jev: {triage_paused: paused}, items: []}, esc).includes('Retry all failed'));
  assert.ok(triageMarkup({status: 'pending', error: 'Waiting'}, 0, esc).includes('Analyze now'));
});
