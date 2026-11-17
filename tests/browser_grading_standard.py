"""Current Standard finalization provenance and retired batch controls, using fake models."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_standard import CloudStandard
from fake_openai import flag_review_answer

fixture = CloudStandard()
fixture.setUp()
response, _ = fixture.scripted(review_reasons=['The final digit may need a look.'],
    flags=lambda p: flag_review_answer(p, 'finalize', True))
aid, rid = fixture.plan['id'], response['id']
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.effective_port}/#grading/{aid}?tab=review&browse=all&response={rid}')
        expect(page.get_by_text('Finalized automatically after the flag reviewer found sufficient support', exact=False)).to_be_visible()
        checks = page.locator('[data-independent-checks]')
        checks.locator(':scope > summary').click()
        expect(checks.get_by_text('Flag reviewer', exact=True)).to_be_visible()
        expect(checks.get_by_text('upheld the unchanged standing grade', exact=False)).to_be_visible()
        checks.get_by_text('Review of each flag', exact=True).click()
        expect(checks.get_by_text('Scripted evidence assessment', exact=False).first).to_be_visible()
        page.get_by_text('Ask for another opinion', exact=True).click()
        expect(page.get_by_role('button', name='Regrade with High Assurance')).to_have_count(0)
        expect(page.get_by_role('button', name='Regrade with Cloud')).to_have_count(1)
        page.get_by_role('button', name='Compute settings', exact=True).click()
        popup = page.get_by_role('dialog', name='Compute settings', exact=True)
        popup.locator('[name="default_tier"][value="cloud_standard"]').check()
        expect(popup.get_by_label('Flag reviewer', exact=True)).to_have_value('delta-vision')
        expect(popup.locator('[name="final_arbitrator"]')).to_have_count(0)
        expect(popup.locator('[name="default_tier"][value="cloud_high_assurance"]')).to_have_count(0)
        # One appearance, desktop only (DESIGN-LANGUAGE): in the narrowest desktop window checked the settings still fit.
        page.set_viewport_size({'width': 1280, 'height': 900})
        expect(popup.get_by_label('Flag reviewer', exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not errors, errors
        browser.close()
        print('Cloud: fourth-model finalization, retained flag reasons, new settings and responsive review passed.')
finally:
    server.close()
    fixture.doCleanups()
