// Certification and Export: the pure pieces of the analysis (requirement, 2026-09-26).
// Every number comes from the server's read-only view (GET /api/grading/assignments/<aid>/certification); these functions only
// order, filter and draw it. Nothing here decides, finalizes or sends a grade, and nothing here holds a UIN.

const EMPTY = "–";
const num = value => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
export const fmt = (value, digits = 2) => { const n = num(value); return n === null ? EMPTY : n.toLocaleString(undefined, {maximumFractionDigits: digits}); };
export const pct = value => { const n = num(value); return n === null ? EMPTY : `${Math.round(n)}%`; };
const count = (n, one, many = `${one}s`) => `${Number(n || 0).toLocaleString()} ${Number(n) === 1 ? one : many}`;
const PARTS = {frq: "Free response", bubble: "Multiple choice", exams: "Exams page"};
export const partName = part => PARTS[part] || part;

// ------------------------------------------------------------------------------------------------------------------------------
// Checks: Block (fix before certifying), Confirm (ticked in the Certify dialog), Info (shown, never required).

export function checkGroups(checks = []) {
  const groups = {block: [], confirm: [], info: []};
  for (const check of checks) (groups[check.level] || groups.info).push(check);
  return groups;
}

// The one-line status band: what stands between these grades and a current certification. Derived from the view only.
// `termId`: the term, for a certified column's link; `next`: the pipeline's current step when it is not this one (grading-guide.js).
export function bandModel(view, day = "", {termId = "", next = null} = {}) {
  const cert = view.certification || {state: "none"}, groups = checkGroups(view.checks);
  const blocking = groups.block.length, missing = Number(view.counts?.missing || 0);
  if (blocking) return {tone: "warning", title: `${count(blocking, "thing")} must be finished before certifying`,
    body: groups.block.slice(0, 3).map(c => c.title).join(" · "), action: groups.block[0]?.action?.href ? {label: groups.block[0].action.label, href: groups.block[0].action.href} : null};
  // The header's lead says what the gradebook keeps meanwhile (missed:certify[1]); the band says what changed.
  if (cert.state === "changed") return {tone: "warning", title: `Grades changed since the certification${day ? ` of ${day}` : ""}`,
    body: `What changed: ${(cert.changed || []).map(changedWords).join(", ") || "the grades"}.`, action: null};
  if (cert.state === "current") {
    const book = cert.gradebook || {};
    // GRADESCOPE "AFTER CERTIFYING": once Faculty sends and publishes by itself, publishing is no longer only the instructor's.
    const faculty = (view.gradescope?.after || []).some(run => run.state === "working" || (run.published || []).length || (run.publishing || []).length);
    return {tone: "success", title: `Certified${day ? ` ${day}` : ""}`,
      body: `${book.title ? `Recorded in the gradebook column “${book.title}”.` : "Recorded in the term gradebook."} ${faculty
        ? "Students see these grades on Gradescope where they are published (what Faculty did there is said below), or in Canvas once you send them from the gradebook."
        : "Students see these grades only after you publish them on Gradescope (below) or send them to Canvas from the gradebook."}`,
      action: termId && book.item_id ? {label: "Open the gradebook column", href: `#gradebook/${encodeURIComponent(termId)}?item=${encodeURIComponent(book.item_id)}`} : null};
  }
  // Nobody graded yet (certify-7): certifying now would only record zeros, so the band says so and names the step that grades.
  if (noGradesYet(view)) {
    const participants = Number(view.counts?.participants || 0);
    // While the assignment waits for its papers, the next thing is the scan, as the header's primary says (Upload scans), not "Grade".
    const nextWords = !next ? "" : next.waiting && next.title ? `${next.title.charAt(0).toLowerCase()}${next.title.slice(1)}` : next.label;
    return {tone: "warning", title: "No student has a grade yet",
      body: `Certifying now would record 0 for ${missing === participants ? `all ${count(participants, "student")}` : count(missing, "student")}.${nextWords ? ` Next: ${nextWords}.` : ""}`, action: null};
  }
  const confirm = groups.confirm.length;
  // ZEROS ARE THE CONSEQUENCE (usability audit, 2026-10-04: 28 zeros sat in the body of a calm "Nothing blocks certification", and
  // the other item to confirm only on the closed Details fold): the title says what certifying records, in the warning tone, and
  // every other confirmation is counted in the same line. Nothing here blocks; Certify grades… asks for each one.
  const others = groups.confirm.filter(c => !["zero_no_work", "zero_missing_part"].includes(c.id)).length;
  const more = others ? ` ${count(others, "other item asks", "other items ask")} for your confirmation too.` : "";
  if (missing) return {tone: "warning", title: `Certifying records 0 for ${count(missing, "student")}`,
    body: `${zeroWords(view.students, missing)}: you confirm each 0 when you certify.${more}`, action: null};
  return {tone: "info", title: confirm ? `${count(confirm, "item")} to confirm when you certify` : "Nothing blocks certification",
    body: confirm ? "Nothing blocks certification. Certify grades… asks for each." : "Every student has a grade.", action: null};
}

// Not certified, and no participant has any grade at all (none complete, in progress or with one part graded).
export function noGradesYet(view) {
  const c = view?.counts || {};
  return (view?.certification?.state || "none") === "none" && Number(c.participants || 0) > 0 && !Number(c.complete || 0) && !Number(c.in_progress || 0)
    && !(view.students || []).some(student => student.status === "missing_part");
}

// A check's title with its count, unless the title already starts with it or the count says nothing (certify-13).
export const countedTitle = check => (/^\d/.test(check?.title || "") || check?.id === "gradebook_column" || !Number(check?.count))
  ? String(check?.title || "") : `${check.title} (${Number(check.count).toLocaleString()})`;

// Who will receive a 0: students with no grade, and students with a part missing (that part counts 0), each said as such.
function zeroWords(students, missing) {
  const none = (students || []).filter(s => s.status === "missing").length, part = (students || []).filter(s => s.status === "missing_part").length;
  if (none + part !== missing) return `${count(missing, "student has", "students have")} no grade or a missing part`;
  return [none ? `${count(none, "student has", "students have")} no grade` : "", part ? `${count(part, "student is", "students are")} missing a part` : ""].filter(Boolean).join(" and ");
}

// A check lists at most this many rows (grading/certification_rules.ROWS). Its count may count something else than its rows (grades,
// or grades and papers, over rows of questions or students), so only a list cut at the cap is said to be a part of the whole.
export const ROWS_LISTED = 400;
export const rowsCut = check => (check?.rows || []).length >= ROWS_LISTED && Number(check?.count || 0) > (check?.rows || []).length;

// What certifying changes besides the gradebook's values (certify-10): said in the Certify dialog and in the page's Details. Certifying
// folds the assignment into "Archived" on the other pages (certified_archive.py), a view that deletes nothing.
export const CERTIFY_CHANGES = ["Records these values in the gradebook column you chose. Nobody is emailed.",
  "With “Send to Gradescope and publish the grades” ticked in the Certify dialog, Faculty then sends what is missing to Gradescope and publishes each section that is fully graded there. Without it, nothing goes to Gradescope or is published by itself.",
  "Moves this assignment’s scans, seating charts, print jobs, Response Card issues, grading card and Exams page row into the Archived section of their pages. Its flagged seating charts, Response Card issues and failed print jobs no longer ask for your attention. Nothing is deleted, and every record still opens.",
  "A later change to a grade, a paper or the roster brings it back here as Certify again."];

const CHANGED = {grades: "grades", bubble_sheets: "bubble sheets", students: "students", dr_date_passed: "a DR exam date passed",
  makeup_ended: "a makeup is no longer owed", new_work: "new papers or work"};
export const changedWords = key => CHANGED[key] || String(key || "").replaceAll("_", " ");

// ------------------------------------------------------------------------------------------------------------------------------
// Distribution: ten bars of percent of possible, drawn as SVG rectangles (attributes, never an inline style: CSP).

export function histogramModel(stats) {
  const bins = Array.isArray(stats?.histogram) ? stats.histogram : [];
  const most = Math.max(0, ...bins.map(b => Number(b.count || 0)));
  return bins.map(b => ({from: Number(b.from), to: Number(b.to), count: Number(b.count || 0), share: most ? Number(b.count || 0) / most : 0,
    label: `${fmt(b.from, 0)}–${fmt(b.to, 0)}%`}));
}

export function histogramMarkup(stats, h, label = "Distribution of totals") {
  const bars = histogramModel(stats);
  if (!bars.length || !stats?.n) return `<p class="supporting-copy" data-cert-histogram-empty>No complete total yet.</p>`;
  const width = 100 / bars.length, top = 36;
  const rects = bars.map((bar, i) => { const height = Math.max(bar.count ? 1 : 0, Math.round(bar.share * top * 10) / 10);
    return `<rect x="${(i * width + 0.6).toFixed(2)}" y="${(top - height).toFixed(2)}" width="${(width - 1.2).toFixed(2)}" height="${height}"><title>${h.e(`${bar.label}: ${count(bar.count, "student")}`)}</title></rect>`; }).join("");
  const text = bars.map(bar => `${bar.label}: ${bar.count}`).join(", ");
  return `<figure class="certification-histogram" data-cert-histogram><svg viewBox="0 0 100 ${top}" preserveAspectRatio="none" role="img" aria-label="${h.e(`${label}. ${text}`)}" focusable="false">${rects}</svg>`
    + `<ol class="certification-histogram-counts" aria-hidden="true">${bars.map(bar => `<li><b>${h.e(bar.count)}</b><span>${h.e(fmt(bar.from, 0))}</span></li>`).join("")}</ol></figure>`;
}

export function statsLine(stats, possible, h) {
  if (!stats?.n) return `<p class="supporting-copy">No complete total yet.</p>`;
  const of = possible ? ` / ${fmt(possible)}` : "";
  const facts = [["n", fmt(stats.n, 0)], ["Mean", `${fmt(stats.mean)}${of}`], ["Median", fmt(stats.median)], ["SD", fmt(stats.sd)], ["Min", fmt(stats.min)], ["Max", fmt(stats.max)]];
  return `<dl class="certification-facts">${facts.map(([k, v]) => `<div><dt>${h.e(k)}</dt><dd>${h.e(v)}</dd></div>`).join("")}</dl>`;
}

// The distribution's choices: all students, each version, each section (only those the view has figures for).
export function distributionChoices(view) {
  const by = view.stats || {}, choices = [{value: "all", label: "All"}];
  for (const letter of Object.keys(by.by_version || {})) choices.push({value: `version:${letter}`, label: `Version ${letter}`});
  for (const number of Object.keys(by.by_section || {})) choices.push({value: `section:${number}`, label: `Section ${number}`});
  return choices;
}

export function distributionStats(view, choice = "all") {
  const by = view.stats || {};
  const [kind, key] = String(choice).split(":");
  return kind === "version" ? by.by_version?.[key] : kind === "section" ? by.by_section?.[key] : by.total;
}

// ------------------------------------------------------------------------------------------------------------------------------
// Stat cards: each one opens the matching table filter (a button, not a link: the page keeps its address).

// Multiple choice only (bubble sheets, no free response): nothing is flagged or decided by hand there (certify-19).
export const choiceOnly = view => (view?.parts || []).includes("bubble") && !(view?.parts || []).includes("frq");

// Students certified at 0 while the certification is current: they are not "missing" any more, they were confirmed (certify-6).
const certifiedZero = (view, s) => view?.certification?.state === "current" && s.status === "missing" && s.certified?.status === "zero" && !s.changed_since;
// Every student the "Missing a grade" card and filter count was certified at 0: both say "Certified 0" instead.
export function missingCertifiedZero(view) {
  const missing = (view?.students || []).filter(STUDENT_TESTS.missing), total = Number(view?.counts?.missing);
  return total > 0 && missing.length === total && missing.every(s => certifiedZero(view, s));
}

export function cardsModel(view) {
  const c = view.counts || {}, total = view.stats?.total || {}, q = view.questions || [];
  const instructor = q.reduce((sum, row) => sum + Number(row.instructor || 0), 0), automatic = q.reduce((sum, row) => sum + Number(row.automatic || 0), 0);
  const written = view.gradescope?.written || {}, sheets = view.gradescope?.sheets || {};
  const confirmed = missingCertifiedZero(view);
  const cards = [
    {key: "complete", value: `${fmt(c.complete, 0)} / ${fmt(c.participants, 0)}`, label: "Students with every grade", jump: "students:complete"},
    {key: "mean", value: total.n ? `${fmt(total.mean)} / ${fmt(view.possible?.total)}` : EMPTY, label: total.n ? `Mean · median ${fmt(total.median)}` : "Mean", jump: "questions"},
    {key: "missing", value: fmt(c.missing, 0), label: confirmed ? "Certified 0 · no work" : "Missing a grade", jump: "students:missing", attention: Number(c.missing) > 0 && !confirmed},
    // Still flagged; grades a reviewing model settled are said apart (2026-09-27: "over 800 flags" on Quiz 2 were 765 settled ones).
    ...(choiceOnly(view) ? [] : [
      {key: "flagged", value: fmt(c.flagged, 0), label: `Still flagged`, note: Number(c.settled || 0) ? `${fmt(c.settled, 0)} more settled by a reviewing model` : "", jump: "students:flagged"},
      {key: "instructor", value: `${fmt(instructor, 0)} / ${fmt(instructor + automatic, 0)}`, label: "Decided by you", jump: "questions"}]),
  ];
  if (view.gradescope?.configured) {
    const parts = [written.grades ? `${fmt(written.grades.sent, 0)} grades` : "", written.papers ? `${fmt(written.papers.sent, 0)} papers` : "", sheets.matched ? `${fmt(sheets.sent, 0)} sheets` : ""].filter(Boolean);
    cards.push({key: "gradescope", value: parts.join(" · ") || EMPTY, label: "On Gradescope", jump: "students:gradescope", attention: Number(c.not_on_gradescope) > 0});
  }
  return cards;
}

export function cardsMarkup(view, h) {
  return `<div class="certification-cards" role="group" aria-label="Summary">${cardsModel(view).map(card =>
    `<button type="button" class="stat" data-cert-card="${h.e(card.key)}" data-cert-jump="${h.e(card.jump)}"${card.attention ? " data-attention" : ""}><strong>${h.e(card.value)}</strong><span>${h.e(card.label)}</span>${card.note ? `<small>${h.e(card.note)}</small>` : ""}</button>`).join("")}</div>`;
}

// ------------------------------------------------------------------------------------------------------------------------------
// Questions: one row per question of every version, sortable by any column (browser state only).

// [key, header, full name]: short headers so the table fits beside the page at 1440 px (review UX9); the full name is the title.
export const QUESTION_COLUMNS = [
  ["title", "Question"], ["version", "Version"], ["max", "Max"], ["mean", "Mean"], ["median", "Median"], ["pct_full", "Full %", "Share at full credit"],
  ["pct_zero", "Zero %", "Share at 0"], ["decided", "Auto / You", "Automatic grades / decided by you"], ["flagged", "Flagged", "Still flagged (grades a reviewing model settled are counted apart)"],
  ["overridden", "Changed", "Changed by you"], ["gradescope", "Gradescope"],
];

const questionValue = (row, key) => key === "decided" ? Number(row.instructor || 0) : key === "gradescope" ? Number(row.gradescope?.not_sent || 0) + Number(row.gradescope?.changed || 0)
  : key === "title" || key === "version" ? String(row[key] ?? "") : num(row[key]);

// Printed order is the server's; a sort is stable and keeps it among equal values. Missing numbers sort last either way.
export function sortQuestions(rows = [], sort = {key: "", dir: "asc"}) {
  if (!sort?.key) return [...rows];
  const sign = sort.dir === "desc" ? -1 : 1;
  return rows.map((row, index) => ({row, index})).sort((a, b) => {
    const x = questionValue(a.row, sort.key), y = questionValue(b.row, sort.key);
    if (x === null || y === null) return x === y ? a.index - b.index : x === null ? 1 : -1;
    const order = typeof x === "string" ? x.localeCompare(y, undefined, {numeric: true}) : x - y;
    return order ? sign * order : a.index - b.index;
  }).map(entry => entry.row);
}

function gradescopeCell(g, h) {
  if (!g) return EMPTY;
  if (g.unsendable) return `<span class="chip" data-tone="warning" title="${h.e(g.unsendable)}">Cannot send</span>`;
  const open = Number(g.not_sent || 0) + Number(g.changed || 0);
  return open ? `<span class="chip" data-tone="warning">${h.e(fmt(open, 0))} not sent</span>` : Number(g.sent || 0) ? `<span class="chip" data-tone="success">Sent</span>` : EMPTY;
}

// `selected`: the question whose panel is open (its row is marked current).
export function questionTableMarkup(view, h, sort = {key: "", dir: "asc"}, selected = "") {
  const rows = sortQuestions(view.questions || [], sort);
  if (!rows.length) return `<p class="supporting-copy">No question has a final grade yet.</p>`;
  const header = QUESTION_COLUMNS.map(([key, label, full]) => {
    const on = sort.key === key, dir = on ? (sort.dir === "desc" ? "descending" : "ascending") : "none";
    return `<th scope="col" aria-sort="${dir}"><button type="button" class="certification-sort" data-cert-sort="${h.e(key)}"${full ? ` title="${h.e(full)}" aria-label="${h.e(full)}"` : ""}>${h.e(label)}${on ? `<span aria-hidden="true">${sort.dir === "desc" ? " ↓" : " ↑"}</span>` : ""}</button></th>`;
  }).join("");
  const body = rows.map(row => `<tr data-question="${h.e(row.question_id)}"${row.question_id === selected ? ' aria-current="true"' : ""}><th scope="row"><button type="button" class="text-button certification-open" data-cert-question-open="${h.e(row.question_id)}" aria-label="${h.e(`Open ${row.title}${row.version ? `, Version ${row.version}` : ""}`)}"><span class="certification-name">${h.e(row.title)}</span></button>${row.not_final ? `<small class="certification-note">${h.e(fmt(row.not_final, 0))} not final</small>` : ""}</th>`
    + `<td>${h.e(row.version || EMPTY)}</td><td>${h.e(fmt(row.max))}</td><td>${h.e(fmt(row.mean))}</td><td>${h.e(fmt(row.median))}</td><td>${h.e(pct(row.pct_full))}</td><td>${h.e(pct(row.pct_zero))}</td>`
    + `<td>${h.e(fmt(row.automatic, 0))} / ${h.e(fmt(row.instructor, 0))}</td><td>${h.e(fmt(row.flagged, 0))}${Number(row.settled || 0) ? `<small class="certification-note">+${h.e(fmt(row.settled, 0))} settled</small>` : ""}</td><td>${h.e(fmt(row.overridden, 0))}</td><td>${gradescopeCell(row.gradescope, h)}</td></tr>`).join("");
  return `<div class="data-table certification-table" tabindex="0" role="region" aria-label="Questions"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function bubbleQuestionsMarkup(view, h) {
  const versions = Object.entries(view.bubble_questions || {}).filter(([, rows]) => rows?.length);
  if (!versions.length) return "";
  const table = ([letter, rows]) => `<div class="data-table certification-table" tabindex="0" role="region" aria-label="${h.e(`Multiple choice, version ${letter}`)}"><table><caption>${h.e(letter ? `Version ${letter}` : "Multiple choice")}</caption><thead><tr><th scope="col">Question</th><th scope="col">Correct</th><th scope="col">Most common wrong answer</th></tr></thead><tbody>${rows.map(r =>
    `<tr><th scope="row">${h.e(r.number)}</th><td>${h.e(pct(r.correct_pct))}</td><td>${h.e(r.common_wrong || EMPTY)}</td></tr>`).join("")}</tbody></table></div>`;
  return versions.map(table).join("");
}

// ------------------------------------------------------------------------------------------------------------------------------
// Students: every participant, filtered in the browser by a chip and by the words typed (names and sections, any order).

export const STUDENT_FILTERS = [
  ["all", "All"], ["missing", "Missing a grade"], ["in_progress", "In progress"], ["flagged", "Flagged"], ["zero", "Zero somewhere"],
  ["changed", "Changed since certification"], ["dr_excused", "DR / makeup / excused"], ["gradescope", "Not on Gradescope"],
  ["faculty", "Certified by Faculty"],
];

// Filters only a card opens (no chip until then): the wording of the chip it shows, pressed.
export const CARD_FILTER_LABELS = {complete: "Complete"};

// The server's own rule (`gradescope.uncovered`, the rule its `counts.not_on_gradescope` counts), so the card and the filter it opens
// always agree.
const notOnGradescope = s => Boolean(s.gradescope?.uncovered);

export const STUDENT_TESTS = {
  all: () => true,
  complete: s => s.status === "complete",
  // A student with no grade at all, and one with a part missing (it counts 0): the "Missing a grade" card counts both.
  missing: s => s.status === "missing" || s.status === "missing_part",
  in_progress: s => s.status === "in_progress",
  flagged: s => Number(s.flagged || 0) > 0,
  zero: s => Number(s.zero_questions || 0) > 0,
  changed: s => Boolean(s.changed_since),
  dr_excused: s => ["dr_ahead", "makeup", "excused"].includes(s.status) || Boolean(s.dr?.length),
  gradescope: notOnGradescope,
  // LATE PAPERS CERTIFY THEMSELVES: whom Faculty certified by itself since you last certified (`markFaculty`).
  faculty: s => Boolean(s.by_faculty),
};

// Marks the students Faculty certified by itself since the instructor's latest Certify (`view.automatic.latest.students`, the server's
// own list) with `by_faculty`, for the "Certified by Faculty" filter and the rows' "By Faculty" tag. Front end only; returns the view.
export function markFaculty(view) {
  const rows = new Set((view?.automatic?.latest?.students || []).map(p => p.row));
  for (const student of view?.students || []) {
    if (rows.has(student.row)) student.by_faculty = true;
  }
  return view;
}

// What Faculty's own send of a late paper says in the student's Gradescope cell (GRADESCOPE "AUTOMATIC LATE-PAPER SEND").
export function automaticSendText(a) {
  if (!a) return "";
  return a.status === "waiting" ? `Faculty sends this by itself${a.reason ? ` · ${a.reason}` : ""}` : `Faculty did not send it: ${a.reason || "no reason recorded"}`;
}

// Case, accents and punctuation ignored; each typed word must begin a word of the name or be the section number.
export function normalizeName(text) {
  return String(text ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function nameMatches(student, query) {
  const words = normalizeName(query).split(" ").filter(Boolean);
  if (!words.length) return true;
  const own = normalizeName(`${student.name} ${student.section}`).split(" ");
  return words.every(word => own.some(part => part.startsWith(word)));
}

// Digits typed (four or more) are a UIN search the SERVER answers with names; the browser never sees a UIN.
export const digitsOf = query => { const text = String(query ?? "").trim(); return /^[\d\s-]+$/.test(text) ? text.replace(/\D/g, "") : ""; };
export const isDigitSearch = query => digitsOf(query).length >= 4;

// `matches`: the server's answer to a digit search ({row, similarity}), or null. With matches the rows are those, closest first.
export function filterStudents(students = [], {filter = "all", query = "", matches = null} = {}) {
  const test = STUDENT_TESTS[filter] || STUDENT_TESTS.all;
  if (matches) {
    const by = new Map(students.map(s => [s.row, s]));
    return matches.map(m => by.get(m.row)).filter(s => s && test(s));
  }
  return students.filter(s => test(s) && nameMatches(s, isDigitSearch(query) ? "" : query));
}

export function filterCounts(students = []) {
  return Object.fromEntries(STUDENT_FILTERS.map(([key]) => [key, students.filter(STUDENT_TESTS[key]).length]));
}

const STATUS = {complete: ["Complete", "success"], missing: ["Missing a grade", "warning"], missing_part: ["Part missing · counts 0", "warning"],
  in_progress: ["In progress", "info"], excused: ["Excused", "neutral"],
  dr_ahead: ["DR exam ahead", "neutral"], makeup: ["Waits for makeup", "neutral"], left_out: ["Left out", "neutral"]};
// `view`: the page's view; while its certification is current, a student certified at 0 reads "Certified 0", not a warning.
export const statusChip = (s, h, view = null) => {
  const [label, tone] = certifiedZero(view, s) ? ["Certified 0", "neutral"] : STATUS[s.status] || [String(s.status || "").replaceAll("_", " "), "neutral"];
  return `<span class="chip" data-tone="${tone}"${s.reason ? ` title="${h.e(s.reason)}"` : ""}>${h.e(label)}</span>`; };

const partCell = part => !part || part.state === "none" ? EMPTY : part.state === "complete" ? `${fmt(part.earned)} / ${fmt(part.possible)}`
  : part.final !== undefined && part.expected ? `${fmt(part.final, 0)} of ${fmt(part.expected, 0)} final` : String(part.state || EMPTY).replaceAll("_", " ");

// `parts`: the plan's parts (frq, bubble); a part the assignment does not have gets no column.
export function studentRowMarkup(s, h, {highlight = false, parts = ["frq", "bubble"], open = false, view = null} = {}) {
  const total = s.total ? `${fmt(s.total.earned)} / ${fmt(s.total.possible)}` : EMPTY;
  const flags = [Number(s.flagged || 0) ? `${fmt(s.flagged, 0)} flagged` : "", Number(s.zero_questions || 0) ? `${fmt(s.zero_questions, 0)} at 0` : "", s.changed_since ? "changed" : ""].filter(Boolean).join(" · ") || EMPTY;
  const automatic = s.gradescope?.automatic;
  const gs = automatic ? `<span class="chip" data-tone="${automatic.status === "waiting" ? "info" : "warning"}" data-cert-automatic-chip title="${h.e(automaticSendText(automatic))}">${automatic.status === "waiting" ? "Faculty sends" : "Not sent"}</span><small class="certification-note">${h.e(automaticSendText(automatic))}</small>`
    : notOnGradescope(s) ? `<span class="chip" data-tone="warning">Not sent</span>` : s.gradescope?.paper || s.gradescope?.sheet ? `<span class="chip" data-tone="success">Sent</span>` : EMPTY;
  const faculty = s.by_faculty ? ` <span class="chip" data-tone="neutral" data-cert-by-faculty>By Faculty</span>` : "";
  // The name opens the student's drawer; a student with no work at all can be excused here (the Exams page's own record).
  const excuse = s.status === "missing" ? ` <button type="button" class="text-button certification-excuse" data-cert-excuse="${h.e(s.row)}" aria-label="${h.e(`Excuse ${s.name}…`)}">Excuse…</button>` : "";
  return `<tr data-row="${h.e(s.row)}"${highlight ? " data-cert-match" : ""}${open ? ' aria-current="true"' : ""}><th scope="row"><button type="button" class="text-button certification-open" data-cert-open="${h.e(s.row)}"><span class="certification-name">${h.e(s.name)}</span></button>${faculty}</th><td>${h.e(s.section)}</td><td>${h.e(s.exam_version || EMPTY)}</td>`
    + (parts.includes("frq") ? `<td>${h.e(partCell(s.frq))}</td>` : "") + (parts.includes("bubble") ? `<td>${h.e(partCell(s.bubble))}</td>` : "") + `<td>${h.e(total)}</td><td>${statusChip(s, h, view)}${s.reason && s.status !== "complete" ? `<small class="certification-note">${h.e(s.reason)}</small>` : ""}${excuse}</td><td>${h.e(flags)}</td><td>${gs}</td></tr>`;
}

export function studentTableMarkup(rows, h, {matches = null, parts = ["frq", "bubble"], open = "", view = null} = {}) {
  if (!rows.length) return "";
  const matched = new Set((matches || []).map(m => m.row));
  const heads = ["Name", "Section", "Version", ...(parts.includes("frq") ? ["Free response"] : []), ...(parts.includes("bubble") ? ["Multiple choice"] : []), "Total", "Status", "Flags", "Gradescope"];
  return `<div class="data-table certification-table certification-students" tabindex="0" role="region" aria-label="Students"><table><thead><tr>${heads.map(x => `<th scope="col">${h.e(x)}</th>`).join("")}</tr></thead><tbody>${rows.map(s => studentRowMarkup(s, h, {highlight: matched.has(s.row), parts, open: s.row === open, view})).join("")}</tbody></table></div>`;
}
