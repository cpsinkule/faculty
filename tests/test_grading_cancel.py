"""Cancelling stops what is in flight (requirement, 2026-09-23). A streamed answer is a blocking read with no timeout, so the dispatcher
closes the connection of every request whose batch, rubric review or rubric draft was cancelled."""
import threading
import time
import unittest
import uuid

from werkzeug.exceptions import Conflict

from grading.cloud import Limiter
from grading.providers.base import Abort, Cancelled, abortable, request_stream
from rosters import connect
from fake_openai import FakeHost, default_answer, payload_of, prompt_name, survey_answer, write_answer
from test_exam_grading import HEADERS
import test_grading_cloud as cloud_fixture
import test_grading_rubric_drafts as drafts_fixture

THINKING = 60  # far longer than any test waits: only a closed connection ends it


def wait_for(condition, seconds=10):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if condition():
            return True
        time.sleep(.05)
    return False


class Connections(unittest.TestCase):
    def test_stopping_closes_a_stream_the_model_is_still_writing(self):
        with FakeHost(lambda body, host: ("thinking", THINKING, {"ok": True})) as host:
            abort, outcome = Abort(), {}

            def ask():
                try:
                    with abortable(abort):
                        request_stream(f"{host.url}/chat/completions", body={"model": "fixture/alpha", "messages": [{"role": "system", "content": "test"}, {"role": "user", "content": "{}"}]})
                except Cancelled as stopped:
                    outcome["stopped"] = stopped
            reader = threading.Thread(target=ask)
            reader.start()
            self.assertTrue(wait_for(lambda: host.requests), "the request never reached the host")
            time.sleep(.3)  # the model is thinking
            began = time.monotonic()
            abort.stop("Cancelled in the test.")
            reader.join(5)
            self.assertFalse(reader.is_alive(), "the read did not end when the connection was closed")
            self.assertLess(time.monotonic() - began, 2)
            self.assertTrue(outcome["stopped"].sent)
            self.assertTrue(wait_for(lambda: host.hung_up, 3), "the host never saw the client hang up")
            self.assertLess(host.hung_up[0], 5)

    def test_a_request_stopped_before_it_leaves_is_never_sent(self):
        with FakeHost() as host:
            abort = Abort()
            abort.stop("Cancelled first.")
            with self.assertRaises(Cancelled) as raised, abortable(abort):
                request_stream(f"{host.url}/chat/completions", body={"model": "fixture/alpha", "messages": [{"role": "system", "content": "test"}, {"role": "user", "content": "{}"}]})
            self.assertFalse(raised.exception.sent)
            self.assertEqual(host.requests, [])

    def test_a_request_waiting_for_its_connections_slot_stops_there(self):
        limiter, abort, outcome = Limiter(1), Abort(), {}
        held = threading.Event()

        def occupy():
            with limiter.slot():
                held.set()
                time.sleep(3)
        threading.Thread(target=occupy, daemon=True).start()
        held.wait(2)

        def queued():
            try:
                with limiter.slot(abort):
                    outcome["ran"] = True
            except Cancelled as stopped:
                outcome["stopped"] = stopped
        waiter = threading.Thread(target=queued)
        waiter.start()
        time.sleep(.2)
        abort.stop("Cancelled while waiting.")
        waiter.join(3)
        self.assertNotIn("ran", outcome)
        self.assertFalse(outcome["stopped"].sent)


class Batches(cloud_fixture.CloudCase):
    def test_cancelling_a_batch_closes_its_requests_in_flight_and_sends_nothing_more(self):
        self.host.script = lambda body, host: ("thinking", THINKING) if prompt_name(body) == "reconcile" else default_answer(body)
        job = self.queue()
        def worker():  # The worker's turns: the local lane cuts the answer areas, the cloud lane asks the models.
            while self.frq.job(job["id"])["status"] not in ("cancelled", "review_ready", "failed"):
                if not self.frq.process_one() and not self.elapse():
                    time.sleep(.05)
        dispatcher = threading.Thread(target=worker, daemon=True)
        dispatcher.start()
        self.assertTrue(wait_for(lambda: any(prompt_name(r["body"]) == "reconcile" for r in self.host.requests), 20), "the reader was never asked")
        time.sleep(.5)
        sent, began = len(self.host.requests), time.monotonic()
        self.frq.cancel_job(job["id"])  # What the Cancel button does: a catalog write, which the dispatcher hears within a second or two.
        dispatcher.join(10)
        self.assertFalse(dispatcher.is_alive(), "the dispatcher was still waiting for the model after the batch was cancelled")
        self.assertLess(time.monotonic() - began, 6)
        self.assertTrue(wait_for(lambda: self.host.hung_up, 3), "the host never saw its connection closed")
        self.assertEqual(len(self.host.requests), sent)  # nothing further was sent
        [call] = self.calls("step='reconciling'")
        self.assertEqual(call["state"], "cancelled")
        self.assertIn("connection was closed", call["error"])
        with connect(self.path) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT status FROM frq_tasks WHERE job_id=?", (job["id"],))], ["cancelled"])
        self.assertEqual(self.frq.job(job["id"])["status"], "cancelled")

    def test_a_finished_batch_refuses_a_late_cancel_and_stays_finished(self):
        # A Cancel click that lands after the batch finished (settled cards stop polling) once turned it into a cancelled batch:
        # automatic grading paused and its failures lost their retry. It is refused, and the batch stays as it was.
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        with self.assertRaises(Conflict) as refused:
            self.frq.cancel_job(job["id"])
        self.assertIn("finished before it could be cancelled", refused.exception.description)
        self.assertEqual((self.frq.job(job["id"])["status"], self.frq.job(job["id"])["cancel_requested"]), ("review_ready", 0))
        with connect(self.path) as db:
            self.assertIsNone(self.frq.auto_grade_paused(db, self.plan["id"]))

    def test_a_batch_that_finished_with_failures_refuses_a_cancel_meant_for_running_work(self):
        # The case that matters: a batch with a failure settles as `failed`, not `review_ready`. A Cancel clicked on the card while
        # it still showed the batch working (`seen`) would have paused automatic grading and taken away the Retry of its failure.
        self.host.script = lambda body, host: ("status", 402)
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "failed")
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["failed"], 1)
        # Through the route the card's Cancel uses, which passes on the status the card showed.
        answer = self.client.post(f"/api/grading/jobs/{job['id']}/cancel", json={"seen": "running"}, headers=HEADERS)
        self.assertEqual(answer.status_code, 409, answer.get_data(as_text=True))
        self.assertIn("keep their Retry", answer.get_data(as_text=True))
        self.assertEqual((self.frq.job(job["id"])["status"], self.frq.job(job["id"])["cancel_requested"]), ("failed", 0))
        self.assertEqual(self.frq.assignment(self.plan["id"])["workflow"]["failed"], 1)
        with connect(self.path) as db:
            self.assertIsNone(self.frq.auto_grade_paused(db, self.plan["id"]))
        self.host.script = lambda body, host: default_answer(body)
        self.assertEqual(self.frq.retry_job(job["id"])["retry"]["retried"], 1)  # its Retry still works
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")

    def test_a_failed_batch_still_cancels_when_asked_without_a_card(self):
        # Without a card that showed it working (the API, or "a cancelled batch's failures are new work") it cancels as before.
        self.host.script = lambda body, host: ("status", 402)
        job = self.queue()
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "failed")
        self.assertEqual(self.frq.cancel_job(job["id"], seen="failed")["status"], "cancelled")


class Drafts(drafts_fixture.RubricDrafts):
    """The rubric-draft fixture; its own tests run in their own file."""
    def test_cancelling_a_rubric_draft_closes_its_request_in_flight(self):
        self.host.script = lambda body, host: ("thinking", THINKING, survey_answer(("18",))) if prompt_name(body) == "rubric_survey" else write_answer(payload_of(body))
        slot = self.frq.assignment_templates(self.plan["id"])["slots"][0]
        queued = self.frq.queue_rubric_generation(self.plan["id"], {"key": slot["key"], "template_id": self.blank["id"], "request_id": str(uuid.uuid4())})
        worker = threading.Thread(target=self.frq.process_cloud, daemon=True)
        worker.start()
        self.assertTrue(wait_for(lambda: self.host.requests, 20), "the survey was never sent")
        time.sleep(.5)
        began = time.monotonic()
        self.frq.cancel_rubric_generation(self.plan["id"], queued["id"])
        worker.join(10)
        self.assertFalse(worker.is_alive(), "the draft's request kept running after it was cancelled")
        self.assertLess(time.monotonic() - began, 6)
        self.assertTrue(wait_for(lambda: self.host.hung_up, 3))
        self.assertEqual(len(self.host.requests), 1)
        self.assertEqual([c["state"] for c in self.calls(queued["id"])], ["cancelled"])
        self.assertEqual(self.frq.rubric_generation(self.plan["id"], queued["id"])["status"], "cancelled")


for _name in [n for n in dir(drafts_fixture.RubricDrafts) if n.startswith("test_")]:
    setattr(Drafts, _name, None)  # Inherited tests belong to their own file.


if __name__ == "__main__":
    unittest.main()
