"""SCHEDULING "DR REQUEST EMAILS" (requirement, 2026-11-01).

The DR Testing Center emails the instructor, from info@disability.tamu.edu, when a student requests to sit an exam or quiz there
("[DR] New Exam Request") and when it approves one ("[DR] Testing Request Approved"). A bounded turn of the Inbox worker reads them
from the instructor's Gmail (reads only) and keeps each once (migration scheduling/008): the UIN, course, section, type, day, start
and whether it was late, never the student's name, with the current student's enrollment and section when they match.

A kept request of a DR quiz (type Quiz, on the day of a row of its section's DR quizzes, CLASS SCHEDULE: DR QUIZZES) whose
accessible copy is ready, while AIM's accepted uploads of that quiz cover fewer requests than were kept for it, opens the student's
Follow-up (To do, "Upload <quiz>'s accessible copy to AIM") with one Approve card (`scheduling.aim_upload`, aim_send.upload_start),
once per student, quiz and day. Approving it on the Follow-up page opens AIM's sign-in window there (Duo), and Send to AIM's own send
of that quiz uploads the copy to every request of the section that lacks it. Once AIM's accepted uploads cover every request kept
for that quiz, Faculty resolves the follow-up (unless the instructor saved it since). Nothing here writes to AIM or sends mail."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from email.utils import parseaddr

from course_storage import timestamp
from rosters import connect

from . import clock
from .aim_send import _sends, plan
from .service import _has

LOG = logging.getLogger(__name__)
SENDER = "info@disability.tamu.edu"
SUBJECTS = {"[DR] New Exam Request": "new", "[DR] Testing Request Approved": "approved"}
QUERY = f'from:{SENDER} newer_than:45d ("New Exam Request" OR "Testing Request Approved")'
READ_EVERY = 600   # seconds between the worker's reads of Gmail for the DR center's request emails
LOOK_EVERY = 60    # seconds between its looks (catalog only) for a request owed a Follow-up or a follow-up to resolve
READ_TURN = 10     # emails kept in one turn
TRIES = 3          # upload cards one follow-up gets at most (AGAIN)
STALE = 1800       # seconds after which a send with no recorded end is over (AGAIN)


def _field(text, name):
    found = re.search(rf"^[ \t]*{name}:[ \t]*(.+?)[ \t]*$", text, re.M | re.I)
    return found.group(1) if found else ""


def parse(text):
    """{uin, code, section, exam_type, date (YYYY-MM-DD), start (HH:MM or ''), late} of one request email's text, or None when it
    does not say each of the student's UIN, the course and section, the type and the day."""
    student = re.search(r"^[ \t]*Student:.*\(UIN:\s*(\d{9})\)", text, re.M | re.I)
    course = re.match(r"([A-Z]{2,5})\s*(\d{3,4})\.(\d{3})\b", _field(text, "Course"))
    kind, day = _field(text, "Exam Type"), _field(text, "Date")
    try:
        date = dt.datetime.strptime(day, "%A, %B %d, %Y").date().isoformat()
    except ValueError:
        date = ""
    if not (student and course and kind and date):
        return None
    start = ""
    try:
        start = dt.datetime.strptime(_field(text, "Start Time").upper(), "%I:%M %p").strftime("%H:%M")
    except ValueError:
        pass
    return {"uin": student.group(1), "code": f"{course.group(1)} {course.group(2)}", "section": course.group(3),
            "exam_type": kind[:60], "date": date, "start": start, "late": _field(text, "Late Request").casefold() == "yes"}


def _member(db, found):
    """(enrollment ID, section ID) of the current student the request names (UIN, course code and section number), or (None, None)."""
    row = db.execute("""SELECT e.id, s.id FROM student_enrollments e JOIN course_terms t ON t.id=e.term_id
        JOIN roster_current m ON m.enrollment_id=e.id JOIN course_sections s ON s.id=m.section_id
        WHERE e.uin=? AND CAST(s.number AS TEXT)=? AND t.is_current=1 AND t.deleted_at IS NULL
        AND replace(upper(t.code),' ','')=? ORDER BY t.created_at DESC LIMIT 1""",
                     (found["uin"], found["section"], found["code"].replace(" ", "").upper())).fetchone()
    return (row[0], row[1]) if row else (None, None)


class DrExamRequests:
    """The Inbox worker's turn (module docstring). `followups` (assistant.followups) and `frq` (the quiz drafts' accessible copies)
    are set by app.py; until then a turn does nothing."""

    def __init__(self, schedule, gmail):
        self.schedule, self.gmail = schedule, gmail
        self.path, self.rosters = schedule.path, schedule.rosters
        self.followups = self.frq = None
        self._read_at = self._look_at = float("-inf")
        self._skipped = set()  # emails read that were no request (process memory): not fetched again

    def process_one(self):
        """One bounded turn: a request owed a Follow-up gets it (one), or a follow-up whose upload AIM accepted is resolved, else
        Gmail is read for new request emails. True when it did one."""
        if self.followups is None or self.frq is None:
            return False
        if time.monotonic() >= self._look_at + LOOK_EVERY:
            self._look_at = time.monotonic()
            try:
                if self.look():
                    self._look_at = float("-inf")  # more may be owed: look again on the next turn
                    return True
            except Exception:  # noqa: BLE001 - looked at again on a later turn
                LOG.exception("DR request follow-ups could not be looked at")
        if self.gmail is None or not self.gmail.ready() or time.monotonic() < self._read_at + READ_EVERY:
            return False
        self._read_at = time.monotonic()
        try:
            kept = self.read()
        except Exception:  # noqa: BLE001 - Gmail unavailable or an email that will not read: tried again on a later read
            LOG.exception("The DR center's request emails could not be read from Gmail")
            return False
        if kept:
            self._look_at = float("-inf")
        return kept > 0

    # -- Gmail --------------------------------------------------------------------------------------------------------------------
    def read(self):
        threads, _ = self.gmail.client.threads(QUERY, limit=20)
        with connect(self.path) as db:
            known = {r[0] for r in db.execute("SELECT gmail_message_id FROM dr_exam_requests")} | self._skipped
        kept = 0
        for thread in threads:
            ids = [m.get("id") for m in thread.get("messages") or [] if isinstance(m, dict)]
            if ids and all(i in known for i in ids if i):
                continue
            for message in self.gmail.client.thread(thread["id"]).get("messages") or []:
                if not message.get("id") or message["id"] in known or kept >= READ_TURN:
                    continue
                known.add(message["id"])
                if self.keep(message):
                    kept += 1
                else:
                    self._skipped.add(message["id"])
        return kept

    def keep(self, message):
        """Keep one email when it is one of the DR center's request emails; False otherwise."""
        from gmail_mail.message import body_and_files, header, instant
        payload = message.get("payload") or {}
        kind = SUBJECTS.get(" ".join(header(payload, "Subject").split()))
        if not kind or parseaddr(header(payload, "From"))[1].casefold() != SENDER:
            return False
        found = parse(body_and_files(payload)[0])
        if not found:
            return False
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            enrollment, section = _member(db, found)
            db.execute("""INSERT OR IGNORE INTO dr_exam_requests(gmail_message_id, received_at, kind, uin, code, section, exam_type, date,
                start, late, enrollment_id, section_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (message["id"], instant(message.get("internalDate")), kind, found["uin"], found["code"], found["section"],
                        found["exam_type"], found["date"], found["start"], int(found["late"]), enrollment, section, timestamp()))
            db.commit()
        return True

    # -- Follow-ups ---------------------------------------------------------------------------------------------------------------
    def look(self):
        """One Follow-up opened, or every follow-up whose upload is done resolved; True when anything was saved."""
        today = clock.campus_today().isoformat()
        with connect(self.path) as db:
            if not _has(db, "dr_exam_request_followups"):
                return False
            terms = [r[0] for r in db.execute("SELECT id FROM course_terms WHERE is_current=1 AND deleted_at IS NULL ORDER BY id")]
            waiting = db.execute("SELECT 1 FROM dr_exam_requests WHERE enrollment_id IS NOT NULL AND date>=? LIMIT 1", (today,)).fetchone()
        if not waiting:
            return False
        for term_id in terms:
            copies = self.frq.quiz_accessible_copies(term_id)
            with connect(self.path) as db:
                db.execute("BEGIN")
                owed, done, again = self._owed(db, term_id, plan(db, term_id, copies), today)
            resolved = False
            for link, quiz, tried in self._again(term_id, again):
                self._card(term_id, link, quiz, tried)
                return True
            for followup_id, quiz in done:
                resolved |= self.followups.resolve_faculty_followup(
                    followup_id, f"AIM accepted the accessible copy of {quiz['title']} for this request (Faculty, {today}).")
            if resolved:
                return True
            if owed:
                self._open(term_id, *owed[0])
                return True
        return False

    def _owed(self, db, term_id, found, today):
        """([(request row, quiz)] owed a Follow-up, [(follow-up ID, quiz)] whose quiz's accepted uploads cover every kept request,
        [(link, quiz)] opened but not covered yet, for AGAIN)."""
        sections = {quiz["section_id"] for quiz in found["quizzes"]}
        if not sections:
            return [], [], []
        rows = [dict(r) for r in db.execute(f"""SELECT * FROM dr_exam_requests WHERE date>=? AND section_id IN
            ({",".join("?" * len(sections))}) ORDER BY received_at, gmail_message_id""", (today, *sections))]
        links = [dict(r) for r in db.execute("SELECT * FROM dr_exam_request_followups WHERE term_id=?", (term_id,))]
        sends = _sends(db, term_id)
        owed, done, again = [], [], []
        for quiz in found["quizzes"]:
            mine = [r for r in rows if r["section_id"] == quiz["section_id"] and r["date"] == quiz["date"]
                    and r["exam_type"].casefold() == "quiz"]
            students = list(dict.fromkeys(r["enrollment_id"] for r in mine))
            if not students:
                continue
            uploaded = sum(len(json.loads(s["rows"])) for s in sends if s["kind"] == "upload" and s["status"] == "accepted"
                           and (s["item_id"], s["section_id"], s["date"]) == (quiz["item"], quiz["section_id"], quiz["date"]))
            opened = {link["enrollment_id"]: link for link in links
                      if (link["item_id"], link["section_id"], link["date"]) == (quiz["item"], quiz["section_id"], quiz["date"])}
            if uploaded >= len(students):
                done += [(link["followup_id"], quiz) for link in opened.values()]
                continue
            if not quiz["copy"]:
                continue
            for student in students:
                if student not in opened:
                    owed.append((next(r for r in mine if r["enrollment_id"] == student), quiz))
            again += [(link, quiz) for link in opened.values()]
        return owed, done, again

    def _again(self, term_id, opened):
        """AGAIN (requirement, 2026-11-01): a follow-up whose upload is not covered yet,
        with no card waiting, whose last card failed, or whose approval's send ended without uploading, gets a fresh card in the same
        run (at most TRIES). A send still signing in is waited for. [(link, quiz, cards so far)]."""
        found = []
        stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=STALE)).isoformat()
        with connect(self.path) as db:
            runs = [dict(r) for r in db.execute("""SELECT r.created_at, COALESCE(e.state, '') AS state FROM aim_send_runs r
                LEFT JOIN aim_send_run_ends e ON e.run_id=r.id WHERE r.term_id=? ORDER BY r.created_at""", (term_id,))]
            for link, quiz in opened:
                cards = [a for a in self.followups._actions(db, link["run_id"]) if a["endpoint"] == "scheduling.aim_upload"]
                if not cards or len(cards) >= TRIES or any(a["pending"] or a["status"] == "approved" for a in cards):
                    continue
                last = cards[-1]
                if last["status"] == "failed":
                    found.append((link, quiz, len(cards)))
                    continue
                if last["status"] != "succeeded":
                    continue  # declined or withdrawn: the instructor's decision
                approved = next((e["at"] for e in reversed(last["events"]) if e["kind"] == "approved"), "")
                started = [run for run in runs if run["created_at"] >= approved]
                # A send a restart cut off records no end: past STALE it is over all the same.
                ended = started and (started[0]["state"] or started[0]["created_at"] < stale)
                if ended and started[0]["state"] != "sent":
                    found.append((link, quiz, len(cards)))
        return found

    def _card(self, term_id, link, quiz, tried):
        body = {"request_id": "$new_request_id", "key": quiz["key"], "enrollment_id": link["enrollment_id"], "attempt": tried + 1}
        answer = self.followups.faculty_card(link["run_id"], f"/api/scheduling/terms/{term_id}/aim-send/uploads", body,
                                             f"Upload the accessible copy of {quiz['title']} to this student's AIM request again "
                                             f"(section {quiz['section']}, {clock.long_day(quiz['date'])}): the last try did not upload it")
        if (answer or {}).get("outcome") not in ("waiting_for_approval", "already_recorded"):
            LOG.warning("The DR request follow-up's fresh upload card was not made: %s", (answer or {}).get("outcome"))

    def _open(self, term_id, request, quiz):
        late = ", a late request" if request["late"] else ""
        received = dt.datetime.fromisoformat(request["received_at"].replace("Z", "+00:00")).astimezone(clock.CAMPUS_ZONE)
        details = (f"The DR Testing Center emailed on {received:%a, %b} {received.day} that this student requested {quiz['title']} at the "
                   f"Testing Center on {clock.long_day(quiz['date'])} (section {quiz['section']}{late}). AIM attaches the quiz to each student's "
                   "request, so its accessible copy can go up now.")
        explanation = (details + f" Approving signs in to AIM with the saved sign-in (Duo shows its code in the window that opens) and "
                       f"uploads the accessible copy of {quiz['title']} (Version {quiz['copy']['version']}) to every request of section "
                       f"{quiz['section']} for that day that does not have it yet.")
        followup = {"title": f"Upload {quiz['title']}'s accessible copy to AIM", "details": details,
                    "next": "Approve the upload; AIM's sign-in asks for your Duo."}
        body = {"request_id": "$new_request_id", "key": quiz["key"], "enrollment_id": request["enrollment_id"]}
        summary = (f"Upload the accessible copy of {quiz['title']} to this student's AIM request (section {quiz['section']}, "
                   f"{clock.long_day(quiz['date'])}); AIM's sign-in asks for your Duo")
        followup_id, run_id, answer = self.followups.faculty_followup(
            request["enrollment_id"], f"dr-request:{request['gmail_message_id']}", followup, explanation,
            f"/api/scheduling/terms/{term_id}/aim-send/uploads", body, summary)
        if (answer or {}).get("outcome") not in ("waiting_for_approval", "already_recorded"):
            LOG.warning("The DR request follow-up's upload card was not made: %s", (answer or {}).get("outcome"))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute("""INSERT OR IGNORE INTO dr_exam_request_followups(gmail_message_id, term_id, item_id, section_id, enrollment_id, date,
                followup_id, run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                       (request["gmail_message_id"], term_id, quiz["item"], quiz["section_id"], request["enrollment_id"], quiz["date"],
                        followup_id, run_id, timestamp()))
            db.commit()
