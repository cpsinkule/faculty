// Regrade requests on the Return stage (requirement, 2026-10-07). The list names each student (Return is not anonymous); one request opens at a time,
// in place: the student's words, their work with the rubric lines applied and missed, where the grade is (Faculty, Gradescope, the
// gradebook, Canvas), the reply a model drafted (the instructor edits it; a draft never replaces typed text), and Apply. Change grade
// mounts the one review desk (grading-review-desk.js, approved and manual only), saved through the ONLY final-change path. Apply is
// previewed first (Faculty reads Gradescope and Canvas, writes nothing), then runs its steps in order while the panel is open: that
// grade on Gradescope, the reply once Gradescope shows it, the gradebook through a certification of just this change, the student's
// Canvas cell. A closed panel leaves the run waiting with Continue; nothing runs by itself. A request on a bubble-sheet question
// (REGRADES "BUBBLE SHEETS", 2026-10-28) shows the question's row of the student's scan with what Faculty read and the key.
import {calloutMarkup, disclosureMarkup, registerLeaveGuard, seenByMarkup} from "./ui.js";
import {mountReviewDesk, closeReviewDesk, responseReviewUrl} from "./grading-review-desk.js";
import {renderMath} from "./grading-math.js";
import {mountCropZoom} from "./grading-crops.js";
import {responseMarkup} from "./grading-certification-students.js";

const base = aid => `/api/grading/assignments/${encodeURIComponent(aid)}/regrade-requests`;
const STATE = {open: ["warning", "Waiting for your answer"], answered: ["success", "Answered"], handled: ["neutral", "Handled"]};
const REPLY = {accepted: ["success", "Posted to Gradescope"], failed: ["danger", "Not posted"], uncertain: ["warning", "Not confirmed yet"]};
const STEP = {pending: ["neutral", "Next"], waiting: ["neutral", "Working"], done: ["success", "Done"], already: ["success", "Already there"],
  stopped: ["danger", "Stopped"], cancelled: ["neutral", "Cancelled"]};
const POLL = 3000;
const when = value => new Date(value).toLocaleString(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
const chip = ([tone, label], h) => `<span class="chip" data-tone="${tone}">${h.e(label)}</span>`;

/** "Question 20 · Version B". @param {any} item */
export function requestTitle(item) {
  return [item.question || item.question_title || "A question", item.exam_version ? `Version ${item.exam_version}` : ""].filter(Boolean).join(" · ");
}

/** Only Gradescope's own https pages are linked. @param {string} url */
export const gradescopeHref = url => (/^https:\/\/[^/]+\/courses\/\d+\/questions\/\d+\/submissions\/\d+\/grade$/.test(String(url || "")) ? url : "");

/** "Example, Alice · 508", or that Faculty could not place the request. @param {any} item */
export const who = item => item.student ? [item.student.name, item.student.section ? String(item.student.section) : ""].filter(Boolean).join(" · ") : "Student not linked";

/**
 * Where the grade is, compared like with like: this question (Faculty and Gradescope), the exam total (the gradebook and Canvas).
 * Returns {parts: [[label, value]], differs: [labels]}. @param {any} where
 */
export function whereSummary(where) {
  if (!where) return {parts: [], differs: []};
  const faculty = where.faculty ? `${where.faculty.points} / ${where.faculty.of}` : "no final grade";
  const gradescope = where.gradescope?.points ?? (where.gradescope?.state === "not_sent" ? "not sent" : "–");
  const parts = [["Faculty", faculty], ["Gradescope", gradescope]];
  const differs = [];
  // A bubble-sheet question: Gradescope is known only where Faculty corrected it, so it differs only when it shows another score.
  const mc = where.kind === "mc";
  if (mc ? where.faculty && where.gradescope?.points != null && where.gradescope.points !== where.faculty.points
    : where.faculty && where.gradescope?.state !== "sent") differs.push("Gradescope");
  if (where.gradebook) {
    parts.push(["Gradebook", where.gradebook.value ?? "–"]);
    if (where.certified?.earned != null && where.gradebook.source === "grading" && where.gradebook.value !== where.certified.earned) differs.push("Gradebook");
  }
  if (where.canvas) {
    parts.push(["Canvas", where.canvas.value ?? "–"]);
    if (where.gradebook && where.canvas.value !== where.gradebook.value) differs.push("Canvas");
  }
  return {parts, differs};
}

/** "B", "B and D" or "a blank". @param {unknown} answer */
export const letters = answer => String(answer || "").split("").join(" and ") || "a blank";

/**
 * A bubble-sheet question (REGRADES "BUBBLE SHEETS"): the question's row of the scan, what Faculty read, the key and the score.
 * @param {any} sheet @param {any} h
 */
export function sheetMarkup(sheet, h) {
  if (!sheet) return "";
  if (sheet.earned == null) return calloutMarkup({tone: "warning", title: "No score for this question", body: sheet.reason || "Faculty does not score this sheet yet."});
  return `<section class="regrade-work regrade-sheet" data-regrade-sheet aria-label="Their bubble sheet">
    ${sheet.picture ? `<img class="regrade-sheet-row" src="${h.e(sheet.picture)}" alt="Question ${h.e(sheet.number)} on the student's bubble sheet">` : ""}
    <p class="regrade-sheet-facts">Question ${h.e(sheet.number)}${sheet.version ? ` · Version ${h.e(sheet.version)}` : ""} · Faculty read <strong>${h.e(letters(sheet.marked))}</strong>
      · your key <strong>${h.e(sheet.answer || "–")}</strong> (${h.e(sheet.scoring)}) · <strong>${h.e(sheet.earned)} / ${h.e(sheet.points)}</strong>
      · sheet ${h.e(sheet.sheet_earned)} / ${h.e(sheet.sheet_possible)}</p></section>`;
}

/** One step of Apply in plain words. @param {any} step */
export function stepWords(step) {
  const change = step.from != null || step.to != null ? `${step.from ?? "–"} → ${step.to ?? "the certified value"}` : "";
  if (step.step === "gradescope") return `This grade on Gradescope${change ? `: ${change}` : ""}${step.published?.length ? " · published: the student sees it at once" : ""}`;
  if (step.step === "reply") return "Your reply, once Gradescope shows the grade · it closes the request";
  if (step.step === "gradebook") return step.problem ? "The gradebook waits for Certify again" : `Gradebook, certifying just this change${change ? `: ${change}` : ""}${step.typed && !step.replace ? " · your typed value stays" : ""}`;
  if (step.step === "canvas") return `Canvas${step.course ? ` (${step.course})` : ""}${change ? `: ${change}` : ""}`;
  return step.title || step.step;
}

/** @param {any} item @param {string} selected @param {any} h */
function rowMarkup(item, selected, h) {
  const score = item.faculty ? `${item.faculty.points} / ${item.faculty.of}` : "";
  return `<li data-patch-key="${h.e(item.id)}"><button type="button" class="regrade-row" data-regrade-open="${h.e(item.id)}"${item.id === selected ? ' aria-current="true"' : ""}>
    <span class="regrade-row-who">${h.e(who(item))}</span><span class="regrade-row-what">${h.e(requestTitle(item))}</span>
    <span class="regrade-row-when">${h.e(when(item.created_at))}</span>${chip(STATE[item.state] || ["neutral", item.state], h)}<span class="regrade-row-score">${h.e(score)}</span></button></li>`;
}

/** The list: open requests first, answered and handled ones folded. @param {any[]} items @param {string} selected @param {any} h */
export function listMarkup(items, selected, h) {
  if (!items.length) return `<p class="supporting-copy" data-regrades-empty>No regrade requests for this assignment.</p>`;
  const open = items.filter(item => item.state === "open"), done = items.filter(item => item.state !== "open");
  return `${open.length ? `<ul class="regrade-list" data-regrade-list>${open.map(item => rowMarkup(item, selected, h)).join("")}</ul>` : '<p class="supporting-copy">Nothing is waiting for your answer.</p>'}
    ${done.length ? disclosureMarkup({label: `Answered or handled · ${done.length}`, body: `<ul class="regrade-list">${done.map(item => rowMarkup(item, selected, h)).join("")}</ul>`,
    open: done.some(item => item.id === selected), className: "regrade-done"}) : ""}`;
}

/**
 * Follow-up's suggestion for the open request (REGRADES "FOLLOW-UP": one suggestion, shown on Return too): what it would do, why,
 * Approve the suggestion and where it is on Follow-up; or that Follow-up is preparing one. "" when there is none.
 * @param {any} suggestion @param {any} h
 */
export function suggestionMarkup(suggestion, h) {
  if (!suggestion) return "";
  if (suggestion.state === "preparing") return `<p class="regrade-suggestion supporting-copy" data-regrade-suggestion role="status">Follow-up is preparing a suggestion from their work and the rubric…</p>`;
  const action = suggestion.action;
  if (!action) return "";
  const grade = (/** @type {any} */ value) => value != null && action.of != null ? `${value} / ${action.of}` : String(value ?? "");
  const head = action.decision === "change" && action.answer != null ? `Read ${letters(action.answer)} for question ${action.number ?? ""}: ${grade(action.to)}`
    : action.decision === "change" ? `Change the grade to ${grade(action.to)}` : `Keep the grade${action.from != null ? ` (${grade(action.from)})` : ""}`;
  const gradeStep = (action.steps || []).find((/** @type {any} */ s) => s.step === "grade") || {};
  const lines = [...(gradeStep.added || []).map((/** @type {string} */ l) => `+${l}`), ...(gradeStep.removed || []).map((/** @type {string} */ l) => `−${l}`)];
  return `<section class="regrade-suggestion" data-regrade-suggestion aria-label="Follow-up's suggestion">
    <h4>Follow-up suggests: ${h.e(head)}</h4>
    ${action.reason ? `<p class="supporting-copy">${h.e(action.reason)}</p>` : ""}
    ${lines.length ? `<p class="regrade-suggestion-lines">Rubric lines ${h.e(lines.join(", "))}${action.adjustment ? ` · adjustment ${h.e(action.adjustment.points)} (${h.e(action.adjustment.reason)})` : ""}</p>` : ""}
    <div class="grading-actions"><button type="button" class="button primary" data-outward data-regrade-approve>Approve the suggestion</button><a class="text-button" href="${h.e(suggestion.href || "#follow-ups?filter=regrades")}">Open on Follow-up</a></div></section>`;
}

/**
 * Drives an Apply run while this page is open (REGRADES: nothing runs by itself): `continue` every POLL while a step waits on its
 * own runner, until it finishes or stops. Returns the last view. `onStep(view)` sees each one.
 * @param {(path: string, method?: string, body?: any, headers?: any, timeout?: number) => Promise<any>} api @param {string} aid
 * @param {any} run @param {(view: any) => void} [onStep]
 */
export async function driveApply(api, aid, run, onStep = () => {}) {
  let view = run;
  const deadline = Date.now() + 15 * 60 * 1000;
  while (view && !view.finished && view.waiting && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, POLL));
    view = await api(`${base(aid)}/applies/${encodeURIComponent(view.id)}/continue`, "POST", {}, {}, 120000);
    onStep(view);
  }
  return view;
}

/** The Apply run as a checklist. @param {any} run @param {any} h */
export function runMarkup(run, h) {
  if (!run) return "";
  const rows = run.steps.map(step => `<li data-step="${h.e(step.step)}" data-status="${h.e(step.status)}">${chip(STEP[step.status] || ["neutral", step.status], h)}
    <span>${h.e(stepWords(step))}</span>${step.detail && step.status !== "done" ? `<small class="regrade-step-detail">${h.e(step.detail)}</small>` : ""}
    ${step.status === "stopped" && step.href ? `<a class="text-button" href="${h.e(step.href)}">${h.e(step.action || "Open")}</a>` : ""}
    ${step.status === "stopped" && step.check ? `<button type="button" class="button" data-regrade-check="${h.e(step.check)}">Check on Gradescope</button>` : ""}
    ${step.status === "stopped" && !step.href && !step.check ? `<button type="button" class="button" data-regrade-retry="${h.e(step.step)}">Try again</button>` : ""}</li>`).join("");
  const status = run.finished ? "Finished" : run.waiting ? "Working…" : run.stopped ? "Stopped" : "Waiting to continue";
  return `<section class="regrade-run" data-regrade-run-view aria-live="polite"><h4>Apply · ${h.e(status)}</h4><ol class="regrade-steps">${rows}</ol>
    ${!run.finished ? `<div class="grading-actions">${!run.waiting && !run.stopped ? '<button type="button" class="button primary" data-regrade-continue>Continue</button>' : ""}<button type="button" class="text-button" data-regrade-cancel>Cancel the rest</button></div>` : ""}</section>`;
}

/**
 * The Return stage's Regrade requests: the list into `host`, and the request named by `request` (the address's `request=`) open in
 * place. `go(id)` changes the address (the page then calls `route`). Returns {start, refresh, route, destroy}.
 * @param {HTMLElement} host @param {string} aid @param {any} h @param {{request?: string, go: (id: string) => void, onChange?: () => void}} options
 */
export function mountRegradePanel(host, aid, h, {request = "", go, onChange = () => {}}) {
  let items = /** @type {any[]} */ ([]), selected = request, detail = /** @type {any} */ (null), value = /** @type {any} */ (null);
  let desk = /** @type {any} */ (null), typed = false, preview = /** @type {any} */ (null), run = /** @type {any} */ (null), timer = 0, destroyed = false;
  let applying = false, generation = 0;
  host.innerHTML = `<div class="regrade-page"><div data-regrade-list-host></div><div data-regrade-panel></div></div>`;
  const listHost = /** @type {HTMLElement} */ (host.querySelector("[data-regrade-list-host]"));
  const panel = /** @type {HTMLElement} */ (host.querySelector("[data-regrade-panel]"));
  const alive = () => !destroyed && host.isConnected;
  const showing = current => alive() && detail === current && selected === current?.id;
  const reply = () => /** @type {HTMLTextAreaElement | null} */ (panel.querySelector("[data-regrade-reply-text]"));
  const unguard = registerLeaveGuard(() => !host.closest('[hidden]') &&
    ((typed && reply()?.value.trim() && !(run && run.finished)) || desk?.dirty()) ? "Your reply or grade change is not saved." : null);

  async function loadList() {
    const data = await h.api(`${base(aid)}/panel`);
    if (!alive()) return;
    items = data.requests || [];
    if (!selected) selected = (items.find(item => item.state === "open") || {}).id || "";
    listHost.innerHTML = listMarkup(items, selected, h);
    listHost.querySelectorAll("[data-regrade-open]").forEach(button => { /** @type {HTMLElement} */ (button).onclick = () => {
      const id = button.getAttribute("data-regrade-open") || "";
      if (id === selected || (desk && !desk.leave())) return;
      go(id);
    }; });
  }

  async function open(id) {
    const token = ++generation;
    stop();
    closeReviewDesk(); desk = null; typed = false; preview = null; run = null;
    detail = null; value = null;
    selected = id;
    if (!id) { panel.innerHTML = ""; return; }
    panel.innerHTML = '<p class="supporting-copy" role="status">Opening the request…</p>';
    const found = await h.api(`${base(aid)}/${encodeURIComponent(id)}`);
    if (!alive() || token !== generation) return;
    // `details=review`, here and on the desk's own reads below: its reasons gathered by cause (GRADING, 2026-10-11).
    const response = found.response_id ? await h.api(responseReviewUrl(found.response_id)) : null;
    if (!alive() || token !== generation) return;
    detail = found; value = response; run = detail.apply;
    draw();
    // ONE SUGGESTION (REGRADES "FOLLOW-UP"): Follow-up's, when it has one or is preparing one; the separate draft otherwise.
    const coming = detail.suggestion && (detail.suggestion.action || detail.suggestion.state === "preparing");
    if (detail.state === "open" && detail.matched && !coming && detail.kind !== "mc") void askDraft();  // a bubble sheet has no drafted reply
    if (detail.suggestion?.state === "preparing") schedule();
    if (run && !run.finished && run.waiting) schedule();
  }

  function draw() {
    const savedReply = typed ? reply()?.value ?? null : null;
    const d = detail, outside = gradescopeHref(d.url);
    const {parts, differs} = whereSummary(d.where);
    const replies = [
      d.reply ? `<li>${chip(["neutral", "Reply on Gradescope"], h)} <span class="supporting-copy">${h.e([d.reply.by, d.reply.at ? when(d.reply.at) : ""].filter(Boolean).join(" · "))}</span><blockquote class="grading-regrade-text">${h.e(d.reply.text)}</blockquote></li>` : "",
      ...(d.replies || []).map(r => `<li>${chip(REPLY[r.status] || ["neutral", r.status], h)} <span class="supporting-copy">${h.e(when(r.at))}${r.detail ? ` · ${h.e(r.detail)}` : ""}</span><blockquote class="grading-regrade-text">${h.e(r.text)}</blockquote>${r.status === "uncertain" ? `<button type="button" class="button" data-regrade-check="${h.e(r.id)}">Check on Gradescope</button>` : ""}</li>`),
    ].filter(Boolean).join("");
    const mark = d.state === "open" ? '<button type="button" class="text-button" data-regrade-mark="handled">Mark handled</button>'
      : d.state === "handled" ? '<button type="button" class="text-button" data-regrade-mark="reopened">Reopen</button>' : "";
    panel.innerHTML = `<article class="regrade-panel" data-regrade-item="${h.e(d.id)}" aria-labelledby="regrade-panel-title">
      <header class="regrade-panel-head"><div><h3 id="regrade-panel-title">${h.e(d.student ? `${d.student.name} · Section ${d.student.section}` : "Student not linked")}</h3>
        <p class="supporting-copy">${h.e(requestTitle({question_title: d.question_title, exam_version: d.exam_version}))} · ${h.e(when(d.created_at))} ${chip(STATE[d.state] || ["neutral", d.state], h)}</p></div>
        <div class="grading-actions">${outside ? `<a class="text-button" href="${h.e(outside)}" target="_blank" rel="noopener noreferrer">Open on Gradescope</a>` : ""}${mark}</div></header>
      <blockquote class="grading-regrade-text grading-regrade-request">${h.e(d.text)}</blockquote>
      ${replies ? `<ul class="grading-regrade-replies">${replies}</ul>` : ""}
      ${!d.matched ? calloutMarkup({tone: "warning", title: d.kind === "mc" ? "Not linked to a bubble sheet" : "Not linked to a response", body: `${d.reason || "Faculty's records do not say which one."} Answer it on Gradescope.`}) : ""}
      ${parts.length ? `<p class="regrade-where" data-regrade-where>${parts.map(([label, text]) => `<span${differs.includes(label) ? " data-differs" : ""}><small>${h.e(label)}</small> <strong>${h.e(text)}</strong></span>`).join("")}<span class="regrade-where-note">${h.e(differs.length ? `${differs.join(" and ")} ${differs.length === 1 ? "differs" : "differ"}` : "All agree")}</span></p>` : ""}
      ${value ? `<section class="regrade-work" data-regrade-work aria-label="Their work and the grade">${responseMarkup(value, null, h, {change: d.state === "open"})}</section>` : ""}
      ${d.kind === "mc" && d.matched ? sheetMarkup(d.sheet, h) : ""}
      ${d.state === "open" && d.can_reply && !(run && !run.finished) ? suggestionMarkup(d.suggestion, h) : ""}
      ${d.state === "open" && d.can_reply ? `<section class="regrade-reply" aria-label="Your reply">
        <label class="field">Your reply${seenByMarkup("student")}<textarea data-regrade-reply-text rows="6" maxlength="5000"></textarea></label>
        <p class="regrade-draft-note supporting-copy" data-regrade-draft-note aria-live="polite"></p></section>
        <section class="regrade-apply" data-regrade-apply></section>` : ""}
      <div data-regrade-run>${runMarkup(run, h)}</div></article>`;
    renderMath(panel);
    mountCropZoom(panel);
    const text = reply();
    if (text && savedReply !== null) text.value = savedReply;
    if (text) text.addEventListener("input", () => { typed = true; preview = null; drawApply(); drawDraft(); });
    drawDraft();
    drawApply();
    bind();
  }

  function bind() {
    const approve = panel.querySelector("[data-regrade-approve]");
    if (approve) /** @type {HTMLElement} */ (approve).onclick = event => h.run(event.currentTarget, approveSuggestion);
    const change = panel.querySelector("[data-cert-change]");
    if (change) /** @type {HTMLElement} */ (change).onclick = () => mountDesk();
    panel.querySelectorAll(".regrade-panel > .grading-regrade-replies [data-regrade-check]").forEach(button => { /** @type {HTMLElement} */ (button).onclick = event => h.run(event.currentTarget, async () => {
      const result = await h.api(`${base(aid)}/replies/${encodeURIComponent(button.getAttribute("data-regrade-check") || "")}/check`, "POST", {});
      h.toast(result.reply.status === "accepted" ? "Gradescope shows the reply." : result.reply.status === "failed" ? "Gradescope does not show the reply: reply again." : "Still not confirmed.", result.reply.status !== "accepted");
      await refresh();
    }); });
    panel.querySelectorAll("[data-regrade-mark]").forEach(button => { /** @type {HTMLElement} */ (button).onclick = event => h.run(event.currentTarget, async () => {
      const action = button.getAttribute("data-regrade-mark");
      await h.api(`${base(aid)}/${encodeURIComponent(detail.id)}/mark`, "POST", {request_id: crypto.randomUUID(), action});
      h.toast(action === "handled" ? "Marked handled." : "Reopened.");
      await refresh();
      onChange();
    }); });
    bindRun();
  }

  function bindRun() {
    const runHost = /** @type {HTMLElement | null} */ (panel.querySelector("[data-regrade-run]"));
    if (!runHost) return;
    runHost.querySelectorAll("[data-regrade-retry]").forEach(button => { /** @type {HTMLElement} */ (button).onclick = event => h.run(event.currentTarget, () => advance(button.getAttribute("data-regrade-retry"))); });
    const onward = runHost.querySelector("[data-regrade-continue]");
    if (onward) /** @type {HTMLElement} */ (onward).onclick = event => h.run(event.currentTarget, () => advance());
    const cancel = runHost.querySelector("[data-regrade-cancel]");
    if (cancel) /** @type {HTMLElement} */ (cancel).onclick = event => h.run(event.currentTarget, async () => {
      const current = detail, pending = run;
      const result = await h.api(`${base(aid)}/applies/${encodeURIComponent(pending.id)}/cancel`, "POST", {});
      if (!showing(current) || run !== pending) return;
      run = result;
      stop();
      drawRun();
      drawApply();
    });
    runHost.querySelectorAll("[data-regrade-check]").forEach(button => { /** @type {HTMLElement} */ (button).onclick = event => h.run(event.currentTarget, async () => {
      await h.api(`${base(aid)}/replies/${encodeURIComponent(button.getAttribute("data-regrade-check") || "")}/check`, "POST", {});
      await advance();
    }); });
  }

  // Follow-up's card approved from here: your edited reply saved on the card first (its Edit), then the card's own approval, which
  // changes the grade when it says so and starts the Apply this panel then drives.
  async function approveSuggestion() {
    const current = detail;
    let action = current.suggestion?.action;
    if (!action) return;
    const text = reply()?.value.trim() || "";
    if (text && text !== String(action.reply || "").trim()) {
      await h.api(`/api/student-support/followups/suggestions/${encodeURIComponent(detail.suggestion.run)}/edit`, "POST",
        {action_id: action.id, seen: action.seen, subject: "", body: text, request_id: crypto.randomUUID()}, {}, 180000);
      const again = await h.api(`${base(aid)}/${encodeURIComponent(current.id)}`);
      if (!showing(current)) return;
      action = again.suggestion?.action;
      if (!action) throw new Error("Your edited reply is on the card; approve it on Follow-up.");
    }
    const published = (action.steps || []).find((/** @type {any} */ s) => s.step === "gradescope" && s.published?.length);
    if (published && !window.confirm(`${published.published.join(", ")} is published on Gradescope: the student sees the new grade as soon as Faculty writes it. Approve?`)) return;
    const done = await h.api(`/api/assistant/actions/${encodeURIComponent(action.id)}/approve`, "POST", {seen: action.seen}, {}, 180000);
    if (!showing(current)) return;
    if (done.stale) { h.toast(done.message || "The suggestion changed. Look again.", true); await refresh(); return; }
    if (done.status >= 400 || !done.result?.apply) throw new Error(done.result?.error || "The suggestion could not be applied.");
    typed = false;
    run = done.result.apply;
    drawApply();
    drawRun();
    if (!run.finished) schedule();
    else { h.toast("Applied."); await settle(); }
  }

  function mountDesk() {
    const slot = /** @type {HTMLElement | null} */ (panel.querySelector("[data-regrade-work]"));
    if (!slot || !value) return;
    slot.innerHTML = `<div class="certification-desk-bar"><p class="supporting-copy">Change the grade here; Apply then carries it to Gradescope, the gradebook and Canvas.</p><button type="button" class="button" data-regrade-desk-cancel>Cancel</button></div><div data-regrade-desk></div>`;
    const work = /** @type {HTMLElement} */ (slot.querySelector("[data-regrade-desk]"));
    const current = detail;
    desk = mountReviewDesk(work, value, h, {heading: detail.student ? `${detail.student.name} · Section ${detail.student.section}` : "", statuses: ["approved", "manual"],
      zeroShortcut: true, explain: true, scroll: false, active: () => alive() && work.isConnected,
      onReveal: async () => {
        const found = await h.api(responseReviewUrl(current.response_id, true));
        if (showing(current) && work.isConnected) { value = found; mountDesk(); }
      },
      // GRADING "KEEP CONFLICTED EDITS": the desk keeps the edits; `value` follows the copy it shows, for a later Cancel or reveal.
      reread: async () => {
        const found = await h.api(responseReviewUrl(current.response_id));
        if (showing(current) && work.isConnected) value = found;
        return found;
      },
      onSaved: async () => {
        closeReviewDesk(); desk = null;
        h.toast("Grade saved in Faculty. Apply carries it everywhere.");
        const keep = reply()?.value || "", wasTyped = typed;
        await open(selected);
        const text = reply();
        if (text && wasTyped) { text.value = keep; typed = true; drawDraft(); }
        await loadList();
        onChange();
      }});
    /** @type {HTMLElement} */ (slot.querySelector("[data-regrade-desk-cancel]")).onclick = () => { if (desk?.leave()) { closeReviewDesk(); desk = null; draw(); } };
  }

  // The model's draft: asked for the grade as it is now (the server answers the same draft again), polled while it is written.
  async function askDraft() {
    const current = detail;
    let draft;
    try {
      draft = await h.api(`${base(aid)}/${encodeURIComponent(current.id)}/draft`, "POST", {});
    } catch (cause) {
      draft = {status: "failed", error: /** @type {any} */ (cause).message};
    }
    if (!showing(current)) return;
    detail.draft = draft;
    drawDraft();
    if (["pending", "running"].includes(draft?.status)) schedule();
  }

  function drawDraft() {
    const note = /** @type {HTMLElement | null} */ (panel.querySelector("[data-regrade-draft-note]"));
    const text = reply();
    if (!note || !text) return;
    const suggested = detail.suggestion?.action;
    const draft = suggested ? {status: "complete", reply: suggested.reply, assessment: ""} : detail.draft;
    if (!draft) { note.textContent = ""; return; }
    if (["pending", "running"].includes(draft.status)) { note.textContent = "Drafting a reply from their work and the rubric…"; return; }
    if (draft.status === "failed") { note.textContent = draft.error || "The reply could not be drafted: write it yourself."; return; }
    if (!typed && !text.value.trim()) text.value = draft.reply || "";
    const differs = text.value.trim() !== String(draft.reply || "").trim();
    note.innerHTML = `${draft.assessment ? `<span><strong>The model's view</strong>: ${h.e(draft.assessment)}</span> ` : ""}${differs ? `<button type="button" class="text-button" data-regrade-use-draft>${suggested ? "Use the suggested reply" : "Use the draft"}</button>` : ""}`;
    const use = note.querySelector("[data-regrade-use-draft]");
    if (use) /** @type {HTMLElement} */ (use).onclick = () => { text.value = draft.reply || ""; typed = false; preview = null; drawDraft(); drawApply(); };
  }

  function drawApply() {
    const slot = /** @type {HTMLElement | null} */ (panel.querySelector("[data-regrade-apply]"));
    if (!slot) return;
    if (run && !run.finished) { slot.innerHTML = ""; return; }
    if (!preview) {
      const suggested = Boolean(detail.suggestion?.action);
      slot.innerHTML = `<div class="grading-actions"><button type="button" class="button${suggested ? "" : " primary"}" data-regrade-preview>${suggested ? "Apply this reply myself…" : "Review what Apply does"}</button></div>`;
      /** @type {HTMLElement} */ (slot.querySelector("[data-regrade-preview]")).onclick = event => h.run(event.currentTarget, async () => {
        const current = detail;
        const text = reply()?.value.trim() || "";
        if (!text) throw new Error("Write your reply to the student first.");
        slot.innerHTML = '<p class="supporting-copy" role="status">Reading Gradescope and Canvas…</p>';
        try {
          const found = await h.api(`${base(aid)}/${encodeURIComponent(current.id)}/apply/preview`, "POST", {reply: text}, {}, 120000);
          if (showing(current) && reply()?.value.trim() === text) preview = found;
        } finally { if (showing(current)) drawApply(); }
      });
      return;
    }
    const usable = preview.steps.filter(step => !step.problem);
    const steps = preview.steps.map(step => `<li${step.problem ? " data-problem" : ""}><span>${h.e(stepWords(step))}</span>${step.problem ? `<small class="regrade-step-detail">${h.e(step.problem)}</small>${step.href ? ` <a class="text-button" href="${h.e(step.href)}">${h.e(step.action || "Open")}</a>` : ""}` : ""}</li>`).join("");
    const notes = (preview.notes || []).filter(note => !preview.steps.some(step => step.problem === note));
    const published = preview.steps.find(step => step.step === "gradescope" && step.published?.length);
    slot.innerHTML = `<section class="regrade-apply-preview" data-regrade-apply-preview><h4>Apply does this, in order</h4><ol class="regrade-steps">${steps}</ol>
      ${notes.length ? `<ul class="regrade-notes">${notes.map(note => `<li class="supporting-copy">${h.e(note)}</li>`).join("")}</ul>` : ""}
      <div class="grading-actions"><button type="button" class="button primary" data-outward data-regrade-apply-go${usable.length ? "" : " disabled"}>Apply</button><button type="button" class="text-button" data-regrade-preview-again>Look again</button></div></section>`;
    /** @type {HTMLElement} */ (slot.querySelector("[data-regrade-preview-again]")).onclick = () => { preview = null; drawApply(); };
    /** @type {HTMLElement} */ (slot.querySelector("[data-regrade-apply-go]")).onclick = event => h.run(event.currentTarget, async () => {
      if (applying) return;
      if (published && !window.confirm(`${published.published.join(", ")} is published on Gradescope: the student sees the new grade as soon as Faculty writes it. Apply?`)) return;
      applying = true;
      const current = detail;
      try {
        const result = await h.api(`${base(aid)}/${encodeURIComponent(current.id)}/apply`, "POST", {reply: reply()?.value.trim() || "", fingerprint: preview.fingerprint,
          request_id: crypto.randomUUID(), confirmation: "apply", published: preview.published || []}, {}, 120000);
        if (!showing(current)) return;
        run = result;
      } catch (cause) {
        if (showing(current) && /** @type {any} */ (cause)?.status === 409) { preview = null; drawApply(); }
        throw cause;
      } finally { applying = false; }
      typed = false;
      drawApply();
      drawRun();
      if (!run.finished) schedule();
      else { h.toast("Applied."); await settle(); }
    });
  }

  function drawRun() {
    const runHost = /** @type {HTMLElement | null} */ (panel.querySelector("[data-regrade-run]"));
    if (!runHost) return;
    runHost.innerHTML = runMarkup(run, h);
    bindRun();
  }

  async function advance(retry = null) {
    const current = detail, pending = run;
    const result = await h.api(`${base(aid)}/applies/${encodeURIComponent(pending.id)}/continue`, "POST", retry ? {retry} : {}, {}, 120000);
    if (!showing(current) || run !== pending) return;
    run = result;
    drawRun();
    if (run.finished) { h.toast(run.steps.some(step => step.status === "stopped") ? "Applied, with a step left for you." : "Applied."); await settle(); }
    else if (run.waiting) schedule();
  }

  // After Apply: the list's state and the request's own (answered) read again; the reply box goes with it.
  async function settle() {
    const current = detail, keepRun = run;
    await loadList();
    if (!showing(current)) return;
    const found = await h.api(`${base(aid)}/${encodeURIComponent(current.id)}`);
    if (!showing(current)) return;
    detail = found; run = keepRun;
    draw();
    onChange();
  }

  function stop() { if (timer) { clearTimeout(timer); timer = 0; } }
  function schedule() {
    stop();
    timer = window.setTimeout(async () => {
      timer = 0;
      if (!alive()) return;
      if (document.hidden) { schedule(); return; }
      try {
        if (run && !run.finished && run.waiting) await advance();
        if (detail && detail.suggestion?.state === "preparing") {
          const again = await h.api(`${base(aid)}/${encodeURIComponent(detail.id)}`);
          if (!alive() || !detail || again.id !== detail.id) return;
          if (again.suggestion?.state !== "preparing") { const keep = reply()?.value || "", wasTyped = typed; detail = again; draw(); if (wasTyped) { reply().value = keep; typed = true; drawDraft(); } }
          else schedule();
          return;
        }
        if (detail && ["pending", "running"].includes(detail.draft?.status)) {
          const again = await h.api(`${base(aid)}/${encodeURIComponent(detail.id)}`);
          if (!alive() || !detail || again.id !== detail.id) return;
          detail.draft = again.draft;
          drawDraft();
          if (["pending", "running"].includes(detail.draft?.status)) schedule();
        }
      } catch (cause) { h.toast(/** @type {any} */ (cause).message, true); }
    }, POLL);
  }

  async function refresh() {
    await loadList();
    if (selected) await open(selected);
  }

  return {
    refresh,
    async start() { await loadList(); await open(selected); },
    async route(id) { if (id && id !== selected) { await loadList(); await open(id); } },
    destroy() { destroyed = true; stop(); closeReviewDesk(); unguard?.(); },
  };
}
