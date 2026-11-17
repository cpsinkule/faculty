// @ts-check
// A student's communications record (communications/, AGENTS.md "Communications record"): every message with them that Faculty
// captured and kept from Gmail, Canvas and Gradescope, and Faculty's own sends and their Response Card answers, newest first, in
// one timeline per student for the term. Read only: message text is shown escaped, kept files open in the app's file viewer (pictures of their pages, re-encoded
// pictures or escaped text: never the file itself as a page) with Download, links go only to
// Faculty's own pages or to https addresses, and Capture now only asks the worker to run a round.
import { calloutMarkup, emptyMarkup, loadingMarkup, errorMessage, setBusy } from "./ui.js";
import { whenText, sizeText } from "./mail.js";
import { viewLink } from "./file-viewer.js";
import { patchChildren } from "./dom-patch.js";

const BASE = "/api/communications";
/** The timeline's filters: [key, label]. */
export const FILTERS = /** @type {const} */ ([["all", "All"], ["email", "Email"], ["canvas", "Canvas"], ["gradescope", "Gradescope"], ["faculty", "Sent by Faculty"], ["meeting", "Meetings"]]);
const CHANNELS = ["gmail", "canvas", "gradescope"]; // the status lines' order (the server's JSON sorts its keys)
const POLL = 3000;
const POLL_FOR = 180000; // Capture now is watched this long, then the timeline is simply read again

/** Which filter an entry falls under. @param {{channel: string, context?: {canvas_notice?: boolean}}} entry */
export function channelGroup(entry) {
  if (entry.context?.canvas_notice) return "canvas"; // a Canvas message as Canvas emailed it
  if (entry.channel === "gmail") return "email";
  if (entry.channel.startsWith("canvas")) return "canvas";
  if (entry.channel === "gradescope_regrade") return "gradescope";
  if (entry.channel === "meeting") return "meeting"; // a recorded meeting, by reference (RECORDINGS "Student meetings")
  return "faculty";
}

/** Whether an entry is shown for a filter and a search (every word, in any order, anywhere in who, subject and text).
 * @param {any} entry @param {string} filter @param {string} query */
export function matches(entry, filter, query) {
  if (filter !== "all" && channelGroup(entry) !== filter) return false;
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const people = [entry.author, ...(entry.recipients || [])].map((person) => `${person?.name || ""} ${person?.email || ""}`).join(" ");
  const haystack = `${people} ${entry.subject || ""} ${entry.text || ""} ${entry.channel_label || ""} ${entry.context?.source || ""}`.toLowerCase();
  return words.every((word) => haystack.includes(word));
}

/** One channel's line in the capture status: what it kept and when, or why it waits. @param {any} channel @param {number} [now] */
export function channelLine(channel, now = Date.now()) {
  if (!channel.ready) return channel.setup || "Not set up";
  if (channel.requested) return "Capturing now…";
  if (channel.status === "waiting" || channel.status === "failed") return channel.detail || (channel.status === "failed" ? "The last capture failed" : "Waiting");
  if (!channel.last_success_at) return "Not captured yet";
  const minutes = Math.round((now - Date.parse(channel.last_success_at)) / 60000);
  const ago = !Number.isFinite(minutes) ? "" : minutes < 1 ? "just now" : minutes < 60 ? `${minutes} min ago` : `at ${whenText(channel.last_success_at, now)}`;
  return `${channel.kept} kept · captured ${ago}`;
}

/** What a message's file list says after the name of a file the student linked from Google Drive (LINKED DRIVE FILES): where a
 * kept one came from, or why one was not fetched. '' for an attachment the message carried. Pure. @param {any} file */
export function linkedNote(file) {
  if (file?.linked !== "google_drive") return "";
  return file.kept === false ? `not fetched from Google Drive${file.reason ? `: ${file.reason}` : ""}` : "from Google Drive";
}

/** Whether a message's words link a file on Google Drive (as sent, or as the mail filter rewrote the link). Pure. @param {unknown} text */
export function linksDrive(text) {
  return /https:\/\/(?:drive|docs)\.google\.com\//.test(String(text || ""));
}

/** Asks Google for permission to read Drive files and goes there (the instructor's own click).
 * @param {any} api @param {HTMLButtonElement} button @param {Function} toast */
export async function allowDrive(api, button, toast) {
  setBusy(button, true);
  try {
    location.assign((await api(`${BASE}/drive/connect`, "POST", {})).url);
  } catch (cause) {
    setBusy(button, false);
    toast(errorMessage(cause), true);
  }
}

/** The tone of a channel's chip. @param {any} channel */
export function channelTone(channel) {
  if (!channel.ready || channel.status === "waiting") return "warning";
  if (channel.status === "failed") return "danger";
  return channel.last_success_at ? "success" : "neutral";
}

/** "85 students": how many a mass message reached. @param {{mass?: {students: number}}} entry */
export function massText(entry) {
  const count = entry.mass?.students || 0;
  return `${count} student${count === 1 ? "" : "s"}`;
}

/** The timeline's entries with each run of two or more consecutive class-wide (`mass`) messages gathered into one item, in order:
 * `{entry}` or `{run: [entries]}` (newest first, as the timeline). A single class-wide message stays an entry of its own.
 * @param {any[]} entries @returns {({entry: any} | {run: any[]})[]} */
export function massRuns(entries) {
  /** @type {({entry: any} | {run: any[]})[]} */
  const items = [];
  /** @type {any[]} */
  let run = [];
  const flush = () => {
    if (run.length > 1) items.push({ run });
    else if (run.length) items.push({ entry: run[0] });
    run = [];
  };
  for (const entry of entries) {
    if (entry.mass) { run.push(entry); continue; }
    flush();
    items.push({ entry });
  }
  flush();
  return items;
}

/** A student's first name for a button ("Write to Alice…"): the part after the comma of "Example, Alice", else the first word.
 * @param {string} name */
export function firstName(name) {
  const text = String(name || "").trim();
  const [family, given] = text.includes(",") ? text.split(/,(.*)/s) : ["", text];
  return (given || family).trim().split(/\s+/)[0] || "";
}

/** Who wrote an entry, as the timeline says it: the timeline's student by their roster name when they wrote it (whatever name
 * their mail program sent), another student or anyone else by the name the message carries. @param {any} entry @param {string} student */
export function authorText(entry, student) {
  if (entry.author_role === "instructor") return entry.channel === "faculty_mail" ? "Faculty, for you" : "You";
  if (entry.author_role === "student" && (entry.roles || []).includes("author")) return student;
  if (entry.author_role === "student") return entry.author?.name || entry.author?.email || "Another student";
  return entry.author?.name || entry.author?.email || "Someone else";
}

/** A Faculty page an entry opens, or "" (only Mail's own thread route, for a hex Gmail thread ID). @param {any} entry */
export function mailHref(entry) {
  const thread = entry.context?.thread_id || entry.context?.gmail?.thread_id || "";
  return /^[0-9a-f]{1,32}$/.test(thread) ? `#mail?thread=${thread}` : "";
}

/** The page of a recorded meeting's entry: the recording at that student's part, or "" (only Faculty's own recording page).
 * @param {any} entry */
export function recordingHref(entry) {
  const href = String(entry?.context?.href || "");
  return entry?.channel === "meeting" && /^#recordings\/[A-Za-z0-9-]{1,64}(\?t=\d{1,12}(&to=\d{1,12})?)?$/.test(href) ? href : "";
}

/** An https address an entry names (Canvas, Gradescope), or "". @param {any} entry */
export function outsideHref(entry) {
  const url = String(entry.context?.url || "");
  try {
    return new URL(url).protocol === "https:" ? url : "";
  } catch {
    return "";
  }
}

/**
 * @param {{api: Function, escape: (value: string) => string, toast: Function, showDialog: Function, dialogHeader: Function, studentSupport?: Function}} tools
 */
export function createCommunications({ api, escape, toast, showDialog, dialogHeader, studentSupport }) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));

  /** @param {string} enrollmentId */
  // THE COORDINATOR'S MAIL: a coordinator's follow-up names the term's coordinator key where a student's names the enrollment.
  const read = (enrollmentId) => api(String(enrollmentId).startsWith("coordinator:")
    ? `${BASE}/coordinators/${encodeURIComponent(String(enrollmentId).slice("coordinator:".length))}` : `${BASE}/students/${encodeURIComponent(enrollmentId)}`);

  /** @param {any} status */
  function statusMarkup(status) {
    const channels = CHANNELS.map((key) => ({ key, channel: status?.channels?.[key] })).filter(({ channel }) => channel);
    const asked = channels.some(({ channel }) => channel.requested);
    // Gmail is connected on the Mail page: its line links there when it cannot capture yet.
    const where = (/** @type {string} */ key, /** @type {any} */ channel) => key === "gmail" && !channel.ready ? '<a class="text-button" href="#mail" data-action="close">Open Mail</a>' : "";
    // LINKED DRIVE FILES: said only while Faculty may not read Drive; allowing it is one click, for the account already connected.
    const drive = status?.drive && !status.drive.ready ? `<li><span class="chip" data-tone="warning">Google Drive</span><span>${e(status.drive.setup)}</span>${status.drive.connected ? '<button type="button" class="text-button" data-comm-drive>Allow Faculty to read it</button>' : ""}</li>` : "";
    return `<div class="comm-status" data-comm-status><ul>${channels.map(({ key, channel }) => `<li><span class="chip" data-tone="${channelTone(channel)}">${e(channel.label)}</span><span>${e(channelLine(channel))}</span>${where(key, channel)}</li>`).join("")}${drive}</ul>
      <button type="button" class="button" data-comm-capture${asked ? " disabled" : ""}>${asked ? "Capturing…" : "Capture now"}</button></div>`;
  }

  /** @param {any} entry @param {string} student */
  function entryMarkup(entry, student) {
    const to = (entry.recipients || []).filter((/** @type {any} */ person) => person.name || person.email).slice(0, 6)
      .map((/** @type {any} */ person) => `${person.kind && person.kind !== "to" ? `${person.kind}: ` : ""}${person.name || person.email}`);
    const status = entry.status && entry.status !== "accepted" ? `<span class="chip" data-tone="${entry.status === "failed" ? "danger" : "warning"}">${e(entry.status === "pending" ? "queued" : entry.status)}</span>` : "";
    const source = entry.context?.source ? `<span class="comm-source">${e(entry.context.source)}${entry.context.card ? ` · ${e(entry.context.card)}` : ""}</span>` : "";
    const files = (entry.attachments || []).map((/** @type {any} */ file) => {
      const label = `${e(file.name || "attachment")}${file.size ? ` <small>${e(sizeText(file.size))}</small>` : ""}`;
      if (file.kept === false) return `<li>${label} <small>${file.linked ? e(linkedNote(file)) : `not kept${file.reason ? `: ${e(file.reason)}` : ""}`}</small></li>`;
      const href = entry.key.startsWith("seat-mail:") ? `${BASE}/seat-mail/${encodeURIComponent(entry.key.slice(10))}/chart.pdf`
        : entry.message_id ? `${BASE}/messages/${encodeURIComponent(entry.message_id)}/attachments/${file.index}` : "";
      return href ? `<li><a ${viewLink(href, file.name || "attachment", e)}>${label}</a>${file.linked ? ` <small>${e(linkedNote(file))}</small>` : ""}</li>` : `<li>${label}</li>`;
    }).join("");
    const links = [
      entry.original && entry.message_id ? `<a class="text-button" ${viewLink(`${BASE}/messages/${encodeURIComponent(entry.message_id)}/original`, "Original message", e)}>Original message</a>` : "",
      mailHref(entry) ? `<a class="text-button" href="${mailHref(entry)}" data-action="close">Open in Mail</a>` : "",
      entry.email_copy ? `<a class="text-button" ${viewLink(`${BASE}/messages/${encodeURIComponent(entry.email_copy)}/original`, "Email copy", e)}>Email copy</a>` : "",
      outsideHref(entry) ? `<a class="text-button" href="${e(outsideHref(entry))}" target="_blank" rel="noopener noreferrer">Open in ${channelGroup(entry) === "canvas" ? "Canvas" : "Gradescope"}</a>` : "",
      recordingHref(entry) ? `<a class="text-button" href="${e(recordingHref(entry))}" data-action="close">Open the recording</a>` : "",
      entry.edited && entry.message_id ? `<button type="button" class="text-button" data-comm-revisions="${e(entry.message_id)}">Earlier versions</button>` : "",
    ].filter(Boolean).join("");
    const time = `<time datetime="${e(entry.at)}" title="${e(new Date(entry.at).toLocaleString())}">${e(whenText(entry.at))}</time>`;
    const body = `${entry.subject ? `<p class="comm-subject">${e(entry.subject)}${entry.edited ? ' <small>(edited)</small>' : ""}</p>` : ""}${source}
      ${!entry.text ? "" : entry.mass ? `<pre class="comm-mass-text">${e(entry.text)}</pre>`
        : `<details class="comm-text"${entry.text.length <= 600 ? " open" : ""}><summary>${e(entry.text.length <= 600 ? "Message" : `${entry.text.slice(0, 160).replace(/\s+/g, " ")}…`)}</summary><pre>${e(entry.text)}</pre></details>`}
      ${files ? `<ul class="comm-files" aria-label="Attachments">${files}</ul>` : ""}${links ? `<div class="comm-links">${links}</div>` : ""}<div data-comm-history></div>`;
    if (entry.mass) {
      // A message to the whole class (requirement, 2026-09-30): one folded line; opening it shows the message as any other.
      return `<li class="comm-entry comm-mass" data-patch-key="entry:${e(entry.key)}"><details class="comm-mass-fold">
        <summary class="comm-entry-head"><span class="chip" data-tone="neutral">${e(entry.channel_label)}</span><strong>${e(authorText(entry, student))}</strong>
          <span class="comm-to">to ${e(massText(entry))}</span><span class="comm-mass-subject">${e(entry.subject || entry.text.slice(0, 80))}</span>${status}${time}</summary>
        ${to.length ? `<p class="comm-to comm-mass-to">To ${e(to.join(", "))}${entry.recipients.length > to.length ? ` and ${entry.recipients.length - to.length} more` : ""}</p>` : ""}${body}</details></li>`;
    }
    return `<li class="comm-entry" data-patch-key="entry:${e(entry.key)}">
      <div class="comm-entry-head"><span class="chip" data-tone="neutral">${e(entry.channel_label)}</span><strong>${e(authorText(entry, student))}</strong>
        ${to.length ? `<span class="comm-to">to ${e(to.join(", "))}</span>` : ""}${status}${time}</div>
      ${body}</li>`;
  }

  /** Consecutive class-wide messages as one folded line ("9 class-wide messages · Sep 2 – Sep 30"); each keeps its own fold inside.
   * @param {any[]} run @param {string} student @param {Set<string>} open */
  function runMarkup(run, student, open) {
    // Keyed by its oldest message, which stays the same when a newer class-wide message arrives at the top (an open run stays open).
    const last = run[run.length - 1], newest = whenText(run[0].at), oldest = whenText(last.at);
    return `<li class="comm-mass-run" data-patch-key="mass:${e(last.key)}"><details data-comm-run="${e(last.key)}"${open.has(last.key) ? " open" : ""}><summary class="comm-entry-head">
      <span class="chip" data-tone="neutral">Class-wide</span><strong>${e(`${run.length} class-wide messages`)}</strong><span class="comm-mass-subject">${e(oldest === newest ? newest : `${oldest} – ${newest}`)}</span></summary>
      <ol class="comm-list">${run.map((entry) => entryMarkup(entry, student)).join("")}</ol></details></li>`;
  }

  /**
   * The timeline of one student in one term, in the shared dialog. `onMail` searches Gmail live, `onWrite` writes to them, and
   * `back` returns to the student's card it was opened from.
   * @param {{enrollmentId: string, name: string, context?: string, onMail?: (() => void) | null, onWrite?: (() => void) | null,
   *   back?: {label: string, run: () => void} | null}} info
   */
  async function openTimeline({ enrollmentId, name, context = "", onMail = null, onWrite = null, back = null }) {
    const title = `Communications with ${name}`;
    showDialog(`<div class="comm-dialog">${dialogHeader(e(title), e(context))}<div class="dialog-body">${loadingMarkup("Reading the record…", 4)}</div></div>`);
    const host = dialog();  // wide while the timeline is shown (communications.css), not the windows opened from it
    const opened = host.firstElementChild;
    let filter = "all", query = "", data = /** @type {any} */ (null), timer = 0, until = 0;
    const alive = () => host.open && host.firstElementChild === opened;
    // Runs of class-wide messages the instructor opened stay open while the list is drawn again (a search keystroke, a filter).
    /** @type {Set<string>} */
    const openRuns = new Set();
    const dialogBody = /** @type {HTMLElement} */ (host.querySelector(".dialog-body"));
    dialogBody.addEventListener("toggle", (event) => {
      const run = /** @type {HTMLElement} */ (event.target);
      if (!(run instanceof HTMLDetailsElement) || !run.dataset.commRun) return;
      if (run.open) openRuns.add(run.dataset.commRun); else openRuns.delete(run.dataset.commRun);
    }, true);

    const draw = () => {
      if (!alive()) return;
      const body = dialogBody;
      const shown = data.entries.filter((/** @type {any} */ entry) => matches(entry, filter, query));
      const counts = Object.fromEntries(FILTERS.map(([key]) => [key, data.entries.filter((/** @type {any} */ entry) => matches(entry, key, "")).length]));
      // Unfiltered, consecutive class-wide messages fold together; a filter or a search lists every entry on its own.
      const items = filter === "all" && !query.trim() ? massRuns(shown) : shown.map((/** @type {any} */ entry) => ({ entry }));
      const list = !data.entries.length
        ? emptyMarkup({ icon: "mail", title: "Nothing in the record yet", body: "Mail, Canvas messages and comments, Gradescope regrade requests and what Faculty sent this student appear here as they are captured." })
        : shown.length ? `<ol class="comm-list">${items.map((/** @type {{entry: any} | {run: any[]}} */ item) => "run" in item ? runMarkup(item.run, name, openRuns) : entryMarkup(item.entry, name)).join("")}</ol>`
          : emptyMarkup({ kind: "filtered", title: "No entries match", body: "Choose another channel or clear the search." });
      // FRONTEND: a capture refresh keeps each message's open state and the search field's caret.
      patchChildren(body, `${statusMarkup(data)}<div class="comm-toolbar"><div class="comm-filters" role="group" aria-label="Channels">${FILTERS.map(([key, label]) => `<button type="button" class="button" data-comm-filter="${key}" aria-pressed="${filter === key}">${e(label)} <small>${counts[key]}</small></button>`).join("")}</div>
        <label class="field comm-search">Search<input type="search" data-comm-search value="${e(query)}" placeholder="Words in a message, subject or name"></label></div>${list}`);
    };

    const load = async () => {
      try {
        data = await read(enrollmentId);
      } catch (cause) {
        if (!alive()) return;
        /** @type {HTMLElement} */ (host.querySelector(".dialog-body")).innerHTML = calloutMarkup({ tone: "danger", title: "The record could not be read", body: errorMessage(cause) });
        return;
      }
      draw();
    };

    const watch = () => {
      window.clearTimeout(timer);
      if (!alive()) return;
      timer = window.setTimeout(async () => {
        if (!alive()) return;
        let status;
        try {
          status = await api(`${BASE}/status`);
        } catch {
          status = null;
        }
        if (!alive()) return;
        const busy = status && Object.values(status.channels).some((/** @type {any} */ channel) => channel.requested);
        if (busy && Date.now() < until) {
          data = { ...data, channels: status.channels };
          draw();
          watch();
        } else {
          await load();
        }
      }, POLL);
    };

    host.onclick = async (event) => {
      if (!alive()) return;  // another window replaced the timeline (Back, Search Gmail now…)
      const target = /** @type {HTMLElement} */ (event.target);
      const chosen = target.closest("[data-comm-filter]");
      if (chosen instanceof HTMLElement) {
        filter = chosen.dataset.commFilter || "all";
        draw();
        return;
      }
      const drive = target.closest("[data-comm-drive]");
      if (drive instanceof HTMLButtonElement) {
        void allowDrive(api, drive, toast);
        return;
      }
      const capture = target.closest("[data-comm-capture]");
      if (capture instanceof HTMLButtonElement) {
        setBusy(capture, true);
        try {
          const status = await api(`${BASE}/capture`, "POST", {});
          data = { ...data, channels: status.channels };
          until = Date.now() + POLL_FOR;
          draw();
          watch();
        } catch (cause) {
          setBusy(capture, false);
          toast(errorMessage(cause), true);
        }
        return;
      }
      const history = target.closest("[data-comm-revisions]");
      if (history instanceof HTMLElement) {
        const slot = history.closest(".comm-entry")?.querySelector("[data-comm-history]");
        if (!slot) return;
        history.remove();
        slot.innerHTML = loadingMarkup("Reading earlier versions…", 1);
        try {
          const found = await api(`${BASE}/messages/${encodeURIComponent(history.dataset.commRevisions || "")}/revisions`);
          slot.innerHTML = `<ol class="comm-revisions">${found.revisions.slice(0, -1).reverse().map((/** @type {any} */ revision) =>
            `<li><small>Captured ${e(whenText(revision.captured_at))}</small><pre>${e(revision.text)}</pre></li>`).join("")}</ol>`;
        } catch (cause) {
          slot.innerHTML = calloutMarkup({ tone: "danger", title: "Earlier versions could not be read", body: errorMessage(cause) });
        }
      }
    };
    host.oninput = (event) => {
      if (!alive()) return;
      const input = /** @type {HTMLElement} */ (event.target);
      if (input instanceof HTMLInputElement && input.hasAttribute("data-comm-search")) {
        query = input.value;
        draw();
      }
    };
    host.addEventListener("close", () => { window.clearTimeout(timer); host.onclick = null; host.oninput = null; }, { once: true });
    await load();
    if (!alive()) return;
    const footer = document.createElement("footer");
    footer.className = "dialog-footer";
    const writeLabel = `Write to ${firstName(name) || "them"}…`;
    footer.innerHTML = `${back ? `<button type="button" class="button" data-dialog-back>${e(back.label)}</button>` : ""}${onMail ? '<button type="button" class="button" data-comm-mail>Search Gmail now…</button>' : ""}${onWrite ? `<button type="button" class="button" data-comm-write>${e(writeLabel)}</button>` : ""}<button type="button" class="button primary" data-action="close">Close</button>`;
    opened?.appendChild(footer);
    if (studentSupport) {
      const follow = document.createElement("button");
      follow.type = "button"; follow.className = "button"; follow.textContent = "Follow-up & office hours";
      follow.onclick = () => studentSupport({enrollmentId, name});
      footer.prepend(follow);
    }
    const mail = footer.querySelector("[data-comm-mail]");
    if (mail instanceof HTMLElement && onMail) mail.onclick = () => onMail();
    const write = footer.querySelector("[data-comm-write]");
    if (write instanceof HTMLElement && onWrite) write.onclick = () => onWrite();
    const backButton = footer.querySelector("[data-dialog-back]");
    if (backButton instanceof HTMLElement && back) backButton.onclick = () => back.run();
  }

  /**
   * The newest entries of a student's record, for the roster's student window: how many there are and the latest three.
   * @param {HTMLElement} host @param {string} enrollmentId @param {() => void} open
   */
  async function summary(host, enrollmentId, open) {
    host.innerHTML = loadingMarkup("Reading the record…", 2);
    let data;
    try {
      data = await read(enrollmentId);
    } catch (cause) {
      host.innerHTML = `<p class="field-hint">${e(errorMessage(cause))}</p>`;
      return;
    }
    if (!host.isConnected) return;
    // The newest three that are this student's own exchange: messages to the whole class stay in the timeline, folded.
    const latest = data.entries.filter((/** @type {any} */ entry) => !entry.mass).slice(0, 3);
    host.innerHTML = `${latest.length ? `<ul class="comm-summary">${latest.map((/** @type {any} */ entry) =>
      `<li><span class="chip" data-tone="neutral">${e(entry.channel_label)}</span><span>${e(authorText(entry, "Student"))}: ${e(entry.subject || entry.text.slice(0, 80))}</span><time datetime="${e(entry.at)}">${e(whenText(entry.at))}</time></li>`).join("")}</ul>`
      : "<p>Nothing in the record yet.</p>"}<button type="button" class="text-button" data-comm-open>${data.entries.length ? `Open the timeline (${data.entries.length})` : "Open the timeline"}</button>`;
    const button = host.querySelector("[data-comm-open]");
    if (button instanceof HTMLElement) button.onclick = () => open();
  }

  return { openTimeline, summary };
}
