"""Grading batches that start by themselves (requirement, 2026-09-28): new work settles, then the dialog's default batch starts."""
import json
import unittest
from unittest.mock import patch

from rosters import connect
import test_frq_grading as fixture


class AutoGradeTests(unittest.TestCase):
    managed = False
    setUp = fixture.FrqWorkflowTests.setUp
    document_version = fixture.FrqWorkflowTests.document_version
    ready_batch = fixture.FrqWorkflowTests.ready_batch
    reviewed_source = fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = fixture.FrqWorkflowTests.configure_rubric
    queue = fixture.FrqWorkflowTests.queue
    finish = fixture.FrqWorkflowTests.finish

    def jobs(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("SELECT id,request_id,status,mode,operation FROM frq_jobs WHERE assignment_id=? ORDER BY created_at,rowid",
                                                 (self.plan["id"],))]

    def settle(self, turns=2):
        """Look, then start: with no settling time the second turn starts the batch the first one found."""
        started = False
        with patch("grading.auto_grade.SETTLE", 0), patch("grading.auto_grade.LOOK_EVERY", 0):
            for _ in range(turns):
                started = self.frq.auto_grade_one() or started
        return started

    def save_settings(self, **changes):
        current = self.frq.settings()
        return self.frq.save_settings({"version": current["version"], "settings": {**current["settings"], **changes}})

    def test_new_work_starts_the_default_batch_once_it_settles(self):
        # The first look only finds the work; the batch waits until it has stayed the same for the settling time.
        self.assertFalse(self.frq.auto_grade_one())
        self.assertEqual(self.jobs(), [])
        self.assertTrue(self.settle())
        [job] = self.jobs()
        self.assertTrue(job["request_id"].startswith("auto:"))
        self.assertEqual((job["mode"], job["operation"]), ("local", "recognize"))  # Compute settings' default mode, reading and grading.
        detail = self.frq.job(job["id"])
        self.assertTrue(detail["automatic"])
        self.assertNotIn("auto_grade", detail["profile"])  # Who started it is no part of the batch.
        with connect(self.path) as db:
            actor = db.execute("SELECT actor FROM frq_events WHERE kind='job_queued'").fetchone()[0]
        self.assertEqual(actor, "automatic")
        # While it runs nothing else starts, and once it has graded everything there is nothing new to start.
        self.assertFalse(self.settle())
        self.finish(job)
        self.assertFalse(self.settle(3))
        self.assertEqual(len(self.jobs()), 1)
        self.assertTrue(self.frq.assignment(self.plan["id"])["auto_grade"]["enabled"])

    def test_a_student_named_on_a_copy_brings_the_next_look_forward(self):
        # Requirement, 2026-11-06: a paper named by hand after its scan went on to grading is graded without waiting for the 15-minute look.
        with connect(self.path) as db:
            before = self.frq._auto_grade_marker(db, self.plan["id"])
            copy = db.execute("""SELECT c.id FROM printed_copies c JOIN exam_event_batches eb ON eb.batch_id=c.print_batch_id
                                 JOIN frq_assignments a ON a.event_id=eb.event_id WHERE a.id=? LIMIT 1""", (self.plan["id"],)).fetchone()[0]
        self.app.extensions["documents"].assign_copy(copy, None, "Named again by the instructor.")
        with connect(self.path) as db:
            self.assertNotEqual(self.frq._auto_grade_marker(db, self.plan["id"]), before)

    def test_the_switch_and_a_cancelled_batch_leave_the_next_batch_to_the_instructor(self):
        self.save_settings(auto_grade=False)
        self.assertFalse(self.settle())
        self.assertEqual(self.jobs(), [])
        state = self.frq.assignment(self.plan["id"])["auto_grade"]
        self.assertEqual((state["enabled"], state["paused"]), (False, None))
        self.save_settings(auto_grade=True)
        self.assertTrue(self.settle())
        [job] = self.jobs()
        self.frq.cancel_job(job["id"])
        # Cancelled: its responses are new work again, yet nothing starts until the instructor starts a batch.
        self.assertFalse(self.settle(3))
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(self.frq.assignment(self.plan["id"])["auto_grade"]["paused"], "cancelled")
        manual, _ = self.queue()
        self.assertIsNone(self.frq.assignment(self.plan["id"])["auto_grade"]["paused"])
        self.finish(manual)
        self.assertFalse(self.settle(3))
        self.assertEqual(len(self.jobs()), 2)

    def test_a_batch_that_cannot_start_is_recorded_once_with_its_reason(self):
        self.save_settings(default_tier="cloud_standard", tiers={"cloud_standard": {"reader": "", "primary": "", "second": "", "verifier": "", "flag_reviewer": ""}})
        self.assertFalse(self.settle(4))
        self.assertEqual(self.jobs(), [])
        with connect(self.path) as db:
            refused = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='automatic_batch_not_started'")]
        self.assertEqual(len(refused), 1)
        state = self.frq.assignment(self.plan["id"])["auto_grade"]
        self.assertTrue(state["not_started"]["reason"])
        self.assertEqual(state["not_started"]["responses"], 1)
        # A change of settings is new ground: the local default starts the batch.
        self.save_settings(default_tier="local")
        self.assertTrue(self.settle())
        self.assertEqual(len(self.jobs()), 1)
        self.assertIsNone(self.frq.assignment(self.plan["id"])["auto_grade"]["not_started"])


if __name__ == "__main__":
    unittest.main()
