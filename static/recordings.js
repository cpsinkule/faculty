// @ts-check
/**
 * The Recordings page, `#recordings` (docs/decisions/recordings.md, RECORDINGS; step 1, Intake). What the instructor's Plaud
 * recorder holds of their TEACHING, as Faculty lists it every ten minutes: the recordings that wait for Keep or Don't keep
 * first, then the kept ones newest first, each named by what it is filed under and its day (not Plaud's title). A recording
 * is filed by where its start falls on the calendar; one the calendar shows to be nothing of Faculty's is not kept and not
 * shown (only how many there are); one that fell nowhere waits here, unfetched, until the instructor says Keep (and what it
 * was) or Don't keep. Opened from the sidebar.
 *
 * ONLY WHAT IS FACULTY'S IS KEPT (RECORDINGS; requirement, 2026-11-14): Don't keep on a kept recording erases it and asks
 * first; Ask me again has what was not kept listed again, to be asked about.
 *
 * The list (step 1): the server decides everything (recordings/service.py); this module draws it and names its controls,
 * Check now, Keep, File elsewhere, Don't keep and Ask me again. Step 2 adds a search of what was said (the kept transcripts, by their words) and one recording's own page,
 * `#recordings/<id>` (recording-page.js: its transcript, its audio from any line, its speakers), which a kept recording's name
 * opens. No reading, no cards: that is step 3. No address carries a student: the student a recording is filed under goes in the
 * request's body.
 *
 * Step 6: a recording may be filed as a course's TRAINING (kept for reference in every term; `#recordings?course=<course>`
 * lists that course's, as its course page links them); the search also answers by MEANING, with the passages nearest what
 * was typed as a description (found on this machine; nearest is never a finding); a memo's row counts what it needs.
 */
import { captureDialogIntent, dialogSession, emptyMarkup, errorMessage, loadingMarkup, plural, runFormAction, setBusy, wallDate, whenVisible } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { studentPicker } from "./student-picker.js";
import { CAMPUS_ZONE } from "./calendar-feed.js";
import { createRecordingPage, findWords, markedText } from "./recording-page.js";

const BASE = "/api/recordings";
const CLOCK = new Intl.DateTimeFormat("en-US", { timeZone: CAMPUS_ZONE, hour: "numeric", minute: "2-digit" });
const KINDS = /** @type {[string, string][]} */ ([["student", "A student"], ["term", "A term"], ["course", "A course, every term"],
  ["training", "A course's training, for reference"], ["class_day", "A section's class day"], ["memo", "A memo of my own"]]);
// ONLY WHAT IS FACULTY'S IS KEPT (RECORDINGS; requirement, 2026-11-14): what erasing a kept recording does, said before it is done.
export const ERASES = "Faculty erases its transcript and what it read from it. What you approved from it stays, and the recording stays in your Plaud archive and at Plaud.";

/** How many recordings at Plaud are not kept: their number is all Faculty can say of them. @param {number} count */
export function notKeptText(count) {
  return `${plural(count, "recording")} at Plaud ${count === 1 ? "is" : "are"} not kept. Faculty holds nothing of ${count === 1 ? "it" : "them"}.`;
}
const KEPT = /** @type {Record<string, string>} */ ({ transcript: "transcript", outline: "outline", marks: "marks", notes: "Plaud's notes", audio: "audio" });

/** "1 h 11 min", "30 min", "45 s" @param {number} ms */
export function lengthText(ms) {
  const seconds = Math.max(0, Math.round(Number(ms || 0) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}

/** "10:02 AM" from the campus wall-clock "10:02" @param {string} clock */
export function clockText(clock) {
  const found = /^(\d{2}):(\d{2})$/.exec(String(clock || ""));
  if (!found) return "";
  const hour = Number(found[1]);
  return `${hour % 12 || 12}:${found[2]} ${hour < 12 ? "AM" : "PM"}`;
}

/** "Tue, Nov 10 · 10:02 AM · 1 h 11 min" @param {any} row */
export function whenText(row) {
  return [wallDate(row.date), row.start_known === false ? "" : clockText(row.time), row.duration_ms ? lengthText(row.duration_ms) : ""].filter(Boolean).join(" · ");
}

/** What Faculty holds of a recording beyond its list row, in a few words: of one filed as Faculty's, what is kept or what it
 * waits on. One that waits for Keep or Don't keep has nothing fetched, and says nothing here.
 * @param {any} row */
export function keptText(row) {
  const kept = row.kept || [];
  const named = kept.map((/** @type {string} */ role) => KEPT[role] || role).join(", ");
  if (row.filing !== "filed") return "";
  if (kept.includes("transcript") && kept.includes("audio")) return "Transcript and audio kept";   // one kept before audio played from the archive
  if (kept.includes("transcript") && row.fetch?.status === "kept") return "Transcript kept";
  const have = kept.length ? `Kept so far: ${named}. ` : "";
  if (row.fetch?.status === "failed") return `${have}${row.fetch.detail || "It could not be fetched."}`;
  return `${have}${row.fetch?.detail || "Fetched at the next check."}`;
}

/** Where capture stands, as one line and its tone. @param {any} capture @returns {{tone: string, text: string}} */
export function captureLine(capture) {
  if (!capture) return { tone: "neutral", text: "" };
  const checked = capture.last_success_at ? `Checked ${CLOCK.format(new Date(capture.last_success_at))}` : "";
  if (capture.sign_in) return { tone: "warning", text: capture.detail || "Sign in to Plaud again." };
  if (!capture.ready) return { tone: "neutral", text: capture.setup || "Plaud is not set up for this Faculty instance." };
  if (capture.requested) return { tone: "neutral", text: "Checking Plaud now…" };
  if (capture.status === "waiting" || capture.status === "failed") return { tone: "warning", text: `Recordings are waiting. ${capture.detail}${checked ? ` ${checked}.` : ""}` };
  if (capture.status === "idle") return { tone: "neutral", text: "Plaud has not been checked yet." };
  return { tone: "neutral", text: [checked, capture.detail].filter(Boolean).join(" · ") };
}

/** The header's line: what needs the instructor first. @param {any} data */
export function headline(data) {
  const waiting = data?.attention?.needs_filing || 0, mine = data?.attention?.for_you || 0;
  const parts = [data?.attention?.sign_in ? "Sign in to Plaud again" : "", waiting ? `${plural(waiting, "recording")} to keep or not` : "",
    mine ? `${plural(mine, "item")} from your recordings for you` : ""].filter(Boolean);
  return { attention: parts.length > 0, text: parts.length ? parts.join(" · ") : "Recordings of your teaching, filed by the calendar." };
}

/** What a recording's row says when it began inside an appointment recorded as one that did not happen (step 4), or "". @param {any} row */
export function appointmentOff(row) {
  const state = (row.links || []).map((/** @type {any} */ link) => link.appointment_off).find(Boolean);
  return state === "cancelled" ? "Appointment cancelled" : state === "no_show" ? "Appointment a no-show" : "";
}

/** One recording as a row: named by what it is filed under, or by its day where it waits for Keep or Don't keep (its group
 * says what it is). One that waits has ONE framed step, Keep, and Don't keep beside it as words; a kept one has only words:
 * File elsewhere, and Don't keep, which erases it and so asks first.
 * @param {any} row @param {(value: unknown) => string} e */
export function recordingRowMarkup(row, e) {
  const waiting = row.filing === "needs_filing";
  const facts = waiting ? (row.filing_detail || "It fell in no calendar entry.") : keptText(row);
  const plaud = row.plaud_title && row.filing !== "filed" ? `<p class="recording-facts">Plaud calls it “${e(row.plaud_title)}”.</p>` : "";
  // A recording Faculty kept something of opens on its own page; one it holds only the list row of has nothing to open.
  const opens = (row.kept || []).some((/** @type {string} */ role) => role === "transcript" || role === "audio");
  const named = row.filing === "filed" ? row.name : whenText(row);
  const name = opens ? `<a class="recording-open" href="#recordings/${e(row.id)}">${e(named)}</a>` : e(named);
  return `<li class="recording-row" data-patch-key="recording-${e(row.id)}" data-filing="${e(row.filing)}">
    <p class="recording-name">${name}${row.filing === "filed" ? `<span class="recording-when">${e(whenText(row))}</span>` : ""}${row.for_you
      ? `<span class="chip" data-tone="warning">${e(row.for_you)} for you</span>` : ""}${appointmentOff(row)
      ? `<span class="chip" data-tone="warning">${e(appointmentOff(row))}</span>` : ""}</p>
    ${facts ? `<p class="recording-facts">${e(facts)}</p>` : ""}${plaud}
    <div class="recording-controls"><button type="button" class="${waiting ? "button" : "text-button"}" data-recording-file="${e(row.id)}">${waiting ? "Keep…" : "File elsewhere"}</button><button
      type="button" class="text-button${waiting ? "" : " danger"}" data-recording-discard="${e(row.id)}">Don’t keep</button></div></li>`;
}

/** What a search of the kept transcripts found: each line with its recording and day, how far in, who spoke and the words, the
 * found words marked; a result opens its recording at that moment. @param {any} found @param {(value: unknown) => string} e */
export function resultsMarkup(found, e) {
  if (!found) return "";
  if (found.error) return `<p class="recording-facts">${e(found.error)}</p>`;
  const results = found.results || [], words = found.words || [];
  const near = nearestMarkup(found, e);
  if (!results.length) return `<p class="recording-facts">No kept recording has a line with ${words.length > 1 ? "every one of those words" : "that word"}.</p>${near}`;
  return `${wordsMarkup(found, e)}${near}`;
}

/** The passages nearest what was typed, read as a description (step 6): each with its recording and day, where it begins and
 * its first lines; it opens the recording at that moment. Nearest is never a finding, and the heading says so. With the
 * embedding worker silent the line says so once; where it is not set up nothing is drawn.
 * @param {any} found @param {(value: unknown) => string} e */
export function nearestMarkup(found, e) {
  const state = found?.meaning, near = found?.nearest || [];
  if (!state || state.state === "off") return "";
  if (state.state === "unavailable") return `<p class="recording-facts" data-recordings-meaning="unavailable">${e(state.detail)}</p>`;
  const waiting = state.waiting ? `<p class="recording-facts">${plural(state.waiting, "recording")} ${state.waiting === 1 ? "is" : "are"} not read for this yet.</p>` : "";
  if (!near.length) return waiting;
  return `<section class="recording-group" data-recordings-nearest><h3 class="recording-head">Nearest by meaning <span class="recording-count">${near.length}</span></h3>
    <p class="recording-facts">The passages nearest what you typed, nearest first. Nearest is not a finding: read them.</p>
    <ul class="recording-list">${near.map((/** @type {any} */ hit) => `<li class="recording-hit" data-patch-key="near-${e(hit.recording_id)}-${e(hit.line)}">
      <a class="recording-open" href="${e(hit.href)}">${e(hit.name)}</a><span class="recording-when">${e([wallDate(hit.date), `at ${hit.at}`].join(" · "))}</span>
      ${(hit.lines || []).slice(0, 4).map((/** @type {any} */ line) => `<p class="recording-hit-text"><span class="recording-line-speaker">${e(line.speaker)}</span>${e(line.text)}</p>`).join("")}
      ${(hit.lines || []).length > 4 ? `<p class="recording-facts">and ${plural(hit.lines.length - 4, "more line")}</p>` : ""}</li>`).join("")}</ul>${waiting}</section>`;
}

/** The lines that hold every word typed. @param {any} found @param {(value: unknown) => string} e */
function wordsMarkup(found, e) {
  const results = found.results || [], words = found.words || [];
  return `<section class="recording-group"><h3 class="recording-head">What was said <span class="recording-count">${results.length}${found.more ? "+" : ""}</span></h3>
    <ul class="recording-list">${results.map((/** @type {any} */ hit) => `<li class="recording-hit" data-patch-key="hit-${e(hit.recording_id)}-${e(hit.line)}">
      <a class="recording-open" href="${e(hit.href)}">${e(hit.name)}</a><span class="recording-when">${e([wallDate(hit.date), `at ${hit.at}`].join(" · "))}</span>
      <p class="recording-hit-text"><span class="recording-line-speaker">${e(hit.speaker)}</span>${markedText(hit.text, words, e)}</p></li>`).join("")}</ul>
    ${found.more ? `<p class="recording-facts">These are the first ${results.length}. Add a word to narrow it.</p>` : ""}</section>`;
}

/** One course's training recordings (step 6; `#recordings?course=<course>`, as its course page links them): the list, with
 * the way back to every recording. @param {any} data @param {string} course @param {(value: unknown) => string} e */
export function trainingsMarkup(data, course, e) {
  const rows = (data.recordings || []).filter((/** @type {any} */ row) => (row.links || []).some((/** @type {any} */ link) => link.kind === "training" && link.definition_id === course));
  const code = (data.choices?.courses || []).find((/** @type {any} */ item) => item.id === course)?.label || "this course";
  const back = `<p class="recording-facts">Kept for reference in every term of ${e(code)}. Faculty does not read a training for items. <a class="link-arrow" href="#recordings">All recordings</a></p>`;
  if (!rows.length) return `${back}${emptyMarkup({ title: "No training recordings yet", body: "File a recording as this course's training and it is kept here for every term." })}`;
  return `<section class="recording-group" data-recordings-trainings><h3 class="recording-head">Training · ${e(code)} <span class="recording-count">${rows.length}</span></h3>
    ${back}<ul class="recording-list">${rows.map((/** @type {any} */ row) => recordingRowMarkup(row, e)).join("")}</ul></section>`;
}

/** The whole list: what waits for Keep or Don't keep, then what is kept; and, where some are not kept, how many (their
 * number is all Faculty holds of them) with the way to be asked about them again.
 * @param {any} data @param {(value: unknown) => string} e */
export function listMarkup(data, e) {
  const rows = data.recordings || [];
  const gone = data.not_kept_count || 0;
  // Erased, but a drive still holds some of its files (it was away, or the nightly backup was running): said plainly, with
  // the storage engine's own reason, until they are gone. "Erased" is not yet true of them.
  const held = data.erase_waiting?.count || 0;
  const stillHeld = held ? `<p class="recording-capture" data-tone="warning" data-recordings-erase-waiting>The files of ${e(plural(held, "erased recording"))}
    are not removed yet. ${e(data.erase_waiting.detail || "")} Faculty tries again at every check.</p>` : "";
  const notKept = `${stillHeld}${gone ? `<p class="recording-facts recording-not-kept" data-recordings-not-kept>${e(notKeptText(gone))}
    <button type="button" class="text-button" data-recordings-ask-again>Ask me again</button></p>` : ""}`;
  if (!rows.length) {
    return `${emptyMarkup({ title: "No recordings yet", body: data.capture?.ready
      ? "A recording of your teaching appears here within twenty minutes of reaching Plaud, filed by where its start falls on your calendar. One Faculty cannot place waits here for you to keep or not."
      : (data.capture?.setup || "Plaud is not set up for this Faculty instance.") })}${notKept}`;
  }
  const waiting = rows.filter((/** @type {any} */ row) => row.filing === "needs_filing");
  const ours = rows.filter((/** @type {any} */ row) => row.filing === "filed");
  const group = (/** @type {string} */ title, /** @type {any[]} */ found, tone = "", lead = "") => found.length ? `<section class="recording-group">
    <h3 class="recording-head">${e(title)} <span class="${tone ? "chip" : "recording-count"}"${tone ? ` data-tone="${tone}"` : ""}>${found.length}</span></h3>
    ${lead ? `<p class="recording-facts">${e(lead)}</p>` : ""}<ul class="recording-list">${found.map((row) => recordingRowMarkup(row, e)).join("")}</ul></section>` : "";
  return `${group("Keep or not", waiting, "warning", "Faculty could not tell whether these belong to your teaching. Nothing of them is fetched until you say.")}${group("Recordings", ours)}${notKept}`;
}

/**
 * @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void,
 *   dialogHeader: (title: string, subtitle?: string) => string, header?: any, changed?: () => unknown}} deps
 *   `changed`: what waits for the instructor changed here (Keep, Don't keep, Ask me again), so Home's card and the rail are read again.
 */
export function createRecordings({ api, escape, toast, showDialog, dialogHeader, header = null, changed = () => {} }) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const root = () => /** @type {HTMLElement | null} */ (document.getElementById("recordings-section"));
  let data = /** @type {any} */ (null), generation = 0, signature = "", timer = 0, bound = false;
  let query = "", found = /** @type {any} */ (null), searchRun = 0, searchTimer = 0, drawnFor = "";
  /** @type {Promise<void> | null} */
  let loading = null;
  const page = createRecordingPage({ api, escape, toast, showDialog, dialogHeader, header, when: whenText });

  /** The course whose trainings the address asks for (`#recordings?course=<course>`), or "". */
  function courseFilter() {
    return /^#recordings\?/.test(location.hash) ? new URLSearchParams(location.hash.split("?")[1] || "").get("course") || "" : "";
  }

  /** The course Home's Training recordings line (step 6): how many are filed under the course, and the latest one's day.
   * @param {any} group @param {HTMLElement | null} node */
  async function summary(group, node) {
    if (!group?.definition_id || !node) return;
    try {
      const found = await api(`${BASE}/trainings?definition_id=${encodeURIComponent(group.definition_id)}`);
      if (!node.isConnected) return;
      node.textContent = found.count ? `${plural(found.count, "recording")} · latest ${wallDate(found.recordings[0].date)}` : "None yet";
    } catch {
      if (node.isConnected) node.textContent = "";
    }
  }

  /** The recording the address names (`#recordings/<id>`), or "" for the list. */
  function recordingId() {
    const match = /^#recordings\/([^?]+)/.exec(location.hash);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function setHeader() {
    const line = headline(data);
    header?.set({ route: location.hash, title: "Recordings", subtitle: line.text, attention: line.attention, action: null,
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Recordings" }] });
  }

  // The shell renders the page, again after its state read, and every 30 s: an unforced render joins the read under way, and a
  // read that finds the same list changes nothing on screen (FRONTEND.md "Quiet refresh contract").
  async function render(force = false) {
    const host = root();
    if (!host) return;
    const one = recordingId();
    if (one) { window.clearTimeout(timer); return page.render(one, force); }
    page.leave();
    setHeader();
    if (!host.querySelector("[data-recordings-frame]")) {
      host.innerHTML = `<div class="recordings-page" data-recordings-frame><div class="recording-status" data-recordings-status></div>
        <label class="field recording-search">Search what was said<input type="search" data-page-search data-recordings-search autocomplete="off" placeholder="Words that were said, or what it was about" value="${e(query)}"></label>
        <div class="recording-groups" data-recordings-found aria-live="polite"></div>
        <div class="recording-groups" data-recordings-list>${loadingMarkup("Loading recordings…", 4)}</div></div>`;
      if (!bound) { bind(host); bound = true; }
      signature = "";
      if (data) draw();
      drawFound();
    }
    if (!force && loading) return loading;
    const run = ++generation;
    const promise = (async () => {
      try {
        // One course's trainings are asked for by the course, so every one of them is answered however many recordings there are.
        const course = courseFilter();
        const next = await api(course ? `${BASE}?course=${encodeURIComponent(course)}` : BASE);
        if (run !== generation) return;
        const text = JSON.stringify(next);
        data = next;
        follow();
        if ((text === signature && drawnFor === location.hash) || root()?.hidden) return;
        signature = text;
        draw();
      } catch (cause) {
        if (run !== generation) return;
        signature = "";   // a failed read: the next good one draws again, changed or not
        const list = host.querySelector("[data-recordings-list]");
        if (list && !data) list.innerHTML = `<div class="callout" data-tone="danger" role="alert"><p>${e(errorMessage(cause))}</p><button type="button" class="button" data-recordings-retry>Try again</button></div>`;
      }
    })();
    loading = promise;
    try { await promise; } finally { if (loading === promise) loading = null; }
  }

  /** While a check that was asked for has not run yet, look again every few seconds (and only while the page is shown). */
  function follow() {
    window.clearTimeout(timer);
    if (!data?.capture?.requested) return;
    timer = window.setTimeout(() => whenVisible(() => { if (!root()?.hidden) void render(true); }), 4000);
  }

  function draw() {
    const host = root();
    if (!host || !data) return;
    setHeader();
    const line = captureLine(data.capture);
    const status = /** @type {HTMLElement} */ (host.querySelector("[data-recordings-status]"));
    patchChildren(status, `<p class="recording-capture"${line.tone === "warning" ? ' data-tone="warning"' : ""}>${e(line.text)}</p>
      ${data.capture?.ready ? `<button type="button" class="button" data-recordings-check${data.capture.requested ? " disabled" : ""}>Check now</button>` : ""}`);
    const course = courseFilter();
    patchChildren(/** @type {HTMLElement} */ (host.querySelector("[data-recordings-list]")), course ? trainingsMarkup(data, course, e) : listMarkup(data, e));
    drawnFor = location.hash;
  }

  /** Search the kept transcripts as the instructor types, a moment after they stop; an answer to an older typing is dropped. */
  async function runSearch() {
    const run = ++searchRun;
    try {
      const answer = await api(`${BASE}/search`, "POST", { query, limit: 30, meaning: true });   // also by meaning, where the worker answers
      if (run !== searchRun) return;
      found = answer;
    } catch (cause) {
      if (run !== searchRun) return;
      found = { error: errorMessage(cause) };
    }
    drawFound();
  }

  function drawFound() {
    const target = /** @type {HTMLElement | null} */ (root()?.querySelector("[data-recordings-found]") || null);
    if (target) patchChildren(target, resultsMarkup(found, e));
  }

  /** @param {HTMLElement} host */
  function bind(host) {
    host.addEventListener("input", (event) => {
      const input = /** @type {HTMLInputElement} */ (event.target);
      if (!input.matches?.("[data-recordings-search]")) return;
      query = input.value;
      window.clearTimeout(searchTimer);
      if (!findWords(query).length) { searchRun++; found = null; drawFound(); return; }
      searchTimer = window.setTimeout(() => void runSearch(), 250);
    });
    host.addEventListener("click", (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      if (target.closest("[data-recordings-retry]")) { void render(true); return; }
      const check = /** @type {HTMLButtonElement | null} */ (target.closest("[data-recordings-check]"));
      if (check) { void checkNow(check); return; }
      const file = /** @type {HTMLElement | null} */ (target.closest("[data-recording-file]"));
      const row = file && (data?.recordings || []).find((/** @type {any} */ item) => item.id === file.dataset.recordingFile);
      if (row) { void openFile(row); return; }
      const drop = /** @type {HTMLButtonElement | null} */ (target.closest("[data-recording-discard]"));
      const gone = drop && (data?.recordings || []).find((/** @type {any} */ item) => item.id === drop.dataset.recordingDiscard);
      if (drop && gone) { void discard(gone, drop); return; }
      const again = /** @type {HTMLButtonElement | null} */ (target.closest("[data-recordings-ask-again]"));
      if (again) void askAgain(again);
    });
  }

  /** Don't keep. One that waits has nothing fetched, so it goes at the press. One Faculty has kept is erased: it asks first.
   * @param {any} row @param {HTMLButtonElement} button */
  async function discard(row, button) {
    if (row.filing === "filed" || (row.kept || []).length) { openDiscard(row); return; }
    setBusy(button, true);
    try {
      await api(`${BASE}/${encodeURIComponent(row.id)}/discard`, "POST", { request_id: crypto.randomUUID() });
      toast("Not kept. Faculty holds nothing of it.");
      void changed();
      await render(true);
    } catch (cause) {
      toast(errorMessage(cause), true);
    } finally {
      setBusy(button, false);
    }
  }

  /** Before a kept recording is erased: what goes and what stays, and one press to do it. @param {any} row */
  function openDiscard(row) {
    showDialog(`<form class="recording-file-form" id="recording-discard-form" novalidate>${dialogHeader("Don’t keep this recording?", e(`${row.name} · ${whenText(row)}`))}
      <div class="dialog-body"><p>${e(ERASES)}</p><p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Erase it</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("recording-discard-form"));
    const session = dialogSession(form, dialog());
    let requestId = "";
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        requestId ||= crypto.randomUUID();
        await api(`${BASE}/${encodeURIComponent(row.id)}/discard`, "POST", { request_id: requestId });
        toast("Erased from Faculty. It stays in your Plaud archive and at Plaud.");
        if (session()) dialog().close();
        void changed();
        void render(true);
      });
    };
  }

  /** Ask me again: what was not kept is listed from Plaud again and waits here. @param {HTMLButtonElement} button */
  async function askAgain(button) {
    setBusy(button, true);
    try {
      const answer = await api(`${BASE}/ask-again`, "POST", { request_id: crypto.randomUUID() });
      toast(answer.asked ? "Checking Plaud. What it still holds will wait here for you." : "There is nothing to ask about.");
      void changed();
      await render(true);
    } catch (cause) {
      toast(errorMessage(cause), true);
    } finally {
      setBusy(button, false);
    }
  }

  /** @param {HTMLButtonElement} button */
  async function checkNow(button) {
    setBusy(button, true);
    try {
      await api(`${BASE}/check`, "POST", {});
      await render(true);
    } catch (cause) {
      toast(errorMessage(cause), true);
    } finally {
      setBusy(button, false);
    }
  }

  /** The one filing dialog: what this recording is filed under. @param {any} row */
  async function openFile(row) {
    const intended = captureDialogIntent(dialog());
    let groups = [];
    try { groups = (await api("/api/student-notes/students")).groups || []; } catch (cause) { toast(errorMessage(cause), true); return; }
    if (!intended()) return;
    const choices = data?.choices || { terms: [], courses: [], sections: [] };
    const picker = studentPicker(escape, {
      id: "recording-file-student", name: "enrollment_id", label: "Student", value: "",
      groups: groups.map((/** @type {any} */ group) => ({ label: `${group.code} · ${group.term_label}`,
        options: group.students.map((/** @type {any} */ student) => ({ value: student.enrollment_id,
          text: `${student.name} · section ${student.sections.join(", ")}`, note: student.dropped ? "dropped" : "" })) })),
      search: async (digits) => {
        const found = await api("/api/student-notes/students/search", "POST", { digits });
        return (found.students || []).map((/** @type {any} */ s) => ({ value: s.enrollment_id, text: `${s.name} · section ${s.section_number}`, note: `${Math.round(s.similarity * 100)}% match` }));
      },
    });
    const options = (/** @type {any[]} */ items) => items.map((item) => `<option value="${e(item.id)}">${e(item.label)}</option>`).join("");
    const current = row.links?.[0]?.kind || "student";
    const waiting = row.filing === "needs_filing";   // Keep: it is fetched once it is filed. A kept one is only filed elsewhere.
    showDialog(`<form class="recording-file-form" id="recording-file-form" novalidate>${dialogHeader(waiting ? "Keep this recording" : "File this recording", e(whenText(row)))}
      <div class="dialog-body">
        ${row.plaud_title ? `<p class="field-hint">Plaud calls it “${e(row.plaud_title)}”.</p>` : ""}
        <label class="field">${waiting ? "Keep it under" : "File under"}<select name="kind">${KINDS.map(([value, label]) => `<option value="${value}"${value === current ? " selected" : ""}>${e(label)}</option>`).join("")}</select></label>
        <div data-file-part="student"><label class="field">Student${picker.markup}</label></div>
        <div data-file-part="term" hidden><label class="field">Term<select name="term_id">${options(choices.terms)}</select></label></div>
        <div data-file-part="course training" hidden><label class="field">Course<select name="definition_id">${options(choices.courses)}</select></label></div>
        <p class="field-hint" data-file-part="training" hidden>Kept under the course for every term: its transcript is kept and its audio plays from the Plaud archive. Faculty does not read a training for items.</p>
        <p class="field-hint" data-file-part="memo" hidden>Faculty reads a memo for what you asked and puts each request to the Assistant. Every change it wants waits for your approval.</p>
        <div data-file-part="class_day" hidden><label class="field">Section<select name="section_id">${options(choices.sections)}</select></label>
          <label class="field">Class day<input type="date" name="date" value="${e(row.date)}" required></label></div>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${waiting ? "Keep" : "File"}</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("recording-file-form"));
    const fields = /** @type {any} */ (form.elements);
    const session = dialogSession(form, dialog());
    picker.bind(form);
    const show = () => form.querySelectorAll("[data-file-part]").forEach((part) => {
      /** @type {HTMLElement} */ (part).hidden = !String(/** @type {HTMLElement} */ (part).dataset.filePart).split(" ").includes(fields.kind.value);
    });
    show();
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    fields.kind.addEventListener("change", show);
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const kind = fields.kind.value;
        const body = /** @type {Record<string, string>} */ ({ kind });
        if (kind === "student") { if (!fields.enrollment_id.value) throw new Error("Choose the student."); body.enrollment_id = fields.enrollment_id.value; }
        if (kind === "term") { if (!fields.term_id.value) throw new Error("Choose the term."); body.term_id = fields.term_id.value; }
        if (kind === "course" || kind === "training") { if (!fields.definition_id.value) throw new Error("Choose the course."); body.definition_id = fields.definition_id.value; }
        if (kind === "class_day") {
          if (!fields.section_id.value || !fields.date.value) throw new Error("Choose the section and its class day.");
          body.section_id = fields.section_id.value; body.date = fields.date.value;
        }
        requestId ||= crypto.randomUUID();
        const filed = await api(`${BASE}/${encodeURIComponent(row.id)}/file`, "POST", { ...body, request_id: requestId });
        toast(`${waiting ? "Kept" : "Filed"} under ${filed.name}`);
        if (session()) dialog().close();
        void changed();
        void render(true);
      });
    };
  }

  return { render, summary };
}
