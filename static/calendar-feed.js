// @ts-check
// The calendar feed as the instructor reads it: event types, one display clock, and events that belong together (one exam's
// rooms, one exam's DR sections) gathered into one group. Shared by the Calendar and Home's Coming up. Pure: display only.
//
// Dates and times are campus wall-clock values (DR bookings keep their center's local time). UTC is used only for date
// arithmetic; no event time is converted through the browser's time zone. The stored time text is never changed: a
// recognized clock is only shown in one format, and anything else is shown as written.

/** The campus zone, once for the browser (scheduling/clock.py ZONE_NAME holds the server to the same; seating-state.js imports it). */
export const CAMPUS_ZONE = "America/Chicago";

/** One event's kind, for its tag: exams, room charts, DR bookings, the instructor's own events and deadlines. */
export const TYPE_LABELS = /** @type {Record<string, string>} */ ({ exam: "Exam", seating: "Seating", dr: "DR exam", custom: "Your event", deadline: "Deadline", google: "Google", schedule: "Term schedule" });
/** The Event type filter's options (values unchanged; "custom" is the instructor's own events, "google" their Google Calendar's). */
export const TYPE_FILTERS = /** @type {Record<string, string>} */ ({ exam: "Exam", seating: "Seating", dr: "DR exam", custom: "Your events", deadline: "Deadline", google: "Google Calendar", schedule: "Term schedule" });

const pad = (/** @type {number} */ value) => String(value).padStart(2, "0");

/** Today's date on campus (YYYY-MM-DD), whatever the browser's time zone. @param {Date} [now] */
export function campusToday(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: CAMPUS_ZONE, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(now).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

/** @param {string} iso YYYY-MM-DD @param {number} count */
export function addDays(iso, count) {
  const date = new Date(`${iso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + count);
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

/** A deadline is a custom event of that category or a source's own deadline; other sources keep their type. @param {any} event */
export const eventType = (event) => (event?.category === "deadline" ? "deadline" : event?.source === "office_hours" ? "custom" : event?.source || "custom");

/**
 * Minutes after midnight for a recognized clock ("7:05 PM", "7 pm", "19:05"), else null. Descriptive text stays text.
 * @param {unknown} text
 */
export function clockMinutes(text) {
  const value = String(text ?? "").trim();
  const twelve = value.match(/^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?$/i);
  if (twelve && +twelve[1] >= 1 && +twelve[1] <= 12 && +(twelve[2] || 0) < 60) return (+twelve[1] % 12 + (twelve[3].toLowerCase() === "p" ? 12 : 0)) * 60 + +(twelve[2] || 0);
  const clock = value.match(/^(\d{1,2}):(\d{2})$/);
  return clock && +clock[1] < 24 && +clock[2] < 60 ? +clock[1] * 60 + +clock[2] : null;
}

/** Sort key: all-day first, then clock times, then descriptive times. @param {any} event */
export function clockOrder(event) {
  if (event?.all_day) return -1;
  return clockMinutes(event?.time) ?? Infinity;
}

/** @param {number} minutes @param {boolean} [meridiem] */
function clockText(minutes, meridiem = true) {
  const hours = Math.floor(minutes / 60), hour = hours % 12 || 12;
  return `${hour}:${pad(minutes % 60)}${meridiem ? ` ${hours < 12 ? "AM" : "PM"}` : ""}`;
}

/** One display format for a recognized clock ("19:05" and "7:05PM" are both "7:05 PM"); other text as written. @param {unknown} text */
export function displayClock(text) {
  const minutes = clockMinutes(text);
  return minutes === null ? String(text ?? "") : clockText(minutes);
}

/**
 * A time span in the display format: "7:05–8:58 PM", "11:30 AM–1:00 PM", or the stored text when it is not a clock.
 * @param {unknown} start @param {unknown} [end]
 */
export function displaySpan(start, end = "") {
  const from = clockMinutes(start), to = clockMinutes(end);
  if (from === null) return [start, end].filter(Boolean).map(String).join("–");
  if (to === null) return end ? `${clockText(from)}–${String(end)}` : clockText(from);
  return (from < 720) === (to < 720) ? `${clockText(from, false)}–${clockText(to)}` : `${clockText(from)}–${clockText(to)}`;
}

/** An event's time as shown on screen ("All day", "7:05–8:58 PM", "Time not set"). @param {any} event */
export function displayTime(event) {
  if ((event?.source === "custom" || event?.source === "google" || event?.category === "deadline") && event?.all_day) return "All day";
  return event?.time ? displaySpan(event.time, event.end_time) : "Time not set";
}

/** @param {number} count @param {string} word @param {string} [many] */
const plural = (count, word, many = `${word}s`) => `${count.toLocaleString()} ${count === 1 ? word : many}`;

/**
 * Events that belong together: the same course, date, title and type (one exam's room charts, one exam's DR sections).
 * Groups keep the order of their first event. Each group counts its rooms, sections and DR bookings and spans its times.
 * @param {any[]} events
 */
export function groupEvents(events) {
  /** @type {Map<string, any>} */
  const groups = new Map();
  for (const event of events || []) {
    const type = eventType(event);
    const key = event.source === "office_hours" ? JSON.stringify(["office_hours", event.id]) : JSON.stringify([event.date, event.definition_id || "", type, String(event.title || "").trim().toLocaleLowerCase()]);
    if (!groups.has(key)) groups.set(key, { key, date: event.date, title: event.title, type, code: event.code || "", events: [] });
    const group = groups.get(key);
    group.events.push(event);
  }
  return [...groups.values()].map((group) => {
    const rooms = new Set(), sections = new Set(), hrefs = new Set(), hubs = new Set();
    let bookings = 0, first = Infinity, last = -Infinity, firstText = "", lastText = "", clocks = true;
    for (const event of group.events) {
      if (event.location) rooms.add(String(event.location));
      for (const section of event.sections || []) sections.add(String(section));
      bookings += Number(event.booking_count || 0);
      if (typeof event.href === "string" && event.href.startsWith("#") && event.href !== "#calendar") hrefs.add(event.href);
      if (typeof event.exam_href === "string" && event.exam_href.startsWith("#exams")) hubs.add(event.exam_href);
      const start = clockMinutes(event.time), end = clockMinutes(event.end_time) ?? start;
      if (start === null) { clocks = false; continue; }
      if (start < first) { first = start; firstText = event.time; }
      if ((end ?? start) > last) { last = end ?? start; lastText = event.end_time || event.time; }
    }
    const single = group.events.length === 1 ? group.events[0] : null;
    const time = single ? displayTime(single) : clocks && Number.isFinite(first) ? (first === last ? displayClock(firstText) : displaySpan(firstText, lastText)) : "";
    return {
      ...group,
      count: group.events.length,
      rooms: rooms.size,
      sections: [...sections].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })),
      bookings,
      time,
      dr_time: group.events.some((/** @type {any} */ event) => event.time_basis === "dr_center"),
      href: hrefs.size === 1 ? [...hrefs][0] : "",
      exam_href: hubs.size === 1 ? [...hubs][0] : "",
    };
  });
}

/**
 * What a group adds up to, in words: "3 rooms", "4 sections", "8 bookings". Its time is separate.
 * @param {any} group from groupEvents
 */
export function groupFacts(group) {
  const facts = [];
  if (group.type === "dr") {
    if (group.sections.length > 1) facts.push(plural(group.sections.length, "section"));
    else if (group.sections.length === 1) facts.push(`Section ${group.sections[0]}`);
    if (group.bookings) facts.push(plural(group.bookings, "booking"));
  } else {
    if (group.rooms > 1) facts.push(plural(group.rooms, "room"));
    else if (group.rooms === 1) facts.push(group.events.find((/** @type {any} */ event) => event.location)?.location || "");
    if (group.sections.length > 1) facts.push(plural(group.sections.length, "section"));
    else if (group.sections.length === 1) facts.push(`Section ${group.sections[0]}`);
    if (group.count > 1 && !group.rooms && !group.sections.length) facts.push(plural(group.count, "event"));
  }
  return facts.filter(Boolean);
}

/**
 * The short label a month cell shows for a group: "Test 1 · 3 rooms · 7:05 PM", "Test 1 · DR · 10".
 * @param {any} group
 */
export function chipLabel(group) {
  return [group.title, ...chipFacts(group)].join(" · ");
}

/**
 * What a group chip adds to its title, shown on the chip's second line: ["3 rooms", "7:05 PM"], ["DR", "10"].
 * @param {any} group
 * @returns {string[]}
 */
export function chipFacts(group) {
  if (group.type === "dr") return ["DR", group.bookings ? group.bookings.toLocaleString() : ""].filter(Boolean);
  const start = clockMinutes(group.events[0]?.time);
  const time = group.events.every((/** @type {any} */ event) => clockMinutes(event.time) === start) && start !== null ? clockText(start) : "";
  return [group.rooms > 1 ? plural(group.rooms, "room") : "", time].filter(Boolean);
}

/**
 * The next `count` groups from `today` on (a multi-day event under way counts from today), in date and time order.
 * @param {any[]} events a feed that starts on or before `today`
 * @param {string} today YYYY-MM-DD (campus)
 * @param {number} [count]
 */
export function nextEvents(events, today, count = 5) {
  const upcoming = (events || [])
    .filter((event) => (event.end_date || event.date) >= today)
    .map((event) => (event.date < today ? { ...event, date: today } : event))
    .sort((a, b) => a.date.localeCompare(b.date) || clockOrder(a) - clockOrder(b) || String(a.title).localeCompare(String(b.title)));
  return groupEvents(upcoming).slice(0, count);
}

/** The calendar day's address: `#calendar?month=YYYY-MM&view=month&date=YYYY-MM-DD`. @param {string} date */
export function calendarHref(date) {
  return `#calendar?${new URLSearchParams({ month: date.slice(0, 7), view: "month", date })}`;
}
