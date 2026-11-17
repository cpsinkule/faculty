"""SCHEDULING "DR QUIZ NOTICES" (requirement, 2026-10-15, answers 1(a), 2(a), 3(a), 4(a), 6): the "Prescheduled in AIM" mark on a DR
quiz row (CLASS SCHEDULE: DR QUIZZES), and the automatic email it starts to each DR quiz taker of the section (a student whose
ACCOMMODATIONS "DR QUIZ TAKERS" flag is on; never one only because of a DR booking or Extra Time) telling them which quizzes are
ready to register for in AIM (the one email AGENTS.md OUTSIDE SERVICES names for this module). Unticking a student's flag sends
nothing: they are simply no longer reached.

The only place that records marks and sends notices (migration `scheduling/004`):
- A MARK (owner the course term; the planned row and section by reference) records the quiz row's date and start as read. The
  instructor marks a quiz after prescheduling it in AIM; Send to AIM records the same mark once AIM lists the date
  (`aim_send.Recorder.mark`). A quiz's mark state is its latest mark row: `marked` while the row is still ready at the marked date
  and start, `changed` once either moved (the mark has lapsed and starts nothing; marking it again sends one "updated" notice when
  its day moved), else `unmarked`.
- A NOTICE (owner the student's enrollment in the section) records that one quiz at one date and start was told to one student, and
  the Mail page's send record that carried it (`gmail_mail_sends.request_id`). It is committed BEFORE the email leaves; a notice that
  has a row is never put in another automatic email, whatever came of its send. A failed or uncertain email waits for Retry.
  The email asks the student to register and tells only each quiz's day: the DR center sets the time and room, the instructor the
  day (requirement, 2026-10-18). So a student told of a quiz on a day is told of it once, whatever its start; only a new day is news.
- The worker's bounded turn (`DrQuizNotices.process_one`) sends one student one email listing every marked upcoming quiz of their
  section they were not told of, through `GmailMailService.preview` and `send` (receipts, Mail › Sent). Nothing reaches AIM."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from request_ids import valid_request_id
from rosters import connect

from . import clock
from .dr_quizzes import _testers, term_quizzes
from .service import ScheduleService, request_digest

LOG = logging.getLogger(__name__)
IDLE = 60  # seconds between the sweep's looks when it found nothing to send
ACTOR = "dr-quiz-notice"
UNCERTAIN = "No answer was recorded for this email; check Gmail's Sent folder before choosing Retry."


# -- Reading -------------------------------------------------------------------------------------------------------------------------
def _marks(db, term_id):
    """{(item, section): latest dr_quiz_marks row} of the term."""
    return {(row["item_id"], row["section_id"]): dict(row) for row in db.execute(
        """SELECT * FROM dr_quiz_marks m WHERE term_id=? AND rowid=(SELECT MAX(x.rowid) FROM dr_quiz_marks x
           WHERE x.term_id=m.term_id AND x.item_id=m.item_id AND x.section_id=m.section_id)""", (term_id,))}


def _state(quiz, mark):
    if not mark or mark["action"] != "mark":
        return "unmarked"
    return "marked" if quiz["ready"] and (quiz["date"], quiz["start"]) == (mark["date"], mark["start"]) else "changed"


def _sends(db, request_ids):
    """{gmail request ID: (status, detail)}: the latest outcome of each send; with no outcome or no send record, uncertain."""
    found = {request: ("uncertain", UNCERTAIN) for request in request_ids}
    for chunk in (list(request_ids)[n:n + 500] for n in range(0, len(request_ids), 500)):
        for request, status, detail in db.execute(f"""SELECT s.request_id, o.status, o.detail FROM gmail_mail_sends s
                LEFT JOIN gmail_mail_outcomes o ON o.id=(SELECT MAX(x.id) FROM gmail_mail_outcomes x WHERE x.send_id=s.id)
                WHERE s.request_id IN ({",".join("?" * len(chunk))})""", chunk):
            if status in ("accepted", "failed"):
                found[request] = (status, "" if status == "accepted" else detail or "")
            else:
                found[request] = ("uncertain", f"{detail} {UNCERTAIN}".strip() if detail else UNCERTAIN)
    return found


def _notices(db, term_id):
    """{(enrollment, section, item, date): the latest attempt's notice row with its send's `status` and `detail`}. Keyed by the day
    alone: the email tells only the day, as the DR center sets the time and room (requirement, 2026-10-18)."""
    rows = [dict(row) for row in db.execute("SELECT * FROM dr_quiz_notices WHERE term_id=? ORDER BY attempt, rowid", (term_id,))]
    sends = _sends(db, {row["request_id"] for row in rows})
    return {(row["enrollment_id"], row["section_id"], row["item_id"], row["date"]):
            {**row, "status": sends[row["request_id"]][0], "detail": sends[row["request_id"]][1]} for row in rows}


def _roster(people):
    return {(person.get("section_id"), person.get("enrollment_id")): person for person in people}


def _skipped(person):
    """Why a DR student cannot be emailed, or "" when they can."""
    if not person:
        return "not on the roster"
    if person.get("dropped"):
        return "dropped"
    return "" if person.get("address") else "no email address on the roster"


def _name(person):
    return (person or {}).get("name") or "Student"


def status(db, term_id, people):
    """{term_id, quizzes: [{key, state, mark, students}]} for every DR quizzes row, in its order. `people` is the Mail page's
    `roster_people()`. Each student is {name, status, detail, batch}: a notice's send status (accepted, failed, uncertain) with its
    email's request ID as `batch`, else `skipped` with why, `waiting` while the quiz is marked, or None. No UIN or enrollment ID."""
    found = term_quizzes(db, term_id)
    testers = _testers(db, [section["section_id"] for section in found["sections"]])
    marks, notices, roster = _marks(db, term_id), _notices(db, term_id), _roster(people)
    quizzes = []
    for quiz in found["quizzes"]:
        section = quiz["section_id"]
        mark = marks.get((quiz["item"], section))
        state = _state(quiz, mark)
        told = mark if state != "unmarked" else None
        students = []
        for enrollment in testers.get(section, ()):
            person = roster.get((section, enrollment))
            notice = told and notices.get((enrollment, section, quiz["item"], told["date"]))
            skipped = _skipped(person)
            if notice:
                entry = {"status": notice["status"], "detail": notice["detail"], "batch": notice["request_id"]}
            elif skipped:
                entry = {"status": "skipped", "detail": skipped, "batch": None}
            else:
                entry = {"status": "waiting" if state == "marked" else None, "detail": "", "batch": None}
            students.append({"name": _name(person), **entry})
        students.sort(key=lambda student: student["name"].casefold())
        quizzes.append({"key": quiz["key"], "state": state, "students": students,
                        "mark": {"date": told["date"], "start": told["start"], "actor": told["actor"], "at": told["created_at"]} if told else None})
    return {"term_id": found["term_id"], "quizzes": quizzes}


# -- Marks ---------------------------------------------------------------------------------------------------------------------------
def _request(data, fields):
    if not isinstance(data, dict) or set(data) - fields:
        raise ValueError(f"Send {', '.join(sorted(fields))}.")
    return valid_request_id(data.get("request_id"))


def _repeat(db, term_id, request_id, digest):
    """True when this request ID already recorded this same request; Conflict when it recorded another."""
    earlier = db.execute("SELECT request_sha256 FROM dr_quiz_marks WHERE term_id=? AND request_id=?", (term_id, request_id)).fetchone()
    if earlier and earlier[0] != digest:
        raise Conflict("This request ID already recorded another change to a DR quiz mark.")
    return bool(earlier)


def _record(db, term_id, item, section, action, date, start, request_id, digest, actor):
    db.execute("""INSERT INTO dr_quiz_marks(id, term_id, item_id, section_id, action, date, start, request_id, request_sha256, actor,
        created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex[:16], term_id, item, section, action, date, start, request_id,
                                                         digest, actor, timestamp()))


def mark(service, term_id, data, actor="workspace"):
    """Mark the DR quiz `key` "Prescheduled in AIM" against the `fingerprint` of the DR quizzes read the caller saw: records the row's
    current date and start, and wakes the notices worker, whose next turn emails the section's DR students. 400 for a key not in the
    read or not ready, 409 for a stale fingerprint; a key already marked records nothing. Exact request IDs as in `link`.
    Send to AIM does not call this function: `aim_send.Recorder.mark` records the same mark with `_record`, with its own actor."""
    request_id = _request(data, {"request_id", "key", "fingerprint"})
    key, fingerprint = data.get("key"), data.get("fingerprint")
    if not isinstance(key, str) or not isinstance(fingerprint, str):
        raise ValueError("Name the quiz by its key, with the fingerprint of the DR quizzes you read.")
    digest = request_digest({"term": term_id, "action": "mark", "key": key, "fingerprint": fingerprint})
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        ScheduleService._term(db, term_id)
        if _repeat(db, term_id, request_id, digest):
            return
        found = term_quizzes(db, term_id)
        quiz = next((quiz for quiz in found["quizzes"] if quiz["key"] == key), None)
        if not quiz:
            raise ValueError("That quiz is not on this term's DR quizzes. Read them again.")
        if fingerprint != found["fingerprint"]:
            raise Conflict("The DR quizzes changed since you read them. Read them again before marking.")
        if not quiz["ready"]:
            raise ValueError(f"{quiz['title']} is not ready to preschedule: {quiz['reason']}.")
        if _state(quiz, _marks(db, term_id).get((quiz["item"], quiz["section_id"]))) == "marked":
            return
        _record(db, term_id, quiz["item"], quiz["section_id"], "mark", quiz["date"], quiz["start"], request_id, digest, actor)
        db.commit()
    service.dr_quiz_notices.wake()


def unmark(service, term_id, data, actor="workspace"):
    """Undo the mark of the DR quiz `key`, only until a notice for it has gone out (409 after). A quiz not marked records nothing."""
    request_id = _request(data, {"request_id", "key"})
    key = data.get("key")
    if not isinstance(key, str) or ":" not in key:
        raise ValueError("Name the quiz by its key.")
    item, _, section = key.partition(":")
    digest = request_digest({"term": term_id, "action": "unmark", "key": key})
    with service.rosters.lock(), connect(service.path) as db:
        db.execute("BEGIN IMMEDIATE")
        ScheduleService._term(db, term_id)
        if _repeat(db, term_id, request_id, digest):
            return
        latest = _marks(db, term_id).get((item, section))
        if not latest or latest["action"] != "mark":
            return
        if db.execute("""SELECT 1 FROM dr_quiz_notices WHERE term_id=? AND item_id=? AND section_id=? AND date=? LIMIT 1""",
                      (term_id, item, section, latest["date"])).fetchone():
            raise Conflict("Students were already emailed about this quiz, so its mark stays.")
        _record(db, term_id, item, section, "unmark", "", "", request_id, digest, actor)
        db.commit()


# -- The email -----------------------------------------------------------------------------------------------------------------------
def _line(quiz, updated):
    # The day only: the DR center sets a DR quiz taker's time and room; the instructor sets the day (requirement, 2026-10-18).
    return f"- {quiz['title']}: {clock.long_day(quiz['date'])}" + (" (new date)" if updated else "")


def _message(code, number, address, lines):
    """The Mail page's {to, subject, body} of one student's notice (answer 6): To them only, no Cc or Bcc, no other student named."""
    body = "\n".join(["Hello,", "", f"These {code} section {number} quizzes are prescheduled with the DR center. Please register for each "
                      "one in AIM, as you do for exams. The DR center sets the time and room.", "", *lines, "", "Professor Sinkule"])
    return {"to": [address], "subject": f"{code} quizzes ready to register at the DR center (AIM)", "body": body}


def _due(db, term_ids, roster, only=None):
    """The first DR student (of `only` = (section, enrollment) when given) on the roster, not dropped, with an address, who has a
    marked upcoming quiz with no notice row at all at its date and start: (term read, person, [(quiz, mark, updated)]), else None."""
    for term_id in term_ids:
        found = term_quizzes(db, term_id)
        testers = _testers(db, [section["section_id"] for section in found["sections"]])
        marks = _marks(db, term_id)
        told = {(row[0], row[1], row[2], row[3]) for row in db.execute(
            "SELECT enrollment_id, section_id, item_id, date FROM dr_quiz_notices WHERE term_id=?", (term_id,))}
        items = {key[:3] for key in told}
        for section in found["sections"]:
            sid = section["section_id"]
            quizzes = [(quiz, marks[(quiz["item"], sid)]) for quiz in found["quizzes"]
                       if quiz["section_id"] == sid and _state(quiz, marks.get((quiz["item"], sid))) == "marked"]
            if not quizzes:
                continue
            for enrollment in sorted(testers.get(sid, ()), key=lambda e: (_name(roster.get((sid, e))).casefold(), e)):
                person = roster.get((sid, enrollment))
                if (only and only != (sid, enrollment)) or _skipped(person):
                    continue
                due = [(quiz, mark, (enrollment, sid, quiz["item"]) in items) for quiz, mark in quizzes
                       if (enrollment, sid, quiz["item"], quiz["date"]) not in told]
                if due:
                    return found, person, due
    return None


def _insert(db, term_id, person, told, request_id, actor):
    """One notice row per (quiz, mark, attempt) told to `person` by the email `request_id`."""
    now = timestamp()
    for quiz, mark, attempt in told:
        db.execute("""INSERT INTO dr_quiz_notices(id, term_id, section_id, enrollment_id, item_id, date, start, mark_id, attempt,
            request_id, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (uuid.uuid4().hex[:16], term_id, quiz["section_id"], person["enrollment_id"], quiz["item"], quiz["date"], quiz["start"],
                    mark["id"], attempt, request_id, actor, now))


class DrQuizNotices:
    """The automatic sweep: a bounded turn of the Inbox worker (teach_inbox.service.work_once), one student's email a turn."""

    def __init__(self, schedule, gmail_mail):
        self.schedule, self.mail = schedule, gmail_mail
        self.rosters, self.path = schedule.rosters, schedule.path
        self._idle_until = 0.0

    def wake(self):
        """A mark was recorded: the next turn looks at once."""
        self._idle_until = 0.0

    def process_one(self):
        """One bounded turn: one student's notice email sent, True; nothing to send (or Gmail not connected), False."""
        if not self.mail.google.connected or time.monotonic() < self._idle_until:
            return False
        try:
            if self._send_one():
                return True
        except Exception:  # noqa: BLE001 - one turn's failure never stops the worker; what was recorded stays (never resent)
            LOG.exception("DR quiz notice turn failed")
        self._idle_until = time.monotonic() + IDLE
        return False

    def _send_one(self):
        with connect(self.path) as db:
            terms = [row[0] for row in db.execute("""SELECT DISTINCT m.term_id FROM dr_quiz_marks m JOIN course_terms t ON t.id=m.term_id
                WHERE t.deleted_at IS NULL ORDER BY m.term_id""")]
        if not terms:
            return False
        roster = _roster(self.mail.roster_people())
        with connect(self.path) as db:
            db.execute("BEGIN")
            found = _due(db, terms, roster)
        if not found:
            return False
        read, person, due = found
        data = _message(read["code"], due[0][0]["section"], person["address"], [_line(quiz, updated) for quiz, _, updated in due])
        preview = self.mail.preview(data)  # raises before anything is recorded
        batch = f"dr-quiz-notice:{uuid.uuid4().hex}"
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            again = _due(db, [read["term_id"]], roster, only=(person["section_id"], person["enrollment_id"]))
            if not again or [(q["key"], q["date"], q["start"]) for q, _, _ in again[2]] != [(q["key"], q["date"], q["start"]) for q, _, _ in due]:
                return True  # changed while the message was built: the next turn reads it again
            _insert(db, read["term_id"], person, [(quiz, mark, 1) for quiz, mark, _ in due], batch, ACTOR)
            db.commit()
        try:
            self.mail.send({**data, "request_id": batch, "fingerprint": preview["fingerprint"], "confirmation": "send"}, actor=ACTOR)
        except Exception:  # noqa: BLE001 - the notices stay recorded and read uncertain; only Retry sends them again
            LOG.exception("DR quiz notice %s was not confirmed sent", batch)
        return True


# -- Retry ---------------------------------------------------------------------------------------------------------------------------
def retry(notices, term_id, data, actor="workspace"):
    """Send again, in a new email, a notice email (`batch`, its gmail request ID) that failed or is uncertain and was not retried yet:
    the quizzes of it still marked at the same date and start, as new notice rows (attempt + 1). The instructor's explicit action;
    a repeat of `request_id` returns what it did and sends nothing."""
    request_id = _request(data, {"request_id", "batch"})
    batch = data.get("batch")
    if not isinstance(batch, str) or not batch:
        raise ValueError("Name the notice email to send again.")
    send_id = "dr-quiz-retry:" + hashlib.sha256((term_id + request_id).encode()).hexdigest()[:32]
    roster = _roster(notices.mail.roster_people())
    with notices.rosters.lock(), connect(notices.path) as db:
        db.execute("BEGIN IMMEDIATE")
        ScheduleService._term(db, term_id)
        rows = [dict(row) for row in db.execute("SELECT * FROM dr_quiz_notices WHERE term_id=? AND request_id=?", (term_id, batch))]
        earlier = [dict(row) for row in db.execute("SELECT * FROM dr_quiz_notices WHERE term_id=? AND request_id=?", (term_id, send_id))]
        if earlier:
            sent = {(row["enrollment_id"], row["section_id"], row["item_id"], row["date"], row["start"], row["attempt"] + 1) for row in rows}
            if any((row["enrollment_id"], row["section_id"], row["item_id"], row["date"], row["start"], row["attempt"]) not in sent for row in earlier):
                raise Conflict("This request ID already sent another notice email again.")
            return
        if not rows:
            raise Conflict("That is not one of this term's DR quiz notice emails.")
        state = _sends(db, {batch})[batch][0]
        if state not in ("failed", "uncertain"):
            raise Conflict("That notice email was accepted; it is not sent again.")
        for row in rows:
            if db.execute("""SELECT 1 FROM dr_quiz_notices WHERE enrollment_id=? AND section_id=? AND item_id=? AND date=? AND start=?
                    AND attempt>? LIMIT 1""", (row["enrollment_id"], row["section_id"], row["item_id"], row["date"], row["start"],
                                               row["attempt"])).fetchone():
                raise Conflict("That notice email was already sent again.")
        found = term_quizzes(db, term_id)
        marks = _marks(db, term_id)
        quizzes = {quiz["key"]: quiz for quiz in found["quizzes"]}
        due = []
        for row in rows:
            quiz = quizzes.get(f"{row['item_id']}:{row['section_id']}")
            mark = quiz and marks.get((quiz["item"], quiz["section_id"]))
            if quiz and _state(quiz, mark) == "marked" and (mark["date"], mark["start"]) == (row["date"], row["start"]):
                updated = db.execute("""SELECT 1 FROM dr_quiz_notices WHERE enrollment_id=? AND section_id=? AND item_id=?
                    AND date!=? LIMIT 1""", (row["enrollment_id"], row["section_id"], row["item_id"], row["date"])).fetchone() is not None
                due.append((quiz, mark, updated, row["attempt"] + 1))
        if not due:
            raise Conflict("None of that email's quizzes is still marked Prescheduled in AIM at the same date and time.")
        person = roster.get((rows[0]["section_id"], rows[0]["enrollment_id"]))
        skipped = _skipped(person)
        if skipped:
            raise Conflict(f"That student cannot be emailed: {skipped}.")
        order = {quiz["key"]: n for n, quiz in enumerate(found["quizzes"])}
        due.sort(key=lambda entry: order[entry[0]["key"]])
        data = _message(found["code"], due[0][0]["section"], person["address"], [_line(quiz, updated) for quiz, _, updated, _ in due])
        preview = notices.mail.preview(data)  # raises before anything is recorded
        _insert(db, term_id, person, [(quiz, mark, attempt) for quiz, mark, _, attempt in due], send_id, actor)
        db.commit()
    try:
        notices.mail.send({**data, "request_id": send_id, "fingerprint": preview["fingerprint"], "confirmation": "send"}, actor=actor)
    except Exception:  # noqa: BLE001 - the notices stay recorded and read uncertain; only another Retry sends them again
        LOG.exception("DR quiz notice retry %s was not confirmed sent", send_id)
