"""Validation of a term's gradebook setup: categories, columns, the letter scale and the grading policies (README "Setup").

The setup belongs to the TERM (`gradebook_setups`): every save is a new immutable revision, and the service compares
normalized setups so that an identical save records nothing. `normalize` therefore returns ONE canonical form for one
logical setup: points as `common.parse_points` writes them, names and titles stripped, section IDs sorted, link parts in
the order frq, bubble, exams, and every dict's keys in the README's order.

IDs are the server's. A category or column sent without an `id` gets a new one here (`common.new_id`); an `id` the base
revision does not hold is refused, because a browser that invented or resurrected an ID could hang old scores on a new
column. A column or category missing from a save is removed, except a column with any score revision (its scores would
lose their column: archive it) and a category a column still names. A category created in the same save may be chosen
for a column before it has an ID: the new category carries a `ref` (browser text, never stored) and the column names that
ref as its `category_id`.

A field left out of an existing entry keeps the base revision's value, and a top-level field left out keeps the base's;
a list that is sent replaces the whole list. A new entry takes the defaults below, except a category's name and a
column's title and points, which it must give. `replaces_lowest` is stored only when true, so setups saved before it
keep their canonical form; so is `canvas` (docs/decisions/canvas.md SYNC WITH CANVAS): "off" for a column the instructor keeps
off Canvas, "webassign" for one WebAssign grades through Canvas and "quiz" for a Canvas quiz (`OWNED`: Canvas's), which only Sync
with Canvas and the nightly import set (`server=True`): a save from the browser keeps it as it is and can neither give nor take
it away. Every value sent is checked, and an instructor-readable ValueError says
what is wrong; nothing is silently dropped or clamped.
"""
import re
from datetime import date
from decimal import Decimal

from .common import RULE, new_id, parse_points

MODES = ("points", "weighted")
AGGREGATES = ("points", "equal")
UNGRADED = ("exclude", "zero", "due")  # "due": GRADEBOOK "ZERO ONCE DUE" (gradebook/blanks.py)
CANVAS = ("off", "webassign", "quiz")  # a column's place in Sync with Canvas; absent: Faculty's, synced to Canvas
OWNED = ("webassign", "quiz")  # Canvas's own: WebAssign's scores, a Canvas quiz's (Canvas always wins; read-only here)
PARTS = ("frq", "bubble", "exams")  # Also the canonical order of a link's parts.
POSTING = ("due", "certified")  # a category's automatic posting in Canvas (gradebook/posting_policy.py); absent: by hand
MAX_CATEGORIES, MAX_ITEMS, MAX_SCALE, MAX_DROP, MAX_ROUNDING = 50, 500, 30, 50, 2
MAX_WEIGHT = MAX_MINIMUM = Decimal(1000)
NAME, TITLE, LETTER, NOTES, REF = 60, 120, 4, 1000, 64
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SCALE = (("A", "90"), ("B", "80"), ("C", "70"), ("D", "60"), ("F", "0"))
CATEGORY_DEFAULTS = {"weight": "0", "drop_lowest": 0, "aggregate": "points"}
ITEM_DEFAULTS = {"category_id": None, "due_date": "", "counts": True, "extra_credit": False, "archived": False, "sections": [],
                 "assignment_id": None, "link": None, "notes": ""}


# Empty cells of a term without a revision counted as zero once due from here on (requirement, 2026-10-06); a first revision saved before it was made on "exclude", which the Changes page compares it with.
DUE_DEFAULT_SINCE = "2026-10-06T19:45:00+00:00"


def default_setup(made_at=None):
    """The setup of a term without a revision: no categories or columns, the A-F scale, points mode, empty cells counted as zero
    once due. `made_at` (a revision's `created_at`): the default in force then, "exclude" empty cells before DUE_DEFAULT_SINCE."""
    ungraded = "exclude" if made_at and made_at < DUE_DEFAULT_SINCE else "due"
    return {"rule": RULE, "mode": "points", "categories": [], "items": [],
            "scale": [{"letter": letter, "min": minimum} for letter, minimum in SCALE], "ungraded": ungraded, "letter_rounding": None}


def find_item(setup, item_id):
    return next((item for item in setup["items"] if item["id"] == item_id), None)


def applies(item, section_id):
    """Whether a column concerns a student of this section: an empty section list means every section of the term."""
    return not item["sections"] or section_id in item["sections"]


def normalize(data, base, *, scored_items=frozenset(), sections=frozenset(), assignments=frozenset(), server=False):
    """The browser's setup, checked and in canonical form. `base` is the normalized setup of the revision it was made on
    (or `default_setup()`); `scored_items` the columns with any score revision; `sections` the term's section IDs;
    `assignments` the course-wide assignment IDs a column may reference. `server` is Faculty's own revision (Sync with Canvas,
    the nightly WebAssign import), the only one that may mark a column "webassign" or take the mark away."""
    if not isinstance(data, dict):
        raise ValueError("Send the gradebook setup.")
    base = base or default_setup()
    if data.get("rule", RULE) != RULE:
        raise ValueError("This setup was made for another version of the gradebook: reload the page and make the change again.")
    given = lambda key: data[key] if key in data else base[key]
    mode = given("mode")
    if mode not in MODES:
        raise ValueError("Choose points or weighted grading.")
    categories, refs = _categories(given("categories"), base["categories"])
    items = _items(given("items"), base, categories, refs, frozenset(scored_items), frozenset(sections), frozenset(assignments), server)
    ungraded = given("ungraded")
    if ungraded not in UNGRADED:
        raise ValueError("Choose whether an empty cell is left out, counts as zero, or counts as zero once due.")
    rounding = given("letter_rounding")
    if rounding is not None and (type(rounding) is not int or not 0 <= rounding <= MAX_ROUNDING):
        raise ValueError(f"Round the percentage for the letter to 0 to {MAX_ROUNDING} decimals, or not at all.")
    return {"rule": RULE, "mode": mode, "categories": categories, "items": items, "scale": _scale(given("scale")),
            "ungraded": ungraded, "letter_rounding": rounding}


def _categories(raw, prior):
    if not isinstance(raw, list):
        raise ValueError("Send the categories as a list.")
    if len(raw) > MAX_CATEGORIES:
        raise ValueError(f"A gradebook has at most {MAX_CATEGORIES} categories.")
    known, taken, refs, folded, clean = {category["id"]: category for category in prior}, set(), {}, set(), []
    for position, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Category {position} is not a category.")
        old = _existing(entry, known, taken, "category")
        if old is None:
            ident = new_id("cat")
            ref = entry.get("ref")
            if ref is not None:
                if not isinstance(ref, str) or not 1 <= len(ref) <= REF or ref in known or ref in refs:
                    raise ValueError(f"Category {position} was sent with an unusable reference: reload the setup and try again.")
                refs[ref] = ident
        else:
            ident = old["id"]
        field = lambda key: entry[key] if key in entry else (old or CATEGORY_DEFAULTS).get(key)
        name = _text(field("name"), f"Category {position}'s name", NAME)
        if name.casefold() in folded:
            raise ValueError(f"Two categories are named “{name}”.")
        folded.add(name.casefold())
        weight = parse_points(field("weight"), f"The weight of “{name}”", maximum=MAX_WEIGHT, unit="")
        drop = field("drop_lowest")
        if type(drop) is not int or not 0 <= drop <= MAX_DROP:
            raise ValueError(f"“{name}” can drop 0 to {MAX_DROP} lowest scores.")
        aggregate = field("aggregate")
        if aggregate not in AGGREGATES:
            raise ValueError(f"Choose how “{name}” adds up: total points or equal weight per column.")
        category = {"id": ident, "name": name, "weight": weight, "drop_lowest": drop, "aggregate": aggregate}
        posting = _posting(field("posting"), name)
        if posting:
            category["posting"] = posting  # stored only when automatic, so a setup made before it keeps its exact form
        clean.append(category)
    return clean, refs


def _posting(value, name):
    """POSTING GRADES (docs/decisions/canvas.md; requirement, 2026-10-08): how the category's columns are posted in Canvas. None (or
    nothing ticked): by hand. {"due": bool, "certified": bool}: automatically, after a column's due date and/or once Faculty
    certifies its grading (gradebook/posting_policy.py). Returns the canonical form, or None for by hand."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - set(POSTING) or any(type(value[key]) is not bool for key in value):
        raise ValueError(f"Choose how “{name}” is posted in Canvas: by hand, or automatically after the due date and/or when certified.")
    if not any(value.values()):
        return None
    return {key: bool(value.get(key)) for key in POSTING}


def _items(raw, base, categories, refs, scored, sections, assignments, server=False):
    if not isinstance(raw, list):
        raise ValueError("Send the columns as a list.")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"A gradebook has at most {MAX_ITEMS} columns.")
    known, taken, clean = {item["id"]: item for item in base["items"]}, set(), []
    kept_categories = {category["id"] for category in categories}
    dropped_categories = {category["id"]: category["name"] for category in base["categories"] if category["id"] not in kept_categories}
    for position, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Column {position} is not a column.")
        old = _existing(entry, known, taken, "column")
        field = lambda key: entry[key] if key in entry else (old or ITEM_DEFAULTS).get(key)
        title = _text(field("title"), f"Column {position}'s title", TITLE)
        label = f"“{title}”"
        category = field("category_id")
        if category == "":
            category = None  # A select's "Uncategorized".
        if category is not None and not isinstance(category, str):
            raise ValueError(f"{label} names a category this gradebook does not have: reload the setup and try again.")
        if category in refs:
            category = refs[category]
        elif category in dropped_categories:
            raise ValueError(f"The category “{dropped_categories[category]}” is still used by {label}: move the column first.")
        elif category is not None and category not in kept_categories:
            raise ValueError(f"{label} names a category this gradebook does not have: reload the setup and try again.")
        extra = _flag(field("extra_credit"), f"Extra credit for {label}")
        points = parse_points(field("points"), f"The points of {label}")
        if Decimal(points) == 0 and not extra:
            raise ValueError(f"{label} must be worth more than 0 points, unless it is extra credit.")
        due = field("due_date")
        due = "" if due is None else due.strip() if isinstance(due, str) else due
        if not isinstance(due, str) or (due and not _date(due)):
            raise ValueError(f"The due date of {label} must be a date (YYYY-MM-DD) or empty.")
        only = field("sections")
        if not isinstance(only, list) or any(not isinstance(section, str) or section not in sections for section in only):
            raise ValueError(f"{label} may only name sections of this term.")
        assignment = field("assignment_id")
        if assignment == "":
            assignment = None
        if assignment is not None and (not isinstance(assignment, str) or assignment not in assignments):
            raise ValueError(f"{label} names an assignment that is not one of this course's.")
        notes = field("notes")
        notes = _text("" if notes is None else notes, f"The notes of {label}", NOTES, required=False)
        replaces = field("replaces_lowest")
        replaces = False if replaces is None else _flag(replaces, f"Whether {label} replaces the lowest score")
        if replaces and category is None:
            raise ValueError(f"{label} replaces the lowest score of its category: choose its category.")
        if replaces and extra:
            raise ValueError(f"{label} cannot both replace the lowest score and be extra credit.")
        # SYNC WITH CANVAS: "off" is the instructor's; "webassign" and "quiz" are Faculty's own marks of a column Canvas grades (WebAssign
        # through it, or a Canvas quiz; Canvas always wins there), which a save from the browser keeps as they were.
        canvas = field("canvas")
        if canvas is not None and canvas not in CANVAS:
            raise ValueError(f"{label} is either kept off Canvas or synced with it.")
        was = (old or {}).get("canvas")
        if not server and canvas in OWNED and was != canvas:
            raise ValueError(f"Only Sync with Canvas marks {label} as graded in Canvas.")
        if not server and was in OWNED and canvas != was:
            raise ValueError(f"{label} is graded in Canvas: Canvas's scores are its scores.")
        link = _link(field("link"), assignment, label)
        if canvas in OWNED and link:
            raise ValueError(f"{label} is graded in Canvas, so it cannot pull Faculty's grading.")
        clean.append({"id": old["id"] if old else new_id("item"), "title": title, "category_id": category, "points": points,
                      "due_date": due, "counts": _flag(field("counts"), f"Whether {label} counts"), "extra_credit": extra,
                      "archived": _flag(field("archived"), f"Whether {label} is archived"), "sections": sorted(set(only)),
                      "assignment_id": assignment, "link": link, "notes": notes,
                      **({"replaces_lowest": True} if replaces else {}), **({"canvas": canvas} if canvas else {})})
    for ident, item in known.items():
        if ident not in taken and ident in scored:
            raise ValueError(f"“{item['title']}” has scores and cannot be removed: archive it instead.")
    replacing = {}
    for item in clean:
        if item.get("replaces_lowest") and not item["archived"]:
            other = replacing.setdefault(item["category_id"], item)
            if other is not item:
                raise ValueError(f"“{other['title']}” and “{item['title']}” both replace the lowest score of one category: keep one.")
    linked = {}
    for item in clean:
        if item["link"] and not item["archived"]:
            other = linked.setdefault(item["assignment_id"], item)
            if other is not item:
                raise ValueError(f"“{other['title']}” and “{item['title']}” both pull from the same assignment: archive one or remove its link.")
    return clean


def _link(value, assignment, label):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("kind") != "grading":
        raise ValueError(f"{label} can only be linked to Faculty's grading.")
    if assignment is None:
        raise ValueError(f"Choose the assignment {label} pulls from.")
    parts = value.get("parts")
    if not isinstance(parts, list) or not parts or any(part not in PARTS for part in parts) or len(set(parts)) != len(parts):
        raise ValueError(f"{label} pulls from FRQ grading, bubble sheets or the Exams page: choose each at most once, at least one.")
    return {"kind": "grading", "parts": [part for part in PARTS if part in parts]}


def _scale(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SCALE:
        raise ValueError(f"A letter scale has 1 to {MAX_SCALE} letters.")
    clean, folded, previous = [], set(), None
    for position, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Letter {position} of the scale is not a letter and its minimum.")
        letter = _text(entry.get("letter"), f"Letter {position} of the scale", LETTER)
        if letter.casefold() in folded:
            raise ValueError(f"The letter {letter} is in the scale twice.")
        folded.add(letter.casefold())
        minimum = parse_points(entry.get("min"), f"The minimum percentage for {letter}", maximum=MAX_MINIMUM, unit="percent")
        if previous is not None and Decimal(minimum) >= previous:
            raise ValueError("List the letters from the highest minimum down; each minimum must be lower than the one above it.")
        previous = Decimal(minimum)
        clean.append({"letter": letter, "min": minimum})
    return clean


def _existing(entry, known, taken, what):
    """The base revision's entry this one edits, or None for a new one; an ID the base does not hold is refused."""
    ident = entry.get("id")
    if ident in (None, ""):
        return None
    if not isinstance(ident, str) or ident not in known:
        raise ValueError(f"A {what} was sent with an ID this gradebook never gave it: reload the setup and try again.")
    if ident in taken:
        raise ValueError(f"A {what} was sent twice: reload the setup and try again.")
    taken.add(ident)
    return known[ident]


def _text(value, label, longest, *, required=True):
    if value is None and required:
        raise ValueError(f"{label} is missing.")
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label} is missing.")
    if len(value) > longest:
        raise ValueError(f"{label} can be at most {longest} characters long.")
    return value


def _flag(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be yes or no.")
    return value


def _date(text):
    if not DATE.fullmatch(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True
