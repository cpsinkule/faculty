"""Recipient-only seating PDFs derived from the existing term-owned chart.

The retained mail snapshot belongs to that chart, not to the room or roster.
Sanitization is an allowlist boundary: rendering never receives any student's
name, identifier, response-card state, exam letter, or private notes.
Only existing room geometry is copied into the recipient's immutable snapshot.
No PDF source is opened and no external resource is loaded by the renderer.
"""

from collections import defaultdict
import io
import math
from pathlib import Path

import reportlab
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.svgpath import SvgPath
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from room_sources import validate_layout


def _text(value, limit=300):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("The seating chart contains invalid display text.")
    return " ".join(value.split())


def _grid_number(item, key, default=1):
    value = item.get(key, default)
    if type(value) is not int or not 1 <= value <= 200:
        raise ValueError("The seating chart contains invalid grid positions.")
    return value


def _grid_geometry(room):
    """Preserve source cell gaps and table groups, allowing chairs enough height."""
    seats = room["seats"]
    rows = _grid_number(room, "grid_rows")
    columns = _grid_number(room, "grid_columns")
    heights = [80.0] * rows
    groups = defaultdict(list)
    for seat in seats:
        row, column = _grid_number(seat, "row"), _grid_number(seat, "column")
        rs, cs = _grid_number(seat, "row_span"), _grid_number(seat, "column_span")
        if row + rs - 1 > rows or column + cs - 1 > columns:
            raise ValueError("A room seat lies outside its seating chart.")
        if seat.get("group"):
            groups[seat["group"]].append(seat)
    for group in groups.values():
        first = group[0]
        start, span = first["row"] - 1, _grid_number(first, "row_span")
        across = _grid_number(first, "column_span")
        required = 24 + math.ceil(len(group) / across) * 72
        extra = max(0, required - sum(heights[start:start + span])) / span
        for index in range(start, start + span):
            heights[index] += extra
    offsets = [0.0]
    for height in heights:
        offsets.append(offsets[-1] + height)

    def cell(item):
        row, column = _grid_number(item, "row"), _grid_number(item, "column")
        rs, cs = _grid_number(item, "row_span"), _grid_number(item, "column_span")
        if row + rs - 1 > rows or column + cs - 1 > columns:
            raise ValueError("A room landmark lies outside its seating chart.")
        return (column - 1) * 92.0, offsets[row - 1], cs * 92.0, offsets[row + rs - 1] - offsets[row - 1]

    placements, features = {}, []
    for seat in seats:
        if seat.get("group"):
            continue
        x, y, width, height = cell(seat)
        placements[seat["label"]] = {"x": x + 4, "y": y + 4, "width": width - 8, "height": height - 8, "rotation": 0}
    for name, group in groups.items():
        group.sort(key=lambda seat: (seat.get("seat_index", 0), seat["label"]))
        x, y, width, height = cell(group[0])
        across = _grid_number(group[0], "column_span")
        chair_width, chair_height = (width - 8) / across, (height - 26) / math.ceil(len(group) / across)
        features.append({"kind": "rect", "role": "table", "x": x + 1, "y": y + 1, "width": width - 2, "height": height - 2, "rotation": 0})
        features.append({"kind": "text", "x": x + width / 2, "y": y + 16, "text": _text(name), "font_size": 12, "rotation": 0})
        for index, seat in enumerate(group):
            placements[seat["label"]] = {"x": x + 6 + (index % across) * chair_width,
                "y": y + 24 + (index // across) * chair_height,
                "width": chair_width - 4, "height": chair_height - 4, "rotation": 0}
    for item in room.get("landmarks", []):
        x, y, width, height = cell(item)
        if item.get("kind") != "row" and item.get("column_span", 1) == 1:
            next_columns = [s["column"] for s in [*seats, *room.get("landmarks", [])]
                            if s is not item and s["row"] == item["row"] and s["column"] > item["column"]]
            width = (min(next_columns, default=columns + 1) - item["column"]) * 92
        features.append({"kind": "text", "x": x + width / 2, "y": y + height / 2,
                         "text": _text(item.get("text", "")), "font_size": 15,
                         "rotation": 90 if item.get("row_span", 1) >= 3 and item.get("column_span", 1) == 1 else 0})
    return {"kind": "grid", "width": columns * 92.0, "height": offsets[-1], "features": features}, placements


def _physical_geometry(room):
    source = validate_layout(room.get("layout"), room["seats"])
    if not source:
        return _grid_geometry(room)
    features = []
    for item in source.get("features", []):
        kind = item["kind"]
        clean = {"kind": kind}
        if kind == "path":
            clean["d"] = item["d"]
        else:
            clean.update({key: item[key] for key in ("x", "y")})
            clean["rotation"] = item.get("rotation", 0)
            if kind == "rect":
                clean.update({key: item[key] for key in ("width", "height")})
            else:
                clean.update(text=_text(item["text"]), font_size=item.get("font_size", 12))
        if item.get("role") in ("wall", "table", "label", "legend"):
            clean["role"] = item["role"]
        features.append(clean)
    placements = {label: {key: box.get(key, 0) for key in ("x", "y", "width", "height", "rotation")}
                  for label, box in source["seats"].items()}
    return {"kind": "floorplan", "width": source["width"], "height": source["height"], "features": features}, placements


def sanitize_chart(chart, enrollment_id):
    """Return a fresh JSON-safe recipient-only snapshot of SeatingService.get().

    The caller reads the chart under its roster lifecycle lock and separately
    validates the email audience. Missing, ambiguous and unusable seats fail
    closed. Empty/unavailable seat conditions expose no accommodation information.
    """
    members = [student for student in chart.get("students", []) if student.get("enrollment_id") == enrollment_id]
    if len(members) != 1 or members[0].get("status") != "seated" or not members[0].get("seat_id"):
        raise ValueError("The recipient must have exactly one current usable seat.")
    student = members[0]
    room = chart["room"]
    if not 1 <= len(room["seats"]) <= 2000:
        raise ValueError("The room contains an invalid number of seats.")
    own = [seat for seat in room["seats"] if (seat.get("student") or {}).get("enrollment_id") == enrollment_id]
    if (len(own) != 1 or own[0]["id"] != student["seat_id"] or own[0].get("broken") or own[0].get("blocked")):
        raise ValueError("The recipient's current room seat does not match the seating chart.")
    if len({seat["label"] for seat in room["seats"]}) != len(room["seats"]):
        raise ValueError("The seating chart contains duplicate seat labels.")
    geometry, placements = _physical_geometry(room)
    clean_seats = []
    for seat in room["seats"]:
        state = ("own" if seat is own[0] else "occupied" if seat.get("student")
                 else "unavailable" if seat.get("broken") or seat.get("blocked") else "empty")
        clean_seats.append({"label": _text(seat["label"]), "state": state, **placements[seat["label"]]})
    geometry["seats"] = clean_seats
    return {"schema_version": 1, "title": _text(chart.get("title", "Seating chart")),
            "code": _text(chart.get("code", "")), "term_label": _text(chart.get("term_label", "")),
            "room_name": _text(chart.get("room_name", room.get("name", ""))),
            "recipient": {"seat": _text(own[0]["label"])}, "room": geometry}


def _font():
    """Use the font bundled with ReportLab, without network/system dependencies."""
    name = "FacultySeatingVera"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")))
    return name


def _wrap(text, font, size, width):
    lines, line = [], ""
    for word in text.split():
        if line and pdfmetrics.stringWidth(line + " " + word, font, size) > width:
            lines.append(line)
            line = ""
        # Unbroken names/labels still stay inside their seat rather than clipping.
        for char in word:
            if line and pdfmetrics.stringWidth(line + char, font, size) > width:
                lines.append(line)
                line = ""
            line += char
        line += " "
    if line.strip():
        lines.append(line.strip())
    return [line.strip() for line in lines] or [""]


def _centered(c, text, x, y, width, height, font, size, color):
    size = min(size, height / 1.3)
    lines = _wrap(text, font, size, max(1, width))
    while len(lines) * size * 1.15 > height and size > 0.7:
        size *= 0.9
        lines = _wrap(text, font, size, max(1, width))
    c.saveState()
    c.scale(1, -1)
    c.setFillColor(color)
    c.setFont(font, size)
    top = -y + (len(lines) - 1) * size * 0.575 - size * 0.35
    for index, line in enumerate(lines):
        c.drawCentredString(x, top - index * size * 1.15, line)
    c.restoreState()


def _draw_plan(c, snapshot, crop, bounds, font):
    room = snapshot["room"]
    left, bottom, width, height = bounds
    x, y, crop_width, crop_height = crop
    scale = min(width / crop_width, height / crop_height)
    dx = left + (width - crop_width * scale) / 2
    dy = bottom + (height - crop_height * scale) / 2
    c.saveState()
    clip = c.beginPath()
    clip.rect(dx, dy, crop_width * scale, crop_height * scale)
    c.clipPath(clip, stroke=0)
    c.translate(dx - x * scale, dy + (crop_height + y) * scale)
    c.scale(scale, -scale)
    # Retained CAD outlines use the same y-down coordinates as the browser.
    for item in room["features"]:
        c.saveState()
        color = colors.HexColor("#697584" if item.get("role") == "label" else "#abb3bd")
        c.setStrokeColor(color)
        c.setLineWidth(0.55 / scale)
        if item["kind"] == "path":
            drawing = Drawing(room["width"], room["height"])
            drawing.add(SvgPath(item["d"], fillColor=None, strokeColor=color, strokeWidth=0.55 / scale))
            renderPDF.draw(drawing, c, 0, 0)
        elif item["kind"] == "rect":
            c.translate(item["x"] + item["width"] / 2, item["y"] + item["height"] / 2)
            c.rotate(item.get("rotation", 0))
            c.rect(-item["width"] / 2, -item["height"] / 2, item["width"], item["height"], fill=0, stroke=1)
        else:
            c.translate(item["x"], item["y"])
            c.rotate(item.get("rotation", 0))
            size = item.get("font_size", 12)
            _centered(c, item["text"], 0, 0, room["width"], size * 1.3, font, size, color)
        c.restoreState()
    for seat in room["seats"]:
        c.saveState()
        w, h = seat["width"], seat["height"]
        c.translate(seat["x"] + w / 2, seat["y"] + h / 2)
        c.rotate(seat.get("rotation", 0))
        own = seat["state"] == "own"
        filled = own or seat["state"] == "occupied"
        c.setFillColor(colors.HexColor("#d6e8ff" if own else "#dce0e5") if filled else colors.white)
        c.setStrokeColor(colors.HexColor("#1554a5" if own else "#7e8792"))
        c.setLineWidth((1.7 if own else 0.6) / scale)
        c.roundRect(-w / 2, -h / 2, w, h, min(w, h) * 0.06, fill=1, stroke=1)
        if seat["state"] == "unavailable":
            c.setDash(2 / scale, 2 / scale)
            c.line(-w / 2, -h / 2, w / 2, h / 2)
        content = "Your seat" if own else {"occupied": "Occupied", "empty": "", "unavailable": "Unavailable"}[seat["state"]]
        ink = colors.HexColor("#15365c" if own else "#273444")
        size = min(11 / scale, w / 5.5, h / 4)
        if content:
            _centered(c, content, 0, -h * 0.22, w * 0.9, h * 0.4, font, size, ink)
            _centered(c, seat["label"], 0, h * 0.26, w * 0.9, h * 0.38, font, size, ink)
        else:
            _centered(c, seat["label"], 0, 0, w * 0.9, h * 0.8, font, size, ink)
        c.restoreState()
    c.restoreState()


def render_pdf(snapshot):
    """Render deterministic PDF bytes from a sanitize_chart() snapshot only."""
    if snapshot.get("schema_version") != 1:
        raise ValueError("The personal seating chart snapshot is unsupported.")
    room = snapshot["room"]
    own = [seat for seat in room["seats"] if seat["state"] == "own"]
    if len(own) != 1 or own[0]["label"] != snapshot["recipient"]["seat"]:
        raise ValueError("The personal seating chart must identify exactly one seat.")
    font = _font()
    # Keep the complete room's original orientation; the detail can be read at
    # normal zoom, without asking students to find tiny text in a large hall.
    pagesize = landscape(letter) if room["width"] >= room["height"] else letter
    page_width, page_height = pagesize
    bounds = (28, 42, page_width - 56, page_height - 172)
    full = (0, 0, room["width"], room["height"])
    scale = min(bounds[2] / room["width"], bounds[3] / room["height"])
    crops = [("Room overview", full)]
    seat = own[0]
    # The local detail enlarges nearby seats while retaining room orientation.
    if min(seat["width"], seat["height"]) * scale < 58:
        detail_scale = max(scale, 84 / min(seat["width"], seat["height"]))
        crop_width, crop_height = min(room["width"], bounds[2] / detail_scale), min(room["height"], bounds[3] / detail_scale)
        cx, cy = seat["x"] + seat["width"] / 2, seat["y"] + seat["height"] / 2
        crops.append(("Your seating area · enlarged from the overview", (
            max(0, min(room["width"] - crop_width, cx - crop_width / 2)),
            max(0, min(room["height"] - crop_height, cy - crop_height / 2)), crop_width, crop_height)))
    output = io.BytesIO()
    c = Canvas(output, pagesize=pagesize, invariant=1, pageCompression=1)
    c.setTitle("Personal seating chart")
    c.setAuthor("Faculty")
    c.setCreator("Faculty")
    for index, (caption, crop) in enumerate(crops):
        c.setFillColor(colors.HexColor("#173654"))
        y = page_height - 29
        headings = [(" · ".join(filter(None, (snapshot["code"], snapshot["title"], snapshot["room_name"]))), 12),
                    (f"Your seat: {snapshot['recipient']['seat']}", 11),
                    (caption, 9)]
        for text, size in headings:
            lines = _wrap(text, font, size, page_width - 56)
            if len(lines) > 2:
                size *= 2 / len(lines)
                lines = _wrap(text, font, size, page_width - 56)
            c.setFont(font, size)
            for line in lines:
                c.drawString(28, y, line)
                y -= size * 1.3
            y -= 6
        _draw_plan(c, snapshot, crop, bounds, font)
        c.setFont(font, 8)
        c.setFillColor(colors.HexColor("#354355"))
        c.drawString(28, 25, "Blue: your seat    Gray: Occupied    White: empty or unavailable")
        c.drawRightString(page_width - 28, 25, f"{index + 1} / {len(crops)}")
        c.showPage()
    c.save()
    return output.getvalue()
