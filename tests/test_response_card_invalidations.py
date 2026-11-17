"""Seat-change access revocation and delivery safety, with fake remote services."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest import mock

from werkzeug.exceptions import Conflict, ServiceUnavailable

from response_cards.service import ResponseCardService
from rosters import connect
import test_response_card_mail as mail_tests


class CardInvalidationTests(unittest.TestCase):
    managed = False
    add = mail_tests.ResponseCardMailTests.add
    route = mail_tests.ResponseCardMailTests.route
    upload = mail_tests.ResponseCardMailTests.upload
    change = mail_tests.ResponseCardMailTests.change
    setUp = mail_tests.ResponseCardMailTests.setUp
    mail_rows = mail_tests.ResponseCardMailTests.mail_rows

    def invalidate(self, *card_ids):
        with self.cards.rosters.lock(), connect(self.cards.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self.cards.invalidate_cards(db, card_ids)

    def invalidation(self, card_id):
        with connect(self.cards.path) as db:
            return dict(db.execute('SELECT * FROM response_card_invalidations WHERE card_id=?', (card_id,)).fetchone())

    def recipient(self, card_id):
        return next(c for c in self.cards.get_issue(self.issue['id'])['recipients'] if c['id'] == card_id)

    def submission(self, card_id):
        self.remote.event(card_id, 'verified', submission={
            'id': 'submission-1', 'request_id': 'response-1', 'status': 'verified',
            'submitted_at': '2026-09-15T14:05:00.000Z',
            'answers': {'room': 'HECC 105', 'pencil': True, 'uin': '[UIN confirmed]'}})

    def test_invalidation_cancels_pending_invitation_and_receipt_without_sending(self):
        self.cards.publish(self.issue['id'])
        card_id = self.issue['recipients'][0]['id']
        self.cards.mail.queue_invitations(self.issue['id'])
        self.submission(card_id)
        self.cards.sync()
        self.invalidate(card_id)
        jobs = [r for r in self.mail_rows() if r['card_id'] == card_id]
        self.assertEqual({r['kind'] for r in jobs}, {'invitation', 'confirmation'})
        self.assertEqual({r['status'] for r in jobs}, {'cancelled'})
        before = len(self.remote.calls)
        result = self.cards.reconcile_invalidations([card_id])
        self.assertEqual(result, {'complete': 1, 'pending': 0, 'errors': []})
        self.assertEqual([c[0] for c in self.remote.calls[before:]], ['POST'])
        self.assertEqual(self.cards.reconcile_invalidations([card_id])['complete'], 0)
        self.assertEqual(self.cards.mail.queue_confirmations(), {'queued': 0})
        self.assertEqual(self.relay.sent, [])

    def test_failed_revoke_retains_local_guard_across_sync_and_restart(self):
        self.cards.publish(self.issue['id'])
        card_id = self.issue['recipients'][0]['id']
        self.invalidate(card_id)
        with mock.patch.object(self.remote, 'call', side_effect=ServiceUnavailable('Offline')):
            result = self.cards.reconcile_invalidations([card_id])
        self.assertEqual((result['complete'], result['pending']), (0, 1))
        self.assertEqual(self.invalidation(card_id)['error'], 'Offline')
        self.submission(card_id)
        self.cards.sync()
        card = self.recipient(card_id)
        self.assertEqual(card['status'], 'revoked')
        self.assertIsNone(card['link'])
        self.assertEqual(len(card['submissions']), 1)
        self.assertEqual(self.mail_rows(), [])
        with self.assertRaises(Conflict):
            self.cards.link(card_id)
        with self.assertRaises(Conflict):
            self.cards.resend(card_id)
        with self.assertRaises(Conflict):
            self.cards.control(card_id, {'action': 'unlock', 'request_id': 'bad-unlock'})
        restarted = ResponseCardService(self.cards.rosters, self.cards.seating, remote=self.remote)
        self.assertEqual(restarted.reconcile_invalidations([card_id])['complete'], 1)
        self.assertEqual(self.invalidation(card_id)['status'], 'complete')

    def test_unpublished_card_is_disabled_locally_and_cannot_publish(self):
        card_id = self.issue['recipients'][0]['id']
        self.invalidate(*(card['id'] for card in self.issue['recipients']))
        self.assertEqual(self.invalidation(card_id)['status'], 'complete')
        self.assertEqual(self.cards.reconcile_invalidations([card_id])['pending'], 0)
        with self.assertRaises(Conflict):
            self.cards.publish(self.issue['id'])
        self.assertEqual(self.remote.calls, [])

    def test_partly_invalidated_issue_can_publish_and_email_unchanged_students(self):
        stale, current = (card['id'] for card in self.issue['recipients'])
        self.invalidate(stale)
        self.cards.publish(self.issue['id'])
        self.assertEqual(set(self.remote.snapshots), {current})
        self.assertEqual(self.cards.mail.queue_invitations(self.issue['id']), {'queued': 1, 'no_email': 0})
        self.assertTrue(self.cards.mail.process_one())
        self.assertFalse(self.cards.mail.process_one())
        self.assertEqual([job['card_id'] for job in self.relay.sent], [current])

    def test_lost_publication_receipt_is_resolved_then_revoked(self):
        self.remote.lose_publish_receipt = True
        with self.assertRaises(ServiceUnavailable):
            self.cards.publish(self.issue['id'])
        card_id = next(iter(self.remote.snapshots))
        snapshot = self.remote.snapshots[card_id]
        self.invalidate(card_id)
        before = len(self.remote.calls)
        self.assertEqual(self.cards.reconcile_invalidations([card_id])['complete'], 1)
        calls = self.remote.calls[before:]
        self.assertEqual([call[0] for call in calls], ['PUT', 'POST'])
        self.assertEqual(calls[0][2], snapshot)
        self.assertEqual(calls[1][2], {'request_id': 'invalidate-' + card_id, 'action': 'revoke'})

    def test_invalidations_roll_back_with_failed_seat_transaction(self):
        self.cards.publish(self.issue['id'])
        self.cards.mail.queue_invitations(self.issue['id'])
        card_id = self.issue['recipients'][0]['id']
        with self.assertRaises(ValueError), self.cards.rosters.lock(), connect(self.cards.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self.cards.invalidate_cards(db, [card_id])
            raise ValueError('Simulated seat mutation failure')
        with connect(self.cards.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_card_invalidations').fetchone()[0], 0)
        self.assertEqual({r['status'] for r in self.mail_rows()}, {'pending'})
        self.assertNotEqual(self.recipient(card_id)['status'], 'revoked')

    def test_worker_rechecks_seating_before_transport(self):
        self.cards.publish(self.issue['id'])
        self.cards.mail.queue_invitations(self.issue['id'])
        with mock.patch.object(self.cards, 'validate_card_access', side_effect=Conflict('Seat changed')):
            while self.cards.mail.process_one():
                pass
        self.assertEqual(self.relay.sent, [])
        self.assertEqual({r['status'] for r in self.mail_rows()}, {'failed'})
        self.assertTrue(all('Seat changed' in r['error'] for r in self.mail_rows()))

    def test_updated_invitation_intent_survives_queue_and_manual_resend(self):
        with connect(self.cards.path) as db:
            db.execute("UPDATE response_card_templates SET name='Updated exam seat assignment (Faculty)'")
        self.cards.publish(self.issue['id'])
        self.cards.mail.queue_invitations(self.issue['id'])
        while self.cards.mail.process_one():
            pass
        card_id = self.issue['recipients'][0]['id']
        self.cards.resend(card_id)
        self.assertTrue(self.cards.mail.process_one())
        for message in self.relay.sent:
            self.assertTrue(message['subject'].startswith('UPDATED: '))
            self.assertIn('the earlier link no longer works', message['text_body'])
            self.assertTrue(message['text_body'].startswith('Hello,\n'))
        self.assertIn('This is a second copy', self.relay.sent[-1]['text_body'])

    def test_transport_and_receipt_finish_before_concurrent_invalidation(self):
        self.cards.publish(self.issue['id'])
        self.cards.mail.queue_invitations(self.issue['id'])
        started, finished = threading.Event(), threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = []

            def invalidate_during_send(card_id):
                started.set()
                self.invalidate(card_id)
                finished.set()

            def transport(job):
                pending.append(pool.submit(invalidate_during_send, job['card_id']))
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.1))
                return self.relay(job)

            self.cards.mail.transport = transport
            self.assertTrue(self.cards.mail.process_one())
            pending[0].result(timeout=5)
        delivered = next(r for r in self.mail_rows() if r['status'] == 'accepted')
        self.assertTrue(delivered['provider_receipt_json'])
        self.assertEqual(len(self.relay.sent), 1)
        self.assertEqual(self.invalidation(delivered['card_id'])['status'], 'pending')

    def test_publication_cannot_race_past_local_invalidation(self):
        started, finished = threading.Event(), threading.Event()
        original = self.remote.call
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = []

            def invalidate_during_publish(card_id):
                started.set()
                self.invalidate(card_id)
                finished.set()

            def publish(method, path, data=None):
                if method == 'PUT' and not pending:
                    pending.append(pool.submit(invalidate_during_publish, data['id']))
                    self.assertTrue(started.wait(1))
                    self.assertFalse(finished.wait(0.1))
                return original(method, path, data)

            with mock.patch.object(self.remote, 'call', side_effect=publish):
                self.cards.publish(self.issue['id'])
            pending[0].result(timeout=5)
        card_id = next(iter(self.remote.snapshots))
        self.assertEqual(self.invalidation(card_id)['status'], 'pending')
        self.assertEqual(self.cards.reconcile_invalidations([card_id])['complete'], 1)
