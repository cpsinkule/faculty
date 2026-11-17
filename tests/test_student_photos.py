"""Student photos (AGENTS.md "Student photos"): a printed photo roster is read in the document sandbox, each caption is matched to
the section's roster by exact name, and the import is fingerprinted, receipted and append-only; the photo is served only by the
private route and shown as a checksum prefix in the roster and the gradebook."""
import hashlib
import io
import unittest
import uuid

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from test_rosters import csv_bytes
from test_storage import HEADERS

from app import create_app
from rosters import connect
from student_photos.service import match, name_key
import tempfile

ROWS = [["", "Ames, Renata Alexandra", "100000001", "Registered", "MATH", "U1", "", "", "", "", "", "renata@tamu.edu"],
        ["", "Bauer, Shea", "100000002", "Registered", "MATH", "U1", "", "", "", "", "", "shea@tamu.edu"],
        ["", "Núñez, José Luis", "100000003", "Registered", "MATH", "U1", "", "", "", "", "", "jose@tamu.edu"],
        ["", "Left, Nophoto", "100000004", "Registered", "MATH", "U1", "", "", "", "", "", "nophoto@tamu.edu"]]


def photo_pdf(captions, colors=None):
    """A page like Howdy's: 4 photo tiles per row, each 128 pt square with its numbered caption under it, plus a logo that is no photo."""
    target = io.BytesIO()
    page = canvas.Canvas(target, pagesize=(700, 900))
    page.drawImage(ImageReader(Image.new("RGB", (300, 60), (80, 0, 0))), 20, 820, width=222, height=57)
    for number, caption in enumerate(captions):
        row, column = divmod(number, 4)
        x, top = 20 + column * 160, 800 - row * 175
        color = (colors or {}).get(caption) or (40 + number * 30 % 200, 90 + number * 17 % 150, 160 - number * 11 % 120)
        page.drawImage(ImageReader(Image.new("RGB", (400, 400), color)), x, top - 128, width=128, height=128)
        page.setFont("Helvetica", 9)
        page.drawString(x, top - 140, f"{number + 1}. {caption}")
    page.save()
    return target.getvalue()


class MatchTests(unittest.TestCase):
    def test_names_agree_without_case_accents_punctuation_or_order(self):
        self.assertEqual(name_key("Núñez, José Luis"), name_key("jose luis NUNEZ"))
        self.assertEqual(name_key("Ames, Renata A."), name_key("Renata A Ames"))

    def test_only_a_caption_naming_exactly_one_student_that_no_other_caption_names_matches(self):
        students = [{"id": "1", "name": "Allen, Ann"}, {"id": "2", "name": "Baker, Bo"}, {"id": "3", "name": "Twin, Al"}, {"id": "4", "name": "Twin, Al"},
                    {"id": "5", "name": "Cruz, Cy"}]
        photos = [{"caption": "Allen, Ann"}, {"caption": "Baker, Bo"}, {"caption": "Baker, Bo"}, {"caption": "Twin, Al"}, {"caption": "Nobody, No"}]
        pairs, unmatched, missing, ambiguous = match(photos, students)
        self.assertEqual([(p["caption"], s["id"]) for p, s in pairs], [("Allen, Ann", "1")])
        self.assertEqual((unmatched, ambiguous), (["Nobody, No", "Twin, Al"], ["Baker, Bo"]))
        self.assertEqual(missing, ["Baker, Bo", "Cruz, Cy", "Twin, Al", "Twin, Al"])

    def test_a_caption_or_name_with_no_latin_letters_pairs_only_with_the_same_text(self):
        pairs, unmatched, missing, _ = match([{"caption": "\u674e\u96f7"}], [{"id": "1", "name": "\u738b\u82b3"}])
        self.assertEqual((pairs, unmatched, missing), ([], ["\u674e\u96f7"], ["\u738b\u82b3"]))
        pairs, unmatched, missing, _ = match([{"caption": ""}, {"caption": "12."}], [{"id": "1", "name": "\u674e\u96f7"}, {"id": "2", "name": ""}])
        self.assertEqual((pairs, sorted(unmatched), sorted(missing)), ([], ["", "12."], ["", "\u674e\u96f7"]))
        pairs, unmatched, missing, _ = match([{"caption": "\u674e\u96f7"}], [{"id": "1", "name": "\u674e\u96f7"}])
        self.assertEqual(([(p["caption"], s["id"]) for p, s in pairs], unmatched, missing), ([("\u674e\u96f7", "1")], [], []))


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app(self.directory.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.get("/").close()
        response = self.client.post("/api/courses", json={"code": "MATH 140", "name": "Calculus", "term": "Fall", "year": 2026,
                                                        "sections": [507, 508], "multiple_sections": True, "is_current": True}, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.json)
        self.group = next(g for g in self.client.get("/api/state").json["course_groups"] if any(c["id"] == response.json["id"] for c in g["offerings"]))
        self.route = f"/api/course-groups/{self.group['id']}/sections/507/roster"
        upload = self.client.post(self.route, data={"file": (io.BytesIO(csv_bytes(ROWS)), "roster.csv")}, headers=HEADERS)
        self.assertEqual(upload.status_code, 201, upload.json)
        self.students = {s["fields"]["Name"]: s["id"] for s in upload.json["students"]}
        self.base = f"/api/student-photos/sections/{self.group['id']}/507"

    def post(self, action, pdf, **fields):
        data = {"file": (io.BytesIO(pdf), "507roster.pdf"), **fields}
        return self.client.post(f"{self.base}/{action}", data=data, headers=HEADERS)

    def captions(self):
        return ["Ames, Renata Alexandra", "Bauer, Shea", "Nunez, Jose Luis", "Stranger, Some One"]

    def approved(self, pdf, **extra):
        preview = self.post("preview", pdf)
        self.assertEqual(preview.status_code, 200, preview.json)
        return preview.json, {"request_id": str(uuid.uuid4()), "fingerprint": preview.json["fingerprint"], "confirmation": "import", **extra}

    def rows(self):
        with connect(self.app.config["DATABASE"]) as db:
            return [dict(r) for r in db.execute("SELECT enrollment_id,revision,sha256 FROM student_photos ORDER BY enrollment_id, revision")]

    def test_a_preview_reports_matches_and_leftovers_and_writes_nothing(self):
        preview, _ = self.approved(photo_pdf(self.captions()))
        self.assertEqual((preview["photos_in_file"], preview["matched"], preview["new"]), (4, 3, 3))
        self.assertEqual((preview["unmatched_captions"], preview["students_without_photo"]), (["Stranger, Some One"], ["Left, Nophoto"]))
        self.assertNotIn("jpeg", str(preview))
        self.assertEqual(self.rows(), [])

    def test_an_import_needs_its_confirmation_and_the_fingerprint_of_the_preview(self):
        pdf = photo_pdf(self.captions())
        _, fields = self.approved(pdf)
        self.assertEqual(self.post("import", pdf, **{**fields, "confirmation": ""}).status_code, 400)
        self.assertEqual(self.post("import", pdf, **{**fields, "fingerprint": "0" * 64}).status_code, 409)
        self.assertEqual(self.post("import", b"not a pdf", **fields).status_code, 400)
        self.assertEqual(self.rows(), [])

    def test_an_import_saves_photos_for_the_matched_students_only_and_serves_them_privately(self):
        pdf = photo_pdf(self.captions())
        _, fields = self.approved(pdf)
        done = self.post("import", pdf, **fields)
        self.assertEqual((done.status_code, done.json["matched"], done.json["repeated"]), (200, 3, False), done.json)
        self.assertEqual(len(self.rows()), 3)
        self.assertNotIn(self.students["Left, Nophoto"], {r["enrollment_id"] for r in self.rows()})
        image = self.client.get(f"/api/student-photos/{self.students['Bauer, Shea']}")
        self.assertEqual((image.status_code, image.mimetype), (200, "image/jpeg"))
        self.assertEqual(Image.open(io.BytesIO(image.data)).size, (256, 256))
        self.assertEqual(image.headers["Cache-Control"], "no-store")  # the app keeps API responses out of every shared cache
        self.assertNotIn("immutable", image.headers["Cache-Control"])
        self.assertEqual(self.client.get(f"/api/student-photos/{self.students['Bauer, Shea']}", headers={"If-None-Match": image.headers["ETag"]}).status_code, 304)
        self.assertEqual(self.client.get(f"/api/student-photos/{self.students['Left, Nophoto']}").status_code, 404)
        self.assertEqual(self.client.get("/api/student-photos/not-an-id").status_code, 404)
        roster = self.client.get(self.route).json
        by_name = {s["fields"]["Name"]: s for s in roster["students"]}
        self.assertEqual(sorted(name for name, s in by_name.items() if s.get("photo")), ["Ames, Renata Alexandra", "Bauer, Shea", "Núñez, José Luis"])
        self.assertEqual(len(by_name["Bauer, Shea"]["photo"]), 12)
        repeat = self.post("import", pdf, **fields)
        self.assertEqual((repeat.status_code, repeat.json["repeated"], len(self.rows())), (200, True, 3))

    def test_the_same_photos_record_nothing_and_a_changed_photo_is_a_new_revision(self):
        pdf = photo_pdf(self.captions())
        _, fields = self.approved(pdf)
        self.post("import", pdf, **fields)
        again, fields = self.approved(pdf)
        self.assertEqual((again["same"], again["new"], again["changed"]), (3, 0, 0))
        self.post("import", pdf, **fields)
        self.assertEqual(len(self.rows()), 3)
        changed = photo_pdf(self.captions(), colors={"Bauer, Shea": (250, 10, 10)})
        preview, fields = self.approved(changed)
        self.assertEqual((preview["changed"], preview["same"]), (1, 2))
        self.assertEqual(self.post("import", changed, **fields).status_code, 200)
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual(sorted(r["revision"] for r in rows if r["enrollment_id"] == self.students["Bauer, Shea"]), [1, 2])
        with connect(self.app.config["DATABASE"]) as db:
            for sql in ("UPDATE student_photos SET width=64", "DELETE FROM student_photos", "DELETE FROM student_photo_imports", "UPDATE student_photo_imports SET actor='x'"):
                with self.assertRaises(Exception):
                    db.execute(sql)

    def test_the_gradebook_names_who_has_a_photo_without_sending_it(self):
        pdf = photo_pdf(self.captions())
        _, fields = self.approved(pdf)
        self.post("import", pdf, **fields)
        term = self.client.get(self.route).json["term_id"]
        view = self.client.get(f"/api/gradebook/terms/{term}").json
        withphoto = {s["name"]: s for s in view["students"] if s.get("photo")}
        self.assertEqual(len(withphoto), 3)
        self.assertTrue(all(len(s["photo"]) == 12 and s["enrollment_id"] for s in withphoto.values()))
        self.assertNotIn("jpeg", str(view).lower())

    def test_the_import_is_not_in_the_generic_export(self):
        pdf = photo_pdf(self.captions())
        _, fields = self.approved(pdf)
        self.post("import", pdf, **fields)
        export = self.client.get("/api/export")
        if export.status_code == 200:
            self.assertNotIn(b"student_photos", export.data)
            self.assertNotIn(hashlib.sha256(b"x").hexdigest().encode(), export.data)


if __name__ == "__main__":
    unittest.main()
