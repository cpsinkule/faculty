// @ts-check
/* Pure, read-only views of one seating chart payload (GET /api/seating/<id>): its next step, its exam date, the
   consequences of reseating a confirmed chart, the student filters and the DR bookings of other exams. Nothing here
   decides or stores anything; the server's chart and its checks stay the source of every count. (UX overhaul, W2-SEATING.) */
import { CAMPUS_ZONE } from "./calendar-feed.js";
import { daysFrom, localToday, wallDate } from "./ui.js";

/** @param {number} count @param {string} one @param {string} [many] */
export function plural(count, one, many = `${one}s`) {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

/**
 * The exam's campus date as a wall-clock date: 'Thu, Sep 17', its time as typed, and how far it is from today.
 * @param {any} chart
 * @param {string} [today] YYYY-MM-DD
 */
export function examTiming(chart, today = localToday()) {
  const iso = String(chart?.exam_date || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return null;
  const date = wallDate(iso);
  const time = String(chart.exam_time || "").trim();
  const relative = daysFrom(iso, today);
  const short = [date, time, relative].filter(Boolean).join(" · ");
  return { iso, date, time, relative, short, line: `Exam · ${short}`, held: iso < today, today: iso === today };
}

/** Room review reasons and roster issues, exactly as the chart page has always read them. @param {any} chart */
export function chartProblems(chart) {
  const roomReview = chart?.check?.room_review || chart?.room?.review_required || [];
  const rosterIssues = (chart?.issues || []).filter((/** @type {string} */ issue) => !issue.startsWith("Room review: "));
  return { roomReview, rosterIssues };
}

/**
 * The chart's one next step. `action` is the chart page's own action key (see seating.js chartAction), except "room"
 * (open the room) and "issues" (go to the roster issues). Problems come first; after the exam date the chart says so.
 * @param {any} chart
 * @param {{today?: string, seatUpdateRequested?: boolean}} [options]
 */
export function nextAction(chart, { today = localToday(), seatUpdateRequested = false } = {}) {
  const { roomReview, rosterIssues } = chartProblems(chart);
  const hasSeatUpdates = Boolean(chart?.confirmed_at && (chart?.seat_updates?.pending || seatUpdateRequested));
  const exam = examTiming(chart, today);
  if (roomReview.length) {
    return { key: "room", action: "room", attention: true, icon: "seat", label: "Review room",
      title: "Review this room before printing", detail: "Check the flagged seat labels and chair locations in Room settings." };
  }
  if (rosterIssues.length) {
    // The header's primary is the fix itself (the same Update seating as the callout under the chart names), not a scroll to it.
    return { key: "issues", action: "update", attention: true, icon: "info", label: "Update seating",
      title: "The seating chart needs attention", detail: "The current roster or DR bookings differ from these seats." };
  }
  if (hasSeatUpdates) {
    return { key: "seat-updates", action: "seat-updates", attention: true, icon: "cards", label: "Send updated seat cards",
      title: seatUpdatesTitle(chart?.seat_updates?.pending), detail: "Students whose seats changed after confirmation still need their updated seat cards." };
  }
  if (exam?.held) {
    return { key: "held", action: "print-roster", attention: false, icon: "print", label: "Print room roster",
      title: `Exam held ${exam.date}`, detail: "Printed stamps keep the seats they were exported with." };
  }
  if (exam?.today) {
    return { key: "today", action: "print-roster", attention: false, icon: "print", label: "Print room roster",
      title: `Exam today · ${exam.date}`, detail: "Print the room roster for check-in; the chart is ready to post." };
  }
  if (!chart?.confirmed_at) {
    return { key: "confirm", action: "confirm", attention: false, icon: "check", label: "Confirm for printing",
      title: "Review seats, then confirm for printing", detail: "Confirming prepares stamped exams or labels; response cards are reviewed on the way." };
  }
  return { key: "print", action: "print", attention: false, icon: "print", label: "Print chart",
    title: "Seating confirmed for printing", detail: "Print the stamped papers and the room chart." };
}

/** '1 student needs an updated seat card' / '3 students need updated seat cards'. @param {number} [pending] */
export function seatUpdatesTitle(pending) {
  const count = Number(pending) || 0;
  if (!count) return "Updated seat cards to send";
  return count === 1 ? "1 student needs an updated seat card" : `${count.toLocaleString()} students need updated seat cards`;
}

/**
 * A recorded moment (an ISO timestamp the server stored) on the campus clock, in the one format the chart page uses:
 * 'Sat, Sep 26 · 8:43 AM'. Exam dates are wall-clock values and use examTiming instead. Unreadable input comes back as is.
 * @param {string} value
 */
export function recordedTime(value) {
  if (!value) return "";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return String(value);
  const part = (/** @type {Intl.DateTimeFormatOptions} */ options) => new Intl.DateTimeFormat("en-US", { timeZone: CAMPUS_ZONE, ...options }).format(moment);
  return `${part({ weekday: "short", month: "short", day: "numeric" })} · ${part({ hour: "numeric", minute: "2-digit" })}`;
}

/**
 * What reseating everyone does to a confirmed chart (null when the chart is not confirmed): the response cards that
 * would be revoked and replaced by updated seat cards, and the print jobs whose seat numbers beside the stamps would
 * no longer match. Counts only; the server decides at the move.
 * @param {any} chart
 */
export function reseatImpact(chart) {
  if (!chart?.confirmed_at) return null;
  const seated = (chart.students || []).filter((/** @type {any} */ item) => item.seat_id && item.status !== "dr_testing");
  const cards = seated.filter((/** @type {any} */ item) => item.card).length;
  const markedJobs = (chart.print_batches || []).filter((/** @type {any} */ batch) => batch.seat_marks && !batch.superseded_at && batch.status !== "cancelled").length;
  return { seated: seated.length, cards, markedJobs };
}

/** The student filters of the chart page, in display order. */
export const STUDENT_FILTERS = [
  { value: "all", label: "All" },
  { value: "issue", label: "Card issues", cards: true },
  { value: "pending", label: "Pending", cards: true },
  { value: "update_pending", label: "Seat updates", cards: true },
  { value: "dr", label: "DR testing" },
  { value: "unseated", label: "No seat" },
];

/** @param {any} student @param {string} filter */
export function matchesFilter(student, filter) {
  if (!filter || filter === "all") return true;
  if (filter === "dr") return student.status === "dr_testing";
  if (filter === "unseated") return student.status === "unseated";
  return student.card?.state === filter;
}

/**
 * Filter chips with their counts. Card filters appear once any card exists; 'Seat updates' only while some wait.
 * @param {any} chart
 */
export function studentFilterOptions(chart) {
  const students = chart?.students || [];
  const hasCards = students.some((/** @type {any} */ item) => item.card);
  return STUDENT_FILTERS
    .map((option) => ({ ...option, count: students.filter((/** @type {any} */ item) => matchesFilter(item, option.value)).length }))
    .filter((option) => !option.cards || (hasCards && (option.value !== "update_pending" || option.count > 0)))
    .map(({ value, label, count }) => ({ value, label, count }));
}

/**
 * DR bookings in these sections linked to OTHER exams. Links the DR export created by itself (auto_created) may be this
 * exam under another name; the rest are ordinary bookings for later exams.
 * @param {any} chart
 */
export function drOthers(chart) {
  const others = chart?.dr?.others || [];
  return {
    auto: others.filter((/** @type {any} */ item) => item.auto_created),
    rest: others.filter((/** @type {any} */ item) => !item.auto_created),
  };
}

/**
 * The legend: Seated, Free, Broken and Blocked always; the other states only when a seat shows them.
 * @param {any} chart
 */
export function legendStates(chart) {
  const states = new Set((chart?.students || []).map((/** @type {any} */ item) => item.card?.state).filter(Boolean));
  const items = [["seated", "Seated"]];
  if (chart?.spacing?.crowded) items.push(["seated crowded", "Beside another student"]);
  items.push(["", "Free"], ["broken", "Broken"], ["blocked", "Blocked"]);
  if (states.has("complete")) items.push(["seated card-complete", "Response card completed"]);
  if (states.has("pending")) items.push(["seated card-pending", "Response card pending"]);
  if (states.has("issue")) items.push(["seated card-issue", "Response card issue"]);
  if (states.has("update_pending")) items.push(["seated card-update_pending", "Seat update not sent"]);
  return items;
}

/**
 * A chart's one-word state on the list: Held after its exam date, Confirmed once confirmed for printing, else Seated.
 * @param {any} chart
 * @param {string} [today]
 */
export function chartState(chart, today = localToday()) {
  if (examTiming(chart, today)?.held) return "Held";
  return chart?.confirmed_at ? "Confirmed" : "Seated";
}

/**
 * The seating list's exams: every chart of one assignment and term together, with the exam's campus date, whether its
 * rooms are confirmed, and the response-card and version totals the list rows carry. Read-only; counts only.
 * @param {any[]} charts GET /api/seating rows, newest first
 * @param {string} [today] YYYY-MM-DD
 */
export function chartGroups(charts, today = localToday()) {
  /** @type {Map<string, any>} */
  const groups = new Map();
  (charts || []).forEach((item) => {
    const key = `${item.assignment_id}|${item.term_id}`;
    if (!groups.has(key)) groups.set(key, { ...item, charts: [] });
    groups.get(key).charts.push(item);
  });
  return [...groups.values()].map((group) => {
    /** @type {any[]} */ const members = group.charts;
    const dated = members.filter((item) => examTiming(item, today)).sort((a, b) => String(a.exam_date).localeCompare(String(b.exam_date)));
    const exam = dated.length ? examTiming(dated[0], today) : null;
    const confirmed = members.filter((item) => item.confirmed_at);
    const modes = [...new Set(confirmed.map((item) => item.confirmed_mode))];
    const firstConfirmed = confirmed.map((item) => String(item.confirmed_at)).sort()[0] || "";
    const cards = { recipients: 0, responded: 0, flagged: 0 };
    members.forEach((item) => {
      cards.recipients += item.card_counts?.recipients || 0;
      cards.responded += item.card_counts?.responded || 0;
      cards.flagged += item.card_counts?.flagged || 0;
    });
    /** @type {Map<string, number>} */
    const versions = new Map();
    members.forEach((item) => Object.entries(item.version_totals || {}).forEach(([letter, count]) => versions.set(letter, (versions.get(letter) || 0) + Number(count || 0))));
    const sections = [...new Set(members.flatMap((item) => item.sections || []))].sort((a, b) => a - b);
    const free = members.map((item) => item.counts?.free_seats).filter((count) => count !== undefined && count !== null);
    return {
      // Archived (certified grades) only when every chart of the exam is: a chart made after the certification keeps it in view.
      ...group, archived: members.every((item) => item.archived) ? members[0].archived : null, exam, past: Boolean(exam?.held),
      confirmed: confirmed.length, mode: modes.length === 1 ? modes[0] : "", confirmedAt: firstConfirmed,
      flagged: members.filter((item) => item.check && !item.check.ok).length,
      seatUpdates: members.reduce((sum, item) => sum + (Number(item.seat_updates_pending) || 0), 0),
      seated: members.reduce((sum, item) => sum + (item.seated_count || 0), 0),
      cards, versions: [...versions].sort(([a], [b]) => a.localeCompare(b)),
      // The exam's tile: its rooms, the sections seated in any of them and the term's sections still without a room, and the
      // rooms' DR and free-seat counts together (a mix counts its DR students on one chart only; null when no room says).
      rooms: members.map((item) => item.room_name), sections,
      missing: (group.term_sections || []).filter((/** @type {number} */ number) => !sections.includes(number)),
      dr: members.reduce((sum, item) => sum + (Number(item.counts?.excluded_dr) || 0), 0),
      free: free.length ? free.reduce((sum, count) => sum + Number(count || 0), 0) : null,
    };
  });
}

/**
 * The page of one exam's rooms: '#seating/exam/<assignment>/<term>' (SEATING "One tile per exam").
 * @param {{assignment_id: string, term_id: string}} item a chart, a list row or an exam of chartGroups
 */
export function examHref(item) {
  return `#seating/exam/${encodeURIComponent(item.assignment_id)}/${encodeURIComponent(item.term_id)}`;
}

/** The exam an address names, or null for the list and for a chart's own page. @param {string} hash */
export function examRoute(hash) {
  const match = String(hash || "").replace(/^#/, "").split("?")[0].match(/^seating\/exam\/([^/]+)\/([^/]+)$/);
  if (!match) return null;
  try {
    return { assignment_id: decodeURIComponent(match[1]), term_id: decodeURIComponent(match[2]) };
  } catch {
    return { assignment_id: match[1], term_id: match[2] };
  }
}

/**
 * Whether an exam's tile opens its page of rooms: with one room and every section seated there is nothing to choose, and
 * the tile opens that room's chart itself. @param {any} group an exam of chartGroups
 */
export function hasRoomsPage(group) {
  return (group?.charts || []).length !== 1 || (group?.missing || []).length > 0;
}

/**
 * An exam's state on its tile: Held after its date, Confirmed once every room is, else how many rooms are.
 * @param {any} group an exam of chartGroups
 */
export function examState(group) {
  if (group?.past) return "Held";
  const rooms = (group?.charts || []).length;
  if (group?.confirmed && group.confirmed === rooms) return "Confirmed";
  return group?.confirmed ? `${group.confirmed} of ${rooms} confirmed` : "Not confirmed";
}

/**
 * Upcoming exams by date (undated ones after them, in their saved order), past exams most recent first, and archived exams
 * (grades certified) most recently certified first.
 * A past exam that still needs the instructor (a failed roster check, updated seat cards to send) stays in view, first;
 * with nothing else in view the most recent past exam stays in view too, so the list never opens on a folded section alone.
 * @param {any[]} groups from chartGroups
 */
export function orderGroups(groups) {
  // Exams whose grades are certified leave the list for Archived (a view; certified_archive.py): a flagged chart or unsent seat updates
  // no longer need the instructor once grades are certified (requirement, 2026-09-27); the charts themselves are unchanged.
  const archived = groups.filter((group) => group.archived)
    .sort((a, b) => String(b.archived.certified_at).localeCompare(String(a.archived.certified_at)));
  groups = groups.filter((group) => !archived.includes(group));
  const dated = groups.filter((group) => group.exam && !group.past).sort((a, b) => a.exam.iso.localeCompare(b.exam.iso));
  const undated = groups.filter((group) => !group.exam);
  const earlier = groups.filter((group) => group.past).sort((a, b) => b.exam.iso.localeCompare(a.exam.iso));
  const attention = earlier.filter((group) => group.flagged > 0 || group.seatUpdates > 0);
  const recent = !attention.length && !dated.length && !undated.length && earlier.length ? [earlier[0]] : [];
  const past = earlier.filter((group) => !attention.includes(group) && !recent.includes(group));
  return { upcoming: [...attention, ...dated, ...undated, ...recent], past, archived };
}

/**
 * A list row needs the instructor: its live roster check failed, or students moved after confirmation still wait for
 * their updated seat cards (the same count the sidebar badge shows). Never once its exam's grades are certified. @param {any} item GET /api/seating row
 */
export function needsAttention(item) {
  if (item?.archived) return false; // grades certified: resolved, whatever the chart's own flags say
  return Boolean((item?.check && !item.check.ok) || Number(item?.seat_updates_pending) > 0);
}

/** The list's one-line summary: '3 room charts · 1 needs attention'. @param {any[]} charts */
export function listSummary(charts) {
  const list = charts || [];
  if (!list.length) return "Place each section in a room; DR bookings stay separate.";
  const attention = list.filter(needsAttention).length;
  return [plural(list.length, "room chart"), attention ? `${attention.toLocaleString()} need${attention === 1 ? "s" : ""} attention` : ""].filter(Boolean).join(" · ");
}

/**
 * The create dialog's rooms: ready rooms by usable seats (most first), then rooms that need review, then rooms with no
 * assignable seat. Only ready rooms can be chosen. `used` is the ready rooms an earlier chart seated, the latest chart
 * first, each with that chart's exam title (`used_for`); `others` is the rest of `ready`, in its order.
 * @param {any[]} rooms GET /api/seating/catalog rooms
 * @param {any[]} [charts] GET /api/seating rows
 */
export function roomOptions(rooms, charts = []) {
  const list = rooms || [];
  const review = list.filter((room) => room.review_required?.length);
  const empty = list.filter((room) => !room.review_required?.length && !(room.usable_count > 0));
  const ready = list.filter((room) => !room.review_required?.length && room.usable_count > 0)
    .sort((a, b) => b.usable_count - a.usable_count || String(a.name).localeCompare(String(b.name)));
  const latest = new Map();
  (charts || []).forEach((chart) => {
    if (String(chart.created_at || "") >= String(latest.get(chart.room_id)?.created_at || "")) latest.set(chart.room_id, chart);
  });
  const used = ready.filter((room) => latest.has(room.id)).map((room) => ({ ...room, used_for: latest.get(room.id).title || "" }))
    .sort((a, b) => String(latest.get(b.id).created_at || "").localeCompare(String(latest.get(a.id).created_at || "")));
  return { ready, review, empty, used, others: ready.filter((room) => !latest.has(room.id)) };
}

/** A room's readiness for exam seating: needs review, has no assignable seat, or ready. @param {any} room */
export function roomStatus(room) {
  if (room?.review_required?.length) return "review";
  return room?.usable_count > 0 ? "ready" : "empty";
}

/** The building part of a room name ("HECC 207" → "HECC"); names without a number stand alone. @param {string} name */
export function buildingOf(name) {
  const match = String(name || "").trim().match(/^(.*?)\s+\S*\d\S*$/);
  return (match ? match[1] : String(name || "")).trim().toUpperCase();
}

/**
 * The seats a review reason names, as exact labels this room has (compared case-insensitively). Only three wordings name a
 * seat, and nothing is inferred from anything else:
 * - the importer's repeated seat, "Source cells H2, K2 repeat seat “G6”…": the seats it relabelled "G6 · H2" and "G6 · K2";
 * - the importer's unread note, "Cell O2 note “H” was not understood; seat F106 was imported without it": seat F106;
 * - any other text's "seat K5" / "seats K5, K6 and K7".
 * Spreadsheet cell references ("Cell O2", "Source cells H2, K2", "Source note at B4") are never seats, even when a seat
 * has the same letters and digits.
 * @param {string} reason
 * @param {{id: string, label: string}[]} seats
 */
export function seatsNamed(reason, seats) {
  let text = String(reason || "");
  /** @type {Set<string>} */
  const named = new Set();
  const key = (/** @type {string} */ label) => label.trim().toLocaleLowerCase();
  const CELL = String.raw`[A-Z]{1,3}\d{1,5}`;
  text = text.replace(new RegExp(String.raw`Source cells (${CELL}(?:, ${CELL})*) repeat seat “([^”]+)”`, "g"), (_, cells, label) => {
    String(cells).split(", ").forEach((cell) => named.add(key(`${label} · ${cell}`)));
    return " ";
  });
  text = text.replace(/\bseat (.+?) was imported without it\b/g, (_, label) => { named.add(key(String(label))); return " "; });
  text = text.replace(new RegExp(String.raw`\b(?:Source cells|Cells?|Source note at) ${CELL}(?:(?:, | and )${CELL})*`, "gi"), " ");
  const TOKEN = String.raw`[“"]?([^\s,;:!?“”"()]+?)[”"]?`;
  const list = new RegExp(String.raw`\bseats?\s+${TOKEN}((?:(?:,\s*|\s+and\s+)${TOKEN})*)(?=[\s,;:.!?)]|$)`, "gi");
  for (const match of text.matchAll(list)) {
    match[0].replace(/^\s*seats?\s+/i, "").split(/,\s*|\s+and\s+/i)
      .forEach((part) => named.add(key(part.replace(/^[“"]|[”".]+$/g, ""))));
  }
  return (seats || []).filter((seat) => named.has(key(String(seat.label || ""))));
}
