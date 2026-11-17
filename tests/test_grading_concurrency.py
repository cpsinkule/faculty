"""Full concurrency (requirement, 2026-09-23), and no spending cap of Faculty's own. Off by default: the configured limits keep working exactly as before."""
import threading
import time
import unittest
from unittest.mock import patch

from grading import cloud, config
from grading.cloud import Limiter
from fake_openai import default_answer, prompt_name
import test_grading_cloud as cloud_fixture
import test_grading_fixes_orchestration as orchestration
from test_grading_cloud import endpoint


class Settings(unittest.TestCase):
    def test_full_concurrency_is_an_off_by_default_switch_and_a_saved_spending_cap_is_retired(self):
        self.assertIs(config.validate({})["full_concurrency"], False)
        self.assertIs(config.validate({"full_concurrency": True})["full_concurrency"], True)
        with self.assertRaises(ValueError):
            config.validate({"full_concurrency": "yes"})
        clean = config.validate({"max_job_cost": 75.0})
        self.assertNotIn("max_job_cost", clean)


class Limits(unittest.TestCase):
    def test_no_limit_until_the_host_is_busy_then_half_of_what_was_in_flight(self):
        limiter, started, release = Limiter(None), [], threading.Event()

        def hold():
            with limiter.slot():
                started.append(1)
                release.wait(5)
        threads = [threading.Thread(target=hold) for _ in range(40)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 3
        while len(started) < 40 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(started), 40)  # Nothing waited for a slot.
        limiter.rate_limited()
        self.assertEqual(limiter.limit, 20)  # The host said it was busy: half of what was in flight.
        release.set()
        for thread in threads:
            thread.join(5)
        for _ in range(20):
            limiter.succeeded()
        self.assertEqual(limiter.limit, 21)  # And it grows back, one step per twenty successes, with no ceiling.

    def test_a_configured_limit_is_kept_as_before(self):
        limiter = Limiter(4)
        limiter.rate_limited()
        self.assertEqual((limiter.maximum, limiter.limit), (4, 2))
        for _ in range(40):
            limiter.succeeded()
        self.assertEqual(limiter.limit, 4)


class Dispatcher(cloud_fixture.CloudCase):
    second_student = orchestration.Lane.second_student

    def run_two(self, full):
        """Two problems whose readers each take a moment, on a connection allowed ONE request at a time."""
        self.host.script = lambda body, host: ("stall", .6) if prompt_name(body) == "reconcile" else default_answer(body)
        self.configure([endpoint(self.host, "alpha-vision", max_inflight=1)], full_concurrency=full, concurrency=1)
        self.host.peak = 0
        job = self.queue(selection="all")
        self.run_all()
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")
        return self.host.peak

    def test_full_concurrency_sets_the_configured_limits_aside_and_off_keeps_them(self):
        self.second_student()
        self.assertEqual(self.run_two(False), 1)  # The connection's limit of one holds.
        self.assertEqual(self.run_two(True), 2)   # Both readers leave at once.

    def test_full_concurrency_goes_past_the_components_in_flight_ceiling(self):
        self.configure(full_concurrency=True)
        captured = []
        real = self.frq._cloud_claim
        def claim(capacity):
            captured.append(capacity)
            return real(capacity)
        with patch.object(self.frq, "_cloud_claim", side_effect=claim):
            self.queue()
            self.run_all()
        self.assertGreater(max(captured), cloud.MAX_UNITS)


if __name__ == "__main__":
    unittest.main()
