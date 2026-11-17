"""SANDBOX (requirement, 2026-10-07): OpenAI Decisions beside Jev, and since 2026-10-13 SubQ Snap beside both. Synthetic only: no
request leaves the machine and no catalog but a throwaway one is opened."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from decisions_sandbox import asof, decisions_client as dc, fakes, gemini_client as gc, gemini_native_client as gn, jev_preview_client as jpc, liquid_client as lqc, openrouter_client as orc, report, runner, safety, snap_client as sc
from decisions_sandbox.cases import Case
from decisions_sandbox.checks import followups, long_context, media, messages
from decisions_sandbox.pair import MODES, PairService, Store
from jev.service import both, encoded, fingerprint, noul, score


def reply(body, status=200):
    response = Mock(status=status)
    response.read = Mock(return_value=json.dumps(body).encode())
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


QUESTIONS = {'yes': noul('Is it so?', {'true': 'It is.', 'false': 'It is not.'}),
             **both('pick', 'Pick one.', {'a': 'First.', 'b': None}),
             'level': score('How much?', ['Low: little.', 'High: a lot.'])}
ANSWERS = [{'type': 'predicate', 'name': 'yes', 'probability': .91},
           {'type': 'choice', 'name': 'pick', 'choice': 'a', 'probabilities': [{'value': 'a', 'probability': .8}, {'value': 'b', 'probability': .2}], 'confidence': .6},
           {'type': 'choice', 'name': 'pick__reversed', 'choice': 'a', 'probabilities': [{'value': 'a', 'probability': .7}], 'confidence': .5},
           {'type': 'score', 'name': 'level', 'score': .25, 'probabilities': [{'value': 0, 'label': 'Low', 'probability': .75},
                                                                              {'value': 1, 'label': 'High', 'probability': .25}], 'confidence': .5}]


class TranslationTests(unittest.TestCase):
    def test_questions_translate_with_criteria_choices_and_levels(self):
        body = dc.request_body({'b': 1, 'a': 2}, QUESTIONS)
        self.assertEqual(body['model'], 'gpt-6-luna')
        self.assertEqual(body['input'], '{"a":2,"b":1}')
        by = {q['name']: q for q in body['questions']}
        self.assertEqual(by['yes']['type'], 'predicate')
        self.assertIn('Answer yes when: It is.', by['yes']['instructions'])
        self.assertEqual(by['pick']['choices'], [{'value': 'a', 'description': 'First.'}, {'value': 'b', 'description': 'b'}])
        self.assertEqual([c['value'] for c in by['pick__reversed']['choices']], ['b', 'a'])
        self.assertEqual(by['level']['levels'], [{'label': 'Low', 'description': 'Low: little.'}, {'label': 'High', 'description': 'High: a lot.'}])

    def test_images_are_inline_png_data_urls_after_the_text(self):
        body = dc.request_body('Look.', {'q': noul('Blank?')}, images=(b'\x89PNG fake',))
        content = body['input'][0]['content']
        self.assertEqual(content[0], {'type': 'input_text', 'text': 'Look.'})
        self.assertTrue(content[1]['image_url'].startswith('data:image/png;base64,'))

    def test_answers_normalize_to_jev_shape_and_record_gaps(self):
        cleaned, raw, gaps = dc.normalize({'answers': ANSWERS}, QUESTIONS)
        self.assertEqual(cleaned['yes'], {'type': 'noul', 'noul': .91})
        self.assertEqual(cleaned['pick']['confidence'], (.8 - .5) / .5)
        self.assertEqual(cleaned['pick']['api_confidence'], .6)
        self.assertEqual(cleaned['pick__reversed']['probabilities'], {'b': 0.0, 'a': .7})
        self.assertEqual(gaps['unlisted_options'], {'pick__reversed': 1})
        self.assertEqual(cleaned['level']['probabilities'], {'0': .75, '1': .25})
        self.assertAlmostEqual(cleaned['level']['score'], .25)

    def test_a_refusal_fails_the_whole_answer(self):
        refused = [ANSWERS[0], {'type': 'refusal', 'name': 'pick'}, *ANSWERS[2:]]
        with self.assertRaises(dc.DecisionError) as error:
            dc.normalize({'answers': refused}, QUESTIONS)
        self.assertEqual(error.exception.gaps, {'refused': ['pick']})


class TransportTests(unittest.TestCase):
    def setUp(self):
        patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-secret'}).start()
        self.addCleanup(patch.stopall)

    def test_one_post_to_the_fixed_url_without_redirects(self):
        with patch('decisions_sandbox.decisions_client.open_request', return_value=reply({'answers': ANSWERS, 'usage': {'input_tokens': 42}})) as post:
            value = dc.evaluate({'x': 1}, QUESTIONS)
        self.assertEqual(post.call_count, 1)
        request = post.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.openai.com/v1/decisions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-secret')
        self.assertEqual(value['usage'], {'input_tokens': 42})
        self.assertIsNone(dc.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))

    def test_errors_are_failed_or_uncertain_and_never_echo_the_key_or_body(self):
        error = HTTPError(dc.URL, 429, 'Too Many', {}, None)
        error.read = Mock(return_value=b'{"error": {"type": "rate_limit", "code": "x", "message": "synthetic-secret text"}}')
        with patch('decisions_sandbox.decisions_client.open_request', side_effect=error) as post:
            with self.assertRaises(dc.DecisionError) as failed:
                dc.evaluate('s', QUESTIONS)
        self.assertEqual((failed.exception.status, failed.exception.uncertain), (429, False))
        self.assertNotIn('synthetic-secret', str(failed.exception))
        self.assertEqual(post.call_count, 1)
        with patch('decisions_sandbox.decisions_client.open_request', side_effect=URLError('private')):
            with self.assertRaises(dc.DecisionError) as unknown:
                dc.evaluate('s', QUESTIONS)
        self.assertTrue(unknown.exception.uncertain)
        self.assertNotIn('private', str(unknown.exception))

    def test_answers_outside_jevs_rounding_are_kept_and_marked(self):
        drifted = [dict(a) for a in ANSWERS]
        drifted[1] = {**drifted[1], 'probabilities': [{'value': 'a', 'probability': .8}, {'value': 'b', 'probability': .3}]}
        with patch('decisions_sandbox.decisions_client.open_request', return_value=reply({'answers': drifted})):
            value = dc.evaluate('s', QUESTIONS)
        self.assertIn('outside_jev_bounds', value['gaps'])


SNAP_ANSWERS = {'yes': {'type': 'noul', 'noul': .91},
                'pick': {'type': 'choice', 'choice': 'a', 'probabilities': {'a': .8, 'b': .2}, 'confidence': .6},
                'pick__reversed': {'type': 'choice', 'choice': 'a', 'probabilities': {'b': .3, 'a': .7}, 'confidence': .4},
                'level': {'type': 'score', 'score': .25, 'legend': {'0': 'Low: little.', '1': 'High: a lot.'},
                          'probabilities': {'0': .75, '1': .25}, 'confidence': .5}}


class SnapTests(unittest.TestCase):
    """SubQ Snap takes Jev's own body and answers in Jev's shape; the adapter adds only images and the 16-question split."""
    def setUp(self):
        patch.dict(os.environ, {'SUBQ_API_KEY': 'synthetic-secret'}).start()
        self.addCleanup(patch.stopall)

    def test_the_body_is_jevs_with_images_inline_and_no_data_prefix(self):
        state = {'b': 1, 'a': 2}
        self.assertEqual(sc.request_body(state, QUESTIONS), {'model': 'subq-snap-preview', 'state': state, 'questions': QUESTIONS})
        body = sc.request_body('Look.', {'q': noul('Blank?')}, images=(b'\x89PNG\r\n\x1a\n fake', b'\xff\xd8\xff fake'))
        self.assertEqual([image['mediaType'] for image in body['images']], ['image/png', 'image/jpeg'])
        self.assertTrue(all(set(image) == {'mediaType', 'base64'} and not image['base64'].startswith('data:') for image in body['images']))

    def test_more_images_than_snap_reads_or_another_format_are_refused_unsent(self):
        with patch('decisions_sandbox.snap_client.open_request') as post:
            for images in ((b'\x89PNG\r\n\x1a\n',) * 5, (b'GIF89a',)):
                with self.subTest(images=len(images)), self.assertRaises(sc.DecisionError):
                    sc.evaluate('s', {'q': noul('Blank?')}, images)
        self.assertEqual(post.call_count, 0)

    def test_an_answer_in_jevs_shape_is_read_with_jevs_confidence_formula(self):
        cleaned, raw, gaps = sc.normalize({'answers': SNAP_ANSWERS}, QUESTIONS)
        self.assertEqual(cleaned['yes'], {'type': 'noul', 'noul': .91})
        self.assertEqual((cleaned['pick']['confidence'], cleaned['pick']['api_confidence']), ((.8 - .5) / .5, .6))
        self.assertEqual(list(cleaned['pick__reversed']['probabilities']), ['b', 'a'])
        self.assertEqual(cleaned['level']['probabilities'], {'0': .75, '1': .25})
        self.assertEqual(gaps, {})
        with self.assertRaises(sc.DecisionError):
            sc.normalize({'answers': {**SNAP_ANSWERS, 'yes': {'type': 'choice'}}}, QUESTIONS)
        with self.assertRaises(sc.DecisionError) as missing:
            sc.normalize({'answers': {k: v for k, v in SNAP_ANSWERS.items() if k != 'level'}}, QUESTIONS)
        self.assertEqual(missing.exception.gaps['missing'], ['level'])

    def test_one_post_to_the_fixed_url_and_errors_keep_only_the_error_type(self):
        answer = reply({'model': 'subq-snap-preview', 'answers': SNAP_ANSWERS, 'usage': {'input_tokens': 42, 'output_tokens': 0}})
        with patch('decisions_sandbox.snap_client.open_request', return_value=answer) as post:
            value = sc.evaluate({'x': 1}, QUESTIONS)
        request = post.call_args.args[0]
        self.assertEqual((post.call_count, request.full_url), (1, 'https://api.subq.ai/v1/systemone'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-secret')
        self.assertEqual((value['usage'], value['gaps']), ({'input_tokens': 42, 'output_tokens': 0}, {}))
        self.assertIsNone(sc.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))
        error = HTTPError(sc.URL, 422, 'Unprocessable', {}, None)
        error.read = Mock(return_value=b'{"detail": {"error_type": "validation_error", "message": "synthetic-secret text"}}')
        with patch('decisions_sandbox.snap_client.open_request', side_effect=error) as post:
            with self.assertRaises(sc.DecisionError) as failed:
                sc.evaluate('s', QUESTIONS)
        self.assertEqual((failed.exception.status, failed.exception.uncertain, post.call_count), (422, False, 1))
        self.assertIn('validation_error', str(failed.exception))
        self.assertNotIn('synthetic-secret', str(failed.exception))
        with patch('decisions_sandbox.snap_client.open_request', side_effect=URLError('private')):
            with self.assertRaises(sc.DecisionError) as unknown:
                sc.evaluate('s', QUESTIONS)
        self.assertTrue(unknown.exception.uncertain)

    def test_more_than_sixteen_questions_go_as_several_requests_and_one_failure_fails_all(self):
        questions = {f'q{i}': noul(f'Is sentence {i} a question?') for i in range(40)}
        sent = []

        def answered(request):
            asked = json.loads(request.data)['questions']
            sent.append(list(asked))
            return reply({'model': 'subq-snap-preview', 'answers': {name: {'type': 'noul', 'noul': .5} for name in asked},
                          'usage': {'input_tokens': 10, 'output_tokens': 0}})
        with patch('decisions_sandbox.snap_client.open_request', side_effect=answered):
            value = sc.evaluate('s', questions)
        self.assertEqual([len(part) for part in sent], [16, 16, 8])
        self.assertEqual([name for part in sent for name in part], list(questions))
        self.assertEqual((set(value['answers']), value['usage']['input_tokens'], value['gaps']), (set(questions), 30, {'split_requests': 3}))
        with patch('decisions_sandbox.snap_client.open_request', side_effect=[answered(Mock(data=json.dumps({'questions': dict(list(questions.items())[:16])}))),
                                                                              URLError('down')]) as post:
            with self.assertRaises(sc.DecisionError):
                sc.evaluate('s', questions)
        self.assertEqual(post.call_count, 2)


class OpenRouterTests(unittest.TestCase):
    """Microsoft-Decision-1 through OpenRouter's System One surface: Jev's own body, text only."""
    def setUp(self):
        patch.dict(os.environ, {'OPENROUTER_API_KEY': 'synthetic-secret'}).start()
        self.addCleanup(patch.stopall)

    def test_the_body_is_jevs_with_the_model_s_name_and_an_image_is_refused_unsent(self):
        state = {'b': 1, 'a': 2}
        self.assertEqual(orc.request_body(state, QUESTIONS), {'model': 'microsoft/microsoft-decision-1', 'state': state, 'questions': QUESTIONS})
        with patch('decisions_sandbox.openrouter_client.open_request') as post:
            with self.assertRaises(orc.DecisionError) as refused:
                orc.evaluate('Look.', {'q': noul('Blank?')}, (b'\x89PNG\r\n\x1a\n',))
        self.assertIn('text only', str(refused.exception))
        self.assertEqual(post.call_count, 0)

    def test_liquid_d1_ultra_is_the_same_host_and_puts_images_in_the_state(self):
        self.assertEqual(lqc.request_body({'a': 1}, QUESTIONS), {'model': 'liquid/d1-ultra', 'state': {'a': 1}, 'questions': QUESTIONS})
        png = b'\x89PNG\r\n\x1a\n'
        with patch('decisions_sandbox.liquid_client.HOST.prepare', side_effect=lambda picture: picture):
            body = lqc.request_body({'a': 1}, QUESTIONS, (png,))
        self.assertNotIn('images', body)
        self.assertEqual(body['state'], [{'type': 'text', 'text': '{"a": 1}'},
                                         {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,iVBORw0KGgo='}}])
        answer = reply({'model': 'liquid/d1-ultra-20261015', 'answers': SNAP_ANSWERS, 'usage': {'input_tokens': 42, 'output_tokens': 0}})
        with patch('decisions_sandbox.liquid_client.open_request', return_value=answer) as post:
            value = lqc.evaluate({'x': 1}, QUESTIONS)
        self.assertEqual((post.call_count, post.call_args.args[0].full_url), (1, 'https://openrouter.ai/api/v1/systemone'))
        self.assertEqual(value['model'], 'liquid/d1-ultra-20261015')

    def test_gemini_decision_1_sends_a_recording_a_clip_and_a_pdf_each_as_its_own_part(self):
        self.assertEqual(gc.request_body({'a': 1}, QUESTIONS), {'model': 'google/gemini-decision-1', 'state': {'a': 1}, 'questions': QUESTIONS})
        png, pdf, wav, mp4 = b'\x89PNG\r\n\x1a\n', b'%PDF-1.4', b'RIFF\x00\x00\x00\x00WAVE', b'\x00\x00\x00\x20ftypisom'
        with patch('decisions_sandbox.gemini_client.HOST.prepare', side_effect=lambda picture: picture):
            body = gc.request_body('Look.', QUESTIONS, (png, pdf, wav, mp4))
        self.assertEqual(body['state'], [
            {'type': 'text', 'text': 'Look.'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,iVBORw0KGgo='}},
            {'type': 'file', 'file': {'file_data': 'data:application/pdf;base64,JVBERi0xLjQ='}},
            {'type': 'input_audio', 'input_audio': {'data': 'UklGRgAAAABXQVZF', 'format': 'wav'}},
            {'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,AAAAIGZ0eXBpc29t'}}])
        with patch('decisions_sandbox.gemini_client.DETAIL', {'low': gc.host('low')}), \
                patch.object(gc.systemone.Host, 'prepare', create=True, new=lambda self, picture: picture):
            self.assertEqual(gc.host('low').request_body('Look.', QUESTIONS, (png,))['state'][1]['image_url']['detail'], 'low')
        # Another host of the same surface still takes pictures only, and an unknown file is never sent.
        for client, sent in ((lqc, pdf), (gc, b'PK\x03\x04 a zip')):
            with patch(f'decisions_sandbox.{client.__name__.rsplit(".", 1)[1]}.open_request') as post:
                with self.assertRaises(dc.DecisionError):
                    client.evaluate('Look.', {'q': noul('Blank?')}, (sent,))
            self.assertEqual(post.call_count, 0)

    def test_googles_own_api_takes_a_translated_body_and_its_answers_come_back_under_the_questions_names(self):
        png = b'\x89PNG\r\n\x1a\n'
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'synthetic-google'}), \
                patch.object(gn.Native, 'prepare', create=True, new=lambda self, picture: picture):
            self.assertEqual(gn.HOSTS['google'].request_body({'a': 1}, {'yes': QUESTIONS['yes']}), {
                'model': 'gemini-decision-1', 'input': '{"a": 1}',
                'questions': {'q0': {'type': 'noul', 'instructions': 'Is it so?', 'criteria': {'true_description': 'It is.', 'false_description': 'It is not.'}}}})
            body = gn.HOSTS['googleultra'].request_body('Look.', QUESTIONS, (png,))
            self.assertEqual(body['input'], [{'type': 'text', 'text': 'Look.'},
                                             {'type': 'image', 'data': 'iVBORw0KGgo=', 'mime_type': 'image/png', 'resolution': 'ultra_high'}])
            self.assertNotIn('resolution', gn.HOSTS['google'].request_body('Look.', QUESTIONS, (png,))['input'][1])
            # An option with no description takes "", and every question travels under a plain key.
            self.assertEqual(body['questions']['q1']['criteria'], {'a': 'First.', 'b': ''})
            self.assertEqual(list(body['questions']), ['q0', 'q1', 'q2', 'q3'])
            answer = reply({'answers': {key: SNAP_ANSWERS[name] for key, name in zip(('q0', 'q1', 'q2', 'q3'), QUESTIONS)},
                            'usage': {'total_input_tokens': 42, 'total_output_tokens': 0, 'total_tokens': 42,
                                      'input_tokens_by_modality': [{'modality': 'image', 'tokens': 30}]}})
            with patch('decisions_sandbox.gemini_native_client.open_request', return_value=answer) as post:
                value = gn.HOSTS['google'].evaluate('Look.', QUESTIONS, (png,))
        request = post.call_args.args[0]
        self.assertEqual((post.call_count, request.full_url), (1, 'https://generativelanguage.googleapis.com/v1beta/decisions'))
        self.assertEqual((request.get_header('X-goog-api-key'), request.get_header('Authorization')), ('synthetic-google', None))
        self.assertEqual(set(value['answers']), set(QUESTIONS))
        self.assertEqual(value['usage'], {'input_tokens': 42, 'output_tokens': 0, 'total_tokens': 42, 'image_tokens': 30})

    def test_one_post_to_the_fixed_url_keeps_the_cost_and_an_error_body_fails(self):
        answer = reply({'model': 'microsoft/microsoft-decision-1', 'provider': 'Azure', 'answers': SNAP_ANSWERS,
                        'usage': {'input_tokens': 42, 'output_tokens': 0, 'cost': .0000018}})
        with patch('decisions_sandbox.openrouter_client.open_request', return_value=answer) as post:
            value = orc.evaluate({'x': 1}, QUESTIONS)
        request = post.call_args.args[0]
        self.assertEqual((post.call_count, request.full_url), (1, 'https://openrouter.ai/api/v1/systemone'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-secret')
        self.assertEqual(value['usage'], {'input_tokens': 42, 'output_tokens': 0, 'cost': .0000018})
        self.assertEqual(value['answers']['pick']['confidence'], (.8 - .5) / .5)
        failed = reply({'error': {'code': 502, 'message': 'synthetic-secret upstream text'}})
        with patch('decisions_sandbox.openrouter_client.open_request', return_value=failed):
            with self.assertRaises(orc.DecisionError) as error:
                orc.evaluate({'x': 1}, QUESTIONS)
        self.assertIn('502', str(error.exception))
        self.assertNotIn('synthetic-secret', str(error.exception))
        error = HTTPError(orc.URL, 429, 'Too Many', {}, None)
        error.read = Mock(return_value=b'{"error": {"code": 429, "message": "synthetic-secret"}}')
        with patch('decisions_sandbox.openrouter_client.open_request', side_effect=error):
            with self.assertRaises(orc.DecisionError) as limited:
                orc.evaluate('s', QUESTIONS)
        self.assertEqual((limited.exception.status, limited.exception.uncertain), (429, False))
        self.assertNotIn('synthetic-secret', str(limited.exception))


class JevPreviewTests(unittest.TestCase):
    """Jev 1.14 and the pinned Jev 1.13 with its opt-in images and long context (2026-10-15): Jev's own URL and key, images in
    Jev's `assets` map, and each opt-in header only on a request that needs it."""
    def setUp(self):
        patch.dict(os.environ, {'TYPESAFE_API_KEY': 'synthetic-secret'}).start()
        self.addCleanup(patch.stopall)

    def _sent(self, mode, state, questions, images=()):
        answer = reply({'model': jpc.MODELS[mode], 'answers': {n: {'type': 'noul', 'noul': .5} for n in questions},
                        'usage': {'input_tokens': 42, 'output_tokens': 0}})
        with patch('decisions_sandbox.jev_preview_client.open_request', return_value=answer) as post:
            value = jpc.HOSTS[mode].evaluate(state, questions, images)
        request = post.call_args.args[0]
        self.assertEqual((post.call_count, request.full_url), (1, 'https://api.typesafe.ai/v1/systemone'))
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-secret')
        return value, request, json.loads(request.data)

    def test_a_short_text_request_is_the_apps_own_body_with_no_opt_in_header(self):
        value, request, body = self._sent('jev114', {'a': 1}, {'q': noul('Blank?')})
        self.assertEqual(body, {'model': 'jev-1.14.0', 'state': {'a': 1}, 'questions': {'q': noul('Blank?')}})
        self.assertFalse([h for h in request.header_items() if 'danger' in h[0].lower()])
        self.assertEqual(value['answers']['q']['noul'], .5)

    def test_images_go_as_assets_named_in_the_state_with_the_images_header(self):
        from PIL import Image
        import io
        pictures = []
        for kind in ('PNG', 'JPEG'):
            out = io.BytesIO()
            Image.new('RGB', (400, 300), 'white').save(out, format=kind)
            pictures.append(out.getvalue())
        png, jpg = pictures
        _, request, body = self._sent('jev113x', 'The images are crops.', {'q': noul('Blank?')}, (png, jpg))
        self.assertEqual(body['model'], 'jev-1.13.0')
        self.assertEqual(body['state']['description'], 'The images are crops.')
        refs = [body['state']['image 1'], body['state']['image 2']]
        self.assertEqual(sorted(body['assets']), sorted(refs))
        self.assertEqual([body['assets'][r]['format'] for r in refs], ['png', 'jpg'])
        self.assertEqual(request.get_header('Typesafe-danger-zone-images'), 'true')
        self.assertIsNone(request.get_header('Typesafe-danger-zone-long-context'))
        with patch('decisions_sandbox.jev_preview_client.open_request') as post:
            with self.assertRaises(jpc.DecisionError):
                jpc.HOSTS['jev114'].evaluate('Look.', {'q': noul('Blank?')}, (b'RIFF0000WEBPxx',))
        self.assertEqual(post.call_count, 0)

    def test_an_image_outside_jevs_size_limits_is_scaled_down_or_padded_never_stretched(self):
        from PIL import Image
        import io

        def png(size):
            out = io.BytesIO()
            Image.new('L', size, 0).save(out, format='PNG')
            return out.getvalue()
        for size, wanted in (((600, 120), (600, 280)), ((3000, 1000), (1568, 523)), ((100, 100), (280, 280)), ((800, 600), (800, 600))):
            got = Image.open(io.BytesIO(jpc.prepare(png(size)))).size
            self.assertEqual(got, wanted, size)
        self.assertEqual(jpc.prepare(png((800, 600))), png((800, 600)))

    def test_only_a_request_past_the_default_window_sends_the_long_context_header(self):
        _, request, _ = self._sent('jev114', 'x' * 70_000, {'q': noul('Blank?')})
        self.assertEqual(request.get_header('Typesafe-danger-zone-long-context'), 'true')
        self.assertIsNone(request.get_header('Typesafe-danger-zone-images'))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_failed_log_directory_sync_keeps_the_pending_receipt(self):
        store = Store(self.folder, available=lambda mode: True)
        store.pending_folder.mkdir()
        entry = {'key': 'durable', 'status': 'uncertain'}
        pending = store.pending_folder / 'durable.json'
        pending.write_text(json.dumps(entry))
        with patch('decisions_sandbox.pair.fsync_dir', side_effect=OSError('directory sync failed')):
            with self.assertRaises(OSError):
                store._record(entry)
        self.assertTrue(pending.exists(), 'An undurable log must retain its recovery receipt')
        restarted = Store(self.folder, available=lambda mode: True)
        self.assertEqual(restarted._entries['durable']['status'], 'uncertain')
        self.assertFalse(pending.exists())

    def test_interrupted_provider_is_durably_uncertain_and_never_repeated(self):
        def interrupted(*args):
            intents = list((self.folder / 'pending-calls').glob('*.json'))
            self.assertEqual(len(intents), 1)
            self.assertNotIn('private state', intents[0].read_text())
            raise KeyboardInterrupt
        store = Store(self.folder, ask_jev=interrupted, available=lambda mode: True)
        with self.assertRaises(KeyboardInterrupt):
            PairService(store, 'jev').ask('p', 'o', 'private state', QUESTIONS, 'r')
        never = Mock(side_effect=AssertionError('An uncertain request must not be resent'))
        restarted = Store(self.folder, ask_jev=never, available=lambda mode: True)
        view = PairService(restarted, 'jev').ask('p', 'o', 'private state', QUESTIONS, 'r')
        self.assertEqual(view['status'], 'uncertain')
        self.assertEqual(len(self.folder.joinpath('calls.jsonl').read_text().splitlines()), 1)
        self.assertFalse(list(self.folder.joinpath('pending-calls').glob('*.json')))
        never.assert_not_called()

    def test_termination_before_final_receipt_recovers_the_durable_intent(self):
        store = Store(self.folder, ask_jev=fakes.model('j'), available=lambda mode: True)
        with patch.object(store, '_record', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            PairService(store, 'jev').ask('p', 'o', 'private state', QUESTIONS, 'r')
        never = Mock(side_effect=AssertionError('A pending request must not be resent'))
        restarted = Store(self.folder, ask_jev=never, available=lambda mode: True)
        self.assertEqual(PairService(restarted, 'jev').ask('p', 'o', 'private state', QUESTIONS, 'r')['status'], 'uncertain')
        never.assert_not_called()

    def test_each_request_is_sent_once_per_model_and_waiters_share_it(self):
        sent, gate = [], threading.Event()

        def slow(state, questions, images):
            sent.append(1)
            gate.wait(2)
            return fakes.model('d')(state, questions, images)
        store = Store(self.folder, ask_jev=fakes.model('j'), ask_decisions=slow, available=lambda mode: True)
        views = []
        threads = [threading.Thread(target=lambda: views.append(PairService(store, 'decisions').ask('p', 'o', {'s': 1}, QUESTIONS, 'r')))
                   for _ in range(3)]
        for t in threads:
            t.start()
        gate.set()
        for t in threads:
            t.join()
        self.assertEqual(len(sent), 1)
        self.assertEqual(len({json.dumps(v, sort_keys=True) for v in views}), 1)
        PairService(store, 'jev').ask('p', 'o', {'s': 1}, QUESTIONS, 'r')
        PairService(store, 'decisions', attempt='repeat').ask('p', 'o', {'s': 1}, QUESTIONS, 'r')
        self.assertEqual(len(sent), 2)
        logged = [json.loads(line) for line in (self.folder / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(len(logged), 3)
        self.assertTrue(all('state' not in entry and entry['input_sha256'] == fingerprint({'s': 1}) for entry in logged))

    def test_a_live_jev_receipt_with_the_same_input_is_replayed_and_images_never_go_to_jev(self):
        state = {'messages': ['hello']}
        seed = {(fingerprint(state), encoded(QUESTIONS)): {'answers': fakes.answers(state, QUESTIONS, 'live'), 'usage': {}, 'model': 'jev-1.13.0'}}
        asked = Mock(side_effect=fakes.model('j'))
        store = Store(self.folder, seed=seed, ask_jev=asked, available=lambda mode: True)
        view = PairService(store, 'jev').ask('p', 'o', state, QUESTIONS, 'r')
        self.assertEqual(view['answers'], fakes.answers(state, QUESTIONS, 'live'))
        self.assertEqual(asked.call_count, 0)
        store = Store(self.folder, available=lambda mode: True)
        refused = PairService(store, 'jev').ask('p', 'o', 'look', {'q': noul('Blank?')}, 'r', images=(b'png',))
        self.assertEqual(refused['status'], 'failed')

    def test_a_model_without_a_key_is_unavailable_and_nothing_is_sent(self):
        with patch.dict(os.environ, {}, clear=True):
            for mode in ('decisions', 'snap', 'microsoft', 'liquid', 'gemini', 'google', 'googleultra', 'jev114', 'jev113x'):
                view = PairService(Store(self.folder), mode).ask('p', 'o', 's', QUESTIONS, 'r')
                self.assertEqual(view['status'], 'unavailable')

    def test_every_hosted_mode_asks_its_own_client_and_keeps_five_fields_of_its_answer(self):
        keys = {'OPENAI_API_KEY': 'synthetic', 'SUBQ_API_KEY': 'synthetic', 'OPENROUTER_API_KEY': 'synthetic', 'TYPESAFE_API_KEY': 'synthetic'}
        modules = {'decisions': 'decisions_client', 'snap': 'snap_client', 'microsoft': 'openrouter_client', 'liquid': 'liquid_client',
                   'gemini': 'gemini_client', 'jev114': 'jev_preview_client', 'jev113x': 'jev_preview_client'}
        store = Store(self.folder)
        with patch.dict(os.environ, keys):
            for mode, module in modules.items():
                body = ({'answers': ANSWERS, 'usage': {'input_tokens': 42}} if mode == 'decisions' else
                        {'model': 'reported-' + mode, 'answers': SNAP_ANSWERS, 'usage': {'input_tokens': 42, 'output_tokens': 0}})
                with self.subTest(mode=mode), patch(f'decisions_sandbox.{module}.open_request', return_value=reply(body)) as post:
                    self.assertTrue(store._available(mode))
                    value = store._ask[mode]({'x': 1}, QUESTIONS, ())
                    self.assertEqual(post.call_count, 1)
                    self.assertEqual(sorted(value), ['answers', 'gaps', 'model', 'response_keys', 'usage'])
                    self.assertEqual(value['usage'], body['usage'])
                    self.assertEqual(value['model'], dc.MODEL if mode == 'decisions' else 'reported-' + mode)
                    if mode in jpc.MODELS:
                        self.assertEqual(json.loads(post.call_args.args[0].data)['model'], jpc.MODELS[mode])

    def test_snap_is_a_third_model_with_its_own_receipts_and_reads_images(self):
        seen = []

        def snap(state, questions, images):
            seen.append(len(images))
            return fakes.model('snap')(state, questions, images)
        store = Store(self.folder, ask_jev=fakes.model('j'), ask_decisions=fakes.model('d'), ask_snap=snap,
                      ask_microsoft=fakes.model('m'), available=lambda mode: True, ask_preview=fakes.model,
                      ask_liquid=fakes.model('l'), ask_gemini=fakes.model('g'))
        views = {mode: PairService(store, mode).ask('p', 'o', {'s': 1}, QUESTIONS, 'r') for mode in MODES}
        self.assertEqual({view['status'] for view in views.values()}, {'complete'})
        self.assertEqual(len({view['id'] for view in views.values()}), len(MODES))
        PairService(store, 'snap').ask('p', 'o', 'look', {'q': noul('Blank?')}, 'r', images=(b'png', b'png2'))
        PairService(store, 'snap').ask('p', 'o', {'s': 1}, QUESTIONS, 'r')  # already answered: not asked again
        self.assertEqual(seen, [0, 2])
        logged = [json.loads(line) for line in (self.folder / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(sorted(entry['mode'] for entry in logged), ['decisions', 'gemini', 'google', 'googleultra', 'jev', 'jev113x', 'jev114', 'liquid', 'microsoft', 'snap', 'snap'])


class SafetyTests(unittest.TestCase):
    def test_scratch_names_and_the_live_catalog_are_refused(self):
        for name in ('../escape', 'a/b', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safety.run_folder(name)
        with self.assertRaises(ValueError):
            safety.refuse_live(safety.LIVE_CATALOG)

    def test_the_environment_keeps_only_the_compared_models_keys(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'FACULTY_GMAIL_SENDER': 'x', 'OPENROUTER_API_KEY': 'y',
                                                                               'SUBQ_API_KEY': 'stale'}), \
                patch.object(safety, 'KEY_FILES', (Path(temp) / 'keys.env',)):
            (Path(temp) / 'keys.env').write_text('TYPESAFE_API_KEY=t\nOPENAI_API_KEY="o"\nMATHPIX_APP_KEY=m\n')
            held = safety.scrub_environment(Path(temp))
            self.assertEqual(held, {'TYPESAFE_API_KEY': True, 'OPENAI_API_KEY': True, 'SUBQ_API_KEY': False, 'OPENROUTER_API_KEY': False,
                                    'GEMINI_API_KEY': False})
            self.assertNotIn('SUBQ_API_KEY', os.environ)
            self.assertNotIn('FACULTY_GMAIL_SENDER', os.environ)
            self.assertNotIn('OPENROUTER_API_KEY', os.environ)
            self.assertNotIn('MATHPIX_APP_KEY', os.environ)
            self.assertEqual(os.environ['OPENAI_API_KEY'], 'o')


class RunFolderTests(unittest.TestCase):
    def test_a_dry_run_and_a_real_run_never_share_a_folder(self):
        import manage_decisions_sandbox as manage
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.assertIsNone(manage.scripted(folder))
            manage.store_for(folder, True)  # an empty folder takes either
            (folder / 'calls.jsonl').write_text(json.dumps({'model': 'fake-snap'}) + '\n')
            with self.assertRaises(SystemExit):
                manage.store_for(folder, False)
            (folder / 'calls.jsonl').write_text(json.dumps({'model': 'subq-snap-preview'}) + '\n')
            with self.assertRaises(SystemExit):
                manage.store_for(folder, True)


class AsOfTests(unittest.TestCase):
    def test_history_tables_show_only_rows_written_by_the_moment(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'copy.sqlite3'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE comm_messages (id TEXT, captured_at TEXT)')
            db.executemany('INSERT INTO comm_messages VALUES (?,?)', [('m1', '2026-10-01T10:00:00Z'), ('m2', '2026-10-03T10:00:00+00:00')])
            db.commit()
            db.close()
            view = asof.at(path, '2026-10-02T00:00:00Z')
            self.assertEqual([r[0] for r in view.execute('SELECT id FROM comm_messages')], ['m1'])
            self.assertEqual([r[0] for r in view.execute('SELECT id FROM main.comm_messages')], ['m1', 'm2'])
            with self.assertRaises(sqlite3.OperationalError):
                view.execute('INSERT INTO main.comm_messages VALUES (?,?)', ('m3', ''))
            view.close()


class CaseTests(unittest.TestCase):
    """Every invented case runs through its site's code on scripted answers, and the report carries none of their text."""
    def test_invented_cases_run_and_the_report_holds_no_message_text(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            store = Store(folder, ask_jev=fakes.model('jev'), ask_decisions=fakes.model('decisions'), ask_snap=fakes.model('snap'),
                          ask_microsoft=fakes.model('microsoft'), available=lambda mode: True, ask_preview=fakes.model,
                          ask_liquid=fakes.model('liquid'), ask_gemini=fakes.model('gemini'))
            made = {name: patch(f'decisions_sandbox.invented_media.{name}', return_value=name.encode()) for name in ('speech', 'document', 'scanned', 'page_png')}
            with patch.object(long_context, 'SIZES', (2000,)), patch.dict(long_context.CHECKS, {2000: 'long_context_0002k'}), \
                    made['speech'] as spoken, made['document'], made['scanned'], made['page_png']:
                cases = followups.invented() + messages.invented() + long_context.invented()
                heard = media.invented()
                runner.run(store, heard, folder / 'results-media.jsonl', modes=MODES, say=lambda *_: None)
                # A recording or a PDF goes only to the model that reads one; the same document's words go to every model.
                read = {(l['check'], tuple(sorted(l['results']))) for l in map(json.loads, (folder / 'results-media.jsonl').read_text().splitlines())}
                self.assertEqual({modes for check, modes in read if check in ('spoken_routing', 'document_pdf', 'document_scan')}, {('gemini',)})
                self.assertEqual({modes for check, modes in read if check == 'document_text'}, {tuple(sorted(MODES))})
                self.assertEqual(spoken.call_count, 2 * len(messages.ROUTES))
                (folder / 'results-media.jsonl').unlink()
                runner.run(store, cases, folder / 'results.jsonl', modes=MODES, say=lambda *_: None)
            lines = [json.loads(line) for line in (folder / 'results.jsonl').read_text().splitlines()]
            self.assertEqual(len(lines), len(cases))
            self.assertEqual([l['id'] for l in lines if any(r.get('error') for r in l['results'].values())], [])
            self.assertTrue(all(set(l['results']) == set(MODES) for l in lines))
            report.write(folder)
            self.assertIn('| check | labels | Jev | Decisions | Snap | Microsoft | Jev 1.14 | Jev 1.13 opt-in | Liquid d1-ultra | Gemini Decision 1 |', (folder / 'report.md').read_text())
            self.assertIn('Jev and Snap', (folder / 'report.md').read_text())
            self.assertEqual((folder / 'disagreements.csv').read_text().splitlines()[0],
                             'check,case,source,fields,jev,decisions,snap,microsoft,jev114,jev113x,liquid,gemini,google,googleultra,labels,meta')
            written = ' '.join((folder / name).read_text() for name in ('report.md', 'disagreements.csv', 'summary.json', 'results.jsonl'))
            for phrase in ('Thank you so much', 'grandfather passed away', 'DR center\'s confirmation', 'I refreshed and it now shows',
                           'anxiety medication', 'golf tournament', 'doctor\'s appointment for an ear infection'):
                self.assertNotIn(phrase, written)
            calls = (folder / 'calls.jsonl').read_text()
            self.assertNotIn('grandfather passed away', calls)

    def test_private_notes_are_compared_in_code_and_what_the_student_said_does_not_count(self):
        self.assertTrue(messages.private_overlap('Given your anxiety medication change affecting your sleep, ...',
                                                 ['Mentioned an anxiety medication change affecting sleep.'], ['Can I have more time?']))
        self.assertFalse(messages.private_overlap('Once your DR letter is processed, extra time applies.', ['DR letter pending.'],
                                                  ['My DR letter is still pending.']))
        self.assertTrue(messages.private_overlap('As I noted in my notes, you were late.', [], []))

    def test_the_long_notes_hold_the_asked_line_once_or_not_at_all(self):
        for depth in (.1, .9, None):
            lines, section, quiz, room = long_context.notes(30000, 7, depth)
            asked = [line for line in lines if line.startswith(f'Section {section}: the makeup for Quiz {quiz} ')]
            self.assertEqual(len(asked), 0 if depth is None else 1)
            self.assertEqual(room is None, depth is None)
            self.assertGreaterEqual(sum(len(line) + 1 for line in lines), 30000)
        self.assertEqual(long_context.notes(30000, 7, .5)[0], long_context.notes(30000, 7, .5)[0])
        self.assertEqual(len(long_context.invented()), 6 * len(long_context.SIZES))

    def test_a_two_model_run_still_reports_two_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            store = Store(folder, ask_jev=fakes.model('jev'), ask_decisions=fakes.model('decisions'), available=lambda mode: True)
            runner.run(store, messages.invented()[:3], folder / 'results.jsonl', say=lambda *_: None)  # no modes named: Snap is not asked
            report.write(folder)
            written = (folder / 'report.md').read_text()
            self.assertIn('| check | labels | Jev | Decisions |\n', written)
            self.assertNotIn('Snap', written)
            self.assertNotIn('"mode": "snap"', (folder / 'calls.jsonl').read_text())

    def test_a_case_that_raises_is_recorded_and_the_run_goes_on(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            store = Store(folder, ask_jev=fakes.model('jev'), ask_decisions=fakes.model('decisions'), available=lambda mode: True)

            def broken(service):
                raise KeyError('missing')
            runner.run(store, [Case('x', 'bad', 'invented', broken)], folder / 'results.jsonl', say=lambda *_: None)
            line = json.loads((folder / 'results.jsonl').read_text())
            self.assertTrue(line['results']['jev']['error'].startswith('KeyError'))


if __name__ == '__main__':
    unittest.main()
