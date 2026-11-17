"""The in-app file viewer (AGENTS.md "Faculty assistant", VIEWER): a file route is read through the route itself; a PDF becomes pictures of
its pages rendered in the document sandbox, kept in memory by digest and page; a picture is re-encoded whatever type its route names; text
is text; an SVG or HTML is never a picture; sign-in, export, mail and viewer routes are never read; every answer is private."""
import io
import os
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import unittest
import weakref
from unittest import mock

from flask import Response
from PIL import Image

from app import create_app
from file_viewer import service as viewer_service


def pdf(pages):
    images = [Image.new("L", (850, 1100), 255 - 20 * n) for n in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:], resolution=100)
    return buffer.getvalue()


def phone_scan():
    """One page the size of a phone's photograph (2228 by 3193 points, 31 by 44 inches): 30.9 million pixels at 150 DPI."""
    image = Image.new("L", (2228, 3193), 255)
    image.paste(0, (400, 600, 1800, 700))  # a line of the note
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=72)
    return buffer.getvalue()


def png(width=40, height=30, mode="RGBA"):
    buffer = io.BytesIO()
    Image.new(mode, (width, height), (200, 10, 10, 128) if mode == "RGBA" else 128).save(buffer, "PNG")
    return buffer.getvalue()


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(os.path.join(self.temp.name, "data"))
        self.app.testing = True
        self.files = {"exam": (pdf(6), "application/pdf", "Exam 1.pdf"), "crop": (png(), "application/octet-stream", "crop.bin"),
                      "grades": ("\ufeffname,score\nLee,9\n\"Kim, Ann\",<script>".encode(), "text/csv", "grades.csv"),
                      "notes": ("# Notes\n<script>".encode(), "text/markdown", "notes.md"),
                      "drawing": (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml", "a.svg"),
                      "page": (b"<html><script>alert(1)</script></html>", "text/html", "a.html"),
                      "report": (b"PK\x03\x04 a word file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "report.docx"),
                      "broken": (b"%PDF-1.4 not really", "application/pdf", "broken.pdf")}
        self.app.add_url_rule("/api/test-files/<name>", "test_files.file", self.serve)
        self.client = self.app.test_client()

    def serve(self, name):
        if name not in self.files:
            return {"error": "No such file."}, 404
        content, media, filename = self.files[name]
        return Response(content, mimetype=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def describe(self, route):
        return self.client.get("/api/viewer/file", query_string={"route": route})

    def test_a_pdf_is_shown_as_its_pages_rendered_in_the_sandbox_a_batch_at_a_time(self):
        from document_identity import process
        calls = []
        real = process.run
        with mock.patch.object(process, "run", side_effect=lambda op, content, manifest, **kw: calls.append(manifest["pages"]) or real(op, content, manifest, **kw)):
            found = self.describe("/api/test-files/exam")
            self.assertEqual(found.status_code, 200, found.json)
            self.assertEqual((found.json["kind"], found.json["name"], found.json["pages"]), ("pdf", "Exam 1.pdf", 6))
            self.assertEqual(found.headers["Cache-Control"], "no-store")
            for number in (1, 2, 4, 5, 6, 5):
                page = self.client.get("/api/viewer/page.png", query_string={"route": "/api/test-files/exam", "page": number})
                self.assertEqual((page.status_code, page.mimetype, page.headers["Cache-Control"]), (200, "image/png", "no-store"))
                self.assertEqual(Image.open(io.BytesIO(page.data)).size, tuple(found.json["page_size"]))
            # A page past the end is refused from what is known of the PDF, without another render.
            self.assertEqual(self.client.get("/api/viewer/page.png", query_string={"route": "/api/test-files/exam", "page": 9}).status_code, 400)
        self.assertEqual(calls, [[1, 2, 3, 4], [5, 6, 7, 8]])  # pages 1-4 with the description, 5-8 once; the rest from memory
        self.assertEqual(self.client.get("/api/viewer/page.png", query_string={"route": "/api/test-files/exam", "page": "x"}).status_code, 400)
        self.assertEqual(self.client.get("/api/viewer/page.png", query_string={"route": "/api/test-files/crop", "page": 1}).status_code, 400)

    def test_a_phone_scans_oversized_page_is_drawn_smaller_not_refused_and_a_scans_own_pixels_still_are(self):
        from document_identity import media
        self.files["note"] = (phone_scan(), "application/pdf", "2nd Dr Note.pdf")
        found = self.describe("/api/test-files/note")
        self.assertEqual(found.status_code, 200, found.json)
        self.assertEqual((found.json["kind"], found.json["pages"], max(found.json["page_size"])), ("pdf", 1, media.LOOK_SIDE))
        page = self.client.get("/api/viewer/page.png", query_string={"route": "/api/test-files/note", "page": 1})
        self.assertEqual(page.status_code, 200)
        drawn = Image.open(io.BytesIO(page.data))
        self.assertEqual(drawn.size, tuple(found.json["page_size"]))
        self.assertAlmostEqual(drawn.width / drawn.height, 2228 / 3193, places=2)   # the page's own shape
        self.assertLess(min(drawn.getdata()), 64)                                    # and what is on it
        # An ordinary page is drawn at 150 DPI as before, and a scan's page (pixels at its resolution) is still refused.
        self.assertEqual(self.describe("/api/test-files/exam").json["page_size"], [1275, 1651])
        with self.assertRaisesRegex(ValueError, "oversized scan page"):
            next(media.render_pages(phone_scan(), 150))

    def test_pages_already_drawn_come_from_memory_by_digest_and_a_changed_file_is_stale(self):
        found = self.describe("/api/test-files/exam").json
        page = lambda number, digest: self.client.get("/api/viewer/page.png", query_string={"route": "/api/test-files/exam", "page": number, "sha256": digest})
        from app_files import AppFiles
        reads = []
        real = AppFiles.read
        with mock.patch.object(AppFiles, "read", autospec=True, side_effect=lambda me, *a, **k: reads.append(a[0]) or real(me, *a, **k)):
            self.assertEqual([page(n, found["sha256"]).status_code for n in (1, 2, 3, 4)], [200] * 4)
            self.assertEqual(reads, [])  # a long PDF is not read again for each page already drawn
            self.assertEqual(page(9, found["sha256"]).status_code, 400)  # past the end, known without reading it
            self.assertEqual(reads, [])
            self.files["exam"] = (pdf(2), "application/pdf", "Exam 1.pdf")  # the file changed since it was opened
            stale = page(5, found["sha256"])
            self.assertEqual(stale.status_code, 409, stale.json)
            self.assertIn("changed since it was opened", stale.json["error"])
        self.assertEqual(page(1, "not-a-digest").status_code, 400)

    def test_a_pdf_faculty_made_opens_in_the_browsers_viewer_and_only_that_response_may_be_framed(self):
        from document_identity import process
        with mock.patch.object(viewer_service, "NATIVE_PDFS", viewer_service.NATIVE_PDFS | {"test_files.file"}), \
             mock.patch.object(process, "run", side_effect=AssertionError("a PDF the browser shows is not rendered")):
            found = self.describe_native("/api/test-files/exam")
            self.assertEqual((found["kind"], found["native"]), ("pdf", True))
            self.assertNotIn("pages", found)
            shown = self.client.get("/api/viewer/pdf", query_string={"route": "/api/test-files/exam", "sha256": found["sha256"]})
            self.assertEqual((shown.status_code, shown.mimetype, shown.data), (200, "application/pdf", self.files["exam"][0]))
            self.assertEqual((shown.headers["X-Frame-Options"], shown.headers["Content-Security-Policy"]), ("SAMEORIGIN", "frame-ancestors 'self'"))
            self.assertEqual(shown.headers["Cache-Control"], "no-store")
            self.assertTrue(shown.headers["Content-Disposition"].startswith("inline"))
            # Only a PDF from the list: a picture on a listed route is refused, and a changed file is stale.
            self.assertEqual(self.client.get("/api/viewer/pdf", query_string={"route": "/api/test-files/crop"}).status_code, 404)
            self.files["exam"] = (pdf(2), "application/pdf", "Exam 1.pdf")
            self.assertEqual(self.client.get("/api/viewer/pdf", query_string={"route": "/api/test-files/exam", "sha256": found["sha256"]}).status_code, 409)
        # Every other response stays unframeable: the file's own route, the viewer's other reads.
        for path in ("/api/test-files/exam", "/api/viewer/file?route=/api/test-files/notes"):
            answer = self.client.get(path)
            self.assertEqual(answer.headers["X-Frame-Options"], "DENY", path)
            self.assertIn("frame-ancestors 'none'", answer.headers["Content-Security-Policy"], path)

    def test_a_pdf_not_on_the_list_is_never_framed_and_keeps_its_pictures(self):
        found = self.describe_native("/api/test-files/exam")  # the test route is not on NATIVE_PDFS, like a student's file
        self.assertNotIn("native", found)
        self.assertEqual(found["pages"], 6)
        refused = self.client.get("/api/viewer/pdf", query_string={"route": "/api/test-files/exam"})
        self.assertEqual(refused.status_code, 404)
        self.assertEqual(refused.headers["X-Frame-Options"], "DENY")
        self.assertNotIn("communications.attachment", viewer_service.NATIVE_PDFS)
        self.assertFalse({"communications.attachment", "communications.original", "communications.seat_chart", "absences.document",
                          "inbox.artifact", "inbox.original"} & viewer_service.NATIVE_PDFS)

    def describe_native(self, route):
        answer = self.client.get("/api/viewer/file", query_string={"route": route, "native": "1"})
        self.assertEqual(answer.status_code, 200, answer.json)
        return answer.json

    def test_a_pdf_that_cannot_be_rendered_is_offered_for_download(self):
        found = self.describe("/api/test-files/broken").json
        self.assertEqual(found["kind"], "file")
        self.assertIn("could not be shown", found["note"])

    def test_a_picture_is_re_encoded_whatever_its_route_calls_it(self):
        found = self.describe("/api/test-files/crop").json
        self.assertEqual((found["kind"], found["picture_size"]), ("image", [40, 30]))
        picture = self.client.get("/api/viewer/picture", query_string={"route": "/api/test-files/crop"})
        self.assertEqual((picture.status_code, picture.mimetype, picture.headers["Cache-Control"]), (200, "image/png", "no-store"))
        self.assertEqual(Image.open(io.BytesIO(picture.data)).mode, "RGB")  # flattened onto white
        self.files["huge"] = (png(4000, 2000, "L"), "image/png", "huge.png")
        self.assertEqual(Image.open(io.BytesIO(self.client.get("/api/viewer/picture", query_string={"route": "/api/test-files/huge"}).data)).size, (3000, 1500))
        # An iPhone photo is a JPEG carrying its HDR gain map as a second picture (Pillow says MPO): the photo is shown.
        buffer = io.BytesIO()
        Image.new("RGB", (60, 40), (10, 200, 10)).save(buffer, "MPO", save_all=True, append_images=[Image.new("L", (30, 20), 0)])
        self.assertEqual(Image.open(io.BytesIO(buffer.getvalue())).format, "MPO")
        self.files["phone"] = (buffer.getvalue(), "image/jpeg", "IMG_4954.jpeg")
        found = self.describe("/api/test-files/phone").json
        self.assertEqual((found["kind"], found["picture_size"]), ("image", [60, 40]))
        shown = self.client.get("/api/viewer/picture", query_string={"route": "/api/test-files/phone"})
        self.assertEqual(shown.status_code, 200)
        self.assertGreater(Image.open(io.BytesIO(shown.data)).getpixel((5, 5))[1], 150)  # the green photo, not the black gain map

    def test_a_heic_photo_is_decoded_in_the_sandbox_once_and_shown_upright(self):
        import pillow_heif  # encodes the fixture only; this process never registers it to read HEIC
        buffer = io.BytesIO()
        pillow_heif.from_pillow(Image.new("RGB", (80, 120), (10, 200, 10))).save(buffer, quality=90)
        self.files["phone"] = (buffer.getvalue(), "application/octet-stream", "IMG_5001.HEIC")
        with mock.patch.object(viewer_service.process, "heif_picture", wraps=viewer_service.process.heif_picture) as decoded:
            found = self.describe("/api/test-files/phone").json
            self.assertEqual((found["kind"], found["picture_size"]), ("image", [80, 120]))
            shown = self.client.get("/api/viewer/picture", query_string={"route": "/api/test-files/phone"})
            self.assertEqual((shown.status_code, shown.mimetype, shown.headers["Cache-Control"]), (200, "image/png", "no-store"))
            picture = Image.open(io.BytesIO(shown.data))
            self.assertEqual((picture.size, picture.mode), ((80, 120), "RGB"))
            self.assertGreater(picture.getpixel((40, 60))[1], 150)
            self.assertEqual(decoded.call_count, 1)  # the second look came from memory by digest
        self.files["torn"] = (buffer.getvalue()[:300], "image/heic", "torn.heic")
        torn = self.describe("/api/test-files/torn").json
        self.assertEqual(torn["kind"], "file")
        self.assertIn("could not be shown", torn["note"])

    def test_a_csv_export_is_a_table_and_text_is_text(self):
        grades = self.describe("/api/test-files/grades").json
        self.assertEqual((grades["kind"], grades["rows"], grades["truncated"]), ("table", [["name", "score"], ["Lee", "9"], ["Kim, Ann", "<script>"]], False))
        with mock.patch.object(viewer_service, "TABLE_ROWS", 2):
            self.assertEqual(self.describe("/api/test-files/grades").json["truncated"], True)
        notes = self.describe("/api/test-files/notes").json
        self.assertEqual((notes["kind"], notes["text"]), ("text", "# Notes\n<script>"))

    def test_a_csv_with_a_cell_past_the_csv_limit_is_shown_as_text(self):
        self.files["wide"] = (b"a,b\n" + b"x" * 200_000 + b",1\n", "text/csv", "wide.csv")
        wide = self.describe("/api/test-files/wide")
        self.assertEqual(wide.status_code, 200, wide.json)
        self.assertEqual((wide.json["kind"], wide.json["truncated"], len(wide.json["text"])), ("text", True, viewer_service.TEXT_CHARS))

    def test_text_is_text_and_svg_html_and_other_files_are_never_shown_as_pages_or_pictures(self):
        for name in ("drawing", "page", "report"):
            with self.subTest(name=name):
                found = self.describe(f"/api/test-files/{name}").json
                self.assertEqual(found["kind"], "file")
                self.assertNotIn("text", found)
                self.assertEqual(self.client.get("/api/viewer/picture", query_string={"route": f"/api/test-files/{name}"}).status_code, 400)
        with mock.patch.object(viewer_service, "TEXT_CHARS", 5):
            self.assertEqual(self.describe("/api/test-files/notes").json["truncated"], True)

    def test_an_email_is_shown_as_a_mail_program_shows_it_whatever_its_route_calls_it(self):
        from email.message import EmailMessage
        mail = EmailMessage()
        mail["From"], mail["To"], mail["Subject"] = "Scanner <scanner@tamu.edu>", "inbox@faculty.example", "=?utf-8?q?Section_507_=E2=80=93_Exam_1?="
        mail["Date"] = "Thu, 01 Oct 2026 10:00:00 -0500"
        mail.set_content("Scanned pages attached.\x07")
        mail.add_attachment(pdf(1), maintype="application", subtype="pdf", filename="scan.pdf")
        # The Inbox serves its original submission as octet-stream named original.eml: the name says what it is.
        self.files["original"] = (mail.as_bytes(), "application/octet-stream", "original.eml")
        found = self.describe("/api/test-files/original").json
        self.assertEqual(found["kind"], "email")
        self.assertEqual(dict(found["headers"])["Subject"], "Section 507 – Exam 1")
        self.assertEqual(dict(found["headers"])["From"], "Scanner <scanner@tamu.edu>")
        self.assertEqual(found["text"].strip(), "Scanned pages attached.")  # control characters removed
        self.assertEqual([(f["name"], f["type"]) for f in found["attachments"]], [("scan.pdf", "application/pdf")])
        self.assertGreater(found["attachments"][0]["size"], 0)
        html = EmailMessage()
        html["Subject"] = "HTML only"
        html.set_content("<p>Hello <b>there</b></p><script>alert(1)</script>", subtype="html")
        self.files["html"] = (html.as_bytes(), "message/rfc822", "message.eml")
        shown = self.describe("/api/test-files/html").json
        self.assertEqual((shown["kind"], shown["text"]), ("email", "Hello there"))  # the HTML reduced to its text, never shown as HTML
        self.files["record"] = (b'{"action": "scan.receive"}', "application/octet-stream", "original.json")
        self.assertEqual(self.describe("/api/test-files/record").json["kind"], "text")

    def test_only_faculty_file_routes_are_read_and_never_sign_in_export_mail_or_the_viewer_itself(self):
        for route, words in (("https://example.com/a.pdf", "not a Faculty file route"), ("/api/../etc/passwd", "not a Faculty file route"),
                             ("/api/export", "cannot be opened"), ("/api/mail/sent", "cannot be opened"),
                             ("/api/viewer/page.png?route=/api/test-files/exam&page=1", "cannot be opened"),
                             ("/api/test-files/missing", "answered 404"), ("/api/no-such-route", "No Faculty file"), ("", "Name the file")):
            with self.subTest(route=route):
                answer = self.describe(route)
                self.assertEqual(answer.status_code, 404, answer.json)
                self.assertIn(words, answer.json["error"])

    def test_rendered_pages_are_kept_by_digest_within_a_bound(self):
        viewer = viewer_service.FileViewer(self.app)
        with mock.patch.object(viewer_service, "CACHE_BYTES", 10):
            viewer._keep(("a", 1), (b"x" * 6, 1))
            viewer._keep(("a", 2), (b"y" * 6, 1))
        self.assertEqual(list(viewer._pages), [("a", 2)])
        self.assertEqual(viewer._size, 6)


class RenderCacheTests(unittest.TestCase):
    """ASSISTANT VIEWER / PERFORMANCE: rendering stays bounded without losing the requested page or duplicating work."""

    def setUp(self):
        self.viewer = viewer_service.FileViewer(None)
        self.content = b"%PDF-synthetic"
        self.digest = "a" * 64
        self.rendered = {"total": 4, "pages": [{"page": n, "png": bytes([n]) * 6} for n in range(1, 5)]}

    def test_a_page_is_returned_even_when_the_batch_does_not_fit_in_the_cache(self):
        with mock.patch.object(viewer_service, "CACHE_BYTES", 10), \
             mock.patch.object(viewer_service.process, "run", return_value=self.rendered):
            self.assertEqual(self.viewer._render(1, self.content, self.digest), (bytes([1]) * 6, 4))
        self.assertLessEqual(self.viewer._size, 10)

    def test_a_page_larger_than_the_cache_is_still_served_without_being_retained(self):
        with mock.patch.object(viewer_service, "CACHE_BYTES", 2), \
             mock.patch.object(viewer_service.process, "run", return_value=self.rendered):
            self.assertEqual(self.viewer._render(2, self.content, self.digest), (bytes([2]) * 6, 4))
        self.assertEqual(self.viewer._size, 0)
        self.assertFalse(self.viewer._pages)

    def test_a_failed_render_keeps_waiters_on_the_same_batch_lock(self):
        first_started, second_joined, fail_first, retry_started, third_joined, release_retry = (threading.Event() for _ in range(6))
        registrations = []

        class ObservedLocks(weakref.WeakValueDictionary):
            def setdefault(self, key, default=None):
                lock = super().setdefault(key, default)
                registrations.append(id(lock))
                if len(registrations) == 2:
                    second_joined.set()
                if len(registrations) == 3:
                    third_joined.set()
                return lock

        self.viewer._rendering = ObservedLocks()
        calls = []

        def render(*args, **kwargs):
            calls.append(threading.get_ident())
            if len(calls) == 1:
                first_started.set()
                self.assertTrue(fail_first.wait(5))
                raise ValueError("synthetic renderer failure")
            retry_started.set()
            self.assertTrue(release_retry.wait(5))
            return self.rendered

        with mock.patch.object(viewer_service.process, "run", side_effect=render), ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(self.viewer._render, 1, self.content, self.digest)
            try:
                self.assertTrue(first_started.wait(5))
                second = pool.submit(self.viewer._render, 2, self.content, self.digest)
                self.assertTrue(second_joined.wait(5))
                fail_first.set()
                with self.assertRaisesRegex(ValueError, "synthetic renderer failure"):
                    first.result(timeout=5)
                self.assertTrue(retry_started.wait(5))
                third = pool.submit(self.viewer._render, 3, self.content, self.digest)
                self.assertTrue(third_joined.wait(5))
                self.assertEqual(len(set(registrations)), 1)
            finally:
                fail_first.set()
                release_retry.set()
            self.assertEqual(second.result(timeout=5), (bytes([2]) * 6, 4))
            self.assertEqual(third.result(timeout=5), (bytes([3]) * 6, 4))
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.viewer._rendering)


if __name__ == "__main__":
    unittest.main()
