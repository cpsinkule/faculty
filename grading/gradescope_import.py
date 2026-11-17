"""Read per-question evaluation CSV metadata, never student evaluation rows.

Gradescope's compact footers predate the nine-column submission header: their
question total is column 5 and rubric items start at column 6. Header items start at column 9, or
column 8 when the course has no sections and Gradescope leaves out Sections. Align
the two explicit item ranges, not their absolute CSV columns.
"""
import csv
import io
import re

from .models import encoded, number


HEADER = ("assignment submission id", "question submission id", "first name", "last name",
          "sid", "email", "sections", "score", "submission time")
# A Gradescope course with no sections exports the same header without Sections.
HEADERS = (HEADER, tuple(name for name in HEADER if name != "sections"))
TRAILER = ("adjustment", "comments", "grader", "tags")
FOOTERS = {"rubric numbers", "point values", "scoring method"}
RUBRIC_NUMBER = re.compile(r"\d+(?:\.\d+)+")


def numeric(value, label, minimum=-10000):
    try:
        return number(float(value), label, minimum, 10000)
    except (ValueError, TypeError):
        raise ValueError(f"Gradescope {label.lower()} is missing or invalid.") from None


def inspect(source):
    """Return exact exported rubric facts, or None for an ordinary CSV.

    Only a recognized footer layout can establish alignment. Unknown, incomplete,
    mixed-question or ambiguous exports fail instead of shifting/dropping items.
    """
    try:
        matrix = list(csv.reader(io.StringIO(source.removeprefix("\ufeff")), strict=True))
    except csv.Error:
        raise ValueError("The rubric CSV is malformed.") from None
    matrix = [row for row in matrix if any(cell.strip() for cell in row)]
    if not matrix:
        return None
    header = matrix[0]
    names = tuple(cell.strip().lower() for cell in header)
    prefix = next((known for known in HEADERS if names[:len(known)] == known), None)
    known_header = prefix is not None
    footers = {}
    for row in matrix[1:]:
        label = row[0].strip().lower()
        if label not in FOOTERS:
            continue
        if label in footers:
            raise ValueError("Gradescope CSV contains duplicate footer rows.")
        footers[label] = row
    if not footers and not known_header:
        return None
    if not {"rubric numbers", "point values"} <= footers.keys():
        raise ValueError("Gradescope CSV needs both Rubric Numbers and Point Values footer rows.")
    identifiers, points = footers["rubric numbers"], footers["point values"]
    positions = [i for i, value in enumerate(identifiers[1:], 1) if value.strip()]
    if not positions or len(positions) > 200 or any(not RUBRIC_NUMBER.fullmatch(identifiers[i].strip()) for i in positions):
        raise ValueError("Gradescope rubric numbers are missing or invalid.")
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError("Gradescope rubric number columns must form one complete range.")
    numbers = [identifiers[i].strip() for i in positions]
    if len(set(numbers)) != len(numbers):
        raise ValueError("Gradescope rubric numbers must be unique.")
    questions = {value.split(".", 1)[0] for value in numbers}
    if len(questions) != 1:
        raise ValueError("Import one question's Gradescope rubric at a time.")
    maximum = None
    if known_header:
        if names[-len(TRAILER):] != TRAILER:
            raise ValueError("The Gradescope evaluation header has an unsupported layout.")
        description_positions = list(range(len(prefix), len(header) - len(TRAILER)))
        if len(description_positions) != len(positions):
            raise ValueError("Gradescope descriptions and rubric numbers have different item counts.")
        if positions[0] == 6:
            total_position, layout = 5, "compact_footer"
        elif positions[0] == len(prefix):
            total_position, layout = prefix.index("score"), "aligned_footer"
        else:
            raise ValueError("Gradescope footer alignment is unsupported; no rubric items were imported.")
        if len(points) <= total_position:
            raise ValueError("Gradescope question total is missing.")
        if points[total_position].strip():
            maximum = numeric(points[total_position], "Question total", .01)
        elif layout == "compact_footer":
            raise ValueError("Gradescope question total is missing.")
        allowed_points = {*positions, total_position}
    else:
        # Older aligned evaluation exports have a shorter identity header.
        description_positions, layout = positions, "aligned_footer"
        allowed_points = set(positions)
        if positions[-1] >= len(header):
            raise ValueError("Gradescope rubric descriptions are missing.")
    if positions[-1] >= len(points) or any(value.strip() for i, value in enumerate(points[1:], 1) if i not in allowed_points):
        raise ValueError("Gradescope point columns do not match the rubric numbers.")
    mode = None
    if "scoring method" in footers:
        values = [value.strip().lower() for value in footers["scoring method"][1:] if value.strip()]
        if len(values) != 1 or values[0] not in ("positive", "negative"):
            raise ValueError("Gradescope Scoring Method must be positive or negative.")
        mode = values[0]
    items = []
    for number_, point_position, description_position in zip(numbers, positions, description_positions):
        description = header[description_position]
        if not description.strip():
            raise ValueError("Gradescope rubric descriptions cannot be blank.")
        items.append({"id": "R" + number_.replace(".", "_"), "description": description,
            "points": numeric(points[point_position], "Item points"),
            "original_source_text": encoded({"rubric_number": number_, "description": description,
                "point_value": points[point_position]})})
    return {"format": "gradescope_evaluations", "layout": layout, "question_number": next(iter(questions)),
            "mode": mode, "max_points": maximum, "items": items}


def summary(source):
    result = inspect(source)
    return {**{key: value for key, value in result.items() if key != "items"}, "item_count": len(result["items"])} if result else None
