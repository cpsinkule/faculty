"""iClicker credit given before the session is imported (docs/decisions/iclicker.md CREDIT BEFORE THE IMPORT; instructor,
2026-10-21: students who cannot open iClicker email a picture of their ID and the projected question before their class ends,
"so followup hasn't had time to actually import that clicker question yet to queue up a grade change for it, so it queues it up
as a to-do item for me. Is there anyway we can fix this so that it can handle this without me needing to manually go in and
change it after the fact"). Pure, like `excused_credit`, whose `plan` it shares: the gradebook service reads the rows, hands them
here and writes what `plan` returns as ordinary score revisions, each credit's batch one request whose summary says
`source: "iclicker_credit"`, the credit and the action (`absence_id` in a write names the credit).

- COVERED: a poll of the iClicker course linked to the credit's SECTION, whose recorded campus date is the credit's class date,
  and whose scores go to a column the section's absences could credit (`excused_credit.column`). A credit whose latest revision
  is `waiting` covers; a `withdrawn` one does not.
- FILL and PUT BACK as for an excused absence: full points (source `instructor`, so an import keeps it), nothing written when the
  cell already holds full points or the credit's fill was changed since; a withdrawn credit's fill gets back what it replaced.
"""
from .common import row_key
from .excused_credit import column

RULE = "iclicker-credit-v1"
MAX_REASON = 200


def note(credit):
    """What a filled cell says: why the instructor gave the credit, as the card said it."""
    return f"{credit['reason']}: full credit."


def covered(credits, courses, sessions, items):
    """{(item_id, row): credit} of every cell a waiting credit covers. `credits` [{credit_id, section_id, enrollment_id,
    class_date, state, reason}] (each credit's latest revision); `courses` {section_id: iClicker course}; `sessions`
    [{course_id, date, item_id}]; `items` {item_id: setup item}. Two credits of one cell: the earlier class date, then ID, names it."""
    found = {}
    for credit in sorted(credits, key=lambda c: (c["class_date"], c["credit_id"])):
        course = courses.get(credit["section_id"])
        if credit["state"] != "waiting" or not course:
            continue
        for session in sessions:
            if session["course_id"] == course and session["date"] == credit["class_date"] and column(items, session["item_id"], credit["section_id"]):
                found.setdefault((session["item_id"], row_key(credit["section_id"], credit["enrollment_id"])), {**credit, "id": credit["credit_id"]})
    return found
