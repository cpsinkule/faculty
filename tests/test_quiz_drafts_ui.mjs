import assert from "node:assert/strict";
import test from "node:test";

// The Quiz drafts strip above the assignment cards (static/quiz-drafts.js `cards`): every state a listed draft can be in has a name
// and a tone. An approved quiz being rebuilt in the current page layout is listed while it rebuilds (QUIZZES).
const { createQuizDrafts } = await import(new URL("../static/quiz-drafts.js", import.meta.url).href);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const drafts = createQuizDrafts({ api: async () => ({}), escape, toast() {}, showDialog() {}, dialogHeader: () => "", header: null });
const draft = (state) => ({ id: `d-${state}`, title: "Chapter 4 Quiz", state, revisions: [{ number: 2 }], course: "MATH 140", term: "Fall 2026", versions: ["A", "B"], total: 10 });

test("every listed state is named, never 'undefined'", () => {
  const strip = drafts.cards(["drafting", "ready", "failed", "approving", "rebuilding"].map(draft));
  assert.doesNotMatch(strip, /undefined/);
  assert.match(strip, /<span class="chip" data-tone="info">Rebuilding<\/span>/);
  assert.match(strip, /<span class="chip" data-tone="success">Ready for you<\/span>/);
  assert.match(strip, /href="#documents\/quiz\/d-rebuilding"/);
});

test("approved drafts leave the strip; an empty strip is nothing", () => {
  assert.equal(drafts.cards([draft("approved")]), "");
  assert.equal(drafts.cards([]), "");
});

// 'Given on' (requirement, 2026-10-02): the approved page says what the approval did, never the typed date alone. An exam record that
// already had a main date kept it (grading/quiz_drafts.py `_quiz_given_on`), and the page says so and where to change it.
test("the approved page's Given on line says the date the record holds", async () => {
  const { givenOnMarkup } = await import(new URL("../static/quiz-drafts.js", import.meta.url).href);
  assert.equal(givenOnMarkup({ input: {} }, escape), "");
  const saved = givenOnMarkup({ input: { given_on: "2026-10-08" }, progress: { plan: { event_id: "e1", given_on: "2026-10-08" } } }, escape);
  assert.match(saved, /Given on Thu, Oct 8: it is on Home’s Coming up and the Calendar\./);
  const kept = givenOnMarkup({ input: { given_on: "2026-10-09" }, progress: { plan: { event_id: "e1", given_on: "2026-10-08", given_on_kept: true } } }, escape);
  assert.match(kept, /Its exam record already had Thu, Oct 8, so that date was kept instead of Fri, Oct 9\./);
  assert.match(kept, /<a href="#exams\/e1">Change it on its Exams page<\/a>\./);
  assert.doesNotMatch(kept, /Given on Fri/);
});

// The approval window's first line says what approving does as the window stands (2026-10-06) and, since 2026-10-11 ("Carry the key
// through"), how many answer keys it saves: the keys the page shows beside its problems (review-notes.js `keyCounts`). A revision
// that read no key (drafted before that day) reads exactly as before.
test("the approval lead says how many answer keys approving saves", async () => {
  const { approvalLead } = await import(new URL("../static/quiz-drafts.js", import.meta.url).href);
  const window_ = { copies: 3, versions: ["A", "B"], sections: ["501"] };
  assert.equal(approvalLead(window_),
    "Approving prints 3 copies of Versions A and B exactly as shown for Section 501, as one stack, and sets up its grading. Key & Rubric Finalization still waits for you.");
  assert.equal(approvalLead({ ...window_, keys: { read: 8, missing: 0 } }),
    "Approving prints 3 copies of Versions A and B exactly as shown for Section 501, as one stack, sets up its grading and saves the 8 answer keys shown. Key & Rubric Finalization still waits for you.");
  assert.equal(approvalLead({ copies: 1, versions: ["A"], sections: ["501", "502", "503"], keys: { read: 1, missing: 3 } }),
    "Approving prints 1 copy of the quiz exactly as shown for Sections 501, 502 and 503, as one stack, sets up its grading and saves the answer key shown (3 could not be read). Key & Rubric Finalization still waits for you.");
  assert.equal(approvalLead({ copies: 0, versions: ["A", "B", "C"], sections: [], keys: { read: 0, missing: 6 } }),
    "Approving prints 0 copies of Versions A, B and C exactly as shown, as one stack, and sets up its grading. No answer key could be read, so none is saved. Key & Rubric Finalization still waits for you.");
});

// The approval request this page sends says `save_keys`: only an approval from the page that showed the keys saves them (a tab opened
// before sends nothing and gets what it always did), and the page shows each problem's key through review-notes.js.
test("the page shows the key read for each problem and its approval asks for the keys to be saved", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../static/quiz-drafts.js", import.meta.url), "utf8");
  assert.match(source, /save_keys: true \}\);/);
  assert.match(source, /\["Answer", key\], \["Answer key", keyReadMarkup\(reading, h\)\]/);
  assert.match(source, /const reading = drafted\.keys\?\.\[letter\];/);
  assert.match(source, /approvalLead\(\{ copies: n, versions: options\.versions, sections: ticked\(\)\.map\(\(input\) => input\.dataset\.number\), keys,/);
  const makeup = await readFile(new URL("../static/makeup-drafts.js", import.meta.url), "utf8");
  assert.match(makeup, /save_keys: true \}\);/);
  assert.match(makeup, /\["Answer", key\], \["Answer key", keyReadMarkup\(reading, h\)\]/);
});

// RUBRIC QUESTIONS AT DRAFT REVIEW (requirement, 2026-10-11): a revision ends with the rubric check Key & Rubric Finalization would
// run, so the approval window's first line says which of three things approving leaves there. A revision with no finished check
// (drafted before, or its check failed) reads exactly as before.
test("the approval lead says what is left in Key & Rubric Finalization, in its three forms", async () => {
  const { approvalLead } = await import(new URL("../static/quiz-drafts.js", import.meta.url).href);
  const window_ = { copies: 3, versions: ["A", "B"], sections: ["501"], keys: { read: 8, missing: 0 } };
  const prints = "Approving prints 3 copies of Versions A and B exactly as shown for Section 501, as one stack, sets up its grading and saves the 8 answer keys shown. ";
  // 1. No finished check: everything is still to do there, as it always was.
  assert.equal(approvalLead({ ...window_, rubric: null }), `${prints}Key & Rubric Finalization still waits for you.`);
  assert.equal(approvalLead(window_), approvalLead({ ...window_, rubric: null }));
  // 2. The check goes with the approval: only the confirmation is left, and it says what is still open.
  assert.equal(approvalLead({ ...window_, rubric: { open: 0, finalize: false } }),
    `${prints}The rubric check made here goes with it: Key & Rubric Finalization waits only for your confirmation.`);
  assert.equal(approvalLead({ ...window_, rubric: { open: 2, finalize: false } }),
    `${prints}The rubric check made here goes with it, with 2 decisions still open: Key & Rubric Finalization waits only for your confirmation.`);
  // 3. The box ticked: the approval records the finalization too.
  assert.equal(approvalLead({ ...window_, rubric: { open: 0, finalize: true } }),
    `${prints}It also records the rubric finalization: nothing is left to do in Key & Rubric Finalization.`);
  assert.equal(approvalLead({ copies: 1, versions: ["A"], sections: [], rubric: { open: 0, finalize: true } }),
    "Approving prints 1 copy of the quiz exactly as shown, as one stack, and sets up its grading. It also records the rubric finalization: nothing is left to do in Key & Rubric Finalization.");
});

test("the one unticked box says what it confirms, and the page says how many decisions are open", async () => {
  const { finalizeLabel, rubricCheckLead } = await import(new URL("../static/quiz-drafts.js", import.meta.url).href);
  const counts = { status: "complete", decisions: 0, overflow: 0, clarifications: 12, exclusions: 4, answered: 3 };
  assert.equal(finalizeLabel(counts), "Also confirm the rubric guidance as shown (12 clarifications, 4 exclusions, your answers to 3 decisions). This is the rubric finalization.");
  assert.equal(finalizeLabel({ ...counts, clarifications: 1, exclusions: 1, answered: 1 }),
    "Also confirm the rubric guidance as shown (1 clarification, 1 exclusion, your answer to 1 decision). This is the rubric finalization.");
  assert.equal(finalizeLabel({ ...counts, answered: 0 }), "Also confirm the rubric guidance as shown (12 clarifications, 4 exclusions). This is the rubric finalization.");
  assert.equal(rubricCheckLead(counts), "No rubric decision is open. Proposed for the graders: 12 clarifications and 4 exclusions, with your 3 answers applied.");
  assert.equal(rubricCheckLead({ ...counts, answered: 1, clarifications: 1, exclusions: 0 }), "No rubric decision is open. Proposed for the graders: 1 clarification and 0 exclusions, with your answer applied.");
  assert.equal(rubricCheckLead({ ...counts, answered: 0 }), "No rubric decision is open. Proposed for the graders: 12 clarifications and 4 exclusions.");
  assert.equal(rubricCheckLead({ ...counts, decisions: 2, overflow: 1 }), "3 rubric decisions open. Answer them below, or later in Key & Rubric Finalization.");
  assert.equal(rubricCheckLead({ ...counts, decisions: 1 }), "1 rubric decision open. Answer it below, or later in Key & Rubric Finalization.");
  // A revision that ran no check, or whose check failed (one of its marked warnings), has no line of its own.
  assert.equal(rubricCheckLead(undefined), "");
  assert.equal(rubricCheckLead({ status: "failed" }), "");
});

// The decisions are drawn by the finalization's own component, read only, inside the form whose one button sends every answer; the
// box is offered only for a finished check with nothing open, starts unticked, and only a ticked box sends `finalization`.
test("the draft's page mounts the finalization's decisions view read only and offers the box only with nothing open", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../static/quiz-drafts.js", import.meta.url), "utf8");
  assert.match(source, /import \{ mountRubricConcerns \} from "\.\/grading-rubric-concerns\.js";/);
  assert.match(source, /mountRubricConcerns\(form\.querySelector\("\[data-quality-concerns\]"\), check\.result, check\.view, h, \{\n\s+retry: page\.answers, split: page\.split, readOnly: true,/);
  assert.match(source, /<form data-rubric-answers novalidate><div data-quality-concerns><\/div>\n\s+<section class="quality-respond" data-quality-respond>/);
  assert.match(source, /api\(`\$\{base\(draft\.id\)\}\/rubric-answers`, "POST", \{ request_id: requestId, based_on: revision\.id, check_sha256: check\.sha256, responses, \.\.\.\(unfixed\.length \? \{ unfixed \} : \{\}\) \}\)/);
  assert.match(source, /\$\{carried && !open \? `<label class="checkbox-field" data-finalize><input type="checkbox" name="finalize"> \$\{escape\(finalizeLabel\(checked\)\)\}<\/label>` : ""\}/);
  assert.match(source, /\.\.\.\(form\.elements\.finalize\?\.checked \? \{ finalization: \{ confirm: true, check_sha256: check\.sha256 \} \} : \{\}\),/);
  assert.doesNotMatch(source, /name="finalize" checked/);
  const concerns = await readFile(new URL("../static/grading-rubric-concerns.js", import.meta.url), "utf8");
  // Read only: no field to edit a clarification, no rubric pop-up; the default (the finalization window) is as it was.
  assert.match(concerns, /readOnly = false, editLabel = 'Open in Rubric items',\n\s+handled = new Set\(\), fixes = \{\}, undoFix = null, pendingKeys = false, applyEdit = null\} = options;/);
  // BOOKKEEPING IS FIXED, NOT ASKED (2026-10-24): the draft's page lists a guidance fix apart; "Ask me instead" makes it a decision.
  // Since 2026-10-29 a key fix too, said to be made once the quiz is approved, and a points change it approves asks for a revision.
  assert.match(source, /\["guidance", "key"\]\.includes\(issue\.fix\?\.kind\) && !page\.unfixed\.has\(n\)/);
  assert.match(source, /onRedraw: oneList, handled, pendingKeys: true,\n/);
  assert.match(source, /onFixUndone: \(_, rest\) => \{ for \(const n of handled\) if \(!rest\.has\(n\)\) page\.unfixed\.add\(n\); \},/);
  assert.match(concerns, /if \(readOnly\) return `<div class="quality-reference-guidance" data-reference-row=/);
  assert.match(concerns, /\$\{readOnly \? '' : `<button type="button" class="text-button quality-rubric-link" data-show-rubric=/);
});

// SEND TO AIM AFTER APPROVAL (SCHEDULING "SEND TO AIM", requirement, 2026-10-27): an approved quiz sends to AIM when the term's DR quizzes have it, by its assignment or by its title.
test("an approved quiz goes to AIM when the term's DR quizzes have it", async () => {
  const { aimMatches } = await import(new URL("../static/quiz-drafts.js", import.meta.url).href);
  const plan = { quizzes: [{ key: "a", title: "Quiz 6", assignment_id: "as1" }, { key: "b", title: "Quiz 7", assignment_id: null }] };
  assert.deepEqual(aimMatches(plan, "as1", "Chapter 4 Quiz").map((quiz) => quiz.key), ["a"]);
  assert.deepEqual(aimMatches(plan, "other", " quiz 7 ").map((quiz) => quiz.key), ["b"]);
  assert.deepEqual(aimMatches(plan, "other", "Quiz 8"), []);
  assert.deepEqual(aimMatches(null, "as1", "Quiz 6"), []);
});

// NOTES SECTIONS FOR THE WRITER (2026-10-28): the window ticks the sections a title or instructions name, as the server reads them.
test("the sections a title or instructions name are found as the server finds them", async () => {
  const { namedSections } = await import(new URL("../static/quiz-drafts.js", import.meta.url).href);
  const sections = ["3.4", "4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.10"].map((number) => ({ number }));
  assert.deepEqual(namedSections(sections, ["Quiz 5", "Sections 4.1–4.3: two free response."]), ["4.1", "4.2", "4.3"]);
  assert.deepEqual(namedSections(sections, ["Chapter 5 Quiz", ""]), ["5.1", "5.2", "5.10"]);
  assert.deepEqual(namedSections(sections, ["Quiz 4", "Coverage is 3.4 (but also 4.2 for the free response)"]), ["3.4", "4.2"]);
  assert.deepEqual(namedSections(sections, ["Chapters 3-4 review", ""]), ["3.4", "4.1", "4.2", "4.3", "4.4"]);
  assert.deepEqual(namedSections(sections, ["Quiz 6", "Worth 10.5 points"]), []);
});

// QUIZZES "SHARED STAMPS": the approval's box, unticked by default, and absent from the request unless ticked.
test("the approval window offers the own-copy box and sends it only when ticked", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../static/quiz-drafts.js", import.meta.url), "utf8");
  assert.match(source, /<input type="checkbox" name="own_copies" aria-describedby="quiz-own-copies-hint"> Each student must use their own copy: report any stamp that comes back twice/);
  assert.match(source, /\.\.\.\(form\.elements\.own_copies\.checked \? \{ own_copies: true \} : \{\}\)/);
});
