"""Bounded scan/template registration and non-destructive derived response images."""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile

from document_identity.process import sandbox_command

# What cut a crop. A changed cut gets a new name: crops cut the old way are then never reused for a new attempt (`jobs`).
# v2 (2026-09-23): a page turned by at least `deskew.MINIMUM` degrees is also tried straightened (`deskew`), and alignment
# is scored on the part of the page the scanner captured.
# v3 (2026-09-25): every page is also tried turned half a turn, and a sheet fed upside down is cut from the page turned upright
# when the blank exam says so (`deskew.turned_wins`). Every other page is cut exactly as under v2.
# v4 (2026-09-25): the blank exam is also tried at other sizes (`deskew.search`), for paper printed smaller or larger than its
# blank; a page aligned at its blank's own size is cut exactly as under v3.
VERSION = "frq-extraction-v4"
PASS = .55  # the aligner's pass mark
KINDS = ("original", "grayscale", "contrast", "ink", "cloud")


def extract(scan, region, template=None, masks=(), sheet=False):
    """`sheet` (GRADING "DR COPIES"): the answer area is the scanned sheet itself, cut whole. The blank then only says which way
    up the sheet is (it is made here from a Word file and does not lie on the printed page), and nothing is left to align."""
    if not 0 < len(scan) <= 30 * 1024**2 or template and len(template) > 30 * 1024**2:
        raise ValueError("Response image exceeds the preparation limit.")
    with tempfile.TemporaryDirectory(prefix="faculty-frq-") as temporary:
        root = Path(temporary)
        (root / "scan").write_bytes(scan)
        if template:
            (root / "template").write_bytes(template)
        (root / "request.json").write_text(json.dumps({"region": region, "masks": masks, **({"sheet": True} if sheet else {})}))
        command = sandbox_command(root, ["/venv/bin/python", "-m", "grading.extraction", "/work"])
        index = command.index("--")
        command[index:index] = ["--ro-bind", str(Path(__file__).parent.resolve()), "/app/grading"]
        try:
            result = subprocess.run(command, cwd=Path(__file__).parent.parent,
                env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        except subprocess.TimeoutExpired:
            raise ValueError("Response preparation exceeded its time limit.") from None
        if result.returncode or not (root / "result.json").is_file():
            raise ValueError("Response preparation failed in the document sandbox.")
        data = json.loads((root / "result.json").read_text())
        if "error" in data:
            raise ValueError(data["error"])
        return {"metadata": data, "images": {kind: (root / (kind + ".png")).read_bytes()
            for kind in KINDS}}


def _prepare(scan_bytes, region, template_bytes=None, masks=(), sheet=False):
    """Runs only in the sandbox (direct calls are reserved for synthetic tests)."""
    import io
    import math
    import numpy as np
    from PIL import Image, ImageChops, ImageEnhance, ImageOps
    Image.MAX_IMAGE_PIXELS = 30_000_000
    image = Image.open(io.BytesIO(scan_bytes))
    if image.width * image.height > 30_000_000:
        raise ValueError("Image is too large.")
    image.load()
    image = image.convert("RGB")
    w, h = image.size
    from grading import deskew
    # The page as scanned and, when it is clearly turned, the same page straightened. With a blank exam, whichever aligns
    # better is cut (the scan wins a tie); without one nothing can check the straightened page, so the reading decides.
    # Each is also tried upside down, used only when the blank exam clearly says so; without one nothing is turned over.
    tilt = deskew.plan(image)
    upright = [(image, None)] + ([deskew.straighten(image, tilt["angle"], (255, 255, 255))] if tilt["applied"] else [])
    candidates = upright + deskew.upside_down(upright)
    chosen = len(upright) - 1
    registered, reason, template, quality = False, "No template is linked; inspect the response boundary.", None, None
    dx = dy = angle = 0
    scale = 1
    orientation = {"rule": deskew.TURN_RULE, "turned": False, "upright_quality": None, "turned_quality": None}
    sizing = {"rule": deskew.SIZE_RULE, "size": 1, "own_size_quality": None}
    if template_bytes:
        template = Image.open(io.BytesIO(template_bytes))
        template.load()
        if template.width * template.height > 30_000_000:
            raise ValueError("Template is too large.")
        template = template.convert("L").resize(image.size)
        # Global similarity search on bounded grayscale thumbnails. Handwriting
        # is additional ink, so correlation proposes alignment but cannot certify it.
        size = (384, max(64, round(h * 384 / w)))
        small, sizes = template.resize(size), {}
        best = {False: None, True: None}  # the best alignment of the page as scanned, and of the page turned over
        for index, (page, captured) in enumerate(candidates):
            over = index >= len(upright)
            # A straightened page is compared only where the scanner saw paper: its filled corners are not blank paper.
            mask = None if captured is None else np.asarray(captured.resize(size, Image.NEAREST)) > 0
            observed = np.asarray(page.convert("L").resize(size), dtype=float)
            observed = deskew.masked((255 - observed) / 255, mask)
            fft = np.fft.fft2(observed)

            def score(scale, candidate):
                if scale not in sizes:
                    sizes[scale] = deskew.resized(small, scale, 255)
                moving = np.asarray(sizes[scale].rotate(candidate, fillcolor=255), dtype=float)
                moving = deskew.masked((255 - moving) / 255, mask)
                corr = np.fft.ifft2(fft * np.conj(np.fft.fft2(moving))).real
                limit = max(1, round(size[0] * .05))
                shifts = [(float(corr[y % size[1], x % size[0]]), x, y)
                    for y in range(-limit, limit+1) for x in range(-limit, limit+1)]
                peak, x, y = max(shifts)
                return peak / max(1e-9, np.linalg.norm(observed) * np.linalg.norm(moving)), x, y
            found, own = deskew.search(score, (-3, -2, -1, 0, 1, 2, 3) if captured is None else deskew.FINE)
            if best[over] is None or found[0] > best[over][0][0]:
                best[over] = (found, own, index)
        turned = deskew.turned_wins(best[False][0][0], best[True][0][0])
        orientation.update(turned=turned, upright_quality=round(float(best[False][0][0]), 4), turned_quality=round(float(best[True][0][0]), 4))
        (quality, scale, angle, x, y), own, chosen = best[turned]
        sizing.update(size=scale, own_size_quality=round(float(own[0]), 4))
        dx, dy = x * w / size[0], y * h / size[1]
        # A plain bool: `numpy.False_ and ...` stays a NumPy value, which JSON refuses. Seen on a real paper, where a poorly aligned
        # scan crashed its preparation instead of being reported as uncertain.
        registered = bool(quality >= PASS and abs(angle) < 3)
        reason = "Template alignment is uncertain; check the original response." if not registered else ""
        if sheet:
            # The whole sheet is the answer area: the blank chose the way up above, and its place on the page moves nothing.
            dx = dy = angle = 0
            scale, template = 1, None
            registered, reason = True, ""
        else:
            template = deskew.resized(template, scale, 255).rotate(angle, fillcolor=255)
            shifted = Image.new("L", image.size, 255)
            shifted.paste(template, (round(dx), round(dy)))
            template = shifted
    elif sheet:
        registered, reason = True, ""
    # Transform question corners into scan pixels; retain an exact unresampled
    # bounding crop, with a small outside-writing margin. Rotation is template-only,
    # except that a page straightened or turned over above is cut from that image.
    image = candidates[chosen][0]
    x, y, rw, rh = [region[k] for k in ("x", "y", "width", "height")]
    radians = math.radians(-angle)
    corners = []
    for px, py in ((x, y), (x+rw, y), (x, y+rh), (x+rw, y+rh)):
        cx, cy = (px*w-w/2)*scale, (py*h-h/2)*scale
        corners.append((cx*math.cos(radians)-cy*math.sin(radians)+w/2+dx,
                        cx*math.sin(radians)+cy*math.cos(radians)+h/2+dy))
    bounds = (max(0, int(min(v[0] for v in corners)-w*.015)), max(0, int(min(v[1] for v in corners)-h*.015)),
              min(w, math.ceil(max(v[0] for v in corners)+w*.015)), min(h, math.ceil(max(v[1] for v in corners)+h*.015)))
    original = image.crop(bounds)
    if original.width < 8 or original.height < 8:
        raise ValueError("Answer area is empty or too small.")
    gray = ImageOps.grayscale(original)
    contrast = ImageEnhance.Contrast(gray).enhance(1.5)
    # Saturated subtraction is a viewing aid only; originals retain faint marks.
    ink = ImageOps.invert(ImageChops.subtract(template.crop(bounds), gray)) if template else gray.copy()
    cloud = original.copy()
    from PIL import ImageDraw
    draw = ImageDraw.Draw(cloud)
    for mask in masks:
        draw.rectangle((mask[0]*cloud.width, mask[1]*cloud.height, mask[2]*cloud.width, mask[3]*cloud.height), fill="white")
    images = {}
    for key, value in (("original", original), ("grayscale", gray), ("contrast", contrast), ("ink", ink), ("cloud", cloud)):
        output = io.BytesIO()
        value.save(output, "PNG")
        images[key] = output.getvalue()
    return {"images": images, "metadata": {"version": VERSION, "registered": registered, "reason": reason,
        "scan_size": [w, h], "crop_bounds": list(bounds), "crop_size": list(original.size),
        "template_rotation": angle, "template_translation": [dx, dy], "privacy_masks": list(masks),
        "alignment_quality": None if quality is None else round(float(quality), 4),
        # What the page's own lines said about its tilt, and whether the crop was cut from the straightened page
        # (then crop_bounds are in the straightened page's pixels, and the crop is resampled).
        "deskew": {"rule": tilt["rule"], "angle": tilt["angle"], "confidence": tilt["confidence"], "straightened": candidates[chosen][1] is not None},
        # Whether the crop was cut from the page turned half a turn (then crop_bounds are in the turned page's pixels, which
        # are the scanner's own unless it was also straightened), and how well each way up aligned with the blank exam.
        "orientation": orientation,
        # The size of the blank exam the page aligned with (1 is its own size), and how well its own size aligned.
        "template_size": sizing, **({"sheet": True} if sheet else {})}}


def main(root):
    resource.setrlimit(resource.RLIMIT_AS, (2*1024**3, 2*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (75, 75))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128*1024**2, 128*1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        value = json.loads((root / "request.json").read_text())
        data = _prepare((root / "scan").read_bytes(), value["region"],
            (root / "template").read_bytes() if (root / "template").exists() else None, value["masks"], sheet=value.get("sheet") is True)
        for key, content in data["images"].items():
            (root / (key + ".png")).write_bytes(content)
        result = data["metadata"]
    except Exception:
        result = {"error": "The response image or template could not be safely prepared."}
    (root / "result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
