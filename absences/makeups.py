"""Makeups owed (requirement, 2026-10-03; ABSENCES "MAKEUPS"): an absence that covers the day an assignment was given means the
student owes a makeup of it. A READ-ONLY projection, kept nowhere: recomputed on every read from

- the absences of a term (`absences.service.standing`, the owner's own reader), excused ones (`owed`) and undecided ones
  (`if_excused`, a heads-up that Home does not count); withdrawn and not excused absences claim nothing;
- each exam record (`exam_events`, one per term assignment: quizzes and exams alike) of the absence's SECTION, and the day it was
  given to the student (EXAMS "DATE GIVEN", `exam_workflow.given_dates` with `own_makeup=False`: a makeup scheduled later never moves
  the day the work was given, so scheduling one does not clear the reminder);
- the student's paper, as the Exams page collects it (`ExamService._participant_paper`, catalog only; scans deleted in the Inbox are
  left out there, so material deleted in the Inbox never counts as received). Instructor's choice: ONLY a received paper clears a
  reminder; there is no dismiss;
- MISSED (requirement, 2026-10-31): the instructor's own "Missed makeup, record
  0" (`absence_makeups_missed`, latest revision `missed`) ends it too, and so does the one Faculty records for a quiz's booking
  card left unanswered (MAKEUP BOOKING › NO ANSWER, THEN A 0, 2026-11-12; actor `automatic`). The student then owes nothing, `covering` no longer claims
  them (Certify gives the 0), and the gradebook counts their empty cell as 0. Undo brings the makeup back.

A dropped student owes nothing. Nothing here writes, or names a student (names are added by the service from the verified roster
payload). The gradebook only READS it (ABSENCES NO-GRADEBOOK, GRADEBOOK ZERO ONCE DUE: `gradebook/blanks.py` keeps an owed
makeup's empty cell from counting as 0)."""
from __future__ import annotations

import json

from exam_workflow.given_dates import given_dates

from .service import STATUSES, _has, dates_text, standing, day_text as _day_text

# A collection status that means a paper of the student came back (anything but no scan, or an absence/excuse with no paper).
RECEIVED = {"complete", "partial", "needs_review", "missing_pages"}
STATES = {"owed": "Makeup owed", "if_excused": "Makeup owed if excused", "scheduled": "Makeup scheduled"}
NO_ANSWER = " · no answer to the booking card"  # added to it when Faculty recorded it for an unanswered card (MAKEUP BOOKING)
MISSED = "Missed makeup: 0"  # the absence window's label of a makeup the instructor closed as missed (not a state of the Makeups view)
CLAIMS = ("excused", "pending")  # the decisions that make a makeup owed (excused) or a heads-up (pending)


def day_text(value):
    return _day_text(value) if value else ""


def owed(db, exams, *, term_ids=None, members=None, absence_id=None, with_missed=False):
    """Each makeup owed in the terms named (default: the current terms), newest given first: one row per (exam record, student),
    the excused absence preferred when an excused and an undecided one both cover the day. `members` limits it to those
    `(section_id, enrollment_id)` memberships; `absence_id` to the work one absence covers. A makeup the instructor closed as
    missed (MISSED) is left out, or, `with_missed`, kept as state "missed" (the absence window, which offers its Undo). Catalog only."""
    if not _has(db, "absences", "absence_revisions", "exam_events"):
        return []
    if term_ids is None:
        term_ids = [row[0] for row in db.execute("SELECT id FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY id")]
    dropped = _dropped(db)
    makeups = _makeup_versions(db)
    closed = missed(db)
    rows = []
    for term_id in term_ids:
        absences = [a for a in standing(db, term_id, members) if a["status"] in CLAIMS and (a["section_id"], a["enrollment_id"]) not in dropped
                    and (absence_id is None or a["id"] == absence_id)]
        if not absences:
            continue
        by_section = {}
        for absence in absences:
            by_section.setdefault(absence["section_id"], []).append(absence)
        for event in db.execute("""SELECT e.*, g.id plan_id FROM exam_events e LEFT JOIN frq_assignments g ON g.event_id=e.id
                WHERE e.term_id=? ORDER BY e.created_at, e.id""" if _has(db, "frq_assignments") else
                "SELECT e.*, NULL plan_id FROM exam_events e WHERE e.term_id=? ORDER BY e.created_at, e.id", (term_id,)).fetchall():
            event = dict(event)
            sections = {row[0] for row in db.execute("SELECT section_id FROM exam_event_sections WHERE event_id=?", (event["id"],))}
            candidates = [a for section in sections for a in by_section.get(section, ())]
            if not candidates:
                continue
            chosen, given = _chosen(db, term_id, event["id"], event["assignment_id"], candidates)
            for (section_id, enrollment_id), absence in chosen.items():
                gone = closed.get((event["id"], section_id, enrollment_id))
                if gone and not with_missed:
                    continue
                person = exams._participant_paper(db, event, section_id, enrollment_id)
                if person and person["collection"]["status"] in RECEIVED:
                    continue
                row = _row(db, event, absence, given[(section_id, enrollment_id)], person, makeups.get(event.get("plan_id"), []))
                if gone:
                    # NO ANSWER, THEN A 0 (2026-11-12): one Faculty recorded for an unanswered booking card says so
                    row.update(state="missed", state_label=MISSED + (NO_ANSWER if gone["actor"] == "automatic" else ""),
                               missed_revision=gone["revision"])
                rows.append(row)
    rows.sort(key=lambda row: (row["title"].casefold(), row["section"], row["enrollment_id"]))
    rows.sort(key=lambda row: row["given_date"], reverse=True)  # stable: newest day first, then title and section
    return rows


def seated(db, exams, *, term_ids, skip=()):
    """SCHEDULED WITHOUT AN ABSENCE (requirement, 2026-10-19): each student on a makeup sitting of the terms' exam records whom no absence claims (`skip`: the (event_id,
    section_id, enrollment_id) of `owed`'s rows), as a "scheduled" row of the Makeups view only. Like an owed row it stands until a
    paper of theirs comes back; a dropped membership shows nothing. Never counted on Home, never a booking card, never read by the
    gradebook: those read `owed`. Catalog only."""
    if not term_ids or not _has(db, "exam_events", "exam_participants", "exam_sittings"):
        return []
    dropped, skip = _dropped(db), set(skip) | set(missed(db))
    makeups = _makeup_versions(db)
    plans = "LEFT JOIN frq_assignments g ON g.event_id=e.id" if _has(db, "frq_assignments") else ""
    rows = []
    for found in db.execute(f"""SELECT p.section_id, p.enrollment_id, e.id event_id, {'g.id' if plans else 'NULL'} plan_id
            FROM exam_participants p JOIN exam_sittings s ON s.id=p.sitting_id AND s.event_id=p.event_id
            JOIN exam_events e ON e.id=p.event_id {plans}
            WHERE s.kind='makeup' AND e.term_id IN ({",".join("?" * len(term_ids))}) ORDER BY e.created_at, e.id, p.enrollment_id""",
            list(term_ids)).fetchall():
        key = (found["event_id"], found["section_id"], found["enrollment_id"])
        if key in skip or key[1:] in dropped:
            continue
        event = {**dict(db.execute("SELECT * FROM exam_events WHERE id=?", (found["event_id"],)).fetchone()), "plan_id": found["plan_id"]}
        person = exams._participant_paper(db, event, found["section_id"], found["enrollment_id"])
        if not person or person["collection"]["status"] in RECEIVED:
            continue
        absence = {"id": "", "section_id": found["section_id"], "enrollment_id": found["enrollment_id"], "first_date": "", "last_date": "",
                   "status": ""}
        row = _row(db, event, absence, None, person, makeups.get(found["plan_id"], []))
        given = given_dates(db, event["id"], event["term_id"], event["assignment_id"], {key[1:]}, own_makeup=False).get(key[1:])
        row.update(given_date=given, given_day=day_text(given), dates="", status_label="No absence", state="scheduled",
                   state_label=STATES["scheduled"])
        rows.append(row)
    return rows


def covering(db, term_id, event_id, assignment_id, members):
    """{(section_id, enrollment_id): {absence_id, status, state, dates, given_date}} of the `members` of one exam record whose
    excused or undecided absence covers the day its work was given to them and who did not miss its makeup (MISSED), chosen as
    `owed` chooses (the excused preferred) but WITHOUT reading papers: Certification and Export asks it only of students it already knows have no work (GRADING
    CERTIFICATION "MAKEUP OWED"), and its "changed" check asks whether a left-out student's makeup is still claimed. Dropped
    memberships owe nothing. Catalog only."""
    if not members or not _has(db, "absences", "absence_revisions", "exam_events"):
        return {}
    dropped = _dropped(db) | {key[1:] for key in missed(db, event_id)}
    sections = {row[0] for row in db.execute("SELECT section_id FROM exam_event_sections WHERE event_id=?", (event_id,))}
    candidates = [a for a in standing(db, term_id, list(members)) if a["status"] in CLAIMS and a["section_id"] in sections
                  and (a["section_id"], a["enrollment_id"]) not in dropped]
    chosen, given = _chosen(db, term_id, event_id, assignment_id, candidates)
    return {key: {"absence_id": a["id"], "status": a["status"], "state": "owed" if a["status"] == "excused" else "if_excused",
                  "dates": a["dates"], "given_date": given[key]} for key, a in chosen.items()}


def _chosen(db, term_id, event_id, assignment_id, candidates):
    """`({membership: absence}, given)`: of `candidates` (claiming absences of the exam record's sections), the one covering the day
    the work was given to each student (EXAMS "DATE GIVEN", ignoring their own makeup sitting), the excused preferred."""
    if not candidates:
        return {}, {}
    given = given_dates(db, event_id, term_id, assignment_id, {(a["section_id"], a["enrollment_id"]) for a in candidates}, own_makeup=False)
    chosen = {}
    for absence in candidates:
        key = (absence["section_id"], absence["enrollment_id"])
        day = given.get(key)
        if not day or not absence["first_date"] <= day <= absence["last_date"]:
            continue
        if key not in chosen or (chosen[key]["status"] != "excused" and absence["status"] == "excused"):
            chosen[key] = absence
    return chosen, given


def missed(db, event_id=None):
    """{(event_id, section_id, enrollment_id): latest row} of the makeups the instructor closed as missed (MISSED): their latest
    revision says `missed`. `event_id` limits it to one exam record. Catalog only."""
    if not _has(db, "absence_makeups_missed"):
        return {}
    where, args = ("AND m.event_id=?", (event_id,)) if event_id else ("", ())
    return {(row["event_id"], row["section_id"], row["enrollment_id"]): dict(row) for row in db.execute(f"""SELECT m.* FROM absence_makeups_missed m
        WHERE m.revision=(SELECT MAX(revision) FROM absence_makeups_missed x WHERE x.event_id=m.event_id AND x.section_id=m.section_id
        AND x.enrollment_id=m.enrollment_id) AND m.missed=1 {where}""", args)}


def _dropped(db):
    view = db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name='roster_dropped'").fetchone()
    return {(row[0], row[1]) for row in db.execute("SELECT section_id, enrollment_id FROM roster_dropped")} if view else set()


def _row(db, event, absence, day, person, makeup_versions):
    section = db.execute("SELECT number FROM course_sections WHERE id=?", (absence["section_id"],)).fetchone()
    scheduled = None
    if person and person.get("sitting_id"):
        sitting = db.execute("SELECT id, label, kind, date, time, location FROM exam_sittings WHERE id=? AND event_id=?",
                             (person["sitting_id"], event["id"])).fetchone()
        if sitting and sitting["kind"] == "makeup":
            scheduled = {"sitting_id": sitting["id"], "label": sitting["label"], "date": sitting["date"], "day": day_text(sitting["date"]),
                         "time": sitting["time"], "location": sitting["location"]}
    state = "owed" if absence["status"] == "excused" else "if_excused"
    status = STATUSES.get(absence["status"], "")
    return {"absence_id": absence["id"], "term_id": event["term_id"], "section_id": absence["section_id"], "enrollment_id": absence["enrollment_id"],
            "section": section["number"] if section else "", "event_id": event["id"], "assignment_id": event["assignment_id"],
            "plan_id": event.get("plan_id"), "title": event["title"], "given_date": day, "given_day": day_text(day),
            "first_date": absence["first_date"], "last_date": absence["last_date"], "dates": dates_text(absence["first_date"], absence["last_date"]) if absence["first_date"] else "",
            "status": absence["status"], "status_label": status, "state": state, "state_label": STATES[state],
            "participant": bool(person), "participant_version": person["version"] if person else None,
            "paper": person["collection"]["status"] if person else None, "scheduled": scheduled, "makeup_versions": makeup_versions}


def _makeup_versions(db):
    """{grading plan: [{letter, approval_id, draft_id}]} of the makeup versions approved into each plan (GRADING "MAKEUP VERSIONS")."""
    if not _has(db, "quiz_draft_approvals", "quiz_drafts"):
        return {}
    found = {}
    # A makeup draft is a quiz draft whose request names its plan (GRADING "MAKEUP VERSIONS").
    for row in db.execute("""SELECT json_extract(d.input_json,'$.makeup.plan_id') plan_id, a.id, a.draft_id, a.result_json
            FROM quiz_draft_approvals a JOIN quiz_drafts d ON d.id=a.draft_id
            WHERE a.status='complete' AND json_extract(d.input_json,'$.makeup') IS NOT NULL ORDER BY a.created_at, a.id"""):
        letter = (json.loads(row["result_json"] or "{}")).get("letter")
        if letter:
            found.setdefault(row["plan_id"], []).append({"letter": letter, "approval_id": row["id"], "draft_id": row["draft_id"]})
    return found


def count_owed(db, exams):
    """How many makeups of current terms are owed for EXCUSED absences (Home's Needs you and the sidebar badge)."""
    return sum(1 for row in owed(db, exams) if row["state"] == "owed")
