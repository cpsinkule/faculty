// The Export step's Gradescope panel (AGENTS.md "Sending grades to Gradescope"): what the instructor reads before and after a
// send. The server decides everything; these check that the page says it in plain words, escapes what the server sends, and
// never lists the course coordinator's course, even if a server bug sent it.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";

// ui.js is inlined; its own imports of icons.js (for empty states) and calendar-feed.js (for the campus date) are replaced by
// stubs, since a data: URL has no base.
const ui = readFileSync(new URL("../static/ui.js", import.meta.url), "utf8").replace(/^export /gm, "")
  .replace(/^import \{ icon as iconSvg \} from "\.\/icons\.js";$/m, "const iconSvg = () => '';")
  .replace(/^import \{ campusToday \} from "\.\/calendar-feed\.js";$/m, "const campusToday = () => '';");
const source = readFileSync(new URL("../static/grading-gradescope.js", import.meta.url), "utf8")
  .replace(/^import \{runFormAction, tableMarkup, whenVisible\} from "\.\/ui\.js";$/m, ui)
  // The create dialog has its own test (test_grading_gradescope_create.mjs); here it is only a name.
  .replace(/^import \{createDialog, creationLive, creationsMarkup\} from "\.\/grading-gradescope-create\.js";$/m,
           'const createDialog = () => {}, creationLive = () => false, creationsMarkup = () => "";')
  // The patch the panel redraws with has its own tests (test_dom_patch.mjs, the browser checks).
  .replace(/^import \{patchKeepingDisclosures\} from "\.\/grading-jobs\.js";$/m, "const patchKeepingDisclosures = () => {};")
  // The popup the confirmations share is only a name here.
  .replace(/^import \{confirmPopup\} from "\.\/grading-gradescope-publish\.js";$/m, "const confirmPopup = () => {};");
const {unitProgress, summaryMarkup, questionsMarkup, problemsMarkup, sectionMarkup, publishedMarkup, sendable, forbidden, unitWords, coverageMarkup, remaining, modeMarkup,
  listsMarkup, replacingMarkup, replaceMarkup, mcMarkup, automaticSendMarkup, sheetsPartMarkup, hasSheets, panelMarkup} =
  await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const h = {e: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;")};
const plan = {id: "plan", questions: [{question_key: "A-q1", exam_version: "A", content: {title: "Question 1"}}, {question_key: "B-q1", exam_version: "B", content: {title: "Question 1"}}]};

test("every state of a paper or a grade is said in plain words, and the bar counts only what is settled", () => {
  assert.deepEqual(unitWords, {accepted: "on Gradescope", present: "left alone (already on Gradescope)", refused: "not sent", failed: "did not go through",
    uncertain: "checking", pending: "waiting", sending: "sending", cancelled: "cancelled"});
  const html = unitProgress("paper", {accepted: 6, present: 1, failed: 1, uncertain: 1, pending: 1}, h);
  assert.match(html, /<b>8<\/b> of 10 papers finished/);
  assert.match(html, /<progress max="10" value="8"/);
  assert.match(html, /<b>6<\/b> on Gradescope/);
  assert.match(html, /<b>1<\/b> left alone \(already on Gradescope\)/);
  assert.match(html, /class="grading-batch-failed" data-gradescope-state="failed"><b>1<\/b> did not go through/);
  assert.match(html, /<b>1<\/b> checking/);
  assert.match(html, /<b>1<\/b> waiting/);
  assert.equal(unitProgress("grade", {}, h), "");
});

test("the preview's summary counts papers and grades as the server gave them", () => {
  const preview = {summary: {papers: {upload: 40, already: 2, skipped: 3}, grades: {send: 500, update: 4, already: 10, refused: 7, held: 45}, final: 566, responses: 640, not_final: 74}};
  const html = summaryMarkup(preview, h);
  assert.match(html, /<strong>40<\/strong> to upload/);
  assert.match(html, /<b>2<\/b> already on Gradescope \(uploaded by Faculty earlier\)/);
  assert.match(html, /<b>3<\/b> skipped/);
  assert.match(html, /<strong>504<\/strong> to send/);
  assert.match(html, /<b>4<\/b> of them replace a grade Faculty sent earlier/);
  assert.match(html, /<b>7<\/b> refused/);
  assert.match(html, /<b>45<\/b> held because the paper was not sent/);
  assert.match(html, /<b>74<\/b> of 640 recorded responses have no final grade yet/);
  assert.equal(sendable(preview), 544);
  assert.equal(sendable({summary: {papers: {upload: 0, already: 5}, grades: {send: 0, update: 0, already: 20}}}), 0);
  assert.equal(sendable({summary: {}}), 0);
});

test("a papers-only preview says no grade goes and how many final grades stay in Faculty (requirement, 2026-09-28)", () => {
  const preview = {papers_only: true, summary: {papers: {upload: 308, already: 0, skipped: 2}, grades: {send: 0, update: 0, already: 0, refused: 0, held: 0}, final: 1154, responses: 1232, not_final: 78}};
  const html = summaryMarkup(preview, h);
  assert.match(html, /<strong>308<\/strong> to upload/);
  assert.match(html, /<b>2<\/b> skipped/);
  assert.match(html, /data-gradescope-papers-only><h4>Grades<\/h4><p><strong>0<\/strong> sent · papers only/);
  assert.match(html, /No rubric item is ticked and no score is set\. <b>1,154<\/b> final grades stay in Faculty until you send papers and grades\./);
  assert.doesNotMatch(html, /to send<\/p>|refused|held because/);
  assert.equal(sendable(preview), 308);
  assert.doesNotMatch(summaryMarkup({...preview, summary: {...preview.summary, final: 0}}, h), /stay in Faculty/);
});

test("the send dialog's choice starts on what opened it: papers and grades, or papers only", () => {
  const both = modeMarkup(false, h), papers = modeMarkup(true, h);
  assert.match(both, /<legend class="grading-gradescope-heading">What to send<\/legend>/);
  assert.match(both, /value="grades" checked>/);
  assert.match(both, /value="papers" >/);
  assert.match(papers, /value="grades" >/);
  assert.match(papers, /value="papers" checked>/);
  assert.match(papers, /<strong>Papers only<\/strong><small>Uploads each paper, matched to its student, and sets no grade\. Send the grades later\.<\/small>/);
});

test("questions are named by the plan's own titles and versions, with every refusal reason escaped", () => {
  const html = questionsMarkup({questions: [{version: "B", faculty: "B-q1", gradescope: "Question 1", send: 3, update: 1, already: 0, refused: 2, held: 0,
    reasons: [{reason: "R3 is worth <2> in Faculty", count: 2}]}]}, plan, h);
  assert.match(html, /<td>Question 1 · Version B<\/td><td>Question 1<\/td><td>4<\/td>/);
  assert.match(html, /2 × R3 is worth &lt;2&gt; in Faculty/);
  assert.ok(!html.includes("<2>"));
  assert.equal(questionsMarkup({questions: []}, plan, h), "");
});

test("problems name the student and section only, and a paper is a paper", () => {
  const html = problemsMarkup([{name: "Ada <Lovelace>", section: 507, status: "failed", question: "", reason: "Gradescope did not keep the paper."},
    {name: "Gina", section: 508, status: "present", question: "A-q1", reason: "Someone graded this question on Gradescope."}], plan, h);
  assert.match(html, /Ada &lt;Lovelace&gt;<\/td><td>507<\/td><td>Paper<\/td><td>did not go through<\/td>/);
  assert.match(html, /<td>Question 1 · Version A<\/td><td>left alone \(already on Gradescope\)<\/td>/);
  assert.match(problemsMarkup([], plan, h), /Nothing in this send is waiting for you/);
  // Cancelled papers and grades are counted on the send itself; the list is for what did not simply land.
  assert.match(problemsMarkup([{name: "Hopper", section: 507, status: "cancelled", question: "", reason: ""}], plan, h), /Nothing in this send is waiting for you/);
});

test("Math 140 for Instructors is never offered, however the server spells it", () => {
  const courses = [{id: "1", short: "140-507", name: "MATH 140", term: "Fall 2026"}, {id: "2", short: "Math  140 for   Instructors", name: "", term: "Fall 2026"},
    {id: "3", short: "coord", name: "math 140 FOR instructors", term: ""}];
  assert.ok(forbidden(courses[1]) && forbidden(courses[2]) && !forbidden(courses[0]));
  const html = sectionMarkup({section_id: "s1", section: 507, course_id: "2", containers: [], container_id: "", proposed: false, problem: ""}, courses, h);
  assert.ok(!/instructors/i.test(html), html);
  assert.match(html, /<option value="" selected>Choose a Gradescope course…<\/option>/);
  assert.match(html, /<option value="1" >140-507 · MATH 140 \(Fall 2026\)<\/option>/);
});

test("a section shows its course and exam as chosen, the versions of each exam, and the server's problem", () => {
  const html = sectionMarkup({section_id: "s1", section: 507, course_id: "1", container_id: "9", proposed: true, problem: "",
    containers: [{id: "8", title: "Quiz <1>", versions: [""]}, {id: "9", title: "Test 1", versions: ["A", "B"]}]}, [{id: "1", short: "140-507", name: "", term: ""}], h);
  assert.match(html, /<option value="1" selected>140-507<\/option>/);
  assert.match(html, /<option value="" disabled>Choose the assignment…<\/option>/);
  assert.match(html, /<option value="9" selected>Test 1 \(versions A, B\)<\/option>/);
  assert.match(html, /Quiz &lt;1&gt;<\/option>/);
  assert.match(html, /Proposed by Faculty/);
  const refused = sectionMarkup({section_id: "s1", section: 507, course_id: "1", container_id: "9", proposed: false, problem: "Grades on Test 1 are published.",
    containers: [{id: "9", title: "Test 1", versions: ["A"]}]}, [{id: "1", short: "140-507", name: "", term: ""}], h);
  assert.match(refused, /class="grading-warning" role="status">Grades on Test 1 are published\./);
  assert.ok(!refused.includes("Proposed by Faculty"));
});

test("a section's own linked course is shown as a fact with Change…, and as a picker once changing (requirement, 2026-09-27)", () => {
  const courses = [{id: "1", short: "140-507", name: "Math <Business>", term: "Fall 2026"}, {id: "2", short: "140-508", name: "", term: ""}];
  const section = {section_id: "s1", section: 507, course_id: "1", course: "140-507 · Math <Business>", linked: true, container_id: "9", proposed: false, problem: "",
    containers: [{id: "9", title: "Test 1", versions: ["A"]}]};
  const fixed = sectionMarkup(section, courses, h);
  assert.ok(!fixed.includes("data-gradescope-course="), fixed);
  assert.match(fixed, /data-gradescope-linked="s1">140-507 · Math &lt;Business&gt;<button type="button" class="text-button" data-gradescope-change="s1"/);
  assert.match(fixed, /data-gradescope-container="s1"/);  // the assignment is still chosen per assignment
  const open = sectionMarkup(section, courses, h, "written", new Set(["s1"]));
  assert.match(open, /data-gradescope-course="s1"/);
  assert.ok(!open.includes("data-gradescope-change"));
  // A link to a course Gradescope no longer lists (or the coordinator's) is never shown as a fact.
  assert.match(sectionMarkup({...section, course_id: "7"}, courses, h), /data-gradescope-course="s1"/);
});

test("a published exam is a warning on its section and a popup that names what would go there (requirement, 2026-09-26)", () => {
  const courses = [{id: "1", short: "140-507", name: "", term: ""}];
  const warning = "Grades on Common Exam 1 are published on Gradescope: students see each paper and grade as soon as Faculty writes it. Sending asks you to confirm this exam.";
  const html = sectionMarkup({section_id: "s1", section: 507, course_id: "1", container_id: "9", proposed: false, problem: "", published: true, warning,
    containers: [{id: "9", title: "Common Exam 1", versions: ["A"]}]}, courses, h);
  assert.match(html, /class="grading-warning" role="status" data-gradescope-published="s1">Grades on Common Exam 1 are published on Gradescope/);
  assert.ok(!sectionMarkup({section_id: "s1", section: 507, course_id: "1", container_id: "9", problem: "", published: true, warning, left_out: true, containers: []}, courses, h)
    .includes("data-gradescope-published"));
  const one = publishedMarkup([{section_id: "s1", section: 507, course: "140-507 · Math <for> Business", container_id: "9", container: "Common Exam 1", papers: 85, grades: 640}], h);
  assert.match(one, /<h2 id="gradescope-published-title">Send to a published assignment\?<\/h2>/);
  assert.match(one, /Students can see each paper and grade as soon as Faculty writes it\./);
  assert.match(one, /<strong>Section 507<\/strong> → Common Exam 1 <span>\(140-507 · Math &lt;for&gt; Business\)<\/span>: 85 papers and 640 grades/);
  assert.match(one, /leave this section out of this send/);
  assert.match(one, /<button type="button" class="button" data-published-cancel>Cancel<\/button><button type="button" class="button danger" data-published-confirm>Send to the published assignment<\/button>/);
  const two = publishedMarkup([{section: 507, container_id: "9", container: "MC 1", sheets: 1}, {section: 508, container_id: "10", container: "MC 1", sheets: 30}], h, "sheets");
  assert.match(two, /Send to published assignments\?/);
  assert.match(two, /each sheet and the score Gradescope gives it/);
  assert.match(two, /: 1 sheet<\/li>.*: 30 sheets<\/li>/s);
  assert.match(two, /Send to the published assignments<\/button>/);
});

test("a section left out keeps its choices, says so plainly, and its papers and grades are counted apart", () => {
  const courses = [{id: "1", short: "140-508", name: "", term: ""}];
  const html = sectionMarkup({section_id: "s2", section: 508, course_id: "1", container_id: "9", proposed: false, problem: "", left_out: true, containers: []}, courses, h);
  assert.match(html, /data-gradescope-section="s2" data-left-out="true"/);
  assert.match(html, /<input type="checkbox" data-gradescope-leave="s2" checked>Leave this section out of this send/);
  assert.match(html, /Left out: none of this section’s papers or grades are sent this time\. Untick to include it\./);
  assert.match(html, /data-gradescope-course="s2"[^>]*data-left-out="true"/);
  assert.match(html, /<option value="1" selected>140-508<\/option>/);
  assert.ok(!html.includes("Proposed by Faculty"));
  const included = sectionMarkup({section_id: "s2", section: 508, course_id: "1", container_id: "", proposed: true, problem: "", containers: []}, courses, h);
  assert.match(included, /data-gradescope-leave="s2" >Leave this section out of this send/);
  assert.ok(!included.includes("Left out:") && !included.includes('data-left-out'));
  const summary = summaryMarkup({summary: {papers: {upload: 85, already: 0, skipped: 0, left_out: 240}, grades: {send: 600, update: 0, already: 0, refused: 0, held: 0, left_out: 960}}}, h);
  assert.match(summary, /<b>240<\/b> in sections left out of this send/);
  assert.match(summary, /<b>960<\/b> in sections left out of this send/);
  assert.ok(!summaryMarkup({summary: {papers: {upload: 1}, grades: {send: 1}}}, h).includes("left out"));
});

test("the headline is what is on Gradescope across every send, and says what is left", () => {
  const coverage = {papers: {total: 338, sent: 335}, grades: {final: 2701, sent: 2679, changed: 2, not_sent: 20}};
  const html = coverageMarkup(coverage, h);
  assert.match(html, /<strong>335 of 338 papers · 2,679 of 2,701 final grades<\/strong> are on Gradescope/);
  assert.match(html, /20 final grades not sent yet/);
  assert.match(html, /2 changed since they were sent/);
  assert.match(html, /3 papers not uploaded/);
  assert.equal(remaining(coverage), 25);
  const done = {papers: {total: 2, sent: 2}, grades: {final: 4, sent: 4, changed: 0, not_sent: 0}};
  assert.equal(remaining(done), 0);
  assert.match(coverageMarkup(done, h), /everything final is there/);
  assert.equal(remaining(undefined), null);  // an older server: the send button stays the primary action
  assert.equal(coverageMarkup(undefined, h), "");
  const sheets = {sheets: {matched: 80, sent: 60, waiting: 5}};
  assert.equal(remaining(sheets, "sheets"), 20);
  const sheetHtml = coverageMarkup(sheets, h, "sheets");
  assert.match(sheetHtml, /<strong>60 of 80 matched sheets<\/strong> are on Gradescope/);
  assert.match(sheetHtml, /20 not sent yet/);
  assert.match(sheetHtml, /5 more sheets in scans still waiting for scan checks/);
  assert.ok(!/\bgrades?\b/.test(sheetHtml));  // upload only: no grade is ever sent for multiple choice
});

test("replacing grades someone else made: counted, listed with who made them, and asked once more (requirement, 2026-10-01)", () => {
  const preview = {replace_others: true, summary: {papers: {upload: 0, already: 78}, grades: {send: 170, update: 0, already: 0, refused: 0, held: 0, replace: 10},
    final: 312, responses: 312, not_final: 0},
    replacing: {grades: 10, score_changes: 9, same: 126, same_by: [{name: "Ada <TA>", grades: 120}, {name: "Sam TA", grades: 6}], by: [{name: "Ada <TA>", grades: 10}],
      listed: [{name: "Kole <K>", section: 508, question: "A-q1", gradescope: "0.5", by: "Ada <TA>", faculty: "1"}]},
    questions: [{version: "A", faculty: "A-q1", gradescope: "Question 1", send: 1, update: 0, replace: 2, already: 3, refused: 0, held: 0, reasons: []}]};
  const html = summaryMarkup(preview, h);
  assert.match(html, /<strong>180<\/strong> to send/);
  assert.match(html, /<b>10<\/b> of them replace a grade someone else made on Gradescope \(listed below\)/);
  // Never sent by Faculty: someone else graded them there exactly as Faculty did, and who (requirement, 2026-10-02).
  assert.match(html, /<b>126<\/b> of them were already graded on Gradescope by someone else, exactly as Faculty graded them: nothing changes there \(by Ada &lt;TA&gt;: 120, Sam TA: 6\)<\/li>/);
  assert.match(summaryMarkup({...preview, replacing: {...preview.replacing, same_by: undefined}}, h), /exactly as Faculty graded them: nothing changes there<\/li>/);
  assert.equal(sendable(preview), 180);
  const table = questionsMarkup(preview, plan, h);
  assert.match(table, /<th[^>]*>Replacing<\/th>/);
  assert.match(table, /<td>3<\/td><td>2<\/td><td>3<\/td>/);  // to send (1 + 2), replacing, already there
  assert.doesNotMatch(questionsMarkup({...preview, replace_others: undefined}, plan, h), /Replacing/);
  const lists = listsMarkup(preview, plan, h);
  assert.match(lists, /data-gradescope-replacing open><summary>Grades this send replaces · 10<\/summary>/);
  assert.match(lists, /Kole &lt;K&gt;.*Question 1 · Version A.*<td>0\.5<\/td><td>Ada &lt;TA&gt;<\/td><td>1<\/td>/s);
  assert.match(lists, /Showing the first 1 of 10/);
  assert.equal(listsMarkup({summary: {}}, plan, h), "");
  const popup = replacingMarkup(preview.replacing, h);
  assert.match(popup, /Replace 10 grades on Gradescope\?/);
  assert.match(popup, /<strong>Ada &lt;TA&gt;<\/strong>: 10 grades/);
  assert.match(popup, /9 scores change; the other 1 grade keeps its score and differs only in rubric items, adjustment or comment\./);
  assert.match(popup, /data-replacing-cancel>Cancel<\/button><button type="button" class="button danger" data-replacing-confirm>Replace 10 grades<\/button>/);
  assert.match(replacingMarkup({grades: 1, score_changes: 0, by: []}, h), /No score changes: they differ only in rubric items, adjustment or comment\./);
  assert.match(popup, /A grade changed on Gradescope after this preview is left alone and listed with the send's problems, so fewer may be replaced\./);
});

test("the replace choice is off unless ticked and never offered with papers only", () => {
  assert.match(replaceMarkup(false, false), /<input type="checkbox" name="gradescope-replace"  >Replace grades someone else made on Gradescope/);
  assert.match(replaceMarkup(true, false), /name="gradescope-replace" checked >/);
  const papers = replaceMarkup(true, true);
  assert.match(papers, /name="gradescope-replace"  disabled>/);
  assert.match(papers, /Papers only sets no grade, so it replaces none\./);
  assert.match(replaceMarkup(false, false), /Off, a question someone already graded there \(a TA, or you on Gradescope\) is left alone\./);
});

test("Label answers renames Gradescope's MC items to show the correct answer, and says where it is (requirement, 2026-10-28)", () => {
  const hb = {...h, button: (action, label, attrs = "") => `<button data-grading-action="${action}" ${attrs}>${label}</button>`};
  const mc = {checked: true, running: false, counts: {compared: 3, agree: 3}, differ: [], failed: []};
  assert.match(mcMarkup(mc, hb, true), />Check MC again<\/button><button data-grading-action="gradescope-mc-labels" [^>]*>Label answers on Gradescope</);
  assert.doesNotMatch(mcMarkup(mc, hb, false), /gradescope-mc-labels/);  // Gradescope not set up: nothing to press
  const running = mcMarkup({...mc, labels: {running: true, sections: [], renamed: 0}}, hb, true);
  assert.match(running, /role="status" data-gradescope-mc-labels>Labelling answers on Gradescope…/);
  assert.doesNotMatch(running, /gradescope-mc-labels"/);  // one run at a time
  const done = mcMarkup({...mc, labels: {running: false, renamed: 558, at: "2026-10-28T15:00:00Z", sections: [{section: 508, state: "accepted"}, {section: 515, state: "failed", error: "3 answers on Gradescope still carry only their letters. Label answers again to finish."}]}}, hb, true);
  assert.match(done, /<strong>Answers are not all labelled on Gradescope\.<\/strong> 558 rubric items renamed to show the correct answer/);
  assert.match(done, /<strong>Section 515<\/strong> <span>not finished: 3 answers on Gradescope still carry only their letters/);
  assert.doesNotMatch(done, /Section 508/);
  assert.match(done, />Label answers again</);
});

test("the MC check names each student Gradescope scored differently, with the questions and why (requirement, 2026-10-06)", () => {
  const hb = {...h, button: (action, label, attrs = "") => `<button data-grading-action="${action}" ${attrs}>${label}</button>`};
  assert.equal(mcMarkup(null, hb, true), "");
  assert.match(mcMarkup({checked: false, running: false, counts: null, differ: [], failed: []}, hb, true), /Not compared yet[\s\S]*>Check MC now</);
  assert.match(mcMarkup({checked: false, running: true, waiting: "", counts: null, differ: [], failed: []}, hb, true), /role="status">Reading Gradescope’s multiple-choice scores…/);
  assert.doesNotMatch(mcMarkup({checked: false, running: true, counts: null, differ: [], failed: []}, hb, true), /gradescope-mc-check/);  // one check at a time
  const mc = {checked: true, running: false, at: "2026-10-06T15:00:00Z", sections: [508, 514], failed: [{section: 515, error: "Gradescope answered 404 for a page Faculty reads."}],
    counts: {compared: 338, agree: 337, differ: 1, not_on_gradescope: 0, not_graded: 0, faculty_unscored: 0, unmatched: 0},
    differ: [{name: "Shaw, Sophie Lynn", section: 508, gradescope: {version: "B", total: "50"}, faculty: {version: "B", total: "54"},
      questions: [{number: 6, gradescope: "0", faculty: "4", why: "left blank; your key gives this question to everyone, Gradescope gives a blank 0"}], detail: "x"}]};
  const html = mcMarkup(mc, hb, true);
  assert.match(html, /<strong>1 student scored differently on Gradescope\.<\/strong> 337 of 338 sheets compared match/);
  assert.match(html, /Sections 508, 514/);
  assert.match(html, /<strong>Shaw, Sophie Lynn<\/strong> <span>Section 508 · Gradescope 50 \(Version B\), Faculty 54 \(Version B\)<\/span>/);
  assert.match(html, /Question 6: Gradescope 0, Faculty 4 · left blank; your key gives this question to everyone/);
  assert.match(html, /role="alert">Section 515 could not be read: Gradescope answered 404/);
  assert.match(html, />Check MC again</);
  assert.doesNotMatch(mcMarkup(mc, hb, false), /gradescope-mc-check/);  // Gradescope not set up: nothing to press
  assert.match(mcMarkup({...mc, differ: [], counts: {...mc.counts, agree: 338, differ: 0}}, hb, true), /Gradescope and Faculty agree\.<\/strong> 338 of 338 sheets compared match/);
  // MC CORRECTIONS (requirement, 2026-10-22): each correction is listed; a corrected
  // difference is no longer one "scored differently".
  const corrected = {...mc, failed: [], differ: [{...mc.differ[0], corrected: true}], corrections: [
    {name: "Shaw, Sophie Lynn", section: 508, version: "B", number: 6, gradescope: "0", faculty: "4", why: "left blank; your key gives this question to everyone, Gradescope gives a blank 0", state: "accepted", error: ""},
    {name: "Ewing, Joan K", section: 514, version: "A", number: 6, gradescope: "0", faculty: "4", why: "", state: "refused", error: "Someone put a point adjustment (1) on this question on Gradescope; Faculty leaves it alone."}]};
  const fixed = mcMarkup(corrected, hb, true);
  assert.match(fixed, /<strong>Faculty is correcting Gradescope’s scores to yours\.<\/strong>/);
  assert.match(fixed, /<strong>1 question corrected on Gradescope to your score\.<\/strong>/);
  assert.match(fixed, /data-gradescope-mc-correction="accepted"><strong>Shaw, Sophie Lynn<\/strong> <span>Section 508 · Version B · Question 6: Gradescope 0 → 4 \(left blank; [^)]*\) · corrected on Gradescope<\/span>/);
  assert.match(fixed, /Question 6: Gradescope 0 → 4 · not corrected: Someone put a point adjustment \(1\)/);
  assert.doesNotMatch(fixed, /data-gradescope-mc-differ/);
  // SETTLED (requirement, 2026-11-01): a correction a later read shows Gradescope agreeing with folds into "Earlier corrections".
  const later = mcMarkup({...corrected, corrections: [{...corrected.corrections[0], settled: true}, corrected.corrections[1]]}, hb, true);
  assert.match(later, /<strong>0 questions corrected on Gradescope to your score\.<\/strong>/);
  assert.match(later, /<details class="disclosure" data-gradescope-mc-settled><summary>Earlier corrections Gradescope now agrees with · 1<\/summary><ul class="grading-gradescope-links"><li data-gradescope-mc-correction="accepted"><strong>Shaw/);
  const quiet = mcMarkup({...corrected, corrections: [{...corrected.corrections[0], settled: true}]}, hb, true);
  assert.doesNotMatch(quiet, /corrected on Gradescope to your score/);
  assert.match(quiet, /Earlier corrections Gradescope now agrees with · 1/);
});

test("a late paper Faculty sent by itself: the card says so, and an unpublished version of a published set asks for your own Publish", () => {
  // GRADESCOPE "AUTOMATIC LATE-PAPER SEND"; 2026-10-09 review: a makeup version Faculty adds to a published set lands unpublished,
  // and the card said "published". Faculty never publishes: the text button only leads to the instructor's Publish grades.
  assert.equal(automaticSendMarkup({row: "s:1"}, h), '<p class="supporting-copy" data-gradescope-automatic>Sent by Faculty by itself, after it certified a late paper.</p>');
  assert.match(automaticSendMarkup({published: true}, h), /late paper, although grades are published there \(as you chose\)\.<\/p>$/);
  const hidden = automaticSendMarkup({published: true, unpublished_version: "E"}, h);
  assert.doesNotMatch(hidden, /as you chose/);
  assert.match(hidden, /data-gradescope-unpublished>Version E is not published on Gradescope, so the student does not see this grade yet · Use Publish grades on Gradescope… at the top of Certify<\/p>/);
  assert.doesNotMatch(hidden, /<button/);  // one place to publish: Certify's header (2026-10-18 review item 10)
});

test("a send Faculty made after your own Certify says so, and never points to a late paper", () => {
  // GRADESCOPE "AFTER CERTIFYING" (2026-10-12): the send's card names who sent it and why.
  assert.equal(automaticSendMarkup({after: "certified", run: "r1", certification_id: "c1"}, h),
    '<p class="supporting-copy" data-gradescope-automatic>Sent by Faculty by itself, after you certified.</p>');
  assert.match(automaticSendMarkup({after: "certified", published: true}, h), /after you certified \(grades were already published there\)\.<\/p>$/);
  assert.doesNotMatch(automaticSendMarkup({after: "certified", published: true}, h), /late paper|Publish grades/);
});

test("the Send dialog's Bubble sheets part: counts, a tick on by default, problems and skipped sheets; nothing without sheets", () => {
  // GRADESCOPE "THE BUBBLE SHEETS TOO" (requirement, 2026-10-26).
  const sheets = {configured: true, fingerprint: "f".repeat(64), summary: {sheets: {upload: 3, already: 1, skipped: 1}, read: 5, waiting: 2},
    sections: [{section: 507, problem: ""}, {section: 508, problem: "Choose the Gradescope bubble-sheet exam these sheets go to in this course."}],
    skipped: [{name: "Bob <B>", section: 507, reason: "No version was read on this sheet."}], published: [{section: 507, container_id: "9"}], targets: {}};
  const html = sheetsPartMarkup(sheets, true, h);
  assert.match(html, /<input type="checkbox" name="gradescope-sheets" checked>Also upload 3 bubble sheets to the bubble-sheet exam, before the papers/);
  assert.match(html, /3 to upload · 1 already on Gradescope · 1 skipped · 2 in scans still waiting for scan checks\. Gradescope scores them with its own key/);
  assert.match(html, /Section 508: Choose the Gradescope bubble-sheet exam these sheets go to in this course\. Choose it on the Bubble sheets page\./);
  assert.match(html, /Skipped sheets · 1<\/summary>.*Bob &lt;B&gt;/s);
  assert.match(html, /published for Section 507: sending asks you to confirm/);
  assert.doesNotMatch(sheetsPartMarkup(sheets, false, h), /name="gradescope-sheets" checked/);
  assert.doesNotMatch(sheetsPartMarkup(sheets, false, h), /sending asks you to confirm/);
  // Nothing to upload, or a send of sheets still running: no tick.
  assert.doesNotMatch(sheetsPartMarkup({...sheets, summary: {sheets: {upload: 0, already: 4}, read: 4}}, true, h), /name="gradescope-sheets"/);
  assert.match(sheetsPartMarkup({...sheets, running: "s1"}, true, h), /still running; they are not part of this send/);
  // An assignment without bubble sheets shows nothing; one whose preview failed says so; one still loading says it is reading.
  assert.equal(hasSheets({configured: true, summary: {sheets: {upload: 0}, read: 0, waiting: 0}, sections: []}), false);
  assert.equal(sheetsPartMarkup({configured: true, summary: {read: 0}, sections: []}, true, h), "");
  assert.match(sheetsPartMarkup({error: "Gradescope could not be read"}, true, h), /could not read the bubble sheets: Gradescope could not be read Send them from the Bubble sheets page/);
  assert.match(sheetsPartMarkup(null, true, h), /Reading the bubble sheets/);
});

test("the MC check is shown once: in the bubble-sheets panel where Certify has one beside the written panel (requirement, 2026-10-27)", () => {
  const hb = {...h, button: (action, label, attrs = "") => `<button data-grading-action="${action}" ${attrs}>${label}</button>`};
  const status = {configured: true, targets: [], sends: [], coverage: null, mc_check: {checked: false, running: false, counts: null, differ: [], failed: []}};
  const sheetsPlan = {...plan, bubble: {sheets: true}};
  assert.doesNotMatch(panelMarkup(status, sheetsPlan, hb, "written"), /Check MC now/);
  assert.match(panelMarkup(status, sheetsPlan, hb, "sheets"), /Not compared yet[\s\S]*>Check MC now</);
  assert.match(panelMarkup(status, plan, hb, "written"), />Check MC now</);  // no bubble-sheets panel: the written panel shows it
});
