"""GRADING "DR COPIES" (requirement, 2026-11-10): the DR center's print of a drafted
quiz's Word copy is registered under a document of its own, counts as its version's paper, and is cut one whole sheet per problem.

Synthetic quiz, roster and pages; the model host is a fake, and the first-page reader is scripted."""
import io
import json
import os
import re
import unittest
import uuid
import zipfile
from unittest.mock import patch

from PIL import Image, ImageDraw
from werkzeug.exceptions import Conflict

from rosters import connect
from teach_inbox.service import sha
from document_identity import accessible_quiz, media, origins, unlabeled
from document_identity.layouts import CORNERS_PROFILE
from grading import dr_copies, extraction, jev_blank
from bubble_fixture import other_page, pdf
import test_quiz_drafts as drafts

HEADERS = drafts.HEADERS
HAS_PANDOC = os.path.exists(accessible_quiz.pandoc())


def png(image):
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def printed(lines_at=120):
    """A page with printed lines at the top, as a DR copy's sheet has its problem."""
    image = Image.new("L", (850, 1100), 255)
    draw = ImageDraw.Draw(image)
    for line in range(6):
        draw.rectangle((90, lines_at + 34 * line, 700 - 60 * (line % 3), lines_at + 34 * line + 9), fill=0)
    return image


class WholeSheets(unittest.TestCase):
    def test_a_sheet_is_cut_whole_whatever_its_blank_says_and_turned_upright(self):
        whole = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        # Another rendering of the same sheet: its lines sit elsewhere, so the page does not align with it.
        blank, page = png(printed(300)), printed()
        ImageDraw.Draw(page).text((300, 800), "x = 2", fill=30)
        ordinary = extraction._prepare(png(page), whole, blank)["metadata"]
        self.assertFalse(ordinary["registered"])
        self.assertNotIn("sheet", ordinary)
        cut = extraction._prepare(png(page), whole, blank, [[0.0, 0.0, 1.0, .1]], sheet=True)
        self.assertEqual((cut["metadata"]["registered"], cut["metadata"]["reason"], cut["metadata"]["sheet"]), (True, "", True))
        self.assertEqual((cut["metadata"]["crop_bounds"], cut["metadata"]["template_translation"], cut["metadata"]["template_rotation"]),
                         ([0, 0, 850, 1100], [0, 0], 0))
        # The picture a hosted reader gets has the name band masked; the original keeps it.
        cloud = Image.open(io.BytesIO(cut["images"]["cloud"])).convert("L")
        self.assertEqual(cloud.crop((0, 0, 850, 109)).getextrema(), (255, 255))
        self.assertEqual(Image.open(io.BytesIO(cut["images"]["original"])).convert("L").tobytes(), page.tobytes())
        # Fed upside down, the sheet is turned upright by its blank's printed top, and still cut whole.
        fed = png(page.transpose(Image.Transpose.ROTATE_180))
        turned = extraction._prepare(fed, whole, png(printed()), sheet=True)
        self.assertTrue(turned["metadata"]["orientation"]["turned"])
        self.assertEqual(Image.open(io.BytesIO(turned["images"]["original"])).convert("L").tobytes(), page.tobytes())
        # Without a blank the sheet is the whole answer all the same.
        self.assertTrue(extraction._prepare(png(page), whole, None, sheet=True)["metadata"]["registered"])

    def test_the_copy_a_blank_is_rendered_from_loses_only_its_page_count(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("word/document.xml", "<w:document/>")
            archive.writestr("docProps/app.xml", "<Properties><Application>Microsoft Word 12.0.0</Application><Pages>1</Pages><Words>83</Words></Properties>")
        with zipfile.ZipFile(io.BytesIO(accessible_quiz.for_rendering(out.getvalue()))) as archive:
            self.assertEqual(archive.read("docProps/app.xml"), b"<Properties><Application>Microsoft Word 12.0.0</Application><Words>83</Words></Properties>")
            self.assertEqual(archive.read("word/document.xml"), b"<w:document/>")


@unittest.skipUnless(HAS_PANDOC, "pandoc is not installed (FACULTY_PANDOC or /mnt/nvme/Teaching/tools/pandoc)")
class DrCopies(unittest.TestCase):
    setUp, draft, approved = drafts.QuizDrafts.setUp, drafts.QuizDrafts.draft, drafts.QuizDrafts.approved

    def answer(self, body, host):
        if drafts.prompt_name(body) == "quiz_write":
            # No figure: a version with one has no Word copy yet (QUIZZES "ACCESSIBLE COPY").
            quiz = drafts.quiz_answer(drafts.payload_of(body))
            for problem in quiz["problems"]:
                for version in problem["versions"]:
                    version.update(figure="", figure_side="", statement=version["statement"].replace(" graphed at right", ""))
            return quiz
        return drafts.QuizDrafts.answer(self, body, host)

    def ready(self):
        self.done = self.approved()
        self.aid, self.plan_id = self.done["assignment_id"], self.done["plan_id"]
        self.scans, self.inbox = self.app.extensions["document_scans"], self.app.extensions["inbox"]
        with connect(self.path) as db:
            uins = {"001234567": "Example, Alice", "123456789": "Example, Bob"}  # the roster fixture (`test_rosters.STUDENTS`)
            self.students = {uins[row["uin"]]: row["enrollment_id"] for row in db.execute(
                "SELECT e.uin,m.enrollment_id FROM roster_memberships m JOIN student_enrollments e ON e.id=m.enrollment_id WHERE m.section_id=?",
                (self.sections[501],))}
            self.letters = dict(db.execute("""SELECT DISTINCT q.exam_version,q.assignment_version_id FROM frq_assignment_questions p
                JOIN frq_questions q ON q.id=p.question_id WHERE p.assignment_id=?""", (self.plan_id,)).fetchall())

    def covers(self, pages, title_box, words, title_unless=None):
        result = []
        for content in pages:
            with connect(self.path) as db:
                index = db.execute("""SELECT sp.source_page_index FROM scan_pages sp LEFT JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id
                                      WHERE sp.content_sha256=:h OR a.sha256=:h""", {"h": sha(content)}).fetchone()[0]
            reads = [{"kind": "band", "rotation": 0, "text": self.texts.get(index, f"Problem {index}. Solve for x.")}]
            if index in self.texts:
                reads.append({"kind": "title", "rotation": 0, "text": "MATH 140 Fall 2026 Chapter 3 Quiz"})  # the copy prints no version
            else:
                reads.append({"kind": "band", "rotation": 180, "text": ""})
            result.append({"reads": reads, "source_sha256": sha(content)})
        return {"pages": result, "model_id": "fixture", "crop_profile": "faculty-cover-batch-v1"}

    def scanned(self, texts, pages):
        """A scan of DR papers filed under the quiz, its first pages read at the copy's sides and its students registered."""
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": pdf(pages)}], source="email", sender="scanner",
                                idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        while self.scans.process_one():
            pass
        message, self.texts = job["message_id"], texts
        state = self.client.get(f"/api/grading/scans/{message}").json
        saved = self.client.post(f"/api/grading/scans/{message}/associate", json={
            "request_id": str(uuid.uuid4()), "fingerprint": state["fingerprint"], "term_id": self.term["id"], "assignment_id": self.aid,
            "section_ids": [self.sections[501]]}, headers=HEADERS)
        self.assertEqual(saved.status_code, 200, saved.json)
        sides = self.client.get(f"/api/documents/messages/{message}/unlabeled").json["paper_sides"]
        queued = self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.aid, "sides": sides}, headers=HEADERS)
        self.assertEqual(queued.status_code, 202, queued.json)
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_covers", side_effect=self.covers), \
                patch.object(unlabeled.UnlabeledService, "_likeness", return_value=0.0):
            for _ in range(20):
                self.scans.unlabeled.process_one()
                run = self.client.get(f"/api/documents/messages/{message}/unlabeled").json["run"]
                if run["status"] != "pending":
                    self.assertEqual(run["status"], "complete", run["error"])
                    break
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        registered = self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]}, headers=HEADERS)
        self.assertEqual(registered.status_code, 201, registered.json)
        while self.documents.build_one():
            pass
        self.assertTrue(self.scans.unlabeled.process_one())
        return message, sides

    def test_the_dr_centers_print_is_graded_on_its_own_sheets_with_its_versions_rubrics(self):
        self.ready()
        copy = self.frq.dr_copy(self.aid, self.term["id"])
        self.assertEqual(self.frq.dr_copy(self.aid, self.term["id"]), copy)  # made once
        with connect(self.path) as db:
            document = db.execute("SELECT * FROM assignment_versions WHERE id=?", (copy,)).fetchone()
            details = json.loads(document["compiler_json"])
            template = db.execute("SELECT * FROM assignment_templates WHERE id=?", (details["dr_copy"]["template_id"],)).fetchone()
            plan = self.frq.plan(db, self.plan_id)
            # The first version a screen reader can read, the one Send to AIM uploads: a label-only document that names it.
            self.assertEqual((document["label"], details["generator"], document["page_count"]), ("DR copy · A", "faculty-stamps-only", 1))
            self.assertEqual({key: details["dr_copy"][key] for key in ("version_id", "pages", "letter", "rule", "accessible_rule")},
                             {"version_id": self.letters["A"], "pages": 2, "letter": "A", "rule": dr_copies.RULE, "accessible_rule": accessible_quiz.RULE})
            # Its blank is the Word file rendered here: one page per problem, kept with its source.
            self.assertEqual((template["label"], template["page_count"], template["source_format"], template["source_version_id"]),
                             ("DR copy · A", 2, "docx", None))
            # It takes no rubrics and no template slot of its own, and papers without labels are counted at its sides.
            self.assertNotIn(copy, {d["id"] for d in self.frq.rubric_upload_documents(db, plan)})
            before = self.frq.template_setup(db, self.plan_id)
            self.assertEqual(unlabeled.UnlabeledService.paper_sides(db, self.aid), 2)
        # Two papers of two sheets each, no label anywhere: Alice's and Bob's.
        pages = [other_page("cover alice"), other_page("work alice"), other_page("cover bob"), other_page("work bob")]
        message, sides = self.scanned({1: "MATH 140 Fall 2026 Chapter 3 Quiz Directions: PRINT your name: Alice Example UIN: 001234567",
                                       3: "MATH 140 Fall 2026 Chapter 3 Quiz Directions: PRINT your name: Bob Example UIN: 123456789"}, pages)
        self.assertEqual(sides, 2)
        with connect(self.path) as db:
            # Registered under the DR copy's own document, never a plain label sheet, and nothing was added to the plan.
            self.assertEqual({row[0] for row in db.execute("""SELECT b.assignment_version_id FROM scan_unlabeled_registrations g
                JOIN printed_copies c ON c.id=g.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id""")}, {copy})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignment_versions WHERE assignment_id=? AND label LIKE 'Stamps only%'", (self.aid,)).fetchone()[0], 0)
            self.assertEqual(self.frq.template_setup(db, self.plan_id)["slots"], before["slots"])
        state = self.client.get(f"/api/grading/scans/{message}").json
        self.frq.review_scan(message, {"fingerprint": state["fingerprint"], "confirm_review": True})
        questions = {f"{q['content']['printed_number']}:{q['exam_version']}": q for q in self.frq.assignment(self.plan_id)["questions"]}
        with connect(self.path) as db:
            plan, event = self.frq.plan(db, self.plan_id), self.frq.exams._event(db, self.frq.plan(db, self.plan_id)["event_id"])
            alice = self.frq.exams._participant_paper(db, event, self.sections[501], self.students["Example, Alice"])
            # The paper counts as Version A's: its document settles the version, with no seat, comparison or question to anyone.
            self.assertEqual(self.frq.manual._paper_version(db, event["id"], alice), self.letters["A"])
            found = self.frq.paper_version(db, plan, alice)
            self.assertEqual((found["status"], found["version"], found.get("printed_document")), ("confirmed", "A", True))
            sources = {number: self.frq.source(db, plan, questions[f"{number}:A"], alice, event) for number in ("1", "2")}
            with self.assertRaisesRegex(Conflict, "different document version"):
                self.frq.source(db, plan, questions["1:B"], alice, event)
            # The manual scores page lays nothing out on the copy: it refuses it as before.
            with self.assertRaisesRegex(Conflict, "different document version"):
                self.frq.manual._source(db, event, alice, {"assignment_version_id": self.letters["A"]})
        whole = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        for number, sheet in (("1", 1), ("2", 2)):
            source, question = sources[number], questions[f"{number}:A"]
            # Problem k is the whole of sheet k, whatever areas the printed version has; sheet 1's name band is masked.
            self.assertEqual([(r["page"], r["page_number"], r["region"], r["privacy_masks"]) for r in source["regions"]],
                             [(sheet, sheet, whole, [[0.0, 0.0, 1.0, dr_copies.NAME_BAND]] if sheet == 1 else [])])
            self.assertEqual((source["assignment_version_id"], source["exam_version"], source["template_id"], source["dr_copy"]),
                             (self.letters["A"], "A", details["dr_copy"]["template_id"], {"assignment_version_id": copy, "rule": dr_copies.RULE}))
            self.assertNotEqual(question["content"]["regions"][0]["region"], whole)  # the version's own areas are untouched
        self.assertTrue(jev_blank.whole_page(sources["2"]["regions"][0]))
        # A read over every paper of the exam (a batch's, the gradebook's) counts both papers as Version A's, and Bob's is laid out the same.
        with connect(self.path) as db:
            _, participants = self.frq.manual._context(db, event["id"])
            documents = {p["enrollment_id"]: document for p, document in self.frq.paper_documents(db, plan, participants, {"prefetch": {}})}
            self.assertEqual(documents, {self.students[name]: self.letters["A"] for name in ("Example, Alice", "Example, Bob")})
            bob = next(p for p in participants if p["enrollment_id"] == self.students["Example, Bob"])
            shared = {"prefetch": {}}
            self.assertEqual(self.frq.paper_version(db, plan, bob, shared)["version"], "A")
            theirs = self.frq.source(db, plan, questions["2:A"], bob, event, shared)
            self.assertEqual((theirs["regions"][0]["page"], theirs["regions"][0]["region"], theirs["dr_copy"]["assignment_version_id"]), (2, whole, copy))
            self.assertNotEqual(theirs["regions"][0]["scan_page_id"], sources["2"]["regions"][0]["scan_page_id"])

    def test_a_paper_with_another_number_of_sheets_waits_and_a_quiz_without_a_copy_is_left_alone(self):
        self.ready()
        copy = self.frq.dr_copy(self.aid, self.term["id"])
        # One paper scanned with a third sheet: its pages are not the copy's.
        pages = [other_page("cover alice"), other_page("work alice"), other_page("more alice")]
        self.texts = {1: "MATH 140 Fall 2026 Chapter 3 Quiz Directions: PRINT your name: Alice Example UIN: 001234567"}
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": pdf(pages)}], source="email", sender="scanner",
                                idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        while self.scans.process_one():
            pass
        message = job["message_id"]
        state = self.client.get(f"/api/grading/scans/{message}").json
        self.client.post(f"/api/grading/scans/{message}/associate", json={"request_id": str(uuid.uuid4()), "fingerprint": state["fingerprint"],
                         "term_id": self.term["id"], "assignment_id": self.aid, "section_ids": [self.sections[501]]}, headers=HEADERS)
        self.client.post(f"/api/documents/messages/{message}/unlabeled", json={"assignment_id": self.aid, "sides": 3}, headers=HEADERS)
        with patch("faculty_ocr.available", return_value=True), patch("faculty_ocr.recognize_covers", side_effect=self.covers), \
                patch.object(unlabeled.UnlabeledService, "_likeness", return_value=0.0):
            for _ in range(20):
                self.scans.unlabeled.process_one()
                if self.client.get(f"/api/documents/messages/{message}/unlabeled").json["run"]["status"] != "pending":
                    break
        summary = self.client.get(f"/api/documents/messages/{message}/unlabeled").json
        self.assertEqual(self.client.post(f"/api/documents/messages/{message}/unlabeled/register", json={"fingerprint": summary["fingerprint"]},
                                          headers=HEADERS).status_code, 201)
        while self.documents.build_one():
            pass
        self.scans.unlabeled.process_one()
        state = self.client.get(f"/api/grading/scans/{message}").json
        self.frq.review_scan(message, {"fingerprint": state["fingerprint"], "confirm_review": True})
        questions = {f"{q['content']['printed_number']}:{q['exam_version']}": q for q in self.frq.assignment(self.plan_id)["questions"]}
        with connect(self.path) as db:
            plan, event = self.frq.plan(db, self.plan_id), self.frq.exams._event(db, self.frq.plan(db, self.plan_id)["event_id"])
            alice = self.frq.exams._participant_paper(db, event, self.sections[501], self.students["Example, Alice"])
            with self.assertRaisesRegex(Conflict, "has 3 pages, but the DR copy has 2"):
                self.frq.source(db, plan, questions["1:A"], alice, event)
            # A version whose questions are not one to a sheet of the copy is refused, never guessed.
            with self.assertRaisesRegex(Conflict, "not one to a page"):
                self.frq.dr_copy_areas(db, plan, questions["1:A"], {"pages": 3}, {})
        # An assignment that was not drafted in Faculty has no DR copy: its papers without labels keep the plain label sheet.
        other = self.documents.create_assignment(self.term["definition_id"], "worksheet", "Worksheet")
        self.assertIsNone(self.frq.dr_copy(other["id"], self.term["id"]))
        self.assertEqual(copy, self.frq.dr_copy(self.aid, self.term["id"]))

    def test_the_word_file_prints_page_codes_that_are_no_students_and_its_papers_are_named_from_the_paper(self):
        """STAMPS (requirement, 2026-11-10)."""
        self.ready()
        with connect(self.path) as db:
            approval = db.execute("SELECT id,draft_id FROM quiz_draft_approvals WHERE status='complete'").fetchone()
        listed = self.frq.quiz_accessible_copies(self.term["id"])
        content, name = self.frq.quiz_accessible_copy(approval["draft_id"], approval["id"], "A")
        self.assertEqual(name, "Chapter-3-Quiz-accessible.docx")
        self.assertEqual(self.frq.quiz_accessible_copy(approval["draft_id"], approval["id"], "A")[0], content)  # the same file every time
        self.assertEqual(self.frq.quiz_accessible_copies(self.term["id"]), listed)
        plain = accessible_quiz.build(json.loads(self.revision())["quiz"], "A", "MATH 140 Fall 2026 Chapter 3 Quiz")[0]
        with zipfile.ZipFile(io.BytesIO(content)) as stamped, zipfile.ZipFile(io.BytesIO(plain)) as bare:
            # One picture per page, each drawn at the two corners and marked decorative; nothing else differs from the bare copy.
            self.assertEqual(sorted(set(stamped.namelist()) - set(bare.namelist())), ["word/media/page-code-1.png", "word/media/page-code-2.png"])
            body = stamped.read("word/document.xml").decode()
            self.assertEqual((body.count("<wp:anchor"), body.count("adec:decorative"), body.count('relativeFrom="page"')), (4, 4, 8))
            self.assertNotIn("<wp:anchor", bare.read("word/document.xml").decode())
            self.assertEqual(re.sub(r"<w:r><w:drawing>.*?</w:drawing></w:r>", "", body), bare.read("word/document.xml").decode())
            pictures = [stamped.read(f"word/media/page-code-{page}.png") for page in (1, 2)]
        with connect(self.path) as db:
            copies = self.frq.dr_copies(db, self.aid)
            (document, copy), = copies.items()
            row = db.execute("SELECT * FROM assignment_versions WHERE id=?", (document,)).fetchone()
            job = db.execute("SELECT * FROM print_batches WHERE assignment_version_id=?", (document,)).fetchone()
            stamps = {(r["copy_number"], r["page_number"]): r["token"] for r in db.execute(
                "SELECT c.copy_number,p.page_number,p.token,p.state FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id WHERE c.print_batch_id=?", (job["id"],))}
            self.assertEqual({r[0] for r in db.execute("SELECT DISTINCT p.state FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id WHERE c.print_batch_id=?", (job["id"],))}, {"issued"})
            self.assertEqual({r[0] for r in db.execute("SELECT enrollment_id FROM printed_copies WHERE print_batch_id=?", (job["id"],))}, {None})
            # A label for every page of a copy that is no student's: a job of the term, never a printing.
            self.assertEqual((row["label"], row["page_count"], row["layout_profile"], copy["pages"], copy["stamps"], copy["version_id"]),
                             ("DR copy · A · page codes", 2, CORNERS_PROFILE, 2, accessible_quiz.STAMPS, self.letters["A"]))
            self.assertEqual(json.loads(row["compiler_json"])["generator"], "faculty-dr-copy")  # every page stamped: whole at its pages
            self.assertEqual((job["request_id"], job["section_id"], job["term_id"], job["quantity"], job["status"]),
                             (dr_copies.JOB + document, None, self.term["id"], dr_copies.COPIES, "ready"))
            self.assertTrue(origins.is_registration(job["request_id"]))
            self.assertNotIn(document, {d["id"] for d in self.frq.rubric_upload_documents(db, self.frq.plan(db, self.plan_id))})
        # The file prints copy 1's codes: each page's picture reads as that page's token.
        self.assertEqual([[found["payload"] for found in media.decode_page(Image.open(io.BytesIO(picture)))] for picture in pictures],
                         [[stamps[(1, 1)]], [stamps[(1, 2)]]])
        # The DR center gives the same print to two students. Both papers come back on copy 1's codes.
        job_ = self.inbox.submit({"action": "scan.receive"}, [{"filename": "quiz.pdf", "content": b"%PDF-fixture" + uuid.uuid4().bytes}], source="email",
                                 sender="scanner", original=b"fixture mail", idempotency_key=str(uuid.uuid4()), authenticated=True, instructor_review_required=True)
        job_ = self.inbox.get(job_["id"])
        original = next(a for a in job_["artifacts"] if a["role"] == "input")
        texts, index = {}, 0
        for paper, written in enumerate(("PRINT your name: Example, Alice UIN: 001234567", "PRINT your name: Bob Example UIN: 123456789")):
            for side in (1, 2):
                index += 1
                page = f"paper-{paper}-side-{side}".encode()
                texts[sha(page)] = written
                self.scans._retain_page(job_, original, {"index": index, "pdf": page, "png": b"png", "decoder": {}, "blank": False,
                                                         "candidates": [{"payload": stamps[(1, side)]}]})
        message = job_["message_id"]
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (message,))

        def read(pages, *args, **kwargs):
            self.assertEqual(kwargs.get("blank_corners"), [True] * len(pages))  # both corner codes are whited out before the band is read
            return {"model_id": "fixture", "revision": "r", "pages": [{"source_sha256": sha(page), "reads": [
                {"kind": "band", "rotation": 0, "text": texts[sha(page)]}]} for page in pages]}
        with patch("document_identity.copy_names.faculty_ocr.recognize_covers", side_effect=read):
            self.assertTrue(self.scans.copy_names.process_one())
        # The second paper takes copy 2 of the job, and each copy is named from its own paper.
        self.assertTrue(self.scans.photocopies.process_one())
        named = {c["copy_number"]: c for c in self.scans.copy_names.summary(message)["copies"]}
        self.assertEqual({number: (c["state"], c["student"]["enrollment_id"]) for number, c in named.items()},
                         {1: ("named", self.students["Example, Alice"]), 2: ("named", self.students["Example, Bob"])})
        # Its labels file the scan under the quiz by themselves, and its review passes.
        self.frq.sync_scan(message)  # as the worker's catch-up does, when nothing filed it yet
        state = self.client.get(f"/api/grading/scans/{message}").json
        self.assertEqual(state["plan"]["id"], self.plan_id)
        # The job's unused copies ask nothing of anyone: no step of the scan waits for them.
        with connect(self.path) as db:
            self.assertEqual(self.frq.review_blockers(db, message), [])
        self.frq.review_scan(message, {"fingerprint": state["fingerprint"], "confirm_review": True})
        questions = {f"{q['content']['printed_number']}:{q['exam_version']}": q for q in self.frq.assignment(self.plan_id)["questions"]}
        whole = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
        with connect(self.path) as db:
            self.assertEqual(self.frq.scan_blockers(db, message), [])
            plan, event = self.frq.plan(db, self.plan_id), self.frq.exams._event(db, self.frq.plan(db, self.plan_id)["event_id"])
            for student in ("Example, Alice", "Example, Bob"):
                person = self.frq.exams._participant_paper(db, event, self.sections[501], self.students[student])
                found = self.frq.paper_version(db, plan, person)
                self.assertEqual((found["status"], found["version"]), ("confirmed", "A"), student)
                source = self.frq.source(db, plan, questions["2:A"], person, event)
                self.assertEqual((source["regions"][0]["page"], source["regions"][0]["region"], source["dr_copy"]["assignment_version_id"]), (2, whole, document), student)
        # Only the DR center prints the copy: a print job of it is refused.
        with self.assertRaisesRegex(ValueError, "prints this copy from its Word file"):
            self.documents.create_batch(document, self.sections[501], 1, str(uuid.uuid4()))
        # A paper found without its codes is still registered under the copy without page codes, made then.
        self.assertNotEqual(self.frq.dr_copy(self.aid, self.term["id"]), document)
        with connect(self.path) as db:
            self.assertEqual(sorted(r[0] for r in db.execute("SELECT label FROM assignment_versions WHERE assignment_id=? AND label LIKE 'DR copy%'", (self.aid,))),
                             ["DR copy · A", "DR copy · A · page codes"])

    def revision(self):
        with connect(self.path) as db:
            return db.execute("SELECT r.result_json FROM quiz_draft_approvals a JOIN quiz_draft_revisions r ON r.id=a.revision_id WHERE a.status='complete'").fetchone()[0]

    def test_a_file_whose_codes_cannot_be_checked_goes_out_as_it_always_did(self):
        self.ready()
        with connect(self.path) as db:
            approval = db.execute("SELECT id,draft_id FROM quiz_draft_approvals WHERE status='complete'").fetchone()
        plain = accessible_quiz.build(json.loads(self.revision())["quiz"], "A", "MATH 140 Fall 2026 Chapter 3 Quiz")[0]
        with patch.object(self.frq, "_dr_stamps_check", side_effect=ValueError("did not come out")), self.assertLogs("grading.quiz_drafts", "WARNING") as logged:
            self.assertEqual(self.frq.quiz_accessible_copy(approval["draft_id"], approval["id"], "A")[0], plain)
        self.assertIn("goes out without page codes: did not come out", logged.output[0])
        # The check itself: every page shows its own code at both corners, and a code on the wrong page is refused.
        stamped = self.frq.quiz_accessible_copy(approval["draft_id"], approval["id"], "A")[0]
        self.assertNotEqual(stamped, plain)
        with connect(self.path) as db:
            tokens = [r[0] for r in db.execute("""SELECT p.token FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id JOIN print_batches b ON b.id=c.print_batch_id
                WHERE b.request_id LIKE ? AND c.copy_number=1 ORDER BY p.page_number""", (dr_copies.JOB + "%",))]
        self.frq._dr_stamps_checked = {}
        self.frq._dr_stamps_check(stamped, tokens, 2)
        self.frq._dr_stamps_checked = {}
        with self.assertRaisesRegex(ValueError, "did not come out on their own pages"):
            self.frq._dr_stamps_check(stamped, tokens[::-1], 2)

    def test_the_worker_makes_the_copy_once_its_word_file_went_up_to_aim(self):
        self.ready()
        self.assertFalse(self.frq.dr_copy_one())  # nothing went up yet
        with connect(self.path) as db:
            self.assertEqual(self.frq.dr_copies(db, self.aid), {})
            approval = db.execute("SELECT id FROM quiz_draft_approvals WHERE status='complete'").fetchone()[0]
            item = "a" * 16
            db.execute("INSERT INTO scheduling_plan_items VALUES(?,?,?)", (item, self.term["id"], "2026-11-01T00:00:00+00:00"))
            db.execute("""INSERT INTO aim_sends(id,term_id,item_id,section_id,kind,date,minutes,approval_id,version,file_sha256,rows,run_id,actor,created_at)
                          VALUES(?,?,?,?,'upload','2026-11-06',0,?,?,?,?,?,?,?)""",
                       ("b" * 16, self.term["id"], item, self.sections[501], approval, "A", "c" * 64, '["d"]', "run-" + "e" * 12, "workspace",
                        "2026-11-04T00:00:00+00:00"))
        self.frq._dr_copy_looked = {}
        self.assertTrue(self.frq.dr_copy_one())
        with connect(self.path) as db:
            self.assertEqual([copy["letter"] for copy in self.frq.dr_copies(db, self.aid).values()], ["A"])
        self.assertFalse(self.frq.dr_copy_one())  # looked at moments ago, and made


if __name__ == "__main__":
    unittest.main()
