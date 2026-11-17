"""TEACHING-WORKFLOWS SAME MATTER (2026-10-07): Jev's one-click question, the joined follow-up with Keep separate, and Write to
student… answering in the student's own Gmail thread; the email it sends shows as Your reply at once (WRITTEN AT ONCE,
2026-10-08). Synthetic data, a recorded Jev decision and fake Gmail only."""
import logging
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from rosters import connect
from student_support.service import conversations
from unittest.mock import patch

from test_followup_suggestions import ScriptedEngine, SuggestionsTests
from test_storage import HEADERS


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = SuggestionsTests()
    fixture.setUp()
    server = None
    try:
        engine = patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine())
        engine.start()
        fixture.addCleanup(engine.stop)
        mail = fixture.fake_mail()
        support = fixture.support
        alice = fixture.alice
        # Yara Mercer's shape: "This Week" waits on the student; a new email the next day gives what it waited on.
        fixture.incoming(ident='w1', thread='week', at='2026-10-05T16:47:00Z', body='I will miss class today and tomorrow.')
        fixture.incoming(ident='w2', thread='week', at='2026-10-05T20:41:00Z', author='instructor', body='Let me know if you miss more days.')
        with connect(fixture.assistant.database) as db:
            inbound = conversations(db, alice)[(alice, 'gmail:week')]['inbound']
        tracked = fixture.client.post('/api/student-support', json={'kind': 'followup', 'enrollment_id': alice, 'source_key': 'gmail:week',
            'observed': list(inbound), 'title': 'This Week', 'state': 'waiting', 'next': 'Any more absence dates', 'request_id': 'week'},
            headers=HEADERS)
        assert tracked.status_code == 201, tracked.json
        fixture.incoming(ident='friday', thread='1a11651cadf2f7b9', at='2026-10-06T19:41:00Z', body='I will miss Friday too.')
        with fixture.rosters_lock(), connect(fixture.assistant.database) as db:  # Jev was unsure (JEV SAME MATTER)
            db.execute('BEGIN IMMEDIATE')
            support._decide(db, alice, 'gmail:1a11651cadf2f7b9', 'ask', 'jev', 'gmail:week', 'call-1', 'jev-match:call-1', '0' * 64, 'jev')
            db.commit()
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            queue = root.locator('.support-queue-row')
            expect(queue).to_have_count(2)
            root.locator('.support-queue-row', has_text='Question').click()  # the new email (the other is "This Week")
            review = root.locator('.support-review')
            question = review.locator('.support-match')
            expect(question).to_contain_text('Same matter as “This Week”?')
            expect(question).to_contain_text('Waiting on student')
            # SEE WHAT IT MEANS (2026-11-08): "This Week" itself opens under the question, and is not repeated as another conversation.
            preview = question.locator('.support-match-preview')
            expect(preview.locator('summary')).to_contain_text('Read “This Week” · 2 messages')
            preview.locator('summary').click()
            expect(preview).to_contain_text('I will miss class today and tomorrow.')
            expect(preview).to_contain_text('Let me know if you miss more days.')
            expect(preview).to_contain_text('Waited on: Any more absence dates')
            expect(review.locator('.support-history', has_text='I will miss class today and tomorrow.')).to_have_count(0)
            # WRITE IN THE THREAD: the reply goes in the student's own Gmail thread, with its "Re:" subject.
            expect(review.locator('.support-message-text')).to_contain_text('I will miss Friday too.')
            review.get_by_role('button', name='Write to student…').first.click()
            dialog = page.locator('#dialog')
            expect(dialog.locator('input[name="subject"]')).to_have_value('Re: Question')
            expect(dialog).to_contain_text('Reply')
            dialog.get_by_role('button', name='Cancel').first.click()
            page.screenshot(path=str(Path.cwd() / 'followup-same-matter-question.png'), full_page=True)
            question.get_by_role('button', name='Join', exact=True).click()
            expect(queue).to_have_count(1)
            joined = root.locator('.support-review .support-joined')
            expect(joined).to_contain_text('Also in this follow-up: “Question”')
            expect(root.locator('.support-review .support-message-text')).to_contain_text('I will miss Friday too.')
            looks = joined.get_by_role('button', name='Keep separate').evaluate('el => [getComputedStyle(el).textTransform, el.offsetParent !== null]')
            assert looks == ['none', True], looks
            joined.get_by_role('button', name='Keep separate').click()
            expect(queue).to_have_count(2)
            expect(root.locator('.support-match')).to_have_count(0)  # answered no: never asked again
            # WRITTEN AT ONCE (requirement, 2026-10-08): an email sent with Write to student… is "Your reply" on that follow-up
            # as soon as Mail sends it, before any Gmail capture round has read it back.
            root.locator('.support-queue-row', has_text='Any more absence dates').click()  # "This Week": a row is titled by its next step (ACTION DESK)
            expect(review.locator('.support-original-subject')).to_have_text('Conversation: This Week')
            review.get_by_role('button', name='Write to student…').first.click()
            compose = page.locator('#dialog form.mail-compose')
            expect(compose.locator('[name="to"]')).to_have_value('alice@example.test')
            compose.locator('[name="subject"]').fill('Thursday meeting')
            compose.locator('[name="body"]').fill('We can meet in BLOC 319 on Thursday between 1 and 4 PM.')
            compose.get_by_role('button', name='Review', exact=True).click()
            page.locator('#dialog').get_by_role('button', name='Send to 1 recipient', exact=True).click()
            own = review.locator('.support-message-own')
            expect(own).to_contain_text('Your reply')
            expect(own).to_contain_text('We can meet in BLOC 319 on Thursday between 1 and 4 PM.')
            assert len(mail.sent) == 1, mail.sent
            page.set_viewport_size({'width': 1280, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors, errors
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
    print('Same matter: the one-click question, Write in the thread, the joined follow-up, Keep separate and Written at once passed.')


if __name__ == '__main__':
    main()
