/**
 * A course's rubric memories (GRADING "RUBRIC MEMORIES", 2026-10-14): `#course/<group>/home/rubric-memories` renders into
 * [data-rubric-memories-host].
 *
 * After an assignment is certified the Rubric drafting and review model reads its rubric questions, the instructor's answers and
 * where AI graders stopped, and writes general rules and watches, kept at once (2026-10-17); the rubric drafter and the rubric
 * check follow the kept ones. This page lists any older row still waiting (Keep, Drop, Keep all), what is kept (a quiet Drop takes one back), what was
 * dropped (Keep brings one back) and the recent learning passes, with Learn from certified assignments for those no pass has read.
 * The server decides what stands; the page only shows its view. While a pass waits or runs the page reads again every few seconds
 * and patches itself in place (dom-patch.js), and stops once none does.
 */
import { disclosureMarkup, emptyMarkup, loadingMarkup, plural, runFormAction, setBusy, whenVisible, dialogSession } from "./ui.js";
import { patchChildren } from "./dom-patch.js";

const POLL = 4000;
const RECENT_RUNS = 4; // passes shown under Learning; the rest fold
const KIND = /** @type {Record<string, string>} */ ({ rule: "Rule", watch: "Watch" });
const KIND_TONE = /** @type {Record<string, string>} */ ({ rule: "neutral", watch: "info" });
const RUN_STATUS = /** @type {Record<string, string>} */ ({ pending: "Waiting", running: "Reading", complete: "Done", failed: "Failed" });
const RUN_TONE = /** @type {Record<string, string>} */ ({ pending: "neutral", running: "info", complete: "success", failed: "danger" });

/** @param {{api: any, escape: (v: any) => string, toast: any, showDialog: (html: string) => void,
 *  dialogHeader: (t: string, s?: string) => string, header?: any}} deps */
export function createRubricMemories({ api, escape, toast, showDialog, dialogHeader, header = null }) {
  const e = escape;
  /** @type {any} */ let state = null; // {definitionId, code, view, signature}
  let generation = 0;
  let timer = 0;
  // Every write the page makes counts here: a poll that was asked before a write never draws over that write's answer.
  let writes = 0;
  let deciding = false;
  /** @type {{definitionId: string, promise: Promise<void>} | null} */
  let loading = null;
  const host = () => /** @type {HTMLElement|null} */ (document.querySelector("[data-rubric-memories-host]"));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.querySelector("#dialog"));
  const route = (/** @type {string} */ definitionId, path = "") => `/api/rubric-memories/${encodeURIComponent(definitionId)}${path}`;
  const day = (/** @type {string} */ iso) => {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  const courseHref = (/** @type {any} */ group) => `#course/${encodeURIComponent(group.id)}`;
  const message = (/** @type {any} */ cause) => cause?.message || "Something went wrong.";

  // -- What the page knows ----------------------------------------------------------------------------------------------------
  function accept(/** @type {any} */ view) {
    state.view = view;
    state.signature = JSON.stringify(view);
  }
  /** A pass waits for its turn or is reading an assignment. */
  function busy(view = state?.view) {
    return Boolean(view?.runs?.some((/** @type {any} */ run) => run.status === "pending" || run.status === "running"));
  }
  /** The certified assignments no pass has read, or whose last pass failed: what Learn reads. */
  function learnable(view = state?.view) {
    return (view?.certified || []).filter((/** @type {any} */ plan) => plan.learned === null || plan.learned === "failed");
  }

  // -- The header and the page --------------------------------------------------------------------------------------------------
  function setHeader(/** @type {any} */ group, /** @type {string} */ termLabel) {
    header?.set({
      route: location.hash,
      title: "Rubric memories",
      context: [group.code, "every term"].filter(Boolean).join(" · "),
      subtitle: "What the rubric drafter and the rubric check follow, learned from your answers and from where graders stopped.",
      crumbs: [
        { label: "Home", href: "#overview" },
        { label: "Courses", href: "#current-courses" },
        { label: [group.code, termLabel].filter(Boolean).join(" · "), href: courseHref(group) },
        { label: "Rubric memories" },
      ],
      action: null,
      secondary: [],
    });
  }

  /**
   * Mount for a course group (the course page calls this after drawing the host).
   * @param {any} group the course group ({id, definition_id, code}) @param {string} [termLabel] @param {boolean} [force]
   */
  async function render(group, termLabel = "", force = false) {
    const node = host();
    if (!node || !group) return;
    if (!group.definition_id) {
      header?.set({ route: location.hash, title: "Rubric memories", context: group.code || "", subtitle: "", action: null, secondary: [] });
      node.innerHTML = emptyMarkup({ icon: "book", title: "This course has no course code yet", body: "Rubric memories belong to a course code. Add one in the course settings first." });
      return;
    }
    const same = state?.definitionId === group.definition_id;
    if (!same) state = { definitionId: group.definition_id, code: group.code, view: null, signature: "" };
    setHeader(group, termLabel);
    if (same && state.view && !force && node.firstElementChild && node.dataset.memoriesFor === group.definition_id) { schedule(); return; }
    // The course page renders twice per navigation: an unforced render of the same course joins the read under way.
    const joined = loading;
    if (!force && joined && joined.definitionId === group.definition_id) return joined.promise;
    const run = ++generation;
    // The first visit shows the loading bars; a return draws what was read last at once, and the read below patches it.
    if (!state.view) node.innerHTML = loadingMarkup("Opening the rubric memories…", 4);
    else if (node.dataset.memoriesFor !== group.definition_id) {
      node.replaceChildren();
      node.dataset.memoriesFor = group.definition_id;
      draw();
    }
    const asked = writes;
    const promise = (async () => {
      try {
        const view = await api(route(group.definition_id));
        // A Keep or Drop answered meanwhile drew a newer view than this read.
        if (run !== generation || state?.definitionId !== group.definition_id || asked !== writes) return;
        accept(view);
        const current = host();
        if (!current) return;
        if (current.dataset.memoriesFor !== group.definition_id) current.replaceChildren();
        current.dataset.memoriesFor = group.definition_id;
        draw();
      } catch (cause) {
        if (run !== generation) return;
        const current = host();
        if (!current) return;
        // What was read last stays on screen; only a first visit shows that nothing could be read.
        if (state?.view && current.dataset.memoriesFor === group.definition_id) {
          toast("Couldn't refresh the rubric memories. Showing the last loaded ones.", { tone: "warning" });
          return;
        }
        delete current.dataset.memoriesFor;
        current.innerHTML = emptyMarkup({ icon: "info", title: "The rubric memories could not be opened", body: message(cause), action: { label: "Try again", data: { memoriesRetry: "" } } });
        const retry = /** @type {HTMLButtonElement|null} */ (current.querySelector("[data-memories-retry]"));
        if (retry) retry.onclick = () => { void render(group, termLabel, true); };
      }
    })();
    loading = { definitionId: group.definition_id, promise };
    try { await promise; } finally { if (loading?.promise === promise) loading = null; }
  }

  // -- Markup ---------------------------------------------------------------------------------------------------------------------
  /** "New", "Narrows M4", … what a proposal does. */
  function actionLabel(/** @type {any} */ memory) {
    return {
      new: "New",
      narrow: `Narrows ${memory.label}`,
      widen: `Widens ${memory.label}`,
      answer: `Answers watch ${memory.label}`,
      retire: `Retires ${memory.label}`,
    }[/** @type {string} */ (memory.action)] || "";
  }

  /** "From Quiz 4: 2 of your answers, 3 grader doubts" (zero counts left out). */
  function fromLine(/** @type {any} */ memory) {
    const counts = memory.evidence || {};
    const parts = [
      counts.answers ? `${counts.answers} of your answers` : "",
      counts.questions ? plural(counts.questions, "rubric check question") : "",
      counts.doubts ? plural(counts.doubts, "grader doubt") : "",
      counts.disagreements ? plural(counts.disagreements, "grader disagreement") : "",
    ].filter(Boolean).join(", ");
    if (memory.from) return parts ? `From ${memory.from}: ${parts}` : `From ${memory.from}`;
    return parts ? `From ${parts}` : "";
  }

  function lineMarkup(/** @type {any} */ memory, { action = false } = {}) {
    return `<div class="rubric-memory-line"><span class="rubric-memory-label">${e(memory.label)}</span><span class="chip" data-tone="${KIND_TONE[memory.kind] || "neutral"}">${e(KIND[memory.kind] || memory.kind)}</span>${action ? `<span class="rubric-memory-action">${e(actionLabel(memory))}</span>` : ""}</div>`;
  }

  /** Covers, Not when (and a revision's Why) as quiet lines. */
  function scopeMarkup(/** @type {any} */ memory, { why = false } = {}) {
    const rows = [["Covers", memory.covers], ["Not when", memory.not_when], ...(why ? [["Why", memory.why]] : [])].filter(([, text]) => String(text || "").trim());
    return rows.length ? `<dl class="rubric-memory-scope">${rows.map(([term, text]) => `<div><dt>${e(term)}</dt><dd>${e(text)}</dd></div>`).join("")}</dl>` : "";
  }

  function decideButton(/** @type {any} */ memory, /** @type {"keep"|"drop"} */ decision, className = "button", said = "") {
    const label = said || (decision === "keep" ? "Keep" : "Drop");
    return `<button type="button" class="${className}" data-memory-decide="${decision}" data-memory-id="${e(memory.id)}" aria-label="${e(`${label} ${memory.label}`)}">${label}</button>`;
  }

  function waitingItem(/** @type {any} */ memory) {
    const from = fromLine(memory);
    const was = memory.replaces && memory.action !== "retire"
      ? disclosureMarkup({ label: "Was", className: "rubric-memory-was", body: `<p class="rubric-memory-rule">${e(memory.replaces.rule)}</p>${scopeMarkup(memory.replaces)}` })
      : "";
    return `<li class="rubric-memory" data-patch-key="${e(memory.id)}" data-memory-row="${e(memory.id)}">
      ${lineMarkup(memory, { action: true })}
      <p class="rubric-memory-rule">${e(memory.rule)}</p>
      ${scopeMarkup(memory, { why: memory.action !== "new" })}
      ${from ? `<p class="rubric-memory-from">${e(from)}</p>` : ""}${was}
      <div class="rubric-memory-actions">${decideButton(memory, "keep")}${decideButton(memory, "drop")}</div>
    </li>`;
  }

  function keptItem(/** @type {any} */ memory) {
    const from = fromLine(memory);
    const earlier = memory.replaces ? `<p class="rubric-memory-from">${e(`Replaced the earlier ${memory.label}: ${memory.replaces.rule}`)}</p>` : "";
    const details = `${scopeMarkup(memory)}${from ? `<p class="rubric-memory-from">${e(from)}</p>` : ""}${earlier}`;
    return `<li class="rubric-memory" data-list="kept" data-patch-key="${e(memory.id)}" data-memory-row="${e(memory.id)}">
      ${lineMarkup(memory)}
      <p class="rubric-memory-rule">${e(memory.rule)}</p>
      <div class="rubric-memory-actions">${decideButton(memory, "drop", "text-button")}</div>
      ${details ? disclosureMarkup({ label: "Details", body: details }) : ""}
    </li>`;
  }

  function droppedItem(/** @type {any} */ memory) {
    return `<li class="rubric-memory" data-list="dropped" data-patch-key="${e(memory.id)}" data-memory-row="${e(memory.id)}">
      ${lineMarkup(memory, { action: memory.action !== "new" })}
      <p class="rubric-memory-rule">${e(memory.rule)}</p>
      <div class="rubric-memory-actions">${decideButton(memory, "keep", "text-button")}</div>
    </li>`;
  }

  /** A memory a learning pass retired (kept at once, like every revision): Bring back drops the retirement, and it stands again. */
  function retiredItem(/** @type {any} */ memory) {
    return `<li class="rubric-memory" data-list="retired" data-patch-key="${e(memory.id)}" data-memory-row="${e(memory.id)}">
      ${lineMarkup(memory)}
      <p class="rubric-memory-rule">${e(memory.rule)}</p>
      ${scopeMarkup({ why: memory.why }, { why: true })}
      <div class="rubric-memory-actions">${decideButton(memory, "drop", "text-button", "Bring back")}</div>
    </li>`;
  }

  function waitingMarkup(/** @type {any[]} */ waiting) {
    return `<section class="rubric-memories-section" data-patch-key="waiting" aria-labelledby="rubric-memories-waiting">
      <div class="rubric-memories-head"><h3 id="rubric-memories-waiting" tabindex="-1">Waiting for you <span class="chip" data-tone="warning">${waiting.length}</span></h3>${waiting.length > 1 ? `<button type="button" class="button" data-memory-keep-all>Keep all</button>` : ""}</div>
      <ul class="rubric-memory-list" data-memory-list="waiting">${waiting.map(waitingItem).join("")}</ul></section>`;
  }

  function keptMarkup(/** @type {any[]} */ standing) {
    return `<section class="rubric-memories-section" data-patch-key="kept" aria-labelledby="rubric-memories-kept">
      <div class="rubric-memories-head"><h3 id="rubric-memories-kept" tabindex="-1">Kept <span class="rubric-memories-count">${standing.length}</span></h3></div>
      <ul class="rubric-memory-list" data-memory-list="kept">${standing.map(keptItem).join("")}</ul></section>`;
  }

  function retiredMarkup(/** @type {any[]} */ retired) {
    return `<section class="rubric-memories-section" data-patch-key="retired" aria-label="Retired memories">${disclosureMarkup({
      label: "Retired by a learning pass", hint: retired.length, key: "rubric-memories:retired", className: "rubric-memories-retired",
      body: `<ul class="rubric-memory-list" data-memory-list="retired">${retired.map(retiredItem).join("")}</ul>`,
    })}</section>`;
  }

  function droppedMarkup(/** @type {any[]} */ dropped) {
    return `<section class="rubric-memories-section" data-patch-key="dropped" aria-label="Dropped memories">${disclosureMarkup({
      label: "Dropped", hint: dropped.length, key: "rubric-memories:dropped", className: "rubric-memories-dropped",
      body: `<ul class="rubric-memory-list" data-memory-list="dropped">${dropped.map(droppedItem).join("")}</ul>`,
    })}</section>`;
  }

  function runItem(/** @type {any} */ run) {
    const detail = run.status === "complete" ? (run.created ? `Proposed ${plural(run.created, "memory", "memories")}` : "Nothing new to propose")
      : run.status === "running" ? "Reading the assignment…" : run.status === "pending" ? "Waits its turn" : "";
    const facts = [detail, run.trigger === "certified" ? "after Certify" : run.after === "finalization" ? "after you approved its rubrics" : "you asked", day(run.updated_at)].filter(Boolean).join(" · ");
    return `<li class="rubric-memory-run" data-patch-key="${e(run.id)}"><strong>${e(run.assignment || "An assignment")}</strong><span class="chip" data-tone="${RUN_TONE[run.status] || "neutral"}">${e(RUN_STATUS[run.status] || run.status)}</span><small>${e(facts)}</small>${run.status === "failed" && run.error ? `<p class="rubric-memory-run-error">${e(run.error)}</p>` : ""}</li>`;
  }

  function learningMarkup(/** @type {any} */ view) {
    const plans = learnable(view);
    if (!view.runs.length && !plans.length) return "";
    const ready = Boolean(view.model);
    const button = plans.length ? `<button type="button" class="button" data-memory-learn ${ready ? "" : 'disabled aria-describedby="rubric-memories-learn-why"'}>Learn from certified assignments</button>` : "";
    const why = !plans.length ? ""
      : ready ? `<p class="rubric-memories-why">${e(`${plural(plans.length, "certified assignment")} not read yet.`)}</p>`
        : `<p class="rubric-memories-why" id="rubric-memories-learn-why">Choose a Rubric drafting and review model in <a href="#grading?settings=1">Compute settings</a> first: it writes the memories.</p>`;
    const recent = view.runs.slice(0, RECENT_RUNS), older = view.runs.slice(RECENT_RUNS);
    const runs = recent.length ? `<ul class="rubric-memory-runs">${recent.map(runItem).join("")}</ul>` : "";
    const folded = older.length ? disclosureMarkup({ label: "Earlier passes", hint: older.length, body: `<ul class="rubric-memory-runs">${older.map(runItem).join("")}</ul>` }) : "";
    return `<section class="rubric-memories-section" data-patch-key="learning" aria-labelledby="rubric-memories-learning">
      <div class="rubric-memories-head"><h3 id="rubric-memories-learning" tabindex="-1">Learning</h3>${button}</div>${why}${runs}${folded}</section>`;
  }

  function pageMarkup() {
    const view = state.view;
    const retired = view.retired || [];
    const nothing = !view.waiting.length && !view.standing.length && !retired.length && !view.dropped.length && !view.runs.length;
    return `<div class="rubric-memories-page">${[
      nothing ? `<div data-patch-key="empty">${emptyMarkup({ icon: "spark", title: "No rubric memories yet", body: "After you certify an assignment, Faculty learns memories here and keeps them. Drop any you disagree with." })}</div>` : "",
      view.waiting.length ? waitingMarkup(view.waiting) : "",
      view.standing.length ? keptMarkup(view.standing) : "",
      retired.length ? retiredMarkup(retired) : "",
      view.dropped.length ? droppedMarkup(view.dropped) : "",
      learningMarkup(view),
    ].join("")}</div>`;
  }

  function draw() {
    const node = host();
    if (!node || !state?.view) return;
    patchChildren(node, pageMarkup());
    node.onclick = click;
    schedule();
  }

  // -- Reading again while a pass works ---------------------------------------------------------------------------------------
  /** One look at a time: a draw or a render while a look is set leaves it (a hidden tab keeps it until it is shown again). */
  function schedule() {
    if (timer || !busy()) return;
    const definition = state.definitionId;
    timer = window.setTimeout(() => whenVisible(() => { timer = 0; void poll(definition); }), POLL);
  }

  async function poll(/** @type {string} */ definition) {
    const node = host();
    if (!node || node.dataset.memoriesFor !== definition || state?.definitionId !== definition) {
      // Another course's page is on screen now: it looks for itself.
      if (node && state && node.dataset.memoriesFor === state.definitionId) schedule();
      return;
    }
    const asked = writes;
    try {
      const view = await api(route(definition));
      if (state?.definitionId !== definition || host()?.dataset.memoriesFor !== definition || asked !== writes) return;
      if (JSON.stringify(view) !== state.signature) {
        const before = state.view;
        accept(view);
        draw();
        if (busy(before) && !busy(view)) finished(before, view);
        return;
      }
    } catch { /* the next look tries again */ }
    schedule();
  }

  /** Once no pass waits or runs: what the passes left, in one toast. */
  function finished(/** @type {any} */ before, /** @type {any} */ view) {
    const under = new Set(before.runs.filter((/** @type {any} */ run) => run.status === "pending" || run.status === "running").map((/** @type {any} */ run) => run.id));
    const failed = view.runs.some((/** @type {any} */ run) => under.has(run.id) && run.status === "failed");
    const fresh = view.runs.filter((/** @type {any} */ run) => under.has(run.id)).reduce((/** @type {number} */ sum, /** @type {any} */ run) => sum + (run.created || 0), 0);
    if (failed) toast("A learning pass failed. Its reason is under Learning.", { tone: "warning" });
    else if (fresh > 0) toast(`Kept ${plural(fresh, "new memory", "new memories")}. Drop any you disagree with.`);
    else toast("Learning finished: nothing new.");
  }

  // -- Keep, Drop and Learn -----------------------------------------------------------------------------------------------------
  function click(/** @type {MouseEvent} */ event) {
    const target = /** @type {HTMLElement} */ (event.target);
    const button = /** @type {HTMLButtonElement|null} */ (target.closest("[data-memory-decide],[data-memory-keep-all],[data-memory-learn]"));
    if (!button || button.disabled) return;
    if (button.hasAttribute("data-memory-learn")) { openLearn(); return; }
    if (button.hasAttribute("data-memory-keep-all")) {
      const waiting = state.view.waiting;
      void decide(waiting.map((/** @type {any} */ m) => ({ memory_id: m.id, decision: "keep" })), button, `Kept ${plural(waiting.length, "memory", "memories")}.`);
      return;
    }
    const id = button.dataset.memoryId || "", decision = /** @type {"keep"|"drop"} */ (button.dataset.memoryDecide);
    const memory = [...state.view.waiting, ...state.view.standing, ...(state.view.retired || []), ...state.view.dropped].find((m) => m.id === id);
    if (!memory) return;
    const kept = state.view.standing.some((/** @type {any} */ m) => m.id === id);
    const back = (state.view.retired || []).some((/** @type {any} */ m) => m.id === id);
    const said = back ? `${memory.label} stands again.`
      : decision === "keep" ? (memory.state === "dropped" ? `${memory.label} is kept again.` : `Kept ${memory.label}.`)
      : kept ? `Dropped ${memory.label}: the rubric drafter and the check no longer follow it.` : `Dropped ${memory.label}.`;
    void decide([{ memory_id: id, decision }], button, said);
  }

  /** The control to give focus to once a row left its list: the same control of the row now in its place. */
  function nextFocus(/** @type {HTMLElement} */ button) {
    const list = /** @type {HTMLElement|null} */ (button.closest("[data-memory-list]"));
    const row = button.closest("[data-memory-row]");
    if (!list || !row) return null;
    const index = [...list.querySelectorAll(":scope > [data-memory-row]")].indexOf(row);
    return { list: list.dataset.memoryList, index, decision: button.dataset.memoryDecide || "" };
  }

  async function decide(/** @type {any[]} */ decisions, /** @type {HTMLButtonElement} */ button, /** @type {string} */ said) {
    if (deciding || !decisions.length) return;
    deciding = true;
    const definition = state.definitionId;
    const place = button.hasAttribute("data-memory-decide") ? nextFocus(button) : null;
    writes += 1;
    setBusy(button, true);
    try {
      const view = await api(route(definition, "/decisions"), "POST", { decisions });
      if (state?.definitionId !== definition) return;
      accept(view);
      draw();
      toast(said);
      // The row left its list: focus goes to the same control of the row now in its place, else to the list's heading.
      if (!button.isConnected) {
        const node = host();
        const rows = place ? [...(node?.querySelectorAll(`[data-memory-list="${place.list}"] > [data-memory-row]`) || [])] : [];
        const row = place ? rows[Math.min(place.index, rows.length - 1)] : null;
        const next = /** @type {HTMLElement|null|undefined} */ (row?.querySelector(`[data-memory-decide="${place?.decision}"]`)
          || node?.querySelector("#rubric-memories-waiting, #rubric-memories-kept"));
        next?.focus();
      }
    } catch (cause) {
      toast(message(cause), true);
    } finally {
      deciding = false;
      if (button.isConnected) setBusy(button, false);
    }
  }

  function openLearn() {
    const view = state?.view;
    const plans = learnable(view);
    if (!view?.model || !plans.length) return;
    const rows = plans.map((/** @type {any} */ plan) => `<li><strong>${e(plan.title || "An assignment")}</strong><small>${e([plan.term, plan.certified_at ? `certified ${day(plan.certified_at)}` : "", plan.learned === "failed" ? "its last pass failed" : ""].filter(Boolean).join(" · "))}</small></li>`).join("");
    showDialog(`<form id="rubric-memories-learn" novalidate>${dialogHeader("Learn from certified assignments", e([state.code, "every term"].filter(Boolean).join(" · ")))}
      <div class="dialog-body"><p>One pass for each reads its rubric questions, your answers and where graders stopped, then keeps the memories it learns. You can drop any of them.</p>
        <ul class="rubric-memories-learn-list">${rows}</ul><p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Learn from ${e(plural(plans.length, "assignment"))}</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#rubric-memories-learn"));
    const definition = state.definitionId;
    const active = dialogSession(form, dialog());
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        if (!active() || state?.definitionId !== definition) return;
        writes += 1;
        const answer = await api(route(definition, "/learn"), "POST", { assignment_ids: plans.map((/** @type {any} */ plan) => plan.assignment_id) });
        if (state?.definitionId !== definition || !active()) return;
        const { queued, ...next } = answer;
        accept(next);
        dialog().close();
        draw();
        /** @type {HTMLElement|null|undefined} */ (host()?.querySelector("#rubric-memories-learning"))?.focus();
        toast(queued ? `Learning from ${plural(queued, "assignment")}. This page updates by itself.` : "Those assignments are already being read.");
      });
    };
  }

  /**
   * The course Home's Rubric memories line: how many wait for the instructor (the needs-you block), else how many are kept, else
   * nothing.
   * @param {any} group @param {HTMLElement|null} node
   */
  async function summary(group, node) {
    if (!group?.definition_id || !node) return;
    try {
      const view = await api(route(group.definition_id));
      if (!node.isConnected) return;
      node.innerHTML = view.waiting.length ? `<span class="chip" data-tone="warning">${e(`${view.waiting.length} waiting`)}</span>`
        : view.standing.length ? e(`${view.standing.length} kept`) : "";
    } catch {
      if (node.isConnected) node.textContent = "";
    }
  }

  return { render, summary };
}
