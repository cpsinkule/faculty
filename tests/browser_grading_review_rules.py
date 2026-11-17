"""Resolved OCR history, explicit rubric conditions and measured review signals in the real UI."""
import copy
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from workspace_steps import open_step  # ASSIGNMENT-WORKSPACE "ONE NAVIGATION"
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_frq_grading import FrqWorkflowTests
from grading.models import plain_transcript

fixture = FrqWorkflowTests('test_end_to_end_idempotency_and_export')
fixture.setUp()
aid, qid = fixture.plan['id'], fixture.qid
settings = fixture.frq.settings()
fixture.frq.save_settings({'version': settings['version'], 'settings': {**settings['settings'], 'mathpix_enabled': True}})
other = copy.copy(fixture.fake)
other.recognize = lambda image: {'provider': 'mathpix', 'transcript': plain_transcript('3x - 6 = 10', confidence=.97), 'raw': {}, 'metadata': {'model': 'synthetic-reader'}}
fixture.frq.provider = lambda kind, settings: other if kind == 'mathpix' else fixture.fake
question = fixture.frq.assignment(aid)['questions'][0]
plan = fixture.frq.import_rubric(aid, qid, {'version': question['version'], 'source_text': 'Synthetic conditions', 'rules': {'items': [
    {'id': 'R1', 'description': 'Arithmetic error', 'points': -1},
    {'id': 'R2', 'description': 'Additional deduction', 'points': -1, 'exceptions': 'Apply only if R1 is selected.'}]}})
question = plan['questions'][0]
fixture.frq.approve_rubric(aid, qid, {'version': question['version'], 'rubric_id': question['rubric_id'], 'sha256': question['rubric']['sha256']})
old_grade = fixture.fake.grade

def grade(payload, images):
    result = old_grade(payload, images)
    result['output']['applications'].append({'rubric_item_id': 'R2', 'applied': False, 'reason': 'Not applicable.', 'evidence': [], 'confidence': .99, 'origin_error_id': None})
    return result
fixture.fake.grade = grade
value = fixture.finish(fixture.queue(mode='hybrid')[0])
assert value['result']['signals']['reading_resolution']['resolved'] == 1, value['result']
assert value['result']['state'] == 'proposed', value['result']
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
output = Path('test-results')
output.mkdir(exist_ok=True)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{address}/#grading/{aid}?tab=review&browse=all')
        page.locator('[data-independent-checks] > summary').click()
        expect(page.locator('[data-independent-checks]')).to_contain_text('OCR differences resolved')
        expect(page.locator('[data-independent-checks]')).to_contain_text('restriction belongs to an unselected item')
        expect(page.locator('[data-independent-checks]')).to_contain_text('not measured probabilities')
        # The proposal has no review reason of its own; what keeps it from being accepted unseen is said once, open, under its
        # heading (grun:P2), and Independent checks keeps the full list.
        reasons = page.locator('.grading-review-reasons')
        expect(reasons.locator('.callout-title')).to_have_text('Decision needed')
        blocks = page.locator('[data-independent-checks] details').filter(has_text='Why this would not be accepted unseen').locator('li').all_text_contents()
        shown = reasons.locator('li').all_text_contents()
        assert shown and set(shown) <= set(blocks), (shown, blocks)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/review')) as reviewed:
            page.get_by_role('button', name='Approve grade', exact=True).click()
        assert reviewed.value.status == 200, reviewed.value.text()
        expect(page.locator('.grading-response-heading')).to_be_visible()
        page.get_by_text('Regrading and diagnostics', exact=True).click()
        page.get_by_role('button', name='Compare with approved work', exact=True).click()
        report = page.locator('[data-review-signal-report]')
        expect(report).to_contain_text('1 reviewed, 0 changed')
        report.locator('details > summary').first.click()
        expect(report).to_contain_text('0.95–1.00')
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03): the report fits its dialog at two desktop widths.
        for width in (1440, 1280):
            page.set_viewport_size({'width': width, 'height': 1100})
            page.screenshot(path=str(output / f'review-measurements-{width}.png'))
            assert page.locator('#dialog').evaluate('el => el.scrollWidth <= el.clientWidth'), page.locator('#dialog').evaluate('el => [...el.querySelectorAll("*")].filter(x => x.getBoundingClientRect().right > el.getBoundingClientRect().right).map(x => [x.tagName, x.className, x.scrollWidth, x.clientWidth])')
        page.locator('#dialog').get_by_role('button', name='Close', exact=True).first.click()
        page.set_viewport_size({'width': 1440, 'height': 1100})
        open_step(page, "rubrics")
        details = page.locator('.grading-question-details').first
        if not details.evaluate('el => el.open'):
            details.locator('summary').first.click()
        page.get_by_role('button', name='Edit interpretation', exact=True).click()
        dialog = page.locator('#dialog')
        # Wording and points are edited with the rubric lines, where comments and the confirmation for review go with them.
        expect(dialog.get_by_label('Description', exact=True)).to_have_count(0)
        expect(dialog.get_by_label('Points', exact=True)).to_have_count(0)
        row = dialog.locator('.grading-item-editor fieldset').nth(1)
        row.get_by_text('Dependencies and conditions', exact=True).click()
        row.get_by_label('Requires item IDs (comma separated)', exact=True).fill('R1')
        row.get_by_label('The structured rules fully express this condition', exact=True).check()
        page.get_by_role('button', name='Save rubric revision', exact=True).click()
        expect(dialog).not_to_be_visible()
        latest = fixture.frq.assignment(aid)['questions'][0]
        assert latest['rubric']['rules']['items'][1]['condition_handling'] == 'structured'
        assert not latest['rubric']['approved_at']
        assert fixture.frq.response(value['id'])['review']['score']['score'] == 9
        assert not errors, errors
        browser.close()
        print('Review rules UI passed: resolved OCR history, inactive condition, explicit structured rubric revision, observed corrections and desktop widths.')
finally:
    server.close()
    fixture.doCleanups()
