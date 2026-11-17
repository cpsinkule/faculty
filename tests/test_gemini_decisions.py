"""GEMINI DECISIONS (2026-11-12): Jev's questions translated for Google's Decisions API, answers back in Jev's shape and bounds,
one bounded request; and JEV "STUDENT MATCH PROVIDER": whose paper a first page is asks it. Synthetic only: no provider is
reached."""
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import gemini_decisions as gemini
from app import create_app
from document_identity import jev_papers
from jev import client
from jev.service import STUDENT_MATCH_PURPOSE, both, noul, provider_for, score
from rosters import connect
from test_jev_papers import CHECK, STUDENTS, FakeJev, picture

QUESTIONS = {'yes': noul('Is it true?', {'true': 'It is.', 'false': 'It is not.'}),
             **both('pick', 'Choose one.', {'a': 'Option A', 'b': None, 'c': 'Option C'}),
             'level': score('How soon?', ['Routine: no hurry.', 'Soon: this week.', 'Now'])}
KEY = {'GEMINI_API_KEY': 'synthetic-google-key'}


def api(questions=QUESTIONS, picks=None):
    """An answer as Google sends it (unrounded, its own confidence the top probability), from scripted picks."""
    picks, answers = picks or {}, {}
    for position, (name, asked) in enumerate(questions.items()):
        if asked['type'] == 'noul':
            answers[f'q{position}'] = {'type': 'noul', 'noul': picks.get(name, .97), 'confidence': .97}
        elif asked['type'] == 'choice':
            chosen = picks.get(name, next(iter(asked['criteria'])))
            rest = .1 / (len(asked['criteria']) - 1)
            answers[f'q{position}'] = {'type': 'choice', 'choice': chosen, 'confidence': .9,
                                       'probabilities': {option: .9 if option == chosen else rest for option in asked['criteria']}}
        else:
            answers[f'q{position}'] = {'type': 'score', 'score': 1.0, 'confidence': .8, 'legend': {'0': 'a', '1': 'b', '2': 'c'},
                                       'probabilities': {'0': .1, '1': .8, '2': .1}}
    return {'answers': answers, 'usage': {'total_input_tokens': 321, 'total_output_tokens': 0, 'total_tokens': 321,
                                          'input_tokens_by_modality': [{'modality': 'text', 'tokens': 321}]}}


def reply(payload, status=200):
    response = Mock(status=status)
    response.read = Mock(return_value=json.dumps(payload).encode())
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


class TranslationTests(unittest.TestCase):
    def test_jev_questions_become_googles_under_plain_keys(self):
        body = gemini.request_body({'b': 1, 'a': 'é'}, QUESTIONS)
        self.assertEqual((body['model'], body['input'], list(body['questions'])), ('gemini-decision-1', '{"a":"é","b":1}', ['q0', 'q1', 'q2', 'q3']))
        self.assertEqual(body['questions']['q0'], {'type': 'noul', 'instructions': 'Is it true?',
                                                   'criteria': {'true_description': 'It is.', 'false_description': 'It is not.'}})
        self.assertEqual(body['questions']['q1']['criteria'], {'a': 'Option A', 'b': '', 'c': 'Option C'})  # no description: ""
        self.assertEqual(list(body['questions']['q2']['criteria']), ['c', 'b', 'a'])
        self.assertEqual(body['questions']['q3']['criteria'], ['Routine: no hurry.', 'Soon: this week.', 'Now'])
        self.assertNotIn('criteria', gemini.request_body('s', {'plain': noul('Blank?')})['questions']['q0'])
        pictured = gemini.request_body('Look.', {'yes': noul('Blank?')}, images=(b'\x89PNG',))
        self.assertEqual(pictured['input'], [{'type': 'text', 'text': 'Look.'}, {'type': 'image', 'data': 'iVBORw==', 'mime_type': 'image/png'}])
        for bad in ((b'',), (b'x' * (client.MAX_IMAGE_BYTES + 1),), (b'png',) * (client.MAX_IMAGES + 1)):
            with self.subTest(pictures=len(bad)), self.assertRaises(gemini.DecisionError):
                gemini.request_body('Look.', {'yes': noul('Blank?')}, images=bad)
        with self.assertRaises(gemini.DecisionError):  # only Jev answers a Box
            gemini.request_body('Look.', {'name': {'type': 'box', 'instructions': 'Find the name.'}})

    def test_answers_come_back_in_jevs_shape_with_jevs_confidence(self):
        value = gemini.normalize(api(picks={'pick': 'b', 'pick__reversed': 'b'}), QUESTIONS)
        self.assertEqual((value['model'], value['usage']), ('gemini-decision-1', {'input_tokens': 321, 'output_tokens': 0}))
        self.assertEqual(value['answers']['yes'], {'type': 'noul', 'noul': .97})
        pick = value['answers']['pick']
        self.assertEqual((pick['choice'], set(pick['probabilities'])), ('b', {'a', 'b', 'c'}))
        self.assertAlmostEqual(pick['confidence'], (.9 - 1 / 3) / (1 - 1 / 3))  # Jev's formula, not the API's .9
        self.assertEqual(value['answers']['level']['score'], 1.0)

    def test_an_incomplete_or_malformed_answer_is_no_answer(self):
        good = api()
        missing = {**good, 'answers': {k: v for k, v in good['answers'].items() if k != 'q0'}}
        retyped = {**good, 'answers': {**good['answers'], 'q0': {'type': 'choice', 'choice': 'a', 'probabilities': {'a': 1.0}}}}
        unknown = {**good, 'answers': {**good['answers'], 'q1': {'type': 'choice', 'choice': 'zebra', 'probabilities': {'a': .5, 'b': .3, 'c': .2}}}}
        unlisted = {**good, 'answers': {**good['answers'], 'q1': {'type': 'choice', 'choice': 'a', 'probabilities': {'a': 1.0}}}}
        no_usage = {**good, 'usage': {}}
        for bad in (missing, retyped, unknown, unlisted, no_usage, {'answers': []}, None):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(gemini.DecisionError):
                gemini.normalize(bad, QUESTIONS)


class TransportTests(unittest.TestCase):
    def setUp(self):
        patch.dict('os.environ', KEY).start()
        self.addCleanup(patch.stopall)

    def test_one_post_to_the_fixed_url_with_the_key_in_googles_header(self):
        with patch('gemini_decisions.open_request', return_value=reply(api())) as post:
            value = gemini.evaluate({'x': 1}, QUESTIONS)
        request = post.call_args.args[0]
        self.assertEqual((post.call_count, request.full_url, request.get_method()),
                         (1, 'https://generativelanguage.googleapis.com/v1beta/decisions', 'POST'))
        self.assertEqual((request.get_header('X-goog-api-key'), request.get_header('Authorization')), ('synthetic-google-key', None))
        self.assertEqual(set(value['answers']), set(QUESTIONS))

    def test_failures_are_refused_or_uncertain_and_never_carry_host_text_or_the_key(self):
        refused = HTTPError(gemini.URL, 429, 'Too Many', {}, None)
        refused.read = Mock(return_value=b'{"error": {"message": "synthetic-google-key echoed"}}')
        with patch('gemini_decisions.open_request', side_effect=refused), self.assertRaises(gemini.DecisionError) as error:
            gemini.evaluate({'x': 1}, QUESTIONS)
        self.assertEqual((error.exception.refused, error.exception.host, error.exception.uncertain), (True, True, False))
        self.assertNotIn('synthetic-google-key', str(error.exception))
        input_failure = HTTPError(gemini.URL, 400, 'Bad', {}, None)
        with patch('gemini_decisions.open_request', side_effect=input_failure), self.assertRaises(gemini.DecisionError) as error:
            gemini.evaluate({'x': 1}, QUESTIONS)
        self.assertEqual((error.exception.refused, error.exception.uncertain), (False, False))
        with patch('gemini_decisions.open_request', side_effect=URLError(TimeoutError('timed out'))), self.assertRaises(gemini.DecisionError) as error:
            gemini.evaluate({'x': 1}, QUESTIONS)
        self.assertTrue(error.exception.uncertain)
        with patch('gemini_decisions.open_request', return_value=reply({'answers': {}})), self.assertRaises(gemini.DecisionError):
            gemini.evaluate({'x': 1}, QUESTIONS)

    def test_no_key_or_switched_off_sends_nothing(self):
        for environment in ({'GEMINI_API_KEY': ''}, {'FACULTY_GEMINI_DECISIONS_ENABLED': '0'}):
            with self.subTest(environment=environment), patch.dict('os.environ', environment), patch('gemini_decisions.open_request') as post:
                self.assertFalse(gemini.available())
                with self.assertRaises(gemini.DecisionError):
                    gemini.evaluate({'x': 1}, QUESTIONS)
                post.assert_not_called()


class StudentMatchProvider(unittest.TestCase):
    """JEV "STUDENT MATCH PROVIDER": `paper-student` asks Gemini Decisions when it is set up; everything else keeps its provider."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.jev = self.app.extensions['jev']

    def test_the_purpose_is_the_student_match_s_and_the_provider_follows_the_key_and_the_switch(self):
        self.assertEqual(STUDENT_MATCH_PURPOSE, jev_papers.STUDENT_PURPOSE)
        with patch.dict('os.environ', {'GEMINI_API_KEY': ''}):
            self.assertIs(provider_for(STUDENT_MATCH_PURPOSE), client)  # no key: Jev as before
            self.assertEqual(jev_papers.reader(), 'Jev')
        with patch.dict('os.environ', KEY):
            self.assertIs(provider_for(STUDENT_MATCH_PURPOSE), gemini)
            self.assertEqual(jev_papers.reader(), 'Gemini')
            for other in (jev_papers.PAGE_PURPOSE, jev_papers.ASSIGNMENT_PURPOSE, 'student-triage', 'paper-version'):
                self.assertIs(provider_for(other), client)
            with patch.dict('os.environ', {'FACULTY_STUDENT_MATCH_PROVIDER': 'jev'}):
                self.assertIs(provider_for(STUDENT_MATCH_PURPOSE), client)

    def test_identify_asks_gemini_keeps_its_model_on_the_receipts_and_says_who_read_the_name(self):
        seen = []

        def evaluate(state, questions, images=()):
            seen.append((sorted(questions), len(images)))
            return {'model': gemini.MODEL, 'usage': {'input_tokens': 1, 'output_tokens': 0},
                    'answers': {name: {'type': 'choice', 'choice': 'Dawson, Calvin Allan', 'confidence': .95, 'probabilities': {}} for name in questions}}

        students = {key: STUDENTS[key] for key in ('1000000001', '1000000003')}
        jev = FakeJev({})
        jev.ask = self.jev.ask
        check = {**CHECK, 'uin_box': CHECK['name_box']}
        with patch.dict('os.environ', KEY), patch('gemini_decisions.evaluate', side_effect=evaluate), \
                patch('jev.client.available', return_value=True), patch('jev.client.evaluate') as asked_jev:
            found = jev_papers.identify(jev, 'page-1', picture(), check, students)
            again = jev_papers.identify(jev, 'page-1', picture(), check, students)
        asked_jev.assert_not_called()
        self.assertEqual((found['enrollment_id'], found['reader'], again['enrollment_id']), ('1000000001', 'Gemini', '1000000001'))
        self.assertEqual([pictures for _, pictures in seen], [1, 1, 1])  # the roster, name and UIN rounds, once; the repeat reads receipts
        with connect(self.app.config['DATABASE']) as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM jev_calls ORDER BY created_at')]
        self.assertEqual(({row['model'] for row in rows}, {row['purpose'] for row in rows}), ({'gemini-decision-1'}, {'paper-student'}))
        kept = json.dumps(rows)
        for words in ('Dawson', 'Porter', '900000305'):
            self.assertNotIn(words, kept)

    def test_a_page_check_still_asks_jev_and_a_gemini_failure_decides_nothing(self):
        page = {'model': client.IMAGE_MODEL, 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'answers': {'has_name': {'type': 'noul', 'noul': .9}}}
        with patch.dict('os.environ', KEY), patch('jev.client.available', return_value=True), \
                patch('jev.client.evaluate', return_value=page) as asked_jev, patch('gemini_decisions.evaluate') as asked_gemini:
            looked = self.jev.ask(jev_papers.PAGE_PURPOSE, 'page-1', 'A page.', {'has_name': noul('A name?')}, jev_papers.RULE, images=[picture((300, 300))])
        self.assertEqual((looked['status'], asked_jev.call_count, asked_gemini.call_count), ('complete', 1, 0))
        jev = FakeJev({})
        jev.ask = self.jev.ask
        failure = gemini.DecisionError('Gemini Decisions could not complete this check (HTTP 503).', refused=True)
        with patch.dict('os.environ', KEY), patch('gemini_decisions.evaluate', side_effect=failure) as asked_gemini, \
                patch('jev.client.available', return_value=True), patch('jev.client.evaluate') as asked_jev:
            found = jev_papers.identify(jev, 'page-2', picture(), CHECK, STUDENTS)
        self.assertEqual((found['status'], found['enrollment_id'], found['reason'], found['reader']),
                         ('failed', None, 'Gemini did not finish matching the name.', 'Gemini'))
        self.assertEqual((asked_gemini.call_count, asked_jev.call_count), (1, 0))  # never resent, and Jev is not asked in its place

    def test_without_the_key_the_match_is_jevs_as_it_was(self):
        def evaluate(state, questions, images=()):
            return {'model': client.IMAGE_MODEL, 'usage': {'input_tokens': 1, 'output_tokens': 1},
                    'answers': {name: {'type': 'choice', 'choice': 'Dawson, Calvin Allan', 'confidence': .95, 'probabilities': {}} for name in questions}}
        jev = FakeJev({})
        jev.ask = self.jev.ask
        with patch.dict('os.environ', {'GEMINI_API_KEY': ''}), patch('jev.client.available', return_value=True), \
                patch('jev.client.evaluate', side_effect=evaluate), patch('gemini_decisions.evaluate') as asked_gemini:
            found = jev_papers.identify(jev, 'page-1', picture(), {**CHECK, 'uin_box': CHECK['name_box']},
                                        {key: STUDENTS[key] for key in ('1000000001', '1000000003')})
        asked_gemini.assert_not_called()
        self.assertEqual(found['enrollment_id'], '1000000001')
        self.assertNotIn('reader', found)


class KeyTests(unittest.TestCase):
    def test_the_google_key_may_be_named_only_by_a_connection_to_googles_own_host(self):
        from grading.config import key_problem
        self.assertEqual(key_problem({'key_env': 'GEMINI_API_KEY', 'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai'}), '')
        for elsewhere in ('https://openrouter.ai/api/v1', 'http://generativelanguage.googleapis.com/v1beta', 'https://example.com/v1'):
            with self.subTest(base_url=elsewhere):
                self.assertIn('GEMINI_API_KEY belongs to generativelanguage.googleapis.com', key_problem({'key_env': 'GEMINI_API_KEY', 'base_url': elsewhere}))


if __name__ == '__main__':
    unittest.main()
