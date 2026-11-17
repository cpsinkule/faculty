"""JEV URGENCY/WELLBEING and SEND-CHECKS (docs/decisions/jev.md; requirement, 2026-10-03, brainstorm items 3 and 4). Scripted Jev
answers only; nothing reaches TypeSafe, Gmail or Canvas."""
import json
import sqlite3
import unittest
from datetime import date
from unittest import mock
from unittest.mock import patch

from assistant import policy
from jev import client, send_checks
from rosters import connect
from student_support import triage as triage_module
from student_support.triage import snapshots
import test_gmail_mail
import test_jev
from test_jev import response
from test_storage import HEADERS


class UrgencyWellbeingTests(test_jev.Base):
    # The Jev fixture and helpers, without re-running test_jev's own tests.
    incoming, analyze, items = test_jev.JevTests.incoming, test_jev.JevTests.analyze, test_jev.JevTests.items

    def setUp(self):  # test_jev.JevTests.setUp
        super().setUp()
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        self.support = self.app.extensions['student_support']
        self.triage = self.support.triage
        self.jev = self.app.extensions['jev']

    def analyze_by_text(self, answers):
        """One worker turn; `answers(text)` gives (nouls, scores) for the latest message's text."""
        self.triage._last = 0

        def evaluate(state, questions):
            nouls, scores = answers(state['messages'][-1]['text'])
            return response(questions, {'kind': 'request'}, {'needs_action': .9, **nouls}, scores)
        with patch('jev.client.evaluate', side_effect=evaluate) as called:
            worked = self.triage.process_one()
        return worked, called

    def concerns_on(self):
        """JEV WELLBEING OFF (requirement, 2026-10-12). What the check did while it was on stays covered, at the
        gate that would put it back (.30), so turning it on again is one value."""
        for gate in triage_module.GATES.values():
            patcher = patch.dict(gate, {'wellbeing': .30})
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_wellbeing_check_is_off_and_a_message_is_treated_like_any_other(self):
        import math
        self.assertEqual({gate['wellbeing'] for gate in triage_module.GATES.values()}, {math.inf})
        self.incoming(body='Thanks. My grandmother passed away and I am struggling this week.')
        worked, evaluate = self.analyze('acknowledgment', wellbeing=.99)
        self.assertIn('wellbeing', evaluate.call_args.args[1])  # still asked, in the one request: kept readings stay valid
        item = self.items()[0]
        self.assertNotIn('wellbeing', item)
        self.assertEqual((item['state'], item['attention']), ('fyi', False))  # a strong acknowledgment is FYI, whatever that answer
        self.assertTrue(item['triage']['no_reply_suggested'])
        self.assertFalse(item['triage']['wellbeing'])
        self.assertEqual(self.support.attention()['wellbeing'], 0)
        self.assertNotIn('support:wellbeing', json.dumps(self.client.get('/api/workspace/overview').json))
        # A message that asks for something is listed by what it asks, with nothing pinned on it and nothing to mark reviewed.
        self.incoming('a2', body='I am still not doing well. Could I have an extension?', at='2026-10-03T14:00:00Z')
        self.analyze('request', needs_action=.9, wellbeing=.99)
        item = self.items()[0]
        self.assertEqual((item['state'], item['attention']), ('needs_reply', True))
        self.assertNotIn('wellbeing', item)
        self.assertEqual(self.support.attention()['wellbeing'], 0)

    def review(self, item, request_id='review-1', call=None):
        return self.client.post('/api/student-support/wellbeing/reviewed', headers=HEADERS, json={
            'enrollment_id': item['enrollment_id'], 'source_key': item['source_key'],
            'call_id': call or item['wellbeing']['call_id'], 'request_id': request_id})

    def test_a_possible_wellbeing_concern_is_pinned_counted_never_fyi_and_cleared_only_by_review(self):
        self.concerns_on()
        self.incoming(body='Thanks. My grandmother passed away and I am struggling this week.')
        self.analyze('acknowledgment', wellbeing=.6)
        item = self.items()[0]
        self.assertEqual((item['state'], item['attention']), ('needs_reply', True))  # an acknowledgment, yet never FYI
        self.assertFalse(item['triage']['no_reply_suggested'])
        self.assertEqual(item['wellbeing']['probability'], .6)
        self.assertEqual(self.support.attention()['wellbeing'], 1)
        overview = self.client.get('/api/workspace/overview').json
        home = json.dumps(overview)
        self.assertIn('support:wellbeing', home)
        # TEACHING-WORKFLOWS "NEEDS ME COUNT": the concern is a follow-up that needs a reply, counted once on the sidebar badge.
        cards = {card['key']: card for card in overview['attention']}
        self.assertEqual((cards['support:followup']['count'], cards['support:wellbeing'].get('counted_in')), (1, 'support:followup'))
        for private in ('Alice', 'grandmother', self.alice):
            self.assertNotIn(private, home)
        self.assertEqual(self.review(item, call='another-call').status_code, 409)
        self.assertEqual(self.review(item).status_code, 200)
        self.assertEqual(self.review(item).status_code, 200)  # the same request again
        self.assertNotIn('wellbeing', self.items()[0])
        self.assertEqual(self.support.attention()['wellbeing'], 0)
        with connect(self.jev.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM student_support_wellbeing_reviews')
        # A newer student message is analysed again; flagged again, it shows again.
        self.incoming('a2', body='I am still not doing well.', at='2026-10-03T14:00:00Z')
        self.analyze('request', wellbeing=.4)
        again = self.items()[0]
        self.assertNotEqual(again['wellbeing']['call_id'], item['wellbeing']['call_id'])

    def test_a_review_holds_until_the_student_writes_again(self):
        self.concerns_on()
        # REVIEWED HOLDS (requirement, 2026-10-06): a nightly due-date move made Jev read the same messages again, and the
        # concern they had marked reviewed came back.
        self.incoming(body='I had a concussion and missed class this week.')
        self.analyze('request', wellbeing=.6)
        item = self.items()[0]
        self.assertEqual(self.review(item).status_code, 200)
        week = {(self.alice, 'gmail:abc'): [{'title': 'Quiz 4', 'when': 'the next day'}]}
        with patch('student_support.triage.upcoming', return_value=week):
            worked, _ = self.analyze('request', wellbeing=.6)
            again = self.items()[0]
            self.assertEqual(self.support.attention()['wellbeing'], 0)
        self.assertTrue(worked)
        self.assertNotEqual(again['triage']['id'], item['wellbeing']['call_id'])  # a new analysis of the same messages
        self.assertNotIn('wellbeing', again)
        # The student writes again: analysed and flagged, it shows again.
        self.incoming('a2', body='The headaches are getting worse.', at='2026-10-03T14:00:00Z')
        self.analyze('request', wellbeing=.6)
        self.assertIn('wellbeing', self.items()[0])
        self.assertEqual(self.support.attention()['wellbeing'], 1)

    def test_an_unreviewed_concern_is_carried_across_providers_outages_and_re_reads(self):
        self.concerns_on()
        # JEV WELLBEING CARRIED (2026-10-09): a concern disappeared whenever the current provider had no complete receipt of its
        # own: the switch to Decisions, Decisions unavailable, or a re-read pending, failed or calmer after other context changed.
        import openai_decisions
        self.incoming(body='My grandmother passed away and I cannot cope.')
        self.analyze('request', wellbeing=.9)
        flagged = self.items()[0]['wellbeing']['call_id']

        def shown():
            item = self.items()[0]
            return (item.get('wellbeing') or {}).get('call_id'), item['attention'], self.support.attention()['wellbeing']
        self.assertEqual(shown(), (flagged, True, 1))
        decisions = patch.dict('os.environ', {'FACULTY_TRIAGE_PROVIDER': 'decisions', 'OPENAI_API_KEY': 'synthetic-key'})
        with decisions, patch('openai_decisions.available', return_value=True):
            self.assertEqual(self.items()[0]['triage']['status'], 'pending')  # Decisions has not read it yet
            self.assertEqual(shown(), (flagged, True, 1))
            calm = lambda state, qs: {**response(qs, {'kind': 'acknowledgment'}, {'needs_action': .0, 'wellbeing': .2}),  # noqa: E731
                                      'model': openai_decisions.MODEL}
            self.triage._last = 0
            with patch('openai_decisions.evaluate', side_effect=calm), patch.object(self.triage.match, 'process_one', return_value=False):
                self.assertTrue(self.triage.process_one())
            item = self.items()[0]
            self.assertEqual(item['triage']['status'], 'complete')
            self.assertNotEqual(item['state'], 'fyi')  # a calm acknowledgment, yet an open concern is never FYI
            self.assertEqual(shown(), (flagged, True, 1))
        with decisions, patch('openai_decisions.available', return_value=False):
            self.assertNotIn('triage', self.items()[0])  # triage unavailable: nothing new is read
            self.assertEqual(shown(), (flagged, True, 1))
        # Back on Jev, a context-only change makes a re-read that fails: still shown.
        week = {(self.alice, 'gmail:abc'): [{'title': 'Quiz 4', 'when': 'the next day'}]}
        with patch('student_support.triage.upcoming', return_value=week):
            self.triage._last = 0
            with patch('jev.client.evaluate', side_effect=client.DecisionError('Jev returned an unreadable answer.')):
                self.triage.process_one()
            self.assertEqual(self.items()[0]['triage']['status'], 'failed')
            self.assertEqual(shown(), (flagged, True, 1))
            # Mark reviewed on the carried receipt clears it.
            self.assertEqual(self.review(self.items()[0]).status_code, 200)
            self.assertEqual(shown()[0::2], (None, 0))
        # A newer student message read calmly is no concern; the old reading no longer carries over it.
        self.incoming('a2', body='Thanks, I am feeling better now.', at='2026-10-03T14:00:00Z')
        self.analyze('request', wellbeing=.1)
        self.assertEqual(shown()[0::2], (None, 0))

    def test_a_carried_concern_ends_at_a_newer_message_without_a_review(self):
        self.concerns_on()
        self.incoming(body='I was in the ER last night.')
        self.analyze('request', wellbeing=.9)
        self.assertIn('wellbeing', self.items()[0])
        self.incoming('a2', body='Also, when is the quiz?', at='2026-10-03T14:00:00Z')
        self.assertNotIn('wellbeing', self.items()[0])  # older than the student's newest message: not carried (pending)
        self.analyze('request', wellbeing=.1)
        self.assertNotIn('wellbeing', self.items()[0])

    def test_resolving_the_follow_up_clears_the_concern_and_below_threshold_is_no_concern(self):
        self.concerns_on()
        self.incoming(body='I was in the hospital last night.')
        self.analyze('request', wellbeing=.29)
        self.assertNotIn('wellbeing', self.items()[0])
        self.incoming('a2', body='Still in the hospital, sorry.', at='2026-10-03T13:00:00Z')
        self.analyze('request', wellbeing=.95)
        item = self.items()[0]
        self.assertIn('wellbeing', item)
        saved = self.client.post('/api/student-support', json={**item, 'state': 'resolved', 'request_id': 'resolve'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(self.support.attention()['wellbeing'], 0)

    def test_urgency_orders_the_list_and_labels_only_confident_levels(self):
        self.incoming(body='When is the final? No rush.', at='2026-10-03T12:00:00Z')
        with connect(self.app.config['DATABASE']) as db:
            from communications.record import record
            record(db, 'gmail', 'b1', {'sent_at': '2026-10-03T11:00:00Z', 'author_role': 'student', 'author': {}, 'recipients': [],
                   'subject': 'Quiz tomorrow', 'body_text': 'I am sick and the quiz is tomorrow, what should I do?', 'attachments': [],
                   'context': {'thread_id': 'bob'}}, [(self.bob, self.term, 'author', 'roster_address')], 'bob')
            db.commit()
        urgent = lambda text: ({}, {'urgency': 2 if 'tomorrow' in text else 0})  # noqa: E731
        self.analyze_by_text(urgent)
        self.analyze_by_text(urgent)
        items = self.items()
        self.assertEqual([i['title'] for i in items], ['Quiz tomorrow', 'Office hours'])
        self.assertEqual(items[0]['triage']['urgency']['label'], 'Time-sensitive')
        self.assertEqual(items[1]['triage']['urgency']['label'], '')

    def test_dates_are_worked_out_from_the_message_never_from_today(self):
        self.incoming(body='I will miss class tomorrow.', at='2026-10-03T12:00:00Z')
        with connect(self.app.config['DATABASE']) as db:
            state = snapshots(db)[(self.alice, 'gmail:abc')][1]
        self.assertEqual(state['relative_dates_resolved_by_code'], {'tomorrow': 'Sunday, October 04, 2026'})
        self.assertEqual(state['upcoming'], [])

    def test_the_review_is_the_instructors_own_control(self):
        self.assertEqual(policy.base_rule('student_support.wellbeing_reviewed').tier, 'never')
        for endpoint in ('jev_routing.palette_route', 'gmail_mail.check', 'canvas.message_check'):
            self.assertEqual(policy.base_rule(endpoint).tier, 'never', endpoint)


class Scripted:
    """A JevService stand-in returning `response()` answers with the given overrides; keeps the questions it was asked."""

    def __init__(self, choices=None, nouls=None, status='complete'):
        self.choices, self.nouls, self.status, self.asked = choices or {}, nouls or {}, status, []

    def ask(self, purpose, owner, state, questions, rule, enrollment=None):
        self.asked.append((purpose, owner, state, questions, rule))
        if self.status != 'complete':
            return {'status': self.status, 'error': 'scripted'}
        return {'status': 'complete', 'id': 'call-1', **response(questions, self.choices, self.nouls)}


QUIZ = {'Quiz 4': [{'date': '2026-10-10', 'time': '19:00', 'location': 'HELD 111', 'sections': [508]}]}
TODAY = date(2026, 10, 3)


class SendCheckTests(unittest.TestCase):
    def check(self, service, body, events=QUIZ, **kwargs):
        with patch('jev.send_checks._events', return_value=events), patch('jev.send_checks._rooms', return_value=['HELD 111', 'ZACH 350']):
            return send_checks.check(service, None, owner='mail:x', body=body, terms={'t1'}, today=TODAY, **kwargs)

    def texts(self, result):
        return [note['text'] for note in result['notes']]

    def picked(self, title='Quiz 4'):
        return {'event_0': title, 'event_0__reversed': title}

    def test_a_wrong_date_time_or_room_is_noted_against_faculty(self):
        result = self.check(Scripted(self.picked()), 'Reminder: Quiz 4 is on Thursday, October 8 at 7pm.')
        self.assertEqual(self.texts(result), ['It says Quiz 4 is Thursday, October 8 (“Thursday, October 8”); Faculty has Saturday, October 10 (section 508).'])
        result = self.check(Scripted(self.picked()), 'Quiz 4 is Saturday, October 10 at 6 pm in ZACH 350.')
        self.assertEqual(self.texts(result), ['It gives Quiz 4 at 6:00 PM; Faculty has 7:00 PM.', 'It puts Quiz 4 in ZACH 350; Faculty has HELD 111.'])
        self.assertEqual(self.texts(self.check(Scripted(self.picked()), 'Quiz 4 is Saturday, October 10 at 7 pm in HELD 111.')), [])

    def test_jev_sees_event_names_never_their_dates_and_must_agree_in_both_orders(self):
        service = Scripted({'event_0': 'Quiz 4', 'event_0__reversed': send_checks.NONE})
        self.assertEqual(self.texts(self.check(service, 'Quiz 4 is on October 9.')), [])
        questions = service.asked[0][3]
        self.assertEqual(set(questions['event_0']['criteria']), {'Quiz 4', send_checks.NONE})
        self.assertEqual(list(questions['event_0__reversed']['criteria']), list(reversed(list(questions['event_0']['criteria']))))
        self.assertNotIn('2026-10-10', json.dumps(questions))

    def test_a_weekday_that_does_not_fit_is_noted_by_code(self):
        result = self.check(None, 'See you Monday, October 6.', events={})
        self.assertEqual(self.texts(result), ['“Monday, October 6”: Tuesday, October 6 is not a Monday.'])

    def test_an_unanswered_question_from_the_student(self):
        service = Scripted(nouls={'asks_0': .99, 'answered_0': .95, 'asks_1': .99, 'answered_1': .1, 'asks_2': .1, 'answered_2': .0})
        result = self.check(service, 'The quiz covers 3.1 to 3.4.\n\nOn Fri, Oct 2, 2026 at 3:00 PM Alex wrote:\n> old text',
                            source='What does the quiz cover? Can I use a calculator? Thanks so much.')
        self.assertEqual(self.texts(result), ['It may not answer: “Can I use a calculator?”'])
        self.assertNotIn('old text', json.dumps(service.asked[0][2]))  # quoted history is set aside

    def test_a_promised_attachment_other_students_and_uins(self):
        people = [{'name': 'Example, Bob', 'enrollment_id': 'b'}, {'name': 'Student, Alex', 'enrollment_id': 'a'}]
        result = self.check(Scripted(nouls={'attached': .9}), 'I attached the slides. Bob Example asked too. UIN 123456789.',
                            people=people, recipients=['a'])
        self.assertEqual(self.texts(result), ['It names Example, Bob, another student. Check it is meant for this reader.',
                                              'It contains a nine-digit number, which may be a UIN.',
                                              'It says a file is attached, and nothing is attached.'])
        mass = self.check(None, 'Alex Student did great.', events={}, people=people, mass=True)
        self.assertEqual(self.texts(mass), ['It names Student, Alex, a student, to everyone it goes to.'])

    def test_a_mass_message_notes_dates_that_differ_by_section(self):
        events = {'Quiz 4': [{'date': '2026-10-09', 'time': '', 'location': '', 'sections': [507]},
                             {'date': '2026-10-10', 'time': '', 'location': '', 'sections': [508]}]}
        result = self.check(Scripted(self.picked()), 'Quiz 4 is on October 9.', events=events, mass=True)
        self.assertEqual(self.texts(result), ['Quiz 4 is on different days for the sections it goes to: Friday, October 9 (section 507); '
                                              'Saturday, October 10 (section 508).'])

    def test_jev_off_or_failed_still_runs_faculty_checks_and_says_so(self):
        result = self.check(Scripted(status='failed'), 'See the attached file for Monday, October 6.', events={})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.texts(result), ['“Monday, October 6”: Tuesday, October 6 is not a Monday.', 'It mentions an attachment, and nothing is attached.'])
        self.assertIn('could not check', result['detail'])
        self.assertEqual(self.check(None, 'Nothing special here.', events={})['notes'], [])

    def test_dates_are_read_from_the_written_forms(self):
        found = send_checks.mentions('Due 10/12 by 5 pm. Exam on Mon Oct 12th, 2026; also 2026-10-14 and tomorrow. Marching 3 bands.', TODAY)
        self.assertEqual([(m['date'], m['weekday'], m['time']) for m in found],
                         [('2026-10-12', '', '17:00'), ('2026-10-12', 'monday', ''), ('2026-10-14', '', ''), ('2026-10-04', '', '')])

    def test_this_weekday_agrees_with_jev_dates(self):
        # 2026-10-09: "this <day>" is a date only on or before that day of the sent week, in both readers.
        from jev import dates
        for sent, text in (('2026-10-07T15:00:00-05:00', 'this Friday'), ('2026-10-07T15:00:00-05:00', 'this Monday'),
                           ('2026-10-10T10:00:00-05:00', 'this Monday'), ('2026-10-11T10:00:00-05:00', 'this Sunday')):
            with self.subTest(sent=sent, text=text):
                read = [m['date'] for m in send_checks.mentions(text, dates.campus_day(sent))]
                definite = [d for m in dates.mentions(text, sent) if m['form'] == dates.DEFINITE for d in m['dates']]
                self.assertEqual(read, definite)


class ScoreValidationTests(unittest.TestCase):
    def test_rounded_scores_pass_and_inconsistent_ones_fail(self):
        questions = {'urgency': {'type': 'score', 'instructions': 'How soon?', 'criteria': ['Routine', 'Soon', 'Now']}}
        good = {'model': client.MODEL, 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'answers': {'urgency': {
            'type': 'score', 'score': .13, 'confidence': .81, 'legend': {}, 'probabilities': {'0': .9, '1': .06, '2': .04}}}}
        self.assertEqual(client.validate(good, questions)['answers']['urgency']['score'], .13)
        for change in ({'score': 1.5}, {'score': 3.0}, {'probabilities': {'0': .5, '1': .1, '2': .1}}, {'probabilities': {'0': 1.0}}):
            bad = {**good, 'answers': {'urgency': {**good['answers']['urgency'], **change}}}
            with self.subTest(change=change), self.assertRaises(client.DecisionError):
                client.validate(bad, questions)


class MailRouteTests(test_gmail_mail.Base):
    def setUp(self):  # test_gmail_mail.StudentTests.setUp's roster, without re-running its tests
        super().setUp()
        roster = {"section_id": "sec-1", "section": 507, "term_id": "t1", "students": [
            {"id": "e1", "fields": {"Name": "Student, Alex", "Email": "alex@tamu.edu"}},
            {"id": "e2", "fields": {"Name": "Student, Bo"}}]}
        patcher = mock.patch.object(self.mail, "_roster", return_value=roster)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_check_reads_the_answered_message_from_gmail_and_changes_nothing(self):
        captured = []

        def evaluate(state, questions):
            captured.append(state)
            return response(questions, nouls={'asks_0': .99, 'answered_0': .05})
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=evaluate), \
                mock.patch.object(self.mail, "sections", return_value=[{"group_id": "g", "number": 507, "code": "MATH 140"}]):
            before = list(self.google.calls)
            answer = self.client.post('/api/mail/check', json=self.fields(reply_thread_id='a1', body='Noted.'), headers=HEADERS)
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual([n['text'] for n in answer.json['notes']], ['It may not answer: “Can I move my exam?”'])
        self.assertEqual(captured[0]['student_sentences'], ['Can I move my exam?'])
        self.assertEqual([c for c in self.google.calls[len(before):] if c[0] != 'GET'], [])  # read only
        with connect(self.app.config['DATABASE']) as db:
            stored = json.dumps([dict(r) for r in db.execute("SELECT * FROM jev_calls WHERE purpose='send-check'")])
        self.assertNotIn('move my exam', stored)


if __name__ == '__main__':
    unittest.main()
