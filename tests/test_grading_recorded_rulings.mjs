// What recorded meetings said of an assessment's grading, in the finalization window and where a rubric is imported
// (static/grading-recorded-rulings.js; GRADING "RULINGS SAID IN A RECORDING"). Invented people and words.
import assert from "node:assert/strict";
import test from "node:test";
import { lineChoices, notFinalMarkup, rulingMarkup, rulingsMarkup } from "../static/grading-recorded-rulings.js";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const SEP = "\u001f";
const questions = [
  { question_id: "q5a", printed_number: "5", exam_version: "A", title: "Problem 5", rubric: { items: [{ id: "i1", description: "Correct derivative" }, { id: "i2", description: "Correct <sign>" }] } },
  { question_id: "q5b", printed_number: "5", exam_version: "B", title: "Problem 5", rubric: { items: [{ id: "i1", description: "Correct derivative" }] } },
  { question_id: "q6a", printed_number: "6", exam_version: "A", title: "Problem 6", rubric: { items: [{ id: "i1", description: "Limits" }] } }];
const ruling = (more) => ({ id: "r1", recording_id: "rec1", sentence: "On question 5, the correct <derivative> earns two points.", quote: "give two points for the correct derivative",
  speaker: "Avery Quill", at: "0:24", day: "Tue, Nov 10", question: "5", href: "#recordings/rec1?t=24000", state: "waiting", ...more });

test("a ruling is offered on the lines of the question it named, else on every line", () => {
  assert.deepEqual(lineChoices(ruling(), questions, SEP).map((choice) => choice.value), [`q5a${SEP}i1`, `q5a${SEP}i2`, `q5b${SEP}i1`]);
  assert.equal(lineChoices(ruling(), questions, SEP)[1].label, "Version A · Problem 5 · Item 2: Correct <sign>");
  assert.equal(lineChoices(ruling({ question: "problem 6b" }), questions, SEP).length, 1);
  assert.equal(lineChoices(ruling({ question: "" }), questions, SEP).length, 4);
  assert.equal(lineChoices(ruling({ question: "9" }), questions, SEP).length, 4);   // no such question: the instructor chooses
});

test("each ruling shows its sentence, the exact words with where to hear them, and its two presses", () => {
  const markup = rulingMarkup(ruling(), questions, escape, SEP);
  assert.match(markup, /data-ruling="r1" data-ruling-recording="rec1" data-ruling-state="waiting"/);
  assert.match(markup, /<strong>Question 5\. <\/strong>On question 5, the correct &lt;derivative&gt; earns two points\./);
  assert.match(markup, /Avery Quill, Tue, Nov 10 at 0:24: “give two points for the correct derivative” <a class="link-arrow" href="#recordings\/rec1\?t=24000" target="_blank" rel="noopener noreferrer">Hear it<\/a>/);
  assert.match(markup, /data-ruling-use>Add to its clarification<\/button><button type="button" class="text-button" data-ruling-decline>Not for this rubric/);
  assert.match(markup, /Correct &lt;sign&gt;/);
  // Settled, it says so and offers nothing more.
  const accepted = rulingMarkup(ruling({ state: "accepted" }), questions, escape, SEP);
  assert.match(accepted, /data-ruling-settled>Added to a clarification\./);
  assert.ok(!accepted.includes("data-ruling-use") && !accepted.includes("<select"));
  assert.match(rulingMarkup(ruling({ state: "declined" }), questions, escape, SEP), /Declined for this rubric\./);
  // HELD BY NAME: one the reading had no listing for says the name it was called by, so the instructor sees why it is here.
  assert.ok(!markup.includes("data-ruling-named"));
  assert.match(rulingMarkup(ruling({ named: "the <final> exam" }), questions, escape, SEP), /Hear it<\/a> <span data-ruling-named>Said of “the &lt;final&gt; exam”\.<\/span>/);
});

test("the window's section is open while one waits, folded once all are settled, and absent when nothing was said", () => {
  const waiting = rulingsMarkup({ rulings: [ruling(), ruling({ id: "r2", state: "declined" })] }, questions, escape, SEP);
  assert.match(waiting, /<details class="compute-fold quality-rulings" data-recorded-rulings open>/);
  assert.match(waiting, /<span data-rulings-count>1 waiting<\/span>/);
  assert.match(waiting, /Nothing here is applied by itself/);
  const settled = rulingsMarkup({ rulings: [ruling({ state: "accepted" })] }, questions, escape, SEP);
  assert.match(settled, /data-recorded-rulings >/);
  assert.match(settled, /1 statement, all settled/);
  assert.equal(rulingsMarkup({ rulings: [] }, questions, escape, SEP), "");
  assert.equal(rulingsMarkup(null, questions, escape, SEP), "");
});

test("where a rubric is imported, words that it is not final are a warning with the quote", () => {
  const markup = notFinalMarkup({ not_final: ruling({ quote: "The Exam 3 rubric is not final yet" }) }, escape);
  assert.match(markup, /<p class="grading-warning" role="status" data-recorded-not-final><strong>A meeting said this rubric is not final\.<\/strong>/);
  assert.match(markup, /“The Exam 3 rubric is not final yet”/);
  assert.equal(notFinalMarkup({ not_final: null }, escape), "");
  assert.equal(notFinalMarkup(null, escape), "");
});
