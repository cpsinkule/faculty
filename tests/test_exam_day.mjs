// Run: node --test tests/test_exam_day.mjs
// The exam-day plan's pure pieces (static/exam-day.js, static/exam-day-packet.js): its route, the Email proctors mailto: link
// (proctors once, instructions dropped past the length limit, nothing sent) and the packet's escaped markup.
import assert from "node:assert/strict";
import test from "node:test";
import { examDayRoute, proctorMail, roomWhen } from "../static/exam-day.js";
import { packetMarkup } from "../static/exam-day-packet.js";
import { escape } from "../static/ui.js";

const view = {
  assignment: { title: "Exam 1", code: "MATH 140" }, term: { term_label: "Fall 2026" },
  rooms: [
    { chart_id: "c1", room_name: "HECC 105", exam_date: "2026-10-08", exam_time: "7:00 PM", sections: [507, 508] },
    { chart_id: "c2", room_name: "ILCB 111", exam_date: "", exam_time: "", sections: [509] },
  ],
};

test("the route names an assignment and a term", () => {
  assert.deepEqual(examDayRoute("#exam-day/a%201/t2"), { assignmentId: "a 1", termId: "t2" });
  assert.deepEqual(examDayRoute("#exam-day/a/t?x=1"), { assignmentId: "a", termId: "t" });
  assert.equal(examDayRoute("#exam-day/a"), null);
  assert.equal(examDayRoute("#exams/a/t"), null);
  assert.equal(roomWhen(view.rooms[0]), "Thu, Oct 8 · 7:00 PM");
  assert.equal(roomWhen(view.rooms[1]), "No exam date on the chart");
});

test("Email proctors writes to every proctor with an address once and says where each one is", () => {
  const proctors = { c1: [{ name: "Ada", email: "ada@tamu.edu" }, { name: "Bea", email: "" }], c2: [{ name: "Ada again", email: "ADA@tamu.edu" }, { name: "Cy", email: "cy+x@tamu.edu" }] };
  const mail = proctorMail(view, proctors, "Arrive early.\nCount the copies.");
  assert.equal(mail.recipients, 2);
  assert.ok(mail.href.startsWith("mailto:ada@tamu.edu,cy%2Bx@tamu.edu?subject="), mail.href);
  assert.equal(mail.instructions, true);
  const body = decodeURIComponent(mail.href.split("&body=")[1]);
  assert.match(body, /HECC 105 · Thu, Oct 8 · 7:00 PM · Sections 507, 508\r\nProctors: Ada, Bea/);
  assert.match(body, /ILCB 111 · No exam date on the chart · Section 509\r\nProctors: Ada again, Cy/);
  assert.match(body, /Proctor instructions\r\nArrive early\.\r\nCount the copies\./);
  assert.equal(proctorMail(view, { c1: [{ name: "Bea", email: "" }] }, "x"), null);
});

test("long instructions are left to the printed packet, and the link stays short", () => {
  const proctors = { c1: [{ name: "Ada", email: "ada@tamu.edu" }] };
  const mail = proctorMail(view, proctors, "Stay in the room. ".repeat(200));
  assert.equal(mail.instructions, false);
  assert.ok(mail.href.length <= 1800, mail.href.length);
  assert.match(decodeURIComponent(mail.href), /The proctor instructions are in each room's printed packet\./);
  assert.match(decodeURIComponent(mail.href), /Proctors: Ada/);
});

test("the packet's markup escapes every text and says when nothing was printed", () => {
  const packet = {
    title: "Exam <1>", code: "MATH 140", term_label: "Fall 2026", room_name: "HECC & 105", sections: [507], exam_date: "2026-10-08",
    exam_time: "7:00 PM", seated: 3, dr_count: 1, versions: { count: 2, counts: [{ version: "A", count: 2 }, { version: "B", count: 1 }], unassigned: 0, conflicts: 0 },
    printing: null, proctors: [{ name: "<b>Ada</b>", email: "ada@tamu.edu", phone: "" }], instructions: "Line one\n<script>x</script>",
    warnings: ["No print jobs from this seating chart yet, so the extras are not known."],
    roster: { title: "Exam <1>", code: "MATH 140", term_label: "Fall 2026", sections: [507], room_name: "HECC & 105", exam_date: "2026-10-08", exam_time: "7:00 PM",
      room_students: [{ name: "Example, Bob", uin: "123456789", section_number: 507, seat: "B1", response_card: { label: "No card issued", no_response: false } }],
      dr_students: [], warnings: [] },
  };
  const markup = packetMarkup(packet, escape);
  assert.ok(!markup.includes("<script>") && !markup.includes("<b>Ada"), markup);
  assert.ok(markup.includes("Exam &lt;1&gt;") && markup.includes("HECC &amp; 105") && markup.includes("&lt;script&gt;x&lt;/script&gt;"));
  assert.match(markup, /Version A<\/th><td>2/);
  assert.match(markup, /Not known: no print jobs yet/);
  assert.ok(!markup.includes("Total printed"));
  assert.match(markup, /Thu, Oct 8, 2026 · 7:00 PM/);
  assert.ok(!markup.includes("seating-room-roster"), "the preview's roster is not hidden like the print node's");
  assert.ok(packetMarkup(packet, escape, { print: true }).includes('class="packet-roster seating-room-roster"'));
  const printed = packetMarkup({ ...packet, versions: null, printing: { copies: 5, extras: 2, sections: [{ section_number: 507, copies: 5, extras: 2 }] } }, escape);
  assert.match(printed, /Exam copies for seated students<\/th><td>3/);
  assert.match(printed, /Total printed<\/th><td>5/);
});
