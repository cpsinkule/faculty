"""An optional spare for the two graders (requirement, 2026-09-21), after a live batch lost 21 primary and 23 second calls.

It is asked ONLY for a request that has exhausted its own attempt cap, once, on its own retained step. Nothing is switched
permanently: the next response starts again with the configured grader, because those steps belong to one task alone.
"""
import json

from grading import config
from rosters import connect
from fake_openai import grade_answer, images_of, payload_of
import test_grading_teams as teams
from test_grading_cloud import endpoint
from test_grading_pipeline import CORRECT, Pipeline


class FallbackGraders(Pipeline):
    legacy_workflow = False
    opinions, requests = teams.GradingTeams.opinions, teams.GradingTeams.requests

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        s = saved['settings']
        s['endpoints'].extend([endpoint(self.host, 'spare-vision'), endpoint(self.host, 'spare-text', modality='text')])
        s['role_fallbacks'] = {'cloud_standard': {'reader': 'spare-vision', 'primary': 'spare-vision', 'second': 'spare-text'}}
        self.frq.save_settings({'version': saved['version'], 'settings': s})

    GRADING = ('grade', 'transcript_grade', 'second_vision')

    def breaking(self, model, broken=None, chosen=('R1',)):
        """A script that fails the named model's GRADING requests (never the reader's, which shares a connection).

        The fixture consults a script twice per request, so this holds no counter: `broken['on']` is flipped by the test.
        """
        broken = {'on': True} if broken is None else broken
        def script(name, body):
            if name not in self.GRADING:
                return None  # The reader and anything else are answered by the fixture as usual.
            if body['model'] == f'fixture/{model}' and broken['on']:
                return ('status', 500, {}, {'error': 'unavailable'})
            return grade_answer(payload_of(body), chosen, image=bool(images_of(body)))
        return script

    def steps(self, response):
        with connect(self.path) as db:
            return {r[0]: json.loads(r[1]) for r in db.execute("SELECT stage, output_json FROM frq_steps WHERE task_id=? ORDER BY rowid", (response['task']['id'],))}

    def test_it_is_off_by_default_and_only_capabilities_restrict_selection(self):
        defaults = config.validate({})
        self.assertEqual((defaults['role_fallbacks'], config.fallback(defaults, 'cloud_standard', 'primary')), ({}, None))
        for bad, why in (({'cloud_standard': {'primary': 'gpt-oss-120b'}}, 'the primary reads the image'),
                         ({'cloud_standard': {'reader': 'gpt-oss-120b'}}, 'the reader reads the image'),
                         ({'cloud_standard': {'verifier': 'kimi-k2.5'}}, 'the additional reviewer has none'),
                         ({'cloud_standard': {'primary': 'nobody'}}, 'unknown connection')):
            with self.subTest(why=why), self.assertRaises(ValueError):
                config.validate({'role_fallbacks': bad})
        same_family = config.validate({'role_fallbacks': {'cloud_standard': {'second': 'qwen3-vl-235b'}}})
        self.assertEqual(config.fallback(same_family, 'cloud_standard', 'second')['name'], 'qwen3-vl-235b')
        # It takes its own default reasoning: the choice saved for a role belongs to the model chosen there.
        chosen = config.validate({'role_fallbacks': {'cloud_standard': {'primary': 'qwen3-vl-235b'}},
                                  'role_reasoning': {'cloud_standard': {'primary': 'off'}}})
        self.assertEqual(config.fallback(chosen, 'cloud_standard', 'primary')['name'], 'qwen3-vl-235b')

    def test_the_primary_grader_falls_back_once_and_the_team_carries_on(self):
        response, names = self.grade(CORRECT, script=self.breaking('alpha-vision'))
        steps = self.steps(response)
        self.assertTrue(steps['grade:primary']['failed'])            # The configured grader gave up at its cap.
        self.assertFalse(steps['grade:primary:fallback'].get('failed'))  # The spare answered, on its own step.
        self.assertEqual(steps['grade:primary:fallback']['endpoint'], 'spare-vision')
        result = response['result']
        # The spare's assessment is the standing one, and every check names the model that actually graded.
        self.assertEqual((result['state'], result['signals']['committee']['status']), ('proposed', 'resolved'))
        self.assertEqual(result['signals']['models']['primary']['endpoint'], 'spare-vision')
        self.assertEqual(result['signals']['risk']['components']['primary']['lineage'], 'spare')
        self.assertEqual((self.proposal(response), response['acceptance']['kind']), (['R1'], 'automatic'))
        # Asked once, and only after the cap: the spare is not a second opinion.
        asked = [r['body']['model'] for r in self.host.requests if r['path'].endswith('/chat/completions')]
        self.assertEqual(asked.count('fixture/spare-vision'), 1)
        self.assertGreater(asked.count('fixture/alpha-vision'), 1)

    def test_the_second_grader_falls_back_too(self):
        response, _ = self.grade(CORRECT, script=self.breaking('beta-text'))
        steps = self.steps(response)
        self.assertTrue(steps['grade:second']['failed'])
        self.assertEqual(steps['grade:second:fallback']['endpoint'], 'spare-text')
        result = response['result']
        self.assertEqual((result['state'], result['signals']['committee']['status']), ('proposed', 'resolved'))
        self.assertEqual(result['signals']['second']['endpoint'], 'spare-text')
        self.assertEqual(result['signals']['risk']['components']['second']['lineage'], 'spare')

    def test_the_next_response_starts_again_with_the_chosen_grader(self):
        """"Switches back immediately": the spare belongs to the one request that needed it, never to the batch."""
        broken = {'on': True}
        script = self.breaking('alpha-vision', broken)
        first, _ = self.grade(CORRECT, script=script)
        self.assertEqual(self.steps(first)['grade:primary:fallback']['endpoint'], 'spare-vision')
        broken['on'] = False  # The configured grader is healthy again.
        before = len(self.host.requests)
        second, _ = self.grade(CORRECT, script=script)
        steps = self.steps(second)
        self.assertNotIn('grade:primary:fallback', steps)  # Nothing was switched: it went straight back to the original.
        self.assertEqual(steps['grade:primary']['endpoint'], 'alpha-vision')
        self.assertNotIn('fixture/spare-vision', [r['body']['model'] for r in self.host.requests[before:] if r['path'].endswith('/chat/completions')])

    def test_a_spare_that_fails_too_leaves_the_response_where_it_was(self):
        """The task fails as it always did when the primary cannot complete: a spare adds an attempt, never a silent grade."""
        from unittest.mock import patch
        from frq_fixtures import page
        from test_grading_pipeline import Reader
        from fake_openai import prompt_name, reconcile_answer
        def answer(body, host):
            name = prompt_name(body)
            if name == 'reconcile':
                return reconcile_answer(payload_of(body))
            if body['model'] in ('fixture/alpha-vision', 'fixture/spare-vision'):
                return ('status', 500, {}, {'error': 'unavailable'})
            return grade_answer(payload_of(body), ('R1',), image=bool(images_of(body)))
        self.host.script = answer
        patch.stopall()
        patch('grading.extraction.extract', return_value={'metadata': {'registered': True, 'crop_bounds': [0, 0, 100, 100]},
            'images': {k: page() for k in ('original', 'grayscale', 'contrast', 'ink', 'cloud')}}).start()
        patch.object(self.frq, 'provider', return_value=Reader(CORRECT)).start()
        self.addCleanup(patch.stopall)
        job = self.queue(mode='cloud_standard')
        self.run_all()
        done = self.frq.job(job['id'])
        self.assertEqual(done['status'], 'failed')
        with connect(self.path) as db:
            steps = {r[0]: json.loads(r[1]) for r in db.execute("""SELECT s.stage, s.output_json FROM frq_steps s JOIN frq_tasks t ON t.id=s.task_id
                WHERE t.job_id=? AND s.stage LIKE 'grade:primary%'""", (job['id'],))}
        self.assertTrue(steps['grade:primary']['failed'] and steps['grade:primary:fallback']['failed'])

    def test_the_reader_falls_back_when_its_host_gives_up(self):
        """The reader stalled a whole live batch on 2026-09-21: 852 rate limits from one model, 29 of 240 responses in an hour."""
        from fake_openai import reconcile_answer
        def script(name, body):
            if name != 'reconcile':
                return None
            if body['model'] == 'fixture/alpha-vision':
                return ('status', 429, {}, {'error': {'message': 'temporarily rate-limited upstream', 'code': 429}})
            return reconcile_answer(payload_of(body))
        response, _ = self.grade(CORRECT, script=script)
        self.assertEqual(response['result']['state'], 'proposed')
        with connect(self.path) as db:
            calls = [tuple(r) for r in db.execute("""SELECT c.step, c.endpoint, c.state FROM frq_cloud_calls c JOIN frq_tasks t ON t.id=c.task_id
                WHERE t.id=? AND c.step LIKE 'reconciling%' AND c.state='completed'""", (response['task']['id'],))]
        self.assertEqual(calls, [('reconciling:fallback', 'spare-vision', 'completed')])  # The spare read it, on its own step.
        self.assertTrue(response['result']['transcript']['lines'])

    def test_a_batch_without_a_spare_runs_exactly_as_it_did(self):
        saved = self.frq.settings()
        self.frq.save_settings({'version': saved['version'], 'settings': {**saved['settings'], 'role_fallbacks': {}}})
        response, names = self.grade(CORRECT)
        self.assertNotIn('grade:primary:fallback', self.steps(response))
        scope = response['result']['signals']['scope']
        for role in ('reader', 'primary', 'second'):
            self.assertNotIn(role + '_fallback', scope['teams']['cloud_standard'])  # Its scope is the scope it always had.

    def test_a_configured_spare_is_named_in_the_acceptance_scope(self):
        response, _ = self.grade(CORRECT)
        team = response['result']['signals']['scope']['teams']['cloud_standard']
        self.assertEqual((team['reader_fallback']['endpoint'], team['primary_fallback']['endpoint'], team['second_fallback']['endpoint']),
                         ('spare-vision', 'spare-vision', 'spare-text'))


del Pipeline  # Imported test cases are collected again by the loader; this file runs only its own.
