"""Total absences (requirement, 2026-10-31). A READ-ONLY projection of one section
membership, kept nowhere and recomputed on every read of the student's card. Nothing here writes, sends or names a student.

A DAY is the unit: each campus date counts once, whatever says the student missed it.
- CLASS DAYS ONLY (requirement, 2026-10-31): a day
  counts only when it is one of the section's class days (`scheduling.class_schedule.section_meetings`, days off left out),
  whatever names it. While the section has no class days on record, an absence counts every Monday to Friday between its dates.
- RECORDED: each absence of the membership (`service.standing`, its latest revision) but a withdrawn one covers its class days.
  Two absences covering one day are one day.
- ICLICKER: a poll of the iClicker course linked to the section (the latest link), on the campus date its import recorded
  (`gradebook_iclicker_sessions`), whose column (the latest remembered match) is in the setup, not archived, applies to the section
  and is worth more than 0, and whose cell's latest revision is missing or a scored 0. Several polls of one day are that day once,
  and an answered poll that day (any score above 0) means the student was there: that day's iClicker 0s are left out. An empty
  cell (never imported, or a student added later) is no absence; full credit given for the day (an excused absence's or an
  iClicker credit) is no 0.
- QUIZ: an exam record that is a quiz (no seating chart is for its assignment, as MAKEUP BOOKING reads quizzes) listing the section,
  whose column (the setup's, by its assignment, not archived, applying to the section) holds missing or a scored 0 for the student,
  on the day the quiz was given to the student's SECTION (`given_dates`, `own_makeup=False`: a makeup sitting on another day never
  moves it, so a missed makeup is the quiz day once). A 0 on a paper that came back (the Exams page's collection, scans deleted in the
  Inbox left out) is a score, not an absence. Work with no day given on record is take-home work (requirement, 2026-10-31): never counted.
  A dropped score is still the student's 0 in its cell; a makeup taken and scored replaced the 0.

STATE of a day: the best decision of the absences covering it (excused, then waiting for your decision, then not excused); a day only
a 0 shows is not excused ("No absence recorded"). Days after the campus day are `ahead`, not counted."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from .makeups import RECEIVED
from .service import STATUSES, _has, day_text, standing

RULE = "absence-total-v1"
RANK = ("excused", "pending", "not_excused")  # the decision that names a day two absences cover
STATES = {"excused": "Excused", "pending": STATUSES["pending"], "not_excused": STATUSES["not_excused"],
          "unrecorded": "No absence recorded"}


def _zero(cell):
    """Whether a cell's latest revision is a 0 that counts: missing, or scored 0."""
    if not cell:
        return False
    if cell["status"] == "missing":
        return True
    if cell["status"] != "scored":
        return False
    try:
        return Decimal(str(cell["points"])) == 0
    except (InvalidOperation, ValueError):
        return False


def _answered(cell):
    try:
        return bool(cell and cell["status"] == "scored" and Decimal(str(cell["points"])) > 0)
    except (InvalidOperation, ValueError):
        return False


def _cells(db, term_id, section_id, enrollment_id):
    """{item_id: latest revision} of the membership's cells."""
    if not _has(db, "gradebook_scores"):
        return {}
    return {row["item_id"]: dict(row) for row in db.execute("""SELECT g.item_id, g.status, g.points FROM gradebook_scores g
        WHERE g.term_id=? AND g.section_id=? AND g.enrollment_id=? AND g.seq=(SELECT MAX(x.seq) FROM gradebook_scores x
            WHERE x.term_id=g.term_id AND x.item_id=g.item_id AND x.section_id=g.section_id AND x.enrollment_id=g.enrollment_id)""",
        (term_id, section_id, enrollment_id))}


def _counted(item, section_id):
    from gradebook.setup import applies
    if not item or item.get("archived") or not applies(item, section_id):
        return False
    try:
        return Decimal(str(item.get("points") or 0)) > 0
    except (InvalidOperation, ValueError):
        return False


def _class_days(db, term_id, section_id):
    """The section's class days; an empty set when it has none on record."""
    from scheduling.class_schedule import section_meetings
    return set(section_meetings(db, term_id, section_id)[0]) if _has(db, "scheduling_revisions", "scheduling_entries") else set()


def _span(first, last):
    start, stop = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    return [(start + dt.timedelta(days=n)).isoformat() for n in range((stop - start).days + 1)]


def missable_ends(db, term_id, section_id, enrollment_id, first, last):
    """A FOLLOW-UP NAMES CLASS DAYS (requirement, 2026-11-03): `(first, last)` of what the student can miss between ISO `first` and `last`, or
    None when there is nothing. A class day of the section; a day work is given to them, planned or on record (the class schedule's
    `given` rows, and their own sitting by `given_dates`, `own_makeup=False`: an evening exam is on no class day, and a department
    makeup needs its absence to cover it); a weekday past the section's last class, the exam period no class day speaks for. While the
    section has no class days on record, every Monday to Friday. Read only."""
    from exam_workflow.given_dates import given_dates
    from scheduling.class_schedule import section_schedule
    classes, key, given = _class_days(db, term_id, section_id), (section_id, enrollment_id), set()
    if _has(db, "scheduling_revisions", "scheduling_entries"):
        given = {day["date"] for day in section_schedule(db, section_id, first, last)["days"]
                 for work in day["work"] if work["what"] == "given"}
    if _has(db, "exam_events", "exam_event_sections"):
        for event_id, assignment_id in db.execute("""SELECT e.id,e.assignment_id FROM exam_events e JOIN exam_event_sections x ON x.event_id=e.id
                WHERE e.term_id=? AND x.section_id=?""", (term_id, section_id)).fetchall():
            given.add(given_dates(db, event_id, term_id, assignment_id, {key}, own_makeup=False).get(key))
    end = max(classes, default="")
    days = [day for day in _span(first, last) if day in classes or day in given or (
        day > end and dt.date.fromisoformat(day).weekday() < 5)]
    return (days[0], days[-1]) if days else None


def _once(memo, key, read):
    """`read()`, kept in `memo` (one read's dict, or None) under `key`: what every student of a term or section shares."""
    if memo is None:
        return read()
    if key not in memo:
        memo[key] = read()
    return memo[key]


def total(db, exams, term_id, section_id, enrollment_id, *, today=None, memo=None):
    """{total, excused, not_excused, pending, days: [{date, day, state, state_label, sources}], ahead: [...]}
    of one section membership (module docstring). `exams` (ExamService, or None) reads whether a quiz's paper came back; `memo`
    (a dict for one read of many students) keeps what a term's or section's students share. Catalog only."""
    from gradebook.service import GradebookService, _setup
    today = (today or dt.date.today()).isoformat() if not isinstance(today, str) else today
    found = {}  # date -> {"absences": [statuses], "sources": [{kind, label}]}

    def note(date, kind, label, status=None):
        entry = found.setdefault(date, {"absences": [], "sources": []})
        if status:
            entry["absences"].append(status)
        if not any(source["kind"] == kind and source["label"] == label for source in entry["sources"]):
            entry["sources"].append({"kind": kind, "label": label})

    # RECORDED: the class days each standing absence covers.
    met = _once(memo, ("met", section_id), lambda: _class_days(db, term_id, section_id))
    every = _once(memo, ("standing", term_id), lambda: standing(db, term_id))
    for absence in (a for a in every if a["section_id"] == section_id and a["enrollment_id"] == enrollment_id and a["status"] in RANK):
        label = f"Absence {absence['dates']}"
        for date in _span(absence["first_date"], absence["last_date"]):
            if (date in met) if met else dt.date.fromisoformat(date).weekday() < 5:
                note(date, "absence", label, absence["status"])

    setup = _once(memo, ("setup", term_id), lambda: _setup(db, term_id)[0] if _has(db, "gradebook_setups") else {"items": []})
    items = {item["id"]: item for item in setup.get("items", [])}
    cells = _cells(db, term_id, section_id, enrollment_id)

    # ICLICKER: the section's polls, a day once; an answered poll that day means the student was there.
    if _has(db, "gradebook_iclicker_sessions", "gradebook_iclicker_links"):
        course = _once(memo, ("courses", term_id), lambda: GradebookService._iclicker_courses(db, term_id)).get(section_id)
        zeros, there = {}, set()
        for session in _once(memo, ("sessions", term_id), lambda: GradebookService._iclicker_sessions(db, term_id)) if course else ():
            item = items.get(session["item_id"]) if session["course_id"] == course and session["item_id"] else None
            if not _counted(item, section_id):
                continue
            cell = cells.get(item["id"])
            if _answered(cell):
                there.add(session["date"])
            elif _zero(cell):
                zeros.setdefault(session["date"], []).append(item["title"])
        for date, titles in zeros.items():
            if date not in there:
                for title in titles:
                    note(date, "iclicker", f"iClicker 0 · {title}")

    # QUIZ: a 0 with no paper back, on the day the quiz was given to the section.
    if _has(db, "exam_events", "exam_event_sections"):
        from exam_workflow.given_dates import given_dates
        charts = ("AND NOT EXISTS(SELECT 1 FROM seating_charts c WHERE c.term_id=e.term_id AND c.assignment_id=e.assignment_id)"
                  if _has(db, "seating_charts") else "")
        quizzes = _once(memo, ("quizzes", section_id), lambda: [dict(row) for row in db.execute(f"""SELECT e.* FROM exam_events e
            JOIN exam_event_sections x ON x.event_id=e.id AND x.section_id=? WHERE e.term_id=? {charts} ORDER BY e.created_at, e.id""",
            (section_id, term_id))])
        for event in quizzes:
            columns = [item for item in items.values() if item.get("assignment_id") == event["assignment_id"] and _counted(item, section_id)]
            column = next((item for item in columns if _zero(cells.get(item["id"]))), None)
            if not column:
                continue
            given = given_dates(db, event["id"], term_id, event["assignment_id"], {(section_id, enrollment_id)},
                                own_makeup=False).get((section_id, enrollment_id))
            if not given:
                continue  # take-home: no day it was given
            if exams is not None:
                person = exams._participant_paper(db, event, section_id, enrollment_id)
                if person and person["collection"]["status"] in RECEIVED:
                    continue
            note(given, "quiz", f"Quiz 0 · {column['title']}")

    days, ahead = [], []
    for date in sorted(found):
        if met and date not in met:
            continue  # CLASS DAYS ONLY
        entry = found[date]
        state = min(entry["absences"], key=RANK.index) if entry["absences"] else "unrecorded"
        row = {"date": date, "day": day_text(date), "state": state, "state_label": STATES[state],
               "sources": sorted(entry["sources"], key=lambda source: (("absence", "quiz", "iclicker").index(source["kind"]), source["label"]))}
        (ahead if date > today else days).append(row)
    counts = {"excused": 0, "pending": 0, "not_excused": 0}
    for row in days:
        counts["excused" if row["state"] == "excused" else "pending" if row["state"] == "pending" else "not_excused"] += 1
    return {"rule": RULE, "total": len(days), **counts, "days": days, "ahead": ahead}
