"""The waiting grades of a plan graded for participation approve themselves (GRADING "THE WAITING GRADES APPROVE THEMSELVES";
grading/participation.py; requirement, 2026-11-11).
Against the local fake host: no live inference."""
import json
import unittest
from unittest.mock import patch

from grading import certification_automatic, certification_rules, participation
from grading.certification import CertificationService
from rosters import connect
import test_grading_flag_resolution as fixture


class WaitingGradesApprove(fixture.FlagResolution):
    def held(self, *args, **kwargs):
        response = super().held(*args, **kwargs)
        # Grading it ran the worker's own turns: each test starts from a worker that has not looked.
        self.frq._auto_participation = {"looked": -participation.LOOK_EVERY, "markers": {}}
        self.frq._auto_flag_review = {}
        return response

    def on(self, enabled=True):
        self.frq.set_participation(self.plan["id"], {"enabled": enabled})

    def turns(self, count=3):
        with patch("grading.participation.LOOK_EVERY", 0):
            return [self.frq.auto_participation_one() for _ in range(count)]

    def flag_turns(self, count=4):
        with patch("grading.auto_flag_review.SETTLE", 0), patch("grading.auto_flag_review.LOOK_EVERY", 0):
            return [self.frq.auto_flag_review_one() for _ in range(count)]

    def state(self, rid):
        return next(r["state"] for r in self.frq.responses(self.plan["id"]) if r["id"] == rid)

    def rows(self, sql, *args):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute(sql, args)]

    def events(self, kind):
        return [(json.loads(row["detail_json"]), row["actor"]) for row in self.rows(
            "SELECT detail_json,actor FROM frq_events WHERE assignment_id=? AND kind=? ORDER BY id", self.plan["id"], kind)]

    def test_a_grade_held_for_review_is_approved_as_the_graders_left_it_and_keeps_its_flags(self):
        before = self.held()
        held_as = self.state(before["id"])
        self.assertIn(held_as, participation.WAITING)
        self.assertEqual(self.turns(), [False, False, False])  # not graded for participation: it waits for the instructor
        self.assertEqual(self.state(before["id"]), held_as)
        self.on()  # ticked later, on Certification and Export
        self.assertEqual(self.turns(), [True, False, False])  # approved once; nothing more to do
        after = self.frq.response(before["id"])
        review, = self.rows("SELECT * FROM frq_reviews WHERE response_id=?", before["id"])
        self.assertEqual((self.state(before["id"]), review["status"], review["actor"], review["comment"], review["result_id"]),
                         ("approved", "approved", "automatic", participation.APPROVED, before["current_result_id"]))
        # The grade is the one the graders proposed: the same transcript, ticked lines and score.
        ticked = lambda applications: sorted(a["rubric_item_id"] for a in applications if a["applied"])
        self.assertEqual(ticked(json.loads(review["applications_json"])), ticked(before["result"]["applications"]))
        self.assertEqual(json.loads(review["score_json"])["score"], before["result"]["score"]["score"])
        self.assertEqual(json.loads(review["transcript_json"]), before["result"]["transcript"])
        # Its result, and so every flag on it, is as it was.
        self.assertEqual((after["current_result_id"], after["result"]["state"], after["result"]["reasons"], after["result"]["signals"]["risk"]),
                         (before["current_result_id"], before["result"]["state"], before["result"]["reasons"], before["result"]["signals"]["risk"]))
        # It is final as any approved review is, and Certification and Export says how it came to be.
        with self.frq.rosters.lock(), connect(self.path) as db:
            _, decisions, skipped = self.frq.final_decisions(db, self.plan["id"])
            projection = CertificationService._projection(db, self.plan["id"])
        self.assertEqual(([d["row"]["id"] for d in decisions], [d["kind"] for d in decisions], skipped), ([before["id"]], ["review"], []))
        self.assertTrue(projection[before["id"]]["approved_alone"])
        self.assertTrue(projection[before["id"]]["flags"])
        note, = certification_rules._participation_checks({"participation": {"enabled": True, "bubble_key": False, "lines": 1, "approved": 1}}, "#x")
        self.assertIn("1 grade that waited for your review was approved by Faculty as the graders left it, without a look; its flags are kept.", note["detail"])
        plain, = certification_rules._participation_checks({"participation": {"enabled": True, "bubble_key": False, "lines": 1}}, "#x")
        self.assertNotIn("approved by Faculty", plain["detail"])
        # One receipt names the review; each is Faculty's, never the instructor's.
        (approved, actor), = self.events("participation_approved")
        self.assertEqual((actor, approved["count"], [(r["response_id"], r["review_id"], r["state"]) for r in approved["reviews"]]),
                         ("automatic", 1, [(before["id"], review["id"], held_as)]))
        self.assertEqual([(detail.get("participation"), actor) for detail, actor in self.events("response_reviewed")], [(True, "automatic")])
        # No flag review is sent by itself, and the offer says why.
        self.assertEqual(self.flag_turns(), [False] * 4)
        self.assertEqual(self.rows("SELECT id FROM frq_flag_resolutions WHERE assignment_id=?", self.plan["id"]), [])
        self.assertEqual(self.frq.flag_resolution(self.plan["id"])["automatic"]["waits"], "participation")
        # It is no training example, no measure of the graders and no work of the instructor's.
        while self.frq.capture_training_one():
            pass
        self.assertEqual(self.rows("SELECT id FROM frq_training_examples WHERE decision_kind='review'"), [])
        with connect(self.path) as db:
            self.assertEqual(self.frq._training_counts(db, self.plan["id"])["waiting"], 0)
        metrics = self.frq.metrics(self.plan["id"])
        self.assertEqual((metrics["profiles"], metrics["burden"]["instructor_actions"], metrics["burden"]["flagged"], metrics["burden"]["false_alarms"],
                          metrics["burden"]["responses_decided"]), ({}, 0, 0, 0, 1))
        self.assertEqual((metrics["review_signals"]["reviewed"], metrics["review_signals"]["groups"]), (0, []))

    def test_no_flag_review_starts_by_itself_and_the_held_grade_waits_while_work_runs(self):
        before = self.held()
        self.on()
        self.assertEqual(self.flag_turns(), [False] * 4)  # the flag review that follows a batch by itself is not sent
        with connect(self.path) as db:  # a batch of the plan is running: its grades are still being made
            job = db.execute("SELECT id,status FROM frq_jobs WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (self.plan["id"],)).fetchone()
            db.execute("UPDATE frq_jobs SET status='running' WHERE id=?", (job["id"],))
        self.assertEqual((self.turns(), self.frq.approve_waiting(self.plan["id"])), ([False] * 3, 0))
        with connect(self.path) as db:  # and after a cancelled batch the instructor is choosing what grades it next
            db.execute("UPDATE frq_jobs SET status=?,cancel_requested=1 WHERE id=?", (job["status"], job["id"]))
        self.assertEqual(self.frq.approve_waiting(self.plan["id"]), 0)
        self.assertEqual(self.rows("SELECT id FROM frq_reviews WHERE response_id=?", before["id"]), [])
        with connect(self.path) as db:
            db.execute("UPDATE frq_jobs SET cancel_requested=0 WHERE id=?", (job["id"],))
        self.assertEqual(self.frq.approve_waiting(self.plan["id"]), 1)

    def test_the_instructors_own_word_on_an_answer_is_left_alone(self):
        before = self.held()
        self.on()
        draft = self.frq.save_review(before["id"], {"version": before["version"], "result_id": before["current_result_id"], "status": "draft",
                                                    "selected_items": [], "comment": "Looking at this one"})
        self.assertEqual((self.turns(), self.state(before["id"])), ([False] * 3, "draft"))
        self.frq.save_review(before["id"], {"version": draft["version"], "result_id": before["current_result_id"], "status": "unreadable",
                                            "selected_items": [], "comment": "Cannot be read"})
        self.frq._auto_participation = {"looked": -participation.LOOK_EVERY, "markers": {}}
        self.assertEqual((self.turns(), self.state(before["id"])), ([False] * 3, "unreadable"))
        self.assertEqual([row["actor"] for row in self.rows("SELECT actor FROM frq_reviews WHERE response_id=? ORDER BY rowid", before["id"])], ["workspace", "workspace"])

    def test_an_answer_with_no_proposed_score_or_whose_lines_do_not_add_up_is_left_waiting(self):
        before = self.held()
        self.on()
        real = self.frq.decode_result
        with patch.object(self.frq, "decode_result", side_effect=lambda row: {**real(row), "score": None}):  # held with no score proposed
            self.assertEqual(self.frq.approve_waiting(self.plan["id"]), 0)
        proposed = before["result"]["score"]
        with patch("grading.participation.score", return_value={**proposed, "score": proposed["score"] + 1}):  # its lines say another score
            self.assertEqual(self.frq.approve_waiting(self.plan["id"]), 0)
        self.assertEqual(self.rows("SELECT id FROM frq_reviews WHERE response_id=?", before["id"]), [])
        self.assertEqual(self.frq.approve_waiting(self.plan["id"]), 1)

    def test_a_late_paper_settled_this_way_is_cleared_to_certify_itself(self):
        # 
        flagged = {"review_status": "approved", "flags": ["grading", "reasons"]}
        self.assertTrue(certification_automatic.cleared({**flagged, "approved_alone": 1}))
        self.assertFalse(certification_automatic.cleared({"flags": ["grading", "reasons"]}))  # one still waiting is not


if __name__ == "__main__":
    unittest.main()
