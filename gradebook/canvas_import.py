"""Scores imported from Canvas (README.md, "Importing from Canvas"; requirement, 2026-09-27): what each linked Canvas course's
assignments hold, planned against one term's gradebook. Pure: the Canvas service reads Canvas, the gradebook service supplies the
roster, the setup, the latest cells and the remembered matches, and writes only the plan's `add` and `update` changes.

The instructor's decisions (2026-09-27):
- A Canvas assignment is matched to a gradebook column ONCE, then by its Canvas ID: a confirmed match (or "skip") is remembered
  per Canvas course (`gradebook_canvas_links`, latest row counts), so renaming either side changes nothing. Matching is per
  course, since every section links its own Canvas course and each course has its own assignment IDs.
- First suggestion: the one live column of the same title, else of the same `exam_links.canonical` title ("Exam 1" is
  "Test 1"), narrowed by points and then by due date; ambiguity suggests nothing, except a column Jev names (docs/decisions/canvas.md
  "JEV MATCHES", `_jev_matches`). A Canvas assignment with no match defaults
  to a NEW column in the category named after its Canvas assignment group, graded yet or not (requirement, 2026-10-02); the same title and points in several
  courses make ONE new column.
- Columns Faculty grades itself (linked to FRQ, bubble sheets or Exams) are COMPARE ONLY: differences are listed, never written.
- The value is Canvas's `score` (after its late and missing penalties). Excused is excused; Canvas's missing flag is `missing`
  only when the instructor ticks it; ungraded and pending review are not scores. Grades Canvas has not posted to students are
  imported like any other and counted.

Students match through their permanent membership: the Canvas course linked to THEIR section, the student there whose SIS user
ID is their nine-digit UIN exactly. Dropped students are never filled in bulk. The plan's JSON never carries a UIN.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from scheduling.clock import CAMPUS_ZONE

from canvas.web import graded_by_webassign
from exam_links import canonical

from . import common
from .imports import collapse, fold
from .setup import MAX_ITEMS, NAME, OWNED

RULE = "gradebook-canvas-v2"  # v2: others' values kept (KEEPS OTHERS)
KEY = re.compile(r"[0-9]{1,20}:[0-9]{1,20}")
NINE = re.compile(r"[0-9]{9}")
MAX_OPTIONS = 256 * 1024
MAX_REPLACE = 20000
MAX_TITLE = 120
CAMPUS = CAMPUS_ZONE
ACTIONS = ("add", "update", "same", "kept", "differs")
COUNTS = (*ACTIONS, "blank", "waiting", "unreadable", "not_applicable", "not_in_canvas", "unposted", "compared", "mismatch", "faculty_only",
          "clear")
WAITING = {"pending_review"}


def assignment_key(course_id, assignment_id):
    return f"{course_id}:{assignment_id}"


def campus_date(value):
    """Canvas's due time (UTC ISO) as the campus-local date; '' when there is none or it cannot be read."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(CAMPUS).date().isoformat()
    except ValueError:
        return ""


def _points(value):
    """Canvas's points possible or score as exact text, rounded half up to four decimals; None when absent or unusable."""
    if value is None or isinstance(value, bool):
        return None, False
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None, False
    if not number.is_finite() or number < 0 or number > common.MAX_POINTS:
        return None, False
    exact = number.quantize(common.PLACES, ROUND_HALF_UP)
    return common.points_text(exact), exact != number


def linked_to_grading(item):
    return bool(item and (item.get("link") or {}).get("kind") == "grading")


def course_order(course_id):
    """Canvas's numeric course IDs by number; another source's IDs (iClicker's) by text, after them."""
    return (0, int(course_id), "") if course_id.isdigit() else (1, 0, course_id)


# --- options ---------------------------------------------------------------------------------------------------------------

def _choice(entry, source=None):
    source = source or CANVAS
    if not isinstance(entry, dict):
        raise ValueError(f"Each {source.label} {source.noun}'s choice must be an object with an action.")
    action = entry.get("action")
    if action == "skip":
        return {"action": "skip"}
    if action == "item":
        item_id = entry.get("item_id")
        if not isinstance(item_id, str) or not 0 < len(item_id) <= 100:
            raise ValueError(f"Choose the gradebook column each {source.label} {source.noun} goes to.")
        return {"action": "item", "item_id": item_id}
    if action != "new":
        raise ValueError(f"Each {source.label} {source.noun} goes to a gradebook column, a new column, or is skipped.")
    title = collapse(entry.get("title")) if isinstance(entry.get("title"), str) else ""
    if not 0 < len(title) <= MAX_TITLE:
        raise ValueError(f"A new column needs a title of 1 to {MAX_TITLE} characters.")
    category = entry.get("category_id")
    if category is not None and (not isinstance(category, str) or not 0 < len(category) <= 100):
        raise ValueError(f"Choose a category for the new column “{title}”, or none.")
    name = entry.get("category_name")
    name = collapse(name) if isinstance(name, str) else None
    if category is not None or not name:
        name = None
    elif len(name) > NAME:
        raise ValueError(f"A new category's name has at most {NAME} characters.")
    points = common.parse_points(entry.get("points"), f"The new column “{title}”'s points", allow_blank=True)
    if points is not None and Decimal(points) == 0:
        raise ValueError(f"The new column “{title}” needs more than 0 points.")
    return {"action": "new", "title": title, "category_id": category, "category_name": name, "points": points}


def normalize_options(value, source=None):
    """{"mapping": {"<course>:<assignment>": choice}|None, "replace": sorted rows, "missing_as_missing": bool}. Idempotent, so a
    plan's `options` can be sent back with the apply. A null mapping asks for the suggested one; an assignment the mapping
    does not name takes its suggestion too. `source` (default Canvas) names what is imported and the keys it uses."""
    source = source or CANVAS
    if value is None:
        value = {}
    if isinstance(value, str):
        if len(value.encode()) > MAX_OPTIONS:
            raise ValueError("The import options are too large.")
        try:
            value = json.loads(value) if value.strip() else {}
        except ValueError:
            raise ValueError("The import options could not be read.") from None
    if not isinstance(value, dict):
        raise ValueError("The import options could not be read.")
    unknown = sorted(map(str, set(value) - {"mapping", "replace", "missing_as_missing"}))
    if unknown:
        raise ValueError(f"Unknown import option: {', '.join(unknown)[:100]}.")
    mapping = value.get("mapping")
    if mapping is not None:
        if not isinstance(mapping, dict) or len(mapping) > 5000 or any(not isinstance(key, str) or not source.key.fullmatch(key) for key in mapping):
            raise ValueError(f"The import options name {source.label} {source.noun}s that could not be read.")
        mapping = {key: _choice(mapping[key], source) for key in sorted(mapping)}
    replace = value.get("replace")
    replace = [] if replace is None else replace
    if not isinstance(replace, list) or len(replace) > MAX_REPLACE:
        raise ValueError("Replace must list students of this term's roster.")
    for row in replace:
        common.split_row(row)
    missing = value.get("missing_as_missing")
    missing = False if missing is None else missing
    if not isinstance(missing, bool):
        raise ValueError(f"Whether {source.missing} is recorded as missing must be true or false.")
    return {"mapping": mapping, "replace": sorted(set(replace)), "missing_as_missing": missing}


# --- Canvas, read into columns and values ------------------------------------------------------------------------------

def columns_of(canvas, courses):
    """Every gradable assignment of the linked courses, course by course in Canvas's order: {key, course_id, course, assignment_id,
    title, points, due_date, group, published, webassign, quiz}. `not_graded` assignments and deleted ones are not columns; they
    are counted. `webassign`: WebAssign grades it and sends its scores to Canvas (`canvas.web.graded_by_webassign`); `quiz`: Canvas
    grades it itself as a quiz (`canvas.web.canvas_quiz`)."""
    columns, skipped = [], 0
    for course_id in sorted(canvas, key=int):
        for group in canvas[course_id]["groups"]:
            for a in group["assignments"]:
                if a["grading_type"] == "not_graded" or a["state"] == "deleted":
                    skipped += 1
                    continue
                points, _ = _points(a["points"])
                columns.append({"key": assignment_key(course_id, a["id"]), "course_id": course_id,
                                "course": courses.get(course_id, {}).get("name") or course_id, "assignment_id": a["id"],
                                "title": collapse(a["name"])[:MAX_TITLE] or f"Canvas assignment {a['id']}",
                                "points": points, "due_date": campus_date(a["due_at"]), "group": collapse(group["name"])[:NAME],
                                "published": a["published"], "webassign": graded_by_webassign(a.get("tool", "")), "quiz": bool(a.get("quiz"))})
    return columns, skipped


@dataclass(frozen=True)
class Source:
    """What an import reads: Canvas here; iClicker (gradebook/iclicker_import.py) reuses the same planner. `columns` turns the
    read data into columns (`columns_of`); `per_section` makes new columns apply only to the sections linked to their course and
    WIDENS a matched column limited to other sections (a polling session belongs to one section's course); `unlinked` lists the
    gradebook columns nothing goes to (Canvas's column search); `new_when_empty` suggests a new column for one with nothing
    graded yet as well (Canvas: every assignment will be filled; an iClicker session with no scores is skipped). `own` is the
    origin (`changes.origin`) of the values this source wrote: it updates only those and KEEPS a value any other import or a pull
    wrote unless Replace is ticked (requirement, 2026-10-04, iClicker then Canvas: KEEPS OTHERS)."""
    label: str
    noun: str
    rule: str
    key: re.Pattern
    columns: object
    missing: str
    per_section: bool = False
    unlinked: bool = True
    new_when_empty: bool = False
    own: str = ""


CANVAS = Source("Canvas", "assignment", RULE, KEY, columns_of, "Canvas's missing flag", new_when_empty=True, own="canvas")


def _value(submission, tally, entered=False):
    """One Canvas submission: scored (its score after penalties), excused, missing (the flag, no score), waiting (pending review),
    blank or unreadable. `posted` says whether students see it. `entered` reads the grade as entered, before Canvas's late penalty
    (Sync with Canvas, for a column Faculty owns: what Faculty sends is the entered grade)."""
    if submission is None:
        return {"kind": "blank", "points": None, "posted": False}
    posted = submission["posted"]
    if submission["excused"]:
        return {"kind": "excused", "points": None, "posted": posted}
    if submission["state"] in WAITING:
        return {"kind": "waiting", "points": None, "posted": posted}
    score = submission.get("entered_score") if entered and submission.get("entered_score") is not None else submission["score"]
    if score is not None:
        points, rounded = _points(score)
        if points is None:
            return {"kind": "unreadable", "points": None, "posted": posted, "raw": str(score)[:40]}
        tally["rounded"] += rounded
        return {"kind": "scored", "points": points, "posted": posted}
    if submission["missing"]:
        return {"kind": "missing", "points": None, "posted": posted}
    return {"kind": "blank", "points": None, "posted": posted}


# --- suggestions and checks --------------------------------------------------------------------------------------------

def owner_of(column):
    """Who grades this Canvas assignment when Canvas does (Canvas always wins there): "webassign", "quiz" (a Canvas quiz) or None
    (Faculty's)."""
    return "webassign" if column.get("webassign") else "quiz" if column.get("quiz") else None


def eligible(item, column, source=None):
    """Whether this assignment (or session) may go to this column (docs/decisions/canvas.md SYNC WITH CANVAS). A column Canvas grades
    (`canvas` "webassign": WebAssign's through Canvas; "quiz": a Canvas quiz) takes only an assignment graded the same way, and
    Faculty's own grading never takes one; a column the instructor keeps off Canvas (`canvas` "off") takes no Canvas assignment; any
    other column takes any other."""
    mark = (item or {}).get("canvas")
    if (source or CANVAS) is CANVAS and mark == "off":
        return False
    owner = owner_of(column)
    if owner:
        return mark == owner or (mark is None and not linked_to_grading(item))
    return mark not in OWNED


def _suggest(column, has_values, items, taken, categories, remembered, warnings, label="Canvas", new_when_empty=True, source=None):
    """A Canvas assignment's starting point: its remembered match, else one column by title (exact, then canonical; points
    and due date narrow ties), else a new column in the category named after its assignment group (with nothing graded yet,
    only when `new_when_empty`). `taken` holds the columns this course's assignments already go to. Only `eligible` columns are
    suggested; one remembered that is now kept off Canvas is skipped without asking."""
    memory = remembered.get(column["key"])
    if memory is not None:
        if memory["item_id"] is None:
            return {"action": "skip"}, "remembered"
        item = items.get(memory["item_id"])
        if item and not item.get("archived") and (source or CANVAS) is CANVAS and item.get("canvas") == "off":
            return {"action": "skip"}, "off"
        if item and not item.get("archived") and item["id"] not in taken and eligible(item, column, source):
            return {"action": "item", "item_id": item["id"]}, "remembered"
        warnings.append(f"“{column['title']}” was imported into a column that is now archived or gone: choose where it goes.")
    live = [item for item in items.values() if not item.get("archived") and item["id"] not in taken and eligible(item, column, source)
            and not any(r["item_id"] == item["id"] for k, r in remembered.items() if k.split(":")[0] == column["course_id"] and k != column["key"])]
    for key in (fold, canonical):
        matches = [item for item in live if key(item["title"]) == key(column["title"])]
        if len(matches) > 1 and column["points"] is not None:
            narrowed = [item for item in matches if Decimal(item["points"]) == Decimal(column["points"])]
            matches = narrowed or matches
        if len(matches) > 1 and column["due_date"]:
            narrowed = [item for item in matches if item.get("due_date") == column["due_date"]]
            matches = narrowed or matches
        if len(matches) == 1:
            return {"action": "item", "item_id": matches[0]["id"]}, "title"
        if matches:
            warnings.append(f"Several gradebook columns could be {label}'s “{column['title']}”: choose one.")
            return {"action": "skip"}, "ambiguous"
    if not has_values and not new_when_empty:
        return {"action": "skip"}, "empty"
    if column["points"] is None or Decimal(column["points"]) <= 0:
        # A new column needs points and Canvas gives none: the instructor gives them (or chooses a column) under "Needs your
        # choice", and the rest imports meanwhile (requirement, 2026-10-02: one such column held back an import of 43 new columns).
        return {"action": "skip"}, "points"
    category = categories.get(fold(column["group"])) if column["group"] else None
    return {"action": "new", "title": column["title"], "category_id": category["id"] if category else None,
            "category_name": None if category or not column["group"] else column["group"], "points": column["points"]}, "new"


ASKABLE = ("ambiguous", "new", "points")  # first-time suggestions Jev may name a column for (JEV CANVAS-MATCH)
JEV_CONFIDENCE = .8


def _jev_matches(columns, hints, askable, items, taken, remembered, suggested, reasons):
    """JEV CANVAS-MATCH (requirement, 2026-10-03): an assignment matched for the first time that the titles could not place
    (several candidates, none, or no points) takes the column Jev named, in both option orders, at confidence .8 or more,
    when that column is live, not already another assignment's in this course, and worth the same points (compared here,
    never by Jev). Two assignments of one course naming one column take neither. The reason is "jev" and the Import confirms
    it; every other pick is only shown first among the likely matches. Returns {key: {"item_id", "confidence", "applied"}}."""
    shown, claims = {}, {}
    for column in columns:
        hint = hints.get(column["key"])
        if column["key"] not in askable or not hint:
            continue
        item = items.get(hint["item_id"])
        if item is None or item.get("archived") or not eligible(item, column):
            continue
        shown[column["key"]] = {"item_id": item["id"], "confidence": round(hint["confidence"], 3), "applied": False}
        course = column["course_id"]
        free = item["id"] not in taken.get(course, set()) and not any(
            r["item_id"] == item["id"] for k, r in remembered.items() if k.split(":")[0] == course and k != column["key"])
        same_points = column["points"] is not None and Decimal(column["points"]) == Decimal(item["points"])
        if free and same_points and hint["confidence"] >= JEV_CONFIDENCE:
            claims.setdefault((course, item["id"]), []).append(column["key"])
    for (course, item_id), keys in claims.items():
        if len(keys) != 1:
            continue
        key = keys[0]
        suggested[key], reasons[key] = {"action": "item", "item_id": item_id}, "jev"
        taken.setdefault(course, set()).add(item_id)
        shown[key]["applied"] = True
    return shown


def _merge_new(columns, mapping, items, source=None, course_sections=None, mirror=None):
    """The new columns: one per title (case-insensitive), gathering the same title of several courses when their points and
    category agree. Refuses a title the gradebook has (archived included), one course's two assignments with one new title, and
    a new title given two different points or categories. A per-section source's new column (and every new column of Sync with
    Canvas, `course_sections` given) applies only to the sections its courses are linked to (`sections`); one made of
    assignments Canvas grades (`mirror`: WebAssign's, a Canvas quiz) is marked so (`canvas`), and never merged with one that is
    not graded the same way."""
    source = source or CANVAS
    existing = {fold(item["title"]): item for item in items.values()}
    merged, order = {}, []
    for column in columns:
        choice = mapping[column["key"]]
        if choice["action"] != "new":
            continue
        name = fold(choice["title"])
        if name in existing:
            raise ValueError(f"The gradebook already has a column titled “{existing[name]['title']}”: choose it or another title.")
        entry = merged.get(name)
        spec = (choice["points"], choice["category_id"], fold(choice["category_name"] or ""),
                owner_of(column) if mirror is not None and column["key"] in mirror else None)
        if entry is None:
            merged[name] = {"title": choice["title"], "points": choice["points"], "category_id": choice["category_id"],
                            "category_name": choice["category_name"], "columns": [column["key"]], "courses": {column["course_id"]}, "spec": spec}
            order.append(name)
            continue
        if column["course_id"] in entry["courses"]:
            raise ValueError(f"Two {source.noun}s of one {source.label} course would both become the new column “{choice['title']}”: give them different titles.")
        if entry["spec"] != spec:
            raise ValueError(f"The new column “{choice['title']}” is given different points or categories: make them the same, or give them different titles.")
        entry["columns"].append(column["key"])
        entry["courses"].add(column["course_id"])
    new = [{key: merged[name][key] for key in ("title", "points", "category_id", "category_name", "columns")}
           | ({"canvas": merged[name]["spec"][3]} if merged[name]["spec"][3] else {}) for name in order]
    if course_sections is not None:
        for entry, name in zip(new, order):
            entry["sections"] = sorted({s for course in merged[name]["courses"] for s in (course_sections or {}).get(course, ())})
    return new


def _check(mapping, columns, items, categories, label="Canvas", source=None):
    by_course = {}
    for column in columns:
        choice = mapping[column["key"]]
        if choice["action"] == "item":
            item = items.get(choice["item_id"])
            if item is None:
                raise ValueError(f"The gradebook column chosen for “{column['title']}” no longer exists. Preview the import again.")
            if item.get("archived"):
                raise ValueError(f"“{item['title']}” is archived: restore it in the setup or choose another column for “{column['title']}”.")
            if not eligible(item, column, source):
                why = ("is kept off Canvas" if item.get("canvas") == "off" else "is graded by WebAssign through Canvas"
                       if item.get("canvas") == "webassign" else "is a Canvas quiz's" if item.get("canvas") == "quiz" else "is graded in Faculty")
                raise ValueError(f"“{item['title']}” {why}: choose another column for “{column['title']}”.")
            other = by_course.setdefault(column["course_id"], {}).get(item["id"])
            if other:
                raise ValueError(f"{label}'s “{other}” and “{column['title']}” (one course) both go to “{item['title']}”: choose one.")
            by_course[column["course_id"]][item["id"]] = column["title"]
        elif choice["action"] == "new" and choice["category_id"] is not None and choice["category_id"] not in categories:
            raise ValueError(f"The category chosen for the new column “{choice['title']}” no longer exists.")


# --- the plan ----------------------------------------------------------------------------------------------------------

def plan(canvas, *, options, students, links, setup, revision, cells, remembered, term_uins=None, sent=None, source=None, hints=None,
         origins=None, sync=None):
    """The Canvas ImportPlan of a term's gradebook.

    `canvas` {course_id: {"groups", "students", "submissions"}} (`CanvasService.read_grades`); `students` every membership of the
    term ({"row", "section_id", "section", "name", "uin", "dropped"}); `links` {section_id: {"course_id", "name"}}; `remembered`
    {"<course>:<assignment>": {"item_id": str|None}} (the latest match per assignment). `sent` {("<course>:<assignment>", row):
    {"status", "points"}}: what Faculty itself last sent there (Sending to Canvas); Canvas holding exactly that is Faculty's own
    value read back, never a change to import. Only `add` and `update` changes are writes, and only when `ready`. `source`
    (default Canvas) is what is imported (`Source`). `hints` {"<course>:<assignment>": {"item_id", "confidence"}}: Jev's agreed
    picks for assignments matched for the first time (jev/matching.py, JEV CANVAS-MATCH), applied by `_jev_matches`. `origins`
    {cell revision id: `changes.origin`} of the imported and pulled cells: a value another source wrote is kept unless Replace is
    ticked (`Source.own`, KEEPS OTHERS), and such a kept (or, ticked, replaced) change names where it came from (`from`).

    `sync` {"mirror": keys or None} is Sync with Canvas's From-Canvas half (docs/decisions/canvas.md SYNC WITH CANVAS): an
    assignment Canvas grades (`mirror`, default every WebAssign one and every Canvas quiz) MIRRORS Canvas, its score written over
    any value and a blank in Canvas taking Faculty's out (`clear`); any other assignment is Faculty's, and only an assignment linked in this plan
    (a new column, or a column it goes to for the first time) takes Canvas's grade as entered, into a cell Faculty never filled.
    Every other value is the To-Canvas half's to compare (`canvas_export.plan`). New columns apply only to the sections linked to
    the courses that have the assignment, and a column limited to other sections is widened, as for iClicker; a column a mirrored
    assignment goes to is marked `canvas` "webassign" or "quiz" (`mark` {column: mark})."""
    source = source or CANVAS
    label, noun = source.label, source.noun
    sent = sent or {}
    options = normalize_options(options, source)
    items = {item["id"]: item for item in setup.get("items", [])}
    categories = {category["id"]: category for category in setup.get("categories", [])}
    by_name = {fold(category["name"]): category for category in setup.get("categories", [])}
    courses = {}
    for link in links.values():
        courses.setdefault(link["course_id"], {"id": link["course_id"], "name": link.get("name") or link["course_id"], "sections": []})
    for student in students:
        link = links.get(student["section_id"])
        if link and student["section"] not in courses[link["course_id"]]["sections"]:
            courses[link["course_id"]]["sections"].append(student["section"])
    columns, not_graded = source.columns(canvas, courses)
    warnings, tally = [], {"rounded": 0}
    if not_graded:
        warnings.append(f"{not_graded} {label} {noun}{'s are' if not_graded != 1 else ' is'} not graded (or deleted) and not imported.")
    course_sections = {}
    for section_id, link in links.items():
        course_sections.setdefault(link["course_id"], set()).add(section_id)

    # Who is who: a membership reads the course linked to ITS section, the Canvas student with its UIN as SIS user ID.
    enrolled = {}  # course -> {uin: user_id}
    only_canvas, no_id = [], 0
    for course_id, data in canvas.items():
        chosen = {}
        for s in data["students"]:
            if NINE.fullmatch(s["sis"] or "") and (s["sis"] not in chosen or s["state"] == "active"):
                chosen[s["sis"]] = s
            elif not NINE.fullmatch(s["sis"] or "") and s["state"] in ("active", "invited"):
                no_id += 1
        enrolled[course_id] = chosen
    rows, not_linked, dropped, not_in_canvas = [], 0, 0, []
    faculty = {}  # course -> uins here
    for student in students:
        link = links.get(student["section_id"])
        if not link:
            not_linked += 1
            continue
        faculty.setdefault(link["course_id"], set()).add(student["uin"])
        if student.get("dropped"):
            dropped += 1
            continue
        found = enrolled.get(link["course_id"], {}).get(student["uin"]) if student.get("uin") else None
        if not found:
            not_in_canvas.append({"name": student["name"], "section": student["section"]})
            continue
        rows.append((student, link["course_id"], found["user_id"]))
    for course_id, chosen in enrolled.items():
        for uin, s in chosen.items():
            # A student of another section of the term (an import from one section's tab reads only that section) is not "on no roster".
            if uin not in faculty.get(course_id, set()) and uin not in (term_uins or ()) and s["state"] in ("active", "invited"):
                only_canvas.append({"name": s["name"], "course": courses.get(course_id, {}).get("name", course_id)})
    rows.sort(key=lambda entry: (entry[0]["name"].casefold(), str(entry[0]["section"]), entry[0]["row"]))
    submissions = {}
    for course_id, data in canvas.items():
        for s in data["submissions"]:
            submissions[(course_id, s["user_id"], s["assignment_id"])] = s
    mirror = None if sync is None else (set(sync["mirror"]) if sync.get("mirror") is not None
                                         else {column["key"] for column in columns if owner_of(column)})
    values = {column["key"]: {} for column in columns}
    for column in columns:
        entered = mirror is not None and column["key"] not in mirror  # Faculty's own column: the grade as entered, before penalties
        for student, course_id, user_id in rows:
            if course_id == column["course_id"]:
                values[column["key"]][student["row"]] = _value(submissions.get((course_id, user_id, column["assignment_id"])), tally, entered)

    # Where each assignment goes: the instructor's choice, else the suggestion.
    suggested, reasons, taken = {}, {}, {}
    for column in columns:
        has_values = any(value["kind"] != "blank" for value in values[column["key"]].values())
        mine = taken.setdefault(column["course_id"], set())
        choice, reason = _suggest(column, has_values, items, mine, by_name, remembered, warnings, label, source.new_when_empty, source)
        if choice["action"] == "item":
            mine.add(choice["item_id"])
        suggested[column["key"]], reasons[column["key"]] = choice, reason
    askable = {column["key"] for column in columns if source is CANVAS and column["key"] not in remembered and reasons[column["key"]] in ASKABLE}
    jev = _jev_matches(columns, hints or {}, askable, items, taken, remembered, suggested, reasons)
    # Suggestions never collide: a title an archived column has, a second assignment of one course with the same new title, and
    # the same title worth other points in another course are skipped with a warning (the warnings only while suggesting).
    speak = options["mapping"] is None
    seen, first = {}, {}
    existing = {fold(item["title"]) for item in items.values()}
    for column in columns:
        choice = suggested[column["key"]]
        if choice["action"] != "new":
            continue
        name, spec = fold(choice["title"]), (choice["points"], choice["category_id"], choice["category_name"])
        if name in existing:
            reason = f"“{column['title']}” matches an archived gradebook column: restore it or choose a column for it."
        elif column["course_id"] in seen.get(name, set()):
            reason = f"{label} has two {noun}s titled “{column['title']}”: the second is skipped unless you choose a column for it."
        elif name in first and first[name] != spec:
            reason = f"“{column['title']}” is worth other points in {column['course']}: it is skipped unless you choose a column for it."
        else:
            seen.setdefault(name, set()).add(column["course_id"])
            first.setdefault(name, spec)
            continue
        suggested[column["key"]], reasons[column["key"]] = {"action": "skip"}, "ambiguous"
        if speak:
            warnings.append(reason)
    chosen = options["mapping"] or {}
    if set(chosen) - {column["key"] for column in columns}:
        raise ValueError(f"The import options name a {label} {noun} these courses no longer have. Preview the import again.")
    mapping = {column["key"]: chosen.get(column["key"], suggested[column["key"]]) for column in columns}
    _check(mapping, columns, items, categories, label, source)
    sections_of = course_sections if (source.per_section or sync is not None) else None
    new_items = _merge_new(columns, mapping, items, source, sections_of, mirror)
    if sync is not None:
        # A new column the term's every section gets applies to all of them (no list), as a column made in the setup does.
        everyone = {student["section_id"] for student in students}
        for entry in new_items:
            if set(entry.get("sections") or ()) >= everyone:
                entry["sections"] = []
    adopting = {column["key"] for column in columns if mapping[column["key"]]["action"] == "new" or (
        mapping[column["key"]]["action"] == "item" and (remembered.get(column["key"]) or {}).get("item_id") != mapping[column["key"]]["item_id"])}
    ready = True
    for entry in new_items:
        if entry["points"] is None:
            ready = False
            warnings.append(f"Give the new column “{entry['title']}” its points: {label} gives it none.")
    if new_items and len(items) + len(new_items) > MAX_ITEMS:
        ready = False
        warnings.append(f"The gradebook has {len(items)} columns (archived ones included) and holds at most {MAX_ITEMS}: "
                        f"skip some of the {len(new_items)} new columns or choose existing columns for them.")
    new_categories = sorted({fold(e["category_name"]): e["category_name"] for e in new_items if e["category_name"] and fold(e["category_name"]) not in by_name}.values(), key=str.casefold)
    new_of = {key: entry for entry in new_items for key in entry["columns"]}

    replace = set(options["replace"])
    column_counts = {column["key"]: {key: 0 for key in COUNTS} for column in columns}
    changes, unreadable, widen = [], [], {}
    for column in columns:
        choice, counts = mapping[column["key"]], column_counts[column["key"]]
        if choice["action"] == "skip":
            continue
        item = items.get(choice.get("item_id")) if choice["action"] == "item" else None
        compare = linked_to_grading(item)
        entry = new_of.get(column["key"])
        if entry is not None and entry["points"] is None:
            continue
        mirrored = mirror is not None and column["key"] in mirror
        if mirror is not None and (compare or not (mirrored or column["key"] in adopting)):
            continue  # SYNC WITH CANVAS: a column Faculty owns, linked before: the To-Canvas half compares it (canvas_export.plan)
        for student, course_id, _ in rows:
            if course_id != column["course_id"]:
                continue
            if item is not None and item.get("sections") and student["section_id"] not in item["sections"]:
                if not ((source.per_section or mirror is not None) and not compare):
                    counts["not_applicable"] += 1
                    continue
                # A per-section source's session matched to a column limited to other sections: the column is WIDENED to this
                # student's section (in the import's setup revision), never skipped. Columns Faculty grades itself keep theirs.
                widen.setdefault(item["id"], set()).add(student["section_id"])
            value = values[column["key"]][student["row"]]
            if value["kind"] == "unreadable":
                counts["unreadable"] += 1
                unreadable.append({"column": column["key"], "row": student["row"], "name": student["name"], "raw": value["raw"]})
                continue
            current = cells.get(item["id"], {}).get(student["row"]) if item is not None else None
            held = current if current and current["status"] != "cleared" else None  # what Faculty's column says now
            if mirror is not None:
                # SYNC WITH CANVAS, From Canvas: WebAssign's score over anything (Canvas's blank takes Faculty's out); an assignment
                # Faculty owns, linked in this plan, fills only a cell Faculty never filled (or an import emptied).
                if value["kind"] == "waiting":
                    counts["waiting"] += 1
                    continue
                if value["kind"] == "blank" or (value["kind"] == "missing" and not options["missing_as_missing"]):
                    counts["blank"] += 1
                    counts["faculty_only"] += bool(held) and not mirrored  # THE GRADE CHECK: only Faculty has a grade
                    if not (mirrored and held):
                        continue
                    proposed, action = {"status": "cleared", "points": None}, "clear"
                    mismatch = True
                else:
                    proposed = {"status": value["kind"], "points": value["points"] if value["kind"] == "scored" else None}
                    empty = current is None or (current["status"] == "cleared" and current["source"] != "instructor")
                    # THE GRADE CHECK: where both hold a grade, do they agree (a first match that disagrees may be the wrong one)?
                    mismatch = bool(held) and not common.same_value(held, proposed)
                    counts["compared"] += bool(held)
                    counts["mismatch"] += mismatch
                    if common.same_value(current, proposed):
                        action = "same"
                    elif mirrored or empty:
                        action = "add" if current is None or current["status"] == "cleared" else "update"
                    else:
                        continue  # Faculty has its own value here: the To-Canvas half compares it
                counts[action] += 1
                if action in ("add", "update") and not value["posted"]:
                    counts["unposted"] += 1
                changes.append({"column": column["key"], "row": student["row"], "name": student["name"], "section": student["section"],
                                "action": action, "proposed": proposed, "current": dict(current) if current else None,
                                "item_id": item["id"] if item is not None else None, "compare": False, "posted": value["posted"],
                                "mismatch": mismatch, "mirror": mirrored})
                continue
            if value["kind"] == "waiting" or value["kind"] == "blank" or (value["kind"] == "missing" and not options["missing_as_missing"]):
                counts["waiting" if value["kind"] == "waiting" else "blank"] += 1
                if held:
                    # THE GRADE CHECK (requirement, 2026-09-28): a grade Faculty has and Canvas does not is listed; nothing is written.
                    counts["faculty_only"] += 1
                    changes.append({"column": column["key"], "row": student["row"], "name": student["name"], "section": student["section"],
                                    "action": "faculty_only", "proposed": None, "current": dict(current), "item_id": item["id"],
                                    "compare": compare, "posted": False, "mismatch": True})
                continue
            proposed = {"status": value["kind"], "points": value["points"] if value["kind"] == "scored" else None}
            own = sent.get((column["key"], student["row"]))
            foreign = None  # where a value this source keeps came from (KEEPS OTHERS)
            if compare:
                action = "same" if common.same_value(current, proposed) else "differs"
            elif own is not None and common.same_value(own, proposed):
                action = "same"  # Faculty's own value, sent to Canvas and read back: nothing to import
            else:
                # KEEPS OTHERS (requirement, 2026-10-04, for iClicker and then Canvas): a value another import or a pull brought in
                # (a 10/10 given in Canvas, a score iClicker recorded) is not this source's to overwrite.
                if source.own and current is not None and current["source"] != "instructor":
                    foreign = (origins or {}).get(current["id"], "pull")
                    foreign = None if foreign == source.own else foreign
                action = common.change_action(current, proposed, replace_hand=student["row"] in replace, foreign=foreign is not None)
            counts[action] += 1
            if action in ("add", "update") and not value["posted"]:
                counts["unposted"] += 1
            # THE GRADE CHECK: where both hold a grade, do they agree? (a value this source imported that differs is still updated,
            # a typed one or another source's kept, a compare-only one listed: the check only says so)
            mismatch = bool(held) and not common.same_value(held, proposed)
            counts["compared"] += bool(held)
            counts["mismatch"] += mismatch
            changes.append({"column": column["key"], "row": student["row"], "name": student["name"], "section": student["section"],
                            "action": action, "proposed": proposed, "current": dict(current) if current else None,
                            "item_id": item["id"] if item is not None else None, "compare": compare,
                            "posted": value["posted"], "mismatch": mismatch,
                            **({"from": foreign} if foreign is not None and action in ("kept", "update") else {})})
        if item is not None and column["points"] is not None and Decimal(column["points"]) != Decimal(item["points"]) and not compare:
            warnings.append(f"{label} says “{column['title']}” is worth {column['points']} points; "
                            f"the gradebook column “{item['title']}” is worth {common.points_text(item['points'])}. Scores are recorded as {label} has them.")
    if tally["rounded"]:
        warnings.append(f"{tally['rounded']} {label} score{'s' if tally['rounded'] != 1 else ''} had more than four decimal places and "
                        f"{'were' if tally['rounded'] != 1 else 'was'} rounded half up to four.")
    totals = {key: sum(counts[key] for counts in column_counts.values()) for key in COUNTS}
    totals.update(not_in_canvas=len(not_in_canvas), only_canvas=len(only_canvas), no_id=no_id, not_linked=not_linked, dropped=dropped)
    remember = {}
    for column in columns:
        choice, memory = mapping[column["key"]], remembered.get(column["key"])
        if choice["action"] == "item":
            target = choice["item_id"]
        elif choice["action"] == "skip" and suggested[column["key"]]["action"] != "skip":
            target = None  # The instructor skipped what Faculty suggested importing: remembered, never asked again.
        else:
            continue  # A skip Faculty suggested (several possible columns, no points, an iClicker session with no scores) decides nothing; a
                      # new column is remembered once it has its ID, at the apply.
        if memory is None or memory["item_id"] != target:
            remember[column["key"]] = target
    # Each match's grade check. A PROPOSED match (one not remembered yet: suggested by title or chosen in this preview) whose grades
    # disagree is warned about by name, since it may be the wrong assignment.
    checks = {}
    for column in columns:
        choice, counts = mapping[column["key"]], column_counts[column["key"]]
        if choice["action"] != "item":
            continue
        proposed = column["key"] in remember
        checks[column["key"]] = {"compared": counts["compared"], "mismatch": counts["mismatch"], "faculty_only": counts["faculty_only"],
                                 "proposed": proposed}
        if proposed and (counts["mismatch"] or counts["faculty_only"]):
            item = items[choice["item_id"]]
            parts = [f"{counts['mismatch']} of {counts['compared']} grades differ" if counts["mismatch"] else "",
                     f"{counts['faculty_only']} grade{'s are' if counts['faculty_only'] != 1 else ' is'} only in Faculty" if counts["faculty_only"] else ""]
            warnings.append(f"Check the proposed match of {label}’s “{column['title']}” with “{item['title']}”: "
                            + " and ".join(part for part in parts if part) + ". It may not be the same assignment.")
    # Gradebook columns no Canvas assignment goes to, per linked course whose students the column applies to (asked about on the page).
    targeted = {(column["course_id"], mapping[column["key"]]["item_id"]) for column in columns if mapping[column["key"]]["action"] == "item"}
    unlinked = []
    for item in setup.get("items", []) if source.unlinked else []:
        if item.get("archived"):
            continue
        missing = [course_id for course_id in sorted(canvas, key=course_order) if (course_id, item["id"]) not in targeted
                   and (not item.get("sections") or set(item["sections"]) & course_sections.get(course_id, set()))]
        if missing:
            unlinked.append({"item_id": item["id"], "title": item["title"], "points": common.points_text(item["points"]),
                             "compare_only": linked_to_grading(item), "courses": missing})
    widened = {item_id: sorted(found) for item_id, found in sorted(widen.items())}
    # SYNC WITH CANVAS: a column a mirrored assignment goes to is Canvas's from now on (`canvas` "webassign" or "quiz", in the setup
    # revision).
    by_key = {column["key"]: column for column in columns}
    mark = {mapping[key]["item_id"]: owner_of(by_key[key]) for key in sorted(mirror or ()) if key in mapping and mapping[key]["action"] == "item"
            and owner_of(by_key[key]) and (items.get(mapping[key]["item_id"]) or {}).get("canvas") != owner_of(by_key[key])}
    synced = {"mirror": sorted(mirror), "mark": mark, "adopting": sorted(adopting)} if mirror is not None else None
    fingerprint = common.digest({
        "rule": source.rule, "revision": revision, "options": options, "mapping": mapping, "remember": remember,
        "columns": [[c["key"], c["title"], c["points"], c["due_date"]] for c in columns],
        "changes": sorted([c["column"], c["row"], c["action"], common.encoded(c["proposed"]), c["posted"],
                           common.encoded(c["current"]["id"] if c["current"] else None)] for c in changes),
        **({"widen": widened, "new_sections": [e.get("sections") for e in new_items]} if source.per_section or synced else {}),
        **({"sync": synced, "new_marks": [e.get("canvas") for e in new_items]} if synced else {})})
    return {"fingerprint": fingerprint, "rule": source.rule, "revision": revision, "ready": ready, "options": options,
            **({"widen": widened} if source.per_section or synced else {}), **({"sync": synced} if synced else {}),
            "courses": [{"id": c["id"], "name": c["name"], "sections": sorted(c["sections"], key=str)} for c in courses.values()],
            "columns": [{**column, "mapping": mapping[column["key"]], "suggested": reasons[column["key"]],
                         "remembered": column["key"] in remembered, "counts": column_counts[column["key"]],
                         "check": checks.get(column["key"]), **({"askable": True} if column["key"] in askable else {}),
                         **({"jev": jev[column["key"]]} if column["key"] in jev else {})} for column in columns],
            "unlinked_items": unlinked,
            "new_items": new_items, "new_categories": new_categories, "remember": remember, "changes": changes,
            "unreadable": unreadable, "not_in_canvas": sorted(not_in_canvas, key=lambda s: s["name"].casefold()),
            "only_canvas": sorted(only_canvas, key=lambda s: s["name"].casefold()), "counts": totals, "warnings": warnings}
