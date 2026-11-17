"""Confirmed-chart mail merge keeps card scope, freshness and delivery honest."""
import io
import json
import unittest
import uuid
from unittest.mock import patch

from openpyxl import load_workbook

from course_storage import timestamp
from response_cards.mail import MailConfig
from rosters import connect
from seating_mail_merge import HEADERS as COLUMNS, workbook
import test_seating as seating_fixture
from test_response_cards import FakeRemote
from test_response_card_mail import FakeRelay

HEADERS = seating_fixture.HEADERS


class FakeGmail:
    """Connected instructor account with an in-memory delivery receipt only."""
    def __init__(self, connected=True):
        self.connected = connected
        self.email = 'instructor@tamu.edu'
        self.sent = []

    def status(self):
        return {'configured': True, 'connected': self.connected,
                'email': self.email if self.connected else '', 'connected_at': ''}

    def send(self, payload, expected_email=None):
        assert expected_email == self.email
        self.sent.append(dict(payload))
        return {'mail_id': payload['mail_id'], 'status': 'accepted',
                'message_id': f'fake-gmail-{len(self.sent)}'}


class SeatingMailMergeTests(unittest.TestCase):
    import_dr = seating_fixture.SeatingTests.import_dr
    create = seating_fixture.SeatingTests.create

    def setUp(self):
        seating_fixture.SeatingTests.setUp(self)
        self.cards = self.app.extensions['response_cards']
        self.cards.remote = FakeRemote()
        self.relay = FakeRelay()
        self.cards.mail.config = MailConfig('https://relay.example.test/message', 'test-secret-' * 4)
        self.cards.mail.transport = self.relay
        self.seating = self.app.extensions['seating']
        self.path = self.app.extensions['rosters'].path
        self.chart = self.create().json
        self.route = f"/api/seating/{self.chart['id']}"
        # Catalog-only print version; these tests never render or claim physical
        # PDF qualification. Confirmation uses real printing/card reservation APIs.
        self.version_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute('INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (self.version_id, self.assignment['id'], 'A', 'source.json', 'a' * 64, 'canonical.pdf', 'b' * 64, 2, '{}', 'faculty-page-v1', timestamp()))
        if getattr(self, 'confirm_on_setup', True):
            self.confirm()

    def confirm(self, **extra):
        self.chart = self.client.get(self.route).json
        response = self.client.post(self.route + '/confirm', json={
            'version': self.chart['version'], 'request_id': str(uuid.uuid4()), 'mode': 'print', 'version_id': self.version_id,
            'exam_date': '2026-10-01', 'exam_time': '7:00 PM', 'publish_cards': True, 'email_cards': False, **extra}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        self.chart = response.json
        return self.chart

    def preview(self, audience='unsent'):
        result = self.client.get(self.route + '/mail-merge', query_string={'audience': audience})
        self.assertEqual(result.status_code, 200, result.json)
        return result.json

    def download(self, preview=None):
        preview = preview or self.preview()
        return self.client.get(self.route + '/mail-merge/download', query_string={
            'audience': preview['audience'], 'fingerprint': preview['fingerprint']})

    def recipient(self, name='Example, Bob'):
        with connect(self.path) as db:
            return dict(db.execute('SELECT * FROM response_card_recipients WHERE student_name=? ORDER BY created_at DESC,id DESC LIMIT 1', (name,)).fetchone())

    def test_confirm_publishes_without_email_then_workbook_has_complete_invitations(self):
        preview = self.preview()
        self.assertEqual(preview['eligible_count'], 9)
        self.assertEqual(preview['skipped_count'], 0)
        self.assertEqual({p['kind'] for p in preview['recipients']}, {'seat', 'dr'})
        self.assertNotIn('/r/', json.dumps(preview))
        calls = len(self.cards.remote.calls)
        response = self.download(preview)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn('spreadsheetml.sheet', response.content_type)
        self.assertIn('no-store', response.headers['Cache-Control'])
        book = load_workbook(io.BytesIO(response.data))
        self.assertEqual(book.sheetnames[:2], ['Recipients', 'Instructions'])
        rows = list(book['Recipients'].values)
        self.assertEqual(rows[0], COLUMNS)
        self.assertEqual(len(rows), 10)
        self.assertNotIn('Name', rows[0])
        self.assertNotIn('LastName', rows[0])
        with connect(self.path) as db:
            cards = [dict(row) for row in db.execute('SELECT * FROM response_card_recipients')]
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_card_mail').fetchone()[0], 0)
            private_values = [r[0] for r in db.execute('SELECT uin FROM student_identities')]
            private_values += [r[0] for r in db.execute('SELECT id FROM student_enrollments')]
        contents = json.dumps(rows)
        for value in private_values:
            self.assertNotIn(value, contents)
        for row in rows[1:]:
            values = dict(zip(rows[0], row))
            card = next(c for c in cards if c['email'] == values['Email'])
            self.assertEqual(values['FirstName'], 'Student')
            self.assertEqual(values['ResponseCardLink'], f"{card['published_url']}/r/{card['token']}")
            self.assertIn(values['ResponseCardLink'], values['Message'])
            self.assertTrue(values['Message'].startswith('Hello,\n\n'))
            self.assertNotIn(card['student_name'], values['Message'])
            self.assertIn('complete every item', values['Message'])
        self.assertTrue(all(cell.data_type == 's' for row in book['Recipients'] for cell in row if cell.value))
        self.assertEqual(len(self.cards.remote.calls), calls)
        self.assertEqual(self.relay.sent, [])
        self.assertEqual(self.preview()['fingerprint'], preview['fingerprint'])

    def test_a_restart_waits_for_a_confirm_and_none_starts_while_stopping(self):
        # SEATING "A RESTART WAITS": on 2026-10-15 a restart 4 s into a Confirm left seat cards issued and DR cards never issued.
        writes = self.app.extensions['canvas'].writes
        seen = []
        original = self.seating.confirm

        def watched(chart_id, data):
            seen.append(writes.labels())
            return original(chart_id, data)
        with patch.object(self.seating, 'confirm', side_effect=watched):
            self.confirm()
        self.assertEqual(seen, [["a seating chart's Confirm"]])
        self.assertEqual(writes.busy(), [])
        writes.close()
        chart = self.client.get(self.route).json
        before = chart['version']
        refused = self.client.post(self.route + '/confirm', json={
            'version': before, 'request_id': str(uuid.uuid4()), 'mode': 'print', 'version_id': self.version_id,
            'exam_date': '2026-10-01', 'exam_time': '7:00 PM'}, headers=HEADERS)
        self.assertEqual(refused.status_code, 503)
        self.assertIn('restarting', refused.json['error'])
        self.assertEqual(self.client.get(self.route).json['version'], before)

    def test_unconfirmed_chart_is_rejected_and_no_email_configuration_is_required(self):
        other = self.create(assignment_id=self.quiz['id']).json
        self.assertEqual(self.client.get(f"/api/seating/{other['id']}/mail-merge").status_code, 409)
        self.cards.mail.config = MailConfig()
        self.assertEqual(self.preview()['eligible_count'], 9)
        self.assertEqual(self.client.get(self.route + '/mail-merge', query_string={'audience': 'everyone'}).status_code, 400)

    def test_queued_sent_uncertain_and_inactive_cards_are_explained(self):
        with connect(self.path) as db:
            rows = list(db.execute('SELECT id FROM response_card_recipients ORDER BY id'))
            for row, field, value in ((rows[0], 'delivery_status', 'queued'), (rows[1], 'delivery_status', 'sent'),
                                      (rows[2], 'delivery_status', 'unconfirmed'), (rows[3], 'status', 'verified'),
                                      (rows[4], 'status', 'revoked'), (rows[5], 'status', 'expired'),
                                      (rows[6], 'publish_status', 'failed'), (rows[7], 'delivery_status', 'failed')):
                db.execute(f'UPDATE response_card_recipients SET {field}=? WHERE id=?', (value, row['id']))
        preview = self.preview()
        self.assertEqual((preview['eligible_count'], preview['skipped_count']), (2, 7))
        all_pending = self.preview('pending')
        self.assertEqual(all_pending['eligible_count'], 4)
        self.assertTrue(any('queued' in s['reason'] for s in all_pending['skipped']))
        book = load_workbook(io.BytesIO(self.download(preview).data))
        self.assertEqual(book['Skipped'].max_row, 8)

    def test_date_room_and_seat_changes_invalidate_personalized_notices(self):
        before = self.preview()
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_time='8:00 PM' WHERE id=?", (self.chart['id'],))
        changed = self.preview()
        self.assertEqual(changed['eligible_count'], 2)  # DR notice uses its own schedule.
        self.assertTrue(all('changed' in s['reason'] for s in changed['skipped']))
        self.assertEqual(self.download(before).status_code, 409)
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_time='7:00 PM' WHERE id=?", (self.chart['id'],))
        chart = self.client.get(self.route).json
        bob = next(s for s in chart['students'] if s['name'] == 'Example, Bob')
        seat = next(s for s in chart['room']['seats'] if not s['student'] and not s['broken'] and not s['blocked'])
        self.seating.move(chart['id'], chart['version'], bob['enrollment_id'], seat['id'])
        changed = self.preview()
        self.assertEqual(changed['eligible_count'], 8)
        self.assertIn('replaced after a seat change', changed['skipped'][0]['reason'].lower())
        with connect(self.path) as db:
            db.execute("UPDATE rooms SET name='Other room' WHERE id=?", (chart['room_id'],))
        self.assertEqual(self.preview()['eligible_count'], 2)

    def test_dr_changes_and_new_bookings_never_export_old_arrangements(self):
        self.import_dr([seating_fixture.booking(**{'Start Time': '09:00 AM', 'End Time': '11:00 AM'})])
        preview = self.preview()
        self.assertEqual(preview['eligible_count'], 8)
        self.assertEqual(preview['skipped'][0]['name'], 'Example, Alice')
        self.import_dr([seating_fixture.booking(UIN='123456789', **{'Last Name': 'Example', 'First Name': 'Bob'})])
        self.assertEqual(self.preview()['eligible_count'], 7)

    def test_current_roster_email_and_duplicate_addresses_are_checked(self):
        with self.app.extensions['rosters'].lock(), connect(self.path) as db:
            term = dict(db.execute('SELECT * FROM course_terms WHERE id=?', (self.chart['term_id'],)).fetchone())
            students = self.cards._students(db, term, self.chart['section_details'])
        other = next(s for s in students if s['name'] != 'Example, Bob')
        altered = [dict(s, email=other['email']) if s['name'] == 'Example, Bob' else s for s in students]
        with patch.object(self.cards, '_students', return_value=altered):
            preview = self.preview()
        self.assertEqual(preview['eligible_count'], 8)
        self.assertIn('email changed', preview['skipped'][0]['reason'])
        with patch.object(self.cards, '_students', return_value=altered):
            self.cards.control(self.recipient()['id'], {'action': 'revoke', 'request_id': str(uuid.uuid4())})
            self.confirm()
            preview = self.preview()
        self.assertEqual(preview['eligible_count'], 7)
        self.assertTrue(all('duplicate address' in s['reason'] for s in preview['skipped']))

    def test_reconfirmation_exports_replacement_only_and_formula_text_is_literal(self):
        chart = self.client.get(self.route).json
        bob = next(s for s in chart['students'] if s['name'] == 'Example, Bob')
        original = self.recipient()
        seat = next(s for s in chart['room']['seats'] if not s['student'] and not s['broken'] and not s['blocked'])
        self.seating.move(chart['id'], chart['version'], bob['enrollment_id'], seat['id'])
        self.confirm()
        response = self.download()
        book = load_workbook(io.BytesIO(response.data))
        self.assertEqual(book['Recipients'].max_row, 10)
        contents = json.dumps(list(book['Recipients'].values))
        self.assertNotIn(original['token'], contents)
        self.assertIn(self.recipient()['token'], contents)
        self.assertIn('this new card replaces it', contents)
        plan = self.preview()
        malicious = ['=SUM(1,2)', '+cmd', '-course', '001', '@exam', 'https://example.test/r/link', '=HYPERLINK("bad")']
        book = load_workbook(io.BytesIO(workbook(plan, [malicious])))
        self.assertEqual([c.value for c in book['Recipients'][2]], malicious)
        self.assertTrue(all(c.data_type == 's' for c in book['Recipients'][2]))

    def test_reminder_export_matches_initial_confirmation_email_without_names(self):
        self.confirm(email_cards=True)
        # The actual confirmation flow queued these originals. Deliver to the
        # fake relay so the same cards become eligible for the reminder export.
        while self.cards.mail.process_one():
            pass
        originals = {mail['recipient']: mail for mail in self.relay.sent}
        self.assertEqual(len(originals), 9)
        before = len(self.relay.sent)
        preview = self.preview('pending')
        book = load_workbook(io.BytesIO(self.download(preview).data))
        rows = list(book['Recipients'].values)
        normalized = set()
        for row in rows[1:]:
            values = dict(zip(rows[0], row))
            original = originals[values['Email']]
            self.assertEqual(values['Message'], original['text_body'])
            self.assertEqual(preview['subject'], original['subject'])
            self.assertNotIn('second copy', values['Message'])
            self.assertNotIn('Alice', values['Message'])
            self.assertNotIn('Bob', values['Message'])
            self.assertEqual(values['FirstName'], 'Student')
            normalized.add(values['Message'].replace(values['ResponseCardLink'], '[card link]'))
        self.assertEqual(len(normalized), 1)
        self.assertEqual(len(self.relay.sent), before)

    def test_queued_jobs_stay_excluded_even_when_recipient_summary_is_stale(self):
        before = self.preview()
        issue_id = self.chart['cards']['seat']['issue_id']
        self.assertEqual(self.cards.mail.queue_invitations(issue_id)['queued'], 7)
        with connect(self.path) as db:
            db.execute("UPDATE response_card_recipients SET delivery_status='not_sent' WHERE issue_id=?", (issue_id,))
            jobs = [dict(r) for r in db.execute('SELECT * FROM response_card_mail ORDER BY id')]
        self.assertEqual(self.download(before).status_code, 409)
        for audience in ('unsent', 'pending'):
            preview = self.preview(audience)
            self.assertEqual(preview['eligible_count'], 2)
            self.assertEqual(self.download(preview).status_code, 200)
        with connect(self.path) as db:
            self.assertEqual(jobs, [dict(r) for r in db.execute('SELECT * FROM response_card_mail ORDER BY id')])
        self.assertEqual(self.relay.sent, [])

    def test_unrepresentable_skipped_name_does_not_block_other_recipients(self):
        with self.seating.rosters.lock(), connect(self.path) as db:
            term = dict(db.execute('SELECT * FROM course_terms WHERE id=?', (self.chart['term_id'],)).fetchone())
            students = self.cards._students(db, term, self.chart['section_details'])
        altered = [dict(s, name='Example, Bob\x01') if s['name'] == 'Example, Bob' else s for s in students]
        # Both the chart's display name and the verified roster read may contain
        # this character: CSV names permit controls other than NUL.
        get_chart = self.seating.get
        def changed_chart(*args, **kwargs):
            chart = get_chart(*args, **kwargs)
            for student in chart['students']:
                if student['name'] == 'Example, Bob':
                    student['name'] += '\x01'
            return chart
        with patch.object(self.cards, '_students', return_value=altered), patch.object(self.seating, 'get', side_effect=changed_chart):
            preview = self.preview()
            self.assertEqual(preview['eligible_count'], 8)
            response = self.download(preview)
        self.assertEqual(response.status_code, 200)
        book = load_workbook(io.BytesIO(response.data))
        self.assertEqual(book['Recipients'].max_row, 9)
        self.assertEqual(book['Skipped']['A2'].value, 'Example, Bob\ufffd')


class SeatingGmailDeliveryTests(unittest.TestCase):
    """Gmail is an explicit invitation choice and cannot replace the default."""
    confirm_on_setup = False
    import_dr = SeatingMailMergeTests.import_dr
    create = SeatingMailMergeTests.create
    confirm = SeatingMailMergeTests.confirm

    def setUp(self):
        SeatingMailMergeTests.setUp(self)
        self.gmail = FakeGmail()
        self.cards.mail.gmail = self.gmail
        self.cards.mail.transport = self.cards.mail._post
        self.cards.mail.relay = lambda payload: self.relay({'id': payload['mail_id'], **payload})
        self.cards.mail.config = MailConfig('https://relay.example.test/message',
                                           'test-secret-' * 4, provider='resend')

    def mail_rows(self):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute('SELECT * FROM response_card_mail ORDER BY id')]

    def snapshot(self):
        # Includes print registrations so invalid choices cannot allocate stamps,
        # print copies, card versions, or mark a chart confirmed before failing.
        tables = ('seating_charts', 'seating_assignments', 'seating_events',
                  'assignment_versions', 'print_batches', 'printed_copies',
                  'response_card_templates', 'response_card_versions',
                  'response_card_issues', 'response_card_recipients',
                  'response_card_events', 'response_card_mail')
        with connect(self.path) as db:
            return {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in tables}

    def test_explicit_gmail_queues_seat_and_dr_invitations_without_changing_default(self):
        options = self.client.get(self.route + '/card-templates').json
        self.assertEqual(options['mail']['provider'], 'resend')
        self.assertEqual(options['mail']['gmail']['email'], self.gmail.email)
        self.confirm(email_cards=True, mail_provider='gmail')
        rows = self.mail_rows()
        self.assertEqual(len(rows), 9)
        self.assertEqual({row['provider'] for row in rows}, {'gmail'})
        self.assertEqual({row['gmail_sender'] for row in rows}, {self.gmail.email})
        with connect(self.path) as db:
            issue_ids = {row[0] for row in db.execute('''SELECT DISTINCT r.issue_id
                FROM response_card_mail m JOIN response_card_recipients r ON r.id=m.card_id''')}
        self.assertEqual(issue_ids, {self.chart['cards'][kind]['issue_id'] for kind in ('seat', 'dr')})
        summary = self.chart['response_cards']
        self.assertEqual((summary['seat']['queued'], summary['dr']['queued']), (7, 2))
        self.assertEqual(self.cards.mail.provider, 'resend')
        while self.cards.mail.process_one():
            pass
        self.assertEqual(len(self.gmail.sent), 9)
        self.assertEqual(self.relay.sent, [])
        self.assertTrue(all(message['text'].startswith('Hello,\n\n') for message in self.gmail.sent))
        self.assertEqual({row['status'] for row in self.mail_rows()}, {'accepted'})
        self.assertEqual(self.cards.mail.provider, 'resend')

    def test_omitted_provider_keeps_resend_even_with_gmail_connected(self):
        self.confirm(email_cards=True)
        self.assertEqual({row['provider'] for row in self.mail_rows()}, {'resend'})
        self.assertEqual({row['gmail_sender'] for row in self.mail_rows()}, {''})
        while self.cards.mail.process_one():
            pass
        self.assertEqual(len(self.relay.sent), 9)
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.cards.mail.provider, 'resend')

    def test_gmail_disconnect_during_publication_retains_cards_without_fallback(self):
        publish_call = self.cards.remote.call

        def disconnect_on_publish(method, path, data=None):
            result = publish_call(method, path, data)
            if method == 'PUT' and path.startswith('/faculty-api/cards/'):
                self.gmail.connected = False
            return result

        with patch.object(self.cards.remote, 'call', side_effect=disconnect_on_publish):
            self.confirm(email_cards=True, mail_provider='gmail')
        self.assertTrue(self.chart['confirmed_at'])
        self.assertTrue(self.chart['cards']['published'])
        self.assertFalse(self.chart['cards']['emailed'])
        self.assertTrue(self.chart['cards']['errors'])
        issue_ids = {self.chart['cards'][kind]['issue_id'] for kind in ('seat', 'dr')}
        with connect(self.path) as db:
            cards = [dict(row) for row in db.execute('SELECT * FROM response_card_recipients')]
            self.assertEqual({row[0] for row in db.execute('SELECT id FROM response_card_issues')}, issue_ids)
        self.assertEqual(len(cards), 9)
        self.assertEqual({card['issue_id'] for card in cards}, issue_ids)
        self.assertEqual({card['publish_status'] for card in cards}, {'published'})
        self.assertEqual({card['delivery_status'] for card in cards}, {'not_sent'})
        self.assertEqual(self.mail_rows(), [])
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.relay.sent, [])
        self.assertEqual(self.cards.mail.provider, 'resend')

    def test_invalid_or_disconnected_gmail_rejects_before_confirmation_mutates_anything(self):
        before = self.snapshot()
        self.gmail.connected = False
        for choice, status in (('gmail', 503), ('other', 400), (None, 400), ([], 400)):
            with self.subTest(choice=choice):
                # An invalid choice must fail before generating even blank pages.
                with patch.object(self.seating.documents, 'register_stamp_pages',
                                  side_effect=AssertionError('Printing ran before provider validation')):
                    response = self.client.post(self.route + '/confirm', json={
                        'version': self.chart['version'], 'request_id': str(uuid.uuid4()),
                        'mode': 'stamps_only', 'page_count': 2,
                        'exam_date': '2026-10-01', 'exam_time': '7:00 PM',
                        'publish_cards': True, 'email_cards': True,
                        'mail_provider': choice}, headers=HEADERS)
                self.assertEqual(response.status_code, status, response.json)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.relay.sent, [])


if __name__ == '__main__':
    unittest.main()
