// READABLE REVIEW of a model's draft (static/review-notes.js; DESIGN-LANGUAGE principle 13): the makeup draft window and the quiz
// draft page sort a revision's notes and warnings under their problem, show first sentences beside it and fold the full wording.
import assert from "node:assert/strict";
import { answerMarkup, firstSentence, keyBasisMarkup, keyCounts, keyReadMarkup, paragraphs, problemCrop, proseMarkup, reviewNotes, saidMarkup, sentences, wholeMarkup } from "../static/review-notes.js";

const escape = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// READABLE REVIEW (requirement, 2026-10-05): the revision's notes and warnings sorted under their
// problem, first sentences beside it, the full wording folded; nothing of an unknown shape is dropped.
assert.deepEqual(sentences("The answer moved from (c) to (b). It leaves 2.9 cm. Use $x. Y$ here, e.g. this one. Done"),
  ["The answer moved from (c) to (b).", "It leaves 2.9 cm.", "Use $x. Y$ here, e.g. this one.", "Done"]);
assert.deepEqual(sentences("Now $$x+y\\le 60$$. Then (a) holds."), ["Now $$x+y\\le 60$$.", "Then (a) holds."]);
assert.deepEqual(paragraphs("I matched it.\n\nProblem 1: The trap moved. Problem 2: New numbers.\n\n"),
  ["I matched it.", "Problem 1: The trap moved.", "Problem 2: New numbers."]);
const revision = {
  quiz: { problems: [{ number: 1 }, { number: 2 }, { number: 3 }] },
  notes: "I matched the makeup to the original problem by problem.\n\nProblem 1: The trap is now in row 3. The answer moved.\n\nProblem 3: A candle maker.",
  rubrics: [{ problem: 1, rubric: { notes: "The correct choice is (b), not (c). The trap is choice (c).", warnings: [] } },
    { problem: 3, rubric: { notes: "", warnings: ["R6, R7 are word for word Version A's: check them."] } }],
  warnings: ["Version E: Problem 2 leaves 2.9 cm to write in.", "Version E: The title runs long.", "Check, problem 3: The grid is small.",
    "Check: The pages are crowded.", "Problem 1 rubric: The correct choice is (b), not (c). The trap is choice (c).",
    "Problem 3 rubric: R6, R7 are word for word Version A's: check them.", "Problem 2: its rubric could not be adapted (timeout). Ask for changes to adapt it again.",
    "Problem 9 rubric: a problem the quiz does not have."],
};
const sorted = reviewNotes(revision);
assert.deepEqual(sorted.problems["1"], { checks: [], writer: "The trap is now in row 3. The answer moved.", rubric: "The correct choice is (b), not (c). The trap is choice (c)." });
// The layout's room warning says what to check (2026-10-06).
assert.deepEqual(sorted.problems["2"].checks, ["Only 2.9 cm left to write in. Enough room?", "Its rubric could not be adapted (timeout). Ask for changes to adapt it again."]);
assert.deepEqual(sorted.problems["3"].checks, ["The grid is small.", "R6, R7 are word for word Version A's: check them."]);
assert.equal(sorted.problems["3"].writer, "A candle maker.");
assert.deepEqual(sorted.whole, { checks: ["The title runs long.", "The pages are crowded.", "Problem 9 rubric: a problem the quiz does not have."],
  notes: ["I matched the makeup to the original problem by problem."] });
// A problem as printed: from its top to the next problem on its page (less a heading's room), the last one to its last line.
const layout = { 1: { page: 1, end_page: 1, top: 0.351, body: 0.595, bottom: 0.595 }, 2: { page: 1, end_page: 1, top: 0.628, body: 0.787, bottom: 0.896 },
  3: { page: 2, end_page: 2, top: 0.129, body: 0.843, bottom: 0.843 }, 4: { page: 3, end_page: 4, top: 0.5, body: 0.2, bottom: 0.2 } };
const near = (crop, page, top, end) => { assert.equal(crop.page, page); assert.ok(Math.abs(crop.top - top) < 1e-9 && Math.abs(crop.end - end) < 1e-9, JSON.stringify(crop)); };
near(problemCrop(layout, 1), 1, 0.349, 0.606);
near(problemCrop(layout, 2), 1, 0.626, 0.904);
near(problemCrop(layout, 3), 2, 0.127, 0.851);
assert.equal(problemCrop(layout, 4), null);  // runs onto the next page: no crop
assert.equal(problemCrop({}, 1), null);
const prose = proseMarkup("Lead line\n- R4 applies when x is missing.\n- R5 <b>too</b>.\n\nSecond.", { e: escape });
assert.match(prose, /<p><span class="grading-math" data-math>Lead line<\/span><\/p><ul><li><span[^>]*>R4 applies[\s\S]*<\/ul><p><span[^>]*>Second\.<\/span><\/p>$/);
assert.match(prose, /&lt;b&gt;too/);
assert.equal((prose.match(/<li>/g) || []).length, 2);

// A quiz of four versions: a finding on every version is said once, on some names them; a paragraph that starts "Problem N," or
// "For Problem N," is that problem's; a check of one version names it; a warning about the lecture notes stays at the top.
const quiz = {
  quiz: { versions: ["A", "B", "C", "D"], problems: [{ number: 1 }, { number: 2 }, { number: 3 }] },
  notes: "I wrote the quiz in your order.\n\nProblem 1, the pivot MC, tests the step-5 rule. Its trap is a zero ratio.\n- the row with the larger ratio;\n\nFor Problem 2, I chose a final tableau. It has three variables.\n\nMC has four options here.",
  rubrics: [{ problem: 2, rubric: { notes: "**How the 3 points are split:**\n- Reason: 1 point.\n\nI checked **every** version.", warnings: [] } }],
  warnings: ["The notes for 3.9 could not be read.", ...["A", "B", "C", "D"].map((v) => `Version ${v}: Problem 2 leaves 2.9 cm to write in.`),
    "Version A: Problem 3 leaves 1.2 cm to write in.", "Version B: Problem 3 leaves 1.2 cm to write in.", "Version C: Problem 3 leaves 1.7 cm to write in.",
    "Check, Version C, problem 1: Choice (b) is cut off.", "Check, Version D: The header overlaps."],
};
const four = reviewNotes(quiz);
assert.deepEqual(four.problems["2"].checks, ["Every version: only 2.9 cm left to write in. Enough room?"]);
assert.deepEqual(four.problems["3"].checks, ["Versions A, B: only 1.2 cm left to write in. Enough room?", "Version C: only 1.7 cm left to write in. Enough room?"]);
assert.deepEqual(four.problems["1"].checks, ["Version C: choice (b) is cut off."]);
assert.deepEqual(four.whole.checks, ["The notes for 3.9 could not be read.", "Version D: the header overlaps."]);
assert.match(four.problems["1"].writer, /^Problem 1, the pivot MC, tests/);
assert.match(four.problems["2"].writer, /^For Problem 2, I chose a final tableau/);
assert.deepEqual(four.whole.notes, ["I wrote the quiz in your order.", "MC has four options here."]);
// First sentences skip a bold heading and bold marks; the folded prose keeps the heading as a label, the list as a list.
assert.equal(firstSentence(quiz.rubrics[0].rubric.notes), "Reason: 1 point.");
assert.equal(firstSentence("I checked **every** version. More."), "I checked every version.");
const folded = proseMarkup(quiz.rubrics[0].rubric.notes, { e: escape });
assert.match(folded, /^<h5>How the 3 points are split<\/h5><ul><li>[\s\S]*Reason: 1 point\.[\s\S]*<\/ul><p>[\s\S]*I checked every version\./);
assert.doesNotMatch(folded, /\*\*/);
// Beside a problem: one labelled sentence per note, and a fold only when there is more to read.
const said = saidMarkup([["Quiz", four.problems["1"].writer], ["Rubric", ""]], { e: escape });
assert.match(said, /<dt>Quiz<\/dt><dd>[\s\S]*Problem 1, the pivot MC, tests the step-5 rule\.<\/span><\/dd>/);
assert.doesNotMatch(said, /<dt>Rubric/);
assert.match(said, /All the model&#39;s notes|All the model's notes/);
assert.doesNotMatch(saidMarkup([["Quiz", "One sentence only."]], { e: escape }), /disclosure/);
assert.equal(saidMarkup([["Quiz", ""]], { e: escape }), "");
assert.match(wholeMarkup(four.whole.notes, { e: escape }), /<li>[\s\S]*I wrote the quiz in your order\.[\s\S]*<li>[\s\S]*MC has four options here\./);
assert.equal(wholeMarkup([], { e: escape }), "");
// A note that ends with a colon keeps the short list it introduces, beside the problem and about the whole quiz (2026-10-06).
const answers = "The answers on the four versions:\n- MC: (c), (a), (d), (b).\n- Problem 2 maximum: P = 20, 24, 32, 26.";
assert.match(wholeMarkup([answers], { e: escape }), /The answers on the four versions:<\/span><ul><li>[\s\S]*MC: \(c\)[\s\S]*<li>[\s\S]*P = 20, 24, 32, 26\.[\s\S]*<\/ul><\/li>/);
assert.match(saidMarkup([["Rubric writer", answers]], { e: escape }), /<dd>[\s\S]*four versions:<\/span><ul>/);
// A fact row (the quiz page's answer of the version shown) is drawn first and never folds.
const facts = saidMarkup([["Quiz writer", "One sentence only."]], { e: escape }, [["Answer", "(b) <span>x = 2</span>"], ["Empty", ""]]);
assert.match(facts, /^<dl class="review-said"><dt>Answer<\/dt><dd>\(b\) <span>x = 2<\/span><\/dd><dt>Quiz writer<\/dt>/);
assert.doesNotMatch(facts, /Empty|disclosure/);
// The answer of the version shown (quiz page and makeup window, 2026-10-06): the choice with its wording, or the final answers by part.
assert.equal(answerMarkup({ kind: "multiple_choice" }, { correct_choice: "c", choices: ["a1", "b1", "The <2>"] }, { e: escape }),
  '(c) <span class="grading-math" data-math>The &lt;2&gt;</span>');
assert.equal(answerMarkup({ kind: "multiple_choice" }, { correct_choice: "", choices: [] }, { e: escape }), "");
assert.equal(answerMarkup({ kind: "free_response" }, { final_answers: [{ part: "b", answer: "$y = 8.5$" }] }, { e: escape }),
  '(b) <span class="grading-math" data-math>$y = 8.5$</span>');
assert.match(answerMarkup({ kind: "free_response" }, { final_answers: [{ part: "a", answer: "x" }, { part: "b", answer: "y" }] }, { e: escape }),
  /^<ul class="review-answers"><li>\(a\) [\s\S]*<li>\(b\) /);
assert.equal(answerMarkup({ kind: "free_response" }, undefined, { e: escape }), "");

// The answer key read while the draft was made (requirement, 2026-10-11): the quiz page and the makeup window
// show it beside the problem as a saved key is typeset (bare LaTeX, "Part a:" for a lettered part, "or" between accepted spellings),
// say so when none could be read, and leave the row out for a revision that read no key (drafted before that day).
const h = { e: escape };
const tableau = { rule: "answer-key-read-v2", unreadable: "", basis: ["From the worked solution.", "", "The line states it; the solution says 12."],
  key: [{ label: "a", accept: ["\\begin{bmatrix} 1 & 2 \\end{bmatrix}"] }, { label: "x", accept: ["x=6", "6"] }, { label: "", accept: ["<b>"] }] };
assert.equal(keyReadMarkup(tableau, h), '<ul class="review-answers"><li><strong>Part a:</strong> <span class="grading-math" data-math data-math-bare="true">\\begin{bmatrix} 1 &amp; 2 \\end{bmatrix}</span></li>'
  + '<li><strong>x:</strong> <span class="grading-math" data-math data-math-bare="true">x=6</span> or <span class="grading-math" data-math data-math-bare="true">6</span></li>'
  + '<li><span class="grading-math" data-math data-math-bare="true">&lt;b&gt;</span></li></ul>');
assert.equal(keyReadMarkup({ key: [{ label: "", accept: ["(b)", "x = 2"] }], basis: [""], unreadable: "" }, h),
  '<span class="grading-math" data-math data-math-bare="true">(b)</span> or <span class="grading-math" data-math data-math-bare="true">x = 2</span>');
assert.equal(keyReadMarkup({ key: [], basis: [], unreadable: "The graph does not give the line." }, h), "None could be read. Approving saves no key for it.");
assert.equal(keyReadMarkup({ key: [], basis: [], unreadable: "", error: "The host refused." }, h), "None could be read. Approving saves no key for it.");
assert.equal(keyReadMarkup(undefined, h), "");
// How each part was found folds with the rubric and solution: only the parts that say, each under its label.
assert.equal(keyBasisMarkup(tableau, h), '<h5>How the answer key was read</h5><ul><li><strong>Part a:</strong> <span class="grading-math" data-math>From the worked solution.</span></li>'
  + '<li><span class="grading-math" data-math>The line states it; the solution says 12.</span></li></ul>');
assert.equal(keyBasisMarkup({ key: [], basis: [], unreadable: "No." }, h), "");
assert.equal(keyBasisMarkup(undefined, h), "");
// What an approval saves: the keys read, one per problem and version; null for a revision that read none at all.
assert.deepEqual(keyCounts({ rubrics: [{ problem: 1, keys: { A: tableau, B: tableau } }, { problem: 2, keys: { A: tableau, B: { key: [], basis: [], unreadable: "No." } } }, { problem: 3, error: "x" }] }),
  { read: 3, missing: 1 });
assert.equal(keyCounts({ rubrics: [{ problem: 1, rubric: {} }] }), null);
assert.equal(keyCounts(undefined), null);
// The reason a key is missing is the problem's marked check, in the layout notes' own wording: said once when it holds on every version.
const unread = reviewNotes({ quiz: { versions: ["A", "B"], problems: [{ number: 1 }, { number: 2 }] }, notes: "", rubrics: [], warnings: [
  "Version A: Problem 2 has no answer key: the model could not read one (The graph is cut off). Approving saves none for it.",
  "Version B: Problem 2 has no answer key: the model could not read one (The graph is cut off). Approving saves none for it.",
  "Version B: Problem 1 has no answer key: reading it failed (The host refused). Approving saves none for it."] });
assert.deepEqual(unread.problems["2"].checks, ["Every version: has no answer key: the model could not read one (The graph is cut off). Approving saves none for it."]);
assert.deepEqual(unread.problems["1"].checks, ["Version B: has no answer key: reading it failed (The host refused). Approving saves none for it."]);
console.log("review notes checks passed");
