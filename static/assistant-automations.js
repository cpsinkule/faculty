// ASSISTANT / FRONTEND: one read-only automation inventory; controls use their existing owners.
import { esc } from "./assistant-markdown.js";
import { patchChildren } from "./dom-patch.js";
import { errorMessage, runFormAction, setBusy } from "./ui.js";
import { preparationMarkup, preparationRunsMarkup, preparationSpec, updatePreparationTiming } from "./assistant-preparations.js";
import { CAMPUS_ZONE } from "./calendar-feed.js";
import { recoveryPrompt } from './assistant-recovery.js';

const STATES = {queued: "Queued", running: "Preparing review", ready: "Ready to review", failed: "Needs attention", interrupted: "Stopped", stale: "Superseded", needs_input: "Needs your guidance"};
// What the instructor reads after Prepare now (2026-10-09 review: it gave no feedback); the run itself is listed below the form.
export const PREPARING = "Preparing in the background · its changes will appear here";

/** @param {string | number} value */
function observedTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(undefined,
    {timeZone: CAMPUS_ZONE, month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(date) + " Central";
}

/** A rule's next check, or "" when there is none to state: paused, held, or a built-in whose schedule line already says when. @param {any} row */
export function nextCheck(row) {
  if (!row.enabled || row.held || !row.next_check) return "";
  return observedTime(row.next_check * 1000);
}

/**
 * The row's result line and its label. `last_outcome` (with `last_checked_at`) is a real result; a ready run with nothing left to
 * approve was reviewed; a built-in row's fixed description says how it works. Pure.
 * @param {any} row
 * @returns {{label: string, text: string}}
 */
export function lastResult(row) {
  if (row.last_outcome) return {label: row.outcome_label || "Last result", text: row.last_outcome + (row.last_checked_at ? ` · ${observedTime(row.last_checked_at)}` : "")};
  if (row.about) return {label: "How it works", text: row.last_result || ""};
  if (row.last_result === "ready" && !row.pending) return {label: "Last result", text: "Reviewed"};
  return {label: "Last result", text: STATES[/** @type {keyof typeof STATES} */ (row.last_result)] || row.last_result || "Not checked yet"};
}

/** The runs worth showing on Automation: queued, running, waiting for guidance, or ready with changes to approve. Pure. @param {any[]} runs */
export function activeRuns(runs) {
  return (runs || []).filter((run) => ["queued", "running", "needs_input"].includes(run.state)
    || run.state === "ready" && (run.actions || []).some((/** @type {any} */ action) => action.pending));
}

/** "Done by Faculty · last 7 days": one line each, with its time and owning page (the Assistant's own changes open Changes). @param {any} done */
export function doneMarkup(done) {
  const lines = done?.lines || [];
  const days = done?.days || 7;
  return `<details class="disclosure" data-patch-key="automation-done"><summary><span>Done by Faculty · last ${days} days</span><span class="disclosure-hint">${lines.length + (done?.more || 0)}</span></summary>
    <div class="disclosure-body">${lines.length ? `<ul class="assistant-done-list" role="list">${lines.map((/** @type {any} */ line) => `<li><span class="assistant-card-note">${esc(observedTime(line.at))}</span>
      ${line.tab ? `<button class="text-button" type="button" data-assistant-tab="${esc(line.tab)}">${esc(line.text)}</button>` : `<a class="link-arrow" href="${esc(line.href)}">${esc(line.text)}</a>`}</li>`).join("")}</ul>
      ${done?.more ? `<p class="assistant-card-note">${done.more} earlier not shown.</p>` : ""}` : `<p class="assistant-card-note">Nothing Faculty did by itself in the last ${days} days.</p>`}</div></details>`;
}

/** @param {any} row */
function rowMarkup(row) {
  const next = nextCheck(row), result = lastResult(row);
  const repair = row.blocker || (row.observations || []).some((/** @type {any} */ o) => o.blocker);
  return `<article class="assistant-card" data-patch-key="automation-${esc(row.kind)}-${esc(row.id)}">
      <strong>${esc(row.title)}</strong><p>${esc(row.scope)} · ${row.enabled ? "On" : "Paused"}</p>
      <p class="assistant-card-note">${esc(row.schedule)}${next ? `<br>Next: ${esc(next)}` : ""}</p>
      <p>${esc(result.label)}: ${esc(result.text)}</p>
      ${row.note ? `<p class="assistant-card-note">${esc(row.note)}</p>` : ""}
      ${(row.observations || []).map((/** @type {any} */ observation) => `<p class="assistant-card-note"><a href="${esc(observation.href)}">${esc(observation.scope)}</a>: ${esc(observation.status)} · ${esc(observedTime(observation.at))}${observation.blocker ? `<br>${esc(observation.blocker)}` : ""}</p>`).join("")}
      ${row.waiting ? `<p class="assistant-card-note" data-automation-waiting>${esc(row.waiting)}</p>` : ""}
      ${row.held ? `<p role="status" data-automation-held>${esc(row.held)}</p>` : ""}
      ${row.blocker ? `<p role="status">${esc(row.blocker)}</p>` : ""}
      <div class="assistant-card-actions">
      ${row.held && row.prepare ? `<button type="button" class="button" data-automation-prepare-again data-kind="${esc(row.prepare.kind)}" data-target="${esc(row.prepare.target)}">Prepare again</button>` : ""}
      ${repair ? `<button type="button" class="button" data-automation-repair="${esc(row.href)}" data-title="${esc(row.title)}">Diagnose and prepare fix</button>` : ""}
      ${row.pending ? `<button class="button" type="button" data-assistant-tab="changes">Review ${row.pending} changes</button>` : ""}
      ${row.kind !== "built-in" ? `<button class="button" type="button" data-automation-toggle="${esc(row.id)}" data-kind="${esc(row.kind)}" data-revision="${row.revision}" data-enabled="${!row.enabled}">${row.enabled ? "Pause" : "Resume"}</button>` : ""}
      <a class="link-arrow" href="${esc(row.href)}">Open owning page</a></div></article>`;
}

/** @param {any} data */
export function automationMarkup(data) {
  return `<h3 class="assistant-subhead">Automations</h3><p>See what runs, what it covers, and what needs you. Changes keep their existing review controls.</p>
    ${(data.automations || []).map(rowMarkup).join("")}
    ${doneMarkup(data.done)}
    <h3 class="assistant-subhead">Review friction</h3><p>${data.metrics?.waiting_over_day || 0} changes waiting over a day · ${data.metrics?.unfinished_preparations || 0} unfinished preparations · ${data.metrics?.stopped_or_failed_preparations || 0} stopped or failed</p>
    <p class="assistant-card-note">${esc(data.metrics?.note || "")}</p>`;
}

export function createAutomationTab({host, api, toast, ask}) {
  let visible = false, generation = 0, timer;
  let data = /** @type {any} */ ({}), options = /** @type {any} */ (null), optionsKey = "";
  // Request IDs of explicit Prepare again clicks, by kind and target: an uncertain request is sent again under its own ID, never a new one.
  const requests = new Map();
  const running = () => (data.preparations || []).some((/** @type {any} */ run) => ["queued", "running"].includes(run.state));
  // Active runs live below the form (2026-10-09 review: they were only on Saved); keyed patches keep typed guidance.
  function drawRuns() {
    const area = /** @type {HTMLElement | null} */ (host.querySelector('[data-automation-prepare] [data-preparation-runs]'));
    if (area) patchChildren(area, preparationRunsMarkup({workflows: [], preparations: activeRuns(data.preparations)}));
  }
  // The quiet 30 s refresh patches the inventory only. The preparation form keeps its preview, fingerprint and request ID
  // (an uncertain request is checked, never resent under a new ID); its choices are read when the tab opens and after a
  // create, never on the timer (each read visits every current exam's overview).
  /** @param {{choices?: boolean}} [how] */
  async function refresh({choices = false} = {}) {
    const current = ++generation;
    try {
      const [found, metrics, fresh] = await Promise.all([api('/api/assistant/automations'), api('/api/assistant/metrics'),
        choices || !options ? api('/api/assistant/workflows/options') : Promise.resolve(null)]);
      if (!visible || current !== generation) return;
      if (!host.querySelector('[data-automation-list]')) host.innerHTML = '<div data-automation-list></div><div data-automation-prepare></div>';
      data = {...found, metrics};
      patchChildren(/** @type {HTMLElement} */ (host.querySelector('[data-automation-list]')), automationMarkup(data));
      const form = /** @type {HTMLFormElement | null} */ (host.querySelector('[data-preparation-form]'));
      const key = fresh ? JSON.stringify(fresh) : optionsKey;
      if (fresh && key !== optionsKey && !form?.dataset.fingerprint && !form?.dataset.requestId) {
        options = fresh; optionsKey = key;
        /** @type {HTMLElement} */ (host.querySelector('[data-automation-prepare]')).innerHTML = preparationMarkup({workflows: [], preparations: []}, options);
      }
      drawRuns();
    } catch (cause) { if (visible) toast(errorMessage(cause), true); }
    schedule();
  }
  // While a run is queued or running, only the runs are read again, every five seconds; otherwise the whole tab every thirty.
  async function refreshRuns() {
    const current = generation;
    try {
      const found = await api('/api/assistant/workflows');
      if (!visible || current !== generation) return;
      data = {...data, preparations: found.preparations};
      drawRuns();
      if (!running()) { void refresh(); return; }
    } catch (cause) { if (visible) toast(errorMessage(cause), true); }
    schedule();
  }
  function schedule() {
    clearTimeout(timer);
    if (!visible) return;
    const fast = running();
    timer = setTimeout(() => {
      if (document.visibilityState === 'hidden') schedule();
      else void (fast ? refreshRuns() : refresh());
    }, fast ? 5000 : 30000);
  }
  /** @param {HTMLFormElement} form */
  function resetForm(form) {
    delete form.dataset.fingerprint; delete form.dataset.requestId;
    form.reset();
    updatePreparationTiming(form);
    /** @type {HTMLButtonElement} */ (form.querySelector('[type=submit]')).textContent = 'Prepare now';
  }
  host.onchange = (event) => {
    const form = event.target.closest('[data-preparation-form]');
    if (!form) return;
    updatePreparationTiming(form);
    delete form.dataset.fingerprint; delete form.dataset.requestId;
    form.querySelector('[data-preparation-preview]').textContent = '';
    form.querySelector('[type=submit]').textContent = form.elements.mode.value === 'automatic' ? 'Review automatic preparation' : 'Prepare now';
  };
  host.onsubmit = (event) => {
    const again = event.target.closest('[data-prepare-again]');
    if (again) {
      event.preventDefault();
      // Its request ID is kept here, not on the form: the five-second patch brings the form's attributes back to the markup.
      const key = `again:${again.dataset.prepareAgain}`;
      if (!requests.has(key)) requests.set(key, crypto.randomUUID());
      void runFormAction(again, async () => {
        await api('/api/assistant/preparations', 'POST', {kind: again.dataset.kind, target: again.dataset.target, instruction: new FormData(again).get('instruction') || '',
          request_id: requests.get(key)});
        requests.delete(key);
        toast(PREPARING);
        await refresh();
      });
      return;
    }
    const form = event.target.closest('[data-preparation-form]');
    if (!form) return;
    event.preventDefault();
    void runFormAction(form, async () => {
      const spec = preparationSpec(form);
      if (form.elements.mode.value === 'automatic' && !form.dataset.fingerprint) {
        const preview = await api('/api/assistant/workflows/preview', 'POST', spec);
        form.dataset.fingerprint = preview.fingerprint;
        form.querySelector('[data-preparation-preview]').textContent = preview.note;
        form.querySelector('[type=submit]').textContent = 'Enable automatic preparation';
        return;
      }
      const automatic = form.elements.mode.value === 'automatic';
      const created = await api('/api/assistant/' + (automatic ? 'workflows' : 'preparations'), 'POST', {...spec,
        fingerprint: form.dataset.fingerprint, request_id: form.dataset.requestId || (form.dataset.requestId = crypto.randomUUID())});
      // After a create the form starts over (2026-10-09 review: a second Enable click ended in "already enabled"), and says what happened.
      resetForm(form);
      await refresh({choices: true});
      const rule = automatic ? (data.automations || []).find((/** @type {any} */ row) => row.kind === 'workflow' && row.id === created?.id) : null;
      const status = host.querySelector('[data-preparation-form] [data-preparation-preview]');
      if (status) status.textContent = automatic ? `Enabled. First check: ${rule && nextCheck(rule) || 'within two minutes'}` : PREPARING;
    });
  };
  host.onclick = async (event) => {
    const repair = event.target.closest('[data-automation-repair]');
    if (repair && ask) {
      setBusy(repair, true);
      // The request names the row; the page goes as review context. An error is a toast, never the button's label.
      try { await ask(recoveryPrompt(repair.dataset.title), {reviewOnly: true, context: {kind: 'recovery', page: repair.dataset.automationRepair}}); }
      catch (cause) { toast(errorMessage(cause), true); }
      finally { setBusy(repair, false); }
      return;
    }
    const prepare = event.target.closest("[data-automation-prepare-again]");
    if (prepare) {
      // A held rule's Prepare again: the instructor's explicit request through the existing preparation route.
      const key = `${prepare.dataset.kind}:${prepare.dataset.target}`;
      if (!requests.has(key)) requests.set(key, crypto.randomUUID());
      setBusy(prepare, true);
      try {
        await api('/api/assistant/preparations', 'POST', {kind: prepare.dataset.kind, target: prepare.dataset.target, request_id: requests.get(key)});
        requests.delete(key);
        toast(PREPARING);
        await refresh();
      } catch (cause) { toast(errorMessage(cause), true); }
      finally { setBusy(prepare, false); }
      return;
    }
    const stop = event.target.closest('[data-stop-preparation]');
    if (stop) {
      setBusy(stop, true);
      try { await api(`/api/assistant/preparations/${stop.dataset.stopPreparation}/stop`, 'POST', {}); await refresh(); }
      catch (cause) { toast(errorMessage(cause), true); }
      finally { setBusy(stop, false); }
      return;
    }
    const button = event.target.closest('[data-automation-toggle]');
    if (!button) return;
    setBusy(button, true);
    try {
      await api('/api/assistant/' + (button.dataset.kind === 'watch' ? 'watches/' : 'workflows/') + button.dataset.automationToggle,
        'POST', {enabled: button.dataset.enabled === 'true', revision: Number(button.dataset.revision)});
      await refresh();
    } catch (cause) { toast(errorMessage(cause), true); }
    finally { setBusy(button, false); }
  };
  return {show() { visible = true; return refresh({choices: true}); }, hide() { visible = false; generation++; clearTimeout(timer); }};
}
