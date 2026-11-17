import assert from 'node:assert/strict';
import test from 'node:test';
import {suggestionsMarkup, learnedMarkup, approveAllMarkup, proposalLabel, suggestionsReady, calendarMarkup, calendarWarns, changesMarkup, assumptionMarkup, doneMarkup, decisionPlanMarkup, withApproving, whileApproving, decideGrade, decideEditMarkup, gradeChoice, readingChoice, sheetEditMarkup, isRegrade, regradeApprovals} from '../static/followup-suggestions.js';
import {supportMatches, supportKey, selectedSupport, observedMessage, reviewOrder, nextReady, waitGroup, groupedOrder, queueGroup, todoSettles} from '../static/student-support.js';
import {esc, cardMarkup} from '../static/assistant.js';

const card = {id: 'a1', seen: 4, endpoint: 'gmail_mail.send', method: 'POST', path: '/api/mail/send', pending: true, status: 'proposed',
  summary: 'Reply to this question', confirmation: 'send', body: {reply_thread_id: 't1'}, page: '#follow-ups', preview: {data: {sender: 'prof@example.test', message: {
    to: ['alice@example.test'], subject: 'Question', text: 'Complete reply\n<script>untrusted</script>', attachments: [
      {route: '/api/course-notes/n1/original/notes.pdf', name: 'Notes.pdf', type: 'application/pdf', size: 1200}]}}}};

test('a local outcome keeps started and partial work out of finished steps, then shows completion', () => {
  const action = {...card, endpoint: 'canvas.content_push', status: 'succeeded', pending: false,
    events: [{detail: {result: {status: 'running'}}}], outcome: 'started'};
  // Still running: the server flags nothing for it (`problem`), and it is not yet among the finished steps.
  const item = {suggestions: {id: 'run', state: 'settled', needs_attention: false, actions: [action]}};
  assert.match(cardMarkup(action), /Started, not confirmed/);
  assert.match(cardMarkup(action), /data-tone="info"/);
  assert.doesNotMatch(suggestionsMarkup(item, 0), /Some work still needs attention/);
  assert.doesNotMatch(suggestionsMarkup(item, 0), /support-finished/);
  action.outcome = 'partial';
  item.suggestions.needs_attention = true;
  assert.match(cardMarkup(action), /Partly carried out/);
  assert.match(suggestionsMarkup(item, 0), /Some work still needs attention/);
  action.outcome = 'succeeded';
  item.suggestions.needs_attention = false;
  assert.doesNotMatch(suggestionsMarkup(item, 0), /Some work still needs attention/);
  // What the instructor cancelled before anything was done is decided: it folds away with the finished steps.
  action.outcome = 'cancelled';
  assert.match(cardMarkup(action), /Cancelled/);
  assert.match(suggestionsMarkup(item, 0), /1 step cancelled/);
});

test('a sent mail Gmail did not accept says Check delivery, whatever the server calls its outcome', () => {
  const sent = {...card, status: 'succeeded', pending: false, events: [{detail: {result: {status: 'uncertain'}}}], outcome: 'uncertain'};
  assert.match(cardMarkup(sent), /Check delivery/);
  assert.doesNotMatch(cardMarkup(sent), /Outcome uncertain/);
  assert.match(cardMarkup({...sent, outcome: 'succeeded'}), /Done/);  // a later receipt says Gmail accepted it
});

test('PLAN COMPLETION: failed settled work and watch errors still need the instructor', () => {
  const failed = {...card, pending: false, status: 'failed'};
  const item = {state: 'resolved', suggestions: {id: 'r', state: 'settled', needs_attention: true, actions: [failed]}};
  assert.equal(suggestionsReady(item), true);
  assert.match(suggestionsMarkup(item, 0), /Some work still needs attention/);
  assert.equal(suggestionsReady({suggestions: {state: 'idle', watch_attention: true}}), true);
});

test('WATCH UNTIL DONE: idle follow-ups expose controls and escape watch messages', () => {
  const html = suggestionsMarkup({suggestions: {state: 'idle', watchable: true, watches: [
    {id: 'w1', state: 'error', title: 'Check back', detail: {message: '<script>unsafe</script>'}}
  ]}}, 2);
  assert.match(html, /data-followup-item="2"/);
  assert.match(html, /Add a watch…/);
  assert.match(html, /data-followup-watch-state="active"/);
  assert.match(html, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});
const meeting = {id: 'a2', seen: 5, endpoint: 'student_support.create', method: 'POST', path: '/api/student-support', pending: true, status: 'proposed',
  summary: 'Schedule office hours', body: {kind: 'appointment', date: '2026-10-12', time: '14:00', end_time: '14:30'},
  calendar: {date: '2026-10-12', time: '14:00', end_time: '14:30', overlaps: [{title: 'MATH 140 <b>Lecture</b>', time: '14:00', end_time: '14:50', source: 'google'}], within: []}};
const item = (suggestions) => ({enrollment_id: 'e1', title: 'Question', student: {name: 'Alice'}, state: 'needs_reply', attention: true, suggestions});

test('ASSISTANT: both review locations show the same exact mail, attachments and explicit Send', () => {
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', explanation: '<img src=x onerror=bad()>', actions: [card]}), 0, esc);
  assert.match(markup, /data-assistant-approve="a1">Send/);
  assert.match(markup, /data-followup-edit="a1">Edit</);  // EDIT IN PLACE: on the card; Edit in Mail is inside the editor
  assert.doesNotMatch(markup, /data-followup-edit-mail/);
  assert.match(markup, /alice@example.test/);
  assert.match(markup, /Complete reply<br>&lt;script&gt;untrusted&lt;\/script&gt;/);
  assert.match(markup, /Notes.pdf/);
  assert.match(markup, /Ask for changes/);
  assert.match(markup, /Remember this for future drafts/);
  assert.doesNotMatch(markup, /<img|<script/);
  // FOLLOW-UP REVIEW: on the follow-up page itself, a card never links to the page already on screen.
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card]}), 0, esc), /Review on its page/);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card]}), 0, esc, {here: '#follow-ups'}), /Review on its page/);
  assert.equal(cardMarkup(card), cardMarkup(card, {}));
});

test('TEACHING-WORKFLOWS: changed sources have no approval or clarification controls', () => {
  const markup = suggestionsMarkup(item({id: 'r1', state: 'superseded', renew: true, actions: [card, meeting], question: {id: 'q1', questions: [{id:'0',title:'When?'}]}}), 0, esc);
  assert.doesNotMatch(markup, /data-assistant-approve|data-assistant-question|data-followup-revise|data-followup-approve-all|data-followup-edit|data-followup-retry/);
  assert.match(markup, /Expired/);
  assert.match(markup, /New suggestions will be prepared/);
});

test('ASSISTANT: uncertain sends stay visibly uncertain, and answers do not approve', () => {
  assert.match(cardMarkup({...card, pending:false, status:'succeeded', events:[{detail:{result:{status:'uncertain'}}}]}), /Check delivery/);
  const markup = suggestionsMarkup(item({id:'r1',state:'needs_input',actions:[],question:{id:'q1',questions:[{id:'0',title:'What time?',options:['2 pm']}] }}), 0, esc);
  assert.match(markup, /data-assistant-question="q1"/);
  assert.match(markup, /Answer and continue/);
  assert.doesNotMatch(markup, /data-assistant-approve/);
});

test('FOLLOW-UP REVIEW: Approve all names the send, the plan of steps and the calendar note', () => {
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card, meeting]}), 3, esc);
  assert.match(markup, /data-followup-approve-all>Send and approve all 2</);
  assert.match(markup, /Approve all takes the steps in order and stops at the first problem/);
  assert.match(markup, /data-followup-item="3"/);
  assert.match(markup, /<strong>2 steps wait for you\.<\/strong> Nothing happens until you approve them\./);
  assert.match(markup, /<ol class="support-plan-steps"><li><strong>Reply<\/strong> <span>· sends a message<\/span><\/li><li><strong>Office hours /);
  assert.match(markup, /Overlaps on your calendar:<\/strong> MATH 140 &lt;b&gt;Lecture&lt;\/b&gt; \(2:00–2:50 PM\)/);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [meeting]}), 0, esc), /approve-all/);
  const noMail = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [meeting, {...meeting, id: 'a3'}]}), 0, esc);
  assert.match(noMail, /data-followup-approve-all>Approve all 2</);
  assert.equal(proposalLabel({endpoint: 'student_support.create', body: {kind: 'followup', state: 'waiting'}}), 'Mark waiting on student');
  assert.equal(proposalLabel({endpoint: 'gmail_mail.send', body: {}}), 'Email');
  assert.equal(proposalLabel({endpoint: 'assistant.code_task_start', body: {title: 'x'}}), 'Change Faculty');
  assert.equal(proposalLabel({endpoint: 'frq_grading.edit_rubric', summary: 'Fix Q3 answer key'}), 'Fix Q3 answer key');
  assert.match(calendarMarkup({overlaps: [], within: [{title: 'Office Hours', time: '13:00', end_time: '15:00'}]}), /During Office Hours \(1:00–3:00 PM\)/);
  assert.match(calendarMarkup({overlaps: [{title: 'Office hours', time: '14:00', end_time: '14:30', source: 'office_hours'}], within: []}), /Another office-hours appointment/);
  // Meetings share one another's time (SCHEDULING, 2026-10-06): another student's appointment is said, unnamed, as what it falls during.
  assert.match(calendarMarkup({overlaps: [], within: [{title: 'Office hours: Sam', time: '14:00', end_time: '14:30', source: 'office_hours'}]}),
               /During another office-hours appointment \(2:00–2:30 PM\)\./);
  assert.match(calendarMarkup({overlaps: [], within: []}), /Nothing else is on your calendar then/);
  // SCHEDULING "GOOGLE CHECKED": its own warning line, shown up front like an overlap.
  const warned = {overlaps: [], within: [], unchecked: 'Faculty is not connected to <your> Google Calendar'};
  assert.match(calendarMarkup(warned), /<strong>Google Calendar not checked:<\/strong> Faculty is not connected to &lt;your&gt; Google Calendar\./);
  assert.doesNotMatch(calendarMarkup(warned), /Nothing else is on your calendar/);
  assert.equal(calendarWarns(warned), true);
  assert.equal(calendarWarns({overlaps: [], within: [], unchecked: null}), false);
});

test('FOLLOW-UP REVIEW: queue position, Prepare now, pause and the per-conversation switch', () => {
  const second = suggestionsMarkup(item({id: 'r1', state: 'queued', preparable: true, queue: {position: 2, total: 5}, actions: []}), 0, esc);
  assert.match(second, />Waiting for the Assistant</);
  assert.match(second, /2nd in line of 5/);
  assert.match(second, /data-followup-prepare>Prepare now/);
  const next = suggestionsMarkup(item({id: 'r1', state: 'queued', preparable: true, queue: {position: 1, total: 5}, actions: []}), 0, esc);
  assert.match(next, /Next in line/);
  assert.doesNotMatch(next, /data-followup-prepare/);
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'queued', preparable: true, queue: {held: true}, actions: []}), 0, esc), /Held while automatic suggestions are paused/);
  const paused = suggestionsMarkup(item({state: 'paused', preparable: true, actions: []}), 0, esc);
  assert.match(paused, /Automatic suggestions are paused/);
  assert.match(paused, /data-followup-prepare/);
  assert.match(paused, /data-followup-mute="on">Don’t suggest for this conversation/);
  const muted = suggestionsMarkup(item({state: 'muted', muted: true, actions: []}), 0, esc);
  assert.match(muted, /Suggestions are off for this conversation/);
  assert.match(muted, /data-followup-mute="off">Turn suggestions back on/);
  assert.equal(suggestionsMarkup(item({state: 'idle', actions: []}), 0, esc), '');
  const analyzing = suggestionsMarkup(item({state: 'analyzing', preparable: true, actions: []}), 0, esc);
  assert.match(analyzing, />Waiting for the conversation analysis</);
  assert.match(analyzing, /Waiting up to 10 minutes/);
  assert.match(analyzing, /data-followup-prepare>Prepare now/);
  const older = suggestionsMarkup(item({id: 'r1', state: 'ready', policies_changed: true, actions: [card]}), 0, esc);
  assert.match(older, /Your term policies changed after these were prepared/);
  assert.match(older, /data-followup-retry\b[^>]*>.*Retry suggestions/s);
  const current = suggestionsMarkup(item({id: 'r1', state: 'ready', policies_changed: false, actions: [card]}), 0, esc);
  assert.doesNotMatch(current, /term policies changed/);
  assert.match(current, /data-followup-retry/);
  // WHAT CAN MAKE A STEP WRONG (2026-10-08): what Approve refuses, then what it approves past, before the click; no second policy line.
  const changed = suggestionsMarkup(item({id: 'r1', state: 'ready', policies_changed: true, actions: [card],
    context_changes: {refuse: ['calendar availability'], warn: ['grades or due dates', 'term policies']}}), 0, esc);
  assert.match(changed, /This draft is out of date/);
  assert.match(changed, /Calendar availability changed since it was prepared/);
  assert.match(changed, /Since this was drafted, grades or due dates and term policies changed\. Retry suggestions to redraft, or approve as it is\./);
  assert.doesNotMatch(changed, /Your term policies changed/);
  const warned = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card], context_changes: {refuse: [], warn: ['student notes']}}), 0, esc);
  assert.doesNotMatch(warned, /out of date/);
  assert.match(warned, /Since this was drafted, student notes changed/);
  assert.match(suggestionsMarkup(item({state: 'queued', note: 'The Inbox worker, which queues new conversations, is not running.', actions: []}), 0, esc), /Inbox worker/);
});

test('TERM POLICIES: legacy and clarification preparations can retry without an outdated-policy flag', () => {
  for (const state of ['ready', 'needs_input', 'failed', 'interrupted', 'settled']) {
    assert.match(suggestionsMarkup(item({id: 'legacy', state, actions: []}), 0, esc), /data-followup-retry/, state);
  }
  for (const state of ['queued', 'running', 'superseded']) {
    assert.doesNotMatch(suggestionsMarkup(item({id: 'legacy', state, actions: []}), 0, esc), /data-followup-retry/, state);
  }
});

test('FOLLOW-UP REVIEW: replaced, withdrawn and declined cards, with the note form only for your own decline', () => {
  const declined = {...meeting, pending: false, status: 'declined', declined_by_you: true};
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [
    {...card, pending: false, status: 'declined', replaced: {status: 'accepted'}},
    {...meeting, id: 'w1', pending: false, status: 'expired', withdrawn: true}, declined]}), 0, esc);
  assert.match(markup, /Sent from Mail/);
  assert.match(markup, /Withdrawn/);
  assert.match(markup, /data-followup-note="a2"/);
  assert.equal((markup.match(/data-followup-note=/g) || []).length, 1);
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...declined, note: 'She has <lab> on Mondays'}]}), 0, esc), /Your note: She has &lt;lab&gt; on Mondays/);
});

test('FOLLOW-UP REVIEW: the Suggestions ready filter and a replied conversation stays in Active', () => {
  const ready = item({id: 'r1', state: 'ready', actions: [card]});
  const reviewed = item({id: 'r2', state: 'ready', actions: [{...card, pending: false, status: 'succeeded'}]});
  const asking = item({id: 'r3', state: 'needs_input', actions: [], question: {id: 'q'}});
  assert.deepEqual([ready, reviewed, asking].map(suggestionsReady), [true, false, true]);
  assert.ok(supportMatches(ready, 'suggestions', ''));
  assert.ok(!supportMatches(reviewed, 'suggestions', ''));
  assert.ok(supportMatches({...ready, state: 'replied', attention: false}, 'active', ''));
});

test('TEACHING-WORKFLOWS / IDENTITY: selection survives tracking and cannot reuse another conversation', () => {
  const first = {...item({}), id: '', source_key: 'gmail:t1'};
  const tracked = {...first, id: 'saved-followup'};
  const other = {...first, source_key: 'gmail:t2'};
  assert.equal(supportKey(first), supportKey(tracked));
  const shown = [{item: other, index: 0}, {item: tracked, index: 1}];
  assert.equal(selectedSupport(shown, supportKey(first)).index, 1);
  assert.equal(selectedSupport(shown.slice(0, 1), supportKey(first)).item, other);
  assert.equal(selectedSupport([], supportKey(first)), null);
});

test('COMMUNICATIONS: the review only displays the exact observed student author, including merged Canvas notices', () => {
  const selected = {observed: ['now', 'm1']};
  const unrelated = {message_id: 'm2', author_role: 'student', roles: ['author'], text: 'Same subject'};
  const exact = {...unrelated, message_id: 'm1', text: 'Exact message'};
  assert.equal(observedMessage(selected, [unrelated, exact]), exact);
  assert.equal(observedMessage(selected, [unrelated]), null);
  assert.equal(observedMessage(selected, [{...exact, roles: ['to']}]), null);
  const merged = {...exact, message_id: 'canvas-copy', email_copy: 'm1'};
  assert.equal(observedMessage(selected, [merged]), merged);
});

test('JEV / ASSISTANT: explanations are folded, limitations remain visible and shared sources appear once', () => {
  const source = {kind: 'api', path: '/api/calendar', fingerprint: 'sha', observed_at: 'now'};
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card], explanation: 'Long narration', evidence: [source],
    verification: [{status: 'review', partial: true, detail: 'Only part was checked', sources: [source], checks: [{verdict: 'contradicted', paragraph: 1, text: 'Claim'}]}]}), 0, esc);
  assert.match(markup, /<div class="callout support-evidence-warning" data-tone="warning" role="note"><strong class="callout-title">Evidence needs review<\/strong>/);
  assert.match(markup, /Something the Assistant wrote contradicts what it read\./);
  // EVIDENCE WARNING (requirement, 2026-10-05): uncertain or unsupported verdicts and omissions
  // raise no warning; they stay in Why the Assistant suggests this.
  const quiet = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card], explanation: 'Long narration', evidence: [source],
    verification: [{status: 'review', partial: true, detail: 'Only part was checked', sources: [source],
      checks: [{verdict: 'uncertain', paragraph: 1, text: 'Claim'}, {verdict: 'unsupported', paragraph: 2, text: 'Other claim'}]}]}), 0, esc);
  assert.doesNotMatch(quiet, /Evidence needs review/);
  assert.match(quiet, /Only part was checked/);
  assert.match(markup, /<button class="text-button" type="button" data-followup-why>See why the Assistant suggests this<\/button>/);  // opens the fold below
  assert.match(markup, /<details class="disclosure support-evidence"[^>]*><summary><span>Why the Assistant suggests this/);
  assert.equal((markup.match(/Sources · 1/g) || []).length, 1);
  assert.match(markup, /Only part was checked/);
  assert.match(markup, /Complete email draft/);
  assert.doesNotMatch(markup, /“send”|<strong>Reply to this question/);
});

test('ASSISTANT: compact review keeps wider scope and certified-work reasons', () => {
  const reason = 'Every follow-up suggestion waits for your approval. It is not limited to this follow-up’s student: check whom it affects. Its assignment’s grades were certified.';
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, reason}]}), 0, esc);
  assert.match(markup, /not limited to this follow-up’s student/);
  assert.match(markup, /grades were certified/);
  assert.doesNotMatch(markup, /Every follow-up suggestion waits/);
});

// FOLLOW-UP CLARITY (requirement, 2026-10-03).
const effect = {title: 'Schedule office hours', button: 'Schedule', where: 'Office hours', href: '#office-hours', own: true, who: 'Alice (this student)',
  changes: [{label: 'When', value: 'Mon, Oct 12, 2:00 PM–2:30 PM'}, {label: 'Purpose', value: 'Talk <b>now</b>'}],
  notes: ['Adds “Office hours” at this time to your calendar.'], audience: 'Saved in Faculty only. The student is not told.'};

test('FOLLOW-UP CLARITY: a waiting step says what it does, for whom, each field it sets and who is told, with a button named for it', () => {
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, effect}]}), 0, esc, {here: '#follow-ups'});
  assert.match(markup, /<strong>One step waits for you\.<\/strong> Nothing happens until you approve it\./);
  assert.doesNotMatch(markup, /support-plan-steps/);  // one step needs no list
  assert.match(markup, /<span class="support-step"><span class="sr-only">Step <\/span>1<\/span><strong>Schedule office hours<\/strong>/);
  assert.match(markup, /On Office hours · For Alice \(this student\)/);
  assert.match(markup, /<dt>Purpose<\/dt><dd>Talk &lt;b&gt;now&lt;\/b&gt;<\/dd>/);
  assert.match(markup, /<li>Saved in Faculty only\. The student is not told\.<\/li>/);
  assert.match(markup, /The Assistant’s note:<\/span> Schedule office hours/);
  assert.match(markup, /<details class="disclosure support-exact"><summary><span>Exact request<\/span>/);
  assert.match(markup, /data-assistant-approve="a2">Schedule<\/button><button class="text-button" type="button" data-assistant-decline="a2">Decline/);
  assert.match(markup, /href="#office-hours">Open Office hours/);
  assert.doesNotMatch(markup, /Waits for you|What it sends/);
  assert.match(markup, /Overlaps on your calendar/);
  // A revision shows what a field was and what it becomes.
  assert.equal(changesMarkup([{label: 'Status', value: 'Resolved', before: 'Needs my reply'}]),
    '<dl class="assistant-fields support-changes"><dt>Status</dt><dd><del>Needs my reply</del> <span aria-hidden="true">→</span><span class="sr-only"> becomes </span> <ins>Resolved</ins></dd></dl>');
  assert.equal(changesMarkup([]), '');
  assert.equal(proposalLabel({...meeting, effect}), 'Schedule office hours');
  // A summary first; long fields, what follows, the Assistant's note and the request wait behind "Show the whole proposal".
  const long = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, calendar: undefined,
    effect: {...effect, changes: [...effect.changes, {label: 'Details', value: 'Reported a tax-office appointment. '.repeat(4)}]}}]}), 0, esc);
  const fold = long.indexOf('<summary><span>Show the whole proposal</span></summary>');
  assert.ok(fold > long.indexOf('<dt>When</dt>') && fold > long.indexOf('<dt>Purpose</dt>'), 'short fields stay in the summary');
  for (const later of ['<dt>Details</dt>', 'class="support-follows"', 'The Assistant’s note', 'support-exact']) assert.ok(long.indexOf(later) > fold, later);
  assert.ok(long.indexOf('data-assistant-approve') > long.indexOf('</details>\n    <div class="assistant-card-actions">') - 1);
});

test('DECISION ON THE CARD: an absence card\'s decision is a dropdown on the Assistant\'s suggestion', () => {
  // requirement, 2026-10-12, of a Record an absence card: "make it into a dropdown menu that defaults to the model's suggestion,
  // but allows me to change it".
  const options = [['pending', 'Waiting for your decision'], ['excused', 'Excused'], ['not_excused', 'Not excused']].map(([value, label]) => ({value, label}));
  const line = (value, more = {}) => ({label: 'Decision', value: options.find(o => o.value === value).label, choice: {value, options, ...more}});
  const absence = (changes, more = {}) => ({id: 'a3', seen: 7, endpoint: 'absences.create', method: 'POST', path: '/api/absences', pending: true, status: 'proposed',
    summary: 'Record the absence', body: {status: 'pending'}, effect: {title: 'Record an absence', where: 'Absences', href: '#absences',
      who: 'Alice (this student)', own: true, button: 'Record absence', changes: [{label: 'Dates', value: 'Mon, Oct 12'}, ...changes], notes: []}, ...more});
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [absence([line('pending')])]}), 0, esc);
  assert.match(markup, /<dt>Decision<\/dt><dd><select class="support-field-choice" data-followup-decision="a3" data-value="pending" aria-label="Decision"><option value="pending" selected>Waiting for your decision<\/option><option value="excused">Excused<\/option><option value="not_excused">Not excused<\/option><\/select><\/dd>/);
  assert.ok(markup.indexOf('data-followup-decision') < markup.indexOf('Show the whole proposal'), 'the decision is on the summary');
  assert.match(markup, /data-action-id="a3" data-seen="7"/);  // what a choice names: the card and the version seen
  assert.doesNotMatch(markup, /Your decision\./);
  // Once the instructor chose another, the card says whose decision it is and what was suggested; chosen back, it says nothing.
  const chosen = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [absence([line('excused', {suggested: 'pending'})])]}), 0, esc);
  assert.match(chosen, /<option value="excused" selected>Excused<\/option>/);
  assert.match(chosen, /<p class="assistant-card-note support-decided">Your decision\. The Assistant suggested “Waiting for your decision”\.<\/p>/);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [absence([line('pending', {suggested: 'pending'})])]}), 0, esc), /Your decision\./);
  // A revision: what it was, then the dropdown on what it becomes.
  const revised = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [absence([{...line('excused'), before: 'Waiting for your decision'}])]}), 0, esc);
  assert.match(revised, /<del>Waiting for your decision<\/del> <span aria-hidden="true">→<\/span><span class="sr-only"> becomes <\/span> <ins><select class="support-field-choice"[^>]*>.*<option value="excused" selected>Excused<\/option>/);
  // No choosing while the preparation still runs, on a follow-up that changed, or on a step an Assistant chat prepared; a line
  // without choices, and the helper called without a card, read as words.
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'running', actions: [absence([line('pending')])]}), 0, esc), /<select/);
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'running', actions: [absence([line('pending')])]}), 0, esc), /<dt>Decision<\/dt><dd>Waiting for your decision<\/dd>/);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'superseded', actions: [absence([line('pending')])]}), 0, esc), /<select/);
  const chat = suggestionsMarkup(item({state: 'idle', chat: [absence([line('pending')])]}), 0, esc);
  assert.match(chat, /data-assistant-approve="a3">Record absence/);
  assert.doesNotMatch(chat, /<select/);
  assert.equal(changesMarkup([line('pending')]), '<dl class="assistant-fields support-changes"><dt>Decision</dt><dd>Waiting for your decision</dd></dl>');
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [absence([{label: 'Decision', value: 'Excused'}])]}), 0, esc), /<select/);
  // An option's words are escaped like any other value.
  const odd = [{value: 'x"y', label: '<b>Odd</b>'}];
  assert.match(changesMarkup([{label: 'Decision', value: 'Odd', choice: {value: 'x"y', options: odd}}], 'a"3'),
    /data-followup-decision="a&quot;3" data-value="x&quot;y" aria-label="Decision"><option value="x&quot;y" selected>&lt;b&gt;Odd&lt;\/b&gt;<\/option>/);
});

test('REGRADES "FOLLOW-UP": a regrade decision shows its reply on the summary, and keeping the grade claims no certified change', () => {
  const reply = 'Hi Hannah,\n\nYour 15.27 is correctly rounded, but the exact decrease is 168/11. Full credit requires the exact value.\n\nBest,\nChris';
  const decide = {...meeting, endpoint: 'frq_regrade_panel.decide', calendar: undefined, body: {decision: 'keep', reply},
    reason: 'Every follow-up suggestion waits for your approval. Its assignment’s grades were certified: approving changes certified work (ASSISTANT-CERTIFIED).',
    effect: {title: 'Keep the grade and answer the regrade request', own: true, who: 'Hannah (this student)', button: 'Apply', sends: true,
      changes: [{label: 'Grade', value: 'Stays 1 / 2'}, {label: 'Reply', value: reply, show: true}, {label: 'Why', value: 'The rubric requires the exact value. '.repeat(4)}],
      notes: [], audience: 'When you approve: the grade on Gradescope, the reply there.'}};
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [decide]}), 0, esc);
  const fold = markup.indexOf('<summary><span>Show the whole proposal</span></summary>');
  assert.ok(markup.indexOf('<dt>Reply</dt>') > 0 && markup.indexOf('<dt>Reply</dt>') < fold, 'the reply is on the summary');
  assert.ok(markup.indexOf('Full credit requires the exact value.') < fold);
  assert.ok(markup.indexOf('<dt>Why</dt>') > fold, 'a long reason still folds');
  assert.doesNotMatch(markup, /approving changes certified work/);
  const change = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...decide, body: {decision: 'change', reply}}]}), 0, esc);
  assert.match(change, /approving changes certified work/);
});

test('EDIT THE GRADE: a regrade decision is edited with its rubric lines, ticked as it proposes, and the reply', () => {
  // requirement, 2026-10-08, of a Keep card: "it doesn't give me the option to edit the regrade other than just the words".
  const keep = {id: 'd1', seen: 3, endpoint: 'frq_regrade_panel.decide', pending: true, body: {decision: 'keep', reply: 'Hi Francisco,\n\nYour score remains 2/4.'},
    preview: {data: {response_id: 'resp1', from: '2', of: '4'}}};
  const value = {question: {rubric: {rules: {items: [
    {id: 'R4_1', points: 4, description: 'Correct Answer: $$P(x) =36x-14000$$'}, {id: 'R4_2', points: 1, description: 'Correctly labeled the line as $$P(x) = $$'},
    {id: 'R4_4', points: 1, description: 'Correct <b>intercept</b>'}, {id: 'R4_7', points: -0.5, description: 'Sign error'}]}}},
    result: {score: {selected: ['R4_4'], adjustments: [{points: 0.5, reason: 'Misprint in part b'}]}}, review: null};
  const grade = decideGrade(keep, value);
  assert.deepEqual([grade.current, [...grade.ticked], grade.kept, grade.adjustment], [['R4_4'], ['R4_4'], [{points: 0.5, reason: 'Misprint in part b'}], null]);
  // A card proposed with the grade now in its preview takes it from there; a change ticks the lines it names.
  const change = {...keep, body: {...keep.body, decision: 'change', lines: ['R4_2', 'R4_4'], adjustment: {points: 1, reason: 'x'}},
    preview: {data: {...keep.preview.data, current: {lines: ['R4_4'], adjustments: []}}}};
  assert.deepEqual([[...decideGrade(change, value).ticked], decideGrade(change, value).kept, decideGrade(change, value).adjustment], [['R4_2', 'R4_4'], [], {points: 1, reason: 'x'}]);
  assert.equal(decideGrade(keep, {...value, hidden: true, result: {}}), null);  // a blind grade is not guessed

  const form = decideEditMarkup(keep, 1, {value});
  assert.match(form, /Edit the regrade decision/);
  assert.match(form, /data-decide-current="R4_4"/);
  assert.match(form, /Now 2 \/ 4/);
  assert.match(form, /value="R4_4" checked>/);
  assert.match(form, /value="R4_2">\s*<span class="support-decide-points">\+1<\/span> <span>2\. Correctly labeled the line as P\(x\) =<\/span>/);
  assert.match(form, /−0\.5/);
  assert.match(form, /Correct &lt;b&gt;intercept&lt;\/b&gt;/);
  assert.doesNotMatch(form, /<b>intercept/);
  assert.match(form, /keeps its point adjustment: \+0\.5 Misprint in part b/);
  assert.match(form, /Your score remains 2\/4\.<\/textarea>/);
  assert.match(decideEditMarkup(keep, 1, {}), /Reading the rubric…/);
  assert.doesNotMatch(decideEditMarkup(keep, 1, {}), /data-decide-current/);  // the reply alone until the rubric is read
  assert.match(decideEditMarkup(keep, 1, {error: 'Offline.'}), /could not be read: Offline\. You can still edit the reply/);

  assert.deepEqual(gradeChoice(['R4_4'], ['R4_4'], '', ''), {decision: 'keep'});
  assert.deepEqual(gradeChoice(['R4_4'], ['R4_4', 'R4_2'], '', ''), {decision: 'change', lines: ['R4_2', 'R4_4']});
  assert.deepEqual(gradeChoice(['R4_4'], ['R4_4'], ' 0.5 ', 'Partial setup'), {decision: 'change', lines: ['R4_4'], adjustment: {points: 0.5, reason: 'Partial setup'}});
  assert.throws(() => gradeChoice(['R4_4'], ['R4_4'], '1', ''), /needs points other than 0 and why/);
  assert.throws(() => gradeChoice(['R4_4'], ['R4_4'], '0', 'x'), /needs points other than 0/);
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...keep, method: 'POST', path: '/x', status: 'proposed',
    effect: {title: 'Keep the grade and answer the regrade request', changes: [{label: 'Grade', value: 'Stays 2 / 4'}]}}]}), 0, esc);
  assert.match(markup, /data-followup-edit="d1">Edit grade or reply</);
});

test('FOLLOW-UP CLARITY: another student, certified work and an unread scope stay said; mail and code cards keep their own previews', () => {
  const certified = 'Every follow-up suggestion waits for your approval. Its assignment’s grades were certified: approving changes certified work (ASSISTANT-CERTIFIED).';
  const other = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, reason: certified, effect: {...effect, own: false, who: 'Bob'}}]}), 0, esc);
  assert.match(other, /This changes a record of Bob, not this follow-up’s student\. Check before approving\./);
  assert.match(other, /grades were certified: approving changes certified work/);
  // WIDE SCOPE (2026-10-15): another student's card says why on its face, once, not only under "Show the whole proposal".
  const why = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, summary: 'For Bob only: hold the card the date fix would send.',
    effect: {...effect, own: false, who: 'Bob'}}]}), 0, esc);
  const face = why.slice(0, why.indexOf('support-whole'));
  assert.match(face, /The Assistant’s note:<\/span> For Bob only: hold the card the date fix would send\./);
  assert.equal(why.split('The Assistant’s note:').length, 2);
  const own = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, summary: 'For this student.', effect}]}), 0, esc);
  assert.doesNotMatch(own.slice(0, own.indexOf('support-whole')), /The Assistant’s note/);
  assert.match(own, /The Assistant’s note:<\/span> For this student\./);
  const unknown = 'Every follow-up suggestion waits for your approval. It is not limited to this follow-up’s student: check whom it affects.';
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, reason: unknown, effect: {...effect, own: null, who: ''}}]}), 0, esc),
    /It is not limited to this follow-up’s student: check whom it affects\./);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, reason: unknown, effect}]}), 0, esc), /not limited/);
  const mail = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...card, effect: {title: 'Reply by email', sends: true,
    audience: 'Goes only to the recipients above, and only when you press Send.'}}]}), 0, esc);
  assert.match(mail, /<span class="support-step"><span class="sr-only">Step <\/span>1<\/span><strong>Reply by email<\/strong>/);
  assert.match(mail, /Goes only to the recipients above, and only when you press Send\. Sent from prof@example\.test\./);
  assert.match(mail, /data-assistant-approve="a1">Send/);
  // Finished cards keep their outcome chip; a card without a reading (an older route) shows as the Assistant wrote it.
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...meeting, pending: false, status: 'succeeded', effect}]}), 0, esc), /Done/);
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [meeting]}), 0, esc), /data-assistant-approve="a2">Approve/);
});

test('FOLLOW-UP CLARITY: a round that follows through on approved steps says so', () => {
  const markup = suggestionsMarkup(item({id: 'r2', state: 'ready', reason: 'receipts', actions: [{...meeting, effect}]}), 0, esc);
  assert.match(markup, /Follow-through on the steps you approved: work that needed their results\. <strong>One step waits for you\./);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', reason: 'automatic', actions: [{...meeting, effect}]}), 0, esc), /Follow-through/);
});

test('FOLLOW-UP CLARITY: an open question comes first and says that answering replaces the steps not yet approved', () => {
  const markup = suggestionsMarkup(item({id: 'r1', state: 'needs_input', actions: [{...meeting, effect}],
    question: {id: 'q1', questions: [{id: '0', title: 'Which room?', options: ['BLOC 306']}]}}), 0, esc);
  assert.ok(markup.indexOf('data-assistant-question') < markup.indexOf('data-assistant-approve'));
  assert.match(markup, /Answer this first\. The Assistant prepares again with your answer, replacing the step below that you have not approved\./);
  assert.match(markup, /data-question-option="0" data-answer="BLOC 306">BLOC 306<\/button>/);
  assert.match(markup, /placeholder="Or write your own answer"/);  // choosing an option sends it; the box is for anything else
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'needs_input', actions: [], question: {id: 'q1', questions: [{id: '0', title: 'When?', options: []}]}}), 0, esc),
    /Answer this first/);
});

test('ASSUME, DON\'T ASK: a waiting card shows the guess and its alternatives, each a button that asks for changes', () => {
  const guessed = {...meeting, effect, assumption: {text: 'Monday works <b>for her</b>', alternatives: ['Tuesday', 'Wednesday']}};
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [guessed]}), 0, esc);
  assert.match(markup, /<div class="support-assumption" role="note"><p class="support-assumption-label">Assumed<\/p><p class="support-assumption-text">Monday works &lt;b&gt;for her&lt;\/b&gt;<\/p><p class="support-assumption-lead">Not right\? Prepare again instead with:<\/p><ul class="support-choices" role="list"><li><button class="support-choice" type="button" data-followup-instead="a2" data-alternative="0">Tuesday<\/button><\/li><li><button class="support-choice" type="button" data-followup-instead="a2" data-alternative="1">Wednesday<\/button><\/li><\/ul><\/div>/);
  // READABLE REVIEW: the guess sits right above the step's buttons, after what the step sets; a choice drops its closing full stop.
  assert.ok(markup.indexOf('support-assumption') > markup.indexOf('support-whole') && markup.indexOf('support-assumption') < markup.indexOf('data-assistant-approve'));
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...guessed, assumption: {text: 'X', alternatives: ['Ask for a note.']}}]}), 0, esc),
    /data-alternative="0">Ask for a note<\/button>/);
  assert.equal(assumptionMarkup({...guessed, pending: false}), '');  // a decided card shows no guess to change
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...card, assumption: {text: 'She meant Quiz 4', alternatives: []}}]}), 0, esc),
    /<p class="support-assumption-label">Assumed<\/p><p class="support-assumption-text">She meant Quiz 4<\/p><\/div>/);  // mail cards carry it too
});

test('FOLLOW-UP REVIEW ORDER: wellbeing, then replies to send, then other cards, then questions; next ready conversation wraps', () => {
  const mk = (key, extra) => ({item: {enrollment_id: 'e', source_key: key, title: key, ...extra}, index: 0});
  const shown = [mk('plain', {}), mk('question', {suggestions: {state: 'needs_input', actions: [], question: {id: 'q'}}}),
    mk('cards', {suggestions: {state: 'ready', actions: [{...meeting, pending: true}]}}),
    mk('reply', {suggestions: {state: 'ready', actions: [{...card, pending: true}]}}), mk('worry', {wellbeing: {call_id: 'c'}})];
  assert.deepEqual(reviewOrder(shown).map(({item}) => item.title), ['worry', 'reply', 'cards', 'question', 'plain']);
  const ordered = reviewOrder(shown);
  assert.equal(nextReady(ordered, 'e:reply').item.title, 'cards');
  assert.equal(nextReady(ordered, 'e:question').item.title, 'reply');  // wraps past the end
  assert.equal(nextReady([mk('only', {})], 'e:only'), null);
});

test('DONE FOR YOU: records the Assistant made by itself are listed with Undo, else their state or a link to their page', () => {
  const done = [
    {id: 'd1', run: 'r1', seen: 7, title: 'Recorded an absence', detail: 'Fri, Oct 2 · Illness <b>', state: 'done', undo: true, href: '#absences'},
    {id: 'd2', run: 'r0', seen: 3, title: 'Put office hours on your calendar', detail: 'Mon, Oct 12', state: 'done', undo: false, href: '#office-hours', why: 'It changed since.'},
    {id: 'd3', run: 'r0', seen: 2, title: 'Attached note.pdf to the absence record', detail: '', state: 'undone', undo: false, href: '#absences'},
  ];
  const markup = suggestionsMarkup(item({id: 'r1', state: 'settled', actions: [], done}), 0, esc);
  assert.match(markup, /<h5 class="support-done-title">Done for you<\/h5>/);
  assert.match(markup, /<strong>Recorded an absence<\/strong> · Fri, Oct 2 · Illness &lt;b&gt;<\/span> <button class="text-button" type="button" data-followup-undo="d1" data-run="r1" data-seen="7">Undo<\/button>/);
  assert.match(markup, /<a class="link-arrow" href="#office-hours" title="It changed since\.">Open its page<\/a>/);
  assert.match(markup, /data-undone="true"><span><strong>Attached note\.pdf to the absence record<\/strong><\/span> <span class="chip" data-tone="neutral">Undone<\/span>/);
  assert.equal(doneMarkup({done: []}), '');
});

test('DONE FOR YOU: a scheduled status reads in the present with Don\'t; a conversation with waiting cards stays in Active and Needs me', () => {
  const markup = doneMarkup({done: [{id: 's1', run: 'r1', seen: 4, title: 'Marked this follow-up resolved', plan: 'Mark this follow-up resolved', state: 'scheduled', undo: true}]});
  assert.match(markup, /After you send the reply: <strong>Mark this follow-up resolved<\/strong><\/span> <button class="text-button" type="button" data-followup-undo="s1" data-run="r1" data-seen="4">Don’t<\/button>/);
  assert.match(doneMarkup({done: [{id: 's2', run: 'r1', seen: 5, title: 'Marked it', state: 'cancelled'}]}), /data-undone="true"[\s\S]*>Not done</);
  const resolved = {...item({id: 'r1', state: 'ready', actions: [{...meeting, pending: true}]}), state: 'resolved', attention: false};
  assert.ok(supportMatches(resolved, 'active', ''));
  assert.ok(supportMatches(resolved, 'attention', ''));
  assert.ok(!supportMatches({...resolved, suggestions: {state: 'settled', actions: []}}, 'active', ''));
});

test('STALE SUGGESTIONS: a superseded set that nothing renews offers Suggest again; one that renews by itself does not', () => {
  const again = /data-followup-prepare>Suggest again for the conversation as it is now</;
  assert.match(suggestionsMarkup(item({id: 'r9', state: 'superseded', preparable: true, renew: false, actions: [meeting]}), 0, esc), again);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r9', state: 'superseded', preparable: true, renew: true, actions: [meeting]}), 0, esc), again);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r9', state: 'superseded', preparable: false, actions: [meeting]}), 0, esc), again);
});

test('READABLE REVIEW: Done for you sits under the steps that wait and leads when nothing waits', () => {
  const done = [{id: 'd1', run: 'r1', seen: 7, title: 'Recorded an absence', detail: '', state: 'done', undo: true}];
  const waiting = suggestionsMarkup(item({id: 'r1', state: 'ready', done, actions: [meeting]}), 0, esc);
  assert.ok(waiting.indexOf('support-done-title') > waiting.indexOf('data-assistant-approve'));
  const quiet = suggestionsMarkup(item({id: 'r1', state: 'settled', done, actions: []}), 0, esc);
  assert.ok(quiet.indexOf('support-done-title') < quiet.indexOf('data-followup-retry'));
});

test('DONE FOR YOU filter: conversations with something done or scheduled by the Assistant, not undone or cancelled ones', () => {
  const base = {...item({id: 'r1', state: 'settled', actions: []}), state: 'resolved', attention: false};
  const done = state => ({...base, suggestions: {...base.suggestions, done: [{id: 'd1', title: 'Marked this follow-up resolved', state}]}});
  assert.ok(supportMatches(done('done'), 'done', ''));
  assert.ok(supportMatches(done('scheduled'), 'done', ''));
  assert.ok(!supportMatches(done('undone'), 'done', ''));
  assert.ok(!supportMatches(done('cancelled'), 'done', ''));
  assert.ok(!supportMatches(base, 'done', ''));
});

test('FINISHED STEPS: with nothing waiting, decided cards fold into one counted line; failed and unconfirmed cards stay in view', () => {
  const done = (id, extra = {}) => ({...meeting, id, pending: false, status: 'succeeded', ...extra});
  const warning = [{status: 'review', partial: true, sources: [], checks: [{verdict: 'contradicted', paragraph: 1, text: 'Claim'}]}];
  const settled = suggestionsMarkup(item({id: 'r1', state: 'settled', verification: warning, actions: [done('d1'), done('d2'),
    {...meeting, id: 'x1', pending: false, status: 'declined', declined_by_you: true},
    {...card, id: 's1', pending: false, status: 'succeeded', events: [{detail: {result: {status: 'accepted'}}}]},
    {...card, id: 'm1', pending: false, status: 'declined', replaced: {status: 'accepted'}}]}), 0, esc);
  assert.match(settled, /<details class="disclosure support-finished" data-patch-key="finished-r1"><summary><span>4 steps done · 1 declined<\/span>/);
  const fold = settled.slice(settled.indexOf('support-finished'));
  for (const id of ['d1', 'd2', 'x1', 's1', 'm1']) assert.match(fold, new RegExp(`data-action-id="${id}"`), id);
  assert.match(fold, /data-followup-note="x1"/);  // the decline note stays one click away
  assert.doesNotMatch(settled, /Evidence needs review/);
  assert.match(settled, /data-followup-retry/);
  assert.match(settled, /data-followup-revise/);
  // A failed card, a send not confirmed and a Mail replacement not confirmed stay out of the fold.
  const trouble = suggestionsMarkup(item({id: 'r2', state: 'settled', actions: [done('d1'), {...meeting, id: 'f1', pending: false, status: 'failed'},
    {...card, id: 'u1', pending: false, status: 'succeeded', events: [{detail: {result: {status: 'uncertain'}}}]},
    {...card, id: 'm2', pending: false, status: 'declined', replaced: {status: 'uncertain'}}]}), 0, esc);
  const head = trouble.slice(0, trouble.indexOf('support-finished'));
  for (const id of ['f1', 'u1', 'm2']) assert.match(head, new RegExp(`data-action-id="${id}"`), id);
  assert.match(trouble, /<summary><span>1 step done<\/span>/);
  // While a card waits or a question is open, nothing folds and the warning stays.
  const waiting = suggestionsMarkup(item({id: 'r3', state: 'ready', verification: warning, actions: [done('d1'), meeting]}), 0, esc);
  assert.doesNotMatch(waiting, /support-finished/);
  assert.match(waiting, /Evidence needs review/);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r4', state: 'needs_input', actions: [done('d1')], question: {id: 'q1', questions: [{id: '0', title: 'When?', options: []}]}}), 0, esc),
    /support-finished/);
  // A follow-up that changed: expired proposals fold with the done ones.
  assert.match(suggestionsMarkup(item({id: 'r5', state: 'superseded', actions: [done('d1'), meeting, card]}), 0, esc), /<summary><span>1 step done · 2 expired<\/span>/);
});

test('THE DECISION FIRST (DESIGN-LANGUAGE §1.4, Ledger): Student follow-up leads with Approve all; the steps then leave it out', () => {
  const ready = item({id: 'r1', state: 'ready', actions: [card, meeting]});
  const lead = approveAllMarkup(ready);
  assert.match(lead, /data-followup-approve-all>Send and approve all 2</);
  assert.match(lead, /Approve all takes the steps in order and stops at the first problem/);
  const steps = suggestionsMarkup(ready, 0, esc, {lead: true});
  assert.doesNotMatch(steps, /data-followup-approve-all|Approve all takes the steps/);
  assert.match(steps, /Retry suggestions/);
  // One step, a superseded run or nothing suggested: no Approve all anywhere.
  assert.equal(approveAllMarkup(item({id: 'r1', state: 'ready', actions: [meeting]})), '');
  assert.equal(approveAllMarkup(item({id: 'r1', state: 'superseded', actions: [card, meeting]})), '');
  assert.equal(approveAllMarkup(item(null)), '');
});

test('FOLLOW-UP QUEUE GROUPS: what is new to review first, then your to-dos, then students, later dates, nothing; review order within', () => {
  const waiting = item({id: 'r1', state: 'ready', actions: [card, meeting]});
  const mine = {...item(null), kind: 'followup', state: 'todo', next: 'Ask DR'};
  const theirs = {...item(null), kind: 'followup', state: 'waiting', next: 'Her note'};
  const later = {...item(null), kind: 'followup', state: 'snoozed', date: '2026-10-09'};
  const done = {...item(null), kind: 'followup', state: 'resolved'};
  assert.deepEqual([waiting, mine, theirs, later, done, {...done, wellbeing: true}].map(waitGroup), ['you', 'you', 'students', 'later', 'none', 'you']);
  const shown = [done, theirs, later, mine, waiting].map((it, index) => ({item: it, index}));
  // WHAT YOU DID (requirement, 2026-10-13): what
  // waits on the instructor is in two groups, the steps still to review before the To do they already took on.
  assert.deepEqual([waiting, mine, theirs, later, done, {...done, wellbeing: true}].map(queueGroup), ['review', 'todo', 'students', 'later', 'none', 'review']);
  assert.deepEqual(groupedOrder(shown).map(({index}) => index), [4, 3, 1, 2, 0]);
  // A To do's work or reply still owed is the instructor's, whatever its label reads (requirement, 2026-10-11).
  for (const obligation of [{work_owed: true}, {reply_owed: true}, {work_owed: true, reply_owed: true}])
    assert.equal(waitGroup({...mine, suggestions: {obligation}}), 'you');
  assert.equal(waitGroup({...theirs, new_reply: true}), 'you');  // a newer student message: the reply is the instructor's
});

test('FOLLOW-UP QUEUE GROUPS: a To do you acted on is under Your to-dos; steps, a question or an unanswered message under To review', () => {
  const todo = {...item(null), id: 'i1', kind: 'followup', state: 'todo', saved_state: 'todo', next: 'Look for the take-home quiz'};
  assert.equal(queueGroup(todo), 'todo');
  for (const obligation of [{work_owed: true}, {reply_owed: true}])
    assert.equal(queueGroup({...todo, suggestions: {obligation}}), 'todo');  // whatever its line reads, as `waitGroup`
  assert.equal(queueGroup({...todo, suggestions: {state: 'settled', approved: {at: '2026-10-13T13:31:00Z', count: 2}, actions: []}}), 'todo');
  // The work split into tasks: still the instructor's own, nothing new to decide.
  assert.equal(queueGroup({...todo, commitments: [{id: 'c1', kind: 'work', state: 'open', waiting_on: 'you', title: 'Look for the quiz'}]}), 'todo');
  // Something new to decide: steps that wait, a question, a wellbeing flag, a student message not answered.
  assert.equal(queueGroup({...todo, suggestions: {id: 'r1', state: 'ready', actions: [card]}}), 'review');
  assert.equal(queueGroup({...todo, suggestions: {id: 'r1', state: 'needs_input', question: {text: 'Which quiz?'}, actions: []}}), 'review');
  assert.equal(queueGroup({...todo, wellbeing: {probability: .9}}), 'review');
  assert.equal(queueGroup({...todo, state: 'needs_reply', new_reply: true}), 'review');
  assert.equal(queueGroup({...item(null), kind: 'followup', id: ''}), 'review');  // an untracked conversation the student wrote last
  // They wrote last and nothing was recorded: dealt with, not new.
  assert.equal(queueGroup({...todo, state: 'needs_reply', saved_state: 'needs_reply', answered: ['2026-10-12T01:37:07Z', 'r1']}), 'todo');
  // ANSWERED: a new message the instructor answered keeps the saved status, so the row is wherever that status puts it.
  assert.equal(queueGroup({...todo, state: 'waiting', saved_state: 'waiting', new_reply_answered: true}), 'students');
  assert.equal(queueGroup({...todo, state: 'resolved', saved_state: 'resolved', new_reply_answered: true}), 'none');
  // ONE CLICK ON A TO DO: Done and Now waits on the student, unless steps wait or the work is split into tasks.
  assert.equal(todoSettles(todo), true);
  assert.equal(todoSettles({...todo, id: ''}), false);
  assert.equal(todoSettles({...todo, state: 'needs_reply', new_reply: true}), false);
  assert.equal(todoSettles({...todo, suggestions: {id: 'r1', state: 'ready', actions: [card]}}), false);
  assert.equal(todoSettles({...todo, commitments: [{id: 'c1', kind: 'work', state: 'open', waiting_on: 'you', title: 'Look for the quiz'}]}), false);
  assert.equal(todoSettles({...todo, state: 'waiting', saved_state: 'waiting'}), false);
});

test('ASK AFTER AN EDIT: a status held for the instructor says why on its card, an edited reply or a changed decision', () => {
  const waits = 'Every follow-up suggestion waits for your approval.';
  const status = {id: 'a7', seen: 5, endpoint: 'student_support.revise', method: 'POST', path: '/api/student-support/i1', pending: true, status: 'proposed',
    summary: 'Mark this follow-up To do', body: {state: 'todo', next: 'Decide the absence'}, page: '#follow-ups',
    effect: {title: 'Mark this follow-up To do', where: 'Follow-up', own: true, changes: [{label: 'Status', value: 'To do'}], button: 'Mark To do'}};
  const edited = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...status, reason: `${waits} You edited the reply, so this no longer happens by itself.`}]}), 0, esc);
  assert.match(edited, /support-scope" role="note">You edited the reply, so this no longer happens by itself\. Check it still fits your version\./);
  assert.doesNotMatch(edited, /You changed the absence decision/);
  // ASK AFTER A CHANGED DECISION (2026-10-13): Excused chosen on the absence card of a round whose status said "decide the absence".
  const decided = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...status, reason: `${waits} You changed the decision on the absence card, so this no longer happens by itself.`}]}), 0, esc);
  assert.match(decided, /support-scope" role="note">You changed the absence decision, so this no longer happens by itself\. Check it still fits your decision\./);
  assert.doesNotMatch(decided, /You edited the reply/);
  // A DECIDED STEP IS NOT CARRIED: its card says why it no longer asks for the decision.
  const settled = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...status, reason: `${waits} You chose the decision it was waiting for on the absence card, so it no longer asks you to decide it.`}]}), 0, esc);
  assert.match(settled, /support-scope" role="note">You decided this on the absence card, so it no longer asks you to decide it\. Check nothing else is left\./);
  assert.doesNotMatch(settled, /You changed the absence decision/);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [{...status, reason: waits}]}), 0, esc), /no longer happens by itself/);
});

test('APPROVING: steps sent for approval read Approving… in every refresh until the request returns, with no second Approve all', async () => {
  const ready = {...item({id: 'r1', state: 'ready', actions: [card, meeting]}), kind: 'followup'};
  const items = [ready];
  assert.equal(withApproving(items), items);  // nothing being approved: the read is used as it is
  let finish = (/** @type {any} */ _) => {}, redraws = 0;
  const sent = whileApproving(['a1', 'a2'], () => new Promise((resolve) => { finish = resolve; }), () => { redraws += 1; });
  assert.equal(redraws, 1);  // drawn again at once
  const [during] = withApproving(items);  // a refresh that lands mid-run (the instructor's report, 2026-10-06)
  assert.deepEqual(during.suggestions.actions.map((a) => [a.pending, a.status]), [[false, 'approving'], [false, 'approving']]);
  assert.equal(ready.suggestions.actions[0].pending, true);  // the read itself is not changed
  assert.equal(approveAllMarkup(during), '');
  assert.equal(suggestionsReady(during), false);
  assert.equal(waitGroup(during), 'you');  // it keeps its place in the queue
  assert.equal(queueGroup(during), 'review');  // and its group, though nothing is left to approve
  assert.match(decisionPlanMarkup(during), /Approving 2 steps…<\/strong> They go in order\. This page updates when they finish\./);
  assert.doesNotMatch(decisionPlanMarkup(during), /wait for you/);
  const steps = suggestionsMarkup(during, 0, esc, {lead: true});
  assert.doesNotMatch(steps, /data-assistant-approve|data-assistant-decline|data-followup-approve-all|data-followup-edit=/);
  assert.equal(steps.match(/Approving…/g)?.length, 3);  // the section's status and each card's chip
  assert.doesNotMatch(steps, /steps done/);  // not folded away as finished
  const changes = suggestionsMarkup(during, 0, esc);  // the Assistant's Changes tab: the same, its plan included
  assert.match(changes, /Approving 2 steps…/);
  assert.doesNotMatch(changes, /data-followup-approve-all/);
  // A step the server already moved on is shown as the server says; a card not sent still waits beside the one being approved.
  const [moved] = withApproving([item({id: 'r1', state: 'ready', actions: [{...card, pending: false, status: 'succeeded'}, meeting]})]);
  assert.deepEqual(moved.suggestions.actions.map((a) => a.status), ['succeeded', 'approving']);
  finish({results: []});
  assert.deepEqual(await sent, {results: []});
  assert.equal(withApproving(items), items);  // back to the server's record
  // One step's own Approve: the rest still wait, counted apart.
  const one = whileApproving(['a2'], () => Promise.resolve('done'));
  const [single] = withApproving(items);
  assert.match(decisionPlanMarkup(single), /Approving one step…<\/strong> This page updates when it finishes\.[\s\S]*One step waits for you\./);
  assert.equal(await one, 'done');
  // A failed request clears its mark too.
  await assert.rejects(whileApproving(['a1'], () => Promise.reject(new Error('Network'))), /Network/);
  assert.equal(withApproving(items), items);
});

test('APPROVING: a card the server is still running is Approving… here, not "Some work still needs attention"', async () => {
  // (a
  // regrade decision's approval ran 51 s; the server counts an approved card with no result yet as unconfirmed).
  const running = {...card, id: 'a9', endpoint: 'frq_regrade_panel.decide', pending: false, status: 'approved', events: [{kind: 'approved'}]};
  const read = [item({id: 'r9', state: 'settled', needs_attention: true, actions: [running]})];
  assert.match(suggestionsMarkup(read[0], 0, esc), /Some work still needs attention/);  // from another browser, or once it stopped
  let finish = (/** @type {any} */ _) => {};
  const sent = whileApproving(['a9'], () => new Promise((resolve) => { finish = resolve; }));
  const [during] = withApproving(read);
  assert.deepEqual(during.suggestions.actions.map((a) => a.status), ['approving']);
  const markup = suggestionsMarkup(during, 0, esc);
  assert.doesNotMatch(markup, /Some work still needs attention/);
  assert.match(markup, /Approving…/);
  // Another card that failed still says so beside the one being approved.
  const failed = {...card, id: 'a8', pending: false, status: 'failed', events: [{kind: 'failed'}]};
  const [both] = withApproving([item({id: 'r9', state: 'settled', needs_attention: true, actions: [running, failed]})]);
  assert.match(suggestionsMarkup(both, 0, esc), /Some work still needs attention/);
  finish('done');
  assert.equal(await sent, 'done');
  assert.match(suggestionsMarkup(withApproving(read)[0], 0, esc), /Some work still needs attention/);  // the server's record again
});

test('SAME MATTER: a conversation Jev joined is listed under Done for you with Keep separate', () => {
  const markup = doneMarkup({done: [{id: 'join-1', run: '', seen: 0, title: 'Joined “Friday Quiz/Class” to this follow-up', detail: 'Jev read it as the same matter',
    state: 'done', separate: 'gmail:friday'}]}, 3);
  assert.match(markup, /<strong>Joined “Friday Quiz\/Class” to this follow-up<\/strong> · Jev read it as the same matter<\/span> <button class="text-button" type="button" data-support-separate="3" data-key="gmail:friday">Keep separate<\/button>/);
  assert.doesNotMatch(markup, /data-followup-undo/);
});

test('JEV EVIDENCE / TOOL-USE (2026-10-09 review): both kept checks show, each labelled, and a contradiction still warns', () => {
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card], explanation: 'Monday is open.', verification: [
    {status: 'review', detail: 'A claim contradicts a source.', sources: [], checks: [{verdict: 'contradicted', paragraph: 1, text: 'Monday is open.'}]},
    {check_type: 'tool_use', status: 'checked', detail: 'Tool-use advice reviewed relevance.', checks: [], sources: []}]}), 0, esc);
  assert.match(markup, /Evidence needs review/);
  assert.match(markup, /Review the evidence/);
  assert.match(markup, /Tool-use review/);
  assert.ok(markup.indexOf('Review the evidence') < markup.indexOf('Tool-use review'));
});

test('BEFORE YOU SEND (2026-10-09 review): a draft-check warning is a callout on its own waiting mail card, never a run-level hint', () => {
  const other = {...card, id: 'a3', seen: 1};
  const s = {id: 'r1', state: 'ready', actions: [card, other], draft_review: {status: 'checked', warnings: [
    {action_id: 'a1', kind: 'contradicts_request', text: 'The reply may contradict the student’s request.'}]}};
  const markup = suggestionsMarkup(item(s), 0, esc);
  assert.equal((markup.match(/Before you send/g) || []).length, 1);
  const first = markup.indexOf('data-action-id="a1"'), second = markup.indexOf('data-action-id="a3"');
  const callout = markup.indexOf('Before you send');
  assert.ok(first < callout && callout < second, 'the callout sits on the card it concerns');
  assert.doesNotMatch(markup, /<p class="field-hint" role="status">The reply may contradict/);
  // Once the card is edited it is a new card: the warning about the old text leaves with it.
  const edited = suggestionsMarkup(item({...s, actions: [{...card, id: 'a9', edited: true}, other]}), 0, esc);
  assert.doesNotMatch(edited, /Before you send/);
  // Unavailable and partial checks stay muted, run-level notes.
  const partial = suggestionsMarkup(item({...s, draft_review: {status: 'partial', warnings: []}}), 0, esc);
  assert.match(partial, /The reply check covered part of the available context/);
  assert.doesNotMatch(partial, /Before you send/);
});

test('EDIT DURING A RUN (2026-10-09 review): no Edit while the suggestions are still being prepared', () => {
  const running = suggestionsMarkup(item({id: 'r1', state: 'running', actions: [card]}), 0, esc);
  assert.doesNotMatch(running, /data-followup-edit="a1"/);
  assert.match(running, /Preparing suggestions…/);
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card]}), 0, esc), /data-followup-edit="a1"/);
});

test('LEARNED FROM AN EDIT: a follow-up says what was remembered from its corrections, with Undo and Change while it stands', () => {
  const kept = {case: 'c1', kind: 'edit', at: '2026-10-13T12:00:00+00:00', memory: {id: 'm1', content: 'Ask plainly for <documentation>;\n no rule number.', standing: true}};
  const line = learnedMarkup({id: 'r1', learned: [kept]}, esc);
  assert.match(line, /Remembered for later drafts/);
  assert.match(line, /From your edit: “Ask plainly for &lt;documentation&gt;; no rule number\.”/);
  assert.match(line, /data-followup-forget="m1">Undo<\/button> · <a class="text-button" href="#memory\?memory=m1">Change<\/a>/);
  assert.doesNotMatch(line, /data-undone|It replaces/);
  // Each kind of correction in its own words; what a newer rule replaced; one taken back says so and offers nothing.
  assert.match(learnedMarkup({learned: [{...kept, kind: 'decision'}]}, esc), /From your decision: “/);
  assert.match(learnedMarkup({learned: [{...kept, kind: 'request', replaced: 'Cite the rule.'}]}, esc), /From the change you asked for: “[^”]+” It replaces “Cite the rule\.”\./);
  const undone = learnedMarkup({learned: [{...kept, replaced: 'Cite the rule.', memory: {...kept.memory, standing: false}}]}, esc);
  assert.match(undone, /data-undone="true"/);
  assert.match(undone, /Taken back/);
  assert.doesNotMatch(undone, /data-followup-forget|It replaces/);
  assert.equal(learnedMarkup({id: 'r2'}, esc), '');
  assert.equal(learnedMarkup({id: 'r2', learned: [{case: 'c9'}]}, esc), '');
  // On the follow-up it comes after the steps, and is there once nothing waits too.
  const markup = suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card], learned: [kept]}), 0, esc);
  assert.ok(markup.indexOf('Remembered for later drafts') > markup.indexOf('data-action-id="a1"'), 'after the steps');
  assert.match(suggestionsMarkup(item({id: 'r1', state: 'settled', actions: [], learned: [kept]}), 0, esc), /Remembered for later drafts/);
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'ready', actions: [card], learning: [{kind: 'lesson', title: 'Old'}]}), 0, esc), /Shaped by/);
});

test('FYI (2026-10-09 review): FYI is a status filter of its own', () => {
  assert.equal(supportMatches({state: 'fyi', title: 'Thanks', student: {name: 'A'}}, 'fyi', ''), true);
  assert.equal(supportMatches({state: 'fyi', title: 'Thanks', student: {name: 'A'}}, 'active', ''), false);
});

test('REDO FROM CHAT: the chat steps still waiting for this conversation show with the follow-up, with Approve and Decline', () => {
  // 2026-10-09, Jared Zane's follow-up: the chat's redone reply waited only in the chat while the page said its proposals no longer applied.
  const tracking = {id: 'c2', seen: 9, endpoint: 'student_support.create', method: 'POST', path: '/api/student-support', pending: true, status: 'proposed',
    summary: 'Track the student follow-up', body: {kind: 'followup', state: 'waiting'}, page: '#follow-ups',
    effect: {title: 'Mark waiting on student', where: 'Follow-up', changes: [{label: 'Status', value: 'Waiting on student'}], button: 'Mark waiting'}};
  const html = suggestionsMarkup(item({id: 'r1', state: 'superseded', preparable: true, renew: false, actions: [meeting],
    chat: [{...card, id: 'c1'}, tracking]}), 0, esc, {here: '#follow-ups'});
  assert.match(html, /2 steps from your Assistant chat wait for you\./);
  assert.match(html, /data-assistant-approve="c1">Send/);
  assert.doesNotMatch(html, /data-followup-edit="c1"/);  // a chat's draft is edited in the chat
  assert.match(html, /data-assistant-approve="c2">Mark waiting/);
  assert.match(html, /data-assistant-decline="c2"/);
  assert.doesNotMatch(html, /support-step/);  // no step number: not one of the follow-up's own numbered steps
  assert.doesNotMatch(html, /<script>untrusted/);  // the draft's text is escaped as in Changes
  assert.doesNotMatch(suggestionsMarkup(item({id: 'r1', state: 'superseded', actions: [meeting]}), 0, esc), /from your Assistant chat/);
  // A follow-up with nothing prepared (idle) shows them too; a decided chat card is not shown.
  const idle = suggestionsMarkup(item({state: 'idle', chat: [{...card, id: 'c1'}]}), 3, esc);
  assert.match(idle, /One step from your Assistant chat waits for you\.[\s\S]*data-assistant-approve="c1"/);
  assert.match(idle, /data-followup-item="3"/);
  assert.equal(suggestionsMarkup(item({state: 'idle', chat: [{...card, id: 'c1', pending: false, status: 'declined'}]}), 3, esc), '');
});

test('REDO FROM CHAT: a chat step this browser is approving reads Approving…, not waiting', async () => {
  await whileApproving(['c1'], async () => {
    const [shown] = withApproving([item({id: 'r1', state: 'superseded', actions: [], chat: [{...card, id: 'c1'}]})]);
    assert.equal(shown.suggestions.chat[0].status, 'approving');
    assert.equal(shown.suggestions.chat[0].pending, false);
    assert.match(suggestionsMarkup(shown, 0, esc), /Approving…/);
  });
});

test('REGRADES BUBBLE SHEETS: a bubble-sheet card edits the letters filled, ticked as it proposes, and keep is Faculty reading', () => {
  const action = {id: 'a1', seen: 2, endpoint: 'frq_regrade_panel.decide', pending: true, body: {decision: 'change', answer: 'BD', reply: 'Hi Bob,'},
    preview: {data: {kind: 'mc', read: 'D', key: 'B', number: 3, from: '4', of: '4', picture: '/api/bubble-sheets/sheets/s1/parts/q3'}}};
  const form = decideEditMarkup(action, 1);
  assert.equal(form, sheetEditMarkup(action, 1));
  assert.match(form, /data-decide-read="D"/);
  assert.match(form, /value="B" checked/);
  assert.match(form, /value="D" checked/);
  assert.doesNotMatch(form, /value="A" checked/);
  assert.match(form, /Faculty read D; your key: B/);
  assert.match(form, /src="\/api\/bubble-sheets\/sheets\/s1\/parts\/q3"/);
  const keep = sheetEditMarkup({...action, body: {decision: 'keep', reply: 'Hi'}}, 1);
  assert.match(keep, /value="D" checked/);
  assert.doesNotMatch(keep, /value="B" checked/);
  assert.deepEqual(readingChoice('D', ['D']), {decision: 'keep'});
  assert.deepEqual(readingChoice('D', ['D', 'B']), {decision: 'change', answer: 'BD'});
  assert.deepEqual(readingChoice('D', []), {decision: 'change', answer: ''});
  assert.deepEqual(readingChoice('', []), {decision: 'keep'});
});

test('REGRADES "APPROVE ALL": Regrades is a tab of its own, out of Active, and Approve all takes its waiting decisions, keeps first', async () => {
  const decide = (id, decision, extra = {}) => ({id, seen: 4, endpoint: 'frq_regrade_panel.decide', pending: true, body: {decision, reply: 'Hi'}, ...extra});
  const regrade = (key, state, actions, extra = {}) => ({kind: 'followup', enrollment_id: `e-${key}`, source_key: `gradescope_regrade:${key}`, state: 'needs_reply',
    title: 'Regrade request', student: {name: 'Student'}, suggestions: {id: `run-${key}`, state, actions}, ...extra});
  const change = regrade('a', 'ready', [decide('c1', 'change')]);
  const keep = regrade('b', 'ready', [decide('k1', 'keep')]);
  const preparing = regrade('c', 'running', [decide('k2', 'keep')]);
  const answered = regrade('d', 'settled', [], {state: 'resolved'});
  const mail = {kind: 'followup', enrollment_id: 'e-m', source_key: 'gmail:m', state: 'needs_reply', title: 'Question', student: {name: 'Other'},
    suggestions: {id: 'run-m', state: 'ready', actions: [decide('m1', 'keep', {endpoint: 'gmail_mail.send'})]}};
  assert.equal(isRegrade(change), true);
  assert.equal(isRegrade(mail), false);
  assert.equal(supportMatches(change, 'regrades', ''), true);
  assert.equal(supportMatches(change, 'active', ''), false);
  assert.equal(supportMatches(change, 'all', ''), true);
  assert.equal(supportMatches({...change, attention: true}, 'attention', ''), true);
  assert.equal(supportMatches(mail, 'regrades', ''), false);
  assert.equal(supportMatches(mail, 'active', ''), true);
  assert.equal(supportMatches(answered, 'regrades', ''), false);
  assert.equal(supportMatches(answered, 'all', ''), true);
  // Keeps first (a keep never outdates another card of the same exam), then the list's order; a preparation still running, a
  // follow-up that is not a regrade request and a card being approved are left out.
  assert.deepEqual(regradeApprovals([change, mail, keep, preparing, answered]).map(c => c.action.id), ['k1', 'c1']);
  assert.deepEqual(regradeApprovals([change, regrade('e', 'needs_input', [decide('k3', 'keep', {pending: false, status: 'approving'})])]).map(c => c.action.id), ['c1']);
  let inside = [];
  await whileApproving(['k1'], async () => { inside = regradeApprovals(withApproving([change, keep])).map(c => c.action.id); });
  assert.deepEqual(inside, ['c1']);
});
