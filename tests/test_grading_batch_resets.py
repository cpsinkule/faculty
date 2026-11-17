"""Reset a batch to grade its responses again: nothing erased, no instructor decision withdrawn, reversible until regraded."""
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from rosters import connect
import test_frq_grading as grading_fixture
import test_grading_answer_groups as groups_fixture
from test_grading_answer_groups import BARE, WORKED


class BatchResetTests(unittest.TestCase):
    managed = False
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric

    def setUp(self):
        groups_fixture.AnswerGroupWorkflow.setUp(self)  # One question, two students with reviewed papers, an approved rubric.
        self.aid = self.plan["id"]

    def grade_all(self, lines=("R1",), selection="all", run=True):
        proposals = {tuple(WORKED): set(lines), tuple(BARE): set(lines)}
        self.enterContext(patch.object(self.frq, "provider", return_value=groups_fixture.ScriptedProvider([WORKED, BARE], proposals)))
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.qid], "mode": "local", "operation": "recognize", "selection": selection}
        preview = self.frq.preview(self.aid, command)
        job = self.frq.create_job(self.aid, {**command, "fingerprint": preview["fingerprint"]})
        for _ in range(60 if run else 0):
            if not self.frq.process_one():
                break
        return self.frq.job(job["id"]), preview

    def reset(self, job, **changes):
        outlook = self.frq.reset_outlook(job["id"])
        return self.frq.reset_job(job["id"], {"request_id": str(uuid.uuid4()), "fingerprint": outlook["fingerprint"], "confirmation": "reset", **changes})

    def states(self):
        return sorted(r["state"] for r in self.frq.responses(self.aid, self.qid))

    def counts(self, *tables):
        with connect(self.path) as db:
            return [db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables]

    def test_a_reset_withdraws_unreviewed_proposals_keeps_the_instructors_decision_and_erases_nothing(self):
        job, _ = self.grade_all()
        self.assertEqual((job["status"], job["reset"]), ("review_ready", {"resettable": 2, "latest": None}))
        reviewed, waiting = self.frq.responses(self.aid, self.qid)
        value = self.frq.response(reviewed["id"])
        self.frq.save_review(reviewed["id"], {"version": value["version"], "result_id": value["current_result_id"], "status": "approved", "confirm_review": True, "selected_items": ["R1"]})
        retained = self.counts("frq_results", "frq_steps", "frq_tasks", "frq_reviews")
        outlook = self.frq.reset_outlook(job["id"])
        self.assertEqual((outlook["withdraw"], outlook["kept_reviewed"], outlook["graded_later"], outlook["busy"], outlook["running"]), (1, 1, 0, 0, False))
        # The server asks for the confirmation and the exact preview, whatever a dialog showed.
        command = {"request_id": str(uuid.uuid4()), "fingerprint": outlook["fingerprint"]}
        with self.assertRaisesRegex(ValueError, "Confirm the reset"):
            self.frq.reset_job(job["id"], command)
        with self.assertRaisesRegex(Conflict, "Look at the reset again"):
            self.frq.reset_job(job["id"], {**command, "confirmation": "reset", "fingerprint": "stale"})
        after = self.frq.reset_job(job["id"], {**command, "confirmation": "reset"})
        self.assertEqual((after["reset"]["latest"]["withdrawn"], after["reset"]["latest"]["kept_reviewed"], after["reset"]["latest"]["ungraded"], after["reset"]["resettable"]), (1, 1, 1, 0))
        self.assertEqual(after["progress"]["awaiting_review"], 0)
        # The same request again is the same reset; a different command under that ID is refused.
        self.assertEqual(self.frq.reset_job(job["id"], {**command, "confirmation": "reset"})["reset"]["latest"]["id"], after["reset"]["latest"]["id"])
        with self.assertRaises(Conflict):
            self.frq.reset_job(job["id"], {**command, "confirmation": "reset", "fingerprint": "another"})
        self.assertEqual(self.counts("frq_job_resets", "frq_job_reset_responses"), [1, 1])
        # Nothing was erased, and the instructor's approved grade is untouched.
        self.assertEqual(self.counts("frq_results", "frq_steps", "frq_tasks", "frq_reviews"), retained)
        self.assertEqual(self.states(), ["approved", "pending"])
        self.assertEqual(self.frq.response(reviewed["id"])["review"]["status"], "approved")
        withdrawn = self.frq.response(waiting["id"])
        self.assertEqual((withdrawn["current_result_id"], withdrawn["result"]), (None, None))
        # Without a proposal there is nothing to review, approve or export for it.
        self.assertEqual(self.frq.review_queue(self.aid)["waiting"], 0)
        with self.assertRaises(Conflict):
            self.frq.save_review(waiting["id"], {"version": withdrawn["version"], "result_id": None, "status": "approved", "confirm_review": True, "selected_items": ["R1"]})
        with self.assertRaisesRegex(Conflict, "Nothing to reset"):
            self.reset(job)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_events WHERE kind='batch_reset'").fetchone()[0], 1)
            for statement in ("UPDATE frq_job_resets SET actor='x'", "DELETE FROM frq_job_resets", "DELETE FROM frq_job_reset_responses"):
                with self.assertRaises(Exception):
                    db.execute(statement)

    def test_reset_responses_are_new_work_for_the_next_batch_and_progress_counts_each_once(self):
        first, _ = self.grade_all(lines=("R1",))
        before = self.frq.assignment(self.aid)["workflow"]
        self.assertEqual((before["new_responses"], before["progress"]["grade"]), (0, {"completed": 2, "total": 2}))
        self.reset(first)
        workflow = self.frq.assignment(self.aid)["workflow"]
        self.assertEqual((workflow["new_responses"], workflow["review_responses"], workflow["progress"]["grade"], workflow["next_tab"]), (2, 0, {"completed": 0, "total": 2}, "jobs"))
        # "New responses" now means these two; another model team proposes something else for them.
        second, preview = self.grade_all(lines=("R2",), selection="new")
        self.assertEqual((len(preview["eligible"]), preview["existing_count"], second["status"]), (2, 0, "review_ready"))
        self.assertEqual(self.counts("frq_responses"), [2])  # The same responses, not new rows.
        proposals = [sorted(a["rubric_item_id"] for a in self.frq.response(r["id"])["result"]["applications"] if a["applied"]) for r in self.frq.responses(self.aid, self.qid)]
        self.assertEqual(proposals, [["R2"], ["R2"]])
        self.assertEqual(self.counts("frq_results"), [4])  # The first team's proposals are retained beside the new ones.
        self.assertEqual(self.frq.assignment(self.aid)["workflow"]["progress"]["grade"], {"completed": 2, "total": 2})
        # The first batch's reset has nothing left to put back, and a restore never replaces newer work.
        self.assertEqual(self.frq.job(first["id"])["reset"]["latest"]["ungraded"], 0)
        restored = self.frq.restore_job_reset(first["id"], {})
        self.assertEqual(restored["reset"]["latest"]["restored"], 0)
        self.assertEqual([self.frq.response(r["id"])["task"]["job_id"] for r in self.frq.responses(self.aid, self.qid)], [second["id"]] * 2)
        # The first batch no longer holds any current proposal; the second one can be reset in turn.
        with self.assertRaisesRegex(Conflict, "Nothing to reset"):
            self.reset(first)
        self.assertEqual(self.frq.reset_outlook(first["id"])["graded_later"], 2)
        self.assertEqual(self.reset(second)["reset"]["latest"]["withdrawn"], 2)

    def test_a_reset_is_put_back_while_nothing_newer_exists(self):
        job, _ = self.grade_all()
        before = [(r["id"], r["current_result_id"], r["state"]) for r in self.frq.responses(self.aid, self.qid)]
        self.reset(job)
        self.assertEqual(self.states(), ["pending", "pending"])
        restored = self.frq.restore_job_reset(job["id"], {})
        self.assertEqual((restored["reset"]["latest"]["restored"], restored["reset"]["latest"]["ungraded"], restored["reset"]["resettable"]), (2, 0, 2))
        self.assertEqual([(r["id"], r["current_result_id"], r["state"]) for r in self.frq.responses(self.aid, self.qid)], before)
        self.assertEqual(self.frq.assignment(self.aid)["workflow"]["new_responses"], 0)
        self.assertEqual(self.frq.restore_job_reset(job["id"], {})["reset"]["latest"]["restored"], 2)  # Asking again changes nothing.
        self.assertEqual(self.counts("frq_job_reset_restores"), [1])
        # Restored work can be reset once more: every reset is its own record.
        self.assertEqual(self.reset(job)["reset"]["latest"]["withdrawn"], 2)
        self.assertEqual(self.counts("frq_job_resets"), [2])

    def test_responses_a_cancelled_batch_never_proposed_for_are_new_work_again(self):
        # 2026-09-24: a 2,169-response batch ran out of file handles and was cancelled before proposing anything. Nothing retries a
        # cancelled batch and a reset has no proposal to withdraw, so "new responses" must take them, or they cannot be graded at all.
        job, _ = self.grade_all(run=False)
        self.frq.cancel_job(job["id"])
        self.assertEqual(self.frq.job(job["id"])["reset"]["resettable"], 0)
        workflow = self.frq.assignment(self.aid)["workflow"]
        self.assertEqual((workflow["new_responses"], workflow["progress"]["grade"]["total"]), (2, 2))  # Each counted once.
        again, preview = self.grade_all(selection="new")
        self.assertEqual((len(preview["eligible"]), preview["existing_count"], again["status"]), (2, 0, "review_ready"))
        self.assertEqual(self.counts("frq_responses"), [2])  # The same responses, not new rows.
        self.assertEqual(self.frq.assignment(self.aid)["workflow"]["new_responses"], 0)

    def test_a_response_that_failed_in_a_batch_still_standing_is_that_batchs_to_retry(self):
        job, _ = self.grade_all(run=False)
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET status='failed' WHERE job_id=?", (job["id"],))
            db.execute("UPDATE frq_jobs SET status='failed' WHERE id=?", (job["id"],))
        self.assertEqual(self.frq.assignment(self.aid)["workflow"]["new_responses"], 0)
        with self.assertRaisesRegex(ValueError, "must contain responses"):  # Nothing new: the failed ones are not taken.
            self.grade_all(selection="new")

    def test_a_batch_that_is_still_working_is_not_reset(self):
        job, _ = self.grade_all(run=False)
        self.assertEqual(job["status"], "pending")
        outlook = self.frq.reset_outlook(job["id"])
        self.assertTrue(outlook["running"])
        with self.assertRaisesRegex(Conflict, "still working"):
            self.frq.reset_job(job["id"], {"request_id": str(uuid.uuid4()), "fingerprint": outlook["fingerprint"], "confirmation": "reset"})


if __name__ == "__main__":
    unittest.main()
