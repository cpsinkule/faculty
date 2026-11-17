// @ts-check
// Print files (UX overhaul W2-DOCS task 5, 2026-09-26): ONE renderer for what the instructor downloads to print: a section
// job's PDF and labels, and a seating chart export's whole-room files in seat order. The batch dialog, the grouped print
// jobs list and the assignment window's last step use it, so every place names the files and label media the same way.
//
// The primary file follows the job: labels for stamps-only paper (on the label media last used in this browser),
// the whole room in seat order for a seating chart export, otherwise the section's print PDF. Every other file sits under
// "Other formats". Links only: the server still decides what is downloadable (ready and current jobs; a room file only
// once every section's job is ready), and nothing here creates, issues or changes anything.
import { escape, actionMenuMarkup } from "./ui.js";
import { viewLink } from "./file-viewer.js";

/** Label media, named once for the whole app. */
export const MEDIA = [
  { id: "dk-1201", label: "Brother QL-800 · 29 × 90 mm (DK-1201)", short: "DK-1201" },
  { id: "dk-1209", label: "Brother QL-800 · 62 × 29 mm (DK-1209)", short: "DK-1209" },
  { id: "avery-5160", label: "Avery 5160 sheet", short: "Avery 5160" },
];
const DEFAULT_MEDIA = "dk-1201";
const MEDIA_KEY = "faculty:label-media";

/** The label media this browser used last (a per-viewer convenience; the default when storage is unavailable). */
export function rememberedMedia() {
  try {
    const stored = globalThis.localStorage?.getItem(MEDIA_KEY);
    return MEDIA.some((item) => item.id === stored) ? /** @type {string} */ (stored) : DEFAULT_MEDIA;
  } catch {
    return DEFAULT_MEDIA;
  }
}

/** @param {string} id */
export function rememberMedia(id) {
  if (!MEDIA.some((item) => item.id === id)) return;
  try { globalThis.localStorage?.setItem(MEDIA_KEY, id); } catch { /* a convenience only */ }
}

/** A stamps-only version prints no exam: its labels are the deliverable. @param {{label?: string, version_label?: string, compiler_json?: string} | null | undefined} version */
export function isStampsOnly(version) {
  if (!version) return false;
  try {
    if (version.compiler_json && JSON.parse(version.compiler_json)?.generator === "faculty-stamps-only") return true;
  } catch { /* fall back to the label register_stamp_pages gives */ }
  return /^Stamps only · /.test(String(version.label ?? version.version_label ?? ""));
}

/** A job's files can be downloaded only while it is ready and not superseded. @param {{status?: string, superseded_at?: string | null}} batch */
export const downloadable = (batch) => batch.status === "ready" && !batch.superseded_at;

/** `batches=a%2Cb`: the comma percent-encoded, since a file route the viewer opens has no bare comma (file-viewer.js viewableRoute,
 * app_files.py ROUTE); a bare one left the click doing nothing. @param {{id: string}[]} batches */
const batchesQuery = (batches) => `batches=${encodeURIComponent(batches.map((batch) => batch.id).join(","))}`;

let bound = false;
/** Remember the media of any label link the instructor follows (bound once, on the document). */
export function bindPrintFiles(doc = globalThis.document) {
  if (bound || !doc) return;
  bound = true;
  doc.addEventListener("click", (event) => {
    const link = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target)?.closest?.("a[href*='labels?']"));
    if (!link) return;
    const media = new URL(link.getAttribute("href") || "", globalThis.location?.href || "http://localhost/").searchParams.get("media");
    if (media) rememberMedia(media);
  }, true);
}

/**
 * @typedef {{label: string, href: string, short?: string}} FileLink
 * @param {FileLink} primary
 * @param {FileLink[]} others
 * @param {{compact?: boolean, primary?: boolean, id: string, extra?: string, secondary?: FileLink, scantron?: FileLink, menuLabel?: string}} options
 */
function filesMarkup(primary, others, { compact = false, primary: cream = true, id, extra = "", secondary, scantron, menuLabel = "Other formats" }) {
  const button = (/** @type {FileLink | undefined} */ item) => item ? `<a class="button print-files-secondary" ${viewLink(item.href, item.label, escape)} title="${escape(item.label)}">${escape(item.short || item.label)}</a>` : '';
  const second = button(secondary) + button(scantron);
  if (compact) {
    const menu = others.length ? actionMenuMarkup({ id: `files-${id}`, label: menuLabel, items: others.map((item) => ({ ...item, data: { viewFile: item.href, viewName: item.label } })) }) : "";
    // A list header has little room: the primary file is named briefly (the media by its code), in full on hover.
    const title = primary.short ? ` title="${escape(primary.label)}"` : "";
    return `<div class="print-files" data-print-files data-compact><a class="button" ${viewLink(primary.href, primary.label, escape)}${title}>${escape(primary.short || primary.label)}</a>${second}${menu}</div>`;
  }
  const list = others.map((item) => `<li><a ${viewLink(item.href, item.label, escape)}>${escape(item.label)}</a></li>`).join("");
  return `<div class="print-files" data-print-files><a class="button${cream ? " primary" : ""} print-files-primary" ${viewLink(primary.href, primary.label, escape)}>${escape(primary.label)}</a>${second}${extra}${others.length ? `<details class="disclosure print-files-other" data-other-formats><summary><span>${escape(menuLabel)}</span><span class="disclosure-hint">${others.length}</span></summary><div class="disclosure-body"><ul class="print-files-list">${list}</ul></div></details>` : ""}</div>`;
}

/**
 * One section job's files: the print PDF and its labels on every media. Empty when the job cannot be downloaded.
 * @param {{id: string, status?: string, superseded_at?: string | null, stamps_only?: boolean}} batch
 * @param {{compact?: boolean, primary?: boolean, extra?: string}} [options] `extra` is caller-built markup after the primary file
 */
export function jobFiles(batch, options = {}) {
  if (!downloadable(batch)) return "";
  const base = `/api/documents/batches/${encodeURIComponent(batch.id)}`;
  const media = rememberedMedia();
  const pdf = { label: options.compact ? "Paper PDF" : "Open paper PDF", href: `${base}/download` };
  const labels = MEDIA.map((item) => ({ id: item.id, label: `Labels · ${item.label}`, short: `Labels · ${item.short}`, href: `${base}/labels?media=${item.id}` }));
  const chosen = /** @type {FileLink} */ (labels.find((item) => item.id === media));
  const primary = batch.stamps_only ? chosen : pdf;
  const others = labels.filter(item => item !== chosen);
  return filesMarkup(primary, others, { ...options, secondary: batch.stamps_only ? pdf : chosen, menuLabel: 'Other label stocks', id: batch.id });
}

/**
 * A stack printed in several versions (a drafted quiz for the term, one job per version): one print PDF with the versions
 * interleaved, copy 1 of A, B, C, D, then copy 2 ..., so every section's share of the stack holds every version and
 * neighbouring students get different ones (requirement, 2026-10-01). Each version's own PDF sits under "Other formats".
 * Until every version's job is ready and current there is only a note.
 * @param {{id: string, status?: string, superseded_at?: string | null, version_label?: string, version?: {label?: string}}[]} batches
 * @param {{compact?: boolean, primary?: boolean}} [options]
 */
export function stackFiles(batches, options = {}) {
  if (!batches.length) return "";
  if (!batches.every(downloadable)) {
    const ended = (/** @type {{superseded_at?: string | null, status?: string}} */ batch) => Boolean(batch.superseded_at) || batch.status === "cancelled";
    // Superseded jobs have no files left (`downloadable`), so only a stack with some versions still current points at their files.
    const note = batches.every((batch) => batch.superseded_at) ? "Superseded: this stack prints nothing more."
      : batches.every(ended) ? "This stack prints nothing more: its versions’ jobs were superseded or cancelled."
      : batches.some(ended) ? "The interleaved stack is no longer available: a version’s job was superseded or cancelled. Print from each version’s file."
      : "The interleaved stack is ready once every version’s job is.";
    return `<p class="print-files-note" data-print-files-note>${escape(note)}</p>`;
  }
  const query = batchesQuery(batches);
  const pdf = { label: "Print PDF · every version interleaved", short: "Print PDF", href: `/api/documents/stack?${query}` };
  const others = batches.map((batch) => ({ label: `Version ${batch.version?.label ?? batch.version_label ?? ""} only · PDF`, href: `/api/documents/batches/${encodeURIComponent(batch.id)}/download` }));
  return filesMarkup(pdf, others, { ...options, menuLabel: 'Individual version PDFs', id: `stack-${batches[0].id}` });
}

/**
 * A seating chart export's whole-room files, in seat order across its sections. They carry the same stamps as the
 * section files. Until every section's job is ready and current there is only a note.
 * @param {string} chartId
 * @param {{id: string, status?: string, superseded_at?: string | null, stamps_only?: boolean, section_number?: number, sheet_labels?: number | boolean}[]} batches
 * @param {{compact?: boolean, primary?: boolean}} [options]
 */
export function exportFiles(chartId, batches, options = {}) {
  if (!chartId || !batches.length) return "";
  if (!batches.every(downloadable)) {
    // A superseded or cancelled section job never becomes ready again, so the room files of this export never will either.
    const ended = (/** @type {{superseded_at?: string | null, status?: string}} */ batch) => Boolean(batch.superseded_at) || batch.status === "cancelled";
    const note = batches.every((batch) => batch.superseded_at) ? "Superseded: this export prints nothing more."
      : batches.some(ended) ? "Whole-room files are no longer available for this export: a section’s job was superseded or cancelled. Print from the section files."
      : "Whole-room files are ready once every section’s job is ready.";
    return `<p class="print-files-note" data-print-files-note>${escape(note)}</p>`;
  }
  const query = batchesQuery(batches);
  const base = `/api/seating/${encodeURIComponent(chartId)}/room-export`;
  const media = rememberedMedia();
  const pdf = { label: "Whole room PDF · seat order", short: "Room PDF", href: `${base}/print?${query}` };
  // Jobs printed with Scantron labels give the bubble sheet labels their own button, and the page labels leave them out
  // (DOCUMENTS "SCANTRON LABELS", requirement, 2026-10-18).
  const scantron = batches.some((batch) => batch.sheet_labels);
  const labels = MEDIA.map((item) => ({ id: item.id, label: `Room ${scantron ? "page " : ""}labels · ${item.label}`, short: `Room ${scantron ? "page " : ""}labels · ${item.short}`, href: `${base}/${scantron ? "page-" : ""}labels?media=${item.id}&${query}` }));
  const sheets = scantron ? MEDIA.map((item) => ({ id: item.id, label: `Scantron labels · ${item.label}`, short: `Scantron labels · ${item.short}`, href: `${base}/scantron-labels?media=${item.id}&${query}` })) : [];
  const chosen = /** @type {FileLink} */ (labels.find((item) => item.id === media));
  const chosenSheets = sheets.find((item) => item.id === media);
  const stampsOnly = batches.every((batch) => batch.stamps_only);
  const primary = stampsOnly ? chosen : pdf;
  // Both kinds in one file stay in the menu, each copy's Scantron label after its pages (requirement, 2026-10-18).
  const together = scantron ? MEDIA.map((item) => ({ label: `Page and Scantron labels together · ${item.label}`, href: `${base}/labels?media=${item.id}&${query}` })) : [];
  const others = [...together, ...labels.filter(item => item !== chosen), ...sheets.filter(item => item !== chosenSheets)];
  return filesMarkup(primary, others, { ...options, secondary: stampsOnly ? pdf : chosen, scantron: chosenSheets, menuLabel: scantron ? 'More label files' : 'Other label stocks', id: `room-${batches[0].id}` });
}
