import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The Accommodations page's pure helpers (UX overhaul, 2026-09-26): derived views of /api/accommodations/overview only.
const page = await import(new URL("../static/accommodations_page.js", import.meta.url).href);
const shared = await import(new URL("../static/accommodations.js", import.meta.url).href);
const { readRoute, routeHash, filterStudents, bookingRows, groupBookings, timeSpread, statusLine, freshnessMarkup, typeCounts, examMatches, seatingLinksMarkup, aimMarkup, shownTypes } = page;
const { scheduleText, scheduleChanges, schedulePartsMarkup, previewMarkup, accommodationTags, completionMarkup } = shared;
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const booking = (changes = {}) => ({ id: "b1", exam_type: "Test 1", date: "2026-09-17", start_time: "19:05", end_time: "20:58", length_minutes: 113, campus: "College Station", requested_on: "2026-09-03", assignment: { id: "a1", title: "Test 1" }, ...changes });
const student = (changes = {}) => ({ id: "e1", uin: "001234567", name: "Example, Alice", code: "MATH 140", term: "Fall", term_other: "", year: 2026, term_id: "t1", section: 507, section_id: "s1", is_current: true, course_url: "#course/g/section/507", accommodations: null, dr_exams: [], ...changes });

test("the address carries tab, dates, course and exam only, and reads them back", () => {
  assert.deepEqual(readRoute("#accommodations?tab=exams&course=MATH%20140&exam=Test%201&when=all"), { tab: "exams", when: "all", course: "MATH 140", exam: "Test 1" });
  assert.deepEqual(readRoute("#accommodations"), { tab: "students" });
  assert.equal(readRoute("#accommodations?tab=exams&when=soon").when, "upcoming");
  assert.equal(routeHash({ tab: "exams", when: "upcoming", course: "MATH 140", exam: "Test 1" }), "#accommodations?tab=exams&course=MATH%20140&exam=Test%201");
  assert.equal(routeHash({ tab: "students", when: "all", exam: "Test 1" }), "#accommodations");
});

test("Upcoming compares campus date strings and groups by linked exam, course and term", () => {
  const alice = student({ dr_exams: [booking(), booking({ id: "b2", exam_type: "Exam 2", date: "2026-10-22", assignment: { id: "a2", title: "Test 2" } })] });
  const bob = student({ id: "e2", name: "Example, Bob", uin: "123456789", code: "MATH 151", term_id: "t2", section_id: "s2", dr_exams: [booking({ id: "b3", start_time: "18:30", end_time: "20:23" })] });
  const today = "2026-09-26";
  assert.equal(bookingRows([alice, bob], { when: "upcoming" }, today).length, 1);
  assert.equal(bookingRows([alice, bob], { when: "past" }, today).length, 2);
  assert.equal(bookingRows([alice, bob], { when: "all" }, "2026-10-22").length, 3);
  assert.equal(bookingRows([alice], { when: "upcoming" }, "2026-10-22").length, 1, "the exam day itself is upcoming");
  const groups = groupBookings(bookingRows([alice, bob], { when: "all" }, today), "all");
  assert.deepEqual(groups.map((group) => [group.title, group.code]), [["Test 1", "MATH 140"], ["Test 1", "MATH 151"], ["Test 2", "MATH 140"]]);
  assert.deepEqual(groups[2].names, ["Exam 2"], "the DR center's own name is shown when it differs");
  assert.deepEqual(groupBookings(bookingRows([alice, bob], { when: "past" }, today), "past").map((group) => group.code), ["MATH 140", "MATH 151"]);
  assert.ok(examMatches(alice.dr_exams[1], "test 2") && examMatches(alice.dr_exams[1], "Exam 2") && !examMatches(alice.dr_exams[1], "Test 1"));
  assert.equal(timeSpread([{ booking: booking() }, { booking: booking() }, { booking: booking({ start_time: "18:30" }) }]), "2 at 7:05 PM · 1 at 6:30 PM");
});

test("filters by term, course and search; never by anything in the address about a student", () => {
  const past = student({ id: "e3", is_current: false, year: 2025, name: "Old, Carol", uin: "222222222" });
  const list = [student(), past];
  assert.equal(filterStudents(list).length, 1);
  assert.equal(filterStudents(list, { term: "all" }).length, 2);
  assert.equal(filterStudents(list, { term: "all", search: "2222" })[0].name, "Old, Carol");
  assert.equal(filterStudents(list, { term: "all", course: "math 140" }).length, 2);
});

test("the status line counts current terms and names the next DR exam", () => {
  const students = [
    student({ accommodations: { items: [{ name: "Extra Time 1.50x", value: "Yes" }] }, dr_exams: [booking({ date: "2026-10-22", assignment: { id: "a2", title: "Test 2" } })] }),
    student({ id: "e2", dr_exams: [booking({ date: "2026-10-22", assignment: { id: "a2", title: "Test 2" } }), booking({ date: "2026-09-01" })] }),
    student({ id: "e3", is_current: false, accommodations: { items: [{ name: "Reader", value: "Yes" }] }, dr_exams: [booking({ date: "2026-10-01" })] }),
  ];
  assert.equal(statusLine(students, "2026-09-26"), "1 student with accommodations · 2 upcoming DR bookings · next: Test 2, Thu, Oct 22 · 2 students");
  assert.equal(statusLine([], "2026-09-26"), "0 students with accommodations · no upcoming DR bookings");
});

test("freshness links each import record and counts rows it could not import", () => {
  const at = new Date(2026, 8, 14, 10, 0).toISOString();
  const html = freshnessMarkup({ accommodations: { job_id: "j1", imported_at: at, not_imported: 3 }, dr_exams: null }, escape, "2026-09-26");
  assert.match(html, /Accommodations imported <a href="#inbox\/job\/j1">Mon, Sep 14<\/a> \(12 days ago\)/);
  assert.match(html, /3 rows weren’t imported/);
  assert.match(html, /No DR export imported yet/);
});

test("accommodation types count and tags keep names verbatim, escaped", () => {
  const students = [student({ accommodations: { items: [{ name: "Extra Time 1.50x", value: "Yes" }, { name: "<b>Reader</b>", value: "As needed" }] } }),
    student({ accommodations: { items: [{ name: "Extra Time 1.50x", value: "Yes" }] } })];
  assert.deepEqual(typeCounts(students), [["Extra Time 1.50x", 2], ["<b>Reader</b>", 1]]);
  const tags = accommodationTags({ ...students[0].accommodations, metadata: { Notes: "<img>", Secret: "x" }, job_id: "j", updated_at: "2026-09-13T00:00:00" }, escape, { key: "k", open: true });
  assert.match(tags, /&lt;b&gt;Reader&lt;\/b&gt;: As needed/);
  assert.match(tags, /data-key="k" open/);
  assert.match(tags, /Notes:<\/strong> &lt;img&gt;/);
  assert.doesNotMatch(tags, /Secret/);
});

test("DR schedules read as wall-clock values and changes are named", () => {
  assert.equal(scheduleText(booking()), "Test 1 · Thu, Sep 17 · 7:05 PM–8:58 PM · 113 min · College Station");
  const moved = booking({ date: "2026-09-18", start_time: "11:00", end_time: "12:53" });
  assert.deepEqual(scheduleChanges(moved, booking()), ["Date: Thu, Sep 17 → Fri, Sep 18", "Time: 7:05 PM–8:58 PM → 11:00 AM–12:53 PM"]);
  assert.equal(schedulePartsMarkup(moved, booking(), escape), "Test 1 · <mark>Fri, Sep 18</mark> · <mark>11:00 AM–12:53 PM</mark> · 113 min · College Station");
});

test("the import preview leads with rows that can't be imported, grouped by reason", () => {
  const rows = [
    { row: 2, name: "A", code: "MATH 140", section: 507, status: "added", accommodations: [{ name: "Extra Time", value: "Yes" }], label: "MATH 140 · Fall 2026 · Section 507" },
    { row: 3, name: "B", code: "MATH 140", section: 508, status: "unmatched", reason: "No matching current course and section." },
    { row: 4, name: "C", code: "MATH 140", section: 508, status: "unmatched", reason: "No matching current course and section." },
    { row: 5, name: "D", code: "MATH 140", section: 507, status: "unchanged", accommodations: [] },
  ];
  const html = previewMarkup({ rows, total: 4, counts: { added: 1, updated: 0, unchanged: 1, unmatched: 2, ambiguous: 0, stale: 0 } }, false, escape);
  assert.match(html, /2 of 4 rows match your rosters/);
  assert.match(html, /1 will be imported<\/strong> · 2 can’t be/);
  assert.match(html, /1 new · 0 updated · 1 unchanged/);
  assert.ok(html.indexOf("Can’t be imported") < html.indexOf(">New "), "problems come first");
  assert.equal((html.match(/No matching current course and section\./g) || []).length, 1, "a reason is stated once for its rows");
  assert.match(html, /<details class="disclosure accommodation-preview-unchanged"/);
  const same = previewMarkup({ rows: [rows[3]], total: 1, counts: { added: 0, updated: 0, unchanged: 1, unmatched: 0, ambiguous: 0, stale: 0 } }, false, escape);
  assert.match(same, /Everything in this file is already saved\./);
  const named = previewMarkup({ rows: [{ ...rows[0], name: "Lane H Gibson", display_name: "Gibson, Lane H" }, { ...rows[1], name: "Nobody Example", display_name: "Example, Nobody" }], total: 2, counts: { added: 1, updated: 0, unchanged: 0, unmatched: 1, ambiguous: 0, stale: 0 } }, false, escape);
  assert.match(named, /Gibson, Lane H/);
  assert.match(named, /Example, Nobody/);
  assert.doesNotMatch(named, /Lane H Gibson|Nobody Example/, "names read 'Last, First' as on the rosters");
  assert.match(completionMarkup({ exam_links: { created_assignments: [{ title: "Final <x>", code: "MATH 140" }] } }, true, escape), /New assignment created: Final &lt;x&gt; \(MATH 140\)/);
});

test("the page source keeps inline styles and student identifiers out of its markup and address", async () => {
  const source = await readFile(new URL("../static/accommodations_page.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /style="/);
  assert.doesNotMatch(source, /routeHash\([^)]*(search|uin|name)/i);
});

test("the DR group's seating link follows the live check, not the counts recorded at generation", () => {
  const chart = (check, counts = { check_ok: true }) => ({ id: "c1", room_name: "HECC 105", title: "Test 1", counts, check });
  const fine = seatingLinksMarkup([chart({ ok: true, counts: { dr_seated: 0 } })], escape);
  assert.match(fine, /not seated there \(DR testing\)/);
  assert.doesNotMatch(fine, /needs update/);
  // A DR booking imported after generation: the recorded counts still say ok, the live check does not.
  const seated = seatingLinksMarkup([chart({ ok: false, counts: { dr_seated: 1 } })], escape);
  assert.match(seated, /needs update/);
  assert.match(seated, /1 DR student still seated · update the chart/);
  assert.doesNotMatch(seated, /not seated there/);
  // Without a live check nothing is claimed about where the DR students sit.
  const unknown = seatingLinksMarkup([chart(undefined)], escape);
  assert.doesNotMatch(unknown, /not seated there|still seated/);
});

test("the coordinator/notes disclosure uses the shared +/− disclosure", () => {
  const html = accommodationTags({ items: [{ name: "Extra Time", value: "Yes" }], metadata: { Notes: "x" }, updated_at: "2026-09-13", job_id: "j1" }, escape, { key: "acc:s:e" });
  assert.match(html, /<details class="disclosure accommodation-source" data-key="acc:s:e"/);
});

test("the From AIM notice: one Review and import while anything waits, names escaped", () => {
  const file = (name) => ({ name });
  const ready = { term: "Fall 2026", files: { accommodations: file("A<b>.xlsx"), exams: file("Exams.xlsx") }, problems: [], done: {} };
  const both = aimMarkup(ready, escape);
  assert.match(both, /From AIM · Fall 2026/);
  assert.match(both, /A&lt;b&gt;\.xlsx/);
  assert.equal((both.match(/button primary/g) || []).length, 1);
  assert.match(both, /class="button primary" data-aim-review>Review and import/);
  assert.equal((both.match(/Not imported yet/g) || []).length, 2);
  const half = aimMarkup({ ...ready, done: { accommodations: true } }, escape);
  assert.match(half, /data-tone="success">Imported/);
  assert.match(half, /data-aim-review/);
  const done = aimMarkup({ ...ready, done: { accommodations: true, exams: true } }, escape);
  assert.match(done, /Both exports are imported\./);
  assert.doesNotMatch(done, /data-aim-review/);
  assert.match(aimMarkup({ ...ready, files: { exams: file("Exams.xlsx") }, done: { exams: true } }, escape), /The export is imported\./);
  assert.match(aimMarkup({ ...ready, problems: ["AIM's <x> page"] }, escape), /AIM&#39;s &lt;x&gt; page/);
  assert.equal(aimMarkup(null, escape), "");
  assert.equal(readRoute("#accommodations?aim=0f8fad5b-d9cb-469f-a165-70867728950e").aim, undefined, "the address carries no AIM state");
});

test("type chips: the six most common, the chosen one beside them, the rest behind N more types", () => {
  const types = Array.from({ length: 18 }, (_, index) => [`Type ${String(index).padStart(2, "0")}`, 18 - index]);
  const folded = shownTypes(types, "", false);
  assert.deepEqual(folded.shown.map(([name]) => name), types.slice(0, 6).map(([name]) => name));
  assert.equal(folded.hidden, 12);
  const chosen = shownTypes(types, "Type 15", false);
  assert.deepEqual(chosen.shown.map(([name]) => name).slice(-1), ["Type 15"]);
  assert.equal(chosen.shown.length, 7);
  assert.equal(chosen.hidden, 11);
  assert.deepEqual(shownTypes(types, "", true), { shown: types, hidden: 0 });
  // Seven types: all of them (a chip saying "1 more type" would take the same room as the type).
  assert.deepEqual(shownTypes(types.slice(0, 7), "", false), { shown: types.slice(0, 7), hidden: 0 });
});
