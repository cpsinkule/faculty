"""Changed confirmed seats keep an update pending until the current card is emailed."""
import hashlib
import io
import json
import unittest
import uuid
from unittest.mock import patch

from openpyxl import load_workbook
from werkzeug.exceptions import ServiceUnavailable

from response_cards.mail import DeliveryError, MailConfig
from rosters import connect
import test_seating_mail_merge as fixture

HEADERS = fixture.HEADERS


class SeatingUpdateTests(unittest.TestCase):
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
        self.cards.mail.relay = lambda payload: self.relay({
            **payload, 'id': payload['mail_id'], 'text_body': payload['text'], 'html_body': payload['html']})
        self.confirm(email_cards=True)
        self.drain()
        self.assertEqual(len(self.relay.sent), 9)

    def drain(self):
        while self.cards.mail.process_one():
            pass

    def current(self):
        return self.client.get(self.route).json

    def student(self, name='Example, Bob', chart=None):
        return next(item for item in (chart or self.current())['students'] if item['name'] == name)

    def move(self, name='Example, Bob', seat_id=None):
        chart = self.current()
        if seat_id is None:
            seat_id = next(seat['id'] for seat in chart['room']['seats']
                           if not seat['student'] and not seat['broken'] and not seat['blocked'])
        response = self.client.post(self.route + '/move', json={
            'version': chart['version'], 'enrollment_id': self.student(name, chart)['enrollment_id'],
            'seat_id': seat_id}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def send_updates(self, **changes):
        response = self.client.post(self.route + '/seat-updates', json={
            'version': self.current()['version'], 'request_id': str(uuid.uuid4()),
            'mail_provider': 'default', **changes}, headers=HEADERS)
        self.assertIn(response.status_code, (200, 201), response.json)
        return response.json

    def mail_rows(self, card_id=None):
        with connect(self.path) as db:
            query = 'SELECT * FROM response_card_mail'
            return [dict(row) for row in db.execute(
                query + (' WHERE card_id=?' if card_id else '') + ' ORDER BY created_at,id',
                (card_id,) if card_id else ())]

    def assert_update_pending(self, name='Example, Bob', chart=None):
        chart = chart or self.current()
        student = self.student(name, chart)
        self.assertEqual(student['card']['state'], 'update_pending')
        tile = next(seat for seat in chart['room']['seats'] if seat['id'] == student['seat_id'])
        self.assertEqual(tile['student']['card']['state'], 'update_pending')

    def assert_update_schema(self, card, seat):
        schema = json.loads(card['snapshot_json'])['schema']
        self.assertEqual([item['id'] for item in schema['fields']], ['confirm_uin'])
        self.assertIn(seat, schema['description'])
        self.assertIn('HECC 105', schema['description'])
        self.assertIn('Test 1', schema['description'])
        verification = schema['fields'][0]['verification']
        self.assertEqual((verification['mode'], verification['on_mismatch']), ('uin-digest', 'BLOCK_SUBMISSION'))
        salt, digest = verification['expected'].split(':')
        self.assertEqual(hashlib.sha256((salt + '123456789').encode()).hexdigest(), digest)
        self.assertNotIn('123456789', json.dumps(schema))

    def test_completed_prior_card_is_revoked_and_update_stays_pending_until_accepted(self):
        old = self.recipient()
        self.cards.remote.event(old['id'], 'verified', submission={
            'id': 'completed-before-move', 'request_id': 'completed-before-move', 'status': 'verified',
            'submitted_at': '2026-09-16T12:00:00.000Z', 'answers': {'confirm_uin': '[UIN confirmed]'},
            'verification': {'confirm_uin': {'matched': True, 'on_mismatch': 'BLOCK_SUBMISSION'}}})
        self.cards.sync()
        self.drain()
        self.assertEqual(self.student()['card']['state'], 'complete')
        templates = self.cards.templates(self.chart['definition_id'])
        with connect(self.path) as db:
            originals = {row['id']: dict(row) for row in db.execute('SELECT * FROM response_card_recipients')}
        moved = self.move()
        self.assert_update_pending(chart=moved)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM response_card_recipients WHERE id=?', (old['id'],)).fetchone()[0], 'revoked')
        self.assertTrue(any(key[0] == old['id'] and action == 'revoke' for key, action in self.cards.remote.controls.items()))
        self.assertEqual(self.client.get(f"/api/response-cards/cards/{old['id']}/link").status_code, 409)
        before = len(self.relay.sent)
        sent = self.send_updates()
        self.assertEqual(sent['seat_update_send']['queued'], 1)
        updated = self.recipient()
        self.assertNotEqual(updated['id'], old['id'])
        self.assert_update_schema(updated, self.student()['seat'])
        self.assert_update_pending(chart=sent)
        self.assertEqual(len(self.relay.sent), before)
        self.assertEqual(self.mail_rows(updated['id'])[0]['status'], 'pending')
        # A replacement must not rewrite the ordinary course-wide card template
        # or change another student's retained snapshot.
        ordinary_before = [t for t in templates if t['name'] == 'Exam seat assignment (Faculty)']
        ordinary_after = [t for t in self.cards.templates(self.chart['definition_id']) if t['name'] == 'Exam seat assignment (Faculty)']
        self.assertEqual(ordinary_after, ordinary_before)
        with connect(self.path) as db:
            for card_id, card in originals.items():
                current = db.execute('SELECT * FROM response_card_recipients WHERE id=?', (card_id,)).fetchone()
                self.assertEqual(current['snapshot_json'], card['snapshot_json'])
        self.drain()
        self.assertEqual(len(self.relay.sent), before + 1)
        self.assertEqual(self.student()['card']['state'], 'pending')
        self.assertEqual(self.student()['card']['card_id'], updated['id'])
        self.assertEqual(self.mail_rows(updated['id'])[0]['status'], 'accepted')
        message = self.relay.sent[-1]
        self.assertTrue(message['subject'].startswith('UPDATED:'))
        self.assertTrue(message['text_body'].startswith('Hello,\n\n'))
        self.assertIn(updated['token'], message['text_body'])
        self.assertNotIn(old['token'], message['text_body'])
        self.assertNotIn('Bob', message['subject'] + message['text_body'])

    def test_swap_marks_both_students_and_only_queues_their_new_cards(self):
        bob, other = self.student(), self.student('Student, 4')
        old = {name: self.recipient(name) for name in ('Example, Bob', 'Student, 4')}
        moved = self.move(seat_id=other['seat_id'])
        self.assertEqual(self.student('Student, 4', moved)['seat_id'], bob['seat_id'])
        for name in old:
            self.assert_update_pending(name, moved)
        sent = self.send_updates()
        self.assertEqual(sent['seat_update_send']['queued'], 2)
        for name in old:
            self.assert_update_pending(name, sent)
        queued = [row for row in self.mail_rows() if row['status'] == 'pending']
        self.assertEqual({row['recipient'] for row in queued}, {card['email'] for card in old.values()})
        self.assertTrue(all(any(key[0] == card['id'] for key in self.cards.remote.controls) for card in old.values()))
        self.drain()
        self.assertEqual(len(self.relay.sent), 11)
        for name in old:
            self.assertEqual(self.student(name)['card']['state'], 'pending')

    def test_request_replay_does_not_duplicate_and_a_later_move_needs_a_new_delivery(self):
        self.move()
        payload = {'version': self.current()['version'], 'request_id': str(uuid.uuid4()), 'mail_provider': 'default'}
        first = self.client.post(self.route + '/seat-updates', json=payload, headers=HEADERS)
        self.assertIn(first.status_code, (200, 201), first.json)
        self.assertEqual(first.json['seat_update_send']['queued'], 1)
        card = self.recipient()
        before = self.mail_rows()
        replay = self.client.post(self.route + '/seat-updates', json=payload, headers=HEADERS)
        self.assertIn(replay.status_code, (200, 201), replay.json)
        self.assertEqual(replay.json['seat_update_send'], first.json['seat_update_send'])
        self.assertEqual(self.mail_rows(), before)
        self.drain()
        replay = self.client.post(self.route + '/seat-updates', json=payload, headers=HEADERS)
        self.assertIn(replay.status_code, (200, 201), replay.json)
        self.assertEqual(replay.json['seat_update_send'], first.json['seat_update_send'])
        self.assertEqual(self.student()['card']['state'], 'pending')
        self.assertEqual(len(self.relay.sent), 10)
        self.move()
        self.assert_update_pending()
        queued = self.send_updates()
        self.assertEqual(queued['seat_update_send']['queued'], 1)
        self.assertNotEqual(self.recipient()['id'], card['id'])
        self.assert_update_pending()
        self.drain()
        self.assertEqual(len(self.relay.sent), 11)
        self.assertEqual(self.student()['card']['state'], 'pending')

    def test_multiple_moves_cancel_obsolete_mail_and_returning_to_original_does_not_reactivate_it(self):
        original = self.recipient()
        original_seat = self.student()['seat_id']
        self.move()
        self.send_updates()
        intermediate = self.recipient()
        self.assertEqual(self.mail_rows(intermediate['id'])[0]['status'], 'pending')
        returned = self.move(seat_id=original_seat)
        self.assert_update_pending(chart=returned)
        self.assertEqual(self.client.get(f"/api/response-cards/cards/{original['id']}/link").status_code, 409)
        self.assertEqual(self.client.get(f"/api/response-cards/cards/{intermediate['id']}/link").status_code, 409)
        self.drain()
        self.assertEqual(len(self.relay.sent), 9)  # The queued intermediate seat never goes out.
        self.assertNotIn(self.mail_rows(intermediate['id'])[0]['status'], ('pending', 'sending', 'accepted'))
        self.assert_update_pending()
        self.send_updates()
        latest = self.recipient()
        self.assertNotIn(latest['id'], (original['id'], intermediate['id']))
        self.drain()
        self.assertEqual(len(self.relay.sent), 10)
        self.assertIn(latest['token'], self.relay.sent[-1]['text_body'])
        self.assertNotIn(intermediate['token'], self.relay.sent[-1]['text_body'])
        self.assertEqual(self.student()['card']['state'], 'pending')

    def test_failed_and_uncertain_delivery_do_not_clear_pending_or_auto_retry(self):
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain):
                self.move()
                self.send_updates()
                current = self.recipient()
                self.relay.fail = DeliveryError('Simulated failed update delivery', uncertain)
                self.drain()
                self.assertEqual(self.mail_rows(current['id'])[0]['status'], 'unconfirmed' if uncertain else 'failed')
                self.assert_update_pending()
                self.assertEqual(len(self.relay.sent), 9)
                self.assertFalse(self.cards.mail.process_one())
                self.assertEqual(len(self.relay.sent), 9)

    def test_explicit_retry_after_definite_failure_reuses_the_current_update_card(self):
        self.move()
        self.send_updates()
        updated = self.recipient()
        self.relay.fail = DeliveryError('The provider rejected this message', False)
        self.drain()
        self.assert_update_pending()
        retry = self.send_updates()
        self.assertEqual(retry['seat_update_send']['queued'], 1)
        self.assertEqual(self.recipient()['id'], updated['id'])
        self.assertEqual([row['status'] for row in self.mail_rows(updated['id'])], ['failed', 'pending'])
        self.assert_update_pending()
        self.drain()
        self.assertEqual(len(self.relay.sent), 10)
        self.assertEqual(self.student()['card']['state'], 'pending')

    def test_publication_retry_reuses_the_prepared_update_without_orphaned_cards(self):
        self.move()
        call = self.cards.remote.call

        def unavailable_publication(method, path, data=None):
            if method == 'PUT' and path.startswith('/faculty-api/cards/'):
                raise ServiceUnavailable('Temporary publication failure')
            return call(method, path, data)

        with patch.object(self.cards.remote, 'call', side_effect=unavailable_publication):
            failed = self.send_updates()
        self.assertEqual(failed['seat_update_send']['queued'], 0)
        self.assertTrue(failed['seat_update_send']['errors'])
        updated = self.recipient()
        self.assert_update_pending()
        self.assertEqual(len(self.mail_rows()), 9)
        retry = self.send_updates()
        self.assertEqual(retry['seat_update_send']['queued'], 1)
        self.assertEqual(self.recipient()['id'], updated['id'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_card_recipients').fetchone()[0], 10)
        self.drain()
        self.assertEqual(len(self.relay.sent), 10)
        self.assertEqual(self.student()['card']['state'], 'pending')

    def test_move_during_print_reservation_rejects_stale_confirmation_before_card_effects(self):
        chart = self.move()
        before_mail = self.mail_rows()
        before_cards = chart['cards']
        with connect(self.path) as db:
            before_issues = [row[0] for row in db.execute('SELECT id FROM response_card_issues ORDER BY id')]
        create_batch = self.seating.documents.create_batch
        moved_versions = []

        def move_after_reservation(*args, **kwargs):
            batch = create_batch(*args, **kwargs)
            if not moved_versions:
                moved_versions.append(self.move()['version'])
            return batch

        with patch.object(self.seating.documents, 'create_batch', side_effect=move_after_reservation), \
                patch.object(self.cards, 'issue', wraps=self.cards.issue) as issue, \
                patch.object(self.cards, 'publish', wraps=self.cards.publish) as publish:
            response = self.client.post(self.route + '/confirm', json={
                'version': chart['version'], 'request_id': str(uuid.uuid4()),
                'mode': 'print', 'version_id': self.version_id,
                'exam_date': '2026-10-01', 'exam_time': '7:00 PM',
                'publish_cards': True, 'email_cards': True}, headers=HEADERS)
            self.assertEqual(response.status_code, 409, response.json)
            issue.assert_not_called()
            publish.assert_not_called()
        self.assertEqual(self.current()['version'], moved_versions[0])
        self.assertEqual(self.current()['cards'], before_cards)
        self.assert_update_pending()
        self.assertEqual(self.mail_rows(), before_mail)
        with connect(self.path) as db:
            self.assertEqual([row[0] for row in db.execute('SELECT id FROM response_card_issues ORDER BY id')], before_issues)
        self.assertEqual(len(self.relay.sent), 9)

    def test_issue_commit_crash_recovers_same_card_after_another_student_moves(self):
        self.move()
        issue = self.cards.issue
        orphaned = []

        def crash_after_commit(payload):
            saved = issue(payload)
            orphaned.append(saved)
            raise RuntimeError('Simulated crash after issue commit, before chart retention')

        with patch.object(self.cards, 'issue', side_effect=crash_after_commit):
            with self.assertRaisesRegex(RuntimeError, 'after issue commit'):
                self.send_updates()
        self.assertEqual(len(orphaned), 1)
        orphan_issue = orphaned[0]
        self.assertEqual(len(orphan_issue['recipients']), 1)
        orphan_card = orphan_issue['recipients'][0]
        self.assertEqual(orphan_card['student_name'], 'Example, Bob')
        self.assertNotIn(orphan_issue['id'], self.current()['cards']['seat']['issue_ids'])
        with connect(self.path) as db:
            self.assertFalse(db.execute('SELECT card_id FROM seating_card_updates WHERE chart_id=? AND enrollment_id=?',
                                        (self.chart['id'], orphan_card['enrollment_id'])).fetchone()[0])
        self.assertEqual(len(self.mail_rows()), 9)
        self.assert_update_pending()

        # A different student's later revision changes the pending audience;
        # Bob's committed issue still belongs to his original seat revision.
        self.move('Student, 4')
        self.assert_update_pending('Student, 4')
        retry = self.send_updates()
        self.assertEqual(retry['seat_update_send']['queued'], 2)
        self.assertEqual(self.recipient()['id'], orphan_card['id'])
        self.assertIn(orphan_issue['id'], retry['cards']['seat']['issue_ids'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM response_card_recipients').fetchone()[0], 11)
            self.assertEqual(db.execute('SELECT card_id FROM seating_card_updates WHERE chart_id=? AND enrollment_id=?',
                                       (self.chart['id'], orphan_card['enrollment_id'])).fetchone()[0], orphan_card['id'])
        self.drain()
        self.assertEqual(len(self.relay.sent), 11)
        self.assertEqual(self.student()['card']['state'], 'pending')
        self.assertEqual(self.student('Student, 4')['card']['state'], 'pending')

    def test_remote_revocation_failure_blocks_distribution_until_retry(self):
        old = self.recipient()
        call = self.cards.remote.call

        def unavailable_control(method, path, data=None):
            if method == 'POST' and path.endswith('/control'):
                raise ServiceUnavailable('Response service is temporarily unavailable')
            return call(method, path, data)

        with patch.object(self.cards.remote, 'call', side_effect=unavailable_control):
            self.move()
            self.assert_update_pending()
            with connect(self.path) as db:
                self.assertEqual(db.execute('SELECT status FROM response_card_recipients WHERE id=?', (old['id'],)).fetchone()[0], 'revoked')
            refused = self.send_updates()
            self.assertEqual(refused['seat_update_send']['queued'], 0)
            self.assertTrue(refused['seat_update_send']['errors'])
            self.assertFalse(any(row['status'] == 'pending' for row in self.mail_rows()))
            self.drain()
            self.assertEqual(len(self.relay.sent), 9)
        accepted = self.send_updates()
        self.assertEqual(accepted['seat_update_send']['queued'], 1)
        self.drain()
        self.assertEqual(len(self.relay.sent), 10)
        self.assertEqual(self.student()['card']['state'], 'pending')

    def test_reconfirmation_and_spreadsheet_export_keep_update_pending(self):
        old = self.recipient()
        self.move()
        self.confirm(email_cards=False)
        self.assert_update_pending()
        updated = self.recipient()
        self.assert_update_schema(updated, self.student()['seat'])
        preview = self.client.get(self.route + '/mail-merge').json
        self.assertEqual(preview['eligible_count'], 1)
        response = self.client.get(self.route + '/mail-merge/download', query_string={
            'audience': preview['audience'], 'fingerprint': preview['fingerprint']})
        self.assertEqual(response.status_code, 200, response.json)
        workbook = load_workbook(io.BytesIO(response.data))
        values = json.dumps(list(workbook['Recipients'].values))
        self.assertIn(updated['token'], values)
        self.assertNotIn(old['token'], values)
        self.assertEqual(len(self.mail_rows()), 9)
        self.assert_update_pending()
        self.confirm(email_cards=True)
        self.assertEqual(self.recipient()['id'], updated['id'])
        self.assert_update_pending()
        self.drain()
        self.assertEqual(len(self.relay.sent), 10)
        self.assertEqual(self.student()['card']['state'], 'pending')

    def test_optional_gmail_update_keeps_resend_default_and_waits_for_acceptance(self):
        self.move()
        sent = self.send_updates(mail_provider='gmail')
        self.assertEqual(sent['seat_update_send']['queued'], 1)
        updated = self.recipient()
        row = self.mail_rows(updated['id'])[0]
        self.assertEqual((row['provider'], row['gmail_sender']), ('gmail', self.gmail.email))
        self.assert_update_pending()
        self.assertEqual(self.cards.mail.provider, 'resend')
        self.drain()
        self.assertEqual(len(self.gmail.sent), 1)
        self.assertEqual(len(self.relay.sent), 9)
        self.assertIn(updated['token'], self.gmail.sent[0]['text'])
        self.assertEqual(self.student()['card']['state'], 'pending')
        self.assertEqual(self.cards.mail.provider, 'resend')

    def test_automatic_reseating_only_marks_the_student_whose_seat_changed(self):
        old = self.recipient()
        bob = self.student()
        room = self.client.get(f"/api/rooms/{self.chart['room_id']}").json
        response = self.client.post(f"/api/rooms/{room['id']}/seats", json={
            'version': room['version'], 'seat_ids': [bob['seat_id']], 'blocked': True}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        chart = self.current()
        updated = self.client.post(self.route + '/update', json={'version': chart['version']}, headers=HEADERS)
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertNotEqual(self.student(chart=updated.json)['seat_id'], bob['seat_id'])
        self.assertEqual([student['name'] for student in updated.json['students']
                          if (student.get('card') or {}).get('state') == 'update_pending'], ['Example, Bob'])
        self.assertEqual(self.client.get(f"/api/response-cards/cards/{old['id']}/link").status_code, 409)
        self.assertEqual(self.send_updates()['seat_update_send']['queued'], 1)
        self.drain()
        self.assertEqual(len(self.relay.sent), 10)

    def test_invalid_provider_and_stale_version_do_not_prepare_or_queue_updates(self):
        self.move()
        before = self.mail_rows()
        calls = len(self.cards.remote.calls)
        old = self.recipient()
        self.gmail.connected = False
        for extra, status in (({'mail_provider': 'gmail'}, 503), ({'mail_provider': 'typo'}, 400),
                              ({'version': 0}, 409)):
            with self.subTest(extra=extra):
                response = self.client.post(self.route + '/seat-updates', json={
                    'version': self.current()['version'], 'request_id': str(uuid.uuid4()),
                    'mail_provider': 'default', **extra}, headers=HEADERS)
                self.assertEqual(response.status_code, status, response.json)
                self.assertEqual(self.mail_rows(), before)
                self.assertEqual(self.recipient()['id'], old['id'])
                self.assertEqual(len(self.cards.remote.calls), calls)
                self.assert_update_pending()

    def test_unconfirmed_moves_and_noop_confirmed_move_do_not_require_updates(self):
        old = self.recipient()
        current = self.student()
        self.move(seat_id=current['seat_id'])
        self.assertNotEqual(self.student()['card']['state'], 'update_pending')
        self.assertEqual(self.recipient()['id'], old['id'])
        self.assertFalse(self.cards.remote.controls)
        other = self.create(assignment_id=self.quiz['id']).json
        student = next(item for item in other['students'] if item['seat_id'])
        free = next(seat for seat in other['room']['seats'] if not seat['student'] and not seat['broken'] and not seat['blocked'])
        moved = self.client.post(f"/api/seating/{other['id']}/move", json={
            'version': other['version'], 'enrollment_id': student['enrollment_id'], 'seat_id': free['id']}, headers=HEADERS)
        self.assertEqual(moved.status_code, 200, moved.json)
        self.assertFalse(any((item.get('card') or {}).get('state') == 'update_pending' for item in moved.json['students']))
        self.assertEqual(len(self.mail_rows()), 9)


if __name__ == '__main__':
    unittest.main()
