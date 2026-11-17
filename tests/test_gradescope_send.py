"""Sending grades to Gradescope (AGENTS.md), offline: a real Faculty plan with FINAL grades against the loopback FakeGradescope.

Nothing here reaches gradescope.com or reads a real sign-in: every test writes its own 0600 sign-in file in its temporary
workspace and hands the fake's loopback URL to `gradescope_web.Settings`. The reviewed papers are the fixtures' synthetic page
bytes; the document sandbox's merge is replaced by a small real PDF with the right number of pages.

The fixture is the real use case: one section (501, Fall 2026), two students seated on Versions A and B of an exam, two questions
per version whose rubrics were imported from Gradescope's evaluation export (numbered 3.x and 4.x, printed as questions 20 and 21,
the second with the Part A / Part B groups whose items restart their positions), and the instructor's approved reviews.
"""
import copy
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from pypdf import PdfWriter
from werkzeug.exceptions import Conflict

from app import create_app
from exam_workflow.service import ExamService
from grading import gradescope_sync as gs
from grading import gradescope_web as gw
from grading.gradescope_sync import GradescopeSender
from rosters import connect
import test_exam_grading as exam_fixture
import test_frq_grading as grading_fixture
import test_grading_exam_versions as versions_fixture
from fake_gradescope import EMAIL, PASSWORD, FakeGradescope
from frq_fixtures import finalize_rubrics, page
from test_gradescope_rubrics import encode, export_rows
from test_rosters import STUDENTS, csv_bytes
import test_grading_standard as standard_fixture
from test_grading_cloud import endpoint
from test_grading_pipeline import RULES, Pipeline

HEADERS = exam_fixture.HEADERS
ALICE_UIN, BOB_UIN = "001234567", "123456789"
# Gradescope's order: (points, description) is a top-level item, (group, [(points, description), ...]) a group.
Q20 = [(10, "Correct answer: $$x=6$$ with work to justify it"), (4, "Set up the equation correctly"), (2, "Correct answer, but no work"),
       (0, "No answer or completely incorrect")]
Q21 = [(8, "Correct answers for parts a and b"),
       ("Part A", [(4, "Correct dimensions: $$3 \\times 2$$"), (1, "Correct number of rows, but not columns"), (0, "Neither rows nor columns correct")]),
       ("Part B", [(4, "Not possible, with the dimensions as the reason"), (2, "Not possible, but no reason given")]),
       (0, "No answers for either part")]
OTHER = [(2, "Correct"), (0, "Incorrect")]


def pdf(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(612, 792)
    data = io.BytesIO()
    writer.write(data)
    return data.getvalue()


def flatten(entries):
    """The export's numbering: groups inline, each grouped line named "<group>: <item>" as Faculty's imported lines are."""
    lines = []
    for entry in entries:
        lines.extend([(p, f"{entry[0]}: {d}") for p, d in entry[1]] if isinstance(entry[1], list) else [entry])
    return lines


def export_csv(maximum, entries, index):
    lines = flatten(entries)
    return encode(export_rows(maximum, [p for p, _ in lines], descriptions=[d for _, d in lines], question=index))


class FirstLine(grading_fixture.FakeProvider):
    """Proposes the first line of whatever rubric it is shown; the instructor's review decides the final grade."""
    def grade(self, payload, images):
        first = payload["rubric"]["items"][0]["id"]
        return {"output": {"applications": [{"rubric_item_id": first, "applied": True, "reason": "Fixture", "origin_error_id": None, "confidence": .98,
            "evidence": [{"line_id": "line-1", "student_text": "3x - 8 = 10", "bbox": [.1, .1, .8, .3], "crop": 0}]}],
            "interpretation": {"meaning": "Fixture", "error_graph": []}, "confidence": .98, "rubric_ambiguities": [], "review_reasons": []},
            "raw": {"fixture": True}, "metadata": {"model": "fixture-grade"}}


class Fixture:
    """The Faculty side, shared by the one-time build and the tests."""
    document_version = exam_fixture.ExamGradingTests.document_version
    ready_batch = exam_fixture.ExamGradingTests.ready_batch
    reviewed_source = exam_fixture.ExamGradingTests.reviewed_source
    blank = versions_fixture.ExamVersionTests.blank
    compare = versions_fixture.ExamVersionTests.compare
    seat = versions_fixture.ExamVersionTests.seat
    KEPT = ("plan", "q", "enrollment", "second", "section", "version_id", "templates", "seats", "event", "assignment", "term_id", "uins", "batch", "looks_like")

    def patches(self):
        self.addCleanup(patch.stopall)
        self.enterContext(patch.object(self.frq, "require_location_review"))
        self.enterContext(patch.object(self.frq.templates, "read", side_effect=lambda identifier, kind, page=None: (f"blank-{identifier}-{page}".encode(), "image/png")))
        self.enterContext(patch("grading.version_match.compare", side_effect=self.compare))
        self.enterContext(patch.object(self.frq, "provider", return_value=FirstLine()))
        image = page()
        self.enterContext(patch("grading.extraction.extract", return_value={"metadata": {"registered": True, "crop_bounds": [0, 0, 100, 100]},
            "images": {k: image for k in ("original", "grayscale", "contrast", "ink", "cloud")}}))
        self.merged, self.rotations = [], []
        self.enterContext(patch.object(gs.process, "merge", side_effect=self.merge))

    def build(self):
        exam_fixture.ExamGradingTests.setUp(self)  # Section 501 (Fall 2026), two students, the exam, the first student's reviewed paper.
        self.frq = self.app.extensions["frq_grading"]
        with connect(self.path) as db:
            self.second = db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=? AND enrollment_id!=? ORDER BY enrollment_id",
                                     (self.section["id"], self.enrollment)).fetchone()[0]
            self.term_id = db.execute("SELECT term_id FROM course_sections WHERE id=?", (self.section["id"],)).fetchone()[0]
            self.uins = dict(db.execute("SELECT id,uin FROM student_enrollments"))
        self.assertEqual(sorted(self.uins.values()), sorted([ALICE_UIN, BOB_UIN]))
        self.reviewed_source(batch=self.ready_batch(enrollment_id=self.second), changed="second")
        self.plan = self.frq.create_assignment({"event_id": self.event["id"]})
        self.templates = {letter: self.blank(letter) for letter in "AB"}
        self.looks_like = {}
        self.patches()
        self.seat(enrollment="A", second="B")
        self.q = {}
        for letter in "AB":
            self.q[("q20", letter)] = self.question(f"q20{'-B' if letter == 'B' else ''}", letter, 10, "20", 3, Q20)
            self.q[("q21", letter)] = self.question(f"q21{'-B' if letter == 'B' else ''}", letter, 8, "21", 4, Q21)
        finalize_rubrics(self.frq, self.plan["id"])
        for _ in range(10):
            if not self.frq.process_version_one():
                break
        self.grade_all()
        # The instructor's decisions: the first student (Version A) 4 on q20 and 6 on q21; the second (Version B) 2 on q20 and 5 on q21.
        self.decide("q20", "A", ["R3_2"])
        self.decide("q21", "A", ["R4_2", "R4_6"])
        self.decide("q20", "B", ["R3_3"])
        self.decide("q21", "B", ["R4_3", "R4_5"])

    def merge(self, contents):
        self.merged.append(len(contents))
        self.rotations.append([item[1] if isinstance(item, tuple) else 0 for item in contents])
        return pdf(len(contents))

    def question(self, key, letter, points, printed, index, entries):
        plan = self.frq.assignment(self.plan["id"])
        plan = self.frq.save_question(plan["id"], {"version": plan["version"], "question_key": key, "assignment_version_id": self.version_id, "exam_version": letter,
            "content": {"title": f"Question {printed}", "max_points": points, "problem": "Solve", "template_id": self.templates.get(letter), "printed_number": printed,
                        "regions": [{"page": 1, "region": {"x": .1, "y": .1, "width": .8, "height": .4}}]}})
        q = next(q for q in plan["questions"] if q["question_key"] == key)
        plan = self.frq.import_rubric(plan["id"], q["question_id"], {"version": q["version"], "source_text": export_csv(points, entries, index), "format": "csv"})
        q = next(q for q in plan["questions"] if q["question_key"] == key)
        self.frq.approve_rubric(plan["id"], q["question_id"], {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})
        return q["question_id"]

    def grade_all(self):
        command = {"request_id": str(uuid.uuid4()), "question_ids": list(self.q.values()), "mode": "local", "operation": "recognize"}
        found = self.frq.preview(self.plan["id"], command)
        self.assertEqual(len(found["eligible"]), 4, found["skipped"])
        job = self.frq.create_job(self.plan["id"], {**command, "fingerprint": found["fingerprint"]})
        for _ in range(80):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")

    def response(self, key, letter):
        qid = self.q[(key, letter)]
        return self.frq.response(next(r["id"] for r in self.frq.responses(self.plan["id"], qid)))

    def decide(self, key, letter, lines, status="approved", **extra):
        response = self.response(key, letter)
        return self.frq.save_review(response["id"], {"version": response["version"], "result_id": response["current_result_id"], "status": status,
                                                     "confirm_review": True, "selected_items": lines, **extra})



BUILT = {}


def tearDownModule():
    # Other modules (test_gradebook_sources, test_grading_certification) share this workspace and tear it down too: forget it
    # entirely, so the next module that uses it builds it again instead of finding "kept" without its template.
    if "template" in BUILT:
        shutil.rmtree(BUILT["template"], ignore_errors=True)
    BUILT.clear()


class Workspace(Fixture, unittest.TestCase):
    """Builds the graded workspace once per test class (`Workspace("build")`); each test works on its own copy. It has no test
    of its own, so the loader collects nothing here."""


class GradescopeCase(Fixture, unittest.TestCase):
    managed = False

    @classmethod
    def setUpClass(cls):
        """The graded workspace is built once for the module (it takes seconds) and copied for each test (it takes milliseconds)."""
        if not BUILT:
            builder = Workspace("build")
            try:
                builder.build()
                BUILT["template"] = tempfile.mkdtemp()
                shutil.copytree(builder.directory.name, BUILT["template"], dirs_exist_ok=True)
                BUILT["kept"] = {name: getattr(builder, name) for name in cls.KEPT}
            finally:
                builder.doCleanups()
        cls.template, cls.kept = BUILT["template"], BUILT["kept"]

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        shutil.copytree(self.template, self.directory.name, dirs_exist_ok=True)
        for name, value in copy.deepcopy(self.kept).items():
            setattr(self, name, value)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.documents, self.scans = self.app.extensions["documents"], self.app.extensions["document_scans"]
        self.inbox, self.rosters = self.app.extensions["inbox"], self.app.extensions["rosters"]
        self.path = self.rosters.path
        self.exams = self.app.extensions.get("exams") or ExamService(self.rosters, self.documents, self.scans, self.app.extensions["seating"],
                                                                    self.app.extensions["response_cards"])
        self.frq = self.app.extensions["frq_grading"]
        self.patches()
        self.aid = self.plan["id"]
        self.gradescope()

    # --- Gradescope -------------------------------------------------------------------------------------------------------
    def gradescope(self):
        self.fake = FakeGradescope().start()
        self.addCleanup(self.fake.stop)
        fake = self.fake
        self.course = fake.add_course("140-501", "Mathematics for Business and Social Sciences", "Fall 2026")
        fake.add_course("140-502", "Mathematics for Business and Social Sciences", "Fall 2026")
        fake.add_course("140-501", "Mathematics for Business and Social Sciences", "Spring 2026")
        fake.add_course("140-501", "A course the instructor takes", "Fall 2026", instructor=False)
        self.forbidden = fake.add_course("MATH 140", "Math  140 for INSTRUCTORS", "Fall 2026")
        # `alice` is the Gradescope student of the first enrollment (seated on Version A), `bob` of the second (Version B).
        self.alice = fake.add_member(self.course, "Alice Example", self.uins[self.enrollment], "alice@example.invalid")
        self.bob = fake.add_member(self.course, "Bob Example", self.uins[self.second], "bob@example.invalid")
        fake.add_member(self.course, "Tess Assistant", self.uins[self.enrollment], "ta@example.invalid", role=2)  # a TA is never matched, whatever their SID
        fake.add_member(self.course, "Carol Nosid", "", "carol@example.invalid")
        fake.add_container(self.course, "Common Exam 1 - Multiple Choice", "ABC", 2, bubble_sheet=True)
        self.container, self.versions = fake.add_container(self.course, "Common Exam 1 - Partial Credit", "ABC", 2)
        fake.add_assignment(self.course, "Homework 1", 2)
        self.gq = {}
        for letter, assignment in self.versions.items():
            fake.add_question(assignment, 1, "Question 18", 2, OTHER)
            fake.add_question(assignment, 2, "Question 19", 2, OTHER)
            self.gq[("q20", letter)] = fake.add_question(assignment, 3, "Question 20", 10, Q20)
            self.gq[("q21", letter)] = fake.add_question(assignment, 4, "Question 21 a and b", 8, Q21)
        directory = tempfile.mkdtemp(dir=self.directory.name)
        self.credentials = os.path.join(directory, "gradescope.json")
        self.write_credentials({"email": EMAIL, "password": PASSWORD})
        self.settings = gw.Settings(credentials_file=self.credentials, base=fake.url, pace=0)
        self.sender = GradescopeSender(self.frq, settings=self.settings)
        routed = self.app.extensions["gradescope"]  # the instance behind the Flask routes
        routed.settings = self.settings

    def write_credentials(self, value, mode=0o600):
        if os.path.exists(self.credentials):
            os.remove(self.credentials)
        descriptor = os.open(self.credentials, os.O_CREAT | os.O_WRONLY | os.O_EXCL, mode)
        os.write(descriptor, (value if isinstance(value, bytes) else json.dumps(value).encode()))
        os.close(descriptor)
        os.chmod(self.credentials, mode)

    def item(self, key, letter, number):
        """The Gradescope item that export number `number` of this question is."""
        return self.fake.rubric_items(self.gq[(key, letter)])[number - 1]["id"]

    def qsub(self, key, letter, user):
        return self.fake.qsub_of(self.versions[letter], user, self.gq[(key, letter)])

    def send(self, view=None, **extra):
        view = view or self.sender.preview(self.aid, {})
        return self.sender.send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {}, **extra})

    def units(self, kind="grades"):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM frq_gradescope_{kind} ORDER BY created_at,rowid")]

    def ready_now(self):
        """Time passes: whatever waits for its minute may go now, and a wait for a busy Gradescope is over (at the tests' own pace)."""
        with connect(self.path) as db:
            for table in ("frq_gradescope_uploads", "frq_gradescope_grades"):
                db.execute(f"UPDATE {table} SET not_before=? WHERE not_before IS NOT NULL", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
        self.sender._busy_until = None
        if self.sender._session is not None:
            self.sender._session.pace = self.settings.pace

    # The only comments Faculty may send: a grade's batch lines from Certification and Export (2026-09-27); a test that makes some
    # names them here.
    allowed_comments = ()

    def assertNothingForbidden(self):
        self.assertEqual(self.fake.forbidden_hits, [])
        self.assertEqual(self.fake.publish_hits, [])
        self.assertEqual([hit for hit in self.fake.comment_hits
                          if hit["json"]["question_submission_evaluation"]["comments"] not in self.allowed_comments], [])
        self.assertFalse(any(r["path"].startswith(f"/courses/{self.forbidden}") for r in self.fake.requests))
        self.assertFalse(any(r["path"] == "/logout" for r in self.fake.requests))  # it would sign the instructor out everywhere
        for save in self.fake.saves():
            comments = save.get("json", {}).get("question_submission_evaluation", {}).get("comments", "missing")
            self.assertTrue(comments is None or comments in self.allowed_comments or comments == "", comments)

    def assertGraded(self, key, letter, user, numbers, points=None, score=None, comment=None):
        applied, adjustment, comments, found = self.fake.state(self.qsub(key, letter, user))
        self.assertEqual(applied, sorted(self.item(key, letter, n) for n in numbers))
        self.assertEqual(adjustment, points)
        self.assertEqual(comments or None, comment)
        if score is not None:
            self.assertEqual(found, score)

    def view(self, targets=None):
        return self.sender.preview(self.aid, {"targets": targets} if targets else {})

    def assertNoUin(self, value):
        text = json.dumps(value)
        for uin in (ALICE_UIN, BOB_UIN):
            self.assertNotIn(uin, text)


class SendTests(GradescopeCase):
    def test_a_whole_send_uploads_each_paper_to_its_version_and_checks_exactly_the_final_items(self):
        view = self.view()
        self.assertTrue(view["configured"])
        section = view["sections"][0]
        self.assertEqual((section["course_id"], section["container_id"], section["proposed"], section["problem"]),
                         (str(self.course), str(self.container), True, ""))
        self.assertEqual(section["versions"], {"A": str(self.versions["A"]), "B": str(self.versions["B"])})  # never Version C
        # The forbidden course is not even offered, however its name is spaced or capitalized; the student course neither.
        self.assertEqual(sorted(c["id"] for c in view["courses"]), sorted(str(c) for c in self.fake.courses if c != self.forbidden and self.fake.courses[c]["instructor"]))
        self.assertEqual(view["summary"]["papers"], {"upload": 2, "already": 0, "skipped": 0})
        self.assertEqual(view["summary"]["grades"], {"send": 4, "update": 0, "already": 0, "refused": 0, "held": 0})
        self.assertEqual((view["refused"], view["skipped"], view["warnings"]), ([], [], []))
        # Faculty's q20 lines are Gradescope's question 3 by their export numbers ("3.x"), which prints as Question 20.
        self.assertEqual({(q["faculty"], q["gradescope"]) for q in view["questions"]},
                         {("q20", "Question 20"), ("q20-B", "Question 20"), ("q21", "Question 21 a and b"), ("q21-B", "Question 21 a and b")})
        self.assertNoUin(view)
        # Coverage across every send, from the receipts alone: nothing is on Gradescope yet.
        self.assertEqual(self.sender.status(self.aid)["coverage"], {"papers": {"total": 2, "sent": 0}, "grades": {"final": 4, "sent": 0, "changed": 0, "not_sent": 4}})
        before = len(self.fake.requests)
        receipt = self.send(view)
        self.assertEqual(receipt["status"], "pending")
        # While the send is live the panel polls every 4 s: the responses coverage counts against are read at the send's first poll
        # and not again (they take the roster lifecycle lock the sender needs), while the receipts are, so the sent counts still move
        # (review fix GRUN-F4). A basis read BEFORE the send is never reused: grades approved since would be missing (review fix, 2026-09-26).
        with patch.object(self.sender.frq, "responses", wraps=self.sender.frq.responses) as read:
            self.assertEqual(self.sender.status(self.aid)["coverage"]["grades"]["final"], 4)
            self.assertEqual(self.sender.drain(limit=2), 2)  # the two papers
            self.assertEqual(self.sender.status(self.aid)["coverage"]["papers"], {"total": 2, "sent": 2})
            self.assertEqual(self.sender.status(self.aid)["coverage"]["papers"], {"total": 2, "sent": 2})
            self.assertEqual(read.call_count, 1)
        self.assertEqual(self.sender.drain(), 4)  # then the four grades, one unit per turn
        self.assertEqual(self.sender.status(self.aid)["sends"][0]["status"], "complete")
        self.assertEqual(self.sender.status(self.aid)["coverage"], {"papers": {"total": 2, "sent": 2}, "grades": {"final": 4, "sent": 4, "changed": 0, "not_sent": 0}})
        uploads = {u["enrollment_id"]: u for u in self.units("uploads")}
        self.assertEqual({u["status"] for u in uploads.values()}, {"accepted"})
        self.assertEqual(self.merged, [2, 2])
        for enrollment, user, letter in ((self.enrollment, self.alice, "A"), (self.second, self.bob, "B")):
            found = self.fake.submission_of(self.versions[letter], user)
            self.assertEqual(found["owners"], [user])
            self.assertEqual((found["filename"], found["pages"]), (f"faculty-{uploads[enrollment]['id']}.pdf", 2))
            self.assertEqual(str(found["id"]), uploads[enrollment]["gradescope_submission_id"])
            self.assertEqual(uploads[enrollment]["pdf_sha256"], gs.sha(pdf(2)))
            self.assertIsNone(self.fake.submission_of(self.versions["C"], user))
        # Exactly the items the lines are: q21's Part A and Part B items restart their positions inside the groups.
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        self.assertGraded("q21", "A", self.alice, [2, 6], score=6.0)
        self.assertGraded("q20", "B", self.bob, [3], score=2.0)
        self.assertGraded("q21", "B", self.bob, [3, 5], score=5.0)
        grades = self.units()
        self.assertEqual({g["status"] for g in grades}, {"accepted"})
        self.assertEqual(sorted(json.loads(g["receipt_json"])["score"] for g in grades), ["2", "4", "5", "6"])
        # Every write went with the page's own tokens; the grading saves asked for JSON; nothing was published or commented.
        uploaded = self.fake.uploads()
        self.assertEqual(len(uploaded), 2)
        self.assertTrue(all(u["csrf"] and u["form"]["authenticity_token"] for u in uploaded))
        self.assertEqual({u["form"]["owner_id"] for u in uploaded}, {str(self.alice), str(self.bob)})  # Gradescope's user IDs, never a UIN
        self.assertEqual([f[1] for u in uploaded for f in u["files"]], [f"faculty-{uploads[self.enrollment]['id']}.pdf", f"faculty-{uploads[self.second]['id']}.pdf"])
        self.assertTrue(all("application/json" in s["accept"] and s["csrf"] for s in self.fake.saves()))
        self.assertNoUin([r for r in self.fake.requests[before:]])
        self.assertNothingForbidden()
        # Sent is sent: the next preview finds everything already there, and there is nothing to send.
        again = self.view()
        self.assertEqual((again["summary"]["papers"]["already"], again["summary"]["grades"]["already"]), (2, 4))
        with self.assertRaisesRegex(Conflict, "nothing to send"):
            self.send(again)

    def test_a_read_with_no_send_live_keeps_no_coverage_basis(self):
        """PERFORMANCE: the coverage basis is kept only to be reused while a send is live, so a read with none live leaves nothing resident."""
        self.assertEqual(self.sender.status(self.aid)["coverage"]["grades"]["final"], 4)
        self.assertEqual(self.sender._coverage_basis_cache, {})

    def test_a_send_names_the_sections_links_and_later_previews_reuse_them(self):
        section = self.section["id"]
        self.send()
        status = self.sender.status(self.aid)
        self.assertEqual(status["targets"], [{"section_id": section, "section": 501, "course_id": str(self.course), "course_title": "140-501",
                                              "container_id": str(self.container), "container_title": "Common Exam 1 - Partial Credit"}])
        self.sender.drain()
        # Another course at the same number now fits too: the recorded choice stands, it is not a proposal any more.
        self.fake.add_course("140-501", "Another Fall course numbered 501", "Fall 2026")
        view = self.view()
        self.assertEqual((view["sections"][0]["course_id"], view["sections"][0]["proposed"]), (str(self.course), False))

    def test_a_section_links_its_course_once_on_its_page_and_every_preview_uses_it(self):
        section, route = self.section["id"], f"/api/grading/gradescope/sections/{self.section['id']}"
        # Reading the link never contacts Gradescope.
        link = self.client.get(route).json
        self.assertEqual((link["section"], link["term_label"], link["linked"], link["configured"]), (501, "Fall 2026", None, True))
        self.assertEqual(self.fake.requests, [])
        # Change reads the list, never the coordinator's course, and proposes the one course that fits.
        listed = self.client.get(f"{route}/courses").json
        self.assertEqual(listed["proposed"], str(self.course))
        self.assertNotIn(str(self.forbidden), [c["id"] for c in listed["courses"]])
        other = self.fake.add_course("140-501", "Another Fall course numbered 501", "Fall 2026")
        for body, status in (({"course_id": str(self.forbidden), "base": ""}, 409), ({"course_id": "99999999", "base": ""}, 409),
                             ({"course_id": "x"}, 400), ({"course_id": str(other), "base": "123"}, 409)):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(route, json=body, headers=HEADERS).status_code, status)
        saved = self.client.post(route, json={"course_id": str(other), "base": ""}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json["linked"]["course_id"], str(other))
        # The preview takes the linked course as a fact (no proposal), for the written exam and the create preview alike.
        view = self.view()
        self.assertEqual((view["sections"][0]["course_id"], view["sections"][0]["linked"], view["sections"][0]["proposed"]), (str(other), True, False))
        self.assertEqual(self.sender.create_preview(self.aid, {})["sections"][0]["course_id"], str(other))
        # Changed on its page: the stale page's base is refused; the right base records a new link and the old stays retained.
        self.assertEqual(self.client.post(route, json={"course_id": str(self.course), "base": ""}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.post(route, json={"course_id": str(self.course), "base": str(other)}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.post(route, json={"course_id": str(self.course), "base": str(self.course)}, headers=HEADERS).status_code, 200)
        with connect(self.path) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT course_id FROM frq_gradescope_courses WHERE section_id=? ORDER BY created_at,rowid", (section,))],
                             [str(other), str(self.course)])
        view = self.view()
        self.assertEqual((view["sections"][0]["course_id"], view["sections"][0]["container_id"], view["sections"][0]["linked"]),
                         (str(self.course), str(self.container), True))
        # A course chosen in the dialog instead of the link is shown as a choice, not as the link.
        self.assertFalse(self.view({section: {"course_id": str(other)}})["sections"][0]["linked"])
        self.assertNothingForbidden()

    def test_without_a_usable_sign_in_file_nothing_is_contacted(self):
        directory = tempfile.mkdtemp(dir=self.directory.name)
        for name in ("", "relative/gradescope.json", os.path.join(directory, "missing.json")):
            with self.subTest(credentials=name):
                sender = GradescopeSender(self.frq, settings=gw.Settings(credentials_file=name, base=self.fake.url, pace=0))
                view = sender.preview(self.aid, {})
                self.assertEqual((view["configured"], view["fingerprint"], view["sections"]), (False, None, []))
                self.assertTrue(view["problem"])
                self.assertFalse(sender.status(self.aid)["configured"])
                with self.assertRaises(Conflict):
                    sender.send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": "0" * 64})
        with patch.dict(os.environ, {gw.CREDENTIALS_ENV: ""}):
            self.assertFalse(gw.Settings.environment().configured)  # no default path: HOME is shared with checks and timers
        self.write_credentials({"email": EMAIL, "password": PASSWORD}, mode=0o644)
        self.assertIn("0600", self.sender.status(self.aid)["problem"])
        self.assertFalse(self.sender.preview(self.aid, {})["configured"])
        self.write_credentials(b"email=instructor")  # 0600 but not the saved sign-in: refused before anything is sent
        with self.assertRaisesRegex(Conflict, "does not hold an email and a password"):
            self.sender.preview(self.aid, {})
        self.assertEqual(self.fake.requests, [])
        self.write_credentials({"email": EMAIL, "password": "not the password"})
        with self.assertRaisesRegex(Conflict, "did not accept the saved sign-in") as caught:
            self.sender.preview(self.aid, {})
        self.assertNotIn("not the password", str(caught.exception.description))
        self.assertEqual([(r["method"], r["path"], r["query"]) for r in self.fake.requests], [("GET", "/", ""), ("POST", "/login", "")])  # the password goes in the body, never the URL
        # The status route only looks at the file (it may be the right one); it never signs in to answer.
        status = self.client.get(f"/api/grading/gradescope/assignments/{self.aid}").json
        self.assertEqual((status["configured"], status["problem"]), (True, ""))
        self.assertEqual(len(self.fake.requests), 2)

    def test_the_course_faculty_never_touches_is_never_listed_read_or_linked(self):
        forbidden = str(self.forbidden)
        view = self.view({self.section["id"]: {"course_id": forbidden, "container_id": str(self.container)}})
        self.assertNotIn(forbidden, [c["id"] for c in view["courses"]])
        self.assertIn("never touches", view["sections"][0]["problem"])
        self.assertEqual(view["summary"]["papers"]["upload"], 0)
        with self.assertRaisesRegex(Conflict, "nothing to send"):
            self.send(view, targets={self.section["id"]: {"course_id": forbidden, "container_id": str(self.container)}})
        session = gw.Session(self.settings)
        try:
            session.courses()
            for read in (lambda: session.roster(forbidden), lambda: session.assignments(forbidden), lambda: session.outline(forbidden, 1),
                         lambda: session.rubric(forbidden, 1), lambda: session.submissions(forbidden, 1), lambda: session.submission(forbidden, 1, 1),
                         lambda: session.grader(forbidden, 1, 1), lambda: session.upload(forbidden, 1, self.alice, pdf(1), "x.pdf")):
                with self.assertRaisesRegex(gw.GradescopeError, "Math 140 for Instructors"):
                    read()
            with self.assertRaisesRegex(gw.GradescopeError, "not one of the instructor's courses"):
                session.roster(99999999)
        finally:
            session.close()
        # The catalog refuses it too, as a backstop.
        with connect(self.path) as db, self.assertRaisesRegex(Exception, "never touches"):
            db.execute("INSERT INTO frq_gradescope_courses VALUES('x',?,?,' Math 140 for Instructors ','workspace','now')", (self.section["id"], forbidden))
        self.assertNothingForbidden()

    def test_the_bubble_sheets_go_with_the_send_first_and_once(self):
        """GRADESCOPE "THE BUBBLE SHEETS TOO" (requirement, 2026-10-26): the dialog's Bubble sheets part is the sheets' own send, recorded after the written send passed its checks and before
        it, under a request ID made from this one; a repeated request returns both and records neither again."""
        from unittest.mock import patch
        order, calls = [], []
        recorded = self.sender._record_send

        def sheet_send(aid, data):
            calls.append(data)
            order.append("sheets")
            return {"send_id": "sheet-send", "request_id": data["request_id"], "status": "pending"}

        def record_send(*args, **kwargs):
            order.append("written")
            return recorded(*args, **kwargs)
        sheets = {"fingerprint": "f" * 64, "targets": {"s": {"course_id": "1", "container_id": "2"}}, "published": ["2"]}
        view = self.view()
        request = {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {}, "sheets": sheets}
        with patch.object(self.sender, "sheet_send", side_effect=sheet_send), patch.object(self.sender, "_record_send", side_effect=record_send):
            receipt = self.sender.send(self.aid, request)
            self.assertEqual(order, ["sheets", "written"])
            [asked] = calls
            self.assertEqual((asked["fingerprint"], asked["targets"], asked["published"], asked["confirmation"]), (sheets["fingerprint"], sheets["targets"], ["2"], "send"))
            self.assertNotEqual(asked["request_id"], request["request_id"])
            self.assertEqual(asked["request_id"], str(uuid.uuid5(uuid.NAMESPACE_URL, f"faculty-sheets:{request['request_id']}")))
            self.assertEqual(receipt["sheets"]["send_id"], "sheet-send")
            # The same request again: the written send already recorded, and the sheets' send asked with the same ID (its own record answers).
            again = self.sender.send(self.aid, request)
            self.assertEqual((again["send_id"], again["sheets"]["request_id"]), (receipt["send_id"], asked["request_id"]))
            self.assertEqual(order, ["sheets", "written", "sheets"])
        # A written send refused by its own checks records no bubble sheets either.
        with patch.object(self.sender, "sheet_send", side_effect=sheet_send):
            with self.assertRaises(Conflict):
                self.sender.send(self.aid, {**request, "request_id": str(uuid.uuid4()), "fingerprint": "0" * 64})
        self.assertEqual(len(calls), 2)
        # A send without the part asks nothing of the sheets, and its request hashes as before.
        with patch.object(self.sender, "sheet_send", side_effect=AssertionError("no sheets asked")):
            self.sender.send(self.aid, {k: v for k, v in request.items() if k != "sheets"})

    def test_an_assignment_of_another_kind_is_never_proposed_for_its_number_alone(self):
        """GRADESCOPE "THE SAME KIND AND NUMBER" (requirement, 2026-10-26): before Exam 2 was on Gradescope, its send proposed "Quiz 2",
        the one assignment with a 2 in it, and every paper was skipped against Quiz 2's two-page template."""
        from grading.gradescope_sync import fits_title
        self.assertTrue(fits_title("Exam 2", "Common Exam 2 - Partial Credit"))
        self.assertTrue(fits_title("Test 1", "Common Exam 1"))
        self.assertTrue(fits_title("Quiz 2", "Quiz 2"))
        self.assertTrue(fits_title("Exam 2", "Week 2 problems"))  # a title naming no kind: its number decides, as before
        self.assertFalse(fits_title("Exam 2", "Quiz 2"))
        self.assertFalse(fits_title("Quiz 1", "Common Exam 1 - Partial Credit"))
        self.assertFalse(fits_title("Exam 2", "Common Exam 1"))
        # In the send: with the exam gone, a quiz of the same number and versions is not proposed.
        section = self.section["id"]
        with connect(self.path) as db:
            title = self.frq.plan(db, self.aid)["title"]
        self.fake.assignments[self.container]["title"] = "Something else"
        number = re.findall(r"\d+", title)[0]
        quiz, _ = self.fake.add_container(self.course, f"Quiz {number}", "AB", 2)
        view = self.view({section: {"course_id": str(self.course)}})
        self.assertEqual((view["sections"][0]["container_id"], view["sections"][0]["proposed"]), ("", False))
        self.assertEqual(view["sections"][0]["problem"], "Choose the Gradescope assignment this goes to in this course.")
        # Chosen by the instructor, it is theirs to choose.
        view = self.view({section: {"course_id": str(self.course), "container_id": str(quiz)}})
        self.assertEqual(view["sections"][0]["container_id"], str(quiz))

    def test_course_and_exam_are_proposed_only_when_exactly_one_fits(self):
        section = self.section["id"]
        self.fake.add_course("140-501", "A second Fall 2026 course at 501", "Fall 2026")
        view = self.view()
        self.assertEqual((view["sections"][0]["course_id"], view["sections"][0]["problem"]), ("", "Choose this section's Gradescope course."))
        self.assertEqual(view["summary"]["papers"], {"upload": 0, "already": 0, "skipped": 2})
        self.assertTrue(all(s["reason"] == "Section 501 has no Gradescope assignment chosen." for s in view["skipped"]))
        self.assertEqual(view["summary"]["grades"]["held"], 4)
        # The instructor's choice: then the exam is proposed, the only non-bubble exam with Versions A and B.
        view = self.view({section: {"course_id": str(self.course)}})
        self.assertEqual((view["sections"][0]["container_id"], view["sections"][0]["proposed"]), (str(self.container), True))
        self.assertEqual({c["title"] for c in view["sections"][0]["containers"]}, {"Common Exam 1 - Partial Credit", "Homework 1"})
        # A second exam with both versions: nothing is guessed.
        other, _ = self.fake.add_container(self.course, "Makeup Exam 1", "AB", 2)
        view = self.view({section: {"course_id": str(self.course)}})
        self.assertEqual((view["sections"][0]["container_id"], view["sections"][0]["problem"]), ("", "Choose the Gradescope assignment this goes to in this course."))
        view = self.view({section: {"course_id": str(self.course), "container_id": str(other)}})
        self.assertEqual(view["sections"][0]["container"], "Makeup Exam 1")
        # An exam without one of Faculty's versions says so; its papers of that version wait.
        lacking, _ = self.fake.add_container(self.course, "Only A", "A", 2)
        view = self.view({section: {"course_id": str(self.course), "container_id": str(lacking)}})
        self.assertEqual(view["sections"][0]["problem"], "Only A has no version B.")
        self.assertEqual(view["sections"][0]["missing_versions"], ["B"])  # what Create the assignment on Gradescope can add to the set
        self.assertEqual([s["reason"] for s in view["skipped"]], ["The Gradescope assignment has no version B."])
        # A version of the plan that no paper of the section is in is no problem there (2026-10-06: a makeup's Version E, which one
        # section's student sat, warned on every section).
        with patch.object(self.frq, "paper_version", return_value={"status": "confirmed", "version": "A"}):
            view = self.view({section: {"course_id": str(self.course), "container_id": str(lacking)}})
        self.assertEqual((view["sections"][0]["problem"], view["skipped"]), ("", []))
        self.assertNotIn("missing_versions", view["sections"][0])

    def test_students_are_matched_by_uin_only_and_never_guessed(self):
        bob = next(m for m in self.fake.members[self.course] if m["user_id"] == self.bob)
        bob["sid"] = ""
        view = self.view()
        self.assertEqual(view["skipped"], [{"name": view["skipped"][0]["name"], "section": 501,
                                            "reason": "No student on the Gradescope roster of 140-501 has this student's UIN."}])
        self.assertTrue(view["skipped"][0]["name"])
        self.assertEqual((view["summary"]["papers"]["upload"], view["summary"]["grades"]["send"], view["summary"]["grades"]["held"]), (1, 2, 2))
        self.assertNoUin(view)
        self.send(view)
        self.sender.drain()
        self.assertIsNone(self.fake.submission_of(self.versions["B"], self.bob))
        self.assertIsNotNone(self.fake.submission_of(self.versions["A"], self.alice))
        # Two students with Alice's UIN: neither is chosen.
        bob["sid"] = self.uins[self.second]
        self.fake.add_member(self.course, "Alice Twin", self.uins[self.enrollment], "twin@example.invalid")
        view = self.view()
        self.assertEqual([s["reason"] for s in view["skipped"]], ["More than one student on the Gradescope roster of 140-501 has this student's UIN."])
        self.assertEqual(view["summary"]["papers"]["upload"], 1)  # Bob now
        self.assertNoUin(view)
        self.assertNoUin(self.sender.status(self.aid))

    def test_a_paper_whose_page_count_differs_from_the_template_is_not_sent(self):
        self.fake.assignments[self.versions["B"]]["pages"] = 3
        view = self.view()
        self.assertEqual([s["reason"] for s in view["skipped"]], ["The reviewed paper has 2 pages; the Gradescope assignment's template has 3."])
        self.assertEqual((view["summary"]["papers"]["upload"], view["summary"]["grades"]["held"]), (1, 2))

    def test_a_paper_whose_extra_sides_are_empty_is_sent_without_them(self):
        """EMPTY BACKS (2026-10-17, requirement: "I need this fixed"): a paper longer than the template only by sides that carry no
        writing is uploaded as the template's pages; one with writing past them still waits."""
        self.fake.assignments[self.versions["B"]]["pages"] = 1
        with patch.object(self.frq, "_empty_side", return_value=False):
            self.assertEqual([s["reason"] for s in self.view()["skipped"]], ["The reviewed paper has 2 pages; the Gradescope assignment's template has 1."])
        with patch.object(self.frq, "_empty_side", return_value=True):
            view = self.view()
            self.assertEqual((view["skipped"], view["summary"]["papers"]["upload"]), ([], 2))
            before = len(self.rotations)
            self.send(view)
            self.sender.drain()
        uploads = {u["enrollment_id"]: u for u in self.units("uploads")}
        self.assertEqual({u["status"] for u in uploads.values()}, {"accepted"})
        self.assertEqual(sorted(len(r) for r in self.rotations[before:]), [1, 2])  # Bob's paper went as its one page

    def test_a_paper_faculty_did_not_upload_and_its_grades_are_left_alone(self):
        theirs = self.fake.add_submission(self.versions["A"], [self.alice], "Alice-scanned-by-a-TA.pdf")
        graded = self.qsub("q20", "A", self.alice)
        self.fake.grade(graded["id"], applied=[self.item("q20", "A", 1)], comments="TA: nice")
        view = self.view()
        self.assertEqual([s["reason"] for s in view["skipped"]],
                         ["Gradescope already has a paper for this student that Faculty did not upload; it and its grades are left alone."])
        self.assertEqual((view["summary"]["papers"], view["summary"]["grades"]["held"]), ({"upload": 1, "already": 0, "skipped": 1}, 2))
        self.send(view)
        self.sender.drain()
        self.assertEqual([s["id"] for s in self.fake.submissions.values() if self.alice in s["owners"]], [theirs])
        self.assertEqual(self.fake.state(graded), ([self.item("q20", "A", 1)], None, "TA: nice", 10.0))
        self.assertFalse(any(f"/submissions/{q}/" in r["path"] for q in self.fake.submissions[theirs]["questions"].values() for r in self.fake.saves()))
        # A paper in ANOTHER version of the exam counts too (Gradescope's student list is exam-wide): Bob's Version C paper.
        self.fake.add_submission(self.versions["C"], [self.bob])
        view = self.view()
        self.assertEqual(view["summary"]["papers"], {"upload": 0, "already": 1, "skipped": 1})
        self.assertEqual(view["summary"]["grades"]["already"], 2)

    def test_points_or_wording_that_differ_refuse_that_question(self):
        rows = next(i for i in self.fake.items.values() if i["id"] == self.item("q21", "A", 3))
        rows["weight"] = 1.5  # Part A's second item: 1 in Faculty
        wording = next(i for i in self.fake.items.values() if i["id"] == self.item("q20", "B", 1))
        wording["description"] = "Correct answer x = 6, work shown"
        self.fake.add_question(self.versions["B"], 5, "Question 22", 2, OTHER)
        view = self.view()
        # Equal points alone cannot tell a reordered or inserted item from the right one (review, 2026-09-25): an item worded
        # neither as Faculty imported it nor as Faculty now words it refuses its question too.
        self.assertEqual(sorted((r["question"], r["reason"]) for r in view["refused"]), sorted([
            ("q21", "R4_3 is worth 1 in Faculty and 1.5 on Gradescope."),
            ("q20-B", "R3_1 is “Correct answer x = 6, work shown” on Gradescope, which is neither its imported nor its current wording.")]))
        self.assertEqual(view["summary"]["grades"], {"send": 2, "update": 0, "already": 0, "refused": 2, "held": 0})
        self.assertEqual(view["warnings"], [])
        q21 = next(q for q in view["questions"] if q["faculty"] == "q21")
        self.assertEqual((q21["refused"], q21["reasons"]), (1, [{"reason": "R4_3 is worth 1 in Faculty and 1.5 on Gradescope.", "count": 1}]))
        self.send(view)
        self.sender.drain()
        self.assertEqual(self.fake.state(self.qsub("q21", "A", self.alice))[0], [])  # never written
        self.assertEqual(self.fake.state(self.qsub("q20", "B", self.bob))[0], [])  # never written either
        # The question itself must be the same question, worth the same.
        self.fake.questions[self.gq[("q20", "B")]]["title"] = "Question 19"
        self.fake.questions[self.gq[("q21", "B")]]["weight"] = 9
        reasons = {r["question"]: r["reason"] for r in self.view()["refused"]}
        self.assertEqual(reasons["q20-B"], "Gradescope's question 3 is “Question 19”, not question 20.")
        self.assertEqual(reasons["q21-B"], "It is worth 8 in Faculty and 9 on Gradescope.")

    def test_a_grade_is_sent_only_when_gradescope_would_give_exactly_faculty_s_score(self):
        self.decide("q20", "A", ["R3_1", "R3_2"])  # 10 + 4, which Faculty's ceiling makes 10
        self.fake.questions[self.gq[("q20", "A")]]["ceiling"] = False
        view = self.view()
        self.assertEqual([r["reason"] for r in view["refused"]], ["Gradescope would give 14 for these items; Faculty's grade is 10."])
        # No line checked at all looks exactly like an ungraded question on Gradescope.
        self.decide("q20", "B", [])
        refused = {r["question"]: r["reason"] for r in self.view()["refused"]}
        self.assertEqual(refused["q20-B"], "No rubric line is checked; on Gradescope that looks exactly like an ungraded question.")

    def test_pages_grading_found_upside_down_are_uploaded_upright(self):
        """(2026-09-27: "pages got uploaded to gradescope upside down if the original scan had them upside down".) Every crop cut
        from the first student's page 1 was cut from the page turned over, so that page goes turned a half turn; the other pages,
        and every page of the second student, go as scanned, and the receipt names the page turned."""
        original = self.frq.step

        def step(db, task, stage):
            found = original(db, task, stage)
            if stage != "extracting":
                return found
            source = json.loads(task["source_json"])
            with connect(self.path) as catalog:
                who = catalog.execute("SELECT enrollment_id FROM frq_responses WHERE id=?", (task["response_id"],)).fetchone()[0]
            turned = [who == self.enrollment and int(region["page_number"]) == 1 for region in source["regions"]]
            return {**found, "crops": [{**crop, "metadata": {**(crop.get("metadata") or {}), "orientation": {"rule": "upside-down-v1", "turned": flag}}}
                                       for crop, flag in zip(found["crops"], turned)]}
        with patch.object(self.frq, "step", side_effect=step):
            self.send(self.view())
            self.sender.drain()
        uploads = {u["enrollment_id"]: u for u in self.units("uploads")}
        self.assertEqual({k: u["status"] for k, u in uploads.items()}, {self.enrollment: "accepted", self.second: "accepted"})
        self.assertEqual(json.loads(uploads[self.enrollment]["receipt_json"]).get("turned"), [1])
        self.assertNotIn("turned", json.loads(uploads[self.second]["receipt_json"]))
        turned = [r for r in self.rotations if any(r)]
        self.assertEqual(len(turned), 1)
        self.assertEqual(turned[0][0], 180)
        self.assertTrue(all(rotation == 0 for rotation in turned[0][1:]))
        self.assertNothingForbidden()

    def test_adjustments_go_as_points_and_no_comment_is_ever_sent(self):
        note = "Partial credit for checking the answer, a private note"
        self.decide("q20", "A", ["R3_2"], status="manual", manual_score=5.5, comment=note)
        self.decide("q20", "B", [], status="manual", manual_score=3, comment="Only an adjustment")
        view = self.view()
        self.assertEqual(view["refused"], [])
        self.send(view)
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], points=1.5, score=5.5)
        self.assertGraded("q20", "B", self.bob, [], points=3.0, score=3.0)
        payloads = [s["json"] for s in self.fake.saves()]
        self.assertEqual(len(payloads), 4)
        self.assertEqual(sorted(p["question_submission_evaluation"]["points"] for p in payloads if p["question_submission_evaluation"]["points"] is not None), [1.5, 3.0])
        self.assertTrue(all(p["question_submission_evaluation"]["comments"] is None for p in payloads))
        self.assertNotIn(note, json.dumps(self.fake.requests))
        # Every rubric item of the question is set, on or off, exactly as the grading page itself saves.
        for payload in payloads:
            self.assertIn(len(payload["rubric_items"]), (len(Q20), len(flatten(Q21))))
        self.assertNothingForbidden()

    def test_a_changed_preview_is_refused_and_request_ids_are_idempotent(self):
        view = self.view()
        self.decide("q20", "A", ["R3_3"])  # Faculty changed
        with self.assertRaisesRegex(Conflict, "Something changed"):
            self.send(view)
        view = self.view()
        self.fake.add_submission(self.versions["B"], [self.bob])  # Gradescope changed
        with self.assertRaisesRegex(Conflict, "Something changed"):
            self.send(view)
        view = self.view()
        next(i for i in self.fake.items.values() if i["id"] == self.item("q20", "A", 4))["weight"] = 1  # a rubric point changed
        with self.assertRaisesRegex(Conflict, "Something changed"):
            self.send(view)
        view = self.view()
        data = {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {}}
        first = self.sender.send(self.aid, data)
        seen = len(self.fake.requests)
        self.assertEqual(self.sender.send(self.aid, dict(data)), first)
        self.assertEqual(len(self.fake.requests), seen)  # a repeated request is answered from its receipt, without Gradescope
        with self.assertRaisesRegex(Conflict, "already used for a different send"):
            self.sender.send(self.aid, {**data, "targets": {self.section["id"]: {"course_id": str(self.course)}}})
        with self.assertRaisesRegex(Conflict, "already used for a different send"):
            self.sender.send(self.aid, {**data, "fingerprint": "f" * 64})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends").fetchone()[0], 1)
        for bad, message in (({"request_id": "not-a-uuid"}, "request ID"), ({"confirmation": "yes"}, "Confirm the send"), ({"fingerprint": "abc"}, "fingerprint")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                self.sender.send(self.aid, {**data, "request_id": str(uuid.uuid4()), **bad})
        self.sender.drain()
        self.assertEqual(self.sender.send(self.aid, dict(data))["status"], "complete")

    def test_only_one_send_runs_for_a_plan_at_a_time(self):
        first = self.send()
        view = self.view()
        self.assertEqual(view["running"], first["send_id"])
        with self.assertRaisesRegex(Conflict, "still running"):
            self.send(view)
        other = GradescopeSender(self.frq, settings=self.settings)
        with self.sender.lock(), self.assertRaisesRegex(Conflict, "Another Gradescope sender"):
            other.drain()
        self.assertEqual(self.fake.uploads(), [])

    def test_papers_only_uploads_every_paper_and_sets_no_grade_until_papers_and_grades_are_sent(self):
        """(asked: the papers go too)."""
        both = self.view()
        before = len(self.fake.requests)
        view = self.sender.preview(self.aid, {"papers_only": True})
        self.assertTrue(view["papers_only"])
        self.assertFalse(both["papers_only"])
        self.assertNotEqual(view["fingerprint"], both["fingerprint"])  # the choice is part of what was approved
        self.assertEqual(view["summary"]["papers"], {"upload": 2, "already": 0, "skipped": 0})
        self.assertEqual(view["summary"]["grades"], {"send": 0, "update": 0, "already": 0, "refused": 0, "held": 0})
        self.assertEqual((view["summary"]["final"], view["questions"], view["refused"], view["warnings"]), (4, [], [], []))
        self.assertFalse(any(r["path"].endswith("/rubric/edit") for r in self.fake.requests[before:]))  # no grade, so no rubric is read
        self.assertNoUin(view)
        # A preview names its mode: the other mode's fingerprint, or a send that does not say the same, is refused.
        with self.assertRaisesRegex(Conflict, "Something changed"):
            self.send(view)
        with self.assertRaisesRegex(Conflict, "Something changed"):
            self.send(both, papers_only=True)
        for bad in ("yes", 1, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "papers_only"):
                self.sender.preview(self.aid, {"papers_only": bad})
        data = {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {}, "papers_only": True}
        receipt = self.sender.send(self.aid, data)
        self.assertTrue(receipt["plan"]["papers_only"])
        self.assertEqual(self.sender.send(self.aid, dict(data))["send_id"], receipt["send_id"])  # the same request, answered from its receipt
        with self.assertRaisesRegex(Conflict, "already used for a different send"):
            self.sender.send(self.aid, {**data, "papers_only": False})
        self.assertEqual((len(self.units("uploads")), self.units()), (2, []))
        with connect(self.path) as db:
            event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='gradescope_send'").fetchone()[0])
        self.assertEqual((event["uploads"], event["grades"], event["papers_only"]), (2, 0, True))
        self.assertEqual(self.sender.drain(), 2)
        status = self.sender.status(self.aid)
        self.assertEqual(status["sends"][0]["status"], "complete")
        self.assertTrue(status["sends"][0]["plan"]["papers_only"])
        self.assertEqual(status["coverage"], {"papers": {"total": 2, "sent": 2}, "grades": {"final": 4, "sent": 0, "changed": 0, "not_sent": 4}})
        self.assertEqual(len(self.fake.uploads()), 2)
        self.assertEqual(self.fake.saves(), [])  # nothing was graded on Gradescope
        for key, letter, user in (("q20", "A", self.alice), ("q21", "A", self.alice), ("q20", "B", self.bob), ("q21", "B", self.bob)):
            self.assertGraded(key, letter, user, [])
        # Papers only again: every paper is already there, so there is nothing to send.
        again = self.sender.preview(self.aid, {"papers_only": True})
        self.assertEqual(again["summary"]["papers"], {"upload": 0, "already": 2, "skipped": 0})
        with self.assertRaisesRegex(Conflict, "nothing to send"):
            self.send(again, papers_only=True)
        # Then papers and grades: the papers Faculty uploaded carry the grades, and nothing is uploaded twice.
        later = self.view()
        self.assertEqual(later["summary"]["papers"], {"upload": 0, "already": 2, "skipped": 0})
        self.assertEqual(later["summary"]["grades"], {"send": 4, "update": 0, "already": 0, "refused": 0, "held": 0})
        self.assertFalse(self.send(later)["plan"].get("papers_only", False))
        self.assertEqual(self.sender.drain(), 4)
        self.assertEqual(len(self.fake.uploads()), 2)
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        self.assertGraded("q21", "B", self.bob, [3, 5], score=5.0)
        self.assertEqual(self.sender.status(self.aid)["coverage"]["grades"], {"final": 4, "sent": 4, "changed": 0, "not_sent": 0})
        self.assertNothingForbidden()

    def test_export_counts_the_same_final_grades_after_the_shared_rule(self):
        content, _, _ = self.frq.export(self.aid)
        exported = json.loads(content)
        records = {(r["question_key"], r["enrollment_id"]): r for r in exported["records"]}
        self.assertEqual(exported["skipped"], [])
        expected = {("q20", self.enrollment): (4, ["R3_2"], "A"), ("q21", self.enrollment): (6, ["R4_2", "R4_6"], "A"),
                    ("q20-B", self.second): (2, ["R3_3"], "B"), ("q21-B", self.second): (5, ["R4_3", "R4_5"], "B")}
        self.assertEqual({k: (r["final"]["score"]["score"], r["final"]["score"]["selected"], r["exam_version"]) for k, r in records.items()}, expected)
        self.assertEqual({r["final"]["status"] for r in records.values()}, {"approved"})
        self.assertEqual(sorted(records[("q20", self.enrollment)]["expected_questions"]), ["q20", "q21"])
        self.assertEqual(sorted(records[("q21-B", self.second)]["expected_questions"]), ["q20-B", "q21-B"])
        for record in records.values():
            self.assertIsNotNone(record["final"]["feedback"])  # the review's retained feedback row, as before the shared rule
            self.assertTrue(record["steps"] and record["crops"] and record["source"]["assembly_revision_id"])
        with self.rosters.lock(), connect(self.path) as db:
            _, decisions, skipped = self.frq.final_decisions(db, self.aid)
            one = self.frq.final_decisions(db, self.aid, records[("q20", self.enrollment)]["response_id"])[1]
        self.assertEqual(sorted(d["row"]["id"] for d in decisions), sorted(r["response_id"] for r in records.values()))
        self.assertEqual(([d["row"]["id"] for d in one], skipped), ([records[("q20", self.enrollment)]["response_id"]], []))
        # A draft is not final, for the export and the sender alike.
        self.decide("q21", "B", ["R4_3"], status="draft")
        content, _, _ = self.frq.export(self.aid)
        self.assertEqual(len(json.loads(content)["records"]), 3)
        self.assertEqual(self.view()["summary"]["final"], 3)


class WriteTests(GradescopeCase):
    """Every write is read back; an unknown outcome is settled by reading, never by sending again."""

    def test_an_upload_answered_302_is_not_trusted_and_waits_for_an_explicit_retry(self):
        self.fake.fail("POST", r"/submissions$", ("status", 302), when=lambda: True)  # the first upload is answered, not kept
        receipt = self.send()
        self.sender.drain()
        uploads = self.units("uploads")
        self.assertEqual([u["status"] for u in uploads], ["failed", "accepted"])
        self.assertIn("did not keep the paper (it answered 302)", uploads[0]["error"])
        failed = [g for g in self.units() if g["upload_id"] == uploads[0]["id"]]
        self.assertEqual({(g["status"], g["error"]) for g in failed}, {("failed", "Its paper was not uploaded.")})
        self.assertEqual(self.sender.status(self.aid)["sends"][0]["status"], "complete")
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(len(self.fake.uploads()), 2)  # nothing is sent again on its own
        problems = self.sender.problems(self.aid, receipt["send_id"])["problems"]
        self.assertEqual(len(problems), 3)
        self.assertTrue(all(p["name"] and p["status"] == "failed" for p in problems))
        self.assertNoUin(problems)
        self.sender.retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual(len(self.fake.uploads()), 3)
        self.assertGraded("q21", "A", self.alice, [2, 6], score=6.0)
        with self.assertRaisesRegex(Conflict, "Nothing in this send failed"):
            self.sender.retry(self.aid, receipt["send_id"])

    def test_a_grade_answered_404_counts_only_as_gradescope_shows_it(self):
        self.fake.fail("POST", r"/save_grade$", ("status_after", 404))  # kept, but answered 404 (as with an HTML Accept)
        self.fake.fail("POST", r"/save_grade$", ("status", 404))  # answered 404 and NOT kept
        receipt = self.send()
        self.sender.drain()
        grades = self.units()
        self.assertEqual([g["status"] for g in grades], ["accepted", "failed", "accepted", "accepted"])
        self.assertEqual(grades[1]["error"], "Gradescope did not keep the grade. Retry to send it again.")
        self.assertEqual(len(self.fake.saves()), 4)
        self.sender.retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual(len(self.fake.saves()), 5)

    def test_a_dropped_connection_after_the_upload_is_settled_by_reading_never_by_sending_again(self):
        self.fake.fail("POST", r"/submissions$", "drop_after")
        self.fake.fail("GET", r"/submissions$", ("status", 500), when=self.fake.after_write())  # and the first check cannot read either
        self.send()
        self.assertTrue(self.sender.process_one())
        upload = self.units("uploads")[0]
        self.assertEqual(upload["status"], "uncertain")
        self.assertIsNotNone(upload["pdf_sha256"])  # what left was recorded before it left
        self.sender.drain()
        upload = self.units("uploads")[0]
        self.assertEqual((upload["status"], upload["attempts"]), ("accepted", 1))
        self.assertEqual(len(self.fake.uploads()), 2)  # one per student, never a second for the uncertain one
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})

    def test_an_upload_that_never_landed_fails_and_is_sent_again_only_on_retry(self):
        self.fake.fail("POST", r"/submissions$", "drop_before")
        receipt = self.send()
        self.sender.drain()
        self.assertEqual([u["status"] for u in self.units("uploads")], ["failed", "accepted"])
        self.assertEqual(self.sender.drain(), 0)
        self.sender.retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        # Uncertain AND not there: read, found missing, failed; still nothing sent on its own.
        self.fake.fail("POST", r"/save_grade$", "drop_before")
        self.fake.fail("GET", r"/grade$", ("status", 500), when=lambda: len(self.fake.saves()) > 4)
        self.decide("q20", "A", ["R3_3"])
        receipt = self.send()
        self.assertTrue(self.sender.process_one())
        self.assertEqual(self.units()[-1]["status"], "uncertain")
        self.sender.drain()
        grade = self.units()[-1]
        self.assertEqual((grade["status"], grade["error"]), ("failed", "Gradescope still shows Faculty's earlier grade; the new one did not land. Retry to send it again."))
        self.assertEqual(len(self.fake.saves()), 5)
        self.sender.retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [3], score=2.0)

    def test_a_dropped_grade_that_landed_is_accepted_from_what_gradescope_shows(self):
        self.fake.fail("POST", r"/save_grade$", "drop_after")
        self.fake.fail("GET", r"/grade$", ("status", 500), when=self.fake.after_write("grade"))
        self.send()
        for _ in range(3):
            self.sender.process_one()
        self.assertEqual(self.units()[0]["status"], "uncertain")
        self.sender.drain()
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual(len(self.fake.saves()), 4)

    def test_a_process_that_stopped_mid_send_is_settled_by_reading(self):
        self.send()
        with connect(self.path) as db:
            db.execute("UPDATE frq_gradescope_uploads SET status='sending',attempts=1")
        upload = {u["enrollment_id"]: u for u in self.units("uploads")}
        # Bob's upload had landed before the process stopped; Alice's had not.
        self.fake._submit(self.versions["B"], [self.bob], f"faculty-{upload[self.second]['id']}.pdf", 2, self.fake.email, ready=0)
        self.sender.drain()
        found = {u["enrollment_id"]: u for u in self.units("uploads")}
        self.assertEqual((found[self.enrollment]["status"], found[self.second]["status"]), ("failed", "accepted"))
        self.assertEqual(self.fake.uploads(), [])  # neither was sent again
        self.assertEqual(found[self.enrollment]["error"], "Faculty's paper is not on Gradescope; it did not land. Retry to send it again.")

    def test_someone_else_s_grade_is_never_overwritten(self):
        self.send()
        self.sender.process_one()
        self.sender.process_one()  # both papers are up
        self.fake.grade(self.qsub("q20", "A", self.alice)["id"], applied=[self.item("q20", "A", 1)])
        self.fake.grade(self.qsub("q21", "B", self.bob)["id"], comments="Seen by the instructor on Gradescope")
        self.sender.drain()
        found = {g["response_id"]: g for g in self.units()}
        present = sorted(g["error"] for g in found.values() if g["status"] == "present")
        self.assertEqual(present, ["Someone graded this question on Gradescope; Faculty never overwrites it."] * 2)
        self.assertEqual(self.fake.state(self.qsub("q20", "A", self.alice)), ([self.item("q20", "A", 1)], None, None, 10.0))
        self.assertEqual(self.fake.state(self.qsub("q21", "B", self.bob))[2], "Seen by the instructor on Gradescope")
        self.assertEqual(len(self.fake.saves()), 2)

    def test_faculty_replaces_its_own_earlier_grade_only_while_gradescope_still_shows_it(self):
        self.send()
        self.sender.drain()
        self.decide("q20", "A", ["R3_3"])
        # A grade changed since it went is neither on Gradescope nor unsent: the coverage says so, across both sends.
        self.assertEqual(self.sender.status(self.aid)["coverage"]["grades"], {"final": 4, "sent": 3, "changed": 1, "not_sent": 0})
        view = self.view()
        self.assertEqual(view["summary"]["grades"], {"send": 0, "update": 1, "already": 3, "refused": 0, "held": 0})
        self.assertEqual(view["summary"]["papers"], {"upload": 0, "already": 2, "skipped": 0})
        self.send(view)
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [3], score=2.0)
        latest = self.units()[-1]
        self.assertEqual((latest["status"], bool(latest["replaces_id"])), ("accepted", True))
        self.assertEqual(self.sender.status(self.aid)["coverage"]["grades"], {"final": 4, "sent": 4, "changed": 0, "not_sent": 0})
        self.decide("q20", "A", ["R3_4"])
        self.send()
        self.fake.grade(self.qsub("q20", "A", self.alice)["id"], applied=[self.item("q20", "A", 1)])  # the instructor graded it there meanwhile
        self.sender.drain()
        self.assertEqual(self.units()[-1]["status"], "present")
        self.assertEqual(self.fake.state(self.qsub("q20", "A", self.alice))[0], [self.item("q20", "A", 1)])

    def test_a_grade_that_is_no_longer_final_when_its_turn_comes_is_refused(self):
        self.send()
        self.decide("q20", "A", ["R3_3"])  # a new decision after the approval
        self.decide("q21", "B", ["R4_3"], status="draft")  # not final at all
        self.sender.drain()
        self.assertEqual(sorted(g["error"] for g in self.units() if g["status"] == "refused"),
                         ["Faculty's grade changed after the send was approved; preview and send again.", "It is no longer a final grade."])
        self.assertEqual(self.fake.state(self.qsub("q20", "A", self.alice))[0], [])
        self.assertEqual(self.fake.state(self.qsub("q21", "B", self.bob))[0], [])
        self.assertEqual(len(self.fake.saves()), 2)

    def test_question_submissions_not_made_yet_wait_and_complete_later(self):
        self.fake.processing_reads = 4
        self.send()
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        grades = self.units()
        self.assertEqual({g["status"] for g in grades}, {"pending"})
        self.assertTrue(all(g["not_before"] and ("has not made this question's submission yet" in g["error"] or "has not finished processing" in g["error"]) for g in grades))
        # Seconds, not a minute: Gradescope processes a paper in 5 to 11 s (PROCESSING_WAIT).
        self.assertTrue(all(datetime.fromisoformat(g["not_before"]) < datetime.now(timezone.utc) + timedelta(seconds=gs.PROCESSING_WAIT + 1) for g in grades))
        status = self.sender.status(self.aid)["sends"][0]
        self.assertEqual((status["status"], status["counts"]["grades"]), ("running", {"pending": 4}))
        self.assertIn("Waiting to try again", status["waiting"])
        self.assertEqual(self.sender.drain(), 0)  # nothing is ready before its minute
        self.ready_now()
        self.sender.drain()
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual(self.sender.status(self.aid)["sends"][0]["status"], "complete")

    def test_cancel_stops_what_waits_and_keeps_what_went(self):
        receipt = self.send()
        self.sender.process_one()  # the first paper is up
        cancelled = self.sender.cancel(self.aid, receipt["send_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(sorted(u["status"] for u in self.units("uploads")), ["accepted", "cancelled"])
        self.assertEqual({g["status"] for g in self.units()}, {"cancelled"})
        self.assertEqual((len(self.fake.uploads()), len(self.fake.saves())), (1, 0))
        with self.assertRaisesRegex(Conflict, "not retried"):
            self.sender.retry(self.aid, receipt["send_id"])
        view = self.view()
        self.assertEqual((view["summary"]["papers"], view["summary"]["grades"]["send"]), ({"upload": 1, "already": 1, "skipped": 0}, 4))
        self.send(view)
        self.sender.drain()
        self.assertEqual({g["status"] for g in self.units() if g["send_id"] != receipt["send_id"]}, {"accepted"})
        self.assertEqual(len(self.fake.uploads()), 2)

    def test_the_card_dates_only_a_send_that_put_something_on_gradescope(self):
        """Review fix GR-7: a cancelled send, or one where nothing was accepted, is the last send but never "sent"."""
        from grading.service import gradescope_brief
        def brief():
            rows = self.sender.frq.responses(self.aid)
            with connect(self.sender.path) as db:
                return gradescope_brief(db, self.aid, rows)
        self.assertIsNone(brief())
        first = self.send()
        self.sender.cancel(self.aid, first["send_id"])  # before anything went
        self.assertEqual((brief()["sent_at"], brief()["grades"]["sent"]), (None, 0))
        self.assertIsNotNone(brief()["last_send_at"])
        self.send(self.view())
        self.sender.drain()
        with connect(self.sender.path) as db:
            latest = db.execute("SELECT created_at FROM frq_gradescope_sends WHERE assignment_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (self.aid,)).fetchone()[0]
        self.assertEqual((brief()["sent_at"], brief()["last_send_at"], brief()["grades"]["sent"]), (latest, latest, 4))

    def test_an_expired_session_signs_in_again(self):
        self.send()
        self.sender.process_one()
        logins = sum(1 for r in self.fake.requests if r["path"] == "/login")
        self.fake.expire_sessions()
        self.fake.fail("POST", r"/save_grade$", "expire")  # and once more exactly at a write: that write waits for its minute
        self.sender.drain()
        self.assertGreater(sum(1 for r in self.fake.requests if r["path"] == "/login"), logins)
        waiting = [g for g in self.units() if g["status"] == "pending"]
        self.assertEqual(len(waiting), 1)
        self.assertIn("signed Faculty out", waiting[0]["error"])
        self.ready_now()
        self.sender.drain()
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual(len(self.fake.uploads()), 2)

    def test_a_published_exam_is_sent_to_only_with_its_confirmation(self):
        # Requirement, 2026-09-26: a published exam is a warning and a confirmation, no longer a refusal.
        self.fake.assignments[self.container]["published"] = True
        view = self.view()
        self.assertNotIn("unpublished_version", view)  # GRADESCOPE: only a late paper's send carries this note.
        section = view["sections"][0]
        self.assertEqual((section["problem"], section["published"]), ("", True))
        self.assertEqual(section["warning"], gs.published_warning("Common Exam 1 - Partial Credit"))
        self.assertIn("students see each paper and grade as soon as Faculty writes it", section["warning"])
        self.assertEqual(view["published"], [{"section_id": self.section["id"], "section": 501, "course": section["course"], "container_id": str(self.container),
                                              "container": "Common Exam 1 - Partial Credit", "papers": 2, "grades": 4}])
        self.assertEqual((view["summary"]["papers"]["upload"], view["summary"]["grades"]["send"], view["skipped"]), (2, 4, []))
        # The server is the control: a send that does not name the published exam is refused and records nothing.
        with self.assertRaisesRegex(ValueError, "Grades on Common Exam 1 - Partial Credit are published on Gradescope"):
            self.send(view)
        with self.assertRaisesRegex(ValueError, "are published on Gradescope"):
            self.send(view, published=["123456"])
        with self.assertRaisesRegex(ValueError, "list of their IDs"):
            self.send(view, published=str(self.container))
        with self.assertRaisesRegex(Conflict, "not the ones published"):
            self.send(view, published=[str(self.container), "123456"])
        self.assertEqual((self.units("uploads"), self.units(), self.sender.status(self.aid)["sends"]), ([], [], []))
        receipt = self.send(view, published=[str(self.container)])
        with connect(self.path) as db:
            plan = json.loads(db.execute("SELECT plan_json FROM frq_gradescope_sends WHERE id=?", (receipt["send_id"],)).fetchone()[0])
        # The whole exam is confirmed: its container and every version, Version C (no Faculty paper) included.
        self.assertEqual(plan["published"], sorted(str(i) for i in (self.container, *self.versions.values())))
        self.assertTrue(plan["targets"][0]["published"])
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")} | {g["status"] for g in self.units()}, {"accepted"})
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        self.assertGraded("q20", "B", self.bob, [3], score=2.0)
        self.assertNothingForbidden()  # Faculty itself still publishes nothing
        # Everything went: nothing is left to confirm, and an exam that receives nothing asks for no confirmation.
        again = self.view()
        self.assertEqual((again["published"], again["sections"][0]["published"]), ([], True))

    def test_publication_changes_the_preview_and_an_exam_published_after_approval_gets_nothing(self):
        view = self.view()
        self.assertEqual((view["published"], view["sections"][0].get("published")), ([], None))
        self.fake.assignments[self.versions["B"]]["published"] = True  # one version published makes the exam published
        with self.assertRaisesRegex(Conflict, "Something changed since the preview"):
            self.send(view, published=[str(self.container)])
        # Published after the send was approved, without a confirmation: that version's paper and grades are not sent.
        self.fake.assignments[self.versions["B"]]["published"] = False
        self.send()
        self.fake.assignments[self.versions["B"]]["published"] = True
        self.sender.drain()
        found = {u["enrollment_id"]: u for u in self.units("uploads")}
        self.assertEqual((found[self.enrollment]["status"], found[self.second]["status"]), ("accepted", "failed"))
        self.assertEqual(found[self.second]["error"], gs.PUBLISHED)
        self.assertIsNone(self.fake.submission_of(self.versions["B"], self.bob))
        # A new preview offers it again, now with the warning, and a confirmed send takes it there.
        again = self.view()
        self.assertEqual([(p["papers"], p["grades"]) for p in again["published"]], [(1, 2)])
        self.send(again, published=[str(self.container)])
        self.sender.drain()
        self.assertEqual(self.units("uploads")[-1]["status"], "accepted")
        self.assertGraded("q21", "B", self.bob, [3, 5], score=5.0)
        self.assertNothingForbidden()

    def test_a_grade_for_a_paper_already_up_is_not_written_once_its_exam_is_published(self):
        self.send()
        self.sender.drain()
        self.decide("q20", "A", ["R3_3"])
        self.send()
        self.fake.assignments[self.versions["A"]]["published"] = True
        self.sender.drain()
        grade = self.units()[-1]
        self.assertEqual((grade["status"], grade["error"]), ("failed", gs.PUBLISHED))
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        self.assertEqual(len(self.fake.saves()), 4)

    def test_confirmed_failures_fail_and_reads_that_fail_only_wait(self):
        # Gradescope down while previewing: nothing is recorded, the route says 409.
        self.fake.fail("GET", r"/assignments$", ("status", 500))
        with self.assertRaisesRegex(Conflict, "Gradescope could not be read: Gradescope answered 500"):
            self.view()
        self.fake.fail("GET", r"/rubric/edit$", ("status", 502))
        answer = self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/preview", json={}, headers=HEADERS)
        self.assertEqual(answer.status_code, 409)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_sends").fetchone()[0], 0)
        # A 500 on a write is a definite answer: read back, not there, failed (and only an explicit Retry sends it again). A busy
        # Gradescope (429, 502, 503, 504) is not: BusyGradescope below.
        self.fake.fail("POST", r"/submissions$", ("status", 500))
        self.fake.fail("POST", r"/save_grade$", ("status", 500))
        receipt = self.send()
        self.sender.drain()
        uploads, grades = self.units("uploads"), self.units()
        self.assertEqual(([u["status"] for u in uploads], uploads[0]["error"]), (["failed", "accepted"], "Gradescope did not keep the paper (it answered 500). Retry to send it again."))
        self.assertEqual(sorted(g["status"] for g in grades), ["accepted", "failed", "failed", "failed"])
        self.assertEqual(sorted(g["error"] for g in grades if g["status"] == "failed"),
                         ["Gradescope did not keep the grade. Retry to send it again.", "Its paper was not uploaded.", "Its paper was not uploaded."])
        self.assertEqual(self.sender.drain(), 0)
        self.sender.retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual((len(self.fake.uploads()), len(self.fake.saves())), (3, 5))
        # A read failing before a write: the unit waits (pending, with its reason), then goes.
        self.decide("q20", "B", ["R3_2"])
        self.fake.fail("GET", r"/grade$", ("status", 500))
        self.send()
        self.sender.drain()
        waiting = self.units()[-1]
        self.assertEqual((waiting["status"], bool(waiting["not_before"])), ("pending", True))
        self.assertIn("Waiting to try again: Gradescope answered 500", waiting["error"])
        self.assertIn("Gradescope answered 500", self.sender.status(self.aid)["sends"][0]["waiting"])
        self.ready_now()
        self.sender.drain()
        self.assertGraded("q20", "B", self.bob, [2], score=4.0)

    def test_the_background_worker_sends_when_woken(self):
        import threading
        stop = threading.Event()
        worker = threading.Thread(target=self.sender.worker, args=(stop,), daemon=True)
        worker.start()
        try:
            self.send()
            for _ in range(200):
                if self.sender.status(self.aid)["sends"][0]["status"] == "complete":
                    break
                stop.wait(.05)
            self.assertEqual(self.sender.status(self.aid)["sends"][0]["status"], "complete")
        finally:
            stop.set()
            self.sender.wake.set()  # the loop is waiting for work; let it see the stop at once
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})

    def test_an_uncertain_unit_whose_check_cannot_read_waits_instead_of_asking_again_at_once(self):
        self.fake.fail("POST", r"/submissions$", "drop_after")
        self.fake.fail("GET", r"/submissions$", ("status", 500), times=40, when=self.fake.after_write())
        self.send()
        self.sender.process_one()
        self.assertEqual(self.units("uploads")[0]["status"], "uncertain")
        reads = len(self.fake.requests)
        self.sender.drain()
        # One more look, then the uncertain paper waits its minute like any other unit: Gradescope is not asked again and again.
        self.assertLessEqual(len([r for r in self.fake.requests[reads:] if r["path"].endswith("/submissions")]), 3)
        upload = self.units("uploads")[0]
        self.assertEqual(upload["status"], "uncertain")
        self.assertTrue(upload["not_before"])
        self.fake.faults.clear()
        self.ready_now()
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual(len(self.fake.uploads()), 2)

    def test_an_uncertain_unit_of_a_cancelled_send_is_still_settled_by_reading(self):
        self.fake.fail("POST", r"/submissions$", "drop_after")
        self.fake.fail("GET", r"/submissions$", ("status", 500), when=self.fake.after_write())
        receipt = self.send()
        self.sender.process_one()
        self.assertEqual(self.units("uploads")[0]["status"], "uncertain")
        self.sender.cancel(self.aid, receipt["send_id"])
        self.sender.drain()
        # Reading changes nothing on Gradescope, and only a read can say it landed: it did, so the paper is Faculty's.
        self.assertEqual(self.units("uploads")[0]["status"], "accepted")
        view = self.view()
        self.assertEqual(view["summary"]["papers"], {"upload": 1, "already": 1, "skipped": 0})
        self.assertEqual(len(self.fake.uploads()), 1)

    def test_a_new_send_waits_for_an_earlier_uncertain_paper_instead_of_failing(self):
        self.fake.fail("POST", r"/submissions$", "drop_before")
        self.fake.fail("GET", r"/submissions$", ("status", 500), when=self.fake.after_write())
        receipt = self.send()
        self.sender.process_one()
        self.assertEqual(self.units("uploads")[0]["status"], "uncertain")
        self.sender.cancel(self.aid, receipt["send_id"])
        view = self.view()  # before the sender has looked again
        self.assertIn("Faculty is still checking whether its earlier upload of this paper reached Gradescope; preview again shortly.", [s["reason"] for s in view["skipped"]])
        self.assertEqual(view["summary"]["papers"]["upload"], 1)
        self.send(view)  # the other paper goes; nothing breaks
        self.sender.drain()
        uploads = self.units("uploads")
        self.assertEqual([u["status"] for u in uploads], ["failed", "cancelled", "accepted"])
        view = self.view()
        self.assertEqual(view["summary"]["papers"], {"upload": 1, "already": 1, "skipped": 0})
        self.send(view)
        self.sender.drain()
        self.assertEqual(len(self.fake.uploads()), 3)
        self.assertEqual(len({(s["assignment"], tuple(s["owners"])) for s in self.fake.submissions.values()}), 2)

    def test_retry_leaves_alone_what_a_later_send_already_sent(self):
        self.fake.fail("POST", r"/submissions$", ("status", 302))
        first = self.send()
        self.sender.drain()
        self.assertEqual([u["status"] for u in self.units("uploads")], ["failed", "accepted"])
        self.send()  # the failed paper, sent again by a new send
        self.sender.drain()
        self.assertEqual([u["status"] for u in self.units("uploads")], ["failed", "accepted", "accepted"])
        with self.assertRaisesRegex(Conflict, "later send"):
            self.sender.retry(self.aid, first["send_id"])
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(len(self.fake.uploads()), 3)

    def test_a_grade_whose_score_would_differ_at_its_turn_is_refused_before_writing(self):
        self.send()
        self.sender.process_one()
        self.sender.process_one()
        self.fake.questions[self.gq[("q20", "A")]]["scoring_type"] = "negative"  # changed on Gradescope after the approval
        self.sender.drain()
        grade = next(g for g in self.units() if g["gradescope_question_id"] == str(self.gq[("q20", "A")]))
        self.assertEqual((grade["status"], grade["error"]), ("refused", "Gradescope would give 6 for these items now; Faculty's grade is 4. Preview and send again."))
        self.assertEqual(self.fake.state(self.qsub("q20", "A", self.alice))[0], [])
        self.assertEqual(len(self.fake.saves()), 3)

    def test_a_grade_already_there_with_another_score_is_not_accepted(self):
        self.send()
        self.sender.process_one()
        self.sender.process_one()
        # The items Faculty wants are already checked, but Gradescope's worth now makes a different score of them (5, not 6).
        self.fake.grade(self.qsub("q21", "A", self.alice)["id"], applied=[self.item("q21", "A", 2), self.item("q21", "A", 6)])
        self.fake.questions[self.gq[("q21", "A")]]["weight"] = 5
        self.sender.drain()
        grade = next(g for g in self.units() if g["gradescope_question_id"] == str(self.gq[("q21", "A")]))
        self.assertEqual((grade["status"], grade["error"]), ("refused", "Gradescope would give 5 for these items now; Faculty's grade is 6. Preview and send again."))
        # And a grade whose write was uncertain is accepted only when Gradescope's score for it is Faculty's.
        self.fake.fail("POST", r"/save_grade$", "drop_after")
        self.fake.fail("GET", r"/grade$", ("status", 500), when=lambda: len(self.fake.saves()) > 3)
        self.decide("q20", "B", ["R3_2"])
        self.send()
        self.sender.process_one()
        self.assertEqual(self.units()[-1]["status"], "uncertain")
        self.fake.questions[self.gq[("q20", "B")]]["weight"] = 3  # Gradescope's ceiling now cuts 4 to 3
        self.sender.drain()
        grade = self.units()[-1]
        self.assertEqual((grade["status"], grade["error"]), ("failed", "Gradescope shows these items with a different score (3) than Faculty's. Look at it on Gradescope."))

    def test_a_paper_that_changed_after_faculty_uploaded_it_holds_its_grades(self):
        self.send()
        self.sender.drain()
        # The instructor rescans and reviews Alice's paper again, and grades it again: the paper on Gradescope is the earlier one.
        self.reviewed_source(changed="rescanned")
        for _ in range(10):  # its version is confirmed against the blank exams again
            if not self.frq.process_version_one():
                break
        self.regrade(["q20", "q21"], "A")
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "A", ["R4_2", "R4_6"])
        view = self.view()
        self.assertEqual([s["reason"] for s in view["skipped"]],
                         ["Gradescope has the earlier reviewed paper of this student that Faculty uploaded; the paper has changed since, so its grades are not sent onto it."])
        self.assertEqual((view["summary"]["grades"]["held"], view["summary"]["grades"]["update"]), (2, 0))

    def test_a_paper_reassigned_on_gradescope_mid_send_gets_no_more_grades(self):
        # Ownership is read before every grade, never remembered from the paper's earlier grade (gradescope_sync._still_ours).
        self.send()
        upload = next(u for u in self.units("uploads") if u["enrollment_id"] == self.enrollment)
        for _ in range(50):
            if any(g["upload_id"] == upload["id"] and g["status"] == "accepted" for g in self.units()):
                break
            self.sender.process_one()
        paper = next(s for s in self.fake.submissions.values() if s["filename"] == f"faculty-{upload['id']}.pdf")
        paper["owners"] = [self.bob]  # someone gives Alice's paper to Bob on Gradescope
        self.sender.drain()
        grades = [g for g in self.units() if g["upload_id"] == upload["id"]]
        self.assertEqual([g["status"] for g in grades].count("accepted"), 1)
        self.assertEqual({g["error"] for g in grades if g["status"] != "accepted"},
                         {"The paper on Gradescope is no longer the one Faculty uploaded (it was replaced or reassigned); nothing was sent."})

    def test_a_faculty_paper_deleted_on_gradescope_is_named_not_uploaded_again(self):
        self.send()
        self.sender.drain()
        del self.fake.submissions[self.fake.submission_of(self.versions["A"], self.alice)["id"]]  # the instructor deleted it there
        view = self.view()
        self.assertEqual([s["reason"] for s in view["skipped"]], ["Gradescope no longer has the paper Faculty uploaded for this student. Faculty keeps its "
                                                                 "receipt and does not upload it again; upload it on Gradescope if it is needed."])
        self.assertEqual((view["summary"]["papers"], view["summary"]["grades"]["held"]), ({"upload": 0, "already": 1, "skipped": 1}, 2))
        answer = self.client.post(f"/api/grading/gradescope/assignments/{self.aid}/send", headers=HEADERS,
                                  json={"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {}})
        self.assertEqual((answer.status_code, answer.json["error"]), (409, "There is nothing to send."))  # never the catalog's 503

    def regrade(self, keys, letter):
        command = {"request_id": str(uuid.uuid4()), "question_ids": [self.q[(k, letter)] for k in keys], "mode": "local", "operation": "recognize"}
        found = self.frq.preview(self.aid, command)
        self.assertEqual(len(found["eligible"]), len(keys), found["skipped"])
        job = self.frq.create_job(self.aid, {**command, "fingerprint": found["fingerprint"]})
        for _ in range(40):
            if not self.frq.process_one():
                break
        self.assertEqual(self.frq.job(job["id"])["status"], "review_ready")


class BusyGradescope(GradescopeCase):
    """Gradescope says it is busy (requirement, 2026-09-25): nothing
    fails for it. The whole sender waits (Retry-After, else a doubling wait), the session sends more slowly, and the paper or grade
    goes again by itself; every try reads Gradescope before it writes, so what landed after all is never sent twice."""

    def test_too_many_requests_on_an_upload_waits_slows_down_and_goes_again_by_itself(self):
        self.send()
        self.fake.fail("POST", r"/submissions$", ("status", 429, {"Retry-After": "120"}))
        self.assertTrue(self.sender.process_one())
        upload = self.units("uploads")[0]
        self.assertEqual((upload["status"], upload["attempts"]), ("pending", 1))
        self.assertIn("Gradescope asked Faculty to slow down (429 Too Many Requests); nothing was changed. Faculty tries again in 2 minutes, more slowly.", upload["error"])
        self.assertGreater(datetime.fromisoformat(upload["not_before"]), datetime.now(timezone.utc) + timedelta(seconds=100))
        self.assertEqual(self.sender._session.pace, 1.0)
        self.assertFalse(self.sender.process_one())  # everything waits, not only this paper
        self.assertEqual(self.sender.drain(), 0)
        self.assertEqual(len(self.fake.uploads()), 1)
        self.assertIn("429 Too Many Requests", self.sender.status(self.aid)["sends"][0]["waiting"])
        self.ready_now()
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual((len(self.fake.uploads()), len(self.fake.submissions)), (3, 2))  # the refused one, then each paper once
        self.assertEqual(self.sender.status(self.aid)["sends"][0]["status"], "complete")
        self.assertNothingForbidden()

    def test_a_busy_grade_save_goes_again_by_itself(self):
        self.fake.fail("POST", r"/save_grade$", ("status", 503))
        self.send()
        self.sender.drain()
        waiting = [g for g in self.units() if g["status"] == "pending"]
        self.assertEqual(len(waiting), 4)  # the busy one waits, and so does everything after it
        self.assertEqual(len(self.fake.saves()), 1)
        self.assertEqual(sum("503 Service Unavailable" in g["error"] for g in waiting), 1)
        self.ready_now()
        self.sender.drain()
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual(len(self.fake.saves()), 5)
        self.assertGraded("q21", "A", self.alice, [2, 6], score=6.0)

    def test_a_gateway_timeout_after_the_paper_landed_is_accepted_without_sending_it_again(self):
        self.fake.fail("POST", r"/submissions$", ("status_after", 504))
        self.fake.fail("POST", r"/save_grade$", ("status_after", 504))
        self.send()
        with patch.object(gw, "CAREFUL", 0):  # the tests' own pace
            self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual((len(self.fake.uploads()), len(self.fake.submissions), len(self.fake.saves())), (2, 2, 4))

    def test_a_gateway_error_that_did_not_land_is_sent_again_after_the_wait(self):
        self.fake.fail("POST", r"/submissions$", ("status", 502))
        self.fake.fail("POST", r"/save_grade$", ("status", 504))
        self.send()
        self.assertTrue(self.sender.process_one())
        upload = self.units("uploads")[0]
        self.assertEqual((upload["status"], upload["attempts"]), ("pending", 1))
        self.assertIn("Gradescope was overloaded (502 Bad Gateway) and did not keep the paper. Faculty tries again", upload["error"])
        self.ready_now()
        self.sender.drain()
        grade = next(g for g in self.units() if g["status"] == "pending")
        self.assertIn("Gradescope did not answer in time (504 Gateway Timeout) and did not keep the grade.", grade["error"])
        self.ready_now()
        self.sender.drain()
        uploads = self.units("uploads")
        self.assertEqual(({u["status"] for u in uploads}, uploads[0]["attempts"]), ({"accepted"}, 2))
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual((len(self.fake.uploads()), len(self.fake.submissions), len(self.fake.saves())), (3, 2, 5))

    def test_a_busy_read_while_settling_an_uncertain_paper_keeps_it_uncertain_and_waits(self):
        self.fake.fail("POST", r"/submissions$", "drop_after")
        self.fake.fail("GET", r"/submissions$", ("status", 503), times=2, when=self.fake.after_write())
        self.send()
        self.assertTrue(self.sender.process_one())
        upload = self.units("uploads")[0]
        self.assertEqual(upload["status"], "uncertain")
        self.assertTrue(upload["not_before"])
        self.assertFalse(self.sender.process_one())
        self.ready_now()
        self.assertTrue(self.sender.process_one())  # read again, busy again: still uncertain, never sent again
        self.assertEqual(self.units("uploads")[0]["status"], "uncertain")
        self.ready_now()
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual(len(self.fake.uploads()), 2)

    def test_a_busy_sign_in_is_waited_for_not_taken_for_a_refused_password(self):
        self.send()
        self.fake.fail("POST", r"^/login$", ("status", 429))
        self.assertTrue(self.sender.process_one())
        upload = self.units("uploads")[0]
        self.assertEqual(upload["status"], "pending")
        self.assertIn("429 Too Many Requests", upload["error"])
        self.assertNotIn("did not accept the saved sign-in", upload["error"])
        self.assertIsNone(self.sender._paused)
        self.assertIsNotNone(self.sender._busy_until)
        self.ready_now()
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})

    def test_a_paper_that_meets_a_busy_gradescope_again_and_again_fails_for_retry(self):
        self.fake.fail("POST", r"/submissions$", ("status", 503), times=3)
        receipt = self.send()
        with patch.object(gs, "BUSY_TRIES", 3):
            for _ in range(3):
                self.ready_now()
                self.sender.drain()
            self.ready_now()
            self.sender.drain()
        uploads = self.units("uploads")
        self.assertEqual([u["status"] for u in uploads], ["failed", "accepted"])
        self.assertTrue(uploads[0]["error"].startswith("Gradescope stayed busy through 3 tries (Gradescope was busy or down for maintenance (503 Service Unavailable)"))
        self.assertTrue(uploads[0]["error"].endswith("Use Retry failed when it is quieter."))
        self.sender.retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual((len(self.fake.uploads()), len(self.fake.submissions)), (5, 2))

    def test_the_wait_doubles_while_gradescope_stays_busy_and_honours_retry_after(self):
        busy = lambda after=None: gw.GradescopeBusy(429, "GET", after)
        self.assertEqual([self.sender._pause(busy()) for _ in range(7)], [30, 60, 120, 240, 480, 900, 900])
        self.sender._settled("a unit that went through")
        self.assertEqual([self.sender._pause(busy()), self.sender._pause(busy(1200)), self.sender._pause(busy(99999)), self.sender._pause(busy(5))],
                         [30, 1200, 3600, 240])


class ParallelSender(GradescopeCase):
    """Several units at once, each on its own connection of ONE sign-in (requirement, 2026-09-25, after the sandbox answered 24
    parallel grade sends without a refusal). Nothing is worked on twice, a renewed or refused sign-in happens once for all."""

    def logins(self):
        return sum(1 for r in self.fake.requests if r["method"] == "POST" and r["path"] == "/login")

    def test_units_go_at_once_on_one_sign_in(self):
        self.send()
        self.fake.delay, self.fake.max_inflight, before = 0.1, 0, self.logins()
        self.assertEqual(self.sender.drain(parallel=8), 6)
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual((len(self.fake.uploads()), len(self.fake.saves())), (2, 4))
        self.assertGraded("q21", "A", self.alice, [2, 6], score=6.0)
        self.assertGreaterEqual(self.fake.max_inflight, 2)  # really at once
        self.assertEqual(self.logins() - before, 1)  # one sign-in for every connection
        self.assertNothingForbidden()

    def test_an_expired_sign_in_is_renewed_once_for_every_connection(self):
        self.send()
        self.sender.drain(parallel=8)
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "B", ["R4_3"])
        self.decide("q20", "B", ["R3_2"])
        self.send()
        self.fake.delay, before = 0.05, self.logins()
        self.fake.expire_sessions()  # Gradescope ended the session while the grades waited
        self.sender.drain(parallel=8)
        self.ready_now()
        self.sender.drain(parallel=8)
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertEqual(self.logins() - before, 1)
        self.assertGraded("q20", "A", self.alice, [3], score=2.0)

    def test_a_refused_sign_in_is_tried_once_not_once_per_connection(self):
        self.send()
        self.write_credentials({"email": EMAIL, "password": "not the password"})
        before = self.logins()
        self.sender.drain(parallel=8)
        self.assertEqual(self.logins() - before, 1)
        self.assertTrue(self.sender._paused)
        waiting = self.units("uploads")
        self.assertEqual({u["status"] for u in waiting}, {"pending"})
        self.assertTrue(all("did not accept the saved sign-in" in u["error"] for u in waiting))
        self.assertEqual(self.sender.drain(parallel=8), 0)  # paused: nobody signs in again
        self.assertEqual(self.logins() - before, 1)

    def test_a_unit_that_stops_unexpectedly_is_settled_by_reading_while_the_others_go_on(self):
        self.send()
        original, calls = GradescopeSender._grade, []

        def stops(sender, grade):
            if not calls:
                calls.append(grade["id"])
                sender._claim(grade)  # claimed, then something unforeseen: whether it went is unknown
                raise RuntimeError("unforeseen")
            return original(sender, grade)
        with patch.object(GradescopeSender, "_grade", stops), self.assertLogs("grading.gradescope_sync", "ERROR"):
            self.sender.drain(parallel=8)
        found = {g["id"]: g for g in self.units()}
        self.assertEqual((found[calls[0]]["status"], found[calls[0]]["error"]), ("failed", "Faculty's grade is not on Gradescope; it did not land. Retry to send it again."))
        self.assertEqual(sorted(g["status"] for g in found.values()), ["accepted", "accepted", "accepted", "failed"])
        self.assertEqual(len(self.fake.saves()), 3)  # read, never sent again on its own


class BusyTransport(unittest.TestCase):
    """The session itself: a busy answer is its own error, says whether a write may have landed, and slows the session down."""

    def setUp(self):
        self.fake = FakeGradescope().start()
        self.addCleanup(self.fake.stop)
        self.fake.add_course("140-501", "Mathematics for Business and Social Sciences", "Fall 2026")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "gradescope.json")
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        os.write(descriptor, json.dumps({"email": EMAIL, "password": PASSWORD}).encode())
        os.close(descriptor)
        self.waits = []
        self.session = gw.Session(gw.Settings(credentials_file=path, base=self.fake.url, pace=0.2), sleep=self.waits.append)

    def test_each_busy_answer_doubles_the_pause_between_requests_up_to_five_seconds(self):
        self.fake.fail("GET", r"^/account$", ("status", 429), times=4)
        for expected in (1.0, 2.0, 4.0, 5.0):
            with self.assertRaises(gw.GradescopeBusy) as caught:
                self.session.courses()
            self.assertEqual((caught.exception.outcome, caught.exception.status, self.session.pace), ("unavailable", 429, expected))
        self.assertEqual([c["short"] for c in self.session.courses()], ["140-501"])
        self.assertGreaterEqual(max(self.waits), 4.9)  # the session really waited longer between requests

    def test_a_busy_answer_says_whether_a_write_may_have_landed(self):
        self.assertEqual([gw.GradescopeBusy(status, "POST").outcome for status in (429, 502, 503, 504)], ["unavailable", "unknown", "unavailable", "unknown"])
        self.assertEqual({gw.GradescopeBusy(status, "GET").outcome for status in (429, 502, 503, 504)}, {"unavailable"})
        self.assertTrue(isinstance(gw.GradescopeBusy(429, "GET"), gw.GradescopeError) and not isinstance(gw.GradescopeBusy(429, "GET"), gw.SignInRefused))

    def test_retry_after_in_seconds_or_as_a_date(self):
        import email.utils
        self.assertEqual(gw.retry_after({"Retry-After": "120"}), 120)
        when = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=300), usegmt=True)
        self.assertTrue(280 <= gw.retry_after({"Retry-After": when}) <= 300)
        self.assertEqual([gw.retry_after(h) for h in ({"Retry-After": "soon"}, {"Retry-After": ""}, {}, None)], [None, None, None, None])


class ReplaceOthers(GradescopeCase):
    """Replacing grades someone else made on Gradescope (requirement, 2026-10-01), after the 508 TA graded Quiz 3 over a papers-only upload."""

    def lists(self, since=0):
        return [r for r in self.fake.requests[since:] if r["method"] == "GET" and re.search(r"/questions/\d+/submissions$", r["path"])]

    def opened(self, since=0):
        return [r for r in self.fake.requests[since:] if r["method"] == "GET" and r["path"].endswith("/grade")]

    def papers_up(self):
        """The papers go first without grades, as Quiz 3's did, so a TA can grade them on Gradescope."""
        self.send(self.sender.preview(self.aid, {"papers_only": True}), papers_only=True)
        self.assertEqual(self.sender.drain(), 2)

    def replace_view(self):
        return self.sender.preview(self.aid, {"replace_others": True})

    def send_replacing(self, view, replacing, **extra):
        """A send as the dialog makes it: the number confirmed in its popup and the replacements as the preview showed them."""
        return self.send(view, replace_others=True, replacing=replacing, replacements=view["replacements"], **extra)

    def grade_unit(self, key, letter):
        response = self.response(key, letter)["id"]
        return [g for g in self.units() if g["response_id"] == response][-1]

    def test_without_the_choice_nothing_is_read_or_replaced(self):
        self.papers_up()
        qsub = self.qsub("q20", "A", self.alice)["id"]
        self.fake.grade(qsub, applied=[self.item("q20", "A", 1)])
        before = len(self.fake.requests)
        view = self.view()
        self.assertFalse({"replace_others", "replacing", "replacements"} & set(view))  # the ordinary preview is what it always was
        self.assertEqual(view["summary"]["grades"], {"send": 4, "update": 0, "already": 0, "refused": 0, "held": 0})
        self.assertFalse(any("replace" in q for q in view["questions"]))
        self.assertEqual((self.lists(before), self.opened(before)), ([], []))
        self.send(view)
        self.sender.drain()
        grade = self.grade_unit("q20", "A")
        self.assertEqual((grade["status"], grade["error"]), ("present", "Someone graded this question on Gradescope; Faculty never overwrites it."))
        self.assertEqual(self.fake.state(qsub)[0], [self.item("q20", "A", 1)])

    def test_a_ta_s_grades_that_differ_are_listed_and_replaced_only_with_the_choice_and_its_confirmation(self):
        self.papers_up()
        # The TA graded three questions there: one with another score, one exactly as Faculty did, one with Faculty's items and a comment.
        self.fake.grade(self.qsub("q20", "A", self.alice)["id"], applied=[self.item("q20", "A", 1)])  # 10, Faculty's is 4
        self.fake.grade(self.qsub("q21", "B", self.bob)["id"], applied=[self.item("q21", "B", 3), self.item("q21", "B", 5)])
        self.fake.grade(self.qsub("q20", "B", self.bob)["id"], applied=[self.item("q20", "B", 3)], comments="Nice work", by="Tess Assistant")
        before = len(self.fake.requests)
        view = self.replace_view()
        self.assertTrue(view["replace_others"])
        self.assertEqual(view["summary"]["grades"], {"send": 2, "update": 0, "already": 0, "refused": 0, "held": 0, "replace": 2})
        replacing = view["replacing"]
        self.assertEqual((replacing["grades"], replacing["score_changes"], replacing["same"]), (2, 1, 1))
        self.assertEqual(replacing["by"], [{"name": "Pat Grader", "grades": 1}, {"name": "Tess Assistant", "grades": 1}])
        self.assertEqual(replacing["same_by"], [{"name": "Pat Grader", "grades": 1}])  # who graded the one already as Faculty did
        self.assertEqual(sorted((r["question"], r["gradescope"], r["faculty"], r["by"]) for r in replacing["listed"]),
                         [("q20", "10", "4", "Pat Grader"), ("q20-B", "2", "2", "Tess Assistant")])
        self.assertTrue(all(r["name"] for r in replacing["listed"]))
        self.assertNoUin(view)
        shown = {response: others for response, others in view["replacements"]}
        self.assertEqual(shown, {self.response("q20", "A")["id"]: {"applied": [str(self.item("q20", "A", 1))], "points": None, "comments": None, "score": "10", "by": "Pat Grader"},
                                 self.response("q20", "B")["id"]: {"applied": [str(self.item("q20", "B", 3))], "points": None, "comments": "Nice work", "score": "2", "by": "Tess Assistant"}})
        per = {q["faculty"]: q for q in view["questions"]}
        self.assertEqual([(per[k]["send"], per[k]["replace"]) for k in ("q20", "q21", "q20-B", "q21-B")], [(0, 1), (1, 0), (0, 1), (1, 0)])
        # One list per Gradescope question, and only the three graded questions opened.
        self.assertEqual((len(self.lists(before)), len(self.opened(before))), (4, 3))
        self.assertNotEqual(view["fingerprint"], self.view()["fingerprint"])
        # The server is the control: the send must name how many it replaces.
        with self.assertRaisesRegex(ValueError, "This send replaces 2 grades someone else made on Gradescope"):
            self.send(view, replace_others=True, replacements=view["replacements"])
        with self.assertRaisesRegex(Conflict, "not the ones on Gradescope now"):
            self.send_replacing(view, 1)
        # The replacements must be the preview's, exactly: another grade, one fewer, one twice or one changed makes the send stale.
        first, second = view["replacements"]
        changed = [first[0], {**first[1], "score": "9"}]
        for wrong in ([first], [first, second, second], [changed, second], []):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(Conflict, "Something changed since the preview"):
                self.send(view, replace_others=True, replacing=2, replacements=wrong)
        for bad in ("all", [first[0]], [[first[0], {**first[1], "extra": 1}]], [[first[0], {**first[1], "score": "ten"}]], [[1, first[1]]]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Send the replacements of the preview you looked at"):
                self.send(view, replace_others=True, replacing=2, replacements=bad)
        with self.assertRaisesRegex(Conflict, "Something changed"):
            self.send(view, replacing=2)  # without the choice the plan is another one
        for bad in ("2", True, -1, 2.0):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "whole number"):
                self.send(view, replace_others=True, replacing=bad)
        self.assertEqual(self.units(), [])
        before = len(self.fake.requests)
        receipt = self.send_replacing(view, 2)
        self.assertEqual((self.lists(before), self.opened(before)), ([], []))  # the send did not read every graded question again
        self.assertEqual((receipt["plan"]["replace_others"], receipt["plan"]["replacing"]), (True, 2))
        with connect(self.path) as db:
            event = json.loads(db.execute("SELECT detail_json FROM frq_events WHERE kind='gradescope_send' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual((event["grades"], event["replace_others"], event["replacing"]), (4, True, 2))
        self.assertEqual(self.sender.drain(), 4)
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        self.assertGraded("q20", "B", self.bob, [3], score=2.0)  # the TA's comment went with their grade
        self.assertGraded("q21", "B", self.bob, [3, 5], score=5.0)
        receipt = lambda key, letter: json.loads(self.grade_unit(key, letter)["receipt_json"])
        self.assertEqual(receipt("q20", "A")["replaced"], {"applied": [str(self.item("q20", "A", 1))], "points": None, "comments": None,
                                                           "score": "10", "by": "Pat Grader"})
        self.assertEqual(receipt("q20", "B")["replaced"]["comments"], "Nice work")
        self.assertNotIn("replaced", receipt("q21", "B"))  # exactly Faculty's: recorded as on Gradescope, nothing written
        self.assertEqual(self.grade_unit("q21", "B")["error"], "It was already on Gradescope.")
        self.assertEqual(len(self.fake.saves()), 3)
        self.assertEqual(self.sender.status(self.aid)["coverage"]["grades"], {"final": 4, "sent": 4, "changed": 0, "not_sent": 0})
        again = self.replace_view()
        self.assertEqual((again["summary"]["grades"]["replace"], again["summary"]["grades"]["already"]), (0, 4))
        self.assertNothingForbidden()

    def test_a_ta_grading_after_the_preview_does_not_stop_the_send_and_what_they_graded_is_left_alone(self):
        self.papers_up()
        qsub, fresh = self.qsub("q20", "A", self.alice)["id"], self.qsub("q21", "A", self.alice)["id"]
        self.fake.grade(qsub, applied=[self.item("q20", "A", 1)])
        view = self.replace_view()
        self.assertEqual(view["replacing"]["grades"], 1)
        # A TA keeps grading after the preview (requirement, 2026-10-01): they change the grade the preview showed and grade a
        # question nobody had graded. The send is not stale for it: it plans the replacement as the preview showed it.
        self.fake.grade(qsub, applied=[self.item("q20", "A", 4)], by="Tess Assistant")
        self.fake.grade(fresh, applied=[self.item("q21", "A", 1)], by="Tess Assistant")
        before = len(self.fake.requests)
        receipt = self.send_replacing(view, 1)
        self.assertEqual((self.lists(before), self.opened(before)), ([], []))
        self.fake.grade(self.qsub("q20", "B", self.bob)["id"], applied=[self.item("q20", "B", 1)], by="Tess Assistant")  # and after it was approved
        self.sender.drain()
        grade = self.grade_unit("q20", "A")
        self.assertEqual((grade["status"], grade["error"]), ("present", "Someone changed this grade on Gradescope after the preview you approved; "
                                                             "Faculty replaces only the grade the preview showed, so it is left alone."))
        self.assertEqual(self.fake.state(qsub)[0], [self.item("q20", "A", 4)])
        self.assertEqual(json.loads(grade["items_json"])["others"]["applied"], [str(self.item("q20", "A", 1))])  # what was approved
        for key, letter, user, number in (("q21", "A", self.alice, 1), ("q20", "B", self.bob, 1)):
            other = self.grade_unit(key, letter)
            self.assertEqual((other["status"], other["error"]), ("present", "Someone graded this question on Gradescope; Faculty never overwrites it."))
            self.assertEqual(self.fake.state(self.qsub(key, letter, user))[0], [self.item(key, letter, number)])
        self.assertGraded("q21", "B", self.bob, [3, 5], score=5.0)  # the one nobody touched went
        problems = self.sender.problems(self.aid, receipt["send_id"])["problems"]
        self.assertEqual(sorted((p["question"], p["status"]) for p in problems), [("q20", "present"), ("q20-B", "present"), ("q21", "present")])

    def test_a_send_carrying_replacements_past_the_ordinary_request_limit_goes_through_the_route(self):
        # 2026-10-02: about 200 replacements came back over the ordinary 32 KiB and the route refused the send as too large.
        self.papers_up()
        self.fake.grade(self.qsub("q20", "A", self.alice)["id"], applied=[self.item("q20", "A", 1)], comments="See the second line. " * 2000)
        view = self.replace_view()
        body = {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {},
                "replace_others": True, "replacing": 1, "replacements": view["replacements"]}
        self.assertGreater(len(json.dumps(body)), 32 * 1024)
        route = f"/api/grading/gradescope/assignments/{self.aid}"
        answer = self.client.post(f"{route}/send", json=body, headers=HEADERS)
        self.assertEqual(answer.status_code, 201, answer.json)
        self.assertEqual((answer.json["plan"]["replace_others"], answer.json["plan"]["replacing"]), (True, 1))
        oversized = b" " * (16 * 1024 * 1024 + 1)
        self.assertEqual(self.client.post(f"{route}/send", data=oversized, content_type="application/json", headers=HEADERS).status_code, 413)
        # Only the send carries them back: the preview keeps the ordinary limit.
        self.assertEqual(self.client.post(f"{route}/preview", data=b" " * (32 * 1024 + 1), content_type="application/json", headers=HEADERS).status_code, 413)

    def test_a_send_without_the_previews_replacements_reads_gradescope_again(self):
        # A send dialog opened before 2026-10-01's change carries no `replacements`: its send reads every graded question again, as
        # its preview did, so a grade changed there since makes it stale.
        self.papers_up()
        qsub = self.qsub("q20", "A", self.alice)["id"]
        self.fake.grade(qsub, applied=[self.item("q20", "A", 1)])
        view = self.replace_view()
        before = len(self.fake.requests)
        self.fake.grade(qsub, applied=[self.item("q20", "A", 4)])
        with self.assertRaisesRegex(Conflict, "Something changed"):
            self.send(view, replace_others=True, replacing=1)
        self.assertEqual((len(self.lists(before)), len(self.opened(before))), (4, 1))
        view = self.replace_view()
        self.send(view, replace_others=True, replacing=1)
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        self.assertEqual(json.loads(self.grade_unit("q20", "A")["receipt_json"])["replaced"]["applied"], [str(self.item("q20", "A", 4))])

    def test_a_change_on_gradescope_over_faculty_s_own_grade_is_found_by_its_score(self):
        self.send()
        self.sender.drain()  # every grade of Faculty's is on Gradescope
        self.fake.grade(self.qsub("q20", "A", self.alice)["id"], applied=[self.item("q20", "A", 1)], by="Tess Assistant")  # 10, not 4
        # Faculty's items with a comment: the list shows Faculty's score, so it is taken as unchanged (the list shows no items).
        self.fake.grade(self.qsub("q21", "B", self.bob)["id"], applied=[self.item("q21", "B", 3), self.item("q21", "B", 5)], comments="Checked", by="Tess Assistant")
        self.assertEqual(self.view()["summary"]["grades"], {"send": 0, "update": 0, "already": 4, "refused": 0, "held": 0})
        before = len(self.fake.requests)
        view = self.replace_view()
        self.assertEqual(view["summary"]["grades"], {"send": 0, "update": 0, "already": 3, "refused": 0, "held": 0, "replace": 1})
        self.assertEqual(len(self.opened(before)), 1)
        self.send_replacing(view, 1)
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        grade = self.grade_unit("q20", "A")
        self.assertEqual((grade["status"], bool(grade["replaces_id"])), ("accepted", True))
        self.assertEqual(json.loads(grade["receipt_json"])["replaced"]["by"], "Tess Assistant")
        self.assertEqual(self.fake.state(self.qsub("q21", "B", self.bob))[2], "Checked")

    def test_a_replacement_that_did_not_land_is_settled_by_reading_and_sent_again_only_on_retry(self):
        self.papers_up()
        qsub = self.qsub("q20", "A", self.alice)["id"]
        self.fake.grade(qsub, applied=[self.item("q20", "A", 1)])
        receipt = self.send_replacing(self.replace_view(), 1)
        self.fake.fail("POST", rf"/submissions/{qsub}/save_grade$", "drop_before")
        self.fake.fail("GET", rf"/submissions/{qsub}/grade$", ("status", 500),
                       when=lambda: any(r["path"].endswith(f"/{qsub}/save_grade") for r in self.fake.saves()))
        self.sender.drain()
        grade = self.grade_unit("q20", "A")
        self.assertEqual((grade["status"], grade["error"]), ("failed", "Gradescope still shows the grade you approved replacing; Faculty's did not land. Retry to send it again."))
        self.assertEqual(self.fake.state(qsub)[0], [self.item("q20", "A", 1)])
        self.sender.retry(self.aid, receipt["send_id"])
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [2], score=4.0)
        self.assertEqual(json.loads(self.grade_unit("q20", "A")["receipt_json"])["replaced"]["score"], "10")

    def test_papers_only_replaces_nothing_and_new_papers_have_nothing_to_replace(self):
        with self.assertRaisesRegex(ValueError, "Papers only sends no grade"):
            self.sender.preview(self.aid, {"papers_only": True, "replace_others": True})
        for bad in ("yes", 1, None):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "replace_others"):
                self.sender.preview(self.aid, {"replace_others": bad})
        before = len(self.fake.requests)
        view = self.replace_view()  # no paper of Faculty's is on Gradescope yet, so nobody can have graded one
        self.assertEqual((self.lists(before), self.opened(before)), ([], []))
        self.assertEqual((view["replacing"], view["replacements"]), ({"grades": 0, "score_changes": 0, "same": 0, "same_by": [], "by": [], "listed": []}, []))
        self.assertEqual(view["summary"]["grades"], {"send": 4, "update": 0, "already": 0, "refused": 0, "held": 0, "replace": 0})
        self.assertEqual(self.send_replacing(view, 0)["plan"]["replacing"], 0)
        self.assertEqual(self.sender.drain(), 6)
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})


class OneResponse(GradescopeCase):
    """A regrade request's Apply sends that student's ONE grade (REGRADES, `only_responses`): never a paper, nothing else of the plan."""

    def one(self, key="q20", letter="A", **extra):
        return self.sender.preview(self.aid, {"only_responses": [self.response(key, letter)["id"]], **extra})

    def send_one(self, view, key="q20", letter="A", **extra):
        return self.sender.send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {},
                                           "only_responses": [self.response(key, letter)["id"]], **extra})

    def test_one_response_sends_that_grade_alone(self):
        self.send()
        self.sender.drain()
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "B", ["R4_3"])  # another changed grade: not part of this send
        view = self.one()
        self.assertEqual(view["only_responses"], [self.response("q20", "A")["id"]])
        self.assertEqual(view["summary"]["grades"], {"send": 0, "update": 1, "already": 0, "refused": 0, "held": 0})
        self.assertEqual(view["summary"]["papers"], {"upload": 0, "already": 1, "skipped": 0})
        self.assertNotEqual(view["fingerprint"], self.view()["fingerprint"])
        reads = len(self.fake.requests)
        self.one()
        version_b = str(self.versions["B"])
        self.assertFalse(any(version_b in r["path"] for r in self.fake.requests[reads:]))  # only Version A is read
        receipt = self.send_one(view)
        with connect(self.path) as db:
            plan = json.loads(db.execute("SELECT plan_json FROM frq_gradescope_sends WHERE id=?", (receipt["send_id"],)).fetchone()[0])
        self.assertEqual(plan["only_responses"], [self.response("q20", "A")["id"]])
        before = self.fake.state(self.qsub("q21", "B", self.bob))
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [3], score=2.0)
        self.assertEqual(self.fake.state(self.qsub("q21", "B", self.bob)), before)
        self.assertEqual(self.sender.status(self.aid)["coverage"]["grades"], {"final": 4, "sent": 3, "changed": 1, "not_sent": 0})
        self.assertNothingForbidden()

    def test_a_grade_already_there_is_already_and_nothing_is_sent(self):
        self.send()
        self.sender.drain()
        view = self.one()
        self.assertEqual(view["summary"]["grades"], {"send": 0, "update": 0, "already": 1, "refused": 0, "held": 0})
        with self.assertRaisesRegex(Conflict, "nothing to send"):
            self.send_one(view)

    def test_a_paper_faculty_never_uploaded_is_not_uploaded_for_one_grade(self):
        view = self.one()
        self.assertEqual(view["summary"]["papers"], {"upload": 0, "already": 0, "skipped": 1})
        self.assertEqual(view["summary"]["grades"]["held"], 1)
        self.assertIn("has not uploaded this student's paper", view["skipped"][0]["reason"])
        with self.assertRaisesRegex(Conflict, "nothing to send"):
            self.send_one(view)
        self.assertEqual(self.fake.uploads(), [])

    def test_one_response_is_named_exactly_once(self):
        rid = self.response("q20", "A")["id"]
        with self.assertRaisesRegex(ValueError, "exactly one response"):
            self.sender.preview(self.aid, {"only_responses": [rid, self.response("q21", "A")["id"]]})
        with self.assertRaisesRegex(ValueError, "Papers only"):
            self.sender.preview(self.aid, {"only_responses": [rid], "papers_only": True})
        with self.assertRaises(gs.NotFound):
            self.sender.preview(self.aid, {"only_responses": ["not-a-response"]})

    def test_a_published_exam_needs_its_confirmation_for_one_grade_too(self):
        self.send()
        self.sender.drain()
        self.decide("q20", "A", ["R3_3"])
        self.fake.assignments[self.container]["published"] = True
        view = self.one()
        self.assertEqual([(p["papers"], p["grades"]) for p in view["published"]], [(0, 1)])
        with self.assertRaisesRegex(ValueError, "are published on Gradescope"):
            self.send_one(view)
        self.send_one(view, published=[str(self.container)])
        self.sender.drain()
        self.assertGraded("q20", "A", self.alice, [3], score=2.0)

    def test_a_running_send_holds_one_grade(self):
        self.send()
        self.sender.drain()
        self.decide("q20", "A", ["R3_3"])
        self.decide("q21", "B", ["R4_3"])
        self.send()  # the plan-wide send, not yet sent
        view = self.one()
        self.assertTrue(view["running"])
        with self.assertRaisesRegex(Conflict, "still running"):
            self.send_one(view)


class RouteTests(GradescopeCase):
    def base(self):
        return f"/api/grading/gradescope/assignments/{self.aid}"

    def test_the_private_routes_preview_send_cancel_retry_and_list_problems(self):
        status = self.client.get(self.base())
        self.assertEqual(status.status_code, 200)
        self.assertEqual((status.json["configured"], status.json["sends"]), (True, []))
        self.assertEqual(self.fake.requests, [])  # the status never contacts Gradescope
        self.assertEqual(self.client.post(self.base() + "/preview", json={}).status_code, 403)  # same-origin writes only
        self.assertEqual(self.client.post(self.base() + "/preview", data="[]", content_type="application/json", headers=HEADERS).status_code, 400)
        view = self.client.post(self.base() + "/preview", json={}, headers=HEADERS)
        self.assertEqual(view.status_code, 200, view.json)
        self.assertNoUin(view.json)
        data = {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view.json["fingerprint"], "targets": {}}
        self.assertEqual(self.client.post(self.base() + "/send", json={**data, "confirmation": "ok"}, headers=HEADERS).status_code, 400)
        sent = self.client.post(self.base() + "/send", json=data, headers=HEADERS)
        self.assertEqual(sent.status_code, 201, sent.json)
        again = self.client.post(self.base() + "/send", json=data, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["send_id"]), (201, sent.json["send_id"]))
        stale = self.client.post(self.base() + "/send", json={**data, "request_id": str(uuid.uuid4())}, headers=HEADERS)
        self.assertEqual(stale.status_code, 409)
        send = f"{self.base()}/sends/{sent.json['send_id']}"
        routed = self.app.extensions["gradescope"]
        routed.process_one()
        self.assertEqual(self.client.post(send + "/cancel", json={}, headers=HEADERS).json["status"], "cancelled")
        self.assertEqual(self.client.post(send + "/retry", json={}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.get(f"{self.base()}/sends/unknown/problems").status_code, 404)
        problems = self.client.get(send + "/problems")
        self.assertEqual(problems.status_code, 200)
        self.assertEqual({p["status"] for p in problems.json["problems"]}, {"cancelled"})
        self.assertNoUin(problems.json)
        listed = self.client.get(self.base()).json
        self.assertEqual(listed["sends"][0]["counts"]["uploads"], {"accepted": 1, "cancelled": 1})
        self.assertEqual(listed["targets"][0]["container_id"], str(self.container))
        self.assertNothingForbidden()


class PageReading(unittest.TestCase):
    """The page readers alone, on the shapes of the real pages."""

    def test_export_order_follows_the_real_hierarchical_positions(self):
        # Question 76670350 of the sandbox's Version A rubric (2026-09-25), ids and positions as captured.
        rubric = {"rubric_items": [
            {"id": 295689621, "position": 0, "group_id": None, "question_id": 76670350, "weight": "4.0", "description": "Correct Answers"},
            {"id": 295689622, "position": 0, "group_id": 5063473, "question_id": 76670350, "weight": "2.0", "description": "Correct Dimensions"},
            {"id": 295689623, "position": 0, "group_id": 5063474, "question_id": 76670350, "weight": "2.0", "description": "Indicated not possible"},
            {"id": 295689624, "position": 1, "group_id": 5063473, "question_id": 76670350, "weight": "1.0", "description": "Rows only"},
            {"id": 295689625, "position": 1, "group_id": 5063474, "question_id": 76670350, "weight": "1.0", "description": "Stated not possible"},
            {"id": 295689626, "position": 2, "group_id": 5063473, "question_id": 76670350, "weight": "1.0", "description": "Columns only"},
            {"id": 295689627, "position": 2, "group_id": 5063474, "question_id": 76670350, "weight": "1.0", "description": "No reason"},
            {"id": 295689628, "position": 3, "group_id": None, "question_id": 76670350, "weight": "0.0", "description": "No Answers"},
            {"id": 295689629, "position": 3, "group_id": 5063473, "question_id": 76670350, "weight": "0.0", "description": "Neither"},
            {"id": 295689630, "position": 3, "group_id": 5063474, "question_id": 76670350, "weight": "1.0", "description": "Work"},
            {"id": 295689631, "position": 4, "group_id": 5063474, "question_id": 76670350, "weight": "0.0", "description": "Incorrect"},
            {"id": 1, "position": 0, "group_id": None, "question_id": 76670351, "weight": "2.0", "description": "Another question"}],
            "rubric_item_groups": [{"id": 5063474, "position": 2, "description": "Part B", "question_id": 76670350},
                                   {"id": 5063473, "position": 1, "description": "Part A", "question_id": 76670350}]}
        order = gw.export_order(rubric, 76670350)
        self.assertEqual([i["id"] for i in order], ["295689621", "295689622", "295689624", "295689626", "295689629",
                                                    "295689623", "295689625", "295689627", "295689630", "295689631", "295689628"])
        self.assertEqual(order[1]["name"], "Part A: Correct Dimensions")
        self.assertEqual(order[1]["points"], Decimal("2.0"))
        self.assertEqual(gw.gradescope_score({"weight": "4.0", "scoring_type": "positive"}, order, {"295689622", "295689625"}), Decimal("3.0"))
        self.assertEqual(gw.gradescope_score({"weight": "4.0", "scoring_type": "negative"}, order, {"295689624"}, Decimal("0.5")), Decimal("3.5"))
        self.assertEqual(gw.gradescope_score({"weight": "4.0", "scoring_type": "positive", "ceiling": True}, order, {"295689621", "295689622"}), Decimal("4.0"))

    def test_the_account_page_is_read_by_term_and_only_instructor_courses(self):
        text = ('<div id="account-show"><h2 class="pageHeading">Instructor Courses</h2><div class="courseList">'
                '<div class="courseList--term pageSubheading" role="heading" aria-level="3">Fall 2026</div><div class="courseList--coursesForTerm">'
                '<a class="courseBox" href="/courses/11"><h3 class="courseBox--shortname">140-507</h3><div class="courseBox--name">Math &amp; More</div></a>'
                '</div><div class="courseList--term pageSubheading">Spring 2026</div><div class="courseList--coursesForTerm">'
                '<a class="courseBox courseBox--inactive" data-x="1" href="/courses/12"><h3 class="courseBox--shortname">140-507</h3><div class="courseBox--name">Old</div></a>'
                '</div></div><h2 class="pageHeading">Student Courses</h2><div class="courseList"><div class="courseList--term pageSubheading">Fall 2026</div>'
                '<a class="courseBox" href="/courses/13"><h3 class="courseBox--shortname">140-507</h3></a></div></div>')
        self.assertEqual(gw.course_list(text), [{"id": "11", "short": "140-507", "name": "Math & More", "term": "Fall 2026"},
                                                {"id": "12", "short": "140-507", "name": "Old", "term": "Spring 2026"}])

    def test_a_question_s_list_of_submissions_is_read_strictly(self):
        # The shape read on the real site 2026-10-01 (140-508, Quiz 3 Version A, Question 1): a graded row and an ungraded one.
        head = ('<table class="table" id="question_submissions"><thead><tr><th></th><th>User</th><th>Last Graded By</th><th>Sections</th><th>Score</th>'
                '<th>Graded?</th></tr></thead>')
        row = ('<tr><td>{n}</td><td class="table--primaryLink"><a href="/courses/1354896/questions/76865443/submissions/{q}/grade">Ryan S Example '
               '(ryan@example.invalid)</a></td><td>{by}</td><td class="table--secondaryLink table--secondaryLink-small"><div class="sectionsColumnCell">'
               '<div class="sectionsColumnCell--section"><span class="sectionsColumnCell--sectionSpan" title="math-140-508"><a href="/courses/1354896/'
               'questions/76865443/submissions/{q}/grade?section_ids=1278349">math-140-508</a></span></div></div></td><td>{score}</td><td>{check}</td></tr>')
        graded = row.format(n=1, q=4172037010, by="Ada &amp; Fair", score="2.0", check='<i class="fa fa-check"></i>')
        ungraded = row.format(n=2, q=4172037012, by="", score="", check="")
        self.assertEqual(gw.question_rows(f"<main>{head}{graded}{ungraded}</table></main>", "1354896", 76865443),
                         {"4172037010": {"graded": True, "by": "Ada & Fair", "score": Decimal("2.0")},
                          "4172037012": {"graded": False, "by": "", "score": None}})
        for broken in (f"{head.replace('Last Graded By', 'Grader')}{graded}</table>", f"{head}{graded.replace('/76865443/', '/1/')}</table>",
                       f"{head}{graded.replace('<td>2.0</td>', '<td>two</td>')}</table>", f"{head}{graded.replace('<td>1</td>', '')}</table>",
                       "<p>There are no submissions.</p>"):
            with self.subTest(broken=broken[-80:]), self.assertRaises(gw.GradescopeError):
                gw.question_rows(broken, "1354896", 76865443)

    def test_the_roster_keeps_students_only_with_their_gradescope_ids(self):
        with FakeGradescope() as fake:
            course = fake.add_course("140-501", "Math", "Fall 2026")
            fake.add_member(course, "Ann O'Neil \"Jr\"", "001234567", "ann@example.invalid")
            fake.add_member(course, "Tess Assistant", "001234567", "ta@example.invalid", role=2)
            rows = gw.roster_rows(fake._roster(course))
        self.assertEqual(rows, [{"user_id": str(min(m["user_id"] for m in fake.members[course])), "sid": "001234567", "email": "ann@example.invalid",
                                 "name": "Ann O'Neil \"Jr\""}])

    def test_only_the_gradescope_host_or_a_loopback_test_server_is_reached(self):
        self.assertEqual(gw.base_url(None), "https://www.gradescope.com")
        self.assertEqual(gw.base_url("http://127.0.0.1:4100/"), "http://127.0.0.1:4100")
        for bad in ("https://gradescope.example.com", "http://10.0.0.5:80", "http://127.0.0.1:4100/path", "ftp://127.0.0.1", "http://user@127.0.0.1:1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gw.base_url(bad)
        session = gw.Session(gw.Settings(credentials_file="/nonexistent", base="http://127.0.0.1:9"))
        with self.assertRaises(gw.GradescopeError):
            session._url("//evil.example.com/x")
        self.assertTrue(gw.forbidden("  MATH 140  for instructors ", "x"))
        # Matched loosely since the review of 2026-09-25: a term suffix or a dash must not make the coordinator's course pickable.
        for variant in ("Math 140 for Instructors Fall", "Math 140 for Instructors - Fall 2026", "MATH 140 Instructor Resources", "Math 151 for Instructors"):
            with self.subTest(variant=variant):
                self.assertTrue(gw.forbidden("Math 140", variant))
        self.assertFalse(gw.forbidden("140-507", "Math for Business and Social Sciences"))
        self.assertFalse(gw.forbidden("140", "Faculty upload test"))


class AutomaticFinals(Pipeline):
    """A grade the grading team finalized automatically (no instructor review) is final for the sender exactly as for the export:
    it is sent with its items, and Faculty's own provenance note ("Finalized automatically ...") never reaches Gradescope.
    Cloud Standard's fixture is borrowed without its tests (they run in their own module)."""
    current_standard, legacy_workflow = True, False
    opinions = standard_fixture.CloudStandard.opinions
    scripted = standard_fixture.CloudStandard.scripted

    def setUp(self):
        super().setUp()
        saved = self.frq.settings()
        settings = saved["settings"]
        settings["endpoints"].append(endpoint(self.host, "delta-vision"))
        settings["tiers"]["cloud_standard"].update(verifier="gamma-vision", flag_reviewer="delta-vision")
        self.frq.save_settings({"version": saved["version"], "settings": settings})
        # The Pipeline rubric, as Gradescope's export numbers it: its question 2, lines 2.1 to 2.4.
        numbered = {**RULES, "items": [{**item, "original_source_text": json.dumps({"description": item["description"], "rubric_number": f"2.{i + 1}",
                                                                                    "point_value": str(item["points"])})} for i, item in enumerate(RULES["items"])]}
        q = self.frq.assignment(self.plan["id"])["questions"][0]
        q = self.frq.import_rubric(self.plan["id"], self.qid, {"version": q["version"], "source_text": "Gradescope evaluation export", "rules": numbered})["questions"][0]
        self.frq.approve_rubric(self.plan["id"], self.qid, {"version": q["version"], "rubric_id": q["rubric_id"], "sha256": q["rubric"]["sha256"], "acknowledge_rules": True})

    def test_an_automatic_final_is_sent_with_its_items_and_never_its_note(self):
        response, _ = self.scripted()
        self.assertEqual(response["acceptance"]["basis"], "team_consensus")
        record = json.loads(self.frq.export(self.plan["id"])[0])["records"][0]
        self.assertEqual((record["final"]["status"], record["final"]["basis"], record["final"]["score"]["score"]), ("automatic", "team_consensus", 10))
        self.assertTrue(record["final"]["comment"].startswith("Finalized automatically"))
        self.enterContext(patch.object(gs.process, "merge", side_effect=lambda contents: pdf(len(contents))))
        with FakeGradescope() as fake:
            course = fake.add_course("140-501", "Math 140", "Fall 2026")
            with connect(self.path) as db:
                uin = db.execute("SELECT uin FROM student_enrollments WHERE id=?", (self.enrollment,)).fetchone()[0]
            student = fake.add_member(course, "Alice Example", uin, "alice@example.invalid")
            quiz = fake.add_assignment(course, "Quiz 1", 2)
            fake.add_question(quiz, 1, "Question 0", 2, OTHER)
            question = fake.add_question(quiz, 2, "Question 1", 10, [(item["points"], item["description"]) for item in RULES["items"]])
            fake.add_container(course, "Common Exam 1", "AB", 2)
            directory = tempfile.mkdtemp(dir=self.directory.name)
            path = os.path.join(directory, "gradescope.json")
            descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.write(descriptor, json.dumps({"email": EMAIL, "password": PASSWORD}).encode())
            os.close(descriptor)
            sender = GradescopeSender(self.frq, settings=gw.Settings(credentials_file=path, base=fake.url, pace=0))
            section = self.section["id"]
            view = sender.preview(self.plan["id"], {})
            self.assertEqual(view["sections"][0]["problem"], "Choose the Gradescope assignment this goes to in this course.")  # two exams: no guess
            targets = {section: {"course_id": str(course), "container_id": str(quiz)}}
            view = sender.preview(self.plan["id"], {"targets": targets})
            self.assertEqual(view["sections"][0]["versions"], {"": str(quiz)})
            self.assertEqual((view["summary"]["final"], view["summary"]["grades"]["send"], view["refused"]), (1, 1, []))
            sender.send(self.plan["id"], {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": targets})
            self.assertEqual(sender.drain(), 2)
            qsub = fake.qsub_of(quiz, student, question)
            self.assertEqual(fake.state(qsub), ([fake.rubric_items(question)[0]["id"]], None, None, 10.0))
            self.assertNotIn("Finalized automatically", json.dumps(fake.requests))
            self.assertEqual(fake.comment_hits, [])
            with connect(self.path) as db:
                self.assertEqual([tuple(r) for r in db.execute("SELECT decision,status FROM frq_gradescope_grades")], [("automatic", "accepted")])
            # The Grading index hands the card its read's decision rows (PERFORMANCE, 2026-10-02): the same answer as reading them.
            from grading.progress import decision_rows
            from grading.service import gradescope_brief
            rows = self.frq.responses(self.plan["id"])
            with connect(self.path) as db:
                decisions = decision_rows(db, self.plan["id"], rows)
                self.assertEqual(set(GradescopeSender._grade_coverage(db, self.plan["id"], rows, decisions=decisions).values()), {"sent"})
                self.assertEqual(GradescopeSender._grade_coverage(db, self.plan["id"], rows, decisions=decisions),
                                 GradescopeSender._grade_coverage(db, self.plan["id"], rows))
                self.assertEqual(gradescope_brief(db, self.plan["id"], rows, decisions), gradescope_brief(db, self.plan["id"], rows))


class ManagedStorage(GradescopeCase):
    """The deployed shape: course storage with its NVMe and HDD roots, so the sender's lock and every page it reads of the reviewed
    paper go through the storage manager. Built fresh (a managed workspace is not copied)."""
    managed = True

    @classmethod
    def setUpClass(cls):
        pass

    def setUp(self):
        self.build()
        self.aid = self.plan["id"]
        self.gradescope()

    def test_a_send_under_course_storage(self):
        self.assertIsNotNone(self.inbox.storage)
        self.send()
        other = GradescopeSender(self.frq, settings=self.settings)
        with self.sender.lock(), self.assertRaisesRegex(Conflict, "Another Gradescope sender"):
            other.drain()
        self.assertEqual(self.sender.drain(), 6)
        self.assertEqual({u["status"] for u in self.units("uploads")} | {g["status"] for g in self.units()}, {"accepted"})
        self.assertGraded("q21", "B", self.bob, [3, 5], score=5.0)


class TwoSections(GradescopeCase):
    """An exam without versions sat by two sections, each with its own Gradescope course (as 140-507 and 140-508 are): each paper
    goes to its own section's course, the links are recorded per section, and each course's rubric is matched on its own."""

    @classmethod
    def setUpClass(cls):
        pass

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        created = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Calculus", "term": "Fall", "year": 2026, "sections": [501, 502], "multiple_sections": True,
                                                         "is_current": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        group = self.client.get("/api/state").json["course_groups"][0]
        for number, rows in ((501, [STUDENTS[0]]), (502, [STUDENTS[1]])):
            result = self.client.post(f"/api/course-groups/{group['id']}/sections/{number}/roster", data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
            self.assertEqual(result.status_code, 201, result.json)
        self.documents, self.scans = self.app.extensions["documents"], self.app.extensions["document_scans"]
        self.inbox, self.rosters = self.app.extensions["inbox"], self.app.extensions["rosters"]
        self.path = self.rosters.path
        self.exams = self.app.extensions.get("exams") or ExamService(self.rosters, self.documents, self.scans, self.app.extensions["seating"],
                                                                    self.app.extensions["response_cards"])
        catalog = self.documents.catalog()
        self.assignment = self.documents.create_assignment(catalog["definitions"][0]["id"], "exam-one", "Exam 1")
        self.sections = {s["number"]: s for s in catalog["sections"]}
        with connect(self.path) as db:
            term_id = self.sections[501]["term_id"]
            self.students = {n: db.execute("SELECT enrollment_id FROM roster_memberships WHERE section_id=?", (s["id"],)).fetchone()[0] for n, s in self.sections.items()}
            self.uins = dict(db.execute("SELECT id,uin FROM student_enrollments"))
        self.event = self.exams.create({"term_id": term_id, "assignment_id": self.assignment["id"], "section_ids": [self.sections[501]["id"], self.sections[502]["id"]]})
        self.version_id = self.document_version("A")
        for number in (501, 502):
            self.section, self.enrollment = self.sections[number], self.students[number]
            self.reviewed_source(batch=self.ready_batch(enrollment_id=self.enrollment), changed=str(number))
        self.frq = self.app.extensions["frq_grading"]
        self.plan = self.frq.create_assignment({"event_id": self.event["id"]})
        self.templates, self.seats, self.looks_like = {}, {}, {}
        self.patches()
        self.q = {("q20", ""): self.question("q20", "", 10, "20", 3, Q20), ("q21", ""): self.question("q21", "", 8, "21", 4, Q21)}
        finalize_rubrics(self.frq, self.plan["id"])
        self.grade_all()
        for key, lines in (("q20", {501: ["R3_2"], 502: ["R3_3"]}), ("q21", {501: ["R4_2", "R4_6"], 502: ["R4_3", "R4_5"]})):
            for response in self.frq.responses(self.plan["id"], self.q[(key, "")]):
                full = self.frq.response(response["id"])
                number = next(n for n, e in self.students.items() if e == response["enrollment_id"])
                self.frq.save_review(full["id"], {"version": full["version"], "result_id": full["current_result_id"], "status": "approved",
                                                  "confirm_review": True, "selected_items": lines[number]})
        self.aid = self.plan["id"]
        self.fake = FakeGradescope().start()
        self.addCleanup(self.fake.stop)
        self.courses, self.exam, self.gq, self.users = {}, {}, {}, {}
        for number in (501, 502):
            course = self.courses[number] = self.fake.add_course(f"140-{number}", "Mathematics for Business and Social Sciences", "Fall 2026")
            self.users[number] = self.fake.add_member(course, f"Student {number}", self.uins[self.students[number]], f"s{number}@example.invalid")
            self.exam[number] = self.fake.add_assignment(course, "Common Exam 1 - Partial Credit", 2)
            self.gq[(number, "q20")] = self.fake.add_question(self.exam[number], 3, "Question 20", 10, Q20)
            self.gq[(number, "q21")] = self.fake.add_question(self.exam[number], 4, "Question 21 a and b", 8, Q21)
        self.credentials = os.path.join(tempfile.mkdtemp(dir=self.directory.name), "gradescope.json")
        self.write_credentials({"email": EMAIL, "password": PASSWORD})
        self.settings = gw.Settings(credentials_file=self.credentials, base=self.fake.url, pace=0)
        self.sender = GradescopeSender(self.frq, settings=self.settings)

    def test_each_section_goes_to_its_own_course_and_rubric(self):
        # 502's q21 differs from 501's on Gradescope (the real 140-507 label lines were 1.5 where the others had 1).
        next(i for i in self.fake.rubric_items(self.gq[(502, "q21")]) if i["description"] == "Correct number of rows, but not columns")["weight"] = 1.5
        view = self.view()
        self.assertEqual([(s["section"], s["course_id"], s["container_id"], s["versions"], s["proposed"]) for s in view["sections"]],
                         [(n, str(self.courses[n]), str(self.exam[n]), {"": str(self.exam[n])}, True) for n in (501, 502)])
        self.assertEqual([(r["section"], r["question"], r["reason"]) for r in view["refused"]], [(502, "q21", "R4_3 is worth 1 in Faculty and 1.5 on Gradescope.")])
        self.send(view)
        self.assertEqual(self.sender.drain(), 5)
        for number in (501, 502):
            submission = self.fake.submission_of(self.exam[number], self.users[number])
            self.assertEqual(submission["owners"], [self.users[number]])
            self.assertIsNone(self.fake.submission_of(self.exam[501 if number == 502 else 502], self.users[number]))
        items = lambda n, key: [i["id"] for i in self.fake.rubric_items(self.gq[(n, key)])]
        self.assertEqual(self.fake.state(self.fake.qsub_of(self.exam[501], self.users[501], self.gq[(501, "q21")]))[0], sorted([items(501, "q21")[1], items(501, "q21")[5]]))
        self.assertEqual(self.fake.state(self.fake.qsub_of(self.exam[502], self.users[502], self.gq[(502, "q20")]))[0], [items(502, "q20")[2]])
        self.assertEqual(self.fake.state(self.fake.qsub_of(self.exam[502], self.users[502], self.gq[(502, "q21")]))[0], [])
        targets = {t["section"]: (t["course_id"], t["container_id"]) for t in self.sender.status(self.aid)["targets"]}
        self.assertEqual(targets, {n: (str(self.courses[n]), str(self.exam[n])) for n in (501, 502)})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_courses").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_gradescope_targets").fetchone()[0], 2)
        # Nothing new to choose: the next send records no new links.
        again = self.view()
        self.assertEqual([s["proposed"] for s in again["sections"]], [False, False])

    def test_a_section_left_out_is_not_sent_and_is_offered_again_next_time(self):
        """requirement, 2026-09-25: send one section first and check it before the rest."""
        out = {self.sections[502]["id"]: {"leave_out": True}}
        full, before = self.view(), len(self.fake.requests)
        view = self.view(out)
        self.assertFalse(any(f"/courses/{self.courses[502]}/" in r["path"] for r in self.fake.requests[before:]))  # its course is not even read
        self.assertNotEqual(view["fingerprint"], full["fingerprint"])
        entry = {s["section"]: s for s in view["sections"]}
        self.assertEqual((entry[502]["left_out"], entry[502]["problem"], entry[502]["containers"]), (True, "", []))
        self.assertNotIn("left_out", entry[501])
        self.assertEqual(view["summary"]["papers"], {"upload": 1, "already": 0, "skipped": 0, "left_out": 1})
        self.assertEqual((view["summary"]["grades"]["send"], view["summary"]["grades"]["left_out"]), (2, 2))
        self.assertEqual((view["skipped"], sorted(view["targets"])), ([], [self.sections[501]["id"]]))
        with self.assertRaisesRegex(Conflict, "Something changed since the preview"):
            self.send(view)  # the same preview with the section included is another preview
        self.send(view, targets=out)
        self.sender.drain()
        self.assertIsNotNone(self.fake.submission_of(self.exam[501], self.users[501]))
        self.assertIsNone(self.fake.submission_of(self.exam[502], self.users[502]))
        with connect(self.path) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT section_id FROM frq_gradescope_courses")], [self.sections[501]["id"]])
        plan = self.sender.status(self.aid)["sends"][0]["plan"]
        self.assertEqual([t["section"] for t in plan["targets"] if t.get("left_out")], [502])
        again = self.view()
        self.assertEqual([(s["section"], s["proposed"], s.get("left_out", False)) for s in again["sections"]], [(501, False, False), (502, True, False)])
        self.assertEqual(again["summary"]["papers"], {"upload": 1, "already": 1, "skipped": 0})
        self.send(again)
        self.sender.drain()
        self.assertIsNotNone(self.fake.submission_of(self.exam[502], self.users[502]))


class ReviewFixes(GradescopeCase):
    """What the review of 2026-09-25 found, held down: a refused sign-in pauses the whole sender, two sends racing over the same
    papers answer 409 (never 503), a cancel landing while a unit is on its way cancels that unit instead of stranding it, and a
    grade never checks two items of a Gradescope group whose items are mutually exclusive."""

    def logins(self):
        return len([r for r in self.fake.requests if r["method"] == "POST" and r["path"] == "/login"])

    def test_a_refused_sign_in_is_tried_once_then_the_sender_waits_for_a_new_sign_in(self):
        self.send()
        before = self.logins()
        saved = json.loads(open(self.credentials).read())
        self.write_credentials({**saved, "password": "changed on gradescope"})
        self.sender.drain()
        self.assertEqual(self.logins() - before, 1)  # one refusal, not one per waiting paper and grade
        self.ready_now()
        self.assertEqual(self.sender.drain(), 0)  # paused: nothing is tried, however many units wait
        self.assertEqual(self.logins() - before, 1)
        self.assertIn("did not accept the saved sign-in", self.sender.status(self.aid)["sends"][0]["waiting"])
        self.write_credentials(saved)  # saved again: the sender goes on at once
        self.ready_now()
        self.sender.drain()
        self.assertEqual({u["status"] for u in self.units("uploads")}, {"accepted"})
        self.assertEqual({g["status"] for g in self.units()}, {"accepted"})

    def test_two_sends_racing_over_the_same_papers_answer_409(self):
        stale = self.sender.preview(self.aid, {})
        other = GradescopeSender(self.frq, settings=self.settings)
        original = self.sender._plan
        state = {"done": False}

        def plan(aid, requested, **mode):
            result = original(aid, requested, **mode)
            if not state["done"]:  # another tab sends and finishes while this send is still reading
                state["done"] = True
                view = other.preview(aid, {})
                other.send(aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": view["fingerprint"], "targets": {}})
                other.drain()
            return result
        self.sender._plan = plan
        with self.assertRaises(Conflict):
            self.sender.send(self.aid, {"request_id": str(uuid.uuid4()), "confirmation": "send", "fingerprint": stale["fingerprint"], "targets": {}})

    def test_a_cancel_while_a_unit_is_on_its_way_cancels_it_instead_of_stranding_it(self):
        receipt = self.send()
        original = self.sender._claim
        state = {"done": False}

        def claim(unit):
            claimed = original(unit)
            if claimed and not state["done"]:
                state["done"] = True
                self.sender.cancel(self.aid, receipt["send_id"])  # Cancel is clicked while this paper is on its way
                self.fake.fail("GET", r"/submissions$", ("status", 500), times=1)  # and the next read of Gradescope fails once
            return claimed
        self.sender._claim = claim
        self.sender.process_one()
        self.sender._claim = original
        self.ready_now()
        self.sender.drain()
        self.assertFalse([u for u in self.units("uploads") + self.units() if u["status"] in ("pending", "sending")])
        view = self.view()  # nothing of the cancelled send holds a paper back
        self.assertFalse([s for s in view["skipped"] if "earlier send" in s["reason"]])
        self.assertGreater(view["summary"]["papers"]["upload"], 0)


class CoverageFromDecisions(unittest.TestCase):
    """`_grade_coverage` with the caller's `decision_rows` (PERFORMANCE, 2026-10-02) answers exactly as when it reads each final grade's
    decision itself, reads neither map then, and reads them when the rows it was given do not cover every final grade."""

    class Catalog:
        def __init__(self, tables):
            self.tables, self.statements = tables, []

        def execute(self, sql, args=()):
            self.statements.append(sql)
            return iter(next((rows for key, rows in self.tables.items() if key in sql), []))

    ROWS = [{"id": "r1", "state": "approved", "current_review_id": "v1", "current_result_id": "x1"},
            {"id": "r2", "state": "auto_accepted", "current_review_id": None, "current_result_id": "x2"},
            {"id": "r3", "state": "auto_accepted", "current_review_id": None, "current_result_id": "x3"},  # its acceptance is no longer active
            {"id": "r4", "state": "manual", "current_review_id": "v4", "current_result_id": "x4"},
            {"id": "r5", "state": "approved", "current_review_id": "v5", "current_result_id": "x5"},
            {"id": "r6", "state": "proposed", "current_review_id": None, "current_result_id": "x6"},
            {"id": "r7", "state": "approved", "current_review_id": "v7", "current_result_id": "x7"}]
    DECISIONS = {"r1": {"kind": "review", "decision_id": "v1", "result_id": "x1"}, "r2": {"kind": "automatic", "decision_id": "a2", "result_id": "x2"},
                 "r3": {"kind": None, "decision_id": None, "result_id": None}, "r4": {"kind": "review", "decision_id": "v4", "result_id": "x4"},
                 "r5": {"kind": "review", "decision_id": "v5", "result_id": "x5"}, "r6": {"kind": None, "decision_id": None, "result_id": None},
                 "r7": {"kind": "review", "decision_id": "v7", "result_id": "x7"}}

    def catalog(self):
        return self.Catalog({
            "FROM frq_reviews v": [("v1", "x1"), ("v4", "x4"), ("v5", "x5"), ("v7", "x7")],
            "FROM frq_all_acceptances": [("x2", "a2")],
            "FROM frq_gradescope_grades": [("r1", "v1", "x1", "{}"), ("r2", "a2", "x2", '{"items": {}}'), ("r3", "a3", "x3", "{}"),
                                           ("r4", "v0", "x4", "{}"), ("r7", "v7", "x7", '{"comment": "Sent with a note"}')]})

    def test_the_same_states_with_or_without_the_callers_decisions(self):
        self.enterContext(patch.object(GradescopeSender, "expected_comments", return_value={}))
        read, given = self.catalog(), self.catalog()
        expected = {"r1": "sent", "r2": "sent", "r3": "changed", "r4": "changed", "r5": "not_sent", "r7": "changed"}
        self.assertEqual(GradescopeSender._grade_coverage(read, "p", self.ROWS), expected)
        self.assertEqual(GradescopeSender._grade_coverage(given, "p", self.ROWS, decisions=self.DECISIONS), expected)
        self.assertEqual([sql for sql in given.statements if "frq_reviews" in sql or "frq_all_acceptances" in sql], [])
        partial = self.catalog()
        self.assertEqual(GradescopeSender._grade_coverage(partial, "p", self.ROWS, decisions={"r1": self.DECISIONS["r1"]}), expected)
        self.assertEqual(len([sql for sql in partial.statements if "frq_reviews" in sql or "frq_all_acceptances" in sql]), 2)
        # Nothing final: no receipt is read at all.
        none = self.catalog()
        self.assertEqual(GradescopeSender._grade_coverage(none, "p", [self.ROWS[5]]), {})
        self.assertEqual([sql for sql in none.statements if "frq_gradescope_grades" in sql], [])


class MutuallyExclusiveGroups(unittest.TestCase):
    def test_a_grade_checking_two_items_of_a_mutually_exclusive_group_is_refused(self):
        order = [{"id": "1", "points": Decimal("2"), "group_id": "", "exclusive": False},
                 {"id": "2", "points": Decimal("1"), "group_id": "g", "exclusive": True},
                 {"id": "3", "points": Decimal("1"), "group_id": "g", "exclusive": True}]
        mapping = {"question": {"weight": "4.0", "scoring_type": "positive", "floor": True, "ceiling": True}, "order": order,
                   "items": {"R1": order[0], "R2": order[1], "R3": order[2]}}
        refused, reason = gs.expected(mapping, {"score": {"selected": ["R2", "R3"], "adjustments": [], "score": 2.0}})
        self.assertIsNone(refused)
        self.assertIn("mutually exclusive", reason)
        chosen, reason = gs.expected(mapping, {"score": {"selected": ["R1", "R2"], "adjustments": [], "score": 3.0}})
        self.assertIsNone(reason)
        self.assertEqual(chosen[0], ["1", "2"])
