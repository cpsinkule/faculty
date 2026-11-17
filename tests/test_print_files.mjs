import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// print-files.js imports ui.js, which imports icons.js, so it is loaded from its file.
const files = await import(new URL("../static/print-files.js", import.meta.url).href);
const { MEDIA, jobFiles, exportFiles, stackFiles, isStampsOnly, rememberedMedia, rememberMedia } = files;
const { viewableRoute } = await import(new URL("../static/file-viewer.js", import.meta.url).href);
const source = await readFile(new URL("../static/print-files.js", import.meta.url), "utf8");

function memoryStorage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)) };
}

test("one media vocabulary", () => {
  assert.deepEqual(MEDIA.map((item) => item.label), ["Brother QL-800 · 29 × 90 mm (DK-1201)", "Brother QL-800 · 62 × 29 mm (DK-1209)", "Avery 5160 sheet"]);
});

test("stamps-only versions are recognised by generator or label", () => {
  assert.equal(isStampsOnly({ label: "Stamps only · 1 page" }), true);
  assert.equal(isStampsOnly({ version_label: "Stamps only · 3 pages" }), true);
  assert.equal(isStampsOnly({ label: "B", compiler_json: JSON.stringify({ generator: "faculty-stamps-only" }) }), true);
  assert.equal(isStampsOnly({ label: "A", compiler_json: "{}" }), false);
  assert.equal(isStampsOnly({ label: "A", compiler_json: "not json" }), false);
  assert.equal(isStampsOnly(null), false);
});

test("a job's primary file follows its kind; nothing for a job that cannot be downloaded", () => {
  // Each file opens in the app's file viewer (VIEWER), whose Download saves it; the href stays the file for a modifier-click.
  const pdf = jobFiles({ id: "b1", status: "ready", superseded_at: null, stamps_only: false });
  assert.match(pdf, /class="button primary print-files-primary" href="\/api\/documents\/batches\/b1\/download" data-view-file="\/api\/documents\/batches\/b1\/download" data-view-name="Open paper PDF">Open paper PDF</);
  assert.match(pdf, /Labels · Brother QL-800 · 29 × 90 mm \(DK-1201\)/);
  const labels = jobFiles({ id: "b1", status: "ready", superseded_at: null, stamps_only: true });
  assert.match(labels, /print-files-primary" href="\/api\/documents\/batches\/b1\/labels\?media=dk-1201" data-view-file="\/api\/documents\/batches\/b1\/labels\?media=dk-1201" data-view-name="Labels · Brother[^"]*">Labels · Brother/);
  assert.match(labels, /data-view-file="\/api\/documents\/batches\/b1\/download" data-view-name="Open paper PDF" title="Open paper PDF">Open paper PDF/);  // separate direct secondary action
  assert.equal(jobFiles({ id: "b1", status: "pending" }), "");
  assert.equal(jobFiles({ id: "b1", status: "ready", superseded_at: "2026-09-26" }), "");
  const compact = jobFiles({ id: "b1", status: "ready", stamps_only: false }, { compact: true });
  assert.match(compact, /^<div class="print-files" data-print-files data-compact><a class="button" href="[^"]+\/download" data-view-file="[^"]+\/download" data-view-name="Paper PDF">Paper PDF<\/a>/);
  assert.match(compact, /role="menuitem" href="[^"]+labels\?media=dk-1209" data-view-file="[^"]+labels\?media=dk-1209" data-view-name="Labels · Brother/);  // the menu's files too
  assert.match(compact, /popovertarget="files-b1"/);  // a stable menu id keeps unchanged polling from redrawing
  assert.equal(jobFiles({ id: "b1", status: "ready" }, { compact: true }), compact);
});

test("room files wait for every section and follow the export's kind", () => {
  const waiting = exportFiles("c1", [{ id: "a", status: "ready" }, { id: "b", status: "rendering" }]);
  assert.match(waiting, /data-print-files-note>Whole-room files are ready once every section’s job is ready\./);
  assert.match(exportFiles("c1", [{ id: "a", status: "ready", superseded_at: "x" }]), /Superseded/);
  // One section superseded or cancelled, the rest ready: room_export will always refuse, so nothing is promised.
  for (const ended of [{ id: "b", status: "ready", superseded_at: "x" }, { id: "b", status: "cancelled" }]) {
    const mixed = exportFiles("c1", [{ id: "a", status: "ready" }, ended]);
    assert.match(mixed, /no longer available for this export/);
    assert.doesNotMatch(mixed, /ready once/);
  }
  // A compact header names the primary file briefly, in full on hover.
  const compactRoom = exportFiles("c1", [{ id: "a", status: "ready", stamps_only: true }], { compact: true });
  assert.match(compactRoom, /data-view-name="Room labels · Brother QL-800 · 29 × 90 mm \(DK-1201\)" title="Room labels · Brother QL-800 · 29 × 90 mm \(DK-1201\)">Room labels · DK-1201<\/a>/);
  const ready = exportFiles("c1", [{ id: "a", status: "ready", stamps_only: true }, { id: "b", status: "ready", stamps_only: true }]);
  // The jobs are joined by an encoded comma: a raw "," is not a file route the viewer opens, so the click did nothing (print-1).
  assert.match(ready, /href="\/api\/seating\/c1\/room-export\/labels\?media=dk-1201&amp;batches=a%2Cb" data-view-file="\/api\/seating\/c1\/room-export\/labels\?media=dk-1201&amp;batches=a%2Cb" data-view-name="[^"]+">Room labels · Brother QL-800 · 29 × 90 mm/);
  assert.equal(viewableRoute("/api/seating/c1/room-export/labels?media=dk-1201&batches=a%2Cb"), true);
  assert.match(ready, /Whole room PDF · seat order/);
  const printed = exportFiles("c1", [{ id: "a", status: "ready" }]);
  assert.match(printed, /print-files-primary" href="\/api\/seating\/c1\/room-export\/print\?batches=a" data-view-file="[^"]+" data-view-name="[^"]+">Whole room PDF · seat order/);
  assert.equal(exportFiles("", [{ id: "a", status: "ready" }]), "");
});

test("an export printed with Scantron labels gives them their own button, and the page labels leave them out", () => {
  // DOCUMENTS "SCANTRON LABELS" (requirement, 2026-10-18).
  const room = exportFiles("c1", [{ id: "a", status: "ready", stamps_only: true, sheet_labels: 1 }, { id: "b", status: "ready", stamps_only: true, sheet_labels: 1 }], { compact: true });
  assert.match(room, /href="\/api\/seating\/c1\/room-export\/page-labels\?media=dk-1201&amp;batches=a%2Cb"[^>]*>Room page labels · DK-1201<\/a>/);
  assert.match(room, /href="\/api\/seating\/c1\/room-export\/scantron-labels\?media=dk-1201&amp;batches=a%2Cb"[^>]*>Scantron labels · DK-1201<\/a>/);
  assert.match(room, /scantron-labels\?media=avery-5160/);  // Other stocks sit in the menu.
  // Both kinds together stay in the menu, on every stock.
  for (const stock of ["dk-1201", "dk-1209", "avery-5160"]) assert.match(room, new RegExp(`room-export/labels\\?media=${stock}&amp;batches=a%2Cb"[^>]*>Page and Scantron labels together · `));
  assert.equal(viewableRoute("/api/seating/c1/room-export/scantron-labels?media=dk-1201&batches=a%2Cb"), true);
  // Without Scantron labels the files are as before.
  assert.doesNotMatch(exportFiles("c1", [{ id: "a", status: "ready", sheet_labels: 0 }]), /Scantron|page-labels/);
});

test("a stack of several versions prints one interleaved file once every version is ready, and says why when it cannot", () => {
  const ready = stackFiles([{ id: "a", status: "ready", version: { label: "A" } }, { id: "b", status: "ready", version: { label: "B" } }]);
  assert.match(ready, /print-files-primary" href="\/api\/documents\/stack\?batches=a%2Cb" data-view-file="\/api\/documents\/stack\?batches=a%2Cb" data-view-name="Print PDF · every version interleaved">/);
  assert.equal(viewableRoute("/api/documents/stack?batches=a%2Cb"), true);
  assert.equal(viewableRoute("/api/documents/stack?batches=a,b"), false);  // why the comma is encoded
  assert.match(ready, /Version B only · PDF/);
  assert.match(stackFiles([{ id: "a", status: "ready" }, { id: "b", status: "rendering" }]), /ready once every version’s job is/);
  // Some versions still current: their own files stay printable.
  assert.match(stackFiles([{ id: "a", status: "ready" }, { id: "b", status: "ready", superseded_at: "x" }]), /Print from each version’s file/);
  // Nothing current: superseded jobs have no files, so the note does not send the instructor to them.
  const ended = stackFiles([{ id: "a", status: "ready", superseded_at: "x" }, { id: "b", status: "ready", superseded_at: "x" }]);
  assert.match(ended, /Superseded: this stack prints nothing more\./);
  assert.doesNotMatch(ended, /each version’s file/);
  assert.match(stackFiles([{ id: "a", status: "cancelled" }, { id: "b", status: "ready", superseded_at: "x" }]), /prints nothing more/);
  assert.equal(stackFiles([]), "");
});

// Every file link must be one the viewer opens: a bare comma in `batches=` failed viewableRoute, so the click did nothing (2026-10-02).
const routes = (markup) => [...markup.matchAll(/data-view-file="([^"]+)"/g)].map((match) => match[1].replaceAll("&amp;", "&"));

test("a stack's interleaved PDF names every version's job and opens in the viewer", () => {
  const batches = [{ id: "j-a", status: "ready", version_label: "A" }, { id: "j-b", status: "ready", version: { label: "B" } }];
  const stack = stackFiles(batches);
  assert.match(stack, /print-files-primary" href="\/api\/documents\/stack\?batches=j-a%2Cj-b" data-view-file="[^"]+" data-view-name="Print PDF · every version interleaved">/);
  assert.match(stack, /Version A only · PDF/);
  assert.match(stack, /Version B only · PDF/);
  assert.match(stackFiles([batches[0], { id: "j-b", status: "rendering" }]), /data-print-files-note>The interleaved stack is ready once/);
  assert.equal(stackFiles([]), "");
});

test("every print file link passes the viewer's route check", () => {
  const two = [{ id: "a", status: "ready" }, { id: "b", status: "ready" }];
  const markup = [jobFiles(two[0]), stackFiles(two), exportFiles("c1", two), exportFiles("c1", two.map((batch) => ({ ...batch, stamps_only: true })))].join("");
  const found = routes(markup);
  assert.ok(found.some((route) => route.startsWith("/api/documents/stack?")) && found.some((route) => route.includes("/room-export/")));
  for (const route of found) assert.ok(viewableRoute(route), route);
});

test("the label media is remembered per browser, and storage failures fall back", () => {
  const original = globalThis.localStorage;
  try {
    assert.equal(rememberedMedia(), "dk-1201");
    globalThis.localStorage = memoryStorage();
    rememberMedia("avery-5160");
    assert.equal(rememberedMedia(), "avery-5160");
    rememberMedia("posters");  // unknown media are ignored
    assert.equal(rememberedMedia(), "avery-5160");
    assert.match(jobFiles({ id: "b2", status: "ready", stamps_only: true }), /print-files-primary" href="[^"]+media=avery-5160" data-view-file="[^"]+" data-view-name="[^"]+">Labels · Avery 5160 sheet/);
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("blocked"); } });
    assert.equal(rememberedMedia(), "dk-1201");
    assert.doesNotThrow(() => rememberMedia("dk-1209"));
  } finally {
    Object.defineProperty(globalThis, "localStorage", { configurable: true, writable: true, value: original });
  }
});

test("no inline styles in the markup", () => {
  assert.doesNotMatch(source, /style=/);
});
