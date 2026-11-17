"""JEV SAME MATTER (TEACHING-WORKFLOWS / JEV, 2026-10-07): a new conversation a student starts joins the follow-up it continues when
Jev is confident, is asked about with one click when it is unsure, and stays its own otherwise. Scripted Jev answers only."""
import json
from unittest.mock import patch

from communications.record import record
from jev import client
from rosters import connect
from student_support import matching
from test_jev import response
from test_storage import HEADERS
from test_student_notes import Base


def answers(questions, first, second, first_p=None, second_p=None, confidence=(1.0, 1.0)):
    """Both orders of the same-matter Choice: each names its choice (an option prefix such as "F1" or "none"), with probabilities."""
    def pick(name, prefix, probabilities, sure):
        options = list(questions[name]['criteria'])
        chosen = next(o for o in options if o.startswith(prefix))
        probabilities = probabilities or {chosen: 1.0}
        full = {o: next((p for k, p in probabilities.items() if o.startswith(k)), 0.0) for o in options}
        return {'type': 'choice', 'choice': chosen, 'probabilities': full, 'confidence': sure}
    if 'same_matter' not in questions:
        return response(questions, {'kind': 'request'}, {'needs_action': .9})
    return {'model': client.MODEL, 'answers': {'same_matter': pick('same_matter', first, first_p, confidence[0]),
                                               'same_matter__reversed': pick('same_matter__reversed', second, second_p, confidence[1])}}


class SameMatterTests(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(patch.stopall)
        patch('jev.client.available', return_value=True).start()
        self.support = self.app.extensions['student_support']
        self.match = self.support.triage.match

    def incoming(self, ident, at, thread, body='About class', author='student', subject='Question', enrollment=None):
        with connect(self.app.config['DATABASE']) as db:
            record(db, 'gmail', ident, {'sent_at': at, 'author_role': author, 'author': {}, 'recipients': [], 'subject': subject,
                   'body_text': body, 'attachments': [], 'context': {'thread_id': thread}},
                   [(enrollment or self.alice, self.term, 'author' if author == 'student' else 'to', 'roster_address')], thread)
            db.commit()

    def page(self):
        response = self.client.get('/api/student-support', query_string={'kind': 'followup', 'suggestions': '1'})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json['items']

    def decisions(self):
        with connect(self.app.config['DATABASE']) as db:
            return [dict(r) for r in db.execute('SELECT source_key,into_key,decision,decided_by,call_id FROM student_support_conversation_joins ORDER BY rowid')]

    def run_match(self, *args, **kwargs):
        asked = []
        def evaluate(state, questions):
            asked.append((state, questions))
            return answers(questions, *args, **kwargs)
        with patch('jev.client.evaluate', side_effect=evaluate):
            worked = self.match.process_one()
        return worked, asked

    def this_week(self):
        """Yara Mercer's shape: a follow-up Waiting on the student for any more absence dates."""
        self.incoming('w1', '2026-10-05T16:47:00Z', 'week', 'There has been a death in my family; I will miss today and tomorrow.', subject='This Week')
        self.incoming('w2', '2026-10-05T20:41:00Z', 'week', 'Let me know if you need to miss any more days.', author='instructor', subject='Re: This Week')
        tracked = self.client.post('/api/student-support', json={'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:week',
            'observed': ['2026-10-05T16:47:00.000Z', self.comm('w1')], 'title': 'This Week', 'state': 'waiting',
            'next': 'Any additional absence dates', 'request_id': 'week'}, headers=HEADERS)
        self.assertEqual(tracked.status_code, 201, tracked.json)
        return tracked.json

    def comm(self, external):
        with connect(self.app.config['DATABASE']) as db:
            return db.execute('SELECT id FROM comm_messages WHERE external_id=?', (external,)).fetchone()[0]

    def test_a_confident_match_joins_the_new_email_with_keep_separate_under_done_for_you(self):
        week = self.this_week()
        self.incoming('f1', '2026-10-06T19:41:00Z', 'friday', 'I will be home Thursday afternoon, so I will miss Friday too.', subject='Friday Quiz/Class')
        worked, asked = self.run_match('F1', 'F1', confidence=(.95, .9))
        self.assertTrue(worked)
        [(state, questions)] = asked
        self.assertEqual(sorted(questions), ['same_matter', 'same_matter__reversed'])  # one request, both option orders
        self.assertEqual([f['title'] for f in state['followups']], ['This Week'])
        self.assertEqual(state['followups'][0]['next_step'], 'Any additional absence dates')
        self.assertIn('miss Friday', state['new_conversation']['messages'][-1]['text'])
        [row] = self.decisions()
        self.assertEqual((row['source_key'], row['into_key'], row['decision'], row['decided_by']), ('gmail:friday', 'gmail:week', 'join', 'jev'))
        with connect(self.app.config['DATABASE']) as db:  # the receipt keeps no message text
            saved = json.dumps(dict(db.execute('SELECT * FROM jev_calls WHERE id=?', (row['call_id'],)).fetchone()))
        self.assertNotIn('miss Friday', saved)
        [item] = self.page()  # one follow-up, reopened by her new email
        self.assertEqual((item['id'], item['state'], item['new_reply'], item['observed'][1]), (week['id'], 'needs_reply', True, self.comm('f1')))
        [done] = [d for d in item['suggestions'].get('done', []) if d.get('separate')]
        self.assertEqual((done['title'], done['separate']), ('Joined “Friday Quiz/Class” to this follow-up', 'gmail:friday'))
        self.assertFalse(self.run_match('F1', 'F1')[0])  # decided: never asked again
        apart = self.client.post('/api/student-support/followups/separate', json={'enrollment_id': self.alice, 'source_key': 'gmail:friday',
                                 'request_id': 'apart'}, headers=HEADERS)
        self.assertEqual(apart.status_code, 200, apart.json)
        self.assertEqual(sorted(i['source_key'] for i in self.page()), ['gmail:friday', 'gmail:week'])
        self.assertFalse(self.run_match('F1', 'F1')[0])  # kept separate: Jev does not ask about it again

    def test_an_unsure_match_asks_with_one_click_and_none_stays_its_own(self):
        self.this_week()
        self.incoming('f1', '2026-10-06T19:41:00Z', 'friday', 'About Friday.', subject='Friday Quiz/Class')
        worked, _ = self.run_match('F1', 'none', {'F1': .6, 'none': .4}, {'none': .55, 'F1': .45}, (.2, .1))
        self.assertTrue(worked)
        self.assertEqual(self.decisions()[0]['decision'], 'ask')
        found = {i['source_key']: i for i in self.page()}
        self.assertEqual(found['gmail:friday']['match']['into_key'], 'gmail:week')
        self.assertEqual(found['gmail:friday']['match']['title'], 'This Week')
        self.assertNotIn('match', {i['source_key']: i for i in self.client.get('/api/student-support?kind=followup').json['items']}['gmail:friday'])
        joined = self.client.post('/api/student-support/followups/join', json={'enrollment_id': self.alice, 'source_key': 'gmail:friday',
                                  'with_key': 'gmail:week', 'request_id': 'join'}, headers=HEADERS)
        self.assertEqual(joined.status_code, 200, joined.json)
        [item] = self.page()
        self.assertEqual([j['by'] for j in item['joins']], ['instructor'])
        # A later email about another matter: both orders say none, so it is a follow-up of its own and never asked about again.
        self.incoming('g1', '2026-10-07T09:00:00Z', 'grade', 'Why did I lose a point on Quiz 3?', subject='Quiz 3 grade')
        worked, _ = self.run_match('none', 'none', confidence=(.9, .8))
        self.assertTrue(worked)
        self.assertEqual(self.decisions()[-1]['decision'], 'none')
        self.assertEqual(sorted(i['source_key'] for i in self.page()), ['gmail:grade', 'gmail:week'])
        self.assertNotIn('match', {i['source_key']: i for i in self.page()}['gmail:grade'])

    def test_candidates_are_the_students_open_or_recent_follow_ups_that_started_earlier(self):
        with patch('student_support.service.timestamp', return_value='2026-09-01T12:00:00+00:00'):
            self.incoming('old', '2026-09-01T10:00:00Z', 'old', 'Old question')
            self.client.post('/api/student-support', json={'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:old',
                'observed': ['2026-09-01T10:00:00.000Z', self.comm('old')], 'title': 'Old', 'state': 'resolved', 'request_id': 'old'}, headers=HEADERS)
        self.this_week()
        self.incoming('b1', '2026-10-06T08:00:00Z', 'bob', 'Bob asks', enrollment=self.bob)
        self.incoming('f1', '2026-10-06T19:41:00Z', 'friday', 'About Friday.')
        with connect(self.app.config['DATABASE']) as db:
            waiting = {item['source_key']: [key for key, _ in chosen] for item, chosen in self.match.pending(db)}
        # Resolved more than 14 days before the new email started: not a candidate; Bob's conversations are never Alice's.
        self.assertEqual(waiting, {'gmail:friday': ['gmail:week']})
        # A failed call finds nothing and is never resent.
        with patch('jev.client.evaluate', side_effect=client.DecisionError('broken')) as evaluate:
            self.assertTrue(self.match.process_one())
            self.assertFalse(self.match.process_one())
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual([d['decision'] for d in self.decisions()], ['none'])

    def test_the_match_comes_before_the_conversation_analysis(self):
        self.this_week()
        self.incoming('f1', '2026-10-06T19:41:00Z', 'friday', 'About Friday.')
        triage = self.support.triage
        triage._last = 0
        with patch('jev.client.evaluate', side_effect=lambda state, questions: answers(questions, 'F1', 'F1')) as evaluate:
            self.assertTrue(triage.process_one())
        self.assertEqual(sorted(evaluate.call_args.args[1]), ['same_matter', 'same_matter__reversed'])
        self.assertEqual(self.decisions()[0]['decision'], 'join')
        with connect(self.app.config['DATABASE']) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jev_calls WHERE purpose='student-triage'").fetchone()[0], 0)

    def test_a_decision_made_meanwhile_wins(self):
        self.this_week()
        self.incoming('f1', '2026-10-06T19:41:00Z', 'friday', 'About Friday.')
        with connect(self.app.config['DATABASE']) as db:
            [(item, chosen)] = self.match.pending(db)
        self.client.post('/api/student-support/followups/join', json={'enrollment_id': self.alice, 'source_key': 'gmail:friday',
                         'with_key': 'gmail:week', 'request_id': 'mine'}, headers=HEADERS)
        receipt = {'id': 'call-1', 'status': 'complete', 'input_sha256': '0' * 64,
                   'answers': {'same_matter': {'choice': matching.NONE, 'confidence': 1.0, 'probabilities': {matching.NONE: 1.0}},
                               'same_matter__reversed': {'choice': matching.NONE, 'confidence': 1.0, 'probabilities': {matching.NONE: 1.0}}}}
        self.assertIsNone(self.match.record(item, receipt, {}))
        self.assertEqual([(d['decision'], d['decided_by']) for d in self.decisions()], [('join', 'instructor')])
