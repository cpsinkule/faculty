"""Real catalog/roster/source-review gates for instructor grading.

Raster and extracted PDF bytes are synthetic retained fixtures; these tests do
not claim to qualify a scanner or invoke a native PDF parser outside its sandbox.
"""
import copy
import csv
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict, Forbidden, NotFound

from app import create_app
from course_storage import timestamp
from document_identity.media import PROFILE
from exam_workflow.grading import GradingService
from exam_workflow.service import ExamService
from rosters import connect
from test_rosters import csv_bytes
from test_rooms import chart_bytes
from test_storage import temporary_storage

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


class ExamGradingTests(unittest.TestCase):
    managed = False

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = temporary_storage(self.directory.name) if getattr(self, "managed", False) else None
        self.app = create_app(storage=self.storage) if self.storage else create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.course = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Calculus", "term": "Fall", "year": 2026,
                                               "sections": [501], "is_current": True}, headers=HEADERS).json
        if self.storage:
            self.storage.reconcile_all()
        state = self.client.get("/api/state").json
        self.group = state["course_groups"][0]
        result = self.client.post(f"/api/course-groups/{self.group['id']}/sections/501/roster",
                                 data={"file": (io.BytesIO(csv_bytes()), "roster.csv")}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        self.documents, self.scans = self.app.extensions["documents"], self.app.extensions["document_scans"]
        self.inbox, self.rosters = self.app.extensions["inbox"], self.app.extensions["rosters"]
        self.path = self.rosters.path
        self.exams = self.app.extensions.get("exams") or ExamService(self.rosters, self.documents, self.scans,
                                                                    self.app.extensions["seating"], self.app.extensions["response_cards"])
        self.grading = GradingService(self.exams)
        catalog = self.documents.catalog()
        self.assignment = self.documents.create_assignment(catalog["definitions"][0]["id"], "exam-one", "Exam 1")
        self.section = catalog["sections"][0]
        with connect(self.path) as db:
            term_id = db.execute("SELECT term_id FROM course_sections WHERE id=?", (self.section["id"],)).fetchone()[0]
            self.enrollment = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? ORDER BY enrollment_id", (self.section["id"],)).fetchone()[0]
        self.event = self.exams.create({"term_id": term_id, "assignment_id": self.assignment["id"], "section_ids": [self.section["id"]]})
        self.version_id = self.document_version("A")
        self.batch = self.ready_batch()
        self.copy_id = self.batch["copies"][0]["id"]
        self.job, self.revision = self.reviewed_source()
        self.questions = [{"id": "q1", "label": "Question 1", "max_points": 10, "page": 1,
                           "criteria": [{"id": "method", "label": "Method", "points": 6}, {"id": "answer", "label": "Answer", "points": 4}],
                           "feedback": ["Check your sign."], "region": {"x": .1, "y": .1, "width": .8, "height": .4}},
                          {"id": "q2", "label": "Question 2", "max_points": 5, "page": 2}]
        self.grading.configure(self.event["id"], {"version": 0, "assignment_version_id": self.version_id, "questions": self.questions})

    def document_version(self, label):
        vid = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (vid, self.assignment["id"], label, "source.json", "a" * 64, "canonical.pdf", "b" * 64, 2, "{}", PROFILE, timestamp()))
        return vid

    def ready_batch(self, version_id=None, enrollment_id=None):
        batch = self.documents.create_batch(version_id or self.version_id, self.section["id"], 1, str(uuid.uuid4()), students=[enrollment_id or self.enrollment])
        with connect(self.path) as db:
            db.execute("UPDATE page_stamps SET state='issued' WHERE printed_copy_id IN (SELECT id FROM printed_copies WHERE print_batch_id=?)", (batch["id"],))
            db.execute("UPDATE print_batches SET status='ready',output_path='print.pdf',output_sha256=?,manifest_path='manifest.json',manifest_sha256=? WHERE id=?",
                       ("c" * 64, "d" * 64, batch["id"]))
        self.event = self.exams.attach_batch(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "batch_id": batch["id"]})
        return self.documents.get_batch(batch["id"])

    def reviewed_source(self, *, pages=(1, 2), gate=False, changed="", batch=None):
        batch = batch or self.batch
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "Alice-name-on-upload.pdf", "content": b"%PDF-fixture" + changed.encode()}],
                                source="email", sender="scanner", original=b"fixture mail", idempotency_key=str(uuid.uuid4()),
                                authenticated=True, instructor_review_required=gate)
        job = self.inbox.get(job["id"])
        original = next(a for a in job["artifacts"] if a["role"] == "input")
        with connect(self.path) as db:
            stamps = {r["page_number"]: dict(r) for r in db.execute("SELECT * FROM page_stamps WHERE printed_copy_id=?", (batch["copies"][0]["id"],))}
        for number in pages:
            self.scans._retain_page(job, original, {"index": number, "pdf": f"fixture-pdf-{number}-{changed}".encode(),
                "png": f"fixture-png-{number}-{changed}".encode(), "decoder": {}, "blank": False,
                "candidates": [{"payload": stamps[number]["token"]}]})
        item = self.scans.review_item(job["message_id"])
        revision = self.scans.select_assembly(batch["copies"][0]["id"], [p["id"] for p in item["pages"]], len(pages) < 2, "Instructor checked source pages")
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (job['message_id'],))
        return job, revision

    def participant(self):
        return next(p for p in self.grading.details(self.event["id"])["participants"] if p["enrollment_id"] == self.enrollment)

    def data(self, **extra):
        p = self.participant()
        return {"section_id": self.section["id"], "enrollment_id": self.enrollment,
                "version": p["version"], "assembly_revision_id": p["assembly_revision_id"], **extra}

    def mark(self, question="q1", **extra):
        return self.grading.save_mark(self.event["id"], self.data(question_id=question, score=6 if question == "q1" else 4,
                                       criterion_ids=["method"] if question == "q1" else [], feedback="Check your sign.", **extra))

    def complete(self):
        self.mark()
        self.mark("q2")
        return self.grading.finalize(self.event["id"], self.data())

    def test_exact_reviewed_work_regions_history_and_hide_names(self):
        work = self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q1", anonymous=True)
        self.assertEqual(work["region"], self.questions[0]["region"])
        self.assertEqual(work["assembly_revision_id"], self.revision["id"])
        self.assertEqual(len(work["pages"]), 2)
        self.assertTrue(work["participant"]["name"].startswith("Paper "))
        self.assertNotIn("Alice", json.dumps(work))
        self.assertNotIn("Example", json.dumps(work))
        self.assertNotIn("uin", json.dumps(work).lower())
        self.assertIn("may identify", work["anonymity_note"])
        self.mark()
        self.mark()
        work = self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q1")
        self.assertEqual([m["revision"] for m in work["history"]], [2, 1])
        self.assertEqual(work["version"], 2)

    def test_points_criteria_and_stale_writes_are_validated_atomically(self):
        for extra in ({"score": float("nan")}, {"score": float("inf")}, {"score": 10**400}, {"score": True}, {"score": 11},
                      {"score": 8}, {"criterion_ids": ["unknown"]}, {"criterion_ids": ["method", "method"]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.grading.save_mark(self.event["id"], self.data(question_id="q1", score=6, criterion_ids=["method"]) | extra)
        self.assertEqual(self.participant()["version"], 0)
        stale = self.data(question_id="q1", score=6, criterion_ids=["method"])
        self.mark()
        with self.assertRaises(Conflict):
            self.grading.save_mark(self.event["id"], stale)

    def test_deleted_unassembled_rescan_is_not_a_grading_source_link(self):
        """INBOX: deleted material cannot appear as work needing review."""
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "rescan.pdf", "content": b"%PDF-rescan"}],
                                source="email", sender="scanner", idempotency_key=str(uuid.uuid4()), authenticated=True)
        retained = self.inbox.get(job["id"])
        original = next(item for item in retained["artifacts"] if item["role"] == "input")
        with connect(self.path) as db:
            token = db.execute("SELECT token FROM page_stamps WHERE printed_copy_id=? AND page_number=1", (self.copy_id,)).fetchone()[0]
        self.scans._retain_page(retained, original, {"index": 1, "pdf": b"fixture-pdf-1-", "png": b"fixture-png-1-",
                                "decoder": {}, "blank": False, "candidates": [{"payload": token}]})
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (job["message_id"],))
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete")
        links = self.participant()["source_job_ids"]
        self.assertIn(self.job["id"], links)
        self.assertNotIn(job["id"], links)

    def test_finalize_release_reopen_and_idempotent_replay(self):
        with self.assertRaises(Conflict):
            self.grading.finalize(self.event["id"], self.data())
        with self.assertRaises(Conflict):
            self.grading.release(self.event["id"], self.data(request_id="early"))
        final = self.complete()
        self.assertEqual(final["status"], "finalized")
        with self.assertRaises(Conflict):
            self.mark()
        payload = self.data(request_id="release-1")
        released = self.grading.release(self.event["id"], payload)
        self.assertEqual(released["total"], 10)
        self.assertEqual(released, self.grading.release(self.event["id"], payload))
        self.assertEqual(self.grading.release_snapshot(released["id"]), released)
        with self.assertRaises(Conflict):
            self.grading.release(self.event["id"], payload | {"enrollment_id": "other"})
        self.assertEqual(len(list(csv.reader(io.StringIO(self.grading.export(self.event["id"]))))), 2)
        with self.assertRaises(ValueError):
            self.grading.reopen(self.event["id"], self.data(reason=""))
        self.grading.reopen(self.event["id"], self.data(reason="Correcting question 1 feedback"))
        with self.assertRaises(Conflict):
            self.grading.release_snapshot(released["id"])
        self.assertEqual(len(list(csv.reader(io.StringIO(self.grading.export(self.event["id"]))))), 1)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_grade_releases").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_grade_finalizations").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_grading_events WHERE kind='grade_reopened'").fetchone()[0], 1)

    def test_source_gate_or_authentication_blocks_all_grading_effects(self):
        with connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET instructor_review_required=1 WHERE id=?", (self.job["message_id"],))
        self.assertIsNone(self.participant()["assembly_revision_id"])
        with self.assertRaises(Conflict):
            self.mark()
        with connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET instructor_review_required=0,authenticated=0 WHERE id=?", (self.job["message_id"],))
        with self.assertRaises((Conflict, Forbidden)):
            self.mark()
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_mark_revisions").fetchone()[0], 0)

    def test_new_source_revision_invalidates_marks_and_old_form(self):
        self.mark()
        old = self.data(question_id="q2", score=4)
        _, newer = self.reviewed_source(changed="corrected")
        self.assertNotEqual(newer["id"], self.revision["id"])
        self.assertEqual(self.participant()["status"], "stale")
        with self.assertRaises(Conflict):
            self.grading.save_mark(self.event["id"], old)
        with self.assertRaises(Conflict):
            self.grading.finalize(self.event["id"], self.data())

    def test_rubric_changes_keep_history_and_block_old_release(self):
        self.complete()
        released = self.grading.release(self.event["id"], self.data(request_id="before-rubric"))
        questions = copy.deepcopy(self.questions)
        questions[1]["max_points"] = 7
        self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id, "questions": questions})
        self.assertEqual(self.participant()["status"], "stale")
        with self.assertRaises(Conflict):
            self.grading.release_snapshot(released["id"])
        self.assertEqual(self.participant()["graded_count"], 0)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_rubric_versions").fetchone()[0], 2)
        with self.assertRaises(Conflict):
            self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id, "questions": questions})

    def test_partial_submission_needs_explicit_missing_question_acknowledgment(self):
        # A second physical copy makes the instructor's submission choice explicit.
        other = self.ready_batch()
        _, partial = self.reviewed_source(pages=(1,), changed="partial", batch=other)
        with self.assertRaises(Conflict):
            self.mark()
        self.scans.choose_submission(partial["id"], "Use the student's partial makeup paper")
        work = self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q2")
        self.assertTrue(work["page_missing"])
        with self.assertRaises(Conflict):
            self.mark("q2")
        self.mark("q2", acknowledge_missing=True)

    def test_wrong_participant_source_and_document_version_are_rejected(self):
        with self.assertRaises(NotFound):
            self.grading.save_mark(self.event["id"], self.data(question_id="q1", score=6, criterion_ids=["method"]) | {"section_id": "wrong"})
        other = self.ready_batch(self.document_version("B"))
        _, reviewed = self.reviewed_source(changed="variant", batch=other)
        self.scans.choose_submission(reviewed["id"], "Version B submitted")
        with self.assertRaisesRegex(Conflict, "Configure a grading rubric"):
            self.grading.work(self.event["id"], self.section["id"], self.enrollment, "q1")

    def test_main_and_makeup_version_rubrics_grade_independently(self):
        self.complete()
        released_a = self.grading.release(self.event["id"], self.data(request_id="version-a"))
        with connect(self.path) as db:
            second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=?", (self.section["id"], self.enrollment)).fetchone()[0]
        version_b = self.document_version("Makeup B")
        batch_b = self.ready_batch(version_b, second)
        _, revision_b = self.reviewed_source(batch=batch_b, changed="makeup-b")
        questions_b = [{"id": "q1", "label": "Makeup question", "page": 2, "max_points": 8,
                        "region": {"x": 0, "y": .5, "width": 1, "height": .5}}]
        self.grading.configure(self.event["id"], {"version": 0, "assignment_version_id": version_b, "questions": questions_b})
        details_a = self.grading.details(self.event["id"], assignment_version_id=self.version_id)
        self.assertEqual(len(details_a["questions"]), 2)
        self.assertEqual(len(details_a["rubrics"]), 2)
        work_b = self.grading.work(self.event["id"], self.section["id"], second, "q1")
        self.assertEqual(work_b["question"]["page"], 2)
        self.assertEqual(work_b["region"]["y"], .5)
        self.assertEqual(work_b["question"]["max_points"], 8)
        payload = {"section_id": self.section["id"], "enrollment_id": second, "version": 0,
                   "assembly_revision_id": revision_b["id"], "question_id": "q1", "score": 7}
        marked = self.grading.save_mark(self.event["id"], payload)
        final_b = self.grading.finalize(self.event["id"], payload | {"version": marked["version"]})
        release_b = self.grading.release(self.event["id"], payload | {"version": final_b["version"], "request_id": "version-b"})
        self.assertEqual(release_b["total"], 7)
        self.assertEqual(self.grading.release_snapshot(released_a["id"])["total"], 10)
        self.assertEqual(len(list(csv.reader(io.StringIO(self.grading.export(self.event["id"]))))), 3)
        original_version = self.participant()["version"]
        questions_b[0]["max_points"] = 9
        self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": version_b, "questions": questions_b})
        self.assertEqual(self.participant()["version"], original_version)
        self.assertEqual(self.participant()["status"], "released")
        self.assertEqual(self.grading.release_snapshot(released_a["id"])["total"], 10)
        with self.assertRaises(Conflict):
            self.grading.release_snapshot(release_b["id"])
        self.assertEqual(len(list(csv.reader(io.StringIO(self.grading.export(self.event["id"]))))), 2)

    def test_course_wide_rubrics_are_referenced_without_propagating_term_edits(self):
        created = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Calculus", "term": "Spring", "year": 2027,
                                                       "sections": [501]}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        with connect(self.path) as db:
            section = db.execute("SELECT s.id,s.term_id FROM course_sections s JOIN course_terms t ON t.id=s.term_id WHERE t.year=2027").fetchone()
        other = self.exams.create({"term_id": section["term_id"], "assignment_id": self.assignment["id"], "section_ids": [section["id"]]})
        shared = self.grading.configure(other["id"], {"version": 0, "assignment_version_id": self.version_id, "questions": self.questions})
        original = self.grading.details(self.event["id"])["rubric"]
        self.assertEqual(shared["id"], original["id"])
        questions = copy.deepcopy(self.questions)
        questions[1]["max_points"] = 7
        changed = self.grading.configure(other["id"], {"version": 1, "assignment_version_id": self.version_id, "questions": questions})
        self.assertNotEqual(changed["id"], shared["id"])
        original_after = self.grading.details(self.event["id"])["rubric"]
        self.assertEqual(original_after["id"], original["id"])
        self.assertEqual(original_after["questions"][1]["max_points"], 5)

    def test_region_schema_limits_and_immutable_revisions(self):
        for change in ({"page": 3}, {"region": {"x": .9, "y": 0, "width": .2, "height": .5}}, {"max_points": 12}, {"id": "bad space"}):
            questions = copy.deepcopy(self.questions)
            questions[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id, "questions": questions})
        self.complete()
        self.grading.release(self.event["id"], self.data(request_id="retained"))
        for table in ("exam_rubric_versions", "exam_mark_revisions", "exam_grade_finalizations", "exam_grade_releases"):
            with self.subTest(table=table), connect(self.path) as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"DELETE FROM {table}")

    def test_rubric_edit_invalidates_an_ungraded_open_form(self):
        old_form = self.data(question_id="q1", score=6, criterion_ids=["method"])
        questions = copy.deepcopy(self.questions)
        questions[0]["label"] = "Explain the method"
        self.grading.configure(self.event["id"], {"version": 1, "assignment_version_id": self.version_id, "questions": questions})
        with self.assertRaises(Conflict):
            self.grading.save_mark(self.event["id"], old_form)
        self.assertEqual(self.participant()["version"], 1)

    def test_http_grading_and_released_feedback_card_are_explicit_and_scoped(self):
        prefix = f"/api/exams/{self.event['id']}/grading"
        self.assertEqual(self.client.get(prefix).status_code, 200)
        payload = self.data(question_id="q1", score=6, criterion_ids=["method"], feedback="Check the sign.")
        denied = self.client.post(prefix + "/marks", json=payload, headers={"X-Teaching-Request": "1", "Origin": "https://elsewhere.example"})
        self.assertEqual(denied.status_code, 403)
        saved = self.client.post(prefix + "/marks", json=payload, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.mark("q2")
        finalized = self.client.post(prefix + "/finalize", json=self.data(), headers=HEADERS)
        self.assertEqual(finalized.status_code, 200, finalized.json)
        released = self.client.post(prefix + "/release", json=self.data(request_id="api-feedback"), headers=HEADERS)
        self.assertEqual(released.status_code, 200, released.json)
        prepared = self.client.post(prefix + "/feedback-cards", json={"release_id": released.json["id"], "request_id": "feedback-card-one"}, headers=HEADERS)
        self.assertEqual(prepared.status_code, 201, prepared.json)
        self.assertFalse(prepared.json["published"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM response_card_mail").fetchone()[0], 0)
            recipient = db.execute("SELECT * FROM response_card_recipients WHERE issue_id=?", (prepared.json["id"],)).fetchone()
            self.assertEqual(recipient["enrollment_id"], self.enrollment)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM response_card_recipients WHERE issue_id=?", (prepared.json["id"],)).fetchone()[0], 1)
        self.grading.reopen(self.event["id"], self.data(reason="Recheck before publishing feedback"))
        with self.assertRaises(Conflict):
            self.app.extensions["exam_requests"].validate_publication(prepared.json["id"])

    def test_export_protects_spreadsheet_formula_names_and_excludes_drafts(self):
        self.complete()
        self.grading.release(self.event["id"], self.data(request_id="csv"))
        original = self.exams._participants
        def renamed(db, event):
            participants = original(db, event)
            for p in participants:
                p["name"] = "=HYPERLINK(unsafe)"
            return participants
        with patch.object(self.exams, "_participants", side_effect=renamed):
            rows = list(csv.reader(io.StringIO(self.grading.export(self.event["id"]))))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1][0].startswith("'="))

    def test_linked_chart_explicit_deletion_retains_exam_provenance_and_prints(self):
        room = self.client.post("/api/rooms", data={"name": "Grading test room", "file": (io.BytesIO(chart_bytes({"A1": "A1", "B1": "A2", "C1": "A3"})), "room.xlsx")}, headers=HEADERS)
        self.assertEqual(room.status_code, 201, room.json)
        chart = self.client.post("/api/seating", json={"assignment_id": self.assignment["id"], "section_ids": [self.section["id"]], "room_id": room.json["id"]}, headers=HEADERS)
        self.assertEqual(chart.status_code, 201, chart.json)
        chart_id = chart.json["id"]
        linked = self.exams.attach_chart(self.event["id"], {"version": self.exams.get(self.event["id"])["version"], "chart_id": chart_id})
        # Earlier local builds used a non-null chart FK. Startup upgrades those
        # links atomically without losing their original chart/sitting identity.
        with connect(self.path) as db:
            retained = dict(db.execute("SELECT * FROM exam_event_charts WHERE chart_id=?", (chart_id,)).fetchone())
            db.execute("DROP TABLE exam_event_charts")
            db.execute("""CREATE TABLE exam_event_charts(event_id TEXT NOT NULL REFERENCES exam_events(id),
                chart_id TEXT NOT NULL UNIQUE REFERENCES seating_charts(id), sitting_id TEXT REFERENCES exam_sittings(id),
                created_at TEXT NOT NULL, PRIMARY KEY(event_id,chart_id))""")
            db.execute("INSERT INTO exam_event_charts VALUES(?,?,?,?)", (retained["event_id"], chart_id, retained["sitting_id"], retained["created_at"]))
        upgraded = create_app(self.directory.name)
        self.assertEqual(upgraded.extensions["exams"].get(self.event["id"])["charts"][0]["id"], chart_id)
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET seating_chart_id=? WHERE id=?", (chart_id, self.batch["id"]))
            db.execute("UPDATE seating_charts SET confirmed_at=? WHERE id=?", (timestamp(), chart_id))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE exam_event_charts SET chart_id=NULL WHERE chart_id=?", (chart_id,))
        denied = self.client.delete(f"/api/seating/{chart_id}", json={"version": chart.json["version"]}, headers=HEADERS)
        self.assertEqual(denied.status_code, 409, denied.json)
        deleted = self.client.delete(f"/api/seating/{chart_id}", json={"version": chart.json["version"], "confirmation": "delete"}, headers=HEADERS)
        self.assertEqual(deleted.status_code, 204, deleted.json)
        after = self.exams.get(self.event["id"])
        self.assertEqual(after["charts"], [])
        self.assertEqual(after["deleted_chart_links"][0]["original_chart_id"], chart_id)
        self.assertEqual(after["version"], linked["version"] + 1)
        self.assertTrue(any(h["kind"] == "chart_deleted" and h["detail"]["chart_id"] == chart_id for h in after["history"]))
        with connect(self.path) as db:
            batch = db.execute("SELECT * FROM print_batches WHERE id=?", (self.batch["id"],)).fetchone()
            self.assertEqual(batch["status"], "ready")
            self.assertIsNone(batch["seating_chart_id"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM page_stamps WHERE printed_copy_id=?", (self.copy_id,)).fetchone()[0], 2)
        self.assertEqual(self.participant()["assembly_revision_id"], self.revision["id"])
        restarted = create_app(self.directory.name)
        self.assertEqual(restarted.extensions["exams"].get(self.event["id"])["deleted_chart_links"][0]["original_chart_id"], chart_id)


class ManagedGradingLifecycleTests(unittest.TestCase):
    managed = True
    setUp = ExamGradingTests.setUp
    document_version = ExamGradingTests.document_version
    ready_batch = ExamGradingTests.ready_batch
    reviewed_source = ExamGradingTests.reviewed_source
    participant = ExamGradingTests.participant
    data = ExamGradingTests.data
    mark = ExamGradingTests.mark
    complete = ExamGradingTests.complete

    def test_verified_archived_roster_and_catalog_preserve_grading(self):
        self.complete()
        release = self.grading.release(self.event["id"], self.data(request_id="managed-release"))
        changed = self.client.patch(f"/api/courses/{self.course['id']}", json={"version": self.course["version"], "is_current": False}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        # The verified term lifecycle handles the actual roster copy and purge.
        # Synthetic document fixtures are never offered as valid print backups.
        term_id = self.event["term_id"]
        with self.rosters.lock():
            self.storage.term_storage.reconcile_course(term_id)
            self.storage.reconcile_course(self.course["id"])
        term = self.storage.term_storage.row(term_id)
        self.assertEqual(term["storage_status"], "archived")
        self.assertFalse(self.storage.term_storage.hot_path(term_id).exists())
        archived = self.participant()
        self.assertEqual(archived["status"], "released")
        self.assertNotEqual(archived["name"], self.enrollment)
        self.assertEqual(self.grading.release_snapshot(release["id"])["total"], 10)
        with self.rosters.lock():
            snapshot = self.storage.backup_catalog()
        snapshot_name = snapshot["catalog_snapshot"] if isinstance(snapshot, dict) else snapshot
        with sqlite3.connect(self.storage.hdd / "workspace-backups" / snapshot_name / "faculty.sqlite3") as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_grade_releases WHERE id=?", (release["id"],)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_mark_revisions").fetchone()[0], 2)
        # A pending restore must finish before grading opens its write transaction.
        activated = self.client.patch(f"/api/courses/{self.course['id']}", json={"version": changed.json["version"], "is_current": True}, headers=HEADERS)
        self.assertEqual(activated.status_code, 200, activated.json)
        self.assertEqual(self.storage.term_storage.row(term_id)["storage_status"], "restore_pending")
        reopened = self.grading.reopen(self.event["id"], {"section_id": self.section["id"], "enrollment_id": self.enrollment,
                                                        "version": archived["version"], "reason": "Archived feedback correction"})
        self.assertEqual(reopened["status"], "in_progress")
        self.assertTrue(self.storage.term_storage.hot_path(term_id).exists())
        self.mark()


if __name__ == "__main__":
    unittest.main()
