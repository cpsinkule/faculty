// @ts-check
// Explicitly saved tasks and preferences, read-only watches and retained teaching files.
import { esc } from "./assistant-markdown.js";
import { calloutMarkup, emptyMarkup, errorMessage, plural, runFormAction, setBusy } from "./ui.js";
import { viewLink } from "./file-viewer.js";
import { preparationMarkup, preparationRunsMarkup, preparationSpec, updatePreparationTiming } from "./assistant-preparations.js";
import { patchChildren } from "./dom-patch.js";

const BASE = "/api/assistant";

/** JEV: advisory judgments, never a correctness guarantee. @param {any} event @param {{sources?: boolean}} [options] */
export function verificationMarkup(event, { sources = true } = {}) {
  if (event.check_type === 'tool_use') {
    return calloutMarkup({tone: 'info', title: 'Tool-use review', body: event.detail || 'Advisory checks; existing permissions still apply.'})
      + ((event.checks || []).length ? `<details class="disclosure"><summary>Points considered · ${event.checks.length}</summary><div class="disclosure-body">${event.checks.map(c => `<p>${esc(c.text)}</p>`).join('')}</div></details>` : '');
  }
  const labels = {supported: "Supported by observed text", contradicted: "Contradicted by a source", unsupported: "Not supported by the sources", uncertain: "Uncertain", not_factual: "Draft or suggestion"};
  const flagged = (event.checks || []).filter((/** @type {any} */ c) => !["supported", "not_factual"].includes(c.verdict));
  return calloutMarkup({tone: event.status === "checked" ? "info" : "warning", title: event.status === "checked" ? "Evidence checked" : "Review the evidence", body: event.detail || "This answer has not been verified."})
    + (flagged.length ? `<details class="disclosure"><summary>Claims to review · ${flagged.length}</summary><div class="disclosure-body">${flagged.map((/** @type {any} */ c) => `<article><strong>${esc(labels[c.verdict] || "Uncertain")} · paragraph ${esc(c.paragraph)}</strong><p>${esc(c.text)}</p></article>`).join("")}</div></details>` : "")
    + (event.partial ? '<p class="assistant-card-note">This check covered only part of the answer or its evidence.</p>' : "")
    + (sources ? evidenceMarkup(event.sources || []) : "");
}

/** @param {any} question @param {string} [prefix] */
export function questionMarkup(question, prefix = "") {
  if (!question) return "";
  return `<form class="assistant-question assistant-card" data-patch-key="question-${esc(question.id)}" data-assistant-question="${esc(question.id)}">
    <strong>A question for you</strong>${question.questions.map((/** @type {any} */ q) => `<fieldset><legend>${esc(q.title)}</legend>
      ${q.options.map((/** @type {string} */ option) => `<button class="button" type="button" data-question-option="${esc(q.id)}" data-answer="${esc(option)}">${esc(option)}</button>`).join("")}
      <label class="sr-only" for="${esc(prefix)}answer-${esc(question.id)}-${esc(q.id)}">${esc(q.title)}</label>
      <textarea id="${esc(prefix)}answer-${esc(question.id)}-${esc(q.id)}" name="${esc(q.id)}" rows="2" maxlength="4000" placeholder="${q.options.length ? "Or write your own answer" : "Write your answer"}" required></textarea></fieldset>`).join("")}
    <p class="form-error" role="alert" hidden></p><button class="button primary" type="submit">Answer and continue</button></form>`;
}

/**
 * A choice under a question is the answer (requirement, 2026-10-04): it fills that question's answer and, once no question of the form is left unanswered,
 * sends the form through its own submit handler, as Answer and continue would. A form already sending ignores the click.
 * @param {HTMLElement} button a `[data-question-option]` button
 */
export function chooseQuestionOption(button) {
  const form = /** @type {HTMLFormElement | null} */ (button.closest("form"));
  if (!form || form.dataset.saving === "true") return;
  const input = /** @type {HTMLTextAreaElement | null} */ (form.elements.namedItem(button.getAttribute("data-question-option") || ""));
  if (!input) return;
  input.value = button.getAttribute("data-answer") || "";
  const fields = [...form.querySelectorAll("textarea")];
  const open = fields.find((field) => !field.value.trim());
  if (open) open.focus();
  else form.requestSubmit();
}

/** ASSISTANT-FOLLOWUP-SUGGESTIONS: one revision form in both review locations. @param {any} suggestion @param {string} attributes */
export function followupRevisionMarkup(suggestion, attributes) {
  if (!suggestion.id || ["queued", "running", "superseded"].includes(suggestion.state)) return "";
  return `<details class="disclosure support-revise" data-patch-key="revise-${esc(suggestion.id)}"><summary><span>Ask for changes</span></summary>
    <form class="disclosure-body" ${attributes}><label class="field">What should the Assistant change?<textarea name="text" maxlength="5000" rows="3" required></textarea></label>
    <label class="checkbox-field"><input type="checkbox" name="remember"> Remember this for future drafts</label>
    <p class="field-hint">Replaces unsubmitted proposals. Completed actions keep their receipts. A remembered request becomes an Assistant preference, written as a rule without the student when it is about this follow-up.</p><p class="form-error" role="alert" hidden></p>
    <button class="button" type="submit">Ask for changes</button></form></details>`;
}

const followupRequests = /** @type {WeakMap<object, {content: string, id: string}>} */ (new WeakMap());
/** How long a request that also remembers what was typed may take: the Assistant's model writes the rule, and Jev checks it. */
export const REMEMBER_WAIT = 90000;

/**
 * What a follow-up's "Remember this" kept, as the toast that says it (ASSISTANT-MEMORY "A FOLLOW-UP'S REMEMBER THIS": the page
 * says exactly what was kept, with Undo for a rule the Assistant wrote): [message, toast options], or null when the request
 * remembered nothing.
 * @param {any} result the request's answer @param {(path: string, method: string, data: unknown) => Promise<any>} api
 * @param {Function} toast
 * @returns {[string, any] | null}
 */
export function rememberedNotice(result, api, toast) {
  if (result?.memory_error) return [`Not saved as a preference: ${result.memory_error}`, true];
  if (!result?.remembered) return null;
  const memory = result.memory || {};
  if (memory.ruled) {
    const words = String(memory.content || "").replace(/\s+/g, " ").trim();
    return [`Remembered for future drafts: “${words}”${/[.!?…]$/.test(words) ? "" : "."} I wrote this from what you typed, without the student.`,
      { timeout: 20000, action: { label: "Undo", run: async () => { await api(`/api/assistant/memory/${encodeURIComponent(memory.id)}/undo`, "POST", {}); toast("Taken back. Nothing is remembered from it."); } } }];
  }
  if (memory.standalone === false) return ["Saved as you typed it. It may not read well on its own: reword it in Memory.", { timeout: 12000 }];
  return ["Saved as an Assistant preference for future drafts.", false];
}
/** Where a learned memory came from, in the words its notice and its line use. */
export const LEARNED_FROM = { edit: "your edit", decision: "your decision", request: "the change you asked for" };

/**
 * What the Assistant remembered from a correction of a follow-up's card, as the toast that says it once (ASSISTANT-MEMORY "LEARNED
 * FROM AN EDIT": kept at once, said where it happened, with Undo): [message, toast options].
 * @param {any} learned one entry of a follow-up's `suggestions.learned` @param {(path: string, method: string, data: unknown) => Promise<any>} api
 * @param {Function} toast @param {() => void} [after] runs once Undo has taken it back
 * @returns {[string, any]}
 */
export function learnedNotice(learned, api, toast, after) {
  const words = String(learned.memory.content || "").replace(/\s+/g, " ").trim();
  const replaced = learned.replaced ? ` It replaces “${String(learned.replaced).replace(/\s+/g, " ").trim()}”.` : "";
  return [`Remembered from ${LEARNED_FROM[learned.kind] || LEARNED_FROM.edit}: “${words}”${/[.!?…]$/.test(words) ? "" : "."}${replaced}`,
    { timeout: 20000, action: { label: "Undo", run: async () => { await api(`/api/assistant/memory/${encodeURIComponent(learned.memory.id)}/undo`, "POST", {}); toast("Taken back. Nothing is remembered from it."); after?.(); } } }];
}

/** Instructor input only: server rechecks source and run; this never approves an action.
 * @param {Function} api @param {any} suggestion @param {string} operation @param {any} data @param {object} element */
export async function requestFollowupSuggestions(api, suggestion, operation, data, element) {
  const content = JSON.stringify({operation, data, id: suggestion.id, seen: suggestion.seen});
  let request = followupRequests.get(element);
  if (!request || request.content !== content) { request = {content, id: crypto.randomUUID()}; followupRequests.set(element, request); }
  // "Remember this" may have the Assistant write the rule out before it answers (ASSISTANT-MEMORY): that takes seconds, not a moment.
  const result = await api(`/api/student-support/followups/suggestions/${suggestion.id}/${operation}`, "POST", {...data,
    request_id: request.id, seen: suggestion.seen, fingerprint: suggestion.fingerprint}, {}, data?.remember ? REMEMBER_WAIT : undefined);
  followupRequests.delete(element);
  window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
  return result;
}

/** Sources are recorded by the transport, never supplied by a model. @param {any[]} sources */
export function evidenceMarkup(sources) {
  const found = (sources || []).filter(s => ["catalog", "repository"].includes(s.kind) || typeof s.path === "string" && /^\/api\/[A-Za-z0-9/_.~%:=&?+-]+$/.test(s.path) && !s.path.includes(".."));
  if (!found.length) return "";
  return `<details class="disclosure assistant-evidence"><summary><span>Sources · ${found.length}</span></summary><div class="disclosure-body">
    <p class="assistant-card-note">These identify what was read at the time. Open a source to inspect its current contents.</p>
    ${found.map(s => `<article>${["catalog", "repository"].includes(s.kind) ? `<strong>${esc(s.path)}</strong><pre>${esc(JSON.stringify(s.read || {}, null, 2))}</pre>` : s.fingerprint_of === "file bytes" ? `<a class="link-arrow" ${viewLink(s.path, s.path.split("?")[0].split("/").pop() || "File", esc)}>${esc(s.path)}</a>` : `<a class="link-arrow" href="${esc(s.path)}" target="_blank" rel="noopener">${esc(s.path)}</a>`}
      <p class="assistant-card-note">Read ${esc(s.observed_at)} · SHA-256 of ${esc(s.fingerprint_of || "response")} <code>${esc(s.fingerprint)}</code></p>
      ${Object.keys(s.versions || {}).length ? `<pre>${esc(JSON.stringify(s.versions, null, 2))}</pre>` : ""}</article>`).join("")}</div></details>`;
}

/** @param {{host: HTMLElement, api: Function, toast: Function, current: Function, ensure: Function, opened: Function}} options */
export function createWorkspaceTab({ host, api, toast, current, ensure, opened }) {
  let generation = 0, visible = false, timer = 0;
  let data = /** @type {any} */ ({});
  // Request IDs of Prepare again clicks, by run: kept here, not on the form, because the timed patch of the runs brings the
  // form's attributes back to the markup. An uncertain request is sent again under its own ID, never a new one.
  const requests = new Map();
  let options = /** @type {any} */ ({});
  const savedForm = () => {
    const saved = current()?.saved || {};
    return `<details class="disclosure" data-patch-key="saved-${esc(current()?.id || "new")}"><summary><span>${saved.title ? "Saved task settings" : "Save this conversation"}</span></summary>
      <form class="disclosure-body assistant-workspace-form" data-save-conversation>
        <label>Title<input name="title" maxlength="160" value="${esc(saved.title || "")}" required></label>
        <label>Task or goal <span class="assistant-card-note">(optional)</span><textarea name="goal" maxlength="2000" rows="2">${esc(saved.goal || "")}</textarea></label>
        <label>Keep after last activity<select name="retention_days">${[7, 30, 90, 365].map(days => `<option value="${days}" ${days === (saved.retention_days || 30) ? "selected" : ""}>${days} days</option>`).join("")}</select></label>
        <label>Automatic continuation<select name="state">${[["active", "Active"], ["paused", "Paused"], ["completed", "Completed"]].map(([key, title]) => `<option value="${key}" ${key === (saved.state || "active") ? "selected" : ""}>${title}</option>`).join("")}</select></label>
        <p class="assistant-card-note">Only saved conversations survive a restart. Saved context keeps the latest 80 messages and up to 2 MB of recent events. Deletion and expiry remove the saved copy; action receipts and existing backups keep their normal retention.</p>
        <p class="form-error" role="alert" hidden></p><button class="button primary" type="submit">Save conversation</button></form></details>`;
  };
  function render() {
    const conversations = data.conversations || [], watches = data.watches || [], files = data.artifacts || [];
    const notifications = (data.notifications || []).filter((/** @type {any} */ n) => !n.read_at);
    const metrics = data.metrics || {};
    patchChildren(host, `${savedForm()}${preparationMarkup(data, options)}
      <details class="disclosure" data-patch-key="review-activity"><summary><span>Review activity</span></summary><div class="disclosure-body">
      <p>${esc(metrics.approved_unchanged ?? 0)} approved without edits · ${esc(metrics.edited ?? 0)} edited · ${esc(metrics.failed_or_uncertain ?? 0)} failed or uncertain · ${esc(metrics.clarifications ?? 0)} clarifications</p>
      <p class="assistant-card-note">${esc(metrics.note || "")}</p></div></details><h3 class="assistant-subhead">Notifications</h3>
      ${notifications.length ? notifications.map((/** @type {any} */ n) => `<article class="assistant-card"><strong>${esc(n.title)}</strong><p>${esc(n.message)}</p><div class="assistant-card-actions"><a class="link-arrow" href="${esc(n.page)}">Open</a><button class="text-button" data-read-notification="${n.id}" type="button">Mark read</button></div></article>`).join("") : '<p class="assistant-card-note">No unread notifications.</p>'}
      <h3 class="assistant-subhead">Saved conversations & tasks</h3>
      ${conversations.length ? conversations.map((/** @type {any} */ c) => `<article class="assistant-card" data-done="true"><strong>${esc(c.title)}</strong>
        ${c.goal ? `<p>${esc(c.goal)}</p>` : ""}<p class="assistant-card-note">${esc(c.state)} · expires ${esc(c.expires_at.slice(0, 10))}</p>
        <div class="assistant-card-actions"><button class="button" type="button" data-open-saved="${esc(c.id)}">Open</button><button class="text-button danger" type="button" data-delete-saved="${esc(c.id)}">Delete saved copy</button></div></article>`).join("") : emptyMarkup({title: "No saved conversations", body: "Save this conversation above to keep it or give it a task to follow.", heading: "h4"})}
      <h3 class="assistant-subhead" id="assistant-memory">Memory</h3>
      <p class="assistant-card-note">${esc(plural(((data.memory || {}).memories || []).filter((/** @type {any} */ m) => m.status === "current").length, "memory", "memories"))} kept. Everything Faculty remembers, and how it learns, is on one page.</p>
      <p><a class="link-arrow" href="#memory">Open Memory</a></p>
      <h3 class="assistant-subhead">Watches</h3><p class="assistant-card-note">Ask in Chat to watch a scan, grading readiness or an exam. Checks run every two minutes while the worker is running.</p>
      ${watches.map((/** @type {any} */ w) => `<article class="assistant-card" data-done="true"><strong>${esc(w.title)}</strong><p class="assistant-card-note">${w.enabled ? "Watching" : "Paused"} · ${esc(w.condition.replaceAll("_", " "))} · ${w.checked_at ? `last checked ${esc(w.checked_at)}` : "not checked yet"}</p>
        <p>${esc(w.last_error || w.observation?.message || "")}</p><div class="assistant-card-actions"><button class="button" type="button" data-toggle-watch="${esc(w.id)}" data-revision="${w.revision}" data-enabled="${!w.enabled}">${w.enabled ? "Pause" : "Resume"}</button><button class="text-button danger" type="button" data-delete-watch="${esc(w.id)}" data-revision="${w.revision}">Remove watch</button></div></article>`).join("")}
      <h3 class="assistant-subhead">Teaching files</h3><p class="assistant-card-note">Ask in Chat for a worksheet, summary, feedback draft or CSV. Choose its course, term or section, then review the file before saving.</p>
      ${files.map((/** @type {any} */ f) => `<article class="assistant-card" data-done="true"><strong>${esc(f.title)}</strong><a class="link-arrow" ${viewLink(f.url, f.filename, esc)}>${esc(f.filename)}</a><p class="assistant-card-note">${esc(f.created_at.slice(0,10))} · ${f.size} bytes</p></article>`).join("")}`);
  }
  function schedule() {
    window.clearTimeout(timer);
    const running = data.preparations?.some((/** @type {any} */ r) => ["queued", "running"].includes(r.state));
    const enabled = data.workflows?.some((/** @type {any} */ rule) => rule.enabled);
    if (visible && (running || enabled)) timer = window.setTimeout(async () => {
      if (document.visibilityState === "hidden") { schedule(); return; }
      const version = generation;
      try {
        const result = await api(`${BASE}/workflows`);
        if (!visible || version !== generation) return;
        Object.assign(data, result);
        const area = host.querySelector("[data-preparation-runs]");
        if (area) patchChildren(area, preparationRunsMarkup(data));
      } catch (cause) { if (visible) toast(errorMessage(cause), true); }
      schedule();
    }, running ? 5000 : 30000);
  }
  async function refresh() {
    const request = ++generation;
    try {
      const [found, choices] = await Promise.all([api(`${BASE}/workspace`), api(`${BASE}/workflows/options`)]);
      if (!visible || request !== generation) return;
      data = found; options = choices; render(); schedule();
    } catch (cause) { if (visible && request === generation) toast(errorMessage(cause), true); }
  }
  host.onsubmit = (event) => {
    const form = /** @type {HTMLFormElement} */ (event.target);
    if (!form.matches("[data-save-conversation], [data-preparation-form], [data-prepare-again]")) return;
    event.preventDefault();
    void runFormAction(form, async () => {
      const values = Object.fromEntries(new FormData(form));
      if (form.matches("[data-preparation-form]")) {
        const spec = preparationSpec(form);
        if (values.mode === "automatic") {
          if (!form.dataset.fingerprint) {
            const preview = await api(`${BASE}/workflows/preview`, "POST", spec);
            form.dataset.fingerprint = preview.fingerprint;
            const area = form.querySelector("[data-preparation-preview]");
            if (area) area.textContent = `${preview.title}. ${preview.note} Enable this rule?`;
            const button = form.querySelector("button[type=submit]");
            if (button) button.textContent = "Enable automatic preparation";
            return;
          }
          await api(`${BASE}/workflows`, "POST", {...spec, fingerprint: form.dataset.fingerprint, request_id: form.dataset.requestId || (form.dataset.requestId = crypto.randomUUID())});
        } else await api(`${BASE}/preparations`, "POST", {...spec, request_id: form.dataset.requestId || (form.dataset.requestId = crypto.randomUUID())});
      } else if (form.matches("[data-prepare-again]")) {
        const key = `again:${form.dataset.prepareAgain}`;
        if (!requests.has(key)) requests.set(key, crypto.randomUUID());
        await api(`${BASE}/preparations`, "POST", {kind: form.dataset.kind, target: form.dataset.target, instruction: values.instruction,
          request_id: requests.get(key)});
        requests.delete(key);
      } else {
        const c = await ensure();
        if (!c) return;  // New conversation retired the one this form was saving.
        c.saved = await api(`${BASE}/conversations/${c.id}/save`, "POST", {...values, retention_days: Number(values.retention_days)});
      }
      delete form.dataset.requestId;
      delete form.dataset.fingerprint;
      if (visible) await refresh();
    });
  };
  host.onchange = (event) => {
    const form = (/** @type {HTMLElement} */ (event.target)).closest("form[data-preparation-form]");
    if (!form) return;
    updatePreparationTiming(/** @type {HTMLFormElement} */ (form));
    delete /** @type {HTMLFormElement} */ (form).dataset.fingerprint;
    delete /** @type {HTMLFormElement} */ (form).dataset.requestId;
    const button = form.querySelector("button[type=submit]");
    if (button) button.textContent = /** @type {HTMLSelectElement} */ (form.querySelector("[name=mode]")).value === "automatic" ? "Review automatic preparation" : "Prepare now";
    const area = form.querySelector("[data-preparation-preview]");
    if (area) area.textContent = "";
  };
  host.onclick = async (event) => {
    const button = /** @type {HTMLButtonElement | null} */ ((/** @type {HTMLElement} */ (event.target)).closest("button"));
    if (!button || button.type === "submit" || button.hasAttribute("data-assistant-tab")) return;
    setBusy(button, true);
    try {
      const d = button.dataset;
      if (d.stopPreparation) await api(`${BASE}/preparations/${d.stopPreparation}/stop`, "POST", {});
      if (d.togglePreparation) await api(`${BASE}/workflows/${d.togglePreparation}`, "POST", {revision: Number(d.revision), enabled: d.enabled === "true"});
      if (d.openSaved) { const view = await api(`${BASE}/saved/${d.openSaved}/open`, "POST", {}); opened(view); return; }
      if (d.deleteSaved) { await api(`${BASE}/saved/${d.deleteSaved}`, "DELETE", {}); if (current()?.id === d.deleteSaved) current().saved = {}; }
      if (d.toggleWatch) await api(`${BASE}/watches/${d.toggleWatch}`, "POST", {revision: Number(d.revision), enabled: d.enabled === "true"});
      if (d.deleteWatch) await api(`${BASE}/watches/${d.deleteWatch}`, "DELETE", {revision: Number(d.revision)});
      if (d.readNotification) await api(`${BASE}/notifications/read`, "POST", {ids: [Number(d.readNotification)]});
      if (visible) await refresh();
    } catch (cause) { toast(errorMessage(cause), true); }
    finally { setBusy(button, false); }
  };
  return {show() { visible = true; return refresh(); }, hide() {visible = false; generation += 1; window.clearTimeout(timer);}};
}
