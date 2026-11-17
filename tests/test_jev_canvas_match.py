"""JEV CANVAS-MATCH (docs/decisions/jev.md; requirement, 2026-10-03): Jev names a gradebook column for a Canvas assignment matched for
the first time; the planner suggests it only when both option orders agree, at confidence .8 or more, worth the same points.
Everything runs against FakeCanvas and a scripted Jev: nothing reaches TypeSafe or Canvas."""
import unittest
from unittest.mock import patch

from test_canvas import ALICE, BOB, GradebookCanvasBase
from test_storage import HEADERS

from gradebook import canvas_import
from jev import client, matching
from rosters import connect


class OptionLabels(unittest.TestCase):
    def test_duplicate_titles_reserve_real_suffixes_and_none_option(self):
        items = [{"id": str(i), "title": title} for i, title in enumerate(
            ["Quiz", "quiz", "quiz (2)", matching.NEW], 1)]
        labels = matching._options(items)
        self.assertEqual(labels, {"Quiz": "1", "quiz (3)": "2", "quiz (2)": "3", matching.NEW + " (2)": "4"})
        state, questions, options = matching.request({"title": "Quiz"}, items, [])
        self.assertEqual(len(questions["column"]["criteria"]), len(items) + 1)
        self.assertIsNone(options.get(matching.NEW))


def scripted(picks, confidence=.95, reversed_pick=None):
    """A fake `jev.client.evaluate`: `picks` {Canvas title: option}, both orders the same unless `reversed_pick` says otherwise."""
    def evaluate(state, questions):
        title = state["canvas_assignment"]["title"]
        pick = picks.get(title, matching.NEW)
        answers = {}
        for name, question in questions.items():
            chosen = (reversed_pick or {}).get(title, pick) if name.endswith("__reversed") else pick
            probabilities = {option: (1.0 if option == chosen else 0.0) for option in question["criteria"]}
            answers[name] = {"type": "choice", "choice": chosen, "probabilities": probabilities, "confidence": confidence}
        return {"model": client.MODEL, "answers": answers, "usage": {"input_tokens": 300, "output_tokens": 20}}
    return evaluate


class CanvasMatchTests(GradebookCanvasBase):
    def setUp(self):
        super().setUp()
        patch("jev.client.available", return_value=True).start()
        self.addCleanup(patch.stopall)
        setup = self.client.get(self.base).json["setup"]
        saved = self.client.post(self.base + "/setup", json={"base_revision": setup["revision"], "request_id": "setup-hw",
                                 "setup": {**setup, "items": [*setup["items"], {"title": "HW II", "points": "5"}]}}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.hw = next(item["id"] for item in saved.json["setup"]["items"] if item["title"] == "HW II")

    def columns(self, plan):
        return {(c["course_id"], c["title"]): c for c in plan["from_canvas"]["columns"]}

    def calls(self):
        with connect(self.app.config["DATABASE"]) as db:
            return db.execute("SELECT status,COUNT(*) n FROM jev_calls WHERE purpose='canvas-match' GROUP BY status").fetchall()

    def test_an_agreed_confident_pick_is_suggested_and_import_reuses_it_without_asking(self):
        with patch("jev.client.evaluate", side_effect=scripted({"Homework 2": "HW II"})) as asked:
            plan = self.preview()
            column = self.columns(plan)
            for course, assignment in ((self.course_a, "Homework 2"), (self.course_b, "Homework 2")):
                self.assertEqual(column[(course, assignment)]["mapping"], {"action": "item", "item_id": self.hw})
                self.assertEqual(column[(course, assignment)]["suggested"], "jev")
                self.assertEqual(column[(course, assignment)]["jev"], {"item_id": self.hw, "confidence": .95, "applied": True})
            # Asked only about first-time assignments the titles could not place: Homework 2 (twice) and Reading check.
            self.assertEqual(asked.call_count, 3)
            self.assertNotIn("jev", column[(self.course_b, "Reading check")])
            self.assertEqual(column[(self.course_b, "Reading check")]["suggested"], "new")
            self.assertEqual(column[(self.course_a, "Quiz 1")]["suggested"], "title")
            self.assertFalse(any("askable" in c for c in plan["from_canvas"]["columns"]))
            # A second preview reads the receipts; Import never asks.
            self.assertEqual(self.preview()["fingerprint"], plan["fingerprint"])
            applied = self.apply(plan, "import-jev")
            self.assertEqual(applied.status_code, 200, applied.json)
            self.ended(applied)
            self.assertEqual(asked.call_count, 3)
        # Remembered by Canvas ID like any confirmed match: later previews do not ask again.
        with patch("jev.client.evaluate", side_effect=AssertionError("asked again")):
            later = self.columns(self.preview())
        self.assertEqual(later[(self.course_a, "Homework 2")]["suggested"], "remembered")
        self.assertEqual([tuple(row) for row in self.calls()], [("complete", 3)])

    def test_import_matches_the_preview_even_when_jev_is_switched_off_after_it(self):
        with patch("jev.client.evaluate", side_effect=scripted({"Homework 2": "HW II"})):
            plan = self.preview()
        with patch("jev.client.available", return_value=False), patch("jev.client.evaluate", side_effect=AssertionError("asked")):
            self.ended(self.apply(plan, "import-off"))

    def test_disagreeing_orders_are_not_a_pick(self):
        with patch("jev.client.evaluate", side_effect=scripted({"Homework 2": "HW II"}, reversed_pick={"Homework 2": matching.NEW})):
            column = self.columns(self.preview())
        self.assertEqual(column[(self.course_a, "Homework 2")]["suggested"], "new")
        self.assertNotIn("jev", column[(self.course_a, "Homework 2")])

    def test_a_low_confidence_pick_is_only_shown(self):
        with patch("jev.client.evaluate", side_effect=scripted({"Homework 2": "HW II"}, confidence=.7)):
            column = self.columns(self.preview())
        self.assertEqual(column[(self.course_a, "Homework 2")]["suggested"], "new")
        self.assertEqual(column[(self.course_a, "Homework 2")]["jev"], {"item_id": self.hw, "confidence": .7, "applied": False})

    def test_a_pick_worth_other_points_is_only_shown(self):
        # Points are compared in code (Reading check is worth 2, HW II 5), never by Jev.
        with patch("jev.client.evaluate", side_effect=scripted({"Reading check": "HW II"})):
            column = self.columns(self.preview())
        self.assertEqual(column[(self.course_b, "Reading check")]["suggested"], "new")
        self.assertFalse(column[(self.course_b, "Reading check")]["jev"]["applied"])

    def test_the_assistant_preview_never_asks_jev(self):
        with patch("jev.client.evaluate", side_effect=AssertionError("asked")):
            plan = self.preview(environ_overrides={"faculty.actor": "assistant"})
        self.assertEqual(self.columns(plan)[(self.course_a, "Homework 2")]["suggested"], "new")

    def test_a_failed_request_is_not_resent_and_suggests_nothing(self):
        with patch("jev.client.evaluate", side_effect=client.DecisionError("Jev could not complete this check (HTTP 500).")) as asked:
            first = self.columns(self.preview())
            self.preview()
        self.assertEqual(asked.call_count, 3)
        self.assertEqual(first[(self.course_a, "Homework 2")]["suggested"], "new")
        self.assertEqual([tuple(row) for row in self.calls()], [("failed", 3)])

    def test_receipts_hold_titles_not_students(self):
        with patch("jev.client.evaluate", side_effect=scripted({})):
            self.preview()
        with connect(self.app.config["DATABASE"]) as db:
            stored = " ".join(" ".join(str(v) for v in tuple(row)) for row in db.execute("SELECT * FROM jev_calls"))
        for private in (ALICE, BOB, "Example, Alice"):
            self.assertNotIn(private, stored)


class PlannerTests(unittest.TestCase):
    """The pure planner: two assignments of one course naming one column take neither."""

    def test_one_column_named_twice_in_a_course_is_suggested_for_neither(self):
        assignments = [{"id": str(n), "name": name, "points": 5, "due_at": "", "published": True, "grading_type": "points",
                        "state": "published"} for n, name in ((1, "Homework 2"), (2, "Homework two"))]
        canvas = {"1": {"groups": [{"id": "9", "name": "Homework", "assignments": assignments}], "students": [], "submissions": []}}
        item = {"id": "hw", "title": "HW II", "points": "5", "archived": False, "sections": [], "due_date": "", "link": None}
        hints = {"1:1": {"item_id": "hw", "confidence": .99}, "1:2": {"item_id": "hw", "confidence": .99}}
        plan = canvas_import.plan(canvas, options={}, students=[], links={"s": {"course_id": "1", "name": "C"}},
                                  setup={"items": [item], "categories": []}, revision=1, cells={}, remembered={}, hints=hints)
        self.assertEqual({c["suggested"] for c in plan["columns"]}, {"new"})
        self.assertEqual({c["jev"]["applied"] for c in plan["columns"]}, {False})


if __name__ == "__main__":
    unittest.main()
