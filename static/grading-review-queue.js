// The Review step's pieces: a question's groups of proposed rubric lines ("Groups & review tools"), and the shared markup the review
// workspace draws (Failed to grade, rubric concerns, grade summary, breadcrumb). Navigation only. Every decision is still made one
// response at a time in the detailed review, and a proposal hidden by a blind acceptance is never sorted by its lines.
import {inPrintedOrder, versionTitle} from "./grading-guide.js";
import {mathText, renderMath} from "./grading-math.js";
import {labels} from "./grading-ui.js";
import {answerGroupsDialog} from "./grading-groups.js";
import {rubricDisplay} from "./grading-rubric-display.js";
import {COMPUTE} from "./grading-jobs.js";

export const reviewHref = (plan, scope = {}) => `#grading/${plan.id}?${new URLSearchParams({tab: "review", ...scope})}`;
export const queueUrl = (plan, scope = {}) => `/api/grading/assignments/${plan.id}/review-queue${scope.job ? `?${new URLSearchParams({job_id: scope.job})}` : ""}`;
// One question's part of the queue (the batch scope kept), for the review desk to find its group's members. A server that cuts the
// answer to the question says so with a top-level `question_id`: such an answer lists only that question and counts only its
// responses, so never read other questions or `other_waiting` from it. Without that name it is the whole queue, as from a server
// without the parameter, and holds the same group.
export const questionQueueUrl = (plan, scope) => `/api/grading/assignments/${plan.id}/review-queue?${new URLSearchParams({...(scope.job ? {job_id: scope.job} : {}), question_id: scope.question})}`;
const n = value => Number(value || 0).toLocaleString();
const count = (value, noun) => `${n(value)} ${noun}${value === 1 ? "" : "s"}`;

const KINDS = {
  hidden: ["Proposal hidden until your own decision", "Blind review: grade these yourself first. Their proposed lines are neither shown nor sorted."],
};

// Number against the complete rubric, not the subset of proposed lines.
export const groupName = (group, rules) => group.kind !== "proposal" ? KINDS[group.kind][0] : group.applied.length ? `${group.applied.length === 1 ? 'Item' : 'Items'} ${rubricDisplay(rules).list(group.applied, ' + ')}` : "No rubric line proposed";

export function groupLines(group, h, rules) {
  if (group.kind !== "proposal") return `<strong>${h.e(KINDS[group.kind][0])}</strong><p class="supporting-copy">${h.e(KINDS[group.kind][1])}</p>`;
  if (!group.items.length) return "<strong>No rubric line proposed</strong>";
  const display = rubricDisplay(rules);
  return `<ul class="grading-review-lines">${display.ordered(group.items).map(item => `<li><strong class="grading-rubric-number" aria-label="${h.e(display.label(item.id))}">${h.e(display.number(item.id))}.</strong> <span class="grading-rubric-points">${h.e(item.points)} pt${Math.abs(item.points) === 1 ? '' : 's'}</span> <span>${mathText(item.description, h)}</span></li>`).join("")}</ul>`;
}

export function breadcrumb(plan, question, group, h, scope = {}) {
  const batch = scope.job ? {job: scope.job} : {};
  const steps = [`<a href="${h.e(reviewHref(plan, batch))}">${scope.job ? "Batch review" : "Review"}</a>`];
  if (question) steps.push(group ? `<a href="${h.e(reviewHref(plan, {...batch, question: question.question_id}))}">${h.e(versionTitle(question))}</a>` : `<span aria-current="page">${h.e(versionTitle(question))}</span>`);
  if (group) steps.push(`<span aria-current="page">${h.e(group)}</span>`);
  return `<nav class="grading-breadcrumb" aria-label="Review navigation"><ol>${steps.map(step => `<li>${step}</li>`).join("")}</ol></nav>`;
}

// Unclear rubric wording is one question about the problem, not one issue per student: shown once, with where to settle it.
// `inline`: in the review workspace, where the concern is settled beside the desk; the finalization stays one link away.
export function rubricDoubts(plan, entry, h, {inline = false} = {}) {
  const doubts = entry?.rubric_doubts || [];
  if (!doubts.length) return "";
  const display = rubricDisplay(plan.questions.find(q => q.question_id === entry.question_id)?.rubric?.rules);
  return `<section class="grading-panel grading-rubric-doubts" data-rubric-doubts><h3>Shared rubric concerns</h3><p class="supporting-copy">${h.e(count(entry.doubted, "response"))} raised a rubric concern. Clarify the rule once, then regrade affected work.</p><ul class="grading-rubric-doubt-list">${doubts.map(doubt => `<li><strong>${doubt.settled ? `${h.e(display.label(doubt.items[0]))} with other lines` : doubt.items.length ? h.e(`Items ${display.list(doubt.items, " · ")}`) : "The rubric as a whole"}</strong> <span class="grading-chip">${h.e(count(doubt.responses, "response"))}</span>${doubt.settled ? `<p class="grading-rubric-doubt-settled">Settled by rule: ${h.e(display.label(doubt.items[0]))} is the ${doubt.settled === "full_credit" ? "full-credit" : "no-credit"} line and excludes every other line. Graders are told so once the rubrics are finalized; grade these responses again.</p>` : ""}${doubt.examples.map(text => `<p>${mathText(text, h)}</p>`).join("")}</li>`).join("")}</ul><a class="${inline ? "text-button" : "button"}" href="#grading/${h.e(plan.id)}?tab=answer-key&focus=${h.e(entry.question_id)}&open=quality">${inline ? "Open the full rubric finalization" : "Clarify in the rubric finalization"}</a></section>`;
}

// The server lists every response whose latest attempt failed, by batch (GRADING "Failed to grade"); a batch's own queue keeps its
// batch only. A retry is the batch's own action; the server says which it skipped and why. No student is named.
const when = value => new Date(value).toLocaleString(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
export function failedSection(plan, failed, h) {
  if (!failed?.total) return "";
  const titles = new Map(plan.questions.map(q => [q.question_id, versionTitle(q)]));
  const batch = b => `<article class="grading-review-failed-batch" data-failed-batch="${h.e(b.job_id)}"><header><strong>${h.e(COMPUTE[b.compute] || b.compute)} batch · ${h.e(when(b.created_at))}</strong><span class="grading-chip grading-chip-failed">${h.e(count(b.count, "response"))}</span></header>`
    + `<ul class="grading-review-failed-reasons">${b.reasons.map(r => `<li><span class="grading-review-failed-count">${h.e(r.count)} ×</span> ${h.e(r.text)}</li>`).join("")}</ul>`
    + `<p class="supporting-copy">${b.questions.map(q => `${h.e(titles.get(q.question_id) || "A question no longer in this assessment")}: ${h.e(q.count)}`).join(" · ")}</p>`
    + `<div class="grading-actions">${b.cancelled ? '<p class="supporting-copy">This batch was cancelled, so it retries nothing: grade these responses in a new batch.</p>'
      : b.at_limit === b.count ? `<p class="supporting-copy">${b.count === 1 ? "It has" : "All have"} reached this batch’s retry limit: grade ${b.count === 1 ? "it" : "them"} in a new batch.</p>`
      : h.button("retry-failed", `Retry ${count(b.count - b.at_limit, "failed response")}`, `data-job="${h.e(b.job_id)}"`)}<a class="text-button" href="#grading/${h.e(plan.id)}?tab=jobs">${b.cancelled || b.at_limit === b.count ? "Start a new batch →" : "Open the batch →"}</a></div></article>`;
  return `<details class="grading-review-failed" data-review-failed aria-labelledby="grading-review-failed-title"><summary id="grading-review-failed-title"><strong>Failed to grade</strong><span class="grading-review-failed-count">${h.e(count(failed.total, "response"))}</span><span class="grading-review-failed-toggle" aria-hidden="true"></span></summary>`
    + `<div class="grading-review-failed-details"><p class="supporting-copy">These responses have no grade because their last attempt failed. Review the reasons below before retrying.</p>${failed.batches.map(batch).join("")}</div></details>`;
}

// A question's current final grades in one line (grun:P17), from the server's read-only `scores`: how many, the mean, and the
// shares at full credit and at zero. For sanity-checking the grading; it names nobody and decides nothing.
const pct = share => `${Math.round(Number(share || 0) * 100)}%`;
export function scoreLine(scores, h) {
  if (!scores?.count) return "";
  const mean = Number(scores.mean).toLocaleString(undefined, {maximumFractionDigits: 2});
  return `<small class="grading-score-line" data-score-summary>Final: mean ${h.e(mean)}${scores.max ? ` of ${h.e(Number(scores.max).toLocaleString())}` : ""} · ${h.e(pct(scores.full))} full · ${h.e(pct(scores.zero))} zero</small>`;
}

// The same numbers for every question, as a table (kept for callers; Certification and Export has its own question table).
export function scoreTableMarkup(plan, queue, h) {
  const entries = new Map((queue?.questions || []).map(q => [q.question_id, q]));
  const rows = inPrintedOrder(plan.questions).map(q => ({q, s: entries.get(q.question_id)?.scores})).filter(r => r.s?.count);
  if (!rows.length) return '<p class="supporting-copy">No question has a final grade yet.</p>';
  return `<div class="grading-score-table"><table><caption class="sr-only">Final grades per question</caption><thead><tr><th scope="col">Question</th><th scope="col">Final grades</th><th scope="col">Mean</th><th scope="col">Full credit</th><th scope="col">Zero</th></tr></thead><tbody>${rows.map(({q, s}) =>
    `<tr><th scope="row">${h.e(versionTitle(q))}</th><td>${h.e(n(s.count))}</td><td>${h.e(Number(s.mean).toLocaleString(undefined, {maximumFractionDigits: 2}))}${s.max ? ` of ${h.e(Number(s.max).toLocaleString())}` : ""}</td><td>${h.e(pct(s.full))}</td><td>${h.e(pct(s.zero))}</td></tr>`).join("")}</tbody></table></div>`;
}

const chips = (group, h) => `${group.flagged ? `<span class="grading-chip grading-chip-attention">${h.e(group.flagged)} flagged for you</span>` : ""}${Object.entries(group.states).filter(([state]) => ["trial_accepted", "draft", "unreadable", "rejected"].includes(state)).map(([state, n]) => `<span class="grading-chip grading-chip-todo">${h.e(n)} · ${h.e(labels[state])}</span>`).join("")}`;

// A question's groups and review tools ({question}); the review workspace is everything else on the Review step.
export async function queueView(host, plan, h, refresh, active, scope) {
  const queue = await h.api(queueUrl(plan, scope));
  if (!active()) return;
  const batch = scope.job ? {job: scope.job} : {};
  const href = extra => reviewHref(plan, {...batch, ...extra});
  const context = queue.batch ? `<p class="supporting-copy" data-review-batch>Batch · ${h.e(when(queue.batch.created_at))}. Current results from this batch only. <a href="${h.e(reviewHref(plan))}">Review all batches${queue.other_waiting ? ` (${h.e(queue.other_waiting)} other responses waiting)` : ""} →</a></p>` : '<p class="supporting-copy" data-review-batch>All batches · current responses for this assessment.</p>';
  const entries = new Map(queue.questions.map(q => [q.question_id, q]));
  questionPage(plan.questions.find(q => q.question_id === scope.question), entries.get(scope.question));
  renderMath(host);

  function questionPage(q, entry) {
    if (!q) { host.innerHTML = `${breadcrumb(plan, null, "", h, scope)}<p class="empty-state">This question is not part of the assessment.</p>`; return; }
    const groups = entry?.groups || [];
    const item = g => `<li><a class="grading-review-group" href="${h.e(href({question: q.question_id, group: g.id}))}"><div>${groupLines(g, h, q.rubric?.rules)}<p class="grading-chips">${chips(g, h)}</p></div><div class="grading-review-group-count"><strong>${h.e(g.size)}</strong> <span>${g.size === 1 ? "response" : "responses"}</span>${g.scores.length ? ` <small>${h.e(g.scores.length > 1 ? `${g.scores[0]}–${g.scores.at(-1)}` : g.scores[0])} of ${h.e(q.content.max_points)} points</small>` : ""}</div></a></li>`;
    // One sentence and one way in: the largest group first, the server's order (grading-review-5).
    const first = groups.length ? `<a class="button primary" href="${h.e(href({question: q.question_id, group: groups[0].id}))}">${groups.length === 1 ? "Review the group →" : "Review the first group →"}</a>` : "";
    host.innerHTML = `${breadcrumb(plan, q, "", h, scope)}${context}<header class="grading-heading"><p class="section-lead">${groups.length ? `${h.e(count(entry.waiting, "response"))} to review in ${h.e(count(groups.length, "group"))}, each one combination of rubric lines the AI proposed.` : "Nothing in this question is waiting for your review."}</p><div class="grading-actions">${first}${!scope.job && groups.some(g => g.kind === "proposal") ? h.button("groups", "Grade by answer group") : ""}</div></header>${entry?.failed ? `<p class="grading-warning">${h.e(count(entry.failed, "response"))} of this question failed to grade and ${entry.failed === 1 ? "has" : "have"} no proposal to review. <a href="${h.e(href({open: "failed"}))}">See Failed to grade</a></p>` : ""}${entry?.regrade ? `<p class="grading-warning" data-review-regrade>${h.e(count(entry.regrade, "response"))} of this question ${entry.regrade === 1 ? "was" : "were"} graded under an earlier rubric, question or paper, so ${entry.regrade === 1 ? "it" : "they"} cannot be approved as graded. The next batch grades ${entry.regrade === 1 ? "it" : "them"} again. <a href="#grading/${h.e(plan.id)}?tab=jobs">Grade again</a></p>` : ""}${rubricDoubts(plan, entry, h)}${groups.length ? `<ol class="grading-review-groups">${groups.map(item).join("")}</ol>` : ""}${entry ? `<p class="supporting-copy">${h.e(n(entry.approved))} of ${h.e(n(entry.responses))} final${entry.automatic ? ` (${h.e(n(entry.automatic))} automatic)` : ""}${entry.not_graded ? ` · ${h.e(n(entry.not_graded))} not graded yet` : ""}${entry.regrade ? ` · ${h.e(n(entry.regrade))} to grade again` : ""}. ${scoreLine(entry.scores, h)} <a href="${h.e(reviewHref(plan, {browse: "all", question: q.question_id}))}">See all responses</a>${entry.automatic ? ` · <a data-spot-check-link href="${h.e(reviewHref(plan, {browse: "all", question: q.question_id, status: "automatic", spot: "10"}))}">Spot-check ${h.e(Math.min(10, entry.automatic))} of ${h.e(count(entry.automatic, "automatic grade"))} at random</a>` : ""}</p>` : ""}`;
    host.querySelector('[data-grading-action="groups"]')?.addEventListener("click", event => h.run(event.currentTarget, () => answerGroupsDialog(plan, q.question_id, h, refresh, active)));
  }
}
