// Final Quality Audit on Certification and Export (requirement, 2026-09-27). The rubric model reads each question's final grades as compact rows, then
// checks what it finds against the scans of the responses named. The findings INFORM: nothing here changes a grade or holds back
// certifying. Each response a finding names opens that student's grade on that question in the ordinary drawer, where the instructor
// changes it through the one review desk. Read only: this module draws GET .../certification/audit and starts or stops an audit.
import {calloutMarkup, disclosureMarkup, filterChipsMarkup} from "./ui.js";
import {shortDate} from "./grading-guide.js";

export const auditUrl = base => `${base}/audit`;
export const auditRunning = audit => ["pending", "running"].includes(audit?.status);
const SEVERITY = {high: ["danger", "High"], medium: ["warning", "Medium"], low: ["neutral", "Low"]};
const KIND = {inconsistent: "Similar work graded differently", misapplied: "Rubric line applied against its wording", outlier: "Stands apart from similar work"};
const VERDICT = {confirmed: ["success", "Held up against the scans"], partly: ["warning", "Partly held up against the scans"], not_confirmed: ["neutral", "Did not hold up against the scans"]};
const CHECK = {same_answer: "Same final answer, different rubric lines", version_gap: "Versions score far apart", section_gap: "A section scores far from the others"};

const chip = (tone, label) => `<span class="chip" data-tone="${tone}">${label}</span>`;

function studentLabel(view, state, ref, h) {
  const student = (view?.students || []).find(s => s.row === ref.row);
  return h.e(state.hideNames || !student ? ref.ref || "Response" : student.name);
}

function responseButton(view, state, question, ref, h) {
  const score = ref.score === null || ref.score === undefined ? "" : ` · ${h.e(ref.score)}`;
  return `<button type="button" class="text-button" data-cert-response-open="${h.e(ref.row)}" data-question-id="${h.e(question)}">${studentLabel(view, state, ref, h)}${score}</button>`;
}

function lineLabel(entry, id, h) {
  const line = entry.line_text?.[id];
  return line ? `<span title="${h.e(line.text)}">${h.e(id)} (${h.e(line.points > 0 ? "+" : "")}${h.e(line.points)})</span>` : h.e(id);
}

function suggestionsMarkup(view, state, entry, suggestions, h) {
  if (!suggestions?.length) return "";
  return `<ul class="certification-audit-suggestions">${suggestions.map(s => `<li><strong>${studentLabel(view, state, s, h)}</strong>: ${[
    s.add?.length ? `add ${s.add.map(id => lineLabel(entry, id, h)).join(", ")}` : "",
    s.remove?.length ? `remove ${s.remove.map(id => lineLabel(entry, id, h)).join(", ")}` : ""].filter(Boolean).join("; ")}${s.note ? ` <span class="supporting-copy">· ${h.e(s.note)}</span>` : ""}</li>`).join("")}</ul>`;
}

function findingMarkup(view, state, entry, f, h) {
  const [tone, severity] = SEVERITY[f.severity] || SEVERITY.low;
  const confirm = f.confirm || {};
  const verdict = VERDICT[confirm.verdict];
  const suggestions = confirm.verdict ? confirm.suggestions : f.suggestions;
  return `<article class="certification-audit-finding" data-cert-audit-finding>
    <header>${chip(tone, severity)} <strong>${h.e(KIND[f.kind] || f.kind)}</strong>${f.source === "image_sample" ? ` ${chip("info", "Found in the image sample")}` : ""}${verdict ? ` ${chip(verdict[0], verdict[1])}` : confirm.skipped ? ` ${chip("neutral", "Not checked against the scans")}` : ""}${f.changed?.length ? ` ${chip("info", `${f.changed.length} grade${f.changed.length === 1 ? "" : "s"} changed since`)}` : ""}</header>
    <p>${h.e(f.explanation)}</p>
    ${f.lines?.length ? `<p class="supporting-copy">Rubric lines: ${f.lines.map(id => lineLabel(entry, id, h)).join(", ")}</p>` : ""}
    <p class="certification-audit-responses">${f.refs.map(ref => responseButton(view, state, entry.question_id, ref, h)).join(" ")}</p>
    ${suggestions?.length ? `<p class="supporting-copy">Suggested${confirm.verdict ? " after the scan check" : ""}:</p>${suggestionsMarkup(view, state, entry, suggestions, h)}` : ""}
    ${confirm.explanation ? `<details class="certification-audit-scan"><summary>What the scan check saw</summary><p>${h.e(confirm.explanation)}</p></details>` : confirm.skipped ? `<p class="supporting-copy">${h.e(confirm.skipped)}</p>` : ""}
    ${confirm.shown_refs?.length < f.refs.length ? `<p class="supporting-copy">The scan check covered ${confirm.shown_refs.length} of these ${f.refs.length} responses.</p>` : ""}
  </article>`;
}

function sampleMarkup(view, state, entry, h, open = false) {
  const sample = entry.image_sample;
  if (!sample) return "";
  return `<details class="disclosure"${open ? " open" : ""}><summary>Image sample · ${h.e(sample.read)} of ${h.e(sample.selected)} read</summary>
    <p class="supporting-copy">The same grades are selected again while the eligible work and scores stay the same, with zero, partial and full credit represented where available. The model read the images before seeing the saved grades. Its opinions are not human-verified accuracy measurements.</p>
    <p>${(sample.refs || []).map(ref => responseButton(view, state, entry.question_id, ref, h)).join(" ")}</p>
    ${sample.error ? `<p class="supporting-copy">${h.e(sample.error)}</p>` : ""}
    ${sample.summary ? `<p>${h.e(sample.summary)}</p>` : ""}
    ${sample.changed?.length ? `<p class="supporting-copy">${h.e(sample.changed.length)} sampled grades changed since this audit.</p>` : ""}
    ${(sample.readings || []).map(r => `<details><summary>${h.e(r.ref)} · reading before grades${r.uncertain ? " · uncertain" : ""}</summary><pre>${h.e(r.work)}</pre><p>${h.e(r.reason)}</p></details>`).join("")}
  </details>`;
}

// ONE FINDING AT A TIME (requirement, 2026-09-29). The findings fall into piles, those that
// held up against the scans first and those that did not last; a pile (chips) and a question (picker) choose which, the most severe
// first, and Previous / Next step through them. What is chosen is the page's browser state (`state.auditView`); the report is unchanged.
export const PILES = [["held", "Held up"], ["partly", "Partly held up"], ["unchecked", "Not checked against the scans"], ["samples", "Image samples"],
  ["checks", "Automatic checks"], ["not", "Did not hold up"]];
const RANK = {high: 0, medium: 1, low: 2};

/** Every item of a finished report, each in one pile: model findings by their scan check, image samples, automatic checks. */
export function auditItems(result) {
  const order = new Map((result.questions || []).map((q, i) => [q.question_id, i]));
  const byId = new Map((result.questions || []).map(q => [q.question_id, q]));
  const items = [];
  (result.questions || []).forEach(q => {
    (q.findings || []).forEach(f => {
      const verdict = f.confirm?.verdict;
      items.push({pile: verdict === "confirmed" ? "held" : verdict === "partly" ? "partly" : verdict === "not_confirmed" ? "not" : "unchecked",
        question_id: q.question_id, label: q.label, q, f, rank: RANK[f.severity] ?? 3});
    });
    if (q.image_sample) items.push({pile: "samples", question_id: q.question_id, label: q.label, q, rank: 3});
  });
  (result.checks || []).forEach(c => items.push({pile: "checks", question_id: c.question_id || "", label: c.title, q: byId.get(c.question_id), c, rank: 3}));
  return items.map((item, i) => ({...item, i}))
    .sort((a, b) => (a.rank - b.rank) || ((order.get(a.question_id) ?? 999) - (order.get(b.question_id) ?? 999)) || (a.i - b.i));
}

/** Where the viewer stands: the chosen pile if it has anything (else the first that does), the chosen question if the pile has it. */
export function auditPlace(items, chosen = {}) {
  const counts = Object.fromEntries(PILES.map(([key]) => [key, items.filter(item => item.pile === key).length]));
  const pile = counts[chosen.pile] ? chosen.pile : (PILES.find(([key]) => counts[key])?.[0] || "");
  const inPile = items.filter(item => item.pile === pile);
  const question = chosen.question && inPile.some(item => item.question_id === chosen.question) ? chosen.question : "";
  const shown = question ? inPile.filter(item => item.question_id === question) : inPile;
  const index = Math.max(0, Math.min(shown.length - 1, Number(chosen.index) || 0));
  return {counts, pile, question, inPile, shown, index};
}

function checkMarkup(view, state, c, h) {
  return `<article class="certification-audit-finding" data-cert-audit-check="${h.e(c.kind)}"><header><strong>${h.e(CHECK[c.kind] || c.kind)}</strong> ${chip("neutral", "No model: measured from the grades")}</header>
    <p>${h.e(c.detail)}</p>${c.responses?.length ? `<p class="certification-audit-responses">${c.responses.map(ref => responseButton(view, state, c.question_id, ref, h)).join(" ")}</p>` : ""}</article>`;
}

function itemMarkup(view, state, item, h) {
  if (item.c) return checkMarkup(view, state, item.c, h);
  if (item.pile === "samples") return sampleMarkup(view, state, item.q, h, true);
  const about = item.q.summaries?.length ? `<details class="certification-audit-about"><summary>What the audit said about ${h.e(item.q.label)}</summary><p class="supporting-copy">${h.e(item.q.summaries.join(" "))}</p></details>` : "";
  return findingMarkup(view, state, item.q, item.f, h) + about;
}

function viewerMarkup(view, state, result, h) {
  const items = auditItems(result);
  if (!items.length) return `<p class="supporting-copy">No finding: every question's grades read as consistent.</p>`;
  const place = auditPlace(items, state.auditView);
  const chips = filterChipsMarkup("cert-audit", PILES.filter(([key]) => place.counts[key]).map(([value, label]) => ({value, label, count: place.counts[value]})),
    place.pile, {label: "Findings"});
  const questions = [...new Map(place.inPile.map(item => [item.question_id, item.label])).entries()].filter(([id]) => id);
  const picker = questions.length > 1 ? `<label class="certification-sort-choice certification-audit-picker">Question<select data-cert-audit-question>`
    + `<option value="">All questions (${place.inPile.length})</option>`
    + questions.map(([id, label]) => `<option value="${h.e(id)}"${id === place.question ? " selected" : ""}>${h.e(label)} (${place.inPile.filter(item => item.question_id === id).length})</option>`).join("")
    + `</select></label>` : "";
  const item = place.shown[place.index], total = place.shown.length;
  const nav = `<div class="certification-audit-nav"><p class="certification-audit-position" data-cert-audit-position tabindex="-1"><strong>${place.index + 1}</strong> of ${total}${item.label ? ` · ${h.e(item.label)}` : ""}</p>`
    + `<div class="grading-actions"><button type="button" class="button" data-cert-audit-move="-1"${place.index ? "" : " disabled"}>← Previous</button>`
    + `<button type="button" class="button" data-cert-audit-move="1"${place.index < total - 1 ? "" : " disabled"}>Next →</button></div></div>`;
  return `<div class="certification-audit-toolbar">${chips}${picker}</div><section class="certification-audit-viewer" data-cert-audit-viewer aria-label="${h.e(`${place.index + 1} of ${total}`)}">${nav}${itemMarkup(view, state, item, h)}</section>`;
}

function statusMarkup(audit) {
  const p = audit.progress || {};
  if (auditRunning(audit)) {
    return calloutMarkup({tone: "info", title: `Auditing ${audit.responses.toLocaleString()} final grades of ${audit.questions} question${audit.questions === 1 ? "" : "s"}`,
      body: `${audit.model} has read ${p.read || 0} of ${p.requests || 0} sets of grades${p.checked ? `, and checked ${p.checked} finding${p.checked === 1 ? "" : "s"} against the scans` : ""}. Nothing is changed while it runs.`,
      action: {label: "Stop audit", data: {certAuditStop: audit.id}}});
  }
  if (audit.status === "failed") {
    return calloutMarkup({tone: audit.cancelled ? "neutral" : "warning", title: audit.cancelled ? "The audit was stopped" : "The audit could not be completed", body: audit.error || ""});
  }
  return "";
}

/** The audit section: nothing until an audit exists; its status while it runs; when done, its summary and one finding at a time.
 * A finished audit is one fold (certify-11: always open, it pushed the tables about 700 px down): open while it is newer than the
 * certification (or nothing is certified), and the instructor's own open or close is remembered per plan. */
export function auditMarkup(data, view, state, h) {
  const audit = data?.audit;
  if (!audit) return "";
  const result = audit.result;
  const when = audit.updated_at ? new Date(audit.updated_at).toLocaleString() : "";
  let body = statusMarkup(audit);
  if (result) {
    const failed = (result.questions || []).filter(q => q.error);
    body += `<p class="certification-audit-summary" data-cert-audit-summary>${h.e(result.summary)} <span class="supporting-copy">${h.e(audit.model)} · ${h.e(when)}. Findings inform; nothing was changed.</span></p>`
      + (failed.length ? `<details class="certification-audit-about"><summary>${failed.length} question${failed.length === 1 ? "" : "s"} could not be audited</summary><ul>${failed.map(q => `<li><strong>${h.e(q.label)}</strong>: ${h.e(q.error)}</li>`).join("")}</ul></details>` : "")
      + viewerMarkup(view, state, result, h);
  }
  if (result && !auditRunning(audit) && audit.status !== "failed") {
    const certified = view?.certification?.created_at || "";
    const day = shortDate(audit.updated_at);
    const newer = !certified || !(Date.parse(certified) >= Date.parse(audit.updated_at));  // an unreadable date leaves it open
    // Opened or closed on this page, it stays so through the patched redraws. The remembered choice (a click, ui.js
    // rememberDisclosures) is per audit and per default, so a certification newer than the audit folds it again whatever was
    // chosen while it was open by default.
    const open = state?.open?.has("cert-fold-audit") ? true : state?.closed?.has("cert-fold-audit") ? false : newer;
    // The hint is short (the label keeps its line): the date and the findings, the summary sentence being inside.
    const totals = result.totals || {}, findings = Number(totals.findings);
    const brief = Number.isFinite(findings) ? `${findings.toLocaleString()} finding${findings === 1 ? "" : "s"}${Number(totals.confirmed) ? ` · ${Number(totals.confirmed).toLocaleString()} held up against the scans` : ""}`
      : String(result.summary || "").split(/(?<=[.;])\s/)[0];
    const fold = disclosureMarkup({label: "Final quality audit", hint: [day, brief].filter(Boolean).join(" · "), body, open, id: "cert-fold-audit",
      key: view?.plan_id ? `grading:certification:audit:${view.plan_id}:${audit.id || ""}:${newer ? "open" : "closed"}` : ""});
    return `<section class="grading-panel certification-audit" data-cert-audit aria-label="Final quality audit">${fold}</section>`;
  }
  return `<section class="grading-panel certification-audit" data-cert-audit aria-labelledby="cert-audit-title"><h4 id="cert-audit-title">Final quality audit</h4>${body}</section>`;
}

/** The start dialog's text: what the audit reads, who reads it, and that it changes nothing. */
export function startDialogMarkup(data, h) {
  const model = data?.model;
  return `<p>${h.e(model || "The Rubric drafting and review model")} reads every final grade of this assignment, question by question and version by version, and reports grades that look inconsistent or unfair: similar work graded differently, a rubric line applied against its wording or clarification, a grade that stands apart. What it finds is then checked against the scans of the responses it names.</p>
    <p>It also samples up to three automatic grades per question and version across available score ranges. For those, it reads the images before seeing the saved transcript or grade, then compares its reading with the record. This adds model calls even when the text audit finds no concerns.</p>
    <p class="supporting-copy">It changes no grade and does not hold back certifying. You act on a finding with the ordinary review editor. It runs on the model's connection${model ? "" : " (choose one in Compute settings first)"} and can take several minutes for a large assignment.</p>`;
}
