// The gradebook (gradebook/README.md, "Browser"): a term's scores as one spreadsheet, shown IN EACH SECTION TAB of the course page
// where the roster was (requirement, 2026-09-28): #course/<gid>/section/<n>[?item=<iid>&assignment=<aid>[&pull=1]] is that section's
// gradebook (its pulls and imports write only that section), /setup under it the term's shared setup, /changes every change to
// the section's scores and the setup; the roster is .../roster. The course Home shows no combined grid:
// #course/<gid>/home/gradebook lists the sections. The earlier #gradebook, #gradebook?group=<gid>[&section=<n>] and
// #gradebook/<term_id>[/setup][?section=<sid>&item=...] addresses (links kept in exam hubs, the calendar, grading) are resolved and
// replaced by the course page's address.
// The browser holds only view state (search, sort, the cell in focus) and the writes still in flight.
// Every score, column and result comes from the server; nothing here publishes, emails or sends a grade anywhere.
// The setup editor, the Changes page and the dialogs (import, pull, history, column, link) live in gradebook-setup.js,
// gradebook-changes.js and gradebook-dialogs.js, loaded through ui.js loadModule and warmed once when the workspace starts (an
// idle moment), so a tab opened before Faculty was updated and restarted already holds them; a fault in any of them never stops
// the rest of the workspace, and a module that still cannot be fetched says "Faculty was updated since this tab opened" with a
// Reload.

import { captureDialogIntent, escape as plain, fold, isCaptureHeld, loadModule, localToday, plural, setBusy } from "./ui.js";
import { viewLink } from "./file-viewer.js";

/**
 * What the grid, the setup page and every gradebook dialog share. Built once per term route.
 * @typedef {object} GradebookContext
 * @property {(path: string, method?: string, data?: unknown, headers?: Record<string, string>, timeoutMs?: number) => Promise<any>} api
 *   The shared JSON client (request.js): never retries; an ApiError keeps `status` and `body`.
 * @property {(value: unknown) => string} escape  HTML-escapes text for markup.
 * @property {(name: string, className?: string) => string} icon  icons.js markup (decorative SVG).
 * @property {(message: string, error?: boolean | {tone?: string, action?: {label: string, run: Function}}) => void} toast
 * @property {(markup: string) => void} showDialog  Fills and opens the shared #dialog.
 * @property {(title: string, subtitle?: string) => string} dialogHeader  The shared dialog header markup.
 * @property {string} termId  `course_terms.id` of this gradebook.
 * @property {GradebookView} view  The latest View (a getter: always the one on screen).
 * @property {string} sectionId  The section the grid shows (a getter). Optional context only.
 * @property {string} scopeSection  A section tab's section (a getter): its pulls and imports cover only that section, and its first
 *   Sync with Canvas ticks only that section's course. "" for the whole term (the course Home's Sync).
 * @property {string} [viewSection]  With no `scopeSection`: the section whose gradebook a Sync's answer carries (one section's cells
 *   instead of the whole term's, which nothing on the course Home shows).
 * @property {() => Promise<GradebookView>} reload  GET the View again, adopt it, re-render; resolves with it.
 * @property {(view: GradebookView) => void} replaceView  Adopt a View returned by a write (setup, import, pull, link)
 *   and re-render. A View carrying `item_id` (from a link) highlights that column.
 * @property {(result: {cells: Object<string, Object<string, GradebookCell>>, results: any, requests?: any[]}) => void} applyScores
 *   Merge the `{cells, results, requests}` of a scores write and update the grid and "Recent changes" in place.
 * @property {() => string} requestId  A new request ID (crypto.randomUUID()).
 * @property {((info: {enrollmentId: string, name: string, context?: string}) => void) | null} [communications]
 *   Opens a student's communications record (communications.js), when the app provides it.
 * @property {(() => any) | null} [absences]  The absences view (absences.js): the student record's Absences part.
 * @property {(() => any) | null} [notes]  The student notes view (student-notes.js): the student record's Notes part.
 */

/**
 * @typedef {{id: string, status: string, points: string|null, note: string, source: string, actor: string, at: string}} GradebookCell
 * @typedef {{row: string, section_id: string, section: number, name: string, accommodations?: string[], dr?: string[], dropped?: boolean}} GradebookStudent
 * @typedef {{id: string, title: string, category_id: string|null, points: string, due_date?: string, counts?: boolean,
 *   extra_credit?: boolean, archived?: boolean, sections?: string[], assignment_id?: string|null,
 *   link?: {kind: string, parts: string[]}|null, notes?: string}} GradebookItem
 * @typedef {{id: string, name: string, weight?: string, drop_lowest?: number, aggregate?: string}} GradebookCategory
 * @typedef {{term: any, sections: {id: string, number: number}[], setup: any, students: GradebookStudent[],
 *   cells: Object<string, Object<string, GradebookCell>>, results: any, assignments: any[], links: Object<string, any>,
 *   requests: any[], scored_items?: string[], item_id?: string}} GradebookView  (gradebook/README.md, "API": TermSummary,
 *   Results, …). `scored_items` names every column with any score revision, cleared ones included: the columns a save
 *   cannot remove.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// A cell names only its source; which file, Canvas or iClicker imported it is in the cell's history (its request).
const SOURCES = { grading: "Pulled from grading", import: "Imported", instructor: "Entered in Faculty" };
const PARTS = { frq: "FRQ", bubble: "Bubble", exams: "Exams" };
const PART_NAMES = { frq: "free-response grading", bubble: "bubble sheets", exams: "the Exams page" };
const KINDS = { scores: "Scores entered", import: "Import", pull: "Pull from grading" };
const PAGE_ROWS = 20;
const MAX_CHANGES = 5000;
// Column widths belong to the script: a fixed table layout is what keeps 400 × 40 cells quick to lay out. One set: the app is
// desktop only (DESIGN-LANGUAGE, 2026-10-03). A score column is 128 px so "100 pts · Sep 17" fits (letter = --gb-letter-w,
// result = --gb-result-w in gradebook.css).
const WIDTHS = { name: 232, item: 128, result: 88, letter: 64 };
// A lazily loaded module that cannot be fetched: most likely this tab predates a restart that retired its build.
const UPDATED = "Faculty was updated since this tab opened. Reload to continue.";
// How much of the results stays at the right edge: 0 nothing, 1 Overall and Letter, 2 with the categories.
const FREEZE_CLASS = { 0: "gb-freeze-none", 1: "", 2: "gb-freeze-cats" };

const enc = encodeURIComponent;
const when = (value) => {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
};

// The section of each course whose gradebook was opened last: a per-viewer convenience for the section chooser (browser storage,
// which may be missing or refuse; nothing depends on it).
const LAST_SECTION = (groupId) => `faculty.gradebook.last-section.${groupId}`;
function lastSection(groupId) {
  try { return globalThis.localStorage?.getItem(LAST_SECTION(groupId)) || ""; } catch { return ""; }
}
function rememberSection(groupId, number) {
  try { globalThis.localStorage?.setItem(LAST_SECTION(groupId), String(number)); } catch { /* a convenience only */ }
}

/** Whether a popover is showing (false where the browser has no popovers). */
function menuOpen(menu) {
  try { return Boolean(menu?.matches(":popover-open")); } catch { return false; }
}

function safeDecode(value) {
  try { return decodeURIComponent(value); } catch { return value; }
}

// A section tab IS its gradebook (requirement, 2026-09-28): #course/<gid>/section/<n>[/setup|/changes]; the roster is
// .../roster. The course Home has no combined grid: #course/<gid>/home/gradebook[/setup|/changes] lists the sections to choose from.
// The short-lived .../section/<n>/gradebook[/setup] addresses still open the section.
const COURSE_ROUTE = /^course\/([^/]+)\/(?:home\/gradebook|section\/([0-9]+)(?:\/gradebook)?)(\/setup|\/changes)?$/;

/** Whether an address is a gradebook page (a course tab's Gradebook view, or an earlier #gradebook address to resolve). */
export function isGradebookHash(hash) {
  const path = String(hash || "").replace(/^#/, "").split("?")[0];
  return path === "gradebook" || path.startsWith("gradebook/") || COURSE_ROUTE.test(path);
}

/** The course page's gradebook address of a group (course code and term) and a section number (null: the course Home's). */
export function courseGradebookHref(groupId, sectionNumber = null) {
  return sectionNumber === null || sectionNumber === undefined || sectionNumber === ""
    ? `#course/${encodeURIComponent(groupId)}/home/gradebook` : `#course/${encodeURIComponent(groupId)}/section/${sectionNumber}`;
}

/** The route a gradebook hash names. `page` is terms, group, grid, setup, changes, course or none; a course route's `sub` is grid,
 * setup or changes. */
export function parseRoute(hash) {
  const [path, query = ""] = String(hash || "").replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  const course = path.match(COURSE_ROUTE);
  if (course) {
    return { page: "course", sub: course[3] ? course[3].slice(1) : "grid", group: safeDecode(course[1]), number: course[2] ? Number(course[2]) : null,
      section: params.get("section") || "", item: params.get("item") || "", assignment: params.get("assignment") || "", pull: params.get("pull") === "1" };
  }
  const parts = path.split("/");
  if (parts[0] !== "gradebook") return { page: "none" };
  if (!parts[1]) {
    const group = params.get("group");
    return group ? { page: "group", group, section: params.get("section") || "" } : { page: "terms" };
  }
  const termId = safeDecode(parts[1]);
  if (parts[2] === "setup" || parts[2] === "changes") return { page: parts[2], termId };
  // `pull=1` (the exam hub's Recorded link) opens the pull preview of the assignment's column once;
  // the explicit "Pull N scores" is still the instructor's.
  return { page: "grid", termId, section: params.get("section") || "", item: params.get("item") || "", assignment: params.get("assignment") || "",
    pull: params.get("pull") === "1" };
}

/** Who made a record, in words: the in-app assistant's (request_actor.py) say "the assistant"; Faculty's own (actor `automatic`: a late
 * paper it certified by itself, GRADING "LATE PAPERS CERTIFY THEMSELVES") "Faculty, by itself"; any other keeps its actor. */
export const actorText = (actor) => (actor === "assistant" ? "the assistant" : actor === "automatic" ? "Faculty, by itself" : String(actor || ""));

/** Points as the instructor writes them: 4, 2.5, 8.3333 (no trailing zeros, no leading zeros). */
export function trimPoints(value) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  const match = /^(\d*)(?:\.(\d*))?$/.exec(text);
  if (!match || text === "" || text === ".") return text;
  const whole = (match[1] || "0").replace(/^0+(?=\d)/, "");
  const fraction = (match[2] || "").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole;
}

/**
 * Points as the grid SHOWS them: at most two decimals (requirement, 2026-10-04). Display only:
 * the stored value (up to four decimals) is what an editor starts with, what a copy takes, what the cell's description and
 * history say and what every total is computed from. `title` carries the stored value when rounding changed it.
 * @param {unknown} value @returns {{text: string, full: string}}
 */
export function shownPoints(value) {
  const full = trimPoints(value);
  if (!/^\d+\.\d{3,}$/.test(full)) return { text: full, full };
  return { text: trimPoints((Math.round(Number(full) * 100) / 100).toFixed(2)), full };
}

/**
 * What a typed or pasted value means: `{status, points?}` or `{error}`. Empty clears; EX/excused and MI/missing are
 * words; a number is points (not negative, four decimals at most, as the server allows). Nothing else is guessed.
 */
export function parseEntry(text) {
  const value = String(text ?? "").trim();
  const lower = value.toLowerCase();
  if (!value) return { status: "cleared" };
  if (lower === "ex" || lower === "excused") return { status: "excused" };
  if (lower === "mi" || lower === "missing") return { status: "missing" };
  if (/^(\d+(\.\d*)?|\.\d+)$/.test(value)) {
    const fraction = value.includes(".") ? value.split(".")[1].replace(/0+$/, "") : "";
    if (fraction.length > 4) return { error: `${value} has more than four decimal places.` };
    const points = trimPoints(value);
    if (Number(points) > 100000) return { error: "A score can be at most 100000 points." };
    return { status: "scored", points };
  }
  return { error: `“${value.slice(0, 40)}” is not a score. Type points, EX (excused), MI (missing), or nothing to clear it.` };
}

/** The text an editor starts with for a cell (its value as it would be typed). */
export function entryText(cell) {
  if (!cell) return "";
  if (cell.status === "scored") return trimPoints(cell.points);
  if (cell.status === "excused") return "EX";
  if (cell.status === "missing") return "MI";
  return "";
}

/** Whether a cell already holds an entry (so nothing needs to be sent). */
export function sameEntry(cell, entry) {
  const status = cell && cell.status !== "cleared" ? cell.status : "cleared";
  if (status !== entry.status) return false;
  return status !== "scored" || trimPoints(cell.points) === trimPoints(entry.points);
}

/** A block of pasted cells: rows of tab-separated values (a spreadsheet's copy), the final line break ignored. */
export function parseBlock(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  if (lines.length > 1 && lines.at(-1) === "") lines.pop();
  return lines.map((line) => line.split("\t"));
}

/** "2026-10-01" as "Oct 1", read as the date written (no time zone). */
export function shortDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  return match ? `${MONTHS[Number(match[2]) - 1] || ""} ${Number(match[3])}` : "";
}

/**
 * THE MORNING SYNC (gradebook/canvas_nightly.py; requirement, 2026-10-08): what its latest run asks of the instructor, or null when it asks
 * nothing (each sync and import is on the Changes page; held grades and an unconfirmed send are the last sync's notice,
 * `syncState`). A run of the 10 pm import (no `kind`) is named as it was. `action` is "signin" (Sign in to Canvas), "import" (their
 * own Sync with Canvas decides what waits) or "". ONE NOTICE (requirement, 2026-10-10, shown two banners for one morning's send):
 * posts that did not take are said by the last sync's own notice (`syncState`) when that sync is this run's and Canvas did not
 * confirm its send (`last`), so they are left out here.
 * @param {any} nightly `view.canvas.nightly`
 * @param {any} [last] `view.canvas.last_sync`
 * @returns {{tone: string, text: string, action: string, skip?: string} | null}
 */
export function nightlyNotice(nightly, last = null) {
  if (!nightly) return null;
  const night = shortDate(nightly.night) || String(nightly.night || "");
  const waiting = nightly.waiting || [];
  const morning = Boolean(nightly.kind), name = morning ? "The morning sync with Canvas" : "The nightly WebAssign import";
  if (nightly.status === "signed_out" && !nightly.signed_in_since) {
    return { tone: "warning", action: "signin", text: `${name} could not read Canvas on ${night}: Faculty’s Canvas sign-in has ended. Sign in, and the next run ${morning ? "syncs" : "brings the scores in"}.` };
  }
  if (nightly.status === "failed") {
    return { tone: "warning", action: "", text: `${name} could not finish on ${night}. ${nightly.problem || ""} It tries again each hour until ${morning ? "8 AM, then the next morning" : "midnight, then the next night"}.`.replace(/\s+/g, " ").trim() };
  }
  if (nightly.status === "needs_choice") {
    return { tone: "warning", action: "import", text: `${name} ${morning && nightly.kind === "sync" ? "sent nothing" : "wrote nothing"} on ${night}. ${nightly.problem || ""}`.trim() };
  }
  if (nightly.unsure) {
    return { tone: "warning", action: "import", text: `${plural(nightly.unsure, "grade")} ${nightly.unsure === 1 ? "waits" : "wait"} for your own Sync with Canvas: Faculty could not tell whether an earlier send of ${nightly.unsure === 1 ? "it" : "them"} reached Canvas, so the morning sync leaves ${nightly.unsure === 1 ? "it" : "them"} to you.` };
  }
  if (nightly.posts_failed && !(sameSend(nightly, last) && sendLeft(last))) {
    return { tone: "warning", action: "import", text: `The morning sync could not post or hide ${plural(nightly.posts_failed, "column")} in Canvas on ${night}. Sync with Canvas tries again where Canvas still differs.` };
  }
  if (waiting.length) {
    const named = waiting.slice(0, 3).map((title) => `“${title}”`).join(", ") + (waiting.length > 3 ? ` and ${waiting.length - 3} more` : "");
    // Leave out (requirement, 2026-10-04): `skip` offers leaving them out of every import.
    return { tone: "info", action: "import", skip: waiting.length === 1 ? "Leave it out" : "Leave them out",
      text: `${waiting.length === 1 ? "A Canvas assignment waits" : `${waiting.length} Canvas assignments wait`} for your choice of column before the morning sync fills ${waiting.length === 1 ? "it" : "them"}: ${named}.` };
  }
  return null;
}

/**
 * What a sync's send left unconfirmed, or null when Canvas confirmed all of it. `unsure`: the grades Canvas is still not known to
 * hold (`send.unconfirmed`, which a later read of Canvas lowers: CONFIRMED BY A LATER READ, gradebook/canvas_confirm.py; a send
 * recorded before it, its own failed and uncertain counts); `stopped`: the send ended before Canvas confirmed every grade.
 * @param {any} last `view.canvas.last_sync`
 * @returns {{unsure: number, stopped: boolean} | null}
 */
function sendLeft(last) {
  const send = last?.send;
  if (!send) return null;
  const progress = send.progress || {};
  const unsure = Number(send.unconfirmed ?? ((progress.failed || 0) + (progress.uncertain || 0))) || 0;
  const stopped = ["failed", "interrupted", "cancelled"].includes(send.status);
  return unsure || stopped ? { unsure, stopped } : null;
}

/** Whether the morning run's send is the last sync's.
 * @param {any} nightly `view.canvas.nightly`
 * @param {any} last `view.canvas.last_sync`
 */
const sameSend = (nightly, last) => Boolean(nightly?.send?.id && nightly.send.id === last?.send?.id);

/**
 * SYNC WITH CANVAS (docs/decisions/canvas.md; requirement, 2026-10-06): the
 * header's one Canvas action. `label` is "Start from Canvas" until the term has linked a Canvas assignment, then "Sync with Canvas";
 * `title` says when the term last synced and how many of Faculty's grades changed since (catalog only, nothing read from Canvas);
 * `notice` only when the last sync left something for the instructor: a send Canvas did not confirm (with the posts the morning
 * sync could not make for it: ONE NOTICE, `nightlyNotice`), or grades changed in Canvas that wait for a choice. Null where no
 * section links a Canvas course.
 * @param {any} canvas `view.canvas`
 * @param {(value: string) => string} [format] a time as the page shows it
 * @returns {{label: string, title: string, notice: {tone: string, text: string} | null} | null}
 */
export function syncState(canvas, format = (value) => String(value || "")) {
  if (!canvas?.linked_sections) return null;
  const last = canvas.last_sync;
  const label = canvas.started || last ? "Sync with Canvas" : "Start from Canvas";
  const title = last ? `Last synced ${format(last.at)}${last.automatic ? " by the morning sync" : ""}${canvas.changed_since ? ` · ${plural(canvas.changed_since, "grade")} changed in Faculty since` : ""}`
    : "Faculty and Canvas have not been synced yet";
  const left = sendLeft(last);
  let notice = null;
  if (left) {
    const posts = sameSend(canvas.nightly, last) ? Number(canvas.nightly.posts_failed) || 0 : 0;
    notice = { tone: "warning", text: `The last sync ${left.unsure ? `could not confirm ${plural(left.unsure, "grade")} in Canvas` : "stopped before Canvas confirmed every grade"}${posts ? `, and ${plural(posts, "column")} ${posts === 1 ? "was" : "were"} not posted or hidden there` : ""}. Sync again: Faculty reads Canvas first and sends only what Canvas still lacks.` };
  } else if (last?.held) {
    notice = { tone: "info", text: `${plural(last.held, "grade")} changed in Canvas since Faculty last knew ${last.held === 1 ? "it waits" : "them wait"} for your choice in Sync with Canvas.` };
  }
  return { label, title, notice };
}

/**
 * ICLICKER AFTER CLASS (gradebook/iclicker_after_class.py; requirement, 2026-10-06): what the section's latest after-class import
 * asks of the instructor, or null when it asks nothing (each import is on the Changes page). Only for a section that links an
 * iClicker course.
 * @param {any} iclicker `view.iclicker`
 * @param {string} sectionId the section shown
 * @returns {{tone: string, text: string} | null}
 */
export function iclickerNotice(iclicker, sectionId) {
  if (!iclicker || !sectionId || !(iclicker.linked_sections || []).includes(sectionId)) return null;
  if (!iclicker.scheduled) return { tone: "info", text: "iClicker is imported by itself after each class once this term uses its Term schedule (the course page’s Schedule)." };
  const run = iclicker.after_class?.[sectionId];
  if (!run) return null;
  const day = shortDate(run.class_date) || run.class_date;
  if (run.status === "failed") {
    return { tone: "warning", text: `iClicker could not be imported after class on ${day}. ${run.problem || ""} It tries again each hour until midnight; Import from iClicker brings it in now.`.replace(/\s+/g, " ").trim() };
  }
  if (run.status === "needs_choice") {
    const waiting = run.waiting || [];
    return { tone: "info", text: waiting.length ? `${waiting.length === 1 ? "An iClicker session waits" : `${waiting.length} iClicker sessions wait`} for your choice of column: ${waiting.slice(0, 3).map((title) => `“${title}”`).join(", ")}${waiting.length > 3 ? ` and ${waiting.length - 3} more` : ""}.`
      : `iClicker’s import after class on ${day} needs your choice. ${run.problem || ""}`.trim() };
  }
  return null;
}

const percentText = (value) => (value === null || value === undefined || value === "" ? "—" : `${value}%`);
const applies = (item, student) => !item.sections?.length || item.sections.includes(student.section_id);
/** Whether the server's results drop this column's score for this student (lowest in its category). */
export const droppedIn = (view, item, student) => Boolean(item?.category_id && view?.results?.students?.[student.row]?.categories?.[item.category_id]?.dropped?.includes(item.id));
/** The column whose higher score replaced this one's for this student (the server's results), else null. */
export const replacedBy = (view, item, student) => (item?.category_id && view?.results?.students?.[student.row]?.categories?.[item.category_id]?.replaced?.find((entry) => entry.item === item.id)?.by) || null;
/** Whether this replacement column's score took the place of the student's lowest in its category. */
const replacing = (view, item, student) => Boolean(item?.replaces_lowest && view?.results?.students?.[student.row]?.categories?.[item.category_id]?.replaced?.some((entry) => entry.by === item.id));
/** Left out of the results as its own score: dropped, or replaced by a higher replacement score. */
const leftOut = (view, item, student) => droppedIn(view, item, student) || Boolean(replacedBy(view, item, student));
// Points above what a column is worth (a 0-point extra-credit column has no "above": every score there is extra).
const above = (item, points) => Number(item.points) > 0 && Number(points) > Number(item.points);
const moment = (value) => { const time = value ? Date.parse(value) : NaN; return Number.isNaN(time) ? null : time; };
const blank = (cell) => !cell || cell.status === "cleared";
const drBooked = (item, student) => Boolean(item.assignment_id && student.dr?.includes(item.assignment_id));

/** GRADEBOOK "ZERO ONCE DUE" (`ungraded: "due"`): the server's word on a student's empty cell, `results.blanks` (its campus clock,
 * certifications, makeups, DR bookings and papers): "zero" (counts as 0), why it waits ("makeup", "if_excused", "dr", "paper"),
 * or null (not due, or another policy). */
export const blankReason = (view, item, student) => view?.results?.blanks?.[student.row]?.[item.id] || null;
/** Whether the server counts the column's empty cells as 0 only because its grades are posted in Canvas (`results.due_posted`). */
export const duePosted = (view, item) => Boolean(view?.results?.due_posted?.includes(item.id));
/** An empty cell's words for its reason: [accessible words, hover line]. */
export function blankWords(item, reason, posted = false) {
  // `posted`: the server's `results.due_posted` names the column (its grades are posted in Canvas; GRADEBOOK ZERO ONCE DUE, POSTED).
  if (reason === "zero") return ["no score, counts as 0", `No score · counts as 0 (${posted ? "its grades are posted in Canvas" : item.link?.kind === "grading" ? "its grading is certified" : `due ${shortDate(item.due_date) || "date passed"}`})`];
  if (reason === "makeup") return ["no score, makeup owed", "No score · makeup owed: not counted until it is graded"];
  if (reason === "if_excused") return ["no score, absence waiting for your decision", "No score · an absence waiting for your decision covers it: not counted until you decide"];
  if (reason === "paper") return ["no score, paper in", "No score · the paper came in and is not graded yet: not counted"];
  if (reason === "dr") return ["no score, DR-center booking", "No score · DR-center booking for this assignment"];
  return null;
}

/** Whether grading changed after a linked column's last pull (the View's read-only `links[item].grading_changed_at`), or has
 * something recorded and was never pulled. Only a hint on the Pull icon: nothing is pulled by it. */
export function gradingChanged(link) {
  const changed = moment(link?.grading_changed_at);
  if (changed === null) return false;
  const pulled = moment(link?.last_pull?.at);
  return pulled === null || changed > pulled;
}

/** The key of one cell in `differences()` and, once the term is stripped from `pendingKey`, in the pending and unsure sets: "<item>\0<row>". */
const cellKey = (item, row) => `${item}\u0000${row}`;

/**
 * DIFFERS FROM GRADING (GRADEBOOK, 2026-10-07): the grading check's rows (`GET …/grading-check`, gradebook/grading_check.py) that
 * still describe the cells on screen, as a Map "<item>\0<row>" -> {item, row, section_id, kind, cell, grading, fix, certified_at,
 * plan_id}. A row whose cell changed since the check (another revision, filled or emptied) is left out until the next check reads
 * it again: a mark never outlives the value it was about.
 */
export function differences(view, check) {
  const found = new Map();
  for (const [item, column] of Object.entries(check?.columns || {})) {
    for (const entry of column?.rows || []) {
      const cell = view?.cells?.[item]?.[entry.row];
      const now = cell && cell.status !== "cleared" ? cell.id : null;
      const then = entry.cell && entry.cell.status !== "cleared" ? entry.cell.id : null;
      if (now !== then) continue;
      found.set(cellKey(item, entry.row), { ...entry, item, fix: column.fix || null, certified_at: column.certified_at || null, plan_id: column.plan_id || null });
    }
  }
  return found;
}

/** What grading has for a cell, in the grid's words: points (two decimals at most), EX, MI, or "no value" (left out). */
export function gradingValue(value) {
  if (!value || value.status === "cleared") return "no value";
  if (value.status === "excused") return "EX";
  if (value.status === "missing") return "MI";
  return shownPoints(trimPoints(value.points)).text;
}

/** A differing cell's lines for its title: what grading has, then how the cell is brought in line. */
export function differsLines(entry, format = (value) => String(value || "")) {
  const grading = gradingValue(entry?.grading);
  if (entry?.kind === "typed") {
    return [`Differs from Grading, which has ${grading}`, entry.cell?.status === "cleared"
      ? "You cleared this cell: a pull or a certification leaves it empty unless you tick Replace"
      : "You typed this value: a pull or a certification keeps it unless you tick Replace"];
  }
  if (entry?.fix === "certify") {
    return [`Differs from Grading, which has ${grading} now`, `Grading changed after you certified it${entry.certified_at ? ` on ${format(entry.certified_at)}` : ""}: certify again in Grading to record it`];
  }
  return [`Differs from Grading, which has ${grading}`, "Pull this column to bring it in"];
}

/**
 * The differences notice: one line per column that differs, in the setup's order. `marks` is differences(); `students` the grid's
 * (one section, or all). A column's rows of students not shown, and the server's `elsewhere` (a section tab's other sections), are
 * counted as "in other sections". [{item, title, text, fix, plan_id, first, count, changed}]: `first` is the first differing row
 * in the grid's order, `count` how many differ on screen and `changed` how many of those a pull or a certification would change.
 */
export function differsSummary(view, check, marks, students, format = (value) => String(value || "")) {
  const order = (students || []).map((student) => student.row), shown = new Set(order);
  const lines = [];
  for (const item of view?.setup?.items || []) {
    const column = check?.columns?.[item.id];
    if (!column || item.archived) continue;
    const mine = [...(marks || new Map()).values()].filter((entry) => entry.item === item.id && shown.has(entry.row));
    const changed = mine.filter((entry) => entry.kind === "changed").length, typed = mine.length - changed;
    const elsewhere = (column.elsewhere?.changed || 0) + (column.elsewhere?.typed || 0) + (column.rows || []).filter((entry) => !shown.has(entry.row)).length;
    if (!mine.length && !elsewhere) continue;
    const parts = [];
    if (changed) {
      parts.push(column.fix === "certify" ? `${plural(changed, "grade")} changed in Grading after you certified it${column.certified_at ? ` on ${format(column.certified_at)}` : ""}`
        : `${plural(changed, "grade")} ${changed === 1 ? "differs" : "differ"} from Grading`);
    }
    if (typed) parts.push(`${plural(typed, "value")} you typed ${typed === 1 ? "differs" : "differ"} from Grading`);
    if (elsewhere) parts.push(`${elsewhere} ${mine.length ? "more " : ""}in ${elsewhere === 1 ? "another section" : "other sections"}`);
    const rows = new Set(mine.map((entry) => entry.row));
    lines.push({ item: item.id, title: item.title, text: parts.join(" · "), fix: column.fix || null, plan_id: column.plan_id || null,
      first: order.find((row) => rows.has(row)) || "", count: mine.length, changed });
  }
  return lines;
}

/** How many students of `students` have each letter of the scale (scale order), from the server's results. */
export function letterCounts(view, students) {
  const counts = new Map((view?.setup?.scale || []).map((entry) => [entry.letter, 0]));
  for (const student of students) {
    const letter = view?.results?.students?.[student.row]?.letter;
    if (letter && counts.has(letter)) counts.set(letter, counts.get(letter) + 1);
  }
  return [...counts].map(([letter, count]) => ({ letter, count }));
}

/**
 * The blanks past due: {row: [item IDs]} for the live columns of `sectionId` whose due date is before `today` (YYYY-MM-DD) and
 * that count (not archived, counted, not extra credit), where the student has no value, the column applies to their section
 * and they have no DR-center booking for its assignment. `total` counts the cells. A view only: nothing is marked missing.
 * Under `ungraded: "due"` (GRADEBOOK "ZERO ONCE DUE") they are exactly the empty cells the server counts as 0 (`blankReason`).
 */
export function pastDue(view, students, today, sectionId = "") {
  const once = view?.setup?.ungraded === "due";
  const items = columnBands(view, sectionId).flatMap((band) => band.items)
    .filter((item) => once || (/^\d{4}-\d{2}-\d{2}$/.test(item.due_date || "") && item.due_date < today && item.counts !== false && !item.extra_credit));
  const rows = new Map();
  let total = 0;
  for (const student of students) {
    const due = items.filter((item) => applies(item, student) && blank(view.cells?.[item.id]?.[student.row])
      && (once ? blankReason(view, item, student) === "zero" : !drBooked(item, student))).map((item) => item.id);
    if (due.length) { rows.set(student.row, due); total += due.length; }
  }
  return { rows, total };
}

/**
 * Whether a student passes the grid's attention filter (view memory): `{kind: "letter", value}` those with that letter,
 * `{kind: "below", value}` those whose letter comes after it in the scale, `{kind: "overdue"}` those with a blank past due
 * (`overdue` from pastDue), `{kind: "differs"}` those with a cell that differs from grading (`differ`: a Set of their rows). No
 * filter passes everyone; a student without a letter passes no letter filter.
 */
export function attentionMatch(view, student, filter, overdue = null, differ = null) {
  if (!filter?.kind) return true;
  if (filter.kind === "overdue") return Boolean(overdue?.rows.has(student.row));
  if (filter.kind === "differs") return Boolean(differ?.has(student.row)); // DIFFERS FROM GRADING: rows with a differing cell
  const letter = view?.results?.students?.[student.row]?.letter;
  if (!letter) return false;
  if (filter.kind === "letter") return letter === filter.value;
  if (filter.kind === "below") {
    const scale = (view?.setup?.scale || []).map((entry) => entry.letter);
    const at = scale.indexOf(filter.value), mine = scale.indexOf(letter);
    return at >= 0 && mine > at;
  }
  return true;
}

// A paste larger than this, or one that replaces or clears a value already there, asks first.
export const PASTE_CONFIRM = 50;
/** Whether a paste of `count` cells, `replaced` of which already hold a value, asks before writing. */
export const pasteNeedsConfirm = (count, replaced) => count > PASTE_CONFIRM || replaced > 0;

/** A column's empty cells among `students` (it applies to them, no value): [{row, name, section, dr}], DR-booked flagged. Students
 * dropped from the roster are never filled in bulk (their row stays for the records they have). */
export function emptyCells(view, item, students) {
  if (canvasGraded(item)) return []; // Canvas's scores (WebAssign's, a Canvas quiz's) come from Canvas only (SYNC WITH CANVAS)
  return students.filter((student) => !student.dropped && applies(item, student) && blank(view.cells?.[item.id]?.[student.row]))
    .map((student) => ({ row: student.row, name: student.name, section: student.section, dr: drBooked(item, student) }));
}

/**
 * The visible columns grouped into category bands, in setup order, with Uncategorized last. Archived columns are hidden;
 * with a section filter, so are columns that do not apply to that section.
 */
export function columnBands(view, sectionId = "") {
  const setup = view?.setup || {};
  const items = (setup.items || []).filter((item) => !item.archived && (!sectionId || !item.sections?.length || item.sections.includes(sectionId)));
  const categories = setup.categories || [];
  const known = new Set(categories.map((category) => category.id));
  const bands = [];
  for (const category of categories) {
    const list = items.filter((item) => item.category_id === category.id);
    if (list.length) bands.push({ category, items: list });
  }
  const rest = items.filter((item) => !item.category_id || !known.has(item.category_id));
  if (rest.length) bands.push({ category: null, items: rest });
  return bands;
}

function sortValue(view, student, sort) {
  const result = view.results?.students?.[student.row];
  const number = (value) => (value === null || value === undefined || value === "" ? null : Number(value));
  if (sort.key === "overall") return [number(result?.percent), 0];
  if (sort.key === "cat") return [number(result?.categories?.[sort.id]?.percent), 0];
  if (sort.key === "item") {
    const cell = view.cells?.[sort.id]?.[student.row];
    if (cell?.status === "scored") return [Number(cell.points), 0];
    if (cell?.status === "missing") return [0, 0];
    return [null, cell?.status === "excused" ? 1 : 2]; // excused, then empty, always after the values
  }
  return [null, 0];
}

/** Students in display order. `sort` is {key: name|section|overall|cat|item, id?, dir: asc|desc}; blanks sort last. */
export function sortRows(view, students, sort = { key: "name", dir: "asc" }) {
  const order = new Map((view.students || []).map((student, index) => [student.row, index]));
  const base = (a, b) => (order.get(a.row) ?? 0) - (order.get(b.row) ?? 0);
  const dir = sort.dir === "desc" ? -1 : 1;
  if (sort.key === "name") return [...students].sort((a, b) => dir * base(a, b));
  if (sort.key === "section") return [...students].sort((a, b) => dir * (Number(a.section) - Number(b.section)) || base(a, b));
  const values = new Map(students.map((student) => [student.row, sortValue(view, student, sort)]));
  return [...students].sort((a, b) => {
    const [x, xr] = values.get(a.row), [y, yr] = values.get(b.row);
    if (x === null || y === null) return x === null && y === null ? xr - yr || base(a, b) : x === null ? 1 : -1;
    return dir * (x - y) || base(a, b);
  });
}

/**
 * Everything the grid shows, in order: bands, flat columns, result columns and the filtered, sorted students. A category in
 * `collapsed` (view memory only) shows as ONE read-only column of its percentage, `{id: "band:<category>", collapsed: true,
 * category, title, hidden}`, unless it holds the column a link asked for. `columnCount` counts the real columns, hidden ones too.
 */
export function gridModel(view, { sectionId = "", sort = { key: "name", dir: "asc" }, highlight = "", collapsed = new Set() } = {}) {
  const bands = columnBands(view, sectionId).map((band) => {
    const category = band.category;
    if (!category || !collapsed.has(category.id) || band.items.some((item) => item.id === highlight)) return band;
    const hidden = band.items.length;
    return { ...band, collapsed: true, hidden, items: [{ id: `band:${category.id}`, collapsed: true, category, title: `${category.name} %`, hidden }] };
  });
  const items = bands.flatMap((band) => band.items);
  const columnCount = bands.reduce((sum, band) => sum + (band.collapsed ? band.hidden : band.items.length), 0);
  const categories = view?.setup?.categories || [];
  const results = [
    ...categories.map((category) => ({ key: `cat:${category.id}`, kind: "cat", id: category.id, label: `${category.name} %`, name: category.name })),
    { key: "overall", kind: "overall", label: "Overall" },
    { key: "letter", kind: "letter", label: "Letter" },
  ];
  const students = sortRows(view, (view?.students || []).filter((student) => !sectionId || student.section_id === sectionId), sort);
  return { bands, items, results, students, sectionId, sort, highlight, columnCount, weighted: view?.setup?.mode === "weighted", hasBands: categories.length > 0 };
}

// The key's marks, in the order the key lists them: [mark, a word or two, what it means]. Only those on screen are shown.
const MARKS = [
  ["linked", "Linked", "A column linked to grading: its Pull icon previews the final grades"],
  ["changed", "Grading changed", "Grading changed since the column’s last pull (or it was never pulled)"],
  ["differs", "Differs from Grading", "Differs from Grading’s final grade now: hover the cell for what Grading has"],
  ["grading", "Pulled", "Pulled from grading"],
  ["import", "Imported", "Imported from a file, Canvas or iClicker"],
  ["instructor", "Typed", "Typed in Faculty"],
  ["note", "Note", "The score has a note"],
  ["missing", "MI missing", "Missing: counts as 0"],
  ["zeroed", "0 no score", "No score and past due (or its grading certified): counts as 0"],
  ["excused", "EX excused", "Excused: not counted"],
  ["makeup", "MU makeup", "No score and a makeup owed, or an absence waiting for your decision: not counted yet"],
  ["paper", "IN paper in", "No score; the paper came in and is not graded yet: not counted yet"],
  ["dropped", "Dropped", "Dropped: the lowest in its category, left out of the results"],
  ["replaced", "Replaced", "Replaced: a higher replacement score takes its place"],
  ["over", "Over", "More than the column’s points"],
  ["dr", "DR booking", "No score and a DR-center booking for this assignment"],
  ["na", "Not assigned", "The column is not assigned to the student’s section"],
  ["overdue", "Past due", "No value and past its due date (shown while that filter is on)"],
];

/** Which of the key's marks the grid shows now (`model` from gridModel): the key lists only these. `overdue`: the past-due
 * blanks are highlighted (their filter is on); `differs`: differences() of the grading check. */
export function marksPresent(view, model, { overdue = false, differs = null } = {}) {
  const found = new Set(overdue ? ["overdue"] : []);
  for (const item of model.items) {
    if (item.collapsed) continue;
    if (differs?.size && model.students.some((student) => differs.has(cellKey(item.id, student.row)))) found.add("differs");
    if (item.link?.kind === "grading") found.add("linked");
    if (item.link?.kind === "grading" && gradingChanged(view.links?.[item.id])) found.add("changed");
    const column = view.cells?.[item.id] || {};
    for (const student of model.students) {
      if (!applies(item, student)) { found.add("na"); continue; }
      const cell = column[student.row];
      if (!cell || cell.status === "cleared") {
        const reason = blankReason(view, item, student);
        if (reason === "zero") found.add("zeroed");
        else if (reason === "makeup" || reason === "if_excused") found.add("makeup");
        else if (reason === "paper") found.add("paper");
        else if (reason === "dr" || drBooked(item, student)) found.add("dr");
        continue;
      }
      if (SOURCES[cell.source]) found.add(cell.source);
      if (cell.note) found.add("note");
      if (cell.status === "missing" || cell.status === "excused") found.add(cell.status);
      else if (cell.status === "scored" && above(item, cell.points)) found.add("over");
      if (droppedIn(view, item, student)) found.add("dropped");
      if (replacedBy(view, item, student)) found.add("replaced");
    }
  }
  return MARKS.filter(([mark]) => found.has(mark)).map(([mark]) => mark);
}

/** The one-line key under the toolbar: a swatch and a few words per mark present, nothing when the grid has none. */
export function keyMarkup(marks, esc = plain) {
  const words = new Map(MARKS.map(([mark, short, long]) => [mark, [short, long]]));
  return marks.length ? `<span class="sr-only">Key: </span>${marks.map((mark) => {
    const [short, long] = words.get(mark) || [mark, mark];
    return `<span class="gb-key" data-mark="${esc(mark)}" title="${esc(long)}"><i aria-hidden="true"></i><span aria-hidden="true">${esc(short)}</span><span class="sr-only">${esc(long)}.</span></span>`;
  }).join("")}` : "";
}

/** A column Canvas grades (SYNC WITH CANVAS: WebAssign's through Canvas, or a Canvas quiz): read-only in Faculty. */
export const canvasGraded = (item) => item?.canvas === "webassign" || item?.canvas === "quiz";

/** One score cell's class and inner markup (shared by the full render and the in-place update after a write). */
export function cellParts(item, student, cell, { pending = null, target = false, esc = plain, dropped = false, uncertain = false, overdue = false, reason = null, differs = false } = {}) {
  if (!applies(item, student)) return { cls: `gb-cell gb-na${target ? " is-target" : ""}`, html: "—", na: true };
  // SYNC WITH CANVAS (requirement, 2026-10-06: Canvas always wins for WebAssign and Canvas quizzes): read-only, its scores come from Canvas.
  const locked = canvasGraded(item);
  const shown = pending || cell;
  const status = shown && shown.status !== "cleared" ? shown.status : "";
  let cls = "gb-cell", html = "";
  if (status === "scored") {
    const points = trimPoints(shown.points), view = shownPoints(points);
    html = view.text === view.full ? esc(points) : `<span title="${esc(view.full)}">${esc(view.text)}</span>`;
    cls += " st-scored";
    if (above(item, points)) cls += " is-over";
  } else if (status === "excused") {
    html = "EX";
    cls += " st-excused";
  } else if (status === "missing") {
    html = '0 <span class="gb-tag">MI</span>';
    cls += " st-missing";
  } else if (!pending && reason === "zero") { // ZERO ONCE DUE: empty, counted as 0 (`reason`: blankReason)
    html = "0";
    cls += " is-zeroed";
  } else if (!pending && (reason === "makeup" || reason === "if_excused" || reason === "paper")) {
    html = `<span class="gb-dr">${reason === "paper" ? "IN" : "MU"}</span>`;
  } else if (reason === "dr" || drBooked(item, student)) {
    html = '<span class="gb-dr">DR</span>';
  }
  if (pending) cls += " is-pending";
  else if (cell && cell.status !== "cleared") {
    cls += ` src-${esc(cell.source)}`; // the source dot is drawn by gradebook.css
    if (cell.note) cls += " has-note";
    if (dropped && status) cls += " is-dropped"; // dimmed and struck: the server's results leave it out
  }
  if (uncertain && !pending) cls += " is-uncertain"; // a write whose outcome is unknown (the notice's Check now)
  if (overdue && !status && !pending) cls += " is-overdue"; // a blank past due, while that filter is on
  if (differs && !pending) cls += " is-differs"; // DIFFERS FROM GRADING: grading's final grade is not what the cell holds
  if (target) cls += " is-target";
  if (locked) cls += " is-locked";
  return { cls, html, na: false, locked };
}

function resultValue(view, student, result) {
  const row = view.results?.students?.[student.row];
  if (result.kind === "overall") return percentText(row?.percent);
  if (result.kind === "letter") return row?.letter || "—";
  return percentText(row?.categories?.[result.id]?.percent);
}

// A linked column's heading: a thin blue rule on top (gradebook.css, th.gb-linked) and a small Pull icon; what it pulls from
// and its last pull are in the icon's tooltip and in the column menu, so the heading stays one line of words.
function pullButton(item, link, esc, icon) {
  if (item.link?.kind !== "grading") return "";
  const names = (item.link.parts || []).map((part) => PART_NAMES[part] || part).join(", ");
  const last = link?.last_pull;
  // A dot when grading changed after the last pull (read-only catalog timestamps): a reason to look, never a pull by itself.
  const changed = gradingChanged(link);
  const news = changed ? (last ? `Grading changed since then (${when(link.grading_changed_at)}).` : "Grading has recorded grades since the column was added.") : "";
  // A certified assignment pulls its latest certification (Certification and Export in Grading), never live grading.
  const certified = link?.certification;
  const from = certified ? `Pull the certification of ${when(certified.created_at)} (${names || "grading"}).` : `Pull from ${names || "grading"}.`;
  const title = [from, last ? `Last pulled ${when(last.at)}${last.actor ? ` by ${actorText(last.actor)}` : ""}.` : "Not pulled yet.", news,
    certified?.may_have_changed ? "Grades may have changed in Grading since; certify again there to record them." : ""].filter(Boolean).join(" ");
  return `<button type="button" class="gb-pull${changed ? " is-changed" : ""}" data-pull="${esc(item.id)}" tabindex="-1" aria-label="Pull ${esc(item.title)} from grading"${changed ? ` aria-description="${esc(news)}"` : ""} title="${esc(title)}">${icon ? icon("download") : "Pull"}</button>`;
}

/** A column heading's meta line: points and due date, then "not counted" / "extra credit" when they apply. `uncounted`: the
 * server's `results.uncounted` names it (a counted column with no category in a weighted gradebook counts toward nothing). */
export function headMeta(item, uncounted = false) {
  return [`${trimPoints(item.points)} pts`, shortDate(item.due_date), item.counts === false ? "not counted" : uncounted ? "not counted (no category)" : "", item.extra_credit ? "extra credit" : "", item.replaces_lowest ? "replaces lowest" : ""].filter(Boolean).join(" · ");
}

/** POSTING GRADES: a column heading's ink chip, how many of its grades Canvas hid from students when Faculty last read it
 * (`view.canvas_posting`; none: nothing). A line of its own: on the meta line a 128px column cut it to "10 pts · 336…"
 * (requirement, 2026-10-07). */
export function hiddenChip(hidden) {
  return hidden > 0 ? `<span class="gb-head-hidden" data-head-hidden>${hidden} hidden</span>` : "";
}

/** POSTING GRADES (requirement, 2026-10-08): a column heading's ink chip saying what the next sync, the instructor's or the morning one, does about the column's posting
 * in Canvas (`view.canvas_posting[item].pending`, from the server; none: nothing). Ink, not orange: it is the instructor's own rule
 * or choice at work, not something that needs them. */
export function pendingChip(mark) {
  const pending = mark?.pending || [];
  const text = pending.includes("hide") ? "hides at sync" : pending.includes("post") ? "posts at sync" : pending.includes("switch") ? "by hand at sync" : "";
  return text ? `<span class="gb-head-pending" data-head-pending>${text}</span>` : "";
}

const POSTING_SOURCES = { sync: "Sync with Canvas", nightly: "the morning sync", post: "a post", hide: "a hide" };
const POSTING_RULES = { due: "after its due date", certified: "when Faculty certifies it" };
const POSTING_FORCED = { archived: "it is archived", "not counted": "it does not count", "no category": "it has no category", "weight 0": "its category weighs 0%" };

/** POSTING GRADES: the posting rule at work on a column (`mark.policy`, the server's), in a few words. */
export function postingRule(mark) {
  const policy = mark?.policy;
  if (!policy) return "";
  if (policy.why === "choice") return policy.state === "posted" ? "you chose to post it" : "you chose to hide it";
  if (policy.why === "iclicker") return "iClicker's columns post automatically";
  if (policy.why) return `its category posts it ${POSTING_RULES[policy.why]}`;
  if (policy.forced) return `always posted by hand: ${POSTING_FORCED[policy.forced] || policy.forced}`;
  if (policy.rules?.length) return `its category posts it ${policy.rules.map((rule) => POSTING_RULES[rule]).join(" or ")}; not yet`;
  return "its category posts by hand";
}

/** POSTING GRADES: the heading's words for a column's posting in Canvas (what Faculty last read, its rule, what the next sync does),
 * or "" when there is nothing to say. */
export function postingTitle(mark) {
  if (!mark) return "";
  const pending = mark.pending || [];
  const next = pending.includes("hide") ? "the next sync hides its grades in Canvas" : pending.includes("post") ? "the next sync posts its hidden grades in Canvas" : "";
  const parts = [mark.hidden > 0 ? `${plural(mark.hidden, "grade")} hidden from students in Canvas when Faculty last read it (${when(mark.read_at)}, ${POSTING_SOURCES[mark.source] || "Canvas"})` : "",
    postingRule(mark), next, pending.includes("switch") ? "the next sync sets its Canvas assignment to post by hand" : ""].filter(Boolean);
  return parts.length && (mark.hidden > 0 || mark.choice || pending.length) ? parts.join("; ") : "";
}

/** The columns the server says count toward nothing (`results.uncounted`: counted, not archived, no category, weighted mode),
 * in the setup's order: [{id, title}]. The server names them term-wide; with `sectionId` only those that apply to that section
 * are kept (columnBands' rule), so a section's notice never names a column its grid does not show. */
export function uncountedItems(view, sectionId = "") {
  const ids = new Set(Array.isArray(view?.results?.uncounted) ? view.results.uncounted : []);
  return ids.size ? (view.setup?.items || []).filter((item) => ids.has(item.id) && (!sectionId || !item.sections?.length || item.sections.includes(sectionId)))
    .map((item) => ({ id: item.id, title: item.title })) : [];
}

/** How many of `students` have a cell of column `itemId` that differs from grading (`differs`: differences()). */
export function differsCount(differs, itemId, students) {
  if (!differs?.size) return 0;
  return (students || []).reduce((count, student) => count + (differs.has(cellKey(itemId, student.row)) ? 1 : 0), 0);
}

/** A column heading's orange needs-you chip: how many of its cells on screen differ from grading (none: nothing). */
export function differsChip(count) {
  return count ? `<span class="gb-head-differs" data-head-differs>${count} ${count === 1 ? "differs" : "differ"}</span>` : "";
}

/** The whole table as one string: two header rows (category bands, then columns), then one row per student. */
export function gridMarkup(view, model, { esc = plain, pending = new Map(), termLabel = "", freeze = 1, icon = null, unsure = new Set(), overdue = null, differs = new Map() } = {}) {
  const itemStats = view.results?.items || {};
  const uncounted = new Set(Array.isArray(view.results?.uncounted) ? view.results.uncounted : []);
  const sorted = (key, id = "") => (model.sort.key === key && (model.sort.id || "") === id ? (model.sort.dir === "desc" ? "descending" : "ascending") : "");
  const ariaSort = (value) => (value ? ` aria-sort="${value}"` : "");
  const nameSort = sorted("name") || sorted("section");
  // The corner's sort buttons are part of the grid's single tab stop: Left from the first heading reaches them.
  const corner = `<th scope="col" class="gb-corner"${model.hasBands ? ' rowspan="2"' : ""}${ariaSort(nameSort)}><span class="gb-corner-label">Student</span><span class="gb-corner-sorts"><button type="button" class="gb-sort" data-sort="name" data-corner="0" tabindex="-1" aria-pressed="${model.sort.key === "name"}" title="Sort by name">Name</button><button type="button" class="gb-sort" data-sort="section" data-corner="1" tabindex="-1" aria-pressed="${model.sort.key === "section"}" title="Sort by section">Section</button></span></th>`;
  const itemHead = (item, index) => {
    if (item.collapsed) {
      const id = item.category.id;
      return `<th scope="col" class="gb-head gb-collapsed-head" data-col="${index}" data-band="${esc(id)}"><button type="button" class="gb-head-button" data-band-toggle="${esc(id)}" tabindex="-1" aria-expanded="false" title="${esc(`Show the ${plural(item.hidden, "column")} of ${item.category.name}`)}"><span class="gb-head-title">${esc(item.title)}</span><span class="gb-head-meta">${esc(`${plural(item.hidden, "column")} hidden`)}</span><span class="gb-head-mean" data-head-mean>${esc(resultMean(view, { kind: "cat", id }))}</span></button></th>`;
    }
    const stats = itemStats[item.id] || {};
    const linked = item.link?.kind === "grading";
    const mean = stats.mean !== null && stats.mean !== undefined ? `avg ${shownPoints(stats.mean).text}` : "avg —";
    const mark = view.canvas_posting?.[item.id];
    const title = [item.title, `${trimPoints(item.points)} points`, item.due_date ? `due ${item.due_date}` : "", linked ? "linked to grading" : "", postingTitle(mark), item.notes || "", "Column options"].filter(Boolean).join(" · ");
    const differing = differsCount(differs, item.id, model.students);
    const cls = `gb-head${linked ? " gb-linked" : ""}${differing ? " gb-differs" : ""}${model.highlight === item.id ? " is-target" : ""}`;
    return `<th scope="col" class="${cls}" data-col="${index}" data-item="${esc(item.id)}"${ariaSort(sorted("item", item.id))}><button type="button" class="gb-head-button" data-column-menu="${esc(item.id)}" tabindex="-1" aria-haspopup="dialog" aria-expanded="false" aria-controls="gradebook-column-menu" title="${esc(title)}"><span class="gb-head-title">${esc(item.title)}</span><span class="gb-head-meta">${esc(headMeta(item, uncounted.has(item.id)))}</span><span class="gb-head-mean" data-head-mean>${esc(mean)}</span>${differsChip(differing)}${hiddenChip(mark?.hidden || 0)}${pendingChip(mark)}</button>${pullButton(item, view.links?.[item.id], esc, icon)}</th>`;
  };
  const resultHead = (result, index) => {
    const col = model.items.length + index;
    const sortKey = result.kind === "cat" ? `cat:${result.id}` : "overall";
    return `<th scope="col" class="gb-head gb-result-head ${resultClass(model, result, index)}" data-col="${col}" data-result="${esc(result.key)}"${ariaSort(result.kind === "cat" ? sorted("cat", result.id) : result.kind === "overall" ? sorted("overall") : "")}><button type="button" class="gb-head-button" data-sort="${esc(sortKey)}" tabindex="-1" title="${esc(`Sort by ${result.kind === "letter" ? "overall percentage" : result.label}`)}"><span class="gb-head-title">${esc(result.label)}</span><span class="gb-head-mean" data-head-mean>${esc(resultMean(view, result))}</span></button></th>`;
  };
  // Results have two bands: the category percentages (frozen with Overall and Letter only when they fit, see fitResults)
  // and Overall with Letter, frozen whenever the grid has room, so no column band ever passes above the frozen headings.
  const categoryCount = model.results.length - 2;
  const bandRow = model.hasBands
    ? `<tr class="gb-bands">${corner}${model.bands.map((band) => {
        const category = band.category;
        const label = category ? `${category.name}${model.weighted ? ` · ${trimPoints(category.weight)}%` : ""}${category.drop_lowest ? ` · drop ${category.drop_lowest}` : ""}` : `Uncategorized${model.weighted ? " · not counted" : ""}`;
        // A category collapses to its percentage (view memory only). The toggle's glyph is drawn by gradebook.css, so the
        // band's text stays its label; it is out of the tab order (the grid is one tab stop): the column menu and the
        // collapsed column's own heading do the same from the keyboard.
        const toggle = category ? `<button type="button" class="gb-band-toggle" data-band-toggle="${esc(category.id)}" tabindex="-1" aria-expanded="${band.collapsed ? "false" : "true"}" aria-label="${esc(band.collapsed ? `Show the columns of ${category.name}` : `Collapse ${category.name} to its percentage`)}"></button>` : "";
        return `<th scope="colgroup" colspan="${band.items.length}" class="gb-band${band.collapsed ? " is-collapsed" : ""}"${category ? ` data-category="${esc(category.id)}"` : ' data-category=""'} title="${esc(label)}">${toggle}<span>${esc(label)}</span></th>`;
      }).join("")}${categoryCount > 0 ? `<th scope="colgroup" colspan="${categoryCount}" class="gb-band gb-band-cats gb-r-cat gb-fr-2 gb-cat-first" title="Category results"><span>${categoryCount === 1 ? "Category" : "Categories"}</span></th>` : ""}<th scope="colgroup" colspan="2" class="gb-band gb-band-results gb-sticky-right" title="Overall percentage and letter"><span>Results</span></th></tr>`
    : "";
  const headRow = `<tr class="gb-heads">${model.hasBands ? "" : corner}${model.items.map(itemHead).join("")}${model.results.map(resultHead).join("")}</tr>`;
  const itemIds = model.items.map((item) => esc(item.id));
  const rows = model.students.map((student) => {
    const rowAttr = esc(student.row);
    const accommodationText = `Accommodations: ${(student.accommodations || []).join("; ")}`;
    const accommodations = (student.accommodations?.length ? `<span class="gb-badge gb-acc" title="${esc(accommodationText)}"><span aria-hidden="true">ACC</span><span class="sr-only">${esc(accommodationText)}</span></span>` : "")
      + (student.dropped ? '<span class="gb-badge gb-acc" title="Dropped from the roster">DROPPED</span>' : "");
    const cells = model.items.map((item, index) => {
      if (item.collapsed) return `<td class="gb-result gb-collapsed-cell" data-result-cell="${esc(item.id)}" tabindex="-1" aria-readonly="true">${esc(resultValue(view, student, { kind: "cat", id: item.category.id }))}</td>`;
      const key = cellKey(item.id, student.row);
      const parts = cellParts(item, student, view.cells?.[item.id]?.[student.row], { pending: pending.get(key) || null, target: model.highlight === item.id, esc,
        dropped: leftOut(view, item, student), uncertain: unsure.has(key), overdue: Boolean(overdue?.rows.get(student.row)?.includes(item.id)),
        reason: blankReason(view, item, student), differs: differs.has(key) });
      return `<td class="${parts.cls}" data-cell data-item="${itemIds[index]}" data-row="${rowAttr}" tabindex="-1"${parts.na ? ' data-na aria-readonly="true"' : parts.locked ? ` data-locked aria-readonly="true" title="${item.canvas === "quiz" ? "A Canvas quiz’s score: change it in Canvas" : "From WebAssign through Canvas: change it in WebAssign"}"` : ""}>${parts.html}</td>`;
    }).join("");
    const results = model.results.map((result, index) => `<td class="gb-result ${resultClass(model, result, index)}" data-result-cell="${esc(result.key)}" tabindex="-1" aria-readonly="true">${esc(resultValue(view, student, result))}</td>`).join("");
    // The name opens the student's record (a dialog of what the grid holds for them); Left from the first column reaches it.
    return `<tr data-row="${rowAttr}" data-section="${esc(student.section_id)}"><th scope="row" class="gb-name"><button type="button" class="gb-name-button" data-student="${rowAttr}" tabindex="-1" aria-haspopup="dialog"><span class="gb-name-text" title="${esc(student.name)}">${esc(student.name)}</span><span class="sr-only">: open the record</span></button><span class="gb-name-meta"><span class="gb-sec">Sec ${esc(student.section)}</span>${accommodations}</span></th>${cells}${results}</tr>`;
  }).join("");
  const cols = `<colgroup><col data-w="name">${model.items.map((item) => `<col data-w="${item.collapsed ? "result" : "item"}">`).join("")}${model.results.map((result) => `<col data-w="${result.kind === "letter" ? "letter" : "result"}">`).join("")}</colgroup>`;
  const caption = `<caption class="sr-only">Scores${termLabel ? ` for ${esc(termLabel)}` : ""}: ${plural(model.students.length, "student")}, ${plural(model.columnCount ?? model.items.length, "column")}. Arrow keys move between cells; Enter or typing edits; EX excused, MI missing; Delete clears; Shift+F10 shows a score's history; Alt+Down opens a column's options.</caption>`;
  return `<table class="gradebook-table${FREEZE_CLASS[freeze] ? ` ${FREEZE_CLASS[freeze]}` : ""}" role="grid" data-gradebook-grid data-assistant-term="${esc(view.term?.id || "")}">${caption}${cols}<thead>${bandRow}${headRow}</thead><tbody>${rows}</tbody></table>`;
}

/**
 * A result column's classes. Overall and Letter are frozen at the right edge (gb-sticky-right) when the grid has room; the
 * category percentages join them when the table is marked gb-freeze-cats. `gb-fr-N` is the column's place counted from the
 * right (Letter 0, Overall 1, the last category 2 …), which gradebook.css turns into its `right` offset.
 */
function resultClass(model, result, index) {
  const place = model.results.length - 1 - index;
  if (result.kind !== "cat") return `gb-r-${result.kind} gb-sticky-right gb-fr-${place}`;
  return `gb-r-cat gb-fr-${place}${index === 0 ? " gb-cat-first" : ""}`;
}

function resultMean(view, result) {
  if (result.kind === "overall") return view.results?.overall?.mean_percent ? `avg ${view.results.overall.mean_percent}%` : "avg —";
  if (result.kind === "letter") return "";
  const mean = view.results?.categories?.[result.id]?.mean_percent;
  return mean ? `avg ${mean}%` : "avg —";
}

/** A request's summary in a line: its file or the columns it wrote (a pull names `item_id`, a scores write `items`), then counts. */
function summaryText(summary, items = []) {
  if (!summary) return "";
  if (typeof summary === "string") return summary;
  if (typeof summary !== "object") return String(summary);
  const ids = [summary.item_id, ...(Array.isArray(summary.items) ? summary.items : [])].filter((id) => typeof id === "string");
  const titles = ids.map((id) => items.find((item) => item.id === id)?.title).filter(Boolean);
  const columns = titles.length > 2 ? `${titles.slice(0, 2).join(", ")} and ${titles.length - 2} more` : titles.join(", ");
  const lead = ["text", "title", "filename", "item_title"].map((key) => summary[key]).find((value) => typeof value === "string" && value) || columns;
  const counts = summary.counts && typeof summary.counts === "object" ? summary.counts : summary;
  const numbers = Object.entries(counts).filter(([, value]) => typeof value === "number" && value).slice(0, 5).map(([key, value]) => `${value} ${key.replace(/_/g, " ")}`).join(" · ");
  return [lead, numbers].filter(Boolean).join(" · ");
}

/** @param {{api: GradebookContext["api"], escape: GradebookContext["escape"], icon: GradebookContext["icon"], toast: GradebookContext["toast"], showDialog: GradebookContext["showDialog"], dialogHeader: GradebookContext["dialogHeader"], header?: any}} deps
 *   `header` is the shell's page header (page-header.js): the grid and setup pages name their term there. */
export function createGradebook({ api, escape, icon, toast, showDialog, dialogHeader, header = null, communications = null, absences = null, notes = null, studentSupport = null }) {
  // A course tab's Gradebook view holds its host inside the course page; the earlier #gradebook addresses use the section of their own.
  const root = () => /** @type {HTMLElement|null} */ (COURSE_ROUTE.test(location.hash.replace(/^#/, "").split("?")[0])
    ? document.querySelector("[data-gradebook-host]") : document.querySelector("#gradebook-section"));
  let generation = 0, route = "", ready = false, loading = false;
  /** @type {null | {termId: string, view: GradebookView, ctx: GradebookContext, page: string, section: string, item: string, assignment: string, linked: string, changes: number}} */
  let current = null;
  /** @type {any} the mounted grid (null on every other page) */
  let grid = null;
  const memory = new Map(); // term ID -> {sort, search, collapsed, filter}: browser view state only
  const pending = new Map(); // "<term>\0<item>\0<row>" -> the entry being saved
  // Writes whose outcome is unknown (the connection dropped, the server failed): exact change batch -> {termId, requestId,
  // changes, entries, label}. "Check now" sends the SAME batch with the SAME request ID again, only when the instructor asks,
  // so the server answers with what it did; typing the same change again reuses it too. Never retried by the page itself.
  const uncertain = new Map();
  const unsure = new Set(); // "<term>\0<item>\0<row>" of the cells such a write touched, marked until a later answer
  let writes = Promise.resolve(); // score writes go one at a time, so results always arrive newest last
  let setupModule = null, dialogsModule = null, changesModule = null, setupLoaded = null;
  // DIFFERS FROM GRADING: whether a grading check is in flight, the term to check next, and when the last one answered.
  let checking = false, checkNext = "", checkedAt = 0;

  // Each goes through ui.js loadModule (its "Faculty was updated · Reload" net) and keeps the build prefix of this file. A failed
  // load is forgotten, so the next use tries again.
  const loadSetup = () => (setupModule ||= loadModule(() => import("./gradebook-setup.js")).then((module) => (setupLoaded = module)).catch((error) => { setupModule = null; throw error; }));
  const loadDialogs = () => (dialogsModule ||= loadModule(() => import("./gradebook-dialogs.js")).catch((error) => { dialogsModule = null; throw error; }));
  const loadChanges = () => (changesModule ||= loadModule(() => import("./gradebook-changes.js")).catch((error) => { changesModule = null; throw error; }));
  // Warm them once, in an idle moment after the workspace starts, under the same build as this file: a service restart
  // afterwards (which retires this build's prefix) no longer stops Setup, Changes or a dialog in this tab (FRONTEND.md, loadModule).
  const idle = typeof window.requestIdleCallback === "function"
    ? (/** @type {() => void} */ run) => window.requestIdleCallback(run, { timeout: 5000 })
    : (/** @type {() => void} */ run) => window.setTimeout(run, 1500);
  idle(() => { loadSetup().catch(() => {}); loadDialogs().catch(() => {}); loadChanges().catch(() => {}); });
  const reloadPage = () => location.reload(); // the address (and so this gradebook) is kept
  const pendingKey = (termId, item, row) => `${termId}\u0000${cellKey(item, row)}`;
  const remember = (termId) => {
    if (!memory.has(termId)) memory.set(termId, { sort: { key: "name", dir: "asc" }, search: "", collapsed: new Set(), filter: { kind: "", value: "" } });
    return memory.get(termId);
  };

  window.addEventListener("hashchange", () => {
    if (!isGradebookHash(location.hash)) {
      generation++; ready = false; loading = false; route = "";
      closeMenu();
    }
  });
  // The frozen results depend on the grid's width: measured again after a resize, at most once a frame.
  let resizing = 0;
  window.addEventListener("resize", () => {
    if (resizing || !grid?.table?.isConnected) return;
    resizing = requestAnimationFrame(() => { resizing = 0; if (grid?.table?.isConnected) { fitResults(); fitHeight(); } });
  }, { passive: true });
  // Something above the grid changed height (a disclosure opened or closed, the course page's work list, TAs or Canvas blocks
  // arriving after the grid drew): the page's height changed with it, so the grid measures its top again, at most once a frame.
  // The grid's own new height measures the same top and sets nothing, so this settles at once.
  let refitting = 0;
  if (typeof ResizeObserver === "function") new ResizeObserver(() => {
    if (refitting || !grid?.scroller?.isConnected) return;
    refitting = requestAnimationFrame(() => { refitting = 0; fitHeight(); });
  }).observe(document.body);
  // DIFFERS FROM GRADING: a grade changed in another tab (Grading's own pages) shows here when the window comes back, the check
  // read at most every 30 s that way (each View, write and Refresh reads it anyway).
  const recheck = () => {
    if (document.visibilityState === "visible" && current?.page === "grid" && viewActive(current.termId) && grid?.table?.isConnected
      && Date.now() - checkedAt > 30000) void checkGrading(current.termId);
  };
  document.addEventListener("visibilitychange", recheck);
  window.addEventListener("focus", recheck);

  async function dialog(name, ...args) {
    const owner = current;
    if (!owner) return;
    const wanted = captureDialogIntent(document.getElementById("dialog"));
    let module;
    try { module = await loadDialogs(); } catch {
      toast(UPDATED, { tone: "danger", action: { label: "Reload", run: reloadPage } });
      return;
    }
    if (current !== owner || !viewActive(owner.termId) || !wanted()) return;
    // A dialog keeps the section it opened over, including queued writes and retries after navigation.
    let view = owner.view;
    const section = owner.section;
    const ctx = { ...owner.ctx,
      get view() { return current === owner ? owner.view : view; },
      sectionId: owner.section, scopeSection: section, gridHref: owner.base,
      reload: async () => {
        if (current === owner) return (view = await reload(owner.termId));
        return (view = await fetchView(owner.termId, section));
      },
      replaceView: (next) => { if (!next) return; view = next; if (current === owner) replaceView(owner.termId, next); },
      applyScores: (result) => { if (current === owner) applyScores(owner.termId, result); else if (result) mergeScores(view, result); },
    };
    if (typeof module[name] !== "function") { toast("This gradebook action is not available yet.", true); return; }
    try { await module[name](ctx, ...args); } catch (error) { toast(error?.message || "Something went wrong. Please try again.", true); }
    // A dialog returns focus to what opened it; when a write redrew the grid that element is gone, so the grid's cell in
    // focus takes it instead of the page's body.
    const node = document.getElementById("dialog");
    if (node instanceof HTMLDialogElement && node.open) node.addEventListener("close", () => {
      if ((!document.activeElement || document.activeElement === document.body) && grid?.active?.isConnected && viewActive(ctx.termId)) grid.active.focus({ preventScroll: true });
    }, { once: true });
  }

  function context(termId) {
    return {
      api, escape, icon, toast, showDialog, dialogHeader, termId, communications, absences, notes, studentSupport,
      get view() { return current?.termId === termId ? current.view : null; },
      get sectionId() { return current?.termId === termId ? current.section : ""; },
      // A section tab's gradebook: pulls and imports cover only this section (the server holds them to it).
      get scopeSection() { return current?.termId === termId ? current.section : ""; },
      get gridHref() { return current?.termId === termId ? current.base : `#gradebook/${enc(termId)}`; },
      reload: () => reload(termId),
      replaceView: (view) => replaceView(termId, view),
      applyScores: (result) => applyScores(termId, result),
      requestId: () => crypto.randomUUID(),
    };
  }

  // A section tab reads ITS View: that section's students and statistics over them (2026-09-28); the setup is the term's.
  // `cells=section` (opt-in, PERFORMANCE "OPT-IN AND ADDITIVE API") keeps only that section's cells, about a third of the
  // body of a four-section term; `cell_counts` still gives the setup each column's term-wide score count.
  const scopeOf = (termId) => (current?.termId === termId ? current.section : "");
  const fetchView = (termId, section = scopeOf(termId)) => api(`/api/gradebook/terms/${enc(termId)}${section ? `?section=${enc(section)}&cells=section` : ""}`, "GET", undefined, {}, 60000);
  const termTitle = (term) => [term?.code, term?.term_label].filter(Boolean).join(" · ");
  const viewActive = (termId) => Boolean(current && current.termId === termId && ready && route === location.hash && !root()?.hidden);

  async function render(force = false) {
    const host = root();
    if (!host || !isGradebookHash(location.hash)) return;
    // The shell renders on navigation and again after each state refresh: keep the grid (and any edit) as it is.
    if (!force && (ready || loading) && route === location.hash && host.firstElementChild) {
      // The course page around it was drawn again (a state refresh): the header stays this gradebook's.
      if (ready && current && grid?.page?.isConnected) nameHeader("grid");
      return;
    }
    closeMenu();
    route = location.hash; ready = false; loading = true;
    const run = ++generation;
    const active = () => run === generation && route === location.hash && !host.hidden;
    const target = parseRoute(location.hash);
    try {
      if (target.page === "course") await coursePage(host, target, active);
      else if (target.page === "terms") { location.replace("#current-courses"); return; }
      else if (target.page === "group") await groupPage(host, target, active);
      else if (target.page === "grid" || target.page === "setup" || target.page === "changes") await legacyPage(host, target, active);
      else host.innerHTML = '<div class="empty-state"><h3>Page not found</h3><p>This gradebook address is not recognized.</p><a class="button" href="#current-courses">Courses</a></div>';
      if (active()) ready = true;
    } catch (error) {
      if (!active()) return;
      grid = null;
      const missing = error?.status === 404;
      host.innerHTML = `<div class="empty-state gradebook-error">${icon("chart")}<h3>${missing ? "Gradebook not found" : "The gradebook could not be loaded"}</h3><p role="alert">${escape(error?.message || "Something went wrong.")}</p><div class="gradebook-actions">${missing ? "" : '<button type="button" class="button" data-gradebook-retry>Try again</button>'}<a class="button" href="#current-courses">Courses</a></div></div>`;
      host.querySelector("[data-gradebook-retry]")?.addEventListener("click", () => { void render(true); });
      // A 404 or 409 makes the shell refresh at once, which would load this address again, fail again and loop: that error
      // page is this address's page until Try again or another address. Other failures are read again by the shell's next
      // refresh (every 30 s, or when the window regains focus).
      if (error?.status === 404 || error?.status === 409) ready = true;
    } finally {
      if (run === generation) loading = false;
    }
  }

  // ---- #gradebook?group=…: from a course page -------------------------------------------------------------------------
  async function groupPage(host, target, active) {
    grid = null;
    host.innerHTML = '<p class="loading-state">Opening the gradebook…</p>';
    const data = await api("/api/gradebook");
    if (!active()) return;
    const term = (data?.terms || []).find((item) => item.group_id === target.group);
    if (!term) {
      host.innerHTML = `<div class="gradebook-page"><div class="empty-state">${icon("chart")}<h3>No gradebook for this course and term</h3><p>A term gets its gradebook once one of its sections has an imported roster.</p><a class="button" href="#gradebook">All gradebooks</a></div></div>`;
      return;
    }
    const section = target.section ? (term.sections || []).find((item) => String(item.number) === String(target.section)) : null;
    location.replace(courseGradebookHref(term.group_id, section ? section.number : null));
  }

  // ---- #gradebook/<term>[/setup|/changes][?section=&item=&assignment=&pull=]: an earlier address, replaced by the course page's --
  async function legacyPage(host, target, active) {
    grid = null;
    host.innerHTML = '<p class="loading-state">Opening the gradebook…</p>';
    const data = await api("/api/gradebook");
    if (!active()) return;
    const term = (data?.terms || []).find((item) => item.id === target.termId);
    if (!term) throw Object.assign(new Error("This gradebook no longer exists."), { status: 404 });
    const section = target.section ? (term.sections || []).find((item) => item.id === target.section) : null;
    const params = new URLSearchParams();
    if (target.item) params.set("item", target.item);
    if (target.assignment) params.set("assignment", target.assignment);
    if (target.pull) params.set("pull", "1");
    location.replace(`${courseGradebookHref(term.group_id, section ? section.number : null)}${target.page === "grid" ? "" : `/${target.page}`}${params.size ? `?${params}` : ""}`);
  }

  // ---- #course/<gid>/home/gradebook: which section? ---------------------------------------------------------------------------------
  // The course Home shows no combined grid (requirement, 2026-09-28). A link that names no section (an exam hub's Recorded stage, a calendar deadline, Home's course list)
  // lands here: a course of one section opens it at once, otherwise the sections are listed, each keeping the link's column or pull,
  // the one opened last first in weight. The setup is the term's, the same from every section, so a link to it opens it from
  // that section (else the first) without asking.
  function chooseSection(host, term, target) {
    grid = null;
    const params = new URLSearchParams();
    if (target.item) params.set("item", target.item);
    if (target.assignment) params.set("assignment", target.assignment);
    if (target.pull) params.set("pull", "1");
    const query = params.size ? `?${params}` : "";
    const sections = [...(term.sections || [])].sort((a, b) => Number(a.number) - Number(b.number));
    const sub = target.sub === "grid" ? "" : `/${target.sub}`;
    const remembered = lastSection(term.group_id);
    const last = sections.length > 1 ? sections.find((section) => String(section.number) === remembered) || null : null;
    if (sections.length === 1 || (target.sub === "setup" && sections.length)) {
      location.replace(`${courseGradebookHref(term.group_id, (last || sections[0]).number)}${sub}${query}`);
      return;
    }
    const place = termTitle(term);
    header?.set({ route: location.hash, variant: "compact", title: `${term.code || "Gradebook"} · Gradebook`, context: place,
      subtitle: "Each section has its own gradebook tab; the columns and weights are shared by the term.",
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Courses", href: "#current-courses" }, { label: place, href: `#course/${enc(term.group_id)}/home` }, { label: "Gradebook" }],
      tab: `Gradebook · ${place}`, action: null, secondary: [] });
    const note = target.pull ? "Choose the section to pull its grades into; each section is pulled on its own tab."
      : target.item || target.assignment ? "Choose the section whose gradebook to open." : "Choose a section.";
    // The section opened last (browser memory, per course) is the page's one primary; the others stay links, in number order.
    host.innerHTML = `<div class="gradebook-page" data-gradebook-page="sections"><p class="supporting-copy">${escape(note)}</p>
      <ul class="gradebook-sections" role="list">${sections.map((section) => `<li${section === last ? " data-gradebook-last" : ""}><a class="${section === last ? "button primary" : "link-arrow"}" href="${escape(`${courseGradebookHref(term.group_id, section.number)}${sub}${query}`)}">Section ${escape(section.number)}</a>${section === last ? "<small>Last opened</small>" : ""}</li>`).join("")}</ul></div>`;
  }

  // ---- #course/<gid>/(home|section/<n>)/gradebook[/setup|/changes]: inside the course page ----------------------------------------
  async function coursePage(host, target, active) {
    grid = null;
    if (!host.firstElementChild) host.innerHTML = '<p class="loading-state">Loading the gradebook…</p>';
    const data = await api("/api/gradebook");
    if (!active()) return;
    const term = (data?.terms || []).find((item) => item.group_id === target.group);
    // No roster yet (the term has no gradebook until one is imported): say so where the gradebook will be, with the way to the roster.
    const noRoster = () => {
      header?.set({ route: location.hash, variant: "compact", title: "Gradebook", context: target.number === null ? "" : `Section ${target.number}`, subtitle: "",
        crumbs: [{ label: "Home", href: "#overview" }, { label: "Courses", href: "#current-courses" }, { label: "Course", href: `#course/${enc(target.group)}/home` },
          ...(target.number === null ? [] : [{ label: `Section ${target.number}` }])], action: null, secondary: [] });
      const roster = target.number === null ? "" : `<a class="button" href="#course/${enc(target.group)}/section/${target.number}/roster">Import this section’s roster</a>`;
      host.innerHTML = `<div class="gradebook-page"><div class="empty-state">${icon("chart")}<h3>No gradebook yet</h3><p>A section’s gradebook starts once its roster is imported.</p>${roster}</div></div>`;
    };
    if (!term) { noRoster(); return; }
    if (target.number === null) { chooseSection(host, term, target); return; }
    const section = (term.sections || []).find((item) => Number(item.number) === target.number);
    if (target.number !== null && !section) throw Object.assign(new Error("This section has no gradebook rows yet."), { status: 404 });
    const base = courseGradebookHref(term.group_id, section ? section.number : null);
    // The short-lived .../section/<n>/gradebook address becomes the section's own.
    const [path, query = ""] = location.hash.split("?");
    if (path.includes(`/section/${section.number}/gradebook`)) {
      const next = `${base}${target.sub === "grid" ? "" : `/${target.sub}`}${query ? `?${query}` : ""}`;
      history.replaceState(history.state, "", next);
      route = next;
    }
    await termPage(host, { page: target.sub, termId: term.id, section: section ? section.id : target.section, item: target.item,
      assignment: target.assignment, pull: target.pull, base, group: term.group_id, number: section ? section.number : null }, active);
  }

  // ---- #course/<gid>/(home|section/<n>)/gradebook[/setup|/changes]: the page itself (called only from coursePage) ----------------
  async function termPage(host, target, active) {
    grid = null;
    host.innerHTML = '<p class="loading-state">Loading the gradebook…</p>';
    const view = await fetchView(target.termId, target.section);
    if (!active()) return;
    const known = (view.sections || []).some((section) => section.id === target.section);
    const ctx = current?.termId === target.termId ? current.ctx : context(target.termId);
    current = { termId: target.termId, view, ctx, page: target.page, section: known ? target.section : "", item: target.item || "", assignment: target.assignment || "", linked: "", changes: 0,
      base: target.base || `#gradebook/${enc(target.termId)}`, group: target.group || view.term?.group_id || "", number: target.number ?? null };
    if (current.group && current.number !== null) rememberSection(current.group, current.number);
    if (target.page === "setup") await setupPage(host, active);
    else if (target.page === "changes") await changesPage(host, active);
    else {
      gridPage(host);
      if (target.pull) openPullFromLink();
    }
    // Warm the dialogs so the first click opens at once; a failure is reported when an action needs them.
    loadDialogs().catch(() => {});
  }

  // `?assignment=<aid>&pull=1` (the exam hub's Recorded link): drop `pull=1` from the address first, so a reload or Back never opens
  // it again, then open the pull PREVIEW of the assignment's live linked column (its "Pull N scores" stays an explicit click),
  // or, without such a column, put the focus on the notice's "Add a column for it".
  function openPullFromLink() {
    if (!current || current.page !== "grid") return;
    const [path, query = ""] = location.hash.split("?");
    const params = new URLSearchParams(query);
    params.delete("pull");
    const next = `${path}${params.size ? `?${params}` : ""}`;
    if (next !== location.hash) { history.replaceState(history.state, "", next); route = next; }
    const itemId = targetItem();
    const item = itemId ? (current.view.setup?.items || []).find((entry) => entry.id === itemId) : null;
    const termId = current.termId;
    // After this render has finished (the page is then the active one a dialog may open over).
    window.setTimeout(() => {
      if (!viewActive(termId) || current.page !== "grid") return;
      if (item && !item.archived && item.link?.kind === "grading" && item.assignment_id === current.assignment) { void dialog("openPull", itemId); return; }
      /** @type {HTMLElement|null} */ (grid?.page.querySelector("[data-gradebook-assignment-notice] [data-gradebook-link]"))?.focus();
    }, 0);
  }

  // The shell's compact header names this term, with its trail; the grid's actions are there (FRONTEND.md, header.set).
  function nameHeader(page) {
    if (!header || !current) return;
    const { view } = current;
    const place = termTitle(view.term) || "Gradebook";
    const gridHref = current.base;
    const sectionLabel = `Section ${current.number}`;
    // The course page's trail: Courses, this course and term, the section, Gradebook.
    const courseHome = current.group ? `#course/${enc(current.group)}/home` : "#current-courses";
    const crumbs = [{ label: "Home", href: "#overview" }, { label: "Courses", href: "#current-courses" }, { label: place, href: courseHome },
      { label: sectionLabel, href: `#course/${enc(current.group)}/section/${current.number}` }, { label: "Gradebook", href: page === "grid" ? undefined : gridHref }];
    if (page === "setup") {
      header.set({ route: location.hash, variant: "compact", title: "Gradebook setup", context: [place, view.term?.name].filter(Boolean).join(" · "),
        subtitle: "Shared by every section of this term; saving records a new revision and changes no score.",
        crumbs: [...crumbs, { label: "Setup" }], tab: `Setup · ${place}`, action: null, secondary: [] });
      return;
    }
    if (page === "changes") {
      header.set({ route: location.hash, variant: "compact", title: "Gradebook changes", context: `${place} · ${sectionLabel}`,
        subtitle: `Every change to Section ${current.number}'s scores and to the term's setup, newest first. Each one is kept; this page changes nothing.`,
        crumbs: [...crumbs, { label: "Changes" }], tab: `Changes · ${place} · ${sectionLabel}`, action: null, secondary: [] });
      return;
    }
    // Add column is setup, never the page's main action once the gradebook has columns (DESIGN-LANGUAGE §1.4, Ledger 2026-10-04):
    // it is the primary only for an empty gradebook, and while the guided start offers a graded assignment to add (its own
    // button) it steps back to a secondary too, so the page has at most one primary action.
    const addColumn = { label: "Add column", icon: "plus", run: () => { void dialog("openColumn", null); } };
    const empty = !(view.setup?.items || []).some((item) => !item.archived);
    const guided = empty && (view.assignments || []).some((assignment) => !(assignment.items || []).length && (assignment.parts || []).length);
    const canvasSync = syncState(view.canvas, when);
    header.set({ route: location.hash, variant: "compact", title: `${view.term?.code || "Gradebook"} · ${sectionLabel}`, context: [place, "Gradebook"].join(" · "),
      subtitle: `Section ${current.number}'s students; the columns, categories and weights are shared by every section of the term.`,
      crumbs, tab: `Gradebook · ${place} · ${sectionLabel}`,
      action: empty && !guided ? addColumn : null,
      secondary: [...(empty && !guided ? [] : [addColumn]), { label: "Import scores", icon: "download", run: () => { void dialog("openImport"); } },
        // SYNC WITH CANVAS: one outward action (DESIGN-LANGUAGE §1.4), Start from Canvas until the term is linked.
        ...(canvasSync ? [{ label: canvasSync.label, icon: "route", title: canvasSync.title, data: { outward: "" }, run: () => { void dialog("openCanvasSync"); } }] : []),
        // iClicker (gradebook/iclicker_import.py): offered wherever iClicker is set up; the section's course is chosen in the import.
        ...(view.iclicker ? [{ label: "Import from iClicker", icon: "download", run: () => { void dialog("openIClickerImport"); } }] : []),
        { label: "Setup", icon: "settings", href: `${gridHref}/setup` }] });
  }

  async function setupPage(host, active = () => true) {
    grid = null;
    const termId = current.termId;
    nameHeader("setup");
    // The setup editor draws its own heading and way back; this page only hosts it.
    host.innerHTML = `<div class="gradebook-page" data-gradebook-page="setup"><div class="gradebook-setup-host" data-gradebook-setup><p class="loading-state">Loading the setup…</p></div></div>`;
    const slot = host.querySelector("[data-gradebook-setup]");
    const failed = (message, reload = false) => {
      if (!slot.isConnected) return;
      slot.innerHTML = `<div class="callout gradebook-notice" data-tone="${reload ? "warning" : "danger"}" role="alert" data-gradebook-setup-failed><strong class="callout-title">${escape(message)}</strong>${reload ? '<button type="button" class="button primary callout-action" data-gradebook-reload>Reload</button>' : ""}<a class="button callout-action" href="${escape(current.base)}">Back to the gradebook</a></div>`;
      slot.querySelector("[data-gradebook-reload]")?.addEventListener("click", reloadPage);
    };
    let module;
    try { module = await loadSetup(); } catch { failed(UPDATED, true); return; }
    if (!active() || !slot.isConnected) return;
    try { await module.renderSetup(slot, current.ctx); } catch (error) { failed(error?.message || "The setup editor could not be shown."); }
  }

  // The Changes page (README.md "Changes"): read-only, so it hosts gradebook-changes.js with the View on screen (names, titles).
  async function changesPage(host, active = () => true) {
    grid = null;
    nameHeader("changes");
    host.innerHTML = `<div class="gradebook-page" data-gradebook-page="changes"><div data-gradebook-changes><p class="loading-state">Loading the changes…</p></div></div>`;
    const slot = /** @type {HTMLElement} */ (host.querySelector("[data-gradebook-changes]"));
    let module;
    try { module = await loadChanges(); } catch {
      if (slot.isConnected) slot.innerHTML = `<div class="callout gradebook-notice" data-tone="warning" role="status"><strong class="callout-title">${escape(UPDATED)}</strong><button type="button" class="button primary callout-action" data-gradebook-reload>Reload</button></div>`;
      slot.querySelector("[data-gradebook-reload]")?.addEventListener("click", reloadPage);
      return;
    }
    if (!active() || !slot.isConnected) return;
    await module.renderChanges(slot, current.ctx);
  }

  // `changes` counts what the page adopted (a View, a scores write). A read that a write's answer overtook is older than
  // what is shown: it is not adopted, and its caller gets the View on screen.
  function reportAssignmentChange(termId, previous, view) {
    if (!viewActive(termId)) return;
    const itemId = new URLSearchParams(location.hash.split("?")[1]).get("item");
    if (!itemId) return;
    const before = previous.setup?.items?.find((item) => item.id === itemId)?.assignment_id || null;
    const after = view.setup?.items?.find((item) => item.id === itemId)?.assignment_id || null;
    if (before === after) return;
    // ASSIGNMENT-WORKSPACE: keep the owner editor, but resolve its changed column reference again.
    root()?.dispatchEvent(new CustomEvent("faculty:gradebook-association-changed", { bubbles: true,
      detail: { route: location.hash, termId, itemId, assignmentId: after } }));
  }

  async function reload(termId) {
    const owner = current?.termId === termId ? current : null;
    const seen = owner?.changes ?? -1;
    const view = await fetchView(termId);
    if (owner && current === owner) {
      if (current.changes !== seen) return current.view;
      const previous = current.view;
      current.view = view;
      current.changes++;
      redraw(termId);
      reportAssignmentChange(termId, previous, view);
    }
    return view;
  }

  function replaceView(termId, view) {
    if (!view || current?.termId !== termId || (view.term?.id && view.term.id !== termId)) return;
    const previous = current.view;
    current.view = view;
    current.changes++;
    if (view.item_id) current.linked = view.item_id;
    redraw(termId);
    reportAssignmentChange(termId, previous, view);
  }

  function redraw(termId) {
    if (!viewActive(termId)) return;
    const host = root();
    if (current.page === "setup") void setupPage(host);
    else if (current.page === "changes") void changesPage(host);
    else gridPage(host);
  }

  // ---- The grid ------------------------------------------------------------------------------------------------------
  function gridPage(host) {
    // GRADEBOOK: only the editor an Assistant selection/chat is protecting defers a quiet redraw (ui.js holdCaptureFocus).
    if (grid?.editing && isCaptureHeld(grid.editing.input)) return;
    const { view, termId } = current;
    const state = remember(termId);
    if (!(view.sections || []).some((section) => section.id === current.section)) current.section = "";
    const keep = grid?.termId === termId && grid.table?.isConnected ? { top: grid.scroller.scrollTop, left: grid.scroller.scrollLeft, focus: activeKeys(), hadFocus: grid.table.contains(document.activeElement) } : null;
    if (grid?.editing) finishEdit("blur");
    closeMenu();
    nameHeader("grid");
    // Add column, Import scores and Setup are in the page header; Export CSV and Refresh are quiet controls here.
    host.innerHTML = `<div class="gradebook-page" data-gradebook-page="grid" data-term="${escape(termId)}">
      <div class="gradebook-toolbar">
        <div class="gradebook-filters" role="group" aria-label="Show students">
          <label class="field gradebook-field gradebook-search">Find a student<input type="search" data-gradebook-search data-page-search placeholder="Name" autocomplete="off" spellcheck="false"></label></div>
        <p class="gradebook-summary" data-gradebook-summary aria-live="polite"></p></div>
      <div class="gradebook-attention" data-gradebook-attention></div>
      <div data-gradebook-differs></div>
      <div data-gradebook-uncertain></div>
      <div data-gradebook-notices></div>
      <div class="gradebook-bar"><p class="gradebook-key" data-gradebook-key hidden></p>
        <div class="gradebook-tools"><button type="button" class="text-button" popovertarget="gradebook-shortcuts" data-gradebook-shortcuts>${icon("info")}Shortcuts</button><a class="text-button" data-gradebook-changes-link href="${escape(`${current.base}/changes`)}">${icon("book")}Changes</a><a class="text-button" data-gradebook-export ${viewLink(`/api/gradebook/terms/${enc(termId)}/export.csv?section=${enc(current.section)}`, "Gradebook export", escape)}>${icon("paper")}Export CSV</a><button type="button" class="text-button" data-gradebook-action="refresh">${icon("refresh")}Refresh</button></div></div>
      <div data-gradebook-body></div>
      <div data-gradebook-activity></div>
      <div class="gradebook-shortcuts" popover id="gradebook-shortcuts" role="dialog" aria-label="Keyboard shortcuts" data-gradebook-shortcuts-list><p class="gradebook-menu-title">Keyboard and entry</p><ul><li>Arrow keys, Home/End and Page Up/Down move; Up from the first row reaches the column headings.</li><li>Type, or press Enter or F2, to edit. Enter saves and moves down, Tab saves and moves right, Esc cancels.</li><li>Type points, <strong>EX</strong> for excused (not counted), <strong>MI</strong> for missing (counts as 0), or nothing to clear. Delete clears a cell.</li><li>Paste a block copied from a spreadsheet into the top-left cell it should fill. Ctrl+C copies a cell.</li><li>Double-click a score, click its ${icon("info")} button or press Shift+F10 for its history. Alt+Down opens a column’s options.</li><li>A student’s name opens their record; Left from the first column reaches it.</li><li>A paste that replaces values, or writes more than ${PASTE_CONFIRM} cells, asks first.</li><li>Overall and Letter (and, when there is room, each category’s percentage) stay at the right edge; scores scroll beneath them.</li></ul></div>
      <div class="gradebook-menu" popover role="dialog" id="gradebook-column-menu" data-gradebook-menu></div></div>`;
    const page = host.querySelector(".gradebook-page");
    const search = page.querySelector("[data-gradebook-search]");
    search.value = state.search;
    search.addEventListener("input", () => { state.search = search.value; applySearch(); });
    page.querySelector('[data-gradebook-action="refresh"]').addEventListener("click", async (event) => {
      const button = event.currentTarget;
      setBusy(button, true);
      try { await reload(termId); toast("Gradebook refreshed."); } catch (error) { toast(error?.message || "The gradebook could not be refreshed.", true); } finally { if (button.isConnected) setBusy(button, false); }
    });
    // The attention strip's filters (view memory only): a letter, "below a letter" or the blanks past due; one at a time.
    const attention = page.querySelector("[data-gradebook-attention]");
    attention.addEventListener("click", (event) => {
      const button = /** @type {HTMLElement} */ (event.target).closest("[data-attention]");
      if (!button) return;
      const [kind, value = ""] = String(button.getAttribute("data-attention")).split(":");
      const filter = remember(termId).filter;
      setFilter(kind === "clear" || (filter.kind === kind && filter.value === value) ? { kind: "", value: "" } : { kind, value }, button.getAttribute("data-attention"));
    });
    attention.addEventListener("change", (event) => {
      const select = /** @type {HTMLSelectElement} */ (event.target);
      if (select.matches("[data-attention-below]")) setFilter(select.value ? { kind: "below", value: select.value } : { kind: "", value: "" }, "below");
    });
    // DIFFERS FROM GRADING: a column's Show (its first differing cell) and Pull (its preview); Certify again is a link.
    page.querySelector("[data-gradebook-differs]").addEventListener("click", (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      const pull = target.closest("[data-differs-pull]"), show = target.closest("[data-differs-show]");
      if (pull) void dialog("openPull", pull.getAttribute("data-differs-pull"));
      else if (show) showDiffering(String(show.getAttribute("data-differs-show")));
    });
    page.querySelector("[data-gradebook-uncertain]").addEventListener("click", (event) => {
      const button = /** @type {HTMLElement} */ (event.target).closest("[data-gradebook-check]");
      if (button instanceof HTMLButtonElement && !button.disabled) checkUncertain(termId, button);
    });
    const menu = page.querySelector("[data-gradebook-menu]");
    menu.addEventListener("click", onMenuClick);
    menu.addEventListener("keydown", onMenuKey);
    // Tabbing out of the menu closes it (a click elsewhere or Esc already does). A press on its own heading also moves the
    // focus out first: recorded like a light dismiss, so the click that follows does not open the menu again.
    menu.addEventListener("focusout", (event) => {
      const next = /** @type {FocusEvent} */ (event).relatedTarget;
      if (!(next instanceof Node) || menu.contains(next) || !menuOpen(menu) || !grid) return;
      const item = menu.dataset.item;
      closeMenu();
      grid.menuClosed = { item, at: performance.now() };
    });
    // A press on the heading's button while its menu is open closes the menu (light dismiss) before the click that would open
    // it again: that click is ignored. A menu this script closed (an action, a sort, a scroll) is reopened at once.
    // Escape closes it too, and is not such a press: the next press on the heading opens the menu again at once.
    menu.addEventListener("beforetoggle", (event) => {
      if (/** @type {any} */ (event).newState !== "closed" || !grid) return;
      grid.menuClosed = grid.hidingMenu || grid.escaped ? null : { item: menu.dataset.item, at: performance.now() };
      grid.escaped = false;
    });
    menu.addEventListener("keydown", (event) => { if (event.key === "Escape" && grid) grid.escaped = true; }, true);
    menu.addEventListener("toggle", (event) => {
      if (/** @type {any} */ (event).newState !== "closed") return;
      const trigger = grid?.menuTrigger;
      trigger?.setAttribute("aria-expanded", "false");
      if (trigger?.isConnected && (!document.activeElement || document.activeElement === document.body || menu.contains(document.activeElement))) trigger.focus({ preventScroll: true });
    });
    grid = { termId, page, menu, menuTrigger: null, editing: null, active: null, pos: { r: 0, c: 0 }, scroller: null, table: null };
    drawGrid(keep);
    void checkGrading(termId); // DIFFERS FROM GRADING: read again for every View this page draws
  }

  function activeKeys() {
    if (!grid?.active) return null;
    const cell = grid.active.closest("td, th");
    return { row: cell?.parentElement?.dataset.row || "", col: grid.pos.c, item: cell?.dataset.item || cell?.closest("th")?.dataset.item || "", result: cell?.dataset.resultCell || cell?.closest("th")?.dataset.result || "", header: grid.pos.r < 0 };
  }

  function drawGrid(keep = null) {
    const { view, termId } = current;
    const state = remember(termId);
    const body = grid.page.querySelector("[data-gradebook-body]");
    const highlight = targetItem();
    const model = gridModel(view, { sectionId: current.section, sort: state.sort, highlight, collapsed: state.collapsed });
    // A pending or unsure key (pendingKey) without its term is the cell's cellKey, the key differs uses too.
    const shownPending = new Map([...pending].filter(([key]) => key.startsWith(`${termId}\u0000`)).map(([key, entry]) => [key.slice(termId.length + 1), entry]));
    const shownUnsure = new Set([...unsure].filter((key) => key.startsWith(`${termId}\u0000`)).map((key) => key.slice(termId.length + 1)));
    grid.model = model;
    grid.overdue = pastDue(view, model.students, localToday(), current.section);
    setDiffers();
    const exportLink = grid.page.querySelector("[data-gradebook-export]");
    const exported = `/api/gradebook/terms/${enc(termId)}/export.csv${current.section ? `?section=${enc(current.section)}` : ""}`;
    exportLink.setAttribute("href", exported);
    exportLink.setAttribute("data-view-file", exported);
    drawDiffers();
    drawNotices(model);
    drawActivity();
    drawAttention();
    drawUncertain();
    drawKey();
    if (!(view.students || []).length) {
      body.innerHTML = `<div class="empty-state gradebook-empty">${icon("users")}<h3>No students yet</h3><p>This section’s gradebook starts once its roster is imported.</p><a class="button" href="#course/${enc(current.group)}/section/${current.number}/roster">Import this section’s roster</a></div>`;
      Object.assign(grid, { table: null, scroller: null, rows: [], allRows: [] });
      updateSummary();
      return;
    }
    const sectionLabel = current.section ? ` · Section ${(view.sections || []).find((section) => section.id === current.section)?.number ?? ""}` : "";
    // What stays frozen at the right edge is decided from the width the grid will have, measured before the table is added
    // (a layout of the page without it), so the first layout of a large table happens once, at paint.
    const freeze = freezeLevel(model, body.clientWidth - 2);
    body.innerHTML = `<div class="gradebook-scroll" role="region" aria-label="${escape(`Scores for ${termTitle(view.term)}${sectionLabel}; scroll to see every student and column`)}" data-gradebook-scroll>${gridMarkup(view, model, { esc: escape, pending: shownPending, termLabel: termTitle(view.term) + sectionLabel, freeze, icon, unsure: shownUnsure, overdue: state.filter.kind === "overdue" ? grid.overdue : null, differs: grid.differs })}<button type="button" class="gb-history" data-cell-history tabindex="-1" aria-label="Score history" title="Score history (Shift+F10)" hidden>${icon("info")}</button></div>`;
    const scroller = body.querySelector("[data-gradebook-scroll]"), table = scroller.querySelector("table");
    const allRows = [...table.tBodies[0].rows];
    const button = /** @type {HTMLButtonElement} */ (scroller.querySelector("[data-cell-history]"));
    button.addEventListener("click", () => { if (grid?.active && itemCell(grid.active)) void showHistory(grid.active); });
    Object.assign(grid, {
      scroller, table, thead: table.tHead, allRows, rows: allRows,
      rowMap: new Map(allRows.map((tr) => [tr.dataset.row, tr])),
      colIndex: new Map(model.items.map((item, index) => [item.id, index])),
      itemById: new Map(model.items.map((item) => [item.id, item])),
      studentByRow: new Map(model.students.map((student) => [student.row, student])),
      heads: [...table.tHead.querySelectorAll("[data-col]")].sort((a, b) => Number(a.dataset.col) - Number(b.dataset.col)),
      corner: [...table.tHead.querySelectorAll(".gb-corner .gb-sort")],
      colCount: model.items.length + model.results.length,
      active: null, editing: null, historyButton: button, hover: null,
    });
    // A grid left for another page stays on screen until that page draws (its reads take a moment) while `grid` is already
    // null: its table answers no event then (a pointer passing over it read `grid.hover` of null).
    const mine = (handler) => (event) => { if (grid?.table === table) handler(event); };
    table.addEventListener("keydown", mine(onKeydown));
    table.addEventListener("focusin", mine(onFocusIn));
    table.addEventListener("click", mine(onClick));
    table.addEventListener("dblclick", mine(onDoubleClick));
    table.addEventListener("pointerdown", mine((event) => {
      const td = /** @type {HTMLElement} */ (event.target).closest?.("td");
      grid.pointer = event.pointerType;
      grid.pressedActive = Boolean(td) && td === grid.active && document.activeElement === td;
    }), { passive: true });
    table.addEventListener("mouseover", mine(onHover), { passive: true });
    table.addEventListener("paste", mine(onPaste));
    table.addEventListener("copy", mine(onCopy));
    // The menu stays by its heading: scrolling the grid closes it. A scroll event fires a frame after the scroll, so one that
    // revealed the heading just before the click (focus or scrolling it into view) leaves the grid where the menu was opened.
    scroller.addEventListener("scroll", () => {
      if (menuOpen(grid?.menu) && (scroller.scrollLeft !== grid.menuScroll?.left || scroller.scrollTop !== grid.menuScroll?.top)) closeMenu();
      markOverlap();
    }, { passive: true });
    applyWidths();
    grid.frozenRight = frozenWidth(model, freeze);
    requestAnimationFrame(() => { if (grid?.scroller === scroller) { markOverlap(); fitHeight(); } }); // read once laid out, never forcing it
    applySearch(false);
    // Restore where the instructor was, else start at the first cell (or the column a link asked for).
    let row = 0, col = 0;
    if (keep?.focus) {
      const r = keep.focus.row ? grid.rows.findIndex((tr) => tr.dataset.row === keep.focus.row) : -1;
      const c = keep.focus.item ? grid.colIndex.get(keep.focus.item)
        : keep.focus.result && grid.colIndex.has(keep.focus.result) ? grid.colIndex.get(keep.focus.result)
        : keep.focus.result && model.results.some((result) => result.key === keep.focus.result) ? model.items.length + model.results.findIndex((result) => result.key === keep.focus.result) : -1;
      row = keep.focus.header ? -1 : Math.max(0, r);
      col = c !== undefined && c >= 0 ? c : Math.min(keep.focus.col, grid.colCount - 1);
    } else if (highlight && grid.colIndex.has(highlight)) col = grid.colIndex.get(highlight);
    if (!grid.rows.length) row = -1;
    let start = elementAt(row, col);
    if (!start) [start, row, col] = [elementAt(-1, 0), -1, 0];
    if (start) activate(start, row, col);
    if (keep) {
      scroller.scrollTop = keep.top; scroller.scrollLeft = keep.left;
      if (keep.hadFocus && start) { start.focus({ preventScroll: true }); reveal(start); }
    } else if (highlight && grid.colIndex.has(highlight)) {
      const head = grid.heads[grid.colIndex.get(highlight)];
      scroller.scrollLeft = Math.max(0, head.offsetLeft - WIDTHS.name - 24);
    }
    updateSummary();
  }

  // The column a link asked for: ?item=, the column linking ?assignment=, or the one a link just added.
  function targetItem() {
    const items = (current.view.setup?.items || []).filter((item) => !item.archived);
    if (current.linked && items.some((item) => item.id === current.linked)) return current.linked;
    if (current.item && items.some((item) => item.id === current.item)) return current.item;
    if (current.assignment) return items.find((item) => item.assignment_id === current.assignment)?.id || "";
    return "";
  }

  // One notice: the shared callout (components.css), keeping this page's data-* hooks. `html` is built by the caller.
  const notice = (tone, hook, html, action = "") => `<div class="callout gradebook-notice" data-tone="${tone}" role="status" ${hook}><p>${html}</p>${action}</div>`;

  function drawNotices(model) {
    const { view } = current;
    const host = grid.page.querySelector("[data-gradebook-notices]");
    const notices = [];
    const items = view.setup?.items || [];
    const highlight = targetItem();
    const setupHref = `${current.base}/setup`;
    // Which disclosures were open: the notices are drawn again with the grid (a sort, a section), and stay as they were.
    const open = new Set([...host.querySelectorAll("details[data-gradebook-fold][open]")].map((node) => node.getAttribute("data-gradebook-fold")));
    if (typeof setupLoaded?.unsavedSetup === "function" && setupLoaded.unsavedSetup(current.termId)) {
      notices.push(notice("warning", "data-gradebook-unsaved-setup", "The setup has changes that are not saved yet.", `<a class="button callout-action" href="${setupHref}">Continue in Setup</a>`));
    }
    // A counted column without a category counts toward nothing in a weighted gradebook (the server names them): one warning,
    // whose action opens that column's settings (several: the setup, where their categories are chosen). Only the columns of the
    // section shown: one limited to another section is not in this grid, and none of its students lose anything.
    const lost = uncountedItems(view, model.sectionId);
    if (lost.length) {
      const named = lost.slice(0, 3).map((item) => `<strong>“${escape(item.title)}”</strong>`);
      const list = lost.length > 3 ? `${named.join(", ")} and ${plural(lost.length - 3, "other")}` : named.length > 1 ? `${named.slice(0, -1).join(", ")} and ${named.at(-1)}` : named[0];
      notices.push(notice("warning", "data-gradebook-uncounted", `${list} ${lost.length === 1 ? "has" : "have"} no category, so ${lost.length === 1 ? "it does" : "they do"} not count toward Overall.`,
        lost.length === 1 ? `<button type="button" class="button callout-action" data-gradebook-uncounted-open="${escape(lost[0].id)}">Choose its category</button>`
          : `<a class="button callout-action" href="${setupHref}">Choose their categories in Setup</a>`));
    }
    // The morning sync: a notice only when it needs the instructor (signed out of Canvas, an assignment to place, grades it leaves
    // to the instructor's own Sync). What its send left unconfirmed, posts included, is the last sync's one notice below.
    const nightly = nightlyNotice(view.canvas?.nightly, view.canvas?.last_sync);
    if (nightly) {
      notices.push(notice(nightly.tone, "data-gradebook-nightly", escape(nightly.text), nightly.action === "signin"
        ? '<button type="button" class="button callout-action" data-gradebook-nightly-signin>Sign in to Canvas</button>'
        : nightly.action === "import" ? `<button type="button" class="button callout-action" data-gradebook-nightly-import>Sync with Canvas</button>${nightly.skip
          ? `<button type="button" class="button callout-action" data-gradebook-nightly-skip title="Leave out of every sync from now on; choosing a column for it in Sync with Canvas brings it back">${escape(nightly.skip)}</button>` : ""}` : ""));
    }
    // ICLICKER AFTER CLASS: the section's latest after-class import, only when it needs the instructor.
    const after = iclickerNotice(view.iclicker, model.sectionId);
    if (after) {
      notices.push(notice(after.tone, "data-gradebook-iclicker", escape(after.text), view.iclicker.scheduled
        ? '<button type="button" class="button callout-action" data-gradebook-iclicker-open>Import from iClicker</button>' : ""));
    }
    // SYNC WITH CANVAS: what the last sync left for the instructor (held grades, a send Canvas did not confirm).
    const synced = syncState(view.canvas, when)?.notice;
    if (synced) {
      notices.push(notice(synced.tone, "data-gradebook-sync", escape(synced.text), '<button type="button" class="button callout-action" data-gradebook-sync-open data-outward>Sync with Canvas</button>'));
    }
    if (current.assignment && !highlight) {
      const assignment = (view.assignments || []).find((item) => item.id === current.assignment);
      const archived = items.find((item) => item.assignment_id === current.assignment && item.archived);
      if (archived) notices.push(notice("info", "data-gradebook-assignment-notice", `<strong>${escape(archived.title)}</strong> links this assignment but is archived. Restore it in Setup to see its scores.`, `<a class="button callout-action" href="${setupHref}">Open setup</a>`));
      else notices.push(notice("info", "data-gradebook-assignment-notice", assignment ? `<strong>${escape(assignment.title)}</strong> has no column in this gradebook yet.` : "This assignment is not part of this term’s gradebook.",
        assignment ? `<button type="button" class="button callout-action" data-gradebook-link="${escape(assignment.id)}">${icon("plus")}Add a column for it</button>` : ""));
    }
    if (current.item && !current.assignment && !highlight) {
      const asked = items.find((item) => item.id === current.item);
      notices.push(notice("info", "data-gradebook-item-notice", asked ? `<strong>${escape(asked.title)}</strong> is archived. Restore it in Setup to see its scores.` : "The column this link names is no longer in this gradebook.",
        asked ? `<a class="button callout-action" href="${setupHref}">Open setup</a>` : ""));
    }
    if (highlight && !model.items.some((item) => item.id === highlight)) {
      const item = items.find((entry) => entry.id === highlight);
      notices.push(notice("info", "", `<strong>${escape(item?.title || "This column")}</strong> does not apply to the section shown.`));
    }
    // Assignments of this course without a live column: those graded in Faculty this term first (something to pull now), then
    // the others ("plan ahead": a linked column pulls once final grades exist).
    const unlinked = (view.assignments || []).filter((assignment) => !(assignment.items || []).length);
    const ready = unlinked.filter((assignment) => (assignment.parts || []).length);
    const later = unlinked.filter((assignment) => !(assignment.parts || []).length);
    const facts = (assignment) => [(assignment.parts || []).map((part) => PARTS[part] || part).join(" · "), assignment.maximum ? `${trimPoints(assignment.maximum)} pts` : ""].filter(Boolean).join(" · ");
    const row = (assignment, label, style = "text-button") => `<li><span>${escape(assignment.title)}${facts(assignment) ? `<small>${escape(facts(assignment))}</small>` : ""}</span><button type="button" class="${style}" data-gradebook-link="${escape(assignment.id)}" aria-label="${escape(`${label} ${assignment.title}`)}">${icon("plus")}${escape(label)}</button></li>`;
    const addAll = ready.length > 1 ? `<button type="button" class="button primary" data-gradebook-add-all>${escape(`Add all ${ready.length} as columns`)}</button>` : "";
    const laterFold = (label) => later.length ? `<details class="disclosure gradebook-plan-ahead" data-gradebook-fold="later"${open.has("later") ? " open" : ""}><summary><span>${escape(label)}</span><span class="disclosure-hint">${escape(`${later.length} not graded yet`)}</span></summary><div class="disclosure-body"><ul class="gradebook-assignments">${later.map((assignment) => row(assignment, "Add linked column")).join("")}</ul></div></details>` : "";
    if (!items.filter((item) => !item.archived).length) {
      // Guided start: the three ways a gradebook begins. Keeps the heading "No columns yet".
      const fromGrading = ready.length
        ? `<ul class="gradebook-assignments">${ready.map((assignment) => row(assignment, "Add and pull", ready.length === 1 ? "button primary" : "button")).join("")}</ul>${addAll}`
        : `<p class="supporting-copy">${later.length ? "No assignment of this course is graded in Faculty this term yet." : "This course has no assignments in Faculty yet."}</p>`;
      notices.push(`<section class="card gradebook-start" aria-labelledby="gradebook-start-title"><header class="card-head"><h3 id="gradebook-start-title">No columns yet</h3><p class="supporting-copy">Start this gradebook one of three ways.</p></header>
        <ol class="gradebook-ways">
          <li data-gradebook-way="grading"><h4>From Faculty’s grading</h4>${fromGrading}${laterFold("Plan ahead")}</li>
          <li data-gradebook-way="import"><h4>Import a Gradescope or spreadsheet export</h4><p class="supporting-copy">Scores are matched to students by UIN and previewed before anything is written.</p><button type="button" class="button" data-gradebook-start-import>${icon("download")}Import scores</button></li>
          <li data-gradebook-way="setup"><h4>Build it by hand</h4><p class="supporting-copy">Categories, weights, columns and a letter scale.</p><a class="button" href="${setupHref}">${icon("settings")}Open Setup</a></li></ol></section>`);
    } else if (unlinked.length) {
      const readyList = ready.length ? `<ul class="gradebook-assignments">${ready.map((assignment) => row(assignment, "Add linked column")).join("")}</ul>${addAll}` : "";
      notices.push(`<details class="disclosure gradebook-unlinked" data-gradebook-fold="unlinked"${open.has("unlinked") ? " open" : ""}><summary><span>Assignments without a column</span><span class="disclosure-hint">${unlinked.length}</span></summary><div class="disclosure-body">${readyList}${ready.length ? laterFold("Not graded yet") : `<ul class="gradebook-assignments">${later.map((assignment) => row(assignment, "Add linked column")).join("")}</ul>`}</div></details>`);
    }
    host.innerHTML = notices.join("");
    host.querySelectorAll("[data-gradebook-link]").forEach((button) => button.addEventListener("click", () => { void dialog("openLink", button.getAttribute("data-gradebook-link")); }));
    host.querySelector("[data-gradebook-add-all]")?.addEventListener("click", () => { void dialog("openAddAll", ready.map((assignment) => assignment.id)); });
    host.querySelector("[data-gradebook-start-import]")?.addEventListener("click", () => { void dialog("openImport"); });
    host.querySelector("[data-gradebook-nightly-import]")?.addEventListener("click", () => { void dialog("openCanvasSync"); });
    host.querySelector("[data-gradebook-sync-open]")?.addEventListener("click", () => { void dialog("openCanvasSync"); });
    host.querySelector("[data-gradebook-iclicker-open]")?.addEventListener("click", () => { void dialog("openIClickerImport"); });
    host.querySelector("[data-gradebook-nightly-signin]")?.addEventListener("click", () => { void dialog("openNightlySignIn"); });
    host.querySelector("[data-gradebook-nightly-skip]")?.addEventListener("click", () => { void dialog("skipNightlyWaiting"); });
    host.querySelector("[data-gradebook-uncounted-open]")?.addEventListener("click", (event) => {
      void dialog("openColumn", /** @type {HTMLElement} */ (event.currentTarget).getAttribute("data-gradebook-uncounted-open"));
    });
  }

  function drawActivity() {
    const host = grid.page.querySelector("[data-gradebook-activity]");
    const requests = current.view.requests || [];
    const details = host.querySelector("details"), open = details?.open, focused = Boolean(details?.contains(document.activeElement));
    host.innerHTML = requests.length ? `<details class="disclosure gradebook-activity"${open ? " open" : ""}><summary><span>Recent changes</span><span class="disclosure-hint">${requests.length}</span></summary><div class="disclosure-body"><ol>${requests.map((request) => `<li><span>${escape(request.summary?.source === "absence" ? "Excused absence (iClicker credit)" : request.summary?.source === "iclicker_credit" ? "iClicker credit you gave" : KINDS[request.kind] || request.kind)}</span><small>${escape([when(request.created_at), actorText(request.actor), summaryText(request.summary, current.view.setup?.items || [])].filter(Boolean).join(" · "))}</small></li>`).join("")}</ol><a class="link-arrow gradebook-activity-all" href="${escape(`${current.base}/changes`)}">All changes</a></div></details>` : "";
    if (focused) /** @type {HTMLElement|null} */ (host.querySelector("summary"))?.focus({ preventScroll: true });
  }

  // A scores write answers with the latest request log (`requests`), which replaces "Recent changes" in place: the grid and
  // any edit stay. A list older than the one shown (a slower response) is ignored.
  function adoptRequests(termId, requests) {
    if (current?.termId !== termId) return;
    const shown = current.view.requests || [], head = shown[0];
    const newer = !head || !requests.length ? requests.length >= shown.length
      : requests.some((request) => request.id === head.id) || String(requests[0].created_at) > String(head.created_at);
    if (!newer) return;
    current.view.requests = requests;
    if (grid?.termId === termId && grid.page.isConnected) drawActivity();
  }

  function applySearch(keepFocus = true) {
    if (!grid?.table) { updateSummary(); return; }
    const state = remember(current.termId);
    const words = fold(state.search).split(/\s+/).filter(Boolean);
    const visible = [];
    for (const tr of grid.allRows) {
      const student = grid.studentByRow.get(tr.dataset.row);
      const name = fold(student?.name);
      const show = words.every((word) => name.includes(word)) && Boolean(student) && attentionMatch(current.view, student, state.filter, grid.overdue, grid.differRows);
      tr.hidden = !show;
      if (show) visible.push(tr);
    }
    grid.rows = visible;
    if (grid.historyButton) grid.historyButton.hidden = true;
    if (keepFocus && grid.active && grid.pos.r >= 0 && grid.active.closest("tr")?.hidden) {
      const target = elementAt(visible.length ? 0 : -1, grid.pos.c);
      if (target) activate(target, visible.length ? 0 : -1, grid.pos.c);
    } else if (grid.active && grid.pos.r >= 0) grid.pos.r = visible.indexOf(grid.active.closest("tr"));
    updateSummary();
  }

  function updateSummary() {
    const node = grid?.page.querySelector("[data-gradebook-summary]");
    if (!node) return;
    const view = current.view;
    const total = grid.model?.students.length ?? 0, shown = grid.table ? grid.rows.length : total;
    let average = view.results?.overall?.mean_percent, label = "class average";
    if (current.section) {
      const values = grid.model.students.map((student) => view.results?.students?.[student.row]?.percent).filter((value) => value !== null && value !== undefined && value !== "").map(Number);
      average = values.length ? (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(2) : null;
      label = "section average";
    }
    const students = shown === total ? plural(total, "student") : `${shown} of ${plural(total, "student")}`;
    node.textContent = `${students} · ${plural(grid.model?.columnCount ?? 0, "column")} · ${average ? `${label} ${average}%` : `no ${label} yet`}`;
  }

  // The attention strip under the toolbar: the letters students have now (each a filter), the blanks past due (a filter that
  // also highlights them) and "Below" a letter. Counts come from the server's results and the cells on screen; the filter is
  // view memory only and one at a time. Nothing here marks, excuses or changes a score.
  function drawAttention() {
    const host = grid?.page.querySelector("[data-gradebook-attention]");
    if (!host || !grid.model) return;
    const { view } = current, filter = remember(current.termId).filter;
    const students = grid.model.students;
    const letters = letterCounts(view, students).filter(({ letter, count }) => count || (filter.kind === "letter" && filter.value === letter));
    const due = grid.overdue?.total || 0;
    const scale = (view.setup?.scale || []).map((entry) => entry.letter);
    const pressed = (kind, value = "") => filter.kind === kind && filter.value === value;
    const chip = (key, label, count, title, isPressed) => `<button type="button" data-attention="${escape(key)}" aria-pressed="${isPressed}" title="${escape(title)}">${escape(label)}${count === null ? "" : ` <b>${escape(count)}</b>`}</button>`;
    const parts = [];
    if (letters.length) parts.push(`<div class="filter-chips gradebook-letters" role="group" aria-label="Students by letter">${letters.map(({ letter, count }) => chip(`letter:${letter}`, letter, count, `Show the ${plural(count, "student")} with ${letter}`, pressed("letter", letter))).join("")}</div>`);
    if (letters.length && scale.length > 1) parts.push(`<label class="gradebook-below">Below<select data-attention-below aria-label="Show students below a letter"><option value="">a letter…</option>${scale.slice(0, -1).map((letter) => `<option value="${escape(letter)}"${pressed("below", letter) ? " selected" : ""}>${escape(letter)}</option>`).join("")}</select></label>`);
    // ZERO ONCE DUE: the same chip counts the empty cells the server counts as 0 (makeups, DR bookings and papers in wait).
    const zeros = view.setup?.ungraded === "due";
    if (due || filter.kind === "overdue") parts.push(`<div class="filter-chips gradebook-due" role="group" aria-label="${zeros ? "Empty cells counted as 0" : "Blanks past due"}">${chip("overdue", zeros ? `${plural(due, "empty cell")} counted as 0` : `${plural(due, "blank")} past due`, null, zeros ? "Empty cells past due, of certified grading or of grades posted in Canvas that count as 0; makeups owed, DR bookings and papers still being graded are left out. Shows those students and highlights the cells" : "Counted columns past their due date without a value (DR-center bookings left out); shows those students and highlights the cells", pressed("overdue"))}</div>`);
    // DIFFERS FROM GRADING: the cells on screen that differ from grading's final grade now; the chip shows those students.
    const differing = grid.differs?.size ? students.reduce((count, student) => count + grid.model.items.filter((item) => !item.collapsed && grid.differs.has(cellKey(item.id, student.row))).length, 0) : 0;
    if (differing || filter.kind === "differs") parts.push(`<div class="filter-chips gradebook-differ-filter" role="group" aria-label="Grades that differ from Grading">${chip("differs", `${plural(differing, "grade")} ${differing === 1 ? "differs" : "differ"} from Grading`, null, "Grades in the gradebook that are not what Grading has now; shows those students (their cells are marked)", pressed("differs"))}</div>`);
    if (filter.kind) parts.push('<button type="button" class="text-button gradebook-clear-filter" data-attention="clear">Show every student</button>');
    const markup = parts.join("");
    host.hidden = !markup;
    if (host.innerHTML === markup) return;
    const focused = /** @type {HTMLElement|null} */ (document.activeElement)?.closest?.("[data-attention], [data-attention-below]");
    const again = focused && host.contains(focused) ? (focused.getAttribute("data-attention") || "below") : "";
    host.innerHTML = markup;
    if (again) focusAttention(again);
  }

  function focusAttention(key) {
    const host = grid?.page.querySelector("[data-gradebook-attention]");
    const node = key === "below" ? host?.querySelector("[data-attention-below]") : host?.querySelector(`[data-attention="${CSS.escape(key)}"]`);
    /** @type {HTMLElement|null} */ (node || host?.querySelector("[data-attention]"))?.focus({ preventScroll: true });
  }

  // ---- DIFFERS FROM GRADING (GRADEBOOK, 2026-10-07) ------------------------------------------------------------------
  // After every View the grid draws (and every write, Refresh, or return to the window), the grading check reads which cells of
  // linked columns differ from grading's final grades now. It reads only; its answer marks the cells, their headings, the
  // attention bar and one notice in place, never redrawing the grid. One check at a time: a View that arrives meanwhile asks
  // for one more, read when the first answers. A failed check leaves the marks as they were until the next.
  async function checkGrading(termId) {
    checkNext = termId;
    if (checking) return;
    checking = true;
    try {
      while (checkNext) {
        const term = checkNext;
        checkNext = "";
        if (current?.termId !== term || current.page !== "grid") continue;
        const section = scopeOf(term);
        let data = null;
        try {
          data = await api(`/api/gradebook/terms/${enc(term)}/grading-check${section ? `?section=${enc(section)}` : ""}`, "GET", undefined, {}, 120000);
        } catch { data = null; }
        checkedAt = Date.now();
        if (!data || current?.termId !== term || scopeOf(term) !== (data.section_id || "")) continue;
        current.check = data;
        applyCheck();
      }
    } finally {
      checking = false;
    }
  }

  // The check's rows that still describe the cells on screen (differences() drops any whose cell changed since).
  function setDiffers() {
    if (!grid) return;
    grid.differs = differences(current?.view, current?.check);
    grid.differRows = new Set([...grid.differs.values()].map((entry) => entry.row));
  }

  // Marks the cells and headings in place, then the notice, the attention chip and the key.
  function applyCheck() {
    if (!current || !grid?.page?.isConnected || grid.termId !== current.termId || !grid.model) return;
    setDiffers();
    const marks = grid.differs;
    if (grid.table?.isConnected) {
      for (const td of grid.table.querySelectorAll("td.is-differs")) {
        if (!marks.has(cellKey(td.getAttribute("data-item"), td.getAttribute("data-row")))) td.classList.remove("is-differs");
      }
      for (const entry of marks.values()) {
        const tr = grid.rowMap.get(entry.row), index = grid.colIndex.get(entry.item);
        const td = tr && index !== undefined ? tr.cells[index + 1] : null;
        if (td && grid.editing?.td !== td && !td.classList.contains("is-pending")) td.classList.add("is-differs");
      }
      grid.model.items.forEach((item, index) => {
        const th = grid.heads[index];
        if (!th || item.collapsed) return;
        const count = differsCount(marks, item.id, grid.model.students);
        th.classList.toggle("gb-differs", count > 0);
        const button = th.querySelector(".gb-head-button");
        button?.querySelector("[data-head-differs]")?.remove();
        if (count) button?.insertAdjacentHTML("beforeend", differsChip(count));
      });
      grid.hover = null; // the next hover reads the cell's title again
    }
    drawDiffers();
    drawAttention();
    drawKey();
    if (remember(current.termId).filter.kind === "differs") applySearch();
  }

  // One warning over the grid: a line per column that differs, with Show, and Certify again or Pull.
  function drawDiffers() {
    const host = grid?.page.querySelector("[data-gradebook-differs]");
    if (!host || !grid.model) return;
    const lines = differsSummary(current.view, current.check, grid.differs, grid.model.students, when);
    let markup = "";
    if (lines.length) {
      const count = lines.reduce((sum, line) => sum + line.count, 0);
      const title = count ? `${plural(count, "grade")} here ${count === 1 ? "differs" : "differ"} from Grading` : "Grades in other sections differ from Grading";
      const action = (line) => line.fix === "certify" && line.plan_id
        ? `<a class="button" href="#grading/${escape(enc(line.plan_id))}?tab=export" title="Certify records the grades in this column">Certify again</a>`
        : line.fix === "pull" && line.changed ? `<button type="button" class="button" data-differs-pull="${escape(line.item)}">Pull</button>` : ""; // a pull from here covers the students shown
      markup = `<div class="callout gradebook-notice gradebook-differs" data-tone="warning" role="status"><strong class="callout-title">${escape(title)}</strong><ul class="gradebook-differs-list">${lines.map((line) => `<li><span><strong>${escape(line.title)}</strong> · ${escape(line.text)}</span><span class="gradebook-differs-actions">${line.first ? `<button type="button" class="text-button" data-differs-show="${escape(line.item)}">Show</button>` : ""}${action(line)}</span></li>`).join("")}</ul></div>`;
    }
    if (grid.differsMarkup === markup) return;
    grid.differsMarkup = markup;
    host.innerHTML = markup;
  }

  // A column's first differing cell, in focus: its category opened, and the students that differ shown if a search or filter
  // hides it.
  function showDiffering(itemId) {
    if (!grid?.table || !current) return;
    const state = remember(current.termId);
    const item = (current.view.setup?.items || []).find((entry) => entry.id === itemId);
    if (item?.category_id && state.collapsed.has(item.category_id)) {
      state.collapsed.delete(item.category_id);
      redrawGridKeepingPlace();
    }
    const find = () => grid.rows.findIndex((tr) => grid.differs?.has(cellKey(itemId, tr.dataset.row)));
    let r = find();
    if (r < 0 && (state.search || state.filter.kind)) {
      state.search = "";
      const search = /** @type {HTMLInputElement|null} */ (grid.page.querySelector("[data-gradebook-search]"));
      if (search) search.value = "";
      setFilter({ kind: "differs", value: "" }, "differs");
      r = find();
    }
    const c = grid.colIndex.get(itemId);
    const td = r >= 0 && c !== undefined ? elementAt(r, c) : null;
    if (!td) return;
    activate(td, r, c);
    td.focus({ preventScroll: true });
    reveal(td);
  }

  // One attention filter at a time. The past-due highlight belongs to its filter, so the grid is drawn again when that filter
  // comes or goes; any other only hides rows.
  function setFilter(next, focus = "") {
    if (!grid) return;
    const state = remember(current.termId), was = state.filter.kind;
    state.filter = next;
    if (was === "overdue" || next.kind === "overdue") redrawGridKeepingPlace();
    else { drawAttention(); applySearch(); drawKey(); }
    focusAttention(next.kind ? (next.kind === "below" ? "below" : focus) : "");
  }

  function applyWidths() {
    const table = grid?.table;
    if (!table) return;
    const size = WIDTHS;
    let total = 0;
    for (const col of table.querySelectorAll("col")) {
      const width = size[col.dataset.w] || size.item;
      col.style.setProperty("width", `${width}px`);
      total += width;
    }
    table.style.setProperty("width", `${total}px`);
  }

  // Overall and Letter stay at the right edge while two score columns still fit beside them and the names.
  // The category percentages join them when there are at most three and three score columns still fit; otherwise they
  // scroll, and pass beneath the frozen block's edge (drawn by gradebook.css) rather than showing as a squeezed sliver.
  function freezeLevel(model, room) {
    const size = WIDTHS, categories = model.results.length - 2, final = size.name + size.result + size.letter;
    if (categories > 0 && categories <= 3 && final + categories * size.result + 3 * size.item <= room) return 2;
    return final + 2 * size.item <= room ? 1 : 0;
  }

  function frozenWidth(model, freeze) {
    const size = WIDTHS;
    return !freeze ? 0 : ((freeze === 2 ? model.results.length - 2 : 0) + 1) * size.result + size.letter;
  }

  // After a resize: the same decision from the grid's new width.
  function fitResults() {
    const table = grid?.table, scroller = grid?.scroller;
    if (!table || !scroller) return;
    const freeze = freezeLevel(grid.model, scroller.clientWidth);
    for (const [level, name] of Object.entries(FREEZE_CLASS)) if (name && table.classList.contains(name) !== (Number(level) === freeze)) table.classList.toggle(name);
    grid.frozenRight = frozenWidth(grid.model, freeze);
    markOverlap();
  }

  // The frozen block casts its shade only while scores pass beneath it (the grid is scrolled short of its right end).
  function markOverlap() {
    const scroller = grid?.scroller;
    if (!scroller) return;
    scroller.classList.toggle("is-overlapped", Boolean(grid.frozenRight) && scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 1);
  }

  // The grid is as tall as the window below its own top (measured, set through the CSSOM as --gb-top), so the page and the
  // grid do not both scroll. A grid that starts below half the window (a long notice or work list above it) is as tall as the
  // window instead: once the page is scrolled to it, it shows a full screen of rows rather than a short box.
  // Read in a frame after drawing and after a resize, never forcing a layout mid-render.
  function fitHeight() {
    const scroller = grid?.scroller;
    if (!scroller?.isConnected || !scroller.getClientRects().length) return; // not shown (another page): nothing to measure
    const start = Math.max(0, Math.round(scroller.getBoundingClientRect().top + window.scrollY));
    const top = start > window.innerHeight / 2 ? 0 : start;
    if (scroller.style.getPropertyValue("--gb-top") !== `${top}px`) scroller.style.setProperty("--gb-top", `${top}px`);
  }

  // The one-line key: only the marks the grid shows now.
  function drawKey() {
    const node = grid?.page.querySelector("[data-gradebook-key]");
    if (!node || !grid.model) return;
    const overdue = remember(current.termId).filter.kind === "overdue" && Boolean(grid.overdue?.total);
    const markup = (current.view.students || []).length ? keyMarkup(marksPresent(current.view, grid.model, { overdue, differs: grid.differs }), escape) : "";
    if (node.innerHTML !== markup) node.innerHTML = markup;
    node.hidden = !markup;
  }

  const frozen = (element) => Boolean(grid.frozenRight && (element.closest(".gb-sticky-right") || (grid.table.classList.contains("gb-freeze-cats") && element.closest(".gb-r-cat"))));

  // ---- Focus and movement (roving tabindex: exactly one cell of the grid is in the tab order) -------------------------
  // Columns count from the first score column (0); the heading row also has the corner's Name (-2) and Section (-1).
  function elementAt(r, c) {
    if (!grid?.table) return null;
    if (r < 0) return c < 0 ? grid.corner[c + 2] || null : grid.heads[c]?.querySelector(".gb-head-button") || null;
    if (c === -1) return grid.rows[r]?.querySelector(".gb-name-button") || null; // the student's name: their record
    return c < 0 ? null : grid.rows[r]?.cells[c + 1] || null;
  }

  function activate(element, r, c) {
    if (grid.active && grid.active !== element) {
      grid.active.tabIndex = -1;
      grid.active.classList.remove("is-active");
    }
    element.tabIndex = 0;
    element.classList.add("is-active");
    grid.active = element;
    grid.pos = { r, c };
    if (element.matches("td")) element.setAttribute("aria-label", describe(element));
    if (grid.historyButton) grid.historyButton.hidden = true;
  }

  function moveTo(r, c) {
    const lastRow = grid.rows.length - 1;
    // Down from the corner's sort buttons goes to the first score, as before; only Left from a row's first score reaches its name.
    const fromHead = grid.pos.r < 0;
    r = Math.max(-1, Math.min(r, lastRow));
    c = Math.max(r < 0 ? -grid.corner.length : fromHead ? 0 : -1, Math.min(c, grid.colCount - 1));
    const target = elementAt(r, c);
    if (!target) return;
    activate(target, r, c);
    target.focus({ preventScroll: true });
    reveal(target);
  }

  function reveal(element) {
    const scroller = grid.scroller, box = scroller.getBoundingClientRect(), rect = element.getBoundingClientRect();
    const size = WIDTHS;
    const inBody = Boolean(element.closest("tbody"));
    const top = box.top + scroller.clientTop + (inBody ? grid.thead.getBoundingClientRect().height : 0);
    const bottom = box.top + scroller.clientTop + scroller.clientHeight;
    const pinned = frozen(element);
    const stickyRight = pinned ? 0 : grid.frozenRight || 0;
    const left = box.left + scroller.clientLeft + (element.closest(".gb-corner, .gb-name") ? 0 : size.name);
    const right = box.left + scroller.clientLeft + scroller.clientWidth - stickyRight;
    if (inBody) {
      if (rect.top < top) scroller.scrollTop -= top - rect.top;
      else if (rect.bottom > bottom) scroller.scrollTop += rect.bottom - bottom;
    }
    if (!pinned && !element.closest(".gb-corner")) {
      if (rect.left < left) scroller.scrollLeft -= left - rect.left;
      else if (rect.right > right) scroller.scrollLeft += rect.right - right;
    }
    // Keep the cell on the page as well as inside the grid's own scrolling region.
    const after = element.getBoundingClientRect();
    if (after.bottom > window.innerHeight) window.scrollBy(0, after.bottom - window.innerHeight + 8);
    else if (after.top < 0) window.scrollBy(0, after.top - 8);
  }

  const itemCell = (element) => element?.matches?.("td[data-cell]") ? element : null;
  const editable = (td) => Boolean(itemCell(td)) && !td.hasAttribute("data-na") && !td.hasAttribute("data-locked");
  const cellOf = (td) => current.view.cells?.[td.dataset.item]?.[td.dataset.row] || null;

  function valueWords(item, student, cell, entry) {
    if (!applies(item, student)) return `not assigned to Section ${student.section}`;
    const shown = entry || cell;
    const status = shown && shown.status !== "cleared" ? shown.status : "";
    let words = status === "scored" ? `${trimPoints(shown.points)} of ${trimPoints(item.points)} points${above(item, shown.points) ? ", above the column’s points" : ""}`
      : status === "excused" ? "excused" : status === "missing" ? "missing, counts as 0"
      : blankWords(item, blankReason(current.view, item, student) || (drBooked(item, student) ? "dr" : null), duePosted(current.view, item))?.[0] || "no score";
    if (entry) return `${words}, saving`;
    if (status && droppedIn(current.view, item, student)) words += ", dropped";
    const by = status ? replacedBy(current.view, item, student) : null;
    if (by) words += `, replaced by ${grid.itemById.get(by)?.title || "the replacement"}`;
    if (status && item.replaces_lowest) words += replacing(current.view, item, student) ? ", replaces the lowest score" : ", not used";
    if (cell?.note) words += ", has a note";
    return words;
  }

  function describe(td) {
    const student = grid.studentByRow.get(td.parentElement.dataset.row);
    if (!student) return "";
    if (td.dataset.resultCell) {
      const result = grid.model.results.find((entry) => entry.key === td.dataset.resultCell);
      const folded = result ? null : grid.model.items.find((entry) => entry.collapsed && entry.id === td.dataset.resultCell);
      return `${student.name}, ${result?.label || folded?.title || ""}: ${td.textContent.trim() || "none"}`;
    }
    const item = grid.itemById.get(td.dataset.item);
    if (!item) return "";
    const cell = cellOf(td), entry = pending.get(pendingKey(current.termId, item.id, student.row));
    const source = !entry && cell && cell.status !== "cleared" && SOURCES[cell.source]
      ? `, ${SOURCES[cell.source].toLowerCase()}${cell.actor === "assistant" ? ` by ${actorText(cell.actor)}` : ""}` : "";
    const unknown = !entry && unsure.has(pendingKey(current.termId, item.id, student.row)) ? ", the outcome of its last change is unknown" : "";
    const differ = !entry && grid.differs?.get(cellKey(item.id, student.row));
    const differs = differ ? `, differs from Grading, which has ${gradingValue(differ.grading)}` : "";
    return `${student.name}, ${item.title}: ${valueWords(item, student, cell, entry)}${source}${unknown}${differs}`;
  }

  function cellTitle(td) {
    const item = grid.itemById.get(td.dataset.item), student = grid.studentByRow.get(td.dataset.row);
    if (!item || !student) return "";
    if (!applies(item, student)) return `${item.title} is not assigned to Section ${student.section}`;
    const cell = cellOf(td), lines = [];
    if (cell && cell.status !== "cleared") {
      lines.push(`${valueWords(item, student, cell, null).replace(/, has a note$/, "").replace(/, (dropped|replaced by .*|replaces the lowest score|not used)$/, "").replace(/, above the column’s points$/, "")}`);
      if (droppedIn(current.view, item, student)) lines.push(`Dropped: the lowest in its category, left out of the results`);
      const by = replacedBy(current.view, item, student);
      if (by) lines.push(`Replaced: ${grid.itemById.get(by)?.title || "the replacement"} is higher and takes its place`);
      if (item.replaces_lowest) lines.push(replacing(current.view, item, student) ? "Replaces the student’s lowest score in its category" : "Not used: not higher than the student’s lowest score in its category");
      lines.push(`${SOURCES[cell.source] || cell.source}${cell.actor ? ` by ${actorText(cell.actor)}` : ""}${cell.at ? ` · ${when(cell.at)}` : ""}`);
      if (cell.status === "scored" && above(item, cell.points)) lines.push(`Above the column’s ${trimPoints(item.points)} points`);
      if (cell.note) lines.push(`Note: ${cell.note}`);
    } else lines.push(blankWords(item, blankReason(current.view, item, student) || (drBooked(item, student) ? "dr" : null), duePosted(current.view, item))?.[1] || "No score");
    const differ = grid.differs?.get(cellKey(item.id, student.row));
    if (differ) lines.push(...differsLines(differ, when));
    lines.push("Double-click for the history");
    return lines.join("\n");
  }

  // Over the left edge of the cell in focus; it scrolls with the cells and passes under the sticky name column.
  function placeHistoryButton(td) {
    const button = grid.historyButton;
    if (!button) return;
    if (!editable(td) || grid.editing) { button.hidden = true; return; }
    button.style.setProperty("top", `${grid.table.offsetTop + td.offsetTop + Math.max(0, (td.offsetHeight - 20) / 2)}px`);
    button.style.setProperty("left", `${grid.table.offsetLeft + td.offsetLeft + 2}px`);
    button.hidden = false;
  }

  // ---- Events ----------------------------------------------------------------------------------------------------------
  function onFocusIn(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target.matches("tbody td")) {
      const r = grid.rows.indexOf(/** @type {HTMLTableRowElement} */ (target.parentElement));
      if (r >= 0 && target !== grid.active) activate(target, r, /** @type {HTMLTableCellElement} */ (target).cellIndex - 1);
      else if (r >= 0) grid.pos = { r, c: /** @type {HTMLTableCellElement} */ (target).cellIndex - 1 };
      placeHistoryButton(target);
    } else if (target.matches(".gb-head-button, .gb-pull")) {
      const head = target.closest("[data-col]");
      if (head) activate(target, -1, Number(/** @type {HTMLElement} */ (head).dataset.col));
    } else if (target.matches(".gb-corner .gb-sort")) activate(target, -1, Number(target.dataset.corner) - grid.corner.length);
    else if (target.matches(".gb-name-button")) {
      const r = grid.rows.indexOf(/** @type {HTMLTableRowElement} */ (target.closest("tr")));
      if (r >= 0) activate(target, r, -1);
    }
  }

  function onKeydown(event) {
    if (!grid?.table || event.defaultPrevented) return;
    const target = /** @type {HTMLElement} */ (event.target);
    if (target instanceof HTMLInputElement) return; // the editor handles its own keys
    const head = (target.closest(".gb-head-button, .gb-pull") && target.closest("[data-col]")) || target.closest(".gb-corner .gb-sort") || target.closest(".gb-name-button");
    const cell = target.matches("tbody td") ? target : null;
    if (!head && !cell) return;
    const { r, c } = grid.pos, last = grid.rows.length - 1, lastCol = grid.colCount - 1;
    const ctrl = event.ctrlKey || event.metaKey;
    if (event.altKey && event.key === "ArrowDown") {
      event.preventDefault();
      const item = c >= 0 ? grid.model.items[c] : null;
      if (item && !item.collapsed) openMenu(item.id, grid.heads[c].querySelector(".gb-head-button"), true);
      return;
    }
    let move = null;
    switch (event.key) {
      case "ArrowDown": move = [ctrl ? last : r + 1, c]; break;
      case "ArrowUp": move = [ctrl ? 0 : r - 1, c]; break;
      case "ArrowRight": move = [r, ctrl ? lastCol : c + 1]; break;
      case "ArrowLeft": move = [r, ctrl ? 0 : c - 1]; break;
      case "Home": move = ctrl ? [0, 0] : [r, 0]; break;
      case "End": move = ctrl ? [last, lastCol] : [r, lastCol]; break;
      case "PageDown": move = [Math.min(last, r + PAGE_ROWS), c]; break;
      case "PageUp": move = [r < 0 ? r : Math.max(0, r - PAGE_ROWS), c]; break;
    }
    if (move) {
      event.preventDefault();
      if (menuOpen(grid.menu)) closeMenu();
      moveTo(move[0], move[1]);
      return;
    }
    if (!cell) return; // a heading's button: Enter and Space press it
    if ((event.key === "F10" && event.shiftKey) || event.key === "ContextMenu") {
      if (itemCell(cell) && !cell.hasAttribute("data-na")) { event.preventDefault(); void showHistory(cell); }
      return;
    }
    if (!editable(cell) || ctrl || event.altKey) return;
    if (event.key === "Enter" || event.key === "F2" || event.key === " ") {
      event.preventDefault();
      startEdit(cell, entryText(pending.get(pendingKey(current.termId, cell.dataset.item, cell.dataset.row)) || cellOf(cell)), false);
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      clearCell(cell);
    } else if (event.key.length === 1 && !event.isComposing) {
      event.preventDefault();
      startEdit(cell, event.key, true);
    }
  }

  function onClick(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    const band = target.closest("[data-band-toggle]");
    if (band) { toggleBand(band.getAttribute("data-band-toggle")); return; }
    const menuButton = target.closest("[data-column-menu]");
    if (menuButton) { openMenu(menuButton.getAttribute("data-column-menu"), /** @type {HTMLElement} */ (menuButton)); return; }
    const sortButton = target.closest("[data-sort]");
    if (sortButton) { setSort(sortButton.getAttribute("data-sort")); return; }
    const pull = target.closest("[data-pull]");
    if (pull) { void dialog("openPull", pull.getAttribute("data-pull")); return; }
    const student = target.closest("[data-student]");
    if (student) { void dialog("openStudent", student.getAttribute("data-student")); return; }
    // On a touch screen, a tap on the cell already in focus starts editing (there is no keyboard to type into it).
    const td = target.closest("td");
    if (td && grid.pointer && grid.pointer !== "mouse" && grid.pressedActive && td === grid.active && editable(td) && !grid.editing) {
      startEdit(td, entryText(cellOf(td)), false);
    }
  }

  function onDoubleClick(event) {
    const target = /** @type {HTMLElement} */ (event.target);
    const td = target.closest("td");
    if (target.closest("[data-cell-history]") || !itemCell(td) || td.hasAttribute("data-na") || grid.editing) return;
    void showHistory(td);
  }

  function onHover(event) {
    const td = /** @type {HTMLElement|null} */ (/** @type {HTMLElement} */ (event.target).closest?.("td[data-cell]"));
    if (!td || td === grid.hover) return;
    grid.hover = td;
    td.title = cellTitle(td);
  }

  async function showHistory(td) {
    await dialog("openHistory", td.dataset.item, td.dataset.row);
  }

  function onCopy(event) {
    if (grid.editing) return;
    const td = /** @type {HTMLElement|null} */ (/** @type {HTMLElement} */ (event.target).closest?.("tbody td"));
    if (!td || !event.clipboardData) return;
    const text = td.dataset.resultCell ? td.textContent.trim() : td.hasAttribute("data-na") ? "" : entryText(cellOf(td));
    event.clipboardData.setData("text/plain", text);
    event.preventDefault();
  }

  function onPaste(event) {
    if (grid.editing) return;
    const td = itemCell(/** @type {HTMLElement} */ (event.target).closest?.("td"));
    const text = event.clipboardData?.getData("text/plain");
    if (!td || !text) return;
    event.preventDefault();
    const block = parseBlock(text);
    const r0 = grid.rows.indexOf(/** @type {HTMLTableRowElement} */ (td.parentElement)), c0 = /** @type {HTMLTableCellElement} */ (td).cellIndex - 1;
    const entries = [];
    let outside = 0, skipped = 0, same = 0, cleared = 0, folded = 0, locked = 0;
    for (let i = 0; i < block.length; i++) {
      for (let j = 0; j < block[i].length; j++) {
        const tr = grid.rows[r0 + i], item = grid.model.items[c0 + j];
        if (!tr || !item) { if (block[i][j].trim()) outside++; continue; }
        if (item.collapsed) { if (block[i][j].trim()) folded++; continue; } // a collapsed category's percentage is not a column
        const cell = tr.cells[c0 + j + 1];
        if (cell.hasAttribute("data-na")) { if (block[i][j].trim()) skipped++; continue; }
        if (cell.hasAttribute("data-locked")) { if (block[i][j].trim()) locked++; continue; } // Canvas's scores only
        const entry = parseEntry(block[i][j]);
        if (entry.error) { toast(`Nothing was pasted. Row ${i + 1}, column ${j + 1} of the copied cells: ${entry.error}`, true); return; }
        if (pending.has(pendingKey(current.termId, item.id, tr.dataset.row))) { toast("Nothing was pasted: some of these cells are still being saved. Paste again in a moment.", true); return; }
        if (sameEntry(cellOf(cell), entry)) { same++; continue; }
        if (entry.status === "cleared") cleared++;
        entries.push({ item: item.id, row: tr.dataset.row, entry });
      }
    }
    const notes = [same ? `${same} already had that value` : "", skipped ? `${skipped} not assigned to the student’s section were skipped` : "",
      locked ? `${locked} in columns Canvas grades were skipped (their scores come from Canvas)` : "", folded ? `${folded} over a collapsed category were skipped` : "", outside ? `${outside} past the edge of the grid were skipped` : ""].filter(Boolean).join("; ");
    if (!entries.length) { toast(`Nothing to change${notes ? `: ${notes}` : ""}.`); return; }
    if (entries.length > MAX_CHANGES) { toast(`Paste at most ${MAX_CHANGES} scores at once.`, true); return; }
    const done = [entries.length > cleared ? `Pasted ${plural(entries.length - cleared, "score")}` : "", cleared ? `${entries.length > cleared ? "cleared" : "Cleared"} ${plural(cleared, "cell")} left blank in the copy` : ""].filter(Boolean).join(" and ");
    const label = `${done}${notes ? ` (${notes})` : ""}.`;
    // A paste that replaces or clears values already there, or writes many cells, asks first; the cells as they were when
    // pasted are checked again before the one write, so the confirmation never covers a value it did not show.
    const owner = current, termId = owner.termId;
    const seen = entries.map(({ item, row }) => current.view.cells?.[item]?.[row]?.id ?? null);
    const replaced = entries.filter(({ item, row }) => !blank(current.view.cells?.[item]?.[row]));
    if (!pasteNeedsConfirm(entries.length, replaced.length)) { void submit(entries, label); return; }
    const name = (row) => grid.studentByRow.get(row)?.name || row, title = (item) => grid.itemById.get(item)?.title || item;
    void dialog("openPasteConfirm", {
      count: entries.length, cleared,
      replaced: replaced.map(({ item, row, entry }) => ({ name: name(row), column: title(item), from: current.view.cells[item][row], to: entry })),
      onConfirm: () => {
        if (current !== owner || !grid?.table?.isConnected) return "The gradebook moved on; paste again.";
        if (entries.some(({ item, row }, index) => (current.view.cells?.[item]?.[row]?.id ?? null) !== seen[index] || pending.has(pendingKey(termId, item, row)))) {
          return "Some of these cells changed since the paste. Nothing was written: paste again.";
        }
        void submit(entries, label);
        return "";
      },
    });
  }

  // Collapse a category's columns to its percentage, or show them again (view memory only). Focus follows to the column
  // standing for the category, or to its first column when shown again.
  function toggleBand(categoryId, collapse = null) {
    if (!categoryId || !grid) return;
    const { collapsed } = remember(current.termId);
    const next = collapse === null ? !collapsed.has(categoryId) : collapse;
    if (next) collapsed.add(categoryId); else collapsed.delete(categoryId);
    const hadFocus = grid.page.contains(document.activeElement) || document.activeElement === document.body;
    redrawGridKeepingPlace();
    if (!grid?.table) return;
    const index = next ? grid.colIndex.get(`band:${categoryId}`)
      : grid.model.items.findIndex((item) => !item.collapsed && item.category_id === categoryId);
    const head = index !== undefined && index >= 0 ? grid.heads[index]?.querySelector(".gb-head-button") : null;
    if (head && hadFocus) { activate(head, -1, index); head.focus({ preventScroll: true }); reveal(head); }
  }

  function setSort(key) {
    const state = remember(current.termId);
    const [kind, ...rest] = String(key).split(":");
    const next = kind === "cat" ? { key: "cat", id: rest.join(":") } : { key: kind };
    const same = state.sort.key === next.key && (state.sort.id || "") === (next.id || "");
    state.sort = { ...next, dir: same && state.sort.dir === "asc" ? "desc" : "asc" };
    redrawGridKeepingPlace();
  }

  function redrawGridKeepingPlace() {
    if (!grid) return;
    const sortControl = /** @type {HTMLElement|null} */ (document.activeElement)?.closest?.("[data-sort]");
    const keep = grid.table?.isConnected ? { top: grid.scroller.scrollTop, left: grid.scroller.scrollLeft, focus: activeKeys(), hadFocus: !sortControl && grid.page.contains(document.activeElement) } : null;
    if (grid.editing) finishEdit("blur");
    drawGrid(keep);
    if (sortControl && grid.table) {
      const again = /** @type {HTMLElement|null} */ ([...grid.table.querySelectorAll("[data-sort]")].find((node) => node.getAttribute("data-sort") === sortControl.getAttribute("data-sort")) || null);
      again?.focus({ preventScroll: true });
    }
  }

  // ---- Editing ---------------------------------------------------------------------------------------------------------
  function startEdit(td, initial, typed) {
    if (grid.editing || !editable(td)) return;
    if (pending.has(pendingKey(current.termId, td.dataset.item, td.dataset.row))) { toast("This score is still being saved."); return; }
    const input = document.createElement("input");
    input.type = "text";
    input.className = "gb-input";
    input.value = initial;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("enterkeyhint", "done");
    input.setAttribute("aria-label", `${describe(td)}. Type points, EX for excused, MI for missing, or nothing to clear.`);
    grid.editing = { td, input, typed };
    if (grid.historyButton) grid.historyButton.hidden = true;
    td.classList.add("is-editing");
    td.replaceChildren(input);
    input.focus({ preventScroll: true });
    if (typed) input.setSelectionRange(input.value.length, input.value.length);
    else input.select();
    input.addEventListener("keydown", onEditKey);
    input.addEventListener("input", () => input.removeAttribute("aria-invalid"));
    input.addEventListener("blur", () => { if (grid?.editing?.input === input) finishEdit("blur"); });
  }

  function onEditKey(event) {
    const editing = grid?.editing;
    if (!editing) return;
    const key = event.key;
    if (key === "Escape") { event.preventDefault(); event.stopPropagation(); finishEdit("cancel"); return; }
    let step = null;
    if (key === "Enter") step = event.shiftKey ? [-1, 0] : [1, 0];
    else if (key === "Tab") step = event.shiftKey ? [0, -1] : [0, 1];
    else if (key === "ArrowDown" || key === "ArrowUp") step = key === "ArrowDown" ? [1, 0] : [-1, 0];
    else if (editing.typed && !event.shiftKey && (key === "ArrowRight" || key === "ArrowLeft")) step = key === "ArrowRight" ? [0, 1] : [0, -1];
    if (!step) return;
    event.preventDefault();
    event.stopPropagation();
    finishEdit("commit", step);
  }

  function finishEdit(mode, step = null) {
    if (mode === 'blur' && isCaptureHeld(grid?.editing?.input)) return;
    const editing = grid?.editing;
    if (!editing) return;
    const { td, input } = editing;
    if (mode === "cancel") {
      grid.editing = null;
      repaintCell(td);
      td.focus({ preventScroll: true });
      return;
    }
    const entry = parseEntry(input.value);
    if (entry.error) {
      toast(entry.error, true);
      if (mode === "blur") { grid.editing = null; repaintCell(td); return; }
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return;
    }
    grid.editing = null;
    const cell = cellOf(td);
    if (sameEntry(cell, entry)) repaintCell(td);
    else submit([{ item: td.dataset.item, row: td.dataset.row, entry }]);
    if (mode === "blur") return;
    if (step) moveTo(grid.pos.r + step[0], grid.pos.c + step[1]);
    else td.focus({ preventScroll: true });
  }

  function clearCell(td) {
    const cell = cellOf(td);
    if (!cell || cell.status === "cleared") return;
    if (pending.has(pendingKey(current.termId, td.dataset.item, td.dataset.row))) { toast("This score is still being saved."); return; }
    submit([{ item: td.dataset.item, row: td.dataset.row, entry: { status: "cleared" } }]);
  }

  // ---- Writes: one POST per commit, one at a time; never retried by the page ------------------------------------------
  function submit(entries, label = "") {
    const { termId, view } = current;
    const section = scopeOf(termId);
    const changes = entries.map(({ item, row, entry }) => ({
      item_id: item, row, base: view.cells?.[item]?.[row]?.id ?? null, status: entry.status,
      ...(entry.status === "scored" ? { points: entry.points } : {}),
    }));
    // The same change sent again after an unknown outcome reuses its request ID, so the server answers with what it did.
    const signature = JSON.stringify([termId, section, changes]);
    const batch = uncertain.get(signature) || { signature, termId, section, requestId: crypto.randomUUID(), changes, entries, label };
    return send(batch);
  }

  // One write of one batch: the grid shows its cells as saving until the answer. A known refusal forgets the batch; an unknown
  // outcome keeps it (its cells marked, the notice's Check now) with its request ID. Resolves when the answer is handled.
  function send(batch) {
    const { termId, section, requestId, changes, entries, signature, label } = batch;
    const keys = entries.map(({ item, row }) => pendingKey(termId, item, row));
    entries.forEach(({ item, row, entry }, index) => { pending.set(keys[index], entry); repaint(termId, item, row); });
    writes = writes.then(async () => {
      const again = uncertain.has(signature);
      try {
        const result = await api(`/api/gradebook/terms/${enc(termId)}/scores`, "POST", { request_id: requestId, changes, ...(section ? { section_id: section } : {}) }, {}, 60000);
        forget(batch);
        keys.forEach((key) => pending.delete(key));
        if (scopeOf(termId) === section) applyScores(termId, result, entries);
        if (label) toast(label);
        // An answer to a write whose outcome was unknown may repeat what the server did then: read the whole gradebook, so
        // nothing older is shown over a newer value.
        if (again) {
          toast(`Checked: ${plural(changes.length, "change")} ${changes.length === 1 ? "is" : "are"} saved.`);
          try { await reload(termId); } catch (failure) { toast(failure?.message || "The gradebook could not be refreshed.", true); }
        }
      } catch (error) {
        keys.forEach((key) => pending.delete(key));
        const status = error?.status || 0;
        const stale = status === 409 && Array.isArray(error?.body?.stale);
        if (status >= 400 && status < 500) forget(batch);
        else {
          uncertain.set(signature, batch);
          keys.forEach((key) => unsure.add(key));
        }
        entries.forEach(({ item, row }) => repaint(termId, item, row));
        if (current?.termId === termId && grid?.termId === termId && grid.page.isConnected) drawUncertain();
        if (stale) {
          toast(again ? "Checked: nothing was saved, and these cells were changed elsewhere since. The latest is shown." : "This score was changed elsewhere; the latest is shown.", true);
          try { await reload(termId); } catch (failure) { toast(failure?.message || "The gradebook could not be refreshed.", true); }
        } else if (!status || status >= 500) {
          toast(`${error?.message || "The score could not be saved."} It may or may not be saved: use Check now above the grid.`, true);
        } else toast(error?.message || "The score could not be saved.", true);
      }
    });
    return writes;
  }

  // A batch answered (saved, or refused for a known reason): its cells are no longer marked, and the notice follows.
  function forget(batch) {
    if (!uncertain.delete(batch.signature)) return;
    const still = new Set([...uncertain.values()].flatMap((other) => other.entries.map(({ item, row }) => pendingKey(other.termId, item, row))));
    for (const { item, row } of batch.entries) {
      const key = pendingKey(batch.termId, item, row);
      if (!still.has(key)) unsure.delete(key);
    }
    if (current?.termId === batch.termId && grid?.termId === batch.termId && grid.page.isConnected) {
      batch.entries.forEach(({ item, row }) => repaint(batch.termId, item, row));
      drawUncertain();
    }
  }

  // "Check now": every write of this term whose outcome is unknown, sent again exactly as it was (same changes, same request ID),
  // one at a time. Only on the instructor's click.
  async function checkUncertain(termId, button) {
    const batches = [...uncertain.values()].filter((batch) => batch.termId === termId);
    if (!batches.length) { drawUncertain(); return; }
    setBusy(button, true);
    button.textContent = "Checking…";
    await Promise.all(batches.map((batch) => send(batch)));
    if (current?.termId === termId && grid?.page.isConnected) drawUncertain(true);
  }

  function drawUncertain(force = false) {
    const host = grid?.page.querySelector("[data-gradebook-uncertain]");
    if (!host) return;
    const batches = [...uncertain.values()].filter((batch) => batch.termId === current.termId);
    const count = batches.reduce((sum, batch) => sum + batch.changes.length, 0);
    const signature = batches.map((batch) => batch.requestId).join(",");
    if (!force && host.dataset.shown === signature) return;
    host.dataset.shown = signature;
    host.innerHTML = batches.length ? `<div class="callout gradebook-notice" data-tone="warning" role="status" data-gradebook-uncertain-notice><strong class="callout-title">${escape(`The outcome of ${plural(count, "change")} is unknown`)}</strong><p>No answer came back. Check now sends the same ${batches.length === 1 ? "write" : "writes"} again; the server never saves ${count === 1 ? "it" : "them"} twice.</p><button type="button" class="button callout-action" data-gradebook-check>Check now</button></div>` : "";
  }

  /** Merge a scores write ({cells, results, requests}) into the View and update only what it touched. */
  function applyScores(termId, result, entries = null) {
    if (!result || current?.termId !== termId) return;
    current.changes++;
    const touched = mergeScores(current.view, result);
    if (Array.isArray(result.requests)) adoptRequests(termId, result.requests);
    for (const { item, row } of entries || []) touched.push([item, row]);
    if (!grid?.table?.isConnected || grid.termId !== termId) return;
    setDiffers(); // a written cell is no longer the value the last check was about
    for (const [item, row] of touched) repaint(termId, item, row);
    refreshDerived();
    applyCheck();
    // Only a column linked to grading can differ from it: a write to another needs no new check.
    const linked = new Set((current.view.setup?.items || []).filter((item) => item.link?.kind === "grading").map((item) => item.id));
    if (touched.some(([item]) => linked.has(item))) void checkGrading(termId);
  }

  function repaint(termId, item, row) {
    if (!grid?.table?.isConnected || grid.termId !== termId) return;
    const tr = grid.rowMap.get(row), index = grid.colIndex.get(item);
    if (!tr || index === undefined) return;
    repaintCell(tr.cells[index + 1]);
  }

  function repaintCell(td) {
    if (!td || grid.editing?.td === td) return;
    const item = grid.itemById.get(td.dataset.item), student = grid.studentByRow.get(td.dataset.row);
    if (!item || !student) return;
    const key = pendingKey(current.termId, item.id, student.row);
    const overdue = remember(current.termId).filter.kind === "overdue" && Boolean(grid.overdue?.rows.get(student.row)?.includes(item.id));
    const parts = cellParts(item, student, cellOf(td), { pending: pending.get(key) || null, target: grid.model.highlight === item.id, esc: escape,
      dropped: leftOut(current.view, item, student), uncertain: unsure.has(key), overdue, reason: blankReason(current.view, item, student),
      differs: Boolean(grid.differs?.has(cellKey(item.id, student.row))) });
    td.className = parts.cls + (td === grid.active ? " is-active" : "");
    td.innerHTML = parts.html;
    if (grid.hover === td) grid.hover = null;
    td.removeAttribute("title");
    if (td === grid.active) {
      td.setAttribute("aria-label", describe(td));
      if (document.activeElement === td) placeHistoryButton(td);
    } else td.removeAttribute("aria-label");
  }

  // Results, column means and the summary after a write (a student's change can move every average).
  function refreshDerived() {
    const view = current.view, model = grid.model, first = model.items.length + 1;
    for (const tr of grid.allRows) {
      const student = grid.studentByRow.get(tr.dataset.row);
      model.results.forEach((result, index) => {
        const td = tr.cells[first + index], text = resultValue(view, student, result);
        if (td && td.textContent !== text) td.textContent = text;
      });
      // A collapsed category's percentage, and which scores its category now drops (a write can change the lowest).
      model.items.forEach((item, index) => {
        const td = tr.cells[index + 1];
        if (!td || td === grid.editing?.td) return;
        if (item.collapsed) {
          const text = resultValue(view, student, { kind: "cat", id: item.category.id });
          if (td.textContent !== text) td.textContent = text;
          return;
        }
        if (!item.category_id || td.classList.contains("is-pending")) return;
        const cell = view.cells?.[item.id]?.[student.row];
        const dropped = Boolean(cell && cell.status !== "cleared") && leftOut(view, item, student);
        if (td.classList.contains("is-dropped") !== dropped) td.classList.toggle("is-dropped", dropped);
      });
    }
    model.items.forEach((item, index) => {
      const stats = view.results?.items?.[item.id] || {};
      const node = grid.heads[index]?.querySelector("[data-head-mean]");
      const text = item.collapsed ? resultMean(view, { kind: "cat", id: item.category.id })
        : stats.mean !== null && stats.mean !== undefined ? `avg ${shownPoints(stats.mean).text}` : "avg —";
      if (node && node.textContent !== text) node.textContent = text;
    });
    model.results.forEach((result, index) => {
      const node = grid.heads[model.items.length + index]?.querySelector("[data-head-mean]");
      const text = resultMean(view, result);
      if (node && node.textContent !== text) node.textContent = text;
    });
    if (grid.active?.matches("td")) grid.active.setAttribute("aria-label", describe(grid.active));
    // The attention counts follow the write; the rows shown stay until the instructor changes a filter or the search, so a row
    // being worked on never vanishes under the cursor.
    grid.overdue = pastDue(view, model.students, localToday(), current.section);
    drawAttention();
    drawKey();
    updateSummary();
  }

  // ---- The column menu -------------------------------------------------------------------------------------------------
  // `fromKey`: opened by Alt+Down, never a press that just light-dismissed the same menu.
  function openMenu(itemId, trigger, fromKey = false) {
    const menu = grid?.menu, item = grid?.itemById.get(itemId);
    if (!menu || !item || item.collapsed || !trigger) return;
    if (menuOpen(menu) && menu.dataset.item === itemId) { closeMenu(); return; }
    if (!fromKey && grid.menuClosed?.item === itemId && performance.now() - grid.menuClosed.at < 400) { grid.menuClosed = null; return; }
    closeMenu();
    const view = current.view, stats = view.results?.items?.[itemId] || {}, link = view.links?.[itemId];
    const sort = remember(current.termId).sort;
    const ascending = sort.key === "item" && sort.id === itemId && sort.dir === "asc";
    const category = grid.model.hasBands ? (view.setup?.categories || []).find((entry) => entry.id === item.category_id) : null;
    const value = (text) => (text === null || text === undefined || text === "" ? "—" : trimPoints(text));
    // The empty cells of the rows shown now (section filter, name search and attention filter), for "Mark N missing" /
    // "Excuse N": one write after a confirmation. N is what the confirmation writes by default: DR-booked cells are named apart.
    const empty = emptyCells(view, item, shownStudents());
    const booked = empty.filter((cell) => cell.dr).length, regular = empty.length - booked;
    const fillLabel = (verb, tail) => (regular ? `${verb} ${plural(regular, "empty cell")}${tail}${booked ? ` (+${booked} with a DR booking)` : ""}…` : `${verb} ${plural(booked, "empty cell")} with a DR booking${tail}…`);
    const fill = empty.length ? `<button type="button" data-menu-action="fill-missing">${icon("edit")}${escape(fillLabel("Mark", " missing"))}</button><button type="button" data-menu-action="fill-excused">${icon("edit")}${escape(fillLabel("Excuse", ""))}</button>` : "";
    // POSTING GRADES: a standing choice, carried out in every Canvas course the column goes to by each sync from now on.
    const choice = view.canvas_posting?.[itemId]?.choice;
    const posting = view.canvas?.linked_sections && item.canvas !== "off" ? `${choice !== "post" ? `<button type="button" data-menu-action="post-canvas" data-outward>${icon("arrow-up-right")}Post in Canvas at each sync…</button>` : ""}${choice !== "hide" ? `<button type="button" data-menu-action="hide-canvas" data-outward>${icon("close")}Hide in Canvas at each sync…</button>` : ""}${choice ? `<button type="button" data-menu-action="rule-canvas">${icon("refresh")}Back to the category’s posting…</button>` : ""}` : "";
    const postingNote = postingTitle(view.canvas_posting?.[itemId]);
    menu.innerHTML = `<p class="gradebook-menu-title">${escape(item.title)}<small>${escape([`${trimPoints(item.points)} points`, item.due_date ? `due ${shortDate(item.due_date)}` : "", item.counts === false ? "not counted" : "", item.extra_credit ? "extra credit" : "", item.replaces_lowest ? "replaces the lowest score if higher" : ""].filter(Boolean).join(" · "))}</small></p>
      <div class="gradebook-menu-actions" role="group" aria-label="${escape(`${item.title} column`)}">
        <button type="button" data-menu-action="edit">${icon("edit")}Edit column</button>
        ${item.link?.kind === "grading" ? `<button type="button" data-menu-action="pull">${icon("download")}Pull from grading</button>` : ""}
        ${link?.plan_id ? `<a href="#grading/${enc(link.plan_id)}" data-menu-action="grading">${icon("grading")}Open in Grading</a>` : ""}
        ${link?.event_id ? `<a href="#exams/${enc(link.event_id)}" data-menu-action="exam">${icon("exam")}Open exam</a>` : item.assignment_id ? `<a href="#exams/assessment/${enc(item.assignment_id)}/${enc(current.termId)}" data-menu-action="exam">${icon("exam")}Open exam</a>` : ""}
        <button type="button" data-menu-action="sort" data-dir="${ascending ? "desc" : "asc"}">${icon("sort")}Sort by this column, ${ascending ? "highest first" : "lowest first"}</button>
        ${category ? `<button type="button" data-menu-action="collapse">${icon("grid")}Collapse ${escape(category.name)} to its percentage</button>` : ""}${fill}${posting}</div>
      <dl class="gradebook-menu-stats" data-gradebook-stats><div><dt>Scored</dt><dd>${escape(stats.scored ?? 0)}</dd></div><div><dt>Excused</dt><dd>${escape(stats.excused ?? 0)}</dd></div><div><dt>Missing</dt><dd>${escape(stats.missing ?? 0)}</dd></div><div><dt>Empty</dt><dd>${escape(stats.empty ?? 0)}</dd></div>
        <div><dt>Mean</dt><dd>${escape(value(stats.mean))}${stats.mean_percent ? `<small>${escape(stats.mean_percent)}%</small>` : ""}</dd></div><div><dt>Median</dt><dd>${escape(value(stats.median))}</dd></div><div><dt>Low</dt><dd>${escape(value(stats.low))}</dd></div><div><dt>High</dt><dd>${escape(value(stats.high))}</dd></div></dl>
      ${link?.last_pull ? `<p class="gradebook-menu-note">Last pulled ${escape(when(link.last_pull.at))}${link.last_pull.actor ? ` by ${escape(actorText(link.last_pull.actor))}` : ""}</p>` : ""}${link?.certification ? `<p class="gradebook-menu-note" data-grading-certified>Certified in Grading ${escape(when(link.certification.created_at))}${link.certification.may_have_changed ? "; grades may have changed since" : ""}</p>` : ""}${item.link?.kind === "grading" && gradingChanged(link) ? `<p class="gradebook-menu-note" data-grading-changed>Grading changed ${escape(when(link.grading_changed_at))}${link?.last_pull ? ", after the last pull" : ""}: pull to see what it would change.</p>` : ""}${postingNote ? `<p class="gradebook-menu-note" data-canvas-hidden>${escape(postingNote.charAt(0).toUpperCase() + postingNote.slice(1))}.</p>` : ""}${item.notes ? `<p class="gradebook-menu-note">${escape(item.notes)}</p>` : ""}`;
    menu.dataset.item = itemId;
    menu.setAttribute("aria-label", `${item.title} column options`);
    grid.menuTrigger = trigger;
    try { menu.showPopover(); } catch { return; }
    grid.menuScroll = { left: grid.scroller?.scrollLeft, top: grid.scroller?.scrollTop };
    const rect = trigger.getBoundingClientRect(), width = menu.offsetWidth, height = menu.offsetHeight;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    let top = rect.bottom + 4;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
    menu.style.setProperty("left", `${left}px`);
    menu.style.setProperty("top", `${top}px`);
    trigger.setAttribute("aria-expanded", "true");
    /** @type {HTMLElement|null} */ (menu.querySelector("button, a"))?.focus();
  }

  function closeMenu() {
    const menu = grid?.menu;
    if (!menuOpen(menu)) return;
    grid.hidingMenu = true;
    try { menu.hidePopover(); } catch { /* already closed */ } finally { grid.hidingMenu = false; }
  }

  function onMenuClick(event) {
    const action = /** @type {HTMLElement} */ (event.target).closest("[data-menu-action]");
    if (!action) return;
    const itemId = grid.menu.dataset.item;
    const kind = action.getAttribute("data-menu-action");
    closeMenu();
    if (kind === "edit") void dialog("openColumn", itemId);
    else if (kind === "pull") void dialog("openPull", itemId);
    else if (kind === "sort") {
      remember(current.termId).sort = { key: "item", id: itemId, dir: action.getAttribute("data-dir") === "desc" ? "desc" : "asc" };
      redrawGridKeepingPlace();
    } else if (kind === "collapse") {
      const category = grid.itemById.get(itemId)?.category_id;
      if (category) toggleBand(category, true);
    } else if (kind === "fill-missing" || kind === "fill-excused") fillEmpty(itemId, kind === "fill-missing" ? "missing" : "excused");
    else if (kind === "post-canvas" || kind === "hide-canvas" || kind === "rule-canvas") void dialog("openPostingChoice", itemId, kind === "post-canvas" ? "post" : kind === "hide-canvas" ? "hide" : "rule");
  }

  // "Mark N empty cells missing" / "Excuse N empty cells": a confirmation that lists what it writes (students with a DR-center
  // booking for the column's assignment stay empty unless ticked), then ONE scores write through the grid's usual path.
  function fillEmpty(itemId, status) {
    const item = grid?.itemById.get(itemId), owner = current, termId = owner.termId;
    if (!item || item.collapsed) return;
    const shown = shownStudents(), cells = emptyCells(current.view, item, shown);
    if (!cells.length) { toast("This column has no empty cell among the students shown."); return; }
    const hidden = Math.max(0, grid.model.students.length - shown.length);
    void dialog("openFillEmpty", {
      item, status, cells, hidden,
      onConfirm: (rows) => {
        if (current !== owner || !grid?.table?.isConnected) return "The gradebook moved on; open the column’s options again.";
        const still = new Set(emptyCells(current.view, item, grid.model.students).map((cell) => cell.row));
        const chosen = rows.filter((row) => still.has(row) && !pending.has(pendingKey(termId, itemId, row)));
        if (!chosen.length) return "These cells are no longer empty; nothing was written.";
        const skipped = rows.length - chosen.length;
        void submit(chosen.map((row) => ({ item: itemId, row, entry: { status } })),
          `${status === "missing" ? "Marked" : "Excused"} ${plural(chosen.length, "empty cell")}${status === "missing" ? " missing" : ""} in ${item.title}${skipped ? ` (${skipped} changed meanwhile and ${skipped === 1 ? "was" : "were"} left alone)` : ""}.`);
        return "";
      },
    });
  }

  // The students of the rows shown now: the section filter, the name search and an attention filter all apply.
  function shownStudents() {
    if (!grid?.table) return grid?.model?.students || [];
    return grid.rows.map((tr) => grid.studentByRow.get(tr.dataset.row)).filter(Boolean);
  }

  function onMenuKey(event) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const actions = [.../** @type {NodeListOf<HTMLElement>} */ (grid.menu.querySelectorAll("button, a"))];
    const index = actions.indexOf(/** @type {HTMLElement} */ (document.activeElement));
    const next = actions[(index + (event.key === "ArrowDown" ? 1 : -1) + actions.length) % actions.length];
    if (next) { event.preventDefault(); next.focus(); }
  }

  /**
   * A student's record opened from another page (requirement, 2026-09-30: clicking a name on the Accommodations page opens the card
   * the gradebook shows): the grid's own dialog over that section's View, read for it. Its Change writes as the section tab's grid
   * does, scoped to the section; nothing else on screen is redrawn, and the gradebook reads its View again when it is next shown.
   * `active` says whether the page that asked still wants it once the View has arrived.
   * @param {{termId: string, sectionId: string, enrollmentId: string, active?: () => boolean}} target
   */
  async function openStudentRecord({ termId, sectionId, enrollmentId, active = () => true }) {
    let module;
    try { module = await loadDialogs(); } catch {
      toast(UPDATED, { tone: "danger", action: { label: "Reload", run: reloadPage } });
      return;
    }
    /** @type {GradebookView} */
    let view;
    try { view = await fetchView(termId, sectionId); } catch (error) {
      if (active()) toast(error?.message || "The gradebook could not be loaded.", true);
      return;
    }
    if (!active()) return;
    const row = `${sectionId}:${enrollmentId}`;
    if (!(view.students || []).some((student) => student.row === row)) { toast("This student is not in the section’s gradebook.", true); return; }
    /** @type {GradebookContext} */
    const ctx = /** @type {any} */ ({
      api, escape, icon, toast, showDialog, dialogHeader, termId, communications, absences, notes, studentSupport,
      get view() { return view; },
      sectionId, scopeSection: sectionId,
      reload: async () => (view = await fetchView(termId, sectionId)),
      replaceView: (next) => { if (next && (!next.term?.id || next.term.id === termId)) view = next; },
      applyScores: (result) => { if (result) mergeScores(view, result); },
      requestId: () => crypto.randomUUID(),
    });
    module.openStudent(ctx, row);
  }

  /**
   * SYNC EVERY SECTION (requirement, 2026-10-06): the course Home's Sync with Canvas. The same dialog as a section
   * tab's, scoped to no section, so every linked course is ticked from the start, the term's first sync included. Its View is one
   * section's (cells=section): the term, setup, sections and Canvas state the dialog reads are the whole term's either way.
   * @param {{groupId: string, active?: () => boolean}} target the course Home's group, and whether it still wants the dialog
   */
  async function openTermSync({ groupId, active = () => true }) {
    let module;
    try { module = await loadDialogs(); } catch {
      toast(UPDATED, { tone: "danger", action: { label: "Reload", run: reloadPage } });
      return;
    }
    /** @type {GradebookView} */
    let view;
    let termId = "", first = "";
    try {
      const data = await api("/api/gradebook");
      const term = (data?.terms || []).find((item) => item.group_id === groupId);
      if (!term) { if (active()) toast("This term has no gradebook yet: import a section’s roster first.", true); return; }
      termId = term.id;
      first = [...(term.sections || [])].sort((a, b) => Number(a.number) - Number(b.number))[0]?.id || "";
      view = await fetchView(termId, first);
    } catch (error) {
      if (active()) toast(error?.message || "The gradebook could not be loaded.", true);
      return;
    }
    if (!active()) return;
    if (!view.canvas?.linked_sections) { toast("No section of this term links a Canvas course yet: link it on the section’s tab (Canvas).", true); return; }
    /** @type {GradebookContext} */
    const ctx = /** @type {any} */ ({
      api, escape, icon, toast, showDialog, dialogHeader, termId, communications, absences, notes, studentSupport,
      get view() { return view; },
      sectionId: "", scopeSection: "", viewSection: first,
      reload: async () => (view = await fetchView(termId, first)),
      replaceView: (next) => { if (next && (!next.term?.id || next.term.id === termId)) view = next; },
      applyScores: (result) => { if (result) mergeScores(view, result); },
      requestId: () => crypto.randomUUID(),
    });
    // Resolves once the dialog is open: its summary reads Canvas inside it (up to a minute), and says there what went wrong.
    module.openCanvasSync(ctx).catch((error) => toast(error?.message || "Something went wrong. Please try again.", true));
  }

  /**
   * The course Home's Grading scheme (GRADEBOOK GRADING SCHEME, requirement, 2026-10-06): the term's letter scale, for every section.
   * It reads the term's whole View (every section's percentages for the dialog's preview) and saves through the term's setup.
   * @param {{groupId: string, active?: () => boolean, onSaved?: (scale: any[]) => void}} target
   */
  async function openTermScheme({ groupId, active = () => true, onSaved }) {
    let module;
    try { module = await loadDialogs(); } catch {
      toast(UPDATED, { tone: "danger", action: { label: "Reload", run: reloadPage } });
      return;
    }
    /** @type {GradebookView} */
    let view;
    let termId = "";
    try {
      const data = await api("/api/gradebook");
      const term = (data?.terms || []).find((item) => item.group_id === groupId);
      if (!term) { if (active()) toast("This term has no gradebook yet: import a section’s roster first.", true); return; }
      termId = term.id;
      view = await fetchView(termId, "");
    } catch (error) {
      if (active()) toast(error?.message || "The gradebook could not be loaded.", true);
      return;
    }
    if (!active()) return;
    /** @type {GradebookContext} */
    const ctx = /** @type {any} */ ({
      api, escape, icon, toast, showDialog, dialogHeader, termId,
      get view() { return view; },
      sectionId: "", scopeSection: "", viewSection: "",
      reload: async () => (view = await fetchView(termId, "")),
      replaceView: (next) => { if (next && (!next.term?.id || next.term.id === termId)) view = next; },
      requestId: () => crypto.randomUUID(),
    });
    module.openGradingScheme(ctx, { onSaved });
  }

  return { render, openStudentRecord, openTermSync, openTermScheme };
}

/** Merge a scores write's `{cells, results}` into a View; the [item, row] pairs it touched. */
export function mergeScores(view, result) {
  view.cells ||= {};
  const touched = [];
  for (const [item, rows] of Object.entries(result.cells || {})) {
    const column = (view.cells[item] ||= {});
    for (const [row, cell] of Object.entries(rows || {})) {
      const had = Boolean(column[row]);
      if (!cell || cell.status === "cleared") delete column[row];
      else column[row] = cell;
      // A section's View counts each column's cells term-wide (`cell_counts`, the setup shows it): kept in step with this write.
      if (view.cell_counts && had !== Boolean(column[row])) view.cell_counts[item] = Math.max(0, (view.cell_counts[item] || 0) + (had ? -1 : 1));
      touched.push([item, row]);
    }
  }
  if (result.results) view.results = result.results;
  return touched;
}
