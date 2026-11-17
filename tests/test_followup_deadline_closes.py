"""TEACHING-WORKFLOWS / ABSENCES DEADLINE CLOSES: a follow-up Waiting on student for an absence's documentation is resolved by itself
once that absence's documentation window closes (Not excused automatically), with Undo.

Synthetic catalogs and scripted engines only. No configured model, mailbox or outside service is reached.
"""
import json
from unittest.mock import patch

import test_followup_suggestions as suggestions  # its helpers, not its test case (imported as a module so it is not run twice)
from rosters import connect
from test_storage import HEADERS
from test_student_notes import Base



class DeadlineClosesTests(Base):
    # Borrowed helpers (no module-level name for that test case, so unittest never collects it here).
    incoming, rosters_lock, listing = suggestions.SuggestionsTests.incoming, suggestions.SuggestionsTests.rosters_lock, suggestions.SuggestionsTests.listing
    wait, approve = suggestions.SuggestionsTests.wait, suggestions.SuggestionsTests.approve
    notice, tool_write = suggestions.SuggestionsTests.notice, suggestions.SuggestionsTests.tool_write

    def session(self, body):
        """A preparation of the student's message, as dispatch_one starts it, and its proposal-only session."""
        self.incoming(body=body)
        self.suggestions.process_one()
        run = self.listing()['items'][0]['suggestions']
        with connect(self.assistant.database) as db:  # ASSISTANT CONTEXT: a preparation records its sources as it starts
            self.suggestions.context.remember(db, self.suggestions.get(db, run['id']))
        session = self.assistant.open_session('test')
        session.update(_proposal_only=True, _followup_run=run['id'])
        return session

    def setUp(self):
        super().setUp()
        self.assistant = self.app.extensions['assistant']
        self.support = self.app.extensions['student_support']
        self.suggestions = self.assistant.followups
        self.suggestions.interval = 0
        self.suggestions.automatic = False  # the preparation's absence is a card the instructor approves
        self.model = patch('assistant.conversations.config.assistant', return_value={'kind': 'hosted', 'name': 'Scripted', 'reasoning_choice': 'high'})
        self.model.start()
        self.addCleanup(self.model.stop)
        self.addCleanup(self.wait)

    def record_absence(self, session, first='2026-10-02', reason='illness'):
        """The preparation's own absence card, approved: an undecided absence this conversation recorded."""
        section = self.listing()['items'][0]['student']['section_id']
        card = self.tool_write(session, '/api/absences', {'section_id': section, 'enrollment_id': self.alice, 'status': 'pending',
                                                          'first_date': first, 'reason': reason, 'notice_message_id': self.notice()})
        approved = self.approve(self.assistant._view(*self.assistant.action(card['action_id'])))
        self.assertEqual(approved.status_code, 200, approved.json)
        with connect(self.assistant.database) as db:
            return db.execute('SELECT id FROM absences ORDER BY rowid DESC LIMIT 1').fetchone()[0]

    def wait_on_student(self, request_id='wait'):
        item = self.listing()['items'][0]
        tracked = self.client.post('/api/student-support', json={'request_id': request_id, 'kind': 'followup', 'enrollment_id': self.alice,
            'source_key': item['source_key'], 'observed': item['observed'], 'title': 'Missed Quiz Make-up', 'state': 'waiting',
            'next': 'Their doctor’s note for Oct 2'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        return tracked.json['id']

    def state(self, item_id):
        with connect(self.assistant.database) as db:
            return json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? ORDER BY revision DESC LIMIT 1',
                                         (item_id,)).fetchone()[0])['state']

    def expire(self, today='2026-10-08'):
        """ABSENCES DECISION DEADLINE: Fri Oct 2's documentation was due by the end of Wed Oct 7."""
        return self.app.extensions['absences'].expire_one(today=today)

    def test_the_follow_up_waiting_for_documentation_resolves_once_its_absence_is_not_excused_automatically(self):
        # 
        session = self.session(body='I crashed my bike and missed the quiz on Friday. Can I make it up?')
        absence = self.record_absence(session)
        item_id = self.wait_on_student()
        self.assertFalse(self.suggestions.close_past_deadline())  # the window is still open
        self.assertFalse(self.expire(today='2026-10-07'))  # Wed: still the third business day
        self.assertFalse(self.suggestions.close_past_deadline())
        self.assertTrue(self.expire())
        self.assertEqual(self.client.get('/api/absences/' + absence).json['status'], 'not_excused')
        self.assertTrue(self.suggestions.close_past_deadline())
        self.assertFalse(self.suggestions.close_past_deadline())  # once
        self.assertEqual(self.state(item_id), 'resolved')
        shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
        self.assertFalse(shown['attention'])
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
        self.assertFalse(self.suggestions.close_past_deadline())  # undone once: theirs from then on

    def test_only_a_follow_up_still_waiting_from_before_the_decision_on_its_own_absence_is_closed(self):
        session = self.session(body='I was sick on Friday and missed the quiz.')
        self.record_absence(session)
        self.assertTrue(self.expire())
        item_id = self.wait_on_student()  # set waiting after the automatic decision: the instructor knew, so it waits for them
        self.assertFalse(self.suggestions.close_past_deadline())
        self.assertEqual(self.state(item_id), 'waiting')

    def test_a_newer_student_message_or_another_undecided_absence_keeps_it_waiting(self):
        session = self.session(body='I was sick on Friday and missed the quiz.')
        self.record_absence(session)
        self.record_absence(session, first='2026-10-06')  # a later day of the same conversation, still in its window on Oct 8
        item_id = self.wait_on_student()
        self.assertTrue(self.expire())
        self.assertFalse(self.expire())  # the Oct 6 absence is not due yet
        self.assertFalse(self.suggestions.close_past_deadline())  # one of the conversation's absences is still undecided
        self.assertTrue(self.expire(today='2026-10-12'))  # Tue Oct 6's window closed at the end of Fri Oct 9
        self.incoming(ident='a2', body='Here is my doctor’s note.', at='2026-10-09T15:00:00Z')
        self.assertFalse(self.suggestions.close_past_deadline())  # the student wrote again: back to Needs my reply, theirs
        self.assertEqual(self.state(item_id), 'waiting')

    def test_a_message_you_answered_still_keeps_it_from_closing(self):
        # ANSWERED (2026-10-13): a new message the instructor answered leaves the follow-up Waiting on the page, and it is still
        # "the student wrote since": the closed window never resolves a follow-up whose student had written.
        session = self.session(body='I was sick on Friday and missed the quiz.')
        self.record_absence(session)
        item_id = self.wait_on_student()
        self.incoming(ident='a2', body='Here is my doctor’s note.', at='2026-10-07T15:00:00Z')
        self.incoming(ident='a3', body='Thank you, I have it.', at='2026-10-07T16:00:00Z', author='instructor')
        shown = next(i for i in self.listing()['items'] if i['id'] == item_id)
        self.assertEqual((shown['state'], shown['new_reply'], shown['new_reply_answered']), ('waiting', False, True))
        self.assertTrue(self.expire())
        self.assertFalse(self.suggestions.close_past_deadline())
        self.assertEqual(self.state(item_id), 'waiting')

    def test_an_absence_another_conversation_recorded_leaves_this_follow_up_alone(self):
        self.session(body='Could I meet you?')
        item_id = self.wait_on_student()
        self.incoming(ident='b1', body='I was sick on Friday.', thread='other', at='2026-10-03T13:00:00Z')
        section = self.listing()['items'][0]['student']['section_id']
        recorded = self.client.post('/api/absences', json={'section_id': section, 'enrollment_id': self.alice, 'first_date': '2026-10-02',
                                    'reason': 'illness', 'notice_message_id': self.notice('b1'), 'request_id': 'other-absence'}, headers=HEADERS)
        self.assertEqual(recorded.status_code, 201, recorded.json)
        self.assertTrue(self.expire())
        self.assertFalse(self.suggestions.close_past_deadline())
        self.assertEqual(self.state(item_id), 'waiting')


if __name__ == '__main__':
    import unittest
    unittest.main()
