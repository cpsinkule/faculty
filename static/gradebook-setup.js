// Gradebook setup (#gradebook/<term>/setup): the grading method, categories, columns and letter scale of ONE term
// (gradebook/README.md, "Setup"). A save records a new immutable revision on the server, which validates everything and
// names what it refuses. Browser memory holds only the unsaved draft, keyed by term, which survives re-renders and
// navigation within this tab. A draft begun on an older revision is set aside when another save lands, and the instructor
// is told. Nothing here changes a score. Because the draft survives navigation within the tab, only closing or reloading the tab
// loses it: that asks first (ui.js registerLeaveGuard; a hash change inside the workspace does not).

import { bindFilterChips, filterChipsMarkup, plural, registerLeaveGuard, setBusy } from "./ui.js";
import { STANDARD_SCALE, scaleProblems, scaleUppers } from "./grading-scheme.js";

export const RULE = "gradebook-v1";
export const PART_ORDER = ["frq", "bubble", "exams"];
export const PARTS = { frq: "Written work (FRQ grading)", bubble: "Bubble sheets", exams: "Exams page grading" };
export const DEFAULT_SCALE = STANDARD_SCALE;
export const PRESETS = {
  plain: { label: "Standard A–F", scale: DEFAULT_SCALE },
  signs: { label: "A–F with +/−", scale: [["A", "93"], ["A-", "90"], ["B+", "87"], ["B", "83"], ["B-", "80"], ["C+", "77"], ["C", "73"], ["C-", "70"], ["D+", "67"], ["D", "63"], ["D-", "60"], ["F", "0"]] },
};
const MODES = [
  ["points", "Total points", "Every counted point over every counted possible point. Categories only group columns and drop scores."],
  ["weighted", "Weighted categories", "Each category’s percentage times its weight, over the weights of the categories a student has results in."],
];
const UNGRADED = [
  // GRADEBOOK "ZERO ONCE DUE" (requirement, 2026-10-06): a new term's default.
  ["due", "Count empty cells as zero once due", "From the day after the due date, or once you certify a graded quiz or exam. Makeups owed, DR bookings and papers still being graded wait."],
  ["exclude", "Leave empty cells out", "A cell without a score does not count until it has one."],
  ["zero", "Count empty cells as zero", "An empty cell of a counted column counts as zero points, due or not."],
];
const ROUNDING = [["", "Exact percentage"], ["0", "Round to a whole percent"], ["1", "Round to one decimal"], ["2", "Round to two decimals"]];
const AGGREGATE = [["points", "Total points"], ["equal", "Equal weight per column"]];
// Fields the View adds to a setup; a save sends the setup without them.
const VIEW_ONLY = ["revision", "created_at", "actor", "sha256", "request_id", "request_sha256"];
// Typed fields update the draft as the instructor types and never redraw (focus and caret stay put).
const TYPED = new Set(["cat.name", "cat.weight", "cat.drop", "item.title", "item.points", "item.due", "item.notes", "scale.letter", "scale.min"]);

const drafts = new Map(); // term ID -> {base, original, setup, removed, requestId, saving, error, conflict, showProblems, release}
const live = new Map(); // term ID -> {host, ctx} the editor was last drawn with
const more = new Map(); // term ID -> the column keys whose "More" row (sections, link, notes) is open: view memory only
// term ID -> {query, only, pinned}: the Columns table's "Find a column" text, its "Uncategorized" filter and the columns edited
// while it was on (kept in view until the find changes). View memory only: it hides rows (the `hidden` attribute) and never
// changes the draft, its order or what Save sends.
const finds = new Map();
const shown = new WeakMap(); // feedback region -> the problem list it shows, so typing re-announces only a change
let counter = 0;
const newKey = kind => `new-${kind}-${++counter}`;

// ---------------------------------------------------------------------------------------------------------------------
// Pure helpers (also used by gradebook-dialogs.js). A draft keeps client-only fields under keys starting with "$".

const text = value => value === null || value === undefined ? "" : String(value).trim();
const strip = record => Object.fromEntries(Object.entries(record || {}).filter(([key]) => !key.startsWith("$")));
const whole = value => /^\d+$/.test(text(value)) ? Number(text(value)) : text(value);
export const sectionIds = view => (view?.sections || []).map(section => section.id);

export function when(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : String(value ?? "");
}

/** The setup fields of a View's setup, as a save sends them back: a deep copy without the revision's own fields. Fields
 * this editor does not know are kept. */
export function savedSetup(setup) {
  const copy = JSON.parse(JSON.stringify(setup || {}));
  for (const key of VIEW_ONLY) delete copy[key];
  copy.rule ||= RULE;
  copy.mode ||= "points";
  copy.categories ||= [];
  copy.items ||= [];
  copy.scale ||= DEFAULT_SCALE.map(([letter, min]) => ({ letter, min }));
  copy.ungraded ||= "due";
  if (!("letter_rounding" in copy)) copy.letter_rounding = null;
  return copy;
}

/** A saved setup as the editor holds it: numbers as the text of their inputs, and a stable `$key` on every row. */
export function draftSetup(setup) {
  const saved = savedSetup(setup);
  return {
    ...saved,
    letter_rounding: saved.letter_rounding ?? null,
    categories: saved.categories.map(category => ({
      ...category, $key: category.id, name: category.name ?? "", weight: text(category.weight ?? "0"),
      drop_lowest: text(category.drop_lowest ?? 0), aggregate: category.aggregate || "points",
      posting: category.posting ? { due: !!category.posting.due, certified: !!category.posting.certified } : null,
    })),
    items: saved.items.map(item => ({
      ...item, $key: item.id, title: item.title ?? "", category_id: item.category_id ?? null, points: text(item.points),
      due_date: item.due_date || "", counts: item.counts !== false, extra_credit: !!item.extra_credit, archived: !!item.archived,
      replaces_lowest: !!item.replaces_lowest, sections: [...(item.sections || [])], assignment_id: item.assignment_id ?? null,
      link: item.link ? { ...item.link, parts: [...(item.link.parts || [])] } : null, notes: item.notes ?? "",
      $specific: (item.sections || []).length > 0,
    })),
    scale: saved.scale.map(entry => ({ ...entry, letter: entry.letter ?? "", min: text(entry.min) })),
  };
}

const folded = value => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();

/** Whether a draft column has no category the setup knows (it is then Uncategorized). */
export function uncategorized(setup, item) {
  return !item.category_id || !setup.categories.some(category => category.$key === item.category_id);
}

/** Whether a draft column is shown by the Columns table's find: its title holds the text (case and accents ignored) and, with
 * `only` "uncategorized", it has no category. A column in `pinned` (edited while this find was on) is shown whatever it now
 * holds, so choosing a category under "Uncategorized" never hides the row in hand. */
export function columnShown(setup, item, { query = "", only = "", pinned = null } = {}) {
  if (pinned?.has(item.$key)) return true;
  const wanted = folded(query);
  return (!wanted || folded(item.title).includes(wanted)) && (only !== "uncategorized" || uncategorized(setup, item));
}

/** A blank column as the editor and the column dialog start it. */
export function blankItem() {
  return { $key: newKey("item"), title: "", category_id: null, points: "", due_date: "", counts: true, extra_credit: false, archived: false,
    replaces_lowest: false, sections: [], assignment_id: null, link: null, notes: "", $specific: false };
}

/** What a save sends for a draft setup: trimmed text, whole numbers where the server wants integers, sections in the term's
 * order, grading parts in their fixed order. Rows the server has not seen carry no `id`; a new category carries its draft
 * key as `ref` (browser text the server never stores), which a column of the same save names as its `category_id`. */
export function setupPayload(setup, order = []) {
  const rank = new Map(order.map((id, index) => [id, index]));
  const bySection = (a, b) => { const d = (rank.get(a) ?? order.length) - (rank.get(b) ?? order.length); return d || 0; };
  return {
    ...strip(setup),
    letter_rounding: setup.letter_rounding === null || setup.letter_rounding === undefined || setup.letter_rounding === "" ? null : whole(setup.letter_rounding),
    categories: setup.categories.map(category => ({ ...strip(category), ...(category.id ? {} : { ref: category.$key }),
      name: text(category.name), weight: text(category.weight), drop_lowest: whole(category.drop_lowest), aggregate: category.aggregate || "points",
      posting: category.posting?.due || category.posting?.certified ? { due: !!category.posting.due, certified: !!category.posting.certified } : null })),
    items: setup.items.map(item => {
      const chosen = item.link?.parts || [];
      const parts = [...PART_ORDER.filter(part => chosen.includes(part)), ...chosen.filter(part => !PART_ORDER.includes(part))];
      return { ...strip(item), title: text(item.title), category_id: item.category_id || null, points: text(item.points),
        due_date: text(item.due_date), counts: !!item.counts, extra_credit: !!item.extra_credit, archived: !!item.archived,
        replaces_lowest: !!item.replaces_lowest, sections: [...new Set(item.sections || [])].sort(bySection), assignment_id: item.assignment_id || null,
        link: item.assignment_id && item.link && parts.length ? { ...item.link, kind: item.link.kind || "grading", parts } : null,
        notes: text(item.notes) };
    }),
    scale: setup.scale.map(entry => ({ ...strip(entry), letter: text(entry.letter), min: text(entry.min) })),
  };
}
export const canonical = (setup, order) => JSON.stringify(setupPayload(setup, order));

const number = (value, max, places = 4) => {
  const raw = text(value);
  if (!new RegExp(`^\\d+(\\.\\d{1,${places}})?$`).test(raw)) return false;
  return Number(raw) <= max;
};

/** Obvious mistakes, found before a save (the server still decides). Each names the field to focus. */
export function problems(setup) {
  const found = [], seen = new Set();
  if (setup.categories.length > 50) found.push({ focus: "", message: "At most 50 categories." });
  setup.categories.forEach((category, index) => {
    const name = text(category.name), label = name || `Category ${index + 1}`, key = category.$key;
    if (!name) found.push({ focus: `cat.name:${key}`, message: `Category ${index + 1} needs a name.` });
    else if (name.length > 60) found.push({ focus: `cat.name:${key}`, message: `${label}: a name has at most 60 characters.` });
    else if (seen.has(name.toLowerCase())) found.push({ focus: `cat.name:${key}`, message: `Two categories are named ${name}.` });
    seen.add(name.toLowerCase());
    if (!number(category.weight, 1000)) found.push({ focus: `cat.weight:${key}`, message: `${label}: the weight is a number from 0 to 1000.` });
    if (!/^\d+$/.test(text(category.drop_lowest)) || Number(text(category.drop_lowest)) > 50) found.push({ focus: `cat.drop:${key}`, message: `${label}: drop lowest is a whole number from 0 to 50.` });
  });
  if (setup.items.length > 500) found.push({ focus: "", message: "At most 500 columns." });
  setup.items.forEach((item, index) => {
    const title = text(item.title), label = title || `Column ${index + 1}`, key = item.$key;
    if (!title) found.push({ focus: `item.title:${key}`, message: `Column ${index + 1} needs a title.` });
    else if (title.length > 120) found.push({ focus: `item.title:${key}`, message: `${label}: a title has at most 120 characters.` });
    if (!number(item.points, 100000)) found.push({ focus: `item.points:${key}`, message: `${label}: points are a number from 0 to 100000, in ten-thousandths at most.` });
    else if (Number(text(item.points)) === 0 && !item.extra_credit) found.push({ focus: `item.points:${key}`, message: `${label}: only an extra-credit column may be worth 0 points.` });
    if (text(item.due_date) && !/^\d{4}-\d{2}-\d{2}$/.test(text(item.due_date))) found.push({ focus: `item.due:${key}`, message: `${label}: the due date is not a date.` });
    if (item.$specific && !(item.sections || []).length) found.push({ focus: `item.sections:${key}`, message: `${label}: choose at least one section, or All sections.` });
    if (item.replaces_lowest && !item.category_id) found.push({ focus: `item.category:${key}`, message: `${label}: a replacement column needs a category, whose lowest score it replaces.` });
    if (item.replaces_lowest && item.extra_credit) found.push({ focus: `item.extra:${key}`, message: `${label}: a replacement column cannot also be extra credit.` });
    if (text(item.notes).length > 1000) found.push({ focus: `item.notes:${key}`, message: `${label}: notes have at most 1000 characters.` });
  });
  const pulling = new Map();
  setup.items.forEach(item => {
    if (!item.assignment_id || !(item.link?.parts || []).length || item.archived) return;
    const other = pulling.get(item.assignment_id);
    if (other) found.push({ focus: `item.assignment:${item.$key}`, message: `${text(other.title) || "A column"} and ${text(item.title) || "another column"} both pull from the same assignment: archive one or remove its link.` });
    else pulling.set(item.assignment_id, item);
  });
  found.push(...scaleProblems(setup.scale));
  return found;
}

// ---------------------------------------------------------------------------------------------------------------------
// The draft of one term.

function isDirty(draft, order) { return canonical(draft.setup, order) !== draft.original; }

/** Whether the setup editor holds unsaved changes for this term (the column dialog warns before saving over them). */
export function unsavedSetup(termId) {
  const draft = drafts.get(termId), slot = live.get(termId);
  return !!draft && isDirty(draft, sectionIds(slot?.ctx.view));
}

// The shell asks every guard before a hash change and before the tab unloads. A draft is kept across hash changes (browser
// memory, the grid says it is there), so the guard is quiet while the shell handles a hashchange and speaks only for an unload.
registerLeaveGuard(() => {
  if (globalThis.window?.event?.type === "hashchange") return null;
  return [...drafts.keys()].some(unsavedSetup) ? "The gradebook setup has changes that are not saved yet." : null;
});

function current(ctx) {
  const revision = ctx.view.setup?.revision ?? null, order = sectionIds(ctx.view);
  let draft = drafts.get(ctx.termId);
  if (draft && draft.base !== revision && !draft.saving) {
    // Saved from another window, a dialog or another tab since this draft began: the saved setup wins, and the instructor is told.
    if (!draft.release && isDirty(draft, order)) ctx.toast(`The gradebook setup was saved elsewhere${revision === null ? "" : ` (revision ${revision})`}, so your unsaved setup changes were set aside. The saved setup is shown.`, true);
    draft = null;
  }
  if (!draft) {
    const setup = draftSetup(ctx.view.setup);
    draft = { base: revision, original: canonical(setup, order), setup, removed: [], requestId: "", saving: false, error: "", conflict: false, showProblems: false, release: false };
    drafts.set(ctx.termId, draft);
  }
  return draft;
}

// Any change is a new command: a retried save of unchanged content reuses its request ID, so a lost response returns the
// revision it made instead of making a second one.
function touched(draft) { draft.requestId = ""; }

// ---------------------------------------------------------------------------------------------------------------------
// Markup

function stateText(draft, view) {
  if (draft.saving) return "Saving…";
  if (isDirty(draft, sectionIds(view))) return "Unsaved changes";
  const setup = view.setup || {};
  return setup.revision === null || setup.revision === undefined ? "Not saved yet · the default setup" : `Saved · revision ${setup.revision}${setup.created_at ? ` · ${when(setup.created_at)}` : ""}`;
}

function invalid(set, key) { return set.has(key) ? ' aria-invalid="true"' : ""; }

/** What a saved column holds: its scores shown now, and whether it has ANY score revision (cleared ones included). The View's
 * `scored_items` names exactly the columns the server refuses to remove; a typed score makes it stale until the next read, so
 * a shown score counts too. A section tab's View holds only that section's cells (`cells=section`): its `cell_counts` keeps the
 * count the term's. */
function scoreFacts(view, item) {
  const count = item.id ? (view.cell_counts?.[item.id] ?? Object.keys(view.cells?.[item.id] || {}).length) : 0;
  const kept = !!item.id && (view.scored_items?.includes(item.id) || count > 0);
  return { count, kept };
}

function methodMarkup(ctx, setup) {
  const e = ctx.escape;
  const choices = (name, field, list, value) => list.map(([key, title, note]) => `<label class="gbs-choice"><input type="radio" name="gbs-${name}" value="${key}" data-field="${field}" data-focus="${field}:${key}" ${value === key ? "checked" : ""}><span><strong>${e(title)}</strong><small>${e(note)}</small></span></label>`).join("");
  const rounding = setup.letter_rounding === null || setup.letter_rounding === undefined ? "" : String(setup.letter_rounding);
  return `<section class="gbs-panel card" aria-labelledby="gbs-method-title"><h3 id="gbs-method-title">Grading method</h3>
    <fieldset class="gbs-choices"><legend>Overall percentage</legend>${choices("mode", "mode", MODES, setup.mode)}</fieldset>
    <fieldset class="gbs-choices"><legend>Empty cells</legend>${choices("ungraded", "ungraded", UNGRADED, setup.ungraded)}</fieldset>
    <label class="gbs-rounding"><span>Letter grades</span><select data-field="letter_rounding" data-focus="letter_rounding">${ROUNDING.map(([key, label]) => `<option value="${key}" ${rounding === key ? "selected" : ""}>${e(label)}</option>`).join("")}</select>
      <small class="field-hint">The percentage is rounded half up before its letter is chosen: rounded to a whole percent, 89.5 is an A with a 90 cutoff.</small></label></section>`;
}

function removedMarkup(ctx, draft, kind) {
  const e = ctx.escape, list = draft.removed.map((entry, index) => ({ ...entry, index })).filter(entry => entry.kind === kind);
  if (!list.length) return "";
  return `<div class="gbs-removed"><p>Removed when you save:</p><ul>${list.map(({ entry, index }) => {
    const name = kind === "item" ? text(entry.title) || "Untitled column" : text(entry.name) || "Unnamed category";
    const facts = kind === "item" ? scoreFacts(ctx.view, entry) : { count: 0, kept: false };
    const warning = !facts.kept ? "" : facts.count ? "it has scores, so the server will keep it: Undo, then archive it instead"
      : "its scores were cleared but their history stays, so the server will keep it: Undo, then archive it instead";
    return `<li><span><strong>${e(name)}</strong>${warning ? ` · <em>${e(warning)}</em>` : ""}</span><button type="button" class="text-button" data-act="undo" data-index="${index}" aria-label="Undo removing ${e(name)}">Undo</button></li>`;
  }).join("")}</ul></div>`;
}

function moveButtons(e, kind, key, index, count, label) {
  return `<button type="button" class="icon-button gbs-move" data-act="${kind}-up" data-key="${e(key)}" data-focus="${kind}-up:${e(key)}" aria-label="Move ${e(label)} up" ${index === 0 ? "disabled" : ""}><span aria-hidden="true">↑</span></button><button type="button" class="icon-button gbs-move" data-act="${kind}-down" data-key="${e(key)}" data-focus="${kind}-down:${e(key)}" aria-label="Move ${e(label)} down" ${index === count - 1 ? "disabled" : ""}><span aria-hidden="true">↓</span></button>`;
}

// A panel's longer explanation, closed: "How it counts" (the open state follows redraws through data-keep).
function howMarkup(key, body, summary = "How it counts") {
  return `<details class="disclosure gbs-how" data-keep="how:${key}"><summary><span>${summary}</span></summary><div class="disclosure-body"><p>${body}</p></div></details>`;
}

/**
 * The letter counts a draft scale would give the percentages shown now (the saved method's `results`): [{letter, count}] in
 * scale order and `none` for students without a percentage. Only a preview: a letter needs a valid minimum here, and the
 * server decides the real letters on save.
 */
export function scalePreview(scale, percents) {
  const ladder = (scale || []).map(entry => ({ letter: text(entry.letter), min: number(entry.min, 1000) ? Number(text(entry.min)) : null }));
  const counts = ladder.map(entry => ({ letter: entry.letter, count: 0 }));
  let none = 0, unmatched = 0;
  for (const value of percents) {
    const percent = value === null || value === undefined || value === "" ? NaN : Number(value);
    if (Number.isNaN(percent)) { none += 1; continue; }
    const index = ladder.findIndex(entry => entry.letter && entry.min !== null && percent >= entry.min);
    if (index < 0) unmatched += 1; else counts[index].count += 1;
  }
  return { counts, none, unmatched };
}

export function previewText(scale, view) {
  const percents = Object.values(view?.results?.students || {}).map(result => result?.percent);
  if (!percents.length) return "";
  const { counts, none, unmatched } = scalePreview(scale, percents);
  const letters = counts.filter(entry => entry.letter).map(entry => `${entry.letter} ${entry.count}`).join(" · ");
  return `Preview from today’s percentages: ${letters || "no letter yet"}${unmatched ? ` · ${unmatched} below every minimum` : ""}${none ? ` · ${none} without a percentage` : ""}. The server decides on save.`;
}

function weightsNote(setup) {
  if (setup.mode !== "weighted") return "Weights are used only with Weighted categories.";
  const total = setup.categories.reduce((sum, category) => sum + (number(category.weight, 1000) ? Number(text(category.weight)) : 0), 0);
  const shown = Math.round(total * 10000) / 10000;
  if (!setup.categories.length) return "";
  return shown === 100 ? "Weights add up to 100." : `Weights add up to ${shown}, not 100. That is fine: each student’s weights are rescaled over the categories they have results in, so this is only a hint.`;
}

/** POSTING GRADES (requirement, 2026-10-08): how a category's columns are posted in Canvas. By hand, or automatically after a column's
 * due date and/or when Faculty certifies it (both may apply in a category that mixes imported and graded columns). Weight 0 in
 * weighted grading is always by hand. */
function postingCell(e, category, key, label, weighted) {
  if (weighted && Number(text(category.weight) || 0) === 0) return `<span class="gbs-quiet" title="A category that does not count is always posted by hand">By hand (doesn’t count)</span>`;
  const auto = !!(category.posting?.due || category.posting?.certified);
  const tick = (rule, words) => `<label class="checkbox-field"><input type="checkbox" data-field="cat.posting-${rule}" data-key="${e(key)}" data-focus="cat.posting-${rule}:${e(key)}" ${category.posting?.[rule] ? "checked" : ""}>${e(words)}</label>`;
  return `<select data-field="cat.posting" data-key="${e(key)}" data-focus="cat.posting:${e(key)}" aria-label="How ${e(label)} is posted in Canvas"><option value="manual" ${auto ? "" : "selected"}>By hand</option><option value="auto" ${auto ? "selected" : ""}>Automatically</option></select>${auto ? tick("due", "after the due date") + tick("certified", "when certified") : ""}`;
}

function categoriesMarkup(ctx, draft, flagged) {
  const e = ctx.escape, setup = draft.setup, weighted = setup.mode === "weighted";
  const rows = setup.categories.map((category, index) => {
    const key = category.$key, label = text(category.name) || `category ${index + 1}`;
    const used = setup.items.filter(item => item.category_id === key).length;
    return `<tr data-key="${e(key)}">
      <td><input type="text" maxlength="60" value="${e(category.name)}" data-field="cat.name" data-key="${e(key)}" data-focus="cat.name:${e(key)}" aria-label="Name of category ${index + 1}" placeholder="Category name"${invalid(flagged, `cat.name:${key}`)}></td>
      <td class="${weighted ? "" : "gbs-quiet"}"><input type="number" min="0" max="1000" step="any" inputmode="decimal" value="${e(category.weight)}" data-field="cat.weight" data-key="${e(key)}" data-focus="cat.weight:${e(key)}" aria-label="Weight of ${e(label)}" aria-describedby="gbs-weights-note"${invalid(flagged, `cat.weight:${key}`)}></td>
      <td><input type="number" min="0" max="50" step="1" inputmode="numeric" value="${e(category.drop_lowest)}" data-field="cat.drop" data-key="${e(key)}" data-focus="cat.drop:${e(key)}" aria-label="Lowest scores of ${e(label)} to drop"${invalid(flagged, `cat.drop:${key}`)}></td>
      <td><select data-field="cat.aggregate" data-key="${e(key)}" data-focus="cat.aggregate:${e(key)}" aria-label="How ${e(label)} combines its columns">${AGGREGATE.map(([value, name]) => `<option value="${value}" ${category.aggregate === value ? "selected" : ""}>${e(name)}</option>`).join("")}</select></td>
      <td class="gbs-num">${used}</td>
      <td>${postingCell(e, category, key, label, weighted)}</td>
      <td class="gbs-row-actions">${moveButtons(e, "cat", key, index, setup.categories.length, label)}<button type="button" class="icon-button gbs-remove" data-act="cat-remove" data-key="${e(key)}" data-focus="cat-remove:${e(key)}" aria-label="Remove ${e(label)}" ${used ? `disabled title="Used by ${plural(used, "column")}: give ${used === 1 ? "it" : "them"} another category first."` : 'title="Remove"'}><span aria-hidden="true">×</span></button></td></tr>`;
  });
  return `<section class="gbs-panel card" aria-labelledby="gbs-categories-title"><div class="gbs-panel-head"><h3 id="gbs-categories-title">Categories</h3>
      <button type="button" class="button" data-act="cat-add" data-focus="cat-add">${ctx.icon("plus")}Add category</button></div>
    <p class="supporting-copy">Categories group columns; drop lowest leaves out each student’s lowest scores there.</p>
    ${howMarkup("categories", "Drop lowest leaves out a student’s lowest scores in the category (never the last one). Total points adds the points; equal weight averages the columns’ percentages. With Weighted categories, each category’s percentage counts by its weight.")}
    ${howMarkup("posting", "Posting in Canvas: each Sync with Canvas, yours or the morning one, posts a column’s grades once its due date has passed or once Faculty certifies it, as its category says; By hand waits for Post in the column’s menu, and a column whose Canvas assignment posts automatically is set to post by hand. iClicker’s columns post automatically. A category that does not count (weight 0) is always by hand. A column’s own Post or Hide wins.", "How posting in Canvas works")}
    ${setup.categories.length ? `<div class="gbs-scroll" data-scroll="categories" role="region" aria-label="Categories" tabindex="0"><table class="gbs-table gbs-categories"><thead><tr><th scope="col">Name</th><th scope="col">Weight</th><th scope="col">Drop lowest</th><th scope="col">Combine</th><th scope="col">Columns</th><th scope="col">Posting in Canvas</th><th scope="col"><span class="sr-only">Order and removal</span></th></tr></thead><tbody>${rows.join("")}</tbody></table></div>`
      : `<p class="gbs-empty">No categories yet. Columns without a category are Uncategorized: counted in total points, not in weighted categories.</p>`}
    <p class="gbs-weights field-hint" id="gbs-weights-note" data-weights>${e(weightsNote(setup))}</p>${removedMarkup(ctx, draft, "category")}</section>`;
}

function sectionsCell(ctx, item) {
  const e = ctx.escape, key = item.$key, sections = ctx.view.sections || [];
  const all = !item.$specific && !(item.sections || []).length;
  const numbers = sections.filter(section => item.sections.includes(section.id)).map(section => section.number);
  const summary = all ? "All sections" : numbers.length ? `Section${numbers.length === 1 ? "" : "s"} ${numbers.join(", ")}` : "No section chosen";
  return `<details class="gbs-sections" data-keep="sections:${e(key)}"><summary data-focus="item.sections:${e(key)}">${e(summary)}</summary><div>
    <label class="checkbox-field"><input type="checkbox" data-field="item.all-sections" data-key="${e(key)}" data-focus="item.all-sections:${e(key)}" ${all ? "checked" : ""}>All sections</label>
    ${sections.map(section => `<label class="checkbox-field"><input type="checkbox" data-field="item.section" data-key="${e(key)}" data-section="${e(section.id)}" data-focus="item.section:${e(key)}:${e(section.id)}" ${!all && item.sections.includes(section.id) ? "checked" : ""} ${all ? "disabled" : ""}>Section ${e(section.number)}</label>`).join("")}</div></details>`;
}

/** The linked-assignment control of one column (the setup table and the column dialog share it). `field` prefixes data-field. */
export function linkControls(ctx, item, { field = "item", key = item.$key, label = "" } = {}) {
  const e = ctx.escape, assignments = ctx.view.assignments || [];
  const assignment = assignments.find(entry => entry.id === item.assignment_id);
  const options = `<option value="">Not linked</option>${assignments.map(entry => `<option value="${e(entry.id)}" ${entry.id === item.assignment_id ? "selected" : ""}>${e(entry.title)}</option>`).join("")}${item.assignment_id && !assignment ? `<option value="${e(item.assignment_id)}" selected>An assignment this term does not list</option>` : ""}`;
  const available = assignment?.parts || [], linked = item.link?.parts || [];
  const parts = [...PART_ORDER.filter(part => available.includes(part) || linked.includes(part)), ...linked.filter(part => !PART_ORDER.includes(part))];
  const boxes = parts.map(part => `<label class="checkbox-field"><input type="checkbox" data-field="${field}.part" data-key="${e(key)}" data-part="${e(part)}" data-focus="${field}.part:${e(key)}:${e(part)}" ${linked.includes(part) ? "checked" : ""}>${e(PARTS[part] || part)}${available.includes(part) ? "" : " <small>(nothing this term)</small>"}</label>`).join("");
  const note = !item.assignment_id ? "" : !parts.length ? "No grading of it this term yet: the column only refers to it."
    : !linked.length ? "Refers to it only: tick a part to pull scores from it." : "Pulls final grades from the ticked parts.";
  return `<select data-field="${field}.assignment" data-key="${e(key)}" data-focus="${field}.assignment:${e(key)}" aria-label="Linked assignment${label ? ` of ${e(label)}` : ""}">${options}</select>${boxes ? `<div class="gbs-parts" role="group" aria-label="Grading to pull from">${boxes}</div>` : ""}${note ? `<small class="field-hint">${e(note)}</small>` : ""}`;
}

// Columns: what every column needs is visible (title, category, points, due, counts, extra credit, archived, order and removal);
// its sections, linked assignment and notes open in a "More" row beneath it (view memory, kept across redraws, opened by itself
// when one of them needs fixing).
function itemsMarkup(ctx, draft, flagged) {
  const e = ctx.escape, setup = draft.setup, open = more.get(ctx.termId) || new Set(), find = finds.get(ctx.termId) || { query: "", only: "" };
  const categoryOptions = selected => `<option value="" ${selected ? "" : "selected"}>Uncategorized</option>${setup.categories.map(category => `<option value="${e(category.$key)}" data-cat="${e(category.$key)}" ${category.$key === selected ? "selected" : ""}>${e(text(category.name) || "Unnamed category")}</option>`).join("")}`;
  // The select's title carries the whole name, which its narrow cell may cut short.
  const categoryName = item => uncategorized(setup, item) ? "Uncategorized" : text(find_(setup.categories, item.category_id)?.name) || "Unnamed category";
  const rows = setup.items.map((item, index) => {
    const key = item.$key, label = text(item.title) || `column ${index + 1}`;
    const { count: scored, kept } = scoreFacts(ctx.view, item);
    const hidden = ["sections", "assignment", "notes"].some(field => flagged.has(`item.${field}:${key}`));
    const expanded = open.has(key) || hidden;
    // What the More row holds, in a word or two, so a closed row still says it: linked, some sections, a note.
    const held = [item.replaces_lowest ? "replaces lowest" : "", item.assignment_id ? "linked" : "", item.$specific || (item.sections || []).length ? "some sections" : "", text(item.notes) ? "note" : ""].filter(Boolean);
    const moreId = `gbs-more-${key}`;
    return `<tbody class="gbs-item" data-item-key="${e(key)}"${columnShown(setup, item, find) ? "" : " hidden"}><tr data-key="${e(key)}" ${item.archived ? 'data-archived="true"' : ""}>
      <th scope="row"><input type="text" maxlength="120" value="${e(item.title)}" data-field="item.title" data-key="${e(key)}" data-focus="item.title:${e(key)}" aria-label="Title of column ${index + 1}" placeholder="Column title"${invalid(flagged, `item.title:${key}`)}>${scored ? `<small>${plural(scored, "score")}</small>` : kept ? "<small>Cleared scores (history kept)</small>" : ""}</th>
      <td><select data-field="item.category" data-key="${e(key)}" data-focus="item.category:${e(key)}" aria-label="Category of ${e(label)}" title="${e(categoryName(item))}">${categoryOptions(item.category_id)}</select></td>
      <td><input type="number" min="0" max="100000" step="any" inputmode="decimal" value="${e(item.points)}" data-field="item.points" data-key="${e(key)}" data-focus="item.points:${e(key)}" aria-label="Points of ${e(label)}"${invalid(flagged, `item.points:${key}`)}></td>
      <td><input type="date" value="${e(item.due_date)}" data-field="item.due" data-key="${e(key)}" data-focus="item.due:${e(key)}" aria-label="Due date of ${e(label)}"${invalid(flagged, `item.due:${key}`)}></td>
      <td class="gbs-check"><input type="checkbox" data-field="item.counts" data-key="${e(key)}" data-focus="item.counts:${e(key)}" aria-label="${e(label)} counts toward the grade" ${item.counts ? "checked" : ""}></td>
      <td class="gbs-check"><input type="checkbox" data-field="item.extra" data-key="${e(key)}" data-focus="item.extra:${e(key)}" aria-label="${e(label)} is extra credit" ${item.extra_credit ? "checked" : ""}></td>
      <td class="gbs-check"><input type="checkbox" data-field="item.archived" data-key="${e(key)}" data-focus="item.archived:${e(key)}" aria-label="${e(label)} is archived" ${item.archived ? "checked" : ""}></td>
      <td class="gbs-row-actions">${moveButtons(e, "item", key, index, setup.items.length, label)}<button type="button" class="text-button gbs-more-toggle${hidden ? " gbs-flagged" : ""}" data-act="item-more" data-key="${e(key)}" data-focus="item-more:${e(key)}" aria-expanded="${expanded}" aria-controls="${e(moreId)}" aria-label="More settings of ${e(label)}: sections, linked assignment, replacement and notes${held.length ? ` (${e(held.join(", "))})` : ""}">More${held.length ? '<span class="gbs-more-dot" aria-hidden="true"></span>' : ""}</button><button type="button" class="icon-button gbs-remove" data-act="item-remove" data-key="${e(key)}" data-focus="item-remove:${e(key)}" aria-label="Remove ${e(label)}" title="${kept ? "It has score history: the server keeps a column with any score, cleared ones included. Archive it instead." : "Remove"}"><span aria-hidden="true">×</span></button></td></tr>
      <tr class="gbs-more" id="${e(moreId)}" data-more="${e(key)}"${expanded ? "" : " hidden"}><td colspan="8"><div class="gbs-more-grid">
        <div class="gbs-more-field${invalid(flagged, `item.sections:${key}`) ? " gbs-flagged" : ""}"><span class="gbs-more-label">Sections</span>${sectionsCell(ctx, item)}</div>
        <div class="gbs-more-field gbs-link"><span class="gbs-more-label">Linked assignment</span>${linkControls(ctx, item, { label })}</div>
        <label class="gbs-more-field"><span class="gbs-more-label">Replacement</span><span class="checkbox-field"><input type="checkbox" data-field="item.replaces" data-key="${e(key)}" data-focus="item.replaces:${e(key)}" ${item.replaces_lowest ? "checked" : ""}>Replaces the lowest score in its category when higher</span></label>
        <label class="gbs-more-field"><span class="gbs-more-label">Notes</span><input type="text" maxlength="1000" value="${e(item.notes)}" data-field="item.notes" data-key="${e(key)}" data-focus="item.notes:${e(key)}" aria-label="Notes on ${e(label)}" placeholder="Notes"${invalid(flagged, `item.notes:${key}`)}></label></div></td></tr></tbody>`;
  });
  return `<section class="gbs-panel card" aria-labelledby="gbs-items-title"><div class="gbs-panel-head"><h3 id="gbs-items-title">Columns</h3>
      <button type="button" class="button" data-act="item-add" data-focus="item-add">${ctx.icon("plus")}Add column</button></div>
    <p class="supporting-copy">In the order the gradebook shows them; a column with scores is archived, never removed.</p>
    ${howMarkup("items", "A column that does not count is shown and never counted; extra credit adds its earned points and never its possible points; an archived column is hidden and not counted, and keeps its scores. A column with scores cannot be removed: archive it. Link a column to an assignment (under More) to pull its final grades from grading. A replacement column (under More) never counts on its own: when its score is higher than the student’s lowest in its category, it takes that score’s place.")}
    ${findMarkup(ctx, setup, find)}
    ${setup.items.length ? `<div class="gbs-scroll gbs-scroll-wide" data-scroll="items" role="region" aria-label="Columns" tabindex="0"><table class="gbs-table gbs-items"><thead><tr><th scope="col">Column</th><th scope="col">Category</th><th scope="col">Points</th><th scope="col">Due</th><th scope="col">Counts</th><th scope="col">Extra credit</th><th scope="col">Archived</th><th scope="col"><span class="sr-only">Order, more settings and removal</span></th></tr></thead>${rows.join("")}</table></div>`
      : `<p class="gbs-empty">No columns yet. Add one here, link one from an assignment, or import a scores file from the gradebook.</p>`}
    ${removedMarkup(ctx, draft, "item")}</section>`;
}

// "Find a column" above a long Columns table (more than five columns), with an Uncategorized filter while a column has no category.
function findMarkup(ctx, setup, find) {
  if (setup.items.length <= 5) return "";
  const e = ctx.escape, loose = setup.items.filter(item => uncategorized(setup, item)).length;
  const chips = loose || find.only ? filterChipsMarkup("gbs-only", [{ value: "", label: "All", count: setup.items.length }, { value: "uncategorized", label: "Uncategorized", count: loose }], find.only || "", { label: "Show columns" }) : "";
  return `<div class="gbs-find-bar"><label class="field gbs-find">Find a column<input type="search" data-gbs-find data-focus="find" data-page-search placeholder="Column title" autocomplete="off" spellcheck="false" value="${e(find.query)}"></label>${chips}<p class="gbs-find-count field-hint" role="status" data-gbs-find-count>${e(findCount(setup, find))}</p></div>`;
}

function findCount(setup, find) {
  if (!find.query && !find.only) return "";
  const shown = setup.items.filter(item => columnShown(setup, item, find)).length;
  return shown ? `Showing ${shown} of ${plural(setup.items.length, "column")}.` : `No column matches: showing none of ${plural(setup.items.length, "column")}.`;
}

// Hide the rows the find leaves out, in place (typing in the find never redraws the editor).
function applyFind(root, termId) {
  const draft = drafts.get(termId);
  if (!draft) return;
  const find = finds.get(termId) || { query: "", only: "" };
  for (const body of root.querySelectorAll("tbody.gbs-item")) {
    const item = find_(draft.setup.items, body.getAttribute("data-item-key"));
    body.hidden = Boolean(item) && !columnShown(draft.setup, item, find);
  }
  const count = root.querySelector("[data-gbs-find-count]"), said = findCount(draft.setup, find);
  if (count && count.textContent !== said) count.textContent = said;
  for (const chip of root.querySelectorAll('[data-filter-group="gbs-only"] [data-filter]')) chip.setAttribute("aria-pressed", String((find.only || "") === chip.getAttribute("data-filter")));
}

// The find changed (typing, a chip): the columns kept in view because they were edited under the old find are let go.
function setFind(termId, change) {
  const { query = "", only = "" } = finds.get(termId) || {};
  finds.set(termId, { query, only, ...change, pinned: new Set() });
}

// A column edited while the find is on stays in view until the find changes, even when the edit means the find would now
// leave it out (a category chosen under "Uncategorized", a title retyped): the find is never cleared under the instructor's hands.
function pin(termId, field) {
  const find = finds.get(termId), key = field.dataset.key;
  if (!find || !(find.query || find.only) || !key || !field.dataset.field?.startsWith("item.")) return;
  (find.pinned ||= new Set()).add(key);
}

function scaleMarkup(ctx, draft, flagged) {
  const e = ctx.escape, scale = draft.setup.scale;
  const minimums = scale.map(entry => number(entry.min, 1000) ? Number(text(entry.min)) : null).filter(value => value !== null);
  const unsorted = minimums.some((value, index) => index > 0 && value >= minimums[index - 1]);
  const uppers = scaleUppers(scale);
  const rows = scale.map((entry, index) => `<tr><td><input type="text" maxlength="4" value="${e(entry.letter)}" data-field="scale.letter" data-index="${index}" data-focus="scale.letter:${index}" aria-label="Letter ${index + 1}"${invalid(flagged, `scale.letter:${index}`)}></td>
    <td><span class="gbs-min"><span class="gbs-upper" data-scale-upper="${index}">${e(uppers[index])}</span><span class="gbs-to">to</span><input type="number" min="0" max="1000" step="any" inputmode="decimal" value="${e(entry.min)}" data-field="scale.min" data-index="${index}" data-focus="scale.min:${index}" aria-label="Minimum percentage for ${e(text(entry.letter) || `letter ${index + 1}`)}"${invalid(flagged, `scale.min:${index}`)}><span aria-hidden="true">%</span></span></td>
    <td class="gbs-row-actions"><button type="button" class="icon-button gbs-remove" data-act="grade-remove" data-index="${index}" data-focus="grade-remove:${index}" aria-label="Remove ${e(text(entry.letter) || `letter ${index + 1}`)}" title="Remove" ${scale.length === 1 ? "disabled" : ""}><span aria-hidden="true">×</span></button></td></tr>`);
  return `<section class="gbs-panel card" aria-labelledby="gbs-scale-title"><div class="gbs-panel-head"><h3 id="gbs-scale-title">Grading scheme</h3>
      <div class="gbs-presets" role="group" aria-label="Start from a preset grading scheme"><span>Start from</span>${Object.entries(PRESETS).map(([name, preset]) => `<button type="button" class="text-button" data-act="preset" data-preset="${name}" data-focus="preset:${name}">${e(preset.label)}</button>`).join("")}</div></div>
    <p class="supporting-copy">Every section of the term uses it. A student’s letter is the first one, from the top, whose minimum their percentage reaches.</p>
    ${howMarkup("scale", "Minimums go down from the top letter; the last letter’s minimum is the floor. The grading method’s rounding, if any, is applied to the percentage before its letter is chosen.")}
    <div class="gbs-scroll" data-scroll="scale" role="region" aria-label="Grading scheme" tabindex="0"><table class="gbs-table gbs-scale"><thead><tr><th scope="col">Letter</th><th scope="col">Range</th><th scope="col"><span class="sr-only">Removal</span></th></tr></thead><tbody>${rows.join("")}</tbody></table></div>
    <div class="gbs-scale-actions"><button type="button" class="text-button" data-act="grade-add" data-focus="grade-add">${ctx.icon("plus")}Add a letter</button>${unsorted ? `<button type="button" class="text-button" data-act="grade-sort" data-focus="grade-sort">Sort by minimum</button><small class="field-hint">The minimums do not go down from the top letter; the server refuses a grading scheme out of order.</small>` : ""}</div>
    <p class="gbs-preview field-hint" data-scale-preview>${e(previewText(scale, ctx.view))}</p></section>`;
}

function feedbackMarkup(ctx, draft) {
  const e = ctx.escape, list = draft.showProblems ? problems(draft.setup) : [];
  return `${list.length ? `<p>Fix ${list.length === 1 ? "this" : "these"} before saving:</p><ul>${list.map(problem => `<li>${problem.focus ? `<a href="#" data-goto="${e(problem.focus)}">${e(problem.message)}</a>` : e(problem.message)}</li>`).join("")}</ul>` : ""}
    ${draft.error ? `<p role="alert">${e(draft.error)}</p>${draft.conflict ? `<p class="gbs-conflict-actions"><button type="button" class="button" data-act="reload">Load the saved setup</button><small>Your unsaved changes here are set aside.</small></p>` : ""}` : ""}`;
}

function markup(ctx, draft) {
  const e = ctx.escape, view = ctx.view, dirty = isDirty(draft, sectionIds(view));
  const flagged = new Set(draft.showProblems ? problems(draft.setup).map(problem => problem.focus) : []);
  // The page header names the term and says what setup is (gradebook.js nameHeader); its trail is the way back to the grid.
  return `<div class="gbs" ${draft.saving ? 'aria-busy="true"' : ""}>
    <fieldset class="gbs-form" ${draft.saving ? "disabled" : ""}><legend class="sr-only">Gradebook setup</legend>
      ${methodMarkup(ctx, draft.setup)}${categoriesMarkup(ctx, draft, flagged)}${itemsMarkup(ctx, draft, flagged)}${scaleMarkup(ctx, draft, flagged)}</fieldset>
    <div class="gbs-savebar" data-state="${draft.saving ? "saving" : dirty ? "dirty" : "saved"}">
      <p class="gbs-state" role="status" tabindex="-1" data-focus="state">${e(stateText(draft, view))}</p>
      <div class="gbs-actions"><button type="button" class="button" data-act="discard" ${dirty && !draft.saving ? "" : "hidden"}>Discard changes</button>
        <button type="button" class="button primary" data-act="save" ${dirty && !draft.saving ? "" : "disabled"}>${draft.saving ? "Saving…" : "Save setup"}</button></div>
      <div class="gbs-feedback" aria-live="polite" data-feedback>${feedbackMarkup(ctx, draft)}</div></div></div>`;
}

// ---------------------------------------------------------------------------------------------------------------------
// Drawing and events

/** @param {string} termId @param {{focus?: string}} [options] */
function draw(termId, { focus } = {}) {
  const slot = live.get(termId);
  // The grid's ctx.view names the term it shows; after it moved on to another term there is nothing to draw here.
  if (!slot || !slot.host.isConnected || !slot.ctx.view) return;
  const { host, ctx } = slot, draft = current(ctx);
  // Keep what the instructor was doing: keyboard focus, open section lists and each table's scroll position.
  const previous = host.querySelector(":scope > .gbs"), active = document.activeElement;
  const kept = active instanceof HTMLElement && previous?.contains(active) ? active.closest("[data-focus]")?.getAttribute("data-focus") : null;
  const scrolls = new Map([...(previous?.querySelectorAll("[data-scroll]") || [])].map(node => [node.getAttribute("data-scroll"), [node.scrollLeft, node.scrollTop]]));
  const open = new Set([...(previous?.querySelectorAll("details[data-keep][open]") || [])].map(node => node.getAttribute("data-keep")));
  host.innerHTML = markup(ctx, draft);
  const root = host.querySelector(":scope > .gbs");
  for (const node of root.querySelectorAll("[data-scroll]")) {
    const position = scrolls.get(node.getAttribute("data-scroll"));
    if (position) [node.scrollLeft, node.scrollTop] = position;
  }
  for (const node of root.querySelectorAll("details[data-keep]")) if (open.has(node.getAttribute("data-keep"))) node.open = true;
  bind(root, termId);
  const target = focus || kept;
  if (!target) return;
  let node = root.querySelector(`[data-focus="${CSS.escape(target)}"]`);
  // A move button at the end of its list is disabled after the move: its neighbour keeps the focus.
  if (node instanceof HTMLButtonElement && node.disabled) node = node.parentElement?.querySelector("button:not([disabled])") || null;
  if (node instanceof HTMLElement) {
    reveal(termId, node);
    node.focus({ preventScroll: !focus });
  }
}

// A field inside a closed "More" row or a closed section list is opened before it takes the focus (a problem's link, a redraw).
function reveal(termId, node) {
  const body = node.closest("tbody.gbs-item");
  if (body instanceof HTMLElement && body.hidden) {
    // A field the find hides (a problem's link, a new column; never one just edited, which `pin` keeps in view): the find steps
    // aside so it can be seen.
    finds.delete(termId);
    const root = node.closest(".gbs");
    const input = root?.querySelector("[data-gbs-find]");
    if (input instanceof HTMLInputElement) input.value = "";
    if (root instanceof HTMLElement) applyFind(root, termId);
  }
  node.closest("details")?.setAttribute("open", "");
  const row = node.closest(".gbs-more");
  if (row instanceof HTMLElement && row.hidden) {
    row.hidden = false;
    const key = row.getAttribute("data-more") || "";
    more.set(termId, (more.get(termId) || new Set()).add(key));
    row.previousElementSibling?.querySelector('[data-act="item-more"]')?.setAttribute("aria-expanded", "true");
  }
}

// Only the save state, the weights note, the problem list and category names in the column selects follow typing.
function refresh(root, termId) {
  const slot = live.get(termId), draft = drafts.get(termId);
  if (!slot || !draft || !slot.ctx.view) return;
  const { ctx } = slot, dirty = isDirty(draft, sectionIds(ctx.view));
  const state = root.querySelector(".gbs-state"), said = stateText(draft, ctx.view);
  if (state.textContent !== said) state.textContent = said; // a live region: only a real change is announced
  root.querySelector(".gbs-savebar").dataset.state = draft.saving ? "saving" : dirty ? "dirty" : "saved";
  root.querySelector('[data-act="save"]').disabled = !dirty || draft.saving;
  root.querySelector('[data-act="discard"]').hidden = !dirty || draft.saving;
  const weights = root.querySelector("[data-weights]");
  if (weights) weights.textContent = weightsNote(draft.setup);
  const preview = root.querySelector("[data-scale-preview]"), previewed = previewText(draft.setup.scale, ctx.view);
  if (preview && preview.textContent !== previewed) preview.textContent = previewed;
  // Each letter's range starts below the minimum of the letter above it: typing a minimum moves the next row's.
  scaleUppers(draft.setup.scale).forEach((upper, index) => {
    const node = root.querySelector(`[data-scale-upper="${index}"]`);
    if (node && node.textContent !== upper) node.textContent = upper;
  });
  for (const category of draft.setup.categories) {
    for (const option of root.querySelectorAll(`option[data-cat="${CSS.escape(category.$key)}"]`)) option.textContent = text(category.name) || "Unnamed category";
  }
  for (const select of root.querySelectorAll('select[data-field="item.category"]')) {
    const name = select.selectedOptions[0]?.textContent || "";
    if (select.title !== name) select.title = name;
  }
  if (draft.showProblems) {
    const flagged = new Set(problems(draft.setup).map(problem => problem.focus));
    for (const node of root.querySelectorAll("input[data-focus]")) {
      if (flagged.has(node.dataset.focus)) node.setAttribute("aria-invalid", "true");
      else node.removeAttribute("aria-invalid");
    }
    const feedback = root.querySelector("[data-feedback]"), next = feedbackMarkup(ctx, draft);
    if (shown.get(feedback) !== next) { feedback.innerHTML = next; shown.set(feedback, next); }
  }
}

function find(list, key) { return list.find(entry => entry.$key === key); }
const find_ = find; // for the places where `find` names the Columns table's find state

function update(draft, field, view) {
  const setup = draft.setup, name = field.dataset.field, key = field.dataset.key;
  const category = () => find(setup.categories, key), item = () => find(setup.items, key);
  const entry = () => setup.scale[Number(field.dataset.index)];
  const order = sectionIds(view);
  switch (name) {
    case "mode": setup.mode = field.value; break;
    case "ungraded": setup.ungraded = field.value; break;
    case "letter_rounding": setup.letter_rounding = field.value === "" ? null : Number(field.value); break;
    case "cat.name": category().name = field.value; break;
    case "cat.weight": category().weight = field.value; break;
    case "cat.drop": category().drop_lowest = field.value; break;
    case "cat.aggregate": category().aggregate = field.value; break;
    case "cat.posting": category().posting = field.value === "auto" ? { due: true, certified: true } : null; break;
    case "cat.posting-due":
    case "cat.posting-certified": {
      const posting = { due: false, certified: false, ...(category().posting || {}), [name.slice("cat.posting-".length)]: field.checked };
      category().posting = posting.due || posting.certified ? posting : null;
      break;
    }
    case "item.title": item().title = field.value; break;
    case "item.category": item().category_id = field.value || null; break;
    case "item.points": item().points = field.value; break;
    case "item.due": item().due_date = field.value; break;
    case "item.counts": item().counts = field.checked; break;
    case "item.extra": item().extra_credit = field.checked; break;
    case "item.replaces": item().replaces_lowest = field.checked; break;
    case "item.archived": item().archived = field.checked; break;
    case "item.notes": item().notes = field.value; break;
    case "item.all-sections": {
      const column = item();
      column.sections = [];
      column.$specific = !field.checked;
      break;
    }
    case "item.section": {
      const column = item(), chosen = new Set(column.sections);
      if (field.checked) chosen.add(field.dataset.section); else chosen.delete(field.dataset.section);
      column.sections = [...chosen].sort((a, b) => order.indexOf(a) - order.indexOf(b));
      column.$specific = true;
      break;
    }
    case "item.assignment": linkAssignment(item(), field.value, view); break;
    case "item.part": togglePart(item(), field.dataset.part, field.checked); break;
    case "scale.letter": entry().letter = field.value; break;
    case "scale.min": entry().min = field.value; break;
  }
}

/** Choosing a column's assignment ticks every part of its grading this term; a blank title takes the assignment's. */
export function linkAssignment(item, assignmentId, view) {
  const assignment = (view.assignments || []).find(entry => entry.id === assignmentId);
  item.assignment_id = assignmentId || null;
  item.link = assignment && (assignment.parts || []).length ? { kind: "grading", parts: PART_ORDER.filter(part => assignment.parts.includes(part)) } : null;
  if (assignment && !text(item.title)) item.title = assignment.title;
}

export function togglePart(item, part, on) {
  const parts = new Set(item.link?.parts || []);
  if (on) parts.add(part); else parts.delete(part);
  item.link = parts.size ? { ...(item.link || {}), kind: "grading", parts: [...PART_ORDER.filter(entry => parts.has(entry)), ...[...parts].filter(entry => !PART_ORDER.includes(entry))] } : null;
}

function move(list, key, step) {
  const index = list.findIndex(entry => entry.$key === key), target = index + step;
  if (index < 0 || target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
}

function act(termId, button) {
  const slot = live.get(termId), draft = drafts.get(termId);
  if (!slot || !draft || draft.saving && button.dataset.act !== "reload") return;
  const { ctx } = slot, setup = draft.setup, key = button.dataset.key;
  let focus = button.dataset.focus;
  switch (button.dataset.act) {
    case "save": void save(termId); return;
    case "reload": void reload(termId, button); return;
    case "discard": drafts.delete(termId); draw(termId, { focus: "state" }); ctx.toast("Setup changes discarded."); return;
    case "cat-add": { const key = newKey("cat"); setup.categories.push({ $key: key, name: "", weight: "0", drop_lowest: "0", aggregate: "points" }); focus = `cat.name:${key}`; break; }
    case "cat-up": move(setup.categories, key, -1); break;
    case "cat-down": move(setup.categories, key, 1); break;
    case "cat-remove": {
      const index = setup.categories.findIndex(entry => entry.$key === key);
      if (index < 0 || setup.items.some(item => item.category_id === key)) return;
      draft.removed.push({ kind: "category", entry: setup.categories.splice(index, 1)[0], index });
      focus = "cat-add";
      break;
    }
    case "item-add": { const item = blankItem(); setup.items.push(item); focus = `item.title:${item.$key}`; break; }
    case "item-more": { // view memory only: the draft is unchanged
      const open = more.get(termId) || new Set();
      if (open.has(key)) open.delete(key); else open.add(key);
      more.set(termId, open);
      draw(termId, { focus });
      return;
    }
    case "item-up": move(setup.items, key, -1); break;
    case "item-down": move(setup.items, key, 1); break;
    case "item-remove": {
      const index = setup.items.findIndex(entry => entry.$key === key);
      if (index < 0) return;
      draft.removed.push({ kind: "item", entry: setup.items.splice(index, 1)[0], index });
      focus = "item-add";
      break;
    }
    case "undo": {
      const [restored] = draft.removed.splice(Number(button.dataset.index), 1);
      if (!restored) return;
      const list = restored.kind === "item" ? setup.items : setup.categories;
      list.splice(Math.min(restored.index, list.length), 0, restored.entry);
      focus = restored.kind === "item" ? `item.title:${restored.entry.$key}` : `cat.name:${restored.entry.$key}`;
      break;
    }
    case "grade-add": {
      const at = Math.max(0, setup.scale.length - 1);
      setup.scale.splice(at, 0, { letter: "", min: "" });
      focus = `scale.letter:${at}`;
      break;
    }
    case "grade-remove": {
      if (setup.scale.length <= 1) return;
      setup.scale.splice(Number(button.dataset.index), 1);
      focus = "grade-add";
      break;
    }
    case "grade-sort": {
      const value = entry => number(entry.min, 1000) ? Number(text(entry.min)) : -Infinity;
      setup.scale = [...setup.scale].sort((a, b) => value(b) - value(a));
      focus = "grade-add";
      break;
    }
    case "preset": {
      const preset = PRESETS[button.dataset.preset];
      if (!preset) return;
      setup.scale = preset.scale.map(([letter, min]) => ({ letter, min }));
      ctx.toast(`Grading scheme set to ${preset.label}. Save the setup to keep it.`);
      break;
    }
    default: return;
  }
  touched(draft);
  draw(termId, { focus });
}

function bind(root, termId) {
  root.addEventListener("input", event => {
    if (event.target instanceof HTMLInputElement && event.target.matches("[data-gbs-find]")) {
      setFind(termId, { query: event.target.value });
      applyFind(root, termId);
    }
  });
  // The chip already pressed applies its filter afresh (the columns edited under it are let go); another chip switches to it.
  root.addEventListener("click", event => {
    const chip = event.target instanceof Element ? event.target.closest('[data-filter-group="gbs-only"] [data-filter][aria-pressed="true"]') : null;
    if (!chip) return;
    setFind(termId, {});
    applyFind(root, termId);
  });
  bindFilterChips(root, "gbs-only", value => {
    setFind(termId, { only: value });
    applyFind(root, termId);
  });
  root.addEventListener("input", event => {
    const field = event.target instanceof Element ? event.target.closest("[data-field]") : null;
    const draft = drafts.get(termId), slot = live.get(termId);
    if (!field || !TYPED.has(field.dataset.field) || !draft || !slot || draft.saving) return;
    pin(termId, field);
    update(draft, field, slot.ctx.view);
    touched(draft);
    refresh(root, termId);
  });
  root.addEventListener("change", event => {
    const field = event.target instanceof Element ? event.target.closest("[data-field]") : null;
    const draft = drafts.get(termId), slot = live.get(termId);
    if (!field || TYPED.has(field.dataset.field) || !draft || !slot || draft.saving) return;
    pin(termId, field);
    update(draft, field, slot.ctx.view);
    touched(draft);
    draw(termId, { focus: field.dataset.focus });
  });
  root.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : null;
    const link = target?.closest("[data-goto]");
    if (link) {
      event.preventDefault();
      const node = root.querySelector(`[data-focus="${CSS.escape(link.getAttribute("data-goto"))}"]`);
      if (node instanceof HTMLElement) { reveal(termId, node); node.focus(); }
      return;
    }
    const button = target?.closest("[data-act]");
    if (button instanceof HTMLButtonElement && !button.disabled) act(termId, button);
  });
}

// ---------------------------------------------------------------------------------------------------------------------
// Saving

async function save(termId) {
  const slot = live.get(termId), draft = drafts.get(termId);
  if (!slot || !draft || draft.saving) return;
  const { ctx } = slot, url = `/api/gradebook/terms/${encodeURIComponent(termId)}/setup`;
  if (problems(draft.setup).length) {
    draft.showProblems = true;
    draw(termId);
    live.get(termId)?.host.querySelector("[data-goto]")?.focus();
    return;
  }
  draft.saving = true;
  draft.error = "";
  draft.conflict = false;
  draw(termId);
  try {
    draft.requestId ||= ctx.requestId();
    const order = sectionIds(live.get(termId)?.ctx.view || ctx.view);
    const result = await ctx.api(url, "POST", { base_revision: draft.base, request_id: draft.requestId, setup: setupPayload(draft.setup, order), ...(ctx.scopeSection ? { section_id: ctx.scopeSection } : {}) });
    drafts.delete(termId);
    (live.get(termId)?.ctx || ctx).replaceView(result);
    ctx.toast("Gradebook setup saved");
    // Back to the grid the setup was opened from (a section's or the course Home's gradebook tab).
    const grid = ctx.gridHref || `#gradebook/${encodeURIComponent(termId)}`;
    if (location.hash.split("?")[0] === `${grid.split("?")[0]}/setup`) location.hash = grid;
  } catch (cause) {
    const failed = /** @type {any} */ (cause);
    draft.saving = false;
    draft.conflict = failed?.status === 409;
    draft.error = draft.conflict
      ? `${failed.message || "The gradebook setup was saved elsewhere."} Nothing was saved from this page.`
      : failed?.message || "The setup could not be saved. Try again.";
    draw(termId, { focus: undefined });
    live.get(termId)?.host.querySelector(".gbs-feedback")?.scrollIntoView({ block: "nearest" });
  }
}

async function reload(termId, button) {
  const slot = live.get(termId), draft = drafts.get(termId);
  if (!slot) return;
  setBusy(button, true);
  if (draft) draft.release = true;
  try {
    const view = await slot.ctx.reload();
    drafts.delete(termId);
    const latest = live.get(termId);
    if (latest && view && (latest.ctx.view.setup?.revision ?? null) !== (view.setup?.revision ?? null)) live.set(termId, { host: latest.host, ctx: { ...latest.ctx, view } });
    draw(termId, { focus: "state" }); // the button pressed is gone: the save state says what is shown
    slot.ctx.toast("The saved setup is shown.");
  } catch (cause) {
    if (draft) { draft.release = false; draft.error = /** @type {any} */ (cause)?.message || "The setup could not be loaded."; }
    draw(termId);
  }
}

/** Draw the setup editor of ctx.termId into host. Safe to call again at any time (a new View, a re-render). */
export function renderSetup(host, ctx) {
  if (!ctx.view) throw new Error("This gradebook is not loaded. Reload the page and try again.");
  live.set(ctx.termId, { host, ctx });
  current(ctx);
  draw(ctx.termId);
}
