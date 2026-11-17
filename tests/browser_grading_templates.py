"""Generated and uploaded references, warning cancellation, and crop handoff."""
import json
import sys
import re
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / 'tests')]
from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from rosters import connect
from test_grading_templates import AssignmentTemplateWorkflow
from assignment_templates import read_verified

fixture = AssignmentTemplateWorkflow('test_uploaded_reference_waits_for_warning_before_any_reading')
fixture.setUp()
with connect(fixture.path) as db:
    row = dict(db.execute('SELECT * FROM assignment_versions WHERE id=?', (fixture.version_id,)).fetchone())
    row.update(id=str(uuid.uuid4()), label='Printed elsewhere', page_count=1, compiler_json=json.dumps({'generator': 'faculty-stamps-only'}))
    db.execute('INSERT INTO assignment_versions VALUES(' + ','.join('?' for _ in row) + ')', tuple(row.values()))
fixture.ready_batch(row['id'])
fixture.upload(assignment_version_id=fixture.version_id)
fixture.upload(assignment_version_id=row['id'])
# Only the original fixture's intentionally synthetic canonical lacks retained
# bytes. Converted references and uploaded files use the real storage API.
fixture.enterContext(patch('assignment_templates.read_verified', side_effect=lambda root, path, checksum:
    b'Synthetic canonical PDF' if path == 'canonical.pdf' else read_verified(root, path, checksum)))
server = create_server(fixture.app, host='127.0.0.1', port=0)
threading.Thread(target=server.run, daemon=True).start()
address = f'http://127.0.0.1:{server.effective_port}'
output = Path('test-results'); output.mkdir(exist_ok=True)
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1050})
        page.set_default_timeout(15000)
        errors, bad = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('response', lambda response: bad.append((response.status, response.url)) if response.status >= 400 else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=templates")
        expect(page.get_by_role('heading', name='Assignment Templates', exact=True)).to_be_visible()
        expect(page.get_by_label('Template for A', exact=True)).not_to_have_value('')
        external = page.locator('[data-template-slot]').filter(has=page.get_by_role('heading', name='Printed elsewhere', exact=True))
        # Not usable yet and not the step's primary: the header leaves it out instead of showing a grey button (review fix GS-D9).
        expect(page.get_by_role('button', name='Continue to answer areas')).to_have_count(0)
        external.locator('input[type=file]').set_input_files({'name': 'blank-scanner.pdf', 'mimeType': 'application/pdf', 'buffer': b'Synthetic blank scan'})
        external.get_by_role('button', name='Upload template', exact=True).click()
        expect(page.get_by_role('button', name='Continue to answer areas')).to_be_enabled()
        assert fixture.reader.reads == 0
        assert not fixture.frq.process_location_one()
        # One appearance, desktop only (DESIGN-LANGUAGE): the page never scrolls sideways at its desktop width.
        page.screenshot(path=str(output / 'assignment-templates-1440.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.get_by_role('button', name='Continue to answer areas').click()
        expect(page.locator('#dialog')).to_contain_text('Digital templates may cause reduced performance')
        expect(page.locator('#dialog')).to_contain_text('same scanner')
        page.locator('#dialog').get_by_role('button', name='Close', exact=True).click()
        assert not fixture.frq.process_location_one()
        expect(page.get_by_label('Template for Printed elsewhere', exact=True)).not_to_have_value('')
        page.get_by_role('button', name='Continue to answer areas').click()
        page.get_by_role('button', name='Confirm and continue', exact=True).click()
        expect(page.get_by_role('heading', name='Answer areas', exact=True)).to_be_visible()
        for _ in range(30):
            fixture.frq.process_location_one()
        expect(page.locator('[data-location-open]')).to_have_count(2)
        expect(page.get_by_role('button', name='Review answer area', exact=True)).to_have_count(1)
        page.get_by_role('button', name='Review answer area', exact=True).first.click()
        expect(page.locator('[data-location-page="1"] .grading-highlight')).to_have_count(1)
        page.get_by_label('I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages').check()
        page.get_by_role('button', name='Confirm answer area', exact=True).click()
        expect(page.locator('#dialog')).not_to_be_visible()
        assert any(q['location_check']['status'] == 'confirmed' for q in fixture.uploaded_questions())
        # A finished first step (2026-09-26 overhaul): a summary, its tools folded away, and Continue in the step header that goes
        # straight on when no template changed, writing no new template receipt.
        writes = []
        page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' and request.url.endswith('/templates/confirm') else None)
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics")
        header = page.locator('[data-step-header="rubrics"]')
        expect(header.get_by_role('heading', name='Rubrics & Templates', exact=True)).to_be_visible()
        confirm_review = header.get_by_role('button', name='Confirm all for rubric review', exact=True)
        if confirm_review.count():
            confirm_review.click()
            expect(confirm_review).to_have_count(0)
        expect(page.locator('[data-setup-summary]')).to_contain_text(re.compile(r'\d+rubrics? confirmed for review'))
        expect(page.locator('[data-setup-summary]')).to_contain_text('version templates confirmed')
        tools = page.locator('[data-setup-tools]')
        expect(tools).not_to_have_attribute('open', '')
        expect(page.get_by_role('heading', name='Assignment Templates', exact=True)).to_be_hidden()
        expect(page.locator('[data-rubric-next]')).to_have_count(0)
        onward = header.get_by_role('button', name='Continue to answer areas', exact=True)
        expect(onward).to_have_class(re.compile(r'\bprimary\b'))
        expect(page.get_by_role('button', name='Continue to answer areas', exact=True)).to_have_count(1)
        page.screenshot(path=str(output / 'assignment-templates-settled.png'), full_page=True)
        onward.click()
        expect(page.get_by_role('heading', name='Answer areas', exact=True)).to_be_visible()
        assert 'tab=locations' in page.url
        expect(page.locator('#dialog')).not_to_be_visible()
        assert writes == [], writes
        # A link asking for the templates opens the folded tools.
        page.goto(address + f"/#grading/{fixture.plan['id']}?tab=rubrics&focus=templates")
        expect(page.locator('[data-setup-tools]')).to_have_attribute('open', '')
        expect(page.get_by_role('heading', name='Assignment Templates', exact=True)).to_be_visible()
        assert not errors, errors
        assert not bad, bad
        browser.close()
        print('Assignment Templates passed: automatic canonical import, external scan upload, mandatory warning, cancellation, crop checks and the desktop layout.')
finally:
    server.close()
    fixture.doCleanups()
