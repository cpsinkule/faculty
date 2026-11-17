// @ts-check
// The Faculty assistant's drawer (assistant/, AGENTS.md "Faculty assistant"): a conversation with the model chosen in Compute
// settings, which reads Faculty through its own tools and makes changes by the instructor's rules. Changes in the instructor's
// chosen categories are made at once; everything else is an Approve card, submitted only by the Approve button here. The
// conversation lives in the web process; the browser keeps only which conversation is open (sessionStorage, a convenience).
import { viewLink } from "./file-viewer.js";
import { calloutMarkup, disclosureMarkup, emptyMarkup, errorMessage, plural, runFormAction, markBusy, setBusy } from "./ui.js";
import { createCodeTab } from "./assistant-code.js";
import { checksMarkup } from "./send-checks.js";
import { byReason } from "./canvas-message.js";
import { sendAtText } from "./scheduled-sends.js";
import { chooseQuestionOption, createWorkspaceTab, questionMarkup } from "./assistant-workspace.js";
import { memoryReworded, memoryUndone } from "./assistant-memory.js";
import { patchChildren } from "./dom-patch.js";
import { installContextCapture, recordContext, routeContext } from "./assistant-context.js";
import { createAutomationTab } from "./assistant-automations.js";
import { suggestionChange, suggestionClick, suggestionsMarkup, suggestionSubmit, whileApproving, withApproving } from "./followup-suggestions.js";
import { bytes, createAttachments, filesMarkup, safeRoute, sentFiles } from "./assistant-attach.js";
import { esc, markdown, multiline } from "./assistant-markdown.js";
import { attachPickers } from "./assistant-pickers.js";
import { chatMarkup, chatTitle, composerMarkup, contextChips, createChatView, createComposer, emptyChatMarkup, eventMarkup, isStep, pageName, sendMessage, turnsOf } from "./assistant-chat.js";

// The message text (assistant-markdown.js), the files a message shows (assistant-attach.js) and the conversation's drawing
// (assistant-chat.js) have their own modules; pages and tests that import them from here keep working.
export { esc, markdown, filesMarkup, chatMarkup, isStep };

const BASE = "/api/assistant";
const KEY = "faculty:assistant:conversation";
// RESIZE: the drawer's width as the instructor dragged it (this browser only; a convenience). Never under 480 px.
const WIDTH = "faculty:assistant:width", MIN_WIDTH = 480, PAGE_KEPT = 420;
// A running answer is read every 900 ms while the drawer shows it and every 2.5 s while it is closed (ANSWER READY).
const BUSY_POLL = 900, CLOSED_POLL = 2500, IDLE_POLL = 20000, STATUS_POLL = 60000;
const HUMAN = /** @type {Record<string, string>} */ ({
  succeeded: "Done", failed: "Failed", proposed: "Waits for you", previewed: "Waits for you", approved: "Approved", approving: "Approving…",
  declined: "Declined", expired: "Expired", replaced: "Sent from Mail", withdrawn: "Withdrawn",
});
const TONES = /** @type {Record<string, string>} */ ({
  succeeded: "success", failed: "danger", proposed: "warning", previewed: "warning", approved: "info", approving: "info", declined: "neutral", expired: "neutral",
  replaced: "info", withdrawn: "neutral",
});

/** @param {unknown} value */
const text = (value) => String(value ?? "");

/** A request body or preview as a readable list, nested values folded. @param {unknown} value @param {number} [depth] */
export function valueMarkup(value, depth = 0) {
  if (value === null || value === undefined || value === "") return '<span class="assistant-value-empty">—</span>';
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return esc(value);
  if (typeof value === "string") return `<span class="assistant-value">${esc(value.length > 4000 ? `${value.slice(0, 4000)}…` : value)}</span>`;
  if (Array.isArray(value)) {
    if (!value.length) return '<span class="assistant-value-empty">none</span>';
    if (depth > 3) return `${value.length} items`;
    const shown = value.slice(0, 25).map((item) => `<li>${valueMarkup(item, depth + 1)}</li>`).join("");
    return `<ol class="assistant-values">${shown}${value.length > 25 ? `<li>${value.length - 25} more</li>` : ""}</ol>`;
  }
  if (typeof value === "object") {
    const entries = Object.entries(/** @type {Record<string, unknown>} */ (value));
    if (!entries.length) return '<span class="assistant-value-empty">—</span>';
    if (depth > 3) return `${entries.length} fields`;
    return `<dl class="assistant-fields">${entries.map(([key, item]) => `<dt>${esc(key.replace(/_/g, " "))}</dt><dd>${valueMarkup(item, depth + 1)}</dd>`).join("")}</dl>`;
  }
  return esc(value);
}

/** A card's state as its chip says it; a sent mail whose delivery is not known says so. @param {any} action */
function actionState(action) {
  // A sent mail Gmail did not accept says where to look, whatever word the server has for it (`outcome`, assistant/outcomes.py).
  const resultStatus = action.events?.at(-1)?.detail?.result?.status;
  const uncertain = action.endpoint === "gmail_mail.send" && action.status === "succeeded"
    && (action.outcome ? action.outcome !== "succeeded" : resultStatus !== "accepted");
  if (uncertain) return { tone: "warning", label: "Check delivery" };
  if (action.outcome) {
    /** @type {Record<string, string>} */
    const labels = { started: "Started, not confirmed", uncertain: "Outcome uncertain", partial: "Partly carried out", cancelled: "Cancelled" };
    // Still running is under way, not a warning; only what may not have happened asks for a look.
    return { tone: ["uncertain", "partial"].includes(action.outcome) ? "warning" : action.outcome === "started" ? "info" : TONES[action.outcome] || "neutral",
      label: labels[action.outcome] || HUMAN[action.outcome] || action.outcome };
  }
  const status = text(action.status);
  return { tone: TONES[status] || "neutral", label: HUMAN[status] || status };
}
/** @param {{tone: string, label: string}} state */
const chipMarkup = (state) => `<span class="chip" data-tone="${state.tone}">${esc(state.label)}</span>`;

// The parts every waiting Approve card repeats: its opening tag, Decline, and the link to the page that owns the change.
/** @param {any} action @param {string} [attributes] */
const cardOpen = (action, attributes = "") => `<article class="assistant-card" data-patch-key="action-${esc(action.id)}" data-action-id="${esc(action.id)}" data-seen="${esc(action.seen)}"${attributes}>`;
/** @param {any} action */
const declineButton = (action) => `<button class="text-button" type="button" data-assistant-decline="${esc(action.id)}">Decline</button>`;
/** @param {any} action @param {{here?: string}} options */
const reviewLink = (action, options) => action.page && action.page !== options.here ? `<a class="link-arrow" href="${esc(action.page)}">Review on its page</a>` : "";
/** The entries of a preview's list that are objects (a long list ends in a note of what was cut, a string). @param {unknown} list */
const objectRows = (list) => /** @type {any[]} */ ((Array.isArray(list) ? list : []).filter((item) => item && typeof item === "object"));

/**
 * One Approve card (or a finished action's line). `here` drops a "Review on its page" link to the page already on screen; `editMail`
 * offers Edit (in place, EDIT IN PLACE) on a waiting mail card (follow-up suggestions); `extra` is caller-built markup shown above the card's buttons;
 * on a waiting mail or coding card, `mark` (markup) stands in for its status chip; `audience` says who receives a mail (FOLLOW-UP CLARITY).
 * `foldPreview`: the caller's `extra` says in plain lines what the card would write, so the route's own preview answer is folded.
 * @param {any} action @param {{here?: string, editMail?: boolean, extra?: string, compact?: boolean, heading?: string, mark?: string, audience?: string, foldPreview?: boolean}} [options]
 */
export function cardMarkup(action, options = {}) {
  const chip = chipMarkup(actionState(action));
  const route = `${esc(action.method)} ${esc(action.path)}`;
  if (!action.pending) {
    const detail = failure(action);
    return `<article class="assistant-card" data-patch-key="action-${esc(action.id)}" data-action-id="${esc(action.id)}" data-done="true"><header class="assistant-card-head">${chip}<strong>${esc(action.summary)}</strong></header>${options.compact ? "" : `<p class="assistant-card-route">${route}${action.tier === "auto" ? " · made by the assistant" : ""}</p>`}${detail ? `<p class="assistant-card-note">${esc(detail)}</p>` : ""}</article>`;
  }
  if (action.endpoint === "assistant.code_task_start") {
    const body = action.body || {};
    return `${cardOpen(action)}
      <header class="assistant-card-head">${options.mark || chip}<strong>Change Faculty: ${esc(body.title)}</strong></header>
      <blockquote class="assistant-request">${multiline(body.request)}</blockquote>
      <details class="disclosure" open><summary><span>What the coding agent will be told</span></summary><div class="disclosure-body assistant-msg">${markdown(body.brief || "")}</div></details>
      <p class="assistant-card-note">A coding agent works in its own copy of Faculty. Nothing changes until you Apply its result in the Code tab.</p>
      <div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}">Start the coding agent</button>${declineButton(action)}</div>
    </article>`;
  }
  const preview = action.preview;
  if (action.endpoint === "assistant.artifact_create") {
    return `${cardOpen(action)}
      <header class="assistant-card-head">${chip}<strong>${esc(action.body.title)}</strong></header>
      <p>${esc(action.body.filename)} · ${esc(preview?.data?.owner || action.body.level)}</p>
      <details class="disclosure" open><summary><span>Complete file content</span></summary><pre class="assistant-file-preview">${esc(action.body.content)}</pre></details>
      <p class="assistant-card-note">Creates a new retained teaching file. ${action.body.filename?.endsWith(".pdf") ? "The PDF uses plain paragraphs." : ""}</p>
      <div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}">Save file</button>${declineButton(action)}</div></article>`;
  }
  if ((action.endpoint === "gmail_mail.send" || action.endpoint === "gmail_mail.schedule") && preview?.data?.message && !preview.error) return mailCardMarkup(action, chip, options);
  if ((action.endpoint === "canvas.message_send" || action.endpoint === "canvas.message_schedule") && Array.isArray(preview?.data?.courses) && !preview.error) return canvasMessageCardMarkup(action, chip, options);
  if (action.endpoint === "canvas.announcement_post" && Array.isArray(preview?.data?.courses) && !preview.error) return canvasAnnouncementCardMarkup(action, chip, options);
  if (action.endpoint === "assistant.material_create" && preview?.data && !preview.error) {
    const body = action.body || {}, data = preview.data;
    const kinds = {assignment_version: "Assignment version", quiz_draft: "Quiz draft", rubric: "Question rubric", course_notes: "Course notes"};
    const material = body.artifact_id
      ? `<a class="link-arrow" ${viewLink(`/api/assistant/artifacts/${encodeURIComponent(body.artifact_id)}/file`, "Review teaching material", esc)}>Review retained file</a>`
      : `<blockquote class="assistant-request">${multiline(body.content || "")}</blockquote>`;
    return `${cardOpen(action)}
      <header class="assistant-card-head">${chip}<strong>${esc(body.title)}</strong></header>
      <p>${esc(kinds[data.kind] || data.kind)} · ${esc(data.destination)}</p>
      <details class="disclosure" open><summary><span>Material to import</span></summary><div class="disclosure-body">${material}</div></details>
      <p class="assistant-card-note">${esc(data.note)}</p>${options.extra || ""}
      <div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}">Approve import</button>${declineButton(action)}</div></article>`;
  }
  const previewBody = preview ? (preview.error ? `<p class="assistant-card-note">${esc(preview.error)}</p>` : valueMarkup(preview.data)) : "";
  return `${cardOpen(action)}
    <header class="assistant-card-head">${chip}<strong>${esc(action.summary)}</strong></header>
    ${options.compact ? "" : `<p class="assistant-card-route">${route}</p>`}
    ${action.reason ? `<p class="assistant-card-note">${esc(action.reason)}</p>` : ""}
    <details class="disclosure"><summary><span>What it sends</span></summary><div class="disclosure-body">${valueMarkup(action.body)}${action.confirmation ? `<p class="assistant-card-note">Approving also confirms it (“${esc(action.confirmation)}”).</p>` : ""}</div></details>
    ${preview ? `<details class="disclosure"${options.foldPreview && !preview.error ? "" : " open"}><summary><span>What the page’s preview says</span></summary><div class="disclosure-body">${previewBody}</div></details>` : ""}
    ${options.extra || ""}<div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}">Approve</button>${declineButton(action)}${reviewLink(action, options)}</div>
  </article>`;
}

// ONE CARD FOR ONE CHANGE (ASSISTANT-BATCHES; requirement, 2026-10-09): a group whose cards share one
// route is drawn as ONE card wherever its cards would be (chat, floating chat, Changes): its title and count, each change on one line
// with Leave out, what the first one sends, Approve all and Decline all. Approve all is the ordinary batch approval: every card
// preflighted, then submitted in order, stopping at the first problem, each with its own receipt.

/** Groups this browser is approving (shared by the drawer and the floating chat): drawn as Approving…, without buttons. */
const approvingBatches = /** @type {Set<string>} */ (new Set());

/** Why a finished card failed, from its last event (as a done card says it). @param {any} action */
const failure = (action) => { const last = (action?.events || []).at(-1); return text(last?.detail?.result?.error || last?.detail?.error || ""); };

/** The one-route groups by the ID of each card in them. @param {any[] | undefined} batches */
const groupsOf = (batches) => {
  const groupOf = /** @type {Map<string, any>} */ (new Map());
  for (const batch of batches || []) if (batch.route) for (const id of batch.action_ids) groupOf.set(id, batch);
  return groupOf;
};

/** A one-route group's cards this view has read, those still waiting, and the state its one card shows. @param {any} batch @param {Map<string, any>} actions */
function batchState(batch, actions) {
  const members = batch.action_ids.map((/** @type {string} */ id) => actions.get(id)).filter(Boolean);
  const waiting = members.filter((/** @type {any} */ a) => a.pending);
  const approving = approvingBatches.has(batch.id) && waiting.length > 0;
  const states = /** @type {Record<string, number>} */ (batch.states || {});
  const state = approving ? "approving" : waiting.length ? "proposed" : states.failed ? "failed" : states.succeeded ? "succeeded" : states.declined ? "declined" : "expired";
  return { members, waiting, approving, states, state };
}

/**
 * A one-route group as one card. `actions` holds the cards this view has read; `batch.states` counts every card of the group.
 * @param {any} batch @param {Map<string, any>} actions @param {{here?: string}} [options]
 */
export function batchCardMarkup(batch, actions, options = {}) {
  const { members, waiting, approving, states, state } = batchState(batch, actions);
  const total = batch.action_ids.length;
  const chip = chipMarkup({ tone: TONES[state] || "neutral", label: HUMAN[state] || state });
  const tally = [["succeeded", "done"], ["failed", "failed"], ["approved", "approved"], ["declined", "left out"], ["expired", "expired"]]
    .filter(([kind]) => states[kind]).map(([kind, words]) => `${states[kind]} ${words}`);
  const count = waiting.length ? `${plural(total, "change")} · ${waiting.length} wait${waiting.length === 1 ? "s" : ""} for you${tally.length ? ` · ${tally.join(", ")}` : ""}`
    : `${plural(total, "change")}${tally.length ? ` · ${tally.join(", ")}` : ""}`;
  const rows = members.map((/** @type {any} */ a) => {
    const mark = a.pending ? "" : `<span class="chip" data-tone="${TONES[a.status] || "neutral"}">${esc(HUMAN[a.status] || a.status)}</span>`;
    const note = a.pending ? text(a.preview?.error) : a.status === "failed" ? failure(a) : "";
    return `<li data-action-id="${esc(a.id)}"${a.pending ? ` data-seen="${esc(a.seen)}"` : ""}>${mark}<span>${esc(a.summary)}${note ? `<span class="assistant-card-note">${esc(note)}</span>` : ""}</span>${a.pending && !approving ? `<button class="text-button" type="button" data-assistant-decline="${esc(a.id)}">Leave out</button>` : ""}</li>`;
  }).join("");
  const first = waiting[0] || members[0];
  const reasons = new Set(waiting.map((/** @type {any} */ a) => a.reason || ""));
  const reason = waiting.length && reasons.size === 1 ? text(waiting[0].reason) : "";
  const pages = new Set(waiting.map((/** @type {any} */ a) => a.page || ""));
  const page = pages.size === 1 && first?.page && first.page !== options.here ? `<a class="link-arrow" href="${esc(first.page)}">Review on its page</a>` : "";
  const sends = first && waiting.length ? disclosureMarkup({ label: "What the first one sends", body: `<p class="assistant-card-route">${esc(first.method)} ${esc(first.path)}</p>${valueMarkup(first.body)}`
    + (first.preview && !first.preview.error ? `<p class="assistant-card-note">The page’s preview of it:</p>${valueMarkup(first.preview.data)}` : "")
    + `<p class="assistant-card-note">Each change sends its own request${first.confirmation ? ` and confirms it (“${esc(first.confirmation)}”)` : ""}.</p>` }) : "";
  const actionsRow = approving ? `<p class="assistant-tool assistant-working" role="status">Approving ${esc(plural(waiting.length, "change"))}…</p>`
    : waiting.length ? `<div class="assistant-card-actions"><button class="button" type="button" data-batch-approve="${esc(batch.id)}">Approve all ${waiting.length}</button><button class="text-button" type="button" data-batch-decline="${esc(batch.id)}">Decline all</button>${page}</div>` : "";
  return `<article class="assistant-card assistant-batch-card" data-patch-key="batch-${esc(batch.id)}" data-batch-card="${esc(batch.id)}"${waiting.length ? "" : ' data-done="true"'}>
    <header class="assistant-card-head">${chip}<strong>${esc(batch.title)}</strong></header>
    <p class="assistant-card-note">${esc(count)}</p>
    ${reason ? `<p class="assistant-card-note">${esc(reason)}</p>` : ""}
    ${rows ? disclosureMarkup({ label: "Each change", hint: String(members.length), body: `<ol class="assistant-batch-list">${rows}</ol>` }) : ""}
    ${sends}${waiting.length && !approving ? `<p class="assistant-card-note">Approve all submits them in this order and stops at the first problem; each keeps its own receipt.</p>` : ""}
    ${actionsRow}
  </article>`;
}

/**
 * Card markup by action ID for one drawing pass: a card of a one-route group draws the group's one card, once, where its first card
 * is; its other cards draw nothing. null: neither a card this view has read nor one of a group.
 * @param {Map<string, any>} actions @param {any[]} batches @param {{here?: string}} [options]
 */
export function chatCards(actions, batches, options = {}) {
  const groupOf = groupsOf(batches);
  const drawn = /** @type {Set<string>} */ (new Set());
  return (/** @type {string} */ id) => {
    const batch = groupOf.get(id);
    if (!batch) return actions.has(id) ? cardMarkup(actions.get(id), options) : null;
    if (drawn.has(batch.id)) return "";
    drawn.add(batch.id);
    return batchCardMarkup(batch, actions, options);
  };
}

/**
 * CHANGES BESIDE THE CHAT (requirement, 2026-10-10): a conversation's cards apart from its messages, for the floating window. `panel` is its
 * cards, each once (a one-route group as its one card): those that wait for the instructor first, in the order they were made, then
 * the finished ones, the latest first. `count` is how many cards that is and `waiting` how many changes among them wait. `line(id)` is what the conversation shows where a card was made: its state
 * and its name on one line, the name a button that brings the card into view ("" for a card already named or a later card of a
 * group already named; null for an action this view has not read, which keeps its step line).
 * @param {any[]} events @param {Map<string, any>} actions @param {any[]} batches @param {{here?: string}} [options]
 */
export function conversationCards(events, actions, batches, options = {}) {
  const card = chatCards(actions, batches, options);
  const groupOf = groupsOf(batches);
  const lines = /** @type {Map<string, string>} */ (new Map());
  let open = "", finished = "", count = 0, waiting = 0;
  for (const event of events || []) {
    const id = event.kind === "tool" && !event.link?.link ? text(event.action?.action_id) : "";
    if (!id || lines.has(id)) continue;
    const markup = card(id);
    if (markup === null) continue;
    if (!markup) { lines.set(id, ""); continue; }
    const batch = groupOf.get(id), action = actions.get(id);
    const group = batch ? batchState(batch, actions) : null;
    const state = group ? { tone: TONES[group.state] || "neutral", label: HUMAN[group.state] || group.state } : actionState(action);
    const name = batch ? `${text(batch.title)} · ${plural(batch.action_ids.length, "change")}` : text(action.summary);
    const key = batch ? `batch-${batch.id}` : `action-${id}`;
    lines.set(id, `<p class="assistant-card-line" data-card-line="${esc(key)}">${chipMarkup(state)}<button class="text-button" type="button" data-card-show="${esc(key)}">${esc(name)}</button></p>`);
    const waits = group ? group.waiting.length : action.pending ? 1 : 0;
    if (waits) open += markup; else finished = markup + finished;
    count += 1; waiting += waits;
  }
  const panel = open + finished;
  // As chatCards: for one drawing pass, so a card whose action the conversation names again still has one line.
  const named = /** @type {Set<string>} */ (new Set());
  const line = (/** @type {string} */ id) => {
    const markup = lines.get(id);
    if (markup === undefined) return null;
    if (!markup || named.has(id)) return "";
    named.add(id);
    return markup;
  };
  return { panel, count, waiting, line };
}

/** What Approve all did, in one sentence or two. @param {any} result */
function batchOutcome(result) {
  if (result.stale || result.already) return { message: result.message, problem: Boolean(result.stale) };
  const results = result.results || [];
  const done = results.filter((/** @type {any} */ r) => r.action?.status === "succeeded").length;
  const stopped = results.find((/** @type {any} */ r) => r.action?.status !== "succeeded");
  const left = result.remaining?.length || 0;
  if (!stopped && !left) return { message: `Approved and done: ${plural(done, "change")}.`, problem: false };
  const why = stopped ? text(stopped.error || failure(stopped.action) || HUMAN[stopped.action?.status] || "not done") : "";
  return { message: `${plural(done, "change")} done.${stopped ? ` Stopped at “${text(stopped.action?.summary || stopped.id)}”: ${why}.` : ""}${left ? ` ${left} still wait${left === 1 ? "s" : ""} for you.` : ""}`, problem: true };
}

/**
 * Approve all / Decline all on a one-route group's card. Returns false when the click is not one of them; otherwise runs it.
 * `redraw` draws from what is already read; `refresh` reads the cards again and redraws.
 * @param {HTMLElement} button @param {{api: Function, toast: Function, redraw: () => void, refresh: () => Promise<unknown>}} context
 */
export function batchClick(button, { api, toast, redraw, refresh }) {
  const approve = button.getAttribute("data-batch-approve"), decline = button.getAttribute("data-batch-decline");
  const id = approve || decline;
  if (!id) return false;
  const card = button.closest("[data-batch-card]");
  const seen = Object.fromEntries([...(card?.querySelectorAll("li[data-action-id][data-seen]") || [])]
    .map((row) => [row.getAttribute("data-action-id"), Number(row.getAttribute("data-seen"))]));
  void (async () => {
    if (approve) {
      if (!Object.keys(seen).length) { toast("Nothing in this group waits for you."); return; }
      approvingBatches.add(id); redraw();
      try {
        const result = await api(`${BASE}/batches/${id}/approve`, "POST", { seen }, {}, Math.max(180000, Object.keys(seen).length * 3000));
        const outcome = batchOutcome(result);
        toast(outcome.message, outcome.problem);
      } catch (cause) { toast(errorMessage(cause), true); }
      finally { approvingBatches.delete(id); }
    } else {
      card?.querySelectorAll("button").forEach((b) => { /** @type {HTMLButtonElement} */ (b).disabled = true; });
      try { toast((await api(`${BASE}/batches/${id}/decline`, "POST", {})).message); }
      catch (cause) { toast(errorMessage(cause), true); }
    }
    window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
    await refresh();
  })();
  return true;
}

/** JEV SEND-CHECKS of pending mail cards, by action ID: filled by the instructor's own browser (never by the assistant). */
const mailChecks = /** @type {Map<string, any>} */ (new Map());
const checking = /** @type {Set<string>} */ (new Set());

/** A waiting draft's files (a mail's, a Canvas message's), each shown from Faculty's own route. @param {any[] | undefined} attachments */
function draftFilesMarkup(attachments) {
  const files = sentFiles(attachments || []);
  // Every file is named, even one whose route the drawer would not draw as a link (the server never accepts such a route).
  const unlinked = files.filter((file) => !safeRoute(file.url)).map((file) => `<li>${esc(file.name)} <span class="assistant-shown-size">${esc(bytes(file.size))}</span></li>`).join("");
  return files.length ? `<h4 class="assistant-subhead">${files.length} attachment${files.length === 1 ? "" : "s"}</h4>${filesMarkup(files)}${unlinked ? `<ul class="assistant-shown-files">${unlinked}</ul>` : ""}` : "";
}

/**
 * A mail draft waiting for the instructor, drawn from the Mail page's own preview (the message exactly as it would go, the server's
 * reading of each attached file): who it goes to, the subject, the text and every attachment, which opens from Faculty's own route.
 * @param {any} action @param {string} chip @param {{here?: string, editMail?: boolean, extra?: string, compact?: boolean, heading?: string, mark?: string, audience?: string}} [options]
 */
/** MAIL-SEND-LATER: a scheduling card says when the message goes; a send card says nothing. @param {any} action */
function laterMarkup(action) {
  if (action.endpoint !== "gmail_mail.schedule" && action.endpoint !== "canvas.message_schedule") return "";
  const when = sendAtText(action.body?.send_at);
  return calloutMarkup({ tone: "info", title: when ? `Sends ${when} (campus time)` : "No send time given",
    body: "Approving keeps it exactly as shown and Faculty sends it then. Until then you can send it now or cancel it under Mail › Scheduled." });
}

function mailCardMarkup(action, chip, options = {}) {
  const review = action.preview.data, message = review.message;
  const people = (/** @type {unknown} */ list) => {
    const all = Array.isArray(list) ? list.map(text) : [];
    if (all.length <= 8) return esc(all.join(", "));
    return `${esc(all.slice(0, 6).join(", "))} <details class="disclosure"><summary><span>and ${all.length - 6} more</span></summary><div class="disclosure-body">${esc(all.slice(6).join(", "))}</div></details>`;
  };
  const head = message.via === "canvas" && message.canvas  // a reply posted in a Canvas conversation (MAIL-CANVAS-POST)
    ? `<dt>Where</dt><dd>Canvas: the conversation with ${esc(message.canvas.recipient_name)}</dd>`
    : [["To", message.to], ["Cc", message.cc], ["Bcc", message.bcc]].filter(([, list]) => Array.isArray(list) && list.length)
      .map(([label, list]) => `<dt>${label}</dt><dd>${people(list)}</dd>`).join("");
  const attached = draftFilesMarkup(message.attachments);
  const notes = (review.notes || []).map((/** @type {unknown} */ note) => `<p class="assistant-card-note">${esc(note)}</p>`).join("");
  return `${cardOpen(action, " data-mail-draft")}
    <header class="assistant-card-head">${options.mark || chip}<strong>${esc(options.heading || action.summary)}</strong></header>
    <dl class="assistant-fields">${head}<dt>Subject</dt><dd>${esc(message.subject)}</dd></dl>
    <blockquote class="assistant-request"${options.compact ? ' tabindex="0" aria-label="Complete email draft"' : ""}>${multiline(message.text)}</blockquote>
    ${attached}${notes}${laterMarkup(action)}
    ${action.pending ? `<div class="send-checks" data-send-checks="${esc(action.id)}">${checksMarkup(mailChecks.get(mailCheckKey(action)) ?? null, esc)}</div>` : ""}
    ${action.reason ? `<p class="assistant-card-note">${esc(action.reason)}</p>` : ""}
    <p class="assistant-card-note">${options.audience ? `${esc(options.audience)} Sent from ${esc(review.sender || message.sender)}.` : options.compact ? `Send uses ${esc(review.sender || message.sender)}.` : `Approving ${laterMarkup(action) ? "schedules it" : "sends it"} from ${esc(review.sender || message.sender)} (“${esc(action.confirmation || "send")}”).`}</p>
    ${options.extra || ""}<div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}">${laterMarkup(action) ? "Schedule" : "Send"}</button>${options.editMail ? `<button class="text-button" type="button" data-followup-edit="${esc(action.id)}">Edit</button>` : ""}${declineButton(action)}${reviewLink(action, options)}</div>
  </article>`;
}

/** "507", "507 and 508", "507, 508 and 509". @param {unknown[]} items */
const listWords = (items) => items.length < 2 ? items.map(text).join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;

/**
 * CANVAS MESSAGE CARD: a Canvas message to sections waiting for the instructor, read from the Canvas page's own review (the
 * requirement, 2026-10-12). One line per Canvas course: "All students in 507 (85)" only when the review counts every current student
 * of its sections among those reached (`roster_reached` of `roster`), else how many of how many. Never the names of everyone who
 * gets it: only of a chosen section's students it does not reach and, folded, of those left out on purpose.
 * @param {any} action @param {string} chip @param {{here?: string, extra?: string}} [options]
 */
function canvasMessageCardMarkup(action, chip, options = {}) {
  const review = action.preview.data;
  const courses = objectRows(review.courses), absent = objectRows(review.not_reached), unlinked = objectRows(review.unlinked), left = objectRows(review.left_out);
  const counted = (/** @type {any} */ c) => Number.isInteger(c.roster) && Number.isInteger(c.roster_reached) && c.roster > 0;
  const total = Number(review.recipients) || 0;
  const to = courses.map((c) => {
    const where = Array.isArray(c.sections) && c.sections.length ? listWords(c.sections) : text(c.name);
    const only = c.only_canvas ? `${plural(c.only_canvas, "student")} only Canvas lists` : "";
    const whom = !counted(c) ? `${plural(Number(c.recipients) || 0, "student")} in ${where}${only ? ` (${only})` : ""}`
      : `${c.roster_reached === c.roster ? `All students in ${where} (${c.roster})` : `${c.roster_reached} of ${c.roster} students in ${where}`}${only ? `, and ${only}` : ""}`;
    return esc(`${whom}${c.too_many_for_group ? ` · individual messages (Canvas allows at most ${text(review.group_limit)} people in a group conversation)` : ""}`);
  }).join("<br>");
  // A chosen section's current students the message does not reach: named when Canvas does not list them, else among the left out.
  const missing = courses.reduce((n, c) => n + (counted(c) ? c.roster - c.roster_reached : 0), 0);
  const notIn = absent.length ? calloutMarkup({ tone: "warning", title: `${plural(absent.length, "student")} of your sections ${absent.length === 1 ? "is" : "are"} not in their Canvas course`,
    body: `They will not get it through Canvas: ${absent.map((s) => `${text(s.name)} (${text(s.section)})`).join(" · ")}` }) : "";
  const noCourse = unlinked.length ? calloutMarkup({ tone: "info", title: `Section${unlinked.length === 1 ? "" : "s"} ${listWords(unlinked.map((u) => u.section))} ${unlinked.length === 1 ? "has" : "have"} no Canvas course linked`,
    body: `${plural(unlinked.reduce((n, u) => n + (Number(u.students) || 0), 0), "student")} there will not get it. Link a course on the section tab.` }) : "";
  const leftOut = left.length ? disclosureMarkup({ label: "Left out on purpose", hint: left.length, open: missing > absent.length,
    body: byReason(left).map((g) => `<p class="assistant-card-note"><strong>${esc(g.reason)}</strong> (${g.names.length})<br>${esc(g.names.join(" · "))}</p>`).join("") }) : "";
  const groups = courses.filter((c) => c.kind === "group").length;
  // GROUP BY DEFAULT (requirement, 2026-10-13): Canvas's "individual message to each recipient" is said on a warning and on Send itself.
  const separate = review.individual === true && total > 0;
  const individually = separate ? calloutMarkup({ tone: "warning", title: "Individual messages, not a group conversation",
    body: `Each of the ${plural(total, "student")} gets their own conversation, and each reply comes to you separately.` }) : "";
  const how = !total || separate ? "" : !groups ? "Each student gets their own conversation. "
    : `One group conversation${courses.length > 1 ? ` in each Canvas course${groups < courses.length ? " that Canvas allows" : ""}` : ""}: students see who else got it and can reply to everyone. `;
  const host = text(review.canvas).replace(/^https?:\/\//, "");
  return `${cardOpen(action, " data-canvas-message")}
    <header class="assistant-card-head">${chip}<strong>${esc(action.summary)}</strong></header>
    <dl class="assistant-fields"><dt>To</dt><dd>${to || "Nobody"}</dd><dt>Subject</dt><dd>${esc(review.subject)}</dd></dl>
    <blockquote class="assistant-request">${multiline(review.body)}</blockquote>
    ${draftFilesMarkup(objectRows(review.attachments))}${laterMarkup(action)}${total ? "" : calloutMarkup({ tone: "warning", title: "Nobody in the sections you chose can be reached through Canvas" })}${individually}${notIn}${noCourse}${leftOut}
    ${action.reason ? `<p class="assistant-card-note">${esc(action.reason)}</p>` : ""}
    ${total ? `<p class="assistant-card-note">${how}Approving ${laterMarkup(action) ? "schedules it" : "sends it"} from your Canvas account${host ? ` at ${esc(host)}` : ""} (“${esc(action.confirmation || "send")}”).</p>` : ""}
    ${options.extra || ""}<div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}"${total ? "" : " disabled"}>${!total ? "Nobody to send to" : laterMarkup(action) ? `Schedule for ${esc(plural(total, "student"))}` : separate ? `Send ${esc(plural(total, "individual message"))}` : `Send to ${esc(plural(total, "student"))}`}</button>${declineButton(action)}${reviewLink(action, options)}</div>
  </article>`;
}

/** ANNOUNCEMENTS (canvas, 2026-10-15): one course-wide announcement in each linked Canvas course, posted only from Approve. */
function canvasAnnouncementCardMarkup(action, chip, options = {}) {
  const review = action.preview.data;
  const courses = objectRows(review.courses), unlinked = Array.isArray(review.unlinked) ? review.unlinked : [];
  const where = courses.map((c) => esc(`${text(c.name)}${Array.isArray(c.sections) && c.sections.length ? ` (Section ${listWords(c.sections)})` : ""}`)).join("<br>");
  const also = courses.filter((c) => Array.isArray(c.also) && c.also.length);
  const shared = also.length ? calloutMarkup({ tone: "warning", title: "Other sections share a Canvas course",
    body: also.map((c) => `${text(c.name)} also holds Section ${listWords(c.also)}, which will see it too.`).join(" ") }) : "";
  const noCourse = unlinked.length ? calloutMarkup({ tone: "info", title: `Section${unlinked.length === 1 ? "" : "s"} ${listWords(unlinked)} ${unlinked.length === 1 ? "has" : "have"} no Canvas course linked`,
    body: "It will not be posted there. Link a course on the section tab." }) : "";
  const host = text(review.canvas).replace(/^https?:\/\//, "");
  const n = courses.length;
  return `${cardOpen(action, " data-canvas-announcement")}
    <header class="assistant-card-head">${chip}<strong>${esc(action.summary)}</strong></header>
    <dl class="assistant-fields"><dt>Posted in</dt><dd>${where || "No Canvas course"}</dd><dt>Title</dt><dd>${esc(review.title)}</dd></dl>
    <blockquote class="assistant-request">${multiline(review.message)}</blockquote>
    ${n ? "" : calloutMarkup({ tone: "warning", title: "None of the sections you chose has a Canvas course linked" })}${shared}${noCourse}
    ${action.reason ? `<p class="assistant-card-note">${esc(action.reason)}</p>` : ""}
    ${n ? `<p class="assistant-card-note">Every student in ${n === 1 ? "that course" : "those courses"} sees it, and Canvas notifies them by their settings. Approving posts it from your Canvas account${host ? ` at ${esc(host)}` : ""} (“${esc(action.confirmation || "post")}”).</p>` : ""}
    ${options.extra || ""}<div class="assistant-card-actions"><button class="button" type="button" data-assistant-approve="${esc(action.id)}"${n ? "" : " disabled"}>${n ? `Post in ${esc(plural(n, "Canvas course"))}` : "Nowhere to post"}</button>${declineButton(action)}${reviewLink(action, options)}</div>
  </article>`;
}

const mailCheckKey = (action) => `${action.id}:${action.seen}`;
/** ASSISTANT / MAIL-SEND-CHECKS: shared by the drawer and follow-ups; only the instructor's browser calls Mail's check. */
export function checkActionMail(actions, api, changed) {
  for (const action of actions) {
    const key = mailCheckKey(action);
    if (!action.pending || action.endpoint !== "gmail_mail.send" || action.preview?.error || mailChecks.has(key) || checking.has(key)) continue;
    checking.add(key);
    api("/api/mail/check", "POST", action.body)
      .then(result => { mailChecks.set(key, result); })
      .catch(() => { mailChecks.set(key, {error: true}); })
      .finally(() => { checking.delete(key); if (mailChecks.size > 200) mailChecks.delete(mailChecks.keys().next().value); changed(); });
  }
}

/** DR REQUEST EMAILS (SCHEDULING, 2026-11-01): an approved upload to AIM started its sign-in on the server; the window that
 *  shows Duo's code follows it (app.js opens it on this event), wherever the card was approved. */
export const AIM_UPLOAD = "scheduling.aim_upload";
/** @param {any} result an approval's answer ({action, result}) */
export function followAimUpload(result) {
  if (result?.action?.endpoint === AIM_UPLOAD && result.action.status === "succeeded" && result.result?.id) {
    window.dispatchEvent(new CustomEvent("faculty:aim-upload", { detail: result.result }));
  }
}

/** One approval path for every card location; seen, previews, expiry and outcomes remain server checked. */
export async function submitActionReview(api, id, seen, approve) {
  const result = await api(`${BASE}/actions/${id}/${approve ? "approve" : "decline"}`, "POST", approve ? {seen} : {}, {}, 180000);
  window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
  if (approve) followAimUpload(result);
  return result;
}

/** @param {any} result */
export function actionReviewMessage(result) {
  if (result.stale) return result.message || "What this would do changed. Review the updated card.";
  const action = result.action;
  if (action?.endpoint === "gmail_mail.send" && result.result?.status !== "accepted") return result.result?.detail || "Check delivery before writing again. This message will not be resent automatically.";
  if (action?.endpoint === AIM_UPLOAD && action.status === "succeeded") return "Approved. Approve the Duo push; Faculty then uploads the copy to AIM.";
  return action?.status === "succeeded" ? "Approved and done." : "Approved, but the page refused it. See the card.";
}

/**
 * @param {{api: Function, icon: Function, toast: Function, registerShortcut: Function}} tools
 */
export function createAssistant({ api, icon, toast, registerShortcut, editMail = null, showDialog, dialogHeader, openWindow = null }) {
  const panel = document.createElement("aside");
  panel.id = "assistant-panel";
  panel.className = "assistant-panel";
  panel.hidden = true;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "false");
  panel.setAttribute("aria-labelledby", "assistant-title");
  panel.innerHTML = `<div class="assistant-resize" data-assistant-resize role="separator" aria-orientation="vertical" aria-label="Assistant width" tabindex="0"></div>
    <header class="assistant-head"><h2 id="assistant-title">Assistant</h2>
      <button class="text-button" type="button" data-assistant="new">New conversation</button>
      <button class="icon-button" type="button" data-assistant="window" aria-label="Open this chat in a window" title="Open this chat in a window">${icon("window")}</button>
      <button class="icon-button" type="button" data-assistant="close" aria-label="Close the assistant" title="Close (Esc)">${icon("close")}</button>
      <p class="assistant-head-chat"><span data-chat-title></span><span class="chip" data-assistant-model hidden></span></p></header>
    <div class="assistant-tabs" role="tablist"><button type="button" role="tab" data-assistant-tab="chat" aria-selected="true">Chat</button><button type="button" role="tab" data-assistant-tab="changes" aria-selected="false">Changes <span class="chip" data-tone="warning" data-assistant-pending hidden></span></button><button type="button" role="tab" data-assistant-tab="code" aria-selected="false">Code</button><button type="button" role="tab" data-assistant-tab="workspace" aria-selected="false">Saved <span class="chip" data-assistant-notifications hidden></span></button></div>
    <div class="assistant-body" data-assistant-chat aria-live="polite"></div>
    <div class="assistant-body" data-assistant-changes hidden></div>
    <div class="assistant-body" data-assistant-code hidden></div>
    <div class="assistant-body" data-assistant-workspace hidden></div>
    <div class="assistant-body" data-assistant-automation hidden></div>
    <form class="assistant-compose" data-assistant-form>${composerMarkup({ id: "assistant-text", label: "Message the assistant", icon,
      placeholder: "Ask about your courses, grades, scans or mail, or ask it to do something.",
      hooks: { attached: "data-assistant-attached", state: "data-assistant-state", stop: 'data-assistant="stop"' } })}
    </form>`;
  document.body.append(panel);
  const $ = (/** @type {string} */ selector) => /** @type {HTMLElement} */ (panel.querySelector(selector));
  const chat = $("[data-assistant-chat]"), changes = $("[data-assistant-changes]"), form = /** @type {HTMLFormElement} */ ($("[data-assistant-form]"));
  const field = /** @type {HTMLTextAreaElement} */ ($("#assistant-text"));
  // ATTACH IN CHAT: files that go with the next message (uploaded as soon as they are chosen).
  const attachments = createAttachments({ toast, host: $("[data-assistant-attached]"), changed: () => drawState() });
  // The shared composer and pane (assistant-chat.js): the box, the queue, the chips, files by paste and drop; the conversation.
  const composer = createComposer({ form, field, attachments, toast, dropTarget: panel, state: $("[data-assistant-state]"),
    deliver: (entry) => void deliver(entry), changed: () => drawState() });
  attachPickers({ form, field, composer, api });
  const pane = createChatView({ host: chat, toast, turns: () => turnsOf(events),
    onAgain: (words) => {
      if (busy) { toast("Wait for the current answer first."); return; }
      if (composer.unsent()) { toast("Send or clear your unsent message first."); return; }
      void deliver({ text: words, files: [], kept: [], page: location.hash || "#overview", view: view(words) });
    },
    onEdit: (words) => { composer.fill(words); }, onStarter: (words) => { composer.fill(words); },
    // ASSISTANT MEMORY: Undo takes back what a message left to remember; the chat's note then says so.
    onUndo: async (id, button) => {
      setBusy(button, true);
      try { await api(`${BASE}/memory/${id}/undo`, "POST", { conversation_id: conversation?.id }); await readConversation(); toast("Taken back."); }
      catch (cause) { toast(errorMessage(cause), true); setBusy(button, false); }
    },
    // MEMORIES GIVEN: Remove (or Restore) a memory from under the answer whose request was given it. THE REWRITE STEP: "Keep
    // my words" puts back what you typed in place of the Assistant's rewording.
    onMemory: async (operation, id, revision, button) => {
      setBusy(button, true);
      try {
        await api(`${BASE}/memory/${id}/${operation}`, "POST", operation === "remove" ? { revision } : { conversation_id: conversation?.id });
        await readConversation(); drawChat();
        toast(operation === "remove" ? "Removed. Your next message is not given it." : operation === "restore" ? "Restored." : "Kept as you typed it.");
      } catch (cause) { toast(errorMessage(cause), true); setBusy(button, false); }
    } });
  /** @type {HTMLInputElement} */ ($("[data-assistant-attach-input]")).onchange = (event) => {
    const input = /** @type {HTMLInputElement} */ (event.target);
    if (input.files?.length) void attachments.add(input.files);
    input.value = "";
  };
  // RESIZE: the left edge drags the width (the arrow keys move it 24 px; Home and End go to its limits). The panel follows
  // the pointer; the page beside it and the toasts follow once, on release (one style pass, not one a frame).
  const widest = () => Math.max(MIN_WIDTH, innerWidth - PAGE_KEPT);
  const handle = $("[data-assistant-resize]");
  function setWidth(/** @type {number} */ wanted, keep = true) {
    const width = Math.round(Math.max(MIN_WIDTH, Math.min(widest(), wanted)));
    panel.style.width = "";
    document.documentElement.style.setProperty("--assistant-width", `${width}px`);
    handle.setAttribute("aria-valuenow", String(width));
    handle.setAttribute("aria-valuemin", String(MIN_WIDTH));
    handle.setAttribute("aria-valuemax", String(widest()));
    if (keep) { try { localStorage.setItem(WIDTH, String(width)); } catch { /* a convenience only */ } }
  }
  try { const kept = Number(localStorage.getItem(WIDTH)); if (kept > MIN_WIDTH) setWidth(kept, false); } catch { /* the usual width */ }
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    panel.dataset.resizing = "";
    let width = panel.getBoundingClientRect().width;
    const move = (/** @type {PointerEvent} */ e) => { width = Math.max(MIN_WIDTH, Math.min(widest(), innerWidth - e.clientX)); panel.style.width = `${width}px`; };
    const end = () => {
      handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", end); handle.removeEventListener("pointercancel", end);
      delete panel.dataset.resizing;
      setWidth(width);
    };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", end); handle.addEventListener("pointercancel", end);
  });
  handle.addEventListener("keydown", (event) => {
    const now = panel.getBoundingClientRect().width;
    const next = event.key === "ArrowLeft" ? now + 24 : event.key === "ArrowRight" ? now - 24 : event.key === "Home" ? MIN_WIDTH : event.key === "End" ? widest() : null;
    if (next === null) return;
    event.preventDefault();
    setWidth(next);
  });
  // Ends a conversation the drawer let go of (New conversation, or another chat opened in its place).
  const retire = (/** @type {string} */ id) => api(`${BASE}/conversations/${id}/close`, "POST", {});
  const code = createCodeTab({ host: $("[data-assistant-code]"), api, toast });
  const automation = createAutomationTab({host: $("[data-assistant-automation]"), api, toast, ask: (...args) => ask(...args)});
  const automationButton = document.createElement('button');
  automationButton.type = 'button'; automationButton.setAttribute('role', 'tab');
  automationButton.dataset.assistantTab = 'automation'; automationButton.setAttribute('aria-selected', 'false');
  automationButton.textContent = 'Automation'; panel.querySelector('.assistant-tabs').append(automationButton);
  const workspace = createWorkspaceTab({host: $("[data-assistant-workspace]"), api, toast, current: () => conversation,
    ensure: ensureConversation, opened: (view) => { forget(); absorb(view); remember(view.id); selectTab("chat"); schedule(); }});
  // CHIPS: what goes with the next message. Text the instructor selects on the page is one of them (their own screen).
  let selection = "";
  document.addEventListener("selectionchange", () => {
    const found = document.getSelection();
    if (!found || found.isCollapsed || panel.contains(found.anchorNode)) return;
    selection = found.toString().trim().slice(0, 4000);
    drawContext();
  });
  installContextCapture();
  const pageNames = () => ({ title: document.getElementById("page-title")?.textContent || "", trail: document.getElementById("breadcrumb")?.textContent || "" });
  function drawContext() {
    if (panel.hidden) return;
    composer.setChips(contextChips({ page: pageName(), record: recordContext().record, route: routeContext(location.hash), selection }));
  }
  field.addEventListener("focus", drawContext);
  /**
   * What a message carries: the page and record hints on screen, less what the instructor removed from the chips, with the
   * records its "@" names stand for (PICKERS).
   */
  const view = (message = "") => {
    const context = recordContext();
    const record = { ...(composer.dropped("record") ? routeContext(location.hash) : context.record), ...composer.mentioned(message) };
    return { ...pageNames(), selection: composer.dropped("selection") ? "" : selection, context: { ...context, record } };
  };
  let status = /** @type {any} */ (null), conversation = /** @type {any} */ (null), events = /** @type {any[]} */ ([]);
  let actions = /** @type {Map<string, any>} */ (new Map()), recent = /** @type {any[]} */ ([]), pending = 0;
  let batches = /** @type {any[]} */ ([]), nextBefore = null, changesHTML = "";
  let historyExpanded = false, batchOutcome = "", releaseCapture = null;
  /** ONE CARD FOR ONE CHANGE: the card drawer for the current drawing pass of the chat. */
  let cards = chatCards(new Map(), []);
  // ASSISTANT-FOLLOWUP-SUGGESTIONS: the follow-ups whose suggestions need the instructor, each with its own cards (FOLLOW-UP REVIEW).
  let followupItems = /** @type {any[]} */ ([]);
  let after = 0, busy = false, pollTimer = 0, idleTimer = 0, tab = "chat";
  // ASSISTANT-MEMORY "STILL BEING CONSIDERED": the view's `remembering` while a sent message's note is not yet as it will
  // stay ({rewording: the memory being reworded}); the chat is read on until it is gone, though the answer is in.
  /** @type {{rewording?: string} | null} */
  let remembering = null;
  // When the running turn began, by this browser's clock (the state line counts from it); ANSWER READY: an answer came in
  // while the drawer was closed.
  let startedAt = 0, unread = false, notifications = 0, sending = false;
  let conversationGeneration = 0, conversationRead = 0, openingIntent = 0;
  /** @type {{generation: number, promise: Promise<any>} | null} */
  let openingConversation = null;
  const launchers = () => /** @type {HTMLElement[]} */ ([...document.querySelectorAll("[data-assistant-open]")]);

  function remember(id) {
    try { if (id) sessionStorage.setItem(KEY, id); else sessionStorage.removeItem(KEY); } catch { /* a convenience only */ }
  }
  function remembered() {
    try { return sessionStorage.getItem(KEY) || ""; } catch { return ""; }
  }

  // -- Reads ------------------------------------------------------------------------------------------------------------------
  async function readStatus() {
    try { status = await api(`${BASE}/status`); } catch (cause) { status = { available: false, reason: errorMessage(cause), pending: 0 }; }
    setPending(status.pending || 0);
    const model = $("[data-assistant-model]");
    model.hidden = !status.available;
    model.textContent = status.available ? `${status.model}` : "";
    // The chip is cut with an ellipsis in the drawer's header; its title names the whole model.
    model.title = status.available ? `${status.model} · ${status.engine} · reasoning ${status.reasoning || "default"}` : "";
    notifications = Number(status.notifications) || 0;
    const chip = $("[data-assistant-notifications]");
    chip.hidden = !notifications;
    chip.textContent = String(notifications);
    drawLauncher();
  }
  function setPending(count) {
    pending = count;
    const chip = $("[data-assistant-pending]");
    chip.hidden = !count;
    chip.textContent = String(count);
    drawLauncher();
  }
  /**
   * The top bar's Assistant button, written in one place: cards waiting (orange: they need the instructor), watch notifications,
   * and ANSWER READY, a quiet dot when an answer came in while the drawer was closed (never orange: nothing waits on it).
   */
  function drawLauncher() {
    const said = [pending ? `${plural(pending, "change")} wait${pending === 1 ? "s" : ""} for you` : "", notifications ? plural(notifications, "watch notification") : "", unread ? "a new answer is ready" : ""].filter(Boolean);
    for (const launcher of launchers()) {
      const badge = launcher.querySelector("[data-assistant-badge]");
      if (badge) { /** @type {HTMLElement} */ (badge).hidden = !pending; badge.textContent = String(pending); }
      let notes = /** @type {HTMLElement | null} */ (launcher.querySelector("[data-assistant-notification-badge]"));
      if (!notes) { notes = document.createElement("span"); notes.className = "chip"; notes.setAttribute("data-assistant-notification-badge", ""); notes.setAttribute("data-tone", "info"); launcher.append(notes); }
      notes.hidden = !notifications;
      notes.textContent = String(notifications);
      notes.setAttribute("title", plural(notifications, "watch notification"));
      let ready = /** @type {HTMLElement | null} */ (launcher.querySelector("[data-assistant-ready]"));
      if (!ready) { ready = document.createElement("span"); ready.className = "assistant-ready"; ready.setAttribute("data-assistant-ready", ""); ready.setAttribute("aria-hidden", "true"); launcher.append(ready); }
      ready.hidden = !unread;
      launcher.setAttribute("aria-label", said.length ? `Assistant (${said.join(", ")})` : "Assistant");
      launcher.title = unread ? "A new answer is ready (Ctrl/⌘ I)" : "Assistant (Ctrl/⌘ I)";
    }
  }
  async function readActions(older = false) {
    try {
      const data = await api(`${BASE}/actions${older && nextBefore ? `?before=${nextBefore}` : ""}`);
      recent = older ? [...recent, ...data.recent.filter((a) => !recent.some(r => r.id === a.id))]
        : historyExpanded ? [...data.recent, ...recent.filter(a => !data.recent.some(r => r.id === a.id) && !data.pending.some(r => r.id === a.id))] : data.recent;
      actions = new Map([...recent, ...data.pending].map((/** @type {any} */ a) => [a.id, a]));
      if (older || !historyExpanded) nextBefore = data.next_before;
      if (older) historyExpanded = true;
      batches = data.batches || [];
      setPending(data.pending.length);
      if (tab === "changes" && !older) {
        try {
          const support = await api("/api/student-support?kind=followup&suggestions=1");
          // APPROVING: steps this browser is still approving read Approving…, never as waiting (TEACHING-WORKFLOWS).
          followupItems = withApproving(support.items.filter((/** @type {any} */ item) => {
            const s = item.suggestions;
            return s?.id && (["needs_input", "failed", "interrupted", "running"].includes(s.state) || s.state === "ready" && s.actions.some((/** @type {any} */ a) => a.pending));
          }));
        } catch { /* Keep review input intact; server stale checks still protect every request. */ }
      }
    } catch { /* the cards keep their last state; the next read tries again */ }
  }
  async function readConversation() {
    if (!conversation) return;
    const id = conversation.id, generation = conversationGeneration, read = ++conversationRead;
    try {
      const view = await api(`${BASE}/conversations/${id}?after=${after}`);
      if (conversation?.id !== id || generation !== conversationGeneration || read !== conversationRead) return;
      absorb(view);
    } catch (cause) {
      if (conversation?.id !== id || generation !== conversationGeneration || read !== conversationRead) return;
      if (/** @type {any} */ (cause)?.status === 410) {
        try {
          const restored = await api(`${BASE}/saved/${id}/open`, "POST", {});
          if (conversation?.id !== id || generation !== conversationGeneration || read !== conversationRead) return;
          events = []; after = 0; absorb(restored); return;
        } catch { if (conversation?.id === id && generation === conversationGeneration && read === conversationRead) lost(); return; }
      }
      throw cause;
    }
  }
  /** Faculty forgot the conversation (it restarted): what the window shows stays, and the next message continues from it. */
  function lost() {
    const interrupted = busy;
    conversation = null; after = 0; busy = false; remembering = null; startedAt = 0; remember("");
    composer.giveBack();
    if (interrupted) events.push({ kind: "status", text: "The answer was interrupted when Faculty restarted. Send your message again to continue." });
    drawChat(); drawState();
  }
  function absorb(/** @type {any} */ view) {
    let newAction = false;
    for (const event of view.events || []) {
      if (event.seq <= after) continue;
      after = event.seq;
      events.push(event);
      if (event.action?.action_id || event.tool === "group_actions") newAction = true;
    }
    const was = busy;
    busy = Boolean(view.busy);
    remembering = view.remembering || null;
    conversation = { ...conversation, ...view, events: undefined };
    if (busy && !startedAt) startedAt = was ? Date.now() : turnStarted();
    if (!busy) startedAt = 0;
    if (newAction) void readActions().then(() => { drawChat(); drawChanges(); });
    // DONE AT ONCE: what the turn changed or prepared shows on the page behind the drawer now, not at that page's next refresh.
    if (newAction) window.dispatchEvent(new CustomEvent("faculty:assistant-pages-changed"));
    drawChat();
    drawState();
    if (was && !busy) {
      if (panel.hidden) { unread = true; drawLauncher(); }
      // QUEUE ONE: the waiting message goes once the answer is in; a stop, a failure or a question gives it back to the box.
      composer.settle({ ready: !conversation.question && !conversation.error && answered() });
    }
  }
  /** The last turn has an answer and did not fail. */
  const answered = () => { const turn = turnsOf(events).at(-1) || []; return turn.some((e) => e.kind === "assistant") && !turn.some((e) => e.kind === "error"); };
  /** When a turn found already running began: its first event's stamp, else now. */
  const turnStarted = () => { const at = Date.parse((turnsOf(events).at(-1) || [])[0]?.at); return Number.isFinite(at) ? Math.min(at, Date.now()) : Date.now(); };
  function forget() {
    conversationGeneration++; conversationRead++; openingIntent++; sending = false;
    conversation = null; events = []; after = 0; busy = false; remembering = null; startedAt = 0; remember("");
    composer.giveBack();
  }

  // -- Drawing ----------------------------------------------------------------------------------------------------------------
  function drawChat() {
    let html;
    if (!status) html = '<p class="assistant-tool">Reading…</p>';
    else if (!status.available && !conversation) {
      html = calloutMarkup({ tone: "info", title: "Choose the assistant's model", body: status.reason || "The assistant needs a model." })
        + (status.settings ? `<a class="link-arrow" href="${esc(status.settings)}">Open Compute settings</a>` : "");
    } else if (!events.length) html = emptyChatMarkup(location.hash);
    else {
      // Patched in place (dom-patch.js), so a "How it got this" the instructor opened stays open while the next answer comes in.
      cards = chatCards(actions, batches);
      const last = busy ? null : events.findLast((e) => e.kind === "user");
      const undone = memoryUndone(events);
      const reworded = memoryReworded(events);
      html = chatMarkup(events, busy, (event) => eventMarkup(event, { cards, capture: conversation?.capture, edit: last, undone, reworded, rewording: remembering?.rewording }), { actions: true, memories: conversation?.memories });
    }
    pane.draw({ html, question: events.length ? questionMarkup(conversation?.question) : "" });
    const title = $("[data-chat-title]"), name = chatTitle(events);
    if (title.textContent !== name) title.textContent = name;
    checkMailCards();
  }
  /** JEV SEND-CHECKS: each pending mail card is checked once, through the Mail page's own route, with the card's own request. */
  function checkMailCards() {
    checkActionMail(actions.values(), api, () => { drawChat(); drawChanges(); });
  }
  function drawChanges() {
    const waiting = [...actions.values()].filter((a) => a.pending);
    const done = recent.filter((a) => !a.pending);
    const open = batches.filter((b) => b.pending);
    const grouped = new Set(open.flatMap(b => b.action_ids));
    const groupMarkup = open.filter((b) => b.route).map((b) => batchCardMarkup(b, actions)).join("") + open.filter((b) => !b.route).map(b => `<section class="assistant-batch" data-batch="${esc(b.id)}"><h4>${esc(b.title)}</h4>
      <p class="assistant-card-note">Select the reviewed changes to approve together. They run in order and stop at the first problem; completed changes keep their receipts.</p>
      ${b.action_ids.map(id => { const a = actions.get(id); return a?.pending ? `<label class="assistant-batch-select"><input type="checkbox" data-batch-selected="${esc(id)}" data-seen="${a.seen}">Include ${esc(a.summary)}</label>${cardMarkup(a)}` : a ? cardMarkup(a) : ""; }).join("")}
      <button class="button" type="button" data-approve-batch="${esc(b.id)}">Approve selected changes</button></section>`).join("");
    // Each follow-up's cards sit in its own section (with its explanation, Approve all and Ask for changes), not in the list above.
    const inSections = new Set(followupItems.flatMap((item) => (item.suggestions.actions || []).map((/** @type {any} */ a) => a.id)));
    const followupMarkup = followupItems.map((item, index) => `<section class="assistant-card assistant-followup" data-patch-key="followup-${esc(item.suggestions.id)}">
        <h4>${esc(item.student?.name || "Student")} · ${esc(item.title)}</h4>${suggestionsMarkup(item, index, esc, { prefix: "changes-" })}
        <p><a class="link-arrow" href="#follow-ups">Open follow-ups</a></p></section>`).join("");
    const elsewhere = waiting.filter((a) => !inSections.has(a.id));
    const recentCard = chatCards(actions, batches.filter((b) => b.route && !b.pending));
    const recentMarkup = done.filter((a) => !grouped.has(a.id)).map((a) => recentCard(a.id) ?? "").join("");
    const html = batchOutcome
      + (elsewhere.length ? `<h3 class="assistant-subhead">Waiting for you</h3>${groupMarkup}${elsewhere.filter(a => !grouped.has(a.id)).map((a) => cardMarkup(a)).join("")}` : "")
      + (followupMarkup ? `<h3 class="assistant-subhead">Follow-up suggestions</h3>${followupMarkup}` : "")
      + (!elsewhere.length && !followupMarkup ? emptyMarkup({ title: "Nothing waits for you", body: "Changes the assistant prepares for your approval appear here.", heading: "h3" }) : "")
      + (recentMarkup ? `<h3 class="assistant-subhead">Recent</h3>${recentMarkup}` : "")
      + (nextBefore ? '<button class="text-button" type="button" data-assistant="older">Load older actions</button>' : "");
    if (html !== changesHTML) { patchChildren(changes, html); changesHTML = html; }
    checkMailCards();
  }
  function drawState() {
    if (busy && !startedAt) startedAt = Date.now();
    composer.setState({ started: busy ? startedAt : 0, text: !busy && conversation?.cost ? `This conversation: $${conversation.cost.toFixed(3)}` : "" });
    $('[data-assistant="stop"]').hidden = !busy;
    // QUEUE ONE: Send stays on while the assistant answers (the message waits for the answer), until one message waits.
    /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]')).disabled = !(status?.available) || attachments.busy() || busy && Boolean(composer.queued());
  }
  function selectTab(/** @type {string} */ name) {
    tab = name;
    for (const button of panel.querySelectorAll("[data-assistant-tab]")) button.setAttribute("aria-selected", String(button.getAttribute("data-assistant-tab") === name));
    chat.hidden = name !== "chat";
    changes.hidden = name !== "changes";
    $("[data-assistant-code]").hidden = name !== "code";
    $("[data-assistant-workspace]").hidden = name !== "workspace";
    $("[data-assistant-automation]").hidden = name !== "automation";
    form.hidden = name === "workspace" || name === 'automation';
    if (name === "code") code.show(); else code.hide();
    if (name === "workspace") void workspace.show(); else workspace.hide();
    if (name === 'automation') void automation.show(); else automation.hide();
    if (name === "changes") void readActions().then(drawChanges);
  }

  // -- Polling ----------------------------------------------------------------------------------------------------------------
  function schedule() {
    clearTimeout(pollTimer);
    if (!busy && !remembering) return;
    // ANSWER READY: a running answer is still read with the drawer closed (less often), so the launcher can say it is in.
    pollTimer = window.setTimeout(async () => {
      try { await readConversation(); } catch { /* read again on the next tick */ }
      schedule();
    }, panel.hidden ? CLOSED_POLL : BUSY_POLL);
  }
  function idle() {
    clearTimeout(idleTimer);
    idleTimer = window.setTimeout(async () => {
      try {
        if (!document.hidden) {
          await readStatus();
          if (!panel.hidden && !busy) { await readActions(); await readConversation(); if (tab === "changes") drawChanges(); drawChat(); schedule(); }
        }
      } catch { /* An idle read can fail while the server restarts; the next tick retries it. */ }
      finally { idle(); }
    }, panel.hidden ? STATUS_POLL : IDLE_POLL);
  }

  // -- Actions ----------------------------------------------------------------------------------------------------------------
  /** What the window shows, as the conversation so far: a new conversation starts from it, so the model keeps the thread. */
  const history = () => events.filter((e) => e.kind === "user" || e.kind === "assistant")
    .map((e) => ({ role: e.kind === "user" ? "instructor" : "assistant", text: e.text }));
  async function ensureConversation() {
    if (openingConversation?.generation === conversationGeneration) return openingConversation.promise;
    const opening = { generation: conversationGeneration, promise: loadConversation() };
    openingConversation = opening;
    try { return await opening.promise; }
    finally { if (openingConversation === opening) openingConversation = null; }
  }
  async function loadConversation() {
    if (conversation) return conversation;
    const generation = conversationGeneration;
    const saved = remembered();
    if (saved && !events.length) {
      conversation = { id: saved };
      try { await readConversation(); if (generation !== conversationGeneration) return null; if (conversation) return conversation; } catch { lost(); }
    }
    const shown = history();
    const view = await api(`${BASE}/conversations`, "POST", { page: location.hash || "#overview", history: shown });
    if (generation !== conversationGeneration) { void retire(view.id).catch(() => {}); return null; }
    conversation = view; after = 0;
    if (shown.length) events.push({ kind: "status", text: "Continued in a new session; the assistant has the conversation above." });
    remember(view.id);
    return conversation;
  }
  /**
   * Sends one message. `carried` holds the page and hints of the moment it was written (a queued message keeps its own). A lost
   * or ambiguous response is settled by one read and never resent (assistant-chat.js sendMessage); only a conversation Faculty
   * has forgotten (410: nothing ran) continues in a new one.
   */
  async function send(/** @type {string} */ message, reviewOnly = false, context = null, /** @type {string[]} */ files = [], /** @type {any} */ carried = null) {
    const generation = conversationGeneration;
    let found = await ensureConversation();
    if (!found || generation !== conversationGeneration) return false;  // nothing was sent: the caller keeps the message
    conversationRead++;
    const body = () => ({ text: message, page: carried?.page || location.hash || "#overview", after, view: carried?.view || view(), ...(reviewOnly ? {review_only: true} : {}), ...(context ? {review_context: context} : {}), ...(found.capture ? {capture_id: found.capture.id} : {}), ...(files.length ? {files} : {}) });
    let sent;
    try {
      sent = await sendMessage(api, BASE, found.id, body(), after);
    } catch (cause) {
      const failed = /** @type {any} */ (cause);
      if (generation !== conversationGeneration || conversation?.id !== found.id) return;
      if (failed?.status !== 410) { if (failed?.view && conversation?.id === found.id) absorb(failed.view); throw cause; }
      if (conversation?.capture) throw new Error('The selected content expired. Select the area again to continue with it.');
      conversation = null;  // Faculty forgot it: continue in a new one that starts from what the window shows
      found = await ensureConversation();
      if (!found || generation !== conversationGeneration) return false;  // the 410 ran nothing, and no new send began
      sent = await sendMessage(api, BASE, found.id, body(), after);
    }
    if (generation !== conversationGeneration || conversation?.id !== found.id) return;
    conversationRead++;
    selection = ""; composer.sent(message, ["selection"]); drawContext();
    startedAt = Date.now();
    absorb(sent.view);
    busy = true;
    drawState();
    schedule();
  }
  /** Sends what the composer held; a send that did not go through puts it back in the box. @param {any} entry */
  async function deliver(entry) {
    const generation = conversationGeneration;
    sending = true;
    try {
      // A new conversation started before this message went out: put it back in the box rather than drop it.
      if (await send(entry.text, false, null, entry.files, entry) === false) composer.restore(entry);
    }
    catch (cause) {
      if (generation !== conversationGeneration) return;
      composer.restore(entry);
      toast(errorMessage(cause) + (/** @type {any} */ (cause)?.uncertain ? " It may already have been sent. Check the chat before sending again." : ""), true);
    } finally { if (generation === conversationGeneration) sending = false; }
  }
  form.onsubmit = (event) => {
    event.preventDefault();
    const message = field.value.trim(), files = attachments.ids(), kept = attachments.snapshot();
    if (!message && !kept.length) return;
    if (!files) { toast("Wait for the files to finish attaching."); return; }
    const entry = { text: message, files, kept, page: location.hash || "#overview", view: view(message) };
    // QUEUE ONE: a message sent while the assistant answers waits for that answer.
    if (busy || sending) { composer.enqueue(entry); return; }
    field.value = ""; attachments.clear(); composer.grow();
    void deliver(entry);
  };
  chat.onsubmit = (event) => {
    const questionForm = /** @type {HTMLFormElement} */ (event.target);
    if (!questionForm.matches("[data-assistant-question]")) return;
    event.preventDefault();
    void runFormAction(questionForm, async () => {
      const generation = conversationGeneration, id = conversation.id;
      const view = await api(`${BASE}/conversations/${id}/questions/${questionForm.dataset.assistantQuestion}`, "POST", {answers: Object.fromEntries(new FormData(questionForm))});
      if (generation === conversationGeneration && conversation?.id === id) { conversationRead++; absorb(view); schedule(); }
    });
  };
  /** @type {import("./followup-suggestions.js").SuggestionContext} */
  const followupContext = { api, toast, showDialog, dialogHeader, item: (index) => followupItems[index], editMail: editMail || undefined,
    refresh: async () => { await readActions(); changesHTML = ""; drawChanges(); },
    redraw: () => { followupItems = withApproving(followupItems); changesHTML = ""; drawChanges(); } };
  changes.onsubmit = (event) => {
    if (suggestionSubmit(/** @type {HTMLFormElement} */ (event.target), followupContext)) event.preventDefault();
  };
  changes.onchange = (event) => { suggestionChange(event.target, followupContext); };  // DECISION ON THE CARD
  panel.onclick = async (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const button = /** @type {HTMLElement | null} */ (target.closest("button, a"));
    if (!button) return;
    if (button.matches("a[href^='#']")) return;  // a Faculty link navigates; the drawer stays open beside the page
    const tabName = button.getAttribute("data-assistant-tab");
    if (tabName) { selectTab(tabName); return; }
    if (suggestionClick(button, followupContext)) return;
    const control = button.getAttribute("data-assistant");
    if (button.hasAttribute("data-question-option")) { chooseQuestionOption(button); return; }
    if (batchClick(button, { api, toast, redraw: () => { drawChat(); drawChanges(); },
      refresh: async () => { await readActions(); changesHTML = ""; drawChanges(); drawChat(); await readConversation(); schedule(); } })) return;
    const batch = button.getAttribute("data-approve-batch");
    if (batch) {
      const selected = [...button.closest("[data-batch]").querySelectorAll("[data-batch-selected]:checked")];
      if (!selected.length) { toast("Select the changes you reviewed."); return; }
      const seen = Object.fromEntries(selected.map(input => [input.getAttribute("data-batch-selected"), Number(input.getAttribute("data-seen"))]));
      setBusy(/** @type {HTMLButtonElement} */ (button), true);
      try {
        const result = await api(`${BASE}/batches/${batch}/approve`, "POST", {seen}, {}, 180000);
        const rows = (result.results || []).map(r => `${r.action?.summary || r.id}: ${HUMAN[r.action?.status] || r.error || "Not submitted"}`);
        if (result.remaining?.length) rows.push(`${result.remaining.length} selected changes were not submitted.`);
        batchOutcome = calloutMarkup({tone: result.stale || result.remaining?.length || result.results?.some(r => r.action?.status !== "succeeded") ? "warning" : "success", title: result.stale ? "Review the changed cards" : "Batch results", body: [result.message, ...rows].join("\n")});
        toast(result.message, Boolean(result.stale));
        window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
      }
      catch (cause) { toast(errorMessage(cause), true); }
      await readActions(); changesHTML = ""; drawChanges(); await readConversation(); schedule(); return;
    }
    if (control === "close") close();
    else if (control === "new") {
      if (conversation) void retire(conversation.id).catch(() => {});
      forget(); selectTab("chat"); drawChat(); drawState(); field.focus();
    } else if (control === "window") { void toWindow();
    } else if (control === "older") { await readActions(true); drawChanges();
    } else if (control === "stop" && conversation) {
      composer.giveBack();  // a stopped answer sends nothing after it: the waiting message returns to the box
      const generation = conversationGeneration, id = conversation.id;
      try { const result = await api(`${BASE}/conversations/${id}/stop`, "POST", {}); if (generation === conversationGeneration && conversation?.id === id) { conversationRead++; absorb(result); schedule(); } } catch (cause) { toast(errorMessage(cause), true); }
    }
    const approve = button.getAttribute("data-assistant-approve");
    const decline = button.getAttribute("data-assistant-decline");
    if (approve || decline) {
      const id = /** @type {string} */ (approve || decline);
      const card = /** @type {HTMLElement} */ (button.closest("[data-action-id]"));
      card.querySelectorAll("button").forEach((b) => { /** @type {HTMLButtonElement} */ (b).disabled = true; });
      markBusy(button, true);
      try {
        if (approve) {
          const result = await whileApproving([id], () => submitActionReview(api, id, Number(card.dataset.seen), true), followupContext.redraw);
          toast(actionReviewMessage(result), Boolean(result.stale) || result.action.status !== "succeeded" || result.result?.status === "uncertain");
          if (!result.stale && result.action.endpoint === "assistant.code_task_start" && result.result?.id) { selectTab("code"); code.show(result.result.id); }
        } else {
          await submitActionReview(api, id, Number(card.dataset.seen), false);
          toast("Declined.");
        }
      } catch (cause) {
        toast(errorMessage(cause), true);
      }
      await readActions();
      pane.redraw(); changesHTML = "";
      drawChat(); drawChanges();
      await readConversation(); schedule();
    }
  };
  panel.onkeydown = (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } };

  /** `how` "click": opened by a click, so the drawer fades in (FRONTEND "Press and open motion"); the keyboard opens it at once. */
  async function open(how = "") {
    if (panel.hidden && how === "click") { panel.dataset.entered = "click"; window.setTimeout(() => { delete panel.dataset.entered; }, 1000); }
    panel.hidden = false;
    document.body.classList.add("assistant-open");
    for (const launcher of launchers()) launcher.setAttribute("aria-expanded", "true");
    if (unread) { unread = false; drawLauncher(); }
    drawContext();
    await readStatus();
    await readActions();
    if (!conversation && remembered()) { try { await ensureConversation(); } catch { forget(); } }
    drawChat(); drawChanges(); drawState();
    if (panel.hidden) return;
    if (tab === "code") code.show();
    if (tab === "workspace") await workspace.show();
    if (tab === "automation") await automation.show();
    if (busy) schedule();
    idle();
    field.focus();
  }
  function close() {
    code.hide();
    workspace.hide();
    automation.hide();
    panel.hidden = true;
    delete panel.dataset.entered;
    if (panel.hasAttribute('popover')) { panel.hidePopover(); panel.removeAttribute('popover'); document.body.append(panel); }
    document.body.classList.remove("assistant-open");
    for (const launcher of launchers()) { launcher.setAttribute("aria-expanded", "false"); }
    schedule();
    idle();
    launchers()[0]?.focus();
    releaseCapture?.();releaseCapture=null;
  }
  function toggle(how = "") { if (panel.hidden) void open(how); else close(); }
  /**
   * BOTH WAYS: this chat moves to the floating window (as "Open in Assistant" moves a window's chat here), with the unsent
   * words and files; the drawer lets go of the conversation without ending it and closes.
   */
  async function toWindow() {
    if (!openWindow) return;
    composer.giveBack();
    const generation = conversationGeneration, id = conversation?.id || "", draft = { text: field.value, kept: attachments.snapshot() };
    if (attachments.busy()) { toast("Wait for the files to finish attaching."); return; }
    if (!await openWindow(id, draft)) return;
    if (generation !== conversationGeneration) return;
    field.value = ""; attachments.clear(); composer.grow();
    conversation = null; forget(); drawChat(); drawState();
    close();
  }
  document.addEventListener("click", (event) => {
    const launcher = /** @type {HTMLElement} */ (event.target)?.closest?.("[data-assistant-open]");
    if (launcher) { event.preventDefault(); toggle(event.detail > 0 ? "click" : ""); return; }
    // A link elsewhere that opens one of the drawer's tabs (Home's "Assistant changes waiting" opens Changes); it never toggles.
    const link = /** @type {HTMLElement} */ (event.target)?.closest?.("[data-assistant-tab-open]");
    const name = link?.getAttribute("data-assistant-tab-open");
    if (link && name && ["chat", "changes", "code", "workspace", "automation"].includes(name)) {
      event.preventDefault();
      void (panel.hidden ? open(event.detail > 0 ? "click" : "") : Promise.resolve()).then(() => selectTab(name));
    }
  });
  // A memory changed on the Memory page: the chat is read once, so a note about it shows the words that stand.
  window.addEventListener("faculty:assistant-memory-changed", () => { if (conversation && !busy) void readConversation().then(schedule).catch(() => {}); });
  registerShortcut({ key: "Mod+I", label: "Open or close the assistant", allowInInput: true, scope: "global", run: () => toggle() });
  void readStatus();
  window.addEventListener("faculty:assistant-actions-changed", () => {
    void readStatus();
    if (!panel.hidden) void readActions().then(() => { drawChat(); drawChanges(); });
  });
  idle();
  async function ask(/** @type {string} */ message, {reviewOnly = false, parent = null, context = null} = {}) {
    if (busy) throw new Error("Wait for the current Assistant answer first.");
    if (composer.unsent()) throw new Error('Send or clear the unsent Assistant message first.');
    if (parent?.open) {
      parent.append(panel); panel.hidden=false; panel.setAttribute('popover','manual'); panel.showPopover();
      parent.addEventListener('close', () => { if (panel.parentElement === parent) { panel.hidePopover(); panel.removeAttribute('popover'); document.body.append(panel); } }, {once:true});
    }
    await open("click");
    if (busy) throw new Error("Wait for the current Assistant answer first.");
    selectTab("chat");
    if (await send(message, reviewOnly, context) === false) throw new Error("A new Assistant conversation started first; ask again.");
  }
  async function openConversation(id, parent = null, release = null) {
    if (composer.unsent() && !window.confirm('Open this Assistant conversation and discard the unsent Assistant message?')) return false;
    const intent = ++openingIntent;
    const incoming = await api(`${BASE}/conversations/${encodeURIComponent(id)}`);
    if (intent !== openingIntent) return false;
    releaseCapture?.();releaseCapture=release;
    // The drawer's previous conversation is released as New releases it (an answering one is left to finish).
    if (conversation && conversation.id !== incoming.id && !busy) void retire(conversation.id).catch(() => {});
    forget(); field.value=''; absorb(incoming); remember(incoming.id); selectTab('chat');
    if (parent?.open) {
      parent.append(panel);panel.hidden=false;panel.setAttribute('popover','manual');panel.showPopover();
      parent.addEventListener('close',()=>{if(panel.parentElement===parent){panel.hidePopover();panel.removeAttribute('popover');document.body.append(panel);releaseCapture?.();releaseCapture=null;}},{once:true});
    }
    await open("click");schedule();return true;
  }
  window.addEventListener('hashchange',()=>{releaseCapture?.();releaseCapture=null;if(!panel.hidden){drawContext();if(!events.length)drawChat();}});
  return { open, close, toggle, ask, openConversation };
}
