"""The flag review that runs by itself (grading/auto_flag_review.py; requirement, 2026-10-11): after a grading batch finishes, the worker
requests the ordinary flag review for that batch's held grades, once each. Against the local fake host: no live inference."""
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from grading import auto_flag_review as auto
from rosters import connect
from fake_openai import flag_resolution_answer, grade_answer, payload_of, prompt_name
import test_grading_flag_resolution as fixture


class AutomaticFlagReview(fixture.FlagResolution):
    def held(self, *args, **kwargs):
        response = super().held(*args, **kwargs)
        # Grading it ran the worker's own turns, whose look already noted these grades: each test starts from a worker that has not looked.
        self.frq._auto_flag_review = {}
        return response

    def turns(self, count=3):
        with patch("grading.auto_flag_review.SETTLE", 0), patch("grading.auto_flag_review.LOOK_EVERY", 0):
            return [self.frq.auto_flag_review_one() for _ in range(count)]

    def work(self, answer=None):
        """Let a requested review and its carrier batch run to the end."""
        self.host.script = lambda body, host: (answer or flag_resolution_answer)(payload_of(body)) if prompt_name(body) == "flag_resolution" else grade_answer(payload_of(body))
        for _ in range(10):
            self.frq.process_cloud()
            with connect(self.path) as db:
                db.execute("UPDATE frq_flag_resolutions SET not_before='' WHERE status='pending'")
        self.run_all()
        return self.frq.flag_resolution(self.plan["id"])

    def reviews(self):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute("SELECT * FROM frq_flag_resolutions WHERE assignment_id=? ORDER BY rowid", (self.plan["id"],))]

    def events(self, kind):
        with connect(self.path) as db:
            return [(json.loads(detail), actor) for detail, actor in db.execute("SELECT detail_json,actor FROM frq_events WHERE assignment_id=? AND kind=? ORDER BY id", (self.plan["id"], kind))]

    def test_a_finished_batch_is_followed_once_and_its_carrier_batch_never(self):
        before = self.held()
        state = self.frq.flag_resolution(self.plan["id"])["automatic"]
        self.assertEqual((state["enabled"], state["waits"], state["not_started"], state["due"]), (True, None, None, {"responses": 1, "response_ids": [before["id"]]}))
        self.assertEqual(self.turns(), [False, True, False])  # a look, the request once it settled, then nothing while it waits to run
        review, = self.reviews()
        self.assertTrue(review["request_id"].startswith(f"auto:{self.plan['id']}:"))
        self.assertIs(json.loads(review["input_json"])["automatic"], True)
        self.assertEqual([actor for _, actor in self.events("flag_resolution_queued")], ["automatic"])
        running = self.frq.flag_resolution(self.plan["id"])
        self.assertEqual((running["resolution"]["automatic"], running["resolution"]["response_ids"], running["automatic"]["due"]["responses"]), (True, [before["id"]], 0))
        done = self.work(lambda p: flag_resolution_answer(p, adopt="standard.second"))
        self.assertEqual((done["resolution"]["status"], done["resolution"]["totals"]["adopted"]), ("complete", 1))
        self.assertNotIn("response_ids", done["resolution"])
        self.assert_finalized(self.frq.response(before["id"]), "flag_resolution")
        # Its carrier batch finished just now: that is no grading batch to follow, and the grade was sent already.
        self.assertEqual(self.turns(6), [False] * 6)
        self.assertEqual(len(self.reviews()), 1)

    def test_a_grade_a_review_left_failed_on_or_was_stopped_before_is_never_sent_again_by_itself(self):
        before = self.held()
        self.turns()
        done = self.work(lambda p: flag_resolution_answer(p, "unreliable"))
        self.assertEqual((done["resolution"]["totals"]["unreliable"], done["automatic"]["due"]["responses"], done["preview"]["responses"]), (1, 0, 1))
        self.assertEqual(self.turns(6), [False] * 6)
        self.assertEqual(len(self.reviews()), 1)
        # The button still sends every held grade it may rule on.
        _, again = self.resolve()
        self.assertEqual(len(self.reviews()), 2)
        self.assertNotIn("automatic", again)
        self.assertEqual(self.frq.response(before["id"])["id"], before["id"])

    def test_a_stopped_review_is_not_started_again(self):
        self.held()
        self.turns()
        review, = self.reviews()
        self.frq.cancel_flag_resolution(self.plan["id"], review["id"])
        self.assertEqual(self.turns(6), [False] * 6)
        self.assertEqual([r["status"] for r in self.reviews()], ["failed"])

    def test_nothing_starts_while_off_busy_or_after_a_cancelled_batch(self):
        self.held()
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "auto_flag_review": False}})
        self.assertEqual(self.turns(4), [False] * 4)
        off = self.frq.flag_resolution(self.plan["id"])["automatic"]
        self.assertEqual((off["enabled"], off["due"]["responses"]), (False, 0))
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "auto_flag_review": True}})
        # A batch that is about to start by itself: one review then follows both.
        self.frq._auto_grade = {self.plan["id"]: {"pending": {"key": "k", "since": 0}}}
        self.assertEqual(self.turns(4), [False] * 4)
        self.frq._auto_grade = {}
        # The last batch was cancelled: the instructor is choosing what grades it next.
        with connect(self.path) as db:
            job = db.execute("SELECT id FROM frq_jobs WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (self.plan["id"],)).fetchone()[0]
            db.execute("UPDATE frq_jobs SET cancel_requested=1 WHERE id=?", (job,))
        self.assertEqual(self.turns(4), [False] * 4)
        self.assertEqual(self.frq.flag_resolution(self.plan["id"])["automatic"]["waits"], "cancelled")
        self.assertEqual(self.reviews(), [])
        with connect(self.path) as db:
            db.execute("UPDATE frq_jobs SET cancel_requested=0 WHERE id=?", (job,))
        self.assertIn(True, self.turns())
        self.assertEqual(len(self.reviews()), 1)

    def test_a_batch_that_finished_long_ago_or_only_recalculated_points_is_left_to_the_button(self):
        self.held()
        with patch("grading.auto_flag_review.WINDOW", timedelta(0)):
            self.assertEqual(self.turns(4), [False] * 4)
            self.assertEqual(self.frq.flag_resolution(self.plan["id"])["automatic"]["due"]["responses"], 0)
        self.assertEqual(self.reviews(), [])
        with connect(self.path) as db:  # the same grade, had it come from a "Recalculate points" batch
            db.execute("UPDATE frq_jobs SET operation='rescore' WHERE assignment_id=?", (self.plan["id"],))
        self.frq._auto_flag_review = {}
        self.assertEqual(self.turns(4), [False] * 4)
        self.assertEqual(self.reviews(), [])
        self.assertEqual(self.frq.flag_resolution(self.plan["id"])["preview"]["responses"], 1)  # the button's offer is unchanged

    def test_a_review_that_cannot_start_is_recorded_once_and_starts_when_a_model_is_chosen(self):
        self.held()
        why = "Choose a Rubric drafting and review model in Compute settings first: it runs the flag review."
        with patch.object(type(self.frq), "_resolution_model", side_effect=ValueError(why)):
            self.assertEqual(self.turns(8), [False] * 8)
            (detail, actor), = self.events(auto.REFUSED)
            self.assertEqual((actor, detail["responses"], detail["reason"]), ("automatic", 1, why))
            self.assertEqual(self.frq.flag_resolution(self.plan["id"])["automatic"]["not_started"]["reason"], why)
            self.frq._auto_flag_review = {}  # a restart forgets what the worker held in memory: the catalog still says it was recorded
            self.assertEqual(self.turns(8), [False] * 8)
            self.assertEqual(len(self.events(auto.REFUSED)), 1)
        # Nothing is tried again until the grades or the settings change.
        self.assertEqual(self.turns(8), [False] * 8)
        self.assertEqual(self.reviews(), [])
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], "auto_certify": False}})
        self.assertIsNone(self.frq.flag_resolution(self.plan["id"])["automatic"]["not_started"])
        self.assertIn(True, self.turns())
        self.assertEqual(len(self.reviews()), 1)

    def test_a_decision_made_while_it_settles_stands(self):
        before = self.held()
        with patch("grading.auto_flag_review.SETTLE", 10**6), patch("grading.auto_flag_review.LOOK_EVERY", 0):
            self.assertFalse(self.frq.auto_flag_review_one())  # the look: these grades are due
        self.assertTrue(self.frq._auto_flag_review[self.plan["id"]]["pending"])
        value = self.frq.response(before["id"])
        self.frq.save_review(before["id"], {"version": value["version"], "result_id": value["current_result_id"], "status": "approved",
                                            "confirm_review": True, "selected_items": self.proposal(value)})
        self.assertEqual(self.turns(4), [False] * 4)
        self.assertEqual(self.reviews(), [])

    def test_only_names_the_grades_a_review_is_requested_for(self):
        before = self.held()
        with self.assertRaises(ValueError):
            self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "auto:none"}, actor="automatic", only=set())
        queued = self.frq.queue_flag_resolution(self.plan["id"], {"request_id": "auto:one"}, actor="automatic", only={before["result"]["id"], "gone"})
        self.assertEqual((queued["responses"], queued["automatic"]), (1, True))


def load_tests(loader, tests, pattern):
    # Only this module's own scenarios: the flag review's and the arbitrator's are run by their own modules.
    return unittest.TestSuite(AutomaticFlagReview(name) for name in AutomaticFlagReview.__dict__ if name.startswith("test_"))


if __name__ == "__main__":
    unittest.main()
