"""Public metadata discovery stays read-only, bounded, cached, and separate from grading."""
import copy
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from grading import config
from grading.model_catalog import ModelCatalog, family, normalize
from grading.providers.base import ProviderError
from grading.providers.cloud import HostedEndpoint
import test_frq_grading as fixture


def model(mid="deepseek/deepseek-v4.1-flash", *, vision=True, **extra):
    return {"id": mid, "name": mid.split("/")[-1], "description": "A synthetic model.", "architecture": {
        "input_modalities": ["text", "image"] if vision else ["text"], "output_modalities": ["text"]},
        "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}, "context_length": 100000,
        "top_provider": {"max_completion_tokens": 4096}, "supported_parameters": ["response_format", "structured_outputs"], **extra}


class CatalogueTests(unittest.TestCase):
    def test_recent_preferences_are_bounded_and_prune_removed_connections(self):
        before = config.validate({})
        self.assertEqual(before['recent_models'], [])
        clean = config.validate({**before, 'recent_models': ['yardstick', 'removed', 'yardstick', 'nebius']})
        self.assertEqual(clean.pop('recent_models'), ['yardstick', 'nebius'])
        before.pop('recent_models')
        self.assertEqual(clean, before)
        for invalid in (['yardstick'] * 41, 'yardstick', [None]):
            with self.assertRaises(ValueError):
                config.validate({'recent_models': invalid})

    def test_metadata_uses_real_units_and_caps_and_rejects_non_chat_or_bad_values(self):
        rows = [model(), model("lab/text", vision=False, supported_parameters=["response_format"]),
                model("lab/raw", supported_parameters=[]), model("lab/bad-prices", pricing={"prompt": "NaN", "completion": "-1"}),
                model("lab/short", top_provider={"max_completion_tokens": 100}),
                model("lab/embedding", architecture={"input_modalities": ["text"], "output_modalities": ["embeddings"]}),
                model("openrouter/auto"), model("https://bad.example/model"), None]
        data = {row["id"]: row for row in normalize({"data": rows})}
        endpoint = data["deepseek/deepseek-v4.1-flash"]["endpoint"]
        self.assertEqual((endpoint["price_in"], endpoint["price_out"], endpoint["max_tokens"]), (.15, .6, 4096))
        self.assertEqual((endpoint["modality"], endpoint["lineage"], endpoint["structured"]), ("vision", "deepseek", "json_schema"))
        self.assertEqual(data["lab/text"]["endpoint"]["structured"], "json_object")
        self.assertEqual(data["lab/raw"]["endpoint"]["structured"], "none")
        self.assertTrue(data["lab/bad-prices"]["unavailable"])
        self.assertTrue(data["lab/short"]["unavailable"])
        self.assertEqual(len(data), 5)
        config.endpoint_entry({**endpoint, "name": "test-deepseek"})

    def test_derivatives_keep_their_base_family_and_unknowns_are_not_independent_by_assumption(self):
        for mid, expected in (("deepseek/deepseek-r1-distill-qwen-32b", "qwen2"),
                              ("deepseek/deepseek-r1-distill-llama-70b", "llama3"),
                              ("lab/qwen3-32b-finetune", "qwen3"), ("lab/unidentified", ""),
                              ("xiaomi/unidentified", ""), ("lab/mimosa", ""),
                              ("lab/mimo-v2.5-finetune", "mimo")):
            self.assertEqual(family(mid), expected)

    def test_small_limits_leave_room_for_input_and_a_reasoning_models_answer(self):
        row = normalize({"data": [model("openai/gpt-oss-120b", vision=False)]})[0]
        endpoint = row["endpoint"]
        self.assertEqual((endpoint["max_tokens"], endpoint["reasoning_tokens"]), (2048, 2048))
        small = normalize({"data": [model("lab/small", context_length=4096)]})[0]
        self.assertEqual(small["endpoint"]["max_tokens"], 2048)
        thinking = normalize({"data": [model("openai/gpt-oss-120b", context_length=4096, top_provider={})]})[0]["endpoint"]
        self.assertLessEqual(thinking["max_tokens"] + thinking["reasoning_tokens"], 2048)

    def test_cache_expiry_stale_fallback_and_recovery_do_not_fetch_on_creation(self):
        with patch("grading.model_catalog.time.monotonic", return_value=100), patch("grading.model_catalog.request_json") as fetch:
            cache = ModelCatalog()
            fetch.assert_not_called()
            fetch.return_value = {"data": [model()]}
            first = cache.listing()
            self.assertFalse(first["stale"])
            self.assertEqual(first, cache.listing())
            self.assertEqual(fetch.call_count, 1)
            cache.listing(refresh=True)
            # Simultaneous refreshes may coalesce; a later explicit refresh downloads again.
        with patch("grading.model_catalog.time.monotonic", return_value=4000), patch("grading.model_catalog.request_json", side_effect=ProviderError("synthetic")) as fetch:
            stale = cache.listing()
            self.assertTrue(stale["stale"])
            self.assertEqual(stale["models"], first["models"])
            self.assertEqual(stale["checked_at"], first["checked_at"])
            cache.listing(refresh=True)
            self.assertEqual(fetch.call_count, 1)
        with patch("grading.model_catalog.time.monotonic", return_value=4031), patch("grading.model_catalog.request_json", return_value={"data": [model("lab/new")]}):
            self.assertFalse(cache.listing()["stale"])
            self.assertEqual(cache.listing()["models"][0]["id"], "lab/new")

    def test_failed_first_load_and_malformed_refresh_leave_saved_catalogue_intact(self):
        cache = ModelCatalog()
        with patch("grading.model_catalog.request_json", side_effect=ProviderError("Private upstream error")):
            failed = cache.listing()
        self.assertEqual(failed["models"], [])
        self.assertNotIn("Private", failed["notice"])
        cache.failed = None
        with patch("grading.model_catalog.request_json", return_value={"data": [model()]}):
            good = cache.listing()
        with patch("grading.model_catalog.request_json", return_value={"data": []}):
            self.assertEqual(cache.listing(refresh=True)["models"], good["models"])

    def test_concurrent_readers_share_one_bounded_public_request(self):
        cache, entered, release = ModelCatalog(), threading.Event(), threading.Event()
        def download(*args, **kwargs):
            self.assertEqual(args, (config.AGGREGATOR + "/models",))
            self.assertNotIn("headers", kwargs)  # No secret or student data on catalogue reads.
            self.assertEqual(kwargs["timeout"], 8)
            entered.set(); release.wait(3)
            return {"data": [model()]}
        with patch("grading.model_catalog.request_json", side_effect=download) as fetch, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(cache.listing)
            self.assertTrue(entered.wait(3))
            second = pool.submit(cache.listing)
            release.set()
            self.assertEqual(first.result(), second.result())
            self.assertEqual(fetch.call_count, 1)

    def test_structured_openrouter_requests_keep_host_pins_and_require_parameter_support(self):
        endpoint = normalize({"data": [model()]})[0]["endpoint"]
        for upstream in ("", "DeepInfra"):
            body, _ = HostedEndpoint({**endpoint, "name": "catalogue", "upstream": upstream}).body(
                {"prompt": "grade", "payload": {"rubric": {"items": []}}, "images": [b"synthetic"]})
            self.assertTrue(body["provider"]["require_parameters"])
            if upstream:
                self.assertEqual(body["provider"]["order"], [upstream])
                self.assertFalse(body["provider"]["allow_fallbacks"])


class CatalogueApiTests(unittest.TestCase):
    managed = False
    setUp = fixture.FrqWorkflowTests.setUp
    document_version = fixture.FrqWorkflowTests.document_version
    ready_batch = fixture.FrqWorkflowTests.ready_batch
    reviewed_source = fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = fixture.FrqWorkflowTests.configure_rubric

    def test_recent_choices_do_not_change_previews_or_frozen_profiles(self):
        command = {'request_id': str(uuid.uuid4()), 'question_ids': [self.qid], 'mode': 'local', 'operation': 'recognize'}
        command['fingerprint'] = self.frq.preview(self.plan['id'], command)['fingerprint']
        current = self.frq.settings()
        current['settings']['recent_models'] = ['deepseek-v4.1-flash', 'yardstick']
        saved = self.frq.save_settings(current)
        self.assertEqual(saved['settings']['recent_models'], current['settings']['recent_models'])
        self.assertEqual(self.frq.preview(self.plan['id'], command)['fingerprint'], command['fingerprint'])
        job = self.frq.create_job(self.plan['id'], command)
        self.assertNotIn('recent_models', job['profile'])
        self.assertEqual(self.frq.settings(), saved)

    def test_discovery_does_not_save_settings_or_run_on_navigation(self):
        before = copy.deepcopy(self.frq.settings())
        client = self.app.test_client()
        with patch("grading.model_catalog.request_json", return_value={"data": [model()]}) as fetch:
            client.get("/api/grading?view=navigation")
            client.get("/api/grading/settings")
            fetch.assert_not_called()
            response = client.get("/api/grading/model-catalog")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(len(response.get_json()["models"]), 1)
            self.assertEqual(self.frq.settings(), before)
            self.assertEqual(client.get("/api/grading/model-catalog?refresh=invalid").status_code, 400)

    def test_a_plan_s_models_are_asked_of_its_program_on_browse_only_and_never_saved(self):
        before = copy.deepcopy(self.frq.settings())
        client = self.app.test_client()
        row = {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "description": "Efficient", "efforts": ["low", "medium", "high"],
               "default_effort": "medium", "vision": True}
        with patch("grading.providers.agent_sdk.listed_models", return_value=[row]) as claude, \
                patch("grading.providers.codex_sdk.listed_models", return_value=[]) as codex:
            client.get("/api/grading?view=navigation")
            client.get("/api/grading/settings")
            claude.assert_not_called()
            codex.assert_not_called()
            response = client.get("/api/grading/model-catalog?provider=claude")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            body = response.get_json()
            self.assertEqual((body["provider"], body["stale"], body["notice"]), ("claude", False, ""))
            self.assertEqual(body["models"], [{"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "description": "Efficient", "vision": True,
                                               "reasoning_options": {"supported": True, "mandatory": True, "efforts": ["low", "medium", "high"], "default_effort": "medium"}}])
            # Nothing is kept: a refresh, or the next Browse, asks again.
            client.get("/api/grading/model-catalog?provider=claude&refresh=1")
            self.assertEqual(claude.call_count, 2)
            # Codex naming nothing usable leaves the built-in list, said so.
            body = client.get("/api/grading/model-catalog?provider=chatgpt").get_json()
            self.assertTrue(body["stale"])
            self.assertIn("gpt-6.1-sol", [m["id"] for m in body["models"]])
            self.assertIn("Codex named no models Faculty can use", body["notice"])
        self.assertEqual(self.frq.settings(), before)
