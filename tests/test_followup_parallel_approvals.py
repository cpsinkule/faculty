"""ASSISTANT "APPROVALS SIDE BY SIDE" (requirement, 2026-10-31): two students' follow-ups finalize at once; one student's wait for
each other; a Decline while a card's preview runs stands; lanes never wait for each other."""
import threading
import time
from unittest.mock import patch

from assistant import lanes as lanes_module
from assistant.lanes import Lanes, SERIAL
from rosters import connect
from test_followup_suggestions import SuggestionsTests, ScriptedEngine, proposing, MAIL
from test_storage import HEADERS


def meeting(hour):
    return ('/api/student-support', lambda c: {'enrollment_id': c.run['enrollment_id'], 'kind': 'appointment', 'title': 'Discuss the question',
            'date': '2026-10-12', 'time': f'{hour}:00', 'end_time': f'{hour}:30'}, 'Schedule office hours to discuss the question')


class ParallelApprovalTests(SuggestionsTests):
    def two_students(self):
        """A prepared follow-up for Alice and one for Bob, each with an office-hours card (at different times)."""
        self.incoming()
        self.incoming(ident='c1', thread='c', enrollment=self.bob, body='Another question', at='2026-10-03T12:10:00Z')
        while self.suggestions.process_one():
            pass
        engine = ScriptedEngine(lambda c, prompt: proposing(meeting(14 if c.run['enrollment_id'] == self.alice else 15))(c, prompt))
        with patch('assistant.conversations.engine_for', return_value=engine):
            for _ in range(2):
                self.assertTrue(self.suggestions.dispatch_one())
                self.wait()
        runs = {i['enrollment_id']: i['suggestions'] for i in self.listing()['items']}
        self.assertEqual({runs[self.alice]['state'], runs[self.bob]['state']}, {'ready'})
        return runs[self.alice], runs[self.bob]

    def approving(self, action):
        """Approves in a thread of its own (its own client), as a second browser request would."""
        answers = []
        thread = threading.Thread(target=lambda: answers.append(self.app.test_client().post(
            '/api/assistant/actions/' + action['id'] + '/approve', json={'seen': action['seen']}, headers=HEADERS)))
        thread.start()
        return thread, answers

    def holding(self, enrollment):
        """The appointment request of this student waits inside its submission until released."""
        entered, release = threading.Event(), threading.Event()
        original = self.assistant.transport.request
        def request(method, path, body=None):
            if method == 'POST' and path == '/api/student-support' and (body or {}).get('enrollment_id') == enrollment \
                    and (body or {}).get('kind') == 'appointment':
                entered.set()
                release.wait(20)
            return original(method, path, body)
        return entered, release, patch.object(self.assistant.transport, 'request', side_effect=request)

    def test_another_students_follow_up_finalizes_while_one_is_still_being_carried_out(self):
        alice, bob = self.two_students()
        entered, release, slow = self.holding(self.alice)
        with slow:
            thread, answers = self.approving(alice['actions'][0])
            try:
                self.assertTrue(entered.wait(10))
                began = time.monotonic()
                done = self.approve(bob['actions'][0])
                self.assertLess(time.monotonic() - began, 5)
                self.assertEqual(done.status_code, 200, done.json)
                self.assertEqual(done.json['action']['status'], 'succeeded')
                self.assertEqual(self.view(alice['actions'][0]['id'])['status'], 'approved')  # still being carried out
            finally:
                release.set()
                thread.join(20)
        self.assertEqual(answers[0].status_code, 200, answers[0].json)
        self.assertEqual(answers[0].json['action']['status'], 'succeeded')
        with connect(self.assistant.database) as db:
            made = sorted(r[0] for r in db.execute("SELECT enrollment_id FROM student_support_items WHERE kind='appointment'"))
        self.assertEqual(made, sorted([self.alice, self.bob]))  # each once
        self.assertEqual(self.assistant.approving(), 0)

    def test_one_students_approvals_still_wait_for_each_other(self):
        self.incoming()
        run, _ = self.prepare(proposing(meeting(14), meeting(16)))
        first, second = run['actions']
        entered, release, slow = self.holding(self.alice)
        with slow:
            thread, answers = self.approving(first)
            try:
                self.assertTrue(entered.wait(10))
                other, later = self.approving(second)
                time.sleep(.5)
                self.assertTrue(other.is_alive())  # waits for Alice's lane, not refused
                self.assertTrue(self.view(second['id'])['pending'])
                self.assertEqual(self.assistant.approving(), 2)  # the restart check counts the one waiting its turn
            finally:
                release.set()
                thread.join(20)
                other.join(20)
        self.assertEqual([answers[0].status_code, later[0].status_code], [200, 200], (answers[0].json, later[0].json))
        self.assertEqual({self.view(a['id'])['status'] for a in (first, second)}, {'succeeded'})

    def test_a_decline_during_the_preview_stands_and_nothing_is_sent(self):
        fake = self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        card = run['actions'][0]
        entered, release = threading.Event(), threading.Event()
        original = self.assistant._preview
        def preview(*args, **kwargs):
            entered.set()
            release.wait(20)
            return original(*args, **kwargs)
        with patch.object(self.assistant, '_preview', side_effect=preview):
            thread, answers = self.approving(card)
            try:
                self.assertTrue(entered.wait(10))
                began = time.monotonic()
                declined = self.client.post(f"/api/assistant/actions/{card['id']}/decline", json={}, headers=HEADERS)
                self.assertLess(time.monotonic() - began, 5)  # the approval lock is not held through the preview
                self.assertEqual(declined.status_code, 200, declined.json)
            finally:
                release.set()
                thread.join(20)
        self.assertEqual(answers[0].status_code, 409, answers[0].json)
        self.assertEqual(self.view(card['id'])['status'], 'declined')
        self.assertEqual(fake.sent, [])
        with connect(self.assistant.database) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM gmail_mail_sends').fetchone()[0], 0)

    def test_approve_all_of_two_students_runs_side_by_side(self):
        alice, bob = self.two_students()
        entered, release, slow = self.holding(self.alice)
        answers = []
        def approve_all(run):
            answers.append(self.app.test_client().post(f"/api/student-support/followups/suggestions/{run['id']}/approve",
                json={'seen': {a['id']: a['seen'] for a in run['actions']}}, headers=HEADERS))
        with slow:
            thread = threading.Thread(target=approve_all, args=(alice,))
            thread.start()
            try:
                self.assertTrue(entered.wait(10))
                began = time.monotonic()
                approve_all(bob)
                self.assertLess(time.monotonic() - began, 5)
                self.assertEqual(answers[0].status_code, 200, answers[0].json)
                self.assertEqual(answers[0].json['remaining'], [])
            finally:
                release.set()
                thread.join(20)
        self.assertEqual(answers[1].status_code, 200, answers[1].json)
        self.assertEqual({self.view(a['id'])['status'] for a in alice['actions'] + bob['actions']}, {'succeeded'})

    def test_lanes_follow_the_student_and_the_route(self):
        self.incoming()
        run, _ = self.prepare(proposing(meeting(14)))
        card = run['actions'][0]
        session = self.assistant.open_session('test')
        chat = self.tool_write(session, '/api/calendar/events', {'title': 'Meeting', 'date': '2026-10-03', 'time': '14:00'})
        with connect(self.assistant.database) as db:
            self.assertEqual(lanes_module.lanes(db, [card['id']]), {'student:' + self.alice})
            self.assertEqual(lanes_module.lanes(db, [chat['action_id']]), {SERIAL})  # not a follow-up's: one at a time
            self.assertEqual(lanes_module.lanes(db, ['missing']), {SERIAL})
            with patch.object(lanes_module, 'PARALLEL', frozenset()):  # a route not shown safe stays one at a time too
                self.assertEqual(lanes_module.lanes(db, [card['id']]), {'student:' + self.alice, SERIAL})

    def test_a_lane_is_never_waited_for_while_another_is_held(self):
        lanes = Lanes()
        with lanes.hold({'student:a', SERIAL}):
            with lanes.hold({'student:a'}):  # already held: an Approve all's own card
                pass
            with self.assertRaises(RuntimeError):
                with lanes.hold({'student:b'}):
                    pass
        self.assertEqual(lanes._locks, {})  # nothing kept once nobody holds or waits
        taken = []
        def other_student():
            with lanes.hold({'student:b'}):
                taken.append(True)
        with lanes.hold({'student:a'}):
            other = threading.Thread(target=other_student)
            other.start()
            other.join(5)
        self.assertEqual(taken, [True])  # another student's lane is free meanwhile

    def test_an_unchanged_follow_up_is_checked_without_the_lifecycle_lock(self):
        self.incoming()
        run, _ = self.prepare(proposing(meeting(14)))
        held, original = [], self.suggestions.unchanged
        def unchanged(db, run, items=None):
            held.append(bool(getattr(self.app.extensions['rosters']._lock_state, 'held', False)))
            return original(db, run, items)
        with patch.object(self.suggestions, 'unchanged', side_effect=unchanged):
            self.assertEqual(self.approve(run['actions'][0]).status_code, 200)
        self.assertTrue(held)
        self.assertNotIn(True, held)


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(ParallelApprovalTests(name) for name in ParallelApprovalTests.__dict__ if name.startswith('test_'))
