// Certification and Export, the sixth grading step (requirement, 2026-09-26).
// The page leads with the analysis: one header with the ONE primary (the student search sits with the Students table) (Certify grades…), a one-line status band
// whose Details hold every check, the summary cards, the distribution under them, one
// question's panel when a question is opened
// (`?question=<qid>`), and Questions | Students as two tabs of one panel; a student opens in a drawer beside the tables, where a grade
// is changed through the one review desk. Gradescope, the files for the instructor's records and the training downloads follow as
// secondary sections.
// Everything shown is the server's read-only view (GET /api/grading/assignments/<aid>/certification): this module orders, filters and
// draws it, and keeps browser state only (tab, filter, sort, what is open). UIN digits typed into the search go in a POST body and are
// answered with names; they never reach an address, an attribute or a status line. Row keys never reach the address either.
// The Gradescope panels read Gradescope receipts (status, sheets, publications: about 0.4 s of locked server work on a big exam), so
// they are mounted only when their fold opens (perf-2), or at once for `#grading/<aid>?tab=export&open=gradescope` (completeness-6).
import {guideSteps, stepHeader, shortDate} from "./grading-guide.js";
import {aboutMarkup, calloutMarkup, disclosureMarkup, filterChipsMarkup, bindFilterChips, loadingMarkup, emptyMarkup, tableMarkup, loadModule, registerShortcut} from "./ui.js";
import {patchChildren} from "./dom-patch.js";
import {mountGradescope, sendDialog, unitProgress, PUBLISH_WHERE} from "./grading-gradescope.js";
import {mountPublish, publishDialog} from "./grading-gradescope-publish.js";
import {renderMath} from "./grading-math.js";
import {downloadsMarkup, trainingMarkup, bindDownloads} from "./grading-certification-files.js";
import * as s from "./grading-certification-stats.js";
import {automaticSendText} from "./grading-certification-stats.js";
import {questionPanelMarkup, filterResponses, storedHideNames, storeHideNames} from "./grading-certification-question.js";
import {createDrawer, excuseDialog, typingText} from "./grading-certification-students.js";
import {auditMarkup, auditUrl, auditRunning, startDialogMarkup} from "./grading-certification-audit.js";
import {take} from "./handoff.js";

export const certificationUrl = plan => `/api/grading/assignments/${encodeURIComponent(plan.id)}/certification`;
const POLL = 20000, HANDOFF_POLL = 4000, SEARCH_DELAY = 300, READ_TIMEOUT = 60000;
const TAB_KEY = "faculty:certification:tab";
const loadDialog = () => loadModule(() => import("./grading-certification-dialog.js"));
const loadAdjust = () => loadModule(() => import("./grading-certification-adjust.js"));
const GRADESCOPE_BASE = (plan, kind) => `/api/grading/gradescope/assignments/${encodeURIComponent(plan.id)}${kind === "sheets" ? "/sheets" : ""}`;
// The question panel a check's rows open, with the filter that shows what the check is about.
const CHECK_FILTER = {automatic_unchecked: "automatic", flagged_finalized: "settled", answer_inconsistency: "inconsistent", zero_question_all: "zero", question_all_full: "full"};

const count = (value, one, many = `${one}s`) => `${Number(value || 0).toLocaleString()} ${Number(value) === 1 ? one : many}`;
const storedTab = () => { try { return globalThis.localStorage?.getItem(TAB_KEY) === "students" ? "students" : "questions"; } catch { return "questions"; } };
const storeTab = tab => { try { globalThis.localStorage?.setItem(TAB_KEY, tab); } catch { /* a convenience only */ } };

// The header's sentence: how many students have every grade, or where the certification stands. A current certification's counts are
// its own record's (the history row; certify-4: `gradebook.written` is only the cells it changed, 0 when certified again unchanged).
// The sentence about late papers Faculty certified is not here: the header draws it with its own button, automaticMarkup.
export function leadText(view) {
  const cert = view.certification || {state: "none"}, c = view.counts || {}, day = shortDate(cert.created_at), n = value => Number(value || 0).toLocaleString();
  if (cert.state === "current") {
    const brief = (view.history || []).find(row => row.id === cert.id);
    if (!brief) return `Certified${day ? ` ${day}` : ""} · ${count(c.participants, "student")}.`;
    const dr = Number(brief.left_out_dr || 0), makeup = Number(brief.left_out_makeup || 0), other = Math.max(0, Number(brief.left_out || 0) - dr - makeup);
    return `Certified${day ? ` ${day}` : ""} · ${count(brief.students, "student")}: ${n(brief.scored)} scored, ${n(brief.zero)} given 0`
      + `${Number(brief.excused) ? `, ${n(brief.excused)} excused` : ""}${dr ? `, ${n(dr)} left out until their DR exam` : ""}`
      + `${makeup ? `, ${n(makeup)} left out until their makeup` : ""}${other ? `, ${n(other)} left out` : ""}.`;
  }
  // The band says that grades changed and what; the lead says what the gradebook holds meanwhile (missed:certify[1]). What Faculty
  // is about to certify by itself is said instead of asking for a Certify (requirement, 2026-11-14), and beside it when more changed.
  if (cert.state === "changed") {
    const next = nextText(view);
    if (next) return view.automatic.next.rest ? `${next} Your other changes wait for you to certify again.` : next;
    return `${cert.gradebook?.title ? `The gradebook column “${cert.gradebook.title}”` : "The gradebook"} keeps the values certified${day ? ` ${day}` : ""} until you certify again.`;
  }
  return `${n(c.complete)} of ${n(c.participants)} students have every grade. Check them here, then certify.`;
}

// Names up to two students, counts more: "Ada Lee and Bo Ray", "3 students".
const people = list => list.length > 2 ? `${list.length} students` : list.map(p => p.name || "a student").join(" and ");
const paperWords = list => list.length > 2 ? `${list.length} late papers` : `${people(list)}’s late paper${list.length > 1 ? "s" : ""}`;

// What Faculty is about to certify by itself (`view.automatic.next`, the server's forecast of its own rule), in the lead while the
// certification reads changed: the page asked for a Certify during the minutes Faculty waits for the work to settle (instructor,
// 2026-11-14). GRADING "LATE PAPERS CERTIFY THEMSELVES" and "MAKEUP CHANGES CERTIFY THEMSELVES". "" when there is nothing.
export function nextText(view) {
  const next = view.automatic?.enabled ? view.automatic.next : null;
  if (!next) return "";
  const papers = next.papers || [], zero = next.zero || [], left = next.left_out || [];
  const does = [papers.length ? `certifies ${paperWords(papers)}` : "", zero.length ? `gives ${people(zero)} 0 (makeup ended)` : "",
    left.length ? `leaves ${people(left)} out until their makeup` : ""].filter(Boolean);
  if (!does.length) return "";
  return `Faculty does this by itself ${next.wait ? "once nothing blocks certifying" : "in a few minutes"}: ${does.join("; ")}.`;
}

// LATE PAPERS CERTIFY THEMSELVES (2026-10-09): who Faculty certified by itself since you last certified, said after the lead, with its
// "who" as a text button that opens the Students tab on "Certified by Faculty" (2026-10-09 review: past two names nothing on the page
// said whom Faculty certified). MAKEUP CHANGES CERTIFY THEMSELVES (2026-11-14): whom it gave 0 after their makeup ended and whom it
// left out until their makeup are named too (`change` on the student), so a 0 Faculty gave is always said here.
export function automaticMarkup(view, h) {
  const latest = view.automatic?.latest;
  if (!latest || !(latest.students || []).length) return "";
  const of = change => latest.students.filter(p => (p.change || "") === change);
  const papers = of(""), zero = of("zero"), left = of("left_out");
  const jump = words => `<button type="button" class="text-button" data-cert-jump="students:faculty" data-cert-faculty-jump>${h.e(words)}</button>`;
  const did = [papers.length ? ["certified", paperWords(papers), ""] : null, zero.length ? ["gave", people(zero), " 0 after their makeup ended"] : null,
    left.length ? ["left", people(left), " out until their makeup"] : null].filter(Boolean);
  if (!did.length) return "";
  const day = shortDate(latest.at);  // the first "who" is the button
  return ` Faculty ${did.map(([verb, who, tail], i) => `${verb} ${i ? h.e(who) : jump(who)}${h.e(tail)}`).join(", ")} by itself`
    + `${day ? ` ${h.e(day)}` : ""}${latest.markers === "carried" ? "; your other changes wait for you to certify again" : ""}.`;
}

// Late papers still waiting before Faculty certifies them: one line per student with their own reason, and the whole plan's reason
// when it waits for your Certify. Nothing while the switch is off (Faculty certifies nothing then). Short lists show open.
export function waitingMarkup(view, state, h) {
  const automatic = view.automatic;
  if (!automatic?.enabled) return "";
  const waiting = automatic.waiting || [];
  const plan = automatic.reason ? `<p class="supporting-copy" data-cert-late-reason>${h.e(`Faculty waits for you to certify again: ${automatic.reason}`)}</p>` : "";
  if (!waiting.length) return plan;
  const id = "cert-fold-late-waiting";
  const open = state.open.has(id) || (!state.closed?.has(id) && waiting.length <= 3);
  const list = `<ul class="certification-late-list" data-cert-late-waiting>${waiting.map(w => `<li>${w.row ? `<button type="button" class="text-button" data-cert-open="${h.e(w.row)}">${h.e(w.name || "A student")}</button>` : h.e(w.name || "A student")}${w.section ? ` <span class="certification-late-section">Section ${h.e(w.section)}</span>` : ""}<span class="certification-late-reason">${h.e(w.reason || "")}</span></li>`).join("")}</ul>`;
  return plan + disclosureMarkup({label: "Late papers waiting", hint: waiting.length.toLocaleString(), body: list, open, id, className: "certification-late"});
}

// A late paper Faculty sent by itself onto a version Gradescope has not published although its set is (a makeup version Faculty added
// lands unpublished): the student does not see that grade until you Publish grades on Gradescope… (the header's). Faculty never publishes.
export function unpublishedMarkup(view, h) {
  const letters = view.gradescope?.unpublished_versions || [];
  if (!letters.length || !view.gradescope?.configured) return "";
  return `<p class="grading-warning" role="status" data-cert-unpublished>${h.e(`${letters.length === 1 ? "Version" : "Versions"} ${letters.join(", ")} ${letters.length === 1 ? "is" : "are"} not published on Gradescope: a late paper Faculty sent there by itself is not visible to the student yet · ${PUBLISH_WHERE}`)}</p>`;
}

// GRADESCOPE "AFTER CERTIFYING": what Faculty does on Gradescope by itself after a certification (`view.gradescope.after`: the latest
// run of each kind). While it works, one quiet line with what it is doing; afterwards what it published, and each section it holds
// with why and your own Publish grades… (the header's). What it could not send is listed once. Read only: nothing is retried here.
const sectionsWords = numbers => `${numbers.length === 1 ? "section" : "sections"} ${numbers.join(", ")}`;
export function afterRunsMarkup(view, h) {
  const runs = view.gradescope?.after || [];
  if (!runs.length || !view.gradescope?.configured) return "";
  const jump = ` · ${PUBLISH_WHERE}`;
  return runs.map(run => {
    const late = run.kind === "late", lines = [];
    const what = late ? "a late paper’s grade" : "the grades";
    if (run.state === "working") {
      lines.push(`<p class="supporting-copy" role="status" data-cert-after="working">${h.e(`Faculty is ${late ? "publishing a late paper’s grade on Gradescope" : "sending this to Gradescope and publishing it"} by itself · ${[run.doing, run.waiting].filter(Boolean).join(" · ")}`)}</p>`);
    } else {
      if ((run.publishing || []).length) lines.push(`<p class="supporting-copy" role="status" data-cert-after="publishing">${h.e(`Faculty is publishing ${what} on Gradescope: ${sectionsWords(run.publishing)}.`)}</p>`);
      if ((run.published || []).length) lines.push(`<p class="supporting-copy" data-cert-after="published">${h.e(`Faculty published ${what} on Gradescope by itself: ${sectionsWords(run.published)}.`)}</p>`);
    }
    for (const held of run.held || []) lines.push(`<p class="grading-warning" role="status" data-cert-after="held">${h.e(`Faculty did not publish section ${held.section}${held.part === "sheets" ? "’s bubble sheets" : ""} on Gradescope: ${held.reason}`)}${jump}</p>`);
    for (const failed of run.failed || []) lines.push(`<p class="grading-warning" role="status" data-cert-after="failed">${h.e(`Gradescope did not publish ${sectionsWords(failed.sections || [])}: ${failed.reason || "no reason given"} Retry it in the Gradescope panel below.`)}</p>`);
    if ((run.notes || []).length) lines.push(`<div data-cert-after="notes"><p class="grading-warning" role="status">Faculty did not send everything to Gradescope by itself:</p><ul class="certification-late-list">${run.notes.map(note => `<li>${h.e(note)}</li>`).join("")}</ul></div>`);
    if (run.stopped) lines.push(`<p class="grading-warning" role="status" data-cert-after="stopped">${h.e(`Faculty stopped its own Gradescope work: ${run.stopped}`)}</p>`);
    return lines.join("");
  }).join("");
}

// Whom Faculty certified by itself since you last certified, each opening their row's drawer.
export function facultyMarkup(view, state, h) {
  const students = view.automatic?.latest?.students || [];
  if (!students.length) return "";
  const id = "cert-fold-faculty";
  const what = {zero: "Given 0: their makeup ended", left_out: "Left out until their makeup"};
  const list = `<ul class="certification-late-list" data-cert-faculty-list>${students.map(p => `<li><button type="button" class="text-button" data-cert-open="${h.e(p.row)}">${h.e(p.name || "A student")}</button>${p.section ? ` <span class="certification-late-section">Section ${h.e(p.section)}</span>` : ""}${what[p.change] ? `<span class="certification-late-reason">${h.e(what[p.change])}</span>` : ""}</li>`).join("")}</ul>`;
  return disclosureMarkup({label: "Certified by Faculty", hint: students.length.toLocaleString(), body: list, open: state.open.has(id), id, className: "certification-late"});
}

// The Gradescope fold's line per late paper Faculty sends by itself (GRADESCOPE "AUTOMATIC LATE-PAPER SEND"): still waiting and why,
// or not sent and why with the send dialog. Read only: Faculty never sends one again by itself.
export function automaticSendsMarkup(view, h) {
  const rows = (view?.students || []).filter(s => s.gradescope?.automatic);
  if (!rows.length) return "";
  const line = s => {
    const a = s.gradescope.automatic, kind = a.kind === "sheets" ? "sheets" : "written";
    const what = automaticSendText(a);
    return `<li data-cert-automatic-send="${h.e(s.row)}"><button type="button" class="text-button" data-cert-open="${h.e(s.row)}">${h.e(s.name)}</button> <span>${h.e(what)}</span>`
      + `${a.status === "skipped" ? ` · <button type="button" class="text-button" data-cert-send="${kind}">Send to Gradescope…</button>` : ""}</li>`;
  };
  return `<section class="certification-automatic-sends" aria-label="Late papers Faculty sends by itself"><h4>Late papers Faculty sends by itself</h4><ul class="certification-late-list">${rows.map(line).join("")}</ul></section>`;
}

// A check's rows are students, questions or sheets: the fold says which (certify-13).
function rowsKind(rows) {
  if (rows.every(r => r.row)) return ["Students", "Student"];
  if (rows.every(r => r.question_id)) return ["Questions", "Question"];
  if (rows.every(r => r.job_id)) return ["Sheets", "Sheet"];
  return ["Details", "Student or question"];
}

// A check's rows: a student opens the drawer, a question its panel (filtered to what the check is about), a sheet its Inbox scan.
function checkRowsMarkup(check, h) {
  const rows = check.rows || [];
  if (!rows.length) return "";
  const cell = r => r.row ? `<button type="button" class="text-button" data-cert-open="${h.e(r.row)}">${h.e(r.name)}</button>`
    : r.question_id ? `<button type="button" class="text-button" data-cert-question-open="${h.e(r.question_id)}" data-cert-filter="${h.e(CHECK_FILTER[check.id] || "all")}"${r.group !== undefined && r.group !== null ? ` data-cert-group="${h.e(r.group)}"` : ""}>${h.e(r.name)}</button>`
    : r.job_id ? `<a href="#inbox/job/${h.e(encodeURIComponent(r.job_id))}/sheets${r.sheet_id ? `?sheet=${h.e(encodeURIComponent(r.sheet_id))}` : ""}">${h.e(r.name)}${r.page ? ` (page ${h.e(r.page)})` : ""}</a>` : h.e(r.name);
  const [label, head] = rowsKind(rows);
  const table = tableMarkup(h.e, [head, "Section", "Detail"], rows.map(r => `<tr><td>${cell(r)}</td><td>${h.e(r.section || "")}</td><td>${h.e(r.detail || "")}</td></tr>`), check.title);
  // The hint counts the rows listed, as the label names them: a check's count may count grades (and papers) over rows of questions
  // or students, and the title already carries it. A list cut at the server's cap says so.
  const shown = rows.length.toLocaleString();
  return disclosureMarkup({label, hint: s.rowsCut(check) ? `first ${shown}` : shown, body: table});
}

function checksMarkup(view, h) {
  const groups = s.checkGroups(view.checks), provisional = (view.checks || []).some(c => c.provisional);
  const cert = view.certification || {}, day = shortDate(cert.created_at);
  const titles = {block: "Must be finished before certifying",
    confirm: cert.state === "current" ? `Confirmed when certified${day ? ` ${day}` : ""}` : "You confirm these when you certify", info: "For your information"};
  const tones = {block: "warning", confirm: "info", info: "neutral"};
  const one = (check, level) => {
    const action = check.action?.href ? {label: check.action.label, href: check.action.href}
      // ONE PLACE TO SEND (2026-10-18 review item 10): the check leads to the Gradescope fold's own Send, never a second Send button.
      : check.action?.kind ? {label: "Open the Gradescope section", data: {certGradescope: check.action.kind === "send_sheets" ? "sheets" : "written"}} : null;
    return `<li data-cert-check="${h.e(check.id)}">${calloutMarkup({tone: tones[level], title: s.countedTitle(check), body: check.detail || "", action})}${checkRowsMarkup(check, h)}</li>`;
  };
  const sections = ["block", "confirm", "info"].filter(level => groups[level].length).map(level =>
    `<section class="certification-checks" data-cert-checks="${level}"><h4>${h.e(titles[level])}</h4><ul>${groups[level].map(c => one(c, level)).join("")}</ul></section>`).join("");
  return `${provisional ? `<p class="supporting-copy">These are read from the page. Certify grades… checks everything again.</p>` : ""}${sections || '<p class="supporting-copy">Nothing to check.</p>'}`;
}

function historyMarkup(view, h) {
  const rows = (view.history || []).map(c => `<tr><td>${h.e(new Date(c.created_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}))}</td><td>${h.e(c.by === "faculty" ? "Faculty" : "You")}</td><td>${h.e(Number(c.students || 0).toLocaleString())}</td><td>${h.e(Number(c.scored || 0).toLocaleString())}</td><td>${h.e(Number(c.zero || 0).toLocaleString())}</td><td>${h.e(Number(c.excused || 0).toLocaleString())}</td><td>${h.e(Number(c.left_out || 0).toLocaleString())}</td><td>${h.e(c.gradebook?.title || "")}</td></tr>`);
  return rows.length ? tableMarkup(h.e, ["Certified", "By", "Students", "Scored", "Zero", "Excused", "Left out", "Gradebook column"], rows, "Earlier certifications") : "";
}

function distributionMarkup(view, state, h) {
  const choices = s.distributionChoices(view), chosen = choices.some(c => c.value === state.dist) ? state.dist : "all";
  const stats = s.distributionStats(view, chosen) || {};
  const label = choices.find(c => c.value === chosen)?.label || "All";
  const parts = (view.parts || []).filter(part => view.stats?.[part] && (view.parts || []).length > 1).map(part => {
    const p = view.stats[part];
    return `<li data-cert-part="${h.e(part)}"><strong>${h.e(s.partName(part))}</strong> ${p?.n ? `mean ${h.e(s.fmt(p.mean))} / ${h.e(s.fmt(view.possible?.[part]))} · median ${h.e(s.fmt(p.median))} · n ${h.e(s.fmt(p.n, 0))}` : "no complete part yet"}</li>`;
  }).join("");
  // No complete total: the histogram's own sentence says so, once (certify-17).
  return `<section class="certification-distribution" aria-labelledby="cert-distribution-title"><h4 id="cert-distribution-title">Distribution of totals</h4>`
    + (choices.length > 1 ? filterChipsMarkup("cert-dist", choices, chosen, {label: "Distribution of"}) : "")
    + `<div class="certification-distribution-body">${s.histogramMarkup(stats, h, `Totals, ${label}, percent of possible`)}${stats?.n ? s.statsLine(stats, view.possible?.total, h) : ""}</div>`
    + (parts ? `<ul class="certification-parts">${parts}</ul>` : "") + `</section>`;
}

// The rows of the Students tab as filtered now (also the drawer's next and previous student).
export const shownStudents = (view, state) => s.filterStudents(view?.students || [], {filter: state.filter, query: state.query, matches: state.matches});

export function panelMarkup(view, state, h) {
  const students = view.students || [], counts = s.filterCounts(students), choiceOnly = s.choiceOnly(view);
  // Multiple choice only (certify-19): the Questions tab is the multiple-choice table itself, counted by its rows.
  const bubbleRows = Object.values(view.bubble_questions || {}).reduce((sum, rows) => sum + (rows || []).length, 0);
  const tabs = filterChipsMarkup("cert-panel", [{value: "questions", label: "Questions", count: choiceOnly ? bubbleRows : (view.questions || []).length}, {value: "students", label: "Students", count: students.length}], state.tab, {label: "Show"});
  // The student search sits with the table it filters (requirement, 2026-09-26), on both tabs, so
  // typing from Questions opens Students; '/' still focuses it (the shell's [data-page-search] rule).
  const top = `<div class="certification-panel-top">${tabs}${searchMarkup()}</div>`;
  let body;
  if (state.tab === "questions") {
    const bubble = s.bubbleQuestionsMarkup(view, h);
    body = choiceOnly && bubble ? bubble
      : s.questionTableMarkup(view, h, state.sort, state.question || "") + (bubble ? disclosureMarkup({label: "Multiple choice per question", hint: "percent correct and the most common wrong answer", body: bubble, open: state.open.has("cert-fold-bubble"), id: "cert-fold-bubble"}) : "");
  } else {
    const rows = shownStudents(view, state);
    // Only the filters with someone in them (certify-12), All, and the one chosen (a card may choose an empty one).
    const shown = s.STUDENT_FILTERS.filter(([value]) => value === "all" || value === state.filter || counts[value] > 0);
    // The "Students with every grade" card opens a filter that is not offered otherwise: its chip appears, pressed, only then.
    if (!shown.some(([value]) => value === state.filter) && s.STUDENT_TESTS[state.filter]) shown.splice(1, 0, [state.filter, s.CARD_FILTER_LABELS[state.filter] || state.filter]);
    // Everyone without a grade was certified at 0: the filter says so, as the card that opens it does (certify-6).
    const named = (value, label) => value === "missing" && s.missingCertifiedZero(view) ? "Certified 0" : label;
    const filters = filterChipsMarkup("cert-students", shown.map(([value, label]) => ({value, label: named(value, label), count: counts[value] ?? students.filter(s.STUDENT_TESTS[value]).length})), state.filter, {label: "Students"});
    const empty = students.length ? emptyMarkup({kind: "filtered", title: state.query ? "No student matches this search" : "No student in this filter", body: "Show every student to look at the others.", action: {label: "Show all students", data: {certShowAll: ""}}})
      : emptyMarkup({title: "No students yet", body: "Students appear once the exam's roster is set on the Exams page."});
    body = filters + (state.searchNote ? `<p class="supporting-copy" data-cert-search-note>${h.e(state.searchNote)}</p>` : "") + (rows.length ? s.studentTableMarkup(rows, h, {matches: state.matches, parts: view.parts, open: state.drawer || "", view}) : empty);
  }
  return `<section class="grading-panel certification-panel" id="cert-panel" aria-label="Questions and students" data-cert-tab="${h.e(state.tab)}">${top}${body}</section>`;
}

export function searchMarkup() {
  return `<label class="field certification-search"><span class="sr-only">Search students by name, section or UIN digits</span><input type="search" data-page-search data-cert-search placeholder="Search students: name, section or UIN" autocomplete="off" spellcheck="false" aria-describedby="cert-search-status"></label>`;
}

// `next`: the pipeline's current step when it is another one; while no student has a grade it is the primary (certify-7).
function headerActions(view, h, next = null) {
  const cert = view?.certification?.state || "none";
  // The Final Quality Audit informs and never gates certifying, so it is a secondary action beside the one primary.
  const audit = `<button type="button" class="button" data-cert-audit-run>Final quality audit…</button>`;
  if (!view) return "";
  // Publishing on Gradescope starts here only (requirement, 2026-10-02): the fold's section shows what was published; a secondary action, offered only where Gradescope is set up.
  const publish = view.gradescope?.configured ? `<button type="button" class="button" data-cert-publish>Publish grades on Gradescope…</button>` : "";
  if (cert === "current") return `${audit}${publish}<span class="chip" data-tone="success" data-cert-certified>Certified</span>`;
  if (view.blocking) return `${audit}${publish}<button type="button" class="button primary" data-cert-blockers>Resolve ${view.blocking} ${view.blocking === 1 ? 'check' : 'checks'}</button>`;
  const href = next && (next.action?.href || next.destination);
  if (href && s.noGradesYet(view)) {
    return `${audit}${publish}<a class="button primary" data-step-action href="${h.e(href)}">${h.e(next.action?.label || `Open ${next.label}`)}</a><button type="button" class="button" data-cert-certify>Certify grades…</button>`;
  }
  // Nothing waits for the instructor while Faculty is about to certify everything that changed: Certify again… stays, not as the primary.
  const faculty = cert === "changed" && nextText(view) && !view.automatic.next.rest;
  return `${audit}${publish}<button type="button" class="button${faculty ? "" : " primary"}" data-cert-certify>${cert === "changed" ? "Certify again…" : "Certify grades…"}</button>`;
}

// A Gradescope send started from the Certify dialog (critique C13): the page follows it and offers Check again once it settles.
export function handoffMarkup(handoff, h) {
  if (!handoff) return "";
  const send = handoff.send, counts = send?.counts || {};
  const live = !send || ["pending", "running"].includes(send.status);
  const what = handoff.kind === "sheets" ? "bubble sheets" : "papers and grades";
  if (live) {
    const progress = `${unitProgress("paper", counts.uploads || {}, h)}${unitProgress("grade", counts.grades || {}, h)}${unitProgress("sheet", counts.sheets || {}, h)}`;
    return `<section class="certification-handoff" data-cert-handoff="live" aria-live="polite"><strong>Sending ${h.e(what)} to Gradescope…</strong><p class="supporting-copy">Certifying waits for the send to finish; this page follows it.</p>${progress}</section>`;
  }
  return `<div class="certification-handoff-done" data-cert-handoff="done">${calloutMarkup({tone: send.status === "complete" ? "success" : "warning", title: `The send to Gradescope ${send.status === "complete" ? "finished" : "stopped"}`,
    body: "Check again to certify with what is on Gradescope now.", action: {label: "Check again", data: {certCertify: ""}}})}</div>`;
}

// What certifying changes besides the gradebook (certify-10): the Certify dialog's own words, in the band's Details.
const aboutCertifying = h => aboutMarkup("grading/certification", s.CERTIFY_CHANGES.map(text => `<p>${h.e(text)}</p>`).join(""), {label: "What certifying changes"});

/** GRADING "GRADED FOR PARTICIPATION" (requirement, 2026-11-01): the assignment's setting, changed here at any time; the grades
 * then count again (a certified assignment shows the change until it is certified again). */
export function participationMarkup(view) {
  const taking = view.participation;
  if (!taking) return "";
  const blocked = !taking.enabled && taking.bubble_key;
  return `<div class="certification-participation" data-cert-participation-row><label class="checkbox-field"><input type="checkbox" data-cert-participation ${taking.enabled ? "checked" : ""} ${blocked ? "disabled" : ""}>Graded for participation</label>`
    + `<p class="field-hint">${blocked ? "Covers written questions only, and this assignment has a bubble-sheet answer key."
      : "Everyone who turned in a paper gets full credit; the rubric lines stay as their feedback, on Gradescope too."}</p></div>`;
}

export function topMarkup(view, state, steps, h) {
  const day = shortDate(view.certification?.created_at), current = view.certification?.state === "current";
  const next = (steps || []).find(step => step.current && step.key !== "export") || null;
  const band = s.bandModel(view, day, {termId: view.term_id, next}), groups = s.checkGroups(view.checks);
  // Certified: the confirmations were given (certify-6), so the hint no longer asks for them.
  const hint = [groups.block.length ? `${groups.block.length} to finish` : "", groups.confirm.length && !current ? `${groups.confirm.length} to confirm` : "", groups.info.length ? `${groups.info.length} notes` : ""].filter(Boolean).join(" · ");
  const history = historyMarkup(view, h);
  const details = disclosureMarkup({label: "Details", hint, body: checksMarkup(view, h) + aboutCertifying(h) + (history ? `<h4>Earlier certifications</h4>${history}` : ""), open: state.open.has("cert-fold-details"), id: "cert-fold-details", className: "certification-band-details"});
  return stepHeader(steps, "export", h, {leadMarkup: h.e(leadText(view)) + automaticMarkup(view, h), actions: headerActions(view, h, next)})
    + `<p class="sr-only" id="cert-search-status" aria-live="polite" data-cert-search-status>${h.e(state.announce || "")}</p>`
    + `<div class="certification-summary"><div class="certification-summary-main"><div class="certification-band" data-cert-band>${calloutMarkup(band)}${waitingMarkup(view, state, h)}${facultyMarkup(view, state, h)}${unpublishedMarkup(view, h)}${afterRunsMarkup(view, h)}${participationMarkup(view)}${details}</div>`
    + handoffMarkup(state.handoff, h)
    + `${s.cardsMarkup(view, h)}</div></div>`
    + distributionMarkup(view, state, h);
}


export function pageMarkup(view, state, steps, h) {
  return topMarkup(view, state, steps, h) + panelMarkup(view, state, h);
}

/**
 * @param {HTMLElement} host
 * @param {any} plan
 * @param {any} h
 * @param {() => boolean} active
 * @param {{question?: string, jobs?: any[], refresh?: (() => Promise<void>) | null, address?: ((changes: Record<string, string>) => void) | null,
 *   onPlan?: ((plan: any) => void) | null, open?: string}} [options] `open: "gradescope"` opens the Gradescope fold and focuses it
 *   (`#grading/<aid>?tab=export&open=gradescope`, completeness-6); `open: "students"` opens on Students with every student shown
 *   (`?tab=export&open=students`: a certified assignment's row on Assignments, requirement, 2026-10-09), leaving the remembered tab as it was.
 */
export async function certificationView(host, plan, h, active, {question = "", jobs = [], refresh = null, address = null, onPlan = null, open = ""} = {}) {
  const state = {tab: open === "students" ? "students" : storedTab(), filter: "all", query: "", matches: null, searchNote: "", announce: "", sort: {key: "", dir: "asc"}, dist: "all",
    open: new Set(), closed: new Set(), view: null, question: "", questionData: null, qFilter: "all", qSort: "name", hideNames: storedHideNames(), groups: [],
    drawer: "", drawerSource: "students", handoff: null, hold: 0, auditView: {pile: "", question: "", index: 0}};
  let timer = null, reading = null, searchTimer = null, searchToken = 0, questionToken = 0, handoffTimer = null, handoffPaused = false;
  // The Gradescope panels once mounted ({refresh} each, from grading-gradescope.js), and the latest send each last saw (certify-2).
  const panels = {written: null, sheets: null}, seen = {};
  let downloadsReady = null;
  host.innerHTML = `<div class="grading-certification" data-certification><div class="certification-main" data-cert-main>${loadingMarkup("Reading every grade…", 6)}</div><div class="certification-audit-host" data-cert-audit-host></div><div class="certification-question-host" data-cert-question-host></div><div class="certification-tables" data-cert-tables></div><div class="certification-drawer-host" data-cert-drawer-host></div><div class="certification-secondary" data-cert-secondary></div></div>`;
  const root = host.querySelector("[data-certification]");
  const main = host.querySelector("[data-cert-main]"), tables = host.querySelector("[data-cert-tables]"), questionHost = host.querySelector("[data-cert-question-host]");
  const secondary = host.querySelector("[data-cert-secondary]"), drawerHost = host.querySelector("[data-cert-drawer-host]");
  // The Gradescope fold's Publish grades section once mounted ({refresh}); a publication started from the header opens the fold and
  // reads it at once, so its progress is in view.
  let publishPanel = null;
  const published = () => {
    const fold = secondary.querySelector("#cert-fold-gradescope");
    // A closed fold that never opened mounts its panels when it opens (perf-2), and they read at once; a mounted one reads again.
    if (publishPanel) void publishPanel.then(panel => panel?.refresh());
    if (fold && !fold.open) fold.open = true;
  };
  const auditHost = host.querySelector("[data-cert-audit-host]");
  let audit = null, auditTimer = null, auditPaused = false;
  const running = () => active() && main.isConnected;
  const base = certificationUrl(plan);
  void import("./grading-certification-dialog.js").catch(() => {});  // warmed for tabs opened before a restart (FRONTEND.md)
  void import("./grading-certification-adjust.js").catch(() => {});

  const drawer = createDrawer(drawerHost, {base, plan, h, active: running, view: () => state.view,
    order: () => state.drawerSource === "question" && state.questionData ? filterResponses(state.questionData, {filter: state.qFilter, sort: state.qSort, groups: state.groups}).map(r => r.row)
      : shownStudents(state.view, state).map(r => r.row),
    onChanged: async () => { await read({force: true}); if (state.question) await loadQuestion(state.question, {quiet: true}); },
    onExcuse: student => excuse(student),
    onToggle: (open, row) => {
      state.drawer = open ? row : ""; root.toggleAttribute("data-drawer-open", open); drawTables();
      // The drawer narrows the tables: keep the row just opened in view (review UX8).
      if (open) requestAnimationFrame(() => tables.querySelector(`tr[data-row="${CSS.escape(row)}"]`)?.scrollIntoView({block: "nearest"}));
    }});

  function draw() {
    if (!running() || !state.view) return;
    patchChildren(main, topMarkup(state.view, state, guideSteps(plan, jobs), h));
    drawTables();
    drawAudit();
  }
  function drawTables() {
    if (!running() || !state.view) return;
    patchChildren(tables, panelMarkup(state.view, state, h));
    // What is typed lives in the box's value only, never in the markup (typed UIN digits must not reach the page's HTML); a box the
    // patch had to replace gets it back here.
    const box = tables.querySelector("[data-cert-search]");
    if (box && box.value !== state.query && document.activeElement !== box) box.value = state.query;
  }
  function drawError(error) {
    const steps = guideSteps(plan, jobs);
    patchChildren(main, stepHeader(steps, "export", h, {lead: "Faculty could not read the grades for certification.", actions: ""})
      + `${calloutMarkup({tone: "danger", title: "The grades could not be read", body: error.message, action: {label: "Try again", data: {certRetry: ""}}})}`);
  }
  // The graph's sixth node follows the certification the page read (the same `workflow.certification` Grading computes).
  function syncPlan(view) {
    if (!plan.workflow) return;
    const was = JSON.stringify(plan.workflow.certification || null), now = JSON.stringify(view.certification || null);
    if (was === now) return;
    plan.workflow = {...plan.workflow, certification: view.certification};
    onPlan?.(plan);
  }

  // A hidden tab reads nothing: the page reads again when it is shown (onVisible, below).
  function schedule() {
    clearTimeout(timer);
    if (document.hidden) return;
    if (running() && (state.view?.live || state.hold)) timer = setTimeout(() => { if (running() && !document.hidden) void read(); }, POLL);
  }
  async function read({force = false} = {}) {
    // A forced read (after a save) that meets a poll in flight waits for it, then reads again: the poll may predate the save.
    if (reading) { if (force) { await reading; return read({force}); } return; }
    if (state.hold && !force) { schedule(); return; }  // a preview or a certify holds the lifecycle lock: no poll meanwhile
    let done = () => {};
    reading = new Promise(resolve => { done = resolve; });
    clearTimeout(timer);
    try {
      const view = s.markFaculty(await h.api(base, "GET", undefined, {}, READ_TIMEOUT));
      if (!running()) return;
      // A dialog or an unsaved grade keeps the page as it is; the next read brings it up to date.
      const busy = document.querySelector("dialog[open]") || host.querySelector('[data-unsaved="true"]');
      if (busy && state.view && !force) return;
      state.view = view;
      syncPlan(view);
      draw();
      drawSecondary();
      drawer.redraw();
    } catch (error) {
      if (running() && !state.view) drawError(error);
    } finally {
      reading = null;
      done();
      schedule();
    }
  }

  // --- the Final Quality Audit (grading-certification-audit.js): its own read, polled only while an audit runs -------------------
  function drawAudit() {
    if (running()) patchChildren(auditHost, auditMarkup(audit, state.view, state, h));
  }
  async function loadAudit() {
    clearTimeout(auditTimer);
    try {
      const data = await h.api(auditUrl(base));
      if (!running()) return;
      const was = audit?.audit;
      audit = data;
      drawAudit();
      // A finished audit may have been acted on elsewhere: its "changed since" marks come with the next read.
      if (was && auditRunning(was) && !auditRunning(data.audit)) h.toast(data.audit?.status === "complete" ? "The quality audit finished." : "The quality audit stopped.");
    } catch { /* The audit is extra: the page stands without it. */ }
    if (running() && auditRunning(audit?.audit)) auditTimer = setTimeout(() => { if (document.hidden) auditPaused = true; else void loadAudit(); }, 5000);
  }
  function startAudit() {
    if (auditRunning(audit?.audit)) { auditHost.scrollIntoView({block: "start", behavior: "smooth"}); return; }
    h.dialog("Final quality audit", "Reviews the final grades for consistency and fairness.", startDialogMarkup(audit, h), "Start audit", async () => {
      const started = await h.api(auditUrl(base), "POST", {request_id: crypto.randomUUID()});
      h.close();
      audit = {...(audit || {}), audit: started};
      drawAudit();
      auditHost.scrollIntoView({block: "start", behavior: "smooth"});
      auditTimer = setTimeout(() => void loadAudit(), 3000);
    });
  }

  // --- the question panel ---------------------------------------------------------------------------------------------------
  function drawQuestion() {
    if (!running()) return;
    if (!state.question || !state.questionData) { questionHost.innerHTML = state.question ? loadingMarkup("Reading this question's grades…", 4) : ""; return; }
    questionHost.innerHTML = questionPanelMarkup(state.questionData, {filter: state.qFilter, sort: state.qSort, hideNames: state.hideNames, groups: state.groups,
      historyOpen: state.open.has("cert-fold-batch-history")}, plan, h);
    renderMath(questionHost);
    // Bar lengths through the CSSOM (no inline style attribute: CSP).
    questionHost.querySelectorAll("[data-share]").forEach(bar => { bar.style.inlineSize = `${Math.max(0, Math.min(100, Number(bar.dataset.share) || 0))}%`; });
  }
  async function loadQuestion(qid, {quiet = false} = {}) {
    const token = ++questionToken;
    if (!quiet) { state.questionData = null; drawQuestion(); }
    try {
      const data = await h.api(`${base}/questions/${encodeURIComponent(qid)}`);
      if (!running() || token !== questionToken || state.question !== qid) return;
      state.questionData = data;
      drawQuestion();
    } catch (error) {
      if (running() && token === questionToken) questionHost.innerHTML = calloutMarkup({tone: "danger", title: "This question could not be read", body: error.message});
    }
  }
  async function openQuestion(qid, {filter = "all", group = null, focus = true} = {}) {
    if (!qid) return;
    state.question = qid;
    state.qFilter = filter || "all";
    const inconsistency = (state.view?.checks || []).find(c => c.id === "answer_inconsistency");
    state.groups = group !== null && group !== undefined ? [String(group)] : (inconsistency?.rows || []).filter(r => r.question_id === qid).map(r => String(r.group));
    address?.({question: qid});
    drawTables();
    await loadQuestion(qid);
    if (!running() || state.question !== qid) return;
    questionHost.scrollIntoView({block: "start", behavior: "smooth"});
    if (focus) questionHost.querySelector("#cert-question-title")?.focus({preventScroll: true});
  }
  function closeQuestion() {
    const was = state.question;
    state.question = ""; state.questionData = null; questionToken++;
    address?.({question: ""});
    questionHost.innerHTML = "";
    drawTables();
    tables.querySelector(`[data-cert-question-open="${CSS.escape(was)}"]`)?.focus({preventScroll: false});
  }

  // --- Excuse, Certify, Gradescope --------------------------------------------------------------------------------------------
  function excuse(student) {
    excuseDialog(h, state.view?.event_id, student, async () => { await read({force: true}); drawer.refresh(); });
  }
  async function certify() {
    const dialog = await loadDialog();
    dialog.openCertifyDialog(plan, h, {base, eventId: state.view?.event_id || "", students: () => state.view?.students || [],
      onBusy: busy => { state.hold = Math.max(0, state.hold + (busy ? 1 : -1)); if (!busy) schedule(); },
      onCertified: async () => { state.handoff = null; if (refresh) await refresh(); else await read({force: true}); },
      onSend: kind => startSend(kind),
      onStudent: row => { void openStudent(row); },
      onQuestion: qid => { void openQuestion(qid); }});
  }
  // A line added to every final grade of the open question in one section, or a batch's line edited or removed
  // (grading-certification-adjust.js): the page holds its polls while the apply holds the lifecycle lock, then reads the page and the
  // question again.
  async function adjust(action = "add", batchId = "") {
    const data = state.questionData;
    if (!data?.question) return;
    const batch = batchId ? (data.batches || []).find(b => b.batch_id === batchId) : null;
    if (batchId && !batch) return;
    const dialog = await loadAdjust();
    dialog.openAdjustDialog(plan, h, {base, question: data.question, sections: state.view?.sections || [], hideNames: state.hideNames, action, batch,
      certified: state.view?.certification?.state === "current",
      onBusy: busy => { state.hold = Math.max(0, state.hold + (busy ? 1 : -1)); if (!busy) schedule(); },
      onDone: async () => { await read({force: true}); if (state.question) await loadQuestion(state.question, {quiet: true}); drawer.refresh(); }});
  }
  function startSend(kind) {
    sendDialog(plan, h, () => {
      // The send is recorded, not finished: the page follows it and offers Check again when it settles (critique C13); the
      // Gradescope panel below shows it too (certify-2).
      state.handoff = {kind, send: null};
      draw();
      void followSend();
      // A written send carries the bubble sheets too (GRADESCOPE "THE BUBBLE SHEETS TOO"): both panels read it at once.
      for (const which of kind === "sheets" ? ["sheets"] : ["written", "sheets"]) void panels[which]?.then(panel => panel?.refresh());
    }, kind === "sheets" ? "sheets" : "written");
  }
  async function followSend() {
    clearTimeout(handoffTimer);
    const handoff = state.handoff;
    if (!handoff || !running()) return;
    try {
      const status = await h.api(GRADESCOPE_BASE(plan, handoff.kind));
      if (!running() || state.handoff !== handoff) return;
      handoff.send = (status.sends || [])[0] || {status: "complete", counts: {}};
      if (!document.querySelector("dialog[open]")) draw();
    } catch { /* the next turn tries again; the Gradescope section below shows its own errors */ }
    const live = !handoff.send || ["pending", "running"].includes(handoff.send.status);
    if (live && running() && state.handoff === handoff) handoffTimer = setTimeout(() => { if (document.hidden) handoffPaused = true; else void followSend(); }, HANDOFF_POLL);
    else if (!live && running()) void read({force: true});
  }
  async function openStudent(row, options = {}) {
    state.drawerSource = options.source || "students";
    await drawer.open(row, options);
  }

  // --- events (delegated to containers that are never replaced, so patched redraws keep them) ---------------------------------
  root.addEventListener("toggle", event => {
    const id = event.target?.id || "";
    if (!id.startsWith("cert-fold-")) return;
    if (event.target.open) { state.open.add(id); state.closed.delete(id); } else { state.open.delete(id); state.closed.add(id); }
  }, true);
  bindFilterChips(tables, "cert-panel", value => { state.tab = value === "students" ? "students" : "questions"; storeTab(state.tab); drawTables(); });
  bindFilterChips(tables, "cert-students", value => { state.filter = value; drawTables(); });
  bindFilterChips(main, "cert-dist", value => { state.dist = value; draw(); });
  // The audit report, one finding at a time (grading-certification-audit.js): a pile, a question, Previous / Next.
  const auditFocus = selector => auditHost.querySelector(selector)?.focus({preventScroll: true});
  bindFilterChips(auditHost, "cert-audit", value => {
    state.auditView = {pile: value, question: "", index: 0};
    drawAudit();
    auditFocus(`[data-filter-group="cert-audit"] [data-filter="${CSS.escape(value)}"]`);
  });
  auditHost.addEventListener("change", event => {
    if (!event.target.matches?.("[data-cert-audit-question]")) return;
    state.auditView = {...state.auditView, question: event.target.value, index: 0};
    drawAudit();
    auditFocus("[data-cert-audit-question]");
  });
  bindFilterChips(questionHost, "cert-responses", value => {
    state.qFilter = value;
    drawQuestion();
    questionHost.querySelector(`[data-filter-group="cert-responses"] [data-filter="${CSS.escape(value)}"]`)?.focus();
  });
  questionHost.addEventListener("change", event => {
    const target = event.target;
    if (target.matches("[data-cert-response-sort]")) { state.qSort = target.value; drawQuestion(); questionHost.querySelector("[data-cert-response-sort]")?.focus(); }
    else if (target.matches("[data-cert-hide-names]")) { state.hideNames = target.checked; storeHideNames(state.hideNames); drawQuestion(); questionHost.querySelector("[data-cert-hide-names]")?.focus(); }
  });
  main.addEventListener("change", async event => {
    const box = event.target;
    if (!box.matches?.("[data-cert-participation]")) return;
    box.disabled = true;
    try {
      await h.api(`/api/grading/assignments/${encodeURIComponent(plan.id)}/participation`, "PUT", {enabled: box.checked});
      h.toast(box.checked ? "Graded for participation: everyone with a paper now gets full credit." : "No longer graded for participation.");
      await read({force: true});
    } catch (error) {
      box.checked = !box.checked;
      h.toast(error.message, true);
    } finally {
      box.disabled = false;
    }
  });
  root.addEventListener("click", event => {
    const target = event.target;
    if (drawerHost.contains(target)) return;  // the drawer handles its own clicks
    const sort = target.closest?.("[data-cert-sort]");
    if (sort) {
      const key = sort.dataset.certSort;
      state.sort = state.sort.key === key ? {key, dir: state.sort.dir === "asc" ? "desc" : "asc"} : {key, dir: ["title", "version"].includes(key) ? "asc" : "desc"};
      drawTables();
      tables.querySelector(`[data-cert-sort="${CSS.escape(key)}"]`)?.focus();
      return;
    }
    const jump = target.closest?.("[data-cert-jump]");
    if (jump) {
      const [tab, filter] = jump.dataset.certJump.split(":");
      state.tab = tab === "students" ? "students" : "questions";
      if (filter) state.filter = s.STUDENT_TESTS[filter] ? filter : "all";
      storeTab(state.tab);
      drawTables();
      const panel = tables.querySelector("#cert-panel");
      panel?.scrollIntoView({block: "start", behavior: "smooth"});
      panel?.querySelector('[data-filter-group="cert-panel"] [aria-pressed="true"]')?.focus({preventScroll: true});
      return;
    }
    if (target.closest?.("[data-cert-show-all]")) {
      clearTimeout(searchTimer); searchToken++;
      state.filter = "all"; state.query = ""; state.matches = null; state.searchNote = ""; state.announce = "";
      const box = root.querySelector("[data-cert-search]");
      if (box) box.value = "";
      draw();
      return;
    }
    if (target.closest?.("[data-cert-retry]")) { void read({force: true}); return; }
    const auditMove = target.closest?.("[data-cert-audit-move]");
    if (auditMove) {
      state.auditView = {...state.auditView, index: Math.max(0, (Number(state.auditView.index) || 0) + Number(auditMove.dataset.certAuditMove))};
      drawAudit();
      auditFocus("[data-cert-audit-position]");  // the button pressed may now be disabled; the position line says where we are
      return;
    }
    const auditRun = target.closest?.("[data-cert-audit-run]");
    if (auditRun) { startAudit(); return; }
    const auditStop = target.closest?.("[data-cert-audit-stop]");
    if (auditStop) { void h.run(auditStop, async () => { audit = await h.api(`${auditUrl(base)}/${encodeURIComponent(auditStop.dataset.certAuditStop)}/cancel`, "POST", {}); drawAudit(); }); return; }
    const open = target.closest?.("[data-cert-open]");
    if (open) { void openStudent(open.dataset.certOpen, {from: open}); return; }
    const response = target.closest?.("[data-cert-response-open]");
    if (response) { void openStudent(response.dataset.certResponseOpen, {from: response, question: response.dataset.questionId, anonymous: state.hideNames, source: "question"}); return; }
    const questionOpen = target.closest?.("[data-cert-question-open]");
    if (questionOpen) { void openQuestion(questionOpen.dataset.certQuestionOpen, {filter: questionOpen.dataset.certFilter || "all", group: questionOpen.dataset.certGroup ?? null}); return; }
    if (target.closest?.("[data-cert-question-close]")) { closeQuestion(); return; }
    const adjustButton = target.closest?.("[data-cert-adjust]");
    if (adjustButton) { void h.run(adjustButton, () => adjust()); return; }
    const editBatch = target.closest?.("[data-cert-batch-edit]");
    if (editBatch) { void h.run(editBatch, () => adjust("edit", editBatch.dataset.certBatchEdit)); return; }
    const removeBatch = target.closest?.("[data-cert-batch-remove]");
    if (removeBatch) { void h.run(removeBatch, () => adjust("remove", removeBatch.dataset.certBatchRemove)); return; }
    const excuseButton = target.closest?.("[data-cert-excuse]");
    if (excuseButton) { const student = (state.view?.students || []).find(x => x.row === excuseButton.dataset.certExcuse); if (student) excuse(student); return; }
    const send = target.closest?.("[data-cert-send]");
    if (send) { startSend(send.dataset.certSend); return; }
    if (target.closest?.("[data-cert-publish]")) { publishDialog(plan, h, published); return; }
    // A check's "Open the Gradescope section": Send to Gradescope… is in the Gradescope fold only (2026-10-18 review item 10).
    const toGradescope = target.closest?.("[data-cert-gradescope]");
    if (toGradescope) { void showSend(toGradescope.dataset.certGradescope); return; }
    const certifyButton = target.closest?.("[data-cert-certify]");
    if (target.closest?.('[data-cert-blockers]')) {
      state.open.add('cert-fold-details'); draw();
      const details = main.querySelector('#cert-fold-details');
      details?.scrollIntoView({block: 'start'}); details?.querySelector('summary')?.focus();
      return;
    }
    if (certifyButton) {
      if (certifyButton.closest("[data-cert-handoff]")) { state.handoff = null; draw(); }
      void h.run(certifyButton, () => certify());
    }
  });
  root.addEventListener("input", event => {
    const box = event.target.closest?.("[data-cert-search]");
    if (!box) return;
    state.query = box.value;
    clearTimeout(searchTimer);
    if (state.query.trim() && state.tab !== "students") { state.tab = "students"; storeTab(state.tab); }
    if (!s.isDigitSearch(state.query)) {
      searchToken++;
      state.matches = null; state.searchNote = "";
      state.announce = state.query.trim() ? `${s.filterStudents(state.view?.students || [], {filter: state.filter, query: state.query}).length} students match` : "";
      draw();
      return;
    }
    // Four or more digits: the server compares them with the roster's UINs and answers with names (never a UIN).
    const token = ++searchToken, digits = s.digitsOf(state.query);
    searchTimer = setTimeout(async () => {
      try {
        const result = await h.api(`${base}/students/search`, "POST", {digits});
        if (!running() || token !== searchToken) return;
        state.matches = result.matches || [];
        state.searchNote = state.matches.length ? "Closest UINs first." : "No student's UIN is close to these digits.";
        state.announce = `${state.matches.length} students match`;
      } catch {
        if (!running() || token !== searchToken) return;
        state.matches = []; state.searchNote = "Search by UIN is not available right now. Type a name instead."; state.announce = "";
      }
      draw();
    }, SEARCH_DELAY);
  });
  // Shown again: the page reads at once, and the send and the audit it follows go on where their polls paused.
  const onVisible = () => {
    if (!running()) { document.removeEventListener("visibilitychange", onVisible); return; }
    if (document.visibilityState !== "visible") return;
    void read();
    if (handoffPaused) { handoffPaused = false; void followSend(); }
    if (auditPaused) { auditPaused = false; void loadAudit(); }
  };
  document.addEventListener("visibilitychange", onVisible);
  // Escape closes the open question when no student is open (the drawer's own Escape comes first while it is open).
  const offEscape = registerShortcut({key: "Escape", label: "Close the question", run: () => closeQuestion(), allowInInput: true,
    when: () => running() && Boolean(state.question) && !drawer.row && !typingText()});
  const stopEscape = () => { if (!running()) { offEscape(); drawer.dispose(); clearTimeout(handoffTimer); clearTimeout(auditTimer); window.removeEventListener("hashchange", stopEscape); } };
  window.addEventListener("hashchange", stopEscape);

  // --- the secondary sections: Gradescope (its panels keep their own state and polls), the files and the training downloads -------
  const fold = () => secondary.querySelector("#cert-fold-gradescope");
  const finalCount = view => (view?.questions || []).reduce((sum, q) => sum + Number(q.automatic || 0) + Number(q.instructor || 0), 0);
  const foldHint = view => !view?.gradescope?.configured ? "not set up"
    : view.gradescope.written?.grades ? `${Number(view.gradescope.written.grades.sent || 0).toLocaleString()} of ${Number(view.gradescope.written.grades.final || 0).toLocaleString()} final grades there` : "";
  function setFoldHint(text) {
    const summary = fold()?.querySelector(":scope > summary");
    if (!summary) return;
    let hint = summary.querySelector(".disclosure-hint");
    if (!text) { hint?.remove(); return; }
    if (!hint) { hint = document.createElement("span"); hint.className = "disclosure-hint"; summary.append(hint); }
    if (hint.textContent !== text) hint.textContent = text;
  }
  // After each read: the fold's hint, and the downloads once a grade becomes final (missed:certify[2]: they were frozen at mount).
  // Faculty's own Gradescope work after a certification moved (GRADESCOPE "AFTER CERTIFYING"): the mounted panels read again, so
  // their cards show the send and the publication Faculty made while the fold was open.
  // `afterMark`: where that work stood when the panels mounted or last read again.
  const facultyMark = () => JSON.stringify((state.view?.gradescope?.after || []).map(run => [run.id, run.state, run.doing, run.sent, run.published, run.publishing, run.made]));
  let afterMark = "";
  function followFaculty() {
    if (!mounted) return;
    const mark = facultyMark();
    if (afterMark !== mark) {
      for (const panel of [panels.written, panels.sheets, publishPanel]) void Promise.resolve(panel).then(found => found?.refresh?.());
    }
    afterMark = mark;
  }
  function drawSecondary() {
    if (!running() || !fold()) return;
    followFaculty();
    setFoldHint(foldHint(state.view));
    const sends = secondary.querySelector("[data-cert-automatic-sends]");
    if (sends) patchChildren(sends, automaticSendsMarkup(state.view, h));
    const ready = finalCount(state.view) > 0, node = secondary.querySelector("[data-export-downloads]");
    if (node && ready !== downloadsReady) {
      downloadsReady = ready;
      const template = document.createElement("template");
      template.innerHTML = downloadsMarkup(finalCount(state.view), h);
      node.replaceWith(template.content);
      bindDownloads(secondary, plan, h);
    }
  }
  // What a panel read (its `onStatus`): a send that started or settled there reads the page again, so the cards, the Questions'
  // Gradescope column, the filter and the checks follow it (certify-2); the written panel's coverage is the fold's hint.
  function panelStatus(kind, status) {
    const latest = (status?.sends || [])[0];
    const mark = latest ? `${latest.id}:${["pending", "running"].includes(latest.status)}` : "";
    if (seen[kind] !== undefined && seen[kind] !== mark && running()) {
      void read({force: true});
      // A written send started or settled: its bubble sheets went with it and its MC check follows, so the sheets panel, which
      // polls only while it saw a send of its own running, reads again (2026-10-27: it still said 0 of 324 after Exam 2's send).
      if (kind === "written") void panels.sheets?.then(panel => panel?.refresh());
    }
    seen[kind] = mark;
    const grades = status?.coverage?.grades;
    if (kind === "written" && status?.configured && grades) setFoldHint(`${Number(grades.sent || 0).toLocaleString()} of ${Number(grades.final || 0).toLocaleString()} final grades there`);
  }
  // Mounted when the fold first opens (perf-2): a closed fold reads nothing from the Gradescope routes.
  let mounted = false;
  function mountPanels() {
    if (mounted || !running()) return null;
    mounted = true;
    afterMark = facultyMark();
    panels.written = mountGradescope(secondary.querySelector("[data-gradescope-host]"), plan, h, running, "written", {onStatus: status => panelStatus("written", status)});
    if (plan.bubble?.sheets) panels.sheets = mountGradescope(secondary.querySelector("[data-gradescope-sheets-host]"), plan, h, running, "sheets", {onStatus: status => panelStatus("sheets", status)});
    // Publishing there, last: only on the instructor's request (2026-10-02), after the grades are sent.
    publishPanel = mountPublish(secondary.querySelector("[data-gradescope-publish-host]"), plan, h, running);
    return panels.written;
  }
  // A check's way to Send to Gradescope… (`kind`: written or sheets): the fold opens (its panels mount and read), comes into view, and
  // that panel's Send button (else the fold's title) takes the focus. It sends nothing itself.
  async function showSend(kind) {
    const details = fold();
    if (!details) return;
    details.open = true;
    mountPanels();
    await (kind === "sheets" ? panels.sheets : panels.written);
    if (!running()) return;
    const host = secondary.querySelector(kind === "sheets" ? "[data-gradescope-sheets-host]" : "[data-gradescope-host]");
    const send = host?.querySelector('[data-grading-action="gradescope-send"]:not([disabled])');
    (send || details).scrollIntoView({block: send ? "center" : "start", behavior: "smooth"});
    (send || details.querySelector(":scope > summary"))?.focus({preventScroll: true});
  }

  const training = h.api(`/api/grading/assignments/${plan.id}/training`).catch(() => null);
  // The gradebook's "See the graded paper" handed a student over in memory (handoff.js): taken as the page mounts, opened once read.
  const wanted = take(plan.id);
  await read({force: true});
  if (!running()) return;
  if (wanted && state.view?.students?.some(x => x.row === wanted.row)) void openStudent(wanted.row);
  void loadAudit();
  if (question) void openQuestion(question, {focus: false});
  const view = state.view, asked = open === "gradescope";
  downloadsReady = finalCount(view) > 0;
  // Open when anything is not on Gradescope. The viewer's own choice (a click, ui.js rememberDisclosures, 2026-10-02) is
  // remembered per plan (certify-2: one key served every plan) and separately under each default: a choice made while grades were
  // missing there applies again whenever they are missing, and one made while none was applies whenever none is.
  const opened = !view || (view.gradescope?.configured && Number(view.counts?.not_on_gradescope || 0) > 0);
  secondary.innerHTML = disclosureMarkup({label: "Gradescope", hint: foldHint(view), key: `grading:certification:gradescope:${plan.id}:${opened ? "open" : "closed"}`, open: opened, id: "cert-fold-gradescope", className: "certification-gradescope",
    body: `<div data-cert-automatic-sends>${automaticSendsMarkup(view, h)}</div><div data-gradescope-host></div>${plan.bubble?.sheets ? '<div data-gradescope-sheets-host></div>' : ""}<div data-gradescope-publish-host></div>`})
    + `<section class="certification-files" aria-labelledby="cert-files-title"><h3 id="cert-files-title">Files for your records</h3><div class="grading-export-columns">${downloadsMarkup(finalCount(view), h)}${trainingMarkup(plan, await training, h)}</div></section>`;
  if (!running()) return;
  bindDownloads(secondary, plan, h);
  const details = fold();
  details?.addEventListener("toggle", () => { if (details.open) mountPanels(); });
  if (asked && details) details.open = true;  // the address asked for it, whatever was remembered
  if (!details?.open) return;
  const written = mountPanels();
  if (!asked) return;
  // Asked for by the address: the fold in view and its first button focused once the send panel has read its status.
  await written;
  if (!running()) return;
  details.scrollIntoView({block: "start", behavior: "smooth"});
  (details.querySelector(".disclosure-body button:not([disabled]), .disclosure-body a[href]") || details.querySelector(":scope > summary"))?.focus({preventScroll: true});
}
