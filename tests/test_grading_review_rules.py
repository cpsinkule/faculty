"""Resolved readings and scoped rubric conditions must not hide genuinely uncertain work."""
import copy
import unittest
from unittest.mock import patch

from grading import config, consensus, recognition, risk, rubrics
from grading.models import plain_transcript
from grading.review_rules import reading_resolution, condition_checks, condition_reasons, current_condition_reasons, LEGACY_CONDITION
from grading.scoring import score
from test_grading_risk import evidence, graded, ROLES
from test_frq_grading import FrqWorkflowTests


class ReviewRulesTests(unittest.TestCase):
    def readings(self, final="x = 8"):
        candidates = [{"provider": name, "transcript": plain_transcript(value, confidence=.97)}
                      for name, value in (("mathpix", "x = 8"), ("glm", "x = 6"))]
        return {"candidates": candidates, "consensus": consensus.align(candidates)}, {"transcript": plain_transcript(final), "visual_confidence": .97}, {"crops": [{"metadata": {"registered": True}}]}

    def test_resolved_difference_is_history_without_review_or_extra_ai_checking(self):
        reading, verified, crops = self.readings()
        before = copy.deepcopy(reading)
        resolution = reading_resolution(reading, verified, crops, config.DEFAULTS)
        self.assertEqual((resolution['total'], resolution['resolved'], resolution['unresolved']), (1, 1, 0))
        self.assertEqual(resolution['details'][0]['matching_providers'], ['mathpix'])
        proposal = {**graded(), 'rubric_ambiguities': []}
        state, signals, reasons = recognition.review_state(reading, verified, proposal, crops, config.DEFAULTS)
        self.assertEqual((state, reasons), ('proposed', []))
        self.assertEqual(signals['reading_resolution'], resolution)
        e = evidence(reading=reading, verified=verified, crops=crops, graded=proposal)
        c = risk.components(**e, roles=ROLES, settings=config.DEFAULTS)
        verdict = risk.assess(c, risk.DEFAULTS)
        self.assertFalse(any('readers disagree' in r for r in verdict['escalate']))
        self.assertIn('The independent readings do not agree closely enough.', verdict['blocks'])
        self.assertEqual(reading, before)  # Resolving a difference is not manufactured OCR agreement.

    def test_confidence_alone_and_incomplete_or_ambiguous_readings_cannot_resolve(self):
        for kind in ('novel', 'ambiguous', 'unclear', 'low', 'alignment', 'inconsistent', 'empty'):
            reading, verified, crops = self.readings('x = 7' if kind == 'novel' else 'x = 8')
            if kind == 'ambiguous': verified['transcript']['ambiguities'] = ['6 or 8']
            if kind == 'unclear': verified['transcript']['lines'][0]['elements'][0]['state'] = 'unclear'
            if kind == 'low': verified['visual_confidence'] = .8
            if kind == 'alignment': crops['crops'][0]['metadata']['registered'] = False
            if kind == 'inconsistent': verified['transcript']['lines'][0]['elements'][0]['text'] = 'x = 7'; verified['transcript']['lines'][0]['elements'][0]['latex'] = 'x = 7'
            if kind == 'empty': verified['transcript'] = plain_transcript('')
            with self.subTest(kind=kind):
                self.assertEqual(reading_resolution(reading, verified, crops, config.DEFAULTS)['unresolved'], 1)

    def test_mixed_readings_are_not_declared_resolved_by_a_digit_somewhere_else(self):
        reading, verified, crops = self.readings('x = 8\ny = 6')
        self.assertEqual(reading_resolution(reading, verified, crops, config.DEFAULTS)['unresolved'], 1)
        verified['human_corrected'] = True
        self.assertEqual(reading_resolution(reading, verified, crops, config.DEFAULTS)['resolved'], 1)
        self.assertEqual(reading_resolution(*self.readings(), {**config.DEFAULTS, 'visual_threshold': .99})['resolved'], 0)

    def rules(self, condition='Apply only if R1 is selected.', handling=None):
        item = {'id': 'R2', 'description': 'Deduction', 'points': -1, 'exceptions': condition}
        if handling:
            item.update(requires=['R1'], condition_handling=handling)
        return rubrics.validate({'items': [{'id': 'R1', 'description': 'Setup deduction', 'points': -1}, item]}, 10)

    def test_unselected_restriction_is_inactive_but_selected_condition_requires_judgment(self):
        rules = self.rules()
        decisions = [{'rubric_item_id': 'R1', 'applied': True}, {'rubric_item_id': 'R2', 'applied': False}]
        self.assertEqual(condition_checks(rules, decisions)[0]['state'], 'inactive')
        self.assertEqual(condition_reasons(rules, decisions), [])
        decisions[1]['applied'] = True
        self.assertIn('Rubric condition R2:', condition_reasons(rules, decisions)[0])
        # Unknown exceptions can affect unselected credit; never assume they are irrelevant.
        decisions[1]['applied'] = False
        for condition in ('Award unless R1 applies.', 'No more than 2 points total.', 'Consult instructor for exceptional methods.'):
            self.assertTrue(condition_reasons(self.rules(condition), decisions))
        self.assertTrue(condition_reasons(rules, []))  # A missing model decision is not "unselected".
        original = 'Rubric condition R2: the image appears to contradict its application.'
        self.assertEqual(current_condition_reasons([LEGACY_CONDITION, original], rules, decisions), [original])

    def test_instructor_structured_mapping_is_enforced_and_compiler_cannot_invent_it(self):
        rules = self.rules(handling='structured')
        valid = [{'rubric_item_id': 'R1', 'applied': True}, {'rubric_item_id': 'R2', 'applied': True}]
        self.assertEqual(condition_reasons(rules, valid), [])
        self.assertEqual(score(rules, valid, 10)['score'], 8)
        with self.assertRaisesRegex(ValueError, 'requires'):
            score(rules, [{'rubric_item_id': 'R2', 'applied': True}], 10)
        invalid = copy.deepcopy(rules)
        invalid['items'][1]['requires'] = []
        with self.assertRaisesRegex(ValueError, 'define its dependencies'):
            rubrics.validate(invalid, 10)
        source = self.rules()
        compiled = rubrics.compiled(rules, source, 10)
        self.assertNotIn('condition_handling', compiled['items'][1])
        self.assertEqual(compiled['items'][1]['exceptions'], source['items'][1]['exceptions'])
        self.assertTrue(condition_reasons(compiled, valid))


class ReviewMeasurementTests(FrqWorkflowTests):
    def test_recalculate_refreshes_resolved_reading_flags_without_inference(self):
        settings = self.frq.settings()
        self.frq.save_settings({'version': settings['version'], 'settings': {**settings['settings'], 'mathpix_enabled': True}})
        other = copy.copy(self.fake)
        other.recognize = lambda image: {'provider': 'mathpix', 'transcript': plain_transcript('3x - 6 = 10', confidence=.97), 'raw': {}, 'metadata': {'model': 'synthetic-reader'}}
        self.frq.provider = lambda kind, settings: other if kind == 'mathpix' else self.fake
        old_resolution = {'total': 1, 'resolved': 0, 'unresolved': 1, 'details': []}
        with patch('grading.recognition.reading_resolution', return_value=old_resolution):
            before = self.finish(self.queue(mode='hybrid')[0])
        self.assertEqual(before['result']['state'], 'ocr_review')
        with patch.object(self.frq, 'provider', side_effect=AssertionError('No inference during flag refresh')):
            after = self.finish(self.queue('rescore', mode='hybrid')[0])
        self.assertEqual(after['result']['state'], 'proposed')
        self.assertEqual(after['result']['signals']['reading_resolution']['resolved'], 1)
        self.assertEqual(after['result']['applications'], before['result']['applications'])
        self.assertEqual(after['result']['score'], before['result']['score'])
        self.assertEqual(after['recognition'], before['recognition'])

    def test_review_metrics_count_current_decisions_once_and_include_reasoning_corrections(self):
        value = self.finish(self.queue()[0])
        approved = self.approve(value)
        first = self.frq.metrics(self.plan['id'])['review_signals']
        self.assertEqual((first['reviewed'], first['groups'][0]['changed']), (1, 0))
        self.frq.save_review(value['id'], {'version': approved['version'], 'result_id': value['current_result_id'],
            'selected_items': ['R1'], 'status': 'approved', 'confirm_review': True, 'reasoning_wrong': True})
        report = self.frq.metrics(self.plan['id'])['review_signals']
        self.assertEqual((report['reviewed'], report['groups'][0]['changed']), (1, 1))
        self.assertEqual(report['groups'][0]['confidence']['0.95–1.00']['changed'], 1)
        self.assertIn('not', report['note'])

    def test_review_policy_change_is_part_of_acceptance_scope(self):
        value = self.finish(self.queue()[0])
        scope = value['result']['signals']['scope']
        self.assertEqual(scope['review_policy']['risk'], risk.POLICY)
        self.assertEqual(scope['review_policy']['review'], 'review-rules-v2')


for _name in [n for n in vars(FrqWorkflowTests) if n.startswith("test_") and n not in vars(ReviewMeasurementTests)]:
    setattr(ReviewMeasurementTests, _name, None)  # FrqWorkflowTests' own tests run in test_frq_grading
del _name


del FrqWorkflowTests  # its own tests run in test_frq_grading
