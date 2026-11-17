// The Certify dialog of Certification and Export (requirement, 2026-09-26).
// It opens with the preview (POST …/certification/preview, which writes nothing): every check again, exactly. Anything that blocks
// is listed with where to fix it and Certify is not offered. Otherwise each group to confirm has ONE tick naming its effect (the
// students who receive 0, those left out until their DR exam…), the Gradescope groups are either sent first through the existing
// Send dialog or not sent with a reason, and the gradebook column is chosen among the server's options (switching needs no new
// preview: every option is fingerprinted). With Gradescope set up and its Compute settings switch on, the dialog offers one more tick,
// on by default (requirement, 2026-10-12; GRADESCOPE "AFTER CERTIFYING"): "Send to Gradescope and publish the grades after certifying".
// Ticked, a Gradescope group left unanswered is one Faculty sends by itself afterwards, and the footer says so beside Certify. The server's default is the one live column with the assignment's name when there is
// exactly one (requirement, 2026-10-02), else a new column. A new column's category is chosen beside it (the server proposes one;
// rest-shell-1): it is in no fingerprint or digest, so choosing it needs no new preview either. Each student about to receive 0
// shows the absences recorded for them (requirement, 2026-10-02: display only, nothing is excused).
// Certify POSTs {request_id, fingerprint, confirmation:
// "certify", acknowledged, gradescope, gradebook}; the server checks all of it again and refuses (409) anything that changed. A request whose answer never came is
// kept in memory and only an explicit Retry sends the SAME body again (the server answers a repeated request ID with what it
// recorded; nothing is recorded twice).
import {calloutMarkup, tableMarkup, disclosureMarkup, runFormAction} from "./ui.js";
import {checkGroups, countedTitle, rowsCut, CERTIFY_CHANGES} from "./grading-certification-stats.js";
import {splitRow} from "./grading-certification-students.js";

const PREVIEW_TIMEOUT = 180000;
export const REASON_LIMIT = 500;
const GRADEBOOK = new Set(["gradebook_column", "gradebook_kept", "gradebook_sections", "gradebook_imported_replaced", "gradebook_cleared"]);
const GROUP = {gradescope_written: "written", gradescope_sheets: "sheets"};
const n = value => Number(value || 0).toLocaleString();
const plural = (count, one, many = `${one}s`) => `${n(count)} ${Number(count) === 1 ? one : many}`;
// A request whose outcome is unknown, per plan: {body}. Only an explicit Retry sends it again.
const unresolved = new Map();

// ------------------------------------------------------------------------------------------------------------------------------
// Pure pieces (tests/test_grading_certification.mjs)

// The gradebook option chosen (its key), or the server's default.
export function chosenOption(preview, key = "") {
  const options = preview?.gradebook?.options || [];
  return options.find(o => o.key === key) || options.find(o => o.key === preview?.gradebook?.selected) || options[0] || null;
}

// Every confirm group the instructor must answer for this option: the preview's own (the selected option's gradebook checks
// replaced by the chosen option's), in the server's order.
export function confirmGroups(preview, option) {
  const own = (preview?.confirm || []).filter(c => !GRADEBOOK.has(c.id));
  const book = (option?.checks || []).filter(c => c.level === "confirm");
  return [...own, ...book];
}

// The chosen option's information (values an import wrote that are replaced, earlier certified values cleared).
export const optionNotes = option => (option?.checks || []).filter(c => c.level === "info");

// What is still missing before Certify, as short phrases, from the ticks (Set of "<id>:<digest>"), the Gradescope decisions
// ({written: {skip, reason}}). There is no final tick (requirement, 2026-10-11: a repeat of what the groups above already confirm):
// clicking Certify is the confirmation, as Approve is on the review desk.
export function missingAnswers(preview, option, {ticks = new Set(), decisions = {}, after = undefined} = {}) {
  if (!preview) return ["the preview"];
  if (Number(preview.blocking || 0) > 0) return ["what blocks certification"];
  const missing = [];
  if (!option) missing.push("a gradebook column");
  for (const check of confirmGroups(preview, option)) {
    if (check.requires === "decision") {
      const answer = groupAnswer(check, decisions, afterOn(preview, after));
      const reason = String(answer.reason || "").trim();
      // Ticked "Do not send" without a usable reason: say that the reason is what is missing.
      if (!answer.skip) { if (!afterOn(preview, after)) missing.push(check.title); }  // unanswered: Faculty sends it afterwards
      else if (!reason) missing.push(`a reason for not sending (${check.title})`);
      else if (reason.length > REASON_LIMIT) missing.push(`a shorter reason, at most ${REASON_LIMIT} characters (${check.title})`);
    } else if (!ticks.has(`${check.id}:${check.digest}`)) missing.push(check.title);
  }
  return missing;
}

// Whether Faculty sends to Gradescope and publishes by itself after this certification: the server offers it (`preview.gradescope.after`)
// and the dialog's tick, on unless the instructor took it off, stands.
export const afterOn = (preview, after) => Boolean(preview?.gradescope?.after) && after !== false;

// A Gradescope group's answer: what the instructor chose in this dialog; else, with the Gradescope tick on, "Do not send" AGAIN where
// the last certification declined exactly these with a reason (the server's `prefill`): Faculty never starts sending by itself what
// was declined before. Unticked, it is sent.
export function groupAnswer(check, decisions = {}, on = false) {
  return decisions[GROUP[check.id]] || (on && check.prefill ? {skip: true, reason: check.prefill, carried: true} : {});
}

// The category a new column gets: the one chosen in the dialog (`undefined` until one is), else the server's proposal; null is
// Uncategorized.
export const chosenCategory = (preview, category) => category !== undefined ? category : preview?.gradebook?.proposed_category_id ?? null;

// The column choice in three groups (certify-1): the columns named like the assignment, a new column, then every other column. The
// server's order (and default) is kept within each; without a new column to make (the assignment's own column) there are no groups.
const folded = text => String(text ?? "").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
export function columnGroups(preview) {
  const options = preview?.gradebook?.options || [];
  const create = options.find(o => o.column?.action === "create");
  if (!create) return [{label: "", options}];
  const same = options.filter(o => o.column?.action === "use" && folded(o.item?.title) === folded(create.item?.title));
  const others = options.filter(o => o !== create && !same.includes(o));
  return [{label: "Same name", options: same}, {label: "New column", options: [create]}, {label: `Other columns (${n(others.length)})`, options: others}]
    .filter(group => group.options.length);
}

const categoryName = (preview, id) => (preview?.gradebook?.categories || []).find(c => (c.id ?? null) === (id ?? null))?.name || "";
// Only a gradebook with categories has a category to choose (Uncategorized alone is no choice).
const hasCategories = preview => (preview?.gradebook?.categories || []).some(c => c.id);

// Why the column would not count toward Overall (weighted mode: Uncategorized, or a category of weight 0), or "": a new column in
// the category chosen, an existing one (the same-named default included, requirement, 2026-10-02) in its own. An existing column's
// category the setup no longer has is no category (gradebook/compute: `uncounted`); it is moved in the gradebook's Setup.
export function uncountedText(preview, option, category) {
  const action = option?.column?.action;
  if (!action) return "";
  const create = action === "create", categories = preview?.gradebook?.categories || [];
  const id = (create ? chosenCategory(preview, category) : option.item?.category_id) ?? null;
  const chosen = categories.find(c => (c.id ?? null) === id) || (create ? null : categories.find(c => (c.id ?? null) === null));
  if (!chosen || chosen.counted !== false) return "";
  const none = chosen.id === null || chosen.id === undefined;
  if (create) {
    const title = option.item?.title || "This column";
    return none ? `${title} will not count toward the overall grade: this gradebook is weighted by category, and Uncategorized is not counted.`
      : `${title} will not count toward the overall grade: this gradebook is weighted by category, and “${chosen.name}” has weight 0.`;
  }
  const fresh = (preview?.gradebook?.options || []).some(o => o.column?.action === "create") ? ", or choose a new column here" : "";
  return `“${option.item?.title || "This column"}” will not count toward the overall grade: this gradebook is weighted by category, and `
    + (none ? `the column has no category. Choose its category in the gradebook's Setup${fresh}.`
      : `its category “${chosen.name}” has weight 0. Move it to a counted category in the gradebook's Setup${fresh}.`);
}

// A new column whose name a column of the gradebook already has (spaces and case ignored): certifying makes a second one, and both
// count (in the same category when the one chosen is the existing column's). With exactly one such column the server's default is
// that column (requirement, 2026-10-02), so this is said only once a new column is chosen instead, or when two share the name.
export function duplicateText(preview, option, category) {
  if (option?.column?.action !== "create") return "";
  const same = columnGroups(preview).find(group => group.label === "Same name")?.options || [];
  if (!same.length) return "";
  const title = option.item?.title || "Column", categorized = hasCategories(preview);
  const own = o => categorized ? ` (${categoryName(preview, o.item?.category_id ?? null) || "Uncategorized"})` : "";
  const id = chosenCategory(preview, category) ?? null;
  const chosen = (preview?.gradebook?.categories || []).find(c => (c.id ?? null) === id);
  const together = categorized && same.some(o => (o.item?.category_id ?? null) === id) ? `, ${chosen?.counted === false ? "" : "counted "}in the same category` : "";
  const existing = same.length === 1 ? `A column named “${same[0].item?.title || title}” already exists${own(same[0])}.`
    : `${n(same.length)} columns named “${title}” already exist: ${same.map(o => `“${o.item?.title || title}”${own(o)}`).join(", ")}.`;
  return `${existing} A new column makes ${same.length === 1 ? "a second" : "another"} “${title}”${together}. Choose ${same.length === 1 ? "it" : "one"} under Same name to record there instead.`;
}

// An existing column chosen (the default when it is the one with the assignment's name: requirement, 2026-10-02): where the values go, and what becomes of those already there, by the gradebook's one rule
// (gradebook/common.change_action): a value typed or cleared in Faculty stays unless Replace is ticked for it (the "you typed" group
// below lists them, with their Replace boxes); an imported value or an earlier pull is replaced. With nothing typed for these
// students (`kept` 0: no Replace box to tick) the line says so instead.
export function existingText(preview, option) {
  if (option?.column?.action !== "use") return "";
  const create = (preview?.gradebook?.options || []).find(o => o.column?.action === "create");
  const named = Boolean(create) && folded(option.item?.title) === folded(create.item?.title);
  const where = hasCategories(preview) ? ` in ${categoryName(preview, option.item?.category_id ?? null) || "Uncategorized"}` : "";
  const typed = Number(option.kept || 0) > 0 || (option.checks || []).some(c => c.id === "gradebook_kept");
  return `Certifying records into the existing column “${option.item?.title || "Column"}”${where}${named ? ", which has this assignment's name" : ""}. `
    + (typed ? "Values typed or cleared there in Faculty stay unless you tick Replace; values imported from Canvas or a file are replaced."
      : "Nothing there was typed in Faculty for these students; values imported from Canvas or a file are replaced.");
}

// The absences recorded for a student about to receive 0 (requirement, 2026-10-02): shown beside Excuse…,
// never acted on (ABSENCES NO-GRADEBOOK). `covers`: the absence includes the date the assessment was given in their section.
const ABSENCE_STATUS = {pending: "waiting for your decision", excused: "excused", not_excused: "not excused", withdrawn: "withdrawn"};
export const absenceText = absence => `Absence recorded ${absence.dates || absence.first_date || ""}${ABSENCE_STATUS[absence.status] ? ` · ${ABSENCE_STATUS[absence.status]}` : ""}`;

// The certify request exactly as the server expects it (grading/certification_rules.parse_request). Replace ticks count only for
// rows the chosen column keeps; a new column carries the category chosen (or proposed), an existing one its own.
export function certifyBody(preview, option, {ticks = new Set(), decisions = {}, replace = new Set(), requestId, category = undefined, after = undefined}) {
  const acknowledged = {}, gradescope = {};
  for (const check of confirmGroups(preview, option)) {
    if (check.requires === "decision") {
      const answer = groupAnswer(check, decisions, afterOn(preview, after));
      if (answer.skip) gradescope[GROUP[check.id]] = {decision: "do_not_send", reason: String(answer.reason || "").trim()};
      else if (afterOn(preview, after)) gradescope[GROUP[check.id]] = {decision: "send"};
    } else if (ticks.has(`${check.id}:${check.digest}`)) acknowledged[check.id] = check.digest;
  }
  const kept = new Set(((option?.checks || []).find(c => c.id === "gradebook_kept")?.rows || []).map(r => r.row));
  return {request_id: requestId, fingerprint: preview.fingerprint, confirmation: "certify", acknowledged, gradescope,
    gradebook: {replace: [...replace].filter(row => kept.has(row)).sort(),
      category_id: option?.column?.action === "create" ? chosenCategory(preview, category) : option?.item?.category_id ?? null, column: option?.column || {}},
    // Named only when ticked, so a certification without it is the request it always was.
    ...(afterOn(preview, after) ? {gradescope_after: true} : {})};
}

// The one tick of each group says what it does.
export function tickLabel(check) {
  const count = Number(check.count || check.rows?.length || 0);
  switch (check.id) {
    case "zero_no_work": return `${count === 1 ? "This student receives" : `These ${n(count)} students receive`} 0`;
    case "zero_missing_part": return `Count the missing part as 0 for ${count === 1 ? "this student" : `these ${n(count)} students`}`;
    case "dr_ahead": return `Leave ${count === 1 ? "this student" : `these ${n(count)} students`} out until their DR exam`;
    case "makeup_ahead": return `Leave ${count === 1 ? "this student" : `these ${n(count)} students`} out until their makeup`;
    case "duplicate_membership": return "Leave out the memberships without work";
    case "bubble_frq_version_mismatch": return "I checked these students' versions";
    case "unmatched_sheets": return "None of these sheets belongs to a student without one";
    case "zero_question_all": return count === 1 ? "I checked this question: every student scored 0" : "I checked these questions: every student scored 0";
    case "gradebook_column": return `Yes: ${String(check.title || "").charAt(0).toLowerCase()}${String(check.title || "").slice(1)}`;
    case "gradebook_kept": return "Keep the values I typed, except those ticked Replace";
    case "gradebook_sections": return "Do not record these students in this column";
    default: return "I confirm";
  }
}

// An option's name in the column choice: its own column check's title, else where it records.
export function optionLabel(option) {
  const own = (option?.checks || []).find(c => c.id === "gradebook_column");
  return own?.title || `Record in “${option?.item?.title || "the column"}”`;
}

// ------------------------------------------------------------------------------------------------------------------------------
// Markup

function rowsTable(check, h, {replace = null, excuse = false} = {}) {
  const rows = check.rows || [];
  if (!rows.length) return "";
  const heads = replace ? ["Replace", "Student", "Section", "Values"] : ["Student", "Section", "Detail"];
  const body = rows.map(r => {
    const name = r.question_id ? `<button type="button" class="text-button" data-certify-question="${h.e(r.question_id)}">${h.e(r.name)}</button>`
      : r.row ? `<button type="button" class="text-button" data-certify-student="${h.e(r.row)}">${h.e(r.name)}</button>` : h.e(r.name);
    const tick = replace ? `<td><input type="checkbox" data-certify-replace="${h.e(r.row)}" aria-label="${h.e(`Replace the value typed for ${r.name}`)}"${replace.has(r.row) ? " checked" : ""}></td>` : "";
    const more = excuse && r.row ? ` <button type="button" class="text-button" data-certify-excuse="${h.e(r.row)}">Excuse…</button>` : "";
    return `<tr data-certify-row="${h.e(r.row || r.question_id || "")}">${tick}<th scope="row">${name}</th><td>${h.e(r.section || "")}</td><td>${h.e(r.detail || "")}${more}${excuse ? absencesMarkup(r, h) : ""}</td></tr>`;
  });
  const shown = tableMarkup(h.e, heads, body, check.title);
  // Only a list cut at the server's cap is a part of the whole (a count may count grades over rows of students or questions).
  return rowsCut(check) ? `${shown}<p class="supporting-copy">The first ${h.e(n(rows.length))} of ${h.e(n(check.count))} are listed.</p>` : shown;
}

// An excused or undecided absence on the day it was given is marked for attention, one not excused is marked quietly; a withdrawn
// absence claims nothing, so it is only named (the server sends it no `covers`).
function absencesMarkup(row, h) {
  const list = row.absences || [];
  if (!list.length) return "";
  const mark = a => a.covers && a.status !== "withdrawn" ? ` <span class="chip" data-tone="${a.status === "excused" || a.status === "pending" ? "warning" : "neutral"}" data-certify-absence-covers>Covers the day it was given</span>` : "";
  return `<ul class="certification-absences" data-certify-absences>${list.map(a => `<li>${h.e(absenceText(a))}${mark(a)}</li>`).join("")}</ul>`;
}

function tickMarkup(check, state, h) {
  const key = `${check.id}:${check.digest}`;
  return `<label class="grading-check certification-tick"><input type="checkbox" data-certify-tick="${h.e(key)}"${state.ticks.has(key) ? " checked" : ""}>${h.e(tickLabel(check))}</label>`;
}

function excuseFormMarkup(student, h) {
  return `<div class="certification-excuse-form" data-certify-excuse-form="${h.e(student.row)}"><label class="field">Why is ${h.e(student.name)} excused? (recorded on the Exams page)<textarea data-certify-excuse-reason rows="2" maxlength="2000"></textarea></label>
    <div class="grading-actions"><button type="button" class="button" data-certify-excuse-save>Excuse</button><button type="button" class="text-button" data-certify-excuse-cancel>Cancel</button></div></div>`;
}

// `detail`: the words to show instead of the check's own (a new column's, for the category chosen in the dialog).
function confirmMarkup(check, state, h, detail = check.detail) {
  if (check.requires === "decision") return gradescopeMarkup(check, state, h);
  const replace = check.id === "gradebook_kept" ? state.replace : null;
  const excusing = check.id === "zero_no_work" && state.excusing ? excuseFormMarkup(state.excusing, h) : "";
  return `<fieldset class="certification-confirm" data-certify-confirm="${h.e(check.id)}"><legend>${h.e(countedTitle(check))}</legend>
    ${detail ? `<p class="supporting-copy">${h.e(detail)}</p>` : ""}${check.warning ? `<p class="grading-warning" role="status">${h.e(check.warning)}</p>` : ""}
    ${rowsTable(check, h, {replace, excuse: check.id === "zero_no_work"})}${excusing}
    ${check.action?.href ? `<p><a class="link-arrow" href="${h.e(check.action.href)}">${h.e(check.action.label)}</a></p>` : ""}
    ${tickMarkup(check, state, h)}</fieldset>`;
}

function gradescopeMarkup(check, state, h) {
  const group = GROUP[check.id], answer = groupAnswer(check, state.decisions, state.afterOn);
  const questions = (check.questions || []).map(q => `<tr><th scope="row">${h.e(q.title || q.question_id)}</th><td>${h.e(n(q.not_sent))}</td><td>${h.e(n(q.changed))}</td><td>${h.e(q.unsendable || "")}</td></tr>`);
  const table = questions.length ? tableMarkup(h.e, ["Question", "Not sent", "Changed since sent", "Can never be sent because"], questions, `${check.title}: questions`) : "";
  const send = check.action?.kind && check.sendable ? `<button type="button" class="button" data-certify-send="${h.e(group)}">${h.e(check.action.label)}</button>` : "";
  const reason = answer.reason ?? check.prefill ?? "";
  return `<fieldset class="certification-confirm certification-gradescope-choice" data-certify-confirm="${h.e(check.id)}"><legend>${h.e(countedTitle(check))}</legend>
    ${check.detail ? `<p class="supporting-copy">${h.e(check.detail)}</p>` : ""}${table}${rowsTable(check, h)}
    ${Number(check.never || 0) ? `<p class="supporting-copy">${h.e(n(check.never))} of these can never be sent from Faculty (a paper changed after its upload, or a rubric not imported from Gradescope's export).</p>` : ""}
    <div class="certification-gradescope-options">${state.afterOn ? `<div><p class="supporting-copy" data-certify-after-sends>Faculty sends these to Gradescope by itself after you certify.</p></div>`
      : send ? `<div><p class="supporting-copy">Send them now: this dialog closes and the page follows the send; once it has finished, Check again.</p>${send}</div>` : ""}
      <div><label class="grading-check certification-tick"><input type="checkbox" data-certify-skip="${h.e(group)}"${answer.skip ? " checked" : ""}>Do not send these to Gradescope</label>
        <label class="field">Why not (recorded with the certification)<textarea data-certify-reason="${h.e(group)}" rows="2" maxlength="${REASON_LIMIT}"${answer.skip ? "" : " disabled"}>${h.e(reason)}</textarea></label>
        ${answer.carried ? `<p class="supporting-copy" data-certify-prefilled>Not sent at the last certification, for this reason. Untick it and Faculty sends them after you certify.</p>`
          : check.prefill && answer.reason === undefined ? `<p class="supporting-copy" data-certify-prefilled>Filled in from the last certification, whose reason covered these.</p>` : ""}</div></div></fieldset>`;
}

// GRADESCOPE "AFTER CERTIFYING": the one tick that lets Faculty send to Gradescope and publish by itself once the grades are certified.
export function afterMarkup(preview, state, h) {
  if (!preview?.gradescope?.after) return "";
  const on = afterOn(preview, state.after);
  return `<fieldset class="certification-confirm" data-certify-after><legend>Gradescope</legend>
    <label class="grading-check certification-tick"><input type="checkbox" data-certify-after-tick${on ? " checked" : ""}>Send to Gradescope and publish the grades after certifying</label>
    <p class="supporting-copy">${h.e(on ? "Faculty makes the assignment there where a section has none, sends what is missing, and publishes each section to its students. A section that is not fully graded there stays unpublished, and this page says why."
      : "Nothing goes to Gradescope by itself: you send and publish from the Gradescope panel.")}</p></fieldset>`;
}

// The column as one select (certify-1: a term with 79 unlinked columns drew 80 radios, 2,206 px tall), grouped by columnGroups. An
// existing column names its category, so two columns with one title can be told apart.
function columnChoiceMarkup(preview, option, h) {
  const options = preview.gradebook?.options || [];
  if (options.length < 2) return "";
  const text = o => `${optionLabel(o)}${o.column?.action !== "create" && o.item?.category_id && categoryName(preview, o.item.category_id) ? ` · ${categoryName(preview, o.item.category_id)}` : ""}`;
  const choice = o => `<option value="${h.e(o.key)}"${o.key === option.key ? " selected" : ""}>${h.e(text(o))}</option>`;
  const groups = columnGroups(preview).map(group => group.label ? `<optgroup label="${h.e(group.label)}">${group.options.map(choice).join("")}</optgroup>` : group.options.map(choice).join("")).join("");
  return `<label class="field certification-column-choice">Column<select name="certify-column" data-certify-column-select>${groups}</select></label>`;
}

// A new column's category (rest-shell-1): the server's categories, Uncategorized last, preselected with its proposal.
function categoryMarkup(preview, option, state, h) {
  if (option.column?.action !== "create" || !hasCategories(preview)) return "";
  const chosen = chosenCategory(preview, state.category) ?? "";
  return `<label class="field certification-column-choice">Category<select data-certify-category>${(preview.gradebook.categories || []).map(c =>
    `<option value="${h.e(c.id ?? "")}"${(c.id ?? "") === chosen ? " selected" : ""}>${h.e(c.name)}</option>`).join("")}</select></label>`;
}

function gradebookMarkup(preview, option, state, h) {
  if (!option) return `<section class="certification-gradebook" data-certify-gradebook>${calloutMarkup({tone: "warning", title: "There is no gradebook column to record in", body: "Check again, or look at the term's gradebook setup."})}</section>`;
  const counts = option.counts || {};
  const words = [["add", "to add"], ["update", "to update"], ["same", "already the same"], ["kept", "kept as you typed them"], ["waiting", "waiting"], ["none", "not recorded"]];
  const facts = words.filter(([key]) => Number(counts[key] || 0)).map(([key, label]) => `<div data-count="${key}"><dt>${h.e(label)}</dt><dd>${h.e(n(counts[key]))}</dd></div>`).join("");
  const item = option.item || {}, create = option.column?.action === "create";
  const category = create ? chosenCategory(preview, state.category) : item.category_id ?? null;
  const named = hasCategories(preview) ? ` · ${categoryName(preview, category) || "Uncategorized"}` : "";
  const uncounted = uncountedText(preview, option, state.category), duplicate = duplicateText(preview, option, state.category);
  const existing = existingText(preview, option);
  // A new column's own check says the words for the category chosen here (the server sends each category's).
  const detail = check => create && check.id === "gradebook_column" && check.category_details ? check.category_details[category ?? ""] ?? check.detail : check.detail;
  const confirms = (option.checks || []).filter(c => c.level === "confirm").map(c => confirmMarkup(c, state, h, detail(c))).join("");
  const notes = optionNotes(option).map(c => `<div data-certify-note="${h.e(c.id)}">${calloutMarkup({tone: "info", title: c.title, body: c.detail || ""})}${rowsTable(c, h)}</div>`).join("");
  const warnings = option.key === preview.gradebook?.selected ? (preview.gradebook?.warnings || []) : [];
  return `<section class="certification-gradebook" data-certify-gradebook aria-labelledby="certify-gradebook-title"><h4 id="certify-gradebook-title">The gradebook</h4>
    ${columnChoiceMarkup(preview, option, h)}${categoryMarkup(preview, option, state, h)}<p class="supporting-copy" data-certify-column>${h.e(`“${item.title || "Column"}” · ${item.points ?? "?"} points${named}`)}${item.sections?.length ? " · the assignment's sections only" : ""}. ${afterOn(preview, state.after) ? "Nobody is emailed." : "Nothing is published or emailed."}</p>
    ${existing ? `<p class="supporting-copy" data-certify-existing>${h.e(existing)}</p>` : ""}
    ${duplicate ? `<p class="grading-warning" role="status" data-certify-duplicate>${h.e(duplicate)}</p>` : ""}${uncounted ? `<p class="grading-warning" role="status" data-certify-uncounted>${h.e(uncounted)}</p>` : ""}
    ${facts ? `<dl class="certification-counts" aria-label="What certifying writes in the gradebook">${facts}</dl>` : ""}
    ${warnings.length ? `<ul class="certification-warnings">${warnings.map(w => `<li>${h.e(w)}</li>`).join("")}</ul>` : ""}
    ${confirms}${notes}</section>`;
}

function changesMarkup(preview, h) {
  const changes = preview.changes || [];
  if (!changes.length) return "";
  const value = v => !v ? "–" : v.earned === null || v.earned === undefined ? String(v.status || "").replaceAll("_", " ") : `${v.earned}${v.status === "zero" ? " (0 confirmed)" : ""}`;
  const rows = changes.map(c => `<tr><th scope="row">${h.e(c.name)}</th><td>${h.e(c.section || "")}</td><td>${h.e(value(c.before))}</td><td>${h.e(value(c.after))}</td></tr>`);
  return disclosureMarkup({label: "Changes since the last certification", hint: changes.length, open: true, id: "certify-changes",
    body: tableMarkup(h.e, ["Student", "Section", "Certified", "Now"], rows, "Changes since the last certification")});
}

// What a certification does besides recording the values (certify-10; the page's Details say the same).
const changesWhat = h => disclosureMarkup({label: "What certifying changes", id: "certify-what", body: CERTIFY_CHANGES.map(text => `<p>${h.e(text)}</p>`).join("")});

export function previewMarkup(preview, state, h) {
  state = {...state, afterOn: afterOn(preview, state.after)};  // what the Gradescope groups say follows the tick
  const groups = checkGroups(preview.checks), snap = preview.snapshot || {};
  const option = chosenOption(preview, state.column);
  // Left out: DR students, students owed a makeup (MAKEUP OWED, 2026-10-07), and the empty one of two memberships (counted once).
  const dr = Number(snap.left_out_dr || 0), makeup = Number(snap.left_out_makeup || 0), twice = Math.max(0, Number(snap.left_out || 0) - dr - makeup);
  const summary = `<p class="section-lead" data-certify-summary>${h.e(`${n(snap.students)} students: ${n(snap.scored)} scored, ${n(snap.zero)} receive 0, ${n(snap.excused)} excused, ${n(dr)} left out until their DR exam${makeup ? `, ${n(makeup)} until their makeup` : ""}.`)}${twice ? ` ${h.e(`${plural(twice, "student")} in two sections counted once, in the section with their work.`)}` : ""}${Number(snap.missing_part || 0) ? ` ${h.e(`${plural(snap.missing_part, "student")} with a part counted 0.`)}` : ""}</p>`;
  const stale = state.stale ? `<div data-certify-stale>${calloutMarkup({tone: "warning", title: "Something changed since this check", body: state.stale, action: {label: "Check again", data: {certifyAgain: ""}}})}</div>` : "";
  const error = state.error ? `<div data-certify-error>${calloutMarkup({tone: state.error.retry ? "warning" : "danger", title: state.error.title, body: state.error.body,
    action: state.error.retry ? {label: "Retry request", data: {certifyRetry: ""}} : null})}</div>` : "";
  if (groups.block.length) {
    const block = groups.block.map(check => `<div data-certify-block="${h.e(check.id)}">${calloutMarkup({tone: "warning", title: check.title, body: check.detail || "",
      action: check.action?.href ? {label: check.action.label, href: check.action.href} : null})}${rowsTable(check, h)}</div>`).join("");
    return `${error}${summary}<h4>Finish these first</h4><p class="supporting-copy">Certifying is refused while any of these stands. Fix them, then Check again.</p>${block}${changesWhat(h)}`;
  }
  const confirm = confirmGroups(preview, null).map(check => confirmMarkup(check, state, h)).join("");
  const info = groups.info.filter(c => !GRADEBOOK.has(c.id));
  const infoBlock = info.length ? disclosureMarkup({label: "For your information", hint: info.length, id: "certify-info",
    body: info.map(c => `<div data-certify-info="${h.e(c.id)}">${calloutMarkup({tone: "neutral", title: c.title, body: c.detail || ""})}${rowsTable(c, h)}</div>`).join("")}) : "";
  return `${error}${stale}${summary}${confirm ? `<h4>Confirm before certifying</h4>${confirm}` : `<p class="supporting-copy">Nothing here needs your confirmation.</p>`}
    ${afterMarkup(preview, state, h)}${gradebookMarkup(preview, option, state, h)}${changesMarkup(preview, h)}${infoBlock}${changesWhat(h)}`;
}

// ------------------------------------------------------------------------------------------------------------------------------
// The dialog

/**
 * @param {any} plan
 * @param {any} h grading helpers
 * @param {{base: string, eventId?: string, students?: () => any[], onCertified?: (result: any) => Promise<void>, onBusy?: (busy: boolean) => void,
 *   onSend?: (kind: string) => void, onStudent?: (row: string) => void, onQuestion?: (id: string) => void}} options
 */
export function openCertifyDialog(plan, h, {base, eventId = "", students = () => [], onCertified = async () => {}, onBusy = () => {}, onSend = () => {}, onStudent = () => {}, onQuestion = () => {}} = {}) {
  const form = h.dialog("Certify grades", "Every grade is checked again. Certifying records them in the gradebook and emails nobody.",
    '<div data-certify-body><p class="loading-state" role="status">Checking every grade… this takes up to a minute.</p></div>', "Certify", async () => { await submit(); });
  form.classList.add("certification-dialog");
  const dialog = form.closest("dialog");
  const live = () => form.isConnected && Boolean(dialog?.open);
  const body = form.querySelector("[data-certify-body]"), submitButton = form.querySelector('button[type="submit"]');
  const footer = form.querySelector(".dialog-footer");
  footer.insertAdjacentHTML("afterbegin", `<p class="certification-final" data-certify-final-text>Certify records these grades in the gradebook.</p><button type="button" class="button" data-certify-again-footer hidden>Check again</button><p class="certification-missing" data-certify-missing aria-live="polite"></p>`);
  const finalText = footer.querySelector("[data-certify-final-text]"), againButton = footer.querySelector("[data-certify-again-footer]");
  const missingLine = footer.querySelector("[data-certify-missing]");
  submitButton.disabled = true;
  // `picked`: the column was chosen in the dialog (not the server's default), so Check again keeps it while the server still offers it.
  // `after`: the Gradescope tick, on until the instructor takes it off (it stays as chosen through Check again).
  const state = {preview: null, column: "", picked: false, category: undefined, ticks: new Set(), decisions: {}, replace: new Set(), stale: "", error: null,
    excusing: null, loading: false, requestId: "", after: undefined};

  function sync() {
    const option = chosenOption(state.preview, state.column);
    const missing = missingAnswers(state.preview, option, state);
    const blocked = !state.preview || Number(state.preview.blocking || 0) > 0;
    finalText.hidden = blocked;
    const says = (option?.item?.title ? `Certify records these grades in the gradebook column “${option.item.title}”.` : "Certify records these grades in the gradebook.")
      + (afterOn(state.preview, state.after) ? " Faculty then sends them to Gradescope and publishes them." : "");
    if (finalText.textContent !== says) finalText.textContent = says;
    submitButton.hidden = blocked;
    submitButton.disabled = state.loading || Boolean(state.stale) || Boolean(state.error) || missing.length > 0 || form.dataset.saving === "true";
    // What Certify still waits for, said in the footer beside it (review UX3: a disabled button's tooltip reaches no keyboard, touch
    // or screen reader user).
    const still = missing.length && !blocked && !state.loading && !state.stale && !state.error ? `Still to answer: ${missing.join(" · ")}.` : "";
    submitButton.title = still;
    if (missingLine.textContent !== still) missingLine.textContent = still;
    againButton.hidden = state.loading || (!state.preview && !state.error);
  }

  function draw() {
    if (!live()) return;
    const open = new Set([...body.querySelectorAll("details[open]")].map(d => d.id).filter(Boolean));
    const scroller = body.closest(".dialog-body"), scroll = scroller?.scrollTop ?? 0;
    body.innerHTML = state.preview ? previewMarkup(state.preview, state, h)
      : state.error ? `<div data-certify-error>${calloutMarkup({tone: state.error.retry ? "warning" : "danger", title: state.error.title, body: state.error.body,
        action: state.error.retry ? {label: "Retry request", data: {certifyRetry: ""}} : {label: "Check again", data: {certifyAgain: ""}}})}</div>` : "";
    open.forEach(id => body.querySelector(`#${CSS.escape(id)}`)?.setAttribute("open", ""));
    if (scroller) scroller.scrollTop = scroll;
    sync();
  }

  async function preview() {
    if (state.loading) return;
    state.loading = true; state.error = null; state.stale = ""; state.excusing = null;
    body.innerHTML = '<p class="loading-state" role="status">Checking every grade… this takes up to a minute.</p>';
    sync();
    onBusy(true);
    try {
      const found = await h.api(`${base}/preview`, "POST", {}, {}, PREVIEW_TIMEOUT);
      if (!live()) return;
      // A new preview asks for every tick again; the reasons typed stay (they are the instructor's words, not a confirmation), and so
      // do the column and category the instructor chose while the server still offers them (Check again after Excuse… must not put
      // back the default: both choices may carry the same title). Otherwise the server's default, and its proposed category.
      const book = found.gradebook || {};
      state.picked = state.picked && (book.options || []).some(o => o.key === state.column);
      if (!state.picked) state.column = book.selected || "";
      if (state.category !== undefined && !(book.categories || []).some(c => (c.id ?? null) === state.category)) state.category = undefined;
      state.preview = found; state.ticks = new Set(); state.requestId = "";
      // With the Gradescope tick on, an unanswered group is SENT, so a "Do not send" the instructor ticked stands through Check again.
      state.decisions = Object.fromEntries(Object.entries(state.decisions).map(([group, answer]) => [group, {skip: afterOn(found, state.after) && Boolean(answer.skip), reason: answer.reason}]));
      const kept = new Set(((chosenOption(found, state.column)?.checks || []).find(c => c.id === "gradebook_kept")?.rows || []).map(r => r.row));
      state.replace = new Set([...state.replace].filter(row => kept.has(row)));
    } catch (error) {
      if (!live()) return;
      state.preview = null;
      state.error = {title: "The grades could not be checked", body: error.message};
    } finally {
      state.loading = false;
      onBusy(false);
      draw();
    }
  }

  async function deliver(payload) {
    let result;
    onBusy(true);
    try {
      result = await h.api(base, "POST", payload, {}, PREVIEW_TIMEOUT);
    } catch (error) {
      const status = Number(error.status || 0);
      if (status >= 400 && status < 500) {
        // A definite refusal: nothing was written. The preview no longer counts; Check again reads the grades as they are now.
        unresolved.delete(plan.id);
        state.error = {title: status === 409 ? "The grades changed while you were checking" : "Certifying was refused", body: `${error.message} Nothing was recorded.`};
        if (state.preview) state.stale = "Check again to see the grades and the checks as they are now.";
        state.requestId = "";
        draw();
        return;
      }
      // No answer, an unreadable one or a server failure: the outcome is unknown. Keep the exact request for an explicit retry.
      unresolved.set(plan.id, {body: payload});
      state.error = {title: "No answer came back", body: "The certification may or may not have been recorded. Retry request sends the same certification again: if it was recorded, nothing is recorded twice.", retry: true};
      draw();
      return;
    } finally {
      onBusy(false);
    }
    unresolved.delete(plan.id);
    const certified = result?.certification || {};
    const title = certified.gradebook?.title || chosenOption(state.preview, state.column)?.item?.title || "";
    if (live()) h.close();
    h.toast(result?.replay ? "This certification was already recorded; nothing was recorded twice." : `Certified. ${plural(certified.students ?? 0, "student")} recorded${title ? ` in ${title}` : " in the gradebook"}.`);
    await onCertified(result);
  }

  async function submit() {
    const option = chosenOption(state.preview, state.column);
    const missing = missingAnswers(state.preview, option, state);
    if (missing.length) throw new Error(`Still to answer: ${missing.join("; ")}.`);
    state.requestId ||= crypto.randomUUID();
    await deliver(certifyBody(state.preview, option, {...state, requestId: state.requestId}));
  }

  body.addEventListener("change", event => {
    const target = event.target;
    if (target.matches("[data-certify-tick]")) { if (target.checked) state.ticks.add(target.dataset.certifyTick); else state.ticks.delete(target.dataset.certifyTick); }
    else if (target.matches("[data-certify-skip]")) {
      const group = target.dataset.certifySkip, reason = body.querySelector(`[data-certify-reason="${CSS.escape(group)}"]`);
      state.decisions[group] = {skip: target.checked, reason: reason?.value ?? ""};
      if (reason) { reason.disabled = !target.checked; if (target.checked) reason.focus(); }
    } else if (target.matches("[data-certify-after-tick]")) {
      // What the Gradescope groups ask, and what Certify says it does, follow the tick.
      state.after = target.checked; state.requestId = "";
      draw();
      body.querySelector("[data-certify-after-tick]")?.focus();
      return;
    } else if (target.matches("[data-certify-replace]")) { if (target.checked) state.replace.add(target.dataset.certifyReplace); else state.replace.delete(target.dataset.certifyReplace); }
    else if (target.matches('[name="certify-column"]')) {
      // Every option was checked by the preview: switching asks for its own confirmations and needs no new preview.
      state.column = target.value; state.picked = true; state.replace = new Set();
      for (const key of [...state.ticks]) if (key.startsWith("gradebook_")) state.ticks.delete(key);
      state.requestId = "";
      draw();
      body.querySelector('[name="certify-column"]')?.focus();
      return;
    } else if (target.matches("[data-certify-category]")) {
      // In no fingerprint or digest: the request changes, the preview stands. The column's tick is asked again, since its words now
      // name the category chosen (as a column switch asks for its ticks again).
      state.category = target.value || null; state.requestId = "";
      for (const key of [...state.ticks]) if (key.startsWith("gradebook_column:")) state.ticks.delete(key);
      draw();
      body.querySelector("[data-certify-category]")?.focus();
      return;
    } else return;
    state.requestId = "";  // other content is another request
    sync();
  });
  body.addEventListener("input", event => {
    const target = event.target;
    if (!target.matches("[data-certify-reason]")) return;
    const group = target.dataset.certifyReason;
    state.decisions[group] = {skip: Boolean(state.decisions[group]?.skip), reason: target.value};
    state.requestId = "";
    sync();
  });
  body.addEventListener("click", event => {
    const target = event.target;
    if (target.closest?.("[data-certify-again]")) { void preview(); return; }
    if (target.closest?.("[data-certify-retry]")) {
      const record = unresolved.get(plan.id);
      if (record) { state.error = null; void runFormAction(form, () => deliver(record.body)).then(sync); }
      return;
    }
    const sendButton = target.closest?.("[data-certify-send]");
    if (sendButton) { h.close(); onSend(sendButton.dataset.certifySend); return; }
    const student = target.closest?.("[data-certify-student]");
    if (student) { h.close(); onStudent(student.dataset.certifyStudent); return; }
    const question = target.closest?.("[data-certify-question]");
    if (question) { h.close(); onQuestion(question.dataset.certifyQuestion); return; }
    if (target.closest?.("a[href^='#']")) { h.close(); return; }  // a link to where something is fixed: the dialog steps aside
    const excuse = target.closest?.("[data-certify-excuse]");
    if (excuse) {
      const row = excuse.dataset.certifyExcuse;
      const found = (state.preview?.confirm || []).flatMap(c => c.rows || []).find(r => r.row === row);
      state.excusing = found ? {row, name: found.name, section: found.section} : null;
      draw();
      body.querySelector("[data-certify-excuse-reason]")?.focus();
      return;
    }
    if (target.closest?.("[data-certify-excuse-cancel]")) { state.excusing = null; draw(); return; }
    const save = target.closest?.("[data-certify-excuse-save]");
    if (save) void h.run(save, async () => {
      const reason = String(body.querySelector("[data-certify-excuse-reason]")?.value || "").trim();
      if (!reason) throw new Error("Write the reason for excusing this student.");
      const who = state.excusing, key = splitRow(who?.row);
      const participant = students().find(s => s.row === who?.row);
      if (!key || !eventId || !participant) throw new Error("Excuse this student on the Exams page.");
      // The Exams page's own record; its version is the participant row's (a 409 says it changed meanwhile).
      await h.api(`/api/exams/${encodeURIComponent(eventId)}/participants/${encodeURIComponent(key.section)}/${encodeURIComponent(key.enrollment)}`, "PATCH",
        {version: participant.participant_version, expectation: "excused", reason});
      if (!live()) return;
      h.toast(`${who.name} is excused.`);
      state.excusing = null;
      state.stale = `${who.name} is excused now, so the students who receive 0 changed. Check again before certifying.`;
      draw();
    });
  });
  // Certifying is an explicit click on Certify: Enter on a tick must not submit the form, now that no final tick stands before it.
  form.addEventListener("keydown", event => { if (event.key === "Enter" && event.target instanceof HTMLInputElement) event.preventDefault(); });
  againButton.addEventListener("click", () => { void preview(); });

  const previous = unresolved.get(plan.id);
  if (previous) {
    // The earlier request's outcome is unknown: offer only its explicit retry, or a fresh check.
    state.error = {title: "An earlier certify request got no answer", body: "Retry request sends that same certification again (nothing is recorded twice), or Check again to start over.", retry: true};
    draw();
  } else void preview();
  return form;
}
