"""Rubric-first setup from an empty plan, with real API/storage and synthetic CSVs."""
import io
import json
import sqlite3
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict
from rosters import connect
import test_exam_grading as exam_fixture
from test_assignment_templates import prepared
from test_gradescope_rubrics import EXAMPLES, encode, export_rows
from test_grading_locations import ReadingProvider, confirm_templates
from frq_fixtures import finalize_rubrics


class RubricUploads(unittest.TestCase):
    managed = False
    document_version = exam_fixture.ExamGradingTests.document_version
    ready_batch = exam_fixture.ExamGradingTests.ready_batch
    reviewed_source = exam_fixture.ExamGradingTests.reviewed_source

    def setUp(self):
        exam_fixture.ExamGradingTests.setUp(self)
        self.frq = self.app.extensions["frq_grading"]
        self.plan = self.frq.create_assignment({"event_id": self.event["id"]})
        self.assertEqual(self.plan["questions"], [])
        self.reader = ReadingProvider()
        self.reader.number = "18"
        self.enterContext(patch.object(self.frq, "provider", return_value=self.reader))
        self.enterContext(patch("grading.config.endpoint", return_value="http://127.0.0.1:11435/v1"))
        self.enterContext(patch("document_identity.template_conversion.convert_template", side_effect=prepared))

    def upload(self, filename="1_Question_18_a_and_b.csv", content=None, **data):
        return self.frq.upload_rubric(self.plan["id"], filename, content or encode(export_rows()).encode(),
            {"request_id": str(uuid.uuid4()), **data})

    def uploaded_questions(self):
        return self.frq.assignment(self.plan["id"])["questions"]

    def template(self):
        return self.frq.templates.upload(self.assignment["id"], "Blank exam", str(uuid.uuid4()), {"blank.pdf": b"Synthetic template"})

    def finish(self):
        confirm_templates(self.frq, self.plan['id'])
        for _ in range(30):
            self.frq.process_location_one()
            question = self.uploaded_questions()[0]
            if question["location_check"]["status"] in ("needs_confirmation", "needs_review", "failed"):
                return question
        self.fail("Location verification did not finish")

    def test_eight_csvs_create_exact_drafts_from_empty_plan(self):
        for internal, (maximum, points) in enumerate(EXAMPLES, 1):
            source = encode(export_rows(maximum, points, question=internal)).encode()
            result = self.upload(f"{internal}_Question_{internal+17}.csv", source)
            self.assertEqual(result["printed_number"], str(internal+17))
        questions = self.uploaded_questions()
        self.assertEqual([q["content"]["max_points"] for q in questions], [4,2,2,4,6,8,6,8])
        self.assertEqual([q["question_key"] for q in questions], [f"q{n}" for n in range(18,26)])
        self.assertEqual(sum(len(q["rubric"]["rules"]["items"]) for q in questions), 89)
        for q in questions:
            self.assertEqual(q["content"]["regions"], [])
            self.assertIsNone(q["rubric"]["approved_at"])
            self.assertEqual(q["rubric"]["rules"]["mode"], "positive")
            _, original = self.inbox.read_artifact(q["rubric"]["source_artifact_id"])
            self.assertEqual(original.decode(), q["rubric"]["source_text"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            for q in questions:
                with self.assertRaises(Conflict):
                    self.frq.require_location_review(db, self.plan["id"], q)

    def test_template_before_rubric_automatically_checks_and_requires_confirmation(self):
        template = self.template()
        self.upload()
        q = self.finish()
        check = q["location_check"]
        self.assertEqual(check["status"], "needs_confirmation")
        self.assertEqual(q["content"]["regions"], [])
        command = {"version": q["version"], "check_id": check["id"], "input_sha256": check["input_sha256"],
            "template_id": template["id"], "printed_number": "18", "regions": check["result"]["regions"]}
        with self.assertRaises(ValueError):
            self.frq.confirm_location(self.plan["id"], q["question_id"], command)
        result = self.frq.confirm_location(self.plan["id"], q["question_id"], {**command, "confirm_locations": True})
        self.assertEqual(result["questions"][0]["location_check"]["status"], "confirmed")
        self.assertIsNone(result["questions"][0]["rubric"]["approved_at"])
        # An imported question has no statement; the blank exam's own reading supplies it as context for the grader.
        confirmed = result["questions"][0]
        self.assertEqual(q["content"]["problem"], "")
        self.assertIn("18. (10 points) Solve this problem.", confirmed["content"]["problem"])
        # The answer key is saved later without undoing that confirmation, the rubric pin, or the areas.
        saved = self.frq.save_answer_key(self.plan["id"], {"version": result["version"], "questions": [{"question_id": q["question_id"],
            "version": confirmed["version"], "answer_key": [{"label": "", "accept": ["x=6"]}]}]})["questions"][0]
        self.assertEqual((saved["location_check"]["status"], saved["rubric_id"], saved["content"]["regions"], saved["content"]["problem"]),
                         ("confirmed", confirmed["rubric_id"], confirmed["content"]["regions"], confirmed["content"]["problem"]))
        self.assertNotEqual(saved["question_revision_id"], confirmed["question_revision_id"])
        with connect(self.path) as db:
            receipts = [json.loads(row[0]) for row in db.execute("SELECT detail_json FROM frq_location_reviews WHERE question_id=? ORDER BY rowid", (q["question_id"],))]
        self.assertEqual((len(receipts), "carried_forward_from" in receipts[0], "Only the answer key" in receipts[1]["carried_forward_reason"]), (2, False, True))
        # Nor does the instructor's correction of one rubric line's wording: the areas are pinned to the rubric revision, and no page,
        # area, template or maximum changed.
        line = saved["rubric"]["rules"]["items"][0]
        fixed = self.frq.correct_rubric_line(self.plan["id"], q["question_id"], {"version": saved["version"], "rubric_id": saved["rubric_id"], "sha256": saved["rubric"]["sha256"],
            "item_id": line["id"], "description": line["description"] + " (corrected)"})["questions"][0]
        self.assertEqual((fixed["location_check"]["status"], fixed["content"]["regions"]), ("confirmed", confirmed["content"]["regions"]))
        self.assertNotEqual(fixed["rubric_id"], saved["rubric_id"])
        with connect(self.path) as db:
            last = json.loads(db.execute("SELECT detail_json FROM frq_location_reviews WHERE question_id=? AND rubric_id=?", (q["question_id"], fixed["rubric_id"])).fetchone()[0])
        self.assertIn("Only the wording of one rubric line changed", last["carried_forward_reason"])
        # Nor do the instructor's edits of lines' wording and points before the rubric reviewer (2026-09-24), or a comment alone.
        second = fixed["rubric"]["rules"]["items"][1]
        edited = self.frq.edit_rubric(self.plan["id"], q["question_id"], {"version": fixed["version"], "rubric_id": fixed["rubric_id"], "sha256": fixed["rubric"]["sha256"],
            "lines": [{"item_id": second["id"], "description": second["description"] + " (edited)"}], "comments": {second["id"]: "Either form."}})["questions"][0]
        self.assertEqual((edited["location_check"]["status"], edited["content"]["regions"]), ("confirmed", confirmed["content"]["regions"]))
        self.assertNotEqual(edited["rubric_id"], fixed["rubric_id"])
        with connect(self.path) as db:
            last = json.loads(db.execute("SELECT detail_json FROM frq_location_reviews WHERE question_id=? AND rubric_id=?", (q["question_id"], edited["rubric_id"])).fetchone()[0])
        self.assertIn("Only rubric lines' wording or points changed", last["carried_forward_reason"])

    def test_rubric_before_template_stays_blocked_even_if_rubric_approved(self):
        self.upload()
        q = self.uploaded_questions()[0]
        self.frq.approve_rubric(self.plan["id"], q["question_id"], {"version": q["version"], "rubric_id": q["rubric_id"],
            "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})
        finalize_rubrics(self.frq, self.plan["id"])
        with self.assertRaisesRegex(Conflict, "blank template"):
            self.frq.preview(self.plan["id"], {"question_ids": [q["question_id"]], "mode": "local", "operation": "recognize"})
        self.template()
        self.assertEqual(self.finish()["location_check"]["status"], "needs_confirmation")

    def test_retries_and_reuploads_are_idempotent_without_overwriting(self):
        request = str(uuid.uuid4())
        first = self.upload(request_id=request)
        original = self.uploaded_questions()[0]
        for data in ({"request_id": request}, {}):
            repeated = self.upload(**data)
            self.assertEqual(repeated["status"], "already_imported")
            self.assertEqual(repeated["question_id"], first["question_id"])
        self.assertEqual(self.uploaded_questions(), [original])
        with self.assertRaises(Conflict):
            self.upload("2_Question_19.csv", request_id=request)
        with self.assertRaisesRegex(Conflict, "Import revision"):
            self.upload(content=encode(export_rows(descriptions=["Changed", "A", "B", "None"])).encode())
        self.assertEqual(self.uploaded_questions(), [original])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_uploads").fetchone()[0], 1)

    def test_a_misspelt_filename_still_names_the_question(self):
        # Exam 2 Version C's "8_Quesiton_25.csv" (2026-10-25); a number after another word still names nothing.
        self.assertEqual(self.upload("8_Quesiton_25.csv")["printed_number"], "25")
        self.assertEqual(self.upload("Points_4.csv", encode(export_rows(descriptions=["Other", "A", "B", "None"])).encode())["printed_number"], "")

    def test_ambiguous_number_requires_review_without_using_internal_index(self):
        self.upload("rubric.csv")
        self.template()
        q = self.finish()
        self.assertEqual(q["content"]["printed_number"], "")
        self.assertEqual(q["location_check"]["context"]["printed_number"], "")
        self.assertEqual(q["location_check"]["status"], "needs_review")
        self.assertEqual(self.reader.reads, 0)

    def test_invalid_file_or_missing_metadata_creates_no_questions(self):
        for filename, source in (("rubric.txt", b"text"), ("rubric.csv", b"id,description,points\nR1,Error,-1"),
                ("rubric.csv", encode(export_rows()[:-1]).encode()), ("rubric.csv", b"\xff")):
            with self.subTest(filename=filename, source=source), self.assertRaises(ValueError):
                self.upload(filename, source)
        self.assertEqual(self.uploaded_questions(), [])

    def test_versions_are_explicit_when_ambiguous_and_pins_do_not_cross_versions(self):
        second = self.document_version("B")
        with self.assertRaises(ValueError):
            self.upload()
        with self.assertRaises(ValueError):
            self.upload(assignment_version_id=str(uuid.uuid4()))
        self.upload(assignment_version_id=self.version_id)
        original = self.uploaded_questions()[0]
        self.upload(assignment_version_id=second)
        self.assertEqual(self.uploaded_questions()[0], original)
        self.assertEqual({q["assignment_version_id"] for q in self.uploaded_questions()}, {self.version_id, second})

    def test_failed_publication_rolls_back_question_and_retry_reuses_source(self):
        request = str(uuid.uuid4())
        with patch.object(self.frq, "event", side_effect=RuntimeError("synthetic interruption")), self.assertRaises(RuntimeError):
            self.upload(request_id=request)
        self.assertEqual(self.uploaded_questions(), [])
        with connect(self.path) as db:
            sources = db.execute("SELECT COUNT(*) FROM inbox_messages").fetchone()[0]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_questions").fetchone()[0], 0)
        self.upload(request_id=request)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox_messages").fetchone()[0], sources)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM frq_rubric_uploads")

    def test_api_upload_needs_no_questions_and_preserves_same_origin_guard(self):
        url = f"/api/grading/assignments/{self.plan['id']}/rubric-uploads"
        def payload():
            return {"file": (io.BytesIO(encode(export_rows()).encode()), "1_Question_18.csv"),
                "request_id": str(uuid.uuid4()), "assignment_version_id": self.version_id}
        response = self.client.post(url, data=payload(), headers={**exam_fixture.HEADERS, "Origin": "https://different.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.uploaded_questions(), [])
        response = self.client.post(url, data=payload(), headers=exam_fixture.HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(self.uploaded_questions()[0]["content"]["printed_number"], "18")


class ManagedRubricUploads(RubricUploads):
    managed = True
