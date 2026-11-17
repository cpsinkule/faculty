// @ts-check
/**
 * Messages to the students of chosen sections through Canvas's inbox (canvas/README.md "Messaging sections"; AGENTS.md "Canvas"
 * MESSAGES), from the Canvas page's Message students action and `#canvas?message=1` (the palette).
 *
 * Write: tick sections, a subject and the message. Review: the server reads each chosen course's students NOW and says exactly
 * who the message reaches, who is left out and why; only that review's fingerprint is sent, with one request ID per review (a
 * second click returns what the first did, never a second message). Like Canvas's inbox, each course gets ONE group conversation
 * unless "Send an individual message to each recipient" is ticked (requirement, 2026-10-01: unticked by default, so their mailbox is
 * not flooded with replies in separate conversations); a course over Canvas's 100-person group limit goes individually. Send
 * answers at once and the server sends course by course in the background; the window follows the record until every course
 * has Canvas's answer (closing it changes nothing: the send goes on, and the Canvas page lists it).
 */
import { plural } from "./canvas-push.js";
import { openCanvasSignIn } from "./aim-signin.js";
import { bindFilterChips, calloutMarkup, dialogSession, disclosureMarkup, errorMessage, filterChipsMarkup, loadingMarkup, runFormAction, setBusy } from "./ui.js";
import { fillChecks } from "./send-checks.js";
import { libraryMarkup } from "./mail.js";
import { viewLink, viewableRoute } from "./file-viewer.js";
import { campusText, laterFieldMarkup } from "./scheduled-sends.js";

const BASE = "/api/canvas/messages";
const LONG = 120000; // a review reads every chosen course's students
export const STATUS = /** @type {Record<string, [string, string]>} */ ({
  accepted: ["success", "Sent"], mixed: ["warning", "Partly sent"], failed: ["danger", "Not sent"], uncertain: ["warning", "Unconfirmed"],
  sending: ["neutral", "Sending…"],
});
const FOLLOW = 1500;          // how often the window reads a message that is still being sent
// ATTACHMENTS (canvas MESSAGES, requirement, 2026-10-09): as Mail's compose window
// attaches them, from the computer or from Faculty; a class-wide message offers lecture notes and saved files, never a student's paper.
export const MAX_FILES = 10;             // canvas/messages.py MAX_FILES
export const MAX_BYTES = 25_000_000;     // canvas/messages.py MAX_TOTAL
const LIBRARY = /** @type {const} */ ([["notes", "Lecture notes"], ["saved", "Saved files"]]);
const UPLOAD_WAIT = 300000;  // a review or send that carries files from the computer
const FOLLOW_FOR = 240000;    // and for how long at most (a restart waits up to 80 s for a send, then takes a few seconds)

/** "240 KB", "1.4 MB". @param {number} n */
const sizeText = (n) => n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

/**
 * ATTACHMENTS: the message's files, each named and sized, with where it comes from; Remove while writing; once sent, what Canvas did
 * with its upload. A file from Faculty opens from its own route. Pure.
 * @param {{name: string, size?: number, route?: string, file?: unknown, from?: string, upload?: string, detail?: string}[]} items
 * @param {(v: any) => string} e @param {{removable?: boolean}} [options]
 */
export function attachmentsMarkup(items, e, { removable = true } = {}) {
  if (!items.length) return "";
  const total = items.reduce((sum, item) => sum + (item.size || 0), 0);
  const upload = /** @type {Record<string, string>} */ ({ failed: "Canvas did not take it", uncertain: "Upload unconfirmed", not_uploaded: "Not uploaded", uploading: "Uploading…" });
  return `<ul class="mail-attached cm-attached">${items.map((item, index) => {
    const local = item.file || item.from === "computer";
    const name = item.route && viewableRoute(item.route) ? `<a ${viewLink(item.route, item.name, e)}>${e(item.name)}</a>` : e(item.name);
    const note = [local ? "from your computer" : "from Faculty", item.size ? sizeText(item.size) : "", item.upload ? upload[item.upload] || "" : ""].filter(Boolean).join(" · ");
    return `<li><span class="mail-attached-name">${name}</span><span class="field-hint">${e(note)}</span>${removable ? `<button type="button" class="text-button" data-cm-detach="${index}" aria-label="Remove ${e(item.name)}">Remove</button>` : ""}</li>${item.detail ? `<li class="field-hint">${e(item.detail)}</li>` : ""}`;
  }).join("")}</ul>${total > MAX_BYTES ? `<p class="form-error">These come to ${e(sizeText(total))}; a Canvas message here carries at most ${MAX_BYTES / 1e6} MB of files.</p>` : ""}`;
}

/** "Section 507" or "Sections 507, 508". @param {(string|number)[]} numbers */
export const sectionsLabel = (numbers) => `Section${numbers.length === 1 ? "" : "s"} ${numbers.join(", ")}`;

/** Left-out students grouped by reason, largest group first. @param {{name: string, reason: string}[]} rows */
export function byReason(rows) {
  /** @type {Map<string, string[]>} */ const groups = new Map();
  for (const row of rows) {
    const names = groups.get(row.reason) || [];
    if (!groups.has(row.reason)) groups.set(row.reason, names);
    names.push(row.name);
  }
  return [...groups].map(([reason, names]) => ({ reason, names })).sort((a, b) => b.names.length - a.names.length || a.reason.localeCompare(b.reason));
}

/** A sent message's one-line place: "to 98 students · Sections 507, 508". @param {any} record */
export function sentDetail(record) {
  const numbers = record.courses.flatMap((/** @type {any} */ c) => c.sections || []);
  const count = record.status === "accepted" ? record.recipients : record.planned;
  return `to ${plural(count, "student")}${numbers.length ? ` · ${sectionsLabel(numbers)}` : ""}`;
}

const when = (/** @type {string} */ iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** The Canvas page's list of messages sent (newest first). @param {any[]} sent @param {(v: any) => string} e */
export function sentMarkup(sent, e) {
  if (!sent.length) return "";
  return `<section class="card canvas-hub-term cm-sent" aria-label="Messages sent through Canvas"><header class="canvas-hub-title"><h2>Messages sent through Canvas</h2></header>
    <ul class="cm-sent-list">${sent.map((r) => {
      const [tone, label] = STATUS[r.status] || ["neutral", r.status];
      return `<li data-patch-key="message-${e(r.id)}"><span class="cm-sent-subject"><strong>${e(r.subject)}</strong><small>${e(sentDetail(r))} · ${e(when(r.sent_at))}</small></span>
        <span class="chip" data-tone="${tone}">${label}</span><button type="button" class="text-button" data-cm-check="${e(r.id)}">${r.status === "failed" ? "Details" : "Check delivery"}</button></li>`;
    }).join("")}</ul></section>`;
}

/** @param {{api: any, escape: (v: any) => string, toast: (m: string, o?: any) => void, showDialog: (html: string) => void,
 *  dialogHeader: (t: string, s?: string) => string, onSent?: () => void}} deps */
export function createCanvasMessage(deps) {
  const { api, escape, toast, showDialog, dialogHeader, onSent = () => {} } = deps;
  const e = escape;
  const dialog = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  // A sign-in replaces the requesting form. After it closes, the retry may restore this flow only while its own sign-in is
  // still the last dialog shown; neither a late read nor a completed send may replace a newer window.
  async function request(run, current) {
    try { return { result: await run(), current }; }
    catch (cause) {
      if (cause?.status !== 401) throw cause;
      if (!current()) return { result: null, current: () => false };
      const signingIn = openCanvasSignIn(deps);
      const signed = dialog().firstElementChild;
      const user = await signingIn;
      const stillHere = () => !dialog().open && dialog().firstElementChild === signed;
      if (!user || !stillHere()) return { result: null, current: stillHere };
      return { result: await run(), current: stillHere };
    }
  }

  /** Opens the window: the sections from GET /api/canvas/messages, then write, review, send.
   * @param {{sections?: string[]}} [prefill] section IDs ticked at first */
  async function open(prefill = {}) {
    showDialog(`<div>${dialogHeader("Message students through Canvas", "")}<div class="dialog-body">${loadingMarkup("Reading your sections…", 3)}</div></div>`);
    const opened = dialog().firstElementChild;
    /** @type {any} */ let view;
    try {
      view = await api(BASE);
    } catch (cause) {
      if (dialog().firstElementChild !== opened || !dialog().open) return;
      showDialog(`<div>${dialogHeader("Message students through Canvas", "")}<div class="dialog-body">${calloutMarkup({ tone: "danger", title: "Your sections could not be read", body: e(errorMessage(cause)) })}</div><footer class="dialog-footer"><button type="button" class="button primary" data-action="close">Close</button></footer></div>`);
      return;
    }
    if (dialog().firstElementChild !== opened || !dialog().open) return;
    const linked = view.terms.flatMap((/** @type {any} */ t) => t.sections.filter((/** @type {any} */ s) => s.course).map((/** @type {any} */ s) => s.section_id));
    const draft = { sections: new Set((prefill.sections || []).filter((id) => linked.includes(id))), subject: "", body: "", individual: false,
      /** @type {{name: string, size: number, route?: string, file?: File}[]} */ attachments: [] };
    /** @type {"notes" | "saved"} */ let libraryKind = "notes";
    if (!view.canvas?.configured) {
      showDialog(`<div>${dialogHeader("Message students through Canvas", "")}<div class="dialog-body">${calloutMarkup({ tone: "warning", title: "Canvas is not set up for this Faculty instance" })}</div><footer class="dialog-footer"><button type="button" class="button primary" data-action="close">Close</button></footer></div>`);
      return;
    }
    if (draft.sections.size === 0 && view.terms.length === 1) view.terms[0].sections.forEach((/** @type {any} */ s) => { if (s.course) draft.sections.add(s.section_id); });
    let requestId = "";

    function sectionsMarkup() {
      if (!view.terms.length) return calloutMarkup({ tone: "info", title: "No current courses", body: "Mark a course current on its settings to message its sections." });
      return view.terms.map((/** @type {any} */ term) => {
        const usable = term.sections.filter((/** @type {any} */ s) => s.course);
        const ticked = usable.filter((/** @type {any} */ s) => draft.sections.has(s.section_id)).length;
        return `<div class="cm-term"><label class="checkbox-field"><input type="checkbox" data-cm-term="${e(term.term_id)}" ${usable.length && ticked === usable.length ? "checked" : ""} ${usable.length ? "" : "disabled"}><span><strong>${e(term.code)}</strong> <small>${e(term.term_label)} · every section</small></span></label>
          <div class="cm-term-sections">${term.sections.map((/** @type {any} */ s) => `<label class="checkbox-field"><input type="checkbox" name="section" value="${e(s.section_id)}" data-term="${e(term.term_id)}" ${draft.sections.has(s.section_id) ? "checked" : ""} ${s.course ? "" : "disabled"}><span>Section ${e(s.number)} <small>${e(plural(s.students, "student"))} · ${s.course ? e(s.course.name || `Canvas course ${s.course.course_id}`) : "no Canvas course linked"}</small></span></label>`).join("")}</div></div>`;
      }).join("");
    }

    function edit() {
      showDialog(`<form class="cm-compose" novalidate>${dialogHeader("Message students through Canvas", "Sent from your Canvas inbox, as Canvas’s Compose Message sends it.")}<div class="dialog-body">
        <fieldset class="cc-targets cm-sections"><legend>Send to</legend>${sectionsMarkup()}</fieldset>
        <label class="checkbox-field cm-individual"><input type="checkbox" name="individual" ${draft.individual ? "checked" : ""}><span>Send an individual message to each recipient
          <small>Unticked: one group conversation per section’s Canvas course, where students see who else got it and can reply to everyone. Ticked: each student gets their own conversation and replies come to you alone.</small></span></label>
        <label class="field">Subject<input name="subject" type="text" value="${e(draft.subject)}" maxlength="255" autocomplete="off" required /></label>
        <label class="field">Message<textarea name="body" maxlength="20000" rows="10" required>${e(draft.body)}</textarea><span class="field-hint">Plain text, as Canvas’s inbox sends it. You see exactly who gets it before anything is sent.</span></label>
        <div class="mail-attach"><div data-cm-attached>${attachmentsMarkup(draft.attachments, e)}</div>
          <div class="mail-attach-actions"><label class="button mail-attach-upload">Attach from your computer<input type="file" multiple class="sr-only" data-cm-attach-file></label>
            <button type="button" class="button" data-cm-attach-faculty aria-expanded="false">Attach from Faculty…</button>
            <span class="field-hint">Up to ${MAX_FILES} files, ${MAX_BYTES / 1e6} MB together. Canvas keeps them in your own files, never the course’s.</span></div>
          <div class="mail-library" data-cm-library hidden>${filterChipsMarkup("cm-library", LIBRARY.map(([value, label]) => ({ value, label })), libraryKind, { label: "Faculty files" })}
            <div data-cm-library-list role="status"></div></div></div>
        <p class="form-error" role="alert" hidden></p></div>
        <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Review</button></footer></form>`);
      const form = /** @type {HTMLFormElement} */ (dialog().querySelector("form.cm-compose"));
      const current = dialogSession(form, dialog());
      const read = () => {
        draft.subject = /** @type {HTMLInputElement} */ (form.elements.namedItem("subject")).value;
        draft.body = /** @type {HTMLTextAreaElement} */ (form.elements.namedItem("body")).value;
        draft.individual = /** @type {HTMLInputElement} */ (form.elements.namedItem("individual")).checked;
        draft.sections = new Set([...form.querySelectorAll('input[name="section"]:checked')].map((b) => /** @type {HTMLInputElement} */ (b).value));
      };
      const sync = () => {
        for (const all of form.querySelectorAll("[data-cm-term]")) {
          const box = /** @type {HTMLInputElement} */ (all);
          const own = [...form.querySelectorAll(`input[name="section"][data-term="${CSS.escape(box.dataset.cmTerm || "")}"]:not(:disabled)`)].map((b) => /** @type {HTMLInputElement} */ (b));
          const on = own.filter((b) => b.checked).length;
          box.checked = own.length > 0 && on === own.length;
          box.indeterminate = on > 0 && on < own.length;
        }
      };
      sync();
      // ATTACHMENTS: files from the computer go with the review and the send; files from Faculty are named by their routes.
      const attachedHost = /** @type {HTMLElement} */ (form.querySelector("[data-cm-attached]"));
      const library = /** @type {HTMLElement} */ (form.querySelector("[data-cm-library]"));
      const libraryList = /** @type {HTMLElement} */ (form.querySelector("[data-cm-library-list]"));
      const toggle = /** @type {HTMLButtonElement} */ (form.querySelector("[data-cm-attach-faculty]"));
      /** @type {any} */ let answer = null;
      let asked = 0;
      const drawLibrary = () => { if (answer) libraryList.innerHTML = libraryMarkup(answer, new Set(draft.attachments.map((item) => item.route || ""))); };
      const drawAttached = () => { attachedHost.innerHTML = attachmentsMarkup(draft.attachments, e); drawLibrary(); };
      const full = () => {
        if (draft.attachments.length < MAX_FILES) return false;
        toast(`A message can carry at most ${MAX_FILES} attachments.`, { tone: "danger" });
        return true;
      };
      const loadLibrary = async () => {
        const ticket = ++asked;
        libraryList.innerHTML = loadingMarkup("Reading Faculty's files…", 2);
        try {
          const found = await api(`/api/mail/files?${new URLSearchParams({ kind: libraryKind, q: "" })}`);
          if (ticket === asked) { answer = found; drawLibrary(); }
        } catch (cause) { if (ticket === asked) { answer = null; libraryList.innerHTML = `<p class="form-error">${e(errorMessage(cause))}</p>`; } }
      };
      toggle.onclick = () => {
        library.hidden = !library.hidden;
        toggle.setAttribute("aria-expanded", String(!library.hidden));
        if (!library.hidden && !answer) void loadLibrary();
      };
      bindFilterChips(library, "cm-library", (value) => { libraryKind = /** @type {"notes" | "saved"} */ (value); answer = null; void loadLibrary(); });
      libraryList.onclick = (event) => {
        const button = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest("[data-mail-pick]"));
        if (!button || !answer || full()) return;
        const [g, i] = String(button.dataset.mailPick).split(":").map(Number);
        const item = answer.groups?.[g]?.items?.[i];
        if (!item?.route || draft.attachments.some((other) => other.route === item.route)) return;
        draft.attachments.push({ route: item.route, name: item.name, size: item.size });
        drawAttached();
      };
      /** @type {HTMLInputElement} */ (form.querySelector("[data-cm-attach-file]")).onchange = (event) => {
        const input = /** @type {HTMLInputElement} */ (event.target);
        for (const file of Array.from(input.files || [])) {
          if (full()) break;
          draft.attachments.push({ file, name: file.name, size: file.size });
        }
        input.value = "";
        drawAttached();
      };
      attachedHost.onclick = (event) => {
        const button = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest("[data-cm-detach]"));
        if (!button) return;
        draft.attachments.splice(Number(button.dataset.cmDetach), 1);
        drawAttached();
      };
      form.onchange = (event) => {
        const target = /** @type {HTMLInputElement} */ (event.target);
        if (target.dataset.cmAttachFile !== undefined) return;  // the file picker has its own handler
        if (target.dataset.cmTerm !== undefined) {
          form.querySelectorAll(`input[name="section"][data-term="${CSS.escape(target.dataset.cmTerm)}"]:not(:disabled)`)
            .forEach((b) => { /** @type {HTMLInputElement} */ (b).checked = target.checked; });
        }
        sync();
      };
      form.onsubmit = (event) => {
        event.preventDefault();
        read();
        void runFormAction(form, async () => {
          if (!draft.sections.size) throw new Error("Choose at least one section.");
          if (!draft.subject.trim()) throw new Error("Write a subject.");
          if (!draft.body.trim()) throw new Error("Write the message.");
          await review(current);
        });
      };
    }

    const fields = () => {
      let upload = 0;
      const attachments = draft.attachments.map((item) => item.file ? { upload: upload++, name: item.name } : { route: item.route, name: item.name });
      return { sections: [...draft.sections], subject: draft.subject, body: draft.body, individual: draft.individual, ...(attachments.length ? { attachments } : {}) };
    };
    /** The fields as JSON, or with the computer's files as the multipart form the routes read (canvas/api.py). @param {Record<string, unknown>} [extra] */
    const payload = (extra = {}) => {
      const message = { ...fields(), ...extra };
      const local = draft.attachments.filter((item) => item.file);
      if (!local.length) return message;
      const data = new FormData();
      data.append("message", JSON.stringify(message));
      for (const item of local) data.append("file", /** @type {File} */ (item.file), item.name);
      return data;
    };
    const wait = () => draft.attachments.some((item) => item.file) ? UPLOAD_WAIT : LONG;

    async function review(current) {
      const { result: preview, current: here } = await request(() => api(`${BASE}/preview`, "POST", payload(), {}, wait()), current);
      if (!preview) { if (here()) edit(); return; }
      if (!here()) return;
      requestId = crypto.randomUUID();
      const total = preview.recipients;
      const courses = preview.courses.map((/** @type {any} */ c) => `<li><span><strong>${e(sectionsLabel(c.sections))}</strong> <small>${e(c.name)}</small></span>
        <span>${e(plural(c.recipients, "student"))}${c.only_canvas ? ` <small>(${e(plural(c.only_canvas, "student"))} only Canvas lists)</small>` : ""} <small>· ${c.kind === "group" ? "one group conversation" : "individual messages"}</small></span>
        ${c.too_many_for_group ? `<p class="field-hint">Canvas allows at most ${e(preview.group_limit)} people in one group conversation, so these students get individual messages.</p>` : ""}
        ${c.students.length ? disclosureMarkup({ label: "Who gets it", hint: c.students.length, body: `<p class="cm-names">${e(c.students.join(" · "))}</p>` }) : ""}</li>`).join("");
      const unlinked = preview.unlinked.length ? calloutMarkup({ tone: "info", title: `${sectionsLabel(preview.unlinked.map((/** @type {any} */ u) => u.section))} ${preview.unlinked.length === 1 ? "has" : "have"} no Canvas course linked`,
        body: e(`${plural(preview.unlinked.reduce((/** @type {number} */ n, /** @type {any} */ u) => n + u.students, 0), "student")} there will not get it. Link a course on the section tab.`) }) : "";
      const missing = preview.not_reached.length ? calloutMarkup({ tone: "warning", title: `${plural(preview.not_reached.length, "student")} of your sections ${preview.not_reached.length === 1 ? "is" : "are"} not in their Canvas course`,
        body: `<p>They will not get it through Canvas.</p><p class="cm-names">${e(preview.not_reached.map((/** @type {any} */ s) => `${s.name} (Section ${s.section})`).join(" · "))}</p>` }) : "";
      // MAIL-SEND-LATER: sent at a chosen time to exactly these students; a file from the computer is never kept, so not with one.
      const later = total > 0 && !draft.attachments.some((item) => item.file);
      let scheduling = false;
      const left = preview.left_out.length ? disclosureMarkup({ label: "Left out on purpose", hint: preview.left_out.length,
        body: byReason(preview.left_out).map((g) => `<p><strong>${e(g.reason)}</strong> (${g.names.length})<br><span class="cm-names">${e(g.names.join(" · "))}</span></p>`).join("") }) : "";
      showDialog(`<form class="cm-review">${dialogHeader("Review before sending", view.canvas?.user?.name ? `From ${e(view.canvas.user.name)} in Canvas` : "From your Canvas account")}<div class="dialog-body">
        <p class="cm-reach">${total ? `<strong>Reaches ${e(plural(total, "student"))}</strong> in ${e(plural(preview.courses.length, "Canvas course"))}, ${preview.individual ? "each in their own conversation." : `one group conversation in each${preview.courses.some((/** @type {any} */ c) => c.kind !== "group") ? " that Canvas allows" : ""}: students see who else got it and can reply to everyone.`}` : "<strong>Nobody in the sections you chose can be reached through Canvas.</strong>"}</p>
        <ul class="cm-courses">${courses}</ul>${unlinked}${missing}${left}
        <dl class="cm-message"><dt>Subject</dt><dd>${e(preview.subject)}</dd>${preview.attachments?.length ? `<dt>Attached</dt><dd>${attachmentsMarkup(preview.attachments, e, { removable: false })}</dd>` : ""}</dl><pre class="mail-text">${e(preview.body)}</pre>
        <div class="send-checks" data-send-checks aria-live="polite"></div>${later ? laterFieldMarkup(e) : ""}
        <p class="form-error" role="alert" hidden></p></div>
        <footer class="dialog-footer"><button type="button" class="button" data-cm-back>Back to edit</button>${later ? '<button type="button" class="button" data-cm-later>Send later…</button>' : ""}<button type="submit" class="button primary" ${total ? "" : "disabled"}>${total ? `Send to ${e(plural(total, "student"))}` : "Nobody to send to"}</button></footer></form>`);
      const form = /** @type {HTMLFormElement} */ (dialog().querySelector("form.cm-review"));
      const reviewing = dialogSession(form, dialog());
      // JEV SEND-CHECKS: advice beside the review (dates against the chosen sections, a student named); Send never waits for it.
      void fillChecks(form.querySelector("[data-send-checks]"), () => api(`${BASE}/check`, "POST", fields(), {}, LONG), e);
      /** @type {HTMLElement} */ (form.querySelector("[data-cm-back]")).onclick = edit;
      const laterButton = form.querySelector("[data-cm-later]");
      if (laterButton instanceof HTMLElement) laterButton.onclick = () => {
        scheduling = true;
        laterButton.hidden = true;
        /** @type {HTMLElement} */ (form.querySelector("[data-send-later]")).hidden = false;
        /** @type {HTMLElement} */ (form.querySelector('button[type="submit"]')).textContent = "Schedule";
        /** @type {HTMLInputElement} */ (form.elements.namedItem("send_at")).focus();
      };
      form.onsubmit = (event) => {
        event.preventDefault();
        void runFormAction(form, async () => {
          if (scheduling) {
            const sendAt = /** @type {HTMLInputElement} */ (form.elements.namedItem("send_at")).value;
            const { result: kept, current: here } = await request(() => api(`${BASE}/schedule`, "POST",
              { ...fields(), fingerprint: preview.fingerprint, request_id: requestId, confirmation: "schedule", send_at: sendAt }, {}, LONG), reviewing);
            if (!kept) { if (here()) edit(); return; }
            onSent();
            if (here()) dialog().close();
            toast(`Scheduled for ${campusText(kept.send_at)} to ${plural(kept.recipients, "student")}.`);
            return;
          }
          try {
            const { result, current: here } = await request(() => api(`${BASE}/send`, "POST",
              payload({ fingerprint: preview.fingerprint, request_id: requestId, confirmation: "send" }), {}, wait()), reviewing);
            if (!result) { if (here()) edit(); return; }
            onSent();
            if (here()) finished(result);
            else toast(`Canvas message: ${(STATUS[result.status] || ["neutral", result.status])[1]}.`);
          } catch (cause) {
            if (/** @type {any} */ (cause)?.status !== 409) throw cause;
            toast(errorMessage(cause));
            if (reviewing()) await review(reviewing);
          }
        });
      };
    }

    function finished(/** @type {any} */ record) {
      if (record.status === "sending") void follow(record);
      else done(record);
    }
    edit();
  }

  /** The end of a send: a toast when Canvas took every course, else the record with what to do. @param {any} record */
  function done(record) {
    if (record.status === "accepted") {
      dialog().close();
      const queued = record.courses.some((/** @type {any} */ c) => c.batch);
      toast(queued ? `Canvas is delivering it to ${plural(record.recipients, "student")}.` : `Sent through Canvas to ${plural(record.recipients, "student")}.`);
      return;
    }
    showRecord(record);
  }

  /** Shows a message being sent and reads its record until every course has Canvas's answer, while the window still shows it.
   * A read that fails (Faculty restarting waits for the send first) is tried again. @param {any} record */
  async function follow(record) {
    showRecord(record);
    const shown = () => Boolean(dialog().open && dialog().querySelector(`[data-cm-record="${CSS.escape(record.id)}"]`));
    let painted = JSON.stringify(record.courses);
    const until = Date.now() + FOLLOW_FOR;
    while (Date.now() < until) {
      await new Promise((resolve) => setTimeout(resolve, FOLLOW));
      if (!shown()) return;
      let found;
      try { found = await api(`${BASE}/${encodeURIComponent(record.id)}`); } catch { continue; }
      if (!shown()) return;
      if (found.status !== "sending") { onSent(); done(found); return; }
      if (JSON.stringify(found.courses) !== painted) { painted = JSON.stringify(found.courses); showRecord(found); }
    }
  }

  /** A sent message's record: what Canvas answered per course, with the words that were sent. @param {any} record @param {any[]} [delivery] */
  function showRecord(record, delivery = []) {
    const label = (STATUS[record.status] || ["neutral", record.status])[1];
    const checked = new Map(delivery.map((d) => [d.course_id, d]));
    const rows = record.courses.map((/** @type {any} */ c) => {
      const [t, l] = STATUS[c.status] || ["neutral", c.status];
      const d = checked.get(c.course_id);
      let state = "";
      if (d?.state === "sending") state = `Canvas is still delivering it (${Math.round((d.completion || 0) * 100)}% done).`;
      else if (d?.state === "delivered") state = `In Canvas: every one of the ${plural(d.recipients, "student")} has it.`;
      else if (d?.state === "partly" || d?.state === "none") state = `In Canvas: ${d.delivered} of ${plural(d.recipients, "student")} have it. Not found: ${d.missing.join(" · ")}.`;
      return `<li><span><strong>${e(sectionsLabel(c.sections || []))}</strong> <small>${e(c.name)} · ${e(plural(c.recipients, "student"))} · ${c.kind === "group" ? "one group conversation" : "individual messages"}</small></span><span class="chip" data-tone="${t}">${l}</span>
        ${c.detail ? `<p class="field-hint">${e(c.detail)}</p>` : ""}${state ? `<p class="cm-state">${e(state)}</p>` : ""}</li>`;
    }).join("");
    const sending = record.status === "sending";
    const advice = sending ? `<p class="cm-state" role="status">Sending one Canvas course at a time. You can close this window; Faculty keeps sending, and a restart waits for it.</p>`
      : record.status === "uncertain" || record.status === "mixed"
      ? calloutMarkup({ tone: "warning", title: "Canvas did not confirm every course", body: "Faculty never sends this message again by itself. Check delivery, or look in Canvas’s Sent folder, before writing it again." })
      : record.status === "failed" ? calloutMarkup({ tone: "danger", title: "Canvas did not send it", body: "Nothing went to the students. Fix what Canvas said and write the message again." }) : "";
    showDialog(`<div class="cm-record" data-cm-record="${e(record.id)}">${dialogHeader(record.subject, `${label} ${when(record.sent_at)}`)}<div class="dialog-body">${advice}
      <ul class="cm-courses">${rows}</ul>${record.attachments?.length ? `<h3 class="cm-attached-title">Attached</h3>${attachmentsMarkup(record.attachments, e, { removable: false })}` : ""}${disclosureMarkup({ label: "The message", body: `<pre class="mail-text">${e(record.body)}</pre>` })}</div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button>${record.status !== "failed" && !sending ? '<button type="button" class="button primary" data-cm-recheck>Check delivery in Canvas</button>' : ""}</footer></div>`);
    dialog().querySelector("[data-cm-recheck]")?.addEventListener("click", () => { void check(record.id); });
  }

  /** Reads Canvas for a sent message: still delivering, delivered, or who does not have it. Records nothing. @param {string} id */
  async function check(id) {
    const button = /** @type {HTMLButtonElement | null} */ (dialog().querySelector("[data-cm-recheck]"));
    const current = dialogSession(dialog().firstElementChild, dialog());
    if (button) { setBusy(button, true); button.textContent = "Reading Canvas…"; }
    try {
      const { result: found, current: here } = await request(() => api(`${BASE}/${encodeURIComponent(id)}/delivery`, "GET", undefined, {}, LONG), current);
      if (found && here()) showRecord(found, found.delivery);
    } catch (cause) {
      toast(errorMessage(cause), { tone: "danger" });
      if (button) { setBusy(button, false); button.textContent = "Check delivery in Canvas"; }
    }
  }

  /** The record of one sent message, from the page's list (Faculty's records; Check delivery reads Canvas). @param {any} record */
  function details(record) {
    if (record.status === "sending") void follow(record);
    else showRecord(record);
  }

  return { open, details };
}
