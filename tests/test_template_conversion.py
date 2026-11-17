"""Actual bounded PDF, DOCX and LaTeX template conversions using synthetic files."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from document_identity import template_conversion as conversion


def pdf_bytes(pages=1, size=(300, 400)):
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=size, invariant=1)
    for page in range(pages):
        pdf.drawString(30, size[1] - 45, f"Template question on page {page + 1}")
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def docx_bytes():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>''')
        archive.writestr("_rels/.rels", '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
        archive.writestr("word/document.xml", '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>DOCX first page</w:t></w:r></w:p><w:p><w:r><w:br w:type="page"/></w:r></w:p><w:p><w:r><w:t>DOCX second page</w:t></w:r></w:p><w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>''')
    return out.getvalue()


class TemplateConversionTests(unittest.TestCase):
    def assert_pages(self, result, count):
        self.assertEqual(len(result["pages"]), count)
        self.assertEqual(len(PdfReader(io.BytesIO(result["pdf"])).pages), count)
        self.assertNotIn("/FacultyLayout", PdfReader(io.BytesIO(result["pdf"])).metadata)
        self.assertTrue(result["conversion"]["reference_only"])
        self.assertFalse(result["conversion"]["physical_layout_qualified"])
        self.assertFalse(result["conversion"]["network"])
        for index, page in enumerate(result["pages"], 1):
            self.assertEqual(page["number"], index)
            with Image.open(io.BytesIO(page["png"])) as image:
                self.assertEqual(image.size, (page["width_px"], page["height_px"]))
            self.assertLessEqual(abs(page["width_px"] - page["width_pt"] * 300 / 72), 1)
            self.assertLessEqual(abs(page["height_px"] - page["height_pt"] * 300 / 72), 1)

    def test_pdf_retains_original_and_normalizes_visible_crop_rotation(self):
        writer = PdfWriter()
        page = writer.add_page(PdfReader(io.BytesIO(pdf_bytes())).pages[0])
        page.cropbox.lower_left = (10, 20)
        page.cropbox.upper_right = (290, 380)
        page.rotate(90)
        source = io.BytesIO()
        writer.write(source)
        data = source.getvalue()
        result = conversion.convert_template({"test.pdf": data})
        self.assert_pages(result, 1)
        self.assertEqual(result["sources"], {"test.pdf": data})
        page = result["pages"][0]
        self.assertEqual((page["width_pt"], page["height_pt"]), (360, 280))
        self.assertIn("Template question", page["text"])
        normalized = PdfReader(io.BytesIO(result["pdf"])).pages[0]
        self.assertEqual(normalized.rotation, 0)
        self.assertEqual(tuple(normalized.mediabox), (0, 0, 360, 280))
        self.assertEqual(normalized.mediabox, normalized.cropbox)

    def test_pdf_pages_keep_order_and_native_text(self):
        result = conversion.convert_template({"pages.pdf": pdf_bytes(2)})
        self.assert_pages(result, 2)
        self.assertIn("page 1", result["pages"][0]["text"])
        self.assertIn("page 2", result["pages"][1]["text"])

    def test_docx_uses_actual_offline_writer_and_retains_page_breaks(self):
        source = docx_bytes()
        result = conversion.convert_template({"exam.docx": source})
        self.assert_pages(result, 2)
        self.assertEqual(result["sources"], {"exam.docx": source})
        self.assertIn("LibreOffice", result["conversion"]["office"])
        self.assertIn("DOCX first page", result["pages"][0]["text"])
        self.assertIn("DOCX second page", result["pages"][1]["text"])
        self.assertEqual((result["pages"][0]["width_pt"], result["pages"][0]["height_pt"]), (612, 792))

    def test_docx_reflowed_away_from_words_own_page_count_is_refused(self):
        def with_properties(application, pages):
            source = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(docx_bytes())) as original, zipfile.ZipFile(source, "w") as changed:
                for item in original.infolist():
                    changed.writestr(item, original.read(item.filename))
                changed.writestr("docProps/app.xml", f'''<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>{application}</Application><Pages>{pages}</Pages></Properties>''')
            return source.getvalue()
        # Word laid out the same two pages: the conversion is accepted and says so.
        result = conversion.convert_template({"exam.docx": with_properties("Microsoft Office Word", 2)})
        self.assert_pages(result, 2)
        self.assertEqual(result["conversion"]["word_page_count"], 2)
        # Word laid it out on three pages, this converter on two: every answer area would be on the wrong page.
        with self.assertRaisesRegex(ValueError, "Word laid this document out on 3 pages, but converting it here produced 2.*Save As → PDF"):
            conversion.convert_template({"exam.docx": with_properties("Microsoft Office Word", 3)})
        # Another editor's count, an absurd one, or none at all is no evidence, so nothing is refused for it.
        for application, pages in (("LibreOffice/24.2", 3), ("Microsoft Office Word", 0), ("Microsoft Office Word", "many")):
            self.assertIsNone(conversion.convert_template({"exam.docx": with_properties(application, pages)})["conversion"]["word_page_count"])
        self.assertIsNone(conversion.convert_template({"exam.docx": docx_bytes()})["conversion"]["word_page_count"])

    def test_docx_equations_are_rendered_by_math_component(self):
        source = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(docx_bytes())) as original, zipfile.ZipFile(source, "w") as changed:
            for item in original.infolist():
                content = original.read(item)
                if item.filename == "word/document.xml":
                    xml = content.decode().replace('<w:document ', '<w:document xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ')
                    xml = xml.replace('<w:body>', '<w:body><w:p><m:oMath><m:r><m:t>123+x=456</m:t></m:r></m:oMath></w:p>')
                    content = xml.encode()
                changed.writestr(item, content)
        result = conversion.convert_template({"equations.docx": source.getvalue()})
        self.assertIn("123+x=456", "".join(result["pages"][0]["text"].split()))
        self.assertTrue(result["conversion"]["office_math"])
        self.assertEqual(result["conversion"]["source_equation_objects"], 1)
        # Refuse equation loss before launching Office if an older/custom
        # runtime lacks Math. Only synthetic ZIP data is parsed in this test.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "input.docx").write_bytes(source.getvalue())
            with patch.object(Path, "is_file", return_value=False), patch.object(conversion.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "missing LibreOffice Math"):
                    conversion._convert_docx(root)
                run.assert_not_called()

    def test_latex_arbitrary_filename_and_explicit_entrypoint_do_not_replace_sources(self):
        source = br'\documentclass{article}\begin{document}\input{part.tex}\end{document}'
        files = {"exam.latex": source, "part.tex": b"Chosen entrypoint", "main.tex": b"Not the chosen entrypoint"}
        with self.assertRaisesRegex(ValueError, "entrypoint"):
            conversion.convert_template(files)
        result = conversion.convert_template(files, entrypoint="exam.latex")
        self.assert_pages(result, 1)
        self.assertIn("Chosen entrypoint", result["pages"][0]["text"])
        for name, content in files.items():
            self.assertEqual(result["sources"][name], content)
        self.assertIn("_conversion/build.log", result["sources"])
        self.assertFalse(result["conversion"]["compiler"]["shell_escape"])

    def test_single_tex_auto_discovers_toolchain_without_mutating_environment(self):
        source = br'\documentclass{article}\begin{document}Standalone TeX\end{document}'
        scratch = tempfile.gettempdir()
        with patch.dict(os.environ, {"TMPDIR": scratch}, clear=True):
            result = conversion.convert_template({"custom-name.tex": source})
            self.assertNotIn("FACULTY_TEXLIVE_ROOT", os.environ)
        self.assert_pages(result, 1)
        self.assertIn("Standalone TeX", result["pages"][0]["text"])

    def test_input_paths_formats_totals_and_entrypoints_are_bounded(self):
        invalid = ({}, {"../test.pdf": b"pdf"}, {"/test.pdf": b"pdf"}, {"a//b.pdf": b"pdf"},
                   {"a\\b.pdf": b"pdf"}, {"a\x00.pdf": b"pdf"}, {"archive.zip": b"zip"},
                   {"custom.sty": b"style"}, {"test.pdf": b"x" * (conversion.MAX_INPUT + 1)},
                   {"test.pdf": b"pdf", "second.pdf": b"pdf"}, {"photo.png": b"image"})
        with patch.object(conversion, "_run", side_effect=AssertionError("Invalid inputs must not run a native parser")):
            for files in invalid:
                with self.subTest(names=list(files)), self.assertRaises(ValueError):
                    conversion.convert_template(files)
            with self.assertRaises(ValueError):
                conversion.convert_template({"test.tex": b"source"}, entrypoint="missing.tex")

    def test_encrypted_oversized_and_excess_page_pdfs_fail_without_source_echo(self):
        encrypted = PdfWriter()
        encrypted.add_blank_page(width=300, height=400)
        encrypted.encrypt("SECRET-test-password")
        out = io.BytesIO()
        encrypted.write(out)
        too_many = PdfWriter()
        for _ in range(101):
            too_many.add_blank_page(width=72, height=72)
        many = io.BytesIO()
        too_many.write(many)
        for data in (out.getvalue(), many.getvalue(), pdf_bytes(size=(10000, 10000)), b"SECRET malformed PDF"):
            with self.subTest(size=len(data)), self.assertRaises(ValueError) as error:
                conversion.convert_template({"input.pdf": data})
            self.assertNotIn("SECRET", str(error.exception))

    def test_docx_macro_and_missing_converter_are_honest_errors(self):
        output = io.BytesIO(docx_bytes())
        with zipfile.ZipFile(output, "a") as archive:
            archive.writestr("word/vbaProject.bin", b"not executed")
        with self.assertRaisesRegex(ValueError, "without macros"):
            conversion.convert_template({"exam.docx": output.getvalue()})
        with patch.dict(os.environ, {"FACULTY_LIBREOFFICE_ROOT": "/nonexistent-converter"}), self.assertRaisesRegex(ValueError, "unavailable"):
            conversion.convert_template({"exam.docx": docx_bytes()})

    def test_sandbox_has_no_workspace_mount_or_network_and_no_parser_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            command = conversion._command(Path(temporary), conversion._office_root())
        self.assertIn("--unshare-all", command)
        self.assertNotIn(str(Path.cwd()), command)
        self.assertNotIn("/mnt/nvme/Teaching", command)
        with patch("document_identity.template_conversion.subprocess.run", side_effect=FileNotFoundError), self.assertRaisesRegex(ValueError, "could not start"):
            conversion.convert_template({"test.pdf": pdf_bytes()})
