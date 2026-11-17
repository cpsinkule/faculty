"""The hosts serving one gateway model, read on demand from the gateway's public listing (2026-09-23)."""
import unittest
from unittest.mock import patch

from grading import config
from grading.model_catalog import ModelCatalog, normalize_hosts
from grading.providers.base import ProviderError


def row(name, tag, *, parameters=("max_tokens", "response_format"), tags=("structured-output", "vision"), uptime=None, quantization="fp8"):
    return {"name": f"{name} | x", "provider_name": name, "tag": tag, "quantization": quantization, "context_length": 131072, "max_completion_tokens": 65536,
            "pricing": {"prompt": "0.000000435", "completion": "0.00000087"}, "supported_parameters": list(parameters), "tags": list(tags),
            **({"uptime_last_30m": uptime} if uptime is not None else {})}


class Hosts(unittest.TestCase):
    def test_each_gateway_saves_what_it_routes_by(self):
        raw = {"data": {"endpoints": [row("Xiaomi", "xiaomi/fp8", uptime=99.9), row("DeepInfra", "deepinfra/fp8", parameters=("max_tokens",), tags=())]}}
        routed = normalize_hosts(raw, "openrouter")
        self.assertEqual([(h["id"], h["name"], h["structured"], h["vision"], h["uptime"]) for h in routed],
                         [("deepinfra/fp8", "DeepInfra", False, None, None), ("xiaomi/fp8", "Xiaomi", True, None, 99.9)])
        self.assertEqual((routed[1]["price_in"], routed[1]["price_out"], routed[1]["quantization"]), (0.435, 0.87, "fp8"))
        # Vercel routes by provider name, and says per host with a tag whether it takes a JSON schema or images.
        gateway = normalize_hosts({"data": {"endpoints": [row("xiaomi", "", parameters=(), quantization=None), row("deepinfra", "", tags=("vision",))]}}, "vercel")
        self.assertEqual([(h["id"], h["structured"], h["vision"]) for h in gateway], [("deepinfra", False, True), ("xiaomi", True, True)])

    def test_the_listing_is_read_on_demand_from_the_fixed_gateway_and_kept_a_while(self):
        catalog = ModelCatalog()
        with patch("grading.model_catalog.request_json", return_value={"data": {"endpoints": [row("xiaomi", "")]}}) as fetch:
            first = catalog.hosts("vercel", "xiaomi/mimo-v2.6-pro")
            again = catalog.hosts("vercel", "xiaomi/mimo-v2.6-pro")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], config.VERCEL_GATEWAY + "/models/xiaomi/mimo-v2.6-pro/endpoints")
        self.assertEqual(([h["id"] for h in first["hosts"]], first["notice"], again), (["xiaomi"], "", first))
        for provider, model in (("elsewhere", "xiaomi/mimo"), ("vercel", "../../etc/passwd"), ("vercel", "xiaomi/../x"), ("openrouter", "no-slash")):
            with self.assertRaises(ValueError):
                catalog.hosts(provider, model)
        with patch("grading.model_catalog.request_json", side_effect=ProviderError("down", confirmed=False)):
            failed = catalog.hosts("openrouter", "xiaomi/mimo-v2.6-pro")
        self.assertEqual(failed["hosts"], [])
        self.assertIn("did not list", failed["notice"])

    def test_the_route_answers_for_the_settings_screen(self):
        from test_frq_grading import FrqWorkflowTests
        fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        client = fixture.app.test_client()
        with patch("grading.model_catalog.request_json", return_value={"data": {"endpoints": [row("Xiaomi", "xiaomi/fp8")]}}):
            found = client.get("/api/grading/model-catalog/hosts?provider=openrouter&model=xiaomi/mimo-v2.6-pro")
        self.assertEqual(found.status_code, 200)
        self.assertEqual([h["id"] for h in found.json["hosts"]], ["xiaomi/fp8"])
        self.assertEqual(client.get("/api/grading/model-catalog/hosts?provider=openrouter&model=bad").status_code, 400)


if __name__ == "__main__":
    unittest.main()
