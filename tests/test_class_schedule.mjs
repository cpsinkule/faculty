// The pure parts of a section's Class schedule page (static/class-schedule-model.js; SCHEDULING "CLASS SCHEDULE"): a class day's
// topics as typed, the days grouped into weeks, and a planned row's values between its form and what the server keeps.
import assert from "node:assert/strict";
import test from "node:test";
import { dateFor, formatTopics, matchRow, mondayOf, packRow, parseTopics, scopeLine, splitWeeks, topicEntry, unpackRow, weekNumber } from "../static/class-schedule-model.js";

test("a class day's topics are the notes sections typed first, then a note kept as written", () => {
  assert.deepEqual(parseTopics("2.3 2.4 review"), { notes: ["2.3", "2.4"], note: "review" });
  assert.deepEqual(parseTopics(" 2.3, 2.4;  "), { notes: ["2.3", "2.4"], note: "" });
  assert.deepEqual(parseTopics("2.3-2.5"), { notes: ["2.3", "2.4", "2.5"], note: "" });
  assert.deepEqual(parseTopics("2.3–2.5 start 3.1"), { notes: ["2.3", "2.4", "2.5"], note: "start 3.1" });
  assert.deepEqual(parseTopics("Exam 2 review"), { notes: [], note: "Exam 2 review" });
  assert.deepEqual(parseTopics("2.3 2.3 2.3"), { notes: ["2.3"], note: "" });
  assert.deepEqual(parseTopics(""), { notes: [], note: "" });
  // A range that would pass eight sections is words, never a silent cut.
  assert.deepEqual(parseTopics("2.1-2.12"), { notes: [], note: "2.1-2.12" });
  assert.deepEqual(parseTopics("1.1 1.2 1.3 1.4 1.5 1.6 1.7 1.8 1.9").notes.length, 8);
  assert.equal(parseTopics("1.1 1.2 1.3 1.4 1.5 1.6 1.7 1.8 1.9").note, "1.9");
  for (const text of ["2.3 2.4 review", "Exam 2 review", "2.3", ""]) assert.equal(formatTopics(parseTopics(text)), text);
  assert.equal(formatTopics({ notes: [{ number: "3.2", title: "Matrices" }], note: "Quiz" }), "3.2 Quiz");
  assert.equal(formatTopics(null), "");
});

test("a typed day changes the term's entry or this section's own, never another section's", () => {
  const stored = { notes: ["3.2"], note: "", sections: { b: { notes: ["3.1"], note: "" } } };
  assert.deepEqual(topicEntry(stored, "a", { notes: ["3.3"], note: "" }, false), { notes: ["3.3"], note: "", sections: { b: { notes: ["3.1"], note: "" } } });
  assert.deepEqual(topicEntry(stored, "a", { notes: ["3.3"], note: "" }, true),
    { notes: ["3.2"], note: "", sections: { a: { notes: ["3.3"], note: "" }, b: { notes: ["3.1"], note: "" } } });
  // Unticking "Only" drops this section's own entry; an entry that says nothing is taken away.
  assert.deepEqual(topicEntry(stored, "b", { notes: ["3.2"], note: "" }, false), { notes: ["3.2"], note: "", sections: {} });
  assert.equal(topicEntry(undefined, "a", { notes: [], note: "" }, false), null);
  assert.equal(topicEntry({ notes: [], note: "", sections: { a: { notes: ["1.1"], note: "" } } }, "a", { notes: [], note: "" }, true), null);
  assert.deepEqual(stored.sections, { b: { notes: ["3.1"], note: "" } }); // the stored entry is never changed in place
});

test("days group into weeks: earlier ones fold, finals come after classes end", () => {
  assert.equal(mondayOf("2026-10-14"), "2026-10-12");
  assert.equal(mondayOf("2026-10-18"), "2026-10-12"); // a Sunday belongs to the week before it
  assert.equal(weekNumber("2026-08-24", "2026-08-24"), 1);
  assert.equal(weekNumber("2026-10-14", "2026-08-24"), 8);
  assert.equal(weekNumber("2026-08-20", "2026-08-24"), 0);
  assert.equal(weekNumber("2026-10-14", ""), 0);
  const days = ["2026-10-05", "2026-10-07", "2026-10-12", "2026-10-14", "2026-12-02", "2026-12-11"].map((date) => ({ date }));
  const runs = splitWeeks(days, "2026-10-14", "2026-08-24", "2026-12-03");
  assert.deepEqual(runs.earlier.map((week) => [week.monday, week.number, week.days.length]), [["2026-10-05", 7, 2]]);
  assert.deepEqual(runs.current.map((week) => week.monday), ["2026-10-12", "2026-11-30"]);
  assert.deepEqual(runs.after.map((week) => week.days[0].date), ["2026-12-11"]);
  // Before the term starts nothing is earlier; once everything is past, it is shown rather than folded away.
  assert.equal(splitWeeks(days, "2026-08-01", "2026-08-24", "2026-12-03").earlier.length, 0);
  const over = splitWeeks(days.slice(0, 4), "2027-01-10", "2026-08-24", "2026-12-03");
  assert.deepEqual([over.earlier.length, over.current.length], [0, 2]);
});

test("a planned row's values: shared where sections agree, a section's own where they differ", () => {
  const sections = [{ id: "a", number: 507 }, { id: "b", number: 508 }, { id: "c", number: 514 }, { id: "d", number: 515 }];
  // A regular exam: one day and time, four rooms.
  const exam = packRow(sections.map((section, n) => ({ id: section.id, date: "2026-11-17", time: "7:30 PM–9:30 PM", place: `ROOM ${n}` })));
  assert.deepEqual([exam.date, exam.time, exam.place], ["2026-11-17", "7:30 PM–9:30 PM", ""]);
  assert.deepEqual(exam.section_values, { a: { place: "ROOM 0" }, b: { place: "ROOM 1" }, c: { place: "ROOM 2" }, d: { place: "ROOM 3" } });
  // A final: each section its own day; two share a room.
  const final = packRow([{ id: "a", date: "2026-12-11", time: "8:00 AM", place: "HELD 107" }, { id: "b", date: "2026-12-14", time: "8:00 AM", place: "HELD 107" },
    { id: "c", date: "2026-12-15", time: "1:00 PM", place: "BLOC 149" }]);
  assert.deepEqual([final.date, final.time, final.place], ["", "8:00 AM", "HELD 107"]);
  assert.deepEqual(final.section_values, { a: { date: "2026-12-11" }, b: { date: "2026-12-14" }, c: { date: "2026-12-15", time: "1:00 PM", place: "BLOC 149" } });
  assert.deepEqual(packRow([{ id: "a", date: "2026-10-23", time: "", place: "" }]), { date: "2026-10-23", time: "", place: "", section_values: {} });
  // Read back for the form: every section's values spelled out.
  const shown = unpackRow({ sections: [], date: "2026-11-17", time: "7:30 PM–9:30 PM", place: "", section_values: exam.section_values }, sections);
  assert.equal(shown.differ, true);
  assert.deepEqual(shown.rows[1], { id: "b", number: 508, date: "2026-11-17", time: "7:30 PM–9:30 PM", place: "ROOM 1" });
  assert.equal(unpackRow({ sections: ["a"], date: "2026-10-23", time: "", place: "", section_values: {} }, sections).rows.length, 1);
  assert.equal(unpackRow({ sections: [], date: "2026-10-23", section_values: {} }, sections).differ, false);
  const label = (/** @type {string} */ iso) => iso;
  const row = (/** @type {number} */ number, date = "2026-10-23", place = "", time = "") => ({ number, date, time, place });
  assert.equal(scopeLine([row(507), row(508), row(514), row(515)], 4, label), "Every section · 2026-10-23");
  assert.equal(scopeLine([row(507, "2026-11-17", "A"), row(508, "2026-11-17", "B")], 4, label), "2 sections · same day, 2 rooms");
  assert.equal(scopeLine([row(507, "2026-12-11"), row(508, "2026-12-14")], 2, label), "2 sections · 2 dates");
  assert.equal(scopeLine([row(507)], 4, label), "Section 507 only · 2026-10-23");
  assert.equal(scopeLine([row(507, ""), row(508, "")], 2, label), "Every section · no date yet");
  assert.equal(scopeLine([], 4, label), "Choose at least one section");
});

test("a form finds the planned row its title names and the date its sections share", () => {
  const rows = [{ item: "1", title: "Quiz 6" }, { item: "2", title: "Exam 3" }];
  assert.equal(matchRow("  quiz   6 ", rows)?.item, "1");
  assert.equal(matchRow("Quiz", rows), null);
  assert.equal(matchRow("", rows), null);
  assert.equal(matchRow("Quiz 6", [...rows, { item: "3", title: "QUIZ 6" }]), null); // two of that name: none is chosen for you
  const final = { date: "", section_values: { a: { date: "2026-12-11" }, b: { date: "2026-12-14" } } };
  assert.equal(dateFor({ date: "2026-10-23", section_values: {} }, []), "2026-10-23");
  assert.equal(dateFor(final, ["a"]), "2026-12-11");
  assert.equal(dateFor(final, ["a", "b"]), ""); // the sections differ: the form fills nothing
  assert.equal(dateFor(final, []), "");
  assert.equal(dateFor({ date: "2026-10-23", section_values: { a: { place: "X" } } }, []), "2026-10-23");
});
