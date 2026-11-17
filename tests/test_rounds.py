"""The shared background round loop (rounds.repeat): skip a busy round, warn once per storage outage, log and go on."""

import logging
import unittest

from werkzeug.exceptions import Conflict

import rounds
from course_storage import StorageError

OK, AWAY, BUSY, BOOM = "ok", "storage", "conflict", "boom"


class Stop:
    """A stand-in stop event: `wait` records the interval and stops the loop once the script is spent."""

    def __init__(self, script):
        self.script, self.waited, self.stopped = list(script), [], False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.waited.append(seconds)
        self.stopped = not self.script


class RepeatTests(unittest.TestCase):
    def run_script(self, script):
        stop, steps = Stop(script), []

        def step():
            outcome = stop.script.pop(0)
            steps.append(outcome)
            if outcome == AWAY:
                raise StorageError("storage is away")
            if outcome == BUSY:
                raise Conflict("busy")
            if outcome == BOOM:
                raise RuntimeError("boom")

        # The stop's script is consumed by the step, so `wait` sees it empty after the last one.
        log = logging.getLogger("test_rounds.loop")
        with self.assertLogs(log, level="DEBUG") as captured:
            log.debug("started")
            rounds.repeat(stop, step, 7, log, "waiting for storage", "stopped unexpectedly")
        return steps, stop.waited, [(record.levelname, record.getMessage(), bool(record.exc_info)) for record in captured.records[1:]]

    def test_every_pass_waits_the_interval_and_the_loop_ends_with_the_stop(self):
        steps, waited, logged = self.run_script([OK, OK, OK])
        self.assertEqual((steps, waited, logged), ([OK, OK, OK], [7, 7, 7], []))

    def test_a_busy_round_is_skipped_quietly(self):
        steps, waited, logged = self.run_script([BUSY, OK, BUSY])
        self.assertEqual((steps, waited, logged), ([BUSY, OK, BUSY], [7, 7, 7], []))

    def test_storage_away_is_warned_once_until_a_round_succeeds(self):
        steps, waited, logged = self.run_script([AWAY, AWAY, BUSY, AWAY, OK, AWAY, AWAY, OK])
        self.assertEqual(steps, [AWAY, AWAY, BUSY, AWAY, OK, AWAY, AWAY, OK])
        self.assertEqual(waited, [7] * 8)
        self.assertEqual(logged, [("WARNING", "waiting for storage", False)] * 2)

    def test_any_other_failure_is_logged_with_its_traceback_and_the_loop_goes_on(self):
        steps, waited, logged = self.run_script([BOOM, AWAY, BOOM, OK])
        self.assertEqual(steps, [BOOM, AWAY, BOOM, OK])
        self.assertEqual(waited, [7] * 4)
        self.assertEqual(logged, [("ERROR", "stopped unexpectedly", True), ("WARNING", "waiting for storage", False),
                                  ("ERROR", "stopped unexpectedly", True)])

    def test_a_stop_before_the_first_pass_runs_no_round(self):
        stop = Stop([])
        stop.stopped = True
        rounds.repeat(stop, lambda: self.fail("no round after the stop"), 7, logging.getLogger("test_rounds.loop"), "w", "f")
        self.assertEqual(stop.waited, [])


if __name__ == "__main__":
    unittest.main()
