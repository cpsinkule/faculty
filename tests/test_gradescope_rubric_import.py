"""Importing rubrics straight from Gradescope (GRADESCOPE "IMPORTING RUBRICS FROM GRADESCOPE"), offline against FakeGradescope.

The sender's fixture (test_gradescope_send): section 501's Gradescope course has "Common Exam 1 - Partial Credit" with Versions A, B
and C, each with questions 18 and 19 (not in Faculty) and 20 and 21 (already imported from the export). Importing reads Gradescope
only, makes the export's own CSV of each chosen question and uploads it as a CSV upload would, so the send matches its items.
"""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict

from grading import gradescope_import, gradescope_rubric_import as gri, gradescope_web as gw, locations
from grading.gradescope_sync import map_question
from rosters import connect
from test_assignment_templates import prepared
from test_gradescope_send import HEADERS, GradescopeCase, Q21


class RubricImportTests(GradescopeCase):
    def setUp(self):
        super().setUp()
        # Template conversion runs in its sandbox in production; here it is the template tests' synthetic two-page conversion.
        self.enterContext(patch("document_identity.template_conversion.convert_template", side_effect=prepared))

    def import_(self, chosen, **extra):
        return self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course),
                                                    "gradescope_assignment_id": str(self.container), "questions": chosen, **extra})

    def preview(self, **data):
        return self.sender.rubric_import_preview(self.aid, data)

    def writes(self):
        return [r for r in self.fake.requests if r["method"] in ("POST", "PATCH", "DELETE", "PUT") and r["path"] != "/login"]

    def ids(self, view, letter, *titles):
        version = next(v for v in view["versions"] if v["letter"] == letter)
        return [q["id"] for q in version["questions"] if q["title"] in titles]

    def faculty_question(self, letter, printed):
        with connect(self.path) as db:
            for (qid,) in db.execute("SELECT question_id FROM frq_assignment_questions WHERE assignment_id=?", (self.aid,)).fetchall():
                question = self.frq.question(db, self.aid, qid)
                if question["exam_version"] == letter and str(question["content"].get("printed_number")) == printed:
                    return question
        return None

    def test_preview_proposes_the_linked_course_and_the_sent_assignment_and_writes_nothing(self):
        self.send()  # links section 501 to its course and the Partial Credit set
        view = self.preview()
        self.assertEqual(view["course_id"], str(self.course))
        self.assertEqual(view["gradescope_assignment_id"], str(self.container))
        self.assertEqual([c["id"] for c in view["courses"] if c["coordinator"]], [str(self.forbidden)])  # listed, read only when chosen
        self.assertEqual([v["letter"] for v in view["versions"]], ["A", "B", "C"])
        questions = {q["title"]: q for q in view["versions"][0]["questions"]}
        self.assertEqual(questions["Question 18"]["printed_number"], "18")
        self.assertTrue(questions["Question 18"]["importable"])
        self.assertFalse(questions["Question 18"]["in_faculty"])
        self.assertTrue(questions["Question 20"]["in_faculty"])
        self.assertNotIn("source", questions["Question 18"])
        before = len(self.writes())
        self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container))
        self.assertEqual(len(self.writes()), before)
        self.assertNothingForbidden()

    def test_import_makes_draft_questions_whose_items_the_send_matches(self):
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container))
        chosen = self.ids(view, "A", "Question 18", "Question 19") + self.ids(view, "B", "Question 18")
        request = str(uuid.uuid4())
        data = {"request_id": request, "course_id": str(self.course), "gradescope_assignment_id": str(self.container), "questions": chosen}
        result = self.sender.rubric_import(self.aid, data)
        self.assertEqual([r["status"] for r in result["results"]], ["imported"] * 3, result)
        self.assertEqual([(r["exam_version"], r["printed_number"]) for r in result["results"]], [("A", "18"), ("A", "19"), ("B", "18")])
        self.assertEqual(self.writes(), [])  # a read of Gradescope only
        question = self.faculty_question("A", "18")
        self.assertEqual(question["content"]["max_points"], 2)
        self.assertEqual([(i["description"], i["points"]) for i in question["rubric"]["rules"]["items"]], [("Correct", 2), ("Incorrect", 0)])
        self.assertEqual([json.loads(i["original_source_text"])["rubric_number"] for i in question["rubric"]["rules"]["items"]], ["1.1", "1.2"])
        rubric = self.sender_rubric("A")
        mapping, warnings = map_question(question, rubric)
        self.assertIsNotNone(mapping, warnings)
        self.assertEqual(warnings, [])
        # The same request again repeats nothing; another request finds the same rubric already imported.
        again = self.sender.rubric_import(self.aid, data)
        self.assertEqual([r["status"] for r in again["results"]], ["already_imported"] * 3)
        other = self.sender.rubric_import(self.aid, {**data, "request_id": str(uuid.uuid4())})
        self.assertEqual([r["status"] for r in other["results"]], ["already_imported"] * 3)
        self.assertNothingForbidden()

    def sender_rubric(self, letter):
        session = self.sender.session_factory()
        try:
            return session.rubric(str(self.course), str(self.versions[letter]))
        finally:
            session.close()

    def test_the_template_and_boxes_come_with_the_rubric(self):
        """Gradescope's template becomes one of the assignment's Assignment Templates (once per template file) and each question's
        boxes its answer areas on it, which the answer-area check then suggests as they are."""
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container))
        version = next(v for v in view["versions"] if v["letter"] == "A")
        self.assertEqual(version["template"], {"pages": 2, "filename": "template.pdf", "ready": True})
        self.assertEqual({q["title"]: q["areas"] for q in version["questions"]}["Question 18"], 1)
        with connect(self.path) as db:
            before = db.execute("SELECT COUNT(*) FROM assignment_templates").fetchone()[0]
        result = self.import_(self.ids(view, "A", "Question 18", "Question 19"))
        self.assertEqual([(r["status"], r["areas"]) for r in result["results"]], [("imported", 1), ("imported", 1)], result)
        self.assertEqual(len(result["templates"]), 1)
        self.assertEqual(result["notes"], [])
        question = self.faculty_question("A", "18")
        self.assertEqual(question["content"]["template_id"], result["templates"][0])
        self.assertEqual(question["content"]["regions_from"], "gradescope")
        region = question["content"]["regions"][0]
        self.assertEqual(region["page"], 1)
        self.assertEqual({k: round(v, 6) for k, v in region["region"].items()}, {"x": .1, "y": .1, "width": .8, "height": .3})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignment_templates").fetchone()[0], before + 1)
            context = self.frq.location_context(db, self.aid, question)
        self.assertEqual(context["template"]["id"], result["templates"][0])
        self.assertEqual(context["gradescope_regions"], question["content"]["regions"])
        combined = locations.combine(context, [])
        self.assertEqual(combined["regions"], question["content"]["regions"])
        self.assertEqual(combined["regions_from"], "gradescope")
        # Each version's template is its own; importing again from the same template file keeps the one already saved.
        again = self.import_(self.ids(view, "B", "Question 18"))
        self.assertEqual(again["results"][0]["status"], "imported")
        third = self.import_(self.ids(self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container)), "C", "Question 19"))
        self.assertEqual(third["results"][0]["areas"], 1)
        kept = self.import_(self.ids(self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container)), "B", "Question 19"))
        self.assertEqual(kept["templates"], again["templates"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignment_templates").fetchone()[0], before + 3)
        self.assertEqual(self.writes(), [])
        # Without the areas: the rubric alone, as before.
        other = self.fake.add_assignment(self.course, "Quiz 9", 2)
        self.fake.add_question(other, 1, "Question 7", 2, [(2, "Correct"), (0, "Incorrect")])
        plain = self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course), "exam_version": "A",
                                                     "gradescope_assignment_id": str(other), "areas": False,
                                                     "questions": [str(next(q for q, v in self.fake.questions.items() if v["assignment"] == other))]})
        self.assertEqual(plain["templates"], [])
        self.assertEqual(self.faculty_question("A", "7")["content"]["regions"], [])

    def test_boxes_on_a_template_of_other_pages_are_left_out_with_a_note(self):
        other = self.fake.add_assignment(self.course, "Quiz 9", 3)
        qid = self.fake.add_question(other, 1, "Question 7", 2, [(2, "Correct"), (0, "Incorrect")])
        result = self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course), "exam_version": "A",
                                                      "gradescope_assignment_id": str(other), "questions": [str(qid)]})
        self.assertEqual(result["results"][0]["status"], "imported")
        self.assertEqual(result["results"][0]["areas"], 0)
        self.assertIn("3 pages", result["notes"][0])
        self.assertEqual(self.faculty_question("A", "7")["content"]["regions"], [])

    def test_crops_become_areas_only_when_they_fit(self):
        crop = {"page_number": 2, "x1": 5, "x2": 95, "y1": 50, "y2": 100}
        self.assertEqual(gri.crop_regions([crop], 2), [{"page": 2, "region": {"x": .05, "y": .5, "width": .9, "height": .5}, "privacy_masks": []}])
        self.assertIsNone(gri.crop_regions([crop], 1))
        self.assertIsNone(gri.crop_regions([], 2))
        self.assertIsNone(gri.crop_regions([{"page_number": 1}], 2))

    def test_a_template_link_elsewhere_is_never_read(self):
        session = self.sender.session_factory()
        try:
            for url in ("https://evil.example/x.pdf", "http://production-gradescope-uploads.s3-us-west-2.amazonaws.com/x.pdf", ""):
                with self.assertRaises(gw.GradescopeError):
                    session.template_pdf({"template_url": url})
        finally:
            session.close()
        self.assertTrue(gw.UPLOADS_HOST.fullmatch("production-gradescope-uploads.s3-us-west-2.amazonaws.com"))
        self.assertTrue(gw.UPLOADS_HOST.fullmatch("production-gradescope-uploads.s3.amazonaws.com"))
        self.assertFalse(gw.UPLOADS_HOST.fullmatch("production-gradescope-uploads.s3.amazonaws.com.evil.example"))

    def test_a_question_already_in_faculty_fails_alone(self):
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container))
        chosen = self.ids(view, "A", "Question 20", "Question 18")
        result = self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course),
                                                       "gradescope_assignment_id": str(self.container), "questions": chosen})
        statuses = {r["printed_number"]: r["status"] for r in result["results"]}
        self.assertEqual(statuses, {"18": "imported", "20": "failed"})
        self.assertIn("already exists", next(r["error"] for r in result["results"] if r["printed_number"] == "20"))

    def test_grouped_items_are_worded_and_numbered_as_the_export(self):
        rubric = self.sender_rubric("A")
        target = next(q for q in rubric["questions"] if q["title"] == "Question 21 a and b")
        source = gri.export_csv(target, gw.export_order(rubric, target["id"]))
        exported = gradescope_import.inspect(source)
        self.assertEqual(exported["max_points"], 8)
        self.assertEqual(exported["mode"], "positive")
        flat = [(p, d) for p, d in Q21 if not isinstance(d, list)]
        self.assertTrue(flat)
        self.assertEqual([i["description"] for i in exported["items"]], [i["name"] for i in gw.export_order(rubric, target["id"])])
        self.assertEqual([json.loads(i["original_source_text"])["rubric_number"] for i in exported["items"]][:2], ["4.1", "4.2"])

    def test_negative_scoring_imports_deductions_and_still_matches(self):
        assignment = self.fake.add_assignment(self.course, "Quiz 9", 1)
        self.fake.add_question(assignment, 1, "Problem 5: limits", 6, [(0, "Correct"), (2, "Sign error"), (6, "No answer")], scoring_type="negative")
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(assignment))
        self.assertEqual([v["letter"] for v in view["versions"]], [""])
        question = view["versions"][0]["questions"][0]
        self.assertEqual((question["printed_number"], question["scoring"], question["importable"]), ("5", "negative", True))
        result = self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course), "exam_version": "A",
                                                       "gradescope_assignment_id": str(assignment), "questions": [question["id"]]})
        self.assertEqual(result["results"][0]["status"], "imported", result)
        imported = self.faculty_question("A", "5")
        self.assertEqual(imported["rubric"]["rules"]["mode"], "negative")
        self.assertEqual([i["points"] for i in imported["rubric"]["rules"]["items"]], [0, -2, -6])
        session = self.sender.session_factory()
        try:
            rubric = session.rubric(str(self.course), str(assignment))
        finally:
            session.close()
        mapping, warnings = map_question(imported, rubric)
        self.assertIsNotNone(mapping, warnings)

    def test_a_coordinator_minus_one_line_imports_and_still_matches(self):
        """GRADING "DEDUCTIONS IN A POSITIVE RUBRIC" (2026-10-25): Exam 2's positive questions with a -1 line import, and the send
        matches every line, the -1 included."""
        assignment = self.fake.add_assignment(self.course, "Quiz 9", 2)
        qid = self.fake.add_question(assignment, 1, "Question 23", 8, [(8, "Correct"), (4, "Half"), (0, "No answer"),
                                                                      (-1, "Corner Points are not written as ordered pairs")])
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(assignment))
        self.assertTrue(view["versions"][0]["questions"][0]["importable"], view["versions"][0]["questions"][0])
        result = self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course), "exam_version": "A",
                                                      "gradescope_assignment_id": str(assignment), "questions": [str(qid)]})
        self.assertEqual(result["results"][0]["status"], "imported", result)
        imported = self.faculty_question("A", "23")
        self.assertEqual([i["points"] for i in imported["rubric"]["rules"]["items"]], [8, 4, 0, -1])
        session = self.sender.session_factory()
        try:
            mapping, warnings = map_question(imported, session.rubric(str(self.course), str(assignment)))
        finally:
            session.close()
        self.assertIsNotNone(mapping, warnings)
        self.assertEqual(len(mapping["items"]), 4)

    def test_what_cannot_be_imported_says_why(self):
        assignment = self.fake.add_assignment(self.course, "Worksheet 3", 1)
        self.fake.add_question(assignment, 1, "Question 1", 4, [])
        self.fake.add_question(assignment, 2, "Question 2", 4, [(5, "Too many points")])
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(assignment))
        reasons = [q["reason"] for q in view["versions"][0]["questions"]]
        self.assertIn("no rubric items", reasons[0])
        self.assertTrue(reasons[1])
        result = self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course), "exam_version": "A",
                                                       "gradescope_assignment_id": str(assignment),
                                                       "questions": [q["id"] for q in view["versions"][0]["questions"]]})
        self.assertEqual([r["status"] for r in result["results"]], ["failed", "failed"])

    def test_the_coordinator_course_is_read_only_for_the_import(self):
        """GRADESCOPE "THE COORDINATOR'S COURSE" (requirement, 2026-10-25): it is listed last and never proposed; its assignments, outlines and rubric pages are read and its template's link
        fetched; nothing else there is ever requested, and every other use of it is still refused."""
        exam = self.fake.add_assignment(self.forbidden, "Common Exam 2 - Partial Credit", 2)
        self.fake.add_question(exam, 1, "Question 7", 4, [(4, "Correct"), (0, "Incorrect")])
        view = self.preview()
        self.assertEqual(view["courses"][-1], {"id": str(self.forbidden), "short": "MATH 140", "name": "Math  140 for INSTRUCTORS",
                                               "term": "Fall 2026", "coordinator": True})
        self.assertNotEqual(view["course_id"], str(self.forbidden))
        view = self.preview(course_id=str(self.forbidden), gradescope_assignment_id=str(exam))
        question = view["versions"][0]["questions"][0]
        self.assertEqual((question["printed_number"], question["areas"]), ("7", 1))
        result = self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.forbidden), "exam_version": "A",
                                                      "gradescope_assignment_id": str(exam), "questions": [question["id"]]})
        self.assertEqual((result["results"][0]["status"], result["results"][0]["areas"]), ("imported", 1), result)
        self.assertEqual(self.faculty_question("A", "7")["content"]["max_points"], 4)
        self.assertEqual(self.fake.forbidden_hits, [])
        mine = [r for r in self.fake.requests if r["path"].startswith(f"/courses/{self.forbidden}")]
        self.assertTrue(mine)
        self.assertTrue(all(r["method"] == "GET" for r in mine))
        self.assertEqual(len(mine), len(self.fake.coordinator_reads))
        self.assertEqual(self.writes(), [])
        session = self.sender.session_factory()
        try:
            for read in (lambda: session.roster(self.forbidden), lambda: session.assignments(self.forbidden),
                         lambda: session.rubric(self.forbidden, exam), lambda: session.outline(self.forbidden, exam),
                         lambda: session.guard(self.forbidden)):
                with self.assertRaisesRegex(gw.GradescopeError, "never touches"):
                    read()
        finally:
            session.close()

    def sheets(self, course=None):
        """The bubble-sheet exam of `course` (section 501's) and {letter: version}, keyed on Gradescope: Version A answers B, C, AD
        (either) at 4, 4, 2 points; Version B answers A, E and every letter; the "?" version has no key."""
        course = course or self.course
        container = next(a for a in self.fake.assignments.values() if a["bubble_sheet"] and a["container"] and a["course"] == course)
        versions = {a["version"]: a["id"] for a in self.fake.assignments.values() if a["parent"] == container["id"]}
        for letter, keyed in (("A", [("B", "exact", 4), ("C", "exact", 4), ("AD", "either", 2)]),
                              ("B", [("A", "exact", 4), ("E", "exact", 4), ("ABCDE", "either", 4)])):
            for number, (answer, scoring, points) in enumerate(keyed, 1):
                self.fake.add_question(versions[letter], number, f"Question {number}", points, [], mc=(answer, scoring))
        self.fake.add_question(versions["?"], 1, "Question 1", 0, [], mc=("", "exact"))
        return container["id"], versions

    def test_a_bubble_sheet_key_is_imported_as_the_plans_answer_key(self):
        """2026-10-25 ("import the bubble sheet key from there as well"): a bubble-sheet exam is listed with the assignments; its
        key per version is read from each version's outline (the set's versions from its Edit page, "?" left out) and saved as the
        plan's answer key, the most usual points and scoring as its defaults. A read of Gradescope only."""
        container, _ = self.sheets()
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(container))
        listed = {a["id"]: a for a in view["assignments"]}
        self.assertTrue(listed[str(container)]["bubble_sheet"])
        self.assertEqual(view["versions"], [])
        bubble = view["bubble"]
        self.assertEqual([v["letter"] for v in bubble["versions"]], ["A", "B", "C"])
        self.assertEqual(bubble["problems"], [])
        self.assertEqual(bubble["versions"][0]["questions"], [{"number": 1, "answer": "B", "points": "4", "scoring": "exact"},
            {"number": 2, "answer": "C", "points": "4", "scoring": "exact"}, {"number": 3, "answer": "AD", "points": "2", "scoring": "either"}])
        result = self.sender.bubble_key_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course),
                                                          "gradescope_assignment_id": str(container), "base_revision": view["key_revision"]})
        self.assertEqual(result["versions"], {"A": 3, "B": 3, "C": 0})
        with connect(self.path) as db:
            key = self.frq.bubble_key(db, self.aid)
        self.assertEqual(key["revision"], result["key_revision"])
        self.assertEqual(key["defaults"], {"points": "4", "scoring": "exact"})
        self.assertEqual(key["versions"][0]["questions"][2], {"number": 3, "answer": "AD", "points": "2", "scoring": "either"})
        self.assertEqual(key["versions"][1]["questions"][2], {"number": 3, "answer": "ABCDE", "scoring": "either"})
        self.assertEqual(self.writes(), [])
        # The dialog's revision is checked: a key saved meanwhile is not replaced.
        with self.assertRaises(Conflict):
            self.sender.bubble_key_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course),
                                                     "gradescope_assignment_id": str(container), "base_revision": view["key_revision"]})
        self.assertNothingForbidden()

    def test_the_exams_bubble_sheet_key_comes_with_its_rubrics(self):
        """2026-10-25 ("shouldn't I be able to import them as part of exam 2?"): choosing "Common Exam 1 - Partial Credit" proposes
        "Common Exam 1 - Multiple Choice", and one import brings the rubrics and that key; "" leaves the key out."""
        unkeyed = self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container))
        self.assertEqual((unkeyed["sheet_id"], unkeyed["companion"]), ("", None))  # no key on Gradescope yet: nothing proposed
        sheets, _ = self.sheets()
        self.assertEqual(gri.exam_stem("Common Exam 2 - Partial Credit"), gri.exam_stem("Common Exam 2 - Multiple Choice"))
        self.assertNotEqual(gri.exam_stem("Common Exam 2 - Partial Credit"), gri.exam_stem("Common Exam 1 - Multiple Choice"))
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(self.container))
        self.assertEqual(view["sheet_id"], str(sheets))
        self.assertEqual([v["letter"] for v in view["companion"]["versions"]], ["A", "B", "C"])
        alone = self.import_(self.ids(view, "A", "Question 18"), sheet_id="")
        self.assertIsNone(alone["key"])
        with connect(self.path) as db:
            self.assertIsNone(self.frq.bubble_key(db, self.aid))
        both = self.import_(self.ids(view, "A", "Question 19"), sheet_id=str(sheets), base_revision=None)
        self.assertEqual(both["results"][0]["status"], "imported")
        self.assertEqual((both["key"]["status"], both["key"]["versions"]), ("imported", {"A": 3, "B": 3, "C": 0}), both)
        # The key alone, with no question ticked; a key saved since the dialog opened is never replaced.
        again = self.import_([], sheet_id=str(sheets), base_revision=None)
        self.assertEqual(again["key"]["status"], "failed")
        with self.assertRaises(ValueError):
            self.import_([], sheet_id="")
        self.assertEqual(self.writes(), [])

    def test_a_bubble_key_faculty_cannot_score_is_refused_with_its_reason(self):
        container, versions = self.sheets()
        self.fake.add_question(versions["C"], 1, "Question 1", 4, [], mc=("B", "weighted"))
        view = self.preview(course_id=str(self.course), gradescope_assignment_id=str(container))
        self.assertIn("weighted", view["bubble"]["problems"][0])
        with self.assertRaisesRegex(Conflict, "weighted"):
            self.sender.bubble_key_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course),
                                                     "gradescope_assignment_id": str(container), "base_revision": None})
        with connect(self.path) as db:
            self.assertIsNone(self.frq.bubble_key(db, self.aid))

    def test_the_coordinator_courses_bubble_key_is_read_only(self):
        exam, versions = self.fake.add_container(self.forbidden, "Common Exam 2 - Multiple Choice", "AB", 1, bubble_sheet=True)
        self.fake.add_question(versions["A"], 1, "Question 1", 4, [], mc=("C", "exact"))
        self.fake.add_question(versions["B"], 1, "Question 1", 4, [], mc=("D", "exact"))
        result = self.sender.bubble_key_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.forbidden),
                                                          "gradescope_assignment_id": str(exam), "base_revision": None})
        self.assertEqual(result["versions"], {"A": 1, "B": 1})
        self.assertEqual(self.fake.forbidden_hits, [])
        mine = [r for r in self.fake.requests if r["path"].startswith(f"/courses/{self.forbidden}")]
        self.assertTrue(mine and all(r["method"] == "GET" for r in mine) and len(mine) == len(self.fake.coordinator_reads))
        session = self.sender.session_factory()
        try:
            with self.assertRaisesRegex(gw.GradescopeError, "never touches"):
                session.version_set(self.forbidden, exam)
        finally:
            session.close()

    def test_unknown_assignments_are_refused(self):
        with self.assertRaises(Conflict):
            self.sender.rubric_import(self.aid, {"request_id": str(uuid.uuid4()), "course_id": str(self.course),
                                                 "gradescope_assignment_id": "999999", "questions": ["1"]})

    def test_routes(self):
        response = self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/rubrics/preview",
                                    json={"course_id": str(self.course), "gradescope_assignment_id": str(self.container)}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.get_json())
        view = response.get_json()
        chosen = self.ids(view, "C", "Question 19")
        response = self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/rubrics/import",
                                    json={"request_id": str(uuid.uuid4()), "course_id": str(self.course),
                                          "gradescope_assignment_id": str(self.container), "questions": chosen}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["imported"], 1)


class PrintedNumbers(unittest.TestCase):
    def test_a_title_gives_the_number_it_starts_with(self):
        for title, expected in (("Question 3", "3"), ("Problem 12: Limits", "12"), ("Q7", "7"), ("4.", "4"), ("#5 (b)", "5"),
                                ("Find f(2)", ""), ("Part one", ""), ("Question", ""), ("2.5 points", ""), ("Question 0", ""),
                                ("Quesiton 25", "25"), ("Problme 4", "4"), ("Points 4", ""), ("Version 2", ""), ("Part 2", "")):
            self.assertEqual(gri.printed_from_title(title), expected, title)


if __name__ == "__main__":
    unittest.main()
