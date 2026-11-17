"""Confirmed-chart Gmail sends approve an exact audience and queue it only once."""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from response_cards.mail import MailConfig
from rosters import connect
from seating_gmail import SeatingGmail
import test_seating_mail_merge as fixture


class SeatingGmailTests(unittest.TestCase):
    import_dr = fixture.SeatingMailMergeTests.import_dr
    create = fixture.SeatingMailMergeTests.create
    confirm = fixture.SeatingMailMergeTests.confirm
    recipient = fixture.SeatingMailMergeTests.recipient

    def setUp(self):
        fixture.SeatingMailMergeTests.setUp(self)
        self.gmail = fixture.FakeGmail()
        self.cards.mail.gmail = self.gmail
        self.cards.mail.config = MailConfig('https://relay.example.test/message',
                                           'test-secret-' * 4, provider='resend')
        self.cards.mail.transport = self.cards.mail._post
        self.cards.mail.relay = lambda payload: self.relay({'id': payload['mail_id'], **payload})
        self.endpoint = self.route + '/gmail-send'

    def preview(self, audience='unsent'):
        response = self.client.get(self.endpoint, query_string={'audience': audience})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def payload(self, preview=None, **extra):
        preview = preview or self.preview()
        return {'audience': preview['audience'], 'fingerprint': preview['fingerprint'],
                'request_id': str(uuid.uuid4()), **extra}

    def send(self, payload):
        return self.client.post(self.endpoint, json=payload, headers=fixture.HEADERS)

    def rows(self):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute('SELECT * FROM response_card_mail ORDER BY id')]

    def drain(self):
        while self.cards.mail.process_one():
            pass

    def test_preview_is_read_only_and_send_queues_both_card_types_with_original_wording(self):
        remote_calls = len(self.cards.remote.calls)
        before_chart = self.client.get(self.route).json
        preview = self.preview()
        self.assertEqual(preview['eligible_count'], 9)
        self.assertEqual({r['kind'] for r in preview['recipients']}, {'seat', 'dr'})
        self.assertEqual(preview['sender'], self.gmail.email)
        self.assertTrue(preview['can_send'])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.gmail.sent, [])
        self.assertNotIn('/r/', json.dumps(preview))
        with connect(self.path) as db:
            cards = {row['id']: dict(row) for row in db.execute('SELECT * FROM response_card_recipients')}
            for card in cards.values():
                self.assertNotIn(card['token'], json.dumps(preview))
        result = self.send(self.payload(preview))
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['queued'], 9)
        self.assertEqual(result.json['sender'], self.gmail.email)
        self.assertEqual(self.gmail.sent, [])  # Only the worker sends.
        self.assertEqual(self.relay.sent, [])
        with connect(self.path) as db:
            for row in self.rows():
                card = cards[row['card_id']]
                original = self.cards.mail.invitation(db, card)
                self.assertEqual((row['subject'], row['text_body'], row['html_body']),
                                 (original['subject'], original['text'], original['html']))
                self.assertEqual((row['provider'], row['gmail_sender'], row['status']),
                                 ('gmail', self.gmail.email, 'pending'))
                self.assertNotIn(card['student_name'], row['text_body'])
        self.assertEqual(self.cards.mail.provider, 'resend')
        after_chart = self.client.get(self.route).json
        self.assertEqual(after_chart['confirmed_at'], before_chart['confirmed_at'])
        self.assertEqual(after_chart['cards']['seat']['issue_id'], before_chart['cards']['seat']['issue_id'])
        self.assertEqual(len(self.cards.remote.calls), remote_calls)
        self.drain()
        self.assertEqual(len(self.gmail.sent), 9)
        self.assertEqual(self.relay.sent, [])
        self.assertEqual({r['status'] for r in self.rows()}, {'accepted'})

    def test_audience_excludes_complete_queued_and_uncertain_unless_explicitly_selected(self):
        with connect(self.path) as db:
            rows = list(db.execute('SELECT id FROM response_card_recipients ORDER BY id'))
            for row, field, value in ((rows[0], 'delivery_status', 'sent'),
                                      (rows[1], 'delivery_status', 'unconfirmed'),
                                      (rows[2], 'delivery_status', 'queued'),
                                      (rows[3], 'status', 'verified'),
                                      (rows[4], 'status', 'revoked'),
                                      (rows[5], 'status', 'expired')):
                db.execute(f'UPDATE response_card_recipients SET {field}=? WHERE id=?', (value, row['id']))
        self.assertEqual(self.preview()['eligible_count'], 3)
        pending = self.preview('pending')
        self.assertEqual(pending['eligible_count'], 5)
        self.assertEqual(self.send(self.payload(pending)).json['queued'], 5)
        self.assertTrue(all('second copy' not in row['text_body'] for row in self.rows()))
        with connect(self.path) as db:
            statuses = {row[0] for row in db.execute('''SELECT r.status FROM response_card_mail m
                JOIN response_card_recipients r ON r.id=m.card_id''')}
        self.assertFalse(statuses & {'verified', 'revoked', 'expired'})

    def test_retry_returns_original_receipt_before_and_after_delivery(self):
        payload = self.payload()
        first = self.send(payload)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(self.send(payload).json, first.json)
        self.drain()
        self.assertEqual(self.send(payload).json, first.json)
        self.assertEqual(len(self.rows()), 9)
        self.assertEqual(len(self.gmail.sent), 9)
        self.assertEqual(self.relay.sent, [])
        changed = {**payload, 'audience': 'pending'}
        self.assertEqual(self.send(changed).status_code, 409)
        with connect(self.path) as db:
            events = [dict(row) for row in db.execute("SELECT * FROM seating_events WHERE kind='gmail_send'")]
            self.assertEqual(len(events), 1)
            self.assertNotIn('/r/', json.dumps(events))
            for row in db.execute('SELECT token FROM response_card_recipients'):
                self.assertNotIn(row['token'], json.dumps(events))

    def test_previously_sent_pending_cards_can_be_explicitly_sent_once_more(self):
        first = self.send(self.payload())
        self.assertEqual(first.status_code, 200, first.json)
        self.drain()
        self.assertEqual(self.preview()['eligible_count'], 0)
        pending = self.preview('pending')
        self.assertEqual(pending['eligible_count'], 9)
        payload = self.payload(pending)
        second = self.send(payload)
        self.assertEqual(second.json['queued'], 9)
        self.assertEqual(self.send(payload).json, second.json)
        self.drain()
        self.assertEqual(len(self.gmail.sent), 18)
        self.assertTrue(all('second copy' not in item['text'] for item in self.gmail.sent))

    def test_stale_preview_and_different_account_reject_without_queuing(self):
        preview = self.preview()
        card = self.recipient()
        with connect(self.path) as db:
            db.execute("UPDATE response_card_recipients SET status='verified' WHERE id=?", (card['id'],))
        self.assertEqual(self.send(self.payload(preview)).status_code, 409)
        self.assertEqual(self.rows(), [])
        preview = self.preview()
        self.gmail.email = 'another@tamu.edu'
        self.assertEqual(self.send(self.payload(preview)).status_code, 409)
        self.assertEqual(self.rows(), [])

    def test_changed_seat_and_dr_booking_use_shared_freshness_validation(self):
        before = self.preview()
        chart = self.client.get(self.route).json
        bob = next(s for s in chart['students'] if s['name'] == 'Example, Bob')
        seat = next(s for s in chart['room']['seats'] if not s['student'] and not s['broken'] and not s['blocked'])
        self.seating.move(chart['id'], chart['version'], bob['enrollment_id'], seat['id'])
        self.assertEqual(self.send(self.payload(before)).status_code, 409)
        self.assertEqual(self.preview()['eligible_count'], 8)
        self.import_dr([fixture.seating_fixture.booking(**{'Start Time': '09:00 AM', 'End Time': '11:00 AM'})])
        after = self.preview()
        self.assertEqual(after['eligible_count'], 7)
        self.assertEqual({s['name'] for s in after['skipped']}, {'Example, Bob', 'Example, Alice'})
        self.assertEqual(self.rows(), [])

    def test_disconnected_invalid_empty_and_unconfirmed_requests_do_not_queue(self):
        preview = self.preview()
        self.gmail.connected = False
        self.assertFalse(self.preview()['can_send'])
        self.assertEqual(self.send(self.payload(preview)).status_code, 503)
        self.gmail.connected = True
        for payload in ({}, self.payload(preview, audience='all'), self.payload(preview, request_id=''),
                        self.payload(preview, fingerprint=None), self.payload(preview, request_id=[])):
            self.assertIn(self.send(payload).status_code, (400, 409))
        unconfirmed = self.create(assignment_id=self.quiz['id']).json
        self.assertEqual(self.client.get(f"/api/seating/{unconfirmed['id']}/gmail-send").status_code, 409)
        with connect(self.path) as db:
            db.execute("UPDATE response_card_recipients SET status='verified'")
        empty = self.preview()
        self.assertEqual(empty['eligible_count'], 0)
        self.assertFalse(empty['can_send'])
        self.assertEqual(self.send(self.payload(empty)).status_code, 409)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.gmail.sent, [])
        self.assertEqual(self.relay.sent, [])

    def test_queue_failure_rolls_back_entire_batch_and_receipt(self):
        payload = self.payload()
        original_queue = self.cards.mail._queue
        calls = []

        def failing_queue(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise RuntimeError('simulated catalog write failure')
            return original_queue(*args, **kwargs)

        with patch.object(self.cards.mail, '_queue', side_effect=failing_queue):
            with self.assertRaises(RuntimeError):
                self.send(payload)
        self.assertEqual(self.rows(), [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM seating_events WHERE kind='gmail_send'").fetchone()[0], 0)
            self.assertEqual({row[0] for row in db.execute('SELECT delivery_status FROM response_card_recipients')}, {'not_sent'})
        self.assertEqual(self.send(payload).json['queued'], 9)

    def test_post_requires_the_existing_same_origin_write_guard(self):
        response = self.client.post(self.endpoint, json=self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rows(), [])

    def test_room_change_between_revalidation_and_queue_is_rejected_atomically(self):
        payload = self.payload()
        service = SeatingGmail(self.seating)
        plan = service._plan

        def changed_room(*args):
            result = plan(*args)
            room = self.seating.rooms.get(self.chart['room_id'])
            occupied = next(s for s in self.chart['students'] if s['seat_id'])
            self.seating.rooms.set_conditions(room['id'], room['version'],
                                             [occupied['seat_id']], {'blocked': True})
            return result

        with patch.object(service, '_plan', side_effect=changed_room):
            with self.assertRaisesRegex(Conflict, 'room changed'):
                service.send(self.chart['id'], payload)
        self.assertEqual(self.rows(), [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM seating_events WHERE kind='gmail_send'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
