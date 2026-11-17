"""ASSISTANT "DECISION ON THE CARD" (requirement, 2026-10-12): the card's Decision is a dropdown on the Assistant's
suggestion, in both review locations; a choice is saved on the card and recorded only by the instructor's own Record absence.
A scripted engine and synthetic records only."""
import logging
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_followup_suggestions import SuggestionsTests, ScriptedEngine


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = SuggestionsTests()
    fixture.setUp()
    server = None
    try:
        engine_patch = patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine())
        engine_patch.start()
        fixture.addCleanup(engine_patch.stop)
        fixture.incoming(body='I was sick on Friday and missed class.')
        section = fixture.listing(suggestions=False)['items'][0]['student']['section_id']

        def absence(c, _):
            result = fixture.tool_write(c.session, '/api/absences', {'section_id': section, 'enrollment_id': fixture.alice, 'status': 'pending',
                'first_date': '2026-10-02', 'reason': 'illness', 'details': 'Sick on Friday.', 'notice_message_id': fixture.notice()})
            assert result['outcome'] == 'waiting_for_approval', result
            c.emit('assistant', text='Review the absence. [Follow-up](#follow-ups)')
        run, _ = fixture.prepare(absence)
        [card] = [a for a in run['actions'] if a['pending']]
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('window.cspViolations=[]; document.addEventListener("securitypolicyviolation", e=>window.cspViolations.push(e.violatedDirective));')
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            proposal = root.locator('.support-review .support-proposal')
            expect(proposal.locator('.assistant-card-head strong')).to_have_text('Record an absence')
            select = proposal.locator('select[data-followup-decision]')
            # The dropdown stands on the Assistant's suggestion and offers the decisions of a new absence.
            expect(select).to_have_value('pending')
            assert select.get_attribute('aria-label') == 'Decision'
            assert select.locator('option').all_inner_texts() == ['Waiting for your decision', 'Excused', 'Not excused']
            # It sits on the Decision line, at the line's own size: no taller than a line and a half of the fields beside it.
            fields = proposal.locator('.support-changes').first  # the summary's fields (the exact request has its own list)
            label, line = fields.locator('dt', has_text='Decision').bounding_box(), fields.locator('dt', has_text='Reason').bounding_box()
            box = select.bounding_box()
            assert abs((box['y'] + box['height'] / 2) - (label['y'] + label['height'] / 2)) < 6, (box, label)
            assert box['height'] <= line['height'] * 1.6, (box, line)
            assert box['x'] > label['x'] + label['width'], (box, label)
            page.screenshot(path=str(Path.cwd() / 'followup-decision.png'), full_page=True)
            # A read that lands meanwhile leaves the dropdown as it is.
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            page.wait_for_timeout(400)
            expect(select).to_have_value('pending')
            # Choosing saves the decision on the card, in the old card's place; nothing is recorded yet.
            select.focus()
            select.select_option('excused')
            expect(page.locator('.toast-item', has_text='Your decision is on the card. Nothing is recorded until you press Record absence.')).to_be_visible()
            expect(proposal).to_have_count(1)
            expect(proposal.locator('.assistant-card-head strong')).to_have_text('Excuse an absence')
            chosen = proposal.locator('select[data-followup-decision]')
            expect(chosen).to_have_value('excused')
            assert chosen.get_attribute('data-followup-decision') != card['id']
            expect(chosen).to_be_focused()  # the keyboard stays on the dropdown of the card that took its place
            expect(proposal.locator('.support-decided')).to_have_text('Your decision. The Assistant suggested “Waiting for your decision”.')
            assert fixture.client.get('/api/absences').json['absences'] == []
            page.screenshot(path=str(Path.cwd() / 'followup-decision-chosen.png'), full_page=True)
            # The Assistant's Changes tab shows the same card with the same dropdown; a choice there reaches the page too.
            page.locator('.assistant-launch').click()
            panel = page.locator('#assistant-panel')
            panel.locator('[data-assistant-tab="changes"]').click()
            drawer = panel.locator('select[data-followup-decision]')
            expect(drawer).to_have_value('excused')
            drawer.select_option('not_excused')
            expect(panel.locator('select[data-followup-decision]')).to_have_value('not_excused')
            expect(panel.locator('.support-proposal .assistant-card-head strong')).to_have_text('Record an absence')
            page.keyboard.press('Control+i')
            expect(proposal.locator('select[data-followup-decision]')).to_have_value('not_excused')
            # A refused choice (the card changed elsewhere) says why and puts the dropdown back.
            stale = proposal.locator('select[data-followup-decision]')
            page.route('**/followups/suggestions/*/edit', lambda route: route.fulfill(status=409, content_type='application/json',
                                                                                         body='{"error": "This card changed since you looked at it."}'))
            stale.select_option('excused')
            expect(page.locator('.toast-item', has_text='This card changed since you looked at it.')).to_be_visible()
            expect(proposal.locator('select[data-followup-decision]')).to_have_value('not_excused')
            expect(proposal.locator('select[data-followup-decision]')).to_be_enabled()
            page.unroute('**/followups/suggestions/*/edit')
            # The instructor's own Record absence records the decision they chose.
            proposal.locator('select[data-followup-decision]').select_option('excused')
            expect(proposal.locator('.assistant-card-head strong')).to_have_text('Excuse an absence')
            proposal.get_by_role('button', name='Record absence', exact=True).click()
            expect(root.locator('.support-review select[data-followup-decision]')).to_have_count(0)
            for _ in range(50):
                recorded = fixture.client.get('/api/absences').json['absences']
                if recorded:
                    break
                page.wait_for_timeout(100)
            assert [a['status'] for a in recorded] == ['excused'], recorded
            # ASK AFTER A CHANGED DECISION (requirement, 2026-10-13): the status the Assistant would have recorded by itself waits as a card, saying why.
            # A DECIDED STEP IS NOT CARRIED (2026-11-04): that To do asked for the very decision they chose, so its card offers to close
            # the follow-up instead, and says why.
            fixture.suggestions.automatic = True
            fixture.incoming(ident='c1', thread='c', enrollment=fixture.bob, body='I was sick on Friday as well.', at='2026-10-03T13:00:00Z')
            theirs = next(i for i in fixture.listing(suggestions=False)['items'] if i['enrollment_id'] == fixture.bob)

            def decide_later(c, _):
                made = fixture.tool_write(c.session, '/api/absences', {'section_id': theirs['student']['section_id'], 'enrollment_id': fixture.bob,
                    'status': 'pending', 'first_date': '2026-10-02', 'reason': 'illness', 'notice_message_id': fixture.notice('c1')})
                assert made['outcome'] == 'waiting_for_approval', made
                status = fixture.tool_write(c.session, '/api/student-support', {'kind': 'followup', 'enrollment_id': fixture.bob,
                    'source_key': theirs['source_key'], 'observed': theirs['observed'], 'title': 'Question', 'state': 'todo',
                    'next': 'Decide the October 2 absence'})
                assert status['outcome'] == 'scheduled', status
                c.emit('assistant', text='Review the absence.')
            fixture.prepare(decide_later)
            page.reload()
            root.locator('.support-queue-row', has_text='Example, Bob').click()
            expect(proposal).to_have_count(1)  # the status is scheduled, under Done for you: only the absence waits
            proposal.locator('select[data-followup-decision]').select_option('excused')
            expect(proposal).to_have_count(2)
            held = proposal.filter(has_text='You decided this on the absence card, so it no longer asks you to decide it. Check nothing else is left.')
            expect(held).to_have_count(1)
            expect(held.locator('.assistant-card-head strong')).to_contain_text('resolved')
            page.screenshot(path=str(Path.cwd() / 'followup-decision-held.png'), full_page=True)
            assert not errors, errors
            assert page.evaluate('window.cspViolations') == []
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
    print('Follow-up absence decision: the dropdown on the suggestion, a choice saved on the card in both review locations, a refused choice, Record absence and the status held after a changed decision passed.')


if __name__ == '__main__':
    main()
