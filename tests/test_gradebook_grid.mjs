import assert from "node:assert/strict";
import test from "node:test";

import { attentionMatch, blankWords, cellParts, duePosted, differences, differsChip, differsCount, differsLines, differsSummary, droppedIn, emptyCells, gradingChanged,
  gradingValue, gridMarkup, gridModel, headMeta, hiddenChip, keyMarkup, letterCounts, marksPresent, mergeScores, nightlyNotice, pastDue, pasteNeedsConfirm,
  pendingChip, postingRule, postingTitle, uncountedItems, shownPoints, entryText, syncState } from "../static/gradebook.js";
import { columnShown, draftSetup, scalePreview, uncategorized } from "../static/gradebook-setup.js";

// The grid's pure pieces (gradebook/README.md, "Browser"): which marks the one-line key lists, dropped scores, a category
// collapsed to its percentage (view memory only) and the one-block linked heading. Nothing here decides a score: dropped
// columns and percentages are the server's `results`.
const students = [
  { row: "s1:e1", section_id: "s1", section: 501, name: "Example, Alice", dr: [] },
  { row: "s2:e2", section_id: "s2", section: 502, name: "Park, Jamie", dr: ["a-test"] },
];
const cell = (points, extra = {}) => ({ id: `c-${points}`, status: "scored", points, note: "", source: "instructor", actor: "workspace", at: "", ...extra });
const view = (overrides = {}) => ({
  setup: {
    mode: "weighted",
    categories: [{ id: "hw", name: "Homework", weight: "40", drop_lowest: 1 }, { id: "ex", name: "Exams", weight: "60", drop_lowest: 0 }],
    items: [
      { id: "i1", title: "HW 1", category_id: "hw", points: "10", sections: [] },
      { id: "i2", title: "Quiz 1", category_id: "hw", points: "6", due_date: "2026-10-01", sections: [] },
      { id: "i3", title: "Test 1", category_id: "ex", points: "20", sections: [], assignment_id: "a-test", link: { kind: "grading", parts: ["bubble"] } },
      { id: "i4", title: "Project", category_id: null, points: "20", sections: ["s1"], counts: false, extra_credit: true },
    ],
  },
  students,
  cells: { i1: { "s1:e1": cell("8"), "s2:e2": cell("7") }, i2: { "s2:e2": cell("4", { note: "late", source: "import" }) } },
  results: {
    students: { "s1:e1": { percent: "80.00", categories: { hw: { percent: "80.00", dropped: [] } } },
      "s2:e2": { percent: "70.00", categories: { hw: { percent: "70.00", dropped: ["i1"] } } } },
    items: {}, categories: {}, overall: {},
  },
  links: { i3: { last_pull: null } },
  ...overrides,
});

test("the key lists only the marks the grid shows", () => {
  const data = view();
  const marks = marksPresent(data, gridModel(data));
  // Typed and imported values, a note, a dropped score, a DR booking without a score, a linked column and a column not
  // assigned to section 502; no pulled value, nothing missing, excused or over its points.
  assert.deepEqual(marks, ["linked", "import", "instructor", "note", "dropped", "dr", "na"]);
  const markup = keyMarkup(marks);
  assert.match(markup, /data-mark="dropped" title="Dropped: the lowest in its category, left out of the results"/);
  assert.equal(keyMarkup([]), "", "no marks: no key");
  const scoredOnly = view({ setup: { ...view().setup, items: [view().setup.items[0]] }, cells: { i1: { "s1:e1": cell("12", { source: "grading" }) } } });
  assert.deepEqual(marksPresent(scoredOnly, gridModel(scoredOnly)), ["grading", "over"]);
});

test("a dropped score is marked from the server's results, never guessed", () => {
  const data = view();
  const [alice, jamie] = students;
  const hw = data.setup.items[0];
  assert.equal(droppedIn(data, hw, jamie), true);
  assert.equal(droppedIn(data, hw, alice), false);
  assert.equal(droppedIn(data, data.setup.items[3], jamie), false, "uncategorized: nothing is dropped");
  assert.match(cellParts(hw, jamie, data.cells.i1["s2:e2"], { dropped: true }).cls, /\bis-dropped\b/);
  assert.doesNotMatch(cellParts(hw, jamie, null, { dropped: true }).cls, /is-dropped/, "an empty cell is never shown as dropped");
  assert.doesNotMatch(cellParts(hw, jamie, data.cells.i1["s2:e2"], { dropped: true, pending: { status: "scored", points: "9" } }).cls, /is-dropped/);
});

test("a collapsed category is one read-only column of its percentage", () => {
  const data = view();
  const model = gridModel(data, { collapsed: new Set(["hw"]) });
  assert.deepEqual(model.items.map((item) => item.id), ["band:hw", "i3", "i4"]);
  assert.equal(model.columnCount, 4, "hidden columns still count");
  assert.equal(model.items[0].collapsed, true);
  const markup = gridMarkup(data, model);
  assert.match(markup, /<td class="gb-result gb-collapsed-cell" data-result-cell="band:hw"[^>]*>70\.00%<\/td>/);
  assert.match(markup, /class="gb-band is-collapsed" data-category="hw"/);
  assert.match(markup, /aria-expanded="false" aria-label="Show the columns of Homework"/);
  assert.doesNotMatch(markup, /data-item="i1"/);
  // A link that asks for a column keeps its category open.
  assert.deepEqual(gridModel(data, { collapsed: new Set(["hw"]), highlight: "i2" }).items.map((item) => item.id), ["i1", "i2", "i3", "i4"]);
  assert.equal(gridModel(data).columnCount, 4);
});

test("a linked heading is one block: a rule, a Pull icon, the plain meta line", () => {
  const data = view();
  const markup = gridMarkup(data, gridModel(data), { icon: () => "<svg></svg>" });
  assert.match(markup, /<th scope="col" class="gb-head gb-linked" data-col="2" data-item="i3">/);
  assert.match(markup, /<button type="button" class="gb-pull" data-pull="i3" tabindex="-1" aria-label="Pull Test 1 from grading" title="Pull from bubble sheets\. Not pulled yet\."><svg><\/svg><\/button>/);
  assert.doesNotMatch(markup, /gb-flag/);
  assert.equal(headMeta(data.setup.items[2]), "20 pts");
  assert.equal(headMeta(data.setup.items[1]), "6 pts · Oct 1");
  assert.equal(headMeta(data.setup.items[3]), "20 pts · not counted · extra credit");
});

test("a counted column the server says counts toward nothing is named on its heading and in one notice's list", () => {
  const item = { id: "q3", title: "Quiz 3", points: "10", category_id: null };
  assert.equal(headMeta(item, true), "10 pts · not counted (no category)");
  assert.equal(headMeta({ ...item, counts: false }, true), "10 pts · not counted");  // its own "not counted" says it already
  assert.equal(headMeta(item), "10 pts");
  const data = view({ results: { students: {}, items: {}, categories: {}, overall: {}, uncounted: ["i4"] } });
  assert.deepEqual(uncountedItems(data), [{ id: "i4", title: "Project" }]);
  assert.deepEqual(uncountedItems(view()), []);
  // The server names it term-wide; Project is limited to section s1, so s2's grid (which does not show it) names nothing.
  assert.deepEqual(uncountedItems(data, "s1"), [{ id: "i4", title: "Project" }]);
  assert.deepEqual(uncountedItems(data, "s2"), []);
  assert.match(gridMarkup(data, gridModel(data)), /<span class="gb-head-meta">20 pts · not counted · extra credit<\/span>/);
  const lost = { ...data, setup: { ...data.setup, items: data.setup.items.map((entry) => (entry.id === "i4" ? { ...entry, counts: true, extra_credit: false } : entry)) } };
  assert.match(gridMarkup(lost, gridModel(lost)), /<span class="gb-head-meta">20 pts · not counted \(no category\)<\/span>/);
});

test("a write merged into a section's View keeps each column's term-wide cell count in step", () => {
  // A section tab reads `cells=section`: its cells are that section's, `cell_counts` the term's (the setup shows them).
  const data = view({ cell_counts: { i1: 40, i2: 12 } });
  const touched = mergeScores(data, { cells: {
    i2: { "s1:e1": cell("5") },                                            // a new score: one more
    i1: { "s1:e1": { status: "cleared" }, "s2:e2": cell("0", { status: "missing" }) }, // a cleared one: one fewer; a status change: the same
    i3: { "s2:e2": cell("18") },                                           // a column with no count recorded yet starts from zero
  }, results: { ...data.results, overall: { mean: "75.00" } } });
  assert.deepEqual(data.cell_counts, { i1: 39, i2: 13, i3: 1 });
  assert.deepEqual(touched, [["i2", "s1:e1"], ["i1", "s1:e1"], ["i1", "s2:e2"], ["i3", "s2:e2"]]);
  assert.equal(data.cells.i1["s1:e1"], undefined);
  assert.equal(data.cells.i1["s2:e2"].status, "missing");
  assert.equal(data.results.overall.mean, "75.00");
  // Two cleared: two fewer; clearing a cell that holds nothing changes nothing.
  mergeScores(data, { cells: { i2: { "s2:e2": null, "s1:e1": null }, i4: { "s1:e1": { status: "cleared" } } } });
  assert.deepEqual(data.cell_counts, { i1: 39, i2: 11, i3: 1 });
  const zero = view({ cell_counts: { i1: 0 } });
  mergeScores(zero, { cells: { i1: { "s1:e1": null } } });
  assert.equal(zero.cell_counts.i1, 0, "never below zero");
  const term = view();  // the term's View has every cell and no counts: none are added
  mergeScores(term, { cells: { i2: { "s1:e1": cell("5") } } });
  assert.equal("cell_counts" in term, false);
});

test("Setup's Find a column hides rows by title or by a missing category, and changes nothing else", () => {
  const setup = draftSetup(view().setup);
  const titles = (find) => setup.items.filter((item) => columnShown(setup, item, find)).map((item) => item.title);
  assert.deepEqual(titles({ query: "" }), ["HW 1", "Quiz 1", "Test 1", "Project"]);
  assert.deepEqual(titles({ query: "  quiz " }), ["Quiz 1"]);
  assert.deepEqual(titles({ query: "PROJÉCT" }), ["Project"]);  // case and accents ignored
  assert.deepEqual(titles({ only: "uncategorized" }), ["Project"]);
  assert.deepEqual(titles({ query: "hw", only: "uncategorized" }), []);
  assert.equal(uncategorized(setup, { category_id: "gone" }), true);  // a category the setup no longer has
  assert.equal(uncategorized(setup, setup.items[0]), false);
});

test("grading changed since the last pull is a hint from the View's timestamps", () => {
  assert.equal(gradingChanged(null), false);
  assert.equal(gradingChanged({ grading_changed_at: null, last_pull: null }), false, "nothing recorded in grading");
  assert.equal(gradingChanged({ grading_changed_at: "2026-09-26T08:00:00+00:00", last_pull: null }), true, "never pulled");
  assert.equal(gradingChanged({ grading_changed_at: "2026-09-26T08:00:00+00:00", last_pull: { at: "2026-09-26T09:00:00+00:00" } }), false);
  assert.equal(gradingChanged({ grading_changed_at: "2026-09-26T10:00:00+00:00", last_pull: { at: "2026-09-26T09:00:00+00:00" } }), true);
  const data = view({ links: { i3: { last_pull: null, grading_changed_at: "2026-09-26T08:00:00+00:00" } } });
  assert.ok(marksPresent(data, gridModel(data)).includes("changed"));
  assert.match(gridMarkup(data, gridModel(data)), /class="gb-pull is-changed" data-pull="i3"[^>]*aria-description="Grading has recorded grades since the column was added\."/);
});

test("the attention strip counts letters and past-due blanks, and filters by them", () => {
  const scale = [{ letter: "A", min: "90" }, { letter: "B", min: "80" }, { letter: "C", min: "70" }, { letter: "F", min: "0" }];
  const data = view({ setup: { ...view().setup, scale } });
  data.results.students["s1:e1"].letter = "B";
  data.results.students["s2:e2"].letter = "C";
  assert.deepEqual(letterCounts(data, students), [{ letter: "A", count: 0 }, { letter: "B", count: 1 }, { letter: "C", count: 1 }, { letter: "F", count: 0 }]);
  const [alice, jamie] = students;
  assert.equal(attentionMatch(data, alice, { kind: "letter", value: "B" }), true);
  assert.equal(attentionMatch(data, jamie, { kind: "letter", value: "B" }), false);
  assert.equal(attentionMatch(data, jamie, { kind: "below", value: "B" }), true, "C is below B");
  assert.equal(attentionMatch(data, alice, { kind: "below", value: "B" }), false);
  assert.equal(attentionMatch(data, alice, { kind: "", value: "" }), true);
  // Quiz 1 is due Oct 1: past due on Oct 2 for Alice (no value); Jamie has one. Test 1 (Jamie's DR booking) has no due date.
  const later = view({ setup: { ...view().setup, items: view().setup.items.map((item) => (item.id === "i3" ? { ...item, due_date: "2026-09-01" } : item)) } });
  const due = pastDue(later, students, "2026-10-02");
  assert.deepEqual([...due.rows], [["s1:e1", ["i2", "i3"]]], "Jamie's Test 1 blank is a DR booking, not past due");
  assert.equal(due.total, 2);
  assert.equal(pastDue(later, students, "2026-10-01").total, 1, "due today is not past due");
  assert.equal(attentionMatch(later, alice, { kind: "overdue" }, due), true);
  assert.equal(attentionMatch(later, jamie, { kind: "overdue" }, due), false);
  assert.match(cellParts(later.setup.items[1], alice, null, { overdue: true }).cls, /\bis-overdue\b/);
  assert.match(cellParts(later.setup.items[0], alice, cell("8"), { uncertain: true }).cls, /\bis-uncertain\b/);
});

test("ZERO ONCE DUE: the server's counted-as-0 blanks are the past-due ones, shown as a muted 0; waiting blanks say why", () => {
  // Alice: Quiz 1 counts as 0, Test 1 waits for her makeup. Jamie: Test 1 waits on his DR booking. The browser's own date and DR
  // rule are not used: the server's `results.blanks` decides (its campus clock, certifications, makeups, papers in).
  const base = view();
  const data = view({ setup: { ...base.setup, ungraded: "due" },
    results: { ...base.results, blanks: { "s1:e1": { i2: "zero", i3: "makeup" }, "s2:e2": { i3: "dr" } } } });
  const due = pastDue(data, students, "2026-09-01");
  assert.deepEqual([...due.rows], [["s1:e1", ["i2"]]], "only the server's zeros; not the browser's date");
  assert.equal(due.total, 1);
  const [alice, jamie] = students;
  const [, quiz, test1] = data.setup.items;
  const zero = cellParts(quiz, alice, null, { reason: "zero" });
  assert.equal(zero.html, "0");
  assert.match(zero.cls, /\bis-zeroed\b/);
  assert.match(cellParts(test1, alice, null, { reason: "makeup" }).html, />MU</);
  assert.match(cellParts(test1, alice, null, { reason: "if_excused" }).html, />MU</);
  assert.match(cellParts(test1, alice, null, { reason: "paper" }).html, />IN</);
  assert.match(cellParts(test1, jamie, null, { reason: "dr" }).html, />DR</);
  assert.doesNotMatch(cellParts(quiz, alice, null, { reason: "zero", pending: { status: "scored", points: "5" } }).cls, /is-zeroed/, "a value being saved is not a zero");
  assert.doesNotMatch(cellParts(quiz, alice, cell("5"), { reason: "zero" }).cls, /is-zeroed/);
  assert.deepEqual(marksPresent(data, gridModel(data)), ["linked", "import", "instructor", "note", "zeroed", "makeup", "dropped", "dr", "na"]);
  // Another policy keeps the browser's rule (and its DR exception).
  assert.equal(pastDue(base, students, "2026-10-02").total, 1);
});

test("POSTING GRADES: a column's heading says how many of its grades Canvas hid when Faculty last read it", () => {
  const [hw] = view().setup.items;
  assert.equal(headMeta(hw, false), "10 pts", "the meta line never carries it: a narrow column cut it off (2026-10-07)");
  assert.equal(hiddenChip(3), '<span class="gb-head-hidden" data-head-hidden>3 hidden</span>');
  assert.equal(hiddenChip(0), "", "nothing hidden: no mark");
  assert.equal(postingTitle({ hidden: 0, read_at: "2026-10-06T12:00:00Z", source: "sync" }), "");
  assert.match(postingTitle({ hidden: 2, read_at: "2026-10-06T12:00:00Z", source: "nightly" }), /^2 grades hidden from students in Canvas when Faculty last read it \(.*the morning sync\)/);
  const data = view({ canvas_posting: { i1: { hidden: 4, graded: 9, courses: 2, manual: true, read_at: "2026-10-06T12:00:00Z", source: "post" } } });
  const markup = gridMarkup(data, gridModel(data));
  assert.match(markup, /<span class="gb-head-meta">10 pts<\/span>.*<span class="gb-head-hidden" data-head-hidden>4 hidden<\/span>/);
  assert.match(markup, /4 grades hidden from students in Canvas/);
});

// POSTING GRADES, AT EVERY SYNC (requirement, 2026-10-08): what the next sync does is on the heading until it is done, in ink.
test("POSTING GRADES: a column's heading says what the next sync does about its posting, and why", () => {
  assert.equal(pendingChip({ pending: ["post"] }), '<span class="gb-head-pending" data-head-pending>posts at sync</span>');
  assert.equal(pendingChip({ pending: ["hide", "switch"] }), '<span class="gb-head-pending" data-head-pending>hides at sync</span>');
  assert.equal(pendingChip({ pending: ["switch"] }), '<span class="gb-head-pending" data-head-pending>by hand at sync</span>');
  assert.equal(pendingChip({ hidden: 3 }), "");
  assert.equal(postingRule({ policy: { state: "posted", why: "choice" } }), "you chose to post it");
  assert.equal(postingRule({ policy: { state: "posted", why: "due" } }), "its category posts it after its due date");
  assert.equal(postingRule({ policy: { state: "posted", why: "iclicker" } }), "iClicker's columns post automatically");
  assert.equal(postingRule({ policy: { state: null, why: null, forced: "weight 0" } }), "always posted by hand: its category weighs 0%");
  assert.equal(postingRule({ policy: { state: null, why: null, rules: ["due", "certified"] } }), "its category posts it after its due date or when Faculty certifies it; not yet");
  assert.equal(postingRule({ policy: { state: null, why: null, rules: [] } }), "its category posts by hand");
  assert.equal(postingTitle({ hidden: 0, policy: { state: null, why: null, rules: [] } }), "", "nothing hidden, nothing chosen, nothing pending: no words");
  assert.equal(postingTitle({ hidden: 0, choice: "hide", policy: { state: "hidden", why: "choice" } }), "you chose to hide it");
  assert.match(postingTitle({ hidden: 5, read_at: "2026-10-06T12:00:00Z", source: "sync", pending: ["post"], policy: { state: "posted", why: "certified" } }),
    /^5 grades hidden .*; its category posts it when Faculty certifies it; the next sync posts its hidden grades in Canvas$/);
  const data = view({ canvas_posting: { i1: { hidden: 4, graded: 9, courses: 2, manual: true, read_at: "2026-10-06T12:00:00Z", source: "sync", pending: ["post"], policy: { state: "posted", why: "due" } } } });
  assert.match(gridMarkup(data, gridModel(data)), /4 hidden<\/span><span class="gb-head-pending" data-head-pending>posts at sync<\/span>/);
});

test("a paste that replaces values or writes many cells asks first; empty cells for Mark missing / Excuse", () => {
  assert.equal(pasteNeedsConfirm(6, 0), false);
  assert.equal(pasteNeedsConfirm(1, 1), true);
  assert.equal(pasteNeedsConfirm(51, 0), true);
  assert.equal(pasteNeedsConfirm(50, 0), false);
  const data = view();
  // Test 1 (i3): nobody has a value; Jamie has a DR booking for it. Project (i4) applies to section s1 only.
  assert.deepEqual(emptyCells(data, data.setup.items[2], students).map((entry) => [entry.row, entry.dr]), [["s1:e1", false], ["s2:e2", true]]);
  assert.deepEqual(emptyCells(data, data.setup.items[3], students).map((entry) => entry.row), ["s1:e1"]);
  assert.deepEqual(emptyCells(data, data.setup.items[0], students), [], "HW 1 has every value");
});

test("the letter-scale preview counts today's percentages under a draft scale", () => {
  const scale = [{ letter: "A", min: "90" }, { letter: "B", min: "80" }, { letter: "F", min: "0" }];
  assert.deepEqual(scalePreview(scale, ["95.00", "80.00", "79.99", null, ""]), {
    counts: [{ letter: "A", count: 1 }, { letter: "B", count: 1 }, { letter: "F", count: 1 }], none: 2, unmatched: 0 });
  assert.deepEqual(scalePreview([{ letter: "A", min: "90" }], ["50"]).unmatched, 1, "below every minimum");
});

test("a student's name opens their record", () => {
  const data = view();
  assert.match(gridMarkup(data, gridModel(data)), /<th scope="row" class="gb-name"><button type="button" class="gb-name-button" data-student="s1:e1" tabindex="-1" aria-haspopup="dialog">/);
});

// Import from iClicker's "When you press Import" (gradebook-dialogs.js `excusedLines`; ABSENCES ICLICKER-CREDIT): which students'
// sessions Import gives full credit for excused absences, and kept values that are an absence's credit, not typed in Faculty.
test("an iClicker preview names the sessions excused absences get full credit for", async () => {
  const { excusedLines } = await import("../static/gradebook-dialogs.js");
  const bob = { name: "Example, Bob", section: 507, sessions: [{ title: "Session 2", day: "Thu, Sep 3" }, { title: "Session 4", day: "Tue, Sep 8" }] };
  const alice = { name: "Example, Alice", section: 507, sessions: [{ title: "Session 2", day: "Thu, Sep 3" }] };
  assert.deepEqual(excusedLines({ cells: 3, points: "10", kept: 1, students: [alice, bob] }, 3), {
    lines: ["Gives 3 iClicker sessions full credit (10 points) for excused absences. Example, Alice: Session 2 (Thu, Sep 3); Example, Bob: Session 2 (Thu, Sep 3), Session 4 (Tue, Sep 8).",
      "1 session already has full credit for an excused absence and keeps it."],
    typedKept: 2 });
  assert.equal(excusedLines({ cells: 3, points: "10", kept: 0, students: [alice, bob] }, 0, 1).lines[0].endsWith("Example, Alice: Session 2 (Thu, Sep 3); and 1 more student."), true);
  assert.deepEqual(excusedLines(undefined, 2), { lines: [], typedKept: 2 });
});

// THE MORNING SYNC (gradebook/canvas_nightly.py; before 2026-10-08 the nightly WebAssign import): named only when it needs the instructor.
test("the morning sync (and the nightly WebAssign import before it) is named only when it needs the instructor", () => {
  assert.equal(nightlyNotice(null), null);
  assert.equal(nightlyNotice({ night: "2026-10-04", status: "imported", waiting: [], written: 12 }), null);
  assert.equal(nightlyNotice({ night: "2026-10-04", status: "unchanged", waiting: [] }), null);
  assert.equal(nightlyNotice({ night: "2026-10-04", status: "none", waiting: [] }), null);
  const out = nightlyNotice({ night: "2026-10-04", status: "signed_out", waiting: [], signed_in_since: false });
  assert.deepEqual([out.tone, out.action], ["warning", "signin"]);
  assert.match(out.text, /could not read Canvas on Oct 4: Faculty’s Canvas sign-in has ended/);
  assert.equal(nightlyNotice({ night: "2026-10-04", status: "signed_out", waiting: [], signed_in_since: true }), null);
  const failed = nightlyNotice({ night: "2026-10-04", status: "failed", waiting: [], problem: "Canvas could not be read (TimeoutError)." });
  assert.deepEqual([failed.tone, failed.action], ["warning", ""]);
  assert.match(failed.text, /could not finish on Oct 4\. Canvas could not be read \(TimeoutError\)\. It tries again each hour until midnight/);
  const one = nightlyNotice({ night: "2026-10-04", status: "imported", waiting: ["Sect. 2.1 Lines"] });
  assert.deepEqual([one.tone, one.action], ["info", "import"]);
  assert.equal(one.text, "A Canvas assignment waits for your choice of column before the morning sync fills it: “Sect. 2.1 Lines”.");
  assert.equal(one.skip, "Leave it out");
  const many = nightlyNotice({ night: "2026-10-04", status: "unchanged", waiting: ["A", "B", "C", "D", "E"] });
  assert.match(many.text, /^5 Canvas assignments wait .* fills them: “A”, “B”, “C” and 2 more\.$/);
  assert.equal(many.skip, "Leave them out");
  assert.equal(nightlyNotice({ night: "2026-10-04", status: "needs_choice", waiting: [], problem: "Give it points." }).action, "import");
  // The morning sync: its own name and hours, the grades it leaves to the instructor's Sync, the posts it could not make.
  const morning = nightlyNotice({ night: "2026-10-09", kind: "sync", status: "failed", waiting: [], problem: "Canvas could not be read." });
  assert.match(morning.text, /^The morning sync with Canvas could not finish on Oct 9\. .* each hour until 8 AM, then the next morning\.$/);
  assert.equal(nightlyNotice({ night: "2026-10-09", kind: "sync", status: "synced", waiting: [], sending: 4 }), null);
  const unsure = nightlyNotice({ night: "2026-10-09", kind: "sync", status: "unchanged", waiting: [], unsure: 2 });
  assert.deepEqual([unsure.tone, unsure.action], ["warning", "import"]);
  assert.match(unsure.text, /^2 grades wait for your own Sync with Canvas/);
  assert.match(nightlyNotice({ night: "2026-10-09", kind: "sync", status: "synced", waiting: [], posts_failed: 1 }).text, /could not post or hide 1 column in Canvas on Oct 9/);
  // ONE NOTICE (requirement, 2026-10-10): posts that did not take are the last sync's notice when Canvas did not confirm that
  // same send; said here when the send was confirmed (a later read), or when another sync came after.
  const run = { night: "2026-10-10", kind: "sync", status: "synced", waiting: [], posts_failed: 2, send: { id: "s1", status: "done" } };
  const sync = (send) => ({ at: "T", automatic: true, send: { id: "s1", status: "done", progress: { uncertain: 5 }, ...send } });
  assert.equal(nightlyNotice(run, sync({ unconfirmed: 5 })), null);
  assert.equal(nightlyNotice(run, sync({})), null); // a send recorded before `unconfirmed`: its own counts
  assert.match(nightlyNotice(run, sync({ unconfirmed: 0 })).text, /could not post or hide 2 columns in Canvas on Oct 10/);
  assert.match(nightlyNotice(run, sync({ id: "s2", unconfirmed: 5 })).text, /could not post or hide 2 columns/);
  assert.match(nightlyNotice({ ...run, waiting: ["Sect. 2.1 Lines"] }, sync({ unconfirmed: 5 })).text, /^A Canvas assignment waits/); // the next thing it needs
});

// SYNC WITH CANVAS (requirement, 2026-10-06): one header action, and a notice only when the last sync left something to do.
test("Sync with Canvas: Start from Canvas first, then Sync, and what the last sync left", () => {
  assert.equal(syncState(null), null);
  assert.equal(syncState({ linked_sections: 0 }), null);
  const fresh = syncState({ linked_sections: 4, started: false, last_sync: null });
  assert.deepEqual([fresh.label, fresh.notice], ["Start from Canvas", null]);
  const synced = syncState({ linked_sections: 4, started: true, changed_since: 3, last_sync: { at: "T", held: 0, send: { status: "done", progress: { accepted: 5 } } } }, (v) => `at ${v}`);
  assert.deepEqual([synced.label, synced.title, synced.notice], ["Sync with Canvas", "Last synced at T · 3 grades changed in Faculty since", null]);
  assert.equal(syncState({ linked_sections: 4, started: true, last_sync: { at: "T", automatic: true } }, (v) => `at ${v}`).title, "Last synced at T by the morning sync");
  const held = syncState({ linked_sections: 4, started: true, last_sync: { at: "T", held: 2 } });
  assert.deepEqual([held.notice.tone, held.notice.text], ["info", "2 grades changed in Canvas since Faculty last knew them wait for your choice in Sync with Canvas."]);
  const unsure = syncState({ linked_sections: 4, started: true, last_sync: { at: "T", held: 2, send: { status: "done", progress: { failed: 1, uncertain: 1 } } } });
  assert.equal(unsure.notice.tone, "warning");
  assert.match(unsure.notice.text, /could not confirm 2 grades in Canvas/);
  // CONFIRMED BY A LATER READ (requirement, 2026-10-10): the notice counts what Canvas is still not known to hold, and says the
  // posts the morning sync could not make for that send in the same sentence (ONE NOTICE).
  const morning = (unconfirmed, nightly = {}) => syncState({ linked_sections: 4, started: true, nightly: { send: { id: "s1" }, ...nightly },
    last_sync: { at: "T", held: 0, automatic: true, send: { id: "s1", status: "done", progress: { uncertain: 5 }, unconfirmed } } }).notice;
  assert.equal(morning(5, { posts_failed: 2 }).text, "The last sync could not confirm 5 grades in Canvas, and 2 columns were not posted or hidden there. Sync again: Faculty reads Canvas first and sends only what Canvas still lacks.");
  assert.match(morning(1, { posts_failed: 1 }).text, /could not confirm 1 grade in Canvas, and 1 column was not posted or hidden there\./);
  assert.equal(morning(3).text, "The last sync could not confirm 3 grades in Canvas. Sync again: Faculty reads Canvas first and sends only what Canvas still lacks.");
  assert.match(morning(3, { posts_failed: 2, send: { id: "another" } }).text, /in Canvas\. Sync again/); // another run's posts are its own notice
  assert.equal(morning(0, { posts_failed: 2 }), null); // every grade confirmed since: nothing left of the send
  const cut = syncState({ linked_sections: 4, started: true, last_sync: { at: "T", send: { id: "s1", status: "interrupted", progress: { uncertain: 2 }, unconfirmed: 0 } } });
  assert.match(cut.notice.text, /^The last sync stopped before Canvas confirmed every grade\./); // values it never sent have no receipt
  const heldAfter = syncState({ linked_sections: 4, started: true, last_sync: { at: "T", held: 1, send: { id: "s1", status: "done", progress: { uncertain: 2 }, unconfirmed: 0 } } });
  assert.equal(heldAfter.notice.tone, "info");
  // WebAssign's columns are read-only: no bulk marking, a locked cell.
  assert.deepEqual(emptyCells({ cells: {} }, { id: "w", canvas: "webassign", sections: [] }, students), []);
  assert.equal(cellParts({ id: "w", canvas: "webassign", sections: [] }, students[0], null).locked, true);
  assert.equal(cellParts({ id: "q", canvas: "quiz", sections: [] }, students[0], null).locked, true); // a Canvas quiz: Canvas's too
  assert.equal(cellParts({ id: "o", canvas: "off", sections: [] }, students[0], null).locked, false);
});

test("cells and column averages show two decimals; the stored four stay in the editor, the copy and the title (2026-10-04)", () => {
  assert.deepEqual(["10.6667", "7.5", "8", "9.0476", "2.25"].map((value) => shownPoints(value).text), ["10.67", "7.5", "8", "9.05", "2.25"]);
  const item = { id: "i1", points: "12", sections: [] }, student = { id: "s1", section: "507" };
  const parts = cellParts(item, student, { status: "scored", points: "10.6667", source: "manual" });
  assert.equal(parts.html, '<span title="10.6667">10.67</span>');
  assert.equal(cellParts(item, student, { status: "scored", points: "7.5", source: "manual" }).html, "7.5");
  assert.equal(entryText({ status: "scored", points: "10.6667" }), "10.6667");
});

test("DIFFERS FROM GRADING: the check's rows mark cells until their value changes, and say what grading has", () => {
  const data = view({ cells: { ...view().cells, i3: { "s1:e1": cell("15", { id: "c-a", source: "grading" }), "s2:e2": cell("18", { id: "c-b" }) } } });
  const check = { columns: {
    i3: { fix: "certify", plan_id: "plan-1", certified_at: "2026-09-27T23:49:34+00:00", rows: [
      { row: "s1:e1", section_id: "s1", kind: "changed", cell: { id: "c-a", status: "scored", points: "15", source: "grading" }, grading: { status: "scored", points: "14.6667" } },
      { row: "s2:e2", section_id: "s2", kind: "typed", cell: { id: "c-b", status: "scored", points: "18", source: "instructor" }, grading: { status: "excused", points: null } },
      // A row the check read before its cell changed (another revision): no mark until the next check.
      { row: "s2:e2", section_id: "s2", kind: "changed", cell: { id: "c-old", status: "scored", points: "1", source: "grading" }, grading: { status: "scored", points: "2" } }] },
    i1: { fix: "pull", plan_id: null, certified_at: null, rows: [], elsewhere: { changed: 2, typed: 0 } } } };
  const marks = differences(data, check);
  assert.deepEqual([...marks.keys()], ["i3\u0000s1:e1", "i3\u0000s2:e2"]);
  assert.equal(marks.get("i3\u0000s2:e2").kind, "typed", "the stale row is dropped, the current one kept");
  assert.equal(differences(data, null).size, 0);
  // An empty cell matches a check row about an empty (or cleared) one; a cell emptied since the check no longer matches its row.
  const empty = { columns: { i2: { fix: "pull", rows: [{ row: "s1:e1", kind: "changed", cell: { id: "c-x", status: "cleared", points: null }, grading: { status: "scored", points: "5" } },
    { row: "s2:e2", kind: "changed", cell: { id: "c-4", status: "scored", points: "4" }, grading: { status: "scored", points: "5" } }] },
    i4: { fix: "pull", rows: [{ row: "s1:e1", kind: "changed", cell: { id: "c-9", status: "scored", points: "9" }, grading: { status: "scored", points: "5" } }] } } };
  assert.deepEqual([...differences(data, empty).keys()], ["i2\u0000s1:e1", "i2\u0000s2:e2"]);

  assert.deepEqual(["14.6667", "8"].map((points) => gradingValue({ status: "scored", points })), ["14.67", "8"]);
  assert.deepEqual([gradingValue({ status: "excused" }), gradingValue({ status: "missing" }), gradingValue({ status: "cleared" }), gradingValue(null)], ["EX", "MI", "no value", "no value"]);
  assert.deepEqual(differsLines(marks.get("i3\u0000s1:e1"), () => "Sep 27"),
    ["Differs from Grading, which has 14.67 now", "Grading changed after you certified it on Sep 27: certify again in Grading to record it"]);
  assert.deepEqual(differsLines(marks.get("i3\u0000s2:e2")), ["Differs from Grading, which has EX", "You typed this value: a pull or a certification keeps it unless you tick Replace"]);
  assert.deepEqual(differsLines({ kind: "changed", fix: "pull", grading: { status: "scored", points: "5" } }), ["Differs from Grading, which has 5", "Pull this column to bring it in"]);

  // The grid: the cells, the heading's chip and the key; the filter shows the rows that differ.
  const model = gridModel(data);
  const markup = gridMarkup(data, model, { differs: marks });
  assert.match(markup, /class="gb-cell st-scored src-grading is-differs" data-cell data-item="i3" data-row="s1:e1"/);
  assert.match(markup, /class="gb-head gb-linked gb-differs"/);
  assert.match(markup, /<span class="gb-head-differs" data-head-differs>2 differ<\/span><\/button>/);
  assert.doesNotMatch(gridMarkup(data, model), /is-differs|gb-differs/);
  assert.equal(differsCount(marks, "i3", students), 2);
  assert.equal(differsCount(marks, "i3", [students[0]]), 1);
  assert.equal(differsChip(0), "");
  assert.equal(differsChip(1), '<span class="gb-head-differs" data-head-differs>1 differs</span>');
  assert.ok(marksPresent(data, model, { differs: marks }).includes("differs"));
  assert.ok(!marksPresent(data, model).includes("differs"));
  assert.match(cellParts(data.setup.items[2], students[0], data.cells.i3["s1:e1"], { differs: true }).cls, /\bis-differs\b/);
  assert.doesNotMatch(cellParts(data.setup.items[2], students[0], data.cells.i3["s1:e1"], { differs: true, pending: { status: "scored", points: "3" } }).cls, /is-differs/,
    "a value being saved is not marked");
  const rows = new Set(["s1:e1"]);
  assert.equal(attentionMatch(data, students[0], { kind: "differs" }, null, rows), true);
  assert.equal(attentionMatch(data, students[1], { kind: "differs" }, null, rows), false);

  // The notice: a line per column in the setup's order, what differs on screen and elsewhere, and its fix.
  assert.deepEqual(differsSummary(data, check, marks, students, () => "Sep 27"), [
    { item: "i1", title: "HW 1", text: "2 in other sections", fix: "pull", plan_id: null, first: "", count: 0, changed: 0 },
    { item: "i3", title: "Test 1", text: "1 grade changed in Grading after you certified it on Sep 27 · 1 value you typed differs from Grading", fix: "certify",
      plan_id: "plan-1", first: "s1:e1", count: 2, changed: 1 }]);
  // One section shown: the other section's rows count as elsewhere.
  assert.deepEqual(differsSummary(data, check, marks, [students[1]]).find((line) => line.item === "i3"),
    { item: "i3", title: "Test 1", text: "1 value you typed differs from Grading · 1 more in another section", fix: "certify", plan_id: "plan-1", first: "s2:e2", count: 1, changed: 0 });
});

test("a counted 0 says why: certified, due, or posted in Canvas (GRADEBOOK ZERO ONCE DUE, POSTED)", () => {
  const view = { results: { due_posted: ["takehome"] } };
  const takehome = { id: "takehome", due_date: "" }, quiz = { id: "quiz", due_date: "2026-10-01" }, exam = { id: "exam", link: { kind: "grading" } };
  assert.equal(duePosted(view, takehome), true);
  assert.equal(duePosted(view, quiz), false);
  assert.equal(duePosted({ results: {} }, takehome), false);
  assert.match(blankWords(takehome, "zero", duePosted(view, takehome))[1], /its grades are posted in Canvas/);
  assert.match(blankWords(quiz, "zero", duePosted(view, quiz))[1], /due /);
  assert.match(blankWords(exam, "zero")[1], /its grading is certified/);
  assert.equal(blankWords(takehome, "makeup", true)[0], "no score, makeup owed");  // a wait keeps its own words
});
