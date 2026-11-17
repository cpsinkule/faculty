"""Scores imported from iClicker (README.md, "Importing from iClicker"; requirement, 2026-09-28). Pure: the iClicker service reads
each linked course's roster and polling sessions; this module turns them into what the Canvas planner reads (`canvas_import.plan`
with the `ICLICKER` source), so matching, remembering, conflicts, the grade check and the apply are Canvas's, decided once.

What differs from Canvas:
- A column is one POLL SESSION (an iClicker "activity" of type POLL; requirement, 2026-10-06),
  keyed "<iClicker course>:<session>" and remembered by that ID (`gradebook_iclicker_links`). Its value is the student's total
  points in the session (participation plus performance), as iClicker's own gradebook counts them: a score the instructor
  changed by hand there counts as changed (`points`).
- A session belongs to ONE section's course, so a new column applies only to the sections linked to its course, and a column
  matched by title that is limited to other sections is widened to this section (`Source.per_section`). Sessions of several
  courses with the same title and points become one column, as Canvas's same-titled assignments do.
- EVERY SESSION IS WORTH 10 POINTS AND BELONGS IN CHECKS FOR UNDERSTANDING (instructor, the same day: "iclickers are 10 points each
  and they are always in the checks for understanding category"): a new column is worth 10 and goes in the term's Checks for
  Understanding category (the one named so, "Category" or not; else a new "Checks for Understanding Category", the Canvas
  assignment group's name, so that Sync with Canvas makes its Canvas assignment in that group). A score above 10 is recorded as
  iClicker has it, with a warning.
- A student who never joined a session has no iClicker record there: `missing` only when the instructor ticks it, else nothing.
- Who is who: the iClicker student ID when it is a nine-digit UIN of this term's roster; else the iClicker account's email when it
  equals exactly one roster email of the term (case-insensitive; "@email.tamu.edu" and "@tamu.edu" are one mailbox, `mailbox`);
  else not matched (listed by name, never guessed). The plan's
  JSON never carries a UIN or an email.
- New columns are not given a due date (a session is a class meeting, not a deadline for the Calendar); the session's date shows
  in the preview and makes repeated session names distinct.
"""
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal

from gmail_mail.service import variants

from . import canvas_import
from .canvas_import import CAMPUS, MAX_TITLE, NINE, Source
from .imports import collapse, fold

RULE = "gradebook-iclicker-v6"  # v3: TAMU's two student email domains match as one mailbox; v4: others' values kept; v5: Poll sessions only; v6: hand changes count
KEY = re.compile(r"[A-Za-z0-9-]{1,64}:[A-Za-z0-9-]{1,64}")
SCORED = {"POLL"}  # the activity type iClicker gives a poll (read on the real site, Section 507, 2026-10-06); nothing else is a score
POINTS = "10"  # 
CATEGORY = "Checks for Understanding Category"  # the Canvas assignment group's name; "Checks for Understanding" matches too
CATEGORY_NAMES = {"checks for understanding category", "checks for understanding"}


# One TAMU mailbox, two domains (requirement, 2026-10-02): the registrar's roster says "x@email.tamu.edu", the iClicker account
# Canvas synced says "x@tamu.edu", and 14 students of Section 507 with no UIN in iClicker went unmatched.
def mailbox(email):
    """The key an email matches by: Mail's rule (`gmail_mail.service.variants`), case-insensitive, TAMU's two domains one."""
    return variants(email)[0]


def scored(session):
    """Whether a session is a poll, the only kind imported (requirement, 2026-10-06)."""
    return str(session.get("type") or "").upper() in SCORED


def points(user):
    """A student's points in one session as iClicker's gradebook shows them (`users` of `iclicker.web.Client.sessions`), or None.
    HAND CHANGES COUNT (requirement, 2026-10-08): a changed score is a record with adjusted participation and/or performance points and no total (read on
    the real site, Section 507, the same day), and each adjusted part stands in for its original, as iClicker's own pages read an
    adjusted attendance."""
    adjusted = (user.get("adjusted_participation"), user.get("adjusted_performance"))
    if any(part is not None for part in adjusted):
        parts = [mine if mine is not None else original for mine, original in zip(adjusted, (user["participation"], user["performance"]))]
        return sum(part or 0 for part in parts)
    if user["total"] is None and (user["participation"] is not None or user["performance"] is not None):
        return (user["participation"] or 0) + (user["performance"] or 0)
    return user["total"]


def session_key(course_id, session_id):
    return f"{course_id}:{session_id}"


def _instant(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(CAMPUS)
    except ValueError:
        return None


def category_name(setup):
    """The term's Checks for Understanding category (the one of those names), else the name a new one takes."""
    for category in setup.get("categories", []):
        if fold(category["name"]) in CATEGORY_NAMES:
            return category["name"]
    return CATEGORY


def _title(session, repeated):
    name = collapse(session["name"]) or "iClicker session"
    when = _instant(session["started"]) if session.get("started") else None
    if repeated.get(name.casefold(), 0) > 1 and when:
        name = f"{name} · {when.strftime('%b')} {when.day}"
    return name[:MAX_TITLE]


def session_dates(iclicker):
    """{(course, session): campus date} of the sessions an import makes columns of (polls only, none still running): what the
    import records (`gradebook_iclicker_sessions`) so that an excused absence finds the sessions within its dates
    (gradebook/excused_credit.py)."""
    dates = {}
    for course_id, read in iclicker.items():
        for session in read["sessions"]:
            when = _instant(session["started"]) if session.get("started") else None
            if when and session["ended"] and scored(session):
                dates[(course_id, session["id"])] = when.date().isoformat()
    return dates


def columns_of(data, courses):
    """The sessions of the linked courses, course by course in iClicker's order (the prepared `columns`, see `shape`)."""
    columns = []
    for course_id in sorted(data, key=canvas_import.course_order):
        columns.extend(data[course_id]["columns"])
    return columns, 0


ICLICKER = Source("iClicker", "session", RULE, KEY, columns_of, "a session the student did not join", per_section=True, unlinked=False,
                  own="iclicker")


def shape(iclicker, courses, term_students, category=CATEGORY, missing_on=None):
    """iClicker's read ({course: {"roster", "sessions"}}) as the planner reads a source: per course its `columns`, `students`
    ({user_id, sis, state, name}) and `submissions`. Returns (data, facts) where facts name what the plan cannot: sessions left
    out and students matched by email or not matched at all. A roster student who did not join a session is `missing` there
    (recorded only with the plan's tick); `missing_on` (iClicker after class, docs/decisions/iclicker.md AFTER CLASS) limits that
    to the sessions started on those campus dates that someone joined: a student added to the roster later never gets 0s for
    earlier classes, and a session nobody answered makes no column."""
    by_uin = {student["uin"] for student in term_students if student.get("uin")}
    emails = Counter(mailbox(student.get("email")) for student in term_students if student.get("email"))
    by_email = {mailbox(student.get("email")): student["uin"] for student in term_students
                if student.get("email") and student.get("uin") and emails[mailbox(student.get("email"))] == 1}
    data, facts = {}, {"not_polls": 0, "running": 0, "by_email": 0, "id_differs": 0, "unmatched": [], "twice": [], "over": 0}
    for course_id, read in iclicker.items():
        students = []
        seen = Counter()
        for entry in read["roster"]:
            sid, email = entry["student_id"], mailbox(entry["email"])
            name = ", ".join(part for part in (entry["last"], entry["first"]) if part) or entry["email"] or "iClicker student"
            if NINE.fullmatch(sid) and sid in by_uin:
                uin = sid
            elif email and email in by_email:
                uin = by_email[email]
                facts["by_email"] += 1
                facts["id_differs"] += bool(NINE.fullmatch(sid))
            elif NINE.fullmatch(sid):
                uin = sid  # a UIN on no roster here: the planner lists the student by name
            else:
                uin = ""
                if not (entry["removed"] or entry["blocked"]):
                    facts["unmatched"].append({"name": name, "course": courses.get(course_id, {}).get("name", course_id)})
            if uin:
                seen[uin] += 1
                if seen[uin] == 2:
                    facts["twice"].append(name)
            students.append({"user_id": entry["id"], "sis": uin, "state": "removed" if entry["removed"] or entry["blocked"] else "active",
                             "name": name, "section_id": ""})
        sessions = []
        for session in read["sessions"]:
            if not scored(session):
                facts["not_polls"] += 1
            elif not session["ended"]:
                facts["running"] += 1
            else:
                sessions.append(session)
        sessions.sort(key=lambda s: (s["started"] or "", s["id"]))
        repeated = Counter((collapse(s["name"]) or "iClicker session").casefold() for s in sessions)
        columns, submissions = [], []
        roster_ids = {entry["id"] for entry in read["roster"]}
        for session in sessions:
            when = _instant(session["started"]) if session["started"] else None
            columns.append({"key": session_key(course_id, session["id"]), "course_id": course_id,
                            "course": courses.get(course_id, {}).get("name") or course_id, "assignment_id": session["id"],
                            "title": _title(session, repeated), "points": POINTS, "due_date": "", "group": category,
                            "published": True, "date": when.date().isoformat() if when else "", "type": session["type"],
                            "questions": session["questions"]})
            joined = set()
            for u in session["users"]:
                user = u["user_id"] if u["user_id"] in roster_ids else u["enrollment_id"] if u["enrollment_id"] in roster_ids else None
                if user is None or user in joined:
                    continue
                joined.add(user)
                total = points(u)
                facts["over"] += total is not None and Decimal(str(total)) > Decimal(POINTS)
                submissions.append({"assignment_id": session["id"], "user_id": user, "score": total, "excused": False, "missing": False,
                                    "late": False, "state": "graded", "posted": True})
            absent = missing_on is None or bool(joined and when and when.date().isoformat() in missing_on)
            for entry_id in sorted(roster_ids - joined):
                submissions.append({"assignment_id": session["id"], "user_id": entry_id, "score": None, "excused": False, "missing": absent,
                                    "late": False, "state": "unsubmitted", "posted": True})
        data[course_id] = {"columns": columns, "students": students, "submissions": submissions}
    return data, facts


def normalize_options(value):
    return canvas_import.normalize_options(value, ICLICKER)


def plan(iclicker, *, options, students, links, setup, revision, cells, remembered, term_students, term_uins=None, origins=None, missing_on=None):
    """The iClicker ImportPlan of a term's gradebook (the Canvas planner's, with `ICLICKER`). `iclicker` {course: {"roster",
    "sessions"}} (`IClickerService.read_scores`); `term_students` every membership of the term with its UIN and roster email (for
    matching only); `origins` {cell revision id: `changes.origin`}: only a value an iClicker import wrote is updated, one another
    import or a pull wrote is kept unless Replace is ticked (KEEPS OTHERS); the rest as `canvas_import.plan`."""
    courses = {link["course_id"]: {"name": link.get("name") or link["course_id"]} for link in links.values()}
    data, facts = shape(iclicker, courses, term_students, category_name(setup), missing_on)
    found = canvas_import.plan(data, options=options, students=students, links=links, setup=setup, revision=revision, cells=cells,
                               remembered=remembered, term_uins=term_uins, source=ICLICKER, origins=origins)
    warnings = found["warnings"]
    if facts["running"]:
        warnings.append(f"{facts['running']} iClicker session{'s are' if facts['running'] != 1 else ' is'} still running and not imported yet.")
    if facts["by_email"]:
        differs = f" ({facts['id_differs']} typed a student ID that is not their UIN)" if facts["id_differs"] else ""
        warnings.append(f"{facts['by_email']} iClicker student{'s were' if facts['by_email'] != 1 else ' was'} matched by "
                        f"{'their' if facts['by_email'] != 1 else 'the'} email on the roster, not a UIN{differs}.")
    if facts["over"]:
        warnings.append(f"{facts['over']} iClicker score{'s are' if facts['over'] != 1 else ' is'} above a session's {POINTS} points: "
                        "recorded as iClicker has them.")
    if facts["twice"]:
        warnings.append("Two iClicker accounts belong to the same student (only one of them is read): " + ", ".join(sorted(set(facts["twice"]), key=str.casefold)) + ".")
    found["counts"]["not_polls"] = facts["not_polls"]
    found["counts"]["by_email"] = facts["by_email"]
    found["unmatched"] = sorted(facts["unmatched"], key=lambda s: s["name"].casefold())
    found["source"] = "iclicker"
    return found
