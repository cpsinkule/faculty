// Certification and Export: one student's drawer (requirement, 2026-09-26). This step may
// name students; the Review step stays anonymous. The drawer reads GET …/certification/students/<section>/<enrollment> (no UIN) and,
// for a question opened, the ordinary GET /api/grading/responses/<rid> (never with `reveal`: a blind proposal stays hidden unless its
// own Show proposal is clicked). "Change grade" mounts the one review desk (grading-review-desk.js) with approved and manual only,
// saved through the ONLY final-change path; nothing here decides, finalizes or sends a grade. The drawer is an aside inside the page
// (not #dialog, which the Gradescope and Excuse dialogs use), docked at the right of the page (desktop only).
import {registerShortcut, calloutMarkup, disclosureMarkup, SEEN_BY} from "./ui.js";
import {mountReviewDesk, closeReviewDesk} from "./grading-review-desk.js";
import {mathText, transcriptMarkup, renderMath} from "./grading-math.js";
import {cropButton, mountCropZoom} from "./grading-crops.js";
import {evidenceMarkup} from "./grading-evidence.js";
import {proposalComment, reviewReasonMarkup} from "./grading-model-comments.js";
import {rubricDisplay} from "./grading-rubric-display.js";
import {firstSentence, sentences} from "./review-notes.js";
import {automaticSendText, fmt, statusChip, partName} from "./grading-certification-stats.js";
import {decidedChip, flagChips, flagWords, gradescopeChip, orderedFlags, settledBy, signedPoints} from "./grading-certification-question.js";

const EMPTY = "–";
// A row key is "<section_id>:<enrollment_id>"; it goes into an API path (as the Exams page's own), never into the page's address.
export function splitRow(row) {
  const text = String(row || ""), at = text.lastIndexOf(":");
  return at > 0 ? {section: text.slice(0, at), enrollment: text.slice(at + 1)} : null;
}
export const studentUrl = (base, row) => { const key = splitRow(row); return key ? `${base}/students/${encodeURIComponent(key.section)}/${encodeURIComponent(key.enrollment)}` : ""; };

// Escape and Alt+J/K act from a button or a checkbox too (ui.js counts every input as typing), never while text is typed.
export const typingText = (node = document.activeElement) => Boolean(node && (node.isContentEditable || ["TEXTAREA", "SELECT"].includes(node.tagName)
  || (node.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset"].includes(node.type))));

// The one next/previous student of the table as it is filtered now (Alt+J / Alt+K).
export function neighbour(order, row, step) {
  const at = order.indexOf(row);
  if (at < 0) return order[step > 0 ? 0 : order.length - 1] || null;
  return order[at + step] || null;
}

// ------------------------------------------------------------------------------------------------------------------------------
// Pure markup

const DR = b => [b.date, [b.start_time, b.end_time].filter(Boolean).join("–"), b.campus, b.exam_type].filter(Boolean).join(" · ");

// A proposal's stored state in plain words (review UX6).
const STATES = {grading_review: "held for grading review", ocr_review: "held for a reading check", both: "held for reading and grading review",
  trial_accepted: "accepted on trial", stale: "graded under an earlier rubric", draft: "a draft review", unreadable: "marked unreadable", rejected: "rejected"};
export const stateWords = state => STATES[state] || String(state || "").replaceAll("_", " ");

// A note as its first sentence; the whole wording folds when there is more (DESIGN-LANGUAGE principle 13, "No walls of text";
// requirement, 2026-10-06). `render` draws text (math, a model's colour).
export function noteMarkup(text, render) {
  const full = String(text || "").trim();
  if (!full) return "";
  const more = /\n/.test(full) || sentences(full).length > 1;
  return `${render(more ? firstSentence(full) || full : full)}${more ? `<details class="certification-more"><summary>Full note</summary>${render(full)}</details>` : ""}`;
}

// A reviewing model's ruling in plain words (as the Independent checks say it).
const RULED = {
  arbiter: {adopted: "adopted an earlier assessment", revised: "posed its own rubric lines", unreliable: "found no reliable grade", unsettled: "left a flag standing",
    failed: "could not complete", not_asked: "was not asked"},
  flag_review: {finalized: "upheld the grade", manual_review: "asked for your review", unsettled: "left a flag standing", failed: "could not complete",
    not_asked: "was not asked"},
};
const TEAM = {resolved: "agreed", unresolved: "disagreed"};
const words = (map, key) => map[key] || String(key || "").replaceAll("_", " ");

/**
 * The models' original flags as ONE fold under the answer: its summary says whether anything still stands (an orange mark: needs
 * you), who settled it, or that only checks your policy waives were recorded; the body sorts every flag by who raised it, each as a
 * sentence with its full wording folded. Nothing is dropped. No flag: nothing.
 */
export function flagsMarkup(flags, h) {
  if (!flags) return "";
  const note = text => noteMarkup(String(text || ""), t => reviewReasonMarkup(t, h));
  const plain = text => noteMarkup(String(text || ""), t => mathText(t, h));
  const list = items => items.length ? `<ul>${items.map(item => `<li>${note(item)}</li>`).join("")}</ul>` : "";
  const rows = [];
  if (flags.original_state && !["proposed", "approved", "manual", "auto_accepted"].includes(flags.original_state)) rows.push(["When proposed", h.e(capital(stateWords(flags.original_state)))]);
  if (flags.base_state && flags.base_state !== "proposed" && flags.base_state !== flags.original_state) rows.push(["Before the rubric finalization", h.e(capital(stateWords(flags.base_state)))]);
  const reasons = [...new Set([...(flags.reasons || []), ...(flags.risk_blocks || [])].map(String))];
  if (reasons.length) rows.push(["Reasons the models gave", list(reasons)]);
  if (flags.committee) {
    const c = flags.committee;
    rows.push(["Grading team", `${h.e(capital(words(TEAM, c.status)))}${c.reason ? `: ${plain(c.reason)}` : "."}${list((c.review_reasons || []).map(String))}`]);
  }
  for (const [label, key] of [["Final arbitrator", "arbiter"], ["Flag reviewer", "flag_review"]]) {
    const entry = flags[key];
    if (!entry) continue;
    const ruled = (entry.flags || []).map(f => {
      const text = typeof f === "string" ? f : f.text || f.id || "", known = typeof f === "object" && f.resolved !== undefined;
      return `<li>${known ? `<span class="certification-flag-state" data-resolved="${f.resolved ? "yes" : "no"}">${f.resolved ? "Resolved" : "Still stands"}</span> ` : ""}${plain(text)}</li>`;
    }).join("");
    rows.push([label, `${h.e(capital(words(RULED[key], entry.outcome)))}${entry.reason ? `: ${plain(entry.reason)}` : "."}${ruled ? `<ul>${ruled}</ul>` : ""}`]);
  }
  const waived = flags.risk_waived || {};
  const waivedRows = [...new Set([...(waived.blocks || []), ...(waived.reasons || []), ...(waived.review_state_reasons || []), ...(waived.direct_reading?.blocks || []),
    ...(waived.choice_reader?.blocks || [])].map(String))];
  if (waivedRows.length) rows.push(["Waived by your review policy", `<span class="certification-quiet">Recorded, not holding it back.</span>${list(waivedRows)}`]);
  if (flags.risk_individual) rows.push(["Risk checks", "Sent to individual review."]);
  if (!rows.length) return "";
  // What the summary says: a flag that still stands (the page's own "still flagged"), who settled them, or only waived checks.
  const standing = orderedFlags(flags.names).filter(name => name !== "waived");
  const open = standing.length > 0 && !flags.settled;
  const summary = open ? `<span class="certification-flag-standing">Still flagged: ${h.e(flagWords(standing[0]))}${standing.length > 1 ? ` +${standing.length - 1}` : ""}</span>`
    : standing.length ? `<span>${h.e(`Flags: ${lower(settledBy(standing))}`)}</span>`
    : waivedRows.length && rows.length === 1 ? `<span>${h.e(`Checks waived by your policy (${waivedRows.length})`)}</span>` : "<span>The models' original flags</span>";
  return `<details class="disclosure certification-fold certification-flags" data-cert-flags${open ? " open" : ""}><summary>${summary}</summary>
    <div class="disclosure-body"><dl class="certification-said">${rows.map(([label, body]) => `<dt>${h.e(label)}</dt><dd>${body}</dd>`).join("")}</dl></div></details>`;
}
const capital = text => text.charAt(0).toUpperCase() + text.slice(1);
const lower = text => text.charAt(0).toLowerCase() + text.slice(1);

// A rubric line's points as a signed figure: "−0.5" taken off, "+2" earned, "0" for a line worth nothing.
const linePoints = points => Number(points) === 0 ? "0" : signedPoints(points) || String(points ?? "");

/**
 * A response as it stands, read only, goal first (requirement, 2026-10-06):
 * the grade with the lines that made it (each with the model's sentence) and Change grade, the paper, then folds for the lines not
 * applied, the reading (open while a reading flag stands), the models' original flags and the team's checks. `change`: put the
 * Change grade button in the grade's row.
 */
export function responseMarkup(value, question, h, {change = false} = {}) {
  const action = change ? `<button type="button" class="button" data-cert-change>Change grade</button>` : "";
  const flags = flagsMarkup(question?.flags, h);
  if (!value?.result) return `<div class="certification-verdict"><p class="supporting-copy">This response has no completed proposal yet.</p>${action}</div>${flags}`;
  const chosen = value.review || value.result, rules = value.question?.rubric?.rules || {items: []}, display = rubricDisplay(rules);
  const proposed = new Map((value.result.applications || []).map(a => [a.rubric_item_id, a]));
  const applied = new Map((chosen.applications || []).map(a => [a.rubric_item_id, a]));
  const later = question?.later_proposal ? `<p class="grading-warning" role="status" data-cert-later>The model output below is a later proposal, made after your decision; your decision is what counts.</p>` : "";
  const hidden = value.hidden ? `<p class="grading-warning">Blind review: the model's proposal is hidden until you decide. Change grade shows it only if you ask.</p>` : "";
  const line = item => {
    const on = Boolean(applied.get(item.id)?.applied), model = proposed.get(item.id);
    const comment = !value.hidden && model?.reason ? `<div class="certification-line-comment">${noteMarkup(model.reason, text => proposalComment(value.result.signals, text, h))}</div>` : "";
    return `<li data-applied="${on ? "yes" : "no"}"><span class="certification-line-points" aria-label="${on ? "Applied" : "Not applied"}, ${h.e(item.points)} points">${h.e(linePoints(item.points))}</span>
      <div class="certification-line-text"><span class="grading-rubric-number">${h.e(display.number(item.id))}.</span> ${mathText(item.description, h)}${comment}</div></li>`;
  };
  const items = rules.items || [], on = items.filter(item => applied.get(item.id)?.applied), off = items.filter(item => !applied.get(item.id)?.applied);
  // Point adjustments (a batch line from Certification and Export, or one typed at the desk) are named with the lines they add to.
  const adjustments = chosen.score?.adjustments || [];
  const adjusted = adjustments.map(a => `<li data-applied="yes" data-cert-adjustment><span class="certification-line-points" aria-label="Adjustment">${h.e(signedPoints(a.points))}</span>
    <div class="certification-line-text">${h.e(a.reason || "")} <small class="certification-quiet">point adjustment</small></div></li>`).join("");
  const score = chosen.score?.score, max = value.question?.content?.max_points;
  const made = [on.length ? `${on.length} ${on.length === 1 ? "line" : "lines"} applied` : "No rubric line applied",
    adjustments.length ? `${adjustments.length} ${adjustments.length === 1 ? "adjustment" : "adjustments"}` : ""].filter(Boolean).join(" · ");
  // The score only when the question's own row (just above) does not already say it.
  const said = question?.score !== null && question?.score !== undefined && fmt(question.score) === fmt(score);
  const shown = said ? "" : `<strong data-cert-score>${score === null || score === undefined ? "Not scored" : `${h.e(fmt(score))} / ${h.e(fmt(max))}`}</strong> `;
  const verdict = `<div class="certification-verdict"><p>${shown}<span>${h.e(made)}</span></p>${action}</div>`;
  const yours = value.review?.comment ? `<p class="certification-line-comment" data-cert-comment><strong>Your comment</strong> <small class="certification-quiet" data-seen-by="you">(${h.e(SEEN_BY.you.toLowerCase())})</small>: ${mathText(value.review.comment, h)}</p>` : "";
  const crops = value.crops || [];
  const paper = crops.length ? `<div class="certification-crops">${crops.map((crop, i) => `<figure>${cropButton(crop.images.original.url, `response crop ${i + 1}`, h)}${crops.length > 1 ? `<figcaption>Region ${i + 1}</figcaption>` : ""}</figure>`).join("")}</div>` : "";
  const others = off.length ? disclosureMarkup({label: `Other rubric lines (${off.length})`, body: `<ul class="certification-line-list" data-cert-other-lines>${off.map(line).join("")}</ul>`,
    className: "certification-fold certification-other-lines"}) : "";
  const doubt = ((question?.flags?.names || []).includes("reading") && !question?.flags?.settled) || Boolean(value.result.signals?.risk?.components?.ocr?.disagreements);
  const reading = disclosureMarkup({label: "What the reader read", hint: doubt ? "in doubt" : "", open: doubt, className: "certification-fold certification-reading",
    body: `<div class="grading-transcript-preview">${transcriptMarkup(chosen.transcript || {}, h)}</div>`});
  return `${later}${hidden}<div class="certification-response">
    <section class="certification-lines" aria-label="The grade">${verdict}${on.length || adjusted ? `<ul class="certification-line-list" data-cert-applied-lines>${on.map(line).join("")}${adjusted}</ul>` : ""}${yours}</section>
    ${paper}${others}${reading}${flags}${value.hidden ? "" : evidenceMarkup(value, h)}</div>`;
}

function questionMarkup(q, i, open, h) {
  const score = q.score === null || q.score === undefined ? EMPTY : `${fmt(q.score)} / ${fmt(q.max)}`;
  const title = `${q.title}${q.version ? ` · Version ${q.version}` : ""}`;
  return `<li class="certification-drawer-question" data-cert-q="${h.e(q.question_id)}"${open ? " data-open" : ""}>
    <button type="button" class="certification-q-toggle" data-cert-q-toggle="${h.e(q.question_id)}" aria-expanded="${open}" aria-controls="cert-q-${i}"${q.response_id ? "" : " disabled"}><span class="certification-name">${h.e(title)}</span><strong>${h.e(score)}</strong></button>
    <p class="certification-q-meta">${decidedChip(q.decided, h)} ${flagChips(q.flags?.names, h, {settled: q.flags?.settled})} ${gradescopeChip(q.gradescope, h)}${q.missing_reason ? ` <small class="certification-note">${h.e(q.missing_reason)}</small>` : ""}${q.participation ? ` <small class="certification-note">Graded for participation +${h.e(q.participation)}</small>` : ""}</p>
    <div class="certification-q-body" id="cert-q-${i}" data-cert-q-body${open ? "" : " hidden"}></div></li>`;
}

// `answers`: the multiple-choice key of the sheet's version (view.bubble_questions), to show the key beside each wrong answer.
function bubbleMarkup(sheet, answers, plan, h) {
  if (!sheet) return "";
  const key = new Map((answers || []).map(q => [String(q.number), q.answer]));
  const wrong = (sheet.wrong || []).map(n => `${n}${key.get(String(n)) ? ` (key ${key.get(String(n))})` : ""}`).join(", ");
  const how = {label: "by its Scantron label", uin: "by the UIN written", confidence: "by a confident match", review: "by your review"}[sheet.identified_by] || "";
  const link = sheet.job_id ? `<a class="link-arrow" data-cert-sheet-link href="#inbox/job/${h.e(encodeURIComponent(sheet.job_id))}/sheets${sheet.sheet_id ? `?sheet=${h.e(encodeURIComponent(sheet.sheet_id))}` : ""}">Review this sheet in the Inbox${sheet.page ? ` (page ${h.e(sheet.page)})` : ""}</a>` : "";
  return `<section class="certification-drawer-part" data-cert-part="bubble"><h4>${h.e(partName("bubble"))}</h4>
    <p>${sheet.earned !== null && sheet.earned !== undefined ? `<strong>${h.e(fmt(sheet.earned))} / ${h.e(fmt(sheet.possible))}</strong> · ` : ""}${h.e(String(sheet.status || "").replaceAll("_", " "))}${sheet.version ? ` · Version ${h.e(sheet.version)}` : ""}${how ? ` · identified ${h.e(how)}` : ""}</p>
    ${sheet.reason ? `<p class="supporting-copy">${h.e(sheet.reason)}</p>` : ""}${sheet.several ? `<p class="grading-warning">More than one sheet is matched to this student.</p>` : ""}
    <p data-cert-wrong>${wrong ? `Wrong: ${h.e(wrong)}` : "No wrong answer."}</p>
    <p class="certification-drawer-links">${link} <a class="link-arrow" href="#grading/${h.e(plan.id)}?tab=bubble">Answer key</a></p></section>`;
}

/**
 * The drawer's whole markup for one student's detail. `open`: the question expanded; `row`: the page's own row of this student
 * (status, total); `anonymous`: opened from a question with names hidden.
 */
export function drawerMarkup(detail, {row = null, open = "", anonymous = false, plan, view, position = null} = {}, h) {
  const s = detail.student || {}, total = row?.total ? `${fmt(row.total.earned)} / ${fmt(row.total.possible)}` : "";
  const name = anonymous ? "Student (name hidden)" : s.name;
  const meta = [`Section ${s.section}`, s.exam_version ? `Version ${s.exam_version}` : "", total].filter(Boolean).join(" · ");
  // LATE PAPERS CERTIFY THEMSELVES: certified by Faculty itself since you last certified, and what its own Gradescope send did.
  const status = (row ? statusChip(row, h, view) : "") + (row?.by_faculty ? ` <span class="chip" data-tone="neutral" data-cert-by-faculty>By Faculty</span>` : "");
  const sent = row?.gradescope?.automatic ? `<p class="supporting-copy" data-cert-drawer-automatic>${h.e(automaticSendText(row.gradescope.automatic))}</p>` : "";
  const excuse = row?.status === "missing" ? `<button type="button" class="button" data-cert-drawer-excuse>Excuse…</button>` : "";
  const collection = view?.event_id ? `<a class="link-arrow" href="#exams/${h.e(encodeURIComponent(view.event_id))}?tab=collection">Exam collection</a>` : "";
  const frq = detail.parts?.frq;
  const frqPart = frq && frq.expected ? `<section class="certification-drawer-part" data-cert-part="frq"><h4>${h.e(partName("frq"))}</h4><p>${frq.earned !== null && frq.earned !== undefined ? `<strong>${h.e(fmt(frq.earned))} / ${h.e(fmt(frq.possible))}</strong> · ` : ""}${h.e(fmt(frq.final, 0))} of ${h.e(fmt(frq.expected, 0))} questions final</p></section>` : "";
  const letter = detail.parts?.bubble?.version || s.exam_version || "";
  const issues = (s.collection_issues || []).length ? `<ul class="certification-issues">${s.collection_issues.map(issue => `<li>${h.e(issue)}</li>`).join("")}</ul>` : "";
  const dr = (detail.dr || []).length ? `<p class="supporting-copy">DR exam: ${detail.dr.map(b => h.e(DR(b))).join("; ")}${detail.dr.length > 1 ? " · several bookings" : ""}</p>` : "";
  const certified = detail.certified ? `<p class="supporting-copy" data-cert-drawer-certified>Certified ${h.e(new Date(detail.certified.created_at).toLocaleDateString(undefined, {month: "short", day: "numeric"}))}: ${h.e(detail.certified.earned === null || detail.certified.earned === undefined ? String(detail.certified.status).replaceAll("_", " ") : `${fmt(detail.certified.earned)} / ${fmt(detail.certified.possible)}`)}${row?.changed_since ? " · changed since" : ""}</p>` : "";
  const questions = (detail.questions || []).map((q, i) => questionMarkup(q, i, q.question_id === open, h)).join("");
  const steps = position ? `<span class="certification-drawer-steps"><button type="button" class="button" data-cert-drawer-step="-1" aria-keyshortcuts="Alt+K"${position.previous ? "" : " disabled"}>← Previous</button><button type="button" class="button" data-cert-drawer-step="1" aria-keyshortcuts="Alt+J"${position.next ? "" : " disabled"}>Next →</button></span>` : "";
  return `<header class="certification-drawer-head">
      <div class="certification-drawer-title"><h3 id="cert-drawer-title" tabindex="-1">${h.e(name)}</h3><button type="button" class="icon-button certification-drawer-x" data-cert-drawer-close aria-label="Close" title="Close (Escape)">×</button></div>
      <p class="certification-drawer-meta">${h.e(meta)} ${status}</p>
      ${s.reason || row?.reason ? `<p class="supporting-copy">${h.e(row?.reason || s.reason)}</p>` : ""}${issues}${dr}${certified}${sent}
      <div class="certification-drawer-actions">${excuse}${collection}${steps}</div></header>
    <div class="certification-drawer-body">${frqPart}${bubbleMarkup(detail.parts?.bubble, view?.bubble_questions?.[letter], plan, h)}
      ${questions ? `<section class="certification-drawer-part"><h4>Questions</h4><ol class="certification-drawer-questions">${questions}</ol></section>` : `<p class="supporting-copy">No written question belongs to this student's paper.</p>`}</div>`;
}

// ------------------------------------------------------------------------------------------------------------------------------
// The drawer

/**
 * @param {HTMLElement} host the drawer's container inside the page (never #dialog)
 * @param {{base: string, plan: any, h: any, active: () => boolean, view: () => any, order: () => string[], onChanged: () => Promise<void>,
 *   onExcuse: (student: any) => void, onToggle: (open: boolean, row: string) => void}} options
 */
export function createDrawer(host, {base, plan, h, active, view, order, onChanged, onExcuse, onToggle}) {
  let row = "", detail = null, open = "", anonymous = false, desk = null, generation = 0, returnTo = null, keys = [], drawnRow = "";
  const on = () => Boolean(row) && active() && host.isConnected;
  const node = () => host.querySelector(".certification-drawer");
  const pageRow = () => (view()?.students || []).find(s => s.row === row) || null;

  function leave() { return desk ? desk.leave() : true; }
  function unmountDesk() { closeReviewDesk(); desk = null; }

  function draw() {
    if (!on() || !detail) return;
    const list = order(), position = {previous: neighbour(list, row, -1), next: neighbour(list, row, 1)};
    const aside = node();
    const kept = aside?.querySelector(`[data-cert-q="${CSS.escape(open)}"] [data-cert-q-body]`);
    drawnRow = JSON.stringify(pageRow());
    aside.innerHTML = drawerMarkup(detail, {row: pageRow(), open, anonymous, plan, view: view(), position}, h);
    aside.setAttribute("aria-label", anonymous ? "Student (name hidden)" : detail.student?.name || "Student");
    renderMath(aside);
    // The question open keeps its body (its response, or the desk with unsaved work) across a redraw of the drawer.
    const slot = open && aside.querySelector(`[data-cert-q="${CSS.escape(open)}"] [data-cert-q-body]`);
    if (slot && kept) slot.replaceWith(kept);
  }

  async function load(focus = true) {
    const token = ++generation, url = studentUrl(base, row);
    try {
      const found = await h.api(url);
      if (!on() || token !== generation) return;
      detail = found;
      draw();
      if (focus) node()?.querySelector("#cert-drawer-title")?.focus({preventScroll: true});
      if (open) await expand(open, {force: true});
    } catch (error) {
      if (!on() || token !== generation) return;
      node().innerHTML = `<header class="certification-drawer-head"><div class="certification-drawer-title"><h3 id="cert-drawer-title" tabindex="-1">Student</h3><button type="button" class="icon-button" data-cert-drawer-close aria-label="Close">×</button></div></header>${calloutMarkup({tone: "danger", title: "This student could not be read", body: error.message})}`;
    }
  }

  // One question open at a time, one desk at a time: opening another asks through the desk's leave first.
  async function expand(questionId, {force = false, change = false} = {}) {
    if (!force && questionId === open && !change) { collapse(); return; }
    if (!leave()) return;
    unmountDesk();
    open = questionId;
    const aside = node();
    aside.querySelectorAll("[data-cert-q]").forEach(item => {
      const mine = item.dataset.certQ === questionId;
      item.toggleAttribute("data-open", mine);
      item.querySelector("[data-cert-q-toggle]")?.setAttribute("aria-expanded", String(mine));
      const body = item.querySelector("[data-cert-q-body]");
      if (body) body.hidden = !mine;
    });
    const q = (detail?.questions || []).find(entry => entry.question_id === questionId);
    const slot = aside.querySelector(`[data-cert-q="${CSS.escape(questionId)}"] [data-cert-q-body]`);
    if (!q || !slot) return;
    if (!q.response_id) { slot.innerHTML = `<p class="supporting-copy">${h.e(q.missing_reason || "No response of this student to this question.")}</p>`; return; }
    slot.innerHTML = `<p class="loading-state" role="status">Loading the answer and its rubric…</p>`;
    const token = ++generation;
    try {
      // `details=review`, here and on the desk's own reads below: its reasons gathered by cause (GRADING, 2026-10-11).
      const value = await h.api(`/api/grading/responses/${encodeURIComponent(q.response_id)}?details=review`);
      if (!on() || token !== generation || open !== questionId) return;
      if (change) mountDesk(slot, value, q); else show(slot, value, q);
    } catch (error) {
      if (on() && token === generation) slot.innerHTML = `<p role="alert">${h.e(error.message)}</p>`;
    }
  }

  function show(slot, value, q) {
    unmountDesk();
    slot.innerHTML = responseMarkup(value, q, h, {change: true});
    renderMath(slot);
    mountCropZoom(slot);
    slot.querySelector("[data-cert-change]").onclick = () => mountDesk(slot, value, q);
  }

  function mountDesk(slot, value, q, reveal = false) {
    const s = detail.student || {};
    slot.innerHTML = `<div class="certification-desk-bar"><p class="supporting-copy">Your decision is saved as an ordinary review: approve the grade as it stands or after ticking other lines, or record a manual grade.</p><button type="button" class="button" data-cert-desk-cancel>Cancel</button></div><div data-cert-desk></div>`;
    const work = slot.querySelector("[data-cert-desk]");
    const heading = `${anonymous ? "Student (name hidden)" : s.name} · Section ${s.section}${s.exam_version ? ` · Version ${s.exam_version}` : ""}`;
    // A grade of a current certification: the desk asks before it grades this response again (grading-review-4).
    const certification = view()?.certification || {};
    desk = mountReviewDesk(work, value, h, {heading, statuses: ["approved", "manual"], zeroShortcut: true, explain: true, scroll: false,
      certified: certification.state === "current" ? certification.created_at || "" : "",
      moveKeys: [["Alt J", "next student"], ["Alt K", "previous student"]],
      active: () => on() && work.isConnected,
      onReveal: async () => {
        const again = await h.api(`/api/grading/responses/${encodeURIComponent(q.response_id)}?details=review&reveal=1`);
        if (on() && work.isConnected) mountDesk(slot, again, q, true);
      },
      // GRADING "KEEP CONFLICTED EDITS": the desk keeps the edits and reads this response as it was shown.
      reread: () => h.api(`/api/grading/responses/${encodeURIComponent(q.response_id)}?details=review${reveal ? "&reveal=1" : ""}`),
      onSaved: async () => {
        unmountDesk();
        // The student again (their scores and flags), then the page: a certification that was current reads "changed" now.
        await load(false);
        await onChanged();
      }});
    slot.querySelector("[data-cert-desk-cancel]").onclick = () => { if (leave()) { unmountDesk(); show(slot, value, q); } };
    // The desk replaces the read-only view; bring its start to the top of the drawer, so the evidence and the rubric follow at once
    // (review UX5: the change was otherwise opened wherever the read-only view had been scrolled to).
    if (host.scrollHeight > host.clientHeight) host.scrollTop += slot.getBoundingClientRect().top - host.getBoundingClientRect().top;
  }

  function collapse() {
    if (!leave()) return;
    unmountDesk();
    const item = node()?.querySelector(`[data-cert-q="${CSS.escape(open)}"]`);
    open = "";
    if (!item) return;
    item.removeAttribute("data-open");
    item.querySelector("[data-cert-q-toggle]")?.setAttribute("aria-expanded", "false");
    const body = item.querySelector("[data-cert-q-body]");
    if (body) { body.hidden = true; body.innerHTML = ""; }
  }

  function bindKeys() {
    keys.forEach(off => off());
    const when = () => on() && !typingText();
    keys = [
      registerShortcut({key: "Escape", label: "Close the student", run: () => close(), when, allowInInput: true}),
      registerShortcut({key: "Alt+J", label: "Next student", run: () => step(1), when, allowInInput: true}),
      registerShortcut({key: "Alt+K", label: "Previous student", run: () => step(-1), when, allowInInput: true}),
    ];
  }

  function step(direction) {
    const target = neighbour(order(), row, direction);
    if (target && target !== row) void openStudent(target, {focus: true});
  }

  async function openStudent(target, {question = "", anonymous: hide = false, change = false, from = null, focus = true} = {}) {
    if (row && !leave()) return false;
    unmountDesk();
    if (from) returnTo = from;
    const switching = Boolean(row) && row !== target;
    row = target; open = question; anonymous = hide; detail = null;
    if (!node()) {
      host.innerHTML = `<aside class="certification-drawer" role="dialog" aria-modal="false" aria-labelledby="cert-drawer-title"><p class="loading-state" role="status">Loading the student…</p></aside>`;
      bindKeys();
    } else if (switching) node().innerHTML = `<p class="loading-state" role="status">Loading the student…</p>`;
    onToggle?.(true, row);
    await load(focus);
    if (change && question && on()) await expand(question, {force: true, change: true});
    return true;
  }

  function close() {
    if (!row || !leave()) return false;
    unmountDesk();
    keys.forEach(off => off());
    keys = [];
    const was = row;
    row = ""; open = ""; detail = null; generation++;
    host.innerHTML = "";
    onToggle?.(false, was);
    // Focus goes back to where the drawer was opened from (the row's button), when it is still on the page.
    const back = returnTo?.isConnected ? returnTo : document.querySelector(`[data-cert-open="${CSS.escape(was)}"]`);
    back?.focus({preventScroll: false});
    returnTo = null;
    return true;
  }

  host.addEventListener("click", event => {
    const target = event.target;
    if (target.closest?.("[data-cert-drawer-close]")) { close(); return; }
    const toggle = target.closest?.("[data-cert-q-toggle]");
    if (toggle) { void expand(toggle.dataset.certQToggle); return; }
    const stepButton = target.closest?.("[data-cert-drawer-step]");
    if (stepButton) { step(Number(stepButton.dataset.certDrawerStep)); return; }
    if (target.closest?.("[data-cert-drawer-excuse]")) { const student = pageRow(); if (student) onExcuse(student); return; }
    // Links out of the page (the Inbox, the Exams page, the key) close the drawer first, asking about unsaved work.
    const link = target.closest?.("a[href^='#']");
    if (link && !leave()) event.preventDefault();
  });

  return {
    open: openStudent,
    close,
    refresh: () => { if (on()) void load(false); },
    // After the page read the view again: the header follows the student's row (status, total, "changed since"), and only when
    // that row changed, so a poll that brought nothing new leaves the drawer (and its focus) alone.
    redraw: () => { if (on() && detail && JSON.stringify(pageRow()) !== drawnRow) draw(); },
    // The page is gone (another address): its keys go, without asking (the shell's leave guard already asked).
    dispose: () => { keys.forEach(off => off()); keys = []; unmountDesk(); row = ""; },
    get row() { return row; },
    dirty: () => Boolean(desk?.dirty()),
  };
}

// ------------------------------------------------------------------------------------------------------------------------------
// Excuse: the Exams page's own participant record (PATCH /api/exams/<event>/participants/<section>/<enrollment>), with a reason.
// Excused students are never given 0. A 409 (the record changed) is shown; nothing is retried.

export function excuseDialog(h, eventId, student, onDone) {
  const key = splitRow(student.row);
  if (!key || !eventId) { h.toast("This student cannot be excused from here. Use the Exams page.", true); return null; }
  return h.dialog(`Excuse ${student.name}`, "Recorded on the Exams page as excused. Excused students are never given 0 and are not certified as missing.",
    `${h.area("Reason (required)", "reason", "", 'rows="3" maxlength="2000" required')}<p class="supporting-copy">Section ${h.e(student.section)}. This changes no seating, DR booking or grade.</p>`,
    "Excuse student", async (values, form, live) => {
      const reason = String(values.get("reason") || "").trim();
      if (!reason) throw new Error("Write the reason for excusing this student.");
      await h.api(`/api/exams/${encodeURIComponent(eventId)}/participants/${encodeURIComponent(key.section)}/${encodeURIComponent(key.enrollment)}`, "PATCH",
        {version: student.participant_version, expectation: "excused", reason});
      if (live()) h.close();
      h.toast(`${student.name} is excused.`);
      await onDone?.();
    });
}
