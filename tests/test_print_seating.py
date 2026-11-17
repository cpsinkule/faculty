"""Seating charts on printed stamps: captions beside the mark, one copy per seated student, and peel-off labels."""
import io
import json
import re
import tempfile
import unittest
import uuid

import zxingcpp
from werkzeug.exceptions import Conflict
from pypdf import PdfReader

from app import create_app
from document_identity.media import render_pages, label_boxes, LABEL_MEDIA, MM
from rosters import connect
from test_dr_exams import booking, workbook as dr_workbook
from test_rooms import fixture
from test_rosters import csv_bytes
from test_seating import HEADERS, SECTION_507, SECTION_508


class PrintSeatingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.documents = self.app.extensions["documents"]
        self.inbox = self.app.extensions["inbox"]
        self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026, "sections": [507, 508], "multiple_sections": True}, headers=HEADERS)
        state = self.client.get("/api/state").json
        self.group, definition_id = state["course_groups"][0], state["course_definitions"][0]["id"]
        for number, rows in ((507, SECTION_507), (508, SECTION_508)):
            self.client.post(f"/api/course-groups/{self.group['id']}/sections/{number}/roster", data={"file": (io.BytesIO(csv_bytes(rows)), "roster.csv")}, headers=HEADERS)
        self.assignment = self.client.post("/api/documents/assignments", json={"definition_id": definition_id, "key": "test-1", "title": "Test 1"}, headers=HEADERS).json
        self.other = self.client.post("/api/documents/assignments", json={"definition_id": definition_id, "key": "quiz-3", "title": "Quiz 3"}, headers=HEADERS).json
        self.version = self.client.post("/api/documents/versions/fixture", json={"assignment_id": self.assignment["id"], "label": "A", "paper": "letter"}, headers=HEADERS).json
        self.hall = self.client.post("/api/rooms", data={"name": "HECC 105", "file": (io.BytesIO(fixture("HECC 105")), "HECC 105.xlsx")}, headers=HEADERS).json
        content = dr_workbook([booking()])
        preview = self.client.post("/api/accommodations/exams/preview", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None)}, headers=HEADERS).json
        self.client.post("/api/accommodations/exams/import", data={"file": (io.BytesIO(content), "exams.xlsx"), "term": json.dumps(None), "preview_hash": preview["preview_hash"], "idempotency_key": str(uuid.uuid4())}, headers=HEADERS)
        self.assertTrue(self.inbox.process_one())
        self.sections = {item["number"]: item for item in self.client.get("/api/seating/catalog").json["sections"]}
        self.chart = self.client.post("/api/seating", json={"assignment_id": self.assignment["id"], "section_ids": [self.sections[507]["id"], self.sections[508]["id"]], "room_id": self.hall["id"]}, headers=HEADERS).json

    def batch(self, **overrides):
        payload = {"version_id": self.version["id"], "section_id": self.sections[507]["id"], "seating_chart_id": self.chart["id"], "extras": 1, "request_id": str(uuid.uuid4()), **overrides}
        return self.client.post("/api/documents/batches", json=payload, headers=HEADERS)

    def build(self, batch_id):
        self.assertTrue(self.documents.build_one())
        detail = self.client.get(f"/api/documents/batches/{batch_id}").json
        self.assertEqual(detail["status"], "ready", detail.get("error"))
        return detail

    def test_seating_chart_allocates_one_copy_per_seated_student_with_captions(self):
        response = self.batch()
        self.assertEqual(response.status_code, 202, response.json)
        batch = response.json
        seated = {student["enrollment_id"]: student for student in self.chart["students"] if student["status"] == "seated" and student["section_number"] == 507}
        self.assertEqual(len(seated), 5)  # Alice's Test 1 booking keeps her out.
        self.assertEqual((batch["quantity"], batch["seating_chart_id"], len(batch["copies"])), (6, self.chart["id"], 6))
        assigned = [copy for copy in batch["copies"] if copy["enrollment_id"]]
        self.assertEqual({copy["enrollment_id"] for copy in assigned}, set(seated))
        labels = [copy["seat_label"] for copy in assigned]
        self.assertEqual(labels, sorted(labels, key=lambda label: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", label)]))
        first = assigned[0]
        self.assertEqual(first["caption"], f"Seat {first['seat_label']} · {seated[first['enrollment_id']]['name']} · Sec 507 · HECC 105")
        self.assertEqual((batch["copies"][-1]["enrollment_id"], batch["copies"][-1]["caption"], batch["copies"][-1]["seat_label"]), (None, "", ""))
        with connect(self.documents.path) as db:
            manifest = self.documents.manifest(db, batch["id"])
        self.assertEqual(len(manifest), 18)
        self.assertEqual(manifest[0]["caption"], first["caption"])
        self.assertNotIn("caption", manifest[-1])
        # The same request is idempotent; a different chart or section is rejected.
        again = self.client.post("/api/documents/batches", json={"version_id": self.version["id"], "section_id": self.sections[507]["id"], "seating_chart_id": self.chart["id"], "extras": 1, "request_id": batch["request_id"]}, headers=HEADERS)
        self.assertEqual(again.json["id"], batch["id"])
        for overrides, message in (
            ({"section_id": self.sections[508]["id"], "seating_chart_id": "missing"}, "no longer exists"),
            ({"extras": -1}, "between 0 and 500"),
            ({"extras": "2"}, "between 0 and 500"),
        ):
            with self.subTest(overrides=overrides):
                rejected = self.batch(**overrides)
                self.assertIn(rejected.status_code, (400, 404), rejected.json)
                self.assertIn(message, rejected.json["error"])
        quiz_chart = self.client.post("/api/seating", json={"assignment_id": self.other["id"], "section_ids": [self.sections[507]["id"]], "room_id": self.hall["id"]}, headers=HEADERS).json
        wrong = self.batch(seating_chart_id=quiz_chart["id"])
        self.assertEqual(wrong.status_code, 400)
        self.assertIn("different exam assignment", wrong.json["error"])
        chart_page = self.client.get(f"/api/seating/{self.chart['id']}").json
        self.assertEqual([(item["id"], item["status"], item["section_number"]) for item in chart_page["print_batches"]], [(batch["id"], "pending", 507)])

    def test_stamped_pages_carry_the_caption_and_labels_reproduce_every_token(self):
        batch = self.batch().json
        self.build(batch["id"])
        pdf = self.documents.download(batch["id"])
        reader = PdfReader(io.BytesIO(pdf))
        self.assertEqual(len(reader.pages), 18)
        with connect(self.documents.path) as db:
            manifest = self.documents.manifest(db, batch["id"])
        for entry, page in zip(manifest, reader.pages, strict=True):
            text = page.extract_text()
            if "caption" in entry:
                self.assertIn(f"Seat {entry['seat_label']}", text)
                self.assertIn("HECC 105", text)
            else:
                self.assertNotIn("Seat ", text)
        # Peel-off labels for the QL-800 by default: one 29 x 90 mm label page per stamp, same tokens,
        # validated in the sandbox, then retained and verified.
        response = self.client.get(f"/api/documents/batches/{batch['id']}/labels")
        self.assertEqual(response.status_code, 200, response.data[:200])
        self.assertEqual(response.mimetype, "application/pdf")
        labels = response.data
        reader = PdfReader(io.BytesIO(labels))
        self.assertEqual(len(reader.pages), len(manifest))
        first = reader.pages[0]
        # Drawn as a 90 x 29 mm strip and stored rotated, so the printer sees its portrait 29 x 90 mm paper.
        self.assertEqual((round(float(first.mediabox.width) / MM), round(float(first.mediabox.height) / MM), first.rotation), (90, 29, 90))
        pages = list(render_pages(labels, 200))
        self.assertGreater(pages[0].height, pages[0].width)
        strip = next(render_pages(labels, 200, unrotate=True))
        self.assertGreater(strip.width, strip.height)
        found = sorted(symbol.text for image in pages for symbol in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True) if symbol.text.startswith("T1-"))
        self.assertEqual(found, sorted(entry["token"] for entry in manifest))
        text = first.extract_text()
        self.assertIn(f"Seat {manifest[0]['seat_label']}", text)
        self.assertIn("copy 1 · page 1/3", text)
        self.assertIn("Test 1", text)
        self.assertIn(manifest[0]["recovery_alias"], text)
        self.assertEqual(self.client.get(f"/api/documents/batches/{batch['id']}/labels").data, labels)
        # The 62 x 29 mm roll label and the Avery sheet are built and retained separately; unknown media are refused.
        small = self.client.get(f"/api/documents/batches/{batch['id']}/labels?media=dk-1209").data
        page = PdfReader(io.BytesIO(small)).pages[0]
        self.assertEqual((round(float(page.mediabox.width) / MM), round(float(page.mediabox.height) / MM), page.rotation), (62, 29, 0))
        self.assertIn(f"Seat {manifest[0]['seat_label']}", page.extract_text())
        sheet = self.client.get(f"/api/documents/batches/{batch['id']}/labels?media=avery-5160").data
        self.assertEqual(len(list(render_pages(sheet, 200))), 1)
        self.assertEqual(self.client.get(f"/api/documents/batches/{batch['id']}/labels?media=dk-2000").status_code, 400)
        detail = self.client.get(f"/api/documents/batches/{batch['id']}").json
        self.assertEqual({(item["media"], item["path"].rsplit("/", 1)[1]) for item in detail["labels"]},
                         {("dk-1201", "labels-dk-1201.pdf"), ("dk-1209", "labels-dk-1209.pdf"), ("avery-5160", "labels.pdf")})
        self.assertEqual([event["kind"] for event in detail["events"]].count("labels_issued"), 3)
        self.assertEqual([item["labels_ready"] for item in self.client.get(f"/api/seating/{self.chart['id']}").json["print_batches"]], [1])

    def test_seat_numbers_can_be_printed_beside_the_stamp(self):
        from document_identity.layouts import SEAT_MARK_SIZE, seat_mark_positions, stamp_boxes
        plain = self.batch().json
        self.assertEqual(plain["seat_marks"], 0)
        with connect(self.documents.path) as db:
            self.assertNotIn("seat_mark", self.documents.manifest(db, plain["id"])[0])
        # The option changes the print instructions, so the same request ID with it is a different job.
        clash = self.batch(request_id=plain["request_id"], seat_marks=True)
        self.assertEqual(clash.status_code, 409, clash.json)
        for overrides, message in ((dict(seat_marks="yes"), "visible seat label"), (dict(seating_chart_id=None, quantity=2, seat_marks=True), "need a seating chart")):
            with self.subTest(overrides=overrides):
                payload = {"version_id": self.version["id"], "section_id": self.sections[507]["id"], "seating_chart_id": self.chart["id"], "extras": 0, "request_id": str(uuid.uuid4()), **overrides}
                payload = {key: value for key, value in payload.items() if value is not None}
                rejected = self.client.post("/api/documents/batches", json=payload, headers=HEADERS)
                self.assertEqual(rejected.status_code, 400, rejected.json)
                self.assertIn(message, rejected.json["error"])
        marked = self.batch(seat_marks=True).json
        self.assertEqual(marked["seat_marks"], 1)
        self.assertTrue(marked["events"][0]["detail_json"].count('"seat_marks": true') or json.loads(marked["events"][0]["detail_json"])["seat_marks"] is True)
        with connect(self.documents.path) as db:
            manifest = self.documents.manifest(db, marked["id"])
        seated = [entry for entry in manifest if "caption" in entry]
        self.assertTrue(seated and all(entry["seat_mark"] == entry["seat_label"] for entry in seated))
        self.assertNotIn("seat_mark", manifest[-1])  # The extra copy has no seat.
        self.assertTrue(self.documents.build_one())  # The plain batch, validated as before.
        ready = self.build(marked["id"])
        self.assertEqual(ready["seat_marks"], 1)
        reader = PdfReader(io.BytesIO(self.documents.download(marked["id"])))
        width, height = float(reader.pages[0].mediabox.width), float(reader.pages[0].mediabox.height)
        (x, y, box), (mark_x, baseline, align) = stamp_boxes(width, height, "faculty-page-v1")[0], seat_mark_positions(width, height, "faculty-page-v1")[0]
        self.assertEqual(align, "right")
        self.assertLess(mark_x, x)  # Beside the box, never inside it.
        self.assertLess(baseline, y + box)
        self.assertGreater(baseline + 0.72 * SEAT_MARK_SIZE, y + box - 1)  # Its top sits on the box's top edge.
        for entry, page in zip(manifest, reader.pages, strict=True):
            bold = []
            page.extract_text(visitor_text=lambda text, cm, tm, font, size: bold.append((text.strip(), round(tm[5]))) if size and abs(size - SEAT_MARK_SIZE) < 0.01 else None)
            if "seat_mark" in entry:
                self.assertIn((entry["seat_label"], round(baseline)), bold)
            else:
                self.assertEqual(bold, [])
        # The stamps-only export and the chart's job list carry the choice too.
        confirmed = self.client.post(f"/api/seating/{self.chart['id']}/confirm", json={"version": self.client.get(f"/api/seating/{self.chart['id']}").json["version"], "mode": "stamps_only",
                                     "page_count": 2, "request_id": "seat-marks", "seat_marks": True, "prepare_cards": False}, headers=HEADERS)
        self.assertEqual(confirmed.status_code, 201, confirmed.json)
        for item in confirmed.json["exported_batches"]:
            self.assertEqual(self.client.get(f"/api/documents/batches/{item['id']}").json["seat_marks"], 1)
        self.assertEqual({item["seat_marks"] for item in confirmed.json["print_batches"] if item["id"] in {b["id"] for b in confirmed.json["exported_batches"]}}, {1})
        self.assertIn("visible seat label", self.client.post(f"/api/seating/{self.chart['id']}/confirm", json={"version": confirmed.json["version"], "mode": "stamps_only", "page_count": 2,
                                                                     "request_id": "seat-marks-2", "seat_marks": "yes", "prepare_cards": False}, headers=HEADERS).json["error"])

    def test_a_scantron_label_follows_each_copys_pages_on_the_labels_only(self):
        # DOCUMENTS "SCANTRON LABELS" (requirement, 2026-10-15): one more label per student, for the bubble sheet, never a page.
        plain = self.batch().json
        refused = self.batch(sheet_labels="yes")
        self.assertEqual(refused.status_code, 400, refused.json)
        self.assertIn("Scantron label", refused.json["error"])
        self.assertEqual(self.batch(request_id=plain["request_id"], sheet_labels=True).status_code, 409)  # A different job under the same request.
        labeled = self.batch(sheet_labels=True).json
        with connect(self.documents.path) as db:
            self.assertEqual(self.documents.label_manifest(db, plain["id"]), self.documents.manifest(db, plain["id"]))
            pages, labels = self.documents.manifest(db, labeled["id"]), self.documents.label_manifest(db, labeled["id"])
        self.assertEqual((len(pages), len(labels)), (18, 24))  # Six copies of three pages; each copy's Scantron label follows its pages.
        self.assertEqual([entry.get("sheet", False) for entry in labels[:8]], [False, False, False, True] * 2)
        self.assertEqual([entry["output_page"] for entry in labels], list(range(1, 25)))
        self.assertEqual((labels[3]["copy_number"], labels[3]["caption"], labels[-1]["copy_number"]), (1, pages[0]["caption"], 6))
        self.assertNotIn("caption", labels[-1])  # The extra copy has no seat.
        sheets = [entry["token"] for entry in labels if entry.get("sheet")]
        self.assertTrue(set(sheets).isdisjoint(entry["token"] for entry in pages))
        self.assertTrue(self.documents.build_one())
        self.build(labeled["id"])
        self.assertEqual(len(PdfReader(io.BytesIO(self.documents.download(labeled["id"]))).pages), 18)  # Nothing is stamped for the sheet.
        roll = self.client.get(f"/api/documents/batches/{labeled['id']}/labels").data
        found = sorted(symbol.text for image in render_pages(roll, 200) for symbol in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True))
        self.assertEqual(found, sorted(entry["token"] for entry in labels))
        reader = PdfReader(io.BytesIO(roll))
        self.assertIn("copy 1 · Scantron", reader.pages[3].extract_text())
        self.assertIn("copy 1 · page 3/3", reader.pages[2].extract_text())
        self.assertEqual(len(PdfReader(io.BytesIO(self.documents.room_export([labeled["id"]], "labels"))).pages), 24)
        self.assertEqual(len(PdfReader(io.BytesIO(self.documents.room_export([labeled["id"]], "labels", "avery-5160"))).pages), 1)
        # The seating chart page prints the Scantron labels on their own button, and the page labels without them (2026-10-18).
        for kind, wanted in (("page-labels", pages), ("scantron-labels", [entry for entry in labels if entry.get("sheet")])):
            for stock in ("dk-1201", "avery-5160"):
                output = self.documents.room_export([labeled["id"]], kind, stock)
                found = sorted(symbol.text for image in render_pages(output, 200) for symbol in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True))
                self.assertEqual(found, sorted(entry["token"] for entry in wanted), (kind, stock))
        self.assertEqual(len(PdfReader(io.BytesIO(self.documents.room_export([labeled["id"]], "scantron-labels"))).pages), 6)
        with self.assertRaisesRegex(Conflict, "no Scantron labels"):
            self.documents.room_export([plain["id"]], "scantron-labels")
        # Scan intake leaves a Scantron label's sheet an unlabeled page: it is no page of the paper.
        scans = self.app.extensions["document_scans"]
        self.assertEqual(scans.classify({"candidates": [{"payload": sheets[0]}], "blank": False}), (None, "unreadable"))
        self.assertEqual(scans.classify({"candidates": [{"payload": pages[0]["token"]}], "blank": False})[1], "resolved")
        # The stamps-only export carries the choice to every section's job.
        version = self.client.get(f"/api/seating/{self.chart['id']}").json["version"]
        self.assertIn("Scantron label", self.client.post(f"/api/seating/{self.chart['id']}/confirm", json={
            "version": version, "mode": "stamps_only", "page_count": 1, "request_id": "scantron-0", "sheet_labels": 1, "prepare_cards": False}, headers=HEADERS).json["error"])
        confirmed = self.client.post(f"/api/seating/{self.chart['id']}/confirm", json={"version": version, "mode": "stamps_only", "page_count": 1,
                                     "request_id": "scantron", "sheet_labels": True, "prepare_cards": False}, headers=HEADERS)
        self.assertEqual(confirmed.status_code, 201, confirmed.json)
        with connect(self.documents.path) as db:
            for item in confirmed.json["exported_batches"]:
                kinds = [entry.get("sheet", False) for entry in self.documents.label_manifest(db, item["id"])]
                self.assertEqual(kinds, [False, True] * item["quantity"])

    def test_a_table_room_seat_label_is_printed_whole_beside_the_stamp(self):
        # "K Table 2 · 14" once lost its seat number to a 12-character cut before the width check.
        from pathlib import Path
        from document_identity import media, tokens
        from document_identity.layouts import SEAT_MARK_SIZE
        canonical = (Path(media.__file__).parent / "fixtures/letter.pdf").read_bytes()
        for label in ("K Table 2 · 4", "K Table 2 · 14", "A12"):
            manifest = [{"output_page": number, "token": tokens.generate_token(), "page_number": number, "recovery_alias": tokens.generate_alias(),
                         "symbology": "DataMatrix", "copy_number": 1, "caption": f"Seat {label} · Example, Alice · Sec 507", "seat_label": label, "seat_mark": label}
                        for number in range(1, len(PdfReader(io.BytesIO(canonical)).pages) + 1)]
            stamped = media.stamp_pdf(canonical, manifest)
            bold = []
            PdfReader(io.BytesIO(stamped[0] if isinstance(stamped, tuple) else stamped)).pages[0].extract_text(
                visitor_text=lambda text, cm, tm, font, size: bold.append(text.strip()) if size and abs(size - SEAT_MARK_SIZE) < 0.01 else None)
            self.assertIn(label, bold)

    def test_a_seat_label_names_its_version_and_keeps_a_table_seat_whole(self):
        # DOCUMENTS "SEAT LABELS NAME THE STUDENT'S VERSION AND SECTION": the version beside the seat when both fit whole, else first on
        # the exam's line; the name after a table seat is the student's, not the seat's number.
        from document_identity import media, tokens
        header = {"exam": "Exam 2", "version": "Version Stamps only · 1 page", "section": "Sections 507, 508", "term": "Fall 2026", "room": "HECC 207", "page_count": 1}
        for label in ("B4", "K Table 2 · 14"):
            entry = {"output_page": 1, "token": tokens.generate_token(), "page_number": None, "sheet": True, "recovery_alias": None, "symbology": "DataMatrix",
                     "copy_number": 13, "caption": f"Seat {label} · McCall, Kathleen Lindy · Sec 508 · HECC 207", "seat_label": label,
                     "section": "Section 508", "exam_version": "B"}
            for kind in ("avery-5160", "dk-1201", "dk-1209"):
                text = PdfReader(io.BytesIO(media.labels_pdf([entry], header, media.PROFILE, kind))).pages[0].extract_text()
                self.assertIn(f"Seat {label}", text, kind)
                self.assertIn("McCall, Kathleen Lindy", text, kind)
                self.assertIn("Section 508", text, kind)
                self.assertNotIn("Sections", text, kind)
                self.assertIn("Version B", text, kind)
                if label == "B4":
                    self.assertIn("Seat B4 · Version B", text, kind)
                if kind != "dk-1201" and label != "B4":
                    self.assertIn("Version B · Exam 2 · Section 508", text, kind)
                self.assertNotIn("Stamps only", text, kind)

    def test_seat_numbers_can_be_added_or_removed_while_a_job_waits_in_the_queue(self):
        from document_identity.layouts import SEAT_MARK_SIZE
        toggle = lambda batch_id, value: self.client.post(f"/api/documents/batches/{batch_id}/seat-marks", json={"seat_marks": value}, headers=HEADERS)
        queued = self.batch().json
        self.assertEqual(queued["seat_marks"], 0)
        self.assertEqual(toggle("missing", True).status_code, 404)
        self.assertIn("visible seat label", toggle(queued["id"], "yes").json["error"])
        plain = self.client.post("/api/documents/batches", json={"version_id": self.version["id"], "section_id": self.sections[507]["id"], "quantity": 1, "request_id": str(uuid.uuid4())}, headers=HEADERS).json
        self.assertIn("need a seating chart", toggle(plain["id"], True).json["error"])
        # Turning it on while pending changes what the worker will render; the creation request keeps its hash.
        turned_on = toggle(queued["id"], True)
        self.assertEqual((turned_on.status_code, turned_on.json["seat_marks"], turned_on.json["request_hash"]), (200, 1, queued["request_hash"]))
        self.assertEqual(json.loads(turned_on.json["events"][-1]["detail_json"]), {"seat_marks": True})
        self.assertEqual(toggle(queued["id"], True).json["events"][-1]["kind"], "seat_marks")  # A repeat changes nothing and records nothing new.
        self.assertEqual(len([e for e in toggle(queued["id"], True).json["events"] if e["kind"] == "seat_marks"]), 1)
        with connect(self.documents.path) as db:
            self.assertEqual(self.documents.manifest(db, queued["id"])[0]["seat_mark"], queued["copies"][0]["seat_label"])
        self.assertEqual(toggle(queued["id"], False).json["seat_marks"], 0)
        with connect(self.documents.path) as db:
            self.assertNotIn("seat_mark", self.documents.manifest(db, queued["id"])[0])
        self.assertEqual(toggle(queued["id"], True).json["seat_marks"], 1)
        self.assertEqual([item["seat_marks"] for item in self.client.get(f"/api/seating/{self.chart['id']}").json["print_batches"] if item["id"] == queued["id"]], [1])
        self.assertTrue(self.documents.build_one())  # plain
        ready = self.build(queued["id"])
        bold = []
        PdfReader(io.BytesIO(self.documents.download(queued["id"]))).pages[0].extract_text(
            visitor_text=lambda text, cm, tm, font, size: bold.append(text.strip()) if size and abs(size - SEAT_MARK_SIZE) < 0.01 else None)
        self.assertIn(ready["copies"][0]["seat_label"], bold)
        # Once issued, the pages are fixed.
        refused = toggle(queued["id"], False)
        self.assertEqual(refused.status_code, 409)
        self.assertIn("already issued", refused.json["error"])

    def test_a_whole_room_prints_and_labels_in_seat_order_across_sections(self):
        from document_identity.service import seat_order
        route = f"/api/seating/{self.chart['id']}"
        # The chart hands out two versions by seat; each label names its student's (DOCUMENTS "SEAT LABELS NAME THE STUDENT'S VERSION AND SECTION").
        versioned = self.client.post(route + "/exam-versions", json={"version": self.chart["version"], "count": 2}, headers=HEADERS)
        self.assertEqual(versioned.status_code, 200, versioned.json)
        confirmed = self.client.post(route + "/confirm", json={"version": versioned.json["version"], "mode": "stamps_only", "page_count": 2, "request_id": "room-1", "prepare_cards": False}, headers=HEADERS)
        self.assertEqual(confirmed.status_code, 201, confirmed.json)
        jobs = self.client.get(route).json["print_batches"]
        self.assertEqual({job["export"] for job in jobs}, {"room-1"})
        self.assertEqual(sorted(job["section_number"] for job in jobs), [507, 508])
        query = "batches=" + ",".join(job["id"] for job in jobs)
        room = lambda kind, extra="": self.client.get(f"{route}/room-export/{kind}?{query}{extra}")
        # Until every section's job is ready, the room file is refused; a foreign or bogus job is rejected outright.
        waiting = room("print")
        self.assertEqual(waiting.status_code, 409)
        self.assertIn("waiting on Section 507, Section 508", waiting.json["error"])
        self.assertEqual(self.client.get(f"{route}/room-export/print?batches=nope").status_code, 400)
        self.assertEqual(room("posters").status_code, 400)
        self.assertEqual(room("labels", "&media=dk-2000").status_code, 400)
        for _ in jobs:
            self.assertTrue(self.documents.build_one())
        students = [student for student in self.client.get(route).json["students"] if student["seat"]]
        seats = {student["enrollment_id"]: (student["seat"], student["section_number"]) for student in students}
        letters = {student["seat"]: student["exam_version"] for student in students}
        self.assertEqual(set(letters.values()), {"A", "B"})
        expected = sorted(seats.values(), key=lambda item: seat_order(item[0]))
        self.assertEqual(len(expected), 8)  # Five in 507 (Alice's booking keeps her out) and three in 508.
        self.assertGreater(len({section for _, section in expected}), 1)
        # The room print PDF holds every section's pages, seated copies in seat order with each copy's pages together.
        printed = room("print")
        self.assertEqual((printed.status_code, printed.mimetype), (200, "application/pdf"))
        reader = PdfReader(io.BytesIO(printed.data))
        self.assertEqual(len(reader.pages), 16)
        # Seats are random, and a table seat is labelled like "K Table 1 · 3": the seat is everything before the name, not one word.
        captions = [re.search(r"Seat (.+) · [^·]+ · Sec (\d+)", page.extract_text()) for page in reader.pages]
        self.assertTrue(all(captions))
        order = [(match.group(1), int(match.group(2))) for match in captions]
        self.assertEqual(order, [item for item in expected for _ in range(2)])
        self.assertEqual(reader.metadata.get("/FacultyRoomExport"), "seat-order")
        # Roll labels are re-cut from the sections' verified label files, one label per page, same order, rotation kept.
        labels = room("labels", "&media=dk-1201")
        self.assertEqual(labels.status_code, 200, labels.data[:200])
        label_reader = PdfReader(io.BytesIO(labels.data))
        self.assertEqual(len(label_reader.pages), 16)
        self.assertEqual({page.rotation for page in label_reader.pages}, {90})
        for page, (seat, section) in zip(label_reader.pages, [item for item in expected for _ in range(2)]):
            self.assertRegex(page.extract_text(), rf"Seat {re.escape(seat)} · Version {letters[seat]}")
            self.assertIn(f"· Section {section} ·", page.extract_text())
        tokens = sorted(symbol.text for image in render_pages(labels.data, 200) for symbol in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True) if symbol.text.startswith("T1-"))
        with connect(self.documents.path) as db:
            persisted = sorted(entry["token"] for job in jobs for entry in self.documents.manifest(db, job["id"]))
        self.assertEqual(tokens, persisted)
        self.assertEqual({job["labels_ready"] for job in self.client.get(route).json["print_batches"]}, {1})
        # Sheet labels are laid out afresh across sections and validated like any label file.
        sheet = room("labels", "&media=avery-5160")
        self.assertEqual(sheet.status_code, 200, sheet.data[:200])
        sheet_text = PdfReader(io.BytesIO(sheet.data)).pages[0].extract_text()
        # Each label names its own student's section and version, never every section of the room (requirement, 2026-10-25).
        self.assertNotIn("Sections", sheet_text)
        for seat, section in expected:
            self.assertEqual(sheet_text.count(f"Seat {seat} · Version {letters[seat]}"), 2)
        for section in (507, 508):
            self.assertEqual(sheet_text.count(f"· Section {section} ·"), 2 * sum(1 for _, number in expected if number == section))
        self.assertEqual(len(list(render_pages(sheet.data, 200))), 1)
        # A superseded section job takes the room file with it.
        self.assertEqual(self.client.post(f"/api/documents/batches/{jobs[0]['id']}/supersede", json={}, headers=HEADERS).status_code, 200)
        self.assertEqual(room("print").status_code, 409)

    def test_placement_check_trusts_the_printed_outline_over_decoder_jitter(self):
        from types import SimpleNamespace
        from document_identity import media
        from document_identity.layouts import MM, get_layout
        from PIL import ImageChops
        batch = self.batch().json
        with connect(self.documents.path) as db:
            manifest = self.documents.manifest(db, batch["id"])[:1]
        header = {"exam": "Test 1", "version": "Version A", "section": "Section 507", "term": "Fall 2026", "room": "HECC 105", "page_count": 3}
        pdf = media.labels_pdf(manifest, header, "faculty-page-v1", "dk-1201")
        image = next(media.render_pages(pdf, 200, unrotate=True, exact=True))
        _, x, y = media.label_boxes(1, "dk-1201")[0]
        _, (bx, by, size) = media.label_stamp_box(x, y, "faculty-page-v1", "dk-1201")
        scale = 200 / 72
        left, top = bx * scale, image.height - (by + size) * scale
        size_px, quiet = size * scale, get_layout("faculty-page-v1")["module_mm"] * MM * scale
        real = next(c for c in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True) if c.text == manifest[0]["token"])
        corner = lambda point, dx, dy: SimpleNamespace(x=getattr(real.position, point).x + dx, y=getattr(real.position, point).y + dy)
        # A decoder estimate three pixels off a correctly printed mark passes on the mark's own outline.
        jittered = SimpleNamespace(position=SimpleNamespace(top_left=corner("top_left", 0, 0), top_right=corner("top_right", 0, 0), bottom_right=corner("bottom_right", 1, 0), bottom_left=corner("bottom_left", -3, 0)))
        media._validate_symbol(image, jittered, left, top, size_px, quiet)
        media._validate_symbol(image, real, left, top, size_px, quiet)
        # A mark that really sits three pixels away fails even with a perfect decoder estimate.
        shifted = ImageChops.offset(image, 3, 0)
        moved = SimpleNamespace(position=SimpleNamespace(**{point: corner(point, 3, 0) for point in ("top_left", "top_right", "bottom_right", "bottom_left")}))
        with self.assertRaises(ValueError):
            media._validate_symbol(shifted, moved, left, top, size_px, quiet)
        with self.assertRaises(ValueError):
            media._validate_symbol(shifted, real, left, top, size_px, quiet)

    def test_labels_span_sheets_and_need_a_ready_batch(self):
        pending = self.batch().json
        refused = self.client.get(f"/api/documents/batches/{pending['id']}/labels")
        self.assertEqual(refused.status_code, 409)
        # A plain batch (no chart) of 12 copies x 3 pages needs two Avery sheets and generic label text.
        plain = self.client.post("/api/documents/batches", json={"version_id": self.version["id"], "section_id": self.sections[508]["id"], "quantity": 12, "request_id": str(uuid.uuid4())}, headers=HEADERS).json
        self.documents.build_one()  # the pending seated batch
        self.build(plain["id"])
        labels = self.client.get(f"/api/documents/batches/{plain['id']}/labels?media=avery-5160").data
        sheets = list(render_pages(labels, 200))
        self.assertEqual(len(sheets), 2)
        roll = self.client.get(f"/api/documents/batches/{plain['id']}/labels").data
        self.assertEqual(len(PdfReader(io.BytesIO(roll)).pages), 36)
        self.assertNotIn("Seat ", PdfReader(io.BytesIO(roll)).pages[0].extract_text())
        with connect(self.documents.path) as db:
            manifest = self.documents.manifest(db, plain["id"])
        self.assertNotIn("caption", manifest[0])
        avery = LABEL_MEDIA["avery-5160"]
        per_sheet = avery["columns"] * avery["rows"]
        for index, sheet in enumerate(sheets):
            expected = {entry["token"] for entry in manifest[index * per_sheet:(index + 1) * per_sheet]}
            found = {symbol.text for symbol in zxingcpp.read_barcodes(sheet, try_rotate=True, try_downscale=True, try_invert=True) if symbol.text.startswith("T1-")}
            self.assertEqual(found, expected)
        self.assertEqual(len(label_boxes(36)), 36)
        text = PdfReader(io.BytesIO(labels)).pages[0].extract_text()
        self.assertIn("Test 1", text)
        self.assertIn("copy 1 · page 1/3", text)
        self.assertNotIn("Seat ", text)
        # A plain batch still downloads and its manifest is unchanged by the caption feature.
        self.assertEqual(len(PdfReader(io.BytesIO(self.documents.download(plain["id"]))).pages), 36)

    def test_catalog_lists_each_job_with_its_export_origin_and_room(self):
        """Assignments & printing groups jobs by export and sets registration jobs apart; the catalog says which is which."""
        route = f"/api/seating/{self.chart['id']}"
        confirmed = self.client.post(route + "/confirm", json={"version": self.chart["version"], "mode": "stamps_only", "page_count": 1,
                                                              "request_id": "room-7", "prepare_cards": False}, headers=HEADERS)
        self.assertEqual(confirmed.status_code, 201, confirmed.json)
        section = self.sections[508]["id"]
        plain = self.client.post("/api/documents/batches", json={"version_id": self.version["id"], "section_id": section, "quantity": 2,
                                                                 "request_id": f"click-9:{section}"}, headers=HEADERS).json
        registration = self.documents.create_batch(self.version["id"], section, 1, f"unlabeled:run-3:{section}:0123456789abcdef")
        listed = {batch["id"]: batch for batch in self.client.get("/api/documents/catalog").json["batches"]}
        seated = [batch for batch in listed.values() if batch["seating_chart_id"] == self.chart["id"]]
        self.assertEqual(len(seated), 2)
        for batch in seated:
            self.assertEqual((batch["origin"], batch["export"], batch["room_name"]), ("seating", "room-7", "HECC 105"))
            self.assertEqual((batch["label_mode"], batch["seat_marks"], batch["reprint_of_batch_id"]), ("every_page", False, None))
        self.assertEqual((listed[plain["id"]]["origin"], listed[plain["id"]]["export"], listed[plain["id"]]["room_name"]), ("section", "click-9", None))
        self.assertEqual(listed[registration["id"]]["origin"], "registration")
        self.assertTrue(all("request_id" not in batch for batch in listed.values()), "The request ID itself stays in the catalog")
        detail = self.client.get(f"/api/documents/batches/{seated[0]['id']}").json
        self.assertEqual((detail["origin"], detail["room_name"]), ("seating", "HECC 105"))
        self.assertEqual(self.client.get(f"/api/documents/batches/{registration['id']}").json["origin"], "registration")
        # A reprint keeps its own request and points at what it reprints.
        again = self.client.post("/api/documents/batches", json={"version_id": self.version["id"], "section_id": section, "quantity": 2,
                                                                 "request_id": "again-1", "reprint_of_batch_id": plain["id"]}, headers=HEADERS).json
        reprint = next(batch for batch in self.client.get("/api/documents/catalog").json["batches"] if batch["id"] == again["id"])
        self.assertEqual((reprint["origin"], reprint["export"], reprint["reprint_of_batch_id"]), ("section", "again-1", plain["id"]))

    def test_section_roster_names_for_the_copy_pickers(self):
        """The enrollments route keeps its list and adds each student's roster name, sorted by name; no UIN is sent."""
        for number, rows in ((507, SECTION_507), (508, SECTION_508)):
            result = self.client.get(f"/api/documents/sections/{self.sections[number]['id']}/enrollments").json
            self.assertEqual(len(result["enrollments"]), len(rows))
            self.assertEqual(result["enrollments"], sorted(result["enrollments"]))
            self.assertEqual(sorted(item["enrollment_id"] for item in result["students"]), result["enrollments"])
            names = [item["name"] for item in result["students"]]
            self.assertEqual(names, sorted(names, key=str.casefold))
            self.assertTrue(all(set(item) == {"enrollment_id", "name"} for item in result["students"]))
        names = [item["name"] for item in self.client.get(f"/api/documents/sections/{self.sections[507]['id']}/enrollments").json["students"]]
        self.assertEqual(names[:2], ["Example, Alice", "Example, Bob"])
        self.assertEqual(self.client.get("/api/documents/sections/no-such-section/enrollments").json, {"enrollments": [], "students": []})
        # A roster payload that cannot be read still gives the enrollment IDs, so Change assignment can open.
        from unittest import mock
        from course_storage import StorageError
        documents = self.app.extensions["documents"]
        with mock.patch.object(type(documents.rosters), "read_payload", side_effect=StorageError("drive unavailable")):
            result = self.client.get(f"/api/documents/sections/{self.sections[507]['id']}/enrollments")
        self.assertEqual(result.status_code, 200)
        self.assertEqual((len(result.json["enrollments"]), result.json["students"]), (len(SECTION_507), []))

    def test_removing_the_chart_keeps_printed_captions(self):
        batch = self.batch().json
        chart = self.client.get(f"/api/seating/{self.chart['id']}").json
        self.assertEqual(chart["delete_confirmation"], "typed")
        self.assertEqual(self.client.delete(f"/api/seating/{self.chart['id']}", json={"version": chart["version"], "confirmation": "delete"}, headers=HEADERS).status_code, 204)
        detail = self.client.get(f"/api/documents/batches/{batch['id']}").json
        self.assertIsNone(detail["seating_chart_id"])
        self.assertTrue(detail["copies"][0]["caption"].startswith("Seat "))
        self.assertEqual(self.build(batch["id"])["status"], "ready")


if __name__ == "__main__":
    unittest.main()
