"""A changed rubric invalidates its grades, and the next batch grades them again (requirement, 2026-09-25).

"If a rubric is edited, it invalidates the grading and it has to be rerun with the updated rubric." A grade made under an earlier
rubric cannot be decided, finalized or exported, waits for no review, and is new work for the next batch. Nothing is erased, and
an instructor's earlier decision stays in the history while the regraded proposal waits for them.
"""
import unittest
import uuid

from werkzeug.exceptions import Conflict

from frq_fixtures import finalize_rubrics
import test_grading_batch_resets as resets


class RubricInvalidationTests(unittest.TestCase):
    managed = False
    document_version = resets.BatchResetTests.document_version  # The reset tests' fixture: one question, two reviewed papers.
    ready_batch = resets.BatchResetTests.ready_batch
    reviewed_source = resets.BatchResetTests.reviewed_source
    configure_rubric = resets.BatchResetTests.configure_rubric
    setUp = resets.BatchResetTests.setUp
    grade_all = resets.BatchResetTests.grade_all
    states = resets.BatchResetTests.states
    counts = resets.BatchResetTests.counts

    def edit_rubric(self, item="R3", points=3):
        q = self.frq.assignment(self.aid)["questions"][0]
        self.frq.edit_rubric(self.aid, self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"],
                                                  "lines": [{"item_id": item, "points": points}]})

    def approve(self, rid, lines):
        value = self.frq.response(rid)
        return self.frq.save_review(rid, {"version": value["version"], "result_id": value["current_result_id"], "status": "approved",
                                          "confirm_review": True, "selected_items": list(lines)})

    def test_an_edited_rubric_sends_every_grade_made_under_the_old_one_back_to_grading(self):
        first, _ = self.grade_all(lines=("R1",))
        reviewed, waiting = self.frq.responses(self.aid, self.qid)
        self.approve(reviewed["id"], ["R1"])
        self.edit_rubric()
        finalize_rubrics(self.frq, self.aid)
        # Neither grade counts any more, the instructor's approval included, and neither waits for review.
        self.assertEqual(self.states(), ["stale", "stale"])
        workflow = self.frq.assignment(self.aid)["workflow"]
        self.assertEqual((workflow["new_responses"], workflow["regrade_responses"], workflow["review_responses"], workflow["failed"],
                          workflow["progress"]["grade"], workflow["next_tab"]), (2, 2, 0, 0, {"completed": 0, "total": 2}, "jobs"))
        queue = self.frq.review_queue(self.aid)
        question, = queue["questions"]
        self.assertEqual((queue["waiting"], question["waiting"], question["regrade"], question["groups"]), (0, 0, 2, []))
        self.assertEqual(self.frq.review_queue(self.aid, first["id"])["waiting"], 0)
        self.assertEqual(self.frq.job(first["id"])["progress"]["awaiting_review"], 0)
        with self.assertRaisesRegex(Conflict, "changed after this was graded"):
            self.approve(waiting["id"], ["R1"])
        with self.assertRaisesRegex(Conflict, "no current approved responses"):
            self.frq.export(self.aid, "audit")
        # The batch dialog's first choice grades both again: the same rows, and nothing earlier is erased.
        second, preview = self.grade_all(lines=("R2",), selection="new")
        self.assertEqual((len(preview["eligible"]), preview["existing_count"], second["status"]), (2, 0, "review_ready"))
        self.assertEqual(self.counts("frq_responses", "frq_results", "frq_reviews"), [2, 4, 1])
        # The earlier approval gives way to the new proposal and stays in the history; the response waits for the instructor.
        after = {r["id"]: r for r in self.frq.responses(self.aid, self.qid)}
        self.assertEqual(after[reviewed["id"]]["earlier_review_status"], "approved")
        self.assertNotIn(after[reviewed["id"]]["state"], ("stale", "approved", "manual", "auto_accepted", "pending"))
        card = self.frq.response(reviewed["id"])
        self.assertEqual((card["review"], card["earlier_review"]["status"], len(card["history"]), card["current"]), (None, "approved", 1, True))
        workflow = self.frq.assignment(self.aid)["workflow"]
        self.assertEqual((workflow["new_responses"], workflow["regrade_responses"], workflow["review_responses"]), (0, 0, 2))
        self.assertEqual(self.frq.review_queue(self.aid)["waiting"], 2)
        self.approve(reviewed["id"], ["R2"])
        self.assertEqual(self.frq.response(reviewed["id"])["review"]["status"], "approved")
        self.assertIn("approved", self.states())
        # Graded under today's rubric, nothing is new work any more.
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": "recognize", "selection": "new"}
        again = self.frq.preview(self.aid, command)
        self.assertEqual((len(again["eligible"]), again["existing_count"]), (0, 2))

    def test_a_rubric_edited_while_its_batch_runs_leaves_new_work_not_a_failure_to_retry(self):
        job, _ = self.grade_all(run=False)
        self.edit_rubric()
        for _ in range(60):
            if not self.frq.process_one():
                break
        job = self.frq.job(job["id"])
        self.assertEqual(job["progress"]["failed"], 2)
        with self.assertRaisesRegex(Conflict, "changed after this was graded"):
            self.frq.retry_job(job["id"])  # Its batch cannot grade them: the rubric it was given is gone.
        queue = self.frq.review_queue(self.aid)
        self.assertEqual((queue["failed"]["total"], queue["questions"][0]["regrade"]), (0, 2))
        # Nothing is ready to grade until the changed rubric's finalization is confirmed again; the Grade step says where to go.
        workflow = self.frq.assignment(self.aid)["workflow"]
        self.assertEqual((workflow["new_responses"], workflow["failed"], workflow["next_tab"]), (0, 0, "answer-key"))
        finalize_rubrics(self.frq, self.aid)
        workflow = self.frq.assignment(self.aid)["workflow"]
        self.assertEqual((workflow["new_responses"], workflow["regrade_responses"], workflow["failed"], workflow["next_tab"]), (2, 2, 0, "jobs"))
        second, preview = self.grade_all(selection="new")
        self.assertEqual((len(preview["eligible"]), preview["existing_count"], second["status"]), (2, 0, "review_ready"))
        self.assertEqual(self.counts("frq_responses"), [2])

    def test_work_under_way_is_never_taken_twice(self):
        self.grade_all(lines=("R1",))
        self.edit_rubric()
        finalize_rubrics(self.frq, self.aid)
        running, preview = self.grade_all(selection="new", run=False)
        self.assertEqual(len(preview["eligible"]), 2)
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": "recognize", "selection": "new"}
        self.assertEqual(len(self.frq.preview(self.aid, command)["eligible"]), 0)
        self.assertEqual(self.frq.assignment(self.aid)["workflow"]["regrade_responses"], 0)


if __name__ == "__main__":
    unittest.main()
