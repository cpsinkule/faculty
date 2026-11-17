"""Derive the sheet geometry Faculty reads from Gradescope's official bubble sheet template.

    python -m bubble_sheets.derive_template /path/to/template.pdf

Gradescope's PDF is not kept in this repository. Its exact bytes are recognised
by SHA-256, as the reviewed room plans are, and only measurements are retained:
where each bubble, corner marker and handwriting box sits, the four marker
images used to align a scan, and a 20-DPI picture of each page used to tell a
bubble sheet from any other page. A changed template needs a new profile.
"""
import base64
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

PROFILE = "gradescope-2020-05-01"
SOURCE_SHA256 = "17c7b1c8c46109a830447091b10a7609bea9bd61c00f3952bee33994c8587f22"
DPI, MARKER_PT = 200, 18.0
MARKERS = {"top_left": (40.1, 742.3), "top_right": (553.9, 742.3), "bottom_left": (40.1, 36.0), "bottom_right": (553.9, 36.0)}
# Handwriting boxes in template points from the bottom-left corner: left, bottom, right, top.
FIELDS = {"id": (84, 672, 302, 703), "name": (92, 707, 302, 738)}


def png(array):
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, "PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def derive(content):
    if hashlib.sha256(content).hexdigest() != SOURCE_SHA256:
        raise ValueError("This is not the reviewed Gradescope Bubble Sheet v2020.05.01 template; a changed template needs its own profile.")
    scale, reader, document, pages = DPI / 72, PdfReader(io.BytesIO(content)), pdfium.PdfDocument(content), []
    for number, page in enumerate(reader.pages):
        items = []
        page.extract_text(visitor_text=lambda text, cm, tm, font, size: items.append((text.strip(), tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5], size)) if text.strip() else None)
        image = np.asarray(document[number].render(scale=scale).to_pil().convert("L"))
        height = image.shape[0]

        def ring(x, y):
            """Centre and diameter of the printed circle around a bubble's letter, in 200-DPI pixels from the top-left."""
            cx, cy = x * scale + 4, height - y * scale - 5
            rows, columns = np.nonzero(image[int(cy - 16):int(cy + 16), int(cx - 16):int(cx + 16)] < 160)
            return [round(int(cx - 16) + (columns.min() + columns.max()) / 2, 1), round(int(cy - 16) + (rows.min() + rows.max()) / 2, 1),
                    round((columns.max() - columns.min() + rows.max() - rows.min()) / 2, 1)]

        letters = [(text, x, y) for text, x, y, size in items if size == 5.0 and text in "ABCDE"]
        questions = {}
        for text, x, y, size in items:
            if text.isdigit() and size != 5.0 and 1 <= int(text) <= 200:
                row = sorted((item for item in letters if abs(item[2] - y) < 4 and 0 < item[1] - x < 110), key=lambda item: item[1])[:5]
                if [item[0] for item in row] == list("ABCDE"):
                    questions[int(text)] = [ring(item[1], item[2]) for item in row]
        if len(questions) != 100:
            raise ValueError("The template's question grid was not found.")
        version = [ring(item[1], item[2]) for item in sorted((item for item in letters if abs(item[2] - 719.7) < 2 and item[1] < 445), key=lambda item: item[1])] if number == 0 else []
        markers = {name: {"centre": [round((x + MARKER_PT / 2) * scale, 1), round(height - (y + MARKER_PT / 2) * scale, 1)],
                          "patch": png(image[int(height - (y + MARKER_PT) * scale) - 6:int(height - y * scale) + 6, int(x * scale) - 6:int((x + MARKER_PT) * scale) + 6])}
                   for name, (x, y) in MARKERS.items()}
        fields = {name: [round(left * scale), round(height - top * scale), round(right * scale), round(height - bottom * scale)]
                  for name, (left, bottom, right, top) in FIELDS.items()} if number == 0 else {}
        pages.append({"page": number + 1, "questions": {str(key): value for key, value in sorted(questions.items())}, "version": version,
                      "markers": markers, "fields": fields, "thumbnail": png(np.asarray(Image.fromarray(image).resize((170, 220), Image.BOX)))})
    return {"profile": PROFILE, "label": "Gradescope Bubble Sheet v2020.05.01", "source_sha256": SOURCE_SHA256, "dpi": DPI,
            "pixels": [int(image.shape[1]), int(image.shape[0])], "choices": "ABCDE", "pages": pages}


if __name__ == "__main__":
    target = Path(__file__).parent / "templates" / f"{PROFILE}.json"
    target.write_text(json.dumps(derive(Path(sys.argv[1]).read_bytes()), separators=(",", ":")))
    print(f"wrote {target} ({target.stat().st_size // 1024} KB)")
