"""Exams printed without labels (requirement, 2026-09-24): covers found at the side-count cadence, read, matched and registered.

Synthetic pages and roster; the local OCR runtime is replaced by scripted readings keyed on each page's retained image."""
import io
import json
import re
import tempfile
import unittest
import uuid
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter

from app import create_app
from rosters import connect
from teach_inbox.service import sha
from document_identity import unlabeled
from document_identity.unlabeled import cover_fields, next_batch, next_read, printed_version
from bubble_fixture import other_page, pdf, sheet
from test_rosters import COLUMNS, csv_bytes

HEADERS = {"X-Teaching-Request": "1", "Origin": "http://localhost"}
KEY = {number: "ABCDE"[number % 5] for number in range(1, 11)}
STUDENTS = (("123456789", "Example, Alice Marie"), ("223456780", "Example, Bob"), ("987650001", "Nguyen, Carol"), ("555000111", "O'Brien, Dylan James"))


def cover_text(name, uin, section="501"):
    return f"Math 140\nCommon Exam I, Fall 2026\n\nFirst and Last Name (printed): {name}\n\nUIN: {uin}\n\nSection Number: {section}\n\nInstructor: A Teacher"


class Parsing(unittest.TestCase):
    def test_fields_and_version_come_from_the_printed_field_names(self):
        cover, fields = cover_fields(cover_text("Jane Doe", "123 456 789", "unknown"))
        self.assertEqual((cover, fields), (True, {"name": "Jane Doe", "uin": "123 456 789", "section": "unknown"}))
        cover, fields = cover_fields("First and Last Name (printed): Jane Doe UIN: 123456789 Section Number: 501 Instructor: A Teacher")
        self.assertEqual((cover, fields), (True, {"name": "Jane Doe", "uin": "123456789", "section": "501"}))  # One line, as OCR may read it.
        self.assertEqual(cover_fields("Write the equation of the line passing through the points (4, -8) and (4, 8).")[0], False)
        self.assertEqual(cover_fields("Name the matrix M.")[0], False)  # Both printed field names, or it is no cover.
        self.assertEqual(printed_version("Instructor: A Teacher\n\nMath 140\nCommon Exam I Version C, Fall 2026\n\nDirections:"), "C")
        self.assertEqual(printed_version("Directions: On the lines above print your name"), "")
        self.assertEqual(cover_fields("x" * 5000 + cover_text("Late", "1"))[0], False)  # Only the first 2,000 characters are looked at.

    def test_the_side_count_is_the_cadence_and_a_cover_that_looks_like_one_is_read_too(self):
        def run(covers, count, sides, likeness=None):
            pages = [{"id": f"p{index}", "status": "unreadable"} for index in range(count)]
            reads, order = {}, []
            while (page := next_read([pages], reads, sides, likeness)) is not None:
                order.append(int(page["id"][1:]))
                reads[page["id"]] = int(page["id"][1:]) in covers
            return order
        # Every exam has its sides: only the covers are read.
        self.assertEqual(run({0, 3, 6, 9}, 12, 3), [0, 3, 6, 9])
        # A short exam: the page where the next cover should be is not one, so the pages after the last cover are read in turn.
        self.assertEqual(run({0, 2, 5}, 8, 3), [0, 3, 1, 2, 5])
        # Two short exams that add up to one: the cadence alone would skip the cover between them. It looks like a cover, so it is read.
        self.assertEqual(run({0, 3, 6}, 12, 6), [0, 6])
        self.assertEqual(run({0, 3, 6}, 12, 6, lambda page, covers: .9 if page["id"] == "p3" else .1), [0, 6, 3])
        # Blank sides keep their place in the count and are never read.
        pages = [{"id": "p0", "status": "unreadable"}, {"id": "p1", "status": "blank"}, {"id": "p2", "status": "unreadable"}]
        self.assertEqual(next_read([pages], {"p0": True}, 2, None)["id"], "p2")

    def test_a_batch_is_the_pages_the_side_count_points_to(self):
        pages = [{"id": f"p{index}", "status": "unreadable"} for index in range(12)]
        batch = lambda reads, size: [page["id"] for page in next_batch([pages], reads, 3, size)]
        # Nothing read yet: the first page, then where every next paper should start, in one model load.
        self.assertEqual(batch({}, 16), ["p0", "p3", "p6", "p9"])
        self.assertEqual(batch({}, 2), ["p0", "p3"])
        # p3 was read and is no first page: the batch goes on from what was actually read, page by page.
        self.assertEqual(batch({"p0": True, "p3": False}, 3), ["p1", "p4", "p7"])
        self.assertEqual(batch({f"p{index}": index % 3 == 0 for index in range(12)}, 4), [])


class UnlabeledExams(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.client = self.app.test_client()
        self.inbox, self.scans, self.documents, self.exams = (self.app.extensions[name] for name in ("inbox", "document_scans", "documents", "exams"))
        self.path = self.app.config["DATABASE"]
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [501, 502],
                                                       "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        state = self.client.get("/api/state").json
        rows = [["", name, uin, "Registered", "", "", "", "", "", "", "", f"s{index}@example.test"] for index, (uin, name) in enumerate(STUDENTS)]
        self.assertEqual(self.client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/501/roster",
                                          data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code, 201)
        self.assignment = self.client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "test-1",
                                                                            "title": "Test 1"}, headers=HEADERS).json
        catalog = self.client.get("/api/seating/catalog").json
        self.section = next(item for item in catalog["sections"] if item["number"] == 501)
        with connect(self.path) as db:
            self.students = {name: db.execute("SELECT m.enrollment_id FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id WHERE e.uin=? AND m.section_id=?",
                                               (uin, self.section["id"])).fetchone()[0] for uin, name in STUDENTS}
        self.version = self.documents.register_stamp_pages(self.assignment["id"], 1)
        self.event = self.exams.create({"term_id": self.section["term_id"], "assignment_id": self.assignment["id"], "section_ids": [self.section["id"]]})
        self.texts, self.titles = {}, {}

    def labels(self, *names):
        """An issued stamps-only label job for these students, and its label pages."""
        batch = self.documents.create_batch(self.version["id"], self.section["id"], len(names), str(uuid.uuid4()), students=[self.students[name] for name in names])
        self.assertTrue(self.documents.build_one())
        self.assertEqual(self.documents.get_batch(batch["id"])["status"], "ready")
        return batch, PdfReader(io.BytesIO(self.documents.download(batch["id"])))

    def submit(self, content):
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content}], source="email", sender="scanner",
                                idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        for _ in range(4):
            if not self.scans.process_one():
                break
        return job

    def page_index(self, content):
        """The page a retained page PDF or page image (what the first-page reader is given) belongs to."""
        with connect(self.path) as db:
            return db.execute("""SELECT sp.source_page_index FROM scan_pages sp LEFT JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id
                                  WHERE sp.content_sha256=:h OR a.sha256=:h""", {"h": sha(content)}).fetchone()[0]

    def bands(self, content, band="top"):
        index = self.page_index(content)
        return {"reads": [{"rotation": 0, "text": self.texts.get(index, f"Question {index}. Solve for x.")}, {"rotation": 180, "text": ""}],
                "source_sha256": sha(content), "model_id": "fixture"}

    def covers(self, pages, title_box, words, title_unless=None):
        """The batch reader: each page's band text (a cover's carries the printed Name and UIN), and its title box for a cover whose
        band has no version."""
        self.assertEqual((list(title_box), list(words), title_unless), ([.12, .17, .88, .36], ["name", "UIN"], unlabeled.VERSION))
        self.batches.append([self.page_index(content) for content in pages])
        result = []
        for content in pages:
            index = self.page_index(content)
            text = self.texts.get(index, f"Question {index}. Solve for x.")
            cover = cover_fields(text)[0]
            reads = [{"kind": "band", "rotation": 0, "text": text}]
            if cover and not re.search(title_unless, text, re.I):
                reads.append({"kind": "title", "rotation": 0, "text": self.titles.pop(0) if self.titles else ""})
            if not cover:  # Read turned too, as the worker does when the upright band lacks the field names.
                reads.append({"kind": "band", "rotation": 180, "text": ""})
            result.append({"reads": reads, "source_sha256": sha(content)})
        return {"pages": result, "model_id": "fixture", "crop_profile": "faculty-cover-batch-v1"}

    def fields(self, content, boxes):
        width, height = Image.open(io.BytesIO(content)).size
        self.assertEqual(boxes, [[int(width * .12), int(height * .17), int(width * .88), int(height * .36)]])
        return {"reads": [{"text": self.titles.pop(0) if self.titles else ""}]}

    def read_all(self, message):
        self.batches = []
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_covers", side_effect=self.covers), \
                patch.object(unlabeled.UnlabeledService, "_likeness", return_value=0.0):
            for _ in range(40):
                self.scans.unlabeled.process_one()
                run = self.client.get(f"/api/documents/messages/{message}/unlabeled").json["run"]
                if run["status"] != "pending":
                    self.assertEqual(run["status"], "complete", run["error"])
                    break
        return [index for batch in self.batches for index in batch]

    def page_index_of(self, page_id):
        with connect(self.path) as db:
            return db.execute("SELECT source_page_index FROM scan_pages WHERE id=?", (page_id,)).fetchone()[0]

    def test_covers_are_read_matched_and_registered_as_their_students_paper(self):
        # Carol already handed in a labeled paper; Dylan was issued a label no scan has shown (seated, then absent).
        _, carol = self.labels("Nguyen, Carol")
        writer = PdfWriter()
        writer.add_page(carol.pages[0])
        out = io.BytesIO()
        writer.write(out)
        self.submit(out.getvalue())
        dylan_batch, _ = self.labels("O'Brien, Dylan James")
        # One scan: three exams of three sides, a bubble sheet with its blank back between the first two, then a cover for Carol and
        # an unreadable one. Every page differs, so nothing is taken for a repeat.
        white = Image.new("L", (1700, 2200), 255)
        pages = [other_page("cover alice"), other_page("q1 alice"), other_page("q2 alice"), sheet(KEY), white,
                 other_page("cover bob"), other_page("q1 bob"), other_page("q2 bob"),
                 other_page("cover carol"), other_page("q1 carol"), other_page("q2 carol"),
                 other_page("cover dylan"), other_page("q1 dylan"), other_page("q2 dylan")]
        job = self.submit(pdf(pages))
        message = job["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 6: cover_text("Bob Example", "223456781"),
                      9: cover_text("Carol Nguyen", "987650001"), 12: cover_text("", "")}
        self.titles = ["Common Exam I Version A, Fall 2026", "Common Exam I Version A, Fall 2026", "Common Exam I Version C, Fall 2026", ""]
        item = self.scans.review_item(message)
        self.assertEqual(item["recognition"]["bubble_sheets"]["sheets"], 1)
        step = next(step for step in item["overview"]["steps"] if step["tool"] == "unlabeled")
        self.assertIn("12 pages carry no label and are not bubble sheets", step["text"])  # 14 pages, less the sheet and its blank back.
        self.assertEqual(item["unlabeled"]["candidates"], 12)  # Unlabeled or blank pages, never the sheet or its blank back.
        # The request names the assignment and the sides; anything else is refused.
        for bad in ({"assignment_id": self.assignment["id"], "sides": 0}, {"assignment_id": "nope", "sides": 3}):
            self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled", json=bad, headers=HEADERS).status_code, 400, bad)
        queued = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 3}, headers=HEADERS)
        self.assertEqual(queued.status_code, 202, queued.json)
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 3},
                                          headers=HEADERS).status_code, 409)
        # The cadence decides what is read: the covers alone, never the sheet, its back or the pages between covers, and all four
        # in one model load.
        self.assertEqual(self.read_all(message), [1, 6, 9, 12])
        self.assertEqual(self.batches, [[1, 6, 9, 12]])
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        covers = {cover["page"]: cover for cover in summary["covers"]}
        self.assertEqual({page: (cover["state"], cover["basis"], cover["version"]) for page, cover in covers.items()},
                         {1: ("identified", "uin", "A"), 6: ("identified", "confidence", "A"), 9: ("conflict", "uin", "C"), 12: ("needs_review", None, "")})
        self.assertIn("already has a scanned paper", " ".join(covers[9]["notes"]))
        # OTHER PAPER: the flag opens Carol's labeled paper, the page she already handed in, and its image; an unflagged row has no link.
        self.assertTrue(covers[9]["other_paper"])
        self.assertNotIn("other_paper", covers[1])
        other = self.client.get(f"/api/documents/messages/{message}/unlabeled/{covers[9]['scan_page_id']}/other").json
        self.assertEqual((other["student"], other["this_page"], other["here"]), ("Nguyen, Carol", 9, []))
        self.assertEqual([[page["page_number"] for page in paper["pages"]] for paper in other["papers"]], [[1]])
        shown = other["papers"][0]["pages"][0]
        self.assertNotEqual(shown["message_id"], message)
        self.assertEqual(self.client.get(f"/api/documents/messages/{shown['message_id']}/pages/{shown['scan_page_id']}/image.png").mimetype, "image/png")
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/unlabeled/{covers[12]['scan_page_id']}/other").status_code, 404)
        self.assertEqual(covers[6]["student"]["enrollment_id"], self.students["Example, Bob"])
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/unlabeled/{covers[1]['scan_page_id']}/cover.png").mimetype, "image/png")
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/unlabeled/nope/cover.png").status_code, 404)
        steps = [step["text"] for step in self.scans.review_item(message)["overview"]["steps"] if step["tool"] == "unlabeled"]
        self.assertEqual(steps, ["Check who wrote 4 papers found without a label, then register them."])
        # The instructor names Dylan for the unreadable cover; a student off these rosters is refused.
        review = lambda **data: self.client.post(f"/api/documents/messages/{message}/unlabeled/review", json={"scan_page_id": covers[12]["scan_page_id"], **data}, headers=HEADERS)
        self.assertEqual(review(decision="student", enrollment_id="0000000000").status_code, 400)
        self.assertEqual(review(decision="student", enrollment_id=self.students["O'Brien, Dylan James"]).status_code, 200)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual([(cover["page"], cover["state"], cover["basis"]) for cover in summary["covers"]],
                         [(1, "identified", "uin"), (6, "identified", "confidence"), (9, "conflict", "uin"), (12, "identified", "review")])
        # Registering names exactly what was shown.
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": "stale"}, headers=HEADERS).status_code, 409)
        registered = self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        self.assertEqual(registered.status_code, 201, registered.json)
        with connect(self.path) as db:
            plans = {row["enrollment_id"]: dict(row) for row in db.execute("SELECT * FROM scan_unlabeled_plans")}
            dylan_copy = db.execute("SELECT id FROM printed_copies WHERE print_batch_id=?", (dylan_batch["id"],)).fetchone()[0]
        # Dylan's issued, never scanned label is reused at once; Alice and Bob get one new label job of their section, never printed.
        self.assertEqual(plans[self.students["O'Brien, Dylan James"]]["printed_copy_id"], dylan_copy)
        new_batch = {plans[self.students[name]]["print_batch_id"] for name in ("Example, Alice Marie", "Example, Bob")}
        self.assertEqual(len(new_batch), 1)
        states = {cover["page"]: cover["state"] for cover in registered.json["covers"]}
        self.assertEqual(states, {1: "registering", 6: "registering", 9: "conflict", 12: "registered"})
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled/review", json={"scan_page_id": covers[1]["scan_page_id"], "decision": "not_cover"},
                                          headers=HEADERS).status_code, 409)  # Registration is under way: corrected as labeled paper is.
        # The label job is made like any other; the next worker turn gives the covers their identities and groups the scan.
        self.assertTrue(self.documents.build_one())
        self.assertTrue(self.scans.unlabeled.process_one())
        item = self.scans.review_item(message)
        pages = {page["source_page_index"]: page for page in item["pages"]}
        with connect(self.path) as db:
            owners = dict(db.execute("SELECT id,enrollment_id FROM printed_copies").fetchall())
        owner = lambda index: owners.get(pages[index]["copy_id"])
        self.assertEqual([(index, pages[index]["status"], pages[index].get("page_number")) for index in (1, 2, 3, 6, 7, 8, 12, 13, 14)],
                         [(1, "manual", 1), (2, "manual", 2), (3, "manual", 3), (6, "manual", 1), (7, "manual", 2), (8, "manual", 3),
                          (12, "manual", 1), (13, "manual", 2), (14, "manual", 3)])
        self.assertEqual([owner(index) for index in (1, 6, 12)], [self.students[name] for name in ("Example, Alice Marie", "Example, Bob", "O'Brien, Dylan James")])
        # The bubble sheet and its back join no exam, and Carol's cover waits for the instructor with its pages.
        self.assertEqual([pages[index]["packet"] for index in (4, 5, 9, 10, 11)], [None] * 5)
        self.assertEqual([pages[index]["status"] for index in (4, 5, 9)], ["unreadable", "blank", "unreadable"])
        self.assertEqual({cover["page"]: cover["state"] for cover in item["unlabeled"]["covers"]}, {1: "registered", 6: "registered", 9: "conflict", 12: "registered"})
        # A registered first page keeps the student it was registered as and is not matched again (2026-09-24: matching the 321
        # registered first pages of a section's scan on every refresh made its page take 7 s). Carol's, still waiting, is matched,
        # and a refresh with the same reading and roster reuses that match.
        shown = {cover["page"]: cover for cover in item["unlabeled"]["covers"]}
        self.assertEqual([(shown[page]["match"], shown[page]["student"]["enrollment_id"]) for page in (1, 6, 12)],
                         [(None, self.students[name]) for name in ("Example, Alice Marie", "Example, Bob", "O'Brien, Dylan James")])
        self.assertEqual(shown[9]["match"]["enrollment_id"], self.students["Nguyen, Carol"])
        with patch.object(unlabeled.UnlabeledService, "_match", side_effect=AssertionError("matched again")):
            self.assertEqual(self.scans.unlabeled.summary(message)["covers"], item["unlabeled"]["covers"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exam_event_batches WHERE event_id=? AND batch_id=?", (self.event["id"], next(iter(new_batch)))).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_unlabeled_registrations").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT instructor_review_required FROM inbox_messages WHERE id=?", (message,)).fetchone()[0], 1)
            for sql in ("UPDATE scan_unlabeled_reads SET cover=0", "DELETE FROM scan_unlabeled_reviews", "UPDATE scan_unlabeled_plans SET basis='uin'",
                        "DELETE FROM scan_unlabeled_registrations"):
                with self.assertRaises(Exception, msg=sql):
                    db.execute(sql)
        self.assertEqual([step["text"] for step in item["overview"]["steps"] if step["tool"] == "unlabeled"], ["Check who wrote 1 paper found without a label, then register it."])
        self.assertTrue(item["instructor_review_required"])
        # OTHER PAPER (2026-10-17): looking again keeps the first attempt's registered covers registered; none is held as its own
        # student's other paper, and Carol's other paper is still only the one she handed in.
        again = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 3}, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["run"]["attempt"]), (202, 2))
        self.read_all(message)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual({cover["page"]: cover["state"] for cover in summary["covers"]}, {1: "registered", 6: "registered", 9: "conflict", 12: "registered"})
        other = self.client.get(f"/api/documents/messages/{message}/unlabeled/{shown[9]['scan_page_id']}/other").json
        self.assertEqual([[page["message_id"] != message for page in paper["pages"]] for paper in other["papers"]], [[True]])

    def test_looking_again_before_a_label_job_is_built_still_registers_the_earlier_run_s_covers_and_the_worker_goes_idle(self):
        # Requirement, 2026-11-06 (DOCUMENTS, first pages without labels): registered covers get their label's page 1 identity once the label
        # job is ready even when the instructor looked again meanwhile (a second run carries reads and reviews, never plans).
        pages = [other_page("cover alice"), other_page("q1 alice"), other_page("q2 alice"),
                 other_page("cover bob"), other_page("q1 bob"), other_page("q2 bob")]
        message = self.submit(pdf(pages))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 4: cover_text("Bob Example", "223456780")}
        self.titles = ["", ""]
        url = f"/api/documents/messages/{message}/unlabeled"
        body = {"assignment_id": self.assignment["id"], "sides": 3}
        self.assertEqual(self.client.post(url, json=body, headers=HEADERS).status_code, 202)
        self.read_all(message)
        summary = self.client.get(url).json
        registered = self.client.post(url + "/register", json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        self.assertEqual(registered.status_code, 201, registered.json)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_unlabeled_registrations").fetchone()[0], 0)  # the label job is not built yet
            first_run = db.execute("SELECT id FROM scan_unlabeled_runs WHERE message_id=?", (message,)).fetchone()[0]
        again = self.client.post(url, json=body, headers=HEADERS)  # looking again while the labels wait
        self.assertEqual((again.status_code, again.json["run"]["attempt"]), (202, 2), again.json)
        self.read_all(message)
        self.assertTrue(self.documents.build_one())
        for _ in range(4):
            self.scans.unlabeled.process_one()
        self.assertFalse(self.scans.unlabeled.process_one())  # idle: nothing is left to apply, so the worker sleeps
        with connect(self.path) as db:
            rows = db.execute("SELECT run_id,basis FROM scan_unlabeled_registrations").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["run_id"] for row in rows}, {first_run})  # each registration keeps its own plan's run
            grouping = db.execute("SELECT COUNT(*) FROM inbox_events WHERE kind='unlabeled_exams_grouping'").fetchone()[0]
        self.scans.unlabeled.process_one()
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox_events WHERE kind='unlabeled_exams_grouping'").fetchone()[0], grouping)

    def test_jev_finds_first_pages_without_a_name_line_and_reads_whose_paper_each_is(self):
        # JEV PAPERS (requirement, 2026-10-15): one-page quizzes scanned two-sided. Alice's carries the printed Name and UIN; Bob and
        # Carol wrote their names at the top of a page with no Name line, so the OCR alone finds no first page there; Dylan's cover
        # is read by the OCR as Alice's UIN but Jev reads Dylan; Jev cannot settle Carol's.
        # GEMINI MATCHES THE STUDENT (2026-11-12): Bob's and Carol's matches say Gemini read them (`reader`), and what the instructor
        # reads names it; Dylan's, without one, is Jev's as before.
        white = Image.new("L", (1700, 2200), 255)
        pages = [other_page("cover alice"), white, other_page("bob in a corner"), white.copy(), other_page("carol in a corner"), white.copy(),
                 other_page("cover dylan"), white.copy()]
        message = self.submit(pdf(pages))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: "Bob Example 223456780\nA survey was conducted of 1200 households.",
                      5: "A survey was conducted of 1200 households.", 7: cover_text("Alice Exampel", "123456789")}
        checked, asked = [], []

        def page_check(jev, owner, picture):
            index = self.page_index_of(owner)
            checked.append(index)
            box = {"top": .05, "bottom": .1, "left": .1, "right": .4}
            return {"status": "complete", "receipt": f"r{index}", "has_name": .99, "first_page": True, "orientation": "upright",
                    "name_box": box, "uin_box": box, "name_confidence": .9, "uin_confidence": .9}

        def identify(jev, owner, picture, check, students):
            index = self.page_index_of(owner)
            asked.append(index)
            self.assertEqual(set(students), set(self.students.values()))  # The roster of the run's sections, at the time of the reading.
            pick = {1: "Example, Alice Marie", 3: "Example, Bob", 7: "O'Brien, Dylan James"}.get(index)
            return {"status": "complete", "enrollment_id": self.students[pick] if pick else None, "candidates": [],
                    **({} if pick else {"reason": "Neither the name nor the UIN settled one student."}),
                    **({"reader": "Gemini"} if index in (3, 5) else {})}

        queued = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        self.assertEqual(queued.status_code, 202, queued.json)
        with patch.object(unlabeled.UnlabeledService, "_jev", return_value=object()), \
                patch("document_identity.jev_papers.page_check", side_effect=page_check), patch("document_identity.jev_papers.identify", side_effect=identify):
            self.assertEqual(self.read_all(message), [1, 3, 5, 7])  # The blank backs are never read; the cadence holds.
        self.assertEqual((checked, asked), ([1, 3, 5, 7], [1, 3, 5, 7]))
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        covers = {cover["page"]: cover for cover in summary["covers"]}
        self.assertEqual({page: (cover["state"], cover["basis"]) for page, cover in covers.items()},
                         {1: ("identified", "uin"), 3: ("identified", "jev"), 5: ("needs_review", None), 7: ("needs_review", None)})
        self.assertEqual(covers[3]["student"]["enrollment_id"], self.students["Example, Bob"])
        self.assertIn("Gemini: Neither the name nor the UIN settled one student.", covers[5]["notes"])
        self.assertEqual([covers[page].get("reader") for page in (1, 3, 5, 7)], [None, "Gemini", None, None])  # only a page it identified
        self.assertIn("Jev reads the handwritten name and UIN as another student's. Check them, then choose the student.", covers[7]["notes"])
        # A first page only Jev found shows where Jev saw the name and UIN.
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/unlabeled/{covers[3]['scan_page_id']}/cover.png").mimetype, "image/png")
        registered = self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        self.assertEqual(registered.status_code, 201, registered.json)
        with connect(self.path) as db:
            self.assertEqual(dict(db.execute("SELECT enrollment_id,basis FROM scan_unlabeled_plans").fetchall()),
                             {self.students["Example, Alice Marie"]: "uin", self.students["Example, Bob"]: "jev"})
            with self.assertRaises(Exception):
                db.execute("UPDATE scan_unlabeled_plans SET basis='uin'")

    def test_jev_sorts_a_mixed_scan_by_assignment(self):
        # JEV MIXED SCANS (requirement, 2026-10-15): one scan holds Test 1 papers and a Quiz 9 paper, and a page of no known
        # assignment. Each first page goes under the assignment Jev reads it as; one Jev reads as none of them waits.
        state = self.client.get("/api/state").json
        quiz = self.client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "quiz-9",
                                                                     "title": "Quiz 9"}, headers=HEADERS).json
        white = Image.new("L", (1700, 2200), 255)
        message = self.submit(pdf([other_page("cover alice"), white, other_page("quiz bob"), white.copy(), other_page("stray carol"), white.copy()]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: cover_text("Bob Example", "223456780"), 5: cover_text("Carol Nguyen", "987650001")}
        offered = []

        def which(jev, owner, picture, assignments):
            offered.append(sorted(a["title"] for a in assignments))
            index = self.page_index_of(owner)
            return {1: {"status": "complete", "assignment_id": self.assignment["id"], "none": False},
                    3: {"status": "complete", "assignment_id": quiz["id"], "none": False},
                    5: {"status": "complete", "assignment_id": None, "none": True}}[index]

        check = {"status": "complete", "first_page": True, "has_name": .99, "orientation": "upright",
                 "name_box": {"top": .05, "bottom": .1, "left": .1, "right": .4}, "uin_box": {"top": .05, "bottom": .1, "left": .5, "right": .9}}
        self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        # Jev is shown how Test 1's blank paper begins, so its "None of these" is evidence.
        course = [{"id": self.assignment["id"], "title": "Test 1", "description": 'Its blank paper begins: "Test 1"', "blank": True},
                  {"id": quiz["id"], "title": "Quiz 9", "description": "No blank paper is on file yet.", "blank": False}]
        with patch.object(unlabeled.UnlabeledService, "_jev", return_value=object()), patch("document_identity.jev_papers.page_check", return_value=check), \
                patch("document_identity.jev_papers.identify", return_value={"status": "complete", "enrollment_id": None, "candidates": []}), \
                patch("document_identity.jev_papers.which_assignment", side_effect=which), \
                patch.object(unlabeled.UnlabeledService, "_course_assignments", return_value=course):
            self.read_all(message)
        self.assertEqual(offered, [["Quiz 9", "Test 1"]] * 3)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        covers = {cover["page"]: cover for cover in summary["covers"]}
        self.assertEqual({page: (cover["state"], cover["assignment_title"]) for page, cover in covers.items()},
                         {1: ("identified", "Test 1"), 3: ("identified", "Quiz 9"), 5: ("needs_review", "Test 1")})
        self.assertIn("Jev reads this paper as Quiz 9; it is registered under Quiz 9.", covers[3]["notes"])
        self.assertIn("Jev reads this page as none of this course's assignments. Check it, then choose the student or say it is not a first page.", covers[5]["notes"])
        registered = self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        self.assertEqual(registered.status_code, 201, registered.json)
        with connect(self.path) as db:
            under = dict(db.execute("""SELECT p.enrollment_id,v.assignment_id FROM scan_unlabeled_plans p JOIN print_batches b ON b.id=p.print_batch_id
                JOIN assignment_versions v ON v.id=b.assignment_version_id""").fetchall())
        self.assertEqual(under, {self.students["Example, Alice Marie"]: self.assignment["id"], self.students["Example, Bob"]: quiz["id"]})

    def test_jevs_none_holds_nothing_when_the_runs_assignment_has_no_blank_paper(self):
        # JEV MIXED SCANS (2026-10-16, an assignment made from its own scan): Jev was never shown how the run's assignment begins, so its
        # "None of these" is no evidence against an exact roster UIN; nothing waits for it, and asking again at another side count
        # keeps the pages already read.
        state = self.client.get("/api/state").json
        self.client.post("/api/documents/assignments", json={"definition_id": state["course_definitions"][0]["id"], "key": "quiz-9", "title": "Quiz 9"},
                         headers=HEADERS)
        white = Image.new("L", (1700, 2200), 255)
        message = self.submit(pdf([other_page("cover alice"), white, other_page("cover bob"), white.copy()]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: cover_text("Bob Example", "223456780")}
        check = {"status": "complete", "first_page": True, "has_name": .99, "orientation": "upright",
                 "name_box": {"top": .05, "bottom": .1, "left": .1, "right": .4}, "uin_box": {"top": .05, "bottom": .1, "left": .5, "right": .9}}
        self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 1}, headers=HEADERS)
        with patch.object(unlabeled.UnlabeledService, "_jev", return_value=object()), patch("document_identity.jev_papers.page_check", return_value=check), \
                patch("document_identity.jev_papers.identify", return_value={"status": "complete", "enrollment_id": None, "candidates": []}), \
                patch("document_identity.jev_papers.which_assignment", return_value={"status": "complete", "assignment_id": None, "none": True}):
            self.assertEqual(self.read_all(message), [1, 3])
        covers = self.client.get(f"/api/documents/messages/{message}/unlabeled").json["covers"]
        self.assertEqual([(c["page"], c["state"], c["basis"]) for c in covers], [(1, "identified", "uin"), (3, "identified", "uin")])
        self.assertFalse([n for c in covers for n in c["notes"] if "none of this course's assignments" in n])
        # A reading kept before Jev's look recorded whether it saw the blank: judged by whether one is on file now.
        with connect(self.path) as db:
            for page_id, reading in db.execute("SELECT scan_page_id,reading_json FROM scan_unlabeled_reads").fetchall():
                reading = json.loads(reading)
                self.assertIs(reading["jev_assignment"]["run_blank"], False)
        again = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        self.assertEqual(again.status_code, 202, again.json)
        self.assertEqual(self.read_all(message), [])  # nothing is read twice
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual((summary["run"]["sides"], [c["state"] for c in summary["covers"]]), (2, ["identified", "identified"]))

    def test_jev_asks_again_about_an_upside_down_page_turned_upright(self):
        # UPSIDE DOWN (2026-10-17): Jev saw four take-home first pages were upside down but read their names at .21 to .74. It is
        # asked again with the page turned; that reading decides, its boxes turned back onto the page as scanned.
        from document_identity import jev_papers
        page = Image.new("L", (100, 200), 255)
        page.paste(0, (0, 0, 10, 10))  # a mark at the top left, as scanned
        out = io.BytesIO()
        page.save(out, format="PNG")
        asked = []

        class Jev:
            def ask(self, purpose, owner, context, questions, rule, images):
                asked.append(Image.open(io.BytesIO(images[0])).convert("L"))
                upright = len(asked) == 2
                box = {"top": .05, "bottom": .1, "left": .1, "right": .4}
                return {"status": "complete", "id": f"receipt-{len(asked)}", "answers": {
                    "orientation": {"choice": "upright" if upright else "upside down"}, "has_name": {"noul": .97 if upright else .3},
                    "name": {"box": box, "confidence": .9}, "uin": {"box": box, "confidence": .8}}}
        with patch("document_identity.jev_papers.client.prepare_image", side_effect=lambda picture: picture):
            check = jev_papers.page_check(Jev(), "page", out.getvalue())
        self.assertEqual(len(asked), 2)
        self.assertEqual((asked[0].getpixel((2, 2)), asked[1].getpixel((97, 197))), (0, 0))  # the second look is turned upright
        self.assertEqual((check["orientation"], check["first_page"], check["has_name"], check["receipt"], check["turned_receipt"]),
                         ("upside down", True, .97, "receipt-1", "receipt-2"))
        self.assertEqual(check["name_box"], {"top": .9, "bottom": .95, "left": .6, "right": .9})
        # The OCR's reading of a handwritten "UIN" label as "VIN" still makes a first page.
        self.assertTrue(unlabeled.cover_fields("name: Iris Wade\nVIN: 900000303")[0])
        self.assertEqual(unlabeled.cover_fields("name: Iris Wade\nVIN: 900000303")[1]["uin"], "900000303")

    def test_a_page_read_as_no_first_page_left_in_no_paper_can_be_named_and_registered(self):
        # LOOSE FIRST PAGES (2026-10-17, requirement: "so how do I fix the ones that are still upside down"): once the search finished
        # and the scan is grouped, a written page read as no first page and left in no paper is listed with the first pages, and the
        # instructor's choice of its student registers and groups it like any first page.
        white = Image.new("L", (1700, 2200), 255)
        message = self.submit(pdf([other_page("cover alice"), white, other_page("bob upside down"), white.copy()]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: "Venn diagram of Aggie and Longhorn fans"}
        self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        self.read_all(message)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual([(c["page"], c["state"]) for c in summary["covers"]], [(1, "identified")])  # not grouped yet: nothing loose
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]},
                                          headers=HEADERS).status_code, 201)
        while self.documents.build_one():
            pass
        self.scans.unlabeled.apply(message)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        covers = {c["page"]: c for c in summary["covers"]}
        self.assertEqual({page: c["state"] for page, c in covers.items()}, {1: "registered", 3: "needs_review"})
        self.assertEqual(covers[3]["notes"], ["Read as no first page, and it is in no paper: nothing on it was read for a name. If it is one, "
                                              "choose its student (search by the UIN written on it); if not, say so."])
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/unlabeled/{covers[3]['scan_page_id']}/cover.png").mimetype, "image/png")
        chosen = self.client.post(f"/api/documents/messages/{message}/unlabeled/review", json={"scan_page_id": covers[3]["scan_page_id"], "decision": "student",
                                  "enrollment_id": self.students["Example, Bob"]}, headers=HEADERS)
        self.assertEqual(chosen.status_code, 200, chosen.json)
        summary = chosen.json
        self.assertEqual([(c["page"], c["state"], c["basis"]) for c in summary["covers"]], [(1, "registered", "uin"), (3, "identified", "review")])
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]},
                                          headers=HEADERS).status_code, 201)
        while self.documents.build_one():
            pass
        self.scans.unlabeled.apply(message)
        with connect(self.path) as db:
            grouped = {row[0]: row[1] for row in db.execute("SELECT source_page_index,page_stamp_id IS NOT NULL FROM scan_pages WHERE message_id=?", (message,))}
            registered = db.execute("SELECT enrollment_id FROM scan_unlabeled_registrations WHERE scan_page_id=?", (covers[3]["scan_page_id"],)).fetchone()
        self.assertEqual(registered[0], self.students["Example, Bob"])
        self.assertEqual(grouped[3], 1)
        self.assertEqual([c["state"] for c in self.client.get(f"/api/documents/messages/{message}/unlabeled").json["covers"]], ["registered", "registered"])

    def test_a_page_jev_sees_as_blank_is_never_listed_as_a_loose_first_page(self):
        # 2026-10-17, instructor: "it thinks blank pages should have a name": an empty back the scanner did not call blank, left in no
        # paper, is not asked whose it is when Jev sees it as blank.
        white = Image.new("L", (1700, 2200), 255)
        message = self.submit(pdf([other_page("cover alice"), white, other_page("faint back"), white.copy()]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: "Question 3. Solve for x."}
        looks = {1: {"status": "complete", "first_page": True, "has_name": .99, "orientation": "upright"},
                 3: {"status": "complete", "first_page": False, "has_name": .04, "orientation": "blank"}}
        self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        with patch.object(unlabeled.UnlabeledService, "_jev_page", side_effect=lambda page: looks[self.page_index_of(page["id"])]):
            self.read_all(message)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        while self.documents.build_one():
            pass
        self.scans.unlabeled.apply(message)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual([(c["page"], c["state"]) for c in summary["covers"]], [(1, "registered")])

    def test_without_jev_the_ocr_reading_stands(self):
        # Jev not set up (no key, or turned off): nothing is asked, and a page without the printed Name and UIN is no first page.
        white = Image.new("L", (1700, 2200), 255)
        message = self.submit(pdf([other_page("cover alice"), white, other_page("bob in a corner"), white.copy()]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: "Bob Example 223456780"}
        self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        with patch("document_identity.jev_papers.page_check", side_effect=AssertionError("Jev was asked")):
            self.read_all(message)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual([(cover["page"], cover["state"], cover["basis"]) for cover in summary["covers"]], [(1, "identified", "uin")])

    def test_registration_checks_the_review_again_after_preparing_labels(self):
        """DOCUMENTS: a changed student choice cannot be overwritten by a stale registration."""
        message = self.submit(pdf([other_page("cover alice"), other_page("q alice")]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789")}
        service = self.scans.unlabeled
        service.request(message, {"assignment_id": self.assignment["id"], "sides": 2})
        self.read_all(message)
        summary = service.summary(message)
        original = self.documents.create_batch

        def prepare_then_change(*args, **kwargs):
            result = original(*args, **kwargs)
            service.review(message, {"scan_page_id": summary["covers"][0]["scan_page_id"], "decision": "student",
                                     "enrollment_id": self.students["Example, Bob"]})
            return result

        with patch.object(self.documents, "create_batch", side_effect=prepare_then_change):
            response = self.client.post(f"/api/documents/messages/{message}/unlabeled/register",
                                        json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(service.summary(message)["covers"][0]["student"]["enrollment_id"], self.students["Example, Bob"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_unlabeled_plans").fetchone()[0], 0)

    def test_a_cover_takes_its_students_copy_of_the_new_label_job_by_copy_number(self):
        """The student's copy of a new label job is found by copy number, the order the batch's own index gave before
        document_identity 012's enrollment index, which would hand back the copy written first."""
        message = self.submit(pdf([other_page("cover alice"), other_page("q alice"), other_page("cover bob"), other_page("q bob")]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: cover_text("Bob Example", "223456780")}
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2},
                                          headers=HEADERS).status_code, 202)
        self.read_all(message)
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual([cover["state"] for cover in summary["covers"]], ["identified", "identified"])
        registered = self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        self.assertEqual(registered.status_code, 201, registered.json)
        self.assertTrue(self.documents.build_one())
        alice, cover = self.students["Example, Alice Marie"], summary["covers"][0]["scan_page_id"]
        with connect(self.path) as db:
            plan = db.execute("SELECT print_batch_id,printed_copy_id FROM scan_unlabeled_plans WHERE enrollment_id=?", (alice,)).fetchone()
            self.assertIsNone(plan["printed_copy_id"])
            copies = dict(db.execute("SELECT copy_number,id FROM printed_copies WHERE print_batch_id=?", (plan["print_batch_id"],)).fetchall())
            self.assertEqual(sorted(copies), [1, 2])
            # Both copies of the job are Alice's, and copy 1 is the one written last.
            db.execute("UPDATE printed_copies SET enrollment_id=? WHERE print_batch_id=?", (alice, plan["print_batch_id"]))
            db.execute("UPDATE printed_copies SET rowid=(SELECT MAX(rowid)+1 FROM printed_copies) WHERE id=?", (copies[1],))
        self.assertTrue(self.scans.unlabeled.apply(message))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT printed_copy_id FROM scan_unlabeled_registrations WHERE scan_page_id=?", (cover,)).fetchone()[0], copies[1])

    def test_an_assignment_made_from_the_scan_reads_its_papers_and_registers_the_confident_ones(self):
        # Section 502 has a student too, but the assignment is made for section 501 only.
        state = self.client.get("/api/state").json
        rows = [["", "Other, Zed", "999888777", "Registered", "", "", "", "", "", "", "", "zed@example.test"]]
        self.assertEqual(self.client.post(f"/api/course-groups/{state['course_groups'][0]['id']}/sections/502/roster",
                                          data={"file": (io.BytesIO(csv_bytes(rows, COLUMNS)), "roster.csv")}, headers=HEADERS).status_code, 201)
        # A quiz scan with no labels at all: Alice's and Bob's papers, and one whose first page cannot be read.
        pages = [other_page("cover alice"), other_page("q alice"), other_page("cover bob"), other_page("q bob"), other_page("cover x"), other_page("q x")]
        message = self.submit(pdf(pages))["message_id"]
        # A quiz prints its version in the top band: Alice's is read there, and her title box is not read at all; Bob's band has none,
        # so his title box is read (Version E, not one of the three); the third first page shows no version anywhere.
        self.texts = {1: cover_text("Alice Example", "123456789") + "\nChapter 2 Quiz Version B 10 points", 3: cover_text("Bob Example", "223456781"),
                      5: cover_text("", "")}
        self.titles = ["Chapter 2 Quiz, Version E", "Directions: PRINT your name"]
        state = self.client.get(f"/api/grading/scans/{message}").json
        body = {"request_id": str(uuid.uuid4()), "fingerprint": state["fingerprint"], "term_id": self.section["term_id"], "assignment_id": None,
                "new_title": "Quiz 2", "section_ids": [self.section["id"]], "version_count": 3, "sides": 2}
        post = lambda data: self.client.post(f"/api/grading/scans/{message}/associate", json=data, headers=HEADERS)
        # The versions and the sides belong to a new assignment, and are checked.
        for bad in ({**body, "version_count": 27}, {**body, "sides": 0}, {**body, "request_id": str(uuid.uuid4()), "new_title": None,
                                                                            "assignment_id": self.assignment["id"]}):
            self.assertEqual(post(bad).status_code, 400, bad)
        saved = post(body)
        self.assertEqual(saved.status_code, 200, saved.json)
        plan = self.app.extensions["frq_grading"].assignment(saved.json["plan"]["id"])
        self.assertEqual((plan["exam_versions"]["declared"], plan["exam_versions"]["letters"]), (["A", "B", "C"], ["A", "B", "C"]))
        # Three versions expect three sets of rubrics: the document they are uploaded under exists at once, and setup waits for each.
        frq = self.app.extensions["frq_grading"]
        self.assertEqual([document["label"] for document in plan["rubric_upload_documents"]], ["Stamps only · 1 page"])
        with connect(self.path) as db:
            self.assertEqual(frq.rubric_quality_context(db, plan["id"])[1], ["Version A", "Version B", "Version C"])
        plan = frq.save_question(plan["id"], {"version": plan["version"], "question_key": "q1", "exam_version": "A",
                                              "assignment_version_id": plan["rubric_upload_documents"][0]["id"],
                                              "content": {"title": "Question 1", "max_points": 3, "problem": "",
                                                          "regions": [{"page": 1, "region": {"x": .1, "y": .1, "width": .8, "height": .4}}]}})
        with connect(self.path) as db:
            setup = frq.template_setup(db, plan["id"])
            self.assertEqual(frq.rubric_quality_context(db, plan["id"])[1], ["Question 1", "Version A", "Version B", "Version C"])
        self.assertEqual([slot["label"] for slot in setup["slots"]], ["Version A", "Version B", "Version C"])
        self.assertEqual(setup["blockers"], ["Upload or draft rubrics for every version first."])
        # Saving started the reading: the declared letters, the sides, and registering what is confident.
        run = self.client.get(f"/api/documents/messages/{message}/unlabeled").json["run"]
        self.assertEqual((run["status"], run["sides"], run["versions"], run["register_confident"], run["assignment_id"]),
                         ("pending", 2, "ABC", True, plan["source_assignment_id"]))
        self.assertNotIn("Nothing printed on these pages", " ".join(step["text"] for step in self.scans.review_item(message)["overview"]["steps"]))
        self.assertEqual(self.read_all(message), [1, 3, 5])
        # The roster is the assessment's sections (requirement, 2026-09-24): Zed of section 502 is neither offered nor found by UIN, and
        # a UIN typed into a picker comes back as names and sections only.
        search = lambda typed: self.client.post(f"/api/documents/messages/{message}/unlabeled/students", json={"uin": typed}, headers=HEADERS)
        found = search("123456789")
        self.assertEqual((found.status_code, found.json["students"][0]["name"], found.json["students"][0]["similarity"]), (200, "Example, Alice Marie", 1.0))
        self.assertNotIn("123456789", found.get_data(as_text=True))
        self.assertEqual(search("999888777").json["students"], [])
        self.assertNotIn("Other, Zed", [student["name"] for student in self.client.get(f"/api/documents/messages/{message}/unlabeled").json["students"]])
        # Alice (exact UIN) and Bob (at the threshold) are registered with no click; the unreadable first page waits.
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        covers = {cover["page"]: cover for cover in summary["covers"]}
        self.assertEqual({page: (cover["state"], cover["version"]) for page, cover in covers.items()},
                         {1: ("registering", "B"), 3: ("registering", "E"), 5: ("needs_review", "")})
        self.assertIn("Version E is not one of this assignment's versions (A, B, C).", covers[3]["notes"])
        self.assertIn("No version (A, B, C) was read on this first page.", covers[5]["notes"])
        with connect(self.path) as db:
            readings = {self.page_index_of(row[0]): json.loads(row[1]) for row in db.execute("SELECT scan_page_id,reading_json FROM scan_unlabeled_reads WHERE cover=1")}
        self.assertEqual({page: (reading["rule"], reading["title_read"], reading["versions_read"]) for page, reading in readings.items()},
                         {1: ("unlabeled-covers-v4", False, {"band": "B", "title": ""}), 3: ("unlabeled-covers-v4", True, {"band": "", "title": "E"}),
                          5: ("unlabeled-covers-v4", True, {"band": "", "title": ""})})
        with connect(self.path) as db:
            self.assertEqual({row[0] for row in db.execute("SELECT actor FROM scan_unlabeled_plans")}, {"automatic"})
            self.assertEqual(db.execute("SELECT instructor_review_required FROM inbox_messages WHERE id=?", (message,)).fetchone()[0], 1)
        self.assertTrue(self.documents.build_one())
        self.assertTrue(self.scans.unlabeled.process_one())
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual({cover["page"]: cover["state"] for cover in summary["covers"]}, {1: "registered", 3: "registered", 5: "needs_review"})
        # Repeating the same request neither links again nor reads again.
        self.assertEqual(post(body).status_code, 200)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_unlabeled_runs WHERE message_id=?", (message,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_declared_versions").fetchone()[0], 1)
        # Looking again from the scan's panel keeps what the first run was asked to do: the declared letters and registering by itself.
        again = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": plan["source_assignment_id"], "sides": 2}, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["run"]["versions"], again.json["run"]["register_confident"]), (202, "ABC", True))
        # Started over (requirement, 2026-09-24): not while it is being read; then a
        # new scan of the same file, filed under the same assignment and sections and read again with the same settings once its
        # pages are checked. Nothing done on the old scan is carried: its first pages are found, read and registered afresh.
        job = self.client.get(f"/api/grading/scans/{message}").json["job_id"]
        start = lambda request: self.client.post(f"/api/inbox/jobs/{job}/start-over", json={"request_id": request, "confirmation": "start over", "keep": True}, headers=HEADERS)
        self.assertEqual(start("too-soon").status_code, 409)
        self.read_all(message)
        made = start("again")
        self.assertEqual(made.status_code, 201, made.json)
        fresh = made.json["message_id"]
        while self.scans.process_one():
            pass
        state = self.client.get(f"/api/grading/scans/{fresh}").json
        self.assertEqual((state["plan"]["source_assignment_id"], state["plan"]["section_ids"]), (plan["source_assignment_id"], [self.section["id"]]))
        run = self.client.get(f"/api/documents/messages/{fresh}/unlabeled").json["run"]
        self.assertEqual((run["status"], run["sides"], run["versions"], run["register_confident"]), ("pending", 2, "ABC", True))
        applied = next(event for event in self.inbox.get(made.json["id"])["events"] if event["kind"] == "scan_start_over_applied")
        self.assertEqual(json.loads(applied["detail"]), {"carried": ["association", "unlabeled"], "notes": []})
        self.assertEqual(self.read_all(fresh), [1, 3, 5])
        summary = self.client.get(f"/api/documents/messages/{fresh}/unlabeled").json
        # Registered at once: the labels issued for the old scan's papers are shown by no scan now, so they are reused.
        self.assertEqual({cover["page"]: cover["state"] for cover in summary["covers"]}, {1: "registered", 3: "registered", 5: "needs_review"})
        self.assertIsNotNone(self.inbox.get(job)["deleted"])

    def test_a_scan_without_labels_leads_to_finding_whose_paper_each_is(self):
        # Requirement, 2026-09-26, of a scan whose pages carry no label: its page led with sorting and alias recovery and was "not very
        # helpful ... make it more user friendly and goal oriented". Finding whose paper each is is the next step; the rest are options.
        message = self.submit(pdf([other_page(f"page {n}") for n in range(6)]))["message_id"]
        state = self.client.get(f"/api/grading/scans/{message}").json
        saved = self.client.post(f"/api/grading/scans/{message}/associate", json={
            "request_id": str(uuid.uuid4()), "fingerprint": state["fingerprint"], "term_id": self.section["term_id"],
            "assignment_id": self.assignment["id"], "section_ids": [self.section["id"]]}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        overview = self.scans.review_item(message)["overview"]
        first = overview["steps"][0]
        self.assertEqual((first["kind"], first["tool"]), ("todo", "unlabeled"))
        self.assertIn("they look like papers printed without labels. Find whose paper each is", first["text"])
        self.assertFalse([step for step in overview["steps"] if step["tool"] == "sort" and step["kind"] != "info"])
        stages = {stage["key"]: stage for stage in overview["stages"]}
        self.assertEqual((stages["scan"]["state"], stages["students"]["state"], stages["students"]["status"]), ("done", "started", "Needs you"))
        self.assertTrue(stages["scan"]["detail"].startswith("6 pages checked; none carries a label. If these pages do carry labels"))
        self.assertEqual(overview["headline"], "Test 1 · papers without labels")
        self.assertIsNone(self.client.get(f"/api/documents/messages/{message}/unlabeled").json["paper_sides"])
        # A blank template of 3 pages says how long a paper is: the overview counts the papers and the request form starts from it.
        with connect(self.path) as db:
            db.execute("""INSERT INTO assignment_templates(id,assignment_id,label,request_id,request_sha256,page_count,source_format,
                          source_names_json,manifest_path,manifest_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                       (str(uuid.uuid4()), self.assignment["id"], "Version A", str(uuid.uuid4()), "0" * 64, 3, "pdf", "[]", "x", "0" * 64,
                        "2026-09-26T00:00:00+00:00"))
        self.assertIn("(2 papers of 3 sides, the length of Test 1's blank template)", self.scans.review_item(message)["overview"]["steps"][0]["text"])
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/unlabeled").json["paper_sides"], 3)

    def test_first_pages_read_before_the_scan_is_filed_name_their_assignment(self):
        # inbox-5: first pages read for Test 1 on a scan nobody filed yet. The scan stopped saying "Unrecognized scan" and its
        # Assignment step names the assignment the pages were read for; nothing is filed automatically.
        message = self.submit(pdf([other_page(f"page {n}") for n in range(6)]))["message_id"]
        self.assertEqual(self.scans.review_item(message)["overview"]["headline"], "Unrecognized scan")
        queued = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 3}, headers=HEADERS)
        self.assertEqual(queued.status_code, 202, queued.json)
        overview = self.scans.review_item(message)["overview"]
        self.assertEqual(overview["headline"], "Test 1 · papers without labels")
        step = next(step for step in overview["steps"] if step["action"] == "grading_associate")
        self.assertEqual(step["kind"], "todo")
        self.assertTrue(step["text"].endswith(" First pages are being read for Test 1."), step["text"])  # the run is queued
        self.assertIsNone(self.client.get(f"/api/grading/scans/{message}").json["plan"])

    def test_finish_inbox_checks_counts_only_assignments_with_a_scan_in_the_inbox(self):
        # 2026-09-26: "this shows there is something to do in inbox checks, but then I click it and there is nothing in the inbox":
        # the count took an assignment whose scans had all been deleted. It counts one with a scan still there, and leads to it.
        job = self.submit(pdf([other_page(f"page {n}") for n in range(4)]))
        state = self.client.get(f"/api/grading/scans/{job['message_id']}").json
        self.assertEqual(self.client.post(f"/api/grading/scans/{job['message_id']}/associate", json={
            "request_id": str(uuid.uuid4()), "fingerprint": state["fingerprint"], "term_id": self.section["term_id"],
            "assignment_id": self.assignment["id"], "section_ids": [self.section["id"]]}, headers=HEADERS).status_code, 200)
        navigation = self.client.get("/api/grading?view=navigation").json
        self.assertEqual((navigation["waiting_for_inbox"], navigation["waiting_href"]), (1, f"#inbox/job/{job['id']}"))
        current = self.client.get(f"/api/inbox/jobs/{job['id']}").json
        deleted = self.client.post(f"/api/inbox/jobs/{job['id']}/delete", json={"version": current["version"], "confirmation": "delete",
                                                                                "reason": "Scanned twice"}, headers=HEADERS)
        self.assertEqual(deleted.status_code, 200, deleted.json)
        navigation = self.client.get("/api/grading?view=navigation").json
        self.assertEqual((navigation["waiting_for_inbox"], navigation["waiting_href"]), (0, "#inbox"))

    def test_busy_ocr_waits_and_an_interrupted_run_fails_until_asked_again(self):
        job = self.submit(pdf([other_page("cover"), other_page("q1"), other_page("q2")]))
        message = job["message_id"]
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 3},
                                          headers=HEADERS).status_code, 202)
        with patch("faculty_ocr.recognize_covers", side_effect=unlabeled.OcrError("Another local OCR task is running; try again after it completes.")):
            self.assertFalse(self.scans.unlabeled.process_one())
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/unlabeled").json["run"]["status"], "pending")
        with connect(self.path) as db:
            db.execute("UPDATE scan_unlabeled_runs SET status='running'")
        self.assertFalse(self.scans.unlabeled.process_one())  # Found interrupted: failed, and nothing else to do.
        run = self.client.get(f"/api/documents/messages/{message}/unlabeled").json["run"]
        self.assertEqual((run["status"], run["error"]), ("failed", "Reading was interrupted. Ask again to read the scan's first pages."))
        again = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 3}, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["run"]["attempt"]), (202, 2))

    def test_the_instructor_stops_a_run_and_what_was_read_is_kept(self):
        message = self.submit(pdf([other_page("cover alice"), other_page("q alice"), other_page("cover bob"), other_page("q bob")]))["message_id"]
        self.texts = {1: cover_text("Alice Example", "123456789"), 3: cover_text("Bob Example", "223456781")}
        stop = lambda: self.client.post(f"/api/documents/messages/{message}/unlabeled/cancel", json={}, headers=HEADERS)
        self.assertEqual(stop().status_code, 409)  # Nothing is being looked for yet.
        self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        # Stopped while a batch is being read: the batch is kept, the run stays stopped, and nothing more is read or registered.
        def stop_midway(pages, title_box, words, title_unless=None):
            self.assertEqual(stop().status_code, 200)
            return self.covers(pages, title_box, words, title_unless)
        self.batches = []
        with patch("faculty_ocr.recognize_covers", side_effect=stop_midway), patch.object(unlabeled.UnlabeledService, "_likeness", return_value=0.0):
            self.assertTrue(self.scans.unlabeled.process_one())
            self.assertFalse(self.scans.unlabeled.process_one())
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual((summary["run"]["status"], summary["run"]["error"], summary["run"]["pages_read"]),
                         ("cancelled", "Stopped by the instructor. Ask again to look for first pages.", 2))
        self.assertEqual([cover["state"] for cover in summary["covers"]], ["identified", "identified"])
        steps = [step for step in self.scans.review_item(message)["overview"]["steps"] if step["tool"] == "unlabeled"]
        self.assertEqual([(step["kind"], step["text"], step["action"]) for step in steps],
                         [("todo", "Looking for first pages stopped after 2 pages read and 2 first pages found (stopped by the instructor). "
                                   "Continue from where it stopped.", "unlabeled_continue")])
        self.assertEqual(stop().status_code, 409)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_unlabeled_plans").fetchone()[0], 0)
        # The instructor names Alice on her first page, then continues: a new attempt that keeps what was read and said, and goes on
        # from there. Here nothing is left to read, so no page is read twice.
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled/review", json={"scan_page_id": summary["covers"][0]["scan_page_id"],
                         "decision": "student", "enrollment_id": self.students["Example, Alice Marie"]}, headers=HEADERS).status_code, 200)
        again = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.assignment["id"], "sides": 2}, headers=HEADERS)
        self.assertEqual((again.status_code, again.json["run"]["attempt"], again.json["run"]["status"], again.json["run"]["pages_read"]), (202, 2, "pending", 2))
        with patch("faculty_ocr.recognize_covers", side_effect=AssertionError("Nothing is read twice.")), patch.object(unlabeled.UnlabeledService, "_likeness", return_value=0.0):
            self.assertTrue(self.scans.unlabeled.process_one())
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual((summary["run"]["status"], [(cover["state"], cover["basis"]) for cover in summary["covers"]]),
                         ("complete", [("identified", "review"), ("identified", "confidence")]))
        with connect(self.path) as db:
            event = json.loads(db.execute("SELECT detail FROM inbox_events WHERE kind='unlabeled_exams_requested' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertIn("continues_run", event)


if __name__ == "__main__":
    unittest.main()
