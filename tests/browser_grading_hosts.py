"""The host serving a gateway model: a list read from the gateway only when opened, saved as the connection's upstream."""
import sys
import threading
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from grading import config
from test_frq_grading import FrqWorkflowTests

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()


def connection(name, base, model, key, **extra):
    return {**config.ENDPOINT, "name": name, "base_url": base, "model": model, "key_env": key, "lineage": "", "aggregator": True, **extra}


def listing(provider, model, rows):
    return {"data": {"id": model, "name": model, "endpoints": rows}}


def host(name, tag, price_in, price_out, structured=True, uptime=None, limit=65536, context=131072):
    return {"name": f"{name} | {tag}", "provider_name": name, "tag": tag, "quantization": "fp8" if "/" in tag else None,
            "pricing": {"prompt": f"{price_in / 1e6:.12f}", "completion": f"{price_out / 1e6:.12f}"}, "context_length": context,
            "max_completion_tokens": limit, "supported_parameters": ["max_tokens", "reasoning"] + (["response_format"] if structured else []),
            "tags": ["reasoning", "vision"] + (["structured-output"] if structured else []), **({"uptime_last_30m": uptime} if uptime else {})}


endpoints = [connection("vercel-mimo", config.VERCEL_GATEWAY, "xiaomi/mimo-v2.6-pro", "AI_GATEWAY_API_KEY", upstream="fireworks", price_in=1.0, price_out=2.0),
             connection("or-deepseek", config.AGGREGATOR, "deepseek/deepseek-v4.1-flash", "OPENROUTER_API_KEY", modality="text")]
start = fixture.frq.settings()
fixture.frq.save_settings({"version": start["version"], "settings": {**start["settings"], "endpoints": [*start["settings"]["endpoints"], *endpoints],
    "default_tier": "cloud_standard", "tiers": {**start["settings"]["tiers"], "cloud_standard": {**start["settings"]["tiers"]["cloud_standard"],
        "second": "or-deepseek", "verifier": "vercel-mimo", "flag_reviewer": "vercel-mimo"}}}})
requested = []


def download(url, **kwargs):
    requested.append(url)
    if url == config.VERCEL_GATEWAY + "/models/xiaomi/mimo-v2.6-pro/endpoints":
        return listing("vercel", "xiaomi/mimo-v2.6-pro", [host("deepinfra", "deepinfra", 0.435, 0.87, limit=1048576, context=1048576),
                                                          host("xiaomi", "xiaomi", 0.4, 0.8, limit=131072, context=1048576),
                                                          host("slowhost", "slowhost", 0.1, 0.2, structured=False)])
    assert url == config.AGGREGATOR + "/models/deepseek/deepseek-v4.1-flash/endpoints", url
    return listing("openrouter", "deepseek/deepseek-v4.1-flash", [host("DeepInfra", "deepinfra/fp8", 0.2, 0.3, uptime=99.5), host("DeepSeek", "deepseek", 0.25, 0.35)])


fetch = patch("grading.model_catalog.request_json", side_effect=download).start()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{address}/#grading/{fixture.plan['id']}?tab=jobs")
        page.get_by_role('button', name='Compute settings', exact=True).click()
        popup = page.locator('#grading-settings-dialog')
        expect(popup.get_by_role('tabpanel', name='Defaults', exact=True)).to_be_visible()
        verifier = popup.locator('[name="host-tier-cloud_standard-verifier"]')
        expect(verifier).to_have_value('fireworks')
        assert not requested, "The settings screen fetches nothing to draw itself"
        # The saved host that the gateway no longer lists stays visible until the list is read.
        expect(popup.locator('[data-host-slot="tier-cloud_standard-verifier"]')).to_contain_text('also used by')
        verifier.focus()
        expect(verifier.locator('option')).to_have_count(5)  # Any host, three listed hosts, and the saved one not listed now.
        verifier = popup.locator('[name="host-tier-cloud_standard-verifier"]')
        options = verifier.locator('option').all_inner_texts()
        assert options[0] == 'Any host (the gateway chooses)', options
        assert any(text.startswith('xiaomi · $0.4 / $0.8 per 1M') for text in options), options
        assert any('slowhost' in text and 'no JSON schema' in text for text in options), options
        assert 'fireworks (not listed now)' in options, options
        assert requested == [config.VERCEL_GATEWAY + "/models/xiaomi/mimo-v2.6-pro/endpoints"], requested
        verifier.select_option('xiaomi')
        # One connection serves both MiMo roles: its host changes for both.
        expect(popup.locator('[name="host-tier-cloud_standard-flag_reviewer"]')).to_have_value('xiaomi')
        verifier.select_option('slowhost')
        expect(popup.locator('[data-host-slot="tier-cloud_standard-verifier"] .grading-model-warning')).to_contain_text('JSON-schema')
        verifier.select_option('xiaomi')
        # The chosen host's published maximum is what a request asks for while token limits are off (2026-09-24).
        expect(popup.locator('[data-host-slot="tier-cloud_standard-verifier"] [data-host-note]')).to_contain_text('up to 131,072 tokens, the most this host accepts')
        verifier.select_option('')
        expect(popup.locator('[data-host-slot="tier-cloud_standard-verifier"] [data-host-note]')).to_contain_text('up to 65,536 tokens, the most every host accepts')
        verifier.select_option('xiaomi')
        limits = popup.locator('[data-answer-limits]')
        expect(limits).to_have_count(0)  # Only said while token limits are off, when the figure is used.
        # The switch is among the request limits under Advanced (2026-10-03); the notice stays with the team under Defaults.
        popup.get_by_role('tab', name='Advanced', exact=True).click()
        popup.locator('[name="token_limits"]').uncheck()
        expect(limits).to_contain_text('Answer limits are not recorded yet for')
        expect(limits).to_contain_text('or-deepseek')  # A role uses it, and no host was chosen for it yet.
        expect(limits).not_to_contain_text('vercel-mimo')  # Its host was just chosen, with the host's figure.
        popup.locator('[name="token_limits"]').check()
        expect(limits).to_have_count(0)
        popup.get_by_role('tab', name='Defaults', exact=True).click()
        second = popup.locator('[name="host-tier-cloud_standard-second"]')
        expect(second).to_have_value('')
        second.focus()
        expect(second.locator('option')).to_have_count(3)
        popup.locator('[name="host-tier-cloud_standard-second"]').select_option('deepinfra/fp8')
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        popup.locator('[data-host-slot="tier-cloud_standard-verifier"]').scroll_into_view_if_needed()
        page.screenshot(path=str(OUTPUT / "grading-hosts-1500.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        saved = {e["name"]: e for e in fixture.frq.settings()["settings"]["endpoints"]}
        assert (saved["vercel-mimo"]["upstream"], saved["vercel-mimo"]["price_in"], saved["vercel-mimo"]["price_out"]) == ("xiaomi", 0.4, 0.8), saved["vercel-mimo"]
        assert (saved["or-deepseek"]["upstream"], saved["or-deepseek"]["price_in"]) == ("deepinfra/fp8", 0.2), saved["or-deepseek"]
        assert (saved["vercel-mimo"]["output_limit"], saved["or-deepseek"]["output_limit"]) == (131072, 65536), saved
        assert len(requested) == 2, requested  # Each list was read once while the screen was open.
        # A late host list belongs to the selected model that requested it. Focus and pointerdown share one pending read.
        page.reload()
        page.get_by_role('button', name='Compute settings', exact=True).click()
        held = []
        page.route('**/api/grading/model-catalog/hosts?*', lambda route: held.append(route))
        verifier = popup.locator('[name="host-tier-cloud_standard-verifier"]')
        with page.expect_request(lambda request: '/api/grading/model-catalog/hosts?' in request.url):
            verifier.focus()
        verifier.dispatch_event('pointerdown', {'button': 0})
        popup.locator('[name="tier-cloud_standard-verifier"]').select_option('')
        expect(popup.locator('[name="host-tier-cloud_standard-verifier"]')).to_have_count(0)
        assert len(held) == 1, 'one pending read per gateway/model'
        with page.expect_response(lambda response: '/api/grading/model-catalog/hosts?' in response.url):
            held[0].fulfill(json={'provider': 'vercel', 'hosts': [], 'any_host_limit': 65536})
        page.evaluate('() => new Promise(requestAnimationFrame)')
        expect(popup.locator('[name="host-tier-cloud_standard-verifier"]')).to_have_count(0)
        assert not errors, errors
        browser.close()
finally:
    server.close()
    fetch.stop()
