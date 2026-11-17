"""What the gradebook's Changes page says about its records (README.md, "Changes"; requirement, 2026-10-01): what made each request, and what each setup revision changed from the one before it. Pure: the service reads the
immutable rows and hands them here. Nothing here writes, and nothing decides a score."""
from . import setup as setups
from .common import points_text

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MODES = {"points": "total points", "weighted": "weighted categories"}
UNGRADED = {"exclude": "empty cells left out", "zero": "empty cells counted as zero", "due": "empty cells counted as zero once due"}
AGGREGATES = {"points": "total points", "equal": "equal weight per column"}
ROUNDING = {None: "the exact percentage", 0: "the percentage rounded to a whole percent", 1: "the percentage rounded to one decimal",
            2: "the percentage rounded to two decimals"}
PARTS = {"frq": "written work", "bubble": "bubble sheets", "exams": "the Exams page"}


def _posting(category):
    """How a category's grades are posted in Canvas (POSTING GRADES), in the Changes page's words."""
    posting = category.get("posting") or {}
    rules = [words for key, words in (("due", "after its due date"), ("certified", "when certified")) if posting.get(key)]
    return "posted in Canvas " + " and ".join(rules) if rules else "posted in Canvas by hand"


def origin(kind, request_id, summary):
    """What made a request: `typed` (Faculty's grid, a dialog or a bulk mark), `absence` (an excused absence's iClicker credit,
    summary `source: "absence"`, docs/decisions/absences.md ICLICKER-CREDIT), `iclicker_credit` (an iClicker credit given before
    the import, docs/decisions/iclicker.md CREDIT BEFORE THE IMPORT), `file`, `canvas` or `iclicker` (an import, by its
    summary's `source`), `pull`, or `certification` (recorded in a certification's own transaction, request ID `certify:…`; a
    later Pull of a certified plan is a `pull`)."""
    if kind == "scores":
        return summary["source"] if summary.get("source") in ("absence", "iclicker_credit") else "typed"
    if kind == "import":
        return summary.get("source") if summary.get("source") in ("canvas", "iclicker") else "file"
    if kind == "pull":
        return "certification" if request_id.startswith("certify:") else "pull"
    return kind


def _date(value):
    """"2026-10-08" as "Oct 8, 2026" (the date written, no time zone); "" as "none"."""
    try:
        year, month, day = (int(part) for part in value.split("-"))
        return f"{MONTHS[month - 1]} {day}, {year}"
    except (AttributeError, ValueError, IndexError):
        return "none"


def _points(value):
    try:
        return points_text(value)
    except (ArithmeticError, TypeError, ValueError):
        return str(value)


def _quoted(text):
    return f"“{text}”"


def _parts(link):
    return ", ".join(PARTS.get(part, part) for part in (link or {}).get("parts") or [])


def _reordered(before, after):
    """Whether the entries both lists keep are in another order (an added or removed entry alone reorders nothing)."""
    old, new = {entry["id"] for entry in before}, {entry["id"] for entry in after}
    return [entry["id"] for entry in before if entry["id"] in new] != [entry["id"] for entry in after if entry["id"] in old]


def _category_changes(before, after, mode):
    lines = []
    old = {category["id"]: category for category in before}
    new = {category["id"]: category for category in after}
    for category in after:
        prior = old.get(category["id"])
        name = _quoted(category["name"])
        if prior is None:
            extra = [f"weight {_points(category.get('weight', '0'))}" if mode == "weighted" else "",
                     f"drops the lowest {category['drop_lowest']}" if category.get("drop_lowest") else "",
                     AGGREGATES["equal"] if category.get("aggregate") == "equal" else "",
                     _posting(category) if category.get("posting") else ""]
            extra = [text for text in extra if text]
            lines.append(f"Added category {name}" + (f" ({', '.join(extra)})" if extra else ""))
            continue
        if prior["name"] != category["name"]:
            lines.append(f"Renamed category {_quoted(prior['name'])} to {name}")
        changed = []
        if _points(prior.get("weight", "0")) != _points(category.get("weight", "0")):
            changed.append(f"weight {_points(prior.get('weight', '0'))} → {_points(category.get('weight', '0'))}")
        if prior.get("drop_lowest", 0) != category.get("drop_lowest", 0):
            changed.append(f"drops the lowest {prior.get('drop_lowest', 0)} → {category.get('drop_lowest', 0)}")
        if prior.get("aggregate", "points") != category.get("aggregate", "points"):
            changed.append(f"{AGGREGATES.get(prior.get('aggregate'), prior.get('aggregate'))} → "
                           f"{AGGREGATES.get(category.get('aggregate'), category.get('aggregate'))}")
        if _posting(prior) != _posting(category):
            changed.append(f"{_posting(prior)} → {_posting(category).removeprefix('posted in Canvas ')}")
        if changed:
            lines.append(f"{name}: {'; '.join(changed)}")
    lines += [f"Removed category {_quoted(category['name'])}" for category in before if category["id"] not in new]
    if _reordered(before, after):
        lines.append("Categories reordered")
    return lines


def _item_changes(before, after, categories, sections, assignments):
    lines = []
    old = {item["id"]: item for item in before}
    new = {item["id"]: item for item in after}

    def category(category_id):
        return categories.get(category_id, "a removed category") if category_id else "Uncategorized"

    def where(ids):
        numbers = sorted(str(sections.get(section_id, "?")) for section_id in ids or [])
        return f"section{'s' if len(numbers) > 1 else ''} {', '.join(numbers)}" if numbers else "every section"

    def linked(item):
        return (item.get("link") or {}).get("kind") == "grading"

    def link(item):
        return f"pulls {_quoted(assignments.get(item.get('assignment_id'), 'an assignment'))} ({_parts(item['link'])})"

    for item in after:
        prior = old.get(item["id"])
        title = _quoted(item["title"])
        if prior is None:
            extra = [f"{_points(item['points'])} points", category(item.get("category_id"))]
            if item.get("due_date"):
                extra.append(f"due {_date(item['due_date'])}")
            if item.get("sections"):
                extra.append(where(item["sections"]))
            if linked(item):
                extra.append(link(item))
            if item.get("extra_credit"):
                extra.append("extra credit")
            if item.get("counts") is False:
                extra.append("not counted")
            if item.get("replaces_lowest"):
                extra.append("replaces the lowest score if higher")
            if item.get("archived"):
                extra.append("archived")
            lines.append(f"Added column {title} ({', '.join(extra)})")
            continue
        if prior["title"] != item["title"]:
            lines.append(f"Renamed column {_quoted(prior['title'])} to {title}")
        changed = []
        if _points(prior["points"]) != _points(item["points"]):
            changed.append(f"points {_points(prior['points'])} → {_points(item['points'])}")
        if prior.get("category_id") != item.get("category_id"):
            changed.append(f"category {category(prior.get('category_id'))} → {category(item.get('category_id'))}")
        if (prior.get("due_date") or "") != (item.get("due_date") or ""):
            changed.append(f"due {_date(prior.get('due_date') or '')} → {_date(item.get('due_date') or '')}")
        if sorted(prior.get("sections") or []) != sorted(item.get("sections") or []):
            changed.append(f"now for {where(item.get('sections'))}")
        if (prior.get("assignment_id"), prior.get("link")) != (item.get("assignment_id"), item.get("link")):
            if linked(item):
                changed.append(link(item))
            elif linked(prior):
                changed.append("no longer linked to grading")
            elif item.get("assignment_id"):
                changed.append(f"references {_quoted(assignments.get(item['assignment_id'], 'an assignment'))}")
            else:
                changed.append("references no assignment")
        if prior.get("counts", True) != item.get("counts", True):
            changed.append("counted again" if item.get("counts", True) else "no longer counted")
        if bool(prior.get("extra_credit")) != bool(item.get("extra_credit")):
            changed.append("now extra credit" if item.get("extra_credit") else "no longer extra credit")
        if bool(prior.get("replaces_lowest")) != bool(item.get("replaces_lowest")):
            changed.append("now replaces the lowest score if higher" if item.get("replaces_lowest") else "no longer a replacement")
        if (prior.get("notes") or "") != (item.get("notes") or ""):
            changed.append("notes edited")
        if bool(prior.get("archived")) != bool(item.get("archived")):
            changed.append("archived" if item.get("archived") else "restored")
        if changed:
            lines.append(f"{title}: {'; '.join(changed)}")
    lines += [f"Removed column {_quoted(item['title'])}" for item in before if item["id"] not in new]
    if _reordered(before, after):
        lines.append("Columns reordered")
    return lines


def _scale_text(scale):
    return ", ".join(f"{entry['letter']} {_points(entry['min'])}" for entry in scale)


def setup_changes(before, after, *, sections=None, assignments=None, made_at=None):
    """What setup revision `after` changed from `before` (None: the first revision, compared with the default setup in force when
    it was made, `made_at`), as instructor-readable lines in the setup's own order: how it counts, the categories, the columns,
    then the letter scale. `sections` maps section IDs to numbers and `assignments` assignment IDs to titles (both only for words)."""
    before = before or setups.default_setup(made_at)
    sections, assignments = sections or {}, assignments or {}
    lines = []
    if before.get("mode", "points") != after.get("mode", "points"):
        lines.append(f"Counting: {MODES.get(before.get('mode'), before.get('mode'))} → {MODES.get(after.get('mode'), after.get('mode'))}")
    if before.get("ungraded", "exclude") != after.get("ungraded", "exclude"):
        lines.append(f"Empty cells: {UNGRADED.get(before.get('ungraded'), before.get('ungraded'))} → "
                     f"{UNGRADED.get(after.get('ungraded'), after.get('ungraded'))}")
    if before.get("letter_rounding") != after.get("letter_rounding"):
        lines.append(f"Letters decided by {ROUNDING.get(after.get('letter_rounding'), after.get('letter_rounding'))} "
                     f"(was {ROUNDING.get(before.get('letter_rounding'), before.get('letter_rounding'))})")
    lines += _category_changes(before.get("categories", []), after.get("categories", []), after.get("mode", "points"))
    names = {category["id"]: category["name"] for category in [*before.get("categories", []), *after.get("categories", [])]}
    lines += _item_changes(before.get("items", []), after.get("items", []), names, sections, assignments)
    old_scale, new_scale = before.get("scale") or [], after.get("scale") or []
    if [(e["letter"], _points(e["min"])) for e in old_scale] != [(e["letter"], _points(e["min"])) for e in new_scale]:
        if [e["letter"] for e in old_scale] == [e["letter"] for e in new_scale]:
            moved = [f"{new['letter']} {_points(old['min'])} → {_points(new['min'])}" for old, new in zip(old_scale, new_scale)
                     if _points(old["min"]) != _points(new["min"])]
            lines.append(f"Grading scheme: {'; '.join(moved)}")
        else:
            lines.append(f"Grading scheme: {_scale_text(old_scale)} → {_scale_text(new_scale)}")
    return lines
