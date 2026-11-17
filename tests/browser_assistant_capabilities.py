"""ASSISTANT: real selected-cell context, unsaved-work hints and the automation inventory."""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from assistant import automation
from rosters import connect

from playwright.sync_api import sync_playwright, expect
from waitress.server import create_server
from test_scheduling_service import ScheduleBase


def main():
    fixture = ScheduleBase()
    fixture.setUp()
    seen = []
    review_flags = []
    class Engine:
        name = 'synthetic'
        def close(self): pass
        def stop(self): pass
        def turn(self, c, text):
            seen.append(c.screen['context'])
            review_flags.append(bool(c.session.get('_review_only')))
            c.emit('assistant', text='Read the selected record without changing it.')
    real_inventory = automation.inventory

    def stubbed(service):
        # AUTOMATION VISIBILITY: rule rows as the rule package returns them (`waiting`, `held`, `last_checked_at`, `last_outcome`).
        view = real_inventory(service)
        base = {'kind': 'workflow', 'scope': 'MATH 140 · Fall 2026', 'enabled': True, 'schedule': 'Daily at 5:00 AM Central',
                'next_check': int(time.time()) + 3600, 'last_result': 'ready', 'blocker': '', 'revision': 1, 'pending': 0,
                'href': '#gradebook/' + fixture.term, 'prepare': {'kind': 'reconciliation', 'target': fixture.term}}
        view['automations'] = [
            {**base, 'id': 'stub-waiting', 'title': 'Grade reconciliation · waiting', 'waiting': 'Waiting for the morning Canvas sync; checks after it finishes',
             'last_outcome': 'No discrepancies', 'last_checked_at': datetime.now(timezone.utc).isoformat()},
            {**base, 'id': 'stub-held', 'title': 'Grade reconciliation · held', 'held': 'Held: a step of the last review failed. Review it, then prepare again.'},
        ] + view['automations']
        return view
    try:
        fixture.setup_gradebook([{'title': 'Synthetic quiz', 'points': '10'}])
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with fixture.app.extensions['rosters'].lock(), connect(fixture.app.extensions['assistant'].database) as db:
            db.execute("""INSERT INTO gradebook_canvas_nightly_runs(id,term_id,night,kind,status,detail_json,created_at)
                VALUES(?,?,?,'sync','unchanged','{}',?)""", (str(uuid.uuid4()), fixture.term, recent[:10], recent))
        server = create_server(fixture.app, host='127.0.0.1', port=0, threads=8)
        threading.Thread(target=server.run, daemon=True).start()
        try:
            with patch('assistant.conversations.config.assistant', return_value={'name': 'Synthetic', 'model': 'test'}), \
                 patch('assistant.conversations.engine_for', side_effect=lambda _: Engine()), \
                 patch('assistant.automation.inventory', side_effect=stubbed), sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.add_init_script("window.cspViolations=[]; document.addEventListener('securitypolicyviolation',e=>cspViolations.push(e.violatedDirective));")
                page.goto(f'http://127.0.0.1:{server.effective_port}/#gradebook/{fixture.term}?section={fixture.sections[507]}')
                cell = page.locator('[data-cell]').first
                expect(cell).to_be_visible()
                row, item = cell.get_attribute('data-row'), cell.get_attribute('data-item')
                cell.click()
                page.locator('.assistant-launch').click()
                panel = page.locator('#assistant-panel')
                panel.locator('#assistant-text').fill('Explain this selected grade.')
                panel.locator('[data-assistant-form] button[type=submit]').click()
                expect(panel.locator('[data-assistant-chat]')).to_contain_text('Read the selected record')
                assert seen[-1]['record']['term_id'] == fixture.term, seen
                assert (seen[-1]['record']['row'], seen[-1]['record']['item_id']) == (row, item), seen
                page.evaluate("""async () => {
                    const source = document.querySelector('script[type=module][src]').src;
                    const ui = await import(new URL('./ui.js', source));
                    window.clearTestGuard = ui.registerLeaveGuard(() => 'Synthetic unsaved work');
                }""")
                panel.locator('#assistant-text').fill('Consider the unsaved work too.')
                panel.locator('[data-assistant-form] button[type=submit]').click()
                expect(panel.locator('[data-assistant-chat]')).to_contain_text('Consider the unsaved work too.')
                for _ in range(50):
                    if len(seen) >= 2: break
                    page.wait_for_timeout(100)
                assert seen[-1]['unsaved'] is True, seen
                page.evaluate('window.clearTestGuard()')
                service = fixture.app.extensions['assistant']
                text, _ = service.run_tool(service.open_session('test'), 'import_teaching_material', {
                    'kind': 'course_notes', 'title': 'Review every line', 'definition_id': fixture.definition,
                    'content': 'Material line. ' * 400 + '<Final review line>'})
                action = json.loads(text)
                panel.get_by_role('tab', name='Changes', exact=False).click()
                card = panel.locator('[data-action-id="' + action['action_id'] + '"]')
                expect(card.get_by_text('Course notes · MATH 140 · Course-wide notes', exact=True)).to_be_visible()
                expect(card.locator('blockquote')).to_contain_text('<Final review line>')
                expect(card.get_by_role('button', name='Approve import', exact=True)).to_be_visible()
                panel.get_by_role('tab', name='Automation', exact=True).click()
                expect(panel.get_by_text('Morning Canvas sync', exact=True)).to_be_visible()
                expect(panel.get_by_text('Review friction', exact=True)).to_be_attached()
                # AUTOMATION VISIBILITY: a wait is a neutral note with no Diagnose; a held rule shows Prepare again and no Next.
                waiting = panel.locator('[data-patch-key="automation-workflow-stub-waiting"]')
                expect(waiting.locator('[data-automation-waiting]')).to_contain_text('Waiting for the morning Canvas sync')
                expect(waiting.locator('[data-automation-repair]')).to_have_count(0)
                expect(waiting).to_contain_text('Last result: No discrepancies · ')
                held = panel.locator('[data-patch-key="automation-workflow-stub-held"]')
                expect(held.locator('[data-automation-held]')).to_contain_text('Held: a step of the last review failed')
                assert 'Next:' not in held.inner_text(), held.inner_text()
                held.get_by_role('button', name='Prepare again', exact=True).click()
                runs = panel.locator('[data-automation-prepare] [data-preparation-runs]')
                expect(runs.locator('article')).to_have_count(1, timeout=10000)
                expect(runs).to_contain_text('Waiting for the Assistant')
                # Prepare now says what happened, starts the form over and lists the run below it.
                panel.locator('[data-assistant-automation] [data-patch-key="preparation-setup"] summary').click()
                form = panel.locator('[data-assistant-automation] [data-preparation-form]')
                form.get_by_role('button', name='Prepare now', exact=True).click()
                expect(form.locator('[data-preparation-preview]')).to_have_text('Preparing in the background · its changes will appear here')
                expect(form.get_by_role('button', name='Prepare now', exact=True)).to_be_visible()
                expect(runs.get_by_role('button', name='Stop preparation')).to_be_visible()
                # Done by Faculty folds the morning sync it recorded, linked to the gradebook.
                done = panel.locator('[data-patch-key="automation-done"]')
                expect(done).not_to_have_attribute('open', '')
                done.locator('summary').click()
                expect(done.get_by_role('link', name='Morning Canvas sync · MATH 140 · Fall 2026: nothing had changed', exact=False)).to_be_visible()
                for width in (1440, 1280):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    panel.screenshot(path=str(Path.cwd() / f'assistant-automation-{width}.png'))
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width': 1440, 'height': 1000})
                # ASSISTANT: recovery preserves unsent text, then starts a review-only turn on explicit request.
                before = len(seen)
                panel.get_by_role('tab', name='Chat', exact=True).click()
                expect(panel.locator('[data-assistant="stop"]')).to_be_hidden(timeout=15000)
                panel.locator('#assistant-text').fill('Keep this unsent draft')
                panel.get_by_role('tab', name='Automation', exact=True).click()
                panel.locator('[data-automation-repair]').first.click()
                expect(panel.locator('#assistant-text')).to_have_value('Keep this unsent draft')
                assert len(seen) == before
                panel.get_by_role('tab', name='Chat', exact=True).click()
                panel.locator('#assistant-text').fill('')
                panel.get_by_role('tab', name='Automation', exact=True).click()
                panel.locator('[data-automation-repair]').first.click()
                for _ in range(100):
                    if len(seen) > before: break
                    page.wait_for_timeout(50)
                assert len(seen) > before and review_flags[-1], (seen, review_flags, page.locator('body').inner_text()[-2000:])
                # The review-only request says so on its message.
                panel.get_by_role('tab', name='Chat', exact=True).click()
                expect(panel.locator('[data-review-only]').last).to_have_text('Review only · every change waits for Approve')
                expect(panel.locator('[data-assistant="stop"]')).to_be_hidden(timeout=15000)
                # ASSISTANT HOME CARD: Home counts the waiting import card and opens the drawer's Changes tab.
                panel.locator('[data-assistant="close"]').click()
                page.goto(f'http://127.0.0.1:{server.effective_port}/#overview')
                need = page.locator('#home-attention [data-need="assistant:changes"]')
                expect(need).to_contain_text('Assistant changes waiting')
                need.get_by_role('link').click()
                expect(panel).to_be_visible()
                expect(panel.get_by_role('tab', name='Changes', exact=False)).to_have_attribute('aria-selected', 'true')
                assert page.evaluate('location.hash') == '#overview'
                for width in (1440, 1280):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.screenshot(path=str(Path.cwd() / f'assistant-capabilities-{width}.png'))
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                assert page.evaluate('window.cspViolations') == []
                browser.close()
        finally:
            server.close()
    finally:
        fixture.doCleanups()
    print('Assistant selected-record context, unsaved hints, automation inventory, Done by Faculty, Prepare now feedback, Home card, desktop layout and CSP passed.')


if __name__ == '__main__': main()
