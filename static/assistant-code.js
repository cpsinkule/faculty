// @ts-check
// The assistant drawer's Code tab (assistant/coding.py, AGENTS.md "Faculty assistant"): changes of Faculty itself, each made by a
// coding agent in its own copy. The instructor follows a task's rounds, reads its report and diff, asks for changes, and applies
// (merge, restart, push) or discards it. Every action here is the instructor's click; the assistant only proposes a task.
import { calloutMarkup, emptyMarkup, errorMessage } from "./ui.js";
import { esc, markdown, multiline } from "./assistant-markdown.js";
import { patchChildren } from "./dom-patch.js";

const BASE = "/api/assistant/code-tasks";
const STATES = /** @type {Record<string, [string, string]>} */ ({
  running: ["Working", "info"], ready: ["Ready to apply", "warning"], failed: ["Stopped or failed", "danger"],
  applied: ["Applied", "success"], discarded: ["Discarded", "neutral"],
});
const DIFF_LINES = 5000;

/** A unified diff as escaped lines, each marked by kind. @param {string} diff */
export function diffMarkup(diff) {
  const lines = String(diff || "").split("\n");
  const shown = lines.slice(0, DIFF_LINES).map((line) => {
    const kind = line.startsWith("diff --git") ? "file" : line.startsWith("@@") ? "hunk" : line.startsWith("+") && !line.startsWith("+++") ? "add"
      : line.startsWith("-") && !line.startsWith("---") ? "del" : "";
    return `<span class="assistant-diff-line"${kind ? ` data-kind="${kind}"` : ""}>${esc(line) || " "}</span>`;
  }).join("");
  return `<pre class="assistant-diff">${shown}${lines.length > DIFF_LINES ? `<span class="assistant-diff-line">… ${lines.length - DIFF_LINES} more lines</span>` : ""}</pre>`;
}

// Apply's stages (CodingService._applying): what the one Apply click is doing now.
const STAGES = /** @type {Record<string, string>} */ ({
  starting: "starting", rebasing: "bringing it up to date with main", checking: "running checks", merging: "merging",
  pushing: "pushing to GitHub", restarting: "scheduling the restart",
});

/** "Applying · running checks 2 of 4 · 3 min": the stage and how long Apply has run. @param {any} applying @param {number} [now] */
export function applyingLabel(applying, now = Date.now()) {
  const stage = STAGES[applying?.stage] || "working";
  const commands = Number(applying?.commands) || 0;
  const step = applying?.stage === "checking" && commands > 1 ? ` ${Math.min(commands, (Number(applying.done) || 0) + 1)} of ${commands}` : "";
  const started = Date.parse(applying?.started_at || "");
  const minutes = Number.isFinite(started) ? Math.max(0, Math.floor((now - started) / 60000)) : 0;
  return `Applying · ${stage}${step} · ${minutes < 1 ? "under a minute" : `${minutes} min`}`;
}

/** @param {string} state */
const chip = (state) => { const [label, tone] = STATES[state] || [state, "neutral"]; return `<span class="chip" data-tone="${tone}">${esc(label)}</span>`; };

/** One task, opened. @param {any} task @param {{confirming?: boolean}} [options] */
export function taskMarkup(task, { confirming = false } = {}) {
  const rounds = (task.rounds || []).map((round) => {
    const steps = round.steps || [];
    const recent = steps.slice(-8), older = steps.slice(0, -8);
    return `<section class="assistant-round"><h4 class="assistant-subhead">Round ${esc(round.number)} · ${esc(round.state)}${round.cost ? ` · $${Number(round.cost).toFixed(2)}` : ""}</h4>
      ${older.length ? `<details class="disclosure"><summary><span>${older.length} earlier steps</span></summary><div class="disclosure-body">${older.map((s) => `<p class="assistant-tool">${esc(s)}</p>`).join("")}</div></details>` : ""}
      ${recent.map((s) => `<p class="assistant-tool">${esc(s)}</p>`).join("")}
      ${round.state === "running" ? '<p class="assistant-tool assistant-working" role="status">Working…</p>' : ""}
      ${round.summary ? `<div class="assistant-msg" data-from="assistant">${markdown(round.summary)}</div>` : ""}
      ${round.error ? calloutMarkup({ tone: "danger", title: "What went wrong", body: round.error }) : ""}</section>`;
  }).join("");
  const files = task.files || [];
  const changes = files.length
    ? `<h4 class="assistant-subhead">${files.length} file${files.length === 1 ? "" : "s"} changed · ${(task.commits || []).length} commit${(task.commits || []).length === 1 ? "" : "s"}</h4>
       <ul class="assistant-files">${files.map((f) => `<li><code>${esc(f.path)}</code> <span class="assistant-diffstat" data-kind="add">+${esc(f.added)}</span> <span class="assistant-diffstat" data-kind="del">−${esc(f.removed)}</span></li>`).join("")}</ul>
       ${(task.migrations || []).length ? calloutMarkup({ tone: "warning", title: "Adds a migration", body: `${task.migrations.join(", ")}: it is applied to the live catalog within a minute of Apply, and cannot be undone by reverting the code.` }) : ""}
       <details class="disclosure"><summary><span>The diff</span></summary><div class="disclosure-body">${diffMarkup(task.diff)}</div></details>`
    : task.state === "ready" ? calloutMarkup({ tone: "neutral", title: "No changes", body: "The agent committed nothing. Ask for changes, or discard the task." }) : "";
  const events = (task.events || []).filter((e) => ["applied", "apply_failed", "pushed", "push_failed", "restart_scheduled", "restart_skipped", "checks_passed"].includes(e.kind));
  const outcome = events.map((e) => {
    const said = { applied: "Merged into Faculty.", pushed: "Pushed to GitHub.", restart_scheduled: "Faculty restart scheduled.", checks_passed: `Required checks passed for commit ${e.detail?.head?.slice(0, 10) || ""}.${(e.detail?.not_run || []).length ? ` ${e.detail.not_run.length} more matching check modules were over the limit and did not run.` : ""}`, apply_failed: "Not applied: ",
      push_failed: "Not pushed: ", restart_skipped: "Not restarted: " }[/** @type {string} */ (e.kind)] || e.kind;
    return `<p class="assistant-tool" data-ok="${e.kind.endsWith("failed") || e.kind === "restart_skipped" ? "false" : "true"}">${esc(said)}${esc(e.detail?.reason || "")}</p>`;
  }).join("");
  let actions = "";
  if (task.applying) {
    // Apply is running on the server (the POST holds until it ends): one status line, nothing to click twice.
    const checks = task.applying.modules || task.applying.scripts
      ? `<p class="assistant-card-note">${esc(task.applying.modules || 0)} Python and ${esc(task.applying.scripts || 0)} JavaScript check modules selected.</p>` : "";
    actions = `<div class="callout" data-tone="neutral" role="status" data-code-applying="${esc(task.id)}"><strong class="callout-title">${esc(applyingLabel(task.applying))}</strong>${checks}
        <div class="assistant-card-actions"><button class="button" type="button" disabled aria-disabled="true">Applying…</button></div></div>`;
  } else if (task.state === "running") actions = `<button class="button" type="button" data-code-stop="${esc(task.id)}">Stop</button>`;
  else if (task.state === "ready" || task.state === "failed") {
    actions = `<form class="assistant-code-follow" data-code-follow="${esc(task.id)}"><label class="sr-only" for="code-follow-${esc(task.id)}">Ask for changes</label>
        <textarea id="code-follow-${esc(task.id)}" name="text" rows="2" maxlength="20000" placeholder="Ask for changes to this work…"></textarea>
        <div class="assistant-card-actions"><button class="button" type="submit">Ask for changes</button><button class="text-button" type="button" data-code-discard="${esc(task.id)}">Discard</button></div></form>`;
    if (task.state === "ready" && files.length) {
      actions += confirming
        ? `<div class="callout" data-tone="warning" role="status"><strong class="callout-title">Apply this change?</strong><p>Faculty runs the required checks against this commit, then merges, pushes and restarts. Apply is refused while Faculty is busy, the morning sync with Canvas included.</p>
            <div class="assistant-card-actions"><button class="button" type="button" data-code-apply="${esc(task.id)}" data-head="${esc(task.head)}" data-seen="${esc(task.seen)}">Apply now</button><button class="text-button" type="button" data-code-cancel>Cancel</button></div></div>`
        : `<div class="assistant-card-actions"><button class="button" type="button" data-code-confirm="${esc(task.id)}">Apply…</button><span class="assistant-card-note">Merge, restart and push.</span></div>`;
    }
  }
  return `<article class="assistant-code-task" data-code-task="${esc(task.id)}" data-patch-key="code-${esc(task.id)}">
    <header class="assistant-card-head">${chip(task.state)}<strong>${esc(task.title)}</strong></header>
    <p class="assistant-card-route">${esc(task.coder === "codex" ? "Codex" : "Claude Code")}${task.model ? ` · ${esc(task.model)}` : ""} · from ${esc(task.base)}${task.page ? ` · asked on <a class="assistant-route" href="${esc(task.page)}">${esc(task.page)}</a>` : ""}</p>
    <blockquote class="assistant-request">${multiline(task.request)}</blockquote>
    ${task.brief ? `<details class="disclosure"><summary><span>What the assistant gathered</span></summary><div class="disclosure-body assistant-msg">${markdown(task.brief)}</div></details>` : ""}
    ${rounds}${changes}${outcome}${actions}</article>`;
}

/**
 * The Code tab. `host` is the tab's container; `api` the shared client; `toast` the shell's.
 * @param {{host: HTMLElement, api: Function, toast: Function}} tools
 */
export function createCodeTab({ host, api, toast }) {
  let tasks = /** @type {any[]} */ ([]), coder = /** @type {any} */ (null), open = "", detail = /** @type {any} */ (null);
  let confirming = false, timer = 0, visible = false, applying = "", started = "";
  let generation = 0;
  const drafts = new Map();

  async function read() {
    const current = ++generation;
    try {
      const data = await api(BASE);
      if (!visible || current !== generation) return;
      if (!open && data.tasks.length) open = data.tasks[0].id;
      const id = open;
      const found = id ? await api(`${BASE}/${id}`) : null;
      if (!visible || current !== generation || id !== open) return;
      tasks = data.tasks; coder = data.coder; detail = found;
      // Between the click and the server's first stage (or behind another Apply), still show this task as applying.
      if (detail && applying === detail.id && !detail.applying && detail.state === "ready") detail = { ...detail, applying: { stage: "starting", started_at: started } };
    } catch (cause) {
      if (!visible || current !== generation) return;
      host.innerHTML = calloutMarkup({ tone: "danger", title: "The coding tasks could not be read", body: errorMessage(cause) });
      schedule();
      return;
    }
    draw();
  }
  function draw() {
    const head = coder ? `<p class="assistant-card-note">Coding agent: ${esc(coder.name)}${coder.available ? "" : ` · ${esc(coder.reason)}`} · <a class="assistant-route" href="#grading?settings=1">change</a></p>` : "";
    if (!tasks.length) {
      patchChildren(host, head + emptyMarkup({ title: "No code changes yet", body: "Ask the assistant to change something about Faculty, a page you are on or a feature. It gathers what the change needs and proposes a task for your approval.", heading: "h3" }));
      schedule();
      return;
    }
    const list = tasks.length > 1 ? `<div class="assistant-code-list">${tasks.map((t) => `<button type="button" class="assistant-code-item" data-code-open="${esc(t.id)}" aria-current="${t.id === open}">${chip(t.state)}<span>${esc(t.title)}</span></button>`).join("")}</div>` : "";
    patchChildren(host, head + list + (detail ? taskMarkup(detail, { confirming }) : ""));
    const field = /** @type {HTMLTextAreaElement | null} */ (host.querySelector("textarea"));
    if (field && detail && field.value !== (drafts.get(detail.id) || "")) field.value = drafts.get(detail.id) || "";
    schedule();
  }
  function schedule() {
    clearTimeout(timer);
    if (!visible || document.hidden) return;
    const running = tasks.some((t) => t.state === "running" || t.applying) || Boolean(applying);
    timer = window.setTimeout(() => { if (document.hidden || host.querySelector("textarea:focus")) schedule(); else void read(); }, running ? 4000 : 30000);
  }
  async function act(/** @type {string} */ path, /** @type {object} */ body, /** @type {string} */ done) {
    const id = path.slice(BASE.length + 1).split("/")[0];
    const isApply = path.endsWith("/apply");
    try {
      await api(path, "POST", body, {}, isApply ? 3600000 : 300000);
      if (path.endsWith("/follow-up") && String(drafts.get(id) || "").trim() === body.text) drafts.delete(id);
      if (done) toast(done);
    } catch (cause) {
      toast(errorMessage(cause), true);
    }
    if (id === open) confirming = false;
    if (isApply) applying = "";
    await read();
  }
  host.addEventListener("input", (event) => {
    const field = /** @type {HTMLTextAreaElement} */ (event.target);
    const id = field.closest("[data-code-follow]")?.getAttribute("data-code-follow");
    if (id) drafts.set(id, field.value);
  });
  document.addEventListener("visibilitychange", () => { if (visible && !document.hidden) void read(); });
  host.addEventListener("click", (event) => {
    const button = /** @type {HTMLElement} */ (event.target)?.closest?.("button");
    if (!button) return;
    const data = button.dataset;
    if (data.codeOpen) { open = data.codeOpen; confirming = false; void read(); }
    else if (data.codeStop) void act(`${BASE}/${data.codeStop}/stop`, {}, "Stopped.");
    else if (data.codeDiscard) void act(`${BASE}/${data.codeDiscard}/discard`, {}, "Discarded.");
    else if (data.codeConfirm) { confirming = true; draw(); }
    else if ("codeCancel" in data) { confirming = false; draw(); }
    else if (data.codeApply) {
      button.setAttribute("disabled", "");
      // The status line replaces the confirmation at once; the 4 s refresh then follows the server's stage.
      confirming = false; applying = data.codeApply; started = new Date().toISOString();
      if (detail && detail.id === applying) { detail = { ...detail, applying: { stage: "starting", started_at: started } }; draw(); }
      void act(`${BASE}/${data.codeApply}/apply`, { confirmation: "apply", head: data.head, seen: Number(data.seen) },
        "Applied. Faculty restarts in a few seconds; reload the page after it does.");
    }
  });
  host.addEventListener("submit", (event) => {
    const form = /** @type {HTMLFormElement} */ (event.target);
    const id = form.dataset.codeFollow;
    if (!id) return;
    event.preventDefault();
    const text = String(new FormData(form).get("text") || "").trim();
    if (!text) return;
    void act(`${BASE}/${id}/follow-up`, { text }, "Asked. The agent is working on it.");
  });
  return {
    /** Show the tab, optionally opening one task. @param {string} [taskId] */
    show(taskId) { visible = true; if (taskId) { open = taskId; confirming = false; } void read(); },
    hide() { visible = false; generation++; clearTimeout(timer); },
  };
}
