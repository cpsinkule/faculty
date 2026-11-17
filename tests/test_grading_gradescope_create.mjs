// Creating the assignment on Gradescope (AGENTS.md "Sending grades to Gradescope"; requirement, 2026-09-27): what the instructor reads
// before Faculty makes anything, and while it does. The server decides; these check the page says it plainly and escapes it.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";

const source = readFileSync(new URL("../static/grading-gradescope-create.js", import.meta.url), "utf8")
  .replace(/^import \{runFormAction\} from "\.\/ui\.js";$/m, "const runFormAction = () => {};");
const {createPreviewMarkup, creationsMarkup, creationLive, creationUnitWords, notAddedMarkup} = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const e = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const h = {e, check: (label, name) => `<label class="grading-check"><input type="checkbox" name="${e(name)}">${e(label)}</label>`,
  button: (key, label) => `<button type="button" class="button" data-grading-action="${e(key)}">${e(label)}</button>`};

test("the preview says what is made per version, where, and asks for the tick only when it can be made", () => {
  const preview = {title: "Quiz <2>", ready: true, configured: true,
    versions: [{letter: "A", title: "Quiz <2> Version A", pages: 2, questions: 4, lines: 23}, {letter: "B", title: "Quiz <2> Version B", pages: 2, questions: 1, lines: 1}],
    courses: [{course_id: "1", course: "140-507 · Math", sections: [507], problem: ""}, {course_id: "2", course: "140-508 · Math", sections: [508, 509], problem: "140-508 · Math already has an assignment named “Quiz <2>”."}],
    sections: [{section: 510, reason: "Already goes to an assignment on Gradescope."}], problems: [], warnings: ["Question 3: its line groups become plain items"]};
  const html = createPreviewMarkup(preview, h);
  assert.match(html, /<strong>Quiz &lt;2&gt; Version A<\/strong> <span>2 pages \(its blank\) · 4 questions with their answer boxes · 23 rubric lines<\/span>/);
  assert.match(html, /1 question with their answer boxes · 1 rubric line</);
  assert.match(html, /grouped as one assignment named “Quiz &lt;2&gt;”/);
  assert.match(html, /<strong>140-507 · Math<\/strong> <span>Section 507<\/span>/);
  assert.match(html, /Sections 508, 509<\/span><p class="grading-warning" role="status">140-508 · Math already has an assignment named “Quiz &lt;2&gt;”\.<\/p>/);
  assert.match(html, /Sections not included · 1<\/summary>.*Section 510: Already goes to an assignment on Gradescope\./s);
  assert.match(html, /Warnings · 1/);
  assert.match(html, /name="understood">I checked this\. Faculty makes “Quiz &lt;2&gt;” in 1 Gradescope course; nothing is published and no student is emailed\./);
  const blocked = createPreviewMarkup({...preview, ready: false, problems: ["Question 4 (Version A): confirm its answer areas first (Answer areas step)."], versions: preview.versions.slice(0, 1)}, h);
  assert.match(blocked, /Before Faculty can make it:.*confirm its answer areas first/s);
  assert.doesNotMatch(blocked, /name="understood"/);
  assert.doesNotMatch(blocked, /grouped as one assignment/);
  assert.match(createPreviewMarkup({...preview, courses: [], ready: false}, h), /No section needs a new assignment/);
});

test("a version a section's papers need is added to the set it already goes to, and the tick says so", () => {
  // 2026-10-06: a makeup's Version E, sat in one section, was missing from the Quiz 4 set that section's papers went to.
  const preview = {title: "Quiz <4>", ready: true, configured: true,
    versions: [{letter: "E", title: "Quiz <4> Version E", pages: 2, questions: 3, lines: 14}],
    courses: [{course_id: "3", course: "140-514 · Math", sections: [514], problem: "", adds: {container: "Quiz <4>", has: ["A", "B", "C", "D"], letters: ["E"]}}],
    sections: [{section: 507, reason: "Already goes to an assignment on Gradescope."}], problems: [], warnings: []};
  const html = createPreviewMarkup(preview, h);
  assert.match(html, /<strong>Quiz &lt;4&gt; Version E<\/strong>/);
  assert.match(html, /<strong>140-514 · Math<\/strong> <span>Section 514 · adds Version E to “Quiz &lt;4&gt;” \(it has A, B, C, D\)<\/span>/);
  assert.doesNotMatch(html, /grouped as one assignment/);
  assert.match(html, /name="understood">I checked this\. Faculty adds Version E to “Quiz &lt;4&gt;” in 1 Gradescope course; nothing is published and no student is emailed\./);
  // Both at once: one course gets the whole assignment, another the missing version.
  const both = createPreviewMarkup({...preview, courses: [...preview.courses, {course_id: "4", course: "140-515 · Math", sections: [515], problem: ""}]}, h);
  assert.match(both, /Faculty makes “Quiz &lt;4&gt;” in 1 Gradescope course and adds Version E to “Quiz &lt;4&gt;” in 1 Gradescope course;/);
  assert.match(createPreviewMarkup({...preview, courses: [], ready: false}, h), /No section needs a new assignment or version/);
});

test("progress names each course's step, and a stopped course says why with a Retry", () => {
  assert.equal(creationsMarkup([], h), "");
  const running = {id: "c1", title: "Quiz 2", status: "running", created_at: "2026-09-27T01:00:00Z",
    units: [{course: "140-507", status: "sending", step: "Rubric of Question 3 (Quiz 2 Version A)", error: ""},
            {course: "140-508", status: "failed", step: "Made Quiz 2 Version A", error: "The outline of Quiz 2 Version A on Gradescope is not the one Faculty made (was it edited there?)."}]};
  assert.ok(creationLive(running));
  const html = creationsMarkup([running], h);
  assert.match(html, /Making “Quiz 2” on Gradescope/);
  assert.match(html, /<h4>Stopped in 1 course<\/h4>/);
  assert.match(html, /<strong>140-507<\/strong> <span data-gradescope-state="sending">working · Rubric of Question 3 \(Quiz 2 Version A\)<\/span>/);
  assert.match(html, /<span data-gradescope-state="failed">stopped<\/span><p class="grading-warning" role="status">The outline of Quiz 2 Version A/);
  assert.match(html, /data-grading-action="gradescope-create-cancel">Cancel</);
  assert.match(html, /data-grading-action="gradescope-create-retry">Retry \(1\)</);
  const made = creationsMarkup([{...running, status: "complete", units: [{course: "140-507", status: "complete", step: "Made Quiz 2", error: ""}]}], h);
  assert.match(made, /<h4>Made<\/h4>/);
  assert.match(made, /Send to Gradescope \(or Upload papers only, which sets no grade\) now proposes it/);
  assert.doesNotMatch(made, /gradescope-create-cancel|gradescope-create-retry/);
  assert.equal(creationUnitWords.uncertain, "checking Gradescope");
});

test("makeup versions Faculty could not add by itself are listed on the creation card, or alone, with Create as the way to add them", () => {
  // GRADESCOPE "A VERSION THE SET LACKS": one automatic attempt per version; 2026-10-09 review: its reason was never shown.
  const automatic = {versions_not_added: [{letter: "E", reasons: ["Section 507: “Quiz <4>” has Versions A, C: a set must hold every letter before E.", "Section 508: x"]}]};
  assert.equal(notAddedMarkup(null, h), "");
  assert.equal(creationsMarkup([], h, true, {}), "");
  const alone = creationsMarkup([], h, true, automatic);
  assert.match(alone, /data-gradescope-creation-notes/);
  assert.match(alone, /Faculty could not add makeup Version E by itself: Section 507: “Quiz &lt;4&gt;” has Versions A, C: a set must hold every letter before E\. \(and 1 more\)\. Create the assignment on Gradescope… adds it\./);
  const card = creationsMarkup([{id: "c1", title: "Quiz 4", status: "complete", units: [], automatic: {letter: "D", skipped: []}}], h, true, automatic);
  assert.match(card, /data-gradescope-creation="c1".*Faculty could not add makeup Version E by itself/s);
});

test("an assignment Faculty made after your own Certify says so, not that it added a makeup version", () => {
  // GRADESCOPE "AFTER CERTIFYING" (2026-10-12).
  const card = creationsMarkup([{id: "c1", title: "Quiz 5", status: "complete", created_at: "2026-10-12T22:00:00Z", automatic: {after: "certified", run: "r1"},
    units: [{course_id: "1", course: "140-507", status: "complete", step: "", error: ""}]}], h);
  assert.match(card, /data-gradescope-automatic>Made by Faculty by itself, after you certified\.<\/p>/);
  assert.doesNotMatch(card, /makeup Version/);
});

test("the bubble-sheet exam is its own part: its versions, where, its own problems, and the tick names it", () => {
  // GRADESCOPE "THE BUBBLE-SHEET EXAM TOO" (requirement, 2026-10-26).
  const sheets = {title: "Exam <2> - Multiple Choice", problems: [], linked: [508],
    versions: [{letter: "A", title: "Exam <2> - Multiple Choice Version A", questions: 17, points: "68"}, {letter: "B", title: "Exam <2> - Multiple Choice Version B", questions: 17, points: "68"}],
    courses: [{course_id: "1", course: "140-507 · Math", sections: [507], problem: ""}, {course_id: "3", course: "140-514 · Math", sections: [514], problem: "140-514 · Math already has an assignment named “Exam <2> - Multiple Choice”."}]};
  const preview = {title: "Exam <2>", ready: true, configured: true, versions: [], courses: [], sections: [], problems: [], warnings: [], sheets};
  const html = createPreviewMarkup(preview, h);
  assert.match(html, /<h4 class="grading-gradescope-heading">Bubble-sheet exam<\/h4>/);
  assert.match(html, /<strong>Exam &lt;2&gt; - Multiple Choice Version A<\/strong> <span>Gradescope's bubble sheet · 17 questions in the answer key · 68 points<\/span>/);
  assert.match(html, /data-gradescope-create-sheet-courses>.*140-507 · Math<\/strong> <span>Section 507<\/span>.*already has an assignment named “Exam &lt;2&gt; - Multiple Choice”/s);
  assert.match(html, /Already linked to a bubble-sheet exam: Section 508\./);
  assert.match(html, /No section needs a new written assignment or version/);
  assert.match(html, /name="understood">I checked this\. Faculty makes the bubble-sheet exam “Exam &lt;2&gt; - Multiple Choice” with its answer key in 1 Gradescope course; nothing is published/);
  const held = createPreviewMarkup({...preview, ready: false, sheets: {...sheets, problems: ["Faculty makes a bubble-sheet exam with versions only; this answer key has one."]}}, h);
  assert.match(held, /Before Faculty can make the bubble-sheet exam:.*with versions only/s);
  assert.doesNotMatch(held, /data-gradescope-create-sheet-versions/);
  // What the written one lacks holds back only it: the tick names the bubble sheets alone.
  const both = createPreviewMarkup({...preview, problems: ["Question 4 (Version A): confirm its answer areas first (Answer areas step)."],
    courses: [{course_id: "1", course: "140-507 · Math", sections: [507], problem: ""}]}, h);
  assert.match(both, /name="understood">I checked this\. Faculty makes the bubble-sheet exam “Exam &lt;2&gt; - Multiple Choice” with its answer key in 1 Gradescope course; nothing/);
  // A plan without an answer key shows no such part.
  assert.doesNotMatch(createPreviewMarkup({...preview, sheets: undefined}, h), /Bubble-sheet exam/);
  const card = creationsMarkup([{id: "c1", title: "Exam 2", status: "complete", units: [{course_id: "1", course: "140-507", status: "complete", step: "Made Exam 2 and Exam 2 - Multiple Choice", sheets: "Exam <2> - Multiple Choice"}]}], h);
  assert.match(card, /<strong>140-507<\/strong> <span class="supporting-copy" data-gradescope-creation-sheets>with “Exam &lt;2&gt; - Multiple Choice”<\/span>/);
  assert.match(card, /the Bubble sheets page sends the sheets to the bubble-sheet exam\./);
});
