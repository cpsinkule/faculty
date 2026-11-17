"""Score imports (README.md, "Imports"): a Gradescope course or assignment export, or a generic sheet keyed by UIN, read
into columns of values (`parse`), then planned against one term's gradebook (`plan`). Nothing here touches the catalog:
the service supplies the roster, the setup and the latest cells, and writes only the plan's `add` and `update` changes.

Invariants:
- Students match by the nine-digit UIN EXACTLY. An SID that is not nine digits is never padded, a UIN held by two
  memberships of the term or written on two rows of the file is never guessed: those rows are listed, not imported.
- A blank changes nothing (unless the instructor ticks "blank as missing"); an unreadable value is listed, never written.
- What a value does to a cell is `common.change_action`: hand-entered values are kept unless the instructor ticks Replace.
- The plan's JSON never carries a UIN or an SID: unmatched rows show the file's line, the file's name and a reason, and a
  run of eight or more digits in an unreadable value is masked.
- Scores with more than four decimals (Gradescope writes 8.333333333) are rounded half up to four, the finest a cell
  holds, and a warning says how many were. Rounding is shown, never silent, and cannot move a score by a ten-thousandth.
- A Gradescope assignment total whose Status is "Ungraded" is not a score yet: it is listed as unreadable, never written
  and never made missing. "Missing" with no total is a blank.
- Generic sheets: identity columns (Section, Email, Canvas's own ID ...) are never score columns even when numeric; a
  column holding any other text is left out with a warning naming it; a Points Possible cell that is text (Canvas writes
  "(read only)" on its computed totals) leaves that column out too; a row with neither an ID nor a name (Canvas's posting
  policy row) is not a student. Canvas's "SIS User ID" is accepted as the ID column after the usual headings.
- Suggestions never make two file columns go to one gradebook column, never propose a title that exists (archived
  included) and propose no new column for a file column without any value.
"""
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from roster_import import read_table

from . import common
from .setup import MAX_ITEMS, OWNED

RULE = "gradebook-import-v1"
MAX_COLUMNS = 1024  # A Gradescope course export has four columns per assignment; the roster reader's 64 is too few.
MAX_OPTIONS = 64 * 1024
MAX_RAW = 40
MAX_TITLE = 120  # An item title's limit in the setup.
MAX_REPLACE = 20000
NINE = re.compile(r"[0-9]{9}")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+")  # No sign, exponent or thousands separator.
LONG_DIGITS = re.compile(r"[0-9]{8,}")
EXCUSED, MISSING = {"ex", "excused"}, {"mi", "missing"}
# Generic sheets: the ID column, by priority. "sis user id" is Canvas's column for the university ID.
ID_HEADINGS = ("uin", "student uin", "sid", "student id", "student_id", "id number", "sis user id")
# Identity columns are never score columns, even when every value is a number (a section number, Canvas's own ID).
IDENTITY = {"id", "sis login id", "integration id", "root account", "section", "sections", "section number", "email",
            "email address", "name", "first name", "last name", "student", "student name", "username", "login id", "netid",
            *ID_HEADINGS}
GRADESCOPE_EXTRA = (" - max points", " - submission time", " - lateness (h:m:s)")
ACTIONS = ("add", "update", "same", "kept")
RECOGNIZED = ("This file is not a score export Faculty recognizes. Upload Gradescope's course Download Grades CSV, a "
              "Gradescope assignment scores export (SID, Total Score, Max Points), or a sheet with a UIN, SID or Student ID "
              "column and one column of points per item.")


def fold(text):
    return " ".join(str(text).split()).casefold()


def collapse(text):
    return " ".join(str(text).split())


def _value(raw, tally):
    """One cell: blank, scored (exact points text), excused, missing or unreadable. `tally["rounded"]` counts roundings."""
    text = raw.strip()
    if not text:
        return {"kind": "blank", "points": None, "raw": ""}
    shown = text[:MAX_RAW]
    token = text.casefold()
    if token in EXCUSED:
        return {"kind": "excused", "points": None, "raw": shown}
    if token in MISSING:
        return {"kind": "missing", "points": None, "raw": shown}
    if NUMBER.fullmatch(text):
        number = Decimal(text)
        if number < common.MAX_POINTS + 1:  # Bounded before quantize, which refuses more digits than its precision.
            exact = number.quantize(common.PLACES, ROUND_HALF_UP)
            if exact <= common.MAX_POINTS:
                tally["rounded"] += exact != number
                return {"kind": "scored", "points": common.points_text(exact), "raw": shown}
    return {"kind": "unreadable", "points": None, "raw": shown}


def _number(raw, tally):
    value = _value(raw, tally)
    return value["points"] if value["kind"] == "scored" else None


def _title_from(filename):
    stem = Path(filename).stem
    for suffix in ("_scores", " scores"):
        if stem.casefold().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return collapse(stem.replace("_", " "))[:MAX_TITLE] or "Gradescope assignment"


def parse(content, filename):
    """Read a score file into {"format", "filename", "sha256", "columns", "rows", "warnings"} (see the module docstring).

    `rows[].sid` is the file's ID exactly as written (stripped); it is for matching only and must never reach JSON."""
    table = read_table(content, filename, max_columns=MAX_COLUMNS, noun="score sheet")
    start = next((index for index, row in enumerate(table) if any(cell.strip() for cell in row)), None)
    if start is None:
        raise ValueError("The file has no column headings or student rows.")
    headers = table[start]
    folded = [fold(header) for header in headers]
    present = set(folded)
    body = []
    for line, row in enumerate(table[start + 1:], start + 2):
        if not any(cell.strip() for cell in row):
            continue
        if any(cell.strip() for cell in row[len(headers):]):
            raise ValueError(f"Row {line} has more cells than the file has column headings.")
        body.append((line, row + [""] * (len(headers) - len(row))))
    tally, warnings, status = {"rounded": 0, "ungraded": 0}, [], None

    def only(heading):
        indexes = [index for index, name in enumerate(folded) if name == heading]
        if len(indexes) > 1:
            raise ValueError(f"The file has more than one “{headers[indexes[0]].strip()}” column. Keep one and upload it again.")
        return indexes[0] if indexes else None

    def name_of(row):
        if "first name" in present and "last name" in present:
            return collapse(f"{row[folded.index('first name')]} {row[folded.index('last name')]}")
        for heading in ("name", "student name", "student"):
            if heading in present:
                return collapse(row[folded.index(heading)])
        return ""

    def maximum(column, index, rows):
        """The first readable maximum of a Gradescope max-points column; a warning when rows disagree. A maximum is
        repeated on every row, so its rounding is not counted among the rounded scores."""
        found = [points for points in (_number(row[index], {"rounded": 0}) for _, row in rows) if points is not None]
        if len({Decimal(points) for points in found}) > 1:
            warnings.append(f"The file gives “{column}” different maximum points on different rows; {found[0]} is used.")
        return found[0] if found else None

    if "sid" in present and any(name and f"{name} - max points" in present for name in folded):
        form, id_index = "gradescope_course", only("sid")
        maxima, seen, specs = {}, {}, []
        for index, name in enumerate(folded):
            if name.endswith(" - max points"):
                maxima.setdefault(name, []).append(index)
        for index, name in enumerate(folded):
            if not name or name.endswith(GRADESCOPE_EXTRA) or f"{name} - max points" not in maxima:
                continue
            # Gradescope allows two assignments with one title: the k-th such column pairs with the k-th maximum.
            occurrence = seen[name] = seen.get(name, -1) + 1
            partner = maxima[f"{name} - max points"]
            specs.append((index, headers[index].strip(), partner[occurrence] if occurrence < len(partner) else None))
        students = [(line, row) for line, row in body if row[id_index].strip() or name_of(row)]
        columns = [{"key": f"c{index}", "title": title, "max_points": maximum(title, partner, students) if partner is not None else None}
                   for index, title, partner in specs]
        indexes = [index for index, _, _ in specs]
    elif {"sid", "total score", "max points"} <= present:
        form, id_index = "gradescope_assignment", only("sid")
        total, status = only("total score"), only("status")
        students = [(line, row) for line, row in body if row[id_index].strip() or name_of(row)]
        title = _title_from(filename)
        columns = [{"key": f"c{total}", "title": title, "max_points": maximum(title, only("max points"), students)}]
        indexes = [total]
    else:
        heading = next((heading for heading in ID_HEADINGS if heading in present), None)
        if heading is None:
            raise ValueError(RECOGNIZED)
        form, id_index = "generic", only(heading)
        others = [name for name in ID_HEADINGS if name in present and name != heading]
        if others:
            warnings.append(f"Students are matched by the “{headers[id_index].strip()}” column; "
                            + ", ".join(f"“{headers[folded.index(name)].strip()}”" for name in others)
                            + (" is" if len(others) == 1 else " are") + " not used.")
        possible, students, skipped = None, [], 0
        for line, row in body:
            first = next((cell for cell in row if cell.strip()), "")
            if possible is None and "points possible" in (fold(first), fold(row[id_index]), fold(name_of(row))):
                possible = row
            elif fold(first) == "points possible":
                continue
            elif row[id_index].strip() or name_of(row):
                students.append((line, row))
            else:
                skipped += 1  # Canvas writes its posting policy on a row with neither an ID nor a name.
        if skipped:
            warnings.append(f"{skipped} row{'s' if skipped != 1 else ''} with neither an ID nor a name "
                            f"{'were' if skipped != 1 else 'was'} not read as students.")
        columns, indexes, text_columns, read_only = [], [], [], []
        for index, name in enumerate(folded):
            if index == id_index or not name or name in IDENTITY:
                continue
            title = headers[index].strip()
            if possible is not None and possible[index].strip() and _number(possible[index], {"rounded": 0}) is None:
                read_only.append(title)  # Canvas writes "(read only)" there for its computed totals.
                continue
            probe = {"rounded": 0}
            if any(_value(row[index], probe)["kind"] == "unreadable" and not NUMBER.fullmatch(row[index].strip())
                   for _, row in students):
                text_columns.append(title)
                continue
            columns.append({"key": f"c{index}", "title": title,
                            "max_points": _number(possible[index], {"rounded": 0}) if possible is not None else None})
            indexes.append(index)
        if read_only:
            warnings.append("Not imported, since the Points Possible row gives them no points: "
                            + ", ".join(f"“{title}”" for title in read_only) + ".")
        if text_columns:
            warnings.append("Not imported, since they hold text other than points, EX or MI: "
                            + ", ".join(f"“{title}”" for title in text_columns) + ".")
        if not columns:
            raise ValueError("No column of this file holds only points, EX or MI, so there is nothing to import.")
    if not students:
        raise ValueError("The file has no student rows.")
    rows = []
    for line, row in students:
        if status is not None and fold(row[status]) == "ungraded":
            # A total Gradescope has not finished grading is not a score; it is listed, never written.
            raws = {column["key"]: row[index].strip() for column, index in zip(columns, indexes)}
            values = {key: {"kind": "unreadable", "points": None, "raw": (f"Ungraded ({raw})" if raw else "Ungraded")[:MAX_RAW]}
                      for key, raw in raws.items()}
            tally["ungraded"] += 1
        else:
            values = {column["key"]: _value(row[index], tally) for column, index in zip(columns, indexes)}
        rows.append({"line": line, "sid": row[id_index].strip(), "name": name_of(row), "values": values})
    if tally["ungraded"]:
        warnings.append(f"{tally['ungraded']} submission{'s are' if tally['ungraded'] != 1 else ' is'} not graded yet on "
                        "Gradescope (Status Ungraded); those totals are not imported.")
    if tally["rounded"]:
        many = tally["rounded"] != 1
        warnings.append(f"{tally['rounded']} score{'s' if many else ''} had more than four decimal places and "
                        f"{'were' if many else 'was'} rounded half up to four.")
    return {"format": form, "filename": Path(filename).name[:200], "sha256": common.digest(content), "columns": columns,
            "rows": rows, "warnings": warnings}


def _mapping(entry):
    if not isinstance(entry, dict):
        raise ValueError("Each file column's choice must be an object with an action.")
    action = entry.get("action")
    if action == "skip":
        return {"action": "skip"}
    if action == "item":
        item_id = entry.get("item_id")
        if not isinstance(item_id, str) or not 0 < len(item_id) <= 100:
            raise ValueError("Choose the gradebook column each file column goes to.")
        return {"action": "item", "item_id": item_id}
    if action != "new":
        raise ValueError("Each file column goes to a gradebook column, a new column, or is skipped.")
    title = collapse(entry.get("title")) if isinstance(entry.get("title"), str) else ""
    if not 0 < len(title) <= MAX_TITLE:
        raise ValueError(f"A new column needs a title of 1 to {MAX_TITLE} characters.")
    category = entry.get("category_id")
    if category is not None and (not isinstance(category, str) or not 0 < len(category) <= 100):
        raise ValueError(f"Choose a category for the new column “{title}”, or none.")
    points = common.parse_points(entry.get("points"), f"The new column “{title}”'s points", allow_blank=True)
    if points is not None and Decimal(points) == 0:
        raise ValueError(f"The new column “{title}” needs more than 0 points.")
    return {"action": "new", "title": title, "category_id": category, "points": points}


def normalize_options(value):
    """{"mapping": {column key: choice}|None, "replace": sorted row keys, "blank_as_missing": bool} from None, a dict or the
    JSON text of a multipart field. Idempotent: normalizing a normalized value returns it unchanged, so a plan's `options`
    can be sent back with the apply. A null mapping asks for the suggested one."""
    if value is None or (isinstance(value, str) and not value.strip()):
        value = {}
    elif isinstance(value, str):
        if len(value.encode()) > MAX_OPTIONS:
            raise ValueError("The import options are too large.")
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("The import options could not be read.") from None
        value = {} if value is None else value
    if not isinstance(value, dict):
        raise ValueError("The import options could not be read.")
    unknown = sorted(map(str, set(value) - {"mapping", "replace", "blank_as_missing", "section_id"}))
    if unknown:
        raise ValueError(f"Unknown import option: {', '.join(unknown)[:100]}.")
    mapping = value.get("mapping")
    if mapping is not None:
        if not isinstance(mapping, dict) or len(mapping) > MAX_COLUMNS or any(not isinstance(key, str) or not 0 < len(key) <= 64 for key in mapping):
            raise ValueError("The import options name file columns that could not be read.")
        mapping = {key: _mapping(mapping[key]) for key in sorted(mapping)}
    replace = value.get("replace")
    replace = [] if replace is None else replace
    if not isinstance(replace, list) or len(replace) > MAX_REPLACE:
        raise ValueError("Replace must list students of this term's roster.")
    for row in replace:
        common.split_row(row)
    blank = value.get("blank_as_missing")
    blank = False if blank is None else blank
    if not isinstance(blank, bool):
        raise ValueError("Whether blanks are recorded as missing must be true or false.")
    section = value.get("section_id") or ""
    if not isinstance(section, str) or len(section) > 64:
        raise ValueError("Choose a section of this term.")
    # A section tab's import names its section; a whole-term import keeps the options (and fingerprints) it always had.
    return {"mapping": mapping, "replace": sorted(set(replace)), "blank_as_missing": blank, **({"section_id": section} if section else {})}


def _suggest(columns, rows, items, warnings):
    """The instructor's starting point: a file column goes to the one non-archived column of the same title, else to a
    new column worth the file's maximum; never two file columns to one column, never a new title that exists."""
    live, every = {}, {fold(item["title"]) for item in items.values()}
    for item in items.values():
        if not item.get("archived") and item.get("canvas") not in OWNED:  # Canvas's scores come from Canvas only
            live.setdefault(fold(item["title"]), []).append(item)
    used, new_titles, mapping = set(), set(), {}
    for column in columns:
        title, name = column["title"], fold(column["title"])
        matches = live.get(name, [])
        has_values = any(row["values"][column["key"]]["kind"] != "blank" for row in rows)
        if len(matches) == 1 and matches[0]["id"] not in used:
            used.add(matches[0]["id"])
            mapping[column["key"]] = {"action": "item", "item_id": matches[0]["id"]}
            continue
        mapping[column["key"]] = {"action": "skip"}
        if len(matches) > 1:
            warnings.append(f"Several gradebook columns are titled “{matches[0]['title']}”: choose one for the file's “{title}”.")
        elif matches:
            warnings.append(f"The file has two columns titled “{title}”: the second is skipped unless you choose a column for it.")
        elif not has_values:
            continue  # Nothing to import: no new column is proposed for an empty file column.
        elif name in every or collapse(title)[:MAX_TITLE].casefold() in every:
            warnings.append(f"“{title}” matches an archived gradebook column: restore it or choose a column for it.")
        elif collapse(title)[:MAX_TITLE].casefold() in new_titles:
            warnings.append(f"The file has two columns titled “{title}”: the second is skipped unless you choose a column for it.")
        else:
            new_title = collapse(title)[:MAX_TITLE]
            new_titles.add(new_title.casefold())
            points = column["max_points"] if column["max_points"] is not None and Decimal(column["max_points"]) > 0 else None
            mapping[column["key"]] = {"action": "new", "title": new_title, "category_id": None, "points": points}
    return mapping


def _check(mapping, columns, items, categories):
    keys = {column["key"]: column for column in columns}
    if set(mapping) - set(keys):
        raise ValueError("The import options name a column this file does not have. Preview the import again.")
    for column in columns:
        if column["key"] not in mapping:
            raise ValueError(f"Choose what to do with the file's “{column['title']}”.")
    taken, new_titles = {}, {}
    existing = {fold(item["title"]): item for item in items.values()}
    for column in columns:
        choice = mapping[column["key"]]
        if choice["action"] == "item":
            item = items.get(choice["item_id"])
            if item is None:
                raise ValueError(f"The gradebook column chosen for “{column['title']}” no longer exists. Preview the import again.")
            if item.get("archived"):
                raise ValueError(f"“{item['title']}” is archived: restore it in the setup or choose another column for “{column['title']}”.")
            if item.get("canvas") in OWNED:
                # SYNC WITH CANVAS (requirement, 2026-10-06): a column Canvas grades takes Canvas's scores only.
                raise ValueError(f"“{item['title']}” is graded in Canvas: choose another column for “{column['title']}”.")
            if item["id"] in taken:
                raise ValueError(f"The file's “{taken[item['id']]}” and “{column['title']}” both go to “{item['title']}”: choose one.")
            taken[item["id"]] = column["title"]
        elif choice["action"] == "new":
            name = choice["title"].casefold()
            if name in existing:
                raise ValueError(f"The gradebook already has a column titled “{existing[name]['title']}”: choose it or another title.")
            if name in new_titles:
                raise ValueError(f"Two new columns would both be titled “{choice['title']}”: give them different titles.")
            new_titles[name] = column["title"]
            if choice["category_id"] is not None and choice["category_id"] not in categories:
                raise ValueError(f"The category chosen for the new column “{choice['title']}” no longer exists.")


def _unmatched_reason(sid, lines, memberships):
    if not sid:
        return "The row has no UIN."
    if len(lines) > 1:
        others = ", ".join(str(line) for line in lines)
        return f"The same ID is on lines {others} of the file: fix the file so each student appears once."
    if not NINE.fullmatch(sid):
        return "The ID is not a nine-digit UIN on this roster; nothing is padded or guessed."
    if not memberships:
        return "No student of this term's sections has this UIN."
    if len(memberships) > 1:
        sections = ", ".join(str(student["section"]) for student in sorted(memberships, key=lambda s: str(s["section"])))
        return f"This UIN is in more than one section of this term ({sections}): enter this student's score by hand."
    return None


def plan(parsed, *, options, students, setup, revision, cells):
    """The ImportPlan (README.md, "API") of a parsed file against a term's gradebook. Pure: `students` is every membership
    of the term ({"row", "section_id", "section", "name", "uin"}), `setup` the current normalized setup, `revision` its
    number or None, `cells` {item_id: {row: latest revision}}. Changes of every action are listed for the preview; only
    `add` and `update` are writes, and only when `ready` (no new column is missing its points, and the new columns fit the
    setup's column limit)."""
    options = normalize_options(options)
    items = {item["id"]: item for item in setup.get("items", [])}
    categories = {category["id"] for category in setup.get("categories", [])}
    columns, rows = parsed["columns"], parsed["rows"]
    warnings = list(parsed["warnings"])
    if options["mapping"] is None:
        mapping = _suggest(columns, rows, items, warnings)
    else:
        mapping = {column["key"]: options["mapping"][column["key"]] for column in columns if column["key"] in options["mapping"]}
        _check(options["mapping"], columns, items, categories)
    ready = True
    for column in columns:
        choice = mapping[column["key"]]
        if choice["action"] == "new" and choice["points"] is None:
            ready = False
            warnings.append(f"Give the new column “{choice['title']}” its points: the file does not say what “{column['title']}” is worth.")
        if choice["action"] == "item" and column["max_points"] is not None:
            item = items[choice["item_id"]]
            if Decimal(column["max_points"]) != Decimal(item["points"]):
                warnings.append(f"The file says “{column['title']}” is worth {column['max_points']} points; "
                                f"the gradebook column “{item['title']}” is worth {common.points_text(item['points'])} points.")
    added = sum(1 for column in columns if mapping[column["key"]]["action"] == "new")
    if added and len(items) + added > MAX_ITEMS:
        # The setup would refuse the new columns at the apply: say so here, where the instructor can still skip some.
        ready = False
        warnings.append(f"The gradebook has {len(items)} columns (archived ones included) and holds at most {MAX_ITEMS}: "
                        f"skip some of the {added} new columns or choose existing columns for them.")

    by_uin, lines = {}, {}
    for student in students:
        if student.get("uin"):
            by_uin.setdefault(student["uin"], []).append(student)
    for row in rows:
        if row["sid"]:
            lines.setdefault(row["sid"], []).append(row["line"])
    replace = set(options["replace"])
    zero = {key: 0 for key in (*ACTIONS, "unreadable", "blank", "not_applicable")}
    column_counts = {column["key"]: dict(zero) for column in columns}
    changes, unmatched, unreadable, matched, other_section = [], [], [], [], 0
    for row in rows:
        memberships = by_uin.get(row["sid"], []) if NINE.fullmatch(row["sid"]) else []
        reason = _unmatched_reason(row["sid"], lines.get(row["sid"], []), memberships)
        if reason:
            unmatched.append({"line": row["line"], "name": row["name"], "reason": reason})
        elif options.get("section_id") and memberships[0]["section_id"] != options["section_id"]:
            other_section += 1  # an import from one section's tab writes only that section (counted, never listed)
        else:
            matched.append((memberships[0], row))
    matched.sort(key=lambda pair: (pair[0]["name"].casefold(), str(pair[0]["section"]), pair[0]["row"]))
    for column in columns:
        choice, counts = mapping[column["key"]], column_counts[column["key"]]
        if choice["action"] == "skip" or (choice["action"] == "new" and choice["points"] is None):
            continue
        item = items.get(choice.get("item_id"))
        for student, row in matched:
            if item is not None and item.get("sections") and student["section_id"] not in item["sections"]:
                counts["not_applicable"] += 1
                continue
            value = row["values"][column["key"]]
            if value["kind"] == "unreadable":
                counts["unreadable"] += 1
                unreadable.append({"column": column["key"], "row": student["row"], "name": student["name"], "line": row["line"],
                                   "raw": LONG_DIGITS.sub("…", value["raw"])})
                continue
            if value["kind"] == "blank" and not options["blank_as_missing"]:
                counts["blank"] += 1
                continue
            status = "missing" if value["kind"] == "blank" else value["kind"]
            proposed = {"status": status, "points": value["points"] if status == "scored" else None}
            current = cells.get(item["id"], {}).get(student["row"]) if item is not None else None
            action = common.change_action(current, proposed, replace_hand=student["row"] in replace)
            counts[action] += 1
            changes.append({"column": column["key"], "row": student["row"], "name": student["name"], "section": student["section"],
                            "action": action, "proposed": proposed, "current": dict(current) if current else None,
                            "item_id": item["id"] if item is not None else None})
    totals = {key: sum(counts[key] for counts in column_counts.values()) for key in zero}
    totals["unmatched"] = len(unmatched)
    totals["other_section"] = other_section
    new_items = [{"column": column["key"], **{key: mapping[column["key"]][key] for key in ("title", "category_id", "points")}}
                 for column in columns if mapping[column["key"]]["action"] == "new"]
    fingerprint = common.digest({
        "rule": RULE, "format": parsed["format"], "sha256": parsed["sha256"], "options": options, "revision": revision,
        "mapping": mapping,
        "changes": sorted([change["column"], change["row"], change["action"], common.encoded(change["proposed"]),
                           common.encoded(change["current"]["id"] if change["current"] else None)] for change in changes)})
    return {"fingerprint": fingerprint, "rule": RULE, "format": parsed["format"], "filename": parsed["filename"],
            "sha256": parsed["sha256"], "revision": revision, "ready": ready, "options": options,
            "columns": [{**column, "mapping": mapping[column["key"]], "counts": column_counts[column["key"]]} for column in columns],
            "new_items": new_items, "changes": changes, "unmatched": unmatched, "unreadable": unreadable, "counts": totals,
            "warnings": warnings}
