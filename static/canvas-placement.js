// @ts-check
/**
 * "Put on Canvas" (canvas/README.md "Putting a Faculty assignment on Canvas"; canvas/placement.py): one of Faculty's assignments,
 * or a gradebook column, becomes an assignment of the term's Canvas copy in a module, then goes to every section's Canvas course
 * with the ordinary push (canvas-push.js). Opened from an assignment card, the Create assignment window's Canvas step, an
 * approved quiz draft and a gradebook column.
 *
 * The proposal (GET, Faculty's records only) fills the form; Save writes one revision of the copy (never Canvas); Push to Canvas…
 * opens the push window. When the copy is not started, or no section has its Canvas course, the form says where that is done.
 */
import { createCanvasPush } from "./canvas-push.js";
import { setBusy } from "./ui.js";

const SUBMISSIONS = [["on_paper", "On paper"], ["none", "No submission"], ["online_upload", "File upload"], ["online_text_entry", "Text entry"], ["online_url", "Website address"]];

/** The body a save posts, from the form's values and the proposal it was made from.
 * @param {any} proposal @param {Record<string, any>} values @param {{assignmentId?: string, itemId?: string}} link @param {string} requestId */
export function placementBody(proposal, values, link, requestId) {
  const module = values.module === "__new" ? { new_name: String(values.new_module || "").trim() } : values.module ? { id: values.module } : null;
  return {
    base_revision: proposal.revision, request_id: requestId, ...(link.assignmentId ? { assignment_id: link.assignmentId } : {}),
    ...(link.itemId ? { item_id: link.itemId } : {}), module,
    fields: { name: String(values.name || "").trim(), points: String(values.points ?? "").trim(),
      group: String((values.group === "__new" ? values.new_group : values.group) || "").trim(),
      due_date: values.due_date || "", due_time: values.due_date ? values.due_time || "23:59" : "", submission: values.submission || "on_paper",
      published: Boolean(values.published) },
  };
}

/** @param {{api: any, escape: (v: any) => string, toast: (m: string, o?: any) => void, showDialog: (html: string) => void,
 *  dialogHeader: (t: string, s?: string) => string}} deps */
export function createCanvasPlacement(deps) {
  const { api, escape, toast, showDialog, dialogHeader } = deps;
  const e = escape;
  const pushWindow = createCanvasPush(deps);

  function formMarkup(p, points) {
    const f = { ...p.fields, ...(points && !p.existing && !p.fields.points ? { points: String(points) } : {}) };
    const sameName = p.existing && !p.existing.linked;
    const note = p.existing
      ? `<p class="field-hint">${sameName ? `Faculty’s copy already has “${e(p.existing.name)}”: saving links it to this ${p.assignment ? "assignment" : "column"} and updates it.` : `Faculty’s copy already has this assignment as “${e(p.existing.name)}”: saving updates it.`}</p>`
      : "";
    const modules = p.modules.map((/** @type {any} */ m) => `<option value="${e(m.id)}" ${f.module === m.id ? "selected" : ""}>${e(m.name)}</option>`).join("");
    // The group is chosen, never left blank: Canvas puts an assignment made without one in the course's first group (2026-10-02).
    const groups = f.group && !p.groups.includes(f.group) ? [...p.groups, f.group] : p.groups;
    const groupOptions = groups.map((/** @type {string} */ g) => `<option value="${e(g)}" ${f.group === g ? "selected" : ""}>${e(g)}</option>`).join("");
    return `${note}
      <div class="cc-module-fields">
        <label class="field cc-grow">Name in Canvas<input name="name" maxlength="255" required value="${e(f.name)}"></label>
        <label class="field">Points<input name="points" inputmode="decimal" required value="${e(f.points)}"></label>
        <label class="field">Assignment Group<select name="group" required>${f.group ? "" : '<option value="" selected disabled>Choose…</option>'}${groupOptions}<option value="__new">New Assignment Group…</option></select></label>
        <label class="field" data-place-new-group hidden>New Assignment Group’s name<input name="new_group" maxlength="255"></label>
      </div>
      <div class="cc-module-fields">
        <label class="field">Due date <span class="field-optional">optional</span><input type="date" name="due_date" value="${e(f.due_date)}"></label>
        <label class="field">Due time<input type="time" name="due_time" value="${e(f.due_time || "23:59")}"></label>
        <label class="field">Handed in${f.external ? '<input type="text" value="Through its tool (set in Canvas)" disabled>' : `<select name="submission">${SUBMISSIONS.map(([k, v]) => `<option value="${k}" ${f.submission === k ? "selected" : ""}>${v}</option>`).join("")}</select>`}</label>
        <label class="checkbox-field"><input type="checkbox" name="published" ${f.published ? "checked" : ""}>Published <small class="field-hint">(students see it; grades can only be sent to a published assignment)</small></label>
      </div>
      <p class="field-hint">Times are College Station time.</p>
      <div class="cc-module-fields">
        <label class="field cc-grow">Module<select name="module">${f.module ? "" : '<option value="">Not in a module</option>'}${modules}<option value="__new">New module…</option></select></label>
        <label class="field cc-grow" data-place-new hidden>New module’s name<input name="new_module" maxlength="255"></label>
      </div>`;
  }

  function guideMarkup(p) {
    const home = p.group ? `#course/${encodeURIComponent(p.group)}/home/canvas` : "#canvas";
    return p.state === "no_courses"
      ? `<div class="callout" data-tone="info"><p>Link each section’s Canvas course first (on the section tab, the Canvas line of its work list).</p><a class="link-arrow" href="#canvas" data-place-leave>Open Canvas</a></div>`
      : `<div class="callout" data-tone="info"><p>Faculty keeps this term’s Canvas modules and assignments in one copy for every section. Start it once from the section whose Canvas course looks right, then come back here.</p><a class="link-arrow" href="${e(home)}" data-place-leave>Start the copy</a></div>`;
  }

  /**
   * Renders the placement into `host` (a dialog body, or the Create assignment window's Canvas step) with its own buttons.
   * @param {HTMLElement} host
   * @param {{termId: string, assignmentId?: string, itemId?: string, points?: string, onLeave?: () => void, onSaved?: (view: any) => void}} options
   */
  async function mount(host, options) {
    host.innerHTML = '<p class="loading-state">Reading Faculty’s copy of the Canvas content…</p>';
    const query = new URLSearchParams({ ...(options.assignmentId ? { assignment_id: options.assignmentId } : {}), ...(options.itemId ? { item_id: options.itemId } : {}) });
    let p;
    try {
      p = await api(`/api/canvas/content/${encodeURIComponent(options.termId)}/placement?${query}`);
    } catch (failure) {
      host.innerHTML = `<div class="callout" data-tone="danger"><p role="alert">${e(/** @type {any} */ (failure)?.message || "Faculty’s copy could not be read.")}</p></div>`;
      return;
    }
    if (p.state !== "ready") {
      host.innerHTML = guideMarkup(p);
      host.querySelectorAll("[data-place-leave]").forEach((link) => link.addEventListener("click", () => options.onLeave?.()));
      return;
    }
    const requestId = crypto.randomUUID();
    host.innerHTML = `<div class="cc-place" data-cc-place>${formMarkup(p, options.points)}
      <p class="form-error" role="alert" hidden></p>
      <div class="cc-actions"><span class="cc-spacer"></span><button type="button" class="button primary" data-place-save>Save to Faculty’s copy</button></div></div>`;
    const place = /** @type {HTMLElement} */ (host.querySelector("[data-cc-place]"));
    const field = (/** @type {string} */ name) => /** @type {HTMLInputElement|null} */ (place.querySelector(`[name="${name}"]`));
    const groupSelect = /** @type {HTMLSelectElement} */ (field("group"));
    groupSelect.onchange = () => {
      const wrap = /** @type {HTMLElement} */ (place.querySelector("[data-place-new-group]"));
      wrap.hidden = groupSelect.value !== "__new";
      if (!wrap.hidden) field("new_group")?.focus();
    };
    const moduleSelect = /** @type {HTMLSelectElement} */ (field("module"));
    moduleSelect.onchange = () => {
      const wrap = /** @type {HTMLElement} */ (place.querySelector("[data-place-new]"));
      wrap.hidden = moduleSelect.value !== "__new";
      if (!wrap.hidden) field("new_module")?.focus();
    };
    const error = /** @type {HTMLElement} */ (place.querySelector(".form-error"));
    const save = /** @type {HTMLButtonElement} */ (place.querySelector("[data-place-save]"));
    save.onclick = async () => {
      const values = Object.fromEntries(["name", "points", "group", "new_group", "due_date", "due_time", "submission", "module", "new_module"].map((n) => [n, field(n)?.value ?? ""]));
      values.published = Boolean(field("published")?.checked);
      if (!values.name.trim()) { error.hidden = false; error.textContent = "Give the assignment its name in Canvas."; return; }
      if (!values.group) { error.hidden = false; error.textContent = "Choose its Assignment Group: Canvas puts an assignment without one in the course’s first Assignment Group."; return; }
      if (values.group === "__new" && !String(values.new_group).trim()) { error.hidden = false; error.textContent = "Name the new Assignment Group."; return; }
      if (values.module === "__new" && !String(values.new_module).trim()) { error.hidden = false; error.textContent = "Name the new module."; return; }
      setBusy(save, true);
      save.textContent = "Saving…";
      error.hidden = true;
      try {
        const view = await api(`/api/canvas/content/${encodeURIComponent(options.termId)}/placement`, "POST",
          placementBody(p, values, { assignmentId: options.assignmentId, itemId: options.itemId }, requestId));
        options.onSaved?.(view);
        saved(host, view, values.name, options);
      } catch (failure) {
        error.hidden = false;
        error.textContent = /** @type {any} */ (failure)?.message || "It could not be saved.";
        setBusy(save, false);
        save.textContent = "Save to Faculty’s copy";
      }
    };
  }

  function saved(host, view, name, options) {
    const pushed = view.pushed_courses || [];
    host.innerHTML = `<div class="callout" data-tone="success"><p>“${e(name)}” is in Faculty’s copy (revision ${e(view.revision)}). Nothing has changed in Canvas yet.</p></div>
      <p class="field-hint">${pushed.length ? "Push to put it in each section’s Canvas course." : "The first push starts with one section ticked: check it in Canvas, then push to the rest."}</p>
      <div class="cc-actions"><span class="cc-spacer"></span><button type="button" class="button primary" data-place-push>Push to Canvas…</button></div>`;
    /** @type {HTMLElement} */ (host.querySelector("[data-place-push]")).onclick = () => {
      const base = `/api/canvas/content/${encodeURIComponent(options.termId)}`;
      void pushWindow.open({
        title: "Push to Canvas", subtitle: `${view.term.code} · ${view.term.term_label}`, courses: view.courses, pushed,
        previewPath: `${base}/preview`, pushPath: `${base}/push`, pairPath: `${base}/pair`,
        onStarted: () => toast("Pushing to Canvas. Canvas content shows its progress.", { action: { label: "Open",
          run: () => { location.hash = view.group ? `#course/${encodeURIComponent(view.group)}/home/canvas` : "#canvas"; } } }),
      });
    };
  }

  /** Opens "Put on Canvas" in the dialog. @param {{termId: string, assignmentId?: string, itemId?: string, points?: string, title?: string}} options */
  async function open(options) {
    showDialog(`<div class="cc-dialog" data-cc-place-dialog>${dialogHeader("Put on Canvas", e(options.title || ""))}
      <div class="dialog-body" data-place-host></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer></div>`);
    const host = /** @type {HTMLElement} */ (document.querySelector("[data-place-host]"));
    const close = () => /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
    await mount(host, { ...options, onLeave: close });
  }

  return { open, mount };
}
