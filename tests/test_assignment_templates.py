"""Template retention, immutable maps, private APIs, and verified owner backups."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

from PIL import Image
from werkzeug.exceptions import Conflict, NotFound

from app import create_app
from course_storage import StorageError
from rosters import connect
from test_storage import HEADERS, temporary_storage


FULL = {"x": 0, "y": 0, "width": 1, "height": 1}


def prepared(files, entrypoint=None):
    image = io.BytesIO()
    Image.new("RGB", (612, 792), "white").save(image, "PNG")
    return {"pdf": b"Synthetic reference PDF", "sources": files,
            "conversion": {"engine": "synthetic-converter", "dpi": 300},
            "pages": [{"number": number, "width_pt": 612, "height_pt": 792, "width_px": 612, "height_px": 792,
                       "text": f"Problem on page {number}", "png": image.getvalue()} for number in (1, 2)]}


def questions():
    return [{"id": "q1", "label": "Explain your method", "max_points": 10, "criteria": [], "feedback": [],
             "regions": [{"page": 1, "region": FULL}, {"page": 2, "region": {"x": .1, "y": .2, "width": .8, "height": .4}}]}]


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.client = self.app.test_client()
        self.service = self.app.extensions["assignment_templates"]
        self.documents = self.app.extensions["documents"]
        course = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Synthetic course", "term": "Fall",
                                  "year": 2026, "sections": [501], "is_current": True}, headers=HEADERS).json
        self.assignment = self.documents.create_assignment(course["definition_id"], "exam", "Exam 1")
        self.converter = self.enterContext(patch("document_identity.template_conversion.convert_template", side_effect=prepared))

    def upload(self, **values):
        return self.service.upload(**{"assignment_id": self.assignment["id"], "label": "Main exam", "request_id": str(uuid.uuid4()),
                                      "files": {"exam.pdf": b"Original bytes"}, **values})

    def test_originals_reference_previews_and_machine_package_have_verified_bytes(self):
        template = self.upload()
        self.assertEqual((template["page_count"], template["version"], template["revision_id"]), (2, 0, None))
        self.assertEqual(template["source_names"], ["exam.pdf"])
        self.assertEqual(self.service.read(template["id"], "pdf"), (b"Synthetic reference PDF", "application/pdf"))
        self.assertTrue(self.service.read(template["id"], "image", 2)[0].startswith(b"\x89PNG"))
        with zipfile.ZipFile(io.BytesIO(self.service.export(template["id"]))) as archive:
            self.assertEqual(archive.read("sources/exam.pdf"), b"Original bytes")
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["canonical"]["path"], "reference.pdf")
            for artifact in manifest["files"]:
                from teach_inbox.service import sha
                self.assertEqual(sha(archive.read(artifact["path"])), artifact["sha256"])
            self.assertEqual(manifest["pages"][1]["text"]["path"], "pages/2.txt")
        with zipfile.ZipFile(io.BytesIO(self.service.export(template["id"], sources_only=True))) as archive:
            self.assertEqual(archive.namelist(), ["sources/exam.pdf"])
        self.assertEqual(self.service.listing(self.assignment["id"])[0]["id"], template["id"])
        with connect(self.documents.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM assignment_versions").fetchone()[0], 0)

    def test_creation_receipt_survives_restart_and_does_not_accept_different_contents(self):
        request = str(uuid.uuid4())
        template = self.upload(request_id=request)
        self.assertEqual(self.upload(request_id=request)["id"], template["id"])
        self.assertEqual(self.converter.call_count, 1)
        with self.assertRaises(Conflict):
            self.upload(request_id=request, files={"exam.pdf": b"Changed"})
        with self.assertRaises(Conflict):
            self.upload()
        restarted = create_app(self.directory.name).extensions["assignment_templates"]
        self.assertEqual(restarted.get(template["id"])["source_names"], ["exam.pdf"])

    def test_concurrent_same_request_publishes_one_template(self):
        request = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: self.upload(request_id=request)["id"], range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.service.listing(self.assignment["id"])), 1)

    def test_mapping_keeps_immutable_history_and_rejects_stale_edits(self):
        template = self.upload()
        first = self.service.save_mapping(template["id"], {"version": 0, "questions": questions()})
        self.assertEqual(first["version"], 1)
        self.assertEqual(len(first["questions"][0]["regions"]), 2)
        same = self.service.save_mapping(template["id"], {"version": 1, "questions": first["questions"]})
        self.assertEqual(same["revision_id"], first["revision_id"])
        changed = questions(); changed[0]["label"] = "Show all work"
        second = self.service.save_mapping(template["id"], {"version": 1, "questions": changed})
        self.assertEqual(second["version"], 2)
        with self.assertRaises(Conflict):
            self.service.save_mapping(template["id"], {"version": 1, "questions": questions()})
        historical = self.service.get(template["id"], first["revision_id"])
        self.assertEqual(historical["questions"][0]["label"], "Explain your method")
        self.assertIn(first["revision_id"], historical["package_url"])
        exported = json.loads(self.service.export(template["id"], first["revision_id"], manifest_only=True))
        self.assertEqual(exported["mapping"]["id"], first["revision_id"])

    def test_invalid_regions_and_owner_references_write_nothing(self):
        with self.assertRaises(NotFound):
            self.upload(assignment_id="absent")
        self.assertFalse(self.converter.called)
        template = self.upload()
        for regions in ([], [{"page": 3}], [{"page": True}], [{"page": 1, "region": {**FULL, "width": 0}}],
                        [{"page": 1, "region": {**FULL, "x": .1}}], [{"page": 1, "region": {**FULL, "y": float("nan")}}]):
            value = questions(); value[0]["regions"] = regions
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                self.service.save_mapping(template["id"], {"version": 0, "questions": value})
        self.assertEqual(self.service.get(template["id"])["version"], 0)
        with self.assertRaises(Conflict):
            self.service.save_mapping(template["id"], {"version": False, "questions": questions()})

    def test_conversion_failure_and_unsafe_names_leave_no_registered_artifacts(self):
        for name in ("../exam.pdf", "/exam.pdf", "a//exam.pdf", "a\\exam.pdf", "bad\x00.pdf"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.upload(files={name: b"bad"})
        self.assertFalse(self.converter.called)
        self.converter.side_effect = ValueError("Synthetic conversion failed")
        with self.assertRaisesRegex(ValueError, "Synthetic conversion"):
            self.upload()
        self.assertEqual(self.service.listing(self.assignment["id"]), [])

    def test_catalog_revisions_and_sources_cannot_be_rewritten(self):
        template = self.upload()
        preview_url = f"/api/assignment-templates/{template['id']}/pages/2/preview.webp"
        self.assertEqual(self.client.get(preview_url).status_code, 200)
        self.service.save_mapping(template["id"], {"version": 0, "questions": questions()})
        for sql in ("UPDATE assignment_templates SET label='changed'", "DELETE FROM assignment_templates",
                    "UPDATE assignment_template_revisions SET revision=99", "DELETE FROM assignment_template_revisions"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError), connect(self.documents.path) as db:
                db.execute(sql)
        with connect(self.documents.path) as db:
            row = db.execute("SELECT * FROM assignment_templates").fetchone()
        with self.documents.rosters.lock(), self.documents.files.files("definition", self.assignment["definition_id"], write=True) as root:
            manifest = json.loads((root / row["manifest_path"]).read_bytes())
            (root / manifest["pages"][1]["image"]["path"]).write_bytes(b"Corrupt synthetic image")
        with self.assertRaisesRegex(StorageError, "checksum"):
            self.service.read(template["id"], "image", 2)
        # A cached display image must not bypass the original's verification.
        self.assertEqual(self.client.get(preview_url).status_code, 503)

    def test_page_preview_is_bounded_and_does_not_replace_original(self):
        original = io.BytesIO()
        Image.new('RGB', (2400, 3200), 'white').save(original, 'PNG')
        value = prepared({'exam.pdf': b'Original bytes'})
        value['pages'][0].update(width_px=2400, height_px=3200, png=original.getvalue())
        self.converter.side_effect = None
        self.converter.return_value = value
        template = self.upload()
        url = f"/api/assignment-templates/{template['id']}/pages/1"
        result = self.client.get(url + '/preview.webp')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.mimetype, 'image/webp')
        self.assertIn('no-store', result.headers['Cache-Control'])
        with Image.open(io.BytesIO(result.data)) as image:
            self.assertEqual(image.size, (1200, 1600))
        self.assertEqual(self.client.get(url + '.png').data, original.getvalue())
        self.assertEqual(self.client.get(url.replace('/1', '/3') + '/preview.webp').status_code, 404)

    def test_api_upload_mapping_download_access_and_same_origin_checks(self):
        fields = {"assignment_id": self.assignment["id"], "label": "API template", "request_id": str(uuid.uuid4()),
                  "files": (io.BytesIO(b"PDF bytes"), "test.pdf")}
        response = self.client.post("/api/assignment-templates", data=fields, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        template = response.json; base = "/api/assignment-templates/" + template["id"]
        fresh = self.app.test_client()
        self.assertEqual(fresh.get(base).status_code, 200)
        for url in (template["pdf_url"], template["source_url"], template["package_url"], template["pages"][0]["image_url"]):
            result = fresh.get(url)
            self.assertEqual(result.status_code, 200)
            self.assertIn("no-store", result.headers["Cache-Control"])
        self.assertEqual(fresh.get(base + "/pages/3.png").status_code, 404)
        self.assertEqual(fresh.get(base + "/manifest?revision_id=wrong").status_code, 404)
        response = fresh.put(base + "/mapping", json={"version": 0, "questions": questions()}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(fresh.put(base + "/mapping", json={"version": 0, "questions": questions()}, headers=HEADERS).status_code, 409)
        self.assertEqual(fresh.put(base + "/mapping", json={}, headers={"X-Teaching-Request": "1", "Origin": "https://elsewhere.invalid"}).status_code, 403)
        self.assertEqual(fresh.post("/api/assignment-templates", json={}, headers=HEADERS).status_code, 415)
        self.assertEqual(fresh.put(base + "/mapping", data="not json", headers=HEADERS).status_code, 415)


class TemplateStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = temporary_storage(self.directory.name)
        self.app = create_app(storage=self.storage)
        self.client = self.app.test_client()
        self.service = self.app.extensions["assignment_templates"]
        self.documents = self.app.extensions["documents"]
        self.course = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Shared template", "term": "Fall",
                                       "year": 2026, "sections": [501], "is_current": True}, headers=HEADERS).json
        self.storage.reconcile_all()
        self.assignment = self.documents.create_assignment(self.course["definition_id"], "template", "Template test")
        self.enterContext(patch("document_identity.template_conversion.convert_template", side_effect=prepared))
        self.template = self.service.upload(self.assignment["id"], "Main", str(uuid.uuid4()), {"original.docx": b"Synthetic original"})

    def test_shared_owner_stays_hot_for_another_current_term_and_archives_verified(self):
        other = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Other term", "term": "Spring",
                                  "year": 2027, "sections": [501], "is_current": True}, headers=HEADERS).json
        self.storage.reconcile_all()
        self.assertEqual(self.client.patch("/api/courses/" + self.course["id"], json={"version": self.course["version"], "is_current": False}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.storage.backup_all()["status"], "complete")
        manager = self.storage.definition_storage
        self.assertTrue(manager.hot_path(self.course["definition_id"]).exists())
        self.assertEqual(self.client.patch("/api/courses/" + other["id"], json={"version": other["version"], "is_current": False}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.storage.backup_all()["status"], "complete")
        self.assertFalse(manager.hot_path(self.course["definition_id"]).exists())
        self.assertEqual(self.service.read(self.template["id"], "pdf")[0], b"Synthetic reference PDF")
        self.assertEqual(len(self.service.get(self.template["id"])["pages"]), 2)

    def test_corrupt_template_cannot_be_verified_or_purged(self):
        with self.documents.rosters.lock(), self.documents.files.files("definition", self.course["definition_id"], write=True) as root:
            with connect(self.documents.path) as db:
                row = db.execute("SELECT * FROM assignment_templates").fetchone()
            manifest = json.loads((root / row["manifest_path"]).read_bytes())
            (root / manifest["sources"][0]["path"]).write_bytes(b"Damaged source")
        self.client.patch("/api/courses/" + self.course["id"], json={"version": self.course["version"], "is_current": False}, headers=HEADERS)
        with self.assertLogs(level="ERROR"), self.assertRaisesRegex(StorageError, "Nightly backup needs attention"):
            self.storage.backup_all()
        self.assertTrue(self.storage.definition_storage.hot_path(self.course["definition_id"]).exists())


if __name__ == "__main__":
    unittest.main()
