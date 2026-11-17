import assert from 'node:assert/strict';
import test from 'node:test';
import {SHARED_STAMPS_NOTE, sharedStampLines, activityRuns, automationLine, missingPapers, waitingScans, WAITS_FOR_PLAN, archiveGroups, deliveryText, gradingWait, indexOrder, indexTab, rowDecisions, rowHref, rowStatus, stageMarks, tabFromStatus, areaFor, mayResolve} from '../static/assignment-workspace.js';
import {collectionNeedsAttention, filterCollection} from '../static/assignment-collection.js';

test('ASSIGNMENT-WORKSPACE: scoped teaching review remains Return and collection filters retain identities', () => {
  assert.equal(areaFor('#teaching-review/p'), 'return');
  assert.equal(mayResolve('#teaching-review/p'), true);
  assert.equal(mayResolve('#teaching-review'), false);
  const people = [{name: 'Sam', section_id: 's1', section_number: '501', collection: {status: 'complete'}},
    {name: 'Sam', section_id: 's2', section_number: '502', collection: {status: 'needs_review'}}];
  assert.deepEqual(filterCollection(people, {query: 'sam', section: 's2', status: 'attention'}), [people[1]]);
  assert.deepEqual(filterCollection(people, {status: 'complete'}), [people[0]]);
  // THE ZEROS ARE SAID AT CERTIFY (requirement, 2026-11-05): no scanned paper is not under "Needs you", though the status filter finds it;
  // a flagged paper still is.
  const none = {name: 'Ann', section_id: 's2', section_number: '502', collection: {status: 'no_scan'}};
  assert.equal(collectionNeedsAttention(none, true), false);
  assert.deepEqual(filterCollection([...people, none], {status: 'attention'}), [people[1]]);
  assert.deepEqual(filterCollection([...people, none], {status: 'no_scan'}), [none]);
  for (const status of ['partial', 'missing_pages', 'needs_review']) assert.equal(collectionNeedsAttention({collection: {status}}, true), true, status);
  assert.equal(collectionNeedsAttention({collection: {status: 'no_scan', issues: ['Flagged']}}, true), true);
});

// ASSIGNMENT-WORKSPACE (usability audit, 2026-10-04: the index listed assignments in the order of their internal IDs).
test('the index lists decisions first, then by date, then by title as read; archived last', () => {
  const row = (title, {archived = false, needs = 0, date = ''} = {}) =>
    ({title, archived: archived ? {certified_at: 'x'} : null, needs_you: Array(needs).fill({key: 'k'}), paper: {exam_date: date}});
  const rows = [row('Exam 3', {needs: 3}), row('Quiz 4', {needs: 1, date: '2026-10-02'}), row('Quiz 2', {archived: true}),
    row('Quiz 10', {archived: true}), row('Final', {needs: 3}), row('Exam 2', {needs: 3}), row('Exam 1', {archived: true, date: '2026-09-17'}),
    row('Quiz 5', {date: '2026-10-09'})];
  assert.deepEqual(rows.sort(indexOrder).map(r => r.title), ['Quiz 4', 'Exam 2', 'Exam 3', 'Final', 'Quiz 5', 'Exam 1', 'Quiz 2', 'Quiz 10']);
});

// ONE NAVIGATION (2026-10-05): each tab's mark, from the overview's read alone. Quiz 4 as read on live: paper and print done, 28
// without a scan, every response reviewed, certification waiting.
test('the tabs are marked done, waiting for you or failed from the assignment read', () => {
  const stages = (over = {}) => ['paper', 'print', 'collected', 'grading', 'recorded'].map(key => ({key, state: over[key] || 'idle'}));
  const quiz4 = {plan_id: 'p', lifecycle: {stages: stages({paper: 'done', print: 'done', collected: 'started', grading: 'started'})},
    needs_you: [{key: 'certify'}], workflow: {certification: {state: 'none'}, progress: {review: {total: 933, completed: 933}}}};
  assert.deepEqual(stageMarks(quiz4), {overview: {state: 'needs', count: 1}, create: {state: 'done', count: 0}, prepare: {state: 'done', count: 0},
    collect: {state: '', count: 0}, grade: {state: 'done', count: 0}, certify: {state: 'needs', count: 1}, return: {state: '', count: 0}});
  const early = {plan_id: 'p', lifecycle: {stages: stages({paper: 'done', print: 'failed'})},
    needs_you: [{key: 'preparation'}, {key: 'answer-areas'}, {key: 'scan:j:0'}, {key: 'scan:j:1'}, {key: 'review_responses'}, {key: 'ready:seating', href: '#seating/chart'}],
    workflow: {certification: {state: 'changed'}, pending: 3, progress: {review: {total: 10, completed: 4}}}};
  const marks = stageMarks(early);
  assert.deepEqual([marks.overview, marks.create, marks.prepare, marks.collect, marks.grade, marks.certify].map(m => `${m.state}${m.count || ''}`),
    ['needs6', 'failed', 'needs3', 'needs2', 'needs1', 'needs1']);
  // Without a plan nothing is claimed done for grading; failed grading reads a failure; certified is done; a read without stages marks nothing.
  assert.equal(stageMarks({lifecycle: {stages: stages()}, needs_you: [], workflow: {}}).prepare.state, '');
  assert.equal(stageMarks({plan_id: 'p', lifecycle: {stages: stages()}, needs_you: [], workflow: {failed: 2}}).grade.state, 'failed');
  assert.equal(stageMarks({plan_id: 'p', lifecycle: {stages: stages({recorded: 'done'})}, needs_you: [], workflow: {certification: {state: 'current'}}}).certify.state, 'done');
  // A changed certification with everything reviewed is listed by the server as `certify` too: one thing to do, not two (2026-10-11).
  assert.deepEqual(stageMarks({plan_id: 'p', lifecycle: {stages: stages()}, needs_you: [{key: 'certify'}],
    workflow: {certification: {state: 'changed'}, progress: {review: {total: 4, completed: 4}}}}).certify, {state: 'needs', count: 1});
  assert.deepEqual(stageMarks({lifecycle: null}), {});
});

test('recent activity says a run of the same event once, and delivery in plain words', () => {
  const at = minute => `2026-10-03T00:${minute}:00Z`;
  assert.deepEqual(activityRuns([{label: 'Gradescope send', created_at: at(47)}, {label: 'Gradescope created', created_at: at(43)}, {label: 'Gradescope created', created_at: at(43)},
    {label: 'Gradescope created', created_at: at(42)}, {kind: 'response_reviewed', created_at: at(40)}]).map(r => [r.label, r.count, r.created_at]),
    [['Gradescope send', 1, at(47)], ['Gradescope created', 3, at(43)], ['response reviewed', 1, at(40)]]);
  assert.equal(deliveryText({columns: [], publications: []}), 'Not in a gradebook column yet · not published on Gradescope');
  assert.equal(deliveryText({columns: [{}], publications: [{status: 'complete'}, {status: 'complete'}, {status: 'failed'}]}), 'In 1 gradebook column · not sent to Canvas · published on Gradescope (2 times)');
  assert.equal(deliveryText({columns: [{}], publications: [{status: 'failed'}]}), 'In 1 gradebook column · not sent to Canvas · not published on Gradescope');
  // Canvas is named once a column holds the grades (2026-10-11: one never sent read like one that was).
  const column = (receipts, without_receipt = 0) => ({recorded_cells: 30, without_receipt, receipts});
  assert.equal(deliveryText({columns: [column({}, 30)], publications: []}), 'In 1 gradebook column · not sent to Canvas · not published on Gradescope');
  assert.equal(deliveryText({columns: [column({accepted: 30})], publications: []}), 'In 1 gradebook column · sent to Canvas · not published on Gradescope');
  assert.equal(deliveryText({columns: [column({accepted: 24, changed: 2, uncertain: 1}, 3), column({accepted: 5, failed: 1})], publications: [{status: 'complete'}]}),
    'In 2 gradebook columns · sent to Canvas, 7 to check · published on Gradescope');
  // Sent with no answer yet is a send, to check; nothing accepted and nothing tried is not one.
  assert.equal(deliveryText({columns: [column({uncertain: 30})], publications: []}), 'In 1 gradebook column · sent to Canvas, 30 to check · not published on Gradescope');
  assert.equal(deliveryText({unavailable: true}), 'Could not be read');
  assert.equal(deliveryText(undefined), null);
});

// INDEX TABS (requirement, 2026-10-09).
test('the index tabs: pending until certified, back to pending when that no longer holds, archived for every earlier term', () => {
  const certified = {certified_at: '2026-10-09T01:49:45.135568+00:00', certification_id: 'c'};
  assert.equal(indexTab({is_current: 1, archived: null}), 'pending');
  assert.equal(indexTab({is_current: 1, archived: certified}), 'certified');
  // A changed certification, new work since or an open regrade request leaves `archived` null (CERTIFIED-ARCHIVE): Pending again.
  assert.equal(indexTab({is_current: 1, archived: null, workflow: {certification: {state: 'changed'}}}), 'pending');
  assert.equal(indexTab({is_current: 0, archived: certified}), 'archived');
  assert.equal(indexTab({is_current: 0, archived: null}), 'archived');
  // Earlier links named the Status select's values; anything else leaves the tab as it was.
  assert.deepEqual(['pending', 'certified', 'archived', 'current', 'all', 'bogus', null].map(tabFromStatus),
    ['pending', 'certified', 'archived', 'pending', 'archived', null, null]);
});

test('Archived is stratified by course, the latest term first inside each', () => {
  const row = (title, code, term, year, extra = {}) => ({title, code, definition_id: `d-${code}`, term, term_other: '', year, needs_you: [], paper: {}, ...extra});
  const groups = archiveGroups([row('Quiz 1', 'MATH 151', 'Spring', 2026), row('Exam 1', 'MATH 140', 'Spring', 2026),
    row('Quiz 10', 'MATH 140', 'Fall', 2025), row('Quiz 2', 'MATH 140', 'Fall', 2025), row('Final', 'MATH 140', 'Summer I', 2026),
    row('Exam 2', 'MATH 1000', 'Fall', 2025)]);
  assert.deepEqual(groups.map(g => [g.code, g.rows.map(r => `${r.title} ${r.term} ${r.year}`)]), [
    ['MATH 140', ['Final Summer I 2026', 'Exam 1 Spring 2026', 'Quiz 2 Fall 2025', 'Quiz 10 Fall 2025']],
    ['MATH 151', ['Quiz 1 Spring 2026']],
    ['MATH 1000', ['Exam 2 Fall 2025']]]);
  assert.deepEqual(archiveGroups([]), []);
});

test('a row says when its grades were certified, or why it is back in Pending', () => {
  const day = iso => iso.slice(0, 10);
  assert.deepEqual(rowStatus({archived: {certified_at: '2026-10-09T01:49:45+00:00'}, summary: 'Grades certified'}, day), ['Grades certified · 2026-10-09']);
  assert.deepEqual(rowStatus({archived: null, summary: '3 responses to review', workflow: {certification: {state: 'changed'}}}, day),
    ['Changed since certification', '3 responses to review']);
  assert.deepEqual(rowStatus({archived: null, summary: 'Ready for the next papers', workflow: {certification: {state: 'current'}}, unavailable: []}, day),
    ['Certified · new work since', 'Ready for the next papers']);
  assert.deepEqual(rowStatus({archived: null, summary: 'Some progress could not be read', workflow: {certification: {state: 'current'}}, unavailable: ['grading']}, day),
    ['Some progress could not be read']);
  assert.deepEqual(rowStatus({archived: null, summary: '', workflow: {}}, day), ['Prepare the paper and grading']);
  assert.deepEqual(rowStatus({summary: 'Ready for the next papers', needs_you: [{key: 'certify', label: 'Certify grades', count: null}]}, day), ['Certify grades']);
  assert.deepEqual(rowStatus({needs_you: [{label: 'Check answer areas', count: 4, unit: 'questions'}]}, day), ['Check answer areas · 4 questions']);
});

// 
test('a certified row opens its certification on every student; any other row opens its overview', () => {
  const row = {assignment_id: 'a 1', term_id: 't', plan_id: 'p/1', href: '#assignments/a%201/t'};
  assert.equal(rowHref({...row, archived: {certified_at: 'x'}}), '#grading/p%2F1?tab=export&open=students');
  assert.equal(rowHref({...row, archived: null, workflow: {certification: {state: 'changed'}}}), '#assignments/a%201/t');
  assert.equal(rowHref({...row, archived: {certified_at: 'x'}, plan_id: null}), '#assignments/a%201/t');
});

// COLLECT'S ACTION IS IN THE ROW (requirement, 2026-10-11): the decision is open in the row of a student still expected whose paper
// is missing or incomplete (the students Collect's lead counts as "without a scanned paper"); everyone else keeps it folded.
test('a student without a paper is decided in their own row; a settled or flagged one keeps the fold', async () => {
  const {collectionStudents, decidesInRow, papersBack} = await import('../static/assignment-collection.js');
  const person = (status, expectation = 'expected') => ({expectation, collection: {status}});
  for (const status of ['no_scan', 'partial', 'missing_pages']) assert.equal(decidesInRow(person(status)), true, status);
  for (const status of ['needs_review', 'complete', 'absent', 'excused', undefined]) assert.equal(decidesInRow(person(status)), false, String(status));
  // Already decided: an absent student's incomplete paper is not asked about again.
  assert.equal(decidesInRow(person('partial', 'absent')), false);
  assert.equal(decidesInRow({expectation: 'expected'}), false);
  // NOTHING SCANNED YET (2026-10-11): until a paper is back no row opens its decision; the scans are still to upload.
  for (const status of ['no_scan', 'partial', 'missing_pages']) assert.equal(decidesInRow(person(status), false), false, status);
  assert.equal(papersBack([person('no_scan'), person('absent', 'absent'), person('excused', 'excused'), {expectation: 'expected'}]), false);
  for (const status of ['complete', 'partial', 'needs_review', 'missing_pages']) assert.equal(papersBack([person('no_scan'), person(status)]), true, status);
  assert.equal(papersBack([]), false);
  // The row redrawn after a save is found by both permanent IDs, with the version the server now holds.
  const exam = {participants: [{section_id: 's', enrollment_id: 'e1', version: 3, expectation: 'excused'}, {section_id: 's', enrollment_id: 'e2', version: 1, expectation: 'expected'}],
    reconciliation: {students: [{section_id: 's', enrollment_id: 'e2', status: 'no_scan'}, {section_id: 's', enrollment_id: 'e1', status: 'excused'}]}};
  assert.deepEqual(collectionStudents(exam).map(p => [p.enrollment_id, p.version, p.collection.status, decidesInRow(p)]),
    [['e1', 3, 'excused', false], ['e2', 1, 'no_scan', true]]);
});

// UPLOAD THE SCANS (requirement, 2026-10-11): the item the server lists once the date has passed and no paper is back is Collect's.
test('Upload the scans marks the Collect tab', () => {
  const read = needs_you => ({lifecycle: {stages: [{key: 'collected', state: 'idle'}]}, needs_you, workflow: {}});
  assert.deepEqual(stageMarks(read([{key: 'upload'}])).collect, {state: 'needs', count: 1});
  assert.deepEqual(stageMarks(read([{key: 'upload'}, {key: 'preparation'}])).overview, {state: 'needs', count: 2});
  assert.deepEqual(stageMarks(read([])).collect, {state: '', count: 0});
});

// A MAKEUP VERSION WAITING TO BE APPROVED (2026-11-05): its item is Create's, and the index row names it with its orange block.
test('a makeup version waiting to be approved marks the Create tab and the index row', () => {
  const item = {key: 'makeup', label: 'Approve makeup version E', count: null, unit: 'setup'};
  const read = {lifecycle: {stages: [{key: 'paper', state: 'done'}, {key: 'print', state: 'done'}]}, needs_you: [item], workflow: {}};
  assert.deepEqual(stageMarks(read).create, {state: 'needs', count: 1});
  assert.deepEqual(stageMarks(read).overview, {state: 'needs', count: 1});
  assert.deepEqual(rowDecisions({needs_you: [item]}), [item]);
  assert.deepEqual(rowStatus({needs_you: [item], workflow: {}}, String), ['Approve makeup version E']);
  assert.deepEqual(rowStatus({needs_you: [item], workflow: {certification: {state: 'current'}}}, String), ['Certified · new work since', 'Approve makeup version E']);
});

// WHY GRADING WAITS (requirement, 2026-10-11): the overview says beside "N responses ready for grading" what the Grade step says.
test('the overview says why responses wait, and opens the Grade step when starting them is the instructor\'s', () => {
  const read = (auto_grade, more = {}) => ({plan_id: 'p/1', needs_you: [], workflow: {new_responses: 12}, auto_grade, ...more});
  const settled = {enabled: true, paused: null, not_started: null, settle_seconds: 120};
  const href = '#grading/p%2F1?tab=jobs';
  assert.deepEqual(gradingWait(read(settled)), {yours: false, href: '',
    text: 'Grading starts by itself about 2 minutes after the last batch finished or the last of these became ready, whichever is later.'});
  assert.deepEqual(gradingWait(read({...settled, enabled: false})), {yours: true, href, text: 'Automatic grading is off in Compute settings: start the batch yourself.'});
  assert.deepEqual(gradingWait(read({...settled, paused: 'cancelled'})), {yours: true, href,
    text: 'Automatic grading is paused because the last batch was cancelled. Starting a batch yourself resumes it.'});
  assert.deepEqual(gradingWait(read({...settled, paused: 'reset'})).text, 'Automatic grading is paused because a batch was reset since. Starting a batch yourself resumes it.');
  assert.deepEqual(gradingWait(read({...settled, not_started: {reason: 'Choose a grading model in Compute settings.'}})), {yours: true, href,
    text: 'A batch could not start by itself: Choose a grading model in Compute settings.'});
  // A batch running: these wait for their own, and say so.
  assert.match(gradingWait(read(settled, {workflow: {new_responses: 1, pending: 4}})).text, /^Not in the batch running now: it grades in its own batch/);
  // Preparation listed under Needs you: no start is promised, but a reason that is the instructor's is still said.
  const preparing = {needs_you: [{key: 'preparation'}]};
  assert.deepEqual(gradingWait(read(settled, preparing)), {yours: false, href: '', text: 'A question still in preparation holds its responses; the others start by themselves.'});
  assert.equal(gradingWait(read({...settled, paused: 'cancelled'}, preparing)).yours, true);
  // Nothing waits, or the state was not read: nothing is said.
  assert.equal(gradingWait(read(settled, {workflow: {new_responses: 0}})), null);
  assert.equal(gradingWait(read(undefined)), null);
  assert.equal(gradingWait(undefined), null);
});

test('item 9: Readiness names what runs by itself from the Compute settings', () => {
  assert.equal(automationLine(null), null);
  assert.equal(automationLine({}), 'Runs by itself: grading, flag review, late papers certify, Gradescope after Certify');
  assert.equal(automationLine({auto_flag_review: false, auto_gradescope: false}), 'Runs by itself: grading, late papers certify');
  assert.equal(automationLine({auto_grade: false, auto_flag_review: false, auto_certify: false, auto_gradescope: false}),
    'Nothing runs by itself: you start each step');
});

test('ONE COUNT OF MISSING PAPERS: no_scan once a paper is back; incomplete papers counted apart', () => {
  assert.deepEqual(missingPapers({no_scan: 30}), {back: 0, without: 0, incomplete: 0});
  assert.deepEqual(missingPapers({complete: 20, no_scan: 3, partial: 1, missing_pages: 2}), {back: 23, without: 3, incomplete: 3});
  assert.deepEqual(missingPapers(null), {back: 0, without: 0, incomplete: 0});
});

test('I2: the scans still in their scan checks, each opening its first open check', () => {
  const v = {scans: [{job_id: 'a', inbox_ready: false, filename: 'a.pdf', pages: 4, overview: {headline: 'A'}},
    {job_id: 'b', inbox_ready: true, overview: {}}, {job_id: 'c', inbox_ready: false, overview: {headline: 'C'}}],
  needs_you: [{key: 'scan:a:0', href: '#inbox/job/a/papers', label: 'Group 3 papers'}]};
  assert.deepEqual(waitingScans(v).map(s => [s.key, s.label, s.href]), [['a', 'a.pdf', '#inbox/job/a/papers'], ['c', 'C', '#inbox/job/c']]);
  assert.deepEqual(waitingScans({}), []);
  assert.deepEqual(Object.keys(WAITS_FOR_PLAN), ['grade', 'certify', 'return']);
});

// THE BADGE CAN BE READ ON THE INDEX (requirement, 2026-11-02): an assignment not started holds nothing against the instructor.
test('a row with no paper and only setup ahead is not a decision; anything else on it still is', () => {
  const steps = ['paper', 'preparation', 'ready:print'].map(key => ({key}));
  assert.deepEqual(rowDecisions({needs_you: steps}), []);
  assert.equal(rowDecisions({needs_you: [{key: 'preparation'}]}).length, 1, 'a paper exists: preparation waits for you');
  assert.equal(rowDecisions({needs_you: [...steps, {key: 'scan:j:0'}]}).length, 4, 'scans came in without a paper: they wait for you');
  assert.deepEqual(rowDecisions({}), []);
  const row = (title, needs_you) => ({title, needs_you, paper: {}});
  assert.deepEqual([row('Exam 3', steps), row('Quiz 6', [{key: 'preparation'}])].sort(indexOrder).map(r => r.title), ['Quiz 6', 'Exam 3']);
});

test('QUIZZES "SHARED STAMPS": each stamp names whose its papers are, and the page says what it cannot catch', () => {
  assert.deepEqual(sharedStampLines([{copy_number: 4, papers: [{student: 'Alice', job_id: 'j1'}, {student: 'Bob', job_id: 'j2'}]},
    {copy_number: 9, papers: [{student: 'Cam', job_id: 'j3'}, {student: '', job_id: 'j4'}]}]),
  [{label: 'Copy 4: Alice and Bob', scan: 'j2'}, {label: 'Copy 9: Cam and name not read yet', scan: 'j4'}]);
  assert.deepEqual(sharedStampLines(undefined), []);
  assert.match(SHARED_STAMPS_NOTE, /only catches copying when both papers come back/);
});
