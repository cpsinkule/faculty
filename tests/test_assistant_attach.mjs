import assert from 'node:assert/strict';
import test from 'node:test';
import {attachedMarkup, emailNote, sentFiles, sizeText, MAX_FILES} from '../static/assistant-attach.js';
import {filesMarkup} from '../static/assistant.js';

test('ATTACH IN CHAT: the files waiting to go are named, sized and removable; one still attaching says so', () => {
  assert.equal(attachedMarkup([]), '');
  const html = attachedMarkup([{name: 'Syllabus <b>.pdf', size: 240000}, {name: 'Photo.jpg', size: 2400000, uploading: true}]);
  assert.match(html, /Syllabus &lt;b&gt;\.pdf/);
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /240 KB/);
  assert.match(html, /data-assistant-detach="0"/);
  assert.match(html, /Attaching…/);
  assert.doesNotMatch(html, /data-assistant-detach="1"/);  // no Remove while it uploads
  assert.equal(sizeText(1_400_000), '1.4 MB');
  assert.equal(MAX_FILES, 10);
});

test('TOO LARGE FOR AN EMAIL: a waiting file larger than one email carries says so, and so do files too large together (requirement, 2026-11-12)', () => {
  const large = attachedMarkup([{name: 'Chapter 5.pdf', size: 25_600_000}, {name: 'Syllabus.pdf', size: 240000}]);
  assert.match(large, /25\.6 MB · too large to email \(25 MB at most\)/);
  assert.match(large, /<small data-assistant-progress="1">240 KB<\/small>/);
  assert.doesNotMatch(large, /Together/);
  assert.match(attachedMarkup([{name: 'Chapter 3.pdf', size: 12_800_000}, {name: 'Chapter 5.pdf', size: 12_300_000}]),
    /Together these are too large for one email \(25 MB at most\)\./);
  assert.doesNotMatch(attachedMarkup([{name: 'Chapter 3.pdf', size: 12_800_000}]), /too large/);
  assert.equal(emailNote(25_000_000), '');
  assert.doesNotMatch(attachedMarkup([{name: 'Chapter 5.pdf', size: 18_619_152}]), /too large/);  // the notes that were refused at 18 MB
});

test('ATTACH IN CHAT: a sent message shows its files from their own routes (pictures inline, others as links)', () => {
  const files = sentFiles([{name: 'Board.png', type: 'image/png', size: 900, route: '/api/assistant/uploads/' + 'a'.repeat(32)},
    {name: 'Notes.pdf', type: 'application/pdf', size: 1200, route: '/api/assistant/uploads/' + 'b'.repeat(32)}]);
  assert.deepEqual(files.map((f) => f.kind), ['image', 'pdf']);
  const html = filesMarkup(files);
  assert.match(html, /<img src="\/api\/assistant\/uploads\/a{32}"/);
  assert.match(html, /Notes\.pdf/);
});

test('ATTACH IN CHAT: files chosen together upload side by side, each showing how much of it has gone (requirement, 2026-10-20)', async () => {
  const {createAttachments, progressText} = await import('../static/assistant-attach.js');
  assert.equal(progressText({size: 12_800_000}), 'Attaching…');
  assert.equal(progressText({size: 12_800_000, sent: 4_100_000}), 'Attaching… 4.1 MB of 12.8 MB');
  const lines = new Map();
  const host = {hidden: true, innerHTML: '', addEventListener() {},
    querySelector: (selector) => lines.get(selector) ?? null};
  const pending = [];
  const upload = (file, onProgress) => new Promise((resolve) => pending.push({file, onProgress, resolve}));
  const toasts = [];
  const attach = createAttachments({toast: (m) => toasts.push(m), host, upload});
  const done = attach.add([{name: 'Chapter 3.pdf', size: 12_800_000}, {name: 'Chapter 5.pdf', size: 10_300_000}]);
  assert.equal(pending.length, 2);  // both are on their way before either finishes
  assert.equal(attach.busy(), true);
  assert.equal(attach.ids(), null);
  const line = {textContent: ''};
  lines.set('[data-assistant-progress="1"]', line);
  pending[1].onProgress(5_000_000);
  assert.equal(line.textContent, 'Attaching… 5.0 MB of 10.3 MB');
  pending[1].resolve({files: [{id: 'b'.repeat(32), name: 'Chapter 5.pdf', size: 10_300_000}]});
  pending[0].resolve({files: [{id: 'a'.repeat(32), name: 'Chapter 3.pdf', size: 12_800_000}]});
  await done;
  assert.deepEqual(attach.ids(), ['a'.repeat(32), 'b'.repeat(32)]);  // in the order chosen
  assert.deepEqual(toasts, []);
});
