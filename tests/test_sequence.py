"""Pages identified from a first-page label: the label anchors an exam and the side count checks it."""
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter

from app import create_app
from rosters import connect
from test_storage import HEADERS

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"
P1, P2, P3, BLANK = "p1", "p2", "p3", "blank"


class SequenceTests(unittest.TestCase):
    def test_nested_label_mode_identifiers_are_rejected_before_deduplication(self):
        """DOCUMENTS: malformed identifiers are a request error, never an unhandled TypeError."""
        response = self.client.post("/api/documents/messages/missing/label-mode",
                                    json={"batch_ids": [["nested"]], "decision": "first_page"}, headers=HEADERS)
        self.assertEqual(response.status_code, 400, response.json)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(self.temp.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.documents = self.app.extensions["documents"]
        self.scans = self.app.extensions["document_scans"]
        self.inbox = self.app.extensions["inbox"]
        self.path = self.app.config["DATABASE"]
        created = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                       "sections": [501, 502], "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(created.status_code, 201, created.json)
        catalog = self.documents.catalog()
        assignment = self.documents.create_assignment(catalog["definitions"][0]["id"], "exam-1", "Exam 1")
        version = self.documents.register_version(assignment["id"], "A", (FIXTURES / "letter.pdf").read_bytes(),
                                                  {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
        batch = self.documents.create_batch(version["id"], catalog["sections"][0]["id"], 5, str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        self.batch = self.documents.get_batch(batch["id"])
        self.assertEqual(self.batch["status"], "ready", self.batch["error"])
        self.printed = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        self.plain = PdfReader(io.BytesIO((FIXTURES / "letter.pdf").read_bytes()))
        with connect(self.path) as db:
            self.manifest = self.documents.manifest(db, batch["id"])

    def scan(self, *exams):
        """Each exam is (copy, sides); a side is a labeled page ("L1"), an unlabeled page ("p2") or "blank"."""
        writer = PdfWriter()
        for copy, sides in exams:
            for side in sides:
                if side == BLANK:
                    writer.add_blank_page(612, 792)
                elif side.startswith("L"):
                    writer.add_page(self.printed.pages[(copy - 1) * 3 + int(side[1]) - 1])
                else:
                    writer.add_page(self.plain.pages[int(side[1]) - 1])
        return self.submit(writer)

    def submit(self, writer):
        content = io.BytesIO()
        writer.write(content)
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content.getvalue()}], source="email", sender="scanner",
                                original=b"retained-email:" + content.getvalue(), idempotency_key=str(uuid.uuid4()),
                                authenticated=True, instructor_review_required=True)
        self.assertTrue(self.scans.process_one())
        return job["message_id"]

    def post(self, message_id, path="", **data):
        return self.client.post(f"/api/documents/messages/{message_id}/sequence{path}", json=data, headers=HEADERS)

    def test_a_scan_being_inspected_says_how_far_it_has_got_and_offers_nothing_else(self):
        message = self.scan((1, ["L1", P2, P3]))
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT pages_total FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()[0], 3)
        # Mid-inspection (3 of 5 pages retained so far), queued again, and stopped: no assignment step, no partial count as the scan.
        for status, kind, text in (("running", "wait", "Checking its pages for labels and bubble sheets: 3 of 5 done."),
                                   ("pending", "wait", "Waiting for the worker to continue checking its pages (3 of 5 done)."),
                                   ("failed", "todo", "Checking its pages stopped after 3 of 5. Open it and retry the inspection.")):
            with self.subTest(status=status):
                with connect(self.path) as db:
                    db.execute("UPDATE document_decode_jobs SET status=?,pages_total=5 WHERE message_id=?", (status, message))
                overview = self.scans.review_item(message)["overview"]
                self.assertEqual((overview["state"], overview["facts"], [step["text"] for step in overview["steps"]]), (kind, ["5 pages"], [text]))
                self.assertIsNone(overview["steps"][0]["action"])
        # Work the worker is doing after inspection is listed with its progress beside what is left for the instructor.
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (message,))
            db.execute("""INSERT INTO scan_version_sorts(message_id,attempt,status,settings_json,actor,pages_total,result_json,error,created_at,updated_at)
                          VALUES(?,1,'running','{}','workspace',2,'{}','',?,?)""", (message, "2026-09-24T00:00:00+00:00", "2026-09-24T00:00:00+00:00"))
        steps = self.scans.review_item(message)["overview"]["steps"]
        self.assertIn({"kind": "info", "progress": True, "text": "Sorting by printed version: 0 of 2 pages read.", "tool": "sort", "action": None}, steps)
        # Shown, never a reason to wait: sorting holds nothing back.
        self.assertNotIn("wait", {step["kind"] for step in steps})

    def test_an_arrival_without_a_pdf_is_its_scan_stage_alone(self):
        # Nothing to inspect, file, review or grade: the graph is one Scan stage, done, in words that say why (UX overhaul 2026-09-26).
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "cover-note.txt", "content": b"No PDF here " + uuid.uuid4().bytes}],
                                source="email", sender="scanner", original=b"retained-email:no-pdf", idempotency_key=str(uuid.uuid4()),
                                authenticated=True, instructor_review_required=True)
        self.scans.process_one()
        overview = self.scans.review_item(job["message_id"])["overview"]
        self.assertEqual([stage["key"] for stage in overview["stages"]], ["scan"])
        self.assertEqual((overview["headline"], overview["state"], overview["stages"][0]["state"], overview["stages"][0]["detail"]),
                         ("No scanned pages in this arrival", "done", "done", "Nothing to inspect: this arrival holds no PDF."))
        # A scan with a PDF still has every stage its assignment and review need.
        message = self.scan((1, ["L1", P2, P3]))
        self.assertIn("review", [stage["key"] for stage in self.scans.review_item(message)["overview"]["stages"]])

    def test_labels_and_the_side_count_identify_the_pages_between_them(self):
        message = self.scan((1, ["L1", P2, P3]), (2, ["L1", P2, P3]), (3, ["L1", P2, P3]))
        # Evenly spaced labels leave nothing to decide: inspection grouped the scan by the label-to-label distance on its own.
        # It only groups. Whether the other pages should carry labels at all is the instructor's answer, so none takes an identity.
        grouped = self.scans.review_item(message)
        automatic, = grouped["sequence"]["runs"]
        self.assertEqual((automatic["automatic"], automatic["sides"], automatic["packets"], automatic["pages"], automatic["review"], automatic["unlabeled"]), (True, 3, 3, 0, 0, 0))
        self.assertEqual([page["status"] for page in grouped["pages"]], ["resolved", "unreadable", "unreadable"] * 3)
        self.assertEqual([page["packet"]["side"] for page in grouped["pages"]], [1, 2, 3] * 3)
        self.assertTrue(grouped["instructor_review_required"])
        with connect(self.path) as db:
            event = json.loads(db.execute("SELECT detail FROM inbox_events WHERE kind='sequence_page_identification'").fetchone()[0])
        self.assertEqual((event["actor"], event["automatic"], event["sides"]), ("automatic", True, 3))
        # Undone, it is never redone behind the instructor's back; the rest of this test is the instructor's own preview and approval.
        self.assertEqual(self.post(message, f"/{automatic['id']}/undo").status_code, 200)
        self.assertIsNone(self.scans.sequence.auto_group(message))
        for bad in (0, 201, "3", None, True):
            self.assertEqual(self.post(message, "/preview", sides=bad).status_code, 400, bad)
        self.assertEqual(self.post("missing", "/preview", sides=3).status_code, 404)
        plan = self.post(message, "/preview", sides=3).json
        self.assertEqual(plan["counts"], {"pages": 9, "labels": 3, "ready": 3, "grouped": 3, "identified": 6, "complete": 0, "review": 0, "unlabeled": 0, "turned": 0})
        self.assertEqual(plan["runs"], [])
        # The scan suggests its own count: the usual distance between page 1 labels.
        self.assertEqual(self.scans.review_item(message)["sequence"]["suggested_sides"], 3)
        self.assertEqual([(exam["copy_number"], exam["first_page"], exam["last_page"], exam["written"], exam["problems"],
                           [(item["source_page_index"], item["page_number"]) for item in exam["assign"]]) for exam in plan["exams"]],
                         [(1, 1, 3, 3, [], [(2, 2), (3, 3)]), (2, 4, 6, 3, [], [(5, 2), (6, 3)]), (3, 7, 9, 3, [], [(8, 2), (9, 3)])])
        # A preview writes nothing.
        before = self.scans.review_item(message)
        self.assertEqual([page["status"] for page in before["pages"]], ["resolved", "unreadable", "unreadable"] * 3)
        self.assertEqual(([run["undone_at"] is None for run in before["sequence"]["runs"]], before["sequence"]["packets"]), ([False], []))
        # The wrong side count finds labels where it expects none, and nothing is identified.
        wrong = self.post(message, "/preview", sides=2).json
        self.assertEqual((wrong["counts"]["ready"], wrong["counts"]["review"]), (0, 3))
        self.assertIn("3 sides arrive before the next label, which is not a whole number of 2-side papers", wrong["exams"][0]["problems"][0])
        self.assertEqual(self.post(message, sides=2, fingerprint=wrong["fingerprint"]).status_code, 409)
        self.assertEqual(self.post(message, sides=3, fingerprint="stale").status_code, 409)
        applied = self.post(message, sides=3, fingerprint=plan["fingerprint"])
        self.assertEqual(applied.status_code, 201, applied.json)
        pages = applied.json["pages"]
        self.assertEqual([(page["status"], page["copy_number"], page["page_number"]) for page in pages],
                         [(status, copy, number) for copy in (1, 2, 3) for status, number in (("resolved", 1), ("manual", 2), ("manual", 3))])
        self.assertEqual([page["sequence"] and (page["sequence"]["label_page"], page["sequence"]["side"]) for page in pages[:3]], [None, (1, 2), (1, 3)])
        self.assertTrue(applied.json["instructor_review_required"])
        self.assertEqual([assembly["missing"] for assembly in applied.json["assemblies"]], [[], [], []])
        run = applied.json["sequence"]["runs"][0]
        self.assertEqual((run["sides"], run["exams"], run["packets"], run["pages"], run["undone_at"], run["automatic"]), (3, 3, 3, 6, None, False))
        # Each exam is kept as that copy's packet, downloadable as one PDF in scan order.
        packets = applied.json["sequence"]["packets"]
        self.assertEqual([(item["copy_number"], item["first_page"], item["last_page"], item["sides"], item["title"]) for item in packets],
                         [(1, 1, 3, 3, "Exam 1"), (2, 4, 6, 3, "Exam 1"), (3, 7, 9, 3, "Exam 1")])
        self.assertEqual([(page["packet"]["copy_number"], page["packet"]["side"]) for page in pages[3:6]], [(2, 1), (2, 2), (2, 3)])
        download = self.client.get(f"/api/documents/packets/{packets[1]['id']}/download")
        self.assertEqual((download.status_code, download.mimetype, len(PdfReader(io.BytesIO(download.data)).pages)), (200, "application/pdf", 3))
        self.assertIn("Exam 1 - copy 2.pdf", download.headers["Content-Disposition"])
        self.assertEqual(self.client.get("/api/documents/packets/missing/download").status_code, 404)
        with connect(self.path) as db:
            event = db.execute("SELECT detail FROM inbox_events WHERE kind='sequence_page_identification' ORDER BY id DESC").fetchone()[0]
        self.assertEqual((json.loads(event)["method"], json.loads(event)["pages"], json.loads(event)["actor"]), ("first_page_label_and_side_count", 6, "workspace"))
        # Identified pages now read as labeled pages of their copy: nothing is left to do, and nothing is done twice.
        again = self.post(message, "/preview", sides=3).json
        self.assertEqual((again["counts"]["grouped"], again["counts"]["identified"], again["counts"]["complete"], again["counts"]["review"]), (0, 0, 3, 0))
        self.assertEqual(self.post(message, sides=3, fingerprint=again["fingerprint"]).status_code, 409)
        # A reviewed assembly keeps its pages when the run is undone; the other exams return to unidentified.
        first = next(assembly for assembly in applied.json["assemblies"] if assembly["copy_number"] == 1)
        self.scans.select_assembly(first["id"], [page["id"] for page in pages[:3]], False, "Reviewed against the paper")
        self.assertEqual(self.post(message, "/missing/undo").status_code, 404)
        undone = self.post(message, f"/{run['id']}/undo")
        self.assertEqual(undone.status_code, 200, undone.json)
        self.assertEqual([page["status"] for page in undone.json["pages"]], ["resolved", "manual", "manual"] + ["resolved", "unreadable", "unreadable"] * 2)
        record = undone.json["sequence"]["runs"][0]
        self.assertEqual((record["undo"]["cleared"], record["undo"]["packets"], [item["reason"] for item in record["undo"]["kept"]]), (4, 3, ["in a reviewed assembly"] * 2))
        self.assertEqual(undone.json["sequence"]["packets"], [])
        self.assertEqual(self.client.get(f"/api/documents/packets/{packets[1]['id']}/download").status_code, 404)
        self.assertEqual(self.post(message, f"/{run['id']}/undo").status_code, 409)
        with self.assertRaises(Exception):
            with connect(self.path) as db:
                db.execute("DELETE FROM scan_packet_pages")

    def test_blank_backs_count_as_sides_and_written_sides_are_numbered(self):
        duplex = ["L1", BLANK, P2, BLANK, P3, BLANK]
        message = self.scan((1, duplex), (2, duplex))
        item = self.scans.review_item(message)
        self.assertEqual((item["sequence"]["runs"][0]["automatic"], item["sequence"]["runs"][0]["sides"]), (True, 6))  # Labels six sides apart, the last exam six long.
        self.post(message, f"/{item['sequence']['runs'][0]['id']}/undo")
        plan = self.post(message, "/preview", sides=6).json
        self.assertEqual((plan["counts"]["ready"], plan["counts"]["identified"]), (2, 4))
        self.assertEqual([(item["source_page_index"], item["side"], item["page_number"]) for item in plan["exams"][1]["assign"]], [(9, 3, 2), (11, 5, 3)])
        pages = self.post(message, sides=6, fingerprint=plan["fingerprint"]).json["pages"]
        self.assertEqual([(page["status"], page["page_number"]) for page in pages[:6]],
                         [("resolved", 1), ("blank", None), ("manual", 2), ("blank", None), ("manual", 3), ("blank", None)])

    def test_anything_that_does_not_add_up_is_reported_and_left_alone(self):
        # Two loose pages first (a bubble sheet block), a short exam, an exam with an extra sheet, an exam whose
        # label was not read (its first page carries no barcode), and one exam labeled with its page 2 label.
        message = self.scan((0, [P1, P1]), (1, ["L1", P2]), (2, ["L1", P2, P3, P3]), (3, ["L1", P2, P3]), (0, [P1, P2, P3]), (4, ["L2", P2, P3]))
        # Uneven labels, loose pages and an unread label: nothing is grouped until the instructor has looked.
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"], [])
        plan = self.post(message, "/preview", sides=3).json
        problems = {exam["copy_number"]: exam["problems"] for exam in plan["exams"]}
        self.assertEqual(problems[1], ["Only 2 of 3 sides arrive before the next label."])
        self.assertEqual(problems[2], ["4 sides arrive before the next label, which is not a whole number of 3-side papers."])
        self.assertEqual(problems[3], [])
        self.assertIn("Its label is the page 2 label, not page 1", problems[4][0])
        self.assertEqual(problems[2], ["4 sides arrive before the next label, which is not a whole number of 3-side papers."])
        self.assertEqual([(run["first_page"], run["last_page"], run["where"].split(":")[0].split(";")[0]) for run in plan["runs"]],
                         [(1, 2, "before the first label"), (12, 14, "after copy 3")])
        self.assertIn("1 paper whose label was not read", plan["runs"][1]["where"])
        self.assertEqual((plan["counts"]["ready"], plan["counts"]["grouped"], plan["counts"]["identified"], plan["counts"]["review"], plan["counts"]["unlabeled"]), (1, 1, 2, 3, 5))
        pages = self.post(message, sides=3, fingerprint=plan["fingerprint"]).json["pages"]
        self.assertEqual([page["source_page_index"] for page in pages if page["status"] == "manual"], [10, 11])
        # Recording the unread label's printed alias on its first page makes that exam identifiable on the next preview.
        alias = next(entry["recovery_alias"] for entry in self.manifest if entry["copy_number"] == 5 and entry["page_number"] == 1)
        unread = next(page for page in pages if page["source_page_index"] == 12)
        self.scans.recover(unread["id"], alias, "Alias printed on the label")
        later = self.post(message, "/preview", sides=3).json
        recovered = next(exam for exam in later["exams"] if exam["first_page"] == 12)
        self.assertEqual((recovered["copy_number"], recovered["problems"], [item["page_number"] for item in recovered["assign"]]), (5, [], [2, 3]))
        self.assertEqual([run["where"] for run in later["runs"]], ["before the first label"])

    def test_copies_are_flagged_for_missing_pages_only_once_their_pages_were_grouped(self):
        # inbox-1: before grouping, the pages that would complete each copy are the Papers step's; Review does not flag the same
        # cause a second time (it used to turn into a wall of hand-assembly forms). After a grouping that holds one paper back,
        # that paper's missing pages are flagged again.
        message = self.scan((1, ["L1", P2, P3]), (2, ["L1", P2, P3]), (3, ["L1", P2, P3, P3]))
        item = self.scans.review_item(message)
        self.assertEqual(item["sequence"]["runs"], [])
        steps = item["overview"]["steps"]
        self.assertEqual([step for step in steps if step["tool"] == "assemblies" and step["kind"] == "check"], [])
        self.assertEqual([step["kind"] for step in steps if step["tool"] == "exams"], ["todo"])
        # Review is not flagged for the copies the grouping will complete (since 2026-10-18 a check and a to-do both read "Needs you").
        self.assertNotIn("no scan has shown", next(stage for stage in item["overview"]["stages"] if stage["key"] == "review")["detail"])
        plan = self.post(message, "/preview", sides=3).json
        self.assertEqual(self.post(message, sides=3, fingerprint=plan["fingerprint"]).status_code, 201)
        steps = self.scans.review_item(message)["overview"]["steps"]
        self.assertIn(("check", "1 copy has pages that no scan has shown with a label."),
                      [(step["kind"], step["text"]) for step in steps if step["tool"] == "assemblies"])

    def stamped_scan(self, *exams):
        """The instructor's practice: a one-page label sheet, one label stuck on the first page of a longer exam."""
        catalog = self.documents.catalog()
        stamps = self.documents.register_stamp_pages(catalog["assignments"][0]["id"], 1)
        batch = self.documents.create_batch(stamps["id"], catalog["sections"][0]["id"], len(exams), str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        labels = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        writer = PdfWriter()
        for copy, sides in enumerate(exams):
            writer.add_page(labels.pages[copy])
            for side in sides:
                writer.add_blank_page(612, 792) if side == BLANK else writer.add_page(self.plain.pages[int(side[1]) - 1])
        content = io.BytesIO()
        writer.write(content)
        self.scans.alias_ocr.enabled = True
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "labels.pdf", "content": content.getvalue()}], source="cli", sender="test",
                                idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        self.assertTrue(self.scans.process_one())
        return self.documents.get_batch(batch["id"]), job

    def review(self, job):
        """The instructor's thumbs-up on a scan in the Inbox."""
        current = self.inbox.get(job["id"])
        return self.inbox.transition(job["id"], "review", current["version"], {"action": "scan.receive", "course": "MATH 140", "section": 501, "metadata": {}})

    def test_a_one_page_label_identifies_the_exam_behind_it_and_the_inbox_review_assembles_it(self):
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2])
        message = job["message_id"]
        # Pages behind a peel-off label carry no barcode by design, so printed-label OCR is not queued for them.
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_alias_ocr").fetchone()[0], 0)
        # The labels are evenly spaced and every exam adds up, so each side takes its position as its page. Nothing was asked.
        first = self.scans.review_item(message)
        run = first["sequence"]["runs"][0]
        self.assertEqual((first["sequence"]["suggested_sides"], run["automatic"], run["packets"], run["pages"]), (4, True, 2, 6))
        self.assertEqual([(page["status"], page["page_stamp_id"] is None, page["packet"]["side"]) for page in first["pages"][:4]],
                         [("resolved", False, 1), ("manual", False, 2), ("manual", False, 3), ("manual", False, 4)])
        with connect(self.path) as db:
            issued = [tuple(row) for row in db.execute("""SELECT c.copy_number,p.page_number,p.symbology,p.state FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id
                                                          WHERE c.print_batch_id=? ORDER BY c.copy_number,p.page_number""", (batch["id"],))]
            self.assertEqual(issued, [(copy, page, "DataMatrix" if page == 1 else "sequence", "issued") for copy in (1, 2) for page in (1, 2, 3, 4)])
            # Identities taken from a scan were never printed: the printed manifest and its layout check leave them out.
            self.assertEqual([entry["page_number"] for entry in self.documents.manifest(db, batch["id"])], [1, 1])
            self.documents._batch_layout(db, dict(db.execute("SELECT * FROM print_batches WHERE id=?", (batch["id"],)).fetchone()))
        self.assertIsNotNone(self.documents.download(batch["id"]))
        # Nothing is a reviewed paper until the instructor has reviewed the scan.
        self.assertTrue(first["instructor_review_required"])
        self.assertEqual(self.scans.sequence.assemble_reviewed(message), {"identified": 0, "assembled": 0, "left": []})
        self.assertEqual([len(assembly["revisions"]) for assembly in first["assemblies"]], [0, 0])
        # Undone, the pages lose their identity; the reserved identities are never recycled and are reused by the next run.
        self.post(message, f"/{run['id']}/undo")
        self.assertEqual([page["status"] for page in self.scans.review_item(message)["pages"][:4]], ["resolved", "unreadable", "unreadable", "unreadable"])
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual((plan["counts"]["ready"], plan["counts"]["grouped"], plan["counts"]["identified"], plan["counts"]["review"]), (2, 2, 6, 0))
        applied = self.post(message, sides=4, fingerprint=plan["fingerprint"])
        self.assertEqual(applied.status_code, 201, applied.json)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM page_stamps WHERE symbology='sequence'").fetchone()[0], 6)
        packet = applied.json["sequence"]["packets"][0]
        self.assertEqual((packet["sides"], packet["version_label"]), (4, "Stamps only · 1 page"))
        self.assertEqual(len(PdfReader(io.BytesIO(self.client.get(f"/api/documents/packets/{packet['id']}/download").data)).pages), 4)
        again = self.post(message, "/preview", sides=4).json
        self.assertEqual((again["counts"]["ready"], again["counts"]["complete"]), (0, 2))
        # One copy was already assembled by hand from its label alone; the instructor's own decision is never replaced.
        second = next(assembly for assembly in applied.json["assemblies"] if assembly["copy_number"] == 2)
        by_hand = self.scans.select_assembly(second["id"], [second["pages"][0]["observations"][0]["id"]], False, "Checked on paper")
        # The Inbox review is the approval: the other exam becomes that copy's reviewed paper, every page in order.
        self.review(job)
        reviewed = self.scans.review_item(message)
        self.assertFalse(reviewed["instructor_review_required"])
        one, two = sorted(reviewed["assemblies"], key=lambda assembly: assembly["copy_number"])
        self.assertEqual(([len(one["revisions"]), len(two["revisions"])], two["revisions"][0]["id"]), ([1, 1], by_hand["id"]))
        revision = one["revisions"][0]
        self.assertEqual(([page["page_number"] for page in json.loads(revision["selection_json"])], revision["partial"], revision["actor"]), ([1, 2, 3, 4], 0, "inbox-review"))
        self.assertEqual(len(PdfReader(io.BytesIO(self.scans.download_assembly(revision["id"]))).pages), 4)
        with connect(self.path) as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT packet_id,sides,pages,actor FROM scan_sequence_assemblies")], [(packet["id"], 4, 4, "inbox-review")])
            event = json.loads(db.execute("SELECT detail FROM inbox_events WHERE kind='first_page_papers_assembled'").fetchone()[0])
        self.assertEqual((event["assembled"], event["left_for_assembly_review"], event["identified_pages"]), (1, [], 0))
        # Reviewing again changes nothing, and a reviewed page keeps its identity even if its run is undone.
        self.assertEqual(self.scans.sequence.assemble_reviewed(message), {"identified": 0, "assembled": 0, "left": []})
        undone = self.post(message, f"/{applied.json['sequence']['runs'][0]['id']}/undo").json
        self.assertEqual([page["status"] for page in undone["pages"][:4]], ["resolved", "manual", "manual", "manual"])

    def test_a_one_page_paper_with_a_blank_back_is_assembled_on_review(self):
        # 2026-10-17 ("why does it say so many responses can't be graded"): a paper of one written side and its blank back, every
        # side in its packet, is a whole paper; the review assembles it like any other.
        batch, job = self.stamped_scan([BLANK], [BLANK])
        message = job["message_id"]
        preview = self.scans.sequence.preview(message, 2)
        self.scans.sequence.apply(message, 2, preview["fingerprint"])
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"][0]["packets"], 2)
        self.review(job)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_sequence_assemblies WHERE message_id=?", (message,)).fetchone()[0], 2)

    def test_a_one_sided_paper_is_assembled_on_review(self):
        # 2026-10-18 (Cort's "Scan needs review" with nothing to review): a scan grouped one side per paper, each side written,
        # holds every side of each paper; the review assembles them like any other.
        batch, job = self.stamped_scan([], [])
        message = job["message_id"]
        preview = self.scans.sequence.preview(message, 1)
        self.scans.sequence.apply(message, 1, preview["fingerprint"])
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"][0]["packets"], 2)
        self.review(job)
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_sequence_assemblies WHERE message_id=?", (message,)).fetchone()[0], 2)

    def test_a_failed_assembly_never_undoes_or_hides_the_instructors_review(self):
        from unittest.mock import patch
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2])
        with patch.object(self.scans.sequence, "assemble_reviewed", side_effect=RuntimeError("synthetic failure")), self.assertLogs("teach_inbox.service", "ERROR"):
            reviewed = self.review(job)
        self.assertFalse(reviewed["instructor_review_required"])
        with connect(self.path) as db:
            kinds = [row[0] for row in db.execute("SELECT kind FROM inbox_events WHERE job_id=? ORDER BY id", (job["id"],))]
        self.assertIn("reviewed", kinds)
        self.assertEqual(kinds[-1], "first_page_papers_not_assembled")
        # Nothing was lost: the same step can simply run again.
        self.assertEqual(self.scans.sequence.assemble_reviewed(job["message_id"])["assembled"], 2)

    def test_nothing_is_inferred_behind_a_label_when_the_cadence_does_not_add_up(self):
        # The third exam is a side short. Its label still says whose paper it is; which page each side is cannot be known.
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2], [P2, P3])
        message = job["message_id"]
        item = self.scans.review_item(message)
        self.assertEqual(item["sequence"]["runs"], [])
        self.assertEqual([page["status"] for page in item["pages"]], (["resolved"] + ["unreadable"] * 3) * 2 + ["resolved", "unreadable", "unreadable"])
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual((plan["counts"]["ready"], plan["counts"]["identified"], plan["counts"]["review"]), (2, 6, 1))
        self.assertEqual(plan["exams"][2]["problems"], ["Only 3 of 4 sides arrive before the next label."])
        self.assertEqual(plan["exams"][2]["assign"], [])
        self.assertEqual(self.post(message, sides=4, fingerprint=plan["fingerprint"]).status_code, 201)
        self.review(job)
        reviewed = self.scans.review_item(message)
        self.assertEqual([len(assembly["revisions"]) for assembly in sorted(reviewed["assemblies"], key=lambda assembly: assembly["copy_number"])], [1, 1, 0])
        self.assertEqual([page["status"] for page in reviewed["pages"][8:]], ["resolved", "unreadable", "unreadable"])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM page_stamps WHERE symbology='sequence'").fetchone()[0], 6)

    def test_a_paper_left_out_of_the_grouping_is_named_with_its_sides_on_its_own_page(self):
        # The second paper has an extra side (requirement, 2026-09-24).
        batch, job = self.stamped_scan([P2, P3, P2], [P2, P3, P2, P3], [P2, P3, P2])
        message = job["message_id"]
        plan = self.post(message, "/preview", sides=4).json
        self.assertEqual(self.post(message, sides=4, fingerprint=plan["fingerprint"]).status_code, 201)
        item = self.scans.review_item(message)
        steps = [(step["kind"], step["text"], step["action"]) for step in item["overview"]["steps"] if step["tool"] == "exams" and step["kind"] == "check"]
        self.assertEqual(steps, [("check", "1 paper has more sides than the 4 per paper, so it was not grouped: pages 5–9 (5 sides). "
                                           "It may hold an extra sheet: look before grading.", "papers")])
        self.assertEqual((item["recognition"]["labels_read"], item["recognition"]["first_pages_read"]), (3, 0))
        # Papers to check lists that paper alone, its first page and every side, and names no one it does not know.
        check = self.client.get(f"/api/documents/messages/{message}/papers-to-check")
        self.assertEqual(check.status_code, 200, check.json)
        self.assertEqual((check.json["sides"], check.json["grouped"], check.json["files"], len(check.json["papers"])), (4, 2, 1, 1))
        paper = check.json["papers"][0]
        self.assertEqual((paper["first_page"], paper["last_page"], paper["sides"], paper["copy_number"], paper["student"]), (5, 9, 5, 2, None))
        self.assertEqual([(page["page"], page["first"]) for page in paper["pages"]], [(5, True), (6, False), (7, False), (8, False), (9, False)])
        # Each side is shown as a picture of the retained page, scaled down; a page of no scan here is refused. Nothing was written.
        image = self.client.get(f"/api/documents/messages/{message}/pages/{paper['pages'][1]['scan_page_id']}/image.png?width=300")
        self.assertEqual((image.status_code, image.mimetype), (200, "image/png"))
        self.assertLessEqual(Image.open(io.BytesIO(image.data)).width, 300)
        self.assertEqual(self.client.get(f"/api/documents/messages/{message}/pages/nope/image.png").status_code, 404)
        self.assertEqual(self.scans.review_item(message)["overview"], item["overview"])

    def sheets_scan(self, order, copies):
        """A two-sided scan of one-sheet papers: `order` lists (copy, face) in scan order, face 1 its label, face 2 its back, and
        (None, None) a stray blank page."""
        catalog = self.documents.catalog()
        stamps = self.documents.register_stamp_pages(catalog["assignments"][0]["id"], 1)
        batch = self.documents.create_batch(stamps["id"], catalog["sections"][0]["id"], copies, str(uuid.uuid4()))
        self.assertTrue(self.documents.build_one())
        labels = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        writer = PdfWriter()
        for copy, face in order:
            if copy is None:
                writer.add_blank_page(612, 792)
            else:
                writer.add_page(labels.pages[copy]) if face == 1 else writer.add_page(self.plain.pages[1 + copy % 2])
        content = io.BytesIO()
        writer.write(content)
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "sheets.pdf", "content": content.getvalue()}], source="cli", sender="test",
                                idempotency_key=str(uuid.uuid4()), instructor_review_required=True, large_scans=True)
        while self.scans.process_one():
            pass
        return job

    @staticmethod
    def papers(plan, order):
        """Each exam of a plan as (copy, [(copy, face) of its sides in reading order], ready)."""
        rows = []
        for exam in plan["exams"]:
            indexes = [member["source_page_index"] for member in exam["members"]] or range(exam["first_page"], exam["last_page"] + 1)
            rows.append((exam["copy_number"] - 1, [order[index - 1] for index in indexes], exam["ready"]))
        return rows

    def grouped(self, message, order):
        """Each grouped paper of the scan as (copy, [(copy, face) of its sides in order]), from the retained packets."""
        with connect(self.path) as db:
            rows = db.execute("""SELECT c.copy_number,sp.source_page_index FROM scan_packets k JOIN scan_sequence_runs r ON r.id=k.run_id
                                 JOIN printed_copies c ON c.id=k.printed_copy_id JOIN scan_packet_pages q ON q.packet_id=k.id
                                 JOIN scan_pages sp ON sp.id=q.scan_page_id WHERE k.message_id=? AND r.undone_at IS NULL
                                 ORDER BY c.copy_number,q.side""", (message,)).fetchall()
        papers = {}
        for copy, index in rows:
            papers.setdefault(copy - 1, []).append(order[index - 1])
        return sorted(papers.items())

    def test_sheets_scanned_back_side_first_are_turned_by_themselves_when_every_chunk_passes(self):
        # Every two pages from the first
        # hold one first page, so the faces of each sheet belong to one paper however they were fed: one sheet back first, two in a
        # row, or the whole stack. Grouped on arrival, each paper is its own two faces, first page first, never a neighbour's page.
        for name, order, copies in (
                ("one sheet", [(0, 1), (0, 2), (1, 2), (1, 1)] + [(copy, face) for copy in range(2, 8) for face in (1, 2)], 8),
                ("two in a row", [(0, 1), (0, 2), (1, 2), (1, 1), (2, 2), (2, 1)] + [(copy, face) for copy in range(3, 9) for face in (1, 2)], 9),
                ("the whole stack", [(copy, face) for copy in range(4) for face in (2, 1)], 4)):
            with self.subTest(name):
                job = self.sheets_scan(order, copies)
                message = job["message_id"]
                run, = self.scans.review_item(message)["sequence"]["runs"]
                # The fronts of the sheets fed back first: a page 2 on a sheet's front, its own page 1 on the back.
                turned = [front for front in range(1, len(order), 2) if order[front - 1][1] == 2 and order[front] == (order[front - 1][0], 1)]
                self.assertEqual((run["automatic"], run["sides"], run["packets"], run["turned"]), (True, 2, copies, turned))
                self.assertEqual(self.grouped(message, order), [(copy, [(copy, 1), (copy, 2)]) for copy in range(copies)])
                # Nothing is left to decide, and the scan's Papers stage says so.
                again = self.scans.sequence.preview(message, 2)
                self.assertEqual((again["counts"]["ready"], again["counts"]["review"], again["counts"]["complete"]), (0, 0, copies))
                self.assertEqual({stage["key"]: stage["state"] for stage in self.scans.review_item(message)["overview"]["stages"]}["papers"], "done")
        # A turned sheet's front takes its position: side 2 of that student's paper, page 2 of the label it was registered to.
        order = [(0, 1), (0, 2), (1, 2), (1, 1)] + [(copy, face) for copy in range(2, 8) for face in (1, 2)]
        job = self.sheets_scan(order, 8)
        pages = {page["source_page_index"]: page for page in self.scans.review_item(job["message_id"])["pages"]}
        self.assertEqual([(pages[index]["packet"]["copy_number"], pages[index]["packet"]["side"]) for index in (3, 4)], [(2, 2), (2, 1)])
        self.assertEqual(pages[3]["page_stamp_id"] is not None and pages[3]["sequence"]["side"], 2)

    def test_a_first_page_found_but_not_yet_identified_counts_in_its_chunk(self):
        # On paper printed without labels, a first page whose student is still unsure has no page 1 identity yet. Found on the paper, it
        # still says which faces make a sheet, so its chunk passes; a page the instructor said is not a first page does not count.
        page = lambda index, status="unreadable", copy=None, number=None, found=False: {"source_page_index": index, "status": status, "copy_id": copy,
                                                                                          "page_number": number, "found_first": found}
        chunked = lambda pages: SequenceTests.chunked(pages)
        pages = [page(1, "manual", "a", 1), page(2), page(3, found=True), page(4), page(5), page(6, "manual", "c", 1)]
        self.assertTrue(chunked(pages))  # the third sheet was fed back side first; the second's student is not known yet
        self.assertFalse(chunked([page(1, "manual", "a", 1), page(2), page(3), page(4), page(5), page(6, "manual", "c", 1)]))
        self.assertFalse(chunked([page(1, "manual", "a", 1), page(2, found=True), page(3), page(4)]))  # two in one sheet

    @staticmethod
    def chunked(pages):
        from document_identity.sequence import SequenceService
        return SequenceService._chunked(pages, 2)

    def test_a_sheet_scanned_back_side_first_waits_for_the_instructor_when_a_chunk_does_not_pass(self):
        # A stray blank sheet at the end: its chunk holds no first page, so nothing turns by itself. The instructor's own preview
        # still reads the scan as sheets (one first page in eight on a back) and puts copy 2's sheet in order.
        order = [(0, 1), (0, 2), (1, 2), (1, 1)] + [(copy, face) for copy in range(2, 8) for face in (1, 2)] + [(None, None), (None, None)]
        job = self.sheets_scan(order, 8)
        message = job["message_id"]
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"], [])
        automatic = self.scans.sequence.preview(message, 2)
        self.assertEqual(self.papers(automatic, order)[:3], [(0, [(0, 1), (0, 2), (1, 2)], False), (1, [(1, 1)], False), (2, [(2, 1), (2, 2)], True)])
        plan = self.post(message, "/preview", sides=2).json
        self.assertEqual(plan["counts"]["turned"], 1)
        self.assertEqual(self.papers(plan, order)[:2], [(0, [(0, 1), (0, 2)], True), (1, [(1, 1), (1, 2)], True)])
        turned = plan["exams"][1]
        self.assertEqual((turned["turned"], turned["first_page"], turned["last_page"], [a["page_number"] for a in turned["assign"]]), (3, 3, 4, [2]))
        applied = self.post(message, sides=2, fingerprint=plan["fingerprint"])
        self.assertEqual(applied.status_code, 201, applied.json)
        self.assertEqual(applied.json["sequence"]["runs"][0]["turned"], [3])
        # Once grouped, a plan that turns nothing by itself recognizes that sheet: complete, not "grouped differently".
        again = self.scans.sequence.preview(message, 2)
        self.assertEqual((again["counts"]["ready"], again["counts"]["review"], again["counts"]["complete"]), (0, 0, 8))

    def test_two_sheets_scanned_back_side_first_never_lend_a_page_when_the_sheets_cannot_be_trusted(self):
        # Copies 2 and 3 fed back side first, and a stray page at the end: the page count is odd, so no side can be paired with its
        # sheet. At two sides per paper copy 2 would "add up" with copy 3's page 2; that paper is held, never grouped by count alone.
        order = [(0, 1), (0, 2), (1, 2), (1, 1), (2, 2), (2, 1)] + [(copy, face) for copy in range(3, 9) for face in (1, 2)] + [(None, None)]
        job = self.sheets_scan(order, 9)
        message = job["message_id"]
        self.assertEqual(self.scans.review_item(message)["sequence"]["runs"], [])
        for plan in (self.scans.sequence.preview(message, 2), self.post(message, "/preview", sides=2).json):
            self.assertEqual(plan["counts"]["turned"], 0)
            self.assertEqual(self.papers(plan, order)[1], (1, [(1, 1), (2, 2)], False))
            self.assertIn("It lies between a paper with more than 2 sides and one with fewer", " ".join(plan["exams"][1]["problems"]))
        # Copy 3's first page is on a back after copy 2's paper, which adds up: that is held too, though it is short anyway.
        self.assertIn("Its first page is on the back of its sheet, after page 5", " ".join(plan["exams"][2]["problems"]))
        # The papers past the disorder are their students' own and group as usual; Papers to check lists the held ones and why.
        self.assertEqual([exam["ready"] for exam in plan["exams"][3:8]], [True] * 5)
        check = self.client.get(f"/api/documents/messages/{message}/papers-to-check").json
        self.assertEqual([(item["first_page"], item["ready"]) for item in check["papers"]][:3], [(1, False), (4, False), (6, False)])

    def test_a_stack_fed_back_side_first_groups_nothing_by_count_when_a_chunk_does_not_pass(self):
        # Every sheet back side first plus a stray page: each paper would add up with the next student's page 2. Nothing may be
        # grouped by the count, and without every chunk passing nothing is turned either: all of it waits for the instructor.
        for stray in ([(None, None)], [(None, None), (None, None)]):  # a stray page, or a stray blank sheet
            with self.subTest(stray=len(stray)):
                order = [(copy, face) for copy in range(4) for face in (2, 1)] + stray
                job = self.sheets_scan(order, 4)
                message = job["message_id"]
                self.assertEqual(self.scans.review_item(message)["sequence"]["runs"], [])
                for plan in (self.scans.sequence.preview(message, 2), self.post(message, "/preview", sides=2).json):
                    self.assertEqual((plan["counts"]["ready"], plan["counts"]["turned"]), (0, 0))
                    self.assertTrue(all("Its first page is on the back of its sheet" in " ".join(exam["problems"]) for exam in plan["exams"]))

    def test_a_blank_back_keeps_its_place_and_scan_identities_only_extend_a_stamps_only_label(self):
        batch, job = self.stamped_scan([P2, BLANK, P3], [P2, BLANK, P3])
        item = self.scans.review_item(job["message_id"])
        self.assertEqual([(page["status"], page["packet"]["side"]) for page in item["pages"][:4]], [("resolved", 1), ("manual", 2), ("blank", 3), ("manual", 4)])
        with connect(self.path) as db:
            self.assertEqual([row[0] for row in db.execute("""SELECT p.page_number FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id
                WHERE c.print_batch_id=? AND c.copy_number=1 ORDER BY p.page_number""", (batch["id"],))], [1, 2, 4])
            # The database itself refuses such an identity anywhere else: on ordinary printed paper, as page 1, or unissued.
            ordinary = self.batch["copies"][0]["id"]
            for copy_id, page, state in ((ordinary, 9, "issued"), (batch["copies"][0]["id"], 1, "issued"), (batch["copies"][0]["id"], 9, "allocated")):
                with self.assertRaisesRegex(Exception, "Sequence identities extend an issued first-page label|UNIQUE"):
                    self.documents._allocate_stamp(db, copy_id, page, "2026-09-19T00:00:00+00:00", symbology="sequence", state=state)
            with self.assertRaisesRegex(Exception, "Invalid page allocation"):
                self.documents._allocate_stamp(db, ordinary, 9, "2026-09-19T00:00:00+00:00")

    def test_sections_that_sat_one_exam_together_are_one_recognized_scan(self):
        catalog = self.documents.catalog()
        self.assertEqual([section["number"] for section in catalog["sections"][:2]], [501, 502])

        def printed(version_id, section, copies=2):
            batch = self.documents.create_batch(version_id, section["id"], copies, str(uuid.uuid4()))
            self.assertTrue(self.documents.build_one())
            return PdfReader(io.BytesIO(self.documents.download(batch["id"])))

        def exams(*sources):
            writer = PdfWriter()
            for source, copy in sources:
                writer.add_page(source.pages[copy * 3])
                writer.add_page(self.plain.pages[1])
                writer.add_page(self.plain.pages[2])
            return self.submit(writer)

        other_section = printed(self.batch["assignment_version_id"], catalog["sections"][1])
        together = self.scans.review_item(exams((self.printed, 0), (other_section, 0), (self.printed, 1), (other_section, 1)))
        recognition = together["recognition"]
        self.assertEqual((recognition["verdict"], recognition["sections"], [(paper["section_number"], paper["copies"]) for paper in recognition["papers"]]),
                         ("recognized", [501, 502], [(501, 2), (502, 2)]))
        self.assertEqual([(run["automatic"], run["packets"]) for run in together["sequence"]["runs"]], [(True, 4)])
        # One summary for the Inbox list and the scan's page. The paper here expects a label on every page, which is the first thing to settle.
        overview = together["overview"]
        self.assertEqual((overview["headline"], overview["facts"], overview["state"]), ("Exam 1 · MATH 140 · Sections 501 and 502", ["12 pages", "4 papers"], "todo"))
        self.assertEqual([(step["kind"], step["tool"], step["action"]) for step in overview["steps"] if step["action"] != "grading_review"], [("todo", None, "label_mode"), ("done", "exams", None), ("info", None, None)])
        # The label question concerns that print job's papers only (2026-09-28): it no longer holds the scan's review back.
        self.assertEqual([step["kind"] for step in overview["steps"] if step["action"] == "grading_review"], ["todo"])
        self.assertIn("4 papers grouped automatically, 3 sides each.", [step["text"] for step in overview["steps"]])
        # Two assignments in one scan are still something to check.
        quiz = self.documents.create_assignment(catalog["definitions"][0]["id"], "quiz-1", "Quiz 1")
        version = self.documents.register_version(quiz["id"], "A", (FIXTURES / "letter.pdf").read_bytes(), {"main.tex": (FIXTURES / "letter.tex").read_bytes()}, {"fixture": "letter"})
        mixed = self.scans.review_item(exams((self.printed, 2), (printed(version["id"], catalog["sections"][0]), 0)))["recognition"]
        self.assertEqual((mixed["verdict"], mixed["sections"], sorted(paper["title"] for paper in mixed["papers"])), ("mixed", [501], ["Exam 1", "Quiz 1"]))

    def test_a_scan_deleted_from_the_inbox_stops_counting_everywhere_and_can_be_restored(self):
        # The instructor's case: an aborted first attempt, then the same stack scanned again. The same label arrives twice with
        # different page images, so both scans are flagged "changed content", and copy 1 counts as seen because of the fragment too.
        fragment = self.scan((1, ["L1", P2]))
        writer = PdfWriter()
        for copy in (1, 2, 3):
            first = writer.add_page(self.printed.pages[(copy - 1) * 3])
            if copy == 1:
                first.merge_page(self.plain.pages[1])  # The same paper scanned again: the same label on another page image.
            writer.add_page(self.plain.pages[1])
            writer.add_page(self.plain.pages[2])
        full = self.submit(writer)
        states = lambda message: [page["review_state"] for page in self.scans.review_item(message)["pages"] if page["status"] == "resolved"]
        self.assertEqual((states(fragment), states(full)), (["conflict"], ["conflict", "candidate", "candidate"]))
        # Each conflicting page says where else its label was read, so the instructor can compare the two (2026-10-24).
        also = lambda message: {page["source_page_index"]: [(o["message_id"], o["source_page_index"]) for o in page.get("also_scanned", [])]
                                for page in self.scans.review_item(message)["pages"] if page.get("also_scanned")}
        self.assertEqual((also(fragment), also(full)), ({1: [(full, 1)]}, {1: [(fragment, 1)]}))
        job = self.inbox.get_by_message(fragment)
        operate = lambda operation, **body: self.client.post(f"/api/inbox/jobs/{job['id']}/{operation}", json=body, headers=HEADERS)
        listing = lambda status="": self.client.get(f"/api/inbox/jobs?status={status}").json
        # Deleting needs the instructor's explicit confirmation, checked by the server, and the job as they saw it.
        self.assertEqual([operate("delete", version=job["version"]).status_code, operate("delete", version=job["version"], confirmation="yes").status_code,
                          operate("delete", version=job["version"] + 1, confirmation="delete").status_code, operate("restore").status_code], [400, 400, 409, 409])
        self.assertEqual(self.inbox.remove.__doc__ is not None and listing()["total"], 2)
        with self.assertRaises(Exception):
            self.inbox.remove(job["id"], job["version"], "delete", actor="api-key")  # Scripts cannot delete.
        removed = operate("delete", version=job["version"], confirmation="delete", reason="Aborted scan, scanned again")
        self.assertEqual((removed.status_code, removed.json["deleted"]["reason"], removed.json["status"]), (200, "Aborted scan, scanned again", "needs_review"))
        # Gone from every list, kept under Deleted; the full scan no longer conflicts with anything.
        after = listing()
        self.assertEqual((after["total"], [item["message_id"] for item in after["jobs"]], after["counts"].get("deleted")), (1, [full], 1))
        self.assertEqual([item["message_id"] for item in listing("deleted")["jobs"]], [fragment])
        self.assertEqual((states(full), also(full)), (["candidate", "candidate", "candidate"], {}))
        self.assertNotIn("check", [step["kind"] for step in self.scans.review_item(full)["overview"]["steps"]])
        # Nothing was erased: the record, its files and its history are all still there, with the deletion in the audit trail.
        kept = self.inbox.get(job["id"])
        self.assertEqual((len(kept["artifacts"]) > 2, [event["kind"] for event in kept["events"]][-1]), (True, "deleted_from_inbox"))
        self.assertEqual(operate("delete", version=kept["version"], confirmation="delete").status_code, 409)
        # A scan that arrives afterwards is compared with what is in the Inbox, not with what was deleted.
        again = self.scan((4, ["L1", P2, P3]), (1, ["L1", P3, P2, P2]))
        self.assertNotIn("duplicate", states(again))
        # Restored, it counts again, and the conflict it causes is back.
        self.assertEqual(operate("restore").status_code, 200)
        self.assertEqual((listing()["total"], listing()["counts"].get("deleted"), states(fragment)[0]), (3, None, "conflict"))
        self.assertIn("conflict", states(full))
        # A scan whose pages are part of a reviewed assembly is a recorded submission and stays.
        item = self.scans.review_item(full)
        second = next(assembly for assembly in item["assemblies"] if assembly["copy_number"] == 2)
        self.documents.set_label_mode(self.batch["id"], "first_page")
        self.scans.select_assembly(second["id"], [item["pages"][3]["id"]], False, "Reviewed against the paper")
        full_job = self.inbox.get_by_message(full)
        refused = self.client.post(f"/api/inbox/jobs/{full_job['id']}/delete", json={"version": full_job["version"], "confirmation": "delete"}, headers=HEADERS)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("reviewed assembly", refused.json["error"])

    def decide(self, message_id, decision, batch_ids=None):
        return self.client.post(f"/api/documents/messages/{message_id}/label-mode", headers=HEADERS,
                                json={"batch_ids": [self.batch["id"]] if batch_ids is None else batch_ids, "decision": decision})

    def identified_twice(self):
        """The same three sides scanned twice and each grouped by sequence: (first message, its run, second message, second job)."""
        def grouped():
            writer = PdfWriter()
            writer.add_page(self.printed.pages[0])
            writer.add_page(self.plain.pages[1])
            writer.add_page(self.plain.pages[2])
            message = self.submit(writer)
            for run in self.scans.review_item(message)["sequence"]["runs"]:
                self.post(message, f"/{run['id']}/undo")  # automatic grouping is undone so the instructor's own applies
            plan = self.post(message, "/preview", sides=3).json
            applied = self.post(message, sides=3, fingerprint=plan["fingerprint"])
            self.assertEqual(applied.status_code, 201, applied.json)
            return message, applied.json["sequence"]["runs"][0]
        first, run = grouped()
        second, _ = grouped()
        self.assertIn("duplicate", [page["review_state"] for page in self.scans.review_item(second)["pages"]])
        return first, run, second, self.inbox.get_by_message(second)

    def test_undo_keeps_pages_a_later_scan_in_the_inbox_repeats(self):
        first, run, second, _ = self.identified_twice()
        undone = self.post(first, f"/{run['id']}/undo")
        self.assertEqual(undone.status_code, 200, undone.json)
        record = undone.json["sequence"]["runs"][0]["undo"]
        self.assertEqual((record["cleared"], {item["reason"] for item in record["kept"]}), (0, {"a later scan repeats it"}))

    def test_undo_ignores_a_repeat_in_a_scan_deleted_from_the_inbox(self):
        # INBOX: material deleted in the Inbox never counts. Its pages still say they repeat the first scan's, but the repeat is gone.
        first, run, second, job = self.identified_twice()
        self.inbox.remove(job["id"], job["version"], "delete", "Scanned twice")
        undone = self.post(first, f"/{run['id']}/undo")
        self.assertEqual(undone.status_code, 200, undone.json)
        record = undone.json["sequence"]["runs"][0]["undo"]
        self.assertEqual((record["cleared"], record["kept"]), (2, []))

    def test_paper_issued_a_label_per_page_but_scanned_with_one_per_exam_is_offered_for_conversion(self):
        message = self.scan((1, ["L1", P2, P3]), (2, ["L1", P2, P3]), (3, ["L1", P2, P3]))
        item = self.scans.review_item(message)
        job, = item["label_modes"]
        self.assertEqual((job["batch_id"], job["label_mode"], job["title"], job["version_label"], job["section_number"], job["page_count"], job["labels"], job["sides"]),
                         (self.batch["id"], "every_page", "Exam 1", "A", 501, 3, 3, 3))
        self.assertEqual((job["offer"], job["can_convert"], job["kept"], job["other_labels"]), (True, True, False, False))
        # Until the instructor answers, the job still expects a label on every page: that is the conflict the answer resolves.
        self.assertEqual([assembly["missing"] for assembly in item["assemblies"]], [[2, 3]] * 3)
        for bad in ({"decision": "first"}, {"batch_ids": []}, {"batch_ids": "x"}, {"batch_ids": [self.batch["id"]] * 2}, {"batch_ids": [7]}):
            response = self.client.post(f"/api/documents/messages/{message}/label-mode", json={"batch_ids": [self.batch["id"]], "decision": "first_page", **bad}, headers=HEADERS)
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(self.decide(message, "first_page", ["missing"]).status_code, 404)
        self.assertEqual(self.decide("missing", "first_page").status_code, 404)
        # Declining is remembered for this scan, so opening it again does not ask again. The instructor can still choose it by hand.
        kept = self.decide(message, "keep")
        self.assertEqual(kept.status_code, 200, kept.json)
        self.assertEqual([(job["label_mode"], job["offer"], job["kept"], job["can_convert"]) for job in kept.json["label_modes"]], [("every_page", False, True, True)])
        self.assertEqual(self.decide(message, "keep").status_code, 409)
        converted = self.decide(message, "first_page")
        self.assertEqual(converted.status_code, 200, converted.json)
        self.assertEqual([(job["label_mode"], job["offer"], job["can_convert"]) for job in converted.json["label_modes"]], [("first_page", False, False)])
        # The conflicts resolve: nothing is expected beyond page 1, each exam stays grouped, and the instructor's answer
        # gives the other sides their position as their page, on the identities this paper was already issued.
        self.assertEqual([(assembly["missing"], [page["page_number"] for page in assembly["pages"]]) for assembly in converted.json["assemblies"]], [([], [1, 2, 3])] * 3)
        self.assertEqual([page["status"] for page in converted.json["pages"]], ["resolved", "manual", "manual"] * 3)
        self.assertEqual(len(converted.json["sequence"]["packets"]), 3)
        identified, grouped = converted.json["sequence"]["runs"]
        self.assertEqual((identified["pages"], identified["packets"], identified["automatic"], grouped["packets"], grouped["automatic"]), (6, 0, False, 3, True))
        self.assertTrue(converted.json["instructor_review_required"])
        batch = self.documents.get_batch(self.batch["id"])
        self.assertEqual((batch["label_mode"], batch["status"], [event["kind"] for event in batch["events"]][-1]), ("first_page", "ready", "label_mode"))
        self.assertEqual(json.loads(batch["events"][-1]["detail_json"]), {"from": "every_page", "to": "first_page", "message_id": message, "labels": 3, "sides": 3})
        with connect(self.path) as db:
            decisions = [json.loads(row[0]) for row in db.execute("SELECT detail FROM inbox_events WHERE kind='label_mode_decision' ORDER BY id")]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM page_stamps WHERE state='issued'").fetchone()[0], 15)
        self.assertEqual([(entry["decision"], entry["from"], entry["instructor_review_required"]) for entry in decisions], [("keep", "every_page", True), ("first_page", "every_page", True)])
        # A copy's first page alone is still a complete assembly by hand. Undoing the identification clears the other sides again.
        first = next(assembly for assembly in converted.json["assemblies"] if assembly["copy_number"] == 1)
        revision = self.scans.select_assembly(first["id"], [converted.json["pages"][0]["id"]], False, "Reviewed against the paper")
        self.assertFalse(revision["partial"])
        # Switching back is as deliberate and as audited, and this scan does not ask again. Taking the answer back
        # also takes back the identities it alone gave; the exams stay grouped.
        back = self.decide(message, "every_page")
        self.assertEqual(back.status_code, 200, back.json)
        self.assertEqual([page["status"] for page in back.json["pages"]], ["resolved", "unreadable", "unreadable"] * 3)
        self.assertEqual(([run["undone_at"] is not None for run in back.json["sequence"]["runs"]], len(back.json["sequence"]["packets"])), ([True, False], 3))
        self.assertEqual([(job["label_mode"], job["offer"], job["kept"]) for job in back.json["label_modes"]], [("every_page", False, True)])
        self.assertEqual([assembly["missing"] for assembly in back.json["assemblies"]], [[2, 3]] * 3)
        self.assertEqual(self.post(message, "/preview", sides=3).json["counts"]["identified"], 6)
        with self.assertRaises(ValueError):
            self.documents.set_label_mode(self.batch["id"], "some_pages")

    def test_one_label_per_exam_is_only_offered_when_the_scans_leave_no_doubt(self):
        # Uneven spacing: not offered, though the instructor may still choose it, since no label of another page was ever read.
        uneven = self.scan((1, ["L1", P2, P3]), (2, ["L1", P2]), (3, ["L1", P2, P3]))
        self.assertEqual([(job["offer"], job["can_convert"], job["sides"], job["labels"]) for job in self.scans.review_item(uneven)["label_modes"]], [(False, True, None, 3)])
        # One exam has no label-to-label distance at all.
        single = self.scan((4, ["L1", P2, P3]))
        self.assertEqual([(job["offer"], job["can_convert"]) for job in self.scans.review_item(single)["label_modes"]], [(False, True)])
        # Once a label of another page of this job is read on any scan, its paper evidently carries them: no offer, no conversion.
        stamped = self.scan((5, ["L1", "L2", "L3"]))
        for message in (uneven, single, stamped):
            self.assertEqual([(job["offer"], job["can_convert"], job["other_labels"]) for job in self.scans.review_item(message)["label_modes"]], [(False, False, True)], message)
        refused = self.decide(uneven, "first_page")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("labeled beyond the first page", refused.json["error"])
        self.assertEqual(self.documents.get_batch(self.batch["id"])["label_mode"], "every_page")
        # One-page stamps-only labels expect nothing else to begin with, so there is never anything to ask.
        self.assertEqual(self.scans.review_item(stamped)["label_modes"][0]["page_count"], 3)

    def test_section_sized_scans_enter_only_through_private_scan_intake_and_are_inspected_in_pieces(self):
        pdf = lambda: {"filename": "scan.pdf", "content": b"%PDF-1.4 not a real scan"}
        for label, arguments, files in (
                ("email", {"source": "email", "instructor_review_required": True}, [pdf()]),
                ("api", {"source": "api", "instructor_review_required": True}, [pdf()]),
                ("no review", {"source": "cli", "instructor_review_required": False}, [pdf()]),
                ("not a pdf", {"source": "cli", "instructor_review_required": True}, [{"filename": "scan.pdf", "content": b"PK zip"}]),
                ("no files", {"source": "cli", "instructor_review_required": True}, [])):
            with self.subTest(label), self.assertRaises(ValueError):
                self.inbox.submit({"action": "scan.receive"}, files, large_scans=True, **arguments)
        with self.assertRaises(ValueError):
            self.inbox.submit({"action": "roster.sync"}, [pdf()], source="cli", instructor_review_required=True, large_scans=True)
        # A copy damaged in transfer: a scanner's compressed PDF never holds 64 KB of consecutive zero bytes.
        with self.assertRaisesRegex(ValueError, r"scan\.pdf looks damaged in transfer: 68 KB of empty bytes start at byte 5,009"):
            self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": b"%PDF-1.4 " + b"x" * 5000 + b"\x00" * 70000}],
                              source="cli", instructor_review_required=True, large_scans=True)
        # Ordinary submissions keep the 10 MB attachment limit.
        with self.assertRaisesRegex(ValueError, "at most 10 MB"):
            self.inbox.submit({"action": "scan.receive"}, [{"filename": "big.pdf", "content": b"%PDF-" + b"0" * (10 * 1024 * 1024)}], source="web")
        # 45 pages are inspected as pieces of 20, 20 and 5, and keep their place in the whole scan.
        from unittest.mock import patch
        from document_identity import process
        self.scans.alias_ocr.enabled = True
        calls = []
        real = process.run
        with patch("document_identity.scans.process.run", side_effect=lambda operation, *a, **k: calls.append((operation, a[1] if len(a) > 1 else None)) or real(operation, *a, **k)), \
                patch("document_identity.alias_ocr.MAX_PER_MESSAGE", 10):
            message = self.scan(*[(copy, ["L1"] + [P2, P3] * 7) for copy in (1, 2, 3)])
            # PIPELINE: twenty pages is a worker turn, not just a sandbox limit. No grouping or OCR sees an incomplete scan.
            with connect(self.path) as db:
                self.assertEqual(tuple(db.execute("SELECT status,pages_total,attempts FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()),
                                 ("pending", 45, 1))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=?", (message,)).fetchone()[0], 20)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_alias_ocr WHERE message_id=?", (message,)).fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_sequence_runs WHERE message_id=?", (message,)).fetchone()[0], 0)
            frq = self.app.extensions["frq_grading"]
            self.assertFalse(frq.process_scan_one())
            self.inbox._work_queue_cursor = 7  # The queue after scan inspection.
            with patch.object(frq, "process_one", return_value=True) as grade:
                self.assertTrue(self.inbox.work_once())
                grade.assert_called_once()
            self.assertTrue(self.scans.process_one())
            with connect(self.path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=?", (message,)).fetchone()[0], 40)
            self.assertTrue(self.scans.process_one())
            with connect(self.path) as db:
                self.assertEqual(tuple(db.execute("SELECT status,attempts FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()), ("complete", 1))
        self.assertEqual(calls, [("pages", None), ("extract", {"first": 1, "last": 20}), ("extract", {"first": 21, "last": 40}), ("extract", {"first": 41, "last": 45})])
        item = self.scans.review_item(message)
        self.assertEqual([page["source_page_index"] for page in item["pages"]], list(range(1, 46)))
        self.assertEqual([page["source_page_index"] for page in item["pages"] if page["status"] == "resolved"], [1, 16, 31])
        self.assertEqual((item["recognition"]["verdict"], item["recognition"]["papers"][0]["copies"], item["recognition"]["papers"][0]["issued"],
                          [copy["copy_number"] for copy in item["recognition"]["papers"][0]["unseen"]], item["recognition"]["without_label"]),
                         ("recognized", 3, 5, [4, 5], 42))  # Grouped automatically; no page takes an identity from its position.
        # Stamped paper keeps the printed-label fallback, bounded for any one scan.
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_alias_ocr").fetchone()[0], 10)
        self.assertEqual([(run["automatic"], run["sides"], run["packets"]) for run in item["sequence"]["runs"]], [(True, 15, 3)])
        # Already grouped. This job still expects a label on every page, so the instructor's own preview can number the written sides.
        plan = self.post(message, "/preview", sides=15).json
        self.assertEqual((plan["counts"]["grouped"], plan["counts"]["ready"], plan["counts"]["identified"], plan["counts"]["complete"]), (0, 3, 6, 0))

    def test_interrupted_chunk_requires_retry_and_reuses_retained_pages(self):
        from unittest.mock import patch
        # INBOX / PIPELINE: yielding is safe to resume, but an interrupted running chunk still needs explicit retry.
        with patch("document_identity.scans.DECODE_CHUNK", 2):
            message = self.scan((1, ["L1", "L2", "L3"]), (2, ["L1", "L2"]))
            retain = self.scans._retain_page

            def interrupted(*args):
                retain(*args)
                raise KeyboardInterrupt()

            with patch.object(self.scans, "_retain_page", side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                self.scans.process_one()
            with connect(self.path) as db:
                self.assertEqual(db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()[0], "running")
                saved = [tuple(row) for row in db.execute("SELECT * FROM scan_pages WHERE message_id=? ORDER BY source_page_index", (message,))]
                self.assertEqual(len(saved), 3)
            self.assertFalse(self.scans.process_one())
            with connect(self.path) as db:
                self.assertEqual(db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()[0], "failed")
            self.assertFalse(self.scans.process_one())
            self.scans.retry(message)
            self.assertTrue(self.scans.process_one())
            with connect(self.path) as db:
                self.assertEqual(tuple(db.execute("SELECT status,attempts FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()), ("complete", 2))
                pages = [tuple(row) for row in db.execute("SELECT * FROM scan_pages WHERE message_id=? ORDER BY source_page_index", (message,))]
                self.assertEqual(len(pages), 5)
                self.assertEqual(pages[:3], saved)

    def test_inspection_budget_spans_attachments_without_reextracting_retained_pages(self):
        from unittest.mock import patch
        from document_identity import process
        # PIPELINE / INBOX: a turn's page budget is shared by every attachment of a combined scan.
        source = (FIXTURES / "letter.pdf").read_bytes()
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": f"scan-{i}.pdf", "content": source} for i in range(2)],
                                source="web", idempotency_key=str(uuid.uuid4()), instructor_review_required=True)
        message, extracted, real = job["message_id"], [], process.run

        def observed(operation, content, *args, **kwargs):
            if operation == "extract":
                extracted.append(args[0])
            return real(operation, content, *args, **kwargs)

        with patch("document_identity.scans.DECODE_CHUNK", 4), patch("document_identity.scans.process.run", side_effect=observed):
            self.assertTrue(self.scans.process_one())
            self.assertEqual(extracted, [{"first": 1, "last": 3}, {"first": 1, "last": 1}])
            with connect(self.path) as db:
                self.assertEqual(db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()[0], "pending")
                saved = {row["id"]: tuple(row) for row in db.execute("SELECT * FROM scan_pages WHERE message_id=?", (message,))}
                self.assertEqual(len(saved), 4)
            self.assertTrue(self.scans.process_one())
            self.assertEqual(extracted[-1], {"first": 2, "last": 3})
            with connect(self.path) as db:
                self.assertEqual(tuple(db.execute("SELECT status,pages_total,attempts FROM document_decode_jobs WHERE message_id=?", (message,)).fetchone()),
                                 ("complete", 6, 1))
                finished = {row["id"]: tuple(row) for row in db.execute("SELECT * FROM scan_pages WHERE message_id=?", (message,))}
                self.assertEqual(len(finished), 6)
                self.assertTrue(all(finished[key] == value for key, value in saved.items()))

    def test_pending_scans_rotate_between_chunks(self):
        from unittest.mock import patch
        # PIPELINE: both scans advance, even when the older PDF needs more turns.
        source = (FIXTURES / "letter.pdf").read_bytes()
        jobs = [self.inbox.submit({"action": "scan.receive"}, [{"filename": f"scan-{i}.pdf", "content": source}], source="web",
                                 idempotency_key=str(uuid.uuid4()), instructor_review_required=True) for i in range(2)]
        with patch("document_identity.scans.DECODE_CHUNK", 2):
            for job in jobs:
                self.assertTrue(self.scans.process_one())
                with connect(self.path) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_pages WHERE message_id=?", (job["message_id"],)).fetchone()[0], 2)
            for job in jobs:
                self.assertTrue(self.scans.process_one())
                with connect(self.path) as db:
                    self.assertEqual(db.execute("SELECT status FROM document_decode_jobs WHERE message_id=?", (job["message_id"],)).fetchone()[0], "complete")

    def test_a_different_count_of_written_sides_and_superseded_paper_are_reported(self):
        message = self.scan((1, ["L1", P2, P3]), (2, ["L1", BLANK, P3]), (3, ["L1", P2, P3]), (4, ["L1", P2, P3]))
        # Evenly spaced, so the exams are grouped on their own: a packet is every side in order, however many are written.
        # Numbering pages by position is where a blank in the middle matters, and that stays the instructor's previewed decision.
        self.assertEqual([(run["automatic"], run["packets"], run["pages"]) for run in self.scans.review_item(message)["sequence"]["runs"]], [(True, 4, 0)])
        plan = self.post(message, "/preview", sides=3).json
        self.assertEqual([bool(exam["problems"]) for exam in plan["exams"]], [False, True, False, False])
        self.assertIn("It has 2 written sides where most papers have 3", plan["exams"][1]["problems"][0])
        self.assertEqual(plan["counts"]["identified"], 6)
        self.documents.transition(self.batch["id"], "supersede")
        other = self.scan((1, ["L1", P3, P2]))
        recognition = self.scans.review_item(other)["recognition"]
        # Superseded paper is named, and the scan is not called recognized.
        self.assertEqual((recognition["verdict"], recognition["doubtful_labels"], [(paper["title"], paper["current"]) for paper in recognition["papers"]]),
                         ("mixed", {"superseded": 1}, [("Exam 1", False)]))
        late = self.post(other, "/preview", sides=3).json
        self.assertEqual((late["exams"], late["counts"]["unlabeled"]), ([], 3))
        self.assertIn("after a barcode that is superseded", late["runs"][0]["where"])
        # Superseding after the preview changes the plan, so the earlier approval no longer applies.
        self.assertEqual(self.post(message, sides=3, fingerprint=plan["fingerprint"]).status_code, 409)


if __name__ == "__main__":
    unittest.main()
