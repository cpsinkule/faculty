"""Vercel discovery and request contracts stay separate from OpenRouter and frozen settings."""
import copy
import os
import unittest
from unittest.mock import patch

from grading import config
from grading.model_catalog import ModelCatalog, family, normalize
from grading.providers.base import ProviderError
from grading.providers.cloud import HostedEndpoint
from test_grading_model_catalog import CatalogueApiTests, model


def gateway_model(mid="alibaba/qwen3.8-flash", **extra):
    return {"id": mid, "name": mid, "type": "language", "context_window": 991000, "max_tokens": 128000,
            "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
            "tags": ["reasoning", "vision"], "temperature": True,
            "reasoning_options": [{"type": "toggle"}, {"type": "effort", "values": ["low", "medium", "xhigh"]},
                                  {"type": "budget_tokens"}],
            "pricing": {"input": "0.00000015", "output": "0.00000047"}, **extra}


class GatewayTests(unittest.TestCase):
    def test_mimo_choices_have_one_family_across_gateways(self):
        for provider, make in (("vercel", gateway_model), ("openrouter", model)):
            for mid in ("xiaomi/mimo-v2-flash", "xiaomi/mimo-v2.5", "xiaomi/mimo-v2.5-pro",
                        "xiaomi/mimo-v2.6-flash", "xiaomi/mimo-v2.6-pro", "xiaomi/mimo-v2.6-pro-ultraspeed"):
                with self.subTest(provider=provider, model=mid):
                    row = normalize({"data": [make(mid)]}, provider)[0]
                    endpoint = config.endpoint_entry({**row["endpoint"], "name": "mimo-choice"})
                    self.assertEqual(endpoint["lineage"], "mimo")
                    settings = config.validate({})
                    settings["endpoints"].extend([endpoint, {**endpoint, "name": "other-mimo", "model": "xiaomi/mimo-v2.5"}])
                    settings["tiers"]["cloud_standard"]["second"] = endpoint["name"]
                    config.validate(settings)
                    settings["tiers"]["cloud_standard"]["primary"] = "other-mimo"
                    config.validate(settings)  # Family metadata never restricts the team.
        # Family recognition never upgrades a provider's text-only model to vision.
        for provider, raw in (("openrouter", model("xiaomi/mimo-v2.5-pro", vision=False)),
                              ("vercel", gateway_model("xiaomi/mimo-v2.5-pro", modalities={"input": ["text"], "output": ["text"]}))):
            endpoint = normalize({"data": [raw]}, provider)[0]["endpoint"]
            self.assertEqual((endpoint["lineage"], endpoint["modality"]), ("mimo", "text"))

    def test_gpt6_choices_keep_the_gpt_family_across_gateways(self):
        for provider, make in (("vercel", gateway_model), ("openrouter", model)):
            for mid in ("openai/gpt-6-luna", "openai/gpt-6-luna-fast", "openai/gpt-6-luna-pro:batch"):
                with self.subTest(provider=provider, model=mid):
                    row = normalize({"data": [make(mid)]}, provider)[0]
                    endpoint = config.endpoint_entry({**row["endpoint"], "name": "gpt6-choice"})
                    self.assertEqual(endpoint["lineage"], "gpt")
                    settings = config.validate({})
                    settings["endpoints"].extend([endpoint, {**endpoint, "name": "gpt5-choice", "model": "openai/gpt-5.6-luna"}])
                    settings["tiers"]["cloud_standard"]["second"] = endpoint["name"]
                    config.validate(settings)
                    settings["tiers"]["cloud_standard"]["primary"] = "gpt5-choice"
                    config.validate(settings)  # Family metadata never restricts the team.

    def test_real_catalogue_shape_capabilities_and_independence(self):
        rows = normalize({"data": [gateway_model(), gateway_model("anthropic/claude-test", temperature=False,
                    reasoning_options=[{"type": "effort", "values": ["low", "high", "max"]}]),
                    gateway_model("alibaba/qwen-3-14b"), gateway_model("lab/embedding", type="embedding")]}, "vercel")
        endpoints = {row["id"]: config.endpoint_entry({**row["endpoint"], "name": "gateway-test"}) for row in rows}
        qwen = endpoints["alibaba/qwen3.8-flash"]
        self.assertEqual((qwen["base_url"], qwen["key_env"]), (config.VERCEL_GATEWAY, "AI_GATEWAY_API_KEY"))
        self.assertEqual((qwen["price_in"], qwen["price_out"]), (.15, .47))
        self.assertEqual((qwen["modality"], qwen["structured"]), ("vision", "json_schema"))
        self.assertEqual(config.reasoning_choices(qwen), ["off", "low", "medium", "xhigh"])
        self.assertEqual(config.reasoning_choices(endpoints["anthropic/claude-test"]), ["low", "high", "max"])
        self.assertEqual(endpoints["alibaba/qwen-3-14b"]["lineage"], family("qwen/qwen3-14b"))
        self.assertEqual(len(endpoints), 3)

    def test_caches_and_failed_refreshes_cannot_mix_providers(self):
        cache = ModelCatalog()
        def download(url, **kwargs):
            self.assertNotIn("headers", kwargs)
            return {"data": [gateway_model()]} if url == config.VERCEL_GATEWAY + "/models" else {"data": [model()]}
        with patch("grading.model_catalog.request_json", side_effect=download) as fetch:
            router = cache.listing()
            vercel = cache.listing(provider="vercel")
            self.assertEqual(cache.listing(), router)
            self.assertEqual(cache.listing(provider="vercel"), vercel)
            self.assertEqual(fetch.call_count, 2)
        with patch("grading.model_catalog.request_json", side_effect=ProviderError("unavailable")):
            stale = cache.listing(provider="vercel", refresh=True)
            self.assertEqual(stale["models"], vercel["models"])
            self.assertTrue(stale["stale"])
            self.assertIn("Vercel", stale["notice"])
            self.assertEqual(cache.listing(), router)
        with self.assertRaises(ValueError):
            cache.listing(provider="https://untrusted.example")

    def test_gateway_wire_contract_uses_its_key_schema_images_and_host_routing(self):
        endpoint = {**normalize({"data": [gateway_model()]}, "vercel")[0]["endpoint"], "name": "test", "upstream": "alibaba"}
        request = {"prompt": "grade", "payload": {"rubric": {"items": [{"id": "point"}]}}, "images": [b"synthetic"]}
        provider = HostedEndpoint(endpoint)
        with patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "synthetic-gateway", "OPENROUTER_API_KEY": "synthetic-router"}):
            self.assertEqual(provider._headers(), {"Authorization": "Bearer synthetic-gateway"})
        body, _ = provider.body(request)
        self.assertNotIn("provider", body)
        self.assertNotIn("usage", body)
        self.assertEqual(body["providerOptions"], {"gateway": {"order": ["alibaba"], "only": ["alibaba"]}})
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        provider.endpoint = {**endpoint, "reasoning_choice": "xhigh", "temperature_supported": False}
        body, metadata = provider.body(request)
        self.assertEqual(body["reasoning"], {"effort": "xhigh", "exclude": False})
        self.assertNotIn("temperature", body)
        self.assertIsNone(metadata["temperature"])
        self.assertGreater(body["max_tokens"], endpoint["max_tokens"])
        provider.endpoint = {**endpoint, "reasoning_choice": "on", "reasoning_controls": ["toggle"]}
        self.assertEqual(provider.body(request)[0]["reasoning"], {"enabled": True, "exclude": False})

    def test_new_metadata_is_optional_for_legacy_frozen_connections(self):
        legacy = copy.deepcopy(config.ENDPOINTS[0])
        clean = config.endpoint_entry(legacy)
        for key in ("reasoning_controls", "temperature_supported"):
            self.assertNotIn(key, clean)
        for key, invalid in (("temperature_supported", "false"), ("reasoning_controls", ["unknown"])):
            with self.assertRaises(ValueError):
                config.endpoint_entry({**legacy, key: invalid})

    def test_gateway_submit_retains_refusal_recovery_and_parses_reported_usage(self):
        endpoint = {**normalize({"data": [gateway_model()]}, "vercel")[0]["endpoint"], "name": "gateway-submit-test"}
        provider = HostedEndpoint(endpoint)
        raw = {"id": "synthetic-receipt", "model": endpoint["model"], "choices": [
            {"finish_reason": "stop", "message": {"content": '{"ok":true}', "reasoning_content": "Synthetic reasoning."}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": .001}}
        refusal = ProviderError("Reasoning required", status=400, confirmed=True,
                                confirmation={"body": "Reasoning is mandatory and cannot be disabled"})
        try:
            with patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "synthetic-only"}), patch(
                    "grading.providers.cloud.request_stream", side_effect=[refusal, raw]) as send:  # Hosted answers are streamed (2026-09-23).
                result = provider.submit({"prompt": "grade", "payload": {"rubric": {"items": []}},
                                          "schema": {"type": "object"}})
                self.assertEqual(send.call_count, 2)
                self.assertEqual(send.call_args.args, (config.VERCEL_GATEWAY + "/chat/completions",))
                self.assertNotEqual(send.call_args.kwargs["body"]["reasoning"], {"enabled": False})
                self.assertEqual(send.call_args.kwargs["headers"], {"Authorization": "Bearer synthetic-only"})
                self.assertIs(send.call_args.kwargs["usage_option"], True)  # Vercel is asked for the usage chunk that carries the cost.
                self.assertEqual(result["result"]["output"], {"ok": True})
                self.assertEqual(provider.cost(result["result"]["metadata"]["usage"]), (.001, "reported by the host"))
        finally:
            provider.hint_refused.pop(provider._route(), None)


class GatewayApiTests(CatalogueApiTests):
    def test_provider_parameter_is_allowlisted_and_discovery_never_saves(self):
        before = copy.deepcopy(self.frq.settings())
        client = self.app.test_client()
        with patch("grading.model_catalog.request_json", return_value={"data": [gateway_model()]}) as fetch:
            response = client.get("/api/grading/model-catalog?provider=vercel")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["provider"], "vercel")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(fetch.call_args.args, (config.VERCEL_GATEWAY + "/models",))
            self.assertEqual(client.get("/api/grading/model-catalog?provider=unknown").status_code, 400)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.frq.settings(), before)


for _name in [n for n in vars(CatalogueApiTests) if n.startswith("test_") and n not in vars(GatewayApiTests)]:
    setattr(GatewayApiTests, _name, None)  # CatalogueApiTests' own tests run in test_grading_model_catalog
del _name


del CatalogueApiTests  # its own tests run in test_grading_model_catalog
