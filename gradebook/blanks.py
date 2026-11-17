"""Which empty cells count as 0 under `ungraded: "due"` (requirement, 2026-10-06; GRADEBOOK "ZERO ONCE DUE"): "I want the gradebook
to assign a zero to missing scores unless the student is owed a makeup for that assignment and except when the assignment isn't
due yet".

A READ, recomputed with every result (`compute.compute`'s `blanks`) and kept nowhere: no cell is written, so a moved due date, a
makeup that becomes owed or a score that arrives changes the result by itself. Sync with Canvas sends each counted 0 to Canvas
(canvas_export ZEROS GO TO CANVAS, requirement, 2026-10-09), and takes it out again once the cell stops counting.

- DUE: a column that counts, is not archived, not extra credit and not a replacement. One linked to Faculty's grading is due once
  its plan is certified (the instructor's choice: papers still being graded never read 0); any other once its due date has passed
  on the campus clock (`due_date < today`: due today is not past due, as the grid's "blanks past due" has it). An undated column
  an iClicker import fills is due once the latest of its sessions' campus dates has passed (requirement, 2026-10-07). POSTED (instructor,
  2026-10-08, of a take-home quiz with no due date: "any posted column automatically imputes 0s for missing scores"): a column
  whose grades students see in Canvas (`canvas_posting.posted`: Faculty's latest read of each of its Canvas assignments shows
  them posted by hand, none hidden) is due whatever its date or certification. A column with none of these is never due.
- WAITING: an empty cell of a due column whose assignment has an exam record is left out, with its reason, while the student
  owes a makeup of it (`absences.makeups.owed`: `owed`, or `if_excused` while the absence waits for a decision, the instructor's
  choice), has a DR-center booking for it (the same booking the grid's DR mark shows), or has a paper the Exams page received
  (`makeups.RECEIVED`) that no value records yet: a makeup handed in after certification ends the makeup row before it is graded.
- Every other empty cell of a due column counts 0.
"""
from . import setup as setups, sources

WAITS = ("makeup", "if_excused", "dr", "paper")


def counted(item):
    """A column whose empty cells may count as 0: counted, live, regular (extra credit and a replacement never cost points)."""
    return bool(item.get("counts")) and not item.get("archived") and not item.get("extra_credit") and not item.get("replaces_lowest")


def linked(item):
    return (item.get("link") or {}).get("kind") == "grading" and bool(item.get("assignment_id"))


def due_items(setup, today, certified, held=None, posted=frozenset()):
    """The IDs of the columns whose empty cells count from now on (pure). `today`: the campus date, YYYY-MM-DD; `certified`: the
    linked columns whose plan has a certification; `held`: `session_dates`, for the columns without a due date; `posted`:
    `canvas_posting.posted`, the columns whose grades students see in Canvas."""
    due = set()
    for item in setup.get("items", []):
        if not counted(item):
            continue
        if item["id"] in posted:
            due.add(item["id"])
            continue
        if linked(item):
            if item["id"] in certified:
                due.add(item["id"])
            continue
        date = item.get("due_date") or (held or {}).get(item["id"])
        if date and date < today:
            due.add(item["id"])
    return frozenset(due)


def session_dates(sessions):
    """{item_id: the latest campus date of the iClicker sessions whose scores go to it} (pure; `sessions`: the service's
    `_iclicker_sessions`). The latest, so a column several sections share is due once every section's session has passed."""
    dates = {}
    for session in sessions:
        if session["item_id"] and session["date"] > dates.get(session["item_id"], ""):
            dates[session["item_id"]] = session["date"]
    return dates


def certified_items(db, term_id, setup, every=False):
    """The linked columns whose grading plan has a certification (catalog only: one query, then one per linked column). `every`:
    every live linked column, extra credit and replacements included (POSTING GRADES' "when certified"), not only counted ones."""
    columns = [item for item in setup.get("items", []) if (not item.get("archived") if every else counted(item)) and linked(item)]
    if not columns:
        return frozenset()
    plans = {assignment_id: link.get("plan_id") for assignment_id, link in sources.available(db, term_id).items()}
    return frozenset(item["id"] for item in columns if sources.latest_certification(db, plans.get(item["assignment_id"])))


def waiting(db, exams, term_id, setup, students, cells, due):
    """{(item_id, row): reason} of the empty cells of `due` columns that wait (see the module). Only empty cells of columns whose
    assignment has an exam record are looked at: WebAssign's and Canvas's columns cost nothing. `exams` is the Exams page's
    service; without it (a workspace without grading) such a cell waits as `paper`, since nothing can be checked."""
    columns = [item for item in setup.get("items", []) if item["id"] in due and item.get("assignment_id")]
    if not columns:
        return {}
    events = {row["assignment_id"]: row["id"] for row in db.execute("SELECT id,assignment_id FROM exam_events WHERE term_id=?", (term_id,))}
    candidates = []
    for item in columns:
        if item["assignment_id"] not in events:
            continue
        column = cells.get(item["id"]) or {}
        candidates += [(item, student) for student in students
                       if setups.applies(item, student["section_id"]) and student["row"] not in column]
    if not candidates:
        return {}
    if exams is None:
        return {(item["id"], student["row"]): "paper" for item, student in candidates}
    from absences import makeups
    found = {}
    members = {(student["section_id"], student["enrollment_id"]) for _, student in candidates}
    owed = {(row["section_id"], row["enrollment_id"], row["assignment_id"]): "makeup" if row["state"] == "owed" else "if_excused"
            for row in makeups.owed(db, exams, term_ids=[term_id], members=members)}
    booked = {(row[0], row[1], row[2]) for row in db.execute("""SELECT b.section_id,b.enrollment_id,l.assignment_id FROM dr_exam_bookings b
        JOIN dr_exam_assignment_links l ON l.booking_id=b.id JOIN course_sections s ON s.id=b.section_id WHERE s.term_id=?""", (term_id,))} \
        if _has(db, "dr_exam_bookings", "dr_exam_assignment_links") else set()
    read = {}
    for item, student in candidates:
        key = (student["section_id"], student["enrollment_id"], item["assignment_id"])
        if key in owed:
            found[(item["id"], student["row"])] = owed[key]
        elif key in booked:
            found[(item["id"], student["row"])] = "dr"
        else:
            event_id = events[item["assignment_id"]]
            if event_id not in read:
                read[event_id] = exams._event(db, event_id)
            person = exams._participant_paper(db, read[event_id], student["section_id"], student["enrollment_id"])
            if person and person["collection"]["status"] in makeups.RECEIVED:
                found[(item["id"], student["row"])] = "paper"
    return found


def read(db, exams, term_id, setup, students, cells, today, sessions=()):
    """`compute.compute`'s `blanks` for this setup: `(due, waiting, by_posting)`, or None unless its empty cells count once due.
    `sessions`: the service's `_iclicker_sessions` of the term; `by_posting`: the due columns due only because their grades are
    posted in Canvas (the grid names that as the reason)."""
    if setup.get("ungraded") != "due":
        return None
    from .canvas_posting import posted
    certified, held = certified_items(db, term_id, setup), session_dates(sessions)
    due = due_items(setup, today, certified, held, posted(db, term_id))
    return due, waiting(db, exams, term_id, setup, students, cells, due), due - due_items(setup, today, certified, held)


def _has(db, *tables):
    marks = ",".join("?" * len(tables))
    return db.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ({marks})", tables).fetchone()[0] == len(tables)
