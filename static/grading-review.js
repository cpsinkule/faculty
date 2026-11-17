import {proposalComment} from "./grading-model-comments.js";
import {reviewSignalsMarkup} from "./grading-review-metrics.js";
import {emptyMarkup, loadingMarkup} from "./ui.js";
import {createJob} from "./grading-jobs.js";
import {answerGroupsDialog} from "./grading-groups.js";
import {inPrintedOrder, lateScan, versionTitle} from "./grading-guide.js";
import {mathText, transcriptMarkup} from "./grading-math.js";
import {cropButton} from "./grading-crops.js";
import {labels} from "./grading-ui.js";
import {acceptanceDialog} from "./grading-acceptance.js";
import {queueView, groupName, reviewHref} from "./grading-review-queue.js";
import {mountReviewDesk, closeReviewDesk} from "./grading-review-desk.js";
import {responseReviewUrl} from "./grading-review-desk.js";
import {reviewWorkspace} from "./grading-review-workspace.js";

// The desk itself (markup, keys, score preview, saving) is grading-review-desk.js, shared with Certification and Export.

/**
 * The button at the head of All responses: the way on to Certify, shown whatever is left to review (inside an assignment a quiet
 * link, grading.css). While a later scan of the assignment is still in its scan checks and no certification is current (GRADING
 * "THE CERTIFY STEP WAITS FOR A LATE SCAN"; requirement, 2026-10-11) it leads to the waiting scans (Collect, I2), with the reason as
 * its title: certification refuses until the scan is through. Pure.
 * @param {any} plan @param {{e: (value: any) => string}} h
 */
export function onwardLink(plan, h) {
  const late = lateScan(plan?.workflow, plan);
  return late ? `<a class="button" href="${h.e(late.href)}" title="${h.e(`${late.waits}: ${late.then}.`)}" data-review-onward="scan">${h.e(late.label)}</a>`
    : `<a class="button" href="#grading/${h.e(plan.id)}?tab=export" data-review-onward="certify">Continue to Certify</a>`;
}
const STATES = ["needs-review", "trial", "approved", "automatic"];
const WAITING = r => !["approved", "manual", "auto_accepted", "pending", "stale"].includes(r.state);
// A random sample for a spot-check: crypto.getRandomValues, Fisher–Yates. Nothing is stored; a reload draws again.
export function spotSample(ids, size, random = crypto) {
  const pool = [...ids], draw = new Uint32Array(1);
  for (let i = pool.length - 1; i > 0; i--) {
    random.getRandomValues(draw);
    const j = draw[0] % (i + 1);
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return pool.slice(0, Math.max(0, size));
}
const count = (n, noun) => `${Number(n).toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;

// Where to go when a group has nothing left: the question's next group, else the next question with work waiting, else
// certification (the sixth step, Certification and Export).
export function continueStep(plan, queue, scope, rules = null) {
  const batch = scope.job ? {job: scope.job} : {};
  const entries = new Map((queue?.questions || []).map(q => [q.question_id, q]));
  const question = plan.questions.find(q => q.question_id === scope.question);
  const nextGroup = entries.get(scope.question)?.groups.find(g => g.id !== scope.group && g.size > 0);
  if (nextGroup) return {title: "Group done.", detail: `${count(nextGroup.size, "response")} ${nextGroup.size === 1 ? "waits" : "wait"} in the next group of ${question ? versionTitle(question) : "this question"}.`,
    label: `Next: ${groupName(nextGroup, rules)}`, href: reviewHref(plan, {...batch, question: scope.question, group: nextGroup.id})};
  const ordered = inPrintedOrder(plan.questions), at = ordered.findIndex(q => q.question_id === scope.question);
  const next = [...ordered.slice(at + 1), ...ordered.slice(0, Math.max(0, at))].find(q => entries.get(q.question_id)?.waiting);
  if (next) return {title: `${question ? versionTitle(question) : "This question"} is reviewed.`, detail: `${count(entries.get(next.question_id).waiting, "response")} ${entries.get(next.question_id).waiting === 1 ? "waits" : "wait"} in the next question.`,
    label: `Next question: ${versionTitle(next)}`, href: reviewHref(plan, {...batch, question: next.question_id})};
  if (scope.job && queue?.other_waiting) return {title: "This batch is reviewed.", detail: `${count(queue.other_waiting, "response")} from other batches still ${queue.other_waiting === 1 ? "waits" : "wait"}.`,
    label: "Review all batches", href: reviewHref(plan)};
  return {title: "Everything is reviewed.", detail: "No response of this assignment waits for your review.", label: "Certification", href: `#grading/${plan.id}?tab=export`};
}

// scope: nothing opens the review workspace on the next waiting response (or says what is next when nothing waits), {question} its
// groups and review tools, {question, group} the workspace on that group, {browse} or {response} every response with the filters
// and the tools for several responses ({question, status} preselect them; {spot: N} draws N automatic grades at random for a
// spot-check). `open: "failed"` opens Failed to grade. `onChanged` is called whenever this step reads its responses again after
// something changed them (a decision saved, a gallery approval, a regrade or retry sent, a finished flag review): the page's step
// graph follows from the plan (grading.js `planFollower`); this step draws nothing for it.
export async function reviewView(host, plan, h, refresh, active, scope = {}, address = () => {}, {open = "", onChanged = null} = {}) {
  closeReviewDesk();
  if (!plan) { host.innerHTML = '<p class="empty-state">Choose an assessment to review its responses.</p>'; return; }
  const workspace = (scope.question && scope.group) || (!scope.question && !scope.browse && !scope.response);
  if (workspace) { await reviewWorkspace(host, plan, h, active, scope, address, {open, onChanged}); return; }
  if (!scope.browse && !scope.response) { await queueView(host, plan, h, refresh, active, scope); return; }
  // All responses reads only the chosen question again after a save, keeping the others until another question is shown.
  // On Exam 1 the whole list is 2.6 MB, one question's 0.16 MB (PERFORMANCE perf-12).
  let othersStale = false, refreshGeneration = 0;
  async function waiting(scoped = false) {
    if (scoped && selection) {
      const question = selection;
      const answer = await h.api(`/api/grading/assignments/${plan.id}/responses?${new URLSearchParams({question_id: question})}`);
      const rows = (answer.responses || []).filter(r => r.question_id === question);
      const at = responses.findIndex(r => r.question_id === question), rest = responses.filter(r => r.question_id !== question);
      othersStale = true;
      return at < 0 ? [...rest, ...rows] : [...rest.slice(0, at), ...rows, ...rest.slice(at)];
    }
    const all = (await h.api(`/api/grading/assignments/${plan.id}/responses`)).responses;
    othersStale = false;  // Only after a successful full read.
    return all;
  }
  // Before another question's rows are shown after a scoped read, the whole list is read again. A read that fails says why and
  // shows nothing new: the caller keeps the question on screen (and its select) as it was.
  async function fresh() {
    const token = ++refreshGeneration;
    if (!othersStale) return true;
    try {
      const list = await waiting();
      if (!active() || token !== refreshGeneration) return false;
      responses = list;
      return true;
    } catch (error) {
      if (active() && token === refreshGeneration) h.toast(error.message, true);
      return false;
    }
  }
  let responses = await waiting();
  if (!active()) return;
  const question = plan.questions.find(q => q.question_id === scope.question);
  let selection = question ? question.question_id : "";
  // With work waiting in the chosen question (or anywhere, for all questions), all responses opens on what needs review, so
  // approving moves on to the next one. A finished question opens on its responses, never on an empty "needs review".
  let status = STATES.includes(scope.status) ? scope.status : scope.spot ? "automatic" : !scope.response && responses.some(r => (!selection || r.question_id === selection) && WAITING(r)) ? "needs-review" : "";
  let index = Math.max(0, responses.findIndex(r => r.id === scope.response)), generation = 0;
  const statusLine = '<p class="sr-only" data-review-status aria-live="polite"></p>';
  // Legacy tools only where they apply (grun:P15): clean proposals finalize by themselves under Cloud. Trial acceptance stays in
  // the closed fold whatever the responses say: its dialog is the only place an enabled automatic-acceptance permission is
  // turned off, and that permission outlives the trial-accepted results it was measured on (a regrade, a batch reset).
  const tools = {bulk: responses.some(r => r.state === "proposed" && !r.review_status)};
  host.innerHTML = `<p><a class="text-button" href="${h.e(reviewHref(plan))}">← Review</a></p><header class="grading-heading"><p class="section-lead">Inspect the handwriting and rubric, then approve each decision.</p>${onwardLink(plan, h)}</header><details class="disclosure grading-tools"><summary><span>Review several responses together</span></summary><div class="grading-tool-options"><div>${h.button("groups", "Grade by answer group")}<p>Compare similar answers side by side and inspect every response before approving.</p></div>${tools.bulk ? `<div>${h.button("bulk", "Review proposed grades")}<p>Inspect current proposals without unresolved review flags and select the ones to approve.</p></div>` : ""}<div>${h.button("acceptance", "Trial acceptance")}<p>Local and older workflows: see what the risk policy accepted as a test, review those responses (the blind ones first), read the measured error rate, or turn an automatic-acceptance permission off. Trial acceptances are never final.</p></div></div></details><details class="disclosure grading-tools"><summary><span>Regrading and diagnostics</span></summary><div class="grading-tool-options"><div>${h.button("rerun", "Grade / regrade")}<p>Reread handwriting, grade saved readings again, or recalculate points.</p></div><div>${h.button("metrics", "Compare with approved work")}<p>Compare model proposals with decisions you have already approved.</p></div></div></details><div class="grading-review-filters">${h.select("Question", "review-question", [["", "All questions"], ...inPrintedOrder(plan.questions).map(q => [q.question_id, versionTitle(q)])], selection)}${h.select("Show responses", "review-status", [["", "All responses"], ["needs-review", "Needs review"], ["trial", "Trial-accepted · not final"], ["approved", "Approved grades"], ["automatic", "Finalized automatically"]], status)}<aside class="grading-review-queue" aria-label="Response queue"></aside><p class="supporting-copy" data-review-progress></p></div><div data-spot-check></div>${statusLine}<div class="grading-review-layout"><div class="grading-review-work"></div></div>`;
  const queue = host.querySelector(".grading-review-queue"), work = host.querySelector(".grading-review-work");
  const titles = new Map(plan.questions.map(q => [q.question_id, versionTitle(q)]));  // "Question 1 · Version A", never "q1-A"
  // A certified assignment's desk asks before grading one of its responses again (grading-review-4).
  const certification = plan.workflow?.certification;
  const certified = certification?.state === "current" ? certification.created_at || "current" : "";
  // An automatically accepted score is final; a trial acceptance never is, so it stays with the work that needs a look.
  const approved = r => ["approved", "manual", "auto_accepted"].includes(r.state);
  const matches = r => !status || (status === "approved" ? approved(r) : status === "trial" ? r.state === "trial_accepted" : status === "automatic" ? r.state === "auto_accepted" : WAITING(r));
  // `only`: the accepted responses waiting for a confirm-or-overturn look, blind ones first, in the order the server gave; or a
  // spot-check's random sample.
  let only = null, spotEmpty = "";
  function filtered() {
    const rows = responses.filter(r => (!selection || r.question_id === selection) && matches(r));
    return only ? only.map(id => rows.find(r => r.id === id)).filter(Boolean) : rows;
  }
  function drawSpot() {
    const size = Math.min(50, Math.max(1, Number.parseInt(scope.spot, 10) || 10));
    const pool = responses.filter(r => r.state === "auto_accepted" && (!selection || r.question_id === selection)).map(r => r.id);
    only = spotSample(pool, size); index = 0;
    const where = selection ? versionTitle(plan.questions.find(q => q.question_id === selection)) : "this assignment";
    // Nothing to draw from is said once, by the empty state (`load`), not in a line of its own above it.
    spotEmpty = only.length ? "" : `No grade of ${where} was finalized automatically, so there is nothing to spot-check.`;
    host.querySelector("[data-spot-check]").innerHTML = only.length
      ? `<p class="supporting-copy grading-spot-check">Spot-check: ${h.e(count(only.length, "automatic grade"))} of ${h.e(where)}, drawn at random from ${h.e(count(pool.length, "grade"))}. A change you save is an ordinary review. <button type="button" class="text-button" data-grading-action="spot-again">Draw another ${h.e(only.length)}</button></p>`
      : "";
    host.querySelector('[data-grading-action="spot-again"]')?.addEventListener("click", () => { if (leave()) { drawSpot(); void load(); } });
  }
  function renderQueue() {
    const rows = filtered();
    index = Math.max(0, Math.min(index, rows.length - 1));
    const selected = responses.filter(r => !selection || r.question_id === selection);
    // Final grades, with how many of them were finalized automatically: "approved" read as the instructor's own (grading-review-9).
    const automatic = selected.filter(r => r.state === "auto_accepted").length;
    host.querySelector("[data-review-progress]").textContent = `${selected.filter(approved).length} of ${selected.length} final${automatic ? ` (${automatic} automatic)` : ""} · ${rows.length} shown`;
    queue.innerHTML = rows.length ? h.select("Response", "review-response", rows.map((r, i) => [r.id, `${i + 1} of ${rows.length} · ${titles.get(r.question_id) || r.question_key} · ${labels[r.state] || r.state}`]), rows[index]?.id)
      : responses.length ? "" : '<p class="supporting-copy">No responses yet. Start a batch in the Grade step.</p>';  // A filtered-out view says so once, in the empty state below.
    const picker = queue.querySelector("select");
    if (picker) picker.onchange = () => {
      if (leave()) { index = rows.findIndex(r => r.id === picker.value); void load(); }
      else picker.value = rows[index].id;
    };
  }
  // Moving between responses inside the desk asks through the desk's leave(); leaving the page asks through its leave guard.
  let desk = null;
  function leave() { return desk ? desk.leave() : true; }
  async function load(reveal = false) {
    const token = ++generation;
    renderQueue();
    const row = filtered()[index];
    if (!row) {
      closeReviewDesk(); desk = null;
      const focusNext = document.activeElement === document.body || !document.activeElement?.isConnected;
      work.innerHTML = responses.length && (status || selection) ? emptyMarkup({kind: "filtered", title: spotEmpty ? "Nothing to spot-check" : status === "needs-review" ? "Nothing here needs your review" : "No responses match these filters",
        body: spotEmpty || (status && selection ? "Show every response of this question to look at the others." : "Show every response to look at the others."), action: {label: "Show all responses", data: {reviewShowAll: ""}}}) : "";
      // Clears the status (and a spot-check) first and keeps the question; with no status left, it clears the question.
      work.querySelector("[data-review-show-all]")?.addEventListener("click", async () => {
        if (status || only) { status = ""; only = null; spotEmpty = ""; } else { if (!await fresh()) return; selection = ""; }
        host.querySelector('[name="review-status"]').value = status; host.querySelector('[name="review-question"]').value = selection;
        host.querySelector("[data-spot-check]").innerHTML = "";
        void load();
      });
      if (focusNext) work.querySelector(".next-step a, [data-review-show-all]")?.focus({preventScroll: true});
      return;
    }
    work.innerHTML = loadingMarkup("Loading original work and rubric…", 5);
    try {
      // `details=review`: the desk's reasons gathered by cause (GRADING, 2026-10-11); nothing more for a hidden proposal.
      const value = await h.api(responseReviewUrl(row.id, reveal));
      if (!active() || token !== generation) return;
      draw(value, reveal);
    } catch (error) { if (active() && token === generation) work.innerHTML = `<p role="alert">${h.e(error.message)}</p>`; }
  }
  let shown = "";  // the response the last desk showed: a desk for another one replaces it on screen (`moved`)
  function draw(value, reveal = false) {
    const total = filtered().length;
    // What waits is read again and the desk shows what is next. A response marked unreadable or rejected still waits, so the one after
    // it is shown; a draft stays on screen for Regrade with Cloud (grading-review-1).
    const reread = async status => {
      onChanged?.();
      const list = await waiting(true);
      if (!active()) return;
      responses = list;
      if (status === "unreadable" || status === "rejected") {
        const rows = filtered(), at = rows.findIndex(r => r.id === value.id);
        if (at >= 0 && at < rows.length - 1) index = at + 1;
      }
      await load();
    };
    desk = mountReviewDesk(work, value, h, {index, total, active, certified, moved: Boolean(shown) && shown !== value.id,
      student: `/api/grading/assignments/${encodeURIComponent(plan.id)}/responses/${encodeURIComponent(value.id)}/student`,
      onMove: step => { index = Math.max(0, Math.min(filtered().length - 1, index + step)); void load(); },
      onReveal: () => { void load(true); },
      onSaved: status => reread(status),
      // The response changed since it was read (a regrade landed, the flag review finalized it, another tab saved it): the desk keeps
      // the edits, reads this response as this page did, and the waiting list is read again around it (GRADING "KEEP CONFLICTED EDITS").
      reread: () => h.api(responseReviewUrl(value.id, reveal)),
      onConflict: async () => {
        onChanged?.();
        const list = await waiting(true);
        if (!active()) return;
        responses = list;
        const at = filtered().findIndex(r => r.id === value.id);
        if (at >= 0) index = at;
      }});
    shown = value.id;  // a redraw of the same response (Save as draft, a stale reread, Show proposal) keeps the page where it is
    // One short line is announced; the desk itself is not a live region. Focus goes to the response's heading after a move.
    const said = host.querySelector("[data-review-status]");
    if (said && value.result) said.textContent = `${value.question?.content?.title || "Response"} · response ${index + 1} of ${total}`;
  }
  const clearSpot = () => { spotEmpty = ""; host.querySelector("[data-spot-check]").innerHTML = ""; };
  let questionChoice = 0;
  host.querySelector('[name="review-question"]').onchange = async event => {
    const token = ++questionChoice;
    if (!leave()) { event.target.value = selection; return; }
    const chosen = event.target.value;
    const ready = await fresh();
    if (token !== questionChoice) return;
    if (!ready) { event.target.value = selection; return; }
    selection = chosen; only = null; clearSpot(); index = 0; void load();
  };
  host.querySelector('[data-grading-action="rerun"]').onclick = () => { if (leave()) createJob(plan, h, refresh); };
  const trialButton = host.querySelector('[data-grading-action="acceptance"]');
  if (trialButton) trialButton.onclick = event => h.run(event.currentTarget, async () => {
    if (!leave()) return;
    const question = selection || responses.find(r => ["trial_accepted", "auto_accepted"].includes(r.state))?.question_id || plan.questions[0]?.question_id;
    await acceptanceDialog(plan, question, h, refresh, active, async ids => {
      if (question !== selection && !await fresh()) return;
      clearSpot(); only = ids; selection = question; status = ""; host.querySelector('[name="review-status"]').value = ""; host.querySelector('[name="review-question"]').value = question; index = 0; void load();
    });
  });
  host.querySelector('[name="review-status"]').onchange = event => { if (leave()) { status = event.target.value; only = null; clearSpot(); index = 0; void load(); } else event.target.value = status; };
  const bulkButton = host.querySelector('[data-grading-action="bulk"]');
  if (bulkButton) bulkButton.onclick = event => h.run(event.currentTarget, async () => {
    if (!leave()) return;
    const candidates = filtered().filter(r => r.state === "proposed").slice(0, 100);
    const values = [];
    // Read in parallel, eight at a time, instead of one after another.
    for (let start = 0; start < candidates.length; start += 8) {
      const chunk = await Promise.all(candidates.slice(start, start + 8).map(row => h.api(`/api/grading/responses/${row.id}`)));
      if (!active()) return;
      values.push(...chunk.filter(response => response.current && !response.hidden && !response.review));
    }
    if (!values.length) { h.toast("No current, unreviewed proposals without review flags."); return; }
    h.dialog("Inspect proposed grades", "Check each response you have inspected. Only checked responses will be approved.", values.map((v, i) =>
      `<section class="grading-panel">${h.check(`Approve response ${i + 1} · ${versionTitle(v.question)} · ${v.result.score.score} points`, v.id)}${v.crops.map(c => cropButton(c.images.original.url, `Response ${i + 1} handwriting`, h)).join("")}<div class="grading-source">${transcriptMarkup(v.result.transcript, h)}</div><ul>${v.result.applications.filter(a => a.applied).map(a => `<li>${h.e(a.rubric_item_id)} · ${proposalComment(v.result.signals, a.reason, h)}<br>${mathText(a.evidence.map(e => e.student_text).join("; "), h)}</li>`).join("")}</ul></section>`).join(""),
      "Approve inspected responses", async (selected, _, live) => {
        const chosen = values.filter(v => selected.has(v.id)).map(v => ({id: v.id, version: v.version}));
        if (!chosen.length) throw new Error("Inspect and select at least one response.");
        await h.api(`/api/grading/assignments/${plan.id}/approve`, "POST", {responses: chosen, confirm_review: true});
        if (live()) { h.close(); await refresh(); }
      });
  });
  host.querySelector('[data-grading-action="groups"]').onclick = event => h.run(event.currentTarget, async () => {
    if (!leave()) return;
    // One question at a time: the chosen one, else the first with a proposal waiting.
    const open = responses.filter(r => !r.review_status && ["proposed", "grading_review", "ocr_review", "both", "trial_accepted"].includes(r.state));
    const questionId = selection || open[0]?.question_id;
    if (!questionId) { h.toast("No proposals are waiting. Start a batch in the Grade step."); return; }
    await answerGroupsDialog(plan, questionId, h, refresh, active);
  });
  host.querySelector('[data-grading-action="metrics"]').onclick = event => h.run(event.currentTarget, async () => {
    const report = await h.api(`/api/grading/assignments/${plan.id}/metrics`);
    if (!active()) return;
    const names = Object.keys(report.profiles);
    const markup = names.length ? names.map(name => `<section class="grading-panel"><h3>${h.e(name)}</h3><dl class="grading-health">${Object.entries(report.profiles[name]).filter(([key]) => key !== "note").map(([key, value]) => `<div><dt>${h.e(key.replaceAll("_", " "))}</dt><dd>${value === null ? "Not available" : h.e(Number.isInteger(value) ? value : Number(value.toFixed(3)))}</dd></div>`).join("")}</dl><p class="supporting-copy">${h.e(report.profiles[name].note)}</p></section>`).join("") : '<p>No human-approved comparisons yet.</p>';
    h.dialog("Agreement with approved work", "Results describe the reviewed sample. Compare model profiles on the same questions and rubric revisions.", reviewSignalsMarkup(report.review_signals, h) + `<details class="disclosure"><summary><span>Detailed model comparisons</span></summary>${markup}</details>`, null, async () => h.close());
  });
  if (scope.spot) drawSpot();
  await load();
}
