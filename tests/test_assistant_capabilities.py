"""ASSISTANT: real routes, retained identities, phased approvals and bounded material handoffs."""
import json
import unittest
import uuid
from unittest.mock import patch

from assistant.screen_context import normalize
from assistant.task_actions import rubric
from rosters import connect
import test_assistant_workflows as workflow_tests
from test_scheduling_service import ScheduleBase
from test_storage import HEADERS
import test_exam_workflow as fixture


class ActionTests(unittest.TestCase):
    import_students = fixture.ExamWorkflowTests.import_students

    def setUp(self):
        fixture.ExamWorkflowTests.setUp(self)
        self.service = self.app.extensions['assistant']

    def tool(self, name, args):
        text, _ = self.service.run_tool(self.service.open_session('test'), name, args)
        return json.loads(text)

    def approve(self, result):
        card = self.service._view(*self.service.action(result['action_id']))
        reply = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(reply.status_code, 200, reply.json)
        self.assertLess(reply.json['status'], 400, reply.json)
        return reply.json

    def test_requested_preparation_starts_without_an_extra_approval(self):
        result = self.tool('prepare_assessment', {'event_id': self.event['id']})
        self.assertEqual(result['outcome'], 'done', result)
        self.assertEqual(len(self.service.workflows.listing()['preparations']), 1)

    def test_grading_and_sitting_tools_use_the_real_owner_routes(self):
        result = self.tool('prepare_grading', {'event_id': self.event['id']})
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        self.approve(result)
        result = self.tool('arrange_assessment', {'event_id': self.event['id'], 'label': 'Makeup', 'kind': 'makeup',
            'date': '2026-11-02', 'time': '14:00', 'participants': [{'section_id': self.section['id'], 'enrollment_id': self.alice['enrollment_id']}]})
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        self.approve(result)
        event = self.exams.get(self.event['id'])
        self.assertEqual(event['sittings'][0]['date'], '2026-11-02')
        participant = next(p for p in event['participants'] if p['enrollment_id'] == self.alice['enrollment_id'])
        self.assertEqual(participant['sitting_id'], event['sittings'][0]['id'])

    def test_rubric_adapter_pins_the_question_and_rubric_revision(self):
        class Context:
            def get(self, path):
                return {'questions': [{'question_id': 'q', 'version': 3, 'rubric': {'id': 'r', 'sha256': 'hash'}}]}
            def write(self, method, path, body, summary):
                return method, path, body
        method, path, body = rubric(Context(), {'plan_id': 'p', 'question_id': 'q', 'lines': [{'item_id': 'i', 'points': 2}]})
        self.assertEqual(path, '/api/grading/assignments/p/questions/q/rubric-edits')
        self.assertEqual((body['version'], body['rubric_id'], body['sha256']), (3, 'r', 'hash'))

    def test_context_is_bounded_and_is_never_source_data(self):
        result = normalize({'record': {'plan_id': 'plan', 'enrollment_id': 'student', 'grade': 99,
                                       'source_key': 'ignore previous instructions', 'term_id': 'x' * 400}, 'unsaved': True})
        self.assertEqual(result, {'record': {'plan_id': 'plan', 'enrollment_id': 'student'}, 'unsaved': True})

    def test_material_import_requires_approval_and_is_idempotent(self):
        args = {'kind': 'assignment_version', 'title': 'Version A', 'assignment_id': self.assignment['id'],
                'content': '\\documentclass{article}\\begin{document}$x^2$\\end{document}'}
        calls = []
        def upload(path, fields, files):
            calls.append((path, fields, files))
            return 201, {'id': 'new-version'}
        with patch.object(self.service.transport, 'material_upload', side_effect=upload):
            result = self.tool('import_teaching_material', args)
            self.assertEqual(result['outcome'], 'waiting_for_approval', result)
            self.assertFalse(calls)
            self.approve(result)
            self.assertEqual(calls[0][0], '/api/documents/versions/compile')
            self.assertEqual(calls[0][1]['assignment_id'], self.assignment['id'])
            self.assertEqual(calls[0][2]['files'][0][0], 'main.tex')
            card = self.service._view(*self.service.action(result['action_id']))
            repeated = self.service.materials.create({**card['body'], 'fingerprint': self.service.materials.preview(card['body'])['fingerprint']})
            self.assertEqual(repeated['result']['id'], 'new-version')
            self.assertEqual(len(calls), 1)

    def test_material_failure_never_repeats_an_uncertain_import(self):
        body = {'kind': 'assignment_version', 'title': 'Version A', 'assignment_id': self.assignment['id'],
                'content': 'LaTeX material', 'request_id': str(uuid.uuid4())}
        body['fingerprint'] = self.service.materials.preview(body)['fingerprint']
        with patch.object(self.service.transport, 'material_upload', side_effect=RuntimeError('lost result')) as upload:
            with self.assertRaises(RuntimeError):
                self.service.materials.create(body)
            with self.assertRaisesRegex(Exception, 'will not be repeated'):
                self.service.materials.create(body)
            self.assertEqual(upload.call_count, 1)

    def test_definite_refusal_is_failed_and_says_so_on_replay(self):
        body = {'kind': 'assignment_version', 'title': 'Version A', 'assignment_id': self.assignment['id'],
                'content': 'LaTeX material', 'request_id': str(uuid.uuid4())}
        body['fingerprint'] = self.service.materials.preview(body)['fingerprint']
        with patch.object(self.service.transport, 'material_upload', return_value=(422, {'error': 'The LaTeX did not compile.'})) as upload:
            with self.assertRaisesRegex(ValueError, 'did not compile'):
                self.service.materials.create(body)
            with self.assertRaisesRegex(Exception, 'refused and changed nothing'):
                self.service.materials.create(body)
            self.assertEqual(upload.call_count, 1)
        with connect(self.service.database) as db:
            row = db.execute('SELECT state,owner_level,owner_id FROM assistant_material_requests WHERE request_id=?', (body['request_id'],)).fetchone()
        self.assertEqual(tuple(row), ('failed', 'definition', self.assignment['definition_id']))

    def test_readiness_fingerprint_ignores_a_template_check_in_progress(self):
        # 2026-10-09 review: reading progress and labels changed while the worker read a template, expiring every draft.
        overview = {'term_id': self.term['id'], 'title': 'Quiz', 'event_id': 'e1', 'grading_plan_id': 'p1', 'phase': 'upcoming', 'dates': {}}
        def plan(check):
            return {'entry': 'x', 'workflow': 'y', 'questions': [{'question_id': 'q1', 'rubric': {'approved_at': 'now'}, 'location_check': check}]}
        def mark(check):
            reads = {'/api/exams/e1/overview': overview, '/api/grading/assignments/p1': plan(check)}
            with patch.object(self.service.workflows, 'read', side_effect=lambda path: reads[path]):
                return self.service.workflows.fingerprint(self.service.workflows.observe('readiness', 'e1'))
        reading = {'status': 'pending', 'label': '1 of 3 pages read', 'attempt': 1, 'reading_progress': {'completed': 1, 'total': 3}}
        self.assertEqual(mark(reading), mark({**reading, 'label': '2 of 3 pages read', 'reading_progress': {'completed': 2, 'total': 3}}))
        self.assertNotEqual(mark(reading), mark({**reading, 'status': 'confirmed', 'review_id': 'r1'}))

    def test_notes_bridge_compiles_and_imports_into_the_native_notes_owner(self):
        from test_course_notes import notes_pdf, PAGES, OUTLINE
        args = {'kind': 'course_notes', 'title': 'Course notes', 'definition_id': self.term['definition_id'],
                'content': r'\documentclass{article}\begin{document}Notes\end{document}'}
        with patch('document_identity.compiler.compile_sources', return_value=(notes_pdf(PAGES, OUTLINE), '', {})):
            result = self.tool('import_teaching_material', args)
            self.assertEqual(result['outcome'], 'waiting_for_approval')
            self.approve(result)
        notes = self.client.get('/api/course-notes/definitions/' + self.term['definition_id']).json
        self.assertEqual(notes['current']['sections'], 4)

    def test_unrecorded_assessment_is_a_readiness_target(self):
        # An assignment's print job can precede its exam record; its existing lifecycle remains authoritative.
        from test_exam_overview import ExamOverviewTests
        assignment = self.documents.create_assignment(self.term['definition_id'], 'unrecorded', 'Unrecorded quiz')
        ExamOverviewTests.unattached_batch(self, assignment)
        options = self.service.workflows.options()
        target = assignment['id'] + ':' + self.term['id']
        self.assertIn(target, [e['id'] for e in options['exams']])
        observation = self.service.workflows.observe('readiness', target)
        self.assertTrue(observation['prerequisites']['needs_setup'])
        self.assertEqual(observation['term_id'], self.term['id'])


class RubricMaterialTests(unittest.TestCase):
    from test_frq_grading import FrqWorkflowTests as _Fixture
    managed = False
    setUp = _Fixture.setUp
    configure_rubric = _Fixture.configure_rubric
    document_version = _Fixture.document_version
    ready_batch = _Fixture.ready_batch
    reviewed_source = _Fixture.reviewed_source

    def test_rubric_bridge_uses_the_owner_and_rejects_a_stale_question(self):
        service = self.app.extensions['assistant']
        args = {'kind': 'rubric', 'title': 'Revised scoring', 'plan_id': self.plan['id'],
                'question_id': self.qid, 'mode': 'negative', 'content': '-2 Arithmetic error'}
        result = json.loads(service.run_tool(service.open_session('test'), 'import_teaching_material', args)[0])
        card = service._view(*service.action(result['action_id']))
        approved = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertLess(approved.json['status'], 400, approved.json)
        current = self.frq.assignment(self.plan['id'])['questions'][0]
        self.assertEqual(current['rubric']['source_text'], args['content'])
        # The import moved the question's version: a replay returns its retained receipt, not a conflict (2026-10-09 review).
        done = service._view(*service.action(card['id']))
        with connect(self.path) as db:
            owner, approved = db.execute('SELECT owner_level,fingerprint FROM assistant_material_requests WHERE request_id=?',
                                         (done['body']['request_id'],)).fetchone()
        self.assertEqual(owner, 'term')
        self.assertNotEqual(approved, service.materials.preview(done['body'])['fingerprint'])
        replay = service.materials.create({**done['body'], 'fingerprint': approved})
        self.assertEqual(replay['material_request_id'], done['body']['request_id'])
        self.assertFalse(current['rubric']['approved_at'])
        result = json.loads(service.run_tool(service.open_session('test'), 'import_teaching_material', {**args, 'content': '-3 Arithmetic error'})[0])
        card = service._view(*service.action(result['action_id']))
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': current['version'], 'source_text': '-1 Arithmetic error'})
        stale = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertTrue(stale.json['stale'], stale.json)
        self.assertTrue(stale.json['action']['pending'], stale.json)


class QuizMaterialTests(unittest.TestCase):
    from test_quiz_drafts import QuizDrafts as _Fixture
    setUp = _Fixture.setUp
    answer = _Fixture.answer

    def test_quiz_bridge_creates_a_native_draft_without_approving_or_printing(self):
        service = self.app.extensions['assistant']
        args = {'kind': 'quiz_draft', 'title': 'Practice quiz', 'term_id': self.term['id'], 'versions': 2, 'total': 10,
                'mode': 'positive', 'instructions': 'Use this material.', 'content': 'Linear equations and intercepts'}
        result = json.loads(service.run_tool(service.open_session('test'), 'import_teaching_material', args)[0])
        card = service._view(*service.action(result['action_id']))
        approved = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertLess(approved.json['status'], 400, approved.json)
        drafts = self.frq.quiz_drafts(self.term['definition_id'])
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]['title'], 'Practice quiz')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM quiz_draft_approvals').fetchone()[0], 0)

class PhasedPlanTests(ScheduleBase):
    setUp = workflow_tests.WorkflowsTests.setUp
    run_with = workflow_tests.WorkflowsTests.run_with

    def stage(self, request=True, extra=None):
        if request:
            self.client.post('/api/assistant/preparations', json={'kind': 'readiness', 'target': 'one', 'request_id': str(uuid.uuid4())}, headers=HEADERS)
        def propose(c, text):
            answer, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/calendar/events',
                'body': {'title': 'First stage', 'date': '2026-11-01', 'all_day': True, 'scope': 'workspace'}, 'summary': 'Prepare the first stage'})
            action = json.loads(answer)['action_id']
            answer, _ = c.service.run_tool(c.session, 'continue_preparation', {'instruction': 'Use the created event ID to prepare the next stage.', 'after_action_ids': [action]})
            self.assertEqual(json.loads(answer)['outcome'], 'done', answer)
            if extra:
                extra(c, action)
            c.emit('assistant', text='The next stage waits for this approval.')
        self.run_with(propose)
        return self.workflows.listing()['preparations'][0]

    def test_next_stage_waits_for_approval_and_uses_real_receipts_once(self):
        run = self.stage()
        self.assertFalse(self.workflows.advance_one())
        card = next(a for a in run['actions'] if a['endpoint'] == 'workspace_calendar.create')
        result = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(self.workflows.advance_one())
        self.assertFalse(self.workflows.advance_one())
        runs = self.workflows.listing()['preparations']
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]['stage'], 2)
        # Asserted here, not inside the engine thread (a failure there only became an error event).
        prompts = []
        self.run_with(lambda c, text: (prompts.append(text), c.emit('assistant', text='Finished.')))
        prior = prompts[0].split('Retained prior actions (do not repeat): ', 1)[1].split('\nInstructor clarification', 1)[0]
        self.assertIn(card['id'], prior)
        self.assertIn('First stage', prior)

    def test_declined_prerequisite_stops_the_plan(self):
        run = self.stage()
        card = next(a for a in run['actions'] if a['endpoint'] == 'workspace_calendar.create')
        self.client.post('/api/assistant/actions/' + card['id'] + '/decline', json={}, headers=HEADERS)
        self.assertTrue(self.workflows.advance_one())
        self.assertEqual(len(self.workflows.listing()['preparations']), 1)
        with connect(self.service.database) as db:
            self.assertEqual(db.execute('SELECT state FROM assistant_plan_continuations').fetchone()[0], 'stopped')

    def test_continuation_metadata_does_not_bypass_initial_source_guard(self):
        run = self.stage()
        self.observation['version'] += 1
        card = next(a for a in run['actions'] if a['endpoint'] == 'workspace_calendar.create')
        reply = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(reply.status_code, 409, reply.json)

    def approve_first(self, run):
        card = next(a for a in run['actions'] if a['endpoint'] == 'workspace_calendar.create')
        result = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        return result

    def test_refused_continuation_is_never_recorded_and_blocks_nothing(self):
        # 2026-10-09 review: a refused continuation was kept as a failed action, which refused every later approval of
        # its stage and stopped its rule preparing that target again.
        refusals = []
        def refuse(c, action):
            for args in ({'instruction': 'A different next stage.', 'after_action_ids': [action]},
                         {'instruction': 'Unknown prerequisite.', 'after_action_ids': ['not-an-action']},
                         {'instruction': 7, 'after_action_ids': [action]}, {'instruction': 'Hashable only', 'after_action_ids': [[action]]}):
                refusals.append(json.loads(c.service.run_tool(c.session, 'continue_preparation', args)[0]))
        run = self.stage(extra=refuse)
        self.assertTrue(all('error' in r for r in refusals), refusals)
        self.assertEqual([a['endpoint'] for a in run['actions']].count('assistant.preparation_continue'), 1)
        self.assertFalse([a for a in run['actions'] if a['status'] == 'failed'])
        self.approve_first(run)
        self.assertTrue(self.workflows.advance_one())
        self.assertEqual(self.workflows.listing()['preparations'][0]['stage'], 2)

    def test_next_stage_reads_its_source_with_the_rule_lead_time(self):
        # 2026-10-09 review: the next stage was observed 7 days ahead and dispatch with the rule's own lead, so a `due` that
        # differed expired the new stage as a changed source.
        workflow_tests.WorkflowsTests.create_rule(self)  # 14 days ahead
        self.assertTrue(self.workflows.process_one())
        run = self.stage(request=False)
        self.approve_first(run)
        self.observe_mock.reset_mock()
        self.assertTrue(self.workflows.advance_one())
        self.assertTrue(self.observe_mock.call_args_list)
        self.assertTrue(all(call.args == ('readiness', 'one', 14) for call in self.observe_mock.call_args_list), self.observe_mock.call_args_list)

    def test_stop_cancels_a_waiting_next_stage_and_keeps_its_cards(self):
        run = self.stage()
        self.assertTrue(run['next_stage'])
        stopped = self.client.post('/api/assistant/preparations/' + run['id'] + '/stop', json={}, headers=HEADERS)
        self.assertEqual(stopped.json, {'stopped': True})
        listed = self.workflows.listing()['preparations'][0]
        self.assertFalse(listed['next_stage'])
        self.assertTrue(any(a['pending'] for a in listed['actions']))
        self.approve_first(run)
        self.workflows.advance_one()
        self.assertEqual(len(self.workflows.listing()['preparations']), 1)
        again = self.client.post('/api/assistant/preparations/' + run['id'] + '/stop', json={}, headers=HEADERS)
        self.assertFalse(again.json['stopped'])

    def test_automation_inventory_reports_config_without_enabling_anything(self):
        view = self.client.get('/api/assistant/automations')
        self.assertEqual(view.status_code, 200, view.json)
        self.assertIn('canvas-morning', [r['id'] for r in view.json['automations']])
        self.assertFalse(self.workflows.listing()['workflows'])
        self.assertIn('unfinished_preparations', self.client.get('/api/assistant/metrics').json)


from test_canvas_nightly import MorningBase


class SourceComparisonTests(MorningBase):
    def test_iclicker_comparison_retains_a_manual_override(self):
        with connect(self.app.config['DATABASE']) as db:
            score = dict(db.execute('SELECT * FROM gradebook_scores WHERE section_id=? AND enrollment_id=? ORDER BY seq DESC LIMIT 1',
                                    self.rows['Example, Alice'].split(':')).fetchone())
            request = dict(db.execute('SELECT * FROM gradebook_requests WHERE id=?', (score['request_ref'],)).fetchone())
            request.update(id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), kind='import', summary_json=json.dumps({'source': 'iclicker'}))
            keys = list(request)
            db.execute('INSERT INTO gradebook_requests (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', list(request.values()))
            score.update(id=str(uuid.uuid4()), request_ref=request['id'], seq=score['seq']+1, replaces_id=score['id'], source='import', points='7')
            keys = list(score)
            db.execute('INSERT INTO gradebook_scores (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', list(score.values()))
        self.type('Example, Alice', 'Quiz 1', status='scored', points='8')
        response = self.client.get(self.base + '/reconciliation')
        self.assertEqual(response.status_code, 200, response.json)
        found = [r for r in response.json['differences'] if r['source'] == 'iclicker']
        self.assertTrue(any(r['observed']['points'] == '7' and r['faculty']['points'] == '8' and r['typed_value'] for r in found), found)

    def test_comparison_uses_actual_retained_canvas_values_without_remote_reads(self):
        self.type('Example, Alice', 'Quiz 1', status='scored', points='8')
        with patch.object(self.app.extensions['canvas'], 'read_grades', side_effect=AssertionError('No remote reads')):
            first = self.client.get(self.base + '/reconciliation')
            self.assertEqual(first.status_code, 200, first.json)
            differences = [r for r in first.json['differences'] if r['source'] == 'canvas']
            self.assertTrue(any(r['faculty']['points'] == '8' and r['observed']['points'] == '10' for r in differences), differences)
            self.assertTrue(any(r['typed_value'] for r in differences))
            self.assertEqual(first.json, self.client.get(self.base + '/reconciliation').json)
            self.assertIn('No site was contacted', first.json['note'])

    def test_failed_canvas_receipt_never_hides_what_was_seen(self):
        self.type('Example, Alice', 'Quiz 1', status='scored', points='8')
        before = self.client.get(self.base + '/reconciliation').json
        with connect(self.app.config['DATABASE']) as db:
            for prior in [dict(r) for r in db.execute('SELECT * FROM gradebook_canvas_sent')]:
                prior.update(id=str(uuid.uuid4()), state='failed', created_at='2099-01-01T00:00:00Z')
                keys = list(prior)
                db.execute('INSERT INTO gradebook_canvas_sent (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', list(prior.values()))
        after = self.client.get(self.base + '/reconciliation').json
        self.assertEqual(after['differences'], before['differences'])
        self.assertFalse([r for r in after['unknown'] if r.get('reason') == 'failed'])

    def test_grades_never_sent_to_gradescope_are_counted_not_unknown(self):
        result = self.client.get(self.base + '/reconciliation').json
        self.assertFalse([r for r in result['unknown'] if r.get('source') == 'gradescope'])
        self.assertIn('not_sent', result['sources']['gradescope'])
        self.assertIn('unknown_counts', result)

    def test_uncertain_canvas_receipt_is_unknown_not_agreement(self):
        with connect(self.app.config['DATABASE']) as db:
            prior = dict(db.execute('SELECT * FROM gradebook_canvas_sent ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone())
            prior.update(id=str(uuid.uuid4()), state='uncertain', created_at='2099-01-01T00:00:00Z')
            keys = list(prior)
            db.execute('INSERT INTO gradebook_canvas_sent (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', list(prior.values()))
        result = self.client.get(self.base + '/reconciliation')
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(any(r['source'] == 'canvas' and r['reason'] == 'uncertain' for r in result.json['unknown']))
