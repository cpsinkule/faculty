// One review desk: a response's crops, reading and rubric beside the decision form, saved through the ONLY final-change path,
// POST /api/grading/responses/<rid>/review (append-only reviews; the server checks the version and the current result). Clicking
// Approve or Record manual grade IS the confirmation (requirement, 2026-09-27). The Review step mounts it one response at a time with previous/next; Certification and Export's student
// drawer mounts it for one question with approved and manual only, since a draft takes an automatic grade out of final (the
// draft trap) and an unreadable or rejected review leaves the question without a grade (critique C1, 2026-09-26).
// One desk at a time: mounting another destroys the one on screen, its keys and its leave guard with it (callers that may hold
// unsaved work ask through `leave()` first).
import {runFormAction, registerShortcut, registerLeaveGuard} from "./ui.js";
import {rubricDisplay} from "./grading-rubric-display.js";
import {flagSpotBox, flagSpotView, reviewMarkup} from "./grading-review-layout.js";

const FLAG_VIEW_REM = 9;  // Height of a spot's enlarged view beside the reading choices (GRADING "THE SPOT TO LOOK AT").
import {mathText, renderMath} from "./grading-math.js";
import {mountCropZoom} from "./grading-crops.js";

export const REVIEW_STATUSES = ["approved", "manual", "draft", "unreadable", "rejected"];
// The one response read of a desk. A read and its reread must ask the same reveal choice (GRADING "KEEP CONFLICTED EDITS").
export const responseReviewUrl = (id, reveal = false) => `/api/grading/responses/${encodeURIComponent(id)}?details=review${reveal ? "&reveal=1" : ""}`;
const UNSAVED = "Leave unsaved review changes?";
// What a saved decision did, in one toast (grading-review-1): a draft, an unreadable mark or a rejection is not final.
export const SAVED = {approved: "Grade approved.", manual: "Manual grade recorded.",
  draft: "Saved as a draft. It is not final: approve it, or regrade it under Ask for another opinion.",
  unreadable: "Marked unreadable. It still needs a grade from you.", rejected: "Proposal rejected. It still needs a grade from you."};
const CERTIFIED = "These grades are certified. Grade this response again? A new grade that passes every check replaces your decision; certify again afterwards.";
let mounted = null;
// Responses sent to be graded again from a desk in this page session, with the result they had then and the batch that grades them:
// until a new result lands the desk says so, since approving meanwhile keeps the decision only if the new grade is flagged
// (acceptance.REVIEW_RULE). Each desk drawn for the response reads that batch once: a batch that settled without a new grade (it
// failed or was cancelled) ends the line and says so. Browser memory only; a reload forgets it.
const regrading = new Map();
const REGRADING_FOR = 2 * 60 * 60 * 1000;  // without a batch to ask (none in the answer), the line goes after two hours
const regradingLine = at => `<p class="grading-warning" role="status" data-desk-regrading>Sent to be graded again at ${new Date(at).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"})}. Until the new grade lands, approving keeps your decision only if the new grade is flagged.</p>`;
const unregradedLine = (error, h) => `<p class="grading-warning" role="status" data-desk-regrading data-desk-regrade-failed>It could not be graded again${error ? `: ${h.e(error.replace(/[.\s]+$/, ""))}` : ""}. This grade is unchanged: regrade it again under Ask for another opinion, or decide it yourself.</p>`;

// Close the desk on screen, if any (its keys and leave guard go with it). Nothing is asked: callers ask through `leave()`.
export function closeReviewDesk() {
  mounted?.destroy();
  mounted = null;
}

const textField = node => Boolean(node && (node.isContentEditable || ["TEXTAREA", "SELECT"].includes(node.tagName)
  || (node.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset"].includes(node.type))));

// The adjustment the form holds, as save_review expects it.
function adjustments(values) {
  return Number(values.get("adjustment")) ? [{points: Number(values.get("adjustment")), reason: values.get("adjustment_reason")}] : [];
}

// GRADING "KEEP CONFLICTED EDITS": browser-only recovery of this desk's edits. A grade is never retried automatically.
export function reviewDraft(form) {
  const values = new FormData(form);
  return Object.fromEntries(["transcript", "comment", "adjustment", "adjustment_reason", "manual_score", "reasoning_wrong", "item"]
    .map(name => [name, name === "item" ? values.getAll(name).map(String).sort() : name === "reasoning_wrong" ? values.has(name) : String(values.get(name) ?? "")]));
}
export const reviewDraftChanges = (before, after) => Object.keys(after).filter(name => JSON.stringify(before[name]) !== JSON.stringify(after[name]));
export const sameReviewRubric = (before, after) => Boolean(before.question?.rubric && after.question?.rubric)
  && before.question.id === after.question.id && before.question.rubric.id === after.question.rubric.id
  && before.question.rubric_id === after.question.rubric_id
  && JSON.stringify(before.question.content) === JSON.stringify(after.question.content)
  && JSON.stringify(before.question.rubric.rules) === JSON.stringify(after.question.rubric.rules);

// The desk offers only the decisions its page allows: the markup is the Review step's, and what is not offered is removed.
function limitStatuses(work, statuses, zeroShortcut, h) {
  for (const status of ["manual", "draft", "unreadable", "rejected"]) {
    if (statuses.includes(status)) continue;
    const button = work.querySelector(`[data-grading-action="${status}"]`);
    // A manual grade's button sits in its own "Manual grade" fold with the score field: without the decision the fold goes too.
    if (status === "manual") button?.closest("details")?.remove();
    else button?.remove();
  }
  work.querySelectorAll(".grading-review-form details").forEach(fold => {
    const actions = fold.querySelector(":scope > .grading-actions");
    if (actions && !actions.querySelector("button")) fold.remove();
  });
  if (zeroShortcut && statuses.includes("manual")) {
    const actions = work.querySelector(".grading-review-decision .grading-actions");
    actions?.insertAdjacentHTML("beforeend", h.button("no-credit", "No credit (manual 0)"));
  }
}

/**
 * @param {HTMLElement} work the element the desk fills
 * @param {any} value GET /api/grading/responses/<rid>
 * @param {any} h grading helpers
 * @param {{index?: number, total?: number | null, groupSize?: number | null, heading?: string, statuses?: string[], zeroShortcut?: boolean,
 *   explain?: boolean, scroll?: boolean, onSaved?: (status: string) => Promise<void> | void, onMove?: ((step: number) => void) | null,
 *   onReveal?: (() => void) | null, reread?: (() => Promise<any>) | null, onConflict?: (() => Promise<void> | void) | null, active?: () => boolean,
 *   moveKeys?: string[][], student?: string, certified?: string, moved?: boolean, left?: number | null, approveNext?: boolean,
 *   onLater?: (() => void) | null, recovery?: any}} [options] `moveKeys`: without `onMove`, the caller's own
 *   next/previous keys for the keys line ([[keys, what]], e.g. [["Alt J", "next student"]]); the desk's Alt N / Alt P are not bound then
 *   and are not named. `student`: the anonymous Review page's route for who wrote this response; the desk offers Show student, which
 *   reads it only when clicked. `certified`: the plan's current certification date, when there is one; grading a response again then
 *   asks first. `moved`: this desk replaces the previous response's, so in a short window it scrolls to the new response's
 *   heading (its handwriting) rather than leaving the rubric on screen. `left`: in a group, how many responses of the question still wait,
 *   said beside "1 of N in this group" (the position alone never moves as approvals empty the group). After a refused save
 *   (GRADING "KEEP CONFLICTED EDITS"): `reread` reads this response again with the page's own reveal choice, and `onConflict` refreshes
 *   the page's counts around the desk.
 * @returns {{dirty(): boolean, leave(): boolean, destroy(): void}}
 */
export function mountReviewDesk(work, value, h, options = {}) {
  let {index = 0, total = null, groupSize = null, heading = "", statuses = REVIEW_STATUSES, zeroShortcut = false,
  explain = false, scroll = true, onSaved = async () => {}, onMove = null, onReveal = null, reread = null, onConflict = null, active = () => true, moveKeys = [],
  student = "", certified = "", moved = false, left = null, approveNext = false, onLater = null, recovery = null} = options;
  closeReviewDesk();
  let dirty = false, destroyed = false, replacement = null, conflict = false;
  const offs = [];
  const markClean = () => { dirty = false; work.dataset.unsaved = "false"; };
  const desk = {
    dirty: () => replacement ? replacement.dirty() : dirty,
    leave() { if (replacement) return replacement.leave(); if (work.querySelector('form[aria-busy="true"]')) return false; if (!dirty || window.confirm(UNSAVED)) { markClean(); return true; } return false; },
    destroy() {
      if (replacement) { replacement.destroy(); return; }
      if (destroyed) return;
      destroyed = true;
      offs.splice(0).forEach(off => off());
      if (mounted === desk) mounted = null;
    },
  };
  mounted = desk;
  // The batch grading this response again, read once: settled without a new grade (failed, cancelled), the line says so and ends.
  async function settledRegrade() {
    const entry = regrading.get(value.id);
    if (!entry?.job) return;
    let batch;
    try { batch = await h.api(`/api/grading/jobs/${encodeURIComponent(entry.job)}`); } catch { return; }  // unread: the line stays
    if (destroyed || !work.isConnected || regrading.get(value.id) !== entry || ["pending", "running"].includes(batch.status)) return;
    regrading.delete(value.id);
    const task = (batch.tasks || []).find(t => t.response_id === value.id);
    const failed = batch.status === "cancelled" || ["failed", "cancelled"].includes(task?.status);
    const line = work.querySelector("[data-desk-regrading]");
    if (line && failed) line.outerHTML = unregradedLine(task?.error || "", h);
    else line?.remove();  // finished: the desk on screen was read before its grade landed; the next read shows it
  }
  if (!value.result) {
    work.innerHTML = `<p class="empty-state">This response has no completed proposal. Check its batch in the Grade step.</p>`;
    markClean();
    return desk;
  }
  const chosen = value.review || value.result;
  const applications = chosen.applications || [];
  const reading = chosen.transcript;
  const lostFocus = !document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected || work.contains(document.activeElement);
  const note = explain && value.acceptance?.kind === "automatic" && !value.review
    ? `<p class="supporting-copy" data-desk-automatic>Saving replaces the automatic grade with your decision (recorded as confirmed or overturned).${value.acceptance.basis === "benchmark" ? " Overturning it suspends that permission’s other automatic grades." : ""}</p>` : "";
  const sent = regrading.get(value.id);
  if (sent && (sent.result !== value.current_result_id || (!sent.job && Date.now() - sent.at > REGRADING_FOR))) regrading.delete(value.id);  // the new grade landed (or, with no batch to ask, the line is old)
  work.innerHTML = `${heading ? `<p class="grading-desk-heading" data-desk-heading>${h.e(heading)}</p>` : ""}${note}${reviewMarkup(value, index, h, groupSize)}`;
  if (regrading.has(value.id)) {
    work.querySelector(".grading-response-heading")?.insertAdjacentHTML("afterend", regradingLine(regrading.get(value.id).at));
    void settledRegrade();
  }
  // Inside the desk, so it stays in sight when the desk fills the window (grading-review-10). Part of the group's position line, with
  // the separator held to the text before it, so a narrow window wraps it after the dot, never with a line that starts with one.
  if (left !== null) work.querySelector(".grading-response-title [data-review-progress]")?.insertAdjacentHTML("afterend",
    `<span data-desk-left>${h.e(`\u00a0· ${Number(left).toLocaleString()} response${left === 1 ? "" : "s"} left in this question`)}</span>`);
  markClean();
  limitStatuses(work, statuses, zeroShortcut, h);
  if (approveNext) {
    work.querySelector('button[type="submit"]').innerHTML = 'Approve &amp; next<kbd class="grading-key-hint" aria-hidden="true">Alt A</kbd>';
    // Keep the two frequent actions together; saving corrections stays with the notes they preserve.
    const draft = work.querySelector('[data-grading-action="draft"]');
    if (draft) work.querySelector('.grading-review-notes').append(draft);
  }
  if (onLater) work.querySelector('.grading-review-decision .grading-actions').insertAdjacentHTML('beforeend',
    '<button type="button" class="button" data-grading-action="later" aria-keyshortcuts="Alt+L">Later<kbd class="grading-key-hint" aria-hidden="true">Alt L</kbd></button>');
  if (!onMove) {
    work.querySelectorAll('[data-grading-action="previous"], [data-grading-action="next"]').forEach(button => button.remove());
    // Keys the desk does not bind are not named (review UX4); the caller's own, when it has some, are.
    const line = work.querySelector(".grading-review-keys");
    const own = moveKeys.map(([keys, what]) => ` · <kbd>${h.e(keys)}</kbd> ${h.e(what)}`).join("");
    if (line) line.innerHTML = line.innerHTML.replace(/ · <kbd>Alt N<\/kbd> next · <kbd>Alt P<\/kbd> previous/, own);
  }
  if (student) {
    // Show student (requirement, 2026-09-29): the Review step stays anonymous; one click names this response's student and section.
    // Nothing is kept: the next response starts anonymous again.
    // Under "1 of N in this group" in a group; beside the title (before Previous / Next) on All responses.
    const markup = `<p class="grading-review-student" data-review-student><button type="button" class="button" data-review-student-show>Show student</button></p>`;
    const group = work.querySelector(".grading-response-title");
    if (group) group.insertAdjacentHTML("beforeend", markup);
    else work.querySelector("[data-review-heading]")?.insertAdjacentHTML("afterend", markup);
    const show = /** @type {HTMLButtonElement | null} */ (work.querySelector("[data-review-student-show]"));
    if (show) show.onclick = () => h.run(show, async () => {
      const who = await h.api(student);
      if (destroyed) return;
      const slot = work.querySelector("[data-review-student]");
      if (slot) slot.innerHTML = `<span class="chip" data-tone="neutral">${h.e(who.name)} · Section ${h.e(who.section)}</span>`;
    });
  }
  renderMath(work);
  mountCropZoom(work);
  // Rubric lines by their displayed number, for the digit keys.
  const display = rubricDisplay(value.question?.rubric?.rules);
  work.querySelectorAll('.grading-rubric-choice input[name="item"]').forEach(input => { input.dataset.line = display.number(input.value); });
  if (scroll && window.matchMedia("(min-height: 650px)").matches) {
    work.querySelector(".grading-review-desk")?.scrollIntoView({block: "start"});
  }
  if (lostFocus) work.querySelector("[data-review-heading]")?.focus({preventScroll: true});
  // In a short window the rubric sits under the handwriting: after a move, start at the new response's heading so its work is seen
  // before its Approve button (grading-review-16).
  if (moved && !window.matchMedia("(min-height: 650px)").matches) work.querySelector("[data-review-heading]")?.scrollIntoView({block: "start"});
  const form = work.querySelector("form");
  const initialDraft = reviewDraft(form);
  form.addEventListener("input", () => { dirty = true; work.dataset.unsaved = "true"; });
  const editor = work.querySelector('[name="transcript"]');
  editor.addEventListener("input", () => {
    dirty = true; work.dataset.unsaved = "true";
    const preview = work.querySelector("[data-transcript-preview]");
    preview.innerHTML = mathText(editor.value, h);
    renderMath(preview);
  });
  const previous = work.querySelector('[data-grading-action="previous"]'), next = work.querySelector('[data-grading-action="next"]');
  if (previous) previous.disabled = index === 0;
  if (next) next.disabled = total === null || index >= total - 1;
  let calculation = 0;
  const calculate = async () => {
    const token = ++calculation, values = new FormData(form);
    try {
      const data = await h.api(`/api/grading/responses/${value.id}/score`, "POST", {version: value.version, selected_items: values.getAll("item"), adjustments: adjustments(values)});
      if (form.isConnected && token === calculation) form.querySelectorAll("[data-score], [data-decision-score]").forEach(node => { node.textContent = `${data.score} / ${data.max_score}`; });
    } catch (error) { if (form.isConnected && token === calculation) form.querySelectorAll("[data-score], [data-decision-score]").forEach(node => { node.textContent = error.message; }); }
  };
  form.querySelectorAll('[name="item"], [name="adjustment"], [name="adjustment_reason"]').forEach(node => node.addEventListener("change", () => { void calculate(); }));
  const draftNames = {transcript: "Transcription", comment: "Comment", adjustment: "Adjustment points", adjustment_reason: "Adjustment reason",
    manual_score: "Manual score", reasoning_wrong: "Model reasoning", item: "Rubric selections"};
  function recoveryPanel(draft, changes, compatible, message, ready) {
    work.querySelector('[data-review-recovery]')?.remove();
    const items = value.question.rubric.rules.items || [];
    const shown = name => name === "item" ? (draft.item.map(id => items.find(item => item.id === id)?.description || id).join("\n") || "No rubric lines selected")
      : name === "reasoning_wrong" ? (draft[name] ? "Marked wrong" : "Not marked wrong") : draft[name];
    work.querySelector('.grading-response-heading').insertAdjacentHTML('afterend', `<aside class="callout grading-review-recovery" data-tone="warning" data-review-recovery role="alert">
      <strong class="callout-title">Your edits are kept</strong><p>${h.e(message)}</p>
      ${changes.length ? `<details class="disclosure"><summary>Your unsaved edits · ${changes.length}</summary>${changes.map(name => `<strong>${h.e(draftNames[name])}</strong><pre class="grading-source">${h.e(shown(name))}</pre>`).join('')}</details>` : ''}
      <div class="grading-actions">${ready ? `<button type="button" class="button primary" data-recovery-apply>${compatible ? 'Apply my edits to this grade' : 'Apply my text edits'}</button><button type="button" class="text-button" data-recovery-discard>Discard my edits</button>`
        : '<button type="button" class="button primary" data-recovery-read>Review updated grade</button>'}</div><p class="form-error" data-recovery-error hidden></p></aside>`);
    const panel = work.querySelector('[data-review-recovery]');
    panel.querySelector('[data-recovery-read]')?.addEventListener('click', event => { void h.run(event.currentTarget, async () => {
      try {
        // The page's own read (`reread`), with its own reveal choice: a new blind proposal stays hidden unless the instructor had
        // revealed this response (GRADING "KEEP CONFLICTED EDITS"). Without one, never reveal.
        const fresh = reread ? await reread() : await h.api(responseReviewUrl(value.id));
        if (destroyed || !active()) return;
        if (!fresh.result) throw new Error('The new grade is not ready yet. Your edits are still kept here; try again when grading finishes.');
        const kept = reviewDraft(form);
        replacement = mountReviewDesk(work, fresh, h, {...options, scroll: false, moved: false,
          recovery: {draft: kept, changes: reviewDraftChanges(initialDraft, kept), compatible: sameReviewRubric(value, fresh)}});
        // The page's counts and queue, read again around the kept desk (never a remount, which would drop the kept edits).
        if (onConflict) void Promise.resolve(onConflict()).catch(cause => h.toast(cause.message, true));
      } catch (cause) {
        if (!destroyed && active()) { const error = panel.querySelector('[data-recovery-error]'); error.textContent = cause.message; error.hidden = false; }
      }
    }); });
    if (!ready) return;
    const controls = [...form.elements].filter(node => 'disabled' in node), disabled = controls.map(node => node.disabled);
    controls.forEach(node => { node.disabled = true; });
    const resolve = apply => {
      controls.forEach((node, i) => { node.disabled = disabled[i]; });
      recovery = null; conflict = false; panel.remove();
      if (apply) for (const name of changes) {
        if (!compatible && !['transcript', 'comment'].includes(name)) continue;
        if (name === 'item') form.querySelectorAll('[name="item"]').forEach(node => { node.checked = draft.item.includes(node.value); });
        else { const node = form.elements.namedItem(name); if (node) { if (name === 'reasoning_wrong') node.checked = draft[name]; else node.value = draft[name]; } }
      }
      dirty = Boolean(apply && changes.some(name => compatible || ['transcript', 'comment'].includes(name)));
      work.dataset.unsaved = String(dirty);
      if (dirty) { editor.dispatchEvent(new Event('input', {bubbles: true})); void calculate(); }
      work.querySelector('[data-review-heading]')?.focus({preventScroll: true});
    };
    panel.querySelector('[data-recovery-apply]').onclick = () => resolve(true);
    panel.querySelector('[data-recovery-discard]').onclick = () => resolve(false);
  }
  if (recovery) {
    dirty = true; conflict = true; work.dataset.unsaved = 'true';
    recoveryPanel(recovery.draft, recovery.changes, recovery.compatible,
      recovery.compatible ? 'The updated grade is shown below. Compare it with your unsaved edits, then apply or discard them. Nothing has been saved.'
        : 'The question or rubric changed. Compare the updated grade below with your edits. Only transcription and comment edits can be restored; select rubric lines and scores again.', true);
  }
  async function save(status) {
    if (conflict) throw new Error('Review the updated grade and resolve your kept edits before saving.');
    if (!statuses.includes(status)) throw new Error("This decision is not offered here.");
    const values = new FormData(form), plain = values.get("transcript");
    const changed = plain !== reading.plain_text;
    const corrected = changed ? {plain_text: plain, latex: plain, confidence: null, ambiguities: [], lines: plain.split("\n").map((line, i) => ({id: `line-${i+1}`, elements: [{type: "math", text: line, latex: line, bbox: null}]}))} : reading;
    try {
      await h.api(`/api/grading/responses/${value.id}/review`, "POST", {version: value.version, result_id: value.current_result_id,
        status, transcript: corrected, selected_items: values.getAll("item"), adjustments: adjustments(values), comment: values.get("comment"), manual_score: Number(values.get("manual_score")), confirm_review: status === "approved" || status === "manual",
        reasoning_wrong: values.has("reasoning_wrong")});
    } catch (error) {
      if (error?.status === 409 && active()) {
        conflict = true; dirty = true; work.dataset.unsaved = 'true';
        const draft = reviewDraft(form);
        recoveryPanel(draft, reviewDraftChanges(initialDraft, draft), false,
          'This response changed before your save was accepted. Your edits have not been saved or discarded. Read the updated grade to compare them.', false);
        return;
      }
      throw error;
    }
    if (!active()) return;
    markClean();
    h.toast(SAVED[status] || "Review decision saved.");
    await onSaved(status);
  }
  // No credit: every rubric line unticked and a manual 0, which save_review records with the reason written in the comment.
  async function noCredit() {
    const comment = form.querySelector('[name="comment"]');
    form.querySelectorAll('input[name="item"]').forEach(input => { input.checked = false; });
    const manual = form.querySelector('[name="manual_score"]');
    if (manual) manual.value = "0";
    if (!String(comment?.value || "").trim()) {
      const notes = comment?.closest("details");
      if (notes) notes.open = true;
      comment?.focus();
      throw new Error("Write why this answer earns no credit in the comment, then choose No credit again.");
    }
    await save("manual");
  }
  form.onsubmit = event => { event.preventDefault(); void runFormAction(form, () => save("approved")); };
  // A reading the flag review named (grading-review-layout.js flagReadingsMarkup): exactly its lines, then the ordinary approval. Where
  // this desk cannot approve (Certification and Export's own statuses), only the note to look at stays.
  if (!statuses.includes("approved")) work.querySelectorAll("[data-flag-readings] .grading-actions, [data-flag-readings] .supporting-copy").forEach(node => node.remove());
  work.querySelectorAll("[data-flag-reading]").forEach(button => button.onclick = () => {
    const reading = value.flag_review?.readings?.[Number(button.dataset.flagReading)];
    if (!reading || !statuses.includes("approved")) return;
    form.querySelectorAll('input[name="item"]').forEach(input => { input.checked = reading.lines.includes(input.value); });
    dirty = true; work.dataset.unsaved = "true";
    // The score preview first, then the approval: sent together, the approval could answer first and change the response's
    // version, so the preview's version check failed (a 409 in the console; 4 of 10 runs of the review-queue check, 2026-10-03).
    void runFormAction(form, async () => { await calculate(); await save("approved"); });
  });
  work.querySelectorAll("[data-grading-action]").forEach(button => button.onclick = () => {
    const action = button.dataset.gradingAction;
    if (["next", "previous"].includes(action)) { if (onMove && desk.leave()) onMove(action === "next" ? 1 : -1); }
    else if (action === "later") { if (onLater && desk.leave()) onLater(); }
    else if (action === "reveal") { if (onReveal && desk.leave()) onReveal(); }
    else if (action.startsWith("regrade-")) {
      // One response, sent again: Cloud on the retained reading, or the whole reading once more. A certified grade asks first.
      if (desk.leave() && (!certified || window.confirm(CERTIFIED))) void h.run(button, async () => {
        const batch = await h.api(`/api/grading/responses/${value.id}/regrade`, "POST", {request_id: crypto.randomUUID(), mode: "cloud_standard", operation: action === "regrade-standard" ? "grade" : "recognize"});
        h.toast("Queued. If the new grade passes every check it replaces your decision; otherwise it waits here beside it.");
        regrading.set(value.id, {result: value.current_result_id, at: Date.now(), job: batch?.id || ""});
        if (!destroyed && work.isConnected) {
          work.querySelector("[data-desk-regrading]")?.remove();
          work.querySelector(".grading-response-heading")?.insertAdjacentHTML("afterend", regradingLine(Date.now()));
        }
      });
    }
    else if (action === "no-credit") void runFormAction(form, noCredit);
    else void runFormAction(form, () => save(action));
  });
  // GRADING FLAG REVIEW "THE SPOT TO LOOK AT": each outline is placed on its crop, and its name in the note scrolls to it.
  work.querySelectorAll("[data-flag-spot]").forEach(node => {
    const box = flagSpotBox(value.flag_review.look[Number(node.dataset.flagSpot)].bbox);
    Object.assign(node.style, {left: `${100*box.left}%`, top: `${100*box.top}%`, width: `${100*box.width}%`, height: `${100*box.height}%`});
  });
  // Its enlarged view beside the choices: the scan picture scaled and moved so the view shows the spot and what surrounds it, as tall
  // as FLAG_VIEW_REM where its width allows, with the same outline on the marks. Placed once the picture's size is known.
  work.querySelectorAll("[data-flag-view]").forEach(button => {
    const index = Number(button.dataset.flagView), spot = value.flag_review.look[index];
    const img = button.querySelector("img"), mark = button.querySelector(".grading-flag-view-mark");
    const place = () => {
      if (!img.naturalWidth || !img.naturalHeight) return;
      const view = flagSpotView(spot.bbox), box = flagSpotBox(spot.bbox);
      const ratio = (view.width * img.naturalWidth) / (view.height * img.naturalHeight);
      button.closest("figure").style.width = `${FLAG_VIEW_REM * ratio}rem`;
      button.style.aspectRatio = String(ratio);
      Object.assign(img.style, {width: `${100 / view.width}%`, height: `${100 / view.height}%`, left: `${-100 * view.left / view.width}%`, top: `${-100 * view.top / view.height}%`});
      Object.assign(mark.style, {left: `${100 * (box.left - view.left) / view.width}%`, top: `${100 * (box.top - view.top) / view.height}%`,
        width: `${100 * box.width / view.width}%`, height: `${100 * box.height / view.height}%`});
      button.dataset.placed = "true";
    };
    if (img.complete) place(); else img.addEventListener("load", place, {once: true});
    button.onclick = () => work.querySelector(`[data-flag-spot="${index}"]`)?.scrollIntoView({block: "center", inline: "center", behavior: "smooth"});
  });
  work.querySelectorAll("[data-evidence-item]").forEach(button => button.onclick = () => {
    const evidence = applications.find(a => a.rubric_item_id === button.dataset.evidenceItem)?.evidence[Number(button.dataset.evidenceIndex)];
    work.querySelectorAll(".grading-highlight").forEach(node => { node.hidden = true; });
    if (!evidence?.bbox) return;
    const image = work.querySelector(`[data-crop="${Number(evidence.crop || 0)}"]`), overlay = image?.querySelector(".grading-highlight");
    if (!overlay) return;
    const [x, y, right, bottom] = evidence.bbox;
    Object.assign(overlay.style, {left: `${100*x}%`, top: `${100*y}%`, width: `${100*(right-x)}%`, height: `${100*(bottom-y)}%`});
    overlay.hidden = false; image.scrollIntoView({block: "nearest", behavior: "smooth"});
  });
  work.querySelectorAll("[data-review-line]").forEach(button => { button.onclick = () => {
    const input = [...work.querySelectorAll('.grading-rubric-choice input[name="item"]')].find(node => node.value === button.dataset.reviewLine);
    const row = input?.closest(".grading-rubric-choice"), details = row?.querySelector("details");
    if (details) details.open = true;
    row?.scrollIntoView({block: "nearest"});
    input?.focus({preventScroll: true});
  }; });
  const original = work.querySelector("[data-review-original]");
  if (original) original.onclick = () => {
    const pane = work.querySelector(".grading-evidence");
    pane.scrollTop = 0;
    pane.scrollIntoView({block: "nearest"});
    pane.focus({preventScroll: true});
  };

  // Keyboard grading (grun:P1, P7; amendment 1): Alt+A approve, Alt+N next, Alt+P previous, and a digit ticks that rubric line
  // (0 then a digit: lines 10-19). All through ui.js's one registry, only while this desk is on screen, never while a dialog is
  // open, and digits never while the instructor types in a text field.
  const deskOn = () => !destroyed && active() && work.isConnected && Boolean(work.querySelector(".grading-review-form"));
  const bind = (key, label, run, inText = false, group = undefined) => offs.push(registerShortcut({key, label, run, allowInInput: true, group,
    when: () => deskOn() && (inText || !textField(document.activeElement))}));
  bind("Alt+A", approveNext ? "Approve this grade and continue" : "Approve this grade", () => work.querySelector(".grading-review-form")?.requestSubmit());
  if (onLater) bind("Alt+L", "Leave this response for later", () => work.querySelector('[data-grading-action="later"]')?.click());
  if (onMove) {
    bind("Alt+N", "Next response", () => work.querySelector('[data-grading-action="next"]:not(:disabled)')?.click());
    bind("Alt+P", "Previous response", () => work.querySelector('[data-grading-action="previous"]:not(:disabled)')?.click());
  }
  // "0" arms lines 10-19 for the next digit; a line number the rubric does not have does nothing.
  let tens = 0, armed = 0;
  const tick = digit => {
    const line = Date.now() - armed < 1500 ? tens + digit : digit;
    armed = 0;
    if (line === 0) { tens = 10; armed = Date.now(); return; }
    work.querySelector(`.grading-rubric-choice input[name="item"][data-line="${line}"]`)?.click();
  };
  // The '?' sheet lists the nine digits as one row.
  const digits = {key: "1–9", label: "Tick or untick that rubric line"};
  for (let digit = 1; digit <= 9; digit++) bind(String(digit), `Tick or untick rubric line ${digit}`, () => tick(digit), false, digits);
  bind("0", "Then a digit: tick rubric lines 10–19", () => tick(0));
  // Leaving the page (any link, the graph, the sidebar, Back) with an unsaved decision asks through the shell's one leave guard.
  const offLeave = registerLeaveGuard(() => {
    if (!work.isConnected) { desk.destroy(); return null; }
    return dirty ? UNSAVED : null;
  });
  offs.push(offLeave);
  return desk;
}
