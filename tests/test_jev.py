"""JEV: synthetic decisions, exact source ownership and fail-open work visibility."""
import json
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

import io
from urllib.error import HTTPError, URLError

from communications.record import record
from jev import client
from jev.checks import absence, evidence, relative_dates
from jev.service import choice, noul
from rosters import connect
from student_support import triage as triage_module
from student_support.triage import QUESTIONS, RULE, snapshots
from test_student_notes import Base
from test_assistant import settle
from test_storage import HEADERS
from assistant.conversations import Conversation
from absences.drafting import EmailDraft


def response(questions, choices=None, nouls=None, scores=None):
    """Scripted answers: positive predicates default to .99, concerns to .0, scores to lowest, choices to first."""
    choices, nouls, scores = choices or {}, nouls or {}, scores or {}
    answers = {}
    for key, question in questions.items():
        if question['type'] == 'noul':
            answers[key] = {'type': 'noul', 'noul': nouls.get(key, .0 if key in ('wellbeing', 'contradicts_request', 'unsupported_promise',
                                                                             'more_context', 'unconfirmed_claim') else .99)}
        elif question['type'] == 'score':
            level = scores.get(key, 0)
            answers[key] = {'type': 'score', 'score': float(level), 'confidence': 1.0,
                            'probabilities': {str(i): 1.0 if i == level else 0.0 for i in range(len(question['criteria']))}}
        else:
            selected = choices.get(key, next(iter(question['criteria'])))
            probabilities = {k: 1.0 if k == selected else 0.0 for k in question['criteria']}
            answers[key] = {'type': 'choice', 'choice': selected, 'probabilities': probabilities, 'confidence': 1.0}
    return {'model': client.MODEL, 'answers': answers, 'usage': {'input_tokens': 123, 'output_tokens': 12}}


def reply_json(payload, status=200):
    answer = Mock(status=status)
    answer.read = Mock(return_value=json.dumps(payload).encode())
    answer.__enter__ = Mock(return_value=answer)
    answer.__exit__ = Mock(return_value=False)
    return answer


class ProviderTests(unittest.TestCase):
    def test_fixed_host_no_redirect_and_malformed_distributions_refused(self):
        questions = {'kind': choice('Choose one.', {'a': 'A', 'b': 'B'}), 'yes': noul('Is it true?')}
        valid = response(questions)
        self.assertEqual(client.validate(valid, questions)['answers'], valid['answers'])
        for bad in ({**valid, 'model': 'jev-latest'}, {**valid, 'answers': {}}, {**valid, 'usage': {}},
                    {**valid, 'answers': {**valid['answers'], 'yes': {'type': 'noul', 'noul': float('nan')}}},
                    {**valid, 'answers': {**valid['answers'], 'kind': {**valid['answers']['kind'], 'confidence': .5}}}):
            with self.subTest(bad=bad), self.assertRaises(client.DecisionError):
                client.validate(bad, questions)
        # Real answers are rounded to two decimals (2026-10-03: sum .99 over 55 options, confidence .015 from the formula).
        many = {'pick': choice('Choose one.', {f'o{i}': None for i in range(55)})}
        rounded = {**valid, 'answers': {'pick': {'type': 'choice', 'choice': 'o0', 'confidence': .69,
                   'probabilities': {'o0': .71, 'o1': .2, 'o2': .08, **{f'o{i}': 0.0 for i in range(3, 55)}}}}}
        self.assertEqual(client.validate(rounded, many)['answers']['pick']['choice'], 'o0')
        for bad in ({'o0': .5, 'o1': .2, **{f'o{i}': 0.0 for i in range(2, 55)}},  # sums to .7
                    {'o0': .2, 'o1': .71, 'o2': .08, **{f'o{i}': 0.0 for i in range(3, 55)}}):  # not the most likely
            with self.subTest(bad=bad), self.assertRaises(client.DecisionError):
                client.validate({**rounded, 'answers': {'pick': {**rounded['answers']['pick'], 'probabilities': bad}}}, many)
        reply = Mock(status=302)
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        with patch.dict('os.environ', {'TYPESAFE_API_KEY': 'synthetic-secret', 'FACULTY_JEV_ENABLED': '1'}), patch('jev.client.open_request', return_value=reply) as post:
            with self.assertRaises(client.DecisionError) as error:
                client.evaluate('state', questions)
            self.assertNotIn('synthetic-secret', str(error.exception))
            self.assertEqual(post.call_args.args[0].full_url, 'https://api.typesafe.ai/v1/systemone')
            self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))
            self.assertEqual(post.call_count, 1)

    def test_network_uncertainty_never_retries_or_exposes_host_exception(self):
        with patch('jev.client.available', return_value=True), patch.dict('os.environ', {'TYPESAFE_API_KEY': 'fake'}), patch('jev.client.open_request', side_effect=URLError('private text and key')) as post:
            with self.assertRaises(client.DecisionError) as error:
                client.evaluate('state', {'q': noul('Question')})
            self.assertTrue(error.exception.uncertain)
            self.assertNotIn('private text', str(error.exception))
            self.assertEqual(post.call_count, 1)

    def test_host_level_failures_are_flagged_and_a_refused_connection_sent_nothing(self):
        # JEV HOST FAILURES (2026-10-09): Jev's transport reads hosts as OpenAI Decisions' does.
        from urllib.error import HTTPError
        cases = ((HTTPError(client.URL, 429, 'No', {}, io.BytesIO(b'key')), (False, True, True)),
                 (HTTPError(client.URL, 422, 'No', {}, io.BytesIO(b'')), (False, False, False)),
                 (URLError(ConnectionRefusedError()), (False, True, True)), (TimeoutError(), (True, True, False)))
        with patch('jev.client.available', return_value=True), patch.dict('os.environ', {'TYPESAFE_API_KEY': 'fake'}):
            for outcome, flags in cases:
                with self.subTest(outcome=type(outcome).__name__), patch('jev.client.open_request', side_effect=outcome), \
                        self.assertRaises(client.DecisionError) as error:
                    client.evaluate('state', {'q': noul('Question')})
                self.assertEqual((error.exception.uncertain, error.exception.host, error.exception.refused), flags)
        with self.assertRaises(client.DecisionError) as error:
            client.validate({**response({'q': noul('Q')}), 'model': 'jev-1.14.0'}, {'q': noul('Q')})
        self.assertTrue(error.exception.host)

    def test_relative_dates_use_the_sent_campus_day_and_do_not_guess_weekdays(self):
        source = {'sent_at_campus': '2026-10-03T01:15:00+00:00', 'email_text': 'I missed today, tomorrow, and next Monday.'}
        values = relative_dates(source)
        self.assertEqual(values['today'], 'Friday, October 02, 2026')
        self.assertEqual(values['tomorrow'], 'Saturday, October 03, 2026')
        self.assertTrue(values['next monday']['ambiguous'])
        self.assertEqual(values['next monday']['candidates'], ['Monday, October 05, 2026', 'Monday, October 12, 2026'])
        self.assertEqual(relative_dates({**source, 'sent_at_campus': ''}), {})

    def test_a_bare_weekday_offers_the_one_just_past_as_well_as_the_coming_one(self):
        # RELATIVE WEEKDAYS (2026-10-07): "I missed class on Monday", sent Friday October 2, means Monday September 28; before,
        # only October 5 and 12 were offered, and the record check confirmed an absence for the wrong week.
        sent = '2026-10-03T01:15:00+00:00'  # Friday, October 2 on campus
        values = relative_dates({'sent_at_campus': sent, 'email_text': 'I missed class on Monday, last Tuesday and this past Wednesday, '
                                                                        'and I will be out Friday and next Monday.'})
        self.assertEqual(values['monday'], {'ambiguous': True, 'candidates': ['Monday, September 28, 2026', 'Monday, October 05, 2026']})
        self.assertEqual(values['last tuesday'], {'ambiguous': True, 'candidates': ['Tuesday, September 22, 2026', 'Tuesday, September 29, 2026']})
        self.assertEqual(values['past wednesday'], 'Wednesday, September 30, 2026')
        self.assertEqual(values['friday'], {'ambiguous': True, 'candidates': ['Friday, October 02, 2026', 'Friday, October 09, 2026']})
        self.assertEqual(values['next monday'], {'ambiguous': True, 'candidates': ['Monday, October 05, 2026', 'Monday, October 12, 2026']})
        self.assertEqual(relative_dates({'sent_at_campus': sent, 'email_text': 'See you this Monday or this Friday.'}),
                         {'this monday': 'Monday, September 28, 2026', 'this friday': 'Friday, October 02, 2026'})


class JevTests(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(settle)  # a turn's thread saves after it stops being busy: it has ended before the folder is removed
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        self.support = self.app.extensions['student_support']
        self.triage = self.support.triage
        self.jev = self.app.extensions['jev']

    def incoming(self, ident='a1', body='Thanks, that answers my question.', at='2026-10-03T12:00:00Z', thread='abc', author='student'):
        with connect(self.app.config['DATABASE']) as db:
            record(db, 'gmail', ident, {'sent_at': at, 'author_role': author, 'author': {}, 'recipients': [],
                   'subject': 'Office hours', 'body_text': body, 'attachments': [], 'context': {'thread_id': thread}},
                   [(self.alice, self.term, 'author' if author == 'student' else 'to', 'roster_address')], thread)
            db.commit()

    def analyze(self, kind='acknowledgment', **nouls):
        self.triage._last = 0
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, {'kind': kind}, {'needs_action': .0, **nouls})) as evaluate:
            worked = self.triage.process_one()
        return worked, evaluate

    def items(self):
        return self.client.get('/api/student-support?kind=followup').json['items']

    def test_acknowledgment_goes_to_fyi_but_manual_tracking_stays_authoritative(self):
        self.incoming()
        self.assertTrue(self.items()[0]['attention'])
        worked, evaluate = self.analyze()
        self.assertTrue(worked)
        item = self.items()[0]
        self.assertEqual((item['state'], item['attention']), ('fyi', False))
        self.assertEqual(item['triage']['mail'], {'thread_id': 'abc', 'message_id': 'a1'})
        self.assertEqual(self.support.attention()['followup'], 0)
        with connect(self.jev.path) as db:
            saved = dict(db.execute('SELECT * FROM jev_calls').fetchone())
            self.assertNotIn('Thanks, that answers', json.dumps(saved))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM student_support_items').fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE jev_calls SET status='failed'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM jev_calls')
        created = self.client.post('/api/student-support', json={**item, 'state': 'needs_reply', 'request_id': 'track'}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        self.assertTrue(self.items()[0]['attention'])
        self.triage._last = 0
        with patch('jev.client.evaluate') as repeated:
            self.assertFalse(self.triage.process_one())
            repeated.assert_not_called()

    def test_triage_can_ask_openai_decisions_with_its_own_receipts_and_gates(self):
        # JEV TRIAGE PROVIDER (2026-10-08): with FACULTY_TRIAGE_PROVIDER=decisions triage asks OpenAI Decisions, receipts carry its
        # model and are read through its own gates; SAME MATTER and every other check keep asking Jev.
        import openai_decisions
        from jev.service import provider_for
        self.incoming()

        def decided(state, qs):  # needs-action .04: FYI at Decisions' calibrated .05, never at Jev's .02
            return {**response(qs, {'kind': 'acknowledgment'}, {'needs_action': .04}), 'model': openai_decisions.MODEL}
        on = patch.dict('os.environ', {'FACULTY_TRIAGE_PROVIDER': 'decisions'})
        with on, patch('openai_decisions.available', return_value=True):
            self.assertIs(provider_for('student-triage'), openai_decisions)
            self.assertIs(provider_for('followup-match'), client)
            self.triage._last = 0
            with patch('openai_decisions.evaluate', side_effect=decided) as asked, patch('jev.client.evaluate') as jev:
                self.assertTrue(self.triage.process_one())
            asked.assert_called_once()
            jev.assert_not_called()
            item = self.items()[0]
            self.assertEqual((item['state'], item['triage']['kind'], item['attention']), ('fyi', 'acknowledgment', False))
            self.assertEqual(self.client.get('/api/jev/status').json['triage'], {'available': True, 'model': 'gpt-6-luna'})
            with patch.dict('student_support.triage.GATES', {'gpt-6-luna': triage_module.GATES[client.MODEL]}):
                self.assertEqual((self.items()[0]['state'], self.items()[0]['attention']), ('needs_reply', True))  # Jev's gates: not FYI
        with connect(self.jev.path) as db:
            self.assertEqual(tuple(db.execute('SELECT purpose,model,status FROM jev_calls').fetchone()), ('student-triage', 'gpt-6-luna', 'complete'))
        self.assertNotIn('triage', self.client.get('/api/jev/status').json)  # on Jev the status reads as before
        self.assertEqual(self.items()[0]['triage']['status'], 'pending')  # Jev's own reading is looked for: none yet
        with on, patch('openai_decisions.available', return_value=False):
            self.assertEqual(self.items()[0]['attention'], True)  # Decisions off: the existing attention rule, no analysis
            self.assertNotIn('triage', self.items()[0])

    def test_embedded_request_or_missing_context_cannot_be_suppressed(self):
        self.incoming(body='Thanks, but can I take the makeup Friday?')
        self.analyze(kind='request', needs_action=.99, makeup=.99, absence=.01, office_hours=.01, grading=.01, materials=.01)
        self.assertEqual(self.items()[0]['triage']['requests'], ['makeup'])
        self.assertTrue(self.items()[0]['attention'])
        # Even a mistaken acknowledgment cannot hide an independently detected action.
        self.incoming('a2', body='Thanks, but please send the missing notes.', at='2026-10-03T13:00:00Z')
        self.analyze(needs_action=.9)
        self.assertTrue(self.items()[0]['attention'])
        for index in range(7):
            self.incoming(f'b{index}', at=f'2026-10-03T14:0{index}:00Z')
        self.analyze()
        self.assertTrue(self.items()[0]['triage']['context_incomplete'])
        self.assertTrue(self.items()[0]['attention'])

    def test_edits_and_new_replies_invalidate_old_results_and_reopen_manual_items(self):
        self.incoming()
        self.analyze()
        first = self.items()[0]
        self.incoming(body='Actually, I need a reply to this new question.')
        self.assertTrue(self.items()[0]['attention'])
        self.assertEqual(self.items()[0]['triage']['status'], 'pending')
        self.assertNotEqual(first['triage']['fingerprint'], self.items()[0]['triage']['fingerprint'])
        self.analyze(kind='request', needs_action=.99)
        current = self.items()[0]
        self.client.post('/api/student-support', json={**current, 'state': 'resolved', 'request_id': 'resolve'}, headers=HEADERS)
        self.incoming('a2', at='2026-10-03T13:00:00Z')
        self.assertTrue(self.items()[0]['new_reply'])  # reopened until Jev has read the new reply
        self.assertEqual(self.support.settled_mail([(self.alice, 'a2')]), set())  # MAIL-FOLLOW-UP: so its mail stays in Students
        self.analyze()
        # A THANKS ON A TRACKED FOLLOW-UP (2026-10-07): a strong thanks leaves a resolved follow-up resolved (a view; nothing saved).
        item = self.items()[0]
        self.assertEqual((item['state'], item['saved_state'], item['attention'], item['new_reply']), ('resolved', 'resolved', False, False))
        self.assertEqual(item['thanks']['message_id'], item['observed'][1])
        self.assertEqual(self.support.settled_mail([(self.alice, 'a2')]), {'a2'})  # MAIL-FOLLOW-UP: the thanks leaves Students with it
        # Jordan Zahn's "Sounds great, thank you!" (2026-10-07): a needs-action answer of .31 (a drop-in planned) and a thread longer
        # than six messages do not reopen it. JEV WELLBEING OFF (requirement, 2026-10-12): neither does a high
        # wellbeing answer; while the check was on (the gate at .30) it did.
        for index in range(6):
            self.incoming(f'c{index}', body='Sounds great, thank you!', at=f'2026-10-03T13:1{index}:00Z')
        self.analyze(needs_action=.31)
        item = self.items()[0]
        self.assertTrue(item['triage']['context_incomplete'])
        self.assertEqual((item['state'], item['attention'], item['new_reply']), ('resolved', False, False))
        self.incoming('c6', body='Thank you for understanding.', at='2026-10-03T13:30:00Z')
        self.analyze(wellbeing=.5)
        self.assertEqual((self.items()[0]['state'], self.items()[0]['new_reply']), ('resolved', False))
        with patch.dict(triage_module.GATES[client.MODEL], {'wellbeing': .30}):
            self.assertEqual((self.items()[0]['state'], self.items()[0]['new_reply']), ('needs_reply', True))
            self.assertEqual(self.support.settled_mail([(self.alice, 'c6')]), set())  # MAIL-FOLLOW-UP: a reopening message is never settled
        self.assertEqual(self.support.settled_mail([(self.alice, 'c6')]), {'c6'})  # the thanks leaves Students with its follow-up
        self.incoming('a3', body='Thanks, but when is the makeup?', at='2026-10-03T14:00:00Z')
        self.analyze(kind='request', needs_action=.9)
        self.assertTrue(self.items()[0]['attention'])  # anything weaker than that reopens it, as ever
        self.assertTrue(self.items()[0]['new_reply'])

    def test_a_thanks_stays_a_thanks_while_a_re_read_waits(self):
        # READ BEFORE (2026-10-16, Rina Akhtar's "okay thank you!" reopened while its re-read after a due-date change waited).
        self.incoming(body='Can I come to office hours?')
        self.analyze(kind='request', needs_action=.99)
        current = self.items()[0]
        self.client.post('/api/student-support', json={**current, 'state': 'resolved', 'request_id': 'resolve'}, headers=HEADERS)
        self.incoming('a2', body='okay thank you!', at='2026-10-03T13:00:00Z')
        self.analyze()
        read = self.items()[0]
        self.assertEqual((read['state'], read['new_reply']), ('resolved', False))
        moved = [{'title': 'WebAssign 4.1', 'due': '2026-10-09'}]
        with patch('student_support.triage.upcoming', side_effect=lambda db, sent: {key: moved for key in sent}):
            item = self.items()[0]
            self.assertNotEqual(item['triage']['fingerprint'], read['triage']['fingerprint'])  # a re-read is owed
            self.assertEqual((item['state'], item['new_reply'], item['attention']), ('resolved', False, False))
            self.assertEqual((item['triage']['id'], item['triage']['kind']), (read['triage']['id'], 'acknowledgment'))
            self.assertEqual(item['thanks']['message_id'], read['observed'][1])
            self.analyze(kind='request', needs_action=.99)  # the re-read decides once it is made
            self.assertEqual((self.items()[0]['state'], self.items()[0]['new_reply']), ('needs_reply', True))
        # A new student message is read before anything is carried.
        self.incoming('a3', body='thanks again', at='2026-10-03T14:00:00Z')
        item = self.items()[0]
        self.assertEqual((item['triage']['status'], item['state'], item['new_reply']), ('pending', 'needs_reply', True))

    def test_a_listing_reads_the_receipts_once_and_finds_what_each_lookup_would(self):
        # PERFORMANCE (2026-11-04): one scan of jev_calls per conversation was 2.1 s of a 3.7 s Follow-up read; the page's own read gave
        # up behind it while an Approve ran.
        self.incoming(body='Can I come to office hours?')
        self.analyze(kind='request', needs_action=.99)
        with patch.object(self.triage, 'lookup', wraps=self.triage.lookup) as lookup:
            items = self.items()
        lookup.assert_not_called()
        item = items[0]
        self.assertEqual(item['triage']['status'], 'complete')
        owner = self.triage.owner(item['enrollment_id'], item['source_key'])
        with connect(self.app.config['DATABASE']) as db:
            newest = lambda: self.triage.newest(db)[(owner, item['triage']['fingerprint'])]['id']
            self.assertEqual(newest(), item['triage']['id'])
            def later(ident, result):
                db.execute('''INSERT INTO jev_calls (id,call_key,purpose,owner_id,enrollment_id,input_sha256,questions_json,model,rule,status,result_json,created_at)
                    SELECT ?,?,purpose,owner_id,enrollment_id,input_sha256,questions_json,model,rule,status,?,created_at FROM jev_calls WHERE id=?''',
                    (ident, ident, result, item['triage']['id']))
            later('refused-later', '{"refused": true}')  # a host's refusal decided nothing, as in `lookup`
            self.assertEqual(newest(), item['triage']['id'])
            later('complete-later', '{}')  # the newest receipt of a state stands, as in `lookup`
            self.assertEqual(newest(), 'complete-later')
            db.rollback()

    def test_cutoff_and_exact_thread_identity_control_eligibility(self):
        self.incoming('old', at='2026-10-01T12:00:00Z')
        preview = self.client.post('/api/student-support/followups/start-now/preview', json={'through': '2026-10-02T12:00:00Z'}, headers=HEADERS).json
        self.client.post('/api/student-support/followups/start-now', json={**preview, 'confirmation': 'start-now', 'request_id': 'baseline'}, headers=HEADERS)
        worked, evaluate = self.analyze()
        self.assertFalse(worked)
        evaluate.assert_not_called()
        self.incoming('new', thread='def')
        self.analyze()
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.items()[0]['source_key'], 'gmail:def')
        self.incoming('later-captured-old', at='2026-10-01T13:00:00Z', thread='123')
        self.assertEqual(len(self.items()), 1)

    def test_capture_during_a_read_does_not_apply_a_new_verdict_to_an_old_item(self):
        self.incoming()
        old_items = self.items()
        with connect(self.jev.path) as db:
            from student_support.service import conversations
            old_threads = conversations(db)
        self.incoming('a2', at='2026-10-03T13:00:00Z')
        self.analyze()
        with connect(self.jev.path) as db:
            with patch('student_support.triage.conversations', return_value=old_threads):
                self.assertEqual(snapshots(db), {})
            self.triage.apply(db, old_items)
        self.assertTrue(old_items[0]['attention'])
        self.assertFalse(self.items()[0]['attention'])

    def test_old_tracked_items_remain_resolved_without_an_impossible_analysis_retry(self):
        self.incoming(at='2026-10-01T12:00:00Z')
        item = self.items()[0]
        with patch('student_support.service.timestamp', return_value='2026-10-01T13:00:00+00:00'):
            self.assertEqual(self.client.post('/api/student-support', json={**item, 'state': 'needs_reply', 'request_id': 'track-old'}, headers=HEADERS).status_code, 201)
        preview = self.client.post('/api/student-support/followups/start-now/preview', json={'through': '2026-10-02T12:00:00Z'}, headers=HEADERS).json
        self.assertEqual(preview['tracked'], 1)
        self.assertEqual(self.client.post('/api/student-support/followups/start-now', json={**preview, 'confirmation': 'start-now', 'request_id': 'reset-old'}, headers=HEADERS).status_code, 200)
        old = self.items()[0]
        self.assertEqual(old['state'], 'resolved')
        self.assertNotIn('triage', old)
        worked, evaluate = self.analyze()
        self.assertFalse(worked)
        evaluate.assert_not_called()

    def test_failure_visibility_explicit_retry_idempotency_and_stale_retry(self):
        self.incoming()
        with patch('jev.client.evaluate', side_effect=client.DecisionError('Uncertain', uncertain=True)) as evaluate:
            self.triage._last = 0
            self.assertTrue(self.triage.process_one())
            self.triage._last = 0
            self.assertFalse(self.triage.process_one())
            self.assertEqual(evaluate.call_count, 1)
        item = self.items()[0]
        self.assertTrue(item['attention'])
        self.assertEqual(item['triage']['status'], 'uncertain')
        body = {'enrollment_id': self.alice, 'source_key': item['source_key'], 'fingerprint': item['triage']['fingerprint'], 'request_id': 'retry'}
        self.assertEqual(self.client.post('/api/jev/triage/retry', json=body).status_code, 403)
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, {'kind': 'acknowledgment'}, {'needs_action': 0})) as evaluate:
            first = self.client.post('/api/jev/triage/retry', json=body, headers=HEADERS)
            again = self.client.post('/api/jev/triage/retry', json=body, headers=HEADERS)
            self.assertEqual(first.json, again.json)
            self.assertEqual(evaluate.call_count, 1)
        self.assertFalse(self.items()[0]['attention'])
        self.incoming('a2', at='2026-10-03T13:00:00Z')
        self.assertEqual(self.client.post('/api/jev/triage/retry', json=body, headers=HEADERS).status_code, 409)
        from assistant.policy import base_rule
        self.assertEqual(base_rule('jev.retry').tier, 'approve')

    def decisions_on(self):
        """Triage on OpenAI Decisions (JEV TRIAGE PROVIDER), available; Jev stays available for every other check."""
        patch.dict('os.environ', {'FACULTY_TRIAGE_PROVIDER': 'decisions', 'OPENAI_API_KEY': 'synthetic-key'}).start()
        patch('openai_decisions.available', return_value=True).start()

    def turn(self, **evaluate):
        """One worker turn of the triage read alone (SAME MATTER, Jev's, has its own test below)."""
        self.triage._last = 0
        with patch('openai_decisions.evaluate', **evaluate) as asked, patch('jev.client.evaluate') as jev, \
                patch.object(self.triage.match, 'process_one', return_value=False):
            worked = self.triage.process_one()
        jev.assert_not_called()
        return worked, asked

    def test_an_outage_pauses_triage_without_burning_any_conversation(self):
        # JEV HOST FAILURES (2026-10-09): during a Decisions outage every arriving conversation got a permanent failed receipt.
        import openai_decisions
        from datetime import datetime, timedelta, timezone
        self.decisions_on()
        for ident, thread in (('a1', 'abc'), ('b1', 'def'), ('c1', 'ghi')):
            self.incoming(ident, body='My brother is in the ICU and I will miss class.', thread=thread, at=f'2026-10-03T12:0{len(thread)}:00Z')
        outage = HTTPError(openai_decisions.URL, 429, 'Too Many Requests', {}, io.BytesIO(b'{"error": {"message": "sk-...abcd"}}'))
        with patch('openai_decisions.open_request', side_effect=outage) as post:
            self.assertTrue(self.turn(side_effect=openai_decisions.evaluate)[0])
            for _ in range(3):  # paused: the next conversations are not asked
                self.assertFalse(self.turn(side_effect=openai_decisions.evaluate)[0])
            self.assertEqual(post.call_count, 1)
        with connect(self.jev.path) as db:
            row = db.execute('SELECT status,result_json,error FROM jev_calls').fetchone()
        self.assertEqual((row['status'], json.loads(row['result_json'])), ('failed', {'host': True, 'refused': True}))
        self.assertNotIn('sk-', row['error'])
        items = self.items()
        self.assertEqual({i['triage']['status'] for i in items}, {'pending'})  # nothing burned: a refusal decided nothing
        self.assertTrue(all(i['attention'] for i in items))
        listed = self.client.get('/api/student-support?kind=followup').json['jev']
        self.assertRegex(listed['triage_paused'], r'^Student triage paused: OpenAI Decisions unavailable since .+\. Retrying every 5 minutes\.$')
        status = self.jev.triage_status()
        self.assertEqual((status['provider'], status['model'], status['failed_count']), ('decisions', 'gpt-6-luna', 0))
        self.assertEqual(status['paused'], listed['triage_paused'])
        # Past the pause, the first ask is the probe: the same conversation, as a separately identified call; then the others.
        later = datetime.now(timezone.utc) + timedelta(minutes=6)
        healthy = lambda state, qs: {**response(qs, {'kind': 'request'}, {'needs_action': .9}), 'model': openai_decisions.MODEL}  # noqa: E731
        with patch('student_support.triage._now', return_value=later):
            for _ in range(3):
                self.assertTrue(self.turn(side_effect=healthy)[0])
            self.assertFalse(self.turn(side_effect=healthy)[0])
        with connect(self.jev.path) as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT status,owner_id FROM jev_calls ORDER BY rowid')][0][0], 'failed')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jev_calls WHERE status='complete'").fetchone()[0], 3)
        self.assertEqual({i['triage']['status'] for i in self.items()}, {'complete'})
        self.assertNotIn('triage_paused', self.client.get('/api/student-support?kind=followup').json['jev'])
        self.assertEqual(self.jev.triage_status()['paused'], '')

    def test_an_uncertain_or_changed_model_failure_pauses_keeps_its_receipt_and_retry_all_failed_asks_once(self):
        import openai_decisions
        from assistant.policy import base_rule
        from datetime import datetime, timedelta, timezone
        self.decisions_on()
        self.incoming('a1', thread='abc')
        self.incoming('b1', thread='def', at='2026-10-03T12:05:00Z')
        with patch('openai_decisions.open_request', side_effect=TimeoutError()):
            self.assertTrue(self.turn(side_effect=openai_decisions.evaluate)[0])
            self.assertFalse(self.turn(side_effect=openai_decisions.evaluate)[0])
        later = datetime.now(timezone.utc) + timedelta(minutes=6)
        renamed = lambda state, qs: {**response(qs), 'model': 'gpt-6-luna-2026-10-09'}  # noqa: E731
        with patch('student_support.triage._now', return_value=later), \
                patch('openai_decisions.open_request', side_effect=lambda request: reply_json(renamed(None, QUESTIONS))):
            self.assertTrue(self.turn(side_effect=openai_decisions.evaluate)[0])
            self.assertFalse(self.turn(side_effect=openai_decisions.evaluate)[0])  # paused again, 10 minutes now
        self.assertIn('Retrying every 10 minutes.', self.jev.triage_status()['paused'])
        # Both outcomes may have been decided: kept, never resent; each conversation shows its failure.
        self.assertEqual(sorted(i['triage']['status'] for i in self.items()), ['failed', 'uncertain'])
        self.assertEqual(self.jev.triage_status()['failed_count'], 2)
        self.assertEqual(base_rule('jev.retry_failed').tier, 'never')
        self.assertEqual(self.client.post('/api/jev/triage/retry-failed', json={'request_id': 'all-1'}).status_code, 403)
        healthy = lambda state, qs: {**response(qs, {'kind': 'request'}, {'needs_action': .9}), 'model': openai_decisions.MODEL}  # noqa: E731
        with patch('openai_decisions.evaluate', side_effect=healthy) as asked:
            first = self.client.post('/api/jev/triage/retry-failed', json={'request_id': 'all-1'}, headers=HEADERS)
            again = self.client.post('/api/jev/triage/retry-failed', json={'request_id': 'all-1'}, headers=HEADERS)
        self.assertEqual(first.json, {'failed': 2, 'asked': 2, 'complete': 2, 'stopped': ''})
        self.assertEqual(again.json['asked'], 0)
        self.assertEqual(asked.call_count, 2)
        self.assertEqual({i['triage']['status'] for i in self.items()}, {'complete'})
        self.assertEqual((self.jev.triage_status()['failed_count'], self.jev.triage_status()['paused']), (0, ''))

    def test_retry_all_failed_stops_at_the_first_host_failure(self):
        import openai_decisions
        self.decisions_on()
        for ident, thread in (('a1', 'abc'), ('b1', 'def')):
            self.incoming(ident, thread=thread, at=f'2026-10-03T12:0{len(ident)}:00Z')
            self.turn(side_effect=client.DecisionError('Decisions returned an unreadable answer.'))  # this input's failure: no pause
        self.assertEqual(self.jev.triage_status()['failed_count'], 2)
        refused = openai_decisions.DecisionError('Decisions could not complete this check (HTTP 503).', refused=True)
        with patch('openai_decisions.evaluate', side_effect=refused) as asked:
            result = self.client.post('/api/jev/triage/retry-failed', json={'request_id': 'all-2'}, headers=HEADERS).json
        self.assertEqual((result['asked'], result['complete'], asked.call_count), (1, 0, 1))
        self.assertIn('HTTP 503', result['stopped'])
        self.assertEqual(self.jev.triage_status()['failed_count'], 2)  # a refusal decided nothing: both keep their failure

    def test_same_matter_and_recovery_run_while_decisions_is_unavailable(self):
        # JEV TRIAGE PROVIDER: SAME MATTER and Jev's recovery are Jev's own; only the triage read waits for Decisions.
        patch.dict('os.environ', {'FACULTY_TRIAGE_PROVIDER': 'decisions'}).start()
        self.incoming()
        with patch('openai_decisions.available', return_value=False), patch('openai_decisions.evaluate') as decisions, \
                patch.object(self.triage.match, 'process_one', return_value=True) as matched, \
                patch.object(self.jev, 'recover_interrupted', return_value=0) as recovered:
            self.triage._last = 0
            self.assertTrue(self.triage.process_one())
            matched.return_value = False
            self.triage._last = 0
            self.assertFalse(self.triage.process_one())
        self.assertEqual((matched.call_count, recovered.call_count), (2, 2))
        decisions.assert_not_called()
        with patch('openai_decisions.available', return_value=False), patch('jev.client.available', return_value=False), \
                patch.object(self.triage.match, 'process_one') as matched:
            self.triage._last = 0
            self.assertFalse(self.triage.process_one())
        matched.assert_not_called()

    def test_oversized_input_retains_failure_instead_of_starving_other_conversations(self):
        self.incoming(body='x')
        with patch('jev.client.evaluate') as evaluate:
            result = self.jev.ask('oversized', 'owner', '😀' * 30000, {'q': noul('Question')}, 'v1')
            again = self.jev.ask('oversized', 'owner', '😀' * 30000, {'q': noul('Question')}, 'v1')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result, again)
        evaluate.assert_not_called()

    def test_interrupted_call_recovers_in_the_worker_even_without_another_request(self):
        self.incoming()
        item = self.items()[0]
        with connect(self.jev.path) as db:
            state = snapshots(db)[(self.alice, item['source_key'])][1]
        with patch('jev.service.timestamp', return_value='2000-01-01T00:00:00+00:00'), patch('jev.client.evaluate', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.jev.ask('student-triage', self.triage.owner(self.alice, item['source_key']), state, QUESTIONS, RULE, self.alice)
        self.assertEqual(self.items()[0]['triage']['status'], 'running')
        with patch('jev.client.evaluate') as evaluate:
            self.triage._last = 0
            self.assertTrue(self.triage.process_one())
            self.triage._last = 0
            self.assertFalse(self.triage.process_one())
            evaluate.assert_not_called()
        self.assertEqual(self.items()[0]['triage']['status'], 'uncertain')
        self.assertTrue(self.items()[0]['attention'])

    def test_absence_verification_blanks_unsupported_fields_and_preserves_waiting(self):
        facts = {'first_date': '2026-10-02', 'last_date': '2026-10-03', 'reason': 'illness', 'details': 'Missed class.',
                 'status': 'pending', 'decision_note': '', 'warnings': []}
        source = {'sent_at_campus': '2026-10-02T19:00:00-05:00', 'email_text': 'I was sick today.'}
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, nouls={'conflict': .0})) as evaluate:
            checked, result = absence(self.jev, 'absence', source, facts, self.alice)
        # JEV DATES (2026-10-08): "today" names Oct 2 in code; nothing in the email names Oct 3, so code blanks it.
        self.assertEqual(checked['first_date'], '2026-10-02')
        self.assertEqual(checked['last_date'], '')
        self.assertIn('Check last date: the email does not name Saturday, October 3.', checked['warnings'])
        self.assertEqual((checked['status'], checked['decision_note']), ('pending', ''))
        self.assertEqual(result['unsupported'], ['last_date'])
        state, asked = evaluate.call_args.args
        self.assertEqual(set(asked), {'reason', 'details', 'conflict'})  # no date is put to Jev
        self.assertEqual(state['proposed'], {'reason': 'illness', 'details': 'Missed class.'})
        self.assertNotIn('relative_dates_resolved_by_code', state)
        with connect(self.jev.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM absences').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gradebook_scores').fetchone()[0], 0)
        with patch('jev.client.evaluate', side_effect=client.DecisionError('Failed')):
            kept, result = absence(self.jev, 'other', source, facts, self.alice)
        self.assertEqual((kept['first_date'], kept['last_date']), ('2026-10-02', ''))  # code's own reading stands without Jev
        self.assertIn('Jev could not verify this draft. Check every field against the email before saving.', kept['warnings'])
        self.assertEqual(result['status'], 'failed')

    def test_absence_verification_asks_jev_only_whether_a_bare_weekday_was_already_past(self):
        facts = {'first_date': '2026-09-28', 'last_date': '2026-09-28', 'reason': 'illness', 'details': '', 'status': 'pending',
                 'decision_note': '', 'warnings': []}
        source = {'sent_at_campus': '2026-10-02T19:00:00-05:00', 'email_text': 'I missed class Monday because I had the flu.'}
        both = {'timing_0': 'past', 'timing_0__reversed': 'past'}
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, choices=both, nouls={'conflict': .0})) as evaluate:
            checked, result = absence(self.jev, 'weekday', source, facts, self.alice)
        self.assertEqual((checked['first_date'], result['unsupported']), ('2026-09-28', []))
        self.assertEqual(set(evaluate.call_args.args[1]), {'reason', 'conflict', 'timing_0', 'timing_0__reversed'})  # one mention, one question
        self.assertIn('"Monday"', evaluate.call_args.args[1]['timing_0']['instructions'])
        self.assertNotIn('2026', json.dumps(evaluate.call_args.args[1]))
        later = {'timing_0': 'later', 'timing_0__reversed': 'later'}
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, choices=later, nouls={'conflict': .0})):
            checked, result = absence(self.jev, 'weekday-later', source, facts, self.alice)
        self.assertEqual((checked['first_date'], result['unsupported']), ('', ['first_date', 'last_date']))
        self.assertIn('Check first date: “Monday” in the email is Monday, October 5.', checked['warnings'])
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, nouls={'conflict': .0})):  # the orders disagree
            checked, _ = absence(self.jev, 'weekday-unclear', source, facts, self.alice)
        self.assertIn('Check first date: Jev could not tell whether “Monday” in the email is Monday, September 28 or Monday, October 5.',
                      checked['warnings'])

    def test_absence_verification_asks_timing_of_each_weekday_and_reads_each_date_with_its_own_answer(self):
        # 2026-10-09 review: one `timing` answer, about "Monday", was applied to "Wednesday" too and blanked a true last date.
        facts = {'first_date': '2026-10-05', 'last_date': '2026-10-07', 'reason': 'illness', 'details': '', 'status': 'pending',
                 'decision_note': '', 'warnings': []}
        source = {'sent_at_campus': '2026-10-06T10:00:00-05:00', 'email_text': 'I was sick Monday and will also miss Wednesday.'}
        sides = {'timing_0': 'past', 'timing_0__reversed': 'past', 'timing_1': 'later', 'timing_1__reversed': 'later'}
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, choices=sides, nouls={'conflict': .0})) as evaluate:
            checked, result = absence(self.jev, 'two-weekdays', source, facts, self.alice)
        asked = evaluate.call_args.args[1]
        self.assertEqual(set(asked), {'reason', 'conflict', 'timing_0', 'timing_0__reversed', 'timing_1', 'timing_1__reversed'})
        self.assertIn('"Monday"', asked['timing_0']['instructions'])
        self.assertIn('"Wednesday"', asked['timing_1']['instructions'])
        self.assertEqual((checked['first_date'], checked['last_date'], result['unsupported'], checked['warnings']),
                         ('2026-10-05', '2026-10-07', [], []))
        wrong = {**sides, 'timing_1': 'past', 'timing_1__reversed': 'past'}  # Jev reads "Wednesday" as last week's
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, choices=wrong, nouls={'conflict': .0})):
            checked, result = absence(self.jev, 'two-weekdays-wrong', source, facts, self.alice)
        self.assertEqual((checked['first_date'], checked['last_date'], result['unsupported']), ('2026-10-05', '', ['last_date']))
        self.assertEqual(checked['warnings'], ['Check last date: “Wednesday” in the email is Wednesday, September 30.'])

    def test_absence_verification_without_jev_never_keeps_a_bare_weekday(self):
        # ABSENCE CHECK v3: a bare weekday is kept only when `timing` picks it, so with Jev off, failed or uncertain it is blanked.
        facts = {'first_date': '2026-10-12', 'last_date': '2026-10-12', 'reason': 'illness', 'details': '', 'status': 'pending',
                 'decision_note': '', 'warnings': []}
        source = {'sent_at_campus': '2026-10-09T10:00:00-05:00', 'email_text': 'I missed class Monday because I had the flu.'}
        undecided = 'Jev could not tell whether “Monday” in the email is Monday, October 5 or Monday, October 12.'
        for name, patched, status, warning in (
                ('off', patch('jev.client.available', return_value=False), 'unavailable',
                 'Jev is not available to check this draft. Check every field against the email before saving.'),
                ('failed', patch('jev.client.evaluate', side_effect=client.DecisionError('Failed')), 'failed',
                 'Jev could not verify this draft. Check every field against the email before saving.'),
                ('uncertain', patch('jev.client.evaluate', side_effect=client.DecisionError('Lost', uncertain=True)), 'uncertain',
                 'Jev could not verify this draft. Check every field against the email before saving.')):
            with self.subTest(name=name), patched:
                checked, result = absence(self.jev, f'weekday-{name}', source, facts, self.alice)
                self.assertEqual((checked['first_date'], checked['last_date']), ('', ''))
                self.assertEqual((result['status'], result['unsupported']), (status, ['first_date', 'last_date']))
                self.assertEqual(checked['warnings'], ['Check first date: ' + undecided, 'Check last date: ' + undecided, warning])
        with patch('jev.client.available', return_value=False):  # no bare weekday: Jev off adds nothing, as before
            checked, result = absence(self.jev, 'definite-off', {**source, 'email_text': 'I was sick today.'},
                                      {**facts, 'first_date': '2026-10-09', 'last_date': '2026-10-09'}, self.alice)
        self.assertEqual((checked['first_date'], checked['warnings'], result['unsupported']), ('2026-10-09', [], []))

    def test_evidence_contradictions_and_limits_are_disclosed_without_copying_sources(self):
        sources = [{'id': 's1', 'path': '/api/absences/example', 'observed_at': '2026-10-03', 'fingerprint': 'digest',
                    'fingerprint_of': 'canonical JSON response', 'text': '{"status":"pending"}'}]
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs, {'paragraph_0': 'contradicted'})):
            result = evidence(self.jev, 'chat', 'Is it excused?', 'The absence was excused.', sources)
        self.assertEqual((result['status'], result['checks'][0]['verdict']), ('review', 'contradicted'))
        self.assertNotIn('text', result['sources'][0])
        with connect(self.jev.path) as db:
            row = dict(db.execute("SELECT * FROM jev_calls WHERE purpose='assistant-evidence'").fetchone())
            self.assertNotIn('"status":"pending"', json.dumps(row))
        image = [{**sources[0], 'fingerprint_of': 'file bytes'}]
        with patch('jev.client.evaluate') as evaluate:
            self.assertEqual(evidence(self.jev, 'image', 'Scan?', 'A grade.', image)['status'], 'unverified')
            evaluate.assert_not_called()
        with patch('jev.client.evaluate', side_effect=lambda state, qs: response(qs)):
            result = evidence(self.jev, 'long', 'Question', '\n\n'.join(['Claim'] * 9), sources)
        self.assertEqual(result['status'], 'review')
        self.assertTrue(result['partial'])

    def test_exclusive_receipt_claim_and_operations_show_only_counts(self):
        entered, release = threading.Event(), threading.Event()
        result = []
        def evaluate(state, questions):
            entered.set()
            release.wait(3)
            return response(questions)
        qs = {'test': noul('True?')}
        with patch('jev.client.evaluate', side_effect=evaluate) as mock:
            thread = threading.Thread(target=lambda: result.append(self.jev.ask('test', 'private-owner', {'secret': 'private source'}, qs, 'test')))
            thread.start()
            self.assertTrue(entered.wait(3))
            try:
                second = self.jev.ask('test', 'private-owner', {'secret': 'private source'}, qs, 'test')
                self.assertEqual(second['status'], 'running')
                overview = self.client.get('/api/workspace/overview').json
                self.assertEqual(overview['operations']['totals']['running'], 1)
                self.assertNotIn('private-owner', json.dumps(overview))
            finally:
                release.set()
                thread.join(3)
            self.assertEqual(mock.call_count, 1)
        self.assertEqual(result[0]['status'], 'complete')

    def test_assistant_buffers_answer_until_check_and_source_bodies_stay_ephemeral(self):
        assistant = self.app.extensions['assistant']
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        observed = []
        def turn(conversation, text):
            rendered, event = assistant.run_tool(conversation.session, 'faculty_get', {'path': '/api/state'})
            observed.append(rendered)
            conversation.tool_event(event)
            conversation.emit('assistant', text='The course is already completed.')
        engine = Mock(name='fake')
        engine.name = 'fake'
        engine.turn.side_effect = turn
        def evaluate(state, questions):
            observed.append(state)
            entered.set()
            release.wait(3)
            return response(questions, {'paragraph_0': 'unsupported'})
        with patch('assistant.conversations.engine_for', return_value=engine), patch('jev.client.evaluate', side_effect=evaluate):
            conversation = Conversation(assistant, assistant.open_session(), {'name': 'fake'}, 'http://localhost', '#overview')
            conversation.send('What is happening with the course?')
            self.assertTrue(entered.wait(3))
            try:
                view = conversation.view()
                self.assertTrue(view['busy'])
                self.assertFalse(any(e['kind'] == 'assistant' for e in view['events']))
                self.assertNotIn('_jev_text', observed[0])
                self.assertNotIn('_jev_truncated', observed[0])
            finally:
                release.set()
            for _ in range(100):
                if not conversation.busy:
                    break
                done.wait(.02)
            self.assertFalse(conversation.busy)
            view = conversation.view()
            verdict = next(e for e in view['events'] if e['kind'] == 'verification')
            self.assertEqual(verdict['status'], 'review')
            self.assertEqual(verdict['checks'][0]['verdict'], 'unsupported')
            self.assertTrue(any(e['kind'] == 'assistant' for e in view['events']))
            self.assertNotIn('_jev_sources', conversation.session)
            self.assertNotIn('"tools":', json.dumps(verdict.get('sources')))
            conversation.close()

    def test_absence_check_runs_inside_busy_gate_and_cancel_discards_late_draft(self):
        assistant = self.app.extensions['assistant']
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        extracted = {'first_date': '2026-10-02', 'last_date': '2026-10-02', 'reason': 'illness', 'details': 'Sick.', 'warnings': []}
        engine = Mock()
        engine.name = 'fake'
        engine.turn.side_effect = lambda c, t: c.emit('assistant', text=json.dumps(extracted))
        def evaluate(state, questions):
            entered.set()
            release.wait(3)
            return response(questions, nouls={'conflict': 0})
        with patch('assistant.conversations.engine_for', return_value=engine), patch('jev.client.evaluate', side_effect=evaluate):
            conversation = EmailDraft(assistant, assistant.open_session(), {'name': 'fake'}, 'http://localhost', '#mail',
                email={'student': {'enrollment_id': self.alice}}, source_payload={'sent_at_campus': '2026-10-02T12:00:00-05:00', 'email_text': 'Sick today.'})
            conversation.send('Extract', internal=True)
            self.assertTrue(entered.wait(3))
            try:
                self.assertTrue(conversation.view()['busy'])
                self.assertIsNone(conversation.view()['draft'])
                conversation.stop()
            finally:
                release.set()
            for _ in range(100):
                if not conversation.busy:
                    break
                done.wait(.02)
            self.assertIsNone(conversation.view()['draft'])
            with connect(self.jev.path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM absences').fetchone()[0], 0)
            conversation.close()
