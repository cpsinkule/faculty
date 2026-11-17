"""Synthetic Gradescope-style bubble sheets for tests, drawn from the stored geometry (the official PDF is not in the repository)."""
import io

import numpy as np
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from bubble_sheets import reader


def sheet(answers, version="A", *, faint=None, shift=(0, 0), upside_down=False, damaged_marker=None):
    """`answers` maps question -> letters to fill; `faint` maps question -> letters with an erased-looking mark."""
    data = reader.template()
    page = data["pages"][0]
    image = Image.new("L", tuple(data["pixels"]), 255)
    draw = ImageDraw.Draw(image)
    for name, marker in page["markers"].items():
        patch = Image.fromarray(marker["patch"].astype(np.uint8))
        if name == damaged_marker:  # a staple tear: the marker is mostly gone
            patch = Image.new("L", patch.size, 255)
        image.paste(patch, (round(marker["centre"][0] - patch.width / 2), round(marker["centre"][1] - patch.height / 2)))

    def bubble(x, y, d, fill):
        r = d / 2
        draw.ellipse((x - r, y - r, x + r, y + r), outline=40, width=2, fill=fill)
        if fill is None:
            draw.line((x - 3, y - 4, x + 3, y + 4), fill=90, width=1)  # a stand-in for the printed letter

    for question, row in page["questions"].items():
        for letter, (x, y, d) in zip("ABCDE", row):
            fill = 35 if letter in (answers or {}).get(int(question), "") else 225 if letter in (faint or {}).get(int(question), "") else None
            bubble(x, y, d, fill)
    for letter, (x, y, d) in zip("ABCDE", page["version"]):
        bubble(x, y, d, 35 if letter in version else None)
    for number in range(1, 101):  # question numbers and rules give the page the template's overall look
        x, y, _ = page["questions"][str(number)][0]
        draw.text((x - 46, y - 6), str(number), fill=30)
    left, top, right, bottom = page["fields"]["name"]
    draw.rectangle((left - 60, top - 6, right + 8, bottom + 6), outline=170)
    left, top, right, bottom = page["fields"]["id"]
    draw.rectangle((left - 50, top - 6, right + 8, bottom + 6), outline=170)
    if shift != (0, 0):
        moved = Image.new("L", image.size, 255)
        moved.paste(image, shift)
        image = moved
    return image.rotate(180) if upside_down else image


def other_page(text="Problem 3. Show that the series converges."):
    image = Image.new("L", tuple(reader.template()["pixels"]), 255)
    draw = ImageDraw.Draw(image)
    for line in range(14):
        draw.text((180, 240 + 120 * line), text, fill=20)
        draw.line((180, 300 + 120 * line, 1500, 300 + 120 * line), fill=120)
    return image


def pdf(images):
    buffer = io.BytesIO()
    document = canvas.Canvas(buffer, pagesize=(612, 792))
    for image in images:
        document.drawImage(ImageReader(image), 0, 0, width=612, height=792)
        document.showPage()
    document.save()
    return buffer.getvalue()
