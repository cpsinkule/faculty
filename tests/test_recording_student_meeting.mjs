// A student's recorded meeting on the page, the timeline and a follow-up (RECORDINGS "Student meetings", step 4;
// static/recording-items.js, static/recording-page.js, static/recordings.js, static/communications.js, static/followup-desk.js):
// who the meeting was with, their two cards, an appointment recorded as one that did not happen in orange, one student's part
// shown alone, and the links back to the moment something was said. The people and their words are invented.
import assert from "node:assert/strict";
import test from "node:test";
import { cardLines, itemMarkup, outcomeMarkup, partText, studentsMarkup } from "../static/recording-items.js";
import { parseOffset, stretched } from "../static/recording-page.js";
import { appointmentOff, recordingRowMarkup } from "../static/recordings.js";
import { FILTERS, channelGroup, matches, recordingHref } from "../static/communications.js";
import { heardLink } from "../static/followup-desk.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const count = (markup, pattern) => (markup.match(pattern) || []).length;
const item = (more) => ({ id: "i1", kind: "todo", sentence: "Regrade question 2 on their <Exam 2>.", quote: "I will regrade question two.", line: 4,
  start_ms: 16000, end_ms: 20000, at: "0:16", speaker: "Chris", for: "you", date: "", time: "", date_words: "", when: "", happening: true,
  about: { what: "none" }, assessment: "", state: "open", memory: null, ruled: "", card: null, found: null, group: "you",
  enrollment_id: "1000000001", followup: "", cards: { commitments: null, note: null }, ...more });
const read = { state: "read", run: 1, again: false, detail: "", read_at: "2026-11-11T22:05:00Z", engine: "scripted", begins_line: 1, dropped: [], readable: true, you: "Chris", your_name: "Chris Example" };
const card = (more) => ({ id: "a1", pending: true, status: "proposed", seen: 3, summary: "Track 2 things", method: "POST", path: "/api/student-support", endpoint: "student_support.create",
  page: "#recordings/r1?t=16000", events: [], body: { commitments: [
    { id: "own", title: "Her own task", waiting_on: "you" },
    { id: "rec-i1", title: "Regrade <question> 2", waiting_on: "you", recording_item_id: "i1" },
    { id: "rec-i2", title: "Send the note", waiting_on: "student", recording_item_id: "i2", check_back: "2026-11-13" }] }, ...more });
const alice = (more) => ({ enrollment_id: "1000000001", name: "Example, <Alice>", sections: [507], part: null, appointment: null, off: "", items: 3, on_followup: 0,
  cards: { commitments: null, note: null }, offer: { commitments: false, note: false }, ...more });
const draw = { card: (action, extra = "") => `<article data-card="${action.id}">${extra}</article>` };

test("a student's meeting says who it was with, in their words for each side, and never names a coordinator", () => {
  const data = { meeting: "student", reading: read, coordinator: { term_id: "t1", name: "", term: "MATH 140 · Fall 2026" }, students: [alice()],
    items: [item(), item({ id: "i2", kind: "waiting", group: "others", for: "others", sentence: "They will send the note." }),
      item({ id: "i3", kind: "answer", group: "answers", sentence: "They said they were sick." })] };
  const markup = outcomeMarkup(data, escape, draw);
  assert.match(markup, /data-recording-students>Students and parts…<\/button>/);
  assert.ok(!markup.includes("data-recording-coordinator"));
  assert.match(markup, /<h3 class="recording-head">Example, &lt;Alice&gt; <span class="recording-count">Section 507<\/span><\/h3>/);
  assert.match(markup, /<h3 class="recording-head">They said they would /);
  assert.match(markup, /<h3 class="recording-head">They told you /);
  assert.match(markup, /<span>You said you would<\/span>/);
  assert.ok(!markup.includes("For others"));   // the heading says whose it is
  // What a student told the instructor may be dismissed: it is then on none of their records.
  assert.match(markup, /data-item-state="dismissed" data-recording-item="i3"/);
  assert.equal(count(markup, /data-tone="warning"/g), 1);   // the count of what needs the instructor, and nothing else
  // A meeting filed under a term is drawn as it always was.
  const staff = outcomeMarkup({ ...data, meeting: "staff", students: [], items: [item({ enrollment_id: "", kind: "answer", group: "answers" })] }, escape, draw);
  assert.ok(!staff.includes("data-recording-students") && !staff.includes('data-item-state="dismissed"'));
  assert.match(staff, /data-recording-coordinator/);
  assert.match(staff, /Answers to your questions/);
});

test("each student has their two cards, drawn as the Assistant's own with what they would do in plain lines", () => {
  const lines = cardLines("commitments", card(), escape);
  assert.equal(lines, '<ul class="recording-card-lines"><li><span class="recording-count">Yours</span> Regrade &lt;question&gt; 2</li>'
    + '<li><span class="recording-count">Theirs</span> Send the note <span class="recording-count">Check back Fri, Nov 13</span></li></ul>');   // only what the meeting adds
  assert.equal(cardLines("note", { body: { text: "Recorded <meeting>.\nYou said you would:" } }, escape), '<div class="recording-card-text">Recorded &lt;meeting&gt;.\nYou said you would:</div>');
  const waiting = studentsMarkup({ meeting: "student", students: [alice({ cards: { commitments: card(), note: null }, offer: { commitments: false, note: true } })] }, escape, draw);
  assert.match(waiting, /<div class="recording-item-card"><article data-card="a1"><ul class="recording-card-lines">/);
  assert.match(waiting, /data-student-card="note" data-student="1000000001">Propose a note<\/button>/);
  assert.ok(!waiting.includes('data-student-card="commitments"'));   // its card is waiting
  // What became of them is said in a line, and nothing is offered twice.
  const done = studentsMarkup({ meeting: "student", students: [alice({ on_followup: 2, cards: { commitments: card({ pending: false, status: "succeeded" }),
    note: { pending: false, status: "succeeded" } } })] }, escape, draw);
  assert.match(done, /2 things from this meeting are on their follow-up\. <a class="link-arrow" href="#follow-ups">Follow-up<\/a>/);
  assert.match(done, /A note of this meeting is in your notes about them\./);
  assert.ok(!done.includes("data-card=") && !done.includes("data-student-card"));
  assert.equal(studentsMarkup({ meeting: "staff", students: [] }, escape, draw), "");
  assert.match(itemMarkup(item({ followup: "f1", group: "done" }), escape), /On their follow-up\. <a class="link-arrow" href="#follow-ups">/);
});

test("an appointment recorded as one that did not happen is the page's orange, on the page and in the list", () => {
  const off = "Their 2:00 PM appointment that day is recorded as a no-show, yet this recording began in it.";
  const markup = studentsMarkup({ meeting: "student", students: [alice({ off })] }, escape, draw);
  assert.match(markup, /<p class="recording-capture" data-tone="warning" data-student-off>Their 2:00 PM appointment that day is recorded as a no-show/);
  const row = { id: "r1", filing: "filed", name: "Example, Alice · MATH 140", date: "2026-11-10", time: "14:02", duration_ms: 720000, kept: ["transcript"], for_you: 0,
    links: [{ kind: "student", label: "Example, Alice · MATH 140", appointment: true, appointment_off: "no_show" }] };
  assert.equal(appointmentOff(row), "Appointment a no-show");
  assert.equal(appointmentOff({ links: [{ appointment_off: "cancelled" }] }), "Appointment cancelled");
  assert.equal(appointmentOff({ links: [{ kind: "student", appointment: true }] }), "");
  assert.match(recordingRowMarkup(row, escape), /<span class="chip" data-tone="warning">Appointment a no-show<\/span>/);
  assert.ok(!recordingRowMarkup({ ...row, links: [] }, escape).includes("data-tone"));
});

test("several students each have their part, and one student's part is shown alone", () => {
  assert.equal(partText({ start_ms: 0, end_ms: 28000 }), "0:00 to 0:28");
  assert.deepEqual([parseOffset("0:28"), parseOffset("12:30"), parseOffset("1:02:30"), parseOffset("90"), parseOffset(" 2:05 ")], [28000, 750000, 3750000, 90000, 125000]);
  assert.deepEqual([parseOffset(""), parseOffset("2:75"), parseOffset("noon"), parseOffset("-3")], [null, null, null, null]);
  const bob = alice({ enrollment_id: "1000000002", name: "Example, Bob", part: { start_ms: 28000, end_ms: 36000 } });
  const data = { meeting: "student", reading: read, students: [alice({ part: { start_ms: 0, end_ms: 28000 } }), bob],
    lines: [{ line: 0, start_ms: 0 }, { line: 4, start_ms: 16000 }, { line: 8, start_ms: 32000 }],
    items: [item(), item({ id: "i9", start_ms: 32000, enrollment_id: "1000000002", sentence: "Post the solution." }), item({ id: "i8", start_ms: 40000, enrollment_id: "" })] };
  const whole = outcomeMarkup(data, escape, draw);
  assert.match(whole, /<span class="recording-count">Section 507 · 0:00 to 0:28<\/span>/);
  assert.match(whole, /<span>Example, &lt;Alice&gt;<\/span>/);   // with several, each item says whose it is
  assert.match(whole, /<span>In nobody&#39;s part<\/span>/);
  assert.equal(stretched(data, null), data);
  const hers = stretched(data, { start: 0, end: 28000 });
  assert.deepEqual([hers.lines.map((line) => line.line), hers.items.map((entry) => entry.id), hers.students.map((student) => student.name)],
    [[0, 4], ["i1"], ["Example, <Alice>"]]);
  const his = stretched(data, { start: 28000, end: 36000 });
  assert.deepEqual([his.lines.map((line) => line.line), his.items.map((entry) => entry.id), his.students.map((student) => student.name)], [[8], ["i9"], ["Example, Bob"]]);
  assert.ok(!outcomeMarkup(his, escape, draw).includes("Regrade"));
  // What the quote check left out carries no place in the recording: it is never shown with one student's part.
  const dropped = { ...data, reading: { ...read, dropped: [{ sentence: "A rule <said> in her part.", reason: "not a kind", quote: "nobody gets a makeup" }] } };
  assert.match(outcomeMarkup(dropped, escape, draw), /Left out by the quote check/);
  assert.ok(!outcomeMarkup(stretched(dropped, { start: 28000, end: 36000 }), escape, draw).includes("Left out by the quote check"));
});

test("a Meeting on the timeline opens its recording, and a follow-up's task the moment it was said", () => {
  const entry = { channel: "meeting", channel_label: "Meeting", subject: "Recorded meeting · 12 min", text: "You said you would:\n- Regrade question 2.",
    author: { name: "Recorded with your recorder" }, recipients: [], context: { source: "Recorded meeting", href: "#recordings/9b1c-22?t=0&to=28000" } };
  assert.equal(channelGroup(entry), "meeting");
  assert.deepEqual(FILTERS.at(-1), ["meeting", "Meetings"]);
  assert.ok(matches(entry, "meeting", "regrade") && !matches(entry, "faculty", "") && matches(entry, "all", "recorded meeting"));
  assert.equal(recordingHref(entry), "#recordings/9b1c-22?t=0&to=28000");
  assert.equal(recordingHref({ ...entry, context: { href: "#recordings/9b1c-22" } }), "#recordings/9b1c-22");
  for (const href of ["https://example.test/", "#mail?thread=1", "#recordings/9b1c-22?t=0&x=<script>", "javascript:alert(1)"]) {
    assert.equal(recordingHref({ ...entry, context: { href } }), "", href);
  }
  assert.equal(recordingHref({ channel: "gmail", context: { href: "#recordings/9b1c-22" } }), "");   // only a Meeting opens a recording
  assert.equal(heardLink({ heard: { href: "#recordings/9b1c-22?t=16000" } }, escape), ' · <a class="link-arrow" href="#recordings/9b1c-22?t=16000">Said in your meeting</a>');
  assert.equal(heardLink({ heard: { href: "https://example.test/" } }, escape), "");
  assert.equal(heardLink({ title: "A task of her own" }, escape), "");
});
