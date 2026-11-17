"""TEACHING-WORKFLOWS EVIDENCE CLOSES and CHECK BACK (requirement, 2026-10-22): a follow-up Waiting on student is
resolved by itself, with Undo, once a record that came in for its student is, Jev reads strongly, what it waits for; every other
one comes back to Needs me on its check-back day, for Resolved, Wait a week or Write to student….

Synthetic catalogs, a scripted Jev and scripted engines only. No configured model, mailbox or outside service is reached.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import test_followup_deadline_closes as deadline  # its helpers, not its test case
from assistant import followup_evidence as evidence
from assistant.followups import FollowupSuggestions
from jev import client
from rosters import connect
from scheduling.clock import campus_now
from student_support.service import check_back
from test_storage import HEADERS


def scripted(arrived=.95, student=.05, instructor=.05, seen=None):
    """A fake `jev.client.evaluate` answering the EVIDENCE CLOSES questions; `seen` collects the states it was asked about."""
    def evaluate(state, questions):
        if seen is not None:
            seen.append(state)
        values = {'student_still_owes': student, 'instructor_still_owes': instructor}
        return {'model': client.MODEL, 'usage': {'input_tokens': 300, 'output_tokens': 20},
                'answers': {name: {'type': 'noul', 'noul': values.get(name, arrived)} for name in questions}}
    return evaluate


class EvidenceClosesTests(deadline.DeadlineClosesTests):
    # Its own tests only: the borrowed case's tests stay in their own module.
    for name in [n for n in dir(deadline.DeadlineClosesTests) if n.startswith('test_')]:
        locals()[name] = None
    del name

    def decide(self, absence, status='excused'):
        response = self.client.post('/api/absences/' + absence, json={'base_revision': 1, 'status': status, 'request_id': 'decide-' + absence},
                                    headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)

    def waiting(self):
        """The follow-up Waiting on its student's doctor's note, and its absence still undecided."""
        session = self.session(body='I was sick on Friday and missed the quiz. I will send a doctor’s note.')
        absence = self.record_absence(session)
        return self.wait_on_student(), absence

    def close(self, **answers):
        with self.jev(scripted(**answers)) as asked:
            return self.suggestions.close_on_evidence(), asked

    @staticmethod
    def jev(evaluate):
        """Jev configured for the close only (triage of the student's message keeps its unconfigured path), answering as scripted."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch('jev.client.available', return_value=True))
        asked = stack.enter_context(patch('jev.client.evaluate', side_effect=evaluate))
        class Asking:
            def __enter__(self):
                return asked
            def __exit__(self, *exc):
                return stack.__exit__(*exc)
        return Asking()

    def test_a_follow_up_resolves_once_the_record_it_waits_for_comes_in(self):
        item_id, absence = self.waiting()
        self.assertFalse(self.close()[0])  # nothing came in yet
        self.decide(absence)
        seen = []
        with self.jev(scripted(seen=seen)):
            self.assertTrue(self.suggestions.close_on_evidence())
        [state] = seen
        self.assertEqual(state['follow_up']['waits_for'], 'Their doctor’s note for Oct 2')
        self.assertEqual([r['record'] for r in state['arrived']], ['absence'])
        self.assertIn('was decided: Excused', state['arrived'][0]['what'])
        self.assertTrue(any('doctor’s note' in m['text'] for m in state['messages']))
        self.assertEqual(self.state(item_id), 'resolved')
        shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
        [row] = [d for d in shown['suggestions']['done'] if d['title'] == 'Marked this follow-up resolved']
        self.assertEqual((row['state'], row['undo']), ('done', True))
        action = self.assistant._view(*self.assistant.action(row['id']))
        self.assertEqual((action['tier'], action['endpoint']), ('auto', 'student_support.revise'))
        self.assertIn('Fri, Oct 2', action['summary'])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_mail_sends').fetchone()[0], 0)  # nothing is sent
        undone = self.client.post(f"/api/student-support/followups/suggestions/{row['run']}/undo",
                                  json={'action_id': row['id'], 'seen': row['seen'], 'request_id': 'reopen'}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual(self.state(item_id), 'waiting')
        self.assertFalse(self.close()[0])  # undone once: theirs from then on

    def test_jev_unsure_keeps_it_waiting_and_is_asked_once(self):
        item_id, absence = self.waiting()
        self.decide(absence)
        closed, asked = self.close(arrived=.6)
        self.assertTrue(closed)  # it asked
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(self.state(item_id), 'waiting')
        with self.jev(AssertionError('asked again')):
            self.assertFalse(self.suggestions.close_on_evidence())
        self.suggestions._evidence_seen.clear()  # a restart: the kept receipt answers, Jev is not asked again
        with self.jev(AssertionError('asked again')):
            self.assertTrue(self.suggestions.close_on_evidence())
        self.assertEqual(self.state(item_id), 'waiting')

    def test_something_else_still_awaited_or_owed_keeps_it_waiting(self):
        for owed in ({'student': .7}, {'instructor': .7}):
            with self.subTest(owed=owed):
                self.assertIsNone(self.verdict(**owed))
        self.assertEqual(self.verdict()['record'], 'absence')

    def verdict(self, arrived=.95, student=.05, instructor=.05):
        records = [{'record': 'absence', 'what': 'Their absence on Fri, Oct 2 (illness) was decided: Excused', 'when': '2026-10-08T12:00:00.000Z'}]
        values = {'student_still_owes': student, 'instructor_still_owes': instructor}
        receipt = {'status': 'complete', 'answers': {name: {'noul': values.get(name, arrived)} for name in evidence.questions(records)}}
        return evidence.verdict(receipt, records)

    def test_a_record_from_before_it_was_set_waiting_is_not_evidence(self):
        session = self.session(body='I was sick on Friday and missed the quiz.')
        absence = self.record_absence(session)
        self.decide(absence)
        item_id = self.wait_on_student()  # set waiting after the decision: the instructor knew of it
        with self.jev(AssertionError('asked')):
            self.assertFalse(self.suggestions.close_on_evidence())
        self.assertEqual(self.state(item_id), 'waiting')

    def test_an_automatic_not_excused_is_deadline_closes_own(self):
        item_id, _ = self.waiting()
        self.assertTrue(self.expire())  # Not excused automatically: DEADLINE CLOSES reads it, not this
        with self.jev(AssertionError('asked')):
            self.assertFalse(self.suggestions.close_on_evidence())

    def test_a_newer_student_message_or_an_open_task_keeps_it_waiting(self):
        item_id, absence = self.waiting()
        self.decide(absence)
        self.incoming(ident='a2', body='Did you get my note?', at='2026-10-09T15:00:00Z')
        with self.jev(AssertionError('asked')):
            self.assertFalse(self.suggestions.close_on_evidence())  # the student wrote again: back to Needs my reply, theirs
        self.assertEqual(self.state(item_id), 'waiting')

    def test_without_jev_nothing_is_read(self):
        item_id, absence = self.waiting()
        self.decide(absence)
        with patch('jev.client.available', return_value=False), patch('jev.client.evaluate', side_effect=AssertionError('asked')):
            self.assertFalse(self.suggestions.close_on_evidence())

    # DECISION CLOSES ---------------------------------------------------------------------------------------------------------

    def todo(self, commitments=None, next_step='Select Excused for the October 2 absence.'):
        session = self.session(body='I was sick on Friday and missed the quiz.')
        absence = self.record_absence(session)
        item = self.listing()['items'][0]
        tracked = self.client.post('/api/student-support', json={'request_id': 'todo', 'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Missed Quiz Make-up', 'state': 'todo', 'next': next_step,
            **({'commitments': commitments} if commitments else {})}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        return tracked.json['id'], absence

    def close_todo(self, **answers):
        with self.jev(scripted(**answers)) as asked:
            return self.suggestions.close_decided_todo(), asked

    def test_a_to_do_resolves_once_the_absence_it_asks_to_decide_is_decided(self):
        task = {'id': 'record', 'title': 'Record the Excused decision', 'kind': 'work', 'state': 'open', 'waiting_on': 'you'}
        item_id, absence = self.todo([task])
        self.assertFalse(self.close_todo()[0])  # still undecided: nothing is asked
        self.decide(absence)
        seen = []
        with self.jev(scripted(seen=seen)):
            self.assertTrue(self.suggestions.close_decided_todo())
        [state] = seen
        self.assertEqual(state['to_do']['next_step'], 'Select Excused for the October 2 absence.')
        self.assertEqual(state['to_do']['open_tasks'], ['Record the Excused decision'])
        self.assertIn('was decided: Excused', state['decided'][0]['what'])
        self.assertEqual(self.state(item_id), 'resolved')
        shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
        self.assertEqual(shown['commitments'][0]['state'], 'completed')
        self.assertIn('was decided: Excused', shown['commitments'][0]['completion_note'])
        [row] = [d for d in shown['suggestions']['done'] if d['title'] == 'Marked this follow-up resolved']
        self.assertEqual((row['state'], row['undo']), ('done', True))
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_mail_sends').fetchone()[0], 0)  # nothing is sent
        undone = self.client.post(f"/api/student-support/followups/suggestions/{row['run']}/undo",
                                  json={'action_id': row['id'], 'seen': row['seen'], 'request_id': 'reopen'}, headers=HEADERS)
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual(self.state(item_id), 'todo')
        shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
        self.assertEqual(shown['commitments'][0]['state'], 'open')  # Undo puts its task back too
        self.assertFalse(self.close_todo()[0])  # undone once: theirs from then on

    def kept_and_asked_once(self, **answers):
        item_id, absence = self.todo(next_step='Decide the October 2 absence, then book the makeup.')
        self.decide(absence)
        closed, asked = self.close_todo(**answers)
        self.assertTrue(closed)
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(self.state(item_id), 'todo')
        with self.jev(AssertionError('asked again')):
            self.assertFalse(self.suggestions.close_decided_todo())

    def test_jev_unsure_keeps_the_to_do(self):
        self.kept_and_asked_once(arrived=.6)

    def test_work_still_owed_keeps_the_to_do(self):
        self.kept_and_asked_once(instructor=.7)

    def test_something_still_awaited_keeps_the_to_do(self):
        self.kept_and_asked_once(student=.7)

    def test_a_promised_reply_keeps_the_to_do_unasked(self):
        reply = {'id': 'reply', 'title': 'Write back about the makeup', 'kind': 'reply', 'state': 'open', 'waiting_on': 'you'}
        item_id, absence = self.todo([reply])
        self.decide(absence)
        with self.jev(AssertionError('asked')):
            self.assertFalse(self.suggestions.close_decided_todo())
        self.assertEqual(self.state(item_id), 'todo')

    def test_which_decisions_count_for_a_to_do(self):
        _, absence = self.todo()
        self.decide(absence)
        with connect(self.assistant.database) as db:
            decided_at = db.execute('SELECT MAX(created_at) FROM absence_revisions WHERE absence_id=?', (absence,)).fetchone()[0]
            after = (datetime.fromisoformat(decided_at) + timedelta(minutes=1)).isoformat()
            before = (datetime.fromisoformat(decided_at) - timedelta(minutes=1)).isoformat()
            item = {'enrollment_id': self.alice, 'updated_at': after, 'observed': [before, 'm']}
            self.assertEqual(evidence.decisions(db, item, 'workspace'), [])  # the instructor saved it knowing
            self.assertEqual(len(evidence.decisions(db, item, 'assistant')), 1)  # the round's own card, a moment before its To do
            self.assertEqual(evidence.decisions(db, {**item, 'observed': [after, 'm']}, 'assistant'), [])  # decided before they wrote

    # CHECK BACK ---------------------------------------------------------------------------------------------------------------

    def later(self, days):
        moment = campus_now() + timedelta(days=days)
        return patch('student_support.service.now', return_value=moment)

    def test_a_waiting_follow_up_comes_back_on_its_check_back_day_for_the_instructor(self):
        item_id, absence = self.waiting()
        shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
        set_on = campus_now().date()
        # "Their doctor's note for Oct 2" names a day already past when it was set: a week after.
        self.assertEqual(shown['check_back'], (set_on + timedelta(days=7)).isoformat())
        self.assertFalse(shown['attention'])
        self.assertEqual(shown['suggestions']['arrived'], [])  # nothing came in yet
        self.decide(absence)
        shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
        self.assertEqual([r['record'] for r in shown['suggestions']['arrived']], ['absence'])
        self.assertNotIn('arrived', json.dumps(next(i for i in self.listing(suggestions=False)['items'] if i['id'] == item_id)))
        with self.later(7):
            shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
            self.assertTrue(shown['attention'])
            source = {'current': True, 'memberships': [{'dropped': False}]}
            self.assertFalse(FollowupSuggestions.eligible(shown, source))  # never prepared by itself: the instructor's to look at
            self.assertTrue(FollowupSuggestions.eligible({**shown, 'state': 'needs_reply'}, source))
        # Wait a week: a date of the instructor's own, the next step kept.
        later = (set_on + timedelta(days=14)).isoformat()
        saved = self.client.post(f'/api/student-support/{item_id}', json={'title': shown['title'], 'details': '', 'date': later, 'state': 'waiting',
                                 'next': shown['next'], 'observed': shown['observed'], 'base_revision': shown['revision'], 'request_id': 'wait-a-week'},
                                 headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        with self.later(7):
            shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
            self.assertEqual((shown['date'], shown['next'], shown['attention']), (later, 'Their doctor’s note for Oct 2', False))
            self.assertNotIn('check_back', shown)

    def test_the_check_back_day_follows_the_latest_day_its_next_step_names(self):
        item = lambda next_step, at='2026-10-13T02:00:00+00:00': {'updated_at': at, 'next': next_step}
        # Set Mon Oct 12 at 9 pm campus time.
        self.assertEqual(check_back(item("Zane's paper copy of the take-home quiz in class Wednesday, October 14.")), '2026-10-15')
        self.assertEqual(check_back(item("Natalie's documentation before October 22. She books Exam 2 for Monday, October 26.")), '2026-10-27')
        self.assertEqual(check_back(item('A reply to your last message')), '2026-10-19')
        self.assertEqual(check_back(item('Her note for Oct 2')), '2026-10-19')  # a day already past
        self.assertEqual(check_back(item('Her note covering October 12, then decide the absence')), '2026-10-19')  # the day it was set: the absence, not a due day
        self.assertEqual(check_back(item('Her note by October 13')), '2026-10-14')  # the next day is still to come
        self.assertEqual(check_back(item('Her paper by 2/20')), '2026-12-11')  # never more than 60 days on
        self.assertEqual(check_back(item('Her paper Oct 14-16')), '2026-10-17')

