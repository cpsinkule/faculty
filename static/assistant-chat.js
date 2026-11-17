// The Assistant's conversation, drawn once for both of its surfaces (ASSISTANT "THE PANEL"; requirement, 2026-10-09): the docked drawer
// (static/assistant.js) and the floating window (static/assistant-area.js) share this module's message drawing, its pane
// (jump to latest, Copy, Ask again, math) and its composer (queue one message, what goes with it, files by paste and drop,
// a box that grows). Each surface keeps its own conversation, cards and window; nothing here sends a request by itself except
// `sendMessage`, which a surface calls from the instructor's Send.
import { esc, markdown, messageTime, tablesTSV } from "./assistant-markdown.js";
import { calloutMarkup, disclosureMarkup, emptyMarkup, loadModule } from "./ui.js";
import { evidenceMarkup, verificationMarkup } from "./assistant-workspace.js";
import { attachButton, filesMarkup, safeRoute, sentFiles } from "./assistant-attach.js";
import { patchChildren } from "./dom-patch.js";
import { memoriesGiven, memoriesGivenMarkup, memoryNote } from "./assistant-memory.js";

// -- Turns --------------------------------------------------------------------------------------------------------------------
// QUIET STEPS (requirement, 2026-10-08): what the assistant read on its way stays out of the conversation once it has answered: the
// turn's steps, its evidence check and one deduplicated list of what it read share a closed "How it got this" under the answer,
// which also says how long the turn took. While it works (2026-10-09), the steps it has finished are listed with the one it is
// on, and the composer's state line counts the time. Pages it opens, files it shows, Approve cards, failed steps and stops stay
// in the conversation, and only a confident contradiction raises a warning outside the fold (EVIDENCE WARNING, as on Follow-up).

/** A read on the way to an answer, or the status line of a check. @param {any} event */
export function isStep(event) {
  if (event.kind === "status") return event.step === true;
  return event.kind === "tool" && !event.link?.link && !event.action?.action_id && !(event.files || []).length && event.ok !== false;
}

/** The conversation as turns: one starts at the instructor's message, an answered question or approved changes. @param {any[]} events */
export function turnsOf(events) {
  /** @type {any[][]} */
  const turns = [];
  for (const event of events || []) {
    if (!turns.length || ["user", "receipt", "answer"].includes(event.kind)) turns.push([]);
    turns[turns.length - 1].push(event);
  }
  return turns;
}

/** Whole seconds from a turn's first event to its last, by the server's own stamps; null when they cannot be read. @param {any[]} turn */
export function elapsedSeconds(turn) {
  const stamps = turn.map((event) => Date.parse(event.at)).filter((value) => Number.isFinite(value));
  if (stamps.length < 2) return null;
  return Math.max(0, Math.round((stamps[stamps.length - 1] - stamps[0]) / 1000));
}

/** "23 s", "1 min 5 s", "12 min". @param {number} seconds */
export function elapsedText(seconds) {
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60), rest = seconds % 60;
  return minutes >= 10 || !rest ? `${minutes} min` : `${minutes} min ${rest} s`;
}

/** The clock of a running turn: "0:07", "12:40". @param {number} seconds */
export const clockText = (seconds) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

/** Each source once, by kind, path, fingerprint and observation time (as Follow-up's Why). @param {any[]} sources */
const uniqueSources = (sources) => [...new Map(sources.map((source) => [JSON.stringify([source.kind, source.path, source.fingerprint, source.observed_at]), source])).values()];

const LIVE_STEPS = 5;

/**
 * While a turn runs: the steps it has finished (the latest five; "N earlier steps" above them) and the line it is on now, a
 * check's own words or "Working…". @param {any[]} steps the turn's steps, in order
 */
export function liveStepsMarkup(steps) {
  const reads = steps.filter((event) => event.kind === "tool");
  const latest = steps[steps.length - 1];
  const current = latest?.kind === "status" ? latest.text : "Working…";
  const recent = reads.slice(-LIVE_STEPS), earlier = reads.length - recent.length;
  const list = reads.length ? `<ol class="assistant-live" aria-label="Steps so far">${earlier ? `<li class="assistant-live-earlier">${earlier} earlier step${earlier === 1 ? "" : "s"}</li>` : ""}${recent.map((event) => `<li>${esc(event.label || event.tool)}</li>`).join("")}</ol>` : "";
  return `${list}<p class="assistant-tool assistant-working" role="status">${esc(current)}</p>`;
}

/**
 * Ask again resends the instructor's own words as a new message. It is offered only where that cannot repeat a change or need
 * something that is gone: the turn began with a typed message and holds no card, receipt, selection, review-only flag or file.
 * @param {any[]} turn
 */
export function askAgainAllowed(turn) {
  const first = turn[0];
  if (first?.kind !== "user" || !String(first.text || "").trim() || first.capture || first.review_only || (first.files || []).length) return false;
  return !turn.some((event) => event.kind === "receipt" || event.kind === "tool" && (event.action?.action_id || event.tool === "group_actions"));
}

/** Copy, Copy table and Ask again under an answered turn. @param {any[]} turn @param {{index?: number, last?: boolean}} options */
function turnActionsMarkup(turn, { index = 0, last = false }) {
  const answer = turn.filter((event) => event.kind === "assistant" && event.text).map((event) => event.text).join("\n\n");
  if (!answer) return "";
  return `<p class="assistant-turn-actions" data-chat-turn="${index}"><button class="text-button" type="button" data-chat-copy>Copy</button>${tablesTSV(answer) ? '<button class="text-button" type="button" data-chat-copy-table>Copy table</button>' : ""}${last && askAgainAllowed(turn) ? '<button class="text-button" type="button" data-chat-again>Ask again</button>' : ""}</p>`;
}

/**
 * One exchange: the instructor's message and every reply that stays in view, then, once answered, its fold and (when `actions`)
 * what can be done with the answer.
 * @param {any[]} turn its events, in order @param {boolean} running the assistant is still on it @param {(event: any) => string} render
 * `memories` (the conversation view's map) says how each memory a request was given stands now (ASSISTANT-MEMORY "MEMORIES GIVEN").
 * @param {{actions?: boolean, index?: number, last?: boolean, memories?: Record<string, any>}} [options]
 */
export function turnMarkup(turn, running, render, options = {}) {
  const steps = turn.filter(isStep);
  const reads = steps.filter((event) => event.kind === "tool");
  const checks = turn.filter((event) => event.kind === "verification");
  const given = memoriesGiven(turn);
  const shown = turn.filter((event) => !isStep(event) && event.kind !== "verification" && event.kind !== "memories").map(render).join("");
  if (running) return shown + liveStepsMarkup(steps);
  const contradicted = checks.flatMap((check) => (check.checks || []).filter((/** @type {any} */ c) => c.verdict === "contradicted"));
  const quote = (/** @type {string} */ words) => words.length > 160 ? `${words.slice(0, 159).trimEnd()}…` : words;
  const warning = contradicted.length ? calloutMarkup({tone: "warning", title: "Something in this answer contradicts what it read",
    body: contradicted.map((/** @type {any} */ c) => `Paragraph ${c.paragraph}: “${quote(String(c.text || ""))}”`).join(" ")}) : "";
  const sources = uniqueSources([...turn.flatMap((event) => event.kind === "tool" ? event.evidence || [] : []), ...checks.flatMap((check) => check.sources || [])]);
  const body = reads.map((event) => `<p class="assistant-tool">${esc(event.label || event.tool)}</p>`).join("")
    + checks.map((check) => verificationMarkup(check, {sources: false})).join("") + evidenceMarkup(sources) + memoriesGivenMarkup(given, options.memories);
  const seconds = elapsedSeconds(turn);
  const hint = [reads.length ? `${reads.length} step${reads.length === 1 ? "" : "s"}` : "", given.length ? `${given.length} memor${given.length === 1 ? "y" : "ies"}` : "",
    seconds === null ? "" : elapsedText(seconds)].filter(Boolean).join(" · ");
  const fold = reads.length || checks.length || sources.length || given.length
    ? disclosureMarkup({label: "How it got this", hint, body, className: "assistant-steps"}) : "";
  return shown + warning + fold + (options.actions ? turnActionsMarkup(turn, options) : "");
}

/**
 * The conversation, one turn at a time.
 * @param {any[]} events @param {boolean} busy @param {(event: any) => string} render @param {{actions?: boolean, memories?: Record<string, any>}} [options]
 */
export function chatMarkup(events, busy, render, options = {}) {
  const turns = turnsOf(events);
  return turns.map((turn, index) => turnMarkup(turn, busy && index === turns.length - 1, render, { ...options, index, last: index === turns.length - 1 })).join("")
    + (busy && !turns.length ? '<p class="assistant-tool assistant-working" role="status">Working…</p>' : "");
}

/** A chat's name: its first typed message, at most 60 characters, cut at a word. "" before one is sent. @param {any[]} events */
export function chatTitle(events) {
  const first = (events || []).find((event) => event.kind === "user");
  if (!first) return "";
  if (first.capture) return "Selected area";
  const words = String(first.text || "").replace(/\s+/g, " ").trim();
  if (words.length <= 60) return words;
  const cut = words.slice(0, 60), space = cut.lastIndexOf(" ");
  return `${(space > 30 ? cut.slice(0, space) : cut).replace(/[\s,.;:–—-]+$/, "")}…`;
}

/** A short stable mark of a text, so a patch can tell an unchanged answer without comparing its markup. @param {string} words */
function mark(words) {
  let hash = 5381;
  for (let index = 0; index < words.length; index += 1) hash = (hash * 33 ^ words.charCodeAt(index)) >>> 0;
  return `${words.length}-${hash.toString(36)}`;
}

/**
 * One event that stays in the conversation, for either surface. `cards` gives a card's markup by action ID (chatCards); `capture`
 * is the conversation's live selection; `selection: false` leaves the selected content out of the message (the floating window
 * shows it above the conversation); `edit` is the own message that offers Edit; `now` and `zone` set the clock a message's time
 * is read on (checks).
 * `undone` is the memories a later event took back (`memoryUndone`); `reworded` those the Assistant reworded (`memoryReworded`).
 * @param {any} event @param {{cards?: (id: string) => string | null, capture?: any, selection?: boolean, now?: Date, zone?: string, edit?: any, undone?: Set<string>, reworded?: Map<string, any>, rewording?: string}} [ctx]
 */
export function eventMarkup(event, ctx = {}) {
  if (event.kind === "user") {
    const capture = event.capture && event.capture.id === ctx.capture?.id ? ctx.capture : event.capture;
    const selected = ctx.selection === false ? "" : capture?.selection?.text;
    const time = messageTime(event.at, ctx.now, ctx.zone);
    const meta = (time ? `<time datetime="${esc(event.at)}">${esc(time)}</time>` : "") + (ctx.edit === event && !capture ? '<button class="text-button" type="button" data-chat-edit>Edit</button>' : "");
    // ASSISTANT "DIAGNOSE": a review-only request says so, from the flag the server recorded on the message.
    return `<div class="assistant-msg" data-from="you"><div class="assistant-msg-meta"><span class="assistant-speaker">You</span>${meta}</div>${event.review_only ? '<span class="chip" data-tone="info" data-review-only>Review only · every change waits for Approve</span>' : ""}<p>${esc(capture ? "Analyze this selected area" : event.text).replace(/\n/g, "<br>")}</p>${selected ? `<details class="disclosure"><summary>Selected content</summary><pre class="assistant-selected-text">${esc(selected)}</pre></details>` : ""}${ctx.selection !== false && capture && safeRoute(capture.url) ? `<figure><img data-capture-image src="${esc(capture.url)}" alt="Selected area of Faculty"><figcaption>Selected image · ${esc(capture.captured_at)}</figcaption></figure>` : ""}${event.files?.length ? filesMarkup(sentFiles(event.files)) : ""}</div>`;
  }
  // An answer never changes once it is here: `data-patch-static` lets a poll's patch leave it (and its drawn math) alone.
  if (event.kind === "assistant") return `<div class="assistant-msg" data-from="assistant" data-patch-static="${esc(`${event.seq ?? ""}-${mark(String(event.text ?? ""))}`)}"><div class="assistant-speaker">Assistant</div>${markdown(event.text)}</div>`;
  if (event.kind === "error") return calloutMarkup({ tone: "danger", title: "The assistant stopped", body: event.text });
  if (event.kind === "memory") return memoryNote(event, ctx.undone, ctx.reworded, ctx.rewording);  // ASSISTANT MEMORY: what a typed message left to remember
  if (["status", "receipt", "answer"].includes(event.kind)) return event.text ? `<p class="assistant-tool">${esc(event.text)}</p>` : "";
  if (event.kind === "tool") {
    if (event.link?.link) return `<p class="assistant-tool"><a class="link-arrow assistant-route" href="${esc(event.link.link)}">${esc(event.link.label || event.link.link)}</a></p>`;
    const card = event.action?.action_id && ctx.cards ? ctx.cards(event.action.action_id) : null;
    if (card !== null && card !== undefined) return card;
    return `<p class="assistant-tool" data-ok="${event.ok === false ? "false" : "true"}">${esc(event.label || event.tool)}</p>${filesMarkup(event.files)}`;
  }
  return "";
}

// -- An empty chat --------------------------------------------------------------------------------------------------------------
// STARTERS: three questions for the page on screen. A click only writes the question into the box; nothing is sent until Send.
const STARTERS = /** @type {Record<string, string[]>} */ ({
  "#gradebook": ["Who is missing work in this gradebook?", "Which cells differ from grading’s final grade?", "Who is below a C right now?"],
  "#follow-ups": ["Which follow-ups wait on me, most urgent first?", "Which students have not replied in a week?", "What did I promise students this week?"],
  "#assignments": ["What is left before this assignment can be certified?", "Which papers still need a look?", "How did students do on each question?"],
  "#grading": ["What is left before this assignment can be certified?", "Which papers still need a look?", "How did students do on each question?"],
  "#inbox": ["Which scans need me?", "Which papers have no student yet?", "What arrived since yesterday?"],
  "#mail": ["Summarize my unread student email.", "Which emails still need a reply from me?", "Who wrote about an absence this week?"],
  "#absences": ["Which absences wait for my decision?", "Who owes a makeup, and for what?", "Which decisions are due this week?"],
  "#calendar": ["What is on my calendar this week?", "When is my next free hour for a makeup?", "Which exams are coming up?"],
  "": ["What needs me today?", "Which grades are ready to certify?", "Summarize my unread student email."],
});

/** The starters for a page address (its first path part decides). @param {string} hash */
export function startersFor(hash) {
  const area = String(hash || "").split(/[/?]/)[0];
  return STARTERS[area] || STARTERS[""];
}

/** The empty chat: what the assistant is for in one sentence, and the page's starters. @param {string} hash */
export function emptyChatMarkup(hash) {
  return emptyMarkup({ title: "Ask Faculty", body: "Ask about your courses, grades, scans or mail, or ask it to do something. Changes it may not make on its own wait for your Approve.", heading: "h3" })
    + `<div class="assistant-starters" role="group" aria-label="Questions to start from">${startersFor(hash).map((words) => `<button class="button" type="button" data-chat-starter>${esc(words)}</button>`).join("")}</div>`;
}

// -- The pane -------------------------------------------------------------------------------------------------------------------

/** Text (and, where the browser allows, its formatted form) to the clipboard; false when the browser refuses. */
async function copy(plain, html = "") {
  try {
    if (html && globalThis.ClipboardItem && navigator.clipboard?.write) {
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": new Blob([plain], { type: "text/plain" }), "text/html": new Blob([html], { type: "text/html" }) })]);
      return true;
    }
  } catch { /* the plain text below */ }
  try { await navigator.clipboard.writeText(plain); return true; } catch { return false; }
}

let math = null;
/** static/grading-math.js (KaTeX), fetched the first time an answer holds math; null when it cannot be read. */
const mathModule = () => (math ||= loadModule(() => import("./grading-math.js")).catch(() => { math = null; return null; }));

/**
 * The conversation pane of one surface: its events (patched in place, so what the instructor opened stays open while the next
 * answer comes in), the assistant's question, and "Jump to latest" when the instructor has scrolled up.
 * `turns` gives the conversation's turns as they are drawn; `onAgain` and `onEdit` receive the instructor's own words;
 * `onStarter` a starter's words; `onUndo` a memory to take back; `onMemory` Remove or Restore under "Memories given", or
 * "Keep my words" on a reworded memory's note.
 * @param {{host: HTMLElement, toast: (message: string, error?: boolean) => void, turns: () => any[][], onAgain?: (words: string) => void, onEdit?: (words: string) => void, onStarter?: (words: string) => void, onUndo?: (id: string, button: HTMLElement) => void, onMemory?: (operation: "remove" | "restore" | "as-typed", id: string, revision: number, button: HTMLElement) => void}} spec
 */
export function createChatView({ host, toast, turns, onAgain, onEdit, onStarter, onUndo, onMemory }) {
  host.innerHTML = '<div data-chat-events></div><div data-chat-question></div><button class="button assistant-latest" type="button" data-chat-latest aria-live="off" hidden>Jump to latest</button>';
  const eventsNode = /** @type {HTMLElement} */ (host.querySelector("[data-chat-events]"));
  const questionNode = /** @type {HTMLElement} */ (host.querySelector("[data-chat-question]"));
  const latest = /** @type {HTMLElement} */ (host.querySelector("[data-chat-latest]"));
  /** @type {string | null} */
  let written = null;
  let writtenQuestion = "";
  const nearEnd = () => host.scrollHeight - host.scrollTop - host.clientHeight < 80;
  const showLatest = () => { latest.hidden = nearEnd(); };
  const toEnd = () => { host.scrollTop = host.scrollHeight; showLatest(); };
  host.addEventListener("scroll", showLatest, { passive: true });
  host.addEventListener("click", (event) => {
    const button = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest?.("button"));
    if (!button || !host.contains(button)) return;
    if (button === latest) { toEnd(); return; }
    if (button.hasAttribute("data-chat-starter")) { onStarter?.(button.textContent || ""); return; }
    if (button.hasAttribute("data-memory-undo")) { onUndo?.(button.getAttribute("data-memory-undo") || "", button); return; }
    if (button.hasAttribute("data-given-remove")) { onMemory?.("remove", button.getAttribute("data-given-remove") || "", Number(button.getAttribute("data-revision")), button); return; }
    if (button.hasAttribute("data-given-restore")) { onMemory?.("restore", button.getAttribute("data-given-restore") || "", 0, button); return; }
    if (button.hasAttribute("data-memory-typed")) { onMemory?.("as-typed", button.getAttribute("data-memory-typed") || "", 0, button); return; }
    if (button.hasAttribute("data-chat-edit")) {
      const first = turns().at(-1)?.[0];
      if (first?.kind === "user") onEdit?.(String(first.text || ""));
      return;
    }
    const row = button.closest("[data-chat-turn]");
    const turn = row ? turns()[Number(row.getAttribute("data-chat-turn"))] : null;
    if (!turn) return;
    const answer = turn.filter((/** @type {any} */ e) => e.kind === "assistant" && e.text).map((/** @type {any} */ e) => e.text).join("\n\n");
    if (button.hasAttribute("data-chat-copy")) {
      void copy(answer, markdown(answer)).then((done) => toast(done ? "Copied the answer." : "This browser did not allow copying.", !done));
    } else if (button.hasAttribute("data-chat-copy-table")) {
      const tables = (markdown(answer).match(/<table>[\s\S]*?<\/table>/g) || []).join("<br>");
      void copy(tablesTSV(answer), tables).then((done) => toast(done ? "Copied the table. Paste it into a spreadsheet." : "This browser did not allow copying.", !done));
    } else if (button.hasAttribute("data-chat-again") && turn[0]?.kind === "user") onAgain?.(String(turn[0].text || ""));
  });
  /** What must be done to markup just written: an expired selection picture says so, and math is drawn. */
  function decorate(stick) {
    eventsNode.querySelectorAll("[data-capture-image]").forEach((node) => {
      const image = /** @type {HTMLImageElement} */ (node);
      image.onerror = () => { image.hidden = true; if (image.nextElementSibling) image.nextElementSibling.textContent = "Selected image expired. Select the area again if it is needed."; };
    });
    if (!eventsNode.querySelector("[data-math]:not([data-math-ready])")) return;
    void mathModule().then((module) => {
      if (!module) return;
      module.renderMath(eventsNode);
      if (stick) toEnd();
    });
  }
  /**
   * Draws the pane. A poll whose markup is unchanged writes nothing (FRONTEND "Quiet refresh contract").
   * @param {{html: string, question?: string}} state
   */
  function draw({ html, question = "" }) {
    const stick = nearEnd();
    if (html !== written) { patchChildren(eventsNode, html); written = html; decorate(stick); }
    if (question !== writtenQuestion) { questionNode.innerHTML = question; writtenQuestion = question; }
    if (stick) host.scrollTop = host.scrollHeight;
    showLatest();
  }
  return { draw, toEnd, redraw: () => { written = null; } };
}

// -- The composer ---------------------------------------------------------------------------------------------------------------

/**
 * A composer's inner markup, the same in the drawer and the window: what goes with the message (chips), a message waiting to
 * be sent, the files attached, the box, and the row with the paperclip, the state line, Stop and Send. `hooks` are each
 * surface's own attribute names, which its checks and other pages use.
 * @param {{id: string, label: string, placeholder: string, icon: (name: string) => string, hooks: {attached: string, state: string, stop: string}}} spec
 */
export function composerMarkup({ id, label, placeholder, icon, hooks }) {
  return `<label class="sr-only" for="${esc(id)}">${esc(label)}</label>
      <div class="assistant-chips" data-chat-chips hidden></div><div data-chat-queued hidden></div><div ${hooks.attached} hidden></div>
      <textarea id="${esc(id)}" name="text" rows="2" maxlength="20000" placeholder="${esc(placeholder)}"></textarea>
      <div class="assistant-compose-row">${attachButton(icon)}<span class="assistant-state" ${hooks.state}></span><button class="button" type="button" ${hooks.stop} hidden>Stop</button><button class="button primary" type="submit">Send</button></div>`;
}

/** The page on screen by its own name: the breadcrumb's last part (the page), else the header's title. */
export function pageName(doc = globalThis.document) {
  const words = doc?.querySelector('#breadcrumb li[aria-current="page"]')?.textContent || doc?.getElementById("page-title")?.textContent || "";
  return words.replace(/\s+/g, " ").trim();
}

/**
 * CHIPS: what the next message carries besides its words, so the instructor sees it and can leave some of it out. The page is
 * always named (the server is told where the instructor is). The record is a chip only when it holds more than the page's
 * address says (a selected gradebook cell, the follow-up on screen); removing it sends the address's own hints.
 * @param {{page?: string, record?: Record<string, string>, route?: Record<string, string>, selection?: string}} context
 */
export function contextChips({ page = "", record = {}, route = {}, selection = "" }) {
  /** @type {{key: string, label: string, title?: string, remove?: string, value?: string}[]} */
  const chips = [];
  if (page) chips.push({ key: "page", label: `${page} page`, title: "Which page is on screen goes with your message" });
  const own = Object.fromEntries(Object.entries(record).filter(([key, value]) => route[key] !== value));
  if (Object.keys(own).length) {
    const label = own.source_key ? "This follow-up" : own.enrollment_id || own.row ? `Selected student${own.item_id ? " and column" : ""}` : "Selected record";
    chips.push({ key: "record", label, title: "Which record you selected goes with your message, not its values", remove: "Do not send the selected record", value: JSON.stringify(own) });
  }
  const words = selection.replace(/\s+/g, " ").trim();
  if (words) chips.push({ key: "selection", label: `Selected text “${words.length > 60 ? `${words.slice(0, 60)}…` : words}”`, title: "The text you selected on the page goes with your message", remove: "Do not send the selected text", value: selection });
  return chips;
}

/** What goes with the next message, one chip each; a removable one carries ×. @param {{key: string, label: string, title?: string, remove?: string}[]} chips */
export function chipsMarkup(chips) {
  return chips.map((chip) => `<span class="assistant-chip" data-chip="${esc(chip.key)}"${chip.title ? ` title="${esc(chip.title)}"` : ""}><span>${esc(chip.label)}</span>${chip.remove ? `<button class="text-button" type="button" data-chip-remove="${esc(chip.key)}" aria-label="${esc(chip.remove)}">×</button>` : ""}</span>`).join("");
}

/** A message waiting for the running answer: its first words, its files, Edit and Remove. @param {{text: string, files?: number} | null} entry */
export function queuedMarkup(entry) {
  if (!entry) return "";
  const words = entry.text.replace(/\s+/g, " ").trim();
  const shown = words.length > 90 ? `${words.slice(0, 89).trimEnd()}…` : words;
  const files = entry.files ? `${shown ? " · " : ""}${entry.files} file${entry.files === 1 ? "" : "s"}` : "";
  return `<p class="assistant-queued"><span><strong>Sends when this answer finishes:</strong> ${esc(shown)}${esc(files)}</span><button class="text-button" type="button" data-chat-queued-edit>Edit</button><button class="text-button" type="button" data-chat-queued-remove>Remove</button></p>`;
}

/**
 * One composer's behaviour. The surface keeps its own submit handler (it knows its conversation) and asks this for the rest:
 * - the box grows with what is typed; Enter sends, Shift+Enter starts a line (never while an input method is composing);
 * - files pasted (when the clipboard holds no text) or dropped on `dropTarget` are attached through `attachments`;
 * - QUEUE ONE: `enqueue` holds one message typed while the assistant answers; `settle` gives it to `deliver` once the answer
 *   is in, or puts it back in the box when the answer stopped, failed or asked a question;
 * - CHIPS: `setChips` shows what goes with the next message; `dropped(key)` says the instructor removed one;
 * - the state line counts a running turn's time.
 * @param {{form: HTMLFormElement, field: HTMLTextAreaElement, attachments: any, toast: (message: string, error?: boolean) => void, dropTarget?: HTMLElement, state: HTMLElement, deliver: (entry: any) => void, changed?: () => void}} spec
 */
export function createComposer({ form, field, attachments, toast, dropTarget, state, deliver, changed = () => {} }) {
  const chipsHost = /** @type {HTMLElement} */ (form.querySelector("[data-chat-chips]"));
  const queuedHost = /** @type {HTMLElement} */ (form.querySelector("[data-chat-queued]"));
  /** @type {any} */
  let queued = null;
  /** Keys of removed chips, each with the value it was removed at: a chip comes back when what it stands for changes. */
  const removed = /** @type {Map<string, string>} */ (new Map());
  /** @type {{key: string, label: string, title?: string, remove?: string, value?: string}[]} */
  let chips = [];
  let chipsHTML = "", timer = 0, since = 0, note = "";
  /** This tab's own messages, latest first, for the "/" picker; memory only (they may name students). */
  const recent = /** @type {string[]} */ ([]);
  /** @type {null | {handle: (event: KeyboardEvent) => boolean, update: () => void, close: () => void, open: () => boolean, mentioned: (message: string) => Record<string, string>, clear: () => void}} */
  let picker = null;

  const grow = () => {
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight + 2}px`;
  };
  field.addEventListener("input", () => { grow(); picker?.update(); });
  field.addEventListener("focus", grow);
  field.addEventListener("blur", () => picker?.close());
  field.addEventListener("keydown", (event) => {
    if (picker?.handle(event)) return;
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
  });
  field.addEventListener("paste", (event) => {
    const data = event.clipboardData;
    const files = [...(data?.files || [])];
    // A spreadsheet's cells arrive as text and a picture: the text is what the instructor meant. A screenshot has no text.
    if (!files.length || data?.types.includes("text/plain")) return;
    event.preventDefault();
    void attachments.add(files);
  });
  if (dropTarget) {
    const carriesFiles = (/** @type {DragEvent} */ event) => [...(event.dataTransfer?.types || [])].includes("Files");
    dropTarget.addEventListener("dragover", (event) => { if (carriesFiles(event)) { event.preventDefault(); dropTarget.dataset.dropping = ""; } });
    dropTarget.addEventListener("dragleave", (event) => { if (!dropTarget.contains(/** @type {Node | null} */ (event.relatedTarget))) delete dropTarget.dataset.dropping; });
    dropTarget.addEventListener("drop", (event) => {
      delete dropTarget.dataset.dropping;
      if (!carriesFiles(event)) return;
      event.preventDefault();
      const files = [...(event.dataTransfer?.files || [])];
      if (files.length) void attachments.add(files);
    });
  }
  form.addEventListener("click", (event) => {
    const button = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest?.("button"));
    if (!button) return;
    const chip = button.getAttribute("data-chip-remove");
    if (chip) { removed.set(chip, chips.find((c) => c.key === chip)?.value ?? ""); drawChips(); changed(); field.focus(); return; }
    if (button.hasAttribute("data-chat-queued-remove")) { queued = null; drawQueued(); changed(); field.focus(); }
    else if (button.hasAttribute("data-chat-queued-edit")) giveBack();
  });

  function drawChips() {
    const shown = chips.filter((chip) => !(removed.has(chip.key) && removed.get(chip.key) === (chip.value ?? "")));
    const html = chipsMarkup(shown);
    if (html === chipsHTML) return;
    chipsHTML = html; chipsHost.hidden = !html;
    chipsHost.innerHTML = html ? `<span class="assistant-chips-label">With your message:</span>${html}` : "";
  }
  function drawQueued() {
    queuedHost.innerHTML = queuedMarkup(queued ? { text: queued.text, files: (queued.kept || []).length } : null);
    queuedHost.hidden = !queued;
  }
  function drawState() {
    const seconds = since ? Math.max(0, Math.round((Date.now() - since) / 1000)) : 0;
    const words = note || (since ? `Working · ${clockText(seconds)}` : "");
    if (state.textContent !== words) state.textContent = words;
  }
  /** Puts the waiting message back in the box, above anything typed since, with its files. */
  function giveBack() {
    if (!queued) return;
    const waiting = queued; queued = null;
    field.value = [waiting.text, field.value.trim()].filter(Boolean).join("\n\n");
    if ((waiting.kept || []).length) attachments.restore([...waiting.kept, ...attachments.snapshot()].slice(0, 10));
    drawQueued(); grow(); changed(); field.focus();
  }
  return {
    grow,
    /** The instructor has something here that was not sent: typed words, attached files or a waiting message. */
    unsent: () => Boolean(field.value.trim() || attachments.count() || queued),
    queued: () => queued,
    /** QUEUE ONE. `entry` is what Send would have sent ({text, files, kept, ...}); false when one already waits. */
    enqueue(entry) {
      if (queued) { toast("One message already waits for this answer. Edit or remove it first."); return false; }
      queued = entry; field.value = ""; attachments.clear(); drawQueued(); grow(); changed();
      return true;
    },
    giveBack,
    /** The surface settled: an answer is in and nothing is asked (`ready`) sends the waiting message; anything else returns it. */
    settle({ ready }) {
      if (!queued) return;
      if (!ready) { giveBack(); return; }
      const waiting = queued; queued = null; drawQueued(); changed();
      deliver(waiting);
    },
    /** A send that did not go through returns what it carried to the box, ahead of a message that waited behind it. */
    restore(entry) {
      const waiting = queued; queued = null;
      field.value = [entry.text, waiting?.text, field.value.trim()].filter(Boolean).join("\n\n");
      const files = [...(entry.kept || []), ...(waiting?.kept || []), ...attachments.snapshot()].slice(0, 10);
      if (files.length) attachments.restore(files);
      drawQueued(); grow(); changed();
    },
    /** @param {typeof chips} next */
    setChips(next) {
      chips = next;
      for (const [key, value] of removed) if ((chips.find((chip) => chip.key === key)?.value ?? "") !== value) removed.delete(key);
      drawChips();
    },
    dropped: (/** @type {string} */ key) => removed.has(key),
    /** After a message went: what was removed for it applies to that message only where the surface says so. */
    sent(/** @type {string} */ words, /** @type {string[]} */ forget = []) {
      for (const key of forget) removed.delete(key);
      if (words && recent[0] !== words) { recent.unshift(words); recent.length = Math.min(recent.length, 20); }
      picker?.clear();
      drawChips();
    },
    recent: () => recent.slice(),
    /** Writes words into an empty box (a starter, Edit, Ask again's refusal); false when the box holds a draft. */
    fill(/** @type {string} */ words) {
      if (field.value.trim()) { toast("Send or clear your unsent message first."); field.focus(); return false; }
      field.value = words; grow(); field.focus();
      field.setSelectionRange(words.length, words.length);
      return true;
    },
    /**
     * The state line: `text` (a surface's own words) wins; a running turn counts its time from `started` (a client clock
     * reading; 0 stops it).
     * @param {{started?: number, text?: string}} next
     */
    setState({ started = 0, text = "" }) {
      note = text;
      if (started !== since) {
        since = started;
        clearInterval(timer);
        if (since) timer = window.setInterval(drawState, 1000);
      }
      drawState();
    },
    /** A "/" or "@" picker (static/assistant-pickers.js) takes the box's keys while it is open. */
    setPicker(/** @type {typeof picker} */ next) { picker = next; },
    pickerOpen: () => Boolean(picker?.open()),
    /** PICKERS: the record hints of the names an "@" choice wrote that are still in `message`. */
    mentioned: (/** @type {string} */ message) => picker?.mentioned(message) || {},
    stop() { clearInterval(timer); timer = 0; since = 0; },
  };
}

/**
 * Sends one message. A definite refusal (4xx other than 409) or a conversation that is gone (404, 410) is thrown as it is: the
 * draft stays. A lost or ambiguous response (network, 5xx, 409) may have started the turn, so the conversation is read ONCE to
 * settle it and the message is never sent again: `{view, sent: true}` when it ran, otherwise the error is thrown with `view`
 * (what was read, if anything) and `uncertain` (false for a 409, whose refusal is known).
 * @param {(path: string, method?: string, body?: any) => Promise<any>} api @param {string} base @param {string} id
 * @param {any} body the message route's body @param {number} before the last event seen before sending
 */
export async function sendMessage(api, base, id, body, before) {
  try {
    return { view: await api(`${base}/conversations/${id}/messages`, "POST", body), sent: true };
  } catch (/** @type {any} */ error) {
    const status = Number(error?.status) || 0;
    if (status === 404 || status === 410 || status >= 400 && status < 500 && status !== 409) throw error;
    let view = null;
    try { view = await api(`${base}/conversations/${id}?after=${before}`); } catch { /* the error below says what to do */ }
    // A message of files alone takes the server's words ("Look at the file I attached."): any message since then is it.
    const ran = Boolean(view) && (view.events || []).some((/** @type {any} */ e) => e.kind === "user" && e.seq > before && (e.text === body.text || !body.text));
    if (ran) return { view, sent: true };
    error.view = view;
    error.uncertain = status !== 409;
    throw error;
  }
}
