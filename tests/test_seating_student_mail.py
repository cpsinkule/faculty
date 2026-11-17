"""Personal seating PDFs: private content, exact audience, durable safe delivery."""
import io
import json
import time
import unittest
import uuid
from unittest.mock import patch

from pypdf import PdfReader
from response_cards.mail import MailConfig, RelayRateLimit, provider_pauses
from rosters import connect
import test_seating as fixture
from test_seating_mail_merge import FakeGmail
from test_rosters import COLUMNS, csv_bytes


class StudentChartMailTests(unittest.TestCase):
    import_dr = fixture.SeatingTests.import_dr
    create = fixture.SeatingTests.create

    def setUp(self):
        fixture.SeatingTests.setUp(self)
        self.service = self.app.extensions['seating_student_mail']
        self.path = self.service.path
        self.mail = self.app.extensions['response_cards'].mail
        self.mail.config = MailConfig('https://relay.example.test/message', 'test-secret-' * 4, provider='resend')
        self.mail.gmail = FakeGmail()
        self.chart = self.create(exam_version_count=3).json
        self.route = f"/api/seating/{self.chart['id']}"
        self.endpoint = self.route + '/student-email'
        self.sent = []
        self.service.transport = self.deliver

    def deliver(self, job, pdf):
        self.sent.append((job, pdf))
        return {'export_id': job['id'], 'attachment_sha256': job['output_sha256'],
                'status': 'accepted', 'message_id': 'mock-provider'}

    def preview(self, **query):
        result = self.client.get(self.endpoint, query_string=query)
        self.assertEqual(result.status_code, 200, result.json)
        return result.json

    def payload(self, preview=None, **extra):
        preview = preview or self.preview()
        return {'request_id': str(uuid.uuid4()), 'fingerprint': preview['fingerprint'],
                'audience': preview['audience'], 'mail_provider': preview['mail_provider'], **extra}

    def send(self, payload):
        return self.client.post(self.endpoint, json=payload, headers=fixture.HEADERS)

    def rows(self):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute('SELECT * FROM seating_student_mail ORDER BY created_at,id')]

    def drain(self):
        while self.service.process_one():
            pass

    def test_preview_and_each_pdf_are_read_only_and_contain_no_names_or_private_data(self):
        preview = self.preview()
        self.assertEqual(preview['eligible_count'], 7)
        self.assertEqual(preview['skipped_count'], 2)
        self.assertTrue(preview['can_send'])
        self.assertFalse(self.chart['confirmed_at'])
        for recipient in preview['recipients'][:2]:
            result = self.client.get(self.endpoint + '/preview', query_string={
                'enrollment_id': recipient['enrollment_id'], 'fingerprint': preview['fingerprint']})
            self.assertEqual(result.status_code, 200, result.json)
            text = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(result.data)).pages)
            self.assertIn('Your seat', text)
            self.assertIn('Occupied', text)
            self.assertIn(recipient['seat'], text)
            self.assertNotIn('Response', text)
            for student in self.chart['students']:
                for private in ('name', 'uin', 'enrollment_id'):
                    self.assertNotIn(student[private], text)
            self.assertIn('no-store', result.headers['Cache-Control'])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.sent, [])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_card_issues').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM print_batches').fetchone()[0], 0)
        foreign = self.client.get(self.endpoint + '/preview', query_string={
            'enrollment_id': '0000000000', 'fingerprint': preview['fingerprint']})
        self.assertEqual(foreign.status_code, 404)

    def test_send_is_atomic_idempotent_and_worker_delivers_only_personal_pdfs(self):
        payload = self.payload()
        first = self.send(payload)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json['queued'], 7)
        self.assertEqual(self.send(payload).json, first.json)
        self.assertEqual(len(self.rows()), 7)
        self.assertTrue(all(row['pdf'] is None for row in self.rows()))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.preview()['eligible_count'], 0)
        self.drain()
        self.assertEqual(len(self.sent), 7)
        self.assertEqual({row['status'] for row in self.rows()}, {'accepted'})
        self.assertTrue(all(row['provider_receipt_json'] for row in self.rows()))
        self.assertEqual(self.send(payload).json, first.json)
        self.assertEqual(self.preview()['eligible_count'], 0)
        self.assertEqual(self.preview(audience='all')['eligible_count'], 7)
        self.assertEqual(self.send({**payload, 'audience': 'all'}).status_code, 409)
        for job, pdf in self.sent:
            self.assertEqual(job['provider'], 'resend')
            text = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
            self.assertIn(job['seat'], text)
            for student in self.chart['students']:
                self.assertNotIn(student['name'], text)
                self.assertNotIn(student['name'], job['text_body'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_card_mail').fetchone()[0], 0)

    def test_stale_preview_is_rejected_without_any_queue_rows(self):
        preview = self.preview()
        student = next(s for s in self.chart['students'] if s['status'] == 'seated')
        free = next(s for s in self.chart['room']['seats'] if not s['student'] and not s['broken'] and not s['blocked'])
        result = self.client.post(self.route + '/move', json={'version': self.chart['version'],
            'enrollment_id': student['enrollment_id'], 'seat_id': free['id']}, headers=fixture.HEADERS)
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.send(self.payload(preview)).status_code, 409)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.client.get(self.endpoint + '/preview', query_string={
            'enrollment_id': student['enrollment_id'], 'fingerprint': preview['fingerprint']}).status_code, 409)

    def test_move_before_delivery_cancels_all_stale_occupancy_pdfs(self):
        self.assertEqual(self.send(self.payload()).status_code, 200)
        student = next(s for s in self.chart['students'] if s['status'] == 'seated')
        free = next(s for s in self.chart['room']['seats'] if not s['student'] and not s['broken'] and not s['blocked'])
        self.client.post(self.route + '/move', json={'version': self.chart['version'],
            'enrollment_id': student['enrollment_id'], 'seat_id': free['id']}, headers=fixture.HEADERS)
        self.drain()
        self.assertEqual(self.sent, [])
        self.assertEqual({row['status'] for row in self.rows()}, {'failed'})
        self.assertEqual(self.preview()['eligible_count'], 7)

    def test_changed_email_after_queue_is_not_sent(self):
        self.send(self.payload())
        target = next(s for s in self.chart['students'] if s['status'] == 'seated' and s['section_number'] == 507)
        rows = [row[:] for row in fixture.SECTION_507]
        for row in rows:
            if row[1] == target['name']:
                row[COLUMNS.index('Email')] = 'changed@example.test'
        result = self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster", data={
            'file': (io.BytesIO(csv_bytes(rows)), 'roster.csv')}, headers=fixture.HEADERS)
        self.assertEqual(result.status_code, 201)
        self.drain()
        failed = [row for row in self.rows() if row['status'] == 'failed']
        self.assertEqual([row['enrollment_id'] for row in failed], [target['enrollment_id']])
        self.assertEqual(len(self.sent), 6)

    def test_duplicate_addresses_are_excluded_for_both_people(self):
        rows = [row[:] for row in fixture.SECTION_507]
        for row in rows[1:3]:
            row[COLUMNS.index('Email')] = 'shared@example.test'
        self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster", data={
            'file': (io.BytesIO(csv_bytes(rows)), 'roster.csv')}, headers=fixture.HEADERS)
        preview = self.preview()
        self.assertEqual(preview['eligible_count'], 5)
        self.assertEqual(sum('shared by multiple' in s['reason'] for s in preview['skipped']), 2)

    def test_interruption_recovers_receipts_but_never_retries_uncertainty(self):
        self.send(self.payload())
        jobs = self.rows()
        with connect(self.path) as db:
            db.execute("UPDATE seating_student_mail SET status='sending'")
            db.execute('UPDATE seating_student_mail SET provider_receipt_json=? WHERE id=?',
                       (json.dumps({'status': 'accepted', 'message_id': 'mock'}), jobs[0]['id']))
        self.assertTrue(self.service.process_one())
        self.assertEqual(self.sent, [])
        self.assertEqual(sum(row['status'] == 'accepted' for row in self.rows()), 1)
        self.assertEqual(sum(row['status'] == 'unconfirmed' for row in self.rows()), 6)
        self.assertFalse(self.service.process_one())
        self.assertEqual(self.preview(audience='all')['eligible_count'], 1)

    def test_provider_pause_survives_restart_and_blocks_shared_card_transport(self):
        self.send(self.payload())
        self.service.transport = lambda job, pdf: (_ for _ in ()).throw(RelayRateLimit(300))
        self.assertFalse(self.service.process_one())
        with connect(self.path) as db:
            self.assertGreater(provider_pauses(db)['relay'], time.time())
        from seating_student_mail import SeatingStudentMail
        restarted = SeatingStudentMail(self.app.extensions['seating'])
        restarted.transport = self.deliver
        self.assertFalse(restarted.process_one())
        self.assertEqual(self.sent, [])
        self.assertEqual(sum(row['attempts'] for row in self.rows()), 1)

    def test_gmail_sender_is_pinned_and_unavailable_does_not_fall_back(self):
        preview = self.preview(mail_provider='gmail')
        self.assertEqual(self.send(self.payload(preview)).status_code, 200)
        self.mail.gmail.email = 'different@tamu.edu'
        self.assertFalse(self.service.process_one())
        self.assertEqual(self.sent, [])
        self.assertTrue(all(row['provider'] == 'gmail' and row['gmail_sender'] == 'instructor@tamu.edu' for row in self.rows()))

    def test_pdf_preparation_runs_in_worker_and_failure_never_sends(self):
        with patch('seating_student_mail.render_pdf', side_effect=ValueError('Unable to render this chart.')) as render:
            self.assertEqual(self.send(self.payload()).status_code, 200)
            render.assert_not_called()
            self.assertTrue(self.service.process_one())
        failed = [row for row in self.rows() if row['status'] == 'failed']
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]['attempts'], 0)
        self.assertEqual(self.sent, [])
        self.drain()
        self.assertEqual(len(self.sent), 6)

    def test_interrupt_during_preparation_stays_pending_and_is_safe_to_resume(self):
        self.send(self.payload())
        with patch('seating_student_mail.render_pdf', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.process_one()
        self.assertEqual({row['status'] for row in self.rows()}, {'pending'})
        self.assertEqual(sum(row['attempts'] for row in self.rows()), 0)
        self.assertEqual(self.sent, [])
        self.drain()
        self.assertEqual(len(self.sent), 7)

    def test_tampered_approved_snapshot_fails_before_transmission(self):
        self.send(self.payload())
        with connect(self.path) as db:
            db.execute("UPDATE seating_student_mail SET snapshot_blob=?", (b'corrupt',))
        self.drain()
        self.assertEqual({row['status'] for row in self.rows()}, {'failed'})
        self.assertEqual(self.sent, [])

    def test_cross_origin_write_and_invalid_options_are_rejected(self):
        self.assertIn(self.client.post(self.endpoint, json=self.payload()).status_code, (400, 403))
        self.assertEqual(self.client.get(self.endpoint, query_string={'audience': 'anything'}).status_code, 400)
        self.assertEqual(self.client.get(self.endpoint, query_string={'mail_provider': 'anything'}).status_code, 400)
        self.assertEqual(self.rows(), [])


if __name__ == '__main__':
    unittest.main()
