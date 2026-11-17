"""Every paper goes on unless it is flagged (requirement, 2026-09-28): a flagged paper, sheet or page holds back only itself."""
import io
import json
import unittest
import uuid
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from rosters import connect
from teach_inbox.service import sha
import test_sequence as fixture
from test_sequence import P2, P3


class PerPaperTests(unittest.TestCase):
    setUp = fixture.SequenceTests.setUp
    scan = fixture.SequenceTests.scan
    submit = fixture.SequenceTests.submit
    post = fixture.SequenceTests.post
    stamped_scan = fixture.SequenceTests.stamped_scan
    review = fixture.SequenceTests.review

    def job(self, message):
        with connect(self.path) as db:
            return self.inbox.get(db.execute("SELECT id FROM inbox_jobs WHERE message_id=?", (message,)).fetchone()[0])

    def overview(self, message):
        with connect(self.path) as db:
            return self.scans.overview(db, message)

    def revisions(self, copy_number):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute("""SELECT ar.* FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
                WHERE c.print_batch_id=? AND c.copy_number=?""", (self.batch["id"], copy_number))]

    def recovered_scan(self, *exams):
        original = self.scans.classify

        def unread(page):
            provenance, state = original(page)
            if state == "resolved":
                page["candidates"] = []
                return None, "unreadable"
            return provenance, state

        with patch.object(self.scans, "classify", side_effect=unread):
            batch, job = self.stamped_scan(*exams)
        entries = json.loads(self.documents.download(batch["id"], manifest=True))["pages"]
        aliases, start = {}, 1
        for entry, sides in zip(entries, exams):
            aliases[start] = entry["recovery_alias"]
            start += 1 + len(sides)
        with connect(self.path) as db:
            texts = {row[0]: aliases.get(row[1], "") for row in db.execute(
                "SELECT content_sha256,source_page_index FROM scan_pages WHERE message_id=?", (job["message_id"],))}
        frq = self.app.extensions["frq_grading"]
        result = lambda content: {"source_sha256": sha(content), "reads": [{"text": texts[sha(content)], "truncated": False}]}
        with patch("document_identity.alias_ocr.recognize_aliases", side_effect=result):
            for turn in range(start):
                if not self.scans.alias_ocr.process_one():
                    break
                if turn == 0:
                    # OCR / DOCUMENTS: no partial cadence is grouped while the remaining aliases are still being read.
                    self.assertFalse(frq.process_scan_one())
                    self.assertEqual(self.scans.review_item(job["message_id"])["sequence"]["runs"], [])
        return job

    def test_recovered_labels_catch_up_grouping_and_assembly_even_after_an_old_discovery_check(self):
        # PIPELINE: exact alias recovery must hand papers on without a Group click.
        job = self.recovered_scan([P2, P3, P2], [P2, P3, P2], [P2, P3, P2])
        message, frq = job["message_id"], self.app.extensions["frq_grading"]
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=? AND status='resolved'", (message,)).fetchone()[0], 3)
            old = frq.scan_status(db, message)["fingerprint"]
            db.execute("INSERT OR REPLACE INTO frq_scan_checks VALUES(?,?)", (message, old))
        for _ in range(3):
            frq.process_scan_one()
        grouped = self.scans.review_item(message)["sequence"]
        self.assertEqual([(run["automatic"], run["sides"], run["packets"]) for run in grouped["runs"]], [(True, 4, 3)])
        self.assertTrue(frq.auto_review_one())
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_sequence_assemblies WHERE message_id=?", (message,)).fetchone()[0], 3)
        for _ in range(3):
            frq.process_scan_one()
        self.assertEqual(len(self.scans.review_item(message)["sequence"]["runs"]), 1)

    def test_recovery_catch_up_keeps_grouping_all_or_nothing(self):
        # DOCUMENTS / PIPELINE: recovering all labels does not excuse an incomplete paper.
        job = self.recovered_scan([P2, P3, P2], [P2, P3, P2], [P2, P3])
        for _ in range(3):
            self.app.extensions["frq_grading"].process_scan_one()
        self.assertEqual(self.scans.review_item(job["message_id"])["sequence"]["runs"], [])

    def test_recovery_catch_up_never_repeats_an_undone_group(self):
        # DOCUMENTS: the instructor's Undo remains authoritative when discovery is revisited.
        job = self.recovered_scan([P2, P3, P2], [P2, P3, P2])
        message = job["message_id"]
        self.assertEqual(self.scans.sequence.auto_group(message), 4)
        run = self.scans.review_item(message)["sequence"]["runs"][0]
        self.scans.sequence.undo(message, run["id"])
        for _ in range(3):
            self.app.extensions["frq_grading"].process_scan_one()
        runs = self.scans.review_item(message)["sequence"]["runs"]
        self.assertEqual(len(runs), 1)
        self.assertIsNotNone(runs[0]["undone_at"])

    def test_a_copy_labeled_on_every_page_becomes_its_reviewed_paper_and_a_missing_page_holds_only_its_copy(self):
        message = self.scan((1, ["L1", "L2", "L3"]), (2, ["L1", "L2"]))
        before = self.overview(message)
        self.assertIn("1 copy has every page and becomes its student’s reviewed paper when the scan passes review.", [s["text"] for s in before["steps"]])
        missing = next(s for s in before["steps"] if s["tool"] == "assemblies" and s["kind"] == "check")
        self.assertNotEqual(missing.get("holds"), "scan")  # One copy's missing page holds back only that copy.
        self.review(self.job(message))
        [complete] = self.revisions(1)
        self.assertEqual(([p["page_number"] for p in json.loads(complete["selection_json"])], complete["partial"], complete["actor"]), ([1, 2, 3], 0, "inbox-review"))
        self.assertEqual(self.revisions(2), [])  # Page 3 never arrived: the per-copy assembly review decides.
        after = self.overview(message)
        self.assertTrue(any(s["tool"] == "assemblies" and s["kind"] == "check" for s in after["steps"]))
        # Its last page arrives in a later scan: once that scan is reviewed too, the copy follows by itself.
        late = self.scan((2, ["L3"]))
        self.review(self.job(late))
        [finished] = self.revisions(2)
        self.assertEqual([p["page_number"] for p in json.loads(finished["selection_json"])], [1, 2, 3])

    def test_a_batch_scanned_in_stacks_assembles_a_paper_split_between_two_files(self):
        """INBOX "BATCHES SCANNED IN STACKS" (requirement, 2026-10-24): each page's own label names its copy and page, whichever file it came in, so a
        paper whose last page starts the next stack becomes its student's reviewed paper once both scans pass review, in either
        order. While the other scan still waits for its review, the first one says so instead of asking the instructor to save it."""
        first = self.scan((1, ["L1", "L2", "L3"]), (2, ["L1", "L2"]))
        second = self.scan((2, ["L3"]), (3, ["L1", "L2", "L3"]))
        self.review(self.job(first))
        self.assertEqual((len(self.revisions(1)), self.revisions(2)), (1, []))
        steps = [(s["kind"], s["text"]) for s in self.overview(first)["steps"] if s["tool"] == "assemblies"]
        self.assertEqual(steps, [("info", "1 copy continues in another scan that has not passed its review yet, and becomes its student’s reviewed paper once it does.")])
        self.review(self.job(second))
        [split] = self.revisions(2)
        self.assertEqual([p["page_number"] for p in json.loads(split["selection_json"])], [1, 2, 3])
        self.assertEqual(len(self.revisions(3)), 1)
        for message in (first, second):
            self.assertFalse([s for s in self.overview(message)["steps"] if s["tool"] == "assemblies"])
        # The later stack reviewed first: the earlier one's review assembles the paper.
        late = self.scan((4, ["L3"]))
        early = self.scan((4, ["L1", "L2"]))
        self.review(self.job(late))
        self.assertEqual(self.revisions(4), [])
        self.review(self.job(early))
        self.assertEqual([p["page_number"] for p in json.loads(self.revisions(4)[0]["selection_json"])], [1, 2, 3])

    def test_a_label_of_paper_no_longer_current_is_a_flag_on_its_page_not_on_the_scan(self):
        catalog = self.documents.catalog()
        old = self.documents.create_batch(self.batch["assignment_version_id"], catalog["sections"][0]["id"], 1, str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        printed = PdfReader(io.BytesIO(self.documents.download(old["id"])))
        self.documents.transition(old["id"], "supersede")
        writer = PdfWriter()
        for index in range(3):
            writer.add_page(self.printed.pages[index])
        writer.add_page(printed.pages[0])
        message = self.submit(writer)
        recognition = self.scans.review_item(message)["recognition"]
        self.assertEqual((recognition["verdict"], recognition["doubtful_labels"]), ("recognized", {"superseded": 1}))
        self.assertEqual([p["current"] for p in recognition["papers"]], [True, False])  # Current paper first: it is what the scan is for.
        self.assertEqual([p["batch_id"] for p in recognition["current_papers"]], [self.batch["id"]])
        flag = next(s for s in self.overview(message)["steps"] if s["tool"] == "pages" and s["kind"] == "check")
        self.assertIn("1 label needs a look (1 superseded)", flag["text"])
        self.assertNotEqual(flag.get("holds"), "scan")
        # Two current assignments on one scan still stop the scan, and the worker separates it into one scan for each (INBOX "SEPARATING A SCAN").
        other = self.documents.create_assignment(catalog["definitions"][0]["id"], "quiz-1", "Quiz 1")
        version = self.documents.register_version(other["id"], "A", (fixture.FIXTURES / "letter.pdf").read_bytes(),
                                                  {"main.tex": (fixture.FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
        quiz = self.documents.create_batch(version["id"], catalog["sections"][0]["id"], 1, str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        writer = PdfWriter()
        writer.add_page(self.printed.pages[0])
        writer.add_page(PdfReader(io.BytesIO(self.documents.download(quiz["id"]))).pages[0])
        mixed = self.submit(writer)
        self.assertEqual(self.scans.review_item(mixed)["recognition"]["verdict"], "mixed")
        stop = next(s for s in self.overview(mixed)["steps"] if s["kind"] == "wait")
        self.assertEqual(stop["text"], "The labels name more than one assignment (Exam 1 and Quiz 1). Faculty is separating these pages into one scan for each.")
        with connect(self.path) as db:
            self.assertTrue(self.app.extensions["frq_grading"].review_blockers(db, mixed))

    def test_papers_grouped_after_the_review_follow_the_rest_of_the_scan(self):
        # The last exam is a side short, so the scan is not grouped by itself (grouping stays all-or-nothing), and it passes review.
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2], [P2, P3])
        message = job["message_id"]
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"], [])
        self.review(job)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assembly_revisions").fetchone()[0], 0)
        # The instructor's Group button afterwards: the two exams that add up become reviewed papers at once; the short one waits.
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual((plan["counts"]["ready"], plan["counts"]["review"]), (2, 1))
        applied = self.post(message, sides=4, fingerprint=plan["fingerprint"])
        self.assertEqual(applied.status_code, 201, applied.json)
        with connect(self.path) as db:
            rows = [tuple(r) for r in db.execute("SELECT sides,pages,actor FROM scan_sequence_assemblies ORDER BY created_at")]
            reasons = {r[0] for r in db.execute("SELECT reason FROM assembly_revisions")}
        self.assertEqual(rows, [(4, 4, "automatic"), (4, 4, "automatic")])
        self.assertEqual(reasons, {"First-page label, even label-to-label cadence and pages in scan order, grouped after this scan passed its Inbox review."})

    def test_the_worker_assembles_papers_made_ready_after_the_review(self):
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2], [P2, P3])
        message = job["message_id"]
        self.review(job)
        plan = self.scans.sequence.preview(message, 4)
        self.scans.sequence.apply(message, 4, plan["fingerprint"], actor="workspace", identify=True)  # Grouped some other way, not assembled.
        frq = self.app.extensions["frq_grading"]
        self.assertTrue(frq.assemble_later_one())
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_sequence_assemblies WHERE actor='automatic'").fetchone()[0], 2)
            event = json.loads(db.execute("SELECT detail FROM inbox_events WHERE kind='first_page_papers_assembled' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual((event["actor"], event["assembled"]), ("automatic", 2))
        # The short exam is left for the per-copy review, and the worker does not look at it again until something changes.
        self.assertFalse(frq.assemble_later_one())
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inbox_events WHERE kind='first_page_papers_assembled'").fetchone()[0], 1)

    def test_transient_assembly_failure_is_retried_without_a_page_edit(self):
        from unittest.mock import patch
        from werkzeug.exceptions import Forbidden
        _, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2], [P2, P3])
        self.review(job)
        message = job['message_id']
        plan = self.scans.sequence.preview(message, 4)
        self.scans.sequence.apply(message, 4, plan['fingerprint'], actor='workspace', identify=True)
        frq = self.app.extensions['frq_grading']
        with patch('grading.intake.time.monotonic', return_value=100):
            with patch.object(self.scans.sequence, 'assemble_reviewed', side_effect=Forbidden('temporarily unavailable')) as attempt:
                self.assertFalse(frq.assemble_later_one())
                self.assertFalse(frq.assemble_later_one())
                self.assertEqual(attempt.call_count, 1)  # Respect the cooldown.
        with patch('grading.intake.time.monotonic', return_value=121):
            self.assertTrue(frq.assemble_later_one())
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_sequence_assemblies WHERE actor='automatic'").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
