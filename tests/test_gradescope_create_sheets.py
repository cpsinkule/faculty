"""THE BUBBLE-SHEET EXAM TOO (GRADESCOPE; requirement, 2026-10-26), offline against FakeGradescope.

The fixture is the creation tests' (test_gradescope_create): section 501 on Versions A and B, its Gradescope course 140-502 empty.
With an answer key saved for the plan, the same Create also makes Gradescope's Bubble Sheet named "<title> - Multiple Choice" in that
course: its versions added on the answer key editor, every version's key saved and read back, and the section linked to it, so the
Bubble sheets page's send goes there. The FakeGradescope answers as the real site did in the sandbox course on 2026-10-26.
"""
import json
import uuid

from werkzeug.exceptions import Conflict

from grading.gradescope_rubric_import import bubble_questions
from rosters import connect
from test_gradescope_create import CreateCase

KEY = {"defaults": {"points": "4", "scoring": "exact"},
       "versions": [{"letter": "A", "questions": [{"number": 1, "answer": "B"}, {"number": 2, "answer": "AC", "scoring": "partial", "points": "2.5"},
                                                  {"number": 3, "answer": "D"}]},
                    {"letter": "B", "questions": [{"number": 1, "answer": "E"}, {"number": 2, "answer": "BD", "scoring": "either"},
                                                  {"number": 3, "answer": "A", "points": "1"}]}]}


class SheetCase(CreateCase):
    def setUp(self):
        super().setUp()
        self.sheet_title = f"{self.title} - Multiple Choice"

    def save_key(self, key=KEY):
        with connect(self.path) as db:
            base = db.execute("SELECT MAX(revision) FROM frq_bubble_keys WHERE assignment_id=?", (self.aid,)).fetchone()[0]
        self.frq.save_bubble_key(self.aid, {"request_id": str(uuid.uuid4()), "base_revision": base, **key})

    def finish(self, rounds=12):
        for _ in range(rounds):
            self.sender.drain()
            self.soon()

    def sheet_exam(self):
        """The bubble-sheet set Faculty made in 140-502, and {letter: version}."""
        container = next(a for a in self.fake.assignments.values() if a["container"] and a["title"] == self.sheet_title and a["course"] == self.empty)
        return container, {a["version"]: a for a in self.fake.assignments.values() if a["parent"] == container["id"]}

    def sheet_link(self):
        with connect(self.path) as db:
            row = db.execute("SELECT * FROM frq_gradescope_sheet_targets WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                             (self.aid,)).fetchone()
        return dict(row) if row else None


class SheetTests(SheetCase):
    def test_without_an_answer_key_the_preview_is_what_it_was(self):
        view = self.create_view()
        self.assertNotIn("sheets", view)
        self.create(view)
        self.finish()
        self.assertEqual([u["status"] for u in self.creation_units()], ["complete"])
        self.assertFalse(any(a["bubble_sheet"] for a in self.fake.assignments.values() if a["course"] == self.empty))
        self.assertIsNone(self.sheet_link())

    def test_the_preview_shows_the_bubble_sheet_exam_and_writes_nothing(self):
        self.save_key()
        view = self.create_view()
        self.assertTrue(view["ready"], view)
        sheets = view["sheets"]
        self.assertEqual(sheets["title"], self.sheet_title)
        self.assertEqual([(v["letter"], v["title"], v["questions"], v["points"]) for v in sheets["versions"]],
                         [("A", f"{self.sheet_title} Version A", 3, "10.5"), ("B", f"{self.sheet_title} Version B", 3, "9")])
        self.assertEqual([(c["course_id"], c["sections"], c["problem"]) for c in sheets["courses"]], [(str(self.empty), [self.section["number"]], "")])
        self.assertEqual(sheets["problems"], [])
        self.assertEqual(self.writes(), [])
        # Its own name, when given.
        self.assertEqual(self.create_view(sheet_title="Exam MC")["sheets"]["versions"][0]["title"], "Exam MC Version A")
        self.assertNothingForbidden()

    def test_create_makes_both_in_one_unit_links_the_sheets_and_the_key_reads_back(self):
        self.save_key()
        self.create()
        self.finish()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        self.assertEqual(unit["step"], f"Made {self.title} and {self.sheet_title}")
        self.made()  # the written set is there, as without a key
        container, versions = self.sheet_exam()
        self.assertEqual(sorted(versions), ["A", "B"])
        self.assertEqual({letter: v["title"] for letter, v in versions.items()},
                         {"A": f"{self.sheet_title} Version A", "B": f"{self.sheet_title} Version B"})
        self.assertEqual(self.fake.bubble_key(versions["A"]["id"]), {1: ("B", "exact", 4.0), 2: ("AC", "partial", 2.5), 3: ("D", "exact", 4.0)})
        self.assertEqual(self.fake.bubble_key(versions["B"]["id"]), {1: ("E", "exact", 4.0), 2: ("BD", "either", 4.0), 3: ("A", "exact", 1.0)})
        self.assertEqual(len(self.fake.key_saves), 1)  # every version's key in one Save
        # The key Gradescope now holds imports back into Faculty's exactly (the rubric import's own reader).
        session = self.sender.session_factory()
        try:
            for version in KEY["versions"]:
                found, problems = bubble_questions(session.outline(str(self.empty), versions[version["letter"]]["id"]))
                self.assertEqual(problems, [])
                self.assertEqual([(q["number"], q["answer"]) for q in found], [(q["number"], q["answer"]) for q in version["questions"]])
        finally:
            session.close()
        link = self.sheet_link()
        self.assertEqual((link["section_id"], link["container_id"], link["container_title"], json.loads(link["versions_json"])),
                         (self.section["id"], str(container["id"]), self.sheet_title, {k: str(v["id"]) for k, v in versions.items()}))
        # The Bubble sheets page shows it as the section's linked exam, which its send goes to.
        [target] = self.sender.sheet_status(self.aid)["targets"]
        self.assertEqual((target["section_id"], target["course_id"], target["container_id"], target["container_title"]),
                         (self.section["id"], str(self.empty), str(container["id"]), self.sheet_title))
        self.assertNothingForbidden()

    def test_only_the_bubble_sheet_exam_when_the_written_one_is_already_there(self):
        self.create()
        self.finish()
        self.save_key()
        view = self.sender.create_preview(self.aid, {})  # as the panel opens it: each section's saved link
        self.assertTrue(view["ready"], view)
        self.assertEqual((view["courses"], view["versions"], view["problems"]), ([], [], []))
        self.assertEqual(len(view["sheets"]["courses"]), 1)
        self.sender.create(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "create", "fingerprint": view["fingerprint"]})
        self.finish()
        latest = self.creation_units()[-1]
        self.assertEqual(latest["status"], "complete", latest["error"])
        self.assertEqual(latest["step"], f"Made {self.sheet_title}")
        self.sheet_exam()
        # Nothing of the written set was made twice.
        self.assertEqual(len([a for a in self.fake.assignments.values() if a["title"] == self.title]), 1)
        # Once linked, there is nothing more to make.
        view = self.sender.create_preview(self.aid, {})
        self.assertFalse(view["ready"])
        self.assertEqual((view["sheets"]["courses"], view["sheets"]["linked"]), ([], [self.section["number"]]))

    def test_what_the_written_one_lacks_does_not_hold_up_the_bubble_sheets(self):
        self.save_key()
        view = self.create_view()
        planned = self.sender._creation_plan(self.aid, {"targets": self.targets})
        self.assertEqual(planned["view"]["fingerprint"], view["fingerprint"])
        # A written problem (made here by refusing every version) drops only the written part.
        from unittest.mock import patch
        broken = self.sender._faculty_assignment(self.aid)
        broken["problems"] = [(None, "This assignment has no questions yet.")]
        with patch.object(self.sender, "_faculty_assignment", return_value=broken):
            view = self.create_view()
            self.assertTrue(view["ready"], view)
            self.assertEqual(view["problems"], ["This assignment has no questions yet."])
            self.create(view)
        self.finish()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        self.sheet_exam()
        self.assertFalse(any(a["title"] == self.title for a in self.fake.assignments.values()))

    def test_a_key_of_one_version_is_not_made_but_the_written_one_is(self):
        self.save_key({**KEY, "versions": KEY["versions"][:1]})
        view = self.create_view()
        self.assertTrue(view["ready"])
        self.assertIn("with versions only", view["sheets"]["problems"][0])
        self.create(view)
        self.finish()
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        self.assertNotIn("sheets", json.loads(unit["plan_json"]))
        self.assertFalse(any(a["bubble_sheet"] for a in self.fake.assignments.values() if a["course"] == self.empty))

    def test_a_name_already_in_the_course_is_refused_for_the_bubble_sheets_only(self):
        self.save_key()
        self.fake.add_assignment(self.empty, f"{self.sheet_title} Version B", 2)
        view = self.create_view()
        self.assertIn("already has an assignment named", view["sheets"]["courses"][0]["problem"])
        self.assertTrue(view["ready"])  # the written one is still made
        self.create(view)
        self.finish()
        [unit] = self.creation_units()
        self.assertNotIn("sheets", json.loads(unit["plan_json"]))
        view = self.create_view(sheet_title="Exam MC")
        self.assertEqual(view["sheets"]["courses"][0]["problem"], "")

    def test_faculty_never_makes_it_by_itself(self):
        """After certifying (linked_only) and for a makeup version, the bubble-sheet exam is never planned."""
        self.save_key()
        self.assertNotIn("sheets", self.sender._creation_plan(self.aid, {"targets": self.targets}, linked_only=True)["view"])
        self.assertNotIn("sheets", self.sender._creation_plan(self.aid, {"targets": self.targets}, makeup="C")["view"])

    def test_a_write_whose_answer_was_lost_is_read_again_and_never_made_twice(self):
        self.save_key()
        self.fake.fail("POST", r"^/courses/\d+/assignments$", "drop_after", times=3)
        self.fake.fail("POST", r"/bubble_sheet_answer_key$", "drop_after")
        self.fake.fail("PATCH", r"/bubble_sheet_answer_key$", "drop_after")
        self.create()
        self.finish(16)
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "complete", unit["error"])
        _, versions = self.sheet_exam()
        self.assertEqual(sorted(versions), ["A", "B"])  # the lost Add Version was read again, not sent twice
        self.assertEqual(len([a for a in self.fake.assignments.values() if a["title"].startswith(self.sheet_title)]), 3)
        self.assertEqual(self.fake.bubble_key(versions["B"]["id"])[3], ("A", "exact", 1.0))

    def test_a_key_edited_on_gradescope_meanwhile_stops_for_the_instructor(self):
        from unittest.mock import patch
        from grading import gradescope_web as gw
        self.save_key()
        self.create()
        reading, edited = gw.Session.bubble_key_editor, []

        def someone_edits_it_first(session, course, kind, target):
            # Once the set has both versions, someone types a key into Version A on Gradescope before Faculty saves its own.
            if kind == "assignment_container" and not edited and len([a for a in self.fake.assignments.values() if a["parent"] == int(target)]) == 2:
                edited.append(target)
                _, versions = self.sheet_exam()
                self.fake._set_key(versions["A"]["id"], {"1": {"answer": ["C"], "scoring_type": "exact", "weight": 4.0}})
            return reading(session, course, kind, target)
        with patch.object(gw.Session, "bubble_key_editor", someone_edits_it_first):
            self.finish()
        self.assertTrue(edited)
        [unit] = self.creation_units()
        self.assertEqual(unit["status"], "failed")
        self.assertIn("another answer key", unit["error"])
        self.assertEqual(self.fake.key_saves, [])  # Faculty saved nothing over it
        self.assertIsNone(self.sheet_link())

    def test_a_create_request_without_the_second_name_hashes_as_before(self):
        self.save_key()
        view = self.create_view()
        request = {"request_id": str(uuid.uuid4()), "confirmation": "create", "fingerprint": view["fingerprint"], "targets": self.targets}
        self.sender.create(self.aid, request)
        self.assertEqual(self.sender.create(self.aid, request)["status"], "pending")  # the same request, recorded once
        with self.assertRaises(Conflict):
            self.sender.create(self.aid, {**request, "sheet_title": "Another name"})
