import assert from "node:assert/strict";
import test from "node:test";

import * as s from "../static/grading-certification-stats.js";

// Certification and Export's pure pieces: every figure is the server's (GET .../certification, spec §3.1); these only order, filter
// and draw it. Synthetic names; no UIN anywhere.
const e = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const h = {e};
const stats = (n, mean, bins = [0, 0, 0, 0, 0, 1, 2, 4, 2, 1]) => ({n, mean, median: mean, sd: 7.5, min: 41, max: 98,
  histogram: bins.map((count, i) => ({from: i * 10, to: i * 10 + 10, count})), excluded: {zero: 1, missing: 0, left_out: 0, excused: 0}});
const student = (row, name, extra = {}) => ({row, name, section: "507", exam_version: "A", participant_version: 3, expectation: "expected", collection: "complete",
  status: "complete", reason: "", frq: {state: "complete", earned: "31.5", possible: "40", final: 12, expected: 12}, bubble: {state: "complete", earned: "52", possible: "60"},
  total: {earned: "83.5", possible: "100", pct: 83.5}, scores: [], flagged: 0, zero_questions: 0, dr: [], gradescope: {paper: true, grades: {sent: 12, changed: 0, not_sent: 0}, sheet: true},
  certified: null, changed_since: false, ...extra});
const VIEW = {
  plan_id: "p", title: "Test 1", parts: ["frq", "bubble"], possible: {frq: "40", bubble: "60", total: "100"}, versions: ["A", "B"],
  sections: [{section_id: "s507", number: "507"}, {section_id: "s508", number: "508"}],
  certification: {state: "none", id: null, created_at: null, changed: [], gradebook: null}, history: [],
  stats: {total: stats(10, 72.4), frq: stats(10, 28.1), bubble: stats(10, 44.3), by_version: {A: stats(6, 74), B: stats(4, 70)}, by_section: {"507": stats(5, 71), "508": stats(5, 73.8)}},
  questions: [
    {question_id: "q1", title: "Question 1", printed_number: "1", version: "A", max: "4", n_final: 6, not_final: 0, mean: 3.1, median: 3, sd: 0.8, pct_full: 50, pct_zero: 0, automatic: 5, instructor: 1, flagged: 2, overridden: 1, gradescope: {sent: 6, changed: 0, not_sent: 0, unsendable: null}},
    {question_id: "q2", title: "Question 2", printed_number: "2", version: "A", max: "6", n_final: 6, not_final: 0, mean: 2.2, median: 2, sd: 1.1, pct_full: 10, pct_zero: 20, automatic: 3, instructor: 3, flagged: 4, overridden: 0, gradescope: {sent: 4, changed: 1, not_sent: 1, unsendable: null}},
    {question_id: "q3", title: "Question 1", printed_number: "1", version: "B", max: "4", n_final: 4, not_final: 1, mean: null, median: null, sd: null, pct_full: 0, pct_zero: 0, automatic: 4, instructor: 0, flagged: 0, overridden: 0, gradescope: {sent: 0, changed: 0, not_sent: 0, unsendable: "rubric not imported from Gradescope's export"}},
  ],
  bubble_questions: {A: [{number: 1, correct_pct: 91.2, common_wrong: "C"}]},
  students: [
    student("s507:1", "Example, Alice"),
    student("s507:2", "Émile Zola", {status: "missing", reason: "No paper scanned", frq: {state: "none"}, bubble: {state: "none"}, total: null, gradescope: {paper: false, grades: {sent: 0, changed: 0, not_sent: 0}, sheet: false}}),
    student("s508:3", "Nguyen, Carol", {section: "508", exam_version: "B", status: "in_progress", reason: "Bubble sheet needs review", flagged: 2, zero_questions: 1, changed_since: true}),
    student("s508:4", "Doe, Dana", {section: "508", status: "dr_ahead", dr: [{date: "2026-10-02"}], total: null}),
    student("s508:5", "Roe, Rick", {section: "508", gradescope: {paper: true, grades: {sent: 11, changed: 1, not_sent: 0}, sheet: true, uncovered: true}}),
  ],
  counts: {participants: 5, complete: 2, missing: 1, flagged: 6, zero_somewhere: 1, changed_since: 1, dr: 1, excused: 0, not_on_gradescope: 1},
  checks: [
    {id: "zero_no_work", level: "confirm", title: "Students with no work receive 0", detail: "", count: 1, digest: "d1", rows: [{row: "s507:2", name: "Émile Zola", section: "507", detail: "No paper scanned"}], provisional: true},
    {id: "automatic_unchecked", level: "info", title: "Automatic grades nobody looked at", detail: "Spot-check a few.", count: 12, provisional: true},
  ],
  blocking: 0, to_confirm: 1,
  gradescope: {configured: true, written: {papers: {total: 4, sent: 4}, grades: {final: 30, sent: 28, changed: 1, not_sent: 1}}, sheets: {matched: 4, sent: 4, waiting: 0}, live: false},
  live: false,
};

test("checks fall into Block, Confirm and Info; the band names what blocks first, then a changed certification, then the zeros", () => {
  const groups = s.checkGroups([...VIEW.checks, {id: "grading_unfinished", level: "block", title: "Grading is not finished", action: {label: "Review grades", href: "#grading/p?tab=review"}}]);
  assert.deepEqual([groups.block.length, groups.confirm.length, groups.info.length], [1, 1, 1]);
  const blocked = s.bandModel({...VIEW, checks: [...VIEW.checks, {id: "a", level: "block", title: "A"}, {id: "b", level: "block", title: "B", action: {label: "Fix", href: "#x"}}, {id: "c", level: "block", title: "C"}, {id: "d", level: "block", title: "D"}]});
  assert.equal(blocked.tone, "warning");
  assert.equal(blocked.title, "4 things must be finished before certifying");
  assert.equal(blocked.body, "A · B · C");
  assert.equal(blocked.action, null, "the first blocking check has no link");
  const open = s.bandModel(VIEW);
  // The zeros are the consequence, in the warning tone (usability audit, 2026-10-04); with none, other confirmations are counted.
  assert.deepEqual([open.tone, open.title, open.body], ["warning", "Certifying records 0 for 1 student", "1 student has no grade: you confirm each 0 when you certify."]);
  const more = s.bandModel({...VIEW, checks: [...VIEW.checks, {id: "dr_ahead", level: "confirm", title: "1 DR student left out until their exam", count: 1}]});
  assert.equal(more.body, "1 student has no grade: you confirm each 0 when you certify. 1 other item asks for your confirmation too.");
  const graded = s.bandModel({...VIEW, counts: {...VIEW.counts, missing: 0}, checks: VIEW.checks.filter(c => c.level !== "confirm")});
  assert.deepEqual([graded.tone, graded.title, graded.body], ["info", "Nothing blocks certification", "Every student has a grade."]);
  const changed = s.bandModel({...VIEW, certification: {state: "changed", created_at: "2026-09-26T15:00:00Z", changed: ["grades", "dr_date_passed"]}}, "Sep 26");
  assert.equal(changed.title, "Grades changed since the certification of Sep 26");
  assert.match(changed.body, /grades, a DR exam date passed/);
  // Certified: the column by name, who sees the grades, and a link to the column itself (certify-5).
  const current = s.bandModel({...VIEW, certification: {state: "current", gradebook: {title: "Test <1>", item_id: "item 1"}}}, "Sep 26", {termId: "t1"});
  assert.deepEqual([current.tone, current.title, current.body], ["success", "Certified Sep 26",
    "Recorded in the gradebook column “Test <1>”. Students see these grades only after you publish them on Gradescope (below) or send them to Canvas from the gradebook."]);
  assert.deepEqual(current.action, {label: "Open the gradebook column", href: "#gradebook/t1?item=item%201"});
  assert.equal(s.bandModel({...VIEW, certification: {state: "current", gradebook: {title: "Test 1", item_id: "i"}}}, "Sep 26").action, null, "no term, no link");
});

test("nobody graded yet: the band says certifying would record only zeros and names the next step (certify-7)", () => {
  const none = student("s507:9", "None, Nia", {status: "missing", frq: {state: "none"}, bubble: {state: "none"}, total: null});
  const view = {...VIEW, checks: [], students: [none, {...none, row: "s508:9", section: "508"}], counts: {participants: 2, complete: 0, in_progress: 0, missing: 2}};
  assert.equal(s.noGradesYet(view), true);
  const band = s.bandModel(view, "", {next: {label: "Grade"}});
  assert.deepEqual([band.tone, band.title, band.body, band.action], ["warning", "No student has a grade yet", "Certifying now would record 0 for all 2 students. Next: Grade.", null]);
  // One part graded is a grade; so is one student in progress; a certification is past this.
  assert.equal(s.noGradesYet({...view, students: [{...none, status: "missing_part"}]}), false);
  assert.equal(s.noGradesYet({...view, counts: {...view.counts, in_progress: 1}}), false);
  assert.equal(s.noGradesYet({...view, certification: {state: "changed"}}), false);
  // The header: the step that grades is the primary; Certify grades… stays, plain.
  const grade = {key: "grade", label: "Grade", current: true, done: false, action: {label: "Grade responses", href: "#grading/p?tab=grade"}, destination: "#grading/p?tab=grade"};
  const markup = pageMarkup(view, state(), [{...steps[0], current: false, done: false}, grade, {...steps[1], current: false}], h);
  assert.equal((markup.match(/class="button primary"/g) || []).length, 1);
  assert.match(markup, /<a class="button primary" data-step-action href="#grading\/p\?tab=grade">Grade responses<\/a><button type="button" class="button" data-cert-certify>Certify grades…<\/button>/);
  assert.match(markup, /No student has a grade yet/);
});

test("a check's title carries its count once, and its rows' fold says what they are (certify-13)", () => {
  assert.equal(s.countedTitle({title: "13 students with no work receive 0", count: 13}), "13 students with no work receive 0");
  assert.equal(s.countedTitle({title: "Automatic grades nobody looked at", count: 1025}), "Automatic grades nobody looked at (1,025)");
  assert.equal(s.countedTitle({id: "gradebook_column", title: "Create the gradebook column “Test 1”", count: 1}), "Create the gradebook column “Test 1”");
  assert.equal(s.countedTitle({title: "Bubble key", count: 0}), "Bubble key");
  const view = {...VIEW, checks: [{id: "zero_question_all", level: "confirm", title: "1 question where every student scored 0", count: 1, digest: "d", rows: [{question_id: "q2", name: "Question 2"}]},
    {id: "zero_no_work", level: "confirm", title: "Students with no work receive 0", count: 1, digest: "d1", rows: [{row: "s507:2", name: "Émile Zola", section: "507"}]}]};
  const markup = pageMarkup(view, state(), steps, h);
  assert.match(markup, /1 question where every student scored 0<\/strong>/);
  assert.match(markup, /Students with no work receive 0 \(1\)<\/strong>/);
  assert.match(markup, /<span>Questions<\/span>/);
  assert.match(markup, /<span>Students<\/span>/);
  assert.doesNotMatch(markup, /Who or what/);
});

test("a check's rows fold counts the rows it lists, not what the title counts; a list cut at the server's cap says so", () => {
  const fold = check => [...pageMarkup({...VIEW, checks: [check]}, state(), steps, h).matchAll(/<summary><span>(Students|Questions|Sheets)<\/span><span class="disclosure-hint">([^<]*)<\/span>/g)]
    .map(m => [m[1], m[2]]);
  // Grades and papers over student rows (6 = 4 grades + 2 papers, for 2 students), and grades over question rows.
  assert.deepEqual(fold({id: "gradescope_written", level: "confirm", title: "Written grades not on Gradescope", count: 6, digest: "g", requires: "decision",
    rows: [{row: "s507:1", name: "Example, Alice", section: "507"}, {row: "s508:5", name: "Roe, Rick", section: "508"}]}), [["Students", "2"]]);
  assert.deepEqual(fold({id: "automatic_unchecked", level: "info", title: "1,025 automatic grades nobody looked at", count: 1025,
    rows: ["q1", "q2", "q3"].map(q => ({question_id: q, name: q, section: "", detail: "300 automatic"}))}), [["Questions", "3"]]);
  // At the cap (grading/certification_rules.ROWS) with more counted: the first 400.
  const many = Array.from({length: 400}, (_, i) => ({row: `s507:${i}`, name: `Student ${i}`, section: "507"}));
  assert.deepEqual(fold({id: "zero_no_work", level: "confirm", title: "Students with no work receive 0", count: 450, digest: "z", rows: many}), [["Students", "first 400"]]);
  assert.deepEqual(fold({id: "zero_no_work", level: "confirm", title: "Students with no work receive 0", count: 400, digest: "z", rows: many}), [["Students", "400"]]);
  assert.equal(s.rowsCut({count: 450, rows: many}), true);
  assert.equal(s.rowsCut({count: 6, rows: many.slice(0, 2)}), false);
});

test("the histogram is ten SVG bars scaled to the tallest, with counts as text and no inline style", () => {
  const bars = s.histogramModel(VIEW.stats.total);
  assert.equal(bars.length, 10);
  assert.equal(bars[7].share, 1);
  assert.equal(bars[0].share, 0);
  assert.equal(bars[9].label, "90–100%");
  const markup = s.histogramMarkup(VIEW.stats.total, h, "Totals");
  assert.equal((markup.match(/<rect /g) || []).length, 10);
  assert.ok(!/style=/.test(markup));
  assert.match(markup, /aria-label="Totals\. 0–10%: 0, .*70–80%: 4/);
  assert.match(s.histogramMarkup({n: 0, histogram: []}, h), /No complete total yet/);
});

test("distribution choices: all, each version, each section; each picks its own figures", () => {
  assert.deepEqual(s.distributionChoices(VIEW).map(c => c.value), ["all", "version:A", "version:B", "section:507", "section:508"]);
  assert.equal(s.distributionStats(VIEW, "version:B").mean, 70);
  assert.equal(s.distributionStats(VIEW, "section:508").mean, 73.8);
  assert.equal(s.distributionStats(VIEW, "all").mean, 72.4);
});

test("summary cards: every grade, the mean of the possible, missing, flagged, your decisions and Gradescope, each opening a filter", () => {
  const cards = Object.fromEntries(s.cardsModel(VIEW).map(c => [c.key, c]));
  assert.equal(cards.complete.value, "2 / 5");
  assert.equal(cards.mean.value, "72.4 / 100");
  assert.equal(cards.missing.jump, "students:missing");
  assert.equal(cards.missing.attention, true);
  assert.equal(cards.instructor.value, "4 / 16");
  assert.equal(cards.gradescope.value, "28 grades · 4 papers · 4 sheets");
  assert.equal(s.cardsModel({...VIEW, gradescope: {configured: false}}).some(c => c.key === "gradescope"), false);
  assert.match(s.cardsMarkup(VIEW, h), /<button type="button" class="stat" data-cert-card="missing" data-cert-jump="students:missing" data-attention>/);
});

test("questions sort by any column, stably, with missing numbers last; the Gradescope column says what is not sent", () => {
  assert.deepEqual(s.sortQuestions(VIEW.questions, {key: "", dir: "asc"}).map(q => q.question_id), ["q1", "q2", "q3"]);
  assert.deepEqual(s.sortQuestions(VIEW.questions, {key: "mean", dir: "desc"}).map(q => q.question_id), ["q1", "q2", "q3"]);
  assert.deepEqual(s.sortQuestions(VIEW.questions, {key: "mean", dir: "asc"}).map(q => q.question_id), ["q2", "q1", "q3"]);
  assert.deepEqual(s.sortQuestions(VIEW.questions, {key: "version", dir: "desc"}).map(q => q.question_id), ["q3", "q1", "q2"]);
  assert.deepEqual(s.sortQuestions(VIEW.questions, {key: "decided", dir: "desc"}).map(q => q.question_id), ["q2", "q1", "q3"]);
  const table = s.questionTableMarkup(VIEW, h, {key: "mean", dir: "desc"});
  assert.match(table, /<th scope="col" aria-sort="descending"><button type="button" class="certification-sort" data-cert-sort="mean">Mean/);
  assert.match(table, /data-question="q2".*2 not sent/);
  assert.match(table, /Cannot send/);
  assert.match(table, /1 not final/);
  assert.match(s.bubbleQuestionsMarkup(VIEW, h), /<th scope="row">1<\/th><td>91%<\/td><td>C<\/td>/);
});

test("student names: accents, case and order ignored; each word begins a word of the name or is the section", () => {
  assert.equal(s.normalizeName("Émile  Zola"), "emile zola");
  assert.ok(s.nameMatches(VIEW.students[1], "zola emi"));
  assert.ok(s.nameMatches(VIEW.students[2], "carol 508"));
  assert.ok(!s.nameMatches(VIEW.students[2], "carol 507"));
  assert.ok(!s.nameMatches(VIEW.students[0], "lice"), "a word must begin a name's word");
  assert.ok(s.nameMatches(VIEW.students[0], ""));
});

test("digits typed are a server search from four on; a digit search never filters names in the browser", () => {
  assert.equal(s.isDigitSearch("123"), false);
  assert.equal(s.isDigitSearch("12 34"), true);
  assert.equal(s.digitsOf("123-4"), "1234");
  assert.equal(s.digitsOf("Alice 1234"), "");
  assert.equal(s.filterStudents(VIEW.students, {query: "1234"}).length, 5, "until the server answers, digits hide nobody");
  const matches = [{row: "s508:3", similarity: 0.9}, {row: "s507:1", similarity: 0.7}, {row: "gone", similarity: 0.5}];
  assert.deepEqual(s.filterStudents(VIEW.students, {query: "1234", matches}).map(x => x.row), ["s508:3", "s507:1"]);
  assert.deepEqual(s.filterStudents(VIEW.students, {filter: "flagged", query: "1234", matches}).map(x => x.row), ["s508:3"]);
});

test("student filters: missing, in progress, flagged, zero somewhere, changed, DR or excused, not on Gradescope", () => {
  const rows = filter => s.filterStudents(VIEW.students, {filter}).map(x => x.row);
  assert.deepEqual(rows("missing"), ["s507:2"]);
  assert.deepEqual(rows("in_progress"), ["s508:3"]);
  assert.deepEqual(rows("flagged"), ["s508:3"]);
  assert.deepEqual(rows("zero"), ["s508:3"]);
  assert.deepEqual(rows("changed"), ["s508:3"]);
  assert.deepEqual(rows("dr_excused"), ["s508:4"]);
  assert.deepEqual(rows("gradescope"), ["s508:5"]);
  assert.deepEqual(s.filterStudents(VIEW.students, {filter: "all", query: "roe"}).map(x => x.row), ["s508:5"]);
  const counts = s.filterCounts(VIEW.students);
  assert.equal(counts.all, 5);
  assert.equal(counts.missing, 1);
});

test("a student missing one part is 'Missing a grade' like one with none: the card's count, its filter and the chip agree (review F2)", () => {
  const partial = student("s507:6", "Part, Pat", {status: "missing_part", reason: "No bubble sheet: that part counts 0", bubble: {state: "none"},
    gradescope: {paper: true, grades: {sent: 12, changed: 0, not_sent: 0}, sheet: null, uncovered: false}});
  const view = {...VIEW, students: [...VIEW.students, partial], counts: {...VIEW.counts, missing: 2}};
  assert.deepEqual(s.filterStudents(view.students, {filter: "missing"}).map(x => x.row), ["s507:2", "s507:6"]);
  assert.equal(s.filterCounts(view.students).missing, Number(view.counts.missing));
  assert.match(s.statusChip(partial, h), /Part missing · counts 0/);
  assert.match(s.statusChip(partial, h), /data-tone="warning"/);
  assert.match(s.bandModel(view).body, /^1 student has no grade and 1 student is missing a part: you confirm each 0/);
});

test("Not on Gradescope reads the server's own per-student rule when it is sent (review F5)", () => {
  const waiting = student("s507:7", "Wait, Wes", {bubble: {state: "waiting"}, gradescope: {paper: true, grades: {sent: 12, changed: 0, not_sent: 0}, sheet: false, uncovered: true}});
  const covered = student("s507:8", "Done, Dee", {gradescope: {paper: true, grades: {sent: 12, changed: 0, not_sent: 0}, sheet: true, uncovered: false}});
  assert.deepEqual(s.filterStudents([waiting, covered], {filter: "gradescope"}).map(x => x.row), ["s507:7"]);
});

test("the student table escapes names, marks search matches and has no column for a part the assignment lacks", () => {
  const rows = [VIEW.students[0], {...VIEW.students[2], name: "<b>Carol</b>"}];
  const markup = s.studentTableMarkup(rows, h, {matches: [{row: "s508:3"}]});
  assert.ok(markup.includes("&lt;b&gt;Carol&lt;/b&gt;"));
  assert.match(markup, /<tr data-row="s508:3" data-cert-match>/);
  assert.match(markup, /Free response.*Multiple choice/);
  assert.match(markup, /31\.5 \/ 40/);
  const frqOnly = s.studentTableMarkup(rows, h, {parts: ["frq"]});
  assert.ok(!frqOnly.includes("Multiple choice"));
  assert.ok(!frqOnly.includes("52 / 60"));
  assert.ok(!/style=/.test(markup));
  assert.match(s.statusChip({status: "dr_ahead"}, h), /DR exam ahead/);
});

import { automaticMarkup, automaticSendsMarkup, leadText, nextText, pageMarkup, unpublishedMarkup } from "../static/grading-certification.js";

const steps = [{key: "review", label: "Review", done: true, destination: "#grading/p?tab=review"},
  {key: "export", label: "Certification and Export", title: "Certify the grades", done: false, current: true, detail: "", action: {label: "Certify grades", href: "#grading/p?tab=export"}, destination: "#grading/p?tab=export"}];
const state = (extra = {}) => ({tab: "questions", filter: "all", query: "", matches: null, searchNote: "", announce: "", sort: {key: "", dir: "asc"}, dist: "all", open: new Set(), ...extra});

test("the header sentence: students with every grade, or where the certification stands", () => {
  assert.equal(leadText(VIEW), "2 of 5 students have every grade. Check them here, then certify.");
  // Certified again with nothing to change wrote 0 cells: the lead counts the certification's own record (certify-4).
  const history = [{id: "c2", created_at: "2026-09-26T15:00:00Z", students: 340, scored: 312, zero: 28, excused: 0, left_out: 0, left_out_dr: 0, gradebook: {title: "Quiz 2", written: 0}}];
  assert.match(leadText({...VIEW, history, certification: {state: "current", id: "c2", created_at: "2026-09-26T15:00:00Z", gradebook: {title: "Quiz 2", written: 0}}}),
    /^Certified Sep 2\d · 340 students: 312 scored, 28 given 0\.$/);
  assert.match(leadText({...VIEW, history: [{...history[0], excused: 2, left_out: 3, left_out_dr: 1}], certification: {state: "current", id: "c2", created_at: "2026-09-26T15:00:00Z"}}),
    /^Certified Sep 2\d · 340 students: 312 scored, 28 given 0, 2 excused, 1 left out until their DR exam, 2 left out\.$/);
  assert.match(leadText({...VIEW, history: [{...history[0], left_out: 4, left_out_dr: 1, left_out_makeup: 3}], certification: {state: "current", id: "c2", created_at: "2026-09-26T15:00:00Z"}}),
    /^Certified Sep 2\d · 340 students: 312 scored, 28 given 0, 1 left out until their DR exam, 3 left out until their makeup\.$/);
  assert.match(leadText({...VIEW, certification: {state: "current", id: "gone", created_at: "2026-09-26T15:00:00Z"}}), /^Certified Sep 2\d · 5 students\.$/);
  // Changed: the band says what changed; the lead says what the gradebook keeps meanwhile (missed:certify[1]).
  assert.match(leadText({...VIEW, certification: {state: "changed", created_at: "2026-09-26T15:00:00Z", gradebook: {title: "Test 1"}}}),
    /^The gradebook column “Test 1” keeps the values certified Sep 2\d until you certify again\.$/);
  assert.match(leadText({...VIEW, certification: {state: "changed", created_at: "2026-09-26T15:00:00Z"}}), /^The gradebook keeps the values certified Sep 2\d until you certify again\.$/);
});

test("late papers Faculty certified by itself are named after the lead, and the history says who certified", () => {
  // LATE PAPERS CERTIFY THEMSELVES (2026-10-09): live markers read current; carried ones say the instructor's changes still wait.
  const history = [{id: "c3", by: "faculty", created_at: "2026-10-09T15:00:00Z", students: 3, scored: 3, zero: 0, excused: 0, left_out: 0, gradebook: {title: "Exam 1"}}];
  const automatic = markers => ({enabled: true, waiting: [], latest: {at: "2026-10-09T15:00:00Z", markers, students: [{row: "s:3", name: "Example, Carol", section: "501"}]}});
  const strip = markup => markup.replace(/<[^>]*>/g, "");
  assert.match(leadText({...VIEW, history, automatic: automatic("live"), certification: {state: "current", id: "c3", created_at: "2026-10-09T15:00:00Z"}}),
    /^Certified Oct 9 · 3 students: 3 scored, 0 given 0\.$/);
  assert.equal(strip(automaticMarkup({...VIEW, history, automatic: automatic("live")}, h)), " Faculty certified Example, Carol’s late paper by itself Oct 9.");
  assert.equal(strip(automaticMarkup({...VIEW, history, automatic: automatic("carried")}, h)),
    " Faculty certified Example, Carol’s late paper by itself Oct 9; your other changes wait for you to certify again.");
  const many = {...automatic("live"), latest: {...automatic("live").latest, students: [1, 2, 3].map(i => ({row: `s:${i}`, name: `Student ${i}`}))}};
  assert.match(automaticMarkup({...VIEW, history, automatic: many}, h), /Faculty certified <button[^>]*>3 late papers<\/button> by itself/);
  const page = pageMarkup({...VIEW, history, automatic: {...automatic("live"), waiting: [{row: "s:4", name: "Example, Dan", section: "501", reason: "Still flagged for review"}]},
    certification: {state: "current", id: "c3", created_at: "2026-10-09T15:00:00Z"}}, state(), steps, {...h, check: () => "", button: () => ""});
  assert.match(page, /<summary><span>Late papers waiting<\/span><span class="disclosure-hint">1<\/span><\/summary>/);
  assert.match(page, /<li><button type="button" class="text-button" data-cert-open="s:4">Example, Dan<\/button> <span class="certification-late-section">Section 501<\/span><span class="certification-late-reason">Still flagged for review<\/span><\/li>/);
  assert.match(page, /<th[^>]*>By<\/th>/);
  assert.match(page, /<td>Faculty<\/td>/);
});

test("what Faculty is about to certify by itself is said in place of asking for a Certify, and a 0 it gave is named", () => {
  // requirement, 2026-11-14: the page read "until you certify again" during the minutes Faculty waits for the work to settle, and a
  // makeup that ended asked for their Certify. GRADING "MAKEUP CHANGES CERTIFY THEMSELVES".
  const changed = {state: "changed", created_at: "2026-09-26T15:00:00Z", gradebook: {title: "Test 1"}};
  const carol = {row: "s:3", name: "Example, Carol", section: "501"}, dan = {row: "s:4", name: "Example, Dan", section: "501"}, erin = {row: "s:5", name: "Example, Erin", section: "501"};
  const next = more => ({enabled: true, waiting: [], latest: null, next: {papers: [], zero: [], left_out: [], rest: false, ...more}});
  assert.equal(leadText({...VIEW, certification: changed, automatic: next({papers: [carol]})}), "Faculty does this by itself in a few minutes: certifies Example, Carol’s late paper.");
  assert.equal(nextText({...VIEW, automatic: next({papers: [carol], zero: [dan], left_out: [erin]})}),
    "Faculty does this by itself in a few minutes: certifies Example, Carol’s late paper; gives Example, Dan 0 (makeup ended); leaves Example, Erin out until their makeup.");
  assert.equal(nextText({...VIEW, automatic: next({zero: [carol, dan, erin], wait: "Papers may still be in the Inbox"})}),
    "Faculty does this by itself once nothing blocks certifying: gives 3 students 0 (makeup ended).");
  assert.equal(leadText({...VIEW, certification: changed, automatic: next({papers: [carol], rest: true})}),
    "Faculty does this by itself in a few minutes: certifies Example, Carol’s late paper. Your other changes wait for you to certify again.");
  // Nothing of Faculty's, or the switch off: the lead asks as before.
  assert.match(leadText({...VIEW, certification: changed, automatic: next({})}), /until you certify again\.$/);
  assert.match(leadText({...VIEW, certification: changed, automatic: {...next({papers: [carol]}), enabled: false}}), /until you certify again\.$/);
  // Certify again… stays, but is the primary only when something waits for the instructor.
  const header = view => pageMarkup(view, state(), steps, {...h, check: () => "", button: () => ""}).match(/<button type="button" class="([^"]*)" data-cert-certify>Certify again…/)[1];
  assert.equal(header({...VIEW, certification: changed, automatic: next({papers: [carol]})}), "button");
  assert.equal(header({...VIEW, certification: changed, automatic: next({papers: [carol], rest: true})}), "button primary");
  assert.equal(header({...VIEW, certification: changed, automatic: next({})}), "button primary");
  // Afterwards: whom it gave 0 and whom it left out are named beside the late papers, and in the fold.
  const strip = markup => markup.replace(/<[^>]*>/g, "");
  const latest = {at: "2026-10-09T15:00:00Z", markers: "live", students: [carol, {...dan, change: "zero"}, {...erin, change: "left_out"}]};
  assert.equal(strip(automaticMarkup({...VIEW, automatic: {enabled: true, waiting: [], latest}}, h)),
    " Faculty certified Example, Carol’s late paper, gave Example, Dan 0 after their makeup ended, left Example, Erin out until their makeup by itself Oct 9.");
  assert.match(automaticMarkup({...VIEW, automatic: {enabled: true, waiting: [], latest: {...latest, students: [{...dan, change: "zero"}]}}}, h),
    /^ Faculty gave <button[^>]*data-cert-faculty-jump>Example, Dan<\/button> 0 after their makeup ended by itself Oct 9\.$/);
  const page = pageMarkup({...VIEW, automatic: {enabled: true, waiting: [], latest}, certification: {state: "current", id: "c3", created_at: "2026-10-09T15:00:00Z"}},
    state({open: new Set(["cert-fold-faculty"])}), steps, {...h, check: () => "", button: () => ""});
  assert.match(page, /data-cert-open="s:4">Example, Dan<\/button> <span class="certification-late-section">Section 501<\/span><span class="certification-late-reason">Given 0: their makeup ended<\/span>/);
  assert.match(page, /data-cert-open="s:5">Example, Erin<\/button>.*?Left out until their makeup<\/span>/);
});

test("late papers: the lead's who opens Certified by Faculty, each waiting student has their own line, nothing while the switch is off", () => {
  // 2026-10-09 review: the waiting line ignored the switch, joined every reason into one sentence and dropped the whole plan's reason;
  // past two names nothing said whom Faculty certified.
  const history = [{id: "c3", by: "faculty", created_at: "2026-10-09T15:00:00Z", students: 5, scored: 5, zero: 0, excused: 0, left_out: 0, gradebook: {title: "Exam 1"}}];
  const latest = {at: "2026-10-09T15:00:00Z", markers: "live", students: [{row: "s507:1", name: "Example, Alice", section: "507"}, {row: "s508:5", name: "Roe, Rick", section: "508"},
    {row: "s508:3", name: "Nguyen, Carol", section: "508"}]};
  const waiting = [{row: "s508:4", name: "Doe, Dana", section: "508", reason: "A line you added to every grade of Q3 is not on this paper; certify it yourself."},
    {row: "s507:2", name: "Émile Zola", section: "507", reason: "Still flagged for review"}];
  const view = s.markFaculty(structuredClone({...VIEW, history, automatic: {enabled: true, waiting, latest, reason: "The assignment's graded parts changed since it was certified: certify it again."},
    certification: {state: "current", id: "c3", created_at: "2026-10-09T15:00:00Z"}}));
  const page = pageMarkup(view, state(), steps, {...h, check: () => "", button: () => ""});
  // The lead's text is escaped as before; its "who" is a text button that opens the Students tab on the new filter.
  assert.match(page, /<p data-step-lead>Certified Oct 9 · 5 students: 5 scored, 0 given 0\. Faculty certified <button type="button" class="text-button" data-cert-jump="students:faculty" data-cert-faculty-jump>3 late papers<\/button> by itself Oct 9\.<\/p>/);
  assert.match(page, /data-cert-late-reason>Faculty waits for you to certify again: The assignment's graded parts changed/);
  assert.equal((page.match(/certification-late-reason/g) || []).length, 2);  // one line per student, each with their own reason
  assert.match(page, /Doe, Dana<\/button> <span class="certification-late-section">Section 508<\/span><span class="certification-late-reason">A line you added to every grade of Q3/);
  assert.match(page, /<summary><span>Certified by Faculty<\/span><span class="disclosure-hint">3<\/span><\/summary>.*data-cert-open="s508:3">Nguyen, Carol<\/button>/s);
  // The switch off: Faculty certifies nothing, so nothing says it waits.
  const off = pageMarkup({...view, automatic: {...view.automatic, enabled: false}}, state(), steps, {...h, check: () => "", button: () => ""});
  assert.doesNotMatch(off, /Late papers waiting|Faculty waits for you/);
  // The filter: only the students Faculty certified, a chip only when it has someone, and a "By Faculty" tag on their rows.
  assert.deepEqual(s.filterStudents(view.students, {filter: "faculty"}).map(x => x.row), ["s507:1", "s508:3", "s508:5"]);
  assert.equal(s.filterCounts(VIEW.students).faculty, 0);
  const students = pageMarkup(view, state({tab: "students", filter: "faculty"}), steps, h);
  assert.match(students, /data-filter="faculty">Certified by Faculty <b>3<\/b>/);
  assert.match(students, /<tr data-row="s507:1">.*?data-cert-by-faculty>By Faculty<\/span><\/th>/);
  assert.doesNotMatch(pageMarkup(VIEW, state({tab: "students"}), steps, h), /data-filter="faculty"|By Faculty/);
  // Two names or fewer are the button themselves.
  const two = {...view, automatic: {...view.automatic, latest: {...latest, students: latest.students.slice(0, 1)}}};
  assert.match(automaticMarkup(two, h), /data-cert-jump="students:faculty" data-cert-faculty-jump>Example, Alice’s late paper<\/button> by itself/);
});

test("Faculty's own Gradescope send of a late paper: the student's cell, the fold and the drawer say waiting or not sent, and why", () => {
  // 2026-10-09 review: the reasons were recorded (frq_gradescope_automatic_sends.error) and never shown.
  const waiting = student("s507:9", "Wait, Wes", {gradescope: {paper: false, grades: {sent: 0, changed: 0, not_sent: 2}, sheet: null, uncovered: true,
    automatic: {kind: "written", status: "waiting", reason: "Waiting: Gradescope is busy; Faculty tries again in 60 s."}}});
  const skipped = student("s507:10", "Skip, Sam", {gradescope: {paper: false, grades: {sent: 0, changed: 0, not_sent: 2}, sheet: null, uncovered: true,
    automatic: {kind: "written", status: "skipped", reason: "Exam 1 has no version E."}}});
  const row = s.studentRowMarkup(waiting, h);
  assert.match(row, /data-tone="info" data-cert-automatic-chip title="Faculty sends this by itself · Waiting: Gradescope is busy; Faculty tries again in 60 s\.">Faculty sends<\/span>/);
  assert.match(s.studentRowMarkup(skipped, h), /data-tone="warning" data-cert-automatic-chip title="Faculty did not send it: Exam 1 has no version E\.">Not sent<\/span><small class="certification-note">Faculty did not send it: Exam 1 has no version E\.<\/small>/);
  const fold = automaticSendsMarkup({students: [VIEW.students[0], waiting, skipped]}, h);
  assert.match(fold, /Wait, Wes<\/button> <span>Faculty sends this by itself · Waiting: Gradescope is busy/);
  assert.match(fold, /Skip, Sam<\/button> <span>Faculty did not send it: Exam 1 has no version E\.<\/span> · <button type="button" class="text-button" data-cert-send="written">Send to Gradescope…<\/button>/);
  assert.equal(automaticSendsMarkup(VIEW, h), "");
  const drawer = drawerMarkup({student: {name: "Skip, Sam", section: "507"}, questions: []}, {row: {...skipped, by_faculty: true}, plan: {}, view: VIEW}, h);
  assert.match(drawer, /data-cert-by-faculty>By Faculty/);
  assert.match(drawer, /data-cert-drawer-automatic>Faculty did not send it: Exam 1 has no version E\./);
  // A version a late paper went to while unpublished on a published set: the band names the header's own Publish, in words (one
  // place to publish, 2026-10-18 review item 10), never a second button.
  assert.equal(unpublishedMarkup(VIEW, h), "");
  const band = unpublishedMarkup({...VIEW, gradescope: {...VIEW.gradescope, unpublished_versions: ["E"]}}, h);
  assert.match(band, /Version E is not published on Gradescope: a late paper Faculty sent there by itself is not visible to the student yet · Use Publish grades on Gradescope… at the top of Certify<\/p>/);
  assert.doesNotMatch(band, /<button/);
});

test("certified at 0 while the certification is current: a neutral chip and card, the confirmations said as given (certify-6)", () => {
  const zero = student("s507:2", "Émile Zola", {status: "missing", reason: "No paper scanned", frq: {state: "none"}, bubble: {state: "none"}, total: null, certified: {status: "zero", earned: "0"}});
  const view = {...VIEW, students: [VIEW.students[0], zero], counts: {...VIEW.counts, missing: 1}, history: [],
    certification: {state: "current", id: "c1", created_at: "2026-09-26T15:00:00Z", gradebook: {title: "Test 1"}}};
  assert.match(s.statusChip(zero, h, view), /data-tone="neutral"[^>]*>Certified 0</);
  assert.match(s.statusChip(zero, h, {...view, certification: {state: "changed"}}), /data-tone="warning"[^>]*>Missing a grade</);
  assert.match(s.statusChip({...zero, changed_since: true}, h, view), />Missing a grade</);
  const card = s.cardsModel(view).find(c => c.key === "missing");
  assert.deepEqual([card.label, Boolean(card.attention)], ["Certified 0 · no work", false]);
  assert.equal(s.cardsModel({...view, certification: {state: "none"}}).find(c => c.key === "missing").label, "Missing a grade");
  const markup = pageMarkup(view, state({tab: "students", filter: "missing"}), steps, h);
  assert.match(markup, /<tr data-row="s507:2">.*>Certified 0<.*Excuse…/);
  // The filter the card opens says the same (review nit); with a student still missing a grade it is "Missing a grade".
  assert.match(markup, /data-filter="missing">Certified 0 <b>1<\/b>/);
  assert.equal(s.missingCertifiedZero(view), true);
  const still = {...view, students: [...view.students, {...zero, row: "s508:9", certified: null}], counts: {...view.counts, missing: 2}};
  assert.equal(s.missingCertifiedZero(still), false);
  assert.match(pageMarkup(still, state({tab: "students", filter: "missing"}), steps, h), /data-filter="missing">Missing a grade <b>2<\/b>/);
  assert.match(markup, /Confirmed when certified Sep 2\d/);
  assert.doesNotMatch(markup, /to confirm/);
  assert.match(markup, /What certifying changes/);
  assert.match(markup, /grading card and Exams page row into the Archived section of their pages\. Its flagged seating charts, Response Card issues and failed print jobs no longer ask for your attention\./);
});

test("multiple choice only: the Questions tab is the multiple-choice table and no card asks about flags (certify-19)", () => {
  const view = {...VIEW, parts: ["bubble"], questions: [], bubble_questions: {A: [{number: 1, correct_pct: 91.2, common_wrong: "C"}, {number: 2, correct_pct: 50, common_wrong: "A"}]}};
  assert.deepEqual(s.cardsModel(view).map(c => c.key), ["complete", "mean", "missing", "gradescope"]);
  const markup = pageMarkup(view, state(), steps, h);
  assert.match(markup, /data-filter="questions">Questions <b>2<\/b>/);
  assert.match(markup, /<th scope="row">1<\/th><td>91%<\/td>/);
  assert.doesNotMatch(markup, /No question has a final grade yet|Multiple choice per question/);
  // Both parts: unchanged.
  assert.match(pageMarkup(VIEW, state(), steps, h), /Multiple choice per question/);
});

test("the student filters show those with someone in them, All, and the one chosen (certify-12)", () => {
  const markup = pageMarkup(VIEW, state({tab: "students"}), steps, h);
  const offered = [...markup.matchAll(/data-filter-group="cert-students".*?<\/div>/gs)][0][0].match(/data-filter="([a-z_]+)"/g).map(x => x.slice(13, -1));
  assert.deepEqual(offered, ["all", "missing", "in_progress", "flagged", "zero", "changed", "dr_excused", "gradescope"]);
  const few = {...VIEW, students: [VIEW.students[0]]};
  const shown = filter => [...pageMarkup(few, state({tab: "students", filter}), steps, h).matchAll(/data-filter-group="cert-students".*?<\/div>/gs)][0][0].match(/data-filter="([a-z_]+)"/g).map(x => x.slice(13, -1));
  assert.deepEqual(shown("all"), ["all"]);
  assert.deepEqual(shown("missing"), ["all", "missing"], "a card's empty filter stays pressed and shown");
});

test("the 'Students with every grade' card opens a filter whose chip is shown and pressed, and the page without it is unchanged (certify-12)", () => {
  assert.equal(s.cardsModel(VIEW).find(c => c.key === "complete").jump, "students:complete");
  const group = markup => [...markup.matchAll(/data-filter-group="cert-students".*?<\/div>/gs)][0][0];
  const chips = markup => group(markup).match(/data-filter="([a-z_]+)"/g).map(x => x.slice(13, -1));
  const opened = group(pageMarkup(VIEW, state({tab: "students", filter: "complete"}), steps, h));
  assert.match(opened, /aria-pressed="true" data-filter="complete">Complete <b>2<\/b>/);
  assert.equal([...opened.matchAll(/aria-pressed="true"/g)].length, 1, "exactly one chip is pressed");
  assert.deepEqual(chips(pageMarkup(VIEW, state({tab: "students", filter: "all"}), steps, h)), ["all", "missing", "in_progress", "flagged", "zero", "changed", "dr_excused", "gradescope"], "no Complete chip unless the card chose it");
});

test("no complete total: the distribution says so once (certify-17)", () => {
  const markup = pageMarkup({...VIEW, stats: {...VIEW.stats, total: {n: 0, histogram: []}}}, state(), steps, h);
  assert.equal((markup.match(/No complete total yet/g) || []).length, 1);
});

test("the page: one header with ONE primary, a one-line band whose Details hold the checks, cards, then Questions | Students with the search", () => {
  const markup = pageMarkup(VIEW, state(), steps, h);
  assert.match(markup, /<h3>Certification and Export<\/h3>/);
  // The student search sits in the panel's top row beside Questions | Students, not in the header (requirement, 2026-09-26).
  assert.equal((markup.match(/data-cert-search /g) || []).length, 1);
  assert.ok(markup.indexOf("data-cert-search ") > markup.indexOf('id="cert-panel"'));
  assert.match(markup, /<div class="certification-panel-top">[\s\S]*<input type="search" data-page-search data-cert-search placeholder="Search students: name, section or UIN" autocomplete/);
  // Typed text (UIN digits included) is never written into the markup.
  assert.doesNotMatch(pageMarkup(VIEW, {...state(), query: "123456"}, steps, h), /123456/);
  assert.equal((markup.match(/class="button primary"/g) || []).length, 1);
  assert.match(markup, /<button type="button" class="button primary" data-cert-certify>Certify grades…<\/button>/);
  assert.ok(markup.indexOf("data-cert-band") < markup.indexOf("certification-cards"));
  assert.ok(markup.indexOf("certification-cards") < markup.indexOf('id="cert-panel"'));
  // No painting beside the summary since 2026-10-04 (instructor: "remove paintings"); the distribution follows the counts.
  assert.doesNotMatch(markup, /painting|harvest/i);
  assert.match(markup, /<div class="certification-summary"><div class="certification-summary-main"><div class="certification-band" data-cert-band>/);
  assert.ok(markup.indexOf("certification-cards") < markup.indexOf("certification-distribution"));
  assert.match(markup, /<details class="disclosure certification-band-details" id="cert-fold-details">/);
  assert.match(markup, /You confirm these when you certify/);
  assert.match(markup, /Certify grades… checks everything again/);
  assert.match(markup, /data-filter-group="cert-panel"/);
  assert.match(markup, /data-question="q1"/);
  assert.ok(!/style=/.test(markup), "no inline style attribute (CSP)");
  // The Students tab, filtered to the missing: the rows carry their keys for patching, never a UIN.
  const students = pageMarkup(VIEW, state({tab: "students", filter: "missing"}), steps, h);
  assert.match(students, /<tr data-row="s507:2">/);
  assert.ok(!students.includes('data-row="s507:1"'));
  assert.match(students, /data-filter-group="cert-students"/);
  // Certified: no primary, a chip instead; changed: Certify again.
  const certified = pageMarkup({...VIEW, certification: {state: "current", created_at: "2026-09-26T15:00:00Z"}}, state(), steps, h);
  assert.equal((certified.match(/class="button primary"/g) || []).length, 0);
  assert.match(certified, /data-cert-certified>Certified</);
  assert.match(pageMarkup({...VIEW, certification: {state: "changed", created_at: "2026-09-26T15:00:00Z", changed: ["grades"]}}, state(), steps, h), /data-cert-certify>Certify again…</);
  // A disclosure the viewer opened stays open across a redraw (state, not the markup's default).
  assert.match(pageMarkup(VIEW, state({open: new Set(["cert-fold-details"])}), steps, h), /id="cert-fold-details" open>/);
});

// ------------------------------------------------------------------------------------------------------------------------------
// B2: the question panel, the drawer's pure pieces, the Gradescope hand-off and the Certify dialog's request.
import * as q from "../static/grading-certification-question.js";
import { splitRow, studentUrl, neighbour, flagsMarkup, drawerMarkup, responseMarkup } from "../static/grading-certification-students.js";
import { handoffMarkup, topMarkup } from "../static/grading-certification.js";
import * as d from "../static/grading-certification-dialog.js";

const QUESTION = {question: {question_id: "q20", title: "Question 20", version: "A", max: "10",
  rubric: [{id: "R3_1", number: "3.1", description: "Correct answer: $$x=6$$", points: 10}, {id: "R3_2", number: "3.2", description: "Set up <the> equation", points: 4}],
  stats: {n: 4, mean: 6, median: 4, sd: 3.3, min: 4, max: 10, histogram: [{score: "4", count: 3}, {score: "10", count: 1}]}},
  lines: [{id: "R3_1", count: 1, pct: 25}, {id: "R3_2", count: 3, pct: 75}],
  responses: [
    {response_id: "r1", row: "s:1", name: "Zed, Ann", section: "501", score: "10", decided: {kind: "automatic", basis: "final_arbitrator"}, flagged: true, flags: ["arbitrator"], overridden: false, selected: ["R3_1"], gradescope: "sent", answer_group: "g1"},
    {response_id: "r2", row: "s:2", name: "Able, Bo", section: "502", score: "4", decided: {kind: "instructor", status: "approved"}, flagged: false, flags: [], overridden: true, selected: ["R3_2"], gradescope: "changed", answer_group: "g1"},
    {response_id: "r3", row: "s:3", name: "Mid, Cy", section: "501", score: "0", decided: {kind: "instructor", status: "manual"}, flagged: false, flags: [], overridden: true, selected: [], gradescope: "not_sent"},
    {response_id: "r4", row: "s:4", name: "Low, Di", section: "501", score: null, decided: {kind: "none"}, flagged: true, flags: ["reading"], overridden: false, selected: [], gradescope: "none"},
  ]};

test("question responses: every filter reads the server's facts; answer groups filter only when the server names them", () => {
  const rows = filter => q.filterResponses(QUESTION, {filter, groups: ["g1"]}).map(r => r.response_id);
  assert.deepEqual(rows("zero"), ["r3"]);
  assert.deepEqual(rows("full"), ["r1"]);
  assert.deepEqual(rows("automatic"), ["r1"]);
  assert.deepEqual(rows("instructor"), ["r2", "r3"]);
  assert.deepEqual(rows("flagged"), ["r4", "r1"]);
  assert.deepEqual(rows("overridden"), ["r2", "r3"]);
  assert.deepEqual(rows("not_final"), ["r4"]);
  assert.deepEqual(rows("gradescope"), ["r2", "r3"]);
  assert.deepEqual(rows("inconsistent"), ["r2", "r1"]);
  assert.deepEqual(q.filterResponses(QUESTION, {filter: "inconsistent", groups: []}), []);
  // Filters with nothing are not offered (All always is, and one a link asked for).
  const offered = q.responseFilterCounts(QUESTION, [], "inconsistent").map(f => f.value);
  assert.ok(offered.includes("all") && offered.includes("inconsistent") && offered.includes("zero"));
  assert.ok(!q.responseFilterCounts({...QUESTION, responses: []}).some(f => f.value === "zero"));
});

test("question responses sort by name, score either way (no score last) or section", () => {
  assert.deepEqual(q.sortResponses(QUESTION.responses, "name").map(r => r.response_id), ["r2", "r4", "r3", "r1"]);
  assert.deepEqual(q.sortResponses(QUESTION.responses, "score_desc").map(r => r.response_id), ["r1", "r2", "r3", "r4"]);
  assert.deepEqual(q.sortResponses(QUESTION.responses, "score_asc").map(r => r.response_id), ["r3", "r2", "r1", "r4"]);
  assert.deepEqual(q.sortResponses(QUESTION.responses, "section").map(r => r.response_id), ["r4", "r3", "r1", "r2"]);
});

test("the question panel: rubric in its original wording with line frequencies, links to the rubric steps, names hidden on request", () => {
  const markup = q.questionPanelMarkup(QUESTION, {filter: "all", sort: "name", hideNames: false, groups: []}, {id: "p"}, h);
  assert.match(markup, /data-rubric-line="R3_2"><th scope="row">3\.2<\/th><td><span class="grading-math" data-math>Set up &lt;the&gt; equation/);
  assert.match(markup, /data-share="75"/);
  assert.match(markup, /href="#grading\/p\?tab=answer-key&amp;focus=q20"/);
  assert.match(markup, /href="#grading\/p\?tab=rubrics&amp;focus=q20"/);
  assert.match(markup, /Automatic · arbitrator/);
  assert.match(markup, /Zed, Ann/);
  assert.equal((markup.match(/<rect /g) || []).length, 2, "one bar per distinct final score");
  assert.ok(!/style=/.test(markup));
  const hidden = q.questionPanelMarkup(QUESTION, {filter: "all", sort: "name", hideNames: true, groups: []}, {id: "p"}, h);
  assert.ok(!hidden.includes("Zed, Ann") && hidden.includes("Response 1"));
});

test("drawer pieces: row keys split at the last colon, go only into API paths, and Alt+J/K walk the filtered order", () => {
  assert.deepEqual(splitRow("sec-1:0123456789"), {section: "sec-1", enrollment: "0123456789"});
  assert.equal(splitRow("nothing"), null);
  assert.equal(studentUrl("/api/x/certification", "a b:1"), "/api/x/certification/students/a%20b/1");
  assert.equal(neighbour(["a", "b", "c"], "b", 1), "c");
  assert.equal(neighbour(["a", "b", "c"], "c", 1), null);
  assert.equal(neighbour(["a", "b", "c"], "gone", -1), "c");
});

test("the drawer folds the models' original flags under one line and offers Excuse only for a student with no work", () => {
  // Still flagged: the fold opens with the needs-you mark; every flag stays, by who raised it, each as its first sentence.
  const raw = {names: ["arbitrator", "disagreement", "waived"], original_state: "grading_review", base_state: "proposed", reasons: ["Graders <disagreed>. Line 2 or 3."], risk_blocks: [],
    risk_waived: {blocks: ["Low reading confidence"]}, arbiter: {outcome: "adopted", reason: "Primary is right", flags: [{id: "f1", text: "Line 3", resolved: true}, {id: "f2", text: "Line 4", resolved: false}]},
    committee: {status: "unresolved", reason: "", review_reasons: []}};
  const flags = flagsMarkup(raw, h);
  assert.match(flags, /<details class="disclosure certification-fold certification-flags" data-cert-flags open><summary><span class="certification-flag-standing">Still flagged: graders disagreed \+1<\/span>/);
  assert.match(flags, /<dt>When proposed<\/dt><dd>Held for grading review<\/dd>/);
  assert.match(flags, /<dt>Reasons the models gave<\/dt><dd><ul><li><span class="grading-math" data-math>Graders &lt;disagreed&gt;\.<\/span><details class="certification-more"><summary>Full note<\/summary>/);
  assert.match(flags, /<dt>Grading team<\/dt><dd>Disagreed\./);
  assert.match(flags, /<dt>Final arbitrator<\/dt><dd>Adopted an earlier assessment: <span class="grading-math" data-math>Primary is right<\/span>/);
  assert.match(flags, /data-resolved="yes">Resolved<\/span> <span class="grading-math" data-math>Line 3/);
  assert.match(flags, /data-resolved="no">Still stands<\/span> <span class="grading-math" data-math>Line 4/);
  assert.match(flags, /<dt>Waived by your review policy<\/dt><dd><span class="certification-quiet">Recorded, not holding it back\.<\/span><ul><li><span class="grading-math" data-math>Low reading confidence/);
  // Settled by a reviewing model: closed, saying who; only checks the policy waives: closed, counted; no flag: nothing.
  const settled = flagsMarkup({names: ["flag_reviewer"], settled: true, flag_review: {outcome: "finalized", reason: "The image supports R6. The rest is right.", flags: [{text: "The grader was uncertain.", resolved: true}]}}, h);
  assert.match(settled, /data-cert-flags><summary><span>Flags: upheld by the flag reviewer<\/span>/);
  assert.match(settled, /<dt>Flag reviewer<\/dt><dd>Upheld the grade: <span class="grading-math" data-math>The image supports R6\.<\/span><details class="certification-more">/);
  assert.match(flagsMarkup({names: [], risk_waived: {direct_reading: {blocks: ["No independent OCR reading"]}}}, h), /data-cert-flags><summary><span>Checks waived by your policy \(1\)<\/span>/);
  assert.equal(flagsMarkup({names: []}, h), "");
  assert.equal(flagsMarkup(null, h), "");
  // One chip, in plain words, the most telling flag first; every flag in its title (review UX6).
  const chip = q.flagChips(["reasons", "risk", "disagreement"], h);
  assert.equal((chip.match(/class="chip"/g) || []).length, 1);
  assert.match(chip, />Flagged: graders disagreed \+2</);
  assert.match(chip, /title="Flagged by the models: graders disagreed; risk checks held it; models gave review reasons"/);
  assert.equal(q.flagChips([], h), "");
  const detail = {student: {row: "s:2", name: "Émile <Z>", section: "507", exam_version: "A", collection_issues: []}, parts: {frq: {final: 0, expected: 2, earned: null, possible: "18"}, bubble: null},
    questions: [{question_id: "q1", title: "Question 1", version: "A", max: "10", response_id: null, score: null, decided: {kind: "none"}, flags: {names: []}, missing_reason: "No paper was received", gradescope: "none"}], dr: [], certified: null};
  const missing = drawerMarkup(detail, {row: {row: "s:2", status: "missing", reason: "No paper scanned"}, plan: {id: "p"}, view: {event_id: "e1"}}, h);
  assert.match(missing, /Émile &lt;Z&gt;/);
  assert.match(missing, /data-cert-drawer-excuse/);
  assert.match(missing, /href="#exams\/e1\?tab=collection"/);
  assert.match(missing, /No paper was received/);
  assert.ok(!drawerMarkup(detail, {row: {row: "s:2", status: "complete"}, plan: {id: "p"}}, h).includes("data-cert-drawer-excuse"));
  assert.match(drawerMarkup(detail, {row: {row: "s:2", status: "complete"}, plan: {id: "p"}, anonymous: true}, h), /Student \(name hidden\)/);
  assert.ok(!/style=/.test(missing));
});

test("a Gradescope send followed from the dialog: progress while it runs, Check again once it settled", () => {
  assert.equal(handoffMarkup(null, h), "");
  const live = handoffMarkup({kind: "written", send: {status: "running", counts: {uploads: {accepted: 1, pending: 1}, grades: {pending: 4}}}}, h);
  assert.match(live, /Sending papers and grades to Gradescope…/);
  assert.match(live, /1<\/b> of 2 papers finished/);
  const done = handoffMarkup({kind: "sheets", send: {status: "complete", counts: {}}}, h);
  assert.match(done, /The send to Gradescope finished/);
  assert.match(done, /data-cert-certify=""/);
  assert.match(topMarkup(VIEW, state({handoff: {kind: "written", send: null}}), steps, h), /data-cert-handoff="live"/);
});

const PREVIEW = {fingerprint: "fp", blocking: 0, snapshot: {students: 3, scored: 2, zero: 1, excused: 0, left_out: 0},
  checks: [], confirm: [
    {id: "zero_no_work", level: "confirm", title: "1 student with no work receives 0", count: 1, digest: "d-zero", requires: "tick", rows: [{row: "s:3", name: "Carol", section: "501", detail: "No paper scanned"}]},
    {id: "gradescope_written", level: "confirm", title: "Written grades not on Gradescope", count: 2, requires: "decision", prefill: "Gradescope is not set up.", action: {label: "Send to Gradescope…", kind: "send_written"}, sendable: true},
    {id: "gradebook_column", level: "confirm", title: "Create the gradebook column “Test 1”", count: 1, digest: "d-col"}],
  gradebook: {selected: "create", warnings: ["w"], options: [
    {key: "create", column: {action: "create"}, item: {id: null, title: "Test 1", points: "18", category_id: null}, counts: {add: 3},
      checks: [{id: "gradebook_column", level: "confirm", title: "Create the gradebook column “Test 1”", count: 1, digest: "d-col"}]},
    {key: "use:i1", column: {action: "use", item_id: "i1"}, item: {id: "i1", title: "Hand Test 1", points: "18", category_id: "c1"}, counts: {add: 1, kept: 1},
      checks: [{id: "gradebook_column", level: "confirm", title: "Record in the column “Hand Test 1”", count: 1, digest: "d-use"},
        {id: "gradebook_kept", level: "confirm", title: "1 value you typed in the gradebook stays", count: 1, digest: "d-kept", rows: [{row: "s:1", name: "Alice", section: "501", detail: "typed 9 · certified 10"}]},
        {id: "gradebook_imported_replaced", level: "info", title: "1 imported value replaced", rows: []}]},
  ]}, changes: []};

test("certify: every group is needed and no final tick; a column switch asks for that column's own answers", () => {
  const create = d.chosenOption(PREVIEW), use = d.chosenOption(PREVIEW, "use:i1");
  assert.equal(create.key, "create");
  assert.deepEqual(d.confirmGroups(PREVIEW, use).map(c => c.id), ["zero_no_work", "gradescope_written", "gradebook_column", "gradebook_kept"]);
  assert.equal(d.confirmGroups(PREVIEW, use).find(c => c.id === "gradebook_column").digest, "d-use");
  const answers = {ticks: new Set(["zero_no_work:d-zero", "gradebook_column:d-col"]), decisions: {written: {skip: true, reason: "Not set up"}}};
  assert.deepEqual(d.missingAnswers(PREVIEW, create, answers), []);
  // No final tick (requirement, 2026-10-11): the groups answered, Certify is the confirmation.
  assert.deepEqual(d.missingAnswers(PREVIEW, create, {...answers, final: false}), []);
  assert.deepEqual(d.missingAnswers(PREVIEW, create, {...answers, decisions: {written: {skip: true, reason: "  "}}}), ["a reason for not sending (Written grades not on Gradescope)"]);
  assert.deepEqual(d.missingAnswers(PREVIEW, create, {...answers, decisions: {written: {skip: true, reason: "x".repeat(501)}}}), ["a shorter reason, at most 500 characters (Written grades not on Gradescope)"]);
  assert.deepEqual(d.missingAnswers(PREVIEW, create, {...answers, decisions: {}}), ["Written grades not on Gradescope"]);
  assert.deepEqual(d.missingAnswers(PREVIEW, use, answers), ["Record in the column “Hand Test 1”", "1 value you typed in the gradebook stays"]);
  assert.deepEqual(d.missingAnswers({...PREVIEW, blocking: 1}, create, answers), ["what blocks certification"]);
  // A tick given for an older digest does not count.
  assert.deepEqual(d.missingAnswers(PREVIEW, create, {...answers, ticks: new Set(["zero_no_work:old", "gradebook_column:d-col"])}), ["1 student with no work receives 0"]);
});

test("certify: the request body names each current digest, the do-not-send reasons, the chosen column and only kept rows to replace", () => {
  const use = d.chosenOption(PREVIEW, "use:i1");
  const body = d.certifyBody(PREVIEW, use, {ticks: new Set(["zero_no_work:d-zero", "gradebook_column:d-use", "gradebook_kept:d-kept"]),
    decisions: {written: {skip: true, reason: "  Not set up  "}}, replace: new Set(["s:1", "s:9"]), requestId: "rid"});
  assert.deepEqual(body, {request_id: "rid", fingerprint: "fp", confirmation: "certify",
    acknowledged: {zero_no_work: "d-zero", gradebook_column: "d-use", gradebook_kept: "d-kept"},
    gradescope: {written: {decision: "do_not_send", reason: "Not set up"}},
    gradebook: {replace: ["s:1"], category_id: "c1", column: {action: "use", item_id: "i1"}}});
  // A new column carries the category chosen in the dialog, else the server's proposal (rest-shell-1); null is Uncategorized.
  const create = d.chosenOption(PREVIEW), answers = {ticks: new Set(), decisions: {}, replace: new Set(), requestId: "rid"};
  assert.equal(d.certifyBody(PREVIEW, create, answers).gradebook.category_id, null);
  const proposing = {...PREVIEW, gradebook: {...PREVIEW.gradebook, proposed_category_id: "c1"}};
  assert.equal(d.certifyBody(proposing, create, answers).gradebook.category_id, "c1");
  assert.equal(d.certifyBody(proposing, create, {...answers, category: "c2"}).gradebook.category_id, "c2");
  assert.equal(d.certifyBody(proposing, create, {...answers, category: null}).gradebook.category_id, null, "Uncategorized chosen over the proposal");
  assert.equal(d.certifyBody(proposing, use, {...answers, category: "c2"}).gradebook.category_id, "c1", "an existing column keeps its own");
  assert.equal(d.tickLabel({id: "zero_no_work", count: 2}), "These 2 students receive 0");
  assert.equal(d.tickLabel({id: "zero_no_work", count: 1}), "This student receives 0");
  assert.equal(d.tickLabel({id: "makeup_ahead", count: 3}), "Leave these 3 students out until their makeup");
  assert.equal(d.optionLabel(use), "Record in the column “Hand Test 1”");
});

test("the Certify dialog's markup: one tick per group, the prefilled reason, the Send hand-off, and blocks alone when anything blocks", () => {
  const markup = d.previewMarkup(PREVIEW, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.match(markup, /3 students: 2 scored, 1 receive 0/);
  // MAKEUP OWED (2026-10-07): students owed a makeup are named apart, never counted as one of two memberships.
  const owed = d.previewMarkup({...PREVIEW, snapshot: {...PREVIEW.snapshot, students: 6, left_out: 3, left_out_dr: 1, left_out_makeup: 2}},
                               {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.match(owed, /0 excused, 1 left out until their DR exam, 2 until their makeup\./);
  assert.doesNotMatch(owed, /in two sections counted once/);
  assert.match(markup, /data-certify-tick="zero_no_work:d-zero"/);
  assert.match(markup, /This student receives 0/);
  assert.match(markup, /data-certify-send="written"/);
  assert.match(markup, /<textarea data-certify-reason="written"[^>]*disabled>Gradescope is not set up\.<\/textarea>/);
  assert.match(markup, /data-certify-excuse="s:3"/);
  assert.equal((markup.match(/data-certify-tick="gradebook_column:/g) || []).length, 1, "the column is confirmed once, in the gradebook section");
  const blocked = d.previewMarkup({...PREVIEW, blocking: 1, checks: [{id: "grading_unfinished", level: "block", title: "Grading is not finished", action: {label: "Review grades", href: "#grading/p?tab=review"}}]},
    {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.match(blocked, /Finish these first/);
  assert.ok(!blocked.includes("data-certify-tick"));
  assert.ok(!/style=/.test(markup));
  // Counted once: a title that starts with its count is not followed by it again (certify-13).
  assert.match(markup, /<legend>1 student with no work receives 0<\/legend>/);
  assert.match(markup, /<legend>Written grades not on Gradescope \(2\)<\/legend>/);
  assert.match(markup, /<legend>Create the gradebook column “Test 1”<\/legend>/);
  // Grades and papers counted over student rows: the two students are the whole list, not "the first 2 of 6" (review).
  const written = {...PREVIEW, confirm: PREVIEW.confirm.map(c => c.id === "gradescope_written" ? {...c, count: 6, rows: [{row: "s:1", name: "Alice", section: "501"}, {row: "s:2", name: "Bob", section: "501"}]} : c)};
  const listed = d.previewMarkup(written, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.match(listed, /data-certify-student="s:2"/);
  assert.doesNotMatch(listed, /The first \d+ of/);
  // What certifying changes besides the gradebook, folded at the end (certify-10).
  assert.match(markup, /<details class="disclosure" id="certify-what"><summary><span>What certifying changes<\/span>/);
  assert.match(markup, /Nothing is deleted, and every record still opens\./);
});

// The students who receive 0 show the absences recorded for them beside Excuse… (requirement, 2026-10-02: display only).
test("absences beside zeros: each recorded absence with its decision, marked when it covers the day it was given; nothing else changes", () => {
  const absences = [{first_date: "2026-09-25", last_date: "2026-09-25", dates: "Fri, Sep 25", status: "not_excused", covers: true},
    {first_date: "2026-09-24", last_date: "2026-09-26", dates: "Thu, Sep 24 – Sat, Sep 26", status: "pending", covers: true},
    {first_date: "2026-09-01", last_date: "2026-09-02", dates: "Tue, Sep 1 – Wed, Sep 2", status: "excused", covers: false},
    {first_date: "2026-09-08", last_date: "2026-09-08", dates: "Tue, Sep 8", status: "withdrawn"}];
  const zero = {...PREVIEW.confirm[0], count: 2, rows: [{row: "s:3", name: "Carol", section: "501", detail: "No paper scanned", absences},
    {row: "s:4", name: "Dan", section: "501", detail: "No paper scanned", absences: []}]};
  const ahead = {id: "dr_ahead", level: "confirm", title: "1 DR student left out until their exam", count: 1, digest: "d-dr", requires: "tick",
    rows: [{row: "s:5", name: "Eve", section: "501", detail: "DR exam on 2099-01-05", absences}]};
  const preview = {...PREVIEW, confirm: [zero, ahead, ...PREVIEW.confirm.slice(1)]};
  const markup = d.previewMarkup(preview, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  const carol = markup.match(/<tr data-certify-row="s:3">.*?<\/tr>/s)[0];
  assert.match(carol, /No paper scanned <button type="button" class="text-button" data-certify-excuse="s:3">Excuse…<\/button><ul class="certification-absences" data-certify-absences>/);
  assert.match(carol, /<li>Absence recorded Fri, Sep 25 · not excused <span class="chip" data-tone="neutral" data-certify-absence-covers>Covers the day it was given<\/span><\/li>/);
  assert.match(carol, /<li>Absence recorded Thu, Sep 24 – Sat, Sep 26 · waiting for your decision <span class="chip" data-tone="warning" data-certify-absence-covers>Covers the day it was given<\/span><\/li>/);
  assert.match(carol, /<li>Absence recorded Tue, Sep 1 – Wed, Sep 2 · excused<\/li>/);
  assert.match(carol, /<li>Absence recorded Tue, Sep 8 · withdrawn<\/li>/);
  assert.doesNotMatch(markup.match(/<tr data-certify-row="s:4">.*?<\/tr>/s)[0], /data-certify-absences/);
  // Only the students about to receive 0 show them, and the request is the same with or without them: nothing is acted on.
  assert.doesNotMatch(markup.match(/<tr data-certify-row="s:5">.*?<\/tr>/s)[0], /data-certify-absences/);
  const answers = {ticks: new Set(["zero_no_work:d-zero", "dr_ahead:d-dr"]), decisions: {written: {skip: true, reason: "Not set up"}}, replace: new Set(), requestId: "rid"};
  const plain = {...preview, confirm: [{...zero, rows: zero.rows.map(({absences: _, ...row}) => row)}, ...preview.confirm.slice(1)]};
  assert.deepEqual(d.certifyBody(preview, d.chosenOption(preview), answers), d.certifyBody(plain, d.chosenOption(plain), answers));
  assert.equal(d.absenceText({dates: "Fri, Sep 25", status: "excused"}), "Absence recorded Fri, Sep 25 · excused");
  assert.equal(d.absenceText({first_date: "2026-09-25", status: "unknown"}), "Absence recorded 2026-09-25", "no raw code is shown");
  assert.ok(!/style=/.test(markup));
});

// A weighted gradebook with 80 columns: one select in three groups, a new column's category proposed and named (certify-1, rest-shell-1).
// As the server sends it since the instructor's decision of 2026-10-02: the one column named like the assignment first, the default.
const column = (id, title, category_id = null) => ({key: `use:${id}`, column: {action: "use", item_id: id}, item: {id, title, points: "10", category_id}, counts: {add: 3},
  checks: [{id: "gradebook_column", level: "confirm", title: `Record in the column “${title}”`, count: 1, digest: `d-${id}`}]});
// A column holding a value the instructor typed for one of these students: `kept`, and its "you typed" group with the Replace box.
const typed = option => ({...option, kept: 1, checks: [...option.checks, {id: "gradebook_kept", level: "confirm", title: "1 value you typed in the gradebook stays", count: 1,
  digest: `k-${option.item.id}`, detail: "Tick Replace for a student to record the certified value instead; unticked rows stay as you typed them.",
  rows: [{row: "s:1", name: "Ann", section: "501", detail: "typed 9 · certified 7"}]}]});
const CREATE_Q4 = {key: "create", column: {action: "create"}, item: {id: null, title: "Quiz 4", points: "10", category_id: null}, counts: {add: 3},
  checks: [{id: "gradebook_column", level: "confirm", title: "Create the gradebook column “Quiz 4”", count: 1, digest: "d-col",
    detail: "10 points · Free response. Uncategorized: it will not count toward Overall until it has a category.",
    category_details: {quiz: "10 points · Free response · Quiz Category", other: "10 points · Free response · Other (Does not count toward final grade)", "": "10 points · Free response · Uncategorized"}}]};
const WEIGHTED = {...PREVIEW, gradebook: {selected: "use:q4", warnings: [], mode: "weighted", proposed_category_id: "quiz",
  categories: [{id: "quiz", name: "Quiz Category", weight: "12", counted: true}, {id: "other", name: "Other (Does not count toward final grade)", weight: "0", counted: false},
    {id: null, name: "Uncategorized", weight: null, counted: false}],
  options: [typed(column("q4", "Quiz  4", "quiz")), CREATE_Q4, ...Array.from({length: 77}, (_, i) => column(`i${i}`, `iClicker ${i + 1}`)), column("cq4", "Canvas Quiz 4", "other")]}};
const NEW = {column: "create", ticks: new Set(), decisions: {}, replace: new Set()};

test("the column choice is one select: the same name first and the default, a new column, then the other columns", () => {
  const groups = d.columnGroups(WEIGHTED);
  assert.deepEqual(groups.map(g => [g.label, g.options.length]), [["Same name", 1], ["New column", 1], ["Other columns (78)", 78]]);
  assert.equal(groups[0].options[0].key, "use:q4");  // "Quiz  4" is "Quiz 4": spaces and case ignored
  assert.equal(d.chosenOption(WEIGHTED).key, "use:q4", "the server's default: the one column with the assignment's name (requirement, 2026-10-02)");
  // The default: the existing column, in its own category. No category to choose and no warning; one line says where the values go
  // and what becomes of those already there (gradebook/common.change_action).
  const named = d.previewMarkup(WEIGHTED, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.equal((named.match(/type="radio"/g) || []).length, 0);
  assert.match(named, /<label class="field certification-column-choice">Column<select name="certify-column" data-certify-column-select><optgroup label="Same name"><option value="use:q4" selected>Record in the column “Quiz  4” · Quiz Category<\/option><\/optgroup><optgroup label="New column"><option value="create">Create the gradebook column “Quiz 4”<\/option><\/optgroup><optgroup label="Other columns \(78\)">/);
  assert.doesNotMatch(named, /data-certify-category|data-certify-duplicate|data-certify-uncounted|class="grading-warning"/);
  assert.match(named, /data-certify-column>“Quiz  4” · 10 points · Quiz Category\./);
  assert.match(named, /<p class="supporting-copy" data-certify-existing>Certifying records into the existing column “Quiz  4” in Quiz Category, which has this assignment's name\. Values typed or cleared there in Faculty stay unless you tick Replace; values imported from Canvas or a file are replaced\.<\/p>/);
  assert.match(named, /<legend>Record in the column “Quiz  4”<\/legend>/);
  // The instructor chooses a new column instead: its category, proposed by the server, and a warning that it makes a second one.
  const markup = d.previewMarkup(WEIGHTED, NEW, h);
  assert.match(markup, /<optgroup label="Same name"><option value="use:q4">Record in the column “Quiz  4” · Quiz Category<\/option><\/optgroup><optgroup label="New column"><option value="create" selected>/);
  assert.doesNotMatch(markup, /data-certify-existing/);
  // A new column: its category, proposed by the server, and the column line and its check say it.
  assert.match(markup, /Category<select data-certify-category><option value="quiz" selected>Quiz Category<\/option><option value="other">Other \(Does not count toward final grade\)<\/option><option value="">Uncategorized<\/option><\/select>/);
  assert.match(markup, /data-certify-column>“Quiz 4” · 10 points · Quiz Category\. Nothing is published or emailed\./);
  assert.match(markup, /<legend>Create the gradebook column “Quiz 4”<\/legend>\s*<p class="supporting-copy">10 points · Free response · Quiz Category<\/p>/);
  assert.doesNotMatch(markup, /data-certify-uncounted/);
  // A new column makes a second “Quiz 4” beside the imported one, in the same counted category: the dialog says so (review).
  assert.match(markup, /<p class="grading-warning" role="status" data-certify-duplicate>A column named “Quiz  4” already exists \(Quiz Category\)\. A new column makes a second “Quiz 4”, counted in the same category\. Choose it under Same name to record there instead\.<\/p>/);
  // Uncategorized, or a category with weight 0, would not count toward Overall: the dialog says so beside the choice.
  const none = d.previewMarkup(WEIGHTED, {...NEW, category: null}, h);
  assert.match(none, /<p class="grading-warning" role="status" data-certify-uncounted>Quiz 4 will not count toward the overall grade: this gradebook is weighted by category, and Uncategorized is not counted\.<\/p>/);
  assert.match(none, /<option value="" selected>Uncategorized<\/option>/);
  assert.match(none, /<p class="supporting-copy">10 points · Free response · Uncategorized<\/p>/);
  assert.equal(d.duplicateText(WEIGHTED, d.chosenOption(WEIGHTED, "create"), null),
    "A column named “Quiz  4” already exists (Quiz Category). A new column makes a second “Quiz 4”. Choose it under Same name to record there instead.");
  assert.equal(d.duplicateText(WEIGHTED, d.chosenOption(WEIGHTED), null), "", "recording in the same-named column makes no second one");
  // Two of them, one in a category that does not count: the server keeps a new column as the default (no one column to choose),
  // each is named, and "in the same category" is said without "counted".
  const two = {...WEIGHTED, gradebook: {...WEIGHTED.gradebook, selected: "create",
    options: [CREATE_Q4, ...WEIGHTED.gradebook.options.filter(o => o.key !== "create"), column("q4b", "quiz 4", "other")]}};
  assert.equal(d.chosenOption(two).key, "create");
  assert.deepEqual(d.columnGroups(two)[0].options.map(o => o.key), ["use:q4", "use:q4b"]);
  assert.equal(d.duplicateText(two, d.chosenOption(two), "other"),
    "2 columns named “Quiz 4” already exist: “Quiz  4” (Quiz Category), “quiz 4” (Other (Does not count toward final grade)). A new column makes another “Quiz 4”, in the same category. Choose one under Same name to record there instead.");
  assert.equal(d.uncountedText(WEIGHTED, d.chosenOption(WEIGHTED, "create"), "other"),
    "Quiz 4 will not count toward the overall grade: this gradebook is weighted by category, and “Other (Does not count toward final grade)” has weight 0.");
  // Another existing column also records in its own category, and its line says so without claiming the assignment's name. Nothing
  // was typed there for these students, so there is no Replace box to speak of (review).
  assert.equal(d.existingText(WEIGHTED, d.chosenOption(WEIGHTED, "use:cq4")),
    "Certifying records into the existing column “Canvas Quiz 4” in Other (Does not count toward final grade). Nothing there was typed in Faculty for these students; values imported from Canvas or a file are replaced.");
  assert.match(d.existingText(WEIGHTED, {...d.chosenOption(WEIGHTED, "use:cq4"), kept: 2}), /\. Values typed or cleared there in Faculty stay unless you tick Replace; values imported/);
  assert.equal(d.existingText(WEIGHTED, d.chosenOption(WEIGHTED, "create")), "");
  // Points mode (or no categories at all): nothing about counting, no category choice without categories.
  const points = {...WEIGHTED, gradebook: {...WEIGHTED.gradebook, mode: "points", categories: [{id: null, name: "Uncategorized", weight: null, counted: true}], proposed_category_id: null}};
  const plain = d.previewMarkup(points, NEW, h);
  assert.doesNotMatch(plain, /data-certify-category|data-certify-uncounted/);
  assert.match(plain, /data-certify-column>“Quiz 4” · 10 points\. Nothing/);
  assert.match(d.previewMarkup(points, {...NEW, column: ""}, h), /data-certify-existing>Certifying records into the existing column “Quiz  4”, which has this assignment's name\. Values typed/);
  // Without categories the duplicate is still said, with no category to name.
  assert.match(plain, /data-certify-duplicate>A column named “Quiz  4” already exists\. A new column makes a second “Quiz 4”\. Choose it/);
  // The assignment's own column (no new one to make): the options without groups.
  const own = {...PREVIEW, gradebook: {selected: "parts:i", options: [{key: "parts:i", column: {action: "parts", item_id: "i", parts: "certified"}, item: {id: "i", title: "Test 1", points: "18"}, checks: []},
    {key: "keep:i", column: {action: "ok", item_id: "i", parts: "keep"}, item: {id: "i", title: "Test 1", points: "18"}, checks: []}]}};
  assert.doesNotMatch(d.previewMarkup(own, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h), /optgroup/);
});

// Review of the same-titled default: an existing column chosen (the default one included) that would not count toward Overall is said
// as a new column's is, with where to change it; a column in a counted category, or a points gradebook, says nothing.
test("an existing column that does not count toward Overall is warned about, the same-named default included", () => {
  const other = {...WEIGHTED, gradebook: {...WEIGHTED.gradebook, selected: "use:cq4"}};
  assert.equal(d.uncountedText(WEIGHTED, d.chosenOption(WEIGHTED, "use:cq4")),
    "“Canvas Quiz 4” will not count toward the overall grade: this gradebook is weighted by category, and its category “Other (Does not count toward final grade)” has weight 0. Move it to a counted category in the gradebook's Setup, or choose a new column here.");
  assert.equal(d.uncountedText(WEIGHTED, d.chosenOption(WEIGHTED, "use:i0")),
    "“iClicker 1” will not count toward the overall grade: this gradebook is weighted by category, and the column has no category. Choose its category in the gradebook's Setup, or choose a new column here.");
  // A category the setup no longer has is no category (gradebook/compute's `uncounted`).
  assert.match(d.uncountedText(WEIGHTED, column("gone", "Quiz 4", "deleted")), /and the column has no category\./);
  assert.equal(d.uncountedText(WEIGHTED, d.chosenOption(WEIGHTED)), "", "Quiz Category counts");
  const markup = d.previewMarkup(other, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.match(markup, /<p class="grading-warning" role="status" data-certify-uncounted>“Canvas Quiz 4” will not count toward the overall grade: [^<]*has weight 0\. Move it to a counted category in the gradebook's Setup, or choose a new column here\.<\/p>/);
  assert.doesNotMatch(markup, /data-certify-duplicate|data-certify-category/);
  // The same-named column as the server's default, in a category of weight 0: the default warns too.
  const zero = {...WEIGHTED, gradebook: {...WEIGHTED.gradebook, options: WEIGHTED.gradebook.options.map(o => o.key === "use:q4" ? {...o, item: {...o.item, category_id: "other"}} : o)}};
  assert.match(d.previewMarkup(zero, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h), /data-certify-uncounted>“Quiz  4” will not count toward the overall grade/);
  // The assignment's own column (no new one to offer): where to change it, and nothing about a new column.
  const own = {...WEIGHTED, gradebook: {...WEIGHTED.gradebook, selected: "ok:i", options: [{key: "ok:i", column: {action: "ok", item_id: "i"}, item: {id: "i", title: "Quiz 3", points: "10", category_id: null}, checks: []}]}};
  assert.equal(d.uncountedText(own, d.chosenOption(own)),
    "“Quiz 3” will not count toward the overall grade: this gradebook is weighted by category, and the column has no category. Choose its category in the gradebook's Setup.");
  // Points mode counts every column; a preview without categories says nothing.
  const points = {...WEIGHTED, gradebook: {...WEIGHTED.gradebook, mode: "points", categories: [{id: "quiz", name: "Quiz Category", weight: "12", counted: true},
    {id: null, name: "Uncategorized", weight: null, counted: true}]}};
  assert.equal(d.uncountedText(points, d.chosenOption(points, "use:i0")), "");
  assert.equal(d.uncountedText({...own, gradebook: {...own.gradebook, categories: undefined}}, d.chosenOption(own)), "");
});

test("a withdrawn absence is named but never marked as covering the day", () => {
  const zero = {...PREVIEW.confirm[0], count: 1, rows: [{row: "s:3", name: "Carol", section: "501", detail: "No paper scanned",
    absences: [{first_date: "2026-09-25", last_date: "2026-09-25", dates: "Fri, Sep 25", status: "withdrawn", covers: true}]}]};
  const markup = d.previewMarkup({...PREVIEW, confirm: [zero, ...PREVIEW.confirm.slice(1)]}, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.match(markup, /<li>Absence recorded Fri, Sep 25 · withdrawn<\/li>/);
  assert.doesNotMatch(markup, /data-certify-absence-covers/);
});

// ------------------------------------------------------------------------------------------------------------------------------
// A line added to every final grade of one question in one section (grading-certification-adjust.js, 2026-09-27).
import * as adjust from "../static/grading-certification-adjust.js";

const ADJUST = {rule: "batch-adjustment-v1", question: {question_id: "q20", title: "Question 20", version: "A", max: "10"}, section_id: "s501", section: 501,
  points: "2", reason: "Misprint <c>", fingerprint: "f".repeat(64),
  changes: [{response_id: "r1", row: "s:1", name: "Zed, Ann", section: 501, kind: "automatic", before: "4", after: "6", later_proposal: false, gradescope: "sent"},
            {response_id: "r2", row: "s:2", name: "Able, Bo", section: 501, kind: "review", before: "0", after: "2", later_proposal: true, gradescope: "none"}],
  left: [{response_id: "r3", row: "s:3", name: "Mid, Cy", section: 501, code: "not_final", state: "draft", score: null, reason: "A draft review takes it out of final"},
         {response_id: "r4", row: "s:4", name: "Low, Di", section: 501, code: "bounded", state: "approved", score: "10", reason: "The question's point bounds keep it at 10"}],
  counts: {change: 2, left: 2, not_final: 1, bounded: 1, on_gradescope: 1}};

test("the question panel offers a line for every grade only when the question has a final grade", () => {
  const markup = q.questionPanelMarkup(QUESTION, {filter: "all", sort: "name", hideNames: false, groups: []}, {id: "p"}, h);
  assert.match(markup, /<button type="button" class="button" data-cert-adjust="q20">Add a line to every grade…<\/button>/);
  const none = q.questionPanelMarkup({...QUESTION, responses: QUESTION.responses.filter(r => r.response_id === "r4")}, {filter: "all", sort: "name", hideNames: false, groups: []}, {id: "p"}, h);
  assert.ok(!none.includes("data-cert-adjust"));
});

test("a batch line: signed points, what is wrong before sending, the body, and the preview's sentence and button", () => {
  assert.equal(adjust.signed(2), "+2");
  assert.equal(adjust.signed("-1.5"), "−1.5");
  assert.equal(adjust.signed("x"), "");
  const ok = {section: "s501", points: "2", reason: "Misprint"};
  assert.equal(adjust.lineProblem(ok, "10"), "");
  assert.equal(adjust.lineProblem({...ok, section: ""}), "Choose a section, or every section.");
  assert.equal(adjust.lineProblem({...ok, reason: "  "}), "Write the line's wording.");
  assert.match(adjust.lineProblem({...ok, reason: "x".repeat(201)}), /200 characters/);
  assert.equal(adjust.lineProblem({...ok, points: ""}), "Give the line's points.");
  assert.equal(adjust.lineProblem({...ok, points: "0"}), "An adjustment of 0 points changes no grade.");
  assert.equal(adjust.lineProblem({...ok, points: "0.125"}), "Give the points with at most two decimals.");
  assert.equal(adjust.lineProblem({...ok, points: "0.29"}), "");
  assert.match(adjust.lineProblem({...ok, points: "-11"}, "10"), /worth 10/);
  assert.deepEqual(adjust.requestBody("add", {section: adjust.EVERY, points: "-1.5", reason: " Units "}), {section_id: "all", points: -1.5, reason: "Units"});
  assert.equal(adjust.summaryText(ADJUST), "2 final grades in section 501 get “Misprint <c>” (+2). 2 responses are left alone.");
  assert.equal(adjust.summaryText({...ADJUST, section_id: "all", counts: {change: 1, left: 0}}), "1 final grade in every section gets “Misprint <c>” (+2).");
  assert.equal(adjust.summaryText({...ADJUST, counts: {change: 0, left: 2}}), "No final grade of this question in section 501 would change.");
  assert.equal(adjust.submitLabel(ADJUST), "Add to 2 grades");
  assert.equal(adjust.submitLabel({...ADJUST, counts: {change: 0}}), "Preview");
  assert.equal(adjust.submitLabel(null), "Preview");
});

test("the batch preview lists each grade before and after, what is left alone and why, and hides names on request", () => {
  const markup = adjust.previewMarkup(ADJUST, {}, h);
  assert.match(markup, /data-adjust-change="r1"><th scope="row">Zed, Ann<\/th><td>501<\/td><td>4<\/td><td>6<\/td><td>on Gradescope: send again<\/td>/);
  assert.match(markup, /data-adjust-change="r2">.*a newer proposal also waits for you/);
  assert.match(markup, /Misprint &lt;c&gt;/);
  assert.match(markup, /data-adjust-left="r3">.*A draft review takes it out of final/);
  assert.match(markup, /1 of these grades is already on Gradescope/);
  assert.ok(!markup.includes("<details class=\"disclosure\" id=\"adjust-left\" open"), "the left-alone list is folded while grades change");
  assert.ok(!/style=/.test(markup));
  const hidden = adjust.previewMarkup(ADJUST, {hideNames: true}, h);
  assert.ok(!hidden.includes("Zed, Ann") && !hidden.includes("Mid, Cy") && hidden.includes("Response 1") && hidden.includes("Response 3"));
  const failed = adjust.previewMarkup(null, {error: {title: "No answer came back", body: "Retry", retry: true}}, h);
  assert.match(failed, /data-adjust-retry/);
  assert.match(adjust.previewMarkup({...ADJUST, certified: true}, {}, h), /the step reopens until you certify again/);
});

test("each grade names its adjustments beside its rubric lines, in the question panel and the drawer", () => {
  const withLine = {...QUESTION, responses: QUESTION.responses.map(r => r.response_id === "r2" ? {...r, adjustments: [{points: "2", reason: "Misprint <c>"}, {points: "-0.5", reason: "Units"}]} : r)};
  const markup = q.questionPanelMarkup(withLine, {filter: "all", sort: "name", hideNames: false, groups: []}, {id: "p"}, h);
  assert.match(markup, /Rubric lines and adjustments/);
  assert.match(markup, /3\.2<span class="certification-adjustment" data-cert-adjustment>\+2 Misprint &lt;c&gt;<\/span><span class="certification-adjustment" data-cert-adjustment>−0\.5 Units<\/span>/);
  const drawer = responseMarkup({result: {applications: [], score: {score: 4}}, review: {applications: [{rubric_item_id: "R3_2", applied: true}], score: {score: 6, adjustments: [{points: 2, reason: "Misprint <c>"}]}},
    question: {content: {max_points: 10}, rubric: {rules: {items: [{id: "R3_2", points: 4, description: "Set up"}]}}}}, {}, h);
  assert.match(drawer, /data-cert-adjustment><span class="certification-line-points" aria-label="Adjustment">\+2<\/span>\s*<div class="certification-line-text">Misprint &lt;c&gt; <small class="certification-quiet">point adjustment<\/small>/);
  assert.match(drawer, /<strong data-cert-score>6 \/ 10<\/strong> <span>1 line applied · 1 adjustment<\/span>/);
});

// 2026-11-11 (instructor: "Can we make sure it's clear which comments will be seen by students and which won't"; GRADING "WHO SEES A
// COMMENT"): the wording of a line for every grade goes to Gradescope and says so; a review's comment never does, and says that.
test("a line's wording says students see it, and a grade's comment says only you do", () => {
  for (const action of ["add", "edit"]) {
    const form = adjust.formMarkup(action, ADJUST.question, [{section_id: "s501", number: 501}], action === "edit" ? {batch_id: "b1", reason: "Misprint", points: "2", section: 501} : null, h);
    assert.ok(form.includes(`<label class="field">The line's wording<small class="field-hint" data-seen-by="students">Students see this on Gradescope</small><input name="reason"`), action);
  }
  assert.match(adjust.formMarkup("add", ADJUST.question, [], null, h), /Gradescope receives the points as the grade's point adjustment and the wording as its comment\./);
  assert.ok(!adjust.formMarkup("remove", ADJUST.question, [], {batch_id: "b1", reason: "Misprint", points: "2", section: 501}, h).includes("data-seen-by"));
  const drawer = responseMarkup({result: {applications: [], score: {score: 4}}, review: {comment: "Looked at <it> twice", applications: [], score: {score: 4}},
    question: {content: {max_points: 10}, rubric: {rules: {items: []}}}}, {}, h);
  assert.ok(drawer.includes('<p class="certification-line-comment" data-cert-comment><strong>Your comment</strong> <small class="certification-quiet" data-seen-by="you">(only you see this)</small>: '));
});

// 2026-10-06 (instructor: "not very user friendly or intuitive … a wall of text"): an open question leads with the grade, the lines
// applied and Change grade, then the paper; the other lines, the reading, the flags and the checks fold.
test("an open question reads goal first: the grade and its lines, the paper, then folds", () => {
  const items = [{id: "R1", points: 0, description: "Correct answer"}, {id: "R6", points: -0.5, description: "Ratio constraint missing"},
    {id: "R7", points: -0.5, description: "Non-negativity missing"}, {id: "R8", points: -0.5, description: "Strict inequality"}];
  const value = {result: {signals: {}, applications: [{rubric_item_id: "R1", applied: false, reason: "Not applicable: R6 and R7 apply."},
    {rubric_item_id: "R6", applied: true, reason: "The ratio constraint is written as 2x≤y. It should read x≥2y."}, {rubric_item_id: "R7", applied: true, reason: "No non-negativity constraints."},
    {rubric_item_id: "R8", applied: false, reason: "No strict inequalities."}], score: {score: 4}, transcript: {lines: [{elements: [{type: "math", latex: "5 2 1 160"}]}]}},
    crops: [{images: {original: {url: "/crop/1.png"}}}], question: {content: {max_points: 5}, rubric: {rules: {items}}}};
  const markup = responseMarkup(value, {flags: {names: ["flag_reviewer"], settled: true, flag_review: {outcome: "finalized", reason: "Upheld."}}}, h, {change: true});
  const at = text => markup.indexOf(text);
  assert.match(markup, /<div class="certification-verdict"><p><strong data-cert-score>4 \/ 5<\/strong> <span>2 lines applied<\/span><\/p><button type="button" class="button" data-cert-change>Change grade<\/button><\/div>/);
  // The lines applied, in rubric order, before the paper; each comment's first sentence, the rest folded.
  const applied = markup.slice(at("data-cert-applied-lines"), at("certification-crops"));
  assert.match(applied, /−0\.5<\/span>[\s\S]*Ratio constraint missing[\s\S]*The ratio constraint is written as 2x≤y\.<\/span><\/span><details class="certification-more">/);
  assert.match(applied, /Non-negativity missing/);
  assert.doesNotMatch(applied, /Correct answer|Strict inequality/);
  // One crop: the drawer's width, no "Region 1" caption.
  assert.doesNotMatch(markup, /Region 1/);
  // Then the folds, closed: the other lines (counted, "0" for a line worth nothing), the reading, the flags, in that order.
  assert.ok(at("certification-crops") < at("Other rubric lines (2)") && at("Other rubric lines (2)") < at("What the reader read") && at("What the reader read") < at("Flags: upheld by the flag reviewer"));
  assert.match(markup, /<details class="disclosure certification-fold certification-other-lines"><summary><span>Other rubric lines \(2\)<\/span>/);
  assert.match(markup, /aria-label="Not applied, 0 points">0<\/span>/);
  assert.match(markup, /<details class="disclosure certification-fold certification-reading"><summary><span>What the reader read<\/span><\/summary>/);
  assert.match(markup, /grading-transcript-row/);
  // The question's row above already shows its score: the grade's row does not repeat it.
  assert.match(responseMarkup(value, {score: "4", flags: {}}, h), /<div class="certification-verdict"><p><span>2 lines applied<\/span><\/p><\/div>/);
  // A reading flag that still stands opens the reading, marked in doubt.
  const doubt = responseMarkup(value, {flags: {names: ["reading"], settled: false, original_state: "ocr_review"}}, h);
  assert.match(doubt, /certification-reading" open><summary><span>What the reader read<\/span><span class="disclosure-hint">in doubt<\/span>/);
  assert.doesNotMatch(doubt, /data-cert-change/);
  // Blind review: no model comment, and no proposal at all says so beside Change grade.
  assert.doesNotMatch(responseMarkup({...value, hidden: true}, {}, h), /The ratio constraint/);
  assert.match(responseMarkup({}, {}, h, {change: true}), /no completed proposal yet\.<\/p><button type="button" class="button" data-cert-change>/);
});

const BATCHES = [
  {batch_id: "b1", action: "add", of: null, section_id: "s501", section: 501, points: "2", reason: "Misprint", old: null, created_at: "2026-09-27T04:00:00+00:00", changed: 30, carrying: 0},
  {batch_id: "b2", action: "edit", of: "b1", section_id: "s501", section: 501, points: "3", reason: "Misprint <c>", old: {points: "2", reason: "Misprint"}, created_at: "2026-09-27T05:00:00+00:00", changed: 28, carrying: 27},
  {batch_id: "b3", action: "add", of: null, section_id: "all", section: "", points: "-1", reason: "Units", old: null, created_at: "2026-09-27T06:00:00+00:00", changed: 60, carrying: 0},
  {batch_id: "b4", action: "remove", of: "b3", section_id: "all", section: "", points: "-1", reason: "Units", old: null, created_at: "2026-09-27T07:00:00+00:00", changed: 60, carrying: 0},
];

test("the question's batch lines: those still on grades with Edit… and Remove…, and every batch's history, newest first", () => {
  assert.equal(q.signedPoints("2"), "+2");
  assert.equal(q.signedPoints(-1.5), "−1.5");
  assert.equal(q.signedPoints(null), "");
  const markup = q.batchesMarkup(BATCHES, h);
  assert.match(markup, /<li data-cert-batch="b2"><span class="certification-batch-line"><strong>\+3<\/strong> Misprint &lt;c&gt;<\/span><span class="supporting-copy">section 501 · on 27 of 28 grades it changed<\/span>/);
  assert.match(markup, /data-cert-batch-edit="b2">Edit…/);
  assert.match(markup, /class="danger-button" data-cert-batch-remove="b2">Remove…/);
  assert.ok(!markup.includes('data-cert-batch="b1"') && !markup.includes('data-cert-batch="b3"') && !markup.includes('data-cert-batch="b4"'), "only lines still on some grade");
  assert.deepEqual([...markup.matchAll(/data-cert-batch-history="(b\d)"/g)].map(m => m[1]), ["b4", "b3", "b2", "b1"]);
  assert.match(q.historyText(BATCHES[1]), /Changed “Misprint” \(\+2\) to “Misprint <c>” \(\+3\) on 28 grades · section 501$/);
  assert.match(q.historyText(BATCHES[3]), /Removed “Units” \(−1\) from 60 grades · every section$/);
  assert.match(q.batchesMarkup([BATCHES[0]], h), /No batch line is on any grade now/);
  assert.equal(q.batchesMarkup([], h), "");
  const panel = q.questionPanelMarkup({...QUESTION, batches: BATCHES}, {filter: "all", sort: "name", hideNames: false, groups: []}, {id: "p"}, h);
  assert.match(panel, /data-cert-batches/);
});

test("editing and removing a batch line: the bodies, the sentences, the buttons and the toast", () => {
  const batch = BATCHES[1];
  assert.deepEqual(adjust.requestBody("edit", {points: "4", reason: " Misprint "}, batch), {action: "edit", batch_id: "b2", points: 4, reason: "Misprint"});
  assert.deepEqual(adjust.requestBody("remove", {}, batch), {action: "remove", batch_id: "b2"});
  assert.equal(adjust.lineProblem({section: "s501", points: "3", reason: "Misprint <c>"}, "10", batch), "Change the line's wording or its points.");
  assert.equal(adjust.lineProblem({section: "s501", points: "4", reason: "Misprint <c>"}, "10", batch), "");
  const edit = {...ADJUST, action: "edit", batch_id: "b2", old: {points: "2", reason: "Misprint"}, points: "3", reason: "Misprint (c)", counts: {change: 2, left: 1}};
  assert.equal(adjust.summaryText(edit), "2 grades have “Misprint” (+2) changed to “Misprint (c)” (+3). 1 response is left alone.");
  assert.equal(adjust.submitLabel(edit), "Change on 2 grades");
  const remove = {...ADJUST, action: "remove", batch_id: "b2", points: "3", reason: "Misprint (c)", counts: {change: 1, left: 0}};
  assert.equal(adjust.summaryText(remove), "“Misprint (c)” (+3) comes off 1 grade.");
  assert.equal(adjust.submitLabel(remove), "Remove from 1 grade");
  assert.equal(adjust.summaryText({...remove, counts: {change: 0, left: 3}}), "No grade still carries “Misprint (c)” (+3); nothing would change. 3 responses are left alone.");
  assert.equal(adjust.doneText({action: "edit", old: {points: "2", reason: "Misprint"}, points: "3", reason: "Misprint (c)", changed: 2}), "Changed “Misprint” (+2) to “Misprint (c)” (+3) on 2 grades.");
  assert.equal(adjust.doneText({action: "remove", points: "3", reason: "Misprint (c)", changed: 1}), "Removed “Misprint (c)” (+3) from 1 grade.");
  assert.equal(adjust.doneText({action: "add", points: "1", reason: "Misprint", changed: 1}), "Added “Misprint” (+1) to 1 grade.");
  assert.equal(adjust.doneText({replay: true}), "This change was already made; nothing was changed twice.");
});

test("grades a reviewing model settled are counted and shown apart from those still flagged", () => {
  const card = s.cardsModel({...VIEW, counts: {...VIEW.counts, flagged: 68, settled: 765}}).find(c => c.key === "flagged");
  // The tile's label stays a label; what the reviewing model settled is a plain note under it (critique 2026-10-05, §1.12).
  assert.deepEqual([card.value, card.label, card.note], ["68", "Still flagged", "765 more settled by a reviewing model"]);
  assert.deepEqual([s.cardsModel(VIEW).find(c => c.key === "flagged").label, s.cardsModel(VIEW).find(c => c.key === "flagged").note], ["Still flagged", ""]);
  const table = s.questionTableMarkup({...VIEW, questions: [{...VIEW.questions[0], flagged: 2, settled: 34}]}, h);
  assert.match(table, /<td>2<small class="certification-note">\+34 settled<\/small><\/td>/);
  assert.match(q.flagChips(["arbitrator"], h, {settled: true}), /data-tone="neutral" data-cert-settled[^>]*>Settled by the arbitrator</);
  assert.match(q.flagChips(["flag_reviewer"], h, {settled: true}), />Upheld by the flag reviewer</);
  assert.match(q.flagChips(["arbitrator", "flag_reviewer"], h, {settled: true}), />Settled by the reviewing models</);
  assert.match(q.flagChips(["arbitrator", "risk"], h), /data-tone="warning"/);
  assert.ok(!q.flagChips(["arbitrator"], h).includes("did not settle"));
  const data = {...QUESTION, responses: QUESTION.responses.map(r => r.response_id === "r1" ? {...r, flagged: false, settled: true} : r)};
  assert.deepEqual(q.filterResponses(data, {filter: "settled"}).map(r => r.response_id), ["r1"]);
  assert.deepEqual(q.filterResponses(data, {filter: "flagged"}).map(r => r.response_id), ["r4"]);
  assert.match(q.questionPanelMarkup(data, {filter: "all", sort: "name", hideNames: false, groups: []}, {id: "p"}, h), /Settled by the arbitrator/);
});

// The Final Quality Audit's report, one finding at a time (requirement, 2026-09-29): piles in the order to look at them,
// the most severe first, a question narrowing a pile, and a place that stays inside what is shown.
test("the audit report is read one finding at a time, in piles, the most severe first", async () => {
  const {auditItems, auditPlace, auditMarkup} = await import("../static/grading-certification-audit.js");
  const finding = (severity, verdict) => ({kind: "inconsistent", severity, refs: [], lines: [], explanation: `${severity} ${verdict}`, suggestions: [],
    confirm: verdict ? {verdict, explanation: "", suggestions: []} : {}});
  const result = {summary: "s", checks: [{kind: "same_answer", question_id: "q1", title: "Question 1 · Version A", detail: "d", responses: []}],
    questions: [
      {question_id: "q1", label: "Question 1 · Version A", responses: 5, findings: [finding("low", "confirmed"), finding("high", "not_confirmed")], image_sample: {selected: 2, read: 2, refs: [], readings: []}},
      {question_id: "q2", label: "Question 2 · Version A", responses: 5, findings: [finding("high", "confirmed"), finding("medium", "partly"), finding("low", null)]}]};
  const items = auditItems(result);
  const first = auditPlace(items);
  assert.deepEqual(first.counts, {held: 2, partly: 1, unchecked: 1, samples: 1, checks: 1, not: 1});
  assert.equal(first.pile, "held");  // what held up against the scans comes first
  assert.deepEqual(first.shown.map(item => item.f.explanation), ["high confirmed", "low confirmed"]);  // the most severe first
  assert.deepEqual(auditPlace(items, {pile: "held", question: "q1"}).shown.map(item => item.question_id), ["q1"]);
  assert.equal(auditPlace(items, {pile: "held", index: 9}).index, 1);  // the place stays inside what is shown
  assert.equal(auditPlace(items, {pile: "held", question: "q9"}).question, "");  // a question the pile lacks means every question
  assert.equal(auditPlace(items.filter(item => item.pile !== "held"), {pile: "held"}).pile, "partly");  // an emptied pile gives way
  const markup = auditMarkup({audit: {status: "complete", model: "m", updated_at: "2026-09-29T00:00:00Z", result}}, {students: []}, {auditView: {pile: "held"}}, h);
  assert.equal((markup.match(/data-cert-audit-finding/g) || []).length, 1);  // one finding shown, never the whole list
  assert.match(markup, /<strong>1<\/strong> of 2/);
  assert.match(markup, /data-cert-audit-move="-1" disabled/);
  assert.match(markup, /data-filter-group="cert-audit"/);
  assert.match(markup, /All questions \(2\)/);
  // Finished, it is one fold (certify-11): open while newer than the certification, closed when certified after it.
  const done = {audit: {status: "complete", model: "m", updated_at: "2026-09-29T00:00:00Z", result}};
  assert.match(markup, /<details class="disclosure" id="cert-fold-audit" open><summary><span>Final quality audit<\/span><span class="disclosure-hint">[^<]+ · s<\/span>/, "no totals: the summary's first sentence");
  assert.match(auditMarkup({audit: {status: "complete", model: "m", updated_at: "2026-09-29T00:00:00Z", result: {...result, totals: {findings: 81, confirmed: 20}}}}, {students: []}, {auditView: {pile: "held"}}, h),
    /<span class="disclosure-hint">[^<]+ · 81 findings · 20 held up against the scans<\/span>/);
  const older = auditMarkup(done, {plan_id: "p", students: [], certification: {state: "current", created_at: "2026-10-02T02:52:00Z"}}, {auditView: {pile: "held"}}, h);
  assert.match(older, /<details class="disclosure" id="cert-fold-audit" data-disclosure-key="grading:certification:audit:p::closed">/);
  const newer = auditMarkup(done, {plan_id: "p", students: [], certification: {state: "current", created_at: "2026-09-28T00:00:00Z"}}, {auditView: {pile: "held"}}, h);
  assert.match(newer, /id="cert-fold-audit" data-disclosure-key="grading:certification:audit:p::open" open>/);
  // Opened or closed on the page, it stays so through a redraw.
  assert.match(auditMarkup(done, {plan_id: "p", students: [], certification: {created_at: "2026-10-02T02:52:00Z"}}, {auditView: {pile: "held"}, open: new Set(["cert-fold-audit"]), closed: new Set()}, h), /id="cert-fold-audit"[^>]* open>/);
  // Running or stopped, it is shown as before.
  assert.doesNotMatch(auditMarkup({audit: {status: "running", model: "m", responses: 3, questions: 1, progress: {}}}, {students: []}, {auditView: {}}, h), /cert-fold-audit/);
});

// GRADESCOPE "AFTER CERTIFYING" (requirement, 2026-10-12): the dialog's Gradescope tick and the page's lines for what Faculty then
// does by itself.
import { afterRunsMarkup } from "../static/grading-certification.js";

test("certify: the Gradescope tick lets Faculty send a group afterwards, and without it the dialog is what it was", () => {
  // (The fixture's preview is of a Faculty without Gradescope, whose reason is prefilled; with Gradescope set up there is none.)
  const offered = {...PREVIEW, gradescope: {...(PREVIEW.gradescope || {}), configured: true, after: true},
    confirm: PREVIEW.confirm.map(c => c.id === "gradescope_written" ? {...c, prefill: ""} : c)};
  const create = d.chosenOption(offered);
  const ticks = new Set(["zero_no_work:d-zero", "gradebook_column:d-col"]);
  // Not offered (the switch is off, or Gradescope is not set up): nothing of it is drawn, asked or sent.
  assert.equal(d.afterMarkup(PREVIEW, {}, h), "");
  assert.equal(d.afterOn(PREVIEW, undefined), false);
  assert.deepEqual(d.missingAnswers(PREVIEW, d.chosenOption(PREVIEW), {ticks, decisions: {}}), ["Written grades not on Gradescope"]);
  assert.ok(!("gradescope_after" in d.certifyBody(PREVIEW, d.chosenOption(PREVIEW), {ticks, decisions: {written: {skip: true, reason: "No"}}})));
  // Offered, it is ticked until the instructor takes it off: an unanswered group is one Faculty sends, and the request says so.
  assert.equal(d.afterOn(offered, undefined), true);
  assert.deepEqual(d.missingAnswers(offered, create, {ticks, decisions: {}}), []);
  const body = d.certifyBody(offered, create, {ticks, decisions: {}});
  assert.equal(body.gradescope_after, true);
  assert.deepEqual(body.gradescope, {written: {decision: "send"}});
  // "Do not send" still stands for its group, with its reason.
  const declined = d.certifyBody(offered, create, {ticks, decisions: {written: {skip: true, reason: " Graded by the TA there "}}});
  assert.deepEqual(declined.gradescope, {written: {decision: "do_not_send", reason: "Graded by the TA there"}});
  assert.equal(declined.gradescope_after, true);
  assert.deepEqual(d.missingAnswers(offered, create, {ticks, decisions: {written: {skip: true, reason: ""}}}), ["a reason for not sending (Written grades not on Gradescope)"]);
  // Unticked: the group is asked as before and nothing is named in the request.
  assert.deepEqual(d.missingAnswers(offered, create, {ticks, decisions: {}, after: false}), ["Written grades not on Gradescope"]);
  const plain = d.certifyBody(offered, create, {ticks, decisions: {written: {skip: true, reason: "No"}}, after: false});
  assert.ok(!("gradescope_after" in plain));
  assert.deepEqual(plain, d.certifyBody(PREVIEW, d.chosenOption(PREVIEW), {ticks, decisions: {written: {skip: true, reason: "No"}}}));

  // What the last certification declined with a reason is declined again by default, and sent only when the instructor unticks it.
  const before = {...offered, confirm: offered.confirm.map(c => c.id === "gradescope_written" ? {...c, prefill: "The TA grades these on Gradescope."} : c)};
  const carried = d.certifyBody(before, d.chosenOption(before), {ticks, decisions: {}});
  assert.deepEqual(carried.gradescope, {written: {decision: "do_not_send", reason: "The TA grades these on Gradescope."}});
  assert.deepEqual(d.missingAnswers(before, d.chosenOption(before), {ticks, decisions: {}}), []);
  assert.deepEqual(d.certifyBody(before, d.chosenOption(before), {ticks, decisions: {written: {skip: false, reason: "The TA grades these on Gradescope."}}}).gradescope, {written: {decision: "send"}});
  assert.deepEqual(d.groupAnswer(before.confirm.find(c => c.id === "gradescope_written"), {}, false), {});  // unticked dialog: asked as before
  const again = d.previewMarkup(before, {column: "", ticks: new Set(), decisions: {}, replace: new Set()}, h);
  assert.match(again, /data-certify-skip="written" checked>/);
  assert.match(again, /data-certify-prefilled>Not sent at the last certification, for this reason\. Untick it and Faculty sends them after you certify\./);
  assert.match(again, /<textarea data-certify-reason="written" rows="2" maxlength="500">The TA grades these on Gradescope\.<\/textarea>/);

  const state = {column: "", ticks: new Set(), decisions: {}, replace: new Set()};
  const on = d.previewMarkup(offered, state, h);
  assert.match(on, /<fieldset class="certification-confirm" data-certify-after><legend>Gradescope<\/legend>/);
  assert.match(on, /<input type="checkbox" data-certify-after-tick checked>Send to Gradescope and publish the grades after certifying/);
  assert.match(on, /data-certify-after-sends>Faculty sends these to Gradescope by itself after you certify\./);
  assert.doesNotMatch(on, /data-certify-send="written"/);  // no "send them now" beside it
  assert.match(on, /data-certify-skip="written"/);  // "Do not send these" stays
  assert.match(on, /Nobody is emailed\./);
  assert.doesNotMatch(on, /Nothing is published or emailed/);
  assert.ok(!/style=/.test(on));
  const off = d.previewMarkup(offered, {...state, after: false}, h);
  assert.match(off, /<input type="checkbox" data-certify-after-tick>Send to Gradescope/);
  assert.match(off, /Nothing goes to Gradescope by itself/);
  assert.doesNotMatch(off, /data-certify-after-sends/);
  assert.match(off, /Nothing is published or emailed\./);
  // The server never offered it: the dialog is exactly what it was.
  assert.doesNotMatch(d.previewMarkup(PREVIEW, state, h), /data-certify-after/);
});

test("certification page: what Faculty does on Gradescope by itself after a certification", () => {
  const page = after => ({gradescope: {configured: true, after}});
  assert.equal(afterRunsMarkup({gradescope: {configured: true}}, h), "");
  assert.equal(afterRunsMarkup({gradescope: {configured: false, after: [{kind: "certified", state: "working"}]}}, h), "");
  // While it works: one quiet line with the step and what it waits for.
  const working = afterRunsMarkup(page([{kind: "certified", state: "working", doing: "Sending the papers and grades", waiting: "Waiting: Gradescope is busy; Faculty tries again in 30 s."}]), h);
  assert.match(working, /<p class="supporting-copy" role="status" data-cert-after="working">Faculty is sending this to Gradescope and publishing it by itself · Sending the papers and grades · Waiting: Gradescope is busy/);
  assert.doesNotMatch(working, /grading-warning/);
  // Done: what it published, each section it holds with why and your own Publish grades…, and what it could not send.
  const done = afterRunsMarkup(page([{kind: "certified", state: "done", published: ["507", "508"], held: [{section: 514, part: "written", reason: "Gradescope shows it 96% graded: students would see the questions nobody graded there without a score."},
    {section: 515, part: "sheets", reason: "Gradescope scored 2 bubble sheets differently from your key."}], notes: ["Section 516: its Gradescope course is not linked yet."]}]), h);
  assert.match(done, /data-cert-after="published">Faculty published the grades on Gradescope by itself: sections 507, 508\.<\/p>/);
  assert.match(done, /data-cert-after="held">Faculty did not publish section 514 on Gradescope: Gradescope shows it 96% graded[^<]* · Use Publish grades on Gradescope… at the top of Certify<\/p>/);
  assert.doesNotMatch(done, /data-cert-publish-jump|<button/);
  assert.match(done, /Faculty did not publish section 515’s bubble sheets on Gradescope: Gradescope scored 2 bubble sheets differently from your key\./);
  assert.match(done, /data-cert-after="notes">.*<li>Section 516: its Gradescope course is not linked yet\.<\/li>/);
  // A late paper's own words; a publication Gradescope refused; a run that stopped waiting.
  assert.match(afterRunsMarkup(page([{kind: "late", state: "done", published: ["515"]}]), h), /Faculty published a late paper’s grade on Gradescope by itself: section 515\./);
  assert.match(afterRunsMarkup(page([{kind: "late", state: "working", doing: "Publishing the grades"}]), h), /Faculty is publishing a late paper’s grade on Gradescope by itself · Publishing the grades/);
  assert.match(afterRunsMarkup(page([{kind: "certified", state: "done", publishing: ["507"]}]), h), /data-cert-after="publishing">Faculty is publishing the grades on Gradescope: section 507\./);
  assert.match(afterRunsMarkup(page([{kind: "certified", state: "done", failed: [{sections: ["507"], reason: "Gradescope does not show these grades published."}]}]), h), /Gradescope did not publish section 507: Gradescope does not show these grades published\. Retry it in the Gradescope panel below\./);
  assert.match(afterRunsMarkup(page([{kind: "certified", state: "done", stopped: "Faculty stopped waiting (Gradescope is busy)"}]), h), /data-cert-after="stopped">Faculty stopped its own Gradescope work: Faculty stopped waiting/);
  assert.ok(!/style=/.test(done));
  // Escaped like every other word on the page.
  assert.doesNotMatch(afterRunsMarkup(page([{kind: "certified", state: "done", notes: ["<b>x</b>"]}]), h), /<b>x<\/b>/);
  // The certified band no longer says publishing is only yours once Faculty works on it or has published; a held section leaves it.
  const certified = after => s.bandModel({certification: {state: "current", gradebook: {title: "Quiz 5"}}, gradescope: {configured: true, after}}, "Oct 12").body;
  assert.match(certified([{kind: "certified", state: "done", published: ["507"]}]), /Students see these grades on Gradescope where they are published \(what Faculty did there is said below\)/);
  assert.match(certified([{kind: "certified", state: "working"}]), /where they are published/);
  assert.match(certified([{kind: "certified", state: "done", held: [{section: 514, part: "written", reason: "x"}]}]), /only after you publish them on Gradescope \(below\)/);
  assert.match(certified(undefined), /only after you publish them on Gradescope \(below\)/);
});
