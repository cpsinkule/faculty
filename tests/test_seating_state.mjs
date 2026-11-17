// Run: node --test tests/test_seating_state.mjs
// The chart page's read-only views of one chart payload (static/seating-state.js).
import assert from "node:assert/strict";
import test from "node:test";
import { buildingOf, chartGroups, chartState, drOthers, examHref, examRoute, examState, examTiming, hasRoomsPage, legendStates, listSummary, matchesFilter, needsAttention, nextAction, orderGroups, recordedTime, reseatImpact, roomOptions, roomStatus, seatsNamed, seatUpdatesTitle, studentFilterOptions } from "../static/seating-state.js";

function chart(extra = {}) {
  return { issues: [], room: { review_required: [] }, check: { ok: true }, confirmed_at: "", seat_updates: { pending: 0 },
    exam_date: "", exam_time: "", students: [], print_batches: [], dr: { others: [] }, spacing: { crowded: 0 }, ...extra };
}

test("the next step puts room review, roster issues and seat updates before everything else", () => {
  assert.equal(nextAction(chart({ room: { review_required: ["K253 twice"] }, issues: ["A has no seat."] })).key, "room");
  assert.equal(nextAction(chart({ issues: ["A has no seat.", "Room review: x"] })).key, "issues");
  assert.equal(nextAction(chart({ issues: ["Room review: x"] })).key, "confirm", "room review lines are not roster issues");
  assert.equal(nextAction(chart({ confirmed_at: "2026-09-15", seat_updates: { pending: 2 }, exam_date: "2026-09-17" }), { today: "2026-09-26" }).key, "seat-updates");
  assert.equal(nextAction(chart({ seat_updates: { pending: 2 } })).key, "confirm", "seat updates exist only after confirmation");
  assert.equal(nextAction(chart({ confirmed_at: "2026-09-15" }), { seatUpdateRequested: true }).action, "seat-updates");
});

test("before, on and after the exam date", () => {
  const confirmed = chart({ confirmed_at: "2026-09-15", exam_date: "2026-09-17", exam_time: "7:05 PM" });
  const before = nextAction(confirmed, { today: "2026-09-10" });
  assert.deepEqual([before.key, before.action, before.label], ["print", "print", "Print chart"]);
  // Confirming exported the papers: the step names them first, then the chart (the Print group leads with the room's files).
  assert.equal(before.detail, "Print the stamped papers and the room chart.");
  const day = nextAction(confirmed, { today: "2026-09-17" });
  assert.deepEqual([day.key, day.action], ["today", "print-roster"]);
  const held = nextAction(confirmed, { today: "2026-09-26" });
  assert.deepEqual([held.key, held.title, held.label], ["held", "Exam held Thu, Sep 17", "Print room roster"]);
  assert.equal(nextAction(chart(), { today: "2026-09-26" }).label, "Confirm for printing");
});

test("exam timing is a wall-clock date, never converted", () => {
  const exam = examTiming(chart({ exam_date: "2026-09-17", exam_time: "7:05 PM" }), "2026-09-26");
  assert.equal(exam.line, "Exam · Thu, Sep 17 · 7:05 PM · 9 days ago");
  assert.equal(exam.held, true);
  assert.equal(examTiming(chart({ exam_date: "2026-09-17" }), "2026-09-08").short, "Thu, Sep 17 · in 9 days");
  assert.equal(examTiming(chart()), null);
  assert.equal(examTiming(chart({ exam_date: "soon" })), null);
});

test("reseating a confirmed chart names the cards and marked print jobs it affects", () => {
  assert.equal(reseatImpact(chart()), null);
  const impact = reseatImpact(chart({ confirmed_at: "x", students: [
    { seat_id: "s1", status: "seated", card: { state: "pending" } },
    { seat_id: "s2", status: "seated", card: null },
    { seat_id: null, status: "dr_testing", card: { state: "pending" } },
  ], print_batches: [{ seat_marks: 1, status: "ready" }, { seat_marks: 1, status: "cancelled" }, { seat_marks: 0, status: "ready" }, { seat_marks: 1, superseded_at: "x" }] }));
  assert.deepEqual(impact, { seated: 2, cards: 1, markedJobs: 1 });
});

test("student filters count from the payload; card filters appear only with cards", () => {
  const students = [
    { status: "seated", card: { state: "issue" } }, { status: "seated", card: { state: "pending" } },
    { status: "dr_testing", card: { state: "pending" } }, { status: "unseated", card: null },
  ];
  const options = studentFilterOptions(chart({ students }));
  assert.deepEqual(options.map((item) => [item.value, item.count]), [["all", 4], ["issue", 1], ["pending", 2], ["dr", 1], ["unseated", 1]]);
  assert.deepEqual(studentFilterOptions(chart({ students: [{ status: "seated" }] })).map((item) => item.value), ["all", "dr", "unseated"]);
  assert.ok(matchesFilter(students[2], "dr") && matchesFilter(students[2], "pending") && !matchesFilter(students[0], "pending"));
  const updates = studentFilterOptions(chart({ students: [{ status: "seated", card: { state: "update_pending" } }] }));
  assert.ok(updates.some((item) => item.value === "update_pending" && item.count === 1));
});

test("DR bookings of other exams split into auto-created links and ordinary ones", () => {
  const split = drOthers(chart({ dr: { others: [{ title: "Final", students: 4, auto_created: false }, { title: "Test 1 makeup", students: 1, auto_created: true }] } }));
  assert.deepEqual(split.auto.map((item) => item.title), ["Test 1 makeup"]);
  assert.deepEqual(split.rest.map((item) => item.title), ["Final"]);
  assert.deepEqual(drOthers({}), { auto: [], rest: [] });
});

test("the legend lists only the states on the chart", () => {
  assert.deepEqual(legendStates(chart()).map(([, label]) => label), ["Seated", "Free", "Broken", "Blocked"]);
  const labels = legendStates(chart({ spacing: { crowded: 2 }, students: [{ card: { state: "update_pending" } }] })).map(([, label]) => label);
  assert.deepEqual(labels, ["Seated", "Beside another student", "Free", "Broken", "Blocked", "Seat update not sent"]);
});

function row(extra = {}) {
  return { assignment_id: "a1", term_id: "t1", title: "Test 1", code: "MATH 140", term_label: "Fall 2026", sections: [507], seated_count: 10,
    check: { ok: true, counts: {} }, confirmed_at: "", confirmed_mode: "", exam_date: "", exam_time: "", card_counts: { recipients: 0, responded: 0, flagged: 0 },
    version_totals: {}, ...extra };
}

test("list rows: one exam per assignment and term, dated upcoming first, past exams apart", () => {
  const rows = [
    row({ id: "1", assignment_id: "final", title: "Final", exam_date: "2026-12-10" }),
    row({ id: "2", exam_date: "2026-09-17", exam_time: "7:05 PM", confirmed_at: "2026-09-15T02:00:00Z", confirmed_mode: "stamps_only",
      card_counts: { recipients: 10, responded: 8, flagged: 1 }, version_totals: { A: 5, B: 5 } }),
    row({ id: "3", room_name: "HECC 105", confirmed_at: "2026-09-15T03:00:00Z", confirmed_mode: "stamps_only", card_counts: { recipients: 4, responded: 4, flagged: 0 }, version_totals: { A: 2, B: 1 } }),
    row({ id: "4", assignment_id: "quiz", title: "Quiz", check: { ok: false, counts: { missing_seat: 1 } } }),
    row({ id: "5", assignment_id: "test2", title: "Test 2", exam_date: "2026-10-15" }),
  ];
  const groups = chartGroups(rows, "2026-09-26");
  const test1 = groups.find((group) => group.title === "Test 1");
  assert.equal(test1.charts.length, 2);
  assert.equal(test1.exam.date, "Thu, Sep 17");
  assert.equal(test1.past, true);
  assert.equal(test1.confirmed, 2);
  assert.equal(test1.mode, "stamps_only");
  assert.deepEqual(test1.cards, { recipients: 14, responded: 12, flagged: 1 });
  assert.deepEqual(test1.versions, [["A", 7], ["B", 6]]);
  assert.equal(test1.seated, 20);
  const { upcoming, past } = orderGroups(groups);
  assert.deepEqual(upcoming.map((group) => group.title), ["Test 2", "Final", "Quiz"], "dated upcoming by date, undated after");
  assert.deepEqual(past.map((group) => group.title), ["Test 1"]);
  // A past exam that still needs the instructor stays in view, first; so does the latest past exam when nothing else is.
  const needy = chartGroups([row({ id: "9", exam_date: "2026-09-17", seat_updates_pending: 9 })], "2026-09-26");
  assert.deepEqual(orderGroups([...groups.filter((group) => group.title !== "Test 1"), ...needy]).upcoming.map((group) => group.title), ["Test 1", "Test 2", "Final", "Quiz"]);
  // Certified grades archive an exam (a view), unless a chart still needs the instructor.
  const certified = { certified_at: "2026-09-25T12:00:00Z", certification_id: "c1" };
  const shelf = orderGroups(groups.map((group) => group.title === "Test 1" ? { ...group, archived: certified } : group));
  assert.deepEqual([shelf.archived.map((group) => group.title), shelf.past.length], [["Test 1"], 0]);
  // Flags and unsent seat updates are resolved once grades are certified: the chart is archived and needs nothing.
  assert.deepEqual(orderGroups(needy.map((group) => ({ ...group, archived: certified }))).archived.map((group) => group.title), ["Test 1"]);
  assert.equal(needsAttention({ ...rows[0], seat_updates_pending: 3, archived: certified }), false);
  // A chart made after the certification (a makeup's room) keeps the exam out of Archived.
  const mixed = chartGroups([row({ id: "7", exam_date: "2026-09-17", archived: certified }), row({ id: "8", exam_date: "2026-09-17", room_name: "Makeup", archived: null })], "2026-09-26");
  assert.equal(mixed[0].archived, null);
  const onlyPast = orderGroups(groups.filter((group) => group.title === "Test 1"));
  assert.deepEqual([onlyPast.upcoming.length, onlyPast.past.length], [1, 0]);
  assert.equal(chartState(rows[1], "2026-09-26"), "Held");
  assert.equal(chartState(rows[2], "2026-09-26"), "Confirmed");
  assert.equal(chartState(rows[0], "2026-09-26"), "Seated");
  assert.equal(listSummary(rows), "5 room charts · 1 needs attention");
  assert.equal(listSummary([rows[0]]), "1 room chart");
});

test("one tile per exam: its rooms, sections and counts together, and where the tile opens", () => {
  const rooms = [
    row({ id: "1", room_name: "HECC 207", sections: [508, 507], term_sections: [507, 508, 514], counts: { excluded_dr: 3, free_seats: 12 } }),
    row({ id: "2", room_name: "HECC 209", sections: [507, 508], term_sections: [507, 508, 514], counts: { excluded_dr: 0, free_seats: 5 }, confirmed_at: "2026-09-15T02:00:00Z" }),
  ];
  const [exam] = chartGroups(rooms, "2026-09-10");
  assert.deepEqual([exam.rooms, exam.sections, exam.missing], [["HECC 207", "HECC 209"], [507, 508], [514]]);
  assert.deepEqual([exam.seated, exam.dr, exam.free], [20, 3, 17]);
  assert.equal(chartGroups([row()])[0].free, null, "no room says how many seats are free");
  // The tile's state: every room confirmed, some, none; Held after the exam.
  assert.equal(examState(exam), "1 of 2 confirmed");
  assert.equal(examState(chartGroups([rooms[1]], "2026-09-10")[0]), "Confirmed");
  assert.equal(examState(chartGroups([rooms[0]], "2026-09-10")[0]), "Not confirmed");
  assert.equal(examState(chartGroups([row({ exam_date: "2026-09-17", confirmed_at: "2026-09-15T02:00:00Z" })], "2026-09-26")[0]), "Held");
  // Several rooms, or a section still without one, open the exam's page; one room with everyone seated opens its chart.
  assert.equal(hasRoomsPage(exam), true);
  assert.equal(hasRoomsPage(chartGroups([rooms[0]])[0]), true, "Section 514 has no room yet");
  assert.equal(hasRoomsPage(chartGroups([row({ term_sections: [507] })])[0]), false);
  assert.equal(hasRoomsPage(chartGroups([row()])[0]), false);
  // The exam's address, and the addresses that are not one.
  assert.equal(examHref(exam), "#seating/exam/a1/t1");
  assert.deepEqual(examRoute(examHref({ assignment_id: "a/1", term_id: "t 1" })), { assignment_id: "a/1", term_id: "t 1" });
  assert.deepEqual(examRoute("seating/exam/a1/t1?x=1"), { assignment_id: "a1", term_id: "t1" });
  assert.deepEqual([examRoute("#seating"), examRoute("#seating/chart-1"), examRoute("#seating/exam/a1"), examRoute("#exams/exam/a1/t1")], [null, null, null, null]);
});

test("create dialog rooms: ready rooms by usable seats, review and empty rooms apart", () => {
  const rooms = [
    { id: "a", name: "A 1", usable_count: 40, review_required: [] },
    { id: "b", name: "B 1", usable_count: 244, review_required: [] },
    { id: "c", name: "C 1", usable_count: 90, review_required: ["x"] },
    { id: "d", name: "D 1", usable_count: 0, review_required: [] },
  ];
  const { ready, review, empty } = roomOptions(rooms);
  assert.deepEqual(ready.map((room) => room.id), ["b", "a"]);
  assert.deepEqual(review.map((room) => room.id), ["c"]);
  assert.deepEqual(empty.map((room) => room.id), ["d"]);
  assert.deepEqual(rooms.map(roomStatus), ["ready", "ready", "review", "empty"]);
  // Rooms an earlier chart used lead, the latest chart first, with its exam; a room that cannot be chosen never does.
  const more = [...rooms, { id: "e", name: "E 1", usable_count: 300, review_required: [] }];
  const charts = [{ room_id: "a", title: "Exam 1", created_at: "2026-09-01" }, { room_id: "a", title: "Exam 2", created_at: "2026-10-01" },
    { room_id: "b", title: "Exam 1", created_at: "2026-09-01" }, { room_id: "c", title: "Exam 1", created_at: "2026-09-01" }];
  const seen = roomOptions(more, charts);
  assert.deepEqual(seen.used.map((room) => [room.id, room.used_for]), [["a", "Exam 2"], ["b", "Exam 1"]]);
  assert.deepEqual(seen.others.map((room) => room.id), ["e"]);
  assert.deepEqual(seen.ready.map((room) => room.id), ["e", "b", "a"]);
  assert.deepEqual([ready, roomOptions(rooms).used, roomOptions(rooms).others.map((room) => room.id)], [ready, [], ["b", "a"]]);
  assert.equal(buildingOf("HECC 207"), "HECC");
  assert.equal(buildingOf("ilsb 1105"), "ILSB");
  assert.equal(buildingOf("Studio"), "STUDIO");
});

test("review reasons name seats only by the importer's wordings or 'seat X', never by spreadsheet cell references", () => {
  const labels = ["H12", "K5", "D4", "C3", "K5 · D4", "K5 · C3", "G1 · B1", "G1 · C1", "G2", "F106", "404", "405", "A2", "B1", "K Table 1 · 2"];
  const seats = labels.map((label, index) => ({ id: String(index), label }));
  const named = (/** @type {string} */ reason) => seatsNamed(reason, seats).map((seat) => seat.label);
  // room_import.py's two wordings, exactly as it writes them.
  assert.deepEqual(named("Cell H12 note “H” was not understood; seat K5 was imported without it."), ["K5"], "H12 is the source cell, not a seat");
  assert.deepEqual(named("Source cells D4, C3 repeat seat “K5”. Their labels include source cell positions pending review; no corrected seat numbers were inferred."),
    ["K5 · D4", "K5 · C3"], "the relabelled duplicates, not the seats that share the cells' letters and digits");
  assert.deepEqual(named("Source cells B1, C1 repeat seat “G1”. Their labels include source cell positions pending review; no corrected seat numbers were inferred."),
    ["G1 · B1", "G1 · C1"]);
  assert.deepEqual(named("Cell B4 note “x” was not understood; seat K Table 1 · 2 was imported without it."), ["K Table 1 · 2"]);
  assert.deepEqual(named("Cell K5 says “broken” but no seat is in that column of its row."), []);
  // Other text names a seat only after the word seat(s); a number is a seat only there.
  assert.deepEqual(named("Seat 404 on map is actually 405"), ["404"]);
  assert.deepEqual(named("Seats A2 and B1 share one position in the source."), ["A2", "B1"]);
  assert.deepEqual(named("Check H12; 8 chairs short in the room"), []);
  assert.deepEqual(named("seat k5 is broken"), ["K5"], "labels match case-insensitively");
  assert.deepEqual(named("Seat Z9 is missing"), [], "a label the room does not have is not a seat");
});

test("seat updates waiting to be sent count as attention on the list, and the chart says how many", () => {
  assert.equal(needsAttention({ check: { ok: true }, seat_updates_pending: 1 }), true);
  assert.equal(needsAttention({ check: { ok: true }, seat_updates_pending: 0 }), false);
  assert.equal(listSummary([{ check: { ok: true }, seat_updates_pending: 1 }, { check: { ok: true } }]), "2 room charts · 1 needs attention");
  assert.equal(chartGroups([{ assignment_id: "a", term_id: "t", check: { ok: true }, seat_updates_pending: 2 }, { assignment_id: "a", term_id: "t", check: { ok: true }, seat_updates_pending: 1 }], "2026-09-26")[0].seatUpdates, 3);
  assert.equal(seatUpdatesTitle(1), "1 student needs an updated seat card");
  assert.equal(seatUpdatesTitle(3), "3 students need updated seat cards");
  assert.equal(nextAction(chart({ confirmed_at: "2026-09-15", seat_updates: { pending: 2 } }), { today: "2026-09-10" }).title, "2 students need updated seat cards");
});

test("recorded moments use one campus-clock format", () => {
  assert.equal(recordedTime("2026-09-26T13:43:08Z"), "Sat, Sep 26 · 8:43 AM");
  assert.equal(recordedTime(""), "");
  assert.equal(recordedTime("not a time"), "not a time");
});
