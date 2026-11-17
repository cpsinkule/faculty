// Flag review on the Review page (requirement, 2026-09-28).
// The model upholds an earlier grade, re-ticks it, or leaves it with the instructor; since v2 (2026-09-29) it may correct a transcript line
// the crop clearly shows and look at the whole page when work runs past the crop. The grades it settles are scored again by an ordinary
// batch and finalized only when every check passes. Its clarifications are proposals for the rubric finalization. This module draws
// GET .../flag-resolution and starts or stops a review; it names no student (the Review step is anonymous).
// In the review workspace it is one line above the desk (requirement, 2026-10-03) and takes room
// only when it has something to say: nothing at all when nothing can be ruled on and nothing was left out; one closed line for what
// was left out or for the last review; the offer, or a running review's progress, as one row with its button and a closed Details
// fold. Models are named by `modelDisplayName`. Polls patch it in place and keep open rulings open (grading-review-8).
// Since 2026-10-11 the review also starts by itself about two minutes after a grading batch (instructor: "Flag review runs itself";
// grading/auto_flag_review.py): the offer says so, or why it did not, and the button stays for a run they ask for.
import {calloutMarkup, whenVisible} from "./ui.js";
import {inPrintedOrder} from "./grading-guide.js";
import {patchKeepingDisclosures} from "./grading-jobs.js";
import {modelDisplayName} from "./grading-model-names.js";
import {adoptProposal} from "./grading-clarify.js";

const url = plan => `/api/grading/assignments/${encodeURIComponent(plan.id)}/flag-resolution`;
const running = r => ["pending", "running"].includes(r?.status);
const n = value => Number(value || 0).toLocaleString();
const count = (value, noun) => `${n(value)} ${noun}${value === 1 ? "" : "s"}`;
const OUTCOME = {adopted: ["success", "Upheld"], revised: ["success", "Rubric lines changed"], unreliable: ["neutral", "Left with you"],
  unsettled: ["warning", "A flag still stands"], failed: ["warning", "No ruling"]};
const FINAL = {finalized: ["success", "Finalized"], held: ["warning", "Still held after the checks"], scoring: ["info", "Being scored"], failed: ["warning", "Scoring failed"]};
const chip = (tone, label, h) => `<span class="chip" data-tone="${tone}">${h.e(label)}</span>`;

const leftOut = preview => (preview?.left_out || []).filter(l => l.responses);
// What a review did to the instructor's queue, said plainly (requirement, 2026-10-17). The finalized grades left the queue; the rest came
// back to it; what it cannot rule on (possible blanks, no line ticked, ...) was never sent and is still in the queue.
const leftTotal = left => left.reduce((s, l) => s + l.responses, 0);
const leftWords = left => `${count(leftTotal(left), "flagged grade")} it cannot rule on, still in your queue`;
const backWords = r => { const back = Number(r.totals?.responses || 0) - Number(r.finalized || 0); return back > 0 ? `, ${n(back)} back in your queue` : ""; };
const leftList = (left, h) => `<ul>${left.map(l => `<li>${h.e(count(l.responses, "response"))}: ${h.e(l.why)}</li>`).join("")}</ul>`;

// How the review works, for the offer's Details fold.
const howMarkup = (preview, model, h) => `<p class="supporting-copy">${h.e(model)} reads the flagged grades of each question and version together, ${h.e(preview.chunk)} at a time with their crops, and answers every flag: it upholds a grade or re-ticks it, correcting a transcript line where the crop clearly shows what the reader missed, and leaves a grade with you only when the evidence itself is missing. When work runs past the crop it looks at the whole page. Settled grades are scored again and finalized only when every check passes. Possible blanks, grades with no line ticked and incomplete pairs stay with you.</p>`;

// What the offer says about the review that starts by itself (`data.automatic`): for which of the grades, or why it did not start.
export function byItselfText(preview, auto) {
  // Graded for participation (2026-11-11): what waits is approved as it is, so no review is sent by itself.
  if (auto?.waits === "participation") return " This assignment is graded for participation: Faculty approves these as they are within a minute or two, so no review starts by itself.";
  const due = auto?.enabled ? Number(auto.due?.responses || 0) : 0;
  if (!due) return "";
  if (auto.not_started) return ` It did not start by itself: ${auto.not_started.reason}`;
  return due >= preview.responses ? " It starts by itself about two minutes after the batch; the button starts it now."
    : ` It starts by itself about two minutes after the batch for the ${n(due)} just graded; the button sends all ${n(preview.responses)} now.`;
}

// What a flag review can be asked to do now (`preview.responses` > 0): one row, its button, and one closed Details fold.
function startMarkup(plan, preview, last, h, auto) {
  const left = leftOut(preview), model = modelDisplayName(preview.model);
  const leftText = left.length ? `<p class="supporting-copy">${h.e(leftWords(left))}:</p>${leftList(left, h)}` : "";
  if (!preview.model) {
    return `<div class="grading-flag-review-row"><span><strong>Flag review</strong> · ${h.e(count(preview.responses, "flagged grade"))} could be ruled on. Choose a Rubric drafting and review model in Compute settings to run it.</span></div>${leftText ? `<details class="disclosure" data-flag-review-fold="left"><summary><span>What it cannot rule on</span></summary><div class="disclosure-body">${leftText}</div></details>` : ""}`;
  }
  const questions = count(preview.questions.length, "question");
  const hint = [last ? lastLine(last, h) : "", left.length ? `${h.e(n(leftTotal(left)))} it cannot rule on` : ""].filter(Boolean).join(" · ");
  return `<div class="grading-flag-review-row"><span><strong>Flag review</strong> · ${h.e(model)} can rule on ${h.e(count(preview.responses, "flagged grade"))} in ${h.e(questions)}; anything it cannot settle stays with you.${h.e(byItselfText(preview, auto))}</span><button type="button" class="button" data-flag-review-start>Send ${h.e(count(preview.responses, "flagged grade"))} to ${h.e(model)}</button></div>
    <details class="disclosure" data-flag-review-fold="how"><summary><span>Details</span>${hint ? `<span class="disclosure-hint">${hint}</span>` : ""}</summary><div class="disclosure-body">${howMarkup(preview, model, h)}${leftText}${last ? resultMarkup(plan, last, h) : ""}</div></details>`;
}

// Each proposal can be added to the rubric for its version and, when the review worded it for them (prompt v3), the same line of the
// other versions (requirement, 2026-10-03), in one dialog the instructor edits and confirms (`grading-clarify.js adoptProposal`).
function clarificationsMarkup(plan, q, h) {
  if (!q.clarifications?.length) return "";
  return `<div class="grading-flag-review-clarifications"><p><strong>Proposed clarifications</strong> <span class="supporting-copy">Nothing changes until you add one to the rubric; grading the affected responses again is a separate step.</span></p><ul>${q.clarifications.map((c, index) => {
    const line = q.line_text?.[c.line_id], versions = (c.linked || []).map(other => other.version).filter(Boolean);
    return `<li><strong title="${h.e(line?.text || "")}">${h.e(c.line_id)}</strong>: ${h.e(c.clarification)}${c.why ? ` <span class="supporting-copy">(${h.e(c.why)})</span>` : ""}${versions.length ? ` <span class="supporting-copy">Also worded for Version${versions.length === 1 ? "" : "s"} ${h.e(versions.join(", "))}.</span>` : ""} <button type="button" class="text-button" data-flag-review-adopt="${h.e(q.question_id)}" data-flag-review-index="${index}">Add to the rubric…</button> <button type="button" class="text-button" data-flag-review-copy="${h.e(c.clarification)}">Copy</button></li>`;
  }).join("")}</ul><a class="text-button" href="#grading/${h.e(plan.id)}?tab=answer-key&open=quality">Open Key &amp; Rubric Finalization</a></div>`;
}

function questionMarkup(plan, q, h) {
  const tally = {};
  q.responses.forEach(r => { tally[r.final || r.outcome] = (tally[r.final || r.outcome] || 0) + 1; });
  const rows = q.responses.map(r => {
    const [tone, label] = FINAL[r.final] || OUTCOME[r.outcome] || ["neutral", r.outcome];
    const open = r.flags.filter(f => !f.resolved).length;
    const changes = (r.ruling?.changes || []).map(c => `${c.kind === "add" ? "added" : "corrected"} ${c.line_id}: ${c.saw}`);
    const seen = [r.ruling?.page_round ? "Looked at the whole page." : "", changes.length ? `Transcript ${changes.join("; ")}` : ""].filter(Boolean).join(" ");
    return `<li>${chip(tone, label, h)} <strong>${h.e(r.ref)}</strong>${r.score_before !== null && r.score_before !== undefined ? ` <span class="supporting-copy">was ${h.e(r.score_before)}</span>` : ""} · ${h.e(r.ruling?.reason || r.ruling?.error || "")}${open && r.outcome !== "failed" ? ` <span class="supporting-copy">(${h.e(count(open, "flag"))} left standing)</span>` : ""}${r.outcome_note ? ` <span class="supporting-copy">${h.e(r.outcome_note)}</span>` : ""}${seen ? `<br><span class="supporting-copy">${h.e(seen)}</span>` : ""}</li>`;
  }).join("");
  const facts = Object.entries(tally).map(([k, v]) => `${n(v)} ${(FINAL[k] || OUTCOME[k] || [0, k])[1].toLowerCase()}`).join(" · ");
  return `<details class="disclosure" data-flag-review-question="${h.e(q.question_id)}"><summary><span><strong>${h.e(q.label)}</strong> · ${h.e(facts)}</span></summary><div class="disclosure-body">${q.error ? calloutMarkup({tone: "warning", title: "This question could not be reviewed", body: q.error}) : ""}${q.summaries?.length ? `<p>${h.e(q.summaries.join(" "))}</p>` : ""}${clarificationsMarkup(plan, q, h)}<ul class="grading-flag-review-rulings">${rows}</ul></div></details>`;
}

function resultMarkup(plan, r, h) {
  if (r.reset) return calloutMarkup({tone: "neutral", title: "Original flags restored",
    body: `${n(r.reset.restored)} grades restored; ${n(r.reset.already_original)} were already unchanged.${r.reset.kept_reviewed ? ` ${count(r.reset.kept_reviewed, "instructor decision")} kept.` : ""}${r.reset.changed ? ` ${count(r.reset.changed, "changed response")} left alone.` : ""} This review's model replies and grades are retained.`});
  if (running(r)) {
    const p = r.progress || {};
    const received = `${n(p.answered)} ${p.answered === 1 ? "reply" : "replies"} received`;
    const active = p.running ? `${count(p.running, "request")} in progress` : r.status === "pending" ?
      (p.requests ? "Queued to continue" : "Queued to start") : p.answered ? "Processing the review" : "Preparing requests";
    const reasons = {usage_limit: "Subscription usage limit reached", capacity: "Model provider is busy",
      unknown: "Waiting to retry an unconfirmed request", retry: "Waiting to retry"};
    const until = p.wait?.until && new Date(p.wait.until);
    const retry = until && Number.isFinite(until.getTime()) ? `; retry scheduled for ${until.toLocaleString(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"})}` : "";
    const status = p.wait ? `${reasons[p.wait.kind] || reasons.retry}${retry}` : `${active}. Additional checks may follow`;
    return `<div class="grading-flag-review-row" role="status"><span><strong>Reviewing ${h.e(count(r.responses, "flagged grade"))} in ${h.e(count(r.questions, "question"))}</strong> · ${h.e(modelDisplayName(r.model))}: ${h.e(received)}. ${h.e(status)}. Grades update when the review finishes.</span><button type="button" class="button" data-flag-review-stop="${h.e(r.id)}">Stop</button></div>`;
  }
  if (r.status === "failed") {
    return calloutMarkup({tone: r.cancelled ? "neutral" : "warning", title: r.cancelled ? "The flag review was stopped" : "The flag review could not be completed", body: r.error || ""});
  }
  const t = r.totals || {};
  const scoring = isScoring(r);
  const head = calloutMarkup({tone: "success", title: `${n(r.finalized)} of ${count(t.responses, "flagged grade")} finalized${scoring ? " so far" : backWords(r)}`,
    body: `${modelDisplayName(r.model)} upheld ${n(t.adopted)}, changed the rubric lines of ${n(t.revised)}, left ${n(t.unreliable)} with you${t.unsettled ? `, and left a flag standing on ${n(t.unsettled)}` : ""}${t.failed ? `; ${n(t.failed)} got no ruling` : ""}. ${t.clarifications ? `It proposed ${count(t.clarifications, "clarification")} for the rubric finalization.` : ""} Finalized grades are marked "flag review" and stay editable in the ordinary review.`});
  // Questions in printed order, not the order they were stored in (by question id), including reviews stored before this.
  const order = new Map(inPrintedOrder(plan.questions || []).map((q, i) => [q.question_id, i]));
  const at = q => order.get(q.question_id) ?? order.size;
  const questions = [...(r.result?.questions || [])].sort((a, b) => at(a) - at(b));
  return `${head}${questions.map(q => questionMarkup(plan, q, h)).join("")}`;
}

const isScoring = r => (r?.result?.questions || []).some(q => q.responses.some(x => x.final === "scoring"));

// "Last flag review · 2 Oct, 10:42 · 11 of 14 flagged grades finalized"
function lastLine(r, h) {
  const when = new Date(r.reset?.at || r.updated_at).toLocaleString(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
  if (r.reset) return `Flag review reset · ${h.e(when)} · original flags restored`;
  const outcome = r.status === "failed" ? (r.cancelled ? "stopped" : "could not be completed")
    : `${n(r.finalized)} of ${count(r.totals?.responses, "flagged grade")} finalized${isScoring(r) ? " so far" : backWords(r)}`;
  return `Last flag review · ${h.e(when)} · ${h.e(outcome)}`;
}

/** The flag review's markup, or "" when it has nothing to show (nothing can be ruled on, nothing was left out, no review yet). */
export function flagReviewMarkup(plan, data, h) {
  const r = data?.resolution, preview = data?.preview || {}, left = leftOut(preview);
  const line = body => `<section class="grading-flag-review" data-flag-review>${body}</section>`;
  // Running: its progress and Stop, one row.
  if (r && running(r)) return line(resultMarkup(plan, r, h));
  // It can run: one sentence and the button; how it works, the last review and what is left out fold into Details.
  if (preview.responses) return line(startMarkup(plan, preview, r, h, data.automatic));
  // Nothing to rule on now. The last review is one line, open while its grades are still being scored (just finished).
  const leftFold = left.length ? `<p class="supporting-copy">${h.e(leftWords(left))}:</p>${leftList(left, h)}` : "";
  if (r) return line(`<details class="disclosure" data-flag-review-last${isScoring(r) ? " open" : ""}><summary><span>${lastLine(r, h)}</span></summary><div class="disclosure-body">${resultMarkup(plan, r, h)}${leftFold}</div></details>`);
  if (left.length) return line(`<details class="disclosure" data-flag-review-fold="left"><summary><span>Flag review · nothing it can rule on now</span></summary><div class="disclosure-body">${leftFold}</div></details>`);
  return "";
}

// What each slot was last drawn from: an unchanged poll writes nothing (FRONTEND "Quiet refresh contract").
const drawn = new WeakMap();
// The disclosures open when the last grades were scored and the page was drawn again for its new counts, by plan: the new panel
// opens them again, so a ruling being read stays open through that redraw too.
const reopen = new Map();
// Each disclosure's name, by which the patch keeps it open or closed (dom-patch matches by position, so a callout that comes or goes
// would otherwise move one disclosure's state onto another).
const disclosureKey = node => node.dataset.flagReviewQuestion ? `question:${node.dataset.flagReviewQuestion}`
  : node.hasAttribute("data-flag-review-last") ? "last" : node.dataset.flagReviewFold || "";

/** Draws the flag review into `slot` and keeps it current while one runs; removes the slot when there is nothing to show. */
export async function mountFlagReview(slot, plan, h, active, onChange, onWaiting) {
  let timer = 0, busy = false, told = "";
  // A hidden tab reads once it is shown again; a failed read tries again in 15 s instead of stopping (bugs-frontend-4).
  const later = wait => { timer = setTimeout(() => whenVisible(() => { draw().catch(() => { if (active() && slot.isConnected) later(15000); }); }), wait); };
  const draw = async () => {
    clearTimeout(timer);
    if (!active() || !slot.isConnected) return;
    const data = await h.api(url(plan));
    if (!active() || !slot.isConnected) return;
    // The responses a review is ruling on now, or is about to by itself: the workspace steps past them (`onWaiting`).
    const due = !data.automatic?.not_started && data.automatic?.enabled ? data.automatic.due?.response_ids || [] : [];
    const waiting = running(data.resolution) ? data.resolution.response_ids || [] : due;
    if (told !== waiting.join()) { told = waiting.join(); onWaiting?.(waiting); }
    const markup = flagReviewMarkup(plan, data, h);
    if (!markup) { slot.remove(); return; }
    // Patched in place: a ruling the instructor opened stays open through the 5 s polls while grades are scored.
    if (drawn.get(slot) !== markup) {
      const first = !drawn.has(slot);
      patchKeepingDisclosures(slot, markup, disclosureKey);
      drawn.set(slot, markup);
      const kept = first ? reopen.get(plan.id) : null;
      reopen.delete(plan.id);
      if (kept) slot.querySelectorAll("details").forEach(node => { if (kept.has(disclosureKey(node))) node.open = true; });
    }
    bind(data);
    const now = running(data.resolution) || isScoring(data.resolution);
    if (busy && !now) {  // Grades were finalized: the queue's counts changed, and the page is drawn again.
      reopen.set(plan.id, new Set([...slot.querySelectorAll("details[open]")].map(disclosureKey).filter(Boolean)));
      onChange?.();
    }
    busy = now;
    // While one is about to start by itself, look again so the row turns into its progress without a reload.
    if (now) later(5000); else if (due.length) later(15000);
  };
  const bind = data => {
    const start = slot.querySelector("[data-flag-review-start]");
    if (start) start.onclick = event => h.run(event.currentTarget, async () => {
      await h.api(url(plan), "POST", {request_id: crypto.randomUUID()});
      await draw();
    });
    const stop = slot.querySelector("[data-flag-review-stop]");
    if (stop) stop.onclick = event => h.run(event.currentTarget, async () => {
      await h.api(`${url(plan)}/${encodeURIComponent(stop.dataset.flagReviewStop)}/cancel`, "POST", {});
      await draw();
    });
    slot.querySelectorAll("[data-flag-review-adopt]").forEach(button => { button.onclick = event => h.run(event.currentTarget, async () => {
      const q = (data.resolution?.result?.questions || []).find(item => item.question_id === button.dataset.flagReviewAdopt);
      const c = q?.clarifications?.[Number(button.dataset.flagReviewIndex)];
      if (c) await adoptProposal(plan, h, {question_id: q.question_id, ...c}, {active, certified: plan.workflow?.certification?.state === "current"});
    }); });
    slot.querySelectorAll("[data-flag-review-copy]").forEach(button => { button.onclick = async () => {
      try { await navigator.clipboard.writeText(button.dataset.flagReviewCopy); h.toast("Copied."); } catch { h.toast("Could not copy.", true); }
    }; });
  };
  await draw();
}
