// @ts-check
/**
 * The push window (canvas/README.md "Modules and pages"): what a push would do in each Canvas course, the instructor's ticks
 * (Replace, Remove), pairings ("Same as…") and which courses it goes to, and Push, which sends the previewed plan's fingerprint.
 * Used by the term's Canvas content page, by "Put on Canvas" (canvas-placement.js) and by a section's own Canvas page.
 *
 * Nothing here decides: every change of a tick or of the courses asks the server for the preview again, and only that preview's
 * fingerprint is pushed. A first push to a term's courses starts with ONE course ticked (requirement, 2026-09-30), so it can be checked in Canvas before the rest.
 *
 * Conflict resolutions (canvas.md "Conflict review"): typed texts survive a preview reload, several rows can be resolved without
 * closing the window, and while one waits to go into the draft the preview is stale, so Push gives way to "Add N texts to the
 * draft" and "Save and preview again" (term) or "Preview again" (direct). Nothing reaches Canvas without the instructor's Push.
 */
import { openCanvasSignIn } from "./aim-signin.js";
import { conflictMarkup, conflictPrompt, conflictContext } from './canvas-conflicts.js';
import { dialogSession } from "./ui.js";

const LONG = 180000; // a preview reads every course of the term

export const TYPES = {
  SubHeader: "Text header", Page: "Page", ExternalUrl: "Web link", ExternalTool: "External tool",
  Assignment: "Assignment", Quiz: "Quiz", Discussion: "Discussion", File: "File",
};
const OPS = {
  create_group: ["Assignment Group", "Assignment Groups", "made"], create_assignment: ["assignment", "assignments", "made"],
  update_assignment: ["assignment", "assignments", "updated"],
  upload_file: ["file", "files", "put in Canvas’s Files"],
  create_page: ["page", "pages", "made"], update_page: ["page", "pages", "updated"], create_module: ["module", "modules", "made"],
  update_module: ["module", "modules", "updated"], create_item: ["item", "items", "added"], update_item: ["item", "items", "updated"],
  move_item: ["item", "items", "moved to another module"], replace_item: ["item", "items", "replaced"],
  delete_item: ["item", "items", "removed"], delete_module: ["module", "modules", "deleted"], delete_page: ["page", "pages", "deleted"],
  delete_assignment: ["assignment", "assignments", "deleted"],
  order_modules: ["module order", "module orders", "changed"], order_items: ["item order", "item orders", "changed"],
};
// A file Faculty keeps (canvas FILES) beside the one Canvas holds under its folder and name.
const FILE_VERDICTS = {
  differs: "Canvas already has a different file of this name", edited_in_canvas: "Replaced in Canvas since Faculty put its file there",
};
const VERDICTS = {
  differs: "Differs from Faculty’s copy (never pushed here)", edited_in_canvas: "Edited in Canvas since Faculty’s last push",
  conflict: "Edited in Canvas and in Faculty", elsewhere: "Found by its title in another module: moved here only if you tick Replace",
};

export const plural = (/** @type {number} */ n, /** @type {string} */ one, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
export const sectionsText = (/** @type {any} */ course) => `Section${course.sections.length === 1 ? "" : "s"} ${course.sections.map((/** @type {any} */ s) => s.number).join(", ")}`;

/** The key of one typed resolution: difference `index` of row `key` under one course's "Needs your decision".
 * @param {string} course @param {string} key @param {number|string} index */
export const resolutionKey = (course, key, index) => `${course}|${key}|${index}`;

/** A course's choices with the rows resolved in the draft left as they are in Canvas: no Replace for them ("Replace all" becomes
 * every other row). @param {any} choice @param {{key: string}[]} attention @param {Set<string>} resolved */
export function leaveResolved(choice, attention, resolved) {
  if (!resolved.size) return choice || {};
  const replace = choice?.replace === "all" ? attention.map((x) => x.key) : choice?.replace || [];
  return { ...(choice || {}), replace: replace.filter((/** @type {string} */ key) => !resolved.has(key)) };
}

/** The window's footer. While a resolution waits to go into the draft the preview is stale, so Push gives way to adding the texts
 * and previewing again: a stale preview is never pushed. @param {{total: number, pending: number, direct?: boolean}} state */
export function footerMarkup({ total, pending, direct }) {
  const cancel = '<button type="button" class="button" data-action="close">Cancel</button>';
  if (pending) {
    return `${cancel}<button type="button" class="button" data-cc-resolve-add>Add ${plural(pending, "text")} to the draft</button>`
      + `<button type="button" class="button primary" data-cc-resolve-preview>${direct ? "Preview again" : "Save and preview again"}</button>`;
  }
  return `${cancel}<button type="button" class="button primary" data-cc-push ${total ? "" : "disabled"}>${total ? `Push ${plural(total, "change")}` : "Nothing to push"}</button>`;
}

/** "12 items added · 1 module made", or "Nothing to change". @param {Record<string, number>} counts */
export function opsSummary(counts) {
  const parts = Object.keys(OPS).filter((op) => counts?.[op]).map((op) => {
    const [one, many, verb] = OPS[op];
    return `${plural(counts[op], one, many)} ${verb}`;
  });
  return parts.length ? parts.join(" · ") : "Nothing to change";
}

/** The courses a push starts with: every course, or on a term's first push only the first (checked in Canvas before the rest).
 * @param {{course_id: string}[]} courses @param {string[]} pushed the courses a push of this copy has written to */
export function initialCourses(courses, pushed) {
  if (courses.length > 1 && !pushed.length) return [courses[0].course_id];
  return courses.map((c) => c.course_id);
}

/** Runs a Canvas request; when Canvas wants the instructor to sign in, opens the sign-in and tries once more (null if they did not). */
export async function withCanvasSignIn(run, { api, showDialog, dialogHeader, escape, current = () => true }) {
  try {
    return await run();
  } catch (failure) {
    if (/** @type {any} */ (failure)?.status !== 401) throw failure;
    if (!current()) return null;
    const who = await openCanvasSignIn({ api, showDialog, dialogHeader, escape });
    if (!who || !current()) return null;
    return run();
  }
}

/** @param {{api: any, escape: (v: any) => string, toast: (m: string, o?: any) => void, showDialog: (html: string) => void,
 *  dialogHeader: (t: string, s?: string) => string}} deps */
export function createCanvasPush(deps) {
  const { api, escape, toast, showDialog, dialogHeader, ask } = deps;
  const e = escape;
  /** @type {any} */ let push = null; // {options, choices, courses (null: every course), preview, resolutions, chosen, drawn}
  let previewGeneration = 0;
  // resolutions: resolutionKey → the text typed under Compare changes; chosen: resolutionKey → {course, key, field, value}, a text
  // the instructor chose for the draft that has not been previewed yet.

  /**
   * Opens the push window.
   * @param {{title: string, subtitle: string, courses: any[], pushed?: string[], previewPath: string, pushPath: string, pairPath?: string,
   *  extra?: Record<string, any>, direct?: boolean, onStarted: (push: any) => void,
   *  onResolve?: (key: string, field: string, value: string) => void, save?: () => Promise<boolean>}} options
   *  `courses` [{course_id, name, sections}], `pushed` the courses already pushed to, `extra` fields sent with the preview and the
   *  push, `direct` one section's own Canvas course edited directly (no copy to pair with; "Needs your decision" is what changed
   *  in Canvas since the page was opened). `onResolve` puts a chosen text in the draft (never saves or pushes); `save` saves the
   *  term's draft before "Save and preview again" (true once saved).
   */
  async function open(options) {
    const chosen = initialCourses(options.courses, options.pushed || []);
    push = { options, choices: {}, courses: chosen.length === options.courses.length ? null : chosen, preview: null, resolutions: {}, chosen: {}, drawn: null, reading: false };
    show(options);
    await refresh();
  }

  function show(options) {
    showDialog(`<form class="cc-dialog" data-cc-dialog novalidate>${dialogHeader(options.title, e(options.subtitle))}
      <div class="dialog-body" data-cc-dialog-body><p class="loading-state">Reading ${e(plural(options.courses.length, "Canvas course"))}…</p></div>
      <footer class="dialog-footer" data-cc-dialog-footer><button type="button" class="button" data-action="close">Cancel</button></footer></form>`);
  }

  function body(state) {
    return { choices: state.choices, ...(state.courses ? { courses: state.courses } : {}), ...(state.options.extra || {}) };
  }

  async function refresh() {
    let node = document.querySelector("[data-cc-dialog-body]");
    if (!node || !push) return;
    const state = push, generation = ++previewGeneration;
    const latest = () => push === state && generation === previewGeneration;
    let current = dialogSession(/** @type {HTMLElement} */ (node), /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
    const payload = structuredClone(body(state));
    state.reading = true;
    node.setAttribute("aria-busy", "true");
    const submit = /** @type {HTMLButtonElement|null} */ (document.querySelector("[data-cc-push]"));
    if (submit) submit.disabled = true;
    try {
      let preview;
      try { preview = await api(state.options.previewPath, "POST", payload, {}, LONG); }
      catch (failure) {
        if (!latest() || !current()) return;
        if (/** @type {any} */ (failure)?.status !== 401) throw failure;
        const who = await openCanvasSignIn(deps);
        if (!who || !latest()) return;
        // Signing in replaced this dialog. Restore its existing choices, then retry the read once.
        show(state.options);
        node = document.querySelector("[data-cc-dialog-body]");
        current = dialogSession(/** @type {HTMLElement} */ (node), /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
        preview = await api(state.options.previewPath, "POST", payload, {}, LONG);
      }
      if (!preview) return;
      if (!latest() || !current()) return;
      state.reading = false;
      push.preview = preview;
      push.choices = preview.choices;
      // A chosen text stays chosen while its row and field are still in the preview; typed texts are kept either way.
      for (const [id, r] of Object.entries(push.chosen)) {
        const entry = preview.courses.find((/** @type {any} */ c) => c.course_id === r.course)?.attention.find((/** @type {any} */ x) => x.key === r.key);
        if (entry?.differences?.[Number(id.split("|").pop())]?.field !== r.field) delete push.chosen[id];
      }
      draw();
    } catch (failure) {
      if (!latest() || !current()) return;
      const again = document.querySelector("[data-cc-dialog-body]");
      if (again) again.innerHTML = `<div class="callout" data-tone="danger"><p role="alert">${e(/** @type {any} */ (failure)?.message || "Canvas could not be read.")}</p></div><button type="button" class="button" data-cc-again>Try again</button>`;
      document.querySelector("[data-cc-again]")?.addEventListener("click", () => { void refresh(); });
    } finally {
      if (latest()) state.reading = false;
      if (latest() && current()) node?.removeAttribute("aria-busy");
    }
  }

  function coursesMarkup() {
    const { courses, pushed = [] } = push.options;
    if (courses.length < 2) return "";
    const ticked = new Set(push.courses || courses.map((/** @type {any} */ c) => c.course_id));
    const first = !pushed.length;
    return `<fieldset class="cc-targets"><legend>Push to</legend>
      ${first ? '<p class="field-hint">Faculty has not pushed to these courses yet. Push to one section first, check it in Canvas, then push to the rest.</p>' : ""}
      ${courses.map((/** @type {any} */ c) => `<label class="checkbox-field"><input type="checkbox" data-cc-target value="${e(c.course_id)}" ${ticked.has(c.course_id) ? "checked" : ""}>${e(sectionsText(c))} <small>${e(c.name)}${pushed.includes(c.course_id) ? " · has Faculty’s copy" : ""}</small></label>`).join("")}</fieldset>`;
  }

  function draw() {
    const node = document.querySelector("[data-cc-dialog-body]"), footer = document.querySelector("[data-cc-dialog-footer]");
    if (!node || !footer) return;
    const { preview, options } = push;
    const total = preview.courses.reduce((/** @type {number} */ n, /** @type {any} */ c) => n + c.ops.length, 0);
    /** What the instructor typed and chose under one row's Compare changes. @param {string} courseId @param {any} x */
    const rowState = (courseId, x) => {
      /** @type {Record<number, string>} */ const typed = {}; /** @type {number[]} */ const chosen = [];
      (x.differences || []).forEach((/** @type {any} */ _, /** @type {number} */ i) => {
        const id = resolutionKey(courseId, x.key, i);
        if (id in push.resolutions) typed[i] = push.resolutions[id];
        if (push.chosen[id]) chosen.push(i);
      });
      return { typed, chosen };
    };
    const course = (/** @type {any} */ c) => {
      const chosen = push.choices[c.course_id] || {};
      const replaceAll = chosen.replace === "all";
      const replaced = new Set(replaceAll ? [] : chosen.replace || []);
      const removing = new Set(c.remove);
      const resolved = resolvedIn(c.course_id);
      const attention = c.attention.length ? `<h4>Needs your decision <span class="count-badge">${c.attention.length}</span></h4>
        <p class="field-hint">Left as they are in Canvas unless you tick Replace.</p>
        <label class="checkbox-field"><input type="checkbox" data-cc-replace-all="${e(c.course_id)}" ${replaceAll ? "checked" : ""}>Replace all of them with ${options.direct ? "your changes" : "Faculty’s copy"}</label>
        <ul class="cc-list">${c.attention.map((/** @type {any} */ x) => {
          const done = resolved.has(x.key);
          return `<li data-conflict-key="${e(x.key)}" data-conflict-course="${e(c.course_id)}"><label class="checkbox-field"><input type="checkbox" data-cc-replace="${e(c.course_id)}" value="${e(x.key)}" ${!done && (replaceAll || replaced.has(x.key)) ? "checked" : ""} ${replaceAll || done ? "disabled" : ""}><span><strong>${e(x.label)}</strong> <small>${e(x.kind === "file" ? FILE_VERDICTS[x.verdict] || x.verdict : options.direct ? "Changed in Canvas since you opened this page" : VERDICTS[x.verdict] || x.verdict)}</small>${done ? ' <span class="chip" data-tone="success" data-conflict-resolved>Resolved in your draft · left as is in Canvas for this push</span>' : ""}</span></label>
        ${conflictMarkup(x, e, Boolean(options.onResolve), options.direct, rowState(c.course_id, x))}${ask && x.differences?.length && x.kind !== "file" ? '<button type="button" class="button" data-conflict-ask>Ask assistant to prepare a resolution</button>' : ''}</li>`;
        }).join("")}</ul>` : "";
      const fresh = c.new.length ? `<h4>New in this course <span class="count-badge">${c.new.length}</span></h4>
        ${options.direct ? "" : "<p class=\"field-hint\">Made by the push. If Canvas already has it under another name, choose it under “Same as”.</p>"}
        <ul class="cc-list">${c.new.map((/** @type {any} */ x) => {
          const [kind, key] = x.key.split(":");
          const candidates = options.pairPath && !options.direct ? (c.unclaimed[kind] || []) : [];
          return `<li class="cc-new"><span>${e(x.label)}</span>${candidates.length ? `<select data-cc-pair="${e(c.course_id)}" data-kind="${e(kind)}" data-key="${e(key)}" aria-label="Same as in Canvas"><option value="">Same as…</option>${candidates.map((/** @type {any} */ o) => `<option value="${e(o.canvas_id)}">${e(o.label)}${o.type ? ` (${e(TYPES[o.type] || o.type)})` : ""}</option>`).join("")}</select>` : ""}</li>`;
        }).join("")}</ul>` : "";
      const blocked = c.blocked.length ? `<h4>Not pushed here <span class="count-badge">${c.blocked.length}</span></h4><ul class="cc-list">${c.blocked.map((/** @type {any} */ x) => `<li><strong>${e(x.label)}</strong>: ${e(x.reasons.join("; "))}</li>`).join("")}</ul>` : "";
      const extra = c.extra.length ? `<details class="disclosure"${c.extra.some((/** @type {any} */ x) => x.remove) ? " open" : ""}><summary><span>${options.direct ? "Removed from this course" : "Only in Canvas"}</span><span class="disclosure-hint">${c.extra.length}${removing.size ? ` · ${removing.size} to remove` : ""}</span></summary><div class="disclosure-body">
        <p class="field-hint">Left in Canvas unless ticked. Removing a module item keeps what it points to; deleting a module keeps its items’ assignments and pages. Deleting an assignment deletes its grades in Canvas.</p>
        <ul class="cc-list">${c.extra.map((/** @type {any} */ x) => `<li><label class="checkbox-field"><input type="checkbox" data-cc-remove="${e(c.course_id)}" value="${e(x.key)}" ${removing.has(x.key) ? "checked" : ""}><span>${x.kind === "page" ? "Delete page" : x.kind === "module" ? "Delete module" : x.kind === "assignment" ? "Delete assignment" : "Remove item"}: <strong>${e(x.label)}</strong>${x.kind === "assignment" ? " <small>(deletes its submissions and grades in Canvas)</small>" : ""}${x.was_faculty && !options.direct ? " <small>(removed from Faculty’s copy)</small>" : ""}${options.direct && !x.was_faculty ? " <small>(not in the editor: kept unless ticked)</small>" : ""}</span></label></li>`).join("")}</ul></div></details>` : "";
      const changes = c.ops.length ? `<details class="disclosure"><summary><span>Every change</span><span class="disclosure-hint">${c.ops.length}</span></summary><div class="disclosure-body"><ul class="cc-list">${c.ops.map((/** @type {any} */ op) => `<li><strong>${e(op.label || (OPS[op.op] || [op.op])[0])}</strong>: ${e((OPS[op.op] || [op.op, "", ""])[2])}${op.fields ? ` <small>(${e(Object.keys(op.fields).map((f) => f.replace(/_/g, " ")).join(", "))})</small>` : ""}</li>`).join("")}</ul></div></details>` : "";
      return `<section class="cc-course"><h3>${e(sectionsText(c))} <small>${e(c.name)}</small></h3><p class="cc-summary">${e(opsSummary(c.counts))}</p>${attention}${fresh}${blocked}${extra}${changes}</section>`;
    };
    const unlinked = preview.unlinked?.length ? `<div class="callout" data-tone="info"><p>${e(plural(preview.unlinked.length, "section"))} without a Canvas course (${e(preview.unlinked.map((/** @type {any} */ s) => s.number).join(", "))}) ${preview.unlinked.length === 1 ? "is" : "are"} left out. Link ${preview.unlinked.length === 1 ? "it" : "them"} on the section tab.</p></div>` : "";
    // Redrawing the same preview (a text chosen or set aside) keeps the disclosures the instructor opened.
    const opened = push.drawn === preview ? [...node.querySelectorAll("details")].map((d) => /** @type {HTMLDetailsElement} */ (d).open) : [];
    node.innerHTML = `${coursesMarkup()}${unlinked}${preview.courses.map(course).join("")}`;
    node.querySelectorAll("details").forEach((d, i) => { if (opened[i]) /** @type {HTMLDetailsElement} */ (d).open = true; });
    push.drawn = preview;
    /** @type {HTMLElement} */ (node).oninput = (event) => {
      const t = /** @type {HTMLTextAreaElement} */ (event.target), row = /** @type {HTMLElement|null} */ (t.closest?.("[data-conflict-key]"));
      if (!row || !("conflictValue" in t.dataset)) return;
      const id = resolutionKey(row.dataset.conflictCourse || "", row.dataset.conflictKey || "", t.dataset.conflictValue || "");
      push.resolutions[id] = t.value;
      if (push.chosen[id]) push.chosen[id].value = t.value;
    };
    /** @type {HTMLElement} */ (node).onclick = async (event) => {
      const button = /** @type {HTMLButtonElement|null} */ (/** @type {HTMLElement} */ (event.target).closest?.("button"));
      const row = /** @type {HTMLElement|null} */ (button?.closest("[data-conflict-key]") || null);
      if (!button || !row) return;
      const courseId = row.dataset.conflictCourse || "";
      const entry = preview.courses.find((/** @type {any} */ c) => c.course_id === courseId)?.attention.find((/** @type {any} */ x) => x.key === row.dataset.conflictKey);
      if (!entry) return;
      if ("conflictAsk" in button.dataset) {
        button.disabled = true;
        try { await ask(conflictPrompt(entry), {reviewOnly: true, context: conflictContext(options.previewPath, entry), parent: document.querySelector('#dialog')}); }
        catch (error) { toast(/** @type {any} */ (error)?.message || 'Could not open the Assistant.', { tone: "danger" }); }
        finally { button.disabled = false; }
      } else if ("conflictKeep" in button.dataset) {
        const index = Number(button.dataset.conflictKeep), diff = entry.differences[index];
        if (push.preview !== preview || diff.truncated || !options.onResolve) return;
        const id = resolutionKey(courseId, entry.key, index);
        const value = /** @type {HTMLTextAreaElement} */ (row.querySelector(`[data-conflict-value="${index}"]`)).value;
        push.resolutions[id] = value;
        push.chosen[id] = { course: courseId, key: entry.key, field: diff.field, value };
        const c = preview.courses.find((/** @type {any} */ x) => x.course_id === courseId);
        push.choices[courseId] = leaveResolved(push.choices[courseId], c.attention, resolvedIn(courseId));
        draw();
      } else if ("conflictUndo" in button.dataset) {
        delete push.chosen[resolutionKey(courseId, entry.key, button.dataset.conflictUndo || "")];
        draw();
      }
    };
    footer.innerHTML = footerMarkup({ total, pending: Object.keys(push.chosen).length, direct: options.direct });
    const submit = /** @type {HTMLButtonElement|null} */ (footer.querySelector("[data-cc-push]"));
    if (submit && push.reading) submit.disabled = true;
    /** @type {HTMLElement} */ (node).onchange = (event) => {
      const t = /** @type {HTMLInputElement} */ (event.target), d = t.dataset;
      const values = (/** @type {string} */ selector) => [...node.querySelectorAll(selector)].map((b) => /** @type {HTMLInputElement} */ (b).value);
      if ("ccTarget" in d) {
        const ticked = values("[data-cc-target]:checked");
        if (!ticked.length) { t.checked = true; toast("Push to at least one course."); return; }
        push.courses = ticked.length === push.options.courses.length ? null : ticked;
        void refresh();
      } else if (d.ccReplaceAll) {
        const choice = { ...push.choices[d.ccReplaceAll], replace: t.checked ? "all" : [] };
        const attention = push.preview.courses.find((/** @type {any} */ c) => c.course_id === d.ccReplaceAll)?.attention || [];
        push.choices[d.ccReplaceAll] = leaveResolved(choice, attention, resolvedIn(d.ccReplaceAll)); // a resolved row stays as is in Canvas
        void refresh();
      }
      else if (d.ccReplace) { push.choices[d.ccReplace] = { ...push.choices[d.ccReplace], replace: values(`[data-cc-replace="${CSS.escape(d.ccReplace)}"]:checked`) }; void refresh(); }
      else if (d.ccRemove) { push.choices[d.ccRemove] = { ...push.choices[d.ccRemove], remove: values(`[data-cc-remove="${CSS.escape(d.ccRemove)}"]:checked`) }; void refresh(); }
      else if (d.ccPair && t.value) { void pair(d.ccPair, d.kind, d.key, t.value); }
    };
    footer.querySelector("[data-cc-push]")?.addEventListener("click", () => { void run(); });
    footer.querySelector("[data-cc-resolve-add]")?.addEventListener("click", () => {
      const count = Object.keys(push.chosen).length;
      if (!addChosen()) return;
      push.chosen = {};
      toast(`${plural(count, "text")} added to your draft. ${options.direct ? "Review and push when ready." : "Save, then review the push again."}`);
      /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
    });
    footer.querySelector("[data-cc-resolve-preview]")?.addEventListener("click", () => { void previewAgain(); });
  }

  /** The entry keys of one course's rows with a text chosen for the draft. @param {string} courseId */
  function resolvedIn(courseId) {
    return new Set(Object.values(push.chosen).filter((/** @type {any} */ r) => r.course === courseId).map((/** @type {any} */ r) => r.key));
  }

  /** Puts every chosen text in the draft through options.onResolve (the same field set again is harmless); false if one could not. */
  function addChosen() {
    const state = push;
    for (const r of Object.values(state.chosen)) {
      try { state.options.onResolve(r.key, r.field, r.value); }
      catch (failure) { toast(/** @type {any} */ (failure)?.message || "The text could not be added to the draft.", { tone: "danger" }); return false; }
    }
    return true;
  }

  /** "Save and preview again" (term) / "Preview again" (direct): the chosen texts go into the draft, the term's draft is saved, and
   * the preview is read again, so what Push sends includes them. Nothing is written to Canvas here. */
  async function previewAgain() {
    const state = push;
    const current = dialogSession(/** @type {HTMLElement} */ (document.querySelector("[data-cc-dialog]")), /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
    if (!addChosen()) return;
    /** The footer's resolution buttons held while the draft is saved and Canvas read (Cancel stays). @param {string} label */
    const hold = (label) => document.querySelectorAll("[data-cc-resolve-add], [data-cc-resolve-preview]").forEach((button) => {
      /** @type {HTMLButtonElement} */ (button).disabled = true;
      if (button.matches("[data-cc-resolve-preview]")) button.textContent = label;
    });
    if (!state.options.direct) {
      if (!state.options.save) { toast("Save your changes, then review the push again."); return; }
      hold("Saving your draft…");
      const saved = await state.options.save();
      if (push !== state || !current()) return;
      if (!saved) { draw(); return; }
    }
    if (push !== state || !current()) return;
    for (const id of Object.keys(state.chosen)) delete state.resolutions[id];
    state.chosen = {};
    hold("Reading Canvas again…");
    await refresh();
  }

  async function pair(course, kind, key, canvasId) {
    const state = push;
    const current = dialogSession(/** @type {HTMLElement} */ (document.querySelector("[data-cc-dialog]")), /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
    try {
      await api(state.options.pairPath, "POST", { course_id: course, kind, plan_key: key, canvas_id: canvasId });
      if (push === state && current()) await refresh();
    } catch (failure) {
      toast(/** @type {any} */ (failure)?.message || "The pairing could not be saved.", { tone: "danger" });
    }
  }

  async function run() {
    const state = push;
    if (!state?.preview || state.reading) return;
    const total = state.preview.courses.reduce((/** @type {number} */ n, /** @type {any} */ c) => n + c.ops.length, 0);
    if (!confirm(`Push ${plural(total, "change")} to ${plural(state.preview.courses.length, "Canvas course")}? Students see published changes at once.`)) return;
    const footer = document.querySelector("[data-cc-dialog-footer]");
    const current = dialogSession(/** @type {HTMLElement} */ (footer), /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
    const payload = structuredClone({ ...body(state), fingerprint: state.preview.fingerprint, request_id: crypto.randomUUID(), confirmation: "push" });
    if (footer) footer.innerHTML = '<p class="loading-state">Checking Canvas once more…</p>';
    try {
      const started = await withCanvasSignIn(async () => {
        if (push !== state) return null;
        try { return await api(state.options.pushPath, "POST", payload, {}, LONG); }
        catch (failure) { if (/** @type {any} */ (failure)?.status === 401 && !current()) return null; throw failure; }
      }, deps);
      if (!started) return;
      if (current()) /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
      state.options.onStarted(started);
    } catch (failure) {
      toast(/** @type {any} */ (failure)?.message || "The push could not start.", { tone: "danger" });
      if (push === state && current()) {
        if (/** @type {any} */ (failure)?.status === 409) await refresh(); else draw();
      }
    }
  }

  return { open };
}
