import { accommodationTags, accommodationLabel, scheduleTime, schedulePartsMarkup, scheduleChanges, importAimExports, termLabel } from "./accommodations.js";
import { wallDate, localToday, daysFrom, emptyMarkup, filterChipsMarkup, bindFilterChips, loadingMarkup, setBusy, captureDialogIntent } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { openAimSignIn, openAimCapture, openAimSend } from "./aim-signin.js";
import { icon } from "./icons.js";
import { saveTaker, takerMarkup } from "./dr-quiz-taker.js";

/* The Accommodations page (UX overhaul, 2026-09-26; DR quizzes tab since). Three tabs. Student accommodations and the DR exam
   schedule are views of the saved records (who needs what, and who tests at the DR center), read from
   /api/accommodations/overview; the DR groups' seating-chart and exam links come from /api/state and /api/exams. DR quizzes
   reads each current term's quizzes from the scheduling routes (dr-quizzes, aim-prescheduling, aim-send) and the quiz drafts'
   accessible copies. Every write starts from the instructor's own click: Import from AIM and the two uploads, Send to AIM, the
   DR quiz marks with Undo and Retry, the DR date and the per-student DR quiz-taker toggle. Capture AIM's form writes nothing
   to the records, and an exam's DR list can be printed. The page stays steady: a refresh that finds the same records changes
   nothing, and a changed one is patched in place, keeping open disclosures, scroll, focus and what is typed. Filters live in
   the address (tab, when, course, exam; never a student). */

const FRESH = 15000;           // a render within this of the last read reuses it (tab switches, repeated shell refreshes)
const LINKS_FRESH = 60000;     // seating charts and exam records for the DR groups' links
const WHEN = ["upcoming", "past", "all"];
const AIM_KINDS = ["accommodations", "exams"];

export const termKey = (item) => JSON.stringify([item.term, item.term_other, item.year]);
const plural = (count, one, many = `${one}s`) => `${Number(count).toLocaleString()} ${count === 1 ? one : many}`;
const same = (a, b) => String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();

/** The page's filters named in the address: {tab, when?, course?, exam?}. Never names or UINs. */
export function readRoute(hash) {
  const params = new URLSearchParams(String(hash || "").split("?")[1] || "");
  const route = { tab: ["exams", "quizzes"].includes(params.get("tab")) ? params.get("tab") : "students" };
  if (params.has("when")) route.when = WHEN.includes(params.get("when")) ? params.get("when") : "upcoming";
  if (params.has("course")) route.course = params.get("course") || "";
  if (params.has("exam")) route.exam = params.get("exam") || "";
  return route;
}

/** The address for these filters (Upcoming, the default, is left out). */
export function routeHash({ tab, when = "upcoming", course = "", exam = "" }) {
  const params = new URLSearchParams();
  if (tab === "exams" || tab === "quizzes") params.set("tab", tab);
  if (tab === "exams" && when !== "upcoming") params.set("when", when);
  if (course) params.set("course", course);
  if (tab === "exams" && exam) params.set("exam", exam);
  const query = params.toString().replace(/\+/g, "%20");
  return `#accommodations${query ? `?${query}` : ""}`;
}

/** The "Booked in AIM" cell of a DR quiz row (SCHEDULING "CLASS SCHEDULE: DR QUIZZES"): the imported DR bookings against the planned
 *  day. It speaks of students' bookings only, so it never reads as contradicting the date sent to AIM or the Prescheduled mark beside it
 *  (requirement, 2026-11-01). */
export function aimStatus(aim) {
  if (aim?.status === "in_aim") return { text: plural(aim.bookings, "booking"), tone: "ok" };
  if (aim?.status === "aim_differs") return { text: `Differs · booked ${(aim.dates || []).map((day) => wallDate(day)).join(", ")}`, tone: "warn" };
  return { text: "None yet", tone: "muted" };
}

/* SCHEDULING "DR QUIZ NOTICES" (job 005, 2026-10-15): the tab's controls. "Prescheduled in AIM" marks a ready quiz against the
   list's fingerprint after a confirm naming how many students it emails, Undo stands while no notice has gone out, each DR quiz
   taker's notice shows its status, and a failed or uncertain one has an explicit Retry behind its own confirm. */
const NOTICES = {
  accepted: () => ({ text: "Told", tone: "success" }),
  waiting: () => ({ text: "Waiting to send", tone: "info" }),
  skipped: (student) => ({ text: `Skipped · ${student.detail}`, tone: "neutral" }),
  failed: () => ({ text: "Failed", tone: "danger" }),
  uncertain: () => ({ text: "Uncertain", tone: "warning" }),
};
const SENT = ["accepted", "failed", "uncertain"];  // a notice row exists: the server refuses Undo (409)

/** A DR quiz taker's notice status as a chip: {text, tone}, or null while the quiz has no mark. */
export function noticeLabel(student) {
  return NOTICES[student?.status]?.(student) || null;
}

/** The post of a mark, an Undo or a Retry: `target` is the quiz key (mark, unmark) or the email's batch (retry). */
export function drQuizAction(action, termId, target, fingerprint, requestId) {
  const path = `/api/scheduling/terms/${encodeURIComponent(termId)}/aim-prescheduling/${action}`;
  if (action === "mark") return { path, body: { request_id: requestId, key: target, fingerprint } };
  if (action === "unmark") return { path, body: { request_id: requestId, key: target } };
  return { path, body: { request_id: requestId, batch: target } };
}

/** True while some student of some quiz in a status read waits for the notices worker. */
export function noticesWaiting(notices) {
  return Boolean(notices?.quizzes?.some((notice) => notice.students.some((student) => student.status === "waiting")));
}

/** The confirm before a mark: it emails every DR quiz taker of the section who is not skipped. */
export function markPrompt(notice) {
  const count = notice.students.filter((student) => student.status !== "skipped").length;
  return { title: "Prescheduled in AIM?", body: `This emails ${plural(count, "student")}.`, send: "Send" };
}

/** The confirm before a Retry (the name is raw; markup escapes it). */
export function retryPrompt(student) {
  return { title: `Send this email again to ${student.name}?`, body: "", send: "Send" };
}

/** Posts `request` only once `ask(prompt)` says Send, with a new request ID; Cancel posts nothing and answers null. */
export async function sendConfirmed(prompt, { action, termId, target, fingerprint }, { ask, api, newId = () => crypto.randomUUID() }) {
  if (!await ask(prompt)) return null;
  const { path, body } = drQuizAction(action, termId, target, fingerprint, newId());
  return api(path, "POST", body);
}

/** The Prescheduled cell of one quiz row: the mark (or its chip and Undo), then each DR quiz taker's notice. */
function noticeCellMarkup(quiz, notice, termId, escape) {
  if (!notice) return "";
  const term = `data-dr-term="${escape(termId)}"`;
  const name = `${escape(quiz.title)}, section ${escape(quiz.section)}`;
  const parts = [];
  if (notice.state === "marked") {
    parts.push('<span class="chip" data-tone="success">Prescheduled in AIM</span>');
    if (!notice.students.some((student) => SENT.includes(student.status))) {
      parts.push(`<button type="button" class="text-button" data-dr-unmark="${escape(quiz.key)}" ${term} aria-label="Undo Prescheduled in AIM: ${name}">Undo</button>`);
    }
  } else if (notice.state === "changed") {
    parts.push('<span class="chip" data-tone="warning">Date or time changed since marked</span>');
  }
  if (quiz.ready && notice.state !== "marked") {
    parts.push(`<button type="button" class="button" data-dr-mark="${escape(quiz.key)}" ${term} aria-label="Prescheduled in AIM: ${name}; emails the section's DR quiz takers">Prescheduled in AIM</button>`);
  }
  const students = notice.students.map((student) => {
    const label = noticeLabel(student);
    if (!label) return "";
    const retry = student.status === "failed" || student.status === "uncertain"
      ? `${student.detail ? `<small class="dr-notice-detail">${escape(student.detail)}</small>` : ""}<button type="button" class="text-button" data-dr-retry="${escape(student.batch)}" ${term} data-dr-name="${escape(student.name)}">Retry</button>`
      : "";
    return `<li><span class="dr-notice-name">${escape(student.name)}</span><span class="chip" data-tone="${label.tone}">${escape(label.text)}</span>${retry}</li>`;
  }).join("");
  return `${parts.length ? `<div class="dr-notice-actions">${parts.join("")}</div>` : ""}${students ? `<ul class="dr-notice-list">${students}</ul>` : ""}`;
}

// SEND TO AIM (SCHEDULING, 2026-10-27; scheduling/aim_send.py): one button per term adds the ready DR quiz dates to AIM, uploads
// their accessible copies to the students' requests and exports the DR bookings; each write's receipt shows under the AIM chip.
const AIM_SENT = {
  date_added: ["Added to AIM", "success"], date_listed: ["Already in AIM", "neutral"], marked: ["Prescheduled; students emailed", "success"],
  date_held: ["Waits for Retry", "warning"], date_failed: ["Not added", "danger"], date_uncertain: ["Not confirmed", "warning"],
  uploaded: ["Accessible copy uploaded", "success"], upload_waiting: ["No student registered yet", "neutral"],
  upload_held: ["Upload waits for Retry", "warning"], upload_failed: ["Upload failed", "danger"], upload_uncertain: ["Upload not confirmed", "warning"],
  stopped: ["Stopped", "danger"],
};

/** The confirm before Send to AIM: what it adds and uploads, from the term's plan (GET …/aim-send); null when nothing is ready. */
export function aimSendPrompt(plan) {
  const quizzes = plan?.quizzes || [];
  if (!quizzes.length) return null;
  const lines = quizzes.map((quiz) => `${quiz.title}, section ${quiz.section}, ${wallDate(quiz.date)}: `
    + (quiz.date_send?.status === "accepted" ? "already sent" : quiz.date_send && ["pending", "uncertain"].includes(quiz.date_send.status) ? "waits for Retry" : "add the date")
    + (quiz.copy ? ", upload the accessible copy to registered students" : `, no accessible copy (${quiz.copy_reason})`));
  return { title: "Send to AIM?", lines,
    note: "Faculty signs in with your saved sign-in (approve Duo), adds each date as a Quiz of 20 minutes at 10 minutes after the section's class ends, emails the DR quiz takers to register, uploads, then imports the DR bookings.",
    send: "Sign in and send" };
}

/** What a finished send did, one line each: [{text, tone, detail}]. */
export function sentLines(sent) {
  return (sent || []).map((line) => {
    const [label, tone] = AIM_SENT[line.did] || [line.did, "neutral"];
    const what = line.title ? `${line.title}, section ${line.section}${line.date ? `, ${wallDate(line.date)}` : ""}: ` : "";
    return { text: `${what}${label}`, tone, detail: line.detail || "" };
  });
}

/** The bookings import a send made by itself, in words. */
export function sentImport(imported) {
  if (!imported.imported) return "AIM's DR bookings matched no current student, so nothing was imported.";
  const counts = imported.counts || {};
  const parts = [["added", "new"], ["updated", "changed"], ["unchanged", "unchanged"]].filter(([key]) => counts[key]).map(([key, word]) => `${counts[key]} ${word}`);
  return `The DR bookings were imported${parts.length ? ` (${parts.join(", ")})` : ""}.`;
}

/** Under a row's AIM chip: its date's receipt and uploads, and Retry for a write AIM did not confirm. */
export function aimSendMarkup(entry, termId, escape) {
  if (!entry) return "";
  const parts = [];
  const retry = (send) => `<button type="button" class="text-button" data-dr-aim-retry="${escape(send.id)}" data-dr-term="${escape(termId)}">Retry</button>`;
  const held = (send) => ["pending", "uncertain"].includes(send.status);
  if (entry.date_send) {
    const send = entry.date_send;
    parts.push(send.status === "accepted" ? "<small>Date sent to AIM</small>"
      : held(send) ? `<small>Date not confirmed in AIM</small>${retry(send)}` : send.status === "failed" ? `<small>Date not added: ${escape(send.detail)}</small>` : "");
  }
  const uploaded = entry.uploads.filter((send) => send.status === "accepted").reduce((sum, send) => sum + send.requests, 0);
  if (uploaded) parts.push(`<small>Accessible copy uploaded to ${plural(uploaded, "request")}</small>`);
  entry.uploads.filter(held).forEach((send) => parts.push(`<small>Upload not confirmed</small>${retry(send)}`));
  return parts.join("");
}

/** The Accessible copy cell (QUIZZES "ACCESSIBLE COPY"): the screen-readable Word file of the row's approved quiz for the DR center,
 *  why it has none, or that the quiz was not drafted in Faculty. */
export function accessibleCopyMarkup(quiz, copies, escape) {
  const copy = quiz.assignment_id ? copies[quiz.assignment_id] : null;
  if (!copy) {
    return `<span class="chip" data-tone="neutral">${quiz.assignment_id ? "Not drafted in Faculty" : "No quiz yet"}</span>`;
  }
  if (!copy.ready) {
    return `<span class="chip" data-tone="warning">Not available</span><small>${escape(copy.reasons.join("; "))}</small>`;
  }
  const href = `/api/quiz-drafts/${encodeURIComponent(copy.draft_id)}/approvals/${encodeURIComponent(copy.approval_id)}/accessible/${encodeURIComponent(copy.version)}`;
  return `<a href="${escape(href)}" download>Word file</a>`;
}

/** The Date cell (SCHEDULING "CLASS SCHEDULE: DR QUIZZES" › DR DATE): the row's date; for a quiz given in class or one with a DR date,
 *  the class day beside it and, with controls, a date to set (or clear) the day its DR quiz takers sit it at the DR center. */
export function drDateMarkup(quiz, termId, escape, controls) {
  const day = (date) => `<time datetime="${escape(date)}">${escape(wallDate(date))}</time>`;
  if (!quiz.date) return escape(quiz.reason);
  const past = !quiz.dr_date && quiz.reason === "Given in class";
  if (!quiz.dr_date && !past) return day(quiz.date);
  const note = quiz.dr_date ? `<small>DR date${quiz.class_date ? ` · class ${escape(wallDate(quiz.class_date))}` : ""}</small>` : "<small>Given in class</small>";
  if (!controls) return `${day(quiz.date)}${note}`;
  const name = `${escape(quiz.title)}, section ${escape(quiz.section)}`;
  const term = `data-dr-term="${escape(termId)}"`;
  return `${day(quiz.date)}${note}<span class="dr-date-set"><input type="date" min="${escape(localToday())}" value="${escape(quiz.dr_date || "")}" data-dr-date-input="${escape(quiz.key)}" aria-label="DR date: ${name}">
    <span class="dr-date-actions"><button type="button" class="text-button" data-dr-date="${escape(quiz.key)}" ${term}>${quiz.dr_date ? "Change DR date" : "Set DR date"}</button>${quiz.dr_date
      ? `<button type="button" class="text-button" data-dr-date-clear="${escape(quiz.key)}" ${term} aria-label="Clear the DR date: ${name}">Clear</button>` : ""}</span></span>`;
}

/** One term's DR quizzes to preschedule (/api/scheduling/terms/<term_id>/dr-quizzes). Read only without `notices`; with the term's
 *  status read (…/aim-prescheduling) a Prescheduled column carries the mark, Undo, each student's notice and Retry; with `copies`
 *  (/api/quiz-drafts/accessible-copies) an Accessible copy column carries each quiz's Word file. */
export function drQuizzesMarkup(view, escape, notices = null, copies = null) {
  const tones = { ok: "success", warn: "warning", muted: "neutral" };
  const sections = view.sections.map((entry) => `Section ${entry.section} · ${plural(entry.dr_students, "DR student")}`);
  const byKey = new Map((notices?.quizzes || []).map((notice) => [notice.key, notice]));
  const sends = new Map((view.sends?.quizzes || []).map((entry) => [entry.key, entry]));
  const rows = view.quizzes.map((quiz) => {
    const aim = aimStatus(quiz.aim);
    const date = drDateMarkup(quiz, view.term_id, escape, Boolean(notices));
    const time = quiz.date && !quiz.start ? escape(quiz.reason) : `${escape(quiz.time)}${quiz.time_source === "class" ? "<small>Class time</small>" : ""}`;
    // No booking yet is plain muted words, not a framed chip: nothing is wrong and nothing waits on you (DESIGN-LANGUAGE §1.5, §1.6).
    const booked = aim.tone === "muted" ? `<span class="dr-aim-none">${escape(aim.text)}</span>` : `<span class="chip" data-tone="${tones[aim.tone]}">${escape(aim.text)}</span>`;
    const prescheduled = notices ? `<td data-label="Prescheduled" class="dr-notice">${noticeCellMarkup(quiz, byKey.get(quiz.key), view.term_id, escape)}</td>` : "";
    return `<tr><td data-label="Quiz" class="dr-nowrap">${escape(quiz.title)}${quiz.place ? `<small>${escape(quiz.place)}</small>` : ""}</td><td data-label="Section">${escape(quiz.section)}</td>
      <td data-label="Date" class="dr-date">${date}</td><td data-label="Time" class="dr-nowrap">${time}</td><td data-label="Length" class="dr-nowrap">${escape(quiz.minutes)} min</td>
      <td data-label="Booked in AIM" class="dr-aim">${booked}${aimSendMarkup(sends.get(quiz.key), view.term_id, escape)}</td>${prescheduled}${copies
        ? `<td data-label="Accessible copy" class="dr-copy">${accessibleCopyMarkup(quiz, copies, escape)}</td>` : ""}</tr>`;
  }).join("");
  const columns = `<th scope="col">Quiz</th><th scope="col">Section</th><th scope="col">Date</th><th scope="col">Time</th><th scope="col">Length</th><th scope="col">Booked in AIM</th>${notices ? '<th scope="col">Prescheduled</th>' : ""}${copies ? '<th scope="col">Accessible copy</th>' : ""}`;
  const body = rows ? `<div class="roster-table-scroll dr-table-scroll" tabindex="0" role="region" aria-label="${escape(view.code)} DR quizzes; scroll to see all columns"><table class="roster-table dr-table"><thead><tr>${columns}</tr></thead><tbody>${rows}</tbody></table></div>`
    : emptyMarkup({ icon: "calendar", title: "No upcoming quizzes on the class schedule for sections with DR students." });
  return `<section class="dr-group"><header class="dr-group-head"><div class="dr-group-text"><h3>${escape(view.code)} · ${escape(view.term_label)}</h3>
    ${sections.length ? `<p class="dr-group-meta">${sections.map(escape).join(" · ")}</p>` : ""}</div>${notices && view.sends?.quizzes?.length
      ? `<button type="button" class="button" data-outward data-dr-send="${escape(view.term_id)}">Send to AIM</button>` : ""}</header>${body}</section>`;
}

/** Students left by the term, course and search filters. */
export function filterStudents(students, { term = "current", course = "", search = "" } = {}) {
  const query = search.trim().toLowerCase();
  return students.filter((student) => (term === "all" || (term === "current" ? student.is_current : termKey(student) === term))
    && (!course || same(student.code, course))
    && (!query || `${student.name} ${student.uin}`.toLowerCase().includes(query)));
}

/** A booking belongs to an exam named by the DR center's exam name or by the Faculty assignment it is linked to. */
export function examMatches(booking, exam) {
  return !exam || same(booking.exam_type, exam) || same(booking.assignment?.title, exam);
}

/** Campus dates compare as YYYY-MM-DD strings: upcoming is today or later, never converted through a time zone. */
export function inWhen(booking, when, today) {
  return when === "all" || (when === "past" ? booking.date < today : booking.date >= today);
}

export function bookingRows(students, { exam = "", when = "upcoming" } = {}, today = localToday()) {
  return students.flatMap((student) => (student.dr_exams || [])
    .filter((booking) => examMatches(booking, exam) && inWhen(booking, when, today))
    .map((booking) => ({ student, booking })));
}

/** '7 at 7:05 PM · 1 at 6:30 PM': how the DR center spread one exam's start times. */
export function timeSpread(rows) {
  const counts = new Map();
  for (const { booking } of rows) {
    const start = scheduleTime({ ...booking, end_time: booking.start_time }).split("–")[0];
    counts.set(start, (counts.get(start) || 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1]).map(([time, count]) => `${count} at ${time}`).join(" · ");
}

/**
 * Bookings grouped by the Faculty assignment they are linked to, in its course and term (unlinked bookings by the DR
 * center's exam name). Upcoming and All list the soonest exam first, Past the latest.
 */
export function groupBookings(rows, when = "upcoming") {
  const groups = new Map();
  for (const row of rows) {
    const { student, booking } = row;
    const assignment = booking.assignment || null;
    const key = assignment
      ? `a:${assignment.id}:${student.term_id || termKey(student)}`
      : `x:${booking.exam_type.toLowerCase()}:${student.code}:${student.term_id || termKey(student)}`;
    if (!groups.has(key)) {
      groups.set(key, { key, assignment, title: assignment?.title || booking.exam_type, code: student.code,
        term: termLabel(student), term_id: student.term_id || "", rows: [] });
    }
    groups.get(key).rows.push(row);
  }
  const order = (a, b) => `${a.booking.date} ${a.booking.start_time} ${a.student.name}`.localeCompare(`${b.booking.date} ${b.booking.start_time} ${b.student.name}`);
  const list = [...groups.values()].map((group) => {
    group.rows.sort(order);
    group.first = group.rows[0].booking.date;
    group.last = group.rows.at(-1).booking.date;
    group.names = [...new Set(group.rows.map(({ booking }) => booking.exam_type))].filter((name) => !same(name, group.title));
    group.campuses = [...new Set(group.rows.map(({ booking }) => booking.campus || ""))];
    group.unlinked = group.rows.some(({ booking }) => !booking.assignment);
    return group;
  });
  return list.sort((a, b) => when === "past" ? b.last.localeCompare(a.last) || a.title.localeCompare(b.title) : a.first.localeCompare(b.first) || a.title.localeCompare(b.title));
}

/** '14 students with accommodations · 24 upcoming DR bookings · next: Test 2, Thu, Oct 22 · 8 students', over current terms. */
export function statusLine(students, today = localToday()) {
  const current = students.filter((student) => student.is_current);
  const withRecords = current.filter((student) => student.accommodations?.items?.length).length;
  const upcoming = current.flatMap((student) => (student.dr_exams || []).filter((booking) => booking.date >= today));
  const parts = [plural(withRecords, "student") + " with accommodations", upcoming.length ? plural(upcoming.length, "upcoming DR booking") : "no upcoming DR bookings"];
  if (upcoming.length) {
    const first = upcoming.reduce((soonest, booking) => booking.date < soonest ? booking.date : soonest, upcoming[0].date);
    const name = (booking) => booking.assignment?.title || booking.exam_type;
    const onDay = upcoming.filter((booking) => booking.date === first);
    const title = name(onDay[0]);
    parts.push(`next: ${title}, ${wallDate(first)} · ${plural(onDay.filter((booking) => name(booking) === title).length, "student")}`);
  }
  return parts.join(" · ");
}

/** When each export was last imported, linked to its Inbox record, and how many rows it could not import. */
export function freshnessMarkup(last, escape, today = localToday()) {
  const part = (label, item, missing) => {
    if (!item) return `<span>${missing}</span>`;
    const day = localToday(new Date(item.imported_at));
    const ago = daysFrom(day, today);
    return `<span>${escape(label)} imported <a href="#inbox/job/${escape(item.job_id)}">${escape(wallDate(day))}</a>${ago && ago !== "today" ? ` (${escape(ago)})` : ago === "today" ? " (today)" : ""}${item.not_imported ? ` · <a class="accommodation-unmatched" href="#inbox/job/${escape(item.job_id)}">${plural(item.not_imported, "row")} weren’t imported</a>` : ""}</span>`;
  };
  return `${part("Accommodations", last?.accommodations, "No accommodation export imported yet")}<span class="accommodation-sep" aria-hidden="true"> · </span>${part("DR bookings", last?.dr_exams, "No DR export imported yet")}`;
}

/** Accommodation types with counts, most common first (the chips that filter the student table). */
export function typeCounts(students) {
  const counts = new Map();
  for (const student of students) for (const item of student.accommodations?.items || []) counts.set(item.name, (counts.get(item.name) || 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** The exam's charts from the LIVE check (`chart.check`), never the counts recorded when the chart was generated: a DR
 *  booking imported afterwards leaves its student seated until the chart is updated, and that is what this must say. */
export function seatingLinksMarkup(charts, escape) {
  const live = (chart) => chart.check || null;
  const stale = (chart) => live(chart) ? live(chart).ok === false : chart.counts?.check_ok === false;
  const seated = charts.reduce((total, chart) => total + (Number(live(chart)?.counts?.dr_seated) || 0), 0);
  const known = charts.every((chart) => live(chart));
  const tail = seated
    ? ` · <strong class="dr-group-seated">${plural(seated, "DR student")} still seated · update the chart</strong>`
    : known ? " · not seated there (DR testing)" : "";
  return `<span class="dr-group-seating">Seating ${charts.map((chart) => `<a href="#seating/${escape(encodeURIComponent(chart.id))}">${escape(chart.room_name || chart.title)}</a>${stale(chart) ? ' <span class="chip" data-tone="warning">needs update</span>' : ""}`).join(", ")}${tail}</span>`;
}

/** What Import from AIM brought back: each export and whether it is imported, and one Review and import while anything
    is not. `aim` lives in browser memory only: {term, files: {kind: File}, problems, done}. */
export function aimMarkup(aim, escape) {
  if (!aim) return "";
  const names = { accommodations: "Accommodation requests", exams: "Upcoming DR exams" };
  const kinds = AIM_KINDS.filter((kind) => aim.files[kind]);
  const waiting = kinds.filter((kind) => !aim.done[kind]);
  const rows = kinds.map((kind) => `<li><span class="accommodation-aim-kind">${names[kind]}</span><span class="accommodation-aim-file">${escape(aim.files[kind].name)}</span>${aim.done[kind]
    ? `<span class="chip" data-tone="success">Imported</span>` : `<span class="chip">Not imported yet</span>`}</li>`).join("");
  const problems = (aim.problems || []).length ? `<ul class="accommodation-aim-problems">${aim.problems.map((text) => `<li>${escape(text)}</li>`).join("")}</ul>` : "";
  const lead = waiting.length ? "Nothing is saved until you import." : `${kinds.length === 1 ? "The export is" : "Both exports are"} imported.`;
  return `<div class="callout accommodation-aim" data-tone="${waiting.length ? "info" : "success"}" role="status"><p class="callout-title">From AIM${aim.term ? ` · ${escape(aim.term)}` : ""}</p><p>${lead}</p><ul class="accommodation-aim-files">${rows}</ul>${problems}
    <div class="accommodation-aim-actions">${waiting.length ? `<button type="button" class="button primary" data-aim-review>Review and import</button>` : ""}<button type="button" class="text-button" data-aim-dismiss>Dismiss</button></div></div>`;
}

const TYPE_CHIPS = 6;  // the most common types; "N more types" lists the rest (18 types made five rows on 3311)

/** The accommodation-type chips shown: the `limit` most common (ties by name, as `typeCounts` sorts them), the selected one
 *  when it is not among them, and how many more there are; every type when `all`. @param {[string, number][]} types
 *  @param {string} selected @param {boolean} all @param {number} [limit] */
export function shownTypes(types, selected, all, limit = TYPE_CHIPS) {
  if (all || types.length <= limit + 1) return { shown: types, hidden: 0 };
  const shown = types.slice(0, limit);
  const chosen = types.find(([name]) => name === selected);
  if (chosen && !shown.includes(chosen)) shown.push(chosen);
  return { shown, hidden: types.length - shown.length };
}

export function createAccommodations({api, escape, showDialog, dialogHeader, toast, header, openStudentRecord = null}) {
  let generation = 0, data = null, signature = "", fetchedAt = 0, inflight = null, lastHash = null, opening = 0;
  let tab = "students", term = "current", course = "", search = "", exam = "", when = "upcoming", type = "";
  let moreTypes = false;  // every type chip shown (browser memory only)
  let links = null, linksAt = 0, linksLoading = null, bound = false;
  let printNode = null, printSheet = null;
  let aim = null;
  let quizViews = null, quizzesAt = 0, quizzesLoading = null;  // each current term's DR quizzes, read when the tab is shown
  let noticePoll = null;  // the one pending 5 s read while a DR quiz notice waits to send

  const root = () => document.querySelector("#accommodations-section");

  /* Import from AIM (aim_import.py, static/aim-signin.js): the instructor signs in to a private browser on the server;
     its exports come back as files that stay in memory and open the ordinary import dialogs with their previews run.
     Nothing is imported without the instructor's click. */
  function importFromAim() {
    void openAimSignIn({ api, showDialog, dialogHeader, escape, onExported: (result) => {
      aim = { term: result.term, files: result.files, problems: result.problems, done: {} };
      drawAim();
      reviewAim();
    } });
  }

  /** Both exports' previews in one dialog and one Import (accommodations.js importAimExports). */
  function reviewAim() {
    const current = aim;
    if (!current) return;
    const files = Object.fromEntries(AIM_KINDS.filter((kind) => current.files[kind] && !current.done[kind]).map((kind) => [kind, current.files[kind]]));
    if (!Object.keys(files).length) return;
    void importAimExports({ api, showDialog, dialogHeader, escape, toast, files, term: current.term, problems: current.problems,
      onImported: (kinds) => { if (aim === current) { kinds.forEach((kind) => { current.done[kind] = true; }); drawAim(); } } });
  }

  function drawAim() {
    const slot = root()?.querySelector("[data-acc-aim]");
    if (slot) patchChildren(slot, aimMarkup(aim, escape));
  }

  function setHeader() {
    header?.set({ route: location.hash, title: "Accommodations", subtitle: "Who needs what, and who tests at the DR center.",
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Accommodations" }], action: null });
  }

  function applyRoute() {
    const route = readRoute(location.hash);
    tab = route.tab;
    if (route.when !== undefined) when = route.when;
    if (route.course !== undefined) course = route.course;
    if (route.exam !== undefined) exam = route.exam;
  }

  /** Keep the address true to the filters without a navigation (no hashchange, no student data). */
  function syncRoute() {
    const hash = routeHash({ tab, when, course, exam });
    if (hash !== location.hash) history.replaceState(history.state, "", `${location.pathname}${location.search}${hash}`);
    lastHash = location.hash;
  }

  async function read(force) {
    // A forced read (Refresh, a finished import) always asks again: a read already in flight may predate the import.
    // Unforced renders share the latest read.
    if (!force && inflight) return inflight;
    if (!force && data && Date.now() - fetchedAt < FRESH) return data;
    const pending = api("/api/accommodations/overview").finally(() => { if (inflight === pending) inflight = null; });
    inflight = pending;
    const result = await pending;
    fetchedAt = Date.now();
    return result;
  }

  async function readLinks() {
    if (links && Date.now() - linksAt < LINKS_FRESH) return links;
    if (linksLoading) return linksLoading;
    linksLoading = Promise.allSettled([api("/api/state?include=seating"), api("/api/exams?summary=0")]).then(([seating, exams]) => {
      links = {
        charts: seating.status === "fulfilled" && Array.isArray(seating.value?.seating_charts) ? seating.value.seating_charts : null,
        exams: exams.status === "fulfilled" && Array.isArray(exams.value?.exams) ? exams.value.exams : null,
      };
      linksAt = Date.now();
      return links;
    }).finally(() => { linksLoading = null; });
    return linksLoading;
  }

  function readQuizzes() {
    const terms = [...new Set(data.students.filter((student) => student.is_current && student.term_id).map((student) => student.term_id))];
    // Each term's DR quizzes beside its notice status (SCHEDULING "DR QUIZ NOTICES") and its accessible copies (QUIZZES "ACCESSIBLE
    // COPY"); a failed status read leaves the list without controls, a failed copies read without that column.
    const readTerm = (id) => {
      const base = `/api/scheduling/terms/${encodeURIComponent(id)}`;
      return Promise.allSettled([api(`${base}/dr-quizzes`), api(`${base}/aim-prescheduling`),
        api(`/api/quiz-drafts/accessible-copies?term_id=${encodeURIComponent(id)}`), api(`${base}/aim-send`)]).then(([list, notices, copies, sends]) => {
        if (list.status !== "fulfilled") return { term_id: id, error: list.reason?.message || "Could not read the DR quizzes." };
        const accessible = copies.status === "fulfilled" ? copies.value.assignments : null;
        const sent = sends.status === "fulfilled" ? sends.value : null;
        return notices.status === "fulfilled" ? { ...list.value, notices: notices.value, copies: accessible, sends: sent }
          : { ...list.value, notices: null, copies: accessible, sends: sent, noticesError: notices.reason?.message || "Could not read the DR quiz notices." };
      });
    };
    quizzesLoading ||= Promise.all(terms.map(readTerm)).then((found) => {
      quizViews = found;
      quizzesAt = Date.now();
      return quizViews;
    }).finally(() => { quizzesLoading = null; });
    return quizzesLoading;
  }

  function quizzesMarkup() {
    if (!quizViews) return loadingMarkup("Loading DR quizzes…", 4);
    if (!quizViews.length) return emptyMarkup({ icon: "calendar", title: "No current course has an imported accommodation or DR booking." });
    return quizViews.map((view) => view.error ? `<p role="alert">${escape(view.error)}</p>`
      : `${view.noticesError ? `<p role="alert">${escape(view.noticesError)}</p>` : ""}${drQuizzesMarkup(view, escape, view.notices, view.copies)}`).join("");
  }

  const quizzesShown = () => tab === "quizzes" && !root()?.hidden;

  /** Patch the DR quizzes tab in place, then keep reading every 5 s while a notice waits to send (FRONTEND quiet refresh). */
  function drawQuizzes() {
    const results = root()?.querySelector("#accommodation-results");
    if (!results || !quizzesShown()) return;
    patchChildren(results, quizzesMarkup());
    pollNotices();
  }

  function pollNotices() {
    if (noticePoll || !quizzesShown() || document.hidden || !quizViews?.some((view) => noticesWaiting(view.notices))) return;
    noticePoll = setTimeout(() => {
      noticePoll = null;
      if (!quizzesShown() || document.hidden) return;
      void readQuizzes().then(drawQuizzes);
    }, 5000);
  }

  /** The shared dialog as the confirm of a mark or a Retry: Send resolves true, any other close false, once. */
  function ask({ title, body, send }) {
    return new Promise((resolve) => {
      showDialog(`<form id="dr-confirm-form">${dialogHeader(escape(title))}${body ? `<div class="dialog-body"><p>${escape(body)}</p></div>` : ""}<footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${escape(send)}</button></footer></form>`);
      const dialog = document.getElementById("dialog");
      let settled = false;
      const settle = (value) => {
        if (settled) return;
        settled = true;
        dialog.removeEventListener("close", closed);
        resolve(value);
      };
      const closed = () => settle(false);
      dialog.addEventListener("close", closed);
      document.getElementById("dr-confirm-form").onsubmit = (event) => { event.preventDefault(); settle(true); dialog.close(); };
    });
  }

  /** A mark, Undo or Retry: the button is busy only while its post is in flight; the answered status read is patched in. */
  async function noticeAction(button, prompt, request) {
    const post = (path, method, body) => { setBusy(button, true); return api(path, method, body); };
    try {
      // Undo sends nothing, so it posts at once: sendConfirmed with a confirm that is already Send.
      const answer = await sendConfirmed(prompt, request, { ask: prompt ? ask : async () => true, api: post });
      if (!answer) return;
      const view = quizViews?.find((item) => item.term_id === request.termId && !item.error);
      if (view) { view.notices = answer; view.noticesError = ""; }
      drawQuizzes();
    } catch (error) {
      toast(error.message, true);
      quizzesAt = 0;
      drawResults();
    } finally {
      setBusy(button, false);
    }
  }

  /** Send to AIM: the confirm listing what it does, then AIM's sign-in window; its export opens the ordinary import review after
   *  a summary of what was done in AIM. */
  async function sendToAim(button) {
    const termId = button.dataset.drSend;
    const view = quizViews?.find((item) => item.term_id === termId && !item.error);
    if (!view) return;
    setBusy(button, true);
    let plan;
    try {
      plan = await api(`/api/scheduling/terms/${encodeURIComponent(termId)}/aim-send`);
    } catch (error) {
      toast(error.message, true);
      return;
    } finally {
      setBusy(button, false);
    }
    const prompt = aimSendPrompt(plan);
    if (!prompt) { toast("No DR quiz of this term is ready to send."); return; }
    showDialog(`<form id="dr-send-form">${dialogHeader(escape(prompt.title))}<div class="dialog-body"><ul class="dr-send-list">${prompt.lines.map((line) => `<li>${escape(line)}</li>`).join("")}</ul>
      <p class="field-hint">${escape(prompt.note)}</p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${escape(prompt.send)}</button></footer></form>`);
    document.getElementById("dr-send-form").onsubmit = (event) => {
      event.preventDefault();
      void openAimSend({ api, showDialog, dialogHeader, escape,
        start: { route: `/api/scheduling/terms/${encodeURIComponent(termId)}/aim-send`, body: { request_id: crypto.randomUUID(), fingerprint: view.fingerprint } },
        onClosed: () => { quizzesAt = 0; drawResults(); },
        // The usual end: Faculty imported the DR bookings itself; the summary says what was done in AIM.
        onDone: (status) => {
          quizzesAt = 0;
          document.dispatchEvent(new CustomEvent("accommodations-imported"));
          showSent(status.sent, status.imported ? sentImport(status.imported) : "", null);
        },
        // The bookings could not be imported by themselves: the summary, then the ordinary import review.
        onExported: (result) => {
          aim = { term: result.term, files: result.files, problems: result.problems, done: {} };
          drawAim();
          quizzesAt = 0;
          showSent(result.sent, "The DR bookings were not imported by themselves: review the import.", "Review the DR bookings import");
        } });
    };
  }

  function showSent(sent, note, review) {
    const lines = sentLines(sent);
    showDialog(`<form id="dr-sent-form">${dialogHeader("Sent to AIM")}<div class="dialog-body"><ul class="dr-send-list">${lines.length ? lines.map((line) => `<li><span class="chip" data-tone="${escape(line.tone)}">${escape(line.text)}</span>${line.detail ? `<small>${escape(line.detail)}</small>` : ""}</li>`).join("")
      : "<li>Nothing needed sending.</li>"}</ul>${note ? `<p class="field-hint">${escape(note)}</p>` : ""}</div><footer class="dialog-footer"><button type="submit" class="button primary">${escape(review || "Done")}</button></footer></form>`);
    document.getElementById("dr-sent-form").onsubmit = (submitted) => {
      submitted.preventDefault();
      if (review) reviewAim(); else document.querySelector("#dialog").close();
    };
  }

  async function aimRetry(button) {
    const termId = button.dataset.drTerm;
    if (!await ask({ title: "Let the next Send try this again?", body: "Check AIM first: if it already shows this, Retry could add it twice.", send: "Retry" })) return;
    setBusy(button, true);
    try {
      await api(`/api/scheduling/terms/${encodeURIComponent(termId)}/aim-send/retry`, "POST", { request_id: crypto.randomUUID(), send_id: button.dataset.drAimRetry });
      quizzesAt = 0;
      drawResults();
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  /** Set or clear a row's DR date (SCHEDULING "CLASS SCHEDULE: DR QUIZZES" › DR DATE); the list is read again after. */
  async function saveDrDate(button) {
    const key = button.dataset.drDate || button.dataset.drDateClear;
    const input = /** @type {HTMLInputElement | null} */ (button.closest(".dr-date-set")?.querySelector("[data-dr-date-input]"));
    const date = button.dataset.drDateClear ? "" : input?.value || "";
    if (!button.dataset.drDateClear && !date) { toast("Pick the day the DR quiz takers sit it.", true); input?.focus(); return; }
    setBusy(button, true);
    try {
      await api(`/api/scheduling/terms/${encodeURIComponent(button.dataset.drTerm)}/dr-quizzes/dr-date`, "POST", { request_id: crypto.randomUUID(), key, date });
      quizzesAt = 0;
      drawResults();
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  function quizControl(target) {
    const send = target.closest("[data-dr-send]");
    if (send) { void sendToAim(send); return true; }
    const aimRetryButton = target.closest("[data-dr-aim-retry]");
    if (aimRetryButton) { void aimRetry(aimRetryButton); return true; }
    const dateButton = target.closest("[data-dr-date], [data-dr-date-clear]");
    if (dateButton) { void saveDrDate(dateButton); return true; }
    const button = target.closest("[data-dr-mark], [data-dr-unmark], [data-dr-retry]");
    if (!button) return false;
    const termId = button.dataset.drTerm;
    const view = quizViews?.find((item) => item.term_id === termId && !item.error);
    if (button.dataset.drMark) {
      const notice = view?.notices?.quizzes.find((item) => item.key === button.dataset.drMark);
      if (notice) void noticeAction(button, markPrompt(notice), { action: "mark", termId, target: notice.key, fingerprint: view.fingerprint });
    } else if (button.dataset.drUnmark) {
      void noticeAction(button, null, { action: "unmark", termId, target: button.dataset.drUnmark });
    } else {
      void noticeAction(button, retryPrompt({ name: button.dataset.drName }), { action: "retry", termId, target: button.dataset.drRetry });
    }
    return true;
  }

  async function render(force = false) {
    const host = root();
    if (!host) return;
    setHeader();
    if (force) quizzesAt = 0;
    const run = ++generation;
    const routeChanged = location.hash !== lastHash;
    if (routeChanged) applyRoute();
    if (!host.querySelector("[data-acc-frame]")) {
      host.innerHTML = `<div class="accommodation-page" data-acc-frame><div data-acc-aim></div><div class="accommodation-head" data-acc-head></div><div id="accommodation-results"></div></div>`;
      bind(host);
      drawAim();
      if (data) draw(); else host.querySelector("#accommodation-results").innerHTML = loadingMarkup("Loading accommodations…", 4);
    } else if (routeChanged && data) {
      draw();
    }
    try {
      const result = await read(force);
      if (run !== generation) return;
      const next = JSON.stringify(result);
      if (next !== signature || !host.querySelector("[data-acc-head]")?.childElementCount) {
        data = result;
        signature = next;
        if (!root()?.hidden) draw();
      } else if (quizzesShown() && Date.now() - quizzesAt >= FRESH) {
        drawResults();  // the DR quizzes and their notices are read apart from the overview
      }
    } catch (error) {
      if (run !== generation || root()?.hidden) return;
      if (!data) {
        host.querySelector("#accommodation-results").innerHTML = `<p role="alert">${escape(error.message)}</p><button class="button" type="button" data-accommodation-retry>Retry</button>`;
      }
    }
  }

  /* A student's name opens their record from the gradebook (requirement, 2026-09-30): the card a name opens in the section's
     gradebook, read for that section when clicked. */
  function nameMarkup(student) {
    if (!openStudentRecord || !student.term_id) return `<strong class="accommodation-name">${escape(student.name)}</strong>`;
    return `<button type="button" class="accommodation-name accommodation-name-button" data-student-record="${escape(`acc:${student.section_id}:${student.id}`)}" aria-haspopup="dialog">${escape(student.name)}<span class="sr-only">: open the record</span></button>`;
  }

  /** Only the latest click opens, and only while this page is still the one shown. */
  function openRecord(key) {
    const student = data?.students.find((item) => `acc:${item.section_id}:${item.id}` === key);
    if (!student || !openStudentRecord) return;
    const token = ++opening, page = location.hash.split("?")[0];
    void openStudentRecord({ termId: student.term_id, sectionId: student.section_id, enrollmentId: student.id,
      active: () => token === opening && location.hash.split("?")[0] === page && !root()?.hidden });
  }

  function openKeys() {
    return new Set([...(root()?.querySelectorAll("details[data-key][open]") || [])].map((node) => node.dataset.key));
  }

  function draw() {
    const host = root();
    if (!host || !data) return;
    const exams = tab === "exams";
    // A deep link's course or exam in another case matches the saved spelling.
    const codes = [...new Set(data.students.map((student) => student.code))].sort();
    course = codes.find((code) => same(code, course)) || course;
    if (course && !codes.includes(course)) codes.push(course);
    const examNames = [...new Set(data.students.flatMap((student) => (student.dr_exams || []).map((booking) => booking.exam_type)))].sort();
    exam = examNames.find((name) => same(name, exam)) || exam;
    if (exam && !examNames.includes(exam)) examNames.unshift(exam);
    if (!["current", "all"].includes(term) && !data.terms.some((item) => termKey(item) === term)) term = "current";
    syncRoute();
    const opened = openKeys();
    const head = host.querySelector("[data-acc-head]");
    patchChildren(head, headMarkup(codes, examNames, exams));
    const termSelect = head.querySelector("#accommodation-term"), courseSelect = head.querySelector("#accommodation-course");
    const examSelect = head.querySelector("#accommodation-exam"), searchInput = head.querySelector("#accommodation-search");
    if (termSelect) termSelect.value = term;
    if (courseSelect) courseSelect.value = course;
    if (examSelect) examSelect.value = exam;
    if (searchInput && searchInput.value !== search) searchInput.value = search;
    drawResults(opened);
  }

  function filtersActive() {
    return Boolean(course || (tab === "exams" && exam) || search.trim() || (tab === "students" && type) || term !== "current");
  }

  function headMarkup(codes, examNames, exams) {
    const students = data.students;
    // Imports are setup, never the page's main action (DESIGN-LANGUAGE §1.4; requirement, 2026-10-04):
    // Import from AIM is the one framed button; Capture AIM's form and the two uploads (an export saved by hand) are text actions
    // after it, so the row reads as one action and its alternatives (requirement, 2026-11-01).
    const other = (name) => `<button type="button" class="text-button" data-action="${name === "exams" ? "import-dr-exams" : "import-accommodations"}">${name === "exams" ? "Import DR exam bookings" : "Import accommodations"}</button>`;
    const terms = data.terms;
    const showTerm = terms.length > 1 || terms.some((item) => !item.is_current);
    const showCourse = codes.length > 1;
    const showExam = exams && (examNames.length > 1 || exam);
    const base = filterStudents(students, { term, course, search });
    const whenCounts = Object.fromEntries(WHEN.map((value) => [value, bookingRows(base, { exam, when: value }).length]));
    const selects = [
      showTerm ? `<label class="field">Teaching term<select id="accommodation-term" aria-label="Teaching term"><option value="current">Current terms</option><option value="all">All terms</option>${terms.map((item) => `<option value="${escape(termKey(item))}">${escape(termLabel(item))}</option>`).join("")}</select></label>` : "",
      showCourse ? `<label class="field">Course<select id="accommodation-course" aria-label="Course"><option value="">All courses</option>${codes.map((code) => `<option value="${escape(code)}">${escape(code)}</option>`).join("")}</select></label>` : "",
      showExam ? `<label class="field">Exam<select id="accommodation-exam" aria-label="Exam"><option value="">All exams</option>${examNames.map((name) => `<option value="${escape(name)}">${escape(name)}</option>`).join("")}</select></label>` : "",
    ].filter(Boolean);
    const active = filtersActive();
    const types = exams ? [] : typeCounts(base.filter((student) => student.accommodations));
    if (type && !types.some(([name]) => name === type)) type = "";
    return `<div class="accommodation-status">
        <p class="accommodation-status-line">${escape(statusLine(students))}</p>
        <p class="accommodation-freshness">${freshnessMarkup(data.last_imports, escape)}<button type="button" class="icon-button accommodation-refresh" data-accommodation-refresh aria-label="Refresh" title="Refresh">${icon("refresh")}</button></p>
      </div>
      <div class="accommodation-actions"><button type="button" class="button" data-aim-import>Import from AIM</button><button type="button" class="text-button" data-aim-capture>Capture AIM's form</button>${other(exams ? "exams" : "students")}${other(exams ? "students" : "exams")}</div>
      <nav class="accommodation-views sub-tabs" aria-label="Accommodations views"><a href="${escape(routeHash({ tab: "students", course }))}" ${tab === "students" ? 'aria-current="page"' : ""}>Student accommodations</a><a href="${escape(routeHash({ tab: "exams", when, course, exam }))}" ${exams ? 'aria-current="page"' : ""}>DR exam schedule</a><a href="${escape(routeHash({ tab: "quizzes" }))}" ${tab === "quizzes" ? 'aria-current="page"' : ""}>DR quizzes</a></nav>
      ${tab === "quizzes" ? "" : `<div class="accommodation-toolbar">
        ${exams ? `<div class="accommodation-tabs accommodation-when" role="group" aria-label="Dates shown">${WHEN.map((value) => `<button type="button" data-when="${value}" aria-pressed="${when === value}">${value[0].toUpperCase()}${value.slice(1)} <b>${whenCounts[value].toLocaleString()}</b></button>`).join("")}</div>` : ""}
        <label class="field accommodation-search">Find a student<input id="accommodation-search" type="search" data-page-search placeholder="Name or UIN" autocomplete="off"></label>
        ${selects.length ? `<div class="accommodation-filters" id="accommodation-filters" role="group" aria-label="Filter the records shown below">${selects.join("")}</div>` : ""}
        ${active ? `<button type="button" class="text-button accommodation-clear" data-clear-filters>Clear filters</button>` : ""}
      </div>`}
      ${types.length > 1 && tab === "students" ? typeChipsMarkup(types, base) : ""}`;
  }

  /** All, the most common types (and the chosen one), then "N more types" / "Fewer types" (not a filter: it only shows more). */
  function typeChipsMarkup(types, base) {
    const { shown, hidden } = shownTypes(types, type, moreTypes);
    const chips = filterChipsMarkup("acc-type", [{ value: "", label: "All", count: types.length ? base.filter((student) => student.accommodations).length : 0 },
      ...shown.map(([name, count]) => ({ value: name, label: name, count }))], type, { label: "Accommodation types" });
    const toggle = hidden ? `<button type="button" class="accommodation-more-types" data-acc-more aria-expanded="false">${plural(hidden, "more type")}</button>`
      : moreTypes && types.length > TYPE_CHIPS + 1 ? '<button type="button" class="accommodation-more-types" data-acc-more aria-expanded="true">Fewer types</button>' : "";
    return toggle ? chips.replace(/<\/div>$/, `${toggle}</div>`) : chips;
  }

  function drawResults(opened = openKeys()) {
    const host = root();
    const results = host?.querySelector("#accommodation-results");
    if (!results || !data) return;
    if (tab === "quizzes") {
      drawQuizzes();
      if (!quizViews || Date.now() - quizzesAt >= FRESH) void readQuizzes().then(drawQuizzes);
      return;
    }
    const students = filterStudents(data.students, { term, course, search });
    patchChildren(results, tab === "exams" ? examsMarkup(students, opened) : studentsMarkup(students, opened));
    if (tab === "exams" && data.students.some((student) => student.dr_exams?.length) && (!links || Date.now() - linksAt > LINKS_FRESH)) {
      // Drawn from the data on hand when the links arrive, whatever render came since: the shell's second render of a page
      // reads the same overview and draws nothing, so a check of its generation here left the groups without their links.
      void readLinks().then(() => { if (tab === "exams" && !root()?.hidden) drawResults(); });
    }
  }

  function studentsMarkup(students, opened) {
    const all = data.students.filter((student) => student.accommodations);
    const withRecords = students.filter((student) => student.accommodations);
    const records = withRecords.filter((student) => !type || student.accommodations.items.some((item) => item.name === type));
    const today = localToday();
    const count = `<p class="accommodation-count section-lead" aria-live="polite">${plural(records.length, "student record")}${type ? ` with ${escape(type)}` : ""}</p>`;
    if (!records.length) {
      return count + (all.length
        ? emptyMarkup({ icon: "search", kind: "filtered", title: "No students match these filters", body: "Change the filters or clear them to see every record.", action: { label: "Clear filters", data: { clearFilters: "" } } })
        : emptyMarkup({ icon: "users", title: "No accommodations imported yet", body: "Import the accommodation export with the button above; each student is matched to their course and section." }));
    }
    const rows = records.map((student) => {
      const key = `acc:${student.section_id}:${student.id}`;
      const upcoming = (student.dr_exams || []).filter((booking) => booking.date >= today);
      const next = upcoming[0];
      const dr = next
        ? `<button type="button" class="text-button accommodation-next" data-dr-next="${escape(key)}">Next: ${escape(next.assignment?.title || next.exam_type)} · ${escape(wallDate(next.date))}</button>${upcoming.length > 1 ? `<small>${plural(upcoming.length - 1, "more booking")}</small>` : ""}`
        : student.dr_exams?.length ? `<span class="field-hint">${plural(student.dr_exams.length, "past booking")}</span>` : '<span class="field-hint">—</span>';
      return `<tr><td data-label="Student">${nameMarkup(student)}<small>${escape(student.uin)}</small>${takerMarkup(student.id, student.dr_quiz_taker, escape, student.dr_quiz_taker_offered)}</td>
        <td data-label="Course & section"><a href="${escape(student.course_url)}">${escape(student.code)} · ${escape(student.section)}</a><small>${escape(termLabel(student))}</small></td>
        <td data-label="Accommodations">${accommodationTags(student.accommodations, escape, { key, open: opened.has(key) })}</td>
        <td data-label="DR bookings">${dr}</td></tr>`;
    }).join("");
    return `${count}<div class="roster-table-scroll accommodation-table-scroll" tabindex="0" role="region" aria-label="Student accommodations; scroll to see all columns"><table class="roster-table accommodation-table"><thead><tr><th scope="col">Student</th><th scope="col">Course &amp; section</th><th scope="col">Accommodations</th><th scope="col">DR bookings</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function groupLinks(group) {
    if (!group.assignment) return `<span class="chip" data-tone="warning">Not linked to an assignment</span>`;
    const parts = [];
    if (links?.charts) {
      const charts = links.charts.filter((chart) => chart.assignment_id === group.assignment.id && (!group.term_id || chart.term_id === group.term_id));
      parts.push(charts.length ? seatingLinksMarkup(charts, escape) : `<span class="dr-group-note">No seating chart yet</span>`);
    }
    if (group.term_id) {
      const event = links?.exams?.find((item) => item.assignment_id === group.assignment.id && item.term_id === group.term_id);
      const href = event ? `#exams/${encodeURIComponent(event.id)}` : `#exams/assessment/${encodeURIComponent(group.assignment.id)}/${encodeURIComponent(group.term_id)}`;
      parts.push(`<a class="link-arrow" href="${escape(href)}">Exam hub</a>`);
    }
    return parts.join('<span aria-hidden="true"> · </span>');
  }

  function examsMarkup(students, opened) {
    const rows = bookingRows(students, { exam, when });
    const any = data.students.some((student) => student.dr_exams?.length);
    const people = new Set(rows.map(({ student }) => `${student.section_id}:${student.id}`)).size;
    const label = when === "all" ? "" : ` · ${when}`;
    const count = `<p class="accommodation-count section-lead" aria-live="polite">${plural(rows.length, "booking")}${rows.length ? ` · ${plural(people, "student")}` : ""}${label}</p>`;
    if (!rows.length) {
      if (!any) return count + emptyMarkup({ icon: "calendar", title: "No DR bookings yet", body: "Import the DR center’s export with Import DR exam bookings above." });
      const elsewhere = when !== "all" && bookingRows(students, { exam, when: "all" }).length;
      return count + (elsewhere
        ? emptyMarkup({ icon: "calendar", kind: "filtered", title: `No ${when} DR bookings`, body: `${plural(elsewhere, "booking")} on other dates match these filters.`, action: { label: "Show all dates", data: { when: "all" } } })
        : emptyMarkup({ icon: "search", kind: "filtered", title: "No bookings match these filters", body: "Change the filters or clear them to see every booking.", action: { label: "Clear filters", data: { clearFilters: "" } } }));
    }
    const groups = groupBookings(rows, when);
    return count + groups.map((group) => {
      const span = group.first === group.last ? wallDate(group.first) : `${wallDate(group.first)} – ${wallDate(group.last)}`;
      const campus = group.campuses.length > 1;
      const meta = [`${group.code} · ${group.term}`, span, plural(group.rows.length, "booking"), timeSpread(group.rows), !campus && group.campuses[0] ? group.campuses[0] : ""].filter(Boolean);
      const body = group.rows.map(({ student, booking }) => {
        const key = `acc:${student.section_id}:${student.id}`;
        const tags = [booking.rescheduled ? '<span class="chip" data-tone="info">Rescheduled</span>' : booking.revision_count > 1 ? '<span class="chip">Updated</span>' : "",
          student.accommodations?.items?.length ? `<span class="dr-acc-count" title="${escape(student.accommodations.items.map(accommodationLabel).join("; "))}">${plural(student.accommodations.items.length, "accommodation")}</span>` : ""].filter(Boolean).join("");
        return `<tr><td data-label="Student">${nameMarkup(student)}<small>${escape(student.uin)}</small></td>
          <td data-label="Course & section"><a href="${escape(student.course_url)}">${escape(student.code)} · ${escape(student.section)}</a><small>${escape(termLabel(student))}</small></td>
          <td data-label="Date"><time datetime="${escape(booking.date)}">${escape(wallDate(booking.date))}</time></td>
          <td data-label="Time (DR center)">${escape(scheduleTime(booking))}<small>${escape(booking.length_minutes)} minutes</small></td>
          ${campus ? `<td data-label="Campus">${escape(booking.campus)}</td>` : ""}
          <td data-label="" class="dr-row-actions">${tags}<button type="button" class="text-button" data-booking-details="${escape(booking.id)}" data-student="${escape(key)}" aria-label="Details: ${escape(student.name)} · ${escape(booking.exam_type)}">Details</button></td></tr>`;
      }).join("");
      return `<section class="dr-group" data-group="${escape(group.key)}" aria-labelledby="dr-group-${escape(group.key.replace(/[^a-z0-9-]/gi, "-"))}">
        <header class="dr-group-head"><div class="dr-group-text"><h3 id="dr-group-${escape(group.key.replace(/[^a-z0-9-]/gi, "-"))}">${escape(group.title)}</h3>${group.names.length ? `<span class="dr-group-alias">DR center: ${group.names.map((name) => `“${escape(name)}”`).join(", ")}</span>` : ""}
          <p class="dr-group-meta">${meta.map((item) => escape(item)).join(" · ")}</p><p class="dr-group-links">${groupLinks(group)}</p></div>
          <button type="button" class="button dr-print" data-print-group="${escape(group.key)}" aria-label="Print DR list for ${escape(group.title)}">${icon("print")}Print DR list</button></header>
        <div class="roster-table-scroll dr-table-scroll" tabindex="0" role="region" aria-label="${escape(group.title)} DR bookings; scroll to see all columns"><table class="roster-table dr-table"><thead><tr><th scope="col">Student</th><th scope="col">Course &amp; section</th><th scope="col">Date</th><th scope="col">Time (DR center)</th>${campus ? '<th scope="col">Campus</th>' : ""}<th scope="col"><span class="sr-only">Details</span></th></tr></thead><tbody>${body}</tbody></table></div>
      </section>`;
    }).join("");
  }

  function find(studentKey, bookingId) {
    const student = data?.students.find((item) => `acc:${item.section_id}:${item.id}` === studentKey);
    return { student, booking: student?.dr_exams?.find((item) => item.id === bookingId) };
  }

  async function details(bookingId, studentKey) {
    const { student, booking } = find(studentKey, bookingId);
    if (!student || !booking) return;
    const current = captureDialogIntent(document.getElementById("dialog"));
    try {
      const history = await api(`/api/accommodations/exams/${encodeURIComponent(bookingId)}/history`);
      if (!current()) return;
      const revisions = history.revisions || [];
      const title = booking.assignment?.title || booking.exam_type;
      const linked = booking.assignment
        ? `<p>Linked to <strong>${escape(booking.assignment.title)}</strong>${same(booking.assignment.title, booking.exam_type) ? "" : ` · the DR center calls it “${escape(booking.exam_type)}”`}</p>`
        : `<p class="field-hint">Not linked to an assignment yet.</p>`;
      const versions = revisions.map((row, index) => {
        const previous = revisions[index + 1] || null;
        const changes = scheduleChanges(row, previous);
        return `<section class="dr-details-version"><h3>${index === 0 ? "Current booking" : "Previous booking"}</h3><p>${schedulePartsMarkup(row, previous, escape)}</p>
          ${changes.length ? `<ul class="dr-details-changes">${changes.map((line) => `<li>${escape(line)}</li>`).join("")}</ul>` : ""}
          <p class="supporting-copy">Requested on ${escape(row.requested_on ? wallDate(row.requested_on, { year: true }) : "—")} · Imported ${escape(wallDate(localToday(new Date(row.imported_at)), { year: true }))}</p><a href="#inbox/job/${escape(row.job_id)}" data-history-import>View import record</a></section>`;
      }).join("");
      const record = student.accommodations;
      showDialog(`${dialogHeader(escape(student.name), escape(`${student.code} · Section ${student.section} · ${termLabel(student)} · ${title}`))}
        <div class="dialog-body dr-details">${linked}${versions}
          <section class="dr-details-version"><h3>Accommodations</h3>${record?.items?.length ? `<ul class="accommodation-tags">${record.items.map((item) => `<li class="chip" data-tone="info">${escape(accommodationLabel(item))}</li>`).join("")}</ul>` : '<p class="field-hint">None on record.</p>'}</section>
          <p><a class="link-arrow" href="${escape(student.course_url)}" data-history-import>Open ${escape(student.code)} · Section ${escape(student.section)}</a></p></div>
        <footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer>`);
      document.querySelectorAll("[data-history-import]").forEach((link) => { link.onclick = () => document.querySelector("#dialog").close(); });
    } catch (error) { toast(error.message, true); }
  }

  /* ---- Print an exam's DR list: an ephemeral view with its own @page, removed after printing ------------------------------ */
  function clearPrint() {
    printNode?.remove();
    printNode = null;
    document.body.classList.remove("print-dr-list");
    if (printSheet) {
      document.adoptedStyleSheets = document.adoptedStyleSheets.filter((sheet) => sheet !== printSheet);
      printSheet = null;
    }
  }

  function printGroup(key) {
    // The whole group, whatever the student search shows: a proctor list must never silently leave students out.
    // The term, course and exam filters only choose groups; the date choice is printed with the list.
    const students = filterStudents(data.students, { term, course, search: "" });
    const group = groupBookings(bookingRows(students, { exam, when }), when).find((item) => item.key === key);
    if (!group) return;
    const dates = when === "all" ? "" : ` · ${when} bookings only`;
    clearPrint();
    const rows = group.rows.map(({ student, booking }) => `<tr><td>${escape(student.name)}</td><td>${escape(student.uin)}</td><td>${escape(student.section)}</td><td>${escape(wallDate(booking.date))}</td><td>${escape(scheduleTime(booking))}</td><td>${escape(booking.length_minutes)} min</td><td>${escape(booking.campus || "")}</td><td>${escape((student.accommodations?.items || []).map(accommodationLabel).join("; ") || "—")}</td></tr>`).join("");
    printNode = document.createElement("section");
    printNode.className = "dr-print-list";
    printNode.setAttribute("aria-hidden", "true");
    printNode.innerHTML = `<header><h2>${escape(group.title)} · DR center</h2><p>${escape(group.code)} · ${escape(group.term)}${group.names.length ? ` · DR center: ${group.names.map((name) => `“${escape(name)}”`).join(", ")}` : ""}</p><p>${plural(group.rows.length, "student")}${escape(dates)} · times are the DR center’s · printed ${escape(wallDate(localToday(), { year: true }))}</p></header>
      <table><thead><tr><th>Name</th><th>UIN</th><th>Section</th><th>Date</th><th>Time (DR center)</th><th>Length</th><th>Campus</th><th>Accommodations</th></tr></thead><tbody>${rows}</tbody></table>`;
    document.body.append(printNode);
    document.body.classList.add("print-dr-list");
    if (typeof CSSStyleSheet === "function" && "adoptedStyleSheets" in document) {
      try {
        printSheet = new CSSStyleSheet();
        printSheet.replaceSync("@page { size: letter portrait; margin: 12mm; }");
        document.adoptedStyleSheets = [...document.adoptedStyleSheets, printSheet];
      } catch { printSheet = null; }
    }
    window.addEventListener("afterprint", clearPrint, { once: true });
    window.print();
  }

  function clearFilters() {
    course = ""; exam = ""; search = ""; type = ""; term = "current";
    draw();
  }

  function bind(host) {
    if (bound) return;
    bound = true;
    document.addEventListener("accommodations-imported", () => { if (!root()?.hidden) void render(true); });
    window.addEventListener("hashchange", () => { if (printNode && !location.hash.startsWith("#accommodations")) clearPrint(); });
    document.addEventListener("visibilitychange", pollNotices);
    host.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      if (target.closest("[data-accommodation-refresh], [data-accommodation-retry]")) { void render(true); return; }
      if (target.closest("[data-aim-review]")) { reviewAim(); return; }
      if (target.closest("[data-aim-dismiss]")) { aim = null; drawAim(); return; }
      if (target.closest("[data-aim-import]")) { importFromAim(); return; }
      if (quizControl(target)) return;
      // AIM FORM CAPTURE: records the structure of AIM's form for the prescheduling driver; it imports and writes nothing.
      if (target.closest("[data-aim-capture]")) { void openAimCapture({ api, showDialog, dialogHeader, escape }); return; }
      const whenButton = target.closest("[data-when]");
      if (whenButton) { when = whenButton.dataset.when; draw(); return; }
      if (target.closest("[data-clear-filters]")) { clearFilters(); return; }
      if (target.closest("[data-acc-more]")) {
        moreTypes = !moreTypes;
        const head = host.querySelector("[data-acc-head]");
        draw();
        /** @type {HTMLElement | null} */ (head?.querySelector("[data-acc-more]"))?.focus();
        return;
      }
      const record = target.closest("[data-student-record]");
      if (record) { openRecord(record.dataset.studentRecord); return; }
      const detailsButton = target.closest("[data-booking-details]");
      if (detailsButton) { void details(detailsButton.dataset.bookingDetails, detailsButton.dataset.student); return; }
      const next = target.closest("[data-dr-next]");
      if (next) {
        // The student's bookings on the DR tab, found by name in memory (never a name or UIN in the address).
        const student = data?.students.find((item) => `acc:${item.section_id}:${item.id}` === next.dataset.drNext);
        if (student) { search = student.name; exam = ""; when = "upcoming"; }
        location.hash = routeHash({ tab: "exams", when, course, exam });
        return;
      }
      const print = target.closest("[data-print-group]");
      if (print) printGroup(print.dataset.printGroup);
    });
    host.addEventListener("change", (event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.dataset.drQuizTaker) {  // ACCOMMODATIONS "DR QUIZ TAKERS" (the DR quizzes tab's controls are the other writes)
        return void saveTaker(api, target.dataset.drQuizTaker, target.checked).catch((error) => toast(error.message, true)).finally(() => render(true));
      }
      if (!(target instanceof HTMLSelectElement)) return;
      if (target.id === "accommodation-term") term = target.value;
      else if (target.id === "accommodation-course") course = target.value;
      else if (target.id === "accommodation-exam") exam = target.value;
      else return;
      draw();
    });
    host.addEventListener("input", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement) || target.id !== "accommodation-search") return;
      search = target.value;
      draw();
    });
    bindFilterChips(host, "acc-type", (value) => { type = value; draw(); });
  }

  return { render };
}
