"""Optional model geometry never repairs or replaces literal grading evidence."""
import copy
import json
import unittest

from grading import recognition, rubrics
from grading.models import box, encoded, plain_transcript, reading_ambiguities, transcript


class OptionalModelGeometry(unittest.TestCase):
    def test_invalid_rectangles_are_discarded_and_audited_without_changing_the_reading(self):
        # Includes the actual shapes of the Gemini failures; no guessed decimal places.
        rectangles = ([.749, .207, 9.07, .317], [.751, .207, 903, .323],
                      [.228, .144, 489, .191], [.229, .147, 4.887, .187],
                      [10, 20, 200, 60], [-.1, 0, .5, .5], [.2, .3, .2, .3],
                      [.8, .2, .3, .5], [0, 0, float('nan'), .5],
                      [0, 0, float('inf'), .5], [0, 0, 10**1000, .5],
                      [False, 0, 1, 1], ['0', 0, 1, 1], [0, 0, 1], 'rectangle', {})
        original = plain_transcript('3x = 18\nx = 8', confidence=.9)
        original['lines'][1]['elements'][0]['state'] = 'crossed_out'
        for rectangle in rectangles:
            with self.subTest(rectangle=rectangle):
                raw = copy.deepcopy(original)
                raw['lines'][0]['elements'][0]['bbox'] = rectangle
                value = transcript(raw)
                expected = copy.deepcopy(original)
                expected['lines'][0]['elements'][0]['bbox_discarded'] = True
                self.assertEqual(value, expected)  # Wrong math and handwriting marks stay verbatim.
                self.assertIs(raw['lines'][0]['elements'][0]['bbox'], rectangle)
                self.assertEqual(reading_ambiguities(value), [])
                self.assertEqual(transcript(json.loads(encoded(value))), value)

    def test_valid_or_absent_geometry_is_unchanged_and_required_geometry_stays_strict(self):
        original = plain_transcript('x = 8')
        for rectangle in (None, [0, 0, 1, 1], [.1, .2, .6, .8]):
            with self.subTest(rectangle=rectangle):
                original['lines'][0]['elements'][0]['bbox'] = rectangle
                self.assertEqual(transcript(original), original)
                self.assertEqual(box(rectangle), rectangle)
        for rectangle in ([.1, .2, 903, .8], [0, 0, 0, 0], 'rectangle'):
            with self.subTest(rectangle=rectangle), self.assertRaises(ValueError):
                box(rectangle)

    def test_bad_geometry_does_not_bypass_other_transcript_checks(self):
        original = plain_transcript('x = 8', confidence=.9)
        original['lines'][0]['elements'][0]['bbox'] = [.1, .2, 903, .8]
        for key, invalid in (('crop', -1), ('crop', '0'), ('confidence', 90),
                             ('state', 'corrected'), ('type', 'answer'), ('text', 8)):
            with self.subTest(key=key, invalid=invalid):
                raw = copy.deepcopy(original)
                raw['lines'][0]['elements'][0][key] = invalid
                with self.assertRaises(ValueError):
                    transcript(raw)
        original['lines'].append(copy.deepcopy(original['lines'][0]))
        with self.assertRaises(ValueError):
            transcript(original)  # Duplicate line identifiers remain invalid.

    def test_grader_geometry_is_optional_but_quotes_crops_and_rubric_decisions_are_checked(self):
        reading = plain_transcript('x = 8')
        rules = rubrics.validate({'mode': 'positive', 'items': [
            {'id': 'R1', 'description': 'Answer with work', 'points': 2}]}, 2)
        raw = {'applications': [{'rubric_item_id': 'R1', 'applied': True, 'reason': '',
            'evidence': [{'line_id': 'line-1', 'student_text': 'x = 8', 'crop': 0,
                          'bbox': [.1, .2, 903, .8]}], 'confidence': .95}],
            'interpretation': {}, 'confidence': .95, 'review_reasons': []}
        def validate(value):
            return recognition.validate_grade(value, reading, rules, 2, 1)
        value = validate(raw)
        evidence = value['applications'][0]['evidence'][0]
        self.assertEqual(evidence, {'line_id': 'line-1', 'student_text': 'x = 8',
                                   'crop': 0, 'bbox': None, 'bbox_discarded': True})
        self.assertEqual(value['review_reasons'], [])
        self.assertEqual(validate(value)['applications'], value['applications'])
        wrong_quote = copy.deepcopy(raw)
        wrong_quote['applications'][0]['evidence'][0]['student_text'] = 'x = 6'
        self.assertIn('R1: evidence does not quote the retained transcript.', validate(wrong_quote)['review_reasons'])
        for key, invalid in (('crop', 1), ('crop', '0')):
            broken = copy.deepcopy(raw)
            broken['applications'][0]['evidence'][0][key] = invalid
            with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                validate(broken)
        broken = copy.deepcopy(raw)
        broken['applications'][0]['rubric_item_id'] = 'invented'
        with self.assertRaises(ValueError):
            validate(broken)


if __name__ == '__main__':
    unittest.main()
