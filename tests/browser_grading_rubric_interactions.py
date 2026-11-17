"""Rubric disclosure clicks cannot change grade selections or collapse from the body."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_teams import GradingTeams
from test_grading_pipeline import CORRECT

fixture = GradingTeams('test_three_distinct_credit_bearing_selections_require_review')
fixture.setUp()
response, _ = fixture.grade(CORRECT, script=fixture.opinions(**{'beta-text': ('R2',), 'gamma-vision': ('R3',)}))
aid, rid = fixture.plan['id'], response['id']
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
output = Path('test-results')
output.mkdir(exist_ok=True)
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1100})
        errors, writes = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: writes.append(request.url) if request.method != 'GET' else None)
        page.goto(f'{address}/#grading/{aid}?tab=review&browse=all&response={rid}')
        row = page.locator('.grading-rubric-disputed').filter(has=page.locator('[data-evidence-item]')).first
        expect(row).to_be_visible()
        checkbox = row.locator('input[name="item"]')
        details = row.locator('details')
        heading = details.locator(':scope > summary')
        initial = checkbox.is_checked()
        initial_score = page.locator('[data-score]').inner_text()
        before = heading.bounding_box()
        toggle = heading.locator('.grading-rubric-toggle')
        assert toggle.evaluate("el => getComputedStyle(el, '::before').content") == '"+"'
        # A disputed line is the steady highlighted row (DESIGN-LANGUAGE: --accent-soft), never an animated mark.
        accent_soft = page.evaluate("""() => { const probe = document.createElement('span'); probe.style.color = 'var(--accent-soft)';
          document.body.append(probe); const value = getComputedStyle(probe).color; probe.remove(); return value; }""")
        assert row.evaluate("el => getComputedStyle(el).backgroundColor") == accent_soft
        assert row.evaluate("el => getComputedStyle(el).animationName") == 'none'
        # The whole text/number heading opens the details; it is no checkbox label.
        heading.locator('.grading-rubric-number').click()
        expect(details).to_have_attribute('open', '')
        assert toggle.evaluate("el => getComputedStyle(el, '::before').content") == '"−"'
        assert checkbox.is_checked() == initial
        after = heading.bounding_box()
        assert abs(before['width'] - after['width']) < 1 and abs(before['height'] - after['height']) < 1
        details.locator(':scope > p').first.click()
        expect(details).to_have_attribute('open', '')
        evidence = details.locator('[data-evidence-item]').first
        expect(evidence).to_be_visible()
        evidence.click()
        expect(details).to_have_attribute('open', '')
        # Space and Enter use the native disclosure; neither changes a checkbox.
        heading.focus()
        page.keyboard.press('Space')
        expect(details).not_to_have_attribute('open', '')
        assert toggle.evaluate("el => getComputedStyle(el, '::before').content") == '"+"'
        page.keyboard.press('Enter')
        expect(details).to_have_attribute('open', '')
        heading.locator('.grading-rubric-item-text').click()
        expect(details).not_to_have_attribute('open', '')
        assert checkbox.is_checked() == initial
        assert page.locator('[data-score]').inner_text() == initial_score
        assert not writes, writes
        # Only the checkbox requests a new score, without opening or closing details.
        for opened in (False, True):
            if opened:
                heading.click()
            for _ in range(2):
                with page.expect_response(lambda result: result.url.endswith('/score') and result.request.method == 'POST'):
                    checkbox.click()
                assert details.evaluate('el => el.open') == opened
            assert checkbox.is_checked() == initial
        expect(page.locator('[data-score]')).to_have_text(initial_score)
        assert len(writes) == 4 and all(url.endswith('/score') for url in writes), writes
        assert fixture.frq.response(rid)['review'] is None
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): two desktop widths.
        for width in (1500, 1280):
            page.set_viewport_size({'width': width, 'height': 1000})
            expect(heading).to_be_visible()
            before = heading.bounding_box()
            heading.click()
            expect(details).not_to_have_attribute('open', '')
            heading.click()
            expect(details).to_have_attribute('open', '')
            assert abs(before['height'] - heading.bounding_box()['height']) < 1
            assert checkbox.is_checked() == initial
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            row.screenshot(path=str(output / f'rubric-disclosure-{width}.png'))
        page.emulate_media(reduced_motion='reduce')
        assert row.evaluate("el => getComputedStyle(el).animationName") == 'none'
        assert row.evaluate("el => getComputedStyle(el).backgroundColor") == accent_soft
        # Forced colours drop the highlight's paper, so a system-colour outline keeps the disputed line apart.
        page.emulate_media(forced_colors='active')
        assert row.evaluate("el => getComputedStyle(el).outlineStyle") == 'solid'
        assert not errors, errors
        browser.close()
        print('Rubric interactions passed: separate checkbox, fixed disclosure target, body clicks, keyboard, steady disputed highlight, reduced motion, forced colors and desktop widths.')
finally:
    server.close()
    fixture.doCleanups()
