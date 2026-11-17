"""REDO FROM CHAT (2026-10-09, Jared Zane's follow-up): the chat's steps still waiting for a conversation show in its Suggested
actions, and Send there sends that reply. Synthetic catalog, fake engine and fake Gmail only."""
import logging
import os
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_followup_suggestions import MAIL, ScriptedEngine, proposing
from test_followup_redo import RedoTests
from test_storage import HEADERS


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = RedoTests()
    fixture.setUp()
    server = None
    try:
        engine_patch = patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine(proposing(MAIL)))
        engine_patch.start()
        fixture.addCleanup(engine_patch.stop)
        google = fixture.fake_mail()
        fixture.thread(google)
        fixture.incoming(body='I woke up with a headache. Will it be okay to excuse my absence for class today?')
        run, _ = fixture.prepare(proposing(MAIL))
        session = fixture.chat()
        reply = fixture.tool(session, 'draft_student_reply', {'enrollment_id': fixture.alice, 'source_key': 'gmail:b1',
            'subject': 'Re: Question', 'body': "Hi Alice,\n\nTo excuse today's iClicker, please send me a doctor's note.\n\nBest,\nChris"})
        assert reply['outcome'] == 'waiting_for_approval', reply
        # The follow-up changes underneath (the instructor marks it Waiting on student themselves): its own proposals no longer apply.
        observed = fixture.listing()['items'][0]['observed']
        changed = fixture.client.post('/api/student-support', json={'enrollment_id': fixture.alice, 'kind': 'followup', 'source_key': 'gmail:b1',
            'title': 'Class attendance', 'state': 'waiting', 'next': "Her doctor's note", 'observed': observed,
            'request_id': 'manual-waiting'}, headers=HEADERS)
        assert changed.status_code in (200, 201), changed.json
        assert fixture.listing()['items'][0]['suggestions']['state'] == 'superseded'
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        shots = Path(os.environ.get('FACULTY_BROWSER_ARTIFACTS') or os.environ.get('TMPDIR') or '.')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width in (1440, 1280):
                page = browser.new_page(viewport={'width': width, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script('window.cspViolations=[]; document.addEventListener("securitypolicyviolation", e=>window.cspViolations.push(e.violatedDirective));')
                page.goto(address + '/#follow-ups')
                root = page.locator('#student-support-section')
                actions = root.locator('.support-review-body > .support-suggestions')
                expect(actions.locator('.support-chat-steps')).to_contain_text('One step from your Assistant chat waits for you.')
                card = actions.locator(f'.support-chat-steps [data-action-id="{reply["action_id"]}"]')
                expect(card).to_contain_text("please send me a doctor's note")
                expect(card.get_by_role('button', name='Send', exact=True)).to_be_visible()
                expect(card.locator('[data-followup-edit]')).to_have_count(0)
                # It sits inside the column, above the withdrawn steps, never off the right edge.
                box, column = card.bounding_box(), actions.bounding_box()
                assert box['x'] >= column['x'] - 1 and box['x'] + box['width'] <= column['x'] + column['width'] + 1, (box, column)
                page.screenshot(path=str(shots / f'followup-redo-{width}.png'), full_page=False)
                assert not errors, errors
                assert page.evaluate('window.cspViolations') == [], page.evaluate('window.cspViolations')
                page.close()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(address + '/#follow-ups')
            actions = page.locator('#student-support-section .support-review-body > .support-suggestions')
            actions.locator(f'.support-chat-steps [data-assistant-approve="{reply["action_id"]}"]').click()
            expect(page.locator('.support-chat-steps')).to_have_count(0, timeout=15000)
            assert len(google.sent) == 1, google.sent
            assert fixture.card(reply['action_id'])['status'] == 'succeeded'
            browser.close()
        print('browser_followup_redo: chat steps on the follow-up at 1440 and 1280, Send from there, CSP clean.')
    finally:
        if server:
            server.close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
