"""A separate half-width/half-height, repeated-corner print contract."""
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import ImageDraw
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfgen.canvas import Canvas

from app import create_app
from document_identity import media, tokens
from document_identity.layouts import CORNERS_PROFILE, PROFILE, SMALL_CORNERS_PROFILE, get_layout, stamp_boxes, barcode_boxes
from rosters import connect
from test_storage import temporary_storage, HEADERS

FIXTURES = Path(__file__).resolve().parents[1] / 'document_identity/fixtures'


class CornerLayoutTests(unittest.TestCase):
    def manifest(self, count=3):
        return [{'output_page':i, 'page_number':i, 'token':tokens.generate_token(),
                 'recovery_alias':tokens.generate_alias(), 'symbology':'DataMatrix'} for i in range(1,count+1)]

    def test_both_corners_and_each_corner_alone_resolve_one_page_identity(self):
        for paper in ('letter','a4'):
            canonical = (FIXTURES / f'{paper}-corners.pdf').read_bytes()
            self.assertEqual(media.inspect_canonical(canonical)['layout_profile'], CORNERS_PROFILE)
            manifest = self.manifest()
            printed = media.stamp_pdf(canonical, manifest, CORNERS_PROFILE)
            checks = media.validate_print(printed, manifest, CORNERS_PROFILE)
            self.assertEqual(checks['marks_per_page'], 2)
            self.assertTrue(checks['quiet_zone_checked'])
            for dpi in (200,300):
                for image, entry in zip(media.render_pages(printed,dpi), manifest, strict=True):
                    both = media.decode_page(image)
                    self.assertEqual([c['payload'] for c in both], [entry['token']])
                    self.assertEqual(len(both[0]['locations']),2)
                    boxes = stamp_boxes(image.width*72/dpi,image.height*72/dpi,CORNERS_PROFILE)
                    for x,y,size in boxes:
                        masked = image.copy()
                        ImageDraw.Draw(masked).rectangle((int(x*dpi/72)-2,int(image.height-(y+size)*dpi/72)-2,
                                                         int((x+size)*dpi/72)+2,int(image.height-y*dpi/72)+2), fill=255)
                        for rotation in (0,90):
                            candidates = media.decode_page(masked.rotate(rotation, expand=True))
                            self.assertEqual([c['payload'] for c in candidates], [entry['token']])
                            self.assertEqual(len(candidates[0]['locations']),1)

    @unittest.skipUnless(__import__('os').environ.get('FACULTY_TEXLIVE_ROOT') or Path('/usr/bin/pdflatex').exists(), 'Install the documented LaTeX toolchain to verify source builds')
    def test_qualification_refuses_a_layout_it_has_no_fixtures_for(self):
        # The simulated qualification has fixtures for the page and 10 mm corner layouts only; asked for 7 mm it used the page
        # fixtures and wrote a record that called them 7 mm.
        from contextlib import redirect_stderr
        from unittest import mock
        from document_identity import qualification
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out'
            with self.assertRaisesRegex(ValueError, 'No qualification fixtures'):
                qualification.qualify(output, SMALL_CORNERS_PROFILE)
            with mock.patch('sys.argv', ['qualification', '--output', str(output), '--profile', SMALL_CORNERS_PROFILE]), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
                qualification.main()
            self.assertEqual(refused.exception.code, 2)
            self.assertFalse((output / 'simulation.json').exists())

    def test_class_lays_out_the_two_corner_codes_by_default(self):
        # the class's default is the 7 mm corner layout;
        # `compactcorners` keeps the 10 mm corner codes (the default from 2026-10-01); `pagecode` the single bottom-right code.
        from document_identity.compiler import compile_sources
        for options, profile in (('letterpaper', SMALL_CORNERS_PROFILE), ('letterpaper,compactcorners', CORNERS_PROFILE), ('letterpaper,pagecode', PROFILE)):
            source = ('\\documentclass[%s]{facultyassignment}\\begin{document}\\assignmenttitle{Quiz}Name: \\hrulefill\\end{document}' % options).encode()
            pdf, _, _ = compile_sources({'main.tex': source})
            self.assertEqual(media.inspect_canonical(pdf)['layout_profile'], profile, options)

    def test_the_7mm_layout_packs_the_token_and_prints_no_alias(self):
        # Requirement, 2026-10-18: two 7 mm marks in 8 mm boxes, the token packed as raw bytes (a 20x20 symbol), no printed alias.
        import zxingcpp
        for paper in ('letter', 'a4'):
            canonical = media.blank_pages(3, paper, SMALL_CORNERS_PROFILE)
            manifest = [{**entry, 'caption': 'Quiz 5 · Copy 12', 'seat_mark': 'A3'} for entry in self.manifest()]
            printed = media.stamp_pdf(canonical, manifest, SMALL_CORNERS_PROFILE)
            checks = media.validate_print(printed, manifest, SMALL_CORNERS_PROFILE)
            self.assertEqual((checks['marks_per_page'], checks['dpi']), (2, 300))
            text = ''.join(page.extract_text() for page in PdfReader(io.BytesIO(printed)).pages)
            self.assertFalse(any(entry['recovery_alias'] in text for entry in manifest))
            self.assertIn('A3', text)
            for image, entry in zip(media.render_pages(printed, 300), manifest, strict=True):
                symbols = zxingcpp.read_barcodes(image)
                self.assertEqual(len(symbols), 2)
                self.assertTrue(all(symbol.content_type == zxingcpp.ContentType.Binary and len(bytes(symbol.bytes)) == 17 for symbol in symbols))
                both = media.decode_page(image)
                self.assertEqual([c['payload'] for c in both], [entry['token']])
                self.assertEqual(len(both[0]['locations']), 2)
                for x, y, size in stamp_boxes(image.width * 72 / 300, image.height * 72 / 300, SMALL_CORNERS_PROFILE):
                    self.assertAlmostEqual(size / media.MM, 8)
                    masked = image.copy()
                    ImageDraw.Draw(masked).rectangle((int(x * 300 / 72) - 2, int(image.height - (y + size) * 300 / 72) - 2,
                                                      int((x + size) * 300 / 72) + 2, int(image.height - y * 300 / 72) + 2), fill=255)
                    self.assertEqual([c['payload'] for c in media.decode_page(masked)], [entry['token']])
            # A scan of the printed pages: the stored page stays at 200 DPI and the marks are read at 300.
            with tempfile.TemporaryDirectory() as folder:
                pages = media.extract_scan(printed, Path(folder))
            self.assertEqual([[c['payload'] for c in page['candidates']] for page in pages], [[entry['token']] for entry in manifest])
            # A changed token is refused before publication.
            wrong = [{**entry, 'token': tokens.generate_token()} for entry in manifest]
            with self.assertRaises(ValueError):
                media.validate_print(printed, wrong, SMALL_CORNERS_PROFILE)

    def test_labels_of_7mm_paper_keep_the_10mm_mark(self):
        # 
        manifest = [{**entry, 'copy_number': 1} for entry in self.manifest(2)]
        header = {'exam': 'Quiz 5', 'section': 'Section 507', 'term': 'Fall 2026', 'page_count': 2}
        for label_media in ('avery-5160', 'dk-1201'):
            small = media.labels_pdf(manifest, header, SMALL_CORNERS_PROFILE, label_media)
            self.assertEqual(media.validate_labels(small, manifest, SMALL_CORNERS_PROFILE, label_media)['layout'], SMALL_CORNERS_PROFILE)
            corners = media.labels_pdf(manifest, header, CORNERS_PROFILE, label_media)
            self.assertEqual([page.extract_text() for page in PdfReader(io.BytesIO(small)).pages],
                             [page.extract_text() for page in PdfReader(io.BytesIO(corners)).pages])
            image = next(media.render_pages(small, 300))
            import zxingcpp
            self.assertTrue(all(symbol.text.startswith('T1-') for symbol in zxingcpp.read_barcodes(image)))

    def test_cover_reads_white_out_each_layouts_stamp_boxes(self):
        from faculty_ocr.prepare import stamp_corners
        width, height = 1700, 2200  # Letter at 200 DPI
        sx, sy = width / 215.9, height / 279.4
        self.assertEqual(stamp_corners(width, height)[0], (int((215.9 - 27) * sx), int(8 * sy), int((215.9 - 8) * sx), int(27 * sy)))
        # The 8 mm box with 2 mm to spare: the page's own text, from 20 mm, is never whited out.
        self.assertEqual(stamp_corners(width, height, small=True)[0], (int((215.9 - 20) * sx), int(8 * sy), int((215.9 - 8) * sx), int(20 * sy)))

    def test_alias_crops_hold_the_printed_alias_line_only(self):
        # A recovery-label read counts only when the crop's whole text is one alias (alias_ocr.summarize), so each corner's crop must
        # hold the alias line and neither the code above it nor the copy caption right of the bottom-left box. The first crops
        # (fixed page fractions) cut the top-right alias in half on Letter and missed it on A4.
        import numpy as np
        from faculty_ocr.prepare import alias_boxes
        for paper in ('letter', 'a4'):
            canonical = (FIXTURES / f'{paper}-corners.pdf').read_bytes()
            manifest = [{**entry, 'caption': 'Quiz 5 · Version A · Copy 12'} for entry in self.manifest()]
            printed = media.stamp_pdf(canonical, manifest, CORNERS_PROFILE)
            for dpi in (150, 300):
                ink = np.asarray(next(iter(media.render_pages(printed, dpi, 10, last=1))).convert('L')) < 128
                height, width = ink.shape
                for corner, (x0, y0, x1, y1), _ in alias_boxes(width, height):
                    with self.subTest(paper=paper, dpi=dpi, corner=corner):
                        rows, cols = np.nonzero(ink[y0:y1, x0:x1])
                        mm = dpi / 25.4
                        self.assertTrue(len(rows), 'no alias in the crop')
                        self.assertLess((rows.max() - rows.min() + 1) / mm, 2.0)  # one line of Courier 5 pt: no code above it
                        self.assertGreater(rows.min() / mm, 1.0)
                        self.assertGreater((y1 - y0 - 1 - rows.max()) / mm, 1.0)
                        self.assertGreater(cols.min() / mm, 1.0)
                        self.assertGreater((x1 - x0 - 1 - cols.max()) / mm, 1.0)  # no caption at the right
                        self.assertLess((cols.max() - cols.min() + 1) / mm, 16.0)

    def test_name_reading_whites_out_both_codes_and_nothing_printed_by_the_page(self):
        # Copy names read the top band of page 1 upright and turned 180 degrees; on a corner copy a code sits at its top right either
        # way up, so `prepare.covers` whites out both stamp boxes (with 2 mm to spare) first. They hold every mark of the stamp and
        # none of the page's own content.
        import numpy as np
        from faculty_ocr.prepare import stamp_corners
        for paper in ('letter', 'a4'):
            canonical = (FIXTURES / f'{paper}-corners.pdf').read_bytes()
            manifest = [{**entry, 'caption': 'Quiz 5 · Version A · Copy 12'} for entry in self.manifest()]
            printed = media.stamp_pdf(canonical, manifest, CORNERS_PROFILE)
            for dpi in (150, 300):
                with self.subTest(paper=paper, dpi=dpi):
                    page = np.asarray(next(iter(media.render_pages(printed, dpi, 10, last=1))).convert('L')) < 128
                    blank = np.asarray(next(iter(media.render_pages(canonical, dpi, 10, last=1))).convert('L')) < 128
                    stamp = page & ~blank
                    covered = np.zeros_like(page)
                    for x0, y0, x1, y1 in stamp_corners(page.shape[1], page.shape[0]):
                        covered[y0:y1 + 1, x0:x1 + 1] = True
                        self.assertFalse(blank[y0:y1 + 1, x0:x1 + 1].any(), 'the page prints inside a stamp box')
                    # Everything the stamp printed in the top band (the top-right code and alias) is inside the whited-out box.
                    band = slice(0, page.shape[0] // 4)
                    self.assertTrue(stamp[band].any())
                    self.assertFalse((stamp & ~covered)[band].any())
                    # The bottom-left code and alias are too; only the caption beside them stays.
                    rows = slice(page.shape[0] * 3 // 4, None)
                    left = int(27 * dpi / 25.4)
                    self.assertFalse((stamp & ~covered)[rows, :left].any())

    def test_reservation_contract_and_missing_wrong_extra_misplaced_marks(self):
        canonical = (FIXTURES / 'letter-corners.pdf').read_bytes()
        manifest = self.manifest(1)
        printed = media.stamp_pdf(canonical,manifest,CORNERS_PROFILE)
        with self.assertRaises(ValueError):
            media.stamp_pdf(canonical,manifest,PROFILE)
        with self.assertRaises(ValueError):
            media.stamp_pdf((FIXTURES/'letter.pdf').read_bytes(),manifest,CORNERS_PROFILE)

        def change(content, change_page):
            reader=PdfReader(io.BytesIO(content)); writer=PdfWriter()
            page=writer.add_page(reader.pages[0]); change_page(page)
            writer.add_metadata({'/FacultyLayout':CORNERS_PROFILE})
            data=io.BytesIO(); writer.write(data); return data.getvalue()

        def rectangle(page,x,y,size,fill=1):
            overlay=io.BytesIO(); canvas=Canvas(overlay,pagesize=(612,792))
            canvas.setFillColorRGB(fill,fill,fill); canvas.rect(x,y,size,size,fill=1,stroke=0); canvas.save()
            page.merge_page(PdfReader(overlay).pages[0])

        for x,y,size in stamp_boxes(612,792,CORNERS_PROFILE):
            occupied=change(canonical,lambda page:rectangle(page,x+1,y+1,4,fill=0))
            with self.assertRaisesRegex(ValueError,'reserved stamp box'):
                media.inspect_canonical(occupied)
            missing=change(printed,lambda page:rectangle(page,x,y,size))
            with self.assertRaises(ValueError): media.validate_print(missing,manifest,CORNERS_PROFILE)

        def wrong_code(page):
            x,y,size=stamp_boxes(612,792,CORNERS_PROFILE)[1]
            rectangle(page,x,y,size)
            x,y,_=barcode_boxes(612,792,CORNERS_PROFILE)[1]
            graphic=PdfReader(io.BytesIO(media.render_barcode(tokens.generate_token(),layout_profile=CORNERS_PROFILE)))
            page.merge_translated_page(graphic.pages[0],x,y)
        wrong=change(printed,wrong_code)
        self.assertEqual(len(media.decode_page(next(media.render_pages(wrong)))),2)
        with self.assertRaises(ValueError): media.validate_print(wrong,manifest,CORNERS_PROFILE)
        graphic=PdfReader(io.BytesIO(media.render_barcode(manifest[0]['token'],layout_profile=CORNERS_PROFILE)))
        extra=change(printed,lambda p:p.merge_translated_page(graphic.pages[0],250,400))
        with self.assertRaises(ValueError): media.validate_print(extra,manifest,CORNERS_PROFILE)
        for transform in (Transformation().translate(6,0),Transformation().scale(.8)):
            changed=change(printed,lambda p:p.add_transformation(transform))
            with self.assertRaises(ValueError): media.validate_print(changed,manifest,CORNERS_PROFILE)

    def test_persisted_profile_old_batch_preservation_and_inbox_redundancy(self):
        with tempfile.TemporaryDirectory() as temp:
            storage=temporary_storage(temp)
            app=create_app(storage=storage); client=app.test_client(); client.get('/').close()
            response=client.post('/api/courses',json={'name':'Qualification','code':'MATH 140','term':'Fall','year':2026,'sections':[507],'is_current':True},headers=HEADERS)
            self.assertEqual(response.status_code,201)
            storage.reconcile_all()
            docs=app.extensions['documents']; inbox=app.extensions['inbox']; scans=app.extensions['document_scans']
            catalog=docs.catalog(); section=catalog['sections'][0]['id']
            assignment=docs.create_assignment(catalog['definitions'][0]['id'],'qualification','Qualification')
            outputs=[]
            for profile,fixture in ((PROFILE,'letter'),(CORNERS_PROFILE,'letter-corners')):
                canonical=(FIXTURES/f'{fixture}.pdf').read_bytes()
                version=docs.register_version(assignment['id'],fixture,canonical,{'main.tex':(FIXTURES/f'{fixture}.tex').read_bytes()},{'fixture':fixture})
                self.assertEqual(version['layout_profile'],profile)
                batch=docs.create_batch(version['id'],section,2,fixture)
                self.assertEqual(json.loads(batch['configuration_json']),get_layout(profile))
                self.assertTrue(docs.build_one())
                pdf=docs.download(batch['id']); manifest=json.loads(docs.download(batch['id'],manifest=True))
                self.assertEqual(manifest['layout'],get_layout(profile))
                self.assertEqual(manifest['validation']['marks_per_page'],2 if profile==CORNERS_PROFILE else 1)
                self.assertEqual(docs.create_batch(version['id'],section,2,fixture)['id'],batch['id'])
                with docs.rosters.lock(),connect(docs.path) as db:
                    placements=db.execute('SELECT p.placement_json FROM page_stamps p JOIN printed_copies c ON c.id=p.printed_copy_id WHERE c.print_batch_id=?',(batch['id'],)).fetchall()
                    self.assertEqual([json.loads(p[0]) for p in placements],[get_layout(profile)]*6)
                with docs.rosters.lock(),docs.files.files('definition',assignment['definition_id']) as root:
                    self.assertEqual((root/version['canonical_path']).read_bytes(),canonical)
                outputs.append((batch,pdf,manifest))
            self.assertEqual(docs.download(outputs[0][0]['id']),outputs[0][1])
            self.assertTrue({p['token'] for p in outputs[0][2]['pages']}.isdisjoint(p['token'] for p in outputs[1][2]['pages']))
            job=inbox.submit({'action':'scan.receive'},[{'filename':'corners.pdf','content':outputs[1][1]}],source='email',sender='scanner',instructor_review_required=True)
            self.assertTrue(scans.process_one())
            review=scans.review_item(job['message_id'])
            self.assertEqual(len(review['pages']),6)
            self.assertTrue(all(p['status']=='resolved' for p in review['pages']))
            self.assertTrue(all(len(json.loads(p['candidates_json']))==1 and len(json.loads(p['candidates_json'])[0]['locations'])==2 for p in review['pages']))
            self.assertEqual(len(review['assemblies']),2)
            self.assertTrue(all(not a['missing'] and not a['conflicts'] for a in review['assemblies']))
            self.assertTrue(review['instructor_review_required'])
            self.assertEqual(inbox.get(job['id'])['status'],'needs_review')
            self.assertEqual(storage.backup_all()['errors'],[])
            self.assertEqual(docs.download(outputs[1][0]['id']),outputs[1][1])


if __name__ == '__main__':
    unittest.main()
