"""Instructor room roster: exact UINs, seats and scoped current DR schedules."""
from datetime import datetime
import io
import json
import unittest

from rosters import connect
import test_seating as fixture
from test_dr_exams import booking
from test_rosters import csv_bytes


class RoomRosterTests(unittest.TestCase):
    import_dr = fixture.SeatingTests.import_dr
    create = fixture.SeatingTests.create

    def setUp(self):
        fixture.SeatingTests.setUp(self)
        self.chart = self.create().json
        self.path = self.app.extensions['rosters'].path
        self.endpoint = f"/api/seating/{self.chart['id']}/room-roster"

    def preview(self):
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def prepare_cards(self):
        seating = self.app.extensions['seating']
        with connect(self.path) as db:
            db.execute('UPDATE seating_charts SET exam_date=?,exam_time=? WHERE id=?',
                       ('2026-09-17', '7:05 PM', self.chart['id']))
        result = seating._prepare_cards(seating.get(self.chart['id']), 'room-roster-test', publish=False, email=False)
        self.assertEqual(result['errors'], [])

    def test_response_status_distinguishes_missing_pending_and_submitted_cards(self):
        before = self.preview()
        for student in before['room_students'] + before['dr_students']:
            self.assertEqual(student['response_card'], {'label': 'No card issued', 'no_response': False})
        self.prepare_cards()
        with connect(self.path) as db:
            for name, status, delivery in (
                    ('Example, Bob', 'opened', 'sent'),
                    ('Student, 3', 'started', 'sent'),
                    ('Student, 4', 'verified', 'failed'),
                    ('Student, 5', 'needs_review', 'sent'),
                    ('Student, 6', 'expired', 'sent'),
                    ('Example, Carol', 'submitted', 'sent')):
                db.execute('UPDATE response_card_recipients SET status=?,delivery_status=? WHERE student_name=?',
                           (status, delivery, name))
        after = self.preview()
        self.assertNotEqual(before['fingerprint'], after['fingerprint'])
        students = {s['name']: s['response_card'] for s in after['room_students'] + after['dr_students']}
        for name in ('Example, Bob', 'Student, 3', 'Example, Alice'):
            self.assertEqual(students[name], {'label': 'No response', 'no_response': True})
        self.assertEqual(students['Student, 6'], {'label': 'No response — expired', 'no_response': True})
        self.assertEqual(students['Student, 4'], {'label': 'Responded', 'no_response': False})
        self.assertEqual(students['Example, Carol'], {'label': 'Responded', 'no_response': False})
        self.assertEqual(students['Student, 5'], {'label': 'Responded — needs review', 'no_response': False})
        with connect(self.path) as db:
            db.execute("UPDATE response_card_recipients SET status='verified' WHERE student_name='Example, Bob'")
        self.assertNotEqual(after['fingerprint'], self.preview()['fingerprint'])

    def test_response_states_are_read_once_for_the_whole_roster(self):
        from seating_roster import roster_snapshot
        self.prepare_cards()
        seating = self.app.extensions['seating']
        statements = []
        with seating.rosters.lock(), connect(self.path) as db:
            chart = seating.get(self.chart['id'], db)
            db.set_trace_callback(statements.append)
            try:
                actual = roster_snapshot(seating, db, chart)
            finally:
                db.set_trace_callback(None)
        reads = [sql for sql in statements if 'FROM response_card_recipients' in sql]
        self.assertEqual(len(reads), 1)
        self.assertEqual(actual, self.preview())

    def test_revoked_card_with_retained_submission_is_not_reported_as_no_response(self):
        self.prepare_cards()
        with connect(self.path) as db:
            card = db.execute("SELECT id FROM response_card_recipients WHERE student_name='Example, Bob'").fetchone()[0]
            db.execute('''INSERT INTO response_card_submissions VALUES(?,?,?,?,?,?)''',
                       ('room-roster-submission', card, 'roster-submission', '{}', '2026-09-17', '2026-09-17'))
            db.execute("UPDATE response_card_recipients SET status='revoked' WHERE id=?", (card,))
        bob = next(s for s in self.preview()['room_students'] if s['name'] == 'Example, Bob')
        self.assertEqual(bob['response_card'], {'label': 'Responded — revoked', 'no_response': False})

    def test_alphabetical_students_exact_uins_seats_and_current_exam_only(self):
        roster = self.preview()
        self.assertEqual(len(roster['room_students']), 7)
        self.assertEqual(len(roster['dr_students']), 2)
        for key in ('room_students', 'dr_students'):
            names = [s['name'] for s in roster[key]]
            self.assertEqual(names, sorted(names, key=str.casefold))
        source = {s['name']: s for s in self.chart['students']}
        for student in roster['room_students']:
            self.assertEqual(student['uin'], source[student['name']]['uin'])
            self.assertEqual(student['seat'], source[student['name']]['seat'])
            self.assertRegex(student['uin'], r'^[0-9]{9}$')
        alice = next(s for s in roster['dr_students'] if s['name'] == 'Example, Alice')
        self.assertEqual(alice['uin'], '001234567')
        self.assertEqual(alice['bookings'], [{
            'exam_type': 'Test 1', 'date': '2026-09-17', 'start_time': '19:05', 'end_time': '20:58',
            'length_minutes': 113, 'campus': 'College Station', 'requested_on': '2026-09-03', 'section_number': 507}])
        encoded = json.dumps(roster)
        self.assertNotIn('Final', encoded)
        self.assertNotIn('source_fields', encoded)
        self.assertNotIn('@example', encoded)
        for student in self.chart['students']:
            self.assertNotIn(student['enrollment_id'], encoded)
        self.assertEqual(self.preview(), roster)

    def test_current_revision_reschedule_changes_fingerprint_but_does_not_drop_other_bookings(self):
        before = self.preview()
        self.import_dr([booking(Date=datetime(2026, 9, 19), Campus='Galveston',
                                **{'Start Time': '09:30 AM', 'End Time': '11:23 AM'})])
        after = self.preview()
        self.assertNotEqual(before['fingerprint'], after['fingerprint'])
        self.assertEqual(len(after['dr_students']), 2)
        alice = next(s for s in after['dr_students'] if s['name'] == 'Example, Alice')
        self.assertEqual(alice['bookings'][0]['date'], '2026-09-19')
        self.assertEqual(alice['bookings'][0]['start_time'], '09:30')
        self.assertEqual(alice['bookings'][0]['campus'], 'Galveston')
        carol = next(s for s in after['dr_students'] if s['name'] == 'Example, Carol')
        self.assertEqual(carol['bookings'][0]['date'], '2026-09-17')

    def test_multiple_linked_bookings_are_all_reported_without_guessing(self):
        self.import_dr([booking(**{'Exam Type': 'Exam 1', 'Date': datetime(2026, 9, 18)})])
        roster = self.preview()
        alice = next(s for s in roster['dr_students'] if s['name'] == 'Example, Alice')
        self.assertEqual([b['exam_type'] for b in alice['bookings']], ['Test 1', 'Exam 1'])
        self.assertEqual(len(roster['warnings']), 1)
        self.assertIn('2 bookings', roster['warnings'][0])

    def test_other_room_sections_are_excluded(self):
        # Remove the first chart so each section can have its own chart.
        result = self.client.delete(f"/api/seating/{self.chart['id']}",
            json={'version': self.chart['version']}, headers=fixture.HEADERS)
        self.assertEqual(result.status_code, 204)
        one = self.create(section_ids=[self.sections[507]['id']]).json
        two = self.create(section_ids=[self.sections[508]['id']]).json
        first = self.client.get(f"/api/seating/{one['id']}/room-roster").json
        second = self.client.get(f"/api/seating/{two['id']}/room-roster").json
        self.assertEqual([s['name'] for s in first['dr_students']], ['Example, Alice'])
        self.assertEqual([s['name'] for s in second['dr_students']], ['Example, Carol'])
        self.assertTrue(all(s['section_number'] == 507 for s in first['room_students']))
        self.assertTrue(all(s['section_number'] == 508 for s in second['room_students']))

    def test_stale_seating_refuses_an_incomplete_room_roster(self):
        rows = [*fixture.SECTION_507, fixture.student_row('New, Student', '444444444')]
        result = self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster", data={
            'file': (io.BytesIO(csv_bytes(rows)), 'roster.csv')}, headers=fixture.HEADERS)
        self.assertEqual(result.status_code, 201)
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 409)
        self.assertIn('Update seating', response.json['error'])

    def test_room_review_is_required_and_preview_creates_no_records(self):
        def counts():
            with connect(self.path) as db:
                return {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in
                        ('seating_events', 'print_batches', 'seating_student_mail', 'response_card_mail', 'dr_exam_revisions')}
        before = counts()
        self.preview()
        self.assertEqual(counts(), before)
        with connect(self.path) as db:
            db.execute('UPDATE rooms SET review_required=? WHERE id=?', (json.dumps(['Seat labels need review.']), self.chart['room_id']))
        self.assertEqual(self.client.get(self.endpoint).status_code, 409)


if __name__ == '__main__':
    unittest.main()
