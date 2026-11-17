"""CONNECT A MESSAGE (2026-10-10, Brenna Lyons's follow-up): asked in the floating chat on the Follow-up page to connect a booking
card's email to a follow-up, the Assistant joins it at once, and the follow-up beside the chat shows the email as Your reply with
Keep separate, without waiting for the page's next refresh; asked to separate it, it is its own conversation again. Synthetic catalog
and a scripted engine only."""
import logging
import os
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_followup_connect import CARD, ConnectTests
from test_followup_suggestions import ScriptedEngine
from test_storage import HEADERS


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = ConnectTests()
    fixture.setUp()
    server = None
    try:
        fixture.incoming(body='I missed class on Friday with a concussion. I will send you an update after my reassessment.')
        item = fixture.listing(suggestions=False)['items'][0]
        saved = fixture.client.post('/api/student-support', json={**item, 'state': 'waiting', 'title': 'Missed class due to concussion',
                                    'next': 'Her update after the reassessment', 'request_id': 'wait'}, headers=HEADERS)
        assert saved.status_code == 201, saved.json
        mine = fixture.email('card')

        def respond(c, prompt):
            asked = prompt.rsplit('The instructor', 1)[-1] if 'The instructor' in prompt else prompt
            if 'eparate' in asked.splitlines()[-1] or 'eparate it' in prompt[-400:]:
                name, args, said = 'separate_followup', {'source_key': 'gmail:card'}, 'I kept the booking card’s email separate again.'
            else:
                name, args, said = ('join_followup', {'source_key': 'gmail:b1', 'message_id': mine},
                                    'I joined the Quiz 4 booking card’s email to her follow-up. It still waits on her update.')
            _, event = c.service.run_tool(c.session, name, {'enrollment_id': fixture.alice, **args})
            c.tool_event(event)
            c.emit('assistant', text=said)
        engine_patch = patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine(respond))
        engine_patch.start()
        fixture.addCleanup(engine_patch.stop)
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        shots = Path(os.environ.get('FACULTY_BROWSER_ARTIFACTS') or os.environ.get('TMPDIR') or '.')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('window.cspViolations=[]; document.addEventListener("securitypolicyviolation", e=>window.cspViolations.push(e.violatedDirective));')
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            root.locator('.support-queue-row').first.click()
            review = root.locator('.support-review')
            # ACTION DESK: the heading is the next step; the original subject stays with the conversation.
            expect(review.locator('h3')).to_have_text('Her update after the reassessment')
            expect(review.locator('.support-original-subject')).to_have_text('Conversation: Missed class due to concussion')
            expect(review.locator('.support-message-own')).to_have_count(0)  # she wrote last: no reply of yours shows yet
            expect(review.locator('.support-joined')).to_have_count(0)
            page.keyboard.press('Meta', delay=120)
            page.keyboard.press('Meta', delay=120)
            floating = page.locator('.assistant-area-bubble')
            expect(floating).to_be_visible()
            box = floating.get_by_label('Message the assistant')
            box.fill('Can you connect the response card I sent to Alice to her follow-up?')
            box.press('Enter')
            expect(floating.locator('.assistant-msg[data-from="assistant"]')).to_have_count(1, timeout=15000)
            expect(floating).to_contain_text('I joined the Quiz 4 booking card’s email to her follow-up.')
            expect(floating.locator('[data-assistant-approve]')).to_have_count(0)  # done at once: no card to approve
            # The follow-up shows it within seconds (its own refresh comes only every 30).
            expect(review.locator('.support-joined')).to_contain_text(f'Also in this follow-up: “{CARD}”', timeout=8000)
            expect(review.locator('.support-message-own')).to_contain_text('Pick a time on your card.', timeout=8000)
            expect(review.locator('.support-joined').get_by_role('button', name='Keep separate')).to_be_visible()
            expect(review.locator('.chip').first).to_have_text('Waiting on student')  # its status is as it was
            assert fixture.joins() == [('gmail:card', 'gmail:b1', 'join', 'instructor')], fixture.joins()
            page.screenshot(path=str(shots / 'followup-connect-1440.png'), full_page=False)
            box.fill('Separate it again.')
            box.press('Enter')
            expect(floating.locator('.assistant-msg[data-from="assistant"]')).to_have_count(2, timeout=15000)
            expect(review.locator('.support-joined')).to_have_count(0, timeout=8000)
            expect(review.locator('.support-message-own')).to_have_count(0)
            assert fixture.joins()[-1] == ('gmail:card', '', 'separate', 'instructor'), fixture.joins()
            assert not errors, errors
            assert page.evaluate('window.cspViolations') == [], page.evaluate('window.cspViolations')
            browser.close()
        print('browser_followup_connect: the floating chat joins a card’s email at once, the follow-up shows it without a refresh, '
              'and separates it again; CSP clean.')
    finally:
        if server:
            server.close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
