// TEACHING-WORKFLOWS: the owning service supplies all attention and reopening rules.
import { calloutMarkup, dialogSession, emptyMarkup, errorMessage, loadingMarkup, localToday, plural, registerShortcut, runFormAction, wallDate, whenVisible, setBusy, captureDialogIntent } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { studentPicker } from "./student-picker.js";
import { allowDrive, channelLine, authorText, linkedNote, linksDrive } from "./communications.js";
import { whenText } from "./mail.js";
import { viewLink } from "./file-viewer.js";
import { checkActionMail, submitActionReview, actionReviewMessage } from "./assistant.js";
import { DEPARTMENT_REQUEST, ensureGoogleSignIn } from "./aim-signin.js";
import { approvingCount, doneMarkup, isRegrade, regradeApprovals, suggestionChange, suggestionClick, suggestionsMarkup, suggestionsReady, suggestionSubmit, whileApproving, withApproving } from "./followup-suggestions.js";
import { chooseQuestionOption, learnedNotice } from "./assistant-workspace.js";
import { driveApply, sheetMarkup } from "./grading-regrades.js";
import { responseMarkup } from "./grading-certification-students.js";
import { renderMath } from "./grading-math.js";
import { mountCropZoom } from "./grading-crops.js";
import { CAMPUS_ZONE, addDays, campusToday, clockMinutes, displaySpan } from "./calendar-feed.js";
import { openCommitments, deskTasks, deskTitle, nextDeskAction, deskPlanMarkup, commitmentsMarkup } from './followup-desk.js';
import { openWatch } from './followup-watches.js';

const BASE = "/api/student-support";
// TEACHING-WORKFLOWS / IDENTITY: local selection follows the exact conversation through its first tracking save.
export const supportKey = item => `${item.enrollment_id}:${item.source_key || item.id}`;
export function selectedSupport(shown, key) { return shown.find(({item}) => supportKey(item) === key) || shown[0] || null; }
/**
 * A COORDINATOR'S FOLLOW-UP (TEACHING-WORKFLOWS; requirement, 2026-11-14: the coordinator's emails come to Follow-up like a
 * student's): whether a listed follow-up is with the course coordinator. The server names them where it names the student
 * (`item.student`, with `coordinator` true, their name, address, course and term). Pure. @param {any} item
 */
export const withCoordinator = item => Boolean(item?.student?.coordinator);
/** Whom the page names in a sentence: a student's first name, the coordinator's name as the course card holds it. Pure. @param {any} item */
export const shortName = item => withCoordinator(item) ? String(item.student.name || "").trim() : firstName(item?.student?.name);
/** "the student" or "the coordinator". Pure. @param {any} item */
export const someone = item => withCoordinator(item) ? "the coordinator" : "the student";
/** A follow-up's status in the page's words; a coordinator's waits on the coordinator. Pure. @param {any} item */
export const stateLabel = item => withCoordinator(item) && item.state === "waiting" ? "Waiting on coordinator" : SUPPORT_STATES[item.state] || item.state;
/** Where a follow-up's kept messages are read: the student's record, or the term's coordinator's. Pure. @param {any} item */
export const recordPath = item => withCoordinator(item) ? `/api/communications/coordinators/${encodeURIComponent(item.term_id)}`
  : `/api/communications/students/${encodeURIComponent(item.enrollment_id)}`;
// COMMUNICATIONS: show the exact observed student message, including a Canvas email merged into its captured copy. A coordinator's
// own message is kept as written by someone who is no student (`other`), linked to them as its author.
export function observedMessage(item, entries) {
  const id = item.observed?.[1] || item.message_id, role = withCoordinator(item) ? "other" : "student";
  return (entries || []).find(entry => id && (entry.message_id === id || entry.email_copy === id)
    && entry.author_role === role && (entry.roles || []).includes("author")) || null;
}
// WAITING ON: the instructor's own message that came after the student's latest one (the service's `answered`), or null. WRITTEN AT
// ONCE: an email written from the follow-up that the record has not captured yet is shown from Mail's own send (`answered_mail`).
export function answeredMessage(item, entries) {
  const id = item.answered?.[1];
  if (item.answered && !id && item.answered_mail) {
    return {author_role: "instructor", at: item.answered[0], channel: "gmail", channel_label: "Email",
      subject: String(item.answered_mail.subject || ""), text: String(item.answered_mail.text || "")};
  }
  return (entries || []).find(entry => id && (entry.message_id === id || entry.email_copy === id) && entry.author_role === "instructor") || null;
}
/**
 * WRITE IN THE THREAD (TEACHING-WORKFLOWS SAME MATTER, requirement, 2026-10-07: Write to student… replies inside the student's
 * Gmail thread): the thread and "Re:" subject of the student's latest message here when it is an email (not a Canvas notice),
 * else null (a new email, as before). Pure. @param {any} item @param {any[]} [entries]
 * @returns {{replyThreadId: string, subject: string} | null}
 */
export function replyTarget(item, entries) {
  const message = observedMessage(item, entries);
  const thread = message?.channel === "gmail" && !message.context?.canvas_notice ? String(message.context?.thread_id || "") : "";
  if (!/^[0-9a-f]{1,32}$/.test(thread)) return null;
  const subject = String(message.subject || "").trim();
  return {replyThreadId: thread, subject: !subject ? "" : /^re:/i.test(subject) ? subject : `Re: ${subject}`};
}
/** SAME MATTER: the student's other follow-ups with a conversation, which `item` can be joined with. Pure. @param {any} item @param {any[]} items */
export function joinChoices(item, items) {
  if (!item?.source_key || item.kind !== "followup" || withCoordinator(item)) return [];  // joins are a student's conversations alone
  return (items || []).filter(other => other !== item && other.kind === "followup" && other.source_key
    && other.enrollment_id === item.enrollment_id && supportKey(other) !== supportKey(item) && !String(other.source_key).startsWith("gradescope_regrade:"));
}
/** A student's first name from the roster's "Last, First Middle". Pure. @param {string} [name] */
export function firstName(name) {
  const text = String(name || "").trim(), given = text.includes(",") ? text.slice(text.indexOf(",") + 1).trim() : text;
  return given.split(/\s+/)[0] || "";
}

/**
 * WAITING ON (requirement, 2026-10-04): whom a follow-up waits on and for what, from its status, its recorded next step and the instructor's latest message.
 * Null when nothing waits (resolved, FYI, appointments). `on` is the queue group of whoever holds the next step, whatever the
 * label reads (requirement, 2026-10-11). Pure.
 * @param {any} item @param {number} [now]
 * @returns {{who: string, what: string, since: string, on: "you" | "students" | "later"} | null}
 */
export function waitingOn(item, now = Date.now()) {
  if (item?.kind !== "followup" || ["resolved", "fyi"].includes(item.state)) return null;
  const first = shortName(item) || someone(item), next = String(item.next || "").trim();
  const since = [item.answered?.[0] ? `You wrote ${whenText(item.answered[0], now)}` : "",
    item.date && item.state !== "snoozed" ? `Follow up ${wallDate(item.date)}` : item.check_back ? `Check back ${wallDate(item.check_back)}` : ""].filter(Boolean).join(" · ");
  // A newer student message reopened it: what it waited on before stays in view.
  if (item.new_reply) return {who: "Waiting on you", what: `Reply to ${first}’s new message`, since: next ? `Before it: ${next}` : "", on: "you"};
  const tasks = openCommitments(item);
  if (tasks.length) {
    const due = c => c.waiting_on === "you" || c.check_back && c.check_back <= campusToday();
    const task = tasks.find(due) || tasks.find(c => c.waiting_on === "student") || tasks[0];
    return {who: due(task) ? "Waiting on you" : task.waiting_on === "student" ? `Waiting on ${first}` : "Check back later",
      what: task.title, since: task.check_back ? `Check back ${wallDate(task.check_back)}` : "", on: due(task) ? "you" : task.waiting_on === "student" ? "students" : "later"};
  }
  // CHECK BACK: back on its day, it waits on the instructor to resolve it, wait longer or write.
  if (item.state === "waiting") return checkBack(item).due ? {who: `Still waiting on ${first}?`, what: next || "A reply to your last message", since, on: "you"}
    : {who: `Waiting on ${first}`, what: next || "A reply to your last message", since, on: "students"};
  if (item.state === "snoozed") return {who: `Follow up ${item.date ? wallDate(item.date) : "later"}`, what: next || item.title, since, on: "later"};
  if (item.state === "todo") {
    const owed = item.suggestions?.obligation;
    return {who: owed?.work_owed ? (owed.reply_owed ? "Work and reply owed" : "Work still owed") : owed?.reply_owed ? "Reply still owed" : "Waiting on you", what: next || item.title, since, on: "you"};
  }
  if (item.state === "replied") return {who: "Waiting on you", what: "The suggested steps still open", since, on: "you"};
  return {who: "Waiting on you", what: next || (item.answered ? "You wrote last. Say what it waits on with Edit follow-up…" : `A reply to ${first}`), since, on: "you"};
}

// The office hours under the conversation list: the calendar's office hours from campus today through the next six days, read
// when the page opens, after a save and at most every five minutes while it polls; the first four still to come are listed.
const HOURS_DAYS = 7, HOURS_EVERY = 300000, HOURS_SHOWN = 4;

/**
 * The office hours on the calendar (GET /api/calendar events): timed events titled "Office hours", or whose title ends in it after
 * a separator ("MATH 140 · Office hours", the instructor's own events; the usability audit, 2026-10-04, found the exact-title match
 * listed none of them), whichever calendar holds them (the appointments Faculty projects there under that neutral title, the
 * instructor's own events, their Google Calendar's), once per date and time, in order. "Office hours prep" is not office hours.
 * Campus wall-clock minutes; no student, purpose or note is in these events. Pure.
 * @param {any[]} events
 */
export function officeHours(events) {
  const rows = new Map();
  for (const event of events || []) {
    const name = String(event?.title || "").trim().split(/\s+[·|:–—-]\s+/).pop()?.trim().toLowerCase();
    if (event?.all_day || name !== "office hours") continue;
    const from = clockMinutes(event.time);
    if (from === null || typeof event.date !== "string") continue;
    const to = Math.max(from, clockMinutes(event.end_time) ?? from), key = `${event.date} ${from} ${to}`;
    const location = String(event.location || "").trim(), same = rows.get(key);
    // The same hours on two calendars (an appointment and its Google copy) are one row, with whichever location was given.
    if (same) { same.location ||= location; continue; }
    rows.set(key, {date: event.date, from, to, time: String(event.time), end_time: String(event.end_time || ""), location});
  }
  return [...rows.values()].sort((a, b) => a.date.localeCompare(b.date) || a.from - b.from || a.to - b.to);
}

/** MEETING LENGTH (requirement, 2026-10-06): the End a moved
 * Start brings along, keeping the length the form had (30 minutes when it had none), never past 23:59. An unfinished Start
 * leaves End as it is. Pure. @param {string} start @param {string} before @param {string} end */
export function followingEnd(start, before, end) {
  const from = clockMinutes(start), was = clockMinutes(before), until = clockMinutes(end);
  if (from === null) return end;
  const length = was !== null && until !== null && until > was ? until - was : MEETING_MINUTES;
  const to = Math.min(from + length, 24 * 60 - 1);
  return `${String(Math.floor(to / 60)).padStart(2, "0")}:${String(to % 60).padStart(2, "0")}`;
}
export const MEETING_MINUTES = 30;

/** Minutes after midnight on the campus clock, whatever the browser's time zone. @param {Date} [now] */
export function campusMinutes(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {timeZone: CAMPUS_ZONE, hourCycle: "h23", hour: "2-digit", minute: "2-digit"})
    .formatToParts(now).map(part => [part.type, part.value]));
  return (Number(parts.hour) % 24) * 60 + Number(parts.minute);
}

/**
 * Student follow-up's office hours, under the conversation list: those still to come this week from the calendar and the Office
 * hours page. (the Office hours column and Leonardo's Lady with an
 * Ermine above it gave way to the email and its suggested actions side by side; DESIGN-LANGUAGE §9).
 * @param {{rows: ReturnType<typeof officeHours> | null, today: string, failed?: boolean} | null} hours @param {(text: string) => string} e
 * @param {Date} [now]
 */
export function officeHoursMarkup(hours, e, now = new Date()) {
  const today = hours?.today || campusToday(now), minutes = campusMinutes(now);
  const coming = (hours?.rows || []).filter(row => row.date > today || (row.date === today && (row.to > minutes || row.from >= minutes))).slice(0, HOURS_SHOWN);
  const day = (/** @type {string} */ date) => date === today ? "Today" : date === addDays(today, 1) ? "Tomorrow" : wallDate(date);
  const list = !hours ? '<p class="field-hint" role="status">Reading the calendar…</p>'
    : !hours.rows ? '<p class="field-hint">The calendar could not be read.</p>'
    : coming.length ? `<ul class="support-hours-list" role="list">${coming.map(row => `<li><strong>${e(day(row.date))}</strong><span>${e(displaySpan(row.time, row.end_time))}${row.location ? ` · ${e(row.location)}` : ""}</span></li>`).join("")}</ul>`
    : '<p class="field-hint">No office hours on the calendar this week.</p>';
  return `<section class="support-hours" aria-labelledby="support-hours-title" data-patch-key="hours"><h3 id="support-hours-title" class="support-hours-title">Office hours</h3>${list}<a class="link-arrow" href="#office-hours">All office hours</a></section>`;
}

export const SUPPORT_STATES = { needs_reply: "Needs my reply", todo: "To do", waiting: "Waiting on student", snoozed: "Follow up later", resolved: "Resolved",
  fyi: "FYI · no reply suggested", replied: "Replied · suggestions open", scheduled: "Scheduled", attended: "Attended", cancelled: "Cancelled", no_show: "No-show" };
export function supportMatches(item, filter, query) {
  // DONE FOR YOU: a conversation whose status the Assistant changed stays in Active and Needs me while cards still wait for you.
  const waiting = suggestionsReady(item);
  if (filter === "attention" && !item.attention && !waiting) return false;
  if (filter === "wellbeing" && !item.wellbeing) return false;
  if (filter === "suggestions" && !suggestionsReady(item)) return false;
  // DONE FOR YOU: the conversations where the Assistant recorded something by itself (closed thank-you replies included).
  if (filter === "done" && !(item.suggestions?.done || []).some(d => ["done", "scheduled"].includes(d.state))) return false;
  // REGRADES "APPROVE ALL": Gradescope regrade requests have their own tab, open ones as Active shows them, and leave Active.
  if (filter === "regrades" && !isRegrade(item)) return false;
  if (filter === "active" && isRegrade(item)) return false;
  if (["active", "regrades"].includes(filter) && ["fyi", "resolved", "attended", "cancelled", "no_show"].includes(item.state) && !waiting) return false;
  if (!["all", "active", "regrades", "attention", "wellbeing", "suggestions", "done"].includes(filter) && item.state !== filter) return false;
  const haystack = `${item.title} ${item.student?.name} ${item.student?.code} ${item.student?.term_label} ${item.student?.sections?.join(" ")} ${deskTasks(item).map(t => t.title).join(" ")}`.toLowerCase();
  return String(query).toLowerCase().split(/\s+/).every(word => haystack.includes(word));
}

/**
 * FOLLOW-UP REVIEW ORDER (the instructor's plan to need less of their time, 2026-10-04): a possible wellbeing concern first, then
 * conversations whose reply waits for Send, then other waiting cards, then the Assistant's questions, then the rest; the
 * server's order within each. Pure. @param {{item: any, index: number}[]} shown
 */
export function reviewOrder(shown) {
  const waiting = (/** @type {any} */ item) => (item.suggestions?.actions || []).filter((/** @type {any} */ a) => a.pending);
  const rank = (/** @type {any} */ item) => item.wellbeing ? 0 : waiting(item).some((/** @type {any} */ a) => a.endpoint === "gmail_mail.send") ? 1
    : waiting(item).length ? 2 : item.suggestions?.state === "needs_input" ? 3 : 4;
  return shown.map((entry, n) => ({entry, n, rank: rank(entry.item)})).sort((a, b) => a.rank - b.rank || a.n - b.n).map(({entry}) => entry);
}

/**
 * FOLLOW-UP QUEUE GROUPS (DESIGN-LANGUAGE §1.6, Ledger, 2026-10-04: what waits on you is strong, what waits on others is muted):
 * "you" when the instructor holds the next step (steps or a question waiting, a wellbeing flag, or the follow-up waits on them:
 * a To do's work or reply still owed included), "students" when it waits on the student, "later" for a follow-up date, "none"
 * when nothing waits. Pure. @param {any} item
 */
export function waitGroup(item) {
  if (item?.wellbeing || suggestionsReady(item)) return "you";
  if (openCommitments(item).some(c => c.waiting_on === "you" || c.check_back && c.check_back <= campusToday())) return "you";
  const w = waitingOn(item);
  if (!w) return "none";
  return w.on;
}
/**
 * WHAT YOU DID (requirement, 2026-10-13): the queue
 * group. What waits on the instructor (`waitGroup` "you") is "review" when something there is new to decide (steps waiting or
 * being approved, a question, a wellbeing flag, a student message they have not answered) and "todo" when they have dealt with
 * the conversation and still owe work (a To do, their open tasks, a due check-back). Pure. @param {any} item
 */
export function queueGroup(item) {
  const group = waitGroup(item);
  if (group !== "you") return group;
  if (item?.wellbeing || suggestionsReady(item) || approvingCount(item?.suggestions)) return "review";
  return ["needs_reply", "replied"].includes(item?.state) && !item?.answered ? "review" : "todo";
}
export const QUEUE_GROUPS = /** @type {const} */ ([["review", "To review"], ["todo", "Your to-dos"], ["students", "Waits on students"], ["later", "Follow up later"], ["none", "Nothing waits"]]);
/** The queue in its groups (QUEUE_GROUPS order), review order kept within each. Pure. @param {{item: any, index: number}[]} shown */
export function groupedOrder(shown) {
  const order = QUEUE_GROUPS.map(([key]) => key);
  return shown.map((entry, n) => ({entry, n, group: order.indexOf(queueGroup(entry.item))}))
    .sort((a, b) => a.group - b.group || a.n - b.n).map(({entry}) => entry);
}

/** An approval and the reply it sent are one act: the reply's Gmail time lands seconds after the click. */
const ACTED_TOGETHER = 600000;
/**
 * WHAT YOU DID: what the instructor last did on a follow-up, for its row under "Your to-dos": the steps they approved
 * (`suggestions.approved`, the suggestions read), their own reply (`answered`) or their own save (`updated_by`), whichever is
 * newest, the approval when its reply followed within ten minutes. "" when they did none. Pure. @param {any} item @param {number} [now]
 */
export function actedLine(item, now = Date.now()) {
  const approved = item?.suggestions?.approved, at = (/** @type {unknown} */ value) => Date.parse(String(value || "")) || 0;
  const step = approved?.count ? {at: at(approved.at), text: `You approved ${approved.count === 1 ? "1 step" : `${approved.count} steps`} · ${whenText(approved.at, now)}`} : null;
  const acts = [step,
    item?.answered?.[0] ? {at: at(item.answered[0]), text: `You replied ${whenText(item.answered[0], now)}`} : null,
    item?.updated_by === "workspace" ? {at: at(item.updated_at), text: `You updated it ${whenText(item.updated_at, now)}`} : null,
  ].filter((/** @type {any} */ act) => act?.at).sort((/** @type {any} */ a, /** @type {any} */ b) => b.at - a.at);
  if (!acts.length) return "";
  return (step?.at && acts[0].at - step.at <= ACTED_TOGETHER ? step : acts[0]).text;
}
/**
 * WAITING ON, on a queue row: the line under the title without what the title already says. A step that is the row's title
 * leaves only whom it waits on ("Follow up Thu, Oct 9", "Work still owed"); the bare "Waiting on you" then says nothing its
 * group does not. Pure. @param {any} item @param {string} title @param {ReturnType<typeof waitingOn>} [w]
 */
export function queueNext(item, title, w = waitingOn(item)) {
  if (!w) return null;
  if (w.what.trim() !== String(title || "").trim()) return {who: w.who, what: w.what};
  return w.who === "Waiting on you" ? null : {who: w.who, what: ""};
}
/**
 * CHECK BACK (requirement, 2026-10-22, of follow-ups still Waiting on student long past the days they named): the day a Waiting on
 * student follow-up comes back (its own date, else the check-back day the server read from its next step, else a week after it was
 * set) and whether it is back. A newer student message reopens it instead. Pure. @param {any} item @param {string} [today]
 */
export function checkBack(item, today = campusToday()) {
  const day = item?.state === "waiting" ? String(item.date || item.check_back || "") : "";
  return {day, due: Boolean(day && day <= today && !item.new_reply)};
}

/** CHECK BACK: a tracked follow-up back on its day with nothing waiting for review and no separate tasks settles from its decision
 * box: Resolved, or Wait a week (Write to student… stays in the row under the conversation). Pure. @param {any} item */
export function checkBackSettles(item) {
  return Boolean(item?.id && item.saved_state === "waiting" && checkBack(item).due && !suggestionsReady(item) && !approvingCount(item.suggestions) && !openCommitments(item).length);
}

/**
 * ONE CLICK ON A TO DO (requirement, 2026-10-13, of a To do naming work their approval had already done; asked, they chose
 * Done and Now waits on the student): a tracked To do with nothing waiting for review and no separate tasks settles from its
 * decision box. One split into tasks keeps Mark completed on each (SEPARATE COMMITMENTS). Pure. @param {any} item
 */
export function todoSettles(item) {
  return Boolean(item?.id && item.saved_state === "todo" && item.state === "todo" && !suggestionsReady(item) && !approvingCount(item.suggestions) && !openCommitments(item).length);
}

/**
 * SEPARATE COMMITMENTS: the tasks a task may wait on under Complete these first. Never itself, and never a task that already
 * waits on it, directly or through others, since the server refuses that cycle (Juliet's Exam 2 makeup, 2026-10-24: "Send
 * the details" waited on "Arrange the makeup", and ticking it on Arrange's completion failed). Pure. @param {any[]} tasks @param {string} ident
 */
export function prerequisiteChoices(tasks, ident) {
  const waiting = new Set([ident]);
  for (let grew = true; grew;) {
    grew = false;
    for (const c of tasks) if (!waiting.has(c.id) && (c.depends_on || []).some(d => waiting.has(d))) { waiting.add(c.id); grew = true; }
  }
  return tasks.filter(c => !waiting.has(c.id));
}

/** The next conversation after `key` (wrapping) that still waits for the instructor, or null. Pure. @param {{item: any}[]} shown @param {string} key */
export function nextReady(shown, key) {
  const at = shown.findIndex(({item}) => supportKey(item) === key);
  return [...shown.slice(at + 1), ...shown.slice(0, Math.max(at, 0))].find(({item}) => suggestionsReady(item)) || null;
}

// JEV HOST FAILURES (2026-10-09): one line while triage waits out its provider's outage, and the failed analyses with one Retry all
// failed (an item's own Retry stays in its Related records & actions).
export function triageStatusMarkup(view, e) {
  const failed = (view.items || []).filter(item => ["failed", "uncertain"].includes(item.triage?.status)).length;
  const paused = view.jev?.triage_paused || "";
  if (!paused && !failed) return "";
  return `<p class="field-hint support-triage-status" role="status" data-patch-key="triage-status">${e(paused)}${paused && failed ? " " : ""}${failed
    ? `${e(failed)} conversation ${failed === 1 ? "analysis" : "analyses"} failed. <button class="text-button" type="button" data-support-retry-failed>Retry all failed</button>` : ""}</p>`;
}

export function triageMarkup(triage, index, e) {
  if (!triage) return "";
  if (triage.status !== "complete") return `<p class="field-hint">${e(triage.error || "Conversation analysis is in progress.")}</p>${triage.status !== "running" ? `<button class="text-button" type="button" data-support-retry="${index}">${triage.status === "pending" ? "Analyze now" : "Retry conversation analysis"}</button>` : ""}`;
  const requests = new Set(triage.requests || []);
  const labels = {absence: "Absence notice", makeup: "Makeup request", office_hours: "Office hours", grading: "Grading question", materials: "Course materials"};
  // JEV URGENCY: Soon / Time-sensitive only when Jev is reasonably sure; otherwise the list order alone reflects it.
  const urgency = triage.urgency?.label ? `<span class="chip" data-tone="${triage.urgency.label === "Time-sensitive" ? "warning" : "neutral"}">${e(triage.urgency.label)}</span> ` : "";
  return `<p class="field-hint">${e(triage.label || "Needs a look")}${triage.context_incomplete ? " · Some earlier context was omitted; review the conversation." : ""}</p>
    <p>${urgency}${Object.keys(labels).filter(key => requests.has(key)).map(key => `<span class="chip" data-tone="neutral">${e(labels[key])}</span>`).join(" ")}</p>
    <div class="support-actions">${requests.has("absence") ? triage.mail ? `<button class="text-button" type="button" data-support-absence="${index}">Draft absence record</button>` : '<a class="link-arrow" href="#absences">Open absences</a>' : ""}
    ${requests.has("makeup") ? '<a class="link-arrow" href="#absences?view=makeups">Open makeups</a>' : ""}
    ${requests.has("office_hours") ? `<button class="text-button" type="button" data-support-meeting="${index}">Schedule office hours</button>` : ""}
    ${requests.has("grading") ? `<button class="text-button" type="button" data-support-record="${index}">Open grading record</button>` : ""}
    ${requests.has("materials") ? '<a class="link-arrow" href="#courses">Open course materials</a>' : ""}</div>`;
}

/** ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP (requirement, 2026-11-03): what each quiz makeup's booking card did or will do, with the Makeups view it is listed on. Pure. @param {any} item @param {(s: any) => string} e */
export function makeupCardsMarkup(item, e) {
  const lines = item.suggestions?.makeup_cards || [];
  return lines.length ? `<ul class="support-joined" role="list" data-patch-key="makeup-cards">${lines.map((/** @type {any} */ line) =>
    `<li><span>${e(line.text)}</span><a class="link-arrow" href="#absences?view=makeups">Open makeups</a></li>`).join("")}</ul>` : "";
}

/** JEV WELLBEING: advisory wording, never a diagnosis; cleared only by the instructor's own Mark reviewed. */
export function wellbeingMarkup(item, index) {
  if (!item.wellbeing) return "";
  return `<div class="callout" data-tone="warning" role="note"><p><strong>Possible wellbeing concern</strong></p>
    <p>Read the student’s message, then mark this concern reviewed. Jev’s flag is advisory.</p>
    <p><button class="text-button" type="button" data-support-reviewed="${index}">Mark reviewed</button></p></div>`;
}

export function createStudentSupport({ api, escape: e, toast, showDialog, dialogHeader, header, openStudent, communications, notes, writeStudent, draftAbsence, changed, editMail }) {
  const root = document.getElementById("student-support-section");
  let view = null, path = "", pending = null, generation = 0, filter = "active", query = "";
  let pollTimer = null, readError = "", refreshAt = 0;
  let hours = null, hoursAt = 0, hoursPending = null, hoursGeneration = 0;
  let selected = "", sourceRead = null, sourceGeneration = 0, deskView = "action";
  // FRONTEND: keep a bounded set of detached review articles, so switching conversations preserves drafts and disclosures.
  // Selection and these unsaved forms are browser memory only; all records remain enrollment-owned by their services.
  const reviews = new Map();
  // RESOLVED WHEN OPENED (TEACHING-WORKFLOWS): the list read leaves a settled resolved follow-up's drafts and evidence out
  // (`suggestions.brief`). Opening one reads them once; they are kept while the list says nothing about it changed, and at most 20.
  const details = new Map();
  const detailReads = new Set();
  let detailGeneration = 0, detailFailed = /** @type {{key: string, message: string} | null} */ (null);
  const currentPath = () => location.hash.split("?")[0];
  const kind = () => path === "#office-hours" ? "appointment" : "followup";
  const dialog = () => document.getElementById("dialog");
  const personTarget = item => ({ termId: item.term_id, sectionId: item.student?.section_id, enrollmentId: item.enrollment_id });

  function messageMarkup(item, index) {
    if (!item.source_key) return "";
    const message = sourceRead?.key === supportKey(item) ? observedMessage(item, sourceRead.data?.entries) : null;
    // LINKED DRIVE FILES: a file the student linked from Google Drive is one of the message's files once Faculty fetched it; one it
    // could not fetch says why, and offers the one click that lets Faculty read Drive when that is what it waits on.
    const files = (message?.attachments || []).map(file => file.kept === false
      ? `<span>${e(file.name || "Attachment")} · ${e(linkedNote(file) || "not kept")}</span>${file.wait === "permission" ? ' <button class="text-button" type="button" data-support-drive>Allow Faculty to read Google Drive</button>' : ""}`
      : `<a class="text-button" ${viewLink(`/api/communications/messages/${encodeURIComponent(message.message_id)}/attachments/${file.index}`, file.name || "Attachment", e)}>${e(file.name || "Attachment")}</a>`).join(" ");
    // A message kept before Faculty read Drive lists no such file yet (requirement, 2026-10-11): its link alone is enough to say so here and offer the click, while Faculty may not read Drive.
    const drive = view?.capture?.drive;
    const unfetched = drive && !drive.ready && drive.connected && message && linksDrive(message.text) && !(message.attachments || []).some(file => file.linked)
      ? '<p class="field-hint support-message-drive">This message links a file on Google Drive. Faculty has not fetched it: it may not read your Google Drive yet. <button class="text-button" type="button" data-support-drive>Allow Faculty to read Google Drive</button></p>' : "";
    // SEE THEIR WORK (REGRADES "FOLLOW-UP", requirement, 2026-10-21): a regrade request opens the answer's crop and rubric lines in a popup.
    const regrade = String(item.source_key).startsWith("gradescope_regrade:")
      ? `<button class="text-button" type="button" data-support-work="${index}" aria-haspopup="dialog">See their work</button>` : "";
    return `<section class="support-message" data-patch-key="message"><div class="support-message-head"><h4>${withCoordinator(item) ? "Coordinator’s message" : "Student’s message"}</h4>
      <span class="support-message-links">${regrade}<button class="text-button" type="button" data-support-messages="${index}">View conversation</button></span></div>
      ${message ? `<p class="field-hint">${e(authorText(message, item.student?.name || "Student"))} · ${e(whenText(message.at))} · ${e(message.channel_label)}</p>
        <div class="support-message-text" tabindex="0" role="region" aria-label="${withCoordinator(item) ? "Complete message from the coordinator" : "Complete student message"}">${e(message.text)}</div>${files ? `<p class="support-message-files">${files}</p>` : ""}${unfetched}`
        : `<p class="field-hint" role="status">${e(sourceRead?.error || (sourceRead?.loading ? "Reading the captured message…" : "The exact message is not in the captured timeline. Open the conversation to inspect the record."))}</p>
          ${sourceRead?.error ? `<button type="button" class="text-button" data-support-source="${index}">Try again</button>` : ""}`}</section>${answeredMarkup(item)}${conversationHistory(item)}`;
  }

  /** SEE THEIR WORK: the answer a regrade request is about, as Return shows it (the grade, the rubric lines applied first, the
   * crops, which enlarge), read-only; Edit grade or reply on the card changes it. @param {any} item */
  async function openWork(item) {
    const title = item.title || "Their work";
    showDialog(`<div class="support-work-dialog">${dialogHeader(e(title), e(item.student?.name || ""))}<div class="dialog-body" data-support-work-body>${loadingMarkup("Reading their work…")}</div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer></div>`);
    const host = /** @type {HTMLElement} */ (dialog().querySelector("[data-support-work-body]"));
    const alive = dialogSession(host, /** @type {HTMLDialogElement} */ (dialog()));
    let markup;
    try {
      const placed = await api(`/api/grading/regrade-work?key=${encodeURIComponent(item.source_key)}`);
      if (placed.kind === "mc") {
        // REGRADES "BUBBLE SHEETS": the question's row of their scan, what Faculty read and the key, from the request's panel.
        const found = await api(`/api/grading/assignments/${encodeURIComponent(placed.plan_id)}/regrade-requests/${encodeURIComponent(placed.message_id)}`);
        markup = sheetMarkup(found.sheet, { e });
      } else if (!placed.response_id) markup = `<p class="supporting-copy" role="status">${e(placed.reason)}</p>`;
      else {
        const value = await api(`/api/grading/responses/${encodeURIComponent(placed.response_id)}?details=review`);
        markup = `<section class="regrade-work" aria-label="Their work and the grade">${responseMarkup(value, null, { e })}</section>`;
      }
    } catch (cause) {
      markup = `<p class="form-error" role="alert">${e(errorMessage(cause))}</p>`;
    }
    if (!alive()) return;
    host.innerHTML = markup;
    renderMath(host);
    mountCropZoom(host);
  }

  function conversationHistory(item) {
    const entries = sourceRead?.key === supportKey(item) ? sourceRead.data?.entries || [] : [];
    const keys = new Set([item.source_key, ...(item.joins || []).map(j => j.source_key)]);
    const selectedIds = new Set([item.observed?.[1], item.answered?.[1]].filter(Boolean));
    const belongs = entry => selectedIds.has(entry.message_id) || keys.has(`gmail:${entry.context?.thread_id || ""}`)
      || keys.has(`canvas:${entry.context?.conversation_id || ""}`);
    const current = entries.filter(belongs).filter(entry => !selectedIds.has(entry.message_id));
    // SAME MATTER: the follow-up Jev asks about is shown on its question, not again here.
    const matched = matchKeys(item);
    const other = entries.filter(entry => !belongs(entry) && !matched.has(entryKey(entry)));
    const messages = rows => historyMessages(item, rows);
    return `${current.length ? `<details class="disclosure support-history" data-patch-key="thread-history"><summary><span>Earlier in this conversation · ${current.length}</span></summary><div class="disclosure-body">${messages(current)}</div></details>` : ""}
      ${other.length ? `<details class="disclosure support-history" data-patch-key="other-history"><summary><span>Other conversations · background</span></summary><div class="disclosure-body"><p class="field-hint">These are separate matters. This reply continues to answer the selected conversation.</p>${messages(other)}</div></details>` : ""}`;
  }

  /** The captured messages of a conversation history, the latest 30. @param {any} item @param {any[]} rows */
  function historyMessages(item, rows) {
    return rows.slice(-30).map(entry => `<article class="support-history-message" data-patch-key="message-${e(entry.message_id)}">
      <p class="field-hint">${e(authorText(entry, item.student?.name || "Student"))} · ${e(whenText(entry.at))} · ${e(entry.channel_label || "")}</p>
      ${entry.subject ? `<strong>${e(entry.subject)}</strong>` : ""}<div class="support-history-text" tabindex="0" role="region" aria-label="Captured message">${e(entry.text || "")}</div></article>`).join("");
  }

  /** The conversation key a captured message belongs to. @param {any} entry */
  function entryKey(entry) {
    return entry.context?.thread_id ? `gmail:${entry.context.thread_id}` : entry.context?.conversation_id ? `canvas:${entry.context.conversation_id}` : "";
  }

  /** SAME MATTER: the follow-up Jev asks about, from the list, when it is there. @param {any} item */
  function matchTarget(item) {
    const m = item.match;
    return m ? (view?.items || []).find((/** @type {any} */ other) => other.enrollment_id === item.enrollment_id && other.source_key === m.into_key) || null : null;
  }

  /** SAME MATTER: the conversation keys of the follow-up Jev asks about (its own and those joined to it). @param {any} item */
  function matchKeys(item) {
    if (!item.match) return new Set();
    return new Set([item.match.into_key, ...(matchTarget(item)?.joins || []).map((/** @type {any} */ j) => j.source_key)]);
  }

  // WAITING ON: what you last wrote after the student's message (what you asked for or promised), under it.
  function answeredMarkup(item) {
    const reply = item.answered ? answeredMessage(item, sourceRead?.key === supportKey(item) ? sourceRead.data?.entries : []) : null;
    return reply ? `<section class="support-message support-message-own" data-patch-key="answered"><div class="support-message-head"><h4>Your reply</h4></div>
      <p class="field-hint">${e(authorText(reply, ""))} · ${e(whenText(reply.at))} · ${e(reply.channel_label)}</p>
      <div class="support-message-text" tabindex="0" role="region" aria-label="Your latest reply">${e(reply.text)}</div></section>` : "";
  }

  // THE DECISION FIRST (DESIGN-LANGUAGE §1.4, Ledger, 2026-10-04): what the conversation waits on, under its title. When it waits on
  // the instructor it is the orange box with the next action and the plan of the steps that wait. A single step keeps its own Send
  // or Approve beside the draft it sends (an explicit Send is next to what it sends). Write to student… is always in the row under
  // the conversation: the desk's own button only leads to a step or to the conversation, so nothing else offers it.
  function decisionMarkup(item, index, mine) {
    const next = nextDeskAction(item), going = approvingCount(item.suggestions);
    // TO DO THANKS: the student only thanked you after your reply and Jev could not tell whether you still owe them a message.
    const finished = item.suggestions?.finished && item.id ? `<p class="support-finished-ask">${e(shortName(item) || "The student")} only thanked you. Is this follow-up finished?</p>` : "";
    const settles = todoSettles(item), back = !settles && checkBackSettles(item);
    const resolve = finished && !settles ? `<button type="button" class="button" data-support-finished="${index}">Mark resolved</button>` : "";
    // CHECK BACK: what it waited for and what came in for the student since (EVIDENCE CLOSES' records), beside Resolved.
    if (back) Object.assign(next, {title: `Still waiting on ${shortName(item) || someone(item)}?`,
      detail: [item.next || "A reply to your last message", `waiting since ${whenText(item.updated_at)}`].join(" · ")});
    const arrived = back ? item.suggestions?.arrived || [] : [];
    const since = !back ? "" : arrived.length ? `<div class="support-check-back-since" data-patch-key="check-back-since"><p class="field-hint">Came in since then</p><ul>${arrived.map((/** @type {any} */ r) => `<li>${e(r.what)} · ${e(whenText(r.when))}</li>`).join("")}</ul></div>`
      : '<p class="field-hint support-check-back-since" data-patch-key="check-back-since">Nothing from them has come in since then.</p>';
    return `<section class="support-decision" data-waits="${mine ? "you" : "other"}" data-patch-key="decision" aria-label="What this waits on">
      <div class="support-desk-next"><div><strong>${e(going ? `Approving ${going === 1 ? "one step" : `${going} steps`}…` : next.title)}</strong>${going ? '<p class="field-hint">They go in order. Waiting for the result.</p>' : next.detail ? `<p class="field-hint">${e(next.detail)}</p>` : ""}</div>
      ${going ? "" : back ? `<div class="support-desk-settle"><button class="button primary" type="button" data-support-finished="${index}">Resolved</button><button class="button" type="button" data-support-later="${index}">Wait a week</button></div>`
        : settles ? `<div class="support-desk-settle"><button class="button primary" type="button" data-support-finished="${index}">Done</button><button class="button" type="button" data-support-waits="${index}">Now waits on ${e(firstName(item.student?.name) || "the student")}</button></div>`
        : next.target === "resolve" && !suggestionsReady(item) ? `<button class="button primary" type="button" data-support-finished="${index}">Mark resolved</button>` : `<button class="button primary" type="button" data-desk-action="${e(next.target)}">${next.target === "conversation" ? "Read conversation" : next.target === "tasks" ? "Review tasks" : "Review next action"}</button>`}</div>${since}${finished}${resolve}</section>${deskPlanMarkup(item, index, e)}`;
  }

  function review(item, index, position, total) {
    const student = item.student || {}, coordinator = withCoordinator(item);
    const mine = waitGroup(item) === "you";
    return `<article class="support-card support-review" data-patch-key="${e(supportKey(item))}" data-support-item="${index}" data-followup-item="${index}" data-assistant-enrollment="${e(item.enrollment_id)}" data-assistant-source="${e(item.source_key || "")}">
      <header class="support-review-head"><div><p class="field-hint">${e([student.code, student.term_label, coordinator ? "Course coordinator" : student.sections?.length ? `Section ${student.sections.join(", ")}` : ""].filter(Boolean).join(" · "))}</p>
        <h3>${e(deskTitle(item))}</h3>${coordinator ? `<span class="support-review-person">${e(student.name || "Course coordinator")}</span>` : `<button class="text-button" type="button" data-support-record="${index}">${e(student.name || "Student")}</button>`}
        <span class="chip" data-tone="${item.attention ? "warning" : "neutral"}">${e(openCommitments(item).length && !mine ? (waitGroup(item) === "later" ? "Check back later" : coordinator ? "Waiting on coordinator" : "Waiting on student") : stateLabel(item))}</span></div>
        <nav class="support-review-nav" aria-label="Review conversations"><span>${position + 1} of ${total}</span>
          <button type="button" class="button" data-support-move="-1"${position === 0 ? " disabled" : ""} aria-label="Previous conversation">←</button>
          <button type="button" class="button" data-support-move="1"${position === total - 1 ? " disabled" : ""} aria-label="Next conversation">→</button></nav></header>
      ${decisionMarkup(item, index, mine)}
      <div class="support-review-body" data-patch-key="body"><div class="support-review-source" data-patch-key="source">
      ${item.new_reply ? `<p class="field-hint">${coordinator ? "A newer message from the coordinator reopened this follow-up." : "A newer student message reopened this follow-up."}</p>`
        : item.new_reply_answered ? `<p class="field-hint">${e(shortName(item) || "The student")} wrote again and you replied. The status is as you left it.</p>` : ""}
      ${wellbeingMarkup(item, index)}
      ${matchMarkup(item, index)}
      <p class="field-hint support-original-subject">Conversation: ${e(item.title)}</p>${messageMarkup(item, index)}
      ${joinedMarkup(item, index)}
      ${makeupCardsMarkup(item, e)}
      <div class="support-actions support-review-actions"><button type="button" class="text-button" data-support-write="${index}">${coordinator ? "Write to coordinator…" : "Write to student…"}</button>
        <button type="button" class="text-button" data-support-edit="${index}">${item.id ? "Edit follow-up…" : "Track follow-up…"}</button>
        ${joinChoices(item, view?.items).length ? `<button type="button" class="text-button" data-support-join="${index}">Join to another follow-up…</button>` : ""}
        ${item.suggestions?.state === "idle" && item.suggestions.preparable ? '<button type="button" class="text-button" data-followup-prepare>Suggest actions</button>' : ""}</div>
      ${item.details || item.date || item.triage ? `<details class="disclosure support-related" data-patch-key="related"><summary><span>Related records &amp; actions</span></summary><div class="disclosure-body">
        ${item.date ? `<p class="field-hint">Follow-up date: ${e(wallDate(item.date))}</p>` : ""}${item.details ? `<p class="support-words">${e(item.details)}</p>` : ""}${triageMarkup(item.triage, index, e)}</div></details>` : ""}
      </div>${item.suggestions?.brief ? detailMarkup(item) : suggestionsMarkup(item, index, e, {here: "#follow-ups", prefix: "support-", lead: false, desk: true}) || doneMarkup(item.suggestions, index)}</div>
    </article>`;
  }

  /** RESOLVED WHEN OPENED: what stands where the suggestions go until the read of them returns. @param {any} item */
  function detailMarkup(item) {
    const failed = detailFailed?.key === supportKey(item) ? detailFailed.message : "";
    return `<p class="field-hint" role="status" data-patch-key="detail-read">${e(failed ? `The Assistant's work here could not be read: ${failed}` : "Reading the Assistant's work here…")}${failed ? ' <button type="button" class="text-button" data-support-detail>Try again</button>' : ""}</p>`;
  }

  /** RESOLVED WHEN OPENED: the list's items, each brief one replaced by its kept full view while the list says the same of it. A
   * brief one that changed keeps the view until the read of it returns. @param {any[]} items */
  function withDetails(items) {
    return items.map(item => {
      const kept = item.suggestions?.brief ? details.get(supportKey(item)) : null;
      if (!kept) return item;
      const stamp = JSON.stringify(item);
      if (stamp !== kept.stamp) Object.assign(kept, {stamp, brief: item, stale: true});
      return kept.item;
    });
  }

  /** RESOLVED WHEN OPENED: reads the selected follow-up's full view when the list left it out or it has changed. @param {any} item */
  function readDetail(item) {
    const key = supportKey(item), kept = details.get(key);
    if (!item.suggestions?.brief && !kept?.stale || detailReads.has(key) || detailFailed?.key === key) return;
    const brief = kept?.brief || item, stamp = JSON.stringify(brief), mine = detailGeneration;
    detailReads.add(key);
    void api(`${BASE}?kind=followup&suggestions=1&enrollment_id=${encodeURIComponent(item.enrollment_id)}`).then(data => {
      if (mine !== detailGeneration) return;
      const found = data.items.find((/** @type {any} */ other) => supportKey(other) === key);
      if (!found) throw new Error("This follow-up changed. Try the list again.");
      details.delete(key);
      details.set(key, {item: found, brief, stamp, stale: false});
      while (details.size > 20) details.delete(details.keys().next().value);
      view = {...view, items: withApproving(view.items.map((/** @type {any} */ other) => supportKey(other) === key ? found : other))};
    }).catch(cause => { if (mine === detailGeneration) detailFailed = {key, message: errorMessage(cause)}; })
      .finally(() => { detailReads.delete(key); if (mine === detailGeneration && currentPath() === "#follow-ups") draw(); });
  }

  // SAME MATTER (requirement, 2026-10-07): Jev was unsure whether this new conversation continues one of the student's
  // follow-ups; one click joins it there, or keeps it separate (Jev does not ask again).
  // SEE WHAT IT MEANS (requirement, 2026-11-08):
  // that follow-up's own messages open under the question, without leaving this one.
  function matchMarkup(item, index) {
    const m = item.match;
    return m ? `<div class="callout support-match" data-tone="info" role="note" data-patch-key="match"><p><strong>Same matter as “${e(m.title)}”?</strong></p>
      <p>Jev thinks this may continue that follow-up (${e(SUPPORT_STATES[m.state] || m.state)}). Joined, it is read there and this one leaves the list.</p>
      ${matchPreview(item)}
      <p class="support-actions"><button type="button" class="button" data-support-join-match="${index}">Join</button>
        <button type="button" class="text-button" data-support-separate="${index}" data-key="${e(item.source_key)}">Keep separate</button></p></div>` : "";
  }

  /** SAME MATTER: the follow-up Jev asks about, folded: what it waited on and its captured messages. @param {any} item */
  function matchPreview(item) {
    const target = matchTarget(item), keys = matchKeys(item);
    const entries = sourceRead?.key === supportKey(item) ? sourceRead.data?.entries : null;
    const rows = (entries || []).filter((/** @type {any} */ entry) => keys.has(entryKey(entry)));
    const facts = [target?.next ? `Waited on: ${target.next}` : "", target?.date ? `Follow-up date: ${wallDate(target.date)}` : "",
      rows.length ? `Last message ${whenText(rows.reduce((a, b) => (b.at > a.at ? b : a)).at)}` : ""].filter(Boolean).join(" · ");
    const body = rows.length ? historyMessages(item, rows)
      : `<p class="field-hint" role="status">${e(sourceRead?.error || (!entries ? "Reading its messages…" : "Its messages are not in the captured record."))}</p>`;
    return `<details class="disclosure support-match-preview" data-patch-key="match-preview"><summary><span>Read “${e(item.match.title)}”${rows.length ? ` · ${rows.length} ${rows.length === 1 ? "message" : "messages"}` : ""}</span></summary>
      <div class="disclosure-body">${facts ? `<p class="field-hint">${e(facts)}</p>` : ""}${target?.details ? `<p class="support-words">${e(target.details)}</p>` : ""}${body}</div></details>`;
  }

  // SAME MATTER: the conversations you joined to this follow-up, each with Keep separate (Jev's joins are under Done for you).
  function joinedMarkup(item, index) {
    const mine = (item.joins || []).filter(j => j.by !== "jev");
    return mine.length ? `<ul class="support-joined" role="list" data-patch-key="joined">${mine.map(j => `<li><span>Also in this follow-up: “${e(j.title)}” (${e(whenText(j.started))})</span>
      <button type="button" class="text-button" data-support-separate="${index}" data-key="${e(j.source_key)}">Keep separate</button></li>`).join("")}</ul>` : "";
  }

  // SAME MATTER: the instructor's own join of this conversation with another of the student's follow-ups (the later one is read as
  // part of the earlier one).
  async function openJoin(item) {
    const choices = joinChoices(item, view?.items);
    if (!choices.length) throw new Error("This student has no other follow-up to join.");
    showDialog(`<form id="support-join">${dialogHeader("Join to another follow-up", e(item.student?.name || ""))}<div class="dialog-body">
      <p class="field-hint">Both conversations become one follow-up: the one that started later is read as part of the earlier one. Keep separate undoes it.</p>
      <fieldset class="support-join-choices"><legend>Same matter as</legend>${choices.map((other, n) => `<label><input type="radio" name="with" value="${n}"${n === 0 ? " checked" : ""}> ${e(other.title)} · ${e(SUPPORT_STATES[other.state] || other.state)}</label>`).join("")}</fieldset>
      <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Join</button></footer></form>`);
    const form = document.getElementById("support-join"), alive = dialogSession(form, dialog());
    let requestId = "", sent = "";
    form.onsubmit = event => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const other = choices[Number(new FormData(form).get("with"))];
        if (!other) throw new Error("Choose the follow-up it belongs with.");
        const content = JSON.stringify([item.source_key, other.source_key]);
        if (content !== sent) { requestId = crypto.randomUUID(); sent = content; }
        await api(`${BASE}/followups/join`, "POST", {enrollment_id: item.enrollment_id, source_key: item.source_key, with_key: other.source_key, request_id: requestId});
        toast("Joined. They are one follow-up now.");
        if (alive()) dialog().close();
        void changed?.();
        await render(true);
      });
    };
  }

  function queueRow(item, index) {
    const s = item.suggestions, waiting = (s?.actions || []).filter(action => action.pending).length, going = approvingCount(s);
    const done = (s?.done || []).filter(d => d.state === "done").length;
    // WAITING ON: whom it waits on, and for what, in place of the bare status.
    const w = waitingOn(item), group = queueGroup(item), title = deskTitle(item), next = queueNext(item, title, w);
    // WHAT YOU DID: under Your to-dos, what the instructor already did here.
    const acted = group === "todo" ? actedLine(item) : "";
    const status = [s?.state === "needs_input" ? "Needs your input" : waiting ? `${waiting} suggested ${waiting === 1 ? "step" : "steps"} to review`
      : going ? `Approving ${going} ${going === 1 ? "step" : "steps"}…` : w ? "" : stateLabel(item), done ? `${done} done for you` : ""].filter(Boolean).join(" · ");
    const flag = item.wellbeing ? "Possible wellbeing concern" : item.triage?.urgency?.label || "";
    return `<button type="button" class="support-queue-row" data-waits="${waitGroup(item)}" data-queue="${group}" data-patch-key="${e(supportKey(item))}" data-support-select="${index}" aria-pressed="${supportKey(item) === selected}" aria-controls="support-review">
      <span class="support-queue-person">${e(item.student?.name || "Student")}</span><span class="support-queue-title" title="${e(title)}">${e(title)}</span>
      <span class="support-queue-meta">${e([item.student?.code, withCoordinator(item) ? "Coordinator" : item.student?.sections?.length ? `§ ${item.student.sections.join(", ")}` : ""].filter(Boolean).join(" · "))}</span>
      ${next ? `<span class="support-queue-next"><strong>${e(next.who)}${next.what ? ":" : ""}</strong> ${e(next.what)}</span>` : ""}${acted ? `<span class="support-queue-acted">${e(acted)}</span>` : ""}${status ? `<span class="support-queue-status">${e(status)}</span>` : ""}${flag ? `<span class="support-queue-flag">${e(flag)}</span>` : ""}</button>`;
  }

  // WRITTEN FROM A FOLLOW-UP (requirement, 2026-10-07): mail
  // written from a conversation here is recorded with it, so it shows as Your reply once captured, wherever Gmail threads it.
  // The follow-up's status stays as it is.
  async function recordWritten(item, requestId) {
    try {
      await api(`${BASE}/followups/written`, "POST", {enrollment_id: item.enrollment_id, source_key: item.source_key, send_request_id: requestId});
    } catch (cause) {
      toast(`Sent, but this follow-up could not record it: ${errorMessage(cause)}`, true);
    }
    if (currentPath() === "#follow-ups") void render(true);
  }

  function readMessage(item, force = false) {
    if (!item?.source_key) { sourceRead = null; sourceGeneration++; return; }
    // A newer reply of yours (WRITTEN FROM A FOLLOW-UP: possibly in another Gmail thread) reads the record again to show it.
    const key = supportKey(item), stamp = JSON.stringify([key, item.observed, item.suggestions?.fingerprint, item.triage?.fingerprint, item.answered]);
    if (!force && sourceRead?.stamp === stamp) return;
    const mine = ++sourceGeneration;
    sourceRead = {key, stamp, loading: true};
    void api(recordPath(item)).then(data => {
      if (mine === sourceGeneration) sourceRead = {key, stamp, data};
    }).catch(cause => { if (mine === sourceGeneration) sourceRead = {key, stamp, error: `The message could not be read: ${errorMessage(cause)}`}; })
      .finally(() => { if (mine === sourceGeneration && currentPath() === "#follow-ups") draw(); });
  }

  // The Office hours list only; the Follow-up page draws queueRow() and review().
  function card(item, index) {
    const student = item.student || {}, dates = [item.date ? wallDate(item.date) : "", item.time ? `${item.time}–${item.end_time}` : ""].filter(Boolean).join(" · ");
    return `<article class="support-card" data-patch-key="${e(item.id || `${item.enrollment_id}:${item.source_key}`)}" data-support-item="${index}" data-followup-item="${index}"><div class="support-card-head">
      <button class="text-button" type="button" data-support-record="${index}">${e(student.name || "Student")}</button>
      <span class="chip" data-tone="${item.attention ? "warning" : "neutral"}">${e(SUPPORT_STATES[item.state])}</span></div>
      <p class="field-hint">${e([student.code, student.term_label, student.sections?.length ? `Section ${student.sections.join(", ")}` : ""].filter(Boolean).join(" · "))}</p>
      <h3>${e(item.title)}</h3>${dates ? `<p>${e(dates)}${item.location ? ` · ${e(item.location)}` : ""}</p>` : ""}
      ${item.details ? `<details><summary>Details</summary><p class="support-words">${e(item.details)}</p></details>` : ""}
      <div class="support-actions"><button type="button" class="button" data-support-edit="${index}">${item.id ? "Update" : "Track follow-up"}</button>
      <button type="button" class="text-button" data-support-messages="${index}">Communications</button>
      <button type="button" class="text-button" data-support-write="${index}">Write to student…</button>
      <button type="button" class="text-button" data-support-note="${index}">Add private note…</button><button type="button" class="text-button" data-support-follow="${index}">Add follow-up…</button></div></article>`;
  }

  // The calendar's office hours under the conversation list: a light, date-bounded read of the calendar feed; a failed read keeps
  // the last good one.
  function loadHours(force = false) {
    const start = campusToday();
    // A read that failed is tried again after a minute, not at every poll.
    if (!force && (hoursPending || (hours && hours.today === start && Date.now() - hoursAt < (hours.rows ? HOURS_EVERY : 60000)))) return hoursPending;
    const mine = ++hoursGeneration;
    hoursPending = (async () => {
      try {
        const data = await api(`/api/calendar?${new URLSearchParams({start, end: addDays(start, HOURS_DAYS - 1)})}`);
        if (mine === hoursGeneration) hours = {rows: officeHours(data?.events), today: data?.today || start};
      } catch {
        if (mine === hoursGeneration && !hours?.rows) hours = {rows: null, today: start, failed: true};
      } finally {
        if (mine === hoursGeneration) {
          hoursAt = Date.now(); hoursPending = null;
          if (currentPath() === "#follow-ups") draw();
        }
      }
    })();
    return hoursPending;
  }

  // SIBLING VIEWS ARE TABS (DESIGN-LANGUAGE §1.8, Ledger 2026-10-04): the common filters are framed tabs with their counts; the
  // Status select keeps the rarer ones (a state, Suggestions ready, possible wellbeing concerns). One filter at a time.
  // REGRADES "APPROVE ALL": Regrades (Gradescope regrade requests) is a Follow-up tab of its own, beside Active.
  const TABS = [["active", "Active"], ["regrades", "Regrades"], ["attention", "Needs me"], ["done", "Done for you"], ["all", "All"]];
  function filterTabs() {
    const tabs = kind() === "followup" ? TABS : TABS.filter(([key]) => !["done", "regrades"].includes(key));
    return `<div class="filter-tabs support-filter-tabs" role="group" aria-label="Show">${tabs.map(([key, label]) => {
      const count = view.items.filter(item => supportMatches(item, key, query)).length;
      return `<button type="button" data-support-tab="${key}" aria-pressed="${filter === key}">${label} <b${count ? "" : " data-zero"}>${count}</b></button>`;
    }).join("")}</div>`;
  }

  function tabsMarkup() {
    const active = kind() === "appointment" ? "office-hours" : deskView;
    return `<nav class="support-desk-views filter-tabs" aria-label="Student follow-through">${[["action", "Action desk"], ["tasks", "Tasks"], ["office-hours", "Office hours"]]
      .map(([key, label]) => `<button type="button" data-desk-view="${key}" aria-pressed="${active === key}">${label}</button>`).join("")}</nav>`;
  }

  function draw() {
    if (currentPath() !== path) return;
    if (!view) {
      // FRONTEND: a new visit shows its own tabs immediately, with no previous visit's records or actions.
      header?.set({route: path.slice(1), title: kind() === "appointment" ? "Office hours" : "Student follow-up",
        subtitle: kind() === "appointment" ? "Plan meetings and record how they went." : "Keep student conversations and tasks moving.",
        action: null, attention: false});
      patchChildren(root, `<div class="support-page"><div class="support-navigation">${tabsMarkup()}</div>${readError
        ? calloutMarkup({tone: "danger", title: "The list could not be read", body: readError}) + '<button type="button" class="button" data-support-reload>Try again</button>'
        : loadingMarkup(kind() === "appointment" ? "Reading office hours…" : "Reading follow-ups…", 3)}</div>`);
      return;
    }
    const shown = visible();
    const chosen = kind() === "followup" ? selectedSupport(shown, selected) : null;
    selected = chosen ? supportKey(chosen.item) : "";
    if (kind() === "followup") readMessage(chosen?.item);
    if (chosen) readDetail(chosen.item);
    const oldReview = root.querySelector(".support-detail > .support-card");
    if (oldReview && oldReview.dataset.patchKey !== selected) {
      reviews.delete(oldReview.dataset.patchKey); reviews.set(oldReview.dataset.patchKey, oldReview); oldReview.remove();
      while (reviews.size > 20) reviews.delete(reviews.keys().next().value);
    }
    const kept = reviews.get(selected);
    if (kept && root.querySelector(".support-detail")) { root.querySelector(".support-detail").appendChild(kept); reviews.delete(selected); }
    const count = view.items.filter(item => item.attention).length;
    const ready = kind() === "followup" ? view.items.filter(suggestionsReady).length : 0;
    const readyText = ready ? `${ready} with suggestions ready` : "";
    header?.set({ route: path.slice(1), title: kind() === "appointment" ? "Office hours" : "Student follow-up",
      subtitle: count ? `${count} ${count === 1 ? "item needs" : "items need"} your attention${readyText ? ` · ${readyText}` : ""}.` : readyText ? `${readyText[0].toUpperCase()}${readyText.slice(1)}.` : kind() === "appointment" ? "Plan meetings and record how they went." : "Keep student conversations and tasks moving.",
      action: null, attention: Boolean(count || ready) });
    // ASSISTANT-FOLLOWUP-SUGGESTIONS: the workspace's switch for automatic preparation (Prepare now and your own requests still run).
    const settings = kind() === "followup" ? view.suggestion_settings : null;
    const automatic = settings ? `<details class="disclosure support-automation" data-patch-key="automatic"><summary><span>Automatic suggestions: ${settings.paused ? "paused" : settings.unavailable ? "unavailable" : "on"}</span></summary><div class="disclosure-body"><p class="field-hint support-automatic">${settings.unavailable ? "" : `Automatic suggestions: ${settings.paused ? "paused" : "on"}
      <button type="button" class="text-button" data-followup-pause="${settings.paused ? "off" : "on"}">${settings.paused ? "Resume" : "Pause"}</button> · `}
      Prepare now and your own requests still run while paused.</p></div></details>` : "";
    // FYI (2026-10-09 review: conversations set aside by Jev left no count): how many are filed there, one click from the filter.
    const filed = kind() === "followup" ? view.items.filter(item => item.state === "fyi").length : 0;
    const capture = kind() === "followup" ? `<details class="support-capture" data-patch-key="capture"><summary>Based on captured communications</summary>
      <p>New student messages appear after capture. A quiet list does not establish that every channel is up to date.</p>
      <ul>${Object.entries(view.capture?.channels || {}).map(([name, channel]) => `<li>${e(name)}: ${e(channelLine(channel))}</li>`).join("")}${view.capture?.drive && !view.capture.drive.ready ? `<li>Google Drive: ${e(view.capture.drive.setup)}${view.capture.drive.connected ? ' <button class="text-button" type="button" data-support-drive>Allow Faculty to read Google Drive</button>' : ""}</li>` : ""}</ul>
      ${view.jev ? `<p>${e(view.jev.detail)}</p><p>Thanks and information-only messages Jev reads confidently appear under FYI. Uncertain conversations stay active. Analysis never sends a message or changes your saved follow-ups.</p>${filed ? `<p><button type="button" class="text-button support-fyi-link" data-support-fyi>Filed as FYI · ${filed}</button></p>` : ""}` : ""}
      <a class="link-arrow" href="#mail">Open Mail</a></details>` : "";
    patchChildren(root, `<div class="support-page"><div class="support-navigation">${tabsMarkup()}
      ${kind() === "followup" ? `<button type="button" class="button" data-support-new>Add follow-up</button>${settings ? `<button type="button" class="text-button" data-support-policies>Term policies…</button>` : ""}` : ""}</div>
      <div class="support-toolbar">${filterTabs()}<label class="field">Find<input type="search" data-page-search data-support-query value="${e(query)}" placeholder="Student, course or task"></label><label class="field">Status<select data-support-filter><option value="" ${TABS.some(([key]) => key === filter) ? "selected" : ""}>Any status</option>${kind() === "followup" ? `<option value="suggestions" ${filter === "suggestions" ? "selected" : ""}>Suggestions ready</option><option value="wellbeing" ${filter === "wellbeing" ? "selected" : ""}>Possible wellbeing concerns</option>` : ""}${(kind() === "appointment" ? ["scheduled", "attended", "cancelled", "no_show"] : ["needs_reply", "todo", "waiting", "snoozed", "resolved", "fyi"]).map(state => `<option value="${state}" ${filter === state ? "selected" : ""}>${SUPPORT_STATES[state]}</option>`).join("")}</select></label>${kind() === "appointment" ? `<button type="button" class="button primary" data-support-new>Schedule office hours</button>` : ""}</div>
      ${readError ? `<p class="field-hint" role="status" data-patch-key="read-error">${e(readError)}</p>` : ""}
      ${kind() === "followup" ? triageStatusMarkup(view, e) : ""}${regradeAllMarkup(shown)}
      ${kind() === "followup" ? `${deskView === "tasks" ? commitmentsMarkup(shown, e) : ""}<div class="support-workspace" data-desk-view="${deskView}" data-patch-key="workspace"><div class="support-side" data-patch-key="side"><nav class="support-queue" aria-label="Follow-up queue">
        ${QUEUE_GROUPS.map(([group, title]) => { const rows = shown.filter(({item}) => queueGroup(item) === group); return rows.length
          ? `<h3 class="support-queue-group" data-waits="${group === "review" || group === "todo" ? "you" : group}" data-queue="${group}" data-patch-key="group-${group}">${title} <span>${rows.length}</span></h3>${rows.map(({item, index}) => queueRow(item, index)).join("")}` : ""; }).join("")}</nav>${officeHoursMarkup(hours, e)}</div><div class="support-detail" id="support-review" data-patch-key="detail">
        ${chosen ? review(chosen.item, chosen.index, shown.indexOf(chosen), shown.length) : emptyMarkup({ title: "No matching follow-ups", body: "Choose another filter, or add a follow-up." })}</div></div>`
        : `<div class="support-list" data-patch-key="items">${shown.length ? shown.map(({item, index}) => card(item, index)).join("") : emptyMarkup({ title: "No matching items", body: "Choose another filter, or add a follow-up or meeting." })}</div>`}
      <footer class="support-page-details" data-patch-key="page-details">${automatic}${capture}</footer></div>`);
    checkActionMail(chosen?.item.suggestions?.brief ? [] : [...chosen?.item.suggestions?.actions || [], ...chosen?.item.suggestions?.chat || []], api, () => { if (currentPath() === path) draw(); });
    // ACTION DESK: the same dependency is checked by the server before sending. Editing remains available.
    if (chosen) {
      for (const task of deskTasks(chosen.item).filter(t => t.kind === "action" && t.blocked)) {
        const card = [...root.querySelectorAll('[data-action-id]')].find(card => card.getAttribute('data-action-id') === task.id);
        const send = card?.querySelector('[data-assistant-approve]');
        if (send) { send.disabled = true; send.title = task.reason; }
      }
      if (openCommitments(chosen.item).some(c => c.blocks_reply)) root.querySelectorAll('[data-followup-approve-all]').forEach(button => {
        button.disabled = true;button.title = "Complete the reply prerequisites first.";
      });
    }
  }

  // REGRADES "APPROVE ALL": the Regrades tab's one button for every waiting decision it shows (two or more), above its queue, and
  // while it runs, how far it got (it runs in this page).
  let regradeRun = /** @type {{done: number, total: number} | null} */ (null);
  function regradeAllMarkup(shown) {
    if (kind() !== "followup" || filter !== "regrades") return "";
    const count = regradeRun ? regradeRun.total : regradeApprovals(shown.map(({item}) => item)).length;
    if (!regradeRun && count < 2) return "";
    return `<div class="support-regrade-all" data-patch-key="regrade-all"><p>${regradeRun ? "<strong>Approving the regrade decisions.</strong> Keep Faculty open until it finishes."
      : `<strong>${count} regrade decisions wait for you.</strong> Keeps go first.`}</p>${regradeRun
      ? `<button type="button" class="button primary" disabled>Approving ${regradeRun.done + 1} of ${regradeRun.total}…</button>`
      : `<button type="button" class="button primary" data-regrade-approve-all>Approve all ${count}</button>`}</div>`;
  }

  // REGRADES "APPROVE ALL": one confirmation, then each decision in turn exactly as its own Approve (keeps first).
  function confirmRegrades() {
    const cards = regradeApprovals(visible().map(({item}) => item));
    if (cards.length < 2 || regradeRun) return;
    const keeps = cards.filter(c => c.action.body?.decision === "keep").length, changes = cards.length - keeps;
    showDialog(`<form id="support-regrades-all">${dialogHeader("Approve all regrade decisions", plural(cards.length, "decision"))}<div class="dialog-body">
      <p>${e([keeps ? `${plural(keeps, "keep")} the grade` : "", changes ? `${plural(changes, "change")} it` : ""].filter(Boolean).join(", "))}. Each reply is posted on Gradescope${changes ? ", and a changed grade goes to Gradescope, the gradebook and Canvas" : ""}.</p>
      <p class="field-hint">A decision that changed since it was made is shown to you again, not sent. Approving stops at the first that fails.</p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Approve all ${cards.length}</button></footer></form>`);
    const form = document.getElementById("support-regrades-all"), alive = dialogSession(form, dialog());
    form.onsubmit = event => {
      event.preventDefault();
      if (alive()) dialog().close();
      void approveRegrades(cards.map(c => c.action.id)).catch(cause => toast(errorMessage(cause), true));
    };
  }

  // Each chosen card approved in turn through its own route (its preview again, then the ordinary write). A card that changed since
  // it was made, or is no longer waiting (409/404), was not submitted: it is shown again and the rest go on. A LOST ANSWER (2026-10-29:
  // the second of 16 went through on the server at 04:04:16Z while the browser's request was cancelled at 04:04:04Z and the run
  // stopped with "Can't reach your server") is settled by reading the card's own record; any other failure stops, since what it did is
  // not known. One read of the page per card would compete with the approvals for the server's threads, so the pages are told
  // once, at the end. The approved decisions' Applies are then driven together while Faculty is open.
  async function approveRegrades(ids) {
    if (regradeRun) return;
    const cards = regradeApprovals(view?.items || []).filter(c => ids.includes(c.action.id));
    if (!cards.length) { toast("Nothing is left to approve.", true); return; }
    regradeRun = {done: 0, total: cards.length};
    const applies = [], again = [];
    let approved = 0, stopped = "", unapplied = 0;
    try {
      await whileApproving(cards.map(c => c.action.id), async () => {
        for (const [n, {action}] of cards.entries()) {
          regradeRun.done = n;
          if (currentPath() === "#follow-ups") draw();
          const outcome = await approveOne(action);
          if (outcome.again) { again.push(action.id); continue; }
          if (outcome.stopped) { stopped = outcome.stopped; break; }
          approved++;
          const apply = outcome.result?.result?.apply;
          if (apply && !apply.finished) applies.push({path: outcome.result.action?.path || "", run: apply});
          else if (outcome.lost && action.body?.decision !== "keep") unapplied++;
        }
      }, followupContext.redraw);
    } finally {
      regradeRun = null;
      window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
    }
    if (unapplied) toast(`${unapplied} changed ${unapplied === 1 ? "grade waits" : "grades wait"} for Continue on Return › Regrade requests.`, true);
    const left = cards.length - approved - again.length;
    toast([`Approved ${approved} of ${cards.length}.`, again.length ? `${again.length} changed since ${again.length === 1 ? "it was" : "they were"} made: look again.` : "",
      stopped ? `Stopped: ${stopped} ${left} still wait.` : ""].filter(Boolean).join(" "), Boolean(stopped || again.length));
    void changed?.();
    void render(true);
    if (!applies.length) return;
    const ends = await Promise.all(applies.map(async ({path, run}) => {
      const aid = (/\/api\/grading\/assignments\/([^/]+)\//.exec(path) || [])[1];
      try { return aid ? await driveApply(api, decodeURIComponent(aid), run) : run; } catch { return null; }
    }));
    const unfinished = ends.filter(end => !end?.finished).length;
    toast(unfinished ? `${unfinished} of ${ends.length} changed grades did not finish applying: see Return › Regrade requests.` : `Applied ${ends.length} changed ${ends.length === 1 ? "grade" : "grades"}: Gradescope, the gradebook and Canvas.`, Boolean(unfinished));
    void changed?.();
    void render(true);
  }

  // One card of Approve all: {result} when it went through, {again} when it was not submitted and waits for a look, {stopped} else.
  // A lost answer (no status: the request failed or was cancelled on the way) is read back from the card's record; a card still
  // waiting as it was is sent once more (the server never approves a card twice), one being carried out is waited for.
  async function approveOne(action, retried = false) {
    try {
      const result = await api(`/api/assistant/actions/${action.id}/approve`, "POST", {seen: action.seen}, {}, 180000);
      return result.stale ? {again: true} : result.action?.status === "succeeded" ? {result} : {stopped: actionReviewMessage(result)};
    } catch (cause) {
      if (cause?.status === 409 || cause?.status === 404) return {again: true};
      if (cause?.status) return {stopped: errorMessage(cause)};
      const card = await cardRecord(action.id);
      if (card?.status === "succeeded") return {result: {action: card}, lost: true};
      if (card?.pending && card.seen === action.seen && !retried) return approveOne(action, true);
      if (card?.pending) return {again: true};
      return {stopped: card ? `the card reads ${card.status}. Look at it before approving more.` : errorMessage(cause)};
    }
  }
  /** The card's own record (`GET /api/assistant/actions/<id>`) once no approval is being carried out (the restart check's count:
   * the server writes "approved" only after the card's preview, so a card may still read as waiting while it is approved) and it
   * is not approved with no result yet; three minutes at most, else null. */
  async function cardRecord(id) {
    const until = Date.now() + 180000;
    while (Date.now() < until) {
      try {
        const check = await api("/api/assistant/restart-check");
        if (!check?.busy?.approvals) {
          const card = (await api(`/api/assistant/actions/${encodeURIComponent(id)}`))?.action;
          if (card && card.status !== "approved") return card;
        }
      } catch { /* read again */ }
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
    return null;
  }

  // REGRADES "FOLLOW-UP": an approved regrade decision's Apply runs while Faculty is open (nothing runs by itself after a restart);
  // the toast says where it ended, and Return › Regrade requests shows every step.
  async function followApply(path, run) {
    const aid = (/\/api\/grading\/assignments\/([^/]+)\//.exec(path) || [])[1];
    toast(run.finished ? "Applied." : "Approved. Faculty writes the grade on Gradescope, then posts the reply.");
    if (aid) {
      try {
        const view = await driveApply(api, decodeURIComponent(aid), run);
        const stopped = (view?.steps || []).find(step => step.status === "stopped");
        toast(stopped ? `Stopped at ${stopped.title}: ${stopped.detail || "see Return › Regrade requests."}`
          : view?.finished ? "Applied: the grade, the reply, the gradebook and Canvas." : "Still working: see Return › Regrade requests.", Boolean(stopped));
      } catch (cause) { toast(errorMessage(cause), true); }
    }
    void changed?.();
    void render(true);
  }

  async function render(force = false) {
    const next = currentPath();
    if (!["#follow-ups", "#office-hours"].includes(next)) return;
    if (next !== path) {
      path = next; view = null; generation++; query = ""; pending = null; readError = ""; selected = ""; sourceRead = null; sourceGeneration++; reviews.clear();
      refreshAt = 0; hours = null; hoursAt = 0; hoursPending = null; hoursGeneration++;
      details.clear(); detailGeneration++; detailFailed = null;
      clearTimeout(pollTimer);
      // Home's wellbeing card opens #follow-ups?filter=wellbeing; the Assistant's Student triage row opens ?filter=fyi; ?filter=regrades
      // opens Regrades.
      const asked = new URLSearchParams(location.hash.split("?")[1] || "").get("filter");
      filter = next === "#follow-ups" && ["wellbeing", "fyi", "regrades"].includes(asked) ? asked : "active";
    }
    if (!force && pending) return pending;
    // FRONTEND / PERFORMANCE: the shell's second render shares this visit's read; polls and returns still read afresh.
    if (!force && Date.now() < refreshAt) { draw(); return; }
    clearTimeout(pollTimer);
    if (force) details.forEach(kept => { kept.stale = true; });  // the instructor's own change may show only in the full view
    if (!view) { readError = ""; draw(); }
    if (kind() === "followup") void loadHours(force);
    // A save starts a fresh read immediately; an earlier request cannot repaint pre-save records afterwards.
    const mine = ++generation;
    pending = (async () => {
      try { const data = await api(`${BASE}?kind=${kind()}${kind() === "followup" ? "&suggestions=1&brief=1" : ""}`); if (generation === mine && currentPath() === path) { detailFailed = null; view = {...data, items: withApproving(withDetails(data.items))}; readError = ""; draw(); sayLearned(view.items); } }
      catch (cause) { if (generation === mine && currentPath() === path) { readError = `${view ? "Refresh could not finish: " : ""}${errorMessage(cause)}`; draw(); } }
      finally { if (generation === mine) { pending = null; schedule(); } }
    })();
    return pending;
  }

  // SAID WHERE IT HAPPENED (ASSISTANT-MEMORY "LEARNED FROM AN EDIT"): a memory kept from a correction of a card is said once, as a
  // toast with Undo, on the page's next read (it lands a little after the approval). Which were said is kept in this browser, a
  // convenience only: the follow-up's own "Remembered for later drafts" line stays either way. At most three a read, a day old at most.
  const TOLD = "faculty:followup-learned-told";
  function sayLearned(items) {
    let told = [];
    try { told = JSON.parse(localStorage.getItem(TOLD) || "[]"); } catch { /* said again rather than not at all */ }
    const fresh = items.flatMap(item => item.suggestions?.learned || [])
      .filter(learned => learned.memory?.standing && !told.includes(learned.case) && Date.now() - Date.parse(learned.at) < 86400000).slice(0, 3);
    if (!fresh.length) return;
    for (const learned of fresh) toast(...learnedNotice(learned, api, toast, () => void render(true)));
    try { localStorage.setItem(TOLD, JSON.stringify([...told, ...fresh.map(learned => learned.case)].slice(-200))); } catch { /* a convenience only */ }
  }

  function schedule() {
    clearTimeout(pollTimer);
    if (currentPath() !== path) return;
    // `remembering`: a correction is being considered, so what it kept is said within seconds rather than at the next half minute.
    const working = view?.items.some(item => ["queued", "running", "analyzing"].includes(item.suggestions?.state)) || view?.suggestion_settings?.remembering;
    const delay = working ? 4000 : 30000, mine = generation;
    refreshAt = Date.now() + delay;
    pollTimer = setTimeout(() => whenVisible(() => { if (generation === mine && currentPath() === path) void render(); }), delay);
  }

  window.addEventListener("hashchange", () => {
    if (path && currentPath() !== path) {
      // Also invalidate a visit left for another sidebar page, even if its pending response arrives after returning.
      path = ""; pending = null; generation++; sourceGeneration++; hoursGeneration++;
      clearTimeout(pollTimer);
    }
  });
  const refreshVisible = () => {
    if (!document.hidden && ["#follow-ups", "#office-hours"].includes(currentPath())) { refreshAt = 0; void render(); }
  };
  window.addEventListener("focus", refreshVisible);
  document.addEventListener("visibilitychange", refreshVisible);

  /** The conversations the filter and search leave, on Follow-up in their groups and review order (FOLLOW-UP REVIEW ORDER). */
  function visible() {
    const shown = view.items.map((item, index) => ({item, index})).filter(({item}) => supportMatches(item, filter, query));
    return kind() === "followup" ? groupedOrder(reviewOrder(shown)) : shown;
  }
  /** Moves the selection `delta` conversations along the queue (the review's ← → and Alt+J / Alt+K). @param {number} delta */
  function move(delta) {
    const shown = visible(), next = shown[shown.findIndex(({item}) => supportKey(item) === selected) + delta]?.item;
    if (next) { selected = supportKey(next); draw(); }
  }
  // AUTO-ADVANCE: after the instructor's own Send, Approve, Decline or Approve all leaves the selected conversation with nothing
  // waiting, the next conversation that waits for them is selected (never on a poll, never while a draft is open for editing).
  let advanceFrom = "";
  function advance() {
    const from = advanceFrom;
    advanceFrom = "";
    if (!from || from !== selected || !view || kind() !== "followup" || root.querySelector("[data-followup-edit-form]")) return;
    const shown = visible(), current = shown.find(({item}) => supportKey(item) === from);
    if (current && (suggestionsReady(current.item) || openCommitments(current.item).some(c => c.waiting_on === "you"))) return;
    const next = nextReady(shown, from);
    if (!next) return;
    selected = supportKey(next.item);
    draw();
    document.getElementById("support-review")?.scrollIntoView({block: "start"});
  }
  const followupContext = { api, toast, showDialog, dialogHeader, item: index => view?.items[index], editMail, refresh: async () => { await render(true); advance(); },
    redraw: () => { if (view) { view = {...view, items: withApproving(view.items)}; draw(); } } };
  // FOLLOW-UP SHORTCUTS (central registry, ui.js): move between conversations, approve all, edit the first waiting draft.
  const onFollowup = () => currentPath() === "#follow-ups" && Boolean(view) && !document.querySelector("#dialog[open]");
  registerShortcut({key: "Alt+J", label: "Next conversation", run: () => move(1), when: onFollowup, allowInInput: true});
  registerShortcut({key: "Alt+K", label: "Previous conversation", run: () => move(-1), when: onFollowup, allowInInput: true});
  registerShortcut({key: "Alt+A", label: "Approve all of this conversation's suggested steps",
    run: () => root.querySelector(".support-review [data-followup-approve-all]")?.click(), when: onFollowup});
  registerShortcut({key: "Alt+E", label: "Edit this conversation's first waiting draft",
    run: () => root.querySelector(".support-review [data-followup-edit]")?.click(), when: onFollowup});
  root?.addEventListener("submit", event => {
    if (suggestionSubmit(event.target, followupContext)) event.preventDefault();
  });

  async function editCommitment(item, ident = "", complete = false) {
    const old = (item.commitments || []).find(c => c.id === ident);
    const task = old || {id: crypto.randomUUID(), title: "", kind: "work", state: "open", waiting_on: "you", check_back: "", depends_on: []};
    const sources = sourceRead?.key === supportKey(item) ? sourceRead.data?.entries || [] : [];
    const sourceIds = new Set([item.observed?.[1], item.answered?.[1], task.source_message_id].filter(Boolean));
    const actions = (item.suggestions?.actions || []).filter(a => !a.withdrawn), choices = prerequisiteChoices(item.commitments || [], task.id);
    showDialog(`<form id="support-commitment-form">${dialogHeader(complete ? "Record completed work" : old ? "Edit task" : "Add a separate task", e(item.student?.name || ""))}
      <div class="dialog-body"><label class="field">Task<input name="title" maxlength="200" required value="${e(task.title)}"></label>
      <div class="support-task-fields"><label class="field">Kind<select name="kind">${[["work","Work"],["reply","Reply"],["check_back","Check back"]].map(([v,l])=>`<option value="${v}"${task.kind===v?" selected":""}>${l}</option>`).join("")}</select></label>
      <label class="field">Waiting on<select name="waiting_on">${[["you","Me"],["student","Student"],["date","Check-back date"]].map(([v,l])=>`<option value="${v}"${task.waiting_on===v?" selected":""}>${l}</option>`).join("")}</select></label></div>
      <label class="field">Check back on<input type="date" name="check_back" value="${e(task.check_back || "")}"></label>
      <label class="field">State<select name="state">${[["open","Open"],["completed","Completed"],["cancelled","Cancelled"]].map(([v,l])=>`<option value="${v}"${(complete?"completed":task.state)===v?" selected":""}>${l}</option>`).join("")}</select></label>
      <label class="field">Source message<select name="source_message_id"><option value="">No message linked</option>${sources.filter(s=>sourceIds.has(s.message_id)).map(s=>`<option value="${e(s.message_id)}"${s.message_id===task.source_message_id?" selected":""}>${e(authorText(s,item.student?.name||"Student"))} · ${e(whenText(s.at))}</option>`).join("")}</select></label>
      <label class="field">Completion receipt<select name="action_id"><option value="">Work completed outside Faculty — record a note</option>${actions.map(a=>`<option value="${e(a.id)}"${a.id===task.action_id?" selected":""}>${e(a.effect?.title || a.summary || "Action")}</option>`).join("")}${task.action_id&&!actions.some(a=>a.id===task.action_id)?`<option value="${e(task.action_id)}" selected>Previously linked action</option>`:""}</select></label>
      ${choices.length?`<fieldset class="support-task-dependencies"><legend>Complete these first</legend>${choices.map(c=>`<label><input type="checkbox" name="dependency" value="${e(c.id)}"${task.depends_on?.includes(c.id)?" checked":""}> ${e(c.title)}</label>`).join("")}</fieldset>`:""}
      <label><input type="checkbox" name="blocks_reply"${task.blocks_reply?" checked":""}> The reply must wait for this work</label>
      <label class="field">Completion or cancellation note<textarea name="completion_note" maxlength="2000" rows="3">${e(task.completion_note||"")}</textarea></label>
      <p class="field-hint">Completing this task does not complete the other tasks or send a reply. A check-back date brings it to your attention; Watch until done can prepare a fresh review.</p>
      <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">${complete?"Record completion":"Save task"}</button></footer></form>`);
    const form = document.getElementById("support-commitment-form"), alive = dialogSession(form, dialog());
    let requestId="", last="";
    form.onsubmit = event => {event.preventDefault();void runFormAction(form, async()=>{
      const fields = new FormData(form), update={...task,...Object.fromEntries(fields), blocks_reply:fields.has("blocks_reply"),depends_on:fields.getAll("dependency")};
      delete update.dependency;delete update.effective_state;
      const tasks=(item.commitments||[]).map(c=>{const {effective_state,...saved}=c;return c.id===task.id?update:saved;});if(!old)tasks.push(update);
      const state=item.saved_state || item.state;
      const data={kind:"followup",enrollment_id:item.enrollment_id,source_key:item.source_key||"",title:item.title,details:item.details||"",date:item.date||"",next:item.next||"",
        state:["needs_reply","todo","waiting","snoozed","resolved"].includes(state)?state:"todo",observed:item.observed||[],commitments:tasks,...(item.id?{base_revision:item.revision}:{})};
      // Adding work to a resolved record reopens it explicitly as part of this save.
      if(data.state==="resolved"&&tasks.some(c=>c.state==="open"))data.state="todo";
      const signature=JSON.stringify(data);if(signature!==last){requestId=crypto.randomUUID();last=signature;}
      await api(item.id?`${BASE}/${encodeURIComponent(item.id)}`:BASE,"POST",{...data,request_id:requestId});
      if(alive())dialog().close();toast(complete?"Task completion recorded. Other work remains tracked.":"Task saved.");await render(true);void changed?.();
    });};
  }

  async function edit(itemKind, item = null, who = null) {
    const current = captureDialogIntent(dialog());
    const groups = (await api("/api/student-notes/students")).groups || [];
    if (!current()) return;
    const selected = item?.enrollment_id || who?.enrollmentId || "";
    const picker = studentPicker(e, { id: "support-student", name: "enrollment_id", label: "Student", value: selected, locked: Boolean(item?.id),
      groups: groups.map(group => ({label: `${group.code} · ${group.term_label}`, options: group.students.map(student => ({value: student.enrollment_id, text: `${student.name} · Section ${student.sections.join(", ")}`}))})),
      search: async digits => ((await api("/api/student-notes/students/search", "POST", {digits})).students || []).map(s => ({value: s.enrollment_id, text: `${s.name} · Section ${s.section_number}`})) });
    const states = itemKind === "appointment" ? ["scheduled", "attended", "cancelled", "no_show"] : ["needs_reply", "todo", "waiting", "snoozed", "resolved"];
    showDialog(`<form id="support-form">${dialogHeader(item?.id ? "Update " + (itemKind === "appointment" ? "office hours" : "follow-up") : itemKind === "appointment" ? "Schedule office hours" : "Add student follow-up")}
      <div class="dialog-body">${withCoordinator(item) ? `<p class="field-hint">${e(item.student.name)} · ${e(item.student.code)} · Course coordinator</p><input type="hidden" name="enrollment_id" value="${e(item.enrollment_id)}">` : `<label class="field">Student${picker.markup}</label>`}
      <label class="field">Purpose<input name="title" required maxlength="500" value="${e(item?.title || "")}"></label>
      <label class="field">Status<select name="state">${states.map(state => `<option value="${state}" ${(item?.state || states[0]) === state ? "selected" : ""}>${withCoordinator(item) && state === "waiting" ? "Waiting on coordinator" : SUPPORT_STATES[state]}</option>`).join("")}</select></label>
      ${itemKind === "followup" ? `<label class="field">Next step<input name="next" maxlength="200" value="${e(item?.next || "")}" placeholder="What this waits on, e.g. her doctor’s note" aria-describedby="support-next-hint"></label><p class="field-hint" id="support-next-hint">Shown at the top of the follow-up and in the list.</p>` : ""}
      <label class="field">${itemKind === "appointment" ? "Date" : "Follow-up date"}<input type="date" name="date" value="${e(item?.date || (itemKind === "appointment" ? localToday() : ""))}" ${itemKind === "appointment" ? "required" : ""}></label>
      ${itemKind === "appointment" ? `<div class="support-times"><label class="field">Start (campus time)<input type="time" name="time" required value="${e(item?.time || "14:00")}"></label><label class="field">End<input type="time" name="end_time" required value="${e(item?.end_time || "14:30")}"></label></div><div data-appointment-check aria-live="polite"></div><label class="field">Location<input name="location" maxlength="240" value="${e(item?.location || "")}"></label><p class="field-hint">The calendar shows “Office hours” with this time and location. Your purpose and student details stay here.</p>` : ""}
      <label class="field">Private details<textarea name="details" rows="3" maxlength="5000">${e(item?.details || "")}</textarea></label>
      <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save</button></footer></form>`);
    const form = document.getElementById("support-form"), alive = dialogSession(form, dialog());
    if (!withCoordinator(item)) picker.bind(form);
    let requestId = "", sent = "";
    // SCHEDULING ONE ENGINE: an appointment that overlaps the calendar, or falls outside office and meeting hours, is said here (a
    // warning only: any time can still be saved).
    let checking = 0;
    async function checkTime() {
      const slot = form.querySelector("[data-appointment-check]");
      if (!slot) return;
      const data = Object.fromEntries(new FormData(form));
      const run = ++checking;
      if (!data.date || !data.time || !data.end_time) { slot.innerHTML = ""; return; }
      const query = new URLSearchParams({ kind: "appointment", date: String(data.date), time: String(data.time), end_time: String(data.end_time),
        enrollment_id: String(data.enrollment_id || ""), own: item?.id ? `office-hours:${item.id}` : "" });
      try {
        const found = await api(`/api/scheduling/check?${query}`);
        if (run !== checking || !alive()) return;
        const overlaps = (found.overlaps || []).map(x => `${x.title} (${displaySpan(x.time, x.end_time)})`);
        slot.innerHTML = overlaps.length ? calloutMarkup({ tone: "warning", title: `Overlaps ${overlaps.join(", ")}`, body: "You can still save it." })
          : found.outside_hours ? calloutMarkup({ tone: "warning", title: "Outside your office and meeting hours", body: "You can still save it." }) : "";
      } catch { if (run === checking) slot.innerHTML = ""; }
    }
    if (itemKind === "appointment") {
      // MEETING LENGTH: End follows Start (before the change event, so the time check reads both).
      const start = /** @type {HTMLInputElement} */ (form.elements.namedItem("time"));
      const end = /** @type {HTMLInputElement} */ (form.elements.namedItem("end_time"));
      let before = start.value;
      start.addEventListener("input", () => {
        if (!start.value) return;
        end.value = followingEnd(start.value, before, end.value);
        before = start.value;
      });
      form.addEventListener("change", () => void checkTime());
      void checkTime();
    }
    form.onsubmit = event => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const data = Object.fromEntries(new FormData(form));
        if (!data.enrollment_id) throw new Error("Choose the student.");
        if (item?.source_key && data.enrollment_id !== item.enrollment_id) throw new Error("This conversation belongs to the selected student. Add a separate follow-up for someone else.");
        Object.assign(data, {kind: itemKind, source_key: item?.source_key || "", observed: item?.observed || [], ...(item?.id ? {base_revision: item.revision} : {})});
        const content = JSON.stringify(data);
        if (content !== sent) { requestId = crypto.randomUUID(); sent = content; }
        await api(item?.id ? `${BASE}/${encodeURIComponent(item.id)}` : BASE, "POST", {...data, request_id: requestId});
        toast("Saved");
        if (alive()) dialog().close();
        void changed?.();
        await render(true);
      });
    };
  }

  /** TERM POLICIES: the instructor's own policies per course term; every follow-up suggestion for that term's students follows them. */
  const MEETING = "meeting_hours";
  async function openPolicies() {
    const current = captureDialogIntent(dialog());
    const data = await api(`${BASE}/policies?areas=1`);
    if (!current()) return;
    const terms = data.terms || [];
    if (!terms.length) {
      showDialog(`<div>${dialogHeader("Term policies", "")}<div class="dialog-body">${emptyMarkup({ title: "No current term", body: "Mark a course term as current to write its policies." })}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer></div>`);
      return;
    }
    const areas = data.areas || [], modes = data.modes || [];
    const drafts = new Map(terms.map(term => [term.term_id, structuredClone(term.areas || {})]));
    // MEETING HOURS (TEACHING-WORKFLOWS, 2026-10-05): the term's weekly times for meetings outside office hours, saved beside the policies.
    const hours = new Map(terms.map(term => [term.term_id, structuredClone(term.meeting_hours?.windows || [])]));
    // MAKEUP ROOMS (2026-10-05): the office-hours rooms big enough to seat a makeup, ticked from the rooms on the calendar.
    const makeupRooms = new Map(terms.map(term => [term.term_id, [...(term.meeting_hours?.makeup_rooms || [])]]));
    const requests = new Map(), hourRequests = new Map();
    let chosen = terms[0];
    let areaId = chosen.areas?.additional ? "additional" : areas[0]?.id;
    const draft = () => drafts.get(chosen.term_id);
    const entry = id => draft()[id] || { mode: "unset", guidance: "" };
    const packed = value => JSON.stringify(Object.fromEntries(areas.flatMap(area => {
      const policy = value[area.id];
      return policy && policy.mode !== "unset" ? [[area.id, policy]] : [];
    })));
    const policiesDirty = term => packed(drafts.get(term.term_id)) !== packed(term.areas || {});
    const hoursDirty = term => JSON.stringify(hours.get(term.term_id)) !== JSON.stringify(term.meeting_hours?.windows || [])
      || JSON.stringify(makeupRooms.get(term.term_id)) !== JSON.stringify(term.meeting_hours?.makeup_rooms || []);
    const dirty = term => policiesDirty(term) || hoursDirty(term);
    const saved = term => term.revision ? `Saved ${wallDate(term.updated_at.slice(0, 10))} · revision ${term.revision}` : "Not written yet.";
    showDialog(`<form id="support-policies">${dialogHeader("Term policies", "Choose an area and tell the Assistant how you want it handled.")}
      <div class="dialog-body"><div class="policy-term">${terms.length > 1 ? `<label class="field"><span id="policy-term-label">Term</span><select name="term_id" aria-labelledby="policy-term-label">${terms.map(term => `<option value="${e(term.term_id)}">${e(`${term.code} · ${term.term_label}`)}</option>`).join("")}</select></label>`
        : `<strong>${e(`${chosen.code} · ${chosen.term_label}`)}</strong>`}<p class="field-hint" data-policy-saved>${e(saved(chosen))}</p></div>
      <div class="policy-workspace"><nav class="policy-areas" aria-label="Policy areas"></nav><section class="policy-editor" aria-labelledby="policy-area-title"></section></div>
      <section data-policy-suggestions></section>
      <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><p class="field-hint" data-policy-progress aria-live="polite"></p><button type="button" class="button" data-action="close">Close</button><button type="submit" class="button primary">Save policies</button></footer></form>`);
    const form = document.getElementById("support-policies"), alive = dialogSession(form, dialog());
    const nav = form.querySelector(".policy-areas"), editor = form.querySelector(".policy-editor");
    const select = form.elements.namedItem("term_id"), save = form.querySelector('[type="submit"]');
    const proposals = form.querySelector('[data-policy-suggestions]');
    function drawProposals() {
      const candidates = chosen.suggested_guidance || [];
      proposals.innerHTML = candidates.length ? `<h3>Guidance from your edits</h3><p class="field-hint">You used this wording in replies to different students. Review whether it should guide similar requests in this term. Nothing is adopted until you save policies.</p>${candidates.map((p, i) => `<details class="disclosure"><summary>${e(p.cases)} conversations used similar corrections</summary><div class="disclosure-body">${p.before ? `<p><strong>Original wording</strong> ${e(p.before)}</p>` : ""}<p><strong>Your wording</strong> ${e(p.wording)}</p><button type="button" class="button" data-policy-proposal="${i}">Review as guidance…</button></div></details>`).join("")}` : "";
    }
    proposals.onclick = event => {
      const button = event.target.closest('[data-policy-proposal]');
      if (!button) return;
      const candidate = chosen.suggested_guidance[Number(button.dataset.policyProposal)];
      areaId = areaId === MEETING ? 'additional' : areaId;
      const old = entry(areaId);
      if (!old.guidance.includes(candidate.wording)) draft()[areaId] = {mode: 'guidance', guidance: [old.guidance, candidate.guidance].filter(Boolean).join('\n\n')};
      drawEditor();
      form.elements.namedItem('policy_guidance')?.focus();
    };
    function progress() {
      const count = areas.filter(area => entry(area.id).mode !== "unset").length;
      form.querySelector("[data-policy-progress]").textContent = `${count} of ${areas.length} areas set${dirty(chosen) ? " · Unsaved changes" : ""}`;
      form.querySelector("[data-policy-saved]").textContent = saved(chosen);
      save.disabled = form.dataset.saving === "true" || !dirty(chosen);
      // The footer's button (the header's ✕ is a close button too, and comes first).
      form.querySelector('.dialog-footer [data-action="close"]').textContent = terms.some(dirty) ? "Cancel" : "Close";
      for (const button of nav.querySelectorAll("[data-policy-area]")) {
        const mode = entry(button.dataset.policyArea).mode;
        button.querySelector("small").textContent = button.dataset.policyArea === MEETING
          ? (hours.get(chosen.term_id).length ? "Set" : "—") : mode === "ask" ? "Ask me" : mode === "guidance" ? "Set" : "—";
      }
    }
    const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    function drawHours(focus) {
      // SCHEDULING "USE THIS SCHEDULE": once the term uses its Term schedule, its meeting hours and makeup rooms live there.
      if (chosen.schedule?.in_use) {
        const windows = chosen.schedule.windows || [];
        editor.oninput = editor.onchange = editor.onclick = null;
        editor.innerHTML = `<h3 id="policy-area-title">Meeting hours</h3>
          <p class="policy-prompt">This term's meeting hours and the office-hours rooms that seat a makeup now come from its Term schedule.</p>
          <ul class="meeting-hours-summary">${windows.map(w => `<li>${e(DAYS[w.weekday])} · ${e(displaySpan(w.start, w.end))}${w.location ? ` · ${e(w.location)}` : ""}</li>`).join("") || "<li>No meeting hours.</li>"}</ul>
          <p class="field-hint">Makeups during office hours: ${e((chosen.schedule.makeup_rooms || []).join(", ") || "no rooms")}.</p>
          <p><a class="link-arrow" href="${e(chosen.schedule.href)}">Edit in the Term schedule</a></p>`;
        progress();
        if (focus) editor.querySelector("a")?.focus();
        return;
      }
      const rows = hours.get(chosen.term_id), ticked = makeupRooms.get(chosen.term_id);
      const rooms = [...new Set([...(data.office_rooms || []), ...ticked])].sort((a, b) => a.localeCompare(b));
      editor.innerHTML = `<h3 id="policy-area-title">Meeting hours</h3>
        <p class="policy-prompt">When may a student meet you outside office hours this term? Replies offer only open times inside these. A quiz makeup card offers them too, on the next two days that have an open time. Each term sets its own.</p>
        <div class="meeting-hours">${rows.map((row, index) => `<div class="meeting-hours-row" data-meeting-row="${index}">
          <label class="field"><span>Day</span><select name="meeting_day" aria-label="Day">${DAYS.map((day, n) => `<option value="${n}"${n === row.weekday ? " selected" : ""}>${day}</option>`).join("")}</select></label>
          <label class="field"><span>From</span><input type="time" name="meeting_start" aria-label="From" step="1800" value="${e(row.start)}" required></label>
          <label class="field"><span>Until</span><input type="time" name="meeting_end" aria-label="Until" step="1800" value="${e(row.end)}" required></label>
          <label class="field"><span>Room</span><input name="meeting_room" aria-label="Room" maxlength="80" value="${e(row.location)}" placeholder="BLOC 319"></label>
          <button type="button" class="text-button" data-meeting-remove="${index}" aria-label="Remove ${e(DAYS[row.weekday])} ${e(row.start)}–${e(row.end)}">Remove</button></div>`).join("")}
        <button type="button" class="button" data-meeting-add>Add a time</button></div>
        <p class="field-hint">${rows.length ? "A reply never asks the student for their general availability while these are set." : "None set: replies ask when the student can meet, and makeup cards offer only office hours in the rooms below."}</p>
        <fieldset class="meeting-rooms"><legend>Makeups during office hours</legend>
          <p class="field-hint">Tick the rooms big enough to seat a student taking a makeup apart from office-hours visitors. Makeup cards offer office hours only in these rooms.</p>
          ${rooms.length ? rooms.map(room => `<label><input type="checkbox" name="makeup_room" value="${e(room)}"${ticked.some(t => t.toLowerCase() === room.toLowerCase()) ? " checked" : ""}> ${e(room)}</label>`).join("")
            : '<p class="field-hint">No office hours on your calendar in the next six weeks.</p>'}</fieldset>`;
      const read = () => {
        hours.set(chosen.term_id, [...editor.querySelectorAll("[data-meeting-row]")].map(row => ({
          weekday: Number(row.querySelector('[name="meeting_day"]').value), start: row.querySelector('[name="meeting_start"]').value,
          end: row.querySelector('[name="meeting_end"]').value, location: row.querySelector('[name="meeting_room"]').value.trim() })));
        makeupRooms.set(chosen.term_id, [...editor.querySelectorAll('[name="makeup_room"]:checked')].map(box => box.value));
      };
      editor.oninput = () => { read(); progress(); };
      editor.onchange = () => { read(); progress(); };
      editor.onclick = event => {
        const remove = event.target.closest("[data-meeting-remove]"), add = event.target.closest("[data-meeting-add]");
        if (!remove && !add) return;
        read();
        const list = hours.get(chosen.term_id);
        if (remove) list.splice(Number(remove.dataset.meetingRemove), 1);
        else list.push({ weekday: 1, start: "13:00", end: "17:00", location: list.at(-1)?.location || "" });
        drawHours(false);
        (add ? editor.querySelector("[data-meeting-row]:last-of-type select") : editor.querySelector("[data-meeting-add]"))?.focus();
        progress();
      };
      progress();
      if (focus) editor.querySelector("select, [data-meeting-add]")?.focus();
    }
    function drawEditor(focus = false) {
      if (areaId === MEETING) {
        for (const button of nav.querySelectorAll("[data-policy-area]")) {
          if (button.dataset.policyArea === areaId) button.setAttribute("aria-current", "true");
          else button.removeAttribute("aria-current");
        }
        drawHours(focus);
        return;
      }
      editor.oninput = editor.onchange = editor.onclick = null;
      const area = areas.find(area => area.id === areaId), policy = entry(areaId);
      if (!area) return;
      const editing = !focus && editor.contains(document.activeElement) ? document.activeElement : null;
      const selection = editing?.name === "policy_guidance" ? [editing.selectionStart, editing.selectionEnd] : null;
      for (const button of nav.querySelectorAll("[data-policy-area]")) {
        if (button.dataset.policyArea === areaId) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      }
      nav.querySelector('[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
      editor.innerHTML = `<h3 id="policy-area-title">${e(area.label)}</h3><p class="policy-prompt">${e(area.prompt)}</p>
        <label class="field"><span id="policy-handling-label">How to handle this area</span><select name="policy_mode" aria-labelledby="policy-handling-label">${modes.map(mode => `<option value="${e(mode.id)}"${mode.id === policy.mode ? " selected" : ""}>${e(mode.label)}</option>`).join("")}</select></label>
        <p class="field-hint" data-policy-handling></p>
        <label class="field"><span id="policy-guidance-label">Your guidance</span><textarea name="policy_guidance" aria-labelledby="policy-guidance-label" rows="8" maxlength="${data.limit || 20000}" placeholder="${e(area.placeholder)}">${e(policy.guidance)}</textarea></label>
        <p class="field-hint">Write as you would brief a TA. These fields start blank; the prompts are questions to consider, not rules.</p>`;
      const mode = form.elements.namedItem("policy_mode"), field = form.elements.namedItem("policy_guidance");
      function handling() {
        editor.querySelector("[data-policy-handling]").textContent = mode.value === "ask"
          ? "The Assistant will ask for your guidance before proposing a decision in this area. Add any context below."
          : mode.value === "guidance" ? "The Assistant will use the guidance below in its replies and proposals."
            : "This area adds no policy. Writing guidance below selects Follow my guidance.";
      }
      field.oninput = () => {
        if (field.value.trim() && mode.value === "unset") mode.value = "guidance";
        draft()[areaId] = { mode: mode.value, guidance: field.value };
        handling(); progress();
      };
      mode.onchange = () => {
        // TERM POLICIES: choosing unset clears this area's guidance only, as the option's empty state promises.
        if (mode.value === "unset") field.value = "";
        draft()[areaId] = { mode: mode.value, guidance: field.value };
        handling(); progress();
      };
      handling(); progress();
      if (focus) mode.focus();
      else if (editing) {
        const control = editing.name === "policy_guidance" ? field : mode;
        control.focus();
        if (selection) field.setSelectionRange(...selection);
      }
    }
    function drawNavigation() {
      let group = "";
      nav.innerHTML = areas.map(area => {
        const heading = area.group !== group ? `<p class="section-label">${e(area.group)}</p>` : "";
        group = area.group;
        const meeting = area.id === "appointments" ? `<button type="button" data-policy-area="${MEETING}"><span>Meeting hours</span><small></small></button>` : "";
        return `${heading}${meeting}<button type="button" data-policy-area="${e(area.id)}"><span>${e(area.label)}</span><small></small></button>`;
      }).join("");
      drawEditor();
    }
    nav.onclick = event => {
      const button = event.target.closest("[data-policy-area]");
      if (!button) return;
      areaId = button.dataset.policyArea;
      drawEditor(true);
    };
    if (select) select.onchange = () => {
      chosen = terms.find(term => term.term_id === select.value) || chosen;
      drawEditor();
      drawProposals();
    };
    drawNavigation();
    drawProposals();
    if (select) select.value = chosen.term_id;
    form.onsubmit = event => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const target = chosen, saved = [];
        if (hoursDirty(target)) {
          const body = { windows: structuredClone(hours.get(target.term_id)), makeup_rooms: [...makeupRooms.get(target.term_id)], base_revision: target.meeting_hours?.revision || 0 };
          const content = JSON.stringify(body), previous = hourRequests.get(target.term_id);
          const request = previous?.content === content ? previous : { content, id: crypto.randomUUID() };
          hourRequests.set(target.term_id, request);
          const result = await api(`${BASE}/meeting-hours/${encodeURIComponent(target.term_id)}`, "POST", { ...body, request_id: request.id });
          // The server orders the times: the draft takes its order unless it was edited while the save was in flight.
          if (JSON.stringify(hours.get(target.term_id)) === JSON.stringify(body.windows)) hours.set(target.term_id, structuredClone(result.windows));
          if (JSON.stringify(makeupRooms.get(target.term_id)) === JSON.stringify(body.makeup_rooms)) makeupRooms.set(target.term_id, [...result.makeup_rooms]);
          target.meeting_hours = result;
          saved.push("Meeting hours");
        }
        if (policiesDirty(target)) {
          const body = { areas: JSON.parse(packed(drafts.get(target.term_id))), base_revision: target.revision };
          const content = JSON.stringify(body), previous = requests.get(target.term_id);
          const request = previous?.content === content ? previous : { content, id: crypto.randomUUID() };
          requests.set(target.term_id, request);
          const result = await api(`${BASE}/policies/${encodeURIComponent(target.term_id)}`, "POST", { ...body, request_id: request.id });
          Object.assign(target, result);
          target.suggested_guidance = (target.suggested_guidance || []).filter(p => !result.content.includes(p.wording));
          // FRONTEND: a reply to Save must not replace edits made while it was in flight.
          if (packed(drafts.get(target.term_id)) === JSON.stringify(body.areas)) drafts.set(target.term_id, structuredClone(result.areas));
          saved.push("Term policies");
        }
        toast(`${saved.join(" and ")} saved for ${target.code} · ${target.term_label}.`);
        if (alive()) { drawEditor(); drawProposals(); }
        await render(true);
      }).finally(() => { if (alive()) progress(); });
    };
  }

  async function openForStudent(who) {
    const current = captureDialogIntent(dialog());
    const data = await api(`${BASE}?enrollment_id=${encodeURIComponent(who.enrollmentId)}`);
    if (!current()) return;
    showDialog(`<div class="support-student">${dialogHeader("Follow-up and office hours", e(who.name || ""))}<div class="dialog-body">
      <div class="support-actions"><button type="button" class="button" data-support-add-follow>Add follow-up</button><button type="button" class="button" data-support-add-meeting>Schedule office hours</button></div>
      ${data.items.length ? data.items.map((item, index) => `<p><button type="button" class="text-button" data-support-person-edit="${index}">${e(item.title)}</button> · ${e(SUPPORT_STATES[item.state])}${item.date ? ` · ${e(wallDate(item.date))}` : ""}</p>`).join("") : '<p class="field-hint">No follow-ups or appointments yet.</p>'}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer></div>`);
    const host = dialog().firstElementChild;
    host.onclick = event => {
      if (event.target.closest("[data-support-add-follow]")) void edit("followup", null, who).catch(cause => toast(errorMessage(cause), true));
      if (event.target.closest("[data-support-add-meeting]")) void edit("appointment", null, who).catch(cause => toast(errorMessage(cause), true));
      const button = event.target.closest("[data-support-person-edit]");
      if (button) { const item = data.items[Number(button.dataset.supportPersonEdit)]; void edit(item.kind, item).catch(cause => toast(errorMessage(cause), true)); }
    };
  }

  root?.addEventListener("input", event => { if (event.target.matches("[data-support-query]")) { query = event.target.value; draw(); } });
  root?.addEventListener("change", event => {
    if (event.target.matches("[data-support-filter]")) { filter = event.target.value || "active"; draw(); }
    else suggestionChange(event.target, followupContext);  // DECISION ON THE CARD: an absence card's dropdown
  });
  root?.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-desk-view")) {
      const target = button.dataset.deskView;
      if (target === "office-hours") { location.hash = "#office-hours"; return; }
      if (target === "action" || target === "tasks") {
        deskView = target;
        if (currentPath() !== "#follow-ups") location.hash = "#follow-ups";
        else draw();
      }
      return;
    }
    if (button.hasAttribute("data-desk-open")) { const item=view.items[Number(button.dataset.deskOpen)];if(item){selected=supportKey(item);deskView="action";draw();root.querySelector(".support-review")?.scrollIntoView({block:"start"});}return; }
    if (button.hasAttribute("data-desk-action")) {
      const target=button.dataset.deskAction;
      const node=target==="tasks"?root.querySelector(".support-task-plan"):target==="conversation"?root.querySelector(".support-review-source"):
        target==="question"?root.querySelector(".support-ask"): [...root.querySelectorAll("[data-action-id]")].find(n=>n.getAttribute("data-action-id")===target);
      if(node){node.closest("details")?.setAttribute("open","");node.scrollIntoView({block:"center",behavior:"auto"});node.querySelector("button,textarea,input")?.focus();}return;
    }
    if (button.hasAttribute("data-desk-watch")) { const node=root.querySelector(".support-watches");node?.scrollIntoView({block:"center"});node?.querySelector("button")?.focus();return; }
    if (button.hasAttribute("data-desk-split")) {
      const item=view.items[Number(button.closest("[data-support-item]")?.dataset.supportItem)];
      if(!item)return;setBusy(button,true);button.dataset.requestId ||= crypto.randomUUID();
      void api(`${BASE}/followups/suggestions/redo`,"POST",{enrollment_id:item.enrollment_id,source_key:item.source_key||"",item_id:item.id||"",request_id:button.dataset.requestId,
        text:"Organize the instructor's existing outstanding promises into separate commitments on this follow-up. Read the current conversation and records; propose only the tracking revision, preserving existing task identities. Separate work, replies, and explicitly dated check backs. Do not invent deadlines or infer that work is done. Do not send a message or perform the work. Each proposed task must be reviewable before it is saved."})
        .then(()=>{toast("Preparing separate tasks for your review.");return render(true);})
        .catch(cause=>toast(errorMessage(cause),true)).finally(()=>setBusy(button,false));return;
    }
    if (button.hasAttribute("data-commitment-watch")) {
      const item=view.items[Number(button.closest("[data-support-item]")?.dataset.supportItem)];
      const task=item?.commitments?.find(c=>c.id===button.dataset.commitmentWatch);
      if(task)void openWatch(item,followupContext,e,task).catch(cause=>toast(errorMessage(cause),true));return;
    }
    if (button.hasAttribute("data-commitment-add")||button.hasAttribute("data-commitment-edit")||button.hasAttribute("data-commitment-complete")) {
      const item=view.items[Number(button.closest("[data-support-item]")?.dataset.supportItem)];
      if(item)void editCommitment(item,button.dataset.commitmentEdit||button.dataset.commitmentComplete||"",button.hasAttribute("data-commitment-complete")).catch(cause=>toast(errorMessage(cause),true));return;
    }
    // The read-error Try again; an item's own Retry conversation analysis (data-support-retry) is the supportRetry action below.
    if (button.hasAttribute("data-support-reload")) { void render(true); return; }
    // JEV HOST FAILURES: Retry all failed, the instructor's click, one request ID per click (sent again, it asks nothing twice).
    if (button.hasAttribute("data-support-retry-failed")) {
      setBusy(button, true);
      button.dataset.requestId ||= crypto.randomUUID();
      void api("/api/jev/triage/retry-failed", "POST", {request_id: button.dataset.requestId})
        .then(result => {
          delete button.dataset.requestId;
          toast(result.stopped ? `Stopped after ${result.asked} of ${result.failed}: ${result.stopped}` : result.asked ? `Analyzed again: ${result.complete} of ${result.asked} completed.` : "Nothing is left to retry.", Boolean(result.stopped));
        })
        .catch(cause => toast(errorMessage(cause), true))
        .finally(() => { setBusy(button, false); void render(true); });
      return;
    }
    if (button.hasAttribute("data-support-select")) {
      const next = view.items[Number(button.dataset.supportSelect)];
      if (next) { selected = supportKey(next); draw(); }
      return;
    }
    if (button.hasAttribute("data-support-detail")) { detailFailed = null; draw(); return; }
    if (button.hasAttribute("data-support-move")) { move(Number(button.dataset.supportMove)); return; }
    if (button.hasAttribute("data-support-tab")) { filter = button.dataset.supportTab || "active"; draw(); return; }
    if (button.hasAttribute("data-regrade-approve-all")) { confirmRegrades(); return; }
    if (button.hasAttribute("data-question-option")) { chooseQuestionOption(button); return; }
    if (button.hasAttribute("data-followup-approve-all") && button.closest(".support-review")) advanceFrom = selected;
    if (suggestionClick(button, followupContext)) return;
    if (button.hasAttribute("data-support-policies")) { void openPolicies().catch(cause => toast(errorMessage(cause), true)); return; }
    if (button.hasAttribute("data-support-fyi")) { filter = "fyi"; draw(); root.querySelector("[data-support-filter]")?.focus(); return; }
    if (button.dataset.followupPause) {
      setBusy(button, true);
      void api("/api/student-support/followups/suggestions/pause", "POST", { paused: button.dataset.followupPause === "on", request_id: crypto.randomUUID() })
        .then(result => toast(result.paused ? "Automatic suggestions are paused. Prepare now still works." : "Automatic suggestions are on."))
        .catch(cause => toast(errorMessage(cause), true))
        .finally(() => { setBusy(button, false); void render(true); });
      return;
    }
    const approve = button.dataset.assistantApprove, decline = button.dataset.assistantDecline;
    if (approve || decline) {
      const actionCard = button.closest("[data-action-id]");
      if (button.closest(".support-review")) advanceFrom = selected;
      setBusy(button, true);
      const seen = Number(actionCard.dataset.seen);
      // SIGN IN WHEN NEEDED (ABSENCES "DEPARTMENT MAKEUP EXAMS", 2026-10-23): a department makeup request signs in to Google first
      // when Faculty's kept sign-in ended, then is sent as usual; closing the sign-in window sends nothing.
      const signedIn = approve && actionCard?.dataset.endpoint === DEPARTMENT_REQUEST
        ? ensureGoogleSignIn({ api, showDialog, dialogHeader, escape: e, toast }) : Promise.resolve(true);
      // APPROVING: a refresh while it is sent shows the step as Approving…, not with its Approve button again.
      void signedIn.then(ok => {
        if (!ok) return { stale: true, message: "Not signed in to Google, so nothing was sent.", action: {} };
        return approve ? whileApproving([approve], () => submitActionReview(api, approve, seen, true), followupContext.redraw) : submitActionReview(api, decline, seen, false);
      })
        .then(result => {
          const apply = approve && result.result?.apply;  // REGRADES "FOLLOW-UP": a regrade decision's Apply, driven while Faculty is open
          if (apply) { void followApply(result.action?.path || "", apply); return; }
          toast(approve ? actionReviewMessage(result) : "Declined.", Boolean(result.stale) || result.result?.status === "uncertain"); void changed?.();
        })
        .catch(cause => toast(errorMessage(cause), true))
        .finally(() => { setBusy(button, false); void render(true).then(advance); });
      return;
    }
    const action = Object.keys(button.dataset).find(key => key.startsWith("support"));
    if (action === "supportDrive") { void allowDrive(api, button, toast); return; }
    const item = view?.items[Number(button.dataset[action])];
    const work = async () => {
      if (action === "supportNew") return edit(kind());
      if (!item) return;
      if (action === "supportSource") { readMessage(item, true); draw(); return; }
      const who = {enrollmentId: item.enrollment_id, name: item.student.name};
      if (action === "supportEdit") return edit(item.kind, item);
      if (action === "supportRecord") return withCoordinator(item) ? undefined : openStudent(personTarget(item));
      if (action === "supportMessages") return communications({...who, context: item.student.code});
      if (action === "supportWork") return openWork(item);
      // WRITE IN THE THREAD: an email conversation is answered in the student's own Gmail thread.
      // A COORDINATOR'S FOLLOW-UP: Write to coordinator… opens Mail's own compose window to the address on the course card, in the
      // coordinator's thread when their latest message here is an email; Send stays Mail's, and the send is recorded with the follow-up.
      if (action === "supportWrite" && withCoordinator(item)) {
        const reply = sourceRead?.key === supportKey(item) ? replyTarget(item, sourceRead.data?.entries) : null;
        if (!item.student.email) throw new Error("This course is no longer coordinated, so Faculty holds no address for the coordinator.");
        return editMail({to: item.student.email, subject: reply?.subject || "", replyThreadId: reply?.replyThreadId || "",
          context: `To ${item.student.name}, coordinator of ${item.student.code}`,
          onSent: item.source_key ? (/** @type {any} */ _result, /** @type {string} */ requestId) => recordWritten(item, requestId) : undefined});
      }
      if (action === "supportWrite") return writeStudent({...personTarget(item), reply: sourceRead?.key === supportKey(item) ? replyTarget(item, sourceRead.data?.entries) : null,
        onSent: item.source_key ? (/** @type {any} */ _result, /** @type {string} */ requestId) => recordWritten(item, requestId) : undefined});
      if (action === "supportJoin") return openJoin(item);
      if (action === "supportJoinMatch" || action === "supportSeparate" || action === "supportFinished" || action === "supportWaits" || action === "supportLater") {
        setBusy(button, true);
        try {
          button.dataset.requestId ||= crypto.randomUUID();
          if (action === "supportFinished") {
            await api(`${BASE}/${encodeURIComponent(item.id)}`, "POST", {title: item.title, details: item.details || "", date: "", state: "resolved",
              ...(item.next ? {next: item.next} : {}), observed: item.observed || [], base_revision: item.revision, request_id: button.dataset.requestId});
          } else if (action === "supportWaits") {
            // ONE CLICK ON A TO DO: the To do's next step was the instructor's own task, so it is not carried over.
            await api(`${BASE}/${encodeURIComponent(item.id)}`, "POST", {title: item.title, details: item.details || "", date: "", state: "waiting",
              observed: item.observed || [], base_revision: item.revision, request_id: button.dataset.requestId});
          } else if (action === "supportLater") {
            // CHECK BACK: Wait a week keeps what it waits for and comes back on a date of the instructor's own.
            await api(`${BASE}/${encodeURIComponent(item.id)}`, "POST", {title: item.title, details: item.details || "", date: addDays(campusToday(), 7), state: "waiting",
              ...(item.next ? {next: item.next} : {}), observed: item.observed || [], base_revision: item.revision, request_id: button.dataset.requestId});
          } else if (action === "supportJoinMatch") {
            await api(`${BASE}/followups/join`, "POST", {enrollment_id: item.enrollment_id, source_key: item.source_key, with_key: item.match.into_key, request_id: button.dataset.requestId});
          } else {
            await api(`${BASE}/followups/separate`, "POST", {enrollment_id: item.enrollment_id, source_key: button.dataset.key, request_id: button.dataset.requestId});
          }
          delete button.dataset.requestId;
          toast(action === "supportFinished" ? "Resolved." : action === "supportLater" ? `Checks back ${wallDate(addDays(campusToday(), 7))}.` : action === "supportWaits" ? `Now waits on ${shortName(item) || someone(item)}.` : action === "supportJoinMatch" ? "Joined. They are one follow-up now." : "Kept separate.");
          void changed?.();
          await render(true);
        } finally { setBusy(button, false); }
        return;
      }
      if (action === "supportAbsence" && item.triage?.mail && draftAbsence) return draftAbsence(item.triage.mail);
      if (action === "supportMeeting") return edit("appointment", {enrollment_id: item.enrollment_id, title: "Office hours", state: "scheduled"});
      if (action === "supportReviewed") {
        button.disabled = true;
        try {
          button.dataset.requestId ||= crypto.randomUUID();
          await api(`${BASE}/wellbeing/reviewed`, "POST", {enrollment_id: item.enrollment_id, source_key: item.source_key,
            call_id: item.wellbeing.call_id, request_id: button.dataset.requestId});
          delete button.dataset.requestId;
          toast("Marked reviewed");
          void changed?.();
          await render(true);
        } finally { button.disabled = false; }
        return;
      }
      if (action === "supportRetry") {
        button.disabled = true;
        try {
          button.dataset.requestId ||= crypto.randomUUID();
          await api("/api/jev/triage/retry", "POST", {enrollment_id: item.enrollment_id, source_key: item.source_key,
            fingerprint: item.triage.fingerprint, request_id: button.dataset.requestId});
          delete button.dataset.requestId;
          await render(true);
        } finally { button.disabled = false; }
      }
      if (action === "supportNote") return notes({enrollmentId: item.enrollment_id, date: item.date, text: `${SUPPORT_STATES[item.state]}: ${item.title}${item.details ? `\n${item.details}` : ""}`});
      if (action === "supportFollow") return edit("followup", {enrollment_id: item.enrollment_id, title: `Follow up: ${item.title}`, state: "needs_reply"});
    };
    void work().catch(cause => toast(errorMessage(cause), true));
  });
  window.addEventListener("faculty:assistant-actions-changed", () => { if (currentPath() === "#follow-ups") void render(true); });
  // DONE AT ONCE (2026-10-10): a chat's turn joined, separated or prepared a follow-up again, or made a card for one.
  window.addEventListener("faculty:assistant-pages-changed", () => { if (currentPath() === "#follow-ups") void render(true); });
  return {render, openForStudent: who => openForStudent(who).catch(cause => toast(errorMessage(cause), true))};
}
