"""With token limits off a request still names the most its host is published to allow (requirement, 2026-09-24).

A request naming no maximum gets the host's DEFAULT: DeepInfra's is 65,536 for MiMo V2.6 Pro, against a ceiling of 943,718, and two
MiMo rulings ended there after 17 minutes of thinking. The figures below are the gateways' published ones of that day."""
import unittest
from unittest.mock import patch

from grading import config
from grading.model_catalog import ModelCatalog, any_host_room, normalize, normalize_hosts, output_room
from grading.providers.base import ProviderError
from grading.providers.cloud import HostedEndpoint
from fake_openai import FakeHost, grade_answer

REQUEST = {"prompt": "grade", "payload": {"rubric": {"items": [{"id": "R1"}]}}, "images": []}


def host(name, tag, limit, context=1048576):
    return {"name": f"{name} | x", "provider_name": name, "tag": tag, "context_length": context, "max_completion_tokens": limit,
            "pricing": {"prompt": "0.000000435", "completion": "0.00000087"}, "supported_parameters": ["max_tokens", "response_format"], "tags": []}


THINKS = {"supported": True, "mandatory": False, "efforts": [], "default_effort": ""}  # MiMo V2.6 Pro: thinking on or off, no efforts


def connection(**extra):
    return {**config.ENDPOINT, "name": "mimo", "base_url": config.AGGREGATOR, "model": "xiaomi/mimo-v2.6-pro", "key_env": "OPENROUTER_API_KEY",
            "lineage": "mimo", "aggregator": True, "reasoning_options": THINKS, "reasoning_choice": "on", **extra}


class TheFact(unittest.TestCase):
    def test_a_host_ceiling_leaves_the_prompt_its_room_and_a_small_one_is_not_worth_sending(self):
        self.assertEqual(output_room(131072, 1048576), 131072)          # Xiaomi's MiMo V2.6 Pro
        self.assertEqual(output_room(943718, 1048576), 943718)          # DeepInfra's, through OpenRouter (90% of the context)
        self.assertEqual(output_room(1048576, 1048576), 943719)         # Vercel lists the whole context: a tenth stays for the prompt
        self.assertEqual(output_room(131072, 131072), 98304)            # A small context keeps at least 32,768 for the prompt
        self.assertEqual((output_room(65535, 1000000), output_room(32768, 1048576)), (0, 0))  # Gemini on Vercel, baseten: read, never sent
        self.assertEqual((output_room(None, 1048576), output_room(0, None)), (None, None))    # Nothing published: unknown

    def test_any_host_is_given_what_every_host_that_allows_enough_accepts(self):
        mimo = normalize_hosts({"data": {"endpoints": [host("DeepInfra", "deepinfra/fp8", 943718), host("Xiaomi", "xiaomi/fp8", 131072)]}}, "openrouter")
        self.assertEqual({h["id"]: h["output_limit"] for h in mimo}, {"deepinfra/fp8": 943718, "xiaomi/fp8": 131072})
        self.assertEqual(any_host_room(mimo), 131072)  # More would drop Xiaomi from OpenRouter's routing.
        deepseek = normalize_hosts({"data": {"endpoints": [host("Baseten", "baseten/fp8", 32768), host("DeepInfra", "deepinfra/fp8", 131072),
                                                           host("Fireworks", "fireworks", 943718)]}}, "openrouter")
        self.assertEqual(any_host_room(deepseek), 131072)  # Baseten's 32,768 would cut every other host short: it is dropped instead.
        self.assertEqual(any_host_room(normalize_hosts({"data": {"endpoints": [host("google", "", 65535, 1000000)]}}, "vercel")), 0)
        self.assertIsNone(any_host_room([]))
        catalog = ModelCatalog()
        with patch("grading.model_catalog.request_json", return_value={"data": {"endpoints": [host("Xiaomi", "xiaomi/fp8", 131072)]}}):
            self.assertEqual(catalog.hosts("openrouter", "xiaomi/mimo-v2.6-pro")["any_host_limit"], 131072)
        with patch("grading.model_catalog.request_json", side_effect=ProviderError("down", confirmed=False)):
            self.assertIsNone(catalog.hosts("openrouter", "deepseek/deepseek-v4.1-flash")["any_host_limit"])

    def test_browse_takes_the_catalogues_figure_and_a_connection_saves_it(self):
        row = {"id": "xiaomi/mimo-v2.6-pro", "name": "MiMo", "context_length": 1048576, "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
               "pricing": {"prompt": "0.000000435", "completion": "0.00000087"}, "supported_parameters": ["structured_outputs"],
               "top_provider": {"max_completion_tokens": 131072}}
        small = {**row, "id": "google/gemini-3.8-flash", "top_provider": {"max_completion_tokens": 32768}}
        silent = {**row, "id": "lab/unlisted", "top_provider": {}}
        found = {m["id"]: m["endpoint"] for m in normalize({"data": [row, small, silent]})}
        self.assertEqual((found["xiaomi/mimo-v2.6-pro"]["output_limit"], found["google/gemini-3.8-flash"]["output_limit"]), (131072, 0))
        self.assertNotIn("output_limit", found["lab/unlisted"])  # Absent, as on every connection saved before the fact existed.
        saved = config.endpoint_entry({**found["xiaomi/mimo-v2.6-pro"], "name": "mimo", "lineage": "mimo"})
        self.assertEqual(saved["output_limit"], 131072)
        self.assertNotIn("output_limit", config.endpoint_entry({**saved, "output_limit": None}))
        self.assertNotIn("output_limit", config.endpoint_entry({k: v for k, v in connection().items() if k != "reasoning_choice"}))
        for bad in (-1, 1.5, True, "131072", 10**8):
            with self.assertRaises(ValueError):
                config.endpoint_entry({**saved, "output_limit": bad})


class TheRequest(unittest.TestCase):
    def test_limits_off_send_the_published_maximum_and_limits_on_keep_the_instructors_caps(self):
        body, metadata = HostedEndpoint(connection(token_limits=False, output_limit=131072)).body(REQUEST)
        self.assertEqual((body["max_tokens"], metadata["output_limit"]), (131072, 131072))
        on = HostedEndpoint(connection(token_limits=True, output_limit=131072, max_tokens=4000, reasoning_tokens=8192)).body(REQUEST)[0]
        self.assertEqual(on["max_tokens"], 4000 + 8192)  # The instructor's caps, answer and reasoning, exactly as before.
        # Every request asks for it, thinking or not (requirement, 2026-09-24): DeepSeek, grading without thinking on its own host,
        # otherwise stopped at that host's default of 8,192 tokens.
        for plain in (connection(token_limits=False, output_limit=131072, reasoning_choice="off"),
                      connection(token_limits=False, output_limit=131072, reasoning_options=config.NEVER_THINKS, reasoning_choice=None)):
            self.assertEqual(HostedEndpoint(plain).body(REQUEST)[0]["max_tokens"], 131072)
        for quiet in (connection(token_limits=False), connection(token_limits=False, output_limit=0)):
            body, metadata = HostedEndpoint(quiet).body(REQUEST)
            self.assertNotIn("max_tokens", body)  # As before the fact existed: the host's default applies.
            self.assertNotIn("output_limit", metadata)
        body, metadata = HostedEndpoint(connection(token_limits=False, output_limit=131072)).body({**REQUEST, "room": False})
        self.assertNotIn("max_tokens", body)
        self.assertIn("withheld", metadata["output_limit_note"])
        # The frozen profile of a batch from before the fact has no such key, so its requests stay exactly what they were.
        self.assertEqual(HostedEndpoint(connection(token_limits=False)).body(REQUEST)[1]["request_sha256"],
                         HostedEndpoint(connection(token_limits=False)).body(REQUEST)[1]["request_sha256"])


class TheRefusals(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)

    def submit(self, first, **extra):
        answered = grade_answer({"transcript": {"lines": []}, "rubric": {"items": [{"id": "R1"}]}, "answer_parts": [""]})
        self.host.script = lambda body, host: first if "max_tokens" in body else answered
        provider = HostedEndpoint({**connection(token_limits=False, output_limit=131072, **extra), "base_url": self.host.url, "key_env": "", "aggregator": False})
        return provider.submit(REQUEST)

    def test_a_gateway_that_refuses_the_maximum_gets_the_request_once_more_without_it(self):
        refusals = [("status", 404, {}, {"error": {"message": "No endpoints found for xiaomi/mimo-v2.6-pro. Every candidate endpoint was removed during routing: Filter by Context Length removed xiaomi/fp8"}}),
                    ("status", 400, {}, {"error": {"message": "This endpoint's maximum context length is 1048576 tokens. However, you requested about 1048584 tokens"}}),
                    ("status", 400, {}, {"error": {"message": "Param Incorrect", "type": "AI_APICallError"}})]
        for refusal in refusals:
            before = len(self.host.requests)
            outcome = self.submit(refusal)
            sent = [request["body"] for request in self.host.requests[before:]]
            self.assertEqual([("max_tokens" in body) for body in sent], [True, False])
            self.assertEqual(outcome["state"], "completed")
            self.assertIn("withheld", outcome["result"]["metadata"]["output_limit_note"])

    def test_any_other_refusal_is_what_happened(self):
        before = len(self.host.requests)
        with self.assertRaises(ProviderError) as caught:
            self.submit(("status", 404, {}, {"error": {"message": "No endpoints found that support image input"}}))
        self.assertEqual((caught.exception.status, len(self.host.requests) - before), (404, 1))


if __name__ == "__main__":
    unittest.main()
