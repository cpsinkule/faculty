"""Term ownership, optimistic writes, reviewed collection and source changes."""
import io
import json
import sqlite3
import tempfile
import unittest
import uuid

from werkzeug.exceptions import Conflict

from app import create_app
from course_storage import timestamp
from exam_workflow import ExamService
from rosters import connect
from teach_inbox.service import encoded, sha
from test_rosters import COLUMNS, csv_bytes
from test_rooms import chart_bytes
from test_dr_exams import booking, workbook as dr_workbook
from test_storage import temporary_storage


HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}


def student(name, uin):
    row = [""] * len(COLUMNS)
    row[1], row[2] = name, uin
    return row


class ExamWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = temporary_storage(self.directory.name) if getattr(self, "managed", False) else None
        self.app = create_app(storage=self.storage) if self.storage else create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.documents = self.app.extensions["documents"]
        self.scans = self.app.extensions["document_scans"]
        self.inbox = self.app.extensions["inbox"]
        self.rosters = self.app.extensions["rosters"]
        self.path = self.rosters.path
        self.exams = ExamService(self.rosters, self.documents, self.scans, self.app.extensions["seating"], self.app.extensions["response_cards"])
        response = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
            "sections": [501, 502], "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        if self.storage:
            self.storage.reconcile_all()
        self.group = self.client.get("/api/state").json["course_groups"][0]
        self.import_students([student("Example, Alice", "001234567"), student("Example, Bob", "123456789")])
        catalog = self.exams.catalog()
        self.term = catalog["terms"][0]
        self.sections = catalog["sections"]
        self.section = next(s for s in self.sections if s["number"] == 501)
        self.assignment = self.documents.create_assignment(self.term["definition_id"], "exam-1", "Exam 1")
        self.event = self.exams.create({"term_id": self.term["id"], "assignment_id": self.assignment["id"], "section_ids": [self.section["id"]]})
        self.alice = next(p for p in self.event["participants"] if p["name"] == "Example, Alice")

    def import_students(self, rows):
        response = self.client.post(f"/api/course-groups/{self.group['id']}/sections/501/roster",
            data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)

    def reload(self):
        self.event = self.exams.get(self.event["id"])
        return self.event

    def batch(self, students=None, page_count=2, section=None, assignment=None):
        """Build catalog provenance, without invoking native PDF rendering in unit tests."""
        version_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (version_id, (assignment or self.assignment)["id"], version_id,
                "fixture/source.json", "a" * 64, "fixture/canonical.pdf", "b" * 64, page_count, "{}", "faculty-page-v1", timestamp()))
        students = students if students is not None else [self.alice["enrollment_id"], None]
        batch = self.documents.create_batch(version_id, (section or self.section)["id"], len(students), str(uuid.uuid4()), students=students)
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET status='ready',output_path='fixture/print.pdf',output_sha256=?,manifest_path='fixture/manifest.json',manifest_sha256=? WHERE id=?", ("c" * 64, "d" * 64, batch["id"]))
            db.execute("UPDATE page_stamps SET state='issued' WHERE printed_copy_id IN (SELECT id FROM printed_copies WHERE print_batch_id=?)", (batch["id"],))
        batch = self.documents.get_batch(batch["id"])
        self.event = self.exams.attach_batch(self.event["id"], {"version": self.event["version"], "batch_id": batch["id"]})
        return batch

    def arrival(self, copy, numbers, suffix="", review=True):
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": b"%PDF-fixture"}],
            source="email", sender="scanner", original=b"retained email", idempotency_key=str(uuid.uuid4()), authenticated=True,
            instructor_review_required=True)
        source = next(a for a in job["artifacts"] if a["role"] == "input")
        with connect(self.path) as db:
            stamps = {p["page_number"]: dict(p) for p in db.execute("SELECT * FROM page_stamps WHERE printed_copy_id=?", (copy["id"],))}
        for index, number in enumerate(numbers, 1):
            page = {"index": index, "candidates": [{"payload": stamps[number]["token"]}], "blank": False,
                    "pdf": f"%PDF page {number} {suffix}".encode(), "png": b"fixture raster", "decoder": {}}
            self.scans._retain_page(job, source, page)
        if review:
            with connect(self.path) as db:
                # Isolate collection semantics from Inbox review UI; preserve explicit gate in other tests.
                db.execute("UPDATE inbox_messages SET instructor_review_required=0 WHERE id=?", (job["message_id"],))
        return self.scans.review_item(job["message_id"])

    def select(self, copy, observations, partial=False):
        return self.scans.select_assembly(copy["id"], [p["id"] for item in observations for p in item["pages"]], partial, "Instructor reviewed exact pages")

    def alice_collection(self):
        event = self.reload()
        # Each collection record is sent once, in reconciliation.students (never again under participants).
        self.assertFalse(any("collection" in p for p in event["participants"]))
        return next(s for s in event["reconciliation"]["students"] if s["enrollment_id"] == self.alice["enrollment_id"])

    def chart(self):
        room = self.app.extensions["rooms"].create("HECC 209", "", chart_bytes({"A1": "A1", "B1": "A2", "C1": "A3"}), "room.xlsx")
        chart = self.app.extensions["seating"].create({"assignment_id": self.assignment["id"], "section_ids": [self.section["id"]], "room_id": room["id"]})
        self.event = self.exams.attach_chart(self.event["id"], {"version": self.event["version"], "chart_id": chart["id"]})
        return chart

    def import_dr(self, **updates):
        content = dr_workbook([booking(Section="501", **updates)])
        preview = self.client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "dr.xlsx"), "term": "null"}, headers=HEADERS)
        self.assertEqual(preview.status_code, 200, preview.json)
        response = self.client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "dr.xlsx"), "term": "null",
            "preview_hash": preview.json["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(response.status_code, 202, response.json)
        self.inbox.process_one()
        self.assertEqual(self.inbox.get(response.json["id"])["status"], "completed")

    def test_printing_extends_the_exam_record_to_its_sections(self):
        """A registration for an unlabeled scan is not printing."""
        other = next(s for s in self.sections if s["number"] == 502)
        response = self.client.post(f"/api/course-groups/{self.group['id']}/sections/502/roster",
            data={"file": (io.BytesIO(csv_bytes([student("Example, Carol", "234567890")])), "roster.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)

        def version(assignment=self.assignment):
            version_id = str(uuid.uuid4())
            with connect(self.path) as db:
                db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (version_id, assignment["id"], version_id,
                    "fixture/source.json", "a" * 64, "fixture/canonical.pdf", "b" * 64, 2, "{}", "faculty-page-v1", timestamp()))
            return version_id
        numbers = lambda: [s["number"] for s in self.reload()["sections"]]
        def history():
            with connect(self.path) as db:
                return [json.loads(r[0]) for r in db.execute(
                    "SELECT detail_json FROM exam_history WHERE event_id=? AND kind='print_sections_added' ORDER BY id", (self.event["id"],))]

        self.documents.create_batch(version(), other["id"], 1, "unlabeled:" + str(uuid.uuid4()))
        self.assertEqual(numbers(), [501])
        before, paper, request = self.event["version"], version(), str(uuid.uuid4())
        job = self.documents.create_batch(paper, other["id"], 1, request)
        self.assertEqual(numbers(), [501, 502])
        self.assertGreater(self.event["version"], before)
        self.assertIn("Example, Carol", [p["name"] for p in self.event["participants"]])
        self.assertEqual(history(), [{"print_batch_id": job["id"], "section_ids": [other["id"]], "participants_added": 1}])
        # The same request again, or a term job for sections the record has, adds nothing.
        self.assertEqual(self.documents.create_batch(paper, other["id"], 1, request)["id"], job["id"])
        self.documents.create_batch(version(), None, 1, str(uuid.uuid4()), term_id=self.term["id"], section_ids=[self.section["id"], other["id"]])
        self.assertEqual(len(history()), 1)
        # A term without an exam record for the assignment gets none from printing.
        second = self.documents.create_assignment(self.term["definition_id"], "exam-2", "Exam 2")
        self.documents.create_batch(version(second), other["id"], 1, str(uuid.uuid4()))
        with connect(self.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM exam_events WHERE assignment_id=?", (second["id"],)).fetchone())

    def test_scope_is_permanent_and_cross_term_sections_are_rejected(self):
        response = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Spring", "year": 2027,
            "sections": [501]}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        foreign = next(s for s in self.exams.catalog()["sections"] if s["term_id"] != self.term["id"])
        assignment = self.documents.create_assignment(self.term["definition_id"], "exam-2", "Exam 2")
        with self.assertRaises(ValueError):
            self.exams.create({"term_id": self.term["id"], "assignment_id": assignment["id"], "section_ids": [foreign["id"]]})
        with self.assertRaises(Conflict):
            self.exams.create({"term_id": self.term["id"], "assignment_id": self.assignment["id"], "section_ids": [self.section["id"]]})
        with connect(self.path) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("UPDATE exam_events SET term_id=? WHERE id=?", (foreign["term_id"], self.event["id"]))
        self.assertEqual(self.exams.get(self.event["id"])["term_id"], self.term["id"])

    def test_sittings_and_participants_use_versions_and_preserve_expectations(self):
        self.event = self.exams.save_sitting(self.event["id"], {"version": self.event["version"], "label": "Makeup", "kind": "makeup", "date": "2026-10-02", "time": "2 PM"})
        sitting = self.event["sittings"][0]
        self.event = self.exams.update_participant(self.event["id"], self.section["id"], self.alice["enrollment_id"],
            {"version": self.alice["version"], "sitting_id": sitting["id"], "expectation": "excused", "reason": "Instructor approved makeup"})
        with self.assertRaises(Conflict):
            self.exams.update_participant(self.event["id"], self.section["id"], self.alice["enrollment_id"], {"version": 1, "expectation": "expected"})
        with self.assertRaises(ValueError):
            self.exams.save_sitting(self.event["id"], {"version": sitting["version"], "date": "2026-02-30"}, sitting["id"])
        self.import_students([student("Example, Carol", "222222222")])
        changed = self.reload()
        self.assertTrue(any(c["kind"] == "roster_addition" for c in changed["impacts"]["changes"]))
        self.event = self.exams.sync_roster(self.event["id"], {"version": self.event["version"]})
        self.assertEqual(len(self.event["participants"]), 3)
        alice = next(p for p in self.event["participants"] if p["enrollment_id"] == self.alice["enrollment_id"])
        self.assertEqual((alice["sitting_id"], alice["expectation"]), (sitting["id"], "excused"))
        self.assertEqual(self.event["reconciliation"]["counts"]["no_scan"], 2)

    def test_the_listing_names_the_main_sittings_date_as_the_record_orders_it(self):
        """A seating chart takes the main sitting's date from the list (main_date) instead of the whole exam record: the same date
        the page picked from get() (the first main sitting with a date, in its order), or '' when there is none."""
        def listed():
            return next(item for item in self.client.get("/api/exams", query_string={"summary": "0"}).json["exams"] if item["id"] == self.event["id"])
        def picked():
            return next((s["date"] for s in self.exams.get(self.event["id"])["sittings"] if s["kind"] == "main" and s["date"]), "")
        steps = [{"label": "Makeup", "kind": "makeup", "date": "2026-10-01"}, {"label": "Main, date to come", "kind": "main"},
                 {"label": "Evening", "kind": "main", "date": "2026-10-05", "time": "7 PM"}, {"label": "DR", "kind": "dr", "date": "2026-10-02"},
                 {"label": "Morning", "kind": "main", "date": "2026-10-05", "time": "9 AM"}, {"label": "Early", "kind": "main", "date": "2026-10-03"}]
        self.assertEqual(listed()["main_date"], "")
        for step in steps:
            self.event = self.exams.save_sitting(self.event["id"], {"version": self.event["version"], **step})
            self.assertEqual(listed()["main_date"], picked())
        self.assertEqual(listed()["main_date"], "2026-10-03")
        early = next(s for s in self.event["sittings"] if s["label"] == "Early")
        self.event = self.exams.save_sitting(self.event["id"], {"version": early["version"], "date": ""}, early["id"])
        self.assertEqual((listed()["main_date"], picked()), ("2026-10-05", "2026-10-05"))
        self.assertEqual(self.client.get("/api/exams").json["exams"][0]["main_date"], "2026-10-05")

    def test_a_dropped_student_is_not_added_to_the_exam_and_keeps_being_a_participant_once_added(self):
        """requirement, 2026-09-27: a dropped student leaves new exams; what is recorded (being a participant) stays."""
        self.import_students([student("Example, Carol", "222222222")])
        group = self.client.get("/api/state").json["course_groups"][0]
        roster = f"/api/course-groups/{group['id']}/sections/501/roster"
        carol = next(s for s in self.client.get(roster).json["students"] if s["uin"] == "222222222")
        dropped = self.client.post(roster + "/drop", json={"enrollment_ids": [carol["id"]], "request_id": "drop-carol"}, headers=HEADERS)
        self.assertEqual(dropped.status_code, 200, dropped.json)
        self.assertFalse(any(c["kind"] == "roster_addition" for c in self.reload()["impacts"]["changes"]))
        self.event = self.exams.sync_roster(self.event["id"], {"version": self.event["version"]})
        self.assertEqual(len(self.event["participants"]), 2)
        # Alice, already a participant, stays one when dropped (and is marked).
        self.client.post(roster + "/drop", json={"enrollment_ids": [self.alice["enrollment_id"]], "request_id": "drop-alice"}, headers=HEADERS)
        with self.app.extensions["rosters"].lock(), connect(self.path) as db:
            listed = {p["enrollment_id"]: p for p in self.exams._participants(db, self.exams._event(db, self.event["id"]))}
        self.assertTrue(listed[self.alice["enrollment_id"]]["dropped"])

    def test_no_scan_is_not_absence_and_unused_extras_are_not_missing_students(self):
        batch = self.batch()
        self.assertEqual(self.event["reconciliation"]["counts"]["no_scan"], 2)
        self.assertEqual(self.event["reconciliation"]["unused_extra_count"], 1)
        with self.assertRaises(ValueError):
            self.exams.update_participant(self.event["id"], self.section["id"], self.alice["enrollment_id"], {"version": 1, "expectation": "absent", "reason": ""})
        self.event = self.exams.update_participant(self.event["id"], self.section["id"], self.alice["enrollment_id"], {"version": 1, "expectation": "absent", "reason": "Confirmed absent in class"})
        self.assertEqual(self.event["reconciliation"]["counts"]["absent"], 1)
        self.arrival(batch["copies"][1], [1])
        self.assertEqual(self.reload()["reconciliation"]["unassigned_received_count"], 1)
        self.assertEqual(self.event["reconciliation"]["counts"]["no_scan"], 1)

    def test_collection_combines_arrivals_only_after_exact_assembly_and_inbox_review(self):
        batch = self.batch()
        copy = batch["copies"][0]
        first = self.arrival(copy, [1])
        self.assertEqual(self.alice_collection()["status"], "missing_pages")
        # Paper labeled on the first page only expects nothing on page 2; the instructor can switch that back.
        self.documents.set_label_mode(batch["id"], "first_page")
        self.assertEqual((self.alice_collection()["status"], self.alice_collection()["missing_pages"]), ("needs_review", []))
        self.documents.set_label_mode(batch["id"], "every_page")
        self.assertEqual((self.alice_collection()["status"], self.alice_collection()["missing_pages"]), ("missing_pages", [2]))
        second = self.arrival(copy, [2], review=False)
        self.assertEqual(self.alice_collection()["status"], "needs_review")
        revision = self.select(copy, [first, second])
        self.assertIsNone(self.alice_collection()["assembly_revision_id"])
        with connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET instructor_review_required=0 WHERE id=?", (second["message_id"],))
        collection = self.alice_collection()
        self.assertEqual((collection["status"], collection["assembly_revision_id"]), ("complete", revision["id"]))
        self.assertEqual(len(collection["copies"][0]["message_ids"]), 2)
        changed = self.arrival(copy, [1], suffix="different handwriting")
        self.assertEqual(self.alice_collection()["status"], "needs_review")
        self.assertIsNone(self.alice_collection()["assembly_revision_id"])
        refreshed = self.select(copy, [changed, second])
        self.assertEqual(self.alice_collection()["assembly_revision_id"], refreshed["id"])

    def test_competing_reviewed_copies_require_explicit_choice(self):
        first = self.batch(students=[self.alice["enrollment_id"]])
        original = self.select(first["copies"][0], [self.arrival(first["copies"][0], [1, 2])])
        second = self.batch(students=[self.alice["enrollment_id"]])
        self.select(second["copies"][0], [self.arrival(second["copies"][0], [1, 2])])
        self.assertEqual(self.alice_collection()["status"], "needs_review")
        self.scans.choose_submission(original["id"], "Instructor selected the first copy")
        self.assertEqual(self.alice_collection()["assembly_revision_id"], original["id"])
        self.arrival(second["copies"][0], [1], suffix="Later additional submission")
        self.assertIsNone(self.alice_collection()["assembly_revision_id"])

    def test_the_whole_exam_is_read_at_once_exactly_as_each_copy_and_student_alone(self):
        """PERFORMANCE (2026-10-02): `_collection` reads every copy's scans, assemblies and Inbox jobs, and every student's newest
        submission choice, in a fixed number of statements for the whole exam. Each copy and each student gets exactly what their
        own reads give (`_participant_paper` reads one student's paper alone)."""
        bob = next(p for p in self.event["participants"] if p["name"] == "Example, Bob")
        first = self.batch(students=[self.alice["enrollment_id"], bob["enrollment_id"], None])
        second = self.batch(students=[self.alice["enrollment_id"]])
        alices, bobs, extra = first["copies"]
        one, two = self.arrival(alices, [1]), self.arrival(alices, [2], suffix="second scan")
        self.select(alices, [one, two])
        latest = self.select(alices, [self.arrival(alices, [1], suffix="written again"), two])  # a second revision of one copy
        chosen = self.select(second["copies"][0], [self.arrival(second["copies"][0], [1, 2])])
        self.arrival(bobs, [1, 2], review=False)  # still waiting for its Inbox review
        self.arrival(extra, [1])  # an extra copy someone wrote on
        gone = self.arrival(bobs, [2], suffix="deleted in the Inbox")
        with connect(self.path) as db:
            db.execute("INSERT INTO inbox_deletions(message_id,actor,reason,created_at) VALUES(?,?,?,?)", (gone["message_id"], "workspace", "", timestamp()))
            # Two choices made at the same moment: the later row counts, for the whole exam as for one student.
            now = timestamp()
            for revision in (latest["id"], chosen["id"]):
                db.execute("INSERT INTO submission_choices VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), self.alice["enrollment_id"], self.assignment["id"],
                                                                                   revision, "workspace", "fixture", now))
        with connect(self.path) as db:
            event = self.exams._event(db, self.event["id"])
            copies = self.exams._copies(db, event)
            whole = self.exams._exam_copy_reads(db, event, copies)
            self.assertEqual(len(copies), 4)
            for copy in copies:
                observations, revisions, jobs = self.exams._copy_reads(db, copy)
                self.assertEqual([tuple(row)[1:] for row in whole[copy["id"]][0]], [tuple(row) for row in observations])
                self.assertEqual([tuple(row)[1:] for row in whole[copy["id"]][1]], [tuple(row) for row in revisions])
                self.assertEqual(whole[copy["id"]][2], jobs)
            self.assertEqual(sum(len(whole[c["id"]][2]) for c in copies), 6)  # the deleted scan's job is not one of them
            self.assertEqual(sorted(len(whole[c["id"]][1]) for c in copies), [0, 0, 1, 2])
            participants = self.exams._participants(db, event)
            queries = []
            db.set_trace_callback(queries.append)
            collection = self.exams._collection(db, event, participants)
            db.set_trace_callback(None)
            self.assertEqual(len(queries), 5)  # copies, observations, revisions, their jobs, choices: however many copies there are
            for student in collection["students"]:
                paper = self.exams._participant_paper(db, event, student["section_id"], student["enrollment_id"])
                self.assertEqual({key: value for key, value in student.items() if key not in ("section_id", "enrollment_id", "name")}, paper["collection"])
        alice = next(s for s in collection["students"] if s["enrollment_id"] == self.alice["enrollment_id"])
        self.assertEqual((alice["status"], alice["assembly_revision_id"]), ("complete", chosen["id"]))
        self.assertEqual((collection["unassigned_received_count"], collection["unused_extra_count"]), (1, 0))

    def test_partial_reviewed_work_retains_partial_status(self):
        batch = self.batch(students=[self.alice["enrollment_id"]])
        revision = self.select(batch["copies"][0], [self.arrival(batch["copies"][0], [1])], partial=True)
        self.assertEqual((self.alice_collection()["status"], self.alice_collection()["assembly_revision_id"]), ("partial", revision["id"]))

    def test_superseded_paper_does_not_satisfy_collection(self):
        batch = self.batch(students=[self.alice["enrollment_id"]])
        self.select(batch["copies"][0], [self.arrival(batch["copies"][0], [1, 2])])
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET superseded_at=? WHERE id=?", (timestamp(), batch["id"]))
        self.assertEqual(self.alice_collection()["status"], "no_scan")
        self.assertIsNone(self.alice_collection()["assembly_revision_id"])

    def test_checkpoint_revalidates_fingerprint_and_retains_change_history(self):
        self.event = self.exams.checkpoint(self.event["id"], {"version": self.event["version"], "fingerprint": self.event["impacts"]["fingerprint"]})
        self.assertEqual(self.event["impacts"]["changes"], [])
        old = self.event
        self.event = self.exams.save_sitting(self.event["id"], {"version": self.event["version"], "label": "Main", "kind": "main", "date": "2026-10-01"})
        self.assertTrue(any(c["kind"] == "sitting" for c in self.event["impacts"]["changes"]))
        with self.assertRaises(Conflict):
            self.exams.checkpoint(self.event["id"], {"version": self.event["version"], "fingerprint": old["impacts"]["fingerprint"]})
        self.event = self.exams.checkpoint(self.event["id"], {"version": self.event["version"], "fingerprint": self.event["impacts"]["fingerprint"]})
        self.assertEqual(self.event["impacts"]["changes"], [])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_preparation_checks WHERE event_id=?", (self.event["id"],)).fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM exam_preparation_checks")

    def test_sitting_from_another_event_cannot_be_assigned(self):
        assignment = self.documents.create_assignment(self.term["definition_id"], "exam-2", "Exam 2")
        other = self.exams.create({"term_id": self.term["id"], "assignment_id": assignment["id"], "section_ids": [self.section["id"]]})
        other = self.exams.save_sitting(other["id"], {"version": other["version"], "label": "Other exam"})
        with self.assertRaises(ValueError):
            self.exams.update_participant(self.event["id"], self.section["id"], self.alice["enrollment_id"], {"version": 1, "sitting_id": other["sittings"][0]["id"]})

    def test_private_identity_and_tokens_never_enter_exam_payload(self):
        self.batch()
        payload = json.dumps(self.reload())
        self.assertNotIn("001234567", payload)
        self.assertNotIn("123456789\"", payload)
        self.assertNotIn("T1-", payload)
        self.assertNotIn('"token"', payload)

    def test_change_preview_names_affected_seats_and_print_artifacts(self):
        chart = self.chart()
        batch = self.batch()
        self.event = self.exams.checkpoint(self.event["id"], {"version": self.event["version"], "fingerprint": self.event["impacts"]["fingerprint"]})
        with connect(self.path) as db:
            seat = db.execute("SELECT seat_id FROM seating_assignments WHERE chart_id=? AND enrollment_id=?", (chart["id"], self.alice["enrollment_id"])).fetchone()[0]
            db.execute("UPDATE room_seats SET blocked=1 WHERE id=?", (seat,))
            db.execute("UPDATE seating_charts SET exam_date='2026-10-03',exam_time='3 PM' WHERE id=?", (chart["id"],))
        changes = self.reload()["impacts"]["changes"]
        self.assertTrue(any(c["kind"] == "chart" and self.alice["enrollment_id"] in c["enrollment_ids"] for c in changes))
        issue = next(c for c in changes if c["kind"] == "unusable_seat")
        self.assertEqual(issue["enrollment_id"], self.alice["enrollment_id"])
        self.assertIn(batch["id"], issue["batch_ids"])

    def test_changed_dr_schedule_remains_a_concrete_impact(self):
        self.import_dr()
        self.reload()
        self.event = self.exams.checkpoint(self.event["id"], {"version": self.event["version"], "fingerprint": self.event["impacts"]["fingerprint"]})
        self.import_dr(**{"Start Time": "14:30:00"})
        changes = self.reload()["impacts"]["changes"]
        change = next(c for c in changes if c["kind"] == "dr")
        self.assertEqual(change["enrollment_id"], self.alice["enrollment_id"])
        self.assertNotEqual(change["before"]["revision_id"], change["after"]["revision_id"])
        self.assertNotEqual(change["before"]["schedule"]["start_time"], change["after"]["schedule"]["start_time"])

    def test_issued_notices_detect_schedule_changes_without_a_checkpoint(self):
        chart = self.chart()
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_date='2026-10-02',exam_time='2 PM' WHERE id=?", (chart["id"],))
        cards = self.app.extensions["response_cards"]
        template = self.app.extensions["seating"]._managed_template(self.term["definition_id"], "seat")
        version = cards.freeze(template["id"], template["version"])
        issued = cards.issue({"version_id": version["id"], "term_id": self.term["id"], "section_ids": [self.section["id"]],
            "assignment_id": self.assignment["id"], "seating_chart_id": chart["id"], "request_id": str(uuid.uuid4())})
        self.assertFalse(any(c["kind"] == "stale_notice" for c in self.reload()["impacts"]["changes"]))
        fingerprint = self.event["impacts"]["fingerprint"]
        with connect(self.path) as db:
            db.execute("UPDATE response_card_recipients SET status='submitted' WHERE issue_id=?", (issued["id"],))
        self.assertEqual(self.reload()["impacts"]["fingerprint"], fingerprint)
        with connect(self.path) as db:
            db.execute("UPDATE seating_charts SET exam_time='3 PM' WHERE id=?", (chart["id"],))
        changes = [c for c in self.reload()["impacts"]["changes"] if c["kind"] == "stale_notice"]
        self.assertEqual(len(changes), 2)
        self.assertTrue(all("description" in c["fields"] for c in changes))
        self.assertEqual({c["card_id"] for c in changes}, {c["id"] for c in issued["recipients"]})

    def test_issued_dr_notice_detects_revised_schedule_without_a_checkpoint(self):
        self.import_dr()
        cards = self.app.extensions["response_cards"]
        template = self.app.extensions["seating"]._managed_template(self.term["definition_id"], "dr")
        version = cards.freeze(template["id"], template["version"])
        cards.issue({"version_id": version["id"], "term_id": self.term["id"], "section_ids": [self.section["id"]],
            "enrollment_ids": [self.alice["enrollment_id"]], "assignment_id": self.assignment["id"], "request_id": str(uuid.uuid4())})
        self.assertFalse(any(c["kind"] == "stale_notice" for c in self.reload()["impacts"]["changes"]))
        self.import_dr(**{"Start Time": "14:30:00"})
        changes = [c for c in self.reload()["impacts"]["changes"] if c["kind"] == "stale_notice"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["fields"], ["confirm_dr_time"])


class ManagedExamLifecycleTests(unittest.TestCase):
    managed = True
    setUp = ExamWorkflowTests.setUp
    import_students = ExamWorkflowTests.import_students
    reload = ExamWorkflowTests.reload
    test_checkpoint_revalidates_fingerprint_and_retains_change_history = ExamWorkflowTests.test_checkpoint_revalidates_fingerprint_and_retains_change_history


if __name__ == "__main__":
    unittest.main()
