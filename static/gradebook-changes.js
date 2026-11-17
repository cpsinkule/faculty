// The gradebook's changes (#course/<gid>/section/<n>/changes; gradebook/README.md "Changes"; requirement, 2026-10-01): every revision of the term's setup and every score write, import, pull or certification that touched this
// section, newest first, each with what it changed. Read-only: it reads GET …/changes a page at a time and, for a request too
// large to list with its page, GET …/changes/<request> when the instructor opens it; it writes nothing. The server sends row
// keys and column IDs only: student names and column titles come from the View on screen.

import { emptyMarkup, plural, tableMarkup } from "./ui.js";
import { actorText, trimPoints } from "./gradebook.js";

const PARTS = { frq: "written work", bubble: "bubble sheets", exams: "the Exams page" };
// What a pull or an import found besides what it wrote (its summary's counts), in its entry's details.
const FOUND = {
  same: "already held that value", kept: "kept (typed or cleared in Faculty)", waiting: "waiting for grading", none: "with nothing in grading",
  differs: "compared only (Faculty grades that column)", faculty_only: "graded in Faculty only", unreadable: "unreadable",
  unmatched: "not matched to a student", blank: "blank",
};
const LONG = 60000;

const listText = (titles) => (titles.length > 2 ? `${titles.slice(0, 2).join(", ")} and ${titles.length - 2} more` : titles.join(" and "));
const moment = (value) => { const date = new Date(value); return Number.isNaN(date.getTime()) ? null : date; };

/** A cell's value as this page shows it: points, Excused, Missing, or — when it holds none (empty or cleared). */
export function valueWords(value) {
  if (!value || value.status === "cleared") return "—";
  if (value.status === "scored") return trimPoints(value.points);
  return value.status === "excused" ? "Excused" : value.status === "missing" ? "Missing" : String(value.status);
}

/** What made an entry (the server's `origin`, gradebook/changes.py), in words; `titleOf` names a column ID. The assistant's own
 * entries (actor `assistant`, request_actor.py) say so: scores it entered, or "· by the assistant" after anything else. */
export function originLabel(entry, titleOf = () => "") {
  const label = originWords(entry, titleOf);
  if (entry.actor !== "assistant") return label;
  return entry.origin === "typed" ? "Entered by the assistant" : `${label} · by ${actorText(entry.actor)}`;
}

function originWords(entry, titleOf) {
  if (entry.type === "setup") {
    if (entry.automatic !== "webassign") return "Setup changed";
    return `${(entry.changes || []).length > 1 ? "Due dates" : "Due date"} updated from Canvas overnight`;
  }
  const summary = entry.summary || {};
  const column = titleOf(summary.item_id) || "a column";
  const parts = (summary.parts || []).map((part) => PARTS[part] || part).join(", ");
  switch (entry.origin) {
    case "typed": return "Typed in Faculty";
    case "absence": return summary.action === "put_back" ? "iClicker credit an absence gave, taken back" : "Full iClicker credit for an excused absence";
    case "iclicker_credit": return summary.action === "put_back" ? "iClicker credit you gave, taken back" : "Full iClicker credit you gave";
    case "file": return summary.filename ? `Imported ${summary.filename}` : "Imported a file";
    case "canvas":
      if (summary.skipped) return `Left out of Sync with Canvas: ${listText(summary.skipped.map((title) => `“${title}”`))}`;
      if (summary.automatic === "webassign") return "Copied from Canvas by the nightly WebAssign import";
      // SYNC WITH CANVAS: what came in from Canvas, and what the sync sent (Canvas's answers are its receipts, not gradebook changes).
      if (summary.sync) return `Synced with Canvas${summary.automatic === "canvas-sync" ? " by the morning sync" : ""}${summary.sync.sending ? ` · ${plural(summary.sync.sending, "grade")} sent` : ""}${summary.sync.held ? ` · ${plural(summary.sync.held, "grade")} held` : ""}`;
      return "Imported from Canvas";
    case "iclicker": return summary.automatic === "iclicker" ? "Imported from iClicker after class" : "Imported from iClicker";
    case "certification": return entry.actor === "automatic" ? `Recorded ${column} from a certification Faculty made by itself` : `Recorded ${column} from its certification`;
    case "pull": return summary.certification_id ? `Pulled ${column} from its certification` : `Pulled ${column} from grading${parts ? ` (${parts})` : ""}`;
    default: return String(entry.kind || "Change");
  }
}

/** One line under the label. A request that wrote one or two cells names them ("Name · Column: before → after"); a larger one
 * its columns and counts; a setup revision its first change. `nameOf` names a row key, `titleOf` a column ID. */
export function entrySummary(entry, { nameOf = () => "", titleOf = () => "" } = {}) {
  const firstOf = (lines) => (lines.length ? `${lines[0]}${lines.length > 1 ? ` and ${plural(lines.length - 1, "more change")}` : ""}` : "");
  if (entry.type === "setup") return firstOf(entry.changes || []) || "A change this page does not describe";
  const cells = entry.cells || {};
  if (entry.detail?.length && entry.detail.length <= 2) {
    return entry.detail.map((cell) => `${nameOf(cell.row) || "A student"} · ${titleOf(cell.item_id) || "a column"}: ${valueWords(cell.before)} → ${valueWords(cell.after)}`).join("; ");
  }
  if (cells.here) {
    const counts = [cells.added && `${cells.added} added`, cells.changed && `${cells.changed} changed`, cells.cleared && `${cells.cleared} cleared`].filter(Boolean).join(", ");
    return [listText((entry.items || []).map(titleOf).filter(Boolean)), counts].filter(Boolean).join(": ");
  }
  const setup = firstOf(entry.setup?.changes || []);
  if (setup) return setup;
  return cells.elsewhere ? `No scores in this section (${plural(cells.elsewhere, "score")} in other sections)` : "No scores written";
}

/** What a pull or an import found besides what it wrote, from its summary's counts ("3 kept (typed or cleared in Faculty)"). */
export function foundText(entry) {
  const counts = entry.summary?.counts;
  if (entry.type !== "request" || !counts || typeof counts !== "object") return "";
  return Object.entries(FOUND).filter(([key]) => Number(counts[key]) > 0).map(([key, words]) => `${counts[key]} ${words}`).join(", ");
}

/**
 * Draws the Changes page into `slot` and keeps it: Show older appends the next page, Refresh reads the newest again, and
 * opening a request whose cells were not listed with its page reads them once.
 * @param {HTMLElement} slot
 * @param {import("./gradebook.js").GradebookContext & {scopeSection: string, gridHref: string}} ctx
 */
export async function renderChanges(slot, ctx) {
  const e = ctx.escape;
  const base = `/api/gradebook/terms/${encodeURIComponent(ctx.termId)}/changes`;
  const section = ctx.scopeSection || "";
  const query = (extra = {}) => { const params = new URLSearchParams({ ...(section ? { section } : {}), ...extra }); return params.size ? `?${params}` : ""; };
  const state = { entries: [], next: null, open: new Set(), cells: new Map(), failed: new Set(), loading: new Map(), older: false };
  let generation = 0;

  const view = () => ctx.view || {};
  let indexedView = null, names = new Map(), titles = new Map(), positions = new Map();
  function indexView() {
    const next = view();
    if (next === indexedView) return;
    indexedView = next;
    names = new Map((next.students || []).map((student) => [student.row, student.name]));
    titles = new Map((next.setup?.items || []).map((item) => [item.id, item.title]));
    positions = new Map((next.setup?.items || []).map((item, index) => [item.id, index]));
  }
  const nameOf = (row) => { indexView(); return names.get(row) || ""; };
  const titleOf = (id) => { indexView(); return titles.get(id) || ""; };
  const order = (id) => { indexView(); return positions.get(id) ?? Infinity; };

  function cellsMarkup(entry, cells) {
    const rows = [...cells].sort((a, b) => order(a.item_id) - order(b.item_id) || (nameOf(a.row) || "").localeCompare(nameOf(b.row) || ""))
      .map((cell) => `<tr><td>${e(nameOf(cell.row) || "A student no longer on this roster")}</td><td>${e(titleOf(cell.item_id) || "A removed column")}</td>
        <td>${e(valueWords(cell.before))}</td><td><strong>${e(valueWords(cell.after))}</strong></td><td class="gbc-note">${e(cell.after?.note || "")}</td></tr>`);
    return tableMarkup(e, ["Student", "Column", "Before", "After", "Note"], rows, `Scores written: ${originLabel(entry, titleOf)}`);
  }

  function cellsSlot(entry) {
    const here = entry.cells?.here || 0;
    if (!here) return "";
    const cells = entry.detail || state.cells.get(entry.id);
    if (cells) return cellsMarkup(entry, cells);
    if (state.failed.has(entry.id)) {
      return `<div class="callout" data-tone="warning" role="status"><strong class="callout-title">The scores of this change could not be read.</strong><button type="button" class="button callout-action" data-gbc-retry="${e(entry.id)}">Try again</button></div>`;
    }
    return `<p class="loading-state">Loading ${e(plural(here, "score"))}…</p>`;
  }

  function bodyMarkup(entry) {
    const parts = [];
    if (entry.type === "setup") {
      parts.push(`<p class="gbc-meta">Setup revision ${e(entry.revision)}, shared by every section of the term.</p>`);
      if (entry.changes?.length) parts.push(`<ul class="gbc-lines">${entry.changes.map((line) => `<li>${e(line)}</li>`).join("")}</ul>`);
    } else {
      if (entry.setup) {
        parts.push(`<p class="gbc-meta">It also saved setup revision ${e(entry.setup.revision)}, shared by every section of the term:</p>`);
        if (entry.setup.changes?.length) parts.push(`<ul class="gbc-lines">${entry.setup.changes.map((line) => `<li>${e(line)}</li>`).join("")}</ul>`);
      }
      const found = foundText(entry);
      if (found) parts.push(`<p class="gbc-meta">It also found: ${e(found)}.</p>`);
      if (entry.cells?.elsewhere) parts.push(`<p class="gbc-meta">${e(`It also wrote ${plural(entry.cells.elsewhere, "score")} in other sections; their tabs list them.`)}</p>`);
      if (!entry.cells?.here && !entry.cells?.elsewhere) parts.push('<p class="gbc-meta">It wrote no scores.</p>');
      parts.push(`<div data-gbc-cells="${e(entry.id)}">${cellsSlot(entry)}</div>`);
    }
    // The assistant is named in the label; any other actor than the instructor's workspace is named here.
    if (entry.actor && entry.actor !== "workspace" && entry.actor !== "assistant") parts.push(`<p class="gbc-meta">${e(`By ${actorText(entry.actor)}`)}</p>`);
    return parts.join("");
  }

  function entryMarkup(entry) {
    const at = moment(entry.created_at);
    const time = at ? at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "";
    return `<li class="gbc-entry" data-gbc-entry="${e(entry.id)}" data-origin="${e(entry.type === "setup" ? "setup" : entry.origin)}">
      <details class="disclosure"${state.open.has(entry.id) ? " open" : ""}><summary><span><strong>${e(originLabel(entry, titleOf))}</strong><span class="gbc-what">${e(entrySummary(entry, { nameOf, titleOf }))}</span></span>${time ? `<span class="disclosure-hint"><time datetime="${e(entry.created_at)}">${e(time)}</time></span>` : ""}</summary>
      <div class="disclosure-body">${bodyMarkup(entry)}</div></details></li>`;
  }

  function draw() {
    if (!slot.isConnected) return;
    if (!state.entries.length) {
      slot.innerHTML = `<div class="gbc">${emptyMarkup({ icon: "book", title: "No changes yet", heading: "h2",
        body: "Setup saves, typed scores, imports, pulls and certifications are listed here as they happen." })}</div>`;
      return;
    }
    const days = [];
    for (const entry of state.entries) {
      const at = moment(entry.created_at);
      const key = at ? at.toDateString() : "";
      if (days.at(-1)?.key !== key) days.push({ key, at, entries: [] });
      days.at(-1).entries.push(entry);
    }
    const focused = /** @type {HTMLElement|null} */ (document.activeElement)?.closest?.("[data-gbc-entry]")?.getAttribute("data-gbc-entry") || "";
    slot.innerHTML = `<div class="gbc">
      <div class="gradebook-bar"><p class="gbc-meta">${e(state.next ? `The latest ${plural(state.entries.length, "change")}.` : plural(state.entries.length, "change"))}</p>
        <div class="gradebook-tools"><button type="button" class="text-button" data-gbc-refresh>${ctx.icon("refresh")}Refresh</button></div></div>
      ${days.map((day, index) => `<section class="gbc-day" aria-labelledby="gbc-day-${index}"><h2 class="gbc-day-title" id="gbc-day-${index}">${e(day.at ? day.at.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }) : "Date unknown")}</h2>
        <ol class="gbc-list">${day.entries.map(entryMarkup).join("")}</ol></section>`).join("")}
      ${state.next ? `<button type="button" class="button gbc-older" data-gbc-older${state.older ? " disabled" : ""}>${state.older ? "Loading older changes…" : "Show older changes"}</button>` : ""}</div>`;
    if (focused) /** @type {HTMLElement|null} */ (slot.querySelector(`[data-gbc-entry="${CSS.escape(focused)}"] summary`))?.focus({ preventScroll: true });
    for (const id of state.open) void loadCells(id); // an entry kept open across Refresh reads its cells again
  }

  // A request too large to list with its page: its cells are read once, when it is first opened (and again on Try again).
  async function loadCells(id) {
    const entry = state.entries.find((item) => item.id === id);
    if (!entry || entry.detail || state.cells.has(id) || state.loading.has(id) || !entry.cells?.here) return;
    const token = generation;
    state.loading.set(id, token);
    state.failed.delete(id);
    try {
      const result = await ctx.api(`${base}/${encodeURIComponent(id)}${query()}`, "GET", undefined, {}, LONG);
      if (token !== generation || !slot.isConnected) return;
      state.cells.set(id, result?.cells || []);
    } catch {
      if (token !== generation || !slot.isConnected) return;
      state.failed.add(id);
    } finally {
      if (state.loading.get(id) === token) state.loading.delete(id);
    }
    if (token !== generation) return;
    const host = slot.querySelector(`[data-gbc-cells="${CSS.escape(id)}"]`);
    if (host) host.innerHTML = cellsSlot(entry);
  }

  async function read(older = false) {
    const token = older ? generation : ++generation;
    if (!older) state.loading.clear(); // Refresh owns new detail reads even while the older ones finish.
    const result = await ctx.api(`${base}${query(older && state.next ? { before: state.next } : {})}`, "GET", undefined, {}, LONG);
    if (token !== generation || !slot.isConnected) return;
    if (older) state.entries.push(...(result?.changes || []).filter((entry) => !state.entries.some((shown) => shown.id === entry.id)));
    else { state.entries = result?.changes || []; state.cells.clear(); state.failed.clear(); }
    state.next = result?.next || null;
  }

  slot.addEventListener("toggle", (event) => {
    const details = /** @type {HTMLElement} */ (event.target);
    const id = details.closest?.("[data-gbc-entry]")?.getAttribute("data-gbc-entry");
    if (!id || !(details instanceof HTMLDetailsElement)) return;
    if (details.open) { state.open.add(id); void loadCells(id); } else state.open.delete(id);
  }, true); // toggle does not bubble; a capturing listener still hears it
  slot.addEventListener("click", async (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const retry = target.closest("[data-gbc-retry]");
    if (retry) { void loadCells(retry.getAttribute("data-gbc-retry") || ""); return; }
    if (target.closest("[data-gbc-older]") && !state.older) {
      state.older = true;
      draw();
      try { await read(true); } catch (error) { ctx.toast(error?.message || "Older changes could not be read.", true); }
      state.older = false;
      draw();
      return;
    }
    if (target.closest("[data-gbc-refresh]")) {
      try { await read(); draw(); } catch (error) { ctx.toast(error?.message || "The changes could not be read.", true); draw(); }
    }
  });

  try {
    await read();
  } catch (error) {
    if (!slot.isConnected) return;
    slot.innerHTML = `<div class="callout" data-tone="danger" role="alert"><strong class="callout-title">${e(error?.message || "The changes could not be read.")}</strong><button type="button" class="button callout-action" data-gbc-refresh>Try again</button></div>`;
    return;
  }
  draw();
}
