"""Provider switching on every cloud model field, safe drafts, separate catalogues and the desktop layout."""
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
from test_grading_model_catalog import model
from test_grading_gateway import gateway_model

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
# Cloud is the saved scheme, so the Defaults page carries its model selectors under the picker.
start = fixture.frq.settings()
fixture.frq.save_settings({"version": start["version"], "settings": {**start["settings"], "default_tier": "cloud_standard"}})
before = fixture.frq.settings()
def download(url, **kwargs):
    if url == config.VERCEL_GATEWAY + "/models":
        return {"data": [gateway_model(), gateway_model("xiaomi/mimo-v2.6-flash"), gateway_model("openai/gpt-6-luna"), gateway_model("openai/gpt-6-luna-fast"), gateway_model("anthropic/claude-test", temperature=False,
                    reasoning_options=[{"type": "effort", "values": ["low", "high"]}])]}
    assert url == config.AGGREGATOR + "/models", url
    return {"data": [model("anthropic/claude-router-only")]}
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
        popup, picker = page.locator('#grading-settings-dialog'), page.locator('#dialog')
        def open_teams():
            page.get_by_role('button', name='Compute settings', exact=True).click()
            expect(popup.get_by_role('tabpanel', name='Defaults', exact=True)).to_be_visible()
        open_teams()
        field = lambda name: popup.locator(f'[name="{name}"]')
        row = lambda name: popup.locator(f'[data-model-field="{name}"]')
        provider = lambda name: field('provider-' + name)
        option = lambda mid: picker.locator(f'input[name="catalog-model"][value="{mid}"]')
        values = lambda select: select.locator('option').evaluate_all('options => options.map(o => o.value)')
        fetch.assert_not_called()
        # Every model selector has a provider control, including folded fallback and benchmark fields.
        names = popup.locator('[data-model-field]').evaluate_all('rows => rows.map(r => r.dataset.modelField)')
        assert 'rubric_reviewer' in names and 'yardstick' in names
        assert any(name.startswith('fallback-') for name in names)
        for name in names:
            # The Gemini subscription (2026-10-14) runs every role but the coding agent (`runsRole`).
            gemini = [] if name == 'coding_model' else ['gemini']
            assert values(provider(name)) == ['openrouter', 'vercel', 'claude', 'chatgpt', *gemini, 'custom'], (name, values(provider(name)))
        primary, second, fallback = 'tier-cloud_standard-primary', 'tier-cloud_standard-second', 'fallback-cloud_standard-primary'
        original_primary = field(primary).input_value()
        provider(primary).select_option('vercel')
        expect(row(primary).get_by_role('button')).to_have_text('Browse Vercel AI Gateway')
        expect(field(primary).locator('option')).to_have_count(1)
        assert not field(primary).evaluate('el => el.checkValidity()')
        provider(primary).select_option('openrouter')
        expect(field(primary)).to_have_value(original_primary)
        assert fixture.frq.settings() == before
        # Browse only fetches the selected provider and choosing changes exactly this role.
        provider(second).select_option('vercel')
        row(second).get_by_role('button').click()
        expect(option('anthropic/claude-test')).to_be_visible()
        expect(option('anthropic/claude-router-only')).to_have_count(0)
        expect(option('alibaba/qwen3.8-flash')).to_be_enabled()  # Same-family choices work across gateways.
        option('anthropic/claude-test').check()
        picker.get_by_role('button', name='Use model', exact=True).click()
        chosen = 'vercel-anthropic-claude-test'
        expect(field(second)).to_have_value(chosen)
        expect(provider(second)).to_have_value('vercel')
        assert values(field('reasoning-' + second)) == ['low', 'high']
        assert fixture.frq.settings() == before
        # Switch away and back restores each provider's last draft selection.
        provider(second).select_option('openrouter')
        expect(field(second)).to_have_value(before['settings']['tiers']['cloud_standard']['second'])
        provider(second).select_option('vercel')
        expect(field(second)).to_have_value(chosen)
        assert values(field(second)) == ['', chosen]
        # Fallback Browse assigns the fallback, not its primary grader.
        popup.locator(f'[data-compute-fold="{fallback}"] > summary').click()
        provider(fallback).select_option('vercel')
        row(fallback).get_by_role('button').click()
        option('alibaba/qwen3.8-flash').check()
        picker.get_by_role('button', name='Use model', exact=True).click()
        expect(field(fallback)).to_have_value('vercel-alibaba-qwen3.8-flash')
        expect(field(primary)).to_have_value(original_primary)
        # Rubric drafting and the benchmark are Other models, beside the grading team's own section (2026-10-03).
        popup.get_by_role('tab', name='Other models', exact=True).click()
        provider('rubric_reviewer').select_option('vercel')
        expect(popup.locator('[data-model-price="rubric_reviewer"]')).to_be_hidden()
        expect(popup.locator('[data-reasoning-slot="rubric_reviewer"]')).to_be_empty()
        field('rubric_reviewer').select_option(chosen)
        provider('yardstick').select_option('vercel')
        field('yardstick').select_option(chosen)
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        saved = fixture.frq.settings()
        assert saved['settings']['tiers']['cloud_standard']['second'] == chosen
        assert saved['settings']['tiers']['cloud_standard']['primary'] == original_primary
        assert saved['settings']['role_fallbacks']['cloud_standard']['primary'] == 'vercel-alibaba-qwen3.8-flash'
        assert saved['settings']['rubric_reviewer'] == saved['settings']['yardstick'] == chosen
        assert saved['settings']['endpoints'][-2]['key_env'] == 'AI_GATEWAY_API_KEY'
        page.reload()
        open_teams()
        expect(provider(second)).to_have_value('vercel')
        expect(provider('rubric_reviewer')).to_have_value('vercel')
        # The old flex row passed overflow checks while shrinking the model to zero.
        # Check actual control widths and label overlap, including nested fallbacks,
        # long Vercel button text, a narrow desktop window and the screenshot's desktop width
        # (desktop only, DESIGN-LANGUAGE: nothing below 1180 px is laid out any more).
        popup.get_by_role('tab', name='Defaults', exact=True).click()
        popup.locator('[data-compute-panel="defaults"] details').evaluate_all('els => els.forEach(el => el.open = true)')
        for width in (1500, 1221):
            page.set_viewport_size({'width': width, 'height': 1100})
            row(second).scroll_into_view_if_needed()
            assert popup.evaluate('el => el.scrollWidth <= el.clientWidth')
            issues = popup.locator('[data-model-field]').evaluate_all('''rows => rows.flatMap(row => {
                if (!row.checkVisibility()) return [];
                const problems = [], model = row.querySelector(`[name="${row.dataset.modelField}"]`);
                if (model.getBoundingClientRect().width < Math.min(240, row.clientWidth) - 2)
                    problems.push(`${row.dataset.modelField}: model is too narrow`);
                const fields = [...row.querySelectorAll('.field, button')].filter(el => el.checkVisibility());
                for (const [index, field] of fields.entries()) {
                    const a = field.getBoundingClientRect();
                    if (field.scrollWidth > field.clientWidth + 1) problems.push(`${row.dataset.modelField}: clipped label`);
                    for (const other of fields.slice(index + 1)) {
                        const b = other.getBoundingClientRect();
                        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
                            Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)
                            problems.push(`${row.dataset.modelField}: overlapping controls`);
                    }
                }
                return problems;
            })''')
            assert not issues, (width, issues)
            # Save has its own space outside the scroller, including at the end.
            popup.locator('.compute-layout').evaluate('el => el.scrollTop = el.scrollHeight')
            body, footer = popup.locator('.compute-layout').bounding_box(), popup.locator('.compute-savebar').bounding_box()
            assert body['y'] + body['height'] <= footer['y'] + 1, (width, body, footer)
            assert footer['y'] + footer['height'] <= 1100, (width, footer)
            popup.get_by_role('tab', name='Connections', exact=True).click()
            popup.get_by_role('tab', name='Defaults', exact=True).click()
            assert popup.locator('.compute-layout').evaluate('el => el.scrollTop') == 0
            popup.locator('[data-compute-panel="defaults"] details').evaluate_all('els => els.forEach(el => el.open = false)')
            page.screenshot(path=str(OUTPUT / f'gateway-{width}.png'), animations='disabled')
            popup.locator('[data-compute-panel="defaults"] details').evaluate_all('els => els.forEach(el => el.open = true)')
        page.set_viewport_size({'width': 1500, 'height': 1100})
        popup.get_by_role('tab', name='Connections', exact=True).click()
        field('provider-connections').select_option('vercel')
        popup.get_by_role('button', name='Browse Vercel AI Gateway', exact=True).click()
        expect(option('alibaba/qwen3.8-flash')).to_be_visible()
        picker.get_by_role('button', name='Close', exact=True).click()
        field('provider-connections').select_option('openrouter')
        popup.get_by_role('button', name='Browse OpenRouter', exact=True).click()
        expect(option('anthropic/claude-router-only')).to_be_visible()
        expect(option('alibaba/qwen3.8-flash')).to_have_count(0)
        assert fetch.call_count == 2
        assert fixture.frq.settings() == saved
        # GPT-6 is a known GPT family, so both Luna variants enable Use model
        # without an offscreen manual family field (the reported trial failure).
        picker.get_by_role('button', name='Close', exact=True).click()
        popup.get_by_role('tab', name='Defaults', exact=True).click()
        flag = 'tier-cloud_standard-flag_reviewer'
        provider(flag).select_option('vercel')
        row(flag).get_by_role('button').click()
        for mid in ('openai/gpt-6-luna', 'openai/gpt-6-luna-fast'):
            option(mid).check()
            expect(picker.get_by_role('button', name='Use model', exact=True)).to_be_enabled()
            expect(picker.get_by_label('Base model family', exact=True)).to_have_count(0)
        picker.get_by_role('button', name='Use model', exact=True).click()
        expect(field(flag)).to_have_value('vercel-openai-gpt-6-luna-fast')
        assert fixture.frq.settings() == saved
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        final = fixture.frq.settings()['settings']
        assert final['tiers']['cloud_standard']['flag_reviewer'] == 'vercel-openai-gpt-6-luna-fast'
        assert next(e for e in final['endpoints'] if e['name'] == 'vercel-openai-gpt-6-luna-fast')['lineage'] == 'gpt'
        # The Vercel MiMo choice applies, saves, and survives reopening the settings.
        row(flag).get_by_role('button').click()
        picker.get_by_label('Search models', exact=True).fill('xiaomi')
        option('xiaomi/mimo-v2.6-flash').check()
        expect(picker.get_by_label('Base model family', exact=True)).to_have_count(0)
        expect(picker.get_by_role('button', name='Use model', exact=True)).to_be_enabled()
        picker.get_by_role('button', name='Use model', exact=True).click()
        expect(field(flag)).to_have_value('vercel-xiaomi-mimo-v2.6-flash')
        assert fixture.frq.settings()['settings'] == final
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        mimo = fixture.frq.settings()['settings']
        assert next(e for e in mimo['endpoints'] if e['name'] == 'vercel-xiaomi-mimo-v2.6-flash')['lineage'] == 'mimo'
        page.reload()
        open_teams()
        expect(field(flag)).to_have_value('vercel-xiaomi-mimo-v2.6-flash')
        assert not errors, errors
        browser.close()
        print('Gateway settings passed: provider filtering, unrestricted families, drafts, fallback assignment, save/reload, caches and desktop widths.')
finally:
    server.close()
    patch.stopall()
    fixture.doCleanups()
