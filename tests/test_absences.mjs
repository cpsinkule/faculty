// Excused absences (static/absences.js) and regrade requests (static/grading-regrades.js): the pure helpers. The Absences address
// carries only filters; a list filters by decision, course and every word typed; a save's iClicker credit reads as words; a
// regrade request's title names its version;
// only Gradescope's own https grading pages are linked.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { readRoute, routeHash, filterAbsences, sizeText, creditText, splitText, aheadText } from "../static/absences.js";
import { requestTitle, gradescopeHref } from "../static/grading-regrades.js";

// The page opens on the absences Waiting for a decision; status=all shows every one (requirement, 2026-10-08).
assert.deepEqual(readRoute("#absences"), { status: "pending", course: "", scope: "current", view: "" });
assert.equal(readRoute("#absences?status=all").status, "all");
assert.equal(routeHash({ status: "all" }), "#absences?status=all");
assert.equal(routeHash({ status: "pending" }), "#absences");
assert.deepEqual(readRoute("#absences?status=pending&course=MATH%20140&scope=all"), { status: "pending", course: "MATH 140", scope: "all", view: "" });
// The Makeups view (ABSENCES "MAKEUPS", 2026-10-03) and Totals by student (TOTAL ABSENCES, 2026-10-31) are the other views;
// anything else reads as the list.
assert.equal(readRoute("#absences?view=totals").view, "totals");
assert.equal(routeHash({ view: "totals", scope: "all" }), "#absences?view=totals&scope=all");
assert.equal(splitText({ excused: 2, not_excused: 1, pending: 0 }), "2 excused · 1 not excused");
assert.equal(aheadText({ ahead: [{ day: "Mon, Nov 2" }] }), "1 more day recorded ahead, not counted yet: Mon, Nov 2.");
assert.equal(readRoute("#absences?view=makeups").view, "makeups");
assert.equal(readRoute("#absences?view=other").view, "");
assert.equal(routeHash({ view: "makeups" }), "#absences?view=makeups");
assert.equal(routeHash(readRoute("#absences?view=makeups&course=MATH%20140")), "#absences?view=makeups&course=MATH+140");
assert.equal(readRoute("#absences?status=nonsense").status, "pending");
assert.equal(routeHash({ status: "excused", course: "MATH 140", scope: "all" }), "#absences?status=excused&course=MATH+140&scope=all");
assert.equal(routeHash({}), "#absences");

const alice = { name: "Example, Alice", code: "MATH 140", section: 507, reason_label: "Illness", dates: "Thu, Sep 17", status: "excused" };
const bob = { name: "Example, Bob", code: "MATH 151", section: 508, reason_label: "Religious holy day", dates: "Mon, Sep 21", status: "pending" };
assert.deepEqual(filterAbsences([alice, bob], { status: "pending" }), [bob]);
assert.deepEqual(filterAbsences([alice, bob], { status: "all" }), [alice, bob]);
assert.deepEqual(filterAbsences([alice, bob], { course: "MATH 140" }), [alice]);
assert.deepEqual(filterAbsences([alice, bob], { search: "alice 507" }), [alice]);
assert.deepEqual(filterAbsences([alice, bob], { search: "holy bob" }), [bob]);
assert.deepEqual(filterAbsences([alice, bob], { search: "nobody" }), []);

assert.equal(sizeText(512), "512 B");
assert.equal(sizeText(2048), "2 KB");
assert.equal(sizeText(3 * 1048576), "3.0 MB");

// What a save changed in the gradebook's iClicker columns (ABSENCES ICLICKER-CREDIT), as a toast's tail.
assert.equal(creditText(undefined), "");
assert.equal(creditText({ filled: 0, put_back: 0 }), "");
assert.equal(creditText({ filled: 1, put_back: 0 }), "full credit for 1 iClicker session");
assert.equal(creditText({ filled: 2, put_back: 1 }), "full credit for 2 iClicker sessions, credit taken back from 1 iClicker session");

// A new absence defaults to the campus date (ui.js localToday): toISOString() is UTC, which is already tomorrow from 7 pm CDT.
const absencesSource = await readFile(new URL("../static/absences.js", import.meta.url), "utf8");
assert.doesNotMatch(absencesSource, /toISOString\(\)\.slice\(0, ?10\)/);
assert.match(absencesSource, /const today = localToday\(\)/);

assert.equal(requestTitle({ question: "Question 20", exam_version: "B" }), "Question 20 · Version B");
assert.equal(requestTitle({ question_title: "Question 7", exam_version: "" }), "Question 7");
assert.equal(gradescopeHref("https://www.gradescope.com/courses/1/questions/2/submissions/3/grade"), "https://www.gradescope.com/courses/1/questions/2/submissions/3/grade");
assert.equal(gradescopeHref("javascript:alert(1)"), "");
assert.equal(gradescopeHref("http://www.gradescope.com/courses/1/questions/2/submissions/3/grade"), "");
