"""Printed provenance, crash recovery, review, and verified owner lifecycle."""
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from werkzeug.exceptions import Conflict

from app import create_app
from course_storage import StorageError, timestamp
from document_identity import tokens
from document_identity.media import PROFILE, MM, stamp_pdf, inspect_canonical, render_pages, decode_page, validate_print, render_barcode
from document_identity.storage import read_verified
from rosters import connect
from teach_inbox.handlers import HANDLERS
from teach_inbox.service import sha
from test_storage import temporary_storage, HEADERS

FIXTURES = Path(__file__).resolve().parents[1] / "document_identity/fixtures"


def grey_side(paper=170, marks=(), shadow=False, uneven=True):
    """A scanned side at 200 DPI as a home scanner in grey draws it: paper at `paper` (lighter towards one corner when `uneven`),
    the dark `shadow` of the paper's top edge, and a line of `pixels` pixels for each (levels below the paper, pixels) of `marks`."""
    import numpy as np
    from PIL import Image
    rows, columns = np.mgrid[0:2200, 0:1700]
    side = np.full((2200, 1700), float(paper)) - (rows / 2200 + columns / 1700) * (6 if uneven else 0)
    for number, (level, pixels) in enumerate(marks):
        top = 300 + 60 * number
        side[top:top + 4, 300:300 + pixels // 4] += level
    if shadow:
        side[:14] = 90
    return Image.fromarray(side.round().clip(0, 255).astype("uint8"))


class TokenTests(unittest.TestCase):
    def test_fixed_vectors_and_os_randomness(self):
        for raw, expected in ((bytes(16), "T1-" + "0" * 26), (b"\xff" * 16, "T1-7" + "Z" * 25),
                              ((1).to_bytes(16, "big"), "T1-" + "0" * 25 + "1")):
            self.assertEqual(tokens.encode_token(raw), expected)
            self.assertEqual(tokens.parse_token(expected), raw)
        with patch("document_identity.tokens.secrets.token_bytes", return_value=bytes(16)) as generator:
            self.assertEqual(tokens.generate_token(), "T1-" + "0" * 26)
            generator.assert_called_once_with(16)
        self.assertEqual(len({tokens.generate_token() for _ in range(2000)}), 2000)

    def test_strict_namespaces_and_aliases(self):
        for bad in (None, 123, "", "ti:1234567890", "1234567890", "T2-" + "0" * 26,
                    "T1-8" + "0" * 25, "T1-" + "I" * 26, "T1-" + "0" * 25,
                    "T1-" + "0" * 27, "T1-" + "0" * 26 + "\n", "t1-" + "0" * 26,
                    "T1-" + "o" * 26, "T1-" + "０" * 26):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tokens.parse_token(bad)
        alias = tokens.generate_alias()
        self.assertTrue(tokens.valid_alias(alias))
        self.assertEqual(len(alias), 13)
        for bad in (tokens.generate_token(), "ti:" + alias, "1234567890", alias.lower(), alias + "\n"):
            self.assertFalse(tokens.valid_alias(bad))


class MediaContractTests(unittest.TestCase):
    def test_reorder_supports_every_version_in_a_stack(self):
        import base64
        from document_identity.process import run
        source = base64.b64encode((FIXTURES / 'letter.pdf').read_bytes()).decode()
        pdf, result = run('reorder', b'', {'sources': [source] * 26, 'pages': [[i, 0] for i in range(26)]})
        self.assertEqual(result['pages'], 26)
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages), 26)

    def pdf(self, transform=None, contract=True):
        reader = PdfReader(FIXTURES / "letter.pdf")
        writer = PdfWriter()
        for source in reader.pages:
            page = writer.add_page(source)
            if transform: transform(page)
        if contract: writer.add_metadata({"/FacultyLayout": PROFILE})
        output = io.BytesIO(); writer.write(output)
        return output.getvalue()

    def test_geometry_contract_and_content_overlap_rejected(self):
        with self.assertRaises(ValueError): inspect_canonical(self.pdf(contract=False))
        from pypdf.generic import NameObject, NumberObject
        for transform in (lambda p: p.cropbox.__setitem__(2, NumberObject(400)),
                          lambda p: p.__setitem__(NameObject("/UserUnit"), NumberObject(2)),
                          lambda p: p.rotate(90), lambda p: p.mediabox.__setitem__(0, NumberObject(10))):
            with self.assertRaises(ValueError): inspect_canonical(self.pdf(transform))
        from reportlab.pdfgen.canvas import Canvas
        mark = io.BytesIO(); drawing = Canvas(mark, pagesize=(612,792)); drawing.drawString(530,50,"content"); drawing.save()
        with self.assertRaises(ValueError): inspect_canonical(self.pdf(lambda p: p.merge_page(PdfReader(mark).pages[0])))

    def test_supported_rotation_normalizes_without_changing_visual_contract(self):
        from pypdf import Transformation
        # Store an upside-down content stream with Rotate=180; its displayed page
        # is exactly the original contract, with normalized portrait dimensions.
        rotated = self.pdf(lambda p: (p.add_transformation(Transformation().rotate(180).translate(612,792)), p.rotate(180)))
        self.assertEqual(inspect_canonical(rotated)["page_count"], 3)
        manifest = [{"output_page": i, "token": tokens.generate_token(), "page_number": i, "symbology": "DataMatrix"} for i in range(1,4)]
        output = stamp_pdf(rotated, manifest)
        for image, expected in zip(render_pages(output, 200), manifest):
            self.assertEqual([c["payload"] for c in decode_page(image)], [expected["token"]])
        self.assertTrue(validate_print(output, manifest)["placement_checked"])

    def test_scan_codes_are_read_at_300_dpi_and_located_on_the_200_dpi_page(self):
        # 2026-10-18 instructor: codes are read from a 300 DPI drawing; the stored page stays at 200 DPI.
        from pypdf import Transformation
        from PIL import Image
        from document_identity.media import extract_scan
        token = tokens.generate_token()
        writer = PdfWriter()
        page = writer.add_page(PdfReader(FIXTURES / "letter.pdf").pages[0])
        size = 7 * MM  # a 7 mm mark: 3 of 22 printed ones read at 200 DPI, 22 of 22 at 300
        page.merge_transformed_page(PdfReader(io.BytesIO(render_barcode(token))).pages[0], Transformation().scale(7 / 20).translate(300, 400))
        scan = io.BytesIO(); writer.write(scan)
        with tempfile.TemporaryDirectory() as folder:
            [result] = extract_scan(scan.getvalue(), Path(folder))
            self.assertEqual(Image.open(Path(folder) / "1.png").size, (1700, 2200))
        self.assertEqual((result["decoder"]["dpi"], result["decoder"]["raster_dpi"]), (300, 200))
        [candidate] = result["candidates"]
        self.assertEqual(candidate["payload"], token)
        centre = [sum(point[axis] for point in candidate["location"]) / 4 for axis in (0, 1)]
        expected = [(300 + size / 2) * 200 / 72, (792 - 400 - size / 2) * 200 / 72]
        self.assertLess(max(abs(a - b) for a, b in zip(centre, expected)), 4)

    def test_a_page_with_no_mark_at_300_dpi_is_read_once_more_at_250(self):
        # SECOND LOOK (Exam 2, 2026-10-25): three Scantron labels printed against the sticker's top edge read at 250 DPI, not at 300.
        from pypdf import Transformation
        from document_identity import media
        token = tokens.generate_token()
        writer = PdfWriter()
        page = writer.add_page(PdfReader(FIXTURES / "letter.pdf").pages[0])
        page.merge_transformed_page(PdfReader(io.BytesIO(render_barcode(token))).pages[0], Transformation().scale(7 / 20).translate(300, 400))
        scan = io.BytesIO(); writer.write(scan)
        sizes, real = [], media.decode_page

        def clipped(image):  # the 300 DPI drawing reads nothing, as the clipped labels did
            sizes.append(image.width)
            return [] if image.width == 2550 else real(image)
        with tempfile.TemporaryDirectory() as folder, patch.object(media, "decode_page", side_effect=clipped):
            [result] = media.extract_scan(scan.getvalue(), Path(folder))
        self.assertEqual((sizes, result["decoder"]["dpi"], [c["payload"] for c in result["candidates"]]), ([2550, 2125], 250, [token]))
        centre = [sum(point[axis] for point in result["candidates"][0]["location"]) / 4 for axis in (0, 1)]
        expected = [(300 + 3.5 * MM) * 200 / 72, (792 - 400 - 3.5 * MM) * 200 / 72]  # still on the stored 200 DPI page
        self.assertLess(max(abs(a - b) for a, b in zip(centre, expected)), 4)
        # A page that has its mark at 300 DPI is read once, as before.
        sizes.clear()
        with tempfile.TemporaryDirectory() as folder, patch.object(media, "decode_page", side_effect=real):
            self.assertEqual(media.extract_scan(scan.getvalue(), Path(folder))[0]["decoder"]["dpi"], 300)

    def test_a_faint_mark_is_read_from_the_page_made_black_and_white(self):
        # FAINT MARKS (Quiz 7, 2026-11-06): photocopied codes too faint for the 300 and 250 DPI reads read once the drawing is thresholded.
        from pypdf import Transformation
        from document_identity import media
        token = tokens.generate_token()
        writer = PdfWriter()
        page = writer.add_page(PdfReader(FIXTURES / "letter.pdf").pages[0])
        page.merge_transformed_page(PdfReader(io.BytesIO(render_barcode(token))).pages[0], Transformation().scale(7 / 20).translate(300, 400))
        scan = io.BytesIO(); writer.write(scan)
        looks, real = [], media.decode_page

        def faint(image):  # only a black-and-white drawing reads, as the photocopies did
            two_tone = sum(1 for count in image.histogram() if count) <= 2
            looks.append((image.width, two_tone))
            return real(image) if two_tone else []
        with tempfile.TemporaryDirectory() as folder, patch.object(media, "decode_page", side_effect=faint):
            [result] = media.extract_scan(scan.getvalue(), Path(folder))
        self.assertEqual(looks, [(2550, False), (2125, False), (2550, True)])
        self.assertEqual(([c["payload"] for c in result["candidates"]], result["decoder"]["dpi"], result["decoder"]["threshold"]), ([token], 300, 100))
        centre = [sum(point[axis] for point in result["candidates"][0]["location"]) / 4 for axis in (0, 1)]
        expected = [(300 + 3.5 * MM) * 200 / 72, (792 - 400 - 3.5 * MM) * 200 / 72]  # still on the stored 200 DPI page
        self.assertLess(max(abs(a - b) for a, b in zip(centre, expected)), 4)
        # A page with no mark at all is drawn black and white at each threshold, keeps what it had, and records no threshold.
        looks.clear()
        with tempfile.TemporaryDirectory() as folder, patch.object(media, "decode_page", side_effect=lambda image: looks.append(image.width) or []):
            [result] = media.extract_scan(scan.getvalue(), Path(folder))
        self.assertEqual((looks, result["candidates"], "threshold" in result["decoder"]), ([2550, 2125, 2550, 2550], [], False))

    def test_an_empty_side_scanned_in_grey_is_blank(self):
        # GREY PAPER (take-home, 2026-11-11): printed one-sided on coloured paper and scanned two-sided on the Epson, which draws that
        # paper at 155 to 180 (white at about 210); none of its 860 empty backs was blank, every pixel being darker than 230.
        from document_identity import media
        self.assertTrue(media.blank_side(grey_side()))
        self.assertTrue(media.blank_side(grey_side(shadow=True)))            # the scanner's shadow of the paper's edge
        self.assertTrue(media.blank_side(grey_side(marks=[(-15, 4000)])))    # the front's writing showing through
        self.assertFalse(media.blank_side(grey_side(marks=[(-60, 400)])))    # a few written characters: .00013 of the side
        self.assertFalse(media.blank_side(grey_side(marks=[(-35, 400)])))    # and in light pencil
        # White paper is judged as ever: anything darker than 230 anywhere on it, the edge included, and nothing lighter.
        self.assertTrue(media.blank_side(grey_side(paper=255, uneven=False)))
        self.assertTrue(media.blank_side(grey_side(paper=255, uneven=False, marks=[(-20, 4000)])))
        self.assertFalse(media.blank_side(grey_side(paper=255, uneven=False, marks=[(-30, 800)])))
        self.assertFalse(media.blank_side(grey_side(paper=255, uneven=False, shadow=True)))

        def scan(side):
            content = io.BytesIO()
            side.save(content, "PDF", resolution=200)
            return content.getvalue()
        looks = []
        with patch.object(media, "decode_page", side_effect=lambda image: looks.append(image.width) or []):
            with tempfile.TemporaryDirectory() as folder:
                [empty] = media.extract_scan(scan(grey_side(shadow=True)), Path(folder))
            with tempfile.TemporaryDirectory() as folder:
                [written] = media.extract_scan(scan(grey_side(marks=[(-60, 400)])), Path(folder))
        # A blank side is read for a code once; a written one gets the second look and the faint ones.
        self.assertEqual((empty["blank"], written["blank"], looks), (True, False, [2550, 2550, 2125, 2550, 2550]))

    def test_publication_rejects_shifted_scaled_repeated_and_obstructed_marks(self):
        from pypdf import Transformation
        from reportlab.pdfgen.canvas import Canvas
        manifest = [{"output_page": 1, "token": tokens.generate_token(), "page_number": 1, "symbology": "DataMatrix"}]
        for paper in ("letter", "a4"):
            content = stamp_pdf((FIXTURES / f"{paper}.pdf").read_bytes(), manifest)
            self.assertTrue(validate_print(content, manifest)["quiet_zone_checked"])
            for transform in (Transformation().translate(-15, 0), Transformation().scale(.9)):
                writer = PdfWriter()
                page = writer.add_page(PdfReader(io.BytesIO(content)).pages[0])
                page.add_transformation(transform)
                result = io.BytesIO(); writer.write(result)
                self.assertEqual(decode_page(next(render_pages(result.getvalue())))[0]["payload"], manifest[0]["token"])
                with self.assertRaisesRegex(ValueError, "position or module size"):
                    validate_print(result.getvalue(), manifest)
            writer = PdfWriter()
            page = writer.add_page(PdfReader(io.BytesIO(content)).pages[0])
            graphic = PdfReader(io.BytesIO(render_barcode(manifest[0]["token"])))
            page.merge_translated_page(graphic.pages[0], 100, 100)
            repeated = io.BytesIO(); writer.write(repeated)
            with self.assertRaisesRegex(ValueError, "exact persisted token"):
                validate_print(repeated.getvalue(), manifest)
            writer = PdfWriter()
            page = writer.add_page(PdfReader(io.BytesIO(content)).pages[0])
            mark = io.BytesIO(); drawing = Canvas(mark, pagesize=(float(page.mediabox.width), float(page.mediabox.height)))
            drawing.rect(float(page.mediabox.width) - 25 * MM, 34.6 * MM, 2 * MM, .2 * MM, fill=1, stroke=0); drawing.save()
            page.merge_page(PdfReader(mark).pages[0])
            obstructed = io.BytesIO(); writer.write(obstructed)
            self.assertEqual(decode_page(next(render_pages(obstructed.getvalue())))[0]["payload"], manifest[0]["token"])
            with self.assertRaisesRegex(ValueError, "quiet zone"):
                validate_print(obstructed.getvalue(), manifest)

    def test_compiler_rejects_conflicting_paths_before_creating_scratch(self):
        from document_identity.compiler import compile_sources
        # DOCUMENTS: invalid source bundles fail validation before starting any native processing.
        for sources in ({'main.tex': b'', 'results/extra.tex': b''},
                        {'main.tex': b'', 'main.tex/extra.tex': b''},
                        {'main.tex': b'', 1: b''}):
            with self.subTest(sources=list(sources)), self.assertRaises(ValueError):
                compile_sources(sources)

    @unittest.skipUnless(os.environ.get("FACULTY_TEXLIVE_ROOT") or Path("/usr/bin/pdflatex").exists(), "Install the documented LaTeX toolchain to verify source builds")
    def test_isolated_compiler_reproducibility_blank_pages_and_forbidden_input(self):
        from document_identity.compiler import compile_sources
        source = b"\\documentclass[letterpaper]{facultyassignment}\n\\begin{document}First\\identifiedblankpage Last\\end{document}"
        first, inputs, compiler = compile_sources({"main.tex": source})
        second, _, _ = compile_sources({"main.tex": source})
        self.assertEqual(first, second)
        self.assertEqual(inspect_canonical(first)["page_count"], 3)
        self.assertIn("build.log", inputs)
        self.assertFalse(compiler["shell_escape"])
        self.assertFalse(compiler["network"])
        malicious = source.replace(b"First", b"\\input{/etc/passwd}")
        with self.assertRaises(ValueError): compile_sources({"main.tex": malicious})

    @unittest.skipUnless(os.environ.get("FACULTY_TEXLIVE_ROOT") or Path("/usr/bin/pdflatex").exists(), "Install the documented LaTeX toolchain to verify source builds")
    def test_compiler_has_the_packages_the_assistant_writes_with(self):
        # DOCUMENTS "THE COMPILER'S PACKAGES" (2026-10-25): the Assistant's 5.1 clicker questions used enumitem for their A-E choices and
        # their build failed, leaving an assignment without a paper; texlive-latex-extra (enumitem) sits beside TikZ in the toolchain.
        from document_identity.compiler import compile_sources
        source = (b"\\documentclass[letterpaper]{facultyassignment}\n\\usepackage{enumitem}\n\\usepackage{tikz}\n\\begin{document}\n"
                  b"\\begin{enumerate}[label=\\textbf{\\Alph*.}]\\item One\\item Two\\end{enumerate}\n"
                  b"\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}\n\\end{document}")
        pdf, _, _ = compile_sources({"main.tex": source})
        self.assertEqual(inspect_canonical(pdf)["page_count"], 1)


class DocumentTests(unittest.TestCase):
    managed = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = temporary_storage(self.temp.name) if self.managed else None
        self.app = create_app(storage=self.storage) if self.storage else create_app(self.temp.name)
        self.app.testing = True
        self.client = self.app.test_client()
        self.client.get("/").close()
        self.documents = self.app.extensions["documents"]
        self.scans = self.app.extensions["document_scans"]
        self.inbox = self.app.extensions["inbox"]
        self.path = self.app.config["DATABASE"]
        self.course = self.add_course()
        catalog = self.documents.catalog()
        self.definition = catalog["definitions"][0]["id"]
        self.sections = catalog["sections"]
        self.assignment = self.documents.create_assignment(self.definition, "hw-1", "Homework 1")
        self.version = self.version_for("A")

    def add_course(self, **overrides):
        result = self.client.post("/api/courses", json={"name": "Calculus", "code": "MATH 140", "term": "Fall", "year": 2026,
                                                      "sections": [501, 502], "multiple_sections": len(overrides.get("sections", [501,502])) > 1,
                                                      "is_current": True, **overrides}, headers=HEADERS)
        self.assertEqual(result.status_code, 201, result.json)
        if self.storage:
            self.storage.reconcile_all()
        return result.json

    def version_for(self, label, paper="letter"):
        return self.documents.register_version(self.assignment["id"], label, (FIXTURES / f"{paper}.pdf").read_bytes(),
                                               {"main.tex": (FIXTURES / f"{paper}.tex").read_bytes(), "asset.txt": b"source asset"}, {"fixture": paper})

    def test_version_preview_is_verified_canonical_without_allocating_paper(self):
        """DOCUMENTS: preview retains its course-wide identity and allocates no print stamps."""
        route = f"/api/documents/versions/{self.version['id']}/preview"
        before = self.documents.catalog()
        response = self.client.get(route)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, (FIXTURES / "letter.pdf").read_bytes())
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.documents.catalog(), before)
        preview = self.client.get('/api/viewer/file', query_string={'route': route, 'native': '1'})
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertTrue(preview.json['native'])
        self.assertEqual(self.client.get("/api/documents/versions/missing/preview").status_code, 404)
        with self.documents.files.files("definition", self.definition, write=True) as root:
            (root / self.version["canonical_path"]).write_bytes(b"changed")
        with self.assertRaises(StorageError):
            self.documents.version_preview(self.version["id"])

    def test_malformed_print_options_fail_before_allocating_paper(self):
        before = self.documents.catalog()['batches']
        for ids in ([{}], [[]]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.documents.create_batch(self.version['id'], None, 1, str(uuid.uuid4()),
                    term_id=self.sections[0]['term_id'], section_ids=ids)
            with self.assertRaises(ValueError):
                self.documents.section_stack(ids)
            with self.assertRaises(ValueError):
                self.documents.room_export(ids)
        for value in ('yes', 1, None):
            with self.subTest(fill_names=value), self.assertRaises(ValueError):
                self.batch(fill_names=value)
        self.assertEqual(self.documents.catalog()['batches'], before)

    def test_source_path_collisions_are_rejected_before_registering_a_version(self):
        for sources in ({'main.tex': b'', 'main.tex/extra.tex': b''}, {'main.tex': b'', 1: b''}):
            with self.subTest(sources=list(sources)), self.assertRaises(ValueError):
                self.documents.register_version(self.assignment['id'], 'conflicting', b'', sources, {})

    def batch(self, quantity=2, section=None, version=None, **kw):
        return self.documents.create_batch((version or self.version)["id"], (section or self.sections[0])["id"], quantity,
                                           kw.pop("request_id", str(uuid.uuid4())), **kw)

    def manifest(self, batch):
        with connect(self.path) as db:
            return self.documents.manifest(db, batch["id"])

    def ready(self, **kw):
        batch = self.batch(**kw)
        self.assertTrue(self.documents.build_one())
        batch = self.documents.get_batch(batch["id"])
        self.assertEqual(batch["status"], "ready", batch["error"])
        return batch

    def intake(self, content, event=None, authenticated=True):
        return self.inbox.submit({"action": "scan.receive"}, [{"filename": "scan.pdf", "content": content}], source="email", sender="scanner",
                                 original=b"retained-email:" + content, idempotency_key=event or str(uuid.uuid4()),
                                 authenticated=authenticated, instructor_review_required=True)

    @unittest.skipUnless(os.environ.get("FACULTY_TEXLIVE_ROOT") or Path("/usr/bin/pdflatex").exists(), "Install the documented LaTeX toolchain to verify source builds")
    def test_compile_upload_accepts_full_source_count_and_retains_build_records(self):
        source = b"\\documentclass[letterpaper]{facultyassignment}\\begin{document}Source upload\\end{document}"
        files = [(io.BytesIO(source), "main.tex"), *[(io.BytesIO(b"% unused asset"), f"asset-{i}.tex") for i in range(99)]]
        response = self.client.post("/api/documents/versions/compile", headers=HEADERS, data={
            "assignment_id": self.assignment["id"], "label": "100-source-files", "files": files})
        self.assertEqual(response.status_code, 201, response.json)
        with self.documents.rosters.lock(), self.documents.files.files("definition", self.definition) as root:
            manifest = json.loads(read_verified(root, response.json["source_path"], response.json["source_manifest_sha256"]))
        self.assertEqual(len(manifest), 102)
        self.assertTrue({"build.log", "facultyassignment.cls", "asset-98.tex"}.issubset({entry["path"] for entry in manifest}))

    def test_24_tokens_resolve_exact_provenance_and_persist_across_restart(self):
        versions = [self.version, self.version_for("B", "a4")]
        seen = set()
        for version in versions:
            for section in self.sections:
                batch = self.batch(section=section, version=version)
                manifest = self.manifest(batch)
                self.assertEqual(len(manifest), 6)
                for entry in manifest:
                    provenance = self.documents.resolve_token(entry["token"], authenticated=True)
                    self.assertEqual(provenance["definition_id"], self.definition)
                    self.assertEqual(provenance["section_id"], section["id"])
                    self.assertEqual(provenance["term_id"], section["term_id"])
                    self.assertEqual(provenance["assignment_id"], self.assignment["id"])
                    self.assertEqual(provenance["version_id"], version["id"])
                    self.assertEqual(provenance["batch_id"], batch["id"])
                    self.assertEqual(provenance["copy_id"], entry["copy_id"])
                    self.assertEqual(provenance["page_number"], entry["page_number"])
                    seen.add(entry["token"])
        self.assertEqual(len(seen), 24)
        restarted = create_app(storage=self.storage) if self.storage else create_app(self.temp.name)
        for token in seen:
            self.assertIsNotNone(restarted.extensions["documents"].resolve_token(token, authenticated=True))

    def test_owner_roster_page_range_and_immutable_constraints(self):
        self.add_course(code="MATH 141")
        foreign = next(s for s in self.documents.catalog()["sections"] if s["definition_id"] != self.definition)
        with self.assertRaises(ValueError): self.batch(section=foreign)
        with self.assertRaises(ValueError): self.batch(students=["1234567890", None])
        batch = self.batch()
        with connect(self.path) as db:
            stamp = dict(db.execute("SELECT * FROM page_stamps LIMIT 1").fetchone())
            stamp.update(id=str(uuid.uuid4()), page_number=4, token=tokens.generate_token(), recovery_alias=tokens.generate_alias())
            with self.assertRaises(sqlite3.IntegrityError): db.execute("INSERT INTO page_stamps VALUES(?,?,?,?,?,?,?,?,?,?,?)", tuple(stamp.values()))
            for query in ("UPDATE assignment_versions SET label='edited'", "DELETE FROM assignment_versions",
                          "UPDATE page_stamps SET token='T1-00000000000000000000000000'", "DELETE FROM page_stamps"):
                with self.subTest(query=query), self.assertRaises(sqlite3.IntegrityError): db.execute(query)
        with self.assertRaises(Conflict): self.version_for("A")
        self.assertEqual(len(self.manifest(batch)), 6)

    def test_administrative_student_links_reprints_and_submission_choice(self):
        section = self.sections[0]
        with self.documents.rosters.lock(), connect(self.path) as db:
            db.execute("INSERT INTO student_identities VALUES('000000001','12345',?)", (timestamp(),))
            db.execute("INSERT INTO student_enrollments VALUES('1234567890',?,'000000001',?)", (section["term_id"], timestamp()))
            db.execute("INSERT INTO roster_memberships VALUES(?,'1234567890')", (section["id"],))
        batch = self.ready(quantity=1, students=["1234567890"])
        original_manifest = self.manifest(batch)
        self.documents.assign_copy(batch["copies"][0]["id"], None, "Instructor extra")
        self.assertEqual(self.manifest(batch), original_manifest)
        self.documents.assign_copy(batch["copies"][0]["id"], "1234567890", "Assign to enrolled student")
        other = self.ready(quantity=1, students=["1234567890"], reprint_of=batch["id"])
        revisions = []
        for printed in (batch, other):
            job = self.intake(self.documents.download(printed["id"])); self.scans.process_one()
            item = self.scans.review_item(job["message_id"])
            revisions.append(self.scans.select_assembly(item["assemblies"][0]["id"], [p["id"] for p in item["pages"]], False, "Reviewed"))
        self.assertTrue(self.scans.assembly(batch["copies"][0]["id"])["submission_choice_required"])
        self.scans.choose_submission(revisions[0]["id"], "Instructor chose the original submission")
        selected = self.scans.assembly(other["copies"][0]["id"])
        self.assertFalse(selected["submission_choice_required"])
        self.assertEqual(selected["selected_submission_revision_id"], revisions[0]["id"])
        with connect(self.path) as db:
            scan = db.execute("SELECT scan_page_id FROM assembly_pages LIMIT 1").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError): db.execute("DELETE FROM scan_pages WHERE id=?", (scan,))
            with self.assertRaises(sqlite3.IntegrityError): db.execute("UPDATE scan_pages SET page_stamp_id=NULL WHERE id=?", (scan,))

    def test_bad_auth_concurrent_delivery_and_decode_crash_retry(self):
        batch = self.ready(quantity=1)
        pdf = self.documents.download(batch["id"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda _: self.intake(pdf, "one-arrival"), range(4)))
        self.assertEqual(len({job["message_id"] for job in jobs}), 1)
        retain = self.scans._retain_page
        calls = []
        def interrupt(job, source, page):
            if calls: raise RuntimeError("Injected decoder crash")
            calls.append(True); retain(job, source, page)
        with patch.object(self.scans, "_retain_page", side_effect=interrupt): self.scans.process_one()
        message_id = jobs[0]["message_id"]
        self.assertEqual(self.scans.review_item(message_id)["decode"]["status"], "failed")
        self.scans.retry(message_id); self.scans.process_one()
        self.assertEqual(len(self.scans.review_item(message_id)["pages"]), 3)
        # A crash says nothing about its cause (it could carry file content); a reason Faculty gives is said, with what to do (inbox-7).
        self.assertEqual(self.scans.review_item(message_id)["decode"]["error"], "")
        notpdf = self.intake(b"not a PDF at all"); self.scans.process_one()
        failed = self.scans.review_item(notpdf["message_id"])
        self.assertEqual(failed["decode"]["error"], "Scanner inspection currently accepts PDF attachments. The original is kept.")
        self.assertEqual([(step["kind"], step["text"]) for step in failed["overview"]["steps"]],
                         [("todo", "Its pages could not be checked: Scanner inspection currently accepts PDF attachments. Retry the inspection; "
                                   "if it fails again, upload a readable copy of the PDF.")])
        with patch.object(self.scans, "decode_message", side_effect=RuntimeError("Injected crash with page text")):
            self.scans.retry(notpdf["message_id"]); self.scans.process_one()
        self.assertEqual(self.scans.review_item(notpdf["message_id"])["decode"]["error"],
                         "PDF inspection failed or exceeded its limits. Originals remain available for instructor review.")
        # Only Faculty's own reasons are said word for word: a library's ValueError (passed on by the sandbox) keeps the generic
        # sentence, and with no attachment there is no original to keep.
        for raised, said in ((ValueError("not enough image data"), "PDF inspection failed or exceeded its limits. Originals remain available for instructor review."),
                             (ValueError("There are no scan attachments to inspect."), "There are no scan attachments to inspect."),
                             (ValueError("PDF page count exceeds the processing limit."), "PDF page count exceeds the processing limit. The original is kept.")):
            with self.subTest(raised=str(raised)), patch.object(self.scans, "decode_message", side_effect=raised):
                self.scans.retry(notpdf["message_id"]); self.scans.process_one()
                self.assertEqual(self.scans.review_item(notpdf["message_id"])["decode"]["error"], said)
        bad = self.intake(pdf, authenticated=False); self.scans.process_one()
        self.assertEqual(self.scans.review_item(bad["message_id"])["pages"], [])
        from werkzeug.exceptions import Forbidden
        with self.assertRaises(Forbidden): self.scans.retry(bad["message_id"])

    def test_collisions_concurrency_idempotency_and_fresh_reprints(self):
        first = self.batch(request_id="same-request")
        token = self.manifest(first)[0]["token"]
        generator = tokens.generate_token
        with patch("document_identity.tokens.generate_token", side_effect=[token, generator(), generator(), generator()]):
            second = self.batch(quantity=1)
        self.assertNotIn(token, {p["token"] for p in self.manifest(second)})
        with ThreadPoolExecutor(max_workers=4) as pool:
            repeats = list(pool.map(lambda _: self.batch(request_id="same-request")["id"], range(4)))
            others = list(pool.map(lambda _: self.batch(quantity=1), range(4)))
        self.assertEqual(repeats, [first["id"]] * 4)
        self.assertEqual(len({p["token"] for b in others for p in self.manifest(b)}), 12)
        with self.assertRaises(Conflict): self.batch(quantity=1, request_id="same-request")
        reprint = self.batch(reprint_of=first["id"])
        self.assertTrue({p["token"] for p in self.manifest(first)}.isdisjoint({p["token"] for p in self.manifest(reprint)}))

    def test_canonical_unchanged_round_trip_and_session_free_publication(self):
        batch = self.ready()
        with self.documents.rosters.lock(), self.documents.files.files("definition", self.definition) as root:
            content = read_verified(root, self.version["canonical_path"], self.version["canonical_sha256"])
        self.assertEqual(content, (FIXTURES / "letter.pdf").read_bytes())
        printed = self.documents.download(batch["id"])
        self.assertNotEqual(sha(printed), sha(content))
        for dpi in (200, 300):
            for image, entry in zip(render_pages(printed, dpi), self.manifest(batch), strict=True):
                payloads = [c["payload"] for c in decode_page(image)]
                self.assertEqual(payloads, [entry["token"]])
                self.assertEqual(self.documents.resolve_token(payloads[0], authenticated=True)["copy_id"], entry["copy_id"])
        fresh = self.app.test_client()
        for route in ("catalog", f"batches/{batch['id']}/download", f"batches/{batch['id']}/manifest"):
            self.assertEqual(fresh.get("/api/documents/" + route).status_code, 200)
        token = self.manifest(batch)[0]["token"]
        denied = fresh.post("/api/documents/lookup", json={"token": token})
        self.assertEqual(denied.status_code, 403)
        self.assertIsNone(self.documents.resolve_token(token))
        self.assertEqual(fresh.post("/api/documents/lookup", json={"token": token}, headers=HEADERS).json["provenance"]["batch_id"], batch["id"])
        with self.assertRaises(Conflict): self.documents.transition(batch["id"], "cancel")

    def test_failed_publication_and_interrupted_retry_retain_allocations(self):
        from document_identity.storage import publish
        batch = self.batch(quantity=1)
        before = self.manifest(batch)
        def fail_manifest(path, content):
            if path.name == "manifest.json": raise StorageError("Injected publication failure")
            return publish(path, content)
        with patch("document_identity.service.publish", side_effect=fail_manifest): self.documents.build_one()
        self.assertEqual(self.documents.get_batch(batch["id"])["status"], "failed")
        with self.assertRaises(Conflict): self.documents.download(batch["id"])
        self.documents.transition(batch["id"], "retry")
        self.documents.build_one()
        self.assertEqual(self.documents.get_batch(batch["id"])["status"], "ready")
        self.assertEqual(self.manifest(batch), before)
        other = self.batch(quantity=1)
        other_manifest = self.manifest(other)
        with self.documents.rosters.lock(), connect(self.path) as db:
            db.execute("UPDATE print_batches SET status='rendering' WHERE id=?", (other["id"],))
        self.documents.build_one()
        self.assertEqual(self.documents.get_batch(other["id"])["status"], "failed")
        self.documents.transition(other["id"], "retry")
        self.assertEqual(self.manifest(other), other_manifest)

    def test_cancel_and_supersede_visible_no_new_tokens(self):
        cancelled = self.batch(quantity=1)
        token = self.manifest(cancelled)[0]["token"]
        self.documents.transition(cancelled["id"], "cancel")
        self.assertEqual(self.documents.resolve_token(token, authenticated=True)["state"], "cancelled")
        self.assertFalse(self.documents.build_one())
        batch = self.ready(quantity=1)
        token = self.manifest(batch)[0]["token"]
        self.documents.transition(batch["id"], "supersede")
        self.assertEqual(self.documents.resolve_token(token, authenticated=True)["state"], "superseded")
        with self.assertRaises(Conflict): self.documents.download(batch["id"])

    def test_identical_arrivals_retries_and_review_never_dispatch(self):
        batch = self.ready(quantity=1)
        pdf = self.documents.download(batch["id"])
        first = self.intake(pdf, "delivery-1")
        again = self.intake(pdf, "delivery-1")
        second = self.intake(pdf, "delivery-2")
        self.assertEqual(first["message_id"], again["message_id"])
        self.assertNotEqual(first["message_id"], second["message_id"])
        self.assertTrue(self.scans.process_one())
        self.assertTrue(self.scans.process_one())
        self.assertFalse(self.scans.process_one())
        self.assertNotIn("scan.receive", HANDLERS)
        for job in (first, second):
            retained = self.inbox.get(job["id"])
            self.assertTrue(retained["instructor_review_required"])
            self.assertEqual(retained["status"], "needs_review")
            self.assertEqual(retained["attempts"], 0)
        item = self.scans.review_item(first["message_id"])
        self.assertEqual(len(item["pages"]), 3)
        self.assertTrue(all(p["duplicate_of_scan_page_id"] for p in self.scans.review_item(second["message_id"])["pages"]))
        self.assertEqual(item["assemblies"][0]["missing"], [])
        chosen = self.scans.select_assembly(item["assemblies"][0]["id"], [p["id"] for p in item["pages"]], False, "Reviewed against originals")
        duplicate = self.scans.review_item(second["message_id"])
        repeated = self.scans.select_assembly(item["assemblies"][0]["id"], [p["id"] for p in duplicate["pages"]], False, "Same content")
        self.assertEqual(chosen["id"], repeated["id"])
        assembled = self.scans.download_assembly(chosen["id"])
        self.assertEqual(len(PdfReader(io.BytesIO(assembled)).pages), 3)
        self.assertTrue(self.inbox.get(first["id"])["instructor_review_required"])

    def test_polled_reads_answer_unchanged_polls_with_304_and_the_lists_can_leave_out_what_they_do_not_show(self):
        """The job and scan reads carry an ETag and answer a matching If-None-Match with an empty 304; the scan's summary and the
        list's input files are the full answers less what they leave out; a page image at full size is the retained raster."""
        batch = self.ready(quantity=1)
        job = self.intake(self.documents.download(batch["id"]))
        self.assertTrue(self.scans.process_one())
        for url in (f"/api/inbox/jobs/{job['id']}", f"/api/documents/messages/{job['message_id']}", f"/api/documents/messages/{job['message_id']}?view=summary"):
            with self.subTest(url=url):
                first = self.client.get(url)
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.headers["Cache-Control"], "no-store")
                tag = first.headers["ETag"]
                unchanged = self.client.get(url, headers={"If-None-Match": tag})
                self.assertEqual((unchanged.status_code, unchanged.data, unchanged.headers["Cache-Control"]), (304, b"", "no-store"))
                stale = self.client.get(url, headers={"If-None-Match": '"stale"'})
                self.assertEqual((stale.status_code, stale.data), (200, first.data))
        # A new event changes the job's body, so its old tag no longer matches.
        tag = self.client.get(f"/api/inbox/jobs/{job['id']}").headers["ETag"]
        with connect(self.path) as db:
            self.inbox.event(db, job["id"], "decode_requested", "Synthetic event")
        self.assertEqual(self.client.get(f"/api/inbox/jobs/{job['id']}", headers={"If-None-Match": tag}).status_code, 200)
        full = self.client.get(f"/api/documents/messages/{job['message_id']}").json
        summary = self.client.get(f"/api/documents/messages/{job['message_id']}?view=summary").json
        self.assertEqual((len(full["pages"]), len(full["assemblies"])), (3, 1))
        self.assertEqual(summary, {**{key: value for key, value in full.items() if key not in ("pages", "assemblies")}, "page_count": 3})
        self.assertEqual(self.scans.review_item(job["message_id"]), full)
        listing = self.client.get("/api/inbox/jobs").json
        inputs = self.client.get("/api/inbox/jobs?files=input").json
        self.assertTrue(any(artifact["role"] == "output" for listed in listing["jobs"] for artifact in listed["artifacts"]))
        for listed in listing["jobs"]:
            self.assertNotIn("events", listed)
            listed["artifacts"] = [artifact for artifact in listed["artifacts"] if artifact["role"] == "input"]
        # The Inbox's own read also says what its list groups scans by (INBOX: scans of one assignment are one row); nothing else differs.
        for listed in inputs["jobs"]:
            if listed.get("scan"):
                if listed["message_id"] == job["message_id"]:
                    self.assertEqual(listed["scan"]["pages"], 3)
                listed["scan"] = {key: value for key, value in listed["scan"].items() if key not in ("assignment", "pages", "papers")}
        self.assertTrue(any(listed["message_id"] == job["message_id"] and listed.get("scan") for listed in inputs["jobs"]))
        self.assertEqual(inputs, listing)
        page = full["pages"][0]
        raster = self.inbox.read_artifact(page["raster_artifact_id"])[1]
        image = f"/api/documents/messages/{job['message_id']}/pages/{page['id']}/image.png"
        self.assertEqual(self.client.get(f"{image}?width=1700").data, raster)
        from PIL import Image, ImageChops
        expected = Image.open(io.BytesIO(raster)).convert("L"); expected.thumbnail((700, 1400))
        small = Image.open(io.BytesIO(self.client.get(f"{image}?width=700").data))
        self.assertEqual((small.size, small.mode), (expected.size, "L"))
        self.assertIsNone(ImageChops.difference(small, expected).getbbox())

    def test_other_reviewed_copies_are_listed_in_copy_id_order(self):
        """The enrollment index (document_identity 012) would list them in insertion order; the query keeps today's id order."""
        section = self.sections[0]
        with self.documents.rosters.lock(), connect(self.path) as db:
            db.execute("INSERT INTO student_identities VALUES('000000001','12345',?)", (timestamp(),))
            db.execute("INSERT INTO student_enrollments VALUES('1234567890',?,'000000001',?)", (section["term_id"], timestamp()))
            db.execute("INSERT INTO roster_memberships VALUES(?,'1234567890')", (section["id"],))
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertTrue({"scan_unlabeled_registrations_copy", "assembly_pages_scan", "printed_copies_enrollment", "submission_choices_student"} <= names)
        batches = [self.ready(quantity=1, students=["1234567890"])]
        for _ in range(3):
            batches.append(self.ready(quantity=1, students=["1234567890"], reprint_of=batches[-1]["id"]))
        for printed in batches:
            job = self.intake(self.documents.download(printed["id"])); self.scans.process_one()
            item = self.scans.review_item(job["message_id"])
            self.scans.select_assembly(item["assemblies"][0]["id"], [p["id"] for p in item["pages"]], False, "Reviewed")
        copies = [printed["copies"][0]["id"] for printed in batches]
        for copy in copies:
            with self.subTest(copy=copy):
                self.assertEqual(self.scans.assembly(copy)["other_reviewed_copies"], sorted(set(copies) - {copy}))

    def test_came_back_in_scans_counts_only_scans_still_in_the_inbox(self):
        """A job says which copies came back in scans; a deleted Inbox submission counts nowhere, and a restore counts again."""
        batch = self.ready(quantity=2)
        pdf = self.documents.download(batch["id"])
        reader, writer = PdfReader(io.BytesIO(pdf)), PdfWriter()
        for index in (0, 1, 2):
            writer.add_page(reader.pages[index])
        output = io.BytesIO(); writer.write(output)
        job = self.intake(output.getvalue())
        self.assertEqual(self.documents.get_batch(batch["id"])["scanned"], {"copies": 0, "total": 2, "scans": [],
                         "unseen": [{"copy_number": 1, "caption": "", "enrollment_id": None}, {"copy_number": 2, "caption": "", "enrollment_id": None}]})
        self.assertTrue(self.scans.process_one())
        scanned = self.documents.get_batch(batch["id"])["scanned"]
        self.assertEqual((scanned["copies"], scanned["total"]), (1, 2))
        self.assertEqual([copy["copy_number"] for copy in scanned["unseen"]], [2])
        self.assertEqual([(scan["job_id"], scan["copies"]) for scan in scanned["scans"]], [(job["id"], 1)])
        listed = lambda: next(item for item in self.documents.catalog()["batches"] if item["id"] == batch["id"])
        self.assertEqual(listed()["scanned"], 1)
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete", reason="Synthetic test")
        scanned = self.documents.get_batch(batch["id"])["scanned"]
        self.assertEqual((scanned["copies"], scanned["scans"], len(scanned["unseen"])), (0, [], 2))
        self.assertEqual(listed()["scanned"], 0)
        self.inbox.restore(job["id"])
        self.assertEqual(self.documents.get_batch(batch["id"])["scanned"]["copies"], 1)
        self.assertEqual(listed()["scanned"], 1)

    def test_a_deleted_scan_does_not_reserve_an_unused_photocopy_destination(self):
        # INBOX: a removed scan never counts as a paper already seen.
        from document_identity.photocopies import PhotocopyService
        batch = self.ready(quantity=2)
        job = self.intake(self.documents.download(batch['id']))
        self.assertTrue(self.scans.process_one())
        with connect(self.path) as db:
            self.assertEqual(PhotocopyService._unused(db, batch['id'], [1], set()), (None, {}))
        self.inbox.remove(job['id'], self.inbox.get(job['id'])['version'], 'delete')
        with connect(self.path) as db:
            copy_id, stamps = PhotocopyService._unused(db, batch['id'], [1], set())
        self.assertEqual(copy_id, batch['copies'][0]['id'])
        self.assertIn(1, stamps)

    def test_photocopied_papers_whose_faint_codes_are_read_again_join_their_own_copies(self):
        """Quiz 7 (requirement, 2026-11-06): photocopied codes came back too faint to read.
        DOCUMENTS "FAINT MARKS" reads them again on a scan inspected before it (a dry run first), QUIZZES "PHOTOCOPIED STAMPS" moves
        each paper to its own copy, a later page of a paper already moved joins that paper (A LATER PAGE OF A MOVED PAPER), and a copy
        whose paper the scan already assembled is kept by that paper, so a photocopy never stays on it."""
        batch = self.ready(quantity=3, request_id=f"quiz:{uuid.uuid4()}:A")  # a quiz job: its copies are named from the paper
        copies = [copy["id"] for copy in batch["copies"]]
        with connect(self.path) as db:
            stamps = {(r["printed_copy_id"], r["page_number"]): (r["id"], r["token"]) for r in db.execute("SELECT * FROM page_stamps")}
        source = copies[0]
        sides = sorted(n for c, n in stamps if c == source)
        self.assertEqual(sides, [1, 2, 3])
        job = self.inbox.get(self.intake(b"%PDF-fixture" + uuid.uuid4().bytes)["id"])
        original = next(a for a in job["artifacts"] if a["role"] == "input")
        message, index, faint = job["message_id"], 0, {}
        # Paper 0 is the printed copy, every code read; paper 1 a photocopy whose page 1 code read and later ones did not; paper 2 a
        # photocopy with no code read at all.
        for paper in range(3):
            for side in sides:
                index += 1
                read = paper == 0 or (paper == 1 and side == 1)
                if not read:
                    faint[index] = stamps[(source, side)][1]
                self.scans._retain_page(job, original, {"index": index, "pdf": f"copied-{paper}-side-{side}".encode(), "png": b"png", "decoder": {},
                                                        "blank": False, "candidates": [{"payload": stamps[(source, side)][1]}] if read else []})
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (message,))
            # The printed copy's paper is already assembled from this scan.
            revision, when = str(uuid.uuid4()), timestamp()
            db.execute("INSERT INTO assembly_revisions VALUES(?,?,?,?,?,?,?,?,?)", (revision, source, 1, "[]", sha(b"[]"), 0, "workspace", "fixture", when))
            for row in db.execute("SELECT id,source_page_index FROM scan_pages WHERE message_id=? AND source_page_index<=3", (message,)).fetchall():
                db.execute("INSERT INTO assembly_pages VALUES(?,?,?,?)", (revision, row["id"], row["source_page_index"], when))

        def where():
            with connect(self.path) as db:
                return {r["source_page_index"]: (r["printed_copy_id"], r["page_number"], r["review_state"]) for r in db.execute(
                    """SELECT sp.source_page_index,p.printed_copy_id,p.page_number,sp.review_state FROM scan_pages sp
                       LEFT JOIN page_stamps p ON p.id=sp.page_stamp_id WHERE sp.message_id=?""", (message,))}
        # Paper 1's page 1 moves to its own copy although paper 0 comes first: the assembled paper keeps copy 1.
        photocopies = self.scans.photocopies
        self.assertTrue(photocopies.process_one())
        self.assertEqual(where()[4][:2], (copies[1], 1))

        def extract(operation, content, span):
            self.assertEqual(operation, "extract")
            return [{"index": i, "pdf": b"", "png": b"", "blank": False, "candidates": [{"payload": faint[i]}] if i in faint else [],
                     "decoder": {"name": "zxing-cpp", "dpi": 300, **({"threshold": 100} if i in faint else {})}}
                    for i in range(span["first"], span["last"] + 1)]
        with patch("document_identity.scans.process.run", side_effect=extract):
            dry = self.scans.read_faint_marks(message)
            self.assertEqual((dry["checked"], sorted(r["page"] for r in dry["read"])), (len(faint), sorted(faint)))
            self.assertEqual({where()[i][0] for i in faint}, {None})  # a dry run changes nothing
            written = self.scans.read_faint_marks(message, write=True)
            self.assertEqual(self.scans.read_faint_marks(message), {"checked": 0, "read": []})  # nothing left to read again
        self.assertEqual(sorted(r["page"] for r in written["read"]), sorted(faint))
        with connect(self.path) as db:
            observed = db.execute("SELECT status,candidates_json FROM scan_pages WHERE message_id=? AND source_page_index=5", (message,)).fetchone()
            event = json.loads(db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                WHERE j.message_id=? AND e.kind='faint_marks_read'""", (message,)).fetchone()[0])
        self.assertEqual((observed["status"], json.loads(observed["candidates_json"])), ("manual", []))  # the original observation stays
        self.assertEqual(sorted(page["page"] for page in event["pages"]), sorted(faint))
        self.assertEqual({where()[i][2] for i in faint}, {"conflict"})

        self.assertTrue(photocopies.process_one())
        self.assertFalse(photocopies.process_one())
        pages = where()
        for paper, copy in enumerate(copies):
            self.assertEqual([pages[paper * 3 + side][:2] for side in sides], [(copy, side) for side in sides])
        self.assertNotIn("conflict", {state for _, _, state in pages.values()})
        with connect(self.path) as db:
            moves = json.loads(db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                WHERE j.message_id=? AND e.kind='photocopied_papers' ORDER BY e.id DESC LIMIT 1""", (message,)).fetchone()[0])["moved"]
        self.assertEqual(sorted((m["to_copy_id"], [p["page_number"] for p in m["pages"]], bool(m.get("joined"))) for m in moves),
                         sorted([(copies[1], [2, 3], True), (copies[2], [1, 2, 3], False)]))

    def test_empty_sides_of_a_scan_inspected_before_grey_paper_become_blank_sides(self):
        """The take-home (requirement, 2026-11-11): four scans were inspected while every empty back scanned in grey read as a written page.
        DOCUMENTS "GREY PAPER" measures such a scan's unlabeled sides again (a dry run first) and makes the empty ones blank sides."""
        batch = self.ready(quantity=1)
        with connect(self.path) as db:
            token = db.execute("SELECT token FROM page_stamps WHERE printed_copy_id=? AND page_number=1", (batch["copies"][0]["id"],)).fetchone()[0]
        job = self.inbox.get(self.intake(b"%PDF-fixture" + uuid.uuid4().bytes)["id"])
        original = next(a for a in job["artifacts"] if a["role"] == "input")
        message = job["message_id"]

        def png(side):
            content = io.BytesIO()
            side.save(content, "PNG")
            return content.getvalue()
        # A labeled front, its empty back, a back with a line written on it, and an empty side on which some other code was read.
        sides = [(grey_side(marks=[(-150, 4000)]), [{"payload": token}]), (grey_side(shadow=True), []),
                 (grey_side(marks=[(-60, 400)]), []), (grey_side(), [{"payload": "https://example.test"}])]
        for index, (side, candidates) in enumerate(sides, 1):
            self.scans._retain_page(job, original, {"index": index, "pdf": f"side-{index}".encode(), "png": png(side), "decoder": {},
                                                    "blank": False, "candidates": candidates})

        def states():
            with connect(self.path) as db:
                return [tuple(r) for r in db.execute("SELECT status,review_state FROM scan_pages WHERE message_id=? ORDER BY source_page_index", (message,))]
        before = states()
        self.assertEqual([state[0] for state in before], ["resolved", "unreadable", "unreadable", "unreadable"])
        with self.assertRaises(Conflict):
            self.scans.read_blank_sides(message)  # still being inspected
        with connect(self.path) as db:
            db.execute("UPDATE document_decode_jobs SET status='complete' WHERE message_id=?", (message,))
        dry = self.scans.read_blank_sides(message)
        self.assertEqual((dry["checked"], [side["page"] for side in dry["blank"]], states()), (2, [2], before))  # a dry run changes nothing
        written = self.scans.read_blank_sides(message, write=True, actor="cli:test")
        self.assertEqual([side["page"] for side in written["blank"]], [2])
        self.assertEqual(states(), [before[0], ("blank", "ignored"), before[2], before[3]])
        with connect(self.path) as db:
            [event] = [json.loads(row[0]) for row in db.execute("""SELECT e.detail FROM inbox_events e JOIN inbox_jobs j ON j.id=e.job_id
                WHERE j.message_id=? AND e.kind='blank_sides_read'""", (message,))]
        self.assertEqual((event["actor"], event["pages"]), ("cli:test", written["blank"]))
        self.assertEqual(self.scans.read_blank_sides(message, write=True), {"checked": 1, "blank": []})  # the written side is left

    def test_catalog_lifecycle_follows_the_shared_stage_rule(self):
        """Each assignment in its course's current term: Paper, Seating and Print as the exam hub derives them."""
        stages = lambda assignment: [row for row in self.documents.catalog()["lifecycle"] if row["assignment_id"] == assignment["id"]]
        blank = self.documents.create_assignment(self.definition, "quiz-1", "Quiz 1")
        [row] = stages(blank)
        self.assertEqual(row["stages"], {"paper": "idle", "seating": "idle", "print": "idle"})
        self.assertEqual((row["term_id"], row["term_label"], row["active"]), (self.sections[0]["term_id"], "Fall 2026", False))
        self.assertEqual((row["exam_event_id"], row["grading_plan_id"], row["last_printed_at"]), (None, None, None))
        # A registration job (papers matched without labels) is never printing.
        self.batch(quantity=1, request_id=f"unlabeled:run-1:{self.sections[0]['id']}:0123456789abcdef")
        [row] = stages(self.assignment)
        self.assertEqual(row["stages"], {"paper": "done", "seating": "idle", "print": "idle"})
        self.assertFalse(row["active"], "A registration job alone does not make the assignment active in the term")
        waiting = self.batch(quantity=1)
        [row] = stages(self.assignment)
        self.assertEqual(row["stages"], {"paper": "done", "seating": "done", "print": "started"})
        self.assertTrue(row["active"])
        self.assertEqual(row["last_printed_at"], waiting["created_at"])
        # What was printed, over current print jobs only (the registration job above never counts): copies, versions, one stack or not.
        self.assertEqual((row["printed_copies"], row["printed_versions"], row["printed_stack"]), (1, ["A"], False))
        self.assertEqual((row["grading_entry_plan_id"], row["quiz_draft_id"]), (None, None))
        self.documents.transition(waiting["id"], "cancel")
        [row] = stages(self.assignment)
        self.assertEqual(row["stages"]["print"], "idle")
        self.assertEqual((row["printed_copies"], row["printed_versions"], row["printed_stack"]), (0, [], False))
        self.batch(quantity=1)
        while self.documents.build_one():
            pass
        self.assertEqual(stages(self.assignment)[0]["stages"], {"paper": "done", "seating": "done", "print": "done"})
        # A course with no current term has no lifecycle row; its card shows the paper alone.
        self.add_course(code="MATH 150", is_current=False)
        other = next(item for item in self.documents.catalog()["definitions"] if item["code"] == "MATH 150")
        self.assertEqual(stages(self.documents.create_assignment(other["id"], "hw-1", "Homework 1")), [])

    def test_the_print_page_lists_every_print_job_and_the_newest_registration_jobs(self):
        """Registration jobs (one scan can issue dozens) never push a print job off the list: the wizard's "Already printed" reads it."""
        printed = self.batch(quantity=1)
        with connect(self.path) as db:
            db.execute("UPDATE print_batches SET created_at='2026-01-01T00:00:00+00:00' WHERE id=?", (printed["id"],))
        registration = [self.batch(quantity=1, request_id=f"unlabeled:run-{n}:{self.sections[0]['id']}:0123456789abcdef") for n in range(101)]
        listed = self.documents.catalog()["batches"]
        ids = [b["id"] for b in listed]
        self.assertIn(printed["id"], ids)
        self.assertEqual(ids[-1], printed["id"])  # newest first
        self.assertEqual(sum(1 for b in listed if b["origin"] == "registration"), 100)
        self.assertNotIn(registration[0]["id"], ids)  # the oldest registration job
        self.assertEqual([b["created_at"] for b in listed], sorted((b["created_at"] for b in listed), reverse=True))

    def test_starter_files_for_latex_are_downloads(self):
        for name, template in (("main.tex", "example.tex"), ("facultyassignment.cls", "facultyassignment.cls")):
            response = self.client.get(f"/api/documents/starter/{name}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'attachment; filename={name}', response.headers["Content-Disposition"])
            self.assertEqual(response.data, (FIXTURES.parent / "templates" / template).read_bytes())
        self.assertEqual(self.client.get("/api/documents/starter/../api.py").status_code, 404)
        self.assertEqual(self.client.get("/api/documents/starter/build.log").status_code, 404)

    def test_mixed_shuffled_multi_message_and_changed_observations(self):
        batch = self.ready(quantity=2)
        pdf = self.documents.download(batch["id"])
        def packet(indices, changed=False):
            reader, writer = PdfReader(io.BytesIO(pdf)), PdfWriter()
            for index in indices:
                page = writer.add_page(reader.pages[index])
                if changed:
                    from reportlab.pdfgen.canvas import Canvas
                    mark = io.BytesIO(); drawing = Canvas(mark, pagesize=(612,792)); drawing.drawString(100,400,"Changed student work"); drawing.save()
                    page.merge_page(PdfReader(mark).pages[0])
            output = io.BytesIO(); writer.write(output); return output.getvalue()
        first = self.intake(packet([5, 0, 3, 2]))
        self.intake(packet([1, 4]))
        self.scans.process_one(); self.scans.process_one()
        assemblies = self.scans.review_item(first["message_id"])["assemblies"]
        self.assertEqual(len(assemblies), 2)
        for a in assemblies:
            self.assertFalse(a["missing"])
            self.assertEqual([p["page_number"] for p in a["pages"]], [1,2,3])
        changed = self.intake(packet([0], changed=True)); self.scans.process_one()
        item = self.scans.review_item(changed["message_id"])
        self.assertEqual(item["assemblies"][0]["conflicts"], [1])
        self.assertEqual(item["pages"][0]["review_state"], "conflict")
        self.assertIsNone(item["pages"][0]["duplicate_of_scan_page_id"])
        with self.assertRaises(Conflict): self.scans.select_assembly(item["assemblies"][0]["id"], [item["pages"][0]["id"]], False, "Missing pages")
        partial = self.scans.select_assembly(item["assemblies"][0]["id"], [item["pages"][0]["id"]], True, "Partial explicitly accepted")
        self.assertEqual(partial["partial"], 1)

    def test_equal_rasters_with_different_page_bytes_require_review(self):
        from pypdf.generic import DecodedStreamObject
        batch = self.ready(quantity=1)
        original = self.documents.download(batch["id"])
        first = self.intake(original); self.scans.process_one()
        writer = PdfWriter()
        page = writer.add_page(PdfReader(io.BytesIO(original)).pages[0])
        stream = DecodedStreamObject()
        stream.set_data(page.get_contents().get_data() + b"\n% Different retained content, same rendered pixels\n")
        page.replace_contents(stream)
        output = io.BytesIO(); writer.write(output)
        second = self.intake(output.getvalue()); self.scans.process_one()
        before = self.scans.review_item(first["message_id"])["pages"][0]
        item = self.scans.review_item(second["message_id"])
        after = item["pages"][0]
        self.assertEqual(self.inbox.read_artifact(before["raster_artifact_id"])[1], self.inbox.read_artifact(after["raster_artifact_id"])[1])
        self.assertNotEqual(before["content_sha256"], after["content_sha256"])
        self.assertEqual(after["content_sha256"], sha(self.inbox.read_artifact(after["page_artifact_id"])[1]))
        self.assertEqual(after["review_state"], "conflict")
        self.assertIsNone(after["duplicate_of_scan_page_id"])
        self.assertEqual(item["assemblies"][0]["conflicts"], [1])

    def test_mixed_sections_and_versions_keep_separate_assemblies(self):
        versions = [self.version, self.version_for("Mixed-A4", "a4")]
        batches = [self.ready(quantity=1, version=version, section=section) for version in versions for section in self.sections]
        readers = [PdfReader(io.BytesIO(self.documents.download(batch["id"]))) for batch in batches]
        def packet(order):
            writer = PdfWriter()
            for batch_index, page_index in order:
                writer.add_page(readers[batch_index].pages[page_index])
            output = io.BytesIO(); writer.write(output)
            return output.getvalue()
        first = self.intake(packet([(2,2),(1,0),(0,2),(3,0),(0,1),(0,0),(1,2),(3,2),(2,0)]))
        self.scans.process_one()
        item = self.scans.review_item(first["message_id"])
        expected = {batch["copies"][0]["id"]: batch for batch in batches}
        for assembly in item["assemblies"]:
            batch = expected[assembly["id"]]
            self.assertEqual(assembly["version_id"], batch["assignment_version_id"])
            self.assertEqual(assembly["section_id"], batch["section_id"])
            self.assertEqual(assembly["missing"], [] if batch["id"] == batches[0]["id"] else [2])
        second = self.intake(packet([(3,1),(2,1),(1,1)])); self.scans.process_one()
        refreshed = self.scans.review_item(first["message_id"])
        self.assertEqual(len(refreshed["assemblies"]), 4)
        self.assertTrue(all(not a["missing"] and not a["conflicts"] for a in refreshed["assemblies"]))
        different_copy_page = self.scans.review_item(second["message_id"])["pages"][0]["id"]
        with self.assertRaises(ValueError):
            self.scans.select_assembly(batches[0]["copies"][0]["id"], [different_copy_page], True, "Cannot mix copies")

    def test_unknown_ambiguous_unreadable_blank_and_manual_alias(self):
        batch = self.ready(quantity=1)
        manifest = self.manifest(batch)
        unknown = tokens.generate_token()
        for payloads, expected in (([unknown], "unknown"), (["T1-malformed"], "malformed"),
                                  ([manifest[0]["token"],manifest[1]["token"]], "ambiguous"), ([], "unreadable")):
            _, state = self.scans.classify({"candidates": [{"payload": p} for p in payloads], "blank": False})
            self.assertEqual(state, expected)
        self.assertEqual(self.scans.classify({"candidates": [], "blank": True})[1], "blank")
        # Unstamped blank verso does not inherit the preceding cover's identity.
        reader = PdfReader(io.BytesIO(self.documents.download(batch["id"]))); writer = PdfWriter()
        writer.add_page(reader.pages[0]); writer.add_blank_page(612,792)
        out = io.BytesIO(); writer.write(out)
        job = self.intake(out.getvalue()); self.scans.process_one()
        item = self.scans.review_item(job["message_id"])
        self.assertEqual(item["pages"][1]["status"], "blank")
        self.assertIsNone(item["pages"][1]["page_stamp_id"])
        self.assertEqual(item["assemblies"][0]["missing"], [2,3])
        updated = self.scans.recover(item["pages"][1]["id"], manifest[1]["recovery_alias"], "Read exact alias from original")
        self.assertEqual(updated["pages"][1]["status"], "manual")
        events = self.inbox.get(job["id"])["events"]
        self.assertTrue(any(e["kind"] == "manual_page_recovery" and "workspace" in e["detail"] for e in events))
        self.assertTrue(updated["instructor_review_required"])


    def test_a_page_recovered_by_alias_follows_the_intake_rule_for_changed_content(self):
        # bugs-backend-7: a page given its identity by hand is compared like a barcode read, among scans still in the Inbox.
        batch = self.ready(quantity=1)
        manifest = self.manifest(batch)
        reader = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        writer = PdfWriter(); writer.add_page(reader.pages[0]); out = io.BytesIO(); writer.write(out)
        first = self.intake(out.getvalue())
        while self.scans.process_one():
            pass
        # A second scan whose first page (different content) the instructor recovers as page 1 by its printed alias.
        writer = PdfWriter(); writer.add_blank_page(612, 792); writer.add_blank_page(612, 792); out = io.BytesIO(); writer.write(out)
        second = self.intake(out.getvalue() + b"%changed")
        while self.scans.process_one():
            pass
        page = self.scans.review_item(second["message_id"])["pages"][0]
        updated = self.scans.recover(page["id"], manifest[0]["recovery_alias"], "Read the alias printed beside the label")
        self.assertEqual((updated["pages"][0]["status"], updated["pages"][0]["review_state"]), ("manual", "conflict"))
        self.assertIsNone(updated["pages"][0]["duplicate_of_scan_page_id"])
        self.assertEqual(self.scans.review_item(first["message_id"])["pages"][0]["review_state"], "conflict")
        with connect(self.path) as db:
            steps = [step["text"] for step in self.scans.overview(db, second["message_id"])["steps"] if step["kind"] == "check"]
        self.assertIn("1 labeled page also arrived in another scan with different content, usually a re-scan.", steps)
        # A deleted scan never counts: recovered again in a third scan once the first two are gone, the page is a plain candidate.
        for job in (first, second):
            self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete", "Scanned again")
        writer = PdfWriter(); writer.add_blank_page(612, 792); out = io.BytesIO(); writer.write(out)
        third = self.intake(out.getvalue() + b"%third")
        while self.scans.process_one():
            pass
        page = self.scans.review_item(third["message_id"])["pages"][0]
        updated = self.scans.recover(page["id"], manifest[0]["recovery_alias"], "Read the alias printed beside the label")
        self.assertEqual((updated["pages"][0]["review_state"], updated["pages"][0]["duplicate_of_scan_page_id"]), ("candidate", None))

    def test_a_page_that_arrives_after_its_paper_was_accepted_as_partial_flags_that_paper(self):
        # bugs-backend-1: an accepted partial paper is settled (no "pages that no scan has shown" for it), but a page of it that
        # arrives later is not silently left out: the paper is flagged until its reviewed paper is saved again.
        batch = self.ready(quantity=1)
        reader = PdfReader(io.BytesIO(self.documents.download(batch["id"])))
        def scan(*indexes, tail=b""):
            writer = PdfWriter()
            for index in indexes:
                writer.add_page(reader.pages[index])
            out = io.BytesIO(); writer.write(out)
            job = self.intake(out.getvalue() + tail)
            while self.scans.process_one():
                pass
            return job["message_id"]
        first = scan(0)
        item = self.scans.review_item(first)
        copy = item["assemblies"][0]["id"]
        def checks(message):
            with connect(self.path) as db:
                return [step["text"] for step in self.scans.overview(db, message)["steps"] if step["tool"] == "assemblies" and step["kind"] == "check"]
        self.assertEqual(checks(first), ["1 copy has pages that no scan has shown with a label."])
        self.scans.select_assembly(copy, [item["pages"][0]["id"]], True, "Only page 1 was handed in")
        self.assertEqual(checks(first), [])
        later = scan(1, tail=b"%late")
        expected = ["1 paper accepted with missing pages has more pages now: save its reviewed paper again to include them."]
        self.assertEqual(checks(later), expected)
        self.assertEqual(checks(first), expected)  # per paper: every scan holding it says so
        # Saved again with the page that arrived, it is settled once more.
        assembly = self.scans.assembly(copy)
        chosen = [next(o["id"] for o in page["observations"] if o["status"] in ("resolved", "manual")) for page in assembly["pages"] if page["observations"]]
        self.assertTrue(assembly["grown"], "Review lists the paper the check counts")
        self.scans.select_assembly(copy, chosen, True, "Page 2 arrived later")
        self.assertEqual(checks(later), [])
        self.assertFalse(self.scans.assembly(copy)["grown"])
        # Like the check, a paper's pages count only from scans still in the Inbox: page 3 arriving flags it, and deleting
        # that scan from the Inbox settles it again, on Review as in the check.
        last = scan(2, tail=b"%last")
        self.assertEqual((checks(first), self.scans.assembly(copy)["grown"]), (expected, True))
        job = self.inbox.get_by_message(last)
        self.inbox.remove(job["id"], self.inbox.get(job["id"])["version"], "delete", "Scanned by mistake")
        self.assertEqual((checks(first), self.scans.assembly(copy)["grown"]), ([], False))
        # Each observation says where it was scanned, so the review names pages as the instructor sees them.
        observation = self.scans.assembly(copy)["pages"][1]["observations"][0]
        self.assertEqual((observation["source_page_index"], observation["source_filename"]), (1, "scan.pdf"))


    def test_page_rows_say_which_pages_are_bubble_sheets_and_only_those(self):
        # The Scan page leaves a bubble sheet to the Bubble sheets stage (it carries no label or alias to recover); a scan without
        # one reads exactly as before, with no such key.
        from bubble_fixture import pdf as sheets_pdf, sheet
        batch = self.ready(quantity=1)
        writer = PdfWriter()
        for source in PdfReader(io.BytesIO(self.documents.download(batch["id"]))).pages:
            writer.add_page(source)
        labeled = io.BytesIO(); writer.write(labeled)
        writer.add_page(PdfReader(io.BytesIO(sheets_pdf([sheet({1: "A"})]))).pages[0])
        mixed = io.BytesIO(); writer.write(mixed)
        plain, with_sheet = self.intake(labeled.getvalue()), self.intake(mixed.getvalue())
        while self.scans.process_one():
            pass
        rows = self.scans.review_item(with_sheet["message_id"])["pages"]
        self.assertEqual([(row["source_page_index"], row["status"], row.get("bubble_sheet")) for row in rows],
                         [(1, "resolved", None), (2, "resolved", None), (3, "resolved", None), (4, "unreadable", True)])
        self.assertFalse(any("bubble_sheet" in row for row in self.scans.review_item(plain["message_id"])["pages"]))
        with connect(self.path) as db:
            steps = self.scans.overview(db, with_sheet["message_id"])["steps"]
        self.assertFalse([step for step in steps if step["tool"] == "pages"], "nothing on the Scan stage needs a look")


class DocumentStorageTests(DocumentTests):
    managed = True

    def test_current_descendant_retains_canonical_and_nightly_archives_section(self):
        self.add_course(term="Spring", year=2027, sections=[501])
        batch = self.ready(quantity=1)
        pdf = self.documents.download(batch["id"])
        self.intake(pdf); self.scans.process_one()
        self.storage.backup_all()
        changed = self.client.patch(f"/api/courses/{self.course['id']}", json={"version": self.course["version"], "is_current": False}, headers=HEADERS)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.storage.reconcile_all()
        definition = self.storage.definition_storage
        section = self.storage.section_storage
        self.assertTrue(definition.hot_path(self.definition).exists())
        self.assertTrue(section.hot_path(batch["section_id"]).exists())
        self.assertEqual(section.row(batch["section_id"])["storage_status"], "staged")
        self.storage.backup_all()
        self.assertTrue(definition.hot_path(self.definition).exists())
        self.assertFalse(section.hot_path(batch["section_id"]).exists())
        self.assertEqual(self.documents.download(batch["id"]), pdf)
        # Archived section writes restore to NVMe first; other current terms remain.
        self.batch(quantity=1)
        self.assertTrue(section.hot_path(batch["section_id"]).exists())
        self.assertEqual(section.row(batch["section_id"])["storage_status"], "staged")

    def test_source_tamper_blocks_backup_and_missing_hot_copy_never_restores_old_snapshot(self):
        manager = self.storage.definition_storage
        self.storage.backup_all()
        with self.documents.rosters.lock(), self.documents.files.files("definition", self.definition, write=True) as root:
            (root / Path(self.version["source_path"]).parent / "sources/asset.txt").write_bytes(b"changed")
        with self.assertRaises(StorageError): self.storage.backup_all()
        self.assertTrue(manager.hot_path(self.definition).exists())
        import shutil
        shutil.rmtree(manager.hot_path(self.definition))
        with self.documents.rosters.lock(), self.assertRaises(StorageError): manager.reconcile_course(self.definition)


if __name__ == "__main__":
    unittest.main()
