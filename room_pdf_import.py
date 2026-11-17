"""Import the ten reviewed ILCB architectural plans by their exact source digest.

Rooms are workspace-owned catalog resources. These CAD PDFs contain outlined
glyphs rather than searchable text. Their reviewed, numeric vector profiles are
bundled application data; an arbitrary PDF must never be interpreted as a known
room merely because its filename resembles one. No native PDF parser runs during
an upload. See room_pdf_sources/README.md for extraction and review provenance.
"""

import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from room_import import MAX_GRID_COLUMNS, MAX_GRID_ROWS, MAX_LABEL, MAX_SEATS, MAX_UPLOAD
from room_sources import validate_layout

SOURCES = Path(__file__).with_name("room_pdf_sources")


@lru_cache(maxsize=1)
def source_index():
    index = SOURCES / "index.json"
    if not index.exists():  # the public copy leaves out the reviewed ILCB plans
        return {}
    return json.loads(index.read_text())


@lru_cache(maxsize=10)
def _profile(digest):
    entry = source_index()[digest]
    profile_name = entry["profile"]
    if Path(profile_name).name != profile_name:
        raise ValueError("The bundled room profile has an invalid name.")
    chart = json.loads((SOURCES / profile_name).read_text())
    seats = chart["seats"]
    source = chart["source_profile"]
    if (source["sha256"] != digest or source["room"] != entry["room"]
            or len(seats) != entry["seat_count"] or not 1 <= len(seats) <= MAX_SEATS
            or not 1 <= chart["rows"] <= MAX_GRID_ROWS
            or not 1 <= chart["columns"] <= MAX_GRID_COLUMNS
            or len({seat["label"].casefold() for seat in seats}) != len(seats)):
        raise ValueError("The bundled room profile failed validation.")
    for seat in seats:
        if (not 1 <= len(seat["label"]) <= MAX_LABEL
                or not 1 <= seat["row"] <= chart["rows"]
                or not 1 <= seat["column"] <= chart["columns"]):
            raise ValueError("The bundled room profile has an invalid seat.")
    validate_layout(chart["layout"], seats)
    return chart


def parse_pdf_chart(content, filename):
    """Return a fresh, catalog-ready chart only for a reviewed source PDF."""
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError("Choose a nonempty room layout PDF of 10 MB or less.")
    if Path(filename).suffix.casefold() != ".pdf":
        raise ValueError("Choose a PDF room layout.")
    digest = hashlib.sha256(content).hexdigest()
    if digest not in source_index():
        raise ValueError(
            "This PDF is not one of the ten reviewed ILCB room layouts. "
            "Use an Excel seating chart, or have the new PDF's seat labels and "
            "physical layout reviewed before importing it. Renaming a file does not change its room."
        )
    # Issue notes and catalog imports mutate returned seat dictionaries. Never
    # allow those mutations to alter a cached profile or a later room import.
    return copy.deepcopy(_profile(digest))
