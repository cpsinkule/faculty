"""A host that refuses a reasoning effort as an invalid parameter (2026-09-24): sent once one listed effort lower, then that effort for
the rest of the batch; a refusal nothing fixes stops at once. Measured live: Meta, behind OpenRouter, refused "max" for Muse Spark and
answered "xhigh", and the arbitrator of trial batch 964f1940 was refused 465 times. Loopback hosts only."""
import json
import unittest

from grading import config
from grading.providers.base import ProviderError
from grading.providers.cloud import HostedEndpoint
from fake_openai import FakeHost, grade_answer

META = {"error": {"message": "Provider returned error", "code": 400, "metadata": {"provider_name": "Meta", "raw": json.dumps(
    {"error": {"code": None, "message": "The request contains invalid parameters. Check the request body for any errors or inconsistencies.",
               "param": None, "type": "invalid_request_error"}})}}}
FACTS = {"supported": True, "mandatory": True, "efforts": ["minimal", "low", "medium", "high", "xhigh", "max"], "default_effort": "medium"}
REQUEST = {"prompt": "grade", "payload": {"rubric": {"items": [{"id": "R1"}]}}, "images": []}


class RefusedEffort(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)
        HostedEndpoint.effort_refused.clear()
        self.addCleanup(HostedEndpoint.effort_refused.clear)

    def provider(self, choice="max", **extra):
        return HostedEndpoint({**config.ENDPOINT, "name": "muse", "base_url": self.host.url, "model": "fixture/muse", "lineage": "muse", "key_env": "",
                               "reasoning": True, "reasoning_options": FACTS, "reasoning_choice": choice, **extra}, None)

    def efforts(self):
        return [(request["body"].get("reasoning") or {}).get("effort") for request in self.host.requests]

    def script(self, refused):
        answer = grade_answer({"transcript": {"lines": []}, "rubric": {"items": [{"id": "R1"}]}, "answer_parts": [""]})
        self.host.script = lambda body, host: ("status", 400, {}, META) if (body.get("reasoning") or {}).get("effort") in refused else answer

    def test_a_refused_effort_is_sent_once_one_step_lower_and_the_batch_learns_it(self):
        self.script({"max"})
        outcome = self.provider().submit(REQUEST)
        self.assertEqual(outcome["state"], "completed")
        self.assertEqual(self.efforts(), ["max", "xhigh"])
        metadata = outcome["result"]["metadata"]
        self.assertEqual((metadata["reasoning"], metadata["reasoning_sent"]), ("max", "xhigh"))  # The role's choice, and what was sent.
        self.assertIn("refused the reasoning effort “max”", metadata["reasoning_note"])
        # The rest of the batch goes straight to the effort that was answered: one request each, no refusal first.
        self.provider().submit(REQUEST)
        self.assertEqual(self.efforts(), ["max", "xhigh", "xhigh"])
        # A lower choice is never raised to it, and another model or host has learned nothing.
        self.provider(choice="high").submit(REQUEST)
        self.assertEqual(self.efforts()[-1], "high")
        self.provider(model="fixture/other").submit(REQUEST)
        self.assertEqual(self.efforts()[-2:], ["max", "xhigh"])

    def test_one_step_only_and_a_refusal_it_does_not_fix_stops_at_once(self):
        self.script({"max", "xhigh"})
        with self.assertRaises(ProviderError) as caught:
            self.provider().submit(REQUEST)
        self.assertEqual(self.efforts(), ["max", "xhigh"])
        error = caught.exception
        self.assertEqual((error.status, error.fixable, error.confirmed), (400, False, True))  # Like a refused key: never resent.
        self.assertIn("not retried", str(error))
        self.assertEqual(HostedEndpoint.effort_refused, {})  # The effort was not what stood in the way: nothing learned.
        self.assertIn("invalid parameters", error.confirmation["body"])

    def test_the_facts_decide_what_is_one_step_lower(self):
        self.script({"max"})
        self.provider(reasoning_options={**FACTS, "efforts": ["low", "high", "max"]}).submit(REQUEST)
        self.assertEqual(self.efforts(), ["max", "high"])

    def test_nothing_below_the_lowest_effort_and_nothing_without_an_effort(self):
        self.script({"minimal"})
        with self.assertRaises(ProviderError) as caught:
            self.provider(choice="minimal").submit(REQUEST)
        self.assertEqual((self.efforts(), caught.exception.fixable), (["minimal"], False))
        self.host.script = lambda body, host: ("status", 400, {}, META)
        with self.assertRaises(ProviderError) as caught:
            self.provider(choice="off", reasoning=False, aggregator=False).submit(REQUEST)
        self.assertEqual((len(self.host.requests), caught.exception.fixable), (2, False))

    def test_other_refusals_keep_the_retry_policy(self):
        self.host.script = lambda body, host: ("status", 400, {}, {"error": {"message": "Upstream request failed"}})
        with self.assertRaises(ProviderError) as caught:
            self.provider().submit(REQUEST)
        self.assertEqual((self.efforts(), caught.exception.fixable), (["max"], True))


if __name__ == "__main__":
    unittest.main()
