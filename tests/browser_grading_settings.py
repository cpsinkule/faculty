"""Focused compute preferences preserve drafts and the complete versioned configuration."""
import copy
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests

OUTPUT = Path("test-results")
OUTPUT.mkdir(exist_ok=True)
fixture = FrqWorkflowTests("test_end_to_end_idempotency_and_export")
fixture.setUp()
original = fixture.frq.settings()
server = create_server(fixture.app, host="127.0.0.1", port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f"http://127.0.0.1:{server.effective_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.set_default_timeout(15000)
        errors, reads, writes = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" and not msg.text.startswith("Failed to load resource") else None)
        page.on("request", lambda req: (reads if req.method == "GET" else writes).append(urlsplit(req.url).path))
        page.goto(f"{address}/#grading/{fixture.plan['id']}?tab=rubrics")
        cog = page.get_by_role('button', name='Compute settings', exact=True)
        expect(cog).to_be_visible()
        assert '/api/grading/settings' not in reads and '/api/grading/endpoints' not in reads
        grading_url = page.url
        reads.clear()
        cog.click()
        popup = page.get_by_role('dialog', name='Compute settings', exact=True)
        expect(popup).to_be_visible()
        assert page.url == grading_url
        expect(page.get_by_role("heading", name="Compute settings", exact=True)).to_be_visible()
        expect(page.get_by_role("tabpanel", name="Defaults", exact=True)).to_be_visible()
        expect(page.locator(".grading-workflow")).to_have_count(1)
        assert not any("/api/grading/assignments/" in url for url in reads), reads
        names = page.locator('.grading-settings input:visible').evaluate_all("els => els.map(e => e.name)")
        # One radio per compute mode: Cloud and Fully local (the other schemes were removed on 2026-09-23).
        # Full concurrency took the retired spending cap's place (2026-09-23): Faculty has no cap of its own.
        # Batches start by themselves (2026-09-28): the switch sits under the default mode, on by default.
        # Request limits and each connection's caps are under Advanced, and the rubric check while a quiz is drafted (2026-10-11) sits
        # with the Rubric drafting and review model under Other models (2026-10-03), so Defaults is the mode, its switches and its team.
        assert names == ["default_tier"] * 2 + ["auto_grade", "auto_flag_review", "auto_certify", "auto_gradescope", "glm_enabled", "mathpix_enabled"], names
        expect(page.locator('.grading-settings [name="auto_grade"]')).to_be_checked()
        expect(popup.get_by_label('Check the rubrics while a quiz is drafted', exact=True)).to_be_checked()
        expect(page.locator('.grading-settings [name="full_concurrency"]')).not_to_be_checked()  # Off by default: the configured limits apply.
        # The top of Defaults says which model each Cloud role of the next batch uses, and what is known about its connection.
        summary = page.locator("[data-team-summary] table")
        expect(summary.locator("caption")).to_have_text("Next Cloud batch uses")
        expect(summary.locator("tbody tr")).to_have_count(5)
        expect(summary.locator('[data-team-role="primary"]')).to_contain_text(original['settings']['tiers']['cloud_standard']['primary'] or "No model chosen")
        # Reading without OCR belongs to Cloud, so it is not among the controls above while another mode is chosen.
        expect(page.locator('input[name="direct_reading"]')).to_be_hidden()
        page.screenshot(path=str(OUTPUT / 'compute-initial.png'), full_page=True)
        bar = page.locator('.compute-savebar').bounding_box()
        assert bar['y'] + bar['height'] <= 1100, bar
        expect(page.get_by_role("button", name="Save compute settings")).to_be_disabled()
        expect(page.get_by_role("button", name="Save compute settings")).to_have_css('cursor', 'not-allowed')
        tab = lambda name: page.get_by_role("tab", name=name, exact=False)
        save = lambda: page.get_by_role("button", name="Save compute settings", exact=True).click()
        discard = lambda: page.get_by_role("button", name="Discard changes", exact=True).click()
        field = lambda name: page.locator(f'.grading-settings [name="{name}"]')
        expect(field('escalation_enabled')).to_have_count(0)
        expect(popup.get_by_label('Require a confirmed answer-key match for full credit', exact=True)).to_be_checked()
        expect(popup.get_by_label('Require supporting work for full credit', exact=True)).to_be_checked()
        # Navigation is keyboard accessible, and unvisited settings remain intact.
        tab("Defaults").focus()
        page.keyboard.press("ArrowDown")
        expect(tab("Other models")).to_be_focused()
        expect(page.get_by_role("tabpanel", name="Other models", exact=True)).to_be_visible()
        page.keyboard.press("ArrowDown")
        expect(tab("Connections")).to_be_focused()
        expect(page.get_by_role("tabpanel", name="Connections", exact=True)).to_be_visible()
        tab("Defaults").click()
        # A scheme's own team appears under the picker, in the Defaults panel, as soon as that scheme is picked.
        page.locator('input[name="default_tier"][value="cloud_standard"]').check()
        expect(page.get_by_role("tabpanel", name="Defaults", exact=True).locator('[name="tier-cloud_standard-primary"]')).to_be_visible()
        # The summary's role names open that role's controls.
        page.locator('[data-jump-role="flag_reviewer"]').click()
        expect(field('tier-cloud_standard-flag_reviewer')).to_be_focused()
        expect(field('tier-cloud_standard-flag_reviewer')).to_be_in_viewport()
        # A change keeps the instructor's place (2026-09-28): with scroll anchoring off, as in Safari, ticking Token limits (which
        # hides or shows the caps under it and redraws the summary) leaves the tick where it was on screen, both ways.
        tab("Advanced").click()
        page.evaluate("() => { const sheet = new CSSStyleSheet(); sheet.replaceSync('* { overflow-anchor: none !important; }'); window.__noAnchor = sheet; document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet]; }")
        for _ in range(2):
            moved = page.evaluate("""async () => { const el = document.querySelector('.grading-settings [name="token_limits"]'); el.scrollIntoView({block: 'center'});
                await new Promise(r => requestAnimationFrame(r)); const before = el.getBoundingClientRect().top; el.click();
                await new Promise(r => setTimeout(r, 600)); return Math.abs(el.getBoundingClientRect().top - before); }""")
            assert moved <= 2, f"Token limits moved {moved}px after a change"
        page.evaluate("() => { document.adoptedStyleSheets = document.adoptedStyleSheets.filter(s => s !== window.__noAnchor); }")
        tab("Defaults").click()
        price = lambda name: popup.locator(f'[data-model-price="{name}"]')
        expect(price('tier-cloud_standard-primary')).to_have_text('$0.104 input · $0.416 output / 1M tokens (USD estimate)')
        expect(price('rubric_reviewer')).to_contain_text('Cloud primary grader · $0.104 input')
        expect(price('tier-cloud_standard-verifier')).not_to_be_visible()
        field('tier-cloud_standard-second').select_option('yardstick')
        expect(price('tier-cloud_standard-second')).to_contain_text('$2 input · $10 output')
        expect(summary.locator('[data-team-role="second"]')).to_contain_text('yardstick')  # the summary follows the draft
        field('tier-cloud_standard-second').select_option('')
        expect(price('tier-cloud_standard-second')).not_to_be_visible()
        discard()
        expect(price('tier-cloud_standard-second')).to_contain_text('$0.15 input · $0.6 output')
        expect(field('tier-cloud_standard-primary')).to_be_hidden()  # Discarding restores the picked scheme, and with it what is shown.
        assert '/api/grading/model-catalog' not in reads  # Selected prices never need another network request.
        expect(field("token_limits")).to_be_checked()
        for index, endpoint in enumerate(original['settings']['endpoints']):
            expect(field(f"endpoint-{index}-max_tokens")).to_have_value(str(endpoint['max_tokens']))
            expect(field(f"endpoint-{index}-reasoning_tokens")).to_have_value(str(endpoint['reasoning_tokens']))
        tab("Advanced").click()
        field("endpoint-0-max_tokens").fill("4096")
        field("token_limits").uncheck()
        expect(field("endpoint-0-max_tokens")).to_be_hidden()
        field("token_limits").check()
        expect(field("endpoint-0-max_tokens")).to_have_value("4096")
        field("full_concurrency").check()
        tab("Other models").click()
        field("draft_rubric_check").uncheck()
        expect(page.locator('[data-settings-status]')).to_have_text("Unsaved changes")
        page.keyboard.press('Escape')
        expect(popup).not_to_be_visible()
        expect(cog).to_be_focused()
        cog.click()
        expect(field('full_concurrency')).to_be_checked()
        expect(page.locator('[data-settings-status]')).to_have_text('Unsaved changes')
        page.evaluate("location.hash = '#tools'")
        expect(popup).not_to_be_visible()
        page.evaluate('hash => location.hash = hash', urlsplit(grading_url).fragment)
        expect(cog).to_be_visible()
        cog.click()
        expect(field('full_concurrency')).to_be_checked()
        tab("Advanced").click()
        page.get_by_text("Dedicated GPU provider", exact=True).click()
        raw = '{"small": {"gpu_count": 1}}'
        field("dedicated_profiles").fill(raw)
        tab("Connections").click()
        before = original['settings']['endpoints'][0]
        page.get_by_role("button", name=f"Edit {before['name']}", exact=True).click()
        expect(page.get_by_role("button", name="Remove connection", exact=True)).to_be_disabled()
        page.get_by_label("Connection name", exact=True).fill("renamed-reader")
        page.get_by_text('Prices and request limits', exact=True).click()
        page.get_by_label('$ per million input tokens', exact=True).fill('1.25')
        page.get_by_label('$ per million output tokens', exact=True).fill('2.5')
        page.get_by_role("button", name="Apply to settings", exact=True).click()
        expect(page.locator("#dialog")).not_to_be_visible()
        expect(popup).to_be_visible()
        expect(page.get_by_role("button", name="Test renamed-reader", exact=True)).to_be_disabled()
        # The renamed connection follows into every team's controls, whether or not that team's scheme is on screen.
        expect(page.get_by_label("Handwriting reader", exact=True).first).to_have_value("renamed-reader")
        expect(price('tier-cloud_standard-reader')).to_contain_text('$1.25 input · $2.5 output')
        expect(price('rubric_reviewer')).to_contain_text('Cloud primary grader · $1.25 input')
        tab("Advanced").click()
        expect(field("dedicated_profiles")).to_have_value(raw)
        tab("Defaults").click()
        expect(field("full_concurrency")).to_be_checked()
        # One save updates only the explicitly changed values, including references on rename.
        pending = []
        page.route('**/api/grading/settings', lambda route: pending.append(route), times=1)
        with page.expect_response(lambda r: r.url.endswith('/api/grading/settings') and r.request.method == 'PUT') as result:
            save()
            expect(page.locator('.grading-settings')).to_have_attribute('aria-busy', 'true')
            expect(page.get_by_role('button', name='Saving…', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Saving…', exact=True)).to_have_css('cursor', 'wait')
            expect(field('full_concurrency')).to_be_disabled()
            assert pending
            pending[0].continue_()
        assert result.value.status == 200
        expect(page.locator('[data-settings-status]')).to_have_text("All changes saved")
        expect(page.get_by_role('button', name='Save compute settings', exact=True)).to_be_disabled()
        expect(page.get_by_role('button', name='Save compute settings', exact=True)).to_have_css('cursor', 'not-allowed')
        expect(page.locator('.grading-settings')).not_to_have_attribute('aria-busy', 'true')
        expected = copy.deepcopy(original['settings'])
        expected['full_concurrency'] = True
        expected['draft_rubric_check'] = False
        expected['endpoints'][0]['max_tokens'] = 4096
        expected['dedicated_profiles'] = {'small': {**original['settings']['dedicated_profiles']['small'], 'gpu_count': 1}}
        expected['endpoints'][0]['name'] = 'renamed-reader'
        expected['endpoints'][0].update(price_in=1.25, price_out=2.5)
        for roles in expected['tiers'].values():
            for key, value in roles.items():
                if value == before['name']:
                    roles[key] = 'renamed-reader'
        if expected['yardstick'] == before['name']:
            expected['yardstick'] = 'renamed-reader'
        assert fixture.frq.settings()['settings'] == expected, {key: (value, fixture.frq.settings()['settings'][key]) for key, value in expected.items() if fixture.frq.settings()['settings'][key] != value}
        # A hidden invalid numeric field must be revealed, not silently block Save.
        tab("Advanced").click()
        page.get_by_text("Batch limits and retries", exact=True).click()
        field("timeout").fill("1")
        tab("Defaults").click()
        save()
        expect(tab("Advanced")).to_have_attribute("aria-selected", "true")
        expect(field("timeout")).to_be_focused()
        discard()
        # Raw JSON survives opening/applying a connection, and invalid JSON points to the field.
        field("dedicated_profiles").fill("{not json")
        tab("Connections").click()
        page.get_by_role("button", name="Add connection", exact=True).click()
        dialog = page.locator("#dialog")
        dialog.get_by_label("Connection name", exact=True).fill("temporary")
        dialog.get_by_label("Model ID", exact=True).fill("fixture/model")
        expect(dialog.get_by_label("Model family", exact=True)).to_have_count(0)
        dialog.get_by_role("button", name="Apply to settings", exact=True).click()
        expect(dialog).not_to_be_visible()
        save()
        expect(tab("Advanced")).to_have_attribute("aria-selected", "true")
        expect(field("dedicated_profiles")).to_have_value("{not json")
        expect(page.locator('.compute-savebar [role="alert"]')).to_have_text("Resource profiles must be valid JSON.")
        tab("Connections").click()
        page.get_by_role("button", name="Edit temporary", exact=True).click()
        dialog.get_by_label("Model ID", exact=True).fill("cancelled/model")
        dialog.get_by_role("button", name="Close", exact=True).click()
        expect(page.locator('[data-endpoint]').last).to_contain_text("fixture/model")
        page.get_by_role("button", name="Edit temporary", exact=True).click()
        dialog.get_by_role("button", name="Remove connection", exact=True).click()
        expect(page.get_by_role("button", name="Edit temporary", exact=True)).to_have_count(0)
        discard()
        assert fixture.frq.settings()['settings'] == expected, {key: (value, fixture.frq.settings()['settings'][key]) for key, value in expected.items() if fixture.frq.settings()['settings'][key] != value}
        assert writes.count('/api/grading/settings') == 1, writes
        # Failed saves restore the controls and normal cursor while retaining the draft.
        tab('Advanced').click()
        field('full_concurrency').uncheck()
        page.route('**/api/grading/settings', lambda route: route.fulfill(status=503, json={'error': 'Synthetic save failure'}), times=1)
        save()
        expect(page.locator('.compute-savebar [role="alert"]')).to_have_text('Synthetic save failure')
        expect(page.locator('[data-settings-status]')).to_have_text('Unsaved changes')
        expect(field('full_concurrency')).to_be_enabled()
        expect(field('full_concurrency')).not_to_be_checked()
        expect(page.get_by_role('button', name='Save compute settings', exact=True)).to_be_enabled()
        expect(page.get_by_role('button', name='Save compute settings', exact=True)).not_to_have_css('cursor', 'wait')
        discard()
        assert fixture.frq.settings()['settings'] == expected
        # Desktop widths (the wide window and the narrowest laptop one) keep every section and editor within the viewport.
        for width in (1500, 1280):
            page.set_viewport_size({"width": width, "height": 1100})
            for name in ("Defaults", "Other models", "Connections", "What holds a response back", "Advanced"):
                tab(name).click()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, name)
                assert popup.evaluate('el => el.scrollWidth <= el.clientWidth'), (width, name)
                page.screenshot(path=str(OUTPUT / f"compute-{name.lower().replace(' ', '-')}-{width}.png"), full_page=True)
            tab("Connections").click()
            page.get_by_role("button", name="Edit renamed-reader", exact=True).click()
            expect(dialog.get_by_label("Model ID", exact=True)).to_be_visible()
            assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth"), width
            page.screenshot(path=str(OUTPUT / f"compute-editor-{width}.png"))
            dialog.get_by_role("button", name="Close", exact=True).click()
        popup.get_by_role('button', name='Close compute settings', exact=True).click()
        expect(cog).to_be_focused()
        assert page.url == grading_url
        # Failed popup loads can be retried without replacing the assignment underneath.
        page.route('**/api/grading/settings', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"Synthetic settings failure"}'), times=1)
        cog.click()
        expect(popup.get_by_role('alert')).to_have_text('Synthetic settings failure')
        popup.get_by_role('button', name='Try again', exact=True).click()
        expect(page.get_by_role('tabpanel', name='Defaults', exact=True)).to_be_visible()
        # Existing bookmarks open the same popup and are consumed once.
        page.goto(f'{address}/#grading?tab=settings')
        expect(popup).to_be_visible()
        expect(page.get_by_role('tabpanel', name='Defaults', exact=True)).to_be_visible()
        # The Grading index is retired (2026-10-18): the popup opens over Assignments.
        page.wait_for_function("location.hash.startsWith('#assignments')")
        # Reading without OCR is a Cloud choice: the box appears with that mode and goes away with another. Last,
        # because choosing a mode makes the draft dirty, which the save-bar assertions above are about.
        direct = page.locator('input[name="direct_reading"]')
        expect(direct).to_be_hidden()
        page.locator('input[name="default_tier"][value="cloud_standard"]').check()
        expect(direct).to_be_visible()
        expect(direct).not_to_be_checked()
        context = page.locator('input[name="reading_page_recovery"]')
        expect(context).to_be_visible()
        expect(context).not_to_be_checked()
        page.locator('input[name="default_tier"][value="local"]').check()
        expect(direct).to_be_hidden()
        expect(context).to_be_hidden()
        popup.get_by_role('button', name='Discard changes', exact=True).click()
        popup.get_by_role('button', name='Close compute settings', exact=True).click()
        expect(popup).not_to_be_visible()
        # Opened by a bookmark over Assignments, it has no trigger to return focus to; the page underneath stays Assignments.
        assert page.evaluate("location.hash").startswith("#assignments")
        assert not errors, errors
        browser.close()
        print("Compute settings passed: focused navigation, draft preservation, versioned save, rename references, validation, cancel/discard, desktop widths and no errors.")
finally:
    server.close()
    fixture.doCleanups()
