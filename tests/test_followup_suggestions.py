"""ASSISTANT / TEACHING-WORKFLOWS: automatic preparation, exact human approvals and durable recovery.

Synthetic catalogs and scripted engines only. No configured model, mailbox or outside service is reached.
"""
import json
import threading
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


from assistant import policy
from communications.record import record
from rosters import connect
from test_assistant import settle
from test_storage import HEADERS
from test_jev import response
from test_student_notes import Base


class ScriptedEngine:
    name = 'scripted'

    def available(self):
        return True

    def why_unavailable(self):
        return ''

    def __init__(self, respond=None):
        self.respond = respond
        self.closed = False

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def turn(self, c, prompt):
        self.prompt = prompt
        if self.respond:
            self.respond(c, prompt)
        else:
            result, event = c.service.run_tool(c.session, 'faculty_get', {'path': '/api/student-support',
                'query': {'enrollment_id': c.run['enrollment_id'], 'kind': 'followup'}})
            c.tool_event(event)
            result, event = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/student-support',
                'body': {'enrollment_id': c.run['enrollment_id'], 'kind': 'appointment', 'title': 'Discuss the question',
                         'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30', 'request_id': '$new_request_id'},
                'summary': 'Schedule office hours to discuss the question'})
            assert 'action_id' in json.loads(result), result
            c.tool_event(event)
            c.emit('assistant', text='Review the office-hours appointment. Nothing has been scheduled yet.')


REPLY = 'Yes, come to office hours on Monday at 2 pm and bring the problem you are stuck on, and we will work through it.'
MAIL = ('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': REPLY}, 'Reply to the question')
MEETING = ('/api/student-support', lambda c: {'enrollment_id': c.run['enrollment_id'], 'kind': 'appointment', 'title': 'Discuss the question',
           'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30'}, 'Schedule office hours to discuss the question')


def proposing(*writes, text='Review these proposals.'):
    """A scripted engine turn that proposes each (path, body, summary) in order and keeps every tool outcome."""
    def respond(c, prompt):
        for path, body, summary in writes:
            body = body(c) if callable(body) else body
            result, event = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': path,
                'body': {**body, 'request_id': '$new_request_id'}, 'summary': summary})
            c.tool_event(event)
            respond.outcomes.append(json.loads(result))
        c.emit('assistant', text=text)
    respond.outcomes = []
    return respond


class SuggestionsTests(Base):
    def test_work_owed_is_distinct_from_reply_and_changed_evidence_is_rechecked(self):
        self.session(body='Please check my grade.')
        item = self.listing()['items'][0]
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=lambda s, q:
                response(q, nouls={'owed': .01, 'work_owed': .99})) as calls:
            self.suggestions._ask_owed(item)
            self.suggestions._ask_owed(item)
            self.assertEqual(calls.call_count, 1)
        with connect(self.assistant.database) as db:
            found = self.suggestions._obligation(db, item)
        self.assertEqual(found, {'state': 'owed', 'reply_owed': False, 'work_owed': True})
        self.incoming('done', 'I checked and corrected it.', author='instructor', at='2026-10-03T13:00:00Z')
        with connect(self.assistant.database) as db:
            self.assertEqual(self.suggestions._owed(db, item), 'pending')

    # -- The 2026-10-09 review: kept verifications, private notes, a clock-free obligation, edits during a run ----------------------

    def test_the_evidence_check_and_the_tool_use_report_are_both_kept(self):
        # JEV EVIDENCE: the tool-use report, emitted after the evidence check, used to replace it, so the EVIDENCE WARNING never fired.
        self.fake_mail()
        self.incoming(body='Could I meet you on Monday?')
        self.assertTrue(self.suggestions.process_one())
        contradicted = {'status': 'review', 'detail': 'A claim contradicts a source.', 'sources': [],
                        'checks': [{'paragraph': 1, 'verdict': 'contradicted', 'text': 'Monday is open.'}]}
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)), \
                patch('jev.checks.evidence', return_value=contradicted), \
                patch('assistant.conversations.engine_for', return_value=ScriptedEngine(proposing(MAIL, text='Monday is open.'))):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        found = self.listing()['items'][0]['suggestions']
        self.assertEqual([v.get('check_type', 'evidence') for v in found['verification']], ['evidence', 'tool_use'])
        self.assertEqual(found['verification'][0]['checks'][0]['verdict'], 'contradicted')  # what raises the warning

    def test_private_notes_reach_the_preparation_but_never_a_jev_state(self):
        # STUDENT-NOTES "Jev does not read them": not the obligation, draft, evidence or tool-use checks.
        self.fake_mail()
        made = self.client.post('/api/student-notes', json={'enrollment_id': self.alice, 'date': '2026-10-01',
            'text': 'PRIVATE-NOTE-MARKER family matter', 'request_id': 'private-note'}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        self.incoming(body='I cannot come on Monday. Is Tuesday possible?')
        self.assertTrue(self.suggestions.process_one())
        states = []
        def judge(state, questions):
            states.append(json.dumps(state))
            return response(questions)
        def respond(c, prompt):
            result, event = c.service.run_tool(c.session, 'student_context', {'enrollment_id': c.run['enrollment_id'], 'source_key': c.run['source_key']})
            c.tool_event(event)
            self.assertIn('PRIVATE-NOTE-MARKER', result)  # the model itself reads them
            proposing(MAIL, text='I drafted a reply.')(c, prompt)
        engine = ScriptedEngine(respond)
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=judge), \
                patch('assistant.conversations.engine_for', return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
            self.suggestions._ask_owed(self.listing()['items'][0])
        self.assertIn('PRIVATE-NOTE-MARKER', engine.prompt)  # the preparation's prompt keeps them
        purposes = ' '.join(states)
        for marker in ('"draft"', '"absences"', 'observed_tools', 'instructor_question', '/context'):
            self.assertIn(marker, purposes)  # the draft, obligation, tool-use and evidence checks all ran, the case read among them
        self.assertTrue(states)
        self.assertFalse([state for state in states if 'PRIVATE-NOTE-MARKER' in state])
        with connect(self.assistant.database) as db:
            run = self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id'])
        given = self.suggestions.evidence_sources(run, self.suggestions.prompt(run))[0]['text']
        self.assertIn("Facts from Faculty's records", given)
        self.assertNotIn('PRIVATE-NOTE-MARKER', given)

    def test_the_obligation_check_is_clock_free_and_a_listing_builds_no_facts(self):
        # TO DO THANKS v3: meeting hours' open times, the rolling window and iClicker lines moved the v2 fingerprint with the clock.
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from jev.service import fingerprint
        from assistant.followup_checks import obligation
        saved = self.client.post(f'/api/student-support/meeting-hours/{self.term}', json={'windows': [
            {'weekday': 1, 'start': '13:00', 'end': '17:00', 'location': ''}], 'makeup_rooms': [], 'base_revision': 0,
            'request_id': 'hours'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.session(body='Please check my grade.')
        item = self.listing()['items'][0]
        tracked = self.client.post('/api/student-support', json={'request_id': 'todo', 'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Grade check', 'state': 'todo',
            'next': 'Check the grade'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        item = next(i for i in self.listing()['items'] if i['id'] == tracked.json['id'])
        zone = ZoneInfo('America/Chicago')
        prints, facts = [], []
        for moment in (datetime(2026, 10, 5, 9, 0, tzinfo=zone), datetime(2026, 10, 6, 15, 0, tzinfo=zone), datetime(2026, 10, 7, 9, 0, tzinfo=zone)):
            with patch('absences.makeup_booking.campus_now', return_value=moment), \
                    patch('scheduling.clock.campus_today', return_value=moment.date()), connect(self.assistant.database) as db:
                prints.append(fingerprint(obligation(db, item)))
                run = {'enrollment_id': self.alice, 'item_key': 'conversation:' + item['source_key'],
                       'source_json': json.dumps(self.suggestions.source(db, self.alice, 'conversation:' + item['source_key']))}
                term = db.execute('SELECT * FROM course_terms WHERE id=?', (self.term,)).fetchone()
                facts.append(self.suggestions._record_facts(db, run, term))
        self.assertGreater(len(set(facts)), 1)  # the draft's facts do move with the clock
        self.assertEqual(len(set(prints)), 1)  # the obligation's inputs do not
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=lambda s, q:
                response(q, nouls={'owed': .99, 'work_owed': .99})) as calls:
            self.suggestions._ask_owed(item)
            with patch('absences.makeup_booking.campus_now', return_value=datetime(2026, 10, 8, 14, 0, tzinfo=zone)):
                self.suggestions.close_acknowledged()
                with patch.object(self.suggestions, '_record_facts', side_effect=AssertionError('a listing built FOLLOW-UP FACTS')):
                    shown = next(i for i in self.listing()['items'] if i['id'] == tracked.json['id'])
            self.assertEqual(calls.call_count, 1)  # asked once, a day later still answered
        self.assertEqual(shown['suggestions']['obligation']['state'], 'owed')
        with connect(self.assistant.database) as db:
            rule = db.execute("SELECT rule FROM jev_calls WHERE purpose='followup-thanks'").fetchone()[0]
        self.assertEqual(rule, 'todo-thanks-v3')

    def test_an_edit_while_the_preparation_runs_is_refused_and_the_draft_check_skips_your_edit(self):
        # EDIT IN PLACE: the draft check at the end of a turn withdrew an edit made during it and put a model repair in its place.
        from assistant.followup_checks import draft_check
        self.fake_mail()
        self.incoming(body='I cannot come on Monday. Is Tuesday possible?')
        self.assertTrue(self.suggestions.process_one())
        refused = []
        def generate(c, prompt):
            result = self.tool_write(c.session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Meeting', 'body': 'Come on Monday.'})
            action = self.assistant._view(*self.assistant.action(result['action_id']))
            edit = self.client.post(f"/api/student-support/followups/suggestions/{c.run['id']}/edit", json={'action_id': action['id'],
                'seen': action['seen'], 'subject': 'Meeting', 'body': 'Tuesday at 2 works.', 'request_id': uuid.uuid4().hex}, headers=HEADERS)
            refused.append((edit.status_code, edit.json.get('error', '')))
            c.emit('assistant', text='Review the reply.')
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(generate)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertEqual(refused[0][0], 409)
        self.assertIn('still being prepared', refused[0][1])
        run = self.listing()['items'][0]['suggestions']
        mail = next(a for a in run['actions'] if a['pending'])
        edited = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={'action_id': mail['id'],
            'seen': mail['seen'], 'subject': 'Meeting', 'body': 'Tuesday at 2 works.', 'request_id': uuid.uuid4().hex}, headers=HEADERS)
        self.assertEqual(edited.status_code, 200, edited.json)
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=lambda state, qs:
                response(qs, nouls={'contradicts_request': .99})) as calls, connect(self.assistant.database) as db:
            review = draft_check(self.suggestions, self.suggestions.get(db, run['id']))
            self.assertEqual(review['status'], 'not_needed')  # your version is yours to send
            self.assertEqual(calls.call_count, 0)
            self.assertEqual(self.suggestions.withdraw_draft_messages(self.suggestions.get(db, run['id'])), [])
        self.assertTrue(self.view(edited.json['action_id'])['pending'])

    def test_draft_check_revises_once_and_keeps_both_versions_unsent(self):
        self.fake_mail()
        self.incoming(body='I cannot come on Monday. Is Tuesday possible?')
        self.assertTrue(self.suggestions.process_one())
        turns = []
        def generate(c, prompt):
            turns.append(prompt)
            text = 'Come on Monday.' if len(turns) == 1 else 'I will check Tuesday availability and get back to you.'
            result = self.tool_write(c.session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Meeting', 'body': text})
            self.assertIn('action_id', result, result)
            c.emit('assistant', text='Review the reply.')
        checks = []
        def judge(state, questions):
            if 'contradicts_request' in questions:
                checks.append(state['draft'])
            return response(questions, nouls={'contradicts_request': .98 if state.get('draft') == 'Come on Monday.' else .01,
                                             'unsupported_promise': .01})
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=judge), \
             patch('assistant.conversations.engine_for', return_value=ScriptedEngine(generate)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertEqual(len(turns), 2)
        self.assertEqual(len(checks), 2)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_mail_sends').fetchone()[0], 0)
        found = self.listing()['items'][0]['suggestions']
        mails = [a for a in found['actions'] if a['endpoint'] == 'gmail_mail.send']
        self.assertEqual(sum(a['pending'] for a in mails), 1)
        self.assertEqual(found['draft_review']['warnings'], [])

    def test_recovery_review_never_runs_an_automatic_write(self):
        from assistant.review_context import prompt
        self.assertIn('Never retry uncertain', prompt({'kind': 'recovery', 'page': '#overview'}))
        self.assertIn('never push, send, publish or delete', prompt({'kind': 'canvas-conflict',
            'preview_path': '/api/canvas/content/term/preview', 'evidence': {'key': 'page:p'}}))
        for invalid in ({'kind': 'recovery', 'page': 'https://evil.test'}, {'kind': 'canvas-conflict',
                        'preview_path': '/api/mail/send', 'evidence': {}}, {'kind': 'unknown'}):
            with self.assertRaises(ValueError):
                prompt(invalid)
        session = self.assistant.open_session('test')
        session['_review_only'] = True
        result = self.tool_write(session, '/api/calendar/events', {'title': 'Meeting', 'date': '2026-10-03', 'time': '14:00'})
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0], 0)

    def test_review_context_is_ephemeral_and_review_mode_survives_continuation(self):
        from assistant.conversations import Conversation
        seen = []
        engine = ScriptedEngine(lambda c, text: seen.append((text, c.session.get('_review_only'))))
        with patch('assistant.conversations.engine_for', return_value=engine):
            session = self.assistant.open_session('test')
            c = Conversation(self.assistant, session, {'name': 'Synthetic'}, '', '#canvas')
        self.addCleanup(c.close)
        def wait():
            for _ in range(500):
                if not c.busy: return
                time.sleep(.01)
            self.fail('The synthetic review did not finish')
        c.send('Resolve this conflict.', review_only=True, review_context={'kind': 'canvas-conflict',
               'preview_path': '/api/canvas/content/t/preview', 'evidence': {'text': 'ephemeral comparison'}})
        wait()
        self.assertIn('ephemeral comparison', seen[0][0])
        self.assertTrue(seen[0][1])
        self.assertNotIn('ephemeral comparison', json.dumps(c.snapshot()))
        self.assertTrue(c.snapshot()['review_only'])
        c.send('Continue from the approved receipt.', internal=True)
        wait()
        self.assertTrue(seen[-1][1])
        c.send('An ordinary new request.')
        wait()
        self.assertFalse(seen[-1][1])

    def test_reply_revision_stops_after_one_pass_even_when_a_warning_remains(self):
        self.fake_mail()
        self.incoming(body='I cannot attend on Monday.')
        self.assertTrue(self.suggestions.process_one())
        turns = []
        def generate(c, prompt):
            turns.append(prompt)
            if len(turns) == 2:
                rejected = self.tool_write(c.session, '/api/calendar/events', {'title': 'Unrequested', 'date': '2026-10-03', 'time': '14:00'})
                self.assertIn('error', rejected)
            result = self.tool_write(c.session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Meeting',
                                      'body': 'Come on Monday.' if len(turns) == 1 else 'Please come on Monday.'})
            self.assertIn('action_id', result)
            c.emit('assistant', text='Review the reply.')
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=lambda s, q:
                response(q, nouls={'contradicts_request': .99, 'unsupported_promise': .01})), \
             patch('assistant.conversations.engine_for', return_value=ScriptedEngine(generate)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        found = self.listing()['items'][0]['suggestions']
        self.assertEqual(len(turns), 2)
        self.assertEqual(len(found['draft_review']['warnings']), 1)
        self.assertEqual(sum(a['pending'] for a in found['actions']), 1)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_mail_sends').fetchone()[0], 0)

    def setUp(self):
        super().setUp()
        self.addCleanup(settle)  # a chat's turn (not a preparation, which `wait` covers) has ended before the folder is removed
        self.assistant = self.app.extensions['assistant']
        self.support = self.app.extensions['student_support']
        self.suggestions = self.assistant.followups
        self.suggestions.interval = 0  # every synthetic turn reads the queue (production: one idle read per INTERVAL)
        self.suggestions.automatic = False  # DONE FOR YOU is tested on its own; elsewhere every write stays a card
        self.model = patch('assistant.conversations.config.assistant', return_value={'kind': 'hosted', 'name': 'Scripted', 'reasoning_choice': 'high'})
        self.model.start()
        self.addCleanup(self.model.stop)
        self.addCleanup(self.wait)

    def incoming(self, ident='a1', body='Could I meet you?', thread='b1', enrollment=None, at='2026-10-03T12:00:00Z', author='student'):
        with self.rosters_lock(), connect(self.assistant.database) as db:
            record(db, 'gmail', ident, {'sent_at': at, 'author_role': author,
                'author': {'name': 'Alice'}, 'recipients': [], 'subject': 'Question', 'body_text': body, 'attachments': [],
                'context': {'thread_id': thread}}, [(enrollment or self.alice, self.term, 'author' if author == 'student' else 'to', 'roster_address')], thread)
            db.commit()

    def rosters_lock(self):
        return self.app.extensions['rosters'].lock()

    def listing(self, suggestions=True):
        response = self.client.get('/api/student-support?kind=followup' + ('&suggestions=1' if suggestions else ''))
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def prepare(self, respond=None):
        self.assertTrue(self.suggestions.process_one())
        engine = ScriptedEngine(respond)
        with patch('assistant.conversations.engine_for', return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        return self.listing()['items'][0]['suggestions'], engine

    def wait(self):
        until = time.monotonic() + 10
        while self.suggestions.busy() and time.monotonic() < until:
            time.sleep(.01)
        self.assertEqual(self.suggestions.busy(), 0)

    def approve(self, action):
        return self.client.post('/api/assistant/actions/' + action['id'] + '/approve', json={'seen': action['seen']}, headers=HEADERS)

    def fake_mail(self):
        from test_gmail_mail import ACCOUNT, FakeGmail, FakeTokens
        from response_cards.gmail import SCOPES, MAIL_SCOPES
        mail = self.app.extensions['gmail_mail']
        root = Path(self.temp.name)
        mail.google.client_file, mail.google.token_file = str(root / 'client.json'), str(root / 'token.json')
        Path(mail.google.client_file).write_text(json.dumps({'web': {'client_id': 'fake.apps.googleusercontent.com', 'client_secret': 'synthetic'}}))
        Path(mail.google.token_file).write_text(json.dumps({'refresh_token': 'synthetic', 'email': ACCOUNT, 'scopes': sorted(set(SCOPES) | set(MAIL_SCOPES))}))
        fake = FakeGmail()
        mail.client.http, mail.google.http = fake, FakeTokens()
        return fake

    def request(self, run, operation, **data):
        return self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/{operation}",
            json={'request_id': 'request-' + operation, 'seen': run['seen'], 'fingerprint': run['fingerprint'], **data}, headers=HEADERS)

    def test_current_queue_is_prepared_once_without_saving_or_sending(self):
        self.incoming()
        default = self.client.get('/api/student-support?kind=followup').data
        run, engine = self.prepare()
        self.assertEqual(run['state'], 'ready')
        self.assertEqual(len(run['actions']), 1)
        self.assertEqual(self.support.attention()['followup'], 1)  # needs a reply and has a card waiting: counted once
        self.assertTrue(engine.closed)
        self.assertFalse(self.suggestions.process_one())
        self.assertEqual(self.client.get('/api/student-support?kind=followup').data, default)
        self.assertEqual(self.assistant.conversations.busy(), 0)
        self.assertTrue(run['evidence'])
        with connect(self.assistant.database) as db:
            for table in ('student_support_items', 'gmail_mail_sends', 'assistant_saved_conversations', 'gradebook_scores'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
            source = db.execute('SELECT source_json FROM student_support_suggestion_runs').fetchone()[0]
            self.assertNotIn('Could I meet you?', source)
        card = run['actions'][0]
        self.assertEqual(card['tier'], 'approve')
        self.assertEqual(self.client.get('/api/assistant/actions').json['pending'][0]['id'], card['id'])
        approved = self.approve(card)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(approved.json['action']['status'], 'succeeded')
        self.assertEqual(self.approve(card).status_code, 409)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_items').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT actor FROM student_support_items').fetchone()[0], 'assistant')

    def test_duplicate_worker_turns_and_dispatcher_lifetime_lock_are_exclusive(self):
        self.incoming()
        from assistant.followups import FollowupSuggestions
        other = FollowupSuggestions(self.assistant, self.support)
        with ThreadPoolExecutor(max_workers=2) as pool:
            turns = list(pool.map(lambda s: s.process_one(), [self.suggestions, other]))
        self.assertEqual(sorted(turns), [False, True])
        with self.suggestions.execution_lock():
            with self.assertRaises(BlockingIOError), other.execution_lock():
                pass
        with other.execution_lock():
            pass

    def test_proposal_only_promotes_chat_automatic_routes_and_keeps_page_restrictions(self):
        session = self.assistant.open_session('test')
        session['_proposal_only'] = True  # Only the server assigns this; the public session route cannot set it.
        result = self.tool_write(session, '/api/calendar/events', {'title': 'Meeting', 'date': '2026-10-03', 'time': '14:00'})
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM calendar_events').fetchone()[0], 0)
        public = self.client.post('/api/assistant/sessions', json={'engine':'test','_proposal_only':True}, headers=HEADERS)
        self.assertNotIn('_proposal_only', self.assistant.session(public.json['id'], public.json['token']))
        self.assertEqual(self.tool_write(session, '/api/assistant/actions/fake/approve', {'seen':1})['outcome'], 'refused')
        self.assertEqual(self.tool_write(session, '/api/grading/assignments/fake/certification', {})['outcome'], 'use_the_page')

    def test_edited_or_new_message_invalidates_both_approval_locations(self):
        self.incoming()
        run, _ = self.prepare()
        old = run['actions'][0]
        self.incoming(body='Actually, I need Thursday.')  # Same message, new revision; no timestamp change.
        self.assertEqual(self.approve(old).status_code, 409)
        self.assertEqual(self.client.get('/api/assistant/actions').json['pending'], [])
        self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'superseded')
        self.assertTrue(self.suggestions.process_one())
        self.assertNotEqual(self.listing()['items'][0]['suggestions']['id'], run['id'])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_items').fetchone()[0], 0)

    def test_decline_does_not_automatically_regenerate_and_explicit_revision_replaces_only_pending(self):
        self.incoming()
        run, _ = self.prepare()
        card = run['actions'][0]
        self.client.post('/api/assistant/actions/' + card['id'] + '/decline', json={}, headers=HEADERS)
        self.suggestions.process_one()
        self.assertEqual(self.listing()['items'][0]['suggestions']['id'], run['id'])
        run = self.listing()['items'][0]['suggestions']
        response = self.request(run, 'revise', text='Suggest Friday instead.')
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(self.request(run, 'revise', text='Suggest Friday instead.').json, response.json)
        self.assertEqual(self.request(run, 'revise', text='Different text').status_code, 409)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine()):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertEqual(len(self.listing()['items'][0]['suggestions']['actions']), 1)

    def test_clarification_survives_restart_and_answer_is_not_approval(self):
        def ask(c, _):
            result, event = c.service.run_tool(c.session, 'ask_question', {'questions': [{'title': 'What time?', 'options': ['2 pm', '3 pm']}]})
            c.tool_event(event)
            self.assertEqual(json.loads(result)['outcome'], 'waiting_for_input')
        self.incoming()
        run, _ = self.prepare(ask)
        self.assertEqual(run['state'], 'needs_input')
        self.suggestions.recover_interrupted()
        self.assertEqual(self.listing()['items'][0]['suggestions']['question'], run['question'])
        response = self.request(run, 'answer', question_id=run['question']['id'], answers={'0': '3 pm on Friday'})
        self.assertEqual(response.status_code, 202, response.json)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_items').fetchone()[0], 0)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine()) as factory:
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
            self.assertIn('3 pm on Friday', factory.return_value.prompt)

    def test_background_busy_and_interactive_priority_and_exclusive_claim(self):
        self.incoming()
        self.assertTrue(self.suggestions.process_one())
        with patch.object(self.assistant.conversations, 'interactive_busy', return_value=1):
            self.assertFalse(self.suggestions.dispatch_one())
        release = threading.Event()
        self.addCleanup(release.set)
        def hold(c, _):
            release.wait(5)
            c.emit('assistant', text='Prepared.')
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(hold)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda _: self.suggestions.dispatch_one(), range(2)))
            self.assertEqual(sorted(outcomes), [False, True])
            self.assertEqual(self.client.get('/api/assistant/status').json['busy'], 1)
            with patch('assistant.conversations.config.assistant', return_value=None):
                self.assertEqual(self.client.get('/api/assistant/status').json['busy'], 1)
            release.set()
            self.wait()

    def test_queue_respects_cutoff_saved_states_due_dates_and_manual_items(self):
        self.incoming(at='2026-10-01T12:00:00Z')
        preview = self.support.start_now_preview({'through': '2026-10-02T12:00:00Z'})
        self.support.start_now({**preview, 'confirmation': 'start-now', 'request_id': 'reset'})
        self.assertFalse(self.suggestions.process_one())
        self.support.save({'kind': 'followup', 'title': 'Later', 'state': 'snoozed', 'date': '2099-10-03', 'enrollment_id': self.alice, 'request_id': 'later'})
        self.assertFalse(self.suggestions.process_one())
        manual = self.support.save({'kind': 'followup', 'title': 'Manual commitment', 'state': 'needs_reply', 'enrollment_id': self.alice, 'request_id': 'manual'})
        self.assertTrue(self.suggestions.process_one())
        run = next(i['suggestions'] for i in self.listing()['items'] if i['id'] == manual['id'])
        self.support.save({'base_revision': 1, 'state': 'resolved', 'title': 'Manual commitment', 'request_id': 'resolve'}, manual['id'])
        self.suggestions.process_one()
        with connect(self.assistant.database) as db:
            self.assertEqual(self.suggestions.get(db, run['id'])['state'], 'superseded')

    def test_fyi_suppresses_generation_and_a_new_request_becomes_eligible(self):
        from test_jev import response
        from student_support.triage import QUESTIONS
        self.incoming(body='Thanks, that answers it.')
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate',
                return_value=response(QUESTIONS, {'kind':'acknowledgment'}, {'needs_action':.0})) as evaluate:
            # ANALYSIS FIRST: nothing is prepared while Jev reads the thank-you, and nothing after it lands under FYI.
            self.assertFalse(self.suggestions.process_one())
            self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'analyzing')
            self.assertTrue(self.support.triage.process_one())
            self.assertEqual(self.listing()['items'][0]['state'], 'fyi')
            self.assertFalse(self.suggestions.process_one())
            self.incoming('a2', body='Actually, I need to meet you.', at='2026-10-03T13:00:00Z')
            self.assertFalse(self.suggestions.process_one())
            evaluate.return_value = response(QUESTIONS)
            self.support.triage._last = 0
            self.assertTrue(self.support.triage.process_one())
            self.assertTrue(self.suggestions.process_one())
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_suggestion_runs').fetchone()[0], 1)

    def test_preparation_waits_for_the_analysis_at_most_its_limit_and_prepare_now_does_not(self):
        from test_jev import response
        from student_support.triage import QUESTIONS
        self.incoming()
        self.incoming(ident='c1', thread='c', enrollment=self.bob, body='Another question', at='2026-10-03T12:10:00Z')
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', return_value=response(QUESTIONS)):
            self.assertFalse(self.suggestions.process_one())
            shown = {i['enrollment_id']: i['suggestions'] for i in self.listing()['items']}
            self.assertEqual({s['state'] for s in shown.values()}, {'analyzing'})
            self.assertTrue(shown[self.bob]['preparable'])
            # Prepare now is the instructor's own request: it does not wait.
            self.assertEqual(self.control('prepare', enrollment_id=self.bob, item_key=shown[self.bob]['item_key']).status_code, 202)
            self.assertEqual({i['enrollment_id']: i['suggestions']['state'] for i in self.listing()['items']}[self.bob], 'queued')
            # Jev never answers about Alice: after the limit her conversation is prepared anyway.
            self.assertFalse(self.suggestions.process_one())
            self.assertEqual(len(self.suggestions._analysis_seen), 1)
            self.suggestions._analysis_seen = {mark: seen - self.suggestions.analysis_wait for mark, seen in self.suggestions._analysis_seen.items()}
            self.assertTrue(self.suggestions.process_one())
            self.assertEqual(self.suggestions._analysis_seen, {})
        self.assertFalse(self.suggestions.analyzing({'triage': {'status': 'failed'}}, {'messages': [{'author_role': 'student'}]}))
        self.assertFalse(self.suggestions.analyzing({'triage': {'status': 'pending'}}, {'messages': [{'author_role': 'instructor'}]}))
        self.assertFalse(self.suggestions.analyzing({}, {'messages': [{'author_role': 'student'}]}))

    def test_due_snoozed_followup_enters_the_existing_fair_worker(self):
        from datetime import timedelta
        from student_support.service import now
        self.support.save({'kind':'followup','enrollment_id':self.alice,'title':'Check this commitment', 'state':'snoozed',
            'date':(now().date()-timedelta(days=1)).isoformat(),'request_id':'due'})
        inbox = self.app.extensions['inbox']
        with patch('assistant.conversations.engine_for') as engine:
            self.assertTrue(inbox.work_once())
            engine.assert_not_called()
        self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'queued')
        self.assertEqual(self.suggestions.busy(), 0)

    def test_missing_model_does_no_model_work_and_preserves_existing_cards(self):
        self.incoming()
        run, _ = self.prepare()
        with patch('assistant.conversations.config.assistant', return_value=None):
            self.assertFalse(self.suggestions.process_one())
            self.assertFalse(self.suggestions.dispatch_one())
            shown = self.listing()['items'][0]['suggestions']
            self.assertIn('Compute settings', shown['unavailable'])
            self.assertEqual(shown['actions'][0]['id'], run['actions'][0]['id'])

    def test_failures_and_interrupted_claims_need_explicit_retry(self):
        def fail(c, _):
            raise RuntimeError('Synthetic engine failure')
        self.incoming()
        run, _ = self.prepare(fail)
        self.assertEqual(run['state'], 'failed')
        self.assertFalse(self.suggestions.process_one())
        response = self.request(run, 'retry')
        self.assertEqual(response.status_code, 202, response.json)
        with self.rosters_lock(), connect(self.assistant.database) as db:
            self.suggestions.event(db, response.json['id'], 'running')
        self.suggestions.recover_interrupted()
        self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'interrupted')
        self.assertFalse(self.suggestions.process_one())

    def session(self, body='Could I meet you?'):
        self.incoming(body=body)
        self.suggestions.process_one()
        run = self.listing()['items'][0]['suggestions']
        # ASSISTANT CONTEXT: a preparation records what its sources were as it starts (dispatch_one); so does this stand-in.
        with connect(self.assistant.database) as db:
            self.suggestions.context.remember(db, self.suggestions.get(db, run['id']))
        session = self.assistant.open_session('test')
        session.update(_proposal_only=True, _followup_run=run['id'])
        return session

    def today_is(self, day):
        """Hold the campus day for what a record made by itself reads (its bounds, its JEV DATES check and Undo; the listing; the
        calendar window of its sources), so a test of a dated message does not move with the day it runs on."""
        from datetime import datetime
        from scheduling.clock import CAMPUS_ZONE
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=CAMPUS_ZONE)
        for clock in (patch('exam_workflow.overview.campus_today', return_value=day.isoformat()),
                      patch('scheduling.clock.campus_now', return_value=noon), patch('student_support.service.now', return_value=noon)):
            clock.start()
            self.addCleanup(clock.stop)

    def notice(self, ident='a1'):
        """The captured message's ID: a recorded absence names its notice (ABSENCES RULE 7 NOTICE)."""
        with connect(self.assistant.database) as db:
            return db.execute('SELECT id FROM comm_messages WHERE external_id=?', (ident,)).fetchone()[0]

    def tool_write(self, session, path, body, method='POST'):
        result, _ = self.assistant.run_tool(session, 'faculty_write', {'method': method, 'path': path,
            'body': {**body, 'request_id': '$new_request_id'}, 'summary': 'Propose this follow-up action'})
        return json.loads(result)

    def test_any_change_is_a_card_for_any_student_but_never_routes_and_broadcasts_stay_out(self):
        # WIDE SCOPE (requirement, 2026-10-03): anything the assistant may prepare, for anyone, always as an approval card.
        session = self.session()
        common = {'kind': 'appointment', 'title': 'Meet', 'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30'}
        other = self.tool_write(session, '/api/student-support', {**common, 'enrollment_id': self.bob})
        self.assertEqual(other['outcome'], 'waiting_for_approval', other)
        self.assertIn('not limited to this follow-up’s student', other['why_approval'])
        mine = self.tool_write(session, '/api/student-support', {**common, 'enrollment_id': self.alice})
        self.assertEqual(mine['outcome'], 'waiting_for_approval', mine)
        self.assertNotIn('not limited', mine['why_approval'])
        section = self.listing()['items'][0]['student']['section_id']
        decided = self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice,  # EXCUSE NEEDS EVIDENCE
            'status': 'not_excused', 'first_date': '2026-10-02', 'reason': 'other', 'notice_message_id': self.notice()})
        self.assertEqual(decided['outcome'], 'waiting_for_approval', decided)
        setup = self.tool_write(session, '/api/gradebook/terms/' + self.term + '/setup', {})
        self.assertEqual(setup['outcome'], 'waiting_for_approval', setup)  # normally automatic in chat: a card here
        code = json.loads(self.assistant.run_tool(session, 'propose_code_change', {'title': 'Show office hours on Home',
            'request': 'The student could not find office hours.'})[0])
        self.assertEqual(code['outcome'], 'waiting_for_approval', code)
        with connect(self.assistant.database) as db:
            for table in ('absences', 'student_support_items', 'gradebook_setups', 'assistant_code_tasks'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
        # Deliveries to whole sections, the assistant's own controls and the instructor's own reviews stay out.
        self.assertIn('reaches others', self.tool_write(session, '/api/canvas/messages/send', {'sections': [section], 'subject': 'Hi', 'body': 'All'})['error'])
        self.assertEqual(self.tool_write(session, '/api/student-support/wellbeing/reviewed', {'enrollment_id': self.alice})['outcome'], 'refused')
        self.assertEqual(self.tool_write(session, '/api/assistant/actions/fake/approve', {'seen': 1})['outcome'], 'refused')
        self.assertEqual(policy.base_rule('student_support.suggestion_request').tier, 'never')
        self.assertEqual(self.client.post('/api/student-support/followups/suggestions/fake/retry', json={}).status_code, 403)
        self.assertIn('error', self.tool_write(session, '/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': 'gmail:b1', 'observed': self.listing()['items'][0]['observed'], 'title': 'Question', 'state': 'waiting'}))

    def test_a_meeting_is_an_appointment_never_a_calendar_event(self):
        # MEETINGS ARE APPOINTMENTS (requirement, 2026-10-06): refused with the appointment route; other calendar events stay cards.
        session = self.session()
        name = self.support.people()[self.alice]['name']
        common = {'date': '2026-10-13', 'end_date': '2026-10-13', 'time': '14:30', 'end_time': '15:00', 'all_day': False,
                  'location': 'BLOC 319', 'category': 'event', 'scope': 'workspace'}
        for title, notes in ((f'Meeting with {name}', ''), ('Office hours', f'For {name.split()[-1]}'), ('Student appointment', ''),
                             ('1:1 check-in', '')):
            refused = self.tool_write(session, '/api/calendar/events', {**common, 'title': title, 'notes': notes})
            self.assertIn('a meeting with this student is an office-hours appointment', refused.get('error', ''), (title, refused))
            self.assertIn(f'"enrollment_id": "{self.alice}"', refused['error'])
        moved = self.tool_write(session, '/api/calendar/events/custom-1', {**common, 'title': f'Meeting with {name}'}, method='PUT')
        self.assertIn('never a calendar event', moved.get('error', ''), moved)
        review = self.tool_write(session, '/api/calendar/events', {**common, 'title': 'Exam 2 review session', 'notes': ''})
        self.assertEqual(review['outcome'], 'waiting_for_approval', review)
        self.assertIn('not limited to this follow-up’s student', review['why_approval'])
        meeting = self.tool_write(session, '/api/student-support', {'kind': 'appointment', 'enrollment_id': self.alice, 'title': 'Meeting',
                                                                    'date': '2026-10-13', 'time': '14:30', 'end_time': '15:00'})
        self.assertEqual(meeting['outcome'], 'waiting_for_approval', meeting)
        self.assertNotIn('not limited', meeting['why_approval'])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assistant_actions WHERE endpoint LIKE 'workspace_calendar.%'").fetchone()[0], 1)

    def test_mail_reaches_the_student_their_tas_and_you_only(self):
        self.fake_mail()
        session = self.session()
        section = self.listing()['items'][0]['student']['section_id']
        saved = self.client.post(f'/api/teaching-assistants/sections/{section}', json={'request_id': 'tas', 'base_revision': 0,
            'contacts': [{'name': 'Tess Assistant', 'email': 'tess@example.test'}]}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        note = {'subject': 'About Alice', 'body': 'Please look at her quiz.'}
        to_ta = self.tool_write(session, '/api/mail/send', {**note, 'to': ['tess@example.test']})
        self.assertEqual(to_ta['outcome'], 'waiting_for_approval', to_ta)
        both = self.tool_write(session, '/api/mail/send', {**note, 'to': ['alice@example.test'], 'cc': ['tess@example.test', 'prof@tamu.edu']})
        self.assertEqual(both['outcome'], 'waiting_for_approval', both)
        for refused in ({'to': ['bob@example.test']}, {'to': ['tess@example.test'], 'cc': ['stranger@example.test']}):
            self.assertIn('emails only this student', self.tool_write(session, '/api/mail/send', {**note, **refused})['error'])

    def test_exact_duplicate_is_one_card_even_with_new_request_ids(self):
        session = self.session()
        body = {'kind': 'appointment', 'enrollment_id': self.alice, 'title': 'Meet', 'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30'}
        one = self.tool_write(session, '/api/student-support', body)
        two = self.tool_write(session, '/api/student-support', body)
        self.assertEqual(one['action_id'], two['action_id'])
        self.assertEqual(len(self.client.get('/api/assistant/actions').json['pending']), 1)

    def test_batch_source_preflight_submits_nothing_when_one_card_is_stale(self):
        session = self.session()
        cards = [self.tool_write(session, '/api/student-support', {'kind': 'appointment', 'enrollment_id': self.alice,
            'title': title, 'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30'})['action_id'] for title in ('One', 'Two')]
        batch = self.assistant.batch(session, cards, 'Related follow-up actions')
        seen = {a: self.assistant._view(*self.assistant.action(a))['seen'] for a in cards}
        self.incoming('a2', at='2026-10-03T13:00:00Z')
        response = self.client.post('/api/assistant/batches/' + batch['batch_id'] + '/approve', json={'seen': seen}, headers=HEADERS)
        self.assertEqual(response.status_code, 409)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_items').fetchone()[0], 0)

    def test_mail_recipient_scope_and_uncertain_receipts_never_start_continuation(self):
        session = self.session()
        mail = {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': 'We can discuss this.', 'reply_thread_id': 'b1'}
        self.assertIn('error', self.tool_write(session, '/api/mail/send', {**mail, 'to': ['bob@example.test']}))
        self.assertIn('error', self.tool_write(session, '/api/mail/send', {**mail, 'cc': ['stranger@example.test']}))
        self.assertIn('error', self.tool_write(session, '/api/mail/send', {**mail, 'reply_thread_id': 'not-my-thread'}))
        preview = {'status': 200, 'data': {'message': {'to': mail['to'], 'text': mail['body']}}, 'fingerprint': 'fixed'}
        with patch.object(self.assistant, '_preview', return_value=preview):
            result = self.tool_write(session, '/api/mail/send', mail)
            self.assertEqual(result['outcome'], 'waiting_for_approval', result)
            action = self.assistant._view(*self.assistant.action(result['action_id']))
            # Simulate a kept uncertain send outcome; an HTTP 200 is not a confirmed delivery.
            self.assistant._event(action['id'], 'succeeded', 'faculty', {'status': 200, 'result': {'status': 'uncertain'}})
            with self.rosters_lock(), connect(self.assistant.database) as db:
                self.suggestions.event(db, session['_followup_run'], 'ready')
            self.assertTrue(self.suggestions.process_one())  # Settles the reviewed run only.
            self.assertFalse(self.suggestions.process_one())
            self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'settled')
            self.assertEqual(self.tool_write(session, '/api/mail/send', mail)['outcome'], 'already_recorded')

    def test_an_approved_round_settles_without_a_follow_through_round(self):
        # ONE ROUND (requirement, 2026-10-04): once every card is decided the run settles and nothing more is prepared from its receipts.
        self.incoming()
        run, _ = self.prepare()
        self.assertEqual(self.approve(run['actions'][0]).status_code, 200)
        self.assertTrue(self.suggestions.process_one())  # settles the reviewed run
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual((shown['id'], shown['state']), (run['id'], 'settled'))
        self.assertFalse(self.suggestions.process_one())
        self.assertFalse(self.suggestions.dispatch_one())
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_suggestion_runs').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assistant_actions').fetchone()[0], 1)
        # A plain Retry still prepares again on request.
        self.assertEqual(self.request(shown, 'retry').status_code, 202)

    def test_waiting_on_student_is_proposed_beside_the_reply_and_approved_after_it_sends(self):
        fake = self.fake_mail()
        self.incoming()
        def first(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            waiting = ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'observed': observed,
                                                'title': 'Question', 'state': 'waiting'}, 'Wait for her answer')
            # Before any reply card the Waiting card is refused; after the reply card it is proposed in the same round.
            result, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': waiting[0],
                'body': {**waiting[1], 'request_id': '$new_request_id'}, 'summary': waiting[2]})
            assert 'propose the reply first' in json.loads(result).get('error', ''), result
            proposing(MAIL, waiting)(c, prompt)
        run, _ = self.prepare(first)
        mail, waiting = run['actions']
        self.assertEqual(waiting['effect']['notes'], ['It can be approved once the reply is sent. Approve all sends the reply first.'])
        early = self.approve(waiting)
        self.assertIn(early.status_code, (400, 409), early.json)
        self.assertIn('send the reply first', early.json['error'])
        self.assertEqual((self.view(waiting['id'])['pending'], fake.sent), (True, []))
        # Approve all sends the reply first (mail always goes first), then marks the conversation.
        result = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/approve",
                                  json={'seen': {waiting['id']: self.view(waiting['id'])['seen'], mail['id']: mail['seen']}}, headers=HEADERS).json
        self.assertEqual([r['id'] for r in result['results']], [mail['id'], waiting['id']], result)
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual([i['state'] for i in self.client.get('/api/student-support?kind=followup').json['items']], ['waiting'])

    def test_real_mail_owner_receipts_gate_tracking_and_never_resend_an_uncertain_send(self):
        fake = self.fake_mail()
        session = self.session()
        body = {'to': ['alice@example.test'], 'subject': 'Your question', 'body': 'Please come to office hours.'}
        result = self.tool_write(session, '/api/mail/send', body)
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        self.assertEqual(fake.sent, [])
        action = self.assistant._view(*self.assistant.action(result['action_id']))
        fake.lose_answer = True
        sent = self.approve(action)
        self.assertEqual(sent.status_code, 200, sent.json)
        self.assertEqual(sent.json['result']['status'], 'uncertain')
        self.assertEqual(len(fake.sent), 1)
        with self.rosters_lock(), connect(self.assistant.database) as db:
            self.suggestions.event(db, session['_followup_run'], 'ready')
        self.suggestions.process_one()
        self.assertFalse(self.suggestions.process_one())
        run = self.listing()['items'][0]['suggestions']
        self.assertEqual(self.request(run, 'retry').status_code, 202)
        session['_followup_run'] = self.listing()['items'][0]['suggestions']['id']
        self.assertEqual(self.tool_write(session, '/api/mail/send', body)['outcome'], 'already_recorded')
        self.assertIn('error', self.tool_write(session, '/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': 'gmail:b1', 'observed': self.listing()['items'][0]['observed'], 'title': 'Question', 'state': 'waiting'}))
        self.assertEqual(len(fake.sent), 1)

    def test_successful_send_allows_dependent_tracking_only_as_a_new_approval(self):
        fake = self.fake_mail()
        session = self.session()
        mail = self.tool_write(session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Your question', 'body': 'Come at 2 pm.'})
        sent = self.approve(self.assistant._view(*self.assistant.action(mail['action_id'])))
        self.assertEqual(sent.json['result']['status'], 'accepted', sent.json)
        self.assertEqual(len(fake.sent), 1)
        tracking = self.tool_write(session, '/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': 'gmail:b1', 'observed': self.listing()['items'][0]['observed'], 'title': 'Question', 'state': 'waiting'})
        self.assertEqual(tracking['outcome'], 'waiting_for_approval', tracking)
        self.assertEqual(self.listing()['items'][0]['state'], 'needs_reply')
        self.assertEqual(self.approve(self.assistant._view(*self.assistant.action(tracking['action_id']))).status_code, 200)
        self.assertEqual(self.listing()['items'][0]['state'], 'waiting')
        self.assertEqual(len(fake.sent), 1)

    def test_confirmed_mail_refusal_can_be_prepared_again_only_after_explicit_retry(self):
        fake = self.fake_mail()
        session = self.session()
        body = {'to':['alice@example.test'],'subject':'Your question','body':'Come at 2 pm.'}
        mail = self.tool_write(session, '/api/mail/send', body)
        fake.fail[('POST', '/messages/send')] = (403, {'error': {'code':403,'message':'Synthetic refusal'}})
        sent = self.approve(self.assistant._view(*self.assistant.action(mail['action_id'])))
        self.assertEqual(sent.json['result']['status'], 'failed', sent.json)
        with self.rosters_lock(), connect(self.assistant.database) as db:
            self.suggestions.event(db, session['_followup_run'], 'ready')
        self.suggestions.process_one()
        self.assertFalse(self.suggestions.process_one())
        self.assertEqual(self.tool_write(session, '/api/mail/send', body)['outcome'], 'already_recorded')
        run = self.listing()['items'][0]['suggestions']
        self.assertEqual(self.request(run, 'retry').status_code, 202)
        session['_followup_run'] = self.listing()['items'][0]['suggestions']['id']
        again = self.tool_write(session, '/api/mail/send', body)
        self.assertEqual(again['outcome'], 'waiting_for_approval', again)
        self.assertNotEqual(again['action_id'], mail['action_id'])
        self.assertEqual(fake.sent, [])

    def test_source_changed_during_preview_is_rechecked_before_send(self):
        self.fake_mail()
        session = self.session()
        result = self.tool_write(session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Your question', 'body': 'Please come.'})
        action = self.assistant._view(*self.assistant.action(result['action_id']))
        preview = self.assistant._preview
        def changed(*args):
            review = preview(*args)
            self.incoming(body='A changed question.')
            return review
        with patch.object(self.assistant, '_preview', side_effect=changed):
            self.assertEqual(self.approve(action).status_code, 409)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_mail_sends').fetchone()[0], 0)

    def test_mail_review_retains_complete_text_beyond_tool_excerpt_limit(self):
        self.fake_mail()
        session = self.session()
        complete = 'Explain the work carefully.\n' * 500 + 'Last sentence must be reviewed.'
        result = self.tool_write(session, '/api/mail/send', {'to':['alice@example.test'],'subject':'Details','body':complete})
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        action = self.assistant._view(*self.assistant.action(result['action_id']))
        self.assertEqual(action['preview']['data']['message']['text'], complete)

    def test_an_absence_names_its_rule_7_notice_and_the_facts_say_when_each_came(self):
        # RULE 7 NOTICE (requirement, 2026-10-05): a follow-up's absence names the
        # student's message that first told the instructor; the preparation is told which one, and how each listed absence stands.
        session = self.session()
        section = self.listing()['items'][0]['student']['section_id']
        body = {'section_id': section, 'enrollment_id': self.alice, 'status': 'pending', 'first_date': '2026-10-02', 'reason': 'illness'}
        refused = self.tool_write(session, '/api/absences', body)
        self.assertIn(f'"notice_message_id" (Student Rule 7\'s notice): in this conversation that is {self.notice()}', refused.get('error', ''), refused)
        made = self.tool_write(session, '/api/absences', {**body, 'notice_message_id': self.notice(), 'death': True})
        self.assertEqual(made['outcome'], 'waiting_for_approval', made)
        self.assertEqual(self.approve(self.assistant._view(*self.assistant.action(made['action_id']))).status_code, 200)
        [absence] = self.client.get('/api/absences').json['absences']
        self.assertEqual((absence['notice_message_id'], absence['notice']['timing']), (self.notice(), 'after'))  # Sat, about Fri
        with connect(self.assistant.database) as db:
            run = dict(self.suggestions.get(db, session['_followup_run']))
            term = db.execute('SELECT * FROM course_terms WHERE id=?', (self.term,)).fetchone()
            facts = self.suggestions._record_facts(db, run, term)
        self.assertIn(f'Their first message in this conversation is {self.notice()}, sent Sat, Oct 3, 7:00 AM.', facts)
        self.assertIn('· Notice after it began (Rule 7: notice by the end of Tue, Oct 6 when it could not come before; documentation by the '
                      'end of Wed, Oct 7)', facts)
        self.assertIn('· Illness (a death) ·', facts)  # DEATHS WAIT: the preparation knows it waits for the instructor

    def test_a_preparation_reads_the_students_section_schedule(self):
        # CLASS SCHEDULE (requirement, 2026-10-10, asked that follow-ups read a section's class schedule): the days the student
        # named with what the class covered and what was given or due, what was due around then, and what is coming; a planned
        # date says so. A change to the plan by someone else is a warning under its own key.
        from datetime import date
        self.today_is(date(2026, 10, 3))
        session = self.session(body='I was sick and missed class on Friday. Is anything due Monday?')
        section = self.listing()['items'][0]['student']['section_id']
        with connect(self.assistant.database) as db:
            number = db.execute('SELECT number FROM course_sections WHERE id=?', (section,)).fetchone()[0]
            run = dict(self.suggestions.get(db, session['_followup_run']))
            term = db.execute('SELECT * FROM course_terms WHERE id=?', (self.term,)).fetchone()
            self.assertNotIn('class schedule', self.suggestions._record_facts(db, run, term))  # nothing on the schedule: nothing said
            self.assertEqual(self.suggestions.context.changes(db, run), {'refuse': [], 'warn': []})
        base = f'/api/scheduling/terms/{self.term}'
        saved = self.client.post(base, headers=HEADERS, json={'request_id': 'weekly', 'base_revision': 0, 'first_day': '2026-08-24',
            'last_day': '2026-12-03', 'entries': [{'kind': 'class', 'section_id': section, 'weekdays': [0, 2, 4], 'start': '08:00', 'end': '08:50',
                                                  'room': 'HELD 107'}]})
        self.assertEqual(saved.status_code, 200, saved.json)
        used = self.client.post(base + '/use', headers=HEADERS, json={'request_id': 'use', 'confirmation': 'use',
                                                                      'fingerprint': self.client.get(base + '/use').json['fingerprint']})
        self.assertEqual(used.status_code, 200, used.json)
        column = self.client.post(f'/api/gradebook/terms/{self.term}/setup', headers=HEADERS, json={
            'base_revision': None, 'request_id': 'columns', 'setup': {'items': [{'title': 'HW 5', 'points': '10', 'due_date': '2026-10-05'}]}})
        self.assertEqual(column.status_code, 200, column.json)
        planned = self.client.post(base + '/class-schedule', headers=HEADERS, json={'request_id': 'plan', 'base_revision': 0,
            'set': [{'kind': 'quiz', 'title': 'Quiz 6', 'date': '2026-10-05', 'time': '8:00 AM'}],
            'topics': {'2026-10-02': {'notes': ['3.2'], 'note': 'Review for the quiz'}}})
        self.assertEqual(planned.status_code, 200, planned.json)
        with connect(self.assistant.database) as db:
            facts = self.suggestions._record_facts(db, run, term)
            found = self.suggestions.context.changes(db, run)
            changed = found['refuse'] + found['warn']
        self.assertIn(f"Section {number}'s class schedule (Faculty's records; a planned date is the instructor's plan for work not created yet):", facts)
        self.assertIn('- Fri, Oct 2: class 8:00–8:50 AM in HELD 107; covered 3.2, Review for the quiz', facts)
        # "Monday" with no date is the one before and the one after the day they wrote: both are stated.
        self.assertIn('- Mon, Sep 28: class 8:00–8:50 AM in HELD 107', facts)
        self.assertIn('- Mon, Oct 5: class 8:00–8:50 AM in HELD 107; given: Quiz 6 (8:00 AM) (planned, not created yet); due: HW 5', facts)
        self.assertIn('Due around then:\n- HW 5: due Mon, Oct 5', facts)
        self.assertIn('Coming up in the next two weeks:\n- Quiz 6 (8:00 AM) (planned, not created yet): given Mon, Oct 5\n- HW 5: due Mon, Oct 5', facts)
        self.assertIn('class schedule', changed)  # the plan changed since the draft was prepared: a warning, never a refusal
        with connect(self.assistant.database) as db:
            self.assertNotIn('Class schedule', ' '.join(self.suggestions.context.changes(db, run)['refuse']))

    def test_waiting_absence_and_attachments_are_scope_checked(self):
        session = self.session()
        section = self.listing()['items'][0]['student']['section_id']
        result = self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice,
            'status': 'pending', 'first_date': '2026-10-02', 'reason': 'illness', 'notice_message_id': self.notice()})
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        self.assertEqual(self.approve(self.assistant._view(*self.assistant.action(result['action_id']))).status_code, 200)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT status FROM absence_revisions').fetchone()[0], 'pending')
        self.assertIn('error', self.tool_write(session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Question', 'body': 'Attached.',
            'attachments': ['/api/gradebook/terms/' + self.term + '/export']}))

    def test_a_chat_file_goes_only_where_the_instructors_request_names_it(self):
        # CHAT FILES IN A FOLLOW-UP (requirement, 2026-10-20): their request for this follow-up names it.
        self.fake_mail()
        [notes] = self.assistant.uploads.save([('Chapter 5 notes.pdf', 'application/pdf', b'%PDF-1.4 filled-in notes')])
        [other] = self.assistant.uploads.save([('Someone else.pdf', 'application/pdf', b'%PDF-1.4 another file')])
        mail = lambda session, route: self.tool_write(session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Notes',
            'body': 'Attached.', 'attachments': [route]})
        self.assertIn('not verified as shared course material', mail(self.session(), notes['route']).get('error', ''))
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine()):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        run = self.listing()['items'][0]['suggestions']
        self.assertEqual(self.request(run, 'revise', text=f"Attach the notes: {notes['route']} ({notes['name']}).").status_code, 202)
        told = self.assistant.open_session('test')
        told.update(_proposal_only=True, _followup_run=self.listing()['items'][0]['suggestions']['id'])
        with connect(self.assistant.database) as db:
            self.suggestions.context.remember(db, self.suggestions.get(db, told['_followup_run']))
        made = mail(told, notes['route'])
        self.assertEqual(made['outcome'], 'waiting_for_approval', made)  # still a card the instructor approves
        self.assertIn('not verified as shared course material', mail(told, other['route']).get('error', ''))

    def test_an_assignment_goes_to_the_student_as_their_own_copy(self):
        # QUIZZES "A COPY BY EMAIL" (requirement, 2026-11-08): the preparation makes the
        # student's own stamped copy by itself and attaches it; a blank, the class's job or a classmate's copy is refused.
        from datetime import date, timedelta
        from exam_workflow.overview import campus_today
        self.fake_mail()
        session = self.session('Could you send me the take-home? I lost my copy.')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        documents = self.app.extensions['documents']
        with connect(self.assistant.database) as db:
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (self.term,)).fetchone()[0]
        assignment = documents.create_assignment(definition, 'take-home', 'Take Home Assignment')
        version = self.client.post('/api/documents/versions/fixture', json={'assignment_id': assignment['id'], 'label': 'A', 'paper': 'letter'},
                                   headers=HEADERS).json['id']
        job = documents.create_batch(version, None, 2, str(uuid.uuid4()), term_id=self.term, section_ids=[section])
        documents.build_one()
        made = self.tool_write(session, f'/api/documents/versions/{version}/student-copy', {'enrollment_id': self.alice})
        self.assertEqual(made['outcome'], 'done', made)
        copy = made['result']
        self.assertEqual((copy['name'], copy['enrollment_id']), ('Take Home Assignment.pdf', self.alice))
        mail = lambda route: self.tool_write(session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Take-home',  # noqa: E731
            'body': 'Here is your copy.', 'attachments': [{'route': route, 'name': 'Take Home Assignment.pdf'}]})
        theirs = documents.student_copy(version, self.bob, 'bob-copy')['route']
        for route in (f'/api/documents/versions/{version}/preview', f"/api/documents/batches/{job['id']}/download", theirs):
            with self.subTest(route=route):
                self.assertIn('only as their own copy', mail(route).get('error', ''))
        other = self.tool_write(session, f'/api/documents/versions/{version}/student-copy', {'enrollment_id': self.bob})
        self.assertEqual(other['outcome'], 'waiting_for_approval', other)  # a classmate's copy is a card
        self.assertIn('it is not for this student', other['why_approval'])
        self.client.post('/api/assistant/actions/' + other['action_id'] + '/decline', json={}, headers=HEADERS)
        reply = mail(copy['route'])
        self.assertEqual(reply['outcome'], 'waiting_for_approval', reply)
        [line] = [d for d in self.listing()['items'][0]['suggestions']['done'] if d['id'] == made['action_id']]
        self.assertEqual((line['title'], line['state'], line['undo']), ('Made their own copy of Take Home Assignment', 'done', False))
        approved = self.approve(self.assistant._view(*self.assistant.action(reply['action_id'])))
        self.assertEqual(approved.status_code, 200, approved.json)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT route FROM gmail_mail_send_attachments').fetchone()[0], copy['route'])
            self.assertTrue(documents.emailed_copy(db, copy['batch_id']))  # never printed now
        # A quiz still to be given to their section is never mailed.
        tomorrow = (date.fromisoformat(campus_today()) + timedelta(days=1)).isoformat()
        quiz = documents.create_assignment(definition, 'quiz-9', 'Quiz 9')
        quiz_version = self.client.post('/api/documents/versions/fixture', json={'assignment_id': quiz['id'], 'label': 'A', 'paper': 'letter'},
                                        headers=HEADERS).json['id']
        exams = self.app.extensions['frq_grading'].exams
        event = exams.create({'term_id': self.term, 'assignment_id': quiz['id'], 'section_ids': [section]})
        sitting = self.client.post(f"/api/exams/{event['id']}/sittings", headers=HEADERS,
                                   json={'kind': 'main', 'label': 'Main', 'date': tomorrow, 'time': '08:00', 'version': event['version']})
        self.assertLess(sitting.status_code, 300, sitting.json)
        early = self.tool_write(session, f'/api/documents/versions/{quiz_version}/student-copy', {'enrollment_id': self.alice})
        self.assertEqual(early['outcome'], 'done', early)
        self.assertIn(f'still to be given to this student ({tomorrow})', mail(early['result']['route']).get('error', ''))

    def test_an_excuse_needs_documentation_or_the_instructors_word(self):
        # EXCUSE NEEDS EVIDENCE (requirement, 2026-10-08): refused on the student's account alone; documents that stand or the instructor's word make it a card.
        session = self.session()
        section = self.listing()['items'][0]['student']['section_id']
        facts = {'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02', 'reason': 'illness',
                 'notice_message_id': self.notice()}
        self.assertIn('EXCUSE NEEDS EVIDENCE', self.tool_write(session, '/api/absences', {**facts, 'status': 'excused'}).get('error', ''))
        made = self.tool_write(session, '/api/absences', {**facts, 'status': 'pending'})
        self.assertEqual(self.approve(self.view(made['action_id'])).status_code, 200)
        [absence] = self.client.get('/api/absences').json['absences']
        path, excuse = '/api/absences/' + absence['id'], {'status': 'excused', 'base_revision': absence['revision']}
        refused = self.tool_write(session, path, excuse)
        self.assertIn('the student’s own account of an absence (or a relative’s) never excuses it', refused.get('error', ''), refused)
        # NOT EXCUSED WHEN NOTHING COULD EXCUSE IT: an illness could still be documented, so Not excused is refused here too.
        self.assertIn('NOT EXCUSED WHEN NOTHING COULD EXCUSE IT',
                      self.tool_write(session, path, {**excuse, 'status': 'not_excused'}).get('error', ''))
        absences = self.app.extensions['absences']
        absences.upload(absence['id'], b'%PDF-1.4 a clinic note', 'note.pdf', 'upload-note')
        card = self.tool_write(session, path, excuse)
        self.assertEqual(card['outcome'], 'waiting_for_approval', card)
        [document] = absences.get(absence['id'])['documents']
        absences.document_event(absence['id'], document['id'], 'removed', {'request_id': 'remove-note'})
        self.assertIn('EXCUSE NEEDS EVIDENCE', self.tool_write(session, path, excuse).get('error', ''))  # a removed document is none
        # Approving the card made while the note stood is the instructor's own decision.
        self.assertEqual(self.approve(self.view(card['action_id'])).status_code, 200)
        self.assertEqual(absences.get(absence['id'])['status'], 'excused')
        # The instructor's word: a follow-up they gave an instruction may propose the excuse.
        absences.revise(absence['id'], {'status': 'pending', 'base_revision': absence['revision'] + 1, 'request_id': 'back-to-pending'})
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine()):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        run = self.listing()['items'][0]['suggestions']
        self.assertEqual(self.request(run, 'revise', text='Excuse her absence.').status_code, 202)
        told = self.assistant.open_session('test')
        told.update(_proposal_only=True, _followup_run=self.listing()['items'][0]['suggestions']['id'])
        with connect(self.assistant.database) as db:
            self.suggestions.context.remember(db, self.suggestions.get(db, told['_followup_run']))
        allowed = self.tool_write(told, path, {**excuse, 'base_revision': absence['revision'] + 2})
        self.assertEqual(allowed['outcome'], 'waiting_for_approval', allowed)
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(self.suggestions.get(db, told['_followup_run']))
        self.assertIn("The student's own account of an absence (or a relative's) never excuses it", prompt)

    def test_an_absence_nothing_could_excuse_is_proposed_not_excused(self):
        # NOT EXCUSED WHEN NOTHING COULD EXCUSE IT (requirement, 2026-11-13): a reason outside Student Rule 7's is proposed Not excused,
        # on a card whose dropdown still stands on it; Rule 7's own reasons, a death and any documentation wait for the instructor.
        session = self.session()
        section = self.listing()['items'][0]['student']['section_id']
        facts = {'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02', 'notice_message_id': self.notice()}
        clear = {**facts, 'reason': 'other', 'status': 'not_excused', 'decision_note': 'An exam in another course.'}
        card = self.tool_write(session, '/api/absences', clear)
        self.assertEqual(card['outcome'], 'waiting_for_approval', card)
        effect = {a['id']: a for a in self.listing()['items'][0]['suggestions']['actions']}[card['action_id']]['effect']
        decision = next(c for c in effect['changes'] if c['label'] == 'Decision')
        self.assertEqual((effect['title'], decision['value'], decision['choice']['value']),
                         ('Record an absence', 'Not excused', 'not_excused'))
        self.assertEqual([o['value'] for o in decision['choice']['options']], ['pending', 'excused', 'not_excused'])
        self.assertNotIn('suggested', decision['choice'])  # the Assistant's own proposal, not a decision they changed
        self.assertIn('An exam in another course.', [c['value'] for c in effect['changes']])
        # Nothing is recorded until the instructor's own Record absence: a decision is never done by itself (DONE FOR YOU).
        self.assertEqual(self.client.get('/api/absences').json['absences'], [])
        auto = self.assistant.open_session('test')
        auto.update(_proposal_only=True, _followup_run=session['_followup_run'], _followup_auto=True)
        held = self.tool_write(auto, '/api/absences', {**clear, 'details': 'He wrote the night before.'})
        self.assertEqual(held['outcome'], 'waiting_for_approval', held)
        self.assertIn('a decision is the instructor’s', held['why_approval'])
        # Rule 7's own reasons could still be documented, so each waits for the instructor with no decision proposed.
        for reason in ('illness', 'family', 'dependent', 'activity', 'religious', 'legal', 'military', 'interview', 'pregnancy'):
            refused = self.tool_write(session, '/api/absences', {**clear, 'reason': reason})
            self.assertIn('NOT EXCUSED WHEN NOTHING COULD EXCUSE IT', refused.get('error', ''), reason)
        death = self.tool_write(session, '/api/absences', {**clear, 'death': True})  # DEATHS WAIT, whatever the reason
        self.assertIn('NOT EXCUSED WHEN NOTHING COULD EXCUSE IT', death.get('error', ''), death)
        sent = self.tool_write(session, '/api/absences', {**clear, 'documents': [{'message_id': 'a-message', 'index': 0}]})
        self.assertIn('the student sent files with this absence', sent.get('error', ''), sent)
        # Approving the card records it, and a document on record keeps a later revision waiting.
        self.assertEqual(self.approve(self.view(card['action_id'])).status_code, 200)
        [absence] = self.client.get('/api/absences').json['absences']
        self.assertEqual((absence['status'], absence['revision']), ('not_excused', 1))
        absences = self.app.extensions['absences']
        absences.revise(absence['id'], {'status': 'pending', 'base_revision': 1, 'request_id': 'back-to-pending'})
        path, at = '/api/absences/' + absence['id'], absence['revision'] + 1
        again = self.tool_write(session, path, {'status': 'not_excused', 'base_revision': at})
        self.assertEqual(again['outcome'], 'waiting_for_approval', again)
        absences.upload(absence['id'], b'%PDF-1.4 a letter', 'letter.pdf', 'upload-letter')
        documented = self.tool_write(session, path, {'status': 'not_excused', 'base_revision': at, 'details': 'A letter came.'})
        self.assertIn('has documentation on record', documented.get('error', ''), documented)
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(self.suggestions.get(db, session['_followup_run']))
        self.assertIn('Propose Not excused when the reason the student gives is not one Student Rule 7 excuses', prompt)

    def test_makeup_assignment_waits_for_a_real_sitting(self):
        session = self.session()
        exams = self.app.extensions['frq_grading'].exams
        section = self.listing()['items'][0]['student']['section_id']
        with connect(self.assistant.database) as db:
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (self.term,)).fetchone()[0]
        assignment = self.app.extensions['documents'].create_assignment(definition, 'exam-1', 'Exam 1')
        event = exams.create({'term_id':self.term, 'assignment_id':assignment['id'], 'section_ids':[section]})
        with connect(self.assistant.database) as db:  # ASSISTANT CONTEXT: the preparation starts once the exam exists
            self.suggestions.context.remember(db, self.suggestions.get(db, session['_followup_run']))
        base = '/api/exams/' + event['id']
        participant = next(p for p in event['participants'] if p['enrollment_id'] == self.alice)
        path = base + '/participants/' + section + '/' + self.alice
        self.assertIn('error', self.tool_write(session, path, {'sitting_id':'invented','version':participant['version']}, 'PATCH'))
        self.assertEqual(self.tool_write(session, base+'/sittings', {'kind':'main','label':'Main','version':event['version']})['outcome'], 'waiting_for_approval')
        proposed = self.tool_write(session, base+'/sittings', {'kind':'makeup','label':'Makeup','date':'2026-10-09','time':'14:00','version':event['version']})
        self.assertEqual(proposed['outcome'], 'waiting_for_approval', proposed)
        self.assertEqual(exams.get(event['id'])['sittings'], [])
        approved = self.approve(self.assistant._view(*self.assistant.action(proposed['action_id'])))
        self.assertEqual(approved.status_code, 200, approved.json)
        sitting = approved.json['result']['sittings'][0]
        excused = self.tool_write(session, path, {'sitting_id':sitting['id'],'expectation':'excused','version':participant['version']}, 'PATCH')
        self.assertEqual(excused['outcome'], 'waiting_for_approval', excused)  # WIDE SCOPE: the decision is the instructor's approval
        assign = self.tool_write(session, path, {'sitting_id':sitting['id'],'version':participant['version']}, 'PATCH')
        self.assertEqual(assign['outcome'], 'waiting_for_approval', assign)
        self.assertEqual(self.approve(self.assistant._view(*self.assistant.action(assign['action_id']))).status_code, 200)
        current = exams.get(event['id'])
        self.assertEqual(next(p for p in current['participants'] if p['enrollment_id']==self.alice)['sitting_id'], sitting['id'])
        self.assertIsNone(next(p for p in current['participants'] if p['enrollment_id']==self.bob)['sitting_id'])
        # ONE ROUND: a new makeup sitting seats the student in the same card (EXAMS WITH PARTICIPANTS).
        bob = next(p for p in current['participants'] if p['enrollment_id'] == self.bob)
        both = self.tool_write(session, base+'/sittings', {'kind':'makeup','label':'Second makeup','date':'2026-10-16','time':'15:00',
            'version':current['version'], 'participants':[{'section_id':section,'enrollment_id':self.bob,'version':bob['version']}]})
        self.assertEqual(both['outcome'], 'waiting_for_approval', both)
        self.assertEqual(self.approve(self.view(both['action_id'])).status_code, 200)
        after = exams.get(event['id'])
        made = next(s for s in after['sittings'] if s['label'] == 'Second makeup')
        self.assertEqual(next(p for p in after['participants'] if p['enrollment_id']==self.bob)['sitting_id'], made['id'])
        with self.assertRaises(Exception):  # a stale participant version seats nobody and makes no sitting
            exams.save_sitting(event['id'], {'kind':'makeup','label':'Stale','version':after['version'],
                                             'participants':[{'section_id':section,'enrollment_id':self.bob,'version':bob['version']}]})
        self.assertNotIn('Stale', [s['label'] for s in exams.get(event['id'])['sittings']])
        with self.assertRaises(ValueError):  # an existing sitting seats students from their own rows
            exams.save_sitting(event['id'], {'label':'Second makeup','version':made['version'], 'participants':[{'section_id':section,'enrollment_id':self.bob,'version':1}]}, made['id'])

    def test_the_preparation_knows_when_the_student_wrote_and_what_was_given_that_day(self):
        # FOLLOW-UP FACTS (requirement, 2026-10-04): a draft asked which day a student missed although he named "the quiz we had
        # in class" and the quiz has a given-on date. The prompt says when he wrote (campus clock) and what his section was given.
        self.incoming(at='2026-10-03T06:20:02Z', body='This morning I crashed my bike and missed the quiz we had in class.')
        item = self.listing()['items'][0]
        exams = self.app.extensions['frq_grading'].exams
        with connect(self.assistant.database) as db:
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (self.term,)).fetchone()[0]
        for slug, title, day in (('quiz-4', 'Quiz 4', '2026-10-02'), ('quiz-1', 'Quiz 1', '2026-08-28')):
            assignment = self.app.extensions['documents'].create_assignment(definition, slug, title)
            event = exams.create({'term_id': self.term, 'assignment_id': assignment['id'], 'section_ids': [item['student']['section_id']]})
            exams.save_sitting(event['id'], {'kind': 'main', 'label': 'In class', 'date': day, 'version': event['version']})
        self.assertTrue(self.suggestions.process_one())
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))
        self.assertIn("The student's latest message in this conversation was sent Sat, Oct 3, 1:20 AM campus time.", prompt)
        self.assertIn(f"- Quiz 4: given Fri, Oct 2 to section {item['student']['sections'][0]}; no paper from them", prompt)
        self.assertNotIn('Quiz 1', prompt)  # more than three weeks before he wrote
        self.assertIn('Never ask the student for what Faculty or their message already shows', prompt)
        # DOCUMENTATION ONLY FOR MISSED WORK (requirement, 2026-10-05): a redraft told "no quiz Friday" still asked for documentation;
        # a corrected assumption reworks every card that followed from it.
        self.assertIn('Ask for documentation of an absence, or what it was for, only when it misses graded work', prompt)
        self.assertIn('When it misses none (no quiz and no iClicker that day, or the instructor says there is none), the reply says so and asks '
                      'for nothing more', prompt)
        # ICLICKER NEEDS A NOTE (requirement, 2026-10-09).
        self.assertIn("A missed iClicker is excused only with documentation (a doctor's note for an illness)", prompt)
        self.assertIn("Answer every part of the student's message", prompt)
        self.assertIn('rework everything that followed from it in every card', prompt)
        # The student's absences in that window are named, so one is attached to rather than recorded twice.
        recorded = self.client.post('/api/absences', json={'section_id': item['student']['section_id'], 'enrollment_id': self.alice,
            'first_date': '2026-10-02', 'reason': 'illness', 'request_id': 'facts-absence'}, headers=HEADERS)
        self.assertEqual(recorded.status_code, 201, recorded.json)
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))
        self.assertIn(f"- Absence {recorded.json['id']}: Fri, Oct 2 · Illness · Waiting for your decision · 0 documents", prompt)
        self.assertIn('never record an absence overlapping one', prompt)
        self.assertIn('"documents": [{"message_id": ..., "index": n}]', prompt)  # an absence and its files are one card

    def test_the_preparation_offers_every_quiz_makeup_time_or_leaves_them_to_the_booking_card(self):
        # MAKEUP TIMES (requirement, 2026-10-04): a draft offered one Tuesday time for a Quiz 4 makeup and said "I'll follow up to
        # confirm". With the term's booking cards off the prompt lists every open time the card would offer; with them on, none.
        from datetime import datetime
        from absences import makeup_booking as booking_module
        self.incoming(at='2026-10-03T14:20:02Z', body='I was sick yesterday and missed the quiz. Can I make it up?')
        item = self.listing()['items'][0]
        section = item['student']['section_id']
        exams = self.app.extensions['frq_grading'].exams
        with connect(self.assistant.database) as db:
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (self.term,)).fetchone()[0]
        assignment = self.app.extensions['documents'].create_assignment(definition, 'quiz-4', 'Quiz 4')
        event = exams.create({'term_id': self.term, 'assignment_id': assignment['id'], 'section_ids': [section]})
        exams.save_sitting(event['id'], {'kind': 'main', 'label': 'In class', 'date': '2026-10-02', 'version': event['version']})
        calendar = self.app.extensions['calendar']
        for title, day, start, end, room in (('MATH 140 · Office hours', '2026-10-05', '15:00', '17:00', 'BLOC 309'),
                                             ('MATH 140 · Office hours', '2026-10-06', '10:00', '11:00', 'BLOC 319'),
                                             ('Math 664', '2026-10-06', '13:00', '14:00', 'BLOC 628'),  # takes the 1:00 and 1:30 starts
                                             ('Meeting', '2026-10-06', '15:00', '15:30', 'BLOC 202')):  # and 3:00, splitting the window
            calendar.create({'request_id': str(uuid.uuid4()), 'title': title, 'date': day, 'time': start, 'end_time': end,
                             'all_day': False, 'location': room})
        # MEETING HOURS (2026-10-05): the Tuesday window is the term's Tuesday meeting hours; NEXT TWO DAYS (2026-10-06): seen on Sunday,
        # the two days with times are Monday and Tuesday, so Thursday's are not offered.
        hours = self.client.post(f'/api/student-support/meeting-hours/{self.term}', headers=HEADERS, json={
            'request_id': 'hours', 'base_revision': 0,
            'makeup_rooms': ['BLOC 309'],
            'windows': [{'weekday': d, 'start': '13:00', 'end': '17:00', 'location': 'BLOC 319'} for d in (1, 3)]})
        self.assertEqual(hours.status_code, 200, hours.json)
        self.assertTrue(self.suggestions.process_one())

        def prompt():
            with patch.object(booking_module, 'campus_now', return_value=datetime(2026, 10, 4, 8, 0)), connect(self.assistant.database) as db:
                return self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))

        offered = prompt()
        self.assertIn("Quiz makeups (this term's booking cards are off)", offered)
        self.assertIn('- Quiz 4 (given Fri, Oct 2): Mon, Oct 5 · 3:00–5:00 pm · BLOC 309 · office hours, come any time; '
                      'Tue, Oct 6 · BLOC 319 · a time they choose between 2:00 pm and 3:00 pm or between 3:30 pm and 5:00 pm', offered)
        self.assertIn('never a list of half-hour slots', offered)  # "in an email it should just ask for a time between 1PM to 5PM"
        self.assertIn('never whether any of them works', offered)  # "ask which of these times work", not an invitation to say no
        self.assertIn('Never offer just one of them', offered)
        self.assertIn('offer every open time listed for that quiz and ask\nthe student which one works', offered)
        # The term's cards on: no time is listed; the card is how she picks, and the follow-up does not wait on her availability.
        turned = self.client.put(f'/api/absences/makeups/terms/{self.term}', json={'request_id': str(uuid.uuid4()), 'cards': True, 'revision': 0},
                                 headers=HEADERS)
        self.assertEqual(turned.status_code, 200, turned.json)
        carded = prompt()
        self.assertIn("Quiz makeups (this term's booking cards are on)", carded)
        self.assertIn('- Quiz 4 (given Fri, Oct 2): its booking card goes out once the absence over that day is excused', carded)
        self.assertIn('does not wait on their availability', carded)
        self.assertNotIn('office hours, come any time', carded)
        recorded = self.client.post('/api/absences', json={'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02',
                                                           'reason': 'illness', 'request_id': 'makeup-times-absence'}, headers=HEADERS)
        self.assertEqual(recorded.status_code, 201, recorded.json)
        excused = self.client.post(f"/api/absences/{recorded.json['id']}", json={'base_revision': recorded.json['revision'],
                                   'request_id': 'makeup-times-excused', 'status': 'excused'}, headers=HEADERS)
        self.assertEqual(excused.status_code, 200, excused.json)
        self.assertIn('- Quiz 4 (given Fri, Oct 2): its booking card goes out shortly', prompt())
        # ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP: the page's own read says so, beside the prompt's words.
        self.assertEqual([line['text'] for line in self.listing()['items'][0]['suggestions']['makeup_cards']],
                         ['Quiz 4 makeup: booking card goes out shortly, by itself, from your Gmail'])
        # A card withdrawn before it went out (2026-10-06): the reply offers the open times itself while the cards are on.
        booking = self.app.extensions['absences'].booking
        annotate = booking.annotate
        def withdrawn(db, rows, **options):
            found = annotate(db, rows, **options)
            for row in rows:
                if row.get('booking'):
                    row['booking'].update(state='expired', label='Card withdrawn before it went out', offer={'id': 'o1'})
            return found
        with patch.object(booking, 'annotate', side_effect=withdrawn):
            text = prompt()
        self.assertIn('- Quiz 4 (given Fri, Oct 2): its booking card was withdrawn, so the reply offers these times itself: '
                      'Mon, Oct 5 · 3:00–5:00 pm · BLOC 309 · office hours, come any time', text)
        self.assertIn('A quiz whose card was withdrawn is the exception', text)
        # MAKEUP HOLDS (requirement, 2026-10-06): a held absence's quiz gets no time and no card, cards on or off, and the absence
        # line names the hold's revision for a release.
        held = self.client.post(f"/api/absences/{recorded.json['id']}/makeup-hold", json={'request_id': 'hold', 'held': True,
                                'reason': 'her concussion reassessment', 'check_back': '2026-10-07'}, headers=HEADERS)
        self.assertEqual(held.status_code, 200, held.json)
        line = ('- Quiz 4 (given Fri, Oct 2): makeup held by the instructor (waiting on her concussion reassessment; check back Wed, Oct 7): '
                'no booking card goes out and no time is offered')
        self.assertIn(line, prompt())
        self.assertIn('its quiz makeups are held (hold revision 1; waiting on her concussion reassessment; check back Wed, Oct 7)', prompt())
        turned = self.client.put(f'/api/absences/makeups/terms/{self.term}', json={'request_id': str(uuid.uuid4()), 'cards': False, 'revision': 1},
                                 headers=HEADERS)
        self.assertEqual(turned.status_code, 200, turned.json)
        self.assertIn(line, prompt())
        self.assertNotIn('office hours, come any time', prompt())

    def test_the_preparation_lists_each_days_office_hours_room_so_a_reply_sets_a_wrong_room_right(self):
        # OFFICE HOURS (requirement, 2026-10-04): a student came to Friday office hours and looked "in your office in Blocker"; the
        # draft apologized and named Monday's room but "left out clarification about office hours location". The prompt lists each
        # day's room from the calendar and says to set the mix-up right.
        self.incoming(at='2026-10-04T23:40:59Z', body='I tried to go to your office hours on Friday, but you were not in your office in Blocker.')
        self.assertTrue(self.suggestions.process_one())

        def prompt():
            with connect(self.assistant.database) as db:
                return self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))

        self.assertNotIn('Your office hours', prompt())  # an empty calendar, no block
        calendar = self.app.extensions['calendar']
        blocks = [('MATH 140 · Office hours', day, start, end, room) for week in (('2026-09-28', '2026-09-29', '2026-10-01', '2026-10-02'),
                                                                                 ('2026-10-05', '2026-10-06', '2026-10-08', '2026-10-09'))
                  for day, (start, end, room) in zip(week, (('15:00', '17:00', 'BLOC 309'), ('10:00', '11:00', 'BLOC 319'),
                                                            ('10:00', '11:00', 'BLOC 319'), ('15:00', '17:00', 'BLOC 306')))]
        blocks += [('Jordan Example quiz makeup, drop-in during office hours', '2026-10-02', '16:00', '17:00', 'BLOC 999'),  # a student's
                   ('Extra office hours before Exam 2', '2026-10-07', '13:00', '14:00', 'BLOC 220')]                     # a one-off
        # A STUDENT'S HOLD (requirement, 2026-10-06): one student's reserved meeting window is not office hours for others.
        bob = self.support.people()[self.bob]['name']
        blocks += [(f'Office hours: {bob} (MATH 140-515)', '2026-10-08', '13:00', '14:20', 'BLOC 319')]
        for title, day, start, end, room in blocks:
            calendar.create({'request_id': str(uuid.uuid4()), 'title': title, 'date': day, 'time': start, 'end_time': end,
                             'all_day': False, 'location': room})
        shown = prompt()
        self.assertIn('Your office hours, from your calendar (a confirmed course record); the room depends on the day:\n'
                      '- Mondays · 3:00–5:00 pm · BLOC 309\n'
                      '- Tuesdays and Thursdays · 10:00–11:00 am · BLOC 319\n'
                      '- Fridays · 3:00–5:00 pm · BLOC 306\n'
                      '- Wed, Oct 7 only · 1:00–2:00 pm · BLOC 220\n', shown)
        self.assertNotIn('BLOC 999', shown)  # a makeup appointment is not an office-hours block
        self.assertNotIn('Thu, Oct 8 only', shown)  # nor is another student's appointment
        self.assertNotIn('Not on', shown)
        # AWAY (requirement, 2026-11-08): a block the calendar says they are away for is named, so no reply sends a student there.
        calendar.create({'request_id': str(uuid.uuid4()), 'title': 'Unavailable', 'date': '2026-10-09', 'time': '15:00',
                         'end_time': '23:59', 'all_day': False})
        self.assertIn('- Fridays · 3:00–5:00 pm · BLOC 306\n- Wed, Oct 7 only · 1:00–2:00 pm · BLOC 220\n'
                      '- Not on Fri, Oct 9 · 3:00–5:00 pm: your calendar says you are away\n', prompt())
        self.assertIn("another student's appointment there (an office-hours entry naming them) is neither office\nhours nor a reason "
                      'to narrow them, and a reply never mentions it.', shown)
        self.assertIn('set it right kindly in a sentence or two so it does not happen again: where office hours were that day, and '
                      "that the room depends on the day, with each day's room.", shown)
        self.assertIn('sets right any mix-up their message shows (a wrong room, time or date)', shown)

    def test_office_hours_are_offered_drop_in_and_only_a_meeting_outside_them_asks_for_a_time(self):
        # DROP-IN OFFICE HOURS (requirement, 2026-10-08): "it should tell the student anytime during those hours are ok. A
        # definite time is only required for non-regular office hours appointments".
        self.incoming(at='2026-10-08T18:00:00Z', body='Could I come by to go over my exam?')
        self.assertTrue(self.suggestions.process_one())
        for day in ('2026-10-02', '2026-10-09'):
            self.app.extensions['calendar'].create({'request_id': str(uuid.uuid4()), 'title': 'MATH 140 · Office hours', 'date': day,
                                                    'time': '15:00', 'end_time': '17:00', 'all_day': False, 'location': 'BLOC 306'})
        with connect(self.assistant.database) as db:
            shown = self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))
        self.assertIn('Regular office hours are drop-in: a reply that offers them names the day, hours and room and tells the student '
                      'to come any\ntime during them; it never asks which time they will come, and offering them proposes no '
                      'appointment. Only a meeting outside\nregular office hours asks the student for a definite time.', shown)
        self.assertIn('- Fridays · 3:00–5:00 pm · BLOC 306\n'
                      'Office hours are drop-in: a reply that offers them says the student can come any time during them and never '
                      'asks which time they will come (only a meeting outside office hours needs a definite time).', shown)
        guide = (Path(__file__).resolve().parent.parent / 'assistant' / 'guide.md').read_text()
        self.assertIn('- Regular office hours are drop-in. When you offer them, name the day, hours and room and say the student can '
                      'come any time\n  then', guide)

    def test_only_a_faculty_appointment_books_a_meeting_and_a_google_event_naming_the_student_books_nothing(self):
        # ONLY FACULTY BOOKS (requirement, 2026-10-06): a
        # Google event "Office hours: <the student> (MATH 140-515)" made outside Faculty held the time she picked, and the
        # preparation proposed no appointment, calling one a duplicate.
        self.incoming(at='2026-10-06T12:08:53Z', body='1:30 works best for me!')
        self.assertTrue(self.suggestions.process_one())

        def prompt():
            with connect(self.assistant.database) as db:
                return self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))

        self.assertNotIn('Their office-hours appointments in Faculty', prompt())  # nothing on either list: nothing said
        alice, bob = (self.support.people()[e]['name'] for e in (self.alice, self.bob))
        event = {'source': 'google', 'category': 'event', 'date': '2026-10-08', 'end_date': '2026-10-08', 'all_day': False}
        events = [{**event, 'id': 'google:a:hold', 'title': f'Office hours: {alice} (MATH 140-515)', 'time': '13:30', 'end_time': '14:20',
                   'location': 'BLOC  319'},
                  {**event, 'id': 'google:a:other', 'title': f'Office hours: {bob} (MATH 140-515)', 'time': '13:00', 'end_time': '14:20'},
                  {**event, 'id': 'google:a:note', 'title': f'{alice} out sick', 'time': None, 'all_day': True},
                  {**event, 'id': 'google:a:makeup', 'title': 'Alice Example quiz makeup', 'time': '15:00', 'end_time': '15:30'}]
        booking = self.app.extensions['absences'].booking
        with patch.object(booking, 'calendar_events', return_value=events):
            shown = prompt()
        self.assertIn('Their office-hours appointments in Faculty from Tue, Oct 6 on: none.\n'
                      'Calendar entries that name them but are not Faculty appointments (they book nothing):\n'
                      f'- Thu, Oct 8 · 1:30–2:20 pm · BLOC 319 · Google Calendar: “Office hours: {alice} (MATH 140-515)”\n'
                      'Only a Faculty appointment books a meeting. When a time is agreed and no appointment listed covers it, propose '
                      'the office-hours appointment for it even where one of these entries already holds that time (it is no '
                      'duplicate), and say in your explanation, never in the reply, that the entry is still on your calendar.', shown)
        self.assertNotIn(f'Office hours: {bob}', shown)  # another student's entry is not theirs
        self.assertNotIn('out sick', shown)  # an all-day note is not a time held
        self.assertNotIn('Alice Example quiz makeup', shown)  # a makeup follows the quiz makeup facts
        self.assertIn('Only that appointment books a meeting: a calendar entry naming the student that is not one (a Google Calendar\n'
                      'event made outside Faculty) books nothing, so an agreed time still gets its appointment.', shown)
        made = self.client.post('/api/student-support', json={'request_id': 'meet', 'kind': 'appointment', 'enrollment_id': self.alice,
                                                              'title': 'Office hours', 'date': '2026-10-08', 'time': '13:30',
                                                              'end_time': '14:00', 'location': 'BLOC 319'}, headers=HEADERS)
        self.assertEqual(made.status_code, 201, made.json)
        with patch.object(booking, 'calendar_events', return_value=events):
            shown = prompt()
        self.assertIn('Their office-hours appointments in Faculty from then on (each one books a meeting):\n'
                      '- Thu, Oct 8 · 1:30–2:00 pm · BLOC 319 · Office hours\n'
                      'Calendar entries that name them but are not Faculty appointments (they book nothing):\n', shown)

    def test_meeting_hours_are_the_terms_own_offered_as_open_times_and_bound_a_meeting_made_by_itself(self):
        # MEETING HOURS (requirement, 2026-10-05).
        from datetime import date, datetime
        from student_support import meeting_hours
        url = f'/api/student-support/meeting-hours/{self.term}'
        tuesday, thursday = ({'weekday': d, 'start': '13:00', 'end': '17:00', 'location': 'BLOC 319'} for d in (1, 3))

        def save(windows, base=0, request='hours'):
            return self.client.post(url, json={'request_id': request, 'base_revision': base, 'windows': windows}, headers=HEADERS)

        for wrong, why in (([{**tuesday, 'end': '13:15'}], 'at least 30 minutes'), ([tuesday, {**tuesday, 'start': '16:00', 'end': '18:00'}], 'overlap'),
                           ([{**tuesday, 'weekday': 7}], 'weekday'), ([{**tuesday, 'start': '1 pm'}], 'such as 13:00')):
            refused = save(wrong)
            self.assertEqual(refused.status_code, 400, wrong)
            self.assertIn(why, refused.json['error'])
        plain = self.client.get('/api/student-support/policies').data
        saved = save([thursday, tuesday])
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual((saved.json['revision'], saved.json['windows']), (1, [tuesday, thursday]))  # in weekday order
        self.assertEqual(save([thursday, tuesday]).json, saved.json)  # the same request again
        self.assertEqual(save([tuesday], request='hours').status_code, 409)  # its request ID with other hours
        self.assertEqual(save([tuesday], request='stale').status_code, 409)  # read against revision 0
        self.assertEqual(save([tuesday, thursday], base=1, request='same').json['revision'], 1)  # identical: nothing recorded
        # MAKEUP ROOMS (2026-10-05): the office-hours rooms big enough to seat a makeup, kept with the hours; left out, they stay.
        rooms = self.client.post(url, json={'request_id': 'rooms', 'base_revision': 1, 'windows': [tuesday, thursday],
                                            'makeup_rooms': ['BLOC 309', ' bloc  309 ', 'BLOC 306']}, headers=HEADERS)
        self.assertEqual((rooms.json['revision'], rooms.json['makeup_rooms']), (2, ['BLOC 309', 'BLOC 306']))
        self.assertEqual(save([tuesday, thursday], base=2, request='kept').json['makeup_rooms'], ['BLOC 309', 'BLOC 306'])
        self.assertEqual(self.client.post(url, json={'request_id': 'long', 'base_revision': 2, 'windows': [],
                                                     'makeup_rooms': ['x' * 81]}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.get('/api/student-support/policies').data, plain)  # the ordinary read is unchanged
        window = next(t for t in self.client.get('/api/student-support/policies?areas=1').json['terms'] if t['term_id'] == self.term)
        self.assertEqual((window['meeting_hours']['windows'], window['meeting_hours']['makeup_rooms']), ([tuesday, thursday], ['BLOC 309', 'BLOC 306']))
        self.assertEqual(policy.base_rule('student_support.save_meeting_hours').tier, 'approve')
        with connect(self.assistant.database) as db:
            self.assertEqual(meeting_hours.read(db, 'a-new-term')['windows'], [])  # another term starts with none
        self.assertEqual(meeting_hours.describe([tuesday, thursday]), 'Tuesdays and Thursdays · 1:00 pm–5:00 pm · BLOC 319')

        # A preparation lists the open times of the next two weeks from the calendar (a busy hour splits a window; the instructor's
        # own office-hours block does not) and says to offer them instead of asking for the student's availability.
        self.incoming(at='2026-10-03T15:00:00Z', body='Your office hours conflict with my classes. Could we meet another time?')
        self.assertTrue(self.suggestions.process_one())
        calendar = self.app.extensions['calendar']
        # A makeup takes a meeting's time (2026-10-05: "we should not overlap makeup sittings with non-makeup related meetings").
        for title, day, start, end in (('Committee', '2026-10-06', '14:00', '15:00'), ('MATH 140 · Office hours', '2026-10-08', '13:00', '14:00'),
                                       ('Jordan Example quiz makeup', '2026-10-13', '13:00', '13:30')):
            calendar.create({'request_id': str(uuid.uuid4()), 'title': title, 'date': day, 'time': start, 'end_time': end, 'all_day': False, 'location': ''})
        # ALL-DAY ENTRIES (2026-10-05, "Stop counting them"): an all-day note neither takes the day nor is mentioned.
        calendar.create({'request_id': str(uuid.uuid4()), 'title': 'Student absent (note)', 'date': '2026-10-15', 'all_day': True, 'location': ''})
        with patch('absences.makeup_booking.campus_now', return_value=datetime(2026, 10, 3, 12, 0)), connect(self.assistant.database) as db:
            shown = self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))
        self.assertIn('Meeting hours this term (the only times for a meeting outside office hours): Tuesdays and Thursdays · '
                      '1:00 pm–5:00 pm · BLOC 319. Open in the next two weeks, checked against your calendar:\n'
                      '- Tue, Oct 6 · BLOC 319 · any time between 1:00 pm and 2:00 pm, or between 3:00 pm and 5:00 pm\n'
                      '- Thu, Oct 8 · BLOC 319 · any time between 1:00 pm and 5:00 pm\n'
                      '- Tue, Oct 13 · BLOC 319 · any time between 1:30 pm and 5:00 pm\n'
                      '- Thu, Oct 15 · BLOC 319 · any time between 1:00 pm and 5:00 pm\n', shown)
        self.assertNotIn('all-day', shown)
        # MEETING LEAD (requirement, 2026-10-22): a meeting prints nothing, so today opens at the next half hour 30 minutes away, not 3 hours.
        with patch('absences.makeup_booking.campus_now', return_value=datetime(2026, 10, 8, 13, 18)), connect(self.assistant.database) as db:
            today = self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))
        self.assertIn('checked against your calendar:\n- Thu, Oct 8 · BLOC 319 · any time between 2:00 pm and 5:00 pm\n', today)
        self.assertIn('it never asks for their general availability', shown)
        self.assertIn('a meeting outside office hours is offered only inside them', shown)
        # THE STUDENT'S TIME (requirement, 2026-10-05): a time the student asked for that is open is taken and booked, not answered
        # with a list of other times.
        self.assertIn('When the student asked for a time inside one of these, take it (the start of what they name: "after 2:30" is '
                      '2:30): propose an office-hours appointment for it (never a calendar event) and confirm it in the reply, listing no other '
                      'times.', shown)
        self.assertIn('A time they asked for\nthat your reply accepts is agreed.', shown)
        # MEETING LENGTH (requirement, 2026-10-06): 30 minutes unless the student or the instructor names another length.
        self.assertIn('A meeting lasts 30 minutes\n(its end_time 30 minutes after its time, or when its open time ends if that is sooner)',
                      shown)
        self.assertIn('falls inside one of them and lasts 30 minutes (ending sooner when its open time does) unless the student asks '
                      'for another length', shown)
        # LENGTH UNSAID (requirement, 2026-10-06): the length sets only the end_time.
        self.assertIn('The length is for the appointment only: a reply never states how long a meeting lasts and never\nnarrows the '
                      'open times to fit it', shown)
        self.assertIn('the reply never states how long the meeting lasts and names these open times as listed, never narrowed to '
                      'fit it.', shown)

        # A meeting put on the calendar by itself falls inside them; anything else is a card saying why. Read on the day the message
        # was sent (Sat, Oct 3), like the prompt above: a month and day with no year is read against the day it was sent (JEV DATES),
        # so on the real clock "October 6" stops being a Tuesday ahead.
        self.today_is(date(2026, 10, 3))
        inside, outside = date(2026, 10, 6), date(2026, 10, 7)  # the Tuesday and the Wednesday ahead
        # JEV DATES: the day and time of a meeting made by itself are written in the conversation.
        session = self.session(body=f'Could we meet {inside:%B} {inside.day} at 1pm?')
        session['_followup_auto'] = True

        def meeting(when, start='14:00', end='14:30'):
            return self.tool_write(session, '/api/student-support', {'kind': 'appointment', 'enrollment_id': self.alice, 'title': 'Talk',
                                                                     'date': when.isoformat(), 'time': start, 'end_time': end})
        self.assertIn('outside this term’s meeting hours (Tuesdays and Thursdays · 1:00 pm–5:00 pm · BLOC 319)',
                      meeting(outside)['why_approval'])
        self.assertIn('outside this term’s meeting hours', meeting(inside, '16:45', '17:15')['why_approval'])
        # GOOGLE CHECKED (2026-10-05): while your Google calendar cannot be checked, the meeting is a card for the instructor to
        # decide, never booked by itself.
        gap = 'Faculty has not read your Google Calendar since Mon, Oct 5 at 3:05 PM'
        with patch.object(self.app.extensions['google_calendar'], 'unchecked', return_value=gap):
            self.assertIn(f'Not done by itself because your Google Calendar could not be checked ({gap})',
                          meeting(inside, '15:00', '15:30')['why_approval'])
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)).start()
        made = meeting(inside, '13:00', '13:30')  # from the hours' first minute, clear of that day's committee hour
        self.assertEqual(made['outcome'], 'done', made)

    def test_a_makeup_shares_its_time_with_makeups_but_never_with_a_meeting(self):
        # 2026-10-05: "we can even overlap makeup sittings then, but we should not overlap makeup sittings with non-makeup related
        # meetings"; a makeup typed as "1PM" lasts its student's quiz (here 30 minutes, 1.5x).
        from assistant.followups import calendar_note
        events = [{'id': 'exam:a', 'source': 'exam', 'title': 'Quiz 4 · Makeup', 'date': '2026-10-06', 'time': '1PM', 'end_time': '',
                   'all_day': False, 'makeup': True, 'minutes': 30},
                  {'id': 'office-hours:b', 'source': 'office_hours', 'title': 'Office hours', 'date': '2026-10-06', 'time': '13:40',
                   'end_time': '14:00', 'all_day': False}]
        meeting = calendar_note(events, {'date': '2026-10-06', 'time': '13:10', 'end_time': '13:30'})
        self.assertEqual([(e['title'], e['time'], e['end_time']) for e in meeting['overlaps']], [('Quiz 4 · Makeup', '13:00', '13:30')])
        makeup = calendar_note(events, {'date': '2026-10-06', 'time': '13:15', 'end_time': '', 'makeup': True, 'minutes': 30})
        self.assertEqual(([e['title'] for e in makeup['overlaps']], [e['title'] for e in makeup['within']], makeup['end_time']),
                         (['Office hours'], ['Quiz 4 · Makeup'], '13:45'))
        # ALL-DAY ENTRIES never count; GOOGLE CHECKED: why Google could not vouch for the time, else None.
        events += [{'id': 'google:a:note', 'source': 'google', 'title': 'Student absent (note)', 'date': '2026-10-05', 'end_date': '2026-10-07', 'all_day': True}]
        note = calendar_note(events, {'date': '2026-10-06', 'time': '15:00', 'end_time': '15:30'}, 'Faculty is not connected to your Google Calendar')
        self.assertEqual((note['overlaps'], note['within'], note['unchecked'], 'all_day' in note),
                         ([], [], 'Faculty is not connected to your Google Calendar', False))
        self.assertIsNone(meeting['unchecked'])

    def test_makeups_never_conflict_with_one_another(self):
        # ASSISTANT "MAKEUPS NEVER CONFLICT" (requirement, 2026-10-19).
        from assistant.followups import PROMPT, calendar_note
        events = [{'id': 'exam:q4', 'source': 'exam', 'title': 'Quiz 4 · Makeup', 'date': '2026-10-19', 'time': '15:00',
                   'end_time': '15:30', 'all_day': False, 'makeup': True, 'location': 'BLOC 309'},
                  {'id': 'custom:c', 'source': 'custom', 'title': 'Department meeting', 'date': '2026-10-19', 'time': '16:00',
                   'end_time': '17:00', 'all_day': False}]
        same = calendar_note(events, {'date': '2026-10-19', 'time': '15:00', 'end_time': '', 'makeup': True, 'minutes': 30})
        self.assertEqual((same['overlaps'], [e['title'] for e in same['within']]), ([], ['Quiz 4 · Makeup']))
        late = calendar_note(events, {'date': '2026-10-19', 'time': '15:45', 'end_time': '', 'makeup': True, 'minutes': 30})
        self.assertEqual([e['title'] for e in late['overlaps']], ['Department meeting'])
        self.assertIn('Makeups never conflict with one\nanother', PROMPT)
        guide = (Path(__file__).resolve().parent.parent / 'assistant' / 'guide.md').read_text()
        self.assertIn('Makeups never conflict with one another', guide)

    def test_the_evidence_check_reads_the_facts_given_and_the_cards_proposed(self):
        # EVIDENCE INPUT (requirement, 2026-10-05): the check
        # gets a short question, not the preparation's instruction, and reads Faculty's facts and the proposed cards first.
        from test_jev import response
        self.fake_mail()
        self.incoming(body='Could I meet you on Monday?')
        self.assertTrue(self.suggestions.process_one())
        seen = []

        def check(service, owner, query, answer, sources):
            seen.append((query, answer, [dict(source) for source in sources]))
            return {'status': 'checked', 'checks': [], 'sources': []}

        engine = ScriptedEngine(proposing(MAIL, text='I drafted a reply.'))
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)), \
                patch('jev.checks.evidence', side_effect=check), patch('assistant.conversations.engine_for', return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertEqual(len(seen), 1)
        query, answer, sources = seen[0]
        self.assertEqual(query, 'Prepare suggested actions for one student follow-up, for the instructor to review, and explain them.')
        self.assertEqual(answer, 'I drafted a reply.')
        given, cards = sources[:2]
        self.assertEqual((given['path'], given['kind']), ("Faculty's records given to this preparation", 'preparation'))
        self.assertIn('Selected follow-up (trusted Faculty identities)', given['text'])
        self.assertIn("Facts from Faculty's records", given['text'])
        self.assertNotIn('This is proposal-only work', given['text'])  # the instructions are not evidence
        self.assertEqual(cards['path'], 'Cards this preparation proposed (exact requests)')
        self.assertIn(REPLY, cards['text'])
        self.assertFalse(given['truncated'] or cards['truncated'])
        self.assertTrue(all(source['path'].startswith('/api/') for source in sources[2:]))  # then the turn's own reads

    def test_the_preparation_carries_the_instructors_notes_and_keeps_them_out_of_replies(self):
        # INSTRUCTOR'S NOTES (requirement, 2026-10-04).
        self.incoming(body='Can we meet again this week?')
        self.assertTrue(self.suggestions.process_one())

        def prompt():
            with connect(self.assistant.database) as db:
                return self.suggestions.prompt(self.suggestions.get(db, self.listing()['items'][0]['suggestions']['id']))

        self.assertNotIn('Your private notes about this student', prompt())  # no notes, no block
        # OTHER CONVERSATIONS AS BACKGROUND (requirement, 2026-10-04): the whole record is read; this conversation is the one answered.
        self.assertIn("Answer only this exact conversation; the student's other conversations are background", prompt())
        self.assertNotIn('use only this exact conversation', prompt())
        notes = []
        for day, text, request in (('2026-09-29', 'Booked a one-on-one\nfor Tuesday and did not show.', 'note-1'),
                                   ('2026-10-01', 'Came to office hours; worried about Exam 1.', 'note-2')):
            made = self.client.post('/api/student-notes', json={'enrollment_id': self.alice, 'date': day, 'text': text, 'request_id': request},
                                    headers=HEADERS)
            self.assertEqual(made.status_code, 201, made.json)
            notes.append(made.json)
        shown = prompt()
        self.assertIn('Your private notes about this student (newest day first)', shown)
        self.assertIn('a reply never quotes, mentions or hints at them', shown)
        self.assertLess(shown.index('- Thu, Oct 1: Came to office hours; worried about Exam 1.'),
                        shown.index('- Tue, Sep 29: Booked a one-on-one for Tuesday and did not show.'))
        first = next(n for n in notes[0]['notes'] if n['date'] == '2026-09-29')
        removed = self.client.post(f"/api/student-notes/{first['id']}/remove", json={'base_revision': first['revision'], 'request_id': 'note-1-x'},
                                   headers=HEADERS)
        self.assertEqual(removed.status_code, 200, removed.json)
        self.assertNotIn('did not show', prompt())  # a deleted note is not given

    # -- The instructor's review of 2026-10-03: fixes and controls (OWN WRITES, FOLLOW-UP REVIEW) ---------------------------------

    def view(self, action_id):
        return self.assistant._view(*self.assistant.action(action_id))

    def control(self, path, **data):
        return self.client.post('/api/student-support/followups/suggestions/' + path, json={'request_id': str(uuid.uuid4()), **data}, headers=HEADERS)

    def dispatch(self, respond=None):
        engine = ScriptedEngine(respond)
        with patch('assistant.conversations.engine_for', return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        return engine

    def test_own_reply_capture_keeps_the_other_cards_and_shows_the_conversation_as_replied(self):
        from datetime import date, timedelta
        from exam_workflow.overview import campus_today
        self.fake_mail()
        self.incoming()
        # A check-back day still ahead whichever day this runs: from that day on, a Waiting on student follow-up needs you again.
        check_back = (date.fromisoformat(campus_today()) + timedelta(days=7)).isoformat()
        def first(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, MEETING, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'waiting', 'date': check_back}, 'Wait for her answer'))(c, prompt)
        run, _ = self.prepare(first)
        meeting, mail, tracking = run['actions']
        self.assertEqual(self.approve(mail).status_code, 409)  # ASSISTANT: prerequisites precede messages.
        self.assertEqual(self.approve(meeting).status_code, 200)
        sent = self.approve(mail)
        self.assertEqual(sent.json['result']['status'], 'accepted', sent.json)
        # Gmail capture records the reply minutes later, in the student's thread, under the message ID Gmail gave the send.
        self.incoming(ident=sent.json['result']['message_id'], body=REPLY, at='2026-10-03T12:05:00Z', author='instructor')
        self.suggestions.process_one()
        self.assertEqual(self.client.get('/api/student-support?kind=followup').json['items'], [])  # the default read is as it was
        shown = self.listing()['items']
        self.assertEqual([(i['state'], i['suggestions']['state']) for i in shown], [('replied', 'ready')])
        waiting = [a for a in shown[0]['suggestions']['actions'] if a['pending']]
        self.assertEqual([a['id'] for a in waiting], [tracking['id']])
        # TEACHING-WORKFLOWS "NEEDS ME COUNT": the Replied conversation is on the page's Needs me tab, so the badge counts it.
        self.assertEqual(self.support.attention()['followup'], 1)
        # The reply's confirmed receipt lets the Waiting on student card of the same round go through.
        self.assertEqual(self.approve(waiting[0]).status_code, 200)
        self.assertEqual([(i['state'], i['attention']) for i in self.client.get('/api/student-support?kind=followup').json['items']], [('waiting', False)])
        self.assertEqual(self.support.attention()['followup'], 0)
        self.assertTrue(self.suggestions.process_one())  # settles; nothing more is prepared
        self.assertFalse(self.suggestions.dispatch_one())

    def test_someone_elses_reply_still_withdraws_the_waiting_cards(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        self.incoming(ident='typed-in-gmail', body='I answered this from my phone.', at='2026-10-03T12:05:00Z', author='instructor')
        self.suggestions.process_one()
        for card in run['actions']:
            current = self.view(card['id'])
            self.assertEqual(current['status'], 'expired')
            self.assertTrue(current['events'][-1]['detail']['superseded'])

    def test_an_unconfirmed_send_is_recognised_by_its_text(self):
        fake = self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        meeting, mail = run['actions']
        self.assertEqual(self.approve(meeting).status_code, 200)
        fake.lose_answer = True
        self.assertEqual(self.approve(mail).json['result']['status'], 'uncertain')
        # No message ID came back: the capture is recognised by its text, sent after Faculty recorded the send.
        from datetime import datetime, timezone
        self.incoming(ident='captured-later', body=REPLY + '\n', at=datetime.now(timezone.utc).isoformat(), author='instructor')
        self.suggestions.process_one()
        self.assertEqual(self.view(meeting['id'])['status'], 'succeeded')
        self.assertTrue(self.listing()['items'][0]['suggestions']['needs_attention'])
        self.assertEqual(len(fake.sent), 1)

    def test_own_tracking_change_keeps_the_other_cards_but_a_manual_edit_withdraws_them(self):
        self.incoming()
        observed = self.listing()['items'][0]['observed']
        later = ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'observed': observed,
                                          'title': 'Question', 'state': 'snoozed', 'date': '2099-01-01'}, 'Follow up later')
        run, _ = self.prepare(proposing(later, MEETING))
        meeting, track = run['actions']
        self.assertEqual(self.approve(track).status_code, 409)
        self.assertEqual(self.approve(meeting).status_code, 200)
        self.assertEqual(self.approve(track).status_code, 200)
        shown = self.listing()['items'][0]
        self.assertEqual((shown['state'], shown['suggestions']['state']), ('snoozed', 'ready'))
        self.assertEqual(self.view(meeting['id'])['status'], 'succeeded')
        self.support.save({'base_revision': shown['revision'], 'title': 'Question', 'state': 'snoozed', 'date': '2099-02-01',
                           'observed': observed, 'request_id': 'by-hand'}, shown['id'])
        self.suggestions.process_one()
        self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'superseded')
        self.assertEqual(self.view(meeting['id'])['status'], 'succeeded')

    def test_a_failed_start_or_a_lost_claim_never_blocks_the_queue(self):
        self.incoming()
        self.incoming(ident='c1', thread='c', enrollment=self.bob, body='Another question')
        self.assertTrue(self.suggestions.process_one())
        self.assertTrue(self.suggestions.process_one())
        with patch.object(self.assistant, 'open_session', side_effect=RuntimeError('engine missing')):
            self.assertFalse(self.suggestions.dispatch_one())
        self.assertEqual(self.suggestions.busy(), 0)
        states = {i['enrollment_id']: i['suggestions'] for i in self.listing()['items']}
        failed = next(s for s in states.values() if s['state'] == 'failed')
        self.assertIn('could not start', failed['error'])
        queued = next(s for s in states.values() if s['state'] == 'queued')
        # A run left marked running with nothing active (its result could not be recorded) is interrupted, never a wedge.
        with self.rosters_lock(), connect(self.assistant.database) as db:
            self.suggestions.event(db, queued['id'], 'running')
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine()):
            self.assertFalse(self.suggestions.dispatch_one())
        states = {s['id']: s['state'] for s in (i['suggestions'] for i in self.listing()['items'])}
        self.assertEqual(sorted(states.values()), ['failed', 'interrupted'])
        self.assertEqual(self.request(self.listing()['items'][0]['suggestions'], 'retry').status_code, 202)
        self.dispatch()

    def test_a_withdrawn_card_may_be_proposed_again_but_not_one_you_declined(self):
        self.incoming()
        run, _ = self.prepare()
        self.incoming(ident='a2', body='Also, is Monday OK?', at='2026-10-03T12:30:00Z')
        again = proposing(MEETING)
        fresh, _ = self.prepare(again)
        self.assertEqual(again.outcomes[0]['outcome'], 'waiting_for_approval', again.outcomes)
        self.assertNotEqual(fresh['actions'][0]['id'], run['actions'][0]['id'])
        self.client.post('/api/assistant/actions/' + fresh['actions'][0]['id'] + '/decline', json={}, headers=HEADERS)
        self.incoming(ident='a3', body='And Tuesday?', at='2026-10-03T12:40:00Z')
        once_more = proposing(MEETING)
        self.prepare(once_more)
        self.assertEqual(once_more.outcomes[0]['outcome'], 'already_recorded', once_more.outcomes)

    def test_a_card_replaced_by_your_request_may_be_proposed_again_after_a_new_message(self):
        # TERM POLICIES: a card your Ask for changes replaced was no refusal, so the automatic preparation after the student's
        # next message may propose it again (2026-10-19, Laura Dunham's release and booking card refused as already recorded).
        self.incoming()
        run, _ = self.prepare()
        self.assertEqual(self.request(run, 'revise', text='Offer another day.').status_code, 202)
        replaced = self.view(run['actions'][0]['id'])
        self.assertEqual(replaced['status'], 'declined')
        self.assertTrue(replaced['events'][-1]['detail']['replaced_by_request'])
        revised = proposing(MEETING)
        self.dispatch(revised)
        self.assertEqual(revised.outcomes[0]['outcome'], 'waiting_for_approval', revised.outcomes)
        self.incoming(ident='a2', body='Also, is Monday OK?', at='2026-10-03T12:30:00Z')
        again = proposing(MEETING)
        fresh, _ = self.prepare(again)
        self.assertEqual(fresh['reason'], 'automatic')
        self.assertEqual(again.outcomes[0]['outcome'], 'waiting_for_approval', again.outcomes)
        self.assertNotEqual(fresh['actions'][0]['id'], run['actions'][0]['id'])

    def test_instructions_and_notes_carry_across_new_messages(self):
        self.incoming()
        run, _ = self.prepare()
        card = run['actions'][0]
        note = lambda **data: self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/note", json={'request_id': 'n1',
            'action_id': card['id'], 'text': 'Never offer Monday meetings.', **data}, headers=HEADERS)
        self.assertEqual(note().status_code, 409)  # a note belongs to a card you declined
        self.client.post('/api/assistant/actions/' + card['id'] + '/decline', json={}, headers=HEADERS)
        from test_assistant_memory import answering
        # ASSISTANT-MEMORY "A FOLLOW-UP'S REMEMBER THIS": checked as a chat message is. A note about this one student is
        # not kept as a preference, and the page is told why; the note itself is saved either way.
        with answering(names_student=.9, about_student=.9):
            refused = note(remember=True, request_id='n0', text='She has lab on Mondays.')
        self.assertEqual(refused.status_code, 200, refused.json)
        self.assertIn('about one student', refused.json['memory_error'])
        with answering():
            saved = note(remember=True)
        self.assertEqual(saved.status_code, 200, saved.json)
        # ASSISTANT-MEMORY "POINTED AT THE STUDENT ON SCREEN": "She" cannot be kept verbatim as a general preference.
        # The successful case uses an actual general rule; the student's schedule remains only in the earlier note.
        self.assertTrue(saved.json['remembered'])
        self.assertEqual(saved.json['memory'], {'id': saved.json['remembered'], 'content': 'Never offer Monday meetings.', 'ruled': False, 'standalone': True})
        self.assertEqual(note(remember=True).json, {'saved': True})
        self.assertEqual(note(text='Different').status_code, 409)
        run = self.listing()['items'][0]['suggestions']
        self.assertEqual(run['actions'][0]['note'], 'Never offer Monday meetings.')
        with answering():
            revised = self.request(run, 'revise', text='Never offer Tuesday meetings either.', remember=True)
        self.assertEqual(revised.status_code, 202, revised.json)
        self.assertTrue(revised.json['remembered'])
        self.incoming(ident='a2', body='One more thing', at='2026-10-03T12:30:00Z')
        self.assertTrue(self.suggestions.process_one())
        engine = self.dispatch()
        for words in ('Never offer Tuesday meetings either.', 'Never offer Monday meetings.', 'Declined by the instructor.', 'Follow-up drafts:'):
            self.assertIn(words, engine.prompt)
        receipts = json.loads(engine.prompt.split('Prior proposed actions and retained outcomes:\n', 1)[1].split('\n', 1)[0])
        declined = next(action for action in receipts if action['id'] == card['id'])
        self.assertTrue(declined['instructor_declined'])
        self.assertFalse(declined['replaced_by_request'])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assistant_memory_items').fetchone()[0], 2)

    def test_plain_retry_supersedes_unsent_proposals_without_requiring_revision_notes(self):
        # TERM POLICIES / ASSISTANT: Retry replaces drafts; it is not a rejection or a missing revision request.
        fake = self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        meeting, mail = run['actions']
        self.assertEqual(self.approve(meeting).status_code, 200)
        default = self.client.get('/api/student-support?kind=followup').data
        saved = self.save_areas({'reply_style': {'mode': 'guidance', 'guidance': 'Use the latest saved guidance for this reply.'}})
        self.assertEqual(saved.status_code, 200, saved.json)
        shown = self.listing()['items'][0]['suggestions']
        retried = self.request(shown, 'retry')
        self.assertEqual(retried.status_code, 202, retried.json)
        old = self.view(mail['id'])
        self.assertTrue(old['events'][-1]['detail']['replaced_by_request'])
        self.assertIn('Retry suggestions', old['events'][-1]['detail']['error'])
        # Existing catalogs have this earlier wording with no operation-specific text. Interpret its marker, not its prose.
        self.assistant._event(mail['id'], 'declined', 'instructor', {
            'error': 'Replaced by the changes you asked for.', 'replaced_by_request': True})
        again = proposing(MAIL, MEETING)
        def verify(c, prompt):
            self.assertIn('Current instructor request: Retry suggestions.', prompt)
            self.assertIn('An empty retry instruction is intentional', prompt)
            self.assertIn('Use the latest saved guidance for this reply.', prompt)
            receipts = json.loads(prompt.split('Prior proposed actions and retained outcomes:\n', 1)[1].split('\n', 1)[0])
            previous = next(action for action in receipts if action['id'] == mail['id'])
            self.assertTrue(previous['replaced_by_request'])
            self.assertFalse(previous['instructor_declined'])
            self.assertEqual(next(action for action in receipts if action['id'] == meeting['id'])['status'], 'succeeded')
            self.assertFalse(c.run['instruction'])
            again(c, prompt)
        self.dispatch(verify)
        fresh = self.listing()['items'][0]['suggestions']
        self.assertEqual(fresh['state'], 'ready')
        self.assertEqual(fresh['policy_revision'], saved.json['revision'])
        self.assertEqual([a['endpoint'] for a in fresh['actions']], ['gmail_mail.send'])
        self.assertTrue(fresh['actions'][0]['pending'])
        self.assertNotEqual(fresh['actions'][0]['id'], mail['id'])
        self.assertEqual([result['outcome'] for result in again.outcomes], ['waiting_for_approval', 'already_recorded'])
        self.assertEqual(fake.sent, [])
        self.assertEqual(self.view(meeting['id'])['status'], 'succeeded')
        self.assertEqual(self.client.get('/api/student-support?kind=followup').data, default)

    def test_approve_all_runs_in_order_and_stops_after_an_unconfirmed_send(self):
        fake = self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        meeting, mail = run['actions']
        seen = {a['id']: a['seen'] for a in run['actions']}
        stale = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/approve", json={'seen': {**seen, mail['id']: mail['seen'] + 1000}}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(fake.sent, [])
        self.assertEqual(self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/approve", json={'seen': {'other': 1}}, headers=HEADERS).status_code, 400)
        fake.lose_answer = True
        result = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/approve", json={'seen': seen}, headers=HEADERS).json
        self.assertEqual([r['id'] for r in result['results']], [meeting['id'], mail['id']])
        self.assertEqual(result['remaining'], [])
        self.assertEqual(self.view(meeting['id'])['status'], 'succeeded')
        self.assertEqual(len(fake.sent), 1)
        self.assertTrue(self.listing()['items'][0]['suggestions']['needs_attention'])
        with self.rosters_lock(), connect(self.assistant.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items WHERE kind='appointment'").fetchone()[0], 1)

    def test_edit_in_mail_replaces_the_card_and_counts_as_the_reply(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        meeting, mail = run['actions']
        def send(to, body):
            data = {'request_id': str(uuid.uuid4()), 'to': to, 'subject': 'Re: Question', 'body': body}
            review = self.client.post('/api/mail/preview', json=data, headers=HEADERS)
            self.assertEqual(review.status_code, 200, review.json)
            return self.client.post('/api/mail/send', json={**data, 'fingerprint': review.json['fingerprint'], 'confirmation': 'send'}, headers=HEADERS).json, data['request_id']
        def replace(send_request, request_id):
            return self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/replace", json={'action_id': mail['id'],
                'seen': mail['seen'], 'send_request_id': send_request, 'request_id': request_id}, headers=HEADERS)
        _, elsewhere = send('bob@example.test', 'This one is for Bob.')
        self.assertEqual(replace(elsewhere, 'r1').status_code, 400)
        mine = 'My own words: come on Monday at 2 pm and we will go through it together.'
        edited, edited_request = send('alice@example.test', mine)
        self.assertEqual(replace(edited_request, 'r2').json, {'action_id': mail['id'], 'status': 'accepted'})
        self.assertEqual(replace(edited_request, 'r2').status_code, 200)
        card = next(a for a in self.listing()['items'][0]['suggestions']['actions'] if a['id'] == mail['id'])
        self.assertEqual((card['status'], card['replaced']['status'], card['declined_by_you']), ('declined', 'accepted', False))
        self.incoming(ident=edited['message_id'], body=mine, at='2026-10-03T12:05:00Z', author='instructor')
        self.suggestions.process_one()
        self.assertEqual(self.approve(self.view(meeting['id'])).status_code, 200)
        self.assertTrue(self.suggestions.process_one())  # the run settles (ONE ROUND: no follow-through)
        self.assertFalse(self.suggestions.dispatch_one())
        with connect(self.assistant.database) as db:  # any later preparation of this follow-up reads the outcome
            prompt = self.suggestions.prompt(self.suggestions.get(db, run['id']))
        self.assertIn('sent an edited version from Mail instead (accepted)', prompt)
        self.assertIn(mine, prompt)

    def test_edit_in_place_puts_your_version_on_the_card_and_sends_nothing(self):
        # EDIT IN PLACE (requirement, 2026-10-04).
        google = self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        meeting, mail = run['actions']
        mine = 'Hi Alice,\n\nCome by Monday at 2 pm and we will go through it.\n\nBest,\nChris'
        def edit(card, request_id, **changes):
            return self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={'action_id': card['id'],
                'seen': card['seen'], 'subject': 'Re: Question', 'body': mine, 'request_id': request_id, **changes}, headers=HEADERS)
        self.assertEqual(edit(meeting, 'e0').status_code, 409)  # only a mail card is edited here
        self.assertEqual(edit(mail, 'e0', body=REPLY, subject='Re: Question').status_code, 400)  # nothing changed
        self.assertEqual(edit({**mail, 'seen': mail['seen'] - 1}, 'e0').status_code, 409)
        saved = edit(mail, 'e1')
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(edit(mail, 'e1').json, saved.json)  # the same request again is the same edit
        self.assertEqual(edit(mail, 'e1', body='Something else').status_code, 409)
        self.assertEqual(google.sent, [])
        shown = self.listing()['items'][0]['suggestions']['actions']
        # Your version stands in the draft's place; the replaced draft is not shown, and it is not your decline.
        self.assertEqual([a['id'] for a in shown], [meeting['id'], saved.json['action_id']])
        card = shown[1]
        self.assertTrue(card['pending'] and card['edited'])
        self.assertEqual((card['body']['body'], card['body']['to'], card['endpoint']), (mine, MAIL[1]['to'], 'gmail_mail.send'))
        self.assertEqual(card['events'][0]['actor'], 'instructor')  # recorded as the instructor's, not the assistant's
        old = self.view(mail['id'])
        self.assertEqual((old['status'], old['events'][-1]['detail']), ('declined', {'replaced_by_edit': card['id']}))
        self.assertEqual(edit(mail, 'e2', body='Again').status_code, 409)  # the replaced draft no longer waits
        self.assertEqual(policy.base_rule('student_support.suggestion_edit').tier, 'never')
        # Send is still the instructor's own click, and it sends the edited text.
        self.assertEqual(self.approve(meeting).status_code, 200)
        self.assertEqual(self.approve(card).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        self.assertIn('Come by Monday at 2 pm', google.parsed(0).get_body(('plain',)).get_content())
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(self.suggestions.get(db, run['id']))
        self.assertIn('The instructor edited this draft; their edited version is the card ' + card['id'], prompt)

    def test_pause_prepare_now_and_the_per_conversation_switch(self):
        self.incoming()
        self.incoming(ident='c1', thread='c', enrollment=self.bob, body='Another question', at='2026-10-03T12:10:00Z')
        waiting = self.listing()['items']
        self.assertEqual({i['suggestions']['state'] for i in waiting}, {'queued'})
        self.assertIn('Inbox worker', waiting[0]['suggestions']['note'])  # no worker heartbeat in this fixture
        self.assertEqual(self.control('pause', paused=True).json, {'paused': True})
        self.assertFalse(self.suggestions.process_one())
        listing = self.listing()
        self.assertTrue(listing['suggestion_settings']['paused'])
        self.assertEqual({i['suggestions']['state'] for i in listing['items']}, {'paused'})
        bob = next(i for i in listing['items'] if i['enrollment_id'] == self.bob)
        self.assertEqual(self.control('prepare', enrollment_id=self.bob, item_key=bob['suggestions']['item_key']).status_code, 202)
        self.assertFalse(self.suggestions.process_one())  # still paused: Alice waits
        self.assertIn(self.bob, self.dispatch().prompt)
        self.assertEqual(self.control('pause', paused=False).status_code, 200)
        self.assertTrue(self.suggestions.process_one())
        alice = next(i for i in self.listing()['items'] if i['enrollment_id'] == self.alice)
        self.assertEqual(alice['suggestions']['queue'], {'position': 1, 'total': 1, 'held': False})
        self.assertEqual(self.control('mute', enrollment_id=self.alice, item_key=alice['suggestions']['item_key'], muted=True).status_code, 200)
        self.assertFalse(self.suggestions.process_one())
        alice = next(i for i in self.listing()['items'] if i['enrollment_id'] == self.alice)
        self.assertEqual((alice['suggestions']['state'], alice['suggestions']['muted']), ('muted', True))
        self.assertFalse(self.suggestions.dispatch_one())
        self.assertEqual(self.control('mute', enrollment_id=self.alice, item_key=alice['suggestions']['item_key'], muted=False).status_code, 200)
        self.assertTrue(self.suggestions.process_one())
        self.assertEqual(next(i for i in self.listing()['items'] if i['enrollment_id'] == self.alice)['suggestions']['state'], 'queued')
        self.assertEqual(self.control('prepare', enrollment_id=self.bob, item_key=bob['suggestions']['item_key']).status_code, 409)

    def test_your_own_requests_go_before_the_automatic_queue(self):
        self.incoming()
        run, _ = self.prepare()
        self.incoming(ident='c1', thread='c', enrollment=self.bob, body='Another question', at='2026-10-03T12:10:00Z')
        self.assertTrue(self.suggestions.process_one())
        self.assertEqual(self.request(run, 'revise', text='Offer Friday instead.').status_code, 202)
        queue = {i['enrollment_id']: i['suggestions']['queue']['position'] for i in self.listing()['items']}
        self.assertEqual(queue, {self.alice: 1, self.bob: 2})
        self.assertIn('Offer Friday instead.', self.dispatch().prompt)
        self.assertIn(self.bob, self.dispatch().prompt)

    def test_meeting_cards_show_the_calendar_as_it_is_now(self):
        self.incoming()
        run, _ = self.prepare()
        self.assertEqual(run['actions'][0]['calendar'], {'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30', 'overlaps': [], 'within': [],
                                                         'unchecked': None})
        for title, start, end in (('MATH 140 lecture', '14:00', '14:50'), ('Office Hours', '13:00', '15:00'), ('Later meeting', '15:00', '16:00')):
            created = self.client.post('/api/calendar/events', json={'request_id': str(uuid.uuid4()), 'scope': 'workspace', 'category': 'event',
                'title': title, 'date': '2026-10-12', 'all_day': False, 'time': start, 'end_time': end}, headers=HEADERS)
            self.assertEqual(created.status_code, 201, created.json)
        note = self.listing()['items'][0]['suggestions']['actions'][0]['calendar']
        self.assertEqual([e['title'] for e in note['overlaps']], ['MATH 140 lecture'])
        self.assertEqual([e['title'] for e in note['within']], ['Office Hours'])
        with connect(self.assistant.database) as db:
            row = self.suggestions.get(db, run['id'])
        self.assertIn('/api/calendar?start=', self.suggestions.prompt(row))

    def test_runs_recorded_before_the_own_writes_rule_keep_their_comparison(self):
        self.incoming()
        with self.rosters_lock(), connect(self.assistant.database) as db:
            db.execute('BEGIN IMMEDIATE')
            item = self.suggestions.index(db)[(self.alice, 'conversation:gmail:b1')]
            older = self.suggestions.insert(db, self.suggestions.target(item), self.suggestions.snapshot_v1(db, item), {'name': 'Older'})
            self.suggestions.event(db, older, 'ready', {'explanation': 'Prepared earlier.'})
            db.commit()
        self.assertFalse(self.suggestions.process_one())
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual((shown['id'], shown['state']), (older, 'ready'))
        revised = self.request(shown, 'revise', text='Shorter, please.')
        self.assertEqual(revised.status_code, 202, revised.json)
        with connect(self.assistant.database) as db:
            source = json.loads(db.execute('SELECT source_json FROM student_support_suggestion_runs WHERE id=?', (revised.json['id'],)).fetchone()[0])
        self.assertEqual(source['v'], 2)

    def test_each_waiting_card_says_what_it_would_do_in_plain_words(self):
        # FOLLOW-UP CLARITY (requirement, 2026-10-03).
        self.incoming()
        item = self.listing()['items'][0]
        tracked = self.client.post('/api/student-support', json={'request_id': 'track-clarity', 'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Question', 'state': 'needs_reply'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        self.suggestions.process_one()
        run = self.listing()['items'][0]['suggestions']
        with connect(self.assistant.database) as db:  # ASSISTANT CONTEXT: as a preparation records it when it starts
            self.suggestions.context.remember(db, self.suggestions.get(db, run['id']))
        session = self.assistant.open_session('test')
        session.update(_proposal_only=True, _followup_run=run['id'])
        section = item['student']['section_id']
        absence = self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'status': 'pending',
            'first_date': '2026-09-30', 'last_date': '2026-10-02', 'reason': 'illness', 'details': 'Concussion; note promised.',
            'notice_message_id': self.notice()})
        meeting = self.tool_write(session, '/api/student-support', {'kind': 'appointment', 'enrollment_id': self.bob, 'title': 'Meet',
            'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30', 'location': 'BLOC 306'})
        resolve = self.tool_write(session, '/api/student-support/' + tracked.json['id'], {'base_revision': 1, 'title': 'Question',
            'state': 'resolved', 'details': 'Answered in class.', 'observed': item['observed']})
        setup = self.tool_write(session, '/api/gradebook/terms/' + self.term + '/setup', {'scale': 'letters'})
        documented = self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'status': 'pending',
            'first_date': '2026-10-05', 'reason': 'illness', 'documents': [{'message_id': 'a-message', 'index': 0}], 'notice_message_id': self.notice()})
        # A revision names only the record's ID: it belongs to this student, so no wider-scope warning (the reason said so before).
        self.assertNotIn('not limited', resolve['why_approval'])
        cards = {a['id']: a for a in self.listing()['items'][0]['suggestions']['actions']}
        name = item['student']['name']
        effect = cards[absence['action_id']]['effect']
        self.assertEqual((effect['title'], effect['button'], effect['own'], effect['who'], effect['where'], effect['href']),
                         ('Record an absence', 'Record absence', True, f'{name} (this student)', 'Absences', '#absences'))
        self.assertEqual([(c['label'], c['value']) for c in effect['changes']][1:], [('Dates', 'Wed, Sep 30 – Fri, Oct 2'), ('Reason', 'Illness'),
            ('Decision', 'Waiting for your decision'), ('Details', 'Concussion; note promised.'),
            ('Notice', 'Question · Sat, Oct 3, 7:00 AM · Notice after it began')])
        self.assertTrue(effect['changes'][0]['value'].startswith('Section '))
        self.assertIn('waits on the Absences page', effect['notes'][0])
        self.assertEqual(effect['audience'], 'Saved in Faculty only. The student is not told.')
        effect = cards[meeting['action_id']]['effect']
        self.assertEqual((effect['title'], effect['own'], effect['who']), ('Schedule office hours', False, self.support.people()[self.bob]['name']))
        self.assertIn(('When', 'Mon, Oct 12, 2:00 PM–2:30 PM'), [(c['label'], c['value']) for c in effect['changes']])
        effect = cards[resolve['action_id']]['effect']
        self.assertEqual((effect['title'], effect['own']), ('Mark this follow-up resolved', True))
        self.assertEqual(effect['changes'], [{'label': 'Status', 'value': 'Resolved', 'before': 'Needs my reply'},
                                             {'label': 'Private details', 'value': 'Answered in class.', 'before': '(empty)'}])
        effect = cards[documented['action_id']]['effect']  # ABSENCES WITH DOCUMENTS: one card records it and keeps the files
        self.assertIn({'label': 'Documentation', 'value': 'an attachment'}, effect['changes'])
        self.assertEqual(effect['notes'][0], 'Keeps the files the student sent with the absence as its documentation.')
        effect = cards[setup['action_id']]['effect']
        self.assertEqual((effect['title'], effect['changes']), ('Save a change on Gradebook', [{'label': 'Scale', 'value': 'letters'}]))
        for card in cards.values():  # bookkeeping never reads as a change
            self.assertFalse({'Request id', 'Enrollment id', 'Section id', 'Base revision', 'Observed'} & {c['label'] for c in card['effect']['changes']})
        # An excusing revision says what follows from it; a request that no longer routes reads as written (no effect).
        self.assertEqual(self.approve(self.assistant._view(*self.assistant.action(absence['action_id']))).status_code, 200)
        with connect(self.assistant.database) as db:
            absence_id, = db.execute('SELECT id FROM absences').fetchone()
        self.app.extensions['absences'].upload(absence_id, b'%PDF-1.4 a clinic note', 'note.pdf', 'upload-note')  # EXCUSE NEEDS EVIDENCE
        excuse = self.tool_write(session, '/api/absences/' + absence_id, {'base_revision': 1, 'status': 'excused'})
        self.assertNotIn('not limited', excuse['why_approval'])
        effect = next(a for a in self.listing()['items'][0]['suggestions']['actions'] if a['id'] == excuse['action_id'])['effect']
        self.assertEqual(effect['title'], 'Excuse an absence')
        # ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP: a card that sets off a booking card says so on its face (the page's read reaches it).
        with patch.object(self.app.extensions['absences'].booking, 'excuse_cards', return_value=['Quiz 4']):
            told = next(a for a in self.listing()['items'][0]['suggestions']['actions'] if a['id'] == excuse['action_id'])['effect']
        self.assertIn('Faculty emails them a booking card for Quiz 4 by itself', next(c['value'] for c in told['changes'] if c['label'] == 'Then'))
        self.assertEqual(told['audience'], 'Saved in Faculty. The booking card above is all this step sends the student.')
        self.assertEqual(effect['changes'], [{'label': 'Decision', 'value': 'Excused', 'before': 'Waiting for your decision', 'choice': {
            'value': 'excused', 'options': [{'value': key, 'label': label} for key, label in (('pending', 'Waiting for your decision'),
                ('excused', 'Excused'), ('not_excused', 'Not excused'), ('withdrawn', 'Withdrawn'))]}}])
        self.assertIn('iClicker', effect['notes'][0])
        # Attaching the student's own file to their absence names the file and the absence, and is not wider in scope.
        with connect(self.assistant.database) as db:
            message_id, = db.execute('SELECT id FROM comm_messages LIMIT 1').fetchone()
        attach = self.tool_write(session, f'/api/absences/{absence_id}/documents/message', {'message_id': message_id, 'index': 0})
        self.assertNotIn('not limited', attach['why_approval'])
        effect = next(a for a in self.listing()['items'][0]['suggestions']['actions'] if a['id'] == attach['action_id'])['effect']
        self.assertEqual((effect['title'], effect['own'], effect['button']), ('Attach a file to the absence record', True, 'Attach file'))
        self.assertEqual(effect['changes'][1], {'label': 'Absence', 'value': 'Wed, Sep 30 – Fri, Oct 2 · Waiting for your decision'})
        # RULE 7 NOTICE (requirement, 2026-10-06): a revision that only names the
        # student's notice says so, as Absences will read it, instead of "Nothing visible changes".
        cleared = self.client.post('/api/absences/' + absence_id, json={'base_revision': 1, 'notice_message_id': None,
                                                                        'request_id': 'clear-notice'}, headers=HEADERS)
        self.assertEqual(cleared.status_code, 200, cleared.json)
        linked = self.tool_write(session, '/api/absences/' + absence_id, {'base_revision': 2, 'notice_message_id': self.notice()})
        effect = next(a for a in self.listing()['items'][0]['suggestions']['actions'] if a['id'] == linked['action_id'])['effect']
        self.assertEqual((effect['title'], effect['changes']), ('Record their notice for an absence', [{'label': 'Notice',
            'value': 'Question · Sat, Oct 3, 7:00 AM · Notice after it began', 'before': 'No notice on record'}]))
        self.assertEqual(effect['notes'], ['Absences shows when they told you, against Student Rule 7. That decides nothing by itself.',
                                           'It waits on the Absences page for your decision. Nothing is excused and no grade changes.'])
        from assistant.followup_effects import Describer
        with connect(self.assistant.database) as db:
            row = dict(self.suggestions.get(db, run['id']))
            self.assertIsNone(Describer(db, self.assistant.transport, row, item['student'], None).describe({'method': 'POST', 'path': '/api/nowhere', 'body': {}}))

    def test_the_decision_on_an_absence_card_is_chosen_from_its_dropdown(self):
        # DECISION ON THE CARD (requirement, 2026-10-12): the card's Decision line names its choices, and the instructor's
        # choice is a new waiting card in the old one's place. Nothing is recorded until they approve it.
        session = self.session()
        item = self.listing()['items'][0]
        run, section = item['suggestions'], item['student']['section_id']
        def edit(card, **fields):
            return self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={
                'action_id': card['id'], 'seen': card['seen'], 'request_id': uuid.uuid4().hex, **fields}, headers=HEADERS)
        def cards():
            return self.listing()['items'][0]['suggestions']['actions']
        def shown(ident):
            return next(a for a in cards() if a['id'] == ident)
        def decision(card):
            return next(c for c in card['effect']['changes'] if c['label'] == 'Decision')
        labels = {'pending': 'Waiting for your decision', 'excused': 'Excused', 'not_excused': 'Not excused', 'withdrawn': 'Withdrawn'}
        facts = {'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02', 'reason': 'illness',
                 'notice_message_id': self.notice()}
        made = self.tool_write(session, '/api/absences', {**facts, 'status': 'pending'})
        card = shown(made['action_id'])
        # The line defaults to what the Assistant suggested; a new absence is never recorded as withdrawn.
        self.assertEqual(decision(card), {'label': 'Decision', 'value': 'Waiting for your decision', 'choice': {'value': 'pending',
            'options': [{'value': key, 'label': labels[key]} for key in ('pending', 'excused', 'not_excused')]}})
        for refused, said in ((edit(card, status='pending'), 'Nothing changed.'),
                              (edit(card, status='withdrawn'), 'Choose one of the decisions this card offers.'),
                              (edit(card, status=None), 'Name the card, the version you saw, and the decision.')):
            self.assertEqual((refused.status_code, refused.json['error']), (400, said))
        self.assertEqual(edit({**card, 'seen': card['seen'] + 1}, status='excused').status_code, 409)
        self.assertEqual([a['id'] for a in cards()], [card['id']])  # a refused choice leaves the card as it was
        # Excused is the instructor's word: the Assistant is refused it without documentation (EXCUSE NEEDS EVIDENCE).
        self.assertIn('EXCUSE NEEDS EVIDENCE', self.tool_write(session, '/api/absences', {**facts, 'status': 'excused'}).get('error', ''))
        chosen = edit(card, status='excused')
        self.assertEqual(chosen.status_code, 200, chosen.json)
        self.assertEqual(chosen.json['replaced'], card['id'])
        excused = shown(chosen.json['action_id'])
        self.assertEqual([a['id'] for a in cards()], [excused['id']])  # in the old card's place
        self.assertEqual((excused['effect']['title'], excused['body']['status'], excused['edited'], excused['pending']),
                         ('Excuse an absence', 'excused', True, True))
        self.assertEqual((decision(excused)['value'], decision(excused)['choice']['value'], decision(excused)['choice']['suggested']),
                         ('Excused', 'excused', 'pending'))
        self.assertIn('iClicker', excused['effect']['notes'][0])
        self.assertEqual(self.view(card['id'])['events'][-1]['detail'], {'replaced_by_edit': excused['id']})
        self.assertEqual(self.client.get('/api/absences').json['absences'], [])  # nothing is recorded by choosing
        # The card their choice replaced was no refusal: its decision can be chosen again, and another after it.
        back = edit(excused, status='pending')
        self.assertEqual(back.status_code, 200, back.json)
        self.assertEqual(shown(back.json['action_id'])['effect']['title'], 'Record an absence')
        final = edit(shown(back.json['action_id']), status='not_excused')
        self.assertEqual(final.status_code, 200, final.json)
        self.assertEqual(decision(shown(final.json['action_id']))['choice']['suggested'], 'pending')  # the Assistant's, three choices on
        self.assertEqual(self.approve(shown(final.json['action_id'])).status_code, 200)
        [absence] = self.client.get('/api/absences').json['absences']
        self.assertEqual((absence['status'], absence['revision']), ('not_excused', 1))
        # A revision's card: every decision is offered, and a note the Assistant wrote on its own decision goes with that decision.
        path = '/api/absences/' + absence['id']
        reopen = self.tool_write(session, path, {'base_revision': 1, 'status': 'pending', 'decision_note': 'She says a note is coming.'})
        card = shown(reopen['action_id'])
        self.assertEqual((decision(card)['before'], [o['value'] for o in decision(card)['choice']['options']]),
                         ('Not excused', ['pending', 'excused', 'not_excused', 'withdrawn']))
        self.assertIn('Note on your decision', [c['label'] for c in card['effect']['changes']])
        kept = edit(card, status='not_excused')  # the decision the absence already has: the line stays, to choose again
        self.assertEqual(kept.status_code, 200, kept.json)
        card = shown(kept.json['action_id'])
        self.assertNotIn('decision_note', card['body'])
        self.assertEqual((card['effect']['title'], [c['label'] for c in card['effect']['changes']], decision(card).get('before')),
                         ('Change an absence record', ['Decision'], None))
        excuse = edit(card, status='excused')
        self.assertEqual(excuse.status_code, 200, excuse.json)
        self.assertEqual(self.approve(shown(excuse.json['action_id'])).status_code, 200)
        self.assertEqual(self.app.extensions['absences'].get(absence['id'])['status'], 'excused')
        # Only an absence card that proposes a decision has one to choose.
        notice = self.tool_write(session, path, {'base_revision': 2, 'details': 'Told me in class too.'})
        card = shown(notice['action_id'])
        self.assertFalse(any(c.get('choice') for c in card['effect']['changes']))
        refused = edit(card, status='excused')
        self.assertEqual((refused.status_code, refused.json['error']), (400, 'This card proposes no decision to change.'))
        meeting = self.tool_write(session, '/api/student-support', {'kind': 'appointment', 'enrollment_id': self.alice, 'title': 'Meet',
            'date': '2026-10-12', 'time': '14:00', 'end_time': '14:30', 'location': 'BLOC 306'})
        refused = edit(shown(meeting['action_id']), status='excused')
        self.assertEqual((refused.status_code, refused.json['error']), (400, 'Only an absence card has a decision to choose.'))

    def test_a_gradebook_card_shows_the_grade_it_changes(self):
        # GRADES ON THE CARD (requirement, 2026-10-09).
        from assistant.followup_effects import Describer
        route = f'/api/gradebook/terms/{self.term}'
        saved = self.client.post(route + '/setup', json={'base_revision': None, 'request_id': 'setup', 'setup': {'items': [
            {'title': 'Class 5 - Poll', 'points': '10'}, {'title': 'Quiz 1', 'points': '20'}]}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        with connect(self.assistant.database) as db:
            poll, quiz = [item['id'] for item in json.loads(db.execute('SELECT setup_json FROM gradebook_setups').fetchone()[0])['items']]
            rows = {e: f'{s}:{e}' for s, e in db.execute('SELECT section_id,enrollment_id FROM roster_memberships WHERE enrollment_id IN (?,?)',
                                                          (self.alice, self.bob))}
        missing = self.client.post(route + '/scores', json={'request_id': 'missing', 'changes': [
            {'item_id': poll, 'row': rows[self.alice], 'base': None, 'status': 'missing'}]}, headers=HEADERS)
        self.assertEqual(missing.status_code, 200, missing.json)
        base = missing.json['cells'][poll][rows[self.alice]]['id']
        note = 'One-time exception: the instructor agreed in class to accept her screenshot of the poll, so the poll gets full credit.'
        with connect(self.assistant.database) as db:
            describer = Describer(db, self.assistant.transport, {'enrollment_id': self.alice, 'item_key': '', 'source_key': ''}, {'name': 'Alice'},
                                  self.support.people)
            write = lambda *changes: describer.describe({'method': 'POST', 'path': route + '/scores', 'body': {'request_id': 'r', 'changes': list(changes)}})
            effect = write({'item_id': poll, 'row': rows[self.alice], 'base': base, 'status': 'scored', 'points': '10.00', 'note': note})
            self.assertEqual((effect['title'], effect['who'], effect['own'], effect['where'], effect['button']),
                             ('Change the grade of “Class 5 - Poll” to 10 / 10', 'Alice (this student)', True, 'Gradebook', 'Save grade'))
            # The grade is on the card's summary (`show`), the one it replaces struck through; the long note reads in the whole proposal.
            self.assertEqual(effect['changes'], [{'label': 'Class 5 - Poll', 'value': '10 / 10', 'show': True, 'before': 'Missing'},
                                                 {'label': 'Note', 'value': note}])
            # Several students: each cell names its student, an empty cell reads as empty, and the card says how many it reaches.
            bob = self.support.people()[self.bob]['name']
            effect = write({'item_id': quiz, 'row': rows[self.alice], 'base': None, 'status': 'excused'},
                           {'item_id': quiz, 'row': rows[self.bob], 'base': None, 'status': 'scored', 'points': '17.50'})
            self.assertEqual((effect['title'], effect['who'], effect['own'], effect['button']), ('Change 2 grades of “Quiz 1”', '2 students', None, 'Save grades'))
            self.assertEqual(effect['changes'], [
                {'label': 'Quiz 1 · Alice (this student)', 'value': 'Excused', 'show': True, 'before': '(empty)'},
                {'label': f'Quiz 1 · {bob}', 'value': '17.5 / 20', 'show': True, 'before': '(empty)'}])

    def test_an_iclicker_credit_card_says_whether_it_waits_for_the_import(self):
        # ICLICKER CREDIT BEFORE THE IMPORT (requirement, 2026-10-21): the card names the class day and says it waits for it.
        from assistant.followup_effects import Describer
        route = f'/api/gradebook/terms/{self.term}/iclicker/credits'
        with connect(self.assistant.database) as db:
            section = db.execute('SELECT section_id FROM roster_memberships WHERE enrollment_id=?', (self.alice,)).fetchone()[0]
            describer = Describer(db, self.assistant.transport, {'enrollment_id': self.alice, 'item_key': '', 'source_key': ''}, {'name': 'Alice'},
                                  self.support.people)
            effect = describer.describe({'method': 'POST', 'path': route, 'body': {'request_id': 'r', 'section_id': section, 'enrollment_id': self.alice,
                                                                                    'class_date': '2026-10-21', 'reason': 'iClicker would not open'}})
            self.assertEqual((effect['title'], effect['who'], effect['own'], effect['button']),
                             ('Give full iClicker credit for Wed, Oct 21', 'Alice (this student)', True, 'Give credit'))
            self.assertIn('when it is imported after class, full credit is given by itself', effect['audience'])
            self.assertIn({'label': 'Note', 'value': 'iClicker would not open: full credit.'}, effect['changes'])

    def test_a_canvas_push_card_is_titled_by_what_its_preview_changes(self):
        # CANVAS PUSH CARDS (requirement, 2026-10-06).
        from assistant.followup_effects import Describer
        with connect(self.assistant.database) as db:
            sections = [tuple(r) for r in db.execute('SELECT id,number FROM course_sections WHERE term_id=? ORDER BY number', (self.term,))]
            describer = Describer(db, self.assistant.transport, {'enrollment_id': self.alice, 'item_id': '', 'source_key': ''}, {'name': 'Alice'}, None)
            (first, number), *others = sections
            due = {'op': 'update_assignment', 'label': 'Math 140 Sect. 4.2 Basics of Probability', 'fields': {'due_at': '2026-10-15T04:55:00Z'}}
            course = lambda ops, *numbers, **more: {'sections': [{'id': '', 'number': n} for n in numbers], 'ops': ops, **more}
            push = lambda path, preview: describer.describe({'method': 'POST', 'path': path, 'body': {}, 'preview': preview})
            effect = push(f'/api/canvas/sections/{first}/content/push', {'status': 200, 'data': {'courses': [course([due], number)]}})
            self.assertEqual((effect['title'], effect['who'], effect['own'], effect['where'], effect['button'], effect['changes']),
                             (f'Move the due date of “Math 140 Sect. 4.2 Basics of Probability” to Wed, Oct 14 in section {number}',
                              f'Everyone in section {number}', None, 'Canvas', 'Push to Canvas', [{'label': 'Due date', 'value': 'Wed, Oct 14, 11:55 PM'}]))
            # A preview that did not read: the place still names the section; the page's preview says why.
            self.assertEqual(push(f'/api/canvas/sections/{first}/content/push', {'status': 409, 'error': 'Canvas changed'})['title'],
                             f'Change section {number}’s Canvas course')
            # The term's copy: one change in every course reads once; changes only some sections get say which.
            second = others[0][1]  # the fixture's sections 507 and 508
            points = {'op': 'update_assignment', 'label': 'Quiz 3', 'fields': {'points': '20', 'published': True}}
            effect = push(f'/api/canvas/content/{self.term}/push', {'status': 200, 'data': {'courses': [
                course([due, points], number), course([due], second, blocked=[{'label': 'Week 9', 'reasons': ['…']}])]}})
            self.assertEqual(effect['title'], f'Make 2 changes in sections {number} and {second}')
            self.assertEqual([c['value'] for c in effect['changes']], [
                'Move the due date of “Math 140 Sect. 4.2 Basics of Probability” to Wed, Oct 14',
                f'Change the points and status of “Quiz 3” (points: 20; status: Published), section {number} only'])
            self.assertEqual(effect['notes'], ['Not pushed: Week 9. The page’s preview says why.'])
            self.assertEqual(push(f'/api/canvas/content/{self.term}/push', {'status': 200, 'data': {'courses': [course([], number)]}})['title'],
                             'Nothing changes on Canvas')

    def test_a_dr_quiz_taker_card_waits_for_the_instructor(self):
        # ACCOMMODATIONS "DR QUIZ TAKERS" (requirement, 2026-10-15): an Approve card, in no automatic category.
        from assistant.followup_effects import Describer, PRIVATE
        from assistant.followups import PROMPT
        self.assertIn('/api/accommodations/dr-quiz-takers/', PROMPT)
        path = '/api/accommodations/dr-quiz-takers/' + self.alice
        session = self.session(body='Could I take my quizzes at the DR testing center?')
        result = self.tool_write(session, path, {'on': True})
        self.assertEqual(result['outcome'], 'waiting_for_approval', result)
        unmarked = {'on': False, 'revision': 0, 'set_at': None, 'set_by': None}
        self.assertEqual(self.client.get(path).json['dr_quiz_taker'], unmarked)  # nothing recorded before approval
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dr_quiz_taker_changes').fetchone()[0], 0)
            describer = Describer(db, self.assistant.transport, {'enrollment_id': self.alice, 'item_id': '', 'source_key': ''}, {'name': 'Alice'}, None)
            effect = describer.describe({'method': 'POST', 'path': path, 'body': {'on': True, 'request_id': 'r'}})
            self.assertEqual((effect['title'], effect['where'], effect['href'], effect['button'], effect['own'], effect['audience']),
                             ('Mark as a DR quiz taker', 'Accommodations', '#accommodations', 'Save', True, PRIVATE))
            self.assertEqual(effect['changes'], [{'label': 'DR quiz taker', 'value': 'Yes'}])
            off = describer.describe({'method': 'POST', 'path': '/api/accommodations/dr-quiz-takers/' + self.bob, 'body': {'on': False, 'request_id': 'r'}})
            self.assertEqual((off['title'], off['own'], off['changes']), ('No longer a DR quiz taker', False, [{'label': 'DR quiz taker', 'value': 'No'}]))
        approved = self.approve(self.view(result['action_id']))
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(approved.json['action']['status'], 'succeeded')
        state = self.client.get(path).json['dr_quiz_taker']
        self.assertEqual((state['on'], state['revision'], state['set_by']), (True, 1, 'assistant'))
        self.assertEqual(self.client.get('/api/accommodations/dr-quiz-takers/' + self.bob).json['dr_quiz_taker'], unmarked)

    def test_a_guess_is_shown_on_its_card_with_its_alternatives_and_questions_are_the_exception(self):
        # ASSUME, DON'T ASK (requirement, 2026-10-04: when unsure, decide from the term policies and flag the guess).
        session = self.session()
        result, _ = self.assistant.run_tool(session, 'faculty_write', {'method': 'POST', 'path': '/api/student-support',
            'body': {'kind': 'appointment', 'enrollment_id': self.alice, 'title': 'Meet', 'date': '2026-10-12', 'time': '14:00',
                     'end_time': '14:30', 'request_id': '$new_request_id'}, 'summary': 'Meet on Monday',
            'assumption': {'text': '  Monday   afternoon works for her ', 'alternatives': ['Tuesday', 'Wednesday', 'Thursday', 'Friday']}})
        self.assertEqual(json.loads(result)['outcome'], 'waiting_for_approval', result)
        card = self.listing()['items'][0]['suggestions']['actions'][0]
        self.assertEqual(card['assumption'], {'text': 'Monday afternoon works for her', 'alternatives': ['Tuesday', 'Wednesday', 'Thursday']})
        plain = self.tool_write(session, '/api/student-support', {'kind': 'appointment', 'enrollment_id': self.alice, 'title': 'Other',
            'date': '2026-10-13', 'time': '14:00', 'end_time': '14:30'})
        self.assertNotIn('assumption', self.view(plain['action_id']))
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(self.suggestions.get(db, session['_followup_run']))
        self.assertIn('When unsure, decide', prompt)
        self.assertIn('only when a policy area is set to Ask me', prompt)

    def test_record_keeping_is_done_for_you_within_its_bounds_and_you_can_undo_it(self):
        # DONE FOR YOU (requirement, 2026-10-04): the Assistant records the absence, attaches the student's files and puts agreed
        # office hours on the calendar by itself; excuse decisions, other students and anything outside the bounds stay cards.
        from datetime import date, timedelta
        # Read on the day the message was sent (Sat, Oct 3): a month and day with no year is read against the day it was sent (JEV
        # DATES), so on the real clock the meeting day stops being the one the student names. Oct 7 is no absence's day below.
        self.today_is(date(2026, 10, 3))
        on = date(2026, 10, 7)
        day = on.isoformat()
        # Sent Oct 3 (campus): "yesterday" is Oct 2. JEV DATES: the days of a record made by itself are written in the conversation.
        session = self.session(body=f'I was sick yesterday and missed class. Could I meet you {on:%B} {on.day} at 2pm?')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(**facts):
            return self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': 'illness',
                                                              'notice_message_id': self.notice(), **facts})
        refused = {
            # Excused: EXCUSE NEEDS EVIDENCE; Not excused needs a reason nothing could excuse (NOT EXCUSED WHEN NOTHING COULD EXCUSE IT).
            'a decision is the instructor’s': absence(first_date='2026-10-05', status='not_excused', reason='other'),
            'its dates are not within': absence(first_date='2026-08-03'),
            'not for this student': self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.bob,
                                                                                'first_date': '2026-10-02', 'reason': 'illness',
                                                                                'notice_message_id': self.notice()}),
        }
        for why, result in refused.items():
            self.assertEqual(result['outcome'], 'waiting_for_approval', result)
            self.assertIn(why, result['why_approval'])
        # JEV DOUBLE-CHECK: a day the student's messages don't name, no Jev, or Jev's doubt about the reason makes it a card.
        self.assertIn('the student’s messages don’t name Fri, Oct 9', absence(first_date='2026-10-09')['why_approval'])
        self.assertIn('Jev is not available', absence(first_date='2026-10-02')['why_approval'])
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        # Jev's receipt covers the messages, the reason and the student's own words for the day, never the record's days: another
        # reason is another question.
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, nouls={'reason': .2})) as evaluate:
            self.assertIn('Jev could not confirm the reason', absence(first_date='2026-10-02', reason='activity')['why_approval'])
        state, asked = evaluate.call_args.args
        self.assertEqual((set(asked), state['proposed_absence']), ({'reason', 'missed'}, {'reason': 'University-authorized activity'}))
        self.assertIn('the student’s messages don’t name Thu, Oct 1', absence(first_date='2026-10-01')['why_approval'])
        patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)).start()
        made = absence(first_date='2026-10-02', status='pending')  # the message was sent Oct 3 (campus)
        self.assertEqual(made['outcome'], 'done', made)
        absence_id = made['result']['id']
        self.assertEqual(self.client.get('/api/absences/' + absence_id).json['status'], 'pending')
        self.assertIn('overlaps an absence already recorded', absence(first_date='2026-10-01', last_date='2026-10-02')['why_approval'])
        self.assertIn('per preparation', absence(first_date='2026-10-06')['why_approval'])
        guessed = json.loads(self.assistant.run_tool(session, 'faculty_write', {'method': 'POST', 'path': f'/api/absences/{absence_id}/documents/message',
            'body': {'message_id': 'not-in-this-conversation', 'index': 0, 'request_id': '$new_request_id'}, 'summary': 'Attach'})[0])
        self.assertIn('not from this conversation', guessed['why_approval'])
        # An agreed meeting within three weeks, an hour or less, with nothing else on the calendar then (`day`, named in the message).
        def meeting(when=day, start='14:00', end='14:30'):
            return self.tool_write(session, '/api/student-support', {'kind': 'appointment', 'enrollment_id': self.alice, 'title': 'Talk',
                                                                     'date': when, 'time': start, 'end_time': end})
        self.assertIn('not a meeting of an hour or less', meeting(start='13:00', end='14:30')['why_approval'])
        self.assertIn('not within the next three weeks', meeting(when=(date.fromisoformat(day) + timedelta(days=30)).isoformat())['why_approval'])
        self.assertIn(f'the conversation never names {on:%a}, {on:%b} {on.day} at 4:00 PM', meeting(start='16:00', end='16:30')['why_approval'])
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)) as evaluate:
            scheduled = meeting()
        state, asked = evaluate.call_args.args
        self.assertEqual(state['proposed_meeting'], {'day_written_as': f'{on:%B} {on.day}', 'day_in_message': 1,
                                                     'time_written_as': '2pm', 'time_in_message': 1})
        self.assertNotIn(day, json.dumps(asked))
        self.assertEqual(scheduled['outcome'], 'done', scheduled)
        self.assertIn('already has an appointment that day', meeting(start='15:00', end='15:30')['why_approval'])
        # Done for you lists both, with Undo; the waiting cards no longer include them.
        shown = self.listing()['items'][0]['suggestions']
        done = {d['id']: d for d in shown['done']}
        self.assertEqual({d['title'] for d in done.values()}, {'Recorded an absence', 'Put office hours on your calendar'})
        self.assertTrue(all(d['undo'] and d['state'] == 'done' for d in done.values()))
        self.assertFalse({made['action_id'], scheduled['action_id']} & {a['id'] for a in shown['actions']})
        def undo(action_id, request_id, run=None):
            line = done[action_id]
            return self.client.post(f"/api/student-support/followups/suggestions/{run or line['run']}/undo",
                                    json={'action_id': action_id, 'seen': line['seen'], 'request_id': request_id}, headers=HEADERS)
        undone = undo(made['action_id'], 'undo-absence')
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual(undo(made['action_id'], 'undo-absence').json, undone.json)  # the same request again
        self.assertEqual(undo(made['action_id'], 'undo-again').status_code, 409)
        self.assertEqual(self.client.get('/api/absences/' + absence_id).json['status'], 'withdrawn')
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT actor FROM absence_revisions WHERE absence_id=? ORDER BY revision DESC LIMIT 1',
                                        (absence_id,)).fetchone()[0], 'workspace')  # the instructor's undo, not the assistant's
        # A changed appointment is no longer undone from here; the row points to its page instead.
        with connect(self.assistant.database) as db:
            item_id = db.execute("SELECT id FROM student_support_items WHERE kind='appointment'").fetchone()[0]
        changed = self.client.post(f'/api/student-support/{item_id}', json={'title': 'Talk about Quiz 4', 'state': 'scheduled', 'date': day,
            'time': '14:00', 'end_time': '14:30', 'base_revision': 1, 'request_id': 'moved'}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        rows = {d['id']: d for d in self.listing()['items'][0]['suggestions']['done']}
        self.assertEqual(rows[made['action_id']]['state'], 'undone')
        self.assertEqual((rows[scheduled['action_id']]['undo'], rows[scheduled['action_id']]['href']), (False, '#office-hours'))
        self.assertEqual(undo(scheduled['action_id'], 'undo-meeting').status_code, 409)
        # Undone once, that kind of record is a card on this follow-up from then on; the controls are never assistant routes.
        self.assertIn('undid this kind of record', absence(first_date='2026-10-08')['why_approval'])
        self.assertEqual(policy.base_rule('student_support.suggestion_undo').tier, 'never')

    def test_a_bare_weekday_is_matched_in_code_and_jev_only_says_whether_it_was_already_past(self):
        # JEV DATES (requirement, 2026-10-08): sent
        # Saturday Oct 3, "Monday" is Sep 28 or Oct 5. Jev says only already past or later; code picks the day. "next Wednesday"
        # (Oct 7 or Oct 14) is never chosen for anyone.
        session = self.session(body='I missed class Monday because I had the flu, and I may miss next Wednesday too.')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(first_date, reason='illness'):  # Jev's receipt covers the messages and the reason: each case its own reason
            return self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': reason,
                                                              'notice_message_id': self.notice(), 'first_date': first_date})
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        self.assertIn('“next Wednesday” in the student’s message could be Wed, Oct 7 or Wed, Oct 14',
                      absence('2026-10-07')['why_approval'])
        def timing(first, second=None):
            return lambda state, qs: response(qs, choices={'timing': first, 'timing__reversed': second or first})
        with patch('jev.client.evaluate', side_effect=timing('later')):
            self.assertIn('“Monday” in the student’s message is Mon, Oct 5, not Mon, Sep 28', absence('2026-09-28', 'activity')['why_approval'])
        with patch('jev.client.evaluate', side_effect=timing('past', 'unclear')):
            self.assertIn('Jev could not tell whether “Monday” is a day already past or a later one',
                          absence('2026-09-28', 'dependent')['why_approval'])
        with patch('jev.client.evaluate', side_effect=timing('past')) as evaluate:
            made = absence('2026-09-28')
        self.assertEqual(made['outcome'], 'done', made)
        state, asked = evaluate.call_args.args
        self.assertEqual(set(asked), {'reason', 'missed', 'timing', 'timing__reversed'})
        self.assertIn('In message 1, the student writes "Monday"', asked['timing']['instructions'])
        self.assertIn('The student writes "Monday" (message 1). Does the student say', asked['missed']['instructions'])
        self.assertNotIn('2026-09-28', json.dumps([state['proposed_absence'], asked]))

    def test_a_day_the_student_wrote_for_something_else_is_not_a_day_of_the_absence(self):
        # A DAY THEY MISS (requirement, 2026-10-10). Sent Saturday Oct 3: "yesterday" and "October 2" are the day missed; Oct 7 is
        # a meeting asked for, Oct 6 a due date, "tomorrow" the day they are back. Every one is named, so code alone let each pass.
        session = self.session(body='I was sick yesterday and missed class; my doctor’s note is dated October 2. Could I meet you '
                                    'October 7 at 2pm? Is the homework still due October 6? I will be back tomorrow.')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(first_date, **facts):
            return self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': 'illness',
                                                              'notice_message_id': self.notice(), 'first_date': first_date, **facts})
        def judge(state, qs):  # the scripted Jev: only the places that say "yesterday" tell of a class missed
            return response(qs, nouls={key: .99 if '"yesterday"' in q['instructions'] else .1
                                       for key, q in qs.items() if key.startswith('missed')})
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        evaluate = patch('jev.client.evaluate', side_effect=judge).start()
        def held(day, first_date, **facts):
            made = absence(first_date, **facts)
            self.assertEqual(made['outcome'], 'waiting_for_approval', made)
            self.assertIn(f'Jev could not confirm that the student’s messages give {day} as a day they miss class', made['why_approval'])
            return evaluate.call_args.args[1]
        asked = held('Wed, Oct 7', '2026-10-07')
        self.assertEqual(set(asked), {'reason', 'missed'})
        self.assertIn('The student writes "October 7" (message 1). Does the student say they missed, or will miss, class then? '
                      'Missing a quiz, an exam or any other class meeting that day counts, and so does asking to be excused for it.',
                      asked['missed']['instructions'])
        held('Tue, Oct 6', '2026-10-06')
        self.assertIn('nothing to record', absence('2026-10-04')['error'])  # a Sunday: no class day, before Jev is asked
        # Two days with no class day between them: each end has its own question.
        asked = held('Tue, Oct 6', '2026-10-06', last_date='2026-10-07')
        self.assertEqual(set(asked), {'reason', 'missed', 'missed_last'})
        self.assertIn('"October 7" (message 1)', asked['missed_last']['instructions'])
        # EVERY CLASS DAY INSIDE: stretched from the day missed to the day of the meeting, it is a card for the first class day
        # inside it the student never wrote, before Jev is asked (no Term schedule here: every Monday to Friday is a class day).
        calls = evaluate.call_count
        stretched = absence('2026-10-02', last_date='2026-10-07')
        self.assertEqual(stretched['outcome'], 'waiting_for_approval', stretched)
        self.assertIn('the student’s messages don’t name Mon, Oct 5, a class day inside this absence', stretched['why_approval'])
        self.assertEqual(evaluate.call_count, calls)
        # The day missed, quoted in each place the student wrote it; never the record's own date.
        made = absence('2026-10-02')
        self.assertEqual(made['outcome'], 'done', made)
        state, asked = evaluate.call_args.args
        self.assertIn('The student writes "yesterday" (message 1) and "October 2" (message 1). In any of those places, does the student '
                      'say they missed, or will miss, class then?', asked['missed']['instructions'])
        self.assertEqual(state['proposed_absence'], {'reason': 'Illness'})
        self.assertNotIn('2026-10-0', json.dumps(asked))
        self.assertEqual(self.client.get('/api/absences/' + made['result']['id']).json['first_date'], '2026-10-02')

    def test_a_week_the_student_wrote_counts_only_for_an_absence_that_covers_it(self):
        # WHOLE WEEK ONLY (requirement, 2026-10-10). Sent Saturday Oct 3: "next week" is Oct 5-11.
        session = self.session(body='I was sick yesterday and missed class. Is there a quiz or anything next week?')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(first_date, **facts):
            return self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': 'illness',
                                                              'notice_message_id': self.notice(), 'first_date': first_date, **facts})
        with patch('jev.client.available', return_value=True), \
                patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)) as evaluate:
            # One day of that week, or a part of it: a card before Jev is asked anything, though the scripted Jev would say yes.
            for first, last, day in (('2026-10-09', '2026-10-09', 'Fri, Oct 9'), ('2026-10-05', '2026-10-08', 'Mon, Oct 5')):
                made = absence(first, last_date=last)
                self.assertEqual(made['outcome'], 'waiting_for_approval', made)
                self.assertIn(f'the student’s messages name {day} only through “next week”, and this absence does not cover that week',
                              made['why_approval'])
            evaluate.assert_not_called()
            # The day they wrote by itself is recorded, and the week is no place Jev is asked about.
            made = absence('2026-10-02')
            self.assertEqual(made['outcome'], 'done', made)
            self.assertIn('The student writes "yesterday" (message 1). Does the student say', evaluate.call_args.args[1]['missed']['instructions'])

    def test_a_whole_week_out_is_recorded_through_the_week(self):
        # WHOLE WEEK ONLY: an absence covering the week's class days (Monday to Friday) is named by the week, in one question.
        session = self.session(body='I will be out all next week for a tournament with the team.')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(first_date, last_date):
            return self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': 'activity',
                'notice_message_id': self.notice(), 'first_date': first_date, 'last_date': last_date})
        with patch('jev.client.available', return_value=True), \
                patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)) as evaluate:
            self.assertIn('only through “next week”', absence('2026-10-05', '2026-10-07')['why_approval'])
            made = absence('2026-10-05', '2026-10-09')
        self.assertEqual(made['outcome'], 'done', made)
        asked = evaluate.call_args.args[1]
        self.assertEqual(set(asked), {'reason', 'missed'})
        self.assertIn('The student writes "next week" (message 1). Does the student say', asked['missed']['instructions'])

    def test_a_weekday_written_beside_a_week_is_read_as_the_weekday(self):
        # WHOLE WEEK ONLY: with the week set aside, "Friday" is the bare weekday it always was (Oct 2 or Oct 9, sent Saturday
        # Oct 3), so Jev says whether it was already past and is asked about "Friday", never about the week.
        session = self.session(body='The quiz on next week’s material is Friday. I have an appointment that day and will miss class.')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        later = lambda state, qs: response(qs, choices={'timing': 'later', 'timing__reversed': 'later'})
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=later) as evaluate:
            made = self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': 'other',
                'notice_message_id': self.notice(), 'first_date': '2026-10-09'})
        self.assertEqual(made['outcome'], 'done', made)
        asked = evaluate.call_args.args[1]
        self.assertEqual(set(asked), {'reason', 'missed', 'timing', 'timing__reversed'})
        self.assertIn('The student writes "Friday" (message 1). Does the student say', asked['missed']['instructions'])

    def test_every_class_day_inside_an_absence_is_checked_as_its_first_and_last_are(self):
        # EVERY CLASS DAY INSIDE (requirement, 2026-10-10). Sent Saturday Oct 3:
        # Sep 28 and Oct 2 are days missed, Sep 30 is written for a meeting, Oct 1 is never written.
        session = self.session(body='I missed class on September 28 and on October 2. On September 30 I met my advisor.')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(reason):  # each proposal its own reason: the same one again is the card already waiting
            return self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': reason,
                'notice_message_id': self.notice(), 'first_date': '2026-09-28', 'last_date': '2026-10-02'})
        def judge(state, qs):  # the scripted Jev: the advisor's day is no day missed
            return response(qs, nouls={key: .1 for key, q in qs.items() if '"September 30"' in q['instructions']})
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        evaluate = patch('jev.client.evaluate', side_effect=judge).start()
        # No Term schedule in use: Tuesday Sep 29 is a class day inside it that the student never wrote.
        self.assertIn('the student’s messages don’t name Tue, Sep 29, a class day inside this absence', absence('illness')['why_approval'])
        evaluate.assert_not_called()
        # The term uses its schedule and the section meets Monday, Wednesday and Friday: Sep 30 is the one class day inside, written
        # in a place of its own, so Jev is asked about it too, and its doubt makes the card.
        saved = self.client.post(f'/api/scheduling/terms/{self.term}', headers=HEADERS, json={
            'request_id': 'schedule', 'base_revision': 0, 'first_day': '2026-08-24', 'last_day': '2026-12-09', 'entries': [
                {'kind': 'class', 'section_id': section, 'weekdays': [0, 2, 4], 'start': '08:00', 'end': '08:50', 'room': 'HELD 107'}]})
        self.assertEqual(saved.status_code, 200, saved.json)
        plan = self.client.get(f'/api/scheduling/terms/{self.term}/use').json
        used = self.client.post(f'/api/scheduling/terms/{self.term}/use', headers=HEADERS,
                                json={'request_id': 'use', 'fingerprint': plan['fingerprint'], 'confirmation': 'use'})
        self.assertEqual(used.status_code, 200, used.json)
        made = absence('dependent')
        self.assertEqual(made['outcome'], 'waiting_for_approval', made)
        self.assertIn('Jev could not confirm that the student’s messages give Wed, Sep 30 as a day they miss class', made['why_approval'])
        asked = evaluate.call_args.args[1]
        self.assertEqual(set(asked), {'reason', 'missed', 'missed_last', 'missed_inside_1'})
        self.assertIn('The student writes "September 30" (message 1). Does the student say', asked['missed_inside_1']['instructions'])
        # When Jev reads all three as days missed, the absence is recorded by itself.
        evaluate.side_effect = lambda state, qs: response(qs)
        self.assertEqual(absence('other')['outcome'], 'done')

    def test_an_absence_a_follow_up_proposes_names_class_days(self):
        # A FOLLOW-UP NAMES CLASS DAYS (requirement, 2026-11-03).
        session = self.session(body='Absent 10/26, 10/30, 11/2. I have been sick since 10/26 and return 11/3.')
        section = self.listing()['items'][0]['student']['section_id']
        def absence(first, last=None, session=session, **facts):
            return self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'first_date': first,
                'last_date': last or first, 'status': 'pending', 'notice_message_id': self.notice(), **facts})
        self.assertIn('nothing to record', absence('2026-10-31', reason='illness')['error'])  # no Term schedule yet: Mon to Fri
        self.assertEqual(absence('2026-10-28', reason='illness')['outcome'], 'waiting_for_approval')
        # The term uses its schedule and the section meets Monday, Wednesday and Friday, through Wed, Dec 9.
        saved = self.client.post(f'/api/scheduling/terms/{self.term}', headers=HEADERS, json={
            'request_id': 'schedule', 'base_revision': 0, 'first_day': '2026-08-24', 'last_day': '2026-12-09', 'entries': [
                {'kind': 'class', 'section_id': section, 'weekdays': [0, 2, 4], 'start': '08:00', 'end': '08:50', 'room': 'HELD 107'}]})
        self.assertEqual(saved.status_code, 200, saved.json)
        plan = self.client.get(f'/api/scheduling/terms/{self.term}/use').json
        used = self.client.post(f'/api/scheduling/terms/{self.term}/use', headers=HEADERS,
                                json={'request_id': 'use', 'fingerprint': plan['fingerprint'], 'confirmation': 'use'})
        self.assertEqual(used.status_code, 200, used.json)
        # The student's days are class days and stand, each its own card as its decision needs.
        for first, reason in (('2026-10-26', 'other'), ('2026-10-30', 'family'), ('2026-11-02', 'dependent')):
            self.assertEqual(absence(first, reason=reason)['outcome'], 'waiting_for_approval', first)
        # A Saturday, a Sunday, and a weekday the section does not meet: nothing to record, and no card appears.
        with connect(self.assistant.database) as db:
            cards = db.execute('SELECT COUNT(*) FROM assistant_actions').fetchone()[0]
        for first in ('2026-10-31', '2026-11-01', '2026-10-29'):
            self.assertIn('nothing to record', absence(first, reason='legal')['error'], first)
        self.assertIn('Sat, Oct 31', absence('2026-10-31', reason='legal')['error'])
        self.assertIn('nothing to record', absence('2026-10-31', '2026-11-01', reason='legal')['error'])  # the whole weekend
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM assistant_actions').fetchone()[0], cards)
        # A stretch is brought to the class days it holds, the ends alone: the days between may be any days.
        self.assertIn('"first_date": "2026-11-02", "last_date": "2026-11-02"', absence('2026-11-01', '2026-11-02', reason='legal')['error'])
        self.assertIn('"first_date": "2026-11-02", "last_date": "2026-11-02"', absence('2026-11-01', '2026-11-03', reason='legal')['error'])
        self.assertIn('"first_date": "2026-11-04", "last_date": "2026-11-06"', absence('2026-11-03', '2026-11-07', reason='legal')['error'])
        self.assertIn('"returns November 3"', absence('2026-11-01', '2026-11-02', reason='legal')['error'])
        self.assertEqual(absence('2026-11-04', '2026-11-06', reason='legal')['outcome'], 'waiting_for_approval')
        # An evening exam on a Thursday: on no class day, but a department makeup needs its absence to cover it.
        with connect(self.assistant.database) as db:
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (self.term,)).fetchone()[0]
        exams = self.app.extensions['frq_grading'].exams
        event = exams.create({'term_id': self.term, 'assignment_id': self.app.extensions['documents'].create_assignment(
            definition, 'exam-1', 'Exam 1')['id'], 'section_ids': [section]})
        self.assertIn('nothing to record', absence('2026-11-12', reason='activity')['error'])
        exams.save_sitting(event['id'], {'kind': 'main', 'label': 'Main', 'date': '2026-11-12', 'time': '19:00', 'version': event['version']})
        self.assertEqual(absence('2026-11-12', reason='activity')['outcome'], 'waiting_for_approval')
        # So does one only planned, not created yet (the class schedule's own row): a live card covered a planned Exam III this way.
        self.assertIn('nothing to record', absence('2026-11-19', reason='activity', details='Conference.')['error'])
        change = {'base_revision': 0, 'set': [{'kind': 'exam', 'title': 'Exam 3', 'sections': [section], 'date': '2026-11-19', 'time': '19:00', 'place': ''}]}
        seen = self.client.post(f'/api/scheduling/terms/{self.term}/class-schedule/preview', headers=HEADERS, json=change)
        self.assertEqual(seen.status_code, 200, seen.json)
        made = self.client.post(f'/api/scheduling/terms/{self.term}/class-schedule', headers=HEADERS,
                                json={**change, 'request_id': 'plan', 'fingerprint': seen.json['fingerprint']})
        self.assertEqual(made.status_code, 200, made.json)
        self.assertEqual(absence('2026-11-19', reason='activity', details='Conference.')['outcome'], 'waiting_for_approval')
        # Past the section's last class: the exam period stands on weekdays, so a reported recovery keeps its end; a weekend alone still has nothing.
        self.assertEqual(absence('2026-12-09', '2026-12-16', reason='military')['outcome'], 'waiting_for_approval')
        self.assertIn('nothing to record', absence('2026-12-12', reason='military')['error'])
        # Dates the instructor gives on the card are their word, and a card already waiting is approved as it stands.
        told = self.assistant.open_session('test')
        told.update(_proposal_only=True, _followup_run=session['_followup_run'], _actor='instructor')
        saturday = absence('2026-10-17', session=told, reason='pregnancy')
        self.assertEqual(saturday['outcome'], 'waiting_for_approval', saturday)
        self.assertEqual(self.approve(self.assistant._view(*self.assistant.action(saturday['action_id']))).status_code, 200)
        # A revision that leaves its dates alone passes (one saved before this rule); one that moves them is checked.
        [recorded] = self.client.get('/api/absences').json['absences']
        path = '/api/absences/' + recorded['id']
        self.assertEqual(self.tool_write(session, path, {'base_revision': recorded['revision'], 'details': 'Same day.'})['outcome'], 'waiting_for_approval')
        moved = self.tool_write(session, path, {'base_revision': recorded['revision'], 'first_date': '2026-10-24', 'last_date': '2026-10-24'})
        self.assertIn('nothing to record', moved['error'])
        # The preparation is told, before it proposes.
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(self.suggestions.get(db, session['_followup_run']))
        self.assertIn('An absence starts and ends on a class day of the student\'s section', prompt)

    def test_a_span_of_days_the_student_wrote_is_asked_about_once(self):
        # A DAY THEY MISS: "October 5-7" names the absence's first and last day in one place, so Jev answers one question.
        session = self.session(body='I will be out October 5-7 for a tournament with the team.')
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        with patch('jev.client.available', return_value=True), \
                patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)) as evaluate:
            made = self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'reason': 'activity',
                'notice_message_id': self.notice(), 'first_date': '2026-10-05', 'last_date': '2026-10-07'})
        self.assertEqual(made['outcome'], 'done', made)
        asked = evaluate.call_args.args[1]
        self.assertEqual(set(asked), {'reason', 'missed'})
        self.assertIn('The student writes "October 5-7" (message 1). Does the student say', asked['missed']['instructions'])

    def test_a_makeup_hold_is_done_for_you_with_undo_and_only_you_release_it(self):
        # MAKEUP HOLDS (requirement, 2026-10-06): holding this student's own absence's quiz makeups
        # is done at once and listed with Undo; a release, another student's absence or a far check-back day stays a card.
        from datetime import date, timedelta
        from exam_workflow.overview import campus_today
        session = self.session()
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(enrollment, request_id):
            made = self.client.post('/api/absences', json={'section_id': section, 'enrollment_id': enrollment, 'first_date': '2026-10-02',
                                                            'reason': 'illness', 'request_id': request_id}, headers=HEADERS)
            self.assertEqual(made.status_code, 201, made.json)
            return made.json['id']
        mine, theirs = absence(self.alice, 'a-alice'), absence(self.bob, 'a-bob')
        today = date.fromisoformat(campus_today())
        def hold(absence_id, **body):
            return self.tool_write(session, f'/api/absences/{absence_id}/makeup-hold', body)
        refused = {
            'releasing a makeup hold is the instructor’s': hold(mine, held=False, revision=0),
            'not one of this student’s absences': hold(theirs, held=True),
            'not within the next 60 days': hold(mine, held=True, check_back=(today + timedelta(days=90)).isoformat()),
        }
        for why, result in refused.items():
            self.assertEqual(result['outcome'], 'waiting_for_approval', result)
            self.assertIn(why, result['why_approval'])
        made = hold(mine, held=True, reason='her concussion reassessment', check_back=(today + timedelta(days=1)).isoformat())
        self.assertEqual(made['outcome'], 'done', made)
        self.assertIn('per preparation', hold(mine, held=True, reason='something else')['why_approval'])
        with connect(self.assistant.database) as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT revision, held, reason FROM absence_makeup_holds WHERE absence_id=?', (mine,))],
                             [(1, 1, 'her concussion reassessment')])
        [line] = [d for d in self.listing()['items'][0]['suggestions']['done'] if d['id'] == made['action_id']]
        self.assertEqual((line['title'], line['state'], line['undo'], line['href']),
                         ('Held makeup booking for the absence', 'done', True, '#absences?view=makeups'))
        self.assertIn('her concussion reassessment', line['detail'])
        undone = self.client.post(f"/api/student-support/followups/suggestions/{line['run']}/undo",
                                  json={'action_id': line['id'], 'seen': line['seen'], 'request_id': 'undo-hold'}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        with connect(self.assistant.database) as db:
            self.assertEqual(tuple(db.execute('SELECT revision, held, actor FROM absence_makeup_holds WHERE absence_id=? ORDER BY revision DESC',
                                              (mine,)).fetchone()), (2, 0, 'workspace'))  # the instructor's undo
        self.assertIn('undid this kind of record', hold(mine, held=True, reason='again')['why_approval'])

    def test_a_follow_up_never_holds_for_a_known_return(self):
        # ABSENCES MAKEUP HOLDS › A KNOWN RETURN (requirement, 2026-11-09): a return day the
        # hold's reason or the absence's details name is refused while the model can drop the hold; an open-ended wait is held.
        from datetime import date, timedelta
        from exam_workflow.overview import campus_today
        session = self.session()
        session['_followup_auto'] = True
        section = self.listing()['items'][0]['student']['section_id']
        def absence(request_id, details=''):
            made = self.client.post('/api/absences', json={'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02',
                                                            'reason': 'illness', 'request_id': request_id, 'details': details}, headers=HEADERS)
            self.assertEqual(made.status_code, 201, made.json)
            return made.json['id']
        back = (date.fromisoformat(campus_today()) + timedelta(days=1)).isoformat()
        def hold(absence_id, reason):
            return self.tool_write(session, f'/api/absences/{absence_id}/makeup-hold', {'held': True, 'reason': reason, 'check_back': back})
        first = absence('a-1')
        refused = hold(first, 'Medical note permits return October 9')
        self.assertIn('A KNOWN RETURN', refused.get('error', ''), refused)
        self.assertIn('Put the return date in the absence', refused['error'])
        second = absence('a-2', details='Doctor’s note: she can return to class on October 9.')
        self.assertIn('A KNOWN RETURN', hold(second, 'her recovery').get('error', ''))
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM absence_makeup_holds').fetchone()[0], 0)
        self.assertEqual(hold(first, 'her uncertain return to class')['outcome'], 'done')

    def test_a_settled_resolved_follow_up_waits_until_it_is_opened(self):
        # RESOLVED WHEN OPENED (TEACHING-WORKFLOWS; requirement, 2026-11-05): the
        # page's list read leaves a resolved follow-up's drafts and evidence out; the default read and the student's own read keep them.
        self.suggestions.automatic = True
        self.fake_mail()
        self.incoming()
        def first(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'resolved'}, 'Close the conversation'))(c, prompt)
        run, _ = self.prepare(first)
        mail = next(a for a in run['actions'] if a['endpoint'] == 'gmail_mail.send')
        read = lambda suffix: self.client.get('/api/student-support?kind=followup&suggestions=1' + suffix).json
        # Cards still waiting: nothing is left out, whatever the follow-up's status.
        self.assertEqual(read('&brief=1'), read(''))
        self.assertEqual(self.approve(mail).status_code, 200)
        [full], [brief] = read('')['items'], read('&brief=1')['items']
        self.assertEqual(full['state'], 'resolved')
        self.assertNotIn('brief', full['suggestions'])
        self.assertIn('explanation', full['suggestions'])
        view = brief['suggestions']
        self.assertTrue(view['brief'])
        self.assertEqual({key: value for key, value in brief.items() if key != 'suggestions'},
                         {key: value for key, value in full.items() if key != 'suggestions'})
        for key in ('explanation', 'evidence', 'verification'):
            self.assertNotIn(key, view)
        self.assertEqual({key: view[key] for key in ('id', 'seen', 'fingerprint', 'state', 'approved', 'needs_attention')},
                         {key: full['suggestions'][key] for key in ('id', 'seen', 'fingerprint', 'state', 'approved', 'needs_attention')})
        self.assertEqual([a['id'] for a in view['actions']], [a['id'] for a in full['suggestions']['actions']])
        [card] = view['actions']
        self.assertEqual((card['endpoint'], card['status'], card['pending']), ('gmail_mail.send', 'succeeded', False))
        self.assertEqual(card['events'][-1]['detail']['result']['status'], full['suggestions']['actions'][0]['events'][-1]['detail']['result']['status'])
        self.assertNotIn('to', card['body'])
        self.assertNotIn('preview', card)
        # The queue's count and its Done for you filter read each line's state.
        self.assertEqual([(line['id'], line['state']) for line in view['done']], [(line['id'], line['state']) for line in full['suggestions']['done']])
        self.assertEqual(sorted(view['done'][0]), ['id', 'state'])
        # Opening it reads the student: the same view as the default read.
        self.assertEqual(read(f'&brief=1&enrollment_id={self.alice}')['items'], read('')['items'])
        self.assertEqual(read(f'&enrollment_id={self.alice}')['items'], read('')['items'])
        # A record that needs a look stays in full, so it stays in Active and Needs me.
        with connect(self.assistant.database) as db:
            db.execute("INSERT INTO assistant_action_events(action_id,kind,actor,detail_json,created_at) VALUES(?,'failed','faculty','{}',?)",
                       (mail['id'], '2026-10-04T00:00:00+00:00'))
            db.commit()
        again = read('&brief=1')['items'][0]['suggestions']
        self.assertNotIn('brief', again)
        self.assertEqual(again, read('')['items'][0]['suggestions'])

    def test_the_follow_ups_status_is_recorded_once_the_reply_is_sent_and_can_be_put_back(self):
        # DONE FOR YOU, scheduled status (requirement, 2026-10-04): proposed beside the reply, run after its confirmed send.
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming()
        outcomes = []
        def first(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            respond = proposing(MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'resolved'}, 'Close the conversation'))
            respond(c, prompt)
            outcomes.extend(respond.outcomes)
        run, _ = self.prepare(first)
        self.assertEqual([o['outcome'] for o in outcomes], ['waiting_for_approval', 'scheduled'], outcomes)
        mail = next(a for a in run['actions'] if a['endpoint'] == 'gmail_mail.send')
        self.assertEqual([a['endpoint'] for a in run['actions']], ['gmail_mail.send'])  # the status is not a card
        [row] = run['done']
        self.assertEqual((row['state'], row['plan'], row['undo']), ('scheduled', 'Mark this conversation resolved', True))
        with connect(self.assistant.database) as db:  # not tracked yet
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items WHERE kind='followup'").fetchone()[0], 0)
        self.assertEqual(self.approve(mail).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        [row] = self.listing()['items'][0]['suggestions']['done']
        self.assertEqual((row['state'], row['title']), ('done', 'Marked this conversation resolved'))
        with connect(self.assistant.database) as db:
            item = db.execute("SELECT id FROM student_support_items WHERE kind='followup'").fetchone()[0]
            self.assertEqual(json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? ORDER BY revision DESC',
                                                   (item,)).fetchone()[0])['state'], 'resolved')
        self.assertNotEqual(self.listing()['items'][0]['suggestions']['state'], 'superseded')  # its own write never withdraws the run
        undone = self.client.post(f"/api/student-support/followups/suggestions/{row['run']}/undo",
                                  json={'action_id': row['id'], 'seen': row['seen'], 'request_id': 'put-back'}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        with connect(self.assistant.database) as db:
            self.assertEqual(json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? ORDER BY revision DESC',
                                                   (item,)).fetchone()[0])['state'], 'needs_reply')
        shown = self.listing()['items'][0]['suggestions']
        self.assertNotEqual(shown['state'], 'superseded')  # the undo is this follow-up's own too
        self.assertEqual(shown['done'][0]['state'], 'undone')

    def test_dont_cancels_a_scheduled_status_and_so_does_a_declined_reply_or_a_round_without_one_runs_it_now(self):
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming()
        def tracking(state, *also):
            def respond(c, prompt):
                observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
                proposing(*also, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                    'observed': observed, 'title': 'Question', 'state': state}, 'Track it'))(c, prompt)
            return respond
        run, _ = self.prepare(tracking('waiting', MAIL))
        mail = run['actions'][0]
        [row] = run['done']
        dont = self.client.post(f"/api/student-support/followups/suggestions/{row['run']}/undo",
                                json={'action_id': row['id'], 'seen': row['seen'], 'request_id': 'dont'}, headers=HEADERS)
        self.assertEqual(dont.json, {'undone': row['id'], 'cancelled': True})
        self.assertEqual(self.approve(mail).status_code, 200)
        self.assertEqual(self.listing()['items'][0]['suggestions']['done'][0]['state'], 'cancelled')
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items WHERE kind='followup'").fetchone()[0], 0)
        self.assertEqual(len(google.sent), 1)
        # Don't counts as an Undo: later status changes on this follow-up are cards.
        with connect(self.assistant.database) as db:
            row = dict(self.suggestions.get(db, run['id']))
            refusal = self.suggestions._automatic(db, row, json.loads(row['source_json']), 'student_support.create', {},
                {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'title': 'Question', 'state': 'resolved'})
        self.assertIn('undid this kind of record', refusal)

    def test_a_scheduled_status_replaced_by_retry_is_not_listed_as_done_for_you(self):
        # Requirement, 2026-10-04: after two Retries, Done for you listed "Marked this conversation waiting on the student" three
        # times, two of them "Not done" (the steps the Retries replaced): "why does it have that line duplicated".
        self.suggestions.automatic = True
        self.fake_mail()
        self.incoming()
        def waiting(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'waiting', 'next': 'Her pick of a time'}, 'Wait for her pick'))(c, prompt)
        run, _ = self.prepare(waiting)
        [first] = run['done']
        self.assertEqual(first['state'], 'scheduled')
        self.assertEqual(self.request(run, 'retry').status_code, 202)
        engine = ScriptedEngine(waiting)
        with patch('assistant.conversations.engine_for', return_value=engine):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        again = self.listing()['items'][0]['suggestions']
        self.assertNotEqual(again['id'], run['id'])
        [row] = again['done']  # only the new preparation's step: the replaced one never happened
        self.assertNotEqual(row['id'], first['id'])
        self.assertEqual(row['state'], 'scheduled')
        mail = next(a for a in again['actions'] if a['endpoint'] == 'gmail_mail.send')
        self.assertEqual(self.approve(mail).status_code, 200)
        [row] = self.listing()['items'][0]['suggestions']['done']
        self.assertEqual((row['state'], row['title']), ('done', 'Marked this conversation waiting on the student'))

    def held_status_round(self, state='todo'):
        """A round with a reply card and this follow-up's own status scheduled for after it (DONE FOR YOU)."""
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming()
        def respond(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': state, 'next': 'Correct the deadline, then tell her'},
                'Track the correction'))(c, prompt)
        run, _ = self.prepare(respond)
        [scheduled] = run['done']
        self.assertEqual(scheduled['state'], 'scheduled')
        return google, run, run['actions'][0], scheduled

    def assert_held(self, scheduled, state='todo', why='You edited the reply, so this no longer happens by itself.'):
        """ASK AFTER AN EDIT: the scheduled status no longer runs by itself; the same request waits as a card, saying why."""
        shown = self.listing()['items'][0]['suggestions']
        self.assertNotIn('done', shown)  # withdrawn, never shown as done or Not done
        old = self.view(scheduled['id'])
        self.assertEqual((old['status'], old['events'][-1]['detail']['held_by_edit']), ('expired', True))
        [card] = [a for a in shown['actions'] if a['pending'] and a['endpoint'] == 'student_support.create']
        self.assertEqual((card['tier'], card['body']['state'], card['body']['next']), ('approve', state, 'Correct the deadline, then tell her'))
        self.assertIn(why, card['reason'])
        self.assertEqual(old['events'][-1]['detail']['error'], why)
        self.assertEqual(card['events'][0]['actor'], 'assistant')  # still the Assistant's proposal, not the instructor's
        self.assertNotEqual(card['body']['request_id'], old['body']['request_id'])
        return card

    def tracked(self):
        with connect(self.assistant.database) as db:
            return [json.loads(r[0])['state'] for r in db.execute(
                "SELECT v.content_json FROM student_support_items i JOIN student_support_revisions v ON v.item_id=i.id "
                "WHERE i.kind='followup' ORDER BY v.revision")]

    def test_an_edited_reply_turns_the_scheduled_status_into_a_card(self):
        # ASK AFTER AN EDIT (requirement, 2026-10-06).
        google, run, mail, scheduled = self.held_status_round()
        edited = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={'action_id': mail['id'],
            'seen': mail['seen'], 'subject': 'Re: Question', 'body': 'The deadline is right; Canvas had not caught up.', 'request_id': 'e1'},
            headers=HEADERS)
        self.assertEqual(edited.status_code, 200, edited.json)
        card = self.assert_held(scheduled)
        self.assertEqual(self.approve(self.view(edited.json['action_id'])).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        self.suggestions.process_one()
        self.assertEqual(self.tracked(), [])  # sending the edited reply runs nothing by itself
        self.assertTrue(self.view(card['id'])['pending'])
        self.assertEqual(self.approve(self.view(card['id'])).status_code, 200)  # approved, it does what was scheduled
        self.assertEqual(self.tracked(), ['todo'])
        self.assertEqual(len([a for a in self.listing()['items'][0]['suggestions']['actions'] if a['endpoint'] == 'student_support.create']), 1)

    def test_a_changed_decision_turns_the_scheduled_status_into_a_card(self):
        # ASK AFTER A CHANGED DECISION (requirement, 2026-10-13).
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming(body='I was sick yesterday and missed class.')
        section, notice = self.listing()['items'][0]['student']['section_id'], self.notice()
        def respond(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(('/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02', 'reason': 'illness',
                                         'notice_message_id': notice, 'status': 'pending'}, 'Record the absence'),  # no Jev here: a card
                      MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                             'observed': observed, 'title': 'Question', 'state': 'todo', 'next': 'Correct the deadline, then tell her'},
                             'Track the decision'))(c, prompt)
        run, _ = self.prepare(respond)
        [scheduled] = run['done']
        self.assertEqual(scheduled['state'], 'scheduled')
        absence, mail = run['actions']
        self.assertEqual((absence['endpoint'], absence['body']['status'], mail['endpoint']), ('absences.create', 'pending', 'gmail_mail.send'))
        def choose(card, status):
            return self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={'action_id': card['id'],
                'seen': card['seen'], 'status': status, 'request_id': uuid.uuid4().hex}, headers=HEADERS)
        self.assertEqual(choose(absence, 'pending').status_code, 400)  # the decision it already has: nothing changed, nothing held
        self.assertEqual(self.listing()['items'][0]['suggestions']['done'][0]['state'], 'scheduled')
        chosen = choose(absence, 'excused')
        self.assertEqual(chosen.status_code, 200, chosen.json)
        card = self.assert_held(scheduled, why='You changed the decision on the absence card, so this no longer happens by itself.')
        self.assertNotIn('You edited the reply', card['reason'])
        # Approve all keeps its order: the record, the reply, then the status, which nothing runs by itself any more.
        waiting = [a for a in self.listing()['items'][0]['suggestions']['actions'] if a['pending']]
        self.assertEqual([a['endpoint'] for a in waiting], ['absences.create', 'gmail_mail.send', 'student_support.create'])
        self.assertEqual(self.approve(self.view(chosen.json['action_id'])).status_code, 200)
        self.assertEqual(self.approve(self.view(mail['id'])).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        self.suggestions.process_one()
        self.assertEqual(self.tracked(), [])
        self.assertTrue(self.view(card['id'])['pending'])
        self.assertEqual(self.client.get('/api/absences').json['absences'][0]['status'], 'excused')
        self.assertEqual(self.approve(self.view(card['id'])).status_code, 200)  # approved, it does what was scheduled
        self.assertEqual(self.tracked(), ['todo'])

    def test_a_step_the_chosen_decision_settled_is_not_carried(self):
        # A DECIDED STEP IS NOT CARRIED (requirement, 2026-11-04). Still their approval: only what it asks changes.
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming(body='I was sick yesterday and missed class.')
        section, notice = self.listing()['items'][0]['student']['section_id'], self.notice()
        def round_with(next_step):
            def respond(c, prompt):
                observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
                proposing(('/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02', 'reason': 'illness',
                                             'notice_message_id': notice, 'status': 'pending'}, 'Record the absence'),
                          MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                                 'observed': observed, 'title': 'Question', 'state': 'todo', 'next': next_step}, 'Track the decision'))(c, prompt)
            return self.prepare(respond)[0]
        run = round_with('Decide October 2; October 5 stays Not excused, and October 6 is the return date.')
        absence, mail = run['actions']
        chosen = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={'action_id': absence['id'],
            'seen': absence['seen'], 'status': 'not_excused', 'request_id': uuid.uuid4().hex}, headers=HEADERS)
        self.assertEqual(chosen.status_code, 200, chosen.json)
        shown = self.listing()['items'][0]['suggestions']
        self.assertNotIn('done', shown)
        [card] = [a for a in shown['actions'] if a['pending'] and a['endpoint'] == 'student_support.create']
        self.assertEqual((card['tier'], card['body']['state'], card['body']['next']), ('approve', 'resolved', ''))  # still asked
        self.assertIn('it no longer asks you to decide it', card['reason'])
        self.assertIn('Close this follow-up', card['summary'])
        self.assertEqual(self.tracked(), [])  # nothing done by itself
        self.assertEqual(self.approve(self.view(chosen.json['action_id'])).status_code, 200)
        self.assertEqual(self.approve(self.view(mail['id'])).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        self.assertEqual(self.approve(self.view(card['id'])).status_code, 200)
        self.assertEqual(self.tracked(), ['resolved'])

    def test_what_a_chosen_decision_settles_of_a_next_step(self):
        # A DECIDED STEP IS NOT CARRIED, on real next steps follow-ups have proposed. Each decided day is the one the step names.
        def left(text, *decided, undecided=False):
            return self.suggestions._without_decision(text, set(decided), undecided, '2026-11-03T23:12:00Z')
        self.assertEqual(left('Decide October 28; October 26 and 30 stay Not excused, and November 3 is the return date.', '2026-10-28'), ('resolved', ''))
        self.assertEqual(left('Decide the October 7 illness absence; no missed in-class graded work is recorded and no documentation was requested.',
                              '2026-10-07'), ('resolved', ''))
        self.assertEqual(left('Review the November 2 orthopaedic-appointment note and decide whether to excuse the absence.', '2026-11-02'), ('resolved', ''))
        # Work beside the decision stays the To do, whatever it is worded around.
        self.assertEqual(left('Review the obituary link and decide the October 12–14 absence; await her take-home quiz screenshots and Friday paper copy.',
                              '2026-10-12', '2026-10-13', '2026-10-14'), ('todo', 'Await her take-home quiz screenshots and Friday paper copy.'))
        self.assertEqual(left("Review Mary's captured doctor's note and decide October 12 absence, then write back; await her paper quiz on return.",
                              '2026-10-12'), ('todo', 'Write back; await her paper quiz on return.'))
        self.assertEqual(left('Verify the medical note, decide the October 21–22 absence, then confirm the earliest permitted Exam 2 makeup and reply.',
                              '2026-10-21', '2026-10-22'), ('todo', 'Confirm the earliest permitted Exam 2 makeup and reply.'))
        # A day not decided yet, or no day while a card still waits: the step stands, word for word.
        self.assertIsNone(left('Decide October 28.', '2026-10-30'))
        self.assertIsNone(left('Decide October 28 and October 30.', '2026-10-28'))
        self.assertIsNone(left('Decide the pending absence.', '2026-10-28', undecided=True))
        self.assertEqual(left('Decide the pending absence.', '2026-10-28'), ('resolved', ''))
        # A step that asks no decision is none of this; "the written decision" to send is work.
        self.assertIsNone(left('Correct the deadline, then tell her', '2026-10-28'))
        self.assertIsNone(left('Send Laura the written decision.', '2026-10-28'))

    def test_a_reply_its_card_sent_answers_a_conversation_in_another_thread(self):
        # A REPLY FROM ITS CARD (requirement, 2026-11-04): a reply to a
        # Canvas conversation starts a Gmail thread of its own, and with no status step nothing tied it to the student's message.
        google = self.fake_mail()
        self.incoming(body='I was sick yesterday and missed class.')
        run, _ = self.prepare(proposing(MAIL))
        [mail] = run['actions']
        sent = self.approve(mail)
        self.assertEqual(sent.json['result']['status'], 'accepted', sent.json)
        self.assertEqual(len(google.sent), 1)
        self.assertEqual(self.client.get('/api/student-support?kind=followup').json['items'][0]['state'], 'needs_reply')  # not captured yet
        self.incoming(ident=sent.json['result']['message_id'], body=REPLY, thread='another', at='2026-10-03T12:05:00Z', author='instructor')
        self.assertEqual(self.client.get('/api/student-support?kind=followup').json['items'], [])
        self.assertEqual(self.support.attention()['followup'], 0)
        # The student writing again brings it back, as for a reply in its own thread.
        self.incoming(ident='a2', body='One more question.', at='2026-10-03T13:00:00Z')
        self.assertEqual([i['state'] for i in self.client.get('/api/student-support?kind=followup').json['items']], ['needs_reply'])

    def test_a_redo_keeps_the_status_step(self):
        # A REDO KEEPS THE STATUS (requirement, 2026-11-04): a round prepared again that proposes the reply and no status of its own.
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming(body='I was sick yesterday and missed class.')
        def status(prompt, state):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            return ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'observed': observed,
                                             'title': 'Question', 'state': state}, 'Track the follow-up')
        run, _ = self.prepare(lambda c, prompt: proposing(MAIL, status(prompt, 'resolved'))(c, prompt))
        [scheduled] = run['done']
        again = lambda respond: (self.assertTrue(self.suggestions.dispatch_one()), self.wait())
        self.assertEqual(self.request(run, 'revise', text='Change only the dates.').status_code, 202)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(proposing(MAIL))):
            again(None)
        run = self.listing()['items'][0]['suggestions']
        self.assertEqual(self.view(scheduled['id'])['status'], 'declined')
        [kept] = run['done']
        self.assertEqual((kept['state'], kept['id'] != scheduled['id']), ('scheduled', True))
        [mail] = run['actions']
        self.assertEqual(self.approve(mail).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        self.suggestions.process_one()
        self.assertEqual(self.tracked(), ['resolved'])  # ran after the reply, as the replaced round's would have
        # A round that proposes a status of its own, or no reply, takes none.
        self.incoming(ident='a2', thread='b1', body='Could I also meet you?', at='2026-10-04T12:00:00Z')
        run, _ = self.prepare(lambda c, prompt: proposing(MAIL, status(prompt, 'resolved'))(c, prompt))
        self.assertEqual(self.request(run, 'revise', text='Keep it open.', request_id='request-second').status_code, 202)
        waiting_on = lambda c, prompt: proposing(MAIL, (status(prompt, 'waiting')[0], {**status(prompt, 'waiting')[1], 'next': 'Her answer'}, 'Wait'))(c, prompt)
        with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(waiting_on)):
            again(None)
        states = [a['body'].get('state') for a in self.listing()['items'][0]['suggestions']['actions'] + self.listing()['items'][0]['suggestions'].get('done', [])
                  if a.get('endpoint', '').startswith('student_support')]
        self.assertNotIn('resolved', states)

    def test_the_steps_you_approved_are_counted_on_the_follow_up(self):
        # WHAT YOU DID (requirement, 2026-10-13): the Follow-up page's read says how many steps the instructor approved
        # in the newest preparation in which they approved one, and when.
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming()
        def first(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, MEETING, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'todo', 'next': 'Look for the quiz'}, 'Track the work'))(c, prompt)
        run, _ = self.prepare(first)
        def shown():
            return self.listing()['items'][0]['suggestions']
        def approved_at(action_id):
            return next(e['at'] for e in self.view(action_id)['events'] if e['kind'] == 'approved')
        self.assertNotIn('approved', run)  # proposed and scheduled, nothing approved
        self.assertNotIn('suggestions', self.listing(False)['items'][0])  # only the page's read carries any of it
        meeting = next(a for a in run['actions'] if a['endpoint'] == 'student_support.create')
        mail = next(a for a in run['actions'] if a['endpoint'] == 'gmail_mail.send')
        self.assertEqual(self.approve(meeting).status_code, 200)
        self.assertEqual((shown()['approved']['count'], shown()['approved']['at'][:19]), (1, approved_at(meeting['id'])[:19]))
        # Their own version of the reply (EDIT IN PLACE) is one step: the card it replaced was declined, never approved.
        edited = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={'action_id': mail['id'],
            'seen': mail['seen'], 'subject': 'Re: Question', 'body': 'My own words.', 'request_id': 'e1'}, headers=HEADERS)
        self.assertEqual(edited.status_code, 200, edited.json)
        self.assertEqual(shown()['approved']['count'], 1)
        self.assertEqual(self.approve(self.view(edited.json['action_id'])).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        after = shown()['approved']
        self.assertEqual((after['count'], after['at'][:19]), (2, approved_at(edited.json['action_id'])[:19]))
        # What the Assistant recorded by itself is Done for you, not a step of theirs; the same read for one student says the same.
        one = self.client.get('/api/student-support?kind=followup&suggestions=1&enrollment_id=' + self.alice).json['items'][0]['suggestions']
        self.assertEqual(one['approved'], after)
        # A later preparation they have approved nothing of leaves the count of the one they did.
        self.incoming('a2', body='One more question.', at='2026-10-03T13:00:00Z')
        again, _ = self.prepare(proposing(('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Re: Question', 'body': 'A second answer.'},
                                           'Answer the second question')))
        self.assertNotEqual(again['id'], run['id'])
        self.assertEqual(again['approved'], after)
        [second] = [a for a in again['actions'] if a['pending']]
        self.assertEqual(self.approve(second).status_code, 200)
        self.assertEqual(shown()['approved']['count'], 1)  # the newest preparation in which they approved a step

    def test_a_message_you_answered_outside_faculty_is_still_prepared_once(self):
        # ANSWERED (2026-10-13): a new message the instructor answered keeps the follow-up's saved status on the page, and the
        # worker still prepares for it once, as it did when such a follow-up read Needs my reply, so the status a reply written
        # outside the cards calls for is still recorded.
        self.incoming()
        item = self.listing(False)['items'][0]
        saved = self.client.post('/api/student-support', json={**item, 'state': 'waiting', 'next': 'Her note', 'request_id': 'wait'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertFalse(self.suggestions.process_one())  # it waits on the student: nothing to prepare
        self.incoming('a2', body='Here is my note.', at='2026-10-03T13:00:00Z')
        self.incoming('a3', body='Thank you, I have it.', at='2026-10-03T14:00:00Z', author='instructor')
        [shown] = self.listing()['items']
        self.assertEqual((shown['state'], shown['new_reply'], shown['new_reply_answered'], shown['attention']), ('waiting', False, True, False))
        self.assertEqual(self.support.attention()['followup'], 0)
        self.assertTrue(self.suggestions.process_one())
        self.assertEqual(self.listing()['items'][0]['suggestions']['state'], 'queued')
        self.dispatch(lambda c, prompt: c.emit('assistant', text='Nothing more is needed.'))
        self.suggestions.process_one()
        self.assertFalse(self.suggestions.dispatch_one())  # once: the same conversation is not prepared again
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_suggestion_runs').fetchone()[0], 1)
        # A thanks that left a Resolved follow-up as it was is not one (A THANKS ON A TRACKED FOLLOW-UP).
        self.assertFalse(self.suggestions.answered_again({'new_reply_answered': True, 'thanks': {'message_id': 'm'}}))
        self.assertFalse(self.suggestions.eligible({'new_reply_answered': True, 'thanks': {'message_id': 'm'}, 'attention': False, 'state': 'resolved'},
                                                   {'current': True, 'memberships': [{'dropped': False}]}))

    def test_declining_the_held_status_after_edit_in_mail_leaves_the_follow_up_alone(self):
        google, run, mail, scheduled = self.held_status_round('resolved')
        data = {'request_id': str(uuid.uuid4()), 'to': 'alice@example.test', 'subject': 'Re: Question', 'body': 'My own words instead.'}
        review = self.client.post('/api/mail/preview', json=data, headers=HEADERS)
        sent = self.client.post('/api/mail/send', json={**data, 'fingerprint': review.json['fingerprint'], 'confirmation': 'send'}, headers=HEADERS)
        self.assertEqual(sent.status_code, 200, sent.json)
        replaced = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/replace", json={'action_id': mail['id'],
            'seen': mail['seen'], 'send_request_id': data['request_id'], 'request_id': 'r1'}, headers=HEADERS)
        self.assertEqual(replaced.json, {'action_id': mail['id'], 'status': 'accepted'})
        card = self.assert_held(scheduled, 'resolved')
        self.suggestions.process_one()  # the worker's turn would have run it (SCHEDULED STATUS)
        self.assertEqual(self.tracked(), [])
        declined = self.client.post(f"/api/assistant/actions/{card['id']}/decline", json={'seen': card['seen']}, headers=HEADERS)
        self.assertEqual(declined.status_code, 200, declined.json)
        self.suggestions.process_one()
        self.assertEqual(self.tracked(), [])

    def test_a_round_without_a_reply_records_its_status_at_once(self):
        self.suggestions.automatic = True
        self.incoming()
        def respond(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'resolved'}, 'Nothing to answer: close it'))(c, prompt)
        run, _ = self.prepare(respond)
        self.assertEqual(run['done'][0]['state'], 'done')
        with connect(self.assistant.database) as db:
            row = db.execute("""SELECT r.content_json FROM student_support_items i JOIN student_support_revisions r ON r.item_id=i.id
                WHERE i.kind='followup' ORDER BY r.revision DESC LIMIT 1""").fetchone()
        self.assertEqual(json.loads(row[0])['state'], 'resolved')

    def test_a_tracked_conversation_is_revised_never_created_twice(self):
        # ONE FOLLOW-UP PER CONVERSATION (requirement, 2026-10-05): a second create is refused while the model can still propose a revision.
        self.suggestions.automatic = True
        self.fake_mail()
        self.incoming()
        item = self.listing()['items'][0]
        tracked = self.client.post('/api/student-support', json={'request_id': 'wait', 'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Question', 'state': 'waiting'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        self.incoming(ident='a2', body='Never mind, that was meant for another professor.', at='2026-10-03T14:00:00Z')
        refusals, seen = [], []
        def respond(c, prompt):
            selected = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])
            seen.append(selected['item_id'])
            body = {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'observed': selected['observed'],
                    'title': 'Question', 'state': 'resolved', 'request_id': '$new_request_id'}
            result, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': '/api/student-support', 'body': body,
                                                                        'summary': 'Close the conversation'})
            refusals.append(json.loads(result).get('error', ''))
            proposing(MAIL, (f"/api/student-support/{tracked.json['id']}",
                             {'base_revision': 1, 'observed': selected['observed'], 'title': 'Question', 'state': 'resolved'},
                             'Close the conversation'))(c, prompt)
        run, _ = self.prepare(respond)
        self.assertEqual(seen, [tracked.json['id']])  # the preparation is told which follow-up to revise
        self.assertIn(f"already tracked by follow-up {tracked.json['id']} (revision 1)", refusals[0])
        self.assertIn(f"POST /api/student-support/{tracked.json['id']}", refusals[0])
        self.assertEqual([a['endpoint'] for a in run['actions']], ['gmail_mail.send'])  # the revision is scheduled, not a card
        [row] = run['done']
        self.assertEqual(row['state'], 'scheduled')
        self.assertEqual(self.approve(run['actions'][0]).status_code, 200)
        self.assertEqual(self.listing()['items'][0]['suggestions']['done'][0]['state'], 'done')
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_support_items WHERE kind='followup'").fetchone()[0], 1)
            self.assertEqual(json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? ORDER BY revision DESC',
                                                   (tracked.json['id'],)).fetchone()[0])['state'], 'resolved')

    def status(self, observed, step):
        return ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'observed': observed,
                                         'title': 'Question', 'state': 'waiting', 'next': step}, 'Wait for her answer')

    def test_the_draft_check_withdraws_the_scheduled_status_with_the_reply(self):
        # THE SCHEDULED STATUS TOO (requirement, 2026-10-13):
        # the draft check withdrew the reply and left its scheduled status, so the status proposed with the repaired reply became a
        # card (one per preparation) and the old one made the follow-up once the reply was sent.
        self.suggestions.automatic = True
        self.fake_mail()
        self.incoming(body='I cannot come on Monday. Is Tuesday possible?')
        turns, seen = [], {}
        def generate(c, prompt):
            turns.append(prompt)
            if len(turns) == 1:
                seen['observed'] = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            text, step = ('Come on Monday.', 'Her pick of a Monday time') if len(turns) == 1 else ('Tuesday at 2 works.', 'Her word that Tuesday at 2 suits her')
            proposing(('/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Meeting', 'body': text}, 'Reply about the day'),
                      self.status(seen['observed'], step))(c, prompt)
        def judge(state, questions):
            return response(questions, nouls={'contradicts_request': .98 if state.get('draft') == 'Come on Monday.' else .01,
                                             'unsupported_promise': .01})
        self.assertTrue(self.suggestions.process_one())
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=judge), \
             patch('assistant.conversations.engine_for', return_value=ScriptedEngine(generate)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        run = self.listing()['items'][0]['suggestions']
        self.assertEqual(len(turns), 2)
        self.assertEqual([a['endpoint'] for a in run['actions'] if a['pending']], ['gmail_mail.send'])  # the repaired status is no card
        [row] = run['done']
        self.assertEqual(row['state'], 'scheduled')
        with connect(self.assistant.database) as db:
            statuses = [a for a in self.suggestions._actions(db, run['id']) if a['endpoint'] == 'student_support.create']
        self.assertEqual([(a['tier'], a['status'], a['body']['next']) for a in statuses],
                         [('auto', 'expired', 'Her pick of a Monday time'), ('auto', 'proposed', 'Her word that Tuesday at 2 suits her')])
        self.assertEqual(self.approve(next(a for a in run['actions'] if a['pending'])).status_code, 200)
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual([r['state'] for r in shown['done']], ['done'])
        self.assertFalse(any(a['pending'] for a in shown['actions']))
        with connect(self.assistant.database) as db:
            [content] = [json.loads(r[0]) for r in db.execute("""SELECT v.content_json FROM student_support_items i
                JOIN student_support_revisions v ON v.item_id=i.id WHERE i.kind='followup'""")]
        self.assertEqual((content['state'], content['next']), ('waiting', 'Her word that Tuesday at 2 suits her'))

    def test_a_status_card_left_after_the_follow_up_was_saved_is_withdrawn_in_your_words(self):
        # The same card on the page: "Save follow-up" answered with the refusal written for the model (a follow-up's ID, a route,
        # "base_revision"). A second status in one preparation is a card; once the first has made the follow-up it cannot be saved.
        self.suggestions.automatic = True
        self.fake_mail()
        self.incoming()
        def respond(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, self.status(observed, 'Her pick of a time'), self.status(observed, 'Her pick of a day'))(c, prompt)
        run, _ = self.prepare(respond)
        mail, card = run['actions']
        self.assertEqual((card['endpoint'], card['body']['next']), ('student_support.create', 'Her pick of a day'))
        self.assertEqual(self.approve(mail).status_code, 200)
        self.assertEqual(self.tracked(), ['waiting'])
        refused = self.approve(self.view(card['id']))
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn('follow-up is already saved (Waiting on student), so this step was withdrawn', refused.json['error'])
        for word in ('/api/', 'base_revision', 'revision 1'):
            self.assertNotIn(word, refused.json['error'])
        after = self.view(card['id'])
        self.assertEqual((after['pending'], after['status'], after['events'][-1]['detail']['superseded']), (False, 'expired', True))
        shown = self.listing()['items'][0]['suggestions']
        self.assertFalse(any(a['pending'] for a in shown['actions']))  # nothing is left waiting for you
        self.assertEqual(self.tracked(), ['waiting'])

    def test_a_promised_action_is_recorded_as_to_do_with_its_next_step_once_the_reply_is_sent(self):
        # WAITING ON (requirement, 2026-10-04): a reply that promises something leaves the follow-up To do with a one-line next
        # step, recorded by itself after the confirmed send; To do is the instructor's own action, never prepared automatically.
        self.suggestions.automatic = True
        google = self.fake_mail()
        self.incoming()
        step = 'Ask Disability Resources how to book her quizzes there, then write back'
        def respond(c, prompt):
            observed = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])['observed']
            proposing(MAIL, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'observed': observed, 'title': 'Question', 'state': 'todo', 'next': step}, 'Keep it on your list'))(c, prompt)
        run, _ = self.prepare(respond)
        self.assertEqual([a['endpoint'] for a in run['actions']], ['gmail_mail.send'])  # the status is not a card
        [row] = run['done']
        self.assertEqual((row['state'], row['plan']), ('scheduled', 'Mark this conversation To do'))
        self.assertEqual(self.approve(run['actions'][0]).status_code, 200)
        self.assertEqual(len(google.sent), 1)
        item = self.listing()['items'][0]
        self.assertEqual((item['state'], item['attention'], item['next']), ('todo', True, step))
        self.assertEqual(self.listing()['items'][0]['suggestions']['done'][0]['state'], 'done')
        self.assertFalse(self.suggestions.eligible(item, {'current': True, 'memberships': [{'dropped': 0}]}))
        self.assertTrue(self.suggestions.eligible({**item, 'state': 'needs_reply'}, {'current': True, 'memberships': [{'dropped': 0}]}))
        # A status change that also rewrites the purpose or details is still a card.
        with connect(self.assistant.database) as db:
            current = dict(self.suggestions.get(db, run['id']))
            item_id = db.execute("SELECT id FROM student_support_items WHERE kind='followup'").fetchone()[0]
            current['item_id'] = item_id
            self.assertIsNone(self.suggestions._tracking_bounds(db, current, 'student_support.revise', {'item_id': item_id},
                                                                {'state': 'waiting', 'next': 'Her reply'}))
            self.assertIn('more than the status', self.suggestions._tracking_bounds(db, current, 'student_support.revise', {'item_id': item_id},
                                                                                    {'state': 'todo', 'details': 'Something else'}))

    def test_a_student_who_only_acknowledges_keeps_their_follow_up_waiting_with_no_reply(self):
        # STILL WAITING (requirement, 2026-11-04).
        self.suggestions.automatic = True
        self.session()
        item = self.listing()['items'][0]
        tracked = self.client.post('/api/student-support', json={'request_id': 'wait', 'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Question', 'state': 'waiting', 'next': 'Her travel notice'},
            headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()  # the triage is read while Jev is on
        patch('assistant.tool_guidance.ToolGuidance.enabled', return_value=False).start()
        def analyse(kind, needs_action):
            triage = self.support.triage
            triage._last = 0
            with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, {'kind': kind}, {'needs_action': needs_action})):
                self.assertTrue(triage.process_one())
        def keep(c, prompt):
            shown = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])
            result, _ = c.service.run_tool(c.session, 'faculty_write', {'method': 'POST', 'path': f"/api/student-support/{tracked.json['id']}",
                'body': {'title': 'Question', 'state': 'waiting', 'next': 'Her travel notice', 'observed': shown['observed'],
                         'base_revision': tracked.json['revision'], 'request_id': '$new_request_id'}, 'summary': 'Keep waiting'})
            return result
        # A new question still needs a reply first.
        self.incoming(ident='a2', body='Which form do you need?', at='2026-10-03T14:00:00Z')
        analyse('request', .9)
        results = []
        self.prepare(lambda c, prompt: results.append(keep(c, prompt)))
        self.assertIn('propose the reply first', results[-1])
        # "I'll send it soon" stays Waiting on student by itself. Jev read needs-action high, as it did Britt's, so THANKS CLOSES leaves it.
        self.incoming(ident='a3', body='Thank you! I will get that to you as soon as possible.', at='2026-10-03T15:00:00Z')
        analyse('acknowledgment', 1.0)
        self.assertFalse(self.suggestions.close_acknowledged())
        self.assertEqual(self.listing()['items'][0]['state'], 'needs_reply')
        self.prepare(lambda c, prompt: results.append(keep(c, prompt)))
        self.assertNotIn('error', json.loads(results[-1]), results[-1])
        shown = self.listing()['items'][0]
        self.assertEqual((shown['state'], shown['new_reply'], shown['next'], shown['attention']), ('waiting', False, 'Her travel notice', False))
        self.assertEqual(shown['suggestions']['done'][-1]['state'], 'done')  # under Done for you, with Undo

    def test_a_thank_you_to_a_waiting_follow_up_closes_it_for_you_with_undo(self):
        # THANKS CLOSES (requirement, 2026-10-04): Jev reads the reply as only an acknowledgment, so the follow-up is resolved.
        self.addCleanup(patch.stopall)
        self.session()  # a preparation exists for this conversation (its Done for you list)
        item = self.listing()['items'][0]
        tracked = self.client.post('/api/student-support', json={'request_id': 'wait', 'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Question', 'state': 'waiting'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        self.assertFalse(self.suggestions.close_acknowledged())  # no new reply yet
        patch('jev.client.available', return_value=True).start()
        def analyse(kind, **nouls):
            triage = self.support.triage
            triage._last = 0
            with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, {'kind': kind}, {'needs_action': .0, **nouls})):
                self.assertTrue(triage.process_one())
        self.incoming(ident='a2', body='Is the quiz still on Friday?', at='2026-10-03T14:00:00Z')
        analyse('request', needs_action=.9)
        self.assertFalse(self.suggestions.close_acknowledged())  # a new question is not a thank-you
        self.incoming(ident='a3', body='Thanks, that helps!', at='2026-10-03T15:00:00Z')
        analyse('acknowledgment')
        self.assertTrue(self.suggestions.close_acknowledged())
        self.assertFalse(self.suggestions.close_acknowledged())  # never twice for one reply
        def state():
            with connect(self.assistant.database) as db:
                return json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? ORDER BY revision DESC LIMIT 1',
                                             (tracked.json['id'],)).fetchone()[0])['state']
        self.assertEqual(state(), 'resolved')
        shown = next(i for i in self.listing()['items'] if i['id'] == tracked.json['id'])
        [row] = shown['suggestions']['done']
        self.assertEqual((row['title'], row['state'], row['undo']), ('Marked this follow-up resolved', 'done', True))
        undone = self.client.post(f"/api/student-support/followups/suggestions/{row['run']}/undo",
                                  json={'action_id': row['id'], 'seen': row['seen'], 'request_id': 'reopen'}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual(state(), 'waiting')
        self.assertFalse(self.suggestions.close_acknowledged())  # undone once: not again on this follow-up

    def test_a_conversation_joined_to_another_withdraws_its_cards_and_its_preparation_reads_both(self):
        # SAME MATTER (2026-10-07): the new email's own suggestions no longer apply once it is part of the earlier follow-up, whose
        # next preparation reads both conversations.
        self.incoming(ident='later', body='About Friday too', thread='b3', at='2026-10-03T13:00:00Z')
        run, _ = self.prepare()
        self.assertTrue(run['actions'][0]['pending'])
        self.incoming(ident='first', body='Could I meet you?', thread='b1', at='2026-10-03T12:00:00Z')
        joined = self.client.post('/api/student-support/followups/join', json={'enrollment_id': self.alice, 'source_key': 'gmail:b3',
                                  'with_key': 'gmail:b1', 'request_id': 'join'}, headers=HEADERS)
        self.assertEqual(joined.status_code, 200, joined.json)
        self.suggestions.process_one()  # the worker's discovery pass withdraws what no longer applies
        action = self.assistant._view(*self.assistant.action(run['actions'][0]['id']))
        self.assertEqual(action['status'], 'expired')
        [item] = self.listing()['items']
        self.assertEqual(item['source_key'], 'gmail:b1')
        with connect(self.assistant.database) as db:
            source = self.suggestions.source(db, self.alice, 'conversation:gmail:b1')
        self.assertEqual(len(source['messages']), 2)

    def test_a_thank_you_on_a_to_do_closes_it_only_when_nothing_more_is_owed(self):
        # TO DO THANKS (requirement, 2026-10-07): Holden's "Thank you!" after "I found your quiz 2" ended his; Fiona's after "you should expect
        # another email from me" did not.
        self.addCleanup(patch.stopall)
        self.session()
        item = self.listing()['items'][0]
        with patch('student_support.service.timestamp', return_value='2026-10-03T12:30:00+00:00'):
            tracked = self.client.post('/api/student-support', json={'request_id': 'todo', 'kind': 'followup', 'enrollment_id': self.alice,
                'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Quiz 2 missing', 'state': 'todo',
                'next': 'Find his Quiz 2 paper'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        patch('jev.client.available', return_value=True).start()
        asked = []
        def jev(kind='acknowledgment', owed=.99, **nouls):
            def answer(state, questions):
                asked.append(sorted(questions))
                return response(questions, {'kind': kind}, {'needs_action': .0, 'owed': owed, 'work_owed': owed, **nouls})
            return patch('jev.client.evaluate', side_effect=answer)
        def analyse(**answers):
            triage = self.support.triage
            triage._last = 0
            with jev(**answers):
                self.assertTrue(triage.process_one())
        def shown():
            return next(i for i in self.listing()['items'] if i['id'] == tracked.json['id'])
        def state():
            with connect(self.assistant.database) as db:
                return json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? ORDER BY revision DESC LIMIT 1',
                                             (tracked.json['id'],)).fetchone()[0])['state']
        # A thanks before the instructor wrote again cannot close the work; its reply/work obligations can be checked.
        self.incoming(ident='a2', body='Thank you!', at='2026-10-03T13:00:00Z')
        analyse()
        found = shown()
        self.assertEqual((found['state'], found['attention'], found['new_reply'], found['thanks']['after_reply']), ('todo', True, False, False))
        with jev():
            self.assertTrue(self.suggestions.close_acknowledged())
            self.assertFalse(self.suggestions.close_acknowledged())
        # Holden: the instructor's message finished it; his thanks ends it, Done for you with Undo.
        self.incoming(ident='i1', body='I found your quiz 2; I will have it graded shortly.', at='2026-10-03T14:00:00Z', author='instructor')
        self.incoming(ident='a3', body='Thank you!', at='2026-10-03T15:00:00Z')
        analyse()
        self.assertTrue(shown()['thanks']['after_reply'])
        asked.clear()
        with jev(owed=.02):
            self.assertTrue(self.suggestions.close_acknowledged())  # Jev asked once
            self.assertTrue(self.suggestions.close_acknowledged())  # resolved
            self.assertFalse(self.suggestions.close_acknowledged())
        self.assertEqual(asked, [['owed', 'work_owed']])
        self.assertEqual(state(), 'resolved')
        [row] = shown()['suggestions']['done']
        self.assertEqual((row['title'], row['undo']), ('Marked this follow-up resolved', True))
        with patch('student_support.service.timestamp', return_value='2026-10-03T15:30:00+00:00'):  # the synthetic day's clock
            undone = self.client.post(f"/api/student-support/followups/suggestions/{row['run']}/undo",
                                      json={'action_id': row['id'], 'seen': row['seen'], 'request_id': 'reopen'}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual(state(), 'todo')
        # Fiona: still owed ("you should expect another email from me"): it stays To do, with no question for the instructor.
        self.incoming(ident='i2', body='You should expect another email from me this week.', at='2026-10-03T16:00:00Z', author='instructor')
        self.incoming(ident='a4', body='Okay thank you so much!', at='2026-10-03T17:00:00Z')
        analyse(needs_action=.85)  # as Jev read hers on live: the quizzes are still to be booked, which her To do records
        with jev(owed=.97):
            self.assertTrue(self.suggestions.close_acknowledged())
            self.assertFalse(self.suggestions.close_acknowledged())
        found = shown()
        self.assertEqual((state(), found['state'], found['attention']), ('todo', 'todo', True))
        self.assertNotIn('finished', found['suggestions'])
        # Unsure: the instructor's one click, never by itself (a conversation of its own: more than six messages are never a thanks).
        self.incoming(ident='q1', body='Can I see my Quiz 3?', thread='b2', at='2026-10-03T18:00:00Z')
        other = next(i for i in self.listing()['items'] if not i['id'])
        with patch('student_support.service.timestamp', return_value='2026-10-03T18:10:00+00:00'):
            tracked = self.client.post('/api/student-support', json={'request_id': 'todo-2', 'kind': 'followup', 'enrollment_id': self.alice,
                'source_key': other['source_key'], 'observed': other['observed'], 'title': 'Quiz 3 paper', 'state': 'todo'}, headers=HEADERS)
        self.incoming(ident='i3', body='It is in my office.', thread='b2', at='2026-10-03T18:30:00Z', author='instructor')
        self.incoming(ident='a5', body='Thanks!', thread='b2', at='2026-10-03T19:00:00Z')
        analyse()
        with jev(owed=.5):
            self.assertTrue(self.suggestions.close_acknowledged())
            self.assertFalse(self.suggestions.close_acknowledged())
        self.assertEqual((state(), shown()['state']), ('todo', 'todo'))
        self.assertTrue(shown()['suggestions']['finished'])

    def test_the_new_controls_are_never_assistant_routes(self):
        for name in ('approve', 'replace', 'note', 'prepare', 'mute', 'pause'):
            self.assertEqual(policy.base_rule('student_support.suggestion_' + name).tier, 'never', name)
        session = self.session()
        self.assertEqual(self.tool_write(session, '/api/student-support/followups/suggestions/pause', {'paused': True})['outcome'], 'refused')

    def test_term_policies_guide_each_preparation_and_flag_older_suggestions(self):
        # TERM POLICIES (requirement, 2026-10-03): saved per course term on the follow-up page, read by every preparation.
        term = next(t for t in self.client.get('/api/student-support/policies').json['terms'] if t['term_id'] == self.term)
        self.assertEqual((term['revision'], term['content']), (0, ''))
        def save(content, base, request_id):
            return self.client.post(f'/api/student-support/policies/{self.term}', json={'content': content, 'base_revision': base,
                                                                                        'request_id': request_id}, headers=HEADERS)
        first = save('Office hours: Mondays 2-3 pm in Blocker 512.\nSign emails as Dr. S.', 0, 'p1')
        self.assertEqual((first.status_code, first.json['revision']), (200, 1), first.json)
        self.assertEqual(save('Office hours: Mondays 2-3 pm in Blocker 512.\nSign emails as Dr. S.', 0, 'p1').json['revision'], 1)
        self.assertEqual(save('Something else', 0, 'p1').status_code, 409)  # the request ID already saved other text
        self.assertEqual(save('Something else', 0, 'p2').status_code, 409)  # edited from an older revision
        self.assertEqual(save('x' * 20001, 1, 'p2').status_code, 400)
        self.incoming()
        run, engine = self.prepare()
        self.assertIn('Term policies the instructor set for MATH 140', engine.prompt)
        self.assertIn('Mondays 2-3 pm in Blocker 512', engine.prompt)
        # REPLY VOICE (requirement, 2026-10-04): policies decide a reply's content; the reply never recites them.
        self.assertIn('a reply never quotes, cites or paraphrases them unless the student asked what a rule is', engine.prompt)
        self.assertIn('Mail to a student sounds like the instructor', engine.prompt)
        self.assertIn('**Mail to a student sounds like the instructor**', (Path(__file__).resolve().parent.parent / 'assistant' / 'guide.md').read_text())
        self.assertFalse(run['policies_changed'])
        self.assertEqual(save('Office hours: Tuesdays 1-2 pm.', 1, 'p3').json['revision'], 2)
        shown = self.listing()['items'][0]['suggestions']
        self.assertTrue(shown['policies_changed'])
        self.assertEqual(self.request(shown, 'retry').status_code, 202)
        self.assertIn('Tuesdays 1-2 pm', self.dispatch().prompt)
        self.assertFalse(self.listing()['items'][0]['suggestions']['policies_changed'])
        cleared = save('', 2, 'p4').json
        self.assertEqual((cleared['revision'], cleared['content']), (3, ''))
        self.assertEqual(policy.base_rule('student_support.save_policy').tier, 'approve')  # the assistant may only propose policies

    def policy_areas(self):
        return next(term for term in self.client.get('/api/student-support/policies?areas=1').json['terms'] if term['term_id'] == self.term)

    def save_areas(self, areas, base=0, request_id='areas-1'):
        return self.client.post(f'/api/student-support/policies/{self.term}', json={
            'areas': areas, 'base_revision': base, 'request_id': request_id}, headers=HEADERS)

    def test_policy_areas_preserve_legacy_text_and_default_reads(self):
        # TEACHING-WORKFLOWS / TERM POLICIES: no history is rewritten and opt-in fields do not change ordinary API bodies.
        legacy = 'Office hours: Mondays 2–3 pm.\n\nSign off as Chris. <Keep these exact words>'
        self.client.post(f'/api/student-support/policies/{self.term}', json={
            'content': legacy, 'base_revision': 0, 'request_id': 'legacy'}, headers=HEADERS).json
        ordinary = self.client.get('/api/student-support/policies').data
        shown = self.policy_areas()
        self.assertEqual(shown['areas'], {'additional': {'mode': 'guidance', 'guidance': legacy}})
        self.assertEqual(shown['content'], legacy)
        self.assertEqual(self.client.get('/api/student-support/policies').data, ordinary)
        areas = {**shown['areas'], 'quiz_makeups': {'mode': 'guidance', 'guidance': 'Confirm the missed quiz and date first.'},
                 'exam_makeups': {'mode': 'ask', 'guidance': 'Consult me for an alternate exam sitting.'}}
        saved = self.save_areas(areas, base=1).json
        self.assertEqual((saved['revision'], saved['areas']), (2, areas))
        self.assertIn(legacy, saved['content'])
        self.assertIn('Missed in-class quizzes', saved['content'])
        self.assertIn('Ask me case by case before proposing a decision', saved['content'])
        ordinary_saved = next(term for term in self.client.get('/api/student-support/policies').json['terms'] if term['term_id'] == self.term)
        self.assertNotIn('areas', ordinary_saved)
        self.assertEqual(ordinary_saved['content'], saved['content'])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT content FROM student_support_term_policies WHERE term_id=? AND revision=1', (self.term,)).fetchone()[0], legacy)
        self.incoming()
        _, engine = self.prepare()
        for value in ('Confirm the missed quiz and date first.', 'Consult me for an alternate exam sitting.', legacy):
            self.assertIn(value, engine.prompt)
        self.assertNotIn('Does an absence need to cover the entire availability period?', engine.prompt)  # editor prompts are not policies

    def test_policy_area_modes_are_revisioned_idempotent_and_clearable(self):
        areas = {'homework': {'mode': 'ask', 'guidance': ''}, 'appointments': {'mode': 'guidance', 'guidance': 'Use my calendar.'}}
        first = self.save_areas(areas)
        self.assertEqual((first.status_code, first.json['revision']), (200, 1), first.json)
        self.assertEqual(self.save_areas(areas).json, first.json)
        different = {**areas, 'homework': {'mode': 'guidance', 'guidance': 'Ask me case by case before proposing a decision or arrangement in this area; ask for my guidance rather than inventing a policy.'}}
        self.assertEqual(self.save_areas(different).status_code, 409)
        self.assertEqual(self.save_areas(different, request_id='areas-stale').status_code, 409)
        # A handling change is a new revision even when the resulting prompt text happens to be identical.
        changed = self.save_areas(different, base=1, request_id='areas-2').json
        self.assertEqual((changed['content'], changed['revision']), (first.json['content'], 2))
        self.assertEqual(self.save_areas(different, base=2, request_id='same').json['revision'], 2)
        cleared = self.save_areas({'homework': {'mode': 'unset', 'guidance': ''}}, base=2, request_id='clear').json
        self.assertEqual((cleared['content'], cleared['areas'], cleared['revision']), ('', {}, 3))
        self.assertEqual(self.policy_areas()['areas'], {})
        self.assertEqual(policy.base_rule('student_support.save_policy').tier, 'approve')

    def test_bad_policy_areas_never_write_a_revision(self):
        for areas in ([], {'unknown': {'mode': 'ask', 'guidance': ''}}, {'homework': 'text'},
                      {'homework': {'mode': 'allow', 'guidance': ''}}, {'homework': {'mode': 'guidance', 'guidance': ''}},
                      {'homework': {'mode': 'unset', 'guidance': 'Do not silently lose this'}},
                      {'homework': {'mode': 'ask', 'guidance': None}}, {'homework': {'mode': 'ask', 'guidance': 'a\x00b'}},
                      {'homework': {'mode': 'ask', 'guidance': '', 'automatic_send': True}},
                      {'homework': {'mode': 'guidance', 'guidance': 'x' * 10000}, 'online_quizzes': {'mode': 'guidance', 'guidance': 'y' * 10000}}):
            with self.subTest(areas=str(areas)[:150]):
                self.assertEqual(self.save_areas(areas).status_code, 400)
        ambiguous = self.client.post(f'/api/student-support/policies/{self.term}', json={
            'areas': {}, 'content': 'Ambiguous', 'base_revision': 0, 'request_id': 'both'}, headers=HEADERS)
        self.assertEqual(ambiguous.status_code, 400)
        self.assertEqual(self.policy_areas()['revision'], 0)

    def test_policy_area_storage_is_term_owned_and_immutable(self):
        import sqlite3
        from browser_course_pages import seed_course
        seed_course(self.client, code='MATH 141', name='Business Math II', sections=[507, 508])
        other = next(term for term in self.client.get('/api/student-support/policies?areas=1').json['terms'] if term['term_id'] != self.term)
        self.save_areas({'absences': {'mode': 'ask', 'guidance': 'Consult me.'}})
        self.assertEqual(next(term for term in self.client.get('/api/student-support/policies?areas=1').json['terms'] if term['term_id'] == other['term_id'])['areas'], {})
        with connect(self.assistant.database) as db:
            for sql in ('UPDATE student_support_term_policy_areas SET areas_json=\'{}\'', 'DELETE FROM student_support_term_policy_areas'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO student_support_term_policy_areas VALUES (?,99,\'{}\')', (self.term,))
        # Legacy clients can still replace/clear the full text, without retaining hidden structured guidance.
        result = self.client.post(f'/api/student-support/policies/{self.term}', json={
            'content': 'Replacement guidance', 'base_revision': 1, 'request_id': 'legacy-replacement'}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.policy_areas()['areas'], {'additional': {'mode': 'guidance', 'guidance': 'Replacement guidance'}})
