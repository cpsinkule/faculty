"""ASSISTANT / TEACHING-WORKFLOWS: both human review locations, fake engines and fake Gmail only."""
import logging
import json
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from waitress.server import create_server

from test_followup_suggestions import SuggestionsTests, ScriptedEngine
from rosters import connect
from test_storage import HEADERS


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
        def mail(c, _):
            result, event = c.service.run_tool(c.session, 'faculty_get', {'path':'/api/communications/students/' + fixture.alice})
            c.tool_event(event)
            result = fixture.tool_write(c.session, '/api/mail/send', {'to':['alice@example.test'], 'subject':'Your question',
                'body':'We can review your question at office hours.\nBring the problem you want to discuss.'})
            assert result['outcome'] == 'waiting_for_approval', result
            c.emit('assistant', text='Review this reply before sending it. [Follow-up](#follow-ups)')
        def initial(c, prompt):
            ScriptedEngine().turn(c, prompt)
            mail(c, prompt)
        run, _ = fixture.prepare(initial)
        meeting, reply = run['actions']
        # TERM POLICIES: an older preparation without a policy revision still needs an explicit Retry control.
        with fixture.rosters_lock(), connect(fixture.assistant.database) as db:
            detail = json.loads(fixture.suggestions.get(db, run['id'])['detail_json'])
            detail.pop('policy_revision', None)
            fixture.suggestions.event(db, run['id'], 'ready', detail)
            db.commit()
        saved = fixture.client.post(f'/api/student-support/policies/{fixture.term}', json={
            'content': 'Use my saved guidance without opening external websites.', 'base_revision': 0,
            'request_id': 'retry-current-policies'}, headers=HEADERS)
        assert saved.status_code == 200, saved.json
        assert 'policies_changed' not in fixture.listing()['items'][0]['suggestions']
        server = create_server(fixture.app, host='127.0.0.1', port=0)
        threading.Thread(target=server.run, daemon=True).start()
        address = f'http://127.0.0.1:{server.effective_port}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width':1440,'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('window.cspViolations=[]; document.addEventListener("securitypolicyviolation", e=>window.cspViolations.push(e.violatedDirective));')
            page.goto(address + '/#follow-ups')
            root = page.locator('#student-support-section')
            expect(root.get_by_role('heading',name='Suggested actions')).to_be_visible()
            # SIDE BY SIDE (requirement, 2026-10-04): no painting; the week's office hours are a short list under the queue, and the
            # selected review puts the student's email beside its suggested actions.
            hours = root.locator('.support-side .support-hours')
            expect(hours.get_by_role('heading', name='Office hours', exact=True)).to_be_visible()
            expect(hours).to_contain_text('No office hours on the calendar this week.')
            expect(root.locator('.painting')).to_have_count(0)
            queue_box, hours_box = root.locator('.support-queue').bounding_box(), hours.bounding_box()
            assert hours_box['y'] >= queue_box['y'] + queue_box['height'] - 1 and abs(hours_box['x'] - queue_box['x']) < 2, (queue_box, hours_box)
            source_box, actions_box = root.locator('.support-review-source').bounding_box(), root.locator('.support-review-body > .support-suggestions').bounding_box()
            assert actions_box['x'] >= source_box['x'] + source_box['width'] and abs(actions_box['y'] - source_box['y']) < 2, (source_box, actions_box)
            # READABLE REVIEW (requirement, 2026-10-04): small capitals are labels only; every text action and link in the review
            # is sentence case and underlined or arrowed (button-theme.css's capitals once won over this page's own rules).
            looks = page.evaluate("""() => [...document.querySelectorAll('#student-support-section .support-review :is(.text-button, .link-arrow)')]
              .filter(el => el.offsetParent).map(el => [el.textContent.trim(), getComputedStyle(el).textTransform, getComputedStyle(el).letterSpacing])""")
            assert looks and all(transform == 'none' and spacing in ('normal', '0px') for _, transform, spacing in looks), looks
            label = root.locator('.support-suggestion-head h4')
            assert label.evaluate('el => getComputedStyle(el).textTransform') == 'uppercase'
            # Office hours today on the calendar (3–5 PM, the campus clock at 2 PM) are listed first.
            campus = ZoneInfo('America/Chicago')
            today = datetime.now(campus).date()
            other = browser.new_page(viewport={'width':1440,'height':900})
            other.on('pageerror', lambda error: errors.append(str(error)))
            other.clock.set_fixed_time(datetime(today.year, today.month, today.day, 14, 0, tzinfo=campus))
            def calendar(route):
                body = route.fetch().json()
                body['events'] = [*body['events'], {'id': 'office-hours:browser', 'source': 'office_hours', 'category': 'event', 'title': 'Office hours',
                    'date': body['today'], 'end_date': body['today'], 'time': '15:00', 'end_time': '17:00', 'all_day': False, 'location': 'BLOC 309',
                    'notes': '', 'href': '#office-hours', 'editable': False, 'time_basis': 'campus'}]
                route.fulfill(json=body)
            other.route('**/api/calendar?*', calendar)
            other.goto(address + '/#follow-ups')
            first = other.locator('#student-support-section .support-hours .support-hours-list li').first
            expect(first.locator('strong')).to_have_text('Today')
            expect(first.locator('span')).to_have_text('3:00–5:00 PM · BLOC 309')
            other.close()
            expect(root.locator(f'[data-action-id="{reply["id"]}"]')).to_contain_text('Bring the problem you want to discuss.')
            expect(root.locator(f'[data-action-id="{reply["id"]}"]')).to_contain_text('alice@example.test')
            assert google.sent == []
            retry = root.get_by_role('button', name='Retry suggestions', exact=True)
            expect(retry).to_have_class('button')
            expect(retry.locator('svg[data-glyph="refresh"]')).to_be_visible()
            retry.click()
            expect(root.get_by_text('Waiting for the Assistant', exact=True)).to_be_visible()
            expect(root.locator('[data-followup-retry]')).to_have_count(0)
            refreshed = ScriptedEngine(initial)
            with patch('assistant.conversations.engine_for', return_value=refreshed):
                assert fixture.suggestions.dispatch_one()
                fixture.wait()
            assert 'Use my saved guidance without opening external websites.' in refreshed.prompt
            new_run = fixture.listing()['items'][0]['suggestions']
            assert new_run['policy_revision'] == saved.json['revision'] and not new_run['policies_changed']
            old_actions = [meeting['id'], reply['id']]
            meeting, reply = new_run['actions']
            assert all(action['id'] not in old_actions for action in new_run['actions'])
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            expect(root.locator(f'[data-assistant-approve="{meeting["id"]}"]')).to_be_visible()
            expect(root.locator(f'[data-assistant-approve="{reply["id"]}"]')).to_have_text('Send')
            # FOLLOW-UP CLARITY: the plan names the steps in order; each card says what it does and for whom, its button says what
            # it does, and the exact request stays folded.
            expect(root.locator('.support-task-plan > .support-task-list li strong')).to_have_text(['Schedule office hours', 'Send an email'])
            proposal = root.locator(f'[data-action-id="{meeting["id"]}"]')
            expect(proposal.locator('.assistant-card-head strong')).to_have_text('Schedule office hours')
            expect(proposal.locator('.support-proposal-meta')).to_contain_text('(this student)')
            expect(proposal.locator('.support-changes')).to_contain_text('Mon, Oct 12, 2:00 PM–2:30 PM')
            expect(proposal.locator('.support-follows')).to_contain_text('The student is not told')
            expect(proposal.locator('.support-exact')).not_to_have_attribute('open', '')
            expect(root.locator(f'[data-assistant-approve="{meeting["id"]}"]')).to_have_text('Schedule')
            expect(root.locator(f'[data-action-id="{reply["id"]}"] .assistant-card-head strong')).to_have_text('Send an email')
            assert google.sent == []
            # APPROVING (requirement, 2026-10-06): a refresh that lands while Approve all is still running shows its steps as
            # Approving…, with no second Approve all; a request that never arrives puts them back as waiting.
            held, refresh = [], 'window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));'
            page.route('**/api/student-support/followups/suggestions/*/approve', lambda route: held.append(route))
            decision = root.locator('.support-review .support-decision')
            root.locator('[data-followup-approve-all]').click()
            expect(root.locator(f'[data-action-id="{meeting["id"]}"] .chip')).to_have_text('Approving…')
            page.evaluate(refresh)
            page.wait_for_timeout(450)
            expect(root.locator(f'[data-action-id="{reply["id"]}"] .chip')).to_have_text('Approving…')
            expect(root.locator('.support-review :is([data-followup-approve-all], [data-assistant-approve], [data-assistant-decline], .support-decision [data-support-write])')).to_have_count(0)
            expect(decision).to_contain_text('Approving 2 steps…')
            expect(root.locator('.support-queue-row[aria-pressed="true"]')).to_contain_text('Approving 2 steps…')
            assert len(held) == 1 and google.sent == []
            held.pop().abort()
            page.unroute('**/api/student-support/followups/suggestions/*/approve')
            expect(root.locator(f'[data-assistant-approve="{meeting["id"]}"]')).to_be_visible()
            expect(root.locator('[data-followup-approve-all]')).to_be_visible()
            # One step's own Approve: it reads Approving… through a refresh while the other step still waits, then Done.
            page.route('**/api/assistant/actions/*/approve', lambda route: held.append(route))
            root.locator(f'[data-assistant-approve="{meeting["id"]}"]').click()
            expect(root.locator(f'[data-action-id="{meeting["id"]}"] .chip')).to_have_text('Approving…')
            page.evaluate(refresh)
            page.wait_for_timeout(450)
            expect(root.locator(f'[data-assistant-approve="{meeting["id"]}"]')).to_have_count(0)
            expect(root.locator(f'[data-assistant-approve="{reply["id"]}"]')).to_have_text('Send')
            expect(decision).to_contain_text('Approving one step…')
            assert len(held) == 1
            held.pop().continue_()
            page.unroute('**/api/assistant/actions/*/approve')
            expect(root.locator(f'[data-action-id="{meeting["id"]}"] .chip')).to_have_text('Done')
            page.locator('.assistant-launch').click()
            panel = page.locator('#assistant-panel')
            panel.locator('[data-assistant-tab="changes"]').click()
            expect(panel.locator(f'[data-assistant-approve="{reply["id"]}"]')).to_have_text('Send')
            panel.locator(f'[data-assistant-decline="{reply["id"]}"]').click()
            expect(panel.locator(f'[data-action-id="{reply["id"]}"] .chip')).to_have_text('Declined')
            page.keyboard.press('Control+i')
            expect(root.locator(f'[data-action-id="{reply["id"]}"] .chip')).to_have_text('Declined')
            # FINISHED STEPS: nothing waits, so the decided cards fold into one counted line, closed, cards kept inside.
            expect(root.locator('.support-finished > summary')).to_have_text('1 step done · 1 declined')
            assert not root.locator('.support-finished').evaluate('(e)=>e.open')
            expect(root.locator('.support-evidence-warning')).to_have_count(0)
            assert google.sent == []
            # Quiet successful and failed refreshes keep the instructor's draft, focus and disclosure.
            root.locator('.support-revise summary').click()
            form = root.locator('[data-followup-revise]')
            field = form.locator('textarea')
            field.fill('Make the reply shorter, without scheduling another appointment.')
            field.focus()
            page.evaluate('window.keptField=document.activeElement; window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            page.wait_for_timeout(450)
            expect(field).to_have_value('Make the reply shorter, without scheduling another appointment.')
            assert page.evaluate('document.activeElement===window.keptField')
            assert root.locator('.support-revise').evaluate('(e)=>e.open')
            page.route('**/api/student-support?kind=followup&suggestions=1&brief=1', lambda route: route.fulfill(status=503,json={'error':'Synthetic refresh interruption'}), times=1)
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            expect(root.get_by_text('Refresh could not finish:',exact=False)).to_be_visible()
            expect(field).to_have_value('Make the reply shorter, without scheduling another appointment.')
            assert page.evaluate('document.activeElement===window.keptField')
            # A FOLLOW-UP'S "REMEMBER THIS" (ASSISTANT-MEMORY, 2026-10-10: "make the follow-up's Remember this do the same"): what was
            # typed leans on this follow-up, so the rule a scripted model writes is kept, and the page says exactly what, with Undo.
            from jev import client as jev_client
            def judged(state, questions):
                yes = ('lasting', 'own_words', 'subject', 'says') + (('standalone',) if 'rewrite' in state else ())
                return {'model': jev_client.MODEL, 'usage': {'input_tokens': 1, 'output_tokens': 0}, 'answers': {
                    name: {'type': 'choice', 'choice': next(iter(q['criteria'])), 'confidence': 1.0, 'probabilities': {o: float(i == 0) for i, o in enumerate(q['criteria'])}}
                    if q['type'] == 'choice' else {'type': 'noul', 'noul': .95 if name in yes else .03} for name, q in questions.items()}}
            class Writer:
                def submit(self, request):
                    assert request['prompt'] == 'memory_rule' and 'open_item' in request['payload'], request
                    return {'state': 'completed', 'result': {'output': {'resolved': True, 'rewrite': 'Keep replies to a student short, with no second appointment.'}}}
            form.locator('[name="remember"]').check()
            with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=judged), \
                    patch.object(fixture.app.extensions['frq_grading'], 'cloud_provider', return_value=Writer()):
                form.get_by_role('button',name='Ask for changes',exact=True).click()
                kept = page.locator('.toast-item', has_text='Remembered for future drafts')
                expect(kept).to_contain_text('“Keep replies to a student short, with no second appointment.” I wrote this from what you typed, without the student.', timeout=15000)
            standing = lambda: [m['content'] for m in fixture.client.get('/api/assistant/memory').json['memories'] if m['status'] == 'current']
            assert standing() == ['Keep replies to a student short, with no second appointment.'], standing()
            kept.get_by_role('button', name='Undo', exact=True).click()
            expect(page.locator('.toast-item', has_text='Taken back')).to_be_visible()
            assert standing() == [], standing()
            expect(root.get_by_text('Waiting for the Assistant',exact=True)).to_be_visible()
            with patch('assistant.conversations.engine_for',return_value=ScriptedEngine(mail)):
                assert fixture.suggestions.dispatch_one()
                fixture.wait()
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            expect(root.get_by_role('button',name='Send',exact=True)).to_be_visible()
            replacement = fixture.listing()['items'][0]['suggestions']['actions'][0]
            assert replacement['id'] != reply['id']
            with connect(fixture.assistant.database) as db:
                assert db.execute('SELECT COUNT(*) FROM student_support_items').fetchone()[0] == 1
            # Desktop only, one appearance (DESIGN-LANGUAGE, 2026-10-03): the narrowest desktop window does not scroll sideways.
            page.set_viewport_size({'width':1280,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(Path.cwd()/'followup-suggestions-1280.png'),full_page=True)
            root.get_by_role('button',name='Send',exact=True).click()
            expect(root.locator(f'[data-action-id="{replacement["id"]}"] .chip')).to_have_text('Done')
            expect(root.locator('.support-finished > summary')).to_have_text('1 step done')
            assert len(google.sent) == 1
            page.locator('.assistant-launch').click()
            panel.locator('[data-assistant-tab="changes"]').click()
            expect(panel.locator(f'[data-action-id="{replacement["id"]}"] .chip')).to_have_text('Done')
            # FOLLOW-UP REVIEW: Changes lists only follow-ups that wait for you; this one has nothing left there.
            expect(panel.locator('[data-followup-revise]')).to_have_count(0)
            page.keyboard.press('Control+i')
            # Existing clarification card supplies information and produces another proposal, with no approval implied.
            root.locator('.support-revise summary').click()
            root.locator('[data-followup-revise] textarea').fill('Prepare another follow-up, but ask about the time first.')
            root.locator('[data-followup-revise]').get_by_role('button',name='Ask for changes',exact=True).click()
            expect(root.get_by_text('Waiting for the Assistant',exact=True)).to_be_visible()
            def ask(c, _):
                result,event=c.service.run_tool(c.session,'ask_question',{'questions':[{'title':'Which time?','options':['2 pm','3 pm']}]})
                c.tool_event(event)
            with patch('assistant.conversations.engine_for',return_value=ScriptedEngine(ask)):
                assert fixture.suggestions.dispatch_one()
                fixture.wait()
            page.locator('.assistant-launch').click()
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            question=panel.locator('[data-assistant-question]')
            expect(question).to_be_visible()
            expect(question.locator('textarea')).to_have_attribute('placeholder', 'Or write your own answer')
            question.locator('textarea').fill('Friday at 3 pm')
            question.locator('textarea').focus()
            page.evaluate('window.keptChangeAnswer=document.activeElement; window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            page.wait_for_timeout(350)
            expect(question.locator('textarea')).to_have_value('Friday at 3 pm')
            assert page.evaluate('document.activeElement===window.keptChangeAnswer')
            question.get_by_role('button',name='Answer and continue',exact=True).click()
            expect(root.get_by_text('Waiting for the Assistant',exact=True)).to_be_visible()
            page.keyboard.press('Control+i')
            with patch('assistant.conversations.engine_for',return_value=ScriptedEngine(ask)):
                assert fixture.suggestions.dispatch_one()
                fixture.wait()
            page.evaluate('window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));')
            question=root.locator('[data-assistant-question]')
            expect(question).to_be_visible()
            expect(root.locator('[data-followup-retry]')).to_be_visible()
            # FOLLOW-UP CLARITY: the open question comes before any step it may replace.
            order = root.locator('.support-review .support-suggestions > *').evaluate_all(
                'nodes => nodes.map(n => n.matches(".support-ask") ? "ask" : n.matches("[data-action-id]") ? "card" : "")')
            assert 'ask' in order and ('card' not in order or order.index('ask') < order.index('card')), order
            # Choosing an answer sends it (requirement, 2026-10-04).
            question.get_by_role('button',name='3 pm',exact=True).click()
            expect(root.get_by_text('Waiting for the Assistant',exact=True)).to_be_visible()
            with connect(fixture.assistant.database) as db:
                answered = db.execute("SELECT instruction FROM student_support_suggestion_runs ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            assert '3 pm' in answered and 'Friday' not in answered, answered
            assert len(google.sent) == 1
            assert not errors, errors
            assert page.evaluate('window.cspViolations') == []
            browser.close()
    finally:
        if server:
            server.close()
        fixture.doCleanups()
    print('Follow-up approval, Send, decline, revision, clarification, quiet refresh and the 1280px desktop layout passed.')


if __name__ == '__main__':
    main()
