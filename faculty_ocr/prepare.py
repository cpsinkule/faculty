"""Normalize one requested page inside the existing bounded PDF sandbox."""
import hashlib
import io
import json
from pathlib import Path
import resource


def main():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from PIL import Image
    request = json.loads(Path("/work/request.json").read_text())
    if request.get("operation") == "covers":
        return covers(request, Image)
    image = load(Path("/work/input").read_bytes(), request["page"])
    original = list(image.size)
    main_regions(request, image, original, Image)


def load(content, page_number):
    """One page of a PDF (rendered at 300 DPI) or a single image, as RGB."""
    from PIL import Image, ImageOps
    import pypdfium2 as pdfium
    if content.startswith(b"%PDF-"):
        with pdfium.PdfDocument(content) as pdf:
            if not 1 <= page_number <= len(pdf) <= 100:
                raise ValueError("Choose an existing page in a PDF of at most 100 pages.")
            page = pdf[page_number - 1]
            try:
                w, h = page.get_size()
                if min(w, h) <= 0 or w * h * (300 / 72)**2 > 30_000_000:
                    raise ValueError("PDF page exceeds the raster limit.")
                bitmap = page.render(scale=300 / 72)
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            finally:
                page.close()
    else:
        if page_number != 1:
            raise ValueError("A single image has only page 1.")
        Image.MAX_IMAGE_PIXELS = 30_000_000
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in ("PNG", "JPEG", "WEBP", "TIFF") or getattr(source, "n_frames", 1) != 1:
                raise ValueError("Use a single PNG, JPEG, WebP or TIFF image, or a PDF page.")
            if source.width * source.height > 30_000_000:
                raise ValueError("Image exceeds the pixel limit.")
            oriented = ImageOps.exif_transpose(source).convert("RGBA")
            image = Image.new("RGB", oriented.size, "white")
            image.paste(oriented, mask=oriented.getchannel("A"))
    return image


def crop_region(image, bounds, name, most, Image):
    """Crop normalized `bounds` of an image to a PNG in /work, scaled to at most one megapixel (and at most `most` times up)."""
    w, h = image.size
    box = tuple(int(value * (w if index % 2 == 0 else h)) for index, value in enumerate(bounds))
    pixels, digest = save_crop(image.crop(box), name, most, Image)
    return {"name": name, "bounds": list(box), "normalized_bounds": list(bounds), "pixels": pixels, "image_sha256": digest}


def save_crop(image, name, most, Image):
    """Write `image` to /work/`name` as a PNG scaled to at most one megapixel (and at most `most` times up); return its size and SHA-256."""
    scale = min(most, (1_000_000 / (image.width * image.height)) ** .5)
    if scale != 1:
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
    image.save("/work/" + name, format="PNG")
    return list(image.size), hashlib.sha256(Path("/work", name).read_bytes()).hexdigest()


# Faculty's papers in millimetres: Letter, A4 (portrait). A scanned page is read as the one whose shape is nearest.
PAPERS_MM = ((215.9, 279.4), (210.0, 297.0))  # Letter, A4 (portrait)


def paper_mm(w, h):
    """(width, height) in millimetres of the paper whose shape is nearest a w x h page, turned if the page is sideways."""
    short, long = min(w, h), max(w, h)
    paper = min(PAPERS_MM, key=lambda size: abs(size[0] / size[1] - short / long))
    return paper if w <= h else paper[::-1]


def stamp_corners(w, h, small=False):
    """Pixel boxes of the two `faculty-corners-10mm-v1` stamp boxes (15 mm, 10 mm from the edges) with 2 mm to spare for a scanner's
    shift, top-right and bottom-left, on the paper whose shape is nearest. Page content starts 30 mm from the top and bottom.
    `small`: the 8 mm boxes of `faculty-corners-7mm-v1` (2026-10-18), whose page content starts 20 mm from the top and bottom."""
    far = 20 if small else 27
    width, height = paper_mm(w, h)
    sx, sy = w / width, h / height
    return [(int((width - far) * sx), int(8 * sy), int((width - 8) * sx), int(far * sy)),
            (int(8 * sx), int((height - far) * sy), int(far * sx), int((height - 8) * sy))]


def covers(request, Image):
    """Several first pages at once (papers printed without labels): each page's top band and the caller's title box, upright
    and turned 180 degrees, for one model load. Pixels only; what a read means is the caller's business. A page the caller marks
    in `blank_corners` is a copy Faculty stamped with the two corner codes: both stamp boxes are whited out first, so the top band
    either way up holds the page's own words and never a Data Matrix code or its alias."""
    count, title, first = request["inputs"], request["title_box"], request.get("first", 0)
    if type(count) is not int or not 1 <= count <= 64:
        raise ValueError("Read between 1 and 64 pages at a time.")
    blank = request.get("blank_corners") or [False] * count
    if not isinstance(blank, list) or len(blank) != count or any(type(value) is not bool and value != "small" for value in blank):
        raise ValueError("blank_corners names every page true, \"small\" or false.")
    pages = []
    for index in range(count):
        image = load(Path(f"/work/input-{index}").read_bytes(), 1)
        if blank[index]:
            from PIL import ImageDraw
            drawing = ImageDraw.Draw(image)
            for box in stamp_corners(*image.size, small=blank[index] == "small"):
                drawing.rectangle(box, fill="white")
        regions = []
        for rotation in (0, 180):
            oriented = image.rotate(rotation, expand=True)
            for kind, bounds, most in (("band", (0.0, 0.0, 1.0, 0.25), 2.0), ("title", tuple(title), 1.0)):
                regions.append({**crop_region(oriented, bounds, f"cover-{first + index}-{kind}-{rotation}.png", most, Image), "kind": kind, "rotation": rotation})
        pages.append({"regions": regions, "original_pixels": list(image.size)})
    Path("/work/prepared.json").write_text(json.dumps({"pages": pages, "crop_profile": "faculty-cover-batch-v1",
                                                       "pdf_render_dpi": 300 if request.get("is_pdf") else None}))


# Where `faculty-corners-10mm-v1` prints its recovery aliases (document_identity/layouts.py; the default layout since 2026-10-01):
# each 15 mm box sits 10 mm from its two page edges, the code in its upper 10 mm and the alias, Courier 5 pt centred, its baseline
# 1.5 mm above the box's bottom. A read counts only when the crop's whole text is one alias, so each crop holds the alias line and
# a little paper around it, never the code above it or the copy caption printed right of the bottom-left box (from 27 mm).
# Millimetres from the left/top edge; a negative value is from the right/bottom edge.
ALIAS_BANDS_MM = (("top-right", (-27.0, 20.6, -8.0, 26.5)), ("bottom-left", (8.0, -14.4, 25.8, -7.0)))


def alias_boxes(w, h):
    """[(corner, pixel box, page fractions)] of the two alias bands of a w x h image of a whole page, on the paper whose shape is
    nearest (a sideways image is read as the paper turned)."""
    width, height = paper_mm(w, h)
    boxes = []
    for corner, bounds in ALIAS_BANDS_MM:
        mm = [value if value >= 0 else (width if index % 2 == 0 else height) + value for index, value in enumerate(bounds)]
        fractions = tuple(round(value / (width if index % 2 == 0 else height), 5) for index, value in enumerate(mm))
        boxes.append((corner, tuple(int(value * (w if index % 2 == 0 else h)) for index, value in enumerate(fractions)), fractions))
    return boxes


def main_regions(request, image, original, Image):
    if request.get("operation") == "aliases":
        regions = []
        for rotation in (0, 90, 180, 270):
            oriented = image.rotate(rotation, expand=True)
            w, h = oriented.size
            for corner, box, bounds in alias_boxes(w, h):
                name = f"alias-{len(regions)}.png"
                pixels, digest = save_crop(oriented.crop(box), name, 2.0, Image)
                regions.append({"name": name, "corner": corner, "rotation": rotation,
                    "bounds": list(box), "normalized_bounds": list(bounds), "pixels": pixels, "image_sha256": digest})
        Path("/work/prepared.json").write_text(json.dumps({"regions": regions, "original_pixels": original,
            "crop_profile": "faculty-corner-alias-v2", "pdf_render_dpi": 300 if request["is_pdf"] else None}))
        return
    if request.get("operation") == "fields":
        # Caller-chosen boxes of an image the caller composed; nothing is rotated or searched for.
        regions = []
        for box in request["boxes"]:
            box = (min(box[0], image.width), min(box[1], image.height), min(box[2], image.width), min(box[3], image.height))
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                raise ValueError("A field box lies outside the image.")
            name = f"field-{len(regions)}.png"
            pixels, digest = save_crop(image.crop(box), name, 1.0, Image)
            regions.append({"name": name, "bounds": list(box), "pixels": pixels, "image_sha256": digest})
        Path("/work/prepared.json").write_text(json.dumps({"regions": regions, "original_pixels": original,
            "crop_profile": "caller-fields-v1", "pdf_render_dpi": None}))
        return
    if request.get("operation") == "bands":
        # One horizontal band (or the whole page) at two rotations, so an
        # upside-down sheet still shows its header. Pixels only, no catalog data.
        band = request.get("band", "top")
        bounds = {"top": (0.0, 0.0, 1.0, 0.25), "bottom": (0.0, 0.75, 1.0, 1.0), "full": (0.0, 0.0, 1.0, 1.0)}[band]
        regions = []
        for rotation in (0, 180):
            oriented = image.rotate(rotation, expand=True)
            w, h = oriented.size
            box = tuple(int(value * (w if index % 2 == 0 else h)) for index, value in enumerate(bounds))
            name = f"band-{len(regions)}.png"
            pixels, digest = save_crop(oriented.crop(box), name, 2.0 if band != "full" else 1.0, Image)
            regions.append({"name": name, "band": band, "rotation": rotation,
                "bounds": list(box), "normalized_bounds": list(bounds), "pixels": pixels, "image_sha256": digest})
        Path("/work/prepared.json").write_text(json.dumps({"regions": regions, "original_pixels": original,
            "crop_profile": "faculty-version-band-v1", "pdf_render_dpi": 300 if request["is_pdf"] else None}))
        return
    pixels, digest = save_crop(image, "page.png", 1.0, Image)
    Path("/work/prepared.json").write_text(json.dumps({"original_pixels": original, "ocr_pixels": pixels,
        "image_sha256": digest, "pdf_render_dpi": 300 if request["is_pdf"] else None}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Parser messages can contain source data. Return only controlled errors.
        message = str(error) if type(error) is ValueError else "The selected image or PDF page could not be prepared."
        Path("/work/error.json").write_text(json.dumps({"error": message}))
