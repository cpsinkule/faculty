"""Search, role compatibility, draft-only application, cancellation and offline catalogue."""
import copy
import sys
import threading
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests
from test_grading_model_catalog import model

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
# Cloud Standard is the saved scheme, so the Defaults page carries its team: the picker decides which models are on screen.
saved = fixture.frq.settings()
fixture.frq.save_settings({"version": saved["version"], "settings": {**saved["settings"], "default_tier": "cloud_standard"}})
before = fixture.frq.settings()
catalogue = {"data": [model(), model("qwen/qwen3-new"), model("openai/gpt-oss-new", vision=False),
                      model("xiaomi/mimo-v2.6-flash"), model("xiaomi/mimo-v2.5-pro", vision=False),
                      model("anthropic/claude-new"), model("lab/mystery", name='<img src=x onerror="alert(1)">'),
                      model("lab/unknown-price", pricing={})]}
download = patch("grading.model_catalog.request_json", return_value=catalogue)
fetch = download.start()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append((request.method, request.url)))
        page.goto(f"{address}/#grading/{fixture.plan['id']}?tab=rubrics")
        page.get_by_role('button', name='Compute settings', exact=True).click()
        popup, picker = page.locator('#grading-settings-dialog'), page.locator('#dialog')
        tab = lambda name: popup.get_by_role('tab', name=name, exact=True)
        expect(popup.get_by_role('tabpanel', name='Defaults', exact=True)).to_be_visible()
        fetch.assert_not_called()
        primary = lambda: popup.get_by_label('Primary grader', exact=True).first
        second = lambda: popup.get_by_label('Second grader', exact=True).first
        browse = lambda field: field.locator('xpath=ancestor::div[contains(@class,"compute-model-selector")]').get_by_role('button').click()
        choices = lambda field: field.locator('option').evaluate_all('options => options.map(o => o.value).filter(Boolean)')
        assert 'deepseek-v4.1-flash' not in choices(second())  # An unused preset is available via Browse only.
        assert all(len(choices(field)) <= 5 for field in (primary(), second()))
        expect(popup.locator('option[value="__openrouter_catalog__"]')).to_have_count(0)
        option = lambda mid: picker.locator(f'input[name="catalog-model"][value="{mid}"]')
        search = lambda value: picker.get_by_label('Search models', exact=True).fill(value)
        use = lambda: picker.get_by_role('button', name='Use model', exact=True).click()
        close = lambda: picker.get_by_role('button', name='Close', exact=True).click()
        browse(primary())
        expect(picker.get_by_role('heading', name='Choose a model')).to_be_visible()
        expect(option('deepseek/deepseek-v4.1-flash')).to_be_visible()
        expect(option('openai/gpt-oss-new')).to_have_count(0)  # Text-only cannot be the image grader.
        picker.get_by_label('Show incompatible models', exact=True).check()
        expect(option('openai/gpt-oss-new')).to_be_disabled()
        close()
        expect(primary()).to_have_value(before['settings']['tiers']['cloud_standard']['primary'])
        expect(popup.get_by_role('button', name='Save compute settings', exact=True)).to_be_disabled()
        assert fixture.frq.settings() == before
        # MiMo is selectable without asking the instructor to identify its family.
        browse(primary())
        search('xiaomi')
        expect(option('xiaomi/mimo-v2.5-pro')).to_have_count(0)
        option('xiaomi/mimo-v2.6-flash').check()
        expect(picker.get_by_label('Base model family', exact=True)).to_have_count(0)
        expect(picker.get_by_role('button', name='Use model', exact=True)).to_be_enabled()
        use()
        expect(primary()).to_have_value('xiaomi-mimo-v2.6-flash')
        assert fixture.frq.settings() == before
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        # Existing connections retain every override when chosen from the catalogue.
        browse(second())
        expect(option('qwen/qwen3-new')).to_be_enabled()  # Same family as the primary is allowed.
        search('deepseek')
        option('deepseek/deepseek-v4.1-flash').check()
        use()
        expect(second()).to_have_value('deepseek-v4.1-flash')
        assert fixture.frq.settings() == before  # Applying is still only a draft.
        assert fetch.call_count == 1  # Reopening uses the server cache.
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        # A new model fills its connection and only the selected role; explicit Save persists it.
        browse(second())
        search('claude-new')
        option('anthropic/claude-new').check()
        use()
        expect(second()).to_have_value('anthropic-claude-new')
        price = popup.locator('[data-model-price="tier-cloud_standard-second"]')
        expect(price).to_have_text('$0.15 input · $0.6 output / 1M tokens (USD estimate)')
        expected = copy.deepcopy(before['settings'])
        expected['tiers']['cloud_standard']['second'] = 'anthropic-claude-new'
        expected['recent_models'] = ['anthropic-claude-new', 'qwen3-vl-32b', 'yardstick']  # The replaced second grader is no longer any role's.
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        after = fixture.frq.settings()
        added = after['settings']['endpoints'][-1]
        assert added['model'] == 'anthropic/claude-new' and added['modality'] == 'vision'
        assert added['base_url'] == 'https://openrouter.ai/api/v1' and added['lineage'] == 'claude'
        expected['endpoints'].append(added)
        assert after['settings'] == expected, {k: (after['settings'].get(k), expected.get(k)) for k in {*after['settings'], *expected} if after['settings'].get(k) != expected.get(k)}
        page.reload()
        page.get_by_role('button', name='Compute settings', exact=True).click()
        expect(popup.get_by_role('tabpanel', name='Defaults', exact=True)).to_be_visible()
        assert choices(second()) == expected['recent_models']
        expect(price).to_contain_text('$0.15 input · $0.6 output')
        for width in (1500, 1280):
            page.set_viewport_size({'width': width, 'height': 1000})
            button = second().locator('xpath=ancestor::div[contains(@class,"compute-model-selector")]').get_by_role('button')
            dropdown_box, button_box = second().bounding_box(), button.bounding_box()
            assert button_box['x'] >= dropdown_box['x'] + dropdown_box['width'], width
            assert abs(button_box['y'] - dropdown_box['y']) < 12, width
            assert popup.evaluate('el => el.scrollWidth <= el.clientWidth')
            page.screenshot(path=str(OUTPUT / f'model-recents-{width}.png'))
        page.set_viewport_size({'width': 1500, 'height': 1100})
        # Unrecognized families are selectable without extra input; hostile names remain literal.
        browse(second())
        search('mystery')
        option('lab/mystery').check()
        expect(picker.locator('img')).to_have_count(0)
        expect(picker.get_by_role('button', name='Use model', exact=True)).to_be_enabled()
        expect(picker.get_by_label('Base model family', exact=True)).to_have_count(0)
        with page.expect_response(lambda response: response.url.endswith('/model-catalog?refresh=1')):
            picker.get_by_role('button', name='Refresh catalogue', exact=True).click()
        expect(picker.get_by_role('button', name='Use model', exact=True)).to_be_enabled()
        use()
        expect(second()).to_have_value('lab-mystery')
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        assert fixture.frq.settings() == after
        # Browsing takes the gateway's current prices into saved connections of that gateway, as a draft to save.
        repriced = copy.deepcopy(catalogue)
        repriced['data'][0]['pricing'] = {'prompt': '0.0000002', 'completion': '0.0000009'}
        fetch.return_value = repriced
        browse(second())
        with page.expect_response(lambda response: response.url.endswith('/model-catalog?refresh=1')):
            picker.get_by_role('button', name='Refresh catalogue', exact=True).click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text('Current prices taken for 1 saved connection')
        expect(picker.locator('[data-catalog-status]')).to_contain_text('deepseek-v4.1-flash $0.15 / $0.6 → $0.2 / $0.9')
        # Saved connections are not listed beside the catalogue; the model's own row is marked and shows the current price.
        expect(option('@connection/deepseek-v4.1-flash')).to_have_count(0)
        row = option('deepseek/deepseek-v4.1-flash').locator('xpath=ancestor::label')
        expect(row).to_contain_text('Saved as deepseek-v4.1-flash')
        expect(row.locator('.model-picker-price')).to_have_text('$0.2 / $0.9')
        close()
        expect(popup.get_by_role('button', name='Save compute settings', exact=True)).to_be_enabled()
        assert fixture.frq.settings() == after  # Only a draft until saved.
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        fetch.return_value = catalogue
        with page.expect_response(lambda response: response.url.endswith('/model-catalog?refresh=1')):
            browse(second()); picker.get_by_role('button', name='Refresh catalogue', exact=True).click()
        # The server's cached catalogue still had the new prices when the picker opened; the refresh brings them back.
        expect(picker.locator('[data-catalog-status]')).to_contain_text('deepseek-v4.1-flash $0.2 / $0.9 → $0.15 / $0.6')
        close()
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        # Refresh failure retains the cached list and never changes saved settings.
        browse(second())
        fetch.side_effect = ValueError('synthetic catalogue failure')
        picker.get_by_role('button', name='Refresh catalogue', exact=True).click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text('last downloaded catalogue')
        expect(option('deepseek/deepseek-v4.1-flash')).to_be_visible()
        for width in (1500, 1280):
            page.set_viewport_size({'width': width, 'height': 1000})
            assert picker.evaluate('el => el.scrollWidth <= el.clientWidth'), width
            footer = picker.locator('.dialog-footer').bounding_box()
            assert footer['y'] + footer['height'] <= 1000, (width, footer)
            page.screenshot(path=str(OUTPUT / f'model-catalog-{width}.png'))
        close()
        # Empty offline state has a retry button; closing a delayed request prevents late edits.
        page.set_viewport_size({'width': 1500, 'height': 1100})
        page.route('**/api/grading/model-catalog', lambda route: route.fulfill(json={'models': [], 'checked_at': None, 'stale': True, 'notice': 'OpenRouter could not be reached. Your saved connections are still available.'}), times=1)
        browse(second())
        expect(picker.locator('[data-catalog-status]')).to_contain_text('could not be reached')
        expect(picker.get_by_role('button', name='Refresh catalogue')).to_be_enabled()
        option('@connection/deepseek-v4.1-flash').check()  # With no catalogue, the saved connections are listed instead.
        use()
        expect(second()).to_have_value('deepseek-v4.1-flash')
        assert choices(second())[0] == 'deepseek-v4.1-flash'
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        assert choices(second()) == expected['recent_models']
        assert fixture.frq.settings() == after
        pending = []
        page.route('**/api/grading/model-catalog', lambda route: pending.append(route), times=1)
        browse(second())
        expect(picker.locator('[data-catalog-status]')).to_have_text('Loading OpenRouter catalogue…')
        close()
        expect(picker).not_to_be_visible()
        assert pending
        pending[0].fulfill(json={'models': [], 'checked_at': None, 'stale': True, 'notice': 'Late reply'})
        tab('Connections').click()
        popup.get_by_role('button', name='Browse OpenRouter', exact=True).click()
        expect(picker).to_be_visible()
        expect(option('qwen/qwen3-new')).to_be_visible()  # Connections is not restricted to one role.
        page.evaluate("location.hash = '#tools'")
        expect(picker).not_to_be_visible()
        expect(popup).not_to_be_visible()
        assert fixture.frq.settings() == after
        assert not any('/probe' in url or 'chat/completions' in url for _, url in requests)
        assert not errors, errors
        browser.close()
        print('Model catalogue passed: lazy loading, cache/offline, filters, auto-configuration, unrestricted families, drafts, save, cancel, stale requests and desktop widths.')
finally:
    server.close()
    download.stop()
    fixture.doCleanups()
