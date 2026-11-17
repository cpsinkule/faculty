"""Bubble sheets sent to Gradescope, upload only (AGENTS.md "Sending grades to Gradescope"; requirement, 2026-09-25). Real sheets are drawn, read and reviewed as in test_bubble_keys; Gradescope is the loopback
fake, whose bubble-sheet exams keep their versions out of the assignments list as the real site does."""
import io
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pypdf import PdfReader
from werkzeug.exceptions import Conflict

from app import create_app
from bubble_fixture import pdf, sheet
from fake_gradescope import EMAIL, PASSWORD, FakeGradescope
from grading import gradescope_web as gw
from grading.gradescope_sync import GradescopeSender
from rosters import connect
from test_bubble_sheets import HEADERS, KEY, STUDENTS
from test_rosters import COLUMNS, csv_bytes

ALICE, BOB, CAROL = (("123456789", "Alice Example"), ("223456780", "Bob Example"), ("987650001", "Carol Nguyen"))
UINS = [student["uin"] for student in STUDENTS]


class SheetCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        self.inbox, self.scans, self.bubbles, self.frq = (self.app.extensions[name] for name in ("inbox", "document_scans", "bubble_sheets", "frq_grading"))
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [501, 502],
                                                         "multiple_sections": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        state = self.client.get("/api/state").json
        rows = [["", student["name"], student["uin"], "Registered", "", "", "", "", "", "", "", f"s{index}@example.test"] for index, student in enumerate(STUDENTS)]
        self.assertEqual(self.client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/501/roster",
                                          data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code, 201)
        self.assignment = self.client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1",
                                                                              "title": "Test 1"}, headers=HEADERS).json
        # Alice and Carol bubbled Version A, Bob Version B; all three are matched by their exact UINs.
        self.message = self.read([sheet(KEY, "A"), sheet(KEY, "B"), sheet(KEY, "A")], [ALICE, BOB, CAROL])
        self.clear(self.message)
        with connect(self.app.config["DATABASE"]) as db:
            self.aid = self.frq.scan_status(db, self.message)["plan"]["id"]
        self.gradescope()

    def read(self, images, texts):
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "sheets.pdf", "content": pdf(images)}], source="email", sender="scanner",
                                idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        self.assertTrue(self.scans.process_one())
        message = job["message_id"]
        self.assertEqual(self.client.post(f"/api/bubble-sheets/messages/{message}", json={"assignment_id": self.assignment["id"], "question_count": 10},
                                          headers=HEADERS).status_code, 202)
        ocr = lambda content, boxes: {"reads": [{"text": text} for pair in texts for text in pair][:len(boxes)], "seconds": 0}
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_fields", side_effect=ocr):
            self.assertTrue(self.bubbles.process_one())
        return message

    def clear(self, message):
        with connect(self.app.config["DATABASE"]) as db:
            fingerprint = self.frq.scan_status(db, message)["fingerprint"]
        return self.frq.review_scan(message, {"confirm_review": True, "fingerprint": fingerprint})

    def gradescope(self):
        self.fake = FakeGradescope().start()
        self.addCleanup(self.fake.stop)
        fake = self.fake
        self.course = fake.add_course("140-501", "Mathematics for Business and Social Sciences", "Fall 2026")
        self.forbidden = fake.add_course("MATH 140", "Math 140 for Instructors", "Fall 2026")
        self.users = {student["uin"]: fake.add_member(self.course, student["name"], student["uin"], f"{student['enrollment_id']}@example.invalid") for student in STUDENTS}
        self.exam, self.versions = fake.add_container(self.course, "Common Exam 1 - Multiple Choice", "ABC", 2, bubble_sheet=True)
        fake.add_container(self.course, "Common Exam 1 - Partial Credit", "ABC", 12)
        fake.add_container(self.course, "Common Exam 2 - Multiple Choice", "AB", 2, bubble_sheet=True)
        directory = tempfile.mkdtemp(dir=self.temp.name)
        self.credentials = os.path.join(directory, "gradescope.json")
        descriptor = os.open(self.credentials, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        os.write(descriptor, json.dumps({"email": EMAIL, "password": PASSWORD}).encode())
        os.close(descriptor)
        self.settings = gw.Settings(credentials_file=self.credentials, base=fake.url, pace=0)
        self.sender = GradescopeSender(self.frq, settings=self.settings)
        self.app.extensions["gradescope"].settings = self.settings  # the instance behind the Flask routes

    # --- helpers ----------------------------------------------------------------------------------------------------------
    def send(self, view=None, targets=None):
        view = view or self.sender.sheet_preview(self.aid, {"targets": targets} if targets else {})
        return self.sender.sheet_send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": targets or {}})

    def units(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT * FROM frq_gradescope_sheet_uploads ORDER BY created_at,rowid")]

    def sheets(self, message=None):
        with self.frq.rosters.lock(), connect(self.app.config["DATABASE"]) as db:
            run = db.execute("SELECT id FROM bubble_runs WHERE message_id=? ORDER BY created_at DESC LIMIT 1", (message or self.message,)).fetchone()[0]
            return self.bubbles._sheets(db, run)

    def on_gradescope(self, uin):
        """(version letter, filename, pages) of the student's sheet on the fake, or None."""
        found = next((s for s in self.fake.submissions.values() if self.users[uin] in s["owners"] and self.fake.assignments[s["assignment"]]["parent"] == self.exam), None)
        return (self.fake.assignments[found["assignment"]]["version"], found["filename"], found["pages"]) if found else None

    def ready_now(self):
        with connect(self.app.config["DATABASE"]) as db:
            db.execute("UPDATE frq_gradescope_sheet_uploads SET not_before=? WHERE not_before IS NOT NULL", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        self.sender._busy_until = None
        if self.sender._session is not None:
            self.sender._session.pace = 0

    def assertNothingForbidden(self):
        self.assertEqual((self.fake.forbidden_hits, self.fake.publish_hits, self.fake.comment_hits, self.fake.saves()), ([], [], [], []))
        self.assertFalse(any(r["path"].startswith(f"/courses/{self.forbidden}") for r in self.fake.requests))
        self.assertFalse(any(r["path"] == "/logout" for r in self.fake.requests))  # it would sign the instructor out everywhere


class SendSheets(SheetCase):
    def test_matched_sheets_go_up_as_their_students_in_the_version_their_bubbles_name(self):
        view = self.sender.sheet_preview(self.aid, {})
        self.assertEqual([(s["section"], s["course_id"], s["container_id"], s["versions"], s["proposed"]) for s in view["sections"]],
                         [(501, str(self.course), str(self.exam), {letter: str(a) for letter, a in self.versions.items()}, True)])  # never "?"
        self.assertEqual(view["summary"], {"sheets": {"upload": 3, "already": 0, "skipped": 0}, "read": 3, "waiting": 0})
        self.assertEqual(self.sender.sheet_status(self.aid)["coverage"], {"sheets": {"matched": 3, "sent": 0, "waiting": 0}})
        for uin in UINS:
            self.assertNotIn(uin, json.dumps(view))
        receipt = self.send(view)
        self.assertEqual(self.sender.drain(), 3)
        self.assertEqual({u["status"] for u in self.units()}, {"accepted"})
        placed = {uin: self.on_gradescope(uin) for uin in UINS[:3]}
        units = {u["enrollment_id"]: u for u in self.units()}
        self.assertEqual({uin: (found[0], found[2]) for uin, found in placed.items()}, {ALICE[0]: ("A", 1), BOB[0]: ("B", 1), CAROL[0]: ("A", 1)})
        self.assertTrue(all(found[1].startswith("faculty-") for found in placed.values()))
        self.assertEqual(sorted(u["gradescope_assignment_id"] for u in units.values()), sorted(str(self.versions[v]) for v in "ABA"))
        self.assertNothingForbidden()  # upload only: no grade was ever saved
        status = self.sender.sheet_status(self.aid)
        self.assertEqual((status["sends"][0]["id"], status["sends"][0]["status"], status["sends"][0]["counts"]), (receipt["send_id"], "complete", {"sheets": {"accepted": 3}}))
        self.assertEqual([(t["section"], t["container_id"]) for t in status["targets"]], [(501, str(self.exam))])
        self.assertEqual(status["coverage"], {"sheets": {"matched": 3, "sent": 3, "waiting": 0}})  # across every send, from the receipts
        with connect(self.app.config["DATABASE"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_courses").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sheet_targets").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends").fetchone()[0], 0)  # the written exam's panel knows nothing of it
        again = self.sender.sheet_preview(self.aid, {})
        self.assertEqual((again["summary"]["sheets"], [s["proposed"] for s in again["sections"]]), ({"upload": 0, "already": 3, "skipped": 0}, [False]))
        with self.assertRaisesRegex(Conflict, "nothing to send"):
            self.send(again)

    def test_sheets_go_several_at_once(self):
        self.send()
        self.fake.delay, self.fake.max_inflight = 0.1, 0
        self.assertEqual(self.sender.drain(parallel=8), 3)
        self.assertEqual({u["status"] for u in self.units()}, {"accepted"})
        self.assertEqual({uin: self.on_gradescope(uin)[0] for uin in UINS[:3]}, {ALICE[0]: "A", BOB[0]: "B", CAROL[0]: "A"})
        self.assertGreaterEqual(self.fake.max_inflight, 2)
        self.assertNothingForbidden()

    def test_the_sheet_s_own_version_decides_and_its_review_can_change_it(self):
        bob = next(item for item in self.sheets() if item["current"]["version"] == "B")
        # The instructor records version C for Bob's sheet in its review: that is the version it goes to.
        self.bubbles.review(bob["id"], {"answers": bob["current"]["answers"], "version": "C", "section_id": bob["current"]["section_id"],
                                        "enrollment_id": bob["current"]["enrollment_id"]})
        self.send()
        self.sender.drain()
        self.assertEqual(self.on_gradescope(BOB[0])[0], "C")

    def test_sheets_without_a_student_a_version_or_a_gradescope_student_are_skipped_and_said_why(self):
        other = self.read([sheet(KEY, "A"), sheet(KEY, "")], [("000000000", "Nobody Here"), ("555000111", "Dylan O'Brien")])
        # The Inbox review needs the instructor's word on both first: the unknown sheet is nobody's, Dylan's has no version.
        nobody, dylan = sorted(self.sheets(other), key=lambda item: item["source_page_index"])
        self.bubbles.review(nobody["id"], {"answers": nobody["current"]["answers"], "version": "A"})
        enrollment = next(s for s in self.sheets(other) if s["id"] == dylan["id"])["match"]["enrollment_id"]
        section = next(item["current"]["section_id"] for item in self.sheets() if item["current"]["section_id"])
        self.bubbles.review(dylan["id"], {"answers": dylan["current"]["answers"], "version": "", "section_id": section, "enrollment_id": enrollment})
        self.clear(other)
        carol = self.users[CAROL[0]]
        self.fake.members[self.course] = [m for m in self.fake.members[self.course] if m["user_id"] != carol]  # Carol is not on the Gradescope roster
        view = self.sender.sheet_preview(self.aid, {})
        reasons = {(s["name"], s["page"]): s["reason"] for s in view["skipped"]}
        self.assertEqual(reasons, {("", 1): "No student is matched to this sheet yet.",
                                   ("O'Brien, Dylan James", 2): "No version was read on this sheet. Record it in the sheet's review in the Inbox.",
                                   ("Nguyen, Carol", 3): "No student on the Gradescope roster of 140-501 has this student's UIN."})
        self.assertEqual(view["summary"]["sheets"], {"upload": 2, "already": 0, "skipped": 3})  # Alice and Bob

    def test_a_student_with_a_sheet_on_gradescope_is_left_alone_whatever_its_version(self):
        self.fake.add_submission(next(a for a, found in self.fake.assignments.items() if found["parent"] == self.exam and found["version"] == "?"), [self.users[ALICE[0]]])
        view = self.sender.sheet_preview(self.aid, {})
        self.assertEqual([s["reason"] for s in view["skipped"]], ["Gradescope already has a sheet for this student that Faculty did not upload; it is left alone."])
        self.send(view)
        self.sender.drain()
        self.assertEqual({uin: bool(self.on_gradescope(uin)) for uin in UINS[:3]}, {ALICE[0]: True, BOB[0]: True, CAROL[0]: True})
        self.assertEqual(len(self.fake.uploads()), 2)

    def test_a_sheet_scanned_twice_is_counted_once_and_never_listed_as_skipped(self):
        # Exam 2 (requirement, 2026-10-27): 49 rescanned sheets were listed as "No student is
        # matched to this sheet yet". Alice's reviewed sheet scanned again with the same marks is her one sheet: the copy is counted.
        alice = next(item for item in self.sheets() if item["current"]["enrollment_id"] and item["source_page_index"] == 1)
        self.bubbles.review(alice["id"], {"answers": alice["current"]["answers"], "version": "A", "section_id": alice["current"]["section_id"],
                                          "enrollment_id": alice["current"]["enrollment_id"]})
        again = self.read([sheet(KEY, "A")], [ALICE])
        self.clear(again)
        self.assertEqual([item["current"]["status"] for item in self.sheets(again)], ["repeat"])
        view = self.sender.sheet_preview(self.aid, {})
        self.assertEqual(view["skipped"], [])
        self.assertEqual(view["summary"]["sheets"], {"upload": 3, "already": 0, "skipped": 0, "repeats": 1})
        self.send(view)
        self.sender.drain()
        self.assertEqual(len(self.fake.uploads()), 3)
        self.assertEqual(self.on_gradescope(ALICE[0])[0], "A")

    def test_scans_still_in_the_inbox_are_counted_not_sent(self):
        self.read([sheet(KEY, "A")], [("555000111", "Dylan O'Brien")])  # not reviewed
        view = self.sender.sheet_preview(self.aid, {})
        self.assertEqual(view["summary"], {"sheets": {"upload": 3, "already": 0, "skipped": 0}, "read": 3, "waiting": 1})
        self.assertEqual(self.sender.sheet_status(self.aid)["coverage"]["sheets"]["waiting"], 1)

    def test_a_section_left_out_sends_nothing_and_a_published_exam_needs_its_confirmation(self):
        section = self.sender.sheet_preview(self.aid, {})["sections"][0]["section_id"]
        view = self.sender.sheet_preview(self.aid, {"targets": {section: {"leave_out": True}}})
        self.assertEqual((view["summary"]["sheets"], view["sections"][0]["left_out"]), ({"upload": 0, "already": 0, "skipped": 0, "left_out": 3}, True))
        with self.assertRaisesRegex(Conflict, "nothing to send"):
            self.send(view, targets={section: {"leave_out": True}})
        # Requirement, 2026-09-26: a published exam is a warning and a confirmation, no longer a refusal.
        self.fake.assignments[self.exam]["published"] = True
        view = self.sender.sheet_preview(self.aid, {})
        self.assertEqual((view["sections"][0]["problem"], view["sections"][0]["published"]), ("", True))
        self.assertIn("students see each sheet and its score", view["sections"][0]["warning"])
        self.assertEqual([(p["container_id"], p["sheets"]) for p in view["published"]], [(str(self.exam), 3)])
        self.assertEqual(view["summary"]["sheets"]["upload"], 3)
        with self.assertRaisesRegex(ValueError, "are published on Gradescope"):
            self.send(view)
        self.assertEqual(self.units(), [])
        self.sender.sheet_send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {},
                                          "published": [str(self.exam)]})
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units()}, {"accepted"})
        self.assertEqual((self.fake.publish_hits, self.fake.saves()), ([], []))  # still upload only, and nothing published

    def test_the_exam_proposed_is_the_bubble_sheet_exam_with_the_assignment_s_number(self):
        view = self.sender.sheet_preview(self.aid, {})
        self.assertEqual([c["title"] for c in view["sections"][0]["containers"]], ["Common Exam 1 - Multiple Choice", "Common Exam 2 - Multiple Choice"])
        self.assertEqual(view["sections"][0]["container"], "Common Exam 1 - Multiple Choice")
        self.assertFalse(any("Instructors" in c["name"] for c in view["courses"]))

    def test_a_sheet_changed_after_the_approval_is_not_sent(self):
        receipt = self.send()
        carol = next(item for item in self.sheets() if item["source_page_index"] == 3)
        self.bubbles.review(carol["id"], {"answers": carol["current"]["answers"], "version": "B", "section_id": carol["current"]["section_id"],
                                          "enrollment_id": carol["current"]["enrollment_id"]})
        self.sender.drain()
        failed = [u for u in self.units() if u["status"] == "failed"]
        self.assertEqual([u["error"] for u in failed], ["The sheet's student or version changed after the send was approved; preview and send again."])
        self.assertIsNone(self.on_gradescope(CAROL[0]))
        problems = self.sender.sheet_problems(self.aid, receipt["send_id"])["problems"]
        self.assertEqual([(p["name"], p["page"], p["status"]) for p in problems], [("Nguyen, Carol", 3, "failed")])
        view = self.sender.sheet_preview(self.aid, {})  # a new preview sends her sheet to the version now recorded
        self.assertEqual(view["summary"]["sheets"]["upload"], 1)
        self.send(view)
        self.sender.drain()
        self.assertEqual(self.on_gradescope(CAROL[0])[0], "B")

    def test_the_page_sent_is_the_page_read_turned_upright_when_it_was_upside_down(self):
        other = self.read([sheet(KEY, "A", upside_down=True)], [("555000111", "Dylan O'Brien")])
        self.clear(other)
        sent = {}
        upload = gw.Session.upload

        def spy(session, course, assignment, owner, data, filename):
            sent[owner] = data
            return upload(session, course, assignment, owner, data, filename)
        with patch.object(gw.Session, "upload", spy):
            self.send()
            self.sender.drain()
        dylan = PdfReader(io.BytesIO(sent[str(self.users["555000111"])]))
        alice = PdfReader(io.BytesIO(sent[str(self.users[ALICE[0]])]))
        self.assertEqual((len(dylan.pages), dylan.pages[0].rotation, len(alice.pages), alice.pages[0].rotation), (1, 180, 1, 0))
        with connect(self.app.config["DATABASE"]) as db:
            pages = {row[0]: row[1] for row in db.execute("SELECT u.enrollment_id,u.rotation FROM frq_gradescope_sheet_uploads u")}
        self.assertEqual(sorted(pages.values()), [0, 0, 0, 180])

    def test_a_busy_gradescope_is_waited_for_and_the_sheet_goes_again(self):
        self.send()
        self.fake.fail("POST", r"/submissions$", ("status", 429, {"Retry-After": "90"}))
        self.assertTrue(self.sender.process_one())
        first = self.units()[0]
        self.assertEqual(first["status"], "pending")
        self.assertIn("429 Too Many Requests", first["error"])
        self.assertFalse(self.sender.process_one())
        self.ready_now()
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units()}, {"accepted"})
        self.assertEqual((len(self.fake.uploads()), len([s for s in self.fake.submissions.values()])), (4, 3))

    def test_cancel_and_retry(self):
        self.fake.fail("POST", r"/submissions$", ("status", 302))  # answered, not kept
        receipt = self.send()
        self.sender.drain()
        self.assertEqual(sorted(u["status"] for u in self.units()), ["accepted", "accepted", "failed"])
        self.sender.sheet_retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units()}, {"accepted"})
        with self.assertRaisesRegex(Conflict, "Nothing in this send failed"):
            self.sender.sheet_retry(self.aid, receipt["send_id"])
        other = self.read([sheet(KEY, "A")], [("555000111", "Dylan O'Brien")])
        self.clear(other)
        second = self.send()
        cancelled = self.sender.sheet_cancel(self.aid, second["send_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(self.units()[-1]["status"], "cancelled")
        self.assertIsNone(self.on_gradescope("555000111"))

    def test_routes(self):
        base = f"/api/grading/gradescope/assignments/{self.aid}/sheets"
        status = self.client.get(base)
        self.assertEqual((status.status_code, status.json["configured"], status.json["sends"]), (200, True, []))
        preview = self.client.post(f"{base}/preview", json={}, headers=HEADERS)
        self.assertEqual((preview.status_code, preview.json["summary"]["sheets"]["upload"]), (200, 3))
        sent = self.client.post(f"{base}/send", json={"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": preview.json["fingerprint"], "targets": {}},
                                headers=HEADERS)
        self.assertEqual(sent.status_code, 201, sent.json)
        again = self.client.post(f"{base}/send", json={"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": preview.json["fingerprint"], "targets": {}},
                                 headers=HEADERS)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self.client.get(f"{base}/sends/{sent.json['send_id']}/problems").json["problems"], [])
        self.assertEqual(self.client.post(f"{base}/sends/{sent.json['send_id']}/cancel", json={}, headers=HEADERS).json["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
