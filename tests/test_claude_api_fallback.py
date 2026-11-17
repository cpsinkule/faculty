"""GRADING CLAUDE API FALLBACK: opt-in direct billing only after a confirmed subscription limit."""
import json
import unittest
from unittest.mock import patch

from grading import config
from grading.providers import agent_sdk
from grading.providers.base import ProviderError
from grading.providers.cloud import provider
from rosters import connect
from test_agent_sdk import FakeBinary, PROBE_SCHEMA, subscription
import test_agent_sdk
from test_grading_cloud import CloudCase


class ApiFallback(FakeBinary):
    def setUp(self):
        super().setUp()
        self.key = patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'synthetic-anthropic-key'})
        self.key.start()
        self.addCleanup(self.key.stop)

    def configure(self, script='limit', api_script=''):
        self.control.write_text(json.dumps({'script': script, 'api_script': api_script}))

    def ask(self, endpoint):
        return endpoint.submit({'prompt': 'probe', 'payload': {'double': 4, 'tag': 'test'}, 'images': [], 'schema': PROBE_SCHEMA})

    def test_opt_in_preserves_old_connections_and_rejects_other_providers(self):
        self.assertNotIn('api_fallback', subscription())
        self.assertEqual(subscription(api_fallback=False), subscription())
        self.assertTrue(subscription(api_fallback=True)['api_fallback'])
        for wrong in ('yes', 1, None):
            with self.assertRaises(ValueError):
                subscription(api_fallback=wrong)
        with self.assertRaises(ValueError):
            config.endpoint_entry({'name': 'other', 'kind': 'codex_sdk', 'model': 'gpt-6-astra', 'api_fallback': True})

    def test_limit_retries_same_request_and_model_on_api_and_records_estimated_cost(self):
        self.configure()
        endpoint = provider(subscription(api_fallback=True))
        result = self.ask(endpoint)['result']
        self.assertEqual(result['output']['sum'], 8)
        calls = self.invocations()
        self.assertEqual([c['api_key'] for c in calls], [False, True])
        self.assertEqual(len({c['model'] for c in calls}), 1)
        self.assertEqual([c['tags'] for c in calls], [['test'], ['test']])
        self.assertTrue(all(c['safe_mode'] and c['tools'] == '' for c in calls))
        meta = result['metadata']
        self.assertEqual(meta['api_fallback']['reason'], 'subscription_usage_limit')
        self.assertEqual(meta['usage']['billing'], 'anthropic_api')
        self.assertEqual(endpoint.cost(meta['usage']), (.01, 'estimated by Claude Code for direct Anthropic API usage'))
        self.assertNotIn('synthetic-anthropic-key', json.dumps(result))

    def test_paused_subscription_uses_api_until_reset_then_returns_to_subscription(self):
        self.configure()
        endpoint = provider(subscription(api_fallback=True))
        self.ask(endpoint)
        self.ask(endpoint)
        self.assertEqual([c['api_key'] for c in self.invocations()], [False, True, True])
        agent_sdk.AgentSdkEndpoint._paused.clear()
        self.configure(script='')
        self.assertEqual(self.ask(endpoint)['result']['metadata']['usage']['billing'], 'claude_subscription')
        self.assertFalse(self.invocations()[-1]['api_key'])

    def test_cli_error_notice_does_not_count_as_a_partially_generated_answer(self):
        self.configure(script='limit_message')
        result = self.ask(provider(subscription(api_fallback=True)))
        self.assertEqual(result['result']['metadata']['usage']['billing'], 'anthropic_api')

    def test_disabled_or_missing_key_keeps_waiting_without_api_billing(self):
        self.configure()
        with self.assertRaises(ProviderError):
            self.ask(provider(subscription()))
        agent_sdk.AgentSdkEndpoint._paused.clear()
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': ''}):
            with self.assertRaises(ProviderError):
                self.ask(provider(subscription(api_fallback=True)))
        self.assertTrue(all(not c['api_key'] for c in self.invocations()))

    def test_capacity_unknown_and_partial_answers_never_trigger_api_fallback(self):
        for script in ('error', 'rate', 'unknown', 'partial_limit'):
            with self.subTest(script=script):
                agent_sdk.AgentSdkEndpoint._paused.clear()
                agent_sdk.AgentSdkEndpoint._busy.clear()
                before = len(self.invocations())
                self.configure(script=script)
                with self.assertRaises(ProviderError):
                    self.ask(provider(subscription(api_fallback=True)))
                self.assertEqual(len(self.invocations()), before + 1)
                self.assertFalse(self.invocations()[-1]['api_key'])

    def test_api_rate_limit_is_not_a_subscription_limit_or_another_fallback(self):
        self.configure(api_script='rate')
        with self.assertRaises(ProviderError) as found:
            self.ask(provider(subscription(api_fallback=True)))
        self.assertEqual(found.exception.status, 429)
        self.assertNotIn('usage_limit', found.exception.confirmation)
        self.assertIn('api_fallback', found.exception.metadata)
        self.assertEqual([c['api_key'] for c in self.invocations()], [False, True])

    def test_bundles_keep_individual_answers_and_split_api_cost_once(self):
        self.configure()
        endpoint = provider(subscription(api_fallback=True, bundle_size=4))
        results = test_agent_sdk.Bundles.ask(self, endpoint, 4)
        self.assertEqual([r['result']['output']['sum'] for r in results], [0, 2, 4, 6])
        self.assertEqual(sum(endpoint.cost(r['result']['metadata']['usage'])[0] for r in results), .01)
        self.assertEqual([c['api_key'] for c in self.invocations()], [False, True])
        self.assertTrue(all(r['result']['metadata']['api_fallback'] for r in results))

    def test_missing_or_invalid_api_cost_is_unknown_not_free(self):
        endpoint = provider(subscription(api_fallback=True))
        for value in (None, -1, float('nan'), True):
            self.assertIsNone(endpoint.cost({'billing': 'anthropic_api', 'api_cost_usd': value})[0])

    def test_workspace_header_only_reaches_the_direct_api_request(self):
        self.configure()
        with patch.dict('os.environ', {'ANTHROPIC_WORKSPACE_ID': 'wrkspc_test123'}):
            self.ask(provider(subscription(api_fallback=True)))
        self.assertEqual([c['custom_headers'] for c in self.invocations()], [None, 'anthropic-workspace-id: wrkspc_test123'])

    def test_invalid_workspace_cannot_inject_headers(self):
        self.configure()
        with patch.dict('os.environ', {'ANTHROPIC_WORKSPACE_ID': 'wrkspc_test\nother-header: secret'}):
            with self.assertRaises(ProviderError) as found:
                self.ask(provider(subscription(api_fallback=True)))
        self.assertFalse(found.exception.fixable)
        self.assertEqual(len(self.invocations()), 1)

    def test_missing_required_workspace_stops_without_useless_retries(self):
        self.configure(api_script='workspace_required')
        with self.assertRaises(ProviderError) as found:
            self.ask(provider(subscription(api_fallback=True)))
        self.assertFalse(found.exception.fixable)
        self.assertEqual(found.exception.status, 400)
        self.assertIn('ANTHROPIC_WORKSPACE_ID', str(found.exception))


class FallbackBatch(CloudCase, FakeBinary):
    def setUp(self):
        CloudCase.setUp(self)
        FakeBinary.setUp(self)

    def test_pipeline_records_api_cost_and_fallback_receipt_and_keeps_frozen_preference(self):
        self.configure([subscription(api_fallback=True, bundle_size=4)])
        self.control.write_text(json.dumps({'script': 'limit', 'api_script': ''}))
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'synthetic-anthropic-key'}):
            job = self.queue()
            self.run_all()
        done = self.frq.job(job['id'])
        self.assertEqual(done['status'], 'review_ready')
        self.assertGreater(done['cost'], 0)
        calls = self.calls()
        api_calls = [c for c in calls if json.loads(c['usage_json'] or '{}').get('billing') == 'anthropic_api']
        self.assertTrue(api_calls)
        self.assertTrue(all(c['cost'] > 0 for c in api_calls))
        self.assertAlmostEqual(done['cost'], sum(c['cost'] or 0 for c in calls))
        self.assertTrue(all('api_fallback' in json.loads(c['receipt_json'])['result']['metadata'] for c in api_calls))
        self.assertNotIn('synthetic-anthropic-key', json.dumps(calls))
        with connect(self.path) as db:
            profile = json.loads(db.execute('SELECT profile_json FROM frq_jobs WHERE id=?', (job['id'],)).fetchone()[0])
        self.assertTrue(profile['endpoints'][0]['api_fallback'])


if __name__ == '__main__':
    unittest.main()
