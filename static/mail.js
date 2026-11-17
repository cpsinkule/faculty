// @ts-check
// The Mail page (gmail_mail/, AGENTS.md "Gmail in Faculty"): read the instructor's Gmail as plain text, a student's mail found by
// their roster address, and write and send from Faculty. Every read and write is the server's own route; Faculty stores nothing it
// reads, the browser shows message text escaped, and a message is sent only from the review step's Send, for the exact
// fingerprint the server gave it. A reply's recipient, thread and headers are rebuilt by the server from Gmail.
import { runFormAction, dialogSession, calloutMarkup, emptyMarkup, disclosureMarkup, loadingMarkup, errorMessage, filterChipsMarkup, bindFilterChips, escape as escapeHtml, setBusy } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { fillChecks } from "./send-checks.js";
import { viewLink, viewableRoute } from "./file-viewer.js";
import { campusText, handleScheduledClick, laterFieldMarkup, scheduledMarkup } from "./scheduled-sends.js";

const BASE = "/api/mail";
const UPLOAD_WAIT = 180000;  // a preview or send carrying files from the computer (up to 25 MB) may take a while to upload
export const TABS = /** @type {const} */ ([["students", "Students"], ["sent", "Sent"], ["all", "All mail"]]);
const EVERY = 45000; // the open list is read again at most this often while it is on screen
// One Gmail page can outlast the shared 15 s request limit: a rate-limited read waits up to 15 s on the server before it gives up
// (gmail_mail/client.py BACKOFF), and each Gmail call may take up to 30 s.
const LIST_WAIT = 60000;

/** A short local time for an instant: today's time, this year's date, else the year too. @param {string} instant @param {number} [now] */
export function whenText(instant, now = Date.now()) {
  const time = Date.parse(instant);
  if (!Number.isFinite(time)) return "";
  const date = new Date(time), today = new Date(now);
  if (date.toDateString() === today.toDateString()) return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
  const sameYear = date.getFullYear() === today.getFullYear();
  return new Intl.DateTimeFormat(undefined, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" }).format(date);
}

/** "12 KB" for a byte count. @param {number} bytes */
export function sizeText(bytes) {
  if (!(bytes > 0)) return "";
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1048576 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}

export const MAX_ATTACHMENTS = 10;
export const MAX_ATTACHED_BYTES = 25_000_000;  // gmail_mail/attachments.py MAX_TOTAL
export const LIBRARY_KINDS = /** @type {const} */ ([["papers", "Student papers"], ["notes", "Lecture notes"], ["saved", "Saved files"]]);

/**
 * The compose window's attachments: each named, sized and (while composing) removable; a file from Faculty opens from its own route.
 * @param {{name: string, size?: number, route?: string, file?: unknown}[]} attachments
 * @param {{removable?: boolean}} [options]
 */
export function attachedMarkup(attachments, { removable = true } = {}) {
  if (!attachments.length) return "";
  const total = attachments.reduce((sum, item) => sum + (item.size || 0), 0);
  return `<ul class="mail-attached">${attachments.map((item, index) => `<li><span class="mail-attached-name">${item.route && viewableRoute(item.route) ? `<a ${viewLink(item.route, item.name, escapeHtml)}>${escapeHtml(item.name)}</a>` : escapeHtml(item.name)}</span><span class="field-hint">${escapeHtml([item.file ? "from your computer" : "from Faculty", sizeText(item.size || 0)].filter(Boolean).join(" · "))}</span>${removable ? `<button type="button" class="text-button" data-mail-detach="${index}" aria-label="Remove ${escapeHtml(item.name)}">Remove</button>` : ""}</li>`).join("")}</ul>${total > MAX_ATTACHED_BYTES ? `<p class="form-error">These come to ${escapeHtml(sizeText(total))}; Gmail takes at most ${MAX_ATTACHED_BYTES / 1e6} MB of files in one message.</p>` : ""}`;
}

/**
 * Faculty files the compose window can attach (GET /api/mail/files): groups of items, each with Attach unless it is already attached
 * or cannot be (a paper not reviewed yet, which says so).
 * @param {any} answer @param {Set<string>} attached
 */
export function libraryMarkup(answer, attached) {
  const groups = (answer?.groups || []).map((/** @type {any} */ group, /** @type {number} */ g) => `<h4 class="mail-library-group">${escapeHtml(group.label)}</h4>${(group.items || []).length ? `<ul class="mail-library-items">${group.items.map((/** @type {any} */ item, /** @type {number} */ i) => `<li><span class="mail-library-title">${escapeHtml(item.title || item.name)}</span><span class="field-hint">${escapeHtml([item.detail, sizeText(item.size || 0)].filter(Boolean).join(" · "))}</span>${item.route ? `<button type="button" class="button" data-mail-pick="${g}:${i}"${attached.has(item.route) ? " disabled" : ""}>${attached.has(item.route) ? "Attached" : "Attach"}</button>` : ""}</li>`).join("")}</ul>` : ""}${group.note ? `<p class="field-hint">${escapeHtml(group.note)}</p>` : ""}`).join("");
  return `${groups}${answer?.note ? `<p class="field-hint">${escapeHtml(answer.note)}</p>` : ""}`;
}

/** The addresses in a comma, semicolon or line separated field, in order, each once (compared without case). @param {string} text */
export function addressList(text) {
  const seen = new Set(), out = [];
  for (const part of String(text || "").split(/[,;\n]/)) {
    const value = part.trim();
    if (value && !seen.has(value.toLowerCase())) { seen.add(value.toLowerCase()); out.push(value); }
  }
  return out;
}

/** Adds addresses to a field's text without repeating any. @param {string} text @param {string[]} more */
export function addAddresses(text, more) {
  return addressList([text, ...more].filter(Boolean).join(", ")).join(", ");
}

/** The address a reply should be written to: the latest message that is not the instructor's own, at its `reply_to` when it names
 * one (MAIL-REPLY-TO; for a Canvas message the server gives the student's roster address, MAIL-CANVAS-REPLY), else its sender. @param {any} thread */
export function replyTarget(thread) {
  const message = [...(thread?.messages || [])].reverse().find((item) => !item.mine) || thread?.messages?.at(-1);
  const person = (message?.mine ? message?.to : message?.reply_to?.some((/** @type {any} */ item) => item.email) ? message.reply_to : message?.from)
    ?.find((/** @type {any} */ item) => item.email);
  return person?.email || "";
}

/**
 * @param {{api: Function, escape: (value: string) => string, toast: Function, showDialog: Function, dialogHeader: Function, header?: any,
 *   openStudentRecord?: ((target: {termId: string, sectionId: string, enrollmentId: string, active?: () => boolean}) => Promise<void>) | null,
 *   draftAbsence?: ((target: {thread_id: string, message_id: string}) => Promise<void>) | null,
 *   canvasSignIn?: (() => Promise<string | null>) | null}} tools
 */
export function createMail({ api, escape, toast, showDialog, dialogHeader, header = null, openStudentRecord = null, draftAbsence = null, canvasSignIn = null }) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const root = () => document.getElementById("mail-section");
  const dialog = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const active = () => location.hash.split("?")[0] === "#mail";
  let status = /** @type {any} */ (null), threads = /** @type {any[]} */ ([]), next = "", tab = "students", query = "";
  let openId = "", reading = /** @type {any} */ (null), readingError = "", listError = "", listAt = 0, generation = 0, route = "";
  let composeRequested = false, showSent = false, sent = /** @type {any} */ (null);
  // MAIL-SEND-LATER: the Scheduled tab lists every message sent later, from Mail and from the Canvas page.
  let showScheduled = false, scheduled = /** @type {any} */ (null);
  let loading = /** @type {Promise<void> | null} */ (null), loadingKey = "", scanning = false, listNote = "", opening = 0;

  function readRoute() {
    if (route === location.hash) return;
    route = location.hash;
    const params = new URLSearchParams(route.split("?")[1] || "");
    const before = `${tab}\n${query}`;
    const wanted = params.get("tab") || "students";
    tab = TABS.some(([key]) => key === wanted) ? wanted : "students";
    query = (params.get("q") || "").slice(0, 300);
    const id = params.get("thread") || "";
    if (id !== openId) { openId = /^[0-9a-f]{1,32}$/.test(id) ? id : ""; reading = null; readingError = ""; }
    showSent = params.get("sent") === "1";
    showScheduled = !showSent && params.get("scheduled") === "1";
    if (params.get("compose") === "1") { composeRequested = true; params.delete("compose"); replaceRoute(params); }
    // Opening or closing a message leaves the list as it is; only another mailbox or search reads it again.
    if (before !== `${tab}\n${query}`) { listAt = 0; threads = []; next = ""; listError = ""; }
  }
  function replaceRoute(/** @type {URLSearchParams} */ params) {
    const text = params.toString();
    route = `#mail${text ? `?${text}` : ""}`;
    history.replaceState(history.state, "", route);
  }
  function go(/** @type {Record<string, string>} */ change) {
    const params = new URLSearchParams({ tab, ...(query ? { q: query } : {}), ...(openId ? { thread: openId } : {}), ...change });
    for (const [key, value] of [...params]) if (!value) params.delete(key);
    if (params.get("tab") === "students") params.delete("tab");
    location.hash = `#mail${params.toString() ? `?${params}` : ""}`;
  }

  // -- Data ---------------------------------------------------------------------------------------------------------------
  async function loadStatus() {
    try { status = await api(BASE); } catch (cause) { status = { error: errorMessage(cause) }; }
  }
  /** Reads the list. The mailbox and search already being read are not asked for again: the shell's poll renders and refreshes this
   * page while a first read may still be under way, and each new read used to start the Gmail scan over. `fresh` reads again anyway
   * (after a change made here). */
  function loadList(more = false, fresh = false) {
    if (!active()) return Promise.resolve();
    const key = `${tab}\n${query}`;
    if (!more && !fresh && loading && loadingKey === key) return loading;
    const run = readList(more);
    loading = run; loadingKey = key;
    void run.finally(() => { if (loading === run) loading = null; });
    return run;
  }
  const mergeThreads = (/** @type {any[]} */ old, /** @type {any[]} */ add) => [...old, ...add.filter((item) => !old.some((seen) => seen.id === item.id))];
  async function readList(more = false) {
    const mine = ++generation;
    // A first read (nothing listed yet) and Show more draw the students of each Gmail page as it arrives; reading a listed
    // mailbox again swaps in the whole result at once, so the list never shrinks while it is read.
    const live = more || !listAt;
    const base = { tab, ...(query ? { q: query } : {}) };
    const page = (/** @type {string} */ token, step = false) => api(`${BASE}/threads?${new URLSearchParams({ ...base, ...(token ? { page_token: token } : {}), ...(step ? { step: "1" } : {}) })}`, "GET", undefined, {}, LIST_WAIT);
    scanning = live && tab === "students";
    try {
      let rows = more ? threads : [], token = more ? next : "", note = "";
      if (tab !== "students") {
        const result = await page(token);
        if (mine !== generation || !active()) return;
        rows = mergeThreads(rows, result.threads); token = result.next || "";
      } else {
        // One Gmail page per request, until the server's own scan would have stopped (`scan`: how many threads, how many pages).
        let found = 0, pages = 0;
        if (live) draw();
        for (;;) {
          let result;
          try {
            result = await page(token, true);
          } catch (cause) {
            if (!found) throw cause;
            // A later page failed (Gmail asked Faculty to slow down, say): what was found stays, and Show more continues from that page.
            note = `Stopped looking: ${errorMessage(cause)}`;
            break;
          }
          if (mine !== generation || !active()) return;
          rows = mergeThreads(rows, result.threads); token = result.next || "";
          found += result.threads.length; pages += 1;
          if (!token || found >= result.scan.want || pages >= result.scan.pages) break;
          if (live) { threads = rows; next = token; draw(); }
        }
      }
      if (mine !== generation) return;
      threads = rows; next = token; listNote = note; listError = ""; listAt = Date.now();
    } catch (cause) {
      if (mine !== generation) return;
      // A Show more that fails keeps what is listed: the note says why, and Show more asks for the same page again.
      if (more && threads.length) listNote = `Could not read more: ${errorMessage(cause)}`;
      else listError = errorMessage(cause);
    } finally {
      if (mine === generation) scanning = false;
    }
    draw();
  }
  async function loadThread() {
    const id = openId;
    if (!id) return;
    try {
      const result = await api(`${BASE}/threads/${id}`);
      if (id !== openId) return;
      reading = result; readingError = "";
    } catch (cause) {
      if (id !== openId) return;
      reading = null; readingError = errorMessage(cause);
    }
    draw();
    const pane = root()?.querySelector(".mail-reader");
    if (pane) pane.scrollTop = 0;
  }
  async function loadScheduled() {
    try { scheduled = await api("/api/scheduled-sends"); } catch (cause) { scheduled = { error: errorMessage(cause), scheduled: [] }; }
    draw();
  }
  async function loadSent() {
    try { sent = await api(`${BASE}/sent`); } catch (cause) { sent = { error: errorMessage(cause), sent: [] }; }
    draw();
  }

  // -- Markup -------------------------------------------------------------------------------------------------------------
  function connectMarkup() {
    if (status?.error) return calloutMarkup({ tone: "danger", title: "Mail could not be read", body: status.error });
    if (!status?.configured) return emptyMarkup({ icon: "cards", title: "Google is not set up on this server", body: "Faculty's Google client file (FACULTY_GMAIL_CLIENT_FILE and FACULTY_GMAIL_TOKEN_FILE) is missing, so Faculty cannot reach Gmail." });
    const lead = status.connected ? `Faculty already sends Response Card mail as ${status.email}. Reading your mail needs one more permission for the same account.` : "Connect the Google account Faculty sends mail as, then read and write your mail here.";
    return `<div class="mail-connect">${calloutMarkup({ tone: "info", title: status.connected ? "Allow Faculty to read your mail" : "Connect Gmail", body: lead })}<p><button type="button" class="button primary" data-mail-connect>Connect Gmail</button></p><p class="field-hint">Faculty reads mail only when you open this page or a student's mail, keeps no copy, and changes nothing in your mailbox. It sends only what you review and approve.</p></div>`;
  }
  function tabsMarkup() {
    return `<div class="mail-toolbar"><div class="filter-tabs" role="group" aria-label="Mailbox">${TABS.map(([key, label]) => `<button type="button" class="filter${tab === key && !showSent && !showScheduled ? " active" : ""}" data-mail-tab="${key}" aria-pressed="${tab === key && !showSent && !showScheduled}">${e(label)}</button>`).join("")}<button type="button" class="filter${showSent ? " active" : ""}" data-mail-faculty-sent aria-pressed="${showSent}">Sent from Faculty</button><button type="button" class="filter${showScheduled ? " active" : ""}" data-mail-scheduled aria-pressed="${showScheduled}">Scheduled</button></div><form class="field" data-mail-search role="search"><label class="sr-only" for="mail-q">Search mail</label><input id="mail-q" type="search" name="q" value="${e(query)}" maxlength="300" placeholder="Search mail (Gmail search: from:, subject:, has:attachment …)" data-page-search /></form><span class="mail-account">${e(status.email || "")}</span></div>`;
  }
  function rowMarkup(/** @type {any} */ item) {
    const who = item.student ? `${item.student.name} · ${item.student.code} §${item.student.section}${item.student.via ? ` · via ${item.student.via}` : ""}` : (item.from || []).join(", ") || "—";
    return `<li><button type="button" class="mail-thread" data-mail-thread="${e(item.id)}" data-unread="${item.unread ? "true" : "false"}" aria-current="${item.id === openId}"><span class="mail-thread-top"><strong>${e(who)}${item.count > 1 ? ` (${item.count})` : ""}</strong><span>${e(whenText(item.at))}</span></span><span class="mail-thread-subject">${e(item.subject)}</span><p class="mail-thread-snippet">${e(item.snippet)}</p></button></li>`;
  }
  function listMarkup() {
    if (listError) return `${calloutMarkup({ tone: "danger", title: "Mail could not be read", body: listError })}<p><button type="button" class="button" data-mail-retry>Try again</button></p>`;
    if (!threads.length && (scanning || !listAt)) return loadingMarkup("Reading your mail…", 4);
    if (!threads.length) return emptyMarkup({ icon: "cards", kind: "filtered", title: query ? "Nothing matches that search" : tab === "students" ? "No mail from students" : "Nothing here", body: query ? "Try other words, or All mail." : tab === "students" ? "Conversations whose latest message is from a student on your rosters (or their Canvas message) appear here. Once you reply, or their follow-up no longer waits for your reply, they move to All mail." : "" });
    const tail = scanning ? '<p class="mail-more field-hint" role="status">Looking for more mail from students…</p>'
      : `${listNote ? `<p class="mail-more field-hint">${e(listNote)}</p>` : ""}${next ? '<p class="mail-more"><button type="button" class="button" data-mail-more>Show more</button></p>' : ""}`;
    return `<ul class="mail-list">${threads.map(rowMarkup).join("")}</ul>${tail}`;
  }
  function personText(/** @type {any[]} */ people) {
    return (people || []).map((p) => (p.name && p.email ? `${p.name} <${p.email}>` : p.name || p.email)).join(", ");
  }
  function messageMarkup(/** @type {any} */ message) {
    // Each attachment opens in the app's file viewer, fetched from Gmail when opened (MAIL-ATTACHMENTS-READ; never kept).
    const files = message.files?.length ? `<ul class="mail-files" aria-label="Attachments">${message.files.map((/** @type {any} */ f) => {
      const route = f.part && /^[0-9a-f]{1,32}$/.test(String(message.id || "")) ? `/api/mail/messages/${message.id}/parts/${encodeURIComponent(f.part)}` : "";
      return `<li>Attachment: ${route ? `<a ${viewLink(route, f.name, e)}>${e(f.name)}</a>` : e(f.name)}${f.size ? ` · ${e(sizeText(f.size))}` : ""}${route ? "" : " — open it in Gmail"}</li>`;
    }).join("")}</ul>` : "";
    const also = [message.to?.length ? `To ${personText(message.to)}` : "", message.cc?.length ? `Cc ${personText(message.cc)}` : ""].filter(Boolean).join(" · ");
    const who = message.student ? `<div class="mail-student-card">${message.student.photo ? `<img class="student-photo" data-size="small" src="/api/student-photos/${e(message.student.enrollment_id)}?v=${e(message.student.photo)}" alt="Photo of ${e(message.student.name)}" width="64" height="64" loading="lazy" />` : ""}<div>${studentName(message.student)}<small>${e([message.student.code, `Section ${message.student.section}`, message.student.dropped ? "dropped" : "", message.student.via ? `via ${message.student.via}` : ""].filter(Boolean).join(" · "))}</small></div></div>` : "";
    const absence = draftAbsence && message.student && !message.mine
      ? `<p><button type="button" class="button" data-mail-absence="${e(message.id)}">Draft absence</button></p>` : "";
    return `<article class="mail-message" data-mine="${message.mine ? "true" : "false"}">${who}<header class="mail-message-head"><strong>${e(personText(message.from) || "Unknown sender")}</strong><span>${e(message.at ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(message.at)) : "")}</span>${also ? `<span>${e(also)}</span>` : ""}</header><pre class="mail-text">${e(message.text || "(no text)")}</pre>${files}${absence}</article>`;
  }
  /* A student's name opens their record from the gradebook (requirement, 2026-09-30): the card a name opens in the section's
     gradebook, read for that section when clicked. */
  function studentName(/** @type {any} */ student) {
    if (!openStudentRecord || !student.term_id || !student.section_id || !student.enrollment_id) return `<strong>${e(student.name)}</strong>`;
    return `<button type="button" class="mail-student-name" data-mail-student-record="${e(student.enrollment_id)}" aria-haspopup="dialog"><strong>${e(student.name)}</strong><span class="sr-only">: open the record</span></button>`;
  }
  /** Only the latest click opens, and only while the same conversation is still the one shown. @param {string} enrollmentId */
  function openRecord(enrollmentId) {
    const student = reading?.messages?.find((/** @type {any} */ item) => item.student?.enrollment_id === enrollmentId)?.student;
    if (!student || !openStudentRecord) return;
    const token = ++opening, thread = openId;
    void openStudentRecord({ termId: student.term_id, sectionId: student.section_id, enrollmentId,
      active: () => token === opening && active() && openId === thread && !root()?.hidden });
  }
  function scheduledListMarkup() {
    if (!scheduled) return loadingMarkup("Reading your scheduled messages…", 3);
    if (scheduled.error) return calloutMarkup({ tone: "danger", title: "The scheduled messages could not be read", body: scheduled.error });
    if (!scheduled.scheduled.length) return emptyMarkup({ icon: "cards", title: "Nothing is scheduled", body: "In a message’s review, choose Send later… to send it at a time you pick. Messages to sections from the Canvas page are listed here too." });
    return scheduledMarkup(scheduled.scheduled, e);
  }
  function readerMarkup() {
    if (showSent) return "";
    if (!openId) return emptyMarkup({ icon: "cards", title: "Choose a message", body: "It opens here as plain text." });
    if (readingError) return calloutMarkup({ tone: "danger", title: "That thread could not be read", body: readingError });
    if (!reading) return loadingMarkup("Opening…", 3);
    return `<div class="mail-reader-head"><h2 tabindex="-1">${e(reading.subject)}</h2><button type="button" class="button primary" data-mail-reply>Reply</button>${reading.inbox ? '<button type="button" class="button" data-mail-archive>Archive</button>' : '<button type="button" class="button" data-mail-unarchive>Move to Inbox</button>'}<button type="button" class="text-button danger" data-mail-trash>Move to Trash…</button><a class="text-button" href="https://mail.google.com/mail/?authuser=${encodeURIComponent(status?.email || "0")}#all/${e(reading.id)}" target="_blank" rel="noopener noreferrer">Open in Gmail</a></div>${[...reading.messages].reverse().map(messageMarkup).join("")}`; // newest first
  }
  const STATUS_WORDS = { accepted: "Sent", failed: "Not sent", uncertain: "Could not be confirmed" };
  function sentMarkup() {
    if (!sent) return loadingMarkup("Reading what Faculty sent…", 3);
    if (sent.error) return calloutMarkup({ tone: "danger", title: "The record could not be read", body: sent.error });
    if (!sent.sent.length) return emptyMarkup({ icon: "cards", title: "Faculty has sent nothing yet", body: "Messages you send from Compose are recorded here." });
    return `<ul class="mail-sent-list">${sent.sent.map((/** @type {any} */ item) => `<li><strong>${e(item.subject)}</strong><span>${e(item.recipients)} recipient${item.recipients === 1 ? "" : "s"}</span><span class="chip" data-tone="${item.status === "accepted" ? "success" : item.status === "failed" ? "danger" : "warning"}">${e(STATUS_WORDS[/** @type {keyof typeof STATUS_WORDS} */ (item.status)] || item.status)}</span><span>${e(new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.sent_at)))}</span>${item.attachments?.length ? `<small class="field-hint">Attached: ${e(item.attachments.map((/** @type {any} */ f) => f.name).join(", "))}</small>` : ""}${item.detail ? `<small class="field-hint">${e(item.detail)}</small>` : ""}</li>`).join("")}</ul>`;
  }
  function draw() {
    const host = root();
    if (!host || host.hidden) return;
    let html;
    if (!status) html = loadingMarkup("Opening Mail…", 4);
    else if (!status.permission) html = connectMarkup();
    else html = `${tabsMarkup()}${showSent ? sentMarkup() : showScheduled ? scheduledListMarkup() : `<div class="mail-layout"><div>${listMarkup()}</div><div class="mail-reader">${readerMarkup()}</div></div>`}`;
    // A search being typed survives the redraw: its text and caret are put back.
    const box = document.activeElement instanceof HTMLInputElement && document.activeElement.id === "mail-q" ? document.activeElement : null;
    const typed = box ? { value: box.value, start: box.selectionStart, end: box.selectionEnd } : null;
    patchChildren(host, html);
    fit();
    const again = document.getElementById("mail-q");
    if (typed && again instanceof HTMLInputElement) { again.value = typed.value; again.focus(); again.setSelectionRange(typed.start, typed.end); }
  }

  // The panes end at the bottom of the window (the page itself then does not scroll), and again when the window changes size.
  function fit() {
    const host = root(), layout = host?.querySelector(".mail-layout");
    if (!host || !layout) return;
    host.style.setProperty("--mail-h", `${Math.max(360, Math.floor(window.innerHeight - layout.getBoundingClientRect().top - 16))}px`);
  }
  window.addEventListener("resize", () => { if (active()) fit(); });

  // -- Actions ------------------------------------------------------------------------------------------------------------
  async function connect(/** @type {HTMLButtonElement} */ button, manage = false) {
    setBusy(button, true);
    try {
      const result = await api(`${BASE}/connect`, "POST", manage ? { manage: true } : {});
      location.assign(result.url);
    } catch (cause) {
      setBusy(button, false);
      toast(errorMessage(cause), true);
    }
  }
  function bind() {
    const host = root();
    if (!host || host.dataset.mailBound) return;
    host.dataset.mailBound = "1";
    host.addEventListener("click", (event) => {
      const target = /** @type {HTMLElement} */ (event.target).closest("button, a");
      if (!(target instanceof HTMLElement)) return;
      if (showScheduled && handleScheduledClick(event, { api, toast, showDialog, dialogHeader, escape: e, items: () => scheduled?.scheduled || [], onChange: () => void loadScheduled() })) return;
      if (target.matches("[data-mail-connect]")) void connect(/** @type {HTMLButtonElement} */ (target));
      else if (target.dataset.mailTab) go({ tab: target.dataset.mailTab, thread: "", sent: "", scheduled: "" });
      else if (target.matches("[data-mail-faculty-sent]")) go({ sent: "1", thread: "", scheduled: "" });
      else if (target.matches("[data-mail-scheduled]")) go({ scheduled: "1", sent: "", thread: "" });
      else if (target.dataset.mailThread) go({ thread: target.dataset.mailThread });
      else if (target.dataset.mailStudentRecord) openRecord(target.dataset.mailStudentRecord);
      else if (target.dataset.mailAbsence && reading && draftAbsence) void draftAbsence({ thread_id: reading.id, message_id: target.dataset.mailAbsence });
      else if (target.matches("[data-mail-trash]") && reading) openTrash(reading);
      else if (target.matches("[data-mail-archive]") && reading) void archive(reading, /** @type {HTMLButtonElement} */ (target));
      else if (target.matches("[data-mail-unarchive]") && reading) void unarchive(reading, /** @type {HTMLButtonElement} */ (target));
      else if (target.matches("[data-mail-more]")) void loadList(true);
      else if (target.matches("[data-mail-retry]")) { listAt = 0; listError = ""; draw(); void loadList(false, true); if (openId && !reading) { readingError = ""; void loadThread(); } }
      else if (target.matches("[data-mail-reply]") && reading) openCompose({ to: replyTarget(reading), subject: reading.reply_subject || (/^re:/i.test(reading.subject) ? reading.subject : `Re: ${reading.subject}`), replyThreadId: reading.id });
    });
    host.addEventListener("submit", (event) => {
      if (!(event.target instanceof HTMLFormElement) || !event.target.matches("[data-mail-search]")) return;
      event.preventDefault();
      go({ q: String(new FormData(event.target).get("q") || "").trim(), thread: "" });
    });
  }

  // -- Archive and Move to Trash ------------------------------------------------------------------------------------------
  // Both are recoverable: Archive only takes the conversation out of the Inbox (it stays in All mail), and Gmail keeps Trash 30 days.
  // The first use asks for the one wider Google permission they need.
  function askToAllow() {
    showDialog(`<div>${dialogHeader("Allow Faculty to archive and trash mail", "One more permission for the same Google account")}<div class="dialog-body">${calloutMarkup({ tone: "info", title: "Faculty can only read your mail so far", body: "Archiving a conversation or moving it to Trash needs Google’s permission to modify mail. Faculty would use it for nothing else: no permanent deletes, no other labels, no read marks." })}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="button" class="button primary" data-mail-allow>Allow…</button></footer></div>`);
    const allow = /** @type {HTMLButtonElement} */ (dialog().querySelector("[data-mail-allow]"));
    allow.onclick = () => void connect(allow, true);
  }
  /** Archive, as Gmail's does it: one click, the conversation leaves the Inbox (so Students too), and Undo brings it back.
   * @param {any} thread @param {HTMLButtonElement} button */
  async function archive(thread, button) {
    if (!status?.can_trash) { askToAllow(); return; }
    setBusy(button, true);
    try {
      await api(`${BASE}/threads/${thread.id}/archive`, "POST", { request_id: crypto.randomUUID() });
    } catch (cause) {
      setBusy(button, false);
      toast(errorMessage(cause), true);
      return;
    }
    // Students lists only the Inbox; Sent and All mail keep showing it.
    if (tab === "students") threads = threads.filter((item) => item.id !== thread.id);
    if (openId === thread.id) go({ thread: "" });
    else draw();
    toast("Archived. It is still in All mail.", { action: { label: "Undo", run: async () => {
      try {
        await api(`${BASE}/threads/${thread.id}/unarchive`, "POST", { request_id: crypto.randomUUID() });
        toast("Moved back to the Inbox.");
        listAt = 0;
        if (active()) void loadList(false, true);
      } catch (cause) { toast(errorMessage(cause), true); }
    } } });
    void loadList(false, true);
  }
  /** Puts an archived conversation back in the Inbox (it shows under Students again when a student wrote last).
   * @param {any} thread @param {HTMLButtonElement} button */
  async function unarchive(thread, button) {
    if (!status?.can_trash) { askToAllow(); return; }
    setBusy(button, true);
    try {
      await api(`${BASE}/threads/${thread.id}/unarchive`, "POST", { request_id: crypto.randomUUID() });
    } catch (cause) {
      setBusy(button, false);
      toast(errorMessage(cause), true);
      return;
    }
    if (reading?.id === thread.id) { reading = { ...reading, inbox: true }; draw(); }
    toast("Moved to the Inbox.");
    void loadList(false, true);
  }
  /** @param {any} thread */
  function openTrash(thread) {
    if (!status?.can_trash) { askToAllow(); return; }
    const count = thread.messages.length;
    showDialog(`<form>${dialogHeader("Move to Trash?", e(thread.subject))}<div class="dialog-body"><p>The whole conversation${count > 1 ? ` (${count} messages)` : ""} moves to Gmail’s Trash. Gmail keeps it there for 30 days, and you can undo right after.</p><p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button danger">Move to Trash</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("form"));
    const requestId = crypto.randomUUID();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        await api(`${BASE}/threads/${thread.id}/trash`, "POST", { request_id: requestId, confirmation: "trash" });
        dialog().close();
        threads = threads.filter((item) => item.id !== thread.id);
        go({ thread: "" });
        toast("Moved to Trash.", { action: { label: "Undo", run: async () => {
          try {
            await api(`${BASE}/threads/${thread.id}/untrash`, "POST", { request_id: crypto.randomUUID() });
            toast("Moved back.");
            listAt = 0;
            if (active()) void loadList(false, true);
          } catch (cause) { toast(errorMessage(cause), true); }
        } } });
        void loadList(false, true);
      });
    };
  }

  // -- Compose and review -------------------------------------------------------------------------------------------------
  /**
   * The compose window: fields, an optional class or TA picker, then a review that shows exactly what will be sent.
   * `attachments` start the message with Faculty files (by route); `onSent` hears about a send that may have gone out (accepted or
   * unconfirmed, never a refused one) with its request ID: a follow-up suggestion's Edit in Mail links its card to it.
   * @param {{to?: string, cc?: string, bcc?: string, subject?: string, body?: string, replyThreadId?: string, context?: string,
   *   attachments?: {route: string, name: string, size?: number}[], onSent?: (result: any, requestId: string) => unknown}} [prefill]
   */
  function openCompose(prefill = {}) {
    const draft = { to: prefill.to || "", cc: prefill.cc || "", bcc: prefill.bcc || "", subject: prefill.subject || "", body: prefill.body || "",
      /** @type {{name: string, size?: number, route?: string, title?: string, file?: File}[]} */
      attachments: (prefill.attachments || []).slice(0, MAX_ATTACHMENTS).map((file) => ({ route: file.route, name: file.name, size: file.size })) };
    const reply = prefill.replyThreadId || "";
    let libraryKind = "papers", libraryQuery = "";
    /** @type {any[] | null} */ let sections = null;
    let requestId = "";
    /** A reply to a Canvas message is posted in Canvas unless "Send by email instead" chose email (MAIL-CANVAS-POST). */
    let via = "";
    const title = reply ? "Reply" : "New message";
    function edit() {
      showDialog(`<form class="mail-compose" novalidate>${dialogHeader(title, prefill.context || (reply ? "Sent in the same Gmail thread" : "Sent from your Gmail account"))}<div class="dialog-body">
        <label class="field">To<input name="to" type="text" value="${e(draft.to)}" autocomplete="off" placeholder="name@tamu.edu, name@tamu.edu" /></label>
        <label class="field">Cc<input name="cc" type="text" value="${e(draft.cc)}" autocomplete="off" /></label>
        <label class="field">Bcc<input name="bcc" type="text" value="${e(draft.bcc)}" autocomplete="off" /><span class="field-hint">Everyone on Bcc gets one message and cannot see who else did.</span></label>
        ${disclosureMarkup({ label: "Add a class or TAs", key: "mail-audience", body: `<div class="mail-audience" data-mail-audience><label class="field">Section<select name="audience-section"><option value="">Choose a section…</option></select></label><button type="button" class="button" data-mail-add-students disabled>Add students to Bcc</button><button type="button" class="button" data-mail-add-tas disabled>Add TAs to Cc</button></div><p class="field-hint" data-mail-audience-note role="status"></p>` })}
        <label class="field">Subject<input name="subject" type="text" value="${e(draft.subject)}" maxlength="300" autocomplete="off" /></label>
        <label class="field">Message<textarea name="body" maxlength="20000">${e(draft.body)}</textarea></label>
        <div class="mail-attach"><div data-mail-attached>${attachedMarkup(draft.attachments)}</div>
          <div class="mail-attach-actions"><label class="button mail-attach-upload">Attach from your computer<input type="file" multiple class="sr-only" data-mail-attach-file></label>
            <button type="button" class="button" data-mail-attach-faculty aria-expanded="false">Attach from Faculty…</button>
            <span class="field-hint">Up to ${MAX_ATTACHMENTS} files, ${MAX_ATTACHED_BYTES / 1e6} MB together.</span></div>
          <div class="mail-library" data-mail-library hidden>${filterChipsMarkup("mail-library", LIBRARY_KINDS.map(([value, label]) => ({ value, label })), libraryKind, { label: "Faculty files" })}
            <label class="field" data-mail-library-search>Student<input type="search" name="library-q" value="${e(libraryQuery)}" autocomplete="off" placeholder="Name or email" /></label>
            <div data-mail-library-list role="status"></div></div></div>
        <p class="form-error" role="alert" hidden></p></div>
        <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Review</button></footer></form>`);
      const form = /** @type {HTMLFormElement} */ (dialog().querySelector("form.mail-compose"));
      const current = dialogSession(form, dialog());
      const read = () => { for (const key of /** @type {const} */ (["to", "cc", "bcc", "subject", "body"])) draft[key] = /** @type {HTMLInputElement} */ (form.elements.namedItem(key)).value; };
      const select = /** @type {HTMLSelectElement} */ (form.elements.namedItem("audience-section"));
      const note = /** @type {HTMLElement} */ (form.querySelector("[data-mail-audience-note]"));
      const students = /** @type {HTMLButtonElement} */ (form.querySelector("[data-mail-add-students]"));
      const tas = /** @type {HTMLButtonElement} */ (form.querySelector("[data-mail-add-tas]"));
      /** @type {any} */ let audience = null;
      let audienceRequest = 0;
      const fill = async () => {
        if (!sections) {
          try { sections = (await api(`${BASE}/sections`)).sections; } catch (cause) { note.textContent = errorMessage(cause); return; }
        }
        const list = sections || [];  // read into a constant: the await above ends the null check's narrowing
        select.innerHTML = `<option value="">Choose a section…</option>${list.map((s, index) => `<option value="${index}">${e(`${s.code} · ${s.term_label} · Section ${s.number} (${s.students})`)}</option>`).join("")}`;
      };
      form.querySelector("details")?.addEventListener("toggle", () => void fill(), { once: true });
      if (form.querySelector("details")?.open) void fill();
      select.onchange = async () => {
        const request = ++audienceRequest;
        audience = null; students.disabled = tas.disabled = true; note.textContent = "";
        const section = sections?.[Number(select.value)];
        if (!section || select.value === "") return;
        try {
          const found = await api(`${BASE}/audience?${new URLSearchParams({ group: section.group_id, section: String(section.number) })}`);
          if (request !== audienceRequest || !current()) return;
          audience = found;
        } catch (cause) { if (request === audienceRequest && current()) note.textContent = errorMessage(cause); return; }
        students.disabled = !audience.students.length; tas.disabled = !audience.tas.length;
        students.textContent = `Add ${audience.students.length} student${audience.students.length === 1 ? "" : "s"} to Bcc`;
        tas.textContent = `Add ${audience.tas.length} TA${audience.tas.length === 1 ? "" : "s"} to Cc`;
        note.textContent = audience.skipped.length ? `Left out: ${audience.skipped.map((/** @type {any} */ s) => `${s.name} (${s.reason})`).join("; ")}.` : "";
      };
      students.onclick = () => { read(); draft.bcc = addAddresses(draft.bcc, audience.students.map((/** @type {any} */ s) => s.email)); /** @type {HTMLInputElement} */ (form.elements.namedItem("bcc")).value = draft.bcc; };
      tas.onclick = () => { read(); draft.cc = addAddresses(draft.cc, audience.tas.map((/** @type {any} */ s) => s.email)); /** @type {HTMLInputElement} */ (form.elements.namedItem("cc")).value = draft.cc; };
      // -- Attachments: files from the computer go with the preview and the send; files from Faculty are named by their routes.
      const attachedHost = /** @type {HTMLElement} */ (form.querySelector("[data-mail-attached]"));
      const library = /** @type {HTMLElement} */ (form.querySelector("[data-mail-library]"));
      const libraryList = /** @type {HTMLElement} */ (form.querySelector("[data-mail-library-list]"));
      const search = /** @type {HTMLElement} */ (form.querySelector("[data-mail-library-search]"));
      const searchInput = /** @type {HTMLInputElement} */ (form.elements.namedItem("library-q"));
      const toggle = /** @type {HTMLButtonElement} */ (form.querySelector("[data-mail-attach-faculty]"));
      /** @type {any} */ let answer = null;
      let asked = 0;
      const drawLibrary = () => { if (answer) libraryList.innerHTML = libraryMarkup(answer, new Set(draft.attachments.map((item) => item.route || ""))); };
      const drawAttached = () => { attachedHost.innerHTML = attachedMarkup(draft.attachments); drawLibrary(); };
      const full = () => {
        if (draft.attachments.length < MAX_ATTACHMENTS) return false;
        toast(`A message can carry at most ${MAX_ATTACHMENTS} attachments.`, true);
        return true;
      };
      const loadLibrary = async () => {
        search.hidden = libraryKind !== "papers";
        const query = libraryKind === "papers" ? searchInput.value.trim() : "";
        const ticket = ++asked;
        libraryList.innerHTML = loadingMarkup("Reading Faculty's files…", 2);
        try {
          const found = await api(`${BASE}/files?${new URLSearchParams({ kind: libraryKind, q: query })}`);
          if (ticket !== asked) return;
          answer = found; libraryQuery = query; drawLibrary();
        } catch (cause) { if (ticket === asked) { answer = null; libraryList.innerHTML = `<p class="form-error">${e(errorMessage(cause))}</p>`; } }
      };
      toggle.onclick = () => {
        library.hidden = !library.hidden;
        toggle.setAttribute("aria-expanded", String(!library.hidden));
        if (!library.hidden && !answer) {
          // A student's papers start from the first student the message is addressed to.
          if (libraryKind === "papers" && !searchInput.value) searchInput.value = addressList(/** @type {HTMLInputElement} */ (form.elements.namedItem("to")).value)[0] || "";
          void loadLibrary();
        }
      };
      bindFilterChips(library, "mail-library", (value) => { libraryKind = value; answer = null; void loadLibrary(); });
      let typing = 0;
      searchInput.oninput = () => { clearTimeout(typing); typing = window.setTimeout(() => void loadLibrary(), 350); };
      searchInput.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); clearTimeout(typing); void loadLibrary(); } };
      libraryList.onclick = (event) => {
        const button = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest("[data-mail-pick]"));
        if (!button || !answer || full()) return;
        const [g, i] = String(button.dataset.mailPick).split(":").map(Number);
        const item = answer.groups?.[g]?.items?.[i];
        if (!item?.route || draft.attachments.some((other) => other.route === item.route)) return;
        draft.attachments.push({ route: item.route, name: item.name, size: item.size, title: item.title });
        drawAttached();
      };
      /** @type {HTMLInputElement} */ (form.querySelector("[data-mail-attach-file]")).onchange = (event) => {
        const input = /** @type {HTMLInputElement} */ (event.target);
        for (const file of Array.from(input.files || [])) {
          if (full()) break;
          draft.attachments.push({ file, name: file.name, size: file.size });
        }
        input.value = "";
        drawAttached();
      };
      attachedHost.onclick = (event) => {
        const button = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest("[data-mail-detach]"));
        if (!button) return;
        draft.attachments.splice(Number(button.dataset.mailDetach), 1);
        drawAttached();
      };
      form.onsubmit = (event) => {
        event.preventDefault();
        read();
        // Review only reads (the preview); its header Close stays usable, and a preview that comes back after the window was
        // closed, or replaced by a newer one, is dropped by `current` (nothing is sent or drawn).
        void runFormAction(form, async () => review(current), { keepClose: true });
      };
    }
    const fields = () => {
      let upload = 0;
      const attachments = draft.attachments.map((item) => item.file ? { upload: upload++, name: item.name } : { route: item.route, name: item.name });
      return { to: addressList(draft.to), cc: addressList(draft.cc), bcc: addressList(draft.bcc), subject: draft.subject, body: draft.body,
        ...(reply ? { reply_thread_id: reply } : {}), ...(attachments.length ? { attachments } : {}), ...(via ? { via } : {}) };
    };
    /**
     * The request: JSON, or, with files from the computer, a form holding the fields as JSON and each file in the order the fields
     * name them (the preview and the send carry the same files; the fingerprint covers their digests).
     * @param {Record<string, unknown>} [extra]
     */
    const payload = (extra = {}) => {
      const message = { ...fields(), ...extra };
      const local = draft.attachments.filter((item) => item.file);
      if (!local.length) return message;
      const form = new FormData();
      form.append("message", JSON.stringify(message));
      for (const item of local) form.append("file", /** @type {File} */ (item.file), item.name);
      return form;
    };
    const wait = () => draft.attachments.some((item) => item.file) ? UPLOAD_WAIT : undefined;
    async function review(current = () => true) {
      const preview = await api(`${BASE}/preview`, "POST", payload(), {}, wait());
      if (!current()) return;
      requestId = crypto.randomUUID();
      const m = preview.message;
      const list = (/** @type {string[]} */ items) => items.length ? e(items.join(", ")) : "—";
      const posted = m.via === "canvas";
      const where = posted
        ? `<dt>Where</dt><dd>Canvas: your conversation with ${e(m.canvas.recipient_name)}${m.canvas.course ? ` (${e(m.canvas.course)})` : ""}</dd>`
        : `<dt>To</dt><dd>${list(m.to)}</dd>${m.cc.length ? `<dt>Cc</dt><dd>${list(m.cc)}</dd>` : ""}${m.bcc.length ? `<dt>Bcc</dt><dd>${m.bcc.length} address${m.bcc.length === 1 ? "" : "es"}: ${list(m.bcc)}</dd>` : ""}<dt>Subject</dt><dd>${e(m.subject)}</dd>`;
      const go = posted ? "Post in Canvas" : `Send to ${preview.recipients} recipient${preview.recipients === 1 ? "" : "s"}`;
      // MAIL-SEND-LATER: a new message may go at a chosen time; a reply (its thread may move on) and a file from the computer (never
      // kept) may not.
      const later = !posted && !reply && !draft.attachments.some((item) => item.file);
      let scheduling = false;
      showDialog(`<form class="mail-review">${dialogHeader("Review before sending", posted ? "As you, in Canvas" : `From ${e(preview.sender)}`)}<div class="dialog-body"><dl>${where}${m.attachments?.length ? `<dt>Attached</dt><dd>${attachedMarkup(m.attachments.map((/** @type {any} */ f) => ({ ...f, file: f.from === "computer" })), { removable: false })}</dd>` : ""}</dl><pre class="mail-text">${e(m.text)}</pre><div class="send-checks" data-send-checks aria-live="polite"></div>${preview.notes.map((/** @type {string} */ n) => `<p class="field-hint">${e(n)}</p>`).join("")}${later ? laterFieldMarkup(e) : ""}<p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-mail-back>Back to edit</button>${later ? '<button type="button" class="button" data-mail-later>Send later…</button>' : ""}${posted ? '<button type="button" class="button" data-mail-by-email>Send by email instead</button>' : ""}${preview.canvas_sign_in && canvasSignIn ? '<button type="button" class="button" data-mail-canvas-sign-in>Sign in to Canvas</button>' : ""}<button type="submit" class="button primary">${go}</button></footer></form>`);
      const form = /** @type {HTMLFormElement} */ (dialog().querySelector("form.mail-review"));
      const reviewing = dialogSession(form, dialog());
      // JEV SEND-CHECKS: advice beside the review; Send never waits for it.
      void fillChecks(form.querySelector("[data-send-checks]"), () => api(`${BASE}/check`, "POST", payload(), {}, wait()), e);
      /** @type {HTMLElement} */ (form.querySelector("[data-mail-back]")).onclick = edit;
      const byEmail = form.querySelector("[data-mail-by-email]");
      if (byEmail instanceof HTMLElement) byEmail.onclick = () => { via = "email"; void runFormAction(form, async () => review(reviewing)); };
      const signIn = form.querySelector("[data-mail-canvas-sign-in]");
      if (signIn instanceof HTMLElement) signIn.onclick = () => { void signInThenReview(); };
      const laterButton = form.querySelector("[data-mail-later]");
      if (laterButton instanceof HTMLElement) laterButton.onclick = () => {
        scheduling = true;
        laterButton.hidden = true;
        const field = /** @type {HTMLElement} */ (form.querySelector("[data-send-later]"));
        field.hidden = false;
        /** @type {HTMLElement} */ (form.querySelector('button[type="submit"]')).textContent = "Schedule";
        /** @type {HTMLInputElement} */ (form.elements.namedItem("send_at")).focus();
      };
      form.onsubmit = (event) => {
        event.preventDefault();
        void runFormAction(form, async () => {
          if (scheduling) {
            const sendAt = /** @type {HTMLInputElement} */ (form.elements.namedItem("send_at")).value;
            const kept = await api(`${BASE}/schedule`, "POST", { ...fields(), fingerprint: preview.fingerprint, request_id: requestId, confirmation: "schedule", send_at: sendAt });
            if (reviewing()) dialog().close();
            toast(`Scheduled for ${campusText(kept.send_at)}. It is listed under Scheduled.`);
            scheduled = null;
            if (active() && showScheduled) void loadScheduled();
            return;
          }
          const result = await api(`${BASE}/send`, "POST", payload({ fingerprint: preview.fingerprint, request_id: requestId, confirmation: "send" }), {}, wait());
          finished(result, reviewing);
        });
      };
    }
    /** Signed out of Canvas (MAIL-CANVAS-POST): Canvas's sign-in window (Duo included), then the review again, which posts in Canvas
     * once Faculty holds a session. Closing the window first reviews again too: the message is never lost. */
    async function signInThenReview() {
      if (canvasSignIn) await canvasSignIn();
      via = "";
      try {
        await review();
      } catch (cause) {
        edit();
        toast(errorMessage(cause), true);
      }
    }
    function finished(/** @type {any} */ result, current = () => true) {
      const canvas = result.via === "canvas";
      if (prefill.onSent && result.status !== "failed") void Promise.resolve(prefill.onSent(result, requestId)).catch(() => {});
      if (result.status === "accepted") {
        if (current()) dialog().close();
        toast(canvas ? `Posted in Canvas to ${result.canvas.recipient_name}.` : `Sent to ${result.recipients} recipient${result.recipients === 1 ? "" : "s"}.`);
      } else if (!current()) {
        toast(result.detail || (result.status === "failed" ? (canvas ? "Canvas did not take the post." : "Gmail did not send the message.")
          : canvas ? "Could not confirm the post. Look in the Canvas conversation." : "Could not confirm the send. Check your Gmail Sent folder."), true);
      } else if (canvas) {
        showDialog(`<div>${dialogHeader(result.status === "failed" ? "Not posted" : "Could not confirm the post", "")}<div class="dialog-body">${calloutMarkup({ tone: result.status === "failed" ? "danger" : "warning", title: result.status === "failed" ? "Canvas did not take it" : "Look in the Canvas conversation", body: result.detail || "Canvas did not confirm the post. Faculty will never post it again by itself." })}</div><footer class="dialog-footer"><button type="button" class="button primary" data-action="close">Close</button></footer></div>`);
      } else {
        showDialog(`<div>${dialogHeader(result.status === "failed" ? "Not sent" : "Could not confirm the send", "")}<div class="dialog-body">${calloutMarkup({ tone: result.status === "failed" ? "danger" : "warning", title: result.status === "failed" ? "Gmail did not send it" : "Check your Gmail Sent folder", body: result.status === "failed" ? result.detail : "Google did not confirm delivery. Faculty will never send this message again by itself; look in Gmail's Sent folder before writing it again." })}</div><footer class="dialog-footer"><button type="button" class="button primary" data-action="close">Close</button></footer></div>`);
      }
      sent = null;
      if (active()) { void loadList(false, true); if (showSent) void loadSent(); }
    }
    edit();
  }

  // -- A student's mail (from a student's card: the roster's student window, the gradebook's record) ----------------------------
  /** Their threads, read from Gmail by the roster address, and Write to them…; with `write`, straight to a message to that address
   * (the gradebook's record knows no address: the server reads it from the roster). `back` returns to the card it was opened from.
   * `onSent` goes to the compose window (openCompose): a follow-up's Write to student… records the send it made. `reply` (a
   * follow-up's email conversation, SAME MATTER) writes in the student's own Gmail thread with its "Re:" subject.
   * @param {{group: string, section: number, studentId: string, name: string, context?: string, write?: boolean,
   *   back?: {label: string, run: () => void} | null, onSent?: (result: any, requestId: string) => unknown,
   *   reply?: {replyThreadId: string, subject: string} | null}} student */
  async function openStudentMail({ group, section, studentId, name, context = "", write = false, back = null, onSent = undefined, reply = null }) {
    const backButton = back ? `<button type="button" class="button" data-dialog-back>${e(back.label)}</button>` : "";
    const bindBack = () => {
      const button = dialog().querySelector("[data-dialog-back]");
      if (button instanceof HTMLElement && back) button.onclick = () => back.run();
    };
    showDialog(`<div>${dialogHeader(`Mail with ${e(name)}`, e(context))}<div class="dialog-body">${loadingMarkup(write ? "Finding their address…" : "Reading Gmail…", 3)}</div><footer class="dialog-footer">${backButton}<button type="button" class="button" data-action="close">Cancel</button></footer></div>`);
    bindBack();
    const opened = dialog().firstElementChild;
    /** @type {any} */ let found;
    /** @type {any} */ let state = null;
    try {
      state = await api(BASE);
      if (state.permission) found = await api(`${BASE}/student?${new URLSearchParams({ group, section: String(section), student: studentId })}`);
    } catch (cause) {
      // Cancelled (closed) or replaced while it read: a failure afterwards opens nothing.
      if (dialog().firstElementChild !== opened || !dialog().open) return;
      showDialog(`<div>${dialogHeader(`Mail with ${e(name)}`, e(context))}<div class="dialog-body">${calloutMarkup({ tone: "danger", title: "Mail could not be read", body: errorMessage(cause) })}</div><footer class="dialog-footer">${backButton}<button type="button" class="button primary" data-action="close">Close</button></footer></div>`);
      bindBack();
      return;
    }
    if (dialog().firstElementChild !== opened || !dialog().open) return;
    if (write && found?.email) { openCompose({ to: found.email, context: `To ${name}`, onSent, ...(reply?.replyThreadId ? reply : {}) }); return; }
    let body;
    if (!state.permission) body = `${calloutMarkup({ tone: "info", title: "Mail is not connected", body: "Connect Gmail on the Mail page to see a student's mail here." })}<p><a class="link-arrow" href="#mail" data-action="close">Open Mail</a></p>`;
    else if (!found.email) body = calloutMarkup({ tone: "info", title: "No mail to show", body: found.reason });
    else body = `<p class="field-hint">Threads with ${e(found.email)}, read from Gmail just now.</p>${found.threads.length ? `<ul class="mail-list">${found.threads.map(rowMarkup).join("")}</ul>` : "<p>No mail with this student yet.</p>"}`;
    showDialog(`<div class="mail-student">${dialogHeader(`Mail with ${e(name)}`, e(context))}<div class="dialog-body">${body}</div><footer class="dialog-footer">${backButton}<button type="button" class="button" data-action="close">Close</button>${found?.email ? '<button type="button" class="button primary" data-mail-write>Write to them…</button>' : ""}</footer></div>`);
    bindBack();
    const d = dialog();
    d.querySelectorAll("[data-mail-thread]").forEach((node) => {
      /** @type {HTMLElement} */ (node).onclick = () => { d.close(); location.hash = `#mail?thread=${/** @type {HTMLElement} */ (node).dataset.mailThread}`; };
    });
    const writeButton = d.querySelector("[data-mail-write]");
    if (writeButton instanceof HTMLElement) writeButton.onclick = () => openCompose({ to: found.email, context: `To ${name}`, onSent });
  }

  // -- The view -----------------------------------------------------------------------------------------------------------
  async function render(force = false) {
    const host = root();
    if (!host || !active()) return;
    readRoute();
    bind();
    header?.set({ route: location.hash, title: "Mail", subtitle: "Read your Gmail, see a student’s mail, and write from Faculty.", crumbs: [{ label: "Home", href: "#overview" }, { label: "Mail" }], action: status?.connected ? { label: "Compose", action: "mail-compose", icon: "plus" } : null });
    draw();
    if (!status || force) await loadStatus();
    if (!active()) return;
    header?.set({ route: location.hash, title: "Mail", subtitle: "Read your Gmail, see a student’s mail, and write from Faculty.", crumbs: [{ label: "Home", href: "#overview" }, { label: "Mail" }], action: status?.connected ? { label: "Compose", action: "mail-compose", icon: "plus" } : null });
    if (!status?.permission) { draw(); if (composeRequested && status?.connected) { composeRequested = false; openCompose(); } return; }
    if (showSent) { if (!sent || force) await loadSent(); } else if (showScheduled) { if (!scheduled || force) await loadScheduled(); } else if (!listAt || force) await loadList();
    if (!active()) return;
    if (openId && !reading && !readingError) await loadThread();
    if (!active()) return;
    draw();
    if (composeRequested) { composeRequested = false; openCompose(); }
  }
  /** The shell's poll: the open list is read again quietly when it is old and no window is open. */
  async function refresh() {
    if (!active() || !status?.permission || showSent || showScheduled || dialog().open || loading || Date.now() - listAt < EVERY) return;
    await loadList();
  }
  return { render: (force = false) => void render(force), refresh, openCompose, openStudentMail, compose: (prefill = {}) => openCompose(prefill) };
}
