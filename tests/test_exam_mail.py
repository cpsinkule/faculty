"""Never distribute reopened feedback or revised decisions from an old queue."""
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from course_storage import StorageError
from response_cards.mail import MailConfig
from rosters import connect
import test_exam_grading as grading_fixture
from test_response_card_mail import FakeRelay
from test_response_cards import FakeRemote


class ExamMailTests(unittest.TestCase):
    document_version = grading_fixture.ExamGradingTests.document_version
    ready_batch = grading_fixture.ExamGradingTests.ready_batch
    reviewed_source = grading_fixture.ExamGradingTests.reviewed_source
    participant = grading_fixture.ExamGradingTests.participant
    data = grading_fixture.ExamGradingTests.data
    mark = grading_fixture.ExamGradingTests.mark
    complete = grading_fixture.ExamGradingTests.complete

    def setUp(self):
        grading_fixture.ExamGradingTests.setUp(self)
        self.cards = self.app.extensions["response_cards"]
        self.requests = self.app.extensions["exam_requests"]
        self.cards.remote = FakeRemote()
        self.relay = FakeRelay()
        self.cards.mail.config = MailConfig("https://relay.example.test/message", "test-secret-" * 4)
        self.cards.mail.transport = self.relay

    def feedback_issue(self):
        self.complete()
        release = self.grading.release(self.event["id"], self.data(request_id=str(uuid.uuid4())))
        prepared = self.requests.prepare_cards(self.event["id"], {"release_id": release["id"], "request_id": str(uuid.uuid4())}, "feedback")
        return self.cards.publish(prepared["id"])

    def decision_issue(self):
        row = self.requests.create(self.event["id"], {"section_id": self.section["id"], "enrollment_id": self.enrollment,
            "kind": "notice", "description": "Please check my exam notice."})
        row = self.requests.update(self.event["id"], row["id"], {"version": row["version"], "status": "approved", "decision": "The corrected notice is approved."})
        prepared = self.requests.prepare_cards(self.event["id"], {"exam_request_id": row["id"], "request_id": str(uuid.uuid4())}, "decision")
        return row, self.cards.publish(prepared["id"])

    def rows(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM response_card_mail ORDER BY created_at,id")]

    def reopen(self):
        self.grading.reopen(self.event["id"], self.data(reason="Instructor is correcting feedback"))

    def revise(self, row):
        return self.requests.update(self.event["id"], row["id"], {"version": row["version"], "status": "declined", "decision": "New facts changed this decision."})

    def test_reopened_feedback_blocks_queue_and_resend(self):
        issue = self.feedback_issue()
        self.assertTrue(self.cards.link(issue["recipients"][0]["id"])["link"])
        self.reopen()
        with self.assertRaises(Conflict):
            self.cards.mail.queue_invitations(issue["id"])
        with self.assertRaises(Conflict):
            self.cards.mail.queue_resend(issue["recipients"][0]["id"])
        with self.assertRaises(Conflict):
            self.cards.link(issue["recipients"][0]["id"])
        retained = self.cards.get_issue(issue["id"])["recipients"][0]
        self.assertIsNone(retained["link"])
        self.assertTrue(retained["distribution_block"])
        self.assertEqual(retained["schema"], issue["recipients"][0]["schema"])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.relay.sent, [])

    def test_reopened_feedback_queued_earlier_fails_without_sending_or_retrying(self):
        issue = self.feedback_issue()
        self.cards.mail.queue_invitations(issue["id"])
        self.reopen()
        self.assertTrue(self.cards.mail.process_one())
        self.assertFalse(self.cards.mail.process_one())
        row = self.rows()[0]
        self.assertEqual((row["status"], row["attempts"], row["provider_receipt_json"]), ("failed", 1, None))
        self.assertIn("not sent", row["error"])
        self.assertEqual(self.relay.sent, [])
        self.assertEqual(self.cards.get_issue(issue["id"])["recipients"][0]["delivery_status"], "failed")

    def test_revised_decision_blocks_queue_and_resend(self):
        request, issue = self.decision_issue()
        self.revise(request)
        with self.assertRaises(Conflict):
            self.cards.mail.queue_invitations(issue["id"])
        with self.assertRaises(Conflict):
            self.cards.mail.queue_resend(issue["recipients"][0]["id"])
        with self.assertRaises(Conflict):
            self.cards.link(issue["recipients"][0]["id"])
        retained = self.cards.get_issue(issue["id"])["recipients"][0]
        self.assertIsNone(retained["link"])
        self.assertTrue(retained["distribution_block"])
        self.assertEqual(self.rows(), [])

    def test_revised_decision_queued_earlier_fails_without_transport(self):
        request, issue = self.decision_issue()
        self.cards.mail.queue_invitations(issue["id"])
        self.revise(request)
        self.assertTrue(self.cards.mail.process_one())
        self.assertFalse(self.cards.mail.process_one())
        self.assertEqual(self.rows()[0]["status"], "failed")
        self.assertEqual(self.relay.sent, [])

    def test_valid_feedback_verifies_before_transaction_and_retains_provider_receipt(self):
        issue = self.feedback_issue()
        original = self.requests.issue_context
        def checked(db, normalized, student=None):
            self.assertFalse(db.in_transaction, "Verified source reads must precede the catalog writer")
            return original(db, normalized, student)
        with patch.object(self.requests, "issue_context", side_effect=checked):
            self.cards.mail.queue_invitations(issue["id"])
            self.assertTrue(self.cards.mail.process_one())
        self.assertEqual(self.rows()[0]["status"], "accepted")
        self.assertTrue(self.rows()[0]["provider_receipt_json"])
        self.assertEqual(len(self.relay.sent), 1)

    def test_unverifiable_exam_source_fails_without_disclosing_or_retrying(self):
        issue = self.feedback_issue()
        self.cards.mail.queue_invitations(issue["id"])
        with patch.object(self.requests, "issue_context", side_effect=StorageError("Sensitive filesystem detail")):
            self.assertTrue(self.cards.mail.process_one())
        self.assertFalse(self.cards.mail.process_one())
        self.assertEqual(self.rows()[0]["status"], "failed")
        self.assertNotIn("Sensitive", self.rows()[0]["error"])
        self.assertEqual(self.relay.sent, [])

    def test_feedback_cannot_change_between_validation_and_provider_call(self):
        issue = self.feedback_issue()
        self.cards.mail.queue_invitations(issue["id"])
        provider_entered, finish_provider, edit_started = threading.Event(), threading.Event(), threading.Event()
        reopen_data = self.data(reason="Correct feedback after distribution")
        def provider(job):
            provider_entered.set()
            if not finish_provider.wait(3):
                raise AssertionError("The test did not release its provider")
            return self.relay(job)
        def edit():
            edit_started.set()
            return self.grading.reopen(self.event["id"], reopen_data)
        self.cards.mail.transport = provider
        with ThreadPoolExecutor(max_workers=2) as executor:
            sending = executor.submit(self.cards.mail.process_one)
            try:
                self.assertTrue(provider_entered.wait(3))
                editing = executor.submit(edit)
                self.assertTrue(edit_started.wait(3))
                with self.assertRaises(TimeoutError):
                    editing.result(timeout=.05)
            finally:
                finish_provider.set()
            self.assertTrue(sending.result(timeout=3))
            editing.result(timeout=3)
        self.assertEqual(len(self.relay.sent), 1)
        self.assertEqual(self.rows()[0]["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
