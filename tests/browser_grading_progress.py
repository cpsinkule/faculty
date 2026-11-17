"""Retained grading work advances tile counts and partial reviews survive reload."""
import re
import sys
import threading
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_grading_intake import GradingIntakeTests
from grading.providers.base import ProviderError

fixture = GradingIntakeTests('test_tile_setup_projection_and_current_export')
fixture.setUp()
fixture.frq.sync_scan(fixture.inspected())
plan = fixture.frq.assignment(fixture.plan['id'])
q = plan['questions'][0]
plan = fixture.frq.save_question(plan['id'], {'version': plan['version'], 'question_key': 'q2',
    'assignment_version_id': q['assignment_version_id'], 'content': {**q['content'], 'title': 'Question 2'}})
second = next(q for q in plan['questions'] if q['question_key'] == 'q2')
plan = fixture.frq.import_rubric(plan['id'], second['question_id'], {'version': second['version'], 'source_text': '-1 Arithmetic error'})
second = next(q for q in plan['questions'] if q['question_key'] == 'q2')
fixture.frq.approve_rubric(plan['id'], second['question_id'], {'version': second['version'],
    'rubric_id': second['rubric_id'], 'sha256': second['rubric']['sha256']})
job, _ = fixture.queue(question_ids=[fixture.qid, second['question_id']])
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
output = Path('test-results'); output.mkdir(exist_ok=True)
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(20000)
        errors, bad = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        # The Grading index is retired (2026-10-18): the step graph is the assignment's own, on its Grade tab.
        steps = address + f'/#grading/{plan["id"]}?tab=jobs'
        page.goto(steps)
        card = page.locator('[data-grading-workflow]')
        grade = card.locator('[data-pipeline-step="grade"]')
        review = card.locator('[data-pipeline-step="review"]')
        expect(grade.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '0')
        expect(grade).to_contain_text('0% · 0/2')
        # The Grade step's batch card reads its batch every 4 s while it waits; a read that changed nothing writes nothing
        # (grading-review-8, FRONTEND "Quiet refresh contract").
        page.goto(address + f'/#grading/{plan["id"]}?tab=jobs')
        batch = page.locator(f'[data-job="{job["id"]}"]')
        expect(batch.locator('[data-job-detail] progress')).to_be_attached()
        reads = []
        page.on('request', lambda request: reads.append(request.url) if request.url.endswith(f'/api/grading/jobs/{job["id"]}') else None)
        page.evaluate("""id => { window.__batchWrites = 0; new MutationObserver(list => { window.__batchWrites += list.length; })
          .observe(document.querySelector(`[data-job="${id}"]`), {subtree: true, childList: true, characterData: true, attributes: true}); }""", job['id'])
        for _ in range(300):
            if len(reads) >= 2:
                break
            page.wait_for_timeout(50)
        assert len(reads) >= 2, reads
        page.wait_for_timeout(300)
        assert page.evaluate('window.__batchWrites') == 0, page.evaluate('window.__batchWrites')
        expect(grade).to_contain_text('0% · 0/2')
        # Finish one real queued task, leaving the other pending; the step graph reads it on the next render.
        for _ in range(30):
            fixture.frq.process_one()
            if any(t['status'] == 'complete' for t in fixture.frq.job(job['id'])['tasks']):
                break
        page.locator('#grading-section > .grading-heading').get_by_role('button', name='Refresh', exact=True).click()
        expect(grade.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '50', timeout=20000)
        expect(grade).to_contain_text('50% · 1/2')
        task = next(t for t in fixture.frq.job(job['id'])['tasks'] if t['status'] == 'complete')
        fixture.approve(fixture.frq.response(task['response_id']))
        page.reload()
        expect(review.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '50')
        expect(review).to_contain_text('50% · 1/2')
        expect(review.locator('.grading-pipeline-fraction')).to_have_text('50% · 1/2')  # The fraction stands in for the step's status.
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.evaluate('document.activeElement?.blur()')
        review.locator('a').focus()
        expect(review.get_by_role('progressbar')).to_be_in_viewport()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.screenshot(path=str(output / 'grading-progress-1440.png'), full_page=True)
        review.locator('a').press('Enter')
        nav = page.get_by_role('navigation', name='Grading workflow')
        expect(nav.locator('[data-pipeline-step="review"] [role="progressbar"]')).to_have_attribute('aria-valuenow', '50')
        expect(nav.locator('[data-pipeline-step="review"] a')).to_have_attribute('aria-current', 'step')
        # A failed task says Failed under the step's label (its colour only repeats the word) and keeps an accessible status.
        with patch.object(fixture.frq, 'execute_stage', side_effect=ProviderError('Synthetic processing failure.')):
            for _ in range(30):
                if not fixture.frq.process_one():
                    break
        page.goto(steps)
        page.reload()
        expect(grade).to_have_class('is-failed')
        expect(grade.locator('.grading-pipeline-status')).to_have_text('Failed')
        expect(grade.get_by_role('progressbar')).to_have_count(0)
        expect(grade.locator('a')).to_have_attribute('aria-label', re.compile(r'^Grade: Failed'))
        fixture.frq.retry_job(job['id'])
        page.reload()
        expect(grade.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '50')
        # A poll that changed something keeps each disclosure as the instructor left it, by name, and the focus where it was. When
        # the batch settles its "Now: …" line goes, and position matching once moved the closed responses disclosure onto the open
        # Batch details one, closing it and dropping focus to the page (FRONTEND "Quiet refresh contract").
        page.goto(address + f'/#grading/{plan["id"]}?tab=jobs')
        batch = page.locator(f'[data-job="{job["id"]}"]')
        expect(batch.locator('.grading-batch-stage')).to_be_visible()
        run_details, responses_details = batch.locator('[data-job-disclosure="run"]'), batch.locator('[data-job-disclosure="responses"]')
        expect(responses_details).not_to_have_attribute('open', '')
        run_details.locator('summary').click()
        expect(run_details).to_have_attribute('open', '')
        run_details.locator('summary').focus()
        fixture.finish(job)
        expect(batch.locator('[data-batch-outcome]')).to_be_visible(timeout=15000)
        expect(batch.locator('.grading-batch-stage')).to_have_count(0)
        expect(run_details).to_have_attribute('open', '')
        expect(responses_details).to_have_count(1)
        expect(responses_details).not_to_have_attribute('open', '')
        assert page.evaluate("document.activeElement?.matches('[data-job-disclosure=\"run\"] > summary') === true")
        page.goto(steps)
        page.reload()
        expect(grade).to_have_class('is-done')
        expect(grade.locator('.grading-pipeline-status')).to_have_text('Done')
        expect(grade.locator('a')).to_have_attribute('aria-label', re.compile(r'^Grade: Done'))
        expect(grade.get_by_role('progressbar')).to_have_count(0)
        expect(review.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '50')
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Progress passed: saved tasks, live polling, partial review, reload, failure, retry, completion, shared navigation and the desktop layout.')
finally:
    server.close()
    fixture.doCleanups()
