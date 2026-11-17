// @ts-check
// ASSISTANT: opt-in preparation controls; reviewed actions stay in Changes.
import { esc } from "./assistant-markdown.js";

/** @param {string} target */
export function preparationTiming(target) {
  const daily = target.startsWith("reconciliation:");
  return {daily, automatic: daily ? "Daily at 5:00 AM Central" : "When the records change",
    hint: daily ? "Checks grade discrepancies after the morning Canvas sync. Corrections wait for your approval."
      : "Automatic preparation also checks when an assessment enters this reminder window. It never sends, certifies or publishes."};
}

/** @param {HTMLFormElement} form */
export function updatePreparationTiming(form) {
  const target = /** @type {HTMLSelectElement} */ (form.querySelector("[name=target]"));
  const timing = preparationTiming(target.value);
  const option = form.querySelector("[name=mode] [value=automatic]");
  if (option) option.textContent = timing.automatic;
  // A one-off run has no rule and is observed with the default lead (ASSISTANT, 2026-10-09 review): the reminder is a rule's setting.
  const mode = /** @type {HTMLSelectElement|null} */ (form.querySelector("[name=mode]"));
  const off = timing.daily || !mode || mode.value !== "automatic";
  const reminder = /** @type {HTMLElement} */ (form.querySelector("[data-preparation-reminder]"));
  reminder.hidden = off;
  /** @type {HTMLInputElement} */ (reminder.querySelector("input")).disabled = off;
  const hint = form.querySelector("[data-preparation-hint]");
  if (hint) hint.textContent = timing.hint;
}

/** @param {any} data @param {any} options */
export function preparationMarkup(data, options) {
  const targets = [...(options.exams || []).map((/** @type {any} */ e) => ({value: `readiness:${e.id}`, label: `Assessment readiness · ${e.title}`})),
    ...(options.terms || []).map((/** @type {any} */ t) => ({value: `reconciliation:${t.id}`, label: `Grade reconciliation · ${t.code} · ${t.term} ${t.year}`}))];
  const timing = preparationTiming(targets[0]?.value || "");
  return `<details class="disclosure" data-patch-key="preparation-setup"><summary><span>Prepare teaching work</span></summary>
    <div class="disclosure-body"><p>Have the Assistant prepare missing steps and corrections. Every change still waits for your review in Changes.</p>
    ${targets.length ? `<form class="assistant-workspace-form" data-preparation-form>
      <label>Work to prepare<select name="target">${targets.map(t => `<option value="${esc(t.value)}">${esc(t.label)}</option>`).join("")}</select></label>
      <label>When<select name="mode"><option value="once">Once, now</option><option value="automatic">${timing.automatic}</option></select></label>
      <label data-preparation-reminder hidden>Assessment reminder, days ahead<input type="number" name="lead_days" min="1" max="30" value="7" required disabled></label>
      <p class="field-hint" data-preparation-hint>${timing.hint}</p>
      <div data-preparation-preview role="status"></div><p class="form-error" role="alert" hidden></p>
      <button class="button" type="submit">Prepare now</button></form>` : '<p>Add a current assessment or course term to prepare its work.</p>'}</div></details>
    <div data-preparation-runs>${preparationRunsMarkup(data)}</div>`;
}

const RUN_STATES = {queued: "Waiting for the Assistant", running: "Preparing review", ready: "Ready to review", failed: "Preparation failed", interrupted: "Preparation interrupted", stale: "Draft withdrawn", needs_input: "Needs your guidance"};

/**
 * Runs grouped by what they prepare (kind and target), in the order given (newest first): each group's first run is its newest.
 * @param {any[]} runs
 * @returns {any[][]}
 */
export function groupRuns(runs) {
  const groups = new Map();
  for (const run of runs || []) {
    const key = `${run.kind}\u0000${run.target}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(run);
  }
  return [...groups.values()];
}

/** @param {string} value */
function runDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value || "") : new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(date);
}

/** Older drafts of one target, folded: state and date only, no form (2026-10-09 review: one Prepare again per target). @param {any[]} older */
function earlierMarkup(older) {
  if (!older.length) return "";
  return `<details class="disclosure"><summary><span>${older.length} earlier draft${older.length === 1 ? "" : "s"}</span></summary>
    <ul class="assistant-earlier-list" role="list">${older.map((run) => `<li>${esc(RUN_STATES[/** @type {keyof typeof RUN_STATES} */ (run.state)] || run.state)} · ${esc(runDate(run.created_at))}</li>`).join("")}</ul></details>`;
}

/** @param {any} data */
export function preparationRunsMarkup(data) {
  const rules = data.workflows || [];
  const states = RUN_STATES;
  return `${rules.map((/** @type {any} */ rule) => `<article class="assistant-card" data-patch-key="rule-${esc(rule.id)}"><strong>${esc(rule.title)}</strong>
    <p class="assistant-card-note">${rule.enabled ? rule.kind === "reconciliation" ? "Daily at 5:00 AM Central" : "Automatic preparation enabled" : "Paused"}</p>${rule.last_error ? `<p role="status">${esc(rule.last_error)}</p>` : ""}
    <button class="button" type="button" data-toggle-preparation="${esc(rule.id)}" data-revision="${rule.revision}" data-enabled="${!rule.enabled}">${rule.enabled ? "Pause" : "Resume"}</button></article>`).join("")}
    ${groupRuns(data.preparations).map(([run, ...older]) => `<article class="assistant-card" data-patch-key="preparation-${esc(run.id)}"><strong>${esc(run.title)}${run.stage > 1 ? ` · Stage ${run.stage}` : ""}</strong>
    <p class="assistant-card-note" role="status">${esc(states[run.state] || run.state)}</p>
    ${["queued", "running"].includes(run.state) ? `<button class="button" type="button" data-stop-preparation="${esc(run.id)}">Stop preparation</button>` : ""}
    ${run.next_stage && !["queued", "running"].includes(run.state) ? `<p class="assistant-card-note">The next stage starts after these changes succeed.</p><button class="button" type="button" data-stop-preparation="${esc(run.id)}">Cancel next stage</button>` : ""}
    ${(run.question?.questions || []).map((/** @type {any} */ q) => `<p><strong>${esc(q.title)}</strong>${q.options?.length ? ` ${q.options.map(esc).join(" / ")}` : ""}</p>`).join("")}
    ${run.explanation ? `<details class="disclosure"><summary><span>Preparation notes</span></summary><p>${esc(run.explanation)}</p></details>` : ""}
    ${run.actions?.some((/** @type {any} */ a) => a.pending) ? '<button class="button" type="button" data-assistant-tab="changes">Review changes</button>' : ""}
    ${run.kind === "memo" ? `<p class="assistant-card-note">A request you dictated in a memo. Ask it again from <a href="${esc(run.page || "#recordings")}">the memo\'s page</a>.</p>` : ""}
    ${["failed", "interrupted", "stale", "needs_input", "ready"].includes(run.state) && run.kind !== "watch" && run.kind !== "memo" ? `<form class="assistant-workspace-form" data-prepare-again="${esc(run.id)}" data-kind="${esc(run.kind)}" data-target="${esc(run.target)}">
      <label>Guidance, if needed<textarea name="instruction" rows="2" maxlength="5000"></textarea></label><p class="form-error" role="alert" hidden></p><button class="button" type="submit">Prepare again</button></form>` : ""}
    ${earlierMarkup(older)}</article>`).join("")}`;
}

/** @param {HTMLFormElement} form */
export function preparationSpec(form) {
  const fields = new FormData(form), target = String(fields.get("target") || "").split(":");
  return {kind: target[0], target: target.slice(1).join(":"), lead_days: Number(fields.get("lead_days") ?? 7)};
}
