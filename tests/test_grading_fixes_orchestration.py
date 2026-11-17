"""Regressions for the cloud lane's orchestration, against the local fake host: the dispatcher's wake-up, retries that never replace
newer work, durable-call recovery, the absent spending cap, the direct-reading
estimate and restoring an older reset. No credentials and no network."""
import json
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from course_storage import StorageError
from grading import config
from grading.cloud import RetryLater
from grading.providers.base import Cancelled, ProviderError
from grading.providers.cloud import DedicatedGpuProvider
from rosters import connect
import test_grading_batch_resets as reset_fixture
import test_grading_cloud as cloud_fixture
from test_grading_cloud import endpoint
from fake_openai import default_answer


class Lane(cloud_fixture.CloudCase):
    def second_student(self):
        with connect(self.path) as db:
            second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        self.reviewed_source(batch=self.ready_batch(enrollment_id=second), changed="second")

    def tasks(self, job):
        return self.frq.job(job["id"])["tasks"]


class Dispatcher(Lane):
    def test_a_wake_up_left_set_never_spins_the_dispatcher(self):
        """`inbox.wake` is set in some twenty places and was never cleared: after the first set() every wait returned at once."""
        rounds, stop = [], threading.Event()
        def round_(halt):
            rounds.append(time.monotonic())
            if len(rounds) > 500:
                stop.set()  # Spinning: end the thread instead of the test.
            return False
        with patch.object(self.frq, "_cloud_rounds", new=round_):
            self.frq.inbox.wake.set()  # Set earlier by any setter, with work that the first round already saw.
            worker = threading.Thread(target=self.frq.cloud_worker, args=(stop,), daemon=True)
            worker.start()
            time.sleep(.6)
            settled = len(rounds)
            self.frq.inbox.wake.set()  # A real wake-up still ends the wait at once.
            time.sleep(.3)
            woken = len(rounds)
            stop.set()
            self.frq.inbox.wake.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual((settled, woken), (2, 3))  # The first round, one for the stale wake-up, then one per real wake-up.

    def test_a_lane_another_process_holds_in_storage_mode_is_not_a_crash(self):
        """`worker --once` beside the running service: the storage manager reports its busy lock as a StorageError."""
        class Storage:
            def __init__(self, busy):
                self.busy = busy

            @contextmanager
            def lock(self, identifier, blocking=True):
                if self.busy:
                    try:
                        raise BlockingIOError()
                    except BlockingIOError as error:
                        raise StorageError("This course is being backed up or moved. Please try again when it finishes.") from error
                raise StorageError("The NVMe course path is redirected. No data was removed.")
                yield
        with patch.object(self.frq.inbox, "storage", Storage(busy=True)):
            self.assertFalse(self.frq.process_cloud())  # The service's own dispatcher does this work.
        with patch.object(self.frq.inbox, "storage", Storage(busy=False)), self.assertRaisesRegex(StorageError, "redirected"):
            self.frq.process_cloud()  # Any other storage failure stays what it is.

    def test_what_is_learnt_about_an_interrupted_request_is_kept_when_the_send_is_refused(self):
        job = self.queue()
        with connect(self.path) as db:
            task = db.execute("SELECT * FROM frq_tasks WHERE job_id=?", (job["id"],)).fetchone()
            db.execute("""INSERT INTO frq_cloud_calls(id,task_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,lineage,request_sha256,prompt,state,submitted_at)
                VALUES('lost',?,?,'reconciling',0,'reader','cloud_standard','alpha-vision','hosted','fixture/alpha-vision','alpha','sha','reconcile','submitted','2026-09-01T00:00:00+00:00')""",
                (task["id"], job["id"]))
            db.execute("UPDATE frq_jobs SET cancel_requested=1 WHERE id=?", (job["id"],))  # A check below the recovery refuses to send.
        with self.assertRaisesRegex(Cancelled, "cancelled"):  # Its own kind since 2026-09-23: no retry policy or optional step swallows it.
            self.frq.cloud_call({"task_id": task["id"], "job_id": job["id"], "step": "reconciling", "task_attempt": 0, "tier": "cloud_standard"}, "reader",
                                config.named_endpoint(self.frq.settings()["settings"], "alpha-vision"), {"prompt": "reconcile", "payload": {}, "images": []}, job["profile"])
        self.assertEqual(self.calls("id='lost'")[0]["state"], "unknown")  # Not rolled back to 'submitted', where it counted as in flight.
        self.assertEqual(self.host.requests, [])


class Retry(Lane):
    def test_retrying_an_older_batch_never_replaces_a_newer_grade_and_retries_the_rest(self):
        self.second_student()
        self.host.script = lambda body, host: ("status", 500)
        older = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(older["id"])["progress"]["failed"], 2)
        first, second = [t["response_id"] for t in self.tasks(older)]
        self.host.script = lambda body, host: default_answer(body)
        newer = self.queue(response_ids=[first])  # A failed task is not pending work, so a new batch may take its response.
        self.run_all()
        self.assertEqual(self.frq.job(newer["id"])["status"], "review_ready")
        graded = self.frq.response(first)["current_result_id"]
        retried = self.frq.retry_job(older["id"])
        self.assertEqual(retried["retry"]["retried"], 1)
        skipped, = retried["retry"]["skipped"]
        self.assertEqual(skipped["response_id"], first)
        self.assertIn("later batch has graded this response", skipped["reason"])
        self.run_all()
        self.assertEqual(self.frq.response(first)["current_result_id"], graded)  # The newer proposal stands.
        self.assertEqual(self.frq.response(second)["task"]["job_id"], older["id"])
        with self.assertRaisesRegex(Conflict, "No failed response of this batch can be retried. A later batch"):
            self.frq.retry_job(older["id"])

    def test_one_response_that_cannot_be_retried_does_not_hold_back_the_others(self):
        self.second_student()
        self.host.script = lambda body, host: ("status", 500)
        job = self.queue()
        self.run_all()
        capped, other = self.tasks(job)
        with connect(self.path) as db:
            db.execute("UPDATE frq_tasks SET attempt=3 WHERE id=?", (capped["id"],))  # max_retries is 3 in this fixture.
        self.host.script = lambda body, host: default_answer(body)
        retried = self.frq.retry_job(job["id"])
        self.assertEqual((retried["retry"]["retried"], [s["task_id"] for s in retried["retry"]["skipped"]]), (1, [capped["id"]]))
        self.assertIn("retry limit", retried["retry"]["skipped"][0]["reason"])
        self.run_all()
        self.assertEqual({t["id"]: t["status"] for t in self.tasks(job)}, {capped["id"]: "failed", other["id"]: "complete"})

    def test_a_context_change_while_scoring_leaves_a_task_that_a_retry_can_finish(self):
        job = self.queue(mode="local")
        task = lambda: self.tasks(job)[0]
        for _ in range(40):
            if task()["stage"] == "scoring":
                break
            self.frq.process_one()
        self.assertEqual((task()["stage"], task()["status"]), ("scoring", "pending"))
        execute, validate, ran = self.frq.execute_stage, self.frq.validate_task, []
        def executed(task, job):
            output = execute(task, job)
            ran.append(task["stage"])
            return output
        def changed(db, task):
            if ran and ran[-1] == "scoring":
                raise Conflict("Question or rubric changed; create a new grading run.")
            return validate(db, task)
        with patch.object(self.frq, "execute_stage", new=executed), patch.object(self.frq, "validate_task", new=changed):
            for _ in range(10):
                if task()["status"] != "pending":
                    break
                self.frq.process_one()
        self.assertEqual((task()["stage"], task()["status"]), ("scoring", "failed"))  # Never 'complete', which no retry can run.
        self.assertEqual(self.frq.retry_job(job["id"])["retry"], {"retried": 1, "skipped": []})
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        self.assertIsNotNone(self.frq.response(task()["response_id"])["current_result_id"])

    def test_a_task_an_earlier_version_left_failed_at_complete_scores_again(self):
        job = self.queue(mode="local")
        for _ in range(40):
            if self.tasks(job)[0]["stage"] == "scoring":
                break
            self.frq.process_one()
        with connect(self.path) as db:  # What the earlier code left behind after a context change during scoring: no result, stage 'complete'.
            db.execute("UPDATE frq_tasks SET stage='complete',status='failed',error='Question or rubric changed; create a new grading run.' WHERE job_id=?", (job["id"],))
        self.frq.retry_job(job["id"])
        self.assertEqual(self.tasks(job)[0]["stage"], "scoring")
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")


class DurableRecovery(Lane):
    def setUp(self):
        super().setUp()
        self.configure([endpoint(self.host, "gpu-reader", kind="dedicated", profile="small")], dedicated_enabled=True)

    def test_a_job_the_host_no_longer_holds_is_an_unknown_outcome_resent_under_that_policy(self):
        job = self.queue()
        call = self.run_until_submitted()
        del self.host.jobs[call["provider_call_id"]]  # Purged: asking about it answers 404.
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        lost, resent = self.calls("step='reconciling'")
        # Settled as unknown, resent once, and superseded by the answer that was applied; never asked about again.
        self.assertEqual((lost["state"], json.loads(lost["receipt_json"])["abandoned"], resent["state"]), ("superseded", True, "completed"))
        self.assertEqual(sum(r["path"].endswith("/run") for r in self.host.requests), 3)  # The reading twice, then the grade.

    def test_a_host_that_cannot_be_asked_is_asked_a_bounded_number_of_times(self):
        def failing(provider, call_id, metadata=None):
            raise ProviderError("Endpoint request failed (HTTP 500).", confirmed=True, status=500)
        with patch("grading.cloud.POLL_FAILURES", 3), patch.object(DedicatedGpuProvider, "poll", new=failing):
            job = self.queue()
            self.run_all(turns=200)
        task = self.tasks(job)[0]
        self.assertEqual(task["status"], "failed")
        self.assertIn("unknown after 2 automatic resends", task["error"])
        calls = self.calls("step='reconciling'")
        self.assertEqual([(c["state"], json.loads(c["receipt_json"])["poll_failures"]) for c in calls], [("unknown", 3)] * 3)

    def test_a_job_the_host_cancels_on_its_own_counts_toward_the_attempt_cap(self):
        with patch.object(DedicatedGpuProvider, "poll", new=lambda provider, call_id, metadata=None: {"state": "cancelled", "confirmation": {"job_status": "CANCELLED"}}):
            job = self.queue()
            self.run_all(turns=200)
        task = self.tasks(job)[0]
        self.assertEqual(task["status"], "failed")
        self.assertIn("attempt cap", task["error"])
        self.assertEqual([c["state"] for c in self.calls("step='reconciling'")], ["failed"] * 3)

    def test_a_finished_job_whose_answer_is_refused_is_a_confirmed_failure_with_its_receipt(self):
        job = self.queue()
        call = self.run_until_submitted()
        self.host.jobs[call["provider_call_id"]]["output"] = {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 1}}
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        refused, resent = self.calls("step='reconciling'")
        self.assertEqual((refused["state"], json.loads(refused["confirmation_json"])["malformed"], resent["state"]), ("failed", True, "completed"))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rejected_outputs WHERE stage='reconciling'").fetchone()[0], 1)


class Spending(Lane):
    def owner(self, job):
        task = self.tasks(job)[0]
        return {"task_id": task["id"], "job_id": job["id"], "step": "reconciling", "task_attempt": 0, "tier": "cloud_standard"}

    def record(self, job, state, cost=None, count=1):
        with connect(self.path) as db:
            task = db.execute("SELECT id FROM frq_tasks WHERE job_id=?", (job["id"],)).fetchone()[0]
            for _ in range(count):
                db.execute("""INSERT INTO frq_cloud_calls(id,task_id,job_id,step,task_attempt,role,tier,endpoint,provider,requested_model,lineage,request_sha256,
                    prompt,state,cost,submitted_at) VALUES(?,?,?,'elsewhere',0,'primary','cloud_standard','alpha-vision','hosted','fixture/alpha-vision','alpha','sha','grade',?,?,?)""",
                    (str(uuid.uuid4()), task, job["id"], state, cost, "2026-09-22T00:00:00+00:00"))

    def test_no_spending_cap_holds_a_batch_back(self):
        # Requirement, 2026-09-23: the OpenRouter and Vercel accounts carry the spending limits. However much a batch has cost, and
        # however many of its requests are still in flight, the next one is sent.
        self.configure([endpoint(self.host, "alpha-vision", price_in=1.0, price_out=2.0)])
        job = self.queue()
        self.record(job, "submitted", count=3)
        with connect(self.path) as db:
            db.execute("UPDATE frq_jobs SET cost=5000 WHERE id=?", (job["id"],))
        e = config.named_endpoint(job["profile"], "alpha-vision")
        self.frq.cloud_call(self.owner(job), "reader", e, {"prompt": "reconcile", "payload": {}, "images": []}, job["profile"])
        self.assertEqual(len(self.host.requests), 1)
        with connect(self.path) as db:
            db.execute("UPDATE frq_cloud_calls SET state='completed',cost=0 WHERE step='elsewhere'")
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")

    def test_a_remote_connection_without_prices_has_an_unknown_estimate_and_still_starts(self):
        remote = {**endpoint(self.host, "alpha-vision"), "base_url": "https://models.example.org/v1"}
        settings = {**self.frq.settings()["settings"], "endpoints": [remote]}
        unknown = self.frq.estimate(settings, "cloud_standard", 4)
        self.assertIsNone(unknown["estimated_cost"])
        self.assertIn("No prices are configured for alpha-vision", unknown["basis"])
        self.assertEqual(self.frq.estimate({**settings, "cost_per_response": .05}, "cloud_standard", 4)["estimated_cost"], .2)
        self.assertEqual(self.frq.estimate(self.frq.settings()["settings"], "cloud_standard", 4)["estimated_cost"], 0)  # A loopback host is fully local.
        self.configure([remote])
        job = self.queue()  # Unknown is said, not refused: there is no cap for it to protect (2026-09-23).
        self.assertIn(job["estimated_cost"], (None, 0))

    def test_direct_reading_is_priced_one_request_per_answer_area_and_nothing_else_changes(self):
        settings = {**self.frq.settings()["settings"], "endpoints": [endpoint(self.host, "alpha-vision", price_in=1.0, price_out=2.0)]}
        plain = self.frq.estimate(settings, "cloud_standard", 10, crops=3)
        direct = self.frq.estimate({**settings, "direct_reading": True}, "cloud_standard", 10, crops=3)
        self.assertNotIn("direct_reading", plain["roles"])
        self.assertEqual({k: v for k, v in direct["roles"].items() if k != "direct_reading"}, plain["roles"])
        self.assertEqual(direct["roles"]["direct_reading"]["requests"], 3)
        self.assertAlmostEqual(direct["roles"]["direct_reading"]["cost"], 3 * (1500 * 1.0 + 500 * 2.0) / 1e6)
        self.assertAlmostEqual(direct["estimated_cost"] - plain["estimated_cost"], 10 * 3 * (1500 + 1000) / 1e6, places=4)
        self.assertEqual(self.frq.estimate({**settings, "direct_reading": True}, "cloud_standard", 10)["roles"]["direct_reading"]["requests"], 1)


class OlderReset(unittest.TestCase):
    """Reset A, B grades, reset B, restore A: A's older grade must not come back while B's newer one exists."""
    managed = False
    Base = reset_fixture.BatchResetTests
    document_version, ready_batch, reviewed_source, configure_rubric = Base.document_version, Base.ready_batch, Base.reviewed_source, Base.configure_rubric
    setUp, grade_all, reset, states = Base.setUp, Base.grade_all, Base.reset, Base.states

    def test_restoring_an_older_reset_never_brings_back_an_older_grade(self):
        first, _ = self.grade_all(lines=("R1",))
        self.reset(first)
        second, _ = self.grade_all(lines=("R2",), selection="new")
        self.reset(second)
        self.assertEqual(self.frq.job(first["id"])["reset"]["latest"]["ungraded"], 0)  # Nothing of it may be put back now.
        restored = self.frq.restore_job_reset(first["id"], {})
        self.assertEqual((restored["reset"]["latest"]["restored"], self.states()), (0, ["pending", "pending"]))
        # The newer batch's own reset is what puts proposals back.
        self.assertEqual(self.frq.restore_job_reset(second["id"], {})["reset"]["latest"]["restored"], 2)
        proposals = [sorted(a["rubric_item_id"] for a in self.frq.response(r["id"])["result"]["applications"] if a["applied"]) for r in self.frq.responses(self.aid, self.qid)]
        self.assertEqual(proposals, [["R2"], ["R2"]])


if __name__ == "__main__":
    unittest.main()
