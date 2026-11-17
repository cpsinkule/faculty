"""The worker skips the search for unsaved grading decisions only while nothing it reads could have changed."""
import unittest
from unittest.mock import patch

from grading import training
import test_frq_grading as frq_fixture
import test_grading_training
from test_grading_training import saved


class TrainingCaptureGate(unittest.TestCase):
    managed = False
    setUp = frq_fixture.FrqWorkflowTests.setUp
    document_version = frq_fixture.FrqWorkflowTests.document_version
    ready_batch = frq_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = frq_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = frq_fixture.FrqWorkflowTests.configure_rubric
    queue = frq_fixture.FrqWorkflowTests.queue
    finish = frq_fixture.FrqWorkflowTests.finish
    approve = frq_fixture.FrqWorkflowTests.approve
    review = test_grading_training.InstructorDecisions.review

    def searches(self):
        calls = []
        original = self.frq._unsaved_decisions

        def counted(db, limit):
            calls.append(limit)
            return original(db, limit)
        return calls, patch.object(self.frq, "_unsaved_decisions", counted)

    def test_a_search_that_found_nothing_is_trusted_until_a_decision_arrives(self):
        response = self.finish(self.queue()[0])
        self.review(response, "draft")  # a draft decides nothing
        calls, counting = self.searches()
        with counting:
            self.assertFalse(self.frq.capture_training_one())
            self.assertFalse(self.frq.capture_training_one())
            self.assertEqual(len(calls), 1, "The second turn read only the high-water marks.")
            approved = self.approve(self.frq.response(response["id"]))
            self.assertTrue(self.frq.capture_training_one())
            self.assertEqual(len(calls), 2)
        self.assertEqual([(row["decision_kind"], row["decision_id"]) for row in saved(self.path)], [("review", approved["review"]["id"])])

    def test_a_decision_committed_while_the_search_ran_is_saved_on_the_next_turn(self):
        response = self.finish(self.queue()[0])
        self.review(response, "draft")  # moves the marks: the next turn searches
        original, raced = self.frq._unsaved_decisions, []

        def racing(db, limit):
            found = original(db, limit)
            if not raced:  # committed by another connection after this search's snapshot began: the search cannot see it
                raced.append(self.approve(self.frq.response(response["id"])))
            return found
        with patch.object(self.frq, "_unsaved_decisions", racing):
            self.assertFalse(self.frq.capture_training_one())
            self.assertTrue(raced)
            self.assertTrue(self.frq.capture_training_one())
        self.assertEqual([row["decision_id"] for row in saved(self.path)], [raced[0]["review"]["id"]])

    def test_the_search_runs_again_after_ten_minutes_whatever_the_marks_say(self):
        self.review(self.finish(self.queue()[0]), "draft")
        calls, counting = self.searches()
        with counting, patch("grading.training.time.monotonic", return_value=1000.0):
            self.assertFalse(self.frq.capture_training_one())
            self.assertFalse(self.frq.capture_training_one())
        self.assertEqual(len(calls), 1)
        with counting, patch("grading.training.time.monotonic", return_value=1000.0 + training.NOTHING_FOR):
            self.assertFalse(self.frq.capture_training_one())
        self.assertEqual(len(calls), 2)

    def test_decisions_set_aside_keep_the_search_running_every_turn(self):
        response = self.finish(self.queue()[0])
        self.approve(self.frq.response(response["id"]))
        with patch.object(self.frq, "_training_row", side_effect=ValueError("cannot be made")), self.assertLogs("grading.training", "ERROR"):
            self.assertFalse(self.frq.capture_training_one())  # set aside, not saved
        calls, counting = self.searches()
        with counting:
            self.assertFalse(self.frq.capture_training_one())
            self.assertFalse(self.frq.capture_training_one())
        self.assertEqual(len(calls), 2, "Only a search that found nothing at all is trusted.")
        self.assertEqual(saved(self.path), [])


if __name__ == "__main__":
    unittest.main()
