"""A Claude subscription connection chosen for a grader, saved, edited (responses per call) and shown without prices or keys. Browse
lists what each plan's program names now (the fake `claude`, `codex` and `agy`), and the built-in list when it cannot say. The Gemini
subscription is chosen the same way, for the Assistant too, and is not offered for the coding agent."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests
import fake_agy
import fake_claude
import fake_codex

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
programs = tempfile.TemporaryDirectory()
claude_binary, claude_control = fake_claude.install(programs.name, REPO / "tests")
codex_binary, codex_control = fake_codex.install(programs.name, REPO / "tests")
for home, sign_in in (("claude-home", ".credentials.json"), ("codex-home", "auth.json")):
    (Path(programs.name) / home).mkdir()
    (Path(programs.name) / home / sign_in).write_text("{}")
agy_binary, agy_control = fake_agy.install(programs.name, REPO / "tests")
agy_home = Path(programs.name) / "antigravity-home"  # Faculty's own folder for agy: the app makes it, the instructor signs in there once
os.environ.update(FACULTY_CLAUDE_CLI=claude_binary, FACULTY_CODEX_CLI=codex_binary, CLAUDE_CONFIG_DIR=str(Path(programs.name) / "claude-home"),
                  CODEX_HOME=str(Path(programs.name) / "codex-home"), FACULTY_ANTIGRAVITY_CLI=agy_binary, FACULTY_ANTIGRAVITY_HOME=str(agy_home))
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
assert (agy_home / ".faculty-antigravity-home").is_file(), "the app makes and marks its folder for agy"
(agy_home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text("a sign-in Faculty never reads")
start = fixture.frq.settings()
fixture.frq.save_settings({"version": start["version"], "settings": {**start["settings"], "default_tier": "cloud_standard"}})
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
        page.get_by_role('button', name='Compute settings', exact=True).click()
        expect(popup.get_by_role('tabpanel', name='Defaults', exact=True)).to_be_visible()
        field = lambda name: popup.locator(f'[name="{name}"]')
        primary = 'tier-cloud_standard-primary'
        field('provider-' + primary).select_option('claude')
        browse = popup.locator(f'[data-model-field="{primary}"]').get_by_role('button')
        expect(browse).to_have_text('Browse Claude subscription')
        browse.click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text('Claude models on your subscription, as Claude Code lists them now')
        expect(picker.locator('.model-picker-row', has_text='Claude Sonnet 5.5')).to_be_visible()
        expect(picker.locator('input[name="catalog-model"][value="claude-sonnet-4-6"]')).to_have_count(1)
        expect(picker.locator('input[name="catalog-model"][value="gpt-oops"]')).to_have_count(0)
        # Refresh asks again; a program that cannot answer leaves the built-in list and says why.
        claude_control.write_text(json.dumps({"script": "", "listing": "exit"}))
        refresh = picker.get_by_role('button', name='Refresh catalogue', exact=True)
        expect(refresh).to_be_visible()
        refresh.click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text("Claude Code stopped before naming its models (exit 3). Showing Faculty's built-in list of your Claude plan's models.")
        expect(picker.locator('input[name="catalog-model"][value="claude-sonnet-4-6"]')).to_have_count(0)
        expect(picker.locator('input[name="catalog-model"][value="claude-sonnet-5-5"]')).to_have_count(1)
        claude_control.write_text(json.dumps({"script": ""}))
        refresh.click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text('as Claude Code lists them now')
        expect(picker.locator('input[name="catalog-model"][value="claude-sonnet-4-6"]')).to_have_count(1)
        picker.locator('input[name="catalog-model"][value="claude-sonnet-5"]').check()
        expect(picker.locator('.model-picker-row').filter(has=page.locator('input[value="claude-sonnet-5"]'))).to_contain_text('Subscription')
        picker.get_by_role('button', name='Use model', exact=True).click()
        expect(field(primary)).to_have_value('subscription-claude-sonnet-5')
        expect(popup.locator(f'[data-model-price="{primary}"]')).to_contain_text('Included in your Claude subscription')
        # Responses per call sits beside the model, like a gateway's host, and belongs to the connection.
        bundle = field('bundle-' + primary)
        expect(bundle).to_have_value('6')
        bundle.select_option('3')
        expect(popup.locator(f'[data-model-price="{primary}"]')).to_contain_text('3 responses per call')
        bundle.select_option('6')
        fallback = field('api-fallback-' + primary)
        expect(fallback).not_to_be_checked()
        fallback.check()
        expect(popup.locator('[data-settings-status]')).to_have_text('Unsaved changes')
        for width in (1440, 1280):
            page.set_viewport_size({'width': width, 'height': 1000})
            fallback.scroll_into_view_if_needed()
            page.screenshot(path=str(OUTPUT / f'claude-api-fallback-{width}.png'), animations='disabled')
        # Its concurrency is a typed number beside it (1 to 256); a number out of range is not taken.
        concurrency = field('sessions-' + primary)
        assert (concurrency.get_attribute('type'), concurrency.get_attribute('min'), concurrency.get_attribute('max')) == ('number', '1', '256')
        expect(concurrency).to_have_value('2')
        concurrency.fill('8'); concurrency.dispatch_event('change')
        expect(popup.locator(f'[data-host-slot="{primary}"]')).to_contain_text('8 calls at once')
        field('sessions-' + primary).fill('0'); field('sessions-' + primary).dispatch_event('change')
        expect(popup.locator(f'[data-host-slot="{primary}"]')).to_contain_text('8 calls at once')
        field('sessions-' + primary).fill('900'); field('sessions-' + primary).dispatch_event('change')
        expect(popup.locator(f'[data-host-slot="{primary}"]')).to_contain_text('256 calls at once')
        expect(field('sessions-' + primary)).to_have_value('256')
        field('sessions-' + primary).fill('2'); field('sessions-' + primary).dispatch_event('change')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        saved = fixture.frq.settings()['settings']
        endpoint = next(e for e in saved['endpoints'] if e['name'] == 'subscription-claude-sonnet-5')
        assert (endpoint['kind'], endpoint['base_url'], endpoint['key_env'], endpoint['bundle_size'], endpoint['sessions'], endpoint['max_inflight']) == ('agent_sdk', '', '', 6, 2, 12), endpoint
        assert saved['tiers']['cloud_standard']['primary'] == 'subscription-claude-sonnet-5'
        assert endpoint['api_fallback'] is True
        # Its editor asks only for what a subscription connection has: the model, responses per call and calls at once.
        popup.get_by_role('tab', name='Connections', exact=True).click()
        card = popup.locator('.compute-connection', has_text='subscription-claude-sonnet-5')
        expect(card).to_contain_text('Claude subscription · 6 responses per call · 2 at once')
        card.get_by_role('button', name='Edit subscription-claude-sonnet-5').click()
        expect(picker.get_by_label('Base URL')).to_have_count(0)
        expect(picker.get_by_label('Key variable on the server')).to_have_count(0)
        expect(picker.get_by_label('Use direct Anthropic API when the subscription limit is reached')).to_be_checked()
        picker.get_by_label('Responses per call').fill('4')
        picker.get_by_role('button', name='Apply to settings', exact=True).click()
        expect(card).to_contain_text('4 responses per call')
        page.screenshot(path=str(OUTPUT / 'grading-subscription-connection.png'), animations='disabled')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        endpoint = next(e for e in fixture.frq.settings()['settings']['endpoints'] if e['name'] == 'subscription-claude-sonnet-5')
        assert (endpoint['bundle_size'], endpoint['max_inflight']) == (4, 8), endpoint
        # The choice beside the model shows what the editor saved, and a change there saves too.
        popup.get_by_role('tab', name='Defaults', exact=True).click()
        expect(field('api-fallback-' + primary)).to_be_checked()
        expect(field('bundle-' + primary)).to_have_value('4')
        field('bundle-' + primary).select_option('2')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        endpoint = next(e for e in fixture.frq.settings()['settings']['endpoints'] if e['name'] == 'subscription-claude-sonnet-5')
        assert (endpoint['bundle_size'], endpoint['max_inflight']) == (2, 4), endpoint
        field('sessions-' + primary).fill('40'); field('sessions-' + primary).dispatch_event('change')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        endpoint = next(e for e in fixture.frq.settings()['settings']['endpoints'] if e['name'] == 'subscription-claude-sonnet-5')
        assert (endpoint['sessions'], endpoint['max_inflight']) == (40, 80), endpoint
        # The connection editor offers the same typed number.
        popup.get_by_role('tab', name='Connections', exact=True).click()
        popup.locator('.compute-connection', has_text='subscription-claude-sonnet-5').get_by_role('button', name='Edit subscription-claude-sonnet-5').click()
        editor = picker.get_by_label('Concurrency (calls at once)')
        expect(editor).to_have_value('40')
        assert (editor.evaluate('el => el.tagName'), editor.get_attribute('type'), editor.get_attribute('max')) == ('INPUT', 'number', '256')
        picker.get_by_role('button', name='Close', exact=True).click()
        popup.get_by_role('tab', name='Defaults', exact=True).click()
        # An API connection has the same choice: one by default and not stored, a larger number saved on the connection.
        from grading import config
        current = fixture.frq.settings()
        api = {**config.ENDPOINT, "name": "api-grader", "base_url": config.AGGREGATOR, "key_env": "OPENROUTER_API_KEY", "model": "qwen/qwen3-vl-32b-instruct", "aggregator": True}
        fixture.frq.save_settings({"version": current["version"], "settings": {**current["settings"], "endpoints": [*current["settings"]["endpoints"], api],
                                   "tiers": {**current["settings"]["tiers"], "cloud_standard": {**current["settings"]["tiers"]["cloud_standard"], "second": "api-grader"}}}})
        page.reload()
        page.get_by_role('button', name='Compute settings', exact=True).click()
        second = 'tier-cloud_standard-second'
        expect(field('bundle-' + second)).to_have_value('1')
        field('bundle-' + second).select_option('5')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        assert next(e for e in fixture.frq.settings()['settings']['endpoints'] if e['name'] == 'api-grader')['bundle_size'] == 5
        field('bundle-' + second).select_option('1')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        assert 'bundle_size' not in next(e for e in fixture.frq.settings()['settings']['endpoints'] if e['name'] == 'api-grader')
        # The ChatGPT subscription is chosen the same way, with its own models and the same two dropdowns.
        field('provider-' + second).select_option('chatgpt')
        browse = popup.locator(f'[data-model-field="{second}"]').get_by_role('button')
        expect(browse).to_have_text('Browse ChatGPT subscription')
        browse.click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text('ChatGPT models on your subscription, as Codex lists them now')
        expect(picker.locator('.model-picker-row', has_text='GPT-6.1-Sol')).to_be_visible()
        expect(picker.locator('input[name="catalog-model"][value="gpt-6-internal"]')).to_have_count(0)
        picker.locator('input[name="catalog-model"][value="gpt-6-astra"]').check()
        picker.get_by_role('button', name='Use model', exact=True).click()
        expect(field(second)).to_have_value('chatgpt-gpt-6-astra')
        expect(popup.locator(f'[data-model-price="{second}"]')).to_contain_text('Included in your ChatGPT subscription')
        expect(field('sessions-' + second)).to_have_value('2')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        chatgpt = next(e for e in fixture.frq.settings()['settings']['endpoints'] if e['name'] == 'chatgpt-gpt-6-astra')
        assert (chatgpt['kind'], chatgpt['base_url'], chatgpt['key_env'], chatgpt['reasoning_options']['efforts']) == ('codex_sdk', '', '', ['low', 'medium', 'high', 'xhigh', 'max']), chatgpt
        # "Next Cloud batch uses" names every option a batch would use with each role, in place of the connection's test state.
        summary = popup.locator('[data-team-summary] table')
        expect(summary.locator('thead')).to_contain_text('Settings')
        expect(summary.locator('thead')).not_to_contain_text('Connection')
        primary_row, second_row = summary.locator('[data-team-role="primary"]'), summary.locator('[data-team-role="second"]')
        for text in ('Claude subscription', 'Responses per call', 'Concurrency', '40 calls at once', 'No API billing', "The model's own maximum"):
            expect(primary_row).to_contain_text(text)
        for text in ('ChatGPT subscription', '2 calls at once'):
            expect(second_row).to_contain_text(text)
        expect(summary).not_to_contain_text('Not tested')
        summary.screenshot(path=str(OUTPUT / 'grading-team-settings.png'), animations='disabled')
        # A change of reasoning is reflected at once.
        popup.locator(f'[data-reasoning-slot="{second}"] select').select_option('high')
        expect(second_row).to_contain_text('High effort')
        expect(second_row).not_to_contain_text('High effort (default)')
        # The rubric model's requests are never bundled, so it shows no such choice.
        popup.get_by_role('tab', name='Other models', exact=True).click()
        field('provider-rubric_reviewer').select_option('claude')
        expect(field('bundle-rubric_reviewer')).to_have_count(0)
        # The Gemini subscription is chosen the same way: the Google plan's models as Antigravity lists them, one row per model with
        # its reasoning levels (agy names one per level), and no row for a role the model cannot do (GPT-OSS reads no pictures).
        verifier = 'tier-cloud_standard-verifier'
        popup.get_by_role('tab', name='Defaults', exact=True).click()
        field('provider-' + verifier).select_option('gemini')
        browse = popup.locator(f'[data-model-field="{verifier}"]').get_by_role('button')
        expect(browse).to_have_text('Browse Gemini subscription')
        browse.click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text('Models on your Gemini subscription, as Antigravity lists them now')
        expect(picker.locator('.model-picker-row', has_text='Gemini 4 Argon')).to_be_visible()
        expect(picker.locator('.model-picker-row', has_text='Claude Sonnet 5.5')).to_be_visible()
        expect(picker.locator('input[name="catalog-model"][value="gemini-4-argon-high"]')).to_have_count(0)
        expect(picker.locator('input[name="catalog-model"][value="gpt-oss-120b"]')).to_have_count(0)
        picker.get_by_label('Show incompatible models').check()
        expect(picker.locator('.model-picker-row', has_text='GPT-OSS 120B')).to_contain_text('This role needs a model that can see images.')
        # Signed out, Browse says how to sign in (in Faculty's own folder) and shows the built-in list.
        token = agy_home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        token.unlink()
        picker.get_by_role('button', name='Refresh catalogue', exact=True).click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text(f"Antigravity is not signed in for Faculty: run `HOME={agy_home} agy` once in a terminal")
        expect(picker.locator('[data-catalog-status]')).to_contain_text("Showing Faculty's built-in list of your Gemini plan's models.")
        token.write_text("signed in again")
        picker.get_by_role('button', name='Refresh catalogue', exact=True).click()
        expect(picker.locator('[data-catalog-status]')).to_contain_text('as Antigravity lists them now')
        picker.locator('input[name="catalog-model"][value="gemini-4-argon"]').check()
        expect(picker.locator('.model-picker-row').filter(has=page.locator('input[value="gemini-4-argon"]'))).to_contain_text('Subscription')
        picker.get_by_role('button', name='Use model', exact=True).click()
        expect(field(verifier)).to_have_value('google-gemini-4-argon')
        expect(popup.locator(f'[data-model-price="{verifier}"]')).to_contain_text('Included in your Gemini subscription · 6 responses per call')
        expect(field('sessions-' + verifier)).to_have_value('2')
        assert [o.get_attribute('value') for o in popup.locator(f'[data-reasoning-slot="{verifier}"] select option').all()] == ['low', 'medium', 'high']
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        gemini = next(e for e in fixture.frq.settings()['settings']['endpoints'] if e['name'] == 'google-gemini-4-argon')
        assert (gemini['kind'], gemini['base_url'], gemini['key_env'], gemini['lineage'], gemini['price_in'], gemini['reasoning_options']['efforts']) == \
            ('antigravity', '', '', 'gemini', 0.0, ['low', 'medium', 'high']), gemini
        expect(summary.locator('[data-team-role="verifier"]')).to_contain_text('Gemini subscription')
        popup.get_by_role('tab', name='Connections', exact=True).click()
        card = popup.locator('.compute-connection', has_text='google-gemini-4-argon')
        expect(card).to_contain_text('gemini-4-argon · Gemini subscription · 6 responses per call · 2 at once')
        card.get_by_role('button', name='Edit google-gemini-4-argon').click()
        expect(picker.get_by_label('Model on the Google plan')).to_have_value('gemini-4-argon')
        expect(picker.get_by_label('Base URL')).to_have_count(0)
        expect(picker).to_contain_text('Runs on your Gemini subscription through Antigravity on the server, with no API billing.')
        page.screenshot(path=str(OUTPUT / 'grading-gemini-subscription.png'), animations='disabled')
        picker.get_by_role('button', name='Close', exact=True).click()
        # It drafts and reviews rubrics like any connection and runs the Assistant; only the coding agent is not offered it. Those
        # models are under Other models (2026-10-03).
        popup.get_by_role('tab', name='Other models', exact=True).click()
        providers = lambda name: [o.get_attribute('value') for o in field('provider-' + name).locator('option').all()]
        assert 'gemini' in providers('rubric_reviewer') and 'gemini' in providers(primary)
        assert 'gemini' not in providers('coding_model') and {'claude', 'chatgpt', 'openrouter'} <= set(providers('coding_model')), providers('coding_model')
        field('provider-assistant_model').select_option('gemini')
        expect(field('assistant_model').locator('option', has_text='google-gemini-4-argon')).to_have_count(1)
        field('assistant_model').select_option('google-gemini-4-argon')
        popup.get_by_role('button', name='Save compute settings', exact=True).click()
        expect(popup.locator('[data-settings-status]')).to_have_text('All changes saved')
        assert fixture.frq.settings()['settings']['assistant_model'] == 'google-gemini-4-argon'
        status = page.evaluate("fetch('/api/assistant/status').then(r => r.json())")
        assert (status['available'], status['model'], status['engine']) == (True, 'google-gemini-4-argon', 'gemini'), status
        expect(popup.locator('details.compute-why', has_text='Which models')).to_contain_text('your Gemini subscription (run by Antigravity)')
        # Nothing ran a model for any of this: the fake agy was only asked for its list.
        assert all(run.get('listing') for run in map(json.loads, Path(str(agy_control) + '.log').read_text().splitlines()))
        assert not errors, errors
        browser.close()
        print('Subscription settings passed: chosen for a grader, saved without URL, key or price, bundle size edited and saved.')
finally:
    server.close()
    fixture.doCleanups()
    programs.cleanup()
