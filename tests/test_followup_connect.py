"""CONNECT A MESSAGE (2026-10-10, requirement, of Brenna Lyons's follow-up: asked in the Assistant chat "can you connect the
response card I sent to Brenna to her followup?", the chat found the card's email in her record and had no route to join it; "joins
shouldn't be instructor only"). The chat records the follow-up's own join (SAME MATTER) at once, naming the email by its message in
the student's record, and Keep separate the same way; a follow-up preparation does neither. Synthetic catalogs, scripted engine."""
import json
import uuid

from communications.record import record
from rosters import connect
from test_followup_suggestions import SuggestionsTests
from test_storage import HEADERS

JOIN = '/api/student-support/followups/join'
CARD = 'IMPORTANT: Book your Quiz 4 makeup for MATH 140'


class ConnectTests(SuggestionsTests):
    def chat(self):
        return self.assistant.open_session('scripted', 'Chat')

    def tool(self, session, name, args):
        result, _ = self.assistant.run_tool(session, name, args)
        return json.loads(result)

    def card(self, action_id):
        return self.assistant._view(*self.assistant.action(action_id))

    def email(self, ident, enrollment=None, subject=CARD, at='2026-10-10T17:13:15Z'):
        """An email Faculty sent the student in a Gmail thread of its own (a booking card's invitation), as the record captures it."""
        with self.rosters_lock(), connect(self.assistant.database) as db:
            record(db, 'gmail', ident, {'sent_at': at, 'author_role': 'instructor', 'author': {'name': 'Faculty'}, 'recipients': [],
                   'subject': subject, 'body_text': 'Pick a time on your card.', 'attachments': [], 'context': {'thread_id': ident}},
                   [(enrollment or self.alice, self.term, 'to', 'roster_address')], ident)
            db.commit()
            return db.execute('SELECT id FROM comm_messages WHERE external_id=?', (ident,)).fetchone()[0]

    def joins(self):
        with connect(self.assistant.database) as db:
            return [tuple(r) for r in db.execute('SELECT source_key,into_key,decision,decided_by FROM student_support_conversation_joins ORDER BY rowid')]

    def followup(self):
        return next(i for i in self.listing()['items'] if i['enrollment_id'] == self.alice)

    def test_the_chat_joins_a_sent_card_to_the_follow_up_at_once_and_separates_it_again(self):
        self.incoming()
        item = self.listing(suggestions=False)['items'][0]
        saved = self.client.post('/api/student-support', json={**item, 'state': 'waiting', 'title': 'Missed class due to concussion',
                                 'next': 'Her update after the reassessment', 'request_id': 'wait'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        mine, bobs = self.email('card'), self.email('bob-card', self.bob)
        session = self.chat()
        context = self.tool(session, 'student_context', {'enrollment_id': self.alice, 'source_key': 'gmail:b1'})
        # The chat finds the card's email in her record, with the message ID the join names it by.
        self.assertEqual([e['message_id'] for e in context['communications'] if e['subject'] == CARD], [mine])
        self.assertNotIn('joined', context)
        self.assertNotIn('answered', self.followup())
        named = {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'message_id': mine}
        asked = self.tool(session, 'join_followup', named)
        self.assertEqual((asked['outcome'], asked['category']), ('done', 'Follow-up conversations'), asked)
        self.assertEqual((asked['result']['source_key'], asked['result']['into_key']), ('gmail:card', 'gmail:b1'))
        done = self.card(asked['action_id'])  # recorded as the Assistant's own action, with what it did in plain words
        self.assertEqual((done['status'], done['tier']), ('succeeded', 'auto'))
        self.assertEqual(done['summary'], f'Join “{CARD}” to the follow-up “Missed class due to concussion”')
        self.assertEqual({k: v for k, v in done['body'].items() if k != 'request_id'},
                         {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'with_message_id': mine})
        uuid.UUID(done['body']['request_id'])  # Faculty's own, never the model's
        self.assertEqual(self.joins(), [('gmail:card', 'gmail:b1', 'join', 'instructor')])
        shown = self.followup()
        # The follow-up keeps its status and next step; the card's email is now the instructor's reply there, with Keep separate.
        self.assertEqual((shown['id'], shown['state'], shown['next']), (saved.json['id'], 'waiting', 'Her update after the reassessment'))
        self.assertEqual(shown['answered'][1], mine)
        self.assertEqual([(j['source_key'], j['title']) for j in shown['joins']], [('gmail:card', CARD)])
        # Asked again, or for what cannot be joined, the chat is told why and nothing is recorded.
        again = self.tool(session, 'join_followup', named)
        self.assertEqual(again['status'], 409, again)
        self.assertIn('already one follow-up', again['error'])
        other = self.tool(session, 'join_followup', {**named, 'message_id': bobs})
        self.assertEqual(other['status'], 400, other)
        self.assertIn("not in this student's communications record", other['error'])
        self.assertEqual(len(self.joins()), 1)
        # The student's case now lists what is joined, by the key Keep separate names; asked to undo it, the chat does at once.
        context = self.tool(session, 'student_context', {'enrollment_id': self.alice, 'source_key': 'gmail:b1'})
        self.assertEqual(context['joined'], [{'source_key': 'gmail:card', 'title': CARD, 'started': '2026-10-10T17:13:15.000Z'}])
        apart = self.tool(session, 'separate_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:card'})
        self.assertEqual((apart['outcome'], apart['result']['decision']), ('done', 'separate'), apart)
        self.assertEqual(self.joins()[-1], ('gmail:card', '', 'separate', 'instructor'))
        shown = self.followup()
        self.assertNotIn('answered', shown)
        self.assertNotIn('joins', shown)
        nothing = self.tool(session, 'separate_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:card'})
        self.assertEqual((nothing['outcome'], nothing['status']), ('failed', 409), nothing)
        self.assertEqual(self.client.get('/api/assistant/actions?pending=1').json['pending'], [])  # no card at any point

    def test_another_conversation_is_joined_by_its_key_and_a_review_only_chat_asks_first(self):
        self.incoming()
        self.incoming('f1', 'I will also miss Friday.', thread='friday', at='2026-10-06T19:41:00Z')
        named = {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'with_key': 'gmail:friday'}
        # A chat that only prepares changes for review does nothing by itself: there the join is a card.
        review = self.chat()
        review['_review_only'] = True
        asked = self.tool(review, 'join_followup', named)
        self.assertEqual(asked['outcome'], 'waiting_for_approval', asked)
        self.assertEqual(self.joins(), [])
        self.assertEqual(self.approve(self.card(asked['action_id'])).json['action']['status'], 'succeeded')
        self.assertEqual(self.joins(), [('gmail:friday', 'gmail:b1', 'join', 'instructor')])
        session = self.chat()
        self.assertEqual(self.tool(session, 'separate_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:friday'})['outcome'], 'done')
        self.assertEqual(self.tool(session, 'join_followup', named)['outcome'], 'done')
        self.assertEqual([row[2] for row in self.joins()], ['join', 'separate', 'join'])
        self.assertEqual(self.tool(session, 'join_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1'})['status'], 400)

    def test_a_follow_up_saved_under_a_joined_conversation_is_tracked_from_the_conversation_it_reads(self):
        """2026-10-15, Fiona Weaver's follow-up: saved under a Gmail thread later read as part of another conversation, the chat's
        track_followup was refused ("select a current conversation") and it asked the follow-up to prepare its suggestions again."""
        self.incoming()
        self.incoming('f1', 'I will also miss Friday.', thread='friday', at='2026-10-06T19:41:00Z')
        item = next(i for i in self.listing(suggestions=False)['items'] if i['source_key'] == 'gmail:friday')
        saved = self.client.post('/api/student-support', json={**item, 'state': 'todo', 'next': 'DR setup', 'request_id': 'friday'},
                                 headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        session = self.chat()
        self.assertEqual(self.tool(session, 'join_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                                                              'with_key': 'gmail:friday'})['outcome'], 'done')
        for key in ('gmail:b1', 'gmail:friday'):  # the conversation it reads, or the key the record keeps
            context = self.tool(session, 'student_context', {'enrollment_id': self.alice, 'source_key': key})
            self.assertEqual((context['source_key'], context['followup']['id']), ('gmail:b1', saved.json['id']), context)
        tracked = self.tool(session, 'track_followup', {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'state': 'waiting',
                                                         'next': 'Her reply about Friday'})
        self.assertNotIn('error', tracked, tracked)
        self.assertEqual(self.card(tracked['action_id'])['body']['base_revision'], saved.json['revision'])

    def test_resolving_from_the_chat_completes_the_follow_ups_open_tasks_with_what_finished_them(self):
        """2026-10-22, Mina Takeda's and Juliet Dane's Exam 2 makeup follow-ups: the chat proposed closing them as booked and
        emailed, and Approve all stopped at "finish or explicitly cancel the open commitments before resolving this follow-up"."""
        self.incoming()
        item = self.listing(suggestions=False)['items'][0]
        tasks = [{'id': 'arrange', 'title': 'Arrange the Exam 2 makeup', 'kind': 'work', 'state': 'open', 'waiting_on': 'you', 'depends_on': []},
                 {'id': 'details', 'title': 'Send her the makeup details', 'kind': 'reply', 'state': 'open', 'waiting_on': 'you',
                  'depends_on': ['arrange']}]
        saved = self.client.post('/api/student-support', json={**item, 'state': 'todo', 'next': 'Arrange the makeup, then write',
                                 'commitments': tasks, 'request_id': 'tasks'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        session = self.chat()
        named = {'enrollment_id': self.alice, 'source_key': 'gmail:b1', 'state': 'resolved'}
        refused = self.tool(session, 'track_followup', named)  # what finished them is never assumed
        self.assertIn('still has open tasks (Arrange the Exam 2 makeup; Send her the makeup details)', refused['error'])
        asked = self.tool(session, 'track_followup', {**named, 'done': 'Makeup booked for Oct 26; details emailed Oct 22.'})
        self.assertNotIn('error', asked, asked)
        card = self.card(asked['action_id'])
        self.assertEqual([(t['id'], t['state'], t['completion_note']) for t in card['body']['commitments']],
                         [('arrange', 'completed', 'Makeup booked for Oct 26; details emailed Oct 22.'),
                          ('details', 'completed', 'Makeup booked for Oct 26; details emailed Oct 22.')])
        if card['status'] != 'succeeded':
            approved = self.approve(card)
            self.assertEqual(approved.status_code, 200, approved.json)
        shown = self.followup()
        self.assertEqual(shown['state'], 'resolved')
        self.assertEqual({t['id']: t['state'] for t in shown['commitments']}, {'arrange': 'completed', 'details': 'completed'})

    def test_a_follow_up_preparation_never_joins_or_separates_conversations(self):
        self.incoming()
        mine = self.email('card')
        answers = []

        def respond(c, prompt):
            for name, args in (('join_followup', {'source_key': 'gmail:b1', 'message_id': mine}), ('separate_followup', {'source_key': 'gmail:card'})):
                result, _ = c.service.run_tool(c.session, name, {'enrollment_id': self.alice, **args})
                answers.append(json.loads(result))
            c.emit('assistant', text='Nothing to propose.')
        self.prepare(respond)
        self.assertEqual([('never joins or separates conversations' in a['error']) for a in answers], [True, True], answers)
        self.assertEqual(self.joins(), [])


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(ConnectTests(name) for name in ConnectTests.__dict__ if name.startswith('test_'))
