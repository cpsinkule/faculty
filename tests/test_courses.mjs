import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// courses.js imports ui.js (which imports icons.js), so it is loaded from its file.
const courses = await import(new URL("../static/courses.js", import.meta.url).href);
const { rosterSummary, rosterColumns, rosterValue, duplicateSection, sectionFromFileName, leavingText, workRows, workCounts, sectionDetail, studentsLabel, taMailto, taSubject, mergeTAs } = courses;
const source = await readFile(new URL("../static/courses.js", import.meta.url), "utf8");

test("the roster summary reads like a sentence and keeps the import counts", () => {
  const at = new Date(2026, 8, 13, 10, 0).toISOString();
  const data = { count: 85, accommodation_count: 1, dr_exam_count: 1, last_import: { at, filename: "roster.xlsx", added: 85, updated: 0 } };
  assert.equal(rosterSummary(data, "2026-09-26"), "85 students · 1 with accommodations · 1 DR booking · Imported Sun, Sep 13 (13 days ago) from roster.xlsx · 85 added · 0 updated");
  assert.equal(rosterSummary({ count: 1, last_import: null }, "2026-09-26"), "1 student");
  const today = new Date(2026, 8, 26, 9, 0).toISOString();
  assert.match(rosterSummary({ count: 2, dr_exam_count: 3, last_import: { at: today, filename: "r.csv", added: 0, updated: 2 } }, "2026-09-26"), /3 DR bookings · Imported Sat, Sep 26 \(today\) from r\.csv · 0 added · 2 updated$/);
});

test("roster columns put name, UIN and email first and set empty columns apart", () => {
  const students = [
    { fields: { Status: "", Name: "A", UIN: "001234567", Email: "a@x", Major: "MATH", Final: "" } },
    { fields: { Status: " ", Name: "B", UIN: "123456789", Email: "", Major: "", Final: "" } },
  ];
  const columns = rosterColumns(["Status", "Major", "Email", "Name", "UIN", "Final"], students);
  assert.deepEqual(columns.lead, ["Name", "UIN", "Email"]);
  assert.deepEqual(columns.rest, ["Major"]);
  assert.deepEqual(columns.empty, ["Status", "Final"]);
});

test("a section may be chosen for one file only; files without a section are ignored", () => {
  assert.equal(duplicateSection(["507", "", "508"]), null);
  assert.equal(duplicateSection(["507", "", "507"]), "507");
  assert.equal(duplicateSection(["", ""]), null);
});

test("section counts read as tiles and chips", () => {
  assert.equal(studentsLabel({ students: 85 }), "85 students");
  assert.equal(studentsLabel({ students: 1 }), "1 student");
  assert.equal(studentsLabel({ students: 0 }), "No roster yet");
  assert.equal(sectionDetail({ students: 85, with_accommodations: 1, dr_bookings: 2 }), "85 students · 1 with accommodations · 2 DR bookings");
  assert.equal(sectionDetail({ students: 3, with_accommodations: 0, dr_bookings: 0 }), "3 students");
  assert.equal(sectionDetail({ students: 0 }), "No roster yet · import it on the section page");
  assert.equal(sectionDetail(null), "");
});

test("the work hub lists this term's work, and on a section tab only what names the section", () => {
  const sources = {
    exams: [
      { id: "e1", title: "Test 1", term_id: "T", sections: [{ id: "S1" }, { id: "S2" }] },
      { id: "e2", title: "Makeup", term_id: "T", sections: [{ id: "S2" }] },
      { id: "e3", title: "Old", term_id: "OTHER", sections: [{ id: "S1" }] },
    ],
    seating: [
      { id: "c1", title: "Test 1", room_name: "HECC 105", term_id: "T", section_ids: ["S1"], sections: [507], confirmed_at: "x" },
      { id: "c2", title: "Test 1", room_name: "HECC 209", term_id: "T", section_ids: ["S2"], sections: [508], confirmed_at: "" },
    ],
    grading: [{ id: "g1", title: "Test 1", term_id: "T" }, { id: "g2", title: "Quiz", term_id: "OTHER" }],
    cards: [{ id: "i1", title: "Test 1 seat cards", term_id: "T", section_ids: ["S1"] }, { id: "i2", term_id: "T", section_ids: ["S2"] }],
  };
  const term = workRows({ termId: "T", ...sources });
  assert.deepEqual(term.map((row) => row.kind), ["Exams", "Seating charts", "Grading", "Response Cards"]);
  assert.deepEqual(term[0].items.map((item) => item.href), ["#exams/e1", "#exams/e2"]);
  assert.deepEqual(term[1].items.map((item) => [item.label, item.note]), [["Test 1 · HECC 105", ""], ["Test 1 · HECC 209", "not confirmed"]]);
  assert.deepEqual(term[2].items, [{ label: "Test 1", href: "#grading/g1" }]);
  assert.deepEqual(term[3].items, [{ label: "Test 1 seat cards", href: "#response-cards/issue/i1" }, { label: "Response cards", href: "#response-cards/issue/i2" }]);
  const section = workRows({ termId: "T", sectionId: "S1", sectionNumber: 507, ...sources });
  assert.deepEqual(section.map((row) => row.kind), ["Exams", "Seating charts", "Grading · whole term", "Response Cards"]);
  assert.deepEqual(section[0].items.map((item) => item.label), ["Test 1"]);
  assert.deepEqual(section[1].items.map((item) => item.href), ["#seating/c1"]);
  assert.deepEqual(section[3].items.map((item) => item.label), ["Test 1 seat cards"]);
  assert.deepEqual(workRows({ termId: "T", exams: null, seating: [], grading: [], cards: [] }), []);
  // ASSIGNMENT-WORKSPACE: "N more" exams or grading open this course's Assignments list (every term from a past term's page).
  assert.deepEqual([term[0].more, term[2].more], ["#assignments", "#assignments"]);
  assert.equal(workRows({ termId: "T", ...sources, definitionId: "D 1" })[0].more, "#assignments?course=D%201");
  assert.equal(workRows({ termId: "T", ...sources, definitionId: "D", currentTerm: false })[2].more, "#assignments?course=D&status=archived");
});

test("certified work is archived out of the work list: counted, linked to its page, a row dropped only when empty", () => {
  const archived = { certification_id: "c1", certified_at: "2026-09-27T23:49:34+00:00" };
  const sources = {
    seating: [
      { id: "c1", title: "Exam 1", room_name: "HECC 207", term_id: "T", section_ids: ["S1"], sections: [507], confirmed_at: "x", archived },
      { id: "c2", title: "Quiz 4", room_name: "HECC 105", term_id: "T", section_ids: ["S1"], sections: [507], confirmed_at: "x", archived: null },
    ],
    cards: [
      { id: "i1", title: "{{exam.name}} · your seat assignment", assignment_title: "Exam 1", term_id: "T", section_ids: ["S1"], archived },
      { id: "i2", title: "{{exam.name}} · Disability Resource Center", assignment_title: "Exam 1", term_id: "T", section_ids: ["S1"], archived },
    ],
  };
  const term = workRows({ termId: "T", ...sources });
  assert.deepEqual(term.map((row) => [row.kind, row.items.length, row.archived || 0]), [["Seating charts", 1, 1], ["Response Cards", 0, 2]]);
  assert.deepEqual(term[0].items, [{ label: "Quiz 4 · HECC 105", href: "#seating/c2", note: "" }]);
  assert.equal(term[1].more, "#response-cards");  // the "2 archived" link opens the page whose Archived section holds them
  assert.equal(workCounts(term), "1 seating chart · 3 archived");  // one chart and two card sets
  // Nothing left and nothing archived: no row at all.
  assert.deepEqual(workRows({ termId: "T", seating: [], cards: sources.cards.map((issue) => ({ ...issue, term_id: "OTHER" })) }), []);
});

test("card sets read as on their page, and the course Home names the sections that tell alike ones apart", () => {
  const cards = [
    { id: "i1", title: "{{exam.name}} · your seat assignment", assignment_title: "Test 1", term_id: "T", section_ids: ["S1"] },
    { id: "i2", title: "{{exam.name}} · Disability Resource Center", assignment_title: "Test 1", term_id: "T", section_ids: ["S2", "S1"] },
    { id: "i3", title: "{{ exam.room }} notice", assignment_title: "Test 1", room_name: "HECC 105", term_id: "T", context: { term_id: "T", section_ids: ["S9"] } },
    { id: "i4", term_id: "T", section_ids: ["S1"] },
  ];
  const sectionNumbers = { S1: 507, S2: 508 };
  const home = workRows({ termId: "T", sectionNumbers, cards })[0].items.map((item) => item.label);
  assert.deepEqual(home, ["Test 1 · your seat assignment · Section 507", "Test 1 · Disability Resource Center · Sections 507, 508", "HECC 105 notice", "Response cards · Section 507"]);
  // A section tab lists only its own sets, without the suffix; without the term's sections there is none either.
  const tab = workRows({ termId: "T", sectionId: "S1", sectionNumber: 507, sectionNumbers, cards })[0].items.map((item) => item.label);
  assert.deepEqual(tab, ["Test 1 · your seat assignment", "Test 1 · Disability Resource Center", "Response cards"]);
  assert.deepEqual(workRows({ termId: "T", cards })[0].items.map((item) => item.label), ["Test 1 · your seat assignment", "Test 1 · Disability Resource Center", "HECC 105 notice", "Response cards"]);
  // A course of one section: nothing to tell apart.
  assert.equal(workRows({ termId: "T", sectionNumbers: { S1: 507 }, cards })[0].items[0].label, "Test 1 · your seat assignment");
});

test("a closed work list says what it holds in a line", () => {
  const rows = workRows({ termId: "T", exams: [{ id: "e1", title: "Test 1", term_id: "T", sections: [] }, { id: "e2", title: "Quiz", term_id: "T", sections: [] }],
    grading: [{ id: "g1", title: "Test 1", term_id: "T" }], cards: [{ id: "i1", title: "Cards", term_id: "T", section_ids: [] }] });
  assert.equal(workCounts(rows), "2 exams · 1 in grading · 1 card set");
  assert.equal(workCounts([]), "Nothing yet");
});

test("the course Home draws no empty Records row, and a section tab folds its exams, seating, grading and cards", () => {
  assert.match(source, /records \? `<li class="course-work-row" data-course-records>/);
  assert.match(source, /key: "course:section-work"/);
});

test("courses.js keeps markup free of inline styles", () => {
  assert.doesNotMatch(source, /style="/);
});

test("a roster file's name suggests its section only when it names exactly one of the course's sections (requirement, 2026-10-15)", () => {
  const sections = [507, 508, 514, 515];
  assert.equal(sectionFromFileName("MATH-140-507_202631-10-15-2026.xlsx", sections), "507");
  assert.equal(sectionFromFileName("roster 515.csv", sections), "515");
  assert.equal(sectionFromFileName("section-5080.csv", sections), "");
  assert.equal(sectionFromFileName("507 and 508.csv", sections), "");
  assert.equal(sectionFromFileName("other.csv", sections), "");
  assert.equal(sectionFromFileName("MATH-140-201_202631.xlsx", sections), "");
});

test("a TA email opens the instructor's mail program with the section in the subject", () => {
  const subject = taSubject({ code: "MATH 140", section: 507 });
  assert.equal(subject, "MATH 140 Section 507");
  assert.equal(taMailto(["jlee@tamu.edu"], subject), "mailto:jlee@tamu.edu?subject=MATH%20140%20Section%20507");
  assert.equal(taMailto(["jlee@tamu.edu", "o'neil+ta@tamu.edu"]), "mailto:jlee@tamu.edu,o'neil%2Bta@tamu.edu");
});

test("copying another section's TAs adds only the ones not already in the form", () => {
  const current = [{ name: "Jordan Lee", email: "JLee@tamu.edu", phone: "", note: "" }, { name: "", email: "", phone: "", note: "" }];
  const incoming = [{ name: "Jordan Lee", email: "jlee@tamu.edu" }, { name: "Sam Park", email: "spark@tamu.edu", note: "Grader" }, { name: "Sam Park", email: "spark@tamu.edu" }];
  const merged = mergeTAs(current, incoming);
  assert.equal(merged.added, 1);
  assert.deepEqual(merged.contacts.map((ta) => ta.name), ["Jordan Lee", "Sam Park"]);
  assert.deepEqual(merged.contacts[1], { name: "Sam Park", email: "spark@tamu.edu", phone: "", note: "Grader" });
});

test("the course Home's Canvas row says where the term's Canvas content stands", () => {
  const sections = [{ number: 507, course: { course_id: "1" } }, { number: 508, course: null }];
  assert.equal(courses.canvasStatusText({ sections: [{ number: 507, course: null }], courses: [], copy: null }), "Link each section’s Canvas course first");
  assert.equal(courses.canvasStatusText({ sections, courses: [{ course_id: "1", pushed: false }], copy: null }), "Not started · 1 section without a Canvas course");
  assert.equal(courses.canvasStatusText({ sections: sections.slice(0, 1), courses: [{ course_id: "1", pushed: false }], copy: { revision: 1 } }), "Not pushed yet");
  assert.equal(courses.canvasStatusText({ sections: sections.slice(0, 1), courses: [{ course_id: "1", pushed: true }, { course_id: "2", pushed: false }], copy: { revision: 2 } }), "Pushed to 1 of 2 Canvas courses");
});

test("roster cells drop the registrar's bold markup; the imported value is kept (usability audit, 2026-10-04)", () => {
  assert.equal(rosterValue("**Web Registered**"), "Web Registered");
  assert.equal(rosterValue("Dropped **after** census"), "Dropped after census");
  assert.equal(rosterValue("  Enrolled "), "Enrolled");
  assert.equal(rosterValue(undefined), "");
  assert.equal(rosterValue("2 * 3 = 6"), "2 * 3 = 6");
});

test("an import's result names who is leaving and why", () => {
  assert.equal(leavingText([{ name: "Lara Castillo, Greta", reason: "Q drop" }, { name: "Example, Bob", reason: "Withdrawn" }]),
    "Leaving: Lara Castillo, Greta (Q drop); Example, Bob (Withdrawn).");
});
