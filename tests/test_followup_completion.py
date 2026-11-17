"""ASSISTANT PLAN COMPLETION: complete workflow scenarios on isolated fake owners."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from test_followup_suggestions import SuggestionsTests, proposing, MAIL, MEETING
from test_gmail_mail import ACCOUNT
from test_storage import HEADERS
from rosters import connect
from scheduling.clock import campus_today
from followup_plan_eval import evaluate


class CompletionTests(SuggestionsTests):
    # Reuse the fixture, not its test methods (the suite below discovers only this
    # module's own scenarios through load_tests).
    def completion(self, *steps, state='resolved'):
        def respond(c, prompt):
            selected = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])
            proposing(*steps, ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice,
                'source_key': 'gmail:b1', 'observed': selected['observed'], 'title': 'Question', 'state': state,
                'next': 'The student replies' if state == 'waiting' else ''}, 'Update tracking'))(c, prompt)
        return respond

    def test_work_precedes_mail_and_failure_keeps_attention(self):
        self.suggestions.automatic = True
        fake = self.fake_mail()
        self.incoming()
        meeting = (MEETING[0], {**MEETING[1](type('C', (), {'run': {'enrollment_id': self.alice}})()),
                             'end_time': '16:00'}, MEETING[2])
        run, _ = self.prepare(self.completion(MAIL, meeting))
        score = evaluate(run, ['student_support.create', 'gmail_mail.send'])
        self.assertFalse(any(score.values()), score)
        self.assertEqual([a['endpoint'] for a in run['actions']], ['student_support.create', 'gmail_mail.send'])
        self.assertEqual(self.approve(run['actions'][1]).status_code, 409)
        original = self.assistant.transport.request
        def fail(method, path, body=None):
            if method == 'POST' and path == '/api/student-support' and (body or {}).get('kind') == 'appointment':
                return 503, {'error': 'Synthetic failure'}, ''
            return original(method, path, body)
        with patch.object(self.assistant.transport, 'request', side_effect=fail):
            answer = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/approve",
                json={'seen': {a['id']: a['seen'] for a in run['actions']}}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(len(answer.json['results']), 1)
        self.assertEqual(fake.sent, [])
        self.assertEqual(self.support.attention()['followup'], 1)
        self.assertTrue(self.listing()['items'][0]['suggestions']['needs_attention'])
        self.assertNotEqual(self.listing()['items'][0]['state'], 'resolved')

    def test_plan_rubric_detects_missing_work_questions_edits_and_false_completion(self):
        plan = {'question': {'text': 'Which student?'}, 'actions': [{'endpoint': 'gmail_mail.send', 'body': {},
            'status': 'failed', 'pending': False, 'edited': True}]}
        score = evaluate(plan, ['student_support.create', 'gmail_mail.send'], final_state='resolved')
        self.assertEqual({k: v for k, v in score.items() if v}, {'missing_steps': 1, 'unnecessary_questions': 1,
                                                              'human_edits': 1, 'incorrect_completion': 1})

    def test_real_owner_changes_trigger_watches_and_invalidate_prepared_mail(self):
        self.fake_mail()
        self.incoming()
        with connect(self.assistant.database) as db:
            section = db.execute('SELECT section_id FROM roster_memberships WHERE enrollment_id=? LIMIT 1', (self.alice,)).fetchone()[0]
        absence = self.client.post('/api/absences', json={'section_id': section, 'enrollment_id': self.alice,
            'first_date': '2026-10-08', 'last_date': '2026-10-08', 'reason': 'illness', 'request_id': 'absence'}, headers=HEADERS)
        self.assertEqual(absence.status_code, 201, absence.json)
        gradebook = self.client.post('/api/gradebook/terms/' + self.term + '/setup', json={'base_revision': None,
            'request_id': 'setup', 'setup': {'items': [{'title': 'Quiz', 'points': '10', 'category': 'quiz', 'due_date': '2026-10-10'}]}}, headers=HEADERS)
        self.assertEqual(gradebook.status_code, 200, gradebook.json)
        column = gradebook.json['setup']['items'][0]['id']
        watches = [self.watch('absence', {'id': absence.json['id']}, check_back=''), self.watch('grade', {'id': column}, check_back='')]
        options = self.client.get('/api/student-support/followups/watch-options/' + self.alice)
        self.assertEqual(options.status_code, 200, options.json)
        self.assertEqual(options.json['targets']['grade'][0]['label'], 'Quiz')
        run, _ = self.prepare(proposing(MAIL))
        response = self.client.post('/api/absences/' + absence.json['id'], json={'base_revision': 1,
            'status': 'excused', 'request_id': 'decision'}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        row = next(s['row'] for s in gradebook.json['students'] if s['enrollment_id'] == self.alice)
        response = self.client.post('/api/gradebook/terms/' + self.term + '/scores', json={'request_id': 'score',
            'changes': [{'item_id': column, 'row': row, 'base': None, 'status': 'scored', 'points': '8'}]}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        # WHAT CAN MAKE A STEP WRONG: the absence's decision refuses; its other record changes and the grade are warnings.
        self.assertEqual(self.listing()['items'][0]['suggestions']['context_changes'],
                         {'refuse': ['absence decisions'], 'warn': ['absence records', 'grades or due dates']})
        refused = self.approve(run['actions'][0])
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn('absence decisions', refused.json['error'].lower())
        self.assertNotIn('grades or due dates', refused.json['error'].lower())
        with self.rosters_lock(), connect(self.assistant.database) as db:
            db.execute('UPDATE student_support_watches SET next_check=0')
        for _ in watches:
            self.assertTrue(self.suggestions.watches.process_one())
        with connect(self.assistant.database) as db:
            self.assertEqual({r[0] for r in db.execute('SELECT state FROM student_support_watches')}, {'triggered'})

    def test_staff_only_email_cannot_satisfy_waiting_on_student(self):
        self.suggestions.automatic = True
        self.fake_mail()
        self.incoming()
        staff = (MAIL[0], {**MAIL[1], 'to': [ACCOUNT]}, 'Ask the instructor')
        run, _ = self.prepare(self.completion(staff, state='waiting'))
        self.assertFalse(run.get('done'))
        self.assertEqual(self.approve(run['actions'][0]).status_code, 200)
        self.assertNotEqual(self.listing()['items'][0]['state'], 'waiting')

    def test_a_declined_step_holds_back_neither_the_reply_nor_its_status(self):
        # The instructor's review (2026-10-08): declining the meeting left the reply refused for good ("finish the preceding work").
        self.suggestions.automatic = True
        fake = self.fake_mail()
        self.incoming()
        meeting = (MEETING[0], {**MEETING[1](type('C', (), {'run': {'enrollment_id': self.alice}})()), 'end_time': '16:00'}, MEETING[2])
        run, _ = self.prepare(self.completion(MAIL, meeting))
        step, mail = run['actions']
        self.assertEqual(step['endpoint'], 'student_support.create')
        declined = self.client.post(f"/api/assistant/actions/{step['id']}/decline", json={'seen': step['seen']}, headers=HEADERS)
        self.assertEqual(declined.status_code, 200, declined.json)
        answer = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/approve",
            json={'seen': {mail['id']: mail['seen']}}, headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(self.listing()['items'][0]['state'], 'resolved')

    def test_a_declined_note_to_staff_leaves_the_students_reply_its_status(self):
        # The instructor's review (2026-10-08): declining a note to the instructor cancelled Waiting on student after the real reply.
        self.suggestions.automatic = True
        fake = self.fake_mail()
        self.incoming()
        staff = (MAIL[0], {**MAIL[1], 'to': [ACCOUNT]}, 'Note to the instructor')
        run, _ = self.prepare(self.completion(MAIL, staff, state='waiting'))
        note = next(a for a in run['actions'] if a['body']['to'] == [ACCOUNT])
        reply = next(a for a in run['actions'] if a['body']['to'] == MAIL[1]['to'])
        declined = self.client.post(f"/api/assistant/actions/{note['id']}/decline", json={'seen': note['seen']}, headers=HEADERS)
        self.assertEqual(declined.status_code, 200, declined.json)
        self.assertEqual(self.approve(reply).status_code, 200)
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(self.listing()['items'][0]['state'], 'waiting')

    def test_a_reply_posted_in_the_students_canvas_conversation_is_their_reply(self):
        # MAIL-CANVAS-POST records no email recipients; the conversation it was posted in says whom it reached (2026-10-08 review).
        run = {'enrollment_id': self.alice, 'source_key': 'canvas:77',
               'source_json': json.dumps({'messages': [{'author_role': 'student', 'sent_at': '2026-10-03T12:00:00Z'}]})}
        with self.rosters_lock(), connect(self.assistant.database) as db:
            for send, conversation in (('send-here', '77'), ('send-elsewhere', '78')):
                db.execute('INSERT INTO gmail_mail_sends(id,request_id,request_hash,sender,to_json,cc_json,bcc_json,subject,body_text,'
                           'reply_thread_id,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (send, 'request-' + send, '0' * 64, ACCOUNT, '[]', '[]', '[]', 'Re: Question', 'Yes.', '', 'instructor',
                            '2026-10-03T13:00:00+00:00'))
                db.execute('INSERT INTO gmail_mail_canvas_posts(send_id,conversation_id,recipient_user_id,recipient_name,'
                           'answered_message_id,course_name,created_at) VALUES(?,?,?,?,?,?,?)',
                           (send, conversation, '501', 'Alice', '9001', 'MATH 140', '2026-10-03T13:00:00+00:00'))
            db.commit()
            def card(send):
                return {'id': 'card-' + send, 'endpoint': 'gmail_mail.send', 'body': {'request_id': 'request-' + send, 'to': []},
                        'created_at': '2026-10-03T13:00:00+00:00'}
            self.assertTrue(self.suggestions.student_reply(db, run, card('send-here')))
            self.assertFalse(self.suggestions.student_reply(db, run, card('send-elsewhere')))

    def test_a_draft_approved_the_next_campus_day_reads_its_own_calendar_window(self):
        # The instructor's review (2026-10-08): the calendar window moved with the day, so every draft went stale at midnight.
        fake = self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        import assistant.student_context as context
        with patch.object(context, 'campus_today', return_value=campus_today() + timedelta(days=1)):
            answer = self.approve(run['actions'][0])
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(len(fake.sent), 1)

    def test_the_plans_own_makeup_sitting_never_stales_its_reply_but_a_later_change_to_it_does(self):
        # The 2026-10-08 review: a makeup sitting the plan made showed on the calendar as someone else's change, so its reply was refused.
        fake = self.fake_mail()
        session = self.session()
        exams = self.app.extensions['frq_grading'].exams
        section = self.listing()['items'][0]['student']['section_id']
        with connect(self.assistant.database) as db:
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (self.term,)).fetchone()[0]
        assignment = self.app.extensions['documents'].create_assignment(definition, 'exam-1', 'Exam 1')
        event = exams.create({'term_id': self.term, 'assignment_id': assignment['id'], 'section_ids': [section]})
        with connect(self.assistant.database) as db:  # the preparation starts once the exam exists
            self.suggestions.context.remember(db, self.suggestions.get(db, session['_followup_run']))
        base = '/api/exams/' + event['id']
        participant = next(p for p in event['participants'] if p['enrollment_id'] == self.alice)
        sitting = self.tool_write(session, base + '/sittings', {'kind': 'makeup', 'label': 'Makeup', 'time': '14:00',
            'date': (campus_today() + timedelta(days=2)).isoformat(), 'version': event['version'],
            'participants': [{'section_id': section, 'enrollment_id': self.alice, 'version': participant['version']}]})
        first = self.tool_write(session, '/api/mail/send', {**MAIL[1], 'body': 'You are booked for the makeup.'})
        second = self.tool_write(session, '/api/mail/send', {**MAIL[1], 'body': 'One more thing about the makeup.'})
        def card(result):
            return self.assistant._view(*self.assistant.action(result['action_id']))
        self.assertEqual(self.approve(card(sitting)).status_code, 200)
        answer = self.approve(card(first))
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(len(fake.sent), 1)
        made = exams.get(event['id'])['sittings'][0]
        moved = self.client.patch(base + '/sittings/' + made['id'], json={'time': '15:00', 'version': made['version']}, headers=HEADERS)
        self.assertEqual(moved.status_code, 200, moved.json)
        refused = self.approve(card(second))
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn('calendar availability', refused.json['error'].lower())
        self.assertEqual(len(fake.sent), 1)

    def test_preexisting_resolved_followup_keeps_failed_work_visible(self):
        self.suggestions.automatic = True
        self.fake_mail()
        self.incoming()
        meeting_step = (MEETING[0], {'enrollment_id': self.alice, 'kind': 'appointment', 'title': 'Meeting',
                                   'date': '2026-10-12', 'time': '14:00', 'end_time': '16:00'}, MEETING[2])
        run, _ = self.prepare(self.completion(MAIL, meeting_step))
        meeting = next(a for a in run['actions'] if a['endpoint'] == 'student_support.create')
        mail = next(a for a in run['actions'] if a['endpoint'] == 'gmail_mail.send')
        self.assertEqual(self.approve(meeting).status_code, 200)
        self.assertEqual(self.approve(mail).status_code, 200)
        # Retained older runs may already have sent the reply and resolved tracking.
        self.assistant._event(meeting['id'], 'failed', 'faculty', {'status': 503, 'result': {'error': 'Synthetic failure'}})
        self.assertEqual(self.listing()['items'][0]['state'], 'resolved')
        self.assertEqual(self.support.attention()['followup'], 1)

    def test_changed_policy_and_notes_are_warnings_on_the_review(self):
        # WHAT CAN MAKE A STEP WRONG (requirement, 2026-10-08): a policy or note change is shown before Approve, never refused.
        fake = self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        action = run['actions'][0]
        result = self.client.post('/api/student-support/policies/' + self.term,
            json={'content': 'Offer meetings only on Friday.', 'base_revision': 0, 'request_id': 'new-policy'}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.write().status_code, 201)
        shown = self.listing()['items'][0]['suggestions']['context_changes']
        self.assertEqual(shown, {'refuse': [], 'warn': ['term policies', 'student notes']})
        approved = self.approve(action)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(len(fake.sent), 1)

    def test_only_a_change_at_a_time_the_draft_names_refuses_it(self):
        # The reply names "Monday at 2 pm": another day or hour, a gradebook column or a new exam without a paper never refuses it.
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        today = campus_today()
        monday = (today + timedelta(days=(0 - today.weekday()) % 7 or 7)).isoformat()
        def event(title, start, end):
            response = self.client.post('/api/calendar/events', json={'scope': 'workspace', 'category': 'event', 'title': title,
                'date': monday, 'all_day': False, 'time': start, 'end_time': end, 'request_id': str(uuid.uuid4())}, headers=HEADERS)
            self.assertEqual(response.status_code, 201, response.json)
        event('Morning meeting', '09:00', '10:00')
        setup = self.client.post('/api/gradebook/terms/' + self.term + '/setup', json={'base_revision': None, 'request_id': 'setup',
            'setup': {'items': [{'title': 'Quiz', 'points': '10', 'category': 'quiz', 'due_date': '2026-10-10'}]}}, headers=HEADERS)
        self.assertEqual(setup.status_code, 200, setup.json)
        with connect(self.assistant.database) as db:
            definition = db.execute('SELECT definition_id FROM course_terms WHERE id=?', (self.term,)).fetchone()[0]
            section = db.execute('SELECT section_id FROM roster_memberships WHERE enrollment_id=? LIMIT 1', (self.alice,)).fetchone()[0]
        assignment = self.app.extensions['documents'].create_assignment(definition, 'exam-1', 'Exam 1')
        self.app.extensions['frq_grading'].exams.create({'term_id': self.term, 'assignment_id': assignment['id'], 'section_ids': [section]})
        self.assertEqual(self.listing()['items'][0]['suggestions']['context_changes'], {'refuse': [], 'warn': ['grades or due dates']})
        event('Department meeting', '14:30', '15:30')
        self.assertEqual(self.listing()['items'][0]['suggestions']['context_changes']['refuse'], ['calendar availability'])
        refused = self.approve(run['actions'][0])
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn('calendar availability', refused.json['error'].lower())

    def test_own_note_does_not_stale_next_step(self):
        fake = self.fake_mail()
        self.incoming()
        note = ('/api/student-notes', {'enrollment_id': self.alice, 'date': '2026-10-08', 'text': 'Meet on Monday.'}, 'Record the meeting context')
        run, _ = self.prepare(proposing(MAIL, note))
        result = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/approve",
            json={'seen': {a['id']: a['seen'] for a in run['actions']}}, headers=HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertFalse(result.json['remaining'], result.json)
        self.assertEqual(len(fake.sent), 1)

    def test_calendar_changes_block_old_drafts_but_exact_own_events_do_not(self):
        fake = self.fake_mail()
        self.incoming()
        calendar = ('/api/calendar/events', {'scope': 'workspace', 'category': 'event', 'title': 'Office unavailable',
            'date': campus_today().isoformat(), 'all_day': False, 'time': '14:00', 'end_time': '15:00'}, 'Block unavailable time')
        run, _ = self.prepare(proposing(MAIL, calendar))
        event, mail = run['actions']
        self.assertEqual(self.approve(event).status_code, 200)
        with connect(self.assistant.database) as db:
            self.assertEqual(self.suggestions.context.changes(db, self.suggestions.get(db, run['id'])), {'refuse': [], 'warn': []})
        # An instructor change after the plan's own write still invalidates it.
        response = self.client.post('/api/calendar/events', json={**calendar[1], 'title': 'A new conflict',
            'request_id': str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        refused = self.approve(mail)
        self.assertEqual(refused.status_code, 409, refused.json)
        self.assertIn('calendar availability', refused.json['error'].lower())
        self.assertEqual(fake.sent, [])

    def test_revising_an_own_appointment_cannot_bypass_context_check(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL, MEETING))
        appointment, mail = run['actions']
        saved = self.approve(appointment)
        self.assertEqual(saved.status_code, 200, saved.json)
        result = saved.json['result']
        changed = self.client.post('/api/student-support/' + result['id'], json={'base_revision': result['revision'],
            'title': 'Different time', 'date': '2026-10-12', 'time': '16:00', 'end_time': '16:30', 'request_id': 'moved'}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(self.approve(mail).status_code, 409)

    def test_removing_watch_during_observation_revokes_preparation(self):
        self.incoming()
        watch = self.watch()
        observe = self.suggestions.watches.observe
        def cancelled(*args):
            result = self.suggestions.watches.update(watch['id'], {'state': 'removed', 'revision': 1, 'request_id': 'cancel'})
            self.assertEqual(result['state'], 'removed')
            return observe(*args)
        with patch.object(self.suggestions.watches, 'observe', side_effect=cancelled):
            self.assertTrue(self.suggestions.watches.process_one())
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_suggestion_runs').fetchone()[0], 0)

    def test_watch_never_retries_a_failed_preparation(self):
        self.incoming()
        def fail(c, prompt):
            raise RuntimeError('Synthetic interrupted preparation')
        run, _ = self.prepare(fail)
        self.watch()
        self.assertTrue(self.suggestions.watches.process_one())
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_suggestion_runs').fetchone()[0], 1)
        shown = self.listing()['items'][0]['suggestions']
        self.assertEqual(shown['id'], run['id'])
        self.assertTrue(shown['watch_attention'])

    def watch(self, kind='date', target=None, **changes):
        body = {'enrollment_id': self.alice, 'item_key': 'conversation:gmail:b1', 'kind': kind,
                'target': target or {}, 'check_back': campus_today().isoformat(), **changes}
        preview = self.client.post('/api/student-support/followups/watches/preview', json=body, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        result = self.client.post('/api/student-support/followups/watches', json={**body, 'fingerprint': preview.json['fingerprint'],
            'request_id': uuid.uuid4().hex}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        return result.json

    def test_date_watch_is_once_only_and_never_sends(self):
        fake = self.fake_mail()
        self.incoming()
        watch = self.watch()
        self.assertTrue(self.suggestions.watches.process_one())
        self.assertFalse(self.suggestions.watches.process_one())
        with connect(self.assistant.database) as db:
            row = db.execute('SELECT state FROM student_support_watches WHERE id=?', (watch['id'],)).fetchone()
            self.assertEqual(row[0], 'triggered')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_suggestion_runs').fetchone()[0], 1)
        self.assertEqual(fake.sent, [])

    def test_assistant_watch_requires_an_exact_approval(self):
        self.incoming()
        session = self.assistant.open_session('scripted', 'Watch request')
        result, _ = self.assistant.run_tool(session, 'watch_followup', {'enrollment_id': self.alice,
            'item_key': 'conversation:gmail:b1', 'kind': 'date', 'check_back': campus_today().isoformat()})
        result = json.loads(result)
        self.assertIn('action_id', result, result)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_watches').fetchone()[0], 0)
        action = self.assistant._view(*self.assistant.action(result['action_id']))
        self.assertEqual(action['tier'], 'approve')
        self.assertEqual(self.approve(action).status_code, 200)
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_watches').fetchone()[0], 1)
        from assistant import policy
        self.assertEqual(policy.base_rule('student_support.watch_update').tier, 'never')

    def test_paused_and_removed_watch_do_not_prepare(self):
        self.incoming()
        watch = self.watch()
        route = '/api/student-support/followups/watches/' + watch['id']
        for version, state in enumerate(('paused', 'removed'), 1):
            result = self.client.post(route, json={'state': state, 'revision': version, 'request_id': uuid.uuid4().hex}, headers=HEADERS)
            self.assertEqual(result.status_code, 200, result.json)
            self.assertFalse(self.suggestions.watches.process_one())

    def test_watch_detects_absence_document_or_grade_change(self):
        self.incoming()
        # Exercise the worker state machine against each owner projection. No
        # live gradebook, file, mailbox or service is used.
        for kind in ('absence', 'grade', 'paper'):
            with self.subTest(kind=kind):
                with patch.object(self.suggestions.watches, 'observe', return_value={'value': 'before', 'ready': False}):
                    watch = self.watch(kind, {'id': 'synthetic'}, check_back='')
                    self.assertTrue(self.suggestions.watches.process_one())
                with self.rosters_lock(), connect(self.assistant.database) as db:
                    db.execute('UPDATE student_support_watches SET next_check=0 WHERE id=?', (watch['id'],))
                with patch.object(self.suggestions.watches, 'observe', return_value={'value': 'after', 'ready': kind == 'paper'}):
                    self.assertTrue(self.suggestions.watches.process_one())
                with connect(self.assistant.database) as db:
                    self.assertEqual(db.execute('SELECT state FROM student_support_watches WHERE id=?', (watch['id'],)).fetchone()[0], 'triggered')

    def test_watch_failure_needs_attention_and_does_not_retry(self):
        self.incoming()
        self.watch()
        with patch.object(self.suggestions.watches, 'observe', side_effect=ValueError('synthetic')):
            self.assertTrue(self.suggestions.watches.process_one())
        self.assertFalse(self.suggestions.watches.process_one())
        self.assertTrue(self.listing()['items'][0]['suggestions']['watch_attention'])
        self.assertEqual(self.support.attention()['followup'], 1)

    def test_case_context_is_student_scoped_and_typed_reply_requires_send(self):
        fake = self.fake_mail()
        from test_gmail_mail import message
        fake.threads['b1'] = [message('a1', 'alice@example.test', ACCOUNT, 'Question', 'Could I meet you?',
                                      extra=[('Message-ID', '<a1@example.test>')])]
        self.incoming()
        self.write(text='Private note about Alice.')
        self.write(enrollment=self.bob, request_id='bob', text='Bob-only private note.')
        session = self.assistant.open_session('scripted', 'Case context')
        result, _ = self.assistant.run_tool(session, 'student_context', {'enrollment_id': self.alice, 'source_key': 'gmail:b1'})
        context = json.loads(result)
        self.assertIn('Private note about Alice', json.dumps(context))
        self.assertNotIn('Bob-only', json.dumps(context))
        self.assertEqual(context['reply']['addresses'], ['alice@example.test'])
        result, _ = self.assistant.run_tool(session, 'draft_student_reply', {'enrollment_id': self.alice, 'source_key': 'gmail:b1',
            'subject': 'Re: Question', 'body': 'Please bring the example to office hours.'})
        action = json.loads(result)
        self.assertIn('action_id', action, action)
        row, _ = self.assistant.action(action['action_id'])
        self.assertEqual(row['tier'], 'approve')
        self.assertEqual(json.loads(row['body_json'])['reply_thread_id'], 'b1')

    def test_repeated_edits_only_propose_term_guidance(self):
        self.fake_mail()
        wording = 'Please use the scheduled office hours for these questions.'
        for index, student in enumerate((self.alice, self.bob)):
            thread = 'b1' if index == 0 else 'b2'
            self.incoming(ident='incoming' + str(index), thread=thread, enrollment=student)
            # Existing Alice draft is retained; select this student's new run.
            self.assertTrue(self.suggestions.process_one())
            from test_followup_suggestions import ScriptedEngine
            mail = (MAIL[0], {**MAIL[1], 'to': ['alice@example.test' if index == 0 else 'bob@example.test']}, MAIL[2])
            with patch('assistant.conversations.engine_for', return_value=ScriptedEngine(proposing(mail))):
                self.assertTrue(self.suggestions.dispatch_one())
                self.wait()
            run = next(i['suggestions'] for i in self.listing()['items'] if i['enrollment_id'] == student)
            action = run['actions'][0]
            changed = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={
                'action_id': action['id'], 'seen': action['seen'], 'subject': 'Re: Question', 'body': wording,
                'request_id': uuid.uuid4().hex}, headers=HEADERS)
            self.assertEqual(changed.status_code, 200, changed.json)
            terms = self.client.get('/api/student-support/policies?areas=1').json['terms']
            candidates = next(t['suggested_guidance'] for t in terms if t['term_id'] == self.term)
            self.assertEqual(len(candidates), index)
        self.assertEqual(candidates[0]['wording'], wording)
        self.assertEqual(candidates[0]['cases'], 2)
        self.assertEqual(next(t['revision'] for t in terms if t['term_id'] == self.term), 0)

    def test_approve_and_edit_both_wait_for_the_preparation_and_the_warning_stays_on_its_card(self):
        # 2026-10-09 review: an Edit made while a preparation ran was withdrawn by the draft check at the end of the turn. Both the
        # instructor's Approve and Edit now wait; the warning left after the one repair names the mail card it concerns.
        self.fake_mail()
        self.incoming(body='I cannot come on Monday. Is Tuesday possible?')
        self.assertTrue(self.suggestions.process_one())
        answers = []
        def generate(c, prompt):
            result = self.tool_write(c.session, '/api/mail/send', {'to': ['alice@example.test'], 'subject': 'Meeting', 'body': 'Come on Monday.'})
            action = self.assistant._view(*self.assistant.action(result['action_id']))
            answers.append(self.approve(action).status_code)
            answers.append(self.client.post(f"/api/student-support/followups/suggestions/{c.run['id']}/edit", json={'action_id': action['id'],
                'seen': action['seen'], 'subject': 'Meeting', 'body': 'Tuesday works.', 'request_id': uuid.uuid4().hex}, headers=HEADERS).status_code)
            c.emit('assistant', text='Review the reply.')
        from test_followup_suggestions import ScriptedEngine
        from test_jev import response
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=lambda state, qs:
                response(qs, nouls={'contradicts_request': .99 if 'draft' in state else .0})), \
                patch('assistant.conversations.engine_for', return_value=ScriptedEngine(generate)):
            self.assertTrue(self.suggestions.dispatch_one())
            self.wait()
        self.assertEqual(answers[:2], [409, 409])
        run = self.listing()['items'][0]['suggestions']
        waiting = [a for a in run['actions'] if a['pending'] and a['endpoint'] == 'gmail_mail.send']
        self.assertEqual(len(waiting), 1)
        self.assertEqual([w['action_id'] for w in run['draft_review']['warnings']], [waiting[0]['id']])
        edited = self.client.post(f"/api/student-support/followups/suggestions/{run['id']}/edit", json={'action_id': waiting[0]['id'],
            'seen': waiting[0]['seen'], 'subject': 'Meeting', 'body': 'Tuesday at 2 works.', 'request_id': uuid.uuid4().hex}, headers=HEADERS)
        self.assertEqual(edited.status_code, 200, edited.json)
        after = self.listing()['items'][0]['suggestions']
        mail = next(a for a in after['actions'] if a['pending'])
        self.assertEqual(mail['id'], edited.json['action_id'])
        self.assertNotIn(mail['id'], [w['action_id'] for w in after['draft_review']['warnings']])  # the card's warning left with it


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(CompletionTests(name) for name in CompletionTests.__dict__ if name.startswith('test_'))
