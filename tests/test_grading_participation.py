"""Graded for participation (GRADING "GRADED FOR PARTICIPATION"; requirement, 2026-11-01; grading/participation.py).

On the graded workspace of `test_gradescope_send` (section 501; the first student on Version A has 4 of 10 on q20 and 6 of 8 on q21,
the second on Version B 2 of 10 and 5 of 8, all the instructor's approved reviews): the setting is the plan's, append-only, and
changes no grade; what counts a final grade (Certification and Export, the export, Sending grades to Gradescope) gives every final
grade below its worth the line "Graded for participation" bringing it to full credit, with the rubric lines kept; turning it off
takes the line away; and a bubble-sheet key keeps an assignment from being graded for participation.
"""
import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grading import participation  # noqa: E402
from grading.gradescope_sync import GradescopeSender  # noqa: E402
from rosters import connect  # noqa: E402
import test_gradescope_send as graded  # noqa: E402

HEADERS = {"X-Teaching-Request": "1"}


def tearDownModule():
    graded.tearDownModule()


class PureTests(unittest.TestCase):
    def test_a_grade_below_its_worth_counts_as_full_credit_with_one_more_line(self):
        score = {"score": 4.0, "max_score": 10, "unclamped_score": 4.0, "selected": ["R3_2"], "adjustments": [{"points": 1, "reason": "Neat"}]}
        found = participation.counted(score)
        self.assertEqual((found["score"], found["selected"], found["participation"]), (10, ["R3_2"], 6))
        self.assertEqual(found["adjustments"], [{"points": 1, "reason": "Neat"}, {"points": 6, "reason": participation.REASON}])
        self.assertEqual(score["score"], 4.0)  # the grade read is left as it was
        self.assertIs(participation.counted(found), found)  # counting again changes nothing
        self.assertEqual(participation.line(found), [{"points": 6, "reason": participation.REASON}])
        self.assertEqual(participation.line(score), [])

    def test_full_credit_and_no_score_are_left_alone(self):
        for score in ({"score": 10.0, "max_score": 10}, {"score": 11.0, "max_score": 10}, {"score": None, "max_score": 10}, {"score": 3.0}, None):
            self.assertIs(participation.counted(score), score)

    def test_the_line_makes_up_the_unclamped_score_so_gradescope_gives_the_worth(self):
        # Deductions past the floor: Faculty's score is 0 but Gradescope adds the adjustment before its own floor.
        found = participation.counted({"score": 0.0, "max_score": 5, "unclamped_score": -2.0, "adjustments": []})
        self.assertEqual((found["score"], found["participation"]), (5, 7))
        self.assertEqual(participation.counted({"score": 2.5, "max_score": 5, "adjustments": []})["participation"], 2.5)

    def test_a_decision_rows_entry_counts_with_exact_text(self):
        entry = {"kind": "review", "score": "4", "adjustments": [], "question_id": "q"}
        self.assertEqual(participation.counted_entry(entry, 10), {**entry, "score": "10", "adjustments": ["6"], "participation": "6"})
        self.assertIs(participation.counted_entry({"kind": None, "score": None}, 10)["kind"], None)
        same = {"kind": "review", "score": "10", "adjustments": []}
        self.assertIs(participation.counted_entry(same, 10), same)


class ParticipationTests(graded.GradescopeCase):
    allowed_comments = ("+6 Graded for participation", "+2 Graded for participation", "+8 Graded for participation", "+3 Graded for participation")

    def url(self, tail=""):
        return f"/api/grading/assignments/{self.aid}{tail}"

    def set(self, on, status=200):
        response = self.client.put(self.url("/participation"), json={"enabled": on}, headers=HEADERS)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def certification(self):
        response = self.client.get(self.url("/certification"), headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_the_setting_is_the_plans_append_only_and_refused_unless_a_boolean(self):
        self.assertEqual(self.client.get(self.url("/participation"), headers=HEADERS).get_json(), {"enabled": False, "bubble_key": False})
        self.assertEqual(self.set(True), {"enabled": True, "bubble_key": False})
        self.set(True)  # what stands already writes nothing
        self.set("yes", status=400)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_participation").fetchone()[0], 1)
            self.assertEqual([json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='participation_set'")], [{"enabled": True}])
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE frq_participation SET enabled=0")
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM frq_participation")
        self.assertEqual(self.set(False)["enabled"], False)

    def test_grading_keeps_the_real_scores_and_what_counts_is_full_credit_with_the_lines_kept(self):
        before = self.certification()
        self.assertEqual(before["participation"], {"enabled": False, "bubble_key": False})
        self.assertNotIn("participation", [c["id"] for c in before["checks"]])
        self.set(True)
        with connect(self.path) as db:
            _, decisions, _ = self.frq.final_decisions(db, self.aid)  # the ONE final rule itself is untouched
        self.assertEqual(sorted(d["final"]["score"]["score"] for d in decisions), [2.0, 4.0, 5.0, 6.0])
        view = self.certification()
        self.assertEqual(view["participation"], {"enabled": True, "bubble_key": False})
        self.assertEqual([(s["frq"]["earned"], s["frq"]["possible"], s["scores"]) for s in view["students"]], [("18", "18", ["10", "8"])] * 2)
        note = next(c for c in view["checks"] if c["id"] == "participation")
        self.assertEqual((note["level"], note["count"]), ("info", 4))
        # The question panel shows the rubric line the grade earned and the participation line beside it.
        panel = self.client.get(self.url(f"/certification/questions/{self.q[('q20', 'A')]}"), headers=HEADERS).get_json()
        self.assertEqual([(r["score"], r["selected"], r["adjustments"]) for r in panel["responses"]],
                         [("10", ["R3_2"], [{"points": "6", "reason": "Graded for participation"}])])
        student = self.client.get(self.url(f"/certification/students/{self.section['id']}/{self.enrollment}"), headers=HEADERS).get_json()
        self.assertEqual(sorted((q["score"], q.get("participation")) for q in student["questions"]), [("10", "6"), ("8", "2")])
        # The certify preview reads the grades exactly, as they count.
        preview = self.client.post(self.url("/certification/preview"), json={}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertEqual(next(c["count"] for c in preview.get_json()["checks"] if c["id"] == "participation"), 4)
        # Turned off, the line is gone again.
        self.set(False)
        view = self.certification()
        self.assertEqual(sorted(s["frq"]["earned"] for s in view["students"]), ["10", "7"])

    def test_gradescope_gets_the_rubric_items_with_the_line_as_adjustment_and_comment(self):
        self.set(True)
        view = self.view()
        self.assertEqual(view["refused"], [])
        self.send(view)
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], points=6.0, score=10.0, comment="+6 Graded for participation")
        self.assertGraded("q21", "B", self.bob, [3, 5], points=3.0, score=8.0, comment="+3 Graded for participation")
        self.assertNothingForbidden()
        with connect(self.path) as db:
            rows = self.frq.responses(self.aid)
            states = GradescopeSender._grade_coverage(db, self.aid, rows)
            self.assertEqual(set(states.values()), {"sent"})
            self.assertEqual(GradescopeSender.expected_comments(db, self.aid)[self.response("q20", "A")["id"]], "+6 Graded for participation")
        # Turned off, what Gradescope has is no longer what the grades count as.
        self.set(False)
        with connect(self.path) as db:
            self.assertEqual(GradescopeSender.expected_comments(db, self.aid), {})
            self.assertEqual(set(GradescopeSender._grade_coverage(db, self.aid, self.frq.responses(self.aid)).values()), {"changed"})

    def test_the_export_counts_the_same_way(self):
        self.set(True)
        self.frq.export(self.aid, "audit")
        with connect(self.path) as db:
            snapshot = json.loads(db.execute("SELECT snapshot_json FROM frq_exports ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual(sorted(r["final"]["score"]["score"] for r in snapshot["records"]), [8, 8, 10, 10])

    def test_a_bubble_sheet_key_keeps_it_from_being_graded_for_participation(self):
        self.set(True)
        with connect(self.path) as db:
            db.execute("INSERT INTO frq_bubble_keys VALUES('k1',?,1,?,'x','r1','x','workspace','2026-11-01T00:00:00+00:00')", (self.aid, json.dumps({"versions": []})))
        view = self.certification()
        self.assertIn("participation_bubble", [c["id"] for c in view["checks"] if c["level"] == "block"])
        self.set(False)
        self.set(True, status=409)
        self.assertEqual(self.client.get(self.url("/participation"), headers=HEADERS).get_json(), {"enabled": False, "bubble_key": True})


if __name__ == "__main__":
    unittest.main()
