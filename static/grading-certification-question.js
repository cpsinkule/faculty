// Certification and Export: one question's panel (requirement, 2026-09-26). It draws GET …/certification/questions/<qid>: the
// rubric in its original wording with how often each line is in a FINAL grade, the distribution of final scores, and every response
// with who decided it, the models' flags and whether it is on Gradescope. Filters and sort are browser state; "Hide names" is a
// per-browser preference (localStorage, a convenience only). Nothing here decides or changes a grade: a response opens the student's
// drawer, where a change is an ordinary review, and a rubric-level change links to Correct wording or Edit rubric, which regrade.
// "Add a line to every grade…" opens the batch adjustment (grading-certification-adjust.js, 2026-09-27): a named point adjustment on
// every final grade of this question in one section, one ordinary review each, previewed first. Every grade's adjustments are named
// beside its rubric lines, and "Lines added to every grade" lists the question's batches still on some grade, each with Edit… and
// Remove… (the same dialog), and their history.
import {filterChipsMarkup, disclosureMarkup} from "./ui.js";
import {mathText} from "./grading-math.js";
import {fmt, pct} from "./grading-certification-stats.js";

const EMPTY = "–";
export const HIDE_KEY = "faculty:certification:hide-names";
export const storedHideNames = () => { try { return globalThis.localStorage?.getItem(HIDE_KEY) === "1"; } catch { return false; } };
export const storeHideNames = on => { try { globalThis.localStorage?.setItem(HIDE_KEY, on ? "1" : "0"); } catch { /* a convenience only */ } };

const num = value => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
const count = (value, one, many = `${one}s`) => `${Number(value || 0).toLocaleString()} ${Number(value) === 1 ? one : many}`;

// Points with their sign, as the instructor reads an adjustment: "+2", "−1.5".
export function signedPoints(points) {
  const value = Number(points);
  if (points === null || points === undefined || points === "" || !Number.isFinite(value)) return "";
  const text = Math.abs(value).toLocaleString(undefined, {maximumFractionDigits: 2});
  return value < 0 ? `−${text}` : `+${text}`;
}
export const adjustmentText = a => `${signedPoints(a.points)} ${a.reason}`;
const where = batch => batch.section_id === "all" ? "every section" : `section ${batch.section}`;

// One line of a batch's history: when, what it did, to how many grades and where.
export function historyText(batch) {
  const day = new Date(batch.created_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
  const line = `“${batch.reason}” (${signedPoints(batch.points)})`;
  const what = batch.action === "remove" ? `Removed ${line} from ${count(batch.changed, "grade")}`
    : batch.action === "edit" ? `Changed “${batch.old?.reason}” (${signedPoints(batch.old?.points)}) to ${line} on ${count(batch.changed, "grade")}`
      : `Added ${line} to ${count(batch.changed, "grade")}`;
  return `${day} · ${what} · ${where(batch)}`;
}

// The question's batch lines still on some grade (each with Edit… and Remove…), and every batch's history, newest first.
export function batchesMarkup(batches, h, {historyOpen = false} = {}) {
  const all = batches || [];
  if (!all.length) return "";
  const live = all.filter(b => b.action !== "remove" && Number(b.carrying || 0) > 0);
  const rows = live.map(b => `<li data-cert-batch="${h.e(b.batch_id)}"><span class="certification-batch-line"><strong>${h.e(signedPoints(b.points))}</strong> ${h.e(b.reason)}</span>`
    + `<span class="supporting-copy">${h.e(`${where(b)} · on ${Number(b.carrying).toLocaleString()} of ${count(b.changed, "grade")} it changed`)}</span>`
    + `<span class="certification-batch-actions"><button type="button" class="button" data-cert-batch-edit="${h.e(b.batch_id)}">Edit…</button><button type="button" class="danger-button" data-cert-batch-remove="${h.e(b.batch_id)}">Remove…</button></span></li>`).join("");
  const history = [...all].reverse().map(b => `<li data-cert-batch-history="${h.e(b.batch_id)}">${h.e(historyText(b))}</li>`).join("");
  return `<section class="certification-batches" data-cert-batches aria-labelledby="cert-batches-title"><h5 id="cert-batches-title">Lines added to every grade</h5>`
    + (live.length ? `<ul class="certification-batch-list">${rows}</ul>` : `<p class="supporting-copy">No batch line is on any grade now.</p>`)
    + disclosureMarkup({label: "History", hint: all.length, id: "cert-fold-batch-history", open: historyOpen, body: `<ul class="certification-batch-history">${history}</ul>`}) + `</section>`;
}
const FINAL = r => r.decided?.kind === "instructor" || r.decided?.kind === "automatic";

// `groups`: the answer groups of this question whose same final answer earned different scores (the answer_inconsistency check's
// rows); a response carries its `answer_group` when the server reads it.
export const RESPONSE_FILTERS = [
  ["all", "All"], ["zero", "Zero"], ["full", "Full credit"], ["automatic", "Automatic"], ["instructor", "Decided by you"],
  ["flagged", "Still flagged"], ["settled", "Settled by a reviewing model"], ["overridden", "Changed by you"], ["not_final", "Not final"], ["gradescope", "Not on Gradescope"],
  ["inconsistent", "Same answer, different scores"],
];

export function responseTests(question, groups = []) {
  const max = num(question?.max), inconsistent = new Set(groups.map(g => String(g)));
  return {
    all: () => true,
    zero: r => FINAL(r) && num(r.score) === 0,
    full: r => FINAL(r) && max !== null && num(r.score) === max,
    automatic: r => r.decided?.kind === "automatic",
    instructor: r => r.decided?.kind === "instructor",
    flagged: r => Boolean(r.flagged),
    settled: r => Boolean(r.settled),
    overridden: r => Boolean(r.overridden),
    not_final: r => !FINAL(r),
    gradescope: r => FINAL(r) && ["not_sent", "changed"].includes(r.gradescope),
    inconsistent: r => Boolean(r.answer_group) && inconsistent.has(String(r.answer_group)),
  };
}

// The filters worth offering: every one with at least one response, and always All (a filter asked for by a link is kept).
export function responseFilterCounts(data, groups = [], keep = "") {
  const tests = responseTests(data?.question, groups), responses = data?.responses || [];
  return RESPONSE_FILTERS.map(([value, label]) => ({value, label, count: responses.filter(tests[value]).length}))
    .filter(option => option.value === "all" || option.count > 0 || option.value === keep);
}

export const RESPONSE_SORTS = [["name", "Name"], ["score_desc", "Score, high first"], ["score_asc", "Score, low first"], ["section", "Section"]];

export function sortResponses(rows, sort = "name") {
  const byName = (a, b) => String(a.name).localeCompare(String(b.name), undefined, {sensitivity: "base"}) || String(a.section).localeCompare(String(b.section), undefined, {numeric: true});
  const score = r => num(r.score);
  const compare = {
    name: byName,
    section: (a, b) => String(a.section).localeCompare(String(b.section), undefined, {numeric: true}) || byName(a, b),
    score_desc: (a, b) => (score(a) === null) - (score(b) === null) || (score(b) ?? 0) - (score(a) ?? 0) || byName(a, b),
    score_asc: (a, b) => (score(a) === null) - (score(b) === null) || (score(a) ?? 0) - (score(b) ?? 0) || byName(a, b),
  }[sort] || byName;
  return [...rows].sort(compare);
}

export function filterResponses(data, {filter = "all", sort = "name", groups = []} = {}) {
  const tests = responseTests(data?.question, groups), test = tests[filter] || tests.all;
  return sortResponses((data?.responses || []).filter(test), sort);
}

// The distinct final scores as bars (a question's scores are few: 0, 2, 4…), scaled to the tallest, drawn as SVG rectangles.
export function scoreHistogramMarkup(stats, h, label = "Final scores") {
  const bins = (stats?.histogram || []).map(b => ({score: String(b.score), count: Number(b.count || 0)}));
  if (!bins.length) return `<p class="supporting-copy">No final score yet.</p>`;
  const most = Math.max(...bins.map(b => b.count)), top = 36, width = 100 / bins.length;
  const rects = bins.map((bin, i) => {
    const height = Math.max(bin.count ? 1 : 0, Math.round((most ? bin.count / most : 0) * top * 10) / 10);
    return `<rect x="${(i * width + 0.6).toFixed(2)}" y="${(top - height).toFixed(2)}" width="${Math.max(0.5, width - 1.2).toFixed(2)}" height="${height}"><title>${h.e(`${bin.score} points: ${bin.count}`)}</title></rect>`;
  }).join("");
  return `<figure class="certification-histogram certification-score-histogram" data-cert-score-histogram><svg viewBox="0 0 100 ${top}" preserveAspectRatio="none" role="img" aria-label="${h.e(`${label}. ${bins.map(b => `${b.score} points: ${b.count}`).join(", ")}`)}" focusable="false">${rects}</svg>`
    + `<ol class="certification-histogram-counts" data-bins="${bins.length}" aria-hidden="true">${bins.map(b => `<li><b>${h.e(b.count)}</b><span>${h.e(b.score)}</span></li>`).join("")}</ol></figure>`;
}

const DECIDED = {team_consensus: "team agreed", final_arbitrator: "arbitrator", flag_review: "flag reviewer", benchmark: "benchmark",
  choice_reader: "multiple-choice reader", jev_blank: "left blank (reader and Jev)"};
export function decidedChip(decided, h) {
  if (decided?.kind === "instructor") return `<span class="chip" data-tone="info">You · ${h.e(decided.status === "manual" ? "manual" : "approved")}</span>`;
  if (decided?.kind === "automatic") return `<span class="chip" data-tone="neutral">Automatic${decided.basis ? ` · ${h.e(DECIDED[decided.basis] || String(decided.basis).replaceAll("_", " "))}` : ""}</span>`;
  return `<span class="chip" data-tone="warning">Not final</span>`;
}

// The models' original flags in plain words, most telling first (review UX6: the internal names told the instructor nothing).
const FLAG_WORDS = {disagreement: "graders disagreed", reading: "reading in doubt", grading: "held for grading review", arbitrator: "the arbitrator ruled on it",
  flag_reviewer: "the flag reviewer looked at it", finalization: "held by the rubric finalization", risk: "risk checks held it", reasons: "models gave review reasons",
  waived: "checks waived by your policy"};
const FLAG_ORDER = Object.keys(FLAG_WORDS);
export const flagWords = name => FLAG_WORDS[name] || String(name || "").replaceAll("_", " ");
/** A grade's flag names once each, most telling first. */
export const orderedFlags = flags => [...new Set(flags || [])].sort((a, b) => (FLAG_ORDER.indexOf(a) + 1 || 99) - (FLAG_ORDER.indexOf(b) + 1 || 99));
/** Who settled a grade whose every flag a reviewing model ruled on (`settled`). */
export const settledBy = names => names.includes("arbitrator") && names.includes("flag_reviewer") ? "Settled by the reviewing models"
  : names.includes("arbitrator") ? "Settled by the arbitrator" : "Upheld by the flag reviewer";
// ONE chip: the most telling flag and how many more, every flag named in its title; the expanded view lists the models' reasons. A
// grade whose only marks are a reviewing model's ruling (`settled`, certification_rules.settled) gets a neutral chip saying who
// settled it (2026-09-27: "arbitrator did not settle it" was shown on the 284 Quiz 2 grades the arbitrator had settled).
export function flagChips(flags, h, {settled = false} = {}) {
  const names = orderedFlags(flags);
  if (!names.length) return "";
  if (settled) {
    return `<span class="chip" data-tone="neutral" data-cert-settled title="${h.e("The models flagged this grade and a reviewing model ruled on every flag; nothing is left for you.")}">${h.e(settledBy(names))}</span>`;
  }
  const more = names.length > 1 ? ` +${names.length - 1}` : "";
  return `<span class="chip" data-tone="warning" title="${h.e(`Flagged by the models: ${names.map(flagWords).join("; ")}`)}">${h.e(`Flagged: ${flagWords(names[0])}${more}`)}</span>`;
}

const GRADESCOPE = {sent: ["On Gradescope", "success"], changed: ["Changed since sent", "warning"], not_sent: ["Not sent", "warning"]};
export function gradescopeChip(state, h) {
  const [label, tone] = GRADESCOPE[state] || [];
  return label ? `<span class="chip" data-tone="${tone}">${h.e(label)}</span>` : "";
}

/**
 * The panel of one question. `state`: {filter, sort, hideNames, groups, historyOpen}; `plan`: the grading plan (links to its rubric
 * steps).
 * @returns {string}
 */
export function questionPanelMarkup(data, state, plan, h) {
  const q = data.question || {}, stats = q.stats || {}, lines = new Map((data.lines || []).map(l => [l.id, l]));
  const title = `${q.title || "Question"}${q.version ? ` · Version ${q.version}` : ""}`;
  const numbers = new Map((q.rubric || []).map(item => [item.id, item.number]));
  const rubric = (q.rubric || []).map(item => {
    const line = lines.get(item.id) || {count: 0, pct: 0};
    return `<tr data-rubric-line="${h.e(item.id)}"><th scope="row">${h.e(item.number)}</th><td>${mathText(item.description, h)}</td><td>${h.e(fmt(item.points))}</td><td>${h.e(fmt(line.count, 0))}</td><td><span class="certification-bar" data-share="${h.e(Math.round(num(line.pct) ?? 0))}" aria-hidden="true"></span>${h.e(pct(line.pct))}</td></tr>`;
  }).join("");
  const facts = [["n", fmt(stats.n, 0)], ["Mean", `${fmt(stats.mean)} / ${fmt(q.max)}`], ["Median", fmt(stats.median)], ["SD", fmt(stats.sd)], ["Min", fmt(stats.min)], ["Max", fmt(stats.max)]];
  const filters = responseFilterCounts(data, state.groups, state.filter);
  const filter = filters.some(f => f.value === state.filter) ? state.filter : "all";
  const rows = filterResponses(data, {filter, sort: state.sort, groups: state.groups});
  const sort = `<label class="certification-sort-choice">Sort<select data-cert-response-sort>${RESPONSE_SORTS.map(([value, label]) => `<option value="${value}"${value === state.sort ? " selected" : ""}>${h.e(label)}</option>`).join("")}</select></label>`;
  const hide = `<label class="certification-hide-names"><input type="checkbox" data-cert-hide-names${state.hideNames ? " checked" : ""}> Hide names</label>`;
  const body = rows.map((r, i) => {
    const who = state.hideNames ? `Response ${i + 1}` : r.name;
    const selected = (r.selected || []).map(id => numbers.get(id) || id).join(", ") || ((r.adjustments || []).length ? "" : EMPTY);
    const adjustments = (r.adjustments || []).map(a => `<span class="certification-adjustment" data-cert-adjustment>${h.e(adjustmentText(a))}</span>`).join("");
    return `<tr data-response="${h.e(r.response_id)}"><th scope="row"><button type="button" class="text-button certification-open" data-cert-response-open="${h.e(r.row)}" data-question-id="${h.e(q.question_id)}"><span class="certification-name">${h.e(who)}</span></button></th>`
      + `<td>${h.e(r.section ?? "")}</td><td>${r.score === null || r.score === undefined ? EMPTY : `${h.e(fmt(r.score))} / ${h.e(fmt(q.max))}`}</td><td>${h.e(selected)}${adjustments}</td>`
      + `<td>${decidedChip(r.decided, h)}${r.overridden ? ' <span class="chip" data-tone="info">Changed by you</span>' : ""}</td><td>${flagChips(r.flags, h, {settled: r.settled}) || EMPTY}</td><td>${gradescopeChip(r.gradescope, h) || EMPTY}</td></tr>`;
  }).join("");
  const table = rows.length ? `<div class="data-table certification-table" tabindex="0" role="region" aria-label="${h.e(`Responses to ${title}`)}"><table><thead><tr><th scope="col">${state.hideNames ? "Response" : "Student"}</th><th scope="col">Section</th><th scope="col">Score</th><th scope="col">Rubric lines and adjustments</th><th scope="col">Decided by</th><th scope="col">Models' flags</th><th scope="col">Gradescope</th></tr></thead><tbody>${body}</tbody></table></div>`
    : `<p class="supporting-copy">No response in this filter.</p>`;
  const adjust = plan && (data.responses || []).some(FINAL) ? `<div class="certification-question-adjust"><button type="button" class="button" data-cert-adjust="${h.e(q.question_id)}">Add a line to every grade…</button><p class="supporting-copy">A named point adjustment on this question's final grades in one section, or every section. The rubric does not change and nothing is regraded.</p></div>` : "";
  const links = plan ? `<p class="certification-question-links">A change to the rubric itself regrades every response: <a class="link-arrow" data-cert-rubric-link href="#grading/${h.e(plan.id)}?tab=answer-key&amp;focus=${h.e(encodeURIComponent(q.question_id))}">Correct wording</a> <a class="link-arrow" data-cert-rubric-edit href="#grading/${h.e(plan.id)}?tab=rubrics&amp;focus=${h.e(encodeURIComponent(q.question_id))}">Edit rubric</a></p>` : "";
  return `<section class="grading-panel certification-question" aria-labelledby="cert-question-title" data-cert-question-panel="${h.e(q.question_id)}">
    <header class="certification-question-head"><h4 id="cert-question-title" tabindex="-1">${h.e(title)} <small>worth ${h.e(fmt(q.max))}</small></h4><button type="button" class="icon-button" data-cert-question-close aria-label="Close this question" title="Close (Escape)">×</button></header>
    <div class="certification-question-summary">${scoreHistogramMarkup(stats, h, `Final scores of ${title}`)}<dl class="certification-facts">${facts.map(([k, v]) => `<div><dt>${h.e(k)}</dt><dd>${h.e(v)}</dd></div>`).join("")}</dl></div>
    ${rubric ? `<div class="data-table certification-rubric" tabindex="0" role="region" aria-label="${h.e(`Rubric of ${title}`)}"><table><caption>Rubric lines in the final grades (original wording)</caption><thead><tr><th scope="col">Line</th><th scope="col">Description</th><th scope="col">Points</th><th scope="col">Final grades</th><th scope="col">Share</th></tr></thead><tbody>${rubric}</tbody></table></div>` : ""}
    ${adjust}${batchesMarkup(data.batches, h, {historyOpen: Boolean(state.historyOpen)})}${links}
    <div class="certification-question-tools">${filterChipsMarkup("cert-responses", filters, filter, {label: "Responses"})}<div class="certification-question-options">${sort}${hide}</div></div>
    ${table}</section>`;
}
