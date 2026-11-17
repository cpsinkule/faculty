import assert from "node:assert/strict";
import test from "node:test";

// The Accommodations page's "DR quizzes" tab (SCHEDULING "CLASS SCHEDULE: DR QUIZZES", 2026-10-15): a read-only view of
// /api/scheduling/terms/<term_id>/dr-quizzes, one term at a time.
const page = await import(new URL("../static/accommodations_page.js", import.meta.url).href);
const { readRoute, routeHash, aimStatus, drQuizzesMarkup } = page;
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const quiz = (changes = {}) => ({ key: "i1:s1", item: "i1", title: "Quiz 5", assignment_id: null, section_id: "s1", section: 507, date: "2026-10-23",
  time: "8:00 AM", start: "08:00", end: "", place: "HELD 107", time_source: "planned", minutes: 20, ready: true, reason: "",
  aim: { status: "not_in_aim", bookings: 0, dates: [] }, ...changes });
const view = (changes = {}) => ({ term_id: "t1", code: "MATH 140", term_label: "Fall 2026", today: "2026-10-12",
  sections: [{ section_id: "s1", section: 507, dr_students: 1 }], quizzes: [quiz()], fingerprint: "f".repeat(64), ...changes });

test("the DR quizzes tab has its own address and the other tabs keep theirs", () => {
  assert.deepEqual(readRoute("#accommodations?tab=quizzes"), { tab: "quizzes" });
  assert.equal(routeHash({ tab: "quizzes" }), "#accommodations?tab=quizzes");
  assert.deepEqual(readRoute("#accommodations"), { tab: "students" });
  assert.equal(readRoute("#accommodations?tab=exams").tab, "exams");
  assert.equal(routeHash({ tab: "exams" }), "#accommodations?tab=exams");
  assert.equal(routeHash({ tab: "students" }), "#accommodations");
});

test("the Booked in AIM cell says whether the imported DR bookings agree with the planned day", () => {
  assert.deepEqual(aimStatus({ status: "not_in_aim", bookings: 0, dates: [] }), { text: "None yet", tone: "muted" });
  assert.deepEqual(aimStatus({ status: "in_aim", bookings: 1, dates: ["2026-10-23"] }), { text: "1 booking", tone: "ok" });
  assert.deepEqual(aimStatus({ status: "in_aim", bookings: 2, dates: ["2026-10-23"] }), { text: "2 bookings", tone: "ok" });
  const differs = aimStatus({ status: "aim_differs", bookings: 1, dates: ["2026-10-21"] });
  assert.equal(differs.tone, "warn");
  assert.ok(differs.text.startsWith("Differs · booked "), differs.text);
  assert.ok(differs.text.length > "Differs · booked ".length, "the booked date is named");
});

test("one term's markup: heading, sections, the quiz table, escaped titles and not-ready reasons, nothing that acts", () => {
  const html = drQuizzesMarkup(view({ quizzes: [
    quiz({ title: "<b>Quiz & 5</b>", aim: { status: "in_aim", bookings: 1, dates: ["2026-10-23"] } }),
    quiz({ key: "i2:s1", item: "i2", title: "Quiz 9", date: "", time: "", start: "", place: "", time_source: "", ready: false, reason: "No date yet" }),
    quiz({ key: "i3:s1", item: "i3", title: "Quiz 8", date: "2026-10-31", time: "", start: "", time_source: "", ready: false, reason: "No time yet",
      aim: { status: "aim_differs", bookings: 1, dates: ["2026-10-30"] } })] }), escape);
  assert.ok(html.includes("MATH 140") && html.includes("Fall 2026"), "the term's heading");
  assert.ok(html.includes("507"), "the listed section");
  assert.ok(html.includes("&lt;b&gt;Quiz &amp; 5&lt;/b&gt;") && !html.includes("<b>Quiz"), "titles are escaped");
  for (const heading of ["Quiz", "Section", "Date", "Time", "Length", "Booked in AIM"]) assert.ok(html.includes(heading), heading);
  assert.ok(html.includes("20 min"));
  assert.ok(html.includes(">1 booking<") && html.includes("Differs · booked "));
  assert.ok(html.includes("No date yet") && html.includes("No time yet"));
  assert.ok(!/<button|<form|<input/i.test(html), "read-only");
});

test("a term with nothing to preschedule says so", () => {
  const html = drQuizzesMarkup(view({ sections: [], quizzes: [] }), escape);
  assert.ok(html.includes("No upcoming quizzes on the class schedule for sections with DR students."), html);
  assert.ok(html.includes("MATH 140"));
});

// QUIZZES "ACCESSIBLE COPY" (requirement, 2026-10-27): with the term's accessible copies an
// Accessible copy column carries each row's Word file, why its quiz has none, or that the quiz was not drafted in Faculty.
test("the Accessible copy column: the Word file, why there is none, or no drafted quiz", () => {
  const { accessibleCopyMarkup } = page;
  const copies = {
    a1: { draft_id: "d1", approval_id: "p1", title: "Quiz 5", version: "B", ready: true, reasons: [] },
    a2: { draft_id: "d2", approval_id: "p2", title: "Quiz 6", version: "A", ready: false, reasons: ["Problem 2 has a figure, & no alt text"] },
  };
  assert.equal(accessibleCopyMarkup(quiz({ assignment_id: "a1" }), copies, escape),
    '<a href="/api/quiz-drafts/d1/approvals/p1/accessible/B" download>Word file</a>');
  const refused = accessibleCopyMarkup(quiz({ assignment_id: "a2" }), copies, escape);
  assert.match(refused, /Not available/);
  assert.match(refused, /Problem 2 has a figure, &amp; no alt text/);
  assert.match(accessibleCopyMarkup(quiz({ assignment_id: "a3" }), copies, escape), /Not drafted in Faculty/);
  assert.match(accessibleCopyMarkup(quiz(), copies, escape), /No quiz yet/);
  // The column shows only with the copies read; without it the table is as before.
  const withCopies = drQuizzesMarkup(view({ quizzes: [quiz({ assignment_id: "a1" })] }), escape, null, copies);
  assert.match(withCopies, /<th scope="col">Accessible copy<\/th>/);
  assert.match(withCopies, /<td data-label="Accessible copy" class="dr-copy"><a href=/);
  assert.equal(drQuizzesMarkup(view(), escape), drQuizzesMarkup(view(), escape, null, null));
  assert.doesNotMatch(drQuizzesMarkup(view(), escape), /Accessible copy/);
});

// SEND TO AIM (SCHEDULING, 2026-10-27): the confirm says what Send does per quiz, the summary what it did, and each row's AIM cell
// its receipts with Retry for a write AIM did not confirm.
test("Send to AIM: the confirm, the summary and the receipts under the AIM chip", () => {
  const { aimSendPrompt, sentLines, aimSendMarkup } = page;
  assert.equal(aimSendPrompt({ quizzes: [] }), null);
  const plan = { quizzes: [
    { key: "k5", title: "Quiz 5", section: 507, date: "2026-10-23", copy: { version: "A" }, copy_reason: "", date_send: null, uploads: [] },
    { key: "k6", title: "Quiz 6", section: 507, date: "2026-10-26", copy: null, copy_reason: "Not drafted in Faculty",
      date_send: { id: "s6", status: "uncertain", detail: "check AIM", requests: 0 }, uploads: [] },
    { key: "k7", title: "Quiz 7", section: 507, date: "2026-10-28", copy: { version: "A" }, copy_reason: "",
      date_send: { id: "s7", status: "accepted", detail: "", requests: 0 }, uploads: [{ id: "u7", status: "accepted", detail: "", requests: 2 }] }] };
  const prompt = aimSendPrompt(plan);
  assert.equal(prompt.send, "Sign in and send");
  assert.match(prompt.lines[0], /^Quiz 5, section 507, .*: add the date, upload the accessible copy to registered students$/);
  assert.match(prompt.lines[1], /waits for Retry, no accessible copy \(Not drafted in Faculty\)$/);
  assert.match(prompt.lines[2], /already sent/);
  assert.match(prompt.note, /10 minutes after the section's class ends/);
  const lines = sentLines([{ title: "Quiz 5", section: 507, date: "2026-10-23", did: "date_added", detail: "" },
    { title: "", section: "", date: "", did: "stopped", detail: "AIM answered 500." }]);
  assert.deepEqual(lines.map((line) => [line.tone, line.detail]), [["success", ""], ["danger", "AIM answered 500."]]);
  assert.match(lines[0].text, /^Quiz 5, section 507, .*: Added to AIM$/);
  assert.equal(lines[1].text, "Stopped");
  assert.equal(aimSendMarkup(plan.quizzes[0], "t1", escape), "");
  assert.match(aimSendMarkup(plan.quizzes[1], "t1", escape), /Date not confirmed in AIM.*data-dr-aim-retry="s6" data-dr-term="t1">Retry/);
  assert.match(aimSendMarkup(plan.quizzes[2], "t1", escape), /Date sent to AIM.*Accessible copy uploaded to 2 requests/);
  // The button shows on a term with ready quizzes and the controls; the AIM cell carries the receipts.
  const html = drQuizzesMarkup(view({ quizzes: [quiz({ key: "k6" })], sends: plan }), escape, { quizzes: [] });
  assert.match(html, /data-dr-send="t1">Send to AIM<\/button>/);
  assert.match(html, /Date not confirmed in AIM/);
  assert.doesNotMatch(drQuizzesMarkup(view({ sends: plan }), escape), /data-dr-send/);  // read only without the notices read
});

test("a send that imported the DR bookings itself says so", () => {
  const { sentImport } = page;
  assert.equal(sentImport({ imported: true, counts: { added: 2, unchanged: 5 } }), "The DR bookings were imported (2 new, 5 unchanged).");
  assert.equal(sentImport({ imported: true, counts: {} }), "The DR bookings were imported.");
  assert.match(sentImport({ imported: false, counts: { unmatched: 1 } }), /matched no current student/);
});

test("DR DATE: a quiz given in class or one with a DR date shows the class day and, with controls, a date to set", () => {
  const { drDateMarkup } = page;
  assert.match(drDateMarkup(quiz(), "t1", escape, true), /^<time datetime="2026-10-23">[^<]+<\/time>$/);  // an ordinary row is unchanged
  const given = quiz({ date: "2026-10-09", class_date: "2026-10-09", dr_date: "", ready: false, reason: "Given in class" });
  assert.match(drDateMarkup(given, "t1", escape, false), /<small>Given in class<\/small>$/);  // read only: no control
  const controls = drDateMarkup(given, "t1", escape, true);
  assert.match(controls, /<input type="date"[^>]+value=""[^>]+data-dr-date-input="i1:s1"/);
  assert.match(controls, /data-dr-date="i1:s1" data-dr-term="t1">Set DR date</);
  assert.doesNotMatch(controls, /data-dr-date-clear/);
  const sitting = drDateMarkup(quiz({ date: "2026-10-14", class_date: "2026-10-09", dr_date: "2026-10-14" }), "t1", escape, true);
  assert.match(sitting, /^<time datetime="2026-10-14">/);
  assert.match(sitting, /<small>DR date · class [^<]+<\/small>/);
  assert.match(sitting, /value="2026-10-14"/);
  assert.match(sitting, />Change DR date</);
  assert.match(sitting, /data-dr-date-clear="i1:s1" data-dr-term="t1" aria-label="Clear the DR date: Quiz 5, section 507">Clear</);
  assert.match(drQuizzesMarkup(view({ quizzes: [given] }), escape), /Given in class/);  // the read-only table has no control
  assert.doesNotMatch(drQuizzesMarkup(view({ quizzes: [given] }), escape), /data-dr-date/);
});
