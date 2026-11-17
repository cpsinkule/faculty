"""OPENAI DECISIONS (2026-10-08): Jev's questions translated, answers back in Jev's shape and bounds, one bounded request."""
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import openai_decisions as decisions
from jev.service import both, noul, score

QUESTIONS = {'yes': noul('Is it true?', {'true': 'It is.', 'false': 'It is not.'}),
             **both('pick', 'Choose one.', {'a': 'Option A', 'b': None, 'c': 'Option C'}),
             'level': score('How soon?', ['Routine: no hurry.', 'Soon: this week.', 'Now'])}


def api(questions=QUESTIONS, picks=None, model=decisions.MODEL, drop=()):
    """A Decisions response as the API sends it (probabilities rounded to two decimals), from scripted picks."""
    picks, answers = picks or {}, []
    for name, asked in questions.items():
        if asked['type'] == 'noul':
            answers.append({'name': name, 'type': 'predicate', 'probability': picks.get(name, .97)})
        elif asked['type'] == 'choice':
            chosen = picks.get(name, next(iter(asked['criteria'])))
            listed = [v for v in asked['criteria'] if v not in drop]  # an option left out had no probability
            answers.append({'name': name, 'type': 'choice', 'choice': chosen, 'confidence': .9,
                            'probabilities': [{'value': v, 'probability': round(1 - .05 * (len(listed) - 1), 2) if v == chosen else .05}
                                              for v in listed]})
        else:
            answers.append({'name': name, 'type': 'score', 'score': 1.0, 'confidence': .8,
                            'probabilities': [{'value': i, 'label': label, 'probability': p} for i, (label, p)
                                              in enumerate((('Routine', .1), ('Soon', .8), ('Level 2', .1)))]})
    return {'model': model, 'answers': answers, 'usage': {'input_tokens': 321, 'output_tokens': 0, 'total_tokens': 321}}


def reply(payload, status=200):
    response = Mock(status=status)
    response.read = Mock(return_value=json.dumps(payload).encode())
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


class TranslationTests(unittest.TestCase):
    def test_jev_questions_become_decisions_questions(self):
        body = decisions.request_body({'b': 1, 'a': 'é'}, QUESTIONS)
        self.assertEqual((body['model'], body['input']), ('gpt-6-luna', '{"a":"é","b":1}'))
        asked = {q['name']: q for q in body['questions']}
        self.assertEqual(asked['yes'], {'type': 'predicate', 'name': 'yes',
                                        'instructions': 'Is it true? Answer yes when: It is. Answer no when: It is not.'})
        self.assertEqual([c['value'] for c in asked['pick__reversed']['choices']], ['c', 'b', 'a'])
        self.assertEqual(asked['pick']['choices'][1], {'value': 'b', 'description': 'b'})  # no description: the option itself
        self.assertEqual([lv['label'] for lv in asked['level']['levels']], ['Routine', 'Soon', 'Level 2'])
        pictured = decisions.request_body('Look.', {'yes': noul('Blank?')}, images=(b'\x89PNG',))
        self.assertEqual(pictured['input'][0]['content'][1], {'type': 'input_image', 'image_url': 'data:image/png;base64,iVBORw=='})

    def test_answers_come_back_in_jevs_shape_with_jevs_confidence(self):
        value = decisions.normalize(api(picks={'pick': 'b', 'pick__reversed': 'b'}), QUESTIONS)
        self.assertEqual((value['model'], value['usage']), ('gpt-6-luna', {'input_tokens': 321, 'output_tokens': 0}))
        self.assertEqual(value['answers']['yes'], {'type': 'noul', 'noul': .97})
        pick = value['answers']['pick']
        self.assertEqual((pick['choice'], pick['probabilities']), ('b', {'a': .05, 'b': .9, 'c': .05}))
        self.assertAlmostEqual(pick['confidence'], (.9 - 1 / 3) / (2 / 3))  # Jev's formula, never the API's own .9
        self.assertEqual(value['answers']['level']['probabilities'], {'0': .1, '1': .8, '2': .1})
        self.assertAlmostEqual(value['answers']['level']['score'], 1.0)
        dropped = decisions.normalize(api(drop=('c',), picks={'pick': 'a', 'pick__reversed': 'a'}), QUESTIONS)
        self.assertEqual(dropped['answers']['pick']['probabilities']['c'], 0.0)  # an unlisted option is 0

    def test_a_wrong_model_a_refusal_or_an_answer_outside_jevs_bounds_fails(self):
        good = api()
        refused = {**good, 'answers': [{'name': 'yes', 'type': 'refusal'}] + good['answers'][1:]}
        unknown = json.loads(json.dumps(good))
        unknown['answers'][1]['choice'] = 'z'
        drifting = json.loads(json.dumps(good))
        drifting['answers'][1]['probabilities'] = [{'value': 'a', 'probability': .5}, {'value': 'b', 'probability': .1}]  # sums to .6
        for bad in ({**good, 'model': 'gpt-6-luna-preview'}, refused, {**good, 'answers': good['answers'][:2]}, unknown, drifting,
                    {**good, 'usage': {}}, {**good, 'answers': good['answers'] + [good['answers'][0]]}):
            with self.subTest(bad=json.dumps(bad)[:80]), self.assertRaises(decisions.DecisionError):
                decisions.normalize(bad, QUESTIONS)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-secret', 'FACULTY_DECISIONS_ENABLED': '1'}).start()

    def test_one_request_to_the_fixed_host_without_redirects(self):
        with patch('openai_decisions.open_request', return_value=reply(api())) as post:
            value = decisions.evaluate({'a': 1}, QUESTIONS)
        self.assertEqual(value['answers']['yes']['noul'], .97)
        sent = post.call_args.args[0]
        self.assertEqual((sent.full_url, sent.get_method()), ('https://api.openai.com/v1/decisions', 'POST'))
        self.assertEqual(sent.headers['Authorization'], 'Bearer synthetic-secret')
        self.assertIsNone(decisions.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))
        self.assertEqual(post.call_count, 1)

    def test_failures_are_failed_or_uncertain_never_resent_and_never_echo_the_host(self):
        echo = HTTPError(decisions.URL, 400, 'Bad', {}, io.BytesIO(b'{"error": {"message": "private student text"}}'))
        cases = ((reply(api(), status=302), False), (echo, False), (URLError('private text and key'), True), (TimeoutError(), True))
        for outcome, uncertain in cases:
            with self.subTest(outcome=type(outcome).__name__), patch('openai_decisions.open_request',
                                                                     **({'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome})) as post:
                with self.assertRaises(decisions.DecisionError) as error:
                    decisions.evaluate('state', QUESTIONS)
                self.assertEqual(error.exception.uncertain, uncertain)
                self.assertNotIn('private', str(error.exception))
                self.assertNotIn('synthetic-secret', str(error.exception))
                self.assertEqual(post.call_count, 1)
        with patch('openai_decisions.open_request') as post, self.assertRaises(decisions.DecisionError):
            decisions.evaluate('x' * 120000, QUESTIONS)  # over the text limit: never sent
        post.assert_not_called()

    def test_host_level_failures_are_flagged_without_host_text(self):
        # JEV HOST FAILURES (2026-10-09): a host refusal decided nothing (`refused`); a timeout or a changed model may have been
        # decided (kept, never resent) but is still the host's (`host`), so the worker that asks pauses.
        import socket
        def http(code, body=b'{"error": {"message": "Incorrect API key provided: sk-...abcd", "type": "invalid_request_error"}}'):
            return HTTPError(decisions.URL, code, 'No', {}, io.BytesIO(body))
        cases = {'401': (http(401), (False, True, True)), '403': (http(403), (False, True, True)), '429': (http(429), (False, True, True)),
                 '503': (http(503), (False, True, True)), '400': (http(400), (False, False, False)),
                 'refused': (URLError(ConnectionRefusedError()), (False, True, True)), 'dns': (URLError(socket.gaierror()), (False, True, True)),
                 'timeout': (TimeoutError(), (True, True, False)), 'reset': (URLError(ConnectionResetError()), (True, True, False))}
        for name, (outcome, (uncertain, host, refused)) in cases.items():
            with self.subTest(name), patch('openai_decisions.open_request', side_effect=outcome):
                with self.assertRaises(decisions.DecisionError) as error:
                    decisions.evaluate('state', QUESTIONS)
                self.assertEqual((error.exception.uncertain, error.exception.host, error.exception.refused), (uncertain, host, refused))
                self.assertNotIn('sk-', str(error.exception))
        with patch('openai_decisions.open_request', return_value=reply(api(model='gpt-6-luna-2026-10-09'))), \
                self.assertRaises(decisions.DecisionError) as error:
            decisions.evaluate('state', QUESTIONS)
        self.assertEqual((error.exception.uncertain, error.exception.host, error.exception.refused), (False, True, False))
        with patch('openai_decisions.open_request', return_value=reply({'model': 'gpt-6-luna', 'answers': 'none'})), \
                self.assertRaises(decisions.DecisionError) as error:
            decisions.evaluate('state', QUESTIONS)
        self.assertFalse(error.exception.host)  # an unreadable answer is this answer's failure, not the host's

    def test_unavailable_without_its_key_or_when_switched_off(self):
        self.assertTrue(decisions.available())
        with patch.dict('os.environ', {'FACULTY_DECISIONS_ENABLED': '0'}):
            self.assertFalse(decisions.available())
        with patch.dict('os.environ', {'OPENAI_API_KEY': ''}), patch('openai_decisions.open_request') as post:
            self.assertFalse(decisions.available())
            with self.assertRaises(decisions.DecisionError):
                decisions.evaluate('state', QUESTIONS)
            post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
