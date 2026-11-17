"""The gradebook's setup validation and its exact computation of categories, drops, overall percentages and letters."""
import copy
import itertools
import random
import time
import unittest
from fractions import Fraction

from gradebook import blanks
from gradebook.common import RULE
from gradebook.compute import compute, rounded
from gradebook.setup import applies, default_setup, find_item, normalize

SECTIONS = frozenset({"sec-1", "sec-2"})
ASSIGNMENTS = frozenset({"asg-test1", "asg-test2"})


def category(ident, name, weight="0", drop=0, aggregate="points"):
    return {"id": ident, "name": name, "weight": weight, "drop_lowest": drop, "aggregate": aggregate}


def item(ident, points, category_id=None, **fields):
    return {"id": ident, "title": fields.pop("title", ident), "category_id": category_id, "points": points, "due_date": "",
            "counts": True, "extra_credit": False, "archived": False, "sections": [], "assignment_id": None, "link": None,
            "notes": "", **fields}


def setup_of(categories=(), items=(), **policies):
    """A setup with fixed IDs, checked by `normalize` against itself so every computation test starts from a valid setup."""
    data = {**default_setup(), "categories": list(categories), "items": list(items), **policies}
    return normalize(data, data, sections=SECTIONS, assignments=ASSIGNMENTS)


def scored(points):
    return {"status": "scored", "points": points}


def run(setup, rows, students=None):
    """`rows`: {row: {item_id: cell}}; every row is a student of sec-1 unless `students` says otherwise."""
    cells = {}
    for row, values in rows.items():
        for item_id, cell in values.items():
            cells.setdefault(item_id, {})[row] = cell
    students = students or [{"row": row, "section_id": "sec-1"} for row in rows]
    return compute(setup, students, cells)


class SetupTests(unittest.TestCase):
    def normalized(self, data, base=None, **kwargs):
        kwargs.setdefault("sections", SECTIONS)
        kwargs.setdefault("assignments", ASSIGNMENTS)
        return normalize(data, base or default_setup(), **kwargs)

    def test_the_default_setup_is_its_own_normal_form(self):
        base = default_setup()
        self.assertEqual(base["rule"], RULE)
        self.assertEqual([(entry["letter"], entry["min"]) for entry in base["scale"]], [("A", "90"), ("B", "80"), ("C", "70"), ("D", "60"), ("F", "0")])
        # New terms count empty cells as zero once due (GRADEBOOK "ZERO ONCE DUE"); a first revision saved before was made on "exclude".
        self.assertEqual((base["mode"], base["ungraded"], base["letter_rounding"], base["categories"], base["items"]), ("points", "due", None, [], []))
        self.assertEqual(default_setup("2026-09-27T23:49:34.501919+00:00")["ungraded"], "exclude")
        self.assertEqual(default_setup("2026-10-07T01:00:00.000000+00:00")["ungraded"], "due")
        self.assertEqual(self.normalized(default_setup()), base)
        self.assertEqual(self.normalized({}), base)  # Nothing sent keeps everything.
        self.assertIsNot(default_setup()["scale"], default_setup()["scale"])

    def test_the_server_gives_ids_and_a_new_category_can_be_chosen_by_its_ref(self):
        setup = self.normalized({"categories": [{"name": "Exams", "weight": 50, "ref": "new-1"}],
                                 "items": [{"title": "Test 1", "points": "100", "category_id": "new-1"}, {"title": "Quiz", "points": 10}]})
        exams, = setup["categories"]
        self.assertTrue(exams["id"].startswith("cat_"))
        self.assertEqual(exams, {"id": exams["id"], "name": "Exams", "weight": "50", "drop_lowest": 0, "aggregate": "points"})
        test, quiz = setup["items"]
        self.assertTrue(test["id"].startswith("item_") and quiz["id"].startswith("item_") and test["id"] != quiz["id"])
        self.assertEqual(test["category_id"], exams["id"])
        self.assertEqual({key: quiz[key] for key in quiz if key != "id"},
                         {"title": "Quiz", "category_id": None, "points": "10", "due_date": "", "counts": True, "extra_credit": False,
                          "archived": False, "sections": [], "assignment_id": None, "link": None, "notes": ""})
        self.assertEqual(list(setup), ["rule", "mode", "categories", "items", "scale", "ungraded", "letter_rounding"])
        self.assertEqual(list(test), ["id", "title", "category_id", "points", "due_date", "counts", "extra_credit", "archived",
                                      "sections", "assignment_id", "link", "notes"])
        # Saved again on itself, the same setup keeps its IDs and is equal.
        self.assertEqual(self.normalized(copy.deepcopy(setup), setup), setup)
        with self.assertRaisesRegex(ValueError, "unusable reference"):
            self.normalized({"categories": [{"name": "A", "ref": "x"}, {"name": "B", "ref": "x"}]})

    def test_an_invented_or_repeated_id_is_refused(self):
        base = setup_of([category("cat_1", "Exams")], [item("item_1", "100", "cat_1")])
        with self.assertRaisesRegex(ValueError, "never gave it"):
            self.normalized({"items": [item("item_new", "5")]}, base)
        with self.assertRaisesRegex(ValueError, "never gave it"):
            self.normalized({"categories": [category("cat_2", "Quizzes")]}, base)
        with self.assertRaisesRegex(ValueError, "never gave it"):  # A category's ID is not a column's.
            self.normalized({"items": [item("cat_1", "5")]}, base)
        with self.assertRaisesRegex(ValueError, "sent twice"):
            self.normalized({"items": [item("item_1", "100", "cat_1"), item("item_1", "100", "cat_1")]}, base)
        with self.assertRaisesRegex(ValueError, "does not have"):
            self.normalized({"items": [item("item_1", "100", "cat_nowhere")]}, base)

    def test_a_scored_column_is_archived_not_removed_and_a_used_category_stays(self):
        base = setup_of([category("cat_1", "Exams"), category("cat_2", "Quizzes")],
                        [item("item_1", "100", "cat_1", title="Test 1"), item("item_2", "10", "cat_2")])
        with self.assertRaisesRegex(ValueError, "“Test 1” has scores and cannot be removed: archive it"):
            self.normalized({"items": [base["items"][1]]}, base, scored_items={"item_1"})
        self.assertEqual([entry["id"] for entry in self.normalized({"items": [base["items"][1]]}, base)["items"]], ["item_2"])
        archived = self.normalized({"items": [{"id": "item_1", "archived": True}, {"id": "item_2"}]}, base, scored_items={"item_1"})
        self.assertTrue(archived["items"][0]["archived"])
        self.assertEqual(archived["items"][0]["title"], "Test 1")  # Fields not sent keep the base revision's values.
        with self.assertRaisesRegex(ValueError, "“Exams” is still used by “Test 1”"):
            self.normalized({"categories": [base["categories"][1]]}, base)
        moved = self.normalized({"categories": [base["categories"][1]], "items": [{"id": "item_1", "category_id": None}, {"id": "item_2"}]}, base)
        self.assertEqual([entry["category_id"] for entry in moved["items"]], [None, "cat_2"])

    def test_limits_and_types(self):
        refused = {
            "Two categories": {"categories": [{"name": "Exams"}, {"name": " exams "}]},
            "at most 60": {"categories": [{"name": "x" * 61}]},
            "is missing": {"categories": [{"name": "  "}]},
            "weight": {"categories": [{"name": "A", "weight": "1000.5"}]},
            "0 to 50": {"categories": [{"name": "A", "drop_lowest": 51}]},
            "can drop": {"categories": [{"name": "A", "drop_lowest": True}]},
            "adds up": {"categories": [{"name": "A", "aggregate": "mean"}]},
            "at most 50 categories": {"categories": [{"name": f"C{n}"} for n in range(51)]},
            "at most 500 columns": {"items": [{"title": f"Q{n}", "points": 1} for n in range(501)]},
            "at most 120": {"items": [{"title": "x" * 121, "points": 1}]},
            "points of “Q”": {"items": [{"title": "Q", "points": "100000.1"}]},
            "ten-thousandths": {"items": [{"title": "Q", "points": "1.00001"}]},
            "more than 0 points": {"items": [{"title": "Q", "points": "0"}]},
            "YYYY-MM-DD": {"items": [{"title": "Q", "points": 1, "due_date": "2026-02-30"}]},
            "due date": {"items": [{"title": "Q", "points": 1, "due_date": "2026-2-3"}]},
            "counts must be yes or no": {"items": [{"title": "Q", "points": 1, "counts": "true"}]},
            "Extra credit": {"items": [{"title": "Q", "points": 1, "extra_credit": 1}]},
            "at most 1000": {"items": [{"title": "Q", "points": 1, "notes": "n" * 1001}]},
            "sections of this term": {"items": [{"title": "Q", "points": 1, "sections": ["sec-9"]}]},
            "not one of this course's": {"items": [{"title": "Q", "points": 1, "assignment_id": "asg-other"}]},
            "points or weighted": {"mode": "percent"},
            "empty cell": {"ungraded": "drop"},
            "0 to 2 decimals": {"letter_rounding": 3},
            "or not at all": {"letter_rounding": False},
            "1 to 30 letters": {"scale": []},
            "another version": {"rule": "gradebook-v0"},
        }
        for message, data in refused.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.normalized(data)
        extra = self.normalized({"items": [{"title": "Bonus", "points": "0", "extra_credit": True, "due_date": "2026-12-01",
                                            "sections": ["sec-2", "sec-1", "sec-2"], "notes": "  kind  "}]})["items"][0]
        self.assertEqual((extra["points"], extra["due_date"], extra["sections"], extra["notes"]), ("0", "2026-12-01", ["sec-1", "sec-2"], "kind"))
        self.assertEqual(self.normalized({"letter_rounding": 0})["letter_rounding"], 0)

    def test_one_logical_setup_has_one_normal_form(self):
        first = setup_of([category("cat_1", "Exams", "50.00", 1)], [item("item_1", "100", "cat_1", link=None)], letter_rounding=None)
        spelled = {**copy.deepcopy(first), "categories": [{"id": "cat_1", "name": "  Exams ", "weight": 50, "drop_lowest": 1, "aggregate": "points"}],
                   "items": [{**first["items"][0], "points": 100.0, "title": " item_1 ", "due_date": None, "assignment_id": ""}],
                   "scale": [{"letter": " A ", "min": 90}, {"letter": "B", "min": "80.0"}, {"letter": "C", "min": "70"},
                             {"letter": "D", "min": 60}, {"letter": "F", "min": "0.0000"}]}
        self.assertEqual(self.normalized(spelled, first), first)
        self.assertEqual(first["categories"][0]["weight"], "50")
        # Review fix: "-0" was kept as the text "-0" (a new revision for no change, and "-0" in cells and the CSV).
        zero = self.normalized({"categories": [{"name": "A", "weight": "-0"}], "items": [{"title": "Bonus", "points": "-0.00", "extra_credit": True}],
                                "scale": [{"letter": "P", "min": "-0"}]})
        self.assertEqual((zero["categories"][0]["weight"], zero["items"][0]["points"], zero["scale"][0]["min"]), ("0", "0", "0"))

    def test_links(self):
        linked = self.normalized({"items": [{"title": "Test 1", "points": 100, "assignment_id": "asg-test1",
                                             "link": {"kind": "grading", "parts": ["exams", "frq"]}}]})["items"][0]
        self.assertEqual(linked["link"], {"kind": "grading", "parts": ["frq", "exams"]})
        refused = {
            "Choose the assignment": {"link": {"kind": "grading", "parts": ["frq"]}},
            "at most once": {"assignment_id": "asg-test1", "link": {"kind": "grading", "parts": ["frq", "frq"]}},
            "at least one": {"assignment_id": "asg-test1", "link": {"kind": "grading", "parts": []}},
            "choose each": {"assignment_id": "asg-test1", "link": {"kind": "grading", "parts": ["gradescope"]}},
            "only be linked": {"assignment_id": "asg-test1", "link": {"kind": "import", "parts": ["frq"]}},
        }
        for message, fields in refused.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.normalized({"items": [{"title": "Q", "points": 1, **fields}]})
        pull = {"assignment_id": "asg-test1", "link": {"kind": "grading", "parts": ["bubble"]}}
        with self.assertRaisesRegex(ValueError, "“A” and “B” both pull from the same assignment"):
            self.normalized({"items": [{"title": "A", "points": 1, **pull}, {"title": "B", "points": 1, **pull}]})
        # An archived column, or one that only references the assignment, does not compete for its pulls.
        both = self.normalized({"items": [{"title": "A", "points": 1, "archived": True, **pull}, {"title": "B", "points": 1, **pull},
                                          {"title": "C", "points": 1, "assignment_id": "asg-test1"}]})
        self.assertEqual([entry["title"] for entry in both["items"]], ["A", "B", "C"])

    def test_the_scale_goes_down_with_unique_letters(self):
        for message, scale in {"lower than the one above": [{"letter": "A", "min": 90}, {"letter": "B", "min": 90}],
                               "from the highest": [{"letter": "B", "min": 80}, {"letter": "A", "min": 90}],
                               "twice": [{"letter": "A", "min": 90}, {"letter": "a", "min": 80}],
                               "at most 4": [{"letter": "Great", "min": 90}],
                               "between 0 and 1000": [{"letter": "A", "min": "-1"}]}.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.normalized({"scale": scale})
        self.assertEqual(self.normalized({"scale": [{"letter": "A+", "min": "96.5"}, {"letter": "P", "min": 50}]})["scale"],
                         [{"letter": "A+", "min": "96.5"}, {"letter": "P", "min": "50"}])

    def test_find_item_and_applies(self):
        setup = setup_of(items=[item("item_1", "5", sections=["sec-2"]), item("item_2", "5")])
        self.assertEqual(find_item(setup, "item_1")["sections"], ["sec-2"])
        self.assertIsNone(find_item(setup, "item_9"))
        self.assertEqual([applies(setup["items"][0], "sec-1"), applies(setup["items"][0], "sec-2"), applies(setup["items"][1], "sec-1")],
                         [False, True, True])


class ComputeTests(unittest.TestCase):
    def test_points_and_weighted_modes(self):
        cats = [category("exams", "Exams", "60"), category("hw", "Homework", "40")]
        items = [item("t1", "100", "exams"), item("h1", "10", "hw"), item("h2", "10", "hw")]
        rows = {"s:0000000001": {"t1": scored("80"), "h1": scored("9"), "h2": scored("10")}}
        points = run(setup_of(cats, items), rows)["students"]["s:0000000001"]
        self.assertEqual((points["percent"], points["letter"], points["earned"], points["possible"]), ("82.50", "B", "99", "120"))
        self.assertEqual(points["categories"]["hw"], {"percent": "95.00", "earned": "19", "possible": "20", "dropped": []})
        weighted = run(setup_of(cats, items, mode="weighted"), rows)["students"]["s:0000000001"]
        self.assertEqual((weighted["percent"], weighted["letter"], weighted["earned"], weighted["possible"]), ("86.00", "B", "99", "120"))

    def test_weights_are_renormalized_over_categories_with_a_result(self):
        cats = [category("exams", "Exams", "60"), category("hw", "Homework", "40"), category("labs", "Labs", "0")]
        items = [item("t1", "100", "exams"), item("h1", "10", "hw"), item("l1", "10", "labs"), item("u1", "50")]
        rows = {"s:0000000001": {"t1": scored("80"), "l1": scored("0"), "u1": scored("50")}}
        weighted = run(setup_of(cats, items, mode="weighted"), rows)["students"]["s:0000000001"]
        # Homework has no score, Labs weighs nothing and Uncategorized is not weighted: Exams alone decides.
        self.assertEqual(weighted["percent"], "80.00")
        self.assertEqual((weighted["earned"], weighted["possible"]), ("130", "160"))  # The points-mode sums, Uncategorized included.
        self.assertEqual(weighted["categories"]["hw"], {"percent": None, "earned": None, "possible": None, "dropped": []})
        points = run(setup_of(cats, items), rows)["students"]["s:0000000001"]
        self.assertEqual(points["percent"], "81.25")  # 130 / 160
        weights = run(setup_of([category("a", "A", "1"), category("b", "B", "3")], [item("a1", "10", "a"), item("b1", "10", "b")], mode="weighted"),
                      {"s:0000000001": {"a1": scored("10"), "b1": scored("5")}})
        self.assertEqual(weights["students"]["s:0000000001"]["percent"], "62.50")  # (1 * 100 + 3 * 50) / 4

    def test_excused_missing_and_empty_cells(self):
        items = [item("a", "10"), item("b", "10"), item("c", "10"), item("d", "10"), item("e", "10")]
        rows = {"s:0000000001": {"a": scored("8"), "b": {"status": "excused", "points": None}, "c": {"status": "missing", "points": None},
                                 "e": {"status": "cleared", "points": None}}}
        left_out = run(setup_of(items=items), rows)["students"]["s:0000000001"]
        self.assertEqual((left_out["percent"], left_out["earned"], left_out["possible"]), ("40.00", "8", "20"))
        zero = run(setup_of(items=items, ungraded="zero"), rows)["students"]["s:0000000001"]
        self.assertEqual((zero["percent"], zero["earned"], zero["possible"]), ("20.00", "8", "40"))  # d and e count as 0; b stays excused.
        nothing = run(setup_of(items=items), {"s:0000000001": {}})["students"]["s:0000000001"]
        self.assertEqual(nothing, {"percent": None, "letter": None, "earned": None, "possible": None, "categories": {}})
        self.assertEqual(run(setup_of(items=items, ungraded="zero"), {"s:0000000001": {}})["students"]["s:0000000001"]["percent"], "0.00")

    def test_empty_cells_count_as_zero_once_due(self):
        # GRADEBOOK "ZERO ONCE DUE" (requirement, 2026-10-06): an empty cell counts 0 once its column is due, unless it waits.
        items = [item("past", "10", due_date="2026-10-01"), item("today", "10", due_date="2026-10-06"), item("later", "10", due_date="2026-10-20"),
                 item("undated", "10"), item("linked", "10", assignment_id="asg-test1", link={"kind": "grading", "parts": ["frq"]}),
                 item("bonus", "5", due_date="2026-10-01", extra_credit=True), item("off", "10", due_date="2026-10-01", counts=False),
                 item("gone", "10", due_date="2026-10-01", archived=True)]
        setup = setup_of(items=items)
        self.assertEqual(setup["ungraded"], "due")
        # Due the day after its due date; a linked column once its plan is certified; never otherwise.
        self.assertEqual(blanks.due_items(setup, "2026-10-06", frozenset()), {"past"})
        self.assertEqual(blanks.due_items(setup, "2026-10-07", {"linked"}), {"past", "today", "linked"})
        # A dated column linked to grading still waits for its certification.
        dated = setup_of(items=[item("linked", "10", due_date="2026-10-01", assignment_id="asg-test1", link={"kind": "grading", "parts": ["frq"]})])
        self.assertEqual(blanks.due_items(dated, "2026-10-07", frozenset()), frozenset())
        replacement = setup_of([category("x", "Exams")], [item("t", "10", "x", due_date="2026-10-01"), item("r", "10", "x", due_date="2026-10-01", replaces_lowest=True)])
        self.assertEqual(blanks.due_items(replacement, "2026-10-07", frozenset()), {"t"})

        students = [{"row": "s:1", "section_id": "sec-1"}, {"row": "s:2", "section_id": "sec-1"}, {"row": "s:3", "section_id": "sec-1"}]
        cells = {"past": {"s:1": scored("8")}, "linked": {"s:1": scored("10")}}
        due = blanks.due_items(setup, "2026-10-07", {"linked"})
        result = compute(setup, students, cells, blanks=(due, {("linked", "s:3"): "makeup", ("past", "s:3"): "dr"}))
        first, second, third = (result["students"][row] for row in ("s:1", "s:2", "s:3"))
        self.assertEqual((first["earned"], first["possible"], first["percent"]), ("18", "30", "60.00"))  # today counts 0; later and undated do not
        self.assertEqual((second["earned"], second["possible"], second["percent"]), ("0", "30", "0.00"))
        self.assertEqual((third["earned"], third["possible"], third["percent"]), ("0", "10", "0.00"))  # only "today": the others wait
        self.assertEqual(result["blanks"], {"s:1": {"today": "zero"}, "s:2": {"past": "zero", "today": "zero", "linked": "zero"},
                                            "s:3": {"past": "dr", "today": "zero", "linked": "makeup"}})
        self.assertEqual(result["items"]["past"]["empty"], 2)  # an empty cell counted as 0 is still an empty cell, never a score
        self.assertEqual(result["items"]["past"]["mean"], "8")
        # Without `blanks` nothing is due, and the other policies keep their results and shape.
        self.assertEqual(compute(setup, students, cells)["blanks"], {})
        self.assertEqual(compute(setup, students, cells)["students"]["s:2"]["percent"], None)
        for policy in ("exclude", "zero"):
            other = compute({**setup, "ungraded": policy}, students, cells, blanks=(due, {}))
            self.assertNotIn("blanks", other)
            self.assertEqual(other, compute({**setup, "ungraded": policy}, students, cells))
        # A section the column is not for has nothing there to count.
        mine = setup_of(items=[item("past", "10", due_date="2026-10-01", sections=["sec-2"])])
        self.assertEqual(compute(mine, students, {}, blanks=(blanks.due_items(mine, "2026-10-07", frozenset()), {}))["blanks"], {})

    def test_an_undated_iclicker_column_is_due_after_its_latest_session(self):
        # GRADEBOOK "ZERO ONCE DUE" (requirement, 2026-10-07): a poll column has no due date; its sessions' campus dates stand in.
        sessions = [{"course_id": "c1", "session_id": "a", "date": "2026-09-23", "item_id": "poll2"},
                    {"course_id": "c2", "session_id": "b", "date": "2026-09-24", "item_id": "poll2"},
                    {"course_id": "c1", "session_id": "skipped", "date": "2026-09-01", "item_id": None}]
        self.assertEqual(blanks.session_dates(sessions), {"poll2": "2026-09-24"})  # the latest section's session
        setup = setup_of(items=[item("poll2", "10"), item("undated", "10"), item("dated", "10", due_date="2026-10-20")])
        held = {"poll2": "2026-09-24", "dated": "2026-09-01"}
        self.assertEqual(blanks.due_items(setup, "2026-09-24", frozenset(), held), frozenset())  # its day is not past due
        self.assertEqual(blanks.due_items(setup, "2026-09-25", frozenset(), held), {"poll2"})  # a due date wins over a session
        self.assertEqual(blanks.due_items(setup, "2026-09-25", frozenset()), frozenset())

    def test_a_column_whose_grades_are_posted_in_canvas_is_due_whatever_its_date(self):
        # GRADEBOOK "ZERO ONCE DUE", POSTED (requirement, 2026-10-08): posted grades make a column due, dated, undated or linked alike.
        items = [item("takehome", "10"), item("later", "10", due_date="2026-10-20"), item("past", "10", due_date="2026-10-01"),
                 item("linked", "10", assignment_id="asg-test1", link={"kind": "grading", "parts": ["frq"]}), item("bonus", "5", extra_credit=True)]
        setup = setup_of(items=items)
        posted = frozenset({"takehome", "later", "linked", "past", "bonus"})
        self.assertEqual(blanks.due_items(setup, "2026-10-08", frozenset()), {"past"})
        self.assertEqual(blanks.due_items(setup, "2026-10-08", frozenset(), posted=posted), {"takehome", "later", "past", "linked"})  # never extra credit
        students = [{"row": "s:1", "section_id": "sec-1"}, {"row": "s:2", "section_id": "sec-1"}]
        cells = {"takehome": {"s:1": scored("10")}}
        due = blanks.due_items(setup, "2026-10-08", frozenset(), posted=posted)
        result = compute(setup, students, cells, blanks=(due, {("linked", "s:2"): "makeup"}, due - {"past"}))
        self.assertEqual(result["blanks"]["s:2"], {"takehome": "zero", "later": "zero", "past": "zero", "linked": "makeup"})  # a makeup still waits
        self.assertEqual(result["due_posted"], ["takehome", "later", "linked"])  # the setup's order; "past" was due by its date
        self.assertNotIn("due_posted", compute(setup, students, cells, blanks=(due, {}, frozenset())))  # only when there is one
        self.assertNotIn("due_posted", compute(setup, students, cells, blanks=(due, {})))

    def test_extra_credit_adds_earned_points_only(self):
        items = [item("h1", "10", "hw"), item("h2", "5", "hw"), item("x1", "5", "hw", extra_credit=True), item("x0", "0", "hw", extra_credit=True),
                 item("x2", "5", "hw", extra_credit=True)]
        rows = {"s:0000000001": {"h1": scored("8"), "h2": scored("5"), "x1": scored("2.5"), "x0": scored("1"), "x2": {"status": "missing", "points": None}}}
        points = run(setup_of([category("hw", "Homework")], items, ungraded="zero"), rows)["students"]["s:0000000001"]
        self.assertEqual(points["categories"]["hw"], {"percent": "110.00", "earned": "16.5", "possible": "15", "dropped": []})
        # Equal weight: mean(80, 100) + (2.5 / 5 * 100) / 2; the 0-point column has no percentage and adds nothing there.
        equal = run(setup_of([category("hw", "Homework", aggregate="equal")], items), rows)["students"]["s:0000000001"]
        self.assertEqual(equal["categories"]["hw"]["percent"], "115.00")
        self.assertEqual((equal["earned"], equal["possible"]), ("16.5", "15"))
        # A category of extra credit only has no result, but its points still add to the points-mode total.
        bonus = setup_of([category("hw", "Homework", "100"), category("bonus", "Bonus", "100")], [item("h1", "10", "hw"), item("x1", "5", "bonus", extra_credit=True)])
        result = run(bonus, {"s:0000000001": {"h1": scored("8"), "x1": scored("1")}})["students"]["s:0000000001"]
        self.assertEqual(result["categories"]["bonus"], {"percent": None, "earned": "1", "possible": "0", "dropped": []})
        self.assertEqual(result["percent"], "90.00")
        self.assertEqual(run({**bonus, "mode": "weighted"}, {"s:0000000001": {"h1": scored("8"), "x1": scored("1")}})["students"]["s:0000000001"]["percent"], "80.00")

    def test_a_points_drop_keeps_the_best_ratio_not_the_best_percentages(self):
        items = [item("a", "10", "q"), item("b", "1", "q"), item("c", "100", "q")]
        rows = {"s:0000000001": {"a": scored("9"), "b": scored("0"), "c": scored("40")}}
        points = run(setup_of([category("q", "Quizzes", drop=1)], items), rows)["students"]["s:0000000001"]["categories"]["q"]
        # Dropping the lowest percentage (b, 0%) would leave 49/110 = 44.55%; dropping c leaves 9/11.
        self.assertEqual(points, {"percent": "81.82", "earned": "9", "possible": "11", "dropped": ["c"]})
        equal = run(setup_of([category("q", "Quizzes", drop=1, aggregate="equal")], items), rows)["students"]["s:0000000001"]["categories"]["q"]
        self.assertEqual(equal, {"percent": "65.00", "earned": "49", "possible": "110", "dropped": ["b"]})

    def test_points_drops_match_every_possible_choice(self):
        generator = random.Random(140)
        for trial in range(300):
            count, drop = generator.randint(1, 7), generator.randint(1, 4)
            entries = [(generator.randint(0, 12), generator.randint(1, 12)) for _ in range(count)]
            bonus = generator.choice([0, 0, 3])
            items = [item(f"i{n}", str(possible), "q") for n, (_, possible) in enumerate(entries)] + [item("x", "5", "q", extra_credit=True)]
            cells = {f"i{n}": scored(str(earned)) for n, (earned, _) in enumerate(entries)}
            cells["x"] = scored(str(bonus))
            got = run(setup_of([category("q", "Q", drop=drop)], items), {"s:0000000001": cells})["students"]["s:0000000001"]["categories"]["q"]
            keep = max(count - drop, 1)
            best = max(Fraction(bonus + sum(entries[n][0] for n in kept), sum(entries[n][1] for n in kept))
                       for kept in itertools.combinations(range(count), keep))
            with self.subTest(trial=trial, entries=entries, drop=drop, bonus=bonus):
                self.assertEqual(got["percent"], format(rounded(best * 100, 2), "f"))
                self.assertEqual(len(got["dropped"]), count - keep)

    def test_ties_drop_the_later_column_and_the_last_entry_stays(self):
        items = [item("a", "10", "q"), item("b", "10", "q"), item("c", "10", "q")]
        rows = {"s:0000000001": {"a": scored("5"), "b": scored("5"), "c": scored("10")}}
        for aggregate in ("points", "equal"):
            result = run(setup_of([category("q", "Q", drop=1, aggregate=aggregate)], items), rows)["students"]["s:0000000001"]["categories"]["q"]
            self.assertEqual((result["percent"], result["dropped"]), ("75.00", ["b"]), aggregate)
        # Equal ratios with different points: all three optimal drops leave 50%; the latest column goes.
        same = run(setup_of([category("q", "Q", drop=1)], [item("a", "2", "q"), item("b", "4", "q"), item("c", "8", "q")]),
                   {"s:0000000001": {"a": scored("1"), "b": scored("2"), "c": scored("4")}})["students"]["s:0000000001"]["categories"]["q"]
        self.assertEqual((same["percent"], same["dropped"]), ("50.00", ["c"]))
        many = run(setup_of([category("q", "Q", drop=5)], items), {"s:0000000001": {"a": scored("5"), "c": scored("9")}})
        self.assertEqual(many["students"]["s:0000000001"]["categories"]["q"], {"percent": "90.00", "earned": "9", "possible": "10", "dropped": ["a"]})
        one = run(setup_of([category("q", "Q", drop=5)], items), {"s:0000000001": {"b": scored("3")}})
        self.assertEqual(one["students"]["s:0000000001"]["categories"]["q"]["dropped"], [])

    def test_a_replacement_takes_the_lowest_score_only_when_higher(self):
        cats = [category("exams", "Exams", "48"), category("final", "Final", "20")]
        items = [item("e1", "100", "exams"), item("e2", "100", "exams"), item("e3", "100", "exams"),
                 item("rep", "50", "exams", replaces_lowest=True), item("f", "100", "final")]
        setup = setup_of(cats, items, mode="weighted")
        base = {"e1": scored("90"), "e2": scored("60"), "e3": scored("80"), "f": scored("70")}
        # 40/50 = 80% replaces Exam 2's 60%: (90 + 80 + 80) / 300; the replacement's own points never count.
        used = run(setup, {"s:0000000001": {**base, "rep": scored("40")}})["students"]["s:0000000001"]
        self.assertEqual(used["categories"]["exams"], {"percent": "83.33", "earned": "250", "possible": "300", "dropped": [],
                                                       "replaced": [{"item": "e2", "by": "rep"}]})
        # Not higher, missing, empty or excused: nothing changes, even when empty cells count as zero.
        for cell in (scored("30"), scored("25"), {"status": "missing", "points": None}, {"status": "excused", "points": None}, None):
            for policies in ({}, {"ungraded": "zero"}):
                cells = {**base, **({"rep": cell} if cell else {})}
                got = run(setup_of(cats, items, mode="weighted", **policies), {"s:0000000001": cells})["students"]["s:0000000001"]
                self.assertEqual(got["categories"]["exams"], {"percent": "76.67", "earned": "230", "possible": "300", "dropped": []}, (cell, policies))
        # Ties among the lowest go to the later column; a fraction of a point is kept exactly; drops come after.
        tie = run(setup, {"s:0000000001": {"e1": scored("60"), "e2": scored("60"), "e3": scored("90"), "rep": scored("33.3333")}})
        self.assertEqual(tie["students"]["s:0000000001"]["categories"]["exams"]["replaced"], [{"item": "e2", "by": "rep"}])
        self.assertEqual(tie["students"]["s:0000000001"]["categories"]["exams"]["earned"], "216.6666")
        dropped = run(setup_of([category("exams", "Exams", "48", drop=1)], items[:4]), {"s:0000000001": {**base, "rep": scored("45")}})
        self.assertEqual(dropped["students"]["s:0000000001"]["categories"]["exams"], {"percent": "90.00", "earned": "180", "possible": "200",
                                                                                   "dropped": ["e3"], "replaced": [{"item": "e2", "by": "rep"}]})
        # Nothing to replace yet: the replacement alone gives the category no result.
        alone = run(setup, {"s:0000000001": {"rep": scored("50")}})["students"]["s:0000000001"]
        self.assertEqual(alone["categories"]["exams"]["percent"], None)

    def test_a_replacement_is_checked(self):
        cats = [category("exams", "Exams")]
        stored = setup_of(cats, [item("e1", "100", "exams"), item("rep", "100", "exams", replaces_lowest=True)])
        self.assertTrue(stored["items"][1]["replaces_lowest"])
        self.assertNotIn("replaces_lowest", stored["items"][0])  # Stored only when true: earlier setups keep their form.
        self.assertNotIn("replaces_lowest", setup_of(cats, [item("e1", "100", "exams", replaces_lowest=False)])["items"][0])
        for items, message in (([item("rep", "100", None, replaces_lowest=True)], "choose its category"),
                               ([item("rep", "100", "exams", replaces_lowest=True, extra_credit=True)], "extra credit"),
                               ([item("r1", "100", "exams", replaces_lowest=True), item("r2", "100", "exams", replaces_lowest=True)], "keep one"),
                               ([item("rep", "100", "exams", replaces_lowest="yes")], "yes or no")):
            with self.assertRaisesRegex(ValueError, message):
                setup_of(cats, items)
        setup_of(cats, [item("r1", "100", "exams", replaces_lowest=True), item("r2", "100", "exams", replaces_lowest=True, archived=True)])

    def test_sections_counts_and_archived_columns(self):
        items = [item("all", "10"), item("only2", "10", sections=["sec-2"]), item("shown", "10", counts=False), item("old", "10", archived=True)]
        students = [{"row": "sec-1:0000000001", "section_id": "sec-1"}, {"row": "sec-2:0000000002", "section_id": "sec-2"}]
        cells = {"all": {"sec-1:0000000001": scored("10"), "sec-2:0000000002": scored("5")},
                 "only2": {"sec-1:0000000001": scored("0"), "sec-2:0000000002": scored("10")},
                 "shown": {"sec-1:0000000001": scored("0")}, "old": {"sec-1:0000000001": scored("0")}}
        result = compute(setup_of(items=items, ungraded="zero"), students, cells)
        self.assertEqual(result["students"]["sec-1:0000000001"]["percent"], "100.00")  # only2 is not sec-1's; shown and old never count.
        self.assertEqual(result["students"]["sec-2:0000000002"]["percent"], "75.00")
        self.assertEqual((result["items"]["only2"]["scored"], result["items"]["only2"]["empty"], result["items"]["only2"]["mean"]), (1, 0, "10"))
        self.assertEqual((result["items"]["shown"]["scored"], result["items"]["shown"]["empty"]), (1, 1))  # Statistics still cover them.
        self.assertEqual(result["items"]["old"]["low"], "0")

    def test_letters_rounding_and_the_floor(self):
        items = [item("a", "200")]
        rows = {"s:0000000001": {"a": scored("179")}, "s:0000000002": {"a": scored("179.99")}, "s:0000000003": {"a": scored("20")}}
        exact = run(setup_of(items=items), rows)
        self.assertEqual([exact["students"][row]["letter"] for row in rows], ["B", "B", "F"])
        # The exact percentage decides, so it is shown cut, not rounded: 89.995 shows as 89.99 beside its B, never 90.00.
        self.assertEqual([exact["students"][row]["percent"] for row in rows], ["89.50", "89.99", "10.00"])
        whole = run(setup_of(items=items, letter_rounding=0), rows)
        self.assertEqual([whole["students"][row]["letter"] for row in rows], ["A", "A", "F"])
        hundredths = run(setup_of(items=items, letter_rounding=2), {"s:0000000001": {"a": scored("179.98")}, "s:0000000002": {"a": scored("179.99")}})
        self.assertEqual([hundredths["students"][row]["letter"] for row in ("s:0000000001", "s:0000000002")], ["B", "A"])  # 89.99 and 89.995 -> 90.00
        self.assertEqual([hundredths["students"][row]["percent"] for row in ("s:0000000001", "s:0000000002")], ["89.99", "90.00"])
        pass_fail = run(setup_of(items=items, scale=[{"letter": "P", "min": "70"}, {"letter": "NP", "min": "50"}]), rows)
        self.assertEqual([pass_fail["students"][row]["letter"] for row in rows], ["P", "P", "NP"])  # Below the last minimum: the floor letter.
        self.assertEqual(pass_fail["overall"]["letters"], {"P": 2, "NP": 1})

    def test_statistics(self):
        cats = [category("q", "Quizzes")]
        items = [item("q1", "8", "q"), item("q2", "0", "q", extra_credit=True)]
        rows = {"s:0000000001": {"q1": scored("8"), "q2": scored("1")}, "s:0000000002": {"q1": scored("5")},
                "s:0000000003": {"q1": scored("2.5")}, "s:0000000004": {"q1": scored("4")},
                "s:0000000005": {"q1": {"status": "missing", "points": None}}, "s:0000000006": {"q1": {"status": "excused", "points": None}},
                "s:0000000007": {}}
        result = run(setup_of(cats, items), rows)
        self.assertEqual(result["items"]["q1"], {"scored": 4, "excused": 1, "missing": 1, "empty": 1, "mean": "4.875", "median": "4.5",
                                                 "low": "2.5", "high": "8", "mean_percent": "60.94"})
        self.assertEqual(result["items"]["q2"], {"scored": 1, "excused": 0, "missing": 0, "empty": 6, "mean": "1", "median": "1",
                                                 "low": "1", "high": "1", "mean_percent": None})
        # Students with a result: 112.5, 62.5, 31.25, 50 and 0 (missing); the excused and the empty student have none.
        self.assertEqual(result["categories"]["q"], {"mean_percent": "51.25", "students": 5})
        self.assertEqual(result["overall"]["students"], 5)
        self.assertEqual(result["overall"]["mean_percent"], "51.25")
        self.assertEqual(result["overall"]["letters"], {"A": 1, "B": 0, "C": 0, "D": 1, "F": 3})
        self.assertEqual(result["students"]["s:0000000006"]["categories"]["q"], {"percent": None, "earned": None, "possible": None, "dropped": []})

    def test_weighted_columns_without_a_category_are_named(self):
        cats = [category("ex", "Exams", "100")]
        items = [item("e1", "10", "ex"), item("q1", "10"), item("x1", "5", extra_credit=True),
                 item("n1", "10", counts=False), item("a1", "10", archived=True)]
        rows = {"s:0000000001": {"e1": scored("8"), "q1": scored("10")}}
        setup = copy.deepcopy(setup_of(cats, items, mode="weighted"))
        setup["items"].insert(2, {**setup["items"][1], "id": "q2", "title": "q2", "category_id": "gone"})  # a category the setup lost
        weighted = run(setup, rows)
        # No category, or one the setup does not know: counted columns only, in the setup's order (extra credit counts nothing either).
        self.assertEqual(weighted["uncounted"], ["q1", "q2", "x1"])
        self.assertEqual(weighted["students"]["s:0000000001"]["percent"], "80.00")  # the quiz's 10/10 changes nothing
        self.assertNotIn("uncounted", run(setup_of(cats, items), rows))  # total points: Uncategorized counts
        self.assertNotIn("uncounted", run(setup_of(cats, items[:1], mode="weighted"), rows))  # none: the shape as before

    def test_an_empty_setup(self):
        result = compute(default_setup(), [{"row": "s:0000000001", "section_id": "sec-1"}], {})
        self.assertEqual(result, {"students": {"s:0000000001": {"percent": None, "letter": None, "earned": None, "possible": None, "categories": {}}},
                                  "items": {}, "categories": {},
                                  "overall": {"mean_percent": None, "students": 0, "letters": {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}},
                                  "blanks": {}})
        self.assertNotIn("blanks", compute({**default_setup(), "ungraded": "exclude"}, [{"row": "s:0000000001", "section_id": "sec-1"}], {}))
        self.assertEqual(compute(default_setup(), [], {})["overall"]["students"], 0)

    def test_a_large_term_is_fast(self):
        generator = random.Random(7)
        cats = [category(f"c{n}", f"C{n}", str(n + 1), drop=2, aggregate=("points", "equal")[n % 2]) for n in range(5)]
        items = [item(f"i{n}", str(generator.choice([5, 10, 12.5, 20, 100])), f"c{n % 5}", extra_credit=n % 17 == 0) for n in range(60)]
        setup = setup_of(cats, items, ungraded="zero", mode="weighted")
        students = [{"row": f"sec-1:{n:010d}", "section_id": "sec-1"} for n in range(400)]
        cells = {entry["id"]: {student["row"]: scored(str(generator.randint(0, int(float(entry["points"]))))) for student in students
                               if generator.random() < 0.9} for entry in setup["items"]}
        started = time.perf_counter()
        result = compute(setup, students, cells)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(result["students"]), 400)
        self.assertLess(elapsed, 1.0, f"400 students x 60 columns took {elapsed:.3f} s")



class PointsTextTests(unittest.TestCase):
    def test_points_are_plain_decimal_digits(self):
        from gradebook.common import parse_points
        self.assertEqual([parse_points(v) for v in ("0", ".5", "5.", " 12.25 ", 10, 2.5, "-0")], ["0", "0.5", "5", "12.25", "10", "2.5", "0"])
        for value in ("1_0", "1e2", "٣", "0x10", "1,000", "", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be a number of points"):
                parse_points(value)
        with self.assertRaisesRegex(ValueError, "between 0 and"):
            parse_points("-3")


if __name__ == "__main__":
    unittest.main()
