"""INBOX "SEPARATING A SCAN" (requirement, 2026-11-10): a scan whose labels name more than one assignment becomes one new scan for each, and goes to Deleted."""
import io
import json
import unittest
import uuid
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from bubble_fixture import sheet
from course_storage import StorageError
from document_identity import separation
from rosters import connect
import test_assignment_finder as finder_fixture
import test_sequence as fixture
import test_unlabeled_exams as unlabeled_fixture
from faculty_ocr import BUSY, OcrError
from test_assignment_finder import QUIZ_TITLE, TEST_TITLE, written
from test_bubble_sheets import KEY
from test_sequence import P2, P3


class SeparationTests(unittest.TestCase):
    setUp = fixture.SequenceTests.setUp

    def quiz(self, copies=2, title="Quiz 1"):
        """A second assignment of the course, printed with a label on every page like the exam."""
        catalog = self.documents.catalog()
        assignment = self.documents.create_assignment(catalog["definitions"][0]["id"], title.lower().replace(" ", "-"), title)
        version = self.documents.register_version(assignment["id"], "A", (fixture.FIXTURES / "letter.pdf").read_bytes(),
                                                  {"main.tex": (fixture.FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
        batch = self.documents.create_batch(version["id"], catalog["sections"][0]["id"], copies, str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        return PdfReader(io.BytesIO(self.documents.download(batch["id"])))

    def exam(self, copy):
        return [self.printed.pages[(copy - 1) * 3 + page] for page in range(3)]

    def submit(self, pages, filename="SKM_1.pdf", inspect=True):
        writer = PdfWriter()
        for page in pages:
            writer.add_page(page)
        writer.add_metadata({"/Title": str(uuid.uuid4())})  # Inbox dedupes identical content
        content = io.BytesIO()
        writer.write(content)
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": filename, "content": content.getvalue()}], source="email", sender="scanner",
                                original=b"retained-email:" + content.getvalue(), idempotency_key=str(uuid.uuid4()),
                                authenticated=True, instructor_review_required=True)
        if inspect:
            self.inspect()
        return job

    def inspect(self):
        while self.scans.process_one():
            pass

    def overview(self, message):
        with connect(self.path) as db:
            return self.scans.overview(db, message)

    def plan(self, message):
        with connect(self.path) as db:
            return self.scans.separation.plan(db, message)

    def events(self, job_id, kind):
        with connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind=? ORDER BY id", (job_id, kind))]

    def scans_now(self):
        """Every scan still in the Inbox as {file name: job}."""
        with connect(self.path) as db:
            ids = [row[0] for row in db.execute("""SELECT j.id FROM inbox_jobs j WHERE j.action='scan.receive' AND NOT EXISTS(
                SELECT 1 FROM inbox_deletions d WHERE d.message_id=j.message_id AND d.restored_at IS NULL) ORDER BY j.created_at,j.id""")]
        jobs = [self.inbox.get(identifier) for identifier in ids]
        return {next(file["filename"] for file in job["artifacts"] if file["role"] == "input"): job for job in jobs}

    def pages(self, message):
        with connect(self.path) as db:
            return [dict(row) for row in db.execute("""SELECT sp.source_page_index page,sp.status,sp.review_state,sp.content_sha256,sp.page_stamp_id
                FROM scan_pages sp WHERE sp.message_id=? ORDER BY sp.source_page_index""", (message,))]

    def test_a_scan_naming_two_assignments_becomes_one_scan_for_each_and_each_goes_on_by_itself(self):
        quiz = self.quiz()
        source = self.submit([*self.exam(1), *quiz.pages[:3], *self.exam(2), *quiz.pages[3:6]])
        message, frq, service = source["message_id"], self.app.extensions["frq_grading"], self.scans.separation
        # Held for the worker, never for the instructor: the tile says what is happening, and nothing can review or file it meanwhile.
        before = self.overview(message)
        self.assertEqual((before["verdict"], before["state"]), ("mixed", "wait"))
        self.assertIn("The labels name more than one assignment (Exam 1 and Quiz 1). Faculty is separating these pages into one scan for each.",
                      [step["text"] for step in before["steps"]])
        self.assertFalse(frq.auto_review_one())
        plan = self.plan(message)
        self.assertEqual([(part["title"], part["pages"], [file["pages"] for file in part["files"]], [file["filename"] for file in part["files"]]) for part in plan["parts"]],
                         [("Exam 1", 6, [[1, 2, 3, 7, 8, 9]], ["SKM_1 (Exam 1).pdf"]), ("Quiz 1", 6, [[4, 5, 6, 10, 11, 12]], ["SKM_1 (Quiz 1).pdf"])])
        self.assertTrue(service.process_one())
        # The scan is in Deleted with the ordinary tombstone, restorable, and says what it became.
        gone = self.inbox.get(source["id"])
        self.assertEqual((gone["deleted"]["actor"], gone["deleted"]["reason"]),
                         ("automatic", "Separated by assignment: its pages are in a new scan for each of Exam 1 and Quiz 1."))
        new = self.scans_now()
        self.assertEqual(sorted(new), ["SKM_1 (Exam 1).pdf", "SKM_1 (Quiz 1).pdf"])
        [separated] = self.events(source["id"], "scan_separated")
        self.assertEqual(separated["scans"], [{"job_id": new["SKM_1 (Exam 1).pdf"]["id"], "title": "Exam 1", "pages": 6},
                                              {"job_id": new["SKM_1 (Quiz 1).pdf"]["id"], "title": "Quiz 1", "pages": 6}])
        [origin] = self.events(new["SKM_1 (Quiz 1).pdf"]["id"], "scan_separated_from")
        self.assertEqual((origin["source"], origin["title"], origin["also"], origin["files"]),
                         ({"job_id": source["id"], "message_id": message}, "Quiz 1", ["Exam 1"],
                          [{"source_filename": "SKM_1.pdf", "filename": "SKM_1 (Quiz 1).pdf", "pages": [4, 5, 6, 10, 11, 12]}]))
        # Each new scan is an ordinary arrival from the same sender, waiting for its review, inspected afresh.
        self.assertEqual({(job["sender"], job["instructor_review_required"], job["status"]) for job in new.values()}, {("scanner", True, "needs_review")})
        self.inspect()
        original = {page["page_stamp_id"]: page["content_sha256"] for page in self.pages(message)}
        for name, title in (("SKM_1 (Exam 1).pdf", "Exam 1"), ("SKM_1 (Quiz 1).pdf", "Quiz 1")):
            child = new[name]["message_id"]
            pages = self.pages(child)
            # The same labels read from the same page content: nothing was rendered or re-encoded on the way, and no page repeats or
            # conflicts with the scan it came from, which no longer counts.
            self.assertEqual([(page["status"], page["review_state"]) for page in pages], [("resolved", "candidate")] * 6)
            self.assertEqual([original[page["page_stamp_id"]] for page in pages], [page["content_sha256"] for page in pages])
            overview = self.overview(child)
            self.assertEqual((overview["verdict"], overview["headline"]), ("recognized", f"{title} · MATH 140 · Section 501"))
            self.assertIsNone(self.plan(child))
            # Filed under its own assignment by its labels, and through its review by itself.
            with connect(self.path) as db:
                self.assertEqual(frq.scan_status(db, child)["plan"]["title"], title)
        self.assertTrue(frq.auto_review_one())
        self.assertTrue(frq.auto_review_one())
        with connect(self.path) as db:
            assembled = db.execute("""SELECT a.title,COUNT(DISTINCT ar.printed_copy_id) FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
                JOIN print_batches b ON b.id=c.print_batch_id JOIN assignment_versions v ON v.id=b.assignment_version_id
                JOIN assignments a ON a.id=v.assignment_id GROUP BY a.title ORDER BY a.title""").fetchall()
        self.assertEqual([tuple(row) for row in assembled], [("Exam 1", 2), ("Quiz 1", 2)])
        # Nothing is left to separate, and nothing is done twice.
        service.EVERY = 0
        self.assertFalse(service.process_one())
        self.assertEqual(len(self.scans_now()), 2)
        # Restored from Deleted, the scan is not separated a second time: it says why, and what to do.
        self.inbox.restore(source["id"])
        self.assertFalse(service.process_one())
        self.assertEqual(len(self.scans_now()), 3)
        held = next(step for step in self.overview(message)["steps"] if step.get("holds") == "scan")
        self.assertEqual((held["kind"], held["tool"], held["text"]), ("check", "pages",
                         "The labels name more than one assignment (Exam 1 and Quiz 1), and Faculty did not separate these pages by itself: "
                         "it was separated before, and the new scans hold its pages. Delete this scan again, or those."))

    def test_pages_without_a_label_stay_with_the_labeled_page_before_them(self):
        # Paper labeled on page 1 only: its other sides carry nothing, and belong to the label before them. The page before a file's
        # first label goes with that label.
        quiz = self.quiz()
        source = self.submit([self.plain.pages[1], self.printed.pages[0], self.plain.pages[1], self.plain.pages[2], quiz.pages[0], self.plain.pages[1],
                              self.printed.pages[3], self.plain.pages[1], self.plain.pages[2]])
        plan = self.plan(source["message_id"])
        self.assertEqual([(part["title"], [file["pages"] for file in part["files"]]) for part in plan["parts"]],
                         [("Exam 1", [[1, 2, 3, 4, 7, 8, 9]]), ("Quiz 1", [[5, 6]])])
        self.assertEqual((plan["reason"], plan["waiting"]), (None, False))
        self.assertTrue(self.scans.separation.process_one())
        self.inspect()
        new = self.scans_now()
        self.assertEqual({name: [page["status"] for page in self.pages(job["message_id"])] for name, job in new.items()},
                         {"SKM_1 (Exam 1).pdf": ["unreadable", "resolved", "unreadable", "unreadable", "resolved", "unreadable", "unreadable"],
                          "SKM_1 (Quiz 1).pdf": ["resolved", "unreadable"]})

    def test_a_scan_worked_on_by_hand_is_left_for_the_instructor_with_its_reason(self):
        quiz = self.quiz()
        source = self.submit([*self.exam(1), self.plain.pages[1], *quiz.pages[:3]])
        message = source["message_id"]
        with connect(self.path) as db:
            stray = db.execute("SELECT id FROM scan_pages WHERE message_id=? AND source_page_index=4", (message,)).fetchone()[0]
        self.scans.set_aside(stray, {"reason": "A cover sheet."})
        self.assertFalse(self.scans.separation.process_one())
        self.assertIsNone(self.inbox.get(source["id"])["deleted"])
        held = next(step for step in self.overview(message)["steps"] if step.get("holds") == "scan")
        self.assertEqual((held["kind"], held["text"]), ("check",
                         "The labels name more than one assignment (Exam 1 and Quiz 1), and Faculty did not separate these pages by itself: its pages were "
                         "already grouped, identified, read or set aside by hand, and a new scan would start without that work. Start it over to separate "
                         "it without that work, or scan each assignment's papers on their own."))
        # Started over, the new scan is as it arrived, and is separated.
        again = self.inbox.start_over(source["id"], str(uuid.uuid4()), "start over", keep=False)
        self.inspect()
        self.assertIsNone(self.plan(again["message_id"])["reason"])
        self.assertTrue(self.scans.separation.process_one())
        self.assertEqual(sorted(self.scans_now()), ["SKM_1 (Exam 1).pdf", "SKM_1 (Quiz 1).pdf"])

    def test_labels_still_being_read_are_waited_for(self):
        quiz = self.quiz()
        source = self.submit([*self.exam(1), *quiz.pages[:3]])
        message, service = source["message_id"], self.scans.separation
        service.EVERY = 0
        with connect(self.path) as db:
            page = db.execute("SELECT id FROM scan_pages WHERE message_id=? AND source_page_index=1", (message,)).fetchone()[0]
            db.execute("""INSERT INTO scan_alias_ocr(scan_page_id,message_id,status,result_json,error,created_at,updated_at)
                          VALUES(?,?,'pending','{}','','2026-11-10T00:00:00+00:00','2026-11-10T00:00:00+00:00')""", (page, message))
        self.assertTrue(self.plan(message)["waiting"])
        self.assertFalse(service.process_one())
        self.assertEqual(self.overview(message)["state"], "wait")
        with connect(self.path) as db:
            db.execute("UPDATE scan_alias_ocr SET status='complete' WHERE message_id=?", (message,))
        self.assertTrue(service.process_one())

    def test_a_part_that_cannot_be_made_changes_nothing_and_is_said_once(self):
        quiz = self.quiz()
        source = self.submit([*self.exam(1), *quiz.pages[:3]])
        message, service = source["message_id"], self.scans.separation
        service.EVERY = 0
        with patch.object(separation.process, "merge_pages", side_effect=ValueError("Assembly exceeds the PDF processing limit.")) as merge:
            self.assertFalse(service.process_one())
            self.assertFalse(service.process_one())
            self.assertEqual(merge.call_count, 1)  # the same pages are not tried again
        self.assertIsNone(self.inbox.get(source["id"])["deleted"])
        self.assertEqual(len(self.events(source["id"], "scan_separation_failed")), 1)
        self.assertEqual(sorted(self.scans_now()), ["SKM_1.pdf"])
        held = next(step for step in self.overview(message)["steps"] if step.get("holds") == "scan")
        self.assertEqual(held["text"], "The labels name more than one assignment (Exam 1 and Quiz 1), and Faculty did not separate these pages by itself: "
                                       "the new scans could not be made (Assembly exceeds the PDF processing limit). Scan each assignment's papers on their own.")

    def test_an_interrupted_separation_finishes_from_its_recorded_plan(self):
        quiz = self.quiz()
        source = self.submit([*self.exam(1), *quiz.pages[:3], *self.exam(2)])
        service, submit, calls = self.scans.separation, self.inbox.submit, []

        def once(*args, **kwargs):
            calls.append(kwargs["idempotency_key"])
            if len(calls) == 2:
                raise StorageError("The NVMe working copy is not available.")
            return submit(*args, **kwargs)

        with patch.object(self.inbox, "submit", side_effect=once), self.assertLogs("document_identity.separation", "ERROR"):
            self.assertFalse(service.process_one())
        # The scan is in Deleted and one of its two new scans exists: no page is in two scans, and the worker is not done.
        self.assertEqual(self.inbox.get(source["id"])["deleted"]["actor"], "automatic")
        self.assertEqual(sorted(self.scans_now()), ["SKM_1 (Exam 1).pdf"])
        self.assertEqual(self.events(source["id"], "scan_separated"), [])
        self.assertTrue(service.process_one())
        new = self.scans_now()
        self.assertEqual(sorted(new), ["SKM_1 (Exam 1).pdf", "SKM_1 (Quiz 1).pdf"])
        self.assertEqual([item["pages"] for item in self.events(source["id"], "scan_separated")[0]["scans"]], [6, 3])
        self.assertFalse(service.process_one())
        self.inspect()
        self.assertEqual({name: len(self.pages(job["message_id"])) for name, job in new.items()}, {"SKM_1 (Exam 1).pdf": 6, "SKM_1 (Quiz 1).pdf": 3})

    def test_a_bubble_sheet_follows_its_own_label_and_one_without_a_label_between_two_assignments_is_not_guessed(self):
        from document_identity.media import render_barcode, render_pages
        quiz = self.quiz()
        catalog = self.documents.catalog()
        stamps = self.documents.register_stamp_pages(catalog["assignments"][0]["id"], 1)
        batch = self.documents.create_batch(stamps["id"], catalog["sections"][0]["id"], 2, str(uuid.uuid4()), sheet_labels=True)
        self.assertTrue(self.documents.build_one())
        labels = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        with connect(self.path) as db:
            tokens = {entry["copy_number"]: entry["token"] for entry in self.documents.label_manifest(db, batch["id"]) if entry.get("sheet")}

        def page(image, size=(1700, 2200)):
            buffer = io.BytesIO()
            document = canvas.Canvas(buffer, pagesize=(size[0] * .36, size[1] * .36))  # 72 points to 200 pixels
            document.drawImage(ImageReader(image.resize(size)), 0, 0, width=size[0] * .36, height=size[1] * .36)
            document.save()
            return PdfReader(buffer).pages[0]

        def labeled(copy):
            image = sheet(KEY)
            image.paste(next(render_pages(render_barcode(tokens[copy]), 200)).convert("L"), (760, 40))
            return page(image)
        back, booklet = self.plain.pages[1], [self.plain.pages[1], self.plain.pages[2], self.plain.pages[1]]
        # The exam's sheet lies before its booklet, right after the quiz: its Scantron label says it is the exam's, and its back follows it.
        own = self.submit([*quiz.pages[:3], labeled(1), back, labels.pages[0], *booklet], filename="own.pdf")
        plan = self.plan(own["message_id"])
        self.assertEqual([(part["title"], [file["pages"] for file in part["files"]]) for part in plan["parts"]],
                         [("Quiz 1", [[1, 2, 3]]), ("Exam 1", [[4, 5, 6, 7, 8, 9]])])
        self.assertIsNone(plan["reason"])
        # A sheet with no label of its own in the same place could be either's: nothing is guessed, and the scan says so.
        bare = self.submit([*quiz.pages[3:6], page(sheet(KEY)), back, labels.pages[1], *booklet], filename="bare.pdf")
        plan = self.plan(bare["message_id"])
        self.assertEqual(plan["reason"], "the bubble sheet on page 4 carries no label of its own and lies between two assignments' papers")
        self.scans.separation.EVERY = 0
        self.assertTrue(self.scans.separation.process_one())
        self.assertFalse(self.scans.separation.process_one())
        self.assertEqual(sorted(self.scans_now()), ["bare.pdf", "own (Exam 1).pdf", "own (Quiz 1).pdf"])


class TitleSeparationTests(unittest.TestCase):
    """A scan with no label on any page (the DR center's copies, 2026-11-10): the title printed on each paper's first page says whose
    assignment it is, and the page counts must agree. The local reader and the templates' files are scripted, as for the finder."""
    setUp = unlabeled_fixture.UnlabeledExams.setUp
    submit = unlabeled_fixture.UnlabeledExams.submit
    page_index = unlabeled_fixture.UnlabeledExams.page_index
    with_templates = finder_fixture.AssignmentFinderTests.with_templates
    read_template = finder_fixture.AssignmentFinderTests.read_template
    covers = finder_fixture.AssignmentFinderTests.covers
    find = finder_fixture.AssignmentFinderTests.find

    def reader(self):
        """The finder's scripted templates and title reads, for the worker's own reads."""
        self.read = []
        stack = [patch.object(self.app.extensions["frq_grading"].templates, "read", side_effect=self.read_template),
                 patch("faculty_ocr.recognize_covers", side_effect=self.covers)]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)

    def stack(self, *papers):
        """One scan of papers without labels: ("quiz", name) is a Quiz 2 paper of two sides, ("test", name) a Test 1 paper of three."""
        from bubble_fixture import other_page, pdf
        pages, self.texts = [], {}
        for kind, name in papers:
            self.texts[len(pages) + 1] = (f"Name: {name}\nUIN: 123456789\n{QUIZ_TITLE}\nSections 2.1-2.4 Version A 10 points" if kind == "quiz" else
                                          f"Math 140\n{TEST_TITLE}\nFirst and Last Name (printed): {name}\nUIN: 123456789")
            pages += [written(self.images[kind], name)] + [other_page(f"{name} side {side}") for side in range(2, 3 if kind == "quiz" else 4)]
        return self.submit(pdf(pages))

    def overview(self, message):
        with connect(self.path) as db:
            return self.scans.overview(db, message)

    def events(self, job_id, kind):
        with connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT detail FROM inbox_events WHERE job_id=? AND kind=? ORDER BY id", (job_id, kind))]

    scans_now = SeparationTests.scans_now

    def test_a_scan_without_labels_is_separated_by_the_titles_on_its_first_pages_and_each_part_is_filed(self):
        self.with_templates()
        source = self.stack(("quiz", "Alice Example"), ("quiz", "Bob Example"), ("test", "Carol Nguyen"))
        # A later page's heading is no title, whatever it says: only a first page (the Name and UIN words at its top) names its paper.
        self.texts[2] = f"Math 140\n{TEST_TITLE}\nProblem 2. Consider the function."
        message, service = source["message_id"], self.scans.separation
        service.EVERY = 0
        # Until the finder has looked, nothing says the scan holds two assignments.
        with connect(self.path) as db:
            self.assertIsNone(service.plan(db, message))
        found = self.find(message)
        self.assertIn("more than one assignment", found["reason"])
        first = self.overview(message)["steps"][0]
        self.assertEqual((first["kind"], first["action"]), ("todo", "grading_associate"))
        # The worker reads the title printed on every written page (all seven in one load here), then separates.
        self.reader()
        self.assertTrue(service.process_one())
        self.assertEqual(self.read, [7])
        [titles] = self.events(source["id"], "scan_titles_read")
        self.assertEqual([(page["first"], len(page["titles"])) for page in titles["pages"]],
                         [(True, 1), (False, 0), (True, 1), (False, 0), (True, 1), (False, 0), (False, 0)])
        waiting = self.overview(message)
        self.assertEqual((waiting["state"], [step["text"] for step in waiting["steps"] if step["kind"] in ("todo", "check", "wait")]),
                         ("wait", ["Its first pages name more than one assignment (Quiz 2 and Test 1). Faculty is separating these pages into one scan for each."]))
        with connect(self.path) as db:
            plan = service.plan(db, message)
        self.assertEqual([(part["title"], [file["pages"] for file in part["files"]]) for part in plan["parts"]], [("Quiz 2", [[1, 2, 3, 4]]), ("Test 1", [[5, 6, 7]])])
        self.assertTrue(service.process_one())
        self.assertEqual(self.inbox.get(source["id"])["deleted"]["reason"], "Separated by assignment: its pages are in a new scan for each of Quiz 2 and Test 1.")
        new = self.scans_now()
        self.assertEqual(sorted(new), ["scan (Quiz 2).pdf", "scan (Test 1).pdf"])
        # Inspected, each new scan is filed under the assignment its titles named, by the ordinary automatic association, and its
        # first pages are looked for at that assignment's sides per paper. The finder has nothing left to decide.
        while self.scans.process_one():
            pass
        frq = self.app.extensions["frq_grading"]
        for name, title, sides in (("scan (Quiz 2).pdf", "Quiz 2", 2), ("scan (Test 1).pdf", "Test 1", 3)):
            child = new[name]["message_id"]
            with connect(self.path) as db:
                status = frq.scan_status(db, child, fingerprint=False)
                run = db.execute("SELECT sides,register_confident FROM scan_unlabeled_runs WHERE message_id=?", (child,)).fetchone()
            self.assertEqual((status["plan"]["title"], status["source"], tuple(run)), (title, "automatic", (sides, 1)))
            self.assertEqual([event["notes"] for event in self.events(new[name]["id"], "scan_separation_applied")], [[]])
            self.assertEqual(self.events(new[name]["id"], "scan_separated_from")[0]["basis"], "titles")
            self.assertEqual(self.events(new[name]["id"], "assignment_found"), [])
        self.assertFalse(self.scans.assignment_finder._find_one())
        self.assertFalse(service.process_one())

    def test_pages_that_do_not_add_up_to_whole_papers_are_left_to_the_instructor(self):
        # The second quiz paper lost its back: its title still says Quiz 2, but a Quiz 2 paper has two sides. Nothing is guessed.
        self.with_templates()
        from bubble_fixture import other_page, pdf
        self.texts = {1: f"Name: A\nUIN: 1\n{QUIZ_TITLE}", 3: f"Name: B\nUIN: 2\n{QUIZ_TITLE}", 4: f"Math 140\n{TEST_TITLE}\nFirst and Last Name (printed): C\nUIN: 3"}
        source = self.submit(pdf([written(self.images["quiz"], "A"), other_page("A 2"), written(self.images["quiz"], "B"),
                                  written(self.images["test"], "C"), other_page("C 2"), other_page("C 3")]))
        message, service = source["message_id"], self.scans.separation
        service.EVERY = 0
        self.find(message)
        self.reader()
        self.assertTrue(service.process_one())
        self.assertFalse(service.process_one())
        self.assertEqual(sorted(self.scans_now()), ["scan.pdf"])
        first = self.overview(message)["steps"][0]
        self.assertEqual((first["kind"], first["action"], first.get("holds")), ("todo", "grading_associate", "scan"))
        self.assertTrue(first["text"].endswith("Faculty did not separate these pages by itself: its pages do not add up to whole papers: "
                                               "a paper of Quiz 2 has 2 pages, and the one starting on page 3 has 1."), first["text"])

    def test_titles_naming_one_assignment_change_nothing_and_a_busy_reader_is_waited_for(self):
        # The finder's conflict here is a look against a title (pages like the quiz's blank that print the test's title): every title
        # names Test 1, so there is nothing to separate and the choice stays the instructor's, as before.
        self.with_templates()
        from bubble_fixture import pdf
        source = self.submit(pdf([written(self.images["quiz"], f"student {n}") for n in range(3)]))
        self.texts = {n + 1: f"Math 140\n{TEST_TITLE}\nFirst and Last Name (printed): x\nUIN: 1" for n in range(3)}
        message, service = source["message_id"], self.scans.separation
        service.EVERY = 0
        self.find(message)
        with patch.object(self.app.extensions["frq_grading"].templates, "read", side_effect=self.read_template), \
                patch("faculty_ocr.recognize_covers", side_effect=OcrError(BUSY)):
            self.assertFalse(service.process_one())
        self.assertEqual(self.events(source["id"], "scan_titles_read"), [])
        with connect(self.path) as db:
            self.assertEqual(service.plan(db, message)["reading"]["done"], 0)
        self.reader()
        self.assertTrue(service.process_one())
        self.assertFalse(service.process_one())
        with connect(self.path) as db:
            self.assertIsNone(service.plan(db, message))
        first = self.overview(message)["steps"][0]
        self.assertEqual(first["action"], "grading_associate")
        self.assertNotIn("separate", first["text"])
        self.assertEqual(sorted(self.scans_now()), ["scan.pdf"])

    def test_a_reader_that_cannot_read_leaves_the_choice_to_the_instructor_and_is_not_asked_again(self):
        self.with_templates()
        source = self.stack(("quiz", "Alice Example"), ("test", "Carol Nguyen"))
        message, service = source["message_id"], self.scans.separation
        service.EVERY = 0
        self.find(message)
        with patch.object(self.app.extensions["frq_grading"].templates, "read", side_effect=self.read_template), \
                patch("faculty_ocr.recognize_covers", side_effect=OcrError("The local reader is not installed.")) as reader, \
                self.assertLogs("document_identity.separation", "ERROR"):
            self.assertFalse(service.process_one())
            self.assertFalse(service.process_one())
            self.assertEqual(reader.call_count, 1)
        with connect(self.path) as db:
            self.assertIsNone(service.plan(db, message))
        self.assertEqual(self.overview(message)["steps"][0]["action"], "grading_associate")


if __name__ == "__main__":
    unittest.main()
