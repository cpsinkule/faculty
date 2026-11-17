import assert from 'node:assert/strict';
import test from 'node:test';
import {makeupCardsMarkup, supportMatches, officeHours, officeHoursMarkup, firstName, waitingOn, answeredMessage, SUPPORT_STATES, followingEnd, replyTarget, joinChoices, actedLine, queueNext, checkBack, checkBackSettles, prerequisiteChoices} from '../static/student-support.js';
import {studentItems, matchItems} from '../static/palette.js';

test('TEACHING-WORKFLOWS: student results preserve enrollment and never provide a stored URL', () => {
  let target;
  const rows = studentItems({groups: [{code: 'MATH 140', term_label: 'Fall 2026', term_id: 'term', students: [
    {name: 'Example, Alice', sections: [507], section_id: 'section', enrollment_id: 'enrollment'}]}]}, who => {target = who;});
  assert.equal(matchItems(rows, 'alice 507').length, 1);
  assert.equal(rows[0].href, undefined);
  rows[0].run();
  assert.deepEqual(target, {termId: 'term', sectionId: 'section', enrollmentId: 'enrollment'});
});

test('TEACHING-WORKFLOWS: filters distinguish waiting from due and resolved', () => {
  const item = {title: 'Office hours', student: {name: 'Alice', code: 'MATH 140'}, state: 'waiting', attention: false};
  assert.equal(supportMatches(item, 'active', 'alice'), true);
  assert.equal(supportMatches(item, 'attention', ''), false);
  assert.equal(supportMatches({...item, state: 'resolved'}, 'active', ''), false);
  assert.equal(supportMatches({...item, state: 'needs_reply', attention: true}, 'attention', 'office'), true);
});

// Student follow-up's office hours, a short list under the conversation queue (the painting column was withdrawn on 2026-10-04,
// DESIGN-LANGUAGE §9). The hours are the calendar's own (events titled "Office hours", campus wall clock); none is ever made up.
const event = (date, time, end_time, extra = {}) => ({date, time, end_time, title: 'Office hours', source: 'office_hours', all_day: false, location: 'BLOC 309', ...extra});

test('DESIGN-LANGUAGE: office hours are the calendar events titled Office hours, once per date and time, in order', () => {
  const rows = officeHours([
    event('2026-10-06', '13:30', '14:00', {location: ''}),
    event('2026-10-05', '16:00', '17:00'),
    event('2026-10-05', '15:00', '16:00'),
    {...event('2026-10-06', '1:30 PM', '2:00 PM'), source: 'google', title: ' office hours '},  // its Google copy: one row, its location kept
    {...event('2026-10-05', '09:00', '10:00'), title: 'Office hours prep'},
    {...event('2026-10-07', '10:00', '11:00'), source: 'google', title: 'MATH 140 · Office hours'},  // the instructor's own title (audit, 2026-10-04)
    {...event('2026-10-07', '12:00', '13:00'), source: 'google', title: 'MATH 140 · Office hours prep'},
    {...event('2026-10-05', '', ''), all_day: true, source: 'custom'},
    event('2026-10-05', 'after class', ''),
  ]);
  assert.deepEqual(rows.map(row => [row.date, row.from, row.to, row.location]),
    [['2026-10-05', 900, 960, 'BLOC 309'], ['2026-10-05', 960, 1020, 'BLOC 309'], ['2026-10-06', 810, 840, 'BLOC 309'], ['2026-10-07', 600, 660, 'BLOC 309']]);
});

test('the office hours under the queue list the week still to come, with no painting', () => {
  const e = text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const now = new Date('2026-10-05T19:00:00Z');  // 2 PM on campus (America/Chicago, CDT)
  const rows = officeHours([event('2026-10-05', '15:00', '17:00'), event('2026-10-06', '13:30', '14:00', {location: 'Online'})]);
  const html = officeHoursMarkup({rows, today: '2026-10-05'}, e, now);
  assert.match(html, /^<section class="support-hours" aria-labelledby="support-hours-title" data-patch-key="hours"><h3 id="support-hours-title" class="support-hours-title">Office hours<\/h3>/);
  assert.doesNotMatch(html, /painting|ermine/);
  assert.match(html, /<li><strong>Today<\/strong><span>3:00–5:00 PM · BLOC 309<\/span><\/li><li><strong>Tomorrow<\/strong><span>1:30–2:00 PM · Online<\/span><\/li>/);
  assert.match(html, /<a class="link-arrow" href="#office-hours">All office hours<\/a>/);
  assert.ok(!/style=/.test(html), 'no inline style attribute (CSP)');
  // Office hours that have ended today are left out.
  assert.doesNotMatch(officeHoursMarkup({rows, today: '2026-10-05'}, e, new Date('2026-10-05T23:00:00Z')), /<strong>Today/);
  assert.match(officeHoursMarkup({rows: [], today: '2026-10-05'}, e, now), /No office hours on the calendar this week\./);
  assert.match(officeHoursMarkup({rows: null, today: '2026-10-05', failed: true}, e, now), /The calendar could not be read\./);
  assert.match(officeHoursMarkup(null, e, now), /Reading the calendar…/);
});

// CHECK BACK (requirement, 2026-10-22, of follow-ups still Waiting on student long past the days they named).
test('CHECK BACK: a Waiting on student follow-up comes back on its day for Resolved or Wait a week', () => {
  const now = Date.parse('2026-10-22T16:00:00Z');
  const base = {kind: 'followup', id: 'f1', revision: 2, title: 'Take-home quiz', student: {name: 'Lennox, Zane T'}, state: 'waiting', saved_state: 'waiting',
    next: 'Zane’s paper copy of the take-home quiz in class Wednesday, October 14.'};
  assert.deepEqual(checkBack({...base, check_back: '2026-10-15'}, '2026-10-22'), {day: '2026-10-15', due: true});
  assert.deepEqual(checkBack({...base, check_back: '2026-10-29'}, '2026-10-22'), {day: '2026-10-29', due: false});
  assert.deepEqual(checkBack({...base, date: '2026-10-22', check_back: '2026-10-15'}, '2026-10-22'), {day: '2026-10-22', due: true});  // its own date
  assert.equal(checkBack({...base, state: 'needs_reply', new_reply: true, check_back: '2026-10-15'}, '2026-10-22').due, false);
  assert.equal(checkBack({...base, state: 'todo', check_back: '2026-10-15'}, '2026-10-22').due, false);
  // Back: it waits on the instructor, under its check-back line.
  assert.deepEqual(waitingOn({...base, check_back: '2026-01-02'}, now),
    {who: 'Still waiting on Zane?', what: base.next, since: 'Check back Fri, Jan 2', on: 'you'});
  assert.deepEqual(waitingOn({...base, check_back: '2099-01-02'}, now).on, 'students');
  assert.equal(checkBackSettles({...base, check_back: '2026-01-02'}), true);
  assert.equal(checkBackSettles({...base, check_back: '2099-01-02'}), false);  // not back yet
  assert.equal(checkBackSettles({...base, id: '', check_back: '2026-01-02'}), false);
  assert.equal(checkBackSettles({...base, check_back: '2026-01-02', commitments: [{id: 'c1', kind: 'work', state: 'open', waiting_on: 'you', title: 'Book it'}]}), false);
  assert.equal(checkBackSettles({...base, check_back: '2026-01-02', suggestions: {id: 'r1', state: 'ready', actions: [{id: 'a1', pending: true, status: 'proposed'}]}}), false);
});

// WAITING ON (requirement, 2026-10-04).
test('WAITING ON: each follow-up says whom it waits on and for what', () => {
  const now = Date.parse('2026-10-04T16:00:00Z');
  const base = {kind: 'followup', title: 'Arrange DR-center quizzes', student: {name: 'Weaver, Fiona E'}, attention: true};
  assert.equal(firstName('Weaver, Fiona E'), 'Fiona');
  assert.equal(firstName('Alice'), 'Alice');
  assert.equal(SUPPORT_STATES.todo, 'To do');
  const todo = waitingOn({...base, state: 'todo', next: 'Ask DR how to book her quizzes, then write back', answered: ['2026-10-03T05:26:45.000Z', 'r1']}, now);
  assert.equal(todo.who, 'Waiting on you');
  assert.equal(todo.what, 'Ask DR how to book her quizzes, then write back');
  assert.match(todo.since, /^You wrote /);
  assert.equal(waitingOn({...base, state: 'todo'}, now).what, 'Arrange DR-center quizzes');  // no next step: the purpose
  assert.deepEqual(waitingOn({...base, state: 'waiting', next: 'Her doctor’s note'}, now), {who: 'Waiting on Fiona', what: 'Her doctor’s note', since: '', on: 'students'});
  assert.equal(waitingOn({...base, state: 'waiting'}, now).what, 'A reply to your last message');
  assert.equal(waitingOn({...base, state: 'needs_reply'}, now).what, 'A reply to Fiona');
  // Replied but nothing recorded: say so rather than claim a reply is owed.
  assert.match(waitingOn({...base, state: 'needs_reply', answered: ['2026-10-03T05:26:45.000Z', 'r1']}, now).what, /^You wrote last/);
  // A newer student message reopened it: the reply comes first, and what it waited on before stays in view.
  const reopened = waitingOn({...base, state: 'needs_reply', new_reply: true, next: 'Her doctor’s note'}, now);
  assert.deepEqual([reopened.who, reopened.what, reopened.since], ['Waiting on you', 'Reply to Fiona’s new message', 'Before it: Her doctor’s note']);
  assert.match(waitingOn({...base, state: 'snoozed', date: '2026-10-08'}, now).who, /^Follow up /);
  for (const state of ['resolved', 'fyi']) assert.equal(waitingOn({...base, state}, now), null);
  assert.equal(waitingOn({...base, kind: 'appointment', state: 'scheduled'}, now), null);
});

test('WAITING ON: your reply is the instructor message the service names, never a student message', () => {
  const entries = [{message_id: 's1', author_role: 'student', roles: ['author'], text: 'I would rather take them in the DR center'},
    {message_id: 'r1', author_role: 'instructor', roles: ['to'], text: 'I’ll look into it and follow up with you.'},
    {message_id: 'c1', email_copy: 'r2', author_role: 'instructor', roles: ['to'], text: 'A Canvas copy'}];
  assert.equal(answeredMessage({answered: ['2026-10-04T05:26:45.000Z', 'r1']}, entries).text, 'I’ll look into it and follow up with you.');
  assert.equal(answeredMessage({answered: ['2026-10-04T05:26:45.000Z', 'r2']}, entries).message_id, 'c1');
  assert.equal(answeredMessage({answered: ['2026-10-04T05:26:45.000Z', 's1']}, entries), null);
  assert.equal(answeredMessage({}, entries), null);
  // WRITTEN AT ONCE (2026-10-08): an email written from the follow-up that the record has not captured yet, from Mail's own send.
  const sent = {answered: ['2026-10-08T14:59:56.826Z', ''], answered_mail: {subject: 'Thursday Meeting', text: 'We can meet in BLOC 319.'}};
  for (const read of [entries, [], undefined]) {
    const reply = answeredMessage(sent, read);
    assert.deepEqual([reply.author_role, reply.at, reply.channel_label, reply.text], ['instructor', '2026-10-08T14:59:56.826Z', 'Email', 'We can meet in BLOC 319.']);
  }
  assert.equal(answeredMessage({answered: ['2026-10-08T14:59:56.826Z', '']}, entries), null);
  assert.equal(answeredMessage({answered: ['2026-10-04T05:26:45.000Z', 'r1'], answered_mail: {text: 'ignored'}}, entries).text, 'I’ll look into it and follow up with you.');
});

// WHAT YOU DID (requirement, 2026-10-13).
test('WAITING ON: a to-do row says what you did last', () => {
  const now = Date.parse('2026-10-13T20:00:00Z'), day = /** @type {string} */ instant => new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric'}).format(new Date(instant));
  const base = {kind: 'followup', title: 'This Week', student: {name: 'Mercer, Yara'}, state: 'todo', attention: true};
  assert.equal(actedLine(base, now), '');  // nothing of theirs: the Assistant's own records are Done for you
  assert.equal(actedLine({...base, suggestions: {approved: {at: '2026-10-11T02:58:35Z', count: 2}}}, now), `You approved 2 steps · ${day('2026-10-11T02:58:35Z')}`);
  assert.match(actedLine({...base, suggestions: {approved: {at: '2026-10-11T02:58:35Z', count: 1}}}, now), /^You approved 1 step · /);
  assert.equal(actedLine({...base, answered: ['2026-10-11T02:58:35Z', 'r1']}, now), `You replied ${day('2026-10-11T02:58:35Z')}`);
  assert.equal(actedLine({...base, updated_by: 'workspace', updated_at: '2026-10-11T02:58:35Z'}, now), `You updated it ${day('2026-10-11T02:58:35Z')}`);
  assert.equal(actedLine({...base, updated_by: 'assistant', updated_at: '2026-10-11T02:58:35Z'}, now), '');  // the Assistant saved it
  // The newest of the three; the reply an approval sent, seconds later, is that approval.
  const approved = {approved: {at: '2026-10-11T02:58:35Z', count: 2}};
  assert.match(actedLine({...base, suggestions: approved, answered: ['2026-10-11T02:58:41Z', 'r1']}, now), /^You approved 2 steps/);
  assert.match(actedLine({...base, suggestions: approved, answered: ['2026-10-12T15:00:00Z', 'r2']}, now), /^You replied /);
  assert.match(actedLine({...base, suggestions: approved, updated_by: 'workspace', updated_at: '2026-10-12T15:00:00Z'}, now), /^You updated it /);
  assert.match(actedLine({...base, suggestions: approved, answered: ['2026-10-09T15:00:00Z', 'r0']}, now), /^You approved 2 steps/);
  // Today it is the time, as everywhere a message is dated.
  assert.match(actedLine({...base, suggestions: {approved: {at: '2026-10-13T19:31:00Z', count: 2}}}, now), /^You approved 2 steps · \d{1,2}:31/);
});

test('WAITING ON: a row keeps whom it waits on when the step is its title', () => {
  const now = Date.parse('2026-10-13T20:00:00Z');
  const base = {kind: 'followup', title: 'This Week', student: {name: 'Mercer, Yara'}, attention: true};
  const todo = {...base, state: 'todo', next: 'Review the obituary link'};
  // The title already is the next step: under Your to-dos "Waiting on you: <the same words>" says nothing more.
  assert.equal(queueNext(todo, 'Review the obituary link', waitingOn(todo, now)), null);
  assert.deepEqual(queueNext(todo, 'Another task', waitingOn(todo, now)), {who: 'Waiting on you', what: 'Review the obituary link'});
  // Whoever else it waits on, or what is owed, stays when the step is left out.
  const owed = {...todo, suggestions: {obligation: {work_owed: true}}};
  assert.deepEqual(queueNext(owed, 'Review the obituary link', waitingOn(owed, now)), {who: 'Work still owed', what: ''});
  const theirs = {...base, state: 'waiting', next: 'Her paper copy'};
  assert.deepEqual(queueNext(theirs, 'Her paper copy', waitingOn(theirs, now)), {who: 'Waiting on Yara', what: ''});
  const later = {...base, state: 'snoozed', date: '2026-10-15'};
  assert.match(queueNext(later, 'This Week', waitingOn(later, now)).who, /^Follow up /);
  assert.equal(queueNext(later, 'This Week', waitingOn(later, now)).what, '');
  // A student message not answered is never the title.
  const reopened = {...todo, state: 'needs_reply', new_reply: true};
  assert.deepEqual(queueNext(reopened, 'Review the obituary link', waitingOn(reopened, now)), {who: 'Waiting on you', what: 'Reply to Yara’s new message'});
  assert.equal(queueNext({...base, state: 'resolved'}, 'This Week'), null);
});

test('WAITING ON: To do needs me and stays active', () => {
  const item = {title: 'Arrange quizzes', student: {name: 'Alice'}, state: 'todo', attention: true};
  assert.equal(supportMatches(item, 'attention', ''), true);
  assert.equal(supportMatches(item, 'active', ''), true);
  assert.equal(supportMatches(item, 'todo', ''), true);
  assert.equal(supportMatches(item, 'needs_reply', ''), false);
});

test('TEACHING-WORKFLOWS MEETING LENGTH: the appointment form’s End follows Start, keeping the length it had', () => {
  assert.equal(followingEnd('15:00', '14:00', '14:30'), '15:30');  // the 30-minute default moves along
  assert.equal(followingEnd('09:15', '14:00', '15:00'), '10:15');  // a length the instructor set is kept
  assert.equal(followingEnd('10:00', '14:00', '13:00'), '10:30');  // no length to keep: 30 minutes
  assert.equal(followingEnd('23:45', '14:00', '14:30'), '23:59');  // never past the day
  assert.equal(followingEnd('', '14:00', '14:30'), '14:30');  // an unfinished Start leaves End alone
});

test('SAME MATTER: Write to student… answers an email in its own Gmail thread; a Canvas notice or no read message starts a new one', () => {
  const item = {observed: ['2026-10-07T15:15:00Z', 'm1']};
  const email = {message_id: 'm1', author_role: 'student', roles: ['author'], channel: 'gmail', subject: 'DR Quizzes', context: {thread_id: '1a11651cadf2f7b9'}};
  assert.deepEqual(replyTarget(item, [email]), {replyThreadId: '1a11651cadf2f7b9', subject: 'Re: DR Quizzes'});
  assert.deepEqual(replyTarget(item, [{...email, subject: 'RE: DR Quizzes'}]), {replyThreadId: '1a11651cadf2f7b9', subject: 'RE: DR Quizzes'});
  assert.equal(replyTarget(item, [{...email, context: {thread_id: '1a1', canvas_notice: true}}]), null);
  assert.equal(replyTarget(item, [{...email, channel: 'canvas_message', context: {}}]), null);
  assert.equal(replyTarget(item, [{...email, context: {thread_id: 'not a thread'}}]), null);
  assert.equal(replyTarget(item, []), null);
});

test('SAME MATTER: a follow-up joins only the same student\'s other conversations, never a regrade request', () => {
  const mine = {kind: 'followup', enrollment_id: 'e1', source_key: 'gmail:friday', id: ''};
  const items = [mine, {kind: 'followup', enrollment_id: 'e1', source_key: 'gmail:week', id: 'w'},
    {kind: 'followup', enrollment_id: 'e2', source_key: 'gmail:other', id: ''}, {kind: 'followup', enrollment_id: 'e1', source_key: 'gradescope_regrade:1', id: ''},
    {kind: 'followup', enrollment_id: 'e1', source_key: '', id: 'manual'}, {kind: 'appointment', enrollment_id: 'e1', source_key: '', id: 'a'}];
  assert.deepEqual(joinChoices(mine, items).map(i => i.source_key), ['gmail:week']);
  assert.deepEqual(joinChoices({...mine, source_key: ''}, items), []);
});

test('TEACHING-WORKFLOWS SEPARATE COMMITMENTS: Complete these first never offers the task itself or one that already waits on it', () => {
  const tasks = [{id: 'arrange', depends_on: []}, {id: 'send', depends_on: ['arrange']}, {id: 'confirm', depends_on: ['send']}, {id: 'other'}];
  assert.deepEqual(prerequisiteChoices(tasks, 'arrange').map(c => c.id), ['other']);
  assert.deepEqual(prerequisiteChoices(tasks, 'send').map(c => c.id), ['arrange', 'other']);
  assert.deepEqual(prerequisiteChoices(tasks, 'new').map(c => c.id), ['arrange', 'send', 'confirm', 'other']);
});

test('ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP: each quiz makeup says what its booking card did, and nothing shows without one', () => {
  const e = (/** @type {any} */ s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  assert.equal(makeupCardsMarkup({suggestions: {}}, e), "");
  assert.equal(makeupCardsMarkup({}, e), "");
  const shown = makeupCardsMarkup({suggestions: {makeup_cards: [{text: "Quiz 6 makeup: booking card emailed Fri, Oct 30, 5:29 pm from a@b.edu; no answer yet"}]}}, e);
  assert.match(shown, /Quiz 6 makeup: booking card emailed/);
  assert.match(shown, /href="#absences\?view=makeups">Open makeups/);
});
