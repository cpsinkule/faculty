"""ASSISTANT: usable approvals, complete bounded reads and independent verification.

Run with manage_checks.environment on NVMe. All owners and outside services are synthetic.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import policy
from assistant.pagination import page
from assistant.transport import render, RESULT_CHARS
from assistant.code_checks import run, selection
from followup_plan_eval import evaluate
from test_scheduling_service import ScheduleBase, OFFICE
from test_storage import HEADERS


class IntegrationTests(ScheduleBase):
    def test_schedule_activation_and_undo_work_through_real_approval_routes(self):
        self.assertEqual(self.save([OFFICE]).status_code, 200)
        service = self.app.extensions['assistant']
        session = service.open_session('test')
        path = f'/api/scheduling/terms/{self.term}/use'
        for target, confirmed in ((path, True), (path + '/undo', False)):
            prepared = service.write(session, 'POST', target, {'request_id': '$new_request_id'}, 'Change the term schedule')
            self.assertEqual(prepared['outcome'], 'waiting_for_approval', prepared)
            card = service._view(*service.action(prepared['action_id']))
            self.assertNotIn('confirmation', card['body'])
            response = self.client.post('/api/assistant/actions/' + card['id'] + '/approve', json={'seen': card['seen']}, headers=HEADERS)
            self.assertEqual(response.json['status'], 200, response.json)
            self.assertEqual(response.json['result']['in_use'], confirmed)

    def test_confirmation_metadata_covers_known_service_requirements(self):
        required = {'scheduling.use': 'use', 'scheduling.undo': 'undo', 'canvas.content_office_hours_put': 'put',
                    'gradebook.canvas_cell_send': 'send-cell', 'gradebook.canvas_quiz_grade': 'set-quiz-grade', 'quiz_drafts.rebuild': 'rebuild',
                    'quiz_drafts.scrap': 'scrap'}
        for endpoint, confirmation in required.items():
            self.assertEqual(policy.base_rule(endpoint).confirmation, confirmation)
        self.assertEqual(policy.base_rule('frq_regrade_panel.apply').tier, 'page')

    def test_student_lookup_does_not_compute_gradebooks(self):
        service = self.app.extensions['assistant']
        original = service.transport.request
        def checked(method, path, body=None):
            self.assertFalse(path.startswith('/api/gradebook/terms/'), path)
            return original(method, path, body)
        with patch.object(service.transport, 'request', side_effect=checked):
            result, _ = service.run_tool(service.open_session('test'), 'find_student', {'query': 'Alice'})
        self.assertTrue(json.loads(result)['matches'])

    def test_paginated_tool_reads_reject_changed_sources_and_blocked_routes(self):
        service = self.app.extensions['assistant']
        session = service.open_session('test')
        args = {'path': '/api/absences/students', 'pointer': '/groups', 'offset': 0, 'limit': 1}
        text, _ = service.run_tool(session, 'faculty_get', args)
        first = json.loads(text)
        self.assertEqual(first['page']['total'], 1)
        text, _ = service.run_tool(session, 'faculty_get', {**args, 'fingerprint': 'stale'})
        self.assertIn('changed', json.loads(text)['error'])
        text, _ = service.run_tool(session, 'faculty_get', {'path': '/api/assistant/sessions', 'pointer': ''})
        self.assertIn('error', json.loads(text))


class BoundedReadTests(unittest.TestCase):
    def test_large_results_are_valid_bounded_json_and_show_omissions(self):
        value = {'entries': [{'body': 'synthetic text ' * 450} for _ in range(100)], 'token': 'not-returned'}
        text = render(value)
        self.assertLessEqual(len(text), RESULT_CHARS)
        parsed = json.loads(text)
        self.assertTrue(parsed['_truncated']['fields'])
        self.assertNotIn('not-returned', text)
        self.assertTrue(any(f['pointer'].startswith('/entries') for f in parsed['_truncated']['fields']))

    def test_every_page_and_string_character_can_be_recovered(self):
        source = {'a/b': {'~items': list(range(125))}, 'text': 'αβγ' * 100}
        pointer = '/a~1b/~0items'
        first = page(source, {'pointer': pointer, 'limit': 60})
        all_items = first['data']
        while first['page']['next_offset'] is not None:
            first = page(source, {'pointer': pointer, 'offset': first['page']['next_offset'], 'limit': 60,
                                  'fingerprint': first['page']['fingerprint']})
            all_items += first['data']
        self.assertEqual(all_items, list(range(125)))
        self.assertEqual(page(source, {'pointer': '/text', 'offset': 299, 'limit': 3})['data'], 'γ')
        # A mistyped index says what is wrong; a field that is gone still says so.
        for bad in ('-1', 'x', '\u00b2'):
            with self.assertRaisesRegex(ValueError, 'nonnegative'):
                page(source, {'pointer': '/a~1b/~0items/' + bad})
        with self.assertRaisesRegex(ValueError, 'no longer exists'):
            page(source, {'pointer': '/a~1b/~0items/999'})
        with self.assertRaisesRegex(ValueError, 'no longer exists'):
            page(source, {'pointer': '/a~1b/missing'})

    def test_continuations_fingerprint_the_paged_node_not_the_whole_answer(self):
        """COMPLETE READS (2026-10-09 review): an overview's `generated_at` changes on every read; only the paged node counts."""
        source = {'generated_at': '1', 'attention': list(range(5)), 'other': {'progress': 1}}
        first = page(source, {'pointer': '/attention', 'limit': 2})
        later = {**source, 'generated_at': '2', 'other': {'progress': 2}}
        second = page(later, {'pointer': '/attention', 'offset': 2, 'limit': 2, 'fingerprint': first['page']['fingerprint']})
        self.assertEqual(second['data'], [2, 3])
        with self.assertRaisesRegex(ValueError, 'source changed'):
            page({**later, 'attention': [0, 1, 9, 3, 4]}, {'pointer': '/attention', 'offset': 2, 'limit': 2,
                                                          'fingerprint': first['page']['fingerprint']})
        # A fingerprint belongs to its pointer: it never continues another field with equal content.
        twin = {'a': [1, 2], 'b': [1, 2]}
        with self.assertRaisesRegex(ValueError, 'source changed'):
            page(twin, {'pointer': '/b', 'offset': 1, 'limit': 1, 'fingerprint': page(twin, {'pointer': '/a', 'limit': 1})['page']['fingerprint']})

    def test_partial_result_notes_name_a_pageable_route_only_for_route_reads(self):
        value = {'rows': [{'n': n} for n in range(200)]}
        routed = json.loads(render(value, source='/api/inbox/jobs?status=current'))['_truncated']['note']
        self.assertIn('faculty_get with path /api/inbox/jobs?status=current', routed)
        composed = json.loads(render(value, composed=True))['_truncated']['note']
        self.assertNotIn('faculty_get with', composed)
        self.assertIn('ask this tool for less', composed)
        from assistant import tools
        registry = tools.registry()
        self.assertEqual(registry['inbox'].source({'status': 'failed'}), '/api/inbox/jobs?files=input&status=failed')
        self.assertEqual(registry['faculty_get'].source({'path': '/api/exams'}), '/api/exams')
        self.assertEqual(registry['class_schedule'].source({'section_id': 's1', 'start': '2026-10-01', 'end': '2026-10-31'}),
                         '/api/scheduling/sections/s1/class-schedule?start=2026-10-01&end=2026-10-31')
        for name in ('student_paper', 'catalog_sql', 'find_student', 'student_grades'):
            self.assertIsNone(registry[name].source, name)

    def test_plan_evaluation_checks_identity_values_recipients_and_missing_work(self):
        action = {'endpoint': 'student_support.create', 'body': {'kind': 'appointment', 'enrollment_id': 'wrong', 'date': '2026-10-10'},
                  'pending': False, 'status': 'succeeded'}
        score = evaluate({'actions': [action]}, [{'endpoint': 'student_support.create', 'body': {
            'kind': 'followup', 'enrollment_id': 'right', 'date': '2026-10-11'}}], enrollment_id='right', final_state='resolved')
        self.assertEqual((score['incorrect_student'], score['incorrect_arguments'], score['incorrect_completion']), (1, 1, 1))
        score = evaluate({'actions': []}, ['gmail_mail.send'], final_state='resolved')
        self.assertEqual(score['incorrect_completion'], 1)
        score = evaluate({'actions': [{'**': '', 'endpoint': 'gmail_mail.send', 'body': {'to': ['wrong@example.test']},
                                     'pending': False, 'status': 'succeeded'}]}, ['gmail_mail.send'], allowed_recipients=['right@example.test'])
        self.assertEqual(score['incorrect_recipients'], 1)

    def test_independent_tests_fail_without_trusting_the_coding_report(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            (repo / 'tests').mkdir()
            (repo / 'tests/test_app.py').write_text('import unittest\nclass Check(unittest.TestCase):\n def test_truth(self): self.assertTrue(False)\n')
            self.assertEqual(selection(repo, ['app.py'])[0], ['test_app'])
            with self.assertRaisesRegex(ValueError, 'checks failed'):
                run(repo, Path(__file__).resolve().parents[1], ['app.py'], 'head', 'base')
