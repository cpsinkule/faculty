// @ts-check
// TOTAL ABSENCES (absences/totals.py, ABSENCES "TOTAL ABSENCES"; requirement, 2026-10-31): the days a student missed, each counted
// once, and Totals by student on the Absences page. Pure markup, so it is checked without a browser (tests/test_absence_totals.mjs).

const DAY_TONES = { pending: "warning", excused: "success", not_excused: "neutral", unrecorded: "neutral" };
// The bar's blocks in the order they are drawn, and each column's words: what counts against the student first, what waits on the
// instructor next, what is settled last.
const TOP = 5;
const SPLIT = /** @type {const} */ ([["not_excused", "not excused"], ["pending", "waiting for your decision"], ["excused", "excused"]]);

/** @param {number} n @param {string} word */
export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "2 excused · 1 not excused · 1 waiting for your decision", the parts that are not 0. @param {any} total */
export function splitText(total) {
  return [total.excused ? `${total.excused} excused` : "", total.not_excused ? `${total.not_excused} not excused` : "",
    total.pending ? `${total.pending} waiting for your decision` : ""].filter(Boolean).join(" · ");
}

/** "1 more day recorded ahead, not counted yet: Mon, Nov 2." @param {any} total */
export function aheadText(total) {
  return `${plural(total.ahead.length, "more day")} recorded ahead, not counted yet: ${total.ahead.map((/** @type {any} */ row) => row.day).join(", ")}.`;
}

/** The days a student missed, each with its decision and what shows it, and the days recorded ahead.
 * @param {any} total @param {(value: unknown) => string} e */
export function daysMarkup(total, e) {
  const day = (/** @type {any} */ row) => `<li><span class="absence-total-day">${e(row.day)}</span> <span class="chip" data-tone="${DAY_TONES[/** @type {keyof typeof DAY_TONES} */ (row.state)] || "neutral"}">${e(row.state_label)}</span>
      <small>${e(row.sources.map((/** @type {any} */ source) => source.label).join(" · "))}</small></li>`;
  return `${total.days.length ? `<ul class="absence-total-list">${total.days.map(day).join("")}</ul>` : ""}${total.ahead?.length ? `<p class="field-hint">${e(aheadText(total))}</p>` : ""}`;
}

/** A DAY IS A BLOCK: a student's days missed as one block a day, drawn to the same scale on every row of the page so the rows
 * compare at a glance: not excused in ink, waiting for your decision in the needs-you orange, excused pale. A picture of the
 * figures beside it, so it is hidden from a screen reader. Drawn as SVG attributes: the page's policy allows no inline style.
 * @param {any} row @param {number} scale the most days any shown student missed */
export function barMarkup(row, scale) {
  let at = 0;
  const blocks = SPLIT.map(([state]) => Array.from({ length: Number(row[state]) || 0 }, () => `<rect class="absence-bar-${state}" x="${at++}.07" y="0" width="0.86" height="1"/>`).join("")).join("");
  return `<svg class="absence-totals-bar" viewBox="0 0 ${Math.max(scale, at, 1)} 1" preserveAspectRatio="none" aria-hidden="true" focusable="false">${blocks}</svg>`;
}

/**
 * TOTAL ABSENCES › ON THE ABSENCES PAGE (requirement, 2026-10-31): one group a section, most days first; each row opens to the days missed, as the
 * student's card lists them. The course filter and Find a student narrow it.
 * A TABLE, NOT SENTENCES (requirement, 2026-11-12): the
 * words are said once, in each section's column headings, and a row is a name, its figures and its bar; the students with no
 * absence fold under their section (open while Find a student narrows the list, so a student searched for is never hidden).
 * THE TOP FIVE (requirement, 2026-11-12): a section shows its five students with the most days missed; the others who missed a class
 * fold under them ("53 more students", "5 absences or fewer"), above the fold of those with none. While Find a student narrows
 * the list every match is a row, so nobody searched for is behind a fold.
 * @param {any} totals @param {{course: string, search: string}} filters
 * @param {{e: (value: unknown) => string, emptyMarkup: (options: any) => string}} deps
 */
export function totalsMarkup(totals, { course, search }, { e, emptyMarkup }) {
  const words = String(search || "").toLowerCase().split(/\s+/).filter(Boolean);
  const sections = (totals.sections || []).filter((/** @type {any} */ section) => !course || section.code === course)
    .map((/** @type {any} */ section) => ({ ...section, shown: section.students.filter((/** @type {any} */ student) =>
      words.every((word) => `${student.name} ${section.code} ${section.section}`.toLowerCase().includes(word))) }))
    .filter((/** @type {any} */ section) => section.shown.length);
  if (!sections.length) {
    return words.length ? emptyMarkup({ icon: "search", kind: "filtered", title: "No students match", body: "Change the search to see every student’s total.",
      action: { label: "Clear filters", data: { absencesClear: "" } } })
      : emptyMarkup({ icon: "calendar", title: "No students yet", body: "Import a section’s roster to count its students’ absences." });
  }
  const everyone = sections.flatMap((/** @type {any} */ section) => section.shown);
  const scale = Math.max(10, ...everyone.map((/** @type {any} */ row) => row.total));
  // The Waiting column is there only while a shown student has a day waiting for a decision.
  const columns = SPLIT.filter(([state]) => state !== "pending" || everyone.some((/** @type {any} */ row) => row.pending));
  const heads = { not_excused: "Not excused", pending: "Waiting", excused: "Excused" };
  const name = (/** @type {any} */ row) => `${e(row.name || "Student")}${row.dropped ? " <small>Dropped</small>" : ""}`;
  const figure = (/** @type {any} */ row, /** @type {readonly [string, string]} */ [state, label]) => row[state]
    ? `<span class="absence-totals-figure" data-state="${state}">${e(row[state])}<span class="sr-only"> ${label}</span></span>`
    : `<span class="absence-totals-figure" data-zero aria-hidden="true">–</span>`;
  const student = (/** @type {any} */ row) => `<li><details class="absence-totals-student"><summary><span class="absence-totals-name">${name(row)}</span>
        <span class="absence-totals-count">${e(row.total)}<span class="sr-only"> ${row.total === 1 ? "absence" : "absences"}</span></span>${barMarkup(row, scale)}${columns.map((column) => figure(row, column)).join("")}</summary>
        <div class="absence-totals-days">${daysMarkup(row, e)}</div></details></li>`;
  const none = (/** @type {any} */ row) => `<li>${name(row)}${row.ahead?.length ? `<small class="absence-totals-ahead">${e(aheadText(row))}</small>` : ""}</li>`;
  const terms = new Set(sections.map((/** @type {any} */ section) => section.term_id));
  const courses = new Set(sections.map((/** @type {any} */ section) => section.code));
  return `<div class="absence-totals${columns.length === 3 ? " has-waiting" : ""}">${sections.map((/** @type {any} */ section) => {
    const missed = section.shown.filter((/** @type {any} */ row) => row.total);
    const rest = section.shown.filter((/** @type {any} */ row) => !row.total);
    // THE TOP FIVE: the rest of the section's students who missed a class fold under them; a search shows every match.
    const top = words.length ? missed : missed.slice(0, TOP);
    const more = missed.slice(top.length);
    return `<section class="absence-totals-section" aria-label="${e(`Section ${section.section}`)}"><div class="absence-totals-head"><h3>${e(`Section ${section.section}`)}</h3>
        <small>${e([terms.size > 1 || course === "" && courses.size > 1 ? `${section.code} · ${section.term_label}` : "",
          `${missed.length} of ${plural(section.shown.length, "student")} missed a class`, `${plural(missed.reduce((/** @type {number} */ sum, /** @type {any} */ row) => sum + row.total, 0), "day")} missed`].filter(Boolean).join(" · "))}</small></div>
        ${missed.length ? `<div class="absence-totals-columns" aria-hidden="true"><span>Student</span><span>Absences</span><span></span>${columns.map(([state]) => `<span data-state="${state}">${heads[state]}</span>`).join("")}</div>
        <ul class="absence-totals-list">${top.map(student).join("")}</ul>` : ""}
        ${more.length ? `<details class="disclosure absence-totals-more"><summary><span>${e(plural(more.length, "more student"))}</span><span class="disclosure-hint">${e(more[0].total === 1 ? "1 absence each" : `${more[0].total} absences or fewer`)}</span></summary>
          <div class="disclosure-body"><ul class="absence-totals-list">${more.map(student).join("")}</ul></div></details>` : ""}
        ${rest.length ? `<details class="disclosure absence-totals-rest"${words.length ? " open" : ""}><summary><span>No absences</span><span class="disclosure-hint">${e(plural(rest.length, "student"))}</span></summary>
          <div class="disclosure-body"><ul class="absence-totals-none">${rest.map(none).join("")}</ul></div></details>` : ""}</section>`;
  }).join("")}</div>`;
}
