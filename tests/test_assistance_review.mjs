import assert from 'node:assert/strict';
import test from 'node:test';
import { conflictMarkup, conflictPrompt, conflictContext } from '../static/canvas-conflicts.js';
import { recoveryPrompt } from '../static/assistant-recovery.js';
import { waitingOn } from '../static/student-support.js';

const esc = x => String(x).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');

test('CANVAS: comparison escapes source markup and disallows copying truncated fields', () => {
  const entry = {differences: [{field: 'body', faculty: '<script>bad()</script>', canvas: '<p>Kept</p>', truncated: false}]};
  const html = conflictMarkup(entry, esc, true);
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('shared term draft'));
  entry.differences[0].truncated = true;
  assert.ok(!conflictMarkup(entry, esc, true).includes('data-conflict-keep'));
});

test('CANVAS: typed resolutions are kept across a preview reload and a chosen text can be set aside', () => {
  const entry = {differences: [{field: 'body', faculty: '<p>F</p>', canvas: '<p>C</p>'}, {field: 'title', faculty: 'F', canvas: 'C'}]};
  const plain = conflictMarkup(entry, esc, true);
  assert.ok(plain.startsWith('<details class="disclosure">'));
  assert.equal((plain.match(/data-conflict-keep/g) || []).length, 2);
  const kept = conflictMarkup(entry, esc, true, false, {typed: {0: '<p>Mine</p>'}, chosen: [1]});
  assert.ok(kept.startsWith('<details class="disclosure" open>'));
  assert.ok(kept.includes('&lt;p&gt;Mine&lt;/p&gt;</textarea>'));
  assert.ok(kept.includes('data-conflict-keep="0"'));
  assert.ok(!kept.includes('data-conflict-keep="1"'));
  assert.ok(kept.includes('data-conflict-undo="1"'));
});

test('CANVAS: explicit assistant handoff stays bounded and labels omitted source', () => {
  const entry = {key: 'page:x', label: 'Hours', verdict: 'conflict', differences: Array.from({length: 20}, () =>
    ({field: 'body', faculty: '"'.repeat(20000), canvas: 'x'.repeat(20000)}))};
  const prompt = JSON.stringify(conflictContext('/api/canvas/content/t/preview', entry));
  assert.ok(prompt.length < 20000);
  assert.ok(prompt.includes('omitted_fields'));
  assert.ok(prompt.includes('"truncated":true'));
  assert.equal(conflictPrompt(entry), 'Help resolve the Canvas conflict for Hours.');
});

test('ASSISTANT: recovery requests exact current evidence and excludes uncertain retries', () => {
  // The request names the row the instructor saw, never the raw route (2026-10-09 review); the page goes as review_context.
  const prompt = recoveryPrompt('Morning Canvas sync');
  assert.ok(prompt.includes('“Morning Canvas sync”'));
  assert.ok(!prompt.includes('#'));
  assert.ok(prompt.includes('prepare a fix for my review'));
});

test('TEACHING-WORKFLOWS: reply owed and work owed are visibly distinct', () => {
  const item = {kind: 'followup', state: 'todo', student: {name: 'Student, Alice'}, next: 'Check the grade', suggestions: {obligation: {work_owed: true}}};
  assert.equal(waitingOn(item).who, 'Work still owed');
  item.suggestions.obligation = {reply_owed: true};
  assert.equal(waitingOn(item).who, 'Reply still owed');
});
