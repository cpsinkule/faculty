"""Navigation stays cheap; request-local reuse never survives a source change."""
import unittest
from unittest.mock import patch

from rosters import connect
import test_frq_grading as fixture
from frq_fixtures import finalize_rubrics


class GradingLoadingTests(unittest.TestCase):
    managed = False
    setUp = fixture.FrqWorkflowTests.setUp
    document_version = fixture.FrqWorkflowTests.document_version
    ready_batch = fixture.FrqWorkflowTests.ready_batch
    reviewed_source = fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = fixture.FrqWorkflowTests.configure_rubric
    queue = fixture.FrqWorkflowTests.queue
    finish = fixture.FrqWorkflowTests.finish
    approve = fixture.FrqWorkflowTests.approve

    def test_navigation_never_reconciles_papers_or_contacts_providers(self):
        self.queue()
        with patch.object(self.frq, "workflow_summary", side_effect=AssertionError("Full workflow")), \
                patch.object(self.frq, "responses", side_effect=AssertionError("All responses")), \
                patch.object(self.frq, "health", side_effect=AssertionError("Provider health")):
            response = self.app.test_client().get("/api/grading?view=navigation")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(set(data), {"assignments", "jobs", "preparations", 'waiting_for_inbox', 'waiting_href', 'waiting'})
        self.assertEqual(data["assignments"][0]["id"], self.plan["id"])
        self.assertEqual(data["jobs"][0]["assignment_id"], self.plan["id"])
        self.assertNotIn("workflow", data["assignments"][0])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_full_overview_retains_progress_without_waiting_for_health(self):
        with patch.object(self.frq, "health", side_effect=AssertionError("Provider health")):
            response = self.app.test_client().get("/api/grading?health=0")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertNotIn("health", data)
        self.assertEqual(data["assignments"][0]["workflow"]["new_responses"], 1)
        self.assertIn("health", self.app.test_client().get("/api/grading").get_json())
        self.assertEqual(self.app.test_client().get("/api/grading?view=unknown").status_code, 400)

    def test_paper_reuse_is_per_request_and_rechecks_review_gates(self):
        # Two questions of the same paper share only the expensive paper read.
        q = self.plan["questions"][0]
        plan = self.frq.save_question(self.plan["id"], {"version": self.plan["version"], "question_key": "q2",
            "assignment_version_id": q["assignment_version_id"], "content": q["content"]})
        second = next(q for q in plan["questions"] if q["question_key"] == "q2")
        plan = self.frq.import_rubric(plan["id"], second["question_id"], {"version": second["version"], "source_text": "-1 Arithmetic error"})
        second = next(q for q in plan["questions"] if q["question_key"] == "q2")
        self.frq.approve_rubric(plan["id"], second["question_id"], {"version": second["version"], "rubric_id": second["rubric_id"], "sha256": second["rubric"]["sha256"]})
        finalize_rubrics(self.frq, plan["id"])
        with patch.object(self.frq.manual, "_source", wraps=self.frq.manual._source) as source:
            self.assertEqual(self.frq.assignment(plan["id"])["workflow"]["new_responses"], 2)
            self.assertEqual(source.call_count, 1)
            self.assertEqual(self.frq.assignment(plan["id"])["workflow"]["new_responses"], 2)
            self.assertEqual(source.call_count, 2)
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?", (self.job["message_id"],))
        workflow = self.frq.assignment(plan["id"])["workflow"]
        self.assertEqual(workflow["new_responses"], 0)
        self.assertGreaterEqual(workflow["blocked_responses"], 2)


class ManagedGradingLoadingTests(GradingLoadingTests):
    managed = True
