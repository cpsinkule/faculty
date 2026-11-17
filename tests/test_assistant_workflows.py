"""ASSISTANT: explicit opt-in, fair dispatch, stale-source refusal and no repeated writes."""
import copy
import json
import time
import unittest
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import patch

from rosters import connect
from scheduling.clock import CAMPUS_ZONE
from test_canvas_nightly import MORNING, MorningBase
from test_scheduling_service import ScheduleBase
from test_storage import HEADERS
import test_exam_workflow as exam_fixture


class WorkflowsTests(ScheduleBase):
    def setUp(self):
        ScheduleBase.setUp(self)
        self.service = self.app.extensions['assistant']
        self.workflows = self.service.workflows
        self.observation = {'term_id': self.term, 'title': 'Quiz 1', 'page': '#exams/one',
                            'needs_review': True, 'source': '/api/exams/one/overview', 'version': 1}
        self.mock_observe = patch.object(self.workflows, 'observe', side_effect=lambda *a, **k: copy.deepcopy(self.observation))
        self.observe_mock = self.mock_observe.start()
        self.addCleanup(self.mock_observe.stop)
        self.endpoint = patch.object(self.service.followups, 'endpoint', return_value={'name': 'Synthetic', 'kind': 'hosted', 'model': 'fake'})
        self.endpoint.start()
        self.addCleanup(self.endpoint.stop)

    def create_rule(self, kind='readiness'):
        data = {'kind': kind, 'target': 'one', 'lead_days': 14, 'request_id': str(uuid.uuid4())}
        preview = self.client.post('/api/assistant/workflows/preview', json=data, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        result = self.client.post('/api/assistant/workflows', json={**data, 'fingerprint': preview.json['fingerprint']}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def due(self):
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.execute('UPDATE assistant_workflows SET next_check=0')

    def run_with(self, callback):
        class Engine:
            name = 'test'
            def turn(self, c, text):
                callback(c, text)
            def stop(self):
                pass
            def close(self):
                pass
        with patch('assistant.conversations.engine_for', return_value=Engine()):
            self.assertTrue(self.service.followups.dispatch_one())
            for _ in range(500):
                if not self.workflows.busy():
                    return
                time.sleep(.01)
        self.fail('Preparation did not finish')

    def test_rules_are_off_until_explicitly_enabled_and_changes_prepare_once(self):
        self.assertFalse(self.workflows.process_one())
        self.assertEqual(self.workflows.listing()['workflows'], [])
        rule = self.create_rule()
        self.assertTrue(self.workflows.process_one())
        self.assertEqual(len(self.workflows.listing()['preparations']), 1)
        self.due()
        self.workflows.process_one()
        self.assertEqual(len(self.workflows.listing()['preparations']), 1)
        self.run_with(lambda c, t: c.emit('assistant', text='Review the missing prerequisites.'))
        self.observation['version'] = 2
        self.due()
        self.workflows.process_one()
        runs = self.workflows.listing()['preparations']
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1]['state'], 'stale')
        self.assertTrue(rule['enabled'])

    def test_auto_category_is_still_an_approval_and_pause_revokes_pending_cards(self):
        rule = self.create_rule()
        self.workflows.process_one()
        def propose(c, text):
            result, event = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
                'body': {'title': 'Synthetic review', 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'}, 'summary': 'Prepare a calendar reminder'})
            self.assertEqual(json.loads(result)['outcome'], 'waiting_for_approval')
            c.tool_event(event)
            c.emit('assistant', text='The reminder waits for review.')
        self.run_with(propose)
        run = self.workflows.listing()['preparations'][0]
        self.assertEqual(run['state'], 'ready')
        self.assertEqual(run['actions'][0]['tier'], 'approve')
        paused = self.client.post('/api/assistant/workflows/' + rule['id'], json={'enabled': False, 'revision': 1}, headers=HEADERS)
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(self.workflows.listing()['preparations'][0]['state'], 'stale')
        self.assertFalse(self.workflows.listing()['preparations'][0]['actions'][0]['pending'])

    def test_workflow_drafts_keep_memory_references_without_saving_a_chat(self):
        from test_assistant_memory import answering
        with answering():
            kept = self.service.memory.write({'content': 'Use concise explanations.'})
        response = self.client.post('/api/assistant/preparations', json={
            'kind': 'readiness', 'target': 'one', 'request_id': 'memory-draft'}, headers=HEADERS)
        self.assertEqual(response.status_code, 202, response.json)
        self.run_with(lambda c, text: c.emit('assistant', text='Ready for review.'))
        run = self.workflows.listing()['preparations'][0]
        self.service.memory.record_given([{'id': kept['id'], 'revision': 1}], preparation=run['id'])
        with connect(self.service.database) as db:
            rows = db.execute('SELECT * FROM assistant_draft_memories').fetchall()
        self.assertEqual(len(rows), 1)
        # The run itself was told it: unjudged (no Jev here), so `nearest`; the repeat above counts once and changes nothing.
        self.assertEqual((rows[0]['preparation_id'], rows[0]['followup_run_id'], rows[0]['selection']), (run['id'], None, 'nearest'))
        self.assertEqual(self.service.memory.listing()['memories'][0]['given'], {'drafts': 1, 'again': 0})

    def test_source_changes_after_preparation_refuse_approval(self):
        self.client.post('/api/assistant/preparations', json={'kind': 'readiness', 'target': 'one', 'request_id': 'manual'}, headers=HEADERS)
        def propose(c, text):
            result, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
                'body': {'title': 'Review', 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'}, 'summary': 'Reminder'})
            self.assertEqual(json.loads(result)['outcome'], 'waiting_for_approval')
        self.run_with(propose)
        card = self.workflows.listing()['preparations'][0]['actions'][0]
        self.observation['version'] += 1
        answer = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(answer.status_code, 409)
        self.assertIn('source changed', answer.json['error'])

    def test_pause_during_observation_does_not_queue_work(self):
        rule = self.create_rule()
        def observe(*args):
            self.client.post('/api/assistant/workflows/' + rule['id'], json={'enabled': False, 'revision': 1}, headers=HEADERS)
            return self.observation
        with patch.object(self.workflows, 'observe', side_effect=observe):
            self.workflows.process_one()
        self.assertEqual(self.workflows.listing()['preparations'], [])

    def test_failed_preparation_never_repeats_automatically_and_busy_chat_has_priority(self):
        self.create_rule()
        self.workflows.process_one()
        with patch.object(self.service.conversations, 'interactive_busy', return_value=1):
            self.assertFalse(self.service.followups.dispatch_one())
        def fail(c, text):
            raise RuntimeError('Synthetic preparation failure')
        self.run_with(fail)
        self.assertEqual(self.workflows.listing()['preparations'][0]['state'], 'failed')
        self.observation['version'] += 1
        self.due()
        self.workflows.process_one()
        self.assertEqual(len(self.workflows.listing()['preparations']), 1)

    def test_recovery_and_control_routes_are_not_agent_permissions(self):
        self.client.post('/api/assistant/preparations', json={'kind': 'readiness', 'target': 'one', 'request_id': 'manual'}, headers=HEADERS)
        run = self.workflows.listing()['preparations'][0]
        with self.service.rosters.lock(), connect(self.service.database) as db:
            self.workflows.event(db, run['id'], 'running')
        self.workflows.recover()
        self.assertEqual(self.workflows.listing()['preparations'][0]['state'], 'interrupted')
        result, _ = self.service.run_tool(self.service.open_session('test'), 'faculty_write', {
            'method': 'POST', 'path': '/api/assistant/workflows/x', 'body': {'enabled': True}, 'summary': 'Self-enable'})
        self.assertEqual(json.loads(result)['outcome'], 'refused')

    def test_real_reconciliation_read_uses_owning_routes(self):
        self.mock_observe.stop()
        result = self.client.get('/api/assistant/reconciliation/' + self.term)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['term_id'], self.term)
        self.assertIn('last recorded', result.json['note'])

    def test_watch_prepares_only_on_matching_transition_and_pause_revokes_dispatch(self):
        state = {'state': 'running', 'finished': False, 'needs_attention': False, 'ready': False, 'page': '#inbox/job/scan', 'message': 'Working'}
        with patch.object(self.service.watches, 'observe', side_effect=lambda *a: dict(state)):
            data = {'title': 'Scan', 'kind': 'scan', 'target': 'scan', 'condition': 'finished', 'prepare_review': True, 'request_id': 'watch'}
            preview = self.service.watches.preview(data)
            watch = self.service.watches.create({**data, 'fingerprint': preview['fingerprint']})
            self.mock_observe.stop()
            state.update(state='done', finished=True, ready=True)
            with self.service.rosters.lock(), connect(self.service.database) as db:
                db.execute('UPDATE assistant_watches SET next_check=0')
            self.service.watches.process_one()
            self.assertEqual(len(self.workflows.listing()['preparations']), 1)
            self.service.watches.update(watch['id'], {'enabled': False, 'revision': 1})
            self.assertFalse(self.workflows.dispatch())
            self.assertEqual(self.workflows.listing()['preparations'][0]['state'], 'stale')

    def test_preparation_request_is_idempotent_and_a_different_instruction_conflicts(self):
        body = {'kind': 'readiness', 'target': 'one', 'request_id': 'exact-request'}
        first = self.client.post('/api/assistant/preparations', json=body, headers=HEADERS)
        same = self.client.post('/api/assistant/preparations', json=body, headers=HEADERS)
        self.assertEqual(first.json['id'], same.json['id'])
        self.assertEqual(len(self.workflows.listing()['preparations']), 1)
        changed = self.client.post('/api/assistant/preparations', json={**body, 'instruction': 'Different work'}, headers=HEADERS)
        self.assertEqual(changed.status_code, 409)

    def test_stopping_a_queued_preparation_requires_an_explicit_restart(self):
        self.create_rule()
        self.workflows.process_one()
        run = self.workflows.listing()['preparations'][0]
        self.workflows.stop(run['id'])
        self.observation['version'] += 1
        self.due()
        self.workflows.process_one()
        runs = self.workflows.listing()['preparations']
        self.assertEqual((len(runs), runs[0]['state']), (1, 'interrupted'))

    def test_metrics_are_read_only_and_do_not_call_a_model(self):
        with patch('assistant.conversations.engine_for', side_effect=AssertionError('No model needed')):
            result = self.client.get('/api/assistant/metrics')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['sample_actions'], 0)
        self.assertIn('not model accuracy', result.json['note'])


class DailyReconciliationTests(ScheduleBase):
    setUp = WorkflowsTests.setUp
    create_rule = WorkflowsTests.create_rule

    @staticmethod
    def at(value):
        return datetime.fromisoformat(value).timestamp()

    def test_daily_check_waits_until_five_and_persists_the_next_day_before_reading(self):
        before = self.at('2026-10-09T04:59:00-05:00')
        morning = self.at('2026-10-09T05:00:00-05:00')
        with patch('assistant.workflows.time.time', return_value=before):
            rule = self.create_rule('reconciliation')
            self.observe_mock.reset_mock()
            self.assertFalse(self.workflows.process_one())
            self.observe_mock.assert_not_called()
        with patch('assistant.workflows.time.time', return_value=morning):
            self.assertTrue(self.workflows.process_one())
            self.assertEqual(self.observe_mock.call_count, 1)
            self.assertEqual(len(self.workflows.listing()['preparations']), 1)
            self.observation['version'] += 1
            self.assertFalse(self.workflows.process_one())
        with connect(self.service.database) as db:
            next_check = db.execute('SELECT next_check FROM assistant_workflows WHERE id=?', (rule['id'],)).fetchone()[0]
        self.assertEqual(next_check, self.at('2026-10-10T05:00:00-05:00'))
        # A fresh worker sees the durable schedule and does not repeat a same-day source change.
        from assistant.workflows import Workflows
        with patch('assistant.workflows.time.time', return_value=morning + 3600):
            self.assertFalse(Workflows(self.service).process_one())
        with patch('assistant.workflows.time.time', return_value=next_check):
            self.assertTrue(self.workflows.process_one())
            self.assertEqual(len(self.workflows.listing()['preparations']), 2)

    def test_central_schedule_follows_daylight_saving_in_both_directions(self):
        for start, following in (
            ('2026-10-31T05:00:00-05:00', '2026-11-01T05:00:00-06:00'),
            ('2027-03-13T05:00:00-06:00', '2027-03-14T05:00:00-05:00'),
        ):
            self.assertEqual(self.workflows.next_morning(self.at(start)), self.at(following))

    def test_enable_resume_and_migrated_rules_wait_for_the_next_morning(self):
        evening = self.at('2026-10-09T19:00:00-05:00')
        morning = self.at('2026-10-10T05:00:00-05:00')
        with patch('assistant.workflows.time.time', return_value=evening):
            rule = self.create_rule('reconciliation')
            self.workflows.update(rule['id'], {'enabled': False, 'revision': 1})
            self.workflows.update(rule['id'], {'enabled': True, 'revision': 2})
            self.assertFalse(self.workflows.process_one())
            with self.service.rosters.lock(), connect(self.service.database) as db:
                # Migration 006's zero schedules an existing rule; it never enables a paused one.
                db.execute("UPDATE assistant_workflows SET next_check=0 WHERE id=?", (rule['id'],))
            self.observe_mock.reset_mock()
            self.assertTrue(self.workflows.process_one())
            self.observe_mock.assert_not_called()
            with connect(self.service.database) as db:
                self.assertEqual(db.execute('SELECT next_check FROM assistant_workflows WHERE id=?', (rule['id'],)).fetchone()[0], morning)

    def test_failed_read_is_not_retried_automatically_that_day(self):
        with patch('assistant.workflows.time.time', return_value=self.at('2026-10-09T04:59:00-05:00')):
            self.create_rule('reconciliation')
        self.observe_mock.side_effect = ValueError('Source unavailable')
        with patch('assistant.workflows.time.time', return_value=self.at('2026-10-09T05:00:00-05:00')):
            self.assertTrue(self.workflows.process_one())
        with patch('assistant.workflows.time.time', return_value=self.at('2026-10-09T06:00:00-05:00')):
            self.assertFalse(self.workflows.process_one())
        self.assertIn('Source unavailable', self.workflows.listing()['workflows'][0]['last_error'])

    def test_recorded_canvas_discrepancies_and_failed_checks_need_review(self):
        self.mock_observe.stop()
        for canvas, needed in (({}, False), ({'last_sync': {'held': 2}}, True),
                               ({'nightly': {'held': 1}}, True), ({'nightly': {'unsure': 1}}, True),
                               ({'nightly': {'status': 'signed_out'}}, True), ({'linked_sections': 1}, True)):
            view = {'canvas': canvas, 'term': {}, 'setup': {}}
            with patch.object(self.workflows, 'read', side_effect=[view, {'grading': {'columns': []}, 'differences': [], 'unknown': []}]):
                observation = self.workflows.observe('reconciliation', self.term)
            self.assertEqual(observation['needs_review'], needed)
            self.assertIn('last recorded', observation['note'])


class ReadinessEligibilityTests(unittest.TestCase):
    """ASSISTANT / EXAMS: real owner projections decide whether preparation still applies."""
    setUp = exam_fixture.ExamWorkflowTests.setUp
    import_students = exam_fixture.ExamWorkflowTests.import_students
    batch = exam_fixture.ExamWorkflowTests.batch
    arrival = exam_fixture.ExamWorkflowTests.arrival

    def options(self):
        response = self.client.get('/api/assistant/workflows/options')
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def set_date(self, day):
        body = {'version': self.event['version'], 'kind': 'main', 'label': 'Main', 'date': day, 'time': '09:00'}
        sitting = next((s['id'] for s in self.event['sittings'] if s['kind'] == 'main'), None)
        if sitting:
            body['version'] = next(s['version'] for s in self.event['sittings'] if s['id'] == sitting)
        self.event = self.exams.save_sitting(self.event['id'], body, sitting)

    def test_options_include_planned_and_upcoming_but_not_past_assessments(self):
        with patch('exam_workflow.overview.campus_today', return_value='2026-10-09'):
            initial = self.options()
            self.assertEqual([e['id'] for e in initial['exams']], [self.event['id']])
            self.set_date('2026-10-10')
            self.assertEqual(self.options(), initial)
            self.set_date('2026-10-08')
            self.assertEqual(self.options(), {'terms': initial['terms'], 'exams': []})
            self.set_date('2026-10-11')
            self.assertEqual(self.options(), initial)

    def test_past_readiness_is_readable_but_new_preparations_are_refused(self):
        self.set_date('2026-10-08')
        with patch('exam_workflow.overview.campus_today', return_value='2026-10-09'):
            observation = self.client.get('/api/assistant/readiness/' + self.event['id'])
            self.assertEqual(observation.status_code, 200, observation.json)
            self.assertEqual(observation.json['overview']['phase'], 'past')
            self.assertIsNotNone(observation.json['overview']['next'])
            self.assertFalse(observation.json['needs_review'])
            for path in ('/api/assistant/workflows/preview', '/api/assistant/workflows', '/api/assistant/preparations'):
                result = self.client.post(path, json={'kind': 'readiness', 'target': self.event['id'],
                                                     'request_id': path}, headers=HEADERS)
                self.assertEqual(result.status_code, 400, result.json)
                self.assertIn('already been given', result.json['error'])
        self.assertEqual(self.app.extensions['assistant'].workflows.listing(), {'workflows': [], 'preparations': []})

    def test_an_early_paper_keeps_the_upcoming_main_sitting_available(self):
        batch = self.batch()
        self.arrival(batch['copies'][0], [1, 2])
        self.set_date('2026-10-10')
        with patch('exam_workflow.overview.campus_today', return_value='2026-10-09'):
            self.assertEqual([e['id'] for e in self.options()['exams']], [self.event['id']])
            # Once the main sitting is reached, the existing paper establishes that it was given.
            self.set_date('2026-10-09')
            self.assertEqual(self.options()['exams'], [])

    def test_an_enabled_rule_withdraws_queued_readiness_once_the_exam_is_past(self):
        service = self.app.extensions['assistant']
        data = {'kind': 'readiness', 'target': self.event['id'], 'request_id': 'rule'}
        self.set_date('2026-10-10')
        with patch('exam_workflow.overview.campus_today', return_value='2026-10-09'):
            preview = self.client.post('/api/assistant/workflows/preview', json=data, headers=HEADERS)
            self.assertEqual(preview.status_code, 200, preview.json)
            rule = self.client.post('/api/assistant/workflows', json={**data, 'fingerprint': preview.json['fingerprint']}, headers=HEADERS)
            self.assertEqual(rule.status_code, 201, rule.json)
            self.assertTrue(service.workflows.process_one())
            self.assertEqual(service.workflows.listing()['preparations'][0]['state'], 'queued')
            self.set_date('2026-10-08')
            with service.rosters.lock(), connect(service.database) as db:
                db.execute('UPDATE assistant_workflows SET next_check=0')
            self.assertTrue(service.workflows.process_one())
            runs = service.workflows.listing()['preparations']
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]['state'], 'stale')


class ReviewFixTests(ScheduleBase):
    """2026-10-09 review of preparations: withdrawal by state, staged plans, an authoritative Stop, metrics, rule status, the
    escalation of a replacing request and bounded source reads."""
    create_rule = WorkflowsTests.create_rule
    due = WorkflowsTests.due
    run_with = WorkflowsTests.run_with

    def setUp(self):
        WorkflowsTests.setUp(self)
        self.outcomes, self.prompts = [], []

    def propose(self, title='Synthetic review', question=False):
        def callback(c, text):
            self.prompts.append(text)
            result, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
                'body': {'title': title, 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'}, 'summary': 'Prepare a reminder'})
            self.outcomes.append(json.loads(result))
            if question:
                c.service.run_tool(c.session, 'ask_question', {'questions': [{'title': 'Which room?', 'options': ['BLOC 306', 'BLOC 309']}]})
            c.emit('assistant', text='Prepared.')
        return callback

    def prepare(self, request_id, **extra):
        answer = self.client.post('/api/assistant/preparations', json={'kind': 'readiness', 'target': 'one', 'request_id': request_id, **extra},
                                  headers=HEADERS)
        self.assertEqual(answer.status_code, 202, answer.json)
        return answer.json

    def runs(self):
        return self.workflows.listing()['preparations']

    def rule(self):
        return self.workflows.listing()['workflows'][0]

    def test_unanswered_cards_are_withdrawn_and_the_question_reaches_the_next_review(self):
        self.create_rule()
        self.workflows.process_one()
        self.run_with(self.propose(question=True))
        self.assertEqual(self.runs()[0]['state'], 'needs_input')
        self.assertTrue(self.runs()[0]['actions'][0]['pending'])
        self.observation['version'] += 1
        self.due()
        self.workflows.process_one()
        runs = self.runs()
        self.assertEqual([r['state'] for r in runs], ['queued', 'stale'])
        self.assertFalse(runs[1]['actions'][0]['pending'])
        self.run_with(self.propose())
        self.assertEqual(self.outcomes[-1]['outcome'], 'waiting_for_approval', self.outcomes[-1])  # not "not repeated"
        self.assertIn('Earlier question and guidance: Question: Which room? Options: BLOC 306; BLOC 309', self.prompts[-1])

    def test_a_failed_review_is_held_keeps_its_state_and_releases_its_cards_when_prepared_again(self):
        self.create_rule()
        self.workflows.process_one()
        def fail(c, text):
            self.propose()(c, text)
            raise RuntimeError('Synthetic failure after a card')
        self.run_with(fail)
        self.assertEqual((self.runs()[0]['state'], self.runs()[0]['actions'][0]['pending']), ('failed', True))
        self.assertIn('Held: the last review failed', self.rule()['held'])
        self.observation['version'] += 1
        self.due()
        self.workflows.process_one()
        self.assertEqual((len(self.runs()), self.rule()['last_outcome']), (1, 'Held'))  # never repeated automatically
        self.prepare('again', instruction='Use BLOC 306.')
        runs = self.runs()
        self.assertEqual([r['state'] for r in runs], ['queued', 'failed'])
        self.assertFalse(runs[1]['actions'][0]['pending'])
        self.assertEqual(self.rule()['held'], '')
        self.run_with(self.propose())
        self.assertEqual(self.outcomes[-1]['outcome'], 'waiting_for_approval', self.outcomes[-1])

    def test_held_says_whether_a_restart_or_the_instructor_stopped_the_last_review(self):
        self.create_rule()
        self.workflows.process_one()
        with self.service.rosters.lock(), connect(self.service.database) as db:
            self.workflows.event(db, self.runs()[0]['id'], 'running')
        self.workflows.recover()
        self.assertIn('interrupted by a restart', self.rule()['held'])
        self.prepare('again')
        self.assertEqual(self.workflows.stop(self.runs()[0]['id']), {'stopped': True})
        self.assertIn('stopped by you', self.rule()['held'])

    def test_a_staged_next_stage_survives_its_rules_check(self):
        self.create_rule()
        self.workflows.process_one()
        def propose(c, text):
            answer, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
                'body': {'title': 'First stage', 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'}, 'summary': 'First stage'})
            action = json.loads(answer)['action_id']
            c.service.run_tool(c.session, 'continue_preparation', {'instruction': 'Use the created event ID.', 'after_action_ids': [action]})
            c.emit('assistant', text='The next stage waits for this approval.')
        self.run_with(propose)
        card = next(a for a in self.runs()[0]['actions'] if a['endpoint'] == 'workspace_calendar.create')
        result = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.observation['version'] += 1  # the approved change's follow-on effects in its owner
        self.due()
        self.workflows.process_one()
        self.assertEqual(self.rule()['last_outcome'], 'A staged review is under way')
        self.assertTrue(self.workflows.advance_one())
        self.assertEqual([(r['state'], r['stage']) for r in self.runs()], [('queued', 2), ('ready', 1)])
        self.observation['version'] += 1
        self.due()
        self.workflows.process_one()
        self.assertEqual([(r['state'], r['stage']) for r in self.runs()], [('queued', 2), ('ready', 1)])

    def test_a_step_still_running_holds_the_next_stage_and_never_stops_it(self):
        self.create_rule()
        self.workflows.process_one()
        def propose(c, text):
            answer, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
                'body': {'title': 'First stage', 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'}, 'summary': 'First stage'})
            c.service.run_tool(c.session, 'continue_preparation', {'instruction': 'Use the created event ID.',
                                                                  'after_action_ids': [json.loads(answer)['action_id']]})
            c.emit('assistant', text='The next stage waits for this approval.')
        self.run_with(propose)
        card = next(a for a in self.runs()[0]['actions'] if a['endpoint'] == 'workspace_calendar.create')
        self.assertEqual(self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS).status_code, 200)
        def waiting():
            with connect(self.service.database) as db:
                return [r[0] for r in db.execute('SELECT state FROM assistant_plan_continuations')]
        teaching = self.workflows.teaching
        # As a Canvas push or a regrade's Apply reads until its own worker has finished (assistant/outcomes.py `started`).
        with patch.object(self.workflows, 'teaching', lambda db, ident: [{**a, 'outcome': 'started'} for a in teaching(db, ident)]):
            self.assertFalse(self.workflows.advance_one())
            self.assertEqual(waiting(), ['waiting'])  # held, not stopped: a stopped stage never starts again
            self.assertEqual(self.rule()['held'], '')
        with patch.object(self.workflows, 'teaching', lambda db, ident: [{**a, 'outcome': 'partial'} for a in teaching(db, ident)]):
            self.assertIn('failed or is uncertain', self.rule()['held'])
        self.assertTrue(self.workflows.advance_one())
        self.assertEqual([(r['state'], r['stage']) for r in self.runs()], [('queued', 2), ('ready', 1)])

    def test_stop_is_recorded_first_and_refuses_what_arrives_after_it(self):
        self.prepare('stop-me')
        def stop_then_propose(c, text):
            with self.workflows._lock:  # Stop lands before the engine can be told (the turn not yet marked active)
                active, self.workflows._active = self.workflows._active, None
            self.assertEqual(self.workflows.stop(c.run['id']), {'stopped': True})
            with self.workflows._lock:
                self.workflows._active = active
            with connect(self.service.database) as db:
                self.assertRaises(Exception, self.workflows.recording, db, c.run['id'])
            self.propose()(c, text)
        self.run_with(stop_then_propose)
        run = self.runs()[0]
        self.assertEqual((run['state'], run['actions']), ('interrupted', []))  # finish never wrote over the Stop
        self.assertNotEqual(self.outcomes[-1].get('outcome'), 'waiting_for_approval', self.outcomes[-1])

    def test_unfinished_preparations_count_only_work_that_waits(self):
        self.prepare('quiet')
        self.run_with(lambda c, t: c.emit('assistant', text='Nothing to change.'))
        self.assertEqual(self.client.get('/api/assistant/metrics').json['unfinished_preparations'], 0)
        self.prepare('card')
        self.run_with(self.propose())
        self.assertEqual(self.client.get('/api/assistant/metrics').json['unfinished_preparations'], 1)

    def test_rule_status_projects_outcomes_and_a_finished_term(self):
        rule = self.create_rule()
        self.workflows.process_one()
        self.assertEqual(self.rule()['last_outcome'], 'Changes found; preparing a review')
        self.assertTrue(self.rule()['last_checked_at'])
        self.run_with(lambda c, t: c.emit('assistant', text='Nothing to change.'))
        self.assertEqual(self.rule()['last_outcome'], 'Reviewed: nothing to change')
        row = next(r for r in self.client.get('/api/assistant/automations').json['automations'] if r['id'] == rule['id'])
        self.assertEqual({k: row[k] for k in ('waiting', 'held', 'last_outcome', 'blocker')},
                         {'waiting': '', 'held': '', 'last_outcome': 'Reviewed: nothing to change', 'blocker': ''})
        self.assertTrue(row['last_checked_at'])
        self.observe_mock.side_effect = ValueError('Synthetic unreadable source')
        self.due()
        self.workflows.process_one()
        self.assertEqual(self.rule()['last_outcome'], 'Source unavailable')
        self.assertIn('Source unavailable', self.rule()['last_error'])
        with self.service.rosters.lock(), connect(self.service.database) as db:
            db.execute('UPDATE course_terms SET is_current=0 WHERE id=?', (self.term,))
        self.due()
        self.observe_mock.reset_mock()
        self.assertFalse(self.workflows.process_one())  # skipped, never "unavailable"
        self.observe_mock.assert_not_called()
        self.assertEqual((self.rule()['last_outcome'], self.rule()['enabled']), ('Finished: term ended', 1))

    def test_a_requested_preparation_that_would_replace_waiting_cards_needs_approval(self):
        self.prepare('first')
        self.run_with(self.propose())
        session = self.service.open_session('test')
        result = json.loads(self.service.run_tool(session, 'prepare_assessment', {'event_id': 'one'})[0])
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        self.assertIn('replaces 1 change', result['why_approval'])
        self.assertTrue(self.runs()[0]['actions'][0]['pending'])  # nothing withdrawn by the model's request
        card = self.runs()[0]['actions'][0]
        self.client.post('/api/assistant/actions/' + card['id'] + '/decline', json={}, headers=HEADERS)
        result = json.loads(self.service.run_tool(session, 'prepare_assessment', {'event_id': 'one'})[0])
        self.assertEqual(result['outcome'], 'done', result)

    def test_reads_do_not_observe_and_a_batch_approval_observes_once(self):
        self.prepare('batch')
        reads = []
        def propose(c, text):
            self.observe_mock.reset_mock()
            c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/absences/students/search',
                                                            'body': {'query': '123'}, 'summary': 'Find a student'})
            reads.append(self.observe_mock.call_count)
            for title in ('First', 'Second'):
                c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
                    'body': {'title': title, 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'}, 'summary': title})
            c.emit('assistant', text='Two reminders wait.')
        self.run_with(propose)
        self.assertEqual(reads, [0])
        batch = self.client.get('/api/assistant/actions').json['batches'][0]
        cards = {a['id']: a for a in self.runs()[0]['actions']}
        self.observe_mock.reset_mock()
        answer = self.client.post('/api/assistant/batches/' + batch['id'] + '/approve',
                                  json={'seen': {aid: cards[aid]['seen'] for aid in batch['action_ids']}}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual([r.get('status') for r in answer.json['results']], [201, 201])
        self.assertEqual(self.observe_mock.call_count, 1)


class RealReadinessTests(unittest.TestCase):
    """Real owner projections, no patched observation (2026-10-09 review)."""
    import_students = exam_fixture.ExamWorkflowTests.import_students
    set_date = ReadinessEligibilityTests.set_date
    run_with = WorkflowsTests.run_with

    def setUp(self):
        exam_fixture.ExamWorkflowTests.setUp(self)
        self.service = self.app.extensions['assistant']
        self.workflows = self.service.workflows
        endpoint = patch.object(self.service.followups, 'endpoint', return_value={'name': 'Synthetic', 'kind': 'hosted', 'model': 'fake'})
        endpoint.start()
        self.addCleanup(endpoint.stop)

    def on(self, day):
        """The campus date, for the exam overview and the reminder window alike."""
        stack = [patch('exam_workflow.overview.campus_today', return_value=day.isoformat()),
                 patch('assistant.workflows.campus_today', return_value=day)]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)

    def test_the_day_changing_keeps_the_fingerprint_and_a_moved_date_is_named(self):
        self.set_date('2026-10-30')
        self.on(date(2026, 10, 9))
        first = self.workflows.observe('readiness', self.event['id'])
        self.on(date(2026, 10, 10))
        second = self.workflows.observe('readiness', self.event['id'])
        self.assertNotEqual(first['overview']['dates']['today'], second['overview']['dates']['today'])
        self.assertEqual(self.workflows.fingerprint(first), self.workflows.fingerprint(second))
        prepared = self.client.post('/api/assistant/preparations', json={'kind': 'readiness', 'target': self.event['id'],
                                                                         'request_id': 'moved'}, headers=HEADERS).json
        self.workflows.guard(prepared['id'])  # unchanged: passes
        self.set_date('2026-10-31')
        with self.assertRaisesRegex(Exception, 'the exam date moved'):
            self.workflows.guard(prepared['id'])

    def test_once_now_with_a_longer_reminder_reaches_the_model(self):
        self.set_date('2026-10-19')  # ten days ahead: due within 14 days, not within 7
        self.on(date(2026, 10, 9))
        answer = self.client.post('/api/assistant/preparations', json={'kind': 'readiness', 'target': self.event['id'], 'lead_days': 14,
                                                                       'request_id': 'once-now'}, headers=HEADERS)
        self.assertEqual(answer.status_code, 202, answer.json)
        prompts = []
        self.run_with(lambda c, text: (prompts.append(text), c.emit('assistant', text='Ready.')))
        self.assertEqual(self.workflows.listing()['preparations'][0]['state'], 'ready')
        self.assertTrue(prompts)

    def test_dispatch_reads_the_source_without_the_approval_or_lifecycle_lock(self):
        self.set_date('2026-10-19')
        self.on(date(2026, 10, 9))
        self.client.post('/api/assistant/preparations', json={'kind': 'readiness', 'target': self.event['id'], 'request_id': 'locks'},
                         headers=HEADERS)
        held, read = [], self.workflows.read
        def probe(path):
            held.append((path, self.service._approve_lock._is_owned(), bool(getattr(self.service.rosters._lock_state, 'held', False))))
            return read(path)
        with patch.object(self.workflows, 'read', side_effect=probe):
            self.run_with(lambda c, text: c.emit('assistant', text='Ready.'))
        self.assertTrue(held)
        self.assertEqual([h for h in held if h[1] or h[2]], [])
        self.assertEqual(self.workflows.listing()['preparations'][0]['state'], 'ready')


class MorningWaitTests(MorningBase):
    """The daily check runs in the Inbox worker, where Canvas writes never run: it waits on the catalog's running sends and posts,
    and on a morning attempt the morning sync will still try again before its end (2026-10-09 review)."""

    def wait(self, when):
        with connect(self.app.config['DATABASE']) as db:
            return self.app.extensions['assistant'].workflows.morning_wait(db, self.term, when.astimezone(CAMPUS_ZONE))

    def attempt(self, status, at):
        with connect(self.app.config['DATABASE']) as db:
            db.execute('INSERT INTO gradebook_canvas_nightly_runs(id,term_id,night,kind,status,detail_json,created_at) VALUES(?,?,?,?,?,?,?)',
                       (str(uuid.uuid4()), self.term, at.astimezone(CAMPUS_ZONE).date().isoformat(), 'sync', status, '{}', at.isoformat()))

    def running_post(self, at):
        ident = str(uuid.uuid4())
        with connect(self.app.config['DATABASE']) as db:
            db.execute('''INSERT INTO gradebook_canvas_posts(id,term_id,item_id,action,request_id,request_sha256,fingerprint,plan_json,
                status,progress_json,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (ident, self.term, self.quiz, 'post', 'post-' + ident, 'x', 'x', '{}', 'running', '{}', 'instructor', at.isoformat()))

    def test_waits_for_the_morning_run_and_for_a_post_running_in_another_process(self):
        self.assertIn('morning Canvas sync', self.wait(MORNING))  # nothing recorded yet this morning
        self.assertEqual(self.wait(MORNING.replace(hour=13)), '')  # 08:00 Central: check and report what is missing
        self.morning_run()
        self.assertEqual(self.wait(MORNING + timedelta(minutes=10)), '')
        self.running_post(MORNING + timedelta(minutes=5))
        self.assertIn('current Canvas work', self.wait(MORNING + timedelta(minutes=10)))
        self.assertEqual(self.wait(MORNING + timedelta(minutes=25)), '')  # running for over 15 minutes: cut off, not running

    def test_waits_while_a_failed_morning_will_still_be_tried_again(self):
        self.attempt('signed_out', MORNING)
        self.assertIn('try again', self.wait(MORNING + timedelta(minutes=5)))
        self.attempt('failed', MORNING.replace(hour=12, minute=30))  # 07:30 Central: its retry would come after 08:00
        self.assertEqual(self.wait(MORNING.replace(hour=12, minute=35)), '')

    def test_the_morning_wait_is_neutral_status_and_the_check_records_its_outcome(self):
        service = self.app.extensions['assistant']
        data = {'kind': 'reconciliation', 'target': self.term, 'request_id': str(uuid.uuid4())}
        preview = self.client.post('/api/assistant/workflows/preview', json=data, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        rule = self.client.post('/api/assistant/workflows', json={**data, 'fingerprint': preview.json['fingerprint']}, headers=HEADERS).json
        with service.rosters.lock(), connect(service.database) as db:
            db.execute('UPDATE assistant_workflows SET next_check=?', (MORNING.timestamp(),))
        with patch('assistant.workflows.time.time', return_value=MORNING.timestamp()):
            self.assertTrue(service.workflows.process_one())
        row = next(r for r in self.client.get('/api/assistant/automations').json['automations'] if r['id'] == rule['id'])
        self.assertEqual((row['waiting'], row['blocker'], row['last_checked_at']),
                         ('Waiting for the morning Canvas sync before checking grades.', '', None))
        self.morning_run()
        with patch('assistant.workflows.time.time', return_value=(MORNING + timedelta(minutes=10)).timestamp()):
            self.assertTrue(service.workflows.process_one())
        row = next(r for r in self.client.get('/api/assistant/automations').json['automations'] if r['id'] == rule['id'])
        self.assertEqual(row['waiting'], '')
        self.assertTrue(row['last_checked_at'])
        self.assertIn(row['last_outcome'], ('Changes found; preparing a review', 'No discrepancies'))
