// Gradebook dialogs over the grid (#gradebook/<term>): a column's settings, linking an assignment, adding every graded
// assignment as columns, pulling final grades from grading, importing a scores file, Sync with Canvas and Import from
// iClicker, the grading scheme, a column's posting choice, the paste and fill confirmations, the student record, one cell's
// history and the nightly import's sign-in and Leave out. Each preview writes nothing; every write names what it was
// previewed or seen on (a fingerprint, a setup revision, a cell's revision) and carries a request ID that is reused only
// while the command is unchanged, so a lost response can be checked by sending the same request again. The only outward
// writes are the explicit Sync with Canvas button's (grades sent, assignments created and published, grades posted or hidden
// in Canvas; AGENTS.md "OUTSIDE SERVICES", docs/decisions/canvas.md); a posting choice only records what the next sync does.
// Nothing here emails or changes grading.
import { dialogSession, markBusy, plural, runFormAction, setBusy, tableMarkup } from "./ui.js";
import { give } from "./handoff.js";
import { openCanvasSignIn } from "./aim-signin.js";
import { attachPrefill, followRecord, openRows } from "./schedule-prefill.js";
import { createCanvasPlacement } from "./canvas-placement.js";
import { saveTaker, takerMarkup } from "./dr-quiz-taker.js";
import { actorText, blankReason, canvasGraded, columnBands, droppedIn, parseEntry, postingRule, replacedBy, shortDate, trimPoints } from "./gradebook.js";
import { PARTS, PART_ORDER, PRESETS, blankItem, draftSetup, linkAssignment, linkControls, previewText, problems, sectionIds, setupPayload,
  togglePart, unsavedSetup, when } from "./gradebook-setup.js";
import { STANDARD_SCALE, scaleProblems, scaleUppers } from "./grading-scheme.js";

const LONG = 180000; // Pulls and imports read grading or a whole file: up to a minute on a big exam.
const MAX_FILE = 10 * 1024 * 1024;
const FORMATS = {
  gradescope_course: "Gradescope course grades export",
  gradescope_assignment: "Gradescope assignment scores export",
  generic: "Spreadsheet with a UIN column",
};
const ACTION = {
  add: ["Add", "Empty cells that get a value"],
  update: ["Update", "Earlier imported or pulled values that change"],
  same: ["Same", "Cells that already hold this value"],
  kept: ["Kept", "Values you typed or cleared in Faculty: tick Replace to overwrite them"],
  differs: ["Differs", "Canvas differs from a column Faculty grades itself: compared, never written"],
  faculty_only: ["Only in Faculty", "Faculty has a grade here and Canvas has none: nothing is written"],
  clear: ["Clear", "Canvas has no grade here, so WebAssign’s column is emptied too"],
  waiting: ["Waiting", "Grading is not final for them yet"],
  none: ["Nothing", "Nothing in grading for them"],
};
const SOURCE = { instructor: "Typed in Faculty", import: "Imported", grading: "Pulled from grading" };
// POSTING GRADES: why Sync posts, hides or switches a column (the summary's `posting_plan`), and what it does in a few words.
const POSTING_WHY = { choice: "your choice", due: "its due date has passed", certified: "certified", iclicker: "iClicker", manual: "its category posts by hand" };
function postingDoes(entry) {
  const where = (n) => (n > 1 ? ` in ${n} courses` : "");
  const does = entry.action === "post" ? `Posts ${plural(entry.grades, "hidden grade")}${where(entry.courses)}`
    : entry.action === "hide" ? `Hides ${plural(entry.grades, "shown grade")}${where(entry.courses)}`
    : entry.new ? "Posts by hand once created" : `Sets it to post by hand${where(entry.courses)}`;
  return entry.courses ? (entry.waiting ? `${does}; waits in ${plural(entry.waiting, "course")}` : does) : `Waits${where(entry.waiting)}`;
}
const STATUS = { scored: "Score", excused: "Excused", missing: "Missing", cleared: "Clear" };

const dialogNode = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
const close = () => dialogNode()?.close();
const url = (ctx, path) => `/api/gradebook/terms/${encodeURIComponent(ctx.termId)}${path}`;
// A gradebook opened in a section's tab (requirement, 2026-09-28) pulls and imports only that section's students.
const scope = ctx => ctx.scopeSection ? { section_id: ctx.scopeSection } : {};
const partsText = parts => (parts || []).map(part => PARTS[part] || part).join(", ");

/** The "When you press Import" lines of an iClicker import about excused absences (the plan's `excused`, ABSENCES ICLICKER-CREDIT):
 * which students' sessions Import gives full credit, naming at most `limit` students, and the kept values that are an absence's
 * credit already (so they are not counted as typed in Faculty). `kept` is the plan's kept count; returns {lines, typedKept}. */
export function excusedLines(excused, kept = 0, limit = 6) {
  const lines = [];
  if (excused?.cells) {
    const students = excused.students || [];
    const named = students.slice(0, limit).map(student => `${student.name}: ${student.sessions.map(session => session.day ? `${session.title} (${session.day})` : session.title).join(", ")}`);
    const more = students.length > limit ? `; and ${plural(students.length - limit, "more student")}` : "";
    lines.push(`Gives ${plural(excused.cells, "iClicker session")} full credit (${excused.points} points) for excused absences. ${named.join("; ")}${more}.`);
  }
  if (excused?.kept) lines.push(`${plural(excused.kept, "session")} already ${excused.kept === 1 ? "has" : "have"} full credit for an excused absence and ${excused.kept === 1 ? "keeps it" : "keep it"}.`);
  return { lines, typedKept: Math.max(0, kept - (excused?.kept || 0)) };
}

/** A proposed or stored value as the grid would show it. */
export function valueText(value) {
  if (!value || value.status === "cleared") return "—";
  if (value.status === "scored") return String(value.points ?? "");
  return value.status === "excused" ? "Excused" : value.status === "missing" ? "Missing" : String(value.status);
}

// Where a value Import from iClicker or from Canvas keeps came from (a change's `from`, KEEPS OTHERS: each updates only its own
// values).
const FROM = { canvas: "from Canvas", iclicker: "from iClicker", file: "from a file", pull: "pulled", certification: "certified" };
const fromText = from => FROM[from] || "imported";

// A preview's "Now": the cell's value and where it came from (`from`: the import or pull that wrote it, when the plan names one).
// A cell cleared by hand in Faculty is a decision a pull or an import keeps (action "kept") until Replace is ticked, so it says
// "Cleared", never looks empty.
function cellMarkup(e, cell, from = "") {
  if (!cell) return '<span class="gbd-empty">empty</span>';
  const source = cell.status === "cleared"
    ? { instructor: "by you", import: "by an import", grading: "by a pull" }[cell.source] || ""
    : from ? fromText(from) : { instructor: "typed", import: "imported", grading: "pulled" }[cell.source] || "";
  const value = cell.status === "cleared" ? '<span class="gbd-empty">Cleared</span>' : e(valueText(cell));
  return `${value}${source ? ` <small class="gbd-source">${e(source)}</small>` : ""}`;
}

// The Replace tick's accessible name: a typed value (or one another import brought in) is overwritten, a cleared cell is filled.
const replaceLabel = (e, name, cell, many = false, from = "") => cell?.status === "cleared"
  ? `Fill ${e(name)}’s cleared cell${many ? "s" : ""}`
  : `Replace ${e(name)}’s ${from ? "" : "typed "}value${many ? "s" : ""}${from ? ` ${e(fromText(from))}` : ""}`;

function actionMarkup(e, action, why = "") {
  const [label, general] = ACTION[action] || [action, ""], hint = why || general;
  return `<span class="gbd-action" data-action-kind="${e(action)}" title="${e(hint)}">${e(label)}</span>`;
}

function countsMarkup(e, counts, keys, label) {
  return `<dl class="gbd-counts" aria-label="${e(label)}">${keys.map(key => `<div data-count="${e(key)}"><dt>${e((ACTION[key] || [key])[0])}</dt><dd>${e(counts?.[key] ?? 0)}</dd></div>`).join("")}</dl>`;
}

function warningsMarkup(e, warnings) {
  return warnings?.length ? `<ul class="gbd-warnings">${warnings.map(warning => `<li>${e(warning)}</li>`).join("")}</ul>` : "";
}

function shell(ctx, { id, title, subtitle = "", body, footer, wide = false }) {
  return `<form class="gbd${wide ? " gbd-wide" : ""}" id="${id}" novalidate>${ctx.dialogHeader(ctx.escape(title), ctx.escape(subtitle))}
    <div class="dialog-body gbd-body">${body}<p class="form-error" role="alert" hidden></p></div>
    <footer class="dialog-footer gbd-footer">${footer}</footer></form>`;
}

function categoryOptions(ctx, setup, selected) {
  const e = ctx.escape;
  return `<option value="" ${selected ? "" : "selected"}>Uncategorized</option>${(setup.categories || []).map(category => `<option value="${e(category.id)}" ${category.id === selected ? "selected" : ""}>${e(category.name)}</option>`).join("")}`;
}

/** The callout over a dialog that saves a new setup revision while the setup page holds unsaved changes (a draft begun on an
 * older revision is set aside, gradebook-setup.js), or nothing when it holds none. `doing` names the save, e.g. "Saving this
 * column". */
function unsavedNote(ctx, doing) {
  return unsavedSetup(ctx.termId) ? `<div class="callout gbd-note" data-tone="warning"><p>The setup page has unsaved changes. ${doing} records a new setup revision, and those changes will be set aside.</p></div>` : "";
}

function failure(cause) {
  const error = /** @type {any} */ (cause);
  return { status: Number(error?.status) || 0, message: error?.message || "Something went wrong. Please try again.", body: error?.body };
}

// A preview area is drawn again after every answer; the instructor keeps their place: the dialog's scroll, each table's
// scroll, open disclosures and the control that had focus. (data-held-all is left out: its Decide all later button has an
// empty value, so keep() would refocus the first [data-held-all], Keep Faculty's for all.)
const KEEP = ["data-replace", "data-mapping", "data-new-title", "data-new-points", "data-new-category", "data-blank", "data-missing", "data-link-search",
  "data-filter-action", "data-filter-column", "data-filter-query", "data-replace-all", "data-replace-none", "data-held", "data-publish", "data-sync-course",
  "data-new-category-all", "data-row-edit"];
function keep(host, draw) {
  const body = host.closest(".gbd-body"), top = body?.scrollTop ?? 0;
  const regions = new Map([...host.querySelectorAll('[role="region"][aria-label]')].map(node => [node.getAttribute("aria-label"), [node.scrollTop, node.scrollLeft]]));
  const open = new Set([...host.querySelectorAll("details[data-keep][open]")].map(node => node.getAttribute("data-keep")));
  const active = document.activeElement instanceof Element && host.contains(document.activeElement) ? document.activeElement : null;
  const name = active ? KEEP.find(attribute => active.hasAttribute(attribute)) : null;
  const selector = name ? (active?.getAttribute(name) ? `[${name}="${CSS.escape(active.getAttribute(name) || "")}"]` : `[${name}]`) : null;
  draw();
  for (const node of host.querySelectorAll('[role="region"][aria-label]')) {
    const position = regions.get(node.getAttribute("aria-label"));
    if (position) [node.scrollTop, node.scrollLeft] = position;
  }
  for (const node of host.querySelectorAll("details[data-keep]")) if (open.has(node.getAttribute("data-keep"))) /** @type {HTMLDetailsElement} */ (node).open = true;
  if (body) body.scrollTop = top;
  if (selector) /** @type {HTMLElement | null} */ (host.querySelector(selector))?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------------------------------------------------------------
// A column's settings (add or edit one column). The save sends the whole setup as saved, with this column added or replaced.

export function openColumn(ctx, itemId) {
  const e = ctx.escape;
  let view = ctx.view;
  const existing = itemId ? (view.setup?.items || []).find(item => item.id === itemId) : null;
  if (itemId && !existing) { ctx.toast("This column is no longer in the gradebook.", true); return; }
  const item = existing ? draftSetup({ ...view.setup, items: [existing] }).items[0] : blankItem();
  const title = existing ? "Column settings" : "Add a column";
  const sections = view.sections || [];
  let requestId = "", session = () => false;

  const linkHost = () => `<div class="gbd-link" data-link-host>${linkControls({ ...ctx, view }, item, { field: "col", key: item.$key, label: item.title })}</div>`;
  // What one paper of the linked grading is worth (the View's `maximum`), for exactly the parts ticked here: the column's own
  // figure while its link is unchanged, else the assignment's for all of its parts. Only a suggestion; typed points stay.
  const sameParts = (a, b) => a.length === b.length && a.every(part => b.includes(part));
  const maximum = () => {
    const parts = item.link?.parts || [];
    if (!item.assignment_id || !parts.length) return null;
    if (existing && existing.assignment_id === item.assignment_id && sameParts(parts, existing.link?.parts || []) && view.links?.[itemId]?.maximum) return view.links[itemId].maximum;
    const assignment = (view.assignments || []).find(entry => entry.id === item.assignment_id);
    return assignment?.maximum && sameParts(parts, assignment.parts || []) ? assignment.maximum : null;
  };
  const body = `${unsavedNote(ctx, "Saving this column")}
    <label class="field">Title<input name="title" maxlength="120" required value="${e(item.title)}" autocomplete="off"></label>
    <div class="gbd-pair"><label class="field">Category<select name="category_id">${categoryOptions(ctx, view.setup || {}, item.category_id)}</select></label>
      <div class="gbd-points"><label class="field">Points<input name="points" type="number" min="0" max="100000" step="any" inputmode="decimal" value="${e(item.points)}" required aria-describedby="gradebook-column-maximum"></label>
        <p class="gbd-maximum" id="gradebook-column-maximum" data-maximum hidden></p></div></div>
    <label class="field">Due date <span class="field-optional">optional</span><input name="due_date" type="date" value="${e(item.due_date)}"></label>
    <fieldset class="gbd-flags"><legend>Counting</legend>
      <label class="checkbox-field"><input type="checkbox" name="counts" ${item.counts ? "checked" : ""}>Counts toward the grade</label>
      <label class="checkbox-field"><input type="checkbox" name="extra_credit" ${item.extra_credit ? "checked" : ""}>Extra credit (earned points count, possible points do not)</label>
      <label class="checkbox-field"><input type="checkbox" name="replaces_lowest" ${item.replaces_lowest ? "checked" : ""}>Replaces the lowest score in its category when higher (never counts on its own)</label>
      ${existing ? `<label class="checkbox-field"><input type="checkbox" name="archived" ${item.archived ? "checked" : ""}>Archived (hidden and not counted; its scores are kept)</label>` : ""}
      ${item.canvas === "webassign" || item.canvas === "quiz" ? `<p class="field-hint">${item.canvas === "quiz" ? "A Canvas quiz" : "Graded by WebAssign through Canvas"}: Sync with Canvas and the nightly import copy Canvas’s scores into it, and it is never sent to Canvas.</p>`
        : `<label class="checkbox-field"><input type="checkbox" name="canvas_off" ${item.canvas === "off" ? "checked" : ""}>Keep off Canvas (Sync with Canvas never sends it or creates its assignment)</label>`}</fieldset>
    <fieldset class="gbd-sections"><legend>Sections</legend>
      <label class="checkbox-field"><input type="checkbox" name="all_sections" ${item.sections.length ? "" : "checked"}>All sections</label>
      ${sections.map(section => `<label class="checkbox-field"><input type="checkbox" name="section" value="${e(section.id)}" ${item.sections.includes(section.id) ? "checked" : ""} ${item.sections.length ? "" : "disabled"}>Section ${e(section.number)}</label>`).join("")}</fieldset>
    <fieldset class="gbd-linked"><legend>Linked assignment</legend>${linkHost()}</fieldset>
    <label class="field">Notes <span class="field-optional">optional</span><textarea name="notes" maxlength="1000" rows="3">${e(item.notes)}</textarea></label>
    <div class="callout gbd-conflict" data-tone="warning" data-conflict hidden></div>`;
  ctx.showDialog(shell(ctx, { id: "gradebook-column-form", title, subtitle: existing ? item.title : "A new column of this term’s gradebook", body,
    footer: `${existing ? '<button type="button" class="text-button" data-column-canvas>Put on Canvas…</button>' : ""}<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${existing ? "Save column" : "Add column"}</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-column-form"));
  // "Put on Canvas" (canvas-placement.js): this column's Canvas assignment in the term's Canvas copy, in a module; Sync with
  // Canvas then sends the column's grades to it. The saved column is what it reads (unsaved edits here are not part of it).
  const toCanvas = /** @type {HTMLElement|null} */ (form.querySelector("[data-column-canvas]"));
  if (toCanvas) toCanvas.onclick = () => {
    const placement = createCanvasPlacement({ api: ctx.api, escape: ctx.escape, toast: ctx.toast, showDialog: ctx.showDialog, dialogHeader: ctx.dialogHeader });
    void placement.open({ termId: ctx.termId, itemId, title: existing.title });
  };
  const fields = /** @type {any} */ (form.elements);
  session = dialogSession(form, dialogNode());
  const hint = /** @type {HTMLElement} */ (form.querySelector("[data-maximum]"));
  function showMaximum() {
    const value = maximum(), points = String(fields.points.value || "").trim();
    hint.hidden = !value;
    hint.innerHTML = value ? `Grading’s maximum: ${e(value)} points${points !== "" && Number(points) === Number(value) ? "" : ` <button type="button" class="text-button" data-use-maximum>Use ${e(value)}</button>`}` : "";
  }
  hint.addEventListener("click", event => {
    const value = maximum();
    if (!value || !/** @type {HTMLElement} */ (event.target).closest("[data-use-maximum]")) return;
    fields.points.value = value;
    item.points = value;
    requestId = "";
    showMaximum();
    fields.points.focus();
  });

  // "From the schedule": for a new column, the term's planned rows no record follows yet; the one chosen gives its due date.
  let prefill = { chosen: () => "", refresh: () => {} };
  if (!existing) {
    void openRows(ctx.api, ctx.termId, "due").then(rows => {
      if (!session() || !rows.length) return;
      prefill = attachPrefill({ rows, before: fields.due_date.closest("label"), dateInput: fields.due_date, title: () => fields.title.value, purpose: "due",
        sections: () => (fields.all_sections?.checked ? [] : [...form.querySelectorAll('input[name="section"]:checked')].map(box => /** @type {HTMLInputElement} */ (box).value)), escape: e });
      fields.title.addEventListener("input", () => prefill.refresh());
    });
  }

  // The form is the column: every edit updates it and makes the save a new command.
  const read = () => {
    item.title = fields.title.value;
    item.category_id = fields.category_id.value || null;
    item.points = fields.points.value;
    item.due_date = fields.due_date.value;
    item.counts = fields.counts.checked;
    item.extra_credit = fields.extra_credit.checked;
    item.replaces_lowest = fields.replaces_lowest.checked;
    if (fields.archived) item.archived = fields.archived.checked;
    if (fields.canvas_off) item.canvas = fields.canvas_off.checked ? "off" : null; // null takes "off" away (a left-out field keeps it)
    item.notes = fields.notes.value;
    const all = fields.all_sections.checked;
    item.$specific = !all;
    item.sections = all ? [] : [...form.querySelectorAll('input[name="section"]:checked')].map(box => /** @type {HTMLInputElement} */ (box).value);
  };
  form.addEventListener("input", event => {
    requestId = "";
    if (/** @type {HTMLElement} */ (event.target).matches('[name="points"]')) showMaximum();
  });
  form.addEventListener("change", event => {
    requestId = "";
    const target = /** @type {HTMLElement} */ (event.target);
    if (target.matches('[name="all_sections"]')) {
      for (const box of form.querySelectorAll('input[name="section"]')) {
        /** @type {HTMLInputElement} */ (box).disabled = fields.all_sections.checked;
        if (fields.all_sections.checked) /** @type {HTMLInputElement} */ (box).checked = false;
      }
    }
    const field = target.dataset?.field;
    if (field === "col.assignment" || field === "col.part") {
      read();
      if (field === "col.assignment") linkAssignment(item, /** @type {HTMLSelectElement} */ (target).value, view);
      else togglePart(item, target.dataset.part, /** @type {HTMLInputElement} */ (target).checked);
      if (field === "col.assignment" && !fields.title.value.trim()) fields.title.value = item.title;
      // A blank points field takes grading's maximum; points the instructor typed are never replaced.
      const value = maximum();
      if (field === "col.assignment" && value && !String(fields.points.value || "").trim()) { fields.points.value = value; item.points = value; }
      const host = form.querySelector("[data-link-host]");
      host.outerHTML = linkHost();
      showMaximum();
      /** @type {HTMLElement | null} */ (form.querySelector(`[data-focus="${CSS.escape(target.dataset.focus || "")}"]`))?.focus();
    }
  });

  const conflict = /** @type {HTMLElement} */ (form.querySelector("[data-conflict]"));
  conflict.addEventListener("click", async event => {
    const button = /** @type {HTMLElement} */ (event.target).closest("[data-reload]");
    if (!(button instanceof HTMLButtonElement) || button.disabled) return;
    setBusy(button, true);
    try {
      const next = await ctx.reload();
      if (!session()) return;
      view = next;
      showMaximum();
      const still = itemId ? (view.setup?.items || []).find(entry => entry.id === itemId) : true;
      conflict.innerHTML = still
        ? `<p>The saved setup (revision ${e(view.setup?.revision ?? "—")}) is loaded. Your entries here are kept: check them and save again.</p>`
        : `<p>This column was removed from the gradebook in the meantime, so it cannot be saved.</p>`;
      /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]')).disabled = !still;
    } catch (cause) {
      if (session()) { conflict.innerHTML = `<p>${e(failure(cause).message)}</p><button type="button" class="button" data-reload>Reload the setup</button>`; }
    }
  });

  form.onsubmit = event => {
    event.preventDefault();
    read();
    void runFormAction(form, async () => {
      conflict.hidden = true;
      const setup = draftSetup(view.setup);
      const index = setup.items.findIndex(entry => entry.id === itemId);
      const column = { ...(index >= 0 ? setup.items[index] : {}), ...item };
      if (index >= 0) setup.items[index] = column; else setup.items.push(column);
      const found = problems(setup);
      if (found.length) throw new Error(found.map(problem => problem.message).join(" "));
      requestId ||= ctx.requestId();
      try {
        const before = new Set((view.setup?.items || []).map(entry => entry.id));
        const planned = prefill.chosen(), submittedTitle = item.title.trim();
        const result = await ctx.api(url(ctx, "/setup"), "POST", { base_revision: view.setup?.revision ?? null, request_id: requestId, setup: setupPayload(setup, sectionIds(view)), ...scope(ctx) });
        // The planned row chosen follows the new column (SCHEDULING "CLASS SCHEDULE"): its due date is the column's from here on.
        const made = existing ? null : (result.setup?.items || []).find(entry => !before.has(entry.id) && entry.title === submittedTitle);
        if (made && planned) await followRecord(ctx.api, ctx.termId, planned, { gradebook_item: made.id });
        ctx.replaceView(result);
        if (session()) close();
        ctx.toast(existing ? "Column saved" : `Column “${submittedTitle}” added`);
      } catch (cause) {
        const failed = failure(cause);
        if (failed.status !== 409 || !session()) throw cause;
        conflict.hidden = false;
        conflict.innerHTML = `<p>${e(failed.message)} This column was not saved; your entries here are kept.</p><button type="button" class="button" data-reload>Reload the setup</button>`;
        requestId = "";
      }
    });
  };
  showMaximum();
  fields.title.focus();
}

// ---------------------------------------------------------------------------------------------------------------------
// Linking an assignment: one column that pulls its final grades from this term's grading.

export function openLink(ctx, assignmentId) {
  const e = ctx.escape, view = ctx.view;
  const assignment = (view.assignments || []).find(entry => entry.id === assignmentId);
  if (!assignment) { ctx.toast("This assignment is not one of this course’s.", true); return; }
  const titles = (assignment.items || []).map(id => (view.setup?.items || []).find(item => item.id === id)?.title).filter(Boolean);
  const parts = PART_ORDER.filter(part => (assignment.parts || []).includes(part));
  let requestId = "";
  // The server gives a new column what one paper of its grading is worth (`maximum`), or 100 points when that is not known.
  const worth = assignment.maximum
    ? `The column starts at ${e(assignment.maximum)} points, what one paper of its grading is worth; change it in the column’s settings.`
    : "The column starts at 100 points: set its real worth in the column’s settings.";
  const body = `<p class="section-lead">${titles.length ? "Show the column" : "Add a column"} for <strong>${e(assignment.title)}</strong> linked to its grading${parts.length ? ` (${e(partsText(parts))})` : ""}.</p>
    <p class="supporting-copy">${parts.length ? "A pull records each student’s final grades from these parts; grading itself is never changed here."
      : "This term has no grading of it yet. The column is linked to its FRQ grading and pulls once final grades exist; change the parts in the column’s settings."}
      ${titles.length ? "" : worth}</p>
    ${titles.length ? `<div class="callout gbd-note" data-tone="info"><p>This assignment already has a column, <strong>${e(titles[0])}</strong>. Linking again shows that column instead of adding a second one.</p></div>` : ""}
    <label class="field">Category <span class="field-optional">optional</span><select name="category_id">${categoryOptions(ctx, view.setup || {}, null)}</select></label>`;
  ctx.showDialog(shell(ctx, { id: "gradebook-link-form", title: "Link an assignment", subtitle: assignment.title, body,
    footer: `<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${titles.length ? "Use that column" : "Add linked column"}</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-link-form"));
  const session = dialogSession(form, dialogNode());
  const select = /** @type {HTMLSelectElement} */ (form.querySelector('[name="category_id"]'));
  // Another category is another command: a retry after a lost answer reuses the ID only while the choice is the same.
  select.addEventListener("change", () => { requestId = ""; });
  form.onsubmit = event => {
    event.preventDefault();
    void runFormAction(form, async () => {
      requestId ||= ctx.requestId();
      const result = await ctx.api(url(ctx, "/items/link"), "POST", { assignment_id: assignmentId, request_id: requestId, category_id: select.value || null, ...scope(ctx) });
      ctx.replaceView(result);
      const column = (result.setup?.items || []).find(item => item.id === result.item_id);
      if (!session()) { ctx.toast(`Column “${column?.title || assignment.title}” is linked`); return; }
      const next = { ...ctx, view: result }, pulls = !!column?.link?.parts?.length;
      // An existing column that only refers to the assignment has nothing to pull until its settings tick a part.
      form.querySelector(".gbd-body").innerHTML = pulls
        ? `<p class="section-lead" role="status">Column <strong>${e(column?.title || assignment.title)}</strong> is linked to ${e(assignment.title)}.</p>
          <p class="supporting-copy">Pull its final grades now, or set the column’s points first (it is worth ${e(column?.points ?? "100")} points).</p><p class="form-error" role="alert" hidden></p>`
        : `<p class="section-lead" role="status">Column <strong>${e(column?.title || assignment.title)}</strong> refers to ${e(assignment.title)} but pulls from none of its grading.</p>
          <p class="supporting-copy">Open its settings and tick the grading to pull from.</p><p class="form-error" role="alert" hidden></p>`;
      form.querySelector(".gbd-footer").innerHTML = `<button type="button" class="button" data-action="close">Not now</button><button type="button" class="button${pulls ? "" : " primary"}" data-settings>Column settings</button>${pulls ? '<button type="button" class="button primary" data-pull>Pull scores now</button>' : ""}`;
      form.onsubmit = submit => submit.preventDefault();
      form.querySelector("[data-pull]")?.addEventListener("click", () => openPull(next, result.item_id));
      form.querySelector("[data-settings]")?.addEventListener("click", () => openColumn(next, result.item_id));
      /** @type {HTMLElement | null} */ (form.querySelector("[data-pull], [data-settings]"))?.focus();
    });
  };
  /** @type {HTMLElement | null} */ (form.querySelector('[type="submit"]'))?.focus();
}

// ---------------------------------------------------------------------------------------------------------------------
// Adding every graded assignment without a column at once (the guided start's "Add all as columns"): ONE setup save, on the
// revision on screen, with a request ID reused only while the command is unchanged. Each new column is linked to all of its
// grading this term and worth what one paper of it is worth (else 100 points), exactly as the link dialog would add it.
// Nothing is pulled: each column's own Pull does that, after its preview.

export function openAddAll(ctx, assignmentIds) {
  const e = ctx.escape;
  let view = ctx.view;
  const chosen = () => (assignmentIds || []).map(id => (view.assignments || []).find(entry => entry.id === id))
    .filter(assignment => assignment && !(assignment.items || []).length && (assignment.parts || []).length);
  if (!chosen().length) { ctx.toast("Every graded assignment already has a column."); return; }
  let requestId = "";
  const rows = list => `<ul class="gbd-add-list" aria-label="Columns to add">${list.map(assignment => `<li><strong>${e(assignment.title)}</strong><small>${e(`${partsText(PART_ORDER.filter(part => assignment.parts.includes(part)))} · ${assignment.maximum || "100"} points`)}</small></li>`).join("")}</ul>`;
  const body = `${unsavedNote(ctx, "Adding these columns")}
    <p class="section-lead">One column per assignment, each linked to its grading this term. Nothing is pulled yet.</p>
    <div data-add-all-list>${rows(chosen())}</div>
    <label class="field">Category <span class="field-optional">optional, for every new column</span><select name="category_id">${categoryOptions(ctx, view.setup || {}, null)}</select></label>
    <p class="supporting-copy">Then pull each column from its ${ctx.icon("download")} button; every pull shows its preview first.</p>
    <div class="callout gbd-conflict" data-tone="warning" data-conflict hidden></div>`;
  const count = chosen().length;
  ctx.showDialog(shell(ctx, { id: "gradebook-add-all-form", title: "Add graded assignments", subtitle: `${plural(count, "column")} in one setup save`, body,
    footer: `<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Add ${e(plural(count, "column"))}</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-add-all-form"));
  const session = dialogSession(form, dialogNode());
  const select = /** @type {HTMLSelectElement} */ (form.querySelector('[name="category_id"]'));
  const submit = /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]'));
  const conflict = /** @type {HTMLElement} */ (form.querySelector("[data-conflict]"));
  select.addEventListener("change", () => { requestId = ""; });
  conflict.addEventListener("click", async event => {
    const button = /** @type {HTMLElement} */ (event.target).closest("[data-reload]");
    if (!(button instanceof HTMLButtonElement) || button.disabled) return;
    setBusy(button, true);
    try {
      view = await ctx.reload();
      if (!session()) return;
      const left = chosen();
      /** @type {HTMLElement} */ (form.querySelector("[data-add-all-list]")).innerHTML = rows(left);
      conflict.innerHTML = left.length
        ? `<p>The saved setup (revision ${e(view.setup?.revision ?? "—")}) is loaded; the list above is what is still without a column. Add them again.</p>`
        : "<p>Every one of these assignments has a column now; nothing is left to add.</p>";
      submit.textContent = `Add ${plural(left.length, "column")}`;
      submit.disabled = !left.length;
    } catch (cause) {
      if (session()) conflict.innerHTML = `<p>${e(failure(cause).message)}</p><button type="button" class="button" data-reload>Reload the setup</button>`;
    }
  });
  form.onsubmit = event => {
    event.preventDefault();
    void runFormAction(form, async () => {
      conflict.hidden = true;
      const list = chosen();
      if (!list.length) throw new Error("Every one of these assignments has a column now.");
      const setup = draftSetup(view.setup);
      for (const assignment of list) {
        setup.items.push({ ...blankItem(), title: String(assignment.title || "").slice(0, 120), category_id: select.value || null,
          points: assignment.maximum ? String(assignment.maximum) : "100", assignment_id: assignment.id,
          link: { kind: "grading", parts: PART_ORDER.filter(part => assignment.parts.includes(part)) } });
      }
      const found = problems(setup);
      if (found.length) throw new Error(found.map(problem => problem.message).join(" "));
      requestId ||= ctx.requestId();
      try {
        const result = await ctx.api(url(ctx, "/setup"), "POST", { base_revision: view.setup?.revision ?? null, request_id: requestId, setup: setupPayload(setup, sectionIds(view)), ...scope(ctx) });
        ctx.replaceView(result);
        if (session()) close();
        ctx.toast(`Added ${plural(list.length, "linked column")}. Pull each one from its column when you are ready.`);
      } catch (cause) {
        const failed = failure(cause);
        if (failed.status !== 409 || !session()) throw cause;
        conflict.hidden = false;
        conflict.innerHTML = `<p>${e(failed.message)} Nothing was added.</p><button type="button" class="button" data-reload>Reload the setup</button>`;
        requestId = "";
      }
    });
  };
  submit.focus();
}

// ---------------------------------------------------------------------------------------------------------------------
// The term's grading scheme (GRADEBOOK GRADING SCHEME, requirement, 2026-10-06), opened from the course Home's Grades row: the setup's
// letter scale written as Canvas writes a grading scheme, each letter's range from below the letter above it down to its own
// minimum, the last letter down to 0%. Save is ONE ordinary setup save of the scale alone on the revision on screen (the rest of
// the setup is kept); every section's letters follow it. Nothing is sent to Canvas.

/** A scheme as the dialog edits it: letters and minimums as text, the last letter's minimum 0 (it is the floor). */
const schemeOf = scale => (scale?.length ? scale : STANDARD_SCALE.map(([letter, min]) => ({ letter, min })))
  .map((entry, index, list) => ({ letter: String(entry.letter ?? ""), min: index === list.length - 1 ? "0" : String(entry.min ?? "") }));
const schemePayload = scheme => scheme.map((entry, index) => ({ letter: entry.letter.trim(), min: index === scheme.length - 1 ? "0" : entry.min.trim() }));

/** @param {any} ctx @param {{onSaved?: (scale: any[]) => void}} [options] */
export function openGradingScheme(ctx, { onSaved } = {}) {
  const e = ctx.escape;
  let view = ctx.view;
  let scheme = schemeOf(view.setup?.scale);
  let saved = JSON.stringify(schemePayload(scheme));
  let requestId = "", checked = false;
  const term = [view.term?.code, view.term?.term_label].filter(Boolean).join(" · ");
  const rowsMarkup = () => {
    const uppers = scaleUppers(scheme), flagged = new Set(checked ? scaleProblems(schemePayload(scheme)).map(problem => problem.focus) : []);
    const bad = key => flagged.has(key) ? ' aria-invalid="true"' : "";
    return scheme.map((entry, index) => {
      const last = index === scheme.length - 1, name = entry.letter.trim() || `letter ${index + 1}`;
      const lower = last
        ? `<span class="gbd-scheme-floor" title="The last letter takes every percentage below the letter above it">0</span>`
        : `<input type="number" min="0" max="1000" step="any" inputmode="decimal" value="${e(entry.min)}" data-scheme-min="${index}" data-focus="scale.min:${index}" aria-label="Lowest percentage for ${e(name)}"${bad(`scale.min:${index}`)}>`;
      return `<tr><td><input type="text" maxlength="4" value="${e(entry.letter)}" data-scheme-letter="${index}" data-focus="scale.letter:${index}" aria-label="Letter ${index + 1}"${bad(`scale.letter:${index}`)}></td>
        <td><span class="gbs-min"><span class="gbs-upper" data-scheme-upper="${index}">${e(uppers[index])}</span><span class="gbs-to">to</span>${lower}<span aria-hidden="true">%</span></span></td>
        <td class="gbs-row-actions"><button type="button" class="icon-button gbs-remove" data-scheme-remove="${index}" aria-label="Remove ${e(name)}" title="Remove" ${scheme.length === 1 ? "disabled" : ""}><span aria-hidden="true">×</span></button></td></tr>`;
    }).join("");
  };
  const rounding = view.setup?.letter_rounding;
  const roundingNote = rounding === null || rounding === undefined ? ""
    : `<p class="supporting-copy">The gradebook rounds a percentage ${rounding === 0 ? "to a whole percent" : `to ${plural(rounding, "decimal")}`} before choosing its letter (Setup, Grading method).</p>`;
  const body = `${unsavedNote(ctx, "Saving the grading scheme")}
    <div class="gbs-presets" role="group" aria-label="Start from a preset grading scheme"><span>Start from</span>${Object.entries(PRESETS).map(([name, preset]) => `<button type="button" class="text-button" data-scheme-preset="${e(name)}">${e(preset.label)}</button>`).join("")}</div>
    <div class="gbs-scroll gbd-scheme" role="region" aria-label="Grading scheme" tabindex="0"><table class="gbs-table gbs-scale"><thead><tr><th scope="col">Letter grade</th><th scope="col">Range</th><th scope="col"><span class="sr-only">Removal</span></th></tr></thead><tbody data-scheme-rows>${rowsMarkup()}</tbody></table></div>
    <div class="gbs-scale-actions"><button type="button" class="text-button" data-scheme-add>${ctx.icon("plus")}Add a letter</button></div>
    ${roundingNote}<p class="gbs-preview field-hint" data-scheme-preview></p>
    <div class="callout gbd-conflict" data-tone="warning" data-conflict hidden></div>`;
  ctx.showDialog(shell(ctx, { id: "gradebook-scheme-form", title: "Grading scheme", subtitle: term ? `${term} · every section` : "Every section of the term", body,
    footer: `<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save grading scheme</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-scheme-form"));
  const session = dialogSession(form, dialogNode());
  const rows = /** @type {HTMLElement} */ (form.querySelector("[data-scheme-rows]"));
  const submit = /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]'));
  const error = /** @type {HTMLElement} */ (form.querySelector(".form-error"));
  const conflict = /** @type {HTMLElement} */ (form.querySelector("[data-conflict]"));
  const preview = /** @type {HTMLElement} */ (form.querySelector("[data-scheme-preview]"));
  // What follows typing without a redraw (focus and caret stay put): the ranges below a minimum, the preview, Save, the marks.
  const follow = () => {
    scaleUppers(scheme).forEach((upper, index) => {
      const node = rows.querySelector(`[data-scheme-upper="${index}"]`);
      if (node && node.textContent !== upper) node.textContent = upper;
    });
    const previewed = previewText(schemePayload(scheme), view);
    if (preview.textContent !== previewed) preview.textContent = previewed;
    submit.disabled = JSON.stringify(schemePayload(scheme)) === saved;
    if (!checked) return;
    const found = scaleProblems(schemePayload(scheme)), flagged = new Set(found.map(problem => problem.focus));
    for (const node of rows.querySelectorAll("input[data-focus]")) {
      if (flagged.has(/** @type {HTMLElement} */ (node).dataset.focus)) node.setAttribute("aria-invalid", "true");
      else node.removeAttribute("aria-invalid");
    }
    error.textContent = found.map(problem => problem.message).join(" ");
    error.hidden = !found.length;
  };
  const redraw = (focus = "") => {
    rows.innerHTML = rowsMarkup();
    follow();
    if (focus) /** @type {HTMLElement|null} */ (form.querySelector(focus))?.focus();
  };
  form.addEventListener("input", event => {
    const field = /** @type {HTMLInputElement} */ (event.target);
    if (field.dataset.schemeLetter !== undefined) scheme[Number(field.dataset.schemeLetter)].letter = field.value;
    else if (field.dataset.schemeMin !== undefined) scheme[Number(field.dataset.schemeMin)].min = field.value;
    else return;
    requestId = "";
    follow();
  });
  form.addEventListener("click", event => {
    const button = /** @type {HTMLElement} */ (event.target).closest("button");
    if (!(button instanceof HTMLButtonElement) || button.disabled || !form.contains(button)) return;
    if (button.dataset.schemeRemove !== undefined) {
      if (scheme.length <= 1) return;
      scheme.splice(Number(button.dataset.schemeRemove), 1);
      scheme[scheme.length - 1].min = "0";
      redraw("[data-scheme-add]");
    } else if (button.hasAttribute("data-scheme-add")) {
      // A new letter goes above the last one, which stays the floor.
      const at = Math.max(0, scheme.length - 1);
      scheme.splice(at, 0, { letter: "", min: "" });
      redraw(`[data-scheme-letter="${at}"]`);
    } else if (button.dataset.schemePreset) {
      const preset = PRESETS[button.dataset.schemePreset];
      if (!preset) return;
      scheme = schemeOf(preset.scale.map(([letter, min]) => ({ letter, min })));
      redraw();
    } else if (button.hasAttribute("data-reload")) {
      void reload(button);
      return;
    } else return;
    requestId = "";
  });
  async function reload(button) {
    setBusy(button, true);
    try {
      view = await ctx.reload();
      if (!session()) return;
      scheme = schemeOf(view.setup?.scale);
      saved = JSON.stringify(schemePayload(scheme));
      conflict.innerHTML = `<p>The saved grading scheme (setup revision ${e(view.setup?.revision ?? "—")}) is shown. Make your change again.</p>`;
      redraw();
    } catch (cause) {
      if (session()) conflict.innerHTML = `<p>${e(failure(cause).message)}</p><button type="button" class="button" data-reload>Reload the grading scheme</button>`;
    }
  }
  form.onsubmit = event => {
    event.preventDefault();
    if (submit.disabled) return;
    checked = true;
    const scale = schemePayload(scheme), found = scaleProblems(scale);
    if (found.length) {
      follow();
      /** @type {HTMLElement|null} */ (form.querySelector(`[data-focus="${CSS.escape(found[0].focus)}"]`))?.focus();
      return;
    }
    void runFormAction(form, async () => {
      conflict.hidden = true;
      requestId ||= ctx.requestId();
      try {
        const result = await ctx.api(url(ctx, "/setup"), "POST", { base_revision: view.setup?.revision ?? null, request_id: requestId, setup: { scale } });
        ctx.replaceView(result);
        if (session()) close();
        ctx.toast("Grading scheme saved. Every section’s letters use it now.");
        onSaved?.(result?.setup?.scale || scale);
      } catch (cause) {
        const failed = failure(cause);
        if (failed.status !== 409 || !session()) throw cause;
        conflict.hidden = false;
        conflict.innerHTML = `<p>${e(failed.message)} Nothing was saved.</p><button type="button" class="button" data-reload>Reload the grading scheme</button>`;
        requestId = "";
      }
    });
  };
  follow();
  /** @type {HTMLElement|null} */ (form.querySelector("[data-scheme-min]") || form.querySelector("[data-scheme-letter]"))?.focus();
}

// ---------------------------------------------------------------------------------------------------------------------
// Pulling a linked column's final grades. Reading grading takes up to a minute on a big exam, so the preview runs once on
// opening and again only on an explicit click; Replace ticks are sent with the apply and do not need a new preview.

export function openPull(ctx, itemId) {
  const e = ctx.escape, view = ctx.view;
  const item = (view.setup?.items || []).find(entry => entry.id === itemId);
  if (!item) { ctx.toast("This column is no longer in the gradebook.", true); return; }
  const assignment = (view.assignments || []).find(entry => entry.id === item.assignment_id);
  const link = view.links?.[itemId];
  const lastPull = link?.last_pull;
  // A certified assignment's column pulls its latest certification, never live grading (Certification and Export, 2026-09-26).
  const certified = link?.certification;
  const source = certified
    ? `Values come from the <strong>certification of ${e(when(certified.created_at))}</strong>; certify again in Grading to change them.`
    : "Only <strong>final</strong> grades are pulled; grading itself is not changed.";
  const body = `<p class="supporting-copy gbd-lead" data-pull-source>${source} A value you typed in the gradebook, or a cell you cleared there, stays unless you tick Replace for that student.</p>
    ${certified?.may_have_changed ? `<div class="callout gbd-note" data-tone="warning" role="status" data-certification-changed><p>Grades may have changed in Grading since that certification. The gradebook keeps the certified values until you certify again.</p>${link.plan_id ? `<a class="button callout-action" href="#grading/${e(encodeURIComponent(link.plan_id))}?tab=export">Open Certification and Export</a>` : ""}</div>` : ""}
    ${lastPull ? `<p class="gbd-meta">Last pulled ${e(when(lastPull.at))}.</p>` : ""}
    <div class="gbd-options"><label class="checkbox-field"><input type="checkbox" name="absent">Also record students marked absent in Exams as missing</label>
      <button type="button" class="button" data-update-preview hidden>Update preview</button></div>
    <div class="gbd-preview" data-preview aria-live="polite"></div>`;
  ctx.showDialog(shell(ctx, { id: "gradebook-pull-form", wide: true, title: `Pull final grades · ${item.title}`,
    subtitle: `${assignment ? `From ${assignment.title}` : "From grading"}${item.link?.parts?.length ? ` · ${partsText(item.link.parts)}` : ""} · column worth ${item.points} points`, body,
    footer: `<p class="gbd-footnote" data-footnote></p><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" disabled>Pull scores</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-pull-form"));
  const session = dialogSession(form, dialogNode());
  const host = /** @type {HTMLElement} */ (form.querySelector("[data-preview]"));
  const submit = /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]'));
  const absent = /** @type {HTMLInputElement} */ (form.querySelector('[name="absent"]'));
  const again = /** @type {HTMLButtonElement} */ (form.querySelector("[data-update-preview]"));
  const footnote = /** @type {HTMLElement} */ (form.querySelector("[data-footnote]"));
  let plan = null, previewedAbsent = false, loading = false, generation = 0, requestId = "", notice = "";
  const ticked = new Set(), filter = { action: "all", query: "" };

  const kept = () => (plan?.rows || []).filter(row => row.action === "kept");
  const writes = () => plan ? (plan.counts?.add || 0) + (plan.counts?.update || 0) + kept().filter(row => ticked.has(row.row)).length : 0;
  const stale = () => absent.checked !== previewedAbsent;
  function sync() {
    const count = writes();
    const usable = !!plan?.fingerprint;
    submit.disabled = loading || !usable || stale() || !count || form.dataset.saving === "true";
    markBusy(submit, loading);
    submit.textContent = plan && count ? `Pull ${plural(count, "score")}` : "Pull scores";
    again.hidden = loading || (usable && !stale());
    again.textContent = plan ? "Update preview" : "Preview again";
    footnote.textContent = loading ? "Reading final grades from grading…" : !plan ? "" : stale() ? "Update the preview to use the changed option." : !usable ? "Preview again before pulling." : count ? "" : "Nothing to write.";
  }

  function rowsMarkup() {
    const query = filter.query.trim().toLowerCase();
    const shown = plan.rows.filter(row => (filter.action === "all" || row.action === filter.action) && (!query || String(row.name).toLowerCase().includes(query)));
    const rows = shown.map(row => {
      const replace = row.action === "kept" ? `<label class="gbd-replace"><input type="checkbox" data-replace="${e(row.row)}" ${ticked.has(row.row) ? "checked" : ""}><span class="sr-only">${replaceLabel(e, row.name, row.current)}</span></label>` : "";
      const differs = row.detail?.possible && plan.points && row.proposed?.status === "scored" && Number(row.detail.possible) !== Number(plan.points) ? ` <small>of ${e(row.detail.possible)}</small>` : "";
      return `<tr data-action-row="${e(row.action)}"><td>${e(row.name)}</td><td>${e(row.section ?? "")}</td><td>${cellMarkup(e, row.current)}</td><td>${row.proposed ? `${e(valueText(row.proposed))}${differs}` : "—"}</td><td>${actionMarkup(e, row.action)}</td><td>${replace}</td><td class="gbd-reason">${e(row.reason || "")}</td></tr>`;
    });
    return shown.length ? tableMarkup(e, ["Student", "Section", "Now", "Proposed", "Action", "Replace", "Why"], rows, "Students of this pull")
      : `<p class="gbd-empty-note">No students in this view.</p>`;
  }

  function render() {
    if (!session()) return;
    if (!plan) {
      host.innerHTML = loading ? `<div class="gbd-busy" role="status"><span class="gbd-spinner" aria-hidden="true"></span><p><strong>Reading final grades from grading…</strong> This can take up to a minute on a large exam.</p></div>` : notice ? `<div class="callout gbd-note" data-tone="danger"><p>${e(notice)}</p></div>` : "";
      sync();
      return;
    }
    const counts = plan.counts || {}, actions = Object.keys(ACTION).filter(key => counts[key]);
    const keptRows = kept();
    if (filter.action !== "all" && !actions.includes(filter.action)) filter.action = "all";
    keep(host, () => { host.innerHTML = `${loading ? `<div class="gbd-busy" role="status"><span class="gbd-spinner" aria-hidden="true"></span><p><strong>Reading final grades from grading again…</strong> This can take up to a minute on a large exam. The preview below is the previous one.</p></div>` : ""}
      ${notice ? `<div class="callout gbd-note" data-tone="warning" role="status"><p>${e(notice)}</p></div>` : ""}
      ${countsMarkup(e, counts, ["add", "update", "same", "kept", "waiting", "none"], "What this pull would do")}
      ${warningsMarkup(e, plan.warnings)}
      <div class="gbd-toolbar"><label class="gbd-filter">Show<select data-filter-action><option value="all">All students (${e(plan.rows.length)})</option>${actions.map(key => `<option value="${e(key)}" ${filter.action === key ? "selected" : ""}>${e(ACTION[key][0])} (${e(counts[key])})</option>`).join("")}</select></label>
        <label class="gbd-filter">Find<input type="search" data-filter-query value="${e(filter.query)}" placeholder="Student name" autocomplete="off"></label>
        ${keptRows.length ? `<span class="gbd-bulk"><button type="button" class="text-button" data-replace-all>Replace all ${e(keptRows.length)} kept</button>${ticked.size ? `<button type="button" class="text-button" data-replace-none>Keep them all</button>` : ""}</span>` : ""}</div>
      <div class="gbd-table" data-rows>${rowsMarkup()}</div>`; });
    sync();
  }

  async function preview() {
    if (loading) return;
    const token = ++generation, wanted = absent.checked;
    loading = true;
    notice = notice && plan ? notice : "";
    render();
    try {
      const result = await ctx.api(url(ctx, `/items/${encodeURIComponent(itemId)}/pull/preview`), "POST", { replace: [], absent_as_missing: wanted, ...scope(ctx) }, {}, LONG);
      if (token !== generation || !session()) return;
      plan = result;
      previewedAbsent = wanted;
      requestId = "";
      // Ticks stay only on students whose typed value is still kept.
      const still = new Set(kept().map(row => row.row));
      for (const row of [...ticked]) if (!still.has(row)) ticked.delete(row);
    } catch (cause) {
      if (token !== generation || !session()) return;
      // The previous preview stays on screen for reference but can no longer be applied.
      notice = failure(cause).message;
      if (plan) plan = { ...plan, fingerprint: "" };
    } finally {
      if (token === generation) { loading = false; render(); }
    }
  }

  host.addEventListener("change", event => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (target.matches("[data-replace]")) {
      if (target.checked) ticked.add(target.dataset.replace || ""); else ticked.delete(target.dataset.replace || "");
      requestId = "";
      sync();
    } else if (target.matches("[data-filter-action]")) {
      filter.action = target.value;
      /** @type {HTMLElement} */ (host.querySelector("[data-rows]")).innerHTML = rowsMarkup();
    }
  });
  host.addEventListener("input", event => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!target.matches("[data-filter-query]")) return;
    filter.query = target.value;
    /** @type {HTMLElement} */ (host.querySelector("[data-rows]")).innerHTML = rowsMarkup();
  });
  host.addEventListener("click", event => {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target.closest("[data-replace-all]")) { for (const row of kept()) ticked.add(row.row); requestId = ""; render(); }
    else if (target.closest("[data-replace-none]")) { ticked.clear(); requestId = ""; render(); }
  });
  absent.addEventListener("change", () => { requestId = ""; sync(); });
  again.addEventListener("click", () => { notice = ""; void preview(); });

  form.onsubmit = event => {
    event.preventDefault();
    if (!plan?.fingerprint || loading || stale() || !writes()) return;
    const count = writes();
    void runFormAction(form, async () => {
      footnote.textContent = "Pulling: grading is read once more to check nothing changed…";
      requestId ||= ctx.requestId();
      try {
        const result = await ctx.api(url(ctx, `/items/${encodeURIComponent(itemId)}/pull`), "POST",
          { fingerprint: plan.fingerprint, request_id: requestId, replace: [...ticked], absent_as_missing: previewedAbsent, ...scope(ctx) }, {}, LONG);
        ctx.replaceView(result);
        if (session()) close();
        ctx.toast(`Pulled ${plural(count, "score")}`);
      } catch (cause) {
        const failed = failure(cause);
        if (failed.status !== 409 || !session()) throw cause;
        // The grades or the gradebook changed since this preview: nothing was written. Read it again; never apply on our own.
        notice = `${failed.message} Nothing was written. The preview below has been read again: check it, then pull.`;
        requestId = "";
        await preview();
      }
    }).then(() => { if (session()) sync(); });
  };
  void preview();
}

// ---------------------------------------------------------------------------------------------------------------------
// Importing a scores file: choose it, preview how its columns map and what each cell would become, then import exactly
// that preview. Mapping and option changes re-preview with the SAME file; the apply sends the plan's own options.

export function openImport(ctx) {
  const e = ctx.escape;
  const term = ctx.view.term || {};
  const body = `<div class="gbd-choose"><label class="field">Scores file (CSV or Excel .xlsx)<input type="file" name="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></label>
      <details class="disclosure gbd-formats"><summary><span>Files Faculty recognizes</span></summary><div class="disclosure-body"><ul>
        <li><strong>Gradescope course grades</strong>: the course’s Download Grades CSV (SID and one “Max Points” column per assignment).</li>
        <li><strong>Gradescope assignment scores</strong>: one assignment’s export (SID, Total Score, Max Points, Status).</li>
        <li><strong>Any sheet with a UIN column</strong> (UIN, SID or Student ID) and one column of points per item; a “Points Possible” row gives each column’s worth.</li></ul>
        <p>Students are matched by their nine-digit UIN exactly. A number is a score, EX excused, MI missing; a blank changes nothing. At most 10 MB.</p></div></details></div>
    <div class="gbd-preview" data-preview aria-live="polite"></div>`;
  ctx.showDialog(shell(ctx, { id: "gradebook-import-form", wide: true, title: "Import scores", subtitle: [term.code, term.term_label].filter(Boolean).join(" · "), body,
    footer: `<p class="gbd-footnote" data-footnote></p><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" disabled>Import</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-import-form"));
  const session = dialogSession(form, dialogNode());
  const input = /** @type {HTMLInputElement} */ (form.querySelector('[name="file"]'));
  const host = /** @type {HTMLElement} */ (form.querySelector("[data-preview]"));
  const submit = /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]'));
  const footnote = /** @type {HTMLElement} */ (form.querySelector("[data-footnote]"));
  /** @type {File | null} */
  let file = null;
  let plan = null, loading = false, generation = 0, requestId = "", notice = "", editing = false, timer = 0;
  // The instructor's choices; a preview answers them, and the apply sends the plan's own normalized options.
  let options = { mapping: null, replace: [], blank_as_missing: false, ...scope(ctx) };
  // Changes open on what Import writes, as in the Canvas and iClicker imports (requirement, 2026-10-01).
  const filter = { action: "change", column: "all" };
  const setup = () => ctx.view.setup || {};
  const items = () => (setup().items || []);

  const writes = () => plan ? (plan.counts?.add || 0) + (plan.counts?.update || 0) : 0;
  const newColumns = () => (plan?.new_items || []).length;
  const reasons = () => {
    const list = [];
    if (plan && !plan.ready) list.push("A new column needs its points before this file can be imported.");
    for (const column of plan?.columns || []) {
      const choice = options.mapping?.[column.key];
      if (choice?.action === "new" && !String(choice.title || "").trim()) list.push(`Give the new column for “${column.title}” a title.`);
    }
    return list;
  };
  function sync() {
    const blocked = reasons();
    submit.disabled = loading || editing || !plan?.fingerprint || blocked.length > 0 || !(writes() || newColumns()) || form.dataset.saving === "true";
    markBusy(submit, loading);
    submit.textContent = plan ? `Import ${plural(writes(), "score")}${newColumns() ? ` · ${plural(newColumns(), "new column")}` : ""}` : "Import";
    footnote.textContent = loading ? "Reading the file…" : editing ? "Finish the change to update the preview." : blocked[0]
      || (plan && !plan.fingerprint ? "Fix the choice above to update the preview." : plan && !(writes() || newColumns()) ? "Nothing to import." : "");
  }

  const mappingValue = choice => !choice || choice.action === "skip" ? "skip" : choice.action === "new" ? "new" : `item:${choice.item_id}`;
  function mappingMarkup() {
    const rows = plan.columns.map(column => {
      const choice = options.mapping?.[column.key] || column.mapping || { action: "skip" };
      const value = mappingValue(choice), key = e(column.key);
      const counts = column.counts || {};
      const summary = ["add", "update", "same", "kept", "unreadable", "blank", "not_applicable"].filter(name => counts[name])
        .map(name => `${counts[name]} ${name === "not_applicable" ? "other section" : name === "unreadable" || name === "blank" ? name : ACTION[name][0].toLowerCase()}`).join(" · ");
      const options_ = `<option value="skip" ${value === "skip" ? "selected" : ""}>Skip</option><option value="new" ${value === "new" ? "selected" : ""}>New column</option>
        <optgroup label="Gradebook columns">${items().map(entry => `<option value="item:${e(entry.id)}" ${value === `item:${entry.id}` ? "selected" : ""} ${entry.archived ? "disabled" : ""}>${e(entry.title)}${entry.archived ? " (archived)" : ""}</option>`).join("")}</optgroup>`;
      const fresh = choice.action === "new" ? `<div class="gbd-new-column">
          <label>Title<input type="text" maxlength="120" data-new-title="${key}" value="${e(choice.title || "")}"></label>
          <label>Category<select data-new-category="${key}">${categoryOptions(ctx, setup(), choice.category_id || null)}</select></label>
          <label>Points<input type="number" min="0" max="100000" step="any" inputmode="decimal" data-new-points="${key}" value="${e(choice.points ?? "")}" ${choice.points ? "" : 'aria-invalid="true"'}></label></div>` : "";
      return `<tr><td><strong>${e(column.title)}</strong>${column.max_points ? `<br><small>worth ${e(column.max_points)}</small>` : ""}</td>
        <td><select data-mapping="${key}" aria-label="Where ${e(column.title)} goes">${options_}</select>${fresh}</td><td class="gbd-reason">${e(summary || "nothing")}</td></tr>`;
    });
    return tableMarkup(e, ["File column", "Goes to", "Values"], rows, "Columns of the file");
  }

  function changesMarkup() {
    const titles = new Map(plan.columns.map(column => [column.key, column.title]));
    const written = change => change.action === "add" || change.action === "update";
    const action = filter.action === "change" && !writes() ? "all" : filter.action;
    const shown = plan.changes.filter(change => (action === "all" || (action === "change" ? written(change) : change.action === action)) && (filter.column === "all" || change.column === filter.column));
    const replaced = new Set(options.replace || []);
    const rows = shown.map(change => {
      const box = change.action === "kept" || replaced.has(change.row) ? `<label class="gbd-replace"><input type="checkbox" data-replace="${e(change.row)}" ${replaced.has(change.row) ? "checked" : ""}><span class="sr-only">${replaceLabel(e, change.name, change.current, true)}</span></label>` : "";
      return `<tr><td>${e(change.name)}</td><td>${e(change.section ?? "")}</td><td>${e(titles.get(change.column) || change.column)}</td><td>${cellMarkup(e, change.current)}</td><td>${e(valueText(change.proposed))}</td><td>${actionMarkup(e, change.action)}</td><td>${box}</td></tr>`;
    });
    return shown.length ? tableMarkup(e, ["Student", "Section", "File column", "Now", "In the file", "Action", "Replace"], rows, "What the import would change")
      : `<p class="gbd-empty-note">No changes in this view.</p>`;
  }

  function render() {
    if (!session()) return;
    if (!plan) {
      host.innerHTML = loading ? `<div class="gbd-busy" role="status"><span class="gbd-spinner" aria-hidden="true"></span><p><strong>Reading the file…</strong></p></div>` : notice ? `<div class="callout gbd-note" data-tone="danger"><p>${e(notice)}</p></div>` : "";
      sync();
      return;
    }
    const counts = plan.counts || {}, actions = ["add", "update", "same", "kept"].filter(key => counts[key]);
    const titles = plan.columns.filter(column => plan.changes.some(change => change.column === column.key));
    const unmatched = plan.unmatched || [], unreadable = plan.unreadable || [];
    if (filter.action !== "all" && filter.action !== "change" && !actions.includes(filter.action)) filter.action = "all";
    if (filter.column !== "all" && !titles.some(column => column.key === filter.column)) filter.column = "all";
    keep(host, () => { host.innerHTML = `${loading ? `<div class="gbd-busy" role="status"><span class="gbd-spinner" aria-hidden="true"></span><p><strong>Updating the preview…</strong></p></div>` : ""}
      ${notice ? `<div class="callout gbd-note" data-tone="warning" role="status"><p>${e(notice)}</p></div>` : ""}
      <p class="gbd-meta"><strong>${e(FORMATS[plan.format] || plan.format || "Scores file")}</strong> · ${e(plan.filename || file?.name || "")}</p>
      ${countsMarkup(e, counts, ["add", "update", "same", "kept"], "What this import would do")}
      <p class="gbd-meta">${[counts.unreadable ? `${plural(counts.unreadable, "unreadable value")}` : "", counts.blank ? `${plural(counts.blank, "blank cell")} (nothing changes)` : "", unmatched.length ? `${plural(unmatched.length, "row")} matched no student` : "", counts.not_applicable ? `${plural(counts.not_applicable, "value")} for students of other sections` : ""].filter(Boolean).map(e).join(" · ")}</p>
      ${warningsMarkup(e, plan.warnings)}
      <h3 class="gbd-heading">Columns</h3>${mappingMarkup()}
      <label class="checkbox-field gbd-option"><input type="checkbox" data-blank ${options.blank_as_missing ? "checked" : ""}>Record blank cells as missing</label>
      ${unmatched.length ? `<details class="disclosure gbd-details" data-keep="unmatched"><summary><span>${e(`${plural(unmatched.length, "row")} that ${unmatched.length === 1 ? "matches" : "match"} no student`)}</span></summary><div class="disclosure-body">${tableMarkup(e, ["Line", "Name", "Why"], unmatched.map(row => `<tr><td>${e(row.line)}</td><td>${e(row.name || "")}</td><td class="gbd-reason">${e(row.reason)}</td></tr>`), "Rows that match no student")}</div></details>` : ""}
      ${unreadable.length ? `<details class="disclosure gbd-details" data-keep="unreadable"><summary><span>${e(plural(unreadable.length, "value"))} Faculty could not read</span></summary><div class="disclosure-body">${tableMarkup(e, ["Line", "Student", "File column", "Value"], unreadable.map(row => `<tr><td>${e(row.line)}</td><td>${e(row.name || "")}</td><td>${e(plan.columns.find(column => column.key === row.column)?.title || row.column)}</td><td>${e(row.raw)}</td></tr>`), "Unreadable values")}</div></details>` : ""}
      <h3 class="gbd-heading">Changes</h3>
      ${counts.kept ? `<p class="gbd-meta">${e(`${plural(counts.kept, "value")} you typed in Faculty ${counts.kept === 1 ? "stays" : "stay"} as ${counts.kept === 1 ? "it is" : "they are"} unless you tick Replace (show Kept).`)}</p>` : ""}
      <div class="gbd-toolbar"><label class="gbd-filter">Show<select data-filter-action>${writes() ? `<option value="change" ${filter.action === "change" ? "selected" : ""}>What Import writes (${e(writes())})</option>` : ""}<option value="all" ${filter.action === "all" || (filter.action === "change" && !writes()) ? "selected" : ""}>Everything compared (${e(plan.changes.length)})</option>${actions.map(key => `<option value="${e(key)}" ${filter.action === key ? "selected" : ""}>${e(ACTION[key][0])} (${e(counts[key])})</option>`).join("")}</select></label>
        ${titles.length > 1 ? `<label class="gbd-filter">Column<select data-filter-column><option value="all">Every column</option>${titles.map(column => `<option value="${e(column.key)}" ${filter.column === column.key ? "selected" : ""}>${e(column.title)}</option>`).join("")}</select></label>` : ""}</div>
      <div class="gbd-table" data-rows>${changesMarkup()}</div>`; });
    sync();
  }

  async function preview({ keepNotice = false } = {}) {
    if (!file || !session()) return;
    clearTimeout(timer);
    const token = ++generation, sent = JSON.stringify(options);
    loading = true;
    editing = false;
    if (!keepNotice) notice = "";
    render();
    const data = new FormData();
    data.append("file", file);
    data.append("options", sent);
    try {
      const result = await ctx.api(url(ctx, "/imports/preview"), "POST", data, {}, LONG);
      if (token !== generation || !session()) return;
      plan = result;
      options = { mapping: Object.fromEntries(result.columns.map(column => [column.key, column.mapping])), replace: [...(result.options?.replace || [])], blank_as_missing: !!result.options?.blank_as_missing, ...scope(ctx) };
      requestId = "";
    } catch (cause) {
      if (token !== generation || !session()) return;
      // The previous plan stays on screen but cannot be imported: it no longer answers the instructor's choices.
      notice = failure(cause).message;
      if (plan) plan = { ...plan, fingerprint: "" };
    } finally {
      if (token === generation) { loading = false; render(); }
    }
  }
  const editingPreview = () => { ++generation; clearTimeout(timer); loading = false; editing = true; sync(); };
  const later = () => { editingPreview(); timer = window.setTimeout(() => void preview(), 350); };

  input.addEventListener("change", () => {
    const chosen = input.files?.[0] || null;
    generation += 1;
    plan = null; loading = false; notice = ""; requestId = "";
    options = { mapping: null, replace: [], blank_as_missing: false, ...scope(ctx) };
    filter.action = "change"; filter.column = "all";
    file = null;
    if (chosen && (chosen.size > MAX_FILE || !/\.(csv|xlsx)$/i.test(chosen.name))) {
      notice = chosen.size > MAX_FILE ? "Choose a file of 10 MB or less." : "Choose a CSV or Excel (.xlsx) file.";
      render();
      return;
    }
    file = chosen;
    if (file) void preview(); else render();
  });
  host.addEventListener("change", event => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    const mapping = { ...(options.mapping || {}) };
    if (target.matches("[data-mapping]")) {
      const key = target.dataset.mapping || "", column = plan.columns.find(entry => entry.key === key), value = target.value;
      mapping[key] = value === "skip" ? { action: "skip" } : value === "new"
        ? { action: "new", title: String(column?.title || "").trim().slice(0, 120), category_id: null, points: column?.max_points && Number(column.max_points) > 0 ? column.max_points : null }
        : { action: "item", item_id: value.slice(5) };
      options = { ...options, mapping };
      void preview();
    } else if (target.matches("[data-new-title], [data-new-points], [data-new-category]")) {
      const key = target.dataset.newTitle || target.dataset.newPoints || target.dataset.newCategory || "";
      const choice = { ...(mapping[key] || { action: "new" }) };
      if (target.matches("[data-new-title]")) choice.title = target.value.trim();
      else if (target.matches("[data-new-points]")) choice.points = target.value.trim() || null;
      else choice.category_id = target.value || null;
      mapping[key] = choice;
      options = { ...options, mapping };
      if (target.matches("[data-new-title]") && !choice.title) { editingPreview(); editing = false; sync(); return; }
      void preview();
    } else if (target.matches("[data-blank]")) {
      options = { ...options, blank_as_missing: target.checked };
      void preview();
    } else if (target.matches("[data-replace]")) {
      const replace = new Set(options.replace || []);
      if (target.checked) replace.add(target.dataset.replace || ""); else replace.delete(target.dataset.replace || "");
      options = { ...options, replace: [...replace] };
      later();
    } else if (target.matches("[data-filter-action], [data-filter-column]")) {
      if (target.matches("[data-filter-action]")) filter.action = target.value; else filter.column = target.value;
      /** @type {HTMLElement} */ (host.querySelector("[data-rows]")).innerHTML = changesMarkup();
    }
  });
  host.addEventListener("input", event => {
    const target = /** @type {HTMLElement} */ (event.target);
    if (target.matches("[data-new-title], [data-new-points]")) editingPreview();
  });

  form.onsubmit = event => {
    event.preventDefault();
    if (!file || !plan || !plan.fingerprint || loading || editing || reasons().length) return;
    const count = writes(), created = newColumns(), filename = plan.filename || file.name;
    void runFormAction(form, async () => {
      requestId ||= ctx.requestId();
      const data = new FormData();
      data.append("file", file);
      data.append("options", JSON.stringify(plan.options));
      data.append("fingerprint", plan.fingerprint);
      data.append("request_id", requestId);
      try {
        const result = await ctx.api(url(ctx, "/imports"), "POST", data, {}, LONG);
        ctx.replaceView(result);
        if (session()) close();
        ctx.toast(`Imported ${plural(count, "score")} from ${filename}${created ? ` · ${plural(created, "new column")}` : ""}`);
      } catch (cause) {
        const failed = failure(cause);
        if (failed.status !== 409 || !session()) throw cause;
        notice = `${failed.message} Nothing was imported. The preview below has been made again from the same file: check it, then import.`;
        requestId = "";
        await preview({ keepNotice: true });
      }
    }).then(() => { if (session()) sync(); });
  };
  input.focus();
}

// ---------------------------------------------------------------------------------------------------------------------
// Sync with Canvas (docs/decisions/canvas.md SYNC WITH CANVAS; requirement, 2026-10-06) and Import from iClicker share this dialog. Faculty reads the course each section links;
// every Canvas assignment (iClicker session) goes to a column (remembered by its ID once linked), a new column, or is left out. The
// summary writes nothing; Sync (Import) names its fingerprint and fails if Canvas or the gradebook changed since. A sync also sends
// Faculty's grades to Canvas and holds the grades changed in Canvas for a choice here. A signed-out Canvas session opens the
// sign-in window and, once signed in, this dialog again.

const IMPORTS = {
  sync: { name: "Canvas", title: "Sync with Canvas", route: "/canvas-sync", noun: "Canvas assignment", nouns: "Canvas assignments", signIn: true,
    reading: "Reading Canvas… every linked course, up to a minute.", missing: "" },
  // iClicker (gradebook/iclicker_import.py): one column per polling session; the section's iClicker course is chosen here.
  iclicker: { name: "iClicker", route: "/iclicker", noun: "iClicker session", nouns: "iClicker sessions", signIn: false,
    reading: "Reading iClicker… every session of the course.", missing: "Record a session a student did not join as missing (counts as zero)" },
};

export function openCanvasSync(ctx) { return openSourceImport(ctx, "sync"); }
export function openIClickerImport(ctx) { return openSourceImport(ctx, "iclicker"); }

async function openSourceImport(ctx, kind) {
  const e = ctx.escape;
  const src = IMPORTS[kind];
  const term = ctx.view?.term || {};
  const syncing = kind === "sync";
  const verb = syncing ? "Sync" : "Import";
  ctx.showDialog(shell(ctx, { id: `gradebook-${kind}-form`, wide: true, title: src.title || `Import from ${src.name}`, subtitle: [term.code, term.term_label].filter(Boolean).join(" · "),
    body: `<div class="gbd-preview" data-preview aria-live="polite"></div>`,
    footer: `<p class="gbd-footnote" data-footnote></p><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" ${syncing ? "data-outward" : ""} disabled>${verb}</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById(`gradebook-${kind}-form`));
  const session = dialogSession(form, dialogNode());
  const host = /** @type {HTMLElement} */ (form.querySelector("[data-preview]"));
  const submit = /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]'));
  const footnote = /** @type {HTMLElement} */ (form.querySelector("[data-footnote]"));
  let plan = null, loading = false, generation = 0, requestId = "", notice = "", editing = false, timer = 0;
  let options = { mapping: null, replace: [], missing_as_missing: false };
  // Sync with Canvas: the whole summary (`whole`), the instructor's picks for held grades, and the courses written to this time
  // (null: every linked course). A term's first sync that writes to Canvas starts with this section's course alone (the
  // instructor's "one real section first"); the course Home's Sync (no section scope, gradebook.js openTermSync) ticks every
  // course from the start (requirement, 2026-10-06).
  let whole = null, picks = {}, sending = null;
  // PUBLISH FROM SYNC (requirement, 2026-10-07): a column whose Canvas assignment is not published is ticked
  // under Publish in Canvas by default; the ones the instructor unticks stay unpublished and send nothing.
  let publishOff = new Set();
  const canvasView = ctx.view?.canvas || {};
  let courses = syncing && !canvasView.sent_before && ctx.scopeSection && canvasView.courses?.[ctx.scopeSection] ? [canvasView.courses[ctx.scopeSection]] : null;
  // The list of changes opens on what Import writes (requirement, 2026-10-01); "Everything compared" and each action stay one choice away.
  const filter = { action: "change", column: "all" };
  // Rows the instructor opened to change, assignments they decided themselves, and each group's open state (browser memory only).
  const openRows = new Set(), decided = new Set(), groupOpen = new Map();
  const course = { choosing: false, loading: false, list: [], proposed: "", linked: null, error: "" };
  const setup = () => ctx.view?.setup || {};
  const items = () => setup().items || [];
  const linked = id => !!items().find(item => item.id === id && item.link?.kind === "grading");

  const writes = () => plan ? (plan.counts?.add || 0) + (plan.counts?.update || 0) + (syncing ? plan.counts?.clear || 0 : 0) : 0;
  const newColumns = () => (plan?.new_items || []).length;
  const remembers = () => Object.keys(plan?.remember || {}).length;
  const picked = value => Object.values(picks).filter(pick => pick === value).length;
  // PUBLISH FROM SYNC: the columns offered (unpublished in Canvas, with grades to send) and those left unpublished this time.
  const offered = column => column.publish && column.send && (column.to_send || column.counts.held);
  // POSTING GRADES: the summary's posting plan entries of one action this sync does somewhere (`courses`: where it does it).
  const postingSteps = action => (whole?.posting_plan || []).filter(entry => entry.action === action && entry.courses);
  const leftOff = column => column.publish && publishOff.has(column.item_id);
  const publishing = () => [...new Set((whole?.columns || []).filter(column => offered(column) && !leftOff(column)).map(column => column.item_id))];
  const toCanvas = () => (whole?.to_canvas?.sending || 0) - (whole?.columns || []).filter(column => column.send && leftOff(column)).reduce((n, column) => n + column.to_send, 0)
    + picked("faculty");
  const fromCanvas = () => writes() + picked("canvas") + (whole?.to_canvas?.filling || 0);
  const reasons = () => {
    const list = [];
    if (plan && !plan.ready) {
      // Names the new columns the import waits for (requirement, 2026-10-02: an unnamed one in a folded group looked like a hang).
      const pointless = (plan.new_items || []).filter(entry => !entry.points).map(entry => `“${entry.title}”`);
      list.push(pointless.length ? `Give ${pointless.slice(0, 3).join(", ")}${pointless.length > 3 ? ` and ${pointless.length - 3} more` : ""} ${pointless.length === 1 ? "its" : "their"} points under Needs your choice before ${src.name} can be imported.`
        : `A new column needs its points before ${src.name} can be imported.`);
    }
    for (const column of plan?.columns || []) {
      const choice = options.mapping?.[column.key];
      if (choice?.action === "new" && !String(choice.title || "").trim()) list.push(`Give the new column for “${column.title}” a title.`);
    }
    return list;
  };
  function sync() {
    const blocked = reasons(), anything = writes() || newColumns() || remembers() || (syncing && (toCanvas() || picked("canvas") || (plan?.sync?.mark || []).length
      || (whole?.posting_plan || []).some(entry => entry.courses)));
    const choosing = kind === "iclicker" && course.choosing;
    submit.disabled = choosing || loading || editing || !plan?.fingerprint || blocked.length > 0 || !anything || form.dataset.saving === "true";
    // Busy only while Faculty reads; waiting on the instructor shows not-allowed (the footnote says what is missing).
    markBusy(submit, loading || course.loading);
    submit.textContent = syncing ? (plan && anything ? `Sync${toCanvas() ? ` · ${plural(toCanvas(), "grade")} to Canvas` : ""}${fromCanvas() ? ` · ${plural(fromCanvas(), "grade")} from Canvas` : ""}${newColumns() ? ` · ${plural(newColumns(), "new column")}` : ""}${postingSteps("post").length ? ` · posts ${plural(postingSteps("post").length, "column")}` : ""}${postingSteps("hide").length ? ` · hides ${plural(postingSteps("hide").length, "column")}` : ""}` : "Sync")
      : plan ? (writes() || newColumns() ? `Import ${plural(writes(), "score")}${newColumns() ? ` · ${plural(newColumns(), "new column")}` : ""}` : anything ? "Save matches" : "Import") : "Import";
    const waiting = syncing ? (whole?.held || []).filter(h => !picks[h.key]).length : 0;
    footnote.textContent = choosing || sending ? "" : loading ? src.reading : editing ? "Finish the change to update the preview." : blocked[0]
      || (plan && !plan.fingerprint ? "Fix the choice above to update the preview." : plan && !anything ? (syncing ? "Faculty and Canvas already agree." : "Nothing to import.")
        : plan && impact().needs ? `${plural(impact().needs, "assignment")} still need${impact().needs === 1 ? "s" : ""} your choice; the rest will ${syncing ? "sync" : "import"}.`
        : waiting ? `${plural(waiting, "grade")} changed in Canvas wait${waiting === 1 ? "s" : ""} for your choice; the rest syncs.` : "");
  }

  const courseName = id => (plan?.courses || []).find(course => course.id === id)?.name || id;
  const mappingValue = choice => !choice || choice.action === "skip" ? "skip" : choice.action === "new" ? "new" : `item:${choice.item_id}`;
  const SUGGESTED = { remembered: "matched before", title: "matched by title", new: "not in the gradebook", empty: kind === "iclicker" ? "no scores yet" : "nothing graded yet", ambiguous: "choose a column", points: `${src.name} gives no points`, jev: "suggested by Jev: check it" };
  const sectionNumber = id => (ctx.view?.sections || []).find(section => section.id === id)?.number ?? "";
  // The assignments, grouped by what needs doing (requirement, 2026-09-29: the flat list of every assignment was cumbersome): the
  // ones that need a choice, columns Faculty grades itself (compared, never written), what will be imported, and what has nothing
  // to import. Only the first two show a picker; the others show where they go and a Change button.
  const choiceOf = column => options.mapping?.[column.key] || column.mapping || { action: "skip" };
  const unfinished = choice => choice.action === "new" && (!String(choice.title || "").trim() || !choice.points);
  const groupOf = column => {
    const choice = choiceOf(column);
    if (choice.action === "skip") return (column.suggested === "ambiguous" || column.suggested === "points") && !decided.has(column.key) ? "needs" : "none";
    // A new column still missing its title or points holds the import back: it stays at the top, never inside a folded group.
    if (unfinished(choice)) return "needs";
    // Sync with Canvas: WebAssign's assignments (Canvas's scores), those linked for the first time, and Faculty's own, linked before.
    if (syncing) return (plan?.sync?.mirror || []).includes(column.key) ? "webassign"
      : choice.action === "new" || (plan?.sync?.adopting || []).includes(column.key) ? "import" : "linked";
    return choice.action === "item" && linked(choice.item_id) ? "compare" : "import";
  };
  const ROMAN = { i: "1", ii: "2", iii: "3", iv: "4", v: "5", vi: "6" };
  const FILLER = new Set(["grade", "the", "of", "and", "a", "in", "for", "only", "used", "if"]);
  const tokens = text => new Set(String(text || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean).map(word => ROMAN[word] || word).filter(word => !FILLER.has(word)));
  /** The live gradebook columns whose titles share most words with an assignment ("Exam I Grade" and "Exam 1"): shown first in its
   * picker, after the column Jev named for it (JEV CANVAS-MATCH), which leads even when it was not made the suggestion. */
  const likelyItems = column => {
    const named = column.jev ? items().find(item => item.id === column.jev.item_id && !item.archived) : null;
    const mine = tokens(column.title);
    const words = !mine.size ? [] : items().filter(item => !item.archived && item !== named).map(item => {
      const theirs = tokens(item.title), shared = [...mine].filter(word => theirs.has(word)).length;
      return { item, score: shared / Math.max(mine.size, theirs.size, 1) };
    }).filter(entry => entry.score >= 0.5).sort((x, y) => y.score - x.score).map(entry => entry.item);
    return [...(named ? [named] : []), ...words].slice(0, 3);
  };
  const categoryName = choice => choice.category_id ? (setup().categories || []).find(entry => entry.id === choice.category_id)?.name : choice.category_name;
  const destination = choice => choice.action === "new" ? `New column${categoryName(choice) ? ` in ${categoryName(choice)}` : ""}${choice.points ? ` · ${choice.points} points` : ""}`
    : choice.action === "item" ? `${items().find(entry => entry.id === choice.item_id)?.title || "A column"}${linked(choice.item_id) ? " (compare only)" : ""}` : "Not imported";
  function impact() {
    const out = { created: 0, scoresNew: 0, existing: 0, scoresExisting: 0, compared: 0, left: 0, needs: 0 };
    for (const column of plan?.columns || []) {
      const group = groupOf(column), written = (column.counts?.add || 0) + (column.counts?.update || 0);
      if (group === "needs") out.needs++;
      else if (group === "none") out.left++;
      else if (group === "compare") out.compared++;
      else if (choiceOf(column).action === "new") { out.created++; out.scoresNew += written; }
      else { out.existing++; out.scoresExisting += written; }
    }
    return out;
  }
  function summaryMarkup() {
    // Values another import or a pull brought in, which this import keeps (KEEPS OTHERS), are not "typed in Faculty".
    const others = plan.changes.filter(change => change.action === "kept" && change.from);
    const o = impact(), excused = excusedLines(plan.excused, (plan.counts?.kept || 0) - others.length), kept = excused.typedKept, lines = [];
    if (o.created) lines.push(`Creates ${plural(o.created, "new column")} and writes ${plural(o.scoresNew, "score")} in ${o.created === 1 ? "it" : "them"}.`);
    if (o.existing) lines.push(o.scoresExisting ? `Writes ${plural(o.scoresExisting, "score")} into ${plural(o.existing, "existing column")}.` : `${plural(o.existing, "existing column")} already up to date.`);
    if (o.compared) lines.push(`Compares ${plural(o.compared, "column")} with Faculty’s grading; nothing there is written.`);
    lines.push(...excused.lines);
    if (kept) lines.push(`${plural(kept, "value")} you typed in Faculty stay as they are unless you tick Replace (show Kept under Review every change).`);
    if (others.length) {
      const where = [...new Set(others.map(change => fromText(change.from)))].join(" or ");
      lines.push(`${plural(others.length, "value")} ${where} ${others.length === 1 ? "stays as it is" : "stay as they are"}: ${src.name} updates only what it imported. Tick Replace to overwrite (show Kept under Review every change).`);
    }
    if (o.left) lines.push(`${plural(o.left, "assignment")} ${o.left === 1 ? "has" : "have"} nothing to import and ${o.left === 1 ? "is" : "are"} left alone.`);
    if (o.needs) lines.push(`${plural(o.needs, "assignment")} need${o.needs === 1 ? "s" : ""} your choice below; until you choose, ${o.needs === 1 ? "it is" : "they are"} left out.`);
    if (!lines.length) lines.push("Nothing to import.");
    return `<div class="callout gbd-note gbd-summary" data-tone="${o.needs ? "warning" : "info"}"><p><strong>When you press Import</strong></p><ul>${lines.map(line => `<li>${e(line)}</li>`).join("")}</ul></div>`;
  }
  const fold = (key, title, hint, body, open = false) => body ? `<details class="disclosure gbd-details" data-keep="${e(key)}" ${open ? "open" : ""}><summary><span>${e(title)}</span><span class="disclosure-hint">${e(hint)}</span></summary><div class="disclosure-body">${body}</div></details>` : "";

  function pickerMarkup(column, choice) {
    const value = mappingValue(choice), key = e(column.key);
    const likely = likelyItems(column), likelyIds = new Set(likely.map(entry => entry.id));
    const optionFor = entry => `<option value="item:${e(entry.id)}" ${value === `item:${entry.id}` ? "selected" : ""} ${entry.archived ? "disabled" : ""}>${e(entry.title)}${entry.archived ? " (archived)" : entry.link?.kind === "grading" ? " (compare only)" : ""}</option>`;
    const choices = `<option value="skip" ${value === "skip" ? "selected" : ""}>Skip</option><option value="new" ${value === "new" ? "selected" : ""}>New column</option>
      ${likely.length ? `<optgroup label="Likely matches">${likely.map(optionFor).join("")}</optgroup>` : ""}
      <optgroup label="Gradebook columns">${items().filter(entry => !likelyIds.has(entry.id)).map(optionFor).join("")}</optgroup>`;
    const category = choice.category_id ? choice.category_id : choice.category_name ? `new:${choice.category_name}` : "";
    const fresh = choice.action === "new" ? `<div class="gbd-new-column">
        <label>Title<input type="text" maxlength="120" data-new-title="${key}" value="${e(choice.title || "")}"></label>
        <label>Category<select data-new-category="${key}"><option value="" ${category ? "" : "selected"}>Uncategorized</option>${(setup().categories || []).map(c => `<option value="${e(c.id)}" ${c.id === category ? "selected" : ""}>${e(c.name)}</option>`).join("")}${choice.category_name ? `<option value="${e(`new:${choice.category_name}`)}" selected>New category “${e(choice.category_name)}”</option>` : column.group && !(setup().categories || []).some(c => c.name.toLowerCase() === column.group.toLowerCase()) ? `<option value="${e(`new:${column.group}`)}">New category “${e(column.group)}”</option>` : ""}</select></label>
        <label>Points<input type="number" min="0" max="100000" step="any" inputmode="decimal" data-new-points="${key}" value="${e(choice.points ?? "")}" ${choice.points ? "" : 'aria-invalid="true"'}></label></div>` : "";
    return `<select data-mapping="${key}" aria-label="Where ${e(column.title)} goes">${choices}</select>${fresh}`;
  }
  function rowMarkup(column, group, several) {
    const choice = choiceOf(column), key = e(column.key), counts = column.counts || {};
    const summary = ["add", "update", "clear", "same", "kept", "differs", "waiting", "unposted", "unreadable", "not_applicable"].filter(name => counts[name])
      .map(name => `${counts[name]} ${name === "not_applicable" ? "other section" : name === "unposted" ? "not posted in Canvas" : name === "waiting" ? "pending review" : name === "unreadable" ? "unreadable" : ACTION[name][0].toLowerCase()}`).join(" · ");
    // A new column missing its title or points stays open: the import waits for it.
    const incomplete = unfinished(choice);
    const open = group === "needs" || openRows.has(column.key) || incomplete;
    const where = open ? `${pickerMarkup(column, choice)}${group !== "needs" && !incomplete ? `<p><button type="button" class="text-button" data-row-edit="${key}">Done</button></p>` : ""}`
      : `<span class="gbd-dest">${e(destination(choice))}</span> <button type="button" class="text-button" data-row-edit="${key}">Change</button>`;
    const compare = choice.action === "item" && linked(choice.item_id) ? `<p class="gbd-reason">Compare only: Faculty grades this column; ${e(src.name)}’s values are shown, never written.</p>` : "";
    const check = choice.action === "item" ? checkMarkup(column) : "";
    // JEV CANVAS-MATCH: a column Jev named but did not suggest (lower confidence, other points, taken) is only pointed out.
    const named = column.jev && !column.jev.applied && choice.action !== "item" ? items().find(item => item.id === column.jev.item_id) : null;
    const jev = named ? `<p class="gbd-reason">Jev’s likely match: “${e(named.title)}” (first under Likely matches).</p>` : "";
    // A session matched to a column limited to other sections widens it to this section (in the import's setup revision).
    const widened = choice.action === "item" ? (plan.widen?.[choice.item_id] || []).map(sectionNumber).filter(n => n !== "") : [];
    const widen = widened.length ? `<p class="gbd-reason">The column will also apply to Section ${e(widened.join(", "))}.</p>` : "";
    const facts = [column.points != null ? `worth ${column.points}` : kind === "iclicker" ? "points not given" : "", column.group, column.date ? shortDate(column.date) : "",
      column.questions ? plural(column.questions, "question") : "", column.due_date ? `due ${shortDate(column.due_date)}` : "", column.published ? "" : "not published", SUGGESTED[column.suggested] || ""].filter(Boolean).join(" · ");
    return `<tr><td><strong>${e(column.title)}</strong>${several ? `<br><small>${e(courseName(column.course_id))}</small>` : ""}<br><small>${e(facts)}</small></td>
      <td>${where}${compare}${widen}${check}${jev}</td><td class="gbd-reason">${e(summary || "nothing")}</td></tr>`;
  }
  const GROUPS = syncing ? [
    ["needs", "Needs your choice", "Faculty could not tell which column these Canvas assignments belong to, or Canvas gives a new column no points. Choose a column or give the points, or leave them out.", true],
    ["import", "New from Canvas", "Linked for the first time: Canvas’s grades fill the cells Faculty never filled; a grade both hold differently waits for your choice.", null],
    ["webassign", "Graded in Canvas", "WebAssign and Canvas quizzes: their columns copy Canvas exactly and are never sent to it.", false],
    ["linked", "Already linked", "Faculty’s columns: their grades go to Canvas (To Canvas, below).", false],
    ["none", "Left out", "Not synced. Change one to bring it in.", false],
  ] : [
    ["needs", "Needs your choice", `Faculty could not tell which column these belong to, or ${src.name} gives a new column no points. Choose a column or give the points, or leave them out.`, true],
    ["compare", "Compared with Faculty’s grading", "These columns are graded in Faculty: their grades are compared with yours and never written.", true],
    ["import", "Will be imported", "", null],
    // A Canvas assignment with nothing graded yet still becomes a column (requirement, 2026-10-02): only skips land here.
    ["none", "Nothing to import", kind === "iclicker" ? "No scores yet, or skipped. Change one to bring it in anyway." : "Skipped. Change one to bring it in.", false],
  ];
  function mappingMarkup() {
    const several = (plan.courses || []).length > 1, newCount = plan.columns.filter(column => choiceOf(column).action === "new").length;
    return GROUPS.map(([group, title, lead, byDefault]) => {
      const list = plan.columns.filter(column => groupOf(column) === group);
      if (!list.length) return "";
      // A match Jev suggested opens its group, so it is seen before Import (JEV CANVAS-MATCH).
      const open = groupOpen.get(group) ?? (byDefault === null ? list.length <= 8 || list.some(column => column.suggested === "jev") : byDefault);
      const all = group === "import" && kind === "iclicker" && newCount ? `<label class="gbd-filter gbd-option">Put the ${e(plural(newCount, "new column"))} in<select data-new-category-all><option value="">Choose a category…</option><option value="none">Uncategorized</option>${(setup().categories || []).map(c => `<option value="${e(c.id)}">${e(c.name)}</option>`).join("")}</select></label>` : "";
      return fold(`group-${group}`, title, list.length, `${lead ? `<p class="supporting-copy">${e(lead)}</p>` : ""}${all}${tableMarkup(e, [src.noun.replace(/^./, c => c.toUpperCase()), "Goes to", "Values"], list.map(column => rowMarkup(column, group, several)), `${src.nouns}: ${title}`)}`, open);
    }).join("");
  }

  // THE GRADE CHECK (requirement, 2026-09-28): for a match, whether the grades both sides hold agree. A proposed match (not yet
  // remembered) that disagrees is flagged: it may be the wrong assignment. "Show" filters the changes to that column's differences.
  function checkMarkup(column) {
    const check = column.check;
    if (!check) return "";
    const differ = (check.mismatch || 0) + (check.faculty_only || 0);
    const parts = [check.mismatch ? `${check.mismatch} of ${check.compared} grades differ` : "", check.faculty_only ? `${check.faculty_only} only in Faculty` : ""].filter(Boolean).join(" · ");
    const tag = check.proposed ? '<span class="chip" data-tone="neutral">Proposed match</span> ' : "";
    if (differ) return `<p class="gbd-check" data-grade-check="differs">${tag}<span class="chip" data-tone="warning">${e(parts)}</span> <button type="button" class="text-button" data-check-show="${e(column.key)}">Show</button>${check.proposed ? "<br><small>Check it is the same assignment before importing.</small>" : ""}</p>`;
    if (check.compared) return `<p class="gbd-check" data-grade-check="match">${tag}<span class="chip" data-tone="success">${e(check.compared === 1 ? "The 1 grade both hold matches" : `All ${check.compared} grades both hold match`)}</span></p>`;
    return tag ? `<p class="gbd-check">${tag}<small>No grades to compare yet.</small></p>` : "";
  }

  // Gradebook columns no Canvas assignment goes to: each can be linked to one found by searching Canvas's assignments of that course.
  const searches = new Map();
  function linkResults(itemId, courseId) {
    const words = String(searches.get(`${itemId}|${courseId}`) || "").toLowerCase().split(/\s+/).filter(Boolean);
    const destination = column => { const choice = options.mapping?.[column.key] || column.mapping || {}; return choice.action === "item" ? `now goes to “${items().find(entry => entry.id === choice.item_id)?.title || "a column"}”` : choice.action === "new" ? "now a new column" : "now skipped"; };
    const pool = plan.columns.filter(column => column.course_id === courseId);
    const found = words.length ? pool.filter(column => words.every(word => column.title.toLowerCase().includes(word)))
      : pool.filter(column => (options.mapping?.[column.key] || column.mapping || {}).action !== "item");
    if (!found.length) return `<p class="gbd-empty-note">${words.length ? "No Canvas assignment of this course matches." : "Every Canvas assignment of this course already goes to a column. Search to choose one anyway."}</p>`;
    return `<ul class="gbd-canvas-results">${found.slice(0, 8).map(column => `<li><button type="button" class="text-button" data-link-choose="${e(column.key)}" data-link-item="${e(itemId)}">${e(column.title)}</button> <small>${e([column.points != null ? `worth ${column.points}` : "", column.group, destination(column)].filter(Boolean).join(" · "))}</small></li>`).join("")}${found.length > 8 ? `<li><small>${e(found.length - 8)} more: type more of the title.</small></li>` : ""}</ul>`;
  }
  function unlinkedMarkup() {
    const list = plan.unlinked_items || [];
    if (!list.length) return "";
    const several = (plan.courses || []).length > 1;
    return `<details class="disclosure gbd-details" data-keep="unlinked"><summary><span>Gradebook columns with no ${e(src.name)} assignment</span><span class="disclosure-hint">${e(list.length)}</span></summary><div class="disclosure-body">
      <p class="supporting-copy">No Canvas assignment goes to these columns. If Canvas has one under another name, find it and link it; its grades are then checked against the column’s.${syncing ? " Otherwise Sync creates it in Canvas once the column has grades." : ""}</p>
      <ul class="gbd-canvas-unlinked">${list.map(entry => `<li><strong>${e(entry.title)}</strong> <small>worth ${e(entry.points)}${entry.compare_only ? " · compare only" : ""}</small>${entry.courses.map(courseId => `<div class="gbd-canvas-link"><label>Link to a Canvas assignment${several ? ` in ${e(courseName(courseId))}` : ""}<input type="search" data-link-search="${e(entry.item_id)}|${e(courseId)}" value="${e(searches.get(`${entry.item_id}|${courseId}`) || "")}" placeholder="Search Canvas assignments…" autocomplete="off"></label><div data-link-results="${e(entry.item_id)}|${e(courseId)}">${linkResults(entry.item_id, courseId)}</div></div>`).join("")}</li>`).join("")}</ul></div></details>`;
  }

  function changesMarkup() {
    const titles = new Map(plan.columns.map(column => [column.key, column.title]));
    const written = change => change.action === "add" || change.action === "update" || change.action === "clear";
    const action = filter.action === "change" && !writes() ? "all" : filter.action;
    const shown = plan.changes.filter(change => (action === "all" || (action === "change" ? written(change) : action === "mismatch" ? change.mismatch : change.action === action)) && (filter.column === "all" || change.column === filter.column));
    const replaced = new Set(options.replace || []);
    const rows = shown.map(change => {
      const box = !change.compare && (change.action === "kept" || replaced.has(change.row)) ? `<label class="gbd-replace"><input type="checkbox" data-replace="${e(change.row)}" ${replaced.has(change.row) ? "checked" : ""}><span class="sr-only">${replaceLabel(e, change.name, change.current, true, change.from)}</span></label>` : "";
      return `<tr><td>${e(change.name)}</td><td>${e(change.section ?? "")}</td><td>${e(titles.get(change.column) || change.column)}</td><td>${cellMarkup(e, change.current, change.from)}</td><td>${e(valueText(change.proposed))}${change.posted ? "" : ' <small class="gbd-source">not posted</small>'}</td><td>${actionMarkup(e, change.action, change.from && change.action === "kept" ? `A value ${fromText(change.from)}, not from ${src.name}: tick Replace to overwrite it` : "")}</td><td>${box}</td></tr>`;
    });
    return shown.length ? tableMarkup(e, ["Student", "Section", src.noun.replace(/^./, c => c.toUpperCase()), "Now", `In ${src.name}`, "Action", "Replace"], rows, "What the import would change")
      : `<p class="gbd-empty-note">No changes in this view.</p>`;
  }

  const people = (list, label, detail) => list?.length ? `<details class="disclosure gbd-details" data-keep="${e(label)}"><summary><span>${e(label)}</span><span class="disclosure-hint">${e(list.length)}</span></summary><div class="disclosure-body"><ul>${list.map(row => `<li>${e(row.name)}${detail(row) ? ` <small>· ${e(detail(row))}</small>` : ""}</li>`).join("")}</ul></div></details>` : "";

  // Sync with Canvas: what goes where, the grades changed in Canvas, the courses written to, and what cannot sync.
  const sentColumns = () => (whole?.columns || []).filter(column => column.send && !leftOff(column));
  function syncSummaryMarkup() {
    const o = impact(), held = whole?.held || [], lines = [], creating = whole?.to_canvas?.creating || 0, mirror = (plan.sync?.mirror || []).length;
    if (toCanvas()) lines.push(`Sends ${plural(toCanvas(), "grade")} to Canvas: what Faculty has that Canvas does not show yet.`);
    if (creating) lines.push(`Creates ${plural(creating, "assignment")} in Canvas first (published, in the Assignment Group named after the column’s category).`);
    const publishes = sentColumns().filter(column => column.publish && column.to_send).length;
    if (publishes) lines.push(`Publishes ${plural(publishes, "assignment")} in Canvas first, then sends ${publishes === 1 ? "its" : "their"} grades.`);
    if (fromCanvas()) lines.push(`Takes ${plural(fromCanvas(), "grade")} from Canvas${mirror ? ": Canvas’s own (WebAssign, Canvas quizzes), and grades Faculty never had" : ": grades Faculty never had"}.`);
    if (newColumns()) lines.push(`Adds ${plural(newColumns(), "new column")} for Canvas assignments the gradebook did not have.`);
    // POSTING GRADES: what each column's choice or its category's rule does at this sync (the fold below says which and why).
    const switches = postingSteps("switch").length, posts = postingSteps("post").length, hides = postingSteps("hide").length;
    if (switches) lines.push(`Sets the Canvas assignments of ${plural(switches, "column")} to post by hand first, as ${switches === 1 ? "its" : "their"} category does.`);
    if (posts || hides) lines.push(`${[posts ? `Posts ${plural(posts, "column")}` : "", hides ? `hides ${plural(hides, "column")}` : ""].filter(Boolean).join(" and ")} in Canvas after sending, as ${posts + hides === 1 ? "its" : "their"} choice or category says.`.replace(/^h/, "H"));
    const waiting = held.filter(h => !picks[h.key]).length;
    if (waiting) lines.push(`${plural(waiting, "grade")} changed in Canvas since Faculty last knew ${waiting === 1 ? "it waits" : "them wait"} for your choice below; until you choose, both sides keep ${waiting === 1 ? "it" : "them"} as they are.`);
    if (o.needs) lines.push(`${plural(o.needs, "Canvas assignment")} need${o.needs === 1 ? "s" : ""} your choice of column; until you choose, ${o.needs === 1 ? "it is" : "they are"} left out.`);
    if (!lines.length) lines.push("Faculty and Canvas already agree.");
    return `<div class="callout gbd-note gbd-summary" data-tone="${o.needs || waiting ? "warning" : "info"}"><p><strong>When you press Sync</strong></p><ul>${lines.map(line => `<li>${e(line)}</li>`).join("")}</ul></div>`;
  }
  function syncWarningsMarkup() {
    // DESIGN-LANGUAGE §1.10: what students see at once, and grades taken out of Canvas, are warnings whose title says so.
    const clears = sentColumns().reduce((n, column) => n + (column.clears || 0), 0);
    const seen = sentColumns().filter(column => column.to_send && column.assignment?.automatic).length;
    const shown = publishing().map(itemId => `“${(whole?.columns || []).find(column => column.item_id === itemId)?.title || ""}”`);
    // POSTING GRADES: each column's choice or its category's rule, carried out after the send (posting_plan, the server's).
    const posts = postingSteps("post"), hides = postingSteps("hide");
    const lines = [clears ? `Takes ${plural(clears, "grade")} out in Canvas: ${clears === 1 ? "it was" : "they were"} emptied in Faculty.` : "",
      shown.length ? `Students see ${shown.join(", ")} in Canvas once ${shown.length === 1 ? "it is" : "they are"} published.` : "",
      seen ? `Students see the grades of ${plural(seen, "assignment")} as soon as they are sent: Canvas posts ${seen === 1 ? "it" : "them"} automatically.` : "",
      posts.length ? `Posts ${posts.map(entry => `“${entry.title}”`).join(", ")} in Canvas after sending: students then see ${posts.length === 1 ? "its" : "their"} grades.` : "",
      hides.length ? `Hides ${hides.map(entry => `“${entry.title}”`).join(", ")} in Canvas: students stop seeing ${hides.length === 1 ? "its" : "their"} grades.` : ""].filter(Boolean);
    return lines.length ? `<div class="callout gbd-note" data-tone="warning"><p><strong>${e(seen || posts.length ? "Students see these grades" : shown.length ? "Students see these assignments" : hides.length ? "Students stop seeing these grades" : "Grades are taken out in Canvas")}</strong></p><ul>${lines.map(line => `<li>${e(line)}</li>`).join("")}</ul></div>` : "";
  }
  function heldMarkup() {
    const list = whole?.held || [];
    if (!list.length) return "";
    const rows = list.map(h => `<tr><td>${e(h.name)}</td><td>${e(h.section ?? "")}</td><td>${e(h.title)}</td><td>${e(valueText(h.canvas))}</td><td>${e(valueText(h.faculty))}</td>
      <td><select data-held="${e(h.key)}" aria-label="${e(`${h.name}, ${h.title}`)}"><option value="" ${picks[h.key] ? "" : "selected"}>Decide later</option><option value="faculty" ${picks[h.key] === "faculty" ? "selected" : ""}>Keep Faculty’s</option>${h.take ? `<option value="canvas" ${picks[h.key] === "canvas" ? "selected" : ""}>Take Canvas’s</option>` : ""}</select></td></tr>`);
    return `<section class="gbd-held" aria-labelledby="gbd-held-title"><h3 id="gbd-held-title">Changed in Canvas: your choice</h3>
      <p class="supporting-copy">Someone changed these in Canvas since Faculty last knew them. Keep Faculty’s sends Faculty’s grade; Take Canvas’s writes Canvas’s grade into Faculty.${list.some(h => !h.take) ? " A column Faculty grades itself keeps its grades: change them in grading." : ""}</p>
      <div class="gbd-toolbar"><button type="button" class="text-button" data-held-all="faculty">Keep Faculty’s for all</button><button type="button" class="text-button" data-held-all="">Decide all later</button></div>
      ${tableMarkup(e, ["Student", "Section", "Column", "In Canvas", "In Faculty", "Choice"], rows, "Grades changed in Canvas")}</section>`;
  }
  function coursesMarkup() {
    const list = plan.courses || [];
    if (list.length < 2) return "";
    const ticked = new Set(courses || list.map(entry => entry.id));
    return `<fieldset class="gbd-courses"><legend>Write grades to these Canvas courses</legend>${list.map(entry => `<label class="checkbox-field"><input type="checkbox" data-sync-course="${e(entry.id)}" ${ticked.has(entry.id) ? "checked" : ""}>${e(entry.name)}${entry.sections?.length ? ` <small>Section ${e(entry.sections.join(", "))}</small>` : ""}</label>`).join("")}
      ${canvasView.sent_before ? "" : '<p class="field-hint">Faculty has not written grades to Canvas yet: sync one section first and check it in Canvas.</p>'}</fieldset>`;
  }
  function toCanvasMarkup() {
    const columns = whole?.columns || [], several = (plan.courses || []).length > 1;
    const facts = column => [column.counts.add ? `${column.counts.add} new` : "", column.counts.update ? `${column.counts.update} changed` : "", column.counts.clear ? `${column.counts.clear} taken out` : "",
      column.counts.held ? `${column.counts.held} held` : "", column.counts.same ? `${column.counts.same} already the same` : "", column.counts.not_in_canvas ? `${column.counts.not_in_canvas} not in Canvas` : "",
      column.counts.waiting ? `${column.counts.waiting} pending review` : "", column.assignment?.new && column.to_send ? "created in Canvas first" : "",
      // ZEROS GO TO CANVAS (requirement, 2026-10-09): the grid's 0s for empty cells; a column Canvas grades gets only those, in its blanks.
      column.zeros ? `${plural(column.zeros, "empty cell")} counted as 0` : "", column.zeros_only ? "Canvas grades it: only blanks get 0" : "",
      column.publish && column.to_send ? "published in Canvas first" : ""].filter(Boolean).join(" · ");
    const going = columns.filter(column => column.send && !leftOff(column) && (column.to_send || column.counts.held));
    // Only what has grades waiting: a future assignment not published yet is not a problem today.
    const blocked = [...columns.filter(column => column.problems.length && (column.to_send || column.counts.held || column.fills || column.counts.blocked)),
      ...columns.filter(column => offered(column) && leftOff(column)).map(column => ({ ...column, problems: [`“${column.assignment?.name || column.title}” is not published in Canvas: tick it under Publish in Canvas to send its grades`] }))];
    const later = columns.filter(column => !column.ticked && !column.problems.length && (column.to_send || column.counts.held));
    const row = column => `<tr><td><strong>${e(column.title)}</strong>${several ? `<br><small>${e(column.course)}</small>` : ""}</td><td>${e(column.assignment?.name || "")}</td><td class="gbd-reason">${e(facts(column) || "nothing")}</td></tr>`;
    // POSTING GRADES (requirement, 2026-10-08): what this sync does about posting, from each column's choice or its category's rule;
    // changed from the column's menu, never here.
    const postingPlan = whole?.posting_plan || [];
    const posting = postingPlan.length ? fold("posting", "Posting in Canvas", postingPlan.filter(entry => entry.courses).length, tableMarkup(e, ["Column", "Sync", "Why"], postingPlan.map(entry =>
      `<tr><td><strong>${e(entry.title)}</strong></td><td>${e(postingDoes(entry))}</td><td class="gbd-reason">${e([POSTING_WHY[entry.why] || "", ...(entry.waits || [])].filter(Boolean).join("; "))}</td></tr>`),
    "What Sync does about posting in Canvas")) : "";
    // PUBLISH FROM SYNC: each column whose Canvas assignment is not published, ticked by default; Sync publishes it in each course first.
    const unpublished = [...new Map(columns.filter(offered).map(column => [column.item_id, column])).values()].map(column => ({ column,
      courses: columns.filter(other => other.item_id === column.item_id && offered(other)).length }));
    const publishMarkup = unpublished.length ? `<fieldset class="gbd-courses" data-publish-list><legend>Publish in Canvas, then send</legend>
      ${unpublished.map(({ column, courses: count }) => `<label class="checkbox-field"><input type="checkbox" data-publish="${e(column.item_id)}" ${publishOff.has(column.item_id) ? "" : "checked"}>${e(column.title)} <small>${e(`not published in Canvas${several ? ` · ${plural(count, "course")}` : ""}`)}</small></label>`).join("")}
      <p class="field-hint">Students see the assignment once it is published. Unticked, it stays unpublished and its grades wait.</p></fieldset>` : "";
    return `${fold("to-canvas", "To Canvas", plural(toCanvas(), "grade"), going.length ? tableMarkup(e, ["Column", "Canvas assignment", "Grades"], going.map(row), "What Sync sends to Canvas") : "")}${publishMarkup}${posting}
      ${fold("not-ticked", "Not this time", later.length, later.length ? `<p class="supporting-copy">Courses not ticked above: their grades wait for the next sync.</p>${tableMarkup(e, ["Column", "Canvas assignment", "Grades"], later.map(row), "Not synced this time")}` : "")}
      ${fold("cannot", "Can’t sync", blocked.length, blocked.length ? `<ul class="gbd-warnings">${blocked.map(column => `<li><strong>${e(column.title)}</strong>${several ? ` <small>${e(column.course)}</small>` : ""}: ${e(column.problems.join("; "))}</li>`).join("")}</ul>` : "")}`;
  }

  function render() {
    if (!session()) return;
    if (sending) return;
    if (!plan || course.choosing) {
      host.innerHTML = course.choosing ? courseMarkup() : loading ? `<div class="gbd-busy" role="status"><span class="gbd-spinner" aria-hidden="true"></span><p><strong>Reading ${e(src.name)}…</strong></p></div>` : notice ? `<div class="callout gbd-note" data-tone="danger"><p>${e(notice)}</p></div>` : "";
      sync();
      return;
    }
    const counts = plan.counts || {}, actions = ["add", "update", "same", "kept", "differs", "faculty_only"].filter(key => counts[key]);
    const titles = plan.columns.filter(column => plan.changes.some(change => change.column === column.key));
    const differing = plan.changes.filter(change => change.mismatch).length;
    if (filter.action !== "all" && filter.action !== "change" && !actions.includes(filter.action) && !(filter.action === "mismatch" && differing)) filter.action = "all";
    if (filter.column !== "all" && !titles.some(column => column.key === filter.column)) filter.column = "all";
    const facts = [counts.unposted ? `${plural(counts.unposted, "grade")} not yet posted to students in Canvas` : "", counts.waiting ? `${plural(counts.waiting, "submission")} pending review in Canvas` : "",
      counts.not_linked ? `${plural(counts.not_linked, "student")} in sections without a ${src.name} course` : "", counts.dropped ? `${plural(counts.dropped, "dropped student")} left alone` : "",
      counts.no_id && syncing ? `${plural(counts.no_id, "Canvas student")} without a university ID` : "",
      counts.not_polls ? `${plural(counts.not_polls, "session")} not imported (only polls are)` : ""].filter(Boolean);
    const changesBody = `<div class="gbd-toolbar"><label class="gbd-filter">Show<select data-filter-action>${writes() ? `<option value="change" ${filter.action === "change" ? "selected" : ""}>What Import writes (${e(writes())})</option>` : ""}<option value="all" ${filter.action === "all" || (filter.action === "change" && !writes()) ? "selected" : ""}>Everything compared (${e(plan.changes.length)})</option>${differing ? `<option value="mismatch" ${filter.action === "mismatch" ? "selected" : ""}>Grades that differ (${e(differing)})</option>` : ""}${actions.map(key => `<option value="${e(key)}" ${filter.action === key ? "selected" : ""}>${e(ACTION[key][0])} (${e(counts[key])})</option>`).join("")}</select></label>
        ${titles.length > 1 ? `<label class="gbd-filter">${kind === "iclicker" ? "Session" : "Assignment"}<select data-filter-column><option value="all">${kind === "iclicker" ? "Every session" : "Every assignment"}</option>${titles.map(column => `<option value="${e(column.key)}" ${filter.column === column.key ? "selected" : ""}>${e(column.title)}</option>`).join("")}</select></label>` : ""}</div>
      <div class="gbd-table" data-rows>${changesMarkup()}</div>`;
    const unreadable = plan.unreadable?.length ? `<details class="disclosure gbd-details" data-keep="unreadable"><summary><span>${e(plural(plan.unreadable.length, "value"))} Faculty could not read</span></summary><div class="disclosure-body">${tableMarkup(e, ["Student", src.noun.replace(/^./, c => c.toUpperCase()), "Value"], plan.unreadable.map(row => `<tr><td>${e(row.name)}</td><td>${e(plan.columns.find(column => column.key === row.column)?.title || row.column)}</td><td>${e(row.raw)}</td></tr>`), "Unreadable values")}</div></details>` : "";
    keep(host, () => { host.innerHTML = `${loading ? `<div class="gbd-busy" role="status"><span class="gbd-spinner" aria-hidden="true"></span><p><strong>Updating the preview…</strong></p></div>` : ""}
      ${notice ? `<div class="callout gbd-note" data-tone="warning" role="status"><p>${e(notice)}</p></div>` : ""}
      <p class="gbd-meta">Reads ${(plan.courses || []).map(entry => `<strong>${e(entry.name)}</strong>${entry.sections?.length ? ` (Section ${e(entry.sections.join(", "))})` : ""}`).join(" · ")}${kind === "iclicker" && ctx.scopeSection ? ` <button type="button" class="text-button" data-course-change>Change course…</button>` : ""}</p>
      ${syncing ? `${syncSummaryMarkup()}${syncWarningsMarkup()}${heldMarkup()}${coursesMarkup()}` : summaryMarkup()}
      ${mappingMarkup()}
      ${unlinkedMarkup()}
      ${syncing ? toCanvasMarkup() : ""}
      ${src.missing ? `<label class="checkbox-field gbd-option"><input type="checkbox" data-missing ${options.missing_as_missing ? "checked" : ""}>${e(src.missing)}</label>` : ""}
      ${people(plan.not_in_canvas, `Students not found in their section’s ${src.name} course`, row => `Section ${row.section}`)}
      ${people(plan.only_canvas, `${src.name} students on no roster here`, row => row.course)}
      ${people(plan.unmatched, "iClicker students Faculty could not match (no UIN or roster email)", row => row.course)}
      ${unreadable}
      ${fold("notes", "Notes", plural((plan.warnings || []).length, "note"), warningsMarkup(e, plan.warnings))}
      ${fold("counts", "Counts", "", `${countsMarkup(e, counts, syncing ? ["add", "update", "clear", "same"] : ["add", "update", "same", "kept", ...(counts.differs ? ["differs"] : [])], syncing ? "What Sync takes from Canvas" : "What this import would do")}${facts.length ? `<p class="gbd-meta">${facts.map(e).join(" · ")}</p>` : ""}`)}
      ${fold("changes", syncing ? "Review what comes from Canvas" : "Review every change", writes(), changesBody)}`; });
    sync();
  }

  async function signIn() {
    // The sign-in window takes the dialog; once signed in, this import opens again from the start (Canvas only: iClicker signs in
    // with the saved sign-in and never answers 401).
    const who = await openCanvasSignIn({ api: ctx.api, showDialog: ctx.showDialog, dialogHeader: ctx.dialogHeader, escape: ctx.escape });
    if (who !== null) void openSourceImport(ctx, kind);
  }

  // The section's iClicker course (iclicker/service.py): chosen here once, remembered for the section, changed with "Change course…".
  const iclickerRoute = () => `/api/iclicker/sections/${encodeURIComponent(ctx.scopeSection)}`;
  function courseMarkup() {
    if (course.loading) return `<div class="gbd-busy" role="status"><span class="gbd-spinner" aria-hidden="true"></span><p><strong>Reading your iClicker courses…</strong></p></div>`;
    const current = course.linked?.course_id || "";
    const chosen = current || course.proposed;
    const choices = course.list.map(c => `<option value="${e(c.id)}" ${c.id === chosen ? "selected" : ""}>${e(c.name)}${c.term ? ` · ${e(c.term)}` : ""}${c.archived ? " (archived)" : ""}</option>`).join("");
    return `${course.error ? `<div class="callout gbd-note" data-tone="danger"><p>${e(course.error)}</p></div>` : ""}
      <p>${current ? "Which iClicker course holds this section’s sessions?" : "Choose the iClicker course that holds this section’s sessions. Faculty remembers it for the section."}</p>
      ${course.list.length ? `<label class="gbd-filter">iClicker course<select data-course-pick><option value="">Choose a course…</option>${choices}</select></label>
      ${course.proposed && !current ? `<p class="field-hint">Proposed from the course and section numbers in its name.</p>` : ""}
      <p><button type="button" class="button" data-course-use>Use this course</button>${current ? ` <button type="button" class="text-button" data-course-cancel>Keep ${e(course.linked.name)}</button>` : ""}</p>`
        : course.error ? "" : `<p class="gbd-empty-note">iClicker lists no courses for you.</p>`}`;
  }
  async function chooseCourse() {
    course.choosing = true; course.loading = true; course.error = "";
    render();
    try {
      const found = await ctx.api(`${iclickerRoute()}/courses`, "GET", undefined, {}, LONG);
      if (!session()) return;
      course.list = found.courses || []; course.proposed = found.proposed || ""; course.linked = found.linked || null;
    } catch (cause) {
      if (!session()) return;
      course.error = failure(cause).message;
    } finally {
      course.loading = false;
      if (session()) render();
    }
  }
  async function useCourse(courseId) {
    if (!courseId) { course.error = "Choose a course."; render(); return; }
    course.loading = true; course.error = "";
    render();
    try {
      const saved = await ctx.api(iclickerRoute(), "POST", { course_id: courseId, base: course.linked?.course_id || "" });
      if (!session()) return;
      course.linked = saved.linked; course.choosing = false; course.loading = false;
      plan = null;
      await preview();
    } catch (cause) {
      if (!session()) return;
      course.loading = false; course.error = failure(cause).message;
      render();
    }
  }

  async function preview({ keepNotice = false } = {}) {
    if (!session()) return;
    clearTimeout(timer);
    const token = ++generation;
    loading = true; editing = false;
    if (!keepNotice) notice = "";
    render();
    try {
      // A sync covers every linked course (the ticks choose only which are written to); an import, the section's.
      const body = syncing ? { options, ...(courses ? { courses } : {}) } : { options, ...scope(ctx) };
      const result = await ctx.api(url(ctx, `${src.route}/preview`), "POST", body, {}, LONG);
      if (token !== generation || !session()) return;
      if (syncing) {
        whole = result;
        plan = { ...result.from_canvas, fingerprint: result.fingerprint, ready: result.ready, options: result.options, courses: result.courses };
        const held = new Set((result.held || []).map(h => h.key));
        picks = Object.fromEntries(Object.entries(picks).filter(([key]) => held.has(key)));
      } else plan = result;
      options = { mapping: Object.fromEntries(plan.columns.map(column => [column.key, column.mapping])), replace: [...(plan.options?.replace || [])], missing_as_missing: !!plan.options?.missing_as_missing };
      requestId = "";
    } catch (cause) {
      if (token !== generation || !session()) return;
      if (failure(cause).status === 401 && src.signIn) { loading = false; void signIn(); return; }
      notice = failure(cause).message;
      if (plan) plan = { ...plan, fingerprint: "" };
    } finally {
      if (token === generation) { loading = false; render(); }
    }
  }
  const editingPreview = () => { ++generation; clearTimeout(timer); loading = false; editing = true; sync(); };
  const later = () => { editingPreview(); timer = window.setTimeout(() => void preview(), 350); };

  host.addEventListener("change", event => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    const mapping = { ...(options.mapping || {}) };
    if (target.matches("[data-mapping]")) {
      const key = target.dataset.mapping || "", column = plan.columns.find(entry => entry.key === key), value = target.value;
      const existing = (setup().categories || []).find(c => c.name.toLowerCase() === String(column?.group || "").toLowerCase());
      mapping[key] = value === "skip" ? { action: "skip" } : value === "new"
        ? { action: "new", title: String(column?.title || "").trim().slice(0, 120), category_id: existing?.id || null, category_name: existing ? null : column?.group || null,
            points: column?.points && Number(column.points) > 0 ? column.points : null }
        : { action: "item", item_id: value.slice(5) };
      options = { ...options, mapping };
      decided.add(key);
      void preview();
    } else if (target.matches("[data-new-title], [data-new-points], [data-new-category]")) {
      const key = target.dataset.newTitle || target.dataset.newPoints || target.dataset.newCategory || "";
      const choice = { ...(mapping[key] || { action: "new" }) };
      if (target.matches("[data-new-title]")) choice.title = target.value.trim();
      else if (target.matches("[data-new-points]")) choice.points = target.value.trim() || null;
      else if (target.value.startsWith("new:")) { choice.category_id = null; choice.category_name = target.value.slice(4); }
      else { choice.category_id = target.value || null; choice.category_name = null; }
      mapping[key] = choice;
      options = { ...options, mapping };
      if (target.matches("[data-new-title]") && !choice.title) { editingPreview(); editing = false; sync(); return; }
      void preview();
    } else if (target.matches("[data-new-category-all]")) {
      // One category for every new column (iClicker: dozens of sessions), then the preview is read again.
      if (!target.value) return;
      for (const column of plan?.columns || []) {
        const choice = mapping[column.key] || column.mapping;
        if (choice?.action === "new") mapping[column.key] = { ...choice, category_id: target.value === "none" ? null : target.value, category_name: null };
      }
      options = { ...options, mapping };
      void preview();
    } else if (target.matches("[data-missing]")) {
      options = { ...options, missing_as_missing: target.checked };
      void preview();
    } else if (target.matches("[data-held]")) {
      // A pick needs no new summary: Sync checks it against the held grades it read.
      const key = target.dataset.held || "";
      if (target.value) picks = { ...picks, [key]: target.value }; else { picks = { ...picks }; delete picks[key]; }
      requestId = "";
      render();
    } else if (target.matches("[data-publish]")) {
      // Part of what Sync is asked: a new request ID, no new summary.
      const itemId = target.dataset.publish || "";
      publishOff = new Set(publishOff);
      if (target.checked) publishOff.delete(itemId); else publishOff.add(itemId);
      requestId = "";
      render();
    } else if (target.matches("[data-sync-course]")) {
      const all = (plan?.courses || []).map(entry => entry.id), ticked = new Set(courses || all);
      if (target.checked) ticked.add(target.dataset.syncCourse || ""); else ticked.delete(target.dataset.syncCourse || "");
      if (!ticked.size) { target.checked = true; return; }
      courses = ticked.size === all.length ? null : all.filter(id => ticked.has(id));
      void preview();
    } else if (target.matches("[data-replace]")) {
      const replace = new Set(options.replace || []);
      if (target.checked) replace.add(target.dataset.replace || ""); else replace.delete(target.dataset.replace || "");
      options = { ...options, replace: [...replace] };
      later();
    } else if (target.matches("[data-filter-action], [data-filter-column]")) {
      if (target.matches("[data-filter-action]")) filter.action = target.value; else filter.column = target.value;
      /** @type {HTMLElement} */ (host.querySelector("[data-rows]")).innerHTML = changesMarkup();
    }
  });
  // A group the instructor opens or folds stays that way through the preview being read again (`toggle` does not bubble).
  host.addEventListener("toggle", event => {
    const node = event.target;
    if (node instanceof HTMLDetailsElement && node.dataset.keep?.startsWith("group-")) groupOpen.set(node.dataset.keep.slice(6), node.open);
  }, true);
  host.addEventListener("input", event => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (target.matches("[data-new-title], [data-new-points]")) editingPreview();
    if (target.matches("[data-link-search]")) {
      // Searching redraws only this column's results, so typing keeps its place and focus.
      const key = target.dataset.linkSearch || "";
      searches.set(key, target.value);
      const [itemId, courseId] = key.split("|");
      const slot = host.querySelector(`[data-link-results="${CSS.escape(key)}"]`);
      if (slot) slot.innerHTML = linkResults(itemId, courseId);
    }
  });
  host.addEventListener("click", event => {
    const pick = /** @type {HTMLElement} */ (event.target).closest("[data-course-change], [data-course-use], [data-course-cancel]");
    if (pick) {
      if (pick.hasAttribute("data-course-change")) void chooseCourse();
      else if (pick.hasAttribute("data-course-cancel")) { course.choosing = false; course.error = ""; render(); }
      else void useCourse(/** @type {HTMLSelectElement|null} */ (host.querySelector("[data-course-pick]"))?.value || "");
      return;
    }
    const edit = /** @type {HTMLElement} */ (event.target).closest("[data-row-edit]");
    if (edit && plan) {
      const key = edit.getAttribute("data-row-edit") || "";
      if (openRows.has(key)) openRows.delete(key); else openRows.add(key);
      render();
      return;
    }
    const all = /** @type {HTMLElement} */ (event.target).closest("[data-held-all]");
    if (all && whole) {
      const value = all.getAttribute("data-held-all") || "";
      picks = value ? Object.fromEntries(whole.held.map(h => [h.key, value])) : {};
      requestId = "";
      render();
      return;
    }
    const button = /** @type {HTMLElement} */ (event.target).closest("[data-link-choose], [data-check-show]");
    if (!button || !plan) return;
    if (button.hasAttribute("data-check-show")) {
      filter.action = "mismatch"; filter.column = button.dataset.checkShow || "all";
      render();
      const changes = /** @type {HTMLDetailsElement | null} */ (host.querySelector('[data-keep="changes"]'));
      if (changes) changes.open = true;
      host.querySelector("[data-rows]")?.scrollIntoView({block: "start", behavior: "smooth"});
      return;
    }
    // Linking a column to the Canvas assignment chosen: that assignment goes to the column (wherever it went before), then the
    // preview is read again with the grade check for the new match.
    options = { ...options, mapping: { ...(options.mapping || {}), [button.dataset.linkChoose || ""]: { action: "item", item_id: button.dataset.linkItem || "" } } };
    decided.add(button.dataset.linkChoose || "");
    searches.clear();
    void preview();
  });

  form.onsubmit = event => {
    event.preventDefault();
    if (!plan || !plan.fingerprint || loading || editing || reasons().length) return;
    const count = writes(), created = newColumns();
    void runFormAction(form, async () => {
      requestId ||= ctx.requestId();
      try {
        if (syncing) {
          // `section_id` only picks the gradebook the answer carries: a section tab's, or one section's on the course Home (viewSection).
          const answer = ctx.scopeSection || ctx.viewSection || "";
          const asked = Date.now();
          let result;
          try {
            result = await ctx.api(url(ctx, src.route), "POST", { options: plan.options, fingerprint: plan.fingerprint, request_id: requestId, confirmation: "sync",
              held: picks, ...(courses ? { courses } : {}), ...(answer ? { section_id: answer } : {}),
              ...(publishing().length ? { publish: publishing() } : {}) }, {}, LONG);
          } catch (cause) {
            const failed = /** @type {any} */ (cause);
            if (!(failed?.unreadable || !failed?.status)) throw cause;
            result = await lostAnswer(requestId, answer, `${failed.unreadable ? `unreadable, HTTP ${failed.status}` : "no answer"} after ${Math.round((Date.now() - asked) / 1000)} s`);
            if (!result) return;
          }
          ctx.replaceView(result.gradebook);
          if (result.send && session()) { sending = result; watch(result.send); return; }
          if (session()) close();
          ctx.toast(syncWords(result.sync));
          return;
        }
        const result = await ctx.api(url(ctx, src.route), "POST", { options: plan.options, fingerprint: plan.fingerprint, request_id: requestId, ...scope(ctx) }, {}, LONG);
        ctx.replaceView(result);
        if (session()) close();
        ctx.toast(count || created ? `Imported ${plural(count, "score")} from ${src.name}${created ? ` · ${plural(created, "new column")}` : ""}` : `${src.name} matches saved`);
      } catch (cause) {
        const failed = failure(cause);
        if (failed.status === 401 && src.signIn && session()) { void signIn(); return; }
        if (failed.status !== 409 || !session()) throw cause;
        notice = syncing ? `${failed.message} Nothing was written. The summary below was read again from Canvas: check it, then sync.`
          : `${failed.message} Nothing was imported. The preview below was read again from ${src.name}: check it, then import.`;
        requestId = "";
        await preview({ keepNotice: true });
      }
    }).then(() => { if (session()) sync(); });
  };
  // SYNC WITH CANVAS, A LOST ANSWER (requirement, 2026-10-31): no answer in time, or one that was not JSON, says nothing of what Sync did. Faculty is
  // asked about the sync's request ID until it is recorded (its answer, as Sync's own), still working, or unknown (nothing was
  // written; the same request ID may Sync again and is answered once). Null when the dialog closed meanwhile.
  async function lostAnswer(id, answer, lost) {
    const route = url(ctx, `${src.route}/requests/${encodeURIComponent(id)}`);
    const until = Date.now() + 15 * 60000;
    let first = true;
    while (session()) {
      footnote.textContent = "Faculty did not get the sync’s answer back. Checking what it did…";
      let found = null;
      try {
        found = await ctx.api(`${route}?${new URLSearchParams({ ...(answer ? { section_id: answer } : {}), ...(first ? { lost } : {}) })}`);
        first = false;
      } catch (cause) {
        // Faculty's own refusal is the answer; no answer (a restart, the network) is asked again.
        const failed = /** @type {any} */ (cause);
        if (failed?.status && !failed.unreadable) throw cause;
      }
      if (found?.state === "recorded") return found;
      if (found?.state === "unknown") throw new Error("Faculty has no record of this sync, so nothing was written or sent. Press Sync to try again.");
      if (Date.now() > until) throw new Error("Faculty is still working on this sync. Close this and look at the gradebook in a few minutes.");
      await new Promise(resolve => window.setTimeout(resolve, 3000));
    }
    return null;
  }
  // What a sync did, in a sentence: taken from Canvas, held for later (the send's own progress says what Canvas took).
  function syncWords(done) {
    const parts = [done?.written ? `${plural(done.written, "grade")} taken from Canvas` : "", done?.new_columns ? plural(done.new_columns, "new column") : "",
      done?.held ? `${plural(done.held, "grade")} still held` : ""].filter(Boolean);
    return parts.length ? `Synced with Canvas: ${parts.join(" · ")}` : "Synced with Canvas";
  }
  // The send runs on after Sync is pressed: its progress here, read again until it ends; each value Canvas shows is recorded.
  function watch(sent) {
    if (!session()) return;
    const p = sent.progress || {};
    const words = sent.status === "running" ? `Sending to Canvas: ${plural(p.done_assignments || 0, "assignment")} of ${p.assignments || 0} done…`
      : sent.status === "done" && !p.total && !p.assignments ? (p.switched ? `${plural(p.switched, "assignment")} set to post by hand in Canvas.` : "Nothing to send to Canvas.")
      : sent.status === "done" ? `Sent. ${p.created ? `${plural(p.created, "assignment")} created in Canvas; ` : ""}${p.published ? `${plural(p.published, "assignment")} published in Canvas; ` : ""}${p.switched ? `${plural(p.switched, "assignment")} set to post by hand; ` : ""}${plural(p.accepted || 0, "grade")} now show${p.accepted === 1 ? "s" : ""} in Canvas${p.failed ? `; ${p.failed} did not` : ""}${p.uncertain ? `; ${p.uncertain} not confirmed yet (Canvas was still working: sync again later)` : ""}.`
      : `The send stopped: ${(p.errors || [])[0]?.message || sent.status}.`;
    // POSTING GRADES: the posts and hides after the send, once it is over.
    const titleOf = itemId => (ctx.view?.setup?.items || []).find(item => item.id === itemId)?.title || "A column";
    const postLines = (sent.posts || []).map(post => post.status === "running" ? `${post.action === "hide" ? "Hiding" : "Posting"} “${titleOf(post.item_id)}” in Canvas…`
      : post.status === "done" && (post.progress?.accepted || 0) ? (post.action === "hide" ? `Hid “${titleOf(post.item_id)}” in Canvas in ${plural(post.progress.accepted, "section")}.`
        : `Posted “${titleOf(post.item_id)}” in Canvas: students see its grades in ${plural(post.progress.accepted, "section")}.`)
      : `“${titleOf(post.item_id)}” was not ${post.action === "hide" ? "hidden" : "posted"}: ${(post.progress?.errors || [])[0]?.message || "Canvas did not confirm it; look in Canvas"}`);
    if (sent.posting && !postLines.length) postLines.push("Posting in Canvas…");
    // Whether the whole sync is over, said first (requirement, 2026-10-08): the
    // send and every post or hide after it.
    const posting = sent.posting || (sent.posts || []).some(post => post.status === "running");
    const finished = sent.status !== "running" && !posting;
    const stopped = finished && sent.status !== "done";
    const postProblems = (sent.posts || []).some(post => post.status !== "running" && (post.status !== "done" || post.progress?.failed || post.progress?.uncertain || (post.progress?.errors || []).length));
    const problems = stopped || p.failed || p.uncertain || postProblems;
    const headline = !finished ? (sent.status === "running" ? "Syncing with Canvas…" : "Posting in Canvas…")
      : stopped ? "The sync stopped" : problems ? "Sync finished, with problems below" : "Sync finished";
    const tone = !finished ? "info" : problems ? "warning" : "success";
    host.innerHTML = `<div class="callout gbd-note" data-tone="${tone}" role="status" data-sync-state="${finished ? (problems ? "problems" : "finished") : "running"}"><p><strong>${e(headline)}</strong></p><p>${e(words)}</p><p>${e(syncWords(sending?.sync))}.</p>${postLines.map(line => `<p>${e(line)}</p>`).join("")}</div>${(p.errors || []).length ? `<ul class="gbd-warnings">${p.errors.map(x => `<li><strong>${e(x.label)}</strong>: ${e(x.message)}</li>`).join("")}</ul>` : ""}`;
    submit.hidden = true;
    footnote.textContent = "";
    // The footer's button (the header's ✕ is a close button too, and comes first): closing never stops the sync.
    const cancel = form.querySelector('.gbd-footer [data-action="close"]');
    if (cancel) cancel.textContent = "Close";
    if (sent.status === "running" || sent.posting) {
      window.setTimeout(async () => {
        try { watch(await ctx.api(url(ctx, `/canvas-send/${encodeURIComponent(sent.id)}`))); } catch { window.setTimeout(() => watch(sent), 3000); }
      }, 1500);
    } else void ctx.reload?.();
  }
  if (kind === "iclicker" && ctx.scopeSection) {
    // The section's course comes first: a section without one chooses it before anything is previewed.
    loading = true; render();
    try {
      const found = await ctx.api(iclickerRoute());
      if (!session()) return;
      course.linked = found.linked || null;
      if (found.problem) { loading = false; notice = found.problem; render(); return; }
    } catch (cause) {
      if (!session()) return;
      loading = false; notice = failure(cause).message; render();
      return;
    }
    loading = false;
    if (!course.linked) { await chooseCourse(); return; }
  }
  await preview();
}

// ---------------------------------------------------------------------------------------------------------------------
// Confirmations the grid asks before one of its own score writes. The grid keeps the write (its request ID, its "saving" cells,
// an unknown outcome's Check now); `onConfirm` returns "" once it has sent it, or why it did not (shown here, nothing written).

function confirmDialog(ctx, { id, title, subtitle, body, label, onConfirm }) {
  ctx.showDialog(shell(ctx, { id, title, subtitle, body,
    footer: `<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${ctx.escape(label)}</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById(id));
  const session = dialogSession(form, dialogNode());
  const submit = /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]'));
  const error = /** @type {HTMLElement} */ (form.querySelector(".form-error"));
  form.onsubmit = event => {
    event.preventDefault();
    if (submit.disabled) return;
    const problem = onConfirm();
    if (problem) { error.textContent = problem; error.hidden = false; submit.disabled = true; return; }
    if (session()) close();
  };
  submit.focus();
  return { form, submit };
}

/** POSTING GRADES (docs/decisions/canvas.md; requirement, 2026-10-08): a column's standing choice, Post or Hide in every Canvas course it goes to, or back
 * to its category's rule. Nothing is asked of Canvas here: the next sync, the instructor's or the morning one, carries it out, and
 * the column's heading says so until then. The request ID is kept while the window is open, so a lost answer is sent again safely. */
export async function openPostingChoice(ctx, itemId, choice) {
  const e = ctx.escape;
  const item = (ctx.view?.setup?.items || []).find(entry => entry.id === itemId);
  if (!item) return;
  const mark = ctx.view?.canvas_posting?.[itemId] || {};
  const category = (ctx.view?.setup?.categories || []).find(entry => entry.id === item.category_id);
  const verb = { post: "Post in Canvas at each sync", hide: "Hide in Canvas at each sync", rule: "Back to the category’s posting" }[choice];
  const lead = choice === "post"
    ? [`Students see these grades from the next sync`, `The next Sync with Canvas, yours or the morning one at 5 AM, posts every grade of “${item.title}” in every section it goes to, and posts each grade that comes in later, until you choose otherwise.`]
    : choice === "hide"
      ? [`Students stop seeing these grades at the next sync`, `The next Sync with Canvas, yours or the morning one at 5 AM, hides every grade of “${item.title}” in every section it goes to, and keeps them hidden until you post them.`]
      : [`“${item.title}” follows ${category ? `“${category.name}”` : "its category"} again`, `From the next sync, ${postingRule({ ...mark, policy: { ...(mark.policy || {}), why: null, state: null } }) || "its category posts by hand"}. A grade already shown in Canvas stays shown.`];
  const now = [mark.hidden > 0 ? `${plural(mark.hidden, "grade")} hidden from students when Faculty last read Canvas (${when(mark.read_at)}).` : "",
    mark.policy ? `Now: ${postingRule(mark)}.` : ""].filter(Boolean);
  ctx.showDialog(shell(ctx, { id: "gradebook-posting-form", title: verb, subtitle: item.title,
    body: `<div class="callout gbd-note" data-tone="${choice === "rule" ? "info" : "warning"}"><p><strong>${e(lead[0])}</strong></p><p>${e(lead[1])}</p></div>
      ${now.map(line => `<p class="supporting-copy">${e(line)}</p>`).join("")}
      <p class="supporting-copy">Nothing changes in Canvas now. The column says what the next sync does until it is done.</p><div data-posting-error></div>`,
    footer: `<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary"${choice === "rule" ? "" : " data-outward"}>${e(verb)}</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-posting-form"));
  const session = dialogSession(form, dialogNode());
  let requestId = "";
  form.onsubmit = event => {
    event.preventDefault();
    void runFormAction(form, async () => {
      requestId ||= ctx.requestId();
      try {
        const result = await ctx.api(url(ctx, "/canvas-posting/choice"), "POST",
          { item_id: itemId, choice, base: mark.choice_id ?? null, request_id: requestId, confirmation: "choose", ...scope(ctx) });
        if (session()) close();
        ctx.replaceView(result);
        ctx.toast(choice === "rule" ? `“${item.title}” follows its category’s posting again.` : `The next sync ${choice === "post" ? "posts" : "hides"} “${item.title}” in Canvas.`);
      } catch (cause) {
        const failed = failure(cause);
        if (!session()) return;
        const host = form.querySelector("[data-posting-error]");
        if (host) host.innerHTML = `<div class="callout gbd-note" data-tone="${failed.status === 409 ? "warning" : "danger"}"><p>${e(failed.message)}</p></div>`;
        if (failed.status === 409) void ctx.reload?.();
      }
    });
  };
}

/** A paste that replaces values already there (or writes many cells): what it replaces, then the grid's one write. */
export function openPasteConfirm(ctx, { count, cleared = 0, replaced = [], onConfirm }) {
  const e = ctx.escape, shown = replaced.slice(0, 12);
  const becomes = to => to?.status === "cleared" ? "cleared" : valueText(to);
  const lead = replaced.length
    ? `This paste replaces ${plural(replaced.length, "value")} already in the gradebook${count > replaced.length ? ` and fills ${plural(count - replaced.length, "other cell")}` : ""}.`
    : `This paste writes ${plural(count, "cell")} at once.`;
  const body = `<p class="section-lead">${e(lead)}</p>
    ${shown.length ? `<ul class="gbd-add-list" aria-label="Values this paste replaces">${shown.map(entry => `<li><strong>${e(entry.name)}</strong><small>${e(`${entry.column}: ${valueText(entry.from)} → ${becomes(entry.to)}`)}</small></li>`).join("")}</ul>` : ""}
    ${replaced.length > shown.length ? `<p class="supporting-copy">And ${e(plural(replaced.length - shown.length, "more value"))}.</p>` : ""}
    <p class="supporting-copy">${cleared ? `${e(plural(cleared, "cell"))} left blank in the copy ${cleared === 1 ? "is" : "are"} cleared. ` : ""}Every earlier value stays in its cell’s history.</p>`;
  confirmDialog(ctx, { id: "gradebook-paste-form", title: replaced.length ? "Replace values?" : "Paste many cells?", subtitle: plural(count, "cell"),
    body, label: `Paste ${plural(count, "cell")}`, onConfirm });
}

/** "Mark N empty cells missing" / "Excuse N empty cells" of one column: students with a DR-center booking for its assignment
 * stay empty unless ticked; one write of exactly the cells listed. */
export function openFillEmpty(ctx, { item, status, cells, hidden = 0, onConfirm }) {
  const e = ctx.escape;
  const regular = cells.filter(cell => !cell.dr), booked = cells.filter(cell => cell.dr);
  const word = status === "missing" ? "missing (counts as 0)" : "excused (not counted)";
  const names = list => `<ul class="gbd-add-list">${list.map(cell => `<li><strong>${e(cell.name)}</strong><small>${e(`Section ${cell.section}`)}</small></li>`).join("")}</ul>`;
  const body = `<p class="section-lead">${e(`${plural(regular.length, "empty cell")} of ${item.title} ${regular.length === 1 ? "becomes" : "become"} ${word}.`)}</p>
    ${regular.length ? `<details class="disclosure gbd-details"><summary><span>Students</span><span class="disclosure-hint">${e(regular.length)}</span></summary><div class="disclosure-body">${names(regular)}</div></details>` : ""}
    ${booked.length ? `<fieldset class="gbd-flags"><legend>DR-center bookings</legend>
      <div class="callout gbd-note" data-tone="warning"><p>${e(`${plural(booked.length, "student")} with a DR-center booking for this assignment ${booked.length === 1 ? "stays" : "stay"} empty unless you tick below.`)}</p></div>
      ${names(booked)}<label class="checkbox-field"><input type="checkbox" name="include_dr">${e(`Also mark ${booked.length === 1 ? "this student" : `these ${booked.length} students`} ${status === "missing" ? "missing" : "excused"}`)}</label></fieldset>` : ""}
    <p class="supporting-copy">${e(hidden ? `Only the rows shown now; the cells of ${plural(hidden, "student")} hidden by the search or a filter are left alone.` : "Every student shown now (the section filter applies).")} Every change stays in its cell’s history.</p>`;
  const verb = status === "missing" ? "Mark" : "Excuse";
  const label = n => `${verb} ${plural(n, "cell")}${status === "missing" ? " missing" : ""}`;
  let include = false;
  const { form, submit } = confirmDialog(ctx, { id: "gradebook-fill-form", title: status === "missing" ? "Mark empty cells missing" : "Excuse empty cells",
    subtitle: item.title, body, label: label(regular.length),
    onConfirm: () => onConfirm([...regular, ...(include ? booked : [])].map(cell => cell.row)) });
  submit.disabled = !regular.length;
  form.querySelector('[name="include_dr"]')?.addEventListener("change", event => {
    include = /** @type {HTMLInputElement} */ (event.target).checked;
    const count = regular.length + (include ? booked.length : 0);
    submit.textContent = label(count);
    submit.disabled = !count;
  });
}

// ---------------------------------------------------------------------------------------------------------------------
// A student's record: everything the grid holds for one student, from the View on screen (nothing is read or written here).
// "Change" opens that cell's history, where a change is made.

/** The categories open in the record drawn last (view memory only): coming back to it keeps them; a fresh open folds them all. */
let recordOpen = { row: "", open: new Set() };

/** @param {any} ctx @param {string} row @param {{keep?: boolean}} [options] `keep`: coming back to the record from a window
 *  opened from it, with the categories that were open. */
export function openStudent(ctx, row, { keep = false } = {}) {
  const e = ctx.escape, view = ctx.view;
  const student = (view?.students || []).find(entry => entry.row === row);
  if (!student) { ctx.toast("This student is no longer in the gradebook.", true); return; }
  const result = view.results?.students?.[row] || {};
  const categories = view.setup?.categories || [];
  const percent = value => value === null || value === undefined || value === "" ? "—" : `${value}%`;
  const titleOf = id => (view.setup?.items || []).find(item => item.id === id)?.title || "";
  const assignmentTitle = id => (view.assignments || []).find(entry => entry.id === id)?.title || "an assignment";
  // The row is `section_id:enrollment_id` for every student (the View names enrollment_id only beside a photo).
  const [sectionId, enrollmentId] = String(row).split(":");
  // A gradebook without categories has nothing to fold its columns into: they open as one Uncategorized group.
  if (!keep || recordOpen.row !== row) recordOpen = { row, open: new Set(categories.length ? [] : [""]) };
  const open = recordOpen.open;
  // Every window opened from the record (a cell's history, the timeline, an absence, their mail) comes back to it.
  const back = { label: `Back to ${student.name}`, run: () => openStudent(ctx, row, { keep: true }) };
  // An absence saved on the way may have changed iClicker cells (ICLICKER-CREDIT): the View is read again (the grid redrawn) before
  // the record comes back, unless the instructor closed the window or opened another meanwhile.
  const absenceBack = { label: back.label, run: async (/** @type {{saved?: boolean}} */ { saved = false } = {}) => {
    const shown = dialogNode()?.firstElementChild;
    if (saved) { try { await ctx.reload?.(); } catch { /* the record shows the View on screen */ } }
    if (dialogNode()?.open && dialogNode()?.firstElementChild === shown) openStudent(ctx, row, { keep: true });
  } };
  const facts = `<dl class="gbd-counts gbd-record-facts" aria-label="Results"><div><dt>Overall</dt><dd>${e(percent(result.percent))}</dd></div><div><dt>Letter</dt><dd>${e(result.letter || "—")}</dd></div>
    <div><dt>Points</dt><dd>${e(result.earned !== null && result.earned !== undefined ? `${trimPoints(result.earned)} of ${trimPoints(result.possible)}` : "—")}</dd></div></dl>`;
  const support = [student.accommodations?.length ? `Accommodations: ${student.accommodations.join("; ")}.` : "",
    student.dr?.length ? `DR-center booking for ${student.dr.map(assignmentTitle).join(", ")}.` : ""].filter(Boolean);
  const columnRow = item => {
    const applies = !item.sections?.length || item.sections.includes(student.section_id);
    const cell = view.cells?.[item.id]?.[row];
    const booked = Boolean(item.assignment_id && student.dr?.includes(item.assignment_id));
    // ZERO ONCE DUE: the server's word on an empty cell (counted as 0, or why it waits).
    const waiting = { zero: "No score · counts as 0", makeup: "No score · makeup owed", if_excused: "No score · absence waiting for your decision",
      dr: "No score · DR booking", paper: "No score · paper in, not graded yet" }[cell ? "" : blankReason(view, item, student)];
    const value = !applies ? "Not assigned" : cell ? `${valueText(cell)}${cell.status === "scored" ? ` of ${trimPoints(item.points)}` : ""}` : waiting || (booked ? "No score · DR booking" : "—");
    const by = cell ? replacedBy(view, item, student) : null;
    const marks = [cell && droppedIn(view, item, student) ? "dropped" : "", by ? `replaced by ${titleOf(by)}` : "", item.replaces_lowest ? "replaces the lowest if higher" : "", item.counts === false ? "not counted" : "", item.extra_credit ? "extra credit" : ""].filter(Boolean);
    const from = cell ? [SOURCE[cell.source] || cell.source, when(cell.at)].filter(Boolean).join(" · ") : "";
    const change = applies ? `<button type="button" class="text-button" data-change="${e(item.id)}" aria-label="${e(`Change ${item.title}`)}">Change</button>` : "";
    // A grade recorded from grading leads to the paper it was given for (Certification and Export opens this student).
    const paper = gradedPaper(ctx, item.id, cell) ? `<button type="button" class="text-button" data-paper="${e(item.id)}" aria-label="${e(`See the graded paper of ${item.title}`)}">Paper</button>` : "";
    // `data-label` names each cell (gradebook-setup.css keeps the Due cell on one line).
    return `<tr${cell && (droppedIn(view, item, student) || by) ? ' class="is-dropped"' : ""}><td><strong>${e(item.title)}</strong></td>
      <td data-label="Score"><span class="gbd-value">${e(value)}</span>${marks.length ? `<br><small>${e(marks.join(" · "))}</small>` : ""}</td><td class="gbd-reason" data-label="From">${e(from)}</td><td class="gbd-reason" data-label="Note">${e(cell?.note || "")}</td><td data-label="Due">${e(shortDate(item.due_date))}</td><td>${change || paper ? `<span class="gbd-record-actions">${change}${paper}</span>` : ""}</td></tr>`;
  };
  // One row per category (requirement, 2026-10-06): its
  // percentage, how many columns and what was dropped; its button opens the columns beneath it. Columns without a category
  // close the table as Uncategorized.
  const bands = columnBands(view, "");
  const groups = [...categories.map(category => ({ key: category.id, category, items: bands.find(band => band.category?.id === category.id)?.items || [] })),
    ...bands.filter(band => !band.category).map(band => ({ key: "", category: null, items: band.items }))];
  const groupRows = (group, index) => {
    const own = group.category ? result.categories?.[group.category.id] || {} : {};
    const dropped = [...(own.dropped || []).map(titleOf), ...(own.replaced || []).map(entry => `${titleOf(entry.item)} (replaced by ${titleOf(entry.by)})`)].filter(Boolean);
    const name = group.category ? group.category.name : "Uncategorized";
    const summary = [group.items.length ? plural(group.items.length, "column") : "No columns", dropped.length ? `Dropped: ${dropped.join(", ")}` : ""].filter(Boolean).join(" · ");
    const id = `gbd-record-group-${index}`, opened = group.items.length > 0 && open.has(group.key);
    const head = group.items.length
      ? `<button type="button" class="gbd-record-fold" data-record-fold="${e(group.key)}" aria-expanded="${opened}" aria-controls="${id}"><span class="gbd-record-name">${e(name)}</span></button>`
      : `<span class="gbd-record-name">${e(name)}</span>`;
    return `<tbody><tr class="gbd-record-category"><th scope="rowgroup">${head}</th><td data-label="Score">${e(group.category ? percent(own.percent) : "")}</td><td class="gbd-reason" colspan="4">${e(summary)}</td></tr></tbody>
      ${group.items.length ? `<tbody class="gbd-record-items" id="${id}"${opened ? "" : " hidden"}>${group.items.map(columnRow).join("")}</tbody>` : ""}`;
  };
  const grades = `<div class="data-table" tabindex="0" role="region" aria-label="Grades"><table><thead><tr>${["Category", "Score", "From", "Note", "Due", "Actions"].map(heading => `<th scope="col">${e(heading)}</th>`).join("")}</tr></thead>${groups.map(groupRows).join("")}</table></div>`;
  const photo = student.photo && student.enrollment_id ? `<img class="student-photo" src="/api/student-photos/${encodeURIComponent(student.enrollment_id)}?v=${encodeURIComponent(student.photo)}" alt="Photo of ${e(student.name)}" width="128" height="128" />` : "";
  // Notes and absences come before the scores (rest-people, 2026-10-02: below 80 columns they began a long scroll down).
  const body = `${photo}${facts}${support.map(line => `<div class="callout gbd-note" data-tone="info"><p>${e(line)}</p></div>`).join("")}
    ${enrollmentId ? "<div data-dr-quiz-taker-host></div>" : ""}
    ${ctx.notes?.() && enrollmentId ? '<h3 class="gbd-heading">Notes</h3><div data-student-notes></div>' : ""}
    ${ctx.absences?.() ? '<h3 class="gbd-heading">Absences</h3><div data-absences-card></div>' : ""}
    <h3 class="gbd-heading">Grades</h3>${groups.length ? `<div class="gbd-record-table gbd-record-grades">${grades}</div>` : '<p class="gbd-empty-note">This gradebook has no columns yet.</p>'}`;
  const communications = ctx.communications && enrollmentId ? '<button type="button" class="button" data-communications>Communications…</button>' : "";
  // Their mail (Search Gmail now…, Write to them) through Mail, which reads their address from the section's roster.
  const groupId = view.term?.group_id || "";
  const mail = groupId && enrollmentId ? { group: groupId, section: student.section, studentId: enrollmentId, name: student.name, context: `Section ${student.section}`, back } : null;
  ctx.showDialog(shell(ctx, { id: "gradebook-student-form", wide: true, title: student.name, subtitle: `Section ${student.section}`, body,
    footer: `${ctx.studentSupport ? '<button type="button" class="button" data-student-support>Follow-up & office hours</button>' : ""}${communications}<button type="button" class="button" data-action="close">Close</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-student-form"));
  form.onsubmit = event => event.preventDefault();
  // Your notes about the student this term (student-notes.js), read when the record opens and changed in place.
  const notesHost = /** @type {HTMLElement | null} */ (form.querySelector("[data-student-notes]"));
  if (notesHost) void ctx.notes?.()?.panel(notesHost, { enrollmentId, name: student.name });
  const takerHost = /** @type {HTMLElement | null} */ (form.querySelector("[data-dr-quiz-taker-host]"));  // ACCOMMODATIONS "DR QUIZ TAKERS"
  const drawTaker = answer => { if (takerHost) takerHost.innerHTML = takerMarkup(enrollmentId, answer.dr_quiz_taker, e, answer.offered); };
  const readTaker = () => ctx.api(`/api/accommodations/dr-quiz-takers/${encodeURIComponent(enrollmentId)}`).then(drawTaker, () => { if (takerHost) takerHost.innerHTML = ""; });
  if (takerHost) void readTaker();
  takerHost?.addEventListener("change", event => void saveTaker(ctx.api, enrollmentId, /** @type {HTMLInputElement} */ (event.target).checked)
    .then(drawTaker, error => { ctx.toast(error.message, true); void readTaker(); }));
  // The student's absences in this section (absences.js), read when the record opens.
  const absencesHost = /** @type {HTMLElement | null} */ (form.querySelector("[data-absences-card]"));
  if (absencesHost) void ctx.absences?.()?.panel(absencesHost, { sectionId, enrollmentId, name: student.name, section: student.section, back: absenceBack });
  form.addEventListener("click", event => {
    const target = /** @type {HTMLElement} */ (event.target);
    // A category's whole row opens or folds its columns (its button is the keyboard's way to it).
    const fold = target.closest(".gbd-record-category")?.querySelector("[data-record-fold]");
    const items = fold && document.getElementById(fold.getAttribute("aria-controls") || "");
    if (fold && items) {
      const opened = fold.getAttribute("aria-expanded") !== "true", key = fold.getAttribute("data-record-fold") || "";
      fold.setAttribute("aria-expanded", String(opened));
      items.hidden = !opened;
      if (opened) open.add(key); else open.delete(key);
    }
    const button = target.closest("[data-change]");
    if (button) openHistory(ctx, button.getAttribute("data-change") || "", row, { back });
    const paper = target.closest("[data-paper]");
    if (paper) seePaper(ctx, paper.getAttribute("data-paper") || "", row);
    if (target.closest("[data-communications]")) ctx.communications?.({ enrollmentId, name: student.name, context: `Section ${student.section}`, back, mail });
    if (target.closest("[data-student-support]")) ctx.studentSupport?.({enrollmentId, name: student.name});
  });
  /** @type {HTMLElement | null} */ (form.querySelector('[data-action="close"]'))?.focus();
}

/** The grading plan whose certified paper a cell's grade came from, or "": the column links grading and the cell's value was
 * recorded from it (not typed or imported over it). @param {any} ctx @param {string} itemId @param {any} cell */
function gradedPaper(ctx, itemId, cell) {
  const plan = ctx.view?.links?.[itemId]?.plan_id;
  return plan && cell?.source === "grading" ? String(plan) : "";
}

/** Certification and Export of the cell's grading plan, opening this student's drawer there: the row goes in memory (handoff.js),
 * never in the address. @param {any} ctx @param {string} itemId @param {string} row */
function seePaper(ctx, itemId, row) {
  const plan = String(ctx.view?.links?.[itemId]?.plan_id || "");
  if (!plan) return;
  give({ plan, row });
  close();
  location.hash = `#grading/${encodeURIComponent(plan)}?tab=export`;
}

// ---------------------------------------------------------------------------------------------------------------------
// One cell's history, newest first, and a small form to change it (a scores write naming the cell's current revision).

/** @param {any} ctx @param {string} itemId @param {string} row @param {{back?: {label: string, run: () => void} | null}} [options]
 *  `back`: the student's record this was opened from, offered in the footer and returned to after a save. */
export function openHistory(ctx, itemId, row, { back = null } = {}) {
  const e = ctx.escape;
  let view = ctx.view;
  const item = (view.setup?.items || []).find(entry => entry.id === itemId);
  const student = (view.students || []).find(entry => entry.row === row);
  if (!item || !student) { ctx.toast("This cell is no longer in the gradebook.", true); return; }
  const applies = !item.sections?.length || item.sections.includes(student.section_id);
  const writable = applies && !item.archived && !canvasGraded(item);
  const body = `<div data-history aria-live="polite"><p class="loading-state">Loading the history…</p></div>
    <fieldset class="gbd-cell" ${writable ? "" : "disabled"}><legend>Change this cell</legend>
      ${!applies ? `<div class="callout gbd-note" data-tone="info"><p>This column does not apply to section ${e(student.section)}.</p></div>` : item.archived ? `<div class="callout gbd-note" data-tone="info"><p>This column is archived. Restore it in the setup before changing its scores.</p></div>` : canvasGraded(item) ? `<div class="callout gbd-note" data-tone="info"><p>${item.canvas === "quiz" ? "Canvas" : "WebAssign"} grades this column. Change its scores in ${item.canvas === "quiz" ? "Canvas" : "WebAssign"}.</p></div>` : ""}
      <div class="gbd-pair"><label class="field">Value<select name="status">${Object.entries(STATUS).map(([key, label]) => `<option value="${key}">${e(label)}</option>`).join("")}</select></label>
        <label class="field" data-points>Points <small>of ${e(item.points)}</small><input name="points" type="number" min="0" max="100000" step="any" inputmode="decimal"></label></div>
      <label class="field">Note <span class="field-optional">optional</span><textarea name="note" maxlength="1000" rows="2"></textarea></label>
      <p class="field-hint">Excused is not counted; missing counts as zero; Clear empties the cell (and its note). Every change is kept in this history.</p></fieldset>
    <div class="callout gbd-conflict" data-tone="warning" data-conflict hidden></div>`;
  const paper = gradedPaper(ctx, itemId, view.cells?.[itemId]?.[row]);
  ctx.showDialog(shell(ctx, { id: "gradebook-history-form", title: student.name, subtitle: `${item.title} · Section ${student.section}`, body,
    footer: `${back ? `<button type="button" class="button" data-dialog-back>${e(back.label)}</button>` : ""}${paper ? '<button type="button" class="button" data-paper>See the graded paper</button>' : ""}<button type="button" class="button" data-action="close">Close</button><button type="submit" class="button primary" ${writable ? "" : "disabled"}>Save</button>` }));
  const form = /** @type {HTMLFormElement} */ (document.getElementById("gradebook-history-form"));
  const backButton = /** @type {HTMLElement | null} */ (form.querySelector("[data-dialog-back]"));
  if (backButton && back) backButton.onclick = () => back.run();
  const paperButton = /** @type {HTMLElement | null} */ (form.querySelector("[data-paper]"));
  if (paperButton) paperButton.onclick = () => seePaper(ctx, itemId, row);
  const session = dialogSession(form, dialogNode());
  const fields = /** @type {any} */ (form.elements);
  const list = /** @type {HTMLElement} */ (form.querySelector("[data-history]"));
  const conflict = /** @type {HTMLElement} */ (form.querySelector("[data-conflict]"));
  let cell = view.cells?.[itemId]?.[row] || null, requestId = "", generation = 0;

  function fill() {
    fields.status.value = cell && cell.status !== "cleared" ? cell.status : "scored";
    fields.points.value = cell?.status === "scored" ? cell.points ?? "" : "";
    fields.note.value = cell && cell.status !== "cleared" ? cell.note || "" : "";
    points();
  }
  function points() {
    const scored = fields.status.value === "scored";
    /** @type {HTMLElement} */ (form.querySelector("[data-points]")).hidden = !scored;
    fields.points.required = scored;
  }
  function sourceText(revision) {
    const summary = revision.request?.summary || {};
    // An excused absence's iClicker credit, or its credit taken back (docs/decisions/absences.md ICLICKER-CREDIT).
    if (summary.source === "absence") {
      return summary.action === "put_back" ? "Put back: no excused absence covers this session now" : `Full credit for an excused absence${summary.dates ? ` (${summary.dates})` : ""}`;
    }
    // An iClicker credit given before the import, or taken back (docs/decisions/iclicker.md CREDIT BEFORE THE IMPORT).
    if (summary.source === "iclicker_credit") {
      return summary.action === "put_back" ? "Put back: the iClicker credit you gave was taken back" : "Full iClicker credit you gave";
    }
    // A file import keeps its filename; Canvas and iClicker name their source (README.md "Syncing with Canvas").
    if (revision.source === "import") {
      if (summary.source === "canvas") return summary.automatic === "webassign" ? "Copied from Canvas (WebAssign), nightly" : summary.sync ? "From Canvas, by Sync with Canvas" : "Imported from Canvas";
      if (summary.source === "iclicker") return summary.automatic === "iclicker" ? "Imported from iClicker after class" : "Imported from iClicker";
      return summary.filename ? `Imported from ${summary.filename}` : "Imported";
    }
    if (revision.source === "grading" && summary.certification_id) {
      // Recorded by a certification (or a pull of a certified assignment): the certification's date when it is the latest one.
      const latest = view.links?.[itemId]?.certification;
      const day = latest?.id === summary.certification_id ? ` of ${when(latest.created_at)}` : "";
      return `Recorded from the certification${day}${summary.parts?.length ? ` (${partsText(summary.parts)})` : ""}`;
    }
    if (revision.source === "grading") return `Pulled from grading${summary.parts?.length ? ` (${partsText(summary.parts)})` : ""}`;
    // The assistant enters a value through the same scores write; it did not type it (request_actor.py).
    if (revision.source === "instructor" && revision.actor === "assistant") return "Entered in Faculty";
    return SOURCE[revision.source] || revision.source || "";
  }
  async function load() {
    const token = ++generation;
    try {
      const result = await ctx.api(url(ctx, `/history?item=${encodeURIComponent(itemId)}&row=${encodeURIComponent(row)}`));
      if (token !== generation || !session()) return;
      const revisions = result.revisions || [];
      list.innerHTML = revisions.length ? `<ol class="gbd-history">${revisions.map((revision, index) => `<li${index === 0 ? ' data-current="true"' : ""}>
          <div class="gbd-history-value"><strong>${e(revision.status === "cleared" ? "Cleared" : valueText(revision))}</strong>${revision.status === "scored" ? ` <small>of ${e(item.points)}</small>` : ""}${index === 0 ? ' <span class="gbd-current">current</span>' : ""}</div>
          <p class="gbd-history-meta">${e(sourceText(revision))} · ${e(actorText(revision.actor))} · <time datetime="${e(revision.created_at)}">${e(when(revision.created_at))}</time></p>
          ${revision.note ? `<p class="gbd-history-note">${e(revision.note)}</p>` : ""}</li>`).join("")}</ol>`
        : `<p class="gbd-empty-note">No value has been recorded for this cell yet.</p>`;
    } catch (cause) {
      if (token === generation && session()) list.innerHTML = `<div class="callout gbd-note" data-tone="danger"><p>${e(failure(cause).message)}</p></div>`;
    }
  }

  form.addEventListener("input", () => { requestId = ""; });
  fields.status.addEventListener("change", () => { requestId = ""; points(); });
  form.onsubmit = event => {
    event.preventDefault();
    if (!writable) return;
    void runFormAction(form, async () => {
      conflict.hidden = true;
      const status = fields.status.value, note = String(fields.note.value || "").trim();
      // The grid's own rule for typed points (plain digits, four decimals at most, as the server allows).
      const typed = status === "scored" ? parseEntry(String(fields.points.value || "")) : null;
      if (typed && typed.status !== "scored") throw new Error(typed.error || "Enter the points as a number, in ten-thousandths at most.");
      const value = typed?.points || "";
      const sentFields = [fields.status.value, fields.points.value, fields.note.value];
      const same = status === "cleared" ? !cell || cell.status === "cleared"
        : cell && cell.status === status && (cell.note || "") === note && (status !== "scored" || Number(cell.points) === Number(value));
      if (same) throw new Error("Nothing changed.");
      requestId ||= ctx.requestId();
      const change = { item_id: itemId, row, base: cell?.id ?? null, status, ...(status === "scored" ? { points: value } : {}), ...(status === "cleared" ? {} : { note }) };
      try {
        const result = await ctx.api(url(ctx, "/scores"), "POST", { request_id: requestId, changes: [change], ...scope(ctx) });
        ctx.applyScores(result);
        cell = result.cells?.[itemId]?.[row] || null;
        requestId = "";
        ctx.toast(status === "cleared" ? "Cell cleared" : "Cell saved");
        if (!session()) return;
        if ([fields.status.value, fields.points.value, fields.note.value].some((value, index) => value !== sentFields[index])) {
          await load();
          return;
        }
        // Opened from the student's record: back to it, with the value just saved.
        if (back) { back.run(); return; }
        fill();
        await load();
      } catch (cause) {
        const failed = failure(cause);
        if (failed.status !== 409 || !session()) throw cause;
        // Changed in another window since the grid read it: show the latest value and let the instructor decide again.
        requestId = "";
        try {
          view = await ctx.reload();
          cell = view.cells?.[itemId]?.[row] || null;
        } catch { /* The history below still shows the latest revision. */ }
        if (!session()) return;
        conflict.hidden = false;
        conflict.innerHTML = `<p>${e(failed.message)} This cell’s latest value is shown; make your change again.</p>`;
        fill();
        await load();
      }
    });
  };
  fill();
  void load();
}

// The nightly WebAssign import found Faculty signed out of Canvas (gradebook/webassign_nightly.py): the notice's Sign in to Canvas;
// once the sign-in is kept the View is read again, and the notice goes.
export async function openNightlySignIn(ctx) {
  const who = await openCanvasSignIn({ api: ctx.api, showDialog: ctx.showDialog, dialogHeader: ctx.dialogHeader, escape: ctx.escape });
  if (who !== null) await ctx.reload();
}

// LEFT OUT OF IMPORTS (requirement, 2026-10-04): the nightly notice's Leave out. The
// assignments waiting for a column are left out of every sync from now on, remembered like a sync's own skip; nothing is read from
// Canvas and no score is written. Choosing a column for one in Sync with Canvas brings it back.
export async function skipNightlyWaiting(ctx) {
  const keys = ctx.view?.canvas?.nightly?.waiting_keys || [];
  if (!keys.length) return;
  const result = await ctx.api(url(ctx, "/canvas/skip"), "POST", { assignments: keys, request_id: ctx.requestId(), ...scope(ctx) });
  ctx.replaceView(result);
  const titles = [...new Set(keys.map((entry) => entry.title))];
  ctx.toast(`${titles.length === 1 ? `“${titles[0]}” is` : `${titles.length} assignments are`} left out of Sync with Canvas. Choosing a column for ${titles.length === 1 ? "it" : "one"} there brings it back.`);
}
