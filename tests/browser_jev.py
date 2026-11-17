"""JEV: real browser, synthetic roster and decisions; no external model calls."""
import logging
import threading
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_jev import JevTests, response

logging.getLogger('waitress.queue').setLevel(logging.ERROR)


def main():
    fixture = JevTests()
    fixture.setUp()
    try:
        fixture.incoming('a1', thread='abc')
        fixture.analyze(absence=.0, makeup=.0, office_hours=.0, grading=.0, materials=.0)
        fixture.incoming('a2', thread='def', body='I missed class. Can I take the makeup and meet during office hours?')
        fixture.analyze(kind='request', needs_action=.99, absence=.99, makeup=.99, office_hours=.99, grading=.0, materials=.0)
        engine = Mock()
        engine.name = 'synthetic'
        def turn(conversation, text):
            _, event = conversation.service.run_tool(conversation.session, 'courses', {})
            conversation.tool_event(event)
            conversation.emit('assistant', text='The course has already been certified.')
        engine.turn.side_effect = turn
        patch('assistant.conversations.config.assistant', return_value={'name': 'Synthetic Jev test'}).start()
        patch('assistant.conversations.engine_for', return_value=engine).start()
        patch('jev.client.evaluate', side_effect=lambda state, questions: response(questions, {'paragraph_0': 'unsupported'})).start()
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.effective_port}/#follow-ups')
                root = page.locator('#student-support-section')
                expect(root.locator('.support-card')).to_have_count(1)
                root.locator('.support-related summary').click()
                expect(root.get_by_text('Makeup request', exact=True)).to_be_visible()
                expect(root.get_by_role('button', name='Draft absence record', exact=True)).to_be_visible()
                root.get_by_role('button', name='Schedule office hours', exact=True).click()
                dialog = page.locator('#dialog')
                expect(dialog.get_by_role('heading', name='Schedule office hours', exact=True)).to_be_visible()
                expect(dialog.get_by_label('Purpose')).to_have_value('Office hours')
                dialog.get_by_role('button', name='Cancel', exact=True).click()
                root.locator('[data-support-filter]').select_option('fyi')
                expect(root.locator('.support-card')).to_have_count(1)
                expect(root.locator('.support-card').get_by_text('FYI · no reply suggested', exact=True)).to_be_visible()
                root.locator('[data-support-tab="all"]').click()
                expect(root.locator('.support-queue-row')).to_have_count(2)
                expect(root.locator('.support-card')).to_have_count(1)
                page.screenshot(path=str(Path.cwd() / 'jev-followups.png'), full_page=True)
                page.locator('.assistant-launch').click()
                panel = page.locator('#assistant-panel')
                panel.locator('#assistant-text').fill('Has the course been certified?')
                panel.locator('#assistant-text').press('Enter')
                # QUIET STEPS: an unsupported paragraph is no warning; the check and the one list of sources wait in the fold.
                expect(panel.locator('.assistant-steps > summary')).to_contain_text('How it got this1 step', timeout=15000)
                expect(panel.get_by_text('Review the evidence', exact=True)).to_be_hidden()
                expect(panel.locator('[data-chat-events] > .callout')).to_have_count(0)
                panel.locator('.assistant-steps > summary').click()
                expect(panel.get_by_text('Review the evidence', exact=True)).to_be_visible()
                panel.get_by_text('Claims to review · 1', exact=True).click()
                expect(panel.get_by_text('Not supported by the sources · paragraph 1', exact=True)).to_be_visible()
                expect(panel.locator('.assistant-evidence')).to_have_count(1)
                page.screenshot(path=str(Path.cwd() / 'jev-evidence.png'), full_page=True)
                # A laptop's window (desktop only): the opened evidence stays inside the drawer and the window.
                page.set_viewport_size({'width': 1280, 'height': 900})
                for summary in panel.locator('.assistant-evidence summary').all():
                    summary.click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                assert panel.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
                assert panel.locator('[data-assistant-chat]').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
                page.screenshot(path=str(Path.cwd() / 'jev-evidence-1280.png'), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.close()
    finally:
        fixture.doCleanups()
    print('Jev follow-up and Assistant evidence browser checks passed')


if __name__ == '__main__':
    main()
