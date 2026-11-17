// A memo on its page, a training's filing and the search by meaning (RECORDINGS "Memos, training, search by meaning", step 6;
// static/recording-items.js, static/recordings.js): what the instructor asked, each request with its words, how the Assistant's
// run of it stands and the run's cards; in orange only what needs them; where Faculty was not sure whose words they are it
// says why and offers the press. The memo and its words are invented.
import assert from "node:assert/strict";
import test from "node:test";
import { MEMO_GROUPS, askLine, askMarkup, controlsMarkup, outcomeMarkup, readingLine, stillReading } from "../static/recording-items.js";
import { nearestMarkup, resultsMarkup, trainingsMarkup } from "../static/recordings.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const count = (markup, pattern) => (markup.match(pattern) || []).length;
const ask = (more) => ({ state: "ready", why: "", run_id: "p1", asked_at: "2026-11-12T02:01:00Z", answer: "I prepared the entry. It waits for your approval.", question: null, cards: [], ...more });
const item = (more) => ({ id: "i1", kind: "todo", sentence: "Add office hours on Thursday from 2 to 3 PM in <Blocker> 220.", quote: "Add office hours this Thursday from two to three.", line: 1,
  start_ms: 4000, end_ms: 8000, at: "0:04", speaker: "Chris", for: "you", date: "", time: "", date_words: "", when: "", happening: true, about: { what: "none" }, assessment: "",
  state: "open", memory: null, ruled: "", card: null, found: null, group: "asked", yours: true, enrollment_id: "", followup: "", cards: { commitments: null, note: null }, ask: ask(), ...more });
const rule = (more) => item({ id: "i2", kind: "policy", sentence: "Late take-home assignments are not accepted.", quote: "from now on I do not accept late take-home assignments.", group: "rules", ask: undefined, ...more });
const read = { state: "read", run: 1, again: false, detail: "", read_at: "2026-11-12T02:00:30Z", engine: "scripted", begins_line: 0, dropped: [], readable: true, you: "Chris", your_name: "Chris Example", changed: false };
const card = (more) => ({ id: "a1", pending: true, status: "proposed", seen: 3, summary: "Add office hours", method: "POST", path: "/api/calendar/events", endpoint: "workspace_calendar.create", page: null, events: [], body: {}, ...more });
const draw = { card: (action) => `<article data-card="${action.id}"></article>` };
const page = (more) => ({ meeting: "memo", memo: { sure: true, why: "", assistant: true }, reading: read, items: [item(), rule()], counts: { you: 2, asked: 1, rules: 1, done: 0 }, coordinator: { term_id: null, name: "", term: "" }, students: [], ...more });

test("a memo's page says what was asked, the Assistant's answer and its card, and marks in orange only what needs the instructor", () => {
  const markup = outcomeMarkup(page({ items: [item({ ask: ask({ cards: [card()] }) }), item({ id: "i3", yours: false, ask: ask({ state: "queued", answer: "" }) }), rule()] }), escape, draw);
  assert.deepEqual(MEMO_GROUPS.map(([key]) => key), ["asked", "rules"]);
  assert.match(markup, /Read by Faculty[^<]*One voice, taken as yours\./);
  assert.doesNotMatch(markup, /It took|Name the coordinator|Coordinator:/);
  assert.match(markup, /<section class="recording-group" data-items="asked"><h3 class="recording-head">You asked <span class="recording-count">2<\/span> <span class="chip" data-tone="warning">1 for you<\/span>/);
  assert.match(markup, /data-items="rules"><h3 class="recording-head">Rules you said <span class="recording-count">1<\/span> <span class="chip" data-tone="warning">1 for you<\/span>/);
  assert.match(markup, /<span>Request<\/span>/);
  assert.match(markup, /Add office hours on Thursday from 2 to 3 PM in &lt;Blocker&gt; 220\./);   // text, never markup
  assert.match(markup, /The Assistant answered\. 1 change waits for your approval\./);
  assert.match(markup, /<div class="recording-ask-answer">I prepared the entry\. It waits for your approval\.<\/div>/);
  assert.equal(count(markup, /<article data-card="a1">/g), 1);
  assert.match(markup, /Waiting for the Assistant\./);
  // The one orange line of the reading is absent when Faculty is sure; the queued request's line is not orange.
  assert.equal(count(markup, /class="recording-capture" data-tone="warning"/g), 0);
  // A rule is kept only by Remember; a request the Assistant is working on offers no Ask.
  assert.match(markup, /data-item-remember="i2"/);
  assert.equal(count(markup, /data-item-ask-again="i1"/g), 1);
  assert.doesNotMatch(markup, /data-item-ask(-again)?="i3"/);
});

test("not sure whose words they are, the page says why in orange and the request waits on the instructor's own press", () => {
  const why = "Plaud heard 2 voices in this memo, so Faculty cannot tell that every word is yours. Nothing was put to the Assistant: press Ask the Assistant on a request that is yours.";
  const markup = outcomeMarkup(page({ memo: { sure: false, why, assistant: true }, items: [item({ ask: ask({ state: "unsent", why, answer: "", run_id: "" }) })] }), escape, draw);
  assert.equal(readingLine(read, { sure: false, why }).tone, "warning");
  assert.match(markup, /class="recording-capture" data-tone="warning">Read by Faculty[^<]*Plaud heard 2 voices in this memo/);
  assert.match(markup, /<button type="button" class="button" data-item-ask="i1">Ask the Assistant<\/button>/);
  assert.doesNotMatch(markup, /recording-ask-answer/);
});

test("each state of a request reads plainly, and a settled request shows nothing of its run", () => {
  assert.deepEqual(["queued", "running", "ready", "needs_input", "failed", "interrupted", "stale"].map((state) => askLine(ask({ state })).tone),
    ["neutral", "neutral", "neutral", "warning", "warning", "warning", "neutral"]);
  const asked = askMarkup(item({ ask: ask({ state: "needs_input", question: { id: "q", questions: [{ id: "0", title: "Which <Thursday>?", options: [] }] } }) }), escape, draw);
  assert.match(asked, /The Assistant asked you something\. Answer with Ask again\./);
  assert.match(asked, /<li>Which &lt;Thursday&gt;\?<\/li>/);
  const settled = askMarkup(item({ ask: ask({ cards: [card({ pending: false, status: "succeeded" }), card({ id: "a2", pending: false, status: "declined" })] }) }), escape, draw);
  assert.match(settled, /Its changes: 1 approved and done · 1 declined\./);
  assert.equal(askMarkup(item({ group: "done", state: "done" }), escape, draw), "");
  assert.match(controlsMarkup(item({ group: "done", state: "done" }), escape), /Open again/);
  // A long answer is folded, never a wall.
  assert.match(askMarkup(item({ ask: ask({ answer: "x".repeat(900) }) }), escape, draw), /<details[^>]*>[\s\S]*The Assistant&#39;s answer|The Assistant's answer/);
});

test("the page asks again by itself only while a request is with the Assistant", () => {
  assert.equal(stillReading(page({ items: [item({ ask: ask({ state: "queued" }) })] })), true);
  assert.equal(stillReading(page({ items: [item({ ask: ask({ state: "running" }) })] })), true);
  assert.equal(stillReading(page()), false);
  assert.equal(stillReading(page({ items: [item({ group: "done", ask: ask({ state: "queued" }) })] })), false);
  // Queued with the Assistant off: the line says why in orange, and the page does not go on asking.
  const off = "The Assistant is off (Compute settings name no model for it), so this waits until it is on.";
  const waits = page({ memo: { sure: true, why: "", assistant: false }, items: [item({ ask: ask({ state: "queued", why: off }) })] });
  assert.equal(stillReading(waits), false);
  assert.deepEqual(askLine(waits.items[0].ask), { text: off, tone: "warning" });
});

test("a training's page says it is kept for reference, and a memo that asked for nothing says so", () => {
  const training = outcomeMarkup({ meeting: "training", reading: { state: "not_read", detail: "A training is kept for reference under its course and is not read for items.", readable: false }, items: [], students: [] }, escape, draw);
  assert.match(training, /A training is kept for reference under its course and is not read for items\./);
  assert.doesNotMatch(training, /Read again/);
  assert.match(outcomeMarkup(page({ items: [] }), escape, draw), /This memo asked for nothing/);
});

const hit = (more) => ({ recording_id: "r1", name: "MATH 140 · Fall 2026", date: "2026-11-10", time: "10:02", line: 1, start_ms: 3200, end_ms: 6100, at: "0:03",
  lines: [{ speaker: "Avery <Quill>", text: "And the seating charts go out Thursday.", start_ms: 3200, at: "0:03" }], href: "#recordings/r1?t=3200", ...more });

test("the search lists the passages nearest by meaning under the lines that hold the words, and says nearest is not a finding", () => {
  const found = { query: "where students sit", words: ["where", "students", "sit"], results: [], more: false, nearest: [hit()], meaning: { state: "ok", detail: "", indexed: 3, waiting: 1 } };
  const markup = resultsMarkup(found, escape);
  assert.match(markup, /No kept recording has a line with every one of those words\./);
  assert.match(markup, /<h3 class="recording-head">Nearest by meaning <span class="recording-count">1<\/span>/);
  assert.match(markup, /Nearest is not a finding: read them\./);
  assert.match(markup, /href="#recordings\/r1\?t=3200">MATH 140 · Fall 2026<\/a>/);
  assert.match(markup, /Avery &lt;Quill&gt;<\/span>And the seating charts go out Thursday\./);
  assert.match(markup, /1 recording is not read for this yet\./);
  assert.doesNotMatch(markup, /0\.\d+/);   // a score is never shown
  // Not set up: nothing of it is drawn. Not answering: one line, and the words were still searched.
  assert.equal(nearestMarkup({ ...found, meaning: { state: "off", detail: "x" } }, escape), "");
  assert.match(nearestMarkup({ ...found, nearest: [], meaning: { state: "unavailable", detail: "Search by meaning is not answering now." } }, escape), /not answering now/);
  assert.equal(nearestMarkup({ results: [], words: ["a"] }, escape), "");   // an answer without it is drawn as before
  const many = nearestMarkup({ ...found, nearest: [hit({ lines: Array.from({ length: 7 }, (_, n) => ({ speaker: "A", text: `line ${n}`, start_ms: n, at: "0:00" })) })] }, escape);
  assert.equal(count(many, /class="recording-hit-text"/g), 4);
  assert.match(many, /and 3 more lines/);
});

test("a course's trainings are listed alone, with the way back to every recording", () => {
  const row = (more) => ({ id: "r9", filing: "filed", name: "Training · MATH 140", date: "2026-11-11", time: "13:00", start_known: true, duration_ms: 3000000, kept: ["transcript", "audio"],
    links: [{ kind: "training", label: "Training · MATH 140", definition_id: "d1" }], for_you: 0, fetch: { status: "kept", detail: "" }, ...more });
  const data = { recordings: [row(), row({ id: "r2", name: "MATH 140 · Fall 2026", links: [{ kind: "term", label: "MATH 140 · Fall 2026" }] })], choices: { courses: [{ id: "d1", label: "MATH 140" }] } };
  const markup = trainingsMarkup(data, "d1", escape);
  assert.match(markup, /<h3 class="recording-head">Training · MATH 140 <span class="recording-count">1<\/span>/);
  assert.equal(count(markup, /class="recording-row"/g), 1);
  assert.match(markup, /href="#recordings">All recordings<\/a>/);
  assert.match(trainingsMarkup(data, "other", escape), /No training recordings yet/);
});
