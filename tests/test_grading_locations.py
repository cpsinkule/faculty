"""Automatic template verification with synthetic OCR and real retained owners."""
import io
import json
import unittest
import uuid
from unittest.mock import patch

from PIL import Image
from werkzeug.exceptions import Conflict

from grading import locations
from grading.models import plain_transcript
from grading.providers.base import ProviderError
from rosters import connect
from test_assignment_templates import prepared
import test_frq_grading as grading_fixture
from test_gradescope_rubrics import encode, export_rows
from frq_fixtures import finalize_rubrics


def heading(number="1", points="(10 points)", **changes):
    return {"number": number, "label": number+".", "points_text": points, "header_bbox": [100,100,500,150],
        "response_bbox": [100,100,900,800], "continued": False, "confidence": .98, **changes}


def confirm_templates(service, aid, template_id=None):
    status = service.assignment_templates(aid)
    choices = [{'key': slot['key'], 'template_id': template_id or slot['template_id'] or slot['options'][0]['id']}
               for slot in status['slots']]
    return service.confirm_assignment_templates(aid, {'input_sha256': status['input_sha256'],
        'selections': choices, 'acknowledge_warning': True, 'reset_regions': True})


class ReadingProvider(grading_fixture.FakeProvider):
    def __init__(self):
        self.reads = 0
        self.layouts = 0
        self.number = "1"
        self.points = 10
        self.ocr_points = 10
        self.fail_reading = False
        self.missing = False
        self.truncated = False
        self.low_confidence = False
        self.payloads = []
        self.extra = []  # Further (number, printed points) problems below the first one on the same page.
        self.blank_note = ""  # A page note the layout model writes on the blank page.

    def recognize(self, image):
        self.reads += 1
        if self.fail_reading:
            raise ProviderError("Synthetic OCR unavailable.")
        source = f"{self.number}. ({self.ocr_points} points) Solve this problem." + "".join(f"\n{n}. ({p} points) Solve this one." for n, p in self.extra) if self.reads % 2 else "Blank page"
        return {"provider": "glm", "transcript": plain_transcript(source), "raw": {"text": source, "truncated": self.truncated}, "metadata": {"model": "synthetic"}}

    def locate(self, payload, images):
        # A page's layout follows its own OCR text, which may have been read ahead of it in one model load.
        self.layouts += 1
        self.payloads.append(payload)
        below = [heading(n, f"({p} points)", header_bbox=[100, 400+200*i, 900, 450+200*i], response_bbox=[100, 450+200*i, 900, 580+200*i]) for i, (n, p) in enumerate(self.extra)]
        return {"output": {"questions": [heading(self.number, f"({self.points} points)", confidence=.6 if self.low_confidence else .98), *below] if payload["ocr_text"] != "Blank page" and not self.missing else [],
            "warnings": [self.blank_note] if self.blank_note and payload["ocr_text"] == "Blank page" else []}, "raw": {"fixture": True}, "metadata": {"model": "synthetic", "prompt": "locate-v3.txt"}}


class LocationParsing(unittest.TestCase):
    def test_question_warning_does_not_fail_unrelated_questions(self):
        page = locations.page_result({"questions": [heading("18", "(4 pts)", warnings=["Unreadable equation"]),
            heading("19", "(2 pts)")], "warnings": []}, "18.(4 pts) Solve\n19.(2 pts) Solve")
        for number, maximum, failed in (("18",4,True), ("19",2,False)):
            result = locations.combine({"issues": [], "printed_number": number, "rubric_maximum": maximum}, [(7,page)])
            self.assertEqual(result["needs_manual_review"], failed)
            self.assertTrue(result["confirmation_required"])

    def test_page_note_holds_only_questions_on_that_page(self):
        # The live Versions B and C (2026-09-25): a scratch page's note held every question of the version for manual review.
        page = locations.page_result({"questions": [heading("18", "(4 pts)"), heading("19", "(2 pts)", header_bbox=[100,500,900,550])],
            "warnings": ["Part of this page is cut off."]}, "18.(4 pts) Solve\n19.(2 pts) Solve")
        other = locations.page_result({"questions": [heading("20", "(4 pts)")], "warnings": []}, "20.(4 pts) Solve")
        scratch = locations.page_result({"questions": [], "warnings": ["Page is intentionally blank for scratch work and contains no numbered exam problems."]}, "")
        pages = [(7, page), (8, other), (12, scratch)]
        for number, maximum, held in (("18",4,True), ("19",2,True), ("20",4,False)):
            with self.subTest(number=number):
                result = locations.combine({"issues": [], "printed_number": number, "rubric_maximum": maximum}, pages)
                self.assertEqual(result["needs_manual_review"], held)
                self.assertEqual(result["issues"], ["Page 7: Part of this page is cut off."] if held else [])
        # A question found nowhere is still held, for that reason alone.
        missing = locations.combine({"issues": [], "printed_number": "21", "rubric_maximum": 4}, pages)
        self.assertEqual(missing["issues"], ["The printed question was not found exactly once; inspect its number and pages.",
                                             "No answer area could be suggested. Draw it on its page."])

    def test_heading_totals_need_exact_number_and_adjacent_printed_score(self):
        for source in ("18. (4 points) Solve", "Question 18 [4 pts] Solve", "18.\n(4) Solve", "## 18. (4 points) Solve"):
            with self.subTest(source=source):
                self.assertEqual(locations.ocr_totals(source, "18"), [4])
        for source in ("118. (4 points)", "18. Solve x = 4", "18. Solve\n(a) (2 points)\n(b) (2 points)", "Question 19 (4 points)"):
            self.assertEqual(locations.ocr_totals(source, "18"), [])

    def test_no_automatic_confirmation_even_for_equal_points(self):
        context = {"issues": [], "printed_number": "18", "rubric_maximum": 4}
        page = locations.page_result({"questions": [heading("18", "(4 points)")], "warnings": []}, "18. (4 points) Solve")
        result = locations.combine(context, [(1, page)])
        self.assertTrue(result["points_match"])
        self.assertTrue(result["confirmation_required"])
        self.assertFalse(result["needs_manual_review"])
        separate = locations.page_result({"questions": [heading("18", "(4 points)", response_bbox=[100,200,900,800])]}, "18. (4 points)")
        self.assertEqual(locations.combine(context, [(1,separate)])["regions"][0]["region"]["y"], .1)
        for bad_pages in ([(1, page),(2,page)], [], [(1,page),(3,{"questions": [{**page["questions"][0], "continued": True}], "warnings": []})]):
            self.assertTrue(locations.combine(context, bad_pages)["needs_manual_review"])
        continued = {"questions": [{**page["questions"][0], "continued": True, "printed_points": None}], "warnings": []}
        self.assertEqual(len(locations.combine(context, [(1,page),(2,continued)])["regions"]), 2)

    def test_score_disagreement_and_invalid_geometry_require_review(self):
        context = {"issues": [], "printed_number": "18", "rubric_maximum": 4}
        for printed, ocr in (("(6 points)", "18. (6 points)"), ("(4 points)", "18. (6 points)"), ("", "18. Solve")):
            page = locations.page_result({"questions": [heading("18", printed)], "warnings": []}, ocr)
            self.assertTrue(locations.combine(context, [(1,page)])["needs_manual_review"])
        with self.assertRaises(ValueError):
            locations.page_result({"questions": [heading(header_bbox=[0,0,1001,1001])]}, "1. (10 points)")
        with self.assertRaises(ValueError):
            locations.page_result({"questions": [heading(header_bbox=[0,.1,.5,.5])]}, "1. (10 points)")
        self.assertEqual(locations.page_result({"questions": [heading(label="1. (10 points) Solve.")]}, "1. (10 points)")["questions"][0]["number"], "1")
        with self.assertRaises(ValueError):
            locations.page_result({"questions": [heading(number="18", label="19.")]}, "18. (10 points)")

    def test_a_heading_quoted_without_its_number_stands_only_on_the_ocr_heading(self):
        # 2026-09-25, a real blank: the reader gave problem 4 as "Using technology, ..." and every question of the template failed.
        ocr = "4. Using technology, solve the following system of equations.\n\n5. (2 points) Given $F$, state the size of matrix F."
        label = "Using technology, solve the following system of equations."
        self.assertEqual(locations.page_result({"questions": [heading("4", "", label=label)]}, ocr)["questions"][0]["number_from"], "ocr_heading")
        given = heading("5", "(2 points)", label="Given $F$, state the size of matrix $F$.")  # A total between number and words.
        self.assertEqual(locations.page_result({"questions": [given]}, ocr)["questions"][0]["number_from"], "ocr_heading")
        self.assertNotIn("number_from", locations.page_result({"questions": [heading("4", "", label="4. " + label)]}, ocr)["questions"][0])
        for number, text, source in (("5", label, ocr),  # The OCR prints these words under another number.
                                     ("4", label, label),  # No number printed at all.
                                     ("4", "Solve.", "4. Solve."),  # Too few words to be that problem's own.
                                     ("4", "Using calculus, solve the following system", ocr)):
            with self.subTest(number=number, label=text), self.assertRaises(ValueError):
                locations.page_result({"questions": [heading(number, "", label=text)]}, source)


class VersionCheckProgress(unittest.TestCase):
    def test_papers_are_counted_once_by_their_latest_comparison(self):
        import sqlite3
        from grading.exam_versions import ExamVersionsMixin
        db = sqlite3.connect(":memory:")
        from grading.exam_versions import FIRST_PAGE, NOT_NEEDED
        db.execute("CREATE TABLE frq_version_checks(assignment_id, section_id, enrollment_id, status, input_json DEFAULT '{}', error DEFAULT '')")
        rows = [("a", "s", "1", "complete"), ("a", "s", "1", "pending"),  # Queued again after a change: left, not done.
                ("a", "s", "2", "complete"), ("a", "s", "3", "failed"), ("a", "s", "4", "running"), ("b", "s", "5", "pending")]
        db.executemany("INSERT INTO frq_version_checks(assignment_id,section_id,enrollment_id,status) VALUES(?,?,?,?)", rows)
        self.assertEqual(ExamVersionsMixin.version_check_progress(db, "a"), {"left": 2, "done": 2, "failed": 1, "total": 4})
        # FIRST PAGE: a comparison set aside is not a paper's latest word, and an unread page 1 is no failure (its paper is compared).
        db.execute("INSERT INTO frq_version_checks VALUES('a','s','2','failed','{}',?)", (NOT_NEEDED,))
        db.execute("INSERT INTO frq_version_checks VALUES('a','s','6','failed',?,'Page 1 could not be read.')", (f'{{"matcher":"{FIRST_PAGE}"}}',))
        self.assertEqual(ExamVersionsMixin.version_check_progress(db, "a"), {"left": 2, "done": 3, "failed": 1, "total": 5})
        self.assertEqual(ExamVersionsMixin.version_check_progress(db, "c"), {"left": 0, "done": 0, "failed": 0, "total": 0})


class ProposedAreas(unittest.TestCase):
    @staticmethod
    def area(number, questions):
        page = locations.page_result({"questions": questions}, "")
        return locations.combine({"issues": [], "printed_number": number, "rubric_maximum": 10}, [(1, page)])["regions"][0]["region"]

    def test_area_spans_the_sheet_until_the_next_printed_problem(self):
        questions = [heading("1", header_bbox=[120,150,880,190], response_bbox=[120,190,880,300]),
            heading("2", header_bbox=[120,400,880,440], response_bbox=[120,440,700,520]),
            heading("3", header_bbox=[120,700,880,740], response_bbox=[120,740,700,800])]
        first, second, last = (self.area(number, questions) for number in "123")
        self.assertEqual((first["x"], first["width"], first["y"]), (0, 1, .15))
        self.assertAlmostEqual(first["y"]+first["height"], .4)
        self.assertAlmostEqual(second["y"]+second["height"], .7)
        # The last problem on a page keeps all remaining space; nothing above a heading is added.
        self.assertEqual((last["y"], last["y"]+last["height"]), (.7, 1))

    def test_an_overlong_suggestion_stops_at_the_next_problem(self):
        questions = [heading("1", header_bbox=[120,150,880,190], response_bbox=[120,190,880,650]),
            heading("2", header_bbox=[120,400,880,440], response_bbox=[120,440,880,900])]
        self.assertAlmostEqual(sum(self.area("1", questions)[k] for k in ("y", "height")), .4)

    def test_side_by_side_problems_keep_their_own_column(self):
        questions = [heading("1", header_bbox=[60,150,480,190], response_bbox=[60,190,480,400]),
            heading("2", header_bbox=[520,150,940,190], response_bbox=[520,190,940,400]),
            heading("3", header_bbox=[60,600,480,640], response_bbox=[60,640,480,900])]
        left = self.area("1", questions)
        self.assertAlmostEqual(left["x"], .06)
        self.assertAlmostEqual(left["width"], .42)
        self.assertAlmostEqual(left["y"]+left["height"], .6)


class LocationWorkflow(unittest.TestCase):
    managed = False
    document_version = grading_fixture.FrqWorkflowTests.document_version
    ready_batch = grading_fixture.FrqWorkflowTests.ready_batch
    reviewed_source = grading_fixture.FrqWorkflowTests.reviewed_source
    configure_rubric = grading_fixture.FrqWorkflowTests.configure_rubric
    queue = grading_fixture.FrqWorkflowTests.queue

    def setUp(self):
        grading_fixture.FrqWorkflowTests.setUp(self)
        self.reader = ReadingProvider()
        self.enterContext(patch.object(self.frq, "provider", return_value=self.reader))
        self.enterContext(patch("grading.config.endpoint", return_value="http://127.0.0.1:11435/v1"))
        self.enterContext(patch("document_identity.template_conversion.convert_template", side_effect=prepared))

    def upload(self, label="Blank exam", confirm=True):
        template = self.frq.templates.upload(self.assignment["id"], label, str(uuid.uuid4()), {"blank.pdf": b"Synthetic source"})
        if confirm:
            confirm_templates(self.frq, self.plan['id'])
        return template

    def question(self):
        return self.frq.assignment(self.plan["id"])["questions"][0]

    def finish_check(self):
        for _ in range(60):
            self.frq.process_location_one()
            status = self.question()["location_check"]
            if status["status"] in ("failed", "needs_review", "needs_confirmation", "confirmed"):
                return status
        self.fail("Template check did not finish")

    def confirmation(self, check=None, **changes):
        q = self.question()
        check = check or q["location_check"]
        return {"version": q["version"], "check_id": check["id"], "input_sha256": check["input_sha256"],
            "template_id": check["context"]["templates"][0]["id"], "printed_number": check["context"]["printed_number"],
            "regions": check["result"]["regions"] if check["result"] and check["result"]["regions"] else q["content"]["regions"],
            "confirm_locations": True, **changes}

    def test_automatic_discovery_after_template_upload_gates_grading_and_keeps_draft(self):
        before = self.question()
        self.assertEqual(before["location_check"]["status"], "waiting_template")
        template = self.upload()
        self.assertEqual(self.reader.reads, 0)
        self.assertEqual(self.question()["location_check"]["status"], "pending")
        with self.assertRaisesRegex(Conflict, "Confirm this question"):
            self.queue()
        check = self.finish_check()
        self.assertEqual(check["status"], "needs_confirmation")
        self.assertEqual(self.reader.reads, 2)
        self.assertEqual(self.question()["content"]["regions"], before["content"]["regions"])
        self.assertTrue(all(set(p) == {"ocr_text"} for p in self.reader.payloads))
        with self.assertRaisesRegex(ValueError, "Explicitly confirm"):
            self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation(confirm_locations=False))
        result = self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation())
        q = result["questions"][0]
        self.assertEqual(q["location_check"]["status"], "confirmed")
        self.assertEqual(q["content"]["template_id"], template["id"])
        self.assertEqual(q["rubric_id"], before["rubric_id"])
        self.assertEqual(q["rubric"]["approved_at"], before["rubric"]["approved_at"])
        self.assertNotEqual(q["question_revision_id"], before["question_revision_id"])
        self.queue()

    def test_rubric_after_template_uses_filename_number_and_reuses_page_reads(self):
        self.upload()
        source = encode(export_rows())
        uploaded = self.frq.upload_source(self.plan["id"], "1_Question_18_a_and_b.csv", source.encode(), "rubric-18")
        q = self.question()
        self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "format": "csv", "source_text": source, "source_artifact_id": uploaded["source_artifact_id"]})
        self.reader.number = "18"
        check = self.finish_check()
        self.assertEqual(check["context"]["printed_number"], "18")
        self.assertEqual(check["status"], "needs_confirmation")
        self.assertIsNone(self.question()["rubric"]["approved_at"])
        q = self.question()
        self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "format": "csv", "source_text": source, "source_artifact_id": uploaded["source_artifact_id"]})
        self.assertEqual(self.finish_check()["status"], "needs_confirmation")
        self.assertEqual(self.reader.reads, 2)

    def test_a_note_on_a_page_without_the_question_leaves_it_to_confirm_all(self):
        # Versions B and C of the live Test 1 (2026-09-25): the scratch page's note held every question for manual review.
        self.reader.blank_note = "Page is intentionally blank for scratch work and contains no numbered exam problems."
        self.upload()
        check = self.finish_check()
        self.assertEqual((check["status"], check["result"]["issues"]), ("needs_confirmation", []))
        self.confirm_all([self.question()])
        self.assertEqual(self.question()["location_check"]["status"], "confirmed")

    def test_a_changed_notes_rule_recomputes_the_check_from_retained_readings(self):
        self.upload()
        first = self.finish_check()
        with patch.object(locations, "NOTES", "own-pages-v2"):
            self.assertEqual(self.question()["location_check"]["status"], "pending")
            again = self.finish_check()
        self.assertNotEqual(again["id"], first["id"])
        self.assertEqual((again["status"], self.reader.reads, self.reader.layouts), ("needs_confirmation", 2, 2))

    def _second_check_after(self, **changes):
        """The reads after a first check finished, settings changed, and a second check (a rule bump) on the same blank was read."""
        self.upload()
        first = self.finish_check()
        settings = self.frq.settings()
        self.frq.save_settings({"version": settings["version"], "settings": {**settings["settings"], **changes}})
        with patch.object(locations, "NOTES", "own-pages-v2"):
            again = self.finish_check()
        self.assertNotEqual(again["id"], first["id"])
        return self.reader.reads

    def test_only_the_settings_the_reader_uses_change_a_template_reading_profile(self):
        # Requirement, 2026-11-06 (GRADING "reading profile"): the profile is an allow-list (the local verifier model and backend, the
        # timeout capped at 120 s); a preference that is no part of a reading never makes a second check read the blank again.
        for changes in ({"auto_grade": False}, {"auto_certify": False}, {"auto_flag_review": False}, {"auto_gradescope": False}, {"draft_rubric_check": False},
                        {"full_concurrency": True}, {"mathpix_enabled": True}, {"concurrency": 7}, {"local_grader_model": "Other-Grader"}, {"timeout": 240}):
            with self.subTest(changes):
                self.setUp()  # a fresh workspace for each case
                self.assertEqual(self._second_check_after(**changes), 2, changes)

    def test_a_setting_the_reader_uses_makes_a_second_check_read_the_blank_again(self):
        for changes in ({"local_verifier_model": "Other-Verifier"}, {"local_verifier_backend": "ollama"}, {"timeout": 60}):
            with self.subTest(changes):
                self.setUp()  # a fresh workspace for each case
                self.assertEqual(self._second_check_after(**changes), 4, changes)

    def test_mismatch_cannot_be_confirmed_without_manual_acknowledgment_and_reason(self):
        self.upload()
        self.reader.points = self.reader.ocr_points = 8
        check = self.finish_check()
        self.assertEqual(check["status"], "needs_review")
        self.assertIn("printed total is 8", " ".join(check["result"]["issues"]))
        with self.assertRaises(ValueError):
            self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation(manual_review=True))
        with self.assertRaises(ValueError):
            self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation(notes="Printed score is a typo; coordinator confirms ten."))
        result = self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation(manual_review=True, notes="Printed score is a typo; coordinator confirms ten."))
        self.assertEqual(result["questions"][0]["content"]["max_points"], 10)
        self.assertEqual(result["questions"][0]["location_check"]["status"], "confirmed")

    def finish_checks(self):
        for _ in range(80):
            self.frq.process_location_one()
            questions = self.frq.assignment(self.plan["id"])["questions"]
            if all(q["location_check"]["status"] in ("failed", "needs_review", "needs_confirmation", "confirmed") for q in questions):
                return {q["location_check"]["context"]["printed_number"]: q for q in questions}
        self.fail("Template checks did not finish")

    def confirm_all(self, questions, **changes):
        return self.frq.confirm_all_locations(self.plan["id"], {"confirm_locations": True, "questions": [
            {"question_id": q["question_id"], "version": q["version"], "check_id": q["location_check"]["id"],
             "input_sha256": q["location_check"]["input_sha256"]} for q in questions], **changes})

    def retry_all(self, questions):
        return self.frq.retry_all_locations(self.plan["id"], {"questions": [
            {"question_id": q["question_id"], "check_id": q["location_check"]["id"], "input_sha256": q["location_check"]["input_sha256"]} for q in questions]})

    def test_one_retry_covers_every_failed_question_without_an_answer_area_even_while_the_worker_is_busy(self):
        for number in ("2", "3"):
            self.frq.upload_rubric(self.plan["id"], f"1_Question_{number}.csv", encode(export_rows(question=int(number))).encode(), {"request_id": str(uuid.uuid4())})
        self.reader.fail_reading = True  # The first page fails, so no question gets an area.
        self.upload()
        questions = self.finish_checks()
        bare = [questions["2"], questions["3"]]
        for q in bare:
            self.assertEqual((q["location_check"]["status"], q["location_check"]["result"]["regions"], q["content"]["regions"]), ("failed", [], []))
        with self.assertRaisesRegex(ValueError, "listed twice"):
            self.retry_all([bare[0], bare[0]])
        stale = {**bare[1], "location_check": {**bare[1]["location_check"], "input_sha256": "0" * 64}}
        with self.assertRaisesRegex(Conflict, "changed since"):
            self.retry_all([bare[0], stale])
        self.assertEqual({q["location_check"]["attempt"] for q in self.frq.assignment(self.plan["id"])["questions"]}, {0})  # All or none.
        # This time the blank second page fails, after the first page gave every question its area.
        self.reader.fail_reading = False
        self.reader.reads = 0  # The fixture's odd reads are the page with the questions, its even reads the blank page.
        self.reader.extra = [("2", 6), ("3", 10)]
        read = self.reader.recognize
        def recognize(image):
            result = read(image)
            if result["transcript"]["plain_text"] == "Blank page":
                raise ProviderError("Synthetic OCR unavailable.")
            return result
        self.reader.recognize = recognize
        with self.frq.inbox.worker_lock():  # The worker holds it through every page read; a retry used to be refused then.
            retried = {q["question_id"]: q for q in self.retry_all(bare)["questions"]}
        self.assertEqual([retried[q["question_id"]]["location_check"]["attempt"] for q in bare], [1, 1])
        questions = self.finish_checks()
        self.assertEqual([questions[n]["location_check"]["status"] for n in "23"], ["failed", "failed"])
        self.assertTrue(questions["2"]["location_check"]["result"]["regions"])
        # A suggested area is retried on its own, never with the others, and only its failed page is read again.
        with self.assertRaisesRegex(Conflict, "without an answer area"):
            self.retry_all([questions["2"]])
        self.reader.recognize = read
        self.reader.reads = 1  # The blank page is the fixture's even read.
        check = questions["2"]["location_check"]
        with self.frq.inbox.worker_lock():
            self.frq.retry_location(self.plan["id"], questions["2"]["question_id"], {"check_id": check["id"], "input_sha256": check["input_sha256"]})
        check = self.finish_checks()["2"]["location_check"]
        self.assertEqual((check["status"], check["attempt"], self.reader.reads), ("needs_review", 2, 2))  # Page 1 kept from attempt 1.

    def test_the_assignment_page_counts_the_blank_pages_read_while_a_check_runs(self):
        self.upload()
        self.assertFalse(self.frq.local_work(self.plan["id"])["active"])  # Nothing queued yet: the worker has not discovered the check.
        for _ in range(10):
            self.frq.process_location_one()
            with connect(self.path) as db:
                if db.execute("SELECT COUNT(*) FROM frq_template_readings").fetchone()[0]:
                    break
        work = self.frq.local_work(self.plan["id"])
        self.assertTrue(work["active"])
        self.assertEqual([(t["read"], t["pages"], t["questions"]) for t in work["templates"]], [(1, 2, 1)])
        self.assertEqual(work["versions"], {"left": 0, "done": 0, "failed": 0, "total": 0})
        self.finish_check()
        self.assertEqual(self.frq.local_work(self.plan["id"]), {"versions": {"left": 0, "done": 0, "failed": 0, "total": 0}, "templates": [], "active": False})

    def test_one_model_load_reads_the_blank_pages_ahead_for_their_own_turns(self):
        batches = []
        def recognize_many(images):
            batches.append(len(images))
            return [self.reader.recognize(image) for image in images]
        self.reader.recognize_many = recognize_many
        self.upload()
        check = self.finish_check()
        self.assertEqual(check["status"], "needs_confirmation")
        # Both pages in one load; the second waited for its own turn and was read by the layout model there.
        self.assertEqual((batches, self.reader.reads, self.reader.layouts), ([2], 2, 2))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_template_readings").fetchone()[0], 2)
        self.assertEqual(self.frq._template_ocr_ahead, {})  # Each reading kept is used once.

    def test_confirm_all_confirms_matching_areas_together_and_refuses_anything_else_whole(self):
        for number in ("2", "3"):
            self.frq.upload_rubric(self.plan["id"], f"1_Question_{number}.csv", encode(export_rows(question=int(number))).encode(), {"request_id": str(uuid.uuid4())})
        self.reader.extra = [("2", 6), ("3", 10)]
        self.upload()
        questions = self.finish_checks()
        self.assertEqual([questions[n]["location_check"]["status"] for n in "123"], ["needs_confirmation", "needs_review", "needs_confirmation"])
        matching = [questions["1"], questions["3"]]
        with self.assertRaisesRegex(ValueError, "Explicitly confirm"):
            self.confirm_all(matching, confirm_locations=False)
        with self.assertRaisesRegex(ValueError, "listed twice"):
            self.confirm_all([questions["1"], questions["1"]])
        # A check that needs manual review, or one that changed since it was drawn, refuses the whole request.
        with self.assertRaisesRegex(Conflict, "printed points match"):
            self.confirm_all(list(questions.values()))
        stale = {**questions["3"], "location_check": {**questions["3"]["location_check"], "input_sha256": "0" * 64}}
        with self.assertRaisesRegex(Conflict, "changed since"):
            self.confirm_all([questions["1"], stale])
        self.assertFalse(any(q["location_check"]["status"] == "confirmed" for q in self.frq.assignment(self.plan["id"])["questions"]))
        result = {q["question_id"]: q for q in self.confirm_all(matching)["questions"]}
        for n in "13":
            q = result[questions[n]["question_id"]]
            self.assertEqual(q["location_check"]["label"], "Locations confirmed · printed points match")
            self.assertEqual([r["region"] for r in q["content"]["regions"]], [r["region"] for r in questions[n]["location_check"]["result"]["regions"]])
            self.assertEqual(q["content"]["printed_number"], n)
        self.assertEqual(result[questions["2"]["question_id"]]["location_check"]["status"], "needs_review")
        with connect(self.path) as db:
            rows = db.execute("SELECT manual_review,detail_json FROM frq_location_reviews WHERE assignment_id=?", (self.plan["id"],)).fetchall()
        self.assertEqual([(row["manual_review"], json.loads(row["detail_json"])["confirmed_together"]) for row in rows], [(0, True)] * 2)
        with self.assertRaises(Conflict):
            self.confirm_all(matching)

    def test_failed_reading_is_retained_not_retried_automatically(self):
        self.upload()
        self.reader.fail_reading = True
        check = self.finish_check()
        self.assertEqual(check["status"], "failed")
        self.assertEqual(check['reading_progress']['completed'], 0)
        for _ in range(8):
            self.frq.process_location_one()
        self.assertEqual(self.reader.reads, 1)
        self.reader.fail_reading = False
        self.reader.reads = 0
        self.frq.retry_location(self.plan["id"], self.qid, {"check_id": check["id"], "input_sha256": check["input_sha256"]})
        self.assertEqual(self.finish_check()["status"], "needs_confirmation")
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_template_readings").fetchone()[0], 3)

    def test_a_missing_read_needs_review_which_a_retry_cannot_change(self):
        self.upload()
        self.reader.missing = True
        check = self.finish_check()
        self.assertEqual((check["status"], check["result"]["regions"]), ("needs_review", []))
        # Nothing failed, so a retry would read the very same pages (2026-09-25): the check is reviewed instead.
        with self.assertRaisesRegex(Conflict, "review it instead"):
            self.frq.retry_location(self.plan["id"], self.qid, {"check_id": check["id"], "input_sha256": check["input_sha256"]})

    def test_a_low_confidence_read_needs_review(self):
        self.upload()
        self.reader.low_confidence = True
        self.assertEqual(self.finish_check()["status"], "needs_review")

    def test_a_truncated_read_fails_and_is_retried_at_most_twice(self):
        self.upload()
        self.reader.truncated = True
        check = self.finish_check()
        self.assertEqual(check["status"], "failed")
        for attempt in (1, 2):
            self.frq.retry_location(self.plan["id"], self.qid, {"check_id": check["id"], "input_sha256": check["input_sha256"]})
            check = self.finish_check()
            self.assertEqual((check["status"], check["attempt"]), ("failed", attempt))
        self.assertEqual(self.reader.reads, 3)  # The failed page, once per attempt.
        with self.assertRaises(Conflict):
            self.frq.retry_location(self.plan["id"], self.qid, {"check_id": check["id"], "input_sha256": check["input_sha256"]})

    def test_a_new_rubric_invalidates_an_unconfirmed_check_and_keeps_a_confirmed_area(self):
        self.upload()
        self.finish_check()
        command = self.confirmation()
        self.configure_rubric(.5)
        command["version"] = self.question()["version"]
        with self.assertRaises(Conflict):  # A check nobody confirmed was of the rubric it read: it is asked again.
            self.frq.confirm_location(self.plan["id"], self.qid, command)
        self.finish_check()
        self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation())
        confirmed = self.question()["location_check"]["review_id"]
        with connect(self.path) as db:
            checks = db.execute("SELECT COUNT(*) FROM frq_location_checks").fetchone()[0]
        # A re-import with the same maximum (requirement, 2026-10-11): the confirmed area is carried by a receipt, nothing is read or
        # asked again, and a batch is no longer refused for it.
        self.configure_rubric(.75)
        status = self.question()["location_check"]
        self.assertEqual(status["status"], "confirmed")
        self.assertFalse(self.frq.process_location_one())
        with connect(self.path) as db:
            detail = json.loads(db.execute("SELECT detail_json FROM frq_location_reviews WHERE id=?", (status["review_id"],)).fetchone()[0])
            imported = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='rubric_imported' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_location_checks").fetchone()[0], checks)
        self.assertEqual(detail["carried_forward_from"], confirmed)
        self.assertIn("same maximum", detail["carried_forward_reason"])
        self.assertTrue(imported["location_carried_forward"])
        self.queue()
        # Another maximum is what the printed points were compared with: that confirmation is asked again.
        q = self.question()
        self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": "-1 Arithmetic error",
            "rules": {"ceiling": q["content"]["max_points"] / 2, "items": [{"id": "R1", "description": "Arithmetic error", "points": -1, "original_source_text": "-1 Arithmetic error"}]}})
        self.assertEqual(self.question()["location_check"]["status"], "pending")
        with connect(self.path) as db:
            imported = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='rubric_imported' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertFalse(imported["location_carried_forward"])

    def test_a_first_rubric_has_no_area_to_carry(self):
        with connect(self.path) as db:
            first = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='rubric_imported' ORDER BY rowid LIMIT 1").fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_location_reviews").fetchone()[0], 0)
        self.assertFalse(first["location_carried_forward"])

    def test_multiple_templates_and_interruption_need_manual_review(self):
        first = self.upload("A", confirm=False)
        self.upload("B", confirm=False)
        self.assertEqual(self.question()['location_check']['status'], 'waiting_templates')
        self.assertFalse(self.frq.process_location_one())
        self.assertEqual(self.reader.reads, 0)
        confirm_templates(self.frq, self.plan['id'], first['id'])
        check = self.finish_check()
        self.assertEqual(check['status'], 'needs_confirmation')
        with connect(self.path) as db:
            db.execute("UPDATE frq_location_checks SET status='running' WHERE id=?", (check["id"],))
        self.frq.process_location_one()
        self.assertEqual(self.question()["location_check"]["status"], "failed")
        self.frq.confirm_location(self.plan["id"], self.qid, self.confirmation(manual_review=True, notes="Inspected template A, all answer space and its ten-point label."))
        self.assertEqual(self.question()["location_check"]["status"], "confirmed")

    def test_choose_template_runs_ocr_without_approving_or_changing_rubric(self):
        first = self.upload("A", confirm=False)
        self.upload("B", confirm=False)
        self.assertEqual(self.question()['location_check']['status'], 'waiting_templates')
        q = self.question()
        plan = self.frq.select_location_template(self.plan["id"], self.qid,
            {"version": q["version"], "template_id": first["id"]})
        chosen = plan["questions"][0]
        self.assertEqual(chosen["rubric_id"], q["rubric_id"])
        self.assertEqual(chosen["content"]["regions"], [])
        self.assertEqual(chosen["location_check"]["status"], "waiting_templates")
        confirm_templates(self.frq, self.plan['id'], first['id'])
        self.assertEqual(self.finish_check()["status"], "needs_confirmation")
        with self.assertRaises(Conflict):
            self.queue()

    def test_reading_status_reports_shared_page_progress(self):
        template = self.upload()
        labels, completed = [], []
        fingerprint = self.question()['location_check']['input_sha256']
        for _ in range(6):
            self.frq.process_location_one()
            status = self.question()["location_check"]
            labels.append(status["label"])
            progress = status['reading_progress']
            self.assertEqual(progress['template_id'], template['id'])
            self.assertEqual(progress['total'], 2)
            completed.append(progress['completed'])
            self.assertEqual(status['input_sha256'], fingerprint)
            compact = self.frq.overview()['assignments'][0]['workflow']['setup']['questions'][0]
            self.assertEqual(compact['location_check']['reading_progress'], progress)
            if status["status"] == "needs_confirmation":
                break
        self.assertTrue(any("0 of 2 pages read" in label for label in labels), labels)
        self.assertTrue(any("1 of 2 pages read" in label for label in labels), labels)
        self.assertEqual(status["status"], "needs_confirmation", labels)
        self.assertEqual(set(completed), {0, 1, 2})
        self.assertEqual(completed, sorted(completed))

    def test_one_template_choice_covers_unconfigured_questions_only(self):
        first = self.upload("A", confirm=False)
        second = self.upload("B", confirm=False)
        for number in ("18", "19"):
            self.frq.upload_rubric(self.plan["id"], f"1_Question_{number}.csv", encode(export_rows()).encode(), {"request_id": str(uuid.uuid4())})
        kept, imported = self.question(), self.frq.assignment(self.plan["id"])["questions"][1:]
        self.assertTrue(kept["content"]["regions"])
        self.assertEqual([q["content"]["regions"] for q in imported], [[], []])
        with self.assertRaisesRegex(ValueError, "other questions"):
            self.frq.select_location_template(self.plan["id"], imported[0]["question_id"],
                {"version": imported[0]["version"], "template_id": first["id"], "apply_to_all": "yes"})
        plan = self.frq.select_location_template(self.plan["id"], imported[0]["question_id"],
            {"version": imported[0]["version"], "template_id": first["id"], "apply_to_all": True})
        self.assertEqual([q["content"]["template_id"] for q in plan["questions"][1:]], [first["id"], first["id"]])
        self.assertEqual([q["rubric_id"] for q in plan["questions"][1:]], [q["rubric_id"] for q in imported])
        # A question with answer areas keeps its own revision and template.
        self.assertEqual(plan["questions"][0]["question_revision_id"], kept["question_revision_id"])
        self.assertEqual(plan["questions"][0]["content"]["regions"], kept["content"]["regions"])
        # Without the explicit choice only the named question changes.
        again = self.frq.select_location_template(self.plan["id"], imported[1]["question_id"],
            {"version": plan["questions"][2]["version"], "template_id": second["id"]})
        self.assertEqual([q["content"]["template_id"] for q in again["questions"][1:]], [first["id"], second["id"]])
        with connect(self.path) as db:
            events = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM frq_events WHERE kind='template_selected' ORDER BY id")]
        self.assertEqual([e.get("applied_from_question_id") for e in events], [None, imported[0]["question_id"], None])

    def test_missing_endpoint_fails_before_calling_ocr(self):
        self.upload()
        with patch("grading.config.endpoint", return_value=""):
            check = self.finish_check()
        self.assertEqual(check["status"], "failed")
        self.assertIn("not configured", check["error"])
        self.assertEqual(self.reader.reads, 0)

    def test_a_retry_reads_with_the_local_model_connected_after_the_check_was_queued(self):
        """2026-09-25: checks queued before the local model was connected failed "not configured", then "endpoint changed" on every
        retry, because a retry kept the address the check was queued with. An explicit retry of a failed check records the current
        address (migration 023) and reads; nothing else about the check may change, and the event keeps the old address."""
        import sqlite3
        from grading.models import digest
        self.upload()
        with patch("grading.config.endpoint", return_value=""):
            check = self.finish_check()
        self.assertIn("not configured", check["error"])
        self.frq.retry_location(self.plan["id"], self.qid, {"check_id": check["id"], "input_sha256": check["input_sha256"]})
        retried = self.finish_check()
        self.assertEqual((retried["id"], retried["attempt"], retried["status"]), (check["id"], 1, "needs_confirmation"))
        self.assertGreater(self.reader.reads, 0)  # The blank's pages were read, with the model connected after queueing.
        with connect(self.path) as db:
            profile = json.loads(db.execute("SELECT profile_json FROM frq_location_checks WHERE id=?", (check["id"],)).fetchone()[0])
            event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='template_verification_retry' ORDER BY id DESC LIMIT 1").fetchone()[0])
            with self.assertRaisesRegex(sqlite3.DatabaseError, "immutable"):
                db.execute("UPDATE frq_location_checks SET profile_json='{}' WHERE id=?", (check["id"],))
        self.assertEqual(profile["endpoint_sha256"], digest(b"http://127.0.0.1:11435/v1"))
        self.assertEqual((event["previous_endpoint_sha256"], event["endpoint_sha256"]), (digest(b""), profile["endpoint_sha256"]))

    def test_incompatible_page_count_cannot_bypass_review(self):
        def single_page(files, entrypoint=None):
            result = prepared(files, entrypoint)
            result["pages"] = result["pages"][:1]
            return result
        with patch("document_identity.template_conversion.convert_template", side_effect=single_page):
            self.upload(confirm=False)
        check = self.question()['location_check']
        self.assertEqual(check['status'], 'waiting_templates')
        self.assertIn('2 pages', ' '.join(check['context']['issues']))
        self.assertEqual(self.reader.reads, 0)
        with self.assertRaises(Conflict):
            self.queue()

    def test_stamps_only_page_count_does_not_block_exam_template_reading(self):
        # A retained one-page label is an identity reference, not the exam length.
        with connect(self.path) as db:
            original = dict(db.execute("SELECT * FROM assignment_versions WHERE id=?", (self.version_id,)).fetchone())
            original.update(id=str(uuid.uuid4()), label="Stamps only", page_count=1,
                compiler_json=json.dumps({"generator": "faculty-stamps-only"}))
            db.execute("INSERT INTO assignment_versions VALUES(" + ",".join("?" for _ in original) + ")", tuple(original.values()))
        self.upload()
        plan = self.frq.assignment(self.plan["id"])
        current = plan["questions"][0]
        plan = self.frq.save_question(plan["id"], {"version": plan["version"], "question_key": "q1",
            "assignment_version_id": original["id"], "content": current["content"]})
        q = plan["questions"][-1]
        self.frq.import_rubric(plan["id"], q["question_id"], {"version": q["version"], "source_text": "-1 Arithmetic error"})
        confirm_templates(self.frq, self.plan['id'])
        for _ in range(30):
            self.frq.process_location_one()
        plan = self.frq.assignment(plan["id"])
        q = plan["questions"][-1]
        check = q["location_check"]
        self.assertEqual(check["status"], "needs_confirmation")
        self.assertTrue(check["context"]["document_stamps_only"])
        self.assertEqual(check["context"]["template"]["page_count"], 2)
        self.assertEqual(self.reader.reads, 2)
        self.assertEqual(q["content"]["regions"], current["content"]["regions"])
        # Confirm an actual page-2 area without pretending the label issued it.
        region = {**check["result"]["regions"][0], "page": 2}
        plan = self.frq.confirm_location(plan["id"], q["question_id"], {"version": q["version"],
            "check_id": check["id"], "input_sha256": check["input_sha256"], "printed_number": "1",
            "template_id": check["context"]["template"]["id"], "regions": [region], "confirm_locations": True})
        q = plan["questions"][-1]
        self.assertEqual(q["location_check"]["status"], "confirmed")
        with connect(self.path) as db, patch.object(self.frq.manual, "_source", return_value={"pages": [{"page_number": 1}]}):
            event, participants = self.frq.manual._context(db, plan["event_id"])
            with self.assertRaisesRegex(Conflict, "every reviewed exam page"):
                self.frq.source(db, plan, q, participants[0], event)
        # End to end: a first-page label, an even cadence and the instructor's Inbox review make a gradable paper,
        # as long as it has as many pages as the chosen blank exam.
        q = self.frq.approve_rubric(plan["id"], q["question_id"], {"version": q["version"], "rubric_id": q["rubric_id"],
            "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})["questions"][-1]
        finalize_rubrics(self.frq, plan["id"])
        with connect(self.path) as db:
            two = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id",
                             (self.section["id"], self.enrollment)).fetchone()[0]
            with patch.object(self.frq.manual, "_source", return_value={"pages": [{"page_number": n} for n in (1, 2, 3)]}):
                with self.assertRaisesRegex(Conflict, "reviewed scan has 3 pages, but the chosen blank exam has 2"):
                    self.frq.source(db, plan, q, participants[0], event)
            # EMPTY BACKS (2026-10-17): a side past the blank exam's last page that carries no writing is no page of the paper.
            sides = {1: {"scan_page_id": "a"}, 2: {"scan_page_id": "b"}, 3: {"scan_page_id": "c"}}
            with patch.object(type(self.frq), "_empty_side", side_effect=lambda db, page_id: page_id == "c"):
                self.assertTrue(self.frq._pages_complete(db, sides, 2))
                self.assertFalse(self.frq._pages_complete(db, sides, 1))  # page 2 has writing on it
                self.assertFalse(self.frq._pages_complete(db, {1: sides[1], 3: sides[3]}, 2))  # page 2 is missing
            images = {}
            for name, marks in (("empty", [(5, 5, 60, 2000)]), ("written", [(200, 300, 1500, 900)])):
                image = Image.new("L", (1700, 2200), 255)
                image.paste(0 if name == "written" else 200, marks[0])  # an edge line (light) on the empty one, writing on the other
                if name == "empty":
                    image.paste(0, (2, 2, 40, 2190))  # a dark scanner edge inside the 4% margin
                out = io.BytesIO()
                image.save(out, format="PNG")
                images[name] = out.getvalue()
            with patch.object(self.frq.inbox, "read_artifact", side_effect=lambda artifact: ("page.png", images[artifact])), \
                    patch.object(db, "execute", side_effect=lambda sql, args: type("R", (), {"fetchone": lambda self: (args[0].split(":")[1],)})()):
                type(self.frq)._empty_sides.clear()
                self.assertTrue(self.frq._empty_side(db, "page:empty"))
                self.assertFalse(self.frq._empty_side(db, "page:written"))
        # The first scan is a three-sided paper still awaiting its Inbox review: grouped, never assembled.
        for enrollment, sides in ((self.enrollment, 3), (two, 2)):
            copy_id = self.ready_batch(original["id"], enrollment)["copies"][0]["id"]
            job = self.inbox.submit({"action": "scan.receive"}, [{"filename": f"stack-{sides}.pdf", "content": f"%PDF-stack-{sides}".encode()}], source="email", sender="scanner",
                                    original=f"mail-{sides}".encode(), idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=sides == 3)
            job = self.inbox.get(job["id"])
            retained = next(a for a in job["artifacts"] if a["role"] == "input")
            with connect(self.path) as db:
                token = db.execute("SELECT token FROM page_stamps WHERE printed_copy_id=?", (copy_id,)).fetchone()[0]
            for side in range(1, sides + 1):
                self.scans._retain_page(job, retained, {"index": side, "pdf": f"pdf-{sides}-{side}".encode(), "png": f"png-{sides}-{side}".encode(),
                    "decoder": {}, "blank": False, "candidates": [{"payload": token}] if side == 1 else []})
            preview = self.scans.sequence.preview(job["message_id"], sides)
            self.scans.sequence.apply(job["message_id"], sides, preview["fingerprint"])
            self.assertEqual(self.scans.sequence.assemble_reviewed(job["message_id"])["assembled"], 0 if sides == 3 else 1)
            context = self.frq.assignment(plan["id"])["questions"][-1]["location_check"]["context"]
            if sides == 3:
                # The scanned papers and the blank exam disagree: said when the template is in use, not first at grading.
                self.assertEqual((context["paper_sides"], context["issues"]), (3, ["Your scanned papers have 3 written sides, but this blank exam has 2 pages. "
                    "Choose or upload the blank exam that matches the printed paper; papers cannot be graded against this one."]))
            else:
                self.assertEqual((context["paper_sides"], context["issues"]), (2, []))
        # A read over several questions asks for a document's written sides once (its `memo`); the status is the same without it.
        with connect(self.path) as db:
            asked, memo = [], {}
            db.set_trace_callback(lambda sql: asked.append(sql) if "SUM(sp.status!='blank')" in sql else None)
            first, again = self.frq.location_status(db, plan["id"], q, memo=memo), self.frq.location_status(db, plan["id"], q, memo=memo)
            self.assertEqual((len(asked), first), (1, again))
            self.assertEqual(self.frq.location_status(db, plan["id"], q), first)
            self.assertEqual(len(asked), 2)
        result = self.frq.preview(plan["id"], {"question_ids": [q["question_id"]]})
        self.assertEqual([(member["enrollment_id"], [region["page"] for region in member["source"]["regions"]]) for member in result["eligible"]], [(two, [2])])
        edited = self.frq.save_question(plan["id"], {"version": plan["version"], "question_key": "q1",
            "assignment_version_id": original["id"], "content": {**q["content"], "expected_answer": "Updated solution"}})
        self.assertEqual(edited["questions"][-1]["content"]["regions"][0]["page"], 2)

    def test_changed_inputs_during_reading_do_not_publish_a_current_check(self):
        self.upload()
        original = self.reader.locate
        def change(payload, images):
            result = original(payload, images)
            self.configure_rubric(.5)
            return result
        self.frq.process_location_one()
        with patch.object(self.reader, "locate", side_effect=change):
            self.frq.process_location_one()
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status FROM frq_location_checks").fetchone()[0], "stale")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_template_readings").fetchone()[0], 1)
        self.assertEqual(self.question()["location_check"]["status"], "pending")


class ManagedLocationWorkflow(LocationWorkflow):
    managed = True
