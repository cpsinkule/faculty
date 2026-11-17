"""Full credit for the iClicker sessions an excused absence covers (requirement, 2026-10-01). Pure: the gradebook service reads the
rows, hands them here and writes what `plan` returns as ordinary score revisions, each batch one request whose summary says
`source: "absence"`, the absence and the action.

- COVERED: a session of the iClicker course linked to the absence's SECTION (`iclicker_course_links`, latest), whose recorded campus
  date (`gradebook_iclicker_sessions`, latest) falls within the dates of an EXCUSED absence of that section membership, and whose
  scores go to a column (`gradebook_iclicker_links`, latest) that is in the setup, not archived, applies to the section and is worth
  more than 0. Only iClicker columns: no other column is ever read or written for an absence.
- FILL: a covered cell gets the column's full points (iClicker columns are 10) with a note naming the absence, source `instructor`
  (the instructor's excuse: an import or a pull keeps it unless Replace is ticked, `common.change_action`). Nothing is written when
  the cell already holds the full points or more, or when an absence filled it before and it was changed after that (typed in
  Faculty, or an import with Replace ticked): what was changed by hand stays.
- PUT BACK: a cell whose latest revision is an absence's fill that no excused absence covers any more (the decision changed, the
  dates moved, the session went to another column) gets back exactly the revision the fill replaced (status, points, note,
  source), or, when the fill was the cell's first value, becomes empty again as an import would leave it (cleared, source
  `import`, so the next import fills it).
"""
from decimal import Decimal

from . import setup as setups
from .common import points_text, row_key

RULE = "absence-iclicker-v1"
FILL, PUT_BACK = "fill", "put_back"


def note(dates):
    """What a filled cell says: which absence gave the credit (its dates, never its reason)."""
    return f"Excused absence ({dates}): full credit."


def column(items, item_id, section_id):
    """The setup's column a session's scores go to, when an absence may write it for that section; else None."""
    item = items.get(item_id) if item_id else None
    if not item or item.get("archived") or not setups.applies(item, section_id):
        return None
    try:
        return item if Decimal(str(item.get("points") or 0)) > 0 else None
    except ArithmeticError:
        return None


def covered(absences, courses, sessions, items):
    """{(item_id, row): absence} of every cell an excused absence covers. `absences` [{id, section_id, enrollment_id, status,
    first_date, last_date, dates}] (any decision; only `excused` ones cover); `courses` {section_id: iClicker course};
    `sessions` [{course_id, date, item_id}]; `items` {item_id: setup item}. When two absences cover one cell, the one that
    starts first (then by ID) names it."""
    found = {}
    for absence in sorted(absences, key=lambda a: (a["first_date"], a["id"])):
        course = courses.get(absence["section_id"])
        if absence["status"] != "excused" or not course:
            continue
        for session in sessions:
            if session["course_id"] != course or not absence["first_date"] <= session["date"] <= absence["last_date"]:
                continue
            if column(items, session["item_id"], absence["section_id"]):
                found.setdefault((session["item_id"], row_key(absence["section_id"], absence["enrollment_id"])), absence)
    return found


def plan(desired, history, items, noted=None):
    """The writes that bring the cells in line with `desired` (`covered`): [{action, absence_id, item_id, row, cell, value}],
    `cell` the revision written over (None for a first value), `value` {status, points, note, source}. `history`
    {(item_id, row): [revision, …]} oldest first, each {id, status, points, note, source, absence, action} (`absence`/`action`
    set on an absence's own writes) of every cell an absence ever wrote and every desired cell that has a value. `noted` (what
    covers a cell -> its note; default the absence's `note`) lets an iClicker credit plan the same way (iclicker_credit.py)."""
    writes = []
    for key in sorted(set(desired) | {key for key, revisions in history.items() if any(r["action"] for r in revisions)}):
        item_id, row = key
        revisions = history.get(key, [])
        latest = revisions[-1] if revisions else None
        if not column(items, item_id, row.rsplit(":", 1)[0]):
            continue  # archived, moved off the section or gone: an absence never writes it
        if latest and latest["action"] == FILL:
            if key in desired:
                continue
            before = revisions[-2] if len(revisions) > 1 else None
            value = ({"status": before["status"], "points": before["points"], "note": before["note"], "source": before["source"]} if before
                     else {"status": "cleared", "points": None, "note": "", "source": "import"})
            writes.append({"action": PUT_BACK, "absence_id": latest["absence"], "item_id": item_id, "row": row, "cell": latest, "value": value})
            continue
        if key not in desired:
            continue
        last = next((r for r in reversed(revisions) if r["action"]), None)
        if last and last["action"] == FILL:
            continue  # filled once, changed since: that change stands
        full = items[item_id]["points"]
        if latest and latest["status"] == "scored" and Decimal(latest["points"]) >= Decimal(str(full)):
            continue
        absence = desired[key]
        writes.append({"action": FILL, "absence_id": absence["id"], "item_id": item_id, "row": row, "cell": latest,
                       "value": {"status": "scored", "points": points_text(full), "note": (noted or (lambda a: note(a["dates"])))(absence),
                                 "source": "instructor"}})
    return writes
