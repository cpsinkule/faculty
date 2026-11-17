"""TEACHING-WORKFLOWS "RESOLVED WHEN OPENED": the list leaves a resolved follow-up's drafts out, opening it reads them. Fakes only."""
import json
import logging
import threading
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_followup_suggestions import MAIL, ScriptedEngine, SuggestionsTests, proposing


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = SuggestionsTests()
    fixture.setUp()
    server = None
    try:
        engine_patch = patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine())
        engine_patch.start()
        fixture.addCleanup(engine_patch.stop)
        fixture.suggestions.automatic = True
        fixture.fake_mail()
        fixture.incoming()
        def closing(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': fixture.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'resolved'}, 'Close the conversation'))(c, prompt)
        run, _ = fixture.prepare(closing)
        assert fixture.approve(next(a for a in run['actions'] if a['endpoint'] == 'gmail_mail.send')).status_code == 200
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            reads = []
            page.on('request', lambda request: reads.append(request.url.split('?', 1)[1]) if '/api/student-support?' in request.url else None)
            # The first read of the student fails: the review says so, and Try again reads it.
            page.route('**/api/student-support?*enrollment_id=*', lambda route: route.fulfill(status=503, json={'error': 'Synthetic read failure'}), times=1)
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            expect(root.locator('.support-toolbar')).to_be_visible()
            assert reads[0] == 'kind=followup&suggestions=1&brief=1', reads
            root.locator('[data-support-filter]').select_option('resolved')
            expect(root.locator('.support-queue-row')).to_have_count(1)
            expect(root.locator('.support-queue-status')).to_contain_text('1 done for you')
            expect(root.get_by_text('could not be read: Synthetic read failure', exact=False)).to_be_visible()
            root.locator('[data-support-detail]').click()
            # Opened: the Assistant's work reads as on any other follow-up, with the record of what it did.
            expect(root.get_by_text('Marked this conversation resolved', exact=False)).to_be_attached()
            expect(root.locator('[data-patch-key="detail-read"]')).to_have_count(0)
            detail = [read for read in reads if 'enrollment_id=' in read]
            assert len(detail) == 2, detail
            # A refresh that says nothing changed keeps the view and does not read the student again.
            page.evaluate('window.dispatchEvent(new Event("focus"))')
            page.wait_for_timeout(600)
            expect(root.get_by_text('Marked this conversation resolved', exact=False)).to_be_attached()
            assert len([read for read in reads if 'enrollment_id=' in read]) == 2, reads
            assert len([read for read in reads if 'brief=1' in read]) >= 2, reads
            # The instructor's own change reads it again, showing what it had meanwhile.
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"))')
            page.wait_for_timeout(600)
            expect(root.get_by_text('Marked this conversation resolved', exact=False)).to_be_attached()
            assert len([read for read in reads if 'enrollment_id=' in read]) == 3, reads
            assert not errors, errors
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
    print('A resolved follow-up is left out of the list read and read when opened.')


if __name__ == '__main__':
    main()
