"""DR bookings link to exam assignments: name matching, auto-creation, startup sync, relinking."""
import io
import json
import tempfile
import unittest
import uuid

from app import create_app
from exam_links import ExamLinkService, canonical, exam_title, slug
from rosters import connect
from test_dr_exams import booking, workbook as dr_workbook
from test_rosters import csv_bytes
from test_seating import HEADERS, SECTION_507


class CanonicalNameTests(unittest.TestCase):
    def test_test_and_exam_are_interchangeable(self):
        same = [("Test 1", "Exam 1"), ("Test #01", "exam-1"), ("Final Exam", "Final"), ("Final Test", "FINAL"),
                ("Test", "Exam"), ("Midterm Exam 2", "Midterm 2"), ("test_1", "Exam No. 1"), ("Exam 2 (Retake)", "Test 2 retake")]
        for left, right in same:
            with self.subTest(left=left, right=right):
                self.assertEqual(canonical(left), canonical(right))
        different = [("Test 1", "Test 2"), ("Midterm 1", "Test 1"), ("Quiz 1", "Test 1"), ("Final", "Test 1")]
        for left, right in different:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(canonical(left), canonical(right))
        self.assertEqual(canonical(""), "test")
        self.assertEqual((slug("Final Exam"), slug("Exam #1"), slug("")), ("final-exam", "exam-1", "exam"))

    def test_a_superscript_or_circled_digit_is_text_not_a_number(self):
        self.assertEqual((canonical("Exam \u00b2"), canonical("Exam 1\u00b2"), canonical("Quiz \u2460")), ("\u00b2", "1\u00b2", "quiz \u2460"))
        self.assertEqual((canonical("Test \u0661"), canonical("Exam #01")), ("1", "1"))


class ExamLinkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.inbox = self.app.extensions["inbox"]
        self.link_service = self.app.extensions["exam_links"]
        self.rosters = self.app.extensions["rosters"]
        self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [507]}, headers=HEADERS)
        state = self.client.get("/api/state").json
        self.group, self.definition_id = state["course_groups"][0], state["course_definitions"][0]["id"]
        self.client.post(f"/api/course-groups/{self.group['id']}/sections/507/roster", data={"file": (io.BytesIO(csv_bytes(SECTION_507)), "roster.csv")}, headers=HEADERS)

    def import_dr(self, rows):
        content = dr_workbook(rows)
        preview = self.client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None)}, headers=HEADERS).json
        submitted = self.client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None),
                                     "preview_hash": preview["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(submitted.status_code, 202, submitted.json)
        self.assertTrue(self.inbox.process_one())
        job = self.inbox.get(submitted.json["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))
        return job

    def assignments(self):
        return {item["title"]: item for item in self.client.get("/api/documents/catalog").json["assignments"]}

    def link_rows(self):
        with connect(self.rosters.path) as db:
            return [dict(row) for row in db.execute("SELECT l.*, b.exam_key FROM dr_exam_assignment_links l JOIN dr_exam_bookings b ON b.id = l.booking_id ORDER BY b.exam_key")]

    def test_imports_match_existing_assignments_or_create_them(self):
        existing = self.client.post("/api/documents/assignments", json={"definition_id": self.definition_id, "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        job = self.import_dr([booking(**{"Exam Type": "Exam 1"}), booking(UIN="123456789", **{"Last Name": "Example", "First Name": "Bob", "Exam Type": "Final Exam"}),
                              booking(UIN="000000003", **{"Last Name": "Student", "First Name": "3", "Exam Type": "Exam 1"})])
        self.assertEqual(job["result"]["exam_links"]["matched"], 2)
        self.assertEqual(job["result"]["exam_links"]["created"], 1)
        self.assertEqual(job["result"]["exam_links"]["created_assignments"][0]["title"], "Final Exam")
        assignments = self.assignments()
        self.assertEqual(sorted(assignments), ["Final Exam", "Test 1"])
        self.assertEqual(assignments["Final Exam"]["assignment_key"], "final-exam")
        links = self.link_rows()
        self.assertEqual([(item["exam_key"], item["method"], item["assignment_id"] == existing["id"]) for item in links],
                         [("exam 1", "matched", True), ("exam 1", "matched", True), ("final exam", "created", False)])
        with connect(self.rosters.path) as db:
            event = db.execute("SELECT actor, detail_json FROM document_events WHERE kind = 'assignment_created' AND assignment_id = ?", (assignments["Final Exam"]["id"],)).fetchone()
        self.assertEqual((event["actor"], json.loads(event["detail_json"])["source"]), ("dr-exam-import", "dr_exam_booking"))
        # The roster view shows which assignment each booking is linked to.
        roster = self.client.get(f"/api/course-groups/{self.group['id']}/sections/507/roster").json
        alice = next(student for student in roster["students"] if student["uin"] == "001234567")
        self.assertEqual(alice["dr_exams"][0]["assignment"], {"id": existing["id"], "title": "Test 1", "method": "matched"})
        # A later "Final" booking matches the created "Final Exam" assignment; a schedule change keeps the link.
        self.import_dr([booking(UIN="000000004", **{"Last Name": "Student", "First Name": "4", "Exam Type": "Final"}),
                        booking(**{"Exam Type": "Exam 1", "Start Time": "08:00 AM", "End Time": "09:53 AM"})])
        self.assertEqual(sorted(self.assignments()), ["Final Exam", "Test 1"])
        self.assertEqual([item["method"] for item in self.link_rows() if item["exam_key"] == "final"], ["matched"])
        self.assertEqual(len(self.link_rows()), 4)

    def test_a_created_assignment_says_exam_where_the_booking_says_test(self):
        """requirement, 2026-09-27: DR imports turn "Test" into "Exam" in the assignments they create; matching stays as it was."""
        self.assertEqual([exam_title(name) for name in ("Test 4", "FINAL TEST", "final test", "Tests 1-2", "Contest 1", "Exam 2")],
                         ["Exam 4", "FINAL EXAM", "final exam", "Exams 1-2", "Contest 1", "Exam 2"])
        job = self.import_dr([booking(**{"Exam Type": "Test 4"})])
        created = job["result"]["exam_links"]["created_assignments"][0]
        self.assertEqual(created["title"], "Exam 4")
        self.assertEqual(self.assignments()["Exam 4"]["assignment_key"], "exam-4")
        with connect(self.rosters.path) as db:
            event = db.execute("SELECT detail_json FROM document_events WHERE kind = 'assignment_created' AND assignment_id = ?", (created["id"],)).fetchone()
        self.assertEqual(json.loads(event["detail_json"])["exam_type"], "Test 4")  # what the DR center wrote is kept
        # A later booking written "Test 4" or "Exam 4" links to it; nothing new is created.
        self.import_dr([booking(UIN="123456789", **{"Last Name": "Example", "First Name": "Bob", "Exam Type": "Exam 4"})])
        self.assertEqual(sorted(self.assignments()), ["Exam 4"])
        self.assertEqual({item["method"] for item in self.link_rows()}, {"created", "matched"})

    def test_a_renamed_assignment_keeps_its_identity_links_and_history(self):
        """renamed "Exam 1". Same assignment, links and key; the old title stays in its history."""
        existing = self.client.post("/api/documents/assignments", json={"definition_id": self.definition_id, "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        self.import_dr([booking(**{"Exam Type": "Test 1"})])
        route = f"/api/documents/assignments/{existing['id']}/rename"
        with connect(self.rosters.path) as db, self.assertRaisesRegex(Exception, "retained identities"):
            db.execute("UPDATE assignments SET title='Exam 1' WHERE id=?", (existing["id"],))  # never without a recorded name
        self.assertEqual(self.client.post(route, json={"title": "Exam 1", "request_id": "r1"}).status_code, 403)
        self.assertEqual(self.client.post(route, json={"title": "", "request_id": "r1"}, headers=HEADERS).status_code, 400)
        renamed = self.client.post(route, json={"title": "Exam 1", "request_id": "r1"}, headers=HEADERS)
        self.assertEqual(renamed.status_code, 200, renamed.json)
        self.assertEqual((renamed.json["title"], renamed.json["assignment_key"]), ("Exam 1", "test-1"))
        self.assertEqual([(n["previous"], n["title"]) for n in renamed.json["names"]], [("Test 1", "Exam 1")])
        self.assertEqual(self.client.post(route, json={"title": "Exam 1", "request_id": "r1"}, headers=HEADERS).status_code, 200)  # a repeat
        self.assertEqual(self.client.post(route, json={"title": "Exam One", "request_id": "r1"}, headers=HEADERS).status_code, 409)
        self.assertEqual(sorted(self.assignments()), ["Exam 1"])
        # Bookings stay linked, and a new "Test 1" booking still matches it.
        self.import_dr([booking(UIN="123456789", **{"Last Name": "Example", "First Name": "Bob", "Exam Type": "Test 1"})])
        self.assertEqual({item["assignment_id"] for item in self.link_rows()}, {existing["id"]})
        with connect(self.rosters.path) as db, self.assertRaisesRegex(Exception, "append-only"):
            db.execute("UPDATE assignment_names SET title='x'")

    def test_startup_links_bookings_imported_before_the_feature(self):
        self.import_dr([booking(**{"Exam Type": "Test 2"}), booking(UIN="123456789", **{"Last Name": "Example", "First Name": "Bob", "Exam Type": "Quiz 4"})])
        self.assertEqual(sorted(self.assignments()), ["Exam 2", "Quiz 4"])  # a booking's "Test" is created as "Exam"
        # Bookings that predate linking have no link rows; the next startup links them,
        # matching the retained assignments rather than creating duplicates.
        with connect(self.rosters.path) as db:
            db.execute("DELETE FROM dr_exam_assignment_links")
        self.assertEqual(self.link_rows(), [])
        ExamLinkService(self.rosters)
        self.assertEqual([(item["exam_key"], item["method"]) for item in self.link_rows()], [("quiz 4", "matched"), ("test 2", "matched")])
        self.assertEqual(sorted(self.assignments()), ["Exam 2", "Quiz 4"])
        ExamLinkService(self.rosters)
        self.assertEqual(len(self.link_rows()), 2)

    def test_key_matching_and_relinking(self):
        # An assignment keyed midterm-1 is Midterm 1 whatever its title says, so the booking links to it.
        keyed = self.client.post("/api/documents/assignments", json={"definition_id": self.definition_id, "key": "midterm-1", "title": "Something else"}, headers=HEADERS).json
        self.import_dr([booking(**{"Exam Type": "Midterm 1"})])
        self.assertEqual(list(self.assignments()), ["Something else"])
        self.assertEqual([(item["method"], item["assignment_id"] == keyed["id"]) for item in self.link_rows()], [("matched", True)])
        target = self.client.post("/api/documents/assignments", json={"definition_id": self.definition_id, "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        section_id = self.client.get("/api/seating/catalog").json["sections"][0]["id"]
        alice = next(student["id"] for student in self.client.get(f"/api/course-groups/{self.group['id']}/sections/507/roster").json["students"] if student["uin"] == "001234567")
        with self.rosters.lock(), connect(self.rosters.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.link_service.excluded(db, [section_id], target["id"]), set())
            self.assertEqual(self.link_service.relink(db, keyed["id"], target["id"]), 1)
            self.assertEqual(self.link_service.relink(db, keyed["id"], target["id"]), 0)
            with self.assertRaises(ValueError):
                self.link_service.relink(db, keyed["id"], "missing")
            self.assertEqual(self.link_service.excluded(db, [section_id], target["id"]), {alice})
            self.assertEqual([(item["title"], item["students"], item["methods"]) for item in self.link_service.summary(db, [section_id])], [("Test 1", 1, {"manual": 1})])
        self.assertEqual([item["method"] for item in self.link_rows()], ["manual"])
        with connect(self.rosters.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM document_events WHERE kind = 'dr_bookings_relinked'").fetchone()[0], 1)
        # Assignments span terms: a term-scoped relink leaves every other term's bookings where they are.
        term_id = self.client.get("/api/seating/catalog").json["sections"][0]["term_id"]
        with self.rosters.lock(), connect(self.rosters.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.link_service.relink(db, target["id"], keyed["id"], term_id="another-term"), 0)
            self.assertEqual(self.link_service.excluded(db, [section_id], target["id"]), {alice})
            self.assertEqual(self.link_service.relink(db, target["id"], keyed["id"], term_id=term_id), 1)
            self.assertEqual(self.link_service.excluded(db, [section_id], keyed["id"]), {alice})
        self.client.post("/api/courses", json={"name": "Physics", "code": "PHYS 206", "term": "Fall", "year": 2026, "sections": [1]}, headers=HEADERS)
        physics = next(d["id"] for d in self.client.get("/api/state").json["course_definitions"] if d["code"] == "PHYS 206")
        foreign = self.client.post("/api/documents/assignments", json={"definition_id": physics, "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        with self.rosters.lock(), connect(self.rosters.path) as db:
            with self.assertRaisesRegex(ValueError, "same course"):
                self.link_service.relink(db, target["id"], foreign["id"])


if __name__ == "__main__":
    unittest.main()
