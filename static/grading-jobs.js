import {labels} from "./grading-ui.js";
import {emptyMarkup, nextStepMarkup, plural as count, whenVisible} from "./ui.js";
import {inPrintedOrder, uploadScansHref, versionTitle} from "./grading-guide.js";
import {assignmentNext, autoGradeLine} from "./grading-overview.js";
import {batchForm, batchPreview, batchReadiness, methodHelp} from "./grading-batch-layout.js";
import {patchChildren, patchElement} from "./dom-patch.js";

// Checking and creating a whole exam's batch (2,600 responses) takes longer than the usual 15 seconds; the page gave up and said the
// server could not be reached while the server went on and created the batch (2026-09-24). Creating is keyed by its request ID.
const BATCH_WAIT = 180000;

// Grading reasons in tiers and roles; vendors and models appear only in Compute settings.
// Cloud (internally `cloud_standard`) and the local modes are the only ones a batch can use (requirement, 2026-09-23). The others name
// batches graded before they were removed, which stay readable.
// Removed methods are named in plain words (2026-10-18, pipeline review item 11); the batch's "Run settings" under Batch details keeps
// what ran.
export const COMPUTE = {cloud_standard: "Cloud", hybrid: "Mathpix + local grading", local: "Fully local", jev_routing: "Routed grading (retired)", tag_team: "Two models in turn (retired)",
  one_shot: "One model (retired)", cloud_high_assurance: "Cloud with extra checks (retired)", nebius: "Nebius cloud (retired)"};
const METHODS = ["cloud_standard", "hybrid", "local"];
export const computeName = job => COMPUTE[job.compute || job.tier || job.mode] || job.mode;

// "824 responses · 513 completed · 266 queued or running · 7 awaiting your review": cheap to compute and enough to follow an exam.
export function progressLine(job) {
  const p = job.progress, parts = [`${p.total} response${p.total === 1 ? "" : "s"}`, `${p.completed} completed`];
  if (p.queued + p.running) parts.push(`${p.queued + p.running} queued or running`);
  if (p.in_flight) parts.push(`${p.in_flight} cloud request${p.in_flight === 1 ? "" : "s"} in flight`);
  if (p.escalated) parts.push(`${p.escalated} sent for an extra check`);
  if (p.awaiting_review) parts.push(`${p.awaiting_review} awaiting your review`);
  if (p.failed) parts.push(`${p.failed} failed`);
  if (p.cancelled) parts.push(`${p.cancelled} cancelled`);
  if (p.retried) parts.push(`${p.retried} request${p.retried === 1 ? "" : "s"} retried automatically`);
  return parts.join(" · ");
}

const operations = {recognize: "Read and grade", grade: "Grade saved readings again", rescore: "Points counted again from the rubric"};
const working = status => ["pending", "running"].includes(status);
const n = value => Number(value || 0).toLocaleString();
const plural = (count, one, many = `${one}s`) => `${n(count)} ${count === 1 ? one : many}`;

// A finished batch with nothing waiting says "Finished"; the stored status (review_ready) is unchanged.
export function jobBadge(job, h) {
  const p = job.progress;
  if (job.status === "review_ready" && p && !p.awaiting_review && !p.failed) return '<span class="grading-badge grading-complete" data-status="review_ready">Finished</span>';
  return h.badge(job.status);
}

// What a batch came to, first: "21 graded · nothing waits for you". While it runs, how far it is.
export function batchOutcome(job) {
  const p = job.progress;
  if (working(job.status)) return `${n(p.completed)} of ${plural(p.total, "response")} graded so far`;
  return [`${n(p.completed)} graded`, p.awaiting_review ? `${n(p.awaiting_review)} ${p.awaiting_review === 1 ? "waits" : "wait"} for your review` : "nothing waits for you",
    p.failed ? `${n(p.failed)} failed` : "", p.cancelled ? `${n(p.cancelled)} cancelled` : ""].filter(Boolean).join(" · ");
}

// No scan, no response and no paper ready to grade yet (a quiz drafted but not given): nothing is missing and nothing can be graded,
// so the Grade step points at Upload scans in Collect instead (journey-1; I1, 2026-10-18). The batch button comes back with the first scan, paper or response.
export function noPapers(plan) {
  const w = plan?.workflow || {};
  return !(w.scans || []).length && !Object.values(w.responses || {}).some(Boolean) && !w.new_responses && !w.pending && !w.failed;
}

// Papers that cannot be graded yet (missing pages, a paper still in review): said on the Grade step, never taken as an absence. Only
// counts above zero are named; the Exams hub's Collection tab is where each is resolved. A student with no scanned paper is not named
// (ASSIGNMENT-WORKSPACE THE ZEROS ARE SAID AT CERTIFY, 2026-11-05).
export function collectionNotice(plan, h) {
  if (noPapers(plan)) return "";
  const w = plan?.workflow || {}, c = w.collection || {};
  const parts = [c.missing_pages ? `${plural(c.missing_pages, "paper")} ${c.missing_pages === 1 ? "is" : "are"} missing pages` : "",
    c.partial ? `${plural(c.partial, "paper")} ${c.partial === 1 ? "is" : "are"} incomplete` : "",
    c.needs_review ? `${plural(c.needs_review, "paper")} ${c.needs_review === 1 ? "needs" : "need"} identity or submission review` : ""].filter(Boolean);
  if (!parts.length) return "";
  const joined = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}` : parts[0];
  // Blocked responses include papers waiting for a version decision, which have their own notice: the count is named only without them.
  const blocked = w.blocked_responses || 0;
  const title = `${joined.charAt(0).toUpperCase()}${joined.slice(1)}${blocked && !w.version_decisions ? `, so ${plural(blocked, "response")} cannot be graded` : ""}.`;
  return `<div class="callout grading-collection-notice" data-tone="warning" role="status" data-grade-blocked><strong class="callout-title">${h.e(title)}</strong><p>A missing scan is not an absence: find the paper, or record an absence or excuse, on the Exams page.</p><div class="grading-actions">${plan.event_id ? `<a class="button" href="#exams/${h.e(plan.event_id)}?tab=collection">Open Collection</a>` : ""}<a class="text-button" href="#grading/${h.e(plan.id)}?tab=papers">Scan records</a></div></div>`;
}

// The Grade step's lead: what is new to grade, from the plan's own counts.
export function gradeLead(plan) {
  const w = plan?.workflow || {}, fresh = w.new_responses || 0, again = w.regrade_responses || 0;
  if (!fresh) return noPapers(plan) ? "No papers yet. Once it is given, upload its scans." : "Nothing new to grade.";
  if (again === fresh) return `${plural(again, "response")} graded under an earlier rubric must be graded again.`;
  return `${plural(fresh, "new response")} ${fresh === 1 ? "is" : "are"} ready to grade.${again ? ` ${n(again)} of them ${again === 1 ? "was" : "were"} graded under an earlier rubric and must be graded again.` : ""}`;
}

// Failures wait for a retry: no batch grades them again by itself (the workflow's `failed`, every batch's). ONE RETRY (2026-10-18,
// pipeline review item 11): it is in Review's Failed to grade, with the reasons; the Grade step and the batch cards link there.
export const failedHref = plan => `#grading/${encodeURIComponent(plan?.id || "")}?tab=review&open=failed`;
export function failedLine(plan) {
  const failed = plan?.workflow?.failed || 0;
  if (!failed) return "";
  return failed === 1 ? "1 response failed to grade and will not be graded again by itself: retry it under Failed to grade in Review."
    : `${plural(failed, "response")} failed to grade and will not be graded again by themselves: retry them under Failed to grade in Review.`;
}

// A polled card or panel brought to new markup in place (dom-patch.js, FRONTEND "Quiet refresh contract"). dom-patch matches children
// by position, so a line that comes or goes (the "Now: …" stage line when a batch settles, a notice appearing mid-run) moves the
// disclosures after it onto other elements. Their state is therefore kept by KEY, not by element: a keyed disclosure that stays keeps
// the open or closed state the instructor left it in, whatever the markup says, and a new key opens as the markup says; an unkeyed one
// keeps its state while its element stays. Focus is handed back the same way: a control by what it does, a disclosure's summary by
// the disclosure's key.
export const jobDisclosureKey = node => node.dataset.jobDisclosure || "";
const FOCUS_NAMES = ["data-grading-action", "data-job-review", "data-flag-review-start", "data-flag-review-stop", "data-flag-review-copy"];
function focusMark(node, key) {
  if (!(node instanceof HTMLElement)) return "";
  const name = FOCUS_NAMES.find(attribute => node.hasAttribute(attribute));
  if (name) return `${name}=${node.getAttribute(name)}`;
  const owner = node.parentElement;
  return node.tagName === "SUMMARY" && owner?.tagName === "DETAILS" && key(owner) ? `summary=${key(owner)}` : "";
}
function findMark(target, mark, key) {
  const at = mark.indexOf("="), name = mark.slice(0, at), value = mark.slice(at + 1);
  if (name === "summary") return [...target.querySelectorAll("details")].find(node => key(node) === value)?.querySelector(":scope > summary");
  return [...target.querySelectorAll(`[${name}]`)].find(node => node.getAttribute(name) === value);
}
export function patchKeepingDisclosures(target, markup, key = jobDisclosureKey) {
  const keyed = new Map(), unkeyed = new Map();
  for (const node of target.querySelectorAll("details")) {
    const name = key(node);
    if (name) keyed.set(name, node.open); else unkeyed.set(node, node.open);
  }
  const focused = target.contains(document.activeElement) ? /** @type {HTMLElement} */ (document.activeElement) : null;
  const mark = focusMark(focused, key);
  patchChildren(target, markup);
  for (const node of target.querySelectorAll("details")) {
    const name = key(node), open = name ? keyed.get(name) : unkeyed.get(node);
    if (open !== undefined && node.open !== open) node.open = open;
  }
  if (mark && (!focused?.isConnected || focusMark(focused, key) !== mark)) findMark(target, mark, key)?.focus({preventScroll: true});
}

function measuredTime(seconds) {
  const total = Math.round(seconds || 0);
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`;
  return `${Math.floor(total / 3600)}h ${Math.floor(total % 3600 / 60)}m`;
}

// A failed arbitration or flag review leaves its response with the team's grade, waiting for you: the batch still completes, so
// the card says how many and why (2026-09-24: every arbitration of one batch was refused and the batch looked finished).
function reviewersFailed(p, h) {
  const names = {arbitrations: ["check of a disagreement", "checks of disagreements"], flag_reviews: ["flag review", "flag reviews"]};
  return Object.entries(p.reviewers_failed || {}).map(([key, item]) => `<div class="grading-warning" role="status" data-reviewers-failed="${h.e(key)}"><p><b>${h.e(item.responses)}</b> ${h.e(names[key][item.responses === 1 ? 0 : 1])} failed. ${item.responses === 1 ? "That response keeps" : "Those responses keep"} the graders’ grade and wait for your review.</p><ul>${item.reasons.map(r => `<li>${h.e(r.responses)} × ${h.e(r.reason)}</li>`).join("")}</ul></div>`).join("");
}

// GRADING MULTIPLE-CHOICE READER HEALTH (2026-10-09): one quiet line for a batch that read by choice, and a warning when reads failed
// (a rejected key or an outage otherwise sent every multiple-choice response down the ordinary path unseen). Those responses were
// graded as written work by the ordinary path: nothing is held for them.
const CHOICE_REASONS = {several: "several marked", none: "none marked", unsure: "unsure", failed: "failed", alignment: "alignment uncertain"};
export function choiceReads(p, h) {
  const c = p.choice_reads;
  if (!c) return "";
  const why = Object.entries(c.reasons || {}).map(([key, count]) => `${n(count)} ${CHOICE_REASONS[key] || key}`).join(", ");
  const line = `<p class="supporting-copy grading-batch-choice" data-choice-reads>Multiple choice: ${h.e(n(c.by_choice))} read by choice${c.written ? ` · ${h.e(n(c.written))} graded as written work${why ? ` (${h.e(why)})` : ""}` : ""}</p>`;
  const failed = (c.reasons || {}).failed || 0;
  return line + (failed ? `<div class="grading-warning" role="status" data-choice-failed><p><b>${h.e(n(failed))}</b> multiple-choice ${failed === 1 ? "read" : "reads"} failed, so ${failed === 1 ? "that response was" : "those responses were"} graded as written work. Check the OpenAI key and the multiple-choice reader in Compute settings.</p><ul>${(c.failures || []).map(r => `<li>${h.e(r.responses)} × ${h.e(r.reason)}</li>`).join("")}</ul></div>` : "");
}

export function jobDetailMarkup(job, h, titles = new Map()) {
  const p = job.progress, cloud = job.mode === "nebius";
  const roles = job.compute === "jev_routing" ? `${job.profile.jev_routing_rule === "jev-routing-v2" ? "Mathpix reads each problem’s blank-template crops. " : ""}Jev rates its transcription and rubric from 1 to 5. Its assigned model grades every response in One-shot mode.` : job.compute === "tag_team" ? "Both Tag-Team models see these crops with the problem, solution and rubric. The reviewer also receives the first model’s transcript, assessment and any flags. No OCR is used." : job.compute === "one_shot" ? `One-shot: ${job.profile.one_shot_model}` : cloud && job.tier ? Object.entries(job.profile.tiers[job.tier]).filter(([, name]) => name).map(([role, name]) => `${role}: ${name}`).join(" · ") : "";
  const stages = Object.entries(p.stages).map(([stage, n]) => `${n} ${(labels[stage] || stage).toLowerCase()}`).join(" · ");
  // The canary was removed on 2026-09-28; an earlier batch still shows what its canary did. A skipped one is routine information.
  // Other notices can stop work and stay prominent.
  // Turned off in Compute settings is the instructor's own choice: routine too.
  const skipped = job.notice?.startsWith("Canary check skipped:"), off = job.notice?.startsWith("Canary check turned off");
  // A canary that never compared anything (cancelled with its batch, or its models could not grade) says why: it has no agreement,
  // and showing one as 0% read as a model that disagreed with every approved grade.
  const canaryText = c => {
    if (c.status === "pending") return "grading your approved responses again first…";
    if (c.agreement === null || c.agreement === undefined) {
      const detail = c.detail || {};
      return `stopped before it could compare anything${detail.compared != null ? ` (${h.e(detail.compared)} of ${h.e(detail.responses ?? detail.needed)} approved responses graded)` : ""}. This says nothing about the model's grading.`;
    }
    return `${h.e(Math.round(c.agreement * 100))}% agreement with your approved grades${c.baseline === null ? " (recorded as its baseline)" : ` · baseline ${h.e(Math.round(c.baseline * 100))}%`}`;
  };
  // Plain words (2026-10-18): the canary is "the check against your approved grades"; it is a past batch's record, under Batch details.
  const canary = job.canary.filter(c => c.status !== "skipped").map(c => `<p class="${c.status === "failed" && c.agreement != null ? "grading-warning" : "supporting-copy"}" data-batch-check>Check against your approved grades (${h.e(c.endpoint)}): ${canaryText(c)}</p>`).join("");
  const cost = cloud ? `$${(job.cost || 0).toFixed(2)} recorded` : "";
  // A settled batch's outcome line already names what waits, failed and was cancelled; its facts row adds only what is still moving.
  const running = working(job.status);
  const lead = running ? `<div class="grading-batch-progress"><p><b>${h.e(n(p.completed))}</b> of ${h.e(n(p.total))} responses completed</p><span>${h.e(p.total ? Math.floor(p.completed / p.total * 100) : 0)}%</span></div>`
    : `<p class="grading-batch-outcome" data-batch-outcome>${h.e(batchOutcome(job))}</p>`;
  return `${lead}
    <progress max="${h.e(Math.max(1, p.total))}" value="${h.e(p.completed)}" aria-label="Completed responses"></progress>
    <div class="grading-batch-facts">${p.queued + p.running ? `<span><b>${h.e(p.queued + p.running)}</b> queued or running</span>` : ""}${running ? `${p.awaiting_review ? `<span><b>${h.e(p.awaiting_review)}</b> awaiting your review</span>` : ""}${p.failed ? `<span class="grading-batch-failed"><b>${h.e(p.failed)}</b> failed</span>` : ""}${p.cancelled ? `<span>${h.e(p.cancelled)} cancelled</span>` : ""}` : ""}</div>
    ${stages ? `<p class="supporting-copy grading-batch-stage">Now: ${h.e(stages)}</p>` : ""}${reviewersFailed(p, h)}${choiceReads(p, h)}
    ${job.notice && !skipped && !off ? `<p class="grading-warning" role="status">${h.e(job.notice)}</p>` : ""}${resetNotice(job, h)}${canary && !working(job.status) ? "" : canary}
    <div class="grading-batch-actions"><div class="grading-actions"><a class="button${p.awaiting_review ? " primary" : ""}" data-job-review href="#grading/${h.e(job.assignment_id)}?tab=review&job=${h.e(job.id)}">Review grades</a>${p.failed ? `<a class="text-button" data-job-failed href="${h.e(failedHref({id: job.assignment_id}))}">${h.e(plural(p.failed, "failed response"))}: retry in Review →</a>` : ""}${["pending", "running"].includes(job.status) ? h.button("cancel", "Cancel remaining work") : ""}</div>${cost ? `<span class="grading-batch-cost">${h.e(cost)}</span>` : ""}</div>
    ${p.failed || job.tasks.length ? `<details class="disclosure grading-batch-details" data-job-disclosure="responses" ${job.status === "failed" ? "open" : ""}><summary>${p.failed ? `${h.e(p.failed)} failed response${p.failed === 1 ? "" : "s"} · details` : job.tasks_listed === "all" ? "Response progress and issues" : "Responses that need attention"}</summary><ul class="grading-task-list">${job.tasks.map((t, i) => `<li><span>${h.e(titles.get(t.question_id) || `Response ${i + 1}`)}: ${h.e(labels[t.stage] || t.stage)}</span>${h.badge(t.status)}${t.error ? `<p role="alert">${h.e(t.error)}</p>` : ""}<small>Instructor retries: ${h.e(t.attempt)}</small></li>`).join("") || "<li>None.</li>"}</ul></details>` : ""}
    <details class="disclosure grading-batch-details" data-job-disclosure="run"><summary><span>Batch details & controls</span></summary><p class="supporting-copy">${h.e(progressLine(job))}</p><dl class="grading-batch-meta"><div><dt>Processing time</dt><dd>${h.e(measuredTime(job.runtime_seconds))} measured across tasks</dd></div>${cloud ? `<div><dt>Cost</dt><dd>$${h.e((job.cost || 0).toFixed(4))} recorded${job.estimated_cost !== null ? ` of $${h.e(job.estimated_cost.toFixed(2))} estimated` : ""}</dd></div>` : ""}<div><dt>Run settings</dt><dd>${h.e(cloud ? (roles || job.profile.nebius_model) : job.profile.local_grader_model)}</dd></div></dl>${skipped || off ? `<p class="supporting-copy" data-batch-check>${h.e(job.notice)}</p>` : ""}${canary && !working(job.status) ? canary : ""}<div class="grading-actions">${cloud && job.operation !== "rescore" ? h.button("crops", "See what is sent") : ""}${!working(job.status) && job.reset.resettable ? h.button("reset", "Reset batch…") : ""}</div></details>`;
}

/**
 * Rubric preparation history: worker readings of retained rubric sources and rubric interpretations, each with Inspect
 * result (which imports a draft; approval stays a separate step) or Retry after a failure. It lives on Rubrics &
 * Templates (UX overhaul W3, grun:P18), where the rubrics it prepares are set up. Draws nothing when there is none.
 */
export function preparationHistory(host, plan, preparations, h, refresh, active, {open = false} = {}) {
  preparations = (preparations || []).filter(p => p.assignment_id === plan.id);
  host.replaceChildren();
  if (!preparations.length) return null;
  const failed = preparations.some(p => p.status === "failed");
  const section = document.createElement("section"); section.className = "grading-panel grading-preparations"; section.id = "grading-preparations";
  section.innerHTML = `<details class="disclosure" data-preparation-history ${failed || open ? "open" : ""}><summary><span>Rubric preparation history</span><span class="disclosure-hint">${failed ? "needs attention" : h.e(preparations.length)}</span></summary><div class="disclosure-body">${preparations.map(p => `<p>${h.badge(p.status)} ${h.e(p.kind === "source" ? "Source reading" : "Rubric interpretation")} ${h.button("preparation", "Inspect result", `data-preparation="${h.e(p.id)}"`)}</p>`).join("")}</div></details>`;
  host.append(section);
  section.querySelectorAll("[data-preparation]").forEach(button => button.onclick = () => h.run(button, async () => {
    const preparation = await h.api(`/api/grading/preparations/${button.dataset.preparation}`);
    if (!active()) return;
    if (preparation.status === "failed") {
      h.dialog("Rubric preparation needs attention", preparation.error, '<p>Original material and completed parts are retained.</p>', "Retry preparation", async (_, __, live) => {
        await h.api(`/api/grading/preparations/${preparation.id}/retry`, "POST", {});
        if (live()) { h.close(); await refresh(); }
      }); return;
    }
    if (preparation.status !== "complete") { h.toast(`Worker preparation: ${preparation.completed_parts} parts retained. Refresh to check progress.`); return; }
    const assignment = await h.api(`/api/grading/assignments/${preparation.assignment_id}`);
    const eligible = assignment.questions.filter(q => preparation.kind !== "compile" || q.question_id === preparation.payload.question_id);
    if (!eligible.length) { h.toast("Define a question before importing this retained source."); return; }
    h.dialog("Inspect prepared rubric", "Review the retained reading. Import creates a draft; approval remains a separate step.", `${h.select("Question", "question", eligible.map(q => [q.question_id, q.content.title]))}${h.area("Original rubric text", "source", preparation.result.source_text)}${preparation.result.rules ? h.area("Interpreted rules (JSON)", "rules", JSON.stringify(preparation.result.rules, null, 2)) : h.select("Scoring", "mode", [["negative", "Deduct from full credit"], ["positive", "Add credit from zero"]])}`,
      "Import draft rubric", async (values, _, live) => {
        const question = eligible.find(q => q.question_id === values.get("question"));
        await h.api(`/api/grading/assignments/${assignment.id}/questions/${question.question_id}/rubrics`, "POST", {
          version: question.version, source_text: values.get("source"), mode: values.get("mode") || "negative",
          source_artifact_id: preparation.result.source_artifact_id,
          ...(preparation.result.rules ? {rules: JSON.parse(values.get("rules")), preparation_id: preparation.id} : {})});
        if (live()) { h.close(); location.hash = `#grading/${assignment.id}?tab=rubrics`; await refresh(); }
      });
  }));
  return section;
}

export async function jobsView(host, plan, overview, h, refresh, active, {openSettings = null} = {}) {
  const jobs = overview.jobs.filter(j => j.assignment_id === plan.id);
  const titles = new Map((plan.questions || []).map(q => [q.question_id, versionTitle(q)]));  // A task is named by its question, never its student.
  const foreground = jobs.slice(0, 1);
  const history = jobs.slice(1);
  const card = j => `<section class="grading-panel grading-batch" data-job="${h.e(j.id)}"><header class="grading-heading"><div><span class="grading-batch-caption">${j === jobs[0] ? "Latest batch" : "Earlier batch"}${j.automatic ? " · started automatically" : ""}${j.created_at ? ` · ${h.e(new Date(j.created_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}))}` : ""}</span><h3>${h.e(computeName(j))} <span>· ${h.e(operations[j.operation] || j.operation)}</span></h3></div>${jobBadge(j, h)}</header><div data-job-detail><p class="supporting-copy">Loading progress…</p></div></section>`;
  const w = plan.workflow || {}, fresh = w.new_responses || 0, empty = noPapers(plan);
  const models = openSettings ? '<button type="button" class="text-button grading-models-link" data-grading-action="models" aria-haspopup="dialog">Models: Compute settings</button>' : "";
  const auto = autoGradeLine(plan), failed = failedLine(plan);
  // Nothing to grade, waiting or failed: the way on is the assignment's next step (usually Certify), from the same workflow counts
  // and steps as the graph (grading-review-14). Without papers the one primary is Upload scans (in Collect, I1) instead.
  const next = !empty && !fresh && !w.pending && !w.failed && !w.review_responses ? assignmentNext(plan, jobs) : null;
  const onward = next && next.current?.key !== "grade" ? nextStepMarkup({eyebrow: next.current?.failed ? "Needs attention" : "Next",
    title: next.current?.title || next.status, detail: next.detail, label: next.current ? next.label : "Certify", href: next.href}) : "";
  const action = empty ? `<a class="button primary" href="${h.e(uploadScansHref(plan))}" data-grade-upload>Upload scans</a>` : h.button("new-job", "Grade new responses");
  host.innerHTML = `<header class="grading-heading grading-stage-heading"><div><h3>Grade responses</h3><p class="section-lead" data-grade-lead>${h.e(gradeLead(plan))}</p>${failed ? `<p class="supporting-copy" data-grade-failed>${h.e(failed)} <a class="link-arrow" href="${h.e(failedHref(plan))}">Open Failed to grade</a></p>` : ""}${auto ? `<p class="supporting-copy" data-auto-grade>${h.e(auto)}</p>` : ""}${models}</div>${action}</header>${onward}${collectionNotice(plan, h)}<div data-job-list>${foreground.map(card).join("") || emptyMarkup({title: "No grading batches yet", body: fresh ? "Grade the new responses to start the first batch." : "A batch starts from the responses that are ready to grade."})}${history.length ? `<details class="disclosure grading-batch-history"><summary>Earlier batches <span>${h.e(history.length)}</span></summary><div>${history.map(card).join("")}</div></details>` : ""}</div>`;
  host.querySelector('[data-grading-action="new-job"]')?.addEventListener("click", () => createJob(plan, h, refresh, null, {openSettings}));
  // One primary: grading the new responses, when there are any.
  if (fresh) host.querySelector('[data-grading-action="new-job"]')?.classList.add("primary");
  const foregroundNode = host.querySelector("[data-job-list] > [data-job]");
  host.querySelector('[data-grading-action="models"]')?.addEventListener("click", event => openSettings(event.currentTarget));
  let timer = null;
  // A finished or cancelled batch changes only by something done on this page, which asks for it again. Polling every card
  // every four seconds cost one full batch read per card for as long as the page stayed open.
  const settled = new Set();
  const historyPanel = host.querySelector('.grading-batch-history');
  let updating = false, updateAgain = false;
  const visibleJobs = () => [...host.querySelectorAll('[data-job]')].filter(node => !node.closest('.grading-batch-history') || historyPanel.open);
  // What each card shows was last written from (dom-patch.js, FRONTEND "Quiet refresh contract"): an unchanged read writes nothing.
  const written = new WeakMap(), badges = new WeakMap();
  // Earlier failures live in an earlier batch's card: the history opens by itself once so its link to Failed to grade is in sight (missed: earlier
  // failures were a dead end once a newer batch existed).
  let historyChecked = false;
  async function update() {
    if (updating) { updateAgain = true; return; }
    updating = true;
    clearTimeout(timer);
    try {
      for (const node of visibleJobs()) {
        if (!active()) return;
        if (node.closest('.grading-batch-history') && !historyPanel.open) continue;
        if (settled.has(node.dataset.job)) continue;
        try {
          const job = await h.api(`/api/grading/jobs/${node.dataset.job}`);
          if (!active() || !node.isConnected) return;
          // A cancelled batch keeps ticking while components already under way finish; after that nothing moves by itself.
          if (!working(job.status) && !job.progress.running && !job.canary.some(c => c.status === "pending")) settled.add(job.id);
          if (!historyChecked && node === foregroundNode) {
            historyChecked = true;
            if (historyPanel && !historyPanel.open && (plan.workflow?.failed || 0) > (job.progress.failed || 0)) historyPanel.open = true;
          }
          const badge = jobBadge(job, h), detail = jobDetailMarkup(job, h, titles);
          const badgeNode = node.querySelector(".grading-heading .grading-badge");
          if (badgeNode && badges.get(node) !== badge) {
            const parsed = document.createElement("template");
            parsed.innerHTML = badge;
            patchElement(badgeNode, /** @type {Element} */ (parsed.content.firstElementChild));
            badges.set(node, badge);
          }
          if (written.get(node) !== detail) {
            // Disclosures keep their state by name and focus goes back to what it was on (a button whose place another took, Retry
            // appearing before Cancel; a summary whose disclosure moved when the stage line went away).
            patchKeepingDisclosures(node.querySelector("[data-job-detail]"), detail);
            written.set(node, detail);
          }
          // Bound as properties, so binding again replaces the handlers with ones that read this answer.
          node.querySelectorAll("[data-grading-action]").forEach(button => button.onclick = () => h.run(button, async () => {
            if (button.dataset.gradingAction === "crops") { await cloudDialog(job, h); return; }
            if (button.dataset.gradingAction === "reset") { await resetDialog(job, h, refresh); return; }
            if (button.dataset.gradingAction === "cancel") { cancelDialog(job, h, async () => { settled.delete(job.id); await update(); }); return; }
            if (button.dataset.gradingAction === "grade-again") { createJob(plan, h, refresh, null, {openSettings}); return; }
            if (button.dataset.gradingAction === "restore-reset") {
              const restored = await h.api(`/api/grading/jobs/${job.id}/reset/restore`, "POST", {reset_id: job.reset.latest.id});
              h.toast(`${restored.reset.latest.restored} proposal${restored.reset.latest.restored === 1 ? "" : "s"} put back.`);
              await refresh(); return;
            }
            await h.api(`/api/grading/jobs/${job.id}/${button.dataset.gradingAction}`, "POST", {});
            settled.delete(job.id);
            await update();
          }));
        } catch (error) {
          if (active() && node.isConnected) { node.querySelector("[data-job-detail]").innerHTML = `<p role="alert">${h.e(error.message)}</p>`; written.delete(node); }
        }
      }
    } finally {
      updating = false;
      // A refresh asked for meanwhile runs at once; the poll of a batch still moving waits while the tab is hidden and reads once
      // it is shown.
      if (active() && updateAgain) timer = setTimeout(() => { if (active()) void update(); }, 0);
      else if (active() && visibleJobs().some(node => !settled.has(node.dataset.job))) timer = setTimeout(() => whenVisible(() => { if (active()) void update(); }), 4000);
      updateAgain = false;
    }
  }
  historyPanel?.addEventListener('toggle', () => { if (historyPanel.open && active()) void update(); });
  await update();
}

// A cancelled batch is never claimed again, so the click that stops it asks first (review fix GRUN-F3). The request is the
// same one as before, sent only from the dialog.
function cancelDialog(job, h, done) {
  const p = job.progress, left = (p.queued || 0) + (p.running || 0);
  const what = left ? `the remaining ${plural(left, "response")}` : "this batch’s remaining work";
  h.dialog(`Cancel ${what}?`, "Nothing already graded is lost; the rest become new work for the next batch.",
    `<p class="supporting-copy">A cancelled batch does not start again. Requests already sent to a model are closed and keep their record.</p>`,
    "Cancel remaining work", async (_, __, live) => {
      // `seen`: the status this card showed. A batch that finished meanwhile (with failures, too) refuses the cancel and keeps its Retry.
      await h.api(`/api/grading/jobs/${job.id}/cancel`, "POST", {seen: job.status});
      if (live()) { h.close(); await done(); h.toast("Batch cancelled. Its ungraded responses are new work for the next batch."); }
    });
}

export function createJob(plan, h, refresh, responseIds = null, {openSettings = null} = {}) {
  const questions = inPrintedOrder(plan.questions).filter(q => q.rubric?.approved_at);
  if (!questions.length) { h.toast("No question has an approved rubric yet. Rubrics are approved in Key & Rubric Finalization.", true); return; }
  let preview = null, requestId = crypto.randomUUID();
  // Grading certified responses again replaces grades that were certified: the preview says so before Start (grading-review-4).
  const certification = plan.workflow?.certification;
  const certified = certification?.state === "current" ? certification.created_at || "current" : "";
  const form = h.dialog("Grade scanned responses", "Choose what to grade, review the batch, then start.",
    batchForm(questions.map(q => [q.question_id, versionTitle(q)]), METHODS.map(mode => [mode, COMPUTE[mode]]), METHODS.includes(plan.default_tier) ? plan.default_tier : "local", responseIds, h),
    "Start grading batch", async (values, _, active) => {
    if (!preview) throw new Error("Find and inspect eligible responses first.");
    try {
      await h.api(`/api/grading/assignments/${plan.id}/jobs`, "POST", {request_id: requestId, question_ids: selected(values),
        selection: values.get("selection"), response_ids: responseIds || undefined, sample: sampleOf(values), fingerprint: preview.fingerprint, mode: values.get("mode"), operation: values.get("operation")},
        {}, BATCH_WAIT);
    } catch (error) {
      if (error.status !== 409) throw error;
      if (active()) {
        preview = null;
        requestId = crypto.randomUUID();
        // Wait for the shared form helper to restore its buttons, then refresh only the preview.
        // A rejected command is never submitted again without another click from the instructor.
        setTimeout(() => { if (active()) void findEligible(error.message); }, 0);
      }
      return;
    }
    if (active()) { h.close(); location.hash = `#grading/${plan.id}?tab=jobs`; await refresh(); h.toast("Grading batch queued."); }
  });
  form.classList.add("grading-batch-form");
  form.elements.mode.setAttribute("aria-describedby", "batch-method-help");
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  let previewGeneration = 0;
  function selected(values) { return values.get("question") === "all" ? questions.map(q => q.question_id) : [values.get("question")]; }
  function sampleOf(values) { return values.get("sample") ? Number(values.get("sample")) : undefined; }
  let sampleTimer;
  form.addEventListener("change", event => {
    if (event.target.name === "operation" && event.target.value !== "recognize") form.elements.selection.value = "all";
    if (["question", "mode", "operation", "sample", "selection"].includes(event.target.name)) {
      clearTimeout(sampleTimer); requestId = crypto.randomUUID(); void findEligible();
    }
  });
  // Editing a limit invalidates the old preview immediately, including before the field loses focus.
  form.elements.sample.addEventListener("input", () => {
    clearTimeout(sampleTimer); ++previewGeneration; requestId = crypto.randomUUID();
    waiting("Checking which responses are eligible…");
    sampleTimer = setTimeout(() => { if (form.isConnected && form.closest("dialog")?.open) void findEligible(); }, 300);
  });
  form.querySelector('[data-grading-action="preview"]').onclick = event => h.run(event.currentTarget, () => findEligible());
  // The batch starts only from a server preview of exactly these choices. The dialog asks for it by itself, on opening and after
  // every change, and says what the Start button is waiting for: a disabled button with a busy cursor once read as a frozen page.
  const waiting = reason => { preview = null; submit.disabled = true; submit.title = reason; };
  async function findEligible(conflict = "") {
    const token = ++previewGeneration, slot = form.querySelector("[data-batch-preview]");
    const live = () => form.isConnected && form.closest("dialog")?.open && token === previewGeneration;
    const error = form.querySelector(".form-error");
    error.textContent = ""; error.hidden = true;
    const notice = conflict ? `<p class="grading-warning" role="status">${h.e(conflict)} The batch was not started. Check the refreshed preview before starting again.</p>` : "";
    form.querySelector('[data-method-help]').textContent = form.elements.operation.value === 'rescore'
      ? 'Recalculate points from saved rubric selections, without calling a model.'
      : methodHelp[form.elements.mode.value] || '';
    if (!form.elements.sample.validity.valid) {
      waiting("Enter a whole number from 1 to 5,000, or leave the limit empty.");
      slot.innerHTML = `<p role="status">${h.e(submit.title)}</p>`;
      form.removeAttribute("aria-busy"); return;
    }
    const before = new FormData(form);
    const ids = selected(before);
    waiting("Checking which responses are eligible…");
    slot.innerHTML = notice + '<p class="loading-state" role="status">Checking which responses are eligible…</p>';
    if (conflict) slot.scrollIntoView({block: "nearest"});
    form.setAttribute("aria-busy", "true");
    let result;
    try {
      result = await h.api(`/api/grading/assignments/${plan.id}/preview`, "POST", {question_ids: ids, selection: before.get("selection"), response_ids: responseIds || undefined, sample: sampleOf(before), mode: before.get("mode"), operation: before.get("operation")},
        {}, BATCH_WAIT);
    } catch (error) {
      if (live()) { waiting("The eligibility check failed."); slot.innerHTML = notice + `<p role="alert">${h.e(error.message)}</p>`; }
      return;
    } finally {
      if (live()) form.removeAttribute("aria-busy");
    }
    if (!live()) return;
    preview = result;
    const readiness = batchReadiness(result, before.get("mode"), before.get("operation"));
    submit.disabled = Boolean(readiness.reason);
    submit.title = readiness.reason;
    slot.innerHTML = notice + batchPreview(result, before.get("mode"), before.get("operation"), h, {changeModels: Boolean(openSettings),
      titles: new Map(questions.map(q => [q.question_id, versionTitle(q)])), certified});
    // Models are workspace settings: close this dialog (nothing was started) and open them; the next batch previews again.
    slot.querySelector('[data-grading-action="change-models"]')?.addEventListener("click", () => { h.close(); openSettings(null); });
    // Everything was graded already: one click chooses "All eligible, including regrading", which previews again (with its warning).
    slot.querySelector("[data-batch-regrade-all]")?.addEventListener("click", () => {
      form.elements.selection.value = "all";
      form.elements.selection.dispatchEvent(new Event("change", {bubbles: true}));
    });
  }
  void findEligible();
}

// What a reset did to this batch. Proposals are withdrawn, never erased, so they can be put back until the responses are graded again.
function resetNotice(job, h) {
  const reset = job.reset?.latest;
  if (!reset) return "";
  const when = new Date(reset.created_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
  if (reset.restored_at) return `<p class="supporting-copy" data-batch-reset>Reset on ${h.e(when)}, then restored: ${h.e(count(reset.restored, "proposal"))} put back.</p>`;
  return `<div class="grading-warning" role="status" data-batch-reset><p><strong>Reset on ${h.e(when)}.</strong> ${h.e(count(reset.withdrawn, "proposal"))} withdrawn so the responses can be graded again${reset.kept_reviewed ? `; ${h.e(count(reset.kept_reviewed, "reviewed response"))} kept your decision` : ""}. ${reset.ungraded ? `${h.e(count(reset.ungraded, "response"))} still ${reset.ungraded === 1 ? "waits" : "wait"} for a new batch: choose the models in Compute settings, then grade them again.` : "All of them have been graded again since."}</p>${reset.ungraded ? `<div class="grading-actions">${h.button("grade-again", "Grade them again")}${h.button("restore-reset", "Put the proposals back")}</div>` : ""}</div>`;
}

// The server checks the confirmation and the exact preview; this dialog only says plainly what will happen.
async function resetDialog(job, h, refresh) {
  const outlook = await h.api(`/api/grading/jobs/${job.id}/reset`), requestId = crypto.randomUUID();
  if (!outlook.withdraw) { h.toast("Nothing to reset: this batch has no unreviewed current proposal."); return; }
  const lines = [
    outlook.kept_reviewed ? `${count(outlook.kept_reviewed, "response")} you already reviewed ${outlook.kept_reviewed === 1 ? "keeps its" : "keep their"} proposal and your decision.` : "",
    outlook.automatic ? `${count(outlook.automatic, "grade")} finalized automatically ${outlook.automatic === 1 ? "is" : "are"} withdrawn with the rest and no longer final.` : "",
    outlook.trial ? `${count(outlook.trial, "trial acceptance")} ${outlook.trial === 1 ? "goes" : "go"} with ${outlook.trial === 1 ? "its proposal" : "their proposals"}.` : "",
    outlook.graded_later ? `${count(outlook.graded_later, "response")} ${outlook.graded_later === 1 ? "was" : "were"} graded again by a later batch; reset that batch for ${outlook.graded_later === 1 ? "it" : "them"}.` : "",
    outlook.busy ? `${count(outlook.busy, "response")} ${outlook.busy === 1 ? "has" : "have"} work under way and ${outlook.busy === 1 ? "is" : "are"} left alone.` : ""].filter(Boolean);
  h.dialog("Reset this batch", "Make its responses ungraded again, so another batch can grade them with other models or settings.",
    `<p><strong>${h.e(count(outlook.withdraw, "proposal"))}</strong> of this batch will be withdrawn. Those responses leave Review and become new work for the next batch.</p>${lines.length ? `<ul>${lines.map(line => `<li>${h.e(line)}</li>`).join("")}</ul>` : ""}<p class="supporting-copy">Nothing is erased: every reading, proposal and call of this batch${outlook.cost ? `, and its recorded cost of $${h.e(Number(outlook.cost).toFixed(2))},` : ""} stays in the record. You can put the proposals back from this batch until the responses are graded again.</p>${h.check("Withdraw these proposals so I can grade the responses again", "understood")}`,
    "Reset batch", async (values, _, live) => {
      if (!values.has("understood")) throw new Error("Tick the box to reset this batch.");
      await h.api(`/api/grading/jobs/${job.id}/reset`, "POST", {request_id: requestId, fingerprint: outlook.fingerprint, confirmation: "reset"});
      if (live()) { h.close(); await refresh(); h.toast("Batch reset. Its responses are new work for the next batch."); }
    });
}

// An optional look at exactly what a cloud batch sends. Nothing waits for it.
async function cloudDialog(job, h) {
  const preview = await h.api(`/api/grading/jobs/${job.id}/cloud-preview`);
  if (!preview.manifest.length) { h.toast("Crops are still being prepared."); return; }
  h.dialog("What this batch sends", job.compute === "jev_routing" ? `${job.profile.jev_routing_rule === "jev-routing-v2" ? "Each problem’s blank-template crops go to Mathpix for transcription. " : ""}Each problem’s transcription and approved rubric go to Jev in its own difficulty request. These response crops, with the problem, solution and rubric, then go directly to that difficulty’s One-shot model.` : job.compute === "tag_team" ? "Both Tag-Team models see these crops with the problem, solution and rubric. The reviewer also receives the first model’s transcript, assessment and any flags. No OCR is used." : job.compute === "one_shot" ? "These crops, with the problem, solution and rubric, go directly to your One-shot model. No OCR or saved transcript is sent." : "These crops, with the transcript, problem, solution and rubric. Faculty keeps the identity mapping; no roster field is sent.",
    preview.manifest.slice(0, 60).map((r, i) => `<section class="grading-panel"><h3>Response ${i + 1}</h3>${r.images.map((image, n) => `<img class="grading-cloud-preview" loading="lazy" src="${h.e(image.url)}" alt="Crop ${i + 1}, region ${n + 1}">`).join("")}</section>`).join("") + (preview.manifest.length > 60 ? `<p class="supporting-copy">Showing the first 60 of ${h.e(preview.manifest.length)} responses.</p>` : ""),
    "Done", async () => { h.close(); });
}
