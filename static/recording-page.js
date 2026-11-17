// @ts-check
/**
 * One recording's page, `#recordings/<id>` (docs/decisions/recordings.md, RECORDINGS; steps 2 and 3). The recording as Faculty
 * kept it: its audio, which plays from the Plaud archive from any line; WHAT CAME OUT OF IT (step 3, recording-items.js: what Faculty's reading left
 * the instructor with, what needs them first, what is already done folded); its transcript, each line with who spoke and when,
 * folded once there are items (DESIGN-LANGUAGE "No walls of text"); its speakers, any of whom the instructor may name here
 * (kept in Faculty, never written to Plaud); and Plaud's own summary and outline, folded and marked as Plaud's words.
 * `#recordings/<id>?t=<milliseconds>` opens at that moment, which is where a search result, a ruling and the Assistant point.
 * `&to=<milliseconds>` (step 4) shows that stretch ALONE: one student's part of a recording that holds several, which is where
 * their timeline points; its lines, what was said in it, and the audio stopping at its end. A CLASS (step 5) opens on what it
 * reached beside what was planned for that section's day, with the card that would move what it did not reach.
 *
 * A transcript is evidence, not an instruction: Plaud mishears, so the page plays the moment a line was said, and every item
 * plays its quote. This module draws what the server answers (recordings/service.py, recordings/readings.py); every change
 * here is the instructor's own press.
 */
import { captureDialogIntent, dialogSession, disclosureMarkup, errorMessage, loadingMarkup, plural, runFormAction } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { actionReviewMessage, cardMarkup, submitActionReview } from "./assistant.js";
import { outcomeMarkup, partText, stillReading, transcriptLabel } from "./recording-items.js";

const BASE = "/api/recordings";

/** "12:34" or "1:02:34": how far into the recording. @param {number} ms */
export function offsetText(ms) {
  const seconds = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor((seconds % 3600) / 60), rest = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** "1:30", "1:02:30" or "90" (seconds) as milliseconds into a recording, or null when it is none of them. @param {string} text */
export function parseOffset(text) {
  const found = /^\s*(?:(\d{1,2}):)?(\d{1,3}):([0-5]?\d)\s*$/.exec(String(text || ""));
  if (found) return ((Number(found[1] || 0) * 60 + Number(found[2])) * 60 + Number(found[3])) * 1000;
  return /^\s*\d{1,6}\s*$/.test(String(text || "")) ? Number(text) * 1000 : null;
}

/**
 * A recording as one STRETCH of it shows it (one student's part): the lines that begin in it, what was said in it, and the
 * student whose part it is. The whole recording when no stretch is asked for. @param {any} data @param {{start: number, end: number} | null} stretch
 */
export function stretched(data, stretch) {
  if (!data || !stretch) return data;
  const inside = (/** @type {any} */ entry) => entry.start_ms >= stretch.start && entry.start_ms < stretch.end;
  // What the quote check left out says words too, and carries no place: it is of the whole recording, and is not shown with a part.
  return { ...data, lines: (data.lines || []).filter(inside), items: (data.items || []).filter(inside),
    reading: data.reading ? { ...data.reading, dropped: [] } : data.reading,
    students: (data.students || []).filter((/** @type {any} */ student) => !student.part || (student.part.start_ms === stretch.start && student.part.end_ms === stretch.end)) };
}

/** The words a find looks for, as the server splits them (recordings/transcript.py `words`). @param {string} query */
export function findWords(query) {
  const found = /** @type {string[]} */ ([]);
  for (const piece of String(query || "").split(/\s+/)) {
    const word = piece.replace(/^[.,;:!?"'()[\]{}“”‘’]+|[.,;:!?"'()[\]{}“”‘’]+$/g, "").toLowerCase();
    if (word && !found.includes(word)) found.push(word);
  }
  return found.slice(0, 8);
}

/** Whether a line holds every word. @param {string} text @param {string[]} words */
export function holds(text, words) {
  const low = String(text || "").toLowerCase();
  return words.length > 0 && words.every((word) => low.includes(word));
}

/** A line's words, escaped, with each found word marked. @param {string} text @param {string[]} words @param {(value: unknown) => string} e */
export function markedText(text, words, e) {
  const raw = String(text || "");
  if (!words.length) return e(raw);
  const pattern = new RegExp(`(${words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return raw.split(pattern).map((piece, index) => (index % 2 ? `<mark>${e(piece)}</mark>` : e(piece))).join("");
}

/** The line being said at `ms` (the last one that has begun), or -1 before the first. @param {any[]} lines @param {number} ms */
export function lineAt(lines, ms) {
  let low = 0, high = lines.length - 1, found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (lines[middle].start_ms <= ms) { found = middle; low = middle + 1; } else high = middle - 1;
  }
  return found;
}

/** One line of the transcript: its time is the control that plays from there; the speaker is printed where the voice changes.
 * @param {any} line @param {any} before @param {string[]} words @param {(value: unknown) => string} e */
export function lineMarkup(line, before, words, e) {
  const same = before && before.speaker_key === line.speaker_key;
  return `<li class="recording-line" data-patch-key="line-${e(line.line)}" data-line="${e(line.line)}"${holds(line.text, words) ? " data-found" : ""}>
    <button type="button" class="recording-line-time" data-play="${e(line.start_ms)}" aria-label="Play from ${e(offsetText(line.start_ms))}">${e(offsetText(line.start_ms))}</button>
    <span class="recording-line-speaker">${same ? "" : e(line.speaker)}</span>
    <p class="recording-line-text">${markedText(line.text, words, e)}</p></li>`;
}

/** The transcript, or what there is to say when there is none. @param {any} data @param {string[]} words @param {(value: unknown) => string} e */
export function transcriptMarkup(data, words, e) {
  const state = data.transcript?.state;
  if (state !== "ready" || !(data.lines || []).length) {
    const why = state === "waiting" ? "Faculty is reading this transcript into lines. It shows after the next check."
      : state === "unreadable" ? "Plaud's transcript of this recording is not in a shape Faculty reads. Its file is kept."
        : data.filing === "filed" ? (data.fetch?.detail || "Faculty has kept no transcript of this recording yet.")
          : "Faculty has fetched nothing of this recording: it waits for your Keep or Don’t keep on the Recordings page.";
    return `<p class="recording-facts">${e(why)}</p>`;
  }
  return `<ol class="recording-lines">${data.lines.map((/** @type {any} */ line, /** @type {number} */ index) => lineMarkup(line, data.lines[index - 1], words, e)).join("")}</ol>`;
}

/** The speakers: each a control that names them here. @param {any[]} speakers @param {(value: unknown) => string} e */
export function speakersMarkup(speakers, e) {
  if (!(speakers || []).length) return "";
  return `<span class="recording-speakers-label">Speakers</span>${speakers.map((speaker) => `<button type="button" class="text-button" data-speaker="${e(speaker.key)}"
    title="${e(speaker.name ? `Plaud calls them “${speaker.plaud || speaker.key}”. Change the name` : "Name this speaker")}">${e(speaker.shown)}</button>`).join("")}`;
}

/** Plaud's own summary and outline: folded, and said to be Plaud's words. @param {any} data @param {(value: unknown) => string} e */
export function plaudMarkup(data, e) {
  const outline = data.outline || [], notes = data.notes || [];
  if (!outline.length && !notes.length) return "";
  const topics = outline.length ? `<ol class="recording-outline">${outline.map((/** @type {any} */ item) => `<li>
    <button type="button" class="recording-line-time" data-play="${e(item.start_ms)}" aria-label="Play from ${e(offsetText(item.start_ms))}">${e(offsetText(item.start_ms))}</button>
    <span>${e(item.topic)}</span></li>`).join("")}</ol>` : "";
  const written = notes.map((/** @type {any} */ note) => `<h4 class="recording-note-tab">${e(note.tab)}</h4><div class="recording-note">${e(note.content)}</div>`).join("");
  return disclosureMarkup({ label: "Plaud's summary and outline", hint: "Plaud's words, not Faculty's", key: "recording-plaud", className: "recording-plaud",
    body: `<p class="recording-facts">Written by Plaud from the same audio. It can be wrong; the transcript and the audio are what was said.</p>${topics}${written}` });
}

/** "Tue, Nov 10 · 10:02 AM · 1 h 11 min" is the list's; the page's subtitle adds nothing else. @param {any} data @param {(row: any) => string} when */
export function pageSubtitle(data, when) {
  return [when(data), data.filing === "needs_filing" ? "Waiting for Keep or Don’t keep" : ""].filter(Boolean).join(" · ");
}

/**
 * @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void,
 *   dialogHeader: (title: string, subtitle?: string) => string, header?: any, when: (row: any) => string}} deps
 */
export function createRecordingPage({ api, escape, toast, showDialog, dialogHeader, header = null, when }) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const root = () => /** @type {HTMLElement | null} */ (document.getElementById("recordings-section"));
  const player = () => /** @type {HTMLAudioElement | null} */ (root()?.querySelector("audio[data-recording-audio]") || null);
  let data = /** @type {any} */ (null), shownId = "", generation = 0, find = "", current = -1, opened = "", folded = false, drawn = "";
  /** @type {Promise<void> | null} */
  let loading = null;

  function setHeader() {
    const name = data?.name || "Recording";
    header?.set({ route: location.hash, title: name, subtitle: data ? pageSubtitle(data, when) : "", action: null,
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Recordings", href: "#recordings" }, { label: name }] });
  }

  /** The moment the address asks for (`?t=<milliseconds>`), or null. */
  function asked() {
    const value = new URLSearchParams(location.hash.split("?")[1] || "").get("t");
    return value && /^\d{1,12}$/.test(value) ? Number(value) : null;
  }

  /** The stretch the address asks for (`?t=<start>&to=<end>`: one student's part, shown alone), or null. */
  function stretch() {
    const query = new URLSearchParams(location.hash.split("?")[1] || ""), start = query.get("t"), end = query.get("to");
    return start && end && /^\d{1,12}$/.test(start) && /^\d{1,12}$/.test(end) && Number(end) > Number(start) ? { start: Number(start), end: Number(end) } : null;
  }

  /** What the page shows now: the recording, or the one stretch of it the address asks for. */
  const shown = () => stretched(data, stretch());

  // The shell renders the page, again after its state read, and every 30 s. A recording is read once: its transcript does not
  // change under the reader, and reading it again would only restart nothing. A forced render (a speaker was named) reads again.
  /** @param {string} id @param {boolean} [force] */
  async function render(id, force = false) {
    const host = root();
    if (!host) return;
    if (shownId !== id || !host.querySelector("[data-recording-frame]")) {
      player()?.pause();   // an audio element taken off the page can go on playing
      shownId = id; data = null; find = ""; current = -1; opened = ""; folded = false; drawn = ""; generation++;
      host.innerHTML = `<div class="recording-page" data-recording-frame><div data-recording-top></div>
        <div class="recording-outcome" data-recording-outcome></div>
        <details class="disclosure recording-transcript-fold" data-recording-fold open><summary><span data-recording-fold-label>Transcript</span></summary>
          <div class="recording-transcript" data-recording-lines tabindex="0" role="region" aria-label="Transcript">${loadingMarkup("Loading the recording…", 6)}</div></details>
        <div data-recording-plaud></div></div>`;
      loading = null;
    }
    setHeader();
    // A reading being made is asked about again by the shell's own quiet refresh; nothing else is read twice.
    if (data && !force && stillReading(data) && !dialog().open) force = true;
    // The same recording at another stretch (a student's part, or the whole of it again) is drawn again from what was read.
    if (data && !force) { if (drawn !== JSON.stringify(stretch())) draw(); seek(); return; }
    if (!force && loading) return loading;
    const run = ++generation;
    const promise = (async () => {
      try {
        const next = await api(`${BASE}/${encodeURIComponent(id)}`);
        if (run !== generation) return;
        data = next;
        draw();
        seek();
      } catch (cause) {
        if (run !== generation) return;
        const lines = host.querySelector("[data-recording-lines]");
        if (lines && !data) lines.innerHTML = `<div class="callout" data-tone="danger" role="alert"><p>${e(errorMessage(cause))}</p><a class="button" href="#recordings">All recordings</a></div>`;
      }
    })();
    loading = promise;
    try { await promise; } finally { if (loading === promise) loading = null; }
  }

  function draw() {
    const host = root();
    if (!host || !data) return;
    setHeader();
    const top = /** @type {HTMLElement} */ (host.querySelector("[data-recording-top]"));
    // The player is built once for a recording and never patched: a patch would start it over.
    if (!top.querySelector("[data-recording-player]")) {
      top.innerHTML = `<div class="recording-player" data-recording-player>${data.audio
        ? `<audio data-recording-audio controls preload="metadata" src="${e(data.audio.href)}"></audio><p class="recording-facts" data-recording-cannot hidden></p>`
        : `<p class="recording-facts">${e(data.filing === "filed" ? "The Plaud archive has no audio of this recording yet." : "Nothing plays from the Plaud archive until this recording is kept.")}</p>`}</div>
        <div class="recording-tools"><div class="recording-speakers" data-recording-speakers></div>
        <label class="field recording-find">Find in this recording<input type="search" data-page-search data-recording-find autocomplete="off" placeholder="Words that were said"></label>
        <p class="recording-facts" data-recording-found aria-live="polite"></p></div>`;
      const audio = player();
      if (audio) {
        const cannot = /** @type {HTMLElement} */ (top.querySelector("[data-recording-cannot]"));
        if (!audio.canPlayType(data.audio.type)) {
          cannot.hidden = false;
          cannot.textContent = "This browser does not play this recording's audio (Ogg). The transcript below is complete; the audio plays in Chrome, Edge or Firefox.";
        }
        audio.addEventListener("timeupdate", follow);
        audio.addEventListener("error", () => { cannot.hidden = false; cannot.textContent = "The audio could not be played. Its file is in the Plaud archive; plaud-intake status says whether the archive can be read."; });
      }
      bind(host);
    }
    patchChildren(/** @type {HTMLElement} */ (host.querySelector("[data-recording-speakers]")), speakersMarkup(data.speakers, e));
    const part = stretch(), view = shown();
    drawn = JSON.stringify(part);
    // One student's part, shown alone: said so, with the way back to the whole recording.
    const alone = part ? `<p class="recording-capture" data-recording-stretch>Showing one part of this recording, ${e(partText({ start_ms: part.start, end_ms: part.end }))}. <a class="link-arrow" href="#recordings/${e(encodeURIComponent(shownId))}?t=${part.start}">Show the whole recording</a></p>` : "";
    patchChildren(/** @type {HTMLElement} */ (host.querySelector("[data-recording-outcome]")),
      // A card's page is this one. Where the page says in plain lines what a card would write (a class's move), the route's
      // own preview answer (revision, fingerprint, IDs) is folded under it; a preview that refused stays open.
      alone + outcomeMarkup(view, e, { card: (/** @type {any} */ action, extra = "") => cardMarkup(action, { here: action.page, compact: true, extra, foldPreview: Boolean(extra) }) }));
    const fold = /** @type {HTMLDetailsElement} */ (host.querySelector("[data-recording-fold]"));
    /** @type {HTMLElement} */ (fold.querySelector("[data-recording-fold-label]")).textContent = part ? `Transcript of this part · ${plural(view.lines.length, "line")}` : transcriptLabel(data);
    // Once there is something that came out of it, the transcript is folded beneath it: once, and never against the reader's
    // own toggle, a find or a moment the address asks for.
    if (!folded && (data.items || []).length) { folded = true; if (asked() === null && !find) fold.open = false; }
    drawLines();
    // Plaud's own summary and outline are of the whole recording: with one student's part shown alone they are left out.
    patchChildren(/** @type {HTMLElement} */ (host.querySelector("[data-recording-plaud]")), part ? "" : plaudMarkup(data, e));
  }

  function drawLines() {
    const host = root();
    if (!host || !data) return;
    const words = findWords(find);
    const view = shown();
    patchChildren(/** @type {HTMLElement} */ (host.querySelector("[data-recording-lines]")), transcriptMarkup(view, words, e));
    const count = words.length ? (view.lines || []).filter((/** @type {any} */ line) => holds(line.text, words)).length : 0;
    const found = host.querySelector("[data-recording-found]");
    if (found) found.textContent = words.length ? (count ? `${plural(count, "line")} found. Enter goes to the next.` : "No line holds every one of those words.") : "";
    mark(current);
  }

  /** Mark the line being said (never orange: it needs nothing of the instructor). @param {number} index */
  function mark(index) {
    const host = root();
    host?.querySelectorAll(".recording-line[aria-current]").forEach((line) => line.removeAttribute("aria-current"));
    current = index;
    const lines = shown()?.lines || [];
    if (index < 0 || !lines[index]) return null;
    const line = /** @type {HTMLElement | null} */ (host?.querySelector(`.recording-line[data-line="${lines[index].line}"]`) || null);
    line?.setAttribute("aria-current", "true");
    return line;
  }

  function follow() {
    const audio = player();
    if (!audio || !data?.lines) return;
    // One student's part plays to its end and stops there: the next student's words are not this page's.
    const part = stretch();
    if (part && !audio.paused && audio.currentTime * 1000 >= part.end) audio.pause();
    const index = lineAt(shown().lines, audio.currentTime * 1000);
    if (index !== current) mark(index);
  }

  /** Go to the moment the address asks for, once for each address: the line shown and marked, the audio set there (not played:
   * a browser plays only on the reader's own press). */
  function seek() {
    const moment = asked();
    if (moment === null || !data || opened === location.hash) return;
    opened = location.hash;
    unfold();
    const line = mark(lineAt(shown().lines || [], moment));
    line?.scrollIntoView({ block: "center" });
    const audio = player();
    if (audio) audio.currentTime = moment / 1000;
  }

  /** The transcript shown: a find, a moment asked for and Enter all need its lines on the page. */
  function unfold() {
    const fold = /** @type {HTMLDetailsElement | null} */ (root()?.querySelector("[data-recording-fold]") || null);
    if (fold) fold.open = true;
  }

  /** What the server answered after a change here: the recording again, drawn in place (the player is never rebuilt). @param {any} next */
  function took(next) {
    if (next?.id !== shownId) return;
    data = next;
    draw();
  }

  /** @param {string} path @param {Record<string, unknown>} body @param {HTMLButtonElement} button @param {string} done */
  async function act(path, body, button, done) {
    button.disabled = true;
    try {
      const next = await api(`${BASE}/${encodeURIComponent(shownId)}/${path}`, "POST", { ...body, request_id: crypto.randomUUID() });
      button.disabled = false;   // before the answer is drawn: the drawing says whether the control waits (Read again does)
      took(next);
      if (done) toast(done);
    } catch (cause) {
      button.disabled = false;
      toast(errorMessage(cause), true);
    }
  }

  /** @param {number} ms */
  function play(ms) {
    const audio = player();
    mark(lineAt(shown()?.lines || [], ms));
    if (!audio) return;
    audio.currentTime = ms / 1000;
    void audio.play().catch(() => {});
  }

  /** @param {HTMLElement} host */
  function bind(host) {
    const top = /** @type {HTMLElement} */ (host.querySelector("[data-recording-frame]"));
    top.addEventListener("click", (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      const moment = /** @type {HTMLElement | null} */ (target.closest("[data-play]"));
      if (moment) { play(Number(moment.dataset.play)); return; }
      const speaker = /** @type {HTMLElement | null} */ (target.closest("[data-speaker]"));
      const found = speaker && (data?.speakers || []).find((/** @type {any} */ item) => item.key === speaker.dataset.speaker);
      if (found) { openName(found); return; }
      const button = /** @type {HTMLButtonElement | null} */ (target.closest("button"));
      if (!button || !data) return;
      const item = (/** @type {string | undefined} */ id) => (data.items || []).find((/** @type {any} */ entry) => entry.id === id);
      // What became of an item is the instructor's own press: an event in Faculty and nothing else.
      if (button.dataset.itemState) {
        void act(`items/${encodeURIComponent(button.dataset.recordingItem || "")}`, { state: button.dataset.itemState }, button, "");
      } else if (button.dataset.itemCard) {
        void act(`items/${encodeURIComponent(button.dataset.itemCard)}/card`, {}, button, "Proposed. Approve its card to add it to your calendar.");
      } else if (button.dataset.itemAsk) {
        // A memo's request, put to the Assistant by the instructor's own press: every change its run wants is a card.
        void act(`items/${encodeURIComponent(button.dataset.itemAsk)}/ask`, {}, button, "Put to the Assistant. Its answer shows here.");
      } else if (button.dataset.itemAskAgain) {
        const found = item(button.dataset.itemAskAgain);
        if (found) openAskAgain(found);
      } else if (button.dataset.itemRemember) {
        const found = item(button.dataset.itemRemember);
        if (found) openRemember(found);
      } else if (button.dataset.studentCard) {
        // A student's card, proposed again: only Approve on it writes anything.
        void act("cards", { kind: button.dataset.studentCard, enrollment_id: button.dataset.student }, button,
          button.dataset.studentCard === "note" ? "Proposed. Approve its card to save the note." : "Proposed. Approve its card to put it on their follow-up.");
      } else if (button.hasAttribute("data-class-move")) {
        // A class's move, proposed again: only Approve on its card changes the class schedule.
        void act("cards", { kind: "move" }, button, "Proposed. Approve its card to change the class schedule.");
      } else if (button.hasAttribute("data-recording-students")) {
        void openStudents();
      } else if (button.hasAttribute("data-recording-read")) {
        void act("read", {}, button, "Faculty is reading it again.");
      } else if (button.hasAttribute("data-recording-coordinator")) {
        openCoordinator();
      } else if (button.dataset.assistantApprove || button.dataset.assistantDecline) {
        // An item's Approve card is the Assistant's own card: approved and declined as everywhere else, then read again.
        const approve = Boolean(button.dataset.assistantApprove), card = /** @type {HTMLElement} */ (button.closest("[data-action-id]"));
        button.disabled = true;
        void (async () => {
          try {
            const result = await submitActionReview(api, button.dataset.assistantApprove || button.dataset.assistantDecline, Number(card.dataset.seen), approve);
            toast(approve ? actionReviewMessage(result) : "Declined.");
          } catch (cause) {
            toast(errorMessage(cause), true);
          } finally {
            button.disabled = false;
            void render(shownId, true);
          }
        })();
      }
    });
    const input = /** @type {HTMLInputElement} */ (top.querySelector("[data-recording-find]"));
    input.addEventListener("input", () => { find = input.value; if (find.trim()) unfold(); drawLines(); });
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      unfold();
      const lines = /** @type {HTMLElement[]} */ ([...top.querySelectorAll(".recording-line[data-found]")]);
      if (!lines.length) return;
      const view = shown().lines || [];
      const after = lines.find((line) => Number(line.dataset.line) > (view[current]?.line ?? -1)) || lines[0];
      mark(view.findIndex((/** @type {any} */ line) => String(line.line) === after.dataset.line));
      after.scrollIntoView({ block: "center" });
    });
  }

  /** @param {any} speaker */
  function openName(speaker) {
    const intended = captureDialogIntent(dialog());
    if (!intended()) return;
    showDialog(`<form class="recording-name-form" id="recording-name-form" novalidate>${dialogHeader("Name this speaker", e(`${plural(speaker.lines, "line")} in this recording`))}
      <div class="dialog-body"><p class="field-hint">Plaud calls them “${e(speaker.plaud || speaker.key)}”. The name is kept in Faculty and never sent to Plaud.</p>
        <label class="field">Name<input type="text" name="name" maxlength="100" autocomplete="off" value="${e(speaker.name || "")}" placeholder="${e(speaker.plaud || speaker.key)}"></label>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save name</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("recording-name-form"));
    const session = dialogSession(form, dialog());
    const fields = /** @type {any} */ (form.elements);
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    fields.name.select();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        requestId ||= crypto.randomUUID();
        const next = await api(`${BASE}/${encodeURIComponent(shownId)}/speakers`, "POST", { speaker: speaker.key, name: fields.name.value, request_id: requestId });
        if (next.id === shownId) { data = next; draw(); }
        toast(fields.name.value.trim() ? "Name saved" : "Name taken back");
        if (session()) dialog().close();
      });
    };
  }

  /**
   * SEVERAL STUDENTS IN ONE RECORDING (step 4): who this recording is of, each with the part that is theirs, set by the
   * instructor. One student may have no part (the whole recording); with several, each has one and no two overlap, so no
   * student's timeline, follow-up or note is given another student's words.
   */
  async function openStudents() {
    const intended = captureDialogIntent(dialog());
    /** @type {any[]} */
    let groups;
    try { groups = (await api("/api/student-notes/students")).groups || []; } catch (cause) { toast(errorMessage(cause), true); return; }
    if (!intended() || !data) return;
    const choices = (/** @type {string} */ chosen) => `<option value="">Choose the student</option>${groups.map((/** @type {any} */ group) => `<optgroup label="${e(`${group.code} · ${group.term_label}`)}">${
      group.students.map((/** @type {any} */ student) => `<option value="${e(student.enrollment_id)}"${student.enrollment_id === chosen ? " selected" : ""}>${e(student.name)} · section ${e(student.sections.join(", "))}</option>`).join("")}</optgroup>`).join("")}`;
    const row = (/** @type {any} */ student) => `<li class="recording-part" data-part-row>
      <label class="field">Student<select name="student">${choices(student.enrollment_id || "")}</select></label>
      <label class="field">From<input type="text" name="from" inputmode="numeric" autocomplete="off" placeholder="0:00" value="${student.part ? e(offsetText(student.part.start_ms)) : ""}"></label>
      <label class="field">To<input type="text" name="to" inputmode="numeric" autocomplete="off" placeholder="${e(offsetText(data.duration_ms || 0))}" value="${student.part ? e(offsetText(student.part.end_ms)) : ""}"></label>
      <button type="button" class="text-button" data-part-now title="Set the empty time of this row to where the audio is now">Now</button>
      <button type="button" class="text-button" data-part-remove>Remove</button></li>`;
    const now = data.students?.length ? data.students : [{}];
    showDialog(`<form class="recording-name-form" id="recording-students-form" novalidate>${dialogHeader("Students in this recording", "Each student sees only their own part")}
      <div class="dialog-body"><p class="field-hint">With one student the whole recording is theirs: leave the times empty. With several, give each the part that is theirs, as minutes and seconds into the recording (12:30). Their timeline, follow-up and note are given that part alone.</p>
        <ol class="recording-parts" data-part-rows>${now.map(row).join("")}</ol>
        <button type="button" class="text-button" data-part-add>Add a student</button>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("recording-students-form"));
    const session = dialogSession(form, dialog());
    const rows = /** @type {HTMLElement} */ (form.querySelector("[data-part-rows]"));
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    form.addEventListener("click", (event) => {
      const button = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest("button"));
      if (!button) return;
      const line = /** @type {HTMLElement | null} */ (button.closest("[data-part-row]"));
      if (button.hasAttribute("data-part-add")) { rows.insertAdjacentHTML("beforeend", row({})); requestId = ""; }
      else if (button.hasAttribute("data-part-remove") && line && rows.children.length > 1) { line.remove(); requestId = ""; }
      else if (button.hasAttribute("data-part-now") && line) {
        // Where the audio is now goes into the first empty time of the row: From, then To.
        const empty = /** @type {HTMLInputElement | undefined} */ ([...line.querySelectorAll("input")].find((input) => !input.value.trim()));
        if (empty) { empty.value = offsetText((player()?.currentTime || 0) * 1000); requestId = ""; }
      }
    });
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const students = [...rows.querySelectorAll("[data-part-row]")].map((line) => {
          const field = (/** @type {string} */ name) => /** @type {HTMLInputElement} */ (line.querySelector(`[name="${name}"]`)).value;
          if (!field("student")) throw new Error("Choose the student of each row, or remove the row.");
          const from = field("from").trim(), to = field("to").trim();
          if (!from && !to) return { enrollment_id: field("student"), start_ms: null, end_ms: null };
          const start = parseOffset(from || "0:00"), end = parseOffset(to);
          if (start === null || end === null) throw new Error("Give each time as minutes and seconds into the recording, like 12:30.");
          return { enrollment_id: field("student"), start_ms: start, end_ms: end };
        });
        requestId ||= crypto.randomUUID();
        took(await api(`${BASE}/${encodeURIComponent(shownId)}/students`, "POST", { students, request_id: requestId }));
        toast("Saved");
        if (session()) dialog().close();
      });
    };
  }

  /** Remember: a standing rule said in the meeting, kept as a memory in the words shown, which are the instructor's to edit
   * first, for this term unless they widen it (ASSISTANT-MEMORY "SAID IN A RECORDING"). @param {any} item */
  function openRemember(item) {
    const intended = captureDialogIntent(dialog());
    if (!intended()) return;
    const term = data.coordinator?.term || "", course = String(term).split(" · ")[0];
    showDialog(`<form class="recording-name-form" id="recording-remember-form" novalidate>${dialogHeader("Remember this", "The Assistant is told it when it bears on a request")}
      <div class="dialog-body"><p class="field-hint">${e(item.speaker)} said, at ${e(item.at)}: “${e(item.quote)}”</p>
        <label class="field">What to remember<textarea name="content" rows="3" maxlength="4000" required>${e(item.sentence)}</textarea></label>
        <label class="field">Applies to<select name="scope">${term ? `<option value="term">${e(term)}</option>` : ""}${course ? `<option value="course">${e(course)}, every term</option>` : ""}<option value="workspace">Everything</option></select></label>
        <p class="field-hint">A memory never names a student and never holds a date, room or grade a record owns.</p>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Remember</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("recording-remember-form"));
    const session = dialogSession(form, dialog());
    const fields = /** @type {any} */ (form.elements);
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        requestId ||= crypto.randomUUID();
        took(await api(`${BASE}/${encodeURIComponent(shownId)}/items/${encodeURIComponent(item.id)}/remember`, "POST",
          { content: fields.content.value, scope: fields.scope.value, request_id: requestId }));
        toast("Kept as a memory");
        if (session()) dialog().close();
      });
    };
  }

  /** Ask again: a memo's request put to the Assistant once more, with a sentence of the instructor's own if they add one
   * (the answer to what it asked back, or what it got wrong). The earlier answer's cards that still wait are withdrawn.
   * @param {any} item */
  function openAskAgain(item) {
    const intended = captureDialogIntent(dialog());
    if (!intended()) return;
    const asked = (item.ask?.question?.questions || []).map((/** @type {any} */ question) => question.title).join(" ");
    showDialog(`<form class="recording-name-form" id="recording-ask-form" novalidate>${dialogHeader("Ask the Assistant again", "Every change it wants waits for your approval")}
      <div class="dialog-body"><p class="field-hint">You said, at ${e(item.at)}: “${e(item.quote)}”</p>
        ${asked ? `<p class="field-hint">The Assistant asked: ${e(asked)}</p>` : ""}
        <label class="field">Anything to add for the Assistant (optional)<textarea name="guidance" rows="3" maxlength="2000"></textarea></label>
        <p class="field-hint">Its earlier answer is replaced. Changes of that answer you have not approved yet are withdrawn; what you approved stands.</p>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Ask again</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("recording-ask-form"));
    const session = dialogSession(form, dialog());
    const fields = /** @type {any} */ (form.elements);
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        requestId ||= crypto.randomUUID();
        took(await api(`${BASE}/${encodeURIComponent(shownId)}/items/${encodeURIComponent(item.id)}/ask`, "POST", { guidance: fields.guidance.value, request_id: requestId }));
        toast("Put to the Assistant again");
        if (session()) dialog().close();
      });
    };
  }

  /** The term's coordinator: a name alone, which the next reading is told. */
  function openCoordinator() {
    const intended = captureDialogIntent(dialog());
    if (!intended() || !data?.coordinator?.term_id) return;
    if (data.coordinator.card) {
      // COORDINATOR "ONE NAME": the course card names this term's coordinator; it is changed there, in Course settings.
      showDialog(`<div class="recording-name-form">${dialogHeader("Coordinator", e(data.coordinator.term))}
        <div class="dialog-body"><p><strong>${e(data.coordinator.name)}</strong></p>
          <p class="field-hint">Named on this course’s card. A reading is told this name, so “the coordinator” in a meeting is a person. To change it, open Course settings on the course’s page.</p></div>
        <footer class="dialog-footer"><button type="button" class="button primary" data-action="close">Close</button></footer></div>`);
      return;
    }
    showDialog(`<form class="recording-name-form" id="recording-coordinator-form" novalidate>${dialogHeader("Coordinator", e(data.coordinator.term))}
      <div class="dialog-body"><p class="field-hint">A reading is told this name, so “the coordinator” in a meeting is a person. It is kept for the term; Read again uses it. A course marked Coordinated on its course card uses the name there instead.</p>
        <label class="field">Name<input type="text" name="name" maxlength="100" autocomplete="off" value="${e(data.coordinator.name || "")}"></label>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save name</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("recording-coordinator-form"));
    const session = dialogSession(form, dialog());
    const fields = /** @type {any} */ (form.elements);
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    fields.name.select();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        requestId ||= crypto.randomUUID();
        const saved = await api(`${BASE}/coordinator`, "POST", { term_id: data.coordinator.term_id, name: fields.name.value, request_id: requestId });
        if (data?.coordinator) { data.coordinator.name = saved.name; draw(); }
        toast(saved.name ? "Coordinator saved" : "Coordinator taken back");
        if (session()) dialog().close();
      });
    };
  }

  /** Leaving the page stops the audio: nothing plays from a page that is not shown. */
  function leave() {
    player()?.pause();
    shownId = ""; data = null; generation++;
  }

  // Another page of the app: the section is hidden, and what is hidden does not go on playing.
  window.addEventListener("hashchange", () => { if (shownId && !location.hash.startsWith(`#recordings/${shownId}`)) player()?.pause(); });

  return { render, leave };
}
