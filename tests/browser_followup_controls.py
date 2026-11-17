"""FOLLOW-UP REVIEW (instructor's review, 2026-10-03): the plan of steps (FOLLOW-UP CLARITY), the calendar beside a meeting card, Edit in Mail, the Suggestions
ready filter, Send and approve all, the pause switch and the per-conversation switch. Fake engines and fake Gmail only."""
import logging
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_followup_suggestions import MAIL, MEETING, ScriptedEngine, SuggestionsTests, proposing
from test_storage import HEADERS
from browser_course_pages import seed_course


def main():
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    fixture = SuggestionsTests()
    fixture.setUp()
    server = None
    try:
        engine_patch = patch('assistant.conversations.engine_for', side_effect=lambda _: ScriptedEngine())
        engine_patch.start()
        fixture.addCleanup(engine_patch.stop)
        google = fixture.fake_mail()
        fixture.incoming()
        created = fixture.client.post('/api/calendar/events', json={'request_id': str(uuid.uuid4()), 'scope': 'workspace', 'category': 'event',
            'title': 'MATH 140 lecture', 'date': '2026-10-12', 'all_day': False, 'time': '14:00', 'end_time': '14:50'}, headers=HEADERS)
        assert created.status_code == 201, created.json
        run, _ = fixture.prepare(proposing(MAIL, MEETING))
        meeting, mail = run['actions']
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
            section = root.locator('.support-suggestions')
            # The plan of the steps that wait leads the review in its orange box (THE DECISION FIRST, Ledger).
            plan = root.locator('.support-task-plan > .support-task-list')
            expect(plan.locator('li')).to_have_count(2)
            expect(plan).to_contain_text('Send an email')  # a new message, not a reply in the thread
            expect(plan).to_contain_text('Schedule office hours')
            expect(section.locator('.support-plan-steps')).to_have_count(0)
            expect(root.locator(f'[data-action-id="{meeting["id"]}"]')).to_contain_text('Overlaps on your calendar: MATH 140 lecture')
            expect(root.get_by_text('Review on its page')).to_have_count(0)
            expect(page.locator('#page-subtitle')).to_contain_text('1 with suggestions ready')
            # TEACHING-WORKFLOWS / COMMUNICATIONS: one selected review, the exact student's message, and retained typing on switches.
            expect(root.locator('.support-message-text')).to_have_text('Could I meet you?')
            expect(root.locator('.support-evidence')).not_to_have_attribute('open', '')
            fixture.incoming('a2', body='Second conversation, unrelated to the meeting.', thread='b2', enrollment=fixture.bob, at='2026-10-03T13:00:00Z')
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            expect(root.locator('.support-queue-row')).to_have_count(2)
            expect(root.locator('.support-review')).to_have_count(1)
            root.locator('.support-revise summary').click()
            root.locator('[data-followup-revise] textarea').fill('Keep this unsent request while I read the other message.')
            root.locator('[data-support-select="1"]').click()
            expect(root.locator('.support-message-text')).to_have_text('Second conversation, unrelated to the meeting.')
            expect(root.locator('[data-assistant-approve]')).to_have_count(0)
            root.get_by_role('button', name='Previous conversation', exact=True).click()
            expect(root.locator('[data-followup-revise] textarea')).to_have_value('Keep this unsent request while I read the other message.')
            expect(root.locator('.support-message-text')).to_have_text('Could I meet you?')
            assert root.locator('.support-revise').evaluate('(e)=>e.open')
            root.locator('.support-revise summary').click()
            # COMMUNICATIONS / FRONTEND: a late read from the previous selection cannot replace this one's message.
            timeline = fixture.client.get('/api/communications/students/' + fixture.alice).json
            held = []
            page.route('**/api/communications/students/*', lambda route: held.append(route))
            root.get_by_role('button', name='Next conversation', exact=True).click()
            page.wait_for_timeout(200)
            root.get_by_role('button', name='Previous conversation', exact=True).click()
            page.wait_for_timeout(200)
            assert len(held) == 2
            held[1].fulfill(status=200, json=timeline)
            expect(root.locator('.support-message-text')).to_have_text('Could I meet you?')
            held[0].fulfill(status=200, json=timeline)
            page.wait_for_timeout(200)
            expect(root.locator('.support-message-text')).to_have_text('Could I meet you?')
            page.unroute('**/api/communications/students/*')
            page.route('**/api/communications/students/*', lambda route: route.fulfill(status=503, json={'error': 'Synthetic message read failure'}), times=1)
            root.get_by_role('button', name='Next conversation', exact=True).click()
            expect(root.get_by_text('The message could not be read:', exact=False)).to_be_visible()
            root.get_by_role('button', name='Try again', exact=True).click()
            expect(root.locator('.support-message-text')).to_have_text('Second conversation, unrelated to the meeting.')
            root.get_by_role('button', name='Previous conversation', exact=True).click()
            expect(root.locator('.support-message-text')).to_have_text('Could I meet you?')
            for width in (1440, 1280):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert root.locator('.support-review').evaluate('(el)=>el.scrollWidth <= el.clientWidth + 1')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                root.locator('.support-workspace').screenshot(path=str(Path.cwd() / f'followup-review-{width}.png'))
            # EDIT IN PLACE: Edit opens the draft on its own card; Approve all waits until the edit is saved or cancelled.
            root.locator(f'[data-followup-edit="{mail["id"]}"]').click()
            editor = root.locator(f'[data-followup-edit-form="{mail["id"]}"]')
            expect(editor.locator('textarea[name="body"]')).to_have_value(MAIL[1]['body'])
            expect(root.get_by_role('button', name='Send and approve all 2', exact=True)).to_have_count(0)
            expect(root.get_by_text('Save or cancel your edit to approve all.', exact=True)).to_be_visible()
            # Edit in Mail, from the editor, opens Mail's own compose window with the draft; nothing is sent until its own Send.
            editor.get_by_role('button', name='Edit in Mail…', exact=True).click()
            compose = page.locator('#dialog form.mail-compose')
            expect(compose).to_be_visible()
            expect(compose.locator('[name="to"]')).to_have_value('alice@example.test')
            expect(compose.locator('[name="body"]')).to_have_value(MAIL[1]['body'])
            compose.get_by_role('button', name='Cancel').click()
            assert google.sent == []
            # Save changes puts the edited text on the card, in the draft's place; nothing is sent.
            root.locator(f'[data-followup-edit="{mail["id"]}"]').click()
            editor.locator('textarea[name="body"]').fill('Come by Monday at 2 pm and we will go through it.\n\nBest,\nChris')
            editor.get_by_role('button', name='Save changes', exact=True).click()
            expect(editor).to_have_count(0)
            mail = next(a for i in fixture.listing()['items'] for a in (i.get('suggestions') or {}).get('actions', []) if a.get('edited'))
            expect(root.locator(f'[data-action-id="{mail["id"]}"]')).to_contain_text('Come by Monday at 2 pm and we will go through it.')
            expect(root.locator(f'[data-action-id="{mail["id"]}"]')).to_contain_text('Your edited version.')
            expect(root.locator('.support-task-plan > .support-task-list li').first).to_contain_text('Schedule office hours')
            assert google.sent == []
            # An explicit watch previews first, then saves. Cancel/pause/removal
            # never prepare or send anything. The same control survives refresh.
            root.get_by_role('button', name='Add a watch…', exact=True).click()
            watch = page.locator('#followup-watch')
            from scheduling.clock import campus_today
            from datetime import timedelta
            watch.get_by_label('Check back on').fill((campus_today() + timedelta(days=2)).isoformat())
            watch.get_by_role('button', name='Review watch', exact=True).click()
            expect(watch.locator('[data-watch-preview]')).to_be_visible()
            assert not fixture.listing()['items'][0]['suggestions']['watches']
            watch.get_by_role('button', name='Save watch', exact=True).click()
            expect(watch).not_to_be_visible()
            watches = root.locator('.support-watches')
            expect(watches).to_contain_text('Watching')
            watches.get_by_role('button', name='Pause', exact=True).click()
            expect(watches).to_contain_text('Paused')
            watches.get_by_role('button', name='Remove', exact=True).click()
            expect(watches.get_by_role('button', name='Resume', exact=True)).to_have_count(0)
            assert google.sent == []
            # The Suggestions ready filter keeps this follow-up; Send and approve all sends the reply and saves the meeting in order.
            root.locator('[data-support-filter]').select_option('suggestions')
            expect(root.locator('.support-card')).to_have_count(1)
            root.locator('[data-support-tab="all"]').click()  # once approved, nothing of it waits for you
            root.get_by_role('button', name='Send and approve all 2', exact=True).click()
            expect(root.locator(f'[data-action-id="{meeting["id"]}"] .chip')).to_have_text('Done')
            expect(root.locator(f'[data-action-id="{mail["id"]}"] .chip')).to_have_text('Done')
            assert len(google.sent) == 1
            # The workspace switch and the conversation's own switch.
            root.locator('.support-automation summary').click()
            root.get_by_role('button', name='Pause', exact=True).click()
            expect(root.locator('.support-automatic')).to_contain_text('Automatic suggestions: paused')
            root.get_by_role('button', name='Resume', exact=True).click()
            expect(root.locator('.support-automatic')).to_contain_text('Automatic suggestions: on')
            root.locator('.support-suggestion-options summary').click()
            root.get_by_role('button', name='Don’t suggest for this conversation', exact=True).click()
            expect(root.get_by_text('Suggestions are off for this conversation', exact=False).first).to_be_visible()
            expect(root.get_by_role('button', name='Turn suggestions back on', exact=True)).to_be_visible()
            # TERM POLICIES: independent areas/modes, legacy preservation, and drafts retained between areas and terms.
            legacy = 'Existing policy text: sign off as Chris.\n\nKeep these notes until I reorganize them.'
            seeded = fixture.client.post(f'/api/student-support/policies/{fixture.term}', json={
                'content': legacy, 'base_revision': 0, 'request_id': 'legacy-policies'}, headers=HEADERS)
            assert seeded.status_code == 200, seeded.json
            seed_course(fixture.client, code='MATH 141', name='Business Math II', sections=[507, 508])
            other = next(term for term in fixture.client.get('/api/student-support/policies').json['terms'] if term['term_id'] != fixture.term)
            root.get_by_role('button', name='Term policies…', exact=True).click()
            policies = page.locator('#support-policies')
            expect(policies).to_be_visible()
            expect(policies.locator('#policy-area-title')).to_have_text('Additional guidance')
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value(legacy)
            expect(policies.get_by_role('button', name='Save policies', exact=True)).to_be_disabled()
            # The footer's button reads Cancel while any term has unsaved changes and Close once none has; the header's ✕ keeps its
            # icon and no words (it had been the one renamed, the word spilling out of its square).
            footer_close = policies.locator('.dialog-footer [data-action="close"]')
            header_close = policies.locator('.dialog-header [data-action="close"]')
            def closes(label):
                expect(footer_close).to_have_text(label)
                expect(header_close).to_have_text('')
                expect(header_close.locator('svg.icon')).to_have_count(1)
            closes('Close')
            policies.locator('[data-policy-area="appointments"]').click()
            policies.get_by_label('Your guidance', exact=True).fill('Office hours: Mondays 2–3 pm. Check my calendar before proposing other times.')
            expect(policies.get_by_label('How to handle this area')).to_have_value('guidance')
            closes('Cancel')
            policies.locator('[data-policy-area="quiz_makeups"]').click()
            policies.get_by_label('How to handle this area').select_option('ask')
            policies.get_by_label('Your guidance', exact=True).fill('Confirm which quiz and the missed class date.')
            policies.locator('[data-policy-area="appointments"]').click()
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value('Office hours: Mondays 2–3 pm. Check my calendar before proposing other times.')
            policies.get_by_label('Term', exact=True).select_option(other['term_id'])
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value('')
            policies.get_by_label('Your guidance', exact=True).fill('Office hours for MATH 141: Tuesdays only.')
            policies.get_by_label('Term', exact=True).select_option(fixture.term)
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value('Office hours: Mondays 2–3 pm. Check my calendar before proposing other times.')
            started, release = threading.Event(), threading.Event()
            original_save = fixture.suggestions.support.save_policy
            def delayed_save(*args, **kwargs):
                started.set()
                assert release.wait(10), 'Synthetic policy save was not released'
                return original_save(*args, **kwargs)
            with patch.object(fixture.suggestions.support, 'save_policy', side_effect=delayed_save):
                policies.get_by_role('button', name='Save policies', exact=True).click()
                assert started.wait(5), 'Synthetic policy save did not start'
                newer = 'Office hours: Mondays 2–3 pm. Keep this edit typed during Save.'
                policies.get_by_label('Your guidance', exact=True).fill(newer)
                release.set()
                expect(policies.locator('[data-policy-saved]')).to_contain_text('revision 2')
                expect(policies.get_by_label('Your guidance', exact=True)).to_have_value(newer)
                expect(policies.get_by_label('Your guidance', exact=True)).to_be_focused()
                expect(policies.locator('[data-policy-progress]')).to_contain_text('Unsaved changes')
            policies.get_by_role('button', name='Save policies', exact=True).click()
            expect(policies.locator('[data-policy-saved]')).to_contain_text('revision 3')
            expect(policies.get_by_role('button', name='Save policies', exact=True)).to_be_disabled()
            closes('Cancel')  # the other term's draft is still unsaved
            policies.get_by_label('Term', exact=True).select_option(other['term_id'])
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value('Office hours for MATH 141: Tuesdays only.')
            policies.get_by_role('button', name='Save policies', exact=True).click()
            expect(policies.locator('[data-policy-saved]')).to_contain_text('revision 1')
            closes('Close')
            policies.get_by_role('button', name='Close', exact=True).click()
            # MAKEUP ROOMS (2026-10-05): the window lists the rooms of the instructor's office hours on the calendar to tick.
            from datetime import date, timedelta
            from exam_workflow.overview import campus_today
            soon = (date.fromisoformat(campus_today()) + timedelta(days=3)).isoformat()
            for room in ('BLOC 309', 'BLOC 319'):
                fixture.app.extensions['calendar'].create({'request_id': str(uuid.uuid4()), 'title': 'MATH 140 · Office hours', 'date': soon,
                                                           'time': '15:00', 'end_time': '17:00', 'all_day': False, 'location': room})
            root.get_by_role('button', name='Term policies…', exact=True).click()
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value(legacy)
            policies.locator('[data-policy-area="quiz_makeups"]').click()
            expect(policies.get_by_label('How to handle this area')).to_have_value('ask')
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value('Confirm which quiz and the missed class date.')
            expect(policies.locator('[data-policy-progress]')).to_contain_text('3 of 15 areas set')
            # MEETING HOURS (2026-10-05): the term's weekly meeting times sit beside Office hours & appointments and save with the rest;
            # a new time starts as Tuesday 1–5 pm in the room above it.
            policies.locator('[data-policy-area="meeting_hours"]').click()
            expect(policies.locator('#policy-area-title')).to_have_text('Meeting hours')
            expect(policies.locator('[data-policy-area="meeting_hours"] small')).to_have_text('—')
            policies.get_by_role('button', name='Add a time', exact=True).click()
            rows = policies.locator('[data-meeting-row]')
            expect(rows).to_have_count(1)
            rows.nth(0).get_by_label('Room', exact=True).fill('BLOC 319')
            policies.get_by_role('button', name='Add a time', exact=True).click()
            expect(rows.nth(1).get_by_label('Day', exact=True)).to_be_focused()
            rows.nth(1).get_by_label('Day', exact=True).select_option('3')
            expect(rows.nth(1).get_by_label('Room', exact=True)).to_have_value('BLOC 319')
            expect(rows.nth(1).get_by_label('From', exact=True)).to_have_value('13:00')
            expect(policies.locator('[name="makeup_room"]')).to_have_count(2)
            expect(policies.get_by_label('BLOC 319', exact=True)).not_to_be_checked()
            policies.get_by_label('BLOC 309', exact=True).check()
            closes('Cancel')  # unsaved meeting hours count too
            policies.get_by_role('button', name='Save policies', exact=True).click()
            expect(policies.get_by_role('button', name='Save policies', exact=True)).to_be_disabled()
            closes('Close')
            expect(policies.locator('[data-policy-area="meeting_hours"] small')).to_have_text('Set')
            hours = next(t for t in fixture.client.get('/api/student-support/policies?areas=1').json['terms'] if t['term_id'] == fixture.term)['meeting_hours']
            assert hours['revision'] == 1 and hours['windows'] == [{'weekday': d, 'start': '13:00', 'end': '17:00', 'location': 'BLOC 319'} for d in (1, 3)], hours
            assert hours['makeup_rooms'] == ['BLOC 309'], hours
            page.screenshot(path=str(Path.cwd() / 'followup-meeting-hours.png'))
            policies.locator('[data-policy-area="quiz_makeups"]').click()
            page.screenshot(path=str(Path.cwd() / 'followup-term-policies.png'))
            page.set_viewport_size({'width': 1280, 'height': 900})
            assert policies.evaluate('(form) => form.scrollWidth <= form.clientWidth')
            expect(policies.get_by_role('button', name='Save policies', exact=True)).to_be_in_viewport()
            page.screenshot(path=str(Path.cwd() / 'followup-term-policies-1280.png'))
            policies.get_by_role('button', name='Close', exact=True).click()
            # Repeated edits are a proposal. Review changes only the draft;
            # the ordinary revision-checked Save is the explicit adoption.
            wording = 'For similar questions, ask students to bring their attempted solution.'
            def repeated_edits(route):
                data = route.fetch().json()
                target = next(t for t in data['terms'] if t['term_id'] == fixture.term)
                target['suggested_guidance'] = [{'id': 'synthetic', 'wording': wording, 'before': 'Come to office hours.',
                    'guidance': wording, 'cases': 2, 'source_actions': []}]
                route.fulfill(json=data)
            page.route('**/api/student-support/policies?areas=1', repeated_edits, times=1)
            root.get_by_role('button', name='Term policies…', exact=True).click()
            policies.locator('[data-policy-area="reply_style"]').click()
            policies.locator('[data-policy-suggestions] summary').click()
            policies.get_by_role('button', name='Review as guidance…', exact=True).click()
            expect(policies.get_by_label('Your guidance', exact=True)).to_have_value(wording)
            saved_before = fixture.client.get('/api/student-support/policies').json['terms']
            assert wording not in next(t['content'] for t in saved_before if t['term_id'] == fixture.term)
            policies.get_by_role('button', name='Save policies', exact=True).click()
            expect(policies.get_by_role('button', name='Save policies', exact=True)).to_be_disabled()
            saved_after = fixture.client.get('/api/student-support/policies').json['terms']
            assert wording in next(t['content'] for t in saved_after if t['term_id'] == fixture.term)
            policies.get_by_role('button', name='Close', exact=True).click()
            # Desktop only, one appearance (DESIGN-LANGUAGE, 2026-10-03): the narrowest desktop window does not scroll sideways.
            page.set_viewport_size({'width': 1280, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(Path.cwd() / 'followup-controls-1280.png'), full_page=True)
            assert not errors, errors
            assert page.evaluate('window.cspViolations') == []
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
    print('Follow-up plan of steps, calendar note, Edit in Mail, filter, Send and approve all, pause and mute passed.')


if __name__ == '__main__':
    main()
