"""Workspace decision-boundary regressions; invented inputs and no network access."""
import unittest

import openai_decisions
from jev import client, send_checks
from decisions_sandbox import decisions_client, snap_client


class DecisionValidation(unittest.TestCase):
    def test_invalid_twelve_hour_clock_is_not_wrapped_to_another_time(self):
        self.assertEqual(send_checks._time_after(' at 12:30 pm', 0), '12:30')
        for clock in ('00 pm', '13 pm', '25 pm', '12:99 am'):
            with self.subTest(clock=clock):
                self.assertEqual(send_checks._time_after(' at ' + clock, 0), '')

    def test_extreme_probabilities_are_failed_answers(self):
        for validator in (client.probability, openai_decisions._probability):
            for value in (10 ** 400, -(10 ** 400), float('inf'), float('nan'), {}, []):
                with self.subTest(validator=validator.__module__, value=type(value).__name__), self.assertRaises(client.DecisionError):
                    validator(value)

    def test_unhashable_choice_is_a_failed_answer(self):
        question = {'pick': {'type': 'choice', 'criteria': {'first': None, 'second': None}}}
        answer = {'model': client.MODEL, 'answers': {'pick': {'type': 'choice', 'choice': [],
                  'probabilities': {'first': 1, 'second': 0}, 'confidence': 1}},
                  'usage': {'input_tokens': 1, 'output_tokens': 1}}
        with self.assertRaises(client.DecisionError):
            client.validate(answer, question)

    def test_sandbox_numeric_errors_stay_explicit_failed_answers(self):
        for value in (10 ** 400, -(10 ** 400), float('inf'), float('nan'), {}, []):
            for validator in (decisions_client._probability, snap_client.HOST._probability):
                with self.subTest(value=type(value).__name__, validator=validator.__qualname__):
                    with self.assertRaises(decisions_client.DecisionError):
                        validator(value, {}, 'sample')
        questions = {'score': {'type': 'score', 'criteria': ['low', 'high']}}
        for value in ({}, [], 'first', 10 ** 400):
            answer = {'answers': [{'name': 'score', 'type': 'score', 'score': 0,
                      'probabilities': [{'value': value, 'probability': 1}, {'value': 1, 'probability': 0}]}]}
            with self.subTest(position=type(value).__name__), self.assertRaises(decisions_client.DecisionError):
                decisions_client.normalize(answer, questions)

    def test_malformed_score_positions_fail_before_ordering(self):
        questions = {'score': {'type': 'score', 'criteria': ['low', 'high']}}
        for value in ({}, [], 'first', 10 ** 400, float('nan')):
            answer = {'model': openai_decisions.MODEL, 'answers': [{'name': 'score', 'type': 'score',
                      'probabilities': [{'value': value, 'probability': 1}, {'value': 1, 'probability': 0}]}]}
            with self.subTest(value=type(value).__name__), self.assertRaises(client.DecisionError):
                openai_decisions.normalize(answer, questions)


if __name__ == '__main__':
    unittest.main()
