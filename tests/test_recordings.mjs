// The Recordings page's views (static/recordings.js, RECORDINGS step 1): what a row says, in what order the list is drawn, and
// that nothing a recording is called reaches the page unescaped. ONLY WHAT IS FACULTY'S IS KEPT (2026-11-14): one that waits
// offers Keep and Don't keep, a kept one File elsewhere and Don't keep, and of what is not kept only the number is said.
// The people are invented.
import assert from "node:assert/strict";
import test from "node:test";
import { ERASES, captureLine, clockText, headline, keptText, lengthText, listMarkup, notKeptText, recordingRowMarkup, resultsMarkup, whenText } from "../static/recordings.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const filed = { id: "r1", filing: "filed", name: "MATH 140 · Fall 2026", date: "2026-11-10", time: "10:02", start_known: true, duration_ms: 71 * 60000,
  plaud_title: "11-10 Meeting", kept: ["transcript", "notes", "audio"], fetch: { status: "kept", detail: "" }, links: [{ kind: "term" }] };
const waiting = { id: "r2", filing: "needs_filing", name: "Needs filing", date: "2026-11-10", time: "18:30", start_known: true, duration_ms: 30 * 60000,
  plaud_title: "<b>Evening</b>", filing_detail: "No calendar entry at that time.", kept: [], fetch: { status: "idle", detail: "" }, links: [] };

test("a length and a time read as a person says them", () => {
  assert.deepEqual([lengthText(45000), lengthText(30 * 60000), lengthText(71 * 60000), lengthText(120 * 60000), lengthText(0)], ["45 s", "30 min", "1 h 11 min", "2 h", "0 s"]);
  assert.deepEqual([clockText("10:02"), clockText("00:05"), clockText("12:00"), clockText("18:30"), clockText("soon")], ["10:02 AM", "12:05 AM", "12:00 PM", "6:30 PM", ""]);
  assert.equal(whenText(filed), "Tue, Nov 10 · 10:02 AM · 1 h 11 min");
  assert.equal(whenText({ ...filed, start_known: false }), "Tue, Nov 10 · 1 h 11 min");   // Plaud did not say when it began
});

test("a filed recording is named by what it is filed under, never by Plaud's title", () => {
  const row = recordingRowMarkup(filed, escape);
  assert.match(row, /<li class="recording-row" data-patch-key="recording-r1" data-filing="filed">/);
  // What Faculty kept something of opens on its own page (step 2).
  assert.match(row, /<a class="recording-open" href="#recordings\/r1">MATH 140 · Fall 2026<\/a><span class="recording-when">Tue, Nov 10 · 10:02 AM · 1 h 11 min<\/span>/);
  assert.doesNotMatch(recordingRowMarkup({ ...filed, kept: [] }, escape), /<a /);   // nothing kept yet: nothing to open
  assert.match(row, /Transcript and audio kept/);
  assert.doesNotMatch(row, /11-10 Meeting|Needs filing/);
  // Words only on a kept row: File elsewhere, and Don't keep, which erases it (and so is marked, and asks first).
  assert.match(row, /<button type="button" class="text-button" data-recording-file="r1">File elsewhere<\/button><button\s+type="button" class="text-button danger" data-recording-discard="r1">Don’t keep<\/button>/);
  assert.doesNotMatch(row, /class="button"/);
  // Step 3: what the meeting left for the instructor is counted on its row, in orange, and only when there is any.
  assert.doesNotMatch(row, /for you/);
  assert.match(recordingRowMarkup({ ...filed, for_you: 6 }, escape), /<span class="chip" data-tone="warning">6 for you<\/span><\/p>/);
});

test("a recording that waits is named by its day, says why, shows Plaud's title escaped, and offers Keep and Don't keep", () => {
  const row = recordingRowMarkup(waiting, escape);
  assert.match(row, /<p class="recording-name">Tue, Nov 10 · 6:30 PM · 30 min<\/p>/);
  assert.doesNotMatch(row, /chip/);   // its group's count is the orange; the row is not marked a second time
  assert.match(row, /No calendar entry at that time\./);
  assert.match(row, /Plaud calls it “&lt;b&gt;Evening&lt;\/b&gt;”\./);
  assert.doesNotMatch(row, /<b>Evening/);
  // ONE framed step, Keep; Don't keep beside it as words, unmarked: nothing was fetched, so nothing is erased.
  assert.match(row, /<button type="button" class="button" data-recording-file="r2">Keep…<\/button><button\s+type="button" class="text-button" data-recording-discard="r2">Don’t keep<\/button>/);
  assert.equal(row.match(/class="button"/g).length, 1);
  assert.doesNotMatch(row, /danger/);
});

test("what is kept of a recording, in a few words", () => {
  assert.equal(keptText(filed), "Transcript and audio kept");   // one whose own audio file Faculty still holds
  // A recording copied from the archive holds no audio of Faculty's own: its audio plays from the archive.
  assert.equal(keptText({ ...filed, kept: ["transcript", "notes"], fetch: { status: "kept", detail: "" } }), "Transcript kept");
  assert.equal(keptText({ ...filed, kept: ["transcript"], fetch: { status: "waiting", detail: "The archive has no audio of it yet." } }),
    "Kept so far: transcript. The archive has no audio of it yet.");
  assert.equal(keptText({ ...filed, kept: ["audio"], fetch: { status: "waiting", detail: "Plaud has no transcript for it yet." } }),
    "Kept so far: audio. Plaud has no transcript for it yet.");
  assert.equal(keptText({ ...filed, kept: [], fetch: { status: "idle", detail: "" } }), "Fetched at the next check.");
  assert.equal(keptText({ ...filed, kept: [], fetch: { status: "failed", detail: "Plaud no longer has that recording." } }), "Plaud no longer has that recording.");
  assert.equal(keptText(waiting), "");   // it waits: nothing was fetched, and there is nothing to say of what is kept
});

test("the list draws what waits for Keep or Don't keep first, then what is kept, and of what is not kept only how many", () => {
  const list = listMarkup({ recordings: [filed, waiting], not_kept_count: 3, capture: { ready: true } }, escape);
  const needs = list.indexOf("Keep or not <span"), ours = list.indexOf("Recordings <span"), gone = list.indexOf("data-recordings-not-kept");
  assert.ok(needs >= 0 && needs < ours && ours < gone, list);
  assert.match(list, /Keep or not <span class="chip" data-tone="warning">1<\/span>/);
  assert.match(list, /Faculty could not tell whether these belong to your teaching\. Nothing of them is fetched until you say\./);
  // What is not kept: a number and the way to be asked again. No row, no title, no time; and no folded list of them.
  assert.equal(notKeptText(3), "3 recordings at Plaud are not kept. Faculty holds nothing of them.");
  assert.equal(notKeptText(1), "1 recording at Plaud is not kept. Faculty holds nothing of it.");
  assert.match(list, /<p class="recording-facts recording-not-kept" data-recordings-not-kept>3 recordings at Plaud are not kept\. Faculty holds nothing of them\.\s*<button type="button" class="text-button" data-recordings-ask-again>Ask me again<\/button><\/p>/);
  assert.doesNotMatch(list, /<details|Not Faculty|Needs filing/);
  assert.equal(list.match(/class="recording-row"/g).length, 2);
  // None not kept: the line is not drawn.
  assert.doesNotMatch(listMarkup({ recordings: [filed], not_kept_count: 0, capture: { ready: true } }, escape), /not kept|Ask me again/);
  // Erased, but a drive still holds its files: said in orange with the engine's own reason, escaped, until they are gone.
  assert.doesNotMatch(list, /data-recordings-erase-waiting/);
  const held = listMarkup({ recordings: [filed], not_kept_count: 1, erase_waiting: { count: 2, detail: "A drive is <away>." }, capture: { ready: true } }, escape);
  assert.match(held, /<p class="recording-capture" data-tone="warning" data-recordings-erase-waiting>The files of 2 erased recordings\s+are not removed yet\. A drive is &lt;away&gt;\. Faculty tries again at every check\.<\/p>/);
  assert.ok(held.indexOf("data-recordings-erase-waiting") < held.indexOf("data-recordings-not-kept"));
  assert.doesNotMatch(listMarkup({ recordings: [filed], erase_waiting: { count: 0, detail: "" }, capture: { ready: true } }, escape), /not removed yet/);
  // Nothing listed, some not kept: the page still says how many, and still offers to ask.
  const none = listMarkup({ recordings: [], not_kept_count: 2, capture: { ready: true } }, escape);
  assert.match(none, /No recordings yet/);
  assert.match(none, /2 recordings at Plaud are not kept\./);
  assert.match(none, /data-recordings-ask-again/);
  const empty = listMarkup({ recordings: [], capture: { ready: false, setup: "Plaud is not set up for this Faculty instance." } }, escape);
  assert.match(empty, /No recordings yet/);
  assert.match(empty, /Plaud is not set up for this Faculty instance\./);
  assert.doesNotMatch(empty, /not kept/);
  // What erasing a kept recording does is said before it is done.
  assert.equal(ERASES, "Faculty erases its transcript and what it read from it. What you approved from it stays, and the recording stays in your Plaud archive and at Plaud.");
});

test("the header and the status line say what needs the instructor", () => {
  assert.deepEqual(headline({ attention: { needs_filing: 2, sign_in: true } }), { attention: true, text: "Sign in to Plaud again · 2 recordings to keep or not" });
  assert.deepEqual(headline({ attention: { needs_filing: 1, sign_in: false } }), { attention: true, text: "1 recording to keep or not" });
  assert.equal(headline({ attention: { needs_filing: 0, sign_in: false } }).attention, false);
  assert.deepEqual(headline({ attention: { needs_filing: 1, sign_in: false, for_you: 6 } }), { attention: true, text: "1 recording to keep or not · 6 items from your recordings for you" });
  assert.deepEqual(headline({ attention: { needs_filing: 0, sign_in: false, for_you: 1 } }), { attention: true, text: "1 item from your recordings for you" });
  assert.deepEqual(captureLine({ ready: true, sign_in: true, status: "sign_in", detail: "Sign in to Plaud again: run Plaud's own sign-in." }),
    { tone: "warning", text: "Sign in to Plaud again: run Plaud's own sign-in." });
  assert.deepEqual(captureLine({ ready: false, setup: "Plaud is not set up for this Faculty instance." }),
    { tone: "neutral", text: "Plaud is not set up for this Faculty instance." });
  assert.equal(captureLine({ ready: true, requested: true, status: "ok" }).text, "Checking Plaud now…");
  assert.equal(captureLine({ ready: true, status: "waiting", detail: "Plaud could not be reached. The next round tries again." }).tone, "warning");
  assert.match(captureLine({ ready: true, status: "ok", detail: "4 recordings at Plaud since Sun, Nov 8.", last_success_at: "2026-11-11T22:00:00.000000Z" }).text,
    /^Checked 4:00 PM · 4 recordings at Plaud since Sun, Nov 8\.$/);
});

test("a search of what was said lists each line with its recording, day, time and speaker, the found words marked", () => {
  const hit = { recording_id: "r1", name: "MATH 140 · Fall 2026", date: "2026-11-10", time: "10:02", line: 7, at: "12:34", start_ms: 754000,
    speaker: "Avery <Quill>", text: "No Calculators on the <exam>, calculators stay home.", href: "#recordings/r1?t=754000" };
  const found = resultsMarkup({ words: ["calculators"], results: [hit], more: false }, escape);
  assert.match(found, /What was said <span class="recording-count">1<\/span>/);
  assert.match(found, /<li class="recording-hit" data-patch-key="hit-r1-7">\s*<a class="recording-open" href="#recordings\/r1\?t=754000">MATH 140 · Fall 2026<\/a><span class="recording-when">Tue, Nov 10 · at 12:34<\/span>/);
  assert.match(found, /<span class="recording-line-speaker">Avery &lt;Quill&gt;<\/span>No <mark>Calculators<\/mark> on the &lt;exam&gt;, <mark>calculators<\/mark> stay home\./);
  assert.match(resultsMarkup({ words: ["x"], results: [hit], more: true }, escape), /<span class="recording-count">1\+<\/span>[^]*These are the first 1\. Add a word to narrow it\./);
  assert.match(resultsMarkup({ words: ["a", "b"], results: [] }, escape), /No kept recording has a line with every one of those words\./);
  assert.match(resultsMarkup({ words: ["a"], results: [] }, escape), /with that word\./);
  assert.match(resultsMarkup({ error: "The <server> said no." }, escape), /The &lt;server&gt; said no\./);
  assert.equal(resultsMarkup(null, escape), "");
});
