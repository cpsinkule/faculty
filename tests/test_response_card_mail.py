"""Response card email: invitations after publishing, confirmations after submissions, and delivery records."""
import base64
import email
import email.policy
import http.client
from email.message import Message
import io
import json
import os
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.parse
import urllib.error
import uuid

from response_cards.gmail import GmailConnection, REVOKE_URL, SCOPES, SEND_URL, TOKEN_URL, USERINFO_URL
from response_cards.mail import CardMailer, DeliveryError, MailConfig, RelayRateLimit, provider_pauses, valid_address
from rosters import connect
import test_rosters as roster_tests
from test_response_cards import FakeRemote
from test_rooms import chart_bytes
from test_seating import HEADERS


class FakeRelay:
    """Records the envelopes the signed relay would receive; never touches the network."""
    def __init__(self):
        self.sent, self.fail = [], None

    def __call__(self, job):
        if self.fail is not None:
            error, self.fail = self.fail, None
            raise error
        self.sent.append(dict(job))
        return {"mail_id": job["id"], "status": "accepted", "message_id": f"provider-{len(self.sent)}"}


class ResponseCardMailTests(unittest.TestCase):
    managed = False
    add = roster_tests.RosterTests.add
    route = roster_tests.RosterTests.route
    upload = roster_tests.RosterTests.upload
    change = roster_tests.RosterTests.change

    def setUp(self):
        roster_tests.RosterTests.setUp(self)
        self.cards = self.app.extensions["response_cards"]
        self.remote = FakeRemote()
        self.cards.remote = self.remote
        self.relay = FakeRelay()
        self.cards.mail.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4)
        self.cards.mail.transport = self.relay
        self.course, self.group = self.add()
        self.upload(self.group)
        context = self.cards.context()
        self.term = next(t for t in context["terms"] if t["code"] == "MATH 140")
        self.section = next(s for s in context["sections"] if s["term_id"] == self.term["id"] and s["number"] == 507)
        self.assignment = self.app.extensions["documents"].create_assignment(self.term["definition_id"], "test-1", "Test 1")
        template = self.cards.save_template({"name": "Seat", "definition_id": self.term["definition_id"], "schema": {
            "title": "{{exam.name}} · your seat", "description": "Your {{exam.name}} for {{course.code}} is in room X.", "fields": [
                {"id": "room", "type": "short_text", "label": "Please confirm your room:", "required": True,
                 "verification": {"expected": "HECC 105", "mode": "compact", "on_mismatch": "ALLOW_AND_FLAG"}},
                {"id": "pencil", "type": "acknowledgment", "label": "I will bring a pencil.", "required": True},
                {"id": "uin", "type": "short_text", "label": "Type your UIN to confirm that you have read and understood all of the above.", "required": True,
                 "verification": {"expected": "{{student.uin | uin_digest}}", "mode": "uin-digest", "on_mismatch": "BLOCK_SUBMISSION"}}]}})
        version = self.cards.freeze(template["id"], template["version"])
        self.issue = self.cards.issue({"request_id": str(uuid.uuid4()), "version_id": version["id"], "term_id": self.term["id"],
                                       "section_ids": [self.section["id"]], "assignment_id": self.assignment["id"]})

    def drain(self):
        while self.cards.mail.process_one():
            pass

    def mail_rows(self):
        with connect(self.cards.path) as db:
            return [dict(row) for row in db.execute("SELECT * FROM response_card_mail ORDER BY created_at,id")]

    def assert_generic_message(self, message):
        self.assertTrue(message["text_body"].startswith("Hello,\n\n"))
        self.assertTrue(message["html_body"].startswith("<p>Hello,</p>"))
        for field in ("subject", "text_body", "html_body"):
            self.assertNotRegex(message[field], r"\b(?:Alice|Bob|Example)\b")
        self.assertEqual(email.utils.parseaddr(message["recipient"]), ("", message["recipient"]))

    def test_publishing_then_emailing_each_card_link_once(self):
        self.assertFalse(self.cards.mail.process_one())
        self.cards.publish(self.issue["id"])
        self.assertEqual(self.cards.mail.queue_invitations(self.issue["id"]), {"queued": 2, "no_email": 0})
        self.assertEqual(self.cards.mail.queue_invitations(self.issue["id"]), {"queued": 0, "no_email": 0})
        recipients = self.cards.get_issue(self.issue["id"])["recipients"]
        self.assertEqual({r["delivery_status"] for r in recipients}, {"queued"})
        self.drain()
        self.assertEqual(len(self.relay.sent), 2)
        alice = next(r for r in recipients if r["student_name"] == "Example, Alice")
        message = next(m for m in self.relay.sent if m["recipient"] == "alice@example.test")
        self.assertEqual(message["subject"], "IMPORTANT: Information about your upcoming Test 1 for MATH 140")
        by_email = {r["email"]: r for r in recipients}
        self.assertEqual({m["recipient"] for m in self.relay.sent}, {"alice@example.test", "bob@example.test"})
        for sent in self.relay.sent:
            self.assert_generic_message(sent)
        for field in ("subject", "text_body", "html_body"):
            self.assertEqual(len({m[field].replace(by_email[m["recipient"]]["link"], "<personal-link>")
                                  for m in self.relay.sent}), 1)
        self.assertIn("your upcoming Test 1 for MATH 140 (Calculus)", message["text_body"])
        self.assertIn(alice["link"], message["text_body"])
        self.assertIn(f'<a href="{alice["link"]}">', message["html_body"])
        self.assertIn("Professor Sinkule", message["text_body"])
        detail = self.cards.get_issue(self.issue["id"])
        self.assertEqual(detail["counts"]["emailed"], 2)
        self.assertEqual({r["delivery_status"] for r in detail["recipients"]}, {"sent"})
        self.assertEqual([(m["kind"], m["status"]) for m in detail["recipients"][0]["mail"]], [("invitation", "accepted")])
        self.assertTrue(all(row["status"] == "accepted" and row["provider_receipt_json"] for row in self.mail_rows()))
        self.assertEqual(self.client.post(f"/api/response-cards/issues/{self.issue['id']}/send", json={}, headers=HEADERS).json["queued"], {"queued": 0, "no_email": 0})
        self.cards.mail.config = MailConfig()
        self.assertEqual(self.client.post(f"/api/response-cards/issues/{self.issue['id']}/send", json={}, headers=HEADERS).status_code, 503)
        self.assertFalse(self.cards.mail.process_one())

    def test_pending_seating_cards_get_reminders_three_two_and_one_day_before_the_exam(self):
        """RESPONSE-CARDS "REMINDERS BEFORE THE EXAM" (requirement, 2026-10-15): 3, 2 and 1 days before, from 9:00 am Central."""
        from datetime import datetime, timedelta, timezone
        from scheduling.clock import CAMPUS_ZONE
        assignment = self.client.post('/api/documents/assignments', json={'definition_id': self.term['definition_id'], 'key': 'exam-2', 'title': 'Exam 2'}, headers=HEADERS).json
        room = self.client.post('/api/rooms', data={'name': 'Exam Hall', 'file': (io.BytesIO(chart_bytes({'A1': 'A', 'B1': 1, 'C1': 2, 'A2': 'B', 'B2': 1, 'C2': 2})), 'seats.xlsx')}, headers=HEADERS)
        chart = self.client.post('/api/seating', json={'assignment_id': assignment['id'], 'section_ids': [self.section['id']], 'room_id': room.json['id']}, headers=HEADERS).json
        exam = datetime.now(CAMPUS_ZONE).date() + timedelta(days=6)
        with connect(self.cards.path) as db:
            db.execute("UPDATE seating_charts SET exam_date=? WHERE id=?", (exam.isoformat(), chart['id']))
        version = self.cards.freeze(*(lambda t: (t['id'], t['version']))(self.cards.templates(self.term['definition_id'])[0]))
        issue = self.cards.issue({'request_id': str(uuid.uuid4()), 'version_id': version['id'], 'term_id': self.term['id'],
                                  'section_ids': [self.section['id']], 'seating_chart_id': chart['id']})
        self.cards.publish(issue['id'])
        self.cards.publish(self.issue['id'])  # a card not from a chart: never reminded
        self.cards.mail.gmail = mock.Mock(connected=True, email="original@example.test")
        self.cards.mail.queue_invitations(issue['id'])
        self.cards.mail.queue_invitations(self.issue['id'])
        self.drain()
        alice = next(c for c in issue['recipients'] if c['student_name'] == 'Example, Alice')
        bob = next(c for c in issue['recipients'] if c['student_name'] != 'Example, Alice')

        def at(days_before, hour, minute=0):
            day = exam - timedelta(days=days_before)
            return datetime(day.year, day.month, day.day, hour, minute, tzinfo=CAMPUS_ZONE).astimezone(timezone.utc)

        def reminders():
            return [r for r in self.mail_rows() if r['kind'] == 'reminder']

        self.assertEqual(self.cards.mail.queue_reminders(at(4, 12))['queued'], 0)  # four days before: nothing
        self.assertEqual(self.cards.mail.queue_reminders(at(3, 8, 59))['queued'], 0)  # before 9:00 am
        self.cards.mail.gmail.connected = False
        self.assertEqual(self.cards.mail.queue_reminders(at(3, 9))['queued'], 0)  # never fall back to the relay
        self.cards.mail.gmail.connected = True
        self.cards.mail.gmail.email = "another@example.test"
        self.assertEqual(self.cards.mail.queue_reminders(at(3, 9))['queued'], 0)  # never change the invitation's account
        self.cards.mail.gmail.email = "original@example.test"
        self.assertEqual(self.cards.mail.queue_reminders(at(3, 9)), {'queued': 2, 'no_email': 0})
        self.assertEqual({(row['provider'], row['gmail_sender']) for row in reminders()}, {('gmail', 'original@example.test')})
        self.assertEqual(self.cards.mail.queue_reminders(at(3, 15))['queued'], 0)  # once per day
        sent = len(self.relay.sent)
        self.drain()
        message = next(m for m in self.relay.sent[sent:] if m['recipient'] == 'alice@example.test')
        self.assertEqual(message['subject'], 'REMINDER: Complete your Exam 2 response card for MATH 140')
        self.assertIn(f"is in 3 days, on {exam.strftime('%A, %B')} {exam.day}.", message['text_body'])
        self.assertIn("/r/", message['text_body'])
        self.assert_generic_message(message)
        # Alice answers: Bob alone is reminded two days before; a reminder queued before an answer is cancelled, never sent.
        self.remote.event(alice['id'], 'verified', submission={'id': 'submission-a', 'request_id': 'response-a', 'status': 'verified',
                                                               'submitted_at': '2026-09-15T14:05:00.000Z', 'answers': {}})
        self.cards.sync()
        self.assertEqual(self.cards.mail.queue_reminders(at(2, 9, 30)), {'queued': 1, 'no_email': 0})
        sent = len(self.relay.sent)
        self.drain()
        self.assertEqual([m['recipient'] for m in self.relay.sent[sent:] if m['subject'].startswith('REMINDER')], ['bob@example.test'])
        self.assertEqual(self.cards.mail.queue_reminders(at(1, 10))['queued'], 1)
        self.remote.event(bob['id'], 'verified', submission={'id': 'submission-b', 'request_id': 'response-b', 'status': 'verified',
                                                             'submitted_at': '2026-09-15T14:05:00.000Z', 'answers': {}})
        self.cards.sync()
        sent = len(self.relay.sent)
        self.drain()
        self.assertEqual([m for m in self.relay.sent[sent:] if m['subject'].startswith('REMINDER')], [])
        self.assertEqual([(r['submission_id'], r['status']) for r in reminders() if r['card_id'] == bob['id']], [('3', 'accepted'), ('2', 'accepted'), ('1', 'cancelled')])
        self.assertEqual({r['card_id'] for r in reminders()}, {alice['id'], bob['id']})
        self.assertEqual({c['delivery_status'] for c in self.cards.get_issue(issue['id'])['recipients']}, {'sent'})
        self.assertEqual(self.cards.mail.queue_reminders(at(0, 12))['queued'], 0)  # exam day: no reminder

    def test_resending_one_card_by_hand_queues_another_copy_only_while_it_is_pending(self):
        by_name = lambda: {r["student_name"]: r for r in self.cards.get_issue(self.issue["id"])["recipients"]}
        resend = lambda card: self.client.post(f"/api/response-cards/cards/{card['id']}/resend", json={}, headers=HEADERS)
        alice, bob = by_name()["Example, Alice"], by_name()["Example, Bob"]
        self.assertEqual(self.client.post("/api/response-cards/cards/missing/resend", json={}, headers=HEADERS).status_code, 404)
        self.assertEqual((resend(alice).status_code, resend(alice).json["error"]), (409, "Publish this card before emailing it."))
        self.cards.publish(self.issue["id"])
        # A published card nobody emailed yet gets its first copy this way, plainly worded.
        self.assertEqual(resend(alice).status_code, 200)
        self.assertEqual((resend(alice).status_code, resend(alice).json["error"]), (409, "An email for this card is already waiting to send."))
        self.assertEqual(by_name()["Example, Alice"]["delivery_status"], "queued")
        self.assertEqual(self.cards.mail.queue_invitations(self.issue["id"]), {"queued": 1, "no_email": 0})  # Bob only; Alice is never doubled.
        self.drain()
        self.assertEqual(len(self.relay.sent), 2)
        self.assertNotIn("second copy", next(m for m in self.relay.sent if m["recipient"] == "alice@example.test")["text_body"])
        # A sent card can be re-sent; the copy says so, keeps the subject, and the record shows both.
        response = resend(alice)
        self.assertEqual((response.status_code, response.json), (200, {"card_id": alice["id"], "delivery_status": "queued", "copies": 2}))
        self.assertEqual(resend(alice).status_code, 409)
        self.assertEqual(self.cards.mail.queue_invitations(self.issue["id"]), {"queued": 0, "no_email": 0})
        self.drain()
        copies = [m for m in self.relay.sent if m["recipient"] == "alice@example.test"]
        self.assertEqual(len(copies), 2)
        self.assertEqual(copies[1]["subject"], copies[0]["subject"])
        for message in copies:
            self.assert_generic_message(message)
        self.assertIn("This is a second copy of an earlier email, sent again in case the first did not reach you; the link is the same.", copies[1]["text_body"])
        self.assertIn(by_name()["Example, Alice"]["link"], copies[1]["text_body"])
        self.assertEqual(by_name()["Example, Alice"]["delivery_status"], "sent")
        self.assertEqual([(m["kind"], m["status"]) for m in by_name()["Example, Alice"]["mail"]], [("invitation", "accepted")] * 2)
        # A failed delivery is retried only by hand.
        self.assertEqual(resend(bob).status_code, 200)
        self.relay.fail = DeliveryError("The email service rejected this message.", False)
        self.drain()
        self.assertEqual(by_name()["Example, Bob"]["delivery_status"], "failed")
        self.assertEqual(resend(bob).status_code, 200)
        self.drain()
        self.assertEqual((by_name()["Example, Bob"]["delivery_status"], len([m for m in self.relay.sent if m["recipient"] == "bob@example.test"])), ("sent", 2))
        # Once the student responds, or the card is revoked, or mail is off, nothing more can be sent.
        self.remote.event(alice["id"], "verified", submission={
            "id": "s1", "request_id": "r1", "status": "verified", "submitted_at": "2026-09-15T14:05:00.000Z",
            "answers": {"room": "HECC 105", "pencil": True, "uin": "[UIN confirmed]"},
            "verification": {"room": {"matched": True, "on_mismatch": "ALLOW_AND_FLAG"}, "uin": {"matched": True, "on_mismatch": "BLOCK_SUBMISSION"}}})
        self.cards.sync()
        self.assertEqual((resend(alice).status_code, resend(alice).json["error"]), (409, "This student has already responded to the card."))
        self.cards.control(bob["id"], {"action": "revoke", "request_id": "revoke-bob"})
        self.assertEqual((resend(bob).status_code, resend(bob).json["error"]), (409, "This card was revoked; re-confirm the seating chart to issue a new one."))
        self.cards.mail.config = MailConfig()
        self.assertEqual(resend(alice).status_code, 503)
        self.assertEqual(len(self.relay.sent), 4)  # Two to Alice and two to Bob; the confirmation waits for a provider.

    def test_replacement_invitation_keeps_update_notice_without_student_name(self):
        self.cards.publish(self.issue["id"])
        alice = next(r for r in self.cards.get_issue(self.issue["id"])["recipients"]
                     if r["email"] == "alice@example.test")
        self.cards.mail.queue_invitations(self.issue["id"], replacements=[alice["enrollment_id"]])
        self.drain()
        sent = {m["recipient"]: m for m in self.relay.sent}
        for message in sent.values():
            self.assert_generic_message(message)
        replacement = sent["alice@example.test"]
        self.assertEqual(replacement["subject"], "UPDATED: Information about your upcoming Test 1 for MATH 140")
        self.assertIn("this new card replaces it, and the earlier link no longer works.", replacement["text_body"])
        self.assertNotIn("second copy", replacement["text_body"])
        self.assertNotIn("this new card replaces it", sent["bob@example.test"]["text_body"])

    def test_a_pending_cards_link_can_be_fetched_to_hand_over_until_it_is_answered(self):
        by_name = lambda: {r["student_name"]: r for r in self.cards.get_issue(self.issue["id"])["recipients"]}
        fetch = lambda card: self.client.get(f"/api/response-cards/cards/{card['id']}/link", headers=HEADERS)
        alice = by_name()["Example, Alice"]
        self.assertEqual(self.client.get("/api/response-cards/cards/missing/link", headers=HEADERS).status_code, 404)
        self.assertEqual((fetch(alice).status_code, fetch(alice).json["error"]), (409, "This card is not published yet."))
        self.cards.publish(self.issue["id"])
        alice = by_name()["Example, Alice"]
        self.assertTrue(alice["link"].startswith("https://respond.example.test/r/"))
        self.assertEqual((fetch(alice).status_code, fetch(alice).json), (200, {"card_id": alice["id"], "link": alice["link"]}))
        self.remote.event(alice["id"], "verified", submission={
            "id": "s1", "request_id": "r1", "status": "verified", "submitted_at": "2026-09-15T14:05:00.000Z",
            "answers": {"room": "HECC 105", "pencil": True, "uin": "[UIN confirmed]"},
            "verification": {"room": {"matched": True, "on_mismatch": "ALLOW_AND_FLAG"}, "uin": {"matched": True, "on_mismatch": "BLOCK_SUBMISSION"}}})
        self.cards.sync()
        self.assertEqual((fetch(alice).status_code, fetch(alice).json["error"]), (409, "This student has already responded to the card."))
        bob = by_name()["Example, Bob"]
        self.cards.control(bob["id"], {"action": "revoke", "request_id": "revoke-bob-link"})
        self.assertEqual(fetch(bob).status_code, 409)

    def test_failed_and_unconfirmed_deliveries_are_recorded_and_never_retried(self):
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.relay.fail = DeliveryError("The email service rejected this message.", False)
        self.assertTrue(self.cards.mail.process_one())
        self.relay.fail = DeliveryError("Delivery could not be confirmed.", True)
        self.assertTrue(self.cards.mail.process_one())
        self.assertFalse(self.cards.mail.process_one())
        rows = self.mail_rows()
        self.assertEqual([(r["status"], r["attempts"]) for r in rows], [("failed", 1), ("unconfirmed", 1)])
        self.assertEqual(rows[0]["error"], "The email service rejected this message.")
        self.assertEqual(sorted(r["delivery_status"] for r in self.cards.get_issue(self.issue["id"])["recipients"]), ["failed", "unconfirmed"])
        self.assertEqual(self.relay.sent, [])
        # An interrupted send recovers from its receipt, or becomes unconfirmed; nothing is resent.
        with connect(self.cards.path) as db:
            db.execute("UPDATE response_card_mail SET status='sending',provider_receipt_json=NULL WHERE id=?", (rows[0]["id"],))
            db.execute("UPDATE response_card_mail SET status='sending',provider_receipt_json='{}' WHERE id=?", (rows[1]["id"],))
        self.assertTrue(self.cards.mail.process_one())
        self.assertEqual([r["status"] for r in self.mail_rows()], ["unconfirmed", "accepted"])
        self.assertEqual(self.relay.sent, [])

    def test_missing_relay_route_keeps_mail_queued_and_pauses_sending(self):
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.relay.fail = DeliveryError("The mail relay has no message route yet.", unavailable=True)
        self.assertFalse(self.cards.mail.process_one())
        rows = self.mail_rows()
        self.assertEqual([(r["status"], r["attempts"], r["error"]) for r in rows][0], ("pending", 1, "The mail relay has no message route yet."))
        self.assertEqual({r["delivery_status"] for r in self.cards.get_issue(self.issue["id"])["recipients"]}, {"queued"})
        self.assertFalse(self.cards.mail.process_one())  # Paused; no relay call.
        self.assertEqual(self.relay.sent, [])
        with mock.patch("response_cards.mail.time.time", return_value=time.time() + 301):
            self.drain()
        self.assertEqual(len(self.relay.sent), 2)
        self.assertEqual({r["status"] for r in self.mail_rows()}, {"accepted"})

    def test_rate_limit_pause_survives_restart_and_new_pending_mail(self):
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.relay.fail = RelayRateLimit(60)
        with mock.patch("response_cards.mail.time.time", return_value=1000.2):
            self.assertFalse(self.cards.mail.process_one())
        rows = self.mail_rows()
        rejected = next(row for row in rows if row["attempts"])
        self.assertEqual((rejected["status"], rejected["attempts"], rejected["not_before"]), ("pending", 1, 1061))
        self.assertFalse(rejected["provider_receipt_json"])
        self.assertEqual({r["delivery_status"] for r in self.cards.get_issue(self.issue["id"])["recipients"]}, {"queued"})
        # A new message with no personal delay must not bypass the global hold.
        with self.cards.rosters.lock(), connect(self.cards.path) as db:
            db.execute("BEGIN IMMEDIATE")
            card = dict(db.execute("SELECT * FROM response_card_recipients WHERE id=?", (rejected["card_id"],)).fetchone())
            self.cards.mail._queue(db, card, "confirmation", "rate-limit-check", {"subject": "Confirmation", "text": "Received", "html": "<p>Received</p>"}, "2000-01-01T00:00:00Z")
        restarted = CardMailer(self.cards, self.cards.mail.config, gmail=self.cards.mail.gmail)
        restarted.transport = self.relay
        for instant in (1001, 1060.99):
            with mock.patch("response_cards.mail.time.time", return_value=instant):
                self.assertFalse(restarted.process_one())
        self.assertEqual(self.relay.sent, [])
        self.assertEqual(sum(row["attempts"] for row in self.mail_rows()), 1)
        with mock.patch("response_cards.mail.time.time", return_value=1061):
            while restarted.process_one():
                pass
        rows = self.mail_rows()
        self.assertEqual(len(self.relay.sent), 3)
        self.assertEqual({r["status"] for r in rows}, {"accepted"})
        self.assertTrue(all(r["provider_receipt_json"] and r["not_before"] == 0 for r in rows))
        self.assertEqual(next(r for r in rows if r["id"] == rejected["id"])["attempts"], 2)
        with mock.patch("response_cards.mail.time.time", return_value=2000):
            self.assertFalse(restarted.process_one())
        self.assertEqual(len(self.relay.sent), 3)

    def test_mail_deadline_migration_preserves_existing_queue_and_runs_once(self):
        from response_cards.service import ResponseCardService
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        with self.cards.rosters.lock(), connect(self.cards.path) as db:
            db.execute("ALTER TABLE response_card_mail DROP COLUMN not_before")
        legacy = self.mail_rows()
        for _ in range(2):
            ResponseCardService(self.cards.rosters, self.cards.seating, self.remote)
        migrated = self.mail_rows()
        self.assertEqual([{k: v for k, v in row.items() if k != "not_before"} for row in migrated], legacy)
        self.assertEqual({row["not_before"] for row in migrated}, {0})

    def test_the_card_index_lands_on_the_current_mail_table_after_a_legacy_copy(self):
        from response_cards.service import ResponseCardService
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        before = self.mail_rows()
        # A catalog from before manual resends and the ledger: one invitation per card (a table-level UNIQUE), no card index.
        with self.cards.rosters.lock(), connect(self.cards.path) as db:
            db.execute("DELETE FROM schema_migrations WHERE namespace='response_cards'")
            sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='response_card_mail'").fetchone()[0]
            db.execute("ALTER TABLE response_card_mail RENAME TO earlier_mail")  # its indexes go with it
            db.execute(sql[:sql.rindex(")")] + ", UNIQUE(card_id,kind,submission_id))")
            db.execute("INSERT INTO response_card_mail SELECT * FROM earlier_mail")
            db.execute("DROP TABLE earlier_mail")
            db.execute("CREATE INDEX response_card_mail_queue ON response_card_mail(status,created_at)")
        ResponseCardService(self.cards.rosters, self.cards.seating, self.remote)
        self.assertEqual(self.mail_rows(), before)
        with connect(self.cards.path) as db:
            self.assertEqual({row["name"]: row["tbl_name"] for row in db.execute("SELECT name,tbl_name FROM sqlite_master WHERE type='index' AND name LIKE 'response_card_mail%'")},
                             {name: "response_card_mail" for name in ("response_card_mail_queue", "response_card_mail_confirmation", "response_card_mail_card",
                                                                         "response_card_mail_reminder")})
            self.assertFalse(any(row["origin"] == "u" for row in db.execute("PRAGMA index_list(response_card_mail)")))
            self.assertEqual([tuple(row) for row in db.execute("SELECT version,adoption FROM schema_migrations WHERE namespace='response_cards' ORDER BY version")],
                             [("001_schema", "adopted"), ("002_mail_card_index", "applied"), ("003_reminders", "applied")])

    def test_mail_provider_migration_pins_pending_mail_once_without_rewriting_receipts(self):
        from response_cards.service import ResponseCardService
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.assertTrue(self.cards.mail.process_one())
        with self.cards.rosters.lock(), connect(self.cards.path) as db:
            db.execute("ALTER TABLE response_card_mail DROP COLUMN provider")
            db.execute("ALTER TABLE response_card_mail DROP COLUMN gmail_sender")
        legacy = self.mail_rows()
        with mock.patch.dict(os.environ, {"FACULTY_PRINT_EMAIL_URL": "https://relay.example.test/send",
                                         "FACULTY_PRINT_EMAIL_SECRET": "s" * 40, "FACULTY_OUTBOUND_EMAIL_PROVIDER": "resend"}):
            ResponseCardService(self.cards.rosters, self.cards.seating, self.remote)
        migrated = self.mail_rows()
        self.assertEqual([{k: v for k, v in row.items() if k not in ("provider", "gmail_sender")} for row in migrated], legacy)
        self.assertEqual({row["status"]: row["provider"] for row in migrated}, {"accepted": "", "pending": "resend"})
        with mock.patch.dict(os.environ, {"FACULTY_OUTBOUND_EMAIL_PROVIDER": "relay"}):
            ResponseCardService(self.cards.rosters, self.cards.seating, self.remote)
        self.assertEqual(self.mail_rows(), migrated)

    def test_sync_queues_a_confirmation_listing_what_the_student_entered(self):
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.drain()
        cards = {r["student_name"]: r for r in self.cards.get_issue(self.issue["id"])["recipients"]}
        self.remote.event(cards["Example, Alice"]["id"], "verified", submission={
            "id": "s1", "request_id": "r1", "status": "verified", "submitted_at": "2026-09-15T14:05:00.000Z",
            "answers": {"room": "hecc 105", "pencil": True, "uin": "[UIN confirmed]"},
            "verification": {"room": {"matched": True, "on_mismatch": "ALLOW_AND_FLAG"}, "uin": {"matched": True, "on_mismatch": "BLOCK_SUBMISSION"}}})
        self.remote.event(cards["Example, Bob"]["id"], "needs_review", submission={
            "id": "s2", "request_id": "r2", "status": "needs_review", "submitted_at": "2026-09-15T15:00:00.000Z",
            "answers": {"room": "HECC 106", "pencil": True, "uin": "[UIN confirmed]"},
            "verification": {"room": {"matched": False, "on_mismatch": "ALLOW_AND_FLAG"}, "uin": {"matched": True, "on_mismatch": "BLOCK_SUBMISSION"}}})
        self.assertEqual(self.cards.sync()["confirmations_queued"], 2)
        self.assertNotIn("confirmations_queued", self.cards.sync())
        self.drain()
        confirmations = [m for m in self.relay.sent if m["subject"].startswith("Confirmed")]
        self.assertEqual(len(confirmations), 2)
        self.assertEqual({m["recipient"] for m in confirmations}, {"alice@example.test", "bob@example.test"})
        for message in confirmations:
            self.assert_generic_message(message)
        alice = next(m for m in confirmations if m["recipient"] == "alice@example.test")
        self.assertEqual(alice["subject"], "Confirmed: your responses for Test 1 (MATH 140)")
        self.assertIn("submitted on September 15, 2026 at 9:05 AM CDT for Test 1 (MATH 140).", alice["text_body"])
        self.assertIn("The card stated:\n\nYour Test 1 for MATH 140 is in room X.", alice["text_body"])
        self.assertIn("- Please confirm your room: hecc 105\n- I will bring a pencil: Yes\n- Type your UIN to confirm that you have read and understood all of the above: confirmed", alice["text_body"])
        self.assertNotIn("differ", alice["text_body"])
        bob = next(m for m in confirmations if m["recipient"] == "bob@example.test")
        self.assertIn("- Please confirm your room: HECC 106 (differs from our records)", bob["text_body"])
        self.assertIn("Professor Sinkule will review them", bob["text_body"])
        self.assertNotIn("HECC 105", bob["text_body"])
        self.assertTrue(all("123456789" not in m["text_body"] and "001234567" not in m["text_body"] for m in confirmations))
        self.assertEqual(sorted(r["submission_id"] for r in self.mail_rows()), ["", "", "s1", "s2"])
        self.assertEqual(self.cards.mail.queue_confirmations(), {"queued": 0})
        self.assertEqual(len(self.relay.sent), 4)

    def test_every_composer_anchors_the_escaped_link_once(self):
        """Invitations, booking invitations and reminders turn the card link into one anchor and change nothing else."""
        import html
        from response_cards.mail import paragraphs
        link = "https://cards.example.test/r/abc?x=1&y=<2>"
        context = {"code": "MATH 140", "course_name": "Calculus", "assignment": "Test 1", "link": link, "schema": {},
                   "issued_at": "2026-01-01T00:00:00Z"}
        card = {"id": "c1", "issue_id": self.issue["id"], "section_id": "s", "enrollment_id": "e"}
        shown = html.escape(link)
        anchor = f'<a href="{html.escape(link, quote=True)}">{shown}</a>'
        self.assertEqual(anchor, '<a href="https://cards.example.test/r/abc?x=1&amp;y=&lt;2&gt;">https://cards.example.test/r/abc?x=1&amp;y=&lt;2&gt;</a>')
        with connect(self.cards.path) as db:
            for name, extra in (("invitation", {}), ("booking invitation", {"makeup_offer_id": 5, "exam_event_id": "e"}), ("reminder", None)):
                with mock.patch.object(CardMailer, "_context", return_value={**context, "context": extra or {}}):
                    if extra is None:
                        message = self.cards.mail.reminder(db, card, 3, "2026-11-10")
                    else:
                        message = self.cards.mail.invitation(db, card)
                self.assertEqual(message["html"].count(anchor), 1, name)
                self.assertEqual(message["html"].replace(anchor, shown), paragraphs(message["text"]), name)

    def test_helpers_and_configuration(self):
        from response_cards.mail import twelve_hour
        from response_cards.service import long_date
        self.assertEqual([twelve_hour("19:05"), twelve_hour("00:30"), twelve_hour("12:00:00"), twelve_hour("7:00 PM"), twelve_hour(None)], ["7:05 PM", "12:30 AM", "12:00 PM", "7:00 PM", None])
        self.assertEqual([long_date("2026-09-25"), long_date("Friday"), long_date("")], ["Friday, September 25, 2026", "Friday", ""])
        self.assertTrue(valid_address("a.b@example.test"))
        for bad in (" a@b.c ", "", "a..b@example.test", "one@example.test,two@example.test", None):
            self.assertFalse(valid_address(bad), bad)
        self.assertFalse(MailConfig("https://relay.example.test/send", "x" * 40).enabled)
        self.assertFalse(MailConfig("https://relay.example.test/message", "short").enabled)
        self.assertTrue(MailConfig("https://relay.example.test/message", "x" * 40).enabled)
        with mock.patch.dict(os.environ, {"FACULTY_PRINT_EMAIL_URL": "https://relay.example.test/send", "FACULTY_PRINT_EMAIL_SECRET": "s" * 40, "FACULTY_MAIL_REPLY_TO": "prof@example.test"}):
            config = MailConfig.environment()
        self.assertEqual((config.url, config.reply_to, config.enabled), ("https://relay.example.test/message", "prof@example.test", True))
        with mock.patch.dict(os.environ, {"FACULTY_PRINT_EMAIL_URL": "", "FACULTY_PRINT_EMAIL_SECRET": "", "FACULTY_MAIL_REPLY_TO": "nonsense"}):
            config = MailConfig.environment()
        self.assertEqual((config.url, config.reply_to, config.enabled), ("", "instructor@tamu.edu", False))

    def test_explicit_resend_uses_relay_even_with_gmail_connected(self):
        mailer = self.cards.mail
        mailer.gmail = mock.Mock(connected=True, email="teacher@example.test")
        mailer.gmail.status.return_value = {"connected": True}
        with mock.patch.dict(os.environ, {"FACULTY_PRINT_EMAIL_URL": "https://relay.example.test/send",
                                         "FACULTY_PRINT_EMAIL_SECRET": "s" * 40,
                                         "FACULTY_OUTBOUND_EMAIL_PROVIDER": "resend"}):
            mailer.config = MailConfig.environment()
        self.assertEqual(mailer.provider, "resend")
        self.assertEqual(mailer.status()["sender"], "Professor Sinkule <exam@example.com>")
        payload = {"mail_id": "message-1"}
        with mock.patch.object(mailer, "relay", return_value={"message_id": "receipt"}) as relay:
            self.assertEqual(mailer.deliver(payload), {"message_id": "receipt"})
            relay.assert_called_once_with(payload)
        mailer.gmail.send.assert_not_called()
        # An explicitly selected provider must never silently use another one.
        for config in (MailConfig(provider="resend"), MailConfig(provider="typo")):
            mailer.config = config
            self.assertFalse(mailer.enabled)
            with self.assertRaises(DeliveryError):
                mailer.deliver(payload)
        mailer.gmail.send.assert_not_called()


class RelayRateLimitContractTests(unittest.TestCase):
    def setUp(self):
        self.mailer = CardMailer(SimpleNamespace(rosters=None, path=":memory:"),
                                 MailConfig("https://relay.example.test/message", "x" * 40),
                                 gmail=SimpleNamespace(connected=False))
        self.payload = {"mail_id": "test-mail", "recipient": "student@example.test", "subject": "Test",
                        "text": "Test", "html": "<p>Test</p>", "reply_to": "teacher@example.test"}

    @staticmethod
    def rejection(body, retry_after="60", code=429):
        headers = Message()
        if retry_after is not None:
            for value in retry_after if isinstance(retry_after, list) else [retry_after]:
                headers.add_header("Retry-After", value)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return urllib.error.HTTPError("https://relay.example.test/message", code, "Rejected", headers, io.BytesIO(raw))

    def deliver_error(self, error):
        with mock.patch("response_cards.mail.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(DeliveryError) as raised:
                self.mailer.relay(self.payload)
        return raised.exception

    def test_only_exact_relay_rejection_allows_a_bounded_retry(self):
        for delay in (1, 60, 86400):
            with self.subTest(delay=delay):
                error = self.deliver_error(self.rejection({"error": "rate_limited", "retry_after_seconds": delay}, str(delay)))
                self.assertIsInstance(error, RelayRateLimit)
                self.assertFalse(error.uncertain)
                self.assertEqual(error.retry_after, delay)

    def test_unknown_or_malformed_429_never_becomes_a_retry(self):
        exact = {"error": "rate_limited", "retry_after_seconds": 60}
        cases = [(b"not JSON", "60"), ([], "60"), ({"error": "rate_limited"}, "60"),
                 ({**exact, "accepted": True}, "60"), ({**exact, "error": "unknown"}, "60"),
                 (exact, None), (exact, "61"), (exact, ["60", "60"]),
                 (b'{"error":"unknown","error":"rate_limited","retry_after_seconds":60}', "60"),
                 (b" " * 16385, "60")]
        cases.extend(({**exact, "retry_after_seconds": value}, str(value)) for value in (0, -1, 86401, True, 60.0, "60", None))
        for body, header in cases:
            with self.subTest(body=repr(body)[:120], header=header):
                error = self.deliver_error(self.rejection(body, header))
                self.assertNotIsInstance(error, RelayRateLimit)
                self.assertFalse(error.unavailable)
                self.assertFalse(error.uncertain)  # Preserve existing definite HTTP429 failure.

    def test_other_http_network_and_malformed_success_stay_unconfirmed(self):
        errors = [self.rejection({"error": "rate_limited", "retry_after_seconds": 60}, code=503),
                  urllib.error.URLError("Connection interrupted")]
        for failure in errors:
            with self.subTest(failure=type(failure).__name__):
                error = self.deliver_error(failure)
                self.assertTrue(error.uncertain)
                self.assertFalse(error.unavailable)
        with mock.patch("response_cards.mail.urllib.request.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 202
            response.read.return_value = b'{"mail_id":"test-mail","status":"accepted"}'
            with self.assertRaises(DeliveryError) as raised:
                self.mailer.relay(self.payload)
        self.assertTrue(raised.exception.uncertain)
        self.assertFalse(raised.exception.unavailable)


class FakeGoogle:
    """Answers Google's token, userinfo, and Gmail send endpoints in memory; never touches the network."""
    def __init__(self):
        self.calls, self.sent, self.fail, self.refreshes = [], [], None, 0
        self.lost = self.unreachable = None  # a send Google kept whose answer is lost; a connection that never reached Google

    def __call__(self, url, data=None, headers=None, timeout=30):
        self.calls.append((url, data, headers))
        if url == TOKEN_URL:
            if data.get("grant_type") == "authorization_code":
                if data.get("code") != "good-code" or not data.get("code_verifier"):
                    return 400, {"error": "invalid_grant", "error_description": "Bad code"}
                return 200, {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600, "scope": " ".join(SCOPES)}
            self.refreshes += 1
            if data.get("refresh_token") == "revoked":
                return 400, {"error": "invalid_grant"}
            return 200, {"access_token": f"access-{self.refreshes + 1}", "expires_in": 3600}
        if url == USERINFO_URL:
            return 200, {"email": "prof@example.edu"}
        if url == SEND_URL:
            if self.unreachable is not None:
                error, self.unreachable = self.unreachable, None
                raise error
            if self.lost is not None:
                error, self.lost = self.lost, None
                self.sent.append((headers["Authorization"], data["raw"]))
                raise error
            if self.fail is not None:
                status, payload = self.fail
                self.fail = None
                return status, payload
            self.sent.append((headers["Authorization"], data["raw"]))
            return 200, {"id": f"gm-{len(self.sent)}", "threadId": "thread"}
        if url == REVOKE_URL:
            return 200, {}
        raise AssertionError(f"unexpected call to {url}")


class GmailDeliveryTests(unittest.TestCase):
    managed = False
    add = roster_tests.RosterTests.add
    route = roster_tests.RosterTests.route
    upload = roster_tests.RosterTests.upload
    change = roster_tests.RosterTests.change
    drain = ResponseCardMailTests.drain
    mail_rows = ResponseCardMailTests.mail_rows
    assert_generic_message = ResponseCardMailTests.assert_generic_message

    def setUp(self):
        ResponseCardMailTests.setUp(self)
        self.google = FakeGoogle()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        client = os.path.join(self.home.name, "gmail-client.json")
        with open(client, "w", encoding="utf-8") as handle:
            json.dump({"web": {"client_id": "client-1.apps.googleusercontent.com", "client_secret": "secret-1"}}, handle)
        self.token_file = os.path.join(self.home.name, "gmail-token.json")
        self.cards.mail.gmail = GmailConnection(client, self.token_file, sender_name="Professor Sinkule")
        self.cards.mail.gmail.http = self.google
        self.cards.mail.transport = self.cards.mail._post  # the real dispatcher, over the fake Google

    def connect(self):
        url = self.client.post("/api/response-cards/gmail/connect", json={}, headers=HEADERS).json["url"]
        with open(self.token_file, encoding="utf-8") as handle:
            state = json.load(handle)["pending"]["state"]
        response = self.client.get(f"/api/response-cards/gmail/callback?state={state}&code=good-code")
        self.assertEqual((response.status_code, response.headers["Location"]), (302, "/#response-cards"), response.data[:200])
        return url

    def message(self, index):
        _, raw = self.google.sent[index]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)

    def gmail_pause(self):
        with connect(self.cards.path) as db:
            return provider_pauses(db)["gmail"]

    def use_resend_default(self):
        self.cards.mail.config.provider = "resend"
        self.cards.mail.relay = mock.Mock(side_effect=lambda payload: {
            "mail_id": payload["mail_id"], "status": "accepted", "message_id": "resend-receipt"})

    def test_optional_gmail_and_default_are_pinned_across_restart_and_confirmation_uses_default(self):
        self.connect()
        self.use_resend_default()
        self.cards.publish(self.issue["id"])
        recipients = self.cards.get_issue(self.issue["id"])["recipients"]
        alice, bob = sorted(recipients, key=lambda r: r["email"])
        self.assertEqual(self.client.post(f"/api/response-cards/cards/{alice['id']}/resend",
                                         json={"mail_provider": "gmail"}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.post(f"/api/response-cards/issues/{self.issue['id']}/send",
                                         json={}, headers=HEADERS).json["queued"]["queued"], 1)
        rows = {row["recipient"]: row for row in self.mail_rows()}
        self.assertEqual((rows[alice["email"]]["provider"], rows[alice["email"]]["gmail_sender"]), ("gmail", "prof@example.edu"))
        self.assertEqual((rows[bob["email"]]["provider"], rows[bob["email"]]["gmail_sender"]), ("resend", ""))
        # A later configuration change must not alter either queued choice.
        relay = self.cards.mail.relay
        restarted = CardMailer(self.cards, MailConfig("https://relay.example.test/message", "x" * 40, provider="gmail"),
                               gmail=self.cards.mail.gmail)
        restarted.relay = relay
        while restarted.process_one():
            pass
        self.assertEqual(len(self.google.sent), 1)
        self.assertEqual(self.message(0)["To"], alice["email"])
        self.assertEqual(relay.call_args.args[0]["recipient"], bob["email"])
        self.assertEqual(self.cards.mail.provider, "resend")
        self.remote.event(alice["id"], "verified", submission={
            "id": "optional-gmail-submission", "request_id": "optional-gmail-response", "status": "verified",
            "submitted_at": "2026-09-15T14:05:00.000Z", "answers": {"room": "HECC 105", "pencil": True}, "verification": {}})
        self.cards.sync()
        confirmation = next(row for row in self.mail_rows() if row["kind"] == "confirmation")
        self.assertEqual(confirmation["provider"], "resend")
        self.drain()
        self.assertEqual((len(self.google.sent), relay.call_count), (1, 2))

    def test_optional_gmail_test_and_issue_send_keep_resend_default(self):
        self.connect()
        self.use_resend_default()
        test = self.client.post("/api/response-cards/mail/test", json={"mail_provider": "gmail"}, headers=HEADERS)
        self.assertEqual((test.status_code, test.json["recipient"]), (200, "prof@example.edu"))
        self.assertEqual(self.message(0)["To"], "prof@example.edu")
        self.cards.mail.relay.assert_not_called()
        default_test = self.client.post("/api/response-cards/mail/test", json={}, headers=HEADERS)
        self.assertEqual((default_test.status_code, default_test.json["recipient"]), (200, "instructor@tamu.edu"))
        self.assertEqual(self.cards.mail.relay.call_count, 1)
        self.cards.publish(self.issue["id"])
        response = self.client.post(f"/api/response-cards/issues/{self.issue['id']}/send",
                                    json={"mail_provider": "gmail"}, headers=HEADERS)
        self.assertEqual((response.status_code, response.json["queued"]["queued"]), (200, 2))
        self.drain()
        self.assertEqual(len(self.google.sent), 3)
        self.assertEqual(self.cards.mail.provider, "resend")

    def test_invalid_or_disconnected_gmail_never_queues_or_falls_back(self):
        self.use_resend_default()
        self.cards.publish(self.issue["id"])
        card = self.cards.get_issue(self.issue["id"])["recipients"][0]
        endpoints = [f"/api/response-cards/issues/{self.issue['id']}/send",
                     f"/api/response-cards/cards/{card['id']}/resend", "/api/response-cards/mail/test"]
        for endpoint in endpoints:
            for choice, status in (("gmail", 503), ("bogus", 400), (None, 400), ([], 400), ({}, 400)):
                with self.subTest(endpoint=endpoint, choice=choice):
                    response = self.client.post(endpoint, json={"mail_provider": choice}, headers=HEADERS)
                    self.assertEqual(response.status_code, status)
        self.assertEqual(self.mail_rows(), [])
        self.assertEqual(self.google.sent, [])
        self.cards.mail.relay.assert_not_called()
        self.connect()
        self.cards.mail.queue_resend(card["id"], mail_provider="gmail")
        self.cards.mail.gmail.disconnect()
        self.assertFalse(self.cards.mail.process_one())
        row = self.mail_rows()[0]
        self.assertEqual((row["provider"], row["status"]), ("gmail", "pending"))
        self.assertIn("Reconnect Gmail", row["error"])
        self.cards.mail.relay.assert_not_called()

    def test_gmail_pause_survives_restart_but_does_not_block_resend(self):
        self.connect()
        self.use_resend_default()
        self.cards.publish(self.issue["id"])
        alice, bob = sorted(self.cards.get_issue(self.issue["id"])["recipients"], key=lambda r: r["email"])
        self.cards.mail.queue_resend(alice["id"], mail_provider="gmail")
        self.cards.mail.queue_resend(bob["id"])
        self.google.fail = (429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
        self.assertFalse(self.cards.mail.process_one())
        restarted = CardMailer(self.cards, self.cards.mail.config, gmail=self.cards.mail.gmail)
        restarted.relay = self.cards.mail.relay
        self.assertTrue(restarted.process_one())
        self.assertFalse(restarted.process_one())
        self.assertEqual(len(self.google.sent), 0)
        self.assertEqual(restarted.relay.call_args.args[0]["recipient"], bob["email"])
        self.assertEqual({row["provider"]: row["status"] for row in self.mail_rows()}, {"gmail": "pending", "resend": "accepted"})
        # Reconnecting another Gmail account cannot redirect the waiting mail.
        with open(self.token_file, encoding="utf-8") as handle:
            state = json.load(handle)
        state["email"] = "another@example.edu"
        with open(self.token_file, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        with mock.patch("response_cards.mail.time.time", return_value=time.time() + 301):
            self.assertFalse(restarted.process_one())
        self.assertIn("account chosen", next(row for row in self.mail_rows() if row["provider"] == "gmail")["error"])
        self.assertEqual((len(self.google.sent), restarted.relay.call_count), (0, 1))

    def test_reconnection_in_another_process_invalidates_the_old_accounts_cached_access_token(self):
        self.connect()
        self.use_resend_default()
        self.assertEqual(self.cards.mail.gmail.access_token(), "access-1")
        with open(self.token_file, encoding="utf-8") as handle:
            state = json.load(handle)
        state.update(email="another@example.edu", refresh_token="refresh-another")
        with open(self.token_file, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        response = self.client.post("/api/response-cards/mail/test", json={"mail_provider": "gmail"}, headers=HEADERS)
        self.assertEqual((response.status_code, response.json["recipient"]), (200, "another@example.edu"))
        self.assertEqual(self.google.sent[0][0], "Bearer access-2")
        self.assertEqual(self.message(0)["From"], "Professor Sinkule <another@example.edu>")
        self.assertEqual(self.google.refreshes, 1)
        self.cards.mail.relay.assert_not_called()
        # A disconnected token file cannot leave the worker's cache authorized.
        os.remove(self.token_file)
        with self.assertRaises(DeliveryError):
            self.cards.mail.gmail.access_token()

    def test_unknown_workspace_provider_disables_even_previously_pinned_mail(self):
        self.connect()
        self.use_resend_default()
        self.cards.publish(self.issue["id"])
        alice, bob = self.cards.get_issue(self.issue["id"])["recipients"]
        self.cards.mail.queue_resend(alice["id"], mail_provider="gmail")
        self.cards.mail.queue_resend(bob["id"])
        self.cards.mail.config.provider = "typo"
        self.assertFalse(self.cards.mail.process_one())
        self.assertFalse(self.cards.mail.process_one())
        for choice in ("default", "gmail"):
            self.assertEqual(self.client.post("/api/response-cards/mail/test", json={"mail_provider": choice}, headers=HEADERS).status_code, 503)
        self.assertEqual({row["status"] for row in self.mail_rows()}, {"pending"})
        self.assertEqual(self.google.sent, [])
        self.cards.mail.relay.assert_not_called()

    def test_connecting_gmail_sends_student_mail_as_the_instructor(self):
        self.assertEqual((self.cards.mail.provider, self.cards.mail.gmail.status()["connected"]), ("relay", False))
        self.assertTrue(self.cards.mail.gmail.status()["configured"])
        url = self.client.post("/api/response-cards/gmail/connect", json={}, headers=HEADERS).json["url"]
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual((query["client_id"], query["response_type"], query["access_type"], query["prompt"], query["code_challenge_method"], query["login_hint"]),
                         ("client-1.apps.googleusercontent.com", "code", "offline", "consent", "S256", "instructor@tamu.edu"))
        self.assertEqual(query["redirect_uri"], "http://127.0.0.1:3000/api/response-cards/gmail/callback")
        self.assertEqual(set(query["scope"].split()), set(SCOPES))
        # A mismatched state consumes the pending sign-in; nothing is stored.
        wrong = self.client.get("/api/response-cards/gmail/callback?state=wrong&code=good-code")
        self.assertEqual(wrong.status_code, 400)
        self.assertIn(b"did not match", wrong.data)
        self.assertFalse(self.cards.mail.gmail.connected)
        self.assertEqual(self.client.get("/api/response-cards/gmail/callback?state=x&code=y").status_code, 400)  # nothing pending
        refused = self.client.get("/api/response-cards/gmail/callback?error=access_denied")
        self.assertEqual(refused.status_code, 400)
        self.assertIn(b"access_denied", refused.data)
        self.connect()
        self.assertEqual(oct(os.stat(self.token_file).st_mode & 0o777), "0o600")
        status = self.cards.mail.status()
        self.assertEqual((status["provider"], status["sender"], status["gmail"]["email"], status["gmail"]["connected"]),
                         ("gmail", "Professor Sinkule <prof@example.edu>", "prof@example.edu", True))
        self.assertEqual(self.cards.context()["mail"]["provider"], "gmail")
        self.cards.publish(self.issue["id"])
        self.assertEqual(self.cards.mail.queue_invitations(self.issue["id"]), {"queued": 2, "no_email": 0})
        self.drain()
        self.assertEqual(self.relay.sent, [])
        self.assertEqual(len(self.google.sent), 2)
        self.assertEqual(self.google.refreshes, 0)  # the sign-in's access token is reused
        sent = {self.message(i)["To"]: self.message(i) for i in range(2)}
        self.assertEqual(set(sent), {"alice@example.test", "bob@example.test"})
        for message in sent.values():
            self.assert_generic_message({"recipient": str(message["To"]), "subject": str(message["Subject"]),
                                         "text_body": message.get_body(preferencelist=("plain",)).get_content(),
                                         "html_body": message.get_body(preferencelist=("html",)).get_content()})
            self.assertEqual(message["To"].addresses[0].display_name, "")
        alice = sent["alice@example.test"]
        self.assertEqual((alice["From"], alice["Reply-To"], alice["Subject"]),
                         ("Professor Sinkule <prof@example.edu>", "instructor@tamu.edu", "IMPORTANT: Information about your upcoming Test 1 for MATH 140"))
        self.assertIn("<a href=", alice.get_body(preferencelist=("html",)).get_content())
        rows = self.mail_rows()
        self.assertEqual({r["status"] for r in rows}, {"accepted"})
        self.assertEqual(sorted(json.loads(r["provider_receipt_json"])["message_id"] for r in rows), ["gm-1", "gm-2"])
        self.assertEqual({r["delivery_status"] for r in self.cards.get_issue(self.issue["id"])["recipients"]}, {"sent"})
        test = self.client.post("/api/response-cards/mail/test", json={}, headers=HEADERS)
        self.assertEqual((test.status_code, test.json["recipient"], test.json["receipt"]["message_id"]), (200, "instructor@tamu.edu", "gm-3"))
        self.assertEqual((self.message(2)["To"], self.message(2)["Reply-To"]), ("instructor@tamu.edu", "instructor@tamu.edu"))
        # No Reply-To when it would only repeat the sender.
        self.cards.mail.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4, "prof@example.edu")
        self.assertEqual(self.client.post("/api/response-cards/mail/test", json={}, headers=HEADERS).json["recipient"], "prof@example.edu")
        self.assertNotIn("Reply-To", self.message(3))
        gone = self.client.post("/api/response-cards/gmail/disconnect", json={}, headers=HEADERS).json
        self.assertEqual((gone["connected"], gone["email"], os.path.exists(self.token_file)), (False, None, False))
        self.assertEqual(self.cards.mail.provider, "relay")
        self.assertIn(REVOKE_URL, [call[0] for call in self.google.calls])

    def test_gmail_limits_pause_the_queue_and_rejections_fail_one_message(self):
        self.connect()
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.google.fail = (429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Rate limit"}})
        self.assertFalse(self.cards.mail.process_one())
        self.assertEqual([r["status"] for r in self.mail_rows()], ["pending", "pending"])
        self.assertGreater(self.gmail_pause() - time.time(), 200)
        self.assertFalse(self.cards.mail.process_one())  # paused
        self.google.fail = (403, {"error": {"errors": [{"reason": "dailyLimitExceeded"}], "message": "Daily limit"}})
        with mock.patch("response_cards.mail.time.time", return_value=time.time() + 301):
            self.assertFalse(self.cards.mail.process_one())
        self.assertGreater(self.gmail_pause() - time.time(), 600)
        self.assertIn("daily sending limit", self.mail_rows()[0]["error"])
        self.google.fail = (400, {"error": {"message": "Invalid To header"}})
        with mock.patch("response_cards.mail.time.time", return_value=time.time() + 1202):
            self.assertTrue(self.cards.mail.process_one())
        self.assertEqual(self.mail_rows()[0]["status"], "failed")
        # An expired access token is renewed once and the send retried; the message is not duplicated.
        self.google.fail = (401, {"error": {"status": "UNAUTHENTICATED"}})
        self.assertTrue(self.cards.mail.process_one())
        self.assertEqual(([r["status"] for r in self.mail_rows()], len(self.google.sent), self.google.refreshes), (["failed", "accepted"], 1, 1))
        self.assertEqual(sorted(r["delivery_status"] for r in self.cards.get_issue(self.issue["id"])["recipients"]), ["failed", "sent"])
        # A revoked grant keeps mail queued with a reconnect message rather than failing it.
        with open(self.token_file, encoding="utf-8") as handle:
            stored = json.load(handle)
        stored["refresh_token"] = "revoked"
        with open(self.token_file, "w", encoding="utf-8") as handle:
            json.dump(stored, handle)
        with self.assertRaises(DeliveryError) as raised:
            self.cards.mail.gmail.access_token(force=True)
        self.assertEqual((raised.exception.unavailable, raised.exception.retry_after), (True, 900))
        self.assertIn("Reconnect Gmail", str(raised.exception))
        self.google.fail = (500, {})
        self.cards.mail.gmail._access = ("access-x", time.time() + 3600)
        self.cards.mail.queue_confirmations()  # nothing to queue, but the path must not send
        self.assertFalse(self.cards.mail.process_one())

    def test_a_send_whose_answer_was_lost_is_never_sent_again(self):
        # Google kept both messages; only the answers were lost. Unconfirmed, never retried (AGENTS.md "OUTSIDE SERVICES").
        self.connect()
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.google.lost = http.client.RemoteDisconnected("Remote end closed connection without response")
        self.assertTrue(self.cards.mail.process_one())
        self.google.lost = ConnectionResetError(104, "Connection reset by peer")
        self.assertTrue(self.cards.mail.process_one())
        self.assertEqual([r["status"] for r in self.mail_rows()], ["unconfirmed", "unconfirmed"])
        with mock.patch("response_cards.mail.time.time", return_value=time.time() + 3600):
            self.cards.mail.process_one()
        self.assertEqual(([r["status"] for r in self.mail_rows()], len(self.google.sent)), (["unconfirmed", "unconfirmed"], 2))
        self.assertEqual(sorted(r["delivery_status"] for r in self.cards.get_issue(self.issue["id"])["recipients"]), ["unconfirmed", "unconfirmed"])

    def test_a_connection_that_never_reached_google_waits_and_sends_once(self):
        self.connect()
        self.cards.publish(self.issue["id"])
        self.cards.mail.queue_invitations(self.issue["id"])
        self.google.unreachable = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        self.assertFalse(self.cards.mail.process_one())
        self.assertEqual(([r["status"] for r in self.mail_rows()], len(self.google.sent)), (["pending", "pending"], 0))
        with mock.patch("response_cards.mail.time.time", return_value=time.time() + 3600):
            self.assertTrue(self.cards.mail.process_one())
            self.assertTrue(self.cards.mail.process_one())
        self.assertEqual(([r["status"] for r in self.mail_rows()], len(self.google.sent)), (["accepted", "accepted"], 2))

    def test_gmail_is_inert_until_both_files_are_configured(self):
        with mock.patch.dict(os.environ, {"FACULTY_GMAIL_CLIENT_FILE": "", "FACULTY_GMAIL_TOKEN_FILE": ""}):
            connection = GmailConnection.environment("Professor Sinkule")
        self.assertEqual(connection.status(), {"configured": False, "connected": False, "email": None, "connected_at": None})
        with self.assertRaises(ValueError):
            connection.start()
        missing = GmailConnection(os.path.join(self.home.name, "absent.json"), self.token_file)
        self.assertFalse(missing.configured)
        self.cards.mail.gmail = missing
        self.assertEqual(self.cards.mail.provider, "relay")
        self.assertEqual(self.client.post("/api/response-cards/gmail/connect", json={}, headers=HEADERS).status_code, 400)


if __name__ == "__main__":
    unittest.main()
