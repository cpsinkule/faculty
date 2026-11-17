"""Symbology-neutral rendering and PDF operations, called in bounded processes."""
import io
from importlib.metadata import version

import numpy as np
import pypdfium2 as pdfium
import zxingcpp
from PIL import Image, ImageFilter
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from .tokens import encode_token, parse_token, valid_alias
from .layouts import MM, PROFILE, LAYOUT, SEAT_MARK_FONT, SEAT_MARK_SIZE, get_layout, stamp_boxes, barcode_boxes, seat_mark_positions, label_profile

PAPERS = {"letter": (612, 792), "a4": (210 * MM, 297 * MM)}
MAX_PAGES = 100
MAX_SCAN_PAGES = 1500
MAX_OUTPUT_PAGES = 1500
# Peel-off stamps. Every label is a landscape strip carrying the mark of its page layout's label_profile at its right end, with the
# seat, name, exam and page text at its left. That is the page's own symbol and size for faculty-page-v1 (20 mm) and
# faculty-corners-10mm-v1 (10 mm); for faculty-corners-7mm-v1 it is the 10 mm corner mark, not the page's 7 mm packed one.
# Sheet media tile strips on a paper sheet (Avery 5160: 3 x 10 labels of 66.7 x 25.4 mm on US Letter).
# Roll media give each strip its own PDF page of exactly the Brother DK label size, so the label
# printer prints it at 100% with no scaling; the 29 x 90 mm label is stored with /Rotate so the page
# matches the printer's portrait paper size. Pads keep the mark and text inside the printable area
# (the QL series leaves about 1.5 mm unprintable across the tape and 3 mm along the feed).
LABEL_MEDIA = {
    "avery-5160": {"kind": "sheet", "title": "Avery 5160 sheets", "paper": "letter", "columns": 3, "rows": 10,
                   "width_mm": 66.675, "height_mm": 25.4, "left_mm": 4.7625, "top_mm": 12.7, "pitch_x_mm": 69.85, "pitch_y_mm": 25.4,
                   "text_left_mm": 2, "text_top_mm": 5.5, "pitch_mm": 4},
    "dk-1201": {"kind": "roll", "title": "Brother DK-1201 address labels, 29 x 90 mm", "width_mm": 90, "height_mm": 29, "rotate": 90,
                "pad_x_mm": 3.5, "pad_y_mm": 2, "text_left_mm": 4, "text_top_mm": 5.5, "pitch_mm": 3.8},
    "dk-1209": {"kind": "roll", "title": "Brother DK-1209 small address labels, 62 x 29 mm (or 62 mm continuous tape cut at 29 mm)",
                "width_mm": 62, "height_mm": 29, "rotate": 0, "pad_x_mm": 2, "pad_y_mm": 3.5, "text_left_mm": 2.5, "text_top_mm": 7, "pitch_mm": 3.8},
}
DEFAULT_MEDIA = "dk-1201"
MAX_CAPTION = 110
MAX_SEAT_MARK = 24  # Table rooms label seats like "K Table 2 · 14"; the 40 mm width is the real bound.


def blank_pages(page_count, paper="letter", layout_profile=PROFILE):
    """A canonical of empty pages carrying the layout contract: the paper for stamps-only exports."""
    if type(page_count) is not int or not 1 <= page_count <= MAX_PAGES:
        raise ValueError("Stamps-only exports need between 1 and 100 pages per copy.")
    width, height = PAPERS[paper]
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=width, height=height)
    writer.add_metadata({"/FacultyLayout": layout_profile})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def caption_position(width, height, layout_profile):
    """Where a seat caption is printed: beside the stamp box, inside the class's reserved bottom band."""
    boxes = stamp_boxes(width, height, layout_profile)
    if layout_profile == PROFILE:
        x, y, _ = boxes[0]
        return x - 2 * MM, y + 1.5 * MM, "right"
    x, y, box = boxes[-1]
    return x + box + 2 * MM, y + 1.5 * MM, "left"


def fit_text(text, font, size, width):
    text = " ".join(str(text or "").split())
    while text and pdfmetrics.stringWidth(text, font, size) > width:
        text = text[:-2].rstrip() + "…" if len(text) > 3 else ""
    return text


def label_media(media):
    if media not in LABEL_MEDIA:
        raise ValueError("Choose a known label media.")
    return LABEL_MEDIA[media]


def label_page_size(spec):
    """Points of one PDF page of the media, in the strip's own (unrotated) frame."""
    if spec["kind"] == "sheet":
        return PAPERS[spec["paper"]]
    return spec["width_mm"] * MM, spec["height_mm"] * MM


def labels_per_page(media):
    spec = label_media(media)
    return spec["columns"] * spec["rows"] if spec["kind"] == "sheet" else 1


def label_boxes(count, media="avery-5160"):
    """Bottom-left corners (points) of the first `count` labels: (page index, x, y)."""
    spec = label_media(media)
    if spec["kind"] == "roll":
        return [(index, 0.0, 0.0) for index in range(count)]
    _, page_height = PAPERS[spec["paper"]]
    per_page = spec["columns"] * spec["rows"]
    result = []
    for index in range(count):
        page, slot = divmod(index, per_page)
        row, column = divmod(slot, spec["columns"])
        x = (spec["left_mm"] + column * spec["pitch_x_mm"]) * MM
        y = page_height - (spec["top_mm"] + row * spec["pitch_y_mm"] + spec["height_mm"]) * MM
        result.append((page, x, y))
    return result


def label_stamp_box(x, y, layout_profile=PROFILE, media="avery-5160"):
    """The stamp box at the right end of a label, and its barcode box.

    Sheet labels reproduce the page's 25 mm white box with the recovery alias under the mark.
    A roll label is already white and must keep the mark clear of the printer's unprintable
    edges, so its box is the mark itself, centred across the strip; the alias joins the text.
    """
    spec = label_media(media)
    layout = get_layout(layout_profile)
    size = layout["barcode_mm"] * MM
    if spec["kind"] == "sheet":
        box = LAYOUT["box_mm"] * MM
        box_x = x + spec["width_mm"] * MM - box - 0.5 * MM
        box_y = y + 0.2 * MM
        return (box_x, box_y, box), (box_x + (box - size) / 2, box_y + LAYOUT["alias_mm"] * MM, size)
    box_x = x + (spec["width_mm"] - spec["pad_x_mm"] - 0.5) * MM - size
    box_y = y + (spec["height_mm"] * MM - size) / 2
    return (box_x, box_y, size), (box_x, box_y, size)


def label_lines(entry, header, spec, width=None):
    """The text column of one label: seat (and version) and name (or exam), then section, term, room and page (or Scantron)."""
    # A Scantron label (migration 015) names the sheet instead of a page.
    page_text = f"copy {entry['copy_number']} · " + ("Scantron" if entry.get("sheet") else f"page {entry['page_number']}/{header.get('page_count', '?')}")
    roll = spec["kind"] == "roll"
    big, mid, small = (10, 8, 6) if roll else (9, 7, 6)
    # SEAT LABELS NAME THE STUDENT'S VERSION AND SECTION (DOCUMENTS): a label's own section, else the file's.
    section = entry.get("section") or header.get("section", "")
    if entry.get("caption"):
        seat = entry.get("seat_label") or ""
        caption = str(entry["caption"])
        # A table seat ("K Table 2 · 14") holds the separator itself: the name follows the whole seat.
        rest = caption[len(f"Seat {seat} · "):] if seat and caption.startswith(f"Seat {seat} · ") else caption.split(" · ", 1)[-1]
        first, exam = f"Seat {seat}" if seat else str(header.get("exam", "")), str(header.get("exam", ""))
        if seat and entry.get("exam_version"):
            # Beside the seat when both fit whole, else first on the exam's line, so neither is cut.
            version = f"Version {entry['exam_version']}"
            if width is None or pdfmetrics.stringWidth(f"{first} · {version}", "Helvetica-Bold", big) <= width:
                first = f"{first} · {version}"
            else:
                exam = f"{version} · {exam}"
        lines = [(first, "Helvetica-Bold", big),
                 (rest.split(" · ")[0], "Helvetica", mid),
                 (f"{exam} · {section} · {header.get('term', '')}", "Helvetica", small),
                 (f"{header.get('room', '')} · {page_text}".strip(" ·"), "Helvetica", small)]
    else:
        lines = [(str(header.get("exam", "")), "Helvetica-Bold", big), (page_text, "Helvetica", mid),
                 (f"{section} · {header.get('term', '')}", "Helvetica", small),
                 (str(header.get("version", "")), "Helvetica", small)]
    if roll and entry.get("recovery_alias"):
        lines.append((entry["recovery_alias"], "Courier", small))
    return lines


PACKED_TOKEN = 1  # the first byte of a packed mark: the token's 16 bytes follow (layout encoding "bytes-v1")


def teaching_payload(barcode):
    """The canonical T1- token a decoded mark carries, or None: a packed mark's bytes or a text mark's own text."""
    data = bytes(barcode.bytes)
    if len(data) == 17 and data[0] == PACKED_TOKEN and barcode.content_type == zxingcpp.ContentType.Binary:
        return encode_token(data[1:])
    return barcode.text if barcode.text.startswith(("T", "t1-")) else None


def barcode_modules(payload, symbology="DataMatrix", layout_profile=PROFILE):
    """(the mark's modules with its quiet zone, 0 dark and 255 light; the layout) for a token under a layout's qualified geometry."""
    raw = parse_token(payload)
    layout = get_layout(layout_profile)
    if symbology != layout["symbology"]:
        raise ValueError("Unsupported symbology or physical layout profile.")
    if layout.get("encoding") == "bytes-v1":
        barcode = zxingcpp.create_barcode(bytes([PACKED_TOKEN]) + raw, zxingcpp.BarcodeFormat.DataMatrix, version=6)
    else:
        barcode = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.DataMatrix, version=8)
    modules = np.asarray(barcode.to_image(scale=1, add_quiet_zones=True))
    n = layout["symbol_modules"] + 2 * layout["quiet_modules"]
    if modules.shape != (n, n) or np.any(modules[0] != 255) or np.any(modules[-1] != 255) or np.any(modules[:, 0] != 255) or np.any(modules[:, -1] != 255):
        raise ValueError("Barcode geometry violates the qualified module and quiet-zone profile.")
    return modules, layout


def barcode_png(payload, symbology="DataMatrix", layout_profile=PROFILE, module_px=24):
    """The same mark as a picture, `module_px` pixels to a module, for a page Faculty does not typeset itself (the DR center's
    Word copy, GRADING "DR COPIES" > STAMPS). The same bytes for the same token."""
    modules, _ = barcode_modules(payload, symbology, layout_profile)
    picture = Image.fromarray(np.kron(modules, np.ones((module_px, module_px), dtype=np.uint8)).astype(np.uint8), "L").convert("1")
    output = io.BytesIO()
    picture.save(output, "PNG", optimize=False)
    return output.getvalue()


def render_barcode(payload, symbology="DataMatrix", layout_profile=PROFILE):
    modules, layout = barcode_modules(payload, symbology, layout_profile)
    n = layout["symbol_modules"] + 2 * layout["quiet_modules"]
    graphic = io.BytesIO()
    size = layout["barcode_mm"] * MM
    drawing = canvas.Canvas(graphic, pagesize=(size, size), invariant=1, pageCompression=1)
    drawing.setFillColorRGB(1, 1, 1)
    drawing.rect(0, 0, size, size, fill=1, stroke=0)
    drawing.setFillColorRGB(0, 0, 0)
    unit = size / n
    for row, col in np.argwhere(modules == 0):
        drawing.rect(float(col) * unit, (n - 1 - float(row)) * unit, unit, unit, fill=1, stroke=0)
    drawing.save()
    return graphic.getvalue()


def decode_page(image):
    """Never execute payloads. Return all candidates so competing tokens are visible.

    One whole-page search, so a second identity anywhere on the page is seen. Corner
    crops searched first were dropped on 2026-10-18 (instructor): over all 7,680 Inbox
    page rasters they found no mark the whole-page search missed (1,577 both, 128
    whole page only, 0 corners only). ZXing also tries right-angle orientations.
    """
    image = image.convert("L")
    if image.width * image.height > 30_000_000:
        raise ValueError("Scan raster exceeds the pixel limit.")
    candidates = {}
    for barcode in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True):
        # Bound untrusted strings before retaining decoder output. A packed mark is read as its canonical token.
        payload = teaching_payload(barcode) or barcode.text
        if len(payload) > 512:
            continue
        position = barcode.position
        location = [
            [getattr(position, point).x, getattr(position, point).y]
            for point in ("top_left", "top_right", "bottom_right", "bottom_left")]
        candidate = candidates.setdefault(payload, {"payload": payload, "symbology": str(barcode.format),
                                                   "location": location, "locations": []})
        center = np.mean(location, axis=0)
        if not any(np.max(np.abs(center - np.mean(existing, axis=0))) < 8 for existing in candidate["locations"]):
            candidate["locations"].append(location)
        if len(candidate["locations"]) > 128:
            raise ValueError("Too many barcode locations; page inspection requires manual review.")
        if len(candidates) > 128:
            raise ValueError("Too many barcode candidates; page inspection requires manual review.")
    return list(candidates.values())


def render_pages(content, dpi=200, maximum=MAX_OUTPUT_PAGES, unrotate=False, exact=False, first=1, last=None, fit=None):
    """Rasterize every page, or the 1-based inclusive range `first`..`last`.

    `unrotate` ignores a page's /Rotate so it is seen in its drawn frame. `exact` trims the
    page's crop box (keeping its origin) to a whole number of raster pixels: PDFium otherwise
    stretches a page whose size is not a whole pixel count (any millimetre-sized label) to fill
    the rounded-up bitmap, shifting drawn positions by a fraction of a pixel from their places.
    `fit` is for looking at a page only (a scan's pixels stay at its resolution): a page too
    large for the pixel limit at `dpi` is drawn with its longer side `fit` pixels instead of
    being refused.
    """
    if dpi not in (100, 150, 200, 300):
        raise ValueError("Unsupported raster resolution.")
    scale = dpi / 72
    with pdfium.PdfDocument(content) as document:
        if not 1 <= len(document) <= maximum:
            raise ValueError("PDF page count exceeds the processing limit.")
        for index in range(first - 1, len(document) if last is None else last):
            page = document[index]
            try:
                if unrotate:
                    page.set_rotation(0)
                width, height = page.get_size()
                drawn = scale
                if width <= 0 or height <= 0:
                    raise ValueError("Unsupported or oversized scan page.")
                if width * height * scale ** 2 > 30_000_000:
                    drawn = fit / max(width, height) if fit else 0
                    if not drawn or min(width, height) * drawn < 1:
                        raise ValueError("Unsupported or oversized scan page.")
                if exact:
                    left, bottom, _, _ = page.get_bbox()
                    columns, rows = int(round(width * scale, 6)), int(round(height * scale, 6))
                    page.set_cropbox(left, bottom, left + (columns - 0.001) / scale, bottom + (rows - 0.001) / scale)
                bitmap = page.render(scale=drawn)
                try:
                    yield bitmap.to_pil().convert("L")
                finally:
                    bitmap.close()
            finally:
                page.close()


def canonical_reader(content, layout_profile=None):
    if not isinstance(content, bytes) or len(content) > 25 * 1024 * 1024:
        raise ValueError("Canonical PDFs must be at most 25 MB.")
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise ValueError("Use an unencrypted PDF compiled with the Faculty LaTeX class and layout contract.")
    profile = (reader.metadata or {}).get("/FacultyLayout")
    get_layout(profile)
    if layout_profile is not None and profile != layout_profile:
        raise ValueError("Canonical PDF and persisted print layout disagree.")
    if not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("Assignment versions require between 1 and 100 pages.")
    normalized = PdfWriter()
    for source in reader.pages:
        page = normalized.add_page(source)
        if page.get("/UserUnit", 1) != 1 or page.get("/Annots"):
            raise ValueError("PDF annotations and nonstandard user units are unsupported.")
        if page.rotation not in (0, 90, 180, 270):
            raise ValueError("Unsupported PDF rotation.")
        page.transfer_rotation_to_content()
        box = tuple(float(value) for value in page.mediabox)
        crop = tuple(float(value) for value in page.cropbox)
        if any(abs(a - b) > .01 for a, b in zip(box, crop)) or abs(box[0]) > .01 or abs(box[1]) > .01:
            raise ValueError("The layout requires matching MediaBox/CropBox and a normalized origin.")
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if not any(abs(width - w) < 1 and abs(height - h) < 1 for w, h in PAPERS.values()):
            raise ValueError("The layout supports portrait US Letter and A4 only.")
    normalized.add_metadata({"/FacultyLayout": profile})
    output = io.BytesIO()
    normalized.write(output)
    return PdfReader(io.BytesIO(output.getvalue()), strict=True)


def inspect_canonical(content):
    reader = canonical_reader(content)
    profile = reader.metadata["/FacultyLayout"]
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    normalized = io.BytesIO()
    writer.write(normalized)
    for image in render_pages(normalized.getvalue(), 200, MAX_PAGES):
        scale = 200 / 72
        for x, y, size in stamp_boxes(image.width / scale, image.height / scale, profile):
            area = image.crop((round(x * scale), round(image.height - (y + size) * scale),
                               round((x + size) * scale), round(image.height - y * scale)))
            if np.min(np.asarray(area)) < 245:
                raise ValueError("Document content overlaps a reserved stamp box.")
    return {"page_count": len(reader.pages), "layout_profile": profile,
            "geometry": [[float(p.mediabox.width), float(p.mediabox.height)] for p in reader.pages]}


NAME_FILL_SIZE = 11  # points: a name written on the page's "Name" line (NAME FILLED IN)


def name_lines(canonical_pdf, pages):
    """{page number: (x, baseline, width)} where a copy's name is printed on each of `pages` that has a "Name" line: just after
    the first whole word "Name" (and its colon) in the page's text, on its baseline, as wide as the line runs before the next
    word on it (a quiz's "UIN:"), else 3 inches. Pages without one are left out: nothing is printed there."""
    found = {}
    with pdfium.PdfDocument(canonical_pdf) as document:
        for number in sorted(pages):
            if not 1 <= number <= len(document):
                continue
            page = document[number - 1]
            text = page.get_textpage()
            try:
                searcher = text.search("Name", match_case=True, match_whole_word=True)
                hit = searcher.get_next()
                searcher.close()
                if not hit:
                    continue
                start, count = hit
                left, bottom, right, top = text.get_charbox(start + count - 1)
                after = start + count
                total = text.count_chars()
                while after < total and text.get_text_range(after, 1) in (":", " "):
                    if text.get_text_range(after, 1) == ":":
                        right = text.get_charbox(after)[2]
                    after += 1
                width = 216.0
                # The next word on the same line (a quiz's "UIN:") ends the space for the name.
                for index in range(after, min(total, after + 400)):
                    char = text.get_text_range(index, 1)
                    if char.strip():
                        box = text.get_charbox(index)
                        if abs(box[1] - bottom) < 3 and box[0] > right + 24:
                            width = min(width, box[0] - right - 18)
                            break
                        if box[1] < bottom - 3:  # the next line: nothing else on this one
                            break
                found[number] = (right + 6, bottom + 1.5, max(width, 60.0))
            finally:
                text.close()
                page.close()
    return found


def stamp_pdf(canonical_pdf, persisted_page_manifest, layout_profile=PROFILE):
    layout = get_layout(layout_profile)
    reader = canonical_reader(canonical_pdf, layout_profile)
    lines = name_lines(canonical_pdf, {e["page_number"] for e in persisted_page_manifest if e.get("name_fill")}) \
        if any(e.get("name_fill") for e in persisted_page_manifest) else {}
    if not 1 <= len(persisted_page_manifest) <= MAX_OUTPUT_PAGES:
        raise ValueError("Print batch exceeds the output page limit.")
    writer = PdfWriter()
    seen = set()
    for index, entry in enumerate(persisted_page_manifest, 1):
        parse_token(entry["token"])
        if entry["token"] in seen or entry["output_page"] != index or not 1 <= entry["page_number"] <= len(reader.pages):
            raise ValueError("Invalid persisted page manifest.")
        seen.add(entry["token"])
        if entry.get("recovery_alias") is not None and not valid_alias(entry["recovery_alias"]):
            raise ValueError("Invalid recovery alias.")
        # reset_translation prevents mutations of a previous copy from leaking.
        writer.reset_translation(reader)
        page = writer.add_page(reader.pages[entry["page_number"] - 1])
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        overlay = io.BytesIO()
        drawing = canvas.Canvas(overlay, pagesize=(width, height), invariant=1)
        for x, y, box in stamp_boxes(width, height, layout_profile):
            drawing.setFillColorRGB(1, 1, 1)
            drawing.rect(x, y, box, box, fill=1, stroke=0)
            if entry.get("recovery_alias") and layout["alias_mm"]:
                drawing.setFillColorRGB(0, 0, 0)
                drawing.setFont("Courier", 6 if layout_profile == PROFILE else 5)
                drawing.drawCentredString(x + box / 2, y + 1.5 * MM, entry["recovery_alias"])
        if entry.get("caption"):
            # Seating data is human-readable beside the mark; the barcode payload stays the opaque token.
            caption = fit_text(str(entry["caption"])[:MAX_CAPTION], "Helvetica", 7, width - 47 * MM)
            x, y, align = caption_position(width, height, layout_profile)
            drawing.setFillColorRGB(0, 0, 0)
            drawing.setFont("Helvetica", 7)
            (drawing.drawRightString if align == "right" else drawing.drawString)(x, y, caption)
        if entry.get("name_fill") and entry["page_number"] in lines:
            # NAME FILLED IN: the copy's student, written on the page's own "Name" line, never over a stamp box.
            x, y, room = lines[entry["page_number"]]
            drawing.setFillColorRGB(0, 0, 0)
            drawing.setFont("Helvetica", NAME_FILL_SIZE)
            drawing.drawString(x, y, fit_text(str(entry["name_fill"])[:120], "Helvetica", NAME_FILL_SIZE, room))
        if entry.get("seat_mark"):
            # The batch asked for a visible seat label: bold, beside the box, top-aligned with it, never inside it.
            mark = fit_text(str(entry["seat_mark"])[:MAX_SEAT_MARK], SEAT_MARK_FONT, SEAT_MARK_SIZE, 40 * MM)
            drawing.setFillColorRGB(0, 0, 0)
            drawing.setFont(SEAT_MARK_FONT, SEAT_MARK_SIZE)
            for x, y, align in seat_mark_positions(width, height, layout_profile):
                (drawing.drawRightString if align == "right" else drawing.drawString)(x, y, mark)
        drawing.save()
        page.merge_page(PdfReader(overlay).pages[0])
        graphic = PdfReader(io.BytesIO(render_barcode(entry["token"], entry["symbology"], layout_profile)))
        for x, y, _ in barcode_boxes(width, height, layout_profile):
            page.merge_translated_page(graphic.pages[0], x, y)
    writer.add_metadata({"/FacultyPrintLayout": layout_profile})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def labels_pdf(manifest, header, layout_profile=PROFILE, media=DEFAULT_MEDIA):
    """Peel-off stamps: one label per persisted page stamp, in copy and page order, and a copy's Scantron label after its pages."""
    spec = label_media(media)
    get_layout(layout_profile)
    page_profile, layout_profile = layout_profile, label_profile(layout_profile)
    if not 1 <= len(manifest) <= MAX_OUTPUT_PAGES:
        raise ValueError("Label sheets exceed the output limit.")
    if not isinstance(header, dict):
        raise ValueError("Invalid label header.")
    width, height = label_page_size(spec)
    positions = label_boxes(len(manifest), media)
    sheet_pdf = io.BytesIO()
    drawing = canvas.Canvas(sheet_pdf, pagesize=(width, height), invariant=1, pageCompression=1)
    current = 0
    seen = set()
    marks = []
    for entry, (page, x, y) in zip(manifest, positions, strict=True):
        parse_token(entry["token"])
        if entry["token"] in seen:
            raise ValueError("Invalid persisted page manifest.")
        seen.add(entry["token"])
        if entry.get("recovery_alias") is not None and not valid_alias(entry["recovery_alias"]):
            raise ValueError("Invalid recovery alias.")
        while current < page:
            drawing.showPage()
            current += 1
        (box_x, box_y, box), (bx, by, size) = label_stamp_box(x, y, layout_profile, media)
        if spec["kind"] == "sheet":
            drawing.setFillColorRGB(1, 1, 1)
            drawing.rect(box_x, box_y, box, box, fill=1, stroke=0)
        marks.append((page, entry["token"], entry["symbology"], bx, by))
        drawing.setFillColorRGB(0, 0, 0)
        if spec["kind"] == "sheet" and entry.get("recovery_alias"):
            drawing.setFont("Courier", 6)
            drawing.drawCentredString(box_x + box / 2, box_y + 1.5 * MM, entry["recovery_alias"])
        left = x + spec["text_left_mm"] * MM
        text_width = box_x - 1.5 * MM - left
        baseline = y + (spec["height_mm"] - spec["text_top_mm"]) * MM
        for text, font, points in label_lines(entry, header, spec, text_width):
            drawing.setFont(font, points)
            drawing.drawString(left, baseline, fit_text(text, font, points, text_width))
            baseline -= spec["pitch_mm"] * MM
    drawing.save()
    # Merge the vector marks of the layout's label_profile (the page's own for faculty-page-v1 and the 10 mm corner layout;
    # the 10 mm corner mark for the 7 mm layout).
    reader = PdfReader(io.BytesIO(sheet_pdf.getvalue()), strict=True)
    writer = PdfWriter()
    pages = [writer.add_page(page) for page in reader.pages]
    for page, token, symbology, bx, by in marks:
        graphic = PdfReader(io.BytesIO(render_barcode(token, symbology, layout_profile)))
        pages[page].merge_translated_page(graphic.pages[0], bx, by)
    if spec.get("rotate"):
        # The strip is drawn landscape; the printer's paper size for this roll is portrait.
        for page in pages:
            page.rotate(spec["rotate"])
    writer.add_metadata({"/FacultyLabelSheet": media, "/FacultyLabelMedia": media, "/FacultyPrintLayout": page_profile})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def validate_labels(content, manifest, layout_profile=PROFILE, media=DEFAULT_MEDIA):
    spec = label_media(media)
    page_profile, layout_profile = layout_profile, label_profile(layout_profile)
    layout = get_layout(layout_profile)
    positions = label_boxes(len(manifest), media)
    per_page = labels_per_page(media)
    scale = 200 / 72
    width, height = label_page_size(spec)
    # Pages stored rotated for the label printer are inspected in the strip's own frame, unstretched.
    sheets = list(render_pages(content, 200, unrotate=True, exact=True))
    if len(sheets) != positions[-1][0] + 1:
        raise ValueError("Label sheet count disagrees with the manifest.")
    for sheet_index, image in enumerate(sheets):
        if abs(image.width - width * scale) > 1 or abs(image.height - height * scale) > 1:
            raise ValueError("A label page is not the media's size.")
        entries = manifest[sheet_index * per_page:(sheet_index + 1) * per_page]
        found = [c for c in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True) if teaching_payload(c)]
        if len(found) != len(entries) or {teaching_payload(c) for c in found} != {entry["token"] for entry in entries}:
            raise ValueError("A label sheet did not recover exactly its persisted tokens.")
        if any(symbol.format != zxingcpp.BarcodeFormat.DataMatrix for symbol in found):
            raise ValueError("Printed symbology disagrees with the persisted profile.")
        for entry, (_, x, y) in zip(entries, positions[sheet_index * per_page:(sheet_index + 1) * per_page], strict=True):
            _, (bx, by, size) = label_stamp_box(x, y, layout_profile, media)
            left, top = bx * scale, image.height - (by + size) * scale
            symbol = min(found, key=lambda mark: (mark.position.top_left.x - left) ** 2 + (mark.position.top_left.y - top) ** 2)
            found.remove(symbol)
            if teaching_payload(symbol) != entry["token"]:
                raise ValueError("A label carries a token belonging to another position.")
            _validate_symbol(image, symbol, left, top, size * scale, layout["module_mm"] * MM * scale)
    return {"sheets": len(sheets), "labels": len(manifest), "template": media, "media": media, "dpi": 200,
            "decoder": version("zxing-cpp"), "layout": page_profile, "placement_checked": True, "quiet_zone_checked": True}


def validate_print(content, manifest, layout_profile=PROFILE):
    layout = get_layout(layout_profile)
    recovered = 0
    # A 7 mm mark's 1-module quiet zone is 2.5 pixels at 200 DPI, too thin to see clear between antialiased edges: it is checked
    # at 300 DPI, as scans are read.
    dpi = layout.get("check_dpi", 200)
    for image, entry in zip(render_pages(content, dpi), manifest, strict=True):
        # Inspect distinct physical symbols, without scan intake's payload dedupe.
        # Only the redundant profile allows two identical, correctly placed marks.
        teaching = [c for c in zxingcpp.read_barcodes(image, try_rotate=True, try_downscale=True, try_invert=True) if teaching_payload(c)]
        if len(teaching) != layout.get("marks_per_page", 1) or any(teaching_payload(symbol) != entry["token"] for symbol in teaching):
            raise ValueError("A printed page did not recover its exact persisted token.")
        if any(symbol.format != zxingcpp.BarcodeFormat.DataMatrix for symbol in teaching):
            raise ValueError("Printed symbology disagrees with the persisted profile.")
        scale = dpi / 72
        remaining = list(teaching)
        for x, y, size in barcode_boxes(image.width / scale, image.height / scale, layout_profile):
            left, top = x * scale, image.height - (y + size) * scale
            size, quiet = size * scale, layout["module_mm"] * MM * scale
            symbol = min(remaining, key=lambda mark: (mark.position.top_left.x - left) ** 2 + (mark.position.top_left.y - top) ** 2)
            remaining.remove(symbol)
            _validate_symbol(image, symbol, left, top, size, quiet)
        recovered += 1
    return {"pages": recovered, "dpi": dpi, "decoder": version("zxing-cpp"), "layout": layout_profile,
            "placement_checked": True, "quiet_zone_checked": True, "position_tolerance_pixels": 2,
            "marks_per_page": layout.get("marks_per_page", 1)}


def _validate_symbol(image, symbol, left, top, size, quiet):
    expected = np.array([[left + quiet, top + quiet], [left + size - quiet, top + quiet],
                         [left + size - quiet, top + size - quiet], [left + quiet, top + size - quiet]])
    actual = np.array([[getattr(symbol.position, point).x, getattr(symbol.position, point).y]
                       for point in ("top_left", "top_right", "bottom_right", "bottom_left")])
    pixels = np.asarray(image)
    # Two raster pixels allow subpixel placement and detector rounding only
    # (0.254 mm at 200 DPI). Measure the mark itself, not just its payload.
    if np.max(np.abs(actual - expected)) > 2:
        # The decoder's corner estimate can wander a pixel or two on some symbol
        # patterns; the printed mark itself is the authority. Inside the barcode
        # box only the symbol is dark, so its dark-pixel extents are its true
        # outline, and those must match the expected outline to the same tolerance.
        region = pixels[int(round(top)):int(round(top + size)), int(round(left)):int(round(left + size))]
        dark = np.argwhere(region < 128)
        if not dark.size:
            raise ValueError("Printed barcode position or module size violates the layout profile.")
        (y0, x0), (y1, x1) = dark.min(axis=0), dark.max(axis=0) + 1
        outline = np.array([[left + x0, top + y0], [left + x1, top + y0], [left + x1, top + y1], [left + x0, top + y1]])
        if np.max(np.abs(outline - expected)) > 2:
            raise ValueError("Printed barcode position or module size violates the layout profile.")
    for x0, y0, x1, y1 in ((left, top, left + size, top + quiet),
                           (left, top + size - quiet, left + size, top + size),
                           (left, top, left + quiet, top + size),
                           (left + size - quiet, top, left + size, top + size)):
        # Exclude only edge antialiasing; the interior of every quiet strip
        # must still be opaque white around the full 24-module symbol.
        strip = pixels[int(np.ceil(y0 + .5)):int(np.floor(y1 - .5)),
                       int(np.ceil(x0 + .5)):int(np.floor(x1 - .5))]
        if not strip.size or np.min(strip) < 245:
            raise ValueError("Printed barcode quiet zone is not clear.")


def scan_page_count(content):
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_SCAN_PAGES:
        raise ValueError(f"Scans must be unencrypted PDFs of at most {MAX_SCAN_PAGES} pages.")
    return len(reader.pages)


def extract_scan(content, output, first=1, last=None):
    """One retained PDF page and a deterministic raster for each observation.

    A section's scan runs to hundreds of pages, so the caller asks for at most
    MAX_PAGES at a time; `index` stays the page's number in the whole scan.
    """
    reader = PdfReader(io.BytesIO(content), strict=True)
    total = scan_page_count(content)
    last = total if last is None else last
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= total or last - first >= MAX_PAGES:
        raise ValueError(f"Inspect at most {MAX_PAGES} scan pages at a time.")
    result = []
    output_bytes = 0
    reading = reading_rasters(content, first, last)
    for index, image in enumerate(render_pages(content, 200, MAX_SCAN_PAGES, first=first, last=last), first):
        png = output / f"{index}.png"
        image.save(png, format="PNG", compress_level=6, optimize=False)
        writer = PdfWriter()
        writer.add_page(reader.pages[index - 1])
        with (output / f"{index}.pdf").open("wb") as handle:
            writer.write(handle)
        output_bytes += png.stat().st_size + (output / f"{index}.pdf").stat().st_size
        if output_bytes > 256 * 1024**2:
            raise ValueError("Extracted scan artifacts exceed the total size limit.")
        sharp = next(reading)
        candidates = decode_page(sharp) if sharp is not None else decode_page(image)
        dpi = READ_DPI if sharp is not None else 200
        blank = blank_side(image)
        if sharp is not None and not blank and not any(candidate["payload"].startswith(TOKEN_PREFIXES) for candidate in candidates):
            # SECOND LOOK: a sticker printed against its top edge clips the code's quiet zone, and the 300 DPI drawing then reads
            # nothing where a smaller one does (Exam 2, 2026-10-25: three Scantron labels, read at 250 DPI, none at 300).
            again = decode_page(sharp.resize((round(sharp.width * SECOND_DPI / READ_DPI), round(sharp.height * SECOND_DPI / READ_DPI)), Image.LANCZOS))
            if any(candidate["payload"].startswith(TOKEN_PREFIXES) for candidate in again):
                candidates, dpi = again, SECOND_DPI
        threshold = None
        if sharp is not None and not blank and not any(candidate["payload"].startswith(TOKEN_PREFIXES) for candidate in candidates):
            candidates, threshold = faint_look(sharp, candidates)
        if dpi != 200:
            # Locations stay in the stored 200 DPI raster's pixels.
            for candidate in candidates:
                candidate["locations"] = [[[round(x * 200 / dpi, 2), round(y * 200 / dpi, 2)] for x, y in location] for location in candidate["locations"]]
                candidate["location"] = candidate["locations"][0]
        result.append({"index": index, "candidates": candidates, "blank": blank,
                       "decoder": {"name": "zxing-cpp", "version": version("zxing-cpp"), "dpi": dpi,
                                   "raster_dpi": 200, "search": "full-page+rotations", **({"threshold": threshold} if threshold else {})}})
    return result


def blank_side(image):
    """Whether a scanned side is empty. On white paper, as ever: next to none of it (`BLANK_SHARE`) is darker than `WHITE_PAPER`.
    GREY PAPER: coloured paper, and any paper on a scanner in grey, is itself darker than that, so no such side is blank by that test; there a side is
    empty when, inside `EDGE` margins (the scanner's shadow of the paper's edge), next to none of it is more than `GREY_INK` levels
    darker than the paper around it (the mean of the 41 pixels square about each pixel: creases and uneven light are no ink)."""
    return bool(ink_share(image) < BLANK_SHARE)


def ink_share(image):
    """How much of a scanned side is ink, as `blank_side` measures it: on white paper the share darker than `WHITE_PAPER`; on paper
    darker than that (GREY PAPER) the share inside `EDGE` margins more than `GREY_INK` levels darker than the paper around it.
    A side whose share is under `PRINTED_SHARE` carries no printed page (DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS")."""
    page = image.convert("L")
    white = float(np.mean(np.asarray(page) < WHITE_PAPER))
    if white < BLANK_SHARE:
        return white
    width, height = page.size
    inside = (int(width * EDGE), int(height * EDGE), int(width * (1 - EDGE)), int(height * (1 - EDGE)))
    pixels = np.asarray(page.crop(inside)).astype(np.int16)
    if not pixels.size or np.median(pixels) >= WHITE_PAPER:
        return white
    paper = np.asarray(page.filter(ImageFilter.BoxBlur(20)).crop(inside)).astype(np.int16)
    return float(np.mean(paper - pixels > GREY_INK))


def faint_look(sharp, candidates):
    """FAINT MARKS: (candidates, threshold) for a page with no teaching mark at READ_DPI or SECOND_DPI, read once more from its
    300 DPI drawing turned black and white at each of FAINT_THRESHOLDS in turn. The marks found are kept only when one is a
    teaching mark; otherwise `candidates` come back as they were, with threshold None."""
    for threshold in FAINT_THRESHOLDS:
        again = decode_page(sharp.point(lambda value, cut=threshold: 0 if value < cut else 255))
        if any(candidate["payload"].startswith(TOKEN_PREFIXES) for candidate in again):
            return again, threshold
    return candidates, None


# Codes are read from a 300 DPI drawing of each page; the stored raster, OCR, grading and every other use stay at 200 DPI
# (requirement, 2026-10-18). A 300 DPI scan read at 300 found 7 mm marks 22/22 where 200 DPI found 3/22.
READ_DPI = 300
SECOND_DPI = 250  # SECOND LOOK: a page with no teaching mark at READ_DPI is read once more at this size
# FAINT MARKS (Quiz 7, 2026-11-06): stamped quizzes photocopied for the class came back with corner codes too faint to read; all 139
# such pages of the scan read once the 300 DPI drawing was made black and white, 127 at 100 and the other 12 at 120.
FAINT_THRESHOLDS = (100, 120)
TOKEN_PREFIXES = ("T1-", "t1-")
# A blank side (`blank_side`): under BLANK_SHARE of it darker than WHITE_PAPER. GREY PAPER (take-home, 2026-11-11: printed one-sided,
# on coloured paper, scanned two-sided at home, and none of its 860 empty backs was blank): the Epson in grey draws that paper at 155
# to 180 and white paper at about 210 (none of 2,872 sides without a label it had scanned before was ever blank), so there ink is
# what is GREY_INK levels darker than the paper around it, as far below the paper as WHITE_PAPER is below white. Measured on every
# page kept that day: of 9,932 grey pages the 6,381 with a label or an identity have .0042 of the side and more that dark.
BLANK_SHARE, WHITE_PAPER, GREY_INK, EDGE = .0001, 230, 25, .04
# A side that can carry a printed page (`ink_share`, DOCUMENTS "A PAGE BETWEEN ITS OWN LABELS", 2026-11-14): a tenth of the least
# printed page measured. Of 3,559 kept sides with a label on paper labeled on every page, the least had .011 of the side (white
# paper) and .017 (grey); the empty backs of the take-home whose front shows through, or with a stain, have .0001 to .0005.
PRINTED_SHARE = .001


def reading_rasters(content, first, last):
    """The page drawn at READ_DPI for the code reader, or None where that drawing would pass the pixel limit."""
    scale = READ_DPI / 72
    with pdfium.PdfDocument(content) as document:
        for index in range(first - 1, last):
            page = document[index]
            try:
                width, height = page.get_size()
                if width <= 0 or height <= 0 or width * height * scale ** 2 > 30_000_000:
                    yield None
                    continue
                bitmap = page.render(scale=scale)
                try:
                    yield bitmap.to_pil().convert("L")
                finally:
                    bitmap.close()
            finally:
                page.close()


LOOK_PAGES = 4
# A phone's scan is one page the size of its photograph (a student's doctor's note, 2026-10-11: 2228 by 3193 points, 31 by 44
# inches, 30.9 million pixels at 150 DPI, just past the limit). Such a page is drawn this many pixels on its longer side (a Letter
# page at 300 DPI) instead of being refused: the file viewer, the assistant's look and picture search could not show it at all.
LOOK_SIDE = 3300


def look_pages(content, request, output):
    """A few pages of any PDF as pictures for the assistant to look at (requirement, 2026-09-30): each chosen page rendered at 150 DPI to `output/<n>.png`, a page too large for that with its longer
    side LOOK_SIDE pixels. Nothing is kept; pages past the end are left out."""
    wanted = (request or {}).get("pages") or [1]
    if not isinstance(wanted, list) or not 1 <= len(wanted) <= LOOK_PAGES or any(type(n) is not int or n < 1 for n in wanted):
        raise ValueError(f"Choose 1 to {LOOK_PAGES} pages.")
    with pdfium.PdfDocument(content) as document:
        total = len(document)
    if not 1 <= total <= MAX_SCAN_PAGES:
        raise ValueError(f"PDFs of at most {MAX_SCAN_PAGES} pages can be looked at.")
    pages = sorted({n for n in wanted if n <= total})
    for number in pages:
        image = next(render_pages(content, 150, MAX_SCAN_PAGES, first=number, last=number, fit=LOOK_SIDE))
        image.save(output / f"{number}.png", format="PNG", compress_level=6)
    return {"total": total, "pages": pages}


HEIF_PIXELS = 60_000_000
HEIF_EDGE = 3000


def heif_picture(content, request, output):
    """A HEIC photo (an iPhone's camera format) as `output/picture.png`: its primary picture, upright, flattened onto white and
    scaled to fit the asked edge. Decoded only here, in the sandbox: libheif is native code reading a file from outside."""
    import pillow_heif
    from PIL import ImageOps
    edge = (request or {}).get("edge")
    if type(edge) is not int or not 64 <= edge <= HEIF_EDGE:
        raise ValueError(f"Choose an edge of 64 to {HEIF_EDGE} pixels.")
    pillow_heif.register_heif_opener()
    try:
        image = Image.open(io.BytesIO(content))
    except Exception:  # noqa: BLE001 - said in words; the caller shows it
        raise ValueError("it is not a HEIC photo this viewer reads") from None
    if image.format != "HEIF":
        raise ValueError("it is not a HEIC photo")
    if image.width * image.height > HEIF_PIXELS:
        raise ValueError("it is too large to show")
    image = ImageOps.exif_transpose(image)
    size = list(image.size)
    if image.mode in ("RGBA", "LA", "P", "PA"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.getchannel("A"))
        image = flat
    elif image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    image.thumbnail((edge, edge))
    image.save(output / "picture.png", format="PNG", compress_level=1)
    return {"size": size}


TEXT_PAGES = 12
TEXT_CHARS = 12000


def page_text(content, request):
    """The text of a few pages of any PDF, for a drafting model reading the course's lecture notes (`grading/drafting_tools.py`):
    each chosen page's text as the PDF holds it, cut at TEXT_CHARS. Nothing is rendered or kept; pages past the end are left out."""
    wanted = (request or {}).get("pages") or [1]
    if not isinstance(wanted, list) or not 1 <= len(wanted) <= TEXT_PAGES or any(type(n) is not int or n < 1 for n in wanted):
        raise ValueError(f"Choose 1 to {TEXT_PAGES} pages.")
    found = []
    with pdfium.PdfDocument(content) as document:
        total = len(document)
        if not 1 <= total <= MAX_SCAN_PAGES:
            raise ValueError(f"PDFs of at most {MAX_SCAN_PAGES} pages can be read.")
        for number in sorted({n for n in wanted if n <= total}):
            page = document[number - 1]
            textpage = page.get_textpage()
            body = textpage.get_text_range()
            textpage.close()
            page.close()
            found.append({"page": number, "text": body.replace("\r\n", "\n").replace("\r", "\n")[:TEXT_CHARS]})
    return {"total": total, "pages": found}


# Student photo rosters (Howdy's printed "roster with photos" page: a grid of photos, each captioned "12. Last, First Middle").
ROSTER_PHOTOS_MAX = 400
ROSTER_PHOTO_SIDE = 256


def roster_photos(content, output):
    """Each embedded photo of a printed photo roster with the caption under it, in reading order.

    The photos are the PDF's own bitmaps (nothing is cropped from a raster), each scaled down to at most ROSTER_PHOTO_SIDE pixels and
    written to `output/<n>.jpg`. A caption is the text in the band under its photo, without its leading "12."; whatever it says is an
    untrusted draft for the caller to match, never an identity."""
    import re
    result = []
    with pdfium.PdfDocument(content) as document:
        if not 1 <= len(document) <= 8:
            raise ValueError("A photo roster is one to eight pages.")
        for page_number, page in enumerate(document, 1):
            text = page.get_textpage()
            found = []
            for obj in page.get_objects():
                if obj.type != pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                    continue
                left, bottom, right, top = obj.get_bounds()
                if 90 < right - left < 220 and 90 < top - bottom < 220:  # a photo tile, not the university's logo or an icon
                    found.append((left, bottom, right, top, obj))
            found.sort(key=lambda item: (-round(item[3] / 6), item[0]))  # rows top to bottom, left to right
            for left, bottom, right, top, obj in found:
                if len(result) >= ROSTER_PHOTOS_MAX:
                    raise ValueError("A photo roster holds at most 400 photos.")
                caption = " ".join(text.get_text_bounded(left - 2, bottom - 38, left + 150, bottom).split())
                caption = re.sub(r"^\d{1,3}\.\s*", "", caption)[:200]
                picture = obj.get_bitmap().to_pil().convert("RGB")
                if min(picture.size) < 32 or max(picture.size) > 4000:
                    raise ValueError("A photo has an unsupported size.")
                picture.thumbnail((ROSTER_PHOTO_SIDE, ROSTER_PHOTO_SIDE), Image.LANCZOS)
                index = len(result) + 1
                path = output / f"{index}.jpg"
                picture.save(path, format="JPEG", quality=88, optimize=True)
                result.append({"index": index, "page": page_number, "caption": caption, "width": picture.size[0], "height": picture.size[1]})
    return result


# Lecture notes (course_notes, AGENTS.md "Course notes"): what a notes PDF says about its sections, and its parts cut out of it.
NOTES_MAX_PAGES = 2000
NOTES_LINES = 12
NOTES_TOC = 3000
NOTES_MAX_PARTS = 300


def notes_outline(content):
    """The evidence for splitting notes into sections: page count, title, bookmarks (level, title, page) and each page's first
    lines of text plus every line anywhere on it that reads like a numbered heading. All of it is untrusted text for the caller's
    rule (course_notes/sections.py); nothing here decides where a section begins."""
    import re
    heading = re.compile(r"^(?:section\s+)?\d{1,2}\.\d{1,2}[.:]?\s+\S", re.I)
    with pdfium.PdfDocument(content) as document:
        total = len(document)
        if not 1 <= total <= NOTES_MAX_PAGES:
            raise ValueError(f"Notes can have at most {NOTES_MAX_PAGES} pages.")
        title = " ".join(str((document.get_metadata_dict() or {}).get("Title") or "").split())[:200]
        toc = []
        for item in document.get_toc(max_depth=8):
            if len(toc) >= NOTES_TOC:
                break
            destination = item.get_dest()
            index = destination.get_index() if destination is not None else None
            toc.append({"level": int(item.level), "title": " ".join((item.get_title() or "").split())[:300],
                        "page": index + 1 if isinstance(index, int) and 0 <= index < total else None})
        text = []
        for number in range(total):
            page = document[number]
            textpage = page.get_textpage()
            body = textpage.get_text_range()
            textpage.close()
            page.close()
            lines = [line[:300] for line in (" ".join(raw.split()) for raw in body.replace("\r", "\n").split("\n")) if line]
            text.append({"page": number + 1, "chars": len(body.strip()), "lines": lines[:NOTES_LINES],
                         "headings": [line for line in lines if heading.match(line)][:20]})
    return {"pages": total, "title": title, "toc": toc, "text": text}


def notes_split(content, manifest, output):
    """Each part of the notes as its own PDF, `output/<position>.pdf`: its pages copied as they are (nothing rendered or
    re-encoded), its topics as bookmarks and its title as the document title, so a viewer's tab names it."""
    parts = manifest.get("parts") or []
    if not 1 <= len(parts) <= NOTES_MAX_PARTS:
        raise ValueError("The notes have too many parts to split.")
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise ValueError("The notes PDF is password protected.")
    total = len(reader.pages)
    result = []
    for part in parts:
        position, first, last = int(part["position"]), int(part["first_page"]), int(part["last_page"])
        if not 1 <= first <= last <= total:
            raise ValueError("A part of the notes lies outside the PDF.")
        writer = PdfWriter()
        for index in range(first - 1, last):
            writer.add_page(reader.pages[index])
        for topic in part.get("topics") or []:
            if first <= int(topic["page"]) <= last:
                writer.add_outline_item(str(topic["title"])[:300], int(topic["page"]) - first)
        writer.add_metadata({"/Title": str(part.get("label") or "")[:300], "/Subject": str(manifest.get("title") or "")[:300]})
        with (output / f"{position}.pdf").open("wb") as handle:
            writer.write(handle)
        result.append({"position": position, "pages": len(writer.pages)})
    return result
