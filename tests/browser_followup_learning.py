"""ASSISTANT-MEMORY "LEARNED FROM AN EDIT" (requirement, 2026-10-12): a reply
edited on its card and sent is said once as a toast with Undo, without a reload, and on the follow-up as one line; Memory
names it as written from the correction; and Term policies no longer has the lesson review it replaces. A scripted engine, a
scripted writer and a scripted Jev; synthetic records only."""
import logging
import threading
import time
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_assistant_memory import scripted
from test_followup_learning import LearningTests, MINE, RULE
from test_followup_suggestions import ScriptedEngine


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = LearningTests()
    fixture.setUp()
    server = None
    try:
        run, card = fixture.drafted()
        (_, provider), sent = fixture.writer()  # the fixture already names an Assistant model; the page's status route reads it
        for held in (provider, patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=scripted()),
                     patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine())):
            held.start()
            fixture.addCleanup(held.stop)
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors, failures = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('response', lambda response: failures.append((response.status, response.url)) if response.status >= 500 else None)
            page.add_init_script('window.cspViolations=[]; document.addEventListener("securitypolicyviolation", e=>window.cspViolations.push(e.violatedDirective));')
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            review = root.locator('.support-review')
            # The lesson review this replaces is gone: no count on the button, no second tab in the dialog.
            opener = root.locator('[data-support-policies]')
            expect(opener).to_have_text('Term policies…')
            opener.click()
            policies = page.locator('#support-policies')
            expect(policies.locator('.policy-workspace')).to_be_visible()
            expect(policies.get_by_text('Learn from feedback')).to_have_count(0)
            policies.locator('.dialog-footer [data-action="close"]').click()
            expect(policies).to_be_hidden()
            # The instructor rewrites the drafted reply on its card and sends it.
            expect(review.get_by_text('Remembered for later drafts')).to_have_count(0)
            review.locator(f'[data-followup-edit="{card["id"]}"]').click()
            form = review.locator('[data-followup-edit-form]')
            form.locator('textarea[name="body"]').fill(MINE)
            form.get_by_role('button', name='Save changes', exact=True).click()
            expect(review.locator('[data-followup-edit-form]')).to_have_count(0)
            edited, = [action for action in fixture.listing()['items'][0]['suggestions']['actions'] if action['pending']]
            assert edited['id'] != card['id'] and edited['body']['body'] == MINE, edited
            fixture.suggestions.automatic = True  # as live: the dispatcher's look is on (synthetic checks keep it off)
            review.get_by_role('button', name='Send', exact=True).click()
            until = time.monotonic() + 15
            while fixture.view(edited['id'])['status'] != 'succeeded' and time.monotonic() < until:
                time.sleep(.05)
            assert fixture.view(edited['id'])['status'] == 'succeeded', fixture.view(edited['id'])
            # The dispatcher's next look (taken here by hand) finds the correction and hands it to memory's worker.
            assert fixture.learning.busy()
            fixture.learning.turn()
            until = time.monotonic() + 15
            while fixture.learning.busy() and time.monotonic() < until:
                time.sleep(.05)
            assert [case['state'] for case in fixture.cases()] == ['remembered'], fixture.cases()
            assert [request['prompt'] for request in sent] == ['memory_edit'], sent
            # Said once, where it happened, without a reload: the page read again because a correction was being considered.
            kept = page.locator('.toast-item', has_text='Remembered from your edit')
            expect(kept).to_contain_text(f'“{RULE}”', timeout=15000)
            line = review.locator('.support-done', has_text='Remembered for later drafts')
            expect(line).to_contain_text(f'From your edit: “{RULE}”')
            box = line.locator('li').bounding_box()
            assert box['height'] < 80, box  # one short line, not a block
            page.screenshot(path=str(Path.cwd() / 'followup-learned.png'), full_page=True)
            for width in (1440, 1280):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            # It is said once: another read brings no second toast.
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            page.wait_for_timeout(800)
            assert page.locator('.toast-item', has_text='Remembered from your edit').count() <= 1
            # Change opens this memory's row on the Memory page (ASSISTANT-MEMORY "ONE PAGE"), which says how it was kept.
            line.get_by_role('link', name='Change', exact=True).click()
            expect(page.locator('#page-title')).to_have_text('Memory')
            memory = page.locator('#memory-section .memory-row', has_text='written by the Assistant from your correction')
            expect(memory).to_be_visible()
            expect(memory.locator('textarea[name="content"]')).to_have_value(RULE)
            standing = lambda: [m['content'] for m in fixture.client.get('/api/assistant/memory').json['memories'] if m['status'] == 'current']
            assert standing() == [RULE], standing()
            page.go_back()
            expect(line).to_contain_text(f'From your edit: “{RULE}”')
            # Undo on the line takes it back; the line then says so and offers nothing.
            line.get_by_role('button', name='Undo', exact=True).click()
            expect(page.locator('.toast-item', has_text='Taken back')).to_be_visible()
            expect(line.locator('li')).to_have_attribute('data-undone', 'true')
            expect(line).to_contain_text('Taken back')
            expect(line.get_by_role('button', name='Undo', exact=True)).to_have_count(0)
            assert standing() == [], standing()
            assert not errors, errors
            assert not failures, failures
            assert page.evaluate('cspViolations') == []
            browser.close()
    finally:
        fixture.suggestions.automatic = False
        if server:
            server.close()
        fixture.doCleanups()
    print('A sent edit is remembered and said once with Undo, shown on the follow-up and in Memory, taken back from its line; the lesson review is gone.')


if __name__ == '__main__':
    main()
