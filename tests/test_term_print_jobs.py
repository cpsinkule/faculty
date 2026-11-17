"""Print jobs owned by the term (document_identity migration 013; requirement, 2026-10-01).

"If an assignment is printed this way with no seating chart, it shouldn't assign sections to the barcode. I intend to mass print
these out and not separate stacks for each section, so I can't guarantee that a given section gets the right copies." A job printed
without a seating chart names no section: its stamps resolve to the course, term, assignment, version, copy and page; a copy can be
named for a student of any section the job lists, and is then in that student's own section for collection and grading.
"""
import io
import sqlite3
import unittest
import uuid

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from rosters import connect
from test_exam_workflow import HEADERS, ExamWorkflowTests, student
from test_rosters import csv_bytes


class TermPrintJobTests(unittest.TestCase):
    # The exam workflow fixtures: a MATH 140 Fall 2026 course with Sections 501 and 502, Alice and Bob in 501.
    setUp = ExamWorkflowTests.setUp
    import_students = ExamWorkflowTests.import_students
    reload = ExamWorkflowTests.reload
    arrival = ExamWorkflowTests.arrival
    select = ExamWorkflowTests.select

    def import_502(self, rows):
        response = self.client.post(f"/api/course-groups/{self.group['id']}/sections/502/roster",
            data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)

    def quiz(self):
        """A quiz for both sections, Carol in 502, and its exam record."""
        self.import_502([student("Example, Carol", "223456789")])
        assignment = self.documents.create_assignment(self.term["definition_id"], "quiz-1", "Quiz 1")
        event = self.exams.create({"term_id": self.term["id"], "assignment_id": assignment["id"],
                                   "section_ids": [s["id"] for s in self.sections]})
        people = {p["name"]: p for p in event["participants"]}
        return assignment, event, people["Example, Alice"], people["Example, Carol"]

    def version(self, assignment, page_count=2):
        """Catalog provenance only, without native PDF rendering (as the exam workflow tests do)."""
        version_id = str(uuid.uuid4())
        with connect(self.path) as db:
            db.execute("INSERT INTO assignment_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (version_id, assignment["id"], version_id,
                "fixture/source.json", "a" * 64, "fixture/canonical.pdf", "b" * 64, page_count, "{}", "faculty-page-v1", timestamp()))
        return version_id

    def term_batch(self, version_id, quantity=2, students=None, sections=None, request_id=None, **kw):
        batch = self.documents.create_batch(version_id, None, quantity, request_id or str(uuid.uuid4()), students=students,
                                            term_id=self.term["id"], section_ids=[s["id"] for s in (sections or self.sections)], **kw)
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET status='ready',output_path='fixture/print.pdf',output_sha256=?,manifest_path='fixture/manifest.json',manifest_sha256=? WHERE id=?",
                       ("c" * 64, "d" * 64, batch["id"]))
            db.execute("UPDATE page_stamps SET state='issued' WHERE printed_copy_id IN (SELECT id FROM printed_copies WHERE print_batch_id=?)", (batch["id"],))
        return self.documents.get_batch(batch["id"])

    def manifest(self, batch):
        with connect(self.path) as db:
            return self.documents.manifest(db, batch["id"])

    def inspected(self, item):
        """An arrival whose inspection has ended, as a real scan's has before its names are read (QUIZZES "NAMES ON UNNAMED STAMPED
        COPIES" > AFTER THE INSPECTION): `arrival` retains its pages directly and leaves the queued inspection pending."""
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (item["message_id"],))
        return item

    def collection(self, event, person):
        event = self.exams.get(event["id"])
        return next(s for s in event["reconciliation"]["students"] if s["enrollment_id"] == person["enrollment_id"])

    def test_a_term_job_names_no_section_and_its_stamps_resolve_to_the_term(self):
        assignment, event, alice, carol = self.quiz()
        batch = self.term_batch(self.version(assignment))
        self.assertIsNone(batch["section_id"])
        self.assertEqual(batch["term_id"], self.term["id"])
        self.assertEqual([s["number"] for s in batch["sections"]], [501, 502])
        self.assertIsNone(batch["section_number"])
        for entry in self.manifest(batch):
            found = self.documents.resolve_token(entry["token"], authenticated=True)
            self.assertEqual((found["section_id"], found["section_number"], found["term_id"], found["assignment_id"], found["page_number"]),
                             (None, None, self.term["id"], assignment["id"], entry["page_number"]))
            alias = self.documents.resolve_token(entry["recovery_alias"], authenticated=True, alias=True)
            self.assertEqual(alias["stamp_id"], found["stamp_id"])
        listed = next(b for b in self.documents.catalog()["batches"] if b["id"] == batch["id"])
        self.assertEqual((listed["section_id"], listed["term_id"], [s["number"] for s in listed["sections"]]), (None, self.term["id"], [501, 502]))
        # A section's job keeps its section and gains only its term.
        section_job = self.documents.create_batch(self.version(assignment), self.section["id"], 1, str(uuid.uuid4()))
        self.assertEqual((section_job["section_id"], section_job["term_id"], section_job["section_number"]), (self.section["id"], self.term["id"], 501))
        self.assertNotIn("sections", section_job)

    def test_any_copy_goes_to_any_listed_sections_student_and_is_collected_in_their_section(self):
        assignment, event, alice, carol = self.quiz()
        batch = self.term_batch(self.version(assignment))
        first, second = batch["copies"]
        # Copy 1 went to a student of 502 and copy 2 to one of 501: the stack was handed out across sections.
        self.documents.assign_copy(first["id"], carol["enrollment_id"], "Read from page 1")
        self.documents.assign_copy(second["id"], alice["enrollment_id"], "Read from page 1")
        event = self.exams.attach_batch(event["id"], {"version": event["version"], "batch_id": batch["id"]})
        for copy in (first, second):
            self.select(copy, [self.arrival(copy, [1, 2])])
        for person in (alice, carol):
            found = self.collection(event, person)
            self.assertEqual(found["status"], "complete", found)
            self.assertEqual(found["section_id"], person["section_id"])
        self.assertEqual(self.exams.get(event["id"])["reconciliation"]["unassigned_copies"], [])
        with connect(self.path) as db:
            paper = self.exams._participant_paper(db, self.exams._event(db, event["id"]), carol["section_id"], carol["enrollment_id"])
        self.assertEqual(paper["collection"]["status"], "complete")
        # A section's job still takes only its own section's students.
        section_job = self.documents.create_batch(self.version(assignment), self.section["id"], 1, str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, "section roster"):
            self.documents.assign_copy(section_job["copies"][0]["id"], carol["enrollment_id"], "Wrong section")

    def test_a_term_job_cannot_take_a_student_or_section_of_another_term(self):
        assignment, event, alice, carol = self.quiz()
        response = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Spring", "year": 2027,
            "sections": [501]}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        foreign = next(s for s in self.exams.catalog()["sections"] if s["term_id"] != self.term["id"])
        version_id = self.version(assignment)
        with self.assertRaisesRegex(ValueError, "term"):
            self.term_batch(version_id, sections=[foreign])
        with self.assertRaisesRegex(ValueError, "sections this print job is for"):
            self.documents.create_batch(version_id, None, 1, str(uuid.uuid4()), term_id=self.term["id"])
        with self.assertRaisesRegex(ValueError, "seating chart"):
            self.documents.create_batch(version_id, None, 1, str(uuid.uuid4()), term_id=self.term["id"], section_ids=[self.section["id"]],
                                        seating_chart_id=str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, "that section only"):
            self.documents.create_batch(version_id, self.section["id"], 1, str(uuid.uuid4()), section_ids=[self.section["id"]])
        batch = self.term_batch(version_id, quantity=1)
        with connect(self.path) as db:
            db.execute("INSERT INTO student_identities VALUES('000000009','99999',?)", (timestamp(),))
            db.execute("INSERT INTO student_enrollments VALUES('9999999999',?,'000000009',?)", (foreign["term_id"], timestamp()))
            db.execute("INSERT INTO roster_memberships VALUES(?,'9999999999')", (foreign["id"],))
        with self.assertRaisesRegex(ValueError, "a section of this term"):
            self.documents.assign_copy(batch["copies"][0]["id"], "9999999999", "Another term")
        with connect(self.path) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO print_batch_sections VALUES(?,?)", (batch["id"], foreign["id"]))

    def test_requests_are_idempotent_and_reprints_keep_term_and_sections(self):
        assignment, event, alice, carol = self.quiz()
        version_id = self.version(assignment)
        request = str(uuid.uuid4())
        sections = [s["id"] for s in self.sections]
        first = self.documents.create_batch(version_id, None, 2, request, term_id=self.term["id"], section_ids=sections)
        again = self.documents.create_batch(version_id, None, 2, request, term_id=self.term["id"], section_ids=list(reversed(sections)))
        self.assertEqual(first["id"], again["id"])
        with self.assertRaises(Conflict):
            self.documents.create_batch(version_id, None, 2, request, term_id=self.term["id"], section_ids=sections[:1])
        reprint = self.documents.create_batch(version_id, None, 2, str(uuid.uuid4()), term_id=self.term["id"], section_ids=sections, reprint_of=first["id"])
        self.assertEqual((reprint["section_id"], reprint["reprint_of_batch_id"]), (None, first["id"]))
        with self.assertRaises(ValueError):  # a section's job is not a reprint of the term's
            self.documents.create_batch(version_id, self.section["id"], 2, str(uuid.uuid4()), reprint_of=first["id"])

    def test_a_scan_of_the_stack_is_for_the_jobs_sections(self):
        assignment, event, alice, carol = self.quiz()
        batch = self.term_batch(self.version(assignment))
        self.exams.attach_batch(event["id"], {"version": event["version"], "batch_id": batch["id"]})
        item = self.arrival(batch["copies"][0], [1, 2])
        with connect(self.path) as db:  # the fixture retains pages directly; say their inspection finished
            db.execute("INSERT OR REPLACE INTO document_decode_jobs(message_id,status,attempts,error,created_at,updated_at) VALUES(?,'complete',1,'',?,?)",
                       (item["message_id"], timestamp(), timestamp()))
        item = self.scans.review_item(item["message_id"])
        recognition = item["recognition"]
        self.assertEqual(recognition["verdict"], "recognized")
        self.assertEqual(recognition["sections"], [501, 502])
        paper = recognition["current_papers"][0]
        self.assertIsNone(paper["section_id"])
        self.assertEqual([s["number"] for s in paper["for_sections"]], [501, 502])
        self.assertIn("Sections 501 and 502", item["overview"]["headline"])
        page = next(p for p in item["pages"] if p["page_number"] == 1)
        self.assertIsNone(page["section_number"])
        self.assertEqual(page["year"], 2026)

    def test_names_on_page_1_are_matched_against_every_listed_sections_roster(self):
        assignment, event, alice, carol = self.quiz()
        batch = self.term_batch(self.version(assignment), request_id=f"quiz:{uuid.uuid4()}:A")
        item = self.arrival(batch["copies"][0], [1, 2])
        names = self.scans.copy_names
        with self.rosters.lock(), connect(self.path) as db:
            copies = names._copies(db, item["message_id"])
            self.assertEqual(len(copies), 1)
            pool = names._roster(db, copies[0], {})
        self.assertEqual({s["name"] for s in pool.values()}, {"Example, Alice", "Example, Bob", "Example, Carol"})
        self.assertEqual({s["section_number"] for s in pool.values()}, {501, 502})
        found = names.search_students(item["message_id"], {"uin": "2234", "printed_copy_id": batch["copies"][0]["id"]})
        self.assertEqual(found["students"][0]["name"], "Example, Carol")
        view = names.assign(item["message_id"], {"printed_copy_id": batch["copies"][0]["id"], "enrollment_id": carol["enrollment_id"]})
        # Named by the instructor (no reading), the copy leaves the list of papers printed without a name.
        self.assertFalse(any(c["copy_id"] == batch["copies"][0]["id"] for c in view["copies"]))
        self.assertEqual(self.documents.get_batch(batch["id"])["copies"][0]["enrollment_id"], carol["enrollment_id"])
        # Printed for no student and naming no section, a term job's copies are read even when no quiz approval made them.
        plain = self.term_batch(self.version(assignment, page_count=1), quantity=1)
        other = self.arrival(plain["copies"][0], [1])
        with connect(self.path) as db:
            self.assertEqual(names.status(db, other["message_id"])["total"], 1)

    def test_jev_names_a_copy_whose_name_the_ocr_could_not_read(self):
        # JEV PAPERS (requirement, 2026-10-15): a stamped copy printed for no student whose top the OCR reads no name from is
        # named from Jev's reading of the handwritten name and UIN, against the copy's own pool (every listed section here).
        self.named_by_the_handwriting({}, "Jev's reading of the handwritten name and UIN")

    def test_a_copy_gemini_matched_says_gemini_read_it(self):
        # DOCUMENTS "JEV PAPERS" > GEMINI MATCHES THE STUDENT (requirement, 2026-11-12): the match says who read the name.
        self.named_by_the_handwriting({"reader": "Gemini"}, "Gemini's reading of the handwritten name and UIN")

    def named_by_the_handwriting(self, said, basis_text):
        from unittest.mock import patch
        from document_identity import unlabeled
        from teach_inbox.service import sha
        assignment, event, alice, carol = self.quiz()
        batch = self.term_batch(self.version(assignment), request_id=f"quiz:{uuid.uuid4()}:A")
        item = self.inspected(self.arrival(batch["copies"][0], [1, 2]))
        names = self.scans.copy_names
        reads = lambda contents, *a, **k: {"pages": [{"source_sha256": sha(c), "reads": [{"kind": "band", "rotation": 0, "text": "MATH 140 Quiz 5"}]}
                                                      for c in contents], "model_id": "fixture"}
        seen = []

        def identify(jev, owner, picture, check, students):
            seen.append(sorted(s["name"] for s in students.values()))
            return {"status": "complete", "enrollment_id": carol["enrollment_id"], "candidates": [carol["enrollment_id"]], **said}

        box = {"top": .05, "bottom": .1, "left": .1, "right": .4}
        check = {"status": "complete", "first_page": True, "has_name": .99, "orientation": "upright", "name_box": box, "uin_box": box}
        with patch("faculty_ocr.recognize_covers", side_effect=reads), patch.object(unlabeled.UnlabeledService, "_jev", return_value=object()), \
                patch("document_identity.jev_papers.page_check", return_value=check), patch("document_identity.jev_papers.identify", side_effect=identify):
            self.assertTrue(names.process_one())
        self.assertEqual(seen, [["Example, Alice", "Example, Bob", "Example, Carol"]])
        self.assertEqual(self.documents.get_batch(batch["id"])["copies"][0]["enrollment_id"], carol["enrollment_id"])
        view = names.summary(item["message_id"])
        self.assertEqual([(c["state"], c["basis_text"]) for c in view["copies"]], [("named", basis_text)])

    def test_the_name_requests_of_two_pages_are_asked_side_by_side_each_page_in_its_own_order(self):
        # QUIZZES "NAMES ON UNNAMED STAMPED COPIES" > SIDE BY SIDE (requirement, 2026-11-14): two first pages' checks are in flight at once, each
        # page's match still follows its own page check, and each copy is named from its own page.
        import threading
        from unittest.mock import patch
        from document_identity import copy_names, unlabeled
        from teach_inbox.service import sha
        self.assertGreaterEqual(copy_names.PARALLEL, 2)
        assignment, event, alice, carol = self.quiz()
        batch = self.term_batch(self.version(assignment), request_id=f"quiz:{uuid.uuid4()}:A")
        owners = {}
        for copy, person in zip(batch["copies"], (alice, carol)):
            item = self.inspected(self.arrival(copy, [1, 2]))
            owners[next(p["id"] for p in item["pages"] if p["page_number"] == 1)] = person["enrollment_id"]
        reads = lambda contents, *a, **k: {"pages": [{"source_sha256": sha(c), "reads": [{"kind": "band", "rotation": 0, "text": "MATH 140 Quiz 1"}]}
                                                      for c in contents], "model_id": "fixture"}
        together, asked = threading.Barrier(2, timeout=5), []
        box = {"top": .05, "bottom": .1, "left": .1, "right": .4}

        def page_check(jev, owner, picture):
            together.wait()  # passes only while the other page's check is being asked too
            asked.append(("check", owner))
            return {"status": "complete", "first_page": True, "has_name": .99, "orientation": "upright", "name_box": box, "uin_box": box}

        def identify(jev, owner, picture, check, students):
            asked.append(("identify", owner))
            return {"status": "complete", "enrollment_id": owners[owner], "candidates": [owners[owner]]}

        with patch("faculty_ocr.recognize_covers", side_effect=reads), patch.object(unlabeled.UnlabeledService, "_jev", return_value=object()), \
                patch("document_identity.jev_papers.page_check", side_effect=page_check), patch("document_identity.jev_papers.identify", side_effect=identify):
            self.assertTrue(self.scans.copy_names.process_one())
        self.assertFalse(together.broken)
        self.assertEqual(sorted(asked), sorted([(step, owner) for owner in owners for step in ("check", "identify")]))
        for owner in owners:
            self.assertLess(asked.index(("check", owner)), asked.index(("identify", owner)))
        self.assertEqual([c["enrollment_id"] for c in self.documents.get_batch(batch["id"])["copies"]],
                         [alice["enrollment_id"], carol["enrollment_id"]])

    def test_names_wait_until_their_scans_inspection_ends(self):
        # QUIZZES "NAMES ON UNNAMED STAMPED COPIES" > AFTER THE INSPECTION (requirement, 2026-11-14): no first page of a scan whose inspection is
        # pending or running is read; the scan beside it that is done is, and the held page is read once its inspection ends.
        from unittest.mock import patch
        from teach_inbox.service import sha
        assignment, event, alice, carol = self.quiz()
        batch = self.term_batch(self.version(assignment), request_id=f"quiz:{uuid.uuid4()}:A")
        inspecting, done = (self.arrival(copy, [1, 2]) for copy in batch["copies"])
        first = {item["message_id"]: next(p["id"] for p in item["pages"] if p["page_number"] == 1) for item in (inspecting, done)}
        loads = []

        def reads(contents, *a, **k):
            loads.append(len(contents))
            return {"pages": [{"source_sha256": sha(c), "reads": [{"kind": "band", "rotation": 0, "text": "MATH 140 Quiz 1"}]} for c in contents],
                    "model_id": "fixture"}

        def decode(item, status):
            with connect(self.path) as db:
                db.execute("INSERT INTO document_decode_jobs(message_id,status,created_at,updated_at) VALUES(?,?,?,?) "
                           "ON CONFLICT(message_id) DO UPDATE SET status=excluded.status", (item["message_id"], status, timestamp(), timestamp()))

        def read_pages():
            with connect(self.path) as db:
                return {row[0] for row in db.execute("SELECT scan_page_id FROM copy_name_reads")}

        names = self.scans.copy_names
        decode(inspecting, "running")
        decode(done, "complete")
        with patch("faculty_ocr.recognize_covers", side_effect=reads):
            self.assertTrue(names.process_one())
            self.assertEqual(read_pages(), {first[done["message_id"]]})
            for status in ("running", "pending"):
                decode(inspecting, status)
                self.assertFalse(names.process_one())
            decode(inspecting, "complete")
            self.assertTrue(names.process_one())
        self.assertEqual(loads, [1, 1])
        self.assertEqual(read_pages(), set(first.values()))

    def test_a_term_jobs_assessment_lists_its_sections_on_exams(self):
        """An assessment printed as one stack without a seating chart and not yet scanned has no exam record: its sections come from
        the jobs' print_batch_sections (by reference), and the hub says it was printed as one stack."""
        assignment = self.documents.create_assignment(self.term["definition_id"], "quiz-5", "Quiz 5")
        for _ in range(2):  # two versions, one term job each: one interleaved stack
            self.term_batch(self.version(assignment), quantity=3)
        query = {"assignment_id": assignment["id"], "term_id": self.term["id"]}
        result = self.client.get("/api/exams/lifecycle", query_string=query).json
        self.assertIsNone(result["event_id"])
        self.assertEqual(result["sections"], [501, 502])
        stages = {stage["key"]: stage for stage in result["stages"]}
        self.assertEqual(stages["seating"]["detail"], "No seating chart · printed as one stack")
        self.assertEqual(stages["print"]["detail"], "2 of 2 print jobs ready · 6 copies · one stack")
        self.assertEqual(stages["print"]["href"], f"#documents?jobs={assignment['id']}")
        listed = next(item for item in self.client.get("/api/exams").json["assessments"] if item["assignment_id"] == assignment["id"])
        self.assertEqual(listed["sections"], [501, 502])


del ExamWorkflowTests  # its own tests run in test_exam_workflow


if __name__ == "__main__":
    unittest.main()
