import assert from 'node:assert/strict';
import test from 'node:test';
import { routeContext } from '../static/assistant-context.js';
import { activeRuns, automationMarkup, doneMarkup, lastResult, nextCheck, PREPARING } from '../static/assistant-automations.js';
import { cardMarkup } from '../static/assistant.js';

test('ASSISTANT: route context carries exact record IDs without page content', () => {
  assert.deepEqual(routeContext('#assignments/a/t'), {assignment_id: 'a', term_id: 't'});
  assert.deepEqual(routeContext('#grading/p?question=q&response=r'), {plan_id: 'p', question_id: 'q', response_id: 'r'});
  assert.deepEqual(routeContext('#inbox/job/j'), {job_id: 'j'});
  assert.deepEqual(routeContext('#overview?password=secret'), {});
});

test('ASSISTANT: automation inventory shows scope, timing, result, blockers and owner controls safely', () => {
  const output = automationMarkup({automations: [{id: 'r', kind: 'workflow', title: '<unsafe>', scope: 'Fall term',
    enabled: true, schedule: 'Daily at 5:00 AM Central', next_check: 0, last_result: 'ready', blocker: '<check>', revision: 2,
    pending: 1, href: '#overview'}], metrics: {waiting_over_day: 3}});
  assert.ok(output.includes('&lt;unsafe&gt;'));
  assert.ok(output.includes('&lt;check&gt;'));
  assert.ok(output.includes('Ready to review'));
  assert.ok(output.includes('Daily at 5:00 AM Central'));
  assert.ok(output.includes('data-automation-toggle="r"'));
  assert.ok(output.includes('3 changes waiting over a day'));
});

test('ASSISTANT: material approval shows the entire proposed text and its named destination', () => {
  const content = 'A'.repeat(5000) + '<last line>';
  const output = cardMarkup({id: 'a', seen: 1, status: 'proposed', pending: true, endpoint: 'assistant.material_create',
    body: {title: 'Notes', content}, preview: {data: {kind: 'course_notes', destination: 'MATH 140', note: 'No publication.'}}});
  assert.ok(output.includes(content.replace('<last line>', '&lt;last line&gt;')));
  assert.ok(output.includes('MATH 140'));
  assert.ok(output.includes('Approve import'));
});

// AUTOMATION VISIBILITY (2026-10-09 review): rows from the rule package carry `waiting`, `held`, `last_checked_at` and
// `last_outcome`; older rows lack them, so each is rendered only when present.
const rule = (extra) => ({id: 'w', kind: 'workflow', title: 'Grade reconciliation · MATH 140', scope: 'MATH 140 · Fall 2026', enabled: true,
  schedule: 'Daily at 5:00 AM Central', next_check: 1791000000, last_result: 'ready', blocker: '', revision: 3, pending: 0,
  href: '#gradebook/t', prepare: {kind: 'reconciliation', target: 't'}, ...extra});

test('ASSISTANT: a normal wait is a neutral note, never a problem to diagnose', () => {
  const output = automationMarkup({automations: [rule({waiting: 'Waiting for the morning Canvas sync; checks after it finishes'})]});
  assert.ok(output.includes('data-automation-waiting'));
  assert.ok(output.includes('Waiting for the morning Canvas sync'));
  assert.ok(!output.includes('data-automation-repair'));
  assert.ok(output.includes('Next: '));
});

test('ASSISTANT: a held rule offers Prepare again for its own target and hides Next', () => {
  const output = automationMarkup({automations: [rule({held: 'Held: a step of the last review failed. Review it, then prepare again.'})]});
  assert.ok(output.includes('data-automation-held'));
  assert.ok(output.includes('data-automation-prepare-again data-kind="reconciliation" data-target="t"'));
  assert.ok(output.includes('>Prepare again<'));
  assert.ok(!output.includes('Next: '));
  assert.ok(!output.includes('data-automation-repair'), 'held is not a blocker by itself');
  assert.equal(nextCheck(rule({held: 'Held'})), '');
});

test('ASSISTANT: the last result is the real outcome with its time; a ready run with nothing pending was reviewed', () => {
  const checked = lastResult(rule({last_outcome: 'No discrepancies', last_checked_at: '2026-10-09T10:02:00+00:00'}));
  assert.equal(checked.label, 'Last result');
  assert.ok(checked.text.startsWith('No discrepancies · Oct 9, 5:02'), checked.text);
  assert.ok(checked.text.endsWith('Central'));
  assert.equal(lastResult(rule({})).text, 'Reviewed');
  assert.equal(lastResult(rule({pending: 2})).text, 'Ready to review');
  assert.deepEqual(lastResult({kind: 'built-in', about: true, last_result: 'Requires approved rubrics'}), {label: 'How it works', text: 'Requires approved rubrics'});
  assert.equal(lastResult({kind: 'built-in', last_outcome: '1 shown as FYI', outcome_label: 'Last 30 days'}).label, 'Last 30 days');
  // A built-in row without a next check states its schedule once.
  const builtIn = automationMarkup({automations: [{id: 'b', kind: 'built-in', title: 'Scan processing', scope: 'Current', enabled: true,
    schedule: 'As eligible papers arrive', about: true, last_result: 'Inspect the Inbox', href: '#inbox', blocker: ''}]});
  assert.equal(builtIn.split('As eligible papers arrive').length, 2);
  assert.ok(!builtIn.includes('Next:'));
});

test('ASSISTANT: Diagnose names the row, not the route, and blockers alone offer it', () => {
  const output = automationMarkup({automations: [rule({blocker: 'Source unavailable'})]});
  assert.ok(output.includes('data-automation-repair="#gradebook/t" data-title="Grade reconciliation · MATH 140"'));
});

test('ASSISTANT: Done by Faculty folds one line per thing, each to its owning page', () => {
  const output = doneMarkup({days: 7, more: 2, lines: [
    {at: '2026-10-09T10:00:00+00:00', text: 'Certified 2 late papers · <Quiz>', href: '#grading/p?tab=export'},
    {at: '2026-10-09T09:00:00+00:00', text: 'The Assistant made 3 changes by itself', tab: 'changes'}]});
  assert.ok(output.startsWith('<details class="disclosure" data-patch-key="automation-done">'));
  assert.ok(!output.includes('<details class="disclosure" data-patch-key="automation-done" open'), 'folded');
  assert.ok(output.includes('Done by Faculty · last 7 days'));
  assert.ok(output.includes('<span class="disclosure-hint">4</span>'));
  assert.ok(output.includes('href="#grading/p?tab=export">Certified 2 late papers · &lt;Quiz&gt;</a>'));
  assert.ok(output.includes('data-assistant-tab="changes">The Assistant made 3 changes by itself</button>'));
  assert.ok(output.includes('2 earlier not shown.'));
  assert.ok(doneMarkup(undefined).includes('Nothing Faculty did by itself in the last 7 days.'));
  assert.ok(automationMarkup({automations: [], done: {lines: []}}).includes('automation-done'));
});

test('ASSISTANT: Automation lists active runs only: queued, running, waiting for guidance, or ready with changes to approve', () => {
  const runs = [{id: 'q', state: 'queued'}, {id: 'r', state: 'running'}, {id: 'n', state: 'needs_input'},
    {id: 'p', state: 'ready', actions: [{pending: true}]}, {id: 'd', state: 'ready', actions: [{pending: false}]},
    {id: 'f', state: 'failed'}, {id: 's', state: 'stale'}];
  assert.deepEqual(activeRuns(runs).map(run => run.id), ['q', 'r', 'n', 'p']);
  assert.ok(PREPARING.startsWith('Preparing in the background'));
});
