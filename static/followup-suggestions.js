// ASSISTANT-FOLLOWUP-SUGGESTIONS / TEACHING-WORKFLOWS: one Suggested actions section for the Follow-up page and the Assistant's
// Changes tab: the same cards, Approve all, Edit in Mail, decline notes, Prepare now and the per-conversation switch, with one set of
// handlers. Every write is the instructor's own click; the server rechecks each card, the run and the conversation.
import { DEPARTMENT_REQUEST, ensureGoogleSignIn } from "./aim-signin.js";
import { actionReviewMessage, cardMarkup, esc, followAimUpload, markdown, valueMarkup } from "./assistant.js";
import { LEARNED_FROM, REMEMBER_WAIT, evidenceMarkup, followupRevisionMarkup, questionMarkup, rememberedNotice, requestFollowupSuggestions, verificationMarkup } from "./assistant-workspace.js";
import { displaySpan } from "./calendar-feed.js";
import { errorMessage, runFormAction, setBusy, wallDate } from "./ui.js";
import { openWatch, watchesMarkup } from "./followup-watches.js";
import { icon } from "./icons.js";

const BASE = "/api/student-support/followups/suggestions";

export const SUGGESTION_LABELS = /** @type {Record<string, string>} */ ({
  queued: "Waiting for the Assistant", running: "Preparing suggestions…", ready: "Ready for your review", needs_input: "Needs your input",
  failed: "Could not finish preparing suggestions", interrupted: "Preparation was interrupted",
  superseded: "The follow-up changed; these proposals no longer apply", settled: "Reviewed", unavailable: "Assistant is not configured",
  paused: "Automatic suggestions are paused", muted: "Suggestions are off for this conversation",
  analyzing: "Waiting for the conversation analysis",
});

/** One proposal in a few words, for the plan above the cards: the server's plain title (FOLLOW-UP CLARITY), else from the request.
 * @param {any} action */
export function proposalLabel(action) {
  if (action.effect?.title) return String(action.effect.title);
  const body = action.body || {};
  const when = () => [body.date ? wallDate(body.date) : "", body.time ? displaySpan(body.time, body.end_time || "") : ""].filter(Boolean).join(" ");
  switch (action.endpoint) {
    case "gmail_mail.send": return body.reply_thread_id ? "Reply" : "Email";
    case "student_support.create":
    case "student_support.revise":
      if (body.kind === "appointment" || body.end_time) return `Office hours ${when()}`.trim();
      return ({ waiting: "Mark waiting on student", resolved: "Mark resolved", todo: "Mark To do", snoozed: `Follow up ${body.date ? wallDate(body.date) : "later"}`,
        needs_reply: "Track this conversation" })[body.state] || "Update follow-up";
    case "absences.create": return "Absence record";
    case "absences.revise": return "Update absence record";
    case "exams.sitting": return `Makeup sitting ${when()}`.trim();
    case "exams.participant": return "Seat in makeup";
    case "assistant.code_task_start": return "Change Faculty";
    default: return String(action.summary || "Change");
  }
}

/** APPROVING (requirement, 2026-10-06): the cards this browser sent for approval and has not heard back on, by
 * action ID. A read that lands meanwhile shows them as Approving…, without Approve, Decline or Approve all. Browser memory only;
 * the server's record takes over when the request returns. */
const approving = /** @type {Set<string>} */ (new Set());

/** The listing's items with each card still being approved from this browser shown as Approving… (copies; the same array when
 * nothing is being approved). @param {any[]} items */
export function withApproving(items) {
  if (!approving.size) return items;
  // Still waiting, or approved with no result yet (the server runs it: a regrade decision's approval read Gradescope and Canvas for
  // 51 s on 2026-10-08): either way it is Approving… here until the request returns.
  const going = (/** @type {any} */ a) => (a.pending || a.status === "approved") && approving.has(a.id);
  const mark = (/** @type {any[]} */ list) => list.map((/** @type {any} */ a) => going(a) ? { ...a, pending: false, status: "approving" } : a);
  return items.map((item) => {
    const s = item?.suggestions;
    if (!s?.actions?.some(going) && !s?.chat?.some(going)) return item;
    return { ...item, suggestions: { ...s, actions: mark(s.actions || []), ...(s.chat ? { chat: mark(s.chat) } : {}) } };
  });
}

/** Sends `work` (an approval of these cards) with them marked Approving… for every read until it returns. `changed` draws the page
 * again at once from the read it holds. @template T @param {string[]} ids @param {() => Promise<T>} work @param {() => void} [changed] */
export async function whileApproving(ids, work, changed) {
  ids.forEach((id) => approving.add(id));
  changed?.();
  try {
    return await work();
  } finally {
    ids.forEach((id) => approving.delete(id));
  }
}

/** How many of a follow-up's cards are being approved from this browser (`withApproving`). @param {any} s */
export const approvingCount = (s) => (s?.actions || []).filter((/** @type {any} */ a) => a.status === "approving").length;

/** A succeeded card's last receipt confirms the work: not failed or uncertain, and a Gmail send must be accepted. @param {any} action */
export function resultConfirmed(action) {
  const result = action.events?.at(-1)?.detail?.result || {};
  return !["failed", "uncertain"].includes(result.status) && (action.endpoint !== "gmail_mail.send" || result.status === "accepted");
}

/** A card that needs a look although nothing waits on it, as the server's `problem` says (assistant/followup_plan.py): failed,
 * unconfirmed, partly done, a mail Gmail did not accept, a Mail replacement not accepted, or approved with no result yet. A step
 * Faculty is still carrying out (`started`) or one the instructor cancelled is neither. @param {any} a */
function needsLook(a) {
  if (a.replaced) return a.replaced.status !== "accepted";
  if (a.outcome) return ["failed", "approved", "uncertain", "partial"].includes(a.outcome);
  if (["failed", "approved", "uncertain"].includes(a.status)) return true;
  if (a.status !== "succeeded") return false;
  return !resultConfirmed(a);
}

/** "Some work still needs attention", less what this browser is still approving (requirement, 2026-10-08). While a card of this follow-up reads Approving…, the warning stands
 * only for another card that needs a look. @param {any} s */
export function needsAttention(s) {
  if (!s?.needs_attention) return false;
  const actions = s.actions || [];
  if (!actions.some((/** @type {any} */ a) => a.status === "approving")) return true;
  return actions.some((/** @type {any} */ a) => a.status !== "approving" && needsLook(a));
}

/** Suggestions that wait for the instructor: cards to review or a question to answer. @param {any} item */
export function suggestionsReady(item) {
  const s = item?.suggestions;
  return Boolean(s && (s.watch_attention || s.state !== "superseded" && (needsAttention(s) || ["ready", "needs_input"].includes(s.state) && ((s.actions || []).some((/** @type {any} */ a) => a.pending) || s.question))));
}

/** @param {number} n */
function ordinal(n) {
  const suffix = ["th", "st", "nd", "rd"], v = n % 100;
  return `${n}${suffix[(v - 20) % 10] || suffix[v] || suffix[0]}`;
}

/** "a, b and c" (labels of what changed since a draft: ASSISTANT CONTEXT). @param {string[]} labels */
function listed(labels) {
  return labels.length < 2 ? labels.join("") : `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

/** WHAT CAN MAKE A STEP WRONG (2026-10-08): what Approve would refuse, then what it approves past, before the click.
 * @param {any} s @param {boolean} stale @param {(value: any) => string} e */
function sinceDraftMarkup(s, stale, e) {
  const found = s.context_changes;
  if (!found || stale) return "";
  const refuse = found.refuse || [], warn = found.warn || [];
  const named = listed(refuse);
  return (refuse.length ? `<div class="callout" data-tone="warning" role="note" data-followup-outdated><strong class="callout-title">This draft is out of date</strong>
      <p>${e(named.charAt(0).toUpperCase() + named.slice(1))} changed since it was prepared. Retry suggestions before approving.</p></div>` : "")
    + (warn.length ? `<p class="field-hint" data-followup-changed>Since this was drafted, ${e(listed(warn))} changed. Retry suggestions to redraft, or approve as it is.</p>` : "");
}

/** @param {any} queue */
function queueText(queue) {
  if (!queue) return "";
  if (queue.held) return "Held while automatic suggestions are paused.";
  if (!queue.position) return "";
  return queue.position === 1 ? "Next in line." : `${ordinal(queue.position)} in line of ${queue.total}.`;
}

/** Whether a card's calendar note warns (an overlap, Google not checked): shown up front. @param {any} note */
export const calendarWarns = (note) => Boolean(note && (note.overlaps?.length || note.unchecked));

/** FOLLOW-UP CALENDAR: what else the calendar holds at a proposed meeting, read when the card is shown; SCHEDULING "GOOGLE
    CHECKED": whether Google could be checked, its own line. @param {any} note */
export function calendarMarkup(note) {
  if (!note) return "";
  // A student's appointment, never named here; meetings share one another's time (SCHEDULING, 2026-10-06), so it reads "During another …".
  const entry = (/** @type {any} */ e, first = "Another") => `${esc(e.source === "office_hours" ? `${first} office-hours appointment` : e.title)} (${esc(displaySpan(e.time, e.end_time))})`;
  const warn = (/** @type {string} */ label, /** @type {string} */ text) => `<p class="assistant-card-note support-calendar" data-tone="warning"><strong>${label}</strong> ${text}</p>`;
  const extra = note.unchecked ? warn("Google Calendar not checked:", `${esc(note.unchecked)}.`) : "";
  if (note.overlaps?.length) return warn("Overlaps on your calendar:", note.overlaps.map((/** @type {any} */ e) => entry(e)).join("; ")) + extra;
  if (note.within?.length) return `<p class="assistant-card-note support-calendar">During ${note.within.map((/** @type {any} */ e) => entry(e, "another")).join("; ")}.</p>${extra}`;
  return extra || '<p class="assistant-card-note support-calendar">Nothing else is on your calendar then.</p>';
}

/** FOLLOW-UP CLARITY: a step's number, serif like the grading pipeline's (DESIGN-LANGUAGE: numbers, not badges). @param {number} step */
const stepMark = (step) => `<span class="support-step"><span class="sr-only">Step </span>${step}</span>`;

/** DECISION ON THE CARD (requirement, 2026-10-12): the cards whose decision this browser is saving, by action ID, with the
 * decision chosen. A read that lands meanwhile keeps the dropdown on that choice, disabled. Browser memory only. */
const deciding = /** @type {Map<string, string>} */ (new Map());

/** An absence card's decision as a dropdown on its own line, on the decision the card proposes. Choosing another saves it on the
 * card (`suggestionChange`); nothing is recorded until the card is approved. @param {any} change @param {string} id the card */
function decisionMarkup(change, id) {
  // DEPARTMENT MAKEUP EXAMS (2026-10-22): a card may have several dropdowns, each naming the request field it sets (`choice.field`).
  const field = String(change.choice.field || ""), going = deciding.get(field ? `${id}|${field}` : id), value = going ?? change.choice.value;
  return `<select class="support-field-choice" data-followup-decision="${esc(id)}"${field ? ` data-followup-field="${esc(field)}"` : ""} data-value="${esc(change.choice.value)}" aria-label="${esc(change.label)}"${going ? " disabled" : ""}>${
    change.choice.options.map((/** @type {any} */ o) => `<option value="${esc(o.value)}"${o.value === value ? " selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
}

/** What a step sets: each field in words; a revision shows what it was and what it becomes. With `choose` (a waiting card's
 * action ID), a field that names its choices is a dropdown (DECISION ON THE CARD). @param {any[]} [changes] @param {string} [choose] */
export function changesMarkup(changes, choose = "") {
  if (!changes?.length) return "";
  const value = (/** @type {any} */ c) => choose && c.choice?.options?.length ? decisionMarkup(c, choose) : esc(c.value);
  return `<dl class="assistant-fields support-changes">${changes.map((/** @type {any} */ c) => `<dt>${esc(c.label)}</dt><dd>${c.before !== undefined
    ? `<del>${esc(c.before)}</del> <span aria-hidden="true">→</span><span class="sr-only"> becomes </span> <ins>${value(c)}</ins>` : value(c)}</dd>`).join("")}</dl>`;
}

/** DECISION ON THE CARD: once the instructor chose another decision than the Assistant's, the card says whose it is and what was
 * suggested (`choice.suggested`, the server's). @param {any[]} changes */
function decidedMarkup(changes) {
  const choice = changes.find((/** @type {any} */ c) => c.choice?.suggested && c.choice.suggested !== c.choice.value)?.choice;
  const suggested = choice?.options.find((/** @type {any} */ o) => o.value === choice.suggested)?.label;
  return suggested ? `<p class="assistant-card-note support-decided">Your decision. The Assistant suggested “${esc(suggested)}”.</p>` : "";
}

/** WIDE SCOPE / ASSISTANT-CERTIFIED: who else a step reaches and certified work, from the server's reading and the card's reason.
 * @param {any} action */
function scopeMarkup(action) {
  const effect = action.effect || {}, reason = String(action.reason || ""), notes = [];
  // A COORDINATOR'S FOLLOW-UP: its steps change what the coordinator's message calls for, never "this follow-up's student".
  const coordinator = /a change the coordinator’s message calls for/.test(reason);
  if (effect.own === false) notes.push(coordinator ? `This changes a record of ${effect.who || "a student"}. Check before approving.`
    : `This changes a record of ${effect.who || "another student"}, not this follow-up’s student. Check before approving.`);
  else if (effect.own == null && coordinator) notes.push("It is a change the coordinator’s message calls for: check whom it affects.");
  else if (effect.own == null && /not limited to this follow-up’s student/.test(reason)) notes.push("It is not limited to this follow-up’s student: check whom it affects.");
  // A regrade decision that keeps the grade changes no certified work (REGRADES "FOLLOW-UP"; cards proposed before 2026-10-07 say so).
  const keeps = action.endpoint === "frq_regrade_panel.decide" && action.body?.decision === "keep";
  if (/grades were certified/.test(reason) && !keeps) notes.push("Its assignment’s grades were certified: approving changes certified work.");
  // ASK AFTER AN EDIT: the status change chosen for the draft waits for the instructor once they edited the reply.
  if (/You edited the reply/.test(reason)) notes.push("You edited the reply, so this no longer happens by itself. Check it still fits your version.");
  // ASK AFTER A CHANGED DECISION (2026-10-13): the same once the instructor chose another decision on the absence card.
  if (/You changed the decision/.test(reason)) notes.push("You changed the absence decision, so this no longer happens by itself. Check it still fits your decision.");
  // A DECIDED STEP IS NOT CARRIED (2026-11-04): the same, when the step it would have tracked is the decision they just chose.
  if (/You chose the decision it was waiting for/.test(reason)) notes.push("You decided this on the absence card, so it no longer asks you to decide it. Check nothing else is left.");
  return notes.map((note) => `<p class="assistant-card-note support-scope" role="note">${esc(note)}</p>`).join("") + (outside(action) ? whyMarkup(action) : "");
}

/** WIDE SCOPE (requirement, 2026-10-15): a card that changes another student's record says why on its face, with
 * the Assistant's note; other cards keep it under "Show the whole proposal". @param {any} action */
const outside = (action) => action.effect?.own === false && Boolean(action.summary);
/** @param {any} action */
const whyMarkup = (action) => `<p class="assistant-card-note support-why"><span>The Assistant’s note:</span> ${esc(action.summary)}</p>`;

/** ASSUME, DON'T ASK (requirement, 2026-10-04: when unsure, decide from the term policies and flag the guess): what the Assistant
 * assumed on a waiting card, with its alternatives one click away (each asks for changes, "Instead of …"). READABLE REVIEW: the
 * guess is a warning-ruled note above the card's buttons and each alternative a framed choice, not a line of small capitals.
 * @param {any} action */
export function assumptionMarkup(action) {
  const assumed = action.assumption;
  if (!action.pending || !assumed?.text) return "";
  const others = (assumed.alternatives || []).map((/** @type {string} */ other, /** @type {number} */ n) =>
    `<li><button class="support-choice" type="button" data-followup-instead="${esc(action.id)}" data-alternative="${n}">${esc(String(other).replace(/\.\s*$/, ""))}</button></li>`).join("");
  return `<div class="support-assumption" role="note"><p class="support-assumption-label">Assumed</p><p class="support-assumption-text">${esc(assumed.text)}</p>${others
    ? `<p class="support-assumption-lead">Not right? Prepare again instead with:</p><ul class="support-choices" role="list">${others}</ul>` : ""}</div>`;
}

/** DONE FOR YOU (requirement, 2026-10-04: the Assistant records the absence, attaches the student's files and puts agreed office
 * hours on the calendar by itself): what it recorded for this follow-up, each with Undo while the record is untouched, else a link
 * to its page. SAME MATTER: a conversation Jev joined to this follow-up has Keep separate (`separate`, its conversation key) for the
 * follow-up at `index`. @param {any} s @param {number} [index] */
export function doneMarkup(s, index = 0) {
  const done = s?.done || [];
  if (!done.length) return "";
  const button = (/** @type {any} */ d, /** @type {string} */ word) =>
    `<button class="text-button" type="button" data-followup-undo="${esc(d.id)}" data-run="${esc(d.run)}" data-seen="${esc(d.seen)}">${word}</button>`;
  const after = (/** @type {any} */ d) => d.separate ? `<button class="text-button" type="button" data-support-separate="${esc(index)}" data-key="${esc(d.separate)}">Keep separate</button>`
    : d.state === "undone" ? '<span class="chip" data-tone="neutral">Undone</span>'
    : d.state === "cancelled" ? '<span class="chip" data-tone="neutral">Not done</span>'
    : d.state === "failed" ? '<span class="chip" data-tone="danger">Not done</span>'
    : d.state === "scheduled" ? button(d, "Don’t") : d.undo ? button(d, "Undo")
    : d.href ? `<a class="link-arrow" href="${esc(d.href)}"${d.why ? ` title="${esc(d.why)}"` : ""}>Open its page</a>` : "";
  // A scheduled status change (the follow-up's own) runs after the reply is sent: said in the present, with Don't.
  const words = (/** @type {any} */ d) => d.state === "scheduled" ? `After you send the reply: <strong>${esc(d.plan || d.title)}</strong>`
    : `<strong>${esc(d.title)}</strong>${d.detail ? ` · ${esc(d.detail)}` : ""}`;
  return `<div class="support-done" data-patch-key="done"><h5 class="support-done-title">Done for you</h5><ul class="support-done-list" role="list">${done.map((/** @type {any} */ d) =>
    `<li data-patch-key="done-${esc(d.id)}"${["undone", "cancelled"].includes(d.state) ? ' data-undone="true"' : ""}><span>${words(d)}</span> ${after(d)}</li>`).join("")}</ul></div>`;
}

/** A field short enough for a step's summary: one line, old and new value alike (FOLLOW-UP CLARITY, requirement, 2026-10-04). @param {any} change */
const brief = (change) => [change.value, change.before].every((value) => value === undefined || (String(value).length <= 80 && !String(value).includes("\n")));
/** A field the summary shows whatever its length: one the card marks `show` (a regrade decision's reply, requirement, 2026-10-07), else a short one. @param {any} change */
const summary = (change) => Boolean(change.show) || brief(change);

/**
 * FOLLOW-UP CLARITY: one waiting step as a summary: what it does and where, for whom, its one-line fields, any other student or
 * certified work it touches, a calendar overlap and a button named for what it does. "Show the whole proposal" holds the rest:
 * long fields (details, notes), what follows and who is told, the Assistant's own note, the page's preview and the exact request.
 * `chat` is a step an Assistant chat prepared (REDO FROM CHAT): its decision is not chosen here (its edits belong to the chat).
 * @param {any} action @param {number} step @param {string} here
 */
function proposalMarkup(action, step, here, preparing = false, chat = false) {
  const effect = action.effect, preview = action.preview, changes = effect.changes || [];
  const choose = chat || preparing ? "" : action.id;  // EDIT IN PLACE waits while a preparation runs
  const meta = [effect.where ? `On ${effect.where}` : "", effect.who ? `For ${effect.who}` : ""].filter(Boolean).join(" · ");
  const follows = [...(effect.notes || []), effect.audience].filter(Boolean);
  const open = action.page && action.page !== here ? `<a class="link-arrow" href="${esc(action.page)}">Review on its page</a>`
    : effect.href && effect.href !== here ? `<a class="link-arrow" href="${esc(effect.href)}">Open ${esc(effect.where)}</a>` : "";
  return `<article class="assistant-card support-proposal" data-patch-key="action-${esc(action.id)}" data-action-id="${esc(action.id)}" data-seen="${esc(action.seen)}" data-endpoint="${esc(action.endpoint)}">
    <header class="assistant-card-head">${step ? stepMark(step) : ""}<strong>${esc(effect.title)}</strong></header>
    ${meta ? `<p class="support-proposal-meta">${esc(meta)}</p>` : ""}${scopeMarkup(action)}
    ${changesMarkup(changes.filter(summary), choose)}${decidedMarkup(changes)}
    ${calendarWarns(action.calendar) ? calendarMarkup(action.calendar) : ""}
    <details class="disclosure support-whole"${preview?.error ? " open" : ""}><summary><span>Show the whole proposal</span></summary><div class="disclosure-body">
      ${changesMarkup(changes.filter((change) => !summary(change)))}
      ${follows.length ? `<ul class="support-follows">${follows.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>` : ""}
      ${action.summary && !outside(action) ? whyMarkup(action) : ""}
      ${action.calendar && !calendarWarns(action.calendar) ? calendarMarkup(action.calendar) : ""}
      ${preview ? `<details class="disclosure"${preview.error ? " open" : ""}><summary><span>What the page’s preview says</span></summary><div class="disclosure-body">${preview.error ? `<p class="assistant-card-note">${esc(preview.error)}</p>` : valueMarkup(preview.data)}</div></details>` : ""}
      <details class="disclosure support-exact"><summary><span>Exact request</span></summary><div class="disclosure-body">
        <p class="assistant-card-note">${esc(action.method)} ${esc(action.path)}</p>${valueMarkup(action.body)}${action.confirmation ? `<p class="assistant-card-note">Approving also confirms it (“${esc(action.confirmation)}”).</p>` : ""}</div></details>
    </div></details>${assumptionMarkup(action)}
    <div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}">${esc(effect.button || "Approve")}</button>${action.endpoint === DECIDE && !preparing ? `<button class="text-button" type="button" data-followup-edit="${esc(action.id)}">Edit grade or reply</button>` : ""}<button class="text-button" type="button" data-assistant-decline="${esc(action.id)}">Decline</button>${open}</div>
  </article>`;
}

/** REGRADES "FOLLOW-UP": a regrade request's decide card (keep or change the grade and answer, carried everywhere by Apply). */
const DECIDE = "frq_regrade_panel.decide";

/** REGRADES "APPROVE ALL": a follow-up that is a Gradescope regrade request's conversation. Pure. @param {any} item */
export const isRegrade = (item) => String(item?.source_key || "").startsWith("gradescope_regrade:");

/**
 * REGRADES "APPROVE ALL" (requirement, 2026-10-29): the waiting decide cards of these regrade follow-ups, keeps first (a keep changes no grade, so it never
 * outdates another card of the same exam; a change does until its Apply has certified it), then in the list's order. A card open
 * for editing, already being approved, or in a preparation that is not ready is left out. Pure.
 * @param {any[]} items @returns {{item: any, action: any}[]}
 */
export function regradeApprovals(items) {
  const cards = items.flatMap((item) => {
    const s = item?.suggestions;
    if (!isRegrade(item) || !s || !["ready", "needs_input"].includes(s.state)) return [];
    return (s.actions || []).filter((/** @type {any} */ a) => a.pending && a.endpoint === DECIDE && !editing.has(a.id)).map((/** @type {any} */ action) => ({ item, action }));
  });
  const keep = (/** @type {{action: any}} */ c) => c.action.body?.decision === "keep";
  return [...cards.filter(keep), ...cards.filter((c) => !keep(c))];
}

/** EDIT THE GRADE (requirement, 2026-10-08): the answer a decide card's edit form grades (its
 * question's rubric and its grade now), by action ID, read when Edit opens: `{}` while reading, then `{value}` or `{error}`. */
const decideResponses = /** @type {Map<string, {value?: any, error?: string}>} */ (new Map());

/** Read the answer a decide card grades for its edit form. @param {any} action @param {SuggestionContext} ctx */
async function readDecideResponse(action, ctx) {
  const rid = action.preview?.data?.response_id;
  decideResponses.set(action.id, {});
  try {
    if (!rid) throw new Error("This card does not say which answer it grades.");
    decideResponses.set(action.id, { value: await ctx.api(`/api/grading/responses/${encodeURIComponent(rid)}`) });
  } catch (cause) {
    decideResponses.set(action.id, { error: errorMessage(cause) });
  }
}

/** A rubric line's words without math delimiters (the card shows no typeset math). @param {unknown} text */
const plainMath = (text) => String(text ?? "").replace(/\$\$?|\\[()[\]]/g, "").replace(/\s+/g, " ").trim();
/** Points as a signed figure: "+1", "−0.5", "0". @param {unknown} points */
const signed = (points) => { const n = Number(points); return !Number.isFinite(n) ? String(points ?? "") : n > 0 ? `+${n}` : n < 0 ? `−${-n}` : "0"; };

/**
 * What a decide card's edit form starts from: the question's rubric lines, the final grade's lines and point adjustments now (the
 * card's preview says them; a card proposed before 2026-10-08 reads them from the answer), and what the card proposes (its lines
 * and adjustment for a change; the grade now for keep). Null while the answer is read or when the grade cannot be known.
 * @param {any} action @param {any} value
 */
export function decideGrade(action, value) {
  const body = action.body || {}, shown = action.preview?.data || {};
  const items = value?.question?.rubric?.rules?.items;
  const chosen = value && !value.hidden ? value.review || value.result : null;
  const lines = Array.isArray(shown.current?.lines) ? shown.current.lines : chosen?.score?.selected;
  if (!Array.isArray(items) || !items.length || !Array.isArray(lines)) return null;
  const kept = Array.isArray(shown.current?.adjustments) ? shown.current.adjustments : chosen?.score?.adjustments || [];
  const change = body.decision === "change";
  return { items, current: [...lines].sort(), kept, ticked: new Set(change && Array.isArray(body.lines) ? body.lines : lines),
    adjustment: change && body.adjustment && typeof body.adjustment === "object" ? body.adjustment : null };
}

/** A waiting decide card open for editing: the rubric lines its grade should have (ticked as it proposes), a point adjustment
 * when no line fits, and the reply, all saved on the card; nothing changes until Apply. While the answer is read, or when it
 * cannot be, the reply alone. @param {any} action @param {number} step @param {{value?: any, error?: string}} [read] the answer read */
export function decideEditMarkup(action, step, read = decideResponses.get(action.id) || {}) {
  const shown = action.preview?.data || {};
  if (shown.kind === "mc") return sheetEditMarkup(action, step);
  const grade = read.value ? decideGrade(action, read.value) : null;
  const now = shown.from != null && shown.of != null ? `Now ${shown.from} / ${shown.of}` : "";
  const lines = grade ? `<fieldset class="support-decide-lines"><legend>Rubric lines${now ? ` <span class="support-decide-now">${esc(now)}</span>` : ""}</legend>
      <ul class="support-decide-list" role="list">${grade.items.map((/** @type {any} */ item, /** @type {number} */ i) => `<li><label class="checkbox-field"><input type="checkbox" name="line" value="${esc(item.id)}"${grade.ticked.has(item.id) ? " checked" : ""}>
        <span class="support-decide-points">${esc(signed(item.points))}</span> <span>${i + 1}. ${esc(plainMath(item.description))}</span></label></li>`).join("")}</ul>
      ${grade.kept.length ? `<p class="field-hint">A changed grade keeps ${grade.kept.length === 1 ? "its point adjustment" : "its point adjustments"}: ${esc(grade.kept.map((/** @type {any} */ a) => `${signed(a.points)} ${a.reason || ""}`.trim()).join("; "))}.</p>` : ""}
    </fieldset>
    <details class="disclosure support-decide-adjust"${grade.adjustment ? " open" : ""}><summary><span>Add a point adjustment</span></summary><div class="disclosure-body">
      <div class="support-decide-adjust-fields"><label class="field">Points<input name="adjust_points" type="number" step="any" value="${esc(grade.adjustment?.points ?? "")}"></label>
      <label class="field">Why<input name="adjust_reason" maxlength="500" value="${esc(grade.adjustment?.reason ?? "")}"></label></div>
      <p class="field-hint">Only when no rubric line fits.</p></div></details>`
    : read.error ? `<p class="assistant-card-note" role="alert">The rubric could not be read: ${esc(read.error)} You can still edit the reply.</p>`
    : read.value ? '<p class="assistant-card-note">This grade cannot be changed here (its rubric or grade is not shown). Change it on Grading; you can still edit the reply.</p>'
    : '<p class="supporting-copy" role="status">Reading the rubric…</p>';
  return `<form class="assistant-card support-mail-edit support-decide-edit" data-patch-key="action-${esc(action.id)}" data-action-id="${esc(action.id)}" data-seen="${esc(action.seen)}" data-followup-edit-form="${esc(action.id)}"${grade ? ` data-decide-current="${esc(grade.current.join(" "))}"` : ""}>
    <header class="assistant-card-head">${step ? stepMark(step) : ""}<strong>Edit the regrade decision</strong></header>
    <input type="hidden" name="subject" value="">
    ${lines}
    <label class="field">Reply<textarea name="body" rows="8" maxlength="5000" required>${esc(action.body?.reply ?? "")}</textarea></label>
    <p class="field-hint">Save puts your ${grade ? "grade and reply" : "reply"} on this card. Nothing changes until you press Apply.</p>
    <p class="form-error" role="alert" hidden></p>
    <div class="assistant-card-actions"><button class="button primary" type="submit">Save changes</button><button class="text-button" type="button" data-followup-edit-cancel="${esc(action.id)}">Cancel</button></div>
  </form>`;
}

/** A bubble-sheet question's decide card open for editing (REGRADES "BUBBLE SHEETS"): the question's row of the scan, the letters
 * the student filled (ticked as the card proposes: its answer for a change, Faculty's reading for keep), and the reply.
 * @param {any} action @param {number} step */
export function sheetEditMarkup(action, step) {
  const shown = action.preview?.data || {}, body = action.body || {};
  const read = String(shown.read ?? ""), ticked = body.decision === "change" && typeof body.answer === "string" ? body.answer : read;
  const now = shown.from != null && shown.of != null ? `Now ${shown.from} / ${shown.of}` : "";
  return `<form class="assistant-card support-mail-edit support-decide-edit" data-patch-key="action-${esc(action.id)}" data-action-id="${esc(action.id)}" data-seen="${esc(action.seen)}" data-followup-edit-form="${esc(action.id)}" data-decide-read="${esc(read)}">
    <header class="assistant-card-head">${step ? stepMark(step) : ""}<strong>Edit the regrade decision</strong></header>
    <input type="hidden" name="subject" value="">
    ${shown.picture ? `<img class="regrade-sheet-row" src="${esc(shown.picture)}" alt="Question ${esc(shown.number ?? "")} on the student's bubble sheet">` : ""}
    <fieldset class="support-decide-lines"><legend>Question ${esc(shown.number ?? "")}: the bubbles they filled${now ? ` <span class="support-decide-now">${esc(now)}</span>` : ""}</legend>
      <ul class="support-decide-list support-decide-letters" role="list">${"ABCDE".split("").map(letter => `<li><label class="checkbox-field"><input type="checkbox" name="letter" value="${letter}"${ticked.includes(letter) ? " checked" : ""}> <span>${letter}</span></label></li>`).join("")}</ul>
      <p class="field-hint">Faculty read ${esc(read ? read.split("").join(" and ") : "a blank")}${shown.key ? `; your key: ${esc(shown.key)}` : ""}. None ticked is a blank.</p>
    </fieldset>
    <label class="field">Reply<textarea name="body" rows="8" maxlength="5000" required>${esc(body.reply ?? "")}</textarea></label>
    <p class="field-hint">Save puts your reading and reply on this card. Nothing changes until you press Apply.</p>
    <p class="form-error" role="alert" hidden></p>
    <div class="assistant-card-actions"><button class="button primary" type="submit">Save changes</button><button class="text-button" type="button" data-followup-edit-cancel="${esc(action.id)}">Cancel</button></div>
  </form>`;
}

/** The reading a bubble-sheet decide card's form asks for: keep when the letters ticked are Faculty's reading, else a change naming
 * them. @param {string} read @param {string[]} ticked */
export function readingChoice(read, ticked) {
  const answer = [...new Set(ticked)].sort().join("");
  return answer === [...read].sort().join("") ? { decision: "keep" } : { decision: "change", answer };
}

/** The grade a decide card's edit form asks for: keep when its lines are the grade's now and no adjustment is added, else a change
 * naming every line ticked (and the adjustment); null when the form shows no rubric (the reply alone).
 * A bubble-sheet card's form asks for its reading (`readingChoice`).
 * @param {HTMLFormElement} form @returns {{decision: "keep"} | {decision: "change", lines?: string[], answer?: string, adjustment?: {points: number, reason: string}} | null} */
function decideChoice(form) {
  if (form.dataset.decideRead !== undefined) return readingChoice(form.dataset.decideRead, new FormData(form).getAll("letter").map(String));
  if (form.dataset.decideCurrent === undefined) return null;
  const fields = new FormData(form);
  return gradeChoice(form.dataset.decideCurrent.split(" "), fields.getAll("line").map(String), fields.get("adjust_points"), fields.get("adjust_reason"));
}

/** `decideChoice` from the grade's lines now, the lines ticked and the adjustment's two fields as typed.
 * @param {string[]} current @param {string[]} ticked @param {unknown} pointsField @param {unknown} reasonField */
export function gradeChoice(current, ticked, pointsField, reasonField) {
  const lines = [...ticked].sort(), now = current.filter(Boolean).sort();
  const points = String(pointsField ?? "").trim(), reason = String(reasonField ?? "").trim();
  if ((points || reason) && (!points || !reason || !Number(points))) throw new Error("A point adjustment needs points other than 0 and why.");
  const adjustment = points ? { points: Number(points), reason } : null;
  if (!adjustment && lines.join(" ") === now.join(" ")) return { decision: "keep" };
  return { decision: "change", lines, ...(adjustment ? { adjustment } : {}) };
}

/** EDIT IN PLACE (requirement, 2026-10-04): the waiting mail cards open for editing on their card, by action ID, in this browser
 * (both review locations), and the request each Save sends (a retry of the same text reuses its ID). */
const editing = /** @type {Set<string>} */ (new Set());
const editRequests = /** @type {Map<string, {content: string, id: string}>} */ (new Map());

/** A waiting mail card open for editing: its subject and text, saved on the card; nothing is sent from here.
 * @param {any} action @param {number} step */
function editMarkup(action, step) {
  const body = action.body || {}, message = action.preview?.data?.message || {};
  const list = (/** @type {unknown} */ value) => Array.isArray(value) ? value.join(", ") : String(value || "");
  const to = list(message.to ?? body.to);
  return `<form class="assistant-card support-mail-edit" data-patch-key="action-${esc(action.id)}" data-action-id="${esc(action.id)}" data-seen="${esc(action.seen)}" data-followup-edit-form="${esc(action.id)}">
    <header class="assistant-card-head">${step ? stepMark(step) : ""}<strong>Edit the ${body.reply_thread_id ? "reply" : "email"}</strong></header>
    ${to ? `<dl class="assistant-fields"><dt>To</dt><dd>${esc(to)}</dd></dl>` : ""}
    <label class="field">Subject<input name="subject" maxlength="900" value="${esc(body.subject ?? message.subject ?? "")}"></label>
    <label class="field">Message<textarea name="body" rows="12" maxlength="20000" required>${esc(body.body ?? message.text ?? "")}</textarea></label>
    <p class="field-hint">Save puts your version on this card in place of the draft. Nothing is sent until you press Send. Recipients and attachments stay as they are; Edit in Mail changes those, and sends from Mail’s own window.</p>
    <p class="form-error" role="alert" hidden></p>
    <div class="assistant-card-actions"><button class="button primary" type="submit">Save changes</button><button class="text-button" type="button" data-followup-edit-cancel="${esc(action.id)}">Cancel</button><button class="text-button" type="button" data-followup-edit-mail="${esc(action.id)}">Edit in Mail…</button></div>
  </form>`;
}

/** BEFORE YOU SEND (2026-10-09 review): a draft-check warning left after the one repair, on the mail card it concerns, while that
 * card waits. An edited card is a new card, so the warning about the old text leaves with it. @param {any[]} warnings */
function beforeSendMarkup(warnings) {
  return warnings.map((/** @type {any} */ w) => `<div class="callout support-before-send" data-tone="warning" role="note"><strong class="callout-title">Before you send</strong><p>${esc(w.text)}</p></div>`).join("");
}

/** A card as the review shows it: a waiting step in plain words, a mail card open for editing, a Mail replacement, a withdrawn
 * proposal, and the instructor's note on one they declined. `review` is the run's review: its draft-check warnings, and whether it
 * is still being prepared (EDIT DURING A RUN: no Edit then; the draft check at the end of the turn would withdraw the edit).
 * @param {any} action @param {boolean} stale @param {string} here @param {number} step the waiting card's place in the plan, or 0
 * @param {any} [review] */
function reviewCardMarkup(action, stale, here, step = 0, review = {}) {
  const current = stale && action.pending ? { ...action, pending: false, status: "expired" }
    : action.replaced ? { ...action, status: "replaced" } : action.withdrawn && !action.pending ? { ...action, status: "withdrawn" } : action;
  const preparing = review.state === "running";
  const mail = current.pending && current.endpoint === "gmail_mail.send";
  if (mail && editing.has(current.id) && !preparing) return editMarkup(current, step);
  if (current.pending && current.endpoint === DECIDE && editing.has(current.id) && !preparing) return decideEditMarkup(current, step);
  if (current.pending && step && current.effect && !mail && current.endpoint !== "assistant.code_task_start") return proposalMarkup(current, step, here, preparing);
  const warnings = mail ? (review.draft_review?.warnings || []).filter((/** @type {any} */ w) => w.action_id === current.id) : [];
  // ASSISTANT: remove only the repeated approval boilerplate; wider-scope and certified-work reasons stay visible.
  const shown = {...current, reason: String(current.reason || "").replace(/^Every follow-up suggestion waits for your approval\.\s*/, "")};
  const card = cardMarkup(shown, { here, compact: true, heading: mail ? current.effect?.title || (shown.body?.reply_thread_id ? "Reply draft" : "Email draft") : "",
    editMail: mail && !preparing, mark: current.pending && step ? stepMark(step) : "", audience: mail ? current.effect?.audience || "" : "",
    extra: shown.pending ? `${beforeSendMarkup(warnings)}${action.edited ? '<p class="assistant-card-note">Your edited version.</p>' : ""}${calendarMarkup(action.calendar)}${assumptionMarkup(action)}` : "" });
  if (!action.declined_by_you || stale) return card;
  if (action.note) return `${card}<p class="assistant-card-note">Your note: ${esc(action.note)}</p>`;
  return `${card}<details class="disclosure support-note" data-patch-key="note-${esc(action.id)}"><summary><span>Tell the Assistant why</span></summary>
    <form class="disclosure-body" data-followup-note="${esc(action.id)}"><label class="field">Why did you decline this?<textarea name="text" maxlength="2000" rows="2" required></textarea></label>
    <label class="checkbox-field"><input type="checkbox" name="remember"> Remember this for future drafts</label>
    <p class="field-hint">Later suggestions for this conversation read your note.</p><p class="form-error" role="alert" hidden></p>
    <button class="button" type="submit">Save note</button></form></details>`;
}

/** What became of a card that no longer waits, in its chip's words: done (a Mail replacement whose send was accepted counts), declined,
 * expired or withdrawn; "" for one that still needs a look (failed, a send not confirmed, approved and still submitting).
 * @param {any} action @param {boolean} stale */
function finishedAs(action, stale) {
  if (action.pending) return stale ? "expired" : "";
  if (action.replaced) return action.replaced.status === "accepted" ? "done" : "";
  if (action.withdrawn) return "withdrawn";
  // A step the instructor cancelled before anything was done is decided, as a declined one is; one still running is not.
  if (action.outcome) return action.outcome === "succeeded" ? "done" : action.outcome === "cancelled" ? "cancelled" : "";
  if (action.status === "succeeded") return resultConfirmed(action) ? "done" : "";
  return ["declined", "expired", "withdrawn"].includes(action.status) ? action.status : "";
}

/** FINISHED STEPS (requirement, 2026-10-04): once nothing waits for the instructor, the cards already decided fold into one line ("4 steps done · 1 declined"), each card,
 * a declined card's "Tell the Assistant why" included, one click away. Nothing is removed; a card that still needs a look stays out.
 * @param {string[]} outcomes @param {string} cards @param {string} key @param {(value: unknown) => string} e */
function finishedMarkup(outcomes, cards, key, e) {
  if (!outcomes.length) return "";
  const counts = ["done", "declined", "expired", "withdrawn", "cancelled"].map((word) => /** @type {[string, number]} */ ([word, outcomes.filter((o) => o === word).length]))
    .filter(([, n]) => n);
  const line = counts.map(([word, n], i) => i ? `${n} ${word}` : `${n} ${n === 1 ? "step" : "steps"} ${word}`).join(" · ");
  return `<details class="disclosure support-finished" data-patch-key="finished-${e(key)}"><summary><span>${e(line)}</span></summary><div class="disclosure-body">${cards}</div></details>`;
}

/** FOLLOW-UP CLARITY: the steps in order before their cards, and that nothing happens until the instructor's own click. A round
 * that follows through on steps already approved says so (requirement, 2026-10-04). READABLE REVIEW: the count leads in bold and the reassurance is one short line.
 * APPROVING: steps sent from this browser and not yet back are counted on their own line, never as waiting.
 * @param {any[]} pending @param {(value: unknown) => string} e @param {string} [reason] @param {number} [going] */
function planMarkup(pending, e, reason = "", going = 0) {
  const sent = going ? `<p class="support-plan-lead" role="status"><strong>${e(going === 1 ? "Approving one step…" : `Approving ${going} steps…`)}</strong> ${
    e(going === 1 ? "This page updates when it finishes." : "They go in order. This page updates when they finish.")}</p>` : "";
  if (!pending.length) return sent ? `<div class="support-plan">${sent}</div>` : "";
  const where = (/** @type {any} */ a) => a.endpoint === "gmail_mail.send" || a.effect?.sends ? "sends a message"
    : a.endpoint === "assistant.code_task_start" ? "starts a coding agent" : a.effect?.where ? `on ${a.effect.where}` : "";
  const count = pending.length === 1 ? "One step waits for you." : `${pending.length} steps wait for you.`;
  const lead = `${reason === "receipts" ? `${e("Follow-through on the steps you approved: work that needed their results.")} ` : ""}<strong>${e(count)}</strong> ${
    e(pending.length === 1 ? "Nothing happens until you approve it." : "Nothing happens until you approve them.")}`;
  return `<div class="support-plan">${sent}<p class="support-plan-lead">${lead}</p>${pending.length > 1 ? `<ol class="support-plan-steps">${pending.map((/** @type {any} */ a) => {
    const place = where(a);
    return `<li><strong>${e(proposalLabel(a))}</strong>${place ? ` <span>· ${e(place)}</span>` : ""}</li>`;
  }).join("")}</ol>` : ""}</div>`;
}

/** REDO FROM CHAT (2026-10-09, requirement, of Jared Zane's follow-up: the chat's redone reply waited only in the chat while this page
 * said "these proposals no longer apply"): the steps an Assistant chat prepared for this conversation that still wait, with the same
 * Approve and Decline as in Changes (a mail card is sent from here as it is; its edits belong to the chat). "" when there are none.
 * @param {any} s @param {string} here @param {(value: unknown) => string} e */
function chatStepsMarkup(s, here, e) {
  const chat = (s?.chat || []).filter((/** @type {any} */ a) => a.pending || a.status === "approving");
  if (!chat.length) return "";
  const lead = chat.length === 1 ? "One step from your Assistant chat waits for you." : `${chat.length} steps from your Assistant chat wait for you.`;
  const card = (/** @type {any} */ a) => !a.pending ? cardMarkup(a, { here, compact: true })
    : a.endpoint === "gmail_mail.send" ? cardMarkup({ ...a, reason: "" }, { here, compact: true, heading: a.effect?.title || (a.body?.reply_thread_id ? "Reply draft" : "Email draft"), audience: a.effect?.audience || "" })
    : a.effect ? proposalMarkup(a, 0, here, false, true) : cardMarkup(a, { here, compact: true });
  return `<div class="support-chat-steps" data-patch-key="chat-steps"><p class="support-plan-lead"><strong>${e(lead)}</strong> ${
    e(chat.length === 1 ? "It is for this conversation. Nothing happens until you approve it." : "They are for this conversation. Nothing happens until you approve them.")}</p>${chat.map(card).join("")}</div>`;
}

/**
 * The Suggested actions section of one follow-up. `index` is the item's place in the caller's own list (`data-followup-item`).
 * @param {any} item @param {number} index @param {(value: unknown) => string} [e] @param {{prefix?: string, here?: string}} [options]
 */
/**
 * APPROVE ALL: the one control that takes every waiting step in order, with its hint, or while a draft is open the hint to save or
 * cancel it first; "" when there are fewer than two steps to take. Student follow-up leads its review with it (DESIGN-LANGUAGE §1.4,
 * Ledger 2026-10-04: the decision and its action first; `suggestionsMarkup(..., {lead: true})` then leaves it out). Pure.
 * @param {any} item
 */
export function approveAllMarkup(item) {
  const s = item?.suggestions;
  if (!s || s.state === "idle" || s.state === "superseded") return "";
  const pending = (s.actions || []).filter((/** @type {any} */ a) => a.pending);
  const mails = pending.filter((/** @type {any} */ a) => a.endpoint === "gmail_mail.send").length;
  const open = pending.some((/** @type {any} */ a) => editing.has(a.id));
  if (pending.length >= 2 && ["ready", "needs_input"].includes(s.state) && !open) {
    return `<button class="button primary" type="button" data-followup-approve-all>${mails ? `Send and approve all ${pending.length}` : `Approve all ${pending.length}`}</button>`
      + '<p class="field-hint support-all-hint">Approve all takes the steps in order and stops at the first problem.</p>';
  }
  return open && pending.length >= 2 ? '<p class="field-hint support-all-hint">Save or cancel your edit to approve all.</p>' : "";
}

/**
 * The plan of the steps that wait ("2 steps wait for you. Nothing happens until you approve them." and the numbered steps), for a
 * page that leads with the decision (Student follow-up's orange box; `suggestionsMarkup(..., {lead: true})` then leaves it out).
 * Pure. @param {any} item @param {(value: unknown) => string} [e]
 */
export function decisionPlanMarkup(item, e = esc) {
  const s = item?.suggestions;
  if (!s || s.state === "idle" || s.state === "superseded") return "";
  return planMarkup((s.actions || []).filter((/** @type {any} */ a) => a.pending), e, s.reason, approvingCount(s));
}

export function suggestionsMarkup(item, index, e = esc, { prefix = "", here = "", lead = false, desk = false } = {}) {
  const s = item?.suggestions;
  if (!s) return "";
  if (s.state === "idle") {
    const watches = watchesMarkup(s, e), chat = chatStepsMarkup(s, here, e);
    return chat ? `<section class="support-suggestions" data-patch-key="suggestions-item-${index}" data-followup-item="${index}">
      <div class="support-suggestion-head"><h4>Suggested actions</h4></div>${chat}${watches}</section>`
      : watches ? `<section data-followup-item="${index}">${watches}</section>` : "";
  }
  const stale = s.state === "superseded";
  const actions = s.actions || [];
  const pending = stale ? [] : actions.filter((/** @type {any} */ a) => a.pending);
  const mails = pending.filter((/** @type {any} */ a) => a.endpoint === "gmail_mail.send").length;
  const going = stale ? 0 : approvingCount(s);
  const label = going ? "Approving…" : desk && s.state === "settled" ? "Assistant review complete" : s.muted && !s.id ? SUGGESTION_LABELS.muted : SUGGESTION_LABELS[s.state] || s.state;
  const queued = s.state === "queued" ? queueText(s.queue) : "";
  const prepare = s.preparable && (["paused", "analyzing"].includes(s.state) || s.state === "queued" && (!s.queue || s.queue.held || s.queue.position !== 1));
  // STALE SUGGESTIONS (usability audit, 2026-10-04: a conversation whose proposals no longer applied offered no way forward): when
  // nothing will prepare them again by itself (muted, paused, a To do follow-up, which is prepared only on request), ask again here.
  const again = stale && s.preparable && !s.renew;
  // JEV / TEACHING-WORKFLOWS: keep the advisory visible; present each observed source once, with the full checks on demand.
  const verification = s.verification || [];
  // EVIDENCE WARNING (requirement, 2026-10-05): only a confident contradiction (Jev keeps "contradicted" at .8 or more) raises it; uncertain and unsupported
  // verdicts and omissions stay in Why the Assistant suggests this.
  const reviewNeeded = verification.some(v => (v.checks || []).some(c => c.verdict === "contradicted"));
  const sources = [...new Map([...(s.evidence || []), ...verification.flatMap(v => v.sources || [])]
    .map(source => [JSON.stringify([source.kind, source.path, source.fingerprint, source.observed_at]), source])).values()];
  const evidence = (s.explanation && !stale ? `<div class="assistant-msg">${markdown(s.explanation)}</div>` : "")
    + verification.map(v => verificationMarkup(v, {sources: false})).join("") + evidenceMarkup(sources);
  const open = pending.some((/** @type {any} */ a) => editing.has(a.id));
  const all = pending.length >= 2 && ["ready", "needs_input"].includes(s.state) && !open;
  const controls = [
    all && !lead ? `<button class="button primary" type="button" data-followup-approve-all>${mails ? `Send and approve all ${pending.length}` : `Approve all ${pending.length}`}</button>` : "",
    prepare ? '<button class="button" type="button" data-followup-prepare>Prepare now</button>' : "",
    again ? '<button class="button" type="button" data-followup-prepare>Suggest again for the conversation as it is now</button>' : "",
    // TERM POLICIES: older preparations may have no policy revision; explicit Retry still applies the latest guidance.
    s.id && ["ready", "needs_input", "failed", "interrupted", "settled"].includes(s.state)
      ? `<button class="button" type="button" data-followup-retry title="Prepare again with your latest saved policies. Unapproved suggestions are replaced; completed actions are kept.">${icon("refresh")}Retry suggestions</button>` : "",
  ].filter(Boolean).join("");
  const mute = s.state !== "unavailable" ? (s.muted ? '<button class="text-button" type="button" data-followup-mute="off">Turn suggestions back on</button>'
    : '<button class="text-button" type="button" data-followup-mute="on">Don’t suggest for this conversation</button>') : "";
  // FOLLOW-UP CLARITY: an open question comes before the steps it may change; answering replaces the steps not yet approved.
  const question = !stale && s.question ? `<div class="support-ask">${pending.length ? `<p class="support-ask-lead">Answer this first. The Assistant prepares again with your answer, replacing the ${pending.length === 1 ? "step" : "steps"} below that you have not approved.</p>` : ""}${questionMarkup(s.question, prefix)}</div>` : "";
  const steps = new Map(pending.map((/** @type {any} */ a, /** @type {number} */ n) => [a.id, n + 1]));
  // FINISHED STEPS: with nothing left to approve or answer, the decided cards fold away and the evidence warning (a reason to look
  // before approving) goes with them.
  const quiet = !pending.length && !question;
  const outcomes = actions.map((/** @type {any} */ a) => quiet || desk ? finishedAs(a, stale) : "");
  const completed = () => desk && doneMarkup(s, index) ? `<details class="disclosure support-completed"><summary><span>Done for you · receipts and Undo</span></summary><div class="disclosure-body">${doneMarkup(s, index)}</div></details>` : doneMarkup(s, index);
  const cards = (/** @type {boolean} */ folded) => actions.filter((/** @type {any} */ _, /** @type {number} */ n) => Boolean(outcomes[n]) === folded)
    .map((/** @type {any} */ a) => reviewCardMarkup(a, stale, here, steps.get(a.id) || 0, s)).join("");
  return `<section class="support-suggestions" data-patch-key="suggestions-${e(s.id || `item-${index}`)}" data-followup-item="${index}">
    <div class="support-suggestion-head"><h4>Suggested actions</h4><p class="field-hint" role="status">${e(label)}</p></div>
    ${queued ? `<p class="field-hint">${e(queued)}</p>` : ""}${s.state === "analyzing" ? '<p class="field-hint">Waiting up to 10 minutes for Jev to read the message before joining the preparation queue.</p>' : ""}${s.note ? `<p class="field-hint">${e(s.note)}</p>` : ""}
    ${s.unavailable ? `<p class="field-hint">${e(s.unavailable)} <a class="link-arrow" href="#grading?settings=1">Compute settings</a></p>` : ""}
    ${s.muted && s.id ? `<p class="field-hint">${e(SUGGESTION_LABELS.muted)}. Waiting cards stay until you act on them.</p>` : ""}
    ${s.error ? `<p class="field-hint">${e(s.error)}</p>` : ""}
    ${needsAttention(s) ? '<div class="callout" data-tone="warning"><strong class="callout-title">Some work still needs attention</strong><p>Review the failed or unconfirmed step below. Nothing is resent automatically.</p></div>' : ""}
    ${stale && s.renew ? '<p class="field-hint">New suggestions will be prepared for the conversation as it is now.</p>' : ""}
    ${sinceDraftMarkup(s, stale, e)}
    ${!stale && !quiet && s.draft_review?.status === 'unavailable' ? '<p class="field-hint">The reply check was unavailable. Review the wording before sending.</p>' : ''}
    ${!stale && !quiet && s.draft_review?.status === 'partial' ? '<p class="field-hint">The reply check covered part of the available context. Review the full request before sending.</p>' : ''}
    ${s.policies_changed && !stale && !s.context_changes ? '<p class="field-hint">Your term policies changed after these were prepared. Retry suggestions or ask for changes to apply them.</p>' : ""}
    ${s.context_unverified && !stale ? '<p class="field-hint">This draft predates source checks. Retry suggestions once to review it against current records before approving.</p>' : ""}
    ${chatStepsMarkup(s, here, e)}
    ${quiet ? completed() : ""}${question}${lead || desk ? "" : planMarkup(pending, e, s.reason, going)}
    ${reviewNeeded && !quiet ? `<div class="callout support-evidence-warning" data-tone="warning" role="note"><strong class="callout-title">Evidence needs review</strong>
      <p>Something the Assistant wrote contradicts what it read.</p>${evidence ? '<button class="text-button" type="button" data-followup-why>See why the Assistant suggests this</button>' : ""}</div>` : ""}
    ${cards(false)}${finishedMarkup(outcomes.filter(Boolean), cards(true), s.id || `item-${index}`, e)}
    ${controls ? `<div class="support-actions">${controls}</div>` : ""}${lead ? "" : all ? '<p class="field-hint support-all-hint">Approve all takes the steps in order and stops at the first problem.</p>' : open && pending.length >= 2 ? '<p class="field-hint support-all-hint">Save or cancel your edit to approve all.</p>' : ""}
    ${quiet ? "" : completed()}
    ${learnedMarkup(s, e)}
    ${followupRevisionMarkup(s, "data-followup-revise")}
    ${evidence ? `<details class="disclosure support-evidence" data-patch-key="evidence-${e(s.id || index)}"><summary><span>Why the Assistant suggests this</span></summary><div class="disclosure-body">${evidence}</div></details>` : ""}
    ${mute ? `<details class="disclosure support-suggestion-options" data-patch-key="options-${e(s.id || index)}"><summary><span>Suggestion settings</span></summary><div class="disclosure-body">${mute}</div></details>` : ""}
    ${watchesMarkup(s, e)}</section>`;
}

/**
 * SAID WHERE IT HAPPENED (ASSISTANT-MEMORY "LEARNED FROM AN EDIT"): what the Assistant remembered from the instructor's corrections of
 * this follow-up's cards, one line each, drawn as Done for you is: Undo and Change (its own form in Memory) while it stands as it
 * was kept, "Taken back" once undone. Pure. @param {any} s @param {(value: unknown) => string} e
 */
export function learnedMarkup(s, e = esc) {
  const learned = Array.isArray(s?.learned) ? s.learned.filter((/** @type {any} */ l) => l?.memory?.id) : [];
  if (!learned.length) return "";
  const line = (/** @type {any} */ l) => {
    const words = `From ${e(LEARNED_FROM[l.kind] || LEARNED_FROM.edit)}: “${e(String(l.memory.content || "").replace(/\s+/g, " ").trim())}”`;
    const after = l.memory.standing
      ? `<span><button class="text-button" type="button" data-followup-forget="${e(l.memory.id)}">Undo</button> · <a class="text-button" href="#memory?memory=${encodeURIComponent(l.memory.id)}">Change</a></span>`
      : '<span class="chip" data-tone="neutral">Taken back</span>';
    return `<li data-patch-key="learned-${e(l.case)}"${l.memory.standing ? "" : ' data-undone="true"'}><span>${words}${l.replaced && l.memory.standing ? ` It replaces “${e(String(l.replaced).replace(/\s+/g, " ").trim())}”.` : ""}</span> ${after}</li>`;
  };
  return `<div class="support-done" data-patch-key="learned-${e(s.id || "")}"><h5 class="support-done-title">Remembered for later drafts</h5><ul class="support-done-list" role="list">${learned.map(line).join("")}</ul></div>`;
}

/** What "Remember this" kept, said as a toast. @param {any} ctx @param {any} result */
function rememberedToast(ctx, result) {
  const notice = rememberedNotice(result, ctx.api, ctx.toast);
  if (notice) ctx.toast(...notice);
}

/**
 * `redraw` draws the page again from the read it holds, after `withApproving` (APPROVING).
 * @typedef {{api: Function, toast: Function, item: (index: number) => any, refresh: () => Promise<void> | void,
 *   editMail?: (prefill: any) => void, redraw?: () => void, showDialog?: Function, dialogHeader?: Function}} SuggestionContext
 */

/** Edit in Mail: the instructor's own copy of the draft in Mail's compose window; their Send there replaces the card.
 * @param {any} item @param {any} action @param {SuggestionContext} ctx */
function editInMail(item, action, ctx) {
  if (!ctx.editMail) { ctx.toast("Mail is not available here.", true); return; }
  if (editing.delete(action.id)) void ctx.refresh();
  const s = item.suggestions, body = action.body || {}, message = action.preview?.data?.message || {};
  const list = (/** @type {unknown} */ value) => Array.isArray(value) ? value.join(", ") : String(value || "");
  ctx.editMail({
    to: list(body.to), cc: list(body.cc), bcc: list(body.bcc), subject: message.subject ?? body.subject ?? "", body: message.text ?? body.body ?? "",
    replyThreadId: body.reply_thread_id || "",
    attachments: (message.attachments || body.attachments || []).filter((/** @type {any} */ f) => f && f.route).map((/** @type {any} */ f) => ({ route: f.route, name: f.name || "Attachment", size: f.size })),
    context: `Your edit of the Assistant’s draft to ${item.student?.name || "the student"}`,
    onSent: async (/** @type {any} */ result, /** @type {string} */ requestId) => {
      try {
        await ctx.api(`${BASE}/${encodeURIComponent(s.id)}/replace`, "POST", { action_id: action.id, seen: action.seen, send_request_id: requestId,
          request_id: crypto.randomUUID() });
        ctx.toast(result.status === "accepted" ? "Sent from Mail. The draft card is marked as replaced." : "The draft card is marked as replaced. Check delivery before writing again.",
          result.status !== "accepted");
      } catch (cause) {
        ctx.toast(`Sent, but the suggestion card was not updated: ${errorMessage(cause)} Decline the card yourself.`, true);
      }
      window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
      await ctx.refresh();
    },
  });
}

/**
 * A click on a suggestion control (not the cards' own Approve/Send/Decline). Returns true when it was one, synchronously.
 * @param {HTMLElement} button @param {SuggestionContext} ctx
 */
export function suggestionClick(button, ctx) {
  const section = button.closest("[data-followup-item]");
  const control = ["followupWatch", "followupWatchState", "followupApproveAll", "followupEdit", "followupEditCancel", "followupEditMail", "followupInstead", "followupUndo", "followupPrepare", "followupMute", "followupRetry", "followupWhy", "followupForget"].find((key) => key in button.dataset);
  if (!section || !control) return false;
  if (control === "followupWhy") {
    const why = /** @type {HTMLDetailsElement | null} */ (section.querySelector(".support-evidence"));
    if (why) { why.open = true; why.scrollIntoView({block: "nearest", behavior: "smooth"}); /** @type {HTMLElement | null} */ (why.querySelector("summary"))?.focus({preventScroll: true}); }
    return true;
  }
  const item = ctx.item(Number(/** @type {HTMLElement} */ (section).dataset.followupItem));
  const s = item?.suggestions;
  if (!s) return true;
  const work = async () => {
    if (control === "followupEdit" || control === "followupEditCancel") {
      const id = (control === "followupEdit" ? button.dataset.followupEdit : button.dataset.followupEditCancel) || "";
      if (control === "followupEdit") editing.add(id);
      else { editing.delete(id); decideResponses.delete(id); }
      // A decide card's form shows its question's rubric: read once it is open ("Reading the rubric…"), then drawn again.
      const decide = control === "followupEdit" && !decideResponses.has(id) ? (s.actions || []).find((/** @type {any} */ a) => a.id === id && a.endpoint === DECIDE
        && a.preview?.data?.kind !== "mc") : null;  // a bubble-sheet card's form needs nothing more than its preview
      const reading = decide ? readDecideResponse(decide, ctx) : null;
      await ctx.refresh();
      if (control === "followupEdit") document.querySelector(`[data-followup-edit-form="${CSS.escape(id)}"] textarea`)?.focus();
      if (reading) { await reading; if (editing.has(id)) await ctx.refresh(); }
      return;
    }
    if (control === "followupEditMail") {
      const action = (s.actions || []).find((/** @type {any} */ a) => a.id === button.dataset.followupEditMail);
      if (action) editInMail(item, action, ctx);
      return;
    }
    setBusy(/** @type {HTMLButtonElement} */ (button), true);
    try {
      if (control === "followupWatch") {
        await openWatch(item, ctx, esc);
      } else if (control === "followupWatchState") {
        const watch = s.watches.find(w => w.id === button.dataset.watch);
        await ctx.api(`/api/student-support/followups/watches/${encodeURIComponent(watch.id)}`, "POST",
          {state: button.dataset.followupWatchState, revision: watch.revision, request_id: crypto.randomUUID()});
      } else if (control === "followupApproveAll") {
        const seen = Object.fromEntries((s.actions || []).filter((/** @type {any} */ a) => a.pending).map((/** @type {any} */ a) => [a.id, a.seen]));
        // SIGN IN WHEN NEEDED: a department makeup request among them signs in to Google first when the kept sign-in ended.
        if ((s.actions || []).some((/** @type {any} */ a) => a.pending && a.endpoint === DEPARTMENT_REQUEST)
            && !await ensureGoogleSignIn({ api: ctx.api, showDialog: ctx.showDialog, dialogHeader: ctx.dialogHeader, escape: esc, toast: ctx.toast })) {
          ctx.toast("Not signed in to Google, so nothing was sent.", true);
          return;
        }
        // APPROVING: until the run returns, every read shows these steps as Approving…, so no second Approve all is offered.
        const result = await whileApproving(Object.keys(seen), () => ctx.api(`${BASE}/${encodeURIComponent(s.id)}/approve`, "POST", { seen }, {}, 600000), ctx.redraw);
        (result.results || []).forEach(followAimUpload);  // DR REQUEST EMAILS: an upload to AIM opens its sign-in window
        const unsent = (result.results || []).find((/** @type {any} */ r) => r.action?.endpoint === "gmail_mail.send" && r.result?.status !== "accepted" && !r.error && !r.stale);
        const problem = result.stale || result.remaining?.length || unsent;
        ctx.toast(result.stale ? result.message || "Review the updated cards. Nothing was submitted."
          : result.already?.length ? result.message || "Already approved. Nothing was submitted again."
          : unsent ? actionReviewMessage(unsent) : result.remaining?.length ? `Stopped: ${result.remaining.length} still wait. See the cards.` : "All approved.", Boolean(problem));
        window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
      } else if (control === "followupForget") {
        // LEARNED FROM AN EDIT: the memory's own Undo (what it replaced stands again); the line then says "Taken back".
        await ctx.api(`/api/assistant/memory/${encodeURIComponent(button.dataset.followupForget || "")}/undo`, "POST", {});
        window.dispatchEvent(new CustomEvent("faculty:assistant-memory-changed"));
        ctx.toast("Taken back. Nothing is remembered from it.");
      } else if (control === "followupPrepare") {
        button.dataset.requestId ||= crypto.randomUUID();
        await ctx.api(`${BASE}/prepare`, "POST", { enrollment_id: item.enrollment_id, item_key: s.item_key, request_id: button.dataset.requestId });
        delete button.dataset.requestId;
        ctx.toast("These suggestions are prepared next.");
      } else if (control === "followupMute") {
        const muted = button.dataset.followupMute === "on";
        await ctx.api(`${BASE}/mute`, "POST", { enrollment_id: item.enrollment_id, item_key: s.item_key, muted, request_id: crypto.randomUUID() });
        ctx.toast(muted ? "No more suggestions for this conversation." : "Suggestions are back on for this conversation.");
      } else if (control === "followupRetry") {
        await requestFollowupSuggestions(ctx.api, s, "retry", {}, button);
      } else if (control === "followupUndo") {
        button.dataset.requestId ||= crypto.randomUUID();
        await ctx.api(`${BASE}/${encodeURIComponent(button.dataset.run || "")}/undo`, "POST", { action_id: button.dataset.followupUndo,
          seen: Number(button.dataset.seen), request_id: button.dataset.requestId });
        ctx.toast(button.textContent === "Undo" ? "Undone." : "It won’t be done.");
        window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
      } else if (control === "followupInstead") {
        const action = (s.actions || []).find((/** @type {any} */ a) => a.id === button.dataset.followupInstead);
        const other = action?.assumption?.alternatives?.[Number(button.dataset.alternative)];
        if (action && other) {
          await requestFollowupSuggestions(ctx.api, s, "revise", { text: `Instead of “${action.assumption.text}”: ${other}` }, button);
          ctx.toast("Preparing again with your choice.");
        }
      }
    } catch (cause) {
      ctx.toast(errorMessage(cause), true);
    } finally {
      setBusy(/** @type {HTMLButtonElement} */ (button), false);
    }
    await ctx.refresh();
  };
  void work();
  return true;
}

/**
 * A changed suggestion control: an absence card's Decision dropdown (DECISION ON THE CARD). The choice is saved on the card: the
 * edit route makes it a new waiting card in the old one's place, through the follow-up's own write checks. Nothing is recorded
 * until the instructor approves the card. Returns true when it was one, synchronously.
 * @param {EventTarget | null} target @param {SuggestionContext} ctx
 */
export function suggestionChange(target, ctx) {
  const select = /** @type {HTMLSelectElement | null} */ (target instanceof Element && target.matches("select[data-followup-decision]") ? target : null);
  if (!select) return false;
  const id = select.dataset.followupDecision || "", was = select.dataset.value || "", status = select.value;
  const field = select.dataset.followupField || "", key = field ? `${id}|${field}` : id;
  const section = /** @type {HTMLElement | null} */ (select.closest("[data-followup-item]"));
  const card = /** @type {HTMLElement | null} */ (select.closest("[data-action-id]"));
  const s = section ? ctx.item(Number(section.dataset.followupItem))?.suggestions : null;
  if (deciding.has(key) || status === was || (field && !status)) { if (field && !status) select.value = was; return true; }
  if (!s?.id || !card) { select.value = was; return true; }
  const seen = Number(card.dataset.seen), approve = card.querySelector("[data-assistant-approve]")?.textContent?.trim() || "Approve";
  const content = JSON.stringify([id, seen, field, status]);
  let request = editRequests.get(id);  // a retry of the same choice reuses its ID
  if (!request || request.content !== content) { request = { content, id: crypto.randomUUID() }; editRequests.set(id, request); }
  const sent = request.id, focused = document.activeElement === select;
  deciding.set(key, status);
  select.disabled = true;
  void (async () => {
    let made = "";
    try {
      const result = await ctx.api(`${BASE}/${encodeURIComponent(s.id)}/edit`, "POST", field ? { action_id: id, seen, choice: { field, value: status }, request_id: sent }
        : { action_id: id, seen, status, request_id: sent }, {}, 180000);
      made = String(result?.action_id || "");
      editRequests.delete(id);
      ctx.toast(field ? `Your choice is on the card. Nothing is sent until you press ${approve}.` : `Your decision is on the card. Nothing is recorded until you press ${approve}.`);
      window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
    } catch (cause) {
      select.value = was;  // a patched redraw keeps the element, and with it what was chosen
      ctx.toast(errorMessage(cause), true);
    } finally {
      deciding.delete(key);
      select.disabled = false;
    }
    await ctx.refresh();
    // The saved choice is a new card: the keyboard stays on its dropdown.
    if (made && focused) /** @type {HTMLElement | null} */ (document.querySelector(`[data-followup-decision="${CSS.escape(made)}"]${field ? `[data-followup-field="${CSS.escape(field)}"]` : ""}`))?.focus({ preventScroll: true });
  })();
  return true;
}

/**
 * A submitted suggestion form: Ask for changes, a clarification answer, or a note on a declined card. Returns true when handled.
 * @param {HTMLFormElement} form @param {SuggestionContext} ctx
 */
export function suggestionSubmit(form, ctx) {
  if (!form.matches("[data-followup-revise], [data-assistant-question], [data-followup-note], [data-followup-edit-form]")) return false;
  const section = form.closest("[data-followup-item]");
  if (!section) return false;
  const s = ctx.item(Number(/** @type {HTMLElement} */ (section).dataset.followupItem))?.suggestions;
  if (!s?.id) return true;
  const fields = Object.fromEntries(new FormData(form));
  void runFormAction(form, async () => {
    if (form.matches("[data-followup-edit-form]")) {
      const id = form.dataset.followupEditForm || "", seen = Number(form.dataset.seen);
      const grade = decideChoice(form);  // a decide card's grade (EDIT THE GRADE); null for mail and for the reply alone
      const content = JSON.stringify([id, seen, fields.subject, fields.body, grade]);
      let request = editRequests.get(id);
      if (!request || request.content !== content) { request = { content, id: crypto.randomUUID() }; editRequests.set(id, request); }
      await ctx.api(`${BASE}/${encodeURIComponent(s.id)}/edit`, "POST", { action_id: id, seen, subject: fields.subject, body: fields.body,
        ...(grade || {}), request_id: request.id }, {}, 180000);
      editing.delete(id); editRequests.delete(id); decideResponses.delete(id);
      ctx.toast(grade ? "Your decision is on the card. Nothing changes until you press Apply." : "Your version is on the card. Nothing was sent.");
      window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
    } else if (form.matches("[data-followup-note]")) {
      form.dataset.requestId ||= crypto.randomUUID();
      const result = await ctx.api(`${BASE}/${encodeURIComponent(s.id)}/note`, "POST", { action_id: form.dataset.followupNote, text: fields.text,
        remember: fields.remember === "on", request_id: form.dataset.requestId }, {}, fields.remember === "on" ? REMEMBER_WAIT : undefined);
      delete form.dataset.requestId;
      ctx.toast("Note saved.");
      rememberedToast(ctx, result);
    } else if (form.matches("[data-followup-revise]")) {
      const result = await requestFollowupSuggestions(ctx.api, s, "revise", { text: fields.text, ...(fields.remember === "on" ? { remember: true } : {}) }, form);
      rememberedToast(ctx, result);
    } else {
      await requestFollowupSuggestions(ctx.api, s, "answer", { answers: fields, question_id: form.dataset.assistantQuestion }, form);
    }
    await ctx.refresh();
  });
  return true;
}
