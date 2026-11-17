// @ts-check
/**
 * The pure parts of a section's Class schedule page (class-schedule.js; SCHEDULING "CLASS SCHEDULE"): reading a class day's topics
 * as typed, grouping the days into weeks, and a planned row's values between its form and what the server keeps. No DOM.
 */

const NUMBER = /^\d{1,2}\.\d{1,2}$/;
const RANGE = /^(\d{1,2})\.(\d{1,2})\s*[-–]\s*(?:\1\.)?(\d{1,2})$/;
export const MAX_NOTES = 8;
export const KINDS = /** @type {Array<[string, string]>} */ ([["quiz", "Quiz"], ["exam", "Exam"], ["final", "Final exam"], ["homework", "Homework due"], ["other", "Something else"]]);
export const VALUES = ["date", "time", "place", "due_date"];

/**
 * A class day's topics as typed: the notes section numbers it starts with ("2.3 2.4", "2.3, 2.4", "2.3-2.5") and, from the first
 * word that is not one, a note kept as written. So "2.3 2.4 review" is two notes sections and the note "review".
 * @param {string} text @returns {{notes: string[], note: string}}
 */
export function parseTopics(text) {
  const words = String(text || "").trim().split(/\s+/).filter(Boolean);
  /** @type {string[]} */ const notes = [];
  let index = 0;
  for (; index < words.length; index += 1) {
    const word = words[index].replace(/[,;]+$/, "");
    const range = RANGE.exec(word);
    const found = NUMBER.test(word) ? [word] : range && Number(range[3]) >= Number(range[2]) && Number(range[3]) - Number(range[2]) < MAX_NOTES
      ? Array.from({ length: Number(range[3]) - Number(range[2]) + 1 }, (_, n) => `${range[1]}.${Number(range[2]) + n}`) : null;
    if (!found || notes.length + found.filter((number) => !notes.includes(number)).length > MAX_NOTES) break;
    for (const number of found) if (!notes.includes(number)) notes.push(number);
  }
  return { notes, note: words.slice(index).join(" ") };
}

/** What `parseTopics` reads back the same. @param {{notes?: Array<string|{number: string}>, note?: string}|null|undefined} topics */
export function formatTopics(topics) {
  if (!topics) return "";
  const numbers = (topics.notes || []).map((entry) => (typeof entry === "string" ? entry : entry.number));
  return [...numbers, topics.note || ""].filter(Boolean).join(" ");
}

/**
 * The term's stored entry for one day after one section's topics are typed: the term's own (`only` false, and this section's
 * own entry is dropped) or this section's alone. null when nothing is left to keep.
 * @param {any} stored the plan's entry for the day, if any
 * @param {string} section @param {{notes: string[], note: string}} typed @param {boolean} only
 */
export function topicEntry(stored, section, typed, only) {
  const entry = { notes: [...(stored?.notes || [])], note: stored?.note || "", sections: { ...(stored?.sections || {}) } };
  if (only) entry.sections[section] = { notes: typed.notes, note: typed.note };
  else {
    delete entry.sections[section];
    entry.notes = typed.notes; entry.note = typed.note;
  }
  for (const [key, own] of Object.entries(entry.sections)) if (!own.notes.length && !own.note) delete entry.sections[key];
  return entry.notes.length || entry.note || Object.keys(entry.sections).length ? entry : null;
}

const day = (/** @type {string} */ iso) => new Date(`${iso}T00:00:00Z`);
const iso = (/** @type {Date} */ date) => date.toISOString().slice(0, 10);

/** The Monday of a date's week (YYYY-MM-DD). @param {string} date */
export function mondayOf(date) {
  const found = day(date);
  found.setUTCDate(found.getUTCDate() - ((found.getUTCDay() + 6) % 7));
  return iso(found);
}

/** The term week a date falls in (1 = the week of the first day of classes); 0 before it or without term dates. @param {string} date @param {string} firstDay */
export function weekNumber(date, firstDay) {
  if (!firstDay) return 0;
  const weeks = Math.floor((day(mondayOf(date)).getTime() - day(mondayOf(firstDay)).getTime()) / (7 * 86400000));
  return weeks < 0 ? 0 : weeks + 1;
}

/**
 * The schedule's days as weeks, in three runs: `earlier` (weeks wholly before today's, folded on the page), `current` (from today's
 * week to the last day of classes) and `after` (dates after classes end: finals). Before the term starts nothing is earlier.
 * @param {any[]} days @param {string} today @param {string} firstDay @param {string} lastDay
 */
export function splitWeeks(days, today, firstDay, lastDay) {
  /** @type {{earlier: any[], current: any[], after: any[]}} */ const runs = { earlier: [], current: [], after: [] };
  const thisWeek = mondayOf(today);
  for (const entry of days) {
    const monday = mondayOf(entry.date);
    const run = lastDay && entry.date > lastDay ? runs.after : monday < thisWeek ? runs.earlier : runs.current;
    let week = run[run.length - 1];
    if (!week || week.monday !== monday) { week = { monday, number: weekNumber(entry.date, firstDay), days: [] }; run.push(week); }
    week.days.push(entry);
  }
  if (!runs.current.length && !runs.after.length) { runs.current = runs.earlier; runs.earlier = []; }
  return runs;
}

/**
 * A stored planned row as its form shows it: every section's values spelled out (`rows`), and whether any differ.
 * @param {any} row @param {Array<{id: string, number: number|string}>} sections
 */
export function unpackRow(row, sections) {
  const shown = sections.filter((section) => !(row.sections || []).length || row.sections.includes(section.id));
  const rows = shown.map((section) => {
    const own = (row.section_values || {})[section.id] || {};
    return { id: section.id, number: section.number, date: own.date || row.date || "", time: own.time || row.time || "", place: own.place || row.place || "" };
  });
  return { rows, differ: Object.keys(row.section_values || {}).some((key) => VALUES.some((field) => (row.section_values[key] || {})[field])) };
}

/**
 * What the server keeps of a form's rows: the commonest date, time and place as the row's own, and under each section only what
 * differs from them.
 * @param {Array<Record<string, any>>} rows each {id, date, time, place}
 */
export function packRow(rows) {
  /** @type {Record<string, string>} */ const shared = {};
  for (const field of ["date", "time", "place"]) {
    /** @type {Map<string, number>} */ const counts = new Map();
    for (const row of rows) counts.set(row[field] || "", (counts.get(row[field] || "") || 0) + 1);
    // The value most sections share; a tie goes to the first section's, and a value only one of several holds is its own.
    const best = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
    shared[field] = best && (best[1] > 1 || rows.length === 1) ? best[0] : "";
  }
  /** @type {Record<string, Record<string, string>>} */ const sectionValues = {};
  for (const row of rows) {
    /** @type {Record<string, string>} */ const own = {};
    for (const field of ["date", "time", "place"]) if ((row[field] || "") && row[field] !== shared[field]) own[field] = row[field];
    if (Object.keys(own).length) sectionValues[row.id] = own;
  }
  return { ...shared, section_values: sectionValues };
}

/**
 * One line saying what a save touches: "Every section · Fri, Oct 23", "4 sections · same day, 4 rooms", "4 sections · 4 dates",
 * "Section 507 only · Fri, Oct 23".
 * @param {Array<Record<string, any>>} rows each {number, date, time, place} @param {number} total sections of the term
 * @param {(iso: string) => string} label
 */
export function scopeLine(rows, total, label) {
  if (!rows.length) return "Choose at least one section";
  const who = rows.length === total ? (total === 1 ? `Section ${rows[0].number}` : "Every section") : rows.length === 1 ? `Section ${rows[0].number} only` : `${rows.length} sections`;
  const count = (/** @type {string} */ field) => new Set(rows.map((row) => row[field] || "")).size;
  const dates = count("date"), places = count("place"), times = count("time");
  if (dates > 1) return `${rows.length} sections · ${dates} dates`;
  const when = rows[0].date ? label(rows[0].date) : "no date yet";
  if (places > 1) return `${rows.length} sections · ${rows[0].date ? "same day" : "no date yet"}, ${places} rooms`;
  if (times > 1) return `${rows.length} sections · ${rows[0].date ? "same day" : "no date yet"}, ${times} times`;
  return `${who} · ${when}`;
}

/**
 * The open planned row a title most likely names (the same words, case and spacing aside), for a form about to create that work.
 * @param {string} title @param {Array<{item: string, title: string}>} rows
 */
export function matchRow(title, rows) {
  const fold = (/** @type {string} */ text) => String(text || "").toLowerCase().replace(/\s+/g, " ").trim();
  const wanted = fold(title);
  if (!wanted) return null;
  const same = rows.filter((row) => fold(row.title) === wanted);
  return same.length === 1 ? same[0] : null;
}

/**
 * A planned row's date for the sections a form is about: the one they share, or "" when they differ (the form then fills nothing).
 * @param {any} row @param {string[]} sections the form's sections ([] for every section of the row) @param {string} [field]
 */
export function dateFor(row, sections, field = "date") {
  const named = sections.length ? sections : Object.keys(row.section_values || {}).concat("");
  const values = new Set(named.map((section) => ((row.section_values || {})[section] || {})[field] || row[field] || ""));
  return values.size === 1 ? [...values][0] : "";
}
