"""Bounded, read-only parsing of room seating-chart workbooks. Never evaluate cells.

The department's charts are drawn on the worksheet grid:

- Row letters can move inward with tapered rows, repeat beside aisles, or label
  two banks independently. They may span two worksheet rows. A numbered cell in
  a lettered row becomes seat "K5".
- "7 Broken" marks seat 7 broken. A bare "Broken" (or "Left-handed") in the cell
  above or below a seat marks the seat in that column.
- "Table (4 seats)", or "TABLE" beside "3 Chairs", is a table holding that many
  seats; its seats are numbered 1..N in reading order within the lettered row.
- A named table's merged caption scopes the numbered seats directly above and
  below it. Uncounted movable-table notes describe planning-only, zero-seat rooms.
- Merged captions such as FRONT OF THE ROOM, Door, or STAIRS are landmarks.

Charts without a row-letter column use each cell's text as the seat label.
Merged ranges are read from the sheet XML because read-only workbooks do not
expose them; the top-left cell carries the value.
"""

import io
import posixpath
import re
import zipfile
from pathlib import Path

from defusedxml import ElementTree
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

from roster_import import MAX_UPLOAD, text_value

MAX_GRID_ROWS = 200
MAX_GRID_COLUMNS = 100
MAX_SEATS = 2000
MAX_LABEL = 40
# Scanning bounds only; the chart itself is measured from the first to the last
# filled row and column, so trailing blank or merely formatted cells do not count.
MAX_SCANNED_ROWS = 100000
MAX_SCANNED_COLUMNS = 16384
MAX_FILLED_CELLS = 5000

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
LETTERS = re.compile(r"^[A-Za-z]{1,3}$")
NUMBERED = re.compile(r"^(\d{1,4})(?:\s*[-–:]?\s*([A-Za-z][A-Za-z ()-]*))?$")
GROUP = re.compile(r"^(\d{1,3})\s*[- ]?\s*(seats?|chairs?|students?|persons?|people|spots?)\b", re.IGNORECASE)
TABLE = re.compile(r"^(tables?|desks?|benche?s?|booths?|pods?|stations?)\b", re.IGNORECASE)
TABLE_COUNT = re.compile(r"(\d{1,3})\s*[- ]?\s*(seats?|chairs?|students?|persons?|people|spots?)\b", re.IGNORECASE)
NAMED_TABLE = re.compile(r"^table\s+([A-Za-z]{1,3})$", re.IGNORECASE)
CHART_NOTE = re.compile(r"^(?:(?:all|long|movable|moveable)\s+tables?\b|tables?\s*\(no\b|no\s+(?:#|left[- ]hand))", re.IGNORECASE)
UNCOUNTED_MOVABLE = re.compile(r"^move?able\s+tables?\s*\(no\s+#", re.IGNORECASE)
LOOSE_DESK = re.compile(r"^(\d{1,2})\s+loose\s+(left[- ](?:hand(?:ed)?\s+)?)?desks?$", re.IGNORECASE)
LANDMARK = re.compile(
    r"^(front|back|rear|door|doors|aisle|board|whiteboard|chalkboard|blackboard|screen|podium|lectern|"
    r"instructor|teacher|window|windows|stage|exit|entrance|projector|wall|hallway|stairs|stairway|steps|"
    r"ramp|pillar|column|elevator|no seat|empty)\b",
    re.IGNORECASE,
)
LANDMARK_ANYWHERE = re.compile(r"\b(front|back|rear)\s+of\b|\b(door|doors|stairs|stairway|exit|entrance|podium|"
                               r"lectern|whiteboard|chalkboard|screen|projector|window|windows|aisle|hallway|elevator)\b",
                               re.IGNORECASE)
BROKEN_WORDS = {"broken", "b", "out of order", "damaged", "missing", "no chair", "removed"}
LEFT_WORDS = {"left", "l", "lh", "left handed", "left-handed", "lefthanded", "left hand", "left-hand"}


def cell_name(row, column):
    return f"{get_column_letter(column)}{row}"


ACCESSIBLE = re.compile(r"^(h|handicap(ped)?|accessible|ada)(\s*\((move?able|removable) chair\))?$")
WHEELCHAIR = re.compile(r"^wheel ?chair( only| space| spot| access(ible)?)?$")


def annotation_flags(text):
    """What a seat's note says: broken, left-handed, or an accessible seat / wheelchair space (kept free: blocked, with
    the chart's wording in the seat's notes). None for anything else."""
    key = " ".join(text.replace("_", " ").replace("–", "-").split()).casefold()
    original = " ".join(text.split())
    if ACCESSIBLE.match(key):
        return {"blocked": True, "notes": f"Accessible seat (marked “{original}” on the department chart); kept free for a student who needs it."}
    if WHEELCHAIR.match(key):
        return {"blocked": True, "notes": f"Wheelchair space (marked “{original}” on the department chart); kept free for a student who needs it."}
    if key in BROKEN_WORDS:
        return {"broken": True}
    if key in LEFT_WORDS:
        return {"left_handed": True}
    if key.replace("-", " ") in {word.replace("-", " ") for word in LEFT_WORDS}:
        return {"left_handed": True}
    return None


def merged_ranges(content):
    """Top-left cell -> (row span, column span) for the first worksheet's merges."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
            return {}
        sheets = ElementTree.fromstring(archive.read("xl/workbook.xml")).findall(f"{MAIN}sheets/{MAIN}sheet")
        if not sheets:
            return {}
        wanted = sheets[0].get(f"{REL}id")
        target = next((rel.get("Target") for rel in ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                       if rel.get("Id") == wanted), None)
        if not target:
            return {}
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
        if path not in names or not path.startswith("xl/worksheets/"):
            return {}
        result = {}
        for node in ElementTree.fromstring(archive.read(path)).iter(f"{MAIN}mergeCell"):
            try:
                min_col, min_row, max_col, max_row = range_boundaries(node.get("ref", ""))
            except ValueError:
                continue
            if None in (min_col, min_row, max_col, max_row):
                continue
            result[(min_row, min_col)] = (max_row - min_row + 1, max_col - min_col + 1)
        return result


def parse_seating_chart(content, filename, *, allow_ambiguous=False):
    """Read one chart; explicit review imports may retain position-qualified duplicates.

    ``allow_ambiguous`` is for administrative intake only. Such imports must
    preserve ``review_required`` and block exam seating until review resolves it.
    """
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError("Choose a nonempty seating chart workbook of 10 MB or less.")
    if Path(filename).suffix.lower() != ".xlsx":
        raise ValueError("Choose an Excel (.xlsx) seating chart. Save other formats as .xlsx first.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 50 * 1024 * 1024:
                raise ValueError("This workbook is too large for a seating chart.")
        merges = merged_ranges(content)
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            if not workbook.worksheets:
                raise ValueError("The workbook has no worksheet with a seating chart.")
            sheet = workbook.worksheets[0]
            # Bounds apply even when the file declares incorrect worksheet dimensions.
            sheet.reset_dimensions()
            cells = []
            for row_number, row in enumerate(sheet.iter_rows(), 1):
                if row_number > MAX_SCANNED_ROWS:
                    raise ValueError(f"The worksheet has more than {MAX_SCANNED_ROWS:,} rows. Copy the chart into a new workbook.")
                for column_number, cell in enumerate(row, 1):
                    if column_number > MAX_SCANNED_COLUMNS:
                        raise ValueError("The worksheet has too many columns. Copy the chart into a new workbook.")
                    if cell.data_type == "f":
                        raise ValueError(f"Cell {cell_name(row_number, column_number)} contains a formula. Paste values before uploading.")
                    text = " ".join(text_value(cell.value).split())
                    if text:
                        cells.append((row_number, column_number, text))
                        if len(cells) > MAX_FILLED_CELLS:
                            raise ValueError(f"The worksheet has more than {MAX_FILLED_CELLS:,} filled cells. A seating chart holds at most {MAX_SEATS:,} seats.")
            return build_chart(sheet.title, cells, merges, allow_ambiguous=allow_ambiguous)
        finally:
            workbook.close()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("The seating chart could not be read. Upload a fresh .xlsx workbook.") from error


def build_chart(sheet, cells, merges=None, *, allow_ambiguous=False):
    merges = merges or {}
    if not cells:
        raise ValueError("The seating chart has no seats. Put one seat number in each cell where a seat is.")
    if "\x00" in sheet or any("\x00" in text for _, _, text in cells):
        raise ValueError("The worksheet contains an invalid character.")
    top, bottom = min(row for row, _, _ in cells), max(row for row, _, _ in cells)
    left, right = min(column for _, column, _ in cells), max(column for _, column, _ in cells)
    # ROOMS: merged cells occupy the chart too. Check their extents before constructing row bands or ranges;
    # an otherwise tiny worksheet can declare a merge spanning a million rows.
    merged_bottom = max(row + merges.get((row, column), (1, 1))[0] - 1 for row, column, _ in cells)
    merged_right = max(column + merges.get((row, column), (1, 1))[1] - 1 for row, column, _ in cells)
    # Only the span between the first and last filled row/column counts; blank
    # rows above, below, or beside the chart are ignored.
    if merged_bottom - top + 1 > MAX_GRID_ROWS:
        raise ValueError(f"The chart spans {merged_bottom - top + 1} rows from {cell_name(top, left)} to {cell_name(merged_bottom, merged_right)}; "
                         f"a seating chart may span at most {MAX_GRID_ROWS} rows. Remove stray text below or above the chart.")
    if merged_right - left + 1 > MAX_GRID_COLUMNS:
        raise ValueError(f"The chart spans {merged_right - left + 1} columns from {cell_name(top, left)} to {cell_name(merged_bottom, merged_right)}; "
                         f"a seating chart may span at most {MAX_GRID_COLUMNS} columns. Remove stray text beside the chart.")

    def span(row, column):
        return merges.get((row, column), (1, 1))

    lookup = {(row, column): text for row, column, text in cells}
    cells_by_row = {}
    for row, column, text in cells:
        cells_by_row.setdefault(row, []).append((column, text))
    # 1. The row-letter column: the leftmost column holding at least two short letter labels.
    letters_by_column = {}
    for row, column, text in cells:
        if LETTERS.match(text):
            letters_by_column.setdefault(column, []).append((row, text))
    label_column = next((column for column in sorted(letters_by_column) if len(letters_by_column[column]) >= 2), None)
    bands = []
    if label_column is not None:
        for row, text in letters_by_column[label_column]:
            rows, _ = span(row, label_column)
            bands.append({"top": row, "bottom": row + rows - 1, "label": text, "row": row, "column": label_column})

    # Tapered rooms move their row letters inward. Retain the established label
    # column, then find a leading single-letter marker on the remaining rows.
    leading = []
    for row, row_cells in cells_by_row.items():
        meaningful = [(column, text) for column, text in row_cells
                      if not CHART_NOTE.match(text) and not LANDMARK.match(text)]
        if meaningful and re.fullmatch(r"[A-Za-z]", meaningful[0][1]):
            column, text = meaningful[0]
            height, _ = span(row, column)
            has_seats = any(NUMBERED.match(value) or TABLE_COUNT.search(value)
                            for other_row in range(row, row + height)
                            for other_column, value in cells_by_row.get(other_row, []) if other_column > column)
            if has_seats:
                leading.append((row, column, text))
    if bands or len({text.casefold() for _, _, text in leading}) >= 2:
        for row, column, text in leading:
            if not any(band["top"] <= row <= band["bottom"] for band in bands):
                height, _ = span(row, column)
                bands.append({"top": row, "bottom": row + height - 1, "label": text,
                              "row": row, "column": column})
    bands.sort(key=lambda band: (band["top"], band["column"]))

    # Some rooms label the two banks independently at their outer edges.
    # Require one shared empty column separating two individually unique
    # number sequences; do not choose a split when the source is ambiguous.
    split_rows = []
    for band in bands:
        row_cells = cells_by_row.get(band["top"], [])
        if not row_cells:
            continue
        last_col, last_text = row_cells[-1]
        if re.fullmatch(r"[A-Za-z]", last_text) and last_text.casefold() != band["label"].casefold():
            numbered_cells = [(column, int(match[1])) for column, value in row_cells
                              if (match := NUMBERED.match(value))]
            candidates = set()
            for column in range(band["column"] + 1, last_col):
                left_numbers = [number for col, number in numbered_cells if col < column]
                right_numbers = [number for col, number in numbered_cells if col > column]
                if ((band["top"], column) not in lookup and left_numbers and right_numbers
                        and len(left_numbers) == len(set(left_numbers))
                        and len(right_numbers) == len(set(right_numbers))
                        and set(left_numbers) & set(right_numbers)):
                    candidates.add(column)
            if candidates:
                split_rows.append((band, last_col, last_text, candidates))
    if split_rows:
        common = set.intersection(*(item[3] for item in split_rows))
        if len(common) == 1:
            split = common.pop()
            for band, column, text, _ in split_rows:
                band["right"] = split - 1
                bands.append({"top": band["top"], "bottom": band["bottom"], "label": text,
                              "row": band["row"], "column": column, "left": split + 1})

    def band_of(row, column):
        return next((band for band in bands if band["top"] <= row <= band["bottom"]
                     and band.get("left", left) <= column <= band.get("right", right)), None)

    named_tables = []
    for row, column, text in cells:
        if NAMED_TABLE.match(text):
            height, width = span(row, column)
            numbered_cells = [(other_row, other_column) for other_row, other_column, value in cells
                              if row - 1 <= other_row <= row + height
                              and column <= other_column < column + width and NUMBERED.match(value)]
            if numbered_cells:
                named_tables.append({"text": text, "cell": (row, column), "seats": set(numbered_cells)})

    # 2. Classify every other filled cell.
    seats, landmarks, annotations, warnings, review_required = [], [], [], [], []
    tables_per_band = {}
    consumed = set()
    for row, column, text in cells:
        if (row, column) in consumed:
            continue
        rows, columns = span(row, column)
        band = band_of(row, column)
        prefix = band["label"] if band else ""
        place = {"row": row, "column": column, "row_span": rows, "column_span": columns, "row_label": prefix}
        if band and (row, column) == (band["row"], band["column"]) and text == band["label"]:
            landmarks.append({**place, "text": text, "kind": "row"})
            continue
        if band and LETTERS.match(text) and text.casefold() == band["label"].casefold():
            continue  # Row letters may repeat at every aisle, including B/L.
        if CHART_NOTE.match(text):
            warnings.append(f"Source note at {cell_name(row, column)}: {text}")
            extra = re.search(r"\b(\d+)\s+extra\s+chairs?\s+in\s+(?:the\s+)?back\b", text, re.IGNORECASE)
            if extra:
                review_required.append(f"Source also lists {extra[1]} extra chairs in back without numbered seat locations.")
            landmarks.append({**place, "text": text[:80], "kind": "caption"})
            continue
        named = next((item for item in named_tables if item["cell"] == (row, column)), None)
        if named:
            landmarks.append({**place, "text": text, "kind": "caption"})
            continue
        loose = LOOSE_DESK.match(text)
        if loose:
            for index in range(1, int(loose[1]) + 1):
                label = f"{prefix} Loose {'left ' if loose[2] else ''}desk {index}".strip()
                seats.append({**place, "label": label, "display": f"Loose desk {index}", "number": None,
                              "group": "", "group_size": None, "seat_index": None, "broken": False,
                              "left_handed": bool(loose[2]), "cell": cell_name(row, column)})
            continue
        group = GROUP.match(text)
        table = TABLE.match(text)
        if table and not TABLE_COUNT.search(text):
            neighbour = lookup.get((row, column + columns))
            beside = GROUP.match(neighbour) if neighbour else None
            if beside:
                consumed.add((row, column + columns))
                place["column_span"] = columns + span(row, column + columns)[1]
                group, text = beside, f"{text} ({beside[1]} {beside[2]})"
            else:
                warnings.append(f"Cell {cell_name(row, column)} says “{text}” without a seat count, so it was kept as a caption.")
                landmarks.append({**place, "text": text, "kind": "caption"})
                continue
        count = TABLE_COUNT.search(text) if table else group
        if count:
            size = int(count[1])
            if not 1 <= size <= 50:
                raise ValueError(f"Cell {cell_name(row, column)} describes a table with {size} seats; use between 1 and 50.")
            key = band["label"] if band else ""
            tables_per_band[key] = tables_per_band.get(key, 0) + 1
            group_label = f"{prefix} Table {tables_per_band[key]}".strip()
            for index in range(1, size + 1):
                seats.append({**place, "label": f"{group_label} · {index}", "display": str(index), "number": None,
                              "group": group_label, "group_size": size, "seat_index": index, "broken": False, "left_handed": False,
                              "cell": cell_name(row, column)})
            continue
        numbered = NUMBERED.match(text)
        if numbered:
            number = int(numbered[1])
            named = [item for item in named_tables if (row, column) in item["seats"]]
            if len(named) > 1:
                raise ValueError(f"Cell {cell_name(row, column)} could belong to more than one table. Separate the table captions.")
            table_context = named[0] if named else None
            if table_context:
                prefix = table_context["text"] + " · "
            flags = annotation_flags(numbered[2]) if numbered[2] else {}
            if flags is None:
                warning = f"Cell {cell_name(row, column)} note “{numbered[2].strip()}” was not understood; seat {prefix}{number} was imported without it."
                warnings.append(warning)
                review_required.append(warning)
                flags = {}
            seats.append({**place, "label": f"{prefix}{number}", "display": str(number), "number": number,
                          "group": table_context["text"] if table_context else "",
                          "group_size": len(table_context["seats"]) if table_context else None,
                          "seat_index": number if table_context else None, "broken": bool(flags.get("broken")),
                          "left_handed": bool(flags.get("left_handed")), "blocked": bool(flags.get("blocked")),
                          "notes": flags.get("notes", ""), "cell": cell_name(row, column)})
            continue
        flags = annotation_flags(text)
        if flags is not None:
            annotations.append({"row": row, "column": column, "flags": flags, "band": band, "cell": cell_name(row, column)})
            continue
        if LANDMARK.match(text) or LANDMARK_ANYWHERE.search(text):
            landmarks.append({**place, "text": text[:80], "kind": "caption"})
            continue
        if len(text) > MAX_LABEL:
            raise ValueError(f"Cell {cell_name(row, column)} has a seat label longer than {MAX_LABEL} characters.")
        seats.append({**place, "label": f"{prefix} {text}".strip(), "display": text, "number": None, "group": "",
                      "group_size": None, "seat_index": None, "broken": False, "left_handed": False, "cell": cell_name(row, column)})
    planning_only = any(UNCOUNTED_MOVABLE.match(text) for _, _, text in cells)
    if not seats and planning_only:
        warnings.append("This movable-table chart specifies no seat positions or counts; it has no assignable seats.")
    if not seats and not planning_only:
        raise ValueError("The seating chart has no seats, only captions such as “Front”. Add seat numbers.")
    if len(seats) > MAX_SEATS:
        raise ValueError(f"A seating chart may contain at most {MAX_SEATS} seats.")

    # 3. Bare annotations mark the nearest seat in their column within the lettered row.
    for note in annotations:
        candidates = [seat for seat in seats
                      if seat["column"] <= note["column"] < seat["column"] + seat["column_span"]
                      and (seat["row_label"] == note["band"]["label"] if note["band"] else abs(seat["row"] - note["row"]) <= 2)]
        if not candidates:
            kind = "broken" if note["flags"].get("broken") else "left-handed" if note["flags"].get("left_handed") else "accessible"
            warnings.append(f"Cell {note['cell']} says “{kind}” but no seat is in that column of its row.")
            continue
        nearest = min(candidates, key=lambda seat: (abs(seat["row"] - note["row"]), seat["seat_index"] or 0))
        nearest.update(note["flags"])

    # 4. Labels must be unique; text seats repeated in a row get a running number.
    if allow_ambiguous:
        numbered_labels = {}
        for seat in seats:
            if seat["number"] is not None:
                numbered_labels.setdefault(seat["label"].casefold(), []).append(seat)
        for duplicates in numbered_labels.values():
            if len(duplicates) < 2:
                continue
            references = ", ".join(seat["cell"] for seat in duplicates)
            warning = (f"Source cells {references} repeat seat “{duplicates[0]['label']}”. "
                       "Their labels include source cell positions pending review; no corrected seat numbers were inferred.")
            warnings.append(warning)
            review_required.append(warning)
            for seat in duplicates:
                seat["label"] = f"{seat['label']} · {seat['cell']}"
    seen = {}
    for seat in seats:
        key = seat["label"].casefold()
        if key in seen and seat["number"] is None and not seat["group"]:
            counter = 2
            while f"{key} {counter}" in seen:
                counter += 1
            seat["label"] = f"{seat['label']} {counter}"
            key = seat["label"].casefold()
        if key in seen:
            raise ValueError(f"Cells {seen[key]} and {seat['cell']} both describe seat “{seat['label']}”. Each seat needs its own number in its row.")
        seen[key] = seat["cell"]

    # 5. Drop annotation-only rows inside lettered bands; keep aisles elsewhere.
    seat_rows = {seat["row"] for seat in seats}
    banded = {row for band in bands for row in range(band["top"], band["bottom"] + 1)}
    kept = {row for row in range(top, bottom + 1) if row not in banded or row in seat_rows}
    for item in landmarks:
        if not any(row in kept for row in range(item["row"], item["row"] + item["row_span"])):
            kept.add(item["row"])
    order = {row: index for index, row in enumerate(sorted(kept), 1)}
    columns_used = [seat["column"] for seat in seats] + [item["column"] for item in landmarks]
    spans_used = [seat["column"] + seat["column_span"] - 1 for seat in seats] + [item["column"] + item["column_span"] - 1 for item in landmarks]
    left, right = min(columns_used), max(spans_used)

    def place(item):
        inside = [row for row in range(item["row"], item["row"] + item["row_span"]) if row in kept]
        item["row"], item["row_span"] = order[inside[0]], len(inside)
        item["column"] = item["column"] - left + 1
        item["column_span"] = min(item["column_span"], right - left + 1 - item["column"] + 1)

    for item in seats + landmarks:
        place(item)
    for seat in seats:
        seat.pop("cell")
    seats.sort(key=lambda seat: (seat["row"], seat["column"], seat["seat_index"] or 0))
    landmarks.sort(key=lambda item: (item["row"], item["column"]))
    return {
        "sheet": " ".join(sheet.split())[:80],
        "rows": len(kept),
        "columns": right - left + 1,
        "seats": seats,
        "landmarks": landmarks,
        "warnings": warnings[:20],
        "review_required": review_required,
    }
