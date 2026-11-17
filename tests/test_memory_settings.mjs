// The Memory page (memory-settings.js; ASSISTANT-MEMORY "ONE PAGE", requirement, 2026-11-10): the switches and numbers, and the Assistant's
// memories as rows whose controls are on the row.
import assert from "node:assert/strict";
import test from "node:test";
import { memoriesMarkup, memoryRowMarkup, ownerFields, settingsMarkup } from "../static/memory-settings.js";

const kept = { id: "m1", content: "Always put an answer key at the end of a quiz.", scope: "workspace", applies_to: "Everything", standalone: true, revision: 1,
  status: "current", source: "chat", created_at: "2026-10-10T12:00:00+00:00", updated_at: "2026-10-10T12:00:00+00:00" };
const listing = { courses: [{ id: "c1", label: "MATH 140" }], terms: [{ id: "t1", label: "MATH 140 Fall 2026" }], memories: [
  kept,
  { ...kept, id: "m2", content: "Avoid em-dashes.", always: true, source: "form" },
  { ...kept, id: "m3", revision: 2, scope: "term", term_id: "t1", applies_to: "MATH 140 Fall 2026", content: "The TA grades the quizzes.", source: "form", last_used: "2026-10-10T09:00:00+00:00" },
  { ...kept, id: "m6", content: "Ask for documentation plainly.", source: "followup", learned: true, given: { drafts: 14, again: 2 } },
  { ...kept, id: "m4", revision: 3, status: "superseded", reason: "replaced", content: "Make four versions.", pictures: [{ id: "p1", route: "/api/assistant/memory-pictures/p1" }] },
  { ...kept, id: "m5", revision: 2, status: "removed", reason: "undone", content: "Taken back one." }] };
const settings = { version: 3, capture: true, learning: false, rubric_learning: true, given: 8, use_gate: 0.6, always_chars: 6000,
  measured: { given: 8, use_gate: 0.6, always_chars: 6000 }, limits: { given: [1, 20], use_gate: [0.3, 0.9], always_chars: [0, 20000] } };

test("a memory is one row with its tick, what it applies to and Remove on the row", () => {
  const row = memoryRowMarkup(kept, listing);
  assert.match(row, /<li class="memory-row" data-patch-key="memory-m1" data-memory-row="m1">/);
  assert.match(row, /<p class="memory-words">Always put an answer key at the end of a quiz\.<\/p>/);
  assert.match(row, /<input type="checkbox" data-memory-always="m1">With every request<\/label>/);  // not ticked until it is marked
  assert.match(row, /<select data-memory-owner="m1" aria-label="Applies to"><option value="workspace" selected>Everything<\/option><option value="course:c1">MATH 140, every term<\/option><option value="term:t1">MATH 140 Fall 2026 only<\/option>/);
  assert.match(row, /data-memory-edit="m1">Edit<\/button><button class="text-button danger" type="button" data-memory-remove="m1">Remove</);
  assert.match(row, /Kept 2026-10-10 · from a chat message</);
  assert.doesNotMatch(row, /<form|<textarea/);  // nothing to open, and no Save, to tick it
  assert.match(memoryRowMarkup(listing.memories[1], listing), /data-memory-always="m2" checked>/);
  // A memory of a past term keeps its owner in the list, so changing something else never moves it.
  assert.match(memoryRowMarkup({ ...kept, scope: "term", term_id: "t0", applies_to: "MATH 140 Spring 2026" }, listing), /<option value="term:t0" selected>MATH 140 Spring 2026<\/option>/);
});

test("its words open in place to be changed, and are escaped", () => {
  const row = memoryRowMarkup({ ...kept, content: "<b>Use</b> tables." }, listing, true);
  assert.match(row, /<form class="memory-edit" data-memory-words="m1"><textarea name="content" rows="3" maxlength="4000" required aria-label="Remember">&lt;b&gt;Use&lt;\/b&gt; tables\.<\/textarea>/);
  assert.match(row, /<button class="button primary" type="submit">Save<\/button><button class="button" type="button" data-memory-cancel>Cancel</);
  assert.doesNotMatch(row, /data-memory-edit=/);
  assert.doesNotMatch(memoryRowMarkup({ ...kept, content: "<img src=x onerror=alert(1)>" }, listing), /<img src=x/);
});

test("a row says whose words they are and how it has been given", () => {
  const facts = (/** @type {any} */ more) => memoryRowMarkup({ ...kept, ...more }, listing);
  assert.match(facts({ source: "followup", learned: true }), /from a follow-up · written by the Assistant from your correction</);
  assert.match(facts({ ruled: true }), /from a chat message · written by the Assistant, without the student</);
  assert.match(facts({ typed: "Three from now on, not <four>." }), /reworded by the Assistant from “Three from now on, not &lt;four&gt;\.”/);
  assert.match(facts({ standalone: false }), /it may not read well on its own/);
  assert.match(facts({ last_used: "2026-10-10T09:00:00+00:00" }), /last chosen 2026-10-10/);
  // DRAFT REFERENCES: how many drafts were given this revision, and in how many the same correction was still made.
  assert.match(facts({ given: { drafts: 14, again: 2 } }), /given to 14 drafts, 2 still corrected this way</);
  assert.match(facts({ given: { drafts: 1, again: 0 } }), /given to 1 draft</);
  assert.doesNotMatch(facts({ given: { drafts: 1, again: 0 } }), /still corrected/);
  // The pictures kept with it are shown small and open in the viewer.
  const pictured = facts({ pictures: [{ id: "p1", route: "/api/assistant/memory-pictures/p1" }] });
  assert.match(pictured, /href="\/api\/assistant\/memory-pictures\/p1" data-view-file="\/api\/assistant\/memory-pictures\/p1" data-view-name="Remembered picture 1"/);
});

test("the list leads with what goes with every request, filters, and keeps what was replaced or removed apart", () => {
  const html = memoriesMarkup(listing);
  assert.match(html, /The Assistant’s memories <span class="memory-count">4<\/span>/);
  assert.deepEqual([...html.matchAll(/data-memory-list="assistant"[\s\S]*?<\/ul>/g)][0][0].match(/data-memory-row="(m\d)"/g), ['data-memory-row="m2"', 'data-memory-row="m1"', 'data-memory-row="m3"', 'data-memory-row="m6"']);
  assert.match(html, /data-filter-group="memory-filter"/);
  for (const [filter, rows] of /** @type {[string, string[]][]} */ ([["always", ["m2"]], ["own", ["m2", "m1", "m3"]], ["learned", ["m6"]]])) {
    const shown = memoriesMarkup(listing, { filter }).split('data-memory-list="assistant-gone"')[0];
    assert.deepEqual((shown.match(/data-memory-row="(m\d)"/g) || []).map((found) => found.slice(17, 19)), rows, filter);
  }
  assert.match(html, /<details class="disclosure memory-add"[^>]*><summary><span>Add a memory<\/span>/);
  assert.match(html, /<form class="memory-edit" data-memory-new>/);
  assert.match(html, /<select name="owner" aria-label="Applies to">/);
  assert.match(html, /Replaced and removed<\/span><span class="disclosure-hint">2<\/span>/);
  assert.match(html, /data-memory-restore="m4">Restore<\/button><button class="text-button danger" type="button" data-memory-erase="m4">Erase its words and pictures</);
  assert.match(html, /Taken back 2026-10-10 · Everything/);
  assert.match(html, /data-memory-erase="m5">Erase its words</);
  // One row opens for its words; the others stay as they are.
  assert.equal((memoriesMarkup(listing, { editing: "m3" }).match(/data-memory-words=/g) || []).length, 1);
  assert.match(memoriesMarkup({ memories: [] }), /Nothing is remembered yet\./);
  assert.match(memoriesMarkup({ memories: [kept] }, { filter: "always" }), /None of this kind\./);
});

test("the switches and the numbers say what stands, and offer the measured values only when one differs", () => {
  const html = settingsMarkup(settings);
  assert.match(html, /<input type="checkbox" data-memory-switch="capture" checked>Keep what I type in chat<\/label>/);
  assert.match(html, /<input type="checkbox" data-memory-switch="learning">Learn from my corrections of follow-up cards<\/label>/);
  assert.match(html, /<input type="checkbox" data-memory-switch="rubric_learning" checked>Learn rubric memories by itself<\/label>/);
  assert.match(html, /<input type="number" name="given" min="1" max="20" step="1" value="8" required>/);
  assert.match(html, /<input type="number" name="use_gate" min="0.3" max="0.9" step="0.05" value="0.60" required>/);
  assert.match(html, /<input type="number" name="always_chars" min="0" max="20000" step="100" value="6000" required>/);
  assert.match(html, /measured: 0\.60</);
  assert.doesNotMatch(html, /data-memory-measured/);
  const own = settingsMarkup({ ...settings, given: 3 });
  assert.match(own, /name="given" min="1" max="20" step="1" value="3"/);
  assert.match(own, /<button class="text-button" type="button" data-memory-measured>Use the measured values</);
});

test("what a row's Applies to sends", () => {
  assert.deepEqual(ownerFields("workspace"), { scope: "workspace" });
  assert.deepEqual(ownerFields("course:c1"), { scope: "course", definition_id: "c1" });
  assert.deepEqual(ownerFields("term:t1"), { scope: "term", term_id: "t1" });
});
