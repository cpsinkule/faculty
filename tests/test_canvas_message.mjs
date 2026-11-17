import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// canvas-message.js imports ui.js (which imports icons.js), so it is loaded from its file.
const message = await import(new URL("../static/canvas-message.js", import.meta.url).href);
const source = await readFile(new URL("../static/canvas-message.js", import.meta.url), "utf8");
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

test("left-out students are grouped by reason, the largest group first", () => {
  const groups = message.byReason([
    { name: "B", reason: "dropped from Section 507 in Faculty" }, { name: "A", reason: "on Section 508’s roster, which you did not choose" },
    { name: "C", reason: "on Section 508’s roster, which you did not choose" },
  ]);
  assert.deepEqual(groups, [{ reason: "on Section 508’s roster, which you did not choose", names: ["A", "C"] },
    { reason: "dropped from Section 507 in Faculty", names: ["B"] }]);
});

test("a sent message names how many it reached and its sections", () => {
  const record = { status: "accepted", recipients: 98, planned: 100, courses: [{ sections: [507] }, { sections: [508, 514] }] };
  assert.equal(message.sentDetail(record), "to 98 students · Sections 507, 508, 514");
  assert.equal(message.sentDetail({ ...record, status: "failed", courses: [{ sections: [507] }] }), "to 100 students · Section 507");
});

test("the list of sent messages escapes what the instructor wrote and labels each outcome", () => {
  const markup = message.sentMarkup([{ id: "m1", subject: "<b>Exam</b>", status: "uncertain", recipients: 0, planned: 3, sent_at: "2026-10-01T17:00:00+00:00",
    courses: [{ sections: [507] }] }], escape);
  assert.match(markup, /&#60;b&#62;Exam/);
  assert.doesNotMatch(markup, /<b>Exam/);
  assert.match(markup, /Unconfirmed/);
  assert.match(markup, /data-cm-check="m1"/);
  assert.equal(message.sentMarkup([], escape), "");
});

test("only the review sends, with its fingerprint, one request ID and the instructor's confirmation", () => {
  assert.doesNotMatch(source, /style="/);
  assert.equal((source.match(/confirmation: "send"/g) || []).length, 1);
  assert.match(source, /fingerprint: preview\.fingerprint, request_id: requestId/);
  assert.match(source, /requestId = crypto\.randomUUID\(\)/);
});

test('ATTACHMENTS: a Canvas message lists its files with where each comes from, Remove while writing and the upload once sent', async () => {
  const {attachmentsMarkup, MAX_BYTES} = await import('../static/canvas-message.js');
  const e = (/** @type {any} */ v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  assert.equal(attachmentsMarkup([], e), '');
  const writing = attachmentsMarkup([{name: 'Review <1>.pdf', size: 31, route: '/api/course-notes/n1/original/review.pdf'}, {name: 'Card.pdf', size: 2_000_000, file: {}}], e);
  assert.match(writing, /Review &lt;1&gt;\.pdf/);
  assert.match(writing, /from Faculty · 1 KB/);
  assert.match(writing, /from your computer · 2\.0 MB/);
  assert.match(writing, /data-cm-detach="1"/);
  const sent = attachmentsMarkup([{name: 'Card.pdf', size: 31, from: 'computer', upload: 'failed', detail: 'user quota exceeded'}], e, {removable: false});
  assert.match(sent, /Canvas did not take it/);
  assert.match(sent, /user quota exceeded/);
  assert.doesNotMatch(sent, /data-cm-detach/);
  assert.match(attachmentsMarkup([{name: 'Big.pdf', size: MAX_BYTES + 1, file: {}}], e), /carries at most 25 MB/);
});
