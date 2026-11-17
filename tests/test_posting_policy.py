"""POSTING POLICY (gradebook/posting_policy.py; docs/decisions/canvas.md POSTING GRADES; requirement, 2026-10-08), pure: each column's
desired posting in Canvas (a choice, always by hand, iClicker, its category's rule, else nothing), the steps a sync takes from its own
read, what waits, and the fingerprint that ignores counts."""
import unittest

from gradebook import posting_policy as policy

TODAY = "2026-10-08"


def setup(mode="weighted", **posting):
    return {"mode": mode, "categories": [
        {"id": "cat_hw", "name": "WebAssign Homework", "weight": "10", **({"posting": posting} if posting else {})},
        {"id": "cat_exam", "name": "Semester Exams", "weight": "48"},
        {"id": "cat_other", "name": "Other (does not count)", "weight": "0", "posting": {"due": True, "certified": True}}],
        "items": []}


def item(ident, category="cat_hw", **extra):
    return {"id": ident, "title": ident.title(), "category_id": category, "counts": True, "archived": False, "due_date": "", "link": None,
            "assignment_id": None, **extra}


LINKED = {"link": {"kind": "grading", "parts": ["frq"]}, "assignment_id": "asg-1"}


class DesiredTests(unittest.TestCase):
    def want(self, column, rules=None, mode="weighted", **context):
        found = setup(mode, **(rules or {}))
        return policy.desired(found, column, today=TODAY, **context)

    def test_a_category_without_a_rule_posts_by_hand_and_its_columns_are_switched(self):
        found = self.want(item("hw1", due_date="2026-10-01"))
        self.assertEqual((found["state"], found["why"], found["manual"], found["switch"]), (None, None, True, True))
        self.assertFalse(self.want(item("wa", due_date="2026-10-01", canvas="webassign"))["switch"])  # Canvas's own grades: never switched

    def test_after_the_due_date_but_not_on_it(self):
        rules = {"due": True}
        self.assertEqual(self.want(item("hw1", due_date="2026-10-07"), rules)["state"], "posted")
        self.assertIsNone(self.want(item("hw2", due_date=TODAY), rules)["state"])  # due today is not past due
        self.assertIsNone(self.want(item("hw3"), rules)["state"])  # no due date: waits for the instructor
        self.assertFalse(self.want(item("hw1", due_date="2026-10-07"), rules)["manual"])

    def test_when_certified_for_columns_faculty_grades_and_the_due_date_for_the_rest(self):
        both = {"due": True, "certified": True}
        quiz = item("quiz4", due_date="2026-10-01", **LINKED)
        self.assertIsNone(self.want(quiz, both)["state"])  # graded here: its date does not post it, its certification does
        self.assertEqual(self.want(quiz, both, certified={"quiz4"})["why"], "certified")
        self.assertIsNone(self.want(quiz, {"due": True}, certified={"quiz4"})["state"])  # the category does not post on certification
        self.assertEqual(self.want(item("paper", due_date="2026-10-01"), both)["why"], "due")  # the mixed category's import

    def test_iclicker_posts_unless_the_column_has_no_effect(self):
        found = self.want(item("poll", category="cat_exam"), iclicker={"poll"})
        self.assertEqual((found["state"], found["why"], found["manual"]), ("posted", "iclicker", False))
        self.assertIsNone(self.want(item("poll", category="cat_other"), iclicker={"poll"})["state"])

    def test_no_effect_on_a_grade_is_always_by_hand(self):
        for column, why in ((item("part", category="cat_other", due_date="2026-10-01"), "weight 0"),
                            (item("loose", category=None, due_date="2026-10-01"), "no category"),
                            (item("old", archived=True), "archived"), (item("skip", counts=False, due_date="2026-10-01"), "not counted")):
            found = self.want(column, {"due": True})
            self.assertEqual((found["state"], found["forced"], found["manual"]), (None, why, True), column)
        # Points mode: weight says nothing, so a weight-0 category's rule applies.
        self.assertEqual(self.want(item("part", category="cat_other", due_date="2026-10-01"), mode="points")["why"], "due")

    def test_a_choice_wins_whatever_the_rule(self):
        self.assertEqual(self.want(item("exam1", category="cat_exam"), choice="post")["why"], "choice")
        self.assertEqual(self.want(item("hw1", due_date="2026-10-01"), {"due": True}, choice="hide")["state"], "hidden")
        self.assertEqual(self.want(item("part", category="cat_other"), choice="post")["state"], "posted")  # posting by hand
        self.assertEqual(self.want(item("hw1", due_date="2026-10-01"), {"due": True}, choice="rule")["why"], "due")


def read(course, *submissions, students=None):
    """A course's read in canvas_export.from_read's shape: assignment 'a1', the given submissions (user, score, posted)."""
    return {course: {"assignments": [], "students": [{"user_id": user, "state": "active"} for user, *_ in submissions] if students is None else students,
                     "submissions": {("a1", user): {"score": score, "excused": False, "posted": posted} for user, score, posted in submissions}}}


def column(item_id, course, values=(), held=(), problems=(), **extra):
    return {"item_id": item_id, "course_id": course, "assignment": {"id": "a1"}, "send": not problems, "values": list(values), "held": list(held),
            "problems": list(problems), **extra}


class StepTests(unittest.TestCase):
    def setUp(self):
        self.setup = {**setup(due=True), "items": [item("hw1", due_date="2026-10-01"), item("exam1", category="cat_exam")]}
        self.where = {("hw1", "101"): {"id": "a1", "post_manually": True, "published": True},
                      ("exam1", "101"): {"id": "a1", "post_manually": False, "published": True}}

    def steps(self, canvas, out=None, choices=None, **extra):
        want = policy.policies(self.setup, choices=choices or {}, today=TODAY)
        return policy.steps(self.setup, self.where, canvas, out or {"columns": []}, policy=want, **extra)

    def test_a_due_column_posts_where_canvas_hides_a_grade_and_a_by_hand_one_is_switched(self):
        found = self.steps(read("101", ("u1", 9, False), ("u2", 7, True)), {"columns": [column("exam1", "101")]})
        self.assertEqual([(s["item_id"], s["action"], s["why"], s["hidden"], s["waits"]) for s in found],
                         [("hw1", "post", "due", 1, []), ("exam1", "switch", "manual", 0, [])])

    def test_nothing_when_canvas_shows_every_grade_and_a_student_who_left_never_counts(self):
        canvas = read("101", ("u1", 9, True), ("gone", 5, False), students=[{"user_id": "u1", "state": "active"}, {"user_id": "gone", "state": "completed"}])
        self.assertEqual([s for s in self.steps(canvas) if s["item_id"] == "hw1"], [])

    def test_posted_stays_posted_when_the_sync_sends_a_grade(self):
        out = {"columns": [column("hw1", "101", values=[{"row": "r"}])]}
        self.assertEqual([s["action"] for s in self.steps(read("101", ("u1", 9, True)), out) if s["item_id"] == "hw1"], ["post"])

    def test_a_hide_choice_hides_what_shows(self):
        found = [s for s in self.steps(read("101", ("u1", 9, True)), choices={"hw1": "hide"}) if s["item_id"] == "hw1"]
        self.assertEqual([(s["action"], s["why"], s["shown"]) for s in found], [("hide", "choice", 1)])

    def test_what_waits_is_said_and_settled_with_the_picks(self):
        out = {"columns": [column("hw1", "101", values=[{"row": "r"}], held=[{"key": "hw1:101:r2"}])]}
        canvas = read("101", ("u1", 9, False))
        found = self.steps(canvas, out, unsure={("hw1", "101")}, automatic=True)
        self.assertIn("could not tell", found[0]["waits"][0])
        found = self.steps(canvas, out)
        self.assertEqual(found[0]["waits"], [])
        self.assertIn("wait for your choice", policy.settle(found, out, {})[0]["waits"][0])
        self.assertEqual(policy.settle(found, out, {"hw1:101:r2": "faculty"})[0]["waits"], [])
        blocked = {"columns": [column("hw1", "101", problems=["“Hw1” is worth 5 points in Canvas"], values=[{"row": "r"}])]}
        self.assertIn("cannot sync here", self.steps(canvas, blocked)[0]["waits"][0])

    def test_courses_not_ticked_are_left_out_and_the_fingerprint_ignores_counts(self):
        self.assertEqual(self.steps(read("101", ("u1", 9, False)), courses=["102"]), [])
        one = self.steps(read("101", ("u1", 9, False)))
        two = self.steps(read("101", ("u1", 9, False), ("u2", 4, False)))
        self.assertNotEqual(one, two)
        self.assertEqual(policy.digest(one), policy.digest(two))
        self.assertNotEqual(policy.digest(one), policy.digest(self.steps(read("101", ("u1", 9, False)), choices={"hw1": "rule", "exam1": "post"})))

    def test_pending_from_what_faculty_last_read(self):
        want = policy.policies(self.setup, choices={}, today=TODAY)
        self.assertEqual(policy.pending(want, [{"item_id": "hw1", "manual": True, "graded": 5, "hidden": 2},
                                               {"item_id": "exam1", "manual": False, "graded": 5, "hidden": 0}]),
                         {"hw1": ["post"], "exam1": ["switch"]})
        self.assertEqual(policy.pending(want, [{"item_id": "hw1", "manual": True, "graded": 5, "hidden": 0}]), {})


if __name__ == "__main__":
    unittest.main()
