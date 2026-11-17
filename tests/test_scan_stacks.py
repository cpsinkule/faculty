"""INBOX "BATCHES SCANNED IN STACKS" (requirement, 2026-10-24): a paper labeled on its first page only that starts at the end of one stack and ends at the top of the next is
grouped as one paper, and both stacks group by themselves."""
import io
import json
import sqlite3
import unittest
import uuid

import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from bubble_fixture import sheet
from bubble_sheets.reader import letter
from rosters import connect
import test_sequence as fixture
from test_bubble_sheets import KEY
from test_sequence import BLANK, P2, P3


class StackTests(unittest.TestCase):
    setUp = fixture.SequenceTests.setUp
    review = fixture.SequenceTests.review

    def labels(self, count):
        """A one-page label sheet per student, stuck on the first page of a four-sided exam."""
        catalog = self.documents.catalog()
        stamps = self.documents.register_stamp_pages(catalog["assignments"][0]["id"], 1)
        self.label_batch = self.documents.create_batch(stamps["id"], catalog["sections"][0]["id"], count, str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        return PdfReader(io.BytesIO(self.documents.download(self.label_batch["id"])))

    def stack(self, labels, items, sender="scanner-folder/epson", inspect=True):
        """One stack from the scanner folder: a number is that copy's label, then its sides."""
        writer = PdfWriter()
        for item in items:
            if isinstance(item, int):
                writer.add_page(labels.pages[item - 1])
            elif item == BLANK:
                writer.add_blank_page(612, 792)
            else:
                writer.add_page(self.plain.pages[int(item[1]) - 1])
        writer.add_metadata({"/Title": str(uuid.uuid4())})  # Inbox dedupes identical content
        content = io.BytesIO()
        writer.write(content)
        self.scans.alias_ocr.enabled = True
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content.getvalue()}], source="cli", sender=sender,
                                idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        if inspect:
            self.inspect()
        return job

    def inspect(self):
        while self.scans.process_one():
            pass

    def runs(self, job):
        return [(run["automatic"], run["sides"], run["packets"]) for run in self.scans.review_item(job["message_id"])["sequence"]["runs"]]

    def paper(self, copy_number):
        """The copy's reviewed paper as (scan, attachment page) per page number, or None."""
        with connect(self.path) as db:
            row = db.execute("""SELECT ar.selection_json FROM assembly_revisions ar JOIN printed_copies c ON c.id=ar.printed_copy_id
                                WHERE c.print_batch_id=? AND c.copy_number=? ORDER BY ar.revision DESC LIMIT 1""",
                             (self.label_batch["id"], copy_number)).fetchone()
            if not row:
                return None
            pages = json.loads(row[0])
            where = {r["id"]: (r["message_id"], r["source_page_index"]) for r in db.execute(
                f"SELECT id,message_id,source_page_index FROM scan_pages WHERE id IN ({','.join('?' * len(pages))})", [p["scan_page_id"] for p in pages])}
            return [(p["page_number"], *where[p["scan_page_id"]]) for p in pages]

    def test_a_paper_split_between_two_stacks_is_one_paper_and_both_stacks_group_by_themselves(self):
        labels = self.labels(5)
        first = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2, 3, P2])  # copy 3's last two sides start the next stack
        second = self.stack(labels, [P3, P2, 4, P2, P3, P2, 5, P2, P3, P2])
        self.assertEqual((self.runs(first), self.runs(second)), ([(True, 4, 3)], [(True, 4, 2)]))
        plan = self.scans.sequence.preview(first["message_id"], 4)
        split = next(exam for exam in plan["exams"] if exam["copy_number"] == 3)
        self.assertEqual(split["continues_in"], {"message_id": second["message_id"], "first_page": 1, "last_page": 2, "sides": 2})
        self.assertNotIn("continuation_rows", plan)
        self.assertEqual(self.scans.sequence.preview(second["message_id"], 4)["continued_pages"], 2)
        # Nothing about either scan needs the instructor, and neither lists the pages the other one's paper holds.
        for job in (first, second):
            with connect(self.path) as db:
                steps = self.scans.overview(db, job["message_id"])["steps"]
            self.assertFalse([step["text"] for step in steps if step["kind"] in ("todo", "check") and step.get("action") != "grading_review"])
        # The paper waits for BOTH scans' reviews.
        self.review(first)
        self.assertEqual(self.paper(1), [(1, first["message_id"], 1), (2, first["message_id"], 2), (3, first["message_id"], 3), (4, first["message_id"], 4)])
        self.assertIsNone(self.paper(3))
        self.review(second)
        self.assertEqual(self.paper(3), [(1, first["message_id"], 9), (2, first["message_id"], 10), (3, second["message_id"], 1), (4, second["message_id"], 2)])
        self.assertEqual(self.paper(4), [(1, second["message_id"], 3), (2, second["message_id"], 4), (3, second["message_id"], 5), (4, second["message_id"], 6)])
        # Its PDF holds all four sides.
        with connect(self.path) as db:
            packet = db.execute("""SELECT k.id FROM scan_packets k JOIN printed_copies c ON c.id=k.printed_copy_id WHERE c.print_batch_id=? AND c.copy_number=3""",
                                (self.label_batch["id"],)).fetchone()[0]
        content, _ = self.scans.sequence.packet_pdf(packet)
        self.assertEqual(len(PdfReader(io.BytesIO(content)).pages), 4)

    def test_the_later_stack_reviewed_first_or_inspected_first(self):
        labels = self.labels(5)
        first = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2, 3, P2, P3], inspect=False)
        # The first stack's inspection is behind the second's (a long scan resumes in chunks behind other scans).
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET updated_at='9999' WHERE message_id=?", (first["message_id"],))
        second = self.stack(labels, [P2, 4, P2, P3, P2, 5, P2, P3, P2], inspect=False)
        self.assertTrue(self.scans.process_one())
        with connect(self.path) as db:
            states = dict(db.execute("SELECT message_id,status FROM document_decode_jobs").fetchall())
        self.assertEqual((states[second["message_id"]], states[first["message_id"]]), ("complete", "pending"))
        self.inspect()
        self.assertEqual((self.runs(first), self.runs(second)), ([(True, 4, 3)], [(True, 4, 2)]))
        self.review(second)
        self.assertIsNone(self.paper(3))
        self.review(first)
        self.assertEqual([page[0] for page in self.paper(3)], [1, 2, 3, 4])
        self.assertEqual(self.paper(3)[3][1:], (second["message_id"], 1))

    def test_stacks_are_joined_only_when_the_sides_add_up_from_the_same_scanner(self):
        labels = self.labels(6)
        # Three sides at the top of the next stack where copy 3 lacks two: nothing is joined, and both wait as before.
        first = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2, 3, P2])
        second = self.stack(labels, [P3, P2, P2, 4, P2, P3, P2, 5, P2, P3, P2])
        self.assertEqual((self.runs(first), self.runs(second)), ([], []))
        self.assertNotIn("continues_in", next(exam for exam in self.scans.sequence.preview(first["message_id"], 4)["exams"] if exam["copy_number"] == 3))
        # The right count from another scanner is another batch: not joined either.
        labels = self.labels(4)
        third = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2, 3, P2], sender="scanner-folder/brother")
        fourth = self.stack(labels, [P3, P2, 4, P2, P3, P2], sender="scanner-folder/epson")
        self.assertEqual(self.runs(third), [])
        self.assertNotIn("continued_pages", self.scans.sequence.preview(fourth["message_id"], 4))

    def test_bubble_sheets_beside_the_booklets_are_no_sides_of_any_paper(self):
        # 2026-10-24, the first home-scanned Exam 2 stack: each student's bubble sheet lay before or after their booklet (or another
        # student's), the Epson cropped every page to the paper's edge, and in grey a sheet's empty back is not blank. The papers
        # still add up once the sheets and their backs are left out, so the stack groups by itself.
        from document_identity.media import render_barcode, render_pages
        catalog = self.documents.catalog()
        stamps = self.documents.register_stamp_pages(catalog["assignments"][0]["id"], 1)
        self.label_batch = self.documents.create_batch(stamps["id"], catalog["sections"][0]["id"], 3, str(uuid.uuid4()), sheet_labels=True)
        self.assertTrue(self.documents.build_one())
        labels = PdfReader(io.BytesIO(self.documents.download(self.label_batch["id"])))
        with connect(self.path) as db:
            tokens = {entry["copy_number"]: entry["token"] for entry in self.documents.label_manifest(db, self.label_batch["id"]) if entry.get("sheet")}

        def trimmed(image, size=(1684, 2206)):
            """The page as a home scanner keeps it: cropped to the paper's edge, at 200 DPI."""
            image, buffer = image.resize(size), io.BytesIO()
            document = canvas.Canvas(buffer, pagesize=(size[0] * .36, size[1] * .36))  # 72 points to 200 pixels
            document.drawImage(ImageReader(image), 0, 0, width=size[0] * .36, height=size[1] * .36)
            document.save()
            return PdfReader(buffer).pages[0]

        def labeled(copy):
            image = sheet(KEY)
            image.paste(next(render_pages(render_barcode(tokens[copy]), 200)).convert("L"), (760, 40))
            return trimmed(image)
        back = self.plain.pages[1]  # an empty back with the sheet showing through reads as written
        writer = PdfWriter()
        for page in [labeled(2), back, labels.pages[0], *[self.plain.pages[i] for i in (1, 2, 1)], labels.pages[1], *[self.plain.pages[i] for i in (1, 2, 1)],
                     labeled(1), back, trimmed(sheet(KEY)), back, labels.pages[2], *[self.plain.pages[i] for i in (1, 2, 1)]]:
            writer.add_page(page)
        writer.add_metadata({"/Title": str(uuid.uuid4())})
        content = io.BytesIO()
        writer.write(content)
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content.getvalue()}], source="cli",
                                sender="scanner-folder/epson", idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        self.inspect()
        with connect(self.path) as db:
            # The unlabeled sheet is known by its marks alone, at the size the scanner kept it.
            recognized = {r[0] for r in db.execute("""SELECT sp.source_page_index FROM bubble_detections b JOIN scan_pages sp ON sp.id=b.scan_page_id
                                                       WHERE b.message_id=? AND b.kind='sheet'""", (job["message_id"],))}
            size = db.execute("SELECT raster_artifact_id FROM scan_pages WHERE message_id=? AND source_page_index=13", (job["message_id"],)).fetchone()[0]
        self.assertEqual(recognized, {1, 11, 13})
        raster = np.asarray(Image.open(io.BytesIO(self.inbox.read_artifact(size)[1])).convert("L"))
        self.assertNotEqual(raster.shape, (2200, 1700))
        self.assertEqual(letter(raster).shape, (2200, 1700))
        self.assertEqual(self.runs(job), [(True, 4, 3)])
        with connect(self.path) as db:
            steps = self.scans.overview(db, job["message_id"])["steps"]
        # Only whose sheets they are is left to say (read by themselves once the scan's labels associate it: GRADING's intake).
        self.assertEqual([step["text"] for step in steps if step["kind"] in ("todo", "check") and step.get("action") != "grading_review"],
                         ["Say whose 3 bubble sheets these are: choose the assignment and sections, then read them."])
        self.review(job)
        self.assertEqual([[page[2] for page in self.paper(copy)] for copy in (1, 2, 3)], [[3, 4, 5, 6], [7, 8, 9, 10], [15, 16, 17, 18]])

    def test_a_paper_whose_sticker_code_was_not_read_is_found_by_its_printed_alias(self):
        # 2026-10-24: stickers whose code ran off the sticker's top edge held whole Exam 2 stacks until the instructor typed the alias
        # printed under the code. The first page of a paper the grouping holds is read for that alias; an exact alias identifies it.
        from unittest.mock import patch
        labels = self.labels(5)
        with connect(self.path) as db:
            alias = db.execute("""SELECT p.recovery_alias FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id
                                  WHERE c.print_batch_id=? AND c.copy_number=2 AND p.page_number=1""", (self.label_batch["id"],)).fetchone()[0]
        read = lambda content, band="top": {"reads": [{"rotation": 0, "band": "top", "text": f"Seat B4 McCall, Kathleen {alias[:6]} {alias[6:]}", "truncated": False},
                                                       {"rotation": 180, "band": "top", "text": "", "truncated": False}],
                                            "source_sha256": __import__("hashlib").sha256(content).hexdigest()}
        corners = lambda content: {"reads": [], "source_sha256": __import__("hashlib").sha256(content).hexdigest()}  # nothing at the corners
        with patch("document_identity.alias_ocr.recognize_bands", side_effect=read) as reader, \
                patch("document_identity.alias_ocr.recognize_aliases", side_effect=corners):
            job = self.stack(labels, [1, P2, P3, P2, P3, P2, P3, P2, 3, P2, P3, P2, 4, P2, P3, P2, 5, P2, P3, P2])  # copy 2's label not read
            self.assertEqual(self.runs(job), [])  # held: the paper starting on page 5 has no label read
            while self.scans.alias_ocr.process_one():
                pass
            self.assertEqual(reader.call_count, 1)  # only the first page of the held paper
            # The worker's look at every changed scan (GRADING intake) groups it by the ordinary rule.
            while self.app.extensions["frq_grading"].process_scan_one():
                pass
        self.assertEqual(self.runs(job), [(True, 4, 5)])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT status FROM scan_pages WHERE message_id=? AND source_page_index=5", (job["message_id"],)).fetchone()[0], "resolved")
        # A paper's sticker on a scan with corner-stamped paper is not read this way (that reader looks at the corner positions).
        with connect(self.path) as db:
            self.assertTrue(self.scans.alias_ocr.sticker_scan(db, job["message_id"]))

    def test_a_paper_scanned_in_two_stacks_is_kept_once_and_the_rest_group(self):
        # Requirement, 2026-10-24: Tessa Pearson's and Hana Blakely's papers went through the scanner in two stacks, and
        # the second stack waited for them. A sticker is one paper: the copy grouped first is kept, the repeat needs nothing.
        labels = self.labels(5)
        first = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2, 3, P2, P3, P2])
        writer = PdfWriter()
        for index, item in enumerate([3, P2, P3, P2, 4, P2, P3, P2, 5, P2, P3, P2]):
            page = writer.add_page(labels.pages[item - 1] if isinstance(item, int) else self.plain.pages[int(item[1]) - 1])
            if index == 0:
                page.merge_page(self.plain.pages[2])  # scanned again: the same label on another page image
        writer.add_metadata({"/Title": str(uuid.uuid4())})
        content = io.BytesIO()
        writer.write(content)
        second = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content.getvalue()}], source="cli",
                                   sender="scanner-folder/epson", idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        self.inspect()
        self.assertEqual((self.runs(first), self.runs(second)), ([(True, 4, 3)], [(True, 4, 2)]))
        plan = self.scans.sequence.preview(second["message_id"], 4)
        self.assertEqual(([e["copy_number"] for e in plan["exams"] if e.get("repeat_of")], plan["counts"]["repeats"]), ([3], 1))
        with connect(self.path) as db:
            states = lambda job: [r[0] for r in db.execute("SELECT review_state FROM scan_pages WHERE message_id=? AND status='resolved' ORDER BY source_page_index",
                                                         (job["message_id"],))]
            self.assertEqual((states(first), states(second)), (["candidate"] * 3, ["rejected", "candidate", "candidate"]))
            for job in (first, second):
                steps = self.scans.overview(db, job["message_id"])["steps"]
                self.assertFalse([step["text"] for step in steps if step["kind"] in ("todo", "check") and step.get("action") != "grading_review"])
        self.review(first)
        self.review(second)
        self.assertEqual([page[1] for page in self.paper(3)], [first["message_id"]] * 4)

    def test_a_stack_scanned_again_whole_is_set_aside_by_itself(self):
        # Exam 2, 2026-10-25: a morning stack held only booklets grouped the night before (and new bubble sheets). Every paper in it is
        # a repeat, so nothing is "ready" to group; the repeats are still set aside, and the kept papers stay assembled.
        labels = self.labels(3)
        first = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2, 3, P2, P3, P2])
        writer = PdfWriter()
        for item in [2, P2, P3, P2, 3, P2, P3, P2]:
            page = writer.add_page(labels.pages[item - 1] if isinstance(item, int) else self.plain.pages[int(item[1]) - 1])
            if isinstance(item, int):
                page.merge_page(self.plain.pages[2])  # scanned again: the same label on another page image
        writer.add_metadata({"/Title": str(uuid.uuid4())})
        content = io.BytesIO()
        writer.write(content)
        second = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content.getvalue()}], source="cli",
                                   sender="scanner-folder/epson", idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        self.inspect()
        plan = self.scans.sequence.preview(second["message_id"], 4)
        self.assertEqual((plan["counts"]["ready"], plan["counts"]["repeats"], plan["counts"]["review"]), (0, 2, 0))
        with connect(self.path) as db:
            states = lambda job: [r[0] for r in db.execute("SELECT review_state FROM scan_pages WHERE message_id=? AND status='resolved' ORDER BY source_page_index",
                                                         (job["message_id"],))]
            self.assertEqual((states(first), states(second)), (["candidate"] * 3, ["rejected", "rejected"]))
            steps = self.scans.overview(db, second["message_id"])["steps"]
            self.assertFalse([step["text"] for step in steps if step["kind"] in ("todo", "check") and step.get("action") != "grading_review"])
        self.review(first)
        self.review(second)
        self.assertEqual([page[1] for page in self.paper(2)], [first["message_id"]] * 4)

    def test_undoing_the_grouping_clears_the_next_stacks_pages_too(self):
        labels = self.labels(5)
        first = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2, 3, P2])
        second = self.stack(labels, [P3, P2, 4, P2, P3, P2, 5, P2, P3, P2])
        with connect(self.path) as db:
            identified = lambda: [r[0] for r in db.execute("SELECT status FROM scan_pages WHERE message_id=? ORDER BY source_page_index LIMIT 2", (second["message_id"],))]
            self.assertEqual(identified(), ["manual", "manual"])
        run = self.scans.review_item(first["message_id"])["sequence"]["runs"][0]
        self.scans.sequence.undo(first["message_id"], run["id"])
        with connect(self.path) as db:
            self.assertEqual([r[0] for r in db.execute("SELECT status FROM scan_pages WHERE message_id=? ORDER BY source_page_index LIMIT 2", (second["message_id"],))],
                             ["unreadable", "unreadable"])

    def test_a_packet_never_takes_a_page_of_another_senders_scan(self):
        labels = self.labels(4)
        first = self.stack(labels, [1, P2, P3, P2, 2, P2, P3, P2])
        other = self.stack(labels, [3, P2, P3, P2, 4, P2, P3, P2], sender="scanner-folder/brother")
        with connect(self.path) as db:
            packet = db.execute("SELECT id FROM scan_packets WHERE message_id=? LIMIT 1", (first["message_id"],)).fetchone()[0]
            page = db.execute("SELECT id FROM scan_pages WHERE message_id=? AND source_page_index=2", (other["message_id"],)).fetchone()[0]
            with self.assertRaisesRegex(sqlite3.IntegrityError, "next stack from its sender"):
                db.execute("INSERT INTO scan_packet_pages VALUES(?,?,?)", (packet, page, 9))


if __name__ == "__main__":
    unittest.main()
