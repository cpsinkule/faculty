"""A full numeric total is not proof that a particular rubric criterion was met.

Synthetic version of the observed ordered-triple case: correct component equations
and a real parameter earn six points while the full-credit item requires a tuple.
No saved student data or inference calls are used.
"""
import copy
import unittest

from grading import recognition, rubrics
from grading.models import plain_transcript
from grading.rubric_quality import FULL_CREDIT_RULE, builtin_exclusions, full_credit_alone
from grading.schemas import SCHEMAS
from fake_openai import grade_answer


class FullCreditEligibility(unittest.TestCase):
    def setUp(self):
        self.rubric = rubrics.validate({'mode': 'positive', 'items': [
            {'id': iid, 'points': points, 'description': description}
            for iid, points, description in [
                ('R1', 6, 'Correct answer as an ordered triple, with work.'),
                ('R2', 1, 'Recognizes a dependent system.'),
                ('R3', 1, 'Sets z=t.'),
                ('R4', 1.5, 'Correct x in terms of t.'),
                ('R5', 1.5, 'Correct y in terms of t.'),
                ('R6', .5, 'Answer follows the work but is not an ordered triple.'),
                ('R7', .5, 'States that t is any real number.'),
                ('R8', 1, 'Answer follows the work and is an ordered triple.'),
                ('R9', 0, 'No Answer or Completely Incorrect')]], 'ceiling': 6}, 6)
        self.entry = {'builtin': builtin_exclusions(self.rubric, 6), 'pairs': [['R6', 'R8'], ['R7', 'R8']]}
        self.transcript = plain_transcript('x = 2t + 3; y = 4t + 5; z = t; t is any real number')
        self.payload = {'rubric': self.rubric, 'transcript': self.transcript, 'answer_parts': ['solution']}
        self.components = ('R2', 'R3', 'R4', 'R5', 'R6', 'R7')

    def assessment(self, eligible=False, selected=None):
        raw = grade_answer(self.payload, selected or self.components, full_credit_eligible=eligible)
        raw['applications'][0]['reason'] = 'The ordered-triple requirement is not met.'
        return recognition.validate_grade(raw, self.transcript, self.rubric, 6, 1)

    def test_full_component_score_preserves_rejected_full_credit_and_its_reason(self):
        graded = self.assessment()
        before = copy.deepcopy(graded)
        read = full_credit_alone(graded, self.entry, self.rubric, 6)
        # Ticks, score and reasons as the grader gave them; full marks by the steps while it rejected full credit is flagged (2026-09-22).
        self.assertEqual({**read, 'review_reasons': before['review_reasons']}, before)
        self.assertEqual(read['review_reasons'], [*before['review_reasons'], "The grader's selected lines add up to full credit, but it said the full-credit item's requirements were not satisfied."])
        self.assertEqual(read['score']['score'], 6)
        self.assertFalse(read['applications'][0]['applied'])
        self.assertNotIn('normalized', read)

    def test_missing_legacy_eligibility_does_not_authorize_new_promotion(self):
        graded = self.assessment()
        del graded['full_credit_eligible']
        self.assertIs(full_credit_alone(graded, self.entry, self.rubric, 6), graded)

    def test_explicit_eligibility_can_still_normalize_genuinely_equivalent_steps(self):
        self.transcript = plain_transcript('(2t+3, 4t+5, t); z=t with the two substitutions shown')
        self.payload['transcript'] = self.transcript
        graded = self.assessment(True, ('R2', 'R3', 'R4', 'R5', 'R8'))
        graded['applications'][0]['reason'] = 'All requirements met; component items selected instead.'
        read = full_credit_alone(graded, self.entry, self.rubric, 6)
        self.assertEqual(read['score']['score'], graded['score']['score'])
        self.assertEqual(read['score']['selected'], ['R1'])
        self.assertEqual(read['normalized']['rule'], FULL_CREDIT_RULE)
        self.assertEqual(read['normalized']['route'], 'steps')

    def test_frozen_rules_keep_their_original_interpretation(self):
        graded = self.assessment()
        self.assertIs(full_credit_alone(graded, self.entry, self.rubric, 6, 'full-credit-alone-v1'), graded)
        for rule in ('full-credit-alone-v2', 'full-credit-alone-v3'):
            with self.subTest(rule=rule):
                read = full_credit_alone(graded, self.entry, self.rubric, 6, rule)
                self.assertEqual(read['score']['selected'], ['R1'])
                self.assertEqual(read['normalized']['rule'], rule)

    def test_eligibility_is_validated_and_does_not_award_missing_credit(self):
        for value in ('true', 1, None, []):
            raw = grade_answer(self.payload, self.components)
            raw['full_credit_eligible'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'eligibility'):
                recognition.validate_grade(raw, self.transcript, self.rubric, 6, 1)
        partial = self.assessment(True, ('R2',))
        self.assertIs(full_credit_alone(partial, self.entry, self.rubric, 6), partial)
        self.assertEqual(partial['score']['score'], 1)

    def test_all_grading_transports_request_the_explicit_decision(self):
        for name in ('grade', 'transcript_grade', 'second_vision', 'adjudicate', 'third_grade', 'arbitrate'):
            with self.subTest(name=name):
                self.assertEqual(SCHEMAS[name]['properties']['full_credit_eligible'], {'type': 'boolean'})
                self.assertIn('full_credit_eligible', SCHEMAS[name]['required'])

    def test_contradictory_eligibility_does_not_silently_finalize_full_credit(self):
        graded = self.assessment(False, ('R1',))
        read = full_credit_alone(graded, self.entry, self.rubric, 6)
        self.assertEqual(read['applications'], graded['applications'])
        self.assertEqual(read['score'], graded['score'])
        self.assertIn('requirements were not satisfied', ' '.join(read['review_reasons']))
