import assert from "node:assert/strict";
import test from "node:test";

import { addDays, calendarHref, campusToday, chipLabel, clockMinutes, displayClock, displaySpan, displayTime, eventType, groupEvents, groupFacts, nextEvents } from "../static/calendar-feed.js";

// The calendar feed as the Calendar and Home show it: one clock format, events that belong together as one group, and the
// next groups from campus today. Display only; the stored text is never changed.
const ev = (over) => ({ id: over.id || Math.random().toString(36).slice(2), source: "seating", title: "Test 1", date: "2026-09-17", end_date: "2026-09-17",
  time: "7:05PM", end_time: "", all_day: false, definition_id: "d1", code: "MATH 140", sections: [501], location: "HECC 105", href: "#seating/a", ...over, end_date: over.end_date || over.date || "2026-09-17" });

test("recognized clocks share one display format; other text is shown as written", () => {
  assert.equal(clockMinutes("7:05PM"), 19 * 60 + 5);
  assert.equal(clockMinutes("19:05"), 19 * 60 + 5);
  assert.equal(clockMinutes("2 p.m."), 14 * 60);
  assert.equal(clockMinutes("after class"), null);
  assert.equal(displayClock("19:05"), "7:05 PM");
  assert.equal(displayClock("7:05PM"), "7:05 PM");
  assert.equal(displayClock("after class"), "after class");
  assert.equal(displaySpan("19:05", "20:58"), "7:05–8:58 PM");
  assert.equal(displaySpan("11:30", "13:00"), "11:30 AM–1:00 PM");
  assert.equal(displayTime({ source: "custom", all_day: true }), "All day");
  assert.equal(displayTime({ source: "exam", time: "" }), "Time not set");
});

test("one exam's rooms and one exam's DR sections are one group each; singletons stay alone", () => {
  const events = [
    ev({ id: "s1", location: "HECC 105", sections: [501], href: "#seating/a" }), ev({ id: "s2", location: "HECC 209", sections: [502], href: "#seating/b" }), ev({ id: "s3", location: "ILCB 111", sections: [503], href: "#seating/c" }),
    ev({ id: "d1", source: "dr", time: "19:05", end_time: "20:58", location: "Center", sections: [501], booking_count: 3, href: "#accommodations?tab=exams", time_basis: "dr_center" }),
    ev({ id: "d2", source: "dr", time: "18:30", end_time: "20:00", location: "Center", sections: [502], booking_count: 7, href: "#accommodations?tab=exams", time_basis: "dr_center" }),
    ev({ id: "c1", source: "custom", category: "deadline", title: "Grades due", time: "", all_day: true, location: "", sections: [] }),
    ev({ id: "o1", definition_id: "d2", code: "MATH 151" }),
  ];
  const groups = groupEvents(events);
  assert.deepEqual(groups.map((group) => [group.type, group.count]), [["seating", 3], ["dr", 2], ["deadline", 1], ["seating", 1]]);
  const [rooms, dr, deadline] = groups;
  assert.equal(chipLabel(rooms), "Test 1 · 3 rooms · 7:05 PM");
  assert.deepEqual(groupFacts(rooms), ["3 rooms", "3 sections"]);
  assert.equal(rooms.href, "", "three charts have no single record");
  assert.equal(chipLabel(dr), "Test 1 · DR · 10");
  assert.deepEqual(groupFacts(dr), ["2 sections", "10 bookings"]);
  assert.equal(dr.time, "6:30–8:58 PM");
  assert.equal(dr.href, "#accommodations?tab=exams");
  assert.equal(dr.dr_time, true);
  assert.equal(deadline.time, "All day");
  assert.equal(eventType(deadline.events[0]), "deadline");
});

test("next events start at campus today, keep a multi-day event under way, and come in date and time order", () => {
  const events = [
    ev({ id: "past", date: "2026-09-20", end_date: "2026-09-20" }),
    ev({ id: "running", source: "custom", title: "Grading week", date: "2026-09-24", end_date: "2026-09-28", time: "", all_day: true, sections: [], location: "" }),
    ev({ id: "later", date: "2026-10-22", title: "Test 2" }),
    ev({ id: "soon", date: "2026-09-27", title: "Quiz", time: "9:00" }),
  ];
  const next = nextEvents(events, "2026-09-26", 5);
  assert.deepEqual(next.map((group) => [group.date, group.title]), [["2026-09-26", "Grading week"], ["2026-09-27", "Quiz"], ["2026-10-22", "Test 2"]]);
  assert.equal(nextEvents(events, "2026-09-26", 1).length, 1);
});

test("campus dates and calendar addresses", () => {
  assert.equal(campusToday(new Date("2026-09-27T03:30:00Z")), "2026-09-26", "Central time is still the day before");
  assert.equal(addDays("2026-09-26", 21), "2026-10-17");
  assert.equal(calendarHref("2026-10-22"), "#calendar?month=2026-10&view=month&date=2026-10-22");
});
