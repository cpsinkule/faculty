"""SCHEDULING "CLASS SCHEDULE: DR QUIZZES" (requirement, 2026-10-15, answers 3(a), 4(a), 5(a)): a course term's upcoming class-schedule
quizzes to preschedule with the DR center, for each section with a DR quiz taker (a student whose ACCOMMODATIONS "DR QUIZ TAKERS"
flag is on), compared by date with the imported DR bookings. Owner: the course term.

A read only (the one exception "CLASS SCHEDULE: NOT ELSEWHERE" names): catalog only, no lock beyond the read transaction, nothing
outside Faculty. No student name, UIN or enrollment ID leaves it. Send to AIM (`aim_send.start`) confirms against `fingerprint`.

DR DATE (requirement, 2026-10-31): a row's DR date (`dr_quiz_sittings`, recorded by `dr_quiz_sittings.record`) is the day its section's
DR quiz takers sit the quiz at the DR center; it replaces the class day as the row's `date` (`class_date` keeps the class day) and its
time is that section's class meeting that day. A quiz given in class in the last PAST_DAYS days with no DR date ahead stays listed,
not ready, so one can be set for it. A certified quiz is left out unless its section's DR date is today or ahead (instructor,
2026-11-01: Quiz 6, certified after class, left the list and Send to AIM before its Nov 4 DR sitting's copy could be uploaded)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from absences.makeup_slots import QUIZ_MINUTES
from exam_links import canonical

from . import clock
from .class_schedule import TermRead
from .service import _has

PAST_DAYS = 14  # a quiz given in class this many days ago or fewer stays listed so a DR date can be set for it

def _testers(db, sections):
    """{section_id: {enrollment_id}} of the DR quiz takers in each of the term's sections: a current (not dropped) member whose
    ACCOMMODATIONS "DR QUIZ TAKERS" flag is on (the latest `dr_quiz_taker_changes` row; none is off). A DR booking or an Extra Time
    alone never makes one (requirement, 2026-10-15)."""
    found = {}
    if sections and _has(db, "dr_quiz_taker_changes"):
        for section, enrollment in db.execute(f"""SELECT m.section_id, m.enrollment_id FROM roster_current m
                JOIN dr_quiz_taker_changes c ON c.enrollment_id=m.enrollment_id AND c.revision=(SELECT MAX(revision)
                FROM dr_quiz_taker_changes WHERE enrollment_id=m.enrollment_id) WHERE c.taker=1 AND m.section_id IN
                ({",".join("?" * len(sections))})""", sections):
            found.setdefault(section, set()).add(enrollment)
    return found


def _bookings(db, sections):
    """{section_id: [(canonical exam name, linked assignment or None, date)]} of the imported DR bookings."""
    found = {}
    if not sections or not _has(db, "dr_exam_bookings"):
        return found
    linked = _has(db, "dr_exam_assignment_links")
    rows = db.execute(f"""SELECT b.section_id, r.snapshot_json, {"l.assignment_id" if linked else "NULL"} FROM dr_exam_bookings b
        JOIN dr_exam_revisions r ON r.id=b.revision_id {"LEFT JOIN dr_exam_assignment_links l ON l.booking_id=b.id" if linked else ""}
        WHERE b.section_id IN ({",".join("?" * len(sections))})""", sections)
    for section, snapshot, assignment in rows:
        booking = json.loads(snapshot)
        found.setdefault(section, []).append((canonical(booking.get("exam_type")), assignment, booking.get("date") or ""))
    return found


def sittings(db, term_id):
    """{(item, section): date} of each row's latest DR date ('' when cleared)."""
    if not _has(db, "dr_quiz_sittings"):
        return {}
    return {(item, section): date for item, section, date in db.execute("""SELECT item_id, section_id, date FROM dr_quiz_sittings s
        WHERE term_id=? AND id=(SELECT MAX(id) FROM dr_quiz_sittings x WHERE x.term_id=s.term_id AND x.item_id=s.item_id
        AND x.section_id=s.section_id)""", (term_id,))}


def _aim(bookings, title, assignment, date):
    name = canonical(title)
    matched = [day for exam, link, day in bookings if exam == name or (assignment and link == assignment)]
    dates = sorted(set(matched))
    status = "not_in_aim" if not matched else "in_aim" if date and dates == [date] else "aim_differs"
    return {"status": status, "bookings": len(matched), "dates": dates}


def _span(text):
    found = clock.parse_span(text) if text else None
    return (clock.hhmm(found[0]), clock.hhmm(found[1]) if found[1] is not None else "") if found else ("", "")


def term_quizzes(db, term_id, memo=None):
    """{term_id, code, term_label, today, sections, quizzes, fingerprint} (module docstring)."""
    memo = memo or TermRead(db, term_id)
    today = clock.campus_today().isoformat()
    testers = _testers(db, list(memo.sections))
    sections = sorted(({"section_id": section, "section": memo.sections[section], "dr_students": len(people)}
                       for section, people in testers.items() if people), key=lambda entry: entry["section"])
    bookings = _bookings(db, [entry["section_id"] for entry in sections])
    dr_dates = sittings(db, memo.term_id) if sections else {}
    recent = (clock.campus_today() - dt.timedelta(days=PAST_DAYS)).isoformat()
    quizzes = []
    for work in memo.works:
        if not work["plan"] or work["plan"]["kind"] != "quiz":
            continue
        for entry in sections:
            section = entry["section_id"]
            facts = memo.facts(work, section)
            row = facts and facts["row"]
            if not row:
                continue
            linked = bool(row.get("link") and not work["missing"])
            title = (work["title"] if linked else row["title"]) or row["title"]
            own = row["section_values"].get(section, {})
            given = facts["given"] or {"date": "", "time": own.get("time") or row["time"], "place": own.get("place") or row["place"],
                                       "source": "planned"}
            dr_date = dr_dates.get((row["item"], section), "")
            if work["archived"] and not (dr_date and dr_date >= today):
                continue  # certified; a DR date still ahead keeps the row (CERTIFIED WITH A DR DATE AHEAD)
            date = dr_date or given["date"]
            past = bool(date and date < today)
            if past and (dr_date or given["date"] < recent):
                continue
            time, source = ("", "") if dr_date else (given["time"], given["source"] if given["time"] else "")
            start, end = _span(time)
            meeting = (memo.meetings(section)[0].get(date) or [None])[0] if date and not time else None
            if meeting:
                start, end = _span(meeting["time"])[0], _span(meeting["end_time"])[0]
                time, source = f"{start}–{end}" if end else start, "class"
            elif dr_date and given["time"]:
                time, source = given["time"], given["source"]
                start, end = _span(time)
            ready = bool(date and start and not past)
            reason = "" if ready else "Given in class" if past else "No date yet" if not date else "No time yet"
            quizzes.append({"key": f"{row['item']}:{section}", "item": row["item"], "title": title, "assignment_id": work["assignment_id"],
                            "section_id": section, "section": entry["section"], "date": date, "class_date": given["date"], "dr_date": dr_date,
                            "time": time, "start": start, "end": end, "place": given["place"], "time_source": source, "minutes": QUIZ_MINUTES,
                            "ready": ready, "reason": reason, "aim": _aim(bookings.get(section, []), title, work["assignment_id"], date)})
    quizzes.sort(key=lambda quiz: (not quiz["date"], quiz["date"], quiz["section"], quiz["title"].casefold()))
    canonical_json = json.dumps({"sections": sections, "quizzes": quizzes}, sort_keys=True, separators=(",", ":"))
    return {"term_id": memo.term_id, "code": memo.code, "term_label": memo.label, "today": today, "sections": sections,
            "quizzes": quizzes, "fingerprint": hashlib.sha256(canonical_json.encode()).hexdigest()}
