"""Regression cases for literal reading, fair progress, and image-first audit evidence. No live models."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock

from grading import direct_read, page_recovery, quality_audit as qa
from grading.jobs import JobsMixin
from grading.models import plain_transcript
from fake_openai import default_answer, images_of, payload_of, prompt_name
import test_grading_direct_read as direct_fixture
from test_grading_pipeline import CORRECT, Pipeline
import test_grading_teams as teams_fixture
from test_grading_cloud import endpoint
import test_quality_audit as audit_fixture
from test_quality_audit import row


class LiteralChecks(unittest.TestCase):
    def changes(self, first, after):
        return direct_read.literal_changes([{'provider': 'direct', 'transcript': plain_transcript(first)}], plain_transcript(after))

    def test_omissions_signs_decimals_order_choices_and_latex_are_checked(self):
        for first, after in [('x=100', ''), ('x=-10', 'x=10'), ('x=1.25', 'x=125'),
                             ('A=10 B=20', 'A=20 B=10'), ('[marked: (b)]', '(c)'), ('x=1\n2y=3', 'x=12y=3')]:
            with self.subTest(first=first, after=after):
                self.assertTrue(self.changes(first, after))
        candidate = plain_transcript('x=100')
        arranged = copy.deepcopy(candidate)
        arranged['lines'][0]['elements'][0]['latex'] = 'x=10'
        self.assertEqual([c['form'] for c in direct_read.literal_changes([{'provider': 'direct', 'transcript': candidate}], arranged)], ['latex'])

    def test_layout_and_tex_spelling_can_change_without_changing_content(self):
        for first, after in [('x = 6', 'x=6'), ('3x=18\nx=6', '3x = 18 x = 6'),
                             ('[marked: (b)]', '(b)'), (r'x=\frac{1}{2}', 'x=1/2')]:
            with self.subTest(first=first):
                self.assertEqual(self.changes(first, after), [])

    def test_active_work_cannot_be_struck_or_moved_to_another_crop(self):
        candidate = plain_transcript('x=100')
        after = copy.deepcopy(candidate)
        after['lines'][0]['elements'][0]['state'] = 'crossed_out'
        self.assertTrue(direct_read.literal_changes([{'provider': 'direct', 'transcript': candidate}], after))
        after['lines'][0]['elements'][0].update(state='written', crop=1)
        self.assertEqual({c['crop'] for c in direct_read.literal_changes([{'provider': 'direct', 'transcript': candidate}], after)}, {0, 1})


class LiteralPipeline(Pipeline):
    legacy_workflow = False
    opinions = teams_fixture.GradingTeams.opinions
    steps, direct = direct_fixture.DirectReading.steps, direct_fixture.DirectReading.direct

    def setUp(self):
        super().setUp()
        self.direct(True)

    def omission(self, repair):
        def script(name, body):
            if name == 'read':
                return ('raw', '\n'.join(CORRECT + ['x=100']), 'stop')
            if name == 'reconcile_literal':
                p = payload_of(body)
                t = copy.deepcopy(p['candidates'][0]['transcript'])
                if not repair or not p.get('literal_changes'):
                    t['lines'].pop()
                    t['plain_text'] = t['latex'] = '\n'.join(CORRECT)
                return {'transcript': t, 'visual_confidence': .96}
        return script

    def test_dropped_value_is_reasked_once_then_retained_as_a_review_reason(self):
        response, names = self.grade(CORRECT, script=self.omission(False))
        self.assertEqual(names.count('reconcile_literal'), 2)
        check = self.steps(response['id'])['reconciling']['first_reading_check']
        self.assertTrue(check['remaining'])
        self.assertIn(direct_read.literal_doubt(check['remaining']), response['result']['reasons'])
        self.assertIsNone(response['acceptance'])

    def test_reask_can_restore_the_first_reading_without_a_lingering_literal_flag(self):
        response, names = self.grade(CORRECT, script=self.omission(True))
        self.assertEqual(names.count('reconcile_literal'), 2)
        self.assertEqual(self.steps(response['id'])['reconciling']['first_reading_check']['remaining'], [])
        self.assertIn('x=100', response['result']['transcript']['plain_text'])

    def test_a_model_cannot_supply_recovered_image_references(self):
        def script(name, body):
            if name == 'read':
                return ('raw', '\n'.join(CORRECT), 'stop')
            if name == 'reconcile_literal':
                return {'transcript': payload_of(body)['candidates'][0]['transcript'], 'visual_confidence': .96,
                        'context_recovery': {'status': 'recovered', 'crops': [{'images': 'not an authorized reference'}]}}
        response, _ = self.grade(CORRECT, script=script)
        self.assertNotIn('context_recovery', self.steps(response['id'])['reconciling'])
        self.assertEqual(response['crops'], self.steps(response['id'])['extracting']['crops'])


class FairTurns(unittest.TestCase):
    def test_every_productive_step_gets_a_turn(self):
        calls = []
        def step(name):
            def run():
                calls.append(name)
                return True
            return run
        service = SimpleNamespace(_guarded=JobsMixin._guarded, process_scan_one=step('scan'), auto_review_one=step('review'),
            assemble_later_one=step('assemble'), auto_prepare_one=step('prepare'), auto_grade_one=step('grade'), auto_flag_review_one=lambda: False, auto_participation_one=lambda: False, auto_full_credit_one=step('credit'), auto_certify_one=lambda: False,
            dr_copy_one=lambda: False,
            process_version_one=lambda: False,
            process_location_one=lambda: False)
        for _ in range(12):
            service._background_turn = 1
            self.assertTrue(JobsMixin.process_one(service))
        self.assertEqual(calls, ['scan', 'review', 'assemble', 'prepare', 'grade', 'credit'] * 2)

class ContextLimits(unittest.TestCase):
    def test_requests_must_name_existing_distinct_crops(self):
        for rows in ([{'crop': -1, 'reason': 'edge'}], [{'crop': True, 'reason': 'edge'}],
                     [{'crop': 0, 'reason': ''}], [{'crop': 0, 'reason': 'edge'}] * 2):
            with self.assertRaises(ValueError):
                page_recovery.requests({'page_requests': rows}, 1)

    def test_masked_crop_cannot_send_an_unmasked_page(self):
        service = SimpleNamespace(_page_views=Mock(), cloud_call=Mock())
        value = {'transcript': plain_transcript('x=1'), 'page_requests': [{'crop': 0, 'reason': 'edge'}]}
        reading = {'candidates': [{'provider': 'direct', 'transcript': plain_transcript('x=1')}]}
        _, result, _ = page_recovery.recover(service, {'source_json': '{"regions":[{}]}'}, {},
            {'reading_page_rule': page_recovery.RULE, 'direct_reading_rule': direct_read.RULE}, {}, reading,
            {'crops': [{'metadata': {'privacy_masks': [[0, 0, 1, 1]]}}]}, {}, {}, value, lambda x: x)
        self.assertEqual(result['context_recovery']['status'], 'unresolved')
        self.assertTrue(result['transcript']['ambiguities'])
        service._page_views.assert_not_called()
        service.cloud_call.assert_not_called()


class AuditSampling(unittest.TestCase):
    def test_sample_is_stable_and_covers_score_bands_without_human_grades(self):
        rows = [row(str(i), [], [], score) for i, score in enumerate([0, 0, 2, 2, 4, 4])]
        rows += [row('human', [], [], 0, decided='instructor')]
        q = {'question_id': 'q', 'points': 4, 'responses': rows}
        selected = qa.image_sample(q)
        self.assertEqual(len(selected), 3)
        self.assertEqual({r['score'] for r in rows if r['ref'] in selected}, {0, 2, 4})
        self.assertEqual(selected, qa.image_sample({**q, 'responses': list(reversed(rows))}))
        self.assertNotIn('human', selected)

    def test_same_printed_number_does_not_link_unrelated_questions(self):
        questions = [{'question_id': v, 'label': v, 'printed_number': '1', 'exam_version': v, 'points': 4,
                      'responses': [row(f'{v}{i}', [], [], score) for i in range(6)]} for v, score in [('A', 0), ('B', 4)]]
        self.assertNotIn('version_gap', [f['kind'] for f in qa.audit_checks(questions)])

    def test_blind_contract_rejects_missing_duplicate_and_unknown_readings(self):
        good = {'ref': 'R1', 'work': 'x=6', 'lines': ['L1'], 'reason': 'visible', 'uncertain': False}
        self.assertEqual(len(qa.blind_readings({'readings': [good]}, {'R1'}, {'L1'})), 1)
        for readings in ([], [good, good], [{**good, 'lines': ['missing']}], [{**good, 'ref': 'R2'}]):
            with self.assertRaises(ValueError):
                qa.blind_readings({'readings': readings}, {'R1'}, {'L1'})


class ImageAuditPipeline(Pipeline):
    legacy_workflow = False
    opinions = teams_fixture.GradingTeams.opinions
    audit, finish = audit_fixture.AuditRuns.audit, audit_fixture.AuditRuns.finish

    def test_automatic_grade_is_read_from_images_before_its_saved_work_and_grade(self):
        response, _ = self.grade(CORRECT)
        self.assertIsNotNone(response['acceptance'])
        self.configure([endpoint(self.host, 'alpha-vision')], rubric_reviewer='alpha-vision')
        def audit_answer(body, host):
            if prompt_name(body) == 'quality_audit_sample':
                ref = payload_of(body)['responses'][0]['ref']
                return {'summary': 'The image reading differs.', 'findings': [{'kind': 'misapplied', 'severity': 'high',
                    'refs': [ref], 'lines': ['R1'], 'explanation': 'Saved work omitted writing.', 'suggestions': []}]}
            return default_answer(body)  # The ordinary text pass reports no concern.
        self.host.script = audit_answer
        self.audit()
        done = self.finish()
        self.assertEqual(done['status'], 'complete', done)
        self.assertEqual((done['result']['totals']['sample_selected'], done['result']['totals']['sample_read']), (1, 1))
        calls = [r['body'] for r in self.host.requests if prompt_name(r['body']).startswith('quality_audit')]
        self.assertEqual([prompt_name(c) for c in calls], ['quality_audit_blind', 'quality_audit_sample', 'quality_audit', 'quality_audit_confirm'])
        self.assertEqual(set(payload_of(calls[0])), {'question', 'rubric', 'images_per_ref'})
        self.assertTrue(images_of(calls[0]))
        self.assertFalse(images_of(calls[1]))
        self.assertEqual(done['result']['questions'][0]['findings'][0]['source'], 'image_sample')
        self.assertEqual(self.frq.response(response['id'])['acceptance'], response['acceptance'])


class PageRecoveryPipeline(Pipeline):
    legacy_workflow = False
    opinions = teams_fixture.GradingTeams.opinions
    steps, direct = direct_fixture.DirectReading.steps, direct_fixture.DirectReading.direct

    def setUp(self):
        super().setUp()
        self.direct(True)
        current = self.frq.settings()
        self.frq.save_settings({'version': current['version'], 'settings': {**current['settings'], page_recovery.SETTING: True}})
        self.page_unavailable = False

    def queue(self, *args, **kwargs):
        from frq_fixtures import page
        patch.object(self.frq, '_page_views', return_value=[] if self.page_unavailable else [page()]).start()
        return super().queue(*args, **kwargs)

    def context_script(self, name, body):
        if name == 'read':
            return ('raw', '\n'.join(CORRECT), 'stop')
        if name == 'read_context':
            return ('raw', 'x=100', 'stop')
        if name == 'reconcile_context':
            p = payload_of(body)
            return {'transcript': copy.deepcopy(p['candidates'][0]['transcript']), 'visual_confidence': .96,
                    'page_requests': [] if p.get('page_round') else [{'crop': 0, 'reason': 'Continuation cut off at edge'}]}

    def test_one_page_round_retains_original_and_expanded_evidence(self):
        response, names = self.grade(CORRECT, script=self.context_script)
        self.assertEqual((names.count('read_context'), names.count('reconcile_context')), (1, 2))
        steps = self.steps(response['id'])
        recovery = steps['reconciling']['context_recovery']
        self.assertEqual(recovery['status'], 'recovered')
        self.assertNotIn('x=100', steps['recognizing']['candidates'][0]['transcript']['plain_text'])
        self.assertIn('x=100', steps['reconciling']['transcript']['plain_text'])
        self.assertEqual(recovery['crops'][0]['metadata']['original_images'], steps['extracting']['crops'][0]['images'])
        self.assertFalse(steps['reconciling']['first_reading_check']['changes'])
        grades = [payload_of(r['body']) for r in self.host.requests if prompt_name(r['body']) == 'grade']
        self.assertTrue(grades[0]['reading_context']['page_context'])

    def test_unavailable_page_becomes_a_response_review_reason(self):
        self.page_unavailable = True
        response, names = self.grade(CORRECT, script=self.context_script)
        self.assertNotIn('read_context', names)
        recovery = self.steps(response['id'])['reconciling']['context_recovery']
        self.assertEqual(recovery['status'], 'unresolved')
        self.assertIn(recovery['error'], response['result']['reasons'])
        self.assertIsNone(response['acceptance'])
