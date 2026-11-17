// @ts-check
// Makeups owed (absences/makeups.py, ABSENCES "MAKEUPS"; requirement, 2026-10-03): an absence that covers the day an assignment was
// given means the student owes a makeup of it, until a paper of it comes back. The Absences page's Makeups view lists them by
// assignment, with Schedule makeup (the Exams page's own routes: a makeup sitting of that exam record and the students placed on it;
// nothing else is written, ABSENCES NO-GRADEBOOK) and Draft makeup version (GRADING "MAKEUP VERSIONS"). Names appear only here and in
// the absence window, never on Home.
import { captureDialogIntent, dialogSession, errorMessage, runFormAction, wallDate } from "./ui.js";
import { displayClock } from "./calendar-feed.js";
import { viewLink } from "./file-viewer.js";

const STATE_TONES = { owed: "warning", if_excused: "neutral", missed: "neutral" };
const PAPER_LABELS = { no_scan: "", absent: "Marked absent on the Exams page", excused: "Marked excused on the Exams page" };
// Quiz makeups booked by card (absences/makeup_booking.py, ABSENCES "MAKEUP BOOKING"; requirement, 2026-10-04): a short chip per
// booking state; a flagged one is a warning, and offers "Send a new card" (the next two days with times after the last card's).
const BOOKING_CHIPS = { queued: "Card goes out shortly", waiting: "Card waits", sending: "Card going out", sent: "Card sent", no_answer: "No answer",
  other_time: "Needs another time", stale: "Could not book", booked: "Booked", paper_not_in: "Paper not in", unseated: "Not on a sitting",
  no_email: "Card not delivered", expired: "Card withdrawn", held: "Held", check_back: "Check back" };
const RENEW = new Set(["no_answer", "other_time", "stale", "unseated", "paper_not_in", "no_email", "expired"]);
// MAKEUP HOLDS (requirement, 2026-10-06): an absence's quiz makeups wait for you, no card and no offered time, until you release
// them. Hold… is offered on a quiz row until the student is on a sitting.
const SEATED = new Set(["booked", "paper_not_in", "unseated", "scheduled"]);
// ONE GRID (requirement, 2026-11-12): every assignment's table has the same five columns at the same
// widths (absences.css), so a column reads straight down the page from one group to the next.
const MAKEUP_COLUMNS = `<colgroup><col class="makeup-col-student"><col class="makeup-col-section"><col class="makeup-col-absence"><col class="makeup-col-makeup"><col class="makeup-col-actions"></colgroup>`;

/** The rows grouped by exam record (assignment), newest given first, owed students before undecided ones.
 * @param {any[]} rows @returns {{event_id: string, title: string, plan_id: string | null, code: string, term_label: string, given_day: string, rows: any[], makeup_versions: any[]}[]} */
export function makeupGroups(rows) {
  /** @type {Map<string, any>} */
  const groups = new Map();
  for (const row of rows || []) {
    let group = groups.get(row.event_id);
    if (!group) {
      group = { event_id: row.event_id, title: row.title, plan_id: row.plan_id || null, code: row.code || "", term_label: row.term_label || "",
        given_day: row.given_day, given_date: row.given_date, rows: [], makeup_versions: row.makeup_versions || [] };
      groups.set(row.event_id, group);
    }
    group.rows.push(row);
    if (row.given_date > group.given_date) { group.given_date = row.given_date; group.given_day = row.given_day; }
  }
  const list = [...groups.values()];
  for (const group of list) group.rows.sort((/** @type {any} */ a, /** @type {any} */ b) => (a.state === b.state ? 0 : a.state === "owed" ? -1 : 1)
    || String(a.name).localeCompare(String(b.name)));
  return list.sort((a, b) => String(b.given_date).localeCompare(String(a.given_date)) || a.title.localeCompare(b.title));
}

/** What the Makeup column says for one row: the scheduled sitting, else what the Exams page records, else nothing.
 * @param {any} row */
export function makeupStatus(row) {
  if (row.scheduled) {
    const s = row.scheduled;
    return `Makeup sitting ${[s.day, s.time && displayClock(s.time), s.location].filter(Boolean).join(" · ") || s.label}`;
  }
  return PAPER_LABELS[/** @type {keyof typeof PAPER_LABELS} */ (row.paper)] || "";
}

/** A makeup sitting in one line (requirement, 2026-11-01): its name unless that is only "Makeup", the campus day ("Tue, Oct 6"), the time and
 * the room. A label a booking card wrote (`sitting_label`, "Makeup · Mon, Nov 2 · 3:00–5:00 pm") already says the day and time, so
 * they are not said again. @param {any} sitting */
export function sittingText(sitting) {
  const day = sitting.day || (sitting.date ? wallDate(sitting.date) : "");
  const written = String(sitting.label || "").trim();
  const label = written.replace(/^makeup(\s*·\s*|$)/i, "");
  const parts = day && written.includes(day) ? [label, sitting.location] : [label, day, sitting.time && displayClock(sitting.time), sitting.location];
  return parts.filter(Boolean).join(" · ") || written || "Makeup";
}

/** KEEP THEIR SITTING (requirement, 2026-10-06; as in the makeup approval window, GRADING): what a student's row in Schedule makeup says
 * when they are already on a makeup sitting of this exam record (`row.scheduled`), with the sitting chosen above ("" for a new one);
 * "" for a student on none. @param {any} row @param {string} chosen */
export function placedNote(row, chosen) {
  const s = row?.scheduled;
  if (!s?.sitting_id) return "";
  if (s.sitting_id === chosen) return "· already on this sitting";
  return `· on ${sittingText(s)}; tick to move them`;
}

/** The booking switch of each term (MAKEUP BOOKING): on, quiz makeups book themselves by card; off, by hand.
 * @param {Record<string, any> | undefined} terms @param {(value: unknown) => string} e */
export function bookingSwitchMarkup(terms, e) {
  return Object.entries(terms || {}).map(([termId, term]) => `<div class="callout makeup-booking" data-tone="${term.cards ? "success" : "info"}" data-makeup-booking="${e(termId)}">
    <p><strong>${term.cards ? "Quiz makeups book themselves" : "Quiz makeups are booked by hand"}</strong> <span class="field-hint">${e([term.code, term.term_label].filter(Boolean).join(" · "))}</span><br>
    ${term.cards ? "Each student who owes a quiz makeup for an excused absence gets a card to pick a time on the next two days that have one, never before the quiz: office hours in the rooms you ticked, or your meeting hours (Term policies on the Follow-up page). Their pick books it, and a print job with their copy is made at once, or as soon as you approve the makeup version."
      : "Turn this on to send each student who owes a quiz makeup a card to pick their time. You see who gets one first."}</p>
    <button type="button" class="button" data-makeup-cards="${e(termId)}" data-makeup-cards-to="${term.cards ? "off" : "on"}" data-makeup-revision="${e(term.revision)}">${term.cards ? "Turn off" : "Turn on…"}</button></div>`).join("");
}

/** Where a quiz's makeup version stands (GRADING "MAKEUP VERSIONS"), for the group's head. @param {any} version @param {(value: unknown) => string} e */
export function versionMarkup(version, e) {
  if (!version) return "";
  const letter = version.letter ? ` ${version.letter}` : "";
  const text = { drafting: `Drafting makeup version${letter}…`, ready: `Makeup version${letter} is ready for you to approve`,
    approving: `Approving makeup version${letter}…`, failed: `The makeup draft${letter} failed; open it to try again` }[/** @type {string} */ (version.state)];
  if (text) return `<small class="makeup-version" data-tone="${version.state === "ready" || version.state === "failed" ? "warning" : "neutral"}">${e(text)}</small>`;
  if (version.state === "approved") {
    return version.rubrics === "to_finalize" ? `<small class="makeup-version" data-tone="warning">Makeup version${e(letter)}: finalize its rubrics</small>`
      : version.rubrics === "reviewing" ? `<small class="makeup-version">Makeup version${e(letter)}: rubric review running</small>` : "";
  }
  if (version.problem) return `<small class="makeup-version" data-tone="warning">${e(`Could not draft a makeup version: ${version.problem}`)}</small>`;
  return version.scrapped ? "" : `<small class="makeup-version">A makeup version will be drafted shortly</small>`;
}

/** The group's way to its makeup version. An approved one is a version of the assignment in its grading pipeline, so the way leads
 * there (requirement, 2026-10-06): Finalize rubrics while they wait on you, else Open makeup version. Before approval it opens the draft window.
 * @param {any} group @param {any} version @param {(value: unknown) => string} e */
export function versionAction(group, version, e) {
  const plan = encodeURIComponent(group.plan_id);
  if (version?.state === "approved") {
    return version.rubrics === "to_finalize" ? `<a class="button" href="#grading/${plan}?tab=answer-key&amp;open=quality">Finalize rubrics</a>`
      : `<a class="button" href="#grading/${plan}">Open makeup version →</a>`;
  }
  return `<button type="button" class="button${version?.state === "ready" ? " primary" : ""}" data-makeup-draft="${e(group.plan_id)}" data-makeup-title="${e(group.title)}">${version?.draft_id ? "Open makeup version…" : "Draft makeup version…"}</button>`;
}

/** A booked student's copy (ABSENCES "COPIES"; named again, with the name filled in on the paper, since 2026-10-18; requirement, 2026-10-12: its print job is made as soon as they book, or as
 * soon as the makeup version is approved): a link to the print job, or what it waits for. "" when there is nothing to say.
 * @param {any} row @param {any} version the quiz's makeup version, when known @param {(value: unknown) => string} e */
export function copyLine(row, version, e) {
  const booking = row.booking;
  if (booking.state !== "booked") return "";
  const copy = booking.copy;
  if (!copy) return version && version.state !== "approved" ? "<small>Their copy is made once the makeup version is approved</small>" : "";
  const ready = !copy.status || copy.status === "ready";
  // AUTOMATIC MAKEUP PRINTS (requirement, 2026-10-18): a ready copy goes to the home printer by itself; the line says how that went.
  const printed = {accepted: "Their copy was printed at home", pending: "Their copy is going to the printer", sending: "Their copy is going to the printer",
    uncertain: "Their copy may have printed: check the printer", failed: "Their copy did not print at home yet; Faculty tries again"}[copy.printed];
  const text = ready ? printed || "Their copy is ready to print" : copy.status === "failed" ? "Their copy could not be made"
    : copy.status === "superseded" ? "Their copy’s print job was superseded" : "Their copy is being made";
  return `<small><a href="#documents?batch=${encodeURIComponent(copy.batch_id)}">${e(text)}</a></small>`;
}

/** PRINT PDF (requirement, 2026-10-12): once a booked student's copy is ready, the job's PDF opens here in the app's viewer. It is the row's framed action
 * while the copy still waits to be printed, and a text action once it printed at home or is on its way there (ONE ROW, ONE STEP).
 * @param {any} row @param {(value: unknown) => string} e */
export function printButton(row, e) {
  const copy = row.booking?.state === "booked" ? row.booking.copy : null;
  if (!copy || (copy.status && copy.status !== "ready")) return "";
  const waits = !["accepted", "pending", "sending"].includes(copy.printed);
  return `<a class="${waits ? "button" : "text-button"}" data-makeup-print ${viewLink(`/api/documents/batches/${encodeURIComponent(copy.batch_id)}/download`, `${row.title || "Makeup"} makeup copy · ${row.name || "Student"}`, e)}>Print PDF</a>`;
}

/** The Makeup column of a quiz makeup with a booking: its chip and what it says. "" when the row reads as before (no card in a
 * term whose cards are off, or a makeup sitting set by hand). The chip already says Booked, so a booked row's line is its day,
 * time and room alone, in ink: it is what you look the row up for.
 * @param {any} row @param {(value: unknown) => string} e @param {any} [version] the quiz's makeup version (for the copy's line) */
export function bookingMarkup(row, e, version) {
  const booking = row.booking;
  if (!booking || booking.state === "scheduled" || booking.state === "manual") return "";
  // TAKEN UP IN FOLLOW-UP (requirement, 2026-11-12): a request for another
  // time whose follow-up you took up and left open reads "In Follow-up" with where it stands there, and is not flagged.
  const taken = booking.state === "other_time" ? booking.followup : null;
  const chip = taken ? "In Follow-up" : BOOKING_CHIPS[/** @type {keyof typeof BOOKING_CHIPS} */ (booking.state)] || booking.label;
  const booked = booking.state === "booked";
  const said = booked ? String(booking.label || "").replace(/^booked:\s*/i, "") : booking.label;
  const lines = [taken ? taken.label : said !== chip ? said : "", booking.state === "other_time" && booking.pick?.choice ? `“${booking.pick.choice}”` : "",
    booked ? "" : `Due by ${booking.due_day}`].filter(Boolean);
  return `<span class="chip" data-tone="${booking.flag ? "warning" : booked ? "success" : "neutral"}">${e(chip)}</span>${
    lines.map((line, i) => `<small${booked && i === 0 ? ' class="makeup-when"' : ""}>${e(line)}</small>`).join("")}${copyLine(row, version, e)}`;
}

/** "Send a new card" on a flagged booking (the next two days with times after the last card's), "Send booking card" on a quiz
 * makeup of a term whose cards are off. @param {any} row @param {(value: unknown) => string} e */
export function cardButton(row, e) {
  const state = row.booking?.state;
  if (state !== "manual" && !RENEW.has(state)) return "";
  return `<button type="button" class="button" data-makeup-card="${e(row.event_id)}" data-makeup-section="${e(row.section_id)}" data-makeup-enrollment="${e(row.enrollment_id)}" data-makeup-absence="${e(row.absence_id)}">${state === "manual" ? "Send booking card" : "Send a new card"}</button>`;
}

/** The Makeups view's list, or an empty state, under each term's booking switch.
 * @param {any[]} rows @param {(value: unknown) => string} e @param {(o: any) => string} emptyMarkup @param {any} [booking] */
export function makeupsMarkup(rows, e, emptyMarkup, booking) {
  const groups = makeupGroups(rows);
  const switches = bookingSwitchMarkup(booking?.terms, e);
  if (!groups.length) {
    return switches + emptyMarkup({ icon: "calendar", title: "No makeups owed",
      body: "When an excused absence covers the day an assignment was given, the student shows here until a paper of it comes back, as does anyone you put on a makeup sitting." });
  }
  const owed = rows.filter((row) => row.state === "owed").length;
  const ifExcused = rows.filter((row) => row.state === "if_excused").length;
  const scheduled = rows.filter((row) => row.state === "scheduled").length;  // SCHEDULED WITHOUT AN ABSENCE (2026-10-19)
  // The work first (critique 2026-10-05, §1.1/§1.7): the owed makeups lead and the booking switch, a setting, follows them.
  return `<p class="absences-count section-lead" aria-live="polite">${e(`${owed} makeup${owed === 1 ? "" : "s"} owed`)}${ifExcused ? e(` · ${ifExcused} if excused`) : ""}${scheduled ? e(` · ${scheduled} scheduled without an absence`) : ""}</p>
    ${groups.map((group) => `<section class="makeup-group" data-makeup-group="${e(group.event_id)}" aria-labelledby="makeup-${e(group.event_id)}">
      <header class="makeup-group-head"><div><h3 id="makeup-${e(group.event_id)}">${e(group.title)}</h3>
        <small>${e([group.code, group.term_label, group.given_day ? `given ${group.given_day}` : "",
          group.makeup_versions.length ? `makeup version${group.makeup_versions.length === 1 ? "" : "s"} ${group.makeup_versions.map((/** @type {any} */ v) => v.letter).join(", ")} ready` : ""].filter(Boolean).join(" · "))}</small>
        ${group.plan_id && group.rows.some((/** @type {any} */ row) => row.booking) ? versionMarkup(booking?.versions?.[group.plan_id], e) : ""}</div>
        <div class="makeup-group-actions"><a class="link-arrow" href="#exams/${encodeURIComponent(group.event_id)}">Open on Exams</a>
          <button type="button" class="button" data-makeup-schedule="${e(group.event_id)}">Schedule makeup…</button>
          ${group.plan_id ? versionAction(group, booking?.versions?.[group.plan_id], e) : ""}</div></header>
      <div class="roster-table-scroll absences-table-scroll" tabindex="0" role="region" aria-label="${e(`Makeups owed for ${group.title}`)}"><table class="roster-table absences-table makeup-table">
        ${MAKEUP_COLUMNS}
        <thead><tr><th scope="col">Student</th><th scope="col">Section</th><th scope="col">Absence</th><th scope="col">Makeup</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>${group.rows.map((/** @type {any} */ row) => `<tr>
          <td data-label="Student">${row.absence_id ? `<button type="button" class="absences-name" data-absence="${e(row.absence_id)}" aria-haspopup="dialog">${e(row.name || "Student")}<span class="sr-only">: open the absence</span></button>` : e(row.name || "Student")}</td>
          <td data-label="Section">${e(row.section)}</td>
          <td data-label="Absence">${e(row.dates)}<small>${e(row.status_label)}</small></td>
          <td data-label="Makeup" class="makeup-cell">${cellMarkup(row, e, group.plan_id ? booking?.versions?.[group.plan_id] : undefined)}</td>
          <td data-label="Actions" class="makeup-actions-cell">${actionsMarkup(row, e)}</td></tr>`).join("")}</tbody></table></div></section>`).join("")}${switches}`;
}

/** GOOGLE SIGN-IN (ABSENCES "DEPARTMENT MAKEUP EXAMS", 2026-10-22): the department's makeup exam form takes a response only from a
 * signed-in TAMU Google account; this line says whether Faculty keeps one, with Sign in to Google (GET …/department-makeups/google).
 * @param {any} google @param {(value: unknown) => string} e */
export function departmentSignInMarkup(google, e) {
  if (!google?.configured) return "";
  const since = google.saved_at ? new Date(google.saved_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
  return `<div class="callout makeup-booking" data-tone="${google.signed_in ? "success" : "warning"}" data-department-google>
    <p><strong>${google.signed_in ? "Department makeup requests are signed in to Google" : "Department makeup requests need a Google sign-in"}</strong><br>
    ${google.signed_in ? e(`Faculty sends them as ${google.email || google.form_email}${since ? `, signed in ${since}` : ""}.`)
      : google.ended ? e(`Google has ended Faculty's sign-in (it no longer opens the form signed in). Sign in again as ${google.form_email} before sending a request.`)
      : e(`The department's form takes a request only from a signed-in TAMU Google account. Sign in once as ${google.form_email}; Faculty keeps the session.`)}</p>
    <button type="button" class="button${google.signed_in ? "" : " primary"}" data-department-signin>${google.signed_in || google.ended ? "Sign in again…" : "Sign in to Google…"}</button></div>`;
}

/** One row's Makeup column, what the row says and nothing to press: its booking when a card is involved, a hold and what it waits
 * on, else the owed chip and the sitting or what the Exams page records.
 * @param {any} row @param {(value: unknown) => string} e @param {any} [version] the quiz's makeup version, when known */
export function cellMarkup(row, e, version) {
  const state = row.booking?.state;
  if (isHeld(row)) return heldMarkup(row, e);
  if (state && state !== "manual" && state !== "scheduled") return bookingMarkup(row, e, version);
  const waits = !row.scheduled && state !== "scheduled";  // on a sitting, it waits on the student (critique 2026-10-05)
  return `<span class="chip" data-tone="${waits ? STATE_TONES[/** @type {keyof typeof STATE_TONES} */ (row.state)] || "neutral" : "neutral"}">${e(row.state_label)}</span>${
    makeupStatus(row) ? `<small>${e(makeupStatus(row))}</small>` : ""}`;
}

/** A hold in force (MAKEUP HOLDS): held, and the student on no sitting. @param {any} row */
const isHeld = (row) => Boolean(row.hold?.held && !row.scheduled && !SEATED.has(row.booking?.state));

/** ONE ROW, ONE STEP (requirement, 2026-11-12): a row's actions sit in their own column at the right. At most one is framed, the step that moves
 * the makeup on (a card to send, a hold to release, a copy still to print); the rest are text actions on one line under it: Print
 * PDF of a copy that already printed, Hold… or Change…, and Missed makeup… last. "" for a row with nothing to press.
 * @param {any} row @param {(value: unknown) => string} e */
export function actionsMarkup(row, e) {
  const held = isHeld(row);
  const print = printButton(row, e);
  const step = held ? releaseButton(row, e) : cardButton(row, e) || (print.includes('class="button"') ? print : "");
  const more = [print === step ? "" : print, held ? changeButton(row, e) : holdButton(row, e), missedButton(row, e)].join("");
  return step || more ? `<div class="makeup-actions">${step}${more ? `<span class="makeup-actions-more">${more}</span>` : ""}</div>` : "";
}

/** MISSED (requirement, 2026-10-31): "Missed makeup…" on a row owed for an
 * excused absence, or on a makeup sitting without one; an undecided absence is decided first. @param {any} row @param {(value: unknown) => string} e */
export function missedButton(row, e) {
  if (row.state !== "owed" && row.state !== "scheduled") return "";
  return `<button type="button" class="text-button" data-missed-event="${e(row.event_id)}" data-missed-section="${e(row.section_id)}" data-missed-enrollment="${e(row.enrollment_id)}">Missed makeup…</button>`;
}

/** Hold… on a quiz row (`row.hold` is set only for quizzes) whose student is on no sitting yet. @param {any} row @param {(value: unknown) => string} e */
export function holdButton(row, e) {
  if (!row.hold || row.hold.held || row.scheduled || SEATED.has(row.booking?.state)) return "";
  return `<button type="button" class="text-button" data-makeup-hold="${e(row.absence_id)}" data-makeup-hold-revision="${e(row.hold.revision)}">Hold…</button>`;
}

/** A held quiz makeup: what it waits on and when you look again. A hold covers every makeup of the absence.
 * @param {any} row @param {(value: unknown) => string} e */
export function heldMarkup(row, e) {
  const hold = row.hold;
  const due = row.booking?.state === "check_back";
  const lines = [hold.reason ? `Waiting on ${hold.reason}` : "Until you release it",
    hold.check_back_day ? `${due ? "Check back was" : "Check back"} ${hold.check_back_day}` : "",
    row.state === "if_excused" ? row.state_label : row.booking?.due_day ? `Due by ${row.booking.due_day}` : ""].filter(Boolean);
  return `<span class="chip" data-tone="${due ? "warning" : "neutral"}">${due ? "Check back" : "Held"}</span>${lines.map((line) => `<small>${e(line)}</small>`).join("")}`;
}

/** Release on a held row (with the term's cards on its card then goes out: "Release and send card"). @param {any} row @param {(value: unknown) => string} e */
export function releaseButton(row, e) {
  const sends = row.state === "owed" && row.booking?.cards;
  return `<button type="button" class="button" data-makeup-release="${e(row.absence_id)}" data-makeup-hold-revision="${e(row.hold.revision)}" data-makeup-release-sends="${sends ? "1" : ""}">${sends ? "Release and send card" : "Release"}</button>`;
}

/** Change… on a held row: what the hold waits on and its check-back day. @param {any} row @param {(value: unknown) => string} e */
export function changeButton(row, e) {
  return `<button type="button" class="text-button" data-makeup-hold="${e(row.absence_id)}" data-makeup-hold-revision="${e(row.hold.revision)}">Change…</button>`;
}

/** Turning a term's cards on asks first, naming who gets a card now and the times it offers (the dry run, GET …/makeups/plan).
 * @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string}} deps
 * @param {{termId: string, revision: number, to: "on" | "off", rows: any[]}} choice @param {() => void} done */
export async function switchBooking({ api, escape, toast, showDialog, dialogHeader }, { termId, revision, to, rows }, done) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const put = () => api(`/api/absences/makeups/terms/${encodeURIComponent(termId)}`, "PUT", { request_id: crypto.randomUUID(), cards: to === "on", revision });
  if (to === "off") {
    try { await put(); toast("Quiz makeup cards turned off"); } catch (cause) { toast(errorMessage(cause), true); }
    done();
    return;
  }
  let planned, held;
  const intended = captureDialogIntent(/** @type {HTMLDialogElement} */ (document.getElementById("dialog")));
  try { ({ cards: planned = [], held = [] } = await api("/api/absences/makeups/plan")); } catch (cause) { toast(errorMessage(cause), true); return; }
  if (!intended()) return;
  const key = (/** @type {any} */ item) => `${item.event_id}:${item.section_id}:${item.enrollment_id}`;
  const names = new Map(rows.map((row) => [key(row), row.name]));
  const termOf = (/** @type {any} */ item) => rows.some((row) => key(row) === key(item) && row.term_id === termId);
  const mine = planned.filter(termOf);
  const waiting = held.filter(termOf);
  showDialog(`<form class="absence-form" id="makeup-booking-form" novalidate>${dialogHeader("Book quiz makeups by card")}
    <div class="dialog-body">
      <p>From now on, each student who owes a quiz makeup for an excused absence gets a card by email to pick when they take it; their pick puts them on that time's makeup sitting. A student who needs another time comes back to you in Follow-up. One who does not answer, and does not write to you by the end of the next business day after the card's last time, gets a 0. You are told when one brings no paper.</p>
      ${mine.length ? `<p><strong>${e(`${mine.length} card${mine.length === 1 ? "" : "s"} go${mine.length === 1 ? "es" : ""} out now:`)}</strong></p>
        <ul class="makeup-plan">${mine.map((/** @type {any} */ item) => `<li>${e(names.get(key(item)) || "Student")} · ${e(item.title)} · section ${e(item.section)}<small>${
          item.options.length ? e(`${item.options.length} times: ${item.options[0]}${item.options.length > 1 ? ` … ${item.options[item.options.length - 1]}` : ""}`) : "No time left before the due date: schedule it by hand"}</small></li>`).join("")}</ul>`
        : "<p>No card goes out now: no quiz makeup is owed without a makeup sitting.</p>"}
      ${waiting.length ? `<p><strong>${e(`Held, no card until you release ${waiting.length === 1 ? "it" : "them"}:`)}</strong></p>
        <ul class="makeup-plan">${waiting.map((/** @type {any} */ item) => `<li>${e(names.get(key(item)) || "Student")} · ${e(item.title)} · section ${e(item.section)}${
          item.reason ? `<small>${e(`Waiting on ${item.reason}`)}</small>` : ""}</li>`).join("")}</ul>` : ""}
      <p class="field-hint">To hold a student's card, cancel and use Hold… on their row.</p>
      <p class="form-error" role="alert" hidden></p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${e(mine.length ? `Turn on and send ${mine.length} card${mine.length === 1 ? "" : "s"}` : "Turn on")}</button></footer></form>`);
  const form = /** @type {HTMLFormElement} */ (document.getElementById("makeup-booking-form"));
  const dialog = /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const session = dialogSession(form, dialog);
  form.onsubmit = (event) => {
    event.preventDefault();
    void runFormAction(form, async () => {
      await put();
      toast("Quiz makeup cards turned on");
      if (session()) dialog.close();
      done();
    });
  };
}

/** "Send a new card" / "Send booking card": the next card for one quiz makeup (the worker sends it). @param {{api: Function, toast: Function}} deps @param {HTMLElement} button @param {() => void} done */
export async function sendCard({ api, toast }, button, done) {
  button.setAttribute("disabled", "");
  try {
    const made = await api("/api/absences/makeups/cards", "POST", { request_id: crypto.randomUUID(), event_id: button.dataset.makeupCard,
      section_id: button.dataset.makeupSection, enrollment_id: button.dataset.makeupEnrollment, absence_id: button.dataset.makeupAbsence });
    toast(`Card ${made.attempt > 1 ? "with new times " : ""}on its way: ${made.options.length} time${made.options.length === 1 ? "" : "s"}`);
  } catch (cause) {
    toast(errorMessage(cause), true);
  } finally {
    button.removeAttribute("disabled");
    done();
  }
}

/** Hold… / Change…: hold every quiz makeup of one absence until you release it (MAKEUP HOLDS), saying what it waits on and when to
 * look again. @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string}} deps
 * @param {HTMLElement} button @param {any} row the row the button is on @param {() => void} done */
export function holdMakeups({ api, escape, toast, showDialog, dialogHeader }, button, row, done) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const hold = row?.hold?.held ? row.hold : null;
  showDialog(`<form class="absence-form" id="makeup-hold-form" novalidate>${dialogHeader(hold ? "Change the hold" : "Hold makeup booking", e(row?.name || ""))}
    <div class="dialog-body">
      <p>Until you release it, no booking card goes out for this absence's quiz makeups, and replies offer no makeup time. An unanswered card is withdrawn.</p>
      <label class="field">Waiting on <span class="field-optional">optional</span><input name="reason" maxlength="300" value="${e(hold?.reason || "")}" placeholder="e.g. her concussion reassessment"></label>
      <label class="field">Check back on <span class="field-optional">optional</span><input type="date" name="check_back" value="${e(hold?.check_back || "")}"><small class="field-hint">From that day it shows under Makeups that need you on Home.</small></label>
      <p class="form-error" role="alert" hidden></p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${hold ? "Save" : "Hold"}</button></footer></form>`);
  const form = /** @type {HTMLFormElement} */ (document.getElementById("makeup-hold-form"));
  const dialog = /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const session = dialogSession(form, dialog);
  form.onsubmit = (event) => {
    event.preventDefault();
    void runFormAction(form, async () => {
      const fields = /** @type {any} */ (form.elements);
      await api(`/api/absences/${encodeURIComponent(button.dataset.makeupHold || "")}/makeup-hold`, "POST", { request_id: crypto.randomUUID(), held: true,
        reason: fields.reason.value.trim(), check_back: fields.check_back.value || null, revision: Number(button.dataset.makeupHoldRevision) || 0 });
      toast(hold ? "Hold changed" : "Makeup booking held");
      if (session()) dialog.close();
      done();
    });
  };
}

/** Release: the absence's quiz makeups book as usual again (with the term's cards on, the card goes out shortly).
 * @param {{api: Function, toast: Function}} deps @param {HTMLElement} button @param {() => void} done */
export async function releaseHold({ api, toast }, button, done) {
  button.setAttribute("disabled", "");
  try {
    await api(`/api/absences/${encodeURIComponent(button.dataset.makeupRelease || "")}/makeup-hold`, "POST",
      { request_id: crypto.randomUUID(), held: false, revision: Number(button.dataset.makeupHoldRevision) || 0 });
    toast(button.dataset.makeupReleaseSends ? "Released: the booking card goes out shortly" : "Hold released");
  } catch (cause) {
    toast(errorMessage(cause), true);
  } finally {
    button.removeAttribute("disabled");
    done();
  }
}

/** The absence window's "Work given during this absence"; a makeup recorded missed (MISSED) offers Undo.
 * @param {any[]} work @param {(value: unknown) => string} e */
export function workMarkup(work, e) {
  if (!work?.length) return "";
  return `<section class="absence-section" aria-labelledby="absence-work-title" data-absence-work><h3 id="absence-work-title">Work given during this absence</h3>
    <ul class="absence-work">${work.map((row) => `<li>${e(row.title)} <small>given ${e(row.given_day)}</small>
      <span class="chip" data-tone="${STATE_TONES[/** @type {keyof typeof STATE_TONES} */ (row.state)] || "neutral"}">${e(row.state_label)}</span>${makeupStatus(row) ? ` <small>${e(makeupStatus(row))}</small>` : ""}${
      row.state === "missed" ? ` <button type="button" class="text-button" data-unmiss-event="${e(row.event_id)}" data-unmiss-section="${e(row.section_id)}" data-unmiss-enrollment="${e(row.enrollment_id)}">Undo</button>` : ""}</li>`).join("")}</ul>
    <p class="field-hint">Each stays owed until a paper of it comes back, or you record it missed. <a href="#absences?view=makeups">Plan makeups</a></p></section>`;
}

/** "Missed makeup…": asks first, then records the makeup missed (POST /api/absences/makeups/missed): it is owed no more, and the
 * student's empty cell counts as 0 until Certify records it.
 * @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string}} deps
 * @param {any} row @param {() => void} done */
export function missedMakeup({ api, escape, toast, showDialog, dialogHeader }, row, done) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const who = row.name || "This student";
  const effect = row.absence_id
    ? `Their makeup of ${row.title} is no longer owed, and their empty grade counts as 0 in the gradebook now. Certify ${row.title} again to record the 0 in its certified grades.`
    : `They leave the Makeups view. No absence held their grade, so it is unchanged.`;
  showDialog(`<form class="absence-form" id="makeup-missed-form" novalidate>${dialogHeader("Missed makeup, record 0", e(`${who} · ${row.title}`))}
    <div class="dialog-body">
      <p>${e(effect)}</p>
      <p class="field-hint">${row.absence_id ? "Undo is in their absence window, under Work given during this absence." : "A paper of theirs that comes back later is still graded."}</p>
      <p class="form-error" role="alert" hidden></p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Record 0</button></footer></form>`);
  const form = /** @type {HTMLFormElement} */ (document.getElementById("makeup-missed-form"));
  const dialog = /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const session = dialogSession(form, dialog);
  const requestId = crypto.randomUUID();
  form.onsubmit = (event) => {
    event.preventDefault();
    void runFormAction(form, async () => {
      await api("/api/absences/makeups/missed", "POST", { request_id: requestId, missed: true, event_id: row.event_id,
        section_id: row.section_id, enrollment_id: row.enrollment_id });
      toast(`Makeup recorded missed: ${who}`);
      if (session()) dialog.close();
      done();
    });
  };
}

/**
 * Schedule makeup: choose a makeup sitting of the exam record, or make one, and place the ticked students on it. Only the Exams
 * page's own routes are used (a sitting, the roster sync when a student is not yet on the record, each student's sitting); the
 * student's expectation and every grade stay as they are.
 * @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void, dialogHeader: (title: string, subtitle?: string) => string}} deps
 * @param {{event_id: string, title: string, rows: any[]}} group @param {() => void} done
 */
export async function scheduleMakeup({ api, escape, toast, showDialog, dialogHeader }, group, done) {
  const intended = captureDialogIntent(/** @type {HTMLDialogElement} */ (document.getElementById("dialog")));
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  let exam;
  try { exam = await api(`/api/exams/${encodeURIComponent(group.event_id)}`); } catch (cause) { toast(errorMessage(cause), true); return; }
  if (!intended()) return;
  const makeups = (exam.sittings || []).filter((/** @type {any} */ s) => s.kind === "makeup");
  // KEEP THEIR SITTING (requirement, 2026-10-06): a student already on a makeup sitting starts unticked, and their row says where they
  // are, so Schedule moves them only when they are ticked.
  const placedOn = (/** @type {any} */ row) => row.scheduled?.sitting_id || "";
  showDialog(`<form class="absence-form" id="makeup-schedule-form" novalidate>${dialogHeader("Schedule makeup", e(group.title))}
    <div class="dialog-body">
      <fieldset class="absence-decision"><legend>Makeup sitting</legend><div class="makeup-sittings">
        ${makeups.map((/** @type {any} */ s, /** @type {number} */ i) => `<label><input type="radio" name="sitting" value="${e(s.id)}"${i === 0 ? " checked" : ""}> ${e(sittingText(s))}</label>`).join("")}
        <label><input type="radio" name="sitting" value=""${makeups.length ? "" : " checked"}> A new makeup sitting</label></div>
        <div class="makeup-new" data-makeup-new${makeups.length ? " hidden" : ""}>
          <label class="field">Name<input name="label" maxlength="280" value="Makeup"><small class="field-hint">Add anything else about the time here, such as "after lecture".</small></label>
          <div class="absence-dates"><label class="field">Date<input type="date" name="date"></label><label class="field">Starts<input type="time" name="time" step="300"></label></div>
          <label class="field">Where <span class="field-optional">optional</span><input name="location" maxlength="280" placeholder="e.g. BLOC 164"></label></div></fieldset>
      <fieldset class="absence-decision"><legend>Students</legend>
        ${group.rows.map((/** @type {any} */ row, /** @type {number} */ i) => `<label><input type="checkbox" data-makeup-student="${i}"${row.state === "owed" && !placedOn(row) ? " checked" : ""}> ${e(row.name || "Student")} · section ${e(row.section)} <small>${e(row.state_label)}</small>${placedOn(row) ? ` <small data-makeup-where="${i}"></small>` : ""}</label>`).join("<br>")}
        <p class="field-hint" data-makeup-moving hidden></p></fieldset>
      <p class="field-hint">This changes only the exam record's sittings: each student keeps their makeup owed until a paper comes back, and nothing is graded or excused.</p>
      <p class="form-error" role="alert" hidden></p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Schedule</button></footer></form>`);
  const form = /** @type {HTMLFormElement} */ (document.getElementById("makeup-schedule-form"));
  const dialog = /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const session = dialogSession(form, dialog);
  // The new sitting's fields, where each placed student is, and how many ticked students Schedule moves off another makeup sitting.
  const follow = () => {
    const chosen = /** @type {HTMLInputElement | null} */ (form.querySelector("[name=sitting]:checked"))?.value || "";
    /** @type {HTMLElement} */ (form.querySelector("[data-makeup-new]")).hidden = Boolean(chosen);
    form.querySelectorAll("[data-makeup-where]").forEach((note) => {
      note.textContent = placedNote(group.rows[Number(/** @type {HTMLElement} */ (note).dataset.makeupWhere)], chosen);
    });
    const moving = [...form.querySelectorAll("[data-makeup-student]:checked")].map((box) => group.rows[Number(/** @type {HTMLElement} */ (box).dataset.makeupStudent)])
      .filter((row) => placedOn(row) && placedOn(row) !== chosen).length;
    const line = /** @type {HTMLElement} */ (form.querySelector("[data-makeup-moving]"));
    line.textContent = moving ? `Schedule moves ${moving} ticked ${moving === 1 ? "student" : "students"} off another makeup sitting.` : "";
    line.hidden = !moving;
  };
  form.addEventListener("change", follow);
  follow();
  form.onsubmit = (event) => {
    event.preventDefault();
    void runFormAction(form, async () => {
      const fields = /** @type {any} */ (form.elements);
      const ticked = [...form.querySelectorAll("[data-makeup-student]:checked")].map((box) => group.rows[Number(/** @type {HTMLElement} */ (box).dataset.makeupStudent)]);
      if (!ticked.length) throw new Error("Tick the students to place on the makeup sitting.");
      let sittingId = /** @type {HTMLInputElement | null} */ (form.querySelector("[name=sitting]:checked"))?.value || "";
      let current = exam;
      if (!sittingId) {
        if (!fields.date.value) throw new Error("Choose the makeup's date.");
        // An exact start (2026-10-05): every overlap check reads it, and the calendar shows the makeup for its quiz's length.
        if (!fields.time.value) throw new Error("Choose when the makeup starts.");
        const before = new Set((current.sittings || []).map((/** @type {any} */ s) => s.id));
        current = await api(`/api/exams/${encodeURIComponent(group.event_id)}/sittings`, "POST",
          { version: current.version, kind: "makeup", label: fields.label.value || "Makeup", date: fields.date.value, time: fields.time.value, location: fields.location.value });
        sittingId = (current.sittings || []).find((/** @type {any} */ s) => !before.has(s.id))?.id || "";
        if (!sittingId) throw new Error("The makeup sitting was saved, but could not be found again. Reload and choose it.");
      }
      const key = (/** @type {any} */ p) => `${p.section_id}:${p.enrollment_id}`;
      if (ticked.some((row) => !(current.participants || []).some((/** @type {any} */ p) => key(p) === key(row)))) {
        current = await api(`/api/exams/${encodeURIComponent(group.event_id)}/roster`, "POST", { version: current.version });
      }
      let placed = 0;
      for (const row of ticked) {
        const person = (current.participants || []).find((/** @type {any} */ p) => key(p) === key(row));
        if (!person) throw new Error(`${row.name || "A student"} is not on this exam's record.`);
        if (person.sitting_id === sittingId) continue;
        current = await api(`/api/exams/${encodeURIComponent(group.event_id)}/participants/${encodeURIComponent(row.section_id)}/${encodeURIComponent(row.enrollment_id)}`,
          "PATCH", { version: person.version, sitting_id: sittingId });
        placed += 1;
      }
      toast(placed ? `${placed} student${placed === 1 ? "" : "s"} placed on the makeup sitting` : "Already on that makeup sitting");
      if (session()) dialog.close();
      done();
    });
  };
}
