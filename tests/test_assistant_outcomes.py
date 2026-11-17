"""Local receipt interpretation: synthetic SQLite only, no outside services."""
import json
import sqlite3
import unittest
from types import SimpleNamespace

from assistant import outcomes
from assistant.outcomes import Outcomes, link, of_view, settled
from assistant.followup_plan import cancelled, problem, running, unsettled
from assistant.service import AssistantService, _went_through


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.reader = Outcomes(db=self.db)

    def test_later_push_receipt_settles_an_unchanged_action_event(self):
        self.db.execute("CREATE TABLE canvas_content_pushes(id TEXT, status TEXT, progress_json TEXT DEFAULT '{}')")
        self.db.execute("INSERT INTO canvas_content_pushes(id,status) VALUES('push','running')")
        event = {'result': {'id': 'push', 'status': 'running'}}
        self.assertEqual(self.reader.read('canvas.content_push', 'succeeded', event), 'started')
        self.db.execute("UPDATE canvas_content_pushes SET status='done'")
        self.assertEqual(self.reader.read('canvas.content_push', 'succeeded', event), 'succeeded')
        self.db.execute('UPDATE canvas_content_pushes SET progress_json=?', (json.dumps({'courses': {'one': {'done': 2, 'failed': 1}}}),))
        self.assertEqual(self.reader.read('canvas.content_push', 'succeeded', event), 'partial')
        self.db.execute("UPDATE canvas_content_pushes SET status='interrupted'")
        self.assertEqual(self.reader.read('canvas.content_push', 'succeeded', event), 'uncertain')
        self.assertEqual(event['result']['status'], 'running')
        # Ended early with objects already made in Canvas: part of it was done.
        for ended in ('failed', 'cancelled'):
            self.db.execute('UPDATE canvas_content_pushes SET status=?', (ended,))
            self.assertEqual(self.reader.read('canvas.content_push', 'succeeded', event), 'partial')
        self.db.execute("UPDATE canvas_content_pushes SET progress_json='{}'")
        self.assertEqual(self.reader.read('canvas.content_push', 'succeeded', event), 'cancelled')

    def test_canvas_message_needs_all_delivery_receipts_and_never_resends(self):
        self.db.executescript('''CREATE TABLE canvas_message_deliveries(id TEXT, message_id TEXT);
            CREATE TABLE canvas_message_outcomes(id INTEGER PRIMARY KEY, delivery_id TEXT, status TEXT);
            INSERT INTO canvas_message_deliveries VALUES('a','message'),('b','message');
            INSERT INTO canvas_message_outcomes(delivery_id,status) VALUES('a','accepted');''')
        event = {'result': {'id': 'message', 'status': 'sending'}}
        self.assertEqual(self.reader.read('canvas.message_send', 'succeeded', event), 'started')
        # A course Canvas did not confirm is not known to have missed it: uncertain, never "partly", which reads as known.
        self.db.execute("INSERT INTO canvas_message_outcomes(delivery_id,status) VALUES('b','uncertain')")
        self.assertEqual(self.reader.read('canvas.message_send', 'succeeded', event), 'uncertain')
        self.db.execute("INSERT INTO canvas_message_outcomes(delivery_id,status) VALUES('b','failed')")
        self.assertEqual(self.reader.read('canvas.message_send', 'succeeded', event), 'partial')
        self.db.execute("INSERT INTO canvas_message_outcomes(delivery_id,status) VALUES('b','accepted')")
        self.assertEqual(self.reader.read('canvas.message_send', 'succeeded', event), 'succeeded')

    def test_regrade_finished_flag_does_not_turn_a_stopped_step_into_success(self):
        self.db.execute('CREATE TABLE frq_regrade_applies(id TEXT)')
        self.db.execute("INSERT INTO frq_regrade_applies VALUES('apply')")
        projected = {'finished': True, 'steps': [{'status': 'done'}, {'status': 'stopped'}]}
        panel = SimpleNamespace(_view=lambda db, row: projected)
        reader = Outcomes(SimpleNamespace(app=SimpleNamespace(extensions={'regrade_panel': panel})), self.db)
        event = {'result': {'status': 'started', 'apply': {'id': 'apply'}}}
        self.assertEqual(reader.read('frq_regrade_panel.decide', 'succeeded', event), 'partial')
        projected['steps'][1]['status'] = 'done'
        self.assertEqual(reader.read('frq_regrade_panel.decide', 'succeeded', event), 'succeeded')
        # Cancelled after the grade was written is part done; cancelled before anything was done is cancelled.
        projected['steps'][1]['status'] = 'cancelled'
        self.assertEqual(reader.read('frq_regrade_panel.decide', 'succeeded', event), 'partial')
        projected['steps'][0]['status'] = 'cancelled'
        self.assertEqual(reader.read('frq_regrade_panel.decide', 'succeeded', event), 'cancelled')
        projected['steps'][0]['status'] = 'stopped'
        self.assertEqual(reader.read('frq_regrade_panel.decide', 'succeeded', event), 'cancelled')

    def test_grade_send_uses_the_linked_cell_receipt(self):
        self.db.execute('CREATE TABLE gradebook_canvas_sent(send_id TEXT, state TEXT, created_at TEXT)')
        event = {'result': {'send': {'id': 'send', 'status': 'running'}}}
        self.assertEqual(self.reader.read('gradebook.canvas_cell_send', 'succeeded', event), 'started')
        self.db.execute("INSERT INTO gradebook_canvas_sent VALUES('send','accepted','now')")
        self.assertEqual(self.reader.read('gradebook.canvas_cell_send', 'succeeded', event), 'succeeded')

    def test_work_still_running_is_neither_done_nor_a_problem_and_accepts_its_later_projection(self):
        action = {'endpoint': 'canvas.content_push', 'pending': False, 'status': 'succeeded',
                  'events': [{'detail': {'result': {'status': 'running'}}}]}
        # Not done: what depends on it waits. Not a problem: nothing is stopped, held or flagged for it.
        self.assertEqual((running(action), problem(action), unsettled([action])), (True, False, True))
        self.assertTrue(_went_through(action))  # already approved: never submitted again
        action['outcome'] = 'succeeded'
        self.assertEqual((running(action), problem(action), unsettled([action])), (False, False, False))
        self.assertTrue(_went_through(action))
        self.assertEqual(of_view(action), 'succeeded')
        for outcome in ('failed', 'uncertain', 'partial'):
            action['outcome'] = outcome
            self.assertEqual((running(action), problem(action), _went_through(action)), (False, True, False), outcome)
        # What the instructor cancelled before anything was done holds nothing back, as a declined step.
        action['outcome'] = 'cancelled'
        self.assertEqual((cancelled(action), running(action), problem(action), unsettled([action])), (True, False, False, False))
        self.assertEqual(self.reader.read('absences.create', 'succeeded', {'result': {'status': 'pending'}}), 'succeeded')
        self.assertEqual(self.reader.read('workspace_tools.create', 'succeeded', {'result': {'status': 'failed'}}), 'failed')

    def test_confirmation_sql_can_pass_unsettled_candidates_before_its_limit(self):
        self.db.executescript('''CREATE TABLE assistant_actions(id TEXT, endpoint TEXT);
            CREATE TABLE assistant_action_events(action_id TEXT, kind TEXT, detail_json TEXT);''')
        for i, status in enumerate(['sending'] * 20 + ['accepted']):
            self.db.execute('INSERT INTO assistant_actions VALUES(?,?)', (str(i), 'gmail_mail.send'))
            self.db.execute('INSERT INTO assistant_action_events VALUES(?,?,?)',
                            (str(i), 'succeeded', json.dumps({'result': {'status': status}})))
        with self.reader.sql() as confirmed:
            rows = self.db.execute(f'''SELECT a.id FROM assistant_actions a JOIN assistant_action_events e
                ON e.action_id=a.id WHERE {confirmed} ORDER BY a.rowid LIMIT 1''').fetchall()
        self.assertEqual([r[0] for r in rows], ['20'])
        with self.assertRaises(sqlite3.OperationalError):
            self.db.execute("SELECT assistant_confirmed('gmail_mail.send','succeeded','{}')")

    def test_a_result_too_long_to_keep_whole_still_names_its_receipts(self):
        self.db.executescript("""CREATE TABLE canvas_message_deliveries(id TEXT, message_id TEXT);
            CREATE TABLE canvas_message_outcomes(id INTEGER PRIMARY KEY, delivery_id TEXT, status TEXT);
            INSERT INTO canvas_message_deliveries VALUES('a','message');
            INSERT INTO canvas_message_outcomes(delivery_id,status) VALUES('a','accepted');""")
        kept = AssistantService._brief({'id': 'message', 'status': 'sending', 'body': 'x' * 5000, 'courses': [{'id': 1}]})
        self.assertEqual((kept['id'], kept['status'], len(kept['summary'])), ('message', 'sending', 4003))
        self.assertNotIn('body', kept)
        self.assertEqual(self.reader.read('canvas.message_send', 'succeeded', {'result': kept}), 'succeeded')
        short = {'id': 'message', 'status': 'sending', 'body': 'short'}
        self.assertEqual(AssistantService._brief(short), short)  # a result that fits is kept as it was
        self.assertEqual(link({'apply': {'id': 'run', 'steps': [1]}, 'send': {'id': 's', 'status': 'done', 'rows': []}, 'receipt': {'state': 'accepted'},
                               'status': {'not': 'a word'}}),
                         {'apply': {'id': 'run'}, 'send': {'id': 's', 'status': 'done'}, 'receipt': {'state': 'accepted'}})
        self.assertEqual(link(['not', 'a', 'mapping']), {})

    def test_a_gradescope_assignment_is_made_only_once_every_course_has_it(self):
        self.db.executescript('''CREATE TABLE frq_gradescope_creations(id TEXT, status TEXT);
            CREATE TABLE frq_gradescope_creation_units(send_id TEXT, course_id TEXT, status TEXT);
            INSERT INTO frq_gradescope_creations VALUES('made','pending');
            INSERT INTO frq_gradescope_creation_units VALUES('made','507','pending'),('made','508','pending');''')
        event = {'result': {'creation_id': 'made', 'request_id': 'r', 'status': 'pending', 'title': 'Quiz 5'}}
        read = lambda: self.reader.read('frq_gradescope.create', 'succeeded', event)  # noqa: E731
        both = lambda creation, *units: [self.db.execute('UPDATE frq_gradescope_creations SET status=?', (creation,))] + [  # noqa: E731
            self.db.execute('UPDATE frq_gradescope_creation_units SET status=? WHERE course_id=?', pair) for pair in zip(units, ('507', '508'))]
        self.assertEqual(read(), 'started')  # approved, and the route only queued it
        both('running', 'complete', 'sending')
        self.assertEqual(read(), 'started')
        both('complete', 'complete', 'complete')
        self.assertEqual(read(), 'succeeded')
        both('complete', 'complete', 'failed')  # the sender ended; one course's assignment stopped
        self.assertEqual(read(), 'partial')
        both('complete', 'failed', 'failed')
        self.assertEqual(read(), 'failed')
        both('running', 'pending', 'failed')  # the instructor's Retry on the panel
        self.assertEqual(read(), 'started')
        both('cancelled', 'complete', 'cancelled')  # what Gradescope already has stays there
        self.assertEqual(read(), 'partial')
        both('cancelled', 'cancelled', 'cancelled')
        self.assertEqual(read(), 'cancelled')
        self.assertEqual(Outcomes().read('frq_gradescope.create', 'succeeded', event), 'started')  # the event alone: as recorded
        # Its plan can be long: what is kept of a long result still names the creation.
        kept = AssistantService._brief({**event['result'], 'plan': {'view': 'x' * 5000}})
        self.assertEqual((kept['creation_id'], kept['status']), ('made', 'pending'))
        self.assertEqual(self.reader.read('frq_gradescope.create', 'succeeded', {'result': kept}), 'cancelled')

    def test_sync_with_canvas_is_done_unless_its_send_says_not(self):
        self.db.execute('CREATE TABLE gradebook_canvas_sends(id TEXT, status TEXT)')
        self.db.execute("INSERT INTO gradebook_canvas_sends VALUES('send','interrupted')")
        read = lambda result: self.reader.read('gradebook.canvas_sync', 'succeeded', {'result': result})  # noqa: E731
        self.assertEqual(read({'sync': {}, 'send': None}), 'succeeded')        # nothing was owed
        self.assertEqual(read({'summary': 'a receipt of before'}), 'succeeded')  # names no send: as recorded
        self.assertEqual(read({'send': {'id': 'send', 'status': 'done'}}), 'uncertain')
        self.db.execute("UPDATE gradebook_canvas_sends SET status='done'")
        self.assertEqual(read({'send': {'id': 'send', 'status': 'running'}}), 'succeeded')
        self.assertEqual(Outcomes().read('gradebook.canvas_sync', 'succeeded', {'result': {'send': {'id': 'send', 'status': 'failed'}}}), 'failed')

    def test_a_receipt_that_cannot_be_read_falls_back_to_the_recorded_event(self):
        event = {'result': {'id': 'push', 'status': 'running'}}
        outcomes._logged.difference_update({'canvas.section_content_push', 'absences.create'})
        with self.assertLogs(level='ERROR') as said:  # no such table: the action reads as its event recorded it
            self.assertEqual(self.reader.read('canvas.section_content_push', 'succeeded', event), 'started')
            self.assertEqual(self.reader.read('canvas.section_content_push', 'succeeded', event), 'started')
            self.assertEqual(self.reader.read('canvas.section_content_push', 'succeeded', '{not json'), 'started')
            self.assertEqual(self.reader.read('absences.create', 'succeeded', '{not json'), 'succeeded')
        self.assertEqual(len(said.records), 2)  # once a route, however many of its cards a page reads
        # The learning look's SQL reader no longer raises for it either: the candidate is simply not confirmed.
        self.db.executescript("""CREATE TABLE assistant_actions(id TEXT, endpoint TEXT);
            CREATE TABLE assistant_action_events(action_id TEXT, kind TEXT, detail_json TEXT);
            INSERT INTO assistant_actions VALUES('1','canvas.section_content_push');""")
        self.db.execute('INSERT INTO assistant_action_events VALUES(?,?,?)', ('1', 'succeeded', json.dumps(event)))
        with self.reader.sql() as confirmed:
            self.assertEqual(self.db.execute(f"""SELECT COUNT(*) FROM assistant_actions a JOIN assistant_action_events e
                ON e.action_id=a.id WHERE {confirmed}""").fetchone()[0], 0)

    def test_an_accepted_send_is_final_and_needs_no_receipt_read(self):
        self.assertTrue(settled('gmail_mail.send', 'succeeded'))
        self.assertFalse(settled('gmail_mail.send', 'uncertain'))
        self.assertFalse(settled('canvas.message_send', 'succeeded'))  # a later delivery can still say otherwise
