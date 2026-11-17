"""TEACHING-WORKFLOWS: independent promises, preserved history and receipt-gated completion."""
import json
import uuid
import unittest
from datetime import timedelta

from rosters import connect
from test_student_notes import Base
from test_student_support import StudentSupportTests
from test_storage import HEADERS
from test_followup_suggestions import SuggestionsTests, proposing, MAIL
from scheduling.clock import campus_today
from course_storage import timestamp


class CommitmentTests(Base):
    incoming = StudentSupportTests.incoming
    listing = StudentSupportTests.listing

    def task(self, ident='work', **extra):
        return {'id': ident, 'title': 'Prepare an alternative', 'kind': 'work', 'state': 'open',
                'waiting_on': 'you', **extra}

    def create(self, tasks):
        self.incoming()
        item = self.listing()[0]
        result = self.client.post('/api/student-support', json={**item, 'state': 'todo', 'commitments': tasks,
                                 'request_id': str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        return self.listing()[0]

    def change_tasks(self, item, tasks=None, **extra):
        body = {**item, 'base_revision': item['revision'], 'request_id': str(uuid.uuid4()), **extra}
        if tasks is None:
            body.pop('commitments', None)
        else:
            body['commitments'] = tasks
        return self.client.post('/api/student-support/' + item['id'], json=body, headers=HEADERS)

    def test_reply_and_ordinary_edits_do_not_complete_or_discard_work(self):
        item = self.create([self.task(), self.task('later', kind='check_back', waiting_on='date', check_back='2099-10-12')])
        self.incoming('reply', '2026-10-01T13:00:00Z', author='instructor', body='I will arrange it.')
        result = self.change_tasks(item, state='waiting', next='Student replies')
        self.assertEqual(result.status_code, 200, result.json)
        after = self.listing()[0]
        self.assertTrue(after['attention'])
        self.assertEqual([c['effective_state'] for c in after['commitments']], ['open', 'open'])
        refused = self.change_tasks(after, state='resolved')
        self.assertEqual(refused.status_code, 409)
        self.assertIn('commitments', refused.json['error'])

    def test_completion_needs_note_and_prerequisite_then_leaves_later_task_open(self):
        item = self.create([self.task(), self.task('reply', kind='reply', depends_on=['work']),
                            self.task('later', kind='check_back', waiting_on='date', check_back='2099-10-12')])
        tasks = item['commitments']
        tasks[1].update(state='completed', completion_note='Sent externally')
        self.assertEqual(self.change_tasks(item, tasks).status_code, 409)
        tasks[1]['state'] = 'open'
        tasks[0]['state'] = 'completed'
        self.assertEqual(self.change_tasks(item, tasks).status_code, 400)
        tasks[0]['completion_note'] = 'Prepared through the assessment editor'
        tasks[1]['state'] = 'completed'
        result = self.change_tasks(item, tasks)
        self.assertEqual(result.status_code, 200, result.json)
        current = self.listing()[0]
        self.assertEqual([c['effective_state'] for c in current['commitments']], ['completed', 'completed', 'open'])
        self.assertFalse(current['attention'], 'A future check-back alone is not work waiting on the instructor today.')
        self.assertEqual(self.change_tasks(current, state='resolved').status_code, 409)

    def test_omission_deletion_cycles_and_stale_revision(self):
        item = self.create([self.task(), self.task('reply', kind='reply', depends_on=['work'])])
        self.assertEqual(self.change_tasks(item, []).status_code, 409)
        circular = item['commitments']
        circular[0]['depends_on'] = ['reply']
        self.assertEqual(self.change_tasks(item, circular).status_code, 400)
        self.assertEqual(self.change_tasks(item, title='New title').status_code, 200)
        self.assertEqual(self.change_tasks(item, title='Stale title').status_code, 409)
        self.assertEqual(len(self.listing()[0]['commitments']), 2)

    def test_source_cannot_be_another_students_message(self):
        self.incoming('bob', thread='bob', enrollment=self.bob)
        with connect(self.app.config['DATABASE']) as db:
            ident = db.execute("SELECT id FROM comm_messages WHERE external_id='bob'").fetchone()[0]
        self.incoming()
        item = next(i for i in self.listing() if i['enrollment_id'] == self.alice)
        result = self.client.post('/api/student-support', json={**item, 'commitments': [self.task(source_message_id=ident)],
                                 'request_id': 'wrong-student'}, headers=HEADERS)
        self.assertEqual(result.status_code, 400)
        self.assertIn('this student', result.json['error'])

    def test_completed_task_is_retained_and_can_be_reopened(self):
        item = self.create([self.task()])
        tasks = item['commitments']
        tasks[0].update(state='completed', completion_note='Alternative is ready')
        self.assertEqual(self.change_tasks(item, tasks).status_code, 200)
        current = self.listing()[0]
        with connect(self.app.config['DATABASE']) as db:
            previous = json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? AND revision=1', (item['id'],)).fetchone()[0])
        self.assertEqual(previous['commitments'][0]['state'], 'open')
        tasks[0]['state'] = 'open'
        self.assertEqual(self.change_tasks(current, tasks).status_code, 200)
        self.assertEqual(self.listing()[0]['commitments'][0]['effective_state'], 'open')

    def test_exact_retry_is_idempotent(self):
        item = self.create([self.task()])
        body = {**item, 'base_revision': item['revision'], 'request_id': 'same-save', 'title': 'Updated purpose'}
        first = self.client.post('/api/student-support/' + item['id'], json=body, headers=HEADERS)
        again = self.client.post('/api/student-support/' + item['id'], json=body, headers=HEADERS)
        self.assertEqual(first.json, again.json)
        self.assertEqual(first.status_code, 200)

    def test_join_preserves_tasks_by_reference_and_prevents_false_resolution(self):
        first = self.create([self.task('first')])
        self.incoming('second', '2026-10-01T13:00:00Z', thread='second')
        other = next(i for i in self.listing() if i['source_key'] == 'gmail:second')
        saved = self.client.post('/api/student-support', json={**other, 'state': 'todo', 'commitments': [self.task('second')],
                                 'request_id': 'second-task'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        joined = self.client.post('/api/student-support/followups/join', json={'enrollment_id': self.alice,
            'source_key': first['source_key'], 'with_key': 'gmail:second', 'request_id': 'join-tasks'}, headers=HEADERS)
        self.assertEqual(joined.status_code, 200, joined.json)
        [combined] = self.listing()
        self.assertEqual(combined['related_commitments'][0]['owner_id'], saved.json['id'])
        self.assertEqual(combined['related_commitments'][0]['commitments'][0]['id'], 'second')
        self.assertEqual(len(combined['commitments']), 1)
        self.assertEqual(self.change_tasks(combined, state='resolved').status_code, 409)

    def test_start_from_now_cancels_tasks_with_retained_history(self):
        item = self.create([self.task()])
        route = '/api/student-support/followups/start-now'
        preview = self.client.post(route + '/preview', json={'through': timestamp()}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        saved = self.client.post(route, json={**preview.json, 'request_id': 'clear-tasks', 'confirmation': 'start-now'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        [current] = self.listing()
        self.assertEqual(current['state'], 'resolved')
        self.assertEqual(current['commitments'][0]['state'], 'cancelled')
        with connect(self.app.config['DATABASE']) as db:
            before = json.loads(db.execute('SELECT content_json FROM student_support_revisions WHERE item_id=? AND revision=1', (item['id'],)).fetchone()[0])
        self.assertEqual(before['commitments'][0]['state'], 'open')


class ReceiptTests(SuggestionsTests):
    def test_new_reply_prerequisite_is_saved_before_mail_and_holds_the_send(self):
        fake = self.fake_mail()
        self.incoming()
        def reply(c, prompt):
            selected = json.loads(prompt.split('Selected follow-up (trusted Faculty identities):\n', 1)[1].split('\n', 1)[0])
            keep = ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                    'observed': selected['observed'], 'title': 'Prepare the alternative', 'state': 'todo',
                    'commitments': [{'id':'prepare', 'title':'Prepare the alternative', 'kind':'work', 'state':'open',
                                     'waiting_on':'you', 'blocks_reply':True}]}, 'Track the prerequisite')
            proposing(MAIL, keep)(c, prompt)
        run, _ = self.prepare(reply)
        self.assertEqual(run['actions'][0]['endpoint'], 'student_support.create')
        saved = self.approve(run['actions'][0])
        self.assertEqual(saved.status_code, 200, saved.json)
        blocked = self.approve(run['actions'][1])
        self.assertEqual(blocked.status_code, 409, blocked.json)
        self.assertEqual(fake.sent, [])

    def keep_without_observed(self, c, prompt):
        """Juliet Dane's To do card (2026-10-21): the model leaves "observed" out of its tracking step."""
        keep = ('/api/student-support', {'kind': 'followup', 'enrollment_id': self.alice, 'source_key': 'gmail:b1',
                'title': 'Arrange the makeup', 'state': 'todo',
                'commitments': [{'id': 'arrange', 'title': 'Arrange the makeup', 'kind': 'work', 'state': 'open', 'waiting_on': 'you'}]},
                'Track the makeup')
        proposing(keep)(c, prompt)

    def test_tracking_step_without_observed_records_the_message_it_read(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(self.keep_without_observed)
        self.assertEqual(run['actions'][0]['endpoint'], 'student_support.create')
        saved = self.approve(run['actions'][0])
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json['action']['status'], 'succeeded', saved.json)
        self.assertEqual(self.listing()['items'][0]['state'], 'todo')

    def test_tracking_step_without_observed_still_stops_for_a_real_new_message(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(self.keep_without_observed)
        self.incoming('a2', body='One more thing about the makeup.', at='2026-10-03T14:00:00Z')
        with connect(self.assistant.database) as db:
            body = json.loads(db.execute('SELECT body_json FROM assistant_actions WHERE id=?', (run['actions'][0]['id'],)).fetchone()[0])
        self.assertIn('observed', body)
        saved = self.approve(run['actions'][0])
        self.assertNotEqual(saved.json.get('action', {}).get('status'), 'succeeded', saved.json)

    def test_watch_is_bound_to_one_task_and_ends_quietly_on_completion(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        item = self.listing()['items'][0]
        tasks = [{'id': 'work', 'title': 'Prepare the alternative', 'kind': 'work', 'state': 'open', 'waiting_on': 'you'}]
        saved = self.client.post('/api/student-support', json={**item, 'state': 'todo', 'commitments': tasks,
                                 'request_id': 'keep-task'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        spec = {'enrollment_id': self.alice, 'item_key': run['item_key'], 'kind': 'date',
                'target': {'commitment_id': 'work'}, 'check_back': (campus_today() + timedelta(days=2)).isoformat()}
        checked = self.client.post('/api/student-support/followups/watches/preview', json=spec, headers=HEADERS)
        self.assertEqual(checked.status_code, 200, checked.json)
        created = self.client.post('/api/student-support/followups/watches', json={**spec, 'fingerprint': checked.json['fingerprint'],
            'request_id': 'watch-task'}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(created.json['commitment_id'], 'work')
        with connect(self.assistant.database) as db:
            prompt = self.suggestions.prompt(dict(self.suggestions.get(db, run['id'])))
        self.assertIn('"commitments"', prompt)
        current = self.listing()['items'][0]
        tasks[0].update(state='completed', completion_note='Checked and saved the alternative.')
        done = self.client.post('/api/student-support/' + saved.json['id'], json={**current, 'commitments': tasks,
            'base_revision': current['revision'], 'request_id': 'task-done'}, headers=HEADERS)
        self.assertEqual(done.status_code, 200, done.json)
        self.assertTrue(self.suggestions.watches.process_one())
        watch = self.listing()['items'][0]['suggestions']['watches'][0]
        self.assertEqual(watch['state'], 'triggered')
        self.assertTrue(watch['detail']['completed'])
        self.assertNotIn('run_id', watch['detail'])

    def test_linked_mail_needs_accepted_receipt(self):
        self.fake_mail()
        self.incoming()
        run, _ = self.prepare(proposing(MAIL))
        action = run['actions'][0]
        item = self.listing()['items'][0]
        tasks = [{'id': 'reply', 'title': 'Send the arrangement', 'kind': 'reply', 'state': 'open',
                  'waiting_on': 'you', 'action_id': action['id']}]
        saved = self.client.post('/api/student-support', json={**item, 'state': 'todo', 'commitments': tasks,
                                 'request_id': 'keep-reply'}, headers=HEADERS)
        self.assertEqual(saved.status_code, 201, saved.json)
        self.assistant._event(action['id'], 'succeeded', 'faculty', {'result': {'status': 'uncertain'}})
        current = self.listing()['items'][0]
        self.assertEqual(current['commitments'][0]['effective_state'], 'open')
        tasks[0]['state'] = 'completed'
        rejected = self.client.post('/api/student-support/' + saved.json['id'], json={**current,
            'commitments': tasks, 'base_revision': current['revision'], 'request_id': 'false-completion'}, headers=HEADERS)
        self.assertEqual(rejected.status_code, 409, rejected.json)
        StudentSupportTests.sent(self, action['body']['request_id'], 'confirmed-message', status='accepted')
        self.assertEqual(self.listing()['items'][0]['commitments'][0]['effective_state'], 'completed')


def load_tests(loader, tests, pattern):
    # Reuse fixture methods, not their unrelated test cases.
    return unittest.TestSuite(cls(name) for cls in (CommitmentTests, ReceiptTests)
                              for name in cls.__dict__ if name.startswith('test_'))
