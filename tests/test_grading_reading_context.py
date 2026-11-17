"""Scattered work, reading alternatives and independent image-aware second grading.

Synthetic contracts/transport only; these do not establish handwriting accuracy.
"""
import copy
import json
import unittest

from grading import config, recognition, schemas
from grading.models import plain_transcript, reading_ambiguities, transcript
from grading.providers.cloud import HostedEndpoint
from grading.providers.local_vlm import prompt
from rosters import connect
from fake_openai import grade_answer, images_of, payload_of, prompt_name, reconcile_answer
from test_grading_cloud import endpoint
from test_grading_pipeline import CORRECT, INCONSISTENT, Pipeline


def scattered(payload):
    value = copy.deepcopy(reconcile_answer(payload))
    lines = value['transcript']['lines']
    value['transcript']['layout_relations'] = [{
        'from_line': lines[1]['id'], 'to_line': lines[2]['id'], 'kind': 'side_calculation',
        'description': 'The right-margin calculation has an arrow into the next line.', 'uncertain': False}]
    value['transcript']['reading_alternatives'] = []
    return value


class TranscriptContext(unittest.TestCase):
    def test_layout_round_trips_without_repairing_written_math_or_changing_old_readings(self):
        old = plain_transcript('\n'.join(INCONSISTENT))
        self.assertEqual(transcript(old), old)
        reading = scattered({'candidates': [{'transcript': old}]})['transcript']
        self.assertEqual(transcript(reading), reading)
        self.assertEqual(reading['lines'][-1]['elements'][0]['text'], 'x = 8')
        self.assertFalse(reading_ambiguities(reading))
        for key, value in (('kind', 'proven_correct'), ('uncertain', 'false')):
            broken = copy.deepcopy(reading)
            broken['layout_relations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                transcript(broken)

    def test_a_layout_note_about_a_line_that_was_not_transcribed_is_dropped_and_counted_without_raising_a_doubt(self):
        """On real papers one reading in eight was refused and billed again for one such note; raised as a doubt it then flagged one in four."""
        reading = scattered({'candidates': [{'transcript': plain_transcript('\n'.join(CORRECT))}]})['transcript']
        good = copy.deepcopy(reading['layout_relations'][0])
        for end, name in (('from_line', 'line-10'), ('to_line', good['from_line'])):  # A line nobody transcribed; a line linked to itself.
            broken = copy.deepcopy(reading)
            broken['layout_relations'] = [{**good, end: name}, good]
            with self.subTest(end=end):
                value = transcript(broken)
                self.assertEqual((value['lines'], value['layout_relations']), (reading['lines'], [good]))  # Every line that was read stays; so does the sound note.
                self.assertEqual((value['layout_notes_dropped'], reading_ambiguities(value), value['ambiguities']), (1, [], []))  # Counted for the audit, never a doubt.
                self.assertEqual(transcript(value), value)  # Validating the retained reading again adds nothing.
        value = transcript({**reading, 'layout_relations': [{**good, 'from_line': 'x' * 5000}, {**good, 'to_line': None}]})
        self.assertEqual((value['layout_relations'], value['layout_notes_dropped'], value['ambiguities']), ([], 2, []))
        self.assertNotIn('layout_notes_dropped', transcript(reading))  # Nothing dropped, nothing recorded: earlier readings are unchanged.
        with self.assertRaises(ValueError):
            transcript({**reading, 'layout_notes_dropped': 'many'})

    def test_structured_uncertainty_is_not_lost_when_ambiguities_is_empty(self):
        reading = scattered({'candidates': [{'transcript': plain_transcript('\n'.join(CORRECT))}]})['transcript']
        reading['layout_relations'][0]['uncertain'] = True
        reading['reading_alternatives'] = [{'line_id': 'line-4', 'options': ['x = 6', 'x = 8'], 'description': 'The last digit is unclear.'}]
        value = transcript(reading)
        self.assertEqual(len(reading_ambiguities(value)), 2)
        self.assertEqual(value['ambiguities'], [])  # Derived warnings do not rewrite retained model text.
        value['reading_alternatives'][0]['line_id'] = 'missing'
        with self.assertRaises(ValueError):
            transcript(value)

    def test_vision_schema_binds_evidence_to_valid_crops_and_rubric_items(self):
        e = {**config.ENDPOINT, 'name': 'vision', 'model': 'fixture', 'base_url': 'http://localhost:1', 'lineage': 'other'}
        body, _ = HostedEndpoint(e).body({'prompt': 'second_vision', 'payload': {'rubric': {'items': [{'id': 'R1'}]}}, 'images': [b'one', b'two']})
        schema = body['response_format']['json_schema']['schema']
        item = schema['properties']['applications']['items']['properties']
        self.assertEqual(item['rubric_item_id']['enum'], ['R1'])
        # As a range, never an integer enum: Google's structured output drops every field of an object that holds one (2026-09-21).
        self.assertEqual(item['evidence']['items']['properties']['crop'], {'type': 'integer', 'minimum': 0, 'maximum': 1})
        self.assertIn('reading_issues', schema['required'])
        self.assertEqual(len(images_of(body)), 2)
        self.assertIn('no other grader', body['messages'][0]['content'])
        self.assertIn('layout_relations', schemas.TRANSCRIPT['properties'])
        self.assertIn('reading_alternatives', schemas.TRANSCRIPT['properties'])


class ReadingPipeline(Pipeline):
    def vision_second(self):
        current = self.frq.settings()
        settings = current['settings']
        settings['endpoints'].append(endpoint(self.host, 'delta-vision'))
        for tier in config.TIERS:
            settings['tiers'][tier]['second'] = 'delta-vision'
        self.frq.save_settings({'version': current['version'], 'settings': settings})

    def test_text_second_gets_layout_and_ocr_vs_visual_differences_without_images_or_primary_opinions(self):
        response, _ = self.grade(CORRECT, reader=scattered, mathpix=INCONSISTENT)
        request = next(r['body'] for r in self.host.requests if prompt_name(r['body']) == 'transcript_grade')
        payload = payload_of(request)
        self.assertFalse(images_of(request))
        self.assertEqual(payload['transcript']['layout_relations'][0]['kind'], 'side_calculation')
        self.assertEqual({r['provider'] for r in payload['reading_context']['ocr_readings']}, {'glm', 'mathpix'})
        self.assertTrue(payload['reading_context']['visual_vs_ocr'])
        self.assertTrue(payload['reading_context']['ocr_disagreements'])
        self.assertEqual(payload['reading_context']['resolution']['unresolved'], 0)
        self.assertTrue({'proposed_applications', 'proposed_score', 'review_reasons', 'interpretation'}.isdisjoint(payload))
        self.assertEqual(response['result']['signals']['second']['input_mode'], 'transcript_only')

    def test_a_reading_with_a_dangling_layout_note_is_graded_once_and_is_not_flagged_for_it(self):
        def dangling(payload):
            value = scattered(payload)
            value['transcript']['layout_relations'].append({**value['transcript']['layout_relations'][0], 'from_line': 'line-10'})
            return value
        response, names = self.grade(CORRECT, reader=dangling)
        self.assertEqual(names.count('reconcile'), 1)  # Not refused, so not asked (and billed) again.
        result = response['result']
        self.assertEqual((len(result['transcript']['layout_relations']), result['transcript']['layout_notes_dropped'], len(result['transcript']['lines'])), (1, 1, len(CORRECT)))
        self.assertFalse(any('layout note' in reason.lower() for reason in result['reasons']), result['reasons'])
        self.assertNotIn(result['state'], ('ocr_review', 'both'))  # The reading raised no doubt, so nothing about it sends the response to review.
        self.assertNotIn('The handwriting is ambiguous in places.', result['signals']['risk']['escalate'])

    def test_invalid_optional_geometry_keeps_the_reading_and_original_receipt_without_a_retry(self):
        rectangle = [.228, .144, 489, .191]
        def bad_geometry(payload):
            value = scattered(payload)
            value['transcript']['lines'][0]['elements'][0]['bbox'] = rectangle
            return value
        response, names = self.grade(CORRECT, reader=bad_geometry)
        self.assertEqual(names.count('reconcile'), 1)
        result = response['result']
        self.assertEqual(result['transcript']['plain_text'], '\n'.join(CORRECT))
        element = result['transcript']['lines'][0]['elements'][0]
        self.assertEqual((element['bbox'], element['bbox_discarded']), (None, True))
        self.assertEqual((result['state'], result['reasons'], result['score']['score']), ('proposed', [], 10))
        with connect(self.path) as db:
            step = json.loads(db.execute("SELECT output_json FROM frq_steps WHERE stage='reconciling'").fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rejected_outputs').fetchone()[0], 0)
        raw = json.loads(step['provider_response']['choices'][0]['message']['content'])
        self.assertEqual(raw['transcript']['lines'][0]['elements'][0]['bbox'], rectangle)

    def test_vision_second_sees_crops_and_layout_but_never_the_primarys_decisions(self):
        self.vision_second()
        response, names = self.grade(CORRECT, reader=scattered)
        self.assertIn('second_vision', names)
        self.assertNotIn('transcript_grade', names)
        primary = next(r['body'] for r in self.host.requests if prompt_name(r['body']) == 'grade')
        second = next(r['body'] for r in self.host.requests if prompt_name(r['body']) == 'second_vision')
        self.assertEqual(images_of(primary), images_of(second))
        self.assertTrue(images_of(second))
        self.assertEqual(payload_of(primary), payload_of(second))
        self.assertTrue({'proposed_applications', 'proposed_score', 'review_reasons', 'interpretation'}.isdisjoint(payload_of(second)))
        signals = response['result']['signals']
        self.assertEqual(signals['second']['input_mode'], 'images_and_transcript')
        self.assertEqual(signals['risk']['components']['second']['input_mode'], 'images_and_transcript')
        self.assertEqual(signals['scope']['roles']['second']['modality'], 'vision')
        self.assertIn('second_vision', signals['scope']['prompts'])
        self.assertEqual(response['result']['transcript']['layout_relations'][0]['kind'], 'side_calculation')

    def test_same_score_does_not_hide_a_second_graders_reading_problem(self):
        self.vision_second()
        def issue(name, body):
            if name != 'second_vision': return None
            value = grade_answer(payload_of(body), {'R1'})
            value['reading_issues'] = [{'line_ids': [payload_of(body)['transcript']['lines'][1]['id']], 'kind': 'layout',
                                       'description': 'The margin work may be a separate attempt; the arrow is unclear.'}]
            return value
        response, _ = self.grade(CORRECT, script=issue)
        signals = response['result']['signals']
        self.assertTrue(signals['risk']['components']['second']['agrees'])
        self.assertTrue(signals['risk']['individual'])
        self.assertEqual(signals['risk']['level'], 'elevated')
        self.assertTrue(any('margin work' in reason for reason in response['result']['reasons']))
        self.assertNotEqual(response['result']['state'], 'proposed')
        self.assertIsNone(response['acceptance'])
        self.assertIsNone(response['review'])
        self.assertEqual(self.proposal(response), ['R1'])

    def test_uncertain_layout_and_alternatives_cannot_become_automatic_acceptance(self):
        def reader(payload):
            value = scattered(payload)
            value['transcript']['layout_relations'][0]['uncertain'] = True
            value['transcript']['reading_alternatives'] = [{'line_id': value['transcript']['lines'][-1]['id'],
                'options': ['x = 6', 'x = 8'], 'description': 'The last digit could be either.'}]
            return value
        response, _ = self.grade(CORRECT, reader=reader)
        self.assertTrue(any('Uncertain layout' in reason for reason in response['result']['reasons']))
        self.assertTrue(any('Uncertain reading' in reason for reason in response['result']['reasons']))
        self.assertIsNone(response['acceptance'])


class QuotationsAreTheStudentsCharacters(unittest.TestCase):
    """Measured 2026-09-21: 72 quotations of one batch failed the check and 23 of them were the student's exact characters."""
    def test_rows_of_a_block_the_readers_layout_and_an_operators_spelling_are_not_the_students_characters(self):
        from grading.models import quotes
        aligned = "\n\\[\n\\begin{aligned}\n\\frac{2790-1780}{46,206-32,571} & = \\frac{1011}{13635} = M \\\\\ny - 1780 = 0.07(x - 32,571)\n\\end{aligned}\n\\]"
        columns = "20x + 27,y = 95     30x/30 = -60/30\n20(-2) + 27y = 95     x = -2\n-40 + 27y = 95"
        for quotation, line in (("\\frac{2790-1780}{46,206-32,571} = \\frac{1011}{13635} = M", aligned),  # The reader's alignment mark is not the student's.
                                ("30x/30 = -60/30\nx = -2", columns), ("x = -2 \\n -40 + 27y = 95", columns),  # One column, or some rows, of one retained line.
                                ("2 × 2", "\\( 2 \\times 2 \\)"), ("a. (8G - 2H)J", "a. \\( (8G - 2H)J \\)"), ("x \\ne 3", "x \\neq 3")):
            with self.subTest(quotation=quotation):
                self.assertTrue(quotes(quotation, line))
        # Nothing else is forgiven: a composed pair, a corrected value, a description, a letter for an operator, or nothing at all.
        for quotation, line in (("46,206, 2790", aligned), ("x = 2", columns), ("with a ≠ symbol over it", "2 \\times 5 \\underbrace{\\cdot}_{\\neq}"),
                                ("2 x 2", "\\( 2 \\times 2 \\)"), ("", columns), ("$ $", columns), ("x = -2\ny = 5", columns)):
            with self.subTest(quotation=quotation):
                self.assertFalse(quotes(quotation, line))

    def test_a_fraction_bar_and_the_readers_text_wrappers_are_one_spelling(self):
        """Next batch: 23 refused pieces of evidence were the reader's LaTeX written on one line by a grader."""
        from grading.models import quotes
        block = "\\[\n\\begin{array}{l}\nx - 8z = 12 \\rightarrow \\frac{-8z}{-8} = \\frac{-x + 12}{-8} \\\\\ny + 5z = 15\n\\end{array}\n\\]"
        said = "\\[\\begin{array}{l}\\text { Not possible, need same dimensions to } \\\\ \\text { subtract matrices. } 3 \\times 4-3 \\times 2 \\neq\\end{array}\\]"
        for quotation, line in (("x - 8z = 12 → -8z/-8 = (-x + 12)/-8", block), ("Not possible, need same dimensions to subtract matrices. 3×4−3×2≠", said),
                                ("(27/2)x", "\\frac{27}{2}x"), ("27/(2x)", "\\frac{27}{2x}"), ("\\dfrac{168}{11}", "168/11"), ("⇒ y = -5", "\\Rightarrow y=-5")):
            with self.subTest(quotation=quotation):
                self.assertTrue(quotes(quotation, line))
        # A product in a denominator keeps its parentheses: the one may not be quoted for the other.
        for quotation, line in (("27/(2x)", "\\frac{27}{2}x"), ("(27/2)x", "\\frac{27}{2x}"), ("-8z/8", block), ("Not possible, the dimensions differ", said)):
            with self.subTest(quotation=quotation):
                self.assertFalse(quotes(quotation, line))

    def test_a_sentence_written_over_several_lines_is_quoted_as_the_sentence_it_is(self):
        """Next batch: 36 of 38 refused final answers were "Not possible because ..." written over two or three handwritten lines."""
        from grading.models import quoted
        lines = {"l13": "Not possible as the \\( GH^T \\) matrix multiplication\x1fNot possible as the \\( GH^T \\) matrix multiplication",
                 "l14": "results in a \\( 3 \\times 4 \\) matrix which is not the same", "l15": "\\( 3 \\times 4 - 3 \\times 2 \\)",
                 "l16": "dimensions as \\( I \\), and therefore cannot be subtracted.", "l17": "Dimensions:", "l18": "3 × 2"}
        sentence = "Not possible as the \\( GH^T \\) matrix multiplication results in a \\( 3 \\times 4 \\) matrix which is not the same dimensions as \\( I \\), and therefore cannot be subtracted."
        for quotation, line in ((sentence, "l13"), ("Not possible as the GH^T matrix multiplication results in a 3 × 4", "l13"),  # As far as the quotation goes.
                                ("matrix multiplication results in a 3 × 4 matrix", "l13"), ("3 × 2", "l18")):
            with self.subTest(quotation=quotation):
                self.assertTrue(quoted(quotation, lines, line))
        # It goes on character for character; side work is passed over, never quoted into the sentence.
        for quotation, line in (("Not possible as the GH^T matrix multiplication gives a 3 × 4", "l13"), ("The dimensions are 3×2.", "l17"),
                                ("Not possible as the GH^T matrix multiplication 3 × 4 - 3 × 2", "l12"), ("3 × 2", "missing"), ("", "l13")):
            with self.subTest(quotation=quotation):
                self.assertFalse(quoted(quotation, lines, line))

    def test_a_grader_may_name_a_neighbouring_line_and_the_quotation_is_kept_where_it_was_found(self):
        """requirement, 2026-09-21: 17 of 82 refused quotations were the student's exact characters one to three lines from the line named."""
        from grading.models import located
        lines = {"l1": "When y decreases by 21 units, x", "l2": "decreases", "l3": "by", "l4": "23 1/8\x1f23\\frac{1}{8}", "l5": "units", "l6": ".",
                 "l7": "Dimensions:", "l8": "Matrix operations not possible because", "l9": "they don't have the same dimensions.",
                 "l10": "CD = E\n35x + 32y = 50", "l11": "x = -370/21, y = 125/6", "l12": "Function:",
                 "l13": "\\begin{array}{ll}\nC(x)=34x+19000 & P(x)=R(x)-C(x) \\\\\nP(x)=42x-190,000 & \n\\end{array}", "l14": "Version B -- Page 7 of 12"}
        for quotation, named, found in (("decreases by 23 1/8 units", "l4", "l2"),                       # the run-on read backward: it TAKES the named line
                                        ("Matrix operations not possible because they don't have the same dimensions.", "l9", "l8"),  # ... its last line
                                        ("P(x)=42x-190,000", "l12", "l13"), ("Function: \\( P(x)=42x-190,000 \\)", "l12", "l13"),  # after a printed label
                                        ("x = \\frac{-370}{21}", "l10", "l11"), ("35x + 32y = 50", "l11", "l10"),              # the line beside the one named
                                        ("23 1/8", "l4", "l4")):
            with self.subTest(quotation=quotation):
                self.assertEqual(located(quotation, lines, named), found)
        # No further: two lines away without taking the named line, the line BEFORE a label, writing the reader never retained.
        for quotation, named in (("decreases", "l4"), ("Version B -- Page 7 of 12", "l12"), ("they don't have the same dimensions.", "l7"),
                                 ("Dimensions: NOT POSSIBLE (the insides don't match)", "l7"), ("x = -370/21", "l13")):
            with self.subTest(quotation=quotation):
                self.assertIsNone(located(quotation, lines, named))

    def test_the_reader_is_told_to_keep_the_labels_around_a_matrix_and_they_can_be_quoted(self):
        """2026-09-21: 16 flagged responses had writing the reader left out, mostly the row and column labels of a matrix that the
        rubric pays two points for; once it described them instead ("with columns labeled: gold, silver")."""
        from grading.models import quotes
        content, named = prompt("reconcile")
        self.assertGreaterEqual(int(named["prompt"].removeprefix("reconcile-v").removesuffix(".txt")), 7)  # v7 and every later version keeps the labels
        for said in ("LABELS AROUND A MATRIX OR TABLE ARE STUDENT WRITING", "keep the student's brackets", "never describe a label", "Handwriting added to PRINTED material is student writing too"):
            self.assertIn(said, content)
        # The arrangement it asks for keeps every label and the brackets quotable, as text and as LaTeX.
        latex = "M=\\begin{array}{cc} & \\begin{array}{cc} P & S \\end{array} \\\\ \\begin{array}{c} G \\\\ B \\end{array} & \\left[\\begin{array}{cc} 3 & 24 \\\\ 45 & 9 \\end{array}\\right] \\end{array}"
        self.assertIn(latex, content)
        for quotation in ("P S", "[3 24; 45 9]", "M =", "G"):
            self.assertTrue(quotes(quotation, latex) and quotes(quotation, "M =\nP S\nG [3 24\nB 45 9]"), quotation)
        self.assertFalse(quotes("S P", latex) or quotes("[3 45; 24 9]", latex))

    def test_rows_joined_the_graders_way_are_still_the_students_rows(self):
        """Same batch: 15 refused quotations were the student's rows joined with `;`, an elision, a typed arrow or a spelled line break."""
        from grading.models import quotes
        work = "R(x)=64x\nP(x)=R(x)-C(x)\nP(x)=64x-[28x+14000]\nP(x)=36x-14000"
        block = "\\[\n\\begin{array}{l}\nx-8 z=12 \\rightarrow x=8 t+12 \\\\\ny+5 z=15 \\rightarrow y=-5 t+15\n\\end{array}\n\\]"
        slope = "\\begin{array}{l}\n1780 \\quad 32,571 \\\\\n\\frac{46,206 - 32,571}{2790 - 1780} = \\frac{13,635}{1,010} \\\\\nM=\\frac{27}{2}\n\\end{array}"
        for quotation, line in (("R(x)=64x; P(x)=R(x)-C(x); P(x)=36x-14000", work), ("R(x)=64x ... P(x)=36x-14000", work), ("[3 24; 45 9]", "G\nB\n[3   24\n45  9]"),
                                ("x-8z=12 -> x=8t+12\ny+5z=15 -> y=-5t+15", block), ("\\frac{13,635}{1,010}\\nM=\\frac{27}{2}", slope),
                                ("1780 32,571; (46,206 - 32,571)/(2790 - 1780) = 13,635/1,010", slope), ("a \\; b; c", "a b\nc")):
            with self.subTest(quotation=quotation):
                self.assertTrue(quotes(quotation, line))
        # Every row must still be the student's: a changed row, a comma-composed pair and a letter for an operator fail as before.
        for quotation, line in (("R(x)=64x; P(x)=36x+14000", work), ("x = -2, y = 5", "x = -2\ny = 5"), ("46,206, 2790", slope), ("x \\neq 3; y = 2", "x = 3\ny = 2"),
                                ("2 x 2", "2 \\times 2"), (";", work), ("...", work)):
            with self.subTest(quotation=quotation):
                self.assertFalse(quotes(quotation, line))


if __name__ == '__main__':
    unittest.main()
