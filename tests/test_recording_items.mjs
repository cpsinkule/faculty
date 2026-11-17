// What came out of a recording (static/recording-items.js, RECORDINGS step 3): what needs the instructor first and alone in orange,
// one sentence an item with the control that plays its quote, what Faculty's records found, what is done folded, and what the
// quote check left out said plainly. The people and their words are invented.
import assert from "node:assert/strict";
import test from "node:test";
import { becameMarkup, controlsMarkup, foundMarkup, itemMarkup, outcomeMarkup, readingLine, stillReading, transcriptLabel } from "../static/recording-items.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const item = (more) => ({ id: "i1", kind: "todo", sentence: "Rebuild the <Exam 3> seating charts.", quote: "Seating charts have to be <rebuilt>.", line: 1,
  start_ms: 24000, end_ms: 28000, at: "0:24", speaker: "Avery <Quill>", for: "you", date: "", time: "", date_words: "", when: "", happening: true,
  about: { what: "none" }, assessment: "", state: "open", memory: null, ruled: "", card: null, found: null, group: "you", ...more });
const read = { state: "read", run: 1, again: false, detail: "", read_at: "2026-11-11T22:05:00Z", engine: "scripted", begins_line: 1, dropped: [], readable: true, you: "Chris", your_name: "Chris Example" };
const count = (markup, pattern) => (markup.match(pattern) || []).length;

test("an item is one sentence, the quote with the control that plays it, and what Faculty found, all escaped", () => {
  const markup = itemMarkup(item({ kind: "date", when: "Thu, Nov 12 · 3:00 PM", found: { state: "open", text: "Not on your calendar on Thu, Nov 12.", href: "#calendar", offer: "calendar" } }), escape);
  assert.match(markup, /data-recording-item="i1"|data-item-row="i1"/);
  assert.match(markup, /<p class="recording-item-sentence">Rebuild the &lt;Exam 3&gt; seating charts\.<\/p>/);
  assert.match(markup, /<button type="button" class="recording-line-time" data-play="24000" aria-label="Play from 0:24">0:24<\/button>/);
  assert.match(markup, /<span class="recording-line-speaker">Avery &lt;Quill&gt;<\/span><q>Seating charts have to be &lt;rebuilt&gt;\.<\/q>/);
  assert.match(markup, /<span>Date<\/span><span>Thu, Nov 12 · 3:00 PM<\/span>/);
  assert.match(markup, /data-state="open">Not on your calendar on Thu, Nov 12\. <a class="link-arrow" href="#calendar">Open<\/a>/);
  assert.ok(!markup.includes("<Exam 3>") && !markup.includes("<rebuilt>"));
  // A meeting called off says so by its day; a ruling names its assessment and question.
  assert.match(itemMarkup(item({ kind: "date", happening: false, when: "Tue, Nov 17" }), escape), /<span>Not on Tue, Nov 17<\/span>/);
  assert.match(itemMarkup(item({ kind: "ruling", group: "rulings", assessment: "Exam 3", about: { what: "assessment", question: "5" } }), escape),
    /<span>Grading<\/span><span>Exam 3 · question 5<\/span>/);
  assert.match(itemMarkup(item({ kind: "waiting", group: "others", for: "coordinator" }), escape), /<span>For the coordinator<\/span>/);
});

test("an item's controls are the instructor's own presses for where it stands", () => {
  const states = (markup) => [...markup.matchAll(/data-item-state="(\w+)"/g)].map((found) => found[1]);
  assert.deepEqual(states(controlsMarkup(item({}), escape)), ["done", "dismissed"]);
  assert.deepEqual(states(controlsMarkup(item({ group: "answers", kind: "answer" }), escape)), ["done"]);
  assert.deepEqual(states(controlsMarkup(item({ group: "rulings", kind: "ruling" }), escape)), ["dismissed"]);
  assert.deepEqual(states(controlsMarkup(item({ group: "done", state: "dismissed" }), escape)), ["reopened"]);
  assert.deepEqual(states(controlsMarkup(item({ group: "done", ruled: "accepted" }), escape)), ["reopened"]);
  // Done only by Faculty's records: nothing of the instructor's to open again.
  assert.deepEqual(states(controlsMarkup(item({ group: "done", found: { state: "done", text: "Already done." } }), escape)), ["dismissed"]);
  // Remember is offered on a standing rule alone, and a calendar entry only where a day is missing and no card waits.
  assert.match(controlsMarkup(item({ kind: "policy" }), escape), /data-item-remember="i1">Remember…/);
  assert.ok(!controlsMarkup(item({ kind: "todo" }), escape).includes("data-item-remember"));
  const offer = { state: "open", text: "Not on your calendar.", offer: "calendar" };
  assert.match(controlsMarkup(item({ kind: "date", found: offer, card: { pending: false, status: "expired" } }), escape), /data-item-card="i1"/);
  assert.ok(!controlsMarkup(item({ kind: "date", found: offer, card: { pending: true } }), escape).includes("data-item-card"));
});

test("a waiting card is drawn by the Assistant's own card, and what became of an item is said under it", () => {
  const card = { id: "a1", pending: true, status: "proposed" };
  assert.match(itemMarkup(item({ card }), escape, { card: (action) => `<article data-action-id="${action.id}"></article>` }),
    /<div class="recording-item-card"><article data-action-id="a1"><\/article><\/div>/);
  assert.ok(!itemMarkup(item({ card: { ...card, pending: false, status: "succeeded" } }), escape, { card: () => "CARD" }).includes("CARD"));
  assert.match(becameMarkup(item({ card: { pending: false, status: "declined" } }), escape), /You declined the calendar entry\./);
  assert.match(becameMarkup(item({ card: { pending: false, status: "expired" } }), escape), /lapsed after a day/);
  assert.match(becameMarkup(item({ group: "done", state: "done", memory: { memory_id: "m1" } }), escape), /Kept as a memory\. <a class="link-arrow" href="#memory">/);
  assert.match(becameMarkup(item({ group: "done", ruled: "declined" }), escape), /Declined at the rubric's finalization\./);
  assert.match(becameMarkup(item({ group: "done", state: "dismissed" }), escape), /You dismissed it\./);
  assert.equal(becameMarkup(item({}), escape), "");
  assert.equal(foundMarkup(null, escape), "");
});

test("the page shows what needs the instructor first, the only orange, and folds what is done and what was left out", () => {
  const data = { reading: { ...read, dropped: [{ sentence: "Bring <donuts>.", reason: "its quote is not in the transcript at that line", quote: "donuts on Friday" }] },
    coordinator: { term_id: "t1", name: "", term: "MATH 140 · Fall 2026" },
    items: [item({ id: "i5", group: "done", state: "done" }), item({ id: "i4", group: "others", kind: "waiting", for: "coordinator" }),
      item({ id: "i3", group: "rulings", kind: "ruling" }), item({ id: "i2", group: "answers", kind: "answer" }), item({ id: "i1" })] };
  const markup = outcomeMarkup(data, escape);
  const order = ["Needs you", "Answers to your questions", "Held for a rubric", "Others said they would", "Already done", "Left out by the quote check"].map((title) => markup.indexOf(title));
  assert.ok(order.every((at, index) => at > (order[index - 1] ?? -1)), String(order));
  assert.equal(count(markup, /data-tone="warning"/g), 1);   // the count of what needs them, and nothing else
  assert.match(markup, /Needs you <span class="chip" data-tone="warning">1<\/span>/);
  assert.match(markup, /<details class="disclosure recording-fold"[^>]*data-disclosure-key="recording-done"(?! open)/);
  assert.match(markup, /Bring &lt;donuts&gt;\./);
  assert.match(markup, /Read by Faculty .* It took “Chris” as you\./);
  assert.match(markup, /data-recording-coordinator>Name the coordinator</);
  assert.match(markup, /data-recording-read>Read again</);
  // A meeting that left nothing says so; a recording with nothing to read shows nothing at all.
  assert.match(outcomeMarkup({ reading: read, items: [], coordinator: {} }, escape), /This meeting left nothing for you/);
  assert.equal(outcomeMarkup({ reading: { state: "none" }, items: [] }, escape), "");
  assert.equal(outcomeMarkup({}, escape), "");
});

test("where the reading stands is one line, in orange only when it asks something of the instructor", () => {
  assert.deepEqual(readingLine({ state: "waiting", detail: "", readable: true }),
    { text: "Faculty is reading this recording. What it left you with shows here in a minute or two.", tone: "neutral" });
  assert.deepEqual(readingLine({ state: "waiting", detail: "The Assistant is off." }), { text: "The Assistant is off.", tone: "warning" });
  assert.deepEqual(readingLine({ state: "failed", detail: "No answer." }), { text: "Faculty could not read this recording. No answer.", tone: "warning" });
  assert.equal(readingLine({ ...read, you: "" }).tone, "warning");   // it could not tell which speaker is the instructor
  assert.match(readingLine({ ...read, you: "" }).text, /name yourself among the speakers above, then Read again/);
  assert.match(readingLine({ ...read, again: true }).text, /Faculty is reading it again now\./);
  // A transcript Plaud changed is read by itself; while that cannot be done the line says why, and never that it is under way.
  assert.match(readingLine({ ...read, changed: true }).text, /Plaud has changed the transcript since: Faculty reads it again in its next round\./);
  const stuck = readingLine({ ...read, changed: true, detail: "The Assistant is off." }).text;
  assert.match(stuck, /Reading it again did not work: The Assistant is off\./);
  assert.doesNotMatch(stuck, /reading it again now|next round/);
  assert.doesNotMatch(outcomeMarkup({ reading: { ...read, changed: true, detail: "The Assistant is off." }, items: [], coordinator: {} }, escape), /data-recording-read disabled/);
  assert.deepEqual(readingLine({ state: "not_read", detail: "Not read yet." }), { text: "Not read yet.", tone: "neutral" });
  // While it is read again the button waits; a recording Faculty does not read offers none.
  assert.match(outcomeMarkup({ reading: { ...read, again: true }, items: [], coordinator: {} }, escape), /data-recording-read disabled>/);
  assert.ok(!outcomeMarkup({ reading: { state: "not_read", detail: "Not read yet.", readable: false }, items: [], coordinator: { term_id: "t1" } }, escape).includes("<button"));
});

test("the page asks again by itself only while a reading is being made, and the transcript's fold says how long it is", () => {
  assert.equal(stillReading({ reading: { state: "waiting", detail: "", readable: true } }), true);
  assert.equal(stillReading({ reading: { state: "waiting", detail: "The Assistant is off.", readable: true } }), false);
  assert.equal(stillReading({ reading: { ...read, again: true } }), true);
  assert.equal(stillReading({ reading: { ...read, changed: true } }), true);
  assert.equal(stillReading({ reading: { ...read, changed: true, detail: "The Assistant is off." } }), false);
  assert.equal(stillReading({ reading: read }), false);
  assert.equal(stillReading({}), false);
  assert.equal(transcriptLabel({ transcript: { lines: 646 } }), "Transcript · 646 lines");
  assert.equal(transcriptLabel({ transcript: { lines: 0 } }), "Transcript");
});
