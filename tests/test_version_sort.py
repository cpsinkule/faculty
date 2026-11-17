"""Printed-version sorting: queued per scan, one OCR page per turn, PDFs per version, no identity or gate changes."""
import io
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from werkzeug.exceptions import Conflict, Forbidden

from app import create_app
from document_identity import process, version_sort
from document_identity.version_sort import assign, decide, label_rotation, settings, text_orientation
from faculty_ocr import OcrError
from rosters import connect
from teach_inbox.service import sha
from test_storage import temporary_storage, HEADERS

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"
LETTER = PdfReader(FIXTURES / "letter-corners.pdf").pages[0]


class VersionSortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = temporary_storage(self.temp.name)
        self.app = create_app(storage=self.storage)
        self.client = self.app.test_client(); self.client.get("/").close()
        response = self.client.post("/api/courses", json={"name": "Sort", "code": "MATH 140", "term": "Fall",
            "year": 2026, "sections": [507], "is_current": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201)
        self.storage.reconcile_all()
        self.scans = self.app.extensions["document_scans"]
        self.inbox = self.app.extensions["inbox"]
        self.sorter = self.scans.version_sort
        runtime = patch.object(self.sorter, "runtime_available", return_value=True); runtime.start(); self.addCleanup(runtime.stop)

    @staticmethod
    def pdf(count, blanks=()):
        writer = PdfWriter()
        writer.add_metadata({"/Title": uuid.uuid4().hex})  # distinct submissions, never deduplicated by content
        for index in range(1, count + 1):
            if index in blanks:
                writer.add_blank_page(width=612, height=792)
            else:
                writer.add_page(LETTER)
        data = io.BytesIO(); writer.write(data); return data.getvalue()

    def intake(self, content, authenticated=True, inspect=True):
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content}],
            source="email", sender="scanner", idempotency_key=str(uuid.uuid4()),
            authenticated=authenticated, instructor_review_required=True)
        if inspect:
            while self.scans.process_one():  # drain older pending inspections too
                pass
            self.assertEqual(self.review(job)["decode"]["status"], "complete")
        return job

    def review(self, job):
        return self.scans.review_item(job["message_id"])

    @staticmethod
    def result(content, text, band="top", rotation=0):
        # `text` is what the given rotation read; the opposite rotation read only a markdown fence.
        other = "```markdown\n\n```"
        return {"source_sha256": sha(content), "model_id": "test-double", "revision": "test", "device": "cuda",
                "seconds": 0.1, "crop_profile": "faculty-version-band-v1", "needs_review": True,
                "reads": [{"rotation": 0, "band": band, "text": text if rotation == 0 else other, "truncated": False},
                          {"rotation": 180, "band": band, "text": text if rotation == 180 else other, "truncated": False}]}

    def run_all(self, texts, calls=None):
        queue = list(texts); seen = []
        def recognize(content, *, band):
            seen.append(band)
            item = queue.pop(0)
            text, rotation = item if isinstance(item, tuple) else (item, 0)
            return self.result(content, text, band, rotation)
        with patch("document_identity.version_sort.recognize_bands", side_effect=recognize):
            turns = 0
            while self.sorter.process_one():
                turns += 1
        self.assertEqual(queue, [], "every prepared reading was consumed")
        if calls is not None:
            self.assertEqual(len(seen), calls)
        return seen, turns

    def request(self, job, **overrides):
        return self.client.post(f"/api/documents/messages/{job['message_id']}/version-sort",
                                json={"preset": "version-letter", "pages_per_quiz": 2, "band": "top", **overrides}, headers=HEADERS)

    def test_pure_rules(self):
        chosen = settings({"preset": "version-letter"})
        self.assertEqual(decide([{"text": "Name ____  Version A\nQuiz 3"}, {"text": ""}], chosen), ("label", "A"))
        self.assertEqual(decide([{"text": "VERSION: b"}], chosen), ("label", "B"))
        self.assertEqual(decide([{"text": "version Of the quiz"}], chosen), ("none", None))
        self.assertEqual(decide([{"text": "Version A"}, {"text": "Version B"}], chosen), ("conflict", None))
        self.assertEqual(decide([{"text": "Version 4"}], chosen), ("none", None))  # no character repair
        custom = settings({"preset": "custom", "pattern": r"quiz\s*([A-D])", "pages_per_quiz": 3, "band": "bottom"})
        self.assertEqual(decide([{"text": "QUIZ C"}], custom), ("label", "C"))
        self.assertEqual(custom["prefix"], "")
        pages = assign([{"outcome": "none"}, {"outcome": "label", "label": "A"}, {"outcome": "blank"}, {"outcome": "none"},
                        {"outcome": "conflict"}, {"outcome": "none"}, {"outcome": "label", "label": "B"}], 2)
        self.assertEqual([p["assigned"] for p in pages], [None, "A", "A", None, None, None, "B"])
        fence = "```markdown\n\n```"
        self.assertEqual(label_rotation([{"rotation": 0, "text": fence}, {"rotation": 180, "text": "Version C"}], "C", chosen), 180)
        self.assertIsNone(label_rotation([{"rotation": 0, "text": "Version C"}, {"rotation": 180, "text": "Version C"}], "C", chosen))
        self.assertEqual(text_orientation([{"rotation": 0, "text": fence}, {"rotation": 180, "text": "Question 4 continued: f(x) = x^2 + 3x - 5"}]), 180)
        self.assertEqual(text_orientation([{"rotation": 0, "text": "Question 4 continued: f(x) = x^2 + 3x - 5"}, {"rotation": 180, "text": "12 x 34"}]), 0)
        self.assertIsNone(text_orientation([{"rotation": 0, "text": "short"}, {"rotation": 180, "text": fence}]))  # too little text
        self.assertIsNone(text_orientation([{"rotation": 0, "text": "a" * 40}, {"rotation": 180, "text": "b" * 30}]))  # not decisive
        self.assertTrue(settings({})["auto_rotate"]); self.assertFalse(settings({"auto_rotate": False})["auto_rotate"])
        for bad in ({"preset": "nope"}, {"preset": "custom", "pattern": "version"}, {"preset": "custom", "pattern": "("},
                    {"preset": "custom", "pattern": "(a)(b)"}, {"pages_per_quiz": 0}, {"pages_per_quiz": "2"},
                    {"pages_per_quiz": 21}, {"band": "left"}, {"band": []}, {"preset": {}}, {"auto_rotate": "yes"}, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                settings(bad)

    def test_custom_pattern_cannot_hold_the_worker_indefinitely(self):
        chosen = settings({'preset': 'custom', 'pattern': r'(a+)+$'})
        with patch.object(version_sort, 'PATTERN_SECONDS', .2), self.assertRaisesRegex(ValueError, 'matching limit'):
            decide([{'text': 'a' * 1500 + '!'}], chosen)

    def test_sorted_labels_with_the_same_filename_stay_separate(self):
        job = self.intake(self.pdf(2))
        response = self.request(job, preset='custom', pattern=r'Version (.+)', pages_per_quiz=1)
        self.assertEqual(response.status_code, 202, response.json)
        self.run_all(['Version A B', 'Version A-B'])
        latest = self.review(job)['version_sort']['latest']
        self.assertEqual(latest['status'], 'complete', latest)
        outputs = latest['result']['outputs']
        self.assertEqual([item['key'] for item in outputs], ['A B', 'A-B'])
        self.assertEqual(len({item['artifact_id'] for item in outputs}), 2)
        self.assertEqual(len({item['filename'] for item in outputs}), 2)
        for item in outputs:
            self.assertEqual(len(PdfReader(io.BytesIO(self.inbox.read_artifact(item['artifact_id'])[1])).pages), 1)

    def test_version_group_larger_than_one_merge_keeps_every_page(self):
        writer = PdfWriter()
        writer.add_page(LETTER)
        buffer = io.BytesIO()
        writer.write(buffer)
        content = buffer.getvalue()
        result = process.merge_pages([(content, 0)] * 100 + [(content, 180)])
        pages = PdfReader(io.BytesIO(result)).pages
        self.assertEqual(len(pages), 101)
        self.assertEqual(pages[-1].rotation, 180)

    def test_two_sided_quizzes_sort_into_one_pdf_per_version(self):
        job = self.intake(self.pdf(6, blanks={2, 6}))
        before = self.review(job)["pages"]
        self.assertEqual([p["status"] for p in before], ["unreadable", "blank", "unreadable", "unreadable", "unreadable", "blank"])
        self.assertTrue(self.review(job)["version_sort"]["can_request"])
        response = self.request(job)
        self.assertEqual(response.status_code, 202)
        latest = response.json["version_sort"]["latest"]
        self.assertEqual((latest["status"], latest["attempt"], latest["pages_total"]), ("pending", 1, 6))
        self.assertEqual(latest["reads"], {"complete": 2, "pending": 4, "running": 0, "failed": 0})  # blank backs skip OCR
        self.assertEqual(response.json["version_sort"]["queue"]["position"], 1)
        self.assertFalse(response.json["version_sort"]["can_request"])
        bands, turns = self.run_all(["Name: ____   Version A", "VERSION: B", "Question 3 continued", "Version A"], calls=4)
        self.assertEqual(bands, ["top"] * 4)
        self.assertEqual(turns, 4)  # one page per worker turn; the last turn also built the PDFs
        self.assertFalse(self.sorter.process_one())
        item = self.review(job); latest = item["version_sort"]["latest"]
        self.assertEqual(latest["status"], "complete")
        outputs = latest["result"]["outputs"]
        self.assertEqual([(o["label"], o["pages"], o["labeled_pages"]) for o in outputs], [("Version A", 4, 2), ("Version B", 2, 1)])
        self.assertEqual(latest["result"]["unsorted_pages"], 0)
        self.assertEqual([(p["attachment_page"], p["assigned"]) for p in latest["result"]["pages"]],
                         [(1, "A"), (2, "A"), (3, "B"), (4, "B"), (5, "A"), (6, "A")])
        for output, expected in zip(outputs, ([1, 2, 5, 6], [3, 4])):
            name, content = self.inbox.read_artifact(output["artifact_id"])
            self.assertEqual(name, output["filename"])
            self.assertEqual(len(PdfReader(io.BytesIO(content)).pages), len(expected))
        self.assertEqual({o["filename"] for o in outputs}, {"sorted-version-A-attempt-1.pdf", "sorted-version-B-attempt-1.pdf"})
        report = json.loads(self.inbox.read_artifact(latest["result"]["report_artifact_id"])[1])
        self.assertEqual(report["attempt"], 1)
        self.assertEqual(report["pages"][0]["reads"][0]["text"], "Name: ____   Version A")
        self.assertEqual(report["pages"][1]["outcome"], "blank")
        self.assertTrue(report["instructor_review_required"])
        self.assertEqual({o["artifact_id"] for o in report["outputs"]}, {o["artifact_id"] for o in outputs})
        # No identity, gate or job status changed; originals and observations are untouched.
        after = self.review(job)["pages"]
        for previous, current in zip(before, after):
            for key in ("status", "page_stamp_id", "review_state", "content_sha256", "candidates_json", "page_artifact_id"):
                self.assertEqual(previous[key], current[key])
        self.assertTrue(item["instructor_review_required"])
        refreshed = self.inbox.get(job["id"])
        self.assertEqual(refreshed["status"], "needs_review")
        self.assertTrue(refreshed["instructor_review_required"])
        kinds = [event["kind"] for event in refreshed["events"]]
        self.assertIn("version_sort_requested", kinds); self.assertIn("version_sort_complete", kinds)
        sorted_files = [a["filename"] for a in refreshed["artifacts"] if a["filename"].startswith(("sorted-", "version-sort-report-"))]
        self.assertEqual(len(sorted_files), 3)
        self.assertEqual(self.storage.backup_all()["errors"], [])
        self.assertEqual(len(PdfReader(io.BytesIO(self.inbox.read_artifact(outputs[0]["artifact_id"])[1])).pages), 4)
        # A second request is a new attempt with its own files.
        self.assertTrue(self.review(job)["version_sort"]["can_request"])
        self.assertEqual(self.request(job, preset="custom", pattern=r"quiz\s*([A-D])", band="bottom").status_code, 202)
        bands, _ = self.run_all(["quiz C", "nothing", "nothing", "QUIZ D"], calls=4)
        self.assertEqual(bands, ["bottom"] * 4)
        latest = self.review(job)["version_sort"]["latest"]
        self.assertEqual(latest["attempt"], 2)
        self.assertEqual([(o["label"], o["pages"]) for o in latest["result"]["outputs"]], [("C", 2), ("D", 2), ("Unsorted", 2)])
        self.assertEqual({o["filename"] for o in latest["result"]["outputs"]},
                         {"sorted-version-C-attempt-2.pdf", "sorted-version-D-attempt-2.pdf", "sorted-unsorted-attempt-2.pdf"})

    def test_upside_down_sheets_are_turned_with_their_backs(self):
        job = self.intake(self.pdf(7, blanks={4}))
        self.assertEqual(self.request(job).status_code, 202)
        # Quiz A fed upside down (label read at 180) with a content back; quiz B upright with a blank back;
        # then a stray page with no front whose text reads clearly at 180, and one with no readable text.
        self.run_all([("Version A", 180), ("Question 3 continued on the back", 180), "Version B",
                      ("Question 2 continued: the derivative of x^2 is 2x", 180), ("12", 0), ("34", 0)], calls=6)
        latest = self.review(job)["version_sort"]["latest"]
        self.assertEqual(latest["status"], "complete")
        self.assertEqual([(p["attachment_page"], p["assigned"], p["rotation"], p["orientation"]) for p in latest["result"]["pages"]],
                         [(1, "A", 180, "label"), (2, "A", 180, "quiz"), (3, "B", 0, "label"), (4, "B", 0, "quiz"),
                          (5, None, 180, "text"), (6, None, 0, "unknown"), (7, None, 0, "unknown")])
        self.assertEqual(latest["result"]["rotated_pages"], 3)
        outputs = {o["label"]: o for o in latest["result"]["outputs"]}
        self.assertEqual((outputs["Version A"]["rotated_pages"], outputs["Version B"]["rotated_pages"], outputs["Unsorted"]["rotated_pages"]), (2, 0, 1))
        for label, expected in (("Version A", [180, 180]), ("Version B", [0, 0]), ("Unsorted", [180, 0, 0])):
            reader = PdfReader(io.BytesIO(self.inbox.read_artifact(outputs[label]["artifact_id"])[1]))
            self.assertEqual([page.rotation for page in reader.pages], expected, label)
        # The retained single pages are untouched; only the working copy carries the rotation.
        for page in self.review(job)["pages"]:
            self.assertEqual(PdfReader(io.BytesIO(self.inbox.read_artifact(page["page_artifact_id"])[1])).pages[0].rotation, 0)
        report = json.loads(self.inbox.read_artifact(latest["result"]["report_artifact_id"])[1])
        self.assertEqual(report["pages"][0]["orientation"], "label")
        # Auto-rotate off leaves every page as scanned.
        self.assertEqual(self.request(job, auto_rotate=False).status_code, 202)
        self.run_all([("Version A", 180), ("Question 3 continued on the back", 180), "Version B",
                      ("Question 2 continued: the derivative of x^2 is 2x", 180), ("12", 0), ("34", 0)], calls=6)
        latest = self.review(job)["version_sort"]["latest"]
        self.assertEqual({p["orientation"] for p in latest["result"]["pages"]}, {"off"})
        self.assertEqual(latest["result"]["rotated_pages"], 0)
        reader = PdfReader(io.BytesIO(self.inbox.read_artifact(latest["result"]["outputs"][0]["artifact_id"])[1]))
        self.assertEqual([page.rotation for page in reader.pages], [0, 0])

    def test_unlabeled_leading_page_conflict_and_extra_pages_stay_unsorted(self):
        job = self.intake(self.pdf(5))
        self.assertEqual(self.request(job).status_code, 202)
        self.run_all(["no label here", "Version A", "back of A", "an extra page", "Version A ... Version B"], calls=5)
        latest = self.review(job)["version_sort"]["latest"]
        self.assertEqual(latest["status"], "complete")
        pages = latest["result"]["pages"]
        self.assertEqual([(p["attachment_page"], p["outcome"], p["assigned"]) for p in pages],
                         [(1, "none", None), (2, "label", "A"), (3, "none", "A"), (4, "none", None), (5, "conflict", None)])
        self.assertIn("no labeled page precedes", pages[0]["reason"])
        self.assertIn("Different labels", pages[4]["reason"])
        self.assertEqual([(o["label"], o["pages"]) for o in latest["result"]["outputs"]], [("Version A", 2), ("Unsorted", 3)])
        unsorted = [o for o in latest["result"]["outputs"] if not o["key"]][0]
        self.assertEqual(len(PdfReader(io.BytesIO(self.inbox.read_artifact(unsorted["artifact_id"])[1])).pages), 3)

    def test_request_gates(self):
        job = self.intake(self.pdf(2))
        route = f"/api/documents/messages/{job['message_id']}/version-sort"
        self.assertEqual(self.app.test_client().post(route, json={}).status_code, 403)  # same-origin write check
        for bad in ({"preset": "nope"}, {"preset": "custom", "pattern": "version"}, {"pages_per_quiz": 0}, {"band": "side"}):
            with self.subTest(bad=bad):
                self.assertEqual(self.request(job, **bad).status_code, 400)
        with self.assertRaises(Forbidden):
            self.sorter.request(self.intake(self.pdf(1), authenticated=False, inspect=False)["message_id"], {})
        with self.assertRaises(Conflict):  # pages not inspected yet
            self.sorter.request(self.intake(self.pdf(1), inspect=False)["message_id"], {})
        with patch.object(self.sorter, "runtime_available", return_value=False):
            self.assertEqual(self.request(job).status_code, 409)
            self.assertFalse(self.review(job)["version_sort"]["runtime_available"])
        self.assertEqual(self.request(job).status_code, 202)
        duplicate = self.request(job)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("already queued", duplicate.json["error"] if isinstance(duplicate.json, dict) else duplicate.get_data(as_text=True))
        other = self.intake(self.pdf(1))
        with patch.object(version_sort, "MAX_QUEUED", 1):
            self.assertEqual(self.request(other).status_code, 409)
            self.assertFalse(self.review(other)["version_sort"]["can_request"])
        self.assertEqual(self.request(other).status_code, 202)
        self.assertEqual(self.review(other)["version_sort"]["queue"], {"live": 2, "capacity": version_sort.MAX_QUEUED, "position": 2})
        self.assertEqual(self.review(job)["version_sort"]["queue"]["position"], 1)
        self.run_all(["Version A", "Version B", "Version C"], calls=3)  # oldest request first
        self.assertEqual(self.review(job)["version_sort"]["latest"]["status"], "complete")
        self.assertEqual(self.review(other)["version_sort"]["latest"]["status"], "complete")
        self.assertEqual(self.review(other)["version_sort"]["queue"]["live"], 0)
        with self.assertRaises(Conflict):
            self.sorter.retry(job["message_id"])

    def test_failed_busy_or_interrupted_readings_resume_only_remaining_pages(self):
        job = self.intake(self.pdf(3))
        self.assertEqual(self.request(job).status_code, 202)
        # A page whose OCR fails stops the sort; finished readings are kept.
        calls = []
        def flaky(content, *, band):
            calls.append(band)
            if len(calls) == 2:
                raise RuntimeError("model crashed")
            return self.result(content, "Version A")
        with patch("document_identity.version_sort.recognize_bands", side_effect=flaky):
            self.assertTrue(self.sorter.process_one()); self.assertTrue(self.sorter.process_one())
            self.assertFalse(self.sorter.process_one())
        latest = self.review(job)["version_sort"]["latest"]
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["reads"], {"complete": 1, "pending": 1, "running": 0, "failed": 1})
        self.assertTrue(self.review(job)["version_sort"]["can_retry"])
        self.assertEqual(self.app.test_client().post(f"/api/documents/messages/{job['message_id']}/version-sort/retry", json={}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/documents/messages/{job['message_id']}/version-sort/retry", json={}, headers=HEADERS).status_code, 200)
        # Another explicit OCR call holds the model: the page stays queued, nothing fails.
        with patch("document_identity.version_sort.recognize_bands", side_effect=OcrError("Another local OCR task is running; try again after it completes.")):
            self.assertFalse(self.sorter.process_one())
        latest = self.review(job)["version_sort"]["latest"]
        self.assertEqual((latest["status"], latest["error"]), ("running", ""))
        self.assertEqual(latest["reads"], {"complete": 1, "pending": 2, "running": 0, "failed": 0})
        self.run_all(["Version B", "Version C"], calls=2)  # only the remaining pages are read
        latest = self.review(job)["version_sort"]["latest"]
        self.assertEqual((latest["status"], latest["attempt"]), ("complete", 1))
        self.assertEqual([(o["label"], o["pages"]) for o in latest["result"]["outputs"]], [("Version A", 1), ("Version B", 1), ("Version C", 1)])
        # A worker interrupted mid-page requires an explicit retry.
        other = self.intake(self.pdf(2))
        self.assertEqual(self.request(other).status_code, 202)
        with self.scans.rosters.lock(), connect(self.scans.path) as db:
            db.execute("UPDATE scan_version_reads SET status='running' WHERE message_id=? AND scan_page_id=(SELECT id FROM scan_pages WHERE message_id=? ORDER BY source_page_index LIMIT 1)",
                       (other["message_id"], other["message_id"]))
        with patch("document_identity.version_sort.recognize_bands", side_effect=AssertionError("must not run")):
            self.assertFalse(self.sorter.process_one())
        latest = self.review(other)["version_sort"]["latest"]
        self.assertEqual(latest["status"], "failed"); self.assertIn("interrupted", latest["error"])
        self.assertIn("version_sort_failed", [e["kind"] for e in self.inbox.get(other["id"])["events"]])
        self.sorter.retry(other["message_id"])
        self.run_all(["Version A", "Version A"], calls=2)
        self.assertEqual(self.review(other)["version_sort"]["latest"]["status"], "complete")

    def test_tampered_source_and_immutable_records(self):
        job = self.intake(self.pdf(1))
        self.assertEqual(self.request(job).status_code, 202)
        with patch.object(self.inbox, "read_artifact", return_value=("page.pdf", b"tampered")), \
             patch("document_identity.version_sort.recognize_bands", side_effect=AssertionError("must not run")):
            self.assertTrue(self.sorter.process_one())
        self.assertEqual(self.review(job)["version_sort"]["latest"]["status"], "failed")
        self.sorter.retry(job["message_id"])
        self.run_all(["Version A"], calls=1)
        self.assertEqual(self.review(job)["version_sort"]["latest"]["status"], "complete")
        with connect(self.scans.path) as db:
            for sql in ("UPDATE scan_version_sorts SET result_json='{}'", "DELETE FROM scan_version_sorts",
                        "UPDATE scan_version_reads SET label='Z'", "DELETE FROM scan_version_reads",
                        "UPDATE scan_version_sorts SET settings_json='{}'"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)


if __name__ == "__main__":
    unittest.main()
