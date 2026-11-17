"""A rejected batch refreshes its preview without starting work or losing choices."""
import copy
import json
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from flask import Response
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests
from rosters import connect

fixture = FrqWorkflowTests('test_end_to_end_idempotency_and_export')
fixture.setUp()
aid = fixture.plan['id']
application = fixture.app.wsgi_app
fail_preview = False
preview_override = None


def preview_failure(environ, start_response):
    if fail_preview and environ['PATH_INFO'].endswith('/preview'):
        return Response('{"error":"Synthetic preview unavailable."}', status=503,
                        mimetype='application/json')(environ, start_response)
    if preview_override is not None and environ['PATH_INFO'].endswith('/preview'):
        return Response(json.dumps(preview_override), mimetype='application/json')(environ, start_response)
    return application(environ, start_response)


def change_settings():
    settings = fixture.frq.settings()
    settings['settings']['timeout'] += 1
    fixture.frq.save_settings(settings)


def jobs():
    with connect(fixture.path) as db:
        return [dict(row) for row in db.execute('SELECT id,status FROM frq_jobs')]


fixture.app.wsgi_app = preview_failure
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(15000)
        commands, errors = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: commands.append(request.post_data_json)
                if request.method == 'POST' and request.url.endswith(f'/assignments/{aid}/jobs') else None)
        page.goto(address + f'/#grading/{aid}?tab=jobs')
        page.get_by_role('button', name='Grade new responses', exact=True).click()
        preview = page.locator('[data-batch-preview]')
        start = page.get_by_role('button', name='Start grading batch', exact=True)
        expect(preview).to_contain_text('1 response eligible')

        # Reproduce the instructor's large batch using synthetic preview data only. Its cost is information: nothing refuses it.
        preview_override = copy.deepcopy(fixture.frq.preview(aid, {'mode': 'local', 'question_ids': [fixture.qid]}))
        preview_override['eligible'] *= 2183
        preview_override['existing_count'] = 417
        preview_override['skipped'] = [{'reason': 'Select a current reviewed submission before grading.'}] * 64
        preview_override['estimate'] = {'estimated_cost': 91.97, 'basis': 'Synthetic configured prices.',
                                        'roles': {'primary': {'endpoint': 'synthetic-grader'}}}
        page.get_by_label('Grading method', exact=True).select_option('cloud_standard')
        expect(preview).to_contain_text('2,183 responses eligible')
        expect(preview).to_contain_text('$91.97')
        expect(preview).not_to_contain_text('spending cap')
        expect(start).to_be_enabled()
        expect(page.locator('[data-estimate-details]')).not_to_have_attribute('open', '')
        expect(page.locator('[data-blocked-responses]')).not_to_have_attribute('open', '')
        expect(page.locator('[data-regrading-options]')).not_to_have_attribute('open', '')
        expect(page.locator('[data-method-help]')).not_to_contain_text('Jev')
        assert not commands and jobs() == []
        output = Path('test-results')
        output.mkdir(exist_ok=True)
        # One appearance, desktop only (DESIGN-LANGUAGE): a desktop window and a short laptop screen.
        for width, height in ((1440, 900), (1280, 620)):
            page.set_viewport_size({'width': width, 'height': height})
            body = page.locator('#grading-dialog > .dialog-body')
            body.evaluate('el => el.scrollTop = 0')
            expect(start).to_be_in_viewport()
            expect(page.locator('#grading-dialog > .dialog-header')).to_be_in_viewport()
            assert page.locator('#dialog').evaluate('el => el.scrollWidth <= el.clientWidth + 2')
            page.screenshot(path=str(output / f'batch-preview-{width}x{height}.png'))
            # Disclosures scroll only the body; the start action remains available.
            page.locator('[data-batch-sources] > summary').click()
            body.evaluate('el => el.scrollTop = el.scrollHeight')
            expect(start).to_be_in_viewport()
            page.locator('[data-batch-sources] > summary').click()
        page.set_viewport_size({'width': 1440, 'height': 1000})

        # An unknown cost is said, never a reason to refuse (2026-09-23: no spending cap in Faculty); rescoring needs no model.
        preview_override['estimate']['estimated_cost'] = None
        page.get_by_role('button', name='Refresh preview', exact=True).click()
        expect(preview).to_contain_text('Not available (no prices set)')
        expect(start).to_be_enabled()
        page.locator('[data-regrading-options] > summary').click()
        page.get_by_label('Recompute', exact=True).select_option('rescore')
        expect(preview).to_contain_text('No model calls')
        expect(start).to_be_enabled()
        page.get_by_label('Recompute', exact=True).select_option('recognize')
        preview_override['estimate']['estimated_cost'] = 12.50
        page.get_by_role('button', name='Refresh preview', exact=True).click()
        expect(start).to_be_enabled()
        page.get_by_label('Papers per question').fill('0')
        expect(start).to_be_disabled()
        expect(preview).to_contain_text('Enter a whole number')
        page.get_by_label('Papers per question').fill('2')
        expect(start).to_be_enabled()
        preview_override['eligible'] = []
        page.get_by_role('button', name='Refresh preview', exact=True).click()
        expect(start).to_be_disabled()
        expect(page.locator('[data-blocked-responses]')).to_have_attribute('open', '')
        assert not commands and jobs() == []
        # The models the batch would use are one visible line; changing them closes the batch (nothing started) for Compute settings.
        expect(page.locator('[data-batch-models]')).to_contain_text('Models · primary: synthetic-grader')
        page.get_by_role('button', name='Change models…', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(page.get_by_role('dialog', name='Compute settings', exact=True)).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog', name='Compute settings', exact=True)).not_to_be_visible()
        assert not commands and jobs() == []
        preview_override = None
        page.get_by_role('button', name='Grade new responses', exact=True).click()
        page.get_by_label('Grading method', exact=True).select_option('local')
        page.get_by_label('Papers per question').fill('1')
        page.get_by_role('button', name='Refresh preview', exact=True).click()
        preview = page.locator('[data-batch-preview]')
        start = page.get_by_role('button', name='Start grading batch', exact=True)
        expect(preview).to_contain_text('1 response eligible')
        expect(start).to_be_enabled()
        change_settings()
        start.click()
        expect(preview).to_contain_text('Check the refreshed preview before starting again.')
        expect(start).to_be_enabled()
        assert len(commands) == 1 and jobs() == []
        expect(page.locator('#grading-dialog .form-error')).not_to_be_visible()
        expect(page.get_by_label('Grading method', exact=True)).to_have_value('local')
        expect(page.get_by_label('Papers per question')).to_have_value('1')
        # Only the instructor's second click creates the batch, using the new preview and request ID.
        start.click()
        expect(page.locator('#dialog')).not_to_be_visible()
        assert len(commands) == 2 and len(jobs()) == 1
        assert commands[0]['fingerprint'] != commands[1]['fingerprint']
        assert commands[0]['request_id'] != commands[1]['request_id']
        job = jobs()[0]
        assert fixture.frq.job(job['id'])['profile']['timeout'] == fixture.frq.settings()['settings']['timeout']
        fixture.frq.cancel_job(job['id'])

        # If refreshing fails, the old fingerprint cannot be reused and Start stays disabled.
        page.get_by_role('button', name='Grade new responses', exact=True).click()
        page.get_by_label('Responses', exact=True).select_option('all')
        page.get_by_label('Grading method', exact=True).select_option('local')
        expect(preview).to_contain_text('1 response eligible')
        expect(start).to_be_enabled()
        change_settings()
        fail_preview = True
        start.click()
        expect(preview).to_contain_text('Synthetic preview unavailable.')
        expect(start).to_be_disabled()
        assert len(commands) == 3 and len(jobs()) == 1
        fail_preview = False
        page.get_by_role('button', name='Refresh preview', exact=True).click()
        expect(preview).to_contain_text('1 response eligible')
        expect(start).to_be_enabled()
        expect(preview).not_to_contain_text('Synthetic preview unavailable.')
        assert len(commands) == 3
        page.keyboard.press('Escape')

        # Once the response is graded, "New" finds nothing and says why with the way on; grading it again warns before Start that a
        # new grade replaces the current one, and Start stays enabled (grading-review-13, grading-review-4).
        graded, _ = fixture.queue()
        fixture.finish(graded)
        page.get_by_role('button', name='Grade new responses', exact=True).click()
        page.get_by_label('Grading method', exact=True).select_option('local')
        expect(preview).to_contain_text('Every response was already graded (1).')
        expect(start).to_be_disabled()
        expect(start).to_have_attribute('title', 'Every response was already graded (1). To grade them again, choose “All eligible, including regrading” under Responses.')
        expect(page.locator('[data-regrade-warning]')).to_have_count(0)
        preview.get_by_role('button', name='Grade them again instead', exact=True).click()
        expect(page.get_by_label('Responses', exact=True)).to_have_value('all')
        expect(preview).to_contain_text('1 response eligible')
        warning = page.locator('[data-regrade-warning]')
        expect(warning.locator('.callout-title')).to_have_text('This response was graded before.')
        expect(warning).to_contain_text('It is graded again.')
        expect(warning).to_contain_text('your own decisions included')
        expect(start).to_be_enabled()
        page.set_viewport_size({'width': 1440, 'height': 900})
        expect(warning).to_be_visible()
        assert page.locator('#dialog').evaluate('el => el.scrollWidth <= el.clientWidth + 2')
        page.screenshot(path=str(output / 'batch-preview-regrade-1440.png'))
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.keyboard.press('Escape')
        assert len(commands) == 3
        assert not errors, errors
        browser.close()
        print('Batch preview passed: settings conflict, fresh preview, preserved choices, explicit resubmission, failed refresh and manual recovery.')
finally:
    server.close()
    fixture.doCleanups()
