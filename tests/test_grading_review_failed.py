"""Review lists the responses that failed to grade (2026-09-23): no proposal exists for them, so no question's queue can.

Per batch, with their reasons and questions, never by student; a retry is the batch's own action. Every batch's failures are listed
(GRADING "Failed to grade": the response's LATEST attempt failed), so a newer batch never hides an older one's (2026-10-02)."""
import unittest

from fake_openai import default_answer
from rosters import connect
from workspace_overview import failed_to_grade
import test_grading_cloud as cloud_fixture


class FailedToGrade(cloud_fixture.CloudCase):
    def add_question(self):
        plan = self.frq.assignment(self.plan["id"])
        plan = self.frq.save_question(plan["id"], {"version": plan["version"], "question_key": "q2", "assignment_version_id": self.version_id,
            "content": {**plan["questions"][0]["content"], "title": "Question 2"}})
        question = next(q for q in plan["questions"] if q["question_key"] == "q2")
        plan = self.frq.import_rubric(plan["id"], question["question_id"], {"version": question["version"], "source_text": "-1 Arithmetic error",
            "rules": plan["questions"][0]["rubric"]["rules"]})
        question = next(q for q in plan["questions"] if q["question_id"] == question["question_id"])
        self.plan = self.frq.approve_rubric(plan["id"], question["question_id"], {"version": question["version"], "rubric_id": question["rubric_id"],
            "sha256": question["rubric"]["sha256"]})
        return question["question_id"]

    def test_every_batchs_failures_are_listed_by_batch(self):
        self.host.script = lambda body, host: ("status", 402)
        old = self.queue()
        self.run_all()
        second_question = self.add_question()
        current = self.queue(questions=[second_question])
        # A newer batch (here another question's, as an automatic batch for late papers would be) hides nothing: the old response's
        # latest attempt failed, so it is listed with its own batch while the new one is still queued.
        queue = self.frq.review_queue(self.plan["id"])
        self.assertEqual(queue["failed"]["total"], 1)
        self.assertEqual([b["job_id"] for b in queue["failed"]["batches"]], [old["id"]])
        self.assertEqual({q["question_id"]: q["failed"] for q in queue["questions"]}, {self.qid: 1, second_question: 0})
        self.run_all()
        queue = self.frq.review_queue(self.plan["id"])
        self.assertEqual(queue["failed"]["total"], 2)
        self.assertEqual([b["job_id"] for b in queue["failed"]["batches"]], [current["id"], old["id"]])  # newest first
        self.assertEqual({q["question_id"]: q["failed"] for q in queue["questions"]}, {self.qid: 1, second_question: 1})
        # The Review page, the Grade step's workflow and Home's card count the same failures.
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["failed"], 2)
        with connect(self.path) as db:
            self.assertEqual(failed_to_grade(db, [self.plan["id"]]), {self.plan["id"]: 2})
        # A batch's own queue lists only its own failures.
        scoped = self.frq.review_queue(self.plan["id"], job_id=current["id"])
        self.assertEqual(([b["job_id"] for b in scoped["failed"]["batches"]], scoped["failed"]["total"]), ([current["id"]], 1))
        # History is untouched. Retrying the current batch does not retry the old one, which stays listed.
        self.frq.retry_job(current["id"])
        queue = self.frq.review_queue(self.plan["id"])
        self.assertEqual(([b["job_id"] for b in queue["failed"]["batches"]], queue["failed"]["total"]), ([old["id"]], 1))
        self.assertEqual(self.frq.job(old["id"])["tasks"][0]["status"], "failed")

    def test_a_cancelled_batchs_failures_are_new_work_not_failures(self):
        self.host.script = lambda body, host: ("status", 402)
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.review_queue(self.plan["id"])["failed"]["total"], 1)
        self.frq.cancel_job(job["id"])  # Nothing retries a cancelled batch: its responses are the next batch's new work.
        queue = self.frq.review_queue(self.plan["id"])
        self.assertEqual(queue["failed"], {"total": 0, "batches": []})
        self.assertEqual((queue["questions"][0]["failed"], queue["questions"][0]["not_graded"]), (0, 1))
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["failed"], 0)

    def test_a_failed_response_is_listed_with_its_reason_until_a_retry_grades_it(self):
        self.host.script = lambda body, host: ("status", 402)
        job = self.queue()
        self.run_all()
        queue = self.frq.review_queue(self.plan["id"])
        [question] = queue["questions"]
        # Failed is not "not graded yet": it will not grade itself, and the question is not all reviewed either.
        self.assertEqual((question["failed"], question["not_graded"], question["waiting"]), (1, 0, 0))
        [batch] = queue["failed"]["batches"]
        self.assertEqual((queue["failed"]["total"], batch["job_id"], batch["count"], batch["cancelled"]), (1, job["id"], 1, False))
        self.assertEqual(batch["questions"], [{"question_id": self.qid, "count": 1}])
        self.assertRegex(batch["reasons"][0]["text"], "no credits")
        self.assertEqual(batch["compute"], "cloud_standard")
        # Nothing about the student travels with the list.
        self.assertNotIn("enrollment", str(queue["failed"]).lower())
        self.assertNotIn("response_id", str(queue["failed"]))
        # The batch's own retry grades it; afterwards it is an ordinary proposal and the list is empty.
        self.host.script = lambda body, host: default_answer(body)
        retried = self.frq.retry_job(job["id"])
        self.assertEqual(retried["retry"]["retried"], 1)
        self.run_all()
        queue = self.frq.review_queue(self.plan["id"])
        self.assertEqual((queue["failed"]["total"], queue["questions"][0]["failed"]), (0, 0))
        self.assertEqual(queue["questions"][0]["waiting"] + queue["questions"][0]["approved"], 1)

    def test_a_failed_regrade_of_a_graded_response_is_not_a_failure_to_grade(self):
        first = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(first["id"])["tasks"][0]["status"], "complete")
        # A later batch fails, but the response keeps the proposal the first one gave it: it is reviewed as usual.
        self.host.script = lambda body, host: ("status", 402)
        second = self.queue(selection="all")
        self.run_all()
        self.assertEqual(self.frq.job(second["id"])["tasks"][0]["status"], "failed")
        queue = self.frq.review_queue(self.plan["id"])
        self.assertEqual(queue["failed"]["total"], 0)
        self.assertEqual(queue["questions"][0]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
