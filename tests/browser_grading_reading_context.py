"""Layout notes and independent image-reading concerns in the actual review UI."""
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from fake_openai import grade_answer, payload_of
from test_grading_reading_context import CORRECT, ReadingPipeline, scattered

fixture = ReadingPipeline('test_vision_second_sees_crops_and_layout_but_never_the_primarys_decisions')
fixture.setUp()
fixture.vision_second()

def reader(payload):
    value = scattered(payload)
    value['transcript']['layout_relations'][0]['uncertain'] = True
    value['transcript']['reading_alternatives'] = [{'line_id': value['transcript']['lines'][-1]['id'],
        'options': ['x = 6', 'x = 8'], 'description': 'The final digit is ambiguous.'}]
    return value

def second(name, body):
    if name != 'second_vision': return None
    payload = payload_of(body)
    value = grade_answer(payload, {'R1'})
    value['reading_issues'] = [{'line_ids': [payload['transcript']['lines'][1]['id']], 'kind': 'layout',
        'description': 'The margin calculation may belong to a separate attempt.'}]
    return value

fixture.grade(CORRECT, reader=reader, script=second)
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
output = Path('test-results')
output.mkdir(exist_ok=True)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1100})
        errors, bad = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
        page.goto(f'{address}/#grading/{fixture.plan["id"]}?tab=review&browse=all')
        expect(page.locator('.grading-review-form')).to_be_visible()
        context = page.locator('[data-transcript-preview] .grading-transcript-context')
        context.locator('summary').click()
        expect(context).to_contain_text('side calculation · uncertain')
        expect(context).to_contain_text('right-margin calculation')
        expect(context).to_contain_text('The final digit is ambiguous')
        expect(context.locator('.katex')).not_to_have_count(0)
        checks = page.locator('[data-independent-checks]')
        checks.locator('summary').first.click()
        expect(checks).to_contain_text('Second grader · images and transcript')
        expect(checks).to_contain_text('agrees with every proposed line')
        expect(checks).to_contain_text('Second grader’s reading concerns')
        expect(checks).to_contain_text('The margin calculation may belong to a separate attempt.')
        expect(page.locator('.grading-review-reasons')).to_contain_text('Second grader (layout)')
        # One appearance, desktop only (DESIGN-LANGUAGE, 2026-10-03).
        for width in (1500, 1280):
            page.set_viewport_size({'width': width, 'height': 1100})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
            page.screenshot(path=str(output / f'reading-context-{width}.png'), full_page=True)
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Reading context UI passed: layout, rendered alternatives, image-aware second grader, preserved reading concerns and desktop widths.')
finally:
    server.close()
    fixture.doCleanups()
