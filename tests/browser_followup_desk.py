"""TEACHING-WORKFLOWS ACTION DESK: synthetic tasks, preserved drafts and independent completion."""
import logging
import re
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_student_support import StudentSupportTests
from test_storage import HEADERS


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = StudentSupportTests()
    fixture.setUp()
    server = None
    try:
        fixture.incoming(body='Could you arrange a quiz alternative?')
        item = fixture.listing()[0]
        saved = fixture.client.post('/api/student-support', json={**item, 'state': 'todo', 'request_id': 'desk-fixture',
            'commitments': [{'id': 'alternative', 'title': 'Arrange a quiz alternative', 'kind': 'work', 'state': 'open', 'waiting_on': 'you'},
                            {'id': 'later', 'title': 'Revisit the makeup hold', 'kind': 'check_back', 'state': 'open',
                             'waiting_on': 'date', 'check_back': '2099-10-12', 'depends_on': ['alternative']}]}, headers=HEADERS)
        assert saved.status_code == 201, saved.json
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1100})
            context.route('**/*', lambda r: r.continue_() if r.request.url.startswith(address + '/') else r.abort())
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            expect(root.get_by_role('navigation', name='Student follow-through').get_by_role('button')).to_have_text(['Action desk', 'Tasks', 'Office hours'])
            expect(root.locator('[data-desk-view="action"]').first).to_have_attribute('aria-pressed', 'true')
            expect(root.locator('.support-task-list').first).to_contain_text('Arrange a quiz alternative')
            expect(root.locator('.support-task-list').first).to_contain_text('Revisit the makeup hold')
            root.get_by_role('button', name='Tasks', exact=True).click()
            expect(root.locator('.support-commitments')).to_contain_text('2 tasks · 1 conversations')
            root.locator('[data-desk-open]').first.click()
            expect(root.locator('.support-review')).to_be_visible()
            root.locator('[data-commitment-complete="alternative"]').click()
            form = page.locator('#support-commitment-form')
            expect(form.locator('[name="dependency"]')).to_have_count(0)  # the check back already waits on it: never offered first
            form.locator('[name="completion_note"]').fill('Created and checked the equivalent activity.')
            form.get_by_role('button', name='Record completion', exact=True).click()
            expect(form).not_to_be_visible()
            expect(root.locator('.support-task-list').first).not_to_contain_text('Arrange a quiz alternative')
            expect(root.locator('.support-task-list').first).to_contain_text('Revisit the makeup hold')
            expect(root.locator('.support-queue-row')).to_have_attribute('data-waits', 'later')
            expect(root.locator('.support-review-source')).to_contain_text('Could you arrange a quiz alternative?')
            for width in (1440, 1280):
                page.set_viewport_size({'width': width, 'height': 1100})
                assert root.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'), 'Action desk overflows its desktop page'
                page.screenshot(path=str(Path.cwd() / f'action-desk-{width}.png'), full_page=True)
            # WHAT YOU DID, ONE CLICK ON A TO DO (requirement, 2026-10-13): a To do the instructor saved is under Your to-dos, saying what they did, and settles in a click.
            fixture.incoming('b1', '2026-10-01T13:00:00Z', thread='two', enrollment=fixture.bob, body='I think my quiz went missing.')
            fixture.incoming('c1', '2026-10-01T14:00:00Z', thread='three', body='Could I see my exam?')
            for key, step in (('gmail:two', 'Look for the take-home quiz'), ('gmail:three', 'Find the exam and write back')):
                item = next(i for i in fixture.listing() if i['source_key'] == key)
                made = fixture.client.post('/api/student-support', json={**item, 'state': 'todo', 'next': step, 'request_id': 'todo-' + key},
                                           headers=HEADERS)
                assert made.status_code == 201, made.json
            page.reload()
            expect(root.locator('.support-queue-group[data-queue="todo"]')).to_have_text(re.compile(r'Your to-dos\s*2'))
            expect(root.locator('.support-queue-group[data-queue="review"]')).to_have_count(0)  # nothing is new to decide
            todo = root.locator('.support-queue-row[data-queue="todo"]')
            expect(todo).to_have_count(2)
            quiz = todo.filter(has_text='Look for the take-home quiz')
            expect(quiz.locator('.support-queue-acted')).to_contain_text('You updated it')
            expect(quiz.locator('.support-queue-next')).to_have_count(0)  # the title already is the next step
            assert quiz.get_attribute('data-waits') == 'you'
            page.screenshot(path=str(Path.cwd() / 'action-desk-to-dos.png'), full_page=True)
            quiz.click()
            box = root.locator('.support-decision')
            expect(box.get_by_role('button', name='Done', exact=True)).to_be_visible()
            expect(box.get_by_role('button', name='Review tasks', exact=True)).to_have_count(0)
            box.get_by_role('button', name='Now waits on Bob', exact=True).click()
            expect(page.locator('.toast-item', has_text='Now waits on Bob.')).to_be_visible()
            waits = root.locator('.support-queue-row[data-queue="students"]')
            expect(waits).to_have_count(1)
            expect(waits.locator('.support-queue-next')).to_have_text('Waiting on Bob: A reply to your last message')  # the To do's step is not carried over
            expect(todo).to_have_count(1)
            todo.click()
            root.locator('.support-decision').get_by_role('button', name='Done', exact=True).click()
            expect(page.locator('.toast-item', has_text='Resolved.')).to_be_visible()
            expect(todo).to_have_count(0)
            states = {i['source_key']: i['state'] for i in fixture.listing() if i['source_key'] in ('gmail:two', 'gmail:three')}
            assert states == {'gmail:two': 'waiting', 'gmail:three': 'resolved'}, states
            assert not errors, errors
            browser.close()
    finally:
        if server:
            server.close()
        fixture.tearDown()


if __name__ == '__main__':
    main()
