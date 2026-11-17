"""Response Card ownership, immutable publication, recovery, and API boundaries."""
import copy
import io
import json
import sqlite3
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from werkzeug.exceptions import Conflict, ServiceUnavailable

from app import create_app
from response_cards.mail import MailConfig
from response_cards.remote import RemoteConfig
from rosters import connect
from test_dr_exams import booking, workbook as dr_workbook
from test_rooms import chart_bytes
import test_rosters as roster_tests
from test_storage import HEADERS


class FakeRemote:
    """Only the documented card publication, control, and event operations."""
    def __init__(self):
        self.config = RemoteConfig('https://respond.example.test', 'test-secret-' * 4)
        self.snapshots, self.events, self.calls, self.controls = {}, [], [], {}
        self.lose_publish_receipt = False
        self.page_override = None

    def event(self, card_id, status='issued', **values):
        item = {'cursor': len(self.events) + 1, 'card_id': card_id,
                'type': 'submission' if 'submission' in values else 'status',
                'created_at': '2026-09-14T12:00:00.000Z', 'data': {'status': status, **values}}
        self.events.append(item)
        return item

    def call(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        if method == 'PUT' and path.startswith('/faculty-api/cards/'):
            card_id = path.rsplit('/', 1)[1]
            if card_id in self.snapshots:
                if self.snapshots[card_id] != data:
                    raise Conflict('Remote immutable snapshot changed')
            else:
                self.snapshots[card_id] = copy.deepcopy(data)
                self.event(card_id)
            if self.lose_publish_receipt:
                self.lose_publish_receipt = False
                raise ServiceUnavailable('Lost the response after publication')
            return {'id': card_id, 'published': True}
        if method == 'GET' and path.startswith('/faculty-api/events?'):
            if self.page_override is not None:
                return copy.deepcopy(self.page_override)
            query = parse_qs(urlsplit(path).query)
            after, limit = int(query['after'][0]), int(query['limit'][0])
            events = [e for e in self.events if e['cursor'] > after]
            selected = events[:limit]
            return {'events': copy.deepcopy(selected), 'cursor': selected[-1]['cursor'] if selected else after, 'has_more': len(events) > limit}
        if method == 'POST' and path.endswith('/control'):
            card_id = path.split('/')[-2]
            operation = (card_id, data['request_id'])
            if operation not in self.controls:
                self.controls[operation] = data['action']
                status = 'revoked' if data['action'] == 'revoke' else 'issued'
                self.event(card_id, status, action=data['action'], request_id=data['request_id'])
            return {'id': card_id, 'action': data['action'], 'applied': True}
        raise AssertionError(f'Unexpected remote operation: {method} {path}')


class ResponseCardTests(unittest.TestCase):
    managed = False
    add = roster_tests.RosterTests.add
    route = roster_tests.RosterTests.route
    upload = roster_tests.RosterTests.upload
    change = roster_tests.RosterTests.change

    def setUp(self):
        roster_tests.RosterTests.setUp(self)
        self.cards = self.app.extensions['response_cards']
        self.remote = FakeRemote()
        self.cards.remote = self.remote
        self.course, self.group = self.add()
        self.roster = self.upload(self.group)
        self.context = self.cards.context()
        self.term = next(t for t in self.context['terms'] if t['code'] == 'MATH 140')
        self.section = next(s for s in self.context['sections'] if s['term_id'] == self.term['id'] and s['number'] == 507)
        self.definition_id = self.term['definition_id']

    def template(self, schema=None, definition_id=None):
        return self.cards.save_template({'name': 'Instructions', 'definition_id': definition_id or self.definition_id,
            'schema': schema or {'title': '{{course.code}} instructions', 'fields': [
                {'id': 'name', 'type': 'confirmation', 'label': 'Your name', 'required': True,
                 'verification': {'expected': '{{student.full_name}}', 'mode': 'normalized'}},
                {'id': 'ack', 'type': 'acknowledgment', 'label': 'I have read the instructions.', 'required': True}]}})

    def issue_data(self, template=None, **changes):
        template = template or self.template()
        version = self.cards.freeze(template['id'], template['version'])
        return {'request_id': str(uuid.uuid4()), 'version_id': version['id'], 'term_id': self.term['id'],
                'section_ids': [self.section['id']], **changes}

    def snapshot(self, card_id):
        with connect(self.cards.path) as db:
            return json.loads(db.execute('SELECT snapshot_json FROM response_card_recipients WHERE id=?', (card_id,)).fetchone()[0])

    def counts(self):
        with connect(self.cards.path) as db:
            return {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                    for table in ('response_card_issues', 'response_card_recipients', 'response_card_submissions', 'response_card_events')}

    def test_q_drops_and_withdrawals_are_issued_no_card(self):
        """requirement, 2026-10-15: withdrawn or Q-drop students get no response cards (since 2026-10-25 the import drops them too)."""
        rows = [row.copy() for row in roster_tests.STUDENTS]
        rows[1][3], rows[1][9] = 'Q-Drop State Limited', 'Q'
        self.upload(self.group, rows=rows)
        issued = self.cards.issue(self.issue_data())
        bob = next(s['id'] for s in self.roster['students'] if s['uin'] == rows[1][2])
        self.assertEqual([c['enrollment_id'] for c in issued['recipients'] if c['enrollment_id'] == bob], [])
        self.assertEqual(len(issued['recipients']), 1)
        self.assertNotIn(bob, [s['enrollment_id'] for s in self.cards.context(self.term['id'])['students']])

    def test_frozen_versions_and_issue_retries_retain_exact_memberships_and_snapshots(self):
        template = self.template()
        data = self.issue_data(template)
        self.assertEqual(self.cards.freeze(template['id'], template['version'])['id'], data['version_id'])
        first = self.cards.issue(data)
        snapshots = {c['id']: self.snapshot(c['id']) for c in first['recipients']}
        changed = copy.deepcopy(template)
        changed['schema']['fields'][0]['label'] = 'A new question'
        second = self.cards.save_template(changed, template['id'])
        self.assertNotEqual(self.cards.freeze(second['id'], second['version'])['id'], data['version_id'])
        rows = copy.deepcopy(roster_tests.STUDENTS)
        rows[0][1] = 'Renamed, Alice'
        self.upload(self.group, rows=rows)
        self.assertEqual(self.cards.issue(data), first)
        self.assertEqual({c['id']: self.snapshot(c['id']) for c in first['recipients']}, snapshots)
        with self.assertRaises(Conflict):
            self.cards.issue({**data, 'policy': 'unlimited'})
        self.assertEqual(self.counts()['response_card_issues'], 1)
        with connect(self.cards.path) as db:
            for sql in ('UPDATE response_card_versions SET schema_json=\'{}\'',
                        'UPDATE response_card_recipients SET token=\'different\'',
                        'DELETE FROM response_card_issues'):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_lost_publication_receipt_reuses_snapshot_token_and_creates_no_remote_duplicate(self):
        issued = self.cards.issue(self.issue_data())
        self.remote.lose_publish_receipt = True
        with self.assertRaises(ServiceUnavailable):
            self.cards.publish(issued['id'])
        self.assertEqual(len(self.remote.snapshots), 1)
        first_snapshot = copy.deepcopy(next(iter(self.remote.snapshots.values())))
        after_failure = self.cards.get_issue(issued['id'])
        self.assertTrue(any(c['publish_status'] == 'failed' for c in after_failure['recipients']))
        published = self.cards.publish(issued['id'])
        self.assertEqual(self.remote.snapshots[first_snapshot['id']], first_snapshot)
        self.assertEqual(len(self.remote.snapshots), 2)
        self.assertEqual(len(self.remote.events), 2)
        for recipient in published['recipients']:
            self.assertRegex(recipient['link'], r'^https://respond\.example\.test/r/[A-Za-z0-9_-]{43}$')
            self.assertEqual(recipient['delivery_status'], 'not_sent')
        before = len(self.remote.calls)
        self.cards.publish(issued['id'])
        self.assertEqual(len(self.remote.calls), before)
        self.assertEqual(self.cards.sync()['events'], 2)

    def test_concurrent_issuance_uses_one_reservation_and_reconciliation_one_cursor(self):
        data = self.issue_data()
        with ThreadPoolExecutor(max_workers=2) as executor:
            issued = list(executor.map(lambda _: self.cards.issue(data), range(2)))
        self.assertEqual(issued[0], issued[1])
        self.assertEqual(self.counts()['response_card_issues'], 1)
        self.assertEqual(self.counts()['response_card_recipients'], 2)
        self.cards.publish(issued[0]['id'])
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: self.cards.sync(), range(2)))
        self.assertEqual(self.counts()['response_card_events'], 2)
        self.assertEqual(self.cards.sync()['events'], 0)

    def test_unsuccessful_remote_receipts_never_record_publication_or_completed_control(self):
        issued = self.cards.issue(self.issue_data())
        with patch.object(self.remote, 'call', side_effect=lambda method, path, data: {'id': data['id'], 'published': False}):
            with self.assertRaises(ServiceUnavailable):
                self.cards.publish(issued['id'])
        detail = self.cards.get_issue(issued['id'])
        self.assertEqual(detail['counts'].get('published', 0), 0)
        self.assertTrue(all(r['link'] is None for r in detail['recipients']))
        published = self.cards.publish(issued['id'])
        card_id = published['recipients'][0]['id']
        with patch.object(self.remote, 'call', return_value={'id': card_id, 'applied': False, 'action': 'revoke'}):
            with self.assertRaises(ServiceUnavailable):
                self.cards.control(card_id, {'action': 'revoke', 'request_id': 'failed-control'})
        with connect(self.cards.path) as db:
            self.assertEqual(db.execute('SELECT status FROM response_card_controls WHERE request_id=?', ('failed-control',)).fetchone()[0], 'pending')

    def test_bounded_event_pages_continue_from_the_last_committed_cursor(self):
        issued = self.cards.publish(self.cards.issue(self.issue_data())['id'])
        for _ in range(123):
            self.remote.event(issued['recipients'][0]['id'], 'started')
        first = self.cards.sync(max_pages=1)
        self.assertEqual(first, {'events': 100, 'cursor': 100, 'has_more': True})
        self.assertEqual(self.cards.sync(), {'events': 25, 'cursor': 125, 'has_more': False})

    def test_sync_retains_immutable_history_and_idempotent_duplicate_submissions(self):
        issued = self.cards.publish(self.cards.issue(self.issue_data())['id'])
        card_id = issued['recipients'][0]['id']
        self.cards.sync()
        submission = {'id': 'submission-1', 'request_id': 'student-submit-1', 'answers': {'ack': True, 'name': 'Alice Example'},
                      'verification': {'name': True}, 'status': 'verified', 'submitted_at': '2026-09-14T12:30:00.000Z'}
        self.remote.event(card_id, 'verified', submission=submission)
        self.remote.event(card_id, 'verified', submission=copy.deepcopy(submission))
        self.assertEqual(self.cards.sync()['events'], 2)
        self.assertEqual(self.cards.sync()['events'], 0)
        self.assertEqual(self.counts()['response_card_submissions'], 1)
        second = {**submission, 'id': 'submission-2', 'request_id': 'student-submit-2', 'answers': {'ack': True, 'name': 'Different'},
                  'status': 'needs_review', 'submitted_at': '2026-09-14T12:31:00.000Z'}
        self.remote.event(card_id, 'needs_review', submission=second)
        self.cards.sync()
        detail = next(c for c in self.cards.get_issue(issued['id'])['recipients'] if c['id'] == card_id)
        self.assertEqual([s['id'] for s in detail['submissions']], ['submission-1', 'submission-2'])
        self.assertEqual(detail['current_submission']['id'], 'submission-2')
        before = self.counts()
        self.remote.event(card_id, 'verified', submission={**submission, 'answers': {'ack': False}})
        with self.assertRaises(Conflict):
            self.cards.sync()
        self.assertEqual(self.counts(), before)
        with connect(self.cards.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM response_card_submissions')

    def test_dr_location_answer_never_flags_a_card(self):
        """RESPONSE-CARDS "DR LOCATION IS NOT CHECKED": a card issued with the old location check stops needing review when only
        the location differs, keeps it when the date differs too, and the stored submission is unchanged."""
        issued = self.cards.publish(self.cards.issue(self.issue_data())['id'])
        only_location, with_date = (card['id'] for card in issued['recipients'][:2])
        flag = lambda matched: {'matched': matched, 'on_mismatch': 'ALLOW_AND_FLAG'}
        for card_id, date in ((only_location, True), (with_date, False)):
            self.remote.event(card_id, 'needs_review', submission={
                'id': 'sub-' + card_id, 'request_id': 'r-' + card_id, 'status': 'needs_review', 'submitted_at': '2026-09-14T12:30:00.000Z',
                'answers': {'confirm_dr_date': '2026-09-17', 'confirm_dr_location': 'Blinn'},
                'verification': {'confirm_dr_date': flag(date), 'confirm_dr_location': flag(False)}})
        self.cards.sync()
        cards = {c['id']: c for c in self.cards.get_issue(issued['id'])['recipients']}
        self.assertEqual((cards[only_location]['status'], cards[with_date]['status']), ('verified', 'needs_review'))
        self.assertEqual(cards[with_date]['current_submission']['verification'], {'confirm_dr_date': flag(False)})
        with connect(self.cards.path) as db:
            stored = json.loads(db.execute('SELECT submission_json FROM response_card_submissions WHERE card_id=?', (only_location,)).fetchone()[0])
            self.assertIn('confirm_dr_location', stored['verification'])
            # A card flagged before the rule (status stored as needs_review) is settled by the next sync.
            db.execute("UPDATE response_card_recipients SET status='needs_review' WHERE id=?", (only_location,))
            db.commit()
        self.cards.sync()
        self.assertEqual(next(c for c in self.cards.get_issue(issued['id'])['recipients'] if c['id'] == only_location)['status'], 'verified')

    def test_attention_counts_labels_and_sync_status_are_read_only(self):
        """The list's attention strip, the issue page's filters and its title placeholders read retained records only."""
        issued = self.cards.publish(self.cards.issue(self.issue_data())['id'])
        first, second = (card['id'] for card in issued['recipients'][:2])
        self.assertIsNone(self.cards.context()['sync']['synced_at'])
        self.assertEqual(self.cards.attention(), {'items': [], 'total': 0})
        submission = {'id': 'submission-a', 'request_id': 'student-a', 'answers': {'ack': True, 'name': 'Someone else'},
                      'verification': {'name': {'matched': False}}, 'status': 'needs_review', 'submitted_at': '2026-09-14T12:30:00.000Z'}
        self.remote.event(second, 'needs_review', submission=submission)
        self.cards.sync()
        self.assertTrue(self.cards.context()['sync']['synced_at'])
        with connect(self.cards.path) as db:
            # An unopened card whose invitation failed; a replaced card with its remote revocation pending; a receipt unconfirmed.
            db.execute("UPDATE response_card_recipients SET delivery_status='failed' WHERE id=?", (first,))
            # Sync queued the submission's receipt; say its delivery could not be confirmed.
            self.assertEqual(db.execute("UPDATE response_card_mail SET status='unconfirmed' WHERE card_id=? AND kind='confirmation'", (second,)).rowcount, 1)
            db.commit()
        before = self.counts()
        detail = self.cards.get_issue(issued['id'])
        counts = detail['counts']
        self.assertEqual((counts['delivery_problem'], counts['needs_review'], counts['receipt_problem'], counts['revocation_pending'], counts['queued']),
                         (1, 1, 1, 0, 0))
        self.assertEqual((detail['course_code'], detail['assignment_title'], detail['room_name'], detail['exam_event_id']), ('MATH 140', '', '', ''))
        self.assertTrue(detail['term_label'])
        attention = self.cards.attention()
        self.assertEqual([item['id'] for item in attention['items']], [issued['id']])
        self.assertEqual(attention['items'][0]['counts'], {'needs_review': 1, 'delivery_problem': 1, 'receipt_problem': 1, 'revocation_pending': 0})
        self.assertEqual(attention['total'], 3)
        text = json.dumps(attention)
        for secret in ('Someone else', '@', 'enrollment', 'token', 'link'):
            self.assertNotIn(secret, text)
        with connect(self.cards.path) as db:
            db.execute("INSERT INTO response_card_invalidations(card_id,reason,status,created_at,updated_at) VALUES(?,'seat_changed','pending','x','x')", (first,))
            db.commit()
        replaced = self.cards.get_issue(issued['id'])
        counts = replaced['counts']
        self.assertEqual((counts['delivery_problem'], counts['revocation_pending']), (0, 1), 'a replaced card is no delivery problem')
        # Each card says whether it was replaced, so the issue page can list the cards whose revocation is pending.
        self.assertEqual({card['id']: card['revocation'] for card in replaced['recipients'] if card['id'] in (first, second)},
                         {first: 'pending', second: None})
        self.assertEqual(self.counts(), before, 'reading attention writes nothing')
        response = self.client.get('/api/response-cards/attention', headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['items'][0]['counts']['revocation_pending'], 1)
        # Templates Faculty keeps for seating notices say so; others do not.
        managed = self.cards.save_template({'name': 'Exam seat assignment (Faculty)', 'definition_id': self.definition_id,
                                            'schema': {'title': '{{exam.name}}', 'fields': [{'id': 'ack', 'type': 'acknowledgment', 'label': 'OK', 'required': True}]}})
        self.assertEqual(managed['managed'], 'seating')
        self.assertEqual({item['name']: item['managed'] for item in self.cards.templates(self.definition_id)},
                         {'Exam seat assignment (Faculty)': 'seating', 'Instructions': ''})

    def test_entire_sync_page_rolls_back_when_later_event_is_invalid(self):
        issued = self.cards.publish(self.cards.issue(self.issue_data())['id'])
        self.cards.sync()
        before = self.counts()
        self.remote.event(issued['recipients'][0]['id'], 'opened')
        bad = self.remote.event('unknown-card', 'started')
        with self.assertRaises(ServiceUnavailable):
            self.cards.sync()
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.cards.get_issue(issued['id'])['counts']['issued'], 2)
        bad['card_id'] = issued['recipients'][1]['id']
        self.assertEqual(self.cards.sync()['events'], 2)
        self.assertEqual(self.cards.get_issue(issued['id'])['counts']['opened'], 1)
        before = self.counts()
        with connect(self.cards.path) as db:
            cursor = db.execute('SELECT cursor FROM response_card_sync').fetchone()[0]
        for page in ({'events': [], 'cursor': cursor + 10, 'has_more': False},
                     {'events': [], 'cursor': cursor, 'has_more': True},
                     {'events': [], 'cursor': 2 ** 70, 'has_more': False},
                     {'events': [{'cursor': cursor + 1, 'card_id': [], 'data': {'status': 'opened'}}], 'cursor': cursor + 1, 'has_more': False}):
            self.remote.page_override = page
            with self.subTest(page=page), self.assertRaises(ServiceUnavailable):
                self.cards.sync()
            self.assertEqual(self.counts(), before)

    def test_audience_never_crosses_course_term_section_or_student_ownership(self):
        _, physics = self.add(code='PHYS 140')
        self.upload(physics)
        _, spring = self.add(term='Spring')
        self.upload(spring)
        context = self.cards.context()
        other_term = next(t for t in context['terms'] if t['code'] == 'PHYS 140')
        spring_term = next(t for t in context['terms'] if t['term'] == 'Spring')
        other_section = next(s for s in context['sections'] if s['term_id'] == other_term['id'])
        spring_section = next(s for s in context['sections'] if s['term_id'] == spring_term['id'])
        other_student = self.cards.context(other_term['id'])['students'][0]['enrollment_id']
        data = self.issue_data()
        for changes in ({'term_id': other_term['id'], 'section_ids': [other_section['id']]},
                        {'section_ids': [other_section['id']]}, {'section_ids': [spring_section['id']]},
                        {'enrollment_ids': [other_student]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.cards.issue({**data, **changes})
        self.assertEqual(self.counts()['response_card_issues'], 0)
        self.upload(self.group, section=508)
        same_course = self.cards.context()
        section508 = next(s for s in same_course['sections'] if s['term_id'] == self.term['id'] and s['number'] == 508)
        issued = self.cards.issue({**data, 'section_ids': [self.section['id'], section508['id']]})
        self.assertEqual(len(issued['recipients']), 4)
        self.assertEqual(len({r['enrollment_id'] for r in issued['recipients']}), 2)
        self.assertEqual(len({r['id'] for r in issued['recipients']}), 4)

    def test_personalized_seats_are_frozen_from_physical_chart_rows(self):
        assignment = self.client.post('/api/documents/assignments', json={'definition_id': self.definition_id, 'key': 'exam-1', 'title': 'Exam 1'}, headers=HEADERS).json
        room = self.client.post('/api/rooms', data={'name': 'Exam Hall', 'file': (io.BytesIO(chart_bytes({'A1': 'A', 'B1': 1, 'C1': 2, 'A2': 'B', 'B2': 1, 'C2': 2})), 'seats.xlsx')}, headers=HEADERS)
        self.assertEqual(room.status_code, 201, room.json)
        chart = self.client.post('/api/seating', json={'assignment_id': assignment['id'], 'section_ids': [self.section['id']], 'room_id': room.json['id']}, headers=HEADERS)
        self.assertEqual(chart.status_code, 201, chart.json)
        template = self.template({'title': '{{exam.name}}', 'description': 'Room {{exam.room}}, row {{seat.row}}', 'fields': [
            {'id': 'seat', 'type': 'confirmation', 'label': 'Seat {{seat.number}}', 'verification': {'expected': '{{seating_assignment.seat}}'}}]})
        issued = self.cards.issue(self.issue_data(template, seating_chart_id=chart.json['id']))
        seats = {s['enrollment_id']: s['seat'] for s in chart.json['students']}
        for recipient in issued['recipients']:
            schema = self.snapshot(recipient['id'])['schema']
            label = seats[recipient['enrollment_id']]
            self.assertEqual(schema['description'], f'Room Exam Hall, row {label[0]}')
            self.assertEqual(schema['fields'][0]['verification']['expected'], label)
        self.assertEqual(self.client.get(self.route(self.group)).json, self.roster)

    def test_seating_cards_close_at_campus_midnight_after_the_exam(self):
        """RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" (requirement, 2026-10-15): revoked at midnight Central after exam day."""
        from datetime import datetime, timezone
        assignment = self.client.post('/api/documents/assignments', json={'definition_id': self.definition_id, 'key': 'exam-2', 'title': 'Exam 2'}, headers=HEADERS).json
        room = self.client.post('/api/rooms', data={'name': 'Exam Hall', 'file': (io.BytesIO(chart_bytes({'A1': 'A', 'B1': 1, 'C1': 2, 'A2': 'B', 'B2': 1, 'C2': 2})), 'seats.xlsx')}, headers=HEADERS)
        chart = self.client.post('/api/seating', json={'assignment_id': assignment['id'], 'section_ids': [self.section['id']], 'room_id': room.json['id']}, headers=HEADERS).json
        with connect(self.cards.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-10-22' WHERE id=?", (chart['id'],))
        seated = self.cards.issue(self.issue_data(seating_chart_id=chart['id']))
        other = self.cards.issue(self.issue_data(assignment_id=assignment['id']))  # not from a chart: never closed by the exam date
        self.cards.publish(seated['id'])
        self.cards.publish(other['id'])
        self.cards.mail.config = MailConfig('https://relay.example.test/message', 'test-secret-' * 4)
        self.cards.mail.queue_invitations(seated['id'])
        # 11:59 pm Central on exam day (04:59 UTC the next day) is still exam day; 12:00 am is not.
        self.assertEqual(self.cards.close_finished_exams(datetime(2026, 10, 23, 4, 59, tzinfo=timezone.utc))['closed'], 0)
        before = len(self.remote.calls)
        result = self.cards.close_finished_exams(datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc))
        self.assertEqual((result['closed'], result['complete'], result['pending'], result['errors']), (2, 2, 0, []))
        self.assertEqual([call[2]['action'] for call in self.remote.calls[before:]], ['revoke', 'revoke'])
        issue = self.cards.get_issue(seated['id'])
        self.assertEqual({card['status'] for card in issue['recipients']}, {'revoked'})
        self.assertEqual({card['delivery_status'] for card in issue['recipients']}, {'cancelled'})
        self.assertNotIn('revoked', {card['status'] for card in self.cards.get_issue(other['id'])['recipients']})
        with connect(self.cards.path) as db:
            self.assertEqual({row[0] for row in db.execute("SELECT error FROM response_card_mail WHERE status='cancelled'")},
                             {'The card closed at midnight after its exam.'})
        with self.assertRaisesRegex(Conflict, 'closed at midnight after its exam'):
            self.cards.link(seated['recipients'][0]['id'])
        self.cards.sync()
        self.assertEqual({card['status'] for card in self.cards.get_issue(seated['id'])['recipients']}, {'revoked'})
        self.assertEqual(self.cards.close_finished_exams(datetime(2026, 10, 24, tzinfo=timezone.utc))['closed'], 0)

    def test_dr_notices_close_at_campus_midnight_after_their_exam_day(self):
        """RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" › DR NOTICES CLOSE TOO (requirement, 2026-11-01): a DR notice, issued
        without a chart, closes from midnight Central after the later of its DR date and its assessment's chart dates, keeping its status."""
        from datetime import datetime, timezone
        from response_cards.service import closed_statuses
        assignment = self.client.post('/api/documents/assignments', json={'definition_id': self.definition_id, 'key': 'exam-3', 'title': 'Exam 3'}, headers=HEADERS).json
        room = self.client.post('/api/rooms', data={'name': 'Exam Hall', 'file': (io.BytesIO(chart_bytes({'A1': 'A', 'B1': 1, 'C1': 2})), 'seats.xlsx')}, headers=HEADERS)
        chart = self.client.post('/api/seating', json={'assignment_id': assignment['id'], 'section_ids': [self.section['id']], 'room_id': room.json['id']}, headers=HEADERS).json
        with connect(self.cards.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-10-22' WHERE id=?", (chart['id'],))
        dr = lambda day: self.template({'title': 'DR notice', 'fields': [{'id': 'confirm_dr_date', 'type': 'date', 'label': 'DR date', 'required': True,
                                                                          'verification': {'expected': day, 'mode': 'exact', 'on_mismatch': 'ALLOW_AND_FLAG'}}]})
        late = self.cards.publish(self.cards.issue(self.issue_data(dr('2026-10-24'), assignment_id=assignment['id']))['id'])
        early = self.cards.publish(self.cards.issue(self.issue_data(dr('2026-10-20'), assignment_id=assignment['id']))['id'])
        plain = self.cards.publish(self.cards.issue(self.issue_data(assignment_id=assignment['id']))['id'])  # no DR date: never closed
        answered = late['recipients'][0]['id']
        self.remote.event(answered, 'verified', submission={'id': 'sub-dr', 'request_id': 'r-dr', 'status': 'verified', 'submitted_at': '2026-10-20T12:00:00.000Z',
            'answers': {'confirm_dr_date': '2026-10-24'}, 'verification': {'confirm_dr_date': {'matched': True, 'on_mismatch': 'ALLOW_AND_FLAG'}}})
        self.cards.sync()
        statuses = lambda issue: {card['status'] for card in self.cards.get_issue(issue['id'])['recipients']}
        # The early DR date waits for the chart's exam day (Oct 22); the late one for its own (Oct 24).
        self.assertEqual(self.cards.close_finished_exams(datetime(2026, 10, 23, 4, 59, tzinfo=timezone.utc))['closed'], 0)
        result = self.cards.close_finished_exams(datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc))
        self.assertEqual((result['closed'], result['errors']), (len(early['recipients']), []))
        self.assertEqual((statuses(early), 'revoked' in statuses(late)), ({'revoked'}, False))
        self.assertEqual(self.cards.close_finished_exams(datetime(2026, 10, 25, 4, 59, tzinfo=timezone.utc))['closed'], 0)
        before = len(self.remote.calls)
        result = self.cards.close_finished_exams(datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc))
        self.assertEqual((result['closed'], result['complete'], result['errors']), (len(late['recipients']), len(late['recipients']), []))
        self.assertEqual({call[2]['action'] for call in self.remote.calls[before:]}, {'revoke'})
        self.assertEqual(statuses(late), {'revoked'})
        self.assertNotIn('revoked', statuses(plain))
        with connect(self.cards.path) as db:
            kept = closed_statuses(db, [card['id'] for card in late['recipients']])
        self.assertEqual(kept[answered], 'verified')
        self.assertEqual({status for card_id, status in kept.items() if card_id != answered}, {'closed'})
        with self.assertRaisesRegex(Conflict, 'closed at midnight after its exam'):
            self.cards.link(answered)
        self.assertEqual(self.cards.close_finished_exams(datetime(2026, 10, 26, tzinfo=timezone.utc))['closed'], 0)

    def test_dr_context_matches_exact_membership_and_assignment_before_any_card_is_saved(self):
        content = dr_workbook([booking()])
        preview = self.client.post('/api/accommodations/exams/preview', data={'file': (io.BytesIO(content), 'dr.xlsx'), 'term': 'null'}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        queued = self.client.post('/api/accommodations/exams/import', data={'file': (io.BytesIO(content), 'dr.xlsx'), 'term': 'null',
            'preview_hash': preview.json['preview_hash'], 'idempotency_key': str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(queued.status_code, 202, queued.json)
        inbox = self.app.extensions['inbox']
        inbox.process_one()
        self.assertEqual(inbox.get(queued.json['id'])['status'], 'completed')
        # DR imports title "Test" as "Exam" (exam_links.exam_title, requirement, 2026-09-27).
        assignment = next(a for a in self.cards.context()['assignments'] if a['title'] == 'Exam 1')
        template = self.template({'title': '{{assignment.name}}', 'description': '{{dr_exam.date}} {{dr_exam.time}} {{dr_exam.location}}', 'fields': [
            {'id': 'uin', 'type': 'confirmation', 'label': 'Last four UIN digits', 'verification': {'expected': '{{student.uin | last4}}'}}]})
        data = self.issue_data(template, assignment_id=assignment['id'])
        with self.assertRaisesRegex(ValueError, 'Missing'):
            self.cards.issue(data)
        self.assertEqual(self.counts()['response_card_issues'], 0)
        alice = next(s for s in self.roster['students'] if s['uin'] == '001234567')
        issued = self.cards.issue({**data, 'enrollment_ids': [alice['id']]})
        snapshot = self.snapshot(issued['recipients'][0]['id'])
        self.assertEqual(snapshot['schema']['description'], '2026-09-17 19:05 College Station')
        self.assertEqual(snapshot['schema']['fields'][0]['verification']['expected'], '4567')
        self.assertNotIn('001234567', json.dumps(snapshot))
        self.assertNotIn(alice['id'], json.dumps(snapshot))

    def test_browser_access_same_origin_and_generic_export_secret_exclusion(self):
        data = self.issue_data()
        fresh = self.app.test_client()
        self.assertEqual(fresh.get('/api/response-cards/context').status_code, 200)
        for headers in ({}, {**HEADERS, 'Origin': 'https://unrelated.example'}, {**HEADERS, 'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(fresh.post('/api/response-cards/issues', json=data, headers=headers).status_code, 403)
        self.assertEqual(fresh.post('/api/response-cards/issues', data='not json', headers=HEADERS).status_code, 415)
        for changes in ({'policy': []}, {'assignment_id': {'id': 'invalid'}}, {'seating_chart_id': ['invalid']}):
            response = fresh.post('/api/response-cards/issues', json={**data, **changes}, headers=HEADERS)
            self.assertEqual(response.status_code, 400, response.json)
        issued = fresh.post('/api/response-cards/issues', json=data, headers=HEADERS)
        self.assertEqual(issued.status_code, 201, issued.json)
        detail = self.cards.publish(issued.json['id'])
        exported = fresh.get('/api/export').get_data(as_text=True)
        state = fresh.get('/api/state').get_data(as_text=True)
        context = fresh.get('/api/response-cards/context').get_data(as_text=True)
        for recipient in detail['recipients']:
            token = recipient['link'].rsplit('/', 1)[1]
            for output in (exported, state, context):
                self.assertNotIn(token, output)
                self.assertNotIn(self.remote.config.secret, output)
                self.assertNotIn(recipient['enrollment_id'], output)
        restarted = create_app(self.directory.name)
        restarted.testing = True
        self.assertEqual(restarted.test_client().get('/api/response-cards/issues/' + issued.json['id']).status_code, 200)

    def test_archival_retains_access_and_course_identity_edits_cannot_orphan_templates(self):
        empty_course, _ = self.add(code='MATH 150')
        empty_definition = next(d['id'] for d in self.cards.context()['definitions'] if d['code'] == 'MATH 150')
        self.template(definition_id=empty_definition)
        blocked = self.change(empty_course, code='MATH 151')
        self.assertEqual(blocked.status_code, 409, blocked.json)
        self.assertIn('Response Card templates', blocked.json['error'])
        template = self.template()
        self.assertEqual(self.change(self.course, code='MATH 141').status_code, 409)
        issued = self.cards.issue(self.issue_data(template))
        before = self.cards.get_issue(issued['id'])
        archived = self.change(self.course, is_current=False)
        self.assertEqual(archived.status_code, 200, archived.json)
        self.assertEqual(self.cards.get_issue(issued['id']), before)
        self.assertEqual(self.client.get(self.route(self.group)).json, self.roster)
        with self.assertRaisesRegex(ValueError, 'current'):
            self.cards.issue(self.issue_data(template))

    def test_remote_binding_and_control_receipt_retries_preserve_audit(self):
        issued = self.cards.publish(self.cards.issue(self.issue_data())['id'])
        card_id = issued['recipients'][0]['id']
        request = {'action': 'revoke', 'request_id': 'control-request-1'}
        self.cards.control(card_id, request)
        self.cards.control(card_id, request)
        self.assertEqual(len(self.remote.controls), 1)
        self.assertEqual(next(r for r in self.cards.get_issue(issued['id'])['recipients'] if r['id'] == card_id)['status'], 'revoked')
        with self.assertRaises(Conflict):
            self.cards.control(card_id, {**request, 'action': 'unlock'})
        with self.assertRaises(ValueError):
            self.cards.control(card_id, {'action': 'unlock', 'request_id': 'contains spaces'})
        self.remote.config.url = 'https://another.example.test'
        with self.assertRaises(Conflict):
            self.cards.sync()


if __name__ == '__main__':
    unittest.main()
