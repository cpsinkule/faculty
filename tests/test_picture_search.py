"""PICTURE-SEARCH (requirement, 2026-10-10): scanned pages are embedded one a turn by the Inbox worker,
kept as vectors in a rebuildable index, and found by describing them; a deleted scan and a page set aside never come back, a
result is a route and never bytes, and the Assistant shows a found page only through fetch_files.

A stand-in embedder throughout: no model is loaded and the live worker is never reached."""
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from faculty_embedding import client as embedding
from picture_search import service as picture_service, sources as picture_sources
from picture_search.index import BLANK, NO_WORDS, PictureIndex
from rosters import connect
import test_assistant_files as base

DIMENSION = 16
ROOT = Path(__file__).resolve().parents[1]
PHOTO = "/api/student-photos/11111111-1111-4111-8111-111111111111"  # a student ID photo's route (PHOTOS: never embedded)


def unit(position):
    vector = np.zeros(DIMENSION, dtype=np.float32)
    vector[position % DIMENSION] = 1
    return vector


class IndexGenerationTests(unittest.TestCase):
    def test_matrix_refreshes_after_another_process_reuses_a_rowid(self):
        """PICTURE-SEARCH: replacing the last row cannot retain an obsolete vector."""
        with tempfile.TemporaryDirectory() as directory:
            reader, writer = PictureIndex(directory), PictureIndex(directory)
            for add, read in ((writer.add, reader.matrix), (writer.set_text, reader.text_matrix)):
                add('scan', 'old', 'old-hash', 'model', unit(0))
                self.assertEqual(read('model')[0], [('scan', 'old')])
                writer.drop('scan', ['old'])
                add('scan', 'new', 'new-hash', 'model', unit(1))
                refs, vectors = read('model')
                self.assertEqual(refs, [('scan', 'new')])
                np.testing.assert_array_equal(vectors, [unit(1)])


class StandIn:
    """Pictures and words land on one coordinate each: a fixture page of shade 180+n on coordinate n, and "page n" beside it.
    `refuse` names shades the worker will not take; `down` makes it fall silent; `lent` makes it say the GPU is lent."""
    def __init__(self):
        self.pictures, self.asked, self.refuse, self.down, self.status_down, self.lent = [], [], set(), False, False, False
        self.other = False  # a worker started before the picture setting changed

    def picture_vectors(self, pictures, timeout=20, path=None):
        self.pictures.append(list(pictures))
        if self.lent:
            raise embedding.CardLent("The GPU is lent to the local grading model; ask again shortly.")
        if self.other:
            raise embedding.OtherSetting("The embedding worker sees pictures at another setting. Restart it.")
        if self.down:
            raise embedding.EmbeddingUnavailable("The embedding worker did not answer: timeout")
        shades = [Image.open(io.BytesIO(data)).convert("L").getpixel((0, 0)) for data in pictures]
        if self.refuse & set(shades):
            raise embedding.PictureRefused("A picture could not be read.")
        return np.stack([unit(shade - 180) for shade in shades])

    def text_vectors(self, texts, role="query", timeout=1.5, path=None):
        self.asked.append((role, list(texts)))
        if self.down:
            raise embedding.EmbeddingUnavailable("The embedding worker did not answer: timeout")
        rows = []
        for text in texts:
            digits = "".join(c for c in text if c.isdigit())
            row = unit(int(digits)) if digits else np.full(DIMENSION, DIMENSION ** -0.5, dtype=np.float32)
            rows.append(row)
        return np.stack(rows)

    def status(self, timeout=1.5, path=None):
        if self.status_down:
            raise embedding.EmbeddingUnavailable("The embedding worker did not answer: timeout")
        return {"device": "test"}


class PictureSearchTests(base.Fixture):
    tool = base.StudentPaper.tool

    def scan(self, numbers, copy=None):
        """A finished scan whose page n has shade 180+n. Pages 1 and 2 carry the stamps of Alice's Exam 1 (or of `copy`); later ones
        no label."""
        job = self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": b"%PDF-fixture " + uuid.uuid4().bytes}],
                                source="email", sender="scanner", original=b"retained email", idempotency_key=str(uuid.uuid4()), authenticated=True,
                                instructor_review_required=True)
        source = next(a for a in job["artifacts"] if a["role"] == "input")
        with connect(self.path) as db:
            stamps = {p["page_number"]: dict(p) for p in db.execute("SELECT * FROM page_stamps WHERE printed_copy_id=?", ((copy or self.copy)["id"],))}
            db.execute("UPDATE inbox_messages SET instructor_review_required=0 WHERE id=?", (job["message_id"],))
        for index, number in enumerate(numbers, 1):
            self.scans._retain_page(job, source, {"index": index, "candidates": [{"payload": stamps[number]["token"]}] if number in stamps else [],
                                                  "blank": False, "pdf": b"%PDF page " + uuid.uuid4().bytes, "png": base.png(850, 1100, 180 + number),
                                                  "decoder": {}})
        with connect(self.path) as db:  # the worker has finished with it, so the instructor may delete it
            for table in ("document_decode_jobs", "scan_alias_ocr", "scan_version_sorts", "scan_unlabeled_runs"):
                db.execute(f"UPDATE {table} SET status='complete' WHERE message_id=? AND status IN ('pending','running')", (job["message_id"],))
        return job

    def setUp(self):
        super().setUp()
        self.search = self.app.extensions["picture_search"]
        self.model = StandIn()
        for name in ("picture_vectors", "text_vectors", "status"):
            patcher = patch.object(embedding, name, getattr(self.model, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.configured = patch.object(embedding, "configured", return_value=True)
        self.configured.start()
        self.addCleanup(self.configured.stop)
        batch = self.batch()
        self.copy = next(c for c in batch["copies"] if c["enrollment_id"] == self.alice["enrollment_id"])
        self.other = next(c for c in batch["copies"] if c["enrollment_id"] != self.alice["enrollment_id"])

    def index_all(self):
        turns = 0
        while self.search.process_one():
            turns += 1
        return turns

    def find(self, text, **query):
        return self.client.get("/api/picture-search", query_string={"q": text, **query})

    def test_archived_scans_leave_search_even_with_retained_vectors(self):
        """PICTURE-SEARCH: current catalog visibility applies to every cached hit."""
        job = self.scan([1, 2])
        self.index_all()
        self.assertTrue(self.find('page 1').json['results'])
        with connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET storage_status='archived' WHERE id=?", (job['message_id'],))
        self.assertEqual(self.find('page 1').json['results'], [])
        source = picture_sources.Scans(self.inbox)
        with connect(self.path) as db:
            self.assertEqual(source.units(db), [])
            self.assertEqual(source.gathered(db), {})
            self.assertEqual(source.same(db), {})

    def test_pages_are_embedded_one_a_turn_and_found_by_describing_them(self):
        job = self.scan([1, 2, 3])
        self.assertEqual(self.search.status()["waiting"], 3)
        self.assertTrue(self.search.process_one())
        self.assertEqual(self.search.status()["indexed"], 1)
        self.assertEqual(self.index_all(), 2)
        self.assertFalse(self.search.process_one())  # nothing waits: the turn costs nothing
        self.assertTrue(all(len(sent) == 1 for sent in self.model.pictures), "one picture a request, so text lookups never wait behind a batch")
        self.assertEqual(len(self.model.pictures), 3)

        found = self.find("page 2")
        self.assertEqual(found.status_code, 200, found.json)
        self.assertEqual(self.model.asked[-1], ("query", ["page 2"]))
        first = found.json["results"][0]
        self.assertEqual(first["label"], "Exam 1, page 2")
        self.assertEqual(first["link"], f"#inbox/job/{job['id']}/pages")
        self.assertIn("scanned 20", first["where"])
        self.assertEqual(found.json["searched"], 3)
        self.assertEqual(sorted(hit["label"] for hit in found.json["results"]), ["Exam 1, page 1", "Exam 1, page 2", "Scan page 3"])
        self.assertGreater(first["score"], found.json["results"][1]["score"])
        picture = self.client.get(first["image"])
        self.assertEqual((picture.status_code, picture.mimetype), (200, "image/png"))
        self.assertEqual(Image.open(io.BytesIO(picture.data)).convert("L").getpixel((0, 0)), 182)
        self.assertTrue(first["open"].endswith("image.png?width=1700"))
        # A result is where a picture is: no bytes, no hash, and never whose page it is.
        text = json.dumps(found.json)
        for absent in ("sha256", "Alice", "Example", self.alice["enrollment_id"], "base64"):
            self.assertNotIn(absent, text)
        self.assertEqual(len(self.find("page 2", limit="1").json["results"]), 1)

    def test_a_scanned_page_is_found_through_the_page_it_was_printed_from_as_one_result_with_its_count(self):
        # The blank copy of Exam 1 says what each page prints; its words (the
        # stand-in puts "Exam 1 A, page 2 ..." on coordinate 12) are what a scan of that page is found by.
        def blank(files, entrypoint=None):
            return {"pdf": b"Synthetic reference PDF", "sources": files, "conversion": {"engine": "synthetic-converter", "dpi": 300},
                    "pages": [{"number": number, "width_pt": 612, "height_pt": 792, "width_px": 612, "height_px": 792,
                               "text": "Graph the inequalities." if number == 2 else "Multiply the matrices.", "png": base.png(612, 792, 184 + number)}
                              for number in (1, 2)]}
        with connect(self.path) as db:
            assignment = db.execute("SELECT id FROM assignments WHERE title='Exam 1'").fetchone()["id"]
        with patch("document_identity.template_conversion.convert_template", side_effect=blank):
            self.app.extensions["assignment_templates"].upload(assignment_id=assignment, label="A", request_id=str(uuid.uuid4()), files={"exam.pdf": b"A"})
        self.scan([1, 2])
        second = self.scan([2], copy=self.other)  # another student's copy of page 2
        self.index_all()

        found = self.find("topic 12").json["results"]
        # The printed page, once, with its two scans. Page 1 and its scan are not guessed at: their words say something else.
        self.assertEqual([(hit["kind"], hit["label"], hit["matched"], hit["score"]) for hit in found], [("Printed page", "Exam 1, page 2", "words", 1.0)])
        self.assertEqual(found[0]["scans"], {"count": 2, "link": f"#inbox/job/{second['id']}/pages"})
        self.assertNotIn("same", found[0])
        # Asked for scans alone, a scan stands for its page, with the page's match.
        only = self.find("topic 12", sources="scan").json["results"]
        self.assertEqual([(hit["source"], hit["label"], hit["matched"], hit["score"]) for hit in only], [("scan", "Exam 1, page 2", "words", 1.0)])
        self.assertEqual(only[0]["scans"]["count"], 2)
        # No words match at all: everything is matched by look, and a printed page with its scans is still one result.
        look = self.find("page 2").json["results"]
        self.assertEqual(sorted(hit["label"] for hit in look), ["Exam 1, page 1", "Exam 1, page 2"])
        self.assertEqual((look[0]["source"], look[0]["label"], look[0]["matched"], look[0]["scans"]["count"]), ("scan", "Exam 1, page 2", "look", 2))
        # A scan deleted in the Inbox is not counted.
        current = self.inbox.get(second["id"])
        self.inbox.remove(second["id"], current["version"], "delete", "wrong stack")
        self.assertEqual(self.find("topic 12").json["results"][0]["scans"]["count"], 1)

    def test_a_picture_finds_the_pages_that_look_like_it_and_is_kept_nowhere(self):
        from test_course_notes import notes_pdf
        from test_storage import HEADERS
        job = self.scan([1, 2, 3])
        self.index_all()
        first = self.find("page 2").json["results"][0]
        before = (self.search.status()["indexed"], len(self.model.asked))
        # By a file Faculty already serves: the scanned page itself is its own nearest.
        found = self.client.get("/api/picture-search/like", query_string={"route": first["image"]})
        self.assertEqual(found.status_code, 200, found.json)
        self.assertEqual([(hit["label"], hit["matched"], hit["score"]) for hit in found.json["results"]][0], ("Exam 1, page 2", "look", 1.0))
        self.assertEqual((found.json["searched"], len(found.json["results"])), (3, 3))
        self.assertEqual(found.json["results"][0]["link"], f"#inbox/job/{job['id']}/pages")
        # By a picture the instructor drops in.
        sent = self.client.post("/api/picture-search/like", data={"file": (io.BytesIO(base.png(400, 500, 183)), "photo.png")}, headers=HEADERS,
                                query_string={"limit": "1", "sources": "scan"})
        self.assertEqual(sent.status_code, 200, sent.json)
        self.assertEqual([hit["label"] for hit in sent.json["results"]], ["Scan page 3"])
        # A PDF is searched by one of its pages, drawn in the document sandbox.
        pdf = notes_pdf([["One"], ["Two"]], [])
        self.assertEqual(self.client.post("/api/picture-search/like", data={"file": (io.BytesIO(pdf), "two.pdf")}, headers=HEADERS,
                                          query_string={"page": "2"}).status_code, 200)
        past = self.client.post("/api/picture-search/like", data={"file": (io.BytesIO(pdf), "two.pdf")}, headers=HEADERS, query_string={"page": "3"})
        self.assertEqual((past.status_code, past.json["error"]), (400, "This PDF has 2 pages."))
        # Nothing of it is kept, and no words are asked about it.
        self.assertEqual((self.search.status()["indexed"], len(self.model.asked)), before)
        for refused, why in ((self.client.post("/api/picture-search/like", data={"file": (io.BytesIO(b"not a picture"), "x.txt")}, headers=HEADERS), "not a picture"),
                             (self.client.post("/api/picture-search/like", data={"note": "no file"}, headers=HEADERS, content_type="multipart/form-data"), "Choose a picture"),
                             (self.client.get("/api/picture-search/like", query_string={"route": "/api/state"}), "not a file"),
                             (self.client.get("/api/picture-search/like", query_string={"route": first["image"], "limit": "x"}), "whole numbers"),
                             (self.client.get("/api/picture-search/like", query_string={"route": PHOTO}), "never embedded")):
            self.assertEqual(refused.status_code, 400, refused.json)
            self.assertIn(why, refused.json["error"])
        self.assertEqual(self.client.post("/api/picture-search/like", json={"file": "x"}, headers=HEADERS).status_code, 415)
        self.model.down = True
        self.assertEqual(self.client.get("/api/picture-search/like", query_string={"route": first["image"]}).status_code, 503)
        routes = {r["endpoint"]: r["assistant"] for r in self.app.extensions["assistant"].route_catalog()["routes"]}
        self.assertEqual((routes["picture_search.like_route"], routes["picture_search.like_upload"]), ("read", "never"))

    def test_a_scan_deleted_in_the_inbox_is_never_read_or_returned_until_it_is_restored(self):
        self.scan([1])
        self.index_all()
        gone = self.scan([2])
        current = self.inbox.get(gone["id"])
        self.inbox.remove(gone["id"], current["version"], "delete", "wrong stack")
        self.assertEqual(self.search.status()["waiting"], 0)  # INBOX `PRESENT`: a deleted scan's page is never read
        self.assertFalse(self.search.process_one())
        self.assertEqual([hit["label"] for hit in self.find("page 2").json["results"]], ["Exam 1, page 1"])

        self.inbox.restore(gone["id"])
        self.search._wait_until = 0
        self.assertEqual(self.index_all(), 1)
        self.assertEqual(self.find("page 2").json["results"][0]["label"], "Exam 1, page 2")
        # Deleted after it was indexed: its vector stays in the cache, and the search leaves it out from the catalog.
        current = self.inbox.get(gone["id"])
        self.inbox.remove(gone["id"], current["version"], "delete", "wrong stack")
        self.assertEqual([hit["label"] for hit in self.find("page 2").json["results"]], ["Exam 1, page 1"])
        self.assertEqual(self.search.status()["indexed"], 2)

    def test_pages_set_aside_in_review_and_blank_sides_are_left_out(self):
        self.scan([1, 2, 3, 4])
        with connect(self.path) as db:
            pages = {row["source_page_index"]: row["id"] for row in db.execute("SELECT id, source_page_index FROM scan_pages")}
            db.execute("UPDATE scan_pages SET status='blank' WHERE id=?", (pages[4],))
        self.assertEqual(self.index_all(), 3)  # a blank side is never embedded
        for state in picture_sources.HIDDEN_REVIEW:
            with connect(self.path) as db:
                db.execute("UPDATE scan_pages SET review_state='selected'")
                db.execute("UPDATE scan_pages SET review_state=? WHERE id=?", (state, pages[2]))
            self.assertEqual(sorted(hit["label"] for hit in self.find("page 2").json["results"]), ["Exam 1, page 1", "Scan page 3"], state)
        with connect(self.path) as db:
            db.execute("UPDATE scan_pages SET review_state='selected'")
        self.assertEqual(self.find("page 2").json["results"][0]["label"], "Exam 1, page 2")
        with connect(self.path) as db:
            db.execute("UPDATE scan_pages SET status='blank' WHERE id=?", (pages[2],))  # found blank after it was indexed
        self.assertNotIn("Exam 1, page 2", [hit["label"] for hit in self.find("page 2").json["results"]])

    def test_archived_scans_wait_and_an_unstamped_page_is_named_by_its_place_in_the_scan(self):
        self.scan([1])
        with connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET storage_status='archived'")
        self.assertEqual(self.search.status()["waiting"], 0)  # its files are on the backup drive: not fetched for an index
        with connect(self.path) as db:
            db.execute("UPDATE inbox_messages SET storage_status='active'")
            db.execute("UPDATE scan_pages SET page_stamp_id=NULL, status='unknown'")
        self.search._wait_until = 0
        self.assertEqual(self.index_all(), 1)
        self.assertEqual(self.find("page 1").json["results"][0]["label"], "Scan page 1")

    def test_a_page_the_worker_refuses_is_set_aside_and_a_silent_worker_sets_nothing_aside(self):
        self.scan([1, 2])
        self.model.refuse = {182}
        self.assertEqual(self.index_all(), 2)  # the refused page took its turn and was set aside; the other was embedded
        self.assertEqual((self.search.status()["indexed"], self.search.status()["unreadable"], self.search.status()["waiting"]), (1, 1, 0))

        self.scan([3])
        self.search._wait_until = 0
        self.model.down, self.model.status_down = True, True
        for _ in range(picture_service.TRIES + 2):  # the worker is down: wait, and blame no page
            self.search._wait_until = 0
            self.assertFalse(self.search.process_one())
        self.assertGreater(self.search._wait_until, 0)
        self.assertEqual((self.search.status()["unreadable"], self.search.status()["waiting"]), (1, 1))
        before = len(self.model.pictures)
        self.assertFalse(self.search.process_one())  # and it is not asked again until the wait is over
        self.assertEqual(len(self.model.pictures), before)

        # The worker answers a status request yet never this picture: after a few tries that page alone is set aside.
        self.model.status_down = False
        self.search._failed = {}
        results = []
        for _ in range(picture_service.TRIES):
            self.search._wait_until = 0
            results.append(self.search.process_one())
        self.assertEqual(results, [False] * (picture_service.TRIES - 1) + [True])
        self.assertEqual((self.search.status()["unreadable"], self.search.status()["waiting"]), (2, 0))

    def test_a_worker_that_has_lent_the_gpu_answers_status_and_still_sets_no_page_aside(self):
        # EMBEDDING "LENDS THE GPU": a loan can outlast its turn (the Inbox worker stopped midway), and the worker answers a
        # status request all through it, which is how a page that silences the worker looks.
        self.scan([1])
        self.model.lent = True
        for _ in range(picture_service.TRIES + 2):
            self.search._wait_until = 0
            self.assertFalse(self.search.process_one())
            self.assertGreater(self.search._wait_until, 0)  # a wait each time
        self.assertEqual((self.search._failed, self.search.status()["unreadable"], self.search.status()["waiting"]), ({}, 0, 1))
        self.model.lent = False
        self.search._wait_until = 0
        self.assertEqual(self.index_all(), 1)  # the card is back: the page is read as if nothing had happened
        self.assertEqual((self.search.status()["indexed"], self.search.status()["unreadable"]), (1, 0))

    def test_a_worker_still_on_another_picture_setting_sets_no_page_aside_and_none_of_its_vectors_is_kept(self):
        # EMBEDDING "PICTURES AT 560 PIECES": a worker started before the setting changed answers a status request too.
        self.scan([1])
        self.model.other = True
        for _ in range(picture_service.TRIES + 2):
            self.search._wait_until = 0
            self.assertFalse(self.search.process_one())
            self.assertGreater(self.search._wait_until, 0)
        self.assertEqual((self.search._failed, self.search.status()["indexed"], self.search.status()["unreadable"], self.search.status()["waiting"]),
                         ({}, 0, 0, 1))
        from test_storage import HEADERS
        sent = self.client.post("/api/picture-search/like", data={"file": (io.BytesIO(base.png(400, 500, 183)), "photo.png")}, headers=HEADERS)
        self.assertEqual((sent.status_code, sent.json["unavailable"]), (503, True))  # a search by a picture says so, and compares nothing
        self.model.other = False
        self.search._wait_until = 0
        self.assertEqual(self.index_all(), 1)
        self.assertEqual((self.search.status()["indexed"], self.search.status()["unreadable"]), (1, 0))

    def test_pictures_seen_at_another_setting_are_embedded_again_never_searched_and_then_forgotten(self):
        # The index of the day before: every picture's vector made at the setting of then, under the model's bare mark.
        self.scan([1, 2])
        with connect(self.path) as db:
            pages = {row["source_page_index"]: dict(row) for row in db.execute(
                "SELECT sp.*, a.sha256 FROM scan_pages sp JOIN inbox_artifacts a ON a.id=sp.raster_artifact_id")}
        index = self.search.index
        for number, page in pages.items():
            index.add("scan", page["id"], page["sha256"], picture_service.MODEL, unit(40 + number))
        index.skip("scan", "an-empty-page", "e" * 64, picture_service.MODEL, BLANK)
        self.assertNotEqual(picture_service.PICTURES, picture_service.MODEL)
        self.assertEqual((self.search.status()["indexed"], self.search.status()["waiting"]), (0, 2))  # all of it waits again
        self.assertEqual(self.find("page 41").json["searched"], 0)  # and what was made at the other setting is never searched
        self.assertTrue(self.search.process_one())
        with sqlite3.connect(index.path) as db:  # until everything is in, the old vectors stay where they are
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pictures WHERE model=?", (picture_service.MODEL,)).fetchone()[0], 2)
        self.index_all()
        self.assertEqual((self.search.status()["indexed"], self.search.status()["waiting"]), (2, 0))
        self.search._wait_until = self.search._tidy_after = 0
        self.assertFalse(self.search.process_one())  # nothing waits: the idle turn forgets what the other setting made
        with sqlite3.connect(index.path) as db:
            self.assertEqual({row[0] for row in db.execute("SELECT model FROM pictures")}, {picture_service.PICTURES})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pictures").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM skipped WHERE ref='an-empty-page'").fetchone()[0], 0)
        self.assertEqual(self.find("page 1").json["results"][0]["label"], "Exam 1, page 1")

    def test_a_page_drawn_again_is_embedded_again_and_a_page_that_is_gone_leaves_the_index(self):
        self.scan([1, 2])
        self.index_all()
        with connect(self.path) as db:
            pages = {row["source_page_index"]: dict(row) for row in db.execute("SELECT * FROM scan_pages")}
            # The scan was started over and its first page drawn again: the same page id, another picture.
            db.execute("UPDATE inbox_artifacts SET sha256=? WHERE id=?", ("f" * 64, pages[1]["raster_artifact_id"]))
        self.search._wait_until = 0
        self.assertEqual(self.search.status()["waiting"], 1)
        with patch.object(self.inbox, "read_artifact", return_value=("page.png", base.png(850, 1100, 187))):
            self.assertEqual(self.index_all(), 1)
        self.assertEqual((self.search.status()["indexed"], self.search.status()["waiting"]), (2, 0))
        self.assertEqual(self.find("page 7").json["results"][0]["label"], "Exam 1, page 1")  # found by what it shows now

        with connect(self.path) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM scan_pages WHERE id=?", (pages[2]["id"],))
        self.search._wait_until = self.search._tidy_after = 0
        self.assertFalse(self.search.process_one())  # an idle turn tidies
        self.assertEqual(self.search.status()["indexed"], 1)
        self.assertEqual(self.search.index.refs("scan"), {pages[1]["id"]})

    def test_a_page_whose_file_cannot_be_read_waits_then_is_set_aside_and_is_tried_again_in_a_week(self):
        self.scan([1, 2])
        results = []
        with patch.object(self.inbox, "read_artifact", side_effect=OSError("busy")):
            for _ in range(picture_service.TRIES):
                self.search._wait_until = 0
                results.append(self.search.process_one())
        self.assertEqual(results, [False] * (picture_service.TRIES - 1) + [True])  # a passing fault costs a wait, not the page
        self.assertEqual(self.model.pictures, [])
        self.search._wait_until = 0
        self.assertEqual(self.index_all(), 1)  # the page behind it is not held up
        self.assertEqual((self.search.status()["indexed"], self.search.status()["unreadable"], self.search.status()["waiting"]), (1, 1, 0))
        with sqlite3.connect(self.search.index.path) as db:
            self.assertIn("could not be read: OSError", db.execute("SELECT reason FROM skipped").fetchone()[0])
            db.execute("UPDATE skipped SET at='2026-01-01T00:00:00+00:00'")  # set aside long ago: due another try
        self.search._wait_until = 0
        self.assertEqual(self.search.status()["waiting"], 1)
        self.assertEqual(self.index_all(), 1)
        self.assertEqual((self.search.status()["indexed"], self.search.status()["unreadable"]), (2, 0))

    def test_without_the_embedding_service_nothing_is_asked_and_the_search_says_so(self):
        self.scan([1])
        self.configured.stop()
        try:
            with patch.dict(os.environ, {"FACULTY_EMBEDDING_SOCKET": ""}):
                self.assertFalse(self.search.process_one())
                self.assertEqual(self.model.pictures, [])
                answer = self.find("a graph")
                self.assertEqual(answer.status_code, 503)
                self.assertIn("not set up", answer.json["error"])
                self.assertEqual(self.search.status(), {"available": False, "indexed": 0, "unreadable": 0, "by_source": {}, "waiting": None})
        finally:
            self.configured.start()
        self.index_all()
        self.model.down = True
        answer = self.find("a graph")
        self.assertEqual(answer.status_code, 503)
        self.assertIn("Try again", answer.json["error"])

    def test_a_description_is_required_and_bounded(self):
        for query in ({"q": ""}, {"q": "   "}, {"q": "x" * 501}, {"q": "a graph", "limit": "many"}):
            answer = self.client.get("/api/picture-search", query_string=query)
            self.assertEqual(answer.status_code, 400, query)
            self.assertTrue(answer.json["error"])
        self.assertEqual(self.model.asked, [])
        self.assertEqual(self.find("a graph").json, {"query": "a graph", "results": [], "searched": 0})
        self.assertEqual(self.client.get("/api/picture-search/status").json["indexed"], 0)

    def test_the_inbox_worker_gives_the_index_a_turn(self):
        self.scan([1, 2])
        for _ in range(200):
            if self.search.status()["indexed"] == 2:
                break
            self.inbox.work_once()
        self.assertEqual(self.search.status()["indexed"], 2)

    def test_the_assistant_finds_pages_and_shows_them_only_through_fetch_files(self):
        self.scan([1, 2])
        self.index_all()
        found, event = self.tool("find_pictures", query="page 2")
        self.assertEqual(found["pictures"][0]["label"], "Exam 1, page 2")
        self.assertIn("not confirmed", found["next"])
        self.assertNotIn("files", event)  # finding shows nothing
        self.assertNotIn("pictures", event)
        answer, event = self.tool("fetch_files", paths=[found["pictures"][0]["image"]], look=True)
        self.assertEqual([f["kind"] for f in event["files"]], ["image"])
        self.assertEqual(len(event["pictures"]), 1)
        # By a picture instead of words: the route of a file Faculty serves.
        alike = self.tool("find_pictures", like=found["pictures"][0]["image"], limit=1)[0]
        self.assertEqual((alike["like"], [(seen["label"], seen["matched"], seen["score"]) for seen in alike["pictures"]]),
                         (found["pictures"][0]["image"], [("Exam 1, page 2", "look", 1.0)]))
        # A file attached in the chat is searched by the route its note gives; it is scratch, and nothing of it is indexed.
        from test_storage import HEADERS
        attached = self.client.post("/api/assistant/uploads", data={"file": (io.BytesIO(base.png(300, 400, 181)), "photo of a page.png")}, headers=HEADERS)
        self.assertEqual(attached.status_code, 201, attached.json)
        by_file = self.tool("find_pictures", like=attached.json["files"][0]["route"], limit=1)[0]
        self.assertEqual([(seen["label"], seen["score"]) for seen in by_file["pictures"]], [("Exam 1, page 1", 1.0)])
        self.assertEqual(self.search.status()["indexed"], 2)
        self.assertIn("never embedded", self.tool("find_pictures", like=PHOTO)[0]["error"])
        self.assertIn("error", self.tool("find_pictures")[0])  # neither words nor a picture
        self.model.down = True
        self.assertIn("error", self.tool("find_pictures", query="page 2")[0])
        registry = __import__("assistant.tools", fromlist=["registry"]).registry()
        self.assertTrue(registry["find_pictures"].read)
        routes = {r["endpoint"]: r["assistant"] for r in self.app.extensions["assistant"].route_catalog()["routes"] if r["method"] == "GET"}
        self.assertEqual(routes["picture_search.search"], "read")


class IndexTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.index = PictureIndex(Path(self.folder.name) / "picture-index")

    def test_the_index_holds_vectors_and_places_and_nothing_else(self):
        self.index.add("scan", "page-1", "a" * 64, "model@1", unit(1))
        self.index.skip("scan", "page-2", "b" * 64, "model@1", "A picture could not be read.")
        self.assertEqual(stat.S_IMODE(self.index.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.index.path.parent.stat().st_mode), 0o700)
        with sqlite3.connect(self.index.path) as db:
            columns = {table: [row[1] for row in db.execute(f"PRAGMA table_info({table})")] for table in ("pictures", "skipped")}
        self.assertEqual(columns["pictures"], ["source", "ref", "sha256", "model", "vector", "indexed_at"])
        self.assertEqual(columns["skipped"], ["source", "ref", "sha256", "model", "reason", "at"])
        self.assertEqual(self.index.done("scan", "model@1"), {"page-1": "a" * 64, "page-2": "b" * 64})
        self.assertEqual(self.index.counts("model@1"), {"indexed": 1, "unreadable": 1, "by_source": {"scan": 1}})

    def test_what_is_set_aside_for_good_is_not_tried_again_and_an_empty_page_is_not_counted_unreadable(self):
        self.index.skip("notes", "page-1", "a" * 64, "model@1", BLANK)
        self.index.skip("notes", "page-2", "b" * 64, "model@1", "A picture could not be read.")
        self.index.skip_text("notes", "page-3", "c" * 64, "model@1", NO_WORDS)
        self.index.skip_text("notes", "page-4", "d" * 64, "model@1", "The embedding worker did not answer for this picture.")
        later = "2999-01-01T00:00:00+00:00"  # every set-aside is older than this: only a passing fault is due another try
        self.assertEqual(self.index.done("notes", "model@1", later), {"page-1": "a" * 64})
        self.assertEqual(self.index.texts_done("notes", "model@1", later), {"page-3": "c" * 64})
        self.assertEqual(self.index.done("notes", "model@1"), {"page-1": "a" * 64, "page-2": "b" * 64})
        self.assertEqual(self.index.counts("model@1"), {"indexed": 0, "unreadable": 1, "by_source": {}})

    def test_what_another_mark_made_is_forgotten_and_what_these_two_made_is_kept(self):
        old, pictures, words = "model@1", "model@1#560", "model@1"
        self.index.add("scan", "page-1", "a" * 64, old, unit(1))
        self.index.add("scan", "page-1", "a" * 64, pictures, unit(2))
        self.index.skip("scan", "page-2", "b" * 64, old, BLANK)
        self.index.skip("scan", "page-3", "c" * 64, pictures, "A picture could not be read.")
        self.index.set_text("notes", "page-4", "d" * 64, words, unit(4))
        self.index.set_text("notes", "page-4", "d" * 64, "model@0", unit(5))
        self.index.skip_text("notes", "page-5", "e" * 64, words, NO_WORDS)
        self.index.skip_text("notes", "page-6", "f" * 64, "model@0", NO_WORDS)
        self.index.keep_only(pictures, words)
        with sqlite3.connect(self.index.path) as db:
            self.assertEqual(db.execute("SELECT source, ref, model FROM pictures").fetchall(), [("scan", "page-1", pictures)])
            self.assertEqual(db.execute("SELECT source, ref, model FROM texts").fetchall(), [("notes", "page-4", words)])
            self.assertEqual(sorted(db.execute("SELECT source, ref, model FROM skipped").fetchall()),
                             [("notes:words", "page-5", words), ("scan", "page-3", pictures)])
        self.assertEqual(float(self.index.matrix(pictures)[1][0, 2]), 1.0)

    def test_the_held_matrix_follows_the_index_and_another_model_s_vectors_are_never_searched(self):
        self.assertEqual(self.index.matrix("model@1")[0], [])
        self.index.add("scan", "page-1", "a" * 64, "model@1", unit(1))
        refs, matrix = self.index.matrix("model@1")
        self.assertEqual((refs, matrix.dtype, matrix.shape), ([("scan", "page-1")], np.float32, (1, DIMENSION)))
        self.assertIs(self.index.matrix("model@1")[1], matrix)  # unchanged index: not read again
        self.index.add("scan", "page-2", "b" * 64, "model@1", unit(2))
        self.assertEqual(len(self.index.matrix("model@1")[0]), 2)
        self.index.add("scan", "page-3", "c" * 64, "model@2", unit(3))
        self.assertEqual(len(self.index.matrix("model@1")[0]), 2)
        self.assertEqual(self.index.done("scan", "model@2"), {"page-3": "c" * 64})  # a new model revision embeds everything again
        self.index.add("scan", "page-1", "d" * 64, "model@1", unit(5))  # drawn again: its row is replaced, and the matrix read again
        refs, matrix = self.index.matrix("model@1")
        self.assertEqual((len(refs), float(matrix[refs.index(("scan", "page-1")), 5])), (2, 1.0))
        self.index.skip("scan", "page-2", "e" * 64, "model@1", "A picture could not be read.")
        self.assertEqual(self.index.counts("model@1"), {"indexed": 1, "unreadable": 1, "by_source": {"scan": 1}})
        self.assertEqual(self.index.matrix("model@1")[0], [("scan", "page-1")])
        self.index.drop("scan", {"page-1", "page-2"})
        self.assertEqual(self.index.counts("model@1"), {"indexed": 0, "unreadable": 0, "by_source": {}})


class ShapeTests(unittest.TestCase):
    def test_every_query_over_scans_applies_present_and_no_picture_or_text_is_written(self):
        source = (ROOT / "picture_search" / "sources.py").read_text()
        # Each search and each choice of what to embed applies `PRESENT`; the tidy's existence check is the named exception.
        self.assertEqual(source.count("FROM scan_pages sp"), source.count('PRESENT.format("sp.message_id")'))
        self.assertEqual((source.count("FROM scan_pages sp"), source.count("FROM scan_pages")), (4, 5))
        self.assertNotIn("scan_pages", (ROOT / "picture_search" / "service.py").read_text())
        for name in ("service.py", "sources.py", "index.py", "api.py"):
            text = (ROOT / "picture_search" / name).read_text()
            for forbidden in ("write_bytes", "write_text", "import requests", "urllib.request", "urlopen", "student_photos", "/tmp"):
                self.assertNotIn(forbidden, text, (name, forbidden))

    def test_the_service_units_name_the_socket_for_both_processes(self):
        for unit_name in ("faculty.service.d", "faculty-inbox.service.d"):
            text = (ROOT / "deploy" / unit_name / "embedding.conf").read_text()
            self.assertIn("Environment=FACULTY_EMBEDDING_SOCKET=/mnt/nvme/Teaching/runtime/embedding/run/embedding.sock", text)


if __name__ == "__main__":
    unittest.main()
