// A recorded class on its page (RECORDINGS "Classes", step 5; static/recording-items.js): what the class reached beside what
// was planned for that section's day, the one orange line for a planned section that was not reached, its one card with what
// it would write in plain lines, and the days told to the class. The class and its words are invented.
import assert from "node:assert/strict";
import test from "node:test";
import { CLASS_GROUPS, classDayMarkup, controlsMarkup, itemMarkup, moveLines, outcomeMarkup } from "../static/recording-items.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const count = (markup, pattern) => (markup.match(pattern) || []).length;
const item = (more) => ({ id: "i1", kind: "covered", sentence: "The class worked through section 5.6, <Exponential> Functions.", quote: "Today we finish five six.", line: 1,
  start_ms: 4000, end_ms: 8000, at: "0:04", speaker: "Chris", for: "you", date: "", time: "", date_words: "", when: "", happening: true,
  about: { what: "notes", notes: ["5.6"] }, assessment: "", state: "open", memory: null, ruled: "", card: null, found: null, group: "covered",
  enrollment_id: "", followup: "", cards: { commitments: null, note: null }, ...more });
const told = (more) => item({ id: "i2", kind: "announced", group: "you", sentence: "You told the class Exam 3 is next Wednesday.", quote: "exam three is next Wednesday night.",
  date: "2026-11-18", when: "Wed, Nov 18", about: { what: "work", work: "a:x" },
  found: { state: "differs", text: "You told the class Wed, Nov 18. Faculty has Exam 3 given Thu, Nov 19 (its exam record).", href: "#course/g1/section/507/schedule" }, ...more });
const read = { state: "read", run: 1, again: false, detail: "", read_at: "2026-11-11T22:05:00Z", engine: "scripted", begins_line: 1, dropped: [], readable: true, you: "Chris", your_name: "Chris Example" };
const day = (more) => ({ section_id: "s507", section: 507, date: "2026-11-11", day: "Wed, Nov 11", href: "#course/g1/section/507/schedule", state: "differs", text: "Not reached: 5.8.",
  planned: [{ number: "5.6", title: "Exponential Functions" }, { number: "5.7", title: "Combining Functions" }, { number: "5.8", title: "<Logarithms>" }], note: "", own: false,
  reached: [{ number: "5.6", title: "Exponential Functions", start_ms: 4000 }, { number: "5.7", title: "Combining Functions", start_ms: 16000 }],
  not_reached: [{ number: "5.8", title: "<Logarithms>" }], ahead: [], earlier: [], next_day: "2026-11-13", next: "Fri, Nov 13", recordings: ["r1"], card: null, offer: true, ...more });
const card = (more) => ({ id: "a1", pending: true, status: "proposed", seen: 3, summary: "Move 5.8", method: "POST", path: "/api/scheduling/terms/t1/class-schedule",
  endpoint: "scheduling.class_plan_save", page: "#recordings/r1", events: [], body: { base_revision: 1, set: [], remove: [], topics: {
    "2026-11-13": { notes: [], note: "", sections: { s507: { notes: ["5.8"], note: "" }, s508: { notes: ["9.9"], note: "" } } },
    "2026-11-11": { notes: ["5.6", "5.7", "5.8"], note: "", sections: { s507: { notes: ["5.6", "5.7"], note: "" } } } } }, ...more });
const draw = { card: (action, extra = "") => `<article data-card="${action.id}">${extra}</article>` };

test("a class's day says what was reached beside what was planned, and only an unreached planned section is orange", () => {
  const markup = classDayMarkup({ meeting: "class", class_day: day() }, escape, draw);
  assert.match(markup, /<section class="recording-class" data-recording-class data-state="differs">/);
  assert.match(markup, /<h3 class="recording-head">Section 507 · Wed, Nov 11 <a class="link-arrow" href="#course\/g1\/section\/507\/schedule">Class schedule<\/a><\/h3>/);
  assert.match(markup, /<dt>Planned<\/dt><dd>5\.6 Exponential Functions · 5\.7 Combining Functions · 5\.8 &lt;Logarithms&gt;<\/dd><dt>Reached<\/dt><dd>5\.6 Exponential Functions · 5\.7 Combining Functions<\/dd>/);
  assert.match(markup, /<p class="recording-capture" data-tone="warning" data-class-differs>Not reached: 5\.8 &lt;Logarithms&gt;\.<\/p>/);
  assert.equal(count(markup, /data-tone="warning"/g), 1);
  // No card waits: the instructor's own press proposes the move.
  assert.match(markup, /<button type="button" class="button" data-class-move>Propose the move to Fri, Nov 13<\/button>/);
  // As planned, with nothing planned, ahead of the plan, reached earlier, or declined: said plainly, never orange.
  for (const quiet of [day({ state: "as_planned", text: "Reached what was planned for this day.", not_reached: [], offer: false }),
    day({ state: "unplanned", planned: [], not_reached: [], offer: false }), day({ state: "none", text: "The reading found no notes section.", reached: [], not_reached: [], offer: false }),
    day({ state: "left" }), day({ state: "unread", reached: [], not_reached: [], offer: false })]) {
    assert.ok(!classDayMarkup({ meeting: "class", class_day: quiet }, escape, draw).includes('data-tone="warning"'), quiet.state);
  }
  assert.match(classDayMarkup({ meeting: "class", class_day: day({ state: "unplanned", planned: [], not_reached: [], offer: false }) }, escape), /<dd>No topics planned for this day<\/dd>/);
  assert.match(classDayMarkup({ meeting: "class", class_day: day({ state: "unread", reached: [], not_reached: [], offer: false }) }, escape), /<dt>Reached<\/dt><dd>Not read yet<\/dd>/);
  const left = classDayMarkup({ meeting: "class", class_day: day({ state: "left" }) }, escape, draw);
  assert.match(left, /Not reached: 5\.8 &lt;Logarithms&gt;\. You declined the move, so the plan stands\./);
  assert.match(left, /data-class-move/);   // theirs to propose again
  const more = classDayMarkup({ meeting: "class", class_day: day({ state: "as_planned", text: "Reached what was planned for this day.", not_reached: [], offer: false, own: true,
    ahead: [{ number: "5.9", title: "" }], earlier: [{ number: "5.8", title: "Logarithms", date: "2026-11-09", day: "Mon, Nov 9" }] }) }, escape, draw);
  assert.match(more, /Also reached, not planned for this day: 5\.9\./);
  assert.match(more, /5\.8 Logarithms was reached on Mon, Nov 9\./);
  assert.match(more, /<span class="chip" data-tone="neutral">This section<\/span>/);
  // Why no move can be offered is said in the server's own words, escaped.
  assert.match(classDayMarkup({ meeting: "class", class_day: day({ next_day: "", next: "", offer: false, blocked: "No later <class> day." }) }, escape),
    /<p class="recording-item-found" data-state="info" data-class-blocked>No later &lt;class&gt; day\.<\/p>/);
  assert.ok(!classDayMarkup({ meeting: "class", class_day: day() }, escape).includes("data-class-blocked"));
  // Only a class has one.
  assert.equal(classDayMarkup({ meeting: "staff", class_day: day() }, escape), "");
  assert.equal(classDayMarkup({ meeting: "class" }, escape), "");
});

test("the move is the Assistant's own card, with this section's topics as the card would write them", () => {
  const lines = moveLines(day(), card(), escape);
  assert.equal(lines, '<p class="recording-facts">Section 507\'s topics would read:</p><ul class="recording-card-lines">'
    + '<li><span class="recording-count">Wed, Nov 11</span> 5.6, 5.7</li><li><span class="recording-count">Fri, Nov 13</span> 5.8</li></ul>');   // in date order, and no other section's
  const emptied = card({ body: { topics: { "2026-11-11": { notes: ["5.8"], note: "", sections: { s507: { notes: [], note: "5.8 moved to <Fri>" } } } } } });
  assert.match(moveLines(day(), emptied, escape), /<span class="recording-count">Wed, Nov 11<\/span> 5\.8 moved to &lt;Fri&gt;<\/li>/);
  assert.equal(moveLines(day(), { body: {} }, escape), "");
  const waiting = classDayMarkup({ meeting: "class", class_day: day({ card: card(), offer: false }) }, escape, draw);
  assert.match(waiting, /<div class="recording-item-card"><article data-card="a1"><p class="recording-facts">Section 507's topics would read:/);
  assert.ok(!waiting.includes("data-class-move"));   // its card is waiting
});

test("a class's page has its own groups, no coordinator and no student, and a day that disagrees needs you with both said", () => {
  const data = { meeting: "class", reading: read, coordinator: { term_id: "t1", name: "", term: "MATH 140 · Fall 2026" }, students: [], class_day: day(),
    items: [item(), told(), told({ id: "i3", group: "told", sentence: "You told the class the homework is due Friday.", when: "Fri, Nov 13",
      found: { state: "agrees", text: "As Faculty has it: Homework 5.6 is due Fri, Nov 13 (the class schedule's plan).", href: "#x" } }),
    told({ id: "i4", group: "done", state: "done", sentence: "You told the class there is no class.", happening: false, when: "Wed, Nov 25", about: { what: "class" }, found: null })] };
  const markup = outcomeMarkup(data, escape, draw);
  assert.deepEqual(CLASS_GROUPS.map(([key]) => key), ["you", "covered", "told"]);
  assert.ok(markup.indexOf("data-recording-class") < markup.indexOf('data-items="you"'));   // the day first, then what needs them
  assert.ok(markup.indexOf('data-items="you"') < markup.indexOf('data-items="covered"') && markup.indexOf('data-items="covered"') < markup.indexOf('data-items="told"'));
  assert.match(markup, /<h3 class="recording-head">What the class reached <span class="recording-count">1<\/span><\/h3>/);
  assert.match(markup, /<h3 class="recording-head">Told the class <span class="recording-count">1<\/span><\/h3>/);
  assert.ok(!markup.includes("data-recording-coordinator") && !markup.includes("data-recording-students"));
  // Two oranges and no third: the day's unreached section, and the count of what needs the instructor.
  assert.equal(count(markup, /data-tone="warning"/g), 2);
  assert.match(markup, /<p class="recording-item-found" data-state="differs">You told the class Wed, Nov 18\. Faculty has Exam 3 given Thu, Nov 19 \(its exam record\)\. <a class="link-arrow" href="#course\/g1\/section\/507\/schedule">Open<\/a><\/p>/);
  assert.match(markup, /<span>Reached<\/span><span>5\.6<\/span>/);
  assert.match(markup, /<span>Told the class<\/span><span>Wed, Nov 18<\/span>/);
  assert.match(markup, /<span>Told the class<\/span><span>No class Wed, Nov 25<\/span>/);
  assert.match(markup, /Already done/);
  // What the class worked through is only ever dismissed (it was not so); a day told to it is settled like any item.
  assert.equal(controlsMarkup(item(), escape), '<button type="button" class="text-button" data-item-state="dismissed" data-recording-item="i1">Dismiss</button>');
  assert.match(controlsMarkup(told(), escape), /data-item-state="done"[^>]*>Done<\/button>.*data-item-state="dismissed"/);
  assert.ok(!controlsMarkup(told(), escape).includes("data-item-card") && !controlsMarkup(told(), escape).includes("data-item-remember"));
  assert.match(itemMarkup(item(), escape), /The class worked through section 5\.6, &lt;Exponential&gt; Functions\./);
  // Read and empty, before any reading, and a meeting as it always was.
  assert.match(outcomeMarkup({ ...data, items: [], class_day: day({ state: "none", reached: [], not_reached: [], offer: false }) }, escape, draw),
    /The reading found no day told to the class and no notes section worked through\./);
  const unread = outcomeMarkup({ meeting: "class", reading: { state: "none" }, items: [], class_day: day({ state: "unread", reached: [], not_reached: [], offer: false }) }, escape, draw);
  assert.match(unread, /data-recording-class data-state="unread"/);
  const staff = outcomeMarkup({ ...data, meeting: "staff", class_day: undefined, items: [item({ kind: "answer", group: "answers" })] }, escape, draw);
  assert.ok(!staff.includes("data-recording-class") && staff.includes("Answers to your questions") && staff.includes("data-recording-coordinator"));
});
