"""SEVERAL AT ONCE (2026-10-12, requirement: "is the assistant able to modify multiple followups at once, for instance if I say
something like 'do X with all followups requesting Y'", then "no card, build it"): asked in the floating chat on the Follow-up page
for the same change to both open follow-ups, the Assistant asks each to prepare its suggestions again at once. The chat shows one
card for them, with nothing to approve, and the follow-up beside it leaves its waiting step for the new preparation without waiting
for the page's next refresh. Synthetic catalog and a scripted engine only."""
import logging
import os
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_followup_redo import RedoTests
from test_followup_suggestions import ScriptedEngine

CHANGES = "Ask for a doctor's note."


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = RedoTests()
    fixture.setUp()
    server = None
    try:
        _, waiting = fixture.two()  # Alice's reply waits for Approve; nothing is prepared for Bob yet
        answers = []

        def respond(c, prompt):
            listed, event = c.service.run_tool(c.session, 'open_followups', {})
            c.tool_event(event)
            asked, event = c.service.run_tool(c.session, 'redo_followups', {'followups': fixture.both(), 'changes': CHANGES})
            c.tool_event(event)
            answers.append((listed, asked))
            c.emit('assistant', text='I asked both follow-ups to prepare their suggestions again: Example, Alice and Example, Bob.')
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
            root.locator('.support-queue-row', has_text='Example, Alice').first.click()
            review = root.locator('.support-review')
            expect(review).to_contain_text('Send an email')  # her step that waits for you
            expect(review.locator(f'[data-assistant-approve="{waiting["id"]}"]')).to_have_count(1)
            page.keyboard.press('Meta', delay=120)
            page.keyboard.press('Meta', delay=120)
            floating = page.locator('.assistant-area-bubble')
            expect(floating).to_be_visible()
            box = floating.get_by_label('Message the assistant')
            box.fill('For every open follow-up, ask for a doctor’s note.')
            box.press('Enter')
            expect(floating.locator('.assistant-msg[data-from="assistant"]')).to_have_count(1, timeout=15000)
            expect(floating).to_contain_text('I asked both follow-ups to prepare their suggestions again')
            # ONE CARD FOR ONE CHANGE: one line in the conversation and one card beside it for both, with nothing to approve.
            line = floating.locator('.assistant-card-line')
            expect(line).to_have_count(1)
            expect(line).to_contain_text(f'Prepare 2 follow-ups again: “{CHANGES}” · 2 changes')
            card = floating.locator('[data-area-changes-list] [data-batch-card]')
            expect(card).to_have_count(1)
            expect(card).to_contain_text('2 changes · 2 done')
            expect(floating.locator('[data-area-changes-list] > *')).to_have_count(1)
            expect(floating.locator('[data-batch-approve], [data-assistant-approve]')).to_have_count(0)
            card.locator('summary', has_text='Each change').click()
            expect(card.locator('.assistant-batch-list li')).to_have_count(2)
            expect(card).to_contain_text('Prepare “Question” again with your changes: Example, Alice')
            expect(card).to_contain_text('Prepare “Question” again with your changes: Example, Bob')
            # The follow-up beside the chat shows it within seconds (its own refresh comes only every 30).
            expect(review.locator(f'[data-assistant-approve="{waiting["id"]}"]')).to_have_count(0, timeout=8000)
            expect(review).to_contain_text('Waiting for the Assistant', timeout=8000)
            shown = fixture.suggested()
            assert [(shown[who]['state'], shown[who]['reason']) for who in (fixture.alice, fixture.bob)] == [('queued', 'revise')] * 2, shown
            assert '"started":2' in answers[0][1].replace(' ', ''), answers[0][1]
            page.screenshot(path=str(shots / 'followup-several-1440.png'), full_page=False)
            assert not errors, errors
            assert page.evaluate('window.cspViolations') == [], page.evaluate('window.cspViolations')
            browser.close()
        print('browser_followup_several: the floating chat prepares both follow-ups again at once, shows one card for them with '
              'nothing to approve, and the follow-up beside it shows the new preparation without a refresh; CSP clean.')
    finally:
        if server:
            server.close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
