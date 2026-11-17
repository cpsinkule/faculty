"""Step navigation reads only its assignment; closed batch history stays unloaded; a link followed from the page bar keeps keyboard
focus; a batch that starts elsewhere shows up on the open Grade step once the shell's overview sees grading start, with the
folds the instructor opened, keyboard focus and the scroll kept."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from workspace_steps import open_scan_records, open_step  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_intake import GradingIntakeTests

fixture = GradingIntakeTests('test_step_activity_is_scoped_and_does_not_walk_other_assignments')
fixture.setUp()
old, _ = fixture.queue()
fixture.finish(old)
current, _ = fixture.queue()
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
aid = fixture.plan['id']
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(15000)
        requests, errors = [], []
        page.on('request', lambda request: requests.append(request.url.removeprefix(address)))
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.clock.install()  # Real time, but the test can move it on to the shell's next 30 s refresh.
        page.goto(address + f'/#grading/{aid}?tab=jobs')
        current_card = page.locator(f'[data-job="{current["id"]}"]')
        old_card = page.locator(f'[data-job="{old["id"]}"]')
        expect(current_card.locator('progress')).to_be_visible()
        expect(old_card).not_to_be_visible()
        assert requests.count(f'/api/grading/assignments/{aid}?include=activity') == 1, requests
        assert not any(url.startswith('/api/grading?') for url in requests), requests
        assert f'/api/grading/jobs/{old["id"]}' not in requests, requests
        page.locator('.grading-batch-history > summary').click()
        expect(old_card.locator('progress')).to_be_visible()
        assert requests.count(f'/api/grading/jobs/{old["id"]}') == 1, requests
        page.locator('.grading-batch-history > summary').click()
        page.locator('.grading-batch-history > summary').click()
        expect(old_card.locator('progress')).to_be_visible()
        assert requests.count(f'/api/grading/jobs/{old["id"]}') == 1, requests
        # Another action changes the batch while the instructor visits a different
        # step. Returning must load fresh metadata, without fetching old history.
        page.goto(address + f'/#grading/{aid}?tab=review')
        expect(page.locator('[data-pipeline-step="review"] a')).to_have_attribute('aria-current', 'step')
        fixture.frq.cancel_job(current['id'])
        requests.clear()
        page.goto(address + f'/#grading/{aid}?tab=jobs')
        expect(current_card.locator('.grading-heading .grading-badge')).to_have_text('Cancelled')
        expect(current_card.locator('progress')).to_be_visible()
        assert f'/api/grading/jobs/{old["id"]}' not in requests, requests
        assert not any(url.startswith('/api/grading?') for url in requests), requests
        # Scan records, inside the assignment, is Collect's button (ASSIGNMENT-WORKSPACE "ONE NAVIGATION"); it opens the records.
        open_scan_records(page)
        expect(page.locator('.grading-scan-records h3')).to_contain_text('Scan records for')
        # A batch that starts elsewhere (the automatic starter, another tab) while the Grade step is open: once the shell's overview
        # read sees grading start, the step is drawn again once and shows it (grading-review-7). Nothing is written by the page.
        open_step(page, "jobs")
        expect(current_card.locator('.grading-heading .grading-badge')).to_have_text('Cancelled')
        # The shell's next refresh reads the overview as it is now (the cancelled batch stopped): the step's baseline.
        with page.expect_response(lambda response: '/api/workspace/overview' in response.url):
            page.clock.fast_forward(31000)
        page.wait_for_timeout(1000)
        expect(current_card.locator('.grading-heading .grading-badge')).to_have_text('Cancelled')
        # The redraw is quiet: the instructor opened Earlier batches, is on its summary and scrolled down; all three stay.
        page.set_viewport_size({'width': 1440, 'height': 420})
        history = page.locator('.grading-batch-history')
        page.locator('.grading-batch-history > summary').click()
        expect(history).to_have_attribute('open', '')
        expect(old_card.locator('progress')).to_be_visible()
        page.locator('.grading-batch-history > summary').focus()
        page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        y = page.evaluate('scrollY')
        assert y > 50, y
        newer, _ = fixture.queue()
        newer_card = page.locator(f'[data-job="{newer["id"]}"]')
        expect(newer_card).to_have_count(0)
        writes = []
        page.on('request', lambda request: writes.append(request.url) if request.method not in ('GET', 'HEAD') else None)
        with page.expect_response(lambda response: '/api/workspace/overview' in response.url):
            page.clock.fast_forward(31000)
        expect(newer_card).to_be_attached()
        expect(page.locator('.grading-batch-caption').first).to_contain_text('Latest batch')
        expect(history).to_have_attribute('open', '')
        expect(page.locator('.grading-batch-history > summary')).to_be_focused()
        expect(current_card).to_be_attached()
        assert abs(page.evaluate('scrollY') - y) <= 2, (y, page.evaluate('scrollY'))
        page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(newer_card).to_be_visible()
        assert not writes, writes
        assert not errors, errors
        browser.close()
        print('Navigation passed: one assignment read, on-demand history, no repeated settled reads, fresh batch status after returning.')
finally:
    server.close()
    fixture.doCleanups()
