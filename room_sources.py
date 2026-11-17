"""Bounded source notes and physical geometry for workspace-owned rooms.

Only extracted facts are stored in the catalog. Source documents are not payload
owners, and an uncertain note never selects a seat or changes its identity.
"""
import hashlib
import io
import math
import re
import zipfile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MAX_ISSUES = 1024 * 1024
WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def issue_document(content, filename):
    if not filename.lower().endswith(".docx") or not content or len(content) > MAX_ISSUES:
        raise ValueError("Choose a nonempty Word (.docx) seat-issue document up to 1 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(item.file_size for item in entries) > 8 * 1024**2:
                raise ValueError("The seat-issue document exceeds the unpacked size limit.")
            info = archive.getinfo("word/document.xml")
            if info.file_size > 2 * 1024**2:
                raise ValueError("The seat-issue document is too large.")
            root = ElementTree.fromstring(archive.read(info))
        paragraphs = [" ".join("".join(t.text or "" for t in p.iter(WORD + "t")).split())
                      for p in root.iter(WORD + "p")]
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, DefusedXmlException,
            RuntimeError, NotImplementedError) as error:
        raise ValueError("The seat-issue document could not be read.") from error
    paragraphs = [text for text in paragraphs if text]
    if len(paragraphs) > 500 or sum(map(len, paragraphs)) > 50000:
        raise ValueError("The seat-issue document contains too many notes.")
    return {"filename": filename, "sha256": hashlib.sha256(content).hexdigest(), "paragraphs": paragraphs}


def apply_issues(chart, source, room_name):
    """Apply exact unusable-seat notes; retain every other statement for review."""
    paragraphs = source["paragraphs"]
    headings = [text for text in paragraphs if text.casefold().endswith(" seat issues")]
    if headings and any(text[:-12].strip().casefold() != room_name.strip().casefold() for text in headings):
        raise ValueError("The seat-issue document names a different room.")
    seats = {seat["label"].casefold(): seat for seat in chart["seats"]}
    review = chart.setdefault("review_required", [])
    for text in paragraphs:
        if text in headings:
            continue
        chart["warnings"].append(f"{source['filename']}: {text}")
        match = re.fullmatch(r"(\d+[A-Z]+|[A-Z]+\d+)\s+((?:missing|broken) desk|chair and desk missing)", text, re.I)
        if match and match[1].casefold() in seats:
            seat = seats[match[1].casefold()]
            seat["broken"] = True
            seat["notes"] = "; ".join(dict.fromkeys(filter(None, (seat.get("notes", ""), text))))
        else:
            # Corrections without a row, or shortages without seat locations, are
            # not enough evidence to relabel or disable a particular chair.
            review.append(text)
    chart["review_required"] = list(dict.fromkeys(review))
    chart["issue_source"] = source
    return chart


def validate_layout(layout, seats):
    if not layout:
        return {}
    if not isinstance(layout, dict) or layout.get("kind") != "floorplan":
        raise ValueError("The room floor plan has an unsupported format.")
    def number(value, bound=100000):
        return type(value) in (int, float) and math.isfinite(value) and abs(value) <= bound
    if any(not number(layout.get(key)) or layout[key] <= 0 for key in ("width", "height")):
        raise ValueError("The room floor plan has invalid dimensions.")
    placements = layout.get("seats")
    if not isinstance(placements, dict) or set(placements) != {seat["label"] for seat in seats}:
        raise ValueError("Every room seat must have exactly one floor-plan placement.")
    for item in placements.values():
        if not isinstance(item, dict) or any(not number(item.get(k)) for k in ("x", "y", "width", "height")):
            raise ValueError("A room seat has invalid physical coordinates.")
        if item["width"] <= 0 or item["height"] <= 0 or not number(item.get("rotation", 0), 360):
            raise ValueError("A room seat has invalid physical dimensions.")
        if item["x"] < 0 or item["y"] < 0 or item["x"] + item["width"] > layout["width"] or item["y"] + item["height"] > layout["height"]:
            raise ValueError("A room seat lies outside its floor plan.")
    features = layout.get("features", [])
    if not isinstance(features, list) or len(features) > 30000:
        raise ValueError("The room floor plan has too many features.")
    total = 0
    for item in features:
        if not isinstance(item, dict) or item.get("kind") not in ("path", "rect", "text"):
            raise ValueError("The room floor plan has an unsupported feature.")
        if item["kind"] == "path":
            path = item.get("d", "")
            if not isinstance(path, str) or not re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9eE.,+\s-]+", path):
                raise ValueError("The room floor plan has an invalid outline.")
            total += len(path)
        else:
            if any(not number(item.get(k)) for k in ("x", "y")):
                raise ValueError("The room floor plan has invalid feature coordinates.")
            if item["kind"] == "rect" and any(not number(item.get(k)) or item[k] <= 0 for k in ("width", "height")):
                raise ValueError("The room floor plan has invalid feature dimensions.")
            if item["kind"] == "text" and (not isinstance(item.get("text"), str) or len(item["text"]) > 300):
                raise ValueError("The room floor plan has an invalid caption.")
            if item["kind"] == "text" and (not number(item.get("font_size", 12), 1000) or item.get("font_size", 12) <= 0):
                raise ValueError("The room floor plan has an invalid caption size.")
        if not number(item.get("rotation", 0), 360):
            raise ValueError("The room floor plan has an invalid rotation.")
    if total > 5 * 1024**2:
        raise ValueError("The room floor plan exceeds the geometry size limit.")
    return layout
