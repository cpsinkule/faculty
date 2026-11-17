// Presentation only: eligibility, prices and the start fingerprint come from the server.
import { viewLink } from "./file-viewer.js";
export const methodHelp = {
  cloud_standard: "Two graders assess each response. Disagreements and other flags receive an additional review.",
  local: "Read and grade on this machine, using the configured local models.",
  hybrid: "Mathpix reads the handwriting; local models grade the saved reading.",
};

export function batchForm(questions, modes, mode, selectedResponses, h) {
  return `<div class="grading-batch-setup">
    <section class="grading-batch-choices" aria-labelledby="batch-choices-title">
      <h3 id="batch-choices-title">Choose responses</h3>
      ${selectedResponses ? '<p class="supporting-copy">Only the responses you selected are included.</p>' : ''}
      ${h.select("Question", "question", [["all", "All approved questions"], ...questions])}
      <div class="grading-batch-fields">
        ${h.select("Responses", "selection", [["new", "New, reset or changed since grading"], ["all", "All eligible, including regrading"]], selectedResponses ? "all" : "new")}
        ${h.field("Papers per question", "sample", "", "number", 'min="1" max="5000" step="1" placeholder="All eligible papers" aria-describedby="batch-sample-hint"')}
      </div>
      <p class="supporting-copy" id="batch-sample-hint">Leave the limit empty to include every eligible paper.</p>
      <div class="grading-batch-method">
        ${h.select("Grading method", "mode", modes, mode)}
        <p class="supporting-copy" id="batch-method-help" data-method-help></p>
      </div>
      <details class="grading-batch-disclosure" data-regrading-options><summary>Regrading options</summary>
        <p class="supporting-copy">A response graded again whose new grade passes every check is finalized with it, replacing your earlier decision (kept in its history). A flagged one waits for you. Recalculating points only never replaces a decision.</p>
        ${h.select("Recompute", "operation", [["recognize", "Read handwriting and grade"], ["grade", "Grade again using saved readings"], ["rescore", "Recalculate points only"]])}
      </details>
    </section>
    <section class="grading-batch-outlook" aria-labelledby="batch-preview-title">
      <header><h3 id="batch-preview-title">Batch preview</h3>${h.button("preview", "Refresh preview")}</header>
      <div data-batch-preview></div>
    </section>
  </div>`;
}

export function batchReadiness(result, mode, operation) {
  const cloud = !["local", "hybrid"].includes(mode) && operation !== "rescore";
  // The estimate is information only (2026-09-23): spending limits are the OpenRouter and Vercel accounts' own.
  const unknown = cloud && result.estimate.estimated_cost === null;
  // Why Start cannot be used, said with the way on: blocked responses are listed below; with none, either everything was graded
  // already (grading it again is one choice away) or nothing is ready yet.
  const reason = result.eligible.length ? ""
    : result.skipped.length ? "No eligible responses. Change your choices or check the blocked responses below."
    : result.existing_count ? `Every response was already graded (${result.existing_count.toLocaleString()}). To grade them again, choose “All eligible, including regrading” under Responses.`
    : "No response of these questions is ready to grade yet.";
  return {cloud, unknown, reason};
}

// The models a batch will use, on one visible line: "Models · primary: X · second: Y". Role names are the workspace's own.
const ROLE = {primary: "primary", second: "second", verifier: "reviewer", reader: "reader", flag_reviewer: "flag reviewer", rubric_reviewer: "rubric reviewer"};
export function modelsLine(roles, h, changeModels = false) {
  // direct_reading is the reader's own extra request, not another model.
  const entries = Object.entries(roles || {}).filter(([role, r]) => r?.endpoint && role !== "direct_reading");
  if (!entries.length) return "";
  const names = entries.map(([role, r]) => `${ROLE[role] || role.replaceAll("_", " ")}: ${r.endpoint}`).join(" · ");
  return `<p class="grading-batch-models-line" data-batch-models><span>Models · ${h.e(names)}</span>${changeModels ? '<button type="button" class="text-button" data-grading-action="change-models">Change models…</button>' : ""}</p>`;
}

// Questions a batch leaves out because their rubric finalization does not stand (2026-09-28): one question's changed rubric holds back
// only that question, and the rest of the batch goes on.
function heldQuestions(result, h, titles) {
  const held = result.held_questions || [];
  if (!held.length) return '';
  return `<div class="grading-warning" role="status" data-held-questions><p><strong>${h.e(held.length)} question${held.length === 1 ? '' : 's'} left out of this batch</strong> until ${held.length === 1 ? 'its' : 'their'} rubric finalization is confirmed again:</p><ul>${held.map(item => `<li>${h.e(titles.get(item.question_id) || 'A question')}: ${h.e(item.reason)}.</li>`).join('')}</ul></div>`;
}

// Responses graded before that this batch grades again (`selection: "all"`, not a recalculation): a new grade that passes every
// check replaces the current one, the instructor's own decisions included (acceptance.REVIEW_RULE). Said before Start, never
// refused; `certified` (the plan's current certification date) says those grades were certified (grading-review-4). A response a
// batch reset left without a proposal was graded before too (its version moved) but has no grade now, hence "any grade … now".
export function regradeWarning(result, operation, h, certified = "") {
  const again = result.selection === "all" && operation !== "rescore" ? result.eligible.filter(m => m.response_id && m.response_version > 0).length : 0;
  if (!again) return "";
  const when = new Date(certified), day = Number.isNaN(when.getTime()) ? "" : ` on ${when.toLocaleDateString(undefined, {month: "short", day: "numeric"})}`;
  const all = again === result.eligible.length, one = again === 1;
  const title = certified ? `These grades were certified${day}.`
    : all ? (one ? "This response was graded before." : `These ${again.toLocaleString()} responses were graded before.`)
    : `${again.toLocaleString()} of these responses ${one ? "was" : "were"} graded before.`;
  return `<div class="callout" data-tone="warning" role="status" data-regrade-warning><strong class="callout-title">${h.e(title)}</strong><p>${one ? "It is" : "They are"} graded again. A new grade that passes every check replaces any grade ${one ? "it has" : "they have"} now, your own decisions included (kept in the history); a flagged one waits for you.${certified ? " Certify again afterwards." : ""}</p></div>`;
}

export function batchPreview(result, mode, operation, h, {changeModels = false, titles = new Map(), certified = ""} = {}) {
  const {cloud, unknown, reason} = batchReadiness(result, mode, operation);
  const estimate = result.estimate, count = result.eligible.length;
  const money = value => `$${Number(value).toFixed(2)}`;
  const blocked = [...result.skipped.reduce((reasons, s) => reasons.set(s.reason, (reasons.get(s.reason) || 0) + 1), new Map())];
  const allGraded = !count && !result.skipped.length && result.existing_count;
  return `<div class="grading-batch-total" role="status"><strong>${h.e(count.toLocaleString())}</strong> <span>${count === 1 ? 'response' : 'responses'} eligible</span></div>
    ${regradeWarning(result, operation, h, certified)}
    ${result.existing_count ? `<p class="grading-batch-kept">${h.e(result.existing_count.toLocaleString())} previously started responses left unchanged.</p>` : ''}
    ${cloud ? `<dl class="grading-batch-price"><div><dt>Estimated cost</dt><dd>${unknown ? 'Not available (no prices set)' : h.e(money(estimate.estimated_cost))}</dd></div></dl>` : `<p class="supporting-copy">${operation === 'rescore' ? 'Recalculate saved selections. No model calls.' : mode === 'local' ? 'Processing stays on this machine.' : 'Mathpix usage is billed separately.'}</p>`}
    ${cloud ? modelsLine(estimate.roles, h, changeModels) : ''}
    ${reason ? `<p class="grading-warning" role="status">${h.e(reason)}${allGraded ? ' <button type="button" class="text-button" data-batch-regrade-all>Grade them again instead</button>' : ''}</p>` : ''}
    ${heldQuestions(result, h, titles)}
    ${cloud ? '<p class="grading-batch-note">Clean grades finalize automatically. Flagged responses wait for your review.</p>' : ''}
    ${result.skipped.length ? `<details class="grading-batch-disclosure" data-blocked-responses ${count ? '' : 'open'}><summary>${h.e(result.skipped.length.toLocaleString())} blocked response${result.skipped.length === 1 ? '' : 's'}</summary><ul>${blocked.map(([reason, n]) => `<li><strong>${h.e(n)} response${n === 1 ? '' : 's'}:</strong> ${h.e(reason)}</li>`).join('')}</ul></details>` : ''}
    ${cloud ? `<details class="grading-batch-disclosure" data-estimate-details><summary>How the cost is estimated</summary><p>${h.e(estimate.basis)}</p></details>` : ''}
    ${count ? `<details class="grading-batch-disclosure" data-batch-sources><summary>Source pages and answer areas</summary><p class="supporting-copy">${count > 50 ? `Showing the first 50 of ${h.e(count.toLocaleString())} responses. ` : ''}Only current reviewed papers are included.</p><ol class="grading-batch-sources">${result.eligible.slice(0, 50).map(member => `<li>${member.source.regions.map(region => `<a ${viewLink(region.raster_url, `Page ${region.page}`, h.e)}>Page ${h.e(region.page)}</a>`).join(' · ')}<span> · ${member.source.regions.length} answer area${member.source.regions.length === 1 ? '' : 's'}</span></li>`).join('')}</ol></details>` : ''}`;
}
