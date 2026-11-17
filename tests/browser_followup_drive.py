"""LINKED DRIVE FILES on the Follow-up page (COMMUNICATIONS; requirement, 2026-10-11): under the student's
message, a file they linked from Google Drive that Faculty could not fetch says why, and offers the one click that lets Faculty read
Drive, for the account already connected. A message kept before Faculty read Drive lists no such file: its link alone says so and
offers the same click (the instructor, of that message: "I dont see the option"), and so does the page's captured-communications
note. Google's sign-in is a stub; a fake Gmail connection only, and no engine runs."""
import logging
import threading
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from communications.record import record
from rosters import connect
from test_communications import LETTER, NOTE, wrapped
from test_followup_suggestions import ScriptedEngine, SuggestionsTests
from test_gmail_mail import ACCOUNT


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = SuggestionsTests()
    fixture.setUp()
    server = None
    try:
        engine_patch = patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine())
        engine_patch.start()
        fixture.addCleanup(engine_patch.stop)
        fixture.fake_mail()
        # Her message as the capture records it while Faculty may not read Drive: the linked note is listed, not kept, with why.
        with fixture.rosters_lock(), connect(fixture.assistant.database) as db:
            record(db, 'gmail', 'a1', {'sent_at': '2026-10-03T12:00:00Z', 'author_role': 'student', 'author': {'name': 'Alice'}, 'recipients': [],
                                       'subject': 'Sick', 'body_text': 'I am back in the ER. My note is attached.', 'context': {'thread_id': 'b1'},
                                       'attachments': [{'name': '2nd Dr Note.pdf', 'drive_id': NOTE, 'kept': False, 'wait': 'permission',
                                                        'reason': 'Faculty may not read your Google Drive yet'}]},
                   [(fixture.alice, fixture.term, 'author', 'roster_address')], 'b1')
            # Bob's message as it was kept before Faculty read Drive at all: the link is in its words, and no file is listed.
            record(db, 'gmail', 'a2', {'sent_at': '2026-10-03T13:00:00Z', 'author_role': 'student', 'author': {'name': 'Bob'}, 'recipients': [],
                                       'subject': 'Excuse', 'context': {'thread_id': 'b2'}, 'attachments': [],
                                       'body_text': f"My excuse is attached.\n\n Excuse.pdf\n<{wrapped('https://drive.google.com/open?id=' + LETTER)}>"},
                   [(fixture.bob, fixture.term, 'author', 'roster_address')], 'b2')
            db.commit()
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('https://accounts.google.com/**', lambda route: route.fulfill(status=200, content_type='text/html', body='<title>Google</title>'))
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            expect(root.locator('.support-queue-row')).to_have_count(2)
            # Bob's message, kept before: the link in its words is enough to say the file was not fetched and to offer the click.
            root.locator('.support-queue-row').filter(has_text='Bob').click()
            message = root.locator('.support-message').first
            expect(message.locator('.support-message-text')).to_contain_text('My excuse is attached.')
            hint = message.locator('.support-message-drive')
            expect(hint).to_contain_text('This message links a file on Google Drive. Faculty has not fetched it: it may not read your Google Drive yet.')
            expect(hint.get_by_role('button', name='Allow Faculty to read Google Drive')).to_be_visible()
            # The page's note on what it is based on says the same, with the same click.
            root.locator('.support-capture summary').click()
            noted = root.locator('.support-capture li').filter(has_text='Google Drive')
            expect(noted).to_contain_text('A file a student links from Google Drive is not fetched')
            expect(noted.get_by_role('button', name='Allow Faculty to read Google Drive')).to_be_visible()
            Path('test-results').mkdir(exist_ok=True)
            page.screenshot(path='test-results/followup-drive-link.png', full_page=True)
            # Alice's message lists the file itself with why, once: no second note about a link.
            root.locator('.support-queue-row').filter(has_text='Alice').click()
            expect(message.locator('.support-message-text')).to_have_text('I am back in the ER. My note is attached.')
            expect(message.locator('.support-message-drive')).to_have_count(0)
            expect(message).to_contain_text('2nd Dr Note.pdf · not fetched from Google Drive: Faculty may not read your Google Drive yet')
            expect(message.get_by_role('link', name='2nd Dr Note.pdf')).to_have_count(0)  # nothing to open: it was not read
            allow = message.get_by_role('button', name='Allow Faculty to read Google Drive')
            expect(allow).to_be_visible()
            output = Path('test-results')
            output.mkdir(exist_ok=True)
            page.screenshot(path=str(output / 'followup-drive.png'))
            assert not errors, errors
            # One click: Google is asked for read-only Drive, for the account already connected.
            allow.click()
            page.wait_for_url('https://accounts.google.com/**')
            asked = urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)
            assert 'https://www.googleapis.com/auth/drive.readonly' in asked['scope'][0].split(), asked
            assert asked['login_hint'] == [ACCOUNT] and asked['include_granted_scopes'] == ['true'], asked
            assert fixture.app.extensions['communications'].google.pending_purpose() == 'drive'
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
    print('follow-up linked Drive file ok')


if __name__ == '__main__':
    main()
