// One recording's page (static/recording-page.js, RECORDINGS step 2): a line of the transcript, the line being said, the words a
// find marks, the speakers, and Plaud's own notes folded and said to be Plaud's. The people and their words are invented.
import assert from "node:assert/strict";
import test from "node:test";
import { findWords, holds, lineAt, lineMarkup, markedText, offsetText, pageSubtitle, plaudMarkup, speakersMarkup, transcriptMarkup } from "../static/recording-page.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const lines = [
  { line: 0, start_ms: 500, end_ms: 3200, at: "0:00", speaker: "Chris", speaker_key: "Speaker 1", text: "Calculators stay in the bag for Exam 3." },
  { line: 1, start_ms: 3200, end_ms: 6100, at: "0:03", speaker: "Avery <Quill>", speaker_key: "Speaker 2", text: "And the <seating> charts go out Thursday." },
  { line: 2, start_ms: 6100, end_ms: 8800, at: "0:06", speaker: "Avery <Quill>", speaker_key: "Speaker 2", text: "All of them." },
];
const ready = { filing: "filed", transcript: { state: "ready", lines: 3 }, lines, fetch: { status: "kept", detail: "" } };

test("how far in reads as a clock, and the line being said is the last one that has begun", () => {
  assert.deepEqual([0, 59999, 754000, 3723000].map(offsetText), ["0:00", "0:59", "12:34", "1:02:03"]);
  assert.deepEqual([0, 499, 500, 3199, 3200, 7000, 99999].map((ms) => lineAt(lines, ms)), [-1, -1, 0, 0, 1, 2, 2]);
  assert.equal(lineAt([], 100), -1);
});

test("a find looks for every word, whatever its case, and marks each where it is, in escaped text", () => {
  assert.deepEqual(findWords('  "Calculators,"  calculators Exam   3?  '), ["calculators", "exam", "3"]);
  assert.equal(findWords("a b c d e f g h i j").length, 8);
  assert.ok(holds("No Calculators on Exam 3", ["calculators", "exam"]));
  assert.ok(!holds("No calculators", ["calculators", "exam"]));
  assert.ok(!holds("anything", []));
  assert.equal(markedText("A <b> for 100% (really)", ["100%", "(really)"], escape), "A &lt;b&gt; for <mark>100%</mark> <mark>(really)</mark>");
  assert.equal(markedText("Exam exam EXAM", ["exam"], escape), "<mark>Exam</mark> <mark>exam</mark> <mark>EXAM</mark>");
  assert.equal(markedText("<i>plain</i>", [], escape), "&lt;i&gt;plain&lt;/i&gt;");
});

test("a line's time is the control that plays from there, and the speaker is printed where the voice changes", () => {
  const first = lineMarkup(lines[1], lines[0], [], escape);
  assert.match(first, /<li class="recording-line" data-patch-key="line-1" data-line="1">/);
  assert.match(first, /<button type="button" class="recording-line-time" data-play="3200" aria-label="Play from 0:03">0:03<\/button>/);
  assert.match(first, /<span class="recording-line-speaker">Avery &lt;Quill&gt;<\/span>/);
  assert.match(first, /<p class="recording-line-text">And the &lt;seating&gt; charts go out Thursday\.<\/p>/);
  assert.match(lineMarkup(lines[2], lines[1], [], escape), /<span class="recording-line-speaker"><\/span>/);   // the same voice goes on
  assert.match(lineMarkup(lines[1], lines[0], ["seating"], escape), /data-line="1" data-found>[^]*<mark>seating<\/mark>/);
  assert.doesNotMatch(lineMarkup(lines[1], lines[0], ["calculators"], escape), /data-found/);
});

test("the transcript is every line in order, or one plain sentence saying why there is none", () => {
  const whole = transcriptMarkup(ready, [], escape);
  assert.equal((whole.match(/<li class="recording-line"/g) || []).length, 3);
  assert.ok(whole.startsWith('<ol class="recording-lines">'));
  assert.match(transcriptMarkup({ ...ready, transcript: { state: "waiting" }, lines: [] }, [], escape), /Faculty is reading this transcript into lines/);
  assert.match(transcriptMarkup({ ...ready, transcript: { state: "unreadable" }, lines: [] }, [], escape), /not in a shape Faculty reads\. Its file is kept\./);
  assert.match(transcriptMarkup({ ...ready, transcript: { state: "none" }, lines: [], fetch: { detail: "Plaud has no transcript for it yet." } }, [], escape),
    /Plaud has no transcript for it yet\./);
  assert.match(transcriptMarkup({ filing: "needs_filing", transcript: { state: "none" }, lines: [] }, [], escape),
    /Faculty has fetched nothing of this recording: it waits for your Keep or Don’t keep on the Recordings page\./);
});

test("each speaker is a control that names them here, and says what Plaud calls one already named", () => {
  const row = speakersMarkup([{ key: "Speaker 1", plaud: "Chris", name: null, shown: "Chris", lines: 2 },
    { key: "Speaker 2", plaud: "Speaker 2", name: "Avery <Quill>", shown: "Avery <Quill>", lines: 1 }], escape);
  assert.match(row, /<span class="recording-speakers-label">Speakers<\/span>/);
  assert.match(row, /<button type="button" class="text-button" data-speaker="Speaker 1"\s+title="Name this speaker">Chris<\/button>/);
  assert.match(row, /data-speaker="Speaker 2"\s+title="Plaud calls them “Speaker 2”\. Change the name">Avery &lt;Quill&gt;<\/button>/);
  assert.equal(speakersMarkup([], escape), "");
});

test("Plaud's summary and outline are folded and said to be Plaud's words, as text and never as markup", () => {
  const plaud = plaudMarkup({ outline: [{ start_ms: 6100, topic: "Rooms & <labs>" }], notes: [{ tab: "Summary", content: "## Exam 3\n\n<b>Calculators</b> are not allowed." }] }, escape);
  assert.match(plaud, /<details class="disclosure recording-plaud" data-disclosure-key="recording-plaud">/);   // folded
  assert.match(plaud, /Plaud&#39;s summary and outline<\/span><span class="disclosure-hint">Plaud&#39;s words, not Faculty&#39;s<\/span>/);
  assert.match(plaud, /It can be wrong; the transcript and the audio are what was said\./);
  assert.match(plaud, /data-play="6100" aria-label="Play from 0:06">0:06<\/button>\s*<span>Rooms &amp; &lt;labs&gt;<\/span>/);
  assert.match(plaud, /<h4 class="recording-note-tab">Summary<\/h4><div class="recording-note">## Exam 3\n\n&lt;b&gt;Calculators&lt;\/b&gt; are not allowed\.<\/div>/);
  assert.equal(plaudMarkup({ outline: [], notes: [] }, escape), "");
});

test("the page's line under its name is its day, and what it is when it is not filed as Faculty's", () => {
  const when = () => "Tue, Nov 10 · 10:02 AM · 1 h 11 min";
  assert.equal(pageSubtitle({ filing: "filed" }, when), "Tue, Nov 10 · 10:02 AM · 1 h 11 min");
  assert.equal(pageSubtitle({ filing: "needs_filing" }, when), "Tue, Nov 10 · 10:02 AM · 1 h 11 min · Waiting for Keep or Don’t keep");
});
